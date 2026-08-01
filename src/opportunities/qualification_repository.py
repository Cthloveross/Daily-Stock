# -*- coding: utf-8 -*-
"""Append-only repository and explicit backfill for opportunity qualification."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.opportunities.cycle_models import (
    OpportunityPremarketCycleAttempt,
    OpportunityPremarketCycleEvent,
)
from src.opportunities.cycle_repository import get_latest_cycle_attempt_status
from src.opportunities.models import (
    OpportunitySnapshotCandidate,
    OpportunitySnapshotRun,
)
from src.opportunities.qualification import (
    ANALYSIS_QUALITY_STATES,
    CAUSAL_WINDOW_STATES,
    OBSERVATION_STATES,
    PUBLICATION_CANONICAL,
    PUBLICATION_STATES,
    QUALIFICATION_POLICY_VERSION,
    QUALIFICATION_STATES,
    TRACK_KEYS,
    CanonicalPublicationProof,
    CandidateTrackQualification,
    PublicationVerification,
    SnapshotQualificationBundle,
    classify_snapshot_qualification,
)
from src.opportunities.qualification_models import (
    OpportunityCandidateTrackAssessment,
    OpportunitySnapshotQualificationAssessment,
)
from src.opportunities.repository import (
    StoredSnapshot,
    canonical_json,
    canonical_sha256,
    get_snapshot,
    init_opportunity_schema,
    list_snapshots,
)
from src.storage import Base, DatabaseManager, get_db


_APPEND_ONLY_TABLES = (
    OpportunitySnapshotQualificationAssessment.__tablename__,
    OpportunityCandidateTrackAssessment.__tablename__,
)


class QualificationRepositoryError(ValueError):
    """Base error for invalid qualification persistence requests."""


class QualificationConflictError(QualificationRepositoryError):
    """An immutable policy slot already contains a different assessment."""


class QualificationBackfillStaleError(QualificationRepositoryError):
    """A planned backfill no longer matches the immutable source bundle."""


@dataclass(frozen=True)
class StoredCandidateTrackAssessment:
    id: int
    assessment_key: str
    snapshot_candidate_id: int
    candidate_key: str
    ticker: str
    policy_version: str
    track_key: str
    causal_window_state: str
    observation_state: str
    qualification_state: str
    reason_codes: tuple[str, ...]
    facts_sha256: str
    assessment_sha256: str
    facts: Mapping[str, Any]
    assessed_at: datetime


@dataclass(frozen=True)
class StoredSnapshotQualification:
    id: int
    assessment_key: str
    snapshot_run_id: int
    snapshot_key: str
    policy_version: str
    publication_state: str
    analysis_quality_state: str
    publication_proof: Optional[CanonicalPublicationProof]
    reason_codes: tuple[str, ...]
    facts_sha256: str
    assessment_sha256: str
    facts: Mapping[str, Any]
    assessed_at: datetime
    candidate_tracks: tuple[StoredCandidateTrackAssessment, ...]


@dataclass(frozen=True)
class QualificationAppendResult:
    snapshot_qualification_id: int
    assessment_key: str
    snapshot_key: str
    policy_version: str
    candidate_track_count: int
    duplicate: bool


@dataclass(frozen=True)
class QualificationBackfillPlan:
    policy_version: str
    plan_sha256: str
    snapshot_count: int
    candidate_track_count: int
    bundles: tuple[SnapshotQualificationBundle, ...]


@dataclass(frozen=True)
class QualificationBackfillResult:
    policy_version: str
    plan_sha256: str
    snapshot_count: int
    inserted_snapshot_count: int
    duplicate_snapshot_count: int
    candidate_track_count: int


@dataclass(frozen=True)
class _PreparedTrack:
    assessment_key: str
    snapshot_candidate_id: int
    candidate_key: str
    ticker: str
    policy_version: str
    track_key: str
    causal_window_state: str
    observation_state: str
    qualification_state: str
    reason_codes_json: str
    facts_sha256: str
    assessment_sha256: str
    assessment_json: str


@dataclass(frozen=True)
class _PreparedBundle:
    assessment_key: str
    snapshot_run_id: int
    snapshot_key: str
    policy_version: str
    publication_state: str
    analysis_quality_state: str
    publication_cycle_key: Optional[str]
    publication_attempt_key: Optional[str]
    publication_event_sequence: Optional[int]
    publication_observed_at: Optional[datetime]
    reason_codes_json: str
    facts_sha256: str
    assessment_sha256: str
    assessment_json: str
    tracks: tuple[_PreparedTrack, ...]


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalized_reasons(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value).strip()
                for value in values
                if str(value or "").strip()
            }
        )
    )


def _proof_payload(
    proof: Optional[CanonicalPublicationProof],
) -> Optional[dict[str, Any]]:
    if proof is None:
        return None
    return {
        "snapshot_key": proof.snapshot_key,
        "cycle_key": proof.cycle_key,
        "attempt_key": proof.attempt_key,
        "event_sequence": int(proof.event_sequence),
        "published_at": _db_utc(proof.published_at).isoformat(),
    }


def _track_payload(track: CandidateTrackQualification) -> dict[str, Any]:
    return {
        "snapshot_candidate_id": int(track.snapshot_candidate_id),
        "candidate_key": track.candidate_key,
        "ticker": track.ticker,
        "track_key": track.track_key,
        "causal_window_state": track.causal_window_state,
        "observation_state": track.observation_state,
        "qualification_state": track.qualification_state,
        "reason_codes": list(_normalized_reasons(track.reason_codes)),
        "facts": dict(track.facts),
    }


def _bundle_payload(bundle: SnapshotQualificationBundle) -> dict[str, Any]:
    return {
        "snapshot_run_id": int(bundle.snapshot_run_id),
        "snapshot_key": bundle.snapshot_key,
        "policy_version": bundle.policy_version,
        "publication_state": bundle.publication_state,
        "analysis_quality_state": bundle.analysis_quality_state,
        "publication_proof": _proof_payload(bundle.publication_proof),
        "reason_codes": list(_normalized_reasons(bundle.reason_codes)),
        "facts": dict(bundle.facts),
        "candidate_tracks": [
            _track_payload(track)
            for track in sorted(
                bundle.candidate_tracks,
                key=lambda item: (
                    item.snapshot_candidate_id,
                    item.track_key,
                ),
            )
        ],
    }


def _prepare_bundle(bundle: SnapshotQualificationBundle) -> _PreparedBundle:
    policy = str(bundle.policy_version or "").strip()
    snapshot_key = str(bundle.snapshot_key or "").strip()
    if not policy or not snapshot_key:
        raise QualificationRepositoryError(
            "snapshot_key and policy_version are required"
        )
    if bundle.publication_state not in PUBLICATION_STATES:
        raise QualificationRepositoryError("unsupported publication_state")
    if bundle.analysis_quality_state not in ANALYSIS_QUALITY_STATES:
        raise QualificationRepositoryError(
            "unsupported analysis_quality_state"
        )
    proof = bundle.publication_proof
    if bundle.publication_state == PUBLICATION_CANONICAL and proof is None:
        raise QualificationRepositoryError(
            "canonical_published requires verified publication proof"
        )
    if bundle.publication_state != PUBLICATION_CANONICAL and proof is not None:
        raise QualificationRepositoryError(
            "non-canonical assessments cannot retain publication proof"
        )

    expected_track_slots = {
        (track.snapshot_candidate_id, track.track_key)
        for track in bundle.candidate_tracks
    }
    if len(expected_track_slots) != len(bundle.candidate_tracks):
        raise QualificationRepositoryError(
            "candidate track assessment slots must be unique"
        )
    candidate_ids = {
        int(track.snapshot_candidate_id) for track in bundle.candidate_tracks
    }
    for candidate_id in candidate_ids:
        tracks = {
            track.track_key
            for track in bundle.candidate_tracks
            if int(track.snapshot_candidate_id) == candidate_id
        }
        if tracks != set(TRACK_KEYS):
            raise QualificationRepositoryError(
                "every candidate requires exactly the three policy tracks"
            )

    prepared_tracks: list[_PreparedTrack] = []
    for track in bundle.candidate_tracks:
        if track.track_key not in TRACK_KEYS:
            raise QualificationRepositoryError(
                f"unsupported track_key: {track.track_key}"
            )
        if track.causal_window_state not in CAUSAL_WINDOW_STATES:
            raise QualificationRepositoryError(
                "unsupported causal_window_state"
            )
        if track.observation_state not in OBSERVATION_STATES:
            raise QualificationRepositoryError(
                "unsupported observation_state"
            )
        if track.qualification_state not in QUALIFICATION_STATES:
            raise QualificationRepositoryError(
                "unsupported qualification_state"
            )
        semantic = _track_payload(track)
        assessment_json = canonical_json(semantic)
        facts_sha256 = canonical_sha256(dict(track.facts))
        prepared_tracks.append(
            _PreparedTrack(
                assessment_key="octa_"
                + canonical_sha256(
                    {
                        "candidate_key": track.candidate_key,
                        "policy_version": policy,
                        "track_key": track.track_key,
                    }
                ),
                snapshot_candidate_id=int(track.snapshot_candidate_id),
                candidate_key=str(track.candidate_key),
                ticker=str(track.ticker),
                policy_version=policy,
                track_key=track.track_key,
                causal_window_state=track.causal_window_state,
                observation_state=track.observation_state,
                qualification_state=track.qualification_state,
                reason_codes_json=canonical_json(
                    list(_normalized_reasons(track.reason_codes))
                ),
                facts_sha256=facts_sha256,
                assessment_sha256=canonical_sha256(semantic),
                assessment_json=assessment_json,
            )
        )

    semantic = _bundle_payload(bundle)
    # Candidate tracks are persisted in their own rows.  The snapshot row hash
    # intentionally covers only snapshot-level semantics so it remains useful
    # without duplicating the complete candidate payload.
    snapshot_semantic = {
        key: value
        for key, value in semantic.items()
        if key != "candidate_tracks"
    }
    snapshot_json = canonical_json(snapshot_semantic)
    return _PreparedBundle(
        assessment_key="osqa_"
        + canonical_sha256(
            {
                "snapshot_key": snapshot_key,
                "policy_version": policy,
            }
        ),
        snapshot_run_id=int(bundle.snapshot_run_id),
        snapshot_key=snapshot_key,
        policy_version=policy,
        publication_state=bundle.publication_state,
        analysis_quality_state=bundle.analysis_quality_state,
        publication_cycle_key=proof.cycle_key if proof else None,
        publication_attempt_key=proof.attempt_key if proof else None,
        publication_event_sequence=proof.event_sequence if proof else None,
        publication_observed_at=(
            _db_utc(proof.published_at) if proof else None
        ),
        reason_codes_json=canonical_json(
            list(_normalized_reasons(bundle.reason_codes))
        ),
        facts_sha256=canonical_sha256(dict(bundle.facts)),
        assessment_sha256=canonical_sha256(snapshot_semantic),
        assessment_json=snapshot_json,
        tracks=tuple(
            sorted(
                prepared_tracks,
                key=lambda item: (
                    item.snapshot_candidate_id,
                    item.track_key,
                ),
            )
        ),
    )


def init_qualification_schema(
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    db = db_manager or get_db()
    init_opportunity_schema(db)
    tables = (
        OpportunitySnapshotQualificationAssessment.__table__,
        OpportunityCandidateTrackAssessment.__table__,
    )
    Base.metadata.create_all(db._engine, tables=tables)
    if db._engine.dialect.name == "sqlite":
        with db._engine.begin() as connection:
            for table_name in _APPEND_ONLY_TABLES:
                for operation in ("UPDATE", "DELETE"):
                    connection.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS "
                        f"trg_{table_name}_{operation.lower()}_immutable "
                        f"BEFORE {operation} ON {table_name} "
                        "BEGIN SELECT RAISE(ABORT, "
                        "'opportunity qualification rows are append-only'); END"
                    )


def verify_canonical_publication(
    snapshot: StoredSnapshot,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> PublicationVerification:
    """Prove canonical publication from the terminal cycle event, or fail closed."""

    cycle = snapshot.payload.get("canonical_cycle")
    if not isinstance(cycle, Mapping):
        return PublicationVerification()
    cycle_key = str(cycle.get("cycle_key") or "").strip()
    attempt_key = str(cycle.get("attempt_key") or "").strip()
    if not cycle_key or not attempt_key:
        return PublicationVerification(
            reason_codes=("canonical_publication_identity_missing",)
        )

    db = db_manager or get_db()
    schema = inspect(db._engine)
    if not (
        schema.has_table("opportunity_premarket_cycle_attempts")
        and schema.has_table("opportunity_premarket_cycle_events")
    ):
        return PublicationVerification(
            reason_codes=("canonical_publication_evidence_tables_missing",)
        )
    try:
        status = get_latest_cycle_attempt_status(
            cycle_key,
            db_manager=db,
        )
    except Exception:  # noqa: BLE001 - verification must fail closed
        return PublicationVerification(
            reason_codes=("canonical_publication_lookup_unavailable",)
        )
    if status is None:
        return PublicationVerification(
            reason_codes=("canonical_publication_attempt_missing",)
        )
    reasons: list[str] = []
    if status.state != "published":
        reasons.append("canonical_publication_terminal_not_published")
    if status.attempt.attempt_key != attempt_key:
        reasons.append("canonical_publication_attempt_mismatch")
    if status.attempt.market_date_et != snapshot.market_date_et:
        reasons.append("canonical_publication_market_date_mismatch")
    if status.attempt.scope_key != snapshot.scope_key:
        reasons.append("canonical_publication_scope_mismatch")
    if (
        status.attempt.freeze_policy_version
        != snapshot.freeze_policy_version
    ):
        reasons.append("canonical_publication_freeze_policy_mismatch")

    terminal = next(
        (
            event
            for event in reversed(status.events)
            if event.event_type == "attempt_finished"
        ),
        None,
    )
    if terminal is None:
        reasons.append("canonical_publication_terminal_event_missing")
    else:
        if terminal.state != "published":
            reasons.append("canonical_publication_terminal_not_published")
        if str(terminal.payload.get("snapshot_key") or "") != snapshot.snapshot_key:
            reasons.append("canonical_publication_snapshot_mismatch")
        # The snapshot uses the first lock-protected publication clock.  The
        # terminal event is written after snapshot validation with a second
        # pre-commit clock, so it may be slightly later but can never precede
        # the frozen publication fact or reach the hard deadline.
        if (
            _db_utc(terminal.observed_at) < _db_utc(snapshot.frozen_at)
            or _db_utc(terminal.observed_at)
            >= _db_utc(status.attempt.hard_deadline_at)
        ):
            reasons.append("canonical_publication_timestamp_mismatch")

    cycle_published_at = cycle.get("published_at")
    try:
        parsed_published_at = datetime.fromisoformat(
            str(cycle_published_at).replace("Z", "+00:00")
        )
        if (
            parsed_published_at.tzinfo is None
            or parsed_published_at.utcoffset() is None
            or parsed_published_at.astimezone(timezone.utc)
            != _db_utc(snapshot.frozen_at)
        ):
            reasons.append("canonical_publication_timestamp_mismatch")
    except (TypeError, ValueError):
        reasons.append("canonical_publication_timestamp_missing")

    normalized_reasons = _normalized_reasons(reasons)
    if normalized_reasons or terminal is None:
        return PublicationVerification(reason_codes=normalized_reasons)
    return PublicationVerification(
        proof=CanonicalPublicationProof(
            snapshot_key=snapshot.snapshot_key,
            cycle_key=cycle_key,
            attempt_key=attempt_key,
            event_sequence=int(terminal.sequence),
            published_at=_db_utc(snapshot.frozen_at),
        )
    )


def _validate_bundle_source(
    session: Session,
    prepared: _PreparedBundle,
) -> None:
    snapshot = session.execute(
        select(OpportunitySnapshotRun).where(
            OpportunitySnapshotRun.id == prepared.snapshot_run_id,
            OpportunitySnapshotRun.snapshot_key == prepared.snapshot_key,
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise QualificationRepositoryError(
            "qualification snapshot does not exist"
        )
    candidate_rows = session.execute(
        select(OpportunitySnapshotCandidate).where(
            OpportunitySnapshotCandidate.snapshot_run_id
            == prepared.snapshot_run_id
        )
    ).scalars()
    candidate_by_id = {
        int(row.id): row for row in candidate_rows
    }
    candidate_ids = set(candidate_by_id)
    requested_ids = {
        int(track.snapshot_candidate_id) for track in prepared.tracks
    }
    if requested_ids != candidate_ids:
        raise QualificationRepositoryError(
            "qualification bundle must contain exactly every snapshot candidate"
        )
    for track in prepared.tracks:
        candidate = candidate_by_id[track.snapshot_candidate_id]
        if (
            track.candidate_key != str(candidate.candidate_key)
            or track.ticker != str(candidate.ticker)
        ):
            raise QualificationRepositoryError(
                "candidate track identity differs from the frozen candidate"
            )

    if prepared.publication_state != PUBLICATION_CANONICAL:
        return
    attempt = session.execute(
        select(OpportunityPremarketCycleAttempt).where(
            OpportunityPremarketCycleAttempt.attempt_key
            == prepared.publication_attempt_key
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise QualificationRepositoryError(
            "canonical publication attempt does not exist"
        )
    event = session.execute(
        select(OpportunityPremarketCycleEvent).where(
            OpportunityPremarketCycleEvent.attempt_id == attempt.id,
            OpportunityPremarketCycleEvent.sequence
            == prepared.publication_event_sequence,
        )
    ).scalar_one_or_none()
    try:
        event_payload = json.loads(event.payload_json) if event is not None else {}
    except (TypeError, json.JSONDecodeError):
        event_payload = {}
    if (
        str(attempt.cycle_key) != prepared.publication_cycle_key
        or attempt.market_date_et != snapshot.market_date_et
        or str(attempt.scope_key) != str(snapshot.scope_key)
        or str(attempt.freeze_policy_version)
        != str(snapshot.freeze_policy_version)
        or event is None
        or str(event.event_type) != "attempt_finished"
        or str(event.state) != "published"
        or str(event_payload.get("snapshot_key") or "")
        != prepared.snapshot_key
        or prepared.publication_observed_at is None
        or _db_utc(prepared.publication_observed_at)
        != _db_utc(snapshot.frozen_at)
    ):
        raise QualificationRepositoryError(
            "canonical publication proof does not match terminal cycle evidence"
        )


def _resolve_existing(
    session: Session,
    row: OpportunitySnapshotQualificationAssessment,
    prepared: _PreparedBundle,
) -> QualificationAppendResult:
    if (
        row.assessment_key != prepared.assessment_key
        or row.assessment_sha256 != prepared.assessment_sha256
        or row.facts_sha256 != prepared.facts_sha256
    ):
        raise QualificationConflictError(
            "snapshot qualification slot contains different immutable content"
        )
    stored_tracks = session.execute(
        select(OpportunityCandidateTrackAssessment).where(
            OpportunityCandidateTrackAssessment.snapshot_qualification_id
            == row.id
        )
    ).scalars()
    stored_by_slot = {
        (int(item.snapshot_candidate_id), str(item.track_key)): item
        for item in stored_tracks
    }
    prepared_by_slot = {
        (item.snapshot_candidate_id, item.track_key): item
        for item in prepared.tracks
    }
    if set(stored_by_slot) != set(prepared_by_slot):
        raise QualificationConflictError(
            "snapshot qualification candidate-track bundle differs"
        )
    for slot, expected in prepared_by_slot.items():
        current = stored_by_slot[slot]
        if (
            current.assessment_key != expected.assessment_key
            or current.assessment_sha256 != expected.assessment_sha256
            or current.facts_sha256 != expected.facts_sha256
        ):
            raise QualificationConflictError(
                "candidate track slot contains different immutable content"
            )
    return QualificationAppendResult(
        snapshot_qualification_id=int(row.id),
        assessment_key=str(row.assessment_key),
        snapshot_key=prepared.snapshot_key,
        policy_version=prepared.policy_version,
        candidate_track_count=len(stored_by_slot),
        duplicate=True,
    )


def _append_in_session(
    session: Session,
    prepared: _PreparedBundle,
) -> QualificationAppendResult:
    _validate_bundle_source(session, prepared)
    existing = session.execute(
        select(OpportunitySnapshotQualificationAssessment).where(
            OpportunitySnapshotQualificationAssessment.snapshot_run_id
            == prepared.snapshot_run_id,
            OpportunitySnapshotQualificationAssessment.policy_version
            == prepared.policy_version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _resolve_existing(session, existing, prepared)

    try:
        with session.begin_nested():
            row = OpportunitySnapshotQualificationAssessment(
                assessment_key=prepared.assessment_key,
                snapshot_run_id=prepared.snapshot_run_id,
                policy_version=prepared.policy_version,
                publication_state=prepared.publication_state,
                analysis_quality_state=prepared.analysis_quality_state,
                publication_cycle_key=prepared.publication_cycle_key,
                publication_attempt_key=prepared.publication_attempt_key,
                publication_event_sequence=prepared.publication_event_sequence,
                publication_observed_at=prepared.publication_observed_at,
                reason_codes_json=prepared.reason_codes_json,
                facts_sha256=prepared.facts_sha256,
                assessment_sha256=prepared.assessment_sha256,
                assessment_json=prepared.assessment_json,
            )
            session.add(row)
            session.flush()
            session.add_all(
                [
                    OpportunityCandidateTrackAssessment(
                        snapshot_qualification_id=int(row.id),
                        assessment_key=track.assessment_key,
                        snapshot_candidate_id=track.snapshot_candidate_id,
                        policy_version=track.policy_version,
                        track_key=track.track_key,
                        causal_window_state=track.causal_window_state,
                        observation_state=track.observation_state,
                        qualification_state=track.qualification_state,
                        reason_codes_json=track.reason_codes_json,
                        facts_sha256=track.facts_sha256,
                        assessment_sha256=track.assessment_sha256,
                        assessment_json=track.assessment_json,
                    )
                    for track in prepared.tracks
                ]
            )
            session.flush()
            result = QualificationAppendResult(
                snapshot_qualification_id=int(row.id),
                assessment_key=prepared.assessment_key,
                snapshot_key=prepared.snapshot_key,
                policy_version=prepared.policy_version,
                candidate_track_count=len(prepared.tracks),
                duplicate=False,
            )
        return result
    except IntegrityError:
        existing = session.execute(
            select(OpportunitySnapshotQualificationAssessment).where(
                OpportunitySnapshotQualificationAssessment.snapshot_run_id
                == prepared.snapshot_run_id,
                OpportunitySnapshotQualificationAssessment.policy_version
                == prepared.policy_version,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return _resolve_existing(session, existing, prepared)


def append_qualification_bundle(
    bundle: SnapshotQualificationBundle,
    *,
    db_manager: Optional[DatabaseManager] = None,
    session: Optional[Session] = None,
) -> QualificationAppendResult:
    """Append one complete snapshot assessment and all three candidate tracks."""

    db = db_manager or get_db()
    if session is None:
        init_qualification_schema(db)
    prepared = _prepare_bundle(bundle)
    if session is not None:
        return _append_in_session(session, prepared)
    with db.session_scope() as owned:
        return _append_in_session(owned, prepared)


def _stored_track(
    row: OpportunityCandidateTrackAssessment,
    candidate: OpportunitySnapshotCandidate,
) -> StoredCandidateTrackAssessment:
    payload = json.loads(row.assessment_json)
    return StoredCandidateTrackAssessment(
        id=int(row.id),
        assessment_key=str(row.assessment_key),
        snapshot_candidate_id=int(row.snapshot_candidate_id),
        candidate_key=str(candidate.candidate_key),
        ticker=str(candidate.ticker),
        policy_version=str(row.policy_version),
        track_key=str(row.track_key),
        causal_window_state=str(row.causal_window_state),
        observation_state=str(row.observation_state),
        qualification_state=str(row.qualification_state),
        reason_codes=tuple(json.loads(row.reason_codes_json)),
        facts_sha256=str(row.facts_sha256),
        assessment_sha256=str(row.assessment_sha256),
        facts=dict(payload.get("facts") or {}),
        assessed_at=_db_utc(row.assessed_at),
    )


def get_snapshot_qualification(
    snapshot_key: str,
    *,
    policy_version: str = QUALIFICATION_POLICY_VERSION,
    db_manager: Optional[DatabaseManager] = None,
    initialize: bool = True,
) -> Optional[StoredSnapshotQualification]:
    db = db_manager or get_db()
    if initialize:
        init_qualification_schema(db)
    elif not inspect(db._engine).has_table(
        OpportunitySnapshotQualificationAssessment.__tablename__
    ):
        return None
    with db.get_session() as session:
        row = session.execute(
            select(OpportunitySnapshotQualificationAssessment)
            .join(
                OpportunitySnapshotRun,
                OpportunitySnapshotRun.id
                == OpportunitySnapshotQualificationAssessment.snapshot_run_id,
            )
            .where(
                OpportunitySnapshotRun.snapshot_key == str(snapshot_key),
                OpportunitySnapshotQualificationAssessment.policy_version
                == str(policy_version),
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        snapshot_row = session.execute(
            select(OpportunitySnapshotRun).where(
                OpportunitySnapshotRun.id == row.snapshot_run_id
            )
        ).scalar_one()
        candidate_rows = session.execute(
            select(OpportunitySnapshotCandidate).where(
                OpportunitySnapshotCandidate.snapshot_run_id
                == row.snapshot_run_id
            )
        ).scalars()
        candidate_by_id = {
            int(candidate.id): candidate for candidate in candidate_rows
        }
        track_rows = session.execute(
            select(OpportunityCandidateTrackAssessment)
            .where(
                OpportunityCandidateTrackAssessment.snapshot_qualification_id
                == row.id
            )
            .order_by(
                OpportunityCandidateTrackAssessment.snapshot_candidate_id,
                OpportunityCandidateTrackAssessment.track_key,
            )
        ).scalars()
        tracks = tuple(
            _stored_track(track, candidate_by_id[int(track.snapshot_candidate_id)])
            for track in track_rows
        )
        payload = json.loads(row.assessment_json)
        proof = None
        if row.publication_state == PUBLICATION_CANONICAL:
            proof = CanonicalPublicationProof(
                snapshot_key=str(snapshot_row.snapshot_key),
                cycle_key=str(row.publication_cycle_key),
                attempt_key=str(row.publication_attempt_key),
                event_sequence=int(row.publication_event_sequence),
                published_at=_db_utc(row.publication_observed_at),
            )
        return StoredSnapshotQualification(
            id=int(row.id),
            assessment_key=str(row.assessment_key),
            snapshot_run_id=int(row.snapshot_run_id),
            snapshot_key=str(snapshot_row.snapshot_key),
            policy_version=str(row.policy_version),
            publication_state=str(row.publication_state),
            analysis_quality_state=str(row.analysis_quality_state),
            publication_proof=proof,
            reason_codes=tuple(json.loads(row.reason_codes_json)),
            facts_sha256=str(row.facts_sha256),
            assessment_sha256=str(row.assessment_sha256),
            facts=dict(payload.get("facts") or {}),
            assessed_at=_db_utc(row.assessed_at),
            candidate_tracks=tracks,
        )


def _plan_hash(bundles: Sequence[SnapshotQualificationBundle]) -> str:
    return canonical_sha256(
        [_bundle_payload(bundle) for bundle in bundles]
    )


def plan_qualification_backfill(
    *,
    policy_version: str = QUALIFICATION_POLICY_VERSION,
    limit: int = 500,
    db_manager: Optional[DatabaseManager] = None,
    calendar=None,
) -> QualificationBackfillPlan:
    """Build a deterministic dry-run plan without writing qualification rows."""

    normalized_limit = int(limit)
    if not 1 <= normalized_limit <= 500:
        raise QualificationRepositoryError(
            "backfill limit must be between 1 and 500"
        )
    db = db_manager or get_db()
    snapshots = sorted(
        list_snapshots(limit=normalized_limit, db_manager=db),
        key=lambda item: item.id,
    )
    bundles = tuple(
        classify_snapshot_qualification(
            snapshot,
            publication_verification=verify_canonical_publication(
                snapshot,
                db_manager=db,
            ),
            policy_version=policy_version,
            calendar=calendar,
        )
        for snapshot in snapshots
    )
    return QualificationBackfillPlan(
        policy_version=str(policy_version),
        plan_sha256=_plan_hash(bundles),
        snapshot_count=len(bundles),
        candidate_track_count=sum(
            len(bundle.candidate_tracks) for bundle in bundles
        ),
        bundles=bundles,
    )


def apply_qualification_backfill(
    plan: QualificationBackfillPlan,
    *,
    db_manager: Optional[DatabaseManager] = None,
    calendar=None,
) -> QualificationBackfillResult:
    """Revalidate and atomically apply an explicit dry-run plan."""

    if plan.plan_sha256 != _plan_hash(plan.bundles):
        raise QualificationBackfillStaleError(
            "qualification backfill plan hash is invalid"
        )
    db = db_manager or get_db()
    revalidated: list[SnapshotQualificationBundle] = []
    for planned in plan.bundles:
        snapshot = get_snapshot(planned.snapshot_key, db_manager=db)
        if snapshot is None:
            raise QualificationBackfillStaleError(
                f"snapshot disappeared: {planned.snapshot_key}"
            )
        current = classify_snapshot_qualification(
            snapshot,
            publication_verification=verify_canonical_publication(
                snapshot,
                db_manager=db,
            ),
            policy_version=plan.policy_version,
            calendar=calendar,
        )
        if canonical_json(_bundle_payload(current)) != canonical_json(
            _bundle_payload(planned)
        ):
            raise QualificationBackfillStaleError(
                f"qualification source changed: {planned.snapshot_key}"
            )
        revalidated.append(current)

    init_qualification_schema(db)
    results: list[QualificationAppendResult] = []
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        for bundle in revalidated:
            results.append(
                append_qualification_bundle(
                    bundle,
                    db_manager=db,
                    session=session,
                )
            )
    return QualificationBackfillResult(
        policy_version=plan.policy_version,
        plan_sha256=plan.plan_sha256,
        snapshot_count=len(results),
        inserted_snapshot_count=sum(
            1 for result in results if not result.duplicate
        ),
        duplicate_snapshot_count=sum(
            1 for result in results if result.duplicate
        ),
        candidate_track_count=sum(
            result.candidate_track_count for result in results
        ),
    )


__all__ = [
    "QualificationAppendResult",
    "QualificationBackfillPlan",
    "QualificationBackfillResult",
    "QualificationBackfillStaleError",
    "QualificationConflictError",
    "QualificationRepositoryError",
    "StoredCandidateTrackAssessment",
    "StoredSnapshotQualification",
    "append_qualification_bundle",
    "apply_qualification_backfill",
    "get_snapshot_qualification",
    "init_qualification_schema",
    "plan_qualification_backfill",
    "verify_canonical_publication",
]
