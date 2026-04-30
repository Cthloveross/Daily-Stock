# -*- coding: utf-8 -*-
"""Moomoo OpenAPI live trade-account broker.

Pulls historical orders / deals from the user's Moomoo account via
:class:`OpenSecTradeContext` and converts them into the same
:class:`MoomooOrder` shape that the existing CSV parser produces. This means
the FIFO matcher / journal storage layer needs **zero** changes — live data
flows through the same ``record_import`` → ``insert_events_from_orders`` →
``match_legs_fifo`` → ``replace_trades`` pipeline.

Why two endpoints (orders + deals)?
-----------------------------------
Moomoo's history APIs split data:
- ``history_deal_list_query`` (per-fill, **LIVE only** per the docs)
- ``history_order_list_query`` (per-order, available in both LIVE and SIMULATE)

For LIVE accounts we prefer deals because they have per-fill timestamps and
prices (like the CSV's fill rows). For SIMULATE we fall back to orders with
``FILLED_ALL`` / ``FILLED_PART`` status — paper-trading granularity is enough
for the journal pipeline since FIFO only needs aggregated qty + avg price.

External-ID strategy
--------------------
The CSV path computes a stable hash from
``(symbol|side|order_time|first_fill_time|qty|price|filled_qty)``. We feed
identical fields into ``MoomooOrder`` so re-syncing the same window produces
the same hash → ``insert_events_from_orders`` skips duplicates. This makes
the sync **idempotent** without us inventing a new dedupe scheme.

Limitations
-----------
- Per-order fee breakdown is not exposed by these APIs (only ``total_fee``
  via account-level queries). Live orders ingested this way carry
  ``commission=0`` etc. and only ``total_fee=0`` until we query account
  funds. Phase B+ can backfill via ``accinfo_query`` if the user cares about
  fee-level PnL accuracy.
- This module assumes US market (``TrdMarket.US``). Pass ``market='HK'`` to
  override.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from src.journal.brokers.moomoo_us import MoomooOrder
from src.options.occ_parser import parse_symbol

__all__ = [
    "MoomooLiveError",
    "fetch_orders_as_journal",
]

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class MoomooLiveError(RuntimeError):
    """Raised when the OpenD trade-context call fails or returns an error code."""


def _parse_api_ts(s: Optional[str]) -> Optional[datetime]:
    """Parse moomoo's `YYYY-MM-DD HH:MM:SS[.MS]` timestamp.

    The doc says US deals come back in US Eastern time. We tag aware ET so
    the rest of the journal pipeline (which converts to UTC for storage)
    handles DST correctly.
    """
    if not s:
        return None
    text = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ET)
        except ValueError:
            continue
    logger.warning("[moomoo_live] could not parse timestamp %r", text)
    return None


def _strip_market_prefix(code: str) -> str:
    """Convert ``US.AAPL`` → ``AAPL`` so the symbol shape matches CSV exports."""
    if not code:
        return ""
    parts = str(code).split(".", 1)
    return parts[1] if len(parts) == 2 and parts[0] in {"US", "HK", "SH", "SZ", "BJ"} else str(code)


def _normalize_side(raw) -> str:
    """``TrdSide.BUY`` / ``BUY`` / ``Buy`` → ``Buy``."""
    s = str(raw).split(".")[-1].lower()
    if "buy" in s:
        return "Buy"
    if "sell" in s:
        return "Sell"
    return s.title()


def _build_order(
    *,
    symbol_with_market: str,
    stock_name: str,
    side_raw,
    order_time: Optional[datetime],
    first_fill_time: Optional[datetime],
    last_fill_time: Optional[datetime],
    filled_qty: float,
    avg_fill_price: float,
    order_qty: float,
    order_price: float,
    currency: str,
    fills: list[dict],
    raw_rows: list[dict],
) -> MoomooOrder:
    """Construct a :class:`MoomooOrder` populated like the CSV path would."""
    symbol = _strip_market_prefix(symbol_with_market)
    side = _normalize_side(side_raw)
    name = (stock_name or "").strip()
    order_amount = float(order_qty or 0) * float(order_price or 0)

    order = MoomooOrder(
        side=side,
        symbol=symbol,
        name=name,
        order_qty=int(order_qty or 0),
        order_price=float(order_price or 0),
        order_amount=order_amount,
        status="Filled",
        order_time=order_time,
        order_type="Limit",
        session="",
        # Fees: see module docstring — not available per-order from this API
        commission=0.0,
        platform_fee=0.0,
        trading_activity_fee=0.0,
        options_regulatory_fee=0.0,
        occ_fee=0.0,
        contract_fee=0.0,
        sec_fee=0.0,
        settlement_fee=0.0,
        total_fee=0.0,
        currency=(currency or "USD").upper(),
        fills=fills,
        filled_qty=int(filled_qty or 0),
        avg_fill_price=float(avg_fill_price or 0),
        first_fill_time=first_fill_time,
        last_fill_time=last_fill_time,
        raw_rows=raw_rows,
    )

    try:
        order.instrument = parse_symbol(symbol)
    except ValueError as exc:
        logger.debug(
            "[moomoo_live] parse_symbol failed for %r: %s — treating as raw equity",
            symbol,
            exc,
        )
        order.instrument = None

    return order


def _ctx_open(host: str, port: int, market: str):
    """Open ``OpenSecTradeContext`` lazily so this module imports without the SDK."""
    from moomoo import OpenSecTradeContext, SecurityFirm, TrdMarket

    market_enum = getattr(TrdMarket, market.upper())
    return OpenSecTradeContext(
        filter_trdmarket=market_enum,
        host=host,
        port=port,
        security_firm=SecurityFirm.FUTUINC,
    )


def _check(ret, data, label: str):
    """Raise MoomooLiveError on non-OK return; pass through DataFrame."""
    from moomoo import RET_OK

    if ret != RET_OK:
        raise MoomooLiveError(f"{label} failed: {data}")
    return data


def _fetch_via_deals(ctx, *, start_str: str, end_str: str, env_enum) -> list[MoomooOrder]:
    """LIVE path: ``history_deal_list_query`` → group by ``order_id`` → MoomooOrder."""
    deals = _check(
        *ctx.history_deal_list_query(start=start_str, end=end_str, trd_env=env_enum),
        label="history_deal_list_query",
    )
    if deals is None or deals.empty:
        logger.info("[moomoo_live] no deals in window %s..%s", start_str, end_str)
        return []

    grouped: dict[str, list[dict]] = defaultdict(list)
    for _, row in deals.iterrows():
        oid = str(row.get("order_id") or "")
        if not oid:
            continue
        grouped[oid].append(row.to_dict())

    # Pull matching parent orders for richer metadata (order_qty, order_price, currency)
    orders_df = _check(
        *ctx.history_order_list_query(start=start_str, end=end_str, trd_env=env_enum),
        label="history_order_list_query",
    )
    order_meta: dict[str, dict] = {}
    if orders_df is not None and not orders_df.empty:
        for _, row in orders_df.iterrows():
            oid = str(row.get("order_id") or "")
            if oid:
                order_meta[oid] = row.to_dict()

    out: list[MoomooOrder] = []
    for oid, deal_rows in grouped.items():
        meta = order_meta.get(oid, {})
        # Order chronologically by deal create_time
        deal_rows.sort(key=lambda r: r.get("create_time") or "")
        first = deal_rows[0]
        last = deal_rows[-1]
        total_qty = float(sum(float(r.get("qty") or 0) for r in deal_rows))
        weighted_price = (
            sum(float(r.get("qty") or 0) * float(r.get("price") or 0) for r in deal_rows)
            / total_qty
            if total_qty > 0
            else 0.0
        )

        fills = [
            {
                "qty": float(r.get("qty") or 0),
                "price": float(r.get("price") or 0),
                "deal_id": r.get("deal_id"),
                "create_time": r.get("create_time"),
            }
            for r in deal_rows
        ]

        out.append(
            _build_order(
                symbol_with_market=str(first.get("code") or meta.get("code") or ""),
                stock_name=str(first.get("stock_name") or meta.get("stock_name") or ""),
                side_raw=first.get("trd_side"),
                order_time=_parse_api_ts(meta.get("create_time")),
                first_fill_time=_parse_api_ts(first.get("create_time")),
                last_fill_time=_parse_api_ts(last.get("create_time")),
                filled_qty=total_qty,
                avg_fill_price=weighted_price,
                order_qty=float(meta.get("qty") or total_qty),
                order_price=float(meta.get("price") or 0),
                currency=str(meta.get("currency") or "USD"),
                fills=fills,
                raw_rows=[meta or first, *deal_rows],
            )
        )
    logger.info("[moomoo_live] built %d orders from %d deals", len(out), len(deals))
    return out


def _fetch_via_orders(ctx, *, start_str: str, end_str: str, env_enum) -> list[MoomooOrder]:
    """SIMULATE path: ``history_order_list_query`` filtered to filled statuses."""
    from moomoo import OrderStatus

    orders_df = _check(
        *ctx.history_order_list_query(
            status_filter_list=[OrderStatus.FILLED_ALL, OrderStatus.FILLED_PART],
            start=start_str,
            end=end_str,
            trd_env=env_enum,
        ),
        label="history_order_list_query",
    )
    if orders_df is None or orders_df.empty:
        logger.info("[moomoo_live] no filled orders in window %s..%s", start_str, end_str)
        return []

    out: list[MoomooOrder] = []
    for _, row in orders_df.iterrows():
        d = row.to_dict()
        dealt_qty = float(d.get("dealt_qty") or 0)
        if dealt_qty <= 0:
            continue
        out.append(
            _build_order(
                symbol_with_market=str(d.get("code") or ""),
                stock_name=str(d.get("stock_name") or ""),
                side_raw=d.get("trd_side"),
                order_time=_parse_api_ts(d.get("create_time")),
                first_fill_time=_parse_api_ts(d.get("updated_time") or d.get("create_time")),
                last_fill_time=_parse_api_ts(d.get("updated_time") or d.get("create_time")),
                filled_qty=dealt_qty,
                avg_fill_price=float(d.get("dealt_avg_price") or 0),
                order_qty=float(d.get("qty") or dealt_qty),
                order_price=float(d.get("price") or 0),
                currency=str(d.get("currency") or "USD"),
                fills=[
                    {
                        "qty": dealt_qty,
                        "price": float(d.get("dealt_avg_price") or 0),
                        "deal_id": None,
                        "create_time": d.get("updated_time") or d.get("create_time"),
                    }
                ],
                raw_rows=[d],
            )
        )
    logger.info("[moomoo_live] built %d orders from history_order_list_query", len(out))
    return out


def fetch_orders_as_journal(
    *,
    start: datetime,
    end: datetime,
    trd_env: str = "SIMULATE",
    market: str = "US",
    host: str = "127.0.0.1",
    port: int = 11111,
) -> list[MoomooOrder]:
    """Pull moomoo trade history and produce a list ready for ``insert_events_from_orders``.

    Args:
        start: window start (timezone-aware preferred; naive treated as UTC).
        end: window end.
        trd_env: ``"SIMULATE"`` (paper) or ``"LIVE"`` (real account).
        market: trade market filter, default ``"US"`` (also ``"HK"`` / ``"CN"``).
        host/port: OpenD daemon address.

    Raises ``MoomooLiveError`` on RET_ERROR or transport failure.
    """
    if trd_env not in {"SIMULATE", "LIVE"}:
        raise ValueError(f"invalid trd_env={trd_env!r}; expected SIMULATE or LIVE")

    try:
        from moomoo import TrdEnv  # noqa: F401  — early SDK probe
    except ImportError as exc:
        raise MoomooLiveError(
            "moomoo-api SDK not installed. `pip install moomoo-api>=10.4.6408`"
        ) from exc

    from moomoo import TrdEnv

    env_enum = getattr(TrdEnv, trd_env)
    fmt = "%Y-%m-%d %H:%M:%S"
    start_str = start.strftime(fmt)
    end_str = end.strftime(fmt)

    ctx = _ctx_open(host=host, port=port, market=market)
    try:
        if trd_env == "LIVE":
            return _fetch_via_deals(ctx, start_str=start_str, end_str=end_str, env_enum=env_enum)
        return _fetch_via_orders(ctx, start_str=start_str, end_str=end_str, env_enum=env_enum)
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
