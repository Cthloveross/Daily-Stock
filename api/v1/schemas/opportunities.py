# -*- coding: utf-8 -*-
"""API contracts for the evidence-first daily opportunity research list."""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^_-]{1,32}$")
_US_OPTION_UNDERLYING_PATTERN = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z])?$")


def _normalized_symbols(value: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        symbol = str(raw or "").strip().upper()
        if not symbol:
            continue
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f"invalid symbol: {raw!r}")
        if symbol not in seen:
            normalized.append(symbol)
            seen.add(symbol)
    return normalized


class DailyOpportunityRequest(BaseModel):
    symbols: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="候选标的；空数组时回退服务端 STOCK_LIST。",
    )
    limit: int = Field(10, ge=1, le=15)
    refresh: bool = Field(
        False,
        description="显式重新扫描时绕过服务端已完成结果的短 TTL；仍复用同 key 的在途请求。",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        return _normalized_symbols(value)


class PremarketCycleRequest(BaseModel):
    symbols: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "兼容保留字段；canonical 盘前周期只使用服务端已持久化研究池，"
            "不会把请求或 STOCK_LIST 隐式提升为正式 universe。"
        ),
    )
    limit: int = Field(5, ge=1, le=15)
    manual: bool = Field(
        False,
        description="只有显式人工操作才设为 true；页面状态读取不会启动研究。",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        return _normalized_symbols(value)


class PremarketUniversePutRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=20)
    limit: int = Field(5, ge=1, le=15)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        return _normalized_symbols(value)


class PremarketUniverseResponse(BaseModel):
    schema_version: str = "premarket-research-universe/1.0"
    configured: bool
    universe_version_key: Optional[str] = None
    source: Literal[
        "persisted",
        "stock_list_fallback",
        "request_fallback",
        "unavailable",
    ]
    symbols: list[str] = Field(default_factory=list)
    limit: int = Field(5, ge=1, le=15)
    created_at: Optional[str] = None
    duplicate: bool = False
    message: str


class ReadinessItem(BaseModel):
    domain: str
    state: Literal[
        "ready",
        "partial",
        "stale",
        "background_only",
        "not_configured",
        "unavailable",
    ]
    source: Optional[str] = None
    as_of: Optional[str] = None
    actionability: str
    message: str


class EvidenceItem(BaseModel):
    evidence_id: str
    domain: str
    metric: str
    value: Any = None
    unit: Optional[str] = None
    status: Literal["supports", "neutral", "contradicts", "unknown"]
    source: str
    observed_at: Optional[str] = None
    published_at: Optional[str] = None
    fetched_at: Optional[str] = None
    observation_window: str
    quality_state: str
    actionability: str
    limitations: list[str] = Field(default_factory=list)


class HardGateItem(BaseModel):
    gate_id: str
    status: Literal["passed", "failed", "unknown"]
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class StyleMatch(BaseModel):
    status: Literal["exact", "compatible", "conflict", "unknown"]
    source: str
    matched_rules: list[str] = Field(default_factory=list)
    conflicting_rules: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)


class DataCompleteness(BaseModel):
    state: Literal["complete", "partial", "insufficient"]
    available_count: int = Field(ge=0)
    expected_count: int = Field(ge=1)


class OpportunityCandidate(BaseModel):
    candidate_id: str
    ticker: str
    research_state: Literal["research_ready", "watch_only", "context_only", "blocked"]
    directional_context: Literal["bullish", "bearish", "mixed", "unknown"]
    setup_tags: list[str] = Field(default_factory=list)
    last_completed_bar_at: Optional[str] = None
    reference_session_date: Optional[str] = None
    reference_close: Optional[float] = Field(default=None, gt=0)
    reference_price_basis: Literal["prior_completed_close"]
    source: Optional[str] = None
    style_match: StyleMatch
    hard_gates: list[HardGateItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    readiness: list[ReadinessItem] = Field(default_factory=list)
    supporting_evidence_count: int = Field(ge=0)
    data_completeness: DataCompleteness
    unknowns: list[str] = Field(default_factory=list)


class DailyOpportunityResponse(BaseModel):
    schema_version: str
    run_id: str
    run_type: Literal["morning_prior_close"]
    market_date_et: str
    as_of: str
    generated_at: str
    signal_version: str
    ranking_method: Literal["rule_based_evidence_count"]
    strategy_validation_state: Literal["not_validated"]
    strategy_validation_message: str
    universe: list[str] = Field(default_factory=list)
    requested_limit: int
    candidate_count: int
    run_readiness: list[ReadinessItem] = Field(default_factory=list)
    candidates: list[OpportunityCandidate] = Field(default_factory=list)


class OpportunityOutcomeProgress(BaseModel):
    horizon_sessions: Literal[5, 20]
    eligible_count: int = Field(ge=0)
    mature_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    partial_count: int = Field(default=0, ge=0)
    data_gap_count: int = Field(default=0, ge=0)


class OpportunityQualificationTrackSummary(BaseModel):
    track_key: Literal[
        "raw_underlying_path_v1",
        "underlying_daily_selection_v1",
        "canonical_full_research_v1",
    ]
    qualified_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    unverified_count: int = Field(ge=0)
    prospective_count: int = Field(ge=0)
    retrospective_count: int = Field(ge=0)
    observation_ready_count: int = Field(ge=0)


class OpportunityQualificationSummary(BaseModel):
    assessment_key: str
    policy_version: str
    publication_state: Literal[
        "canonical_published",
        "audit_frozen",
        "legacy_unverified",
    ]
    analysis_quality_state: Literal[
        "ready",
        "degraded",
        "blocked",
        "unassessed",
    ]
    assessed_at: str
    reason_codes: list[str] = Field(default_factory=list)
    tracks: list[OpportunityQualificationTrackSummary] = Field(
        default_factory=list
    )


class OpportunitySnapshotItem(BaseModel):
    schema_version: str = "opportunity-snapshot/1.0"
    snapshot_key: str
    market_date_et: str
    source_run_id: str
    frozen_at: str
    signal_version: str
    candidate_count: int = Field(ge=0)
    eligible_candidate_count: int = Field(ge=0)
    validation_eligible: bool
    eligibility_reasons: list[str] = Field(default_factory=list)
    analysis_quality_eligible: Optional[bool] = None
    analysis_quality_reasons: list[str] = Field(default_factory=list)
    qualification: Optional[OpportunityQualificationSummary] = None
    outcome_progress: list[OpportunityOutcomeProgress] = Field(default_factory=list)
    underlying_path_candidate_count: int = Field(default=0, ge=0)
    underlying_path_progress: list[OpportunityOutcomeProgress] = Field(
        default_factory=list
    )
    full_research_candidate_count: int = Field(default=0, ge=0)
    full_research_progress: list[OpportunityOutcomeProgress] = Field(
        default_factory=list
    )
    idempotent_replay: bool = False


class OpportunitySnapshotListResponse(BaseModel):
    schema_version: str = "opportunity-snapshot-list/1.0"
    items: list[OpportunitySnapshotItem] = Field(default_factory=list)


class OpportunitySnapshotDetailResponse(BaseModel):
    """One immutable snapshot summary plus its frozen run payload, verbatim."""

    schema_version: str = "opportunity-snapshot-detail/1.0"
    snapshot: OpportunitySnapshotItem
    run: dict[str, Any]


class OpportunitySnapshotEnsureResponse(BaseModel):
    schema_version: str = "opportunity-snapshot-ensure/1.0"
    state: Literal["saved", "existing", "outside_window", "unavailable"]
    market_date_et: str
    snapshot: Optional[OpportunitySnapshotItem] = None
    message: str


class PremarketCycleStage(BaseModel):
    name: str
    state: Literal[
        "pending",
        "running",
        "completed",
        "degraded",
        "blocked",
        "failed",
        "skipped",
    ]
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_code: Optional[str] = None


class PremarketCycleResponse(BaseModel):
    schema_version: str = "canonical-premarket-cycle/1.0"
    cycle_version: str
    freeze_policy_version: str
    scope_key: str
    cycle_key: str
    state: Literal[
        "non_session",
        "waiting_window",
        "ready_to_run",
        "research_pool_missing",
        "running",
        "published",
        "blocked",
        "window_closed",
        "failed",
    ]
    quality: Literal["unknown", "ready", "degraded", "blocked"]
    market_date_et: str
    previous_session: Optional[str] = None
    regular_open_at: Optional[str] = None
    window_start_at: Optional[str] = None
    window_end_at: Optional[str] = None
    latest_start_at: Optional[str] = None
    cycle_as_of: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_after_seconds: Optional[int] = Field(default=None, ge=1)
    universe: list[str] = Field(default_factory=list)
    requested_limit: int
    universe_source: Literal[
        "persisted",
        "stock_list_fallback",
        "request_fallback",
        "unavailable",
    ] = "unavailable"
    universe_version_key: Optional[str] = None
    attempt_key: Optional[str] = None
    attempt_trigger: Optional[Literal["manual", "scheduler"]] = None
    attempt_started_at: Optional[str] = None
    lease_expires_at: Optional[str] = None
    recovered_from_attempt_key: Optional[str] = None
    recoverable: bool = False
    next_scheduled_at: Optional[str] = None
    scheduler_enabled: bool = False
    primary_scheduled_at: Optional[str] = None
    retry_scheduled_at: Optional[str] = None
    stages: list[PremarketCycleStage] = Field(default_factory=list)
    regime_quality: dict[str, str] = Field(default_factory=dict)
    quality_reasons: list[str] = Field(default_factory=list)
    run: Optional[DailyOpportunityResponse] = None
    snapshot: Optional[OpportunitySnapshotItem] = None
    idempotent_replay: bool = False
    error_code: Optional[str] = None
    message: str


class OpportunitySnapshotEvaluationResponse(BaseModel):
    schema_version: str = "opportunity-outcome-evaluation/1.0"
    snapshot_key: str
    evaluated_at: str
    candidate_count: int = Field(ge=0)
    tracking_candidate_count: int = Field(default=0, ge=0)
    inserted_outcomes: int = Field(ge=0)
    already_recorded: int = Field(ge=0)
    pending_horizons: int = Field(ge=0)
    data_gap_horizons: int = Field(ge=0)
    outcome_progress: list[OpportunityOutcomeProgress] = Field(default_factory=list)
    underlying_path_progress: list[OpportunityOutcomeProgress] = Field(
        default_factory=list
    )
    full_research_progress: list[OpportunityOutcomeProgress] = Field(
        default_factory=list
    )
    message: str


class OpportunityLearningHorizon(BaseModel):
    horizon_sessions: Literal[5, 20]
    mature_count: int = Field(ge=0)
    distinct_signal_sessions: int = Field(ge=0)
    directional_sample_count: int = Field(default=0, ge=0)
    context_hit_count: Optional[int] = Field(default=None, ge=0)
    context_miss_count: Optional[int] = Field(default=None, ge=0)
    neutral_count: Optional[int] = Field(default=None, ge=0)
    non_directional_count: Optional[int] = Field(default=None, ge=0)
    context_hit_rate_percent: Optional[float] = Field(default=None, ge=0, le=100)
    summary_visible: bool
    investigation_ready: bool
    cohort_key: Optional[str] = None
    cohort_label: Optional[str] = None
    excluded_quality_count: int = Field(default=0, ge=0)


class OpportunityOutcomeMaintenanceStatus(BaseModel):
    session_date_et: str
    policy_version: str
    state: Literal["pending", "running", "completed", "degraded", "failed"]
    attempt_count: int = Field(ge=0, le=2)
    completed_at: Optional[str] = None
    next_retry_at: Optional[str] = None
    due_snapshot_count: int = Field(default=0, ge=0)
    inserted_outcomes: int = Field(default=0, ge=0)
    data_gap_horizons: int = Field(default=0, ge=0)
    last_error_code: Optional[str] = None


class OpportunityLearningSummaryResponse(BaseModel):
    schema_version: str = "opportunity-learning/1.0"
    generated_at: str
    strategy_state: Literal[
        "collecting",
        "descriptive_summary_available",
        "investigation_ready",
    ]
    auto_adjustment: Literal[False] = False
    minimum_summary_samples: int = 20
    minimum_investigation_samples: int = 20
    automatic_maintenance_enabled: bool = False
    maintenance_policy_version: Optional[str] = None
    latest_maintenance: Optional[OpportunityOutcomeMaintenanceStatus] = None
    horizons: list[OpportunityLearningHorizon] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class OptionContextRequest(BaseModel):
    symbols: list[str] = Field(
        min_length=1,
        max_length=3,
        description="最多 3 个美股期权 underlying；US. 前缀会被规范化移除。",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_us_option_underlyings(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            symbol = str(raw or "").strip().upper()
            if symbol.startswith("US."):
                symbol = symbol[3:]
            if not _US_OPTION_UNDERLYING_PATTERN.fullmatch(symbol):
                raise ValueError(
                    f"unsupported US option underlying: {raw!r}"
                )
            if symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        if not normalized:
            raise ValueError("at least one US option underlying is required")
        return normalized


class OptionContextItem(BaseModel):
    ticker: str
    state: Literal["ready", "not_configured", "unavailable"]
    source: str
    fetched_at: str
    expiry: Optional[str] = None
    atm_call_iv_percent: Optional[float] = Field(default=None, gt=0)
    message: str
    limitations: list[str] = Field(default_factory=list)


class OptionContextResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    items: list[OptionContextItem] = Field(default_factory=list)


class OptionOverviewRequest(BaseModel):
    symbols: list[str] = Field(
        min_length=1,
        max_length=15,
        description="最多 15 个美股期权 underlying；批量读取概览，不下单。",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_us_option_underlyings(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            symbol = str(raw or "").strip().upper()
            if symbol.startswith("US."):
                symbol = symbol[3:]
            if not _US_OPTION_UNDERLYING_PATTERN.fullmatch(symbol):
                raise ValueError(f"unsupported US option underlying: {raw!r}")
            if symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        if not normalized:
            raise ValueError("at least one US option underlying is required")
        return normalized


class OptionOverviewItem(BaseModel):
    ticker: str
    name: Optional[str] = None
    state: Literal["ready", "not_configured", "unavailable"]
    source: str
    fetched_at: str
    session_volume_date: str
    open_interest_as_of: Optional[str] = None
    volume_basis: Literal["current_session_cumulative"]
    open_interest_basis: Literal["prior_clearing_session"]
    volatility_basis: Literal["provider_snapshot"]
    call_volume: Optional[int] = Field(default=None, ge=0)
    put_volume: Optional[int] = Field(default=None, ge=0)
    put_call_volume_ratio: Optional[float] = Field(default=None, ge=0)
    call_open_interest: Optional[int] = Field(default=None, ge=0)
    put_open_interest: Optional[int] = Field(default=None, ge=0)
    put_call_open_interest_ratio: Optional[float] = Field(default=None, ge=0)
    iv_percent: Optional[float] = Field(default=None, ge=0)
    iv_rank_percent: Optional[float] = Field(default=None, ge=0, le=100)
    iv_percentile_percent: Optional[float] = Field(default=None, ge=0, le=100)
    previous_iv_percent: Optional[float] = Field(default=None, ge=0)
    iv_change_points: Optional[float] = None
    hv_30d_percent: Optional[float] = Field(default=None, ge=0)
    hv_30d_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    hv_60d_percent: Optional[float] = Field(default=None, ge=0)
    hv_60d_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    hv_90d_percent: Optional[float] = Field(default=None, ge=0)
    hv_90d_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    hv_120d_percent: Optional[float] = Field(default=None, ge=0)
    hv_120d_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    hv_365d_percent: Optional[float] = Field(default=None, ge=0)
    hv_365d_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    iv_hv30_spread_points: Optional[float] = None
    message: str
    limitations: list[str] = Field(default_factory=list)


class OptionOverviewResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    items: list[OptionOverviewItem] = Field(default_factory=list)


class OptionWallRequest(BaseModel):
    symbols: list[str] = Field(
        min_length=1,
        max_length=5,
        description="最多 5 个美股期权 underlying；US. 前缀会被规范化移除。",
    )
    dte_min: int = Field(0, ge=0, le=90)
    dte_max: int = Field(45, ge=0, le=90)

    @field_validator("symbols")
    @classmethod
    def normalize_us_option_underlyings(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            symbol = str(raw or "").strip().upper()
            if symbol.startswith("US."):
                symbol = symbol[3:]
            if not _US_OPTION_UNDERLYING_PATTERN.fullmatch(symbol):
                raise ValueError(f"unsupported US option underlying: {raw!r}")
            if symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        if not normalized:
            raise ValueError("at least one US option underlying is required")
        return normalized

    @field_validator("dte_max")
    @classmethod
    def validate_dte_range(cls, value: int, info) -> int:
        dte_min = info.data.get("dte_min", 0)
        if value < dte_min:
            raise ValueError("dte_max must be greater than or equal to dte_min")
        return value


class OptionWallScope(BaseModel):
    dte_min: int = Field(ge=0)
    dte_max: int = Field(ge=0)
    expiries: list[str] = Field(default_factory=list)
    standard_contracts_only: bool


class OptionWallCoverage(BaseModel):
    requested_contracts: int = Field(ge=0)
    snapshot_received_contracts: int = Field(ge=0)
    valid_contracts: int = Field(ge=0)
    coverage_percent: float = Field(ge=0, le=100)
    failed_batches: int = Field(ge=0)
    excluded_nonstandard_contracts: int = Field(ge=0)
    excluded_unknown_standard_type_contracts: int = Field(ge=0)
    gamma_contracts: int = Field(ge=0)


class OptionWallLevelExpiryQuote(BaseModel):
    """Quote fields traced to the single snapshot row backing one expiry cell.

    Fields absent from the observed snapshot stay ``None``; they are never
    zero-filled.  当前 Moomoo 墙快照行只携带 IV 与 update_time，bid/ask/mark
    结构性缺失，因此保持显式 null 并通过 ``quote_evidence`` 标缺。
    """

    iv_percent: Optional[float] = Field(default=None, gt=0)
    bid: Optional[float] = Field(default=None, ge=0)
    ask: Optional[float] = Field(default=None, ge=0)
    mark: Optional[float] = Field(default=None, ge=0)
    quote_as_of: Optional[str] = None


class OptionWallLevelExpiry(BaseModel):
    expiry: str
    dte: Optional[int] = Field(default=None, ge=0)
    metric_value: float = Field(gt=0)
    share_of_level_percent: float = Field(ge=0, le=100)
    contract_count: int = Field(ge=1)
    quote: OptionWallLevelExpiryQuote = Field(
        default_factory=OptionWallLevelExpiryQuote
    )
    quote_evidence: Literal["observed", "partial", "unavailable"] = "unavailable"


class OptionWallLevelExpiryOther(BaseModel):
    expiry_count: int = Field(ge=1)
    metric_value: float = Field(ge=0)
    share_of_level_percent: float = Field(ge=0, le=100)


class OptionWallLevelExpiryBreakdown(BaseModel):
    top_expiries: list[OptionWallLevelExpiry] = Field(default_factory=list)
    other: Optional[OptionWallLevelExpiryOther] = None


class OptionWallLevel(BaseModel):
    rank: int = Field(ge=1)
    strike: float = Field(gt=0)
    distance_from_spot_percent: float
    metric_value: float = Field(gt=0)
    share_of_bucket_percent: float = Field(ge=0, le=100)
    unit: Literal["contracts", "usd_delta_change_per_1pct_move"]
    method: Literal[
        "sum_open_interest",
        "sum_session_volume",
        "gross_gamma_concentration_1pct",
    ]
    # Additive per-level contract fields (option-wall/1.2); optional with
    # defaults so pre-1.2 payloads and older clients remain valid.
    side: Optional[Literal["call", "put", "call_put_aggregate"]] = None
    metric_basis: Optional[
        Literal[
            "settled_open_interest_prior_session",
            "current_session_cumulative_volume",
            "model_from_settled_oi_and_snapshot_greeks",
        ]
    ] = None
    quote_evidence: Optional[
        Literal["observed", "partial", "unavailable"]
    ] = None
    expiry_breakdown: Optional[OptionWallLevelExpiryBreakdown] = None


class OptionWallSet(BaseModel):
    call_oi: list[OptionWallLevel] = Field(default_factory=list)
    put_oi: list[OptionWallLevel] = Field(default_factory=list)
    call_volume: list[OptionWallLevel] = Field(default_factory=list)
    put_volume: list[OptionWallLevel] = Field(default_factory=list)
    call_gamma_concentration: list[OptionWallLevel] = Field(default_factory=list)
    put_gamma_concentration: list[OptionWallLevel] = Field(default_factory=list)
    gross_gamma_concentration: list[OptionWallLevel] = Field(default_factory=list)


class OptionWallAtmCallIv(BaseModel):
    state: Literal["ready", "not_configured", "unavailable"]
    expiry: Optional[str] = None
    strike: Optional[float] = Field(default=None, gt=0)
    atm_call_iv_percent: Optional[float] = Field(default=None, gt=0)
    selection_method: Literal[
        "nearest_expiry_atm_call_from_same_wall_snapshot"
    ]


class OptionWallItem(BaseModel):
    ticker: str
    state: Literal["ready", "partial", "not_configured", "unavailable"]
    source: str
    fetched_at: str
    quote_as_of: Optional[str] = None
    formula_version: str
    spot: Optional[float] = Field(default=None, gt=0)
    atm_call_iv: OptionWallAtmCallIv
    scope: OptionWallScope
    coverage: OptionWallCoverage
    walls: OptionWallSet
    message: str
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class OptionWallResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    items: list[OptionWallItem] = Field(default_factory=list)


class IntradayEarningsProximity(BaseModel):
    """v3 财报临近标记：前向 5 天窗口内最近财报日；不可得时显式标缺。

    within_blackout=None 表示日历不可得（诚实未知），不是「安全」。
    ≤3 天的「期权贵」阈值是用户自身回避规则的 v1 启发式。
    日内扫描表候选与临期合约面板（G-8）共用同一形状与同一份日历缓存。
    """

    state: Literal["ready", "unavailable"]
    days_to_earnings: Optional[int] = Field(default=None, ge=0)
    earnings_date: Optional[str] = None
    within_blackout: Optional[bool] = None
    blackout_days: int = Field(ge=0)
    window_days: int = Field(ge=1)
    basis: Literal["finnhub_earnings_calendar_forward_window"]
    source: str
    fetched_at: Optional[str] = None
    unavailable_reason: Optional[str] = None


class NearExpiryContractRequest(BaseModel):
    """临期合约面板请求：单个美股期权 underlying 的 0–max_dte 天合约读数。"""

    symbol: str = Field(
        description="单个美股期权 underlying；US. 前缀会被规范化移除。",
    )
    max_dte: int = Field(
        3,
        ge=0,
        le=7,
        description="纳入的最大 DTE（含 0DTE）；默认 3，上限 7。",
    )
    refresh: bool = Field(
        False,
        description="显式刷新时绕过服务端短 TTL；仍复用同 key 的在途请求。",
    )

    @field_validator("symbol")
    @classmethod
    def normalize_us_option_underlying(cls, value: str) -> str:
        symbol = str(value or "").strip().upper()
        if symbol.startswith("US."):
            symbol = symbol[3:]
        if not _US_OPTION_UNDERLYING_PATTERN.fullmatch(symbol):
            raise ValueError(f"unsupported US option underlying: {value!r}")
        return symbol


class NearExpiryContractRow(BaseModel):
    """单张临期合约的只读读数：逐字段可空，缺失显式标缺，不打分不推荐。"""

    code: str
    right: Literal["C", "P"]
    strike: float = Field(gt=0)
    expiry: str
    dte: int = Field(ge=0)
    bid: Optional[float] = Field(default=None, ge=0)
    ask: Optional[float] = Field(default=None, ge=0)
    mid: Optional[float] = Field(default=None, ge=0)
    spread_percent: Optional[float] = Field(default=None, ge=0)
    spread_unavailable_reason: Optional[str] = None
    last_price: Optional[float] = Field(default=None, gt=0)
    session_volume: Optional[int] = Field(default=None, ge=0)
    open_interest: Optional[int] = Field(default=None, ge=0)
    iv_percent: Optional[float] = Field(default=None, gt=0)
    delta: Optional[float] = Field(default=None, ge=-1, le=1)
    quote_as_of: Optional[str] = None
    quote_state: Literal["observed", "unavailable"]
    unavailable_reason: Optional[str] = None
    is_atm: bool = False


class NearExpiryExpiryGroup(BaseModel):
    expiry: str
    dte: int = Field(ge=0)
    state: Literal["ready", "partial", "unavailable"]
    contract_count: int = Field(ge=0)
    observed_quote_count: int = Field(ge=0)
    contracts: list[NearExpiryContractRow] = Field(default_factory=list)


class NearExpiryStrikeWindow(BaseModel):
    percent_band: float = Field(gt=0)
    min_strikes_per_side: int = Field(ge=1)
    basis: str


class NearExpiryCoverage(BaseModel):
    requested_contracts: int = Field(ge=0)
    snapshot_received_contracts: int = Field(ge=0)
    observed_contracts: int = Field(ge=0)
    missing_contracts: int = Field(ge=0)
    failed_batches: int = Field(ge=0)
    excluded_nonstandard_contracts: int = Field(ge=0)
    excluded_unknown_standard_type_contracts: int = Field(ge=0)


class NearExpiryContractItem(BaseModel):
    ticker: str
    state: Literal["ready", "partial", "empty", "not_configured", "unavailable"]
    source: str
    fetched_at: str
    formula_version: str
    max_dte: int = Field(ge=0, le=7)
    spot: Optional[float] = Field(default=None, gt=0)
    spot_as_of: Optional[str] = None
    open_interest_as_of: Optional[str] = None
    open_interest_basis: Literal["prior_clearing_session"] = (
        "prior_clearing_session"
    )
    strike_window: NearExpiryStrikeWindow
    coverage: NearExpiryCoverage
    expiries: list[NearExpiryExpiryGroup] = Field(default_factory=list)
    # v3 财报临近：与扫描表候选同形状、同一份逐 ET 日日历缓存；日历不可得
    # 时显式 unavailable（未知≠安全），与面板自身 state 正交。
    earnings_proximity: IntradayEarningsProximity
    message: str
    limitations: list[str] = Field(default_factory=list)


class NearExpiryContractResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    item: NearExpiryContractItem


class OptionEventRequest(BaseModel):
    symbols: list[str] = Field(
        min_length=1,
        max_length=3,
        description="最多 3 个美股期权 underlying；US. 前缀会被规范化移除。",
    )
    limit_per_symbol: int = Field(5, ge=1, le=10)

    @field_validator("symbols")
    @classmethod
    def normalize_us_option_underlyings(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            symbol = str(raw or "").strip().upper()
            if symbol.startswith("US."):
                symbol = symbol[3:]
            if not _US_OPTION_UNDERLYING_PATTERN.fullmatch(symbol):
                raise ValueError(f"unsupported US option underlying: {raw!r}")
            if symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        if not normalized:
            raise ValueError("at least one US option underlying is required")
        return normalized


class OptionEventRecord(BaseModel):
    event_id: str
    option_code: str
    owner_code: Optional[str] = None
    symbol: Optional[str] = None
    fill_time: Optional[str] = None
    ticker_type: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    volume: Optional[int] = Field(default=None, ge=0)
    turnover: Optional[float] = Field(default=None, ge=0)
    option_type: Optional[str] = None
    strike_price: Optional[float] = Field(default=None, gt=0)
    expiry: Optional[str] = None
    dte: Optional[int] = Field(default=None, ge=0)
    underlying_price: Optional[float] = Field(default=None, gt=0)
    bid_price: Optional[float] = Field(default=None, ge=0)
    ask_price: Optional[float] = Field(default=None, ge=0)
    iv_percent: Optional[float] = Field(default=None, ge=0)
    total_volume: Optional[int] = Field(default=None, ge=0)
    total_open_interest: Optional[int] = Field(default=None, ge=0)
    vo_ratio_percent: Optional[float] = Field(default=None, ge=0)
    delta: Optional[float] = Field(default=None, ge=-1, le=1)
    sentiment: Optional[str] = None
    order_types: list[str] = Field(default_factory=list)
    strategy_type: Optional[str] = None


class OptionEventItem(BaseModel):
    ticker: str
    state: Literal["ready", "empty", "not_configured", "unavailable"]
    source: str
    fetched_at: str
    event_as_of: Optional[str] = None
    all_count: Optional[int] = Field(default=None, ge=0)
    events: list[OptionEventRecord] = Field(default_factory=list)
    message: str
    limitations: list[str] = Field(default_factory=list)


class OptionEventResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    items: list[OptionEventItem] = Field(default_factory=list)


class IntradayTrackingRequest(BaseModel):
    symbols: list[str] = Field(
        min_length=1,
        max_length=5,
        description=(
            "冻结盘前计划中的 Top 5 美股期权 underlying；US. 前缀会被规范化移除。"
        ),
    )
    refresh: bool = Field(
        False,
        description="显式刷新时绕过服务端短 TTL；仍复用同 key 的在途请求。",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_us_option_underlyings(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            symbol = str(raw or "").strip().upper()
            if symbol.startswith("US."):
                symbol = symbol[3:]
            if not _US_OPTION_UNDERLYING_PATTERN.fullmatch(symbol):
                raise ValueError(f"unsupported US option underlying: {raw!r}")
            if symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        if not normalized:
            raise ValueError("at least one US option underlying is required")
        return normalized


class IntradayTrackingItem(BaseModel):
    """One symbol's live tracking row against the frozen premarket plan.

    每个指标要么可追溯（值 + 口径标签），要么显式标缺（reason）；
    绝不以 0 或旧值冒充实时数据。该行不包含任何买卖信号字段。
    """

    ticker: str
    state: Literal["ready", "partial", "not_configured", "unavailable"]
    source: str
    fetched_at: str
    quote_as_of: Optional[str] = None
    last_price: Optional[float] = Field(default=None, gt=0)
    session_open: Optional[float] = Field(default=None, gt=0)
    session_high: Optional[float] = Field(default=None, gt=0)
    session_low: Optional[float] = Field(default=None, gt=0)
    prev_close: Optional[float] = Field(default=None, gt=0)
    session_volume: Optional[int] = Field(default=None, ge=0)
    session_turnover: Optional[float] = Field(default=None, ge=0)
    vwap: Optional[float] = Field(default=None, gt=0)
    vwap_basis: Literal["session_turnover_over_volume"]
    vwap_unavailable_reason: Optional[str] = None
    atr14: Optional[float] = Field(default=None, gt=0)
    atr14_method: Literal["wilder_smoothing_14_daily_completed_bars"]
    atr14_bar_count: int = Field(default=0, ge=0)
    atr14_last_bar_date: Optional[str] = None
    atr14_source: Optional[str] = None
    atr14_unavailable_reason: Optional[str] = None
    volume_pace_ratio: Optional[float] = Field(default=None, ge=0)
    volume_pace_basis: Literal[
        "session_cumulative_vs_prior_20_session_full_day_median"
    ]
    prior_20d_median_volume: Optional[float] = Field(default=None, ge=0)
    volume_pace_unavailable_reason: Optional[str] = None
    message: str
    limitations: list[str] = Field(default_factory=list)


class IntradayTrackingResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    session_state: Literal["premarket", "regular", "afterhours", "closed"]
    session_state_basis: Literal["america_new_york_clock_v1"]
    tracking_basis: Literal["frozen_premarket_plan_readonly"]
    items: list[IntradayTrackingItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class IntradayTopRequest(BaseModel):
    """日内 Top N 滚动扫描请求：空 symbols 回退服务端 STOCK_LIST。"""

    symbols: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="候选标的（最多 20 个）；空数组时回退服务端 STOCK_LIST。",
    )
    limit: int = Field(5, ge=1, le=10)
    refresh: bool = Field(
        False,
        description="显式刷新时绕过服务端 60 秒 TTL；仍复用同 key 的在途请求。",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        return _normalized_symbols(value)


class IntradayTopOptionActivity(BaseModel):
    """一页有界 Moomoo 异动成交的诚实聚合：只有计数与供应商分类，无方向推断。"""

    state: Literal["ready", "empty", "not_configured", "unavailable"]
    count: int = Field(0, ge=0)
    all_count: Optional[int] = Field(default=None, ge=0)
    bullish_count: int = Field(0, ge=0)
    bearish_count: int = Field(0, ge=0)
    neutral_count: int = Field(0, ge=0)
    unclassified_count: int = Field(0, ge=0)
    dominant_sentiment: Literal["bullish", "bearish", "neutral", "mixed", "unknown"]
    max_single_turnover: Optional[float] = Field(default=None, ge=0)
    event_as_of: Optional[str] = None
    fetched_at: Optional[str] = None
    source: str
    limitations: list[str] = Field(default_factory=list)


class IntradayTopPriorDayContext(BaseModel):
    """上一完整交易日结构背景；仅作 research_context，不参与盘中排序。"""

    prior_close: Optional[float] = Field(default=None, gt=0)
    prior_close_date: Optional[str] = None
    prior_high_20d: Optional[float] = Field(default=None, gt=0)
    prior_low_20d: Optional[float] = Field(default=None, gt=0)
    range_position: Optional[
        Literal[
            "above_prior_20d_high",
            "below_prior_20d_low",
            "inside_prior_20d_range",
        ]
    ] = None
    ema_alignment: Optional[Literal["bullish", "bearish", "mixed"]] = None


# v3 时段上下文：常规时段按用户自身历史纪律再切分（提示文案为硬编码 v1）。
IntradaySessionPhase = Literal[
    "premarket",
    "opening_probe",
    "prime",
    "midday",
    "noise",
    "afternoon",
    "power_hour",
    "afterhours",
    "closed",
]


class IntradayBurstWindow(BaseModel):
    """一个 15 分钟滚动窗口的爆发读数：推力、量比与两者乘积的爆发分。"""

    start_et: str
    end_et: str
    thrust_percent: Optional[float] = None
    thrust_norm: Optional[float] = Field(default=None, ge=0)
    vol_norm: Optional[float] = Field(default=None, ge=0)
    score: Optional[float] = Field(default=None, ge=0)
    direction: Literal["up", "down", "flat"]
    # v2 波段分级：strong=暴动口径（≥8.0，2026-07-31 校准）、medium=持续
    # 推升口径（≥2.5，2026-08-03 NVDA 上午波校准）。仅波段列表携带；
    # current 窗口不分级（其强弱由 supports 口径决定）。
    grade: Optional[Literal["strong", "medium"]] = None


class IntradayBurstSpeed(BaseModel):
    """v3 速度分级：相邻两个滚动 15 分钟窗口爆发分之差（5m K 线近似）。

    「减速」对应用户纪律 R1 的离场提示，不是系统信号；窗口不足或缺分数时
    显式 unknown + reason，绝不以「持平」冒充观测值。
    """

    state: Literal["accelerating", "decelerating", "flat", "unknown"]
    current_score: Optional[float] = Field(default=None, ge=0)
    previous_score: Optional[float] = Field(default=None, ge=0)
    delta: Optional[float] = None
    basis: Literal["consecutive_rolling_15m_window_burst_score_delta_5m_bars"]
    unavailable_reason: Optional[str] = None


class IntradayMarketAlignment(BaseModel):
    """v3 大盘对齐：候选当前爆发方向 vs SPY 会话 VWAP 位置，仅作标注。"""

    state: Literal["aligned", "against", "unknown"]
    burst_direction: Optional[Literal["up", "down", "flat"]] = None
    spy_vwap_position: Optional[Literal["above", "below", "flat"]] = None
    basis: Literal[
        "candidate_current_burst_direction_vs_spy_session_vwap_position"
    ]
    unavailable_reason: Optional[str] = None


class IntradayMarketContext(BaseModel):
    """v3 SPY 大盘上下文：会话 VWAP 位置（累计额/量近似）+ 明确 provenance。"""

    ticker: str
    state: Literal["ready", "not_configured", "unavailable"]
    last_price: Optional[float] = Field(default=None, gt=0)
    vwap: Optional[float] = Field(default=None, gt=0)
    vwap_position: Literal["above", "below", "flat", "unknown"]
    vwap_basis: Literal["session_turnover_over_volume"]
    quote_as_of: Optional[str] = None
    fetched_at: Optional[str] = None
    source: str
    unavailable_reason: Optional[str] = None


class IntradaySessionBursts(BaseModel):
    """波段爆发（v2 主信号）：当前窗口 + 当日（或最近一个交易时段）波段列表。

    K 线不可得或不足时显式标 state + reason，绝不以 0 分冒充平静。
    """

    state: Literal["ready", "insufficient_bars", "unavailable"]
    session_date_et: Optional[str] = None
    bar_count: int = Field(0, ge=0)
    median_bar_range: Optional[float] = Field(default=None, ge=0)
    median_bar_volume: Optional[float] = Field(default=None, ge=0)
    median_basis: Optional[
        Literal["current_session_bars_so_far", "prior_session_fallback"]
    ] = None
    window_minutes: int = Field(15, ge=1)
    current: Optional[IntradayBurstWindow] = None
    legs: list[IntradayBurstWindow] = Field(default_factory=list)
    speed: IntradayBurstSpeed
    unavailable_reason: Optional[str] = None
    source: Optional[str] = None
    fetched_at: Optional[str] = None
    basis: str
    limitations: list[str] = Field(default_factory=list)


class IntradaySetupPlaybookRef(BaseModel):
    """S1/S2/S3 形态对应的 Playbook 条目：只读展示，规则不反哺评分或排序。"""

    setup_key: Literal["S1", "S2", "S3"]
    candidate_key: Optional[str] = None
    status: Literal["candidate", "promoted"]
    title: str


class IntradaySetupMatch(BaseModel):
    """单个 setup 的 v1 几何相似度：matched/partial/not_matched/unavailable。

    reason 与 evidence_lines 说明 v1 检查了什么、缺了什么；形态相似 ≠ 可交易。
    """

    setup_key: Literal["S1", "S2", "S3"]
    label: str
    title: str
    state: Literal["matched", "partial", "not_matched", "unavailable"]
    reason: str
    evidence_lines: list[str] = Field(default_factory=list)
    basis: str
    playbook: Optional[IntradaySetupPlaybookRef] = None


class IntradaySetupMatchProfile(BaseModel):
    """v4 styleMatch v1：当前时段几何形状 vs 用户三个 Playbook setup。

    纯标注：不参与排序、不隐藏行、不是信号；5m 聚合到 15m 近似，
    非 2m/1m 确认帧，不含 8/13 EMA 托举与回踩企稳细节。
    """

    state: Literal["ready", "unavailable"]
    style_match_version: Literal["style_match_v1"]
    quote_session_scope: Literal["current_session", "latest_prior_session"]
    session_date_et: Optional[str] = None
    bar_count_5m: int = Field(0, ge=0)
    bar_count_15m: int = Field(0, ge=0)
    matched_setups: list[Literal["S1", "S2", "S3"]] = Field(default_factory=list)
    partial_setups: list[Literal["S1", "S2", "S3"]] = Field(default_factory=list)
    setups: list[IntradaySetupMatch] = Field(default_factory=list)
    basis: str
    unavailable_reason: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)


class IntradayDeepLaneReason(BaseModel):
    """两层模式下该候选进入深度层的原因：计划钉选或异动排名（v1 闸门）。

    闸门不是信号：晋升只决定「谁被深度分析」，不代表方向或质量结论。
    """

    promoted_by: Literal["plan_always_include", "mover_rank"]
    mover_rank: Optional[int] = Field(default=None, ge=1)
    basis: Literal[
        "abs_change_percent_then_turnover_v1",
        "premarket_pre_price_change_then_pre_turnover_v1",
    ]


class IntradaySnapshotOnlyRow(BaseModel):
    """宽层（仅快照）单行：只有快照可得字段，绝不虚构深度层读数。

    行内没有爆发/形态/速度/异动/财报字段——那些属于深度层；缺失即缺席。
    """

    ticker: str
    state: Literal["ready", "partial", "unavailable"]
    last_price: Optional[float] = Field(default=None, gt=0)
    change_percent: Optional[float] = None
    change_basis: Literal["moomoo_snapshot_prev_close"]
    session_high: Optional[float] = Field(default=None, gt=0)
    session_low: Optional[float] = Field(default=None, gt=0)
    volume: Optional[float] = Field(default=None, ge=0)
    turnover: Optional[float] = Field(default=None, ge=0)
    quote_as_of: Optional[str] = None
    unavailable_reason: Optional[str] = None
    # 盘前专用读数（additive）：盘前时段常规字段仍指向上一常规时段，真实
    # 盘前变动在 pre_*；快照缺列即 None（标缺），绝不 0 回填。
    pre_change_percent: Optional[float] = None
    pre_turnover: Optional[float] = Field(default=None, ge=0)


class IntradayDeepLaneEntry(BaseModel):
    """深度层名单单行：含未上榜候选，保证深度层名单本身无声不了之。"""

    ticker: str
    promoted_by: Literal["plan_always_include", "mover_rank"]
    mover_rank: Optional[int] = Field(default=None, ge=1)


class IntradayUniverseScan(BaseModel):
    """watchlist 两层模式的诚实 universe 概览：谁被深扫、谁只有快照。

    仅在 INTRADAY_WATCHLIST 已配置且客户端未显式传 symbols 时出现；
    单层（现状）模式恒为 null。
    """

    mode: Literal["watchlist_two_tier"]
    gate_basis: Literal[
        "abs_change_percent_then_turnover_v1",
        "premarket_pre_price_change_then_pre_turnover_v1",
    ]
    gate_warnings: list[str] = Field(default_factory=list)
    watchlist_total: int = Field(ge=0)
    watchlist_truncated: bool = False
    scanned_total: int = Field(ge=0)
    deep_lane_count: int = Field(ge=0)
    deep_lane_max: int = Field(ge=1, le=20)
    deep_lane: list[IntradayDeepLaneEntry] = Field(default_factory=list)
    plan_always_include: list[str] = Field(default_factory=list)
    gated_out_count: int = Field(ge=0)
    snapshot_unresolved_symbols: list[str] = Field(default_factory=list)
    day_promotion_cap: int = Field(ge=1)
    day_promotion_cap_reached: bool = False
    snapshot_only: list[IntradaySnapshotOnlyRow] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class IntradayTopCandidate(BaseModel):
    """一行盘中滚动研究候选：每个指标要么有值+口径，要么显式标缺原因。"""

    ticker: str
    research_state: Literal["active", "watch", "insufficient"]
    state_reason: str
    supporting_evidence_count: int = Field(ge=0)
    source: str
    fetched_at: Optional[str] = None
    quote_as_of: Optional[str] = None
    last_price: Optional[float] = Field(default=None, gt=0)
    session_open: Optional[float] = Field(default=None, gt=0)
    session_high: Optional[float] = Field(default=None, gt=0)
    session_low: Optional[float] = Field(default=None, gt=0)
    session_change_percent: Optional[float] = None
    session_change_basis: Literal["moomoo_snapshot_prev_close"]
    gap_percent: Optional[float] = None
    gap_atr_multiple: Optional[float] = Field(default=None, ge=0)
    gap_basis: Literal[
        "session_open_vs_prior_completed_close_daily_loader",
        "session_open_vs_moomoo_snapshot_prev_close",
    ]
    gap_unavailable_reason: Optional[str] = None
    volume_pace_ratio: Optional[float] = Field(default=None, ge=0)
    volume_pace_basis: Literal[
        "session_cumulative_vs_prior_20_session_full_day_median"
    ]
    volume_pace_unavailable_reason: Optional[str] = None
    vwap: Optional[float] = Field(default=None, gt=0)
    vwap_position: Literal["above", "below", "flat", "unknown"]
    vwap_basis: Literal["session_turnover_over_volume"]
    vwap_unavailable_reason: Optional[str] = None
    atr14: Optional[float] = Field(default=None, gt=0)
    atr14_last_bar_date: Optional[str] = None
    atr_range_expansion: Optional[float] = Field(default=None, ge=0)
    range_expansion_unavailable_reason: Optional[str] = None
    session_bursts: IntradaySessionBursts
    earnings_proximity: IntradayEarningsProximity
    market_alignment: IntradayMarketAlignment
    setup_match: IntradaySetupMatchProfile
    option_activity: IntradayTopOptionActivity
    prior_day_context: IntradayTopPriorDayContext
    evidence: list[EvidenceItem] = Field(default_factory=list)
    message: str
    limitations: list[str] = Field(default_factory=list)
    # watchlist 两层模式（additive）：单层模式恒为 null。
    scan_tier: Optional[Literal["deep"]] = None
    deep_lane_reason: Optional[IntradayDeepLaneReason] = None


class IntradayTopRecentOptionEvent(BaseModel):
    """跨标的异动 feed 单行；供应商分类原样透传，不改写为方向结论。"""

    ticker: str
    event_id: str
    option_code: str
    fill_time: Optional[str] = None
    ticker_type: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    volume: Optional[int] = Field(default=None, ge=0)
    turnover: Optional[float] = Field(default=None, ge=0)
    option_type: Optional[str] = None
    strike_price: Optional[float] = Field(default=None, gt=0)
    expiry: Optional[str] = None
    dte: Optional[int] = Field(default=None, ge=0)
    sentiment: Optional[str] = None
    order_types: list[str] = Field(default_factory=list)
    strategy_type: Optional[str] = None


class IntradayTopResponse(BaseModel):
    """盘中滚动 Top N：不冻结、不入统计，与盘前冻结榜互不替代。"""

    schema_version: str
    run_id: str
    generated_at: str
    as_of: str
    market_date_et: str
    session_state: Literal["premarket", "regular", "afterhours", "closed"]
    session_state_basis: Literal["america_new_york_clock_v1"]
    session_phase: IntradaySessionPhase
    session_phase_label: str
    session_phase_hint_basis: Literal["user_trading_history_hardcoded_v1"]
    quote_session_scope: Literal["current_session", "latest_prior_session"]
    quote_session_label: str
    market_context: IntradayMarketContext
    signal_version: Literal["intraday_session_evidence_v5"]
    ranking_method: Literal[
        "burst_score_first_then_evidence_count",
        "rule_based_evidence_count",
    ]
    statistics_track: Literal["none_intraday_v1_unscored"]
    moomoo_enabled: bool
    universe: list[str] = Field(default_factory=list)
    # watchlist 两层模式（additive）：单层（现状）模式恒为 null。
    universe_scan: Optional[IntradayUniverseScan] = None
    unsupported_symbols: list[str] = Field(default_factory=list)
    requested_limit: int = Field(ge=1, le=10)
    candidate_count: int = Field(ge=0)
    candidates: list[IntradayTopCandidate] = Field(default_factory=list)
    recent_option_events: list[IntradayTopRecentOptionEvent] = Field(
        default_factory=list
    )
    limitations: list[str] = Field(default_factory=list)


class IntradayPulseItem(BaseModel):
    """市场脉搏单行（SPY/QQQ/VIX）：缺失显式标缺，不以 0 冒充。"""

    ticker: str
    state: Literal["ready", "partial", "not_configured", "unavailable"]
    last_price: Optional[float] = Field(default=None, gt=0)
    prev_close: Optional[float] = Field(default=None, gt=0)
    change_percent: Optional[float] = None
    change_basis: Literal["moomoo_snapshot_prev_close"]
    vwap: Optional[float] = Field(default=None, gt=0)
    vwap_position: Literal["above", "below", "flat", "unknown"] = "unknown"
    vwap_basis: Literal["session_turnover_over_volume"] = (
        "session_turnover_over_volume"
    )
    vwap_unavailable_reason: Optional[str] = None
    quote_as_of: Optional[str] = None
    fetched_at: str
    source: str
    message: str
    limitations: list[str] = Field(default_factory=list)


class IntradayPulseResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    session_state: Literal["premarket", "regular", "afterhours", "closed"]
    session_state_basis: Literal["america_new_york_clock_v1"]
    session_phase: IntradaySessionPhase
    session_phase_label: str
    session_phase_hint_basis: Literal["user_trading_history_hardcoded_v1"]
    items: list[IntradayPulseItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
