# -*- coding: utf-8 -*-
"""Pydantic schemas for the journal edge-panel endpoint (Phase 1 E-5).

The shape is the fixed contract of
``New-docs/phase1/16_EDGE_FORENSICS_AND_REGIME_GATE.md`` E-5: both the
backend and the web card build to it verbatim.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

BlockedReason = Literal[
    "market_momentum",
    "sector_momentum",
    "volatility_scale",
    "market_data_unavailable",
]


class EdgePanelGateFeatures(BaseModel):
    mom_q_20d: Optional[float] = None
    mom_s_20d: Optional[float] = None
    mm_scale: Optional[float] = None


class EdgePanelGate(BaseModel):
    allow_0_1dte: bool
    default_bucket: Literal["2-7"] = "2-7"
    blocked_by: List[BlockedReason]
    features: EdgePanelGateFeatures
    data_status: Literal["ok", "unavailable"]


class EdgePanelBucketStat(BaseModel):
    n: int
    total: float
    mean: Optional[float] = None
    win_rate: Optional[float] = None


class EdgePanelExpectancy(BaseModel):
    n: int
    mean: Optional[float] = None
    ci95_low: Optional[float] = None
    ci95_high: Optional[float] = None


class EdgePanelEvidence(BaseModel):
    h1_t_stat: Optional[float] = None
    hlz_hurdle: float = 3.0
    status: Literal["insufficient", "significant"]
    n_needed: Optional[int] = None
    n_remaining: Optional[int] = None


class EdgePanelEdge(BaseModel):
    buckets: Dict[str, EdgePanelBucketStat]
    expectancy: EdgePanelExpectancy
    evidence: EdgePanelEvidence


class EdgePanelDiscipline(BaseModel):
    trades_today: int
    daily_cap: int = 8
    month_fees: float
    month_gross: float
    fee_ratio: Optional[float] = None


class EdgePanelResponse(BaseModel):
    as_of: str
    gate: EdgePanelGate
    edge: EdgePanelEdge
    discipline: EdgePanelDiscipline
