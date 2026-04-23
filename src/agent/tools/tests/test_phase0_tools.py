# -*- coding: utf-8 -*-
"""Unit tests for the four v4 Phase 0 agent tools."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "agent_tools_test.db"))
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


class TestGetRegimeScoreTool:
    def test_missing_score_returns_flag(self):
        from src.agent.tools.get_regime_score_tool import get_regime_score_tool

        out = get_regime_score_tool(target_date="2020-01-01")
        assert out["found"] is False
        assert "hint" in out

    def test_found_returns_snapshot(self):
        from src.agent.tools.get_regime_score_tool import get_regime_score_tool
        from src.regime.classifier import RegimeResult
        from src.regime.storage import save_regime_score

        save_regime_score(
            RegimeResult(
                date=date(2026, 4, 17),
                score=75,
                label="aggressive",
                action_hint="",
                d1_direction=25,
                d2_volatility=15,
                d3_macro_penalty=0,
                d4_sector=10,
                d5_prev_day=10,
                d6_premarket=15,
                snapshot={},
            )
        )
        out = get_regime_score_tool(target_date="2026-04-17")
        assert out["found"] is True
        assert out["score"] == 75


class TestCheckBreakoutTool:
    def test_low_regime_rejected(self):
        from src.agent.tools.check_breakout_tool import check_breakout_tool

        out = check_breakout_tool(
            symbol="NVDA",
            direction="up",
            breakout_price=101.0,
            reference_high=100.0,
            reference_low=95.0,
            current_volume=2000,
            reference_volume=1000,
            regime_score=30,
        )
        assert out["passed"] is False
        assert out["rejected_at"] == "Q1"

    def test_all_gates_pass(self):
        from src.agent.tools.check_breakout_tool import check_breakout_tool

        out = check_breakout_tool(
            symbol="NVDA",
            direction="up",
            breakout_price=101.0,
            reference_high=100.0,
            reference_low=95.0,
            current_volume=2000,
            reference_volume=1000,
            regime_score=70,
            symbol_return_pct=1.2,
            spy_return_pct=0.2,
        )
        assert out["passed"] is True
        assert out["q3_volume"]["passed"] is True


class TestGetJournalSnapshotTool:
    def test_empty_journal(self):
        from src.agent.tools.get_journal_snapshot_tool import get_journal_snapshot_tool

        out = get_journal_snapshot_tool(days=30)
        assert out["total_trades"] == 0
        assert out["current_phase"] == 0
        assert out["reality_test"]["total_trades"] == 0

    def test_counts_zero_dte_this_week(self):
        from src.agent.tools.get_journal_snapshot_tool import get_journal_snapshot_tool
        from src.journal.models import JournalTrade
        from src.journal.storage import init_journal_schema
        from src.storage import get_db

        init_journal_schema()
        db = get_db()
        with db.session_scope() as session:
            session.add(
                JournalTrade(
                    portfolio_label="default_moomoo_us",
                    is_option=True,
                    underlying="NVDA",
                    direction="long",
                    status="closed",
                    quantity=1,
                    avg_entry_price=2.0,
                    avg_exit_price=2.5,
                    entry_time=datetime.now(),
                    pnl_net=50.0,
                    pnl_pct=25.0,
                    dte_at_entry=0,
                    dte_bucket="0DTE",
                )
            )
        out = get_journal_snapshot_tool(days=30)
        assert out["zero_dte_trades_last_week"] == 1


class TestGetOptionChainTool:
    def test_without_expiry_returns_list(self):
        from src.agent.tools.get_option_chain_tool import get_option_chain_tool

        with patch("data_provider.options_chain.OptionsChainFetcher.get_expirations", return_value=["2027-01-15"]):
            out = get_option_chain_tool("NVDA")
            assert out["expirations"] == ["2027-01-15"]

    def test_with_expiry_returns_quotes(self):
        from src.agent.tools.get_option_chain_tool import get_option_chain_tool

        with patch("data_provider.options_chain.OptionsChainFetcher.get_chain", return_value=[]):
            out = get_option_chain_tool("NVDA", expiry="2027-01-15")
            assert out["expiry"] == "2027-01-15"
            assert out["count"] == 0
