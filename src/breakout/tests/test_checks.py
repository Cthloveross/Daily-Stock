# -*- coding: utf-8 -*-
"""Unit tests for the individual Q-checks."""
from __future__ import annotations

from src.breakout.rs_check import check_rs_vs_spy
from src.breakout.timeframe_check import check_timeframe_alignment
from src.breakout.volume_check import check_volume_confirmation


class TestVolumeCheck:
    def test_above_threshold(self):
        r = check_volume_confirmation(150, 100, required_multiple=1.2)
        assert r.passed is True
        assert r.multiple == 1.5

    def test_below_threshold(self):
        r = check_volume_confirmation(100, 100, required_multiple=1.2)
        assert r.passed is False

    def test_zero_reference(self):
        r = check_volume_confirmation(100, 0)
        assert r.passed is False
        assert r.multiple == 0.0


class TestTimeframeAlignment:
    def test_all_aligned_up(self):
        prices = {"2Min": 101, "5Min": 101, "15Min": 101, "1Day": 101}
        mas = {"2Min": 100, "5Min": 100, "15Min": 100, "1Day": 100}
        r = check_timeframe_alignment("up", prices, mas, require_aligned=3)
        assert r.passed is True
        assert r.aligned_count == 4

    def test_two_of_four_fails_default(self):
        prices = {"2Min": 101, "5Min": 99, "15Min": 101, "1Day": 99}
        mas = {"2Min": 100, "5Min": 100, "15Min": 100, "1Day": 100}
        r = check_timeframe_alignment("up", prices, mas, require_aligned=3)
        assert r.passed is False
        assert r.aligned_count == 2

    def test_missing_tf_counts_as_fail(self):
        prices = {"2Min": 101}  # only one tf
        mas = {"2Min": 100}
        r = check_timeframe_alignment(
            "up", prices, mas, timeframes=["2Min", "5Min", "15Min"], require_aligned=2
        )
        assert r.passed is False
        assert r.aligned_count == 1

    def test_direction_down(self):
        prices = {"2Min": 99, "5Min": 99, "15Min": 99}
        mas = {"2Min": 100, "5Min": 100, "15Min": 100}
        r = check_timeframe_alignment("down", prices, mas, require_aligned=3)
        assert r.passed is True


class TestRSCheck:
    def test_up_pass(self):
        r = check_rs_vs_spy("up", 1.0, 0.2, rs_threshold=0.3)
        assert r.passed is True
        assert r.rs_value == 0.8

    def test_up_fail(self):
        r = check_rs_vs_spy("up", 0.3, 0.2, rs_threshold=0.3)
        assert r.passed is False

    def test_down_pass(self):
        r = check_rs_vs_spy("down", -1.0, -0.3, rs_threshold=0.3)
        # rs = -1 - (-0.3) = -0.7, need <= -0.3 -> pass
        assert r.passed is True

    def test_bad_direction(self):
        r = check_rs_vs_spy("sideways", 0.5, 0.1)
        assert r.passed is False
