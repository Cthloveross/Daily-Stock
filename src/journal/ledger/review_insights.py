# -*- coding: utf-8 -*-
"""Zero-write L1 aggregation of review annotations (PatternObservation).

Implements slice C-1 of ``New-docs/phase1/13_PLAYBOOK_PROMOTION_CONTRACT.md``:
observed-pattern buckets keyed by at least ``{group_kind, group_value,
direction, boundary_policy}``, derived on demand from the current default
episode build joined with each episode's latest review annotation revision.

Hard boundaries honored here:

* pure SELECT derivation — no new tables, no rows written, no counters;
* win-rate / expectancy style ratios fail closed below the sample threshold
  (default: 10 verified episodes across 5 distinct ET trading days);
* conditional P&L episodes (assumed-flat / left-censored / group-fee affected
  / open / incomplete) are counted separately and never enter the stats,
  even above the threshold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from src.journal.ledger.activation_repository import (
    CANONICAL_SOURCE_KIND,
    SNAPSHOT_FENCE_SOURCE_KIND,
)
from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    _latest_build,
    _position_item,
    position_episode_pnl_exclusion_reasons,
)
from src.journal.ledger.models import (
    EpisodeBuildCanonicalSource,
    EpisodeBuildSnapshotFenceSource,
    PositionEpisode,
    ReviewAnnotation,
    StrategyEpisode,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.ledger.review_repository import _parse_labels
from src.storage import get_db

__all__ = [
    "BOUNDARY_POLICY_ASSUMED_OR_CENSORED",
    "BOUNDARY_POLICY_VERIFIED",
    "DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT",
    "DEFAULT_MIN_EPISODE_COUNT",
    "REVIEW_INSIGHTS_SCHEMA_VERSION",
    "STATS_GATE_BELOW_SAMPLE_THRESHOLD",
    "STATS_GATE_NO_VERIFIED_PNL",
    "ReviewInsightBucket",
    "ReviewInsightStats",
    "ReviewInsightsResult",
    "ReviewInsightsUnreviewed",
    "get_latest_review_insights",
]


REVIEW_INSIGHTS_SCHEMA_VERSION = "journal-review-insights/1.0"
DEFAULT_MIN_EPISODE_COUNT = 10
DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT = 5
# Machine-readable stats-gate reasons: the sample below the threshold, or a
# bucket whose members are all conditional-P&L (never eligible for ratios).
STATS_GATE_BELOW_SAMPLE_THRESHOLD = "below_sample_threshold"
STATS_GATE_NO_VERIFIED_PNL = "no_verified_pnl_episodes"

BOUNDARY_POLICY_VERIFIED = "verified"
BOUNDARY_POLICY_ASSUMED_OR_CENSORED = "assumed_or_censored"

_EXCHANGE_TZ = ZoneInfo("America/New_York")
_RATE_QUANTUM = Decimal("0.0001")
_AMOUNT_QUANTUM = Decimal("0.0000000001")


@dataclass(frozen=True)
class ReviewInsightStats:
    """Ratios over verified-P&L members only; present above the gate."""

    win_rate: Decimal
    avg_pnl: Decimal
    sum_pnl: Decimal
    win_count: int
    loss_count: int
    breakeven_count: int


@dataclass(frozen=True)
class ReviewInsightBucket:
    """One observed-pattern bucket; direction and boundary never mix."""

    group_kind: str  # "tag" | "error_type"
    group_value: str
    direction: str  # "LONG" | "SHORT" (uppercased episode direction)
    boundary_policy: str  # BOUNDARY_POLICY_* value
    episode_count: int  # every member episode of this bucket
    distinct_trading_day_count: int  # ET days across every member
    review_completed_count: int
    verified_episode_count: int  # members with verified (headline-style) P&L
    verified_distinct_trading_day_count: int
    conditional_episode_count: int  # never enters stats
    stats: Optional[ReviewInsightStats]
    stats_gate_eligible: bool
    stats_gate_reason: Optional[str]


@dataclass(frozen=True)
class ReviewInsightsUnreviewed:
    """Episodes with no annotation at all: counts only, never any stats."""

    episode_count: int
    distinct_trading_day_count: int


@dataclass(frozen=True)
class ReviewInsightsResult:
    """Derived L1 view over one immutable default build; nothing persisted."""

    build_id: int
    build_key: str
    source_kind: str
    account_key: str
    generated_at: datetime
    min_episode_count: int
    min_distinct_trading_day_count: int
    total_episode_count: int
    annotated_episode_count: int
    unreviewed: ReviewInsightsUnreviewed
    buckets: tuple[ReviewInsightBucket, ...]


@dataclass
class _BucketState:
    member_ids: set[int] = field(default_factory=set)
    all_days: set[date] = field(default_factory=set)
    completed_count: int = 0
    verified_days: set[date] = field(default_factory=set)
    verified_pnl: list[Decimal] = field(default_factory=list)
    conditional_count: int = 0


def _build_source_kind(session, build_id: int) -> str:
    """Derive the build's evidence source the same way summaries do."""
    canonical_linked = session.execute(
        select(EpisodeBuildCanonicalSource.id)
        .where(EpisodeBuildCanonicalSource.episode_build_id == build_id)
        .limit(1)
    ).scalar_one_or_none()
    if canonical_linked is not None:
        return CANONICAL_SOURCE_KIND
    fence_linked = session.execute(
        select(EpisodeBuildSnapshotFenceSource.id)
        .where(EpisodeBuildSnapshotFenceSource.episode_build_id == build_id)
        .limit(1)
    ).scalar_one_or_none()
    if fence_linked is not None:
        return SNAPSHOT_FENCE_SOURCE_KIND
    return "csv_batch"


def _trading_day(value: datetime) -> date:
    """Map an episode open time to its US exchange (ET) calendar day."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_EXCHANGE_TZ).date()


def _stats(pnl_values: list[Decimal]) -> ReviewInsightStats:
    win_count = sum(1 for value in pnl_values if value > 0)
    loss_count = sum(1 for value in pnl_values if value < 0)
    breakeven_count = len(pnl_values) - win_count - loss_count
    total = sum(pnl_values, Decimal("0"))
    count = Decimal(len(pnl_values))
    return ReviewInsightStats(
        win_rate=(Decimal(win_count) / count).quantize(
            _RATE_QUANTUM, rounding=ROUND_HALF_EVEN
        ),
        avg_pnl=(total / count).quantize(
            _AMOUNT_QUANTUM, rounding=ROUND_HALF_EVEN
        ),
        sum_pnl=total.quantize(_AMOUNT_QUANTUM, rounding=ROUND_HALF_EVEN),
        win_count=win_count,
        loss_count=loss_count,
        breakeven_count=breakeven_count,
    )


def _bucket(
    key: tuple[str, str, str, str],
    state: _BucketState,
    *,
    min_episode_count: int,
    min_distinct_trading_day_count: int,
) -> ReviewInsightBucket:
    group_kind, group_value, direction, boundary_policy = key
    verified_count = len(state.verified_pnl)
    verified_day_count = len(state.verified_days)
    if verified_count == 0:
        eligible = False
        reason: Optional[str] = STATS_GATE_NO_VERIFIED_PNL
    elif (
        verified_count < min_episode_count
        or verified_day_count < min_distinct_trading_day_count
    ):
        eligible = False
        reason = STATS_GATE_BELOW_SAMPLE_THRESHOLD
    else:
        eligible = True
        reason = None
    return ReviewInsightBucket(
        group_kind=group_kind,
        group_value=group_value,
        direction=direction,
        boundary_policy=boundary_policy,
        episode_count=len(state.member_ids),
        distinct_trading_day_count=len(state.all_days),
        review_completed_count=state.completed_count,
        verified_episode_count=verified_count,
        verified_distinct_trading_day_count=verified_day_count,
        conditional_episode_count=state.conditional_count,
        stats=_stats(state.verified_pnl) if eligible else None,
        stats_gate_eligible=eligible,
        stats_gate_reason=reason,
    )


def get_latest_review_insights(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    min_episode_count: int = DEFAULT_MIN_EPISODE_COUNT,
    min_distinct_trading_day_count: int = (
        DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT
    ),
) -> Optional[ReviewInsightsResult]:
    """Aggregate the default build's latest annotations into pattern buckets.

    Returns ``None`` when the account has no episode build.  The whole read
    happens in one session and issues only SELECT statements.
    """
    if min_episode_count < 1:
        raise EpisodeRepositoryError("min_episode_count must be positive")
    if min_distinct_trading_day_count < 1:
        raise EpisodeRepositoryError(
            "min_distinct_trading_day_count must be positive"
        )
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, account_key)
        if build is None:
            return None

        # Same latest-revision-per-episode pattern as the review queue counts.
        latest_revisions = (
            select(
                ReviewAnnotation.position_episode_id.label(
                    "position_episode_id"
                ),
                func.max(ReviewAnnotation.revision).label("revision"),
            )
            .where(
                ReviewAnnotation.account_key == build.account_key,
                ReviewAnnotation.episode_build_id == build.id,
            )
            .group_by(ReviewAnnotation.position_episode_id)
            .subquery()
        )
        latest_review = aliased(ReviewAnnotation, name="latest_review")
        annotated_rows = session.execute(
            select(PositionEpisode, StrategyEpisode, latest_review)
            .join(
                StrategyEpisode,
                StrategyEpisode.id == PositionEpisode.strategy_episode_id,
            )
            .join(
                latest_revisions,
                latest_revisions.c.position_episode_id == PositionEpisode.id,
            )
            .join(
                latest_review,
                and_(
                    latest_review.account_key == build.account_key,
                    latest_review.episode_build_id == build.id,
                    latest_review.position_episode_id == PositionEpisode.id,
                    latest_review.revision == latest_revisions.c.revision,
                ),
            )
            .where(
                PositionEpisode.episode_build_id == build.id,
                PositionEpisode.account_key == build.account_key,
            )
            .order_by(PositionEpisode.id)
        ).all()

        open_times = session.execute(
            select(PositionEpisode.id, PositionEpisode.opened_at).where(
                PositionEpisode.episode_build_id == build.id,
                PositionEpisode.account_key == build.account_key,
            )
        ).all()

        build_id = int(build.id)
        build_key = str(build.build_key)
        source_kind = _build_source_kind(session, build_id)
        resolved_account_key = str(build.account_key)

        # Project everything while the session is open: the ORM rows expire
        # once the read transaction ends.
        buckets: dict[tuple[str, str, str, str], _BucketState] = {}
        annotated_ids: set[int] = set()
        for position, strategy, review in annotated_rows:
            item = _position_item(position, strategy, review)
            annotated_ids.add(item.episode_id)
            day = _trading_day(item.opened_at)
            direction = str(item.direction).upper()
            boundary_policy = (
                BOUNDARY_POLICY_VERIFIED
                if item.left_boundary_verified and not item.is_left_censored
                else BOUNDARY_POLICY_ASSUMED_OR_CENSORED
            )
            verified = not position_episode_pnl_exclusion_reasons(item)
            completed = item.review_status == "completed"
            labels = [
                ("tag", value)
                for value in _parse_labels(str(review.tags_json))
            ] + [
                ("error_type", value)
                for value in _parse_labels(str(review.error_types_json))
            ]
            for group_kind, group_value in labels:
                key = (group_kind, group_value, direction, boundary_policy)
                state = buckets.setdefault(key, _BucketState())
                if item.episode_id in state.member_ids:
                    continue
                state.member_ids.add(item.episode_id)
                state.all_days.add(day)
                if completed:
                    state.completed_count += 1
                if verified:
                    state.verified_days.add(day)
                    # Verified members always have a known realized net P&L.
                    state.verified_pnl.append(Decimal(item.realized_pnl_net))
                else:
                    state.conditional_count += 1

        unreviewed_days: set[date] = set()
        unreviewed_count = 0
        for episode_id, opened_at in open_times:
            if int(episode_id) in annotated_ids:
                continue
            unreviewed_count += 1
            unreviewed_days.add(_trading_day(opened_at))

    ordered = sorted(
        buckets.items(),
        key=lambda entry: (-len(entry[1].member_ids), entry[0]),
    )
    return ReviewInsightsResult(
        build_id=build_id,
        build_key=build_key,
        source_kind=source_kind,
        account_key=resolved_account_key,
        generated_at=datetime.now(timezone.utc),
        min_episode_count=min_episode_count,
        min_distinct_trading_day_count=min_distinct_trading_day_count,
        total_episode_count=len(open_times),
        annotated_episode_count=len(annotated_ids),
        unreviewed=ReviewInsightsUnreviewed(
            episode_count=unreviewed_count,
            distinct_trading_day_count=len(unreviewed_days),
        ),
        buckets=tuple(
            _bucket(
                key,
                state,
                min_episode_count=min_episode_count,
                min_distinct_trading_day_count=min_distinct_trading_day_count,
            )
            for key, state in ordered
        ),
    )
