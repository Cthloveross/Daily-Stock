# -*- coding: utf-8 -*-
"""Explicit, append-only user reviews for immutable PositionEpisodes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.journal_reviews import (
    ReviewAnnotationCreateRequest,
    ReviewAnnotationCreateResponse,
    ReviewAnnotationHistoryResponse,
    ReviewAnnotationItem,
    ReviewAnnotationLatestResponse,
    ReviewInsightBucketModel,
    ReviewInsightStatsGate,
    ReviewInsightStatsModel,
    ReviewInsightThresholds,
    ReviewInsightUnreviewed,
    ReviewInsightsResponse,
)
from src.journal.ledger.episode_repository import EpisodeRepositoryError
from src.journal.ledger.repository import DEFAULT_LEDGER_ACCOUNT_KEY
from src.journal.ledger.review_insights import (
    DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT,
    DEFAULT_MIN_EPISODE_COUNT,
    ReviewInsightBucket,
    get_latest_review_insights,
)
from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    ReviewAnnotationRepositoryError,
    ReviewAnnotationScopeNotFoundError,
    StoredReviewAnnotation,
    append_review_annotation,
    get_latest_review_annotation,
    list_review_annotation_history,
)


router = APIRouter()


def _item(value: StoredReviewAnnotation) -> ReviewAnnotationItem:
    return ReviewAnnotationItem(
        id=value.annotation_id,
        account_key=value.account_key,
        episode_build_id=value.episode_build_id,
        position_episode_id=value.position_episode_id,
        revision=value.revision,
        review_status=value.review_status,
        setup_thesis=value.setup_thesis,
        entry_trigger=value.entry_trigger,
        invalidation_plan=value.invalidation_plan,
        position_rationale=value.position_rationale,
        exit_reason=value.exit_reason,
        post_trade_reflection=value.post_trade_reflection,
        tags=list(value.tags),
        error_types=list(value.error_types),
        content_sha256=value.content_sha256,
        previous_annotation_id=value.previous_annotation_id,
        created_at=value.created_at,
    )


def _scope_error(exc: ReviewAnnotationRepositoryError) -> HTTPException:
    if isinstance(exc, ReviewAnnotationScopeNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _insight_bucket(bucket: ReviewInsightBucket) -> ReviewInsightBucketModel:
    stats = None
    if bucket.stats is not None:
        stats = ReviewInsightStatsModel(
            win_rate=format(bucket.stats.win_rate, "f"),
            avg_pnl=format(bucket.stats.avg_pnl, "f"),
            sum_pnl=format(bucket.stats.sum_pnl, "f"),
            win_count=bucket.stats.win_count,
            loss_count=bucket.stats.loss_count,
            breakeven_count=bucket.stats.breakeven_count,
        )
    return ReviewInsightBucketModel(
        group_kind=bucket.group_kind,
        group_value=bucket.group_value,
        direction=bucket.direction,
        boundary_policy=bucket.boundary_policy,
        episode_count=bucket.episode_count,
        distinct_trading_day_count=bucket.distinct_trading_day_count,
        review_completed_count=bucket.review_completed_count,
        verified_episode_count=bucket.verified_episode_count,
        verified_distinct_trading_day_count=(
            bucket.verified_distinct_trading_day_count
        ),
        conditional_episode_count=bucket.conditional_episode_count,
        stats=stats,
        stats_gate=ReviewInsightStatsGate(
            eligible=bucket.stats_gate_eligible,
            reason=bucket.stats_gate_reason,
        ),
    )


@router.get("/v2/review-insights", response_model=ReviewInsightsResponse)
def get_review_insights(
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewInsightsResponse:
    """Zero-write L1 pattern observation over the current default build.

    Buckets join the default build's episodes with each episode's latest
    review annotation.  Win-rate style ratios fail closed below the sample
    threshold; conditional-P&L members never enter the stats.
    """
    thresholds = ReviewInsightThresholds(
        min_episode_count=DEFAULT_MIN_EPISODE_COUNT,
        min_distinct_trading_day_count=(
            DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT
        ),
    )
    try:
        result = get_latest_review_insights(account_key)
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        return ReviewInsightsResponse(
            data_state="not_built",
            account_key=account_key,
            thresholds=thresholds,
            total_episode_count=0,
            annotated_episode_count=0,
        )
    return ReviewInsightsResponse(
        data_state="ready",
        build_id=result.build_id,
        build_key=result.build_key,
        source_kind=result.source_kind,
        account_key=result.account_key,
        generated_at=result.generated_at,
        thresholds=ReviewInsightThresholds(
            min_episode_count=result.min_episode_count,
            min_distinct_trading_day_count=(
                result.min_distinct_trading_day_count
            ),
        ),
        total_episode_count=result.total_episode_count,
        annotated_episode_count=result.annotated_episode_count,
        unreviewed=ReviewInsightUnreviewed(
            episode_count=result.unreviewed.episode_count,
            distinct_trading_day_count=(
                result.unreviewed.distinct_trading_day_count
            ),
        ),
        buckets=[_insight_bucket(bucket) for bucket in result.buckets],
    )


@router.get(
    "/v2/position-episodes/{episode_id}/review-annotations/latest",
    response_model=ReviewAnnotationLatestResponse,
)
def get_latest_position_episode_review(
    episode_id: int,
    build_id: int = Query(..., ge=1),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewAnnotationLatestResponse:
    """Return the latest revision without treating no review as an error."""

    try:
        annotation = get_latest_review_annotation(
            account_key=account_key,
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )
    except ReviewAnnotationRepositoryError as exc:
        raise _scope_error(exc) from exc
    if annotation is None:
        return ReviewAnnotationLatestResponse(
            data_state="not_started",
            annotation=None,
        )
    return ReviewAnnotationLatestResponse(
        data_state="ready",
        annotation=_item(annotation),
    )


@router.get(
    "/v2/position-episodes/{episode_id}/review-annotations",
    response_model=ReviewAnnotationHistoryResponse,
)
def get_position_episode_review_history(
    episode_id: int,
    build_id: int = Query(..., ge=1),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewAnnotationHistoryResponse:
    """Return immutable revisions newest first."""

    try:
        items = list_review_annotation_history(
            account_key=account_key,
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )
    except ReviewAnnotationRepositoryError as exc:
        raise _scope_error(exc) from exc
    return ReviewAnnotationHistoryResponse(
        data_state="ready" if items else "not_started",
        total=len(items),
        items=[_item(item) for item in items],
    )


@router.post(
    "/v2/position-episodes/{episode_id}/review-annotations",
    response_model=ReviewAnnotationCreateResponse,
)
def append_position_episode_review(
    episode_id: int,
    request: ReviewAnnotationCreateRequest,
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewAnnotationCreateResponse:
    """Append explicit user content; identical latest content is idempotent."""

    try:
        result = append_review_annotation(
            ReviewAnnotationInput(
                review_status=request.review_status,
                setup_thesis=request.setup_thesis,
                entry_trigger=request.entry_trigger,
                invalidation_plan=request.invalidation_plan,
                position_rationale=request.position_rationale,
                exit_reason=request.exit_reason,
                post_trade_reflection=request.post_trade_reflection,
                tags=tuple(request.tags),
                error_types=tuple(request.error_types),
            ),
            account_key=account_key,
            episode_build_id=request.build_id,
            position_episode_id=episode_id,
        )
    except ReviewAnnotationRepositoryError as exc:
        raise _scope_error(exc) from exc
    return ReviewAnnotationCreateResponse(
        created=not result.duplicate,
        idempotent_replay=result.duplicate,
        annotation=_item(result.annotation),
    )
