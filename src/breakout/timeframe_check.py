# -*- coding: utf-8 -*-
"""Q4: Multi-timeframe alignment check.

For a long breakout, we want price above its SMA / EMA on *multiple* higher
timeframes (not just the trigger one). Passing 3/4 or 4/4 of the configured
frames counts as aligned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

__all__ = ["TimeframeCheckResult", "check_timeframe_alignment"]


@dataclass(frozen=True)
class TimeframeCheckResult:
    passed: bool
    aligned_count: int
    total_count: int
    per_timeframe: dict[str, bool]


def check_timeframe_alignment(
    direction: str,
    per_timeframe_price: dict[str, float],
    per_timeframe_ma: dict[str, float],
    timeframes: Iterable[str] | None = None,
    require_aligned: int = 3,
) -> TimeframeCheckResult:
    """Check if price is on the right side of MA across enough timeframes.

    Inputs are dicts keyed by timeframe string (``'2Min'``, ``'5Min'``,
    ``'15Min'``, ``'1Day'`` etc). ``direction`` is ``'up'`` (price should be
    >= MA) or ``'down'`` (price <= MA).
    """
    frames = list(timeframes) if timeframes is not None else list(per_timeframe_price.keys())
    per: dict[str, bool] = {}
    aligned = 0
    for tf in frames:
        px = per_timeframe_price.get(tf)
        ma = per_timeframe_ma.get(tf)
        if px is None or ma is None:
            per[tf] = False
            continue
        ok = (px >= ma) if direction == "up" else (px <= ma)
        per[tf] = bool(ok)
        if ok:
            aligned += 1
    return TimeframeCheckResult(
        passed=aligned >= require_aligned,
        aligned_count=aligned,
        total_count=len(frames),
        per_timeframe=per,
    )
