# -*- coding: utf-8 -*-
"""API contracts for immutable, current-only Moomoo position snapshots."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.v1.schemas.journal import (
    EpisodeBuildMetadata,
    EpisodeReconciliationSummary,
    PositionEpisodeSummaryResponse,
)


class PositionSnapshotCostContext(BaseModel):
    """Broker display context; never execution cost basis or realized P&L."""

    cost_price: Optional[str] = None
    cost_price_valid: Optional[bool] = None
    average_cost: Optional[str] = None
    diluted_cost: Optional[str] = None
    semantics: str


class PositionSnapshotMember(BaseModel):
    symbol: str
    market: Literal["US"] = "US"
    asset_type: Literal["option"] = "option"
    underlying: str
    expiry: date
    strike: str
    option_right: Literal["C", "P"]
    position_side: Literal["LONG", "SHORT"]
    quantity_contracts: str
    signed_quantity_contracts: str
    can_sell_quantity_contracts: Optional[str] = None
    currency: Literal["USD"] = "USD"
    contract_multiplier: Optional[str] = None
    basis: str
    context: PositionSnapshotCostContext


class PositionSnapshotFilterCounts(BaseModel):
    first_total_rows: int = Field(ge=0)
    second_total_rows: int = Field(ge=0)
    filtered_non_us_rows: int = Field(ge=0)
    filtered_non_option_rows: int = Field(ge=0)
    filtered_zero_quantity_rows: int = Field(ge=0)
    contract_spec_count: int = Field(ge=0)
    validation_issue_count: int = Field(ge=0)


class PositionSnapshotStability(BaseModel):
    stable: bool
    comparison_basis: str
    added_symbols: list[str] = Field(default_factory=list)
    removed_symbols: list[str] = Field(default_factory=list)
    changed_symbols: list[str] = Field(default_factory=list)


class PositionSnapshotObservation(BaseModel):
    """Locally bracketed current observation, not a broker timestamp."""

    observed_from: datetime
    observed_through: datetime
    operation_completed_at: datetime
    broker_as_of: Optional[datetime] = None
    stable: bool
    stability: PositionSnapshotStability
    retrieval_complete: bool
    position_snapshot_complete: bool
    contract_spec_status: str
    analysis_ready: bool
    content_state: Literal["positions", "empty", "incomplete", "failed"]
    position_count: int = Field(ge=0)
    total_contracts: str
    future_anchor_candidate: bool
    historical_opening_proven: Literal[False] = False
    filter_counts: PositionSnapshotFilterCounts
    positions: list[PositionSnapshotMember] = Field(default_factory=list)
    hashes: dict[str, str] = Field(default_factory=dict)


class PositionSnapshotFreshness(BaseModel):
    """Age of a local acquisition; Moomoo provides no broker as-of time."""

    status: Literal["fresh", "stale"]
    age_seconds: int = Field(ge=0)
    future_skew_seconds: int = Field(ge=0)
    threshold_seconds: int = Field(ge=1)
    evaluated_at: datetime
    age_basis: Literal["operation_completed_at"] = "operation_completed_at"
    time_semantics: str


class ConfirmedPositionSnapshotResponse(BaseModel):
    id: int = Field(ge=1)
    key: str
    recorded_at: datetime
    acknowledged_future_only: Literal[True] = True
    freshness: PositionSnapshotFreshness
    account_binding_status: Literal[
        "matches_latest_publication",
        "mismatch",
        "publication_unavailable",
    ]
    publication_anchor_status: Literal[
        "latest",
        "superseded",
        "publication_unavailable",
    ]
    is_current: bool
    observation: PositionSnapshotObservation


class PositionSnapshotLatestResponse(BaseModel):
    enabled: bool
    configured: bool
    continuity_ready: bool
    continuity_reason: Optional[str] = None
    latest_is_current: bool
    latest_account_binding_matches: Optional[bool] = None
    latest_publication_anchor_matches: Optional[bool] = None
    latest: Optional[ConfirmedPositionSnapshotResponse] = None
    trading_action_performed: Literal[False] = False


class PositionSnapshotPreviewResponse(BaseModel):
    artifact_id: int = Field(ge=1)
    artifact_key: str
    preview_key: str
    expires_at: datetime
    confirm_allowed: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    observation: PositionSnapshotObservation
    evidence_written: Literal[False] = False
    trading_action_performed: Literal[False] = False


class PositionSnapshotConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    acknowledge_future_only: Literal[True]


class PositionSnapshotConfirmResponse(BaseModel):
    snapshot: ConfirmedPositionSnapshotResponse
    duplicate: bool
    trading_action_performed: Literal[False] = False


class PositionSnapshotContinuityCounts(BaseModel):
    snapshot_member_count: int = Field(ge=0)
    chain_publication_count: int = Field(ge=0)
    chain_batch_count: int = Field(ge=0)
    target_order_count: int = Field(ge=0)
    target_fill_count: int = Field(ge=0)
    guard_fill_count: int = Field(ge=0)
    pre_boundary_fill_count: int = Field(ge=0)
    post_boundary_fill_count: int = Field(ge=0)
    aggregate_changed_pre_boundary_count: int = Field(ge=0)
    straddling_order_count: int = Field(ge=0)
    straddling_group_count: int = Field(ge=0)
    unbacked_post_boundary_count: int = Field(ge=0)
    option_identity_mismatch_count: int = Field(ge=0)


class PositionSnapshotContinuityReason(BaseModel):
    code: str = Field(min_length=1, max_length=96)
    message: str = Field(min_length=1, max_length=512)
    entity_kind: Optional[str] = Field(default=None, max_length=64)
    entity_ids: list[int] = Field(default_factory=list)
    count: int = Field(ge=0)


class PositionSnapshotContinuityResponse(BaseModel):
    """Zero-write evidence gate; this response never creates a build."""

    policy_version: str
    status: Literal["no_snapshot", "awaiting_refresh", "blocked", "ready"]
    ready_for_episode_build: bool
    fence_key: Optional[str] = None
    account_key: str
    snapshot_id: Optional[int] = Field(default=None, ge=1)
    snapshot_key: Optional[str] = None
    anchor_publication_id: Optional[int] = Field(default=None, ge=1)
    anchor_publication_key: Optional[str] = None
    target_publication_id: Optional[int] = Field(default=None, ge=1)
    target_publication_key: Optional[str] = None
    target_canonical_set_id: Optional[int] = Field(default=None, ge=1)
    target_canonical_set_sha256: Optional[str] = None
    boundary_at: Optional[datetime] = None
    guard_started_at: Optional[datetime] = None
    guard_completed_at: Optional[datetime] = None
    publication_ids: list[int] = Field(default_factory=list)
    source_batch_ids: list[int] = Field(default_factory=list)
    counts: PositionSnapshotContinuityCounts
    reasons: list[PositionSnapshotContinuityReason] = Field(default_factory=list)
    evidence_written: Literal[False] = False
    trading_action_performed: Literal[False] = False


class FuturePositionEpisodeBuildCountsResponse(BaseModel):
    """Compact build-plan counts; no Episode rows are returned or stored."""

    canonical_member_count: int = Field(ge=0)
    opening_position_count: int = Field(ge=0)
    opening_contract_count: str
    post_boundary_order_event_count: int = Field(ge=0)
    post_boundary_fill_event_count: int = Field(ge=0)
    supporting_order_count: int = Field(ge=0)
    excluded_non_option_event_count: int = Field(ge=0)
    execution_group_count: int = Field(ge=0)
    source_event_count: int = Field(ge=0)
    planned_position_episode_count: int = Field(ge=0)
    planned_open_episode_count: int = Field(ge=0)
    planned_closed_episode_count: int = Field(ge=0)
    left_censored_episode_count: int = Field(ge=0)
    right_censored_episode_count: int = Field(ge=0)
    headline_episode_count: int = Field(ge=0)
    headline_excluded_episode_count: int = Field(ge=0)
    group_fee_affected_episode_count: int = Field(ge=0)


class FuturePositionEpisodeBuildPreviewResponse(BaseModel):
    """Fence-bound, in-memory PositionEpisode build preview."""

    projection_name: str
    projection_version: str
    continuity_policy_version: str
    account_key: str
    scope: Literal["moomoo_live_us_options"]
    fence_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    snapshot_id: int = Field(ge=1)
    snapshot_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    target_publication_id: int = Field(ge=1)
    target_publication_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    target_canonical_set_id: int = Field(ge=1)
    target_canonical_set_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    canonical_source_batch_ids: list[int] = Field(default_factory=list)
    publication_source_batch_ids: list[int] = Field(default_factory=list)
    boundary_at: datetime
    source_cutoff_at: datetime
    builder_name: str
    builder_version: str
    builder_config_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    evidence_set_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    planned_build_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    opening_boundary_policy: Literal["complete_snapshot"]
    max_multiplier_proof_residual: str
    source_known_fee_total: str
    accounted_known_fee_total: str
    retained_execution_group_fee_total: str
    fee_conservation_by_currency: dict[str, dict[str, str]] = Field(
        default_factory=dict
    )
    fee_conserved: Literal[True] = True
    headline_realized_pnl_gross: Optional[str] = None
    headline_total_fee: Optional[str] = None
    headline_realized_pnl_net: Optional[str] = None
    counts: FuturePositionEpisodeBuildCountsResponse
    warnings: list[str] = Field(default_factory=list)
    preview_only: Literal[True] = True
    business_data_written: Literal[False] = False
    episode_build_written: Literal[False] = False
    activation_changed: Literal[False] = False
    evidence_written: Literal[False] = False
    trading_action_performed: Literal[False] = False


class FuturePositionEpisodeBuildConfirmRequest(BaseModel):
    """Explicit confirm body; every hash is echoed from the fenced preview."""

    model_config = ConfigDict(extra="forbid")

    expected_fence_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_build_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    expected_evidence_set_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    accept_left_censored_openings: bool = False
    accept_group_fee_scope: bool = False


class FuturePositionEpisodeBuildSourceResponse(BaseModel):
    """Immutable snapshot-fence identity recorded beside the build."""

    source_kind: Literal["position_snapshot_fenced_canonical"] = (
        "position_snapshot_fenced_canonical"
    )
    snapshot_id: int = Field(ge=1)
    snapshot_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    fence_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    continuity_policy_version: str
    target_publication_id: int = Field(ge=1)
    target_publication_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    target_canonical_set_id: int = Field(ge=1)
    target_canonical_set_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    boundary_at: datetime
    source_cutoff_at: datetime
    projection_name: str
    projection_version: str
    link_key: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class FuturePositionEpisodeBuildConfirmResponse(BaseModel):
    """EpisodeBuild append result plus its snapshot-fence identity block."""

    data_state: str
    duplicate: bool
    build: EpisodeBuildMetadata
    reconciliation: EpisodeReconciliationSummary
    summary: PositionEpisodeSummaryResponse
    snapshot_fence: FuturePositionEpisodeBuildSourceResponse
    message: str
    activation_changed: Literal[False] = False
    trading_action_performed: Literal[False] = False
