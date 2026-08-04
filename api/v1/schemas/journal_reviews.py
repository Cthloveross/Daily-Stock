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


# --- playbook (slice C-2, append-only L2 candidates / L3 rule versions) ------


class PlaybookSourceBucketModel(BaseModel):
    """Echo of the insights bucket a candidate was saved from."""

    model_config = ConfigDict(extra="forbid")

    group_kind: Literal["tag", "error_type"]
    group_value: str = Field(min_length=1, max_length=64)
    direction: str = Field(min_length=1, max_length=24)
    boundary_policy: Literal["verified", "assumed_or_censored"]


class PlaybookCandidateCreateRequest(BaseModel):
    """Explicit user content only; nothing is created automatically."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    rule_text: str = Field(min_length=1, max_length=2000)
    source_bucket: Optional[PlaybookSourceBucketModel] = None

    @model_validator(mode="after")
    def strip_and_require_content(self) -> "PlaybookCandidateCreateRequest":
        title = self.title.strip()
        rule_text = self.rule_text.strip()
        if not title:
            raise ValueError("title cannot be empty")
        if not rule_text:
            raise ValueError("rule_text cannot be empty")
        self.title = title
        self.rule_text = rule_text
        return self


class PlaybookCandidateItem(BaseModel):
    schema_version: str = "playbook-candidate/1.0"
    id: int
    candidate_key: str
    account_key: str
    title: str
    rule_text: str
    source_bucket: Optional[PlaybookSourceBucketModel] = None
    evidence_snapshot: dict
    evidence_snapshot_sha256: str
    promoted: bool
    created_at: datetime


class PlaybookRuleItem(BaseModel):
    schema_version: str = "playbook-rule/1.0"
    id: int
    rule_key: str
    lineage_key: str
    account_key: str
    version: int = Field(ge=1)
    status: Literal["active", "retired"]
    promoted_from_candidate_id: int
    promoted_from_candidate_key: str
    previous_rule_id: Optional[int] = None
    title: str
    rule_text: str
    evidence_snapshot: dict
    evidence_snapshot_sha256: str
    is_latest_version: bool
    created_at: datetime


class PlaybookListResponse(BaseModel):
    schema_version: Literal["journal-playbook/1.0"] = "journal-playbook/1.0"
    account_key: str
    candidates: list[PlaybookCandidateItem] = Field(default_factory=list)
    rules: list[PlaybookRuleItem] = Field(default_factory=list)


class PlaybookCandidateCreateResponse(BaseModel):
    data_state: Literal["ready"] = "ready"
    created: bool
    idempotent_replay: bool
    candidate: PlaybookCandidateItem


class PlaybookRulePromoteRequest(BaseModel):
    """Promotion is always an explicit user action; no auto-promotion."""

    model_config = ConfigDict(extra="forbid")

    allow_new_version: bool = False
    expected_current_version: Optional[int] = Field(default=None, ge=1)


class PlaybookRulePromoteResponse(BaseModel):
    data_state: Literal["ready"] = "ready"
    created: bool
    idempotent_replay: bool
    rule: PlaybookRuleItem


class PlaybookRuleRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_current_version: int = Field(ge=1)


class PlaybookRuleRetireResponse(BaseModel):
    data_state: Literal["ready"] = "ready"
    retired: bool
    idempotent_replay: bool
    rule: PlaybookRuleItem


# --- playbook episode links (slice C-3, zero-write reverse lookup) -----------


class PlaybookEpisodeLinkItem(BaseModel):
    """One frozen-snapshot reference from a candidate/rule to this episode.

    ``link_state`` is ``confirmed`` when the frozen ``episode_ids`` sample
    contains the episode, and ``possible_truncated`` when the sample was
    truncated so membership is honestly unknowable.  ``bucket`` echoes the
    snapshot's frozen bucket, never a re-derived live one.
    """

    schema_version: str = "playbook-episode-link/1.0"
    kind: Literal["rule", "candidate"]
    link_state: Literal["confirmed", "possible_truncated"]
    title: str
    rule_text: str
    bucket: Optional[PlaybookSourceBucketModel] = None
    snapshot_build_id: int = Field(ge=1)
    snapshot_generated_at: Optional[datetime] = None
    lineage_key: Optional[str] = None
    version: Optional[int] = Field(default=None, ge=1)
    status: Optional[Literal["active", "retired"]] = None
    candidate_key: Optional[str] = None
    promoted: Optional[bool] = None
    created_at: datetime


class PlaybookEpisodeLinksResponse(BaseModel):
    """Confirmed rules, confirmed candidates, then possible entries."""

    schema_version: Literal["journal-playbook-episode-links/1.0"] = (
        "journal-playbook-episode-links/1.0"
    )
    account_key: str
    build_id: int = Field(ge=1)
    episode_id: int = Field(ge=1)
    links: list[PlaybookEpisodeLinkItem] = Field(default_factory=list)


# --- personal edge (个人画像回灌): zero-write descriptive stats --------------


class PersonalEdgeUnderlyingModel(BaseModel):
    """Per-underlying stats; only n >= threshold entries are returned."""

    underlying: str
    n: int = Field(ge=1)
    net: float
    win_rate: float = Field(ge=0, le=1)
    fees: float


class PersonalEdgeHoldBucketModel(BaseModel):
    """One hold-duration bucket; empty buckets keep n=0 and null ratios."""

    bucket: str
    n: int = Field(ge=0)
    net: float
    win_rate: Optional[float] = Field(default=None, ge=0, le=1)
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None


class PersonalEdgeDteBucketModel(BaseModel):
    bucket: str
    n: int = Field(ge=0)
    net: float
    win_rate: Optional[float] = Field(default=None, ge=0, le=1)


class PersonalEdgeMonthlyBucketModel(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    n: int = Field(ge=0)
    net: float
    fees: float
    win_rate: Optional[float] = Field(default=None, ge=0, le=1)


class PersonalEdgeDisciplineStatsModel(BaseModel):
    """规模与频率纪律读数：每美元回报 + 仓位 + 频率 + 本体/尾部 + 成交明细来源.

    比率一律 fail-closed：分母为 0、无可用开仓现金流或样本不足时为 null 并附
    ``*_reason``；``exact_fill_share`` < 1 表示该区间含重建成交明细。
    """

    n: int = Field(ge=0)
    trading_day_count: int = Field(ge=0)
    trades_per_day: Optional[float] = Field(default=None, ge=0)
    trades_per_day_reason: Optional[str] = None
    premium_known_count: int = Field(default=0, ge=0)
    premium_missing_count: int = Field(default=0, ge=0)
    median_premium_at_risk: Optional[float] = None
    total_premium_at_risk: Optional[float] = None
    premium_reason: Optional[str] = None
    net_pnl: float = 0.0
    pnl_per_dollar_risked: Optional[float] = None
    pnl_per_dollar_risked_reason: Optional[str] = None
    median_episode_pnl: Optional[float] = None
    body_pnl: Optional[float] = None
    body_episode_count: Optional[int] = Field(default=None, ge=0)
    body_pnl_reason: Optional[str] = None
    dte_known_count: int = Field(default=0, ge=0)
    zero_dte_share: Optional[float] = Field(default=None, ge=0, le=1)
    zero_dte_reason: Optional[str] = None
    exact_fill_share: Optional[float] = Field(default=None, ge=0, le=1)
    has_reconstructed_fills: bool = False


class PersonalEdgeDisciplineMonthModel(PersonalEdgeDisciplineStatsModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")


class PersonalEdgeDisciplineWindowModel(PersonalEdgeDisciplineStatsModel):
    """Trailing-N-trading-day window over the days present in the build."""

    requested_trading_days: int = Field(ge=1)
    start_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class PersonalEdgeDisciplineModel(BaseModel):
    """规模与频率监控：月度序列 + 当前窗口，同一 build 同一口径."""

    monthly: list[PersonalEdgeDisciplineMonthModel] = Field(default_factory=list)
    current_window: PersonalEdgeDisciplineWindowModel
    body_trim_count: int = Field(ge=1)
    body_min_episode_count: int = Field(ge=1)


class PersonalEdgeResponse(BaseModel):
    """Descriptive personal stats over the current default episode build.

    ``data_state`` is ``not_built`` when the account has no episode build;
    every numeric block is recomputed from the build at (cached) request
    time and stamped with ``build_id`` + date range + ``computed_at`` so the
    as-of moment is always explicit.  Ratios and buckets are descriptive
    statistics only — the endogeneity caveat ships in ``limitations``.
    """

    schema_version: Literal["journal-personal-edge/1.0"] = (
        "journal-personal-edge/1.0"
    )
    data_state: Literal["ready", "not_built"]
    account_key: str
    build_id: Optional[int] = Field(default=None, ge=1)
    build_key: Optional[str] = None
    source_kind: Optional[str] = None
    computed_at: Optional[datetime] = None
    first_opened_at: Optional[datetime] = None
    last_closed_at: Optional[datetime] = None
    closed_episode_count: int = Field(default=0, ge=0)
    excluded_open_count: int = Field(default=0, ge=0)
    excluded_missing_pnl_count: int = Field(default=0, ge=0)
    underlying_min_episode_count: int = Field(default=5, ge=1)
    underlyings: list[PersonalEdgeUnderlyingModel] = Field(
        default_factory=list
    )
    small_sample_underlying_count: int = Field(default=0, ge=0)
    hold_time_buckets: list[PersonalEdgeHoldBucketModel] = Field(
        default_factory=list
    )
    hold_unknown_count: int = Field(default=0, ge=0)
    dte_buckets: list[PersonalEdgeDteBucketModel] = Field(
        default_factory=list
    )
    dte_unknown: Optional[PersonalEdgeDteBucketModel] = None
    monthly: list[PersonalEdgeMonthlyBucketModel] = Field(
        default_factory=list
    )
    # additive（2026-08-04）：规模与频率纪律；not_built 时为 null。
    discipline: Optional[PersonalEdgeDisciplineModel] = None
    month_basis: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)
