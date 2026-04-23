# -*- coding: utf-8 -*-
"""Classifier + compute_regime_score tests with fetchers stubbed."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from src.regime.classifier import classify, compute_regime_score


class TestClassify:
    def test_thresholds(self):
        assert classify(80)[0] == "aggressive"
        assert classify(75)[0] == "aggressive"
        assert classify(74)[0] == "standard"
        assert classify(55)[0] == "standard"
        assert classify(54)[0] == "cautious"
        assert classify(35)[0] == "cautious"
        assert classify(34)[0] == "no_trade"
        assert classify(-50)[0] == "no_trade"

    def test_custom_thresholds(self):
        label, _ = classify(60, aggressive=50, standard=30, cautious=10)
        assert label == "aggressive"


class StubFetcher:
    def __init__(self, snapshot):
        self._snap = snapshot

    def get_spy_snapshot(self, d):
        return self._snap["spy"]

    def get_vix(self, d):
        return self._snap["vix"]

    def get_macro_events(self, d, wl):
        return self._snap["events"]

    def get_sector_performance(self, d, lookback_days=5):
        return self._snap["sectors"]

    def get_prev_day_structure(self, d):
        return self._snap["prev_day"]

    def get_premarket_activity(self, wl, d):
        return self._snap["premarket"]


def _rosy_snapshot():
    return {
        "spy": {"close": 520, "ma20": 510, "ma50": 500, "pct_change_5d": 2.0},
        "vix": {"level": 13.0, "pct_change_5d": 0},
        "events": {},
        "sectors": {"sectors_above_ma20": 10, "defensive_leaders": False},
        "prev_day": {"close_vs_high_pct": 0.95, "prev_day_range_pct": 2.0},
        "premarket": {"spy_pre_pct": 0.3, "watchlist_up_5pct": 3, "watchlist_down_5pct": 0},
    }


def _grim_snapshot():
    return {
        "spy": {"close": 400, "ma20": 420, "ma50": 450, "pct_change_5d": -5.0},
        "vix": {"level": 35.0, "pct_change_5d": 40.0},
        "events": {"fomc_today": True, "cpi_today": True, "tariff_headline_today": True},
        "sectors": {"sectors_above_ma20": 1, "defensive_leaders": True},
        "prev_day": {"close_vs_high_pct": 0.1, "prev_day_range_pct": 3.0},
        "premarket": {"spy_pre_pct": -1.2, "watchlist_down_5pct": 5, "watchlist_up_5pct": 0},
    }


class TestComputeRegimeScore:
    def test_rosy_day_aggressive(self):
        with patch("src.regime.classifier.RegimeDataFetcher", return_value=StubFetcher(_rosy_snapshot())):
            res = compute_regime_score(target_date=date(2026, 4, 17), save_to_db=False)
        assert res.label in ("aggressive", "standard")
        assert res.score > 55

    def test_grim_day_no_trade(self):
        with patch("src.regime.classifier.RegimeDataFetcher", return_value=StubFetcher(_grim_snapshot())):
            res = compute_regime_score(target_date=date(2026, 4, 17), save_to_db=False)
        assert res.label == "no_trade"
        assert res.score < 35

    def test_save_to_db(self):
        with patch("src.regime.classifier.RegimeDataFetcher", return_value=StubFetcher(_rosy_snapshot())):
            res = compute_regime_score(target_date=date(2026, 4, 17), save_to_db=True)

        from src.regime.storage import get_regime_score

        row = get_regime_score(date(2026, 4, 17))
        assert row is not None
        assert row["score"] == res.score
        assert row["label"] == res.label


class TestStorage:
    def test_upsert_keeps_one_row_per_date(self):
        snap = _rosy_snapshot()
        with patch("src.regime.classifier.RegimeDataFetcher", return_value=StubFetcher(snap)):
            compute_regime_score(target_date=date(2026, 4, 17), save_to_db=True)
            compute_regime_score(target_date=date(2026, 4, 17), save_to_db=True)

        from src.regime.storage import get_recent_scores

        rows = get_recent_scores(days=7)
        dates = [r["date"] for r in rows]
        assert dates.count(date(2026, 4, 17)) == 1
