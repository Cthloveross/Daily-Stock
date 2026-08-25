# -*- coding: utf-8 -*-
"""Tests for src.journal.edge_panel (E-5 payload assembly).

Pure-function coverage: the market fetch is always injected, so no test
touches the network.  The API-level contract is exercised separately in
``api/v1/tests/test_journal_edge_panel_endpoint.py``.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.journal.edge_panel import (
    _et_date,
    _failed_gate,
    build_edge_panel,
    compute_gate,
)

# --- deterministic synthetic market data ------------------------------------


def _alternating_up_closes(n: int, start: float = 100.0, step: float = 1.004) -> list[float]:
    """Rising series whose daily returns alternate step-1 and 0.

    Every 20-return window holds exactly ten of each value, so the rolling
    20d variance is constant: recent == baseline -> mm scale == 1.0, and the
    trailing 20d momentum is positive.
    """
    closes = [start]
    for i in range(1, n):
        closes.append(closes[-1] * (step if i % 2 == 0 else 1.0))
    return closes


def _passing_fetch(symbol: str, sessions: int) -> list[float]:
    if symbol == "QQQ":
        return _alternating_up_closes(sessions)
    return [50.0 + i for i in range(sessions)]  # rising sector proxy


def _failing_fetch(symbol: str, sessions: int) -> list[float]:
    raise RuntimeError("network down")


# --- gate --------------------------------------------------------------------


def test_gate_passes_on_trending_low_vol_market():
    gate = compute_gate(_passing_fetch)
    assert gate["allow_0_1dte"] is True
    assert gate["blocked_by"] == []
    assert gate["default_bucket"] == "2-7"
    assert gate["data_status"] == "ok"
    feats = gate["features"]
    assert feats["mom_q_20d"] > 0.0
    assert feats["mom_s_20d"] > 0.0
    assert feats["mm_scale"] == pytest.approx(1.0)


def test_gate_fails_closed_when_fetch_raises():
    assert compute_gate(_failing_fetch) == _failed_gate()
    shape = compute_gate(_failing_fetch)
    assert shape["allow_0_1dte"] is False
    assert shape["blocked_by"] == ["market_data_unavailable"]
    assert shape["data_status"] == "unavailable"
    assert shape["features"] == {"mom_q_20d": None, "mom_s_20d": None, "mm_scale": None}


def test_gate_fails_closed_on_empty_series():
    gate = compute_gate(lambda symbol, sessions: [])
    assert gate == _failed_gate()


def test_gate_too_short_for_momentum_is_unavailable():
    gate = compute_gate(lambda symbol, sessions: _alternating_up_closes(10))
    assert gate == _failed_gate()


def test_gate_short_variance_history_blocks_volatility_scale_only():
    # 100 closes: momentum computable, 250-window baseline is not.
    gate = compute_gate(
        lambda symbol, sessions: _alternating_up_closes(min(sessions, 100))
    )
    assert gate["allow_0_1dte"] is False
    assert gate["blocked_by"] == ["volatility_scale"]
    assert gate["data_status"] == "ok"
    assert gate["features"]["mom_q_20d"] > 0.0
    assert gate["features"]["mm_scale"] is None


def test_gate_negative_momentum_blocks():
    def declining(symbol: str, sessions: int) -> list[float]:
        closes = _alternating_up_closes(sessions)
        return list(reversed(closes))

    gate = compute_gate(declining)
    assert gate["allow_0_1dte"] is False
    assert "market_momentum" in gate["blocked_by"]
    assert "sector_momentum" in gate["blocked_by"]
    assert gate["data_status"] == "ok"


# --- helpers -----------------------------------------------------------------


def test_et_date_naive_is_assumed_eastern():
    assert _et_date(datetime(2026, 6, 15, 9, 31)) == datetime(2026, 6, 15).date()


def test_et_date_aware_is_converted():
    from datetime import timezone

    # 2026-06-16 01:00 UTC == 2026-06-15 21:00 ET (EDT).
    aware = datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc)
    assert _et_date(aware) == datetime(2026, 6, 15).date()


# --- payload assembly --------------------------------------------------------


_NOW = datetime(2026, 6, 15, 12, 0)  # naive == ET by module convention

_H1_PNLS = [100.0, 120.0, 90.0, 110.0, 80.0, 130.0, 105.0, 95.0, 115.0, 85.0, 125.0, 100.0]


def _seed_trades() -> list[dict]:
    trades: list[dict] = []
    for day, pnl in enumerate(_H1_PNLS, start=1):
        trades.append(
            {
                "status": "closed",
                "dte_at_entry": 3,
                "entry_time": datetime(2026, 6, day, 10, 0),
                "pnl_net": pnl,
                "pnl_gross": pnl + 2.0,
                "total_fee": 2.0,
            }
        )
    trades.append(
        {
            "status": "closed",
            "dte_at_entry": 0,
            "entry_time": datetime(2026, 6, 5, 11, 0),
            "pnl_net": -50.0,
            "pnl_gross": -50.0,
            "total_fee": 0.0,
        }
    )
    trades.append(
        {
            "status": "closed",
            "dte_at_entry": None,
            "entry_time": datetime(2026, 6, 5, 12, 0),
            "pnl_net": 10.0,
            "pnl_gross": 10.0,
            "total_fee": 0.0,
        }
    )
    # Today: one closed 2DTE trade and one still-open trade.
    trades.append(
        {
            "status": "closed",
            "dte_at_entry": 2,
            "entry_time": datetime(2026, 6, 15, 9, 31),
            "pnl_net": 50.0,
            "pnl_gross": 55.0,
            "total_fee": 5.0,
        }
    )
    trades.append(
        {
            "status": "open",
            "dte_at_entry": 1,
            "entry_time": datetime(2026, 6, 15, 10, 15),
            "pnl_net": None,
            "pnl_gross": None,
            "total_fee": 0.0,
        }
    )
    return trades


def test_build_edge_panel_full_payload():
    payload = build_edge_panel(_seed_trades(), fetch_closes=_passing_fetch, now=_NOW)

    assert payload["as_of"].startswith("2026-06-15T12:00:00")

    buckets = payload["edge"]["buckets"]
    assert set(buckets) == {"0-1", "2-7", "8-30", "31+", "stock"}
    assert buckets["2-7"]["n"] == 13
    assert buckets["2-7"]["total"] == pytest.approx(sum(_H1_PNLS) + 50.0)
    assert buckets["0-1"]["n"] == 1
    assert buckets["0-1"]["total"] == pytest.approx(-50.0)
    assert buckets["stock"]["n"] == 1
    assert buckets["8-30"] == {"n": 0, "total": 0.0, "mean": None, "win_rate": None}

    expectancy = payload["edge"]["expectancy"]
    assert expectancy["n"] == 15
    assert expectancy["mean"] is not None
    assert expectancy["ci95_low"] is not None
    assert expectancy["ci95_low"] <= expectancy["mean"] <= expectancy["ci95_high"]

    evidence = payload["edge"]["evidence"]
    assert evidence["hlz_hurdle"] == 3.0
    assert evidence["h1_t_stat"] is not None
    assert evidence["h1_t_stat"] >= 3.0
    assert evidence["status"] == "significant"
    assert evidence["n_needed"] is not None
    assert evidence["n_remaining"] == 0

    discipline = payload["discipline"]
    assert discipline["trades_today"] == 2
    assert discipline["daily_cap"] == 8
    assert discipline["month_fees"] == pytest.approx(29.0)
    assert discipline["month_gross"] == pytest.approx(sum(_H1_PNLS) + 24.0 + 55.0 - 50.0 + 10.0)
    assert discipline["fee_ratio"] == pytest.approx(29.0 / (sum(_H1_PNLS) + 24.0 + 55.0 - 50.0 + 10.0))

    assert payload["gate"]["allow_0_1dte"] is True


def test_build_edge_panel_empty_journal():
    payload = build_edge_panel([], fetch_closes=_passing_fetch, now=_NOW)
    buckets = payload["edge"]["buckets"]
    for label in ("0-1", "2-7", "8-30", "31+", "stock"):
        assert buckets[label]["n"] == 0
        assert buckets[label]["mean"] is None
    expectancy = payload["edge"]["expectancy"]
    assert expectancy == {"n": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    evidence = payload["edge"]["evidence"]
    assert evidence["h1_t_stat"] is None
    assert evidence["status"] == "insufficient"
    assert evidence["n_needed"] is None
    assert evidence["n_remaining"] is None
    discipline = payload["discipline"]
    assert discipline["trades_today"] == 0
    assert discipline["month_fees"] == 0.0
    assert discipline["month_gross"] == 0.0
    assert discipline["fee_ratio"] is None


def test_build_edge_panel_market_failure_keeps_journal_blocks():
    payload = build_edge_panel(_seed_trades(), fetch_closes=_failing_fetch, now=_NOW)
    assert payload["gate"] == _failed_gate()
    assert payload["edge"]["buckets"]["2-7"]["n"] == 13
    assert payload["discipline"]["trades_today"] == 2


def test_t_stat_needs_ten_days():
    # Only 5 distinct 2-7DTE entry days -> t-stat withheld.
    trades = [
        {
            "status": "closed",
            "dte_at_entry": 4,
            "entry_time": datetime(2026, 6, day, 10, 0),
            "pnl_net": 100.0 + day,
            "pnl_gross": 100.0 + day,
            "total_fee": 0.0,
        }
        for day in range(1, 6)
    ]
    payload = build_edge_panel(trades, fetch_closes=_passing_fetch, now=_NOW)
    evidence = payload["edge"]["evidence"]
    assert evidence["h1_t_stat"] is None
    assert evidence["status"] == "insufficient"
