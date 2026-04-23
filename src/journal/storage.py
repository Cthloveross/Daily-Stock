# -*- coding: utf-8 -*-
"""Journal storage layer: schema init + CRUD.

Reuses the project-wide SQLAlchemy engine / Session from
:mod:`src.storage` so Journal data shares the same SQLite file.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import delete, select

from src.journal.brokers.moomoo_us import MoomooOrder, compute_external_id
from src.journal.models import (
    JournalHealthCheck,
    JournalImport,
    JournalMonthlyReview,
    JournalOrder,
    JournalPhaseState,
    JournalShadowTrade,
    JournalTrade,
)
from src.storage import Base, get_db

logger = logging.getLogger(__name__)

__all__ = [
    "init_journal_schema",
    "csv_sha256",
    "record_import",
    "insert_events_from_orders",
    "query_events_for_matching",
    "replace_trades",
    "get_or_create_default_portfolio",
]


DEFAULT_PORTFOLIO_LABEL = "default_moomoo_us"


def init_journal_schema() -> None:
    """Idempotently create all ``journal_*`` tables and seed phase_state.

    Uses :class:`Base.metadata.create_all` so new tables are picked up without
    disturbing existing ones. Safe to call repeatedly.
    """
    db = get_db()
    Base.metadata.create_all(db._engine)

    # Seed phase_state with the singleton row if missing.
    with db.session_scope() as session:
        existing = session.get(JournalPhaseState, 1)
        if existing is None:
            session.add(
                JournalPhaseState(id=1, phase=0, phase_started=date.today())
            )
    logger.info("journal schema ready")


def csv_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def get_or_create_default_portfolio() -> str:
    """Return the default Moomoo portfolio label.

    Stage 2 uses a single hardcoded label because the Moomoo CSV doesn't
    carry account identifiers. Multi-account support arrives when a user
    needs it.
    """
    return DEFAULT_PORTFOLIO_LABEL


def record_import(
    source_path: str,
    content: bytes,
    broker: str,
    rows_total: int,
    portfolio_label: str = DEFAULT_PORTFOLIO_LABEL,
) -> Optional[int]:
    """Record a CSV import attempt and return its ``id``.

    Returns ``None`` if this exact CSV (by sha256) has already been imported.
    """
    sha = csv_sha256(content)
    db = get_db()
    with db.session_scope() as session:
        existing_id = session.execute(
            select(JournalImport.id).where(JournalImport.csv_sha256 == sha)
        ).scalar_one_or_none()
        if existing_id:
            logger.info("CSV %s already imported (id=%s), skipping", sha[:12], existing_id)
            return None
        row = JournalImport(
            source_path=source_path,
            csv_sha256=sha,
            broker=broker,
            portfolio_label=portfolio_label,
            rows_total=rows_total,
            rows_imported=0,
            rows_skipped=0,
            status="success",
        )
        session.add(row)
        session.flush()
        return int(row.id)


def _order_to_row(
    import_id: int,
    portfolio_label: str,
    order: MoomooOrder,
) -> dict:
    """Convert a :class:`MoomooOrder` into kwargs for ``JournalOrder``."""
    info = order.instrument
    is_option = bool(info.is_option) if info else False
    underlying = info.underlying if info else order.symbol
    expiry = info.option.expiry if info and info.option else None
    strike = info.option.strike if info and info.option else None
    right = info.option.right if info and info.option else None

    # Convert aware datetimes to naive UTC for SQLite compatibility.
    def _to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    return {
        "import_id": import_id,
        "portfolio_label": portfolio_label,
        "external_id": compute_external_id(order),
        "raw_symbol": order.symbol,
        "is_option": is_option,
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "right": right,
        "side": order.side.lower(),
        "quantity": order.filled_qty,
        "price": order.avg_fill_price,
        "currency": order.currency,
        "order_time": _to_naive_utc(order.order_time),
        "session": order.session or None,
        "first_fill_time": _to_naive_utc(order.first_fill_time),
        "last_fill_time": _to_naive_utc(order.last_fill_time),
        "commission": order.commission,
        "platform_fee": order.platform_fee,
        "trading_activity_fee": order.trading_activity_fee,
        "options_regulatory_fee": order.options_regulatory_fee,
        "occ_fee": order.occ_fee,
        "contract_fee": order.contract_fee,
        "sec_fee": order.sec_fee,
        "settlement_fee": order.settlement_fee,
        "total_fee": order.total_fee,
        "raw_row": json.dumps(order.raw_rows, default=str),
    }


def insert_events_from_orders(
    import_id: int,
    orders: Iterable[MoomooOrder],
    portfolio_label: str = DEFAULT_PORTFOLIO_LABEL,
) -> tuple[int, int]:
    """Persist orders, skipping duplicates by ``external_id``.

    Returns ``(inserted, skipped)``.
    """
    db = get_db()
    inserted = 0
    skipped = 0
    with db.session_scope() as session:
        # Pre-fetch existing external_ids in this portfolio to minimise round-trips.
        existing = set(
            session.execute(
                select(JournalOrder.external_id).where(
                    JournalOrder.portfolio_label == portfolio_label
                )
            ).scalars()
        )
        for order in orders:
            row = _order_to_row(import_id, portfolio_label, order)
            if row["external_id"] in existing:
                skipped += 1
                continue
            session.add(JournalOrder(**row))
            existing.add(row["external_id"])
            inserted += 1

        # Update import summary counts.
        imp = session.get(JournalImport, import_id)
        if imp is not None:
            imp.rows_imported = inserted
            imp.rows_skipped = skipped
    return inserted, skipped


def query_events_for_matching(
    portfolio_label: str = DEFAULT_PORTFOLIO_LABEL,
) -> list[dict]:
    """Return all orders as plain dicts, ordered by first_fill_time.

    Kept as dicts (not ORM objects) so the matcher stays a pure function.
    """
    db = get_db()
    with db.session_scope() as session:
        rows = (
            session.execute(
                select(JournalOrder)
                .where(JournalOrder.portfolio_label == portfolio_label)
                .order_by(
                    # first_fill_time NULL -> fall back to order_time -> created_at
                    JournalOrder.first_fill_time.asc().nulls_last(),
                    JournalOrder.order_time.asc().nulls_last(),
                    JournalOrder.id.asc(),
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "external_id": r.external_id,
                "raw_symbol": r.raw_symbol,
                "is_option": bool(r.is_option),
                "underlying": r.underlying,
                "expiry": r.expiry,
                "strike": r.strike,
                "right": r.right,
                "side": r.side,
                "quantity": int(r.quantity),
                "price": float(r.price),
                "order_time": r.order_time,
                "first_fill_time": r.first_fill_time,
                "total_fee": float(r.total_fee or 0.0),
            }
            for r in rows
        ]


def replace_trades(
    trades: list[dict],
    portfolio_label: str = DEFAULT_PORTFOLIO_LABEL,
) -> int:
    """Clear trades for the portfolio and rewrite from scratch.

    FIFO matching is cheap enough that full rebuild is simpler than an
    incremental approach and always correct when a new CSV arrives with
    late-arriving fills for older orders.
    """
    db = get_db()
    with db.session_scope() as session:
        session.execute(
            delete(JournalTrade).where(JournalTrade.portfolio_label == portfolio_label)
        )
        session.flush()
        for t in trades:
            row = dict(t)
            row.setdefault("portfolio_label", portfolio_label)
            # Serialize JSON fields if they arrive as lists.
            for key in ("open_order_ids", "close_order_ids", "mistakes_ai"):
                val = row.get(key)
                if val is not None and not isinstance(val, str):
                    row[key] = json.dumps(val)
            session.add(JournalTrade(**row))
    return len(trades)
