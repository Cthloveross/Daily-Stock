# -*- coding: utf-8 -*-
"""Retest tracker: classify a breakout as fake / real after a cooldown window.

Retest rules (heuristic, Phase 0):
    - Real breakout: price did NOT close back inside the pre-breakout range
      during the window AND at least one close > breakout price.
    - Fake breakout: any close back inside the range invalidates.
    - Retest success: after a breakout, price pulled back near the level then
      resumed in the breakout direction (continuation).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from src.breakout.detector import Bar

__all__ = ["RetestOutcome", "track_retest"]


@dataclass(frozen=True)
class RetestOutcome:
    is_fake_breakout: bool
    retest_observed: bool
    continuation_after_retest: bool
    window_minutes: int
    max_close_after: float
    min_close_after: float
    bars_observed: int


def track_retest(
    breakout_price: float,
    direction: str,
    breakout_time: datetime,
    subsequent_bars: Sequence[Bar],
    window_minutes: int = 30,
    retest_tolerance_pct: float = 0.2,
) -> RetestOutcome:
    """Observe bars within ``window_minutes`` after the breakout.

    Returns a :class:`RetestOutcome`. All inputs are plain values so this
    function is purely CPU-bound and unit-testable.
    """
    cutoff = breakout_time + timedelta(minutes=window_minutes)
    window = [b for b in subsequent_bars if breakout_time <= b.t <= cutoff]
    if not window:
        return RetestOutcome(
            is_fake_breakout=False,
            retest_observed=False,
            continuation_after_retest=False,
            window_minutes=window_minutes,
            max_close_after=0.0,
            min_close_after=0.0,
            bars_observed=0,
        )

    closes = [b.c for b in window]
    max_c = max(closes)
    min_c = min(closes)
    tol = breakout_price * retest_tolerance_pct / 100.0

    retest_observed = False
    continuation = False
    is_fake = False

    if direction == "up":
        # Any close back <= breakout_price * (1 - tol) within window => fake
        for b in window:
            if b.c < breakout_price - tol:
                is_fake = True
                break
        # Retest = at least one close near (within tolerance) after initial breakout,
        # followed by a bar closing above breakout level.
        for i, b in enumerate(window):
            if abs(b.c - breakout_price) <= tol:
                retest_observed = True
                if any(x.c > breakout_price + tol for x in window[i + 1 :]):
                    continuation = True
                break
    elif direction == "down":
        for b in window:
            if b.c > breakout_price + tol:
                is_fake = True
                break
        for i, b in enumerate(window):
            if abs(b.c - breakout_price) <= tol:
                retest_observed = True
                if any(x.c < breakout_price - tol for x in window[i + 1 :]):
                    continuation = True
                break
    else:
        is_fake = False

    return RetestOutcome(
        is_fake_breakout=is_fake,
        retest_observed=retest_observed,
        continuation_after_retest=continuation,
        window_minutes=window_minutes,
        max_close_after=max_c,
        min_close_after=min_c,
        bars_observed=len(window),
    )
