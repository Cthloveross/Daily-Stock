# -*- coding: utf-8 -*-
"""Edge-stats unit tests: expectancy, sample size, DTE buckets, fees, Kelly."""
from __future__ import annotations

import math
from datetime import datetime

from src.journal.edge_stats import (
    dte_bucket_expectancy,
    expectancy_stats,
    fee_drag,
    kelly_estimate,
    monthly_breakdown,
    required_sample_estimate,
    trade_frequency,
)


class TestExpectancyStats:
    def test_known_answer(self):
        # Hand-computed: mean 50, median 25, 2 wins / 2 losses.
        pnls = [100.0, -50.0, 200.0, -50.0]
        r = expectancy_stats(pnls)
        assert r["n"] == 4
        assert math.isclose(r["mean"], 50.0)
        assert math.isclose(r["median"], 25.0)
        assert math.isclose(r["win_rate"], 0.5)
        assert math.isclose(r["avg_win"], 150.0)
        assert math.isclose(r["avg_loss"], -50.0)
        assert math.isclose(r["payoff_ratio"], 3.0)
        # Bootstrap CI must straddle the sample mean and stay within the data range.
        assert r["ci95_low"] <= 50.0 <= r["ci95_high"]
        assert min(pnls) <= r["ci95_low"] <= r["ci95_high"] <= max(pnls)

    def test_bootstrap_deterministic_for_fixed_seed(self):
        pnls = [100.0, -50.0, 200.0, -50.0, 30.0, -10.0]
        r1 = expectancy_stats(pnls, n_boot=500, seed=7)
        r2 = expectancy_stats(pnls, n_boot=500, seed=7)
        assert r1["ci95_low"] == r2["ci95_low"]
        assert r1["ci95_high"] == r2["ci95_high"]

    def test_insufficient_n_fail_closed(self):
        for pnls in ([], [5.0]):
            r = expectancy_stats(pnls)
            assert r["n"] == len(pnls)
            for key in (
                "mean",
                "median",
                "win_rate",
                "avg_win",
                "avg_loss",
                "payoff_ratio",
                "ci95_low",
                "ci95_high",
            ):
                assert r[key] is None

    def test_no_losses(self):
        r = expectancy_stats([10.0, 20.0])
        assert r["win_rate"] == 1.0
        assert r["avg_loss"] is None
        assert r["payoff_ratio"] is None


class TestRequiredSampleEstimate:
    def test_known_answer(self):
        # Six +10 and four -5: mean 4, sample variance 540/9 = 60.
        # n_needed = ceil(2.485^2 * 60 / 16) = ceil(23.157) = 24.
        pnls = [10.0] * 6 + [-5.0] * 4
        r = required_sample_estimate(pnls)
        assert r["n_current"] == 10
        assert math.isclose(r["mean"], 4.0)
        assert r["n_needed"] == 24
        assert r["n_remaining"] == 14

    def test_negative_mean_fail_closed(self):
        pnls = [-10.0] * 8 + [5.0] * 4  # n=12, mean < 0
        r = required_sample_estimate(pnls)
        assert r["n_current"] == 12
        assert r["mean"] < 0
        assert r["n_needed"] is None
        assert r["n_remaining"] is None

    def test_small_n_fail_closed(self):
        r = required_sample_estimate([5.0] * 9)  # positive mean, but n < 10
        assert r["n_current"] == 9
        assert r["n_needed"] is None
        assert r["n_remaining"] is None


def _dt(dte, pnl):
    return {"dte_at_entry": dte, "pnl_net": pnl}


class TestDteBucketExpectancy:
    def test_boundaries_and_stock(self):
        # dte=1 vs 2 is the sign-flip boundary from doc 16.
        trades = [
            _dt(0, 10.0),
            _dt(1, -4.0),
            _dt(2, 20.0),
            _dt(7, -10.0),
            _dt(8, 5.0),
            _dt(30, 5.0),
            _dt(31, 100.0),
            _dt(None, 7.0),  # stock row
        ]
        r = dte_bucket_expectancy(trades)
        assert r["0-1"]["n"] == 2
        assert math.isclose(r["0-1"]["total"], 6.0)
        assert math.isclose(r["0-1"]["mean"], 3.0)
        assert math.isclose(r["0-1"]["win_rate"], 0.5)
        assert r["2-7"]["n"] == 2
        assert math.isclose(r["2-7"]["total"], 10.0)
        assert r["8-30"]["n"] == 2
        assert math.isclose(r["8-30"]["win_rate"], 1.0)
        assert r["31+"]["n"] == 1
        assert math.isclose(r["31+"]["total"], 100.0)
        assert r["stock"]["n"] == 1
        assert math.isclose(r["stock"]["mean"], 7.0)

    def test_skips_pnl_none_and_keeps_empty_buckets(self):
        r = dte_bucket_expectancy([_dt(1, None)])
        for label in ("0-1", "2-7", "8-30", "31+", "stock"):
            assert r[label] == {"n": 0, "total": 0.0, "mean": None, "win_rate": None}


class TestFeeDrag:
    def test_known_answer(self):
        trades = [
            {"total_fee": 2.0, "pnl_gross": 60.0},
            {"total_fee": 1.0, "pnl_gross": 40.0},
        ]
        r = fee_drag(trades)
        assert math.isclose(r["total_fees"], 3.0)
        assert math.isclose(r["total_gross"], 100.0)
        assert math.isclose(r["total_net"], 97.0)
        assert math.isclose(r["fee_to_gross_ratio"], 0.03)

    def test_ratio_none_when_gross_near_zero(self):
        trades = [
            {"total_fee": 1.5, "pnl_gross": 50.0},
            {"total_fee": 1.5, "pnl_gross": -50.0},
        ]
        r = fee_drag(trades)
        assert r["fee_to_gross_ratio"] is None
        assert math.isclose(r["total_net"], -3.0)

    def test_missing_fields_count_as_zero(self):
        r = fee_drag([{"pnl_gross": 10.0}, {"total_fee": None, "pnl_gross": None}])
        assert math.isclose(r["total_fees"], 0.0)
        assert math.isclose(r["total_gross"], 10.0)


class TestTradeFrequency:
    def test_mixed_datetime_and_string_inputs(self):
        trades = [
            {"entry_time": datetime(2026, 1, 5, 14, 30)},
            {"entry_time": "2026-01-05T15:00:00"},
            {"entry_time": "2026-01-06"},
            {"entry_time": "garbage"},  # unparseable -> skipped
            {"entry_time": None},  # missing -> skipped
        ]
        r = trade_frequency(trades)
        assert r["n"] == 3
        assert r["active_days"] == 2
        assert math.isclose(r["avg_per_day"], 1.5)
        assert r["max_day"] == "2026-01-05"
        assert r["max_day_count"] == 2

    def test_empty(self):
        r = trade_frequency([])
        assert r == {
            "n": 0,
            "active_days": 0,
            "avg_per_day": None,
            "max_day": None,
            "max_day_count": None,
        }


class TestKellyEstimate:
    def test_fail_closed_under_30(self):
        pnls = [100.0] * 20 + [-50.0] * 9  # n=29
        r = kelly_estimate(pnls)
        assert r == {"f_star": None, "half_kelly": None, "quarter_kelly": None}

    def test_known_answer(self):
        # p=0.6, b = 100/50 = 2 -> f* = 0.6 - 0.4/2 = 0.4.
        pnls = [100.0] * 18 + [-50.0] * 12
        r = kelly_estimate(pnls)
        assert math.isclose(r["f_star"], 0.4)
        assert math.isclose(r["half_kelly"], 0.2)
        assert math.isclose(r["quarter_kelly"], 0.1)

    def test_fail_closed_without_losses(self):
        r = kelly_estimate([10.0] * 30)
        assert r["f_star"] is None


class TestMonthlyBreakdown:
    def test_grouping_across_year_boundary(self):
        trades = [
            {"entry_time": "2025-12-30T10:00:00", "pnl_net": 100.0, "total_fee": 1.0},
            {"entry_time": datetime(2025, 12, 31, 9, 30), "pnl_net": -50.0, "total_fee": 2.0},
            {"entry_time": "2026-01-02", "pnl_net": 30.0},  # missing fee -> 0.0
            {"entry_time": "2026-01-05", "pnl_net": None},  # skipped
        ]
        r = monthly_breakdown(trades)
        assert list(r.keys()) == ["2025-12", "2026-01"]
        dec = r["2025-12"]
        assert dec["n"] == 2
        assert math.isclose(dec["total_net"], 50.0)
        assert math.isclose(dec["mean"], 25.0)
        assert math.isclose(dec["win_rate"], 0.5)
        assert math.isclose(dec["total_fees"], 3.0)
        jan = r["2026-01"]
        assert jan["n"] == 1
        assert math.isclose(jan["total_net"], 30.0)
        assert math.isclose(jan["win_rate"], 1.0)
        assert math.isclose(jan["total_fees"], 0.0)

    def test_empty(self):
        assert monthly_breakdown([]) == {}
