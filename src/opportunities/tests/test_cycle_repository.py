# -*- coding: utf-8 -*-
"""Persistence contracts for the append-only premarket cycle repository."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import threading

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from src.opportunities.cycle_models import (
    OpportunityPremarketCycleAttempt,
    OpportunityPremarketCycleEvent,
    OpportunityPremarketCycleSlot,
    OpportunityResearchUniverseVersion,
)
from src.opportunities.models import (
    OpportunitySnapshotCandidate,
    OpportunitySnapshotRun,
)
from src.opportunities import cycle_repository as cycle_repository_module
from src.opportunities.cycle_repository import (
    PremarketCycleRepositoryError,
    PremarketAttemptLeaseLostError,
    PremarketPublicationContractError,
    PremarketPublicationDeadlineError,
    append_cycle_event,
    append_research_universe,
    claim_cycle_attempt,
    count_cycle_attempts,
    get_latest_cycle_attempt_status,
    get_latest_research_universe,
    init_premarket_cycle_schema,
    publish_cycle_snapshot_atomically,
)
from src.opportunities.repository import (
    SnapshotCandidateInput,
    SnapshotRunInput,
    append_snapshot,
    get_snapshot,
    init_opportunity_schema,
)
from src.storage import DatabaseManager


UTC = timezone.utc
SCOPE_KEY = "default_us_options_watchlist"
MARKET_DATE_ET = date(2026, 7, 24)
CYCLE_KEY = "premarket:2026-07-24:default_us_options_watchlist"
STARTED_AT = datetime(2026, 7, 24, 13, 12, tzinfo=UTC)
HARD_DEADLINE_AT = datetime(2026, 7, 24, 13, 20, tzinfo=UTC)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "premarket_cycle_repository.db"
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
    yield db, db_path
    DatabaseManager.reset_instance()
    config_module.Config.reset_instance()


def _count(db, model) -> int:
    with db.get_session() as session:
        return int(session.execute(select(func.count(model.id))).scalar_one())


def _claim(
    db,
    *,
    owner_key: str,
    now: datetime = STARTED_AT,
    lease_seconds: int = 90,
    max_attempts: int = 2,
):
    return claim_cycle_attempt(
        cycle_key=CYCLE_KEY,
        market_date_et=MARKET_DATE_ET,
        scope_key=SCOPE_KEY,
        freeze_policy_version="preopen_xnys_v2",
        cycle_version="canonical_premarket_v1",
        universe=("AAPL", "MSFT"),
        requested_limit=2,
        universe_source="persisted",
        universe_version_key="opu_test_universe",
        trigger="scheduler",
        owner_key=owner_key,
        hard_deadline_at=HARD_DEADLINE_AT,
        now=now,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
        db_manager=db,
    )


def _snapshot_run(published_at: datetime) -> SnapshotRunInput:
    return SnapshotRunInput(
        market_date_et=MARKET_DATE_ET,
        run_type="morning_prior_close",
        signal_version="daily_completed_bars_v1",
        schema_version="1.1",
        freeze_policy_version="preopen_xnys_v2",
        playbook_version="unverified",
        scope_key=SCOPE_KEY,
        universe=("AAPL", "MSFT"),
        requested_limit=2,
        source_run_id="opr_atomic_publication",
        ranking_method="rule_based_evidence_count",
        strategy_validation_state="not_validated",
        as_of=STARTED_AT,
        frozen_at=published_at,
        payload={
            "schema_version": "1.1",
            "market_date_et": MARKET_DATE_ET.isoformat(),
            "universe": ["AAPL", "MSFT"],
            "requested_limit": 2,
            "candidates": [{"ticker": "AAPL"}],
            "canonical_cycle": {
                "cycle_key": CYCLE_KEY,
                "attempt_key": "",
                "published_at": published_at.isoformat(),
            },
            "snapshot_meta": {
                "frozen_at": published_at.isoformat(),
            },
        },
        validation_eligible=True,
    )


def _snapshot_candidates(
    published_at: datetime,
) -> tuple[SnapshotCandidateInput, ...]:
    return (
        SnapshotCandidateInput(
            ticker="AAPL",
            rank=1,
            research_state="watch_only",
            directional_context="bullish",
            supporting_evidence_count=4,
            data_completeness_state="complete",
            payload={
                "ticker": "AAPL",
                "rank": 1,
                "outcome_validation": {
                    "frozen_at": published_at.isoformat(),
                },
            },
            source_candidate_id="opc_atomic_aapl",
            reference_session_date=date(2026, 7, 23),
            reference_close=Decimal("225.25"),
            reference_source="fixture",
            validation_eligible=True,
        ),
    )


def _snapshot_factory(db, attempt_key: str):
    def factory(session, published_at):
        run = _snapshot_run(published_at)
        run_payload = dict(run.payload)
        run_payload["canonical_cycle"] = {
            **dict(run_payload["canonical_cycle"]),
            "attempt_key": attempt_key,
        }
        run = SnapshotRunInput(
            **{
                **run.__dict__,
                "payload": run_payload,
            }
        )
        return append_snapshot(
            run,
            _snapshot_candidates(published_at),
            db_manager=db,
            session=session,
        )

    return factory


def _attempt_events(db, attempt_key: str):
    with db.get_session() as session:
        attempt = session.execute(
            select(OpportunityPremarketCycleAttempt).where(
                OpportunityPremarketCycleAttempt.attempt_key == attempt_key
            )
        ).scalar_one()
        return session.execute(
            select(OpportunityPremarketCycleEvent)
            .where(OpportunityPremarketCycleEvent.attempt_id == attempt.id)
            .order_by(OpportunityPremarketCycleEvent.sequence)
        ).scalars().all()


def test_universe_append_is_idempotent_and_latest_is_persistent(isolated_db):
    db, _ = isolated_db

    first, first_duplicate = append_research_universe(
        ("aapl", "US.MSFT", "AAPL"),
        2,
        scope_key=SCOPE_KEY,
        source="api",
        created_at=STARTED_AT,
        db_manager=db,
    )
    duplicate, duplicate_flag = append_research_universe(
        ("AAPL", "MSFT"),
        2,
        scope_key=SCOPE_KEY,
        source="scheduler",
        created_at=STARTED_AT + timedelta(seconds=10),
        db_manager=db,
    )
    latest_written, latest_duplicate = append_research_universe(
        ("TSLA", "NVDA"),
        1,
        scope_key=SCOPE_KEY,
        source="api",
        created_at=STARTED_AT + timedelta(seconds=20),
        db_manager=db,
    )

    assert first_duplicate is False
    assert first.symbols == ("AAPL", "MSFT")
    assert duplicate_flag is True
    assert duplicate == first
    assert duplicate.source == "api"
    assert latest_duplicate is False
    assert _count(db, OpportunityResearchUniverseVersion) == 2

    latest = get_latest_research_universe(
        scope_key=SCOPE_KEY,
        db_manager=db,
    )
    assert latest == latest_written
    assert latest.symbols == ("TSLA", "NVDA")
    assert latest.requested_limit == 1

    reactivated, reactivated_duplicate = append_research_universe(
        ("AAPL", "MSFT"),
        2,
        scope_key=SCOPE_KEY,
        source="api",
        created_at=STARTED_AT + timedelta(seconds=30),
        db_manager=db,
    )

    assert reactivated_duplicate is False
    assert reactivated.universe_version_key != first.universe_version_key
    assert _count(db, OpportunityResearchUniverseVersion) == 3
    assert get_latest_research_universe(
        scope_key=SCOPE_KEY,
        db_manager=db,
    ) == reactivated

    backdated, backdated_duplicate = append_research_universe(
        ("META",),
        1,
        scope_key=SCOPE_KEY,
        source="api",
        created_at=STARTED_AT - timedelta(days=1),
        db_manager=db,
    )

    assert backdated_duplicate is False
    assert _count(db, OpportunityResearchUniverseVersion) == 4
    assert get_latest_research_universe(
        scope_key=SCOPE_KEY,
        db_manager=db,
    ) == backdated


def test_repository_rejects_non_us_option_underlyings(isolated_db):
    db, _ = isolated_db

    with pytest.raises(
        PremarketCycleRepositoryError,
        match="unsupported US option",
    ):
        append_research_universe(
            ("600519", "AAPL"),
            2,
            scope_key=SCOPE_KEY,
            source="api",
            created_at=STARTED_AT,
            db_manager=db,
        )

    assert _count(db, OpportunityResearchUniverseVersion) == 0


def test_cycle_schema_is_reentrant_and_all_new_rows_are_append_only(
    isolated_db,
):
    db, _ = isolated_db
    init_premarket_cycle_schema(db)
    init_premarket_cycle_schema(db)
    append_research_universe(
        ("AAPL", "MSFT"),
        2,
        scope_key=SCOPE_KEY,
        created_at=STARTED_AT,
        db_manager=db,
    )
    _claim(db, owner_key="worker-a")

    cycle_tables = {
        "opportunity_research_universe_versions",
        "opportunity_premarket_cycle_slots",
        "opportunity_premarket_cycle_attempts",
        "opportunity_premarket_cycle_events",
    }
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

    assert cycle_tables <= tables
    for table_name in cycle_tables:
        assert f"trg_{table_name}_update_immutable" in triggers
        assert f"trg_{table_name}_delete_immutable" in triggers
        for statement in (
            f"UPDATE {table_name} SET id=id WHERE id=(SELECT MIN(id) FROM {table_name})",
            f"DELETE FROM {table_name} WHERE id=(SELECT MIN(id) FROM {table_name})",
        ):
            with pytest.raises(IntegrityError, match="append-only"):
                with db._engine.begin() as connection:
                    connection.exec_driver_sql(statement)


def test_live_lease_cannot_be_stolen(isolated_db):
    db, _ = isolated_db
    first = _claim(db, owner_key="worker-a")

    contender = _claim(
        db,
        owner_key="worker-b",
        now=STARTED_AT + timedelta(seconds=30),
    )

    assert first.claimed is True
    assert contender.claimed is False
    assert contender.attempt.attempt_key == first.attempt.attempt_key
    assert contender.attempt.owner_key == "worker-a"
    assert contender.status.state == "running"
    assert count_cycle_attempts(CYCLE_KEY, db_manager=db) == 1


def test_two_connections_claim_only_one_first_attempt(isolated_db):
    db, _ = isolated_db
    init_premarket_cycle_schema(db)
    barrier = threading.Barrier(2)
    checkout_lock = threading.Lock()
    checked_out_connections = set()

    def record_checkout(dbapi_connection, *_args):
        with checkout_lock:
            checked_out_connections.add(id(dbapi_connection))

    event.listen(db._engine, "checkout", record_checkout)
    try:
        def contend(owner_key):
            barrier.wait(timeout=2)
            return _claim(db, owner_key=owner_key)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(contend, ("worker-a", "worker-b"))
            )
    finally:
        event.remove(db._engine, "checkout", record_checkout)

    assert sorted(result.claimed for result in results) == [False, True]
    assert len({result.attempt.attempt_key for result in results}) == 1
    assert len(checked_out_connections) >= 2
    assert _count(db, OpportunityPremarketCycleSlot) == 1
    assert count_cycle_attempts(CYCLE_KEY, db_manager=db) == 1


def test_unique_slot_conflict_is_reread_before_claim(
    isolated_db,
    monkeypatch,
):
    db, _ = isolated_db
    init_premarket_cycle_schema(db)
    # Exercise the non-SQLite savepoint/conflict/re-read branch against the
    # same relational uniqueness contract; SQLite simply ignores FOR UPDATE.
    monkeypatch.setattr(db, "_is_sqlite_engine", False)

    first = _claim(db, owner_key="worker-a")
    contender = _claim(
        db,
        owner_key="worker-b",
        now=STARTED_AT + timedelta(seconds=1),
    )

    assert first.claimed is True
    assert contender.claimed is False
    assert contender.attempt.attempt_key == first.attempt.attempt_key
    assert _count(db, OpportunityPremarketCycleSlot) == 1
    assert count_cycle_attempts(CYCLE_KEY, db_manager=db) == 1


def test_claim_budget_is_capped_at_two_attempts(isolated_db):
    db, _ = isolated_db
    first = _claim(db, owner_key="worker-a")
    append_cycle_event(
        first.attempt.attempt_key,
        owner_key="worker-a",
        event_type="attempt_finished",
        state="failed",
        error_code="provider_unavailable",
        now=STARTED_AT + timedelta(seconds=10),
        db_manager=db,
    )

    second = _claim(
        db,
        owner_key="worker-b",
        now=STARTED_AT + timedelta(seconds=20),
    )
    assert second.claimed is True
    assert second.attempt.recovered_from_attempt_key == first.attempt.attempt_key
    append_cycle_event(
        second.attempt.attempt_key,
        owner_key="worker-b",
        event_type="attempt_finished",
        state="blocked",
        error_code="quality_gate_blocked",
        now=STARTED_AT + timedelta(seconds=30),
        db_manager=db,
    )

    third = _claim(
        db,
        owner_key="worker-c",
        now=STARTED_AT + timedelta(seconds=40),
    )

    assert third.claimed is False
    assert third.attempt.attempt_key == second.attempt.attempt_key
    assert third.status.state == "blocked"
    assert count_cycle_attempts(CYCLE_KEY, db_manager=db) == 2


def test_backdated_recovery_is_latest_by_persisted_revision(isolated_db):
    db, _ = isolated_db
    first_started_at = STARTED_AT + timedelta(minutes=4)
    first = _claim(
        db,
        owner_key="worker-a",
        now=first_started_at,
    )
    append_cycle_event(
        first.attempt.attempt_key,
        owner_key="worker-a",
        event_type="attempt_finished",
        state="failed",
        error_code="provider_unavailable",
        now=first_started_at + timedelta(seconds=10),
        db_manager=db,
    )

    # Simulate a corrected/backward-moving wall clock.  Persistence order,
    # not started_at, must determine the current owner.
    second = _claim(
        db,
        owner_key="worker-b",
        now=STARTED_AT + timedelta(minutes=3),
    )
    status = get_latest_cycle_attempt_status(
        CYCLE_KEY,
        now=STARTED_AT + timedelta(minutes=5),
        db_manager=db,
    )

    assert second.claimed is True
    assert second.attempt.started_at < first.attempt.started_at
    assert second.attempt.recovered_from_attempt_key == first.attempt.attempt_key
    assert status is not None
    assert status.attempt.attempt_key == second.attempt.attempt_key
    with pytest.raises(
        PremarketAttemptLeaseLostError,
        match="no longer the current owner",
    ):
        append_cycle_event(
            first.attempt.attempt_key,
            owner_key="worker-a",
            event_type="stage_finished",
            stage="late",
            state="completed",
            now=STARTED_AT + timedelta(minutes=3, seconds=30),
            db_manager=db,
        )


def test_append_event_locks_cycle_slot_before_reading_sequence(
    isolated_db,
    monkeypatch,
):
    db, _ = isolated_db
    claim = _claim(db, owner_key="worker-a")
    original_lock = cycle_repository_module._lock_existing_cycle_slot
    original_events_for = cycle_repository_module._events_for
    lock_held = False

    def record_slot_lock(session, *, db, cycle_key):
        nonlocal lock_held
        slot = original_lock(session, db=db, cycle_key=cycle_key)
        lock_held = True
        return slot

    def require_slot_lock(session, attempt_id):
        assert lock_held is True
        return original_events_for(session, attempt_id)

    # Exercise the PostgreSQL/MySQL code path. SQLite accepts SELECT FOR
    # UPDATE as a no-op, while the call-order assertion verifies that event
    # sequence allocation occurs only after the shared cycle lock.
    monkeypatch.setattr(db, "_is_sqlite_engine", False)
    monkeypatch.setattr(
        cycle_repository_module,
        "_lock_existing_cycle_slot",
        record_slot_lock,
    )
    monkeypatch.setattr(
        cycle_repository_module,
        "_events_for",
        require_slot_lock,
    )

    status = append_cycle_event(
        claim.attempt.attempt_key,
        owner_key="worker-a",
        event_type="stage_finished",
        stage="scan",
        state="completed",
        now=STARTED_AT + timedelta(seconds=1),
        db_manager=db,
    )

    assert status.state == "running"
    assert status.events[-1].sequence == 2


def test_expired_attempt_is_closed_before_recovery_and_old_owner_is_rejected(
    isolated_db,
):
    db, _ = isolated_db
    first = _claim(
        db,
        owner_key="worker-a",
        lease_seconds=90,
    )

    second = _claim(
        db,
        owner_key="worker-b",
        now=STARTED_AT + timedelta(seconds=91),
        lease_seconds=90,
    )

    assert second.claimed is True
    assert second.attempt.recovered_from_attempt_key == first.attempt.attempt_key
    with db.get_session() as session:
        first_row = session.execute(
            select(OpportunityPremarketCycleAttempt).where(
                OpportunityPremarketCycleAttempt.attempt_key
                == first.attempt.attempt_key
            )
        ).scalar_one()
        first_events = session.execute(
            select(OpportunityPremarketCycleEvent)
            .where(OpportunityPremarketCycleEvent.attempt_id == first_row.id)
            .order_by(OpportunityPremarketCycleEvent.sequence)
        ).scalars().all()

    assert [event.event_type for event in first_events] == [
        "attempt_started",
        "attempt_finished",
    ]
    assert first_events[-1].state == "failed"
    assert first_events[-1].error_code == "attempt_lease_expired"
    assert first_events[-1].actor_key == "worker-b"

    with pytest.raises(
        PremarketAttemptLeaseLostError,
        match="no longer the current owner",
    ):
        append_cycle_event(
            first.attempt.attempt_key,
            owner_key="worker-a",
            event_type="stage_finished",
            stage="scan",
            state="completed",
            now=STARTED_AT + timedelta(seconds=92),
            db_manager=db,
        )


def test_status_is_reconstructed_after_database_manager_restart(isolated_db):
    db, _ = isolated_db
    claim = _claim(db, owner_key="worker-a")
    append_cycle_event(
        claim.attempt.attempt_key,
        owner_key="worker-a",
        event_type="stage_started",
        stage="scan",
        state="running",
        payload={"universe_size": 2},
        now=STARTED_AT + timedelta(seconds=10),
        db_manager=db,
    )
    append_cycle_event(
        claim.attempt.attempt_key,
        owner_key="worker-a",
        event_type="stage_finished",
        stage="scan",
        state="completed",
        payload={"candidate_count": 2},
        now=STARTED_AT + timedelta(seconds=20),
        db_manager=db,
    )
    append_cycle_event(
        claim.attempt.attempt_key,
        owner_key="worker-a",
        event_type="attempt_finished",
        state="blocked",
        error_code="quality_gate_blocked",
        payload={"reasons": ["benchmark_unavailable"]},
        now=STARTED_AT + timedelta(seconds=30),
        db_manager=db,
    )

    DatabaseManager.reset_instance()
    restarted_db = DatabaseManager.get_instance()
    status = get_latest_cycle_attempt_status(
        CYCLE_KEY,
        now=STARTED_AT + timedelta(minutes=2),
        db_manager=restarted_db,
    )

    assert status is not None
    assert status.attempt.attempt_key == claim.attempt.attempt_key
    assert status.state == "blocked"
    assert status.error_code == "quality_gate_blocked"
    assert status.recoverable is True
    assert len(status.events) == 4
    assert status.events[-1].payload == {
        "reasons": ["benchmark_unavailable"]
    }
    assert status.stages == (
        {
            "name": "scan",
            "state": "completed",
            "started_at": (
                STARTED_AT + timedelta(seconds=10)
            ).isoformat(),
            "completed_at": (
                STARTED_AT + timedelta(seconds=20)
            ).isoformat(),
            "error_code": None,
        },
    )


def test_atomic_publication_succeeds_at_091959(isolated_db):
    db, _ = isolated_db
    claim = _claim(
        db,
        owner_key="worker-a",
        lease_seconds=600,
    )
    published_at = HARD_DEADLINE_AT - timedelta(seconds=1)

    result = publish_cycle_snapshot_atomically(
        CYCLE_KEY,
        claim.attempt.attempt_key,
        owner_key="worker-a",
        snapshot_factory=_snapshot_factory(
            db,
            claim.attempt.attempt_key,
        ),
        guard_clock=lambda: published_at,
        terminal_payload={"quality": "ready"},
        db_manager=db,
    )

    assert result.published_at == published_at
    assert result.status.state == "published"
    assert result.snapshot.duplicate is False
    stored = get_snapshot(result.snapshot.snapshot_key, db_manager=db)
    assert stored is not None
    expected_timestamp = published_at.isoformat()
    assert stored.frozen_at == published_at
    assert (
        stored.payload["canonical_cycle"]["published_at"]
        == expected_timestamp
    )
    assert stored.payload["snapshot_meta"]["frozen_at"] == expected_timestamp
    assert (
        stored.candidates[0].payload["outcome_validation"]["frozen_at"]
        == expected_timestamp
    )
    events = _attempt_events(db, claim.attempt.attempt_key)
    assert [event.event_type for event in events] == [
        "attempt_started",
        "stage_finished",
        "attempt_finished",
    ]
    assert events[-2].stage == "persist_snapshot"
    assert events[-2].state == "completed"
    assert events[-1].state == "published"
    assert events[-1].payload_json
    assert _count(db, OpportunitySnapshotRun) == 1
    assert _count(db, OpportunitySnapshotCandidate) == 1


def test_atomic_publication_treats_universe_as_an_unordered_scope(isolated_db):
    db, _ = isolated_db
    claim = claim_cycle_attempt(
        cycle_key=CYCLE_KEY,
        market_date_et=MARKET_DATE_ET,
        scope_key=SCOPE_KEY,
        freeze_policy_version="preopen_xnys_v2",
        cycle_version="canonical_premarket_v1",
        universe=("MSFT", "AAPL"),
        requested_limit=2,
        universe_source="persisted",
        universe_version_key="opu_test_universe",
        trigger="scheduler",
        owner_key="worker-a",
        hard_deadline_at=HARD_DEADLINE_AT,
        now=STARTED_AT,
        lease_seconds=600,
        db_manager=db,
    )
    published_at = HARD_DEADLINE_AT - timedelta(seconds=1)

    result = publish_cycle_snapshot_atomically(
        CYCLE_KEY,
        claim.attempt.attempt_key,
        owner_key="worker-a",
        snapshot_factory=_snapshot_factory(
            db,
            claim.attempt.attempt_key,
        ),
        guard_clock=lambda: published_at,
        terminal_payload={"quality": "ready"},
        db_manager=db,
    )

    assert result.status.state == "published"
    stored = get_snapshot(result.snapshot.snapshot_key, db_manager=db)
    assert stored is not None


def test_lock_wait_crossing_deadline_writes_nothing(isolated_db):
    db, _ = isolated_db
    claim = _claim(
        db,
        owner_key="worker-a",
        lease_seconds=600,
    )
    init_opportunity_schema(db)
    guard_called = threading.Event()
    worker_started = threading.Event()

    def guard_clock():
        guard_called.set()
        return HARD_DEADLINE_AT

    def publish():
        worker_started.set()
        return publish_cycle_snapshot_atomically(
            CYCLE_KEY,
            claim.attempt.attempt_key,
            owner_key="worker-a",
            snapshot_factory=_snapshot_factory(
                db,
                claim.attempt.attempt_key,
            ),
            guard_clock=guard_clock,
            db_manager=db,
        )

    blocker = db._engine.connect()
    blocker.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(publish)
            assert worker_started.wait(timeout=1)
            assert guard_called.wait(timeout=0.15) is False
            blocker.commit()
            with pytest.raises(PremarketPublicationDeadlineError):
                future.result(timeout=3)
    finally:
        if blocker.in_transaction():
            blocker.rollback()
        blocker.close()

    assert guard_called.is_set()
    assert _count(db, OpportunitySnapshotRun) == 0
    assert _count(db, OpportunitySnapshotCandidate) == 0
    events = _attempt_events(db, claim.attempt.attempt_key)
    assert [event.event_type for event in events] == ["attempt_started"]


def test_factory_crossing_deadline_rolls_back_everything(isolated_db):
    db, _ = isolated_db
    claim = _claim(
        db,
        owner_key="worker-a",
        lease_seconds=600,
    )
    freeze_at = HARD_DEADLINE_AT - timedelta(seconds=1)
    guard_values = iter((freeze_at, HARD_DEADLINE_AT))
    guard_calls = []

    def advancing_guard():
        observed_at = next(guard_values)
        guard_calls.append(observed_at)
        return observed_at

    with pytest.raises(
        PremarketPublicationDeadlineError,
        match="before commit",
    ):
        publish_cycle_snapshot_atomically(
            CYCLE_KEY,
            claim.attempt.attempt_key,
            owner_key="worker-a",
            snapshot_factory=_snapshot_factory(
                db,
                claim.attempt.attempt_key,
            ),
            guard_clock=advancing_guard,
            db_manager=db,
        )

    assert guard_calls == [freeze_at, HARD_DEADLINE_AT]
    assert _count(db, OpportunitySnapshotRun) == 0
    assert _count(db, OpportunitySnapshotCandidate) == 0
    assert [
        event.event_type
        for event in _attempt_events(db, claim.attempt.attempt_key)
    ] == ["attempt_started"]


def test_terminal_event_failure_rolls_back_snapshot_and_stage(
    isolated_db,
    monkeypatch,
):
    db, _ = isolated_db
    claim = _claim(
        db,
        owner_key="worker-a",
        lease_seconds=600,
    )
    published_at = HARD_DEADLINE_AT - timedelta(seconds=1)
    original_event_row = cycle_repository_module._event_row

    def fail_terminal_event(**kwargs):
        if kwargs["event_type"] == "attempt_finished":
            raise RuntimeError("terminal audit unavailable")
        return original_event_row(**kwargs)

    monkeypatch.setattr(
        cycle_repository_module,
        "_event_row",
        fail_terminal_event,
    )
    with pytest.raises(RuntimeError, match="terminal audit unavailable"):
        publish_cycle_snapshot_atomically(
            CYCLE_KEY,
            claim.attempt.attempt_key,
            owner_key="worker-a",
            snapshot_factory=_snapshot_factory(
                db,
                claim.attempt.attempt_key,
            ),
            guard_clock=lambda: published_at,
            db_manager=db,
        )

    assert _count(db, OpportunitySnapshotRun) == 0
    assert _count(db, OpportunitySnapshotCandidate) == 0
    events = _attempt_events(db, claim.attempt.attempt_key)
    assert [event.event_type for event in events] == ["attempt_started"]
    status = get_latest_cycle_attempt_status(
        CYCLE_KEY,
        now=published_at,
        db_manager=db,
    )
    assert status is not None
    assert status.state == "running"


def test_publication_audit_failure_rolls_back_snapshot_and_terminal_events(
    isolated_db,
):
    db, _ = isolated_db
    claim = _claim(
        db,
        owner_key="worker-a",
        lease_seconds=600,
    )
    published_at = HARD_DEADLINE_AT - timedelta(seconds=1)
    observed = {}

    def fail_publication_audit(session, snapshot, status):
        observed["snapshot_key"] = snapshot.snapshot_key
        observed["state"] = status.state
        assert session.in_transaction()
        raise RuntimeError("qualification audit unavailable")

    with pytest.raises(RuntimeError, match="qualification audit unavailable"):
        publish_cycle_snapshot_atomically(
            CYCLE_KEY,
            claim.attempt.attempt_key,
            owner_key="worker-a",
            snapshot_factory=_snapshot_factory(
                db,
                claim.attempt.attempt_key,
            ),
            guard_clock=lambda: published_at,
            publication_audit_factory=fail_publication_audit,
            db_manager=db,
        )

    assert observed["state"] == "published"
    assert observed["snapshot_key"].startswith("ops_")
    assert _count(db, OpportunitySnapshotRun) == 0
    assert _count(db, OpportunitySnapshotCandidate) == 0
    assert [
        event.event_type
        for event in _attempt_events(db, claim.attempt.attempt_key)
    ] == ["attempt_started"]


def test_stale_publication_provenance_rolls_back_snapshot(isolated_db):
    db, _ = isolated_db
    claim = _claim(
        db,
        owner_key="worker-a",
        lease_seconds=600,
    )
    published_at = HARD_DEADLINE_AT - timedelta(seconds=1)
    stale_at = published_at - timedelta(minutes=1)

    def stale_factory(session, _authoritative_published_at):
        run = _snapshot_run(stale_at)
        run_payload = dict(run.payload)
        run_payload["canonical_cycle"] = {
            **dict(run_payload["canonical_cycle"]),
            "attempt_key": claim.attempt.attempt_key,
        }
        return append_snapshot(
            SnapshotRunInput(
                **{
                    **run.__dict__,
                    "payload": run_payload,
                }
            ),
            _snapshot_candidates(stale_at),
            db_manager=db,
            session=session,
        )

    with pytest.raises(
        PremarketPublicationContractError,
        match="lock-protected publication timestamp",
    ):
        publish_cycle_snapshot_atomically(
            CYCLE_KEY,
            claim.attempt.attempt_key,
            owner_key="worker-a",
            snapshot_factory=stale_factory,
            guard_clock=lambda: published_at,
            db_manager=db,
        )

    assert _count(db, OpportunitySnapshotRun) == 0
    assert _count(db, OpportunitySnapshotCandidate) == 0
    assert [
        event.event_type
        for event in _attempt_events(db, claim.attempt.attempt_key)
    ] == ["attempt_started"]
