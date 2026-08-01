# -*- coding: utf-8 -*-
"""Append-only persistence for Playbook candidates and rules (slice C-2).

Hard boundaries from ``New-docs/phase1/13_PLAYBOOK_PROMOTION_CONTRACT.md``:

* a candidate is created only by an explicit user request, and a rule only by
  an explicit user promotion — nothing here is ever triggered automatically;
* every write freezes an evidence snapshot by re-running the zero-write C-1
  aggregation for the referenced bucket inside the same session; when the
  bucket (or the default build) no longer exists the write fails closed with
  a conflict instead of fabricating evidence;
* rules form an append-only version chain per ``lineage_key``; retirement
  appends a new ``retired`` version and never updates or deletes a row;
* rules never feed back into any scoring weight, ranking, or AI prompt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.journal.ledger.playbook_models import PlaybookCandidate, PlaybookRule
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.ledger.review_insights import (
    BOUNDARY_POLICY_ASSUMED_OR_CENSORED,
    BOUNDARY_POLICY_VERIFIED,
    DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT,
    DEFAULT_MIN_EPISODE_COUNT,
    ReviewInsightBucketEvidence,
    collect_review_insight_bucket_evidence,
)
from src.storage import get_db

__all__ = [
    "PLAYBOOK_EVIDENCE_SNAPSHOT_SCHEMA_VERSION",
    "PlaybookCandidateCreateResult",
    "PlaybookConflictError",
    "PlaybookRepositoryError",
    "PlaybookRulePromotionResult",
    "PlaybookRuleRetireResult",
    "PlaybookSourceBucket",
    "StoredPlaybookCandidate",
    "StoredPlaybookRule",
    "create_playbook_candidate",
    "list_playbook_candidates",
    "list_playbook_rules",
    "promote_candidate_to_rule",
    "retire_playbook_rule",
]


PLAYBOOK_EVIDENCE_SNAPSHOT_SCHEMA_VERSION = "playbook-evidence-snapshot/1.0"
_CANDIDATE_KEY_SCHEMA = "playbook-candidate-key/1.0"
_LINEAGE_KEY_SCHEMA = "playbook-rule-lineage-key/1.0"
_RULE_KEY_SCHEMA = "playbook-rule-key/1.0"

_MAX_TITLE_CHARACTERS = 120
_MAX_RULE_TEXT_CHARACTERS = 2_000
_MAX_EVIDENCE_EPISODE_IDS = 200
_VALID_GROUP_KINDS = {"tag", "error_type"}
_VALID_DIRECTIONS = {"LONG", "SHORT"}
_VALID_BOUNDARY_POLICIES = {
    BOUNDARY_POLICY_VERIFIED,
    BOUNDARY_POLICY_ASSUMED_OR_CENSORED,
}
_MAX_GROUP_VALUE_CHARACTERS = 64

RULE_STATUS_ACTIVE = "active"
RULE_STATUS_RETIRED = "retired"


class PlaybookRepositoryError(ValueError):
    """Raised when a playbook payload violates the persistence contract."""


class PlaybookConflictError(PlaybookRepositoryError):
    """Raised when a CAS expectation is stale or evidence no longer exists."""


@dataclass(frozen=True)
class PlaybookSourceBucket:
    """Echo of the insights bucket a candidate was saved from."""

    group_kind: str
    group_value: str
    direction: str
    boundary_policy: str


@dataclass(frozen=True)
class StoredPlaybookCandidate:
    """One immutable candidate row, safe for API projection."""

    candidate_id: int
    candidate_key: str
    account_key: str
    title: str
    rule_text: str
    source_bucket: Optional[PlaybookSourceBucket]
    evidence_snapshot: dict[str, Any]
    evidence_snapshot_sha256: str
    promoted: bool
    created_at: datetime


@dataclass(frozen=True)
class StoredPlaybookRule:
    """One immutable rule version row, safe for API projection."""

    rule_id: int
    rule_key: str
    lineage_key: str
    account_key: str
    version: int
    status: str
    promoted_from_candidate_id: int
    promoted_from_candidate_key: str
    previous_rule_id: Optional[int]
    title: str
    rule_text: str
    evidence_snapshot: dict[str, Any]
    evidence_snapshot_sha256: str
    is_latest_version: bool
    created_at: datetime


@dataclass(frozen=True)
class PlaybookCandidateCreateResult:
    candidate: StoredPlaybookCandidate
    duplicate: bool


@dataclass(frozen=True)
class PlaybookRulePromotionResult:
    rule: StoredPlaybookRule
    duplicate: bool


@dataclass(frozen=True)
class PlaybookRuleRetireResult:
    rule: StoredPlaybookRule
    duplicate: bool


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalized_account_key(account_key: str) -> str:
    normalized = str(account_key or "").strip()
    if not normalized or len(normalized) > 64:
        raise PlaybookRepositoryError(
            "account_key must contain between 1 and 64 characters"
        )
    return normalized


def _normalized_text(value: Any, field_name: str, max_characters: int) -> str:
    if not isinstance(value, str):
        raise PlaybookRepositoryError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise PlaybookRepositoryError(f"{field_name} cannot be empty")
    if len(normalized) > max_characters:
        raise PlaybookRepositoryError(
            f"{field_name} must be at most {max_characters} characters"
        )
    return normalized


def _normalized_source_bucket(
    value: Optional[PlaybookSourceBucket],
) -> Optional[PlaybookSourceBucket]:
    if value is None:
        return None
    group_kind = str(value.group_kind or "").strip()
    if group_kind not in _VALID_GROUP_KINDS:
        raise PlaybookRepositoryError(
            "source bucket group_kind must be tag or error_type"
        )
    group_value = _normalized_text(
        value.group_value, "source bucket group_value", _MAX_GROUP_VALUE_CHARACTERS
    )
    direction = str(value.direction or "").strip().upper()
    if direction not in _VALID_DIRECTIONS:
        raise PlaybookRepositoryError(
            "source bucket direction must be LONG or SHORT"
        )
    boundary_policy = str(value.boundary_policy or "").strip()
    if boundary_policy not in _VALID_BOUNDARY_POLICIES:
        raise PlaybookRepositoryError(
            "source bucket boundary_policy must be verified or "
            "assumed_or_censored"
        )
    return PlaybookSourceBucket(
        group_kind=group_kind,
        group_value=group_value,
        direction=direction,
        boundary_policy=boundary_policy,
    )


def _source_bucket_payload(
    bucket: Optional[PlaybookSourceBucket],
) -> Optional[dict[str, str]]:
    if bucket is None:
        return None
    return {
        "group_kind": bucket.group_kind,
        "group_value": bucket.group_value,
        "direction": bucket.direction,
        "boundary_policy": bucket.boundary_policy,
    }


def _validated_key(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise PlaybookRepositoryError(f"{field_name} must be SHA-256")
    return normalized


def _candidate_key(
    account_key: str,
    title: str,
    rule_text: str,
    bucket: Optional[PlaybookSourceBucket],
) -> str:
    return _sha256_json(
        {
            "schema_version": _CANDIDATE_KEY_SCHEMA,
            "account_key": account_key,
            "title": title,
            "rule_text": rule_text,
            "source_bucket": _source_bucket_payload(bucket),
        }
    )


def _lineage_key(account_key: str, candidate_key: str) -> str:
    return _sha256_json(
        {
            "schema_version": _LINEAGE_KEY_SCHEMA,
            "account_key": account_key,
            "candidate_key": candidate_key,
        }
    )


def _rule_key(lineage_key: str, version: int, status: str) -> str:
    return _sha256_json(
        {
            "schema_version": _RULE_KEY_SCHEMA,
            "lineage_key": lineage_key,
            "version": version,
            "status": status,
        }
    )


def _frozen_bucket_snapshot(
    evidence: ReviewInsightBucketEvidence,
    *,
    captured_for: str,
) -> dict[str, Any]:
    """Project one aggregation result into an immutable snapshot payload."""
    return {
        "schema_version": PLAYBOOK_EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_kind": "insights_bucket",
        "captured_for": captured_for,
        "build_id": evidence.build_id,
        "build_key": evidence.build_key,
        "source_kind": evidence.source_kind,
        "account_key": evidence.account_key,
        "bucket": {
            "group_kind": evidence.group_kind,
            "group_value": evidence.group_value,
            "direction": evidence.direction,
            "boundary_policy": evidence.boundary_policy,
        },
        "counts": {
            "episode_count": evidence.episode_count,
            "distinct_trading_day_count": (
                evidence.distinct_trading_day_count
            ),
            "review_completed_count": evidence.review_completed_count,
            "verified_episode_count": evidence.verified_episode_count,
            "verified_distinct_trading_day_count": (
                evidence.verified_distinct_trading_day_count
            ),
            "conditional_episode_count": evidence.conditional_episode_count,
        },
        "episode_ids": list(evidence.episode_ids),
        "episode_id_sample_truncated": evidence.episode_id_sample_truncated,
        "annotation_revisions": [
            {
                "position_episode_id": ref.position_episode_id,
                "annotation_id": ref.annotation_id,
                "revision": ref.revision,
            }
            for ref in evidence.annotation_refs
        ],
        "thresholds": {
            "min_episode_count": DEFAULT_MIN_EPISODE_COUNT,
            "min_distinct_trading_day_count": (
                DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _free_form_snapshot(*, captured_for: str) -> dict[str, Any]:
    """A free-form candidate binds no bucket; nothing is fabricated."""
    return {
        "schema_version": PLAYBOOK_EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_kind": "free_form",
        "captured_for": captured_for,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _freeze_snapshot(
    session: Any,
    *,
    account_key: str,
    bucket: Optional[PlaybookSourceBucket],
    captured_for: str,
) -> dict[str, Any]:
    """Freeze evidence in this session, or fail closed with a conflict."""
    if bucket is None:
        return _free_form_snapshot(captured_for=captured_for)
    evidence = collect_review_insight_bucket_evidence(
        session,
        account_key=account_key,
        group_kind=bucket.group_kind,
        group_value=bucket.group_value,
        direction=bucket.direction,
        boundary_policy=bucket.boundary_policy,
        max_episode_ids=_MAX_EVIDENCE_EPISODE_IDS,
    )
    if evidence is None:
        raise PlaybookConflictError(
            "the referenced insights bucket does not exist in the current "
            "default build; refresh the pattern observation view and retry"
        )
    return _frozen_bucket_snapshot(evidence, captured_for=captured_for)


def _parse_snapshot(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_source_bucket(
    value: Optional[str],
) -> Optional[PlaybookSourceBucket]:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return PlaybookSourceBucket(
        group_kind=str(parsed.get("group_kind", "")),
        group_value=str(parsed.get("group_value", "")),
        direction=str(parsed.get("direction", "")),
        boundary_policy=str(parsed.get("boundary_policy", "")),
    )


def _stored_candidate(
    row: PlaybookCandidate,
    *,
    promoted: bool,
) -> StoredPlaybookCandidate:
    return StoredPlaybookCandidate(
        candidate_id=int(row.id),
        candidate_key=str(row.candidate_key),
        account_key=str(row.account_key),
        title=str(row.title),
        rule_text=str(row.rule_text),
        source_bucket=_parse_source_bucket(
            None if row.source_bucket_json is None
            else str(row.source_bucket_json)
        ),
        evidence_snapshot=_parse_snapshot(str(row.evidence_snapshot_json)),
        evidence_snapshot_sha256=str(row.evidence_snapshot_sha256),
        promoted=promoted,
        created_at=_utc(row.created_at),
    )


def _stored_rule(
    row: PlaybookRule,
    *,
    promoted_from_candidate_key: str,
    is_latest_version: bool,
) -> StoredPlaybookRule:
    return StoredPlaybookRule(
        rule_id=int(row.id),
        rule_key=str(row.rule_key),
        lineage_key=str(row.lineage_key),
        account_key=str(row.account_key),
        version=int(row.version),
        status=str(row.status),
        promoted_from_candidate_id=int(row.promoted_from_candidate_id),
        promoted_from_candidate_key=promoted_from_candidate_key,
        previous_rule_id=(
            int(row.previous_rule_id)
            if row.previous_rule_id is not None
            else None
        ),
        title=str(row.title),
        rule_text=str(row.rule_text),
        evidence_snapshot=_parse_snapshot(str(row.evidence_snapshot_json)),
        evidence_snapshot_sha256=str(row.evidence_snapshot_sha256),
        is_latest_version=is_latest_version,
        created_at=_utc(row.created_at),
    )


def _candidate_has_rule(session: Any, candidate_id: int) -> bool:
    return (
        session.execute(
            select(PlaybookRule.id)
            .where(PlaybookRule.promoted_from_candidate_id == candidate_id)
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _latest_rule_version(
    session: Any,
    account_key: str,
    lineage_key: str,
) -> Optional[PlaybookRule]:
    return session.execute(
        select(PlaybookRule)
        .where(
            PlaybookRule.account_key == account_key,
            PlaybookRule.lineage_key == lineage_key,
        )
        .order_by(PlaybookRule.version.desc(), PlaybookRule.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _candidate_by_key(
    session: Any,
    account_key: str,
    candidate_key: str,
) -> Optional[PlaybookCandidate]:
    return session.execute(
        select(PlaybookCandidate).where(
            PlaybookCandidate.account_key == account_key,
            PlaybookCandidate.candidate_key == candidate_key,
        )
    ).scalar_one_or_none()


def create_playbook_candidate(
    *,
    title: str,
    rule_text: str,
    source_bucket: Optional[PlaybookSourceBucket] = None,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> PlaybookCandidateCreateResult:
    """Append one explicit user candidate with a frozen evidence snapshot.

    Replaying the same content is idempotent and keeps the original frozen
    snapshot; the snapshot is never re-frozen for a duplicate.
    """
    account_key = _normalized_account_key(account_key)
    title = _normalized_text(title, "title", _MAX_TITLE_CHARACTERS)
    rule_text = _normalized_text(
        rule_text, "rule_text", _MAX_RULE_TEXT_CHARACTERS
    )
    bucket = _normalized_source_bucket(source_bucket)
    candidate_key = _candidate_key(account_key, title, rule_text, bucket)

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            # Serialize the duplicate check and append so two identical
            # explicit submissions cannot both insert.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        existing = _candidate_by_key(session, account_key, candidate_key)
        if existing is not None:
            return PlaybookCandidateCreateResult(
                candidate=_stored_candidate(
                    existing,
                    promoted=_candidate_has_rule(session, int(existing.id)),
                ),
                duplicate=True,
            )
        snapshot = _freeze_snapshot(
            session,
            account_key=account_key,
            bucket=bucket,
            captured_for="candidate_creation",
        )
        bucket_payload = _source_bucket_payload(bucket)
        row = PlaybookCandidate(
            candidate_key=candidate_key,
            account_key=account_key,
            title=title,
            rule_text=rule_text,
            source_bucket_json=(
                None
                if bucket_payload is None
                else _canonical_json(bucket_payload)
            ),
            evidence_snapshot_json=_canonical_json(snapshot),
            evidence_snapshot_sha256=_sha256_json(snapshot),
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise PlaybookConflictError(
                "candidate state changed; refresh and retry"
            ) from exc
        return PlaybookCandidateCreateResult(
            candidate=_stored_candidate(row, promoted=False),
            duplicate=False,
        )


def promote_candidate_to_rule(
    *,
    candidate_key: str,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    allow_new_version: bool = False,
    expected_current_version: Optional[int] = None,
) -> PlaybookRulePromotionResult:
    """Append one explicit user promotion, re-freezing evidence at this time.

    Version 1 requires that the lineage has no rows yet.  When the lineage's
    latest version is active, an identical replay is idempotent.  When it is
    retired, appending a new active version requires the explicit
    ``allow_new_version`` intent plus a matching ``expected_current_version``.
    """
    account_key = _normalized_account_key(account_key)
    candidate_key = _validated_key(candidate_key, "candidate_key")
    if expected_current_version is not None and (
        isinstance(expected_current_version, bool)
        or not isinstance(expected_current_version, int)
        or expected_current_version < 1
    ):
        raise PlaybookRepositoryError(
            "expected_current_version must be a positive integer or null"
        )

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        candidate = _candidate_by_key(session, account_key, candidate_key)
        if candidate is None:
            raise PlaybookConflictError(
                "playbook candidate does not exist for this account; "
                "refresh and retry"
            )
        lineage_key = _lineage_key(account_key, candidate_key)
        latest = _latest_rule_version(session, account_key, lineage_key)

        if latest is not None and str(latest.status) == RULE_STATUS_ACTIVE:
            # The active rule of this lineage was promoted from this same
            # immutable candidate: re-promotion is an idempotent replay.
            return PlaybookRulePromotionResult(
                rule=_stored_rule(
                    latest,
                    promoted_from_candidate_key=candidate_key,
                    is_latest_version=True,
                ),
                duplicate=True,
            )
        if latest is None:
            if expected_current_version is not None:
                raise PlaybookConflictError(
                    "promotion expected an existing rule version but the "
                    "lineage has none; refresh and retry"
                )
            version = 1
            previous_rule_id = None
        else:
            # Latest version is retired: reactivation appends a new version.
            if not allow_new_version:
                raise PlaybookConflictError(
                    "this rule lineage is retired; promoting again requires "
                    "explicit new-version intent"
                )
            if expected_current_version != int(latest.version):
                raise PlaybookConflictError(
                    "rule version changed; refresh and retry with the "
                    "current version"
                )
            version = int(latest.version) + 1
            previous_rule_id = int(latest.id)

        snapshot = _freeze_snapshot(
            session,
            account_key=account_key,
            bucket=_normalized_source_bucket(
                _parse_source_bucket(
                    None
                    if candidate.source_bucket_json is None
                    else str(candidate.source_bucket_json)
                )
            ),
            captured_for="rule_promotion",
        )
        row = PlaybookRule(
            rule_key=_rule_key(lineage_key, version, RULE_STATUS_ACTIVE),
            lineage_key=lineage_key,
            account_key=account_key,
            version=version,
            status=RULE_STATUS_ACTIVE,
            promoted_from_candidate_id=int(candidate.id),
            previous_rule_id=previous_rule_id,
            title=str(candidate.title),
            rule_text=str(candidate.rule_text),
            evidence_snapshot_json=_canonical_json(snapshot),
            evidence_snapshot_sha256=_sha256_json(snapshot),
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise PlaybookConflictError(
                "rule state changed; refresh and retry"
            ) from exc
        return PlaybookRulePromotionResult(
            rule=_stored_rule(
                row,
                promoted_from_candidate_key=candidate_key,
                is_latest_version=True,
            ),
            duplicate=False,
        )


def retire_playbook_rule(
    *,
    lineage_key: str,
    expected_current_version: int,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> PlaybookRuleRetireResult:
    """Append a ``retired`` version row; the promoted snapshot is copied.

    ``expected_current_version`` must name the active version being retired.
    An exact replay after a successful retirement is idempotent; any other
    stale expectation is rejected with zero writes.
    """
    account_key = _normalized_account_key(account_key)
    lineage_key = _validated_key(lineage_key, "lineage_key")
    if (
        isinstance(expected_current_version, bool)
        or not isinstance(expected_current_version, int)
        or expected_current_version < 1
    ):
        raise PlaybookRepositoryError(
            "expected_current_version must be a positive integer"
        )

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        latest = _latest_rule_version(session, account_key, lineage_key)
        if latest is None:
            raise PlaybookConflictError(
                "playbook rule lineage does not exist for this account"
            )
        candidate = session.get(
            PlaybookCandidate, int(latest.promoted_from_candidate_id)
        )
        candidate_key = (
            str(candidate.candidate_key) if candidate is not None else ""
        )
        if str(latest.status) == RULE_STATUS_RETIRED:
            if int(latest.version) == expected_current_version + 1:
                # Exact replay of the retirement that already happened.
                return PlaybookRuleRetireResult(
                    rule=_stored_rule(
                        latest,
                        promoted_from_candidate_key=candidate_key,
                        is_latest_version=True,
                    ),
                    duplicate=True,
                )
            raise PlaybookConflictError(
                "rule version changed; refresh and retry with the current "
                "version"
            )
        if int(latest.version) != expected_current_version:
            raise PlaybookConflictError(
                "rule version changed; refresh and retry with the current "
                "version"
            )
        version = int(latest.version) + 1
        row = PlaybookRule(
            rule_key=_rule_key(lineage_key, version, RULE_STATUS_RETIRED),
            lineage_key=lineage_key,
            account_key=account_key,
            version=version,
            status=RULE_STATUS_RETIRED,
            promoted_from_candidate_id=int(latest.promoted_from_candidate_id),
            previous_rule_id=int(latest.id),
            title=str(latest.title),
            rule_text=str(latest.rule_text),
            # Retirement freezes nothing new: the promoted evidence snapshot
            # is copied verbatim so the rule's basis stays replayable.
            evidence_snapshot_json=str(latest.evidence_snapshot_json),
            evidence_snapshot_sha256=str(latest.evidence_snapshot_sha256),
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise PlaybookConflictError(
                "rule state changed; refresh and retry"
            ) from exc
        return PlaybookRuleRetireResult(
            rule=_stored_rule(
                row,
                promoted_from_candidate_key=candidate_key,
                is_latest_version=True,
            ),
            duplicate=False,
        )


def list_playbook_candidates(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> tuple[StoredPlaybookCandidate, ...]:
    """Return every immutable candidate, newest first."""
    account_key = _normalized_account_key(account_key)
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(PlaybookCandidate)
            .where(PlaybookCandidate.account_key == account_key)
            .order_by(
                PlaybookCandidate.created_at.desc(),
                PlaybookCandidate.id.desc(),
            )
        ).scalars().all()
        promoted_ids = {
            int(candidate_id)
            for (candidate_id,) in session.execute(
                select(PlaybookRule.promoted_from_candidate_id).where(
                    PlaybookRule.account_key == account_key
                )
            ).all()
        }
        return tuple(
            _stored_candidate(row, promoted=int(row.id) in promoted_ids)
            for row in rows
        )


def list_playbook_rules(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> tuple[StoredPlaybookRule, ...]:
    """Return every immutable rule version, newest first."""
    account_key = _normalized_account_key(account_key)
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(PlaybookRule, PlaybookCandidate.candidate_key)
            .join(
                PlaybookCandidate,
                PlaybookCandidate.id == PlaybookRule.promoted_from_candidate_id,
            )
            .where(PlaybookRule.account_key == account_key)
            .order_by(
                PlaybookRule.created_at.desc(),
                PlaybookRule.id.desc(),
            )
        ).all()
        latest_versions: dict[str, int] = {}
        for row, _candidate_key_value in rows:
            lineage = str(row.lineage_key)
            latest_versions[lineage] = max(
                latest_versions.get(lineage, 0), int(row.version)
            )
        return tuple(
            _stored_rule(
                row,
                promoted_from_candidate_key=str(candidate_key_value),
                is_latest_version=(
                    int(row.version)
                    == latest_versions[str(row.lineage_key)]
                ),
            )
            for row, candidate_key_value in rows
        )
