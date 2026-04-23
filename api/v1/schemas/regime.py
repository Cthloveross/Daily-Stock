# -*- coding: utf-8 -*-
"""Pydantic schemas for Regime + Breakout REST endpoints."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel


class RegimeScoreItem(BaseModel):
    date: date
    score: int
    label: str
    action_hint: Optional[str] = None
    d1_direction: int
    d2_volatility: int
    d3_macro_penalty: int
    d4_sector: int
    d5_prev_day: int
    d6_premarket: int
    snapshot: dict[str, Any] = {}
    version: str
    generated_at: Optional[datetime] = None


class RegimeHistoryResponse(BaseModel):
    count: int
    items: list[RegimeScoreItem]


class BreakoutCheckResponse(BaseModel):
    passed: bool
    reason: str
    rejected_at: Optional[str] = None
    q1_regime_score: Optional[int] = None
    q1_regime_min: Optional[int] = None
    q1_passed: Optional[bool] = None
    q3_volume: Optional[dict] = None
    q4_timeframe: Optional[dict] = None
    q5_rs: Optional[dict] = None
    signal: dict


class BreakoutSignalItem(BaseModel):
    trade_id: int
    underlying: str
    entry_time: Optional[datetime] = None
    trade_style: Optional[str] = None
    was_fake_breakout: Optional[bool] = None
    pnl_net: Optional[float] = None
    regime_score_at_entry: Optional[int] = None
    breakout_volume_mult: Optional[float] = None
    rs_vs_spy: Optional[float] = None


class BreakoutSignalsResponse(BaseModel):
    count: int
    items: list[BreakoutSignalItem]
