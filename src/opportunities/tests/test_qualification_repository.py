# -*- coding: utf-8 -*-
"""Persistence and explicit backfill tests for opportunity qualification."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from src.opportunities.cycle_repository import (
    claim_cycle_attempt,
    publish_cycle_snapshot_atomically,
)
from src.opportunities.qualification import (
    ANALYSIS_DEGRADED,
    ANALYSIS_READY,
    PUBLICATION_CANONICAL,
    PUBLICATION_LEGACY_UNVERIFIED,
    QUALIFICATION_POLICY_VERSION,
    TRACK_CANONICAL_FULL_RESEARCH,
    TRACK_RAW_UNDERLYING_PATH,
    TRACK_UNDERLYING_DAILY_SELECTION,
    CanonicalPublicationProof,
    classify_snapshot_qualification,
)
from src.opportunities.qualification_models import (
    OpportunityCandidateTrackAssessment,
    OpportunitySnapshotQualificationAssessment,
)
from src.opportunities.qualification_repository import (
    QualificationBackfillStaleError,
    QualificationConflictError,
    QualificationRepositoryError,
    append_qualification_bundle,
    apply_qualification_backfill,
    get_snapshot_qualification,
    plan_qualification_backfill,
    verify_canonical_publication,
)
from src.opportunities.repository import (
    SnapshotCandidateInput,
    SnapshotRunInput,
    append_snapshot,
    get_snapshot,
)
from src.storage import DatabaseManager


UTC = timezone.utc
MARKET_DATE = date(2026, 7, 24)
SIGNAL_DATE = date(2026, 7, 23)
SIGNAL_CLOSE = datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
ENTRY_OPEN = datetime(2026, 7, 24, 13, 30, tzinfo=UTC)
PUBLISHED_AT = datetime(2026, 7, 24, 13, 17, tzinfo=UTC)
CYCLE_KEY = "opcycle_2026-07-24"
SCOPE_KEY = "canonical_premarket_research"
FREEZE_POLICY = "canonical_premarket_xnys_v2"


class _Calendar:
    def is_session(self, value):
        return value == SIGNAL_DATE

    def session_close(self, value):
        assert value == SIGNAL_DATE
        return SIGNAL_CLOSE

    def next_session(self, value):
        assert value == SIGNAL_DATE
        return MARKET_DATE

    def session_open(self, value):
        assert value == MARKET_DATE
        return ENTRY_OPEN


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "qualification.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    import src.config as config_module

    monkeypatch.setattr(
        config_module.Config,
        "_parse_stock_email_groups",
        classmethod(lambda _cls: []),
    )
    config_module.Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    yield db
    DatabaseManager.reset_instance()
    config_module.Config.reset_instance()


def _candidate(published_at: datetime) -> SnapshotCandidateInput:
    return SnapshotCandidateInput(
        ticker="AAPL",
        rank=1,
        research_state="research_ready",
        directional_context="bullish",
        supporting_evidence_count=5,
        data_completeness_state="complete",
        source_candidate_id="opc_aapl",
        reference_session_date=SIGNAL_DATE,
        reference_close=Decimal("225.25"),
        reference_source="MoomooFetcher",
        validation_eligible=False,
        payload={
            "ticker": "AAPL",
            "data_completeness": {"state": "complete"},
            "outcome_validation": {
                "frozen_at": published_at.isoformat(),
                "benchmark_anchor": {
                    "ticker": "SPY",
                    "reference_session_date": SIGNAL_DATE.isoformat(),
                    "reference_close": 738.18,
                    "source": "MoomooFetcher",
                },
            },
        },
    )


def _run(
    published_at: datetime,
    *,
    attempt_key: str,
    quality: str = "degraded",
) -> SnapshotRunInput:
    reasons = (
        ["regime_supporting_events_degraded"]
        if quality == "degraded"
        else []
    )
    return SnapshotRunInput(
        market_date_et=MARKET_DATE,
        run_type="morning_prior_close",
        signal_version="daily_completed_bars_v1",
        schema_version="1.1",
        freeze_policy_version=FREEZE_POLICY,
        playbook_version="unverified",
        scope_key=SCOPE_KEY,
        universe=("AAPL",),
        requested_limit=1,
        source_run_id="opr_canonical",
        ranking_method="rule_based_evidence_count",
        strategy_validation_state="not_validated",
        as_of=datetime(2026, 7, 24, 13, 12, tzinfo=UTC),
        frozen_at=published_at,
        validation_eligible=quality == "ready",
        eligibility_reasons=tuple(reasons),
        payload={
            "schema_version": "1.1",
            "market_date_et": MARKET_DATE.isoformat(),
            "universe": ["AAPL"],
            "requested_limit": 1,
            "canonical_cycle": {
                "cycle_key": CYCLE_KEY,
                "attempt_key": attempt_key,
                "published_at": published_at.isoformat(),
                "quality": quality,
                "quality_reasons": reasons,
            },
            "snapshot_meta": {
                "frozen_at": published_at.isoformat(),
                "analysis_quality_eligible": quality == "ready",
                "analysis_quality_reasons": reasons,
            },
        },
    )


def _seed_canonical(db, *, quality: str = "degraded"):
    owner = "qualification-test-owner"
    claim = claim_cycle_attempt(
        cycle_key=CYCLE_KEY,
        market_date_et=MARKET_DATE,
        scope_key=SCOPE_KEY,
        freeze_policy_version=FREEZE_POLICY,
        cycle_version="canonical_premarket_cycle_v1",
        universe=("AAPL",),
        requested_limit=1,
        universe_source="persisted",
        universe_version_key="opu_fixture",
        trigger="scheduler",
        owner_key=owner,
        hard_deadline_at=datetime(2026, 7, 24, 13, 20, tzinfo=UTC),
        now=datetime(2026, 7, 24, 13, 12, tzinfo=UTC),
        lease_seconds=8 * 60,
        db_manager=db,
    )
    attempt_key = claim.attempt.attempt_key

    def factory(session, published_at):
        return append_snapshot(
            _run(
                published_at,
                attempt_key=attempt_key,
                quality=quality,
            ),
            (_candidate(published_at),),
            db_manager=db,
            session=session,
        )

    times = iter([PUBLISHED_AT, PUBLISHED_AT + timedelta(seconds=1)])
    publication = publish_cycle_snapshot_atomically(
        CYCLE_KEY,
        attempt_key,
        owner_key=owner,
        snapshot_factory=factory,
        guard_clock=lambda: next(times),
        terminal_payload={"quality": quality},
        db_manager=db,
    )
    snapshot = get_snapshot(
        publication.snapshot.snapshot_key,
        db_manager=db,
    )
    assert snapshot is not None
    return snapshot


def _seed_legacy(db):
    run = SnapshotRunInput(
        market_date_et=MARKET_DATE,
        run_type="morning_prior_close",
        signal_version="daily_completed_bars_v1",
        schema_version="1.1",
        freeze_policy_version="premarket_prior_close_xnys_v1",
        playbook_version="unverified",
        scope_key="web_daily_opportunity",
        universe=("AAPL",),
        requested_limit=1,
        source_run_id="opr_legacy",
        ranking_method="rule_based_evidence_count",
        strategy_validation_state="not_validated",
        as_of=PUBLISHED_AT,
        frozen_at=PUBLISHED_AT,
        payload={
            "schema_version": "1.1",
            "market_date_et": MARKET_DATE.isoformat(),
            "universe": ["AAPL"],
            "snapshot_meta": {},
        },
        validation_eligible=True,
    )
    result = append_snapshot(run, (_candidate(PUBLISHED_AT),), db_manager=db)
    snapshot = get_snapshot(result.snapshot_key, db_manager=db)
    assert snapshot is not None
    return snapshot


def _count(db, model) -> int:
    with db.get_session() as session:
        return int(session.execute(select(func.count(model.id))).scalar_one())


def test_verified_terminal_cycle_event_proves_canonical_publication(isolated_db):
    snapshot = _seed_canonical(isolated_db)

    verification = verify_canonical_publication(
        snapshot,
        db_manager=isolated_db,
    )

    assert verification.reason_codes == ()
    assert verification.proof is not None
    assert verification.proof.snapshot_key == snapshot.snapshot_key
    assert verification.proof.published_at == snapshot.frozen_at


def test_missing_cycle_evidence_stays_legacy_unverified(isolated_db):
    snapshot = _seed_legacy(isolated_db)

    verification = verify_canonical_publication(
        snapshot,
        db_manager=isolated_db,
    )
    bundle = classify_snapshot_qualification(
        snapshot,
        publication_verification=verification,
        calendar=_Calendar(),
    )

    assert verification.proof is None
    assert bundle.publication_state == PUBLICATION_LEGACY_UNVERIFIED


def test_dry_run_writes_no_qualification_rows_and_apply_is_idempotent(
    isolated_db,
):
    snapshot = _seed_canonical(isolated_db)
    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 0
    assert _count(isolated_db, OpportunityCandidateTrackAssessment) == 0

    plan = plan_qualification_backfill(
        db_manager=isolated_db,
        calendar=_Calendar(),
    )

    assert plan.snapshot_count == 1
    assert plan.candidate_track_count == 3
    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 0
    assert _count(isolated_db, OpportunityCandidateTrackAssessment) == 0

    first = apply_qualification_backfill(
        plan,
        db_manager=isolated_db,
        calendar=_Calendar(),
    )
    replay = apply_qualification_backfill(
        plan,
        db_manager=isolated_db,
        calendar=_Calendar(),
    )

    assert first.inserted_snapshot_count == 1
    assert first.duplicate_snapshot_count == 0
    assert replay.inserted_snapshot_count == 0
    assert replay.duplicate_snapshot_count == 1
    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 1
    assert _count(isolated_db, OpportunityCandidateTrackAssessment) == 3
    stored = get_snapshot_qualification(
        snapshot.snapshot_key,
        db_manager=isolated_db,
    )
    assert stored is not None
    assert stored.publication_state == PUBLICATION_CANONICAL
    assert stored.analysis_quality_state == ANALYSIS_DEGRADED
    by_track = {item.track_key: item for item in stored.candidate_tracks}
    assert by_track[TRACK_RAW_UNDERLYING_PATH].qualification_state == "qualified"
    assert (
        by_track[TRACK_UNDERLYING_DAILY_SELECTION].qualification_state
        == "qualified"
    )
    assert (
        by_track[TRACK_CANONICAL_FULL_RESEARCH].qualification_state
        == "excluded"
    )


def test_policy_versions_append_without_rewriting_old_assessment(isolated_db):
    snapshot = _seed_canonical(isolated_db, quality="ready")
    verification = verify_canonical_publication(
        snapshot,
        db_manager=isolated_db,
    )
    first_bundle = classify_snapshot_qualification(
        snapshot,
        publication_verification=verification,
        calendar=_Calendar(),
    )
    second_bundle = classify_snapshot_qualification(
        snapshot,
        publication_verification=verification,
        policy_version="opportunity_qualification_v3",
        calendar=_Calendar(),
    )

    first = append_qualification_bundle(
        first_bundle,
        db_manager=isolated_db,
    )
    second = append_qualification_bundle(
        second_bundle,
        db_manager=isolated_db,
    )

    assert first.duplicate is False
    assert second.duplicate is False
    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 2
    assert _count(isolated_db, OpportunityCandidateTrackAssessment) == 6
    stored = get_snapshot_qualification(
        snapshot.snapshot_key,
        policy_version="opportunity_qualification_v3",
        db_manager=isolated_db,
    )
    assert stored is not None
    assert stored.analysis_quality_state == ANALYSIS_READY


def test_same_policy_different_content_is_an_immutable_conflict(isolated_db):
    snapshot = _seed_canonical(isolated_db)
    verification = verify_canonical_publication(
        snapshot,
        db_manager=isolated_db,
    )
    bundle = classify_snapshot_qualification(
        snapshot,
        publication_verification=verification,
        calendar=_Calendar(),
    )
    append_qualification_bundle(bundle, db_manager=isolated_db)
    conflicting = replace(bundle, analysis_quality_state=ANALYSIS_READY)

    with pytest.raises(QualificationConflictError):
        append_qualification_bundle(
            conflicting,
            db_manager=isolated_db,
        )


def test_incomplete_candidate_track_bundle_is_rejected_atomically(isolated_db):
    snapshot = _seed_canonical(isolated_db)
    bundle = classify_snapshot_qualification(
        snapshot,
        publication_verification=verify_canonical_publication(
            snapshot,
            db_manager=isolated_db,
        ),
        calendar=_Calendar(),
    )
    incomplete = replace(
        bundle,
        candidate_tracks=bundle.candidate_tracks[:-1],
    )

    with pytest.raises(QualificationRepositoryError):
        append_qualification_bundle(incomplete, db_manager=isolated_db)

    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 0
    assert _count(isolated_db, OpportunityCandidateTrackAssessment) == 0


def test_fabricated_canonical_proof_is_rejected_before_insert(isolated_db):
    snapshot = _seed_legacy(isolated_db)
    bundle = classify_snapshot_qualification(
        snapshot,
        calendar=_Calendar(),
    )
    fabricated = replace(
        bundle,
        publication_state=PUBLICATION_CANONICAL,
        publication_proof=CanonicalPublicationProof(
            snapshot_key=snapshot.snapshot_key,
            cycle_key="missing_cycle",
            attempt_key="missing_attempt",
            event_sequence=1,
            published_at=snapshot.frozen_at,
        ),
    )

    with pytest.raises(
        QualificationRepositoryError,
        match="publication attempt does not exist",
    ):
        append_qualification_bundle(fabricated, db_manager=isolated_db)

    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 0


def test_candidate_identity_cannot_be_relabelled(isolated_db):
    snapshot = _seed_canonical(isolated_db)
    bundle = classify_snapshot_qualification(
        snapshot,
        publication_verification=verify_canonical_publication(
            snapshot,
            db_manager=isolated_db,
        ),
        calendar=_Calendar(),
    )
    first = replace(
        bundle.candidate_tracks[0],
        candidate_key="opc_fabricated",
        ticker="MSFT",
    )
    relabelled = replace(
        bundle,
        candidate_tracks=(first, *bundle.candidate_tracks[1:]),
    )

    with pytest.raises(
        QualificationRepositoryError,
        match="identity differs",
    ):
        append_qualification_bundle(relabelled, db_manager=isolated_db)

    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 0


def test_backfill_rejects_tampered_plan_before_writing(isolated_db):
    _seed_canonical(isolated_db)
    plan = plan_qualification_backfill(
        db_manager=isolated_db,
        calendar=_Calendar(),
    )
    tampered = replace(plan, plan_sha256="0" * 64)

    with pytest.raises(QualificationBackfillStaleError):
        apply_qualification_backfill(
            tampered,
            db_manager=isolated_db,
            calendar=_Calendar(),
        )

    assert _count(
        isolated_db,
        OpportunitySnapshotQualificationAssessment,
    ) == 0
    assert _count(isolated_db, OpportunityCandidateTrackAssessment) == 0


def test_sqlite_triggers_reject_update_and_delete(isolated_db):
    snapshot = _seed_canonical(isolated_db)
    plan = plan_qualification_backfill(
        db_manager=isolated_db,
        calendar=_Calendar(),
    )
    apply_qualification_backfill(
        plan,
        db_manager=isolated_db,
        calendar=_Calendar(),
    )

    with isolated_db.get_session() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(OpportunitySnapshotQualificationAssessment)
                .where(
                    OpportunitySnapshotQualificationAssessment.snapshot_run_id
                    == snapshot.id
                )
                .values(analysis_quality_state="ready")
            )
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                delete(OpportunityCandidateTrackAssessment)
            )
            session.commit()


def test_default_policy_constant_is_stable():
    assert QUALIFICATION_POLICY_VERSION == "opportunity_qualification_v2"
