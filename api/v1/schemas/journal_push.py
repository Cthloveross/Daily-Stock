# -*- coding: utf-8 -*-
"""Pydantic schemas for the push-ledger daily feed (blueprint 17 Phase B)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

PushSessionScope = Literal["premarket", "regular", "afterhours"]
PushDeliveryStatus = Literal[
    "not_requested", "pending", "succeeded", "failed", "indeterminate",
    "expired_unconfirmed",
]


class P02mDataQualityModel(BaseModel):
    status: Literal["complete", "incomplete"]
    issues: List[str] = Field(default_factory=list)


class P02mSignalEvidenceModel(BaseModel):
    """Typed, additive evidence contract for strict 2m P0/P0-X rows."""

    state: str
    alert_qualified: bool
    alert_tier: Optional[Literal["P0", "P0X"]] = None
    signal_version: str
    symbol: Optional[str] = None
    universe_hash: Optional[str] = None
    session_scope: Optional[PushSessionScope] = None
    window_start: Optional[str] = None
    impulse_window_start: Optional[str] = None
    displacement_window_start: Optional[str] = None
    window_end: Optional[str] = None
    direction: Optional[Literal["up", "down", "flat"]] = None
    trigger_price: Optional[float] = None
    impulse_8m_log: Optional[float] = None
    impulse_8m_percent: Optional[float] = None
    price_percentile: Optional[float] = None
    rvol_8m: Optional[float] = None
    reference_median_volume_8m: Optional[float] = None
    displacement_18m_percent: Optional[float] = None
    displacement_atr: Optional[float] = None
    prior_daily_atr14: Optional[float] = None
    baseline_count: int = 0
    baseline_first_date: Optional[str] = None
    baseline_last_date: Optional[str] = None
    source: Optional[str] = None
    data_quality: P02mDataQualityModel
    option_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    research_fields: Dict[str, Any] = Field(default_factory=dict)
    failed_checks: List[str] = Field(default_factory=list)
    unavailable_reason: Optional[str] = None
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    basis: str
    limitations: List[str] = Field(default_factory=list)


class PushLedgerEventModel(BaseModel):
    push_id: str
    record_kind: Literal["decision", "delivery"]
    et_date: str
    ts: str
    tier: Literal["P0X", "P0", "P1", "P2", "P3", "system"]
    session_scope: Optional[PushSessionScope] = None
    rule: str
    ticker: Optional[str] = None
    text: str
    delivery_channel: Literal[
        "telegram", "telegram_silent", "web_only", "ledger_only", "none"
    ]
    suppressed: bool
    suppressed_reason: Optional[str] = None
    gate_state: Optional[str] = None
    lane_rule_id: Optional[str] = None
    option_mid: Optional[float] = None
    option_spread_pct: Optional[float] = None
    message_key: Optional[str] = None
    telegram_ok: Optional[bool] = None
    # Read-only projection; immutable ledger rows and telegram_ok stay intact.
    delivery_status: Optional[PushDeliveryStatus] = None
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    p0_signal_evidence_v2: Optional[P02mSignalEvidenceModel] = None


class PushBudgetModel(BaseModel):
    opening_used_today: int
    opening_daily_budget: int
    p0_used_today: int
    p0_daily_budget: int
    p0_used_week: int
    p0_weekly_budget: int
    p1_used_today: int
    p1_daily_budget: int
    telegram_lines_used: int
    telegram_lines_cap: int
    # Additive P0X fields keep older endpoint constructors/clients valid while
    # exposing the independent weekly and per-session budget dimensions.
    p0x_used_today_by_session: Dict[PushSessionScope, int] = Field(
        default_factory=lambda: {
            "premarket": 0,
            "regular": 0,
            "afterhours": 0,
        }
    )
    p0x_daily_budget_by_session: Dict[PushSessionScope, int] = Field(
        default_factory=dict
    )
    p0x_used_week: int = 0
    p0x_weekly_budget: int = 0
    research_rth_used_today: int = 0
    research_rth_daily_budget: int = 0
    research_rth_used_week: int = 0
    research_rth_weekly_budget: int = 0
    research_extended_used_today_by_session: Dict[PushSessionScope, int] = Field(
        default_factory=dict
    )
    research_extended_daily_budget_by_session: Dict[PushSessionScope, int] = Field(
        default_factory=dict
    )
    research_extended_used_week: int = 0
    research_extended_weekly_budget: int = 0


class P0PassRateModel(BaseModel):
    status: Literal["ready", "missing"]
    reason: Optional[str] = None
    window: int
    delivered: int
    passed: int
    rate: Optional[float] = None
    threshold: float
    auto_tighten_enabled: bool
    limitations: List[str]


class PushLedgerDailyResponse(BaseModel):
    et_date: str
    events: List[PushLedgerEventModel]
    budgets: PushBudgetModel
    p0_pass_rate: P0PassRateModel
    limitations: List[str]
