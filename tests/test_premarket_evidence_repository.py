# -*- coding: utf-8 -*-
"""Persistence-contract tests for shadow premarket evidence prefetch."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DatabaseError

import src.regime.premarket_repository as premarket_repository
from src.regime.premarket_artifacts import (
    PREMARKET_ARTIFACT_SCHEMA_VERSION,
    PremarketArtifactBundle,
    PremarketObservation,
    build_premarket_artifact_bundle,
)
from src.regime.premarket_models import (
    RegimePremarketArtifactBundle,
    RegimePremarketPrefetchRun,
)
from src.regime.premarket_repository import (
    PremarketArtifactConflictError,
    PremarketEvidenceRepositoryError,
    PremarketPrefetchLeaseLostError,
    append_premarket_artifact_and_settle,
    build_premarket_universe_sha256,
    claim_premarket_prefetch_run,
    get_premarket_artifact_bundle,
    get_premarket_prefetch_run,
    heartbeat_premarket_prefetch_run,
    init_premarket_evidence_schema,
    mark_premarket_prefetch_missed,
    select_formal_premarket_artifact_bundle,
    settle_premarket_prefetch_run,
)
from src.storage import DatabaseManager


UTC = timezone.utc
_TEST_CLOCK = datetime.now(UTC).replace(microsecond=0)
TARGET_AS_OF = _TEST_CLOCK - timedelta(minutes=5)
MARKET_DATE = TARGET_AS_OF.astimezone(
    ZoneInfo("America/New_York")
).date()
PREVIOUS_SESSION = MARKET_DATE - timedelta(days=1)
DEADLINE = _TEST_CLOCK + timedelta(hours=2)
CLAIMED_AT = _TEST_CLOCK - timedelta(minutes=10)
CONSUMER_AS_OF = _TEST_CLOCK + timedelta(minutes=90)
POLICY = "moomoo-shadow-prefetch-v1"
UNIVERSE_KEY = "daily-options-watchlist-v1"
SYMBOLS = ("SPY", "QQQ", "TSLA")
SCHEMA = PREMARKET_ARTIFACT_SCHEMA_VERSION
ADAPTER = "moomoo-openapi-v1"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PATH",
        str(tmp_path / "premarket_evidence.db"),
    )

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


def _claim(
    db,
    *,
    owner: str = "worker-a",
    now: datetime = CLAIMED_AT,
    lease_seconds: int = 20 * 60,
    max_attempts: int = 3,
):
    return claim_premarket_prefetch_run(
        MARKET_DATE,
        PREVIOUS_SESSION,
        target_as_of=TARGET_AS_OF,
        hard_deadline_at=DEADLINE,
        policy_version=POLICY,
        universe_key=UNIVERSE_KEY,
        symbols=SYMBOLS,
        owner_key=owner,
        now=now,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
        db_manager=db,
    )


def _append(
    claim,
    db,
    *,
    payload=None,
    coverage=None,
    created_at: datetime = TARGET_AS_OF + timedelta(minutes=4),
    fetched_at: datetime = TARGET_AS_OF + timedelta(minutes=3),
    expires_at: datetime = DEADLINE,
):
    bundle_payload = payload or _artifact_payload()
    bundle = PremarketArtifactBundle.from_payload(bundle_payload)
    expected_coverage = {
        "requested_count": len(bundle.required_universe),
        "available_count": sum(
            item.is_available for item in bundle.observations
        ),
        "unavailable_count": sum(
            not item.is_available for item in bundle.observations
        ),
        "available_symbols": [
            item.symbol for item in bundle.observations if item.is_available
        ],
        "unavailable_reasons": {
            item.symbol: item.unavailable_reason
            for item in bundle.observations
            if not item.is_available
        },
    }
    return append_premarket_artifact_and_settle(
        claim.run.slot_key,
        owner_key=claim.run.owner_key,
        attempt_count=claim.run.attempt_count,
        schema_version=SCHEMA,
        adapter_key=ADAPTER,
        fetch_started_at=TARGET_AS_OF + timedelta(minutes=1),
        fetched_at=fetched_at,
        expires_at=expires_at,
        quality_state=bundle.quality,
        coverage=coverage or expected_coverage,
        payload=bundle_payload,
        created_at=created_at,
        db_manager=db,
    )


def _artifact_payload(
    *,
    tsla_price: float = 103.0,
    evidence_as_of: datetime = TARGET_AS_OF - timedelta(minutes=1),
):
    observations = (
        PremarketObservation.available(
            symbol="SPY",
            market_date_et=MARKET_DATE,
            previous_session=PREVIOUS_SESSION,
            requested_as_of=TARGET_AS_OF,
            evidence_as_of=evidence_as_of,
            price=101.0,
            previous_close=100.0,
            change_pct=1.0,
        ),
        PremarketObservation.available(
            symbol="QQQ",
            market_date_et=MARKET_DATE,
            previous_session=PREVIOUS_SESSION,
            requested_as_of=TARGET_AS_OF,
            evidence_as_of=evidence_as_of,
            price=102.0,
            previous_close=100.0,
            change_pct=2.0,
        ),
        PremarketObservation.available(
            symbol="TSLA",
            market_date_et=MARKET_DATE,
            previous_session=PREVIOUS_SESSION,
            requested_as_of=TARGET_AS_OF,
            evidence_as_of=evidence_as_of,
            price=tsla_price,
            previous_close=100.0,
            change_pct=tsla_price - 100.0,
        ),
    )
    return build_premarket_artifact_bundle(
        market_date_et=MARKET_DATE,
        previous_session=PREVIOUS_SESSION,
        target_as_of=TARGET_AS_OF,
        required_universe=SYMBOLS,
        observations=observations,
    ).to_payload()


def _select(db, **overrides):
    params = {
        "as_of": CONSUMER_AS_OF,
        "policy_version": POLICY,
        "universe_key": UNIVERSE_KEY,
        "universe_sha256": build_premarket_universe_sha256(
            UNIVERSE_KEY,
            SYMBOLS,
        ),
        "schema_version": SCHEMA,
        "adapter_key": ADAPTER,
        "max_age_seconds": 2 * 60 * 60,
        "db_manager": db,
    }
    params.update(overrides)
    return select_formal_premarket_artifact_bundle(
        MARKET_DATE,
        PREVIOUS_SESSION,
        **params,
    )


def test_schema_is_idempotent_and_bundle_table_is_append_only(isolated_db):
    init_premarket_evidence_schema(isolated_db)
    init_premarket_evidence_schema(isolated_db)
    appended = _append(_claim(isolated_db), isolated_db)

    with pytest.raises(DatabaseError, match="append-only"):
        with isolated_db._engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE regime_premarket_artifact_bundles "
                "SET quality_state = 'degraded' "
                "WHERE bundle_key = ?",
                (appended.bundle.bundle_key,),
            )
    with pytest.raises(DatabaseError, match="append-only"):
        with isolated_db._engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM regime_premarket_artifact_bundles "
                "WHERE bundle_key = ?",
                (appended.bundle.bundle_key,),
            )
    with pytest.raises(DatabaseError, match="append-only"):
        with isolated_db._engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE regime_premarket_artifact_ingestions "
                "SET ingested_at = '2000-01-01 00:00:00.000' "
                "WHERE bundle_key = ?",
                (appended.bundle.bundle_key,),
            )
    with pytest.raises(DatabaseError, match="append-only"):
        with isolated_db._engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM regime_premarket_artifact_ingestions "
                "WHERE bundle_key = ?",
                (appended.bundle.bundle_key,),
            )
    with pytest.raises(DatabaseError, match="append-only"):
        with isolated_db._engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT OR REPLACE INTO "
                "regime_premarket_artifact_ingestions "
                "(bundle_key, ingested_at) VALUES "
                "(?, '2000-01-01 00:00:00.000')",
                (appended.bundle.bundle_key,),
            )


def test_ready_schema_init_uses_read_only_fast_path(isolated_db):
    init_premarket_evidence_schema(isolated_db)
    statements = []

    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(str(statement).strip().upper())

    event.listen(
        isolated_db._engine,
        "before_cursor_execute",
        capture_statement,
    )
    try:
        init_premarket_evidence_schema(isolated_db)
    finally:
        event.remove(
            isolated_db._engine,
            "before_cursor_execute",
            capture_statement,
        )

    assert statements
    assert all(statement.startswith("SELECT") for statement in statements)


def test_trusted_receipt_publish_and_select_fail_closed_outside_sqlite(
    isolated_db,
    monkeypatch,
):
    claim = _claim(isolated_db)
    monkeypatch.setattr(isolated_db, "_is_sqlite_engine", False)

    with pytest.raises(
        PremarketEvidenceRepositoryError,
        match="require SQLite",
    ):
        _append(claim, isolated_db)
    assert _select(isolated_db) is None


def test_legacy_artifact_receipt_uses_migration_database_time(isolated_db):
    legacy_bundle_key = "rpb_legacy_migration_fixture"
    legacy_slot_key = "rpp_legacy_migration_fixture"
    legacy_created_at = datetime(2000, 1, 1, tzinfo=UTC)
    with isolated_db.session_scope() as session:
        session.add(
            RegimePremarketPrefetchRun(
                slot_key=legacy_slot_key,
                market_date_et=MARKET_DATE,
                previous_session=PREVIOUS_SESSION,
                target_as_of=TARGET_AS_OF,
                hard_deadline_at=DEADLINE,
                policy_version=POLICY,
                universe_key=UNIVERSE_KEY,
                universe_sha256=build_premarket_universe_sha256(
                    UNIVERSE_KEY,
                    SYMBOLS,
                ),
                symbols_json='["SPY","QQQ","TSLA"]',
                state="succeeded",
                attempt_count=1,
                completed_at=legacy_created_at,
                bundle_key=legacy_bundle_key,
                created_at=legacy_created_at,
                updated_at=legacy_created_at,
            )
        )
        session.flush()
        session.add(
            RegimePremarketArtifactBundle(
                bundle_key=legacy_bundle_key,
                slot_key=legacy_slot_key,
                attempt_count=1,
                schema_version=SCHEMA,
                policy_version=POLICY,
                adapter_key=ADAPTER,
                market_date_et=MARKET_DATE,
                previous_session=PREVIOUS_SESSION,
                universe_key=UNIVERSE_KEY,
                universe_sha256=build_premarket_universe_sha256(
                    UNIVERSE_KEY,
                    SYMBOLS,
                ),
                symbols_json='["SPY","QQQ","TSLA"]',
                target_as_of=TARGET_AS_OF,
                fetch_started_at=TARGET_AS_OF,
                fetched_at=TARGET_AS_OF,
                expires_at=DEADLINE,
                quality_state="ready",
                coverage_json="{}",
                payload_sha256="0" * 64,
                payload_json="{}",
                created_at=legacy_created_at,
            )
        )

    with isolated_db._engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) "
            "FROM regime_premarket_artifact_ingestions"
        ).scalar_one() == 0
        migration_started = datetime.fromisoformat(
            connection.exec_driver_sql(
                "SELECT STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW')"
            ).scalar_one()
        ).replace(tzinfo=UTC)

    init_premarket_evidence_schema(isolated_db)
    init_premarket_evidence_schema(isolated_db)

    with isolated_db._engine.connect() as connection:
        migration_finished = datetime.fromisoformat(
            connection.exec_driver_sql(
                "SELECT STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW')"
            ).scalar_one()
        ).replace(tzinfo=UTC)
        row = connection.exec_driver_sql(
            "SELECT ingested_at "
            "FROM regime_premarket_artifact_ingestions "
            "WHERE bundle_key = ?",
            (legacy_bundle_key,),
        ).one()
        receipt_count = connection.exec_driver_sql(
            "SELECT COUNT(*) "
            "FROM regime_premarket_artifact_ingestions "
            "WHERE bundle_key = ?",
            (legacy_bundle_key,),
        ).scalar_one()

    ingested_at = datetime.fromisoformat(str(row[0])).replace(tzinfo=UTC)
    assert migration_started <= ingested_at <= migration_finished
    assert ingested_at != legacy_created_at
    assert receipt_count == 1


def test_claim_heartbeat_and_owner_attempt_fencing(isolated_db):
    claim = _claim(
        isolated_db,
        lease_seconds=5 * 60,
    )
    contender = _claim(
        isolated_db,
        owner="worker-b",
        now=CLAIMED_AT + timedelta(minutes=1),
    )

    assert claim.claimed is True
    assert claim.run.attempt_count == 1
    assert contender.claimed is False
    assert contender.reason == "lease_live"

    heartbeat = heartbeat_premarket_prefetch_run(
        claim.run.slot_key,
        owner_key="worker-a",
        attempt_count=1,
        now=CLAIMED_AT + timedelta(minutes=2),
        lease_seconds=30 * 60,
        db_manager=isolated_db,
    )
    assert heartbeat.lease_expires_at == CLAIMED_AT + timedelta(minutes=32)

    with pytest.raises(PremarketPrefetchLeaseLostError):
        heartbeat_premarket_prefetch_run(
            claim.run.slot_key,
            owner_key="worker-b",
            attempt_count=1,
            now=CLAIMED_AT + timedelta(minutes=3),
            db_manager=isolated_db,
        )
    with pytest.raises(PremarketPrefetchLeaseLostError):
        settle_premarket_prefetch_run(
            claim.run.slot_key,
            owner_key="worker-a",
            attempt_count=2,
            state="failed",
            error_code="wrong_attempt",
            now=CLAIMED_AT + timedelta(minutes=3),
            db_manager=isolated_db,
        )


def test_expired_lease_takeover_rejects_stale_worker(isolated_db):
    first = _claim(
        isolated_db,
        lease_seconds=30,
    )
    recovered = _claim(
        isolated_db,
        owner="worker-b",
        now=CLAIMED_AT + timedelta(seconds=31),
        lease_seconds=10 * 60,
    )

    assert recovered.claimed is True
    assert recovered.run.attempt_count == 2
    assert recovered.run.owner_key == "worker-b"

    with pytest.raises(PremarketPrefetchLeaseLostError):
        settle_premarket_prefetch_run(
            first.run.slot_key,
            owner_key="worker-a",
            attempt_count=1,
            state="failed",
            error_code="late_child",
            now=CLAIMED_AT + timedelta(seconds=32),
            db_manager=isolated_db,
        )

    settled = settle_premarket_prefetch_run(
        recovered.run.slot_key,
        owner_key="worker-b",
        attempt_count=2,
        state="failed",
        error_code="provider_unavailable",
        error_detail="connection refused",
        now=CLAIMED_AT + timedelta(minutes=2),
        db_manager=isolated_db,
    )
    assert settled.state == "failed"
    assert settled.last_error_code == "provider_unavailable"


def test_current_attempt_can_record_timeout_during_bounded_terminal_grace(
    isolated_db,
):
    claim = _claim(
        isolated_db,
        lease_seconds=30,
    )

    settled = settle_premarket_prefetch_run(
        claim.run.slot_key,
        owner_key="worker-a",
        attempt_count=1,
        state="timed_out",
        error_code="child_hard_timeout",
        now=DEADLINE + timedelta(seconds=30),
        settlement_grace_seconds=60,
        db_manager=isolated_db,
    )
    assert settled.state == "timed_out"
    assert settled.completed_at == DEADLINE + timedelta(seconds=30)


def test_terminal_settlement_after_grace_is_rejected(isolated_db):
    claim = _claim(
        isolated_db,
        lease_seconds=30,
    )

    with pytest.raises(PremarketPrefetchLeaseLostError):
        settle_premarket_prefetch_run(
            claim.run.slot_key,
            owner_key="worker-a",
            attempt_count=1,
            state="timed_out",
            error_code="too_late",
            now=DEADLINE + timedelta(seconds=61),
            settlement_grace_seconds=60,
            db_manager=isolated_db,
        )
    assert (
        get_premarket_prefetch_run(
            claim.run.slot_key,
            db_manager=isolated_db,
        ).state
        == "running"
    )


def test_missed_slot_is_persisted_without_claiming_work(isolated_db):
    missed = mark_premarket_prefetch_missed(
        MARKET_DATE,
        PREVIOUS_SESSION,
        target_as_of=TARGET_AS_OF,
        hard_deadline_at=DEADLINE,
        policy_version=POLICY,
        universe_key=UNIVERSE_KEY,
        symbols=SYMBOLS,
        now=DEADLINE,
        db_manager=isolated_db,
    )

    assert missed.state == "missed"
    assert missed.attempt_count == 0
    assert missed.owner_key is None
    assert missed.last_error_code == "hard_deadline_missed"
    replay = _claim(
        isolated_db,
        now=DEADLINE + timedelta(minutes=1),
    )
    assert replay.claimed is False
    assert replay.reason == "terminal_missed"

    with pytest.raises(PremarketEvidenceRepositoryError):
        mark_premarket_prefetch_missed(
            MARKET_DATE,
            PREVIOUS_SESSION,
            target_as_of=TARGET_AS_OF,
            hard_deadline_at=DEADLINE,
            policy_version="different-policy",
            universe_key=UNIVERSE_KEY,
            symbols=SYMBOLS,
            now=DEADLINE - timedelta(seconds=1),
            db_manager=isolated_db,
        )


def test_append_and_success_settlement_are_atomic_and_idempotent(isolated_db):
    claim = _claim(isolated_db)
    first = _append(claim, isolated_db)

    assert first.duplicate is False
    assert first.run.state == "succeeded"
    assert first.run.bundle_key == first.bundle.bundle_key
    assert first.bundle.payload_sha256
    assert first.bundle.ingested_at is not None
    assert first.bundle.ingested_at.tzinfo is not None
    assert first.bundle.ingested_at > first.bundle.created_at
    assert first.run.completed_at == first.bundle.ingested_at
    stored = get_premarket_artifact_bundle(
        first.bundle.bundle_key,
        db_manager=isolated_db,
    )
    assert stored == first.bundle
    assert (
        get_premarket_prefetch_run(
            claim.run.slot_key,
            db_manager=isolated_db,
        ).state
        == "succeeded"
    )

    replay = _append(
        claim,
        isolated_db,
        created_at=TARGET_AS_OF + timedelta(minutes=10),
    )
    assert replay.duplicate is True
    assert replay.bundle.bundle_key == first.bundle.bundle_key
    assert replay.bundle.ingested_at == first.bundle.ingested_at

    with pytest.raises(PremarketArtifactConflictError):
        _append(
            claim,
            isolated_db,
            payload=_artifact_payload(tsla_price=104.0),
        )


def test_formal_selector_waits_for_conservative_receipt_boundary(isolated_db):
    appended = _append(_claim(isolated_db), isolated_db)
    ingested_at = appended.bundle.ingested_at

    assert ingested_at is not None
    assert _select(isolated_db, as_of=ingested_at) is None
    selected = _select(
        isolated_db,
        as_of=ingested_at + timedelta(milliseconds=1),
    )
    assert selected is not None
    assert selected.bundle_key == appended.bundle.bundle_key


def test_missing_receipt_fails_closed_until_recovery_replay(
    isolated_db,
    monkeypatch,
):
    appended = _append(_claim(isolated_db), isolated_db)
    delete_trigger = (
        "trg_regime_premarket_artifact_ingestions_delete_immutable"
    )
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER {delete_trigger}")
        connection.exec_driver_sql(
            "DELETE FROM regime_premarket_artifact_ingestions "
            "WHERE bundle_key = ?",
            (appended.bundle.bundle_key,),
        )
    monkeypatch.setattr(
        premarket_repository,
        "init_premarket_evidence_schema",
        lambda *_args, **_kwargs: None,
    )

    assert _select(isolated_db) is None
    with isolated_db._engine.connect() as connection:
        recovery_started = datetime.fromisoformat(
            connection.exec_driver_sql(
                "SELECT STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW')"
            ).scalar_one()
        ).replace(tzinfo=UTC)
    replay = _append(
        _claim(isolated_db),
        isolated_db,
        created_at=TARGET_AS_OF + timedelta(minutes=10),
    )

    assert replay.duplicate is True
    assert replay.bundle.ingested_at is not None
    assert replay.bundle.ingested_at >= recovery_started


def test_duplicate_replay_rejects_receipt_that_crosses_deadline(isolated_db):
    claim = _claim(isolated_db)
    appended = _append(claim, isolated_db)
    update_trigger = (
        "trg_regime_premarket_artifact_ingestions_update_immutable"
    )
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER {update_trigger}")
        connection.exec_driver_sql(
            "UPDATE regime_premarket_artifact_ingestions "
            "SET ingested_at = ? WHERE bundle_key = ?",
            (DEADLINE, appended.bundle.bundle_key),
        )

    with pytest.raises(
        PremarketArtifactConflictError,
        match="causal ordering",
    ):
        _append(
            claim,
            isolated_db,
            created_at=TARGET_AS_OF + timedelta(minutes=10),
        )


def test_database_receipt_after_deadline_marks_run_failed_and_hidden(
    isolated_db,
):
    claim = _claim(isolated_db)
    expired_deadline = _TEST_CLOCK - timedelta(seconds=30)
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE regime_premarket_prefetch_runs "
            "SET hard_deadline_at = ? WHERE slot_key = ?",
            (expired_deadline, claim.run.slot_key),
        )

    with pytest.raises(
        PremarketEvidenceRepositoryError,
        match="database ingestion crossed",
    ):
        _append(claim, isolated_db)

    with isolated_db._engine.connect() as connection:
        artifact_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM regime_premarket_artifact_bundles"
        ).scalar_one()
        receipt_count = connection.exec_driver_sql(
            "SELECT COUNT(*) "
            "FROM regime_premarket_artifact_ingestions"
        ).scalar_one()
        run_row = connection.exec_driver_sql(
            "SELECT state, bundle_key "
            "FROM regime_premarket_prefetch_runs "
            "WHERE slot_key = ?",
            (claim.run.slot_key,),
        ).one()
    assert artifact_count == 1
    assert receipt_count == 1
    assert tuple(run_row) == ("failed", None)
    assert get_premarket_prefetch_run(
        claim.run.slot_key,
        db_manager=isolated_db,
    ).state == "failed"
    assert _select(isolated_db) is None


def test_receipt_failure_leaves_fail_closed_stage_and_replay_recovers(
    isolated_db,
):
    claim = _claim(isolated_db)
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER "
            "trg_regime_premarket_receipt_failure_fixture "
            "BEFORE INSERT ON regime_premarket_artifact_ingestions "
            "BEGIN SELECT RAISE(ABORT, "
            "'fixture receipt failure'); END"
        )

    with pytest.raises(DatabaseError, match="fixture receipt failure"):
        _append(claim, isolated_db)

    with isolated_db._engine.connect() as connection:
        artifact_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM regime_premarket_artifact_bundles"
        ).scalar_one()
        receipt_count = connection.exec_driver_sql(
            "SELECT COUNT(*) "
            "FROM regime_premarket_artifact_ingestions"
        ).scalar_one()
        run_row = connection.exec_driver_sql(
            "SELECT state, bundle_key "
            "FROM regime_premarket_prefetch_runs "
            "WHERE slot_key = ?",
            (claim.run.slot_key,),
        ).one()
    assert artifact_count == 1
    assert receipt_count == 0
    assert run_row[0] == "succeeded"
    assert run_row[1] is not None

    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER "
            "trg_regime_premarket_receipt_failure_fixture"
        )
        recovery_started = datetime.fromisoformat(
            connection.exec_driver_sql(
                "SELECT STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW')"
            ).scalar_one()
        ).replace(tzinfo=UTC)
    replay = _append(
        claim,
        isolated_db,
        created_at=TARGET_AS_OF + timedelta(minutes=10),
    )

    assert replay.duplicate is True
    assert replay.run.state == "succeeded"
    assert replay.bundle.ingested_at is not None
    assert replay.bundle.ingested_at >= recovery_started


def test_late_receipt_recovery_marks_staged_run_failed_and_hidden(
    isolated_db,
):
    claim = _claim(isolated_db)
    failure_trigger = "trg_regime_premarket_receipt_failure_fixture"
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TRIGGER {failure_trigger} "
            "BEFORE INSERT ON regime_premarket_artifact_ingestions "
            "BEGIN SELECT RAISE(ABORT, "
            "'fixture receipt failure'); END"
        )

    with pytest.raises(DatabaseError, match="fixture receipt failure"):
        _append(claim, isolated_db)

    expired_deadline = _TEST_CLOCK - timedelta(seconds=30)
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER {failure_trigger}")
        connection.exec_driver_sql(
            "UPDATE regime_premarket_prefetch_runs "
            "SET hard_deadline_at = ? WHERE slot_key = ?",
            (expired_deadline, claim.run.slot_key),
        )

    init_premarket_evidence_schema(isolated_db)

    with isolated_db._engine.connect() as connection:
        receipt_count = connection.exec_driver_sql(
            "SELECT COUNT(*) "
            "FROM regime_premarket_artifact_ingestions"
        ).scalar_one()
        run_row = connection.exec_driver_sql(
            "SELECT state, bundle_key, last_error_code, "
            "last_error_detail "
            "FROM regime_premarket_prefetch_runs "
            "WHERE slot_key = ?",
            (claim.run.slot_key,),
        ).one()
    assert receipt_count == 1
    assert tuple(run_row) == (
        "failed",
        None,
        "artifact_receipt_causal_order_invalid",
        "recovered ingestion receipt violates causal ordering",
    )
    assert _select(isolated_db) is None


def test_semantically_equivalent_payload_is_canonicalized_for_replay(
    isolated_db,
):
    claim = _claim(isolated_db)
    first = _append(claim, isolated_db)
    equivalent = deepcopy(_artifact_payload())
    equivalent["required_universe"].reverse()
    equivalent["observations"].reverse()
    equivalent["observations"][0]["change_pct"] += 0.005

    replay = _append(claim, isolated_db, payload=equivalent)

    assert replay.duplicate is True
    assert replay.bundle.bundle_key == first.bundle.bundle_key
    assert replay.bundle.payload == first.bundle.payload


def test_append_rejects_malformed_payload_or_forged_coverage(isolated_db):
    claim = _claim(isolated_db)

    with pytest.raises(
        PremarketEvidenceRepositoryError,
        match="artifact contract",
    ):
        append_premarket_artifact_and_settle(
            claim.run.slot_key,
            owner_key=claim.run.owner_key,
            attempt_count=claim.run.attempt_count,
            schema_version=SCHEMA,
            adapter_key=ADAPTER,
            fetch_started_at=TARGET_AS_OF,
            fetched_at=TARGET_AS_OF + timedelta(minutes=1),
            expires_at=DEADLINE,
            quality_state="ready",
            coverage={},
            payload={"unexpected": "payload"},
            created_at=TARGET_AS_OF + timedelta(minutes=2),
            db_manager=isolated_db,
        )

    with pytest.raises(
        PremarketEvidenceRepositoryError,
        match="does not match",
    ):
        _append(
            claim,
            isolated_db,
            coverage={
                "requested_count": 3,
                "available_count": 2,
                "unavailable_count": 1,
                "available_symbols": ["QQQ", "SPY"],
                "unavailable_reasons": {"TSLA": "no_quote"},
            },
        )

    non_policy_payload = _artifact_payload()
    non_policy_payload["max_freshness_minutes"] = 6
    with pytest.raises(
        PremarketEvidenceRepositoryError,
        match="does not match",
    ):
        _append(claim, isolated_db, payload=non_policy_payload)

    with pytest.raises(
        PremarketEvidenceRepositoryError,
        match="must not follow fetched_at",
    ):
        _append(
            claim,
            isolated_db,
            payload=_artifact_payload(evidence_as_of=TARGET_AS_OF),
            fetched_at=TARGET_AS_OF - timedelta(seconds=1),
        )

    assert _append(claim, isolated_db).run.state == "succeeded"


@pytest.mark.parametrize("state", ("failed", "timed_out"))
def test_post_deadline_terminal_replay_is_read_only(isolated_db, state):
    claim = _claim(isolated_db)
    settled = settle_premarket_prefetch_run(
        claim.run.slot_key,
        owner_key=claim.run.owner_key,
        attempt_count=claim.run.attempt_count,
        state=state,
        error_code=f"fixture_{state}",
        now=TARGET_AS_OF + timedelta(minutes=1),
        db_manager=isolated_db,
    )

    replay = mark_premarket_prefetch_missed(
        MARKET_DATE,
        PREVIOUS_SESSION,
        target_as_of=TARGET_AS_OF,
        hard_deadline_at=DEADLINE,
        policy_version=POLICY,
        universe_key=UNIVERSE_KEY,
        symbols=SYMBOLS,
        now=DEADLINE + timedelta(minutes=1),
        db_manager=isolated_db,
    )
    second_replay = mark_premarket_prefetch_missed(
        MARKET_DATE,
        PREVIOUS_SESSION,
        target_as_of=TARGET_AS_OF,
        hard_deadline_at=DEADLINE,
        policy_version=POLICY,
        universe_key=UNIVERSE_KEY,
        symbols=SYMBOLS,
        now=DEADLINE + timedelta(minutes=2),
        db_manager=isolated_db,
    )

    assert replay == settled
    assert second_replay == settled


def test_invalid_or_stale_owner_never_appends_partial_bundle(isolated_db):
    claim = _claim(
        isolated_db,
        lease_seconds=30,
    )
    with pytest.raises(PremarketPrefetchLeaseLostError):
        _append(claim, isolated_db)

    with isolated_db.session_scope() as session:
        count = session.connection().exec_driver_sql(
            "SELECT COUNT(*) FROM regime_premarket_artifact_bundles"
        ).scalar_one()
    assert count == 0
    assert (
        get_premarket_prefetch_run(
            claim.run.slot_key,
            db_manager=isolated_db,
        ).state
        == "running"
    )


def test_formal_selector_requires_exact_causal_and_integrity_contract(
    isolated_db,
):
    appended = _append(_claim(isolated_db), isolated_db)

    selected = _select(isolated_db)
    assert selected is not None
    assert selected.bundle_key == appended.bundle.bundle_key
    assert selected.fetched_at > selected.target_as_of
    assert _select(isolated_db, as_of=DEADLINE) is None

    mismatch_cases = (
        {"as_of": TARGET_AS_OF - timedelta(seconds=1)},
        {"as_of": DEADLINE + timedelta(seconds=1)},
        {"policy_version": "other-policy"},
        {"universe_key": "other-universe"},
        {"universe_sha256": "0" * 64},
        {"schema_version": "other-schema"},
        {"adapter_key": "other-adapter"},
        {"expected_payload_sha256": "f" * 64},
        {"max_age_seconds": 3 * 60},
        {"allowed_quality_states": ("partial",)},
    )
    for overrides in mismatch_cases:
        assert _select(isolated_db, **overrides) is None

    assert (
        select_formal_premarket_artifact_bundle(
            MARKET_DATE,
            MARKET_DATE - timedelta(days=1),
            as_of=CONSUMER_AS_OF,
            policy_version=POLICY,
            universe_key=UNIVERSE_KEY,
            universe_sha256=appended.bundle.universe_sha256,
            schema_version=SCHEMA,
            db_manager=isolated_db,
        )
        is None
    )


def test_expired_on_arrival_bundle_remains_auditable_but_not_formal(
    isolated_db,
):
    appended = _append(
        _claim(isolated_db),
        isolated_db,
        expires_at=TARGET_AS_OF,
    )

    assert appended.run.state == "succeeded"
    assert appended.bundle.expires_at < appended.bundle.fetched_at
    assert _select(isolated_db) is None


def test_formal_selector_fails_closed_on_payload_hash_corruption(isolated_db):
    appended = _append(_claim(isolated_db), isolated_db)
    update_trigger = (
        "trg_regime_premarket_artifact_bundles_update_immutable"
    )
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER {update_trigger}")
        connection.exec_driver_sql(
            "UPDATE regime_premarket_artifact_bundles "
            "SET payload_sha256 = ? WHERE bundle_key = ?",
            ("0" * 64, appended.bundle.bundle_key),
        )

    assert _select(isolated_db) is None


def test_formal_selector_fails_closed_on_nonfinite_coverage_corruption(
    isolated_db,
):
    appended = _append(_claim(isolated_db), isolated_db)
    update_trigger = (
        "trg_regime_premarket_artifact_bundles_update_immutable"
    )
    with isolated_db._engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER {update_trigger}")
        connection.exec_driver_sql(
            "UPDATE regime_premarket_artifact_bundles "
            "SET coverage_json = ? WHERE bundle_key = ?",
            ('{"requested_count":NaN}', appended.bundle.bundle_key),
        )

    assert _select(isolated_db) is None
