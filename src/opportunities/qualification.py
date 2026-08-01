# -*- coding: utf-8 -*-
"""Pure, versioned qualification policy for frozen opportunity research.

The legacy ``validation_eligible`` flag mixes several independent questions:
whether a snapshot was officially published, whether its research inputs were
complete, whether the candidate was frozen before the next session open, and
whether a particular downstream analysis may use it.  This module keeps those
axes separate and performs no persistence or provider access.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import math
from typing import Any, Mapping, Optional

from src.opportunities.repository import StoredSnapshot, StoredSnapshotCandidate


QUALIFICATION_POLICY_VERSION = "opportunity_qualification_v2"

PUBLICATION_CANONICAL = "canonical_published"
PUBLICATION_AUDIT = "audit_frozen"
PUBLICATION_LEGACY_UNVERIFIED = "legacy_unverified"
PUBLICATION_STATES = frozenset(
    {
        PUBLICATION_CANONICAL,
        PUBLICATION_AUDIT,
        PUBLICATION_LEGACY_UNVERIFIED,
    }
)

ANALYSIS_READY = "ready"
ANALYSIS_DEGRADED = "degraded"
ANALYSIS_BLOCKED = "blocked"
ANALYSIS_UNASSESSED = "unassessed"
ANALYSIS_QUALITY_STATES = frozenset(
    {
        ANALYSIS_READY,
        ANALYSIS_DEGRADED,
        ANALYSIS_BLOCKED,
        ANALYSIS_UNASSESSED,
    }
)

CAUSAL_PROSPECTIVE = "prospective"
CAUSAL_RETROSPECTIVE = "retrospective"
CAUSAL_PREMATURE = "premature"
CAUSAL_UNVERIFIED = "unverified"
CAUSAL_WINDOW_STATES = frozenset(
    {
        CAUSAL_PROSPECTIVE,
        CAUSAL_RETROSPECTIVE,
        CAUSAL_PREMATURE,
        CAUSAL_UNVERIFIED,
    }
)

OBSERVATION_READY = "ready"
OBSERVATION_PARTIAL = "partial"
OBSERVATION_UNAVAILABLE = "unavailable"
OBSERVATION_STATES = frozenset(
    {
        OBSERVATION_READY,
        OBSERVATION_PARTIAL,
        OBSERVATION_UNAVAILABLE,
    }
)

QUALIFIED = "qualified"
EXCLUDED = "excluded"
UNVERIFIED = "unverified"
QUALIFICATION_STATES = frozenset({QUALIFIED, EXCLUDED, UNVERIFIED})

TRACK_RAW_UNDERLYING_PATH = "raw_underlying_path_v1"
TRACK_UNDERLYING_DAILY_SELECTION = "underlying_daily_selection_v1"
TRACK_CANONICAL_FULL_RESEARCH = "canonical_full_research_v1"
TRACK_KEYS = (
    TRACK_RAW_UNDERLYING_PATH,
    TRACK_UNDERLYING_DAILY_SELECTION,
    TRACK_CANONICAL_FULL_RESEARCH,
)

_LEGACY_SCOPE_KEYS = frozenset({"web_daily_opportunity"})
_LEGACY_FREEZE_POLICIES = frozenset({"premarket_prior_close_xnys_v1"})


@dataclass(frozen=True)
class CanonicalPublicationProof:
    """Repository-verified terminal publication evidence."""

    snapshot_key: str
    cycle_key: str
    attempt_key: str
    event_sequence: int
    published_at: datetime


@dataclass(frozen=True)
class PublicationVerification:
    """Publication proof plus fail-closed verification diagnostics."""

    proof: Optional[CanonicalPublicationProof] = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateTrackQualification:
    """One candidate's immutable qualification for one analysis track."""

    snapshot_candidate_id: int
    candidate_key: str
    ticker: str
    track_key: str
    causal_window_state: str
    observation_state: str
    qualification_state: str
    reason_codes: tuple[str, ...]
    facts: Mapping[str, Any]


@dataclass(frozen=True)
class SnapshotQualificationBundle:
    """Snapshot axes plus the three candidate-track decisions."""

    snapshot_run_id: int
    snapshot_key: str
    policy_version: str
    publication_state: str
    analysis_quality_state: str
    publication_proof: Optional[CanonicalPublicationProof]
    reason_codes: tuple[str, ...]
    facts: Mapping[str, Any]
    candidate_tracks: tuple[CandidateTrackQualification, ...]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _aware_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _finite_positive(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reason_codes(values) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value).strip()
                for value in values
                if str(value or "").strip()
            }
        )
    )


def _calendar_date(value: Any) -> Optional[date]:
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    return _parse_date(converted)


def _calendar_datetime(value: Any) -> Optional[datetime]:
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(converted, datetime):
        return None
    if converted.tzinfo is None or converted.utcoffset() is None:
        return None
    return converted.astimezone(timezone.utc)


def _default_xnys_calendar():
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS")


def _publication_state(
    snapshot: StoredSnapshot,
    verification: PublicationVerification,
) -> tuple[str, tuple[str, ...]]:
    reasons = list(verification.reason_codes)
    proof = verification.proof
    cycle = _mapping(snapshot.payload.get("canonical_cycle"))
    frozen_at = _aware_utc(snapshot.frozen_at)
    proof_matches = bool(
        proof is not None
        and proof.snapshot_key == snapshot.snapshot_key
        and proof.cycle_key == str(cycle.get("cycle_key") or "")
        and proof.attempt_key == str(cycle.get("attempt_key") or "")
        and _aware_utc(proof.published_at) == frozen_at
        and _parse_datetime(cycle.get("published_at")) == frozen_at
    )
    if proof_matches:
        return PUBLICATION_CANONICAL, _reason_codes(reasons)
    if proof is not None:
        reasons.append("canonical_publication_proof_mismatch")

    looks_legacy_or_canonical = bool(
        cycle
        or snapshot.scope_key in _LEGACY_SCOPE_KEYS
        or snapshot.freeze_policy_version in _LEGACY_FREEZE_POLICIES
    )
    if looks_legacy_or_canonical:
        reasons.append("canonical_publication_unverified")
        return PUBLICATION_LEGACY_UNVERIFIED, _reason_codes(reasons)
    return PUBLICATION_AUDIT, _reason_codes(reasons)


def _analysis_quality(
    snapshot: StoredSnapshot,
) -> tuple[str, tuple[str, ...]]:
    cycle = _mapping(snapshot.payload.get("canonical_cycle"))
    meta = _mapping(snapshot.payload.get("snapshot_meta"))
    reasons = [
        *list(cycle.get("quality_reasons") or ()),
        *list(meta.get("analysis_quality_reasons") or ()),
    ]
    cycle_quality = str(cycle.get("quality") or "").strip().lower()
    explicit_present = "analysis_quality_eligible" in meta
    explicit = meta.get("analysis_quality_eligible")

    if cycle_quality in {
        ANALYSIS_READY,
        ANALYSIS_DEGRADED,
        ANALYSIS_BLOCKED,
    }:
        contradictory = (
            (cycle_quality == ANALYSIS_READY and explicit_present and explicit is False)
            or (
                cycle_quality in {ANALYSIS_DEGRADED, ANALYSIS_BLOCKED}
                and explicit_present
                and explicit is True
            )
        )
        if contradictory:
            reasons.append("analysis_quality_manifest_conflict")
            return ANALYSIS_UNASSESSED, _reason_codes(reasons)
        return cycle_quality, _reason_codes(reasons)

    if explicit_present and explicit is True:
        return ANALYSIS_READY, _reason_codes(reasons)
    if explicit_present and explicit is False and reasons:
        return ANALYSIS_DEGRADED, _reason_codes(reasons)
    reasons.append("analysis_quality_not_assessed")
    return ANALYSIS_UNASSESSED, _reason_codes(reasons)


def _candidate_axes(
    snapshot: StoredSnapshot,
    candidate: StoredSnapshotCandidate,
    *,
    calendar,
) -> tuple[str, str, tuple[str, ...], Mapping[str, Any]]:
    reasons: list[str] = []
    reference_session = candidate.reference_session_date
    reference_close = _finite_positive(candidate.reference_close)
    reference_source = str(candidate.reference_source or "").strip()
    frozen_at = _aware_utc(snapshot.frozen_at)
    signal_close_at: Optional[datetime] = None
    entry_session: Optional[date] = None
    entry_open_at: Optional[datetime] = None

    if reference_session is None:
        reasons.append("missing_reference_session")
    if reference_close is None:
        reasons.append("missing_reference_close")
    if not reference_source:
        reasons.append("missing_reference_source")

    if reference_session is not None:
        try:
            if not bool(calendar.is_session(reference_session)):
                reasons.append("reference_session_not_xnys")
            else:
                signal_close_at = _calendar_datetime(
                    calendar.session_close(reference_session)
                )
                entry_session = _calendar_date(
                    calendar.next_session(reference_session)
                )
                if entry_session is not None:
                    entry_open_at = _calendar_datetime(
                        calendar.session_open(entry_session)
                    )
                if (
                    signal_close_at is None
                    or entry_session is None
                    or entry_open_at is None
                ):
                    reasons.append("xnys_schedule_unavailable")
        except Exception:  # noqa: BLE001 - calendar failure must remain explicit
            reasons.append("xnys_schedule_unavailable")

    stored_validation = _mapping(candidate.payload.get("outcome_validation"))
    stored_schedule = _mapping(stored_validation.get("schedule"))
    if stored_schedule and signal_close_at is not None and entry_open_at is not None:
        stored_signal_close = _parse_datetime(stored_schedule.get("signal_close_at"))
        stored_entry_open = _parse_datetime(stored_schedule.get("entry_open_at"))
        stored_entry_session = _parse_date(stored_schedule.get("entry_session"))
        if (
            stored_signal_close != signal_close_at
            or stored_entry_open != entry_open_at
            or stored_entry_session != entry_session
        ):
            reasons.append("stored_schedule_mismatch")
            signal_close_at = None
            entry_open_at = None

    if signal_close_at is None or entry_open_at is None:
        causal_state = CAUSAL_UNVERIFIED
    elif frozen_at < signal_close_at:
        causal_state = CAUSAL_PREMATURE
    elif frozen_at >= entry_open_at:
        causal_state = CAUSAL_RETROSPECTIVE
    else:
        causal_state = CAUSAL_PROSPECTIVE

    if reference_session is None or reference_close is None:
        observation_state = OBSERVATION_UNAVAILABLE
    elif (
        not reference_source
        or signal_close_at is None
        or entry_open_at is None
    ):
        observation_state = OBSERVATION_PARTIAL
    else:
        observation_state = OBSERVATION_READY

    facts = {
        "snapshot_frozen_at": frozen_at.isoformat(),
        "reference_session_date": (
            reference_session.isoformat()
            if reference_session is not None
            else None
        ),
        "reference_close": (
            format(Decimal(candidate.reference_close), "f")
            if candidate.reference_close is not None
            else None
        ),
        "reference_source": candidate.reference_source,
        "signal_close_at": (
            signal_close_at.isoformat() if signal_close_at is not None else None
        ),
        "entry_session": (
            entry_session.isoformat() if entry_session is not None else None
        ),
        "entry_open_at": (
            entry_open_at.isoformat() if entry_open_at is not None else None
        ),
        "research_state": candidate.research_state,
        "data_completeness_state": candidate.data_completeness_state,
        "directional_context": candidate.directional_context,
    }
    return (
        causal_state,
        observation_state,
        _reason_codes(reasons),
        facts,
    )


def _benchmark_ready(candidate: StoredSnapshotCandidate) -> bool:
    validation = _mapping(candidate.payload.get("outcome_validation"))
    anchor = _mapping(validation.get("benchmark_anchor"))
    return bool(
        str(anchor.get("ticker") or "SPY").strip().upper() == "SPY"
        and _parse_date(anchor.get("reference_session_date"))
        == candidate.reference_session_date
        and _finite_positive(anchor.get("reference_close")) is not None
        and str(anchor.get("source") or "").strip()
    )


def _decision(
    *,
    exclusions: list[str],
    uncertainties: list[str],
) -> tuple[str, tuple[str, ...]]:
    if exclusions:
        return EXCLUDED, _reason_codes([*exclusions, *uncertainties])
    if uncertainties:
        return UNVERIFIED, _reason_codes(uncertainties)
    return QUALIFIED, ()


def _track_qualifications(
    snapshot: StoredSnapshot,
    candidate: StoredSnapshotCandidate,
    *,
    publication_state: str,
    analysis_quality_state: str,
    causal_window_state: str,
    observation_state: str,
    axis_reasons: tuple[str, ...],
    facts: Mapping[str, Any],
) -> tuple[CandidateTrackQualification, ...]:
    raw_exclusions: list[str] = []
    raw_uncertainties: list[str] = []
    if observation_state == OBSERVATION_UNAVAILABLE:
        raw_exclusions.append("underlying_observation_unavailable")
    elif observation_state == OBSERVATION_PARTIAL:
        raw_uncertainties.append("underlying_observation_partial")
    if causal_window_state == CAUSAL_RETROSPECTIVE:
        raw_exclusions.append("causal_window_retrospective")
    elif causal_window_state in {CAUSAL_PREMATURE, CAUSAL_UNVERIFIED}:
        raw_uncertainties.append(
            "causal_window_" + causal_window_state
        )
    raw_state, raw_reasons = _decision(
        exclusions=raw_exclusions,
        uncertainties=raw_uncertainties,
    )

    daily_exclusions: list[str] = []
    daily_uncertainties: list[str] = []
    if publication_state == PUBLICATION_AUDIT:
        daily_exclusions.append("not_an_official_daily_publication")
    elif publication_state == PUBLICATION_LEGACY_UNVERIFIED:
        daily_uncertainties.append("official_publication_unverified")
    if causal_window_state in {CAUSAL_RETROSPECTIVE, CAUSAL_PREMATURE}:
        daily_exclusions.append("causal_window_" + causal_window_state)
    elif causal_window_state == CAUSAL_UNVERIFIED:
        daily_uncertainties.append("causal_window_unverified")
    if observation_state == OBSERVATION_UNAVAILABLE:
        daily_exclusions.append("underlying_observation_unavailable")
    elif observation_state == OBSERVATION_PARTIAL:
        daily_uncertainties.append("underlying_observation_partial")
    if candidate.research_state == "blocked":
        daily_exclusions.append("candidate_blocked")
    if analysis_quality_state == ANALYSIS_BLOCKED:
        daily_exclusions.append("analysis_quality_blocked")
    elif analysis_quality_state == ANALYSIS_UNASSESSED:
        daily_uncertainties.append("analysis_quality_unassessed")
    daily_state, daily_reasons = _decision(
        exclusions=daily_exclusions,
        uncertainties=daily_uncertainties,
    )

    full_exclusions: list[str] = []
    full_uncertainties: list[str] = []
    if daily_state == EXCLUDED:
        full_exclusions.extend(daily_reasons)
    elif daily_state == UNVERIFIED:
        full_uncertainties.extend(daily_reasons)
    if analysis_quality_state == ANALYSIS_DEGRADED:
        full_exclusions.append("analysis_quality_degraded")
    if candidate.research_state != "research_ready":
        full_exclusions.append("candidate_not_research_ready")
    if candidate.data_completeness_state != "complete":
        full_exclusions.append("candidate_data_not_complete")
    if str(candidate.directional_context).lower() not in {"bullish", "bearish"}:
        full_exclusions.append("candidate_direction_not_actionable")
    if not _benchmark_ready(candidate):
        full_exclusions.append("benchmark_anchor_not_ready")
    full_state, full_reasons = _decision(
        exclusions=full_exclusions,
        uncertainties=full_uncertainties,
    )

    common = {
        "snapshot_candidate_id": candidate.id,
        "candidate_key": candidate.candidate_key,
        "ticker": candidate.ticker,
        "causal_window_state": causal_window_state,
        "observation_state": observation_state,
        "facts": {
            **dict(facts),
            "publication_state": publication_state,
            "analysis_quality_state": analysis_quality_state,
        },
    }
    return (
        CandidateTrackQualification(
            track_key=TRACK_RAW_UNDERLYING_PATH,
            qualification_state=raw_state,
            reason_codes=_reason_codes([*axis_reasons, *raw_reasons]),
            **common,
        ),
        CandidateTrackQualification(
            track_key=TRACK_UNDERLYING_DAILY_SELECTION,
            qualification_state=daily_state,
            reason_codes=_reason_codes([*axis_reasons, *daily_reasons]),
            **common,
        ),
        CandidateTrackQualification(
            track_key=TRACK_CANONICAL_FULL_RESEARCH,
            qualification_state=full_state,
            reason_codes=_reason_codes([*axis_reasons, *full_reasons]),
            **common,
        ),
    )


def classify_snapshot_qualification(
    snapshot: StoredSnapshot,
    *,
    publication_verification: Optional[PublicationVerification] = None,
    policy_version: str = QUALIFICATION_POLICY_VERSION,
    calendar=None,
) -> SnapshotQualificationBundle:
    """Classify a frozen snapshot without reading or mutating external state."""

    normalized_policy = str(policy_version or "").strip()
    if not normalized_policy:
        raise ValueError("policy_version is required")
    verification = publication_verification or PublicationVerification()
    publication_state, publication_reasons = _publication_state(
        snapshot,
        verification,
    )
    analysis_state, analysis_reasons = _analysis_quality(snapshot)
    reasons = _reason_codes([*publication_reasons, *analysis_reasons])
    xnys = calendar if calendar is not None else _default_xnys_calendar()

    tracks: list[CandidateTrackQualification] = []
    for candidate in snapshot.candidates:
        causal_state, observation_state, axis_reasons, facts = _candidate_axes(
            snapshot,
            candidate,
            calendar=xnys,
        )
        tracks.extend(
            _track_qualifications(
                snapshot,
                candidate,
                publication_state=publication_state,
                analysis_quality_state=analysis_state,
                causal_window_state=causal_state,
                observation_state=observation_state,
                axis_reasons=axis_reasons,
                facts=facts,
            )
        )

    cycle = _mapping(snapshot.payload.get("canonical_cycle"))
    meta = _mapping(snapshot.payload.get("snapshot_meta"))
    proof = verification.proof if publication_state == PUBLICATION_CANONICAL else None
    return SnapshotQualificationBundle(
        snapshot_run_id=snapshot.id,
        snapshot_key=snapshot.snapshot_key,
        policy_version=normalized_policy,
        publication_state=publication_state,
        analysis_quality_state=analysis_state,
        publication_proof=proof,
        reason_codes=reasons,
        facts={
            "snapshot_key": snapshot.snapshot_key,
            "market_date_et": snapshot.market_date_et.isoformat(),
            "scope_key": snapshot.scope_key,
            "freeze_policy_version": snapshot.freeze_policy_version,
            "frozen_at": _aware_utc(snapshot.frozen_at).isoformat(),
            "cycle_key": cycle.get("cycle_key"),
            "attempt_key": cycle.get("attempt_key"),
            "cycle_quality": cycle.get("quality"),
            "cycle_quality_reasons": list(cycle.get("quality_reasons") or ()),
            "analysis_quality_eligible": meta.get(
                "analysis_quality_eligible"
            ),
            "analysis_quality_reasons": list(
                meta.get("analysis_quality_reasons") or ()
            ),
        },
        candidate_tracks=tuple(tracks),
    )


__all__ = [
    "ANALYSIS_QUALITY_STATES",
    "CAUSAL_WINDOW_STATES",
    "OBSERVATION_STATES",
    "PUBLICATION_STATES",
    "QUALIFICATION_POLICY_VERSION",
    "QUALIFICATION_STATES",
    "TRACK_KEYS",
    "TRACK_RAW_UNDERLYING_PATH",
    "TRACK_UNDERLYING_DAILY_SELECTION",
    "TRACK_CANONICAL_FULL_RESEARCH",
    "CanonicalPublicationProof",
    "CandidateTrackQualification",
    "PublicationVerification",
    "SnapshotQualificationBundle",
    "classify_snapshot_qualification",
]
