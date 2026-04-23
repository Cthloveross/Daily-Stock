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


def score_market_direction(spy: dict) -> int:
    """Trend strength from SPY close-over-MA20 and recent momentum.

    Expects keys: ``close``, ``ma20``, ``ma50``, ``pct_change_5d``.
    """
    close = float(spy.get("close") or 0)
    ma20 = float(spy.get("ma20") or 0)
    ma50 = float(spy.get("ma50") or 0)
    pct_5d = float(spy.get("pct_change_5d") or 0)

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
    level = float(vix.get("level") or 0)
    pct_5d = float(vix.get("pct_change_5d") or 0)

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
    n_above = int(sectors.get("sectors_above_ma20") or 0)
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
    clp = float(prev_day.get("close_vs_high_pct") or 0)
    range_pct = float(prev_day.get("prev_day_range_pct") or 0)

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
    spy_pre = float(premarket.get("spy_pre_pct") or 0)
    up = int(premarket.get("watchlist_up_5pct") or 0)
    down = int(premarket.get("watchlist_down_5pct") or 0)

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
