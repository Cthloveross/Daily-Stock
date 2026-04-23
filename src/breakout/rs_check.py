# -*- coding: utf-8 -*-
"""Q5: Relative-strength check vs SPY (benchmark).

Compare symbol return vs SPY return over an intraday window. Long breakouts
should show *positive* RS (symbol outperforming); short breakouts negative.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RSCheckResult", "check_rs_vs_spy"]


@dataclass(frozen=True)
class RSCheckResult:
    passed: bool
    direction: str
    symbol_return_pct: float
    spy_return_pct: float
    rs_value: float
    rs_threshold: float


def check_rs_vs_spy(
    direction: str,
    symbol_return_pct: float,
    spy_return_pct: float,
    rs_threshold: float = 0.3,
) -> RSCheckResult:
    """Compute RS = symbol_return - spy_return and compare to threshold.

    For an ``up`` breakout: RS must be >= ``rs_threshold``. For ``down``: RS
    must be <= -``rs_threshold``. Both inputs are in percent (e.g. ``0.8`` for
    +0.8%).
    """
    rs = symbol_return_pct - spy_return_pct
    if direction == "up":
        passed = rs >= rs_threshold
    elif direction == "down":
        passed = rs <= -rs_threshold
    else:
        passed = False
    return RSCheckResult(
        passed=passed,
        direction=direction,
        symbol_return_pct=symbol_return_pct,
        spy_return_pct=spy_return_pct,
        rs_value=rs,
        rs_threshold=rs_threshold,
    )
