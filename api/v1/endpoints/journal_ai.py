"""Read-only AI review endpoint for one immutable PositionEpisode."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from api.v1.schemas.journal_ai import (
    PositionEpisodeAiReviewRequest,
    PositionEpisodeAiReviewResponse,
)
from src.journal.ledger.episode_repository import (
    get_latest_position_episode_detail,
    get_position_episode_detail,
)
from src.journal.ledger.repository import DEFAULT_LEDGER_ACCOUNT_KEY
from src.services.episode_ai_review_service import generate_episode_ai_review

router = APIRouter()


@router.post(
    "/v2/position-episodes/{episode_id}/ai-review",
    response_model=PositionEpisodeAiReviewResponse,
)
def review_position_episode_with_ai(
    episode_id: int,
    review_request: Optional[PositionEpisodeAiReviewRequest] = Body(None),
    build_id: Optional[int] = Query(None, ge=1),
    enhance_with_model: bool = Query(
        True,
        alias="enhance",
        description=(
            "When false, return the deterministic evidence review without "
            "calling an external model."
        ),
    ),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> PositionEpisodeAiReviewResponse:
    """Generate a non-persisted review without writing Journal or trading state."""
    if build_id is None:
        detail = get_latest_position_episode_detail(
            episode_id=episode_id,
            account_key=account_key,
        )
    else:
        detail = get_position_episode_detail(
            episode_id=episode_id,
            build_id=build_id,
            account_key=account_key,
        )
    if detail is None:
        scope = "selected build" if build_id is not None else "latest build"
        raise HTTPException(
            status_code=404,
            detail=f"position episode not found in {scope}",
        )
    try:
        user_context = None
        if review_request is not None and review_request.user_context is not None:
            user_context = review_request.user_context.model_dump(exclude_none=True) or None
        result = generate_episode_ai_review(
            detail,
            enhance_with_model=enhance_with_model,
            user_context=user_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PositionEpisodeAiReviewResponse(**result)


__all__ = ["router"]
