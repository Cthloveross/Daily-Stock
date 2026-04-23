# -*- coding: utf-8 -*-
"""End-to-end Q1-Q5 filter tests."""
from __future__ import annotations

from datetime import datetime

from src.breakout.detector import BreakoutSignal
from src.breakout.filter import filter_breakout


def _signal(direction="up", ref_vol=1000, cur_vol=2000, reason="range_high"):
    return BreakoutSignal(
        symbol="NVDA",
        timeframe="2Min",
        direction=direction,
        detected_at=datetime(2026, 4, 17, 10, 0),
        breakout_price=101.0,
        reference_high=100.0,
        reference_low=95.0,
        reference_volume=ref_vol,
        current_volume=cur_vol,
        reason=reason,
    )


class TestQ1Regime:
    def test_regime_too_low_blocks(self):
        res = filter_breakout(_signal(), regime_score=30, regime_min=55)
        assert res.passed is False
        assert res.rejected_at == "Q1"

    def test_regime_missing_blocks(self):
        res = filter_breakout(_signal(), regime_score=None)
        assert res.passed is False
        assert res.rejected_at == "Q1"


class TestQ3Volume:
    def test_low_volume_blocks(self):
        res = filter_breakout(_signal(cur_vol=500, ref_vol=1000), regime_score=70)
        assert res.passed is False
        assert res.rejected_at == "Q3"


class TestQ4Timeframe:
    def test_misaligned_blocks(self):
        res = filter_breakout(
            _signal(),
            regime_score=70,
            tf_price={"2Min": 101, "5Min": 99, "15Min": 98, "1Day": 97},
            tf_ma={"2Min": 100, "5Min": 100, "15Min": 100, "1Day": 100},
            tf_require_aligned=3,
        )
        assert res.passed is False
        assert res.rejected_at == "Q4"

    def test_aligned_pass(self):
        res = filter_breakout(
            _signal(),
            regime_score=70,
            tf_price={"2Min": 101, "5Min": 101, "15Min": 101, "1Day": 101},
            tf_ma={"2Min": 100, "5Min": 100, "15Min": 100, "1Day": 100},
            tf_require_aligned=3,
            symbol_return_pct=1.0,
            spy_return_pct=0.2,
        )
        assert res.passed is True


class TestQ5RS:
    def test_weak_rs_blocks(self):
        res = filter_breakout(
            _signal(),
            regime_score=70,
            symbol_return_pct=0.1,
            spy_return_pct=0.5,
        )
        assert res.passed is False
        assert res.rejected_at == "Q5"


class TestFullPass:
    def test_all_gates_pass(self):
        res = filter_breakout(
            _signal(),
            regime_score=70,
            symbol_return_pct=1.2,
            spy_return_pct=0.1,
        )
        assert res.passed is True
        assert res.rejected_at is None
        assert res.q3_volume.passed
        assert res.q5_rs.passed
