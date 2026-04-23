# -*- coding: utf-8 -*-
"""Agent tool: wrap filter_breakout so skills can call it with minimal payload.

For Phase 0 we don't have a live BarsProvider, so callers pass the detected
signal fields explicitly. This keeps the tool deterministic and unit-testable.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from src.breakout.detector import BreakoutSignal
from src.breakout.filter import filter_breakout


def check_breakout_tool(
    symbol: str,
    direction: str,
    breakout_price: float,
    reference_high: float,
    reference_low: float,
    current_volume: float,
    reference_volume: float,
    regime_score: int | None,
    detected_at: str | None = None,
    reason: str = "range_high",
    tf_price: dict | None = None,
    tf_ma: dict | None = None,
    symbol_return_pct: float | None = None,
    spy_return_pct: float | None = None,
    regime_min: int = 55,
    volume_multiple: float = 1.2,
    rs_threshold: float = 0.3,
) -> dict:
    """Run the Q1-Q5 decision tree on an explicit signal spec.

    Returns the filter result (verdict + per-Q breakdown) as a plain dict.
    """
    when = datetime.fromisoformat(detected_at) if detected_at else datetime.utcnow()
    signal = BreakoutSignal(
        symbol=symbol.upper(),
        timeframe="2Min",
        direction=direction,
        detected_at=when,
        breakout_price=breakout_price,
        reference_high=reference_high,
        reference_low=reference_low,
        reference_volume=reference_volume,
        current_volume=current_volume,
        reason=reason,
    )
    result = filter_breakout(
        signal,
        regime_score=regime_score,
        regime_min=regime_min,
        volume_multiple=volume_multiple,
        tf_price=tf_price,
        tf_ma=tf_ma,
        symbol_return_pct=symbol_return_pct,
        spy_return_pct=spy_return_pct,
        rs_threshold=rs_threshold,
    )
    out = {
        "passed": result.passed,
        "reason": result.reason,
        "rejected_at": result.rejected_at,
        "q1_regime_score": result.q1_regime_score,
        "q1_passed": result.q1_passed,
        "q2_pattern_ok": result.q2_pattern_ok,
        "q3_volume": asdict(result.q3_volume) if result.q3_volume else None,
        "q4_timeframe": asdict(result.q4_timeframe) if result.q4_timeframe else None,
        "q5_rs": asdict(result.q5_rs) if result.q5_rs else None,
    }
    return out
