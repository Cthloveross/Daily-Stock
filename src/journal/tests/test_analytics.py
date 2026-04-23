# -*- coding: utf-8 -*-
"""Analytics unit tests: reality_test, dte_distribution, health_check."""
from __future__ import annotations

from datetime import date, datetime

from src.journal.analytics import (
    daily_health_check,
    dte_bucket_win_rates,
    dte_distribution,
    reality_test,
)


def _tr(idx, pnl, *, status="closed", is_option=True, dte=None, bucket=None):
    return {
        "id": idx,
        "pnl_net": pnl,
        "status": status,
        "is_option": is_option,
        "dte_at_entry": dte,
        "dte_bucket": bucket,
        "underlying": "NVDA",
    }


class TestRealityTest:
    def test_empty(self):
        r = reality_test([], top_n=5)
        assert r["total_trades"] == 0
        assert r["total_pnl_net"] == 0.0
        assert r["top_n_ids"] == []

    def test_concentration(self):
        # 10 trades: 5 big wins + 5 small losses; top 5 should dominate.
        trades = [
            _tr(1, 10000),
            _tr(2, 8000),
            _tr(3, 5000),
            _tr(4, 3000),
            _tr(5, 2000),
            _tr(6, -100),
            _tr(7, -150),
            _tr(8, -50),
            _tr(9, -200),
            _tr(10, -300),
        ]
        r = reality_test(trades, top_n=5)
        assert r["total_trades"] == 10
        assert r["total_pnl_net"] == 27200
        assert r["top_n_pnl_net"] == 28000
        assert r["pnl_without_top_n"] == -800
        assert r["top_n_pct_of_total"] > 100  # super-concentrated profit
        assert set(r["top_n_ids"]) == {1, 2, 3, 4, 5}

    def test_top_n_exceeds_count(self):
        trades = [_tr(1, 500), _tr(2, -100)]
        r = reality_test(trades, top_n=10)
        assert r["top_n_pnl_net"] == 400
        assert r["pnl_without_top_n"] == 0

    def test_ignores_open_trades(self):
        trades = [
            _tr(1, 500),
            _tr(2, None, status="open"),
        ]
        r = reality_test(trades)
        assert r["total_trades"] == 1

    def test_all_losses(self):
        trades = [_tr(1, -100), _tr(2, -50), _tr(3, -300)]
        r = reality_test(trades, top_n=5)
        assert r["total_pnl_net"] == -450
        assert r["top_n_pnl_net"] == -450  # "top" = least negative total


class TestDteDistribution:
    def test_mixed_buckets(self):
        trades = [
            _tr(1, 100, bucket="0DTE"),
            _tr(2, 100, bucket="0DTE"),
            _tr(3, 100, bucket="1-3DTE"),
            _tr(4, 100, is_option=False),
            _tr(5, 100, is_option=False),
        ]
        dist = dte_distribution(trades)
        assert dist["0DTE"] == 2
        assert dist["1-3DTE"] == 1
        assert dist["equity"] == 2

    def test_dte_auto_bucket(self):
        trades = [_tr(1, 100, dte=2)]
        dist = dte_distribution(trades)
        assert dist["1-3DTE"] == 1


class TestWinRate:
    def test_basic(self):
        trades = [
            _tr(1, 100, bucket="1-3DTE"),
            _tr(2, -50, bucket="1-3DTE"),
            _tr(3, 200, bucket="0DTE"),
        ]
        wr = dte_bucket_win_rates(trades)
        assert wr["1-3DTE"]["count"] == 2
        assert wr["1-3DTE"]["wins"] == 1
        assert wr["1-3DTE"]["win_rate"] == 0.5
        assert wr["0DTE"]["count"] == 1
        assert wr["0DTE"]["win_rate"] == 1.0


class TestDailyHealthCheck:
    def test_minimal(self):
        orders = [
            {
                "is_option": True,
                "underlying": "NVDA",
                "expiry": date(2026, 4, 17),
                "first_fill_time": datetime(2026, 4, 16, 14, 00),  # UTC 14:00 = EDT 10:00 -> outside opening hour
            },
            {
                "is_option": False,
                "underlying": "NVDA",
                "first_fill_time": datetime(2026, 4, 16, 13, 45),  # UTC 13:45 = EDT 09:45 -> opening hour
            },
        ]
        closed = [{"pnl_net": 150.0}, {"pnl_net": -50.0}]
        hc = daily_health_check(orders, closed, target_date=date(2026, 4, 16))
        assert hc["total_orders"] == 2
        assert hc["orders_0dte"] == 0  # expiry is 17th, fill is 16th -> 1DTE
        assert hc["orders_1_3dte"] == 1
        assert hc["orders_opening_hour"] == 1
        assert hc["top_underlying"] == "NVDA"
        assert hc["top_underlying_pct"] == 100.0
        assert hc["pnl_estimate"] == 100.0
        assert hc["warnings_json"] == []
        assert hc["regime_score"] is None
