# -*- coding: utf-8 -*-
"""Deterministic tests for the pure intraday tracking indicator math."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.opportunities.intraday import (
    ATR14_METHOD,
    SESSION_STATE_BASIS,
    VOLUME_PACE_BASIS,
    VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
    compute_atr14,
    compute_prior_full_day_median_volume,
    compute_session_vwap,
    compute_volume_pace,
    market_session_state,
)

_NEW_YORK = ZoneInfo("America/New_York")


def _bar(day: date, *, high: float, low: float, close: float, volume: float = 1_000.0):
    return {
        "date": day,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": close * volume,
    }


def _flat_bars(count: int, *, close: float = 100.0, spread: float = 2.0, volume: float = 1_000.0):
    start = date(2026, 6, 1)
    return [
        _bar(
            start + timedelta(days=index),
            high=close + spread / 2,
            low=close - spread / 2,
            close=close,
            volume=volume,
        )
        for index in range(count)
    ]


class TestSessionVwap:
    def test_happy_path_is_turnover_over_volume(self):
        result = compute_session_vwap(1_250_000.0, 10_000)
        assert result.value == 125.0
        assert result.basis == VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME
        assert result.unavailable_reason is None

    def test_missing_turnover_fails_closed_with_reason(self):
        result = compute_session_vwap(None, 10_000)
        assert result.value is None
        assert result.unavailable_reason == "missing_or_nonpositive_turnover"

    def test_zero_volume_never_divides_or_fabricates(self):
        result = compute_session_vwap(1_250_000.0, 0)
        assert result.value is None
        assert result.unavailable_reason == "missing_or_nonpositive_volume"

    def test_both_missing_is_a_single_explicit_reason(self):
        result = compute_session_vwap(None, None)
        assert result.value is None
        assert result.unavailable_reason == "missing_turnover_and_volume"


class TestAtr14:
    def test_hand_checked_wilder_seed(self):
        # 15 identical bars: every TR = high − low = 2.0 (no gaps), so the
        # Wilder seed — and therefore ATR14 — must be exactly 2.0.
        result = compute_atr14(_flat_bars(15))
        assert result.value == 2.0
        assert result.method == ATR14_METHOD
        assert result.bar_count == 15
        assert result.unavailable_reason is None

    def test_hand_checked_wilder_smoothing_step(self):
        # 15 flat bars seed ATR = 2.0; one extra bar gaps up: prev close 100,
        # high 110, low 106 → TR = max(4, |110−100|, |106−100|) = 10.
        # Wilder: ATR = (2.0 × 13 + 10) / 14 = 36 / 14 = 2.571428…
        bars = _flat_bars(15)
        bars.append(_bar(date(2026, 6, 16), high=110.0, low=106.0, close=108.0))
        result = compute_atr14(bars)
        assert result.value == pytest.approx(36.0 / 14.0, abs=1e-6)
        assert result.bar_count == 16
        assert result.last_bar_date == str(date(2026, 6, 16))

    def test_fourteen_bars_are_not_enough(self):
        result = compute_atr14(_flat_bars(14))
        assert result.value is None
        assert result.unavailable_reason == "insufficient_completed_bars:14<15"

    def test_gap_in_high_low_fields_restarts_the_window(self):
        bars = _flat_bars(20)
        bars[10]["high"] = None  # a hole must not be bridged silently
        result = compute_atr14(bars)
        # Only 9 consecutive usable bars remain after the hole.
        assert result.value is None
        assert result.unavailable_reason == "insufficient_completed_bars:9<15"


class TestVolumePace:
    def test_ratio_against_20_session_full_day_median(self):
        bars = _flat_bars(25, volume=1_000.0)
        median, reason = compute_prior_full_day_median_volume(bars)
        assert median == 1_000.0
        assert reason is None
        pace = compute_volume_pace(2_500.0, median)
        assert pace.ratio == 2.5
        assert pace.basis == VOLUME_PACE_BASIS
        assert pace.prior_median_volume == 1_000.0
        assert pace.unavailable_reason is None

    def test_median_uses_only_the_latest_20_sessions(self):
        bars = _flat_bars(30, volume=999_999.0)[:10] + _flat_bars(20, volume=2_000.0)
        median, reason = compute_prior_full_day_median_volume(bars)
        assert median == 2_000.0
        assert reason is None

    def test_short_history_fails_closed(self):
        median, reason = compute_prior_full_day_median_volume(_flat_bars(12))
        assert median is None
        assert reason == "insufficient_volume_history:12<20"
        pace = compute_volume_pace(2_500.0, median, median_unavailable_reason=reason)
        assert pace.ratio is None
        assert pace.unavailable_reason == reason

    def test_missing_session_volume_is_explicit(self):
        pace = compute_volume_pace(None, 1_000.0)
        assert pace.ratio is None
        assert pace.prior_median_volume == 1_000.0
        assert pace.unavailable_reason == "missing_session_volume"


class TestMarketSessionState:
    @pytest.mark.parametrize(
        ("hour", "minute", "expected"),
        [
            (3, 59, "closed"),
            (4, 0, "premarket"),
            (9, 29, "premarket"),
            (9, 30, "regular"),
            (15, 59, "regular"),
            (16, 0, "afterhours"),
            (19, 59, "afterhours"),
            (20, 0, "closed"),
            (23, 30, "closed"),
        ],
    )
    def test_weekday_clock_boundaries(self, hour, minute, expected):
        # 2026-07-28 is a Tuesday.
        now = datetime(2026, 7, 28, hour, minute, tzinfo=_NEW_YORK)
        assert market_session_state(now) == expected

    def test_weekend_is_closed_even_at_market_hours(self):
        saturday = datetime(2026, 7, 25, 10, 30, tzinfo=_NEW_YORK)
        assert market_session_state(saturday) == "closed"

    def test_utc_input_is_converted_to_new_york(self):
        # 14:00 UTC on a Tuesday in July = 10:00 ET (EDT) → regular.
        now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
        assert market_session_state(now) == "regular"

    def test_naive_datetime_is_rejected(self):
        with pytest.raises(ValueError):
            market_session_state(datetime(2026, 7, 28, 10, 0))

    def test_basis_label_is_clock_only(self):
        assert SESSION_STATE_BASIS == "america_new_york_clock_v1"
