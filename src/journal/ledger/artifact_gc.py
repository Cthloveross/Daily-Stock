# -*- coding: utf-8 -*-
"""Explicit, bounded GC for short-lived Journal preview artifacts (F-2a).

Frozen design: ``New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md``.  Scope is
exactly two tables — ``journal_v2_refresh_artifacts`` and
``journal_v2_position_snapshot_artifacts``.  A candidate row must be all of:

1. expired for more than the grace period (default 7 days);
2. never confirmed — no publication / confirmed-snapshot reference;
3. not the account's newest artifact (so ``get_journal_refresh_status``
   output is byte-for-byte unchanged).

Both tables are protected by SQLite UPDATE/DELETE deny triggers, so apply
mode runs one ``BEGIN IMMEDIATE`` transaction per table: recompute the
predicate in-transaction, drop ONLY the DELETE trigger, run one bounded
DELETE with a rowcount assertion, recreate the trigger from the owner
module's shared DDL constant, assert both guards exist in ``sqlite_master``,
append an immutable GC receipt row, then commit.  Any failure rolls the
whole transaction back, restoring rows and triggers.

This module is deliberately path-based and uses the stdlib ``sqlite3``
driver: dry-run opens the database file with ``mode=ro`` and can never
write; apply opens ``mode=rw`` (never creates a database).  Non-SQLite
targets fail closed.  There is no startup sweep, no scheduler, and no API
endpoint — the only entry point is ``scripts/artifact_gc.py``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from sqlalchemy.dialects import sqlite as _sqlite_dialect
from sqlalchemy.schema import CreateTable

from src.journal.ledger.artifact_gc_models import (
    ARTIFACT_GC_RECEIPT_TABLE_NAME,
    ArtifactGcReceipt,
)
from src.journal.ledger.position_snapshot_models import (
    position_snapshot_guard_trigger_ddl,
    position_snapshot_guard_trigger_name,
)
from src.journal.ledger.refresh_repository import (
    refresh_guard_trigger_ddl,
    refresh_guard_trigger_name,
)
from src.journal.ledger.repository import ledger_guard_trigger_ddl

__all__ = [
    "ARTIFACT_GC_PREDICATE_VERSION",
    "ARTIFACT_GC_TABLES",
    "DEFAULT_ARTIFACT_GC_BATCH_LIMIT",
    "DEFAULT_ARTIFACT_GC_GRACE_DAYS",
    "ArtifactGcError",
    "ArtifactGcReport",
    "ArtifactGcTableResult",
    "run_artifact_gc",
]


logger = logging.getLogger(__name__)

ARTIFACT_GC_PREDICATE_VERSION = "artifact_gc_predicate_v1"
DEFAULT_ARTIFACT_GC_GRACE_DAYS = 7
DEFAULT_ARTIFACT_GC_BATCH_LIMIT = 500
_SQLITE_HEADER = b"SQLite format 3\x00"


class ArtifactGcError(RuntimeError):
    """Raised when a GC precondition or in-transaction assertion fails."""


@dataclass(frozen=True)
class _GcTableSpec:
    """One in-scope artifact table plus its owner-module trigger DDL."""

    table_name: str
    reference_table: str
    reference_column: str
    required_tables: tuple[str, ...]
    trigger_name: Callable[[str, str], str]
    trigger_ddl: Callable[[str, str], str]


# The ONLY two tables the GC may ever touch (contract §1; everything else —
# publications, confirmed snapshots/members, all evidence tables,
# regime_premarket_* and opportunity_* — is a red line).
_TABLE_SPECS = (
    _GcTableSpec(
        table_name="journal_v2_refresh_artifacts",
        reference_table="journal_v2_refresh_publications",
        reference_column="refresh_artifact_id",
        required_tables=(
            "journal_v2_refresh_artifacts",
            "journal_v2_refresh_publications",
        ),
        trigger_name=refresh_guard_trigger_name,
        trigger_ddl=refresh_guard_trigger_ddl,
    ),
    _GcTableSpec(
        table_name="journal_v2_position_snapshot_artifacts",
        reference_table="journal_v2_position_snapshots",
        reference_column="artifact_id",
        required_tables=(
            "journal_v2_position_snapshot_artifacts",
            "journal_v2_position_snapshots",
        ),
        trigger_name=position_snapshot_guard_trigger_name,
        trigger_ddl=position_snapshot_guard_trigger_ddl,
    ),
)
ARTIFACT_GC_TABLES = tuple(spec.table_name for spec in _TABLE_SPECS)
_SPEC_BY_NAME = {spec.table_name: spec for spec in _TABLE_SPECS}


@dataclass(frozen=True)
class ArtifactGcTableResult:
    """Outcome of one bounded GC pass over one artifact table."""

    table_name: str
    run_kind: str
    candidate_count: int
    deleted_count: int
    payload_bytes: int
    candidate_ids: tuple[int, ...]
    candidate_ids_sha256: str
    receipt_id: Optional[int]
    receipt_key: Optional[str]
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True)
class ArtifactGcReport:
    """Outcome of one CLI invocation across the requested tables."""

    db_path: str
    run_kind: str
    grace_days: int
    batch_limit: int
    cutoff_utc: datetime
    backup_path: Optional[str]
    backup_sha256: Optional[str]
    results: tuple[ArtifactGcTableResult, ...]


def _format_db_datetime(value: datetime) -> str:
    """Render a UTC datetime exactly as SQLAlchemy's SQLite DATETIME stores it."""
    if value.tzinfo is None:
        raise ArtifactGcError("artifact GC clocks must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_ids_json(ids: Sequence[int]) -> str:
    return json.dumps([int(item) for item in ids], separators=(",", ":"))


def _require_sqlite_file(path: Path, *, role: str) -> None:
    if not path.is_file():
        raise ArtifactGcError(f"{role} does not exist or is not a file: {path}")
    try:
        with path.open("rb") as handle:
            header = handle.read(len(_SQLITE_HEADER))
    except OSError as exc:
        raise ArtifactGcError(f"{role} is not readable: {path}") from exc
    if header != _SQLITE_HEADER:
        raise ArtifactGcError(
            f"{role} is not a SQLite database (artifact GC only supports "
            f"SQLite): {path}"
        )


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    # ``mode=rw`` (not ``rwc``) never creates a missing database file.
    uri = path.resolve().as_uri() + ("?mode=ro" if read_only else "?mode=rw")
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    # Same safety posture as the application engine (src/storage.py): FK
    # enforcement is the second line of defense behind the predicate.
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=ON")
    return connection


def _existing_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _assert_guard_triggers(
    connection: sqlite3.Connection,
    spec: _GcTableSpec,
) -> None:
    """Assert both UPDATE and DELETE deny triggers exist for one table."""
    expected = {
        spec.trigger_name(spec.table_name, "UPDATE"),
        spec.trigger_name(spec.table_name, "DELETE"),
    }
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'trigger' AND tbl_name = ?",
        (spec.table_name,),
    ).fetchall()
    present = {str(row[0]) for row in rows}
    missing = expected - present
    if missing:
        raise ArtifactGcError(
            f"append-only guard triggers missing on {spec.table_name}: "
            f"{sorted(missing)}"
        )


def _candidate_sql(spec: _GcTableSpec) -> str:
    """Predicate ``artifact_gc_predicate_v1`` (contract §2), one table."""
    return (
        "SELECT a.id, LENGTH(a.payload_json) "
        f"FROM {spec.table_name} AS a "
        "WHERE a.expires_at < :cutoff "
        "AND NOT EXISTS ("
        f"SELECT 1 FROM {spec.reference_table} AS p "
        f"WHERE p.{spec.reference_column} = a.id"
        ") "
        "AND a.id <> ("
        f"SELECT a2.id FROM {spec.table_name} AS a2 "
        "WHERE a2.broker = a.broker AND a2.account_key = a.account_key "
        "ORDER BY a2.recorded_at DESC, a2.id DESC LIMIT 1"
        ") "
        "ORDER BY a.recorded_at ASC, a.id ASC "
        "LIMIT :batch_limit"
    )


def _select_candidates(
    connection: sqlite3.Connection,
    spec: _GcTableSpec,
    *,
    cutoff: datetime,
    batch_limit: int,
) -> tuple[tuple[int, ...], int]:
    rows = connection.execute(
        _candidate_sql(spec),
        {"cutoff": _format_db_datetime(cutoff), "batch_limit": batch_limit},
    ).fetchall()
    ids = tuple(int(row[0]) for row in rows)
    payload_bytes = sum(int(row[1] or 0) for row in rows)
    return ids, payload_bytes


def _ensure_receipt_table(connection: sqlite3.Connection) -> None:
    """Create the append-only receipt table + guards if this DB predates it.

    Uses the SQLAlchemy model as the single schema source and the ledger
    guard DDL constant from ``repository.py`` — no second hand-written copy.
    """
    if ARTIFACT_GC_RECEIPT_TABLE_NAME not in _existing_tables(connection):
        ddl = str(
            CreateTable(ArtifactGcReceipt.__table__).compile(
                dialect=_sqlite_dialect.dialect()
            )
        )
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(ddl)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    for operation in ("UPDATE", "DELETE"):
        connection.execute(
            ledger_guard_trigger_ddl(ARTIFACT_GC_RECEIPT_TABLE_NAME, operation)
        )


def _insert_receipt(
    connection: sqlite3.Connection,
    *,
    run_kind: str,
    table_name: str,
    grace_days: int,
    batch_limit: int,
    candidate_ids: Sequence[int],
    deleted_count: int,
    payload_bytes: int,
    backup_path: Optional[str],
    backup_sha256: Optional[str],
    started_at: datetime,
    completed_at: datetime,
) -> tuple[int, str]:
    ids_json = _canonical_ids_json(candidate_ids)
    ids_sha256 = _sha256_text(ids_json)
    started_at_text = _format_db_datetime(started_at)
    receipt_key = _sha256_text(
        json.dumps(
            {
                "kind": "journal_v2_artifact_gc_receipt_v1",
                "table_name": table_name,
                "deleted_ids_sha256": ids_sha256,
                "started_at": started_at_text,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    cursor = connection.execute(
        f"INSERT INTO {ARTIFACT_GC_RECEIPT_TABLE_NAME} ("
        "receipt_key, run_kind, table_name, predicate_version, grace_days, "
        "batch_limit, candidate_count, deleted_count, payload_bytes_reclaimed, "
        "deleted_ids_json, deleted_ids_sha256, backup_path, backup_sha256, "
        "started_at, completed_at, recorded_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            receipt_key,
            run_kind,
            table_name,
            ARTIFACT_GC_PREDICATE_VERSION,
            grace_days,
            batch_limit,
            len(candidate_ids),
            deleted_count,
            payload_bytes,
            ids_json,
            ids_sha256,
            backup_path,
            backup_sha256,
            started_at_text,
            _format_db_datetime(completed_at),
            _format_db_datetime(datetime.now(timezone.utc)),
        ),
    )
    return int(cursor.lastrowid), receipt_key


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_backup(backup_path: Path, db_path: Path) -> str:
    """Fail closed unless the backup is a readable, integrity-ok SQLite file."""
    _require_sqlite_file(backup_path, role="backup file")
    try:
        if backup_path.resolve().samefile(db_path.resolve()):
            raise ArtifactGcError(
                "backup path must not be the target database itself"
            )
    except OSError:
        pass
    check_connection = _connect(backup_path, read_only=True)
    try:
        row = check_connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise ArtifactGcError(
            f"backup integrity_check failed: {backup_path}: {exc}"
        ) from exc
    finally:
        check_connection.close()
    if row is None or str(row[0]).lower() != "ok":
        raise ArtifactGcError(
            f"backup integrity_check is not ok: {backup_path}: "
            f"{None if row is None else row[0]}"
        )
    return _sha256_file(backup_path)


def _resolve_specs(tables: Optional[Sequence[str]]) -> tuple[_GcTableSpec, ...]:
    if tables is None:
        return _TABLE_SPECS
    resolved = []
    for name in tables:
        spec = _SPEC_BY_NAME.get(str(name))
        if spec is None:
            raise ArtifactGcError(
                f"table {name!r} is outside the artifact GC scope; "
                f"allowed: {list(ARTIFACT_GC_TABLES)}"
            )
        if spec not in resolved:
            resolved.append(spec)
    if not resolved:
        raise ArtifactGcError("no artifact GC tables requested")
    return tuple(resolved)


def _apply_one_table(
    connection: sqlite3.Connection,
    spec: _GcTableSpec,
    *,
    cutoff: datetime,
    grace_days: int,
    batch_limit: int,
    backup_path: str,
    backup_sha256: str,
    pre_delete_hook: Optional[Callable[[sqlite3.Connection, str, tuple[int, ...]], None]],
) -> ArtifactGcTableResult:
    """Contract §3 fixed sequence: one BEGIN IMMEDIATE transaction per table."""
    started_at = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()
    connection.execute("BEGIN IMMEDIATE")
    try:
        # Step 3: recompute the candidate set inside the write transaction.
        ids, payload_bytes = _select_candidates(
            connection,
            spec,
            cutoff=cutoff,
            batch_limit=batch_limit,
        )
        deleted_count = 0
        if ids:
            # Step 5: drop ONLY the DELETE trigger; UPDATE guard stays live.
            connection.execute(
                "DROP TRIGGER "
                + spec.trigger_name(spec.table_name, "DELETE")
            )
            if pre_delete_hook is not None:
                pre_delete_hook(connection, spec.table_name, ids)
            # Step 6: bounded delete with a strict rowcount assertion.
            placeholders = ", ".join("?" for _ in ids)
            cursor = connection.execute(
                f"DELETE FROM {spec.table_name} "
                f"WHERE id IN ({placeholders})",
                ids,
            )
            if cursor.rowcount != len(ids):
                raise ArtifactGcError(
                    f"bounded delete affected {cursor.rowcount} rows but "
                    f"{len(ids)} candidates were selected on "
                    f"{spec.table_name}; rolling back"
                )
            # Step 7: recreate the trigger from the owner module's DDL.
            connection.execute(spec.trigger_ddl(spec.table_name, "DELETE"))
            deleted_count = len(ids)
        # Step 8: both guards must exist before the transaction may commit.
        _assert_guard_triggers(connection, spec)
        # Step 9: append the immutable receipt (also for empty candidate sets).
        completed_at = datetime.now(timezone.utc)
        receipt_id, receipt_key = _insert_receipt(
            connection,
            run_kind="apply",
            table_name=spec.table_name,
            grace_days=grace_days,
            batch_limit=batch_limit,
            candidate_ids=ids,
            deleted_count=deleted_count,
            payload_bytes=payload_bytes,
            backup_path=backup_path,
            backup_sha256=backup_sha256,
            started_at=started_at,
            completed_at=completed_at,
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    # Step 10: post-commit invariant + structured log line.
    _assert_guard_triggers(connection, spec)
    logger.info(
        "artifact_gc apply table=%s candidates=%d deleted=%d bytes=%d "
        "duration_ms=%d backup=%s receipt_id=%d",
        spec.table_name,
        len(ids),
        deleted_count,
        payload_bytes,
        int((time.monotonic() - monotonic_start) * 1000),
        backup_path,
        receipt_id,
    )
    return ArtifactGcTableResult(
        table_name=spec.table_name,
        run_kind="apply",
        candidate_count=len(ids),
        deleted_count=deleted_count,
        payload_bytes=payload_bytes,
        candidate_ids=ids,
        candidate_ids_sha256=_sha256_text(_canonical_ids_json(ids)),
        receipt_id=receipt_id,
        receipt_key=receipt_key,
        started_at=started_at,
        completed_at=completed_at,
    )


def _dry_run_one_table(
    connection: sqlite3.Connection,
    spec: _GcTableSpec,
    *,
    cutoff: datetime,
    grace_days: int,
    batch_limit: int,
    write_receipt: bool,
) -> ArtifactGcTableResult:
    started_at = datetime.now(timezone.utc)
    ids, payload_bytes = _select_candidates(
        connection,
        spec,
        cutoff=cutoff,
        batch_limit=batch_limit,
    )
    completed_at = datetime.now(timezone.utc)
    receipt_id: Optional[int] = None
    receipt_key: Optional[str] = None
    if write_receipt:
        connection.execute("BEGIN IMMEDIATE")
        try:
            receipt_id, receipt_key = _insert_receipt(
                connection,
                run_kind="dry_run",
                table_name=spec.table_name,
                grace_days=grace_days,
                batch_limit=batch_limit,
                candidate_ids=ids,
                deleted_count=0,
                payload_bytes=payload_bytes,
                backup_path=None,
                backup_sha256=None,
                started_at=started_at,
                completed_at=completed_at,
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    logger.info(
        "artifact_gc dry-run table=%s candidates=%d bytes=%d",
        spec.table_name,
        len(ids),
        payload_bytes,
    )
    return ArtifactGcTableResult(
        table_name=spec.table_name,
        run_kind="dry_run",
        candidate_count=len(ids),
        deleted_count=0,
        payload_bytes=payload_bytes,
        candidate_ids=ids,
        candidate_ids_sha256=_sha256_text(_canonical_ids_json(ids)),
        receipt_id=receipt_id,
        receipt_key=receipt_key,
        started_at=started_at,
        completed_at=completed_at,
    )


def run_artifact_gc(
    db_path: str | Path,
    *,
    apply: bool = False,
    backup_path: Optional[str | Path] = None,
    tables: Optional[Sequence[str]] = None,
    grace_days: int = DEFAULT_ARTIFACT_GC_GRACE_DAYS,
    batch_limit: int = DEFAULT_ARTIFACT_GC_BATCH_LIMIT,
    now: Optional[datetime] = None,
    write_dry_run_receipt: bool = False,
    _pre_delete_hook: Optional[
        Callable[[sqlite3.Connection, str, tuple[int, ...]], None]
    ] = None,
) -> ArtifactGcReport:
    """Run the bounded artifact GC (dry-run by default).

    Dry-run opens the SQLite file read-only (``mode=ro``) and cannot write.
    Apply requires a same-day verified backup path (contract §4): the file
    must exist, be a valid SQLite database, and pass ``PRAGMA
    integrity_check``; its path and sha256 are recorded in every receipt.
    ``_pre_delete_hook`` is a test-only fault-injection point that runs
    inside the apply transaction after the DELETE trigger is dropped.
    """
    if grace_days < 1:
        raise ArtifactGcError("grace_days must be >= 1 (contract §3.2)")
    if batch_limit < 1:
        raise ArtifactGcError("batch_limit must be >= 1")
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ArtifactGcError("artifact GC clocks must be timezone-aware")
    cutoff = now.astimezone(timezone.utc) - timedelta(days=grace_days)

    target = Path(db_path)
    _require_sqlite_file(target, role="target database")
    specs = _resolve_specs(tables)

    backup_text: Optional[str] = None
    backup_sha256: Optional[str] = None
    if apply:
        if backup_path is None:
            raise ArtifactGcError(
                "--apply requires --backup-path pointing at a same-day "
                "verified SQLite backup (contract §4); refusing to delete "
                "without one"
            )
        backup = Path(backup_path)
        backup_sha256 = _validate_backup(backup, target)
        backup_text = str(backup.resolve())

    read_only = not apply and not write_dry_run_receipt
    connection = _connect(target, read_only=read_only)
    try:
        existing = _existing_tables(connection)
        for spec in specs:
            missing = [
                name for name in spec.required_tables if name not in existing
            ]
            if missing:
                raise ArtifactGcError(
                    f"schema for {spec.table_name} is not initialized "
                    f"(missing tables: {missing}); run the application "
                    "schema init first"
                )
        if not read_only:
            _ensure_receipt_table(connection)
        results = []
        for spec in specs:
            if apply:
                assert backup_text is not None and backup_sha256 is not None
                results.append(
                    _apply_one_table(
                        connection,
                        spec,
                        cutoff=cutoff,
                        grace_days=grace_days,
                        batch_limit=batch_limit,
                        backup_path=backup_text,
                        backup_sha256=backup_sha256,
                        pre_delete_hook=_pre_delete_hook,
                    )
                )
            else:
                results.append(
                    _dry_run_one_table(
                        connection,
                        spec,
                        cutoff=cutoff,
                        grace_days=grace_days,
                        batch_limit=batch_limit,
                        write_receipt=write_dry_run_receipt,
                    )
                )
    finally:
        connection.close()

    return ArtifactGcReport(
        db_path=str(target.resolve()),
        run_kind="apply" if apply else "dry_run",
        grace_days=grace_days,
        batch_limit=batch_limit,
        cutoff_utc=cutoff,
        backup_path=backup_text,
        backup_sha256=backup_sha256,
        results=tuple(results),
    )
