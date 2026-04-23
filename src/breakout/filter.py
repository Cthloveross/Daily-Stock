# -*- coding: utf-8 -*-
"""Q1-Q5 decision tree.

Composes Regime gate + Pattern detection + Volume + Timeframe + RS. Short-
circuits on the first failed gate so callers see *why* a breakout was rejected.

Canonical input is a :class:`BreakoutSignal` + the dependent probe inputs.
Returns a :class:`BreakoutFilterResult` carrying the decision and every
intermediate check output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from src.breakout.detector import BreakoutSignal
from src.breakout.rs_check import RSCheckResult, check_rs_vs_spy
from src.breakout.timeframe_check import TimeframeCheckResult, check_timeframe_alignment
from src.breakout.volume_check import VolumeCheckResult, check_volume_confirmation

__all__ = ["BreakoutFilterResult", "filter_breakout"]


@dataclass
class BreakoutFilterResult:
    """Outcome of the Q1-Q5 decision tree.

    ``passed`` is the overall verdict; the per-Q fields are populated up to
    (and including) the Q that decided the result, later Q's are None.
    """

    passed: bool
    reason: str  # short reason tag for UI/log/journal
    signal: BreakoutSignal
    q1_regime_score: Optional[int] = None
    q1_regime_min: Optional[int] = None
    q1_passed: Optional[bool] = None
    q2_pattern_ok: Optional[bool] = None
    q3_volume: Optional[VolumeCheckResult] = None
    q4_timeframe: Optional[TimeframeCheckResult] = None
    q5_rs: Optional[RSCheckResult] = None
    rejected_at: Optional[str] = None  # 'Q1' / 'Q2' / 'Q3' / 'Q4' / 'Q5'


def filter_breakout(
    signal: BreakoutSignal,
    regime_score: Optional[int],
    *,
    regime_min: int = 55,
    volume_multiple: float = 1.2,
    tf_price: Optional[dict] = None,
    tf_ma: Optional[dict] = None,
    tf_timeframes: Optional[list[str]] = None,
    tf_require_aligned: int = 3,
    symbol_return_pct: Optional[float] = None,
    spy_return_pct: Optional[float] = None,
    rs_threshold: float = 0.3,
) -> BreakoutFilterResult:
    """Run Q1-Q5 on ``signal``. Short-circuits on first failure.

    Parameters whose values we can't resolve (e.g. Alpaca not configured) can
    be passed as ``None`` — the check is treated as unresolvable and the gate
    is considered *not passed* to remain conservative.
    """
    # Q1: Regime must be at or above threshold.
    q1_passed = regime_score is not None and regime_score >= regime_min
    if not q1_passed:
        return BreakoutFilterResult(
            passed=False,
            reason=f"Regime {regime_score} < {regime_min}",
            signal=signal,
            q1_regime_score=regime_score,
            q1_regime_min=regime_min,
            q1_passed=False,
            rejected_at="Q1",
        )

    # Q2: Pattern (signal itself must exist with a known reason).
    q2_ok = bool(signal.reason)
    if not q2_ok:
        return BreakoutFilterResult(
            passed=False,
            reason="Pattern missing",
            signal=signal,
            q1_regime_score=regime_score,
            q1_regime_min=regime_min,
            q1_passed=True,
            q2_pattern_ok=False,
            rejected_at="Q2",
        )

    # Q3: Volume.
    vol = check_volume_confirmation(
        current_volume=signal.current_volume,
        reference_volume=signal.reference_volume,
        required_multiple=volume_multiple,
    )
    if not vol.passed:
        return BreakoutFilterResult(
            passed=False,
            reason=f"Volume {vol.multiple:.2f}x < {vol.required_multiple:.2f}x",
            signal=signal,
            q1_regime_score=regime_score,
            q1_regime_min=regime_min,
            q1_passed=True,
            q2_pattern_ok=True,
            q3_volume=vol,
            rejected_at="Q3",
        )

    # Q4: Multi-timeframe alignment.
    if tf_price is not None and tf_ma is not None:
        tf = check_timeframe_alignment(
            direction=signal.direction,
            per_timeframe_price=tf_price,
            per_timeframe_ma=tf_ma,
            timeframes=tf_timeframes,
            require_aligned=tf_require_aligned,
        )
        if not tf.passed:
            return BreakoutFilterResult(
                passed=False,
                reason=f"Timeframe aligned {tf.aligned_count}/{tf.total_count}",
                signal=signal,
                q1_regime_score=regime_score,
                q1_regime_min=regime_min,
                q1_passed=True,
                q2_pattern_ok=True,
                q3_volume=vol,
                q4_timeframe=tf,
                rejected_at="Q4",
            )
    else:
        tf = None  # not evaluated; continue to Q5

    # Q5: Relative strength vs SPY.
    if symbol_return_pct is not None and spy_return_pct is not None:
        rs = check_rs_vs_spy(
            direction=signal.direction,
            symbol_return_pct=symbol_return_pct,
            spy_return_pct=spy_return_pct,
            rs_threshold=rs_threshold,
        )
        if not rs.passed:
            return BreakoutFilterResult(
                passed=False,
                reason=f"RS {rs.rs_value:+.2f}%",
                signal=signal,
                q1_regime_score=regime_score,
                q1_regime_min=regime_min,
                q1_passed=True,
                q2_pattern_ok=True,
                q3_volume=vol,
                q4_timeframe=tf,
                q5_rs=rs,
                rejected_at="Q5",
            )
    else:
        rs = None

    return BreakoutFilterResult(
        passed=True,
        reason="all_gates_passed",
        signal=signal,
        q1_regime_score=regime_score,
        q1_regime_min=regime_min,
        q1_passed=True,
        q2_pattern_ok=True,
        q3_volume=vol,
        q4_timeframe=tf,
        q5_rs=rs,
        rejected_at=None,
    )
