# -*- coding: utf-8 -*-
"""API contract for GET /api/v1/journal/edge-panel (Phase 1 E-5).

The market-data fetch is always monkeypatched — no test touches the
network.  Fixture approach mirrors ``test_journal_endpoints.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")

URL = "/api/v1/journal/edge-panel"


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal_edge_panel_api.db"))
    import src.config as config_mod
    import src.storage as storage
    from src.journal import edge_panel

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    edge_panel._reset_market_cache()
    yield
    edge_panel._reset_market_cache()
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _client():
    """Minimal FastAPI app with just the edge-panel router mounted."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from api.v1.endpoints import journal_edge

    app = FastAPI()
    app.include_router(journal_edge.router, prefix="/api/v1/journal")
    return TestClient(app)


# --- synthetic market data ----------------------------------------------------


def _alternating_up_closes(n: int) -> list[float]:
    closes = [100.0]
    for i in range(1, n):
        closes.append(closes[-1] * (1.004 if i % 2 == 0 else 1.0))
    return closes


def _patch_market_ok(monkeypatch):
    from src.journal import edge_panel

    def fake(symbol: str, sessions: int) -> list[float]:
        if symbol == "QQQ":
            return _alternating_up_closes(sessions)
        return [50.0 + i for i in range(sessions)]

    monkeypatch.setattr(edge_panel, "_default_fetch_closes", fake)


def _patch_market_down(monkeypatch):
    from src.journal import edge_panel

    def fake(symbol: str, sessions: int) -> list[float]:
        raise RuntimeError("simulated market data outage")

    monkeypatch.setattr(edge_panel, "_default_fetch_closes", fake)


# --- journal seeding ----------------------------------------------------------


def _seed_trades():
    """Insert deterministic journal_trades rows via the storage layer."""
    from src.journal.storage import init_journal_schema, replace_trades

    init_journal_schema()
    now_et = datetime.now(ET).replace(tzinfo=None)  # naive == ET wall-clock
    base = {
        "is_option": True,
        "underlying": "QQQ",
        "direction": "long",
        "quantity": 1,
        "avg_entry_price": 1.0,
    }
    trades = []
    # Twelve 2-7DTE closed winners on twelve distinct past days.
    for day in range(1, 13):
        trades.append(
            {
                **base,
                "status": "closed",
                "dte_at_entry": 3,
                "entry_time": now_et - timedelta(days=40 + day),
                "pnl_net": 100.0 + day,
                "pnl_gross": 102.0 + day,
                "total_fee": 2.0,
            }
        )
    # One 0-1DTE loser and one stock trade.
    trades.append(
        {
            **base,
            "status": "closed",
            "dte_at_entry": 0,
            "entry_time": now_et - timedelta(days=45),
            "pnl_net": -50.0,
            "pnl_gross": -50.0,
            "total_fee": 0.0,
        }
    )
    trades.append(
        {
            **base,
            "is_option": False,
            "status": "closed",
            "dte_at_entry": None,
            "entry_time": now_et - timedelta(days=45),
            "pnl_net": 10.0,
            "pnl_gross": 10.0,
            "total_fee": 0.0,
        }
    )
    # Two entries today: one closed with fees, one still open.
    trades.append(
        {
            **base,
            "status": "closed",
            "dte_at_entry": 2,
            "entry_time": now_et,
            "pnl_net": 50.0,
            "pnl_gross": 55.0,
            "total_fee": 5.0,
        }
    )
    trades.append(
        {
            **base,
            "status": "open",
            "dte_at_entry": 1,
            "entry_time": now_et,
            "pnl_net": None,
            "pnl_gross": None,
            "total_fee": 0.0,
        }
    )
    replace_trades(trades)


# --- tests --------------------------------------------------------------------


def test_edge_panel_contract_shape(monkeypatch):
    _patch_market_ok(monkeypatch)
    _seed_trades()
    resp = _client().get(URL)
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {"as_of", "gate", "edge", "discipline"}

    gate = body["gate"]
    assert gate["allow_0_1dte"] is True
    assert gate["default_bucket"] == "2-7"
    assert gate["blocked_by"] == []
    assert gate["data_status"] == "ok"
    assert gate["features"]["mom_q_20d"] > 0.0
    assert gate["features"]["mom_s_20d"] > 0.0
    assert gate["features"]["mm_scale"] == pytest.approx(1.0)

    buckets = body["edge"]["buckets"]
    assert set(buckets) == {"0-1", "2-7", "8-30", "31+", "stock"}
    assert buckets["2-7"]["n"] == 13
    assert buckets["0-1"]["n"] == 1
    assert buckets["0-1"]["total"] == pytest.approx(-50.0)
    assert buckets["stock"]["n"] == 1
    assert buckets["31+"]["n"] == 0
    assert buckets["31+"]["mean"] is None

    expectancy = body["edge"]["expectancy"]
    assert expectancy["n"] == 15
    assert expectancy["ci95_low"] <= expectancy["mean"] <= expectancy["ci95_high"]

    evidence = body["edge"]["evidence"]
    assert evidence["hlz_hurdle"] == 3.0
    assert evidence["status"] in {"significant", "insufficient"}
    # 13 distinct 2-7DTE days with a strongly positive mean -> real t-stat.
    assert evidence["h1_t_stat"] is not None
    assert evidence["n_needed"] is not None

    discipline = body["discipline"]
    assert discipline["trades_today"] == 2
    assert discipline["daily_cap"] == 8
    assert discipline["month_fees"] >= 5.0
    assert discipline["fee_ratio"] is None or discipline["fee_ratio"] > 0.0


def test_edge_panel_market_failure_fails_closed(monkeypatch):
    _patch_market_down(monkeypatch)
    _seed_trades()
    resp = _client().get(URL)
    assert resp.status_code == 200
    body = resp.json()

    gate = body["gate"]
    assert gate["allow_0_1dte"] is False
    assert gate["blocked_by"] == ["market_data_unavailable"]
    assert gate["data_status"] == "unavailable"
    assert gate["features"] == {"mom_q_20d": None, "mom_s_20d": None, "mm_scale": None}
    assert gate["default_bucket"] == "2-7"

    # Journal-derived blocks stay real even when the market feed is down.
    assert body["edge"]["buckets"]["2-7"]["n"] == 13
    assert body["discipline"]["trades_today"] == 2


def test_edge_panel_empty_journal(monkeypatch):
    _patch_market_ok(monkeypatch)
    from src.journal.storage import init_journal_schema

    init_journal_schema()
    resp = _client().get(URL)
    assert resp.status_code == 200
    body = resp.json()

    buckets = body["edge"]["buckets"]
    for label in ("0-1", "2-7", "8-30", "31+", "stock"):
        assert buckets[label]["n"] == 0
        assert buckets[label]["total"] == 0.0
        assert buckets[label]["mean"] is None
        assert buckets[label]["win_rate"] is None

    expectancy = body["edge"]["expectancy"]
    assert expectancy == {"n": 0, "mean": None, "ci95_low": None, "ci95_high": None}

    evidence = body["edge"]["evidence"]
    assert evidence["h1_t_stat"] is None
    assert evidence["status"] == "insufficient"
    assert evidence["n_needed"] is None
    assert evidence["n_remaining"] is None

    discipline = body["discipline"]
    assert discipline == {
        "trades_today": 0,
        "daily_cap": 8,
        "month_fees": 0.0,
        "month_gross": 0.0,
        "fee_ratio": None,
    }

    # Gate is independent of the journal.
    assert body["gate"]["data_status"] == "ok"
