# -*- coding: utf-8 -*-
"""Regime + Breakout API contract tests."""
from __future__ import annotations

from datetime import date, datetime

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "regime_api.db"))
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from api.v1.endpoints import breakout, regime

    app = FastAPI()
    app.include_router(regime.router, prefix="/api/v1/regime")
    app.include_router(breakout.router, prefix="/api/v1/breakout")
    return TestClient(app)


def _seed_regime():
    from src.regime.classifier import RegimeResult
    from src.regime.storage import save_regime_score

    today = date.today()
    save_regime_score(
        RegimeResult(
            date=today,
            score=70,
            label="standard",
            action_hint="Standard risk",
            d1_direction=20,
            d2_volatility=10,
            d3_macro_penalty=0,
            d4_sector=10,
            d5_prev_day=10,
            d6_premarket=20,
            snapshot={"spy": {"close": 520}},
        )
    )


def _seed_trade(style="breakout_chase", was_fake=True, pnl_net=-50.0):
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
                avg_exit_price=1.5,
                entry_time=datetime(2026, 4, 17, 10, 0),
                exit_time=datetime(2026, 4, 17, 10, 30),
                hold_seconds=30 * 60,
                dte_at_entry=0,
                pnl_gross=pnl_net,
                pnl_net=pnl_net,
                pnl_pct=-25.0,
                trade_style=style,
                was_fake_breakout=was_fake,
            )
        )


class TestRegimeToday:
    def test_none_when_empty(self):
        c = _client()
        resp = c.get("/api/v1/regime/today")
        assert resp.status_code == 200
        assert resp.json() is None

    def test_returns_item_after_seed(self):
        _seed_regime()
        c = _client()
        resp = c.get("/api/v1/regime/today")
        assert resp.status_code == 200
        body = resp.json()
        assert body["score"] == 70
        assert body["label"] == "standard"
        assert body["action_hint"]


class TestRegimeHistory:
    def test_empty_history(self):
        c = _client()
        resp = c.get("/api/v1/regime/history", params={"days": 30})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_history_after_seed(self):
        _seed_regime()
        c = _client()
        resp = c.get("/api/v1/regime/history", params={"days": 30})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["items"][0]["score"] == 70


class TestRegimeRecompute:
    def test_cooldown_triggers_429(self):
        """Regression: hammering /recompute must 429 after the first success."""
        from datetime import date
        from unittest.mock import patch

        import api.v1.endpoints.regime as reg_mod
        from src.regime.classifier import RegimeResult

        reg_mod._last_recompute_at = 0.0

        stub = RegimeResult(
            date=date.today(),
            score=60,
            label="standard",
            action_hint="",
            d1_direction=10,
            d2_volatility=10,
            d3_macro_penalty=0,
            d4_sector=10,
            d5_prev_day=10,
            d6_premarket=20,
            snapshot={},
        )
        row = {
            "date": stub.date,
            "score": stub.score,
            "label": stub.label,
            "action_hint": None,
            "d1_direction": 10,
            "d2_volatility": 10,
            "d3_macro_penalty": 0,
            "d4_sector": 10,
            "d5_prev_day": 10,
            "d6_premarket": 20,
            "snapshot": {},
            "version": "v1",
            "generated_at": None,
        }
        c = _client()
        with patch("api.v1.endpoints.regime.compute_regime_score", return_value=stub):
            with patch("api.v1.endpoints.regime.get_regime_score", return_value=row):
                first = c.post("/api/v1/regime/recompute")
                second = c.post("/api/v1/regime/recompute")
        assert first.status_code == 200
        assert second.status_code == 429
        reg_mod._last_recompute_at = 0.0


class TestBreakoutSignals:
    def test_empty(self):
        c = _client()
        resp = c.get("/api/v1/breakout/signals")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_returns_breakout_trades(self):
        _seed_trade("breakout_chase", was_fake=True)
        _seed_trade("retest", was_fake=False, pnl_net=100.0)
        _seed_trade("pullback_buy", was_fake=None, pnl_net=50.0)  # should NOT show
        c = _client()
        resp = c.get("/api/v1/breakout/signals")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        styles = {i["trade_style"] for i in body["items"]}
        assert styles == {"breakout_chase", "retest"}

    def test_filter_only_fake(self):
        _seed_trade("breakout_chase", was_fake=True)
        _seed_trade("retest", was_fake=False, pnl_net=100.0)
        c = _client()
        resp = c.get("/api/v1/breakout/signals", params={"only_fake": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert all(i["was_fake_breakout"] is True for i in body["items"])
