# -*- coding: utf-8 -*-
"""Append-only, user-authored reviews for immutable PositionEpisodes.

This repository is deliberately separate from AI review generation.  Only an
explicit caller-supplied review payload can create a revision; broker evidence,
Episode economics, and model output are never modified or copied implicitly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select

from src.journal.ledger.models import (
    EpisodeBuild,
    PositionEpisode,
    ReviewAnnotation,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.storage import get_db

__all__ = [
    "ReviewAnnotationAppendResult",
    "ReviewAnnotationInput",
    "ReviewAnnotationRepositoryError",
    "ReviewAnnotationScopeNotFoundError",
    "StoredReviewAnnotation",
    "append_review_annotation",
    "get_latest_review_annotation",
    "list_review_annotation_history",
    "normalize_review_annotation_input",
]


_SCHEMA_VERSION = "review-annotation/1.0"
_TEXT_FIELDS = (
    "setup_thesis",
    "entry_trigger",
    "invalidation_plan",
    "position_rationale",
    "exit_reason",
    "post_trade_reflection",
)
_VALID_REVIEW_STATUSES = {"in_progress", "completed"}
_MAX_FIELD_CHARACTERS = 2_000
_MAX_TOTAL_CHARACTERS = 6_000
_MAX_LABEL_ITEMS = 20
_MAX_LABEL_CHARACTERS = 64


class ReviewAnnotationRepositoryError(ValueError):
    """Raised when a review payload violates the persistence contract."""


class ReviewAnnotationScopeNotFoundError(ReviewAnnotationRepositoryError):
    """Raised when build, Episode, and account do not identify one scope."""


@dataclass(frozen=True)
class ReviewAnnotationInput:
    """Normalized user-authored content for one new review revision."""

    review_status: str
    setup_thesis: str = ""
    entry_trigger: str = ""
    invalidation_plan: str = ""
    position_rationale: str = ""
    exit_reason: str = ""
    post_trade_reflection: str = ""
    tags: tuple[str, ...] = ()
    error_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredReviewAnnotation:
    """One immutable stored revision, safe for API projection."""

    annotation_id: int
    account_key: str
    episode_build_id: int
    position_episode_id: int
    revision: int
    review_status: str
    setup_thesis: str
    entry_trigger: str
    invalidation_plan: str
    position_rationale: str
    exit_reason: str
    post_trade_reflection: str
    tags: tuple[str, ...]
    error_types: tuple[str, ...]
    content_sha256: str
    previous_annotation_id: Optional[int]
    created_at: datetime


@dataclass(frozen=True)
class ReviewAnnotationAppendResult:
    """Result of an append attempt, including latest-content idempotency."""

    annotation: StoredReviewAnnotation
    duplicate: bool


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_labels(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ReviewAnnotationRepositoryError(
                f"{field_name} items must be strings"
            )
        value = raw.strip()
        if not value:
            continue
        if len(value) > _MAX_LABEL_CHARACTERS:
            raise ReviewAnnotationRepositoryError(
                f"{field_name} items must be at most "
                f"{_MAX_LABEL_CHARACTERS} characters"
            )
        normalized.add(value)
    if len(normalized) > _MAX_LABEL_ITEMS:
        raise ReviewAnnotationRepositoryError(
            f"{field_name} must contain at most {_MAX_LABEL_ITEMS} items"
        )
    # Labels are sets, not ranked evidence.  Stable sorting makes content
    # idempotent when a client sends the same labels in another order.
    return tuple(sorted(normalized))


def normalize_review_annotation_input(
    value: ReviewAnnotationInput,
) -> ReviewAnnotationInput:
    """Trim, bound, and canonicalize a review before hashing or storage."""

    review_status = str(value.review_status or "").strip().lower()
    if review_status not in _VALID_REVIEW_STATUSES:
        raise ReviewAnnotationRepositoryError(
            "review_status must be in_progress or completed"
        )

    fields: dict[str, str] = {}
    for field_name in _TEXT_FIELDS:
        raw = getattr(value, field_name)
        if not isinstance(raw, str):
            raise ReviewAnnotationRepositoryError(
                f"{field_name} must be a string"
            )
        normalized = raw.strip()
        if len(normalized) > _MAX_FIELD_CHARACTERS:
            raise ReviewAnnotationRepositoryError(
                f"{field_name} must be at most "
                f"{_MAX_FIELD_CHARACTERS} characters"
            )
        fields[field_name] = normalized

    if sum(len(fields[name]) for name in _TEXT_FIELDS) > _MAX_TOTAL_CHARACTERS:
        raise ReviewAnnotationRepositoryError(
            f"review text must be at most {_MAX_TOTAL_CHARACTERS} characters"
        )
    if review_status == "completed" and not any(fields.values()):
        raise ReviewAnnotationRepositoryError(
            "completed review requires at least one non-empty review field"
        )

    return ReviewAnnotationInput(
        review_status=review_status,
        **fields,
        tags=_normalize_labels(value.tags, "tags"),
        error_types=_normalize_labels(value.error_types, "error_types"),
    )


def _content_payload(value: ReviewAnnotationInput) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "review_status": value.review_status,
        **{field_name: getattr(value, field_name) for field_name in _TEXT_FIELDS},
        "tags": list(value.tags),
        "error_types": list(value.error_types),
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _content_sha256(value: ReviewAnnotationInput) -> str:
    return hashlib.sha256(
        _canonical_json(_content_payload(value)).encode("utf-8")
    ).hexdigest()


def _parse_labels(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if isinstance(item, str))


def _stored(row: ReviewAnnotation) -> StoredReviewAnnotation:
    return StoredReviewAnnotation(
        annotation_id=int(row.id),
        account_key=str(row.account_key),
        episode_build_id=int(row.episode_build_id),
        position_episode_id=int(row.position_episode_id),
        revision=int(row.revision),
        review_status=str(row.review_status),
        setup_thesis=str(row.setup_thesis),
        entry_trigger=str(row.entry_trigger),
        invalidation_plan=str(row.invalidation_plan),
        position_rationale=str(row.position_rationale),
        exit_reason=str(row.exit_reason),
        post_trade_reflection=str(row.post_trade_reflection),
        tags=_parse_labels(str(row.tags_json)),
        error_types=_parse_labels(str(row.error_types_json)),
        content_sha256=str(row.content_sha256),
        previous_annotation_id=(
            int(row.previous_annotation_id)
            if row.previous_annotation_id is not None
            else None
        ),
        created_at=_utc(row.created_at),
    )


def _validate_scope(
    session: object,
    *,
    account_key: str,
    episode_build_id: int,
    position_episode_id: int,
) -> None:
    matched = session.execute(
        select(PositionEpisode.id)
        .join(EpisodeBuild, EpisodeBuild.id == PositionEpisode.episode_build_id)
        .where(
            EpisodeBuild.id == episode_build_id,
            EpisodeBuild.account_key == account_key,
            PositionEpisode.id == position_episode_id,
            PositionEpisode.episode_build_id == episode_build_id,
            PositionEpisode.account_key == account_key,
        )
    ).scalar_one_or_none()
    if matched is None:
        raise ReviewAnnotationScopeNotFoundError(
            "position episode not found in the selected build and account"
        )


def _scope_values(
    account_key: str,
    episode_build_id: int,
    position_episode_id: int,
) -> tuple[str, int, int]:
    normalized_account = str(account_key or "").strip()
    if not normalized_account or len(normalized_account) > 64:
        raise ReviewAnnotationRepositoryError(
            "account_key must contain between 1 and 64 characters"
        )
    if episode_build_id <= 0:
        raise ReviewAnnotationRepositoryError(
            "episode_build_id must be positive"
        )
    if position_episode_id <= 0:
        raise ReviewAnnotationRepositoryError(
            "position_episode_id must be positive"
        )
    return normalized_account, episode_build_id, position_episode_id


def append_review_annotation(
    value: ReviewAnnotationInput,
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    episode_build_id: int,
    position_episode_id: int,
) -> ReviewAnnotationAppendResult:
    """Append a revision, or return the latest identical revision."""

    account_key, episode_build_id, position_episode_id = _scope_values(
        account_key,
        episode_build_id,
        position_episode_id,
    )
    normalized = normalize_review_annotation_input(value)
    content_sha256 = _content_sha256(normalized)

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            # Serialize the latest-read and append so two concurrent edits
            # cannot fork one immutable revision.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _validate_scope(
            session,
            account_key=account_key,
            episode_build_id=episode_build_id,
            position_episode_id=position_episode_id,
        )
        latest = session.execute(
            select(ReviewAnnotation)
            .where(
                ReviewAnnotation.account_key == account_key,
                ReviewAnnotation.episode_build_id == episode_build_id,
                ReviewAnnotation.position_episode_id == position_episode_id,
            )
            .order_by(
                ReviewAnnotation.revision.desc(),
                ReviewAnnotation.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None and latest.content_sha256 == content_sha256:
            return ReviewAnnotationAppendResult(
                annotation=_stored(latest),
                duplicate=True,
            )

        row = ReviewAnnotation(
            account_key=account_key,
            episode_build_id=episode_build_id,
            position_episode_id=position_episode_id,
            revision=1 if latest is None else int(latest.revision) + 1,
            review_status=normalized.review_status,
            **{
                field_name: getattr(normalized, field_name)
                for field_name in _TEXT_FIELDS
            },
            tags_json=_canonical_json(list(normalized.tags)),
            error_types_json=_canonical_json(list(normalized.error_types)),
            content_sha256=content_sha256,
            previous_annotation_id=int(latest.id) if latest is not None else None,
        )
        session.add(row)
        session.flush()
        return ReviewAnnotationAppendResult(
            annotation=_stored(row),
            duplicate=False,
        )


def get_latest_review_annotation(
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    episode_build_id: int,
    position_episode_id: int,
) -> Optional[StoredReviewAnnotation]:
    """Return the newest revision, or None when this valid scope has none."""

    account_key, episode_build_id, position_episode_id = _scope_values(
        account_key,
        episode_build_id,
        position_episode_id,
    )
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        _validate_scope(
            session,
            account_key=account_key,
            episode_build_id=episode_build_id,
            position_episode_id=position_episode_id,
        )
        row = session.execute(
            select(ReviewAnnotation)
            .where(
                ReviewAnnotation.account_key == account_key,
                ReviewAnnotation.episode_build_id == episode_build_id,
                ReviewAnnotation.position_episode_id == position_episode_id,
            )
            .order_by(
                ReviewAnnotation.revision.desc(),
                ReviewAnnotation.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        return None if row is None else _stored(row)


def list_review_annotation_history(
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    episode_build_id: int,
    position_episode_id: int,
) -> tuple[StoredReviewAnnotation, ...]:
    """Return every immutable revision, newest first."""

    account_key, episode_build_id, position_episode_id = _scope_values(
        account_key,
        episode_build_id,
        position_episode_id,
    )
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        _validate_scope(
            session,
            account_key=account_key,
            episode_build_id=episode_build_id,
            position_episode_id=position_episode_id,
        )
        rows = session.execute(
            select(ReviewAnnotation)
            .where(
                ReviewAnnotation.account_key == account_key,
                ReviewAnnotation.episode_build_id == episode_build_id,
                ReviewAnnotation.position_episode_id == position_episode_id,
            )
            .order_by(
                ReviewAnnotation.revision.desc(),
                ReviewAnnotation.id.desc(),
            )
        ).scalars()
        return tuple(_stored(row) for row in rows)
