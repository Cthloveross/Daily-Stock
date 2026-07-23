# -*- coding: utf-8 -*-
"""Six pure scorer functions producing the Regime Score components.

Each scorer receives a dict with the raw market data it needs and returns an
``int``. Ranges are fixed so the sum lives in a known band:

    score_market_direction      [0,   30]
    score_volatility            [-15, 20]
    score_macro_penalty         [-50, 0]
    score_sector_rotation       [-5,  15]
    score_prev_day_structure    [-2,  13]
    score_premarket_activity    [0,   20]
    -----------------------------------
    Sum total                   [-72, 98]  (classifier clamps below)

References: New-docs/06_REGIME_CLASSIFIER.md §3-4.
"""
from __future__ import annotations

import math
from typing import Iterable

__all__ = [
    "score_market_direction",
    "score_volatility",
    "score_macro_penalty",
    "score_sector_rotation",
    "score_prev_day_structure",
    "score_premarket_activity",
]


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _has_finite_numbers(payload: dict, keys: Iterable[str]) -> bool:
    """Return ``True`` only when every required numeric input is observed."""
    if str(payload.get("_status") or "").lower() in {"degraded", "unavailable"}:
        return False
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(float(value)):
            return False
    return True


def score_market_direction(spy: dict) -> int:
    """Trend strength from SPY close-over-MA20 and recent momentum.

    Expects keys: ``close``, ``ma20``, ``ma50``, ``pct_change_5d``.
    """
    if not _has_finite_numbers(spy, ("close", "ma20", "ma50", "pct_change_5d")):
        return 0
    close = float(spy["close"])
    ma20 = float(spy["ma20"])
    ma50 = float(spy["ma50"])
    pct_5d = float(spy["pct_change_5d"])

    score = 0
    if ma20 and close:
        if close > ma20:
            score += 10
        if ma20 > ma50:
            score += 10
    # Momentum: -5% to +5% maps linearly to -10..+10, clamped.
    momentum = int(round(pct_5d * 2))
    score += _clamp(momentum, -10, 10)
    return _clamp(score, 0, 30)


def score_volatility(vix: dict) -> int:
    """VIX regime: low vol = tradeable, crisis vol = pass.

    Keys: ``level`` (current VIX close), ``pct_change_5d``.
    Buckets tuned to Phase 0 heuristic; sub-regions:
        VIX < 15      -> +20  (calm)
        15 <= VIX < 20-> +10
        20 <= VIX < 25-> 0
        25 <= VIX < 30-> -10
        VIX >= 30     -> -15
    Additional penalty when vol spiked > 25% in 5d.
    """
    if not _has_finite_numbers(vix, ("level", "pct_change_5d")):
        return 0
    level = float(vix["level"])
    pct_5d = float(vix["pct_change_5d"])

    if level <= 0:
        base = 0
    elif level < 15:
        base = 20
    elif level < 20:
        base = 10
    elif level < 25:
        base = 0
    elif level < 30:
        base = -10
    else:
        base = -15
    if pct_5d > 25:
        base -= 5
    return _clamp(base, -15, 20)


def score_macro_penalty(events: dict) -> int:
    """Penalty on big-event days: FOMC / CPI / NFP / earnings for watchlist heavies.

    Keys:
        fomc_today (bool)
        cpi_today (bool)
        nfp_today (bool)
        earnings_count_watchlist (int)
        tariff_headline_today (bool)
    """
    if not events or str(events.get("_status") or "").lower() in {
        "degraded",
        "unavailable",
    }:
        return 0
    penalty = 0
    if events.get("fomc_today"):
        penalty -= 30
    if events.get("cpi_today"):
        penalty -= 20
    if events.get("nfp_today"):
        penalty -= 15
    earnings_n = int(events.get("earnings_count_watchlist") or 0)
    if earnings_n >= 3:
        penalty -= 15
    elif earnings_n >= 1:
        penalty -= 5
    if events.get("tariff_headline_today"):
        penalty -= 10
    return _clamp(penalty, -50, 0)


def score_sector_rotation(sectors: dict) -> int:
    """Breadth: how many of the 11 S&P sectors are above their 20-day MA.

    Keys: ``sectors_above_ma20`` (int 0-11), ``defensive_leaders`` (bool).
    """
    if not _has_finite_numbers(sectors, ("sectors_above_ma20",)):
        return 0
    n_above = int(sectors["sectors_above_ma20"])
    defensive = bool(sectors.get("defensive_leaders"))

    # 0..11 -> -5..+15
    scaled = int(round((n_above / 11.0) * 20 - 5))
    score = _clamp(scaled, -5, 15)
    if defensive and score > 0:
        score -= 3  # defensive leadership dilutes risk-on quality
    return _clamp(score, -5, 15)


def score_prev_day_structure(prev_day: dict) -> int:
    """Prior-day structure: was yesterday's close near highs?

    Keys: ``close_vs_high_pct`` (how close to day's high, 0..1),
          ``prev_day_range_pct`` (day range / prior close).
    """
    if not _has_finite_numbers(
        prev_day, ("close_vs_high_pct", "prev_day_range_pct")
    ):
        return 0
    clp = float(prev_day["close_vs_high_pct"])
    range_pct = float(prev_day["prev_day_range_pct"])

    score = 0
    if clp >= 0.9:
        score += 10
    elif clp >= 0.7:
        score += 5
    elif clp <= 0.3:
        score -= 2
    if range_pct > 2.0:
        score += 3
    return _clamp(score, -2, 13)


def score_premarket_activity(premarket: dict) -> int:
    """Premarket signals: SPY premarket return + watchlist leader count.

    Keys: ``spy_pre_pct`` (float), ``watchlist_up_5pct`` (int),
          ``watchlist_down_5pct`` (int).
    """
    if not _has_finite_numbers(
        premarket,
        ("spy_pre_pct", "watchlist_up_5pct", "watchlist_down_5pct"),
    ):
        return 0
    spy_pre = float(premarket["spy_pre_pct"])
    up = int(premarket["watchlist_up_5pct"])
    down = int(premarket["watchlist_down_5pct"])

    score = 0
    if spy_pre >= 0.3:
        score += 8
    elif spy_pre >= 0:
        score += 3
    elif spy_pre <= -0.5:
        score -= 5
    score += min(up, 5) * 2  # each premarket mover +2, cap 10
    score -= min(down, 5)
    return _clamp(score, 0, 20)
