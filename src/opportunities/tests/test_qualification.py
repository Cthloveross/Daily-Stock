# -*- coding: utf-8 -*-
"""Pure qualification policy tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from src.opportunities.qualification import (
    ANALYSIS_DEGRADED,
    ANALYSIS_READY,
    ANALYSIS_UNASSESSED,
    CAUSAL_PROSPECTIVE,
    CAUSAL_RETROSPECTIVE,
    CAUSAL_UNVERIFIED,
    EXCLUDED,
    OBSERVATION_PARTIAL,
    OBSERVATION_READY,
    PUBLICATION_AUDIT,
    PUBLICATION_CANONICAL,
    PUBLICATION_LEGACY_UNVERIFIED,
    QUALIFIED,
    TRACK_CANONICAL_FULL_RESEARCH,
    TRACK_RAW_UNDERLYING_PATH,
    TRACK_UNDERLYING_DAILY_SELECTION,
    UNVERIFIED,
    CanonicalPublicationProof,
    PublicationVerification,
    classify_snapshot_qualification,
)
from src.opportunities.repository import StoredSnapshot, StoredSnapshotCandidate


UTC = timezone.utc
SIGNAL_SESSION = date(2026, 7, 23)
ENTRY_SESSION = date(2026, 7, 24)
SIGNAL_CLOSE = datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
ENTRY_OPEN = datetime(2026, 7, 24, 13, 30, tzinfo=UTC)
PUBLISHED_AT = datetime(2026, 7, 24, 13, 17, tzinfo=UTC)


class _Calendar:
    def is_session(self, value):
        return value == SIGNAL_SESSION

    def session_close(self, value):
        assert value == SIGNAL_SESSION
        return SIGNAL_CLOSE

    def next_session(self, value):
        assert value == SIGNAL_SESSION
        return ENTRY_SESSION

    def session_open(self, value):
        assert value == ENTRY_SESSION
        return ENTRY_OPEN


def _candidate(
    *,
    research_state: str = "research_ready",
    data_state: str = "complete",
    direction: str = "bullish",
    source: str | None = "MoomooFetcher",
    schedule: dict | None = None,
) -> StoredSnapshotCandidate:
    validation = {
        "benchmark_anchor": {
            "ticker": "SPY",
            "reference_session_date": SIGNAL_SESSION.isoformat(),
            "reference_close": 738.18,
            "source": "MoomooFetcher",
        }
    }
    if schedule is not None:
        validation["schedule"] = schedule
    return StoredSnapshotCandidate(
        id=11,
        candidate_key="opc_candidate_aapl",
        ticker="AAPL",
        rank=1,
        research_state=research_state,
        directional_context=direction,
        supporting_evidence_count=5,
        data_completeness_state=data_state,
        reference_session_date=SIGNAL_SESSION,
        reference_close=Decimal("225.25"),
        reference_price_basis="prior_completed_close",
        reference_source=source,
        validation_eligible=False,
        eligibility_reasons=(),
        payload={
            "ticker": "AAPL",
            "data_completeness": {"state": data_state},
            "outcome_validation": validation,
        },
    )


def _snapshot(
    *,
    frozen_at: datetime = PUBLISHED_AT,
    quality: str | None = "ready",
    scope_key: str = "canonical_premarket_research",
    freeze_policy: str = "canonical_premarket_xnys_v2",
    candidate: StoredSnapshotCandidate | None = None,
) -> StoredSnapshot:
    canonical_cycle = {
        "cycle_key": "opcycle_2026-07-24",
        "attempt_key": "opa_attempt",
        "published_at": frozen_at.isoformat(),
    }
    if quality is not None:
        canonical_cycle["quality"] = quality
        canonical_cycle["quality_reasons"] = (
            ["regime_supporting_events_degraded"]
            if quality == "degraded"
            else []
        )
    payload = {
        "universe": ["AAPL"],
        "canonical_cycle": canonical_cycle,
        "snapshot_meta": {
            "analysis_quality_eligible": quality == "ready",
            "analysis_quality_reasons": (
                ["regime_supporting_events_degraded"]
                if quality == "degraded"
                else []
            ),
        },
    }
    return StoredSnapshot(
        id=7,
        snapshot_key="ops_snapshot",
        market_date_et=ENTRY_SESSION,
        run_type="morning_prior_close",
        scope_key=scope_key,
        signal_version="daily_completed_bars_v1",
        schema_version="1.1",
        freeze_policy_version=freeze_policy,
        playbook_version="unverified",
        source_run_id="opr_source",
        requested_limit=1,
        ranking_method="rule_based_evidence_count",
        strategy_validation_state="not_validated",
        as_of=frozen_at,
        frozen_at=frozen_at,
        validation_eligible=False,
        eligibility_reasons=(),
        payload_sha256="a" * 64,
        payload=payload,
        candidates=(candidate or _candidate(),),
    )


def _verification(snapshot: StoredSnapshot) -> PublicationVerification:
    return PublicationVerification(
        proof=CanonicalPublicationProof(
            snapshot_key=snapshot.snapshot_key,
            cycle_key="opcycle_2026-07-24",
            attempt_key="opa_attempt",
            event_sequence=13,
            published_at=snapshot.frozen_at,
        )
    )


def _tracks(bundle):
    return {item.track_key: item for item in bundle.candidate_tracks}


def test_ready_canonical_candidate_qualifies_for_all_three_tracks():
    snapshot = _snapshot()

    result = classify_snapshot_qualification(
        snapshot,
        publication_verification=_verification(snapshot),
        calendar=_Calendar(),
    )

    assert result.publication_state == PUBLICATION_CANONICAL
    assert result.analysis_quality_state == ANALYSIS_READY
    tracks = _tracks(result)
    assert len(tracks) == 3
    assert {item.qualification_state for item in tracks.values()} == {
        QUALIFIED
    }
    assert {
        item.causal_window_state for item in tracks.values()
    } == {CAUSAL_PROSPECTIVE}
    assert {item.observation_state for item in tracks.values()} == {
        OBSERVATION_READY
    }


def test_degraded_canonical_keeps_raw_and_selection_but_excludes_full():
    snapshot = _snapshot(quality="degraded")

    result = classify_snapshot_qualification(
        snapshot,
        publication_verification=_verification(snapshot),
        calendar=_Calendar(),
    )

    assert result.publication_state == PUBLICATION_CANONICAL
    assert result.analysis_quality_state == ANALYSIS_DEGRADED
    tracks = _tracks(result)
    assert (
        tracks[TRACK_RAW_UNDERLYING_PATH].qualification_state
        == QUALIFIED
    )
    assert (
        tracks[TRACK_UNDERLYING_DAILY_SELECTION].qualification_state
        == QUALIFIED
    )
    full = tracks[TRACK_CANONICAL_FULL_RESEARCH]
    assert full.qualification_state == EXCLUDED
    assert "analysis_quality_degraded" in full.reason_codes


def test_legacy_snapshot_never_guesses_official_publication_or_quality():
    snapshot = _snapshot(
        quality=None,
        scope_key="web_daily_opportunity",
        freeze_policy="premarket_prior_close_xnys_v1",
        candidate=_candidate(
            research_state="watch_only",
            data_state="partial",
        ),
    )
    snapshot = replace(
        snapshot,
        payload={"universe": ["AAPL"], "snapshot_meta": {}},
    )

    result = classify_snapshot_qualification(
        snapshot,
        calendar=_Calendar(),
    )

    assert result.publication_state == PUBLICATION_LEGACY_UNVERIFIED
    assert result.analysis_quality_state == ANALYSIS_UNASSESSED
    tracks = _tracks(result)
    assert tracks[TRACK_RAW_UNDERLYING_PATH].qualification_state == QUALIFIED
    assert (
        tracks[TRACK_UNDERLYING_DAILY_SELECTION].qualification_state
        == UNVERIFIED
    )
    assert (
        tracks[TRACK_CANONICAL_FULL_RESEARCH].qualification_state
        == EXCLUDED
    )


def test_retrospective_audit_is_excluded_from_directional_raw_path():
    candidate = _candidate()
    snapshot = _snapshot(
        frozen_at=datetime(2026, 7, 24, 16, 5, tzinfo=UTC),
        scope_key="manual_audit",
        freeze_policy="audit_v1",
        candidate=candidate,
    )
    snapshot = replace(
        snapshot,
        payload={
            "universe": ["AAPL"],
            "snapshot_meta": {
                "analysis_quality_eligible": True,
                "analysis_quality_reasons": [],
            },
        },
    )

    result = classify_snapshot_qualification(
        snapshot,
        calendar=_Calendar(),
    )

    assert result.publication_state == PUBLICATION_AUDIT
    tracks = _tracks(result)
    raw = tracks[TRACK_RAW_UNDERLYING_PATH]
    assert raw.causal_window_state == CAUSAL_RETROSPECTIVE
    assert raw.qualification_state == EXCLUDED
    assert "causal_window_retrospective" in raw.reason_codes
    assert (
        tracks[TRACK_UNDERLYING_DAILY_SELECTION].qualification_state
        == EXCLUDED
    )
    assert (
        tracks[TRACK_CANONICAL_FULL_RESEARCH].qualification_state
        == EXCLUDED
    )


def test_missing_source_is_partial_and_never_silently_qualified():
    snapshot = _snapshot(candidate=_candidate(source=None))

    result = classify_snapshot_qualification(
        snapshot,
        publication_verification=_verification(snapshot),
        calendar=_Calendar(),
    )

    for track in result.candidate_tracks:
        assert track.observation_state == OBSERVATION_PARTIAL
        assert track.qualification_state == UNVERIFIED
        assert "missing_reference_source" in track.reason_codes


def test_stored_schedule_disagreement_fails_causal_state_closed():
    snapshot = _snapshot(
        candidate=_candidate(
            schedule={
                "signal_close_at": SIGNAL_CLOSE.isoformat(),
                "entry_session": ENTRY_SESSION.isoformat(),
                "entry_open_at": datetime(
                    2026,
                    7,
                    24,
                    14,
                    30,
                    tzinfo=UTC,
                ).isoformat(),
            }
        )
    )

    result = classify_snapshot_qualification(
        snapshot,
        publication_verification=_verification(snapshot),
        calendar=_Calendar(),
    )

    for track in result.candidate_tracks:
        assert track.causal_window_state == CAUSAL_UNVERIFIED
        assert "stored_schedule_mismatch" in track.reason_codes
    assert (
        _tracks(result)[TRACK_RAW_UNDERLYING_PATH].qualification_state
        == UNVERIFIED
    )
