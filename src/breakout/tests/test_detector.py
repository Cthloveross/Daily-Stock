# -*- coding: utf-8 -*-
"""Breakout detector tests using injected bar fixtures."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.breakout.detector import Bar, BreakoutDetector


def _mk_bars(closes, vol=1000.0, start=datetime(2026, 4, 17, 9, 30)):
    return [
        Bar(
            t=start + timedelta(minutes=2 * i),
            o=c,
            h=c,
            l=c,
            c=c,
            v=vol,
        )
        for i, c in enumerate(closes)
    ]


class TestScan:
    def test_up_breakout(self):
        bars = _mk_bars([100, 100.5, 100.2, 100.8, 100.6, 100.9, 101.1, 100.7, 100.4] + [100.3] * 10 + [105.0])
        det = BreakoutDetector()
        sig = det.scan("NVDA", bars=bars)
        assert sig is not None
        assert sig.direction == "up"
        assert sig.reason == "range_high"
        assert sig.breakout_price == 105.0
        assert sig.reference_high == 101.1

    def test_down_breakout(self):
        bars = _mk_bars([100, 99.9, 99.5, 99.8] * 5 + [95.0])
        det = BreakoutDetector()
        sig = det.scan("NVDA", bars=bars)
        assert sig is not None
        assert sig.direction == "down"

    def test_no_breakout_in_range(self):
        bars = _mk_bars([100 + 0.1 * (i % 5) for i in range(30)])
        det = BreakoutDetector()
        sig = det.scan("NVDA", bars=bars)
        assert sig is None

    def test_insufficient_history(self):
        bars = _mk_bars([100, 101, 102])
        det = BreakoutDetector()
        assert det.scan("NVDA", bars=bars) is None


class TestPrevDayLevels:
    def test_breaks_prev_day_high(self):
        bars = _mk_bars([100] * 9 + [105.0])
        det = BreakoutDetector()
        sig = det.scan_with_prev_day_levels("NVDA", prev_day_high=104.0, prev_day_low=99.0, bars=bars)
        assert sig is not None
        assert sig.reason == "prev_day_high"
        assert sig.reference_high == 104.0

    def test_respects_range(self):
        bars = _mk_bars([100] * 10)
        det = BreakoutDetector()
        sig = det.scan_with_prev_day_levels("NVDA", prev_day_high=104.0, prev_day_low=99.0, bars=bars)
        assert sig is None
