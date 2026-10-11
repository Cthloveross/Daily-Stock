# -*- coding: utf-8 -*-
"""Morning-brief template + CLI tests."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.regime.classifier import RegimeResult
from src.regime.morning_brief import format_brief, send_brief


def _mk_result(label="standard", score=60, events=None):
    return RegimeResult(
        date=date(2026, 4, 17),
        score=score,
        label=label,
        action_hint="Regime context only; not a DTE or underlying-signal gate.",
        d1_direction=20,
        d2_volatility=10,
        d3_macro_penalty=0,
        d4_sector=10,
        d5_prev_day=10,
        d6_premarket=10,
        snapshot={
            "spy": {"close": 520.12, "ma20": 510.45, "pct_change_5d": 1.23},
            "vix": {"level": 14.5},
            "events": events or {},
        },
    )


class TestFormatBrief:
    def test_basic_render(self):
        body = format_brief(_mk_result())
        assert "Regime Brief" in body
        assert "2026-04-17" in body
        assert "mixed" in body
        assert "+60" in body
        assert "SPY" in body
        assert "520.12" in body

    def test_renders_macro_event_lines(self):
        events = {"fomc_today": True, "cpi_today": True}
        body = format_brief(_mk_result(events=events))
        assert "FOMC" in body
        assert "CPI" in body

    def test_no_trade_has_warning(self):
        body = format_brief(_mk_result(label="no_trade", score=20))
        assert "adverse" in body
        assert "Warnings" in body
        assert "not a DTE or underlying-signal gate" in body
        assert "stand aside" not in body.lower()
        assert "no new risk" not in body.lower()

    def test_event_and_crisis_warnings_are_facts_not_trade_instructions(self):
        result = _mk_result(label="cautious", score=40)
        result.d3_macro_penalty = -30
        result.d2_volatility = -15
        body = format_brief(result)
        assert "higher_risk" in body
        assert "event and jump risk is elevated" in body
        assert "volatility and liquidity risk is elevated" in body
        assert "avoid held" not in body
        assert "size down" not in body
        assert "widen stops" not in body

    def test_handles_missing_vix(self):
        r = _mk_result()
        r.snapshot["vix"] = {}
        # Should not raise.
        format_brief(r)


class TestSendBrief:
    def test_prints_when_telegram_unconfigured(self, capsys):
        with patch("src.notification_sender.telegram_sender.TelegramSender") as Sender:
            instance = MagicMock()
            instance._is_telegram_configured.return_value = False
            Sender.return_value = instance
            ok = send_brief(_mk_result())
        captured = capsys.readouterr()
        assert ok is False
        assert "Regime Brief" in captured.out

    def test_sends_when_configured(self):
        with patch("src.notification_sender.telegram_sender.TelegramSender") as Sender:
            instance = MagicMock()
            instance._is_telegram_configured.return_value = True
            instance.send_to_telegram.return_value = True
            Sender.return_value = instance
            ok = send_brief(_mk_result())
        assert ok is True
        instance.send_to_telegram.assert_called_once()
