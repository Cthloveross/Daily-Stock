# -*- coding: utf-8 -*-
"""Moomoo live trade-account sync orchestrator.

End-to-end pipeline:
    Moomoo OpenD (history_deal_list_query / history_order_list_query)
        → list[MoomooOrder] (broker shape)
        → record_import (synthetic CSV digest for audit + dedup)
        → insert_events_from_orders (per-order dedup via external_id hash)
        → match_legs_fifo (FIFO leg pairing)
        → replace_trades

The sync is **idempotent**: re-running it for the same window just returns
``inserted=0, skipped=N`` because external IDs are deterministic from order
fields. The watermark stored in ``JournalPhaseState.last_synced_ts`` lets a
periodic cron skip work it has already done.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.journal.brokers.moomoo_live import (
    MoomooLiveError,
    fetch_orders_as_journal,
)
from src.journal.matcher import match_legs_fifo
from src.journal.storage import (
    DEFAULT_PORTFOLIO_LABEL,
    init_journal_schema,
    insert_events_from_orders,
    query_events_for_matching,
    record_import,
    replace_trades,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """End-to-end result for one sync run, mirrored to the API response."""

    window_start: str
    window_end: str
    trd_env: str
    market: str
    fetched: int
    inserted: int
    skipped: int
    trades_rebuilt: int
    message: str
    note: Optional[str] = None  # e.g. "no new orders" / "already imported"

    def to_dict(self) -> dict:
        return asdict(self)


def _env_get(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v.strip() if v else default


def _resolve_endpoint() -> tuple[str, int]:
    """Pick OpenD address from the same env vars used by MoomooFetcher."""
    host = _env_get("MOOMOO_OPEND_HOST", "127.0.0.1")
    try:
        port = int(_env_get("MOOMOO_OPEND_PORT", "11111") or "11111")
    except ValueError:
        port = 11111
    return host, port


def _digest_for_audit(orders) -> bytes:
    """Build a deterministic byte payload to feed `record_import` (which hashes it).

    We synthesise a JSON snapshot of the orders (sorted, stable) so that
    re-running the same sync window on identical broker data hits the same
    sha256 → record_import returns ``None`` (already imported), avoiding a
    second JournalImport row for no-op runs.
    """
    rows = []
    for o in sorted(orders, key=lambda x: (x.symbol, x.first_fill_time or datetime.min)):
        rows.append(
            {
                "symbol": o.symbol,
                "side": o.side,
                "qty": o.filled_qty,
                "price": round(o.avg_fill_price, 6),
                "first_fill_time": o.first_fill_time.isoformat() if o.first_fill_time else None,
                "last_fill_time": o.last_fill_time.isoformat() if o.last_fill_time else None,
            }
        )
    payload = json.dumps(rows, sort_keys=True).encode("utf-8")
    return payload


def sync_live_orders(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    trd_env: Optional[str] = None,
    market: Optional[str] = None,
    portfolio: str = DEFAULT_PORTFOLIO_LABEL,
    window_days: int = 7,
) -> SyncResult:
    """Pull recent Moomoo orders, persist via the journal pipeline.

    Args:
        start, end: explicit window. When omitted, defaults to "last
            ``window_days`` days, ending now (UTC)".
        trd_env: ``"SIMULATE"`` or ``"LIVE"``. When omitted, reads
            ``MOOMOO_TRADE_ENV`` env var (default ``"SIMULATE"`` for safety).
        market: ``"US"`` / ``"HK"`` / ``"CN"``. When omitted, ``"US"``.
        portfolio: journal portfolio label. Defaults to project-wide default.
        window_days: how far back to look when ``start`` is omitted.
    """
    init_journal_schema()
    host, port = _resolve_endpoint()
    enabled = _env_get("MOOMOO_OPEND_ENABLED", "false").lower() in ("1", "true", "yes", "on")
    if not enabled:
        raise MoomooLiveError(
            "MOOMOO_OPEND_ENABLED is not true; refuse to sync. Set the env "
            "var, ensure OpenD is logged in, and try again."
        )

    trd_env = (trd_env or _env_get("MOOMOO_TRADE_ENV", "SIMULATE")).upper()
    market = (market or "US").upper()

    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=max(1, window_days))

    logger.info(
        "[moomoo_sync] env=%s market=%s window=%s..%s portfolio=%s",
        trd_env, market, start.isoformat(), end.isoformat(), portfolio,
    )

    try:
        orders = fetch_orders_as_journal(
            start=start,
            end=end,
            trd_env=trd_env,
            market=market,
            host=host,
            port=port,
        )
    except MoomooLiveError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MoomooLiveError(f"moomoo fetch failed: {exc}") from exc

    fetched = len(orders)
    if fetched == 0:
        return SyncResult(
            window_start=start.isoformat(),
            window_end=end.isoformat(),
            trd_env=trd_env,
            market=market,
            fetched=0,
            inserted=0,
            skipped=0,
            trades_rebuilt=0,
            message="no new orders in window",
            note=f"querying {trd_env} on {market} returned 0 rows",
        )

    digest = _digest_for_audit(orders)
    src_label = f"moomoo_live://{market}/{trd_env}/{start.date()}..{end.date()}"

    import_id = record_import(
        source_path=src_label,
        content=digest,
        broker="moomoo_live",
        rows_total=fetched,
        portfolio_label=portfolio,
    )
    if import_id is None:
        # Identical digest already recorded — re-running an unchanged sync.
        # Still rebuild trades so the user can recover from a partial replace.
        events = query_events_for_matching(portfolio_label=portfolio)
        trades = match_legs_fifo(events)
        rebuilt = replace_trades(trades, portfolio_label=portfolio)
        return SyncResult(
            window_start=start.isoformat(),
            window_end=end.isoformat(),
            trd_env=trd_env,
            market=market,
            fetched=fetched,
            inserted=0,
            skipped=fetched,
            trades_rebuilt=rebuilt,
            message="already synced; no new orders",
            note="csv-sha256 match — record_import returned None",
        )

    inserted, skipped = insert_events_from_orders(import_id, orders, portfolio_label=portfolio)
    events = query_events_for_matching(portfolio_label=portfolio)
    trades = match_legs_fifo(events)
    rebuilt = replace_trades(trades, portfolio_label=portfolio)

    msg = (
        f"{inserted} new live orders ingested, {skipped} dupes skipped; "
        f"{rebuilt} paired trades rebuilt"
    )
    logger.info("[moomoo_sync] %s", msg)
    return SyncResult(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        trd_env=trd_env,
        market=market,
        fetched=fetched,
        inserted=inserted,
        skipped=skipped,
        trades_rebuilt=rebuilt,
        message=msg,
    )
