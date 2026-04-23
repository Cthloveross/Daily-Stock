# -*- coding: utf-8 -*-
"""Bot command tests for /journal, /regime, /phase."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from bot.models import BotMessage, ChatType


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bot_phase0.db"))
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _msg(text: str = "/journal today") -> BotMessage:
    return BotMessage(
        platform="telegram",
        message_id="1",
        user_id="u1",
        user_name="tester",
        chat_id="c1",
        chat_type=ChatType.PRIVATE,
        content=text,
    )


class TestJournalCommand:
    def test_today_without_data(self):
        from bot.commands.journal_cmd import JournalCommand

        resp = JournalCommand().execute(_msg(), [])
        assert resp is not None
        assert "尚未生成" in resp.text

    def test_reality_without_data(self):
        from bot.commands.journal_cmd import JournalCommand

        resp = JournalCommand().execute(_msg("/journal reality"), ["reality"])
        assert "尚无已关闭交易" in resp.text

    def test_reality_with_data(self):
        from bot.commands.journal_cmd import JournalCommand
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
                    avg_exit_price=3.0,
                    entry_time=datetime(2026, 4, 17, 10, 0),
                    pnl_gross=100.0,
                    pnl_net=99.0,
                    pnl_pct=49.5,
                )
            )
        resp = JournalCommand().execute(_msg(), ["reality"])
        assert "Reality Test" in resp.text
        assert "$99" in resp.text


class TestRegimeCommand:
    def test_no_score(self):
        from bot.commands.regime_cmd import RegimeCommand

        resp = RegimeCommand().execute(_msg(), [])
        assert "尚未计算" in resp.text

    def test_bad_date_arg(self):
        from bot.commands.regime_cmd import RegimeCommand

        resp = RegimeCommand().execute(_msg(), ["not-a-date"])
        assert "日期格式" in resp.text

    def test_shows_saved_score(self):
        from bot.commands.regime_cmd import RegimeCommand
        from src.regime.classifier import RegimeResult
        from src.regime.storage import save_regime_score

        save_regime_score(
            RegimeResult(
                date=date.today(),
                score=72,
                label="standard",
                action_hint="",
                d1_direction=22,
                d2_volatility=10,
                d3_macro_penalty=0,
                d4_sector=10,
                d5_prev_day=10,
                d6_premarket=20,
                snapshot={},
            )
        )
        resp = RegimeCommand().execute(_msg(), [])
        assert "+72" in resp.text
        assert "standard" in resp.text


class TestPhaseCommand:
    def test_default_phase_0(self):
        from bot.commands.phase_cmd import PhaseCommand

        resp = PhaseCommand().execute(_msg(), [])
        assert "Phase 0" in resp.text
        assert "Mirror" in resp.text
