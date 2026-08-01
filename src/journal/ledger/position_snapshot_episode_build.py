# -*- coding: utf-8 -*-
"""Formal, append-only future PositionEpisode build behind a verified fence.

The confirm path re-runs the exact fenced preview core inside one
``BEGIN IMMEDIATE`` write transaction, CAS-compares the caller's frozen
identity (fence key, planned build key, evidence-set hash), enforces the
explicit acceptance gates, and then appends one immutable EpisodeBuild plus
one snapshot-fence source link.  It never activates a build: the default
Episode view is unchanged until a separate, explicit activation slice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Mapping, Optional

from sqlalchemy import select

from src.journal.ledger.episode_repository import (
    EpisodeBuildAppendResult,
    EpisodeBuildPreview,
    EpisodeRepositoryError,
    SNAPSHOT_FENCE_SOURCE_KIND,
    _AMOUNT_HALF_UNIT_TOLERANCE,
    _PreparedBuild,
    _SCORE_QUANTUM,
    _append_prepared_build,
    _headline_metrics,
    _require_group_fee_scope_acceptance,
    _sha256_json,
)
from src.journal.ledger.episodes import BUILDER_NAME, BUILDER_VERSION
from src.journal.ledger.models import EpisodeBuildSnapshotFenceSource
from src.journal.ledger.position_snapshot_episode_preview import (
    FuturePositionEpisodeBuildPreview,
    _compute_fenced_position_episode_preview,
    _FencedPreviewComputation,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.storage import get_db

__all__ = [
    "FutureEpisodeBuildAppendResult",
    "append_future_position_episode_build",
]


_PLAN_CHANGED_MESSAGE = "future build plan changed; request a new preview"


@dataclass(frozen=True)
class FutureEpisodeBuildAppendResult:
    """Idempotent append result plus the immutable snapshot-fence identity."""

    build_id: int
    build_key: str
    duplicate: bool
    status: str
    strategy_episode_count: int
    position_episode_count: int
    evidence_allocation_count: int
    opening_boundary_policy: str
    snapshot_id: int
    snapshot_key: str
    fence_key: str
    continuity_policy_version: str
    target_publication_id: int
    target_publication_key: str
    target_canonical_set_id: int
    target_canonical_set_sha256: str
    boundary_at: datetime
    source_cutoff_at: datetime
    projection_name: str
    projection_version: str
    link_id: int
    link_key: str
    activation_changed: bool = False
    trading_action_performed: bool = False


def _require_hash(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise EpisodeRepositoryError(f"{field_name} must be a SHA-256")
    return normalized


def _utc(value: Optional[datetime], *, field_name: str) -> datetime:
    if value is None:
        raise EpisodeRepositoryError(f"{field_name} is required")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _future_partial_reasons(
    preview: FuturePositionEpisodeBuildPreview,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if any(
        episode.completeness_status not in {"exact", "complete"}
        for episode in preview.episodes
    ):
        reasons.append("episode_quality_partial")
    if not preview.episodes:
        reasons.append("no_execution_episodes")
    for warning in preview.warnings:
        if warning in {
            "opening_positions_remain_left_censored",
            "execution_group_fee_retained_unallocated",
        }:
            reasons.append(warning)
    return tuple(dict.fromkeys(reasons))


def _future_completeness_score(
    preview: FuturePositionEpisodeBuildPreview,
) -> Decimal:
    if not preview.episodes:
        return Decimal("0")
    total = sum(
        (episode.completeness_score for episode in preview.episodes),
        Decimal("0"),
    )
    return (total / Decimal(len(preview.episodes))).quantize(
        _SCORE_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )


def _prepared_future_build(
    computation: _FencedPreviewComputation,
) -> _PreparedBuild:
    """Project the fenced preview into the shared append template."""
    preview = computation.preview
    counts = preview.counts
    if not preview.publication_source_batch_ids or any(
        batch_id <= 0 for batch_id in preview.publication_source_batch_ids
    ):
        raise EpisodeRepositoryError(
            "future build has no publication source batches"
        )
    partial_reasons = _future_partial_reasons(preview)
    headline = _headline_metrics(
        preview.episodes,
        group_fee_affected_episode_keys=(
            computation.group_fee_affected_episode_keys
        ),
    )
    mapped = EpisodeBuildPreview(
        # The newest publication batch stands in for the batch-shaped fields
        # of the shared preview record; snapshot-fence provenance below is
        # the authoritative source identity for this kind.
        batch_id=max(preview.publication_source_batch_ids),
        batch_key=preview.target_publication_key,
        account_key=preview.account_key,
        parser_name=preview.projection_name,
        parser_version=preview.projection_version,
        source_kind=SNAPSHOT_FENCE_SOURCE_KIND,
        source_batch_ids=preview.publication_source_batch_ids,
        canonical_set_id=preview.target_canonical_set_id,
        canonical_set_sha256=preview.target_canonical_set_sha256,
        builder_name=BUILDER_NAME,
        builder_version=BUILDER_VERSION,
        build_key=preview.planned_build_key,
        builder_config_sha256=preview.builder_config_sha256,
        evidence_set_sha256=preview.evidence_set_sha256,
        multiplier_amount_tolerance=_AMOUNT_HALF_UNIT_TOLERANCE,
        max_multiplier_proof_residual=(
            preview.max_multiplier_proof_residual
        ),
        status="partial" if partial_reasons else "succeeded",
        reconciliation_status="not_run",
        reconciliation_scope="not_run",
        reconciliation_window_start=None,
        reconciliation_window_end=None,
        reconciled_order_count=0,
        total_order_count=0,
        completeness_score=_future_completeness_score(preview),
        opening_boundary_policy=preview.opening_boundary_policy,
        partial_reasons=partial_reasons,
        source_window_start=preview.boundary_at,
        source_cutoff_at=preview.source_cutoff_at,
        source_event_count=counts.source_event_count,
        aggregate_order_event_count=counts.post_boundary_order_event_count,
        detailed_fill_event_count=counts.post_boundary_fill_event_count,
        source_known_fee_total=preview.source_known_fee_total,
        allocated_known_fee_total=preview.accounted_known_fee_total,
        retained_execution_group_fee_total=(
            preview.retained_execution_group_fee_total
        ),
        fee_conservation_by_currency=preview.fee_conservation_by_currency,
        fee_conserved=preview.fee_conserved,
        execution_group_count=counts.execution_group_count,
        group_fee_affected_episode_count=(
            counts.group_fee_affected_episode_count
        ),
        leg_fee_attribution_complete=counts.execution_group_count == 0,
        # The preview core proves the allocated evidence set equals the
        # windowed source set exactly, so nothing can remain unresolved.
        unresolved_evidence_count=0,
        open_episode_count=headline["open_episode_count"],
        closed_episode_count=headline["closed_episode_count"],
        boundary_unverified_episode_count=headline[
            "boundary_unverified_episode_count"
        ],
        left_censored_episode_count=headline["left_censored_episode_count"],
        incomplete_episode_count=headline["incomplete_episode_count"],
        aggregate_only_episode_count=headline["aggregate_only_episode_count"],
        conditional_closed_episode_count=headline[
            "conditional_closed_episode_count"
        ],
        conditional_realized_pnl_gross=headline[
            "conditional_realized_pnl_gross"
        ],
        conditional_total_fee=headline["conditional_total_fee"],
        conditional_realized_pnl_net=headline["conditional_realized_pnl_net"],
        headline_episode_count=headline["headline_episode_count"],
        headline_realized_pnl_gross=headline["headline_realized_pnl_gross"],
        headline_total_fee=headline["headline_total_fee"],
        headline_realized_pnl_net=headline["headline_realized_pnl_net"],
        headline_excluded_episode_count=headline[
            "headline_excluded_episode_count"
        ],
        headline_exclusion_counts=headline["headline_exclusion_counts"],
        episodes=preview.episodes,
    )
    snapshot_fence_provenance: Mapping[str, Any] = {
        "fence_key": preview.fence_key,
        "continuity_policy_version": preview.continuity_policy_version,
        "snapshot_id": preview.snapshot_id,
        "snapshot_key": preview.snapshot_key,
        "snapshot_member_set_sha256": (
            computation.snapshot_member_set_sha256
        ),
        "snapshot_provenance_sha256": (
            computation.snapshot_provenance_sha256
        ),
        "target_publication_id": preview.target_publication_id,
        "target_publication_key": preview.target_publication_key,
        "boundary_at": preview.boundary_at,
        "source_cutoff_at": preview.source_cutoff_at,
        "projection_name": preview.projection_name,
        "projection_version": preview.projection_version,
    }
    return _PreparedBuild(
        preview=mapped,
        batch_source_sha256=preview.evidence_set_sha256,
        batch_analysis_level="not_applicable",
        source_order_ids=computation.source_order_ids,
        source_fill_ids=computation.source_fill_ids,
        group_fee_affected_episode_keys=(
            computation.group_fee_affected_episode_keys
        ),
        canonical_set_key=computation.target_canonical_set_key,
        canonical_source_cutoff_at=preview.source_cutoff_at,
        snapshot_fence_provenance=snapshot_fence_provenance,
    )


def _fence_link_key(preview: FuturePositionEpisodeBuildPreview) -> str:
    return _sha256_json(
        {
            "episode_build_key": preview.planned_build_key,
            "snapshot_id": preview.snapshot_id,
            "snapshot_key": preview.snapshot_key,
            "fence_key": preview.fence_key,
            "target_canonical_set_id": preview.target_canonical_set_id,
            "target_canonical_set_sha256": (
                preview.target_canonical_set_sha256
            ),
            "projection_name": preview.projection_name,
            "projection_version": preview.projection_version,
        }
    )


def _verify_or_append_fence_link(
    session: Any,
    preview: FuturePositionEpisodeBuildPreview,
    append_result: EpisodeBuildAppendResult,
) -> EpisodeBuildSnapshotFenceSource:
    """Append the link for a new build; verify it verbatim for a duplicate."""
    link_key = _fence_link_key(preview)
    stored = session.execute(
        select(EpisodeBuildSnapshotFenceSource).where(
            EpisodeBuildSnapshotFenceSource.episode_build_id
            == append_result.build_id
        )
    ).scalar_one_or_none()
    if not append_result.duplicate:
        if stored is not None:
            raise EpisodeRepositoryError(
                "new future build unexpectedly already has a fence link"
            )
        stored = EpisodeBuildSnapshotFenceSource(
            link_key=link_key,
            episode_build_id=append_result.build_id,
            snapshot_id=preview.snapshot_id,
            snapshot_key=preview.snapshot_key,
            fence_key=preview.fence_key,
            continuity_policy_version=preview.continuity_policy_version,
            target_publication_id=preview.target_publication_id,
            target_publication_key=preview.target_publication_key,
            target_canonical_set_id=preview.target_canonical_set_id,
            target_canonical_set_sha256=(
                preview.target_canonical_set_sha256
            ),
            source_cutoff_at=preview.source_cutoff_at,
            boundary_at=preview.boundary_at,
            projection_name=preview.projection_name,
            projection_version=preview.projection_version,
        )
        session.add(stored)
        session.flush()
        return stored
    # A duplicate build key alone is not proof of the same source: the stored
    # snapshot-fence binding must match the recomputed identity exactly.
    if (
        stored is None
        or str(stored.link_key) != link_key
        or int(stored.snapshot_id) != preview.snapshot_id
        or str(stored.snapshot_key) != preview.snapshot_key
        or str(stored.fence_key) != preview.fence_key
        or str(stored.continuity_policy_version)
        != preview.continuity_policy_version
        or int(stored.target_publication_id) != preview.target_publication_id
        or str(stored.target_publication_key)
        != preview.target_publication_key
        or int(stored.target_canonical_set_id)
        != preview.target_canonical_set_id
        or str(stored.target_canonical_set_sha256)
        != preview.target_canonical_set_sha256
        or _utc(stored.boundary_at, field_name="link.boundary_at")
        != preview.boundary_at
        or _utc(stored.source_cutoff_at, field_name="link.source_cutoff_at")
        != preview.source_cutoff_at
        or str(stored.projection_name) != preview.projection_name
        or str(stored.projection_version) != preview.projection_version
    ):
        raise EpisodeRepositoryError(
            "existing future build has an invalid snapshot-fence binding"
        )
    return stored


def append_future_position_episode_build(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    expected_fence_key: str,
    expected_build_key: str,
    expected_evidence_set_sha256: str,
    accept_left_censored_openings: bool,
    accept_group_fee_scope: bool = False,
) -> FutureEpisodeBuildAppendResult:
    """Append one explicitly previewed future build; never activate it."""
    normalized_account = str(account_key or "").strip()
    if not normalized_account:
        raise EpisodeRepositoryError("account_key cannot be empty")
    normalized_fence = str(expected_fence_key or "").strip()
    if normalized_fence != normalized_fence.lower():
        raise EpisodeRepositoryError(
            "expected_fence_key must be a lowercase SHA-256"
        )
    normalized_fence = _require_hash(
        normalized_fence,
        field_name="expected_fence_key",
    )
    expected_key = _require_hash(
        expected_build_key,
        field_name="expected_build_key",
    )
    expected_evidence_hash = _require_hash(
        expected_evidence_set_sha256,
        field_name="expected_evidence_set_sha256",
    )

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        connection = session.connection()
        # Full in-transaction re-plan: the same pure preview core recomputes
        # the fence and every derived identity before anything is appended.
        computation = _compute_fenced_position_episode_preview(
            connection,
            session,
            normalized_account,
            normalized_fence,
            require_readonly_session=False,
        )
        preview = computation.preview
        if (
            preview.fence_key != normalized_fence
            or preview.planned_build_key != expected_key
            or preview.evidence_set_sha256 != expected_evidence_hash
        ):
            raise EpisodeRepositoryError(_PLAN_CHANGED_MESSAGE)
        if not preview.fee_conserved:
            raise EpisodeRepositoryError(
                "future build did not conserve known source fees"
            )
        if preview.opening_boundary_policy != "complete_snapshot":
            raise EpisodeRepositoryError(
                "future build lost its complete snapshot boundary"
            )
        if any(
            not episode.left_boundary_verified
            for episode in preview.episodes
        ):
            raise EpisodeRepositoryError(
                "future build contains a boundary-unverified episode"
            )
        if (
            preview.counts.left_censored_episode_count > 0
            and not accept_left_censored_openings
        ):
            raise EpisodeRepositoryError(
                "snapshot-inherited openings are left-censored with no "
                "broker cost basis; explicit acceptance is required"
            )
        prepared = _prepared_future_build(computation)
        _require_group_fee_scope_acceptance(
            prepared.preview,
            accept_group_fee_scope=accept_group_fee_scope,
        )
        append_result = _append_prepared_build(session, prepared)
        link = _verify_or_append_fence_link(session, preview, append_result)
        return FutureEpisodeBuildAppendResult(
            build_id=append_result.build_id,
            build_key=append_result.build_key,
            duplicate=append_result.duplicate,
            status=append_result.status,
            strategy_episode_count=append_result.strategy_episode_count,
            position_episode_count=append_result.position_episode_count,
            evidence_allocation_count=(
                append_result.evidence_allocation_count
            ),
            opening_boundary_policy=append_result.opening_boundary_policy,
            snapshot_id=preview.snapshot_id,
            snapshot_key=preview.snapshot_key,
            fence_key=preview.fence_key,
            continuity_policy_version=preview.continuity_policy_version,
            target_publication_id=preview.target_publication_id,
            target_publication_key=preview.target_publication_key,
            target_canonical_set_id=preview.target_canonical_set_id,
            target_canonical_set_sha256=(
                preview.target_canonical_set_sha256
            ),
            boundary_at=preview.boundary_at,
            source_cutoff_at=preview.source_cutoff_at,
            projection_name=preview.projection_name,
            projection_version=preview.projection_version,
            link_id=int(link.id),
            link_key=str(link.link_key),
        )
