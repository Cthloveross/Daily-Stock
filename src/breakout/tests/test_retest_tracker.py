# -*- coding: utf-8 -*-
"""Retest tracker unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.breakout.detector import Bar
from src.breakout.retest_tracker import track_retest


def _bars(closes, start=datetime(2026, 4, 17, 10, 0), vol=1000.0):
    return [
        Bar(t=start + timedelta(minutes=2 * i), o=c, h=c, l=c, c=c, v=vol)
        for i, c in enumerate(closes)
    ]


def test_real_breakout_continues_up():
    # Breakout at 100 -> subsequent bars hold above
    out = track_retest(
        breakout_price=100.0,
        direction="up",
        breakout_time=datetime(2026, 4, 17, 10, 0),
        subsequent_bars=_bars([101, 102, 103, 104, 105]),
    )
    assert out.is_fake_breakout is False
    assert out.retest_observed is False


def test_fake_breakout_up():
    # Closed back below breakout level within window.
    out = track_retest(
        breakout_price=100.0,
        direction="up",
        breakout_time=datetime(2026, 4, 17, 10, 0),
        subsequent_bars=_bars([101, 99.5, 98, 97, 96]),
    )
    assert out.is_fake_breakout is True


def test_retest_continuation():
    # Price went up, came back to 100, then continued up.
    out = track_retest(
        breakout_price=100.0,
        direction="up",
        breakout_time=datetime(2026, 4, 17, 10, 0),
        subsequent_bars=_bars([102, 101, 100.1, 100.05, 101.5]),
        retest_tolerance_pct=0.5,
    )
    assert out.retest_observed is True
    assert out.continuation_after_retest is True


def test_empty_window():
    out = track_retest(
        breakout_price=100.0,
        direction="up",
        breakout_time=datetime(2026, 4, 17, 10, 0),
        subsequent_bars=[],
    )
    assert out.bars_observed == 0
