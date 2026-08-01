# -*- coding: utf-8 -*-
"""API contracts for append-only PositionEpisode review annotations."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    normalize_review_annotation_input,
)


ReviewStatus = Literal["in_progress", "completed"]


class ReviewAnnotationCreateRequest(BaseModel):
    """Explicit user content only; model-generated analysis is not accepted."""

    model_config = ConfigDict(extra="forbid")

    build_id: int = Field(ge=1)
    review_status: ReviewStatus = "in_progress"
    setup_thesis: str = ""
    entry_trigger: str = ""
    invalidation_plan: str = ""
    position_rationale: str = ""
    exit_reason: str = ""
    post_trade_reflection: str = ""
    tags: list[str] = Field(default_factory=list)
    error_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ReviewAnnotationCreateRequest":
        normalized = normalize_review_annotation_input(
            ReviewAnnotationInput(
                review_status=self.review_status,
                setup_thesis=self.setup_thesis,
                entry_trigger=self.entry_trigger,
                invalidation_plan=self.invalidation_plan,
                position_rationale=self.position_rationale,
                exit_reason=self.exit_reason,
                post_trade_reflection=self.post_trade_reflection,
                tags=tuple(self.tags),
                error_types=tuple(self.error_types),
            )
        )
        self.review_status = normalized.review_status  # type: ignore[assignment]
        self.setup_thesis = normalized.setup_thesis
        self.entry_trigger = normalized.entry_trigger
        self.invalidation_plan = normalized.invalidation_plan
        self.position_rationale = normalized.position_rationale
        self.exit_reason = normalized.exit_reason
        self.post_trade_reflection = normalized.post_trade_reflection
        self.tags = list(normalized.tags)
        self.error_types = list(normalized.error_types)
        return self


class ReviewAnnotationItem(BaseModel):
    schema_version: str = "review-annotation/1.0"
    id: int
    account_key: str
    episode_build_id: int
    position_episode_id: int
    revision: int
    review_status: ReviewStatus
    setup_thesis: str
    entry_trigger: str
    invalidation_plan: str
    position_rationale: str
    exit_reason: str
    post_trade_reflection: str
    tags: list[str] = Field(default_factory=list)
    error_types: list[str] = Field(default_factory=list)
    content_sha256: str
    previous_annotation_id: Optional[int] = None
    created_at: datetime


class ReviewAnnotationLatestResponse(BaseModel):
    data_state: Literal["not_started", "ready"]
    annotation: Optional[ReviewAnnotationItem] = None


class ReviewAnnotationHistoryResponse(BaseModel):
    data_state: Literal["not_started", "ready"]
    total: int = Field(ge=0)
    items: list[ReviewAnnotationItem] = Field(default_factory=list)


class ReviewAnnotationCreateResponse(BaseModel):
    data_state: Literal["ready"] = "ready"
    created: bool
    idempotent_replay: bool
    annotation: ReviewAnnotationItem


# --- review insights (slice C-1, zero-write L1 aggregation) ------------------


class ReviewInsightStatsModel(BaseModel):
    """Ratios over verified-P&L members only; absent below the gate."""

    win_rate: str
    avg_pnl: str
    sum_pnl: str
    win_count: int = Field(ge=0)
    loss_count: int = Field(ge=0)
    breakeven_count: int = Field(ge=0)


class ReviewInsightStatsGate(BaseModel):
    eligible: bool
    reason: Optional[
        Literal["below_sample_threshold", "no_verified_pnl_episodes"]
    ] = None


class ReviewInsightBucketModel(BaseModel):
    """One observed-pattern bucket; direction and boundary never mix."""

    group_kind: Literal["tag", "error_type"]
    group_value: str
    direction: str
    boundary_policy: Literal["verified", "assumed_or_censored"]
    episode_count: int = Field(ge=0)
    distinct_trading_day_count: int = Field(ge=0)
    review_completed_count: int = Field(ge=0)
    verified_episode_count: int = Field(ge=0)
    verified_distinct_trading_day_count: int = Field(ge=0)
    conditional_episode_count: int = Field(ge=0)
    stats: Optional[ReviewInsightStatsModel] = None
    stats_gate: ReviewInsightStatsGate


class ReviewInsightThresholds(BaseModel):
    min_episode_count: int = Field(ge=1)
    min_distinct_trading_day_count: int = Field(ge=1)


class ReviewInsightUnreviewed(BaseModel):
    """Episodes without any annotation: counts only, never any stats."""

    episode_count: int = Field(ge=0)
    distinct_trading_day_count: int = Field(ge=0)


class ReviewInsightsResponse(BaseModel):
    schema_version: Literal["journal-review-insights/1.0"] = (
        "journal-review-insights/1.0"
    )
    data_state: Literal["not_built", "ready"]
    build_id: Optional[int] = None
    build_key: Optional[str] = None
    source_kind: Optional[str] = None
    account_key: str
    generated_at: Optional[datetime] = None
    thresholds: ReviewInsightThresholds
    total_episode_count: int = Field(ge=0)
    annotated_episode_count: int = Field(ge=0)
    unreviewed: Optional[ReviewInsightUnreviewed] = None
    buckets: list[ReviewInsightBucketModel] = Field(default_factory=list)
