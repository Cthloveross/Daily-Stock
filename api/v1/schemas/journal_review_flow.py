# -*- coding: utf-8 -*-
"""API contracts for the guided daily close review + excursions (Phase A).

盲评契约：``DailyReviewFlowResponse`` 在会话密封并主动揭示之前**不含任何
盈亏字段**（``reveal`` 为 null）；端点测试锁定整个 JSON 载荷不出现 pnl。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SessionKind = Literal["trading_day", "rest_day"]
MetricBasis = Literal["auto", "manual", "missing"]
ManualScoreValue = Literal["yes", "no", "missing"]


class DailyEpisodeVerdictModel(BaseModel):
    episode_id: int
    raw_symbol: str
    underlying: str
    direction: str
    lifecycle_status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    dte_at_entry: Optional[int] = None
    lane: str
    rule_id: str
    verdict: str
    opened_today: bool
    closed_today: bool
    needs_ack: bool
    acked: bool


class OpenPositionExitPlanModel(BaseModel):
    episode_id: int
    raw_symbol: str
    underlying: str
    opened_at: datetime
    dte_at_entry: Optional[int] = None
    has_exit_plan: bool
    exit_plan_text: str = ""
    exit_plan_registered_at: Optional[datetime] = None


class ProcessMetricModel(BaseModel):
    metric_id: int
    name: str
    basis: MetricBasis
    value_ratio: Optional[float] = None
    value_text: Optional[str] = None
    numerator: Optional[float] = None
    denominator: Optional[float] = None
    reason: Optional[str] = None
    manual_allowed: bool = False
    manual_value: Optional[str] = None


class DailyReviewSessionModel(BaseModel):
    session_id: int
    account_key: str
    et_date: str
    session_kind: SessionKind
    revision: int
    previous_session_id: Optional[int] = None
    episode_build_id: Optional[int] = None
    started_at: datetime
    sealed_at: Optional[datetime] = None
    revealed_at: Optional[datetime] = None
    revealed_after_seal: bool
    steps: dict[str, Any]
    process_scores: list[dict[str, Any]]
    violation_acks: list[dict[str, Any]]
    note: str
    content_sha256: str
    created_at: datetime


class RevealEpisodeModel(BaseModel):
    episode_id: int
    raw_symbol: str
    verdict: str
    quadrant: str
    quadrant_label: str
    pnl_net: Optional[float] = None


class RevealSummaryModel(BaseModel):
    et_date: str
    closed_episode_count: int
    pnl_known_count: int
    total_net: Optional[float] = None
    quadrant_counts: dict[str, int]
    episodes: list[RevealEpisodeModel]


class DailyReviewFlowResponse(BaseModel):
    data_state: Literal["ready", "not_built"]
    account_key: str
    et_date: str
    build_id: Optional[int] = None
    build_key: Optional[str] = None
    session: Optional[DailyReviewSessionModel] = None
    rest_day_candidate: bool = False
    rest_streak: int = 0
    episodes_today: list[DailyEpisodeVerdictModel] = Field(default_factory=list)
    open_positions: list[OpenPositionExitPlanModel] = Field(default_factory=list)
    process_metrics: list[ProcessMetricModel] = Field(default_factory=list)
    mistake_vocabulary: list[str] = Field(default_factory=list)
    # 仅当最新会话已密封且已记录揭示时非空；此前载荷不含任何盈亏字段。
    reveal: Optional[RevealSummaryModel] = None
    limitations: list[str] = Field(default_factory=list)


class ViolationAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_episode_id: int = Field(ge=1)
    ack_text: str = Field(min_length=1, max_length=200)


class ManualScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: int = Field(ge=1, le=7)
    value: ManualScoreValue
    note: str = ""


class ExitPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_episode_id: int = Field(ge=1)
    plan_text: str = Field(min_length=1, max_length=2000)


class DailyReviewPostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    et_date: Optional[str] = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    session_kind: Optional[SessionKind] = None
    steps: dict[str, Any] = Field(default_factory=dict)
    manual_scores: list[ManualScoreRequest] = Field(default_factory=list)
    violation_acks: list[ViolationAckRequest] = Field(default_factory=list)
    exit_plans: list[ExitPlanRequest] = Field(default_factory=list)
    note: str = ""
    seal: bool = False
    # 揭示请求是最小请求：服务端逐字重放已密封修订，内容字段一律忽略。
    # expected_revision（可选）＝客户端认为的最新修订号，链位不符则 422。
    reveal: bool = False
    expected_revision: Optional[int] = Field(None, ge=1)


class DailyReviewPostResponse(BaseModel):
    created: bool
    idempotent_replay: bool
    session: DailyReviewSessionModel
    reveal: Optional[RevealSummaryModel] = None


class EpisodeVerdictModel(BaseModel):
    episode_id: int
    build_id: int
    lane: str
    rule_id: str
    verdict: str
    quadrant: str
    quadrant_label: str
    counterfactual_compliant_excluded: Optional[bool] = None
    counterfactual_compliant_text: str
    counterfactual_gate_status: str
    counterfactual_gate_reason: str


class EpisodeExcursionModel(BaseModel):
    excursion_id: int
    episode_build_id: int
    position_episode_id: int
    code_version: str
    # 同 code_version 下的重试序号（读取已取最优/最新 attempt）。
    attempt: int = 1
    source: str
    status: str
    status_reason: Optional[str] = None
    exposure: Optional[int] = None
    u0: Optional[float] = None
    u0_at: Optional[datetime] = None
    u0_flag: Optional[str] = None
    mfe_underlying_pct: Optional[float] = None
    mfe_at: Optional[datetime] = None
    mae_underlying_pct: Optional[float] = None
    mae_at: Optional[datetime] = None
    mae_before_mfe: Optional[bool] = None
    atr14: Optional[float] = None
    mfe_atr: Optional[float] = None
    mae_atr: Optional[float] = None
    bars_used: int
    coverage_start: Optional[datetime] = None
    coverage_end: Optional[datetime] = None
    missing_sessions: list[str] = Field(default_factory=list)
    computed_at: datetime


class EpisodeExcursionResponse(BaseModel):
    data_state: Literal["ready", "missing", "not_built"]
    episode_id: int
    build_id: Optional[int] = None
    excursion: Optional[EpisodeExcursionModel] = None
    # 标缺原因（无记录 / 超出 5m 保留窗口等），绝不以 0 冒充。
    missing_reason: Optional[str] = None
    verdict: Optional[EpisodeVerdictModel] = None
    disclaimer: str
    limitations: list[str] = Field(default_factory=list)


class ExcursionScatterItemModel(BaseModel):
    episode_id: int
    raw_symbol: str
    hold_structure: Literal["intraday", "overnight", "unknown"]
    status: str
    mae_underlying_pct: Optional[float] = None
    mfe_underlying_pct: Optional[float] = None
    mae_atr: Optional[float] = None
    mfe_atr: Optional[float] = None
    r_multiple: Optional[float] = None
    r_missing_reason: Optional[str] = None


class ExcursionScatterResponse(BaseModel):
    data_state: Literal["ready", "not_built"]
    account_key: str
    build_id: Optional[int] = None
    code_version: str
    items: list[ExcursionScatterItemModel] = Field(default_factory=list)
    disclaimer: str
    limitations: list[str] = Field(default_factory=list)
