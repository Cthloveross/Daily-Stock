# -*- coding: utf-8 -*-
"""Persistence contracts for immutable opportunity research snapshots."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import IntegrityError

from src.opportunities.models import (
    OpportunityCandidateOutcome,
    OpportunitySnapshotCandidate,
    OpportunitySnapshotRun,
)
from src.opportunities.repository import (
    CandidateOutcomeInput,
    OpportunityRepositoryError,
    OutcomeConflictError,
    SnapshotCandidateInput,
    SnapshotConflictError,
    SnapshotRunInput,
    append_candidate_outcome,
    append_snapshot,
    build_snapshot_key,
    get_preferred_candidate_outcome,
    get_snapshot,
    init_opportunity_schema,
    list_candidate_outcomes,
    list_snapshot_outcomes,
    list_snapshots,
)
from src.storage import DatabaseManager


UTC = timezone.utc


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "opportunity_repository.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    import src.config as config_module

    # Keep this focused suite independent of optional provider configuration.
    monkeypatch.setattr(
        config_module.Config,
        "_parse_stock_email_groups",
        classmethod(lambda _cls: []),
    )
    config_module.Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    yield db, db_path
    DatabaseManager.reset_instance()
    config_module.Config.reset_instance()


def _run(
    *,
    payload_note: str = "frozen",
    as_of: datetime = datetime(2026, 7, 22, 13, 20, tzinfo=UTC),
    source_run_id: str = "opr_source_1",
) -> SnapshotRunInput:
    return SnapshotRunInput(
        market_date_et=date(2026, 7, 22),
        run_type="morning_prior_close",
        signal_version="daily_completed_bars_v1",
        schema_version="1.1",
        freeze_policy_version="preopen_xnys_v1",
        playbook_version="unverified",
        scope_key="default_us_options_watchlist",
        universe=("MSFT", "AAPL"),
        requested_limit=2,
        source_run_id=source_run_id,
        ranking_method="rule_based_evidence_count",
        strategy_validation_state="not_validated",
        as_of=as_of,
        payload={
            "schema_version": "1.1",
            "market_date_et": "2026-07-22",
            "note": payload_note,
            "candidates": ["AAPL", "MSFT"],
        },
        validation_eligible=True,
        eligibility_reasons=(),
    )


def _candidates(
    *,
    first_direction: str = "bullish",
    first_reference_close: Decimal = Decimal("210.125"),
) -> tuple[SnapshotCandidateInput, ...]:
    return (
        SnapshotCandidateInput(
            ticker="AAPL",
            rank=1,
            research_state="watch_only",
            directional_context=first_direction,
            supporting_evidence_count=4,
            data_completeness_state="complete",
            source_candidate_id="volatile_candidate_aapl",
            reference_session_date=date(2026, 7, 21),
            reference_close=first_reference_close,
            reference_source="yfinance",
            validation_eligible=True,
            payload={"ticker": "AAPL", "rank": 1, "evidence": ["ema"]},
        ),
        SnapshotCandidateInput(
            ticker="MSFT",
            rank=2,
            research_state="watch_only",
            directional_context="mixed",
            supporting_evidence_count=2,
            data_completeness_state="complete",
            source_candidate_id="volatile_candidate_msft",
            reference_session_date=date(2026, 7, 21),
            reference_close=Decimal("510.50"),
            reference_source="yfinance",
            validation_eligible=True,
            payload={"ticker": "MSFT", "rank": 2, "evidence": []},
        ),
    )


def _seed_snapshot(db, *, first_direction: str = "bullish"):
    result = append_snapshot(
        _run(),
        _candidates(first_direction=first_direction),
        db_manager=db,
    )
    snapshot = get_snapshot(result.snapshot_key, db_manager=db)
    assert snapshot is not None
    return snapshot


def _outcome(
    *,
    result_state: str = "complete",
    close_return_pct: Decimal = Decimal("5.50000000"),
    signed: bool = True,
) -> CandidateOutcomeInput:
    return CandidateOutcomeInput(
        horizon_sessions=5,
        evaluator_version="xnys_underlying_path_v1",
        result_state=result_state,
        context_label="CONTEXT_HIT" if signed else "NON_DIRECTIONAL",
        reference_session_date=date(2026, 7, 21),
        reference_close=Decimal("210.125"),
        refetched_reference_close=Decimal("210.125"),
        entry_session_date=date(2026, 7, 22),
        entry_open=Decimal("211.00"),
        target_session_date=date(2026, 7, 28),
        target_close_at=datetime(2026, 7, 28, 20, 0, tzinfo=UTC),
        window_start_date=date(2026, 7, 22),
        window_end_date=date(2026, 7, 28),
        end_close=Decimal("221.682"),
        max_high=Decimal("225.00"),
        min_low=Decimal("207.00"),
        close_return_pct=close_return_pct,
        entry_proxy_return_pct=Decimal("5.06255924"),
        max_high_return_pct=Decimal("7.07911957"),
        min_low_return_pct=Decimal("-1.48720999"),
        signed_close_return_pct=(close_return_pct if signed else None),
        signed_entry_return_pct=(Decimal("5.06255924") if signed else None),
        mfe_pct=(Decimal("7.07911957") if signed else None),
        mae_pct=(Decimal("-1.48720999") if signed else None),
        spy_reference_close=Decimal("630.00"),
        spy_refetched_reference_close=Decimal("630.00"),
        spy_entry_open=Decimal("631.00"),
        spy_end_close=Decimal("636.30"),
        spy_close_return_pct=Decimal("1.00000000"),
        spy_entry_proxy_return_pct=Decimal("0.83993661"),
        excess_close_return_pct=Decimal("4.50000000"),
        excess_entry_proxy_return_pct=Decimal("4.22262263"),
        signed_excess_spy_pct=(Decimal("4.50000000") if signed else None),
        input_bars_sha256="a" * 64,
        benchmark_bars_sha256="b" * 64,
        provenance={"calendar": "XNYS", "source": "stock_daily"},
    )


def _count(db, model) -> int:
    with db.get_session() as session:
        return int(session.execute(select(func.count(model.id))).scalar_one())


def test_schema_is_reentrant_and_installs_all_append_only_triggers(isolated_db):
    db, _ = isolated_db
    init_opportunity_schema(db)
    init_opportunity_schema(db)

    with db.get_session() as session:
        tables = set(
            session.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name LIKE 'opportunity_%'"
                )
            ).scalars()
        )
        triggers = set(
            session.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND name LIKE 'trg_opportunity_%'"
                )
            ).scalars()
        )

    snapshot_tables = {
        "opportunity_snapshot_runs",
        "opportunity_snapshot_candidates",
        "opportunity_candidate_outcomes",
    }
    assert snapshot_tables <= tables
    for table_name in snapshot_tables:
        assert f"trg_{table_name}_update_immutable" in triggers
        assert f"trg_{table_name}_delete_immutable" in triggers


def test_snapshot_exact_retry_is_idempotent_and_readable(isolated_db):
    db, _ = isolated_db
    run = _run()
    candidates = _candidates()

    first = append_snapshot(run, candidates, db_manager=db)
    duplicate = append_snapshot(
        replace(
            run,
            # Volatile provenance is not part of the daily slot identity.  An
            # identical frozen payload remains an exact retry.
            source_run_id="opr_retry_after_response_loss",
            as_of=run.as_of + timedelta(seconds=5),
        ),
        candidates,
        db_manager=db,
    )

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.snapshot_run_id == first.snapshot_run_id
    assert build_snapshot_key(run) == duplicate.snapshot_key
    assert _count(db, OpportunitySnapshotRun) == 1
    assert _count(db, OpportunitySnapshotCandidate) == 2

    stored = get_snapshot(first.snapshot_key, db_manager=db)
    assert stored is not None
    assert stored.payload["note"] == "frozen"
    assert [candidate.ticker for candidate in stored.candidates] == [
        "AAPL",
        "MSFT",
    ]
    assert stored.candidates[0].reference_close == Decimal("210.1250000000")


def test_daily_slot_rejects_different_run_or_candidate_payload(isolated_db):
    db, _ = isolated_db
    run = _run()
    candidates = _candidates()
    append_snapshot(run, candidates, db_manager=db)

    with pytest.raises(SnapshotConflictError, match="different immutable payload"):
        append_snapshot(
            replace(run, payload={**run.payload, "note": "changed-after-freeze"}),
            candidates,
            db_manager=db,
        )

    changed_candidates = _candidates(
        first_reference_close=Decimal("211.00")
    )
    with pytest.raises(SnapshotConflictError, match="candidate bundle differs"):
        append_snapshot(run, changed_candidates, db_manager=db)

    assert _count(db, OpportunitySnapshotRun) == 1
    assert _count(db, OpportunitySnapshotCandidate) == 2


def test_run_and_candidates_roll_back_as_one_transaction(isolated_db):
    db, _ = isolated_db

    def fail_second_candidate(_mapper, _connection, target):
        if int(target.rank) == 2:
            raise RuntimeError("injected candidate failure")

    event.listen(
        OpportunitySnapshotCandidate,
        "before_insert",
        fail_second_candidate,
    )
    try:
        with pytest.raises(RuntimeError, match="injected candidate failure"):
            append_snapshot(_run(), _candidates(), db_manager=db)
    finally:
        event.remove(
            OpportunitySnapshotCandidate,
            "before_insert",
            fail_second_candidate,
        )

    assert _count(db, OpportunitySnapshotRun) == 0
    assert _count(db, OpportunitySnapshotCandidate) == 0


def test_database_rejects_update_delete_and_insert_or_replace(isolated_db):
    db, _ = isolated_db
    snapshot = _seed_snapshot(db)
    candidate_key = snapshot.candidates[0].candidate_key
    append_candidate_outcome(candidate_key, _outcome(), db_manager=db)

    models = (
        OpportunitySnapshotRun,
        OpportunitySnapshotCandidate,
        OpportunityCandidateOutcome,
    )
    for model in models:
        with pytest.raises(IntegrityError, match="append-only"):
            with db.session_scope() as session:
                session.execute(update(model).values(id=model.id))
        with pytest.raises(IntegrityError, match="append-only"):
            with db.session_scope() as session:
                session.execute(delete(model))

    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO opportunity_snapshot_runs "
                    "SELECT * FROM opportunity_snapshot_runs WHERE id = 1"
                )
            )

    assert _count(db, OpportunitySnapshotRun) == 1
    assert _count(db, OpportunitySnapshotCandidate) == 2
    assert _count(db, OpportunityCandidateOutcome) == 1


def test_outcome_append_is_idempotent_and_conflicts_on_changed_result(isolated_db):
    db, _ = isolated_db
    snapshot = _seed_snapshot(db)
    candidate_key = snapshot.candidates[0].candidate_key
    outcome = _outcome()

    first = append_candidate_outcome(candidate_key, outcome, db_manager=db)
    duplicate = append_candidate_outcome(candidate_key, outcome, db_manager=db)

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.outcome_id == first.outcome_id
    assert _count(db, OpportunityCandidateOutcome) == 1

    with pytest.raises(OutcomeConflictError, match="different immutable result"):
        append_candidate_outcome(
            candidate_key,
            replace(outcome, close_return_pct=Decimal("6.00")),
            db_manager=db,
        )
    assert _count(db, OpportunityCandidateOutcome) == 1


def test_partial_does_not_block_complete_and_reader_prefers_complete(isolated_db):
    db, _ = isolated_db
    snapshot = _seed_snapshot(db)
    candidate_key = snapshot.candidates[0].candidate_key

    partial = replace(
        _outcome(result_state="partial"),
        entry_session_date=None,
        entry_open=None,
        entry_proxy_return_pct=None,
        signed_entry_return_pct=None,
        mfe_pct=None,
        mae_pct=None,
        spy_reference_close=None,
        spy_entry_open=None,
        spy_end_close=None,
        spy_close_return_pct=None,
        spy_entry_proxy_return_pct=None,
        excess_close_return_pct=None,
        excess_entry_proxy_return_pct=None,
        signed_excess_spy_pct=None,
        benchmark_bars_sha256=None,
    )
    append_candidate_outcome(candidate_key, partial, db_manager=db)
    append_candidate_outcome(candidate_key, _outcome(), db_manager=db)

    assert _count(db, OpportunityCandidateOutcome) == 2
    preferred = get_preferred_candidate_outcome(
        candidate_key,
        horizon_sessions=5,
        evaluator_version="xnys_underlying_path_v1",
        db_manager=db,
    )
    assert preferred is not None
    assert preferred.result_state == "complete"
    assert preferred.result["close_return_pct"] == "5.50000000"


def test_pending_outcome_is_not_persisted(isolated_db):
    db, _ = isolated_db
    snapshot = _seed_snapshot(db)
    candidate_key = snapshot.candidates[0].candidate_key

    with pytest.raises(OpportunityRepositoryError, match="do not persist a pending"):
        append_candidate_outcome(
            candidate_key,
            replace(_outcome(), window_end_date=None, end_close=None),
            db_manager=db,
        )
    assert _count(db, OpportunityCandidateOutcome) == 0


def test_mixed_or_unknown_context_forbids_signed_mfe_mae(isolated_db):
    db, _ = isolated_db
    snapshot = _seed_snapshot(db, first_direction="mixed")
    candidate_key = snapshot.candidates[0].candidate_key

    with pytest.raises(
        OpportunityRepositoryError,
        match="signed returns, MFE, and MAE null",
    ):
        append_candidate_outcome(candidate_key, _outcome(), db_manager=db)

    neutral = _outcome(signed=False)
    result = append_candidate_outcome(candidate_key, neutral, db_manager=db)
    assert result.duplicate is False
    assert _count(db, OpportunityCandidateOutcome) == 1


def test_list_snapshots_is_newest_first_and_includes_candidates(isolated_db):
    db, _ = isolated_db
    older_run = replace(
        _run(),
        market_date_et=date(2026, 7, 21),
        frozen_at=datetime(2026, 7, 21, 13, 20, tzinfo=UTC),
        payload={**_run().payload, "market_date_et": "2026-07-21"},
    )
    newer_run = replace(
        _run(),
        frozen_at=datetime(2026, 7, 22, 13, 20, tzinfo=UTC),
    )
    older = append_snapshot(older_run, _candidates(), db_manager=db)
    newer = append_snapshot(newer_run, _candidates(), db_manager=db)

    rows = list_snapshots(limit=2, db_manager=db)

    assert [row.snapshot_key for row in rows] == [
        newer.snapshot_key,
        older.snapshot_key,
    ]
    assert [candidate.ticker for candidate in rows[0].candidates] == [
        "AAPL",
        "MSFT",
    ]
    assert list_snapshots(limit=1, db_manager=db)[0].snapshot_key == newer.snapshot_key
    with pytest.raises(OpportunityRepositoryError, match="limit"):
        list_snapshots(limit=0, db_manager=db)


def test_outcome_lists_are_repository_backed_associated_and_preferred(isolated_db):
    db, _ = isolated_db
    snapshot = _seed_snapshot(db)
    candidate_key = snapshot.candidates[0].candidate_key
    partial = replace(
        _outcome(result_state="partial"),
        entry_session_date=None,
        entry_open=None,
        entry_proxy_return_pct=None,
        signed_entry_return_pct=None,
        mfe_pct=None,
        mae_pct=None,
    )
    append_candidate_outcome(candidate_key, partial, db_manager=db)
    append_candidate_outcome(candidate_key, _outcome(), db_manager=db)
    append_candidate_outcome(
        candidate_key,
        replace(
            _outcome(),
            evaluator_version="xnys_underlying_path_v2",
        ),
        db_manager=db,
    )

    snapshot_rows = list_snapshot_outcomes(
        snapshot.snapshot_key,
        evaluator_version="xnys_underlying_path_v1",
        db_manager=db,
    )
    assert len(snapshot_rows) == 1
    assert snapshot_rows[0].result_state == "complete"
    assert snapshot_rows[0].snapshot_key == snapshot.snapshot_key
    assert snapshot_rows[0].market_date_et == date(2026, 7, 22)
    assert snapshot_rows[0].ticker == "AAPL"
    assert snapshot_rows[0].rank == 1
    assert snapshot_rows[0].directional_context == "bullish"

    all_rows = list_candidate_outcomes(db_manager=db)
    assert [(row.evaluator_version, row.result_state) for row in all_rows] == [
        ("xnys_underlying_path_v1", "complete"),
        ("xnys_underlying_path_v2", "complete"),
    ]
    v2_rows = list_candidate_outcomes(
        evaluator_version="xnys_underlying_path_v2",
        db_manager=db,
    )
    assert len(v2_rows) == 1
    assert v2_rows[0].candidate_key == candidate_key
