# -*- coding: utf-8 -*-
"""Pydantic schemas for the Journal REST endpoints (Phase 0 v4)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional, Dict, List

from pydantic import BaseModel, Field


class TradeItem(BaseModel):
    id: int
    portfolio_label: str
    is_option: bool
    raw_symbol: Optional[str] = None
    underlying: str
    expiry: Optional[date] = None
    strike: Optional[float] = None
    right: Optional[str] = None
    direction: str
    status: str
    quantity: int
    avg_entry_price: float
    avg_exit_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    hold_seconds: Optional[int] = None
    dte_at_entry: Optional[int] = None
    dte_bucket: Optional[str] = None
    pnl_gross: Optional[float] = None
    pnl_net: Optional[float] = None
    pnl_pct: Optional[float] = None
    total_fee: Optional[float] = None
    trade_style: Optional[str] = None
    regime_score_at_entry: Optional[int] = None
    was_fake_breakout: Optional[bool] = None
    user_notes: Optional[str] = None
    emotional_state: Optional[str] = None
    strategy_tag_ai: Optional[str] = None


class TradeListResponse(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[TradeItem]


class RealityTestResponse(BaseModel):
    total_trades: int
    total_pnl_net: float
    top_n: int
    top_n_pnl_net: float
    top_n_ids: list[int]
    pnl_without_top_n: float
    top_n_pct_of_total: Optional[float] = None
    median_pnl_net: Optional[float] = None


class HealthCheckItem(BaseModel):
    check_date: date
    total_orders: int
    orders_0dte: int
    orders_1_3dte: int
    orders_opening_hour: int
    top_underlying: Optional[str] = None
    top_underlying_pct: Optional[float] = None
    warnings_json: list[Any] = Field(default_factory=list)
    pnl_estimate: Optional[float] = None
    regime_score: Optional[int] = None


class JournalStatsResponse(BaseModel):
    window_days: int
    closed_trade_count: int
    total_pnl_net: float
    win_rate: Optional[float] = None
    dte_distribution: dict[str, int]
    win_rate_by_bucket: dict[str, dict[str, Any]]
    reality_test: RealityTestResponse


class ImportResponse(BaseModel):
    inserted: int
    skipped: int
    trades_rebuilt: int
    message: str


class StatementTimeRange(BaseModel):
    first: Optional[datetime] = None
    last: Optional[datetime] = None


class MoomooStatementPreviewResponse(BaseModel):
    """Read-only quality preview for a Moomoo history CSV."""

    parser_version: str
    analysis_level: str
    rows_total: int
    orders_total: int
    status_counts: dict[str, int]
    filled_orders: int
    detail_backed_filled_orders: int
    aggregate_only_filled_orders: int
    inconsistent_filled_orders: int
    fill_records: int
    orphan_fill_rows: int
    filled_fee_total: str
    detail_backed_fee_total: str
    aggregate_only_fee_total: str
    order_time_range: StatementTimeRange
    fill_detail_time_range: StatementTimeRange
    warnings: list[str] = Field(default_factory=list)
    journal_database_written: bool = False


class MoomooOpenApiPreviewResponse(BaseModel):
    """Read-only validation result for a de-identified OpenAPI export."""

    source_schema: str
    parser_name: str
    parser_version: str
    source_sha256: str
    evidence_sha256: str
    batch_key: str
    environment: str
    market: str
    analysis_level: str
    analysis_ready: bool
    reconciliation_status: str
    reconciliation: dict[str, Optional[int]]
    warnings: list[str] = Field(default_factory=list)
    window_start: datetime
    window_end: datetime
    source_timezone: str
    order_observations: int
    fill_observations: int
    fee_observations: int
    fee_totals_by_currency: dict[str, str] = Field(default_factory=dict)
    journal_database_written: bool = False


class OpenApiSourceScope(BaseModel):
    environment: str
    market: str
    account_selection: str
    window_start: datetime
    window_end: datetime
    source_timezone: str
    order_observations: int
    fill_observations: int
    fee_observations: int
    fee_totals_by_currency: dict[str, str] = Field(default_factory=dict)


class OpenApiBaseScope(BaseModel):
    batch_id: int
    batch_key: str
    window_start: datetime
    window_end: datetime
    order_observations: int
    fill_observations: int
    window_order_observations: int


class OpenApiCoverage(BaseModel):
    scope: str
    matched_orders: int
    csv_only_in_window: int
    api_only_orders: int
    ambiguous_identity_keys: int
    covered_base_orders: int
    base_orders: int
    coverage_ratio: str
    outside_unverified_orders: int


class OpenApiWritePlan(BaseModel):
    already_imported: bool
    order_observations: int
    fill_observations: int
    fee_observations: int
    order_identity_links: int
    deal_identity_links: int
    fill_set_attestations: int
    canonical_sets: int


class OpenApiCanonicalImpact(BaseModel):
    input_order_observations: int
    input_fill_observations: int
    canonical_orders: int
    canonical_fills: int
    duplicate_order_observations: int
    duplicate_fill_observations: int
    shadowed_csv_fills: int
    shadowed_aggregate_orders: int
    blocking_issues: int
    analysis_ready: bool
    canonical_set_sha256: str


class OpenApiPlanIssue(BaseModel):
    code: str
    severity: str
    entity_kind: str
    entity_key: str
    field_name: Optional[str] = None
    message: str


class MoomooOpenApiPlanResponse(BaseModel):
    """Zero-write cross-source plan bound to one CSV baseline and API export."""

    preview_key: str
    confirm_allowed: bool
    source_scope: OpenApiSourceScope
    base_scope: Optional[OpenApiBaseScope] = None
    coverage: OpenApiCoverage
    write_plan: OpenApiWritePlan
    canonical_impact: OpenApiCanonicalImpact
    issues: list[OpenApiPlanIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    scope_is_full_batch: bool
    journal_database_written: bool = False


class OpenApiAppendedCounts(BaseModel):
    orders: int = 0
    fills: int = 0
    fees: int = 0
    order_links: int = 0
    deal_links: int = 0
    fill_set_attestations: int = 0
    canonical_sets: int = 0


class OpenApiConfirmedCanonical(BaseModel):
    orders: int
    fills: int
    shadowed_csv_fills: int
    shadowed_aggregate_orders: int
    blocking_issues: int
    analysis_ready: bool


class MoomooOpenApiConfirmResponse(BaseModel):
    status: str
    duplicate: bool
    import_batch_id: int
    canonical_set_id: int
    canonical_set_sha256: str
    scope: str
    appended: OpenApiAppendedCounts
    canonical: OpenApiConfirmedCanonical
    legacy_journal_written: bool = False
    episode_build_triggered: bool = False
    trading_action_performed: bool = False
    message: str


class LedgerImportResponse(BaseModel):
    batch_id: int
    duplicate: bool
    analysis_level: str
    order_observations: int
    fill_observations: int
    legacy_journal_written: bool = False
    message: str


class LedgerDataHealthResponse(BaseModel):
    has_data: bool
    batch_id: Optional[int] = None
    analysis_level: Optional[str] = None
    reconciliation_status: Optional[str] = None
    reconciliation_scope: Optional[str] = None
    reconciliation_window_start: Optional[datetime] = None
    reconciliation_window_end: Optional[datetime] = None
    reconciled_order_observations: int = 0
    completeness_score: Optional[str] = None
    order_observations: int = 0
    fill_observations: int = 0
    aggregate_only_filled_orders: int = 0
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    recorded_at: Optional[datetime] = None
    legacy_journal_written: bool = False


class EpisodeBuildMetadata(BaseModel):
    """Immutable provenance for the latest PositionEpisode build."""

    id: int
    build_key: str
    builder_name: str
    builder_version: str
    status: str
    source_batch_ids: list[int] = Field(default_factory=list)
    source_kind: str = "csv_batch"
    canonical_set_id: Optional[int] = None
    canonical_set_sha256: Optional[str] = None
    source_cutoff_at: datetime
    position_episode_count: int
    unresolved_evidence_count: int
    completeness_score: str
    opening_boundary_policy: str
    assumed_flat_unverified: bool
    partial_reasons: list[str] = Field(default_factory=list)
    recorded_at: datetime


class EpisodeReconciliationSummary(BaseModel):
    """Reconciliation coverage; a passed partial window is not a full pass."""

    status: str
    scope: str
    partial_window: bool
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    matched_order_count: int = 0
    total_order_count: int = 0


class EpisodeHeadlinePnl(BaseModel):
    """PnL safe for the page headline under the strict evidence policy."""

    eligible_closed_count: int
    excluded_episode_count: int
    eligibility_rule: str = (
        "closed_net_known_boundary_verified_not_left_censored_complete"
    )
    exclusion_counts: dict[str, int] = Field(default_factory=dict)
    realized_pnl_gross: Optional[str] = None
    total_fee: Optional[str] = None
    realized_pnl_net: Optional[str] = None


class EpisodeConditionalPnl(BaseModel):
    """Closed cash-flow result under the build's opening-boundary policy."""

    basis: str = "closed_known_cash_flows_under_opening_boundary_policy"
    opening_boundary_policy: str
    count: int
    realized_pnl_gross: Optional[str] = None
    total_fee: Optional[str] = None
    realized_pnl_net: Optional[str] = None
    included_in_headline: bool = False


class PositionEpisodeSummaryResponse(BaseModel):
    total_episode_count: int
    open_episode_count: int
    closed_episode_count: int
    boundary_unverified_episode_count: int
    left_censored_episode_count: int
    incomplete_episode_count: int
    aggregate_only_episode_count: int
    headline_pnl: EpisodeHeadlinePnl
    conditional_pnl: EpisodeConditionalPnl


PositionEpisodeCaseFocus = Literal[
    "top_profit",
    "top_loss",
    "largest_fee",
    "longest_hold",
]


class PositionEpisodeInstrument(BaseModel):
    raw_symbol: str
    asset_type: str
    underlying: str
    expiry: Optional[date] = None
    strike: Optional[str] = None
    option_right: Optional[str] = None
    contract_multiplier: Optional[str] = None
    currency: str


class PositionEpisodeQuality(BaseModel):
    completeness_status: str
    completeness_score: str
    construction_basis: str
    contract_multiplier_basis: str
    opening_boundary_policy: str
    assumed_flat_unverified: bool
    left_boundary_verified: bool
    is_left_censored: bool
    is_right_censored: bool
    pnl_summary_eligible: bool
    pnl_exclusion_reasons: list[str] = Field(default_factory=list)


class PositionEpisodeItem(BaseModel):
    id: int
    episode_build_id: int
    strategy_episode_id: int
    episode_key: str
    lineage_key: str
    strategy_type: str
    instrument: PositionEpisodeInstrument
    direction: str
    lifecycle_status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    hold_seconds: Optional[int] = None
    opened_quantity: str
    closed_quantity: str
    remaining_quantity: str
    average_entry_price: Optional[str] = None
    average_exit_price: Optional[str] = None
    realized_pnl_gross: Optional[str] = None
    total_fee: Optional[str] = None
    realized_pnl_net: Optional[str] = None
    quality: PositionEpisodeQuality


class PositionEpisodeEvidenceItem(BaseModel):
    id: int
    evidence_key: str
    evidence_kind: str
    event_role: str
    allocation_sequence: int
    evidence_time: datetime
    allocated_quantity: Optional[str] = None
    allocated_fee: Optional[str] = None
    allocated_cash_flow: Optional[str] = None
    allocation_ratio: Optional[str] = None
    broker_order_observation_id: Optional[int] = None
    broker_fill_observation_id: Optional[int] = None
    allocation: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class PositionEpisodeListResponse(BaseModel):
    data_state: str
    build: Optional[EpisodeBuildMetadata] = None
    reconciliation: Optional[EpisodeReconciliationSummary] = None
    summary: Optional[PositionEpisodeSummaryResponse] = None
    total: int
    page: int
    per_page: int
    items: list[PositionEpisodeItem] = Field(default_factory=list)


class PositionEpisodeDetailResponse(BaseModel):
    data_state: str
    build: EpisodeBuildMetadata
    reconciliation: EpisodeReconciliationSummary
    item: PositionEpisodeItem
    matching: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    completeness: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    evidence: list[PositionEpisodeEvidenceItem] = Field(default_factory=list)


class EpisodeBuildResponse(BaseModel):
    data_state: str
    duplicate: bool
    build: EpisodeBuildMetadata
    reconciliation: EpisodeReconciliationSummary
    summary: PositionEpisodeSummaryResponse
    message: str


class CanonicalEpisodeBuildPlanResponse(BaseModel):
    """Zero-row-write plan for one frozen canonical EpisodeBuild."""

    data_state: str
    canonical_set_id: Optional[int] = None
    canonical_set_sha256: Optional[str] = None
    build_key: Optional[str] = None
    source_batch_ids: list[int] = Field(default_factory=list)
    source_window_start: Optional[datetime] = None
    source_window_end: Optional[datetime] = None
    source_event_count: int = 0
    aggregate_order_event_count: int = 0
    detailed_fill_event_count: int = 0
    planned_position_episode_count: int = 0
    planned_open_episode_count: int = 0
    planned_closed_episode_count: int = 0
    source_known_fee_total: Optional[str] = None
    allocated_known_fee_total: Optional[str] = None
    fee_conserved: bool = False
    opening_boundary_policy: Optional[str] = None
    requires_assumed_flat_acceptance: bool = False
    default_build_id: Optional[int] = None
    default_position_episode_count: int = 0
    episode_count_delta: int = 0
    default_will_change: bool = False
    confirm_allowed: bool = False
    warnings: list[str] = Field(default_factory=list)


class CanonicalEpisodeBuildConfirmRequest(BaseModel):
    canonical_set_id: int = Field(..., gt=0)
    canonical_set_sha256: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    build_key: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    accept_assumed_flat: bool = False


class TradeUpdateRequest(BaseModel):
    user_notes: Optional[str] = None
    emotional_state: Optional[str] = None
    trade_style: Optional[str] = None


class MonthlyReviewItem(BaseModel):
    year_month: str
    current_phase: int
    review_markdown: str
    generated_at: Optional[datetime] = None


class MonthlyReviewListResponse(BaseModel):
    count: int
    items: list[MonthlyReviewItem]


class MonthlyReviewGenerateRequest(BaseModel):
    dry_run: bool = False


# ============================================================
# Stats-by-style + Journal QA (user-defined framework analysis)
# ============================================================


class StyleBucketStat(BaseModel):
    """Aggregated stats for trades of one `trade_style` label."""
    style: str
    count: int
    win_rate: float = Field(..., description="0..1")
    avg_pnl_net: float
    sum_pnl_net: float
    median_hold_seconds: Optional[int] = None
    avg_pnl_pct: Optional[float] = None


class DteBucketStat(BaseModel):
    """Aggregated stats for trades in one DTE bucket."""
    bucket: str
    count: int
    win_rate: float = Field(..., description="0..1")
    avg_pnl_net: float
    sum_pnl_net: float


class JournalStatsByStyleResponse(BaseModel):
    """Style + DTE P&L breakdown for a date window."""
    period: Dict[str, Optional[str]] = Field(
        ..., description="{'start': YYYY-MM-DD, 'end': YYYY-MM-DD}",
    )
    total_count: int
    total_pnl_net: float
    by_style: List[StyleBucketStat] = Field(default_factory=list)
    by_dte: List[DteBucketStat] = Field(default_factory=list)
    worst_trades: List[Dict[str, Any]] = Field(default_factory=list)
    best_trades: List[Dict[str, Any]] = Field(default_factory=list)


class JournalQaRequest(BaseModel):
    framework: str = Field(..., max_length=10000, description="用户自定义交易框架文本")
    question: str = Field(..., max_length=2000)
    trade_window_days: int = Field(30, ge=1, le=365)
    trade_limit: int = Field(50, ge=1, le=200)


class JournalQaResponse(BaseModel):
    answer: str = Field(..., description="LLM 返回的中文 Markdown 回答")
    trades_considered: int
    framework_hash: str = Field(..., description="sha256(framework) 前 16 位")
    generated_at: str


# ============================================================
# Moomoo live trade-account sync
# ============================================================


class MoomooSyncRequest(BaseModel):
    """Trigger a one-shot sync of the Moomoo live trade account into the journal."""

    start: Optional[str] = Field(
        None,
        description="Window start (ISO 8601). When omitted, end - window_days.",
    )
    end: Optional[str] = Field(
        None,
        description="Window end (ISO 8601). When omitted, current UTC time.",
    )
    window_days: int = Field(
        7, ge=1, le=180, description="Lookback window when `start` is omitted."
    )
    trd_env: Optional[str] = Field(
        None,
        description="Override MOOMOO_TRADE_ENV. Allowed: 'SIMULATE' (default) or 'LIVE'.",
    )
    market: str = Field("US", description="Trade market filter: US / HK / CN.")


class MoomooSyncResponse(BaseModel):
    window_start: str
    window_end: str
    trd_env: str
    market: str
    fetched: int
    inserted: int
    skipped: int
    trades_rebuilt: int
    message: str
    note: Optional[str] = None
