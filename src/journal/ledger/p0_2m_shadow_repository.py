# -*- coding: utf-8 -*-
"""Append-only live-forward telemetry and strict 20-session export.

Nothing in this module enables Telegram or treats historical replay as
prospective evidence.  Export always emits the fixed observation mode
``live_forward_shadow`` and materialises every expected symbol/session pair,
including entirely missing days and individual missing endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import re
import secrets
import threading
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from sqlalchemy import MetaData, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.schema import CreateIndex, CreateTable

from src.journal.ledger.p0_2m_shadow_models import (
    P02mActivationArtifactAttestation,
    P02mShadowEnrollment,
    P02mShadowExportAttestation,
    P02mShadowObservation,
)
from src.opportunities.p0_2m_signal import P0_2M_SIGNAL_VERSION
from src.storage import Base, get_db

LIVE_FORWARD_SHADOW_MODE = "live_forward_shadow"
P0_2M_SHADOW_EXPORT_SCHEMA_VERSION = "p0_2m_live_shadow_export_v1"
P0_2M_SHADOW_EXPORT_ATTESTATION_SCHEMA_VERSION = (
    "p0_2m_live_shadow_export_attestation_v1"
)
P0_2M_SHADOW_EXPORT_ATTESTATION_AUTHORITY = (
    "sqlite_append_only_shadow_export_v1"
)
P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_SCHEMA_VERSION = (
    "p0_2m_activation_artifact_attestation_v1"
)
P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_AUTHORITY = (
    "sqlite_append_only_p0_2m_calibration_v1"
)
_EXPECTED_SHADOW_SESSIONS = 20
_EXPECTED_UNIVERSE_SIZE = 69
_MAX_LIVE_WRITE_SKEW_SECONDS = 300.0
_MAX_ENDPOINT_LIVE_LAG_SECONDS = 75.0
_ET = ZoneInfo("America/New_York")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCHEMA_LOCK = threading.RLock()
_READY_ENGINES: set[int] = set()
_ENROLLMENT_TABLE = "journal_v2_p0_2m_shadow_enrollments"
_OBSERVATION_TABLE = "journal_v2_p0_2m_shadow_observations"
_LEGACY_ENROLLMENT_TABLE = f"{_ENROLLMENT_TABLE}_pre_run_identity"
_LEGACY_OBSERVATION_TABLE = f"{_OBSERVATION_TABLE}_pre_run_identity"
_VALID_ENQUEUE_EVIDENCE_MODES = {
    "counterfactual_no_send_same_queue",
    "actual_telegram_queue_receipt",
}

__all__ = [
    "LIVE_FORWARD_SHADOW_MODE",
    "P0_2M_SHADOW_EXPORT_SCHEMA_VERSION",
    "P0_2M_SHADOW_EXPORT_ATTESTATION_SCHEMA_VERSION",
    "P0_2M_SHADOW_EXPORT_ATTESTATION_AUTHORITY",
    "P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_SCHEMA_VERSION",
    "P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_AUTHORITY",
    "P02mShadowConflictError",
    "P02mShadowObservationInput",
    "P02mShadowAppendResult",
    "append_p0_2m_shadow_observations",
    "attest_p0_2m_activation_artifact",
    "compute_p0_2m_activation_artifact_sha256",
    "compute_p0_2m_live_shadow_export_sha256",
    "ensure_p0_2m_live_shadow_enrollment",
    "start_new_p0_2m_live_shadow_run",
    "export_p0_2m_live_shadow_rows",
    "list_p0_2m_shadow_observations",
    "prospective_p0_2m_shadow_sessions",
    "validate_p0_2m_shadow_enrollment_boundary",
    "validate_p0_2m_activation_artifact_attestation",
    "validate_p0_2m_live_shadow_export_attestation",
]


class P02mShadowConflictError(ValueError):
    """Raised when an endpoint already exists with different immutable facts."""


@dataclass(frozen=True)
class P02mShadowObservationInput:
    symbol: str
    et_date: str
    window_end: datetime
    universe_hash: str
    signal_version: str
    observed_at: datetime
    data_available: bool
    evaluation_available: bool
    evaluation_state: str
    qualified: bool
    would_notify: bool
    notification_decision_available: bool
    max_component_end: Optional[datetime] = None
    duplicate_episode_suppressed: bool = False
    record_kind: str = "endpoint_observation"
    notification_attempt_id: Optional[str] = None
    direction: Optional[str] = None
    source: Optional[str] = None
    unavailable_reason: Optional[str] = None
    data_quality: Mapping[str, Any] = field(default_factory=dict)
    observer_queue_accepted: Optional[bool] = None
    observer_queue_enqueued_at: Optional[datetime] = None
    telegram_enqueue_evidence_mode: Optional[str] = None
    telegram_enqueued_at: Optional[datetime] = None
    enrollment_key: Optional[str] = None
    window_close: Optional[float] = None


@dataclass(frozen=True)
class P02mShadowAppendResult:
    inserted_count: int
    duplicate_count: int


@dataclass(frozen=True)
class _StoredObservation:
    enrollment_key: str
    record_kind: str
    symbol: str
    et_date: str
    window_end: datetime
    window_close: Optional[float]
    data_quality: Mapping[str, Any]
    source: Optional[str]
    universe_hash: str
    signal_version: str
    observed_at: datetime
    max_component_end: Optional[datetime]
    data_available: bool
    evaluation_available: bool
    evaluation_state: str
    qualified: bool
    would_notify: bool
    notification_decision_available: bool
    duplicate_episode_suppressed: bool
    direction: Optional[str]
    unfinished_bar_trigger: bool
    observer_queue_accepted: Optional[bool]
    observer_queue_enqueued_at: Optional[datetime]
    observer_queue_latency_seconds: Optional[float]
    telegram_enqueue_evidence_mode: Optional[str]
    telegram_enqueued_at: Optional[datetime]
    telegram_enqueue_latency_seconds: Optional[float]
    observation_key: str
    notification_attempt_id: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class P02mShadowEnrollmentRecord:
    universe_hash: str
    signal_version: str
    universe: tuple[str, ...]
    sessions: tuple[str, ...]
    enrolled_at: datetime
    created_at: datetime
    enrollment_key: Optional[str] = None
    run_request_id: Optional[str] = None
    restart_reason: Optional[str] = None
    supersedes_enrollment_key: Optional[str] = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    # SQLite drops the offset for DateTime columns.  Every repository write is
    # normalised to UTC, so a naive value read back from SQLite is UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, datetime):
            return _aware_utc(item, field_name="datetime").isoformat()
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, Decimal):
            return format(item, "f")
        raise TypeError(type(item).__name__)

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=default,
    )


def _migrate_shadow_run_identity_schema(engine: Any) -> None:
    """Retain the v1 tables and copy their rows into the run-aware schema.

    SQLite cannot replace the two old UNIQUE constraints in place.  Renaming
    the original tables preserves the exact failed run and every observation;
    the current tables are then recreated and populated by id.  The retained
    v1 tables keep UPDATE/DELETE deny guards, so the migration does not turn
    old evidence into mutable backup data and is safe to resume.
    """

    from src.journal.ledger.repository import (
        ledger_guard_trigger_ddl,
        ledger_guard_trigger_name,
    )

    def _table_exists(cursor: Any, table_name: str) -> bool:
        return cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone() is not None

    def _columns(cursor: Any, table_name: str) -> set[str]:
        if not _table_exists(cursor, table_name):
            return set()
        return {
            str(row[1])
            for row in cursor.execute(f"PRAGMA table_info({table_name})")
        }

    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        # SQLite DDL is otherwise autocommitted statement by statement on the
        # raw driver connection.  Hold the guard drops and both table renames
        # in one write transaction so a lock/error cannot expose mutable v1
        # evidence or a half-renamed schema.
        cursor.execute("BEGIN IMMEDIATE")
        enrollment_columns = _columns(cursor, _ENROLLMENT_TABLE)
        observation_columns = _columns(cursor, _OBSERVATION_TABLE)
        enrollment_is_v1 = bool(
            enrollment_columns and "run_request_id" not in enrollment_columns
        )
        observation_is_v1 = bool(
            observation_columns and "enrollment_key" not in observation_columns
        )

        for current, legacy, should_rename, indexes in (
            (
                _ENROLLMENT_TABLE,
                _LEGACY_ENROLLMENT_TABLE,
                enrollment_is_v1,
                ("ix_jv2_p0_2m_shadow_enrollment_identity",),
            ),
            (
                _OBSERVATION_TABLE,
                _LEGACY_OBSERVATION_TABLE,
                observation_is_v1,
                (
                    "ix_jv2_p0_2m_shadow_day_symbol",
                    "ix_jv2_p0_2m_shadow_identity",
                ),
            ),
        ):
            if not should_rename:
                continue
            if _table_exists(cursor, legacy):
                raise RuntimeError(
                    f"shadow run-identity migration has both v1 tables: {current}"
                )
            for operation in ("UPDATE", "DELETE"):
                cursor.execute(
                    "DROP TRIGGER IF EXISTS "
                    + ledger_guard_trigger_name(current, operation)
                )
            cursor.execute(f"ALTER TABLE {current} RENAME TO {legacy}")
            for index_name in indexes:
                cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
            for operation in ("UPDATE", "DELETE"):
                cursor.execute(ledger_guard_trigger_ddl(legacy, operation))
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()

    # This is also the resume point when a prior process stopped immediately
    # after the rename.
    Base.metadata.create_all(
        engine,
        tables=[
            P02mShadowEnrollment.__table__,
            P02mShadowObservation.__table__,
        ],
        checkfirst=True,
    )

    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        # Copying both evidence tables and installing every deny guard is one
        # transaction.  A crash before commit leaves the retained v1 tables
        # authoritative and the next call safely resumes by original row id.
        cursor.execute("BEGIN IMMEDIATE")
        copied_pairs: list[tuple[str, str, list[str]]] = []
        if _table_exists(cursor, _LEGACY_ENROLLMENT_TABLE):
            available_columns = _columns(cursor, _LEGACY_ENROLLMENT_TABLE)
            legacy_columns = [
                column.name
                for column in P02mShadowEnrollment.__table__.columns
                if column.name in available_columns and column.name
                not in {
                    "run_request_id",
                    "restart_reason",
                    "supersedes_enrollment_key",
                }
            ]
            joined = ", ".join(legacy_columns)
            cursor.execute(
                f"INSERT INTO {_ENROLLMENT_TABLE} "
                f"({joined}, run_request_id, restart_reason, "
                "supersedes_enrollment_key) "
                f"SELECT {joined}, enrollment_key, "
                "'legacy_schema_migration', NULL "
                f"FROM {_LEGACY_ENROLLMENT_TABLE} "
                f"WHERE id NOT IN (SELECT id FROM {_ENROLLMENT_TABLE})"
            )
            copied_pairs.append(
                (
                    _LEGACY_ENROLLMENT_TABLE,
                    _ENROLLMENT_TABLE,
                    legacy_columns,
                )
            )

        if _table_exists(cursor, _LEGACY_OBSERVATION_TABLE):
            orphan_count = int(
                cursor.execute(
                    f"SELECT COUNT(*) FROM {_LEGACY_OBSERVATION_TABLE} o "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {_ENROLLMENT_TABLE} e "
                    "WHERE e.universe_hash = o.universe_hash "
                    "AND e.signal_version = o.signal_version "
                    "AND e.restart_reason = 'legacy_schema_migration')"
                ).fetchone()[0]
            )
            if orphan_count:
                raise RuntimeError(
                    "legacy shadow observations have no matching enrollment"
                )
            available_columns = _columns(cursor, _LEGACY_OBSERVATION_TABLE)
            legacy_columns = [
                column.name
                for column in P02mShadowObservation.__table__.columns
                if column.name in available_columns and column.name != "enrollment_key"
            ]
            joined = ", ".join(legacy_columns)
            cursor.execute(
                f"INSERT INTO {_OBSERVATION_TABLE} ({joined}, enrollment_key) "
                f"SELECT {joined}, (SELECT e.enrollment_key "
                f"FROM {_ENROLLMENT_TABLE} e "
                "WHERE e.universe_hash = o.universe_hash "
                "AND e.signal_version = o.signal_version "
                "AND e.restart_reason = 'legacy_schema_migration' "
                "ORDER BY e.enrolled_at, e.id LIMIT 1) "
                f"FROM {_LEGACY_OBSERVATION_TABLE} o "
                f"WHERE o.id NOT IN (SELECT id FROM {_OBSERVATION_TABLE})"
            )
            copied_pairs.append(
                (
                    _LEGACY_OBSERVATION_TABLE,
                    _OBSERVATION_TABLE,
                    legacy_columns,
                )
            )

        # Do not declare the engine ready unless every retained v1 id and all
        # of its original columns exist verbatim in the run-aware table.  This
        # catches an interrupted copy or id collision while the transaction
        # can still roll back; additional rows from later runs are allowed.
        for legacy, current, columns in copied_pairs:
            joined = ", ".join(columns)
            legacy_count = int(
                cursor.execute(f"SELECT COUNT(*) FROM {legacy}").fetchone()[0]
            )
            matched_id_count = int(
                cursor.execute(
                    f"SELECT COUNT(*) FROM {legacy} l JOIN {current} c "
                    "ON c.id = l.id"
                ).fetchone()[0]
            )
            missing_content_count = int(
                cursor.execute(
                    "SELECT COUNT(*) FROM ("
                    f"SELECT {joined} FROM {legacy} EXCEPT "
                    f"SELECT {joined} FROM {current})"
                ).fetchone()[0]
            )
            new_identity_mismatch_count = 0
            if current == _ENROLLMENT_TABLE:
                new_identity_mismatch_count = int(
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {legacy} l JOIN {current} c "
                        "ON c.id = l.id WHERE "
                        "c.run_request_id != l.enrollment_key OR "
                        "c.restart_reason != 'legacy_schema_migration' OR "
                        "c.supersedes_enrollment_key IS NOT NULL"
                    ).fetchone()[0]
                )
            elif current == _OBSERVATION_TABLE:
                new_identity_mismatch_count = int(
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {legacy} l JOIN {current} c "
                        "ON c.id = l.id WHERE c.enrollment_key != ("
                        f"SELECT e.enrollment_key FROM {_ENROLLMENT_TABLE} e "
                        "WHERE e.universe_hash = l.universe_hash "
                        "AND e.signal_version = l.signal_version "
                        "AND e.restart_reason = 'legacy_schema_migration' "
                        "ORDER BY e.enrolled_at, e.id LIMIT 1)"
                    ).fetchone()[0]
                )
            if (
                matched_id_count != legacy_count
                or missing_content_count != 0
                or new_identity_mismatch_count != 0
            ):
                raise RuntimeError(
                    f"shadow run-identity migration copy mismatch: {legacy}"
                )

        for table_name in (
            _LEGACY_ENROLLMENT_TABLE,
            _LEGACY_OBSERVATION_TABLE,
            _ENROLLMENT_TABLE,
            _OBSERVATION_TABLE,
        ):
            if not _table_exists(cursor, table_name):
                continue
            for operation in ("UPDATE", "DELETE"):
                cursor.execute(ledger_guard_trigger_ddl(table_name, operation))
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _migrate_shadow_outcome_schema(engine: Any) -> None:
    """Add nullable prices and remove the obsolete date-window uniqueness.

    The enrollment rebuild is atomic and preserves every original column and
    row.  Its staging table exists only inside the migration transaction; no
    new business table or mutable audit copy survives the commit.
    """

    from src.journal.ledger.repository import ledger_guard_trigger_ddl

    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        observation_columns = {
            str(row[1])
            for row in cursor.execute(f"PRAGMA table_info({_OBSERVATION_TABLE})")
        }
        if "window_close" not in observation_columns:
            cursor.execute(
                f"ALTER TABLE {_OBSERVATION_TABLE} "
                "ADD COLUMN window_close NUMERIC(24, 10)"
            )
        indexes = list(cursor.execute(f"PRAGMA index_list({_ENROLLMENT_TABLE})"))
        window_unique = any(
            bool(row[2])
            and tuple(
                str(column[2])
                for column in cursor.execute(f'PRAGMA index_info("{row[1]}")')
            ) == ("universe_hash", "signal_version", "start_session", "end_session")
            for row in indexes
        )
        if window_unique:
            staging_name = f"{_ENROLLMENT_TABLE}_migration"
            staging = P02mShadowEnrollment.__table__.to_metadata(
                MetaData(), name=staging_name
            )
            cursor.execute(str(CreateTable(staging).compile(dialect=engine.dialect)))
            columns = [
                str(row[1])
                for row in cursor.execute(f"PRAGMA table_info({_ENROLLMENT_TABLE})")
            ]
            expected_columns = {column.name for column in staging.columns}
            if not set(columns).issubset(expected_columns):
                raise RuntimeError("shadow enrollment migration has unknown columns")
            joined = ", ".join(columns)
            cursor.execute(
                f"INSERT INTO {staging_name} ({joined}) "
                f"SELECT {joined} FROM {_ENROLLMENT_TABLE}"
            )
            mismatch = cursor.execute(
                "SELECT COUNT(*) FROM ("
                f"SELECT {joined} FROM {_ENROLLMENT_TABLE} EXCEPT "
                f"SELECT {joined} FROM {staging_name})"
            ).fetchone()[0]
            if mismatch:
                raise RuntimeError("shadow enrollment migration copy mismatch")
            cursor.execute(f"DROP TABLE {_ENROLLMENT_TABLE}")
            cursor.execute(f"ALTER TABLE {staging_name} RENAME TO {_ENROLLMENT_TABLE}")
            for index in P02mShadowEnrollment.__table__.indexes:
                cursor.execute(str(CreateIndex(index).compile(dialect=engine.dialect)))
        for table_name in (_ENROLLMENT_TABLE, _OBSERVATION_TABLE):
            for operation in ("UPDATE", "DELETE"):
                cursor.execute(ledger_guard_trigger_ddl(table_name, operation))
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _ensure_schema() -> Any:
    db = get_db()
    if db._engine.dialect.name != "sqlite":
        raise RuntimeError("P0 2m shadow telemetry requires SQLite")
    identity = id(db._engine)
    with _SCHEMA_LOCK:
        if identity not in _READY_ENGINES:
            _migrate_shadow_run_identity_schema(db._engine)
            _migrate_shadow_outcome_schema(db._engine)
            Base.metadata.create_all(
                db._engine,
                tables=[
                    P02mActivationArtifactAttestation.__table__,
                    P02mShadowEnrollment.__table__,
                    P02mShadowExportAttestation.__table__,
                    P02mShadowObservation.__table__,
                ],
                checkfirst=True,
            )
            _READY_ENGINES.add(identity)
    return db


def _enrollment_record(row: P02mShadowEnrollment) -> P02mShadowEnrollmentRecord:
    try:
        universe = tuple(str(value) for value in json.loads(row.universe_json))
        sessions = tuple(str(value) for value in json.loads(row.sessions_json))
    except (TypeError, ValueError) as exc:
        raise ValueError("stored live-shadow enrollment JSON is invalid") from exc
    return P02mShadowEnrollmentRecord(
        universe_hash=str(row.universe_hash),
        signal_version=str(row.signal_version),
        universe=universe,
        sessions=sessions,
        enrolled_at=_stored_utc(row.enrolled_at),
        created_at=_stored_utc(row.created_at),
        enrollment_key=str(row.enrollment_key),
        run_request_id=str(row.run_request_id),
        restart_reason=str(row.restart_reason),
        supersedes_enrollment_key=(
            str(row.supersedes_enrollment_key)
            if row.supersedes_enrollment_key is not None
            else None
        ),
    )


def _load_enrollment(
    session: Any,
    *,
    universe_hash: str,
    signal_version: str,
    enrollment_key: Optional[str] = None,
    run_request_id: Optional[str] = None,
    sessions: Optional[Sequence[str]] = None,
) -> Optional[P02mShadowEnrollmentRecord]:
    statement = select(P02mShadowEnrollment).where(
        (P02mShadowEnrollment.universe_hash == universe_hash)
        & (P02mShadowEnrollment.signal_version == signal_version)
    )
    if enrollment_key is not None:
        statement = statement.where(
            P02mShadowEnrollment.enrollment_key == str(enrollment_key)
        )
    if run_request_id is not None:
        statement = statement.where(
            P02mShadowEnrollment.run_request_id == str(run_request_id)
        )
    rows = session.execute(
        statement.order_by(
            P02mShadowEnrollment.enrolled_at.desc(),
            P02mShadowEnrollment.id.desc(),
        )
    ).scalars().all()
    expected_sessions = tuple(str(value) for value in sessions or ())
    for row in rows:
        record = _enrollment_record(row)
        if expected_sessions and record.sessions != expected_sessions:
            continue
        return record
    return None


def _normalise_enrollment_request(
    *,
    symbols: Sequence[str],
    universe_hash: str,
    signal_version: str,
    enrollment_started_at: datetime,
) -> tuple[datetime, datetime, tuple[str, ...], str]:
    enrolled_at = _aware_utc(
        enrollment_started_at, field_name="enrollment_started_at"
    )
    persisted_at = _utc_now()
    if abs((persisted_at - enrolled_at).total_seconds()) > _MAX_LIVE_WRITE_SKEW_SECONDS:
        raise ValueError("enrollment_started_at is not a live-forward write")
    tickers = tuple(str(value or "").strip().upper() for value in symbols)
    if (
        len(tickers) != _EXPECTED_UNIVERSE_SIZE
        or len(set(tickers)) != len(tickers)
        or any(not ticker for ticker in tickers)
    ):
        raise ValueError("live forward shadow enrollment requires 69 unique symbols")
    from src.opportunities.p0_2m_calibration import compute_universe_hash

    expected_hash = compute_universe_hash(tickers)
    if str(universe_hash) != expected_hash:
        raise ValueError("enrollment universe_hash mismatch")
    if str(signal_version) != P0_2M_SIGNAL_VERSION:
        raise ValueError("enrollment signal_version mismatch")
    return enrolled_at, persisted_at, tickers, expected_hash


def prospective_p0_2m_shadow_sessions(enrolled_at: datetime) -> tuple[str, ...]:
    """Derive the only legal 20-session block from an enrollment timestamp."""

    enrolled_at = _aware_utc(enrolled_at, field_name="enrollment_started_at")
    calendar = xcals.get_calendar("XNYS")
    observed_et = enrolled_at.astimezone(_ET)
    day = observed_et.date()
    if bool(calendar.is_session(day)):
        opened = calendar.session_open(day).to_pydatetime().astimezone(_ET)
        closed = calendar.session_close(day).to_pydatetime().astimezone(_ET)
        if opened <= observed_et <= closed:
            raise ValueError(
                "live forward shadow enrollment waits for the current "
                "XNYS session to close"
            )
        start_label = (
            calendar.date_to_session(day, direction="none")
            if observed_et < opened
            else calendar.next_session(day)
        )
    else:
        start_label = calendar.date_to_session(day, direction="next")
    labels = calendar.sessions_window(start_label, _EXPECTED_SHADOW_SESSIONS)
    sessions = tuple(stamp.date().isoformat() for stamp in labels)
    if len(sessions) != _EXPECTED_SHADOW_SESSIONS:
        raise ValueError("XNYS calendar did not return exactly 20 sessions")
    return sessions


def validate_p0_2m_shadow_enrollment_boundary(
    *,
    enrolled_at: datetime,
    created_at: datetime,
    sessions: Sequence[str],
) -> tuple[str, ...]:
    """Validate timestamps and the exact prospective block from one source."""

    enrolled = _aware_utc(enrolled_at, field_name="enrolled_at")
    created = _aware_utc(created_at, field_name="created_at")
    # Resolve the market boundary first so an RTH enrollment is never masked
    # by a secondary timestamp inconsistency in a forged payload.
    expected = prospective_p0_2m_shadow_sessions(enrolled)
    if created < enrolled:
        raise ValueError("live shadow enrollment created_at precedes enrolled_at")
    if (created - enrolled).total_seconds() > _MAX_LIVE_WRITE_SKEW_SECONDS:
        raise ValueError("live shadow enrollment persistence exceeds live-write skew")
    declared = tuple(str(value) for value in sessions)
    if declared != expected:
        raise ValueError(
            "live shadow sessions do not match the enrollment timestamp boundary"
        )
    calendar = xcals.get_calendar("XNYS")
    first_open = calendar.session_open(date.fromisoformat(expected[0])).to_pydatetime()
    if created > first_open:
        raise ValueError("live shadow enrollment created_at follows first open")
    return expected


def _freeze_enrollment(
    *,
    symbols: Sequence[str],
    universe_hash: str,
    signal_version: str,
    enrollment_started_at: datetime,
    run_request_id: Optional[str],
    restart_reason: str,
    force_new_run: bool,
) -> P02mShadowEnrollmentRecord:
    enrolled_at, persisted_at, tickers, expected_hash = (
        _normalise_enrollment_request(
            symbols=symbols,
            universe_hash=universe_hash,
            signal_version=signal_version,
            enrollment_started_at=enrollment_started_at,
        )
    )
    request_id = str(run_request_id or "initial").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", request_id):
        raise ValueError("run_request_id must be 1-64 stable identifier characters")
    reason = str(restart_reason or "").strip()
    if not reason or len(reason) > 160:
        raise ValueError("restart_reason must contain 1-160 characters")

    db = _ensure_schema()
    with db.session_scope() as session:
        # Acquire the SQLite writer lock before reading latest.  Two distinct
        # operator requests must form one supersedes chain, never siblings.
        session.execute(text("BEGIN IMMEDIATE"))
        latest = _load_enrollment(
            session,
            universe_hash=expected_hash,
            signal_version=signal_version,
        )
        exact_request = _load_enrollment(
            session,
            universe_hash=expected_hash,
            signal_version=signal_version,
            run_request_id=request_id,
        )
        selected = latest if not force_new_run else exact_request
        if selected is not None:
            if selected.universe != tickers:
                raise P02mShadowConflictError(
                    "stored enrollment universe conflicts with current universe"
                )
            if force_new_run and selected.restart_reason != reason:
                raise P02mShadowConflictError("run_request_id has a different restart_reason")
            return selected

        # Only a new request reaches this check.  Existing requests may be
        # retried during RTH, but a new run may never use a partial session.
        # Re-read the clock after acquiring the writer lock: a request queued
        # before the open is not permission to enroll after the open.
        persisted_at = _utc_now()
        sessions = prospective_p0_2m_shadow_sessions(enrolled_at)
        validate_p0_2m_shadow_enrollment_boundary(
            enrolled_at=enrolled_at,
            created_at=persisted_at,
            sessions=sessions,
        )
        if latest is not None and latest.sessions == sessions:
            first_open = xcals.get_calendar("XNYS").session_open(
                date.fromisoformat(latest.sessions[0])
            ).to_pydatetime()
            observed = session.execute(
                select(P02mShadowObservation.id)
                .where(P02mShadowObservation.enrollment_key == latest.enrollment_key)
                .limit(1)
            ).first()
            if not force_new_run or persisted_at >= first_open or observed is not None:
                raise P02mShadowConflictError(
                    "same-window replacement requires explicit start_new, "
                    "before first open and zero observations"
                )
        if latest is not None and enrolled_at < latest.enrolled_at:
            raise P02mShadowConflictError("new run timestamp precedes the latest enrollment")
        supersedes = latest.enrollment_key if latest is not None else None
        enrollment_payload = {
            "observation_mode": LIVE_FORWARD_SHADOW_MODE,
            "universe_hash": expected_hash,
            "signal_version": signal_version,
            "universe": list(tickers),
            "sessions": list(sessions),
            "enrolled_at": enrolled_at.isoformat(),
            "run_request_id": request_id,
            "restart_reason": reason,
            "supersedes_enrollment_key": supersedes,
        }
        key = hashlib.sha256(_canonical_json(enrollment_payload).encode()).hexdigest()
        result = session.execute(
            sqlite_insert(P02mShadowEnrollment)
            .values(
                enrollment_key=key,
                run_request_id=request_id,
                restart_reason=reason,
                supersedes_enrollment_key=supersedes,
                observation_mode=LIVE_FORWARD_SHADOW_MODE,
                universe_hash=expected_hash,
                signal_version=signal_version,
                universe_json=_canonical_json(list(tickers)),
                sessions_json=_canonical_json(list(sessions)),
                start_session=sessions[0],
                end_session=sessions[-1],
                enrolled_at=enrolled_at,
                created_at=persisted_at,
            )
            .on_conflict_do_nothing()
        )
        if int(result.rowcount or 0) != 1:
            winner = _load_enrollment(
                session,
                universe_hash=expected_hash,
                signal_version=signal_version,
                run_request_id=request_id,
            )
            if winner is None or winner.universe != tickers:
                raise P02mShadowConflictError("concurrent enrollment conflict")
            return winner
    return P02mShadowEnrollmentRecord(
        universe_hash=expected_hash,
        signal_version=signal_version,
        universe=tickers,
        sessions=sessions,
        enrolled_at=enrolled_at,
        created_at=persisted_at,
        enrollment_key=key,
        run_request_id=request_id,
        restart_reason=reason,
        supersedes_enrollment_key=supersedes,
    )


def ensure_p0_2m_live_shadow_enrollment(
    *,
    symbols: Sequence[str],
    universe_hash: str,
    signal_version: str = P0_2M_SIGNAL_VERSION,
    enrollment_started_at: datetime,
) -> P02mShadowEnrollmentRecord:
    """Freeze the next complete 20-session prospective observation window.

    Enrollment at or before the XNYS open may include that day.  Enrollment
    during RTH is rejected and retried after the close: otherwise the current
    day could be neither a completed holdout day nor a complete prospective
    shadow day, making the frozen run impossible to validate.
    """

    return _freeze_enrollment(
        symbols=symbols,
        universe_hash=universe_hash,
        signal_version=signal_version,
        enrollment_started_at=enrollment_started_at,
        run_request_id=None,
        restart_reason="initial_enrollment",
        force_new_run=False,
    )


def start_new_p0_2m_live_shadow_run(
    *,
    symbols: Sequence[str],
    universe_hash: str,
    restart_request_id: str,
    restart_reason: str,
    signal_version: str = P0_2M_SIGNAL_VERSION,
    enrollment_started_at: datetime,
) -> P02mShadowEnrollmentRecord:
    """Explicitly freeze a new run without mutating the failed predecessor.

    ``restart_request_id`` is a stable operator/incident idempotency key.  A
    first call during RTH is rejected, while retrying an already-created key is
    safe.  The default monitor enroller subsequently resolves the newest run.
    """

    return _freeze_enrollment(
        symbols=symbols,
        universe_hash=universe_hash,
        signal_version=signal_version,
        enrollment_started_at=enrollment_started_at,
        run_request_id=restart_request_id,
        restart_reason=restart_reason,
        force_new_run=True,
    )


def _normalise_input(item: P02mShadowObservationInput) -> dict[str, Any]:
    symbol = str(item.symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    et_date = str(item.et_date or "").strip()
    if not _ISO_DATE.fullmatch(et_date):
        raise ValueError("et_date must be YYYY-MM-DD")
    window_end = _aware_utc(item.window_end, field_name="window_end")
    observed_at = _aware_utc(item.observed_at, field_name="observed_at")
    persisted_at = _utc_now()
    if abs((persisted_at - observed_at).total_seconds()) > _MAX_LIVE_WRITE_SKEW_SECONDS:
        raise ValueError("observed_at is not a live-forward write")
    if abs((observed_at - window_end).total_seconds()) > _MAX_ENDPOINT_LIVE_LAG_SECONDS:
        raise ValueError("observation is too far from its live 2m endpoint")
    max_component_end = (
        _aware_utc(item.max_component_end, field_name="max_component_end")
        if item.max_component_end is not None
        else None
    )
    if window_end.astimezone(_ET).date().isoformat() != et_date:
        raise ValueError("window_end ET date does not match et_date")
    if window_end.second or window_end.microsecond:
        raise ValueError("window_end must be minute-aligned")
    universe_hash = str(item.universe_hash or "").strip()
    signal_version = str(item.signal_version or "").strip()
    state = str(item.evaluation_state or "").strip()
    if not universe_hash or not signal_version or not state:
        raise ValueError("universe_hash, signal_version and evaluation_state are required")
    record_kind = str(item.record_kind or "").strip()
    if record_kind not in {
        "endpoint_observation",
        "completed_evaluation",
        "notification_attempt",
    }:
        raise ValueError(f"invalid shadow record_kind: {record_kind!r}")
    direction = str(item.direction or "").strip().lower() or None
    if direction not in {None, "up", "down", "flat"}:
        raise ValueError(f"invalid direction: {direction!r}")
    observer_enqueued = (
        _aware_utc(
            item.observer_queue_enqueued_at,
            field_name="observer_queue_enqueued_at",
        )
        if item.observer_queue_enqueued_at is not None
        else None
    )
    enqueued = (
        _aware_utc(item.telegram_enqueued_at, field_name="telegram_enqueued_at")
        if item.telegram_enqueued_at is not None
        else None
    )
    enqueue_mode = str(item.telegram_enqueue_evidence_mode or "").strip() or None
    if enqueue_mode not in _VALID_ENQUEUE_EVIDENCE_MODES | {None}:
        raise ValueError(f"invalid telegram enqueue evidence mode: {enqueue_mode!r}")
    attempt_id = str(item.notification_attempt_id or "").strip() or None
    if record_kind == "notification_attempt" and (
        not item.would_notify
        or enqueue_mode is None
        or attempt_id is None
        or direction not in {"up", "down"}
    ):
        raise ValueError(
            "notification_attempt requires direction, would_notify, attempt id "
            "and evidence mode"
        )
    if record_kind != "notification_attempt" and attempt_id is not None:
        raise ValueError("notification_attempt_id is only valid on attempt rows")
    if record_kind != "notification_attempt" and enqueue_mode is not None:
        raise ValueError("enqueue evidence mode is only valid on attempt rows")
    if item.observer_queue_accepted is True and record_kind == "notification_attempt":
        if observer_enqueued is None or enqueued is None:
            raise ValueError("accepted notification attempt requires enqueue timestamps")
    if item.observer_queue_accepted is not True and (
        observer_enqueued is not None or enqueued is not None
    ):
        raise ValueError("unaccepted notification attempt cannot claim enqueue time")
    observer_latency = (
        (observer_enqueued - window_end).total_seconds()
        if observer_enqueued is not None
        else None
    )
    latency = (enqueued - window_end).total_seconds() if enqueued else None
    quality = dict(item.data_quality or {})
    window_close = None
    if item.window_close is not None:
        try:
            window_close = float(item.window_close)
        except (TypeError, ValueError) as exc:
            raise ValueError("window_close must be a finite positive price") from exc
        basis = quality.get("window_close_basis")
        if not math.isfinite(window_close) or window_close <= 0:
            raise ValueError("window_close must be a finite positive price")
        if (
            record_kind != "completed_evaluation"
            or not item.data_available
            or not item.evaluation_available
            or max_component_end != window_end
            or observed_at < window_end
        ):
            raise ValueError("window_close requires a complete final evaluation")
        if (
            not isinstance(basis, Mapping)
            or basis.get("source") != item.source
            or not item.source
            or basis.get("price") != "completed_exact_2m_close"
            or basis.get("session_scope") != "regular"
            or not basis.get("adjustment")
        ):
            raise ValueError("window_close requires an explicit source/price basis")
    quality_json = _canonical_json(quality)
    requested_enrollment_key = str(item.enrollment_key or "").strip() or None
    if requested_enrollment_key is not None and not re.fullmatch(
        r"[0-9a-f]{64}", requested_enrollment_key
    ):
        raise ValueError("enrollment_key must be a 64-character SHA-256 digest")
    identity = {
        "observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "symbol": symbol,
        "et_date": et_date,
        "window_end": window_end.isoformat(),
        "universe_hash": universe_hash,
        "signal_version": signal_version,
        "record_kind": record_kind,
        "notification_attempt_id": attempt_id or "first",
    }
    logical = {
        **identity,
        "session_scope": "regular",
        "window_close": window_close,
        "observed_at": observed_at.isoformat(),
        "max_component_end": (
            max_component_end.isoformat() if max_component_end else None
        ),
        "data_available": bool(item.data_available),
        "evaluation_available": bool(item.evaluation_available),
        "evaluation_state": state,
        "qualified": bool(item.qualified),
        "would_notify": bool(item.would_notify),
        "notification_decision_available": bool(
            item.notification_decision_available
        ),
        "duplicate_episode_suppressed": bool(
            item.duplicate_episode_suppressed
        ),
        "direction": direction,
        "source": str(item.source or "").strip() or None,
        "unavailable_reason": str(item.unavailable_reason or "").strip() or None,
        "data_quality_json": quality_json,
        "unfinished_bar_trigger": bool(
            observed_at < window_end
            or (
                max_component_end is not None
                and max_component_end > observed_at
            )
            or (
                record_kind in {"completed_evaluation", "notification_attempt"}
                and (
                    max_component_end is None
                    or max_component_end != window_end
                )
            )
        ),
        "observer_queue_accepted": (
            bool(item.observer_queue_accepted)
            if item.observer_queue_accepted is not None
            else None
        ),
        "observer_queue_enqueued_at": (
            observer_enqueued.isoformat() if observer_enqueued else None
        ),
        "observer_queue_latency_seconds": observer_latency,
        "telegram_enqueue_evidence_mode": enqueue_mode,
        "telegram_enqueued_at": enqueued.isoformat() if enqueued else None,
        "telegram_enqueue_latency_seconds": latency,
    }
    return {
        **logical,
        "window_end": window_end,
        "observed_at": observed_at,
        "max_component_end": max_component_end,
        "observer_queue_enqueued_at": observer_enqueued,
        "telegram_enqueued_at": enqueued,
        "_requested_enrollment_key": requested_enrollment_key,
        "created_at": persisted_at,
    }


def _bind_observation_to_enrollment(
    values: Mapping[str, Any], enrollment: P02mShadowEnrollmentRecord
) -> dict[str, Any]:
    key = str(enrollment.enrollment_key or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("stored live-shadow enrollment key is invalid")
    row = dict(values)
    row.pop("_requested_enrollment_key", None)
    row["enrollment_key"] = key
    identity = {
        "observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "enrollment_key": key,
        "symbol": row["symbol"],
        "et_date": row["et_date"],
        "window_end": row["window_end"].isoformat(),
        "universe_hash": row["universe_hash"],
        "signal_version": row["signal_version"],
        "record_kind": row["record_kind"],
        "notification_attempt_id": row["notification_attempt_id"],
    }
    # The run-scoped identity, rather than a mutable retry payload, is the
    # idempotency key.  A retry preserves the first live fact, while a new run
    # can observe an overlapping endpoint without colliding with its failed
    # predecessor.
    row["observation_key"] = hashlib.sha256(
        _canonical_json(identity).encode()
    ).hexdigest()
    return row


def _identity_where(values: Mapping[str, Any]):
    return (
        (P02mShadowObservation.enrollment_key == values["enrollment_key"])
        & (P02mShadowObservation.symbol == values["symbol"])
        & (P02mShadowObservation.et_date == values["et_date"])
        & (P02mShadowObservation.window_end == values["window_end"])
        & (P02mShadowObservation.universe_hash == values["universe_hash"])
        & (P02mShadowObservation.signal_version == values["signal_version"])
        & (P02mShadowObservation.record_kind == values["record_kind"])
        & (
            P02mShadowObservation.notification_attempt_id
            == values["notification_attempt_id"]
        )
    )


def _compatible_observation_keys(
    values: Mapping[str, Any], enrollment: P02mShadowEnrollmentRecord
) -> set[str]:
    keys = {str(values["observation_key"])}
    if enrollment.restart_reason == "legacy_schema_migration":
        legacy_identity = {
            "observation_mode": LIVE_FORWARD_SHADOW_MODE,
            "symbol": values["symbol"],
            "et_date": values["et_date"],
            "window_end": values["window_end"].isoformat(),
            "universe_hash": values["universe_hash"],
            "signal_version": values["signal_version"],
            "record_kind": values["record_kind"],
            "notification_attempt_id": values["notification_attempt_id"],
        }
        keys.add(
            hashlib.sha256(
                _canonical_json(legacy_identity).encode()
            ).hexdigest()
        )
    return keys


def append_p0_2m_shadow_observations(
    observations: Sequence[P02mShadowObservationInput],
) -> P02mShadowAppendResult:
    """Append immutable endpoint/evaluation/attempt events idempotently.

    Endpoint and completed-evaluation stages each have one stable identity;
    every queue attempt has its own message id.  The caller treats repository
    failure as non-blocking for the signal path.
    """

    normalised = [_normalise_input(item) for item in observations]
    if not normalised:
        return P02mShadowAppendResult(0, 0)
    db = _ensure_schema()
    inserted = 0
    duplicates = 0
    with db.session_scope() as session:
        identity_pairs = {
            (str(row["universe_hash"]), str(row["signal_version"]))
            for row in normalised
        }
        requested_keys = {
            str(row["_requested_enrollment_key"])
            for row in normalised
            if row["_requested_enrollment_key"] is not None
        }
        if len(identity_pairs) != 1 or len(requested_keys) > 1:
            raise ValueError("one append batch must belong to exactly one shadow run")
        universe_hash, signal_version = next(iter(identity_pairs))
        enrollment = _load_enrollment(
            session,
            universe_hash=universe_hash,
            signal_version=signal_version,
            enrollment_key=(next(iter(requested_keys)) if requested_keys else None),
        )
        if enrollment is None:
            raise ValueError("live-forward shadow enrollment is missing")
        values = [
            _bind_observation_to_enrollment(row, enrollment)
            for row in normalised
        ]
        for row in values:
            if (
                row["universe_hash"] != enrollment.universe_hash
                or row["signal_version"] != enrollment.signal_version
                or row["symbol"] not in enrollment.universe
                or row["et_date"] not in enrollment.sessions
                or row["observed_at"] < enrollment.enrolled_at
            ):
                raise ValueError("observation is outside the frozen enrollment")
        for row in values:
            existing = session.execute(
                select(P02mShadowObservation).where(_identity_where(row))
            ).scalar_one_or_none()
            if existing is not None:
                if str(existing.observation_key) not in _compatible_observation_keys(
                    row, enrollment
                ):
                    raise P02mShadowConflictError(
                        "immutable endpoint already exists with different facts: "
                        f"{row['symbol']} {row['window_end'].isoformat()}"
                    )
                duplicates += 1
                continue
            result = session.execute(
                sqlite_insert(P02mShadowObservation)
                .values(**row)
                .on_conflict_do_nothing()
            )
            if int(result.rowcount or 0) == 1:
                inserted += 1
                continue
            # A concurrent writer may have won after the read.  Verify it is
            # the exact same immutable observation before calling it idempotent.
            winner = session.execute(
                select(P02mShadowObservation).where(_identity_where(row))
            ).scalar_one_or_none()
            if winner is None or str(
                winner.observation_key
            ) not in _compatible_observation_keys(row, enrollment):
                raise P02mShadowConflictError(
                    "concurrent immutable endpoint conflict: "
                    f"{row['symbol']} {row['window_end'].isoformat()}"
                )
            duplicates += 1
    return P02mShadowAppendResult(inserted, duplicates)


def _stored(row: P02mShadowObservation) -> _StoredObservation:
    return _StoredObservation(
        enrollment_key=str(row.enrollment_key),
        record_kind=str(row.record_kind),
        symbol=str(row.symbol),
        et_date=str(row.et_date),
        window_end=_stored_utc(row.window_end),
        window_close=float(row.window_close) if row.window_close is not None else None,
        data_quality=json.loads(row.data_quality_json),
        source=str(row.source) if row.source is not None else None,
        universe_hash=str(row.universe_hash),
        signal_version=str(row.signal_version),
        observed_at=_stored_utc(row.observed_at),
        max_component_end=(
            _stored_utc(row.max_component_end)
            if row.max_component_end is not None
            else None
        ),
        data_available=bool(row.data_available),
        evaluation_available=bool(row.evaluation_available),
        evaluation_state=str(row.evaluation_state),
        qualified=bool(row.qualified),
        would_notify=bool(row.would_notify),
        notification_decision_available=bool(
            row.notification_decision_available
        ),
        duplicate_episode_suppressed=bool(row.duplicate_episode_suppressed),
        direction=str(row.direction) if row.direction is not None else None,
        unfinished_bar_trigger=bool(row.unfinished_bar_trigger),
        observer_queue_accepted=(
            bool(row.observer_queue_accepted)
            if row.observer_queue_accepted is not None
            else None
        ),
        observer_queue_enqueued_at=(
            _stored_utc(row.observer_queue_enqueued_at)
            if row.observer_queue_enqueued_at is not None
            else None
        ),
        observer_queue_latency_seconds=(
            float(row.observer_queue_latency_seconds)
            if row.observer_queue_latency_seconds is not None
            else None
        ),
        telegram_enqueue_evidence_mode=(
            str(row.telegram_enqueue_evidence_mode)
            if row.telegram_enqueue_evidence_mode is not None
            else None
        ),
        telegram_enqueued_at=(
            _stored_utc(row.telegram_enqueued_at)
            if row.telegram_enqueued_at is not None
            else None
        ),
        telegram_enqueue_latency_seconds=(
            float(row.telegram_enqueue_latency_seconds)
            if row.telegram_enqueue_latency_seconds is not None
            else None
        ),
        observation_key=str(row.observation_key),
        notification_attempt_id=(
            str(row.notification_attempt_id)
            if row.record_kind == "notification_attempt"
            else None
        ),
        created_at=_stored_utc(row.created_at),
    )


def list_p0_2m_shadow_observations(
    *,
    et_dates: Sequence[str],
    symbols: Sequence[str],
    universe_hash: str,
    signal_version: str = P0_2M_SIGNAL_VERSION,
    enrollment_key: Optional[str] = None,
) -> list[_StoredObservation]:
    dates = tuple(dict.fromkeys(str(value) for value in et_dates))
    tickers = tuple(dict.fromkeys(str(value).strip().upper() for value in symbols))
    if not dates or not tickers:
        return []
    db = _ensure_schema()
    with db.session_scope() as session:
        effective_enrollment_key = enrollment_key
        if effective_enrollment_key is None:
            latest = _load_enrollment(
                session,
                universe_hash=str(universe_hash),
                signal_version=str(signal_version),
            )
            effective_enrollment_key = (
                latest.enrollment_key if latest is not None else None
            )
        statement = (
            select(P02mShadowObservation)
            .where(P02mShadowObservation.et_date.in_(dates))
            .where(P02mShadowObservation.symbol.in_(tickers))
            .where(P02mShadowObservation.universe_hash == str(universe_hash))
            .where(P02mShadowObservation.signal_version == str(signal_version))
        )
        if effective_enrollment_key is not None:
            statement = statement.where(
                P02mShadowObservation.enrollment_key
                == str(effective_enrollment_key)
            )
        rows = session.execute(
            statement.order_by(
                P02mShadowObservation.et_date,
                P02mShadowObservation.symbol,
                P02mShadowObservation.window_end,
            )
        ).scalars().all()
        return [_stored(row) for row in rows]


def _normalise_sessions(values: Sequence[str]) -> tuple[date, ...]:
    if len(values) != _EXPECTED_SHADOW_SESSIONS:
        raise ValueError("live forward shadow export requires exactly 20 XNYS sessions")
    try:
        result = tuple(date.fromisoformat(str(value)) for value in values)
    except ValueError as exc:
        raise ValueError("shadow session must be YYYY-MM-DD") from exc
    if len(set(result)) != len(result) or tuple(sorted(result)) != result:
        raise ValueError("shadow sessions must be unique and chronological")
    calendar = xcals.get_calendar("XNYS")
    if any(not bool(calendar.is_session(day)) for day in result):
        raise ValueError("all shadow dates must be XNYS sessions")
    expected = tuple(
        stamp.date()
        for stamp in calendar.sessions_window(
            calendar.date_to_session(result[0], direction="none"),
            _EXPECTED_SHADOW_SESSIONS,
        )
    )
    if result != expected:
        raise ValueError("shadow sessions must be 20 consecutive XNYS sessions")
    return result


def _expected_endpoints(calendar: Any, day: date) -> tuple[datetime, ...]:
    opened = calendar.session_open(day).to_pydatetime().astimezone(_ET)
    closed = calendar.session_close(day).to_pydatetime().astimezone(_ET)
    seconds = int((closed - opened).total_seconds())
    if seconds <= 0 or seconds % 120:
        raise ValueError(f"invalid XNYS 2m session duration: {day.isoformat()}")
    return tuple(
        opened + timedelta(minutes=2 * index)
        for index in range(1, seconds // 120 + 1)
    )


def compute_p0_2m_live_shadow_export_sha256(payload: Mapping[str, Any]) -> str:
    """Hash the complete export envelope, excluding only its DB receipt."""

    unsigned = dict(payload)
    unsigned.pop("export_attestation", None)
    return hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


def _record_export_attestation(
    *,
    export_sha256: str,
    enrollment: P02mShadowEnrollmentRecord,
    exported_at: datetime,
) -> dict[str, Any]:
    enrollment_key = str(enrollment.enrollment_key or "").strip()
    if not enrollment_key:
        raise ValueError("live-shadow enrollment key is missing")
    attestation_id = secrets.token_hex(32)
    created_at = _utc_now()
    db = _ensure_schema()
    with db.session_scope() as session:
        stored_enrollment = _load_enrollment(
            session,
            universe_hash=enrollment.universe_hash,
            signal_version=enrollment.signal_version,
            enrollment_key=enrollment_key,
        )
        if stored_enrollment is None or any(
            (
                stored_enrollment.universe != enrollment.universe,
                stored_enrollment.sessions != enrollment.sessions,
                stored_enrollment.enrolled_at != enrollment.enrolled_at,
                stored_enrollment.created_at != enrollment.created_at,
            )
        ):
            raise ValueError("live-shadow export enrollment is not DB-backed")
        validate_p0_2m_shadow_enrollment_boundary(
            enrolled_at=stored_enrollment.enrolled_at,
            created_at=stored_enrollment.created_at,
            sessions=stored_enrollment.sessions,
        )
        session.execute(
            sqlite_insert(P02mShadowExportAttestation).values(
                attestation_id=attestation_id,
                export_sha256=export_sha256,
                attestation_schema_version=(
                    P0_2M_SHADOW_EXPORT_ATTESTATION_SCHEMA_VERSION
                ),
                authority=P0_2M_SHADOW_EXPORT_ATTESTATION_AUTHORITY,
                observation_mode=LIVE_FORWARD_SHADOW_MODE,
                universe_hash=enrollment.universe_hash,
                signal_version=enrollment.signal_version,
                enrollment_key=enrollment_key,
                exported_at=exported_at,
                created_at=created_at,
            )
        )
    return {
        "schema_version": P0_2M_SHADOW_EXPORT_ATTESTATION_SCHEMA_VERSION,
        "authority": P0_2M_SHADOW_EXPORT_ATTESTATION_AUTHORITY,
        "attestation_id": attestation_id,
        "export_sha256": export_sha256,
        "observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "universe_hash": enrollment.universe_hash,
        "signal_version": enrollment.signal_version,
        "enrollment_key": enrollment_key,
        "exported_at": exported_at.isoformat(),
    }


def validate_p0_2m_live_shadow_export_attestation(
    value: Any,
    *,
    expected_universe_hash: str,
    expected_signal_version: str = P0_2M_SIGNAL_VERSION,
) -> tuple[bool, str]:
    """Verify that an export receipt exists in the append-only local DB."""

    if not isinstance(value, Mapping):
        return False, "live_shadow_export_attestation_missing"
    expected = {
        "schema_version": P0_2M_SHADOW_EXPORT_ATTESTATION_SCHEMA_VERSION,
        "authority": P0_2M_SHADOW_EXPORT_ATTESTATION_AUTHORITY,
        "observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "universe_hash": str(expected_universe_hash),
        "signal_version": str(expected_signal_version),
    }
    for field_name, expected_value in expected.items():
        if value.get(field_name) != expected_value:
            return False, f"live_shadow_export_attestation_{field_name}_mismatch"
    attestation_id = str(value.get("attestation_id") or "").strip()
    export_sha256 = str(value.get("export_sha256") or "").strip()
    enrollment_key = str(value.get("enrollment_key") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", attestation_id):
        return False, "live_shadow_export_attestation_id_invalid"
    if not re.fullmatch(r"[0-9a-f]{64}", export_sha256):
        return False, "live_shadow_export_sha256_invalid"
    if not re.fullmatch(r"[0-9a-f]{64}", enrollment_key):
        return False, "live_shadow_export_enrollment_key_invalid"
    try:
        exported_at = datetime.fromisoformat(
            str(value.get("exported_at") or "").replace("Z", "+00:00")
        )
        exported_at = _aware_utc(exported_at, field_name="exported_at")
    except (TypeError, ValueError):
        return False, "live_shadow_export_exported_at_invalid"
    try:
        db = _ensure_schema()
        with db.session_scope() as session:
            row = session.execute(
                select(P02mShadowExportAttestation).where(
                    P02mShadowExportAttestation.attestation_id == attestation_id
                )
            ).scalar_one_or_none()
            enrollment = (
                _load_enrollment(
                    session,
                    universe_hash=str(expected_universe_hash),
                    signal_version=str(expected_signal_version),
                    enrollment_key=enrollment_key,
                )
                if row is not None
                else None
            )
            stored = (
                {
                    "export_sha256": str(row.export_sha256),
                    "schema_version": str(row.attestation_schema_version),
                    "authority": str(row.authority),
                    "observation_mode": str(row.observation_mode),
                    "universe_hash": str(row.universe_hash),
                    "signal_version": str(row.signal_version),
                    "enrollment_key": str(row.enrollment_key),
                    "exported_at": _stored_utc(row.exported_at),
                }
                if row is not None
                else None
            )
    except Exception as exc:  # noqa: BLE001 - inability to prove must fail closed
        return False, f"live_shadow_export_attestation_store_unavailable:{type(exc).__name__}"
    if row is None:
        return False, "live_shadow_export_attestation_not_found"
    if enrollment is None:
        return False, "live_shadow_export_enrollment_not_found"
    try:
        validate_p0_2m_shadow_enrollment_boundary(
            enrolled_at=enrollment.enrolled_at,
            created_at=enrollment.created_at,
            sessions=enrollment.sessions,
        )
    except ValueError:
        return False, "live_shadow_export_enrollment_boundary_invalid"
    claimed = {
        "export_sha256": export_sha256,
        "schema_version": expected["schema_version"],
        "authority": expected["authority"],
        "observation_mode": expected["observation_mode"],
        "universe_hash": expected["universe_hash"],
        "signal_version": expected["signal_version"],
        "enrollment_key": enrollment_key,
        "exported_at": exported_at,
    }
    if stored != claimed:
        return False, "live_shadow_export_attestation_record_mismatch"
    return True, "ok"


def compute_p0_2m_activation_artifact_sha256(
    payload: Mapping[str, Any],
) -> str:
    """Recompute the artifact's canonical self-hash."""

    unsigned = dict(payload)
    unsigned.pop("artifact_sha256", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _activation_artifact_facts(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise ValueError("activation_artifact_not_object")
    artifact_sha256 = str(payload.get("artifact_sha256") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
        raise ValueError("activation_artifact_hash_invalid")
    if compute_p0_2m_activation_artifact_sha256(payload) != artifact_sha256:
        raise ValueError("activation_artifact_hash_mismatch")
    if payload.get("activation_ready") is not True:
        raise ValueError("activation_artifact_not_ready")
    gates = payload.get("gate_results")
    if not isinstance(gates, Mapping) or not gates or any(
        not isinstance(value, Mapping) or value.get("passed") is not True
        for value in gates.values()
    ):
        raise ValueError("activation_artifact_gate_not_passed")

    calibration_schema_version = str(
        payload.get("schema_version") or ""
    ).strip()
    universe_hash = str(payload.get("universe_hash") or "").strip()
    signal_version = str(payload.get("signal_version") or "").strip()
    shadow_binding_sha256 = str(
        payload.get("shadow_source_binding_sha256") or ""
    ).strip()
    provenance = payload.get("input_provenance")
    live_merge = (
        provenance.get("live_shadow_merge")
        if isinstance(provenance, Mapping)
        else None
    )
    if not isinstance(live_merge, Mapping):
        raise ValueError("activation_artifact_live_shadow_provenance_missing")
    export_attestation = live_merge.get("export_attestation")
    if not isinstance(export_attestation, Mapping):
        raise ValueError("activation_artifact_export_attestation_missing")
    source_export_attestation_id = str(
        export_attestation.get("attestation_id") or ""
    ).strip()
    source_export_sha256 = str(
        export_attestation.get("export_sha256") or ""
    ).strip()
    source_enrollment_key = str(
        export_attestation.get("enrollment_key") or ""
    ).strip()
    provenance_enrollment = live_merge.get("enrollment")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", source_enrollment_key)
        or str(live_merge.get("enrollment_key") or "").strip()
        != source_enrollment_key
        or not isinstance(provenance_enrollment, Mapping)
        or str(provenance_enrollment.get("enrollment_key") or "").strip()
        != source_enrollment_key
    ):
        raise ValueError("activation_artifact_enrollment_key_binding_mismatch")
    for field_name, value in (
        ("universe_hash", universe_hash),
        ("signal_version", signal_version),
        ("calibration_schema_version", calibration_schema_version),
    ):
        if not value:
            raise ValueError(f"activation_artifact_{field_name}_missing")
    for field_name, value in (
        ("shadow_binding_sha256", shadow_binding_sha256),
        ("source_export_attestation_id", source_export_attestation_id),
        ("source_export_sha256", source_export_sha256),
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"activation_artifact_{field_name}_invalid")
    if live_merge.get("shadow_binding_sha256") != shadow_binding_sha256:
        raise ValueError("activation_artifact_shadow_binding_mismatch")
    if live_merge.get("live_shadow_export_sha256") != source_export_sha256:
        raise ValueError("activation_artifact_export_hash_mismatch")
    valid_export, export_reason = validate_p0_2m_live_shadow_export_attestation(
        export_attestation,
        expected_universe_hash=universe_hash,
        expected_signal_version=signal_version,
    )
    if not valid_export:
        raise ValueError(f"activation_artifact_export_rejected:{export_reason}")
    return {
        "artifact_sha256": artifact_sha256,
        "calibration_schema_version": calibration_schema_version,
        "universe_hash": universe_hash,
        "signal_version": signal_version,
        "shadow_binding_sha256": shadow_binding_sha256,
        "source_export_attestation_id": source_export_attestation_id,
        "source_export_sha256": source_export_sha256,
    }


def attest_p0_2m_activation_artifact(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Append a DB receipt binding the exact activation artifact digest."""

    facts = _activation_artifact_facts(payload)
    attestation_id = secrets.token_hex(32)
    created_at = _utc_now()
    db = _ensure_schema()
    try:
        with db.session_scope() as session:
            session.execute(
                sqlite_insert(P02mActivationArtifactAttestation)
                .values(
                    attestation_id=attestation_id,
                    artifact_sha256=facts["artifact_sha256"],
                    attestation_schema_version=(
                        P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_SCHEMA_VERSION
                    ),
                    authority=P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_AUTHORITY,
                    calibration_schema_version=facts[
                        "calibration_schema_version"
                    ],
                    universe_hash=facts["universe_hash"],
                    signal_version=facts["signal_version"],
                    shadow_binding_sha256=facts["shadow_binding_sha256"],
                    source_export_attestation_id=facts[
                        "source_export_attestation_id"
                    ],
                    source_export_sha256=facts["source_export_sha256"],
                    created_at=created_at,
                )
                .on_conflict_do_nothing(index_elements=["artifact_sha256"])
            )
            row = session.execute(
                select(P02mActivationArtifactAttestation).where(
                    P02mActivationArtifactAttestation.artifact_sha256
                    == facts["artifact_sha256"]
                )
            ).scalar_one()
            stored = {
                "attestation_id": str(row.attestation_id),
                "artifact_sha256": str(row.artifact_sha256),
                "schema_version": str(row.attestation_schema_version),
                "authority": str(row.authority),
                "calibration_schema_version": str(
                    row.calibration_schema_version
                ),
                "universe_hash": str(row.universe_hash),
                "signal_version": str(row.signal_version),
                "shadow_binding_sha256": str(row.shadow_binding_sha256),
                "source_export_attestation_id": str(
                    row.source_export_attestation_id
                ),
                "source_export_sha256": str(row.source_export_sha256),
                "created_at": _stored_utc(row.created_at).isoformat(),
            }
    except Exception as exc:  # noqa: BLE001 - attestation must fail closed
        raise ValueError(
            f"activation_artifact_attestation_write_failed:{type(exc).__name__}"
        ) from exc
    expected_stored = {
        "artifact_sha256": facts["artifact_sha256"],
        "schema_version": P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_SCHEMA_VERSION,
        "authority": P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_AUTHORITY,
        "calibration_schema_version": facts["calibration_schema_version"],
        "universe_hash": facts["universe_hash"],
        "signal_version": facts["signal_version"],
        "shadow_binding_sha256": facts["shadow_binding_sha256"],
        "source_export_attestation_id": facts[
            "source_export_attestation_id"
        ],
        "source_export_sha256": facts["source_export_sha256"],
    }
    if any(stored.get(key) != value for key, value in expected_stored.items()):
        raise P02mShadowConflictError(
            "activation artifact digest already has conflicting attestation"
        )
    return stored


def validate_p0_2m_activation_artifact_attestation(
    payload: Any,
) -> tuple[bool, str]:
    """Require an exact final-artifact digest in the append-only local DB."""

    try:
        facts = _activation_artifact_facts(payload)
    except ValueError as exc:
        return False, str(exc)
    try:
        db = _ensure_schema()
        with db.session_scope() as session:
            row = session.execute(
                select(P02mActivationArtifactAttestation).where(
                    P02mActivationArtifactAttestation.artifact_sha256
                    == facts["artifact_sha256"]
                )
            ).scalar_one_or_none()
            stored = (
                {
                    "artifact_sha256": str(row.artifact_sha256),
                    "schema_version": str(row.attestation_schema_version),
                    "authority": str(row.authority),
                    "calibration_schema_version": str(
                        row.calibration_schema_version
                    ),
                    "universe_hash": str(row.universe_hash),
                    "signal_version": str(row.signal_version),
                    "shadow_binding_sha256": str(
                        row.shadow_binding_sha256
                    ),
                    "source_export_attestation_id": str(
                        row.source_export_attestation_id
                    ),
                    "source_export_sha256": str(row.source_export_sha256),
                }
                if row is not None
                else None
            )
    except Exception as exc:  # noqa: BLE001 - inability to prove fails closed
        return False, (
            "activation_artifact_attestation_store_unavailable:"
            f"{type(exc).__name__}"
        )
    if stored is None:
        return False, "activation_artifact_attestation_not_found"
    expected = {
        **facts,
        "schema_version": P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_SCHEMA_VERSION,
        "authority": P0_2M_ACTIVATION_ARTIFACT_ATTESTATION_AUTHORITY,
    }
    if stored != expected:
        return False, "activation_artifact_attestation_record_mismatch"
    return True, "ok"


def _forward_outcome_diagnostics(
    *,
    evaluations: Mapping[str, _StoredObservation],
    qualified: Mapping[str, _StoredObservation],
    session_close: datetime,
    exported_at: datetime,
    actual_notification_attempts: Optional[
        Mapping[str, Sequence[_StoredObservation]]
    ] = None,
) -> dict[str, Any]:
    """Read exact same-run/day RTH endpoints; never fetch or interpolate."""

    def event(item: _StoredObservation) -> dict[str, Any]:
        future_end = item.window_end + timedelta(minutes=20)
        future = evaluations.get(future_end.astimezone(_ET).isoformat())
        basis = item.data_quality.get("window_close_basis")
        result = {
            "window_end": item.window_end.astimezone(_ET).isoformat(),
            "direction": item.direction,
            "window_close": item.window_close,
            "future_window_end": future_end.astimezone(_ET).isoformat(),
            "future_close": future.window_close if future is not None else None,
            "window_close_basis": basis,
            "would_notify": item.would_notify,
            "notification_decision_available": item.notification_decision_available,
            "outcome_status": "missing",
            "outcome_reason": None,
            "direction_adjusted_return_20m": None,
        }
        if future_end > session_close:
            result.update(outcome_status="session_censored", outcome_reason="t_plus_20_after_rth_close")
        elif item.window_close is None:
            result["outcome_reason"] = "entry_price_missing"
        elif item.direction not in {"up", "down"}:
            result["outcome_reason"] = "entry_direction_invalid"
        elif item.window_end.astimezone(_ET).isoformat() not in evaluations:
            result["outcome_reason"] = "entry_evaluation_incomplete"
        elif future is None:
            result.update(
                outcome_status=(
                    "pending" if exported_at <= future_end + timedelta(
                        seconds=_MAX_ENDPOINT_LIVE_LAG_SECONDS
                    ) else "missing"
                ),
                outcome_reason="exact_t_plus_20_evaluation_unavailable",
            )
        elif future.window_close is None:
            result["outcome_reason"] = "future_price_missing"
        elif (
            future.enrollment_key != item.enrollment_key
            or future.symbol != item.symbol
            or future.et_date != item.et_date
            or future.source != item.source
            or not isinstance(basis, Mapping)
            or future.data_quality.get("window_close_basis") != basis
        ):
            result["outcome_reason"] = "price_basis_mismatch"
        else:
            direction_sign = 1.0 if item.direction == "up" else -1.0
            result.update(
                outcome_status="matured",
                outcome_reason=None,
                direction_adjusted_return_20m=direction_sign * (
                    future.window_close / item.window_close - 1.0
                ),
            )
        return result

    events: list[dict[str, Any]] = []
    would_notify_events: list[dict[str, Any]] = []
    notification_events: list[dict[str, Any]] = []
    anchors: dict[Optional[str], datetime] = {}
    for item in sorted(qualified.values(), key=lambda row: row.window_end):
        rendered = event(item)
        # Counterfactual intent and real queue receipts are different facts.
        # Neither defines the full pre-budget signal population.
        if item.would_notify:
            would_notify_events.append(rendered)
        actual_attempts = (actual_notification_attempts or {}).get(
            item.window_end.astimezone(_ET).isoformat(), ()
        )
        if actual_attempts:
            notification_events.append({
                **rendered,
                "notification_evidence_mode": "actual_telegram_queue_receipt",
                "notification_attempt_ids": sorted(
                    attempt.notification_attempt_id for attempt in actual_attempts
                    if attempt.notification_attempt_id is not None
                ),
                "phone_delivery_verified": False,
            })
        anchor = anchors.get(item.direction)
        if anchor is not None and (item.window_end - anchor).total_seconds() < 1800:
            continue
        anchors[item.direction] = item.window_end
        events.append(rendered)

    statuses = ("matured", "session_censored", "missing", "pending")
    return {
        "schema_version": "p0_2m_shadow_outcomes_v1",
        "activation_evidence": False,
        "population": "all_qualified_before_budget_direction_30m_anchored",
        "notification_population": "actual_telegram_queue_receipt_accepted_not_phone_delivery",
        "return_basis": "direction_times_exact_close_ratio_minus_one",
        "qualified_endpoint_count": len(qualified),
        "deduplicated_qualified_count": len(qualified) - len(events),
        "event_count": len(events),
        "notification_event_count": len(notification_events),
        "would_notify_event_count": len(would_notify_events),
        "status_counts": {
            status: sum(row["outcome_status"] == status for row in events)
            for status in statuses
        },
        "notification_status_counts": {
            status: sum(row["outcome_status"] == status for row in notification_events)
            for status in statuses
        },
        "would_notify_status_counts": {
            status: sum(row["outcome_status"] == status for row in would_notify_events)
            for status in statuses
        },
        "events": events,
        "notification_events": notification_events,
        "would_notify_events": would_notify_events,
    }


def export_p0_2m_live_shadow_rows(
    *,
    session_dates: Sequence[str],
    symbols: Sequence[str],
    universe_hash: str,
    signal_version: str = P0_2M_SIGNAL_VERSION,
    enrollment_key: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate a frozen prospective enrollment into calibration rows.

    Missing symbol-days, endpoint probes, completed evaluations, notification
    decisions, and accepted queue receipts stay explicit.  Queue duplicates
    are derived only from independent append-only notification-attempt rows.
    """

    sessions = _normalise_sessions(session_dates)
    tickers = tuple(str(value or "").strip().upper() for value in symbols)
    if len(tickers) != _EXPECTED_UNIVERSE_SIZE or len(set(tickers)) != len(tickers):
        raise ValueError("live forward shadow export requires 69 unique symbols")
    if any(not ticker for ticker in tickers):
        raise ValueError("shadow universe contains a blank symbol")
    from src.opportunities.p0_2m_calibration import compute_universe_hash

    expected_hash = compute_universe_hash(tickers)
    if str(universe_hash) != expected_hash:
        raise ValueError("universe_hash does not match the 69-symbol universe")
    if str(signal_version) != P0_2M_SIGNAL_VERSION:
        raise ValueError("signal_version does not match the strict 2m runtime")

    date_strings = tuple(day.isoformat() for day in sessions)
    db = _ensure_schema()
    with db.session_scope() as session:
        enrollment = _load_enrollment(
            session,
            universe_hash=expected_hash,
            signal_version=signal_version,
            enrollment_key=enrollment_key,
            sessions=date_strings,
        )
    if enrollment is None:
        raise ValueError("live-forward shadow enrollment is missing")
    if enrollment.universe != tickers:
        raise ValueError("export universe does not match frozen enrollment")
    if enrollment.sessions != date_strings:
        raise ValueError("export sessions do not match frozen enrollment")
    validate_p0_2m_shadow_enrollment_boundary(
        enrolled_at=enrollment.enrolled_at,
        created_at=enrollment.created_at,
        sessions=enrollment.sessions,
    )

    observations = list_p0_2m_shadow_observations(
        et_dates=date_strings,
        symbols=tickers,
        universe_hash=expected_hash,
        signal_version=signal_version,
        enrollment_key=enrollment.enrollment_key,
    )
    by_pair: dict[tuple[str, str], list[_StoredObservation]] = {}
    for item in observations:
        by_pair.setdefault((item.symbol, item.et_date), []).append(item)

    def _accepted_attempt(item: _StoredObservation) -> bool:
        return bool(
            item.record_kind == "notification_attempt"
            and item.qualified
            and item.would_notify
            and item.notification_decision_available
            and item.observer_queue_accepted is True
            and item.observer_queue_enqueued_at is not None
            and item.telegram_enqueued_at is not None
            and item.telegram_enqueue_latency_seconds is not None
            and math.isfinite(item.telegram_enqueue_latency_seconds)
            and item.telegram_enqueue_evidence_mode
            in _VALID_ENQUEUE_EVIDENCE_MODES
        )

    # Episode anchors, rather than adjacent-attempt comparisons, implement the
    # production 30-minute rule.  This correctly treats t+35m as a new episode
    # even if a duplicate was attempted at t+10m.
    duplicate_attempt_keys: set[str] = set()
    attempts_by_episode: dict[tuple[str, str], list[_StoredObservation]] = {}
    for item in observations:
        if _accepted_attempt(item) and item.direction in {"up", "down"}:
            attempts_by_episode.setdefault((item.symbol, item.direction), []).append(item)
    for attempts in attempts_by_episode.values():
        episode_anchor: Optional[datetime] = None
        for item in sorted(
            attempts,
            key=lambda row: (
                row.window_end,
                row.telegram_enqueued_at or row.observed_at,
                row.notification_attempt_id or "",
            ),
        ):
            if episode_anchor is None or (
                item.window_end - episode_anchor
            ).total_seconds() >= 30 * 60:
                episode_anchor = item.window_end
            else:
                duplicate_attempt_keys.add(item.observation_key)

    exported_at = _utc_now()
    calendar = xcals.get_calendar("XNYS")
    shadow_rows: list[dict[str, Any]] = []
    for day in sessions:
        expected = _expected_endpoints(calendar, day)
        expected_by_iso = {value.isoformat(): value for value in expected}
        for symbol in tickers:
            pair_rows = by_pair.get((symbol, day.isoformat()), [])
            rows_by_end: dict[str, list[_StoredObservation]] = {}
            for item in pair_rows:
                key = item.window_end.astimezone(_ET).isoformat()
                rows_by_end.setdefault(key, []).append(item)
            unexpected = sorted(
                key for key in rows_by_end if key not in expected_by_iso
            )

            endpoint_rows: dict[str, _StoredObservation] = {}
            evaluation_rows: dict[str, _StoredObservation] = {}
            attempts_by_end: dict[str, list[_StoredObservation]] = {}
            for key, values in rows_by_end.items():
                if key not in expected_by_iso:
                    continue
                for item in values:
                    if item.record_kind == "endpoint_observation":
                        endpoint_rows.setdefault(key, item)
                    elif item.record_kind == "completed_evaluation":
                        evaluation_rows.setdefault(key, item)
                    elif item.record_kind == "notification_attempt":
                        attempts_by_end.setdefault(key, []).append(item)

            missing = [key for key in expected_by_iso if key not in endpoint_rows]
            usable_evaluations: dict[str, _StoredObservation] = {}
            data_unavailable: list[str] = []
            evaluation_unavailable: list[str] = []
            initial_data_unavailable = [
                key for key, item in endpoint_rows.items() if not item.data_available
            ]
            for key in expected_by_iso:
                item = evaluation_rows.get(key)
                finality_valid = bool(
                    item is not None
                    and item.max_component_end == item.window_end
                    and item.max_component_end <= item.observed_at
                    and not item.unfinished_bar_trigger
                )
                if (
                    key in endpoint_rows
                    and item is not None
                    and item.data_available
                    and item.evaluation_available
                    and finality_valid
                ):
                    usable_evaluations[key] = item
                if item is None or not item.data_available or not finality_valid:
                    data_unavailable.append(key)
                if item is None or not item.evaluation_available or not finality_valid:
                    evaluation_unavailable.append(key)

            qualified_rows = {
                key: item
                for key, item in evaluation_rows.items()
                if item.qualified
            }
            notification_rows = {
                key: item
                for key, item in qualified_rows.items()
                if item.would_notify
            }
            missing_notification_decision = sorted(
                key
                for key, item in qualified_rows.items()
                if not item.notification_decision_available
            )
            orphan_attempts = sorted(
                key for key in attempts_by_end if key not in notification_rows
            )

            first_accepted_by_end: dict[str, _StoredObservation] = {}
            missing_latency: list[str] = []
            for key in notification_rows:
                accepted = sorted(
                    (
                        item
                        for item in attempts_by_end.get(key, ())
                        if _accepted_attempt(item)
                    ),
                    key=lambda item: (
                        item.telegram_enqueued_at or item.observed_at,
                        item.notification_attempt_id or "",
                    ),
                )
                if accepted:
                    first_accepted_by_end[key] = accepted[0]
                else:
                    missing_latency.append(key)

            evidence_modes = {
                str(item.telegram_enqueue_evidence_mode)
                for values in attempts_by_end.values()
                for item in values
                if _accepted_attempt(item)
            }
            # Zero-event rows retain the no-send mode.  Mixed trusted modes
            # are resolved below to the receipt supplying the worst latency;
            # retain the whole accepted-source set for audit, not rejected or
            # timestamp-free attempts that cannot prove queue acceptance.
            enqueue_mode = (
                next(iter(evidence_modes))
                if len(evidence_modes) == 1
                else "counterfactual_no_send_same_queue"
                if not evidence_modes
                else None
            )
            all_endpoint_records = [
                item
                for key, values in rows_by_end.items()
                if key in expected_by_iso
                for item in values
            ]
            duplicate_count: Optional[int]
            if missing_notification_decision or orphan_attempts:
                duplicate_count = None
            else:
                duplicate_count = sum(
                    item.observation_key in duplicate_attempt_keys
                    for values in attempts_by_end.values()
                    for item in values
                )

            aggregate: dict[str, Any] = {
                "observation_mode": LIVE_FORWARD_SHADOW_MODE,
                "enrollment_key": enrollment.enrollment_key,
                "symbol": symbol,
                "session_date": day.isoformat(),
                "session_scope": "regular",
                "universe_hash": expected_hash,
                "signal_version": signal_version,
                "expected_evaluations": len(expected),
                "available_evaluations": len(usable_evaluations),
                "data_available_evaluations": len(usable_evaluations),
                "recorded_endpoint_count": len(endpoint_rows),
                "completed_evaluation_count": len(evaluation_rows),
                "notification_attempt_count": sum(
                    len(values) for values in attempts_by_end.values()
                ),
                "accepted_notification_attempt_count": sum(
                    _accepted_attempt(item)
                    for values in attempts_by_end.values()
                    for item in values
                ),
                "missing_endpoint_count": len(missing),
                "missing_window_ends": missing,
                "initial_data_unavailable_window_ends": sorted(
                    initial_data_unavailable
                ),
                "data_unavailable_window_ends": data_unavailable,
                "evaluation_unavailable_window_ends": evaluation_unavailable,
                "unexpected_window_ends": unexpected,
                "duplicate_notification_count": duplicate_count,
                "missing_notification_decision_window_ends": (
                    missing_notification_decision
                ),
                "orphan_notification_attempt_window_ends": orphan_attempts,
                "duplicate_episode_suppressed_count": sum(
                    item.duplicate_episode_suppressed
                    for item in evaluation_rows.values()
                ),
                "unfinished_bar_trigger_count": sum(
                    item.unfinished_bar_trigger for item in all_endpoint_records
                ),
                "alert_qualified": bool(qualified_rows),
                "would_notify": bool(notification_rows),
                "would_notify_endpoint_count": len(notification_rows),
                "latency_sample_count": len(first_accepted_by_end),
                "missing_latency_window_ends": sorted(missing_latency),
                "telegram_enqueue_evidence_mode": enqueue_mode,
                "telegram_enqueue_evidence_modes": sorted(evidence_modes),
                "telemetry_status": (
                    "missing_symbol_day"
                    if not pair_rows
                    else "missing_endpoints"
                    if missing
                    else "complete"
                ),
                "diagnostics": _forward_outcome_diagnostics(
                    evaluations=usable_evaluations,
                    qualified=qualified_rows,
                    session_close=calendar.session_close(day).to_pydatetime(),
                    exported_at=exported_at,
                    actual_notification_attempts={
                        key: [
                            attempt for attempt in values
                            if _accepted_attempt(attempt)
                            and attempt.telegram_enqueue_evidence_mode
                            == "actual_telegram_queue_receipt"
                        ]
                        for key, values in attempts_by_end.items()
                        if key in usable_evaluations and key in notification_rows
                    },
                ),
            }
            if notification_rows and not missing_latency:
                worst = max(
                    first_accepted_by_end.values(),
                    key=lambda item: float(
                        item.telegram_enqueue_latency_seconds or 0.0
                    ),
                )
                aggregate.update(
                    telegram_enqueue_evidence_mode=(
                        worst.telegram_enqueue_evidence_mode
                    ),
                    window_end=worst.window_end.astimezone(_ET).isoformat(),
                    telegram_enqueued_at=(
                        worst.telegram_enqueued_at.astimezone(_ET).isoformat()
                        if worst.telegram_enqueued_at is not None
                        else None
                    ),
                    telegram_enqueue_latency_seconds=float(
                        worst.telegram_enqueue_latency_seconds or 0.0
                    ),
                )
            shadow_rows.append(aggregate)

    export_payload = {
        "schema_version": P0_2M_SHADOW_EXPORT_SCHEMA_VERSION,
        "shadow_observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "enrollment": {
            "observation_mode": LIVE_FORWARD_SHADOW_MODE,
            "universe_hash": enrollment.universe_hash,
            "signal_version": enrollment.signal_version,
            "universe": list(enrollment.universe),
            "sessions": list(enrollment.sessions),
            "enrolled_at": enrollment.enrolled_at.isoformat(),
            "created_at": enrollment.created_at.isoformat(),
            "enrollment_key": enrollment.enrollment_key,
            "run_request_id": enrollment.run_request_id,
            "restart_reason": enrollment.restart_reason,
            "supersedes_enrollment_key": (
                enrollment.supersedes_enrollment_key
            ),
        },
        "enrollment_started_at": enrollment.enrolled_at.isoformat(),
        "expected_first_session": enrollment.sessions[0],
        "exported_at": exported_at.isoformat(),
        "signal_version": signal_version,
        "universe": list(tickers),
        "universe_hash": expected_hash,
        "shadow_sessions": list(date_strings),
        "shadow": shadow_rows,
    }
    export_payload["export_attestation"] = _record_export_attestation(
        export_sha256=compute_p0_2m_live_shadow_export_sha256(export_payload),
        enrollment=enrollment,
        exported_at=exported_at,
    )
    return export_payload
