# -*- coding: utf-8 -*-
"""Pure intraday tracking indicator math (G-2 / G-4 first slice).

This module never fetches data.  Callers inject a live session snapshot
(cumulative turnover / volume) and the same completed daily bars the daily
opportunity board uses (:func:`src.opportunities.engine.completed_daily_bars`
output).  Every indicator either returns an observed/derived value or an
explicit ``unavailable_reason`` — nothing is zero-filled or estimated.

The intraday panel these values feed only *tracks* the frozen premarket plan.
It never re-ranks candidates intraday and never emits a buy/sell signal.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

INTRADAY_TRACKING_VERSION = "intraday_tracking_v1"

# VWAP here is an approximation: cumulative session turnover ÷ cumulative
# session volume.  It is not an official exchange tick-weighted VWAP.
VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME = "session_turnover_over_volume"

# Honest v1: today's cumulative volume is compared against the median of the
# prior 20 *full-day* volumes — deliberately not time-of-day adjusted.  A 2.0×
# reading at 10:00 ET therefore means "already twice a typical full day",
# while 0.4× mid-morning can still be a normal pace.
VOLUME_PACE_BASIS = "session_cumulative_vs_prior_20_session_full_day_median"
VOLUME_PACE_PRIOR_SESSIONS = 20

ATR14_METHOD = "wilder_smoothing_14_daily_completed_bars"
ATR14_PERIOD = 14

# Session state is derived from the America/New_York wall clock only; it does
# not consult an exchange holiday calendar (an NYSE holiday weekday will be
# labelled by clock, not "closed").
SESSION_STATE_BASIS = "america_new_york_clock_v1"
_NEW_YORK = ZoneInfo("America/New_York")
_PREMARKET_START = time(4, 0)
_REGULAR_START = time(9, 30)
_REGULAR_END = time(16, 0)
_AFTERHOURS_END = time(20, 0)


def _finite_positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class SessionVwapResult:
    """Approximate session VWAP with an explicit basis and failure reason."""

    value: Optional[float]
    basis: str = VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME
    unavailable_reason: Optional[str] = None


def compute_session_vwap(
    turnover: Optional[float],
    volume: Optional[float],
) -> SessionVwapResult:
    """Return ``turnover / volume`` only when both are present and positive.

    Formula: ``vwap ≈ cumulative_session_turnover / cumulative_session_volume``
    (basis label ``session_turnover_over_volume``).  Anything else — missing
    field, zero or negative input — fails closed with an explicit reason
    instead of fabricating a price.
    """

    turnover_value = _finite_positive(turnover)
    volume_value = _finite_positive(volume)
    if turnover_value is None and volume_value is None:
        return SessionVwapResult(None, unavailable_reason="missing_turnover_and_volume")
    if turnover_value is None:
        return SessionVwapResult(None, unavailable_reason="missing_or_nonpositive_turnover")
    if volume_value is None:
        return SessionVwapResult(None, unavailable_reason="missing_or_nonpositive_volume")
    return SessionVwapResult(round(turnover_value / volume_value, 6))


@dataclass(frozen=True)
class Atr14Result:
    """Wilder ATR(14) over completed daily bars, or an explicit absence."""

    value: Optional[float]
    method: str = ATR14_METHOD
    bar_count: int = 0
    last_bar_date: Optional[str] = None
    unavailable_reason: Optional[str] = None


def compute_atr14(bars: Sequence[Mapping[str, Any]]) -> Atr14Result:
    """Compute ATR(14) with Wilder smoothing from completed daily bars.

    Formula (classic Wilder, period N = 14):

    - ``TR_t = max(high_t − low_t, |high_t − close_{t−1}|, |low_t − close_{t−1}|)``
    - seed: ``ATR_N = mean(TR_1 … TR_N)`` (simple average of the first N TRs)
    - then: ``ATR_t = (ATR_{t−1} × (N − 1) + TR_t) / N``

    Inputs are chronologically ordered completed daily bars (the same
    ``completed_daily_bars`` rows the daily board consumes); the current,
    still-forming session is deliberately excluded.  Requires at least
    ``N + 1 = 15`` consecutive bars each carrying finite ``high``/``low``/
    ``close`` with ``high ≥ low``; otherwise fails closed with a reason.
    All available completed bars are used, so the smoothing depth equals the
    injected history window (~80 calendar days from the shared loader).
    """

    usable: list[tuple[float, float, float, str]] = []
    for bar in bars:
        high = _finite_number(bar.get("high"))
        low = _finite_number(bar.get("low"))
        close = _finite_positive(bar.get("close"))
        bar_date = str(bar.get("date") or "")
        if high is None or low is None or close is None or high < low:
            # A gap inside the series would silently distort TR chaining;
            # restart the consecutive window instead of bridging the hole.
            usable = []
            continue
        usable.append((high, low, close, bar_date))

    if len(usable) < ATR14_PERIOD + 1:
        return Atr14Result(
            None,
            bar_count=len(usable),
            unavailable_reason=(
                f"insufficient_completed_bars:{len(usable)}<{ATR14_PERIOD + 1}"
            ),
        )

    true_ranges: list[float] = []
    for index in range(1, len(usable)):
        high, low, _, _ = usable[index]
        prev_close = usable[index - 1][2]
        true_ranges.append(
            max(high - low, abs(high - prev_close), abs(low - prev_close))
        )

    atr = statistics.fmean(true_ranges[:ATR14_PERIOD])
    for tr in true_ranges[ATR14_PERIOD:]:
        atr = (atr * (ATR14_PERIOD - 1) + tr) / ATR14_PERIOD

    if not math.isfinite(atr) or atr <= 0:
        return Atr14Result(
            None,
            bar_count=len(usable),
            last_bar_date=usable[-1][3] or None,
            unavailable_reason="degenerate_true_range_series",
        )
    return Atr14Result(
        round(atr, 6),
        bar_count=len(usable),
        last_bar_date=usable[-1][3] or None,
    )


@dataclass(frozen=True)
class VolumePaceResult:
    """Session volume vs prior 20-session full-day median, or explicit absence."""

    ratio: Optional[float]
    basis: str = VOLUME_PACE_BASIS
    prior_median_volume: Optional[float] = None
    unavailable_reason: Optional[str] = None


def compute_prior_full_day_median_volume(
    bars: Sequence[Mapping[str, Any]],
    *,
    sessions: int = VOLUME_PACE_PRIOR_SESSIONS,
) -> tuple[Optional[float], Optional[str]]:
    """Median full-day volume over the most recent ``sessions`` completed bars."""

    volumes = [
        volume
        for bar in bars[-sessions:]
        if (volume := _finite_number(bar.get("volume"))) is not None and volume >= 0
    ]
    if len(volumes) < sessions:
        return None, f"insufficient_volume_history:{len(volumes)}<{sessions}"
    median = statistics.median(volumes)
    if median <= 0:
        return None, "zero_prior_median_volume"
    return median, None


def compute_volume_pace(
    session_volume: Optional[float],
    prior_median_volume: Optional[float],
    *,
    median_unavailable_reason: Optional[str] = None,
) -> VolumePaceResult:
    """今日累计成交量 ÷ 前 20 个完整交易日全日成交量中位数.

    The label ``session_cumulative_vs_prior_20_session_full_day_median`` is
    deliberate: this is NOT a time-of-day adjusted pace.  Early in the session
    a small ratio is normal; the honest v1 keeps the raw full-day comparison.
    """

    if prior_median_volume is None or prior_median_volume <= 0:
        return VolumePaceResult(
            None,
            unavailable_reason=median_unavailable_reason or "missing_prior_median_volume",
        )
    volume_value = _finite_number(session_volume)
    if volume_value is None or volume_value < 0:
        return VolumePaceResult(
            None,
            prior_median_volume=round(prior_median_volume, 6),
            unavailable_reason="missing_session_volume",
        )
    return VolumePaceResult(
        round(volume_value / prior_median_volume, 6),
        prior_median_volume=round(prior_median_volume, 6),
    )


def market_session_state(now: datetime) -> str:
    """Classify the US equity session via the America/New_York clock.

    - ``premarket``: Mon–Fri 04:00 ≤ t < 09:30 ET
    - ``regular``:   Mon–Fri 09:30 ≤ t < 16:00 ET
    - ``afterhours``: Mon–Fri 16:00 ≤ t < 20:00 ET
    - ``closed``: everything else (nights and weekends)

    ``now`` must be timezone-aware.  Basis ``america_new_york_clock_v1``:
    a pure wall-clock rule with no exchange holiday calendar — a weekday
    NYSE holiday is still labelled by clock, which the caller must surface
    as a documented limitation rather than hide.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("market_session_state requires a timezone-aware datetime")
    local = now.astimezone(_NEW_YORK)
    if local.weekday() >= 5:
        return "closed"
    clock = local.time()
    if _PREMARKET_START <= clock < _REGULAR_START:
        return "premarket"
    if _REGULAR_START <= clock < _REGULAR_END:
        return "regular"
    if _REGULAR_END <= clock < _AFTERHOURS_END:
        return "afterhours"
    return "closed"
