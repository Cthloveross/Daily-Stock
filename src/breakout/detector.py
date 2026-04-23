# -*- coding: utf-8 -*-
"""Breakout detector: identify price breaks of recent highs on intraday bars.

Produces :class:`BreakoutSignal` rows that :mod:`src.breakout.filter` then
feeds through the Q1-Q5 decision tree. Pure function of bar data; data source
(yfinance, Alpaca, fixture) is injected as a callable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = ["Bar", "BreakoutSignal", "BreakoutDetector"]


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar."""

    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class BreakoutSignal:
    """One detected breakout candidate (before Q1-Q5 filtering)."""

    symbol: str
    timeframe: str
    direction: str  # 'up' / 'down'
    detected_at: datetime
    breakout_price: float
    reference_high: float
    reference_low: float
    reference_volume: float
    current_volume: float
    reason: str  # 'range_high' / 'prev_day_high' / 'vwap_break' / 'round_number'
    meta: dict = field(default_factory=dict)


# Type alias: bars_provider(symbol, timeframe, lookback_bars, now) -> list[Bar]
BarsProvider = Callable[[str, str, int, Optional[datetime]], Sequence[Bar]]


class BreakoutDetector:
    """Detects breakouts of lookback range highs/lows.

    The detector is deliberately data-source agnostic: pass a callable that
    returns a sequence of :class:`Bar` instances. Production callers wire
    this to yfinance / Alpaca; tests pass fixture bar lists.
    """

    def __init__(self, bars_provider: Optional[BarsProvider] = None):
        self._bars_provider = bars_provider

    def scan(
        self,
        symbol: str,
        timeframe: str = "2Min",
        lookback_bars: int = 60,
        now: Optional[datetime] = None,
        bars: Optional[Sequence[Bar]] = None,
    ) -> Optional[BreakoutSignal]:
        """Scan the most recent ``lookback_bars`` for a fresh breakout.

        ``bars`` can be passed directly (testing). Otherwise the configured
        ``bars_provider`` is called. Returns ``None`` when no breakout is
        visible on the latest bar.
        """
        if bars is None:
            if self._bars_provider is None:
                return None
            bars = list(self._bars_provider(symbol, timeframe, lookback_bars, now))
        bars = list(bars)
        if len(bars) < 10:
            return None

        current = bars[-1]
        ref_window = bars[:-1]  # all bars before the current one
        ref_high = max(b.h for b in ref_window)
        ref_low = min(b.l for b in ref_window)
        avg_vol = sum(b.v for b in ref_window) / len(ref_window) if ref_window else 0.0

        if current.c > ref_high:
            return BreakoutSignal(
                symbol=symbol,
                timeframe=timeframe,
                direction="up",
                detected_at=current.t,
                breakout_price=current.c,
                reference_high=ref_high,
                reference_low=ref_low,
                reference_volume=avg_vol,
                current_volume=current.v,
                reason="range_high",
                meta={"lookback_bars": lookback_bars},
            )

        if current.c < ref_low:
            return BreakoutSignal(
                symbol=symbol,
                timeframe=timeframe,
                direction="down",
                detected_at=current.t,
                breakout_price=current.c,
                reference_high=ref_high,
                reference_low=ref_low,
                reference_volume=avg_vol,
                current_volume=current.v,
                reason="range_low",
                meta={"lookback_bars": lookback_bars},
            )

        return None

    def scan_with_prev_day_levels(
        self,
        symbol: str,
        prev_day_high: float,
        prev_day_low: float,
        now: Optional[datetime] = None,
        bars: Optional[Sequence[Bar]] = None,
    ) -> Optional[BreakoutSignal]:
        """Detect breakout of the prior day's high/low specifically."""
        if bars is None:
            if self._bars_provider is None:
                return None
            bars = list(self._bars_provider(symbol, "2Min", 60, now))
        bars = list(bars)
        if not bars:
            return None

        current = bars[-1]
        avg_vol = sum(b.v for b in bars[:-1]) / max(1, len(bars) - 1)

        if current.c > prev_day_high:
            return BreakoutSignal(
                symbol=symbol,
                timeframe="2Min",
                direction="up",
                detected_at=current.t,
                breakout_price=current.c,
                reference_high=prev_day_high,
                reference_low=prev_day_low,
                reference_volume=avg_vol,
                current_volume=current.v,
                reason="prev_day_high",
            )
        if current.c < prev_day_low:
            return BreakoutSignal(
                symbol=symbol,
                timeframe="2Min",
                direction="down",
                detected_at=current.t,
                breakout_price=current.c,
                reference_high=prev_day_high,
                reference_low=prev_day_low,
                reference_volume=avg_vol,
                current_volume=current.v,
                reason="prev_day_low",
            )
        return None
