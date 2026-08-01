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
)
from src.journal.ledger.repository import DEFAULT_LEDGER_ACCOUNT_KEY
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
