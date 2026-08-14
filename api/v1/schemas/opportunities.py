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


class OptionWallTotals(BaseModel):
    """Window totals behind the aggregate ratios (so a reader can check them)."""

    call_oi: float = Field(ge=0)
    put_oi: float = Field(ge=0)
    call_volume: float = Field(ge=0)
    put_volume: float = Field(ge=0)


class OptionWallRatio(BaseModel):
    """One aggregate call/put ratio — a fact, never a direction signal.

    ``value`` is ``None`` with an explicit ``reason`` whenever the denominator
    is zero or the window carries no valid contracts.  它绝不以 0、1 或无穷大
    冒充一个「有定义」的比例。``metric_basis`` 说明这一侧读的是**上一交易日
    结算后的 OI** 还是**当日累计成交量**——两者不是同一件事，不可混读。
    """

    value: Optional[float] = Field(default=None, ge=0)
    numerator_total: float = Field(ge=0)
    denominator_total: float = Field(ge=0)
    numerator_side: Literal["call", "put"]
    denominator_side: Literal["call", "put"]
    metric_basis: Literal[
        "settled_open_interest_prior_session",
        "current_session_cumulative_volume",
    ]
    reason: Optional[str] = None


class OptionWallRatios(BaseModel):
    call_put_oi_ratio: OptionWallRatio
    call_put_volume_ratio: OptionWallRatio
    #: 逐字渲染：这两个比例在本账户数据上尚未被检验过。
    caveat: str


class OptionWallOiWeightedCenter(BaseModel):
    """``Σ(strike × OI) / Σ(OI)`` —— 描述性重心，**不是** max pain 预测。

    ``validated_as_price_magnet`` 恒为 ``False``：本仓库没有历史 OI 序列，
    从未检验过价格是否会向这个位置靠拢，因此任何把它当目标位使用的读法都
    是在用未经检验的假设下注。
    """

    strike: Optional[float] = Field(default=None, gt=0)
    total_open_interest: float = Field(ge=0)
    label: str
    method: Literal["open_interest_weighted_mean_strike"]
    metric_basis: Literal["settled_open_interest_prior_session"]
    validated_as_price_magnet: Literal[False] = False
    reason: Optional[str] = None


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
    # Additive (option-wall/1.3); optional with defaults so pre-1.3 payloads
    # and older clients stay valid.
    totals: Optional[OptionWallTotals] = None
    ratios: Optional[OptionWallRatios] = None
    oi_weighted_center: Optional[OptionWallOiWeightedCenter] = None
    message: str
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class OptionWallResponse(BaseModel):
    schema_version: str
    generated_at: str
    market_date_et: str
    items: list[OptionWallItem] = Field(default_factory=list)


class OptionWallSnapshotRequest(OptionWallRequest):
    """Same bounded symbol contract as the wall read it reuses (≤5 symbols).

    刻意继承 :class:`OptionWallRequest`：逐日快照记录器**复用同一条墙位车道
    与它的缓存**，不新增取数路径，也不会扩大标的范围。
    """


class OptionWallSnapshotResultItem(BaseModel):
    """One ticker's outcome: written, replayed, or explicitly skipped.

    ``written=False`` 且 ``duplicate=False`` 表示 fail-closed 跳过——该标的
    本次没有取得可用观测，于是**什么都没写**（不是写了一行 0）。
    """

    ticker: str
    market_date_et: str
    state: str
    written: bool
    duplicate: bool
    reason: Optional[str] = None
    coverage_percent: Optional[float] = Field(default=None, ge=0, le=100)


class OptionWallSnapshotResponse(BaseModel):
    schema_version: Literal["option-wall-daily-snapshot/1.0"] = (
        "option-wall-daily-snapshot/1.0"
    )
    market_date_et: str
    generated_at: str
    results: list[OptionWallSnapshotResultItem] = Field(default_factory=list)


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


class NearExpiryAvailabilityExpiry(BaseModel):
    """车道可用性里的一个到期日：只有 (到期日, DTE)，不含任何报价。"""

    expiry: str
    dte: int = Field(ge=0)


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
    # 今日车道可用性（V2-E，additive）：链已读到即由已在手的到期日分组推导，
    # 零额外抓取。has_zero_dte=None 表示链读不到（未知），**不是**「没有
    # 0DTE」；state="empty"（链可读、窗口内无到期日）时为 False。
    has_zero_dte: Optional[bool] = None
    available_dte_list: list[int] = Field(default_factory=list)
    availability_unavailable_reason: Optional[str] = None
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
    # 用户在「盘中计划」里手动提升的标的（additive，≤8）：与既有 user_pinned
    # 同语义并入深度层（不占异动额度、与计划/钉选去重、并入同一批快照，
    # 不新增任何取数路径）。**不影响 universe 解析**：仍是空 symbols 才走两层
    # 扫描，因此它不会像 symbols 那样把两层模式关掉。单层路径完全不受影响。
    focus_symbols: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="盘中计划提升的标的（最多 8 个）；只在两层扫描模式下生效。",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        return _normalized_symbols(value)

    @field_validator("focus_symbols")
    @classmethod
    def normalize_focus_symbols(cls, value: list[str]) -> list[str]:
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


class IntradayFizzleReference(BaseModel):
    """哑火形态的冻结研究参考数字（fresh onsets、时间有序切分）。"""

    sample: str
    in_sample_rate: float = Field(ge=0, le=1)
    out_of_sample_rate: float = Field(ge=0, le=1)
    base_rate_in: float = Field(ge=0, le=1)
    base_rate_out: float = Field(ge=0, le=1)
    n_in: int = Field(ge=0)
    n_out: int = Field(ge=0)


class IntradayFizzleFlag(BaseModel):
    """v7 哑火形态：对**当前 15 分钟窗口**的形态描述 + 历史频率（additive 标注）。

    命中＝intraday 分层（median_basis 非上一时段回退）且窗口效率 ≥0.9
    （|收−开| ÷ 窗口高低差，几乎无回撤）且量比 <2.0（量能平平）。2026-08 起速
    回放研究（9,173 次爆发起点、21 标的 × 123 个交易时段）实测：该形态 30 分钟
    内达到 ≥0.5 ATR 有利位移仅 26.8%（样本内 n=291）/ 25.4%（样本外 n=177），
    基准 47.8%/50.5%；方向一致性 20/20 标的、6/6 月、三把 ATR 标尺同号。

    **不是卖出信号、不是方向判断**：同一份样本里起速那一刻的方向 AUC 全在
    0.48–0.52、P(方向)=50.6%。unavailable＝缺输入，缺席不是「没命中」的结论。
    """

    state: Literal["flagged", "not_flagged", "unavailable"]
    efficiency: Optional[float] = Field(default=None, ge=0)
    vol_norm: Optional[float] = Field(default=None, ge=0)
    stratum: Optional[Literal["open", "intraday"]] = None
    reason: str
    basis: str
    reference: IntradayFizzleReference


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
    # v7 哑火形态（additive）：描述 current 窗口；旧载荷可省略。
    fizzle_flag: Optional[IntradayFizzleFlag] = None
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


class IntradayRecentDisplacement(BaseModel):
    """v6 近 30 分钟位移：它「已经」在不在动，按 ATR 归一化（additive 标注）。

    证据来源是用户自己的 766 笔期权回合（2026-06-08→07-31，Journal build #3
    取证分析）：进场几何（追高 vs 回调）没有预测力，进场后 30 分钟的位移把
    结果分得很开——速死亏损单（<30 分钟）前向 MFE 中位 0.18 ATR /
    MAE −0.49 ATR，仅 18.3% 达到 ≥0.5 ATR；走出来的赢家（30 分钟–3 小时）
    MFE 0.69 / MAE −0.13，71.2% 达到 ≥0.5 ATR；样本 84% 的合约 ≤1DTE。

    【v8 更正】上面这张表按**持仓时长**分组，而持仓时长本身由结果决定——该分层
    按构造就是循环的。2026-08 的 2m 回放研究（3,492 次回踩持稳进场）量化了它：
    循环记分下「速度未死 vs 已死」的 P(>0) 差 37.6 个百分点（54.4% vs 16.8%），
    改成只从第 15 分钟检查点**向前**计分后只剩 **0.1 个百分点**（49.9% vs
    49.8%），且样本内那点微弱效应样本外反号（+0.122 → −0.128）。结论：肉眼可见
    的鸿沟几乎全部是循环性，**本字段只描述已经发生的事，不含任何前向信息**。

    因此 ``survival_line_atr = 0.5`` 只是**用户自己样本里的经验参考刻度**——
    描述统计，不是预测、不是买卖信号，更不是「越过就能活下来」的门槛；样本窗口
    恰是他最差的两个月。K 线不足或 ATR 标尺不可得时显式标缺，绝不 0 回填。
    """

    state: Literal["ready", "insufficient_bars", "unavailable"]
    window_minutes: int = Field(30, ge=1)
    net_move_atr: Optional[float] = None
    high_excursion_atr: Optional[float] = None
    low_excursion_atr: Optional[float] = None
    abs_range_atr: Optional[float] = Field(default=None, ge=0)
    atr_basis: Optional[
        Literal[
            "atr14_daily",
            # v7 新增回退层：上一批交易时段真实波幅均值（日线量级、当日内恒定）。
            "prior_sessions_true_range_mean",
            "intraday_20bar_proxy_x3",
        ]
    ] = None
    # v7 ATR 标尺可比性（additive）：这一行的读数能不能与「日线 ATR14 口径」
    # 横向比较。旧盘中代理与真实日线 ATR14 之比在盘中 0.28→0.42→0.20 漂移，
    # 因此显式标 not_comparable，绝不让它冒充同一把尺。旧载荷可省略。
    atr_scale_comparability: Optional[
        Literal[
            "daily_atr14",
            "daily_scale_prior_sessions_approximate",
            "intraday_scale_not_comparable",
        ]
    ] = None
    atr_prior_session_count: Optional[int] = Field(default=None, ge=1)
    survival_line_atr: float = Field(0.5, gt=0)
    bar_count: int = Field(0, ge=0)
    # additive（断档诚实化）：窗口实际跨度（末根开始 + 5 分钟 − 首根开始）。
    # == window_minutes 即无断档；> window_minutes 表示 6 根 K 线含断档
    # （停牌/缺 K 线），消费端必须标注「含断档，跨 X 分钟」；超过 45 分钟时
    # 读数本身已按 fail-closed 标缺。旧载荷可省略。
    window_span_minutes: Optional[int] = Field(default=None, ge=1)
    unavailable_reason: Optional[str] = None


class IntradayDeepLaneReason(BaseModel):
    """两层模式下该候选进入深度层的原因：计划钉选/用户钉选/异动排名。

    闸门不是信号：晋升只决定「谁被深度分析」，不代表方向或质量结论。
    v2 常规口径＝15 分钟动量子额度优先、当日涨跌子额度兜底（2026-08-03
    普涨跳空日校准：|当日涨跌| 单口径会让隔夜跳空横盘标的挤掉正在动的标的）。
    """

    promoted_by: Literal[
        "plan_always_include", "user_pinned", "user_focus", "mover_rank"
    ]
    mover_rank: Optional[int] = Field(default=None, ge=1)
    basis: Literal[
        "abs_change_percent_then_turnover_v1",
        "momentum15m_then_day_change_v2",
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
    promoted_by: Literal[
        "plan_always_include", "user_pinned", "user_focus", "mover_rank"
    ]
    mover_rank: Optional[int] = Field(default=None, ge=1)


class IntradayDayLedgerEntry(BaseModel):
    """今日曾深扫账本单行：轮换出深度层的标的以最后一次深扫摘要 as-of 呈现。

    数据取自该标的最后一次进入深度层的周期（含分级波段与形态匹配），不实时
    刷新；进程内缓存——重启后只从当前时刻起累计（day_ledger_basis 如实声明），
    不写数据库。
    """

    ticker: str
    last_seen_at: str
    session_bursts_legs: list[IntradayBurstWindow] = Field(default_factory=list)
    setup_matched_setups: list[Literal["S1", "S2", "S3"]] = Field(
        default_factory=list
    )
    last_change_percent: Optional[float] = None
    state: Literal["rotated_out"]


class IntradayTrimmedLaneEntry(BaseModel):
    """因总行数上限未进深度层的标的：显式披露，绝不静默丢弃。"""

    ticker: str
    would_be_promoted_by: Literal[
        "plan_always_include", "user_pinned", "user_focus", "mover_rank"
    ]


class IntradayUniverseScan(BaseModel):
    """watchlist 两层模式的诚实 universe 概览：谁被深扫、谁只有快照。

    仅在 INTRADAY_WATCHLIST 已配置且客户端未显式传 symbols 时出现；
    单层（现状）模式恒为 null。
    """

    mode: Literal["watchlist_two_tier"]
    gate_basis: Literal[
        "abs_change_percent_then_turnover_v1",
        "momentum15m_then_day_change_v2",
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
    # 用户钉选（INTRADAY_PINNED_TICKERS，additive）：旧载荷可省略。
    user_pinned: list[str] = Field(default_factory=list)
    # 盘中计划提升（请求内 focus_symbols，additive）：旧载荷可省略。
    user_focus: list[str] = Field(default_factory=list)
    gated_out_count: int = Field(ge=0)
    snapshot_unresolved_symbols: list[str] = Field(default_factory=list)
    day_promotion_cap: int = Field(ge=1)
    day_promotion_cap_reached: bool = False
    # 今日曾深扫账本（additive）：旧载荷可省略；重启后只从当前时刻累计。
    day_ledger: list[IntradayDayLedgerEntry] = Field(default_factory=list)
    day_ledger_basis: Literal[
        "in_process_since_service_start_resets_on_restart"
    ] = "in_process_since_service_start_resets_on_restart"
    # 深度层总行数硬顶（用户要求「留 8 个」）与被挤掉的标的：优先级
    # 盘中计划 → 用户钉选 → 异动（保底名额）→ 盘前计划；披露而非静默丢弃。
    deep_lane_total_max: int = Field(default=8, ge=1, le=40)
    trimmed_by_total_cap: list[IntradayTrimmedLaneEntry] = Field(
        default_factory=list
    )
    snapshot_only: list[IntradaySnapshotOnlyRow] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class IntradayTickerLaneAvailability(BaseModel):
    """单个标的今日 0..max_dte 的到期日可用性读数。

    ``state="unavailable"`` 时 ``has_zero_dte`` 恒为 None：**未知不等于
    「今天没有 0DTE」**。``state="ready"`` 且 ``available_dte_list`` 为空是
    诚实空态——链可读、窗口内确实没有到期日。
    """

    ticker: str
    state: Literal["ready", "unavailable"]
    has_zero_dte: Optional[bool] = None
    available_dte_list: list[int] = Field(default_factory=list)
    expiries: list[NearExpiryAvailabilityExpiry] = Field(default_factory=list)
    unavailable_reason: Optional[str] = None


class IntradayLaneAvailability(BaseModel):
    """今日车道可用性（V2-E）：深度层标的今天有没有 0DTE 可用。

    ``day_type`` 三态：``intraday_available``（≥1 个标的确证有 0DTE）/
    ``overnight_only``（全部标的都读到了链且都没有 0DTE → 日内车道关闭）/
    ``unknown``（一个都没查，或没查到 0DTE 但存在读不到的标的）。
    判定输入是当日真实期权到期日元数据，**不含任何星期规则**——假日与
    特殊到期会让星期规则失效。只描述可用性，不打分、不推荐。
    """

    formula_version: str
    market_date_et: str
    max_dte: int = Field(ge=0, le=7)
    day_type: Literal["intraday_available", "overnight_only", "unknown"]
    day_type_reason: str
    basis: Literal["per_ticker_option_expiry_metadata_within_0_7_dte_v1"]
    checked_scope: Literal["intraday_deep_lane_tickers"]
    checked_count: int = Field(ge=0)
    readable_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    zero_dte_tickers: list[str] = Field(default_factory=list)
    deferred_tickers: list[str] = Field(default_factory=list)
    tickers: list[IntradayTickerLaneAvailability] = Field(default_factory=list)
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
    # v7 哑火形态（additive）：当前窗口的形态描述 + 历史频率；旧载荷可省略。
    fizzle_flag: Optional[IntradayFizzleFlag] = None
    earnings_proximity: IntradayEarningsProximity
    market_alignment: IntradayMarketAlignment
    setup_match: IntradaySetupMatchProfile
    recent_displacement: IntradayRecentDisplacement
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
    signal_version: Literal["intraday_session_evidence_v8"]
    ranking_method: Literal[
        "burst_score_first_then_evidence_count",
        "rule_based_evidence_count",
    ]
    statistics_track: Literal["none_intraday_v1_unscored"]
    moomoo_enabled: bool
    universe: list[str] = Field(default_factory=list)
    # watchlist 两层模式（additive）：单层（现状）模式恒为 null。
    universe_scan: Optional[IntradayUniverseScan] = None
    # 今日车道可用性（V2-E，additive）：仅两层模式对深度层标的判定；
    # 单层（现状）模式恒为 null。
    lane_availability: Optional[IntradayLaneAvailability] = None
    unsupported_symbols: list[str] = Field(default_factory=list)
    requested_limit: int = Field(ge=1, le=10)
    candidate_count: int = Field(ge=0)
    candidates: list[IntradayTopCandidate] = Field(default_factory=list)
    recent_option_events: list[IntradayTopRecentOptionEvent] = Field(
        default_factory=list
    )
    limitations: list[str] = Field(default_factory=list)
    # 延迟诊断（additive）：工厂墙钟耗时（秒）。命中完成态缓存的响应报告
    # **原始**生成耗时，不是本次请求耗时；上游未提供时保持缺席。
    generated_in_seconds: Optional[float] = Field(default=None, ge=0)
    # fresh=等到了一次工厂运行；cache=命中请求驱动的完成态缓存；
    # warm_cache=命中服务端预热循环写入的完成态缓存。载荷其余字段与发起方
    # 无关——预热结果与请求驱动结果不可区分。
    served_from: Optional[Literal["fresh", "cache", "warm_cache"]] = None


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
