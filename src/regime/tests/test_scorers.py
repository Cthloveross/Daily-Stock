# -*- coding: utf-8 -*-
"""Scorer unit tests — edge cases + monotonicity."""
from __future__ import annotations

from src.regime.scorers import (
    score_macro_penalty,
    score_market_direction,
    score_premarket_activity,
    score_prev_day_structure,
    score_sector_rotation,
    score_volatility,
)


class TestMarketDirection:
    def test_strong_uptrend_maxed(self):
        s = score_market_direction(
            {"close": 520, "ma20": 510, "ma50": 500, "pct_change_5d": 3.0}
        )
        assert s == 26  # 10 (close>ma20) + 10 (ma20>ma50) + 6 (momentum)

    def test_weak_market_floor(self):
        s = score_market_direction(
            {"close": 450, "ma20": 470, "ma50": 500, "pct_change_5d": -4.0}
        )
        assert s == 0  # clamped

    def test_empty_dict_safe(self):
        assert score_market_direction({}) == 0


class TestVolatility:
    def test_calm_vix(self):
        assert score_volatility({"level": 12.0, "pct_change_5d": 0}) == 20

    def test_crisis_vix(self):
        s = score_volatility({"level": 40.0, "pct_change_5d": 50.0})
        assert s == -15  # clamped

    def test_mid_range(self):
        assert score_volatility({"level": 22.0, "pct_change_5d": 0}) == 0


class TestMacroPenalty:
    def test_all_events_max_penalty(self):
        s = score_macro_penalty(
            {
                "fomc_today": True,
                "cpi_today": True,
                "nfp_today": True,
                "earnings_count_watchlist": 5,
                "tariff_headline_today": True,
            }
        )
        assert s == -50  # clamped

    def test_clean_day_zero(self):
        assert score_macro_penalty({}) == 0


class TestSectorRotation:
    def test_broad_risk_on(self):
        s = score_sector_rotation(
            {"sectors_above_ma20": 10, "defensive_leaders": False}
        )
        assert 10 <= s <= 15

    def test_defensive_leadership_penalty(self):
        s = score_sector_rotation(
            {"sectors_above_ma20": 5, "defensive_leaders": True}
        )
        # sectors_above=5 -> scaled 4; defensive -3 -> 1
        assert s == 1

    def test_bearish_breadth(self):
        assert score_sector_rotation({"sectors_above_ma20": 0}) == -5


class TestPrevDay:
    def test_strong_close_near_high(self):
        s = score_prev_day_structure(
            {"close_vs_high_pct": 0.95, "prev_day_range_pct": 2.5}
        )
        assert s == 13  # 10 + 3

    def test_weak_close_near_low(self):
        assert score_prev_day_structure({"close_vs_high_pct": 0.1}) == -2


class TestPremarket:
    def test_bullish_gapup(self):
        s = score_premarket_activity(
            {"spy_pre_pct": 0.5, "watchlist_up_5pct": 3, "watchlist_down_5pct": 0}
        )
        # 8 + 6 = 14
        assert s == 14

    def test_bearish_gapdown(self):
        s = score_premarket_activity(
            {"spy_pre_pct": -1.0, "watchlist_up_5pct": 0, "watchlist_down_5pct": 3}
        )
        assert s == 0  # -5 - 3 clamped at 0

    def test_cap_up_moves(self):
        s = score_premarket_activity({"spy_pre_pct": 1.0, "watchlist_up_5pct": 10})
        # 8 + min(10,5)*2 = 8 + 10 = 18; clamp 20
        assert 18 <= s <= 20


class TestSum:
    def test_total_score_is_within_classifier_bands(self):
        s = sum(
            [
                score_market_direction({"close": 510, "ma20": 505, "ma50": 500, "pct_change_5d": 1.0}),
                score_volatility({"level": 14.0, "pct_change_5d": 0}),
                score_macro_penalty({}),
                score_sector_rotation({"sectors_above_ma20": 8}),
                score_prev_day_structure({"close_vs_high_pct": 0.8}),
                score_premarket_activity({"spy_pre_pct": 0.2, "watchlist_up_5pct": 2}),
            ]
        )
        # Should comfortably land in aggressive territory.
        assert s >= 55
