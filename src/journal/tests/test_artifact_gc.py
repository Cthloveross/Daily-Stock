# -*- coding: utf-8 -*-
"""T1-T10 test contract for the bounded artifact GC (F-2a).

Frozen source: ``New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md`` §5.  All tests
are offline and run against the per-test isolated SQLite file provided by
``conftest.isolated_sqlite``.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.journal.ledger.artifact_gc import (
    ARTIFACT_GC_PREDICATE_VERSION,
    ARTIFACT_GC_TABLES,
    ArtifactGcError,
    run_artifact_gc,
)
from src.journal.ledger.artifact_gc_models import ARTIFACT_GC_RECEIPT_TABLE_NAME
from src.journal.ledger.models import CanonicalEvidenceSetRecord, ImportBatch
from src.journal.ledger.position_snapshot_models import (
    ConfirmedPositionSnapshot,
    PositionSnapshotArtifact,
)
from src.journal.ledger.position_snapshot_repository import (
    init_position_snapshot_schema,
)
from src.journal.ledger.refresh_models import (
    JournalRefreshArtifact,
    JournalRefreshPublication,
)
from src.journal.ledger.refresh_repository import (
    get_journal_refresh_status,
    init_refresh_schema,
)
from src.journal.ledger.repository import init_ledger_schema
from src.storage import get_db


ACCOUNT_KEY = "default_moomoo_us"
NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
REFRESH_TABLE = "journal_v2_refresh_artifacts"
SNAPSHOT_TABLE = "journal_v2_position_snapshot_artifacts"

# Ages relative to NOW and the default 7-day grace window.
DEAD = {"expires_at": NOW - timedelta(days=8)}  # expired beyond grace
IN_GRACE = {"expires_at": NOW - timedelta(days=1)}  # expired, inside grace
FRESH = {"expires_at": NOW + timedelta(hours=1)}  # not expired


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _init_schemas() -> None:
    init_ledger_schema()
    init_refresh_schema()
    init_position_snapshot_schema()


def _add_refresh_artifact(
    session: Any,
    *,
    suffix: str,
    recorded_at: datetime,
    expires_at: datetime,
    account_key: str = ACCOUNT_KEY,
    payload: str = "x" * 100,
) -> int:
    row = JournalRefreshArtifact(
        artifact_key=f"gc-refresh-artifact-{suffix}",
        broker="moomoo",
        account_key=account_key,
        account_binding="b" * 64,
        environment="LIVE",
        market="US",
        window_start=recorded_at - timedelta(days=1),
        window_end=recorded_at,
        generated_at=recorded_at,
        source_sha256=_digest(f"refresh-source-{suffix}"),
        evidence_sha256=_digest(f"refresh-evidence-{suffix}"),
        preview_key=_digest(f"refresh-preview-{suffix}"),
        confirm_allowed=True,
        payload_json=payload,
        expires_at=expires_at,
        recorded_at=recorded_at,
    )
    session.add(row)
    session.flush()
    return int(row.id)


def _add_refresh_publication(
    session: Any,
    *,
    artifact_id: int,
    suffix: str,
    recorded_at: datetime,
) -> int:
    """FK-complete publication chain (batch + canonical set + publication)."""
    batch = ImportBatch(
        batch_key=f"gc-test-batch-{suffix}",
        broker="moomoo",
        account_key=ACCOUNT_KEY,
        source_kind="openapi",
        source_schema="test",
        source_sha256=_digest(f"batch-source-{suffix}"),
        parser_name="test",
        parser_version="1",
        window_start=recorded_at - timedelta(days=1),
        window_end=recorded_at,
        source_timezone="UTC",
        status="succeeded",
        analysis_level="full",
        analysis_ready=True,
        order_observation_count=0,
        fill_observation_count=0,
        rejected_record_count=0,
        reconciliation_status="complete",
        reconciliation_json="{}",
        completeness_score=1,
        completeness_json="{}",
        warnings_json="[]",
        provenance_json="{}",
        recorded_at=recorded_at,
    )
    session.add(batch)
    session.flush()
    canonical = CanonicalEvidenceSetRecord(
        set_key=f"gc-test-canonical-{suffix}",
        canonical_set_sha256=_digest(f"canonical-{suffix}"),
        broker="moomoo",
        account_key=ACCOUNT_KEY,
        reader_name="test",
        reader_version="1",
        source_cutoff_at=recorded_at,
        source_batch_ids_json=json.dumps([batch.id]),
        analysis_ready=True,
        canonical_order_count=0,
        canonical_fill_count=0,
        blocking_issue_count=0,
        canonical_payload_json="{}",
        provenance_json="{}",
        recorded_at=recorded_at,
    )
    session.add(canonical)
    session.flush()
    publication = JournalRefreshPublication(
        publication_key=_digest(f"gc-publication-{suffix}"),
        refresh_artifact_id=artifact_id,
        import_batch_id=batch.id,
        canonical_set_id=canonical.id,
        broker="moomoo",
        account_key=ACCOUNT_KEY,
        account_binding="b" * 64,
        broker_queried_through=recorded_at,
        latest_fill_at=None,
        evidence_published_through=recorded_at,
        import_duplicate=False,
        recorded_at=recorded_at,
    )
    session.add(publication)
    session.flush()
    return int(publication.id)


def _add_snapshot_artifact(
    session: Any,
    *,
    suffix: str,
    recorded_at: datetime,
    expires_at: datetime,
    account_key: str = ACCOUNT_KEY,
    payload: str = "y" * 80,
) -> int:
    row = PositionSnapshotArtifact(
        artifact_key=_digest(f"gc-snapshot-artifact-{suffix}"),
        broker="moomoo",
        account_key=account_key,
        account_binding_id="c" * 64,
        binding_scheme="dsa-journal-account-binding-v1",
        refresh_publication_id=None,
        refresh_publication_key=None,
        account_continuity_sha256=None,
        environment="LIVE",
        market="US",
        source_schema="dsa.moomoo.position-snapshot.v1",
        parser_name="moomoo-position-snapshot",
        parser_version="1.1.0",
        query_started_at=recorded_at - timedelta(minutes=2),
        query_completed_at=recorded_at - timedelta(minutes=1),
        generated_at=recorded_at,
        broker_as_of_at=None,
        scope_sha256=_digest(f"snap-scope-{suffix}"),
        source_sha256=_digest(f"snap-source-{suffix}"),
        evidence_sha256=_digest(f"snap-evidence-{suffix}"),
        snapshot_sha256=_digest(f"snap-hash-{suffix}"),
        stability_evidence_sha256=_digest(f"snap-stability-{suffix}"),
        preview_key=_digest(f"snap-preview-{suffix}"),
        retrieval_complete=True,
        position_snapshot_complete=True,
        stability_status="stable",
        contract_spec_status="complete",
        position_count=0,
        contract_spec_count=0,
        confirm_allowed=True,
        historical_opening_proven=False,
        payload_json=payload,
        expires_at=expires_at,
        recorded_at=recorded_at,
    )
    session.add(row)
    session.flush()
    return int(row.id)


def _add_confirmed_snapshot(
    session: Any,
    *,
    artifact_id: int,
    suffix: str,
    recorded_at: datetime,
) -> int:
    row = ConfirmedPositionSnapshot(
        snapshot_key=_digest(f"gc-snapshot-{suffix}"),
        artifact_id=artifact_id,
        broker="moomoo",
        account_key=ACCOUNT_KEY,
        account_binding_id="c" * 64,
        binding_scheme="dsa-journal-account-binding-v1",
        refresh_publication_id=None,
        refresh_publication_key=None,
        account_continuity_sha256=None,
        environment="LIVE",
        market="US",
        query_started_at=recorded_at - timedelta(minutes=2),
        query_completed_at=recorded_at - timedelta(minutes=1),
        operation_completed_at=recorded_at,
        broker_as_of_at=None,
        scope_sha256=_digest(f"conf-scope-{suffix}"),
        source_sha256=_digest(f"conf-source-{suffix}"),
        evidence_sha256=_digest(f"conf-evidence-{suffix}"),
        snapshot_sha256=_digest(f"conf-hash-{suffix}"),
        stability_evidence_sha256=None,
        member_set_sha256=_digest(f"conf-members-{suffix}"),
        member_count=0,
        acknowledged_future_only=True,
        historical_opening_proven=False,
        cost_context_only=True,
        provenance_json="{}",
        provenance_sha256=None,
        recorded_at=recorded_at,
    )
    session.add(row)
    session.flush()
    return int(row.id)


def _recorded(days_ago: float) -> datetime:
    return NOW - timedelta(days=days_ago)


def _connect_rw(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_state(db_path: Path, table: str) -> tuple[int, str]:
    connection = sqlite3.connect(
        Path(db_path).resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        rows = connection.execute(
            f"SELECT * FROM {table} ORDER BY 1"
        ).fetchall()
    finally:
        connection.close()
    return len(rows), _digest(repr(rows))


def _table_ids(db_path: Path, table: str) -> list[int]:
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(f"SELECT id FROM {table} ORDER BY id").fetchall()
    finally:
        connection.close()
    return [int(row[0]) for row in rows]


def _receipts(db_path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"SELECT * FROM {ARTIFACT_GC_RECEIPT_TABLE_NAME} ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def _make_backup(db_path: Path, backup_path: Path) -> Path:
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def _apply(db_path: Path, backup_dir: Path, **kwargs: Any):
    backup = _make_backup(db_path, backup_dir / "gc-backup.db")
    return run_artifact_gc(
        db_path,
        apply=True,
        backup_path=backup,
        now=NOW,
        **kwargs,
    )


def _seed_standard_mixture(db_path: Path) -> dict[str, Any]:
    """Refresh: dead+dead+dead-latest; snapshot: dead+dead-latest."""
    _init_schemas()
    with get_db().session_scope() as session:
        r1 = _add_refresh_artifact(
            session,
            suffix="r1",
            recorded_at=_recorded(12),
            payload="x" * 1000,
            **DEAD,
        )
        r2 = _add_refresh_artifact(
            session,
            suffix="r2",
            recorded_at=_recorded(11),
            payload="x" * 500,
            **DEAD,
        )
        r3 = _add_refresh_artifact(
            session,
            suffix="r3-latest",
            recorded_at=_recorded(10),
            payload="x" * 400,
            **DEAD,
        )
        s1 = _add_snapshot_artifact(
            session,
            suffix="s1",
            recorded_at=_recorded(12),
            payload="y" * 700,
            **DEAD,
        )
        s2 = _add_snapshot_artifact(
            session,
            suffix="s2-latest",
            recorded_at=_recorded(10),
            payload="y" * 300,
            **DEAD,
        )
    return {"r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2}


def _result_for(report: Any, table: str) -> Any:
    matches = [item for item in report.results if item.table_name == table]
    assert len(matches) == 1
    return matches[0]


def test_t1_apply_deletes_expired_unreferenced_non_latest_artifacts(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    ids = _seed_standard_mixture(isolated_sqlite)

    report = _apply(isolated_sqlite, tmp_path)

    refresh = _result_for(report, REFRESH_TABLE)
    snapshot = _result_for(report, SNAPSHOT_TABLE)
    assert refresh.candidate_ids == (ids["r1"], ids["r2"])
    assert refresh.deleted_count == refresh.candidate_count == 2
    assert refresh.payload_bytes == 1000 + 500
    assert snapshot.candidate_ids == (ids["s1"],)
    assert snapshot.deleted_count == 1
    assert snapshot.payload_bytes == 700

    assert _table_ids(isolated_sqlite, REFRESH_TABLE) == [ids["r3"]]
    assert _table_ids(isolated_sqlite, SNAPSHOT_TABLE) == [ids["s2"]]

    receipts = _receipts(isolated_sqlite)
    assert [item["table_name"] for item in receipts] == [
        REFRESH_TABLE,
        SNAPSHOT_TABLE,
    ]
    for item in receipts:
        assert item["run_kind"] == "apply"
        assert item["predicate_version"] == ARTIFACT_GC_PREDICATE_VERSION
        assert item["grace_days"] == 7
        assert item["batch_limit"] == 500
        assert item["backup_path"] == report.backup_path
        assert item["backup_sha256"] == report.backup_sha256
        assert item["candidate_count"] == item["deleted_count"]
    assert json.loads(receipts[0]["deleted_ids_json"]) == [ids["r1"], ids["r2"]]
    assert receipts[0]["payload_bytes_reclaimed"] == 1500
    assert json.loads(receipts[1]["deleted_ids_json"]) == [ids["s1"]]
    assert receipts[1]["payload_bytes_reclaimed"] == 700


def test_t2_referenced_artifacts_are_never_deleted(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _init_schemas()
    with get_db().session_scope() as session:
        r_referenced = _add_refresh_artifact(
            session, suffix="ref", recorded_at=_recorded(13), **DEAD
        )
        _add_refresh_publication(
            session,
            artifact_id=r_referenced,
            suffix="ref",
            recorded_at=_recorded(13),
        )
        r_dead = _add_refresh_artifact(
            session, suffix="dead", recorded_at=_recorded(12), **DEAD
        )
        r_latest = _add_refresh_artifact(
            session, suffix="latest", recorded_at=_recorded(10), **DEAD
        )
        s_referenced = _add_snapshot_artifact(
            session, suffix="ref", recorded_at=_recorded(13), **DEAD
        )
        _add_confirmed_snapshot(
            session,
            artifact_id=s_referenced,
            suffix="ref",
            recorded_at=_recorded(13),
        )
        s_dead = _add_snapshot_artifact(
            session, suffix="dead", recorded_at=_recorded(12), **DEAD
        )
        s_latest = _add_snapshot_artifact(
            session, suffix="latest", recorded_at=_recorded(10), **DEAD
        )
    publications_before = _table_state(
        isolated_sqlite, "journal_v2_refresh_publications"
    )
    snapshots_before = _table_state(
        isolated_sqlite, "journal_v2_position_snapshots"
    )

    report = _apply(isolated_sqlite, tmp_path)

    assert _result_for(report, REFRESH_TABLE).candidate_ids == (r_dead,)
    assert _result_for(report, SNAPSHOT_TABLE).candidate_ids == (s_dead,)
    assert _table_ids(isolated_sqlite, REFRESH_TABLE) == sorted(
        [r_referenced, r_latest]
    )
    assert _table_ids(isolated_sqlite, SNAPSHOT_TABLE) == sorted(
        [s_referenced, s_latest]
    )
    assert (
        _table_state(isolated_sqlite, "journal_v2_refresh_publications")
        == publications_before
    )
    assert (
        _table_state(isolated_sqlite, "journal_v2_position_snapshots")
        == snapshots_before
    )


def test_t3_unexpired_or_in_grace_artifacts_are_kept(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _init_schemas()
    with get_db().session_scope() as session:
        _add_refresh_artifact(
            session, suffix="fresh", recorded_at=_recorded(10), **FRESH
        )
        _add_refresh_artifact(
            session, suffix="grace", recorded_at=_recorded(2), **IN_GRACE
        )
        _add_refresh_artifact(
            session, suffix="latest", recorded_at=_recorded(0.5), **FRESH
        )
        _add_snapshot_artifact(
            session, suffix="grace", recorded_at=_recorded(2), **IN_GRACE
        )
        _add_snapshot_artifact(
            session, suffix="latest", recorded_at=_recorded(0.5), **FRESH
        )
    refresh_before = _table_state(isolated_sqlite, REFRESH_TABLE)
    snapshot_before = _table_state(isolated_sqlite, SNAPSHOT_TABLE)

    report = _apply(isolated_sqlite, tmp_path)

    for table in ARTIFACT_GC_TABLES:
        result = _result_for(report, table)
        assert result.candidate_count == 0
        assert result.deleted_count == 0
    assert _table_state(isolated_sqlite, REFRESH_TABLE) == refresh_before
    assert _table_state(isolated_sqlite, SNAPSHOT_TABLE) == snapshot_before
    # Empty candidate sets still leave zero-count receipts (contract §3 step 3).
    assert [item["deleted_count"] for item in _receipts(isolated_sqlite)] == [0, 0]


def test_t4_latest_artifact_survives_and_refresh_status_is_unchanged(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _init_schemas()
    with get_db().session_scope() as session:
        old_id = _add_refresh_artifact(
            session, suffix="old", recorded_at=_recorded(12), **DEAD
        )
        latest_id = _add_refresh_artifact(
            session, suffix="latest", recorded_at=_recorded(10), **DEAD
        )
    status_before = get_journal_refresh_status(ACCOUNT_KEY, now=NOW)
    assert status_before.latest_artifact_id == latest_id

    report = _apply(isolated_sqlite, tmp_path)

    assert _result_for(report, REFRESH_TABLE).candidate_ids == (old_id,)
    assert _table_ids(isolated_sqlite, REFRESH_TABLE) == [latest_id]
    status_after = get_journal_refresh_status(ACCOUNT_KEY, now=NOW)
    assert status_after == status_before


def test_t5_dry_run_is_zero_write_and_reports_the_apply_set(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _seed_standard_mixture(isolated_sqlite)
    watched = (REFRESH_TABLE, SNAPSHOT_TABLE, ARTIFACT_GC_RECEIPT_TABLE_NAME)
    states_before = {
        table: _table_state(isolated_sqlite, table) for table in watched
    }

    dry = run_artifact_gc(isolated_sqlite, apply=False, now=NOW)

    assert dry.run_kind == "dry_run"
    for table in watched:
        assert _table_state(isolated_sqlite, table) == states_before[table]
    for result in dry.results:
        assert result.deleted_count == 0
        assert result.receipt_id is None

    applied = _apply(isolated_sqlite, tmp_path)
    for table in ARTIFACT_GC_TABLES:
        dry_result = _result_for(dry, table)
        apply_result = _result_for(applied, table)
        assert dry_result.candidate_ids == apply_result.candidate_ids
        assert apply_result.deleted_count == len(apply_result.candidate_ids)
        assert dry_result.candidate_ids_sha256 == apply_result.candidate_ids_sha256


def test_t6_second_apply_with_same_parameters_deletes_nothing(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _seed_standard_mixture(isolated_sqlite)
    first = _apply(isolated_sqlite, tmp_path)
    assert sum(item.deleted_count for item in first.results) == 3

    second = _apply(isolated_sqlite, tmp_path)

    for table in ARTIFACT_GC_TABLES:
        result = _result_for(second, table)
        assert result.candidate_count == 0
        assert result.deleted_count == 0
    receipts = _receipts(isolated_sqlite)
    assert len(receipts) == 4
    assert [item["deleted_count"] for item in receipts[-2:]] == [0, 0]


def test_t7_triggers_survive_apply_and_still_abort_external_writes(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _seed_standard_mixture(isolated_sqlite)
    _apply(isolated_sqlite, tmp_path)

    connection = _connect_rw(isolated_sqlite)
    try:
        for table in ARTIFACT_GC_TABLES:
            triggers = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND tbl_name=?",
                    (table,),
                )
            }
            assert f"trg_{table}_update_immutable" in triggers
            assert f"trg_{table}_delete_immutable" in triggers
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                connection.execute(f"DELETE FROM {table}")
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                connection.execute(
                    f"UPDATE {table} SET account_key = 'tampered'"
                )
    finally:
        connection.close()


def test_t7_rowcount_mismatch_rolls_back_rows_triggers_and_receipts(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _seed_standard_mixture(isolated_sqlite)
    states_before = {
        table: _table_state(isolated_sqlite, table)
        for table in (REFRESH_TABLE, SNAPSHOT_TABLE)
    }
    receipts_before = len(_receipts(isolated_sqlite))
    backup = _make_backup(isolated_sqlite, tmp_path / "t7-backup.db")

    def sabotage(
        connection: sqlite3.Connection,
        table: str,
        candidate_ids: tuple[int, ...],
    ) -> None:
        # The DELETE trigger is dropped at this point; removing one candidate
        # out-of-band forces the executor's rowcount assertion to fail.
        connection.execute(
            f"DELETE FROM {table} WHERE id = ?", (candidate_ids[0],)
        )

    with pytest.raises(ArtifactGcError, match="rolling back"):
        run_artifact_gc(
            isolated_sqlite,
            apply=True,
            backup_path=backup,
            now=NOW,
            _pre_delete_hook=sabotage,
        )

    for table, state in states_before.items():
        assert _table_state(isolated_sqlite, table) == state
    assert len(_receipts(isolated_sqlite)) == receipts_before
    connection = _connect_rw(isolated_sqlite)
    try:
        for table in ARTIFACT_GC_TABLES:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                connection.execute(f"DELETE FROM {table}")
    finally:
        connection.close()


def test_t8_out_of_scope_tables_are_untouched_by_apply(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _init_schemas()
    with get_db().session_scope() as session:
        referenced = _add_refresh_artifact(
            session, suffix="ref", recorded_at=_recorded(13), **DEAD
        )
        _add_refresh_publication(
            session, artifact_id=referenced, suffix="ref", recorded_at=_recorded(13)
        )
        _add_refresh_artifact(
            session, suffix="dead", recorded_at=_recorded(12), **DEAD
        )
        _add_refresh_artifact(
            session, suffix="latest", recorded_at=_recorded(10), **DEAD
        )
        s_referenced = _add_snapshot_artifact(
            session, suffix="ref", recorded_at=_recorded(13), **DEAD
        )
        _add_confirmed_snapshot(
            session,
            artifact_id=s_referenced,
            suffix="ref",
            recorded_at=_recorded(13),
        )
        _add_snapshot_artifact(
            session, suffix="dead", recorded_at=_recorded(12), **DEAD
        )
        _add_snapshot_artifact(
            session, suffix="latest", recorded_at=_recorded(10), **DEAD
        )
    connection = sqlite3.connect(str(isolated_sqlite))
    try:
        all_tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
    finally:
        connection.close()
    protected = [
        table
        for table in all_tables
        if table not in ARTIFACT_GC_TABLES
        and table != ARTIFACT_GC_RECEIPT_TABLE_NAME
        and not table.startswith("sqlite_")
    ]
    # The forbidden zones from contract §1/§6 must be part of the sweep.
    assert "journal_v2_refresh_publications" in protected
    assert "journal_v2_position_snapshots" in protected
    assert "journal_v2_position_snapshot_members" in protected
    assert "journal_v2_import_batches" in protected
    assert "journal_v2_canonical_evidence_sets" in protected
    states_before = {
        table: _table_state(isolated_sqlite, table) for table in protected
    }

    report = _apply(isolated_sqlite, tmp_path)

    assert sum(item.deleted_count for item in report.results) == 2
    for table, state in states_before.items():
        assert _table_state(isolated_sqlite, table) == state, table


def test_t9_apply_fails_closed_without_a_validated_backup(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    ids = _seed_standard_mixture(isolated_sqlite)
    all_ids_before = {
        table: _table_ids(isolated_sqlite, table) for table in ARTIFACT_GC_TABLES
    }

    with pytest.raises(ArtifactGcError, match="backup-path"):
        run_artifact_gc(isolated_sqlite, apply=True, now=NOW)

    with pytest.raises(ArtifactGcError, match="does not exist"):
        run_artifact_gc(
            isolated_sqlite,
            apply=True,
            backup_path=tmp_path / "missing-backup.db",
            now=NOW,
        )

    fake_backup = tmp_path / "not-a-database.db"
    fake_backup.write_bytes(b"this is not a sqlite database at all")
    with pytest.raises(ArtifactGcError, match="not a SQLite database"):
        run_artifact_gc(
            isolated_sqlite, apply=True, backup_path=fake_backup, now=NOW
        )

    corrupt_backup = _make_backup(isolated_sqlite, tmp_path / "corrupt.db")
    size = corrupt_backup.stat().st_size
    with corrupt_backup.open("rb+") as handle:
        handle.truncate(max(4096, size // 2))
    with pytest.raises(ArtifactGcError, match="integrity"):
        run_artifact_gc(
            isolated_sqlite, apply=True, backup_path=corrupt_backup, now=NOW
        )

    not_sqlite_target = tmp_path / "plain.txt"
    not_sqlite_target.write_text("postgres://not-supported")
    with pytest.raises(ArtifactGcError, match="SQLite"):
        run_artifact_gc(not_sqlite_target, apply=False, now=NOW)

    with pytest.raises(ArtifactGcError, match="grace_days"):
        run_artifact_gc(isolated_sqlite, apply=False, grace_days=0, now=NOW)

    for table in ARTIFACT_GC_TABLES:
        assert _table_ids(isolated_sqlite, table) == all_ids_before[table]
    assert ids  # rows seeded and fully intact


def test_t9_cli_requires_backup_path_with_apply(isolated_sqlite: Path) -> None:
    cli = importlib.import_module("scripts.artifact_gc")
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--apply", "--db", str(isolated_sqlite)])
    assert excinfo.value.code == 2


def test_t10_receipts_are_append_only_and_fingerprint_recomputable(
    isolated_sqlite: Path,
    tmp_path: Path,
) -> None:
    _seed_standard_mixture(isolated_sqlite)
    report = _apply(isolated_sqlite, tmp_path)

    receipts = _receipts(isolated_sqlite)
    assert len(receipts) == 2
    for item, result in zip(receipts, report.results):
        recomputed = _digest(item["deleted_ids_json"])
        assert recomputed == item["deleted_ids_sha256"]
        assert item["deleted_ids_sha256"] == result.candidate_ids_sha256
        assert json.loads(item["deleted_ids_json"]) == list(result.candidate_ids)
        assert item["receipt_key"] == result.receipt_key

    connection = _connect_rw(isolated_sqlite)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(
                f"UPDATE {ARTIFACT_GC_RECEIPT_TABLE_NAME} SET deleted_count = 0"
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(f"DELETE FROM {ARTIFACT_GC_RECEIPT_TABLE_NAME}")
    finally:
        connection.close()


def test_dry_run_receipt_is_optional_and_marked_dry_run(
    isolated_sqlite: Path,
) -> None:
    """Contract §3.2: ``--receipt`` writes an explicit dry_run receipt row."""
    _seed_standard_mixture(isolated_sqlite)

    report = run_artifact_gc(
        isolated_sqlite,
        apply=False,
        now=NOW,
        write_dry_run_receipt=True,
    )

    receipts = _receipts(isolated_sqlite)
    assert len(receipts) == 2
    for item, result in zip(receipts, report.results):
        assert item["run_kind"] == "dry_run"
        assert item["deleted_count"] == 0
        assert item["candidate_count"] == result.candidate_count
        assert item["backup_path"] is None
        assert item["backup_sha256"] is None
    # The reported candidates are still intact rows.
    assert len(_table_ids(isolated_sqlite, REFRESH_TABLE)) == 3
    assert len(_table_ids(isolated_sqlite, SNAPSHOT_TABLE)) == 2


def test_gc_scope_rejects_out_of_scope_table_names(
    isolated_sqlite: Path,
) -> None:
    _init_schemas()
    with pytest.raises(ArtifactGcError, match="outside the artifact GC scope"):
        run_artifact_gc(
            isolated_sqlite,
            apply=False,
            tables=("journal_v2_refresh_publications",),
            now=NOW,
        )
