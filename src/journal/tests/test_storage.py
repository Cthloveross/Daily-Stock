# -*- coding: utf-8 -*-
"""End-to-end storage + schema tests for the journal pipeline."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.journal.brokers.moomoo_us import parse as parse_moomoo
from src.journal.matcher import match_legs_fifo
from src.journal.storage import (
    DEFAULT_PORTFOLIO_LABEL,
    csv_sha256,
    init_journal_schema,
    insert_events_from_orders,
    query_events_for_matching,
    record_import,
    replace_trades,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "journal"
    / "moomoo_inline_sample.csv"
)


def test_schema_is_idempotent():
    init_journal_schema()
    init_journal_schema()
    init_journal_schema()
    # No exception.


def test_end_to_end_import_and_match():
    init_journal_schema()
    content = FIXTURE.read_bytes()
    orders = parse_moomoo(content)

    import_id = record_import(
        source_path=str(FIXTURE),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
    )
    assert import_id is not None

    inserted, skipped = insert_events_from_orders(import_id, orders)
    assert inserted == len(orders)
    assert skipped == 0

    events = query_events_for_matching()
    assert len(events) == len(orders)
    assert any(e["is_option"] for e in events)
    assert any(not e["is_option"] for e in events)

    trades = match_legs_fifo(events)
    closed = [t for t in trades if t["status"] == "closed"]
    assert len(closed) >= 2  # NVDA call + NVDA equity should both close in the fixture
    written = replace_trades(trades)
    assert written == len(trades)


def test_dedup_by_sha256_skips_second_import():
    init_journal_schema()
    content = FIXTURE.read_bytes()
    orders = parse_moomoo(content)
    first = record_import(
        source_path=str(FIXTURE),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
    )
    assert first is not None

    second = record_import(
        source_path=str(FIXTURE),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
    )
    assert second is None


def test_dedup_by_external_id():
    init_journal_schema()
    content = FIXTURE.read_bytes()
    orders = parse_moomoo(content)
    import_id = record_import(
        source_path=str(FIXTURE),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
    )
    inserted1, skipped1 = insert_events_from_orders(import_id, orders)
    # Same orders again -> everything skipped.
    # (Need a new fake import id; re-use the existing one to keep FK valid.)
    inserted2, skipped2 = insert_events_from_orders(import_id, orders)
    assert inserted1 > 0 and skipped1 == 0
    assert inserted2 == 0 and skipped2 > 0


def test_replace_trades_is_wipe_then_insert():
    init_journal_schema()
    content = FIXTURE.read_bytes()
    orders = parse_moomoo(content)
    import_id = record_import(
        source_path=str(FIXTURE),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
    )
    insert_events_from_orders(import_id, orders)
    events = query_events_for_matching()
    trades1 = match_legs_fifo(events)
    replace_trades(trades1)
    # Second rebuild must not double-count.
    replace_trades(trades1)

    from src.journal.models import JournalTrade
    from src.storage import get_db
    from sqlalchemy import select

    db = get_db()
    with db.session_scope() as session:
        count = len(session.execute(select(JournalTrade)).scalars().all())
    assert count == len(trades1)


def test_csv_sha256_stable():
    content = FIXTURE.read_bytes()
    assert csv_sha256(content) == csv_sha256(content)
    assert csv_sha256(content) != csv_sha256(content + b"\n")
