# -*- coding: utf-8 -*-
"""Monthly review: pure stats + dry-run wrapper."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.journal.models import JournalTrade
from src.journal.monthly_review import (
    compute_monthly_stats,
    generate_review,
    run,
)
from src.journal.storage import init_journal_schema
from src.storage import get_db


def _seed_closed(**overrides):
    defaults = dict(
        portfolio_label="default_moomoo_us",
        is_option=True,
        underlying="NVDA",
        direction="long",
        status="closed",
        quantity=1,
        avg_entry_price=2.0,
        avg_exit_price=2.5,
        entry_time=datetime(2026, 3, 10, 10, 0),
        exit_time=datetime(2026, 3, 10, 10, 30),
        hold_seconds=30 * 60,
        dte_at_entry=3,
        dte_bucket="1-3DTE",
        pnl_gross=50.0,
        pnl_net=49.0,
        pnl_pct=24.5,
        trade_style="breakout_chase",
    )
    defaults.update(overrides)
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        session.add(JournalTrade(**defaults))


class TestComputeMonthlyStats:
    def test_month_with_no_trades(self):
        stats = compute_monthly_stats(2026, 3)
        assert stats["total_trades"] == 0

    def test_mixed_month(self):
        _seed_closed(pnl_net=100.0)
        _seed_closed(pnl_net=-50.0, entry_time=datetime(2026, 3, 12, 10, 0))
        _seed_closed(pnl_net=200.0, entry_time=datetime(2026, 3, 15, 10, 0))
        # Out-of-month trade should not count.
        _seed_closed(pnl_net=9999.0, entry_time=datetime(2026, 4, 1, 10, 0))

        stats = compute_monthly_stats(2026, 3)
        assert stats["total_trades"] == 3
        assert stats["total_pnl_net"] == 250.0
        assert stats["win_rate"] == pytest.approx(2 / 3)


class TestGenerateReviewDryRun:
    def test_dry_run_skips_llm(self):
        _seed_closed(pnl_net=100.0)
        md, stats = generate_review(2026, 3, dry_run=True)
        assert "[DRY RUN]" in md
        assert stats["total_trades"] == 1


class TestRun:
    def test_run_persists_markdown(self):
        _seed_closed(pnl_net=100.0)
        res = run("2026-03", dry_run=True)
        assert res["created"] is True
        # Re-running upserts instead of inserting duplicate.
        res2 = run("2026-03", dry_run=True)
        assert res2["created"] is False
