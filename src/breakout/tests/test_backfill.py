# -*- coding: utf-8 -*-
"""Backfill functions against an in-memory trades table."""
from __future__ import annotations

from datetime import datetime

from src.breakout.backfill_fake_breakout import backfill_fake_breakout
from src.breakout.backfill_trade_style import backfill_trade_style, classify_trade
from src.journal.models import JournalTrade
from src.journal.storage import init_journal_schema
from src.storage import get_db


def _seed_trade(**overrides):
    defaults = dict(
        portfolio_label="default_moomoo_us",
        is_option=True,
        underlying="NVDA",
        direction="long",
        status="closed",
        quantity=1,
        avg_entry_price=2.0,
        avg_exit_price=1.5,
        entry_time=datetime(2026, 4, 17, 10, 0),
        exit_time=datetime(2026, 4, 17, 10, 30),
        hold_seconds=30 * 60,
        dte_at_entry=0,
        pnl_gross=-50,
        pnl_net=-51,
        pnl_pct=-25.0,
    )
    defaults.update(overrides)
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        session.add(JournalTrade(**defaults))


class TestClassifyTrade:
    def test_equity_swing(self):
        assert classify_trade(
            {"is_option": False, "direction": "long", "hold_seconds": 3 * 24 * 3600}
        ) == "equity_swing"

    def test_zero_dte_quick_flip_chase(self):
        assert classify_trade(
            {"is_option": True, "direction": "long", "dte_at_entry": 0, "hold_seconds": 10 * 60}
        ) == "breakout_chase"

    def test_long_dte_pullback(self):
        assert classify_trade(
            {"is_option": True, "direction": "long", "dte_at_entry": 5, "hold_seconds": 3600}
        ) == "pullback_buy"

    def test_other_fallback(self):
        assert classify_trade({"is_option": True, "direction": "long", "dte_at_entry": 90}) == "other"


class TestBackfillTradeStyle:
    def test_populates(self):
        _seed_trade(dte_at_entry=0, hold_seconds=20 * 60)
        n = backfill_trade_style()
        assert n == 1
        db = get_db()
        with db.session_scope() as session:
            from sqlalchemy import select

            row = session.execute(select(JournalTrade)).scalar_one()
            assert row.trade_style == "breakout_chase"

    def test_skips_when_already_labeled(self):
        _seed_trade(dte_at_entry=0, hold_seconds=20 * 60)
        backfill_trade_style()
        # Hand-override
        db = get_db()
        with db.session_scope() as session:
            from sqlalchemy import select

            row = session.execute(select(JournalTrade)).scalar_one()
            row.trade_style = "my_custom_label"
        n = backfill_trade_style(overwrite=False)
        assert n == 0


class TestBackfillFakeBreakout:
    def test_flags_short_big_loss(self):
        _seed_trade(dte_at_entry=0, hold_seconds=20 * 60, pnl_pct=-25.0)
        backfill_trade_style()  # needed to populate trade_style
        n = backfill_fake_breakout()
        assert n == 1
        db = get_db()
        with db.session_scope() as session:
            from sqlalchemy import select

            row = session.execute(select(JournalTrade)).scalar_one()
            assert row.was_fake_breakout is True

    def test_small_loss_not_fake(self):
        _seed_trade(dte_at_entry=0, hold_seconds=20 * 60, pnl_pct=-5.0)
        backfill_trade_style()
        backfill_fake_breakout()
        db = get_db()
        with db.session_scope() as session:
            from sqlalchemy import select

            row = session.execute(select(JournalTrade)).scalar_one()
            assert row.was_fake_breakout is False
