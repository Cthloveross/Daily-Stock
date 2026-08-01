# -*- coding: utf-8 -*-
"""Lease-safe repository for shadow premarket evidence prefetch.

This module deliberately contains no provider or scheduler integration.  It
only defines the persistence contract needed by a future default-off shadow
producer:

* one mutable run row per deterministic market-date/universe slot;
* one append-only coherent artifact bundle per claimed attempt;
* owner-and-attempt fencing for every heartbeat or settlement;
* a fail-closed selector for evidence that is safe to consume formally.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import threading
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.opportunities.repository import canonical_json, canonical_sha256
from src.regime.premarket_artifacts import (
    DEFAULT_MAX_FRESHNESS_MINUTES,
    PREMARKET_ARTIFACT_SCHEMA_VERSION,
    PremarketArtifactBundle,
    PremarketArtifactValidationError,
)
from src.regime.premarket_models import (
    RegimePremarketArtifactBundle,
    RegimePremarketArtifactIngestion,
    RegimePremarketPrefetchRun,
)
from src.storage import Base, DatabaseManager, get_db

__all__ = [
    "PremarketArtifactAppendResult",
    "PremarketArtifactConflictError",
    "PremarketEvidenceRepositoryError",
    "PremarketPrefetchClaim",
    "PremarketPrefetchLeaseLostError",
    "StoredPremarketArtifactBundle",
    "StoredPremarketPrefetchRun",
    "append_premarket_artifact_and_settle",
    "build_premarket_prefetch_slot_key",
    "build_premarket_universe_sha256",
    "claim_premarket_prefetch_run",
    "get_premarket_artifact_bundle",
    "get_premarket_prefetch_run",
    "heartbeat_premarket_prefetch_run",
    "init_premarket_evidence_schema",
    "mark_premarket_prefetch_missed",
    "select_formal_premarket_artifact_bundle",
    "settle_premarket_prefetch_run",
]


_APPEND_ONLY_TABLES = (
    RegimePremarketArtifactBundle.__tablename__,
    RegimePremarketArtifactIngestion.__tablename__,
)
_SQLITE_LEGACY_RECEIPT_TRIGGER = (
    "trg_regime_premarket_artifact_bundle_receipt"
)
_SQLITE_SCHEMA_INIT_LOCK = threading.RLock()
_DEFAULT_LEASE_SECONDS = 5 * 60
_DEFAULT_MAX_ATTEMPTS = 2
_DEFAULT_TERMINAL_SETTLEMENT_GRACE_SECONDS = 2 * 60
_DATABASE_TIMESTAMP_RESOLUTION = timedelta(milliseconds=1)
_TERMINAL_STATES = frozenset({"succeeded", "missed"})
_QUALITY_STATES = frozenset({"ready", "partial", "unavailable"})


class PremarketEvidenceRepositoryError(ValueError):
    """Base error for invalid premarket evidence persistence operations."""


class PremarketPrefetchLeaseLostError(PremarketEvidenceRepositoryError):
    """A stale or foreign worker attempted to mutate a claimed run."""


class PremarketArtifactConflictError(PremarketEvidenceRepositoryError):
    """An immutable slot/attempt already contains different evidence."""


@dataclass(frozen=True)
class StoredPremarketPrefetchRun:
    slot_key: str
    market_date_et: date
    previous_session: date
    target_as_of: datetime
    hard_deadline_at: datetime
    policy_version: str
    universe_key: str
    universe_sha256: str
    symbols: tuple[str, ...]
    state: str
    attempt_count: int
    owner_key: Optional[str]
    claimed_at: Optional[datetime]
    lease_expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    bundle_key: Optional[str]
    last_error_code: Optional[str]
    last_error_detail: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PremarketPrefetchClaim:
    claimed: bool
    reason: str
    run: StoredPremarketPrefetchRun


@dataclass(frozen=True)
class StoredPremarketArtifactBundle:
    bundle_key: str
    slot_key: str
    attempt_count: int
    schema_version: str
    policy_version: str
    adapter_key: str
    market_date_et: date
    previous_session: date
    universe_key: str
    universe_sha256: str
    symbols: tuple[str, ...]
    target_as_of: datetime
    fetch_started_at: datetime
    fetched_at: datetime
    expires_at: datetime
    quality_state: str
    coverage: Mapping[str, Any]
    payload_sha256: str
    payload: Mapping[str, Any]
    created_at: datetime
    ingested_at: Optional[datetime]


@dataclass(frozen=True)
class PremarketArtifactAppendResult:
    bundle: StoredPremarketArtifactBundle
    run: StoredPremarketPrefetchRun
    duplicate: bool


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise PremarketEvidenceRepositoryError(f"{field_name} is required")
    return normalized


def _optional_text(value: Any) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _calendar_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise PremarketEvidenceRepositoryError(
            f"{field_name} must be a date"
        )
    return value


def _sha256_text(value: Any, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if (
        len(normalized) != 64
        or normalized != normalized.lower()
        or any(character not in "0123456789abcdef" for character in normalized)
    ):
        raise PremarketEvidenceRepositoryError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return normalized


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise PremarketEvidenceRepositoryError(
            f"{field_name} must be a datetime"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise PremarketEvidenceRepositoryError(
            f"{field_name} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _db_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _symbols(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise PremarketEvidenceRepositoryError(
            "symbols must be a sequence, not a string"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    if not normalized:
        raise PremarketEvidenceRepositoryError("symbols must not be empty")
    return tuple(normalized)


def _mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PremarketEvidenceRepositoryError(
            f"{field_name} must be a mapping"
        )
    return dict(value)


def _artifact_coverage(bundle: PremarketArtifactBundle) -> dict[str, Any]:
    available = [item.symbol for item in bundle.observations if item.is_available]
    unavailable = {
        item.symbol: item.unavailable_reason
        for item in bundle.observations
        if not item.is_available
    }
    return {
        "requested_count": len(bundle.required_universe),
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "available_symbols": available,
        "unavailable_reasons": unavailable,
    }


def _parse_artifact_payload(
    payload: Mapping[str, Any],
) -> PremarketArtifactBundle:
    try:
        return PremarketArtifactBundle.from_payload(payload)
    except PremarketArtifactValidationError as exc:
        raise PremarketEvidenceRepositoryError(
            "payload does not satisfy the premarket artifact contract"
        ) from exc


def _artifact_matches_run_contract(
    bundle: PremarketArtifactBundle,
    run: StoredPremarketPrefetchRun,
    *,
    quality_state: str,
    coverage: Mapping[str, Any],
) -> bool:
    return bool(
        bundle.market_date_et == run.market_date_et
        and bundle.previous_session == run.previous_session
        and bundle.target_as_of == run.target_as_of
        and tuple(bundle.required_universe) == tuple(sorted(run.symbols))
        and bundle.max_freshness_minutes
        == DEFAULT_MAX_FRESHNESS_MINUTES
        and bundle.quality == quality_state
        and canonical_json(_artifact_coverage(bundle))
        == canonical_json(dict(coverage))
    )


def _artifact_evidence_available_by(
    bundle: PremarketArtifactBundle,
    fetched_at: datetime,
) -> bool:
    return all(
        item.evidence_as_of is None or item.evidence_as_of <= fetched_at
        for item in bundle.observations
    )


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _load_mapping(value: str, field_name: str) -> Mapping[str, Any]:
    try:
        loaded = json.loads(value, parse_constant=_reject_nonfinite_json)
    except (TypeError, ValueError) as exc:
        raise PremarketEvidenceRepositoryError(
            f"stored {field_name} is invalid JSON"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise PremarketEvidenceRepositoryError(
            f"stored {field_name} must be a JSON object"
        )
    return loaded


def _load_symbols(value: str) -> tuple[str, ...]:
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PremarketEvidenceRepositoryError(
            "stored symbols_json is invalid JSON"
        ) from exc
    if not isinstance(loaded, list):
        raise PremarketEvidenceRepositoryError(
            "stored symbols_json must be a JSON array"
        )
    normalized = _symbols(loaded)
    if list(normalized) != loaded:
        raise PremarketEvidenceRepositoryError(
            "stored symbols_json is not canonical"
        )
    return normalized


def build_premarket_universe_sha256(
    universe_key: str,
    symbols: Sequence[str],
) -> str:
    """Hash the ordered, normalized universe identity."""

    key = _required_text(universe_key, "universe_key")
    normalized = _symbols(symbols)
    return canonical_sha256(
        {
            "universe_key": key,
            "symbols": list(normalized),
        }
    )


def build_premarket_prefetch_slot_key(
    market_date_et: date,
    previous_session: date,
    *,
    target_as_of: datetime,
    policy_version: str,
    universe_key: str,
    universe_sha256: str,
) -> str:
    """Build one deterministic coordination slot identity."""

    market_date = _calendar_date(market_date_et, "market_date_et")
    prior_session = _calendar_date(previous_session, "previous_session")
    if prior_session >= market_date:
        raise PremarketEvidenceRepositoryError(
            "previous_session must precede market_date_et"
        )
    observed_as_of = _utc(target_as_of, "target_as_of")
    policy = _required_text(policy_version, "policy_version")
    key = _required_text(universe_key, "universe_key")
    digest = _sha256_text(universe_sha256, "universe_sha256")
    return "rpp_" + canonical_sha256(
        {
            "market_date_et": market_date,
            "previous_session": prior_session,
            "target_as_of": observed_as_of,
            "policy_version": policy,
            "universe_key": key,
            "universe_sha256": digest,
        }
    )


def init_premarket_evidence_schema(
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    """Create prefetch tables and SQLite immutability guards.

    A ready SQLite schema takes a read-only fast path.  The writer lock and
    migration DDL are only used when a table, trigger, or trusted receipt is
    actually missing.
    """

    db = db_manager or get_db()
    tables = (
        RegimePremarketPrefetchRun.__table__,
        RegimePremarketArtifactBundle.__table__,
        RegimePremarketArtifactIngestion.__table__,
    )
    if db._engine.dialect.name != "sqlite":
        Base.metadata.create_all(db._engine, tables=tables)
        return

    with db._engine.connect() as connection:
        if _sqlite_premarket_evidence_schema_ready(connection):
            return

    # Serialize same-process cold starts.  BEGIN IMMEDIATE provides the
    # corresponding cross-process migration boundary for SQLite.
    with _SQLITE_SCHEMA_INIT_LOCK:
        with db._engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                if not _sqlite_premarket_evidence_schema_ready(connection):
                    Base.metadata.create_all(connection, tables=tables)
                    _init_sqlite_premarket_evidence_guards(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def _sqlite_premarket_evidence_schema_ready(connection) -> bool:
    """Return whether SQLite can serve evidence without schema writes."""

    objects = {
        (str(object_type), str(name))
        for object_type, name in connection.exec_driver_sql(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'trigger')"
        ).all()
    }
    required_tables = {
        ("table", RegimePremarketPrefetchRun.__tablename__),
        ("table", RegimePremarketArtifactBundle.__tablename__),
        ("table", RegimePremarketArtifactIngestion.__tablename__),
    }
    required_triggers = {
        *{
            (
                "trigger",
                f"trg_{table_name}_{operation.lower()}_immutable",
            )
            for table_name in _APPEND_ONLY_TABLES
            for operation in ("UPDATE", "DELETE")
        },
    }
    if ("trigger", _SQLITE_LEGACY_RECEIPT_TRIGGER) in objects:
        return False
    if not required_tables.issubset(objects):
        return False
    if not required_triggers.issubset(objects):
        return False
    untrusted_receipts = connection.exec_driver_sql(
        "SELECT COUNT(*) "
        "FROM regime_premarket_artifact_bundles AS artifact "
        "JOIN regime_premarket_prefetch_runs AS run "
        "ON run.slot_key = artifact.slot_key "
        "AND run.state = 'succeeded' "
        "AND run.bundle_key = artifact.bundle_key "
        "AND run.attempt_count = artifact.attempt_count "
        "LEFT JOIN regime_premarket_artifact_ingestions AS receipt "
        "ON receipt.bundle_key = artifact.bundle_key "
        "WHERE receipt.bundle_key IS NULL "
        "OR JULIANDAY(artifact.fetched_at) > "
        "JULIANDAY(receipt.ingested_at) + (1.0 / 86400000.0) "
        "OR (run.claimed_at IS NOT NULL "
        "AND JULIANDAY(run.claimed_at) > "
        "JULIANDAY(receipt.ingested_at) + (1.0 / 86400000.0)) "
        "OR JULIANDAY(receipt.ingested_at) "
        "+ (1.0 / 86400000.0) > JULIANDAY(run.hard_deadline_at)"
    ).scalar_one()
    return int(untrusted_receipts) == 0


def _init_sqlite_premarket_evidence_guards(connection) -> None:
    """Install immutable guards and migration-time receipt coverage."""

    for table_name in _APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS "
                f"trg_{table_name}_"
                f"{operation.lower()}_immutable "
                f"BEFORE {operation} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, "
                "'premarket evidence rows are append-only'); END"
            )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS "
        f"{_SQLITE_LEGACY_RECEIPT_TRIGGER}"
    )
    connection.exec_driver_sql(
        "INSERT INTO regime_premarket_artifact_ingestions "
        "(bundle_key, ingested_at) "
        "SELECT artifact.bundle_key, "
        "STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW') "
        "FROM regime_premarket_artifact_bundles AS artifact "
        "JOIN regime_premarket_prefetch_runs AS run "
        "ON run.slot_key = artifact.slot_key "
        "AND run.state = 'succeeded' "
        "AND run.bundle_key = artifact.bundle_key "
        "AND run.attempt_count = artifact.attempt_count "
        "LEFT JOIN regime_premarket_artifact_ingestions AS receipt "
        "ON receipt.bundle_key = artifact.bundle_key "
        "WHERE receipt.bundle_key IS NULL"
    )
    connection.exec_driver_sql(
        "UPDATE regime_premarket_prefetch_runs "
        "SET completed_at = ("
        "SELECT receipt.ingested_at "
        "FROM regime_premarket_artifact_ingestions AS receipt "
        "WHERE receipt.bundle_key = "
        "regime_premarket_prefetch_runs.bundle_key"
        "), updated_at = ("
        "SELECT receipt.ingested_at "
        "FROM regime_premarket_artifact_ingestions AS receipt "
        "WHERE receipt.bundle_key = "
        "regime_premarket_prefetch_runs.bundle_key"
        ") "
        "WHERE state = 'succeeded' "
        "AND bundle_key IS NOT NULL "
        "AND EXISTS ("
        "SELECT 1 "
        "FROM regime_premarket_artifact_ingestions AS receipt "
        "WHERE receipt.bundle_key = "
        "regime_premarket_prefetch_runs.bundle_key"
        ")"
    )
    connection.exec_driver_sql(
        "UPDATE regime_premarket_prefetch_runs "
        "SET state = 'failed', "
        "lease_expires_at = NULL, "
        "bundle_key = NULL, "
        "last_error_code = "
        "'artifact_receipt_causal_order_invalid', "
        "last_error_detail = "
        "'recovered ingestion receipt violates causal ordering' "
        "WHERE state = 'succeeded' "
        "AND bundle_key IS NOT NULL "
        "AND EXISTS ("
        "SELECT 1 "
        "FROM regime_premarket_artifact_bundles AS artifact "
        "JOIN regime_premarket_artifact_ingestions AS receipt "
        "ON receipt.bundle_key = artifact.bundle_key "
        "WHERE artifact.bundle_key = "
        "regime_premarket_prefetch_runs.bundle_key "
        "AND ("
        "JULIANDAY(artifact.fetched_at) > "
        "JULIANDAY(receipt.ingested_at) + (1.0 / 86400000.0) "
        "OR (regime_premarket_prefetch_runs.claimed_at IS NOT NULL "
        "AND JULIANDAY("
        "regime_premarket_prefetch_runs.claimed_at"
        ") > JULIANDAY(receipt.ingested_at) "
        "+ (1.0 / 86400000.0)) "
        "OR JULIANDAY(receipt.ingested_at) "
        "+ (1.0 / 86400000.0) > JULIANDAY("
        "regime_premarket_prefetch_runs.hard_deadline_at"
        ")"
        "))"
    )
    untrusted_receipts = connection.exec_driver_sql(
        "SELECT COUNT(*) "
        "FROM regime_premarket_artifact_bundles AS artifact "
        "JOIN regime_premarket_prefetch_runs AS run "
        "ON run.slot_key = artifact.slot_key "
        "AND run.state = 'succeeded' "
        "AND run.bundle_key = artifact.bundle_key "
        "AND run.attempt_count = artifact.attempt_count "
        "LEFT JOIN regime_premarket_artifact_ingestions AS receipt "
        "ON receipt.bundle_key = artifact.bundle_key "
        "WHERE receipt.bundle_key IS NULL "
        "OR JULIANDAY(artifact.fetched_at) > "
        "JULIANDAY(receipt.ingested_at) + (1.0 / 86400000.0) "
        "OR (run.claimed_at IS NOT NULL "
        "AND JULIANDAY(run.claimed_at) > "
        "JULIANDAY(receipt.ingested_at) + (1.0 / 86400000.0)) "
        "OR JULIANDAY(receipt.ingested_at) "
        "+ (1.0 / 86400000.0) > JULIANDAY(run.hard_deadline_at)"
    ).scalar_one()
    if int(untrusted_receipts) != 0:
        raise PremarketEvidenceRepositoryError(
            "artifact ingestion receipt migration is incomplete"
        )


def _stored_run(
    row: RegimePremarketPrefetchRun,
) -> StoredPremarketPrefetchRun:
    return StoredPremarketPrefetchRun(
        slot_key=str(row.slot_key),
        market_date_et=row.market_date_et,
        previous_session=row.previous_session,
        target_as_of=_db_utc(row.target_as_of),
        hard_deadline_at=_db_utc(row.hard_deadline_at),
        policy_version=str(row.policy_version),
        universe_key=str(row.universe_key),
        universe_sha256=str(row.universe_sha256),
        symbols=_load_symbols(row.symbols_json),
        state=str(row.state),
        attempt_count=int(row.attempt_count),
        owner_key=str(row.owner_key) if row.owner_key else None,
        claimed_at=_db_utc(row.claimed_at),
        lease_expires_at=_db_utc(row.lease_expires_at),
        completed_at=_db_utc(row.completed_at),
        bundle_key=str(row.bundle_key) if row.bundle_key else None,
        last_error_code=(
            str(row.last_error_code) if row.last_error_code else None
        ),
        last_error_detail=(
            str(row.last_error_detail) if row.last_error_detail else None
        ),
        created_at=_db_utc(row.created_at),
        updated_at=_db_utc(row.updated_at),
    )


def _stored_bundle(
    row: RegimePremarketArtifactBundle,
    *,
    ingested_at: Optional[datetime] = None,
) -> StoredPremarketArtifactBundle:
    return StoredPremarketArtifactBundle(
        bundle_key=str(row.bundle_key),
        slot_key=str(row.slot_key),
        attempt_count=int(row.attempt_count),
        schema_version=str(row.schema_version),
        policy_version=str(row.policy_version),
        adapter_key=str(row.adapter_key),
        market_date_et=row.market_date_et,
        previous_session=row.previous_session,
        universe_key=str(row.universe_key),
        universe_sha256=str(row.universe_sha256),
        symbols=_load_symbols(row.symbols_json),
        target_as_of=_db_utc(row.target_as_of),
        fetch_started_at=_db_utc(row.fetch_started_at),
        fetched_at=_db_utc(row.fetched_at),
        expires_at=_db_utc(row.expires_at),
        quality_state=str(row.quality_state),
        coverage=_load_mapping(row.coverage_json, "coverage_json"),
        payload_sha256=str(row.payload_sha256),
        payload=_load_mapping(row.payload_json, "payload_json"),
        created_at=_db_utc(row.created_at),
        ingested_at=_db_utc(ingested_at),
    )


def _select_run(
    session,
    slot_key: str,
    *,
    lock: bool,
    sqlite: bool,
) -> Optional[RegimePremarketPrefetchRun]:
    query = select(RegimePremarketPrefetchRun).where(
        RegimePremarketPrefetchRun.slot_key == slot_key
    )
    if lock and not sqlite:
        query = query.with_for_update()
    return session.execute(query).scalar_one_or_none()


def _lock_or_create_run(
    session,
    *,
    db: DatabaseManager,
    values: Mapping[str, Any],
) -> RegimePremarketPrefetchRun:
    slot_key = str(values["slot_key"])
    if db._is_sqlite_engine:
        row = _select_run(
            session,
            slot_key,
            lock=False,
            sqlite=True,
        )
        if row is None:
            row = RegimePremarketPrefetchRun(**dict(values))
            session.add(row)
            session.flush()
        return row

    try:
        with session.begin_nested():
            session.add(RegimePremarketPrefetchRun(**dict(values)))
            session.flush()
    except IntegrityError:
        pass
    row = _select_run(
        session,
        slot_key,
        lock=True,
        sqlite=False,
    )
    if row is None:  # pragma: no cover - defensive against broken isolation
        raise PremarketEvidenceRepositoryError(
            "prefetch slot disappeared during claim"
        )
    return row


def _assert_run_contract(
    run: StoredPremarketPrefetchRun,
    *,
    market_date_et: date,
    previous_session: date,
    target_as_of: datetime,
    hard_deadline_at: datetime,
    policy_version: str,
    universe_key: str,
    universe_sha256: str,
    symbols: tuple[str, ...],
) -> None:
    expected = (
        market_date_et,
        previous_session,
        target_as_of,
        hard_deadline_at,
        policy_version,
        universe_key,
        universe_sha256,
        symbols,
    )
    actual = (
        run.market_date_et,
        run.previous_session,
        run.target_as_of,
        run.hard_deadline_at,
        run.policy_version,
        run.universe_key,
        run.universe_sha256,
        run.symbols,
    )
    if actual != expected:
        raise PremarketArtifactConflictError(
            "prefetch slot already contains different immutable inputs"
        )


def claim_premarket_prefetch_run(
    market_date_et: date,
    previous_session: date,
    *,
    target_as_of: datetime,
    hard_deadline_at: datetime,
    policy_version: str,
    universe_key: str,
    symbols: Sequence[str],
    owner_key: str,
    now: Optional[datetime] = None,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    db_manager: Optional[DatabaseManager] = None,
) -> PremarketPrefetchClaim:
    """Claim or recover one deterministic prefetch slot.

    ``owner_key`` and the returned ``attempt_count`` form the fencing token
    required by every later mutation.
    """

    market_date_et = _calendar_date(market_date_et, "market_date_et")
    previous_session = _calendar_date(previous_session, "previous_session")
    if previous_session >= market_date_et:
        raise PremarketEvidenceRepositoryError(
            "previous_session must precede market_date_et"
        )
    target = _utc(target_as_of, "target_as_of")
    deadline = _utc(hard_deadline_at, "hard_deadline_at")
    if deadline < target:
        raise PremarketEvidenceRepositoryError(
            "hard_deadline_at must not precede target_as_of"
        )
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    policy = _required_text(policy_version, "policy_version")
    universe = _required_text(universe_key, "universe_key")
    normalized_symbols = _symbols(symbols)
    owner = _required_text(owner_key, "owner_key")
    attempts = int(max_attempts)
    if attempts < 1:
        raise PremarketEvidenceRepositoryError(
            "max_attempts must be at least 1"
        )
    lease_duration = max(30, int(lease_seconds))
    universe_sha256 = build_premarket_universe_sha256(
        universe,
        normalized_symbols,
    )
    slot_key = build_premarket_prefetch_slot_key(
        market_date_et,
        previous_session,
        target_as_of=target,
        policy_version=policy,
        universe_key=universe,
        universe_sha256=universe_sha256,
    )
    symbols_json = canonical_json(list(normalized_symbols))
    db = db_manager or get_db()
    init_premarket_evidence_schema(db)

    values = {
        "slot_key": slot_key,
        "market_date_et": market_date_et,
        "previous_session": previous_session,
        "target_as_of": target,
        "hard_deadline_at": deadline,
        "policy_version": policy,
        "universe_key": universe,
        "universe_sha256": universe_sha256,
        "symbols_json": symbols_json,
        "state": "pending",
        "attempt_count": 0,
        "created_at": observed_at,
        "updated_at": observed_at,
    }
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = _lock_or_create_run(session, db=db, values=values)
        current = _stored_run(row)
        _assert_run_contract(
            current,
            market_date_et=market_date_et,
            previous_session=previous_session,
            target_as_of=target,
            hard_deadline_at=deadline,
            policy_version=policy,
            universe_key=universe,
            universe_sha256=universe_sha256,
            symbols=normalized_symbols,
        )

        if current.state in _TERMINAL_STATES:
            return PremarketPrefetchClaim(
                False,
                f"terminal_{current.state}",
                current,
            )

        lease_live = (
            current.state == "running"
            and current.lease_expires_at is not None
            and current.lease_expires_at > observed_at
        )
        if lease_live:
            return PremarketPrefetchClaim(False, "lease_live", current)

        if observed_at >= deadline:
            if current.state in {"failed", "timed_out"}:
                return PremarketPrefetchClaim(
                    False,
                    f"terminal_{current.state}",
                    current,
                )
            if current.state == "running":
                row.state = "timed_out"
            elif current.attempt_count == 0:
                row.state = "missed"
            else:
                row.state = current.state
            row.completed_at = observed_at
            row.lease_expires_at = None
            row.last_error_code = (
                "lease_expired_at_deadline"
                if current.state == "running"
                else (
                    "hard_deadline_missed"
                    if current.attempt_count == 0
                    else current.last_error_code
                )
            )
            if current.state == "running" or current.attempt_count == 0:
                row.last_error_detail = None
            row.updated_at = observed_at
            session.flush()
            return PremarketPrefetchClaim(
                False,
                f"terminal_{row.state}",
                _stored_run(row),
            )

        if current.attempt_count >= attempts:
            if current.state == "running":
                row.state = "timed_out"
                row.completed_at = observed_at
                row.lease_expires_at = None
                row.last_error_code = "lease_expired_attempt_budget"
                row.last_error_detail = None
                row.updated_at = observed_at
                session.flush()
                current = _stored_run(row)
            return PremarketPrefetchClaim(
                False,
                "attempt_budget_exhausted",
                current,
            )

        row.state = "running"
        row.attempt_count = current.attempt_count + 1
        row.owner_key = owner
        row.claimed_at = observed_at
        row.lease_expires_at = min(
            deadline,
            observed_at + timedelta(seconds=lease_duration),
        )
        row.completed_at = None
        row.bundle_key = None
        row.last_error_code = None
        row.last_error_detail = None
        row.updated_at = observed_at
        session.flush()
        return PremarketPrefetchClaim(True, "claimed", _stored_run(row))


def mark_premarket_prefetch_missed(
    market_date_et: date,
    previous_session: date,
    *,
    target_as_of: datetime,
    hard_deadline_at: datetime,
    policy_version: str,
    universe_key: str,
    symbols: Sequence[str],
    now: Optional[datetime] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> StoredPremarketPrefetchRun:
    """Persist a never-started slot as missed after its hard deadline.

    If the slot did start, its existing ``failed`` or ``timed_out`` outcome is
    preserved; a lease that expired at the deadline becomes ``timed_out``.
    """

    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    deadline = _utc(hard_deadline_at, "hard_deadline_at")
    if observed_at < deadline:
        raise PremarketEvidenceRepositoryError(
            "a prefetch slot cannot be marked missed before its deadline"
        )
    claim = claim_premarket_prefetch_run(
        market_date_et,
        previous_session,
        target_as_of=target_as_of,
        hard_deadline_at=deadline,
        policy_version=policy_version,
        universe_key=universe_key,
        symbols=symbols,
        owner_key="_system_missed_marker",
        now=observed_at,
        db_manager=db_manager,
    )
    if claim.claimed:  # pragma: no cover - guarded by observed_at >= deadline
        raise PremarketEvidenceRepositoryError(
            "missed marker unexpectedly claimed work"
        )
    return claim.run


def _require_owned_live_run(
    row: Optional[RegimePremarketPrefetchRun],
    *,
    owner_key: str,
    attempt_count: int,
    observed_at: datetime,
) -> StoredPremarketPrefetchRun:
    if row is None:
        raise PremarketPrefetchLeaseLostError("prefetch run does not exist")
    current = _stored_run(row)
    if (
        current.state != "running"
        or current.owner_key != owner_key
        or current.attempt_count != attempt_count
        or current.lease_expires_at is None
        or current.lease_expires_at <= observed_at
        or current.hard_deadline_at <= observed_at
    ):
        raise PremarketPrefetchLeaseLostError(
            "prefetch lease is no longer owned by this attempt"
        )
    return current


def get_premarket_prefetch_run(
    slot_key: str,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[StoredPremarketPrefetchRun]:
    """Return one coordination row without mutating it."""

    key = _required_text(slot_key, "slot_key")
    db = db_manager or get_db()
    init_premarket_evidence_schema(db)
    with db.session_scope() as session:
        row = _select_run(
            session,
            key,
            lock=False,
            sqlite=db._is_sqlite_engine,
        )
        return _stored_run(row) if row is not None else None


def get_premarket_artifact_bundle(
    bundle_key: str,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[StoredPremarketArtifactBundle]:
    """Return one immutable artifact bundle by content-derived key."""

    key = _required_text(bundle_key, "bundle_key")
    db = db_manager or get_db()
    init_premarket_evidence_schema(db)
    with db.session_scope() as session:
        result = session.execute(
            select(
                RegimePremarketArtifactBundle,
                RegimePremarketArtifactIngestion,
            )
            .outerjoin(
                RegimePremarketArtifactIngestion,
                RegimePremarketArtifactIngestion.bundle_key
                == RegimePremarketArtifactBundle.bundle_key,
            )
            .where(
                RegimePremarketArtifactBundle.bundle_key == key
            )
        ).one_or_none()
        if result is None:
            return None
        artifact_row, ingestion_row = result
        return _stored_bundle(
            artifact_row,
            ingested_at=(
                ingestion_row.ingested_at
                if ingestion_row is not None
                else None
            ),
        )


def heartbeat_premarket_prefetch_run(
    slot_key: str,
    *,
    owner_key: str,
    attempt_count: int,
    now: Optional[datetime] = None,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    db_manager: Optional[DatabaseManager] = None,
) -> StoredPremarketPrefetchRun:
    """Extend a live lease without crossing the slot's hard deadline."""

    key = _required_text(slot_key, "slot_key")
    owner = _required_text(owner_key, "owner_key")
    attempt = int(attempt_count)
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    lease_duration = max(30, int(lease_seconds))
    db = db_manager or get_db()
    init_premarket_evidence_schema(db)

    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = _select_run(
            session,
            key,
            lock=True,
            sqlite=db._is_sqlite_engine,
        )
        current = _require_owned_live_run(
            row,
            owner_key=owner,
            attempt_count=attempt,
            observed_at=observed_at,
        )
        row.lease_expires_at = min(
            current.hard_deadline_at,
            observed_at + timedelta(seconds=lease_duration),
        )
        row.updated_at = observed_at
        session.flush()
        return _stored_run(row)


def settle_premarket_prefetch_run(
    slot_key: str,
    *,
    owner_key: str,
    attempt_count: int,
    state: str,
    error_code: Optional[str] = None,
    error_detail: Optional[str] = None,
    now: Optional[datetime] = None,
    settlement_grace_seconds: int = (
        _DEFAULT_TERMINAL_SETTLEMENT_GRACE_SECONDS
    ),
    db_manager: Optional[DatabaseManager] = None,
) -> StoredPremarketPrefetchRun:
    """Settle a live attempt without publishing formal evidence.

    Success is intentionally unavailable here.  Only
    :func:`append_premarket_artifact_and_settle` can transition a run to
    ``succeeded`` so the bundle and terminal state commit atomically.
    """

    key = _required_text(slot_key, "slot_key")
    owner = _required_text(owner_key, "owner_key")
    attempt = int(attempt_count)
    normalized_state = str(state or "").strip().lower()
    if normalized_state not in {"timed_out", "failed"}:
        raise PremarketEvidenceRepositoryError(
            "state must be timed_out or failed"
        )
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    normalized_error = _optional_text(error_code)
    if normalized_error is None:
        raise PremarketEvidenceRepositoryError(
            "error_code is required for unsuccessful settlement"
        )
    detail = _optional_text(error_detail)
    grace_seconds = int(settlement_grace_seconds)
    if grace_seconds < 0:
        raise PremarketEvidenceRepositoryError(
            "settlement_grace_seconds must not be negative"
        )
    db = db_manager or get_db()
    init_premarket_evidence_schema(db)

    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = _select_run(
            session,
            key,
            lock=True,
            sqlite=db._is_sqlite_engine,
        )
        if row is None:
            raise PremarketPrefetchLeaseLostError(
                "prefetch run does not exist"
            )
        current = _stored_run(row)
        settlement_deadline = current.hard_deadline_at + timedelta(
            seconds=grace_seconds
        )
        if (
            current.state != "running"
            or current.owner_key != owner
            or current.attempt_count != attempt
            or observed_at > settlement_deadline
        ):
            raise PremarketPrefetchLeaseLostError(
                "prefetch attempt can no longer record its terminal state"
            )
        row.state = normalized_state
        row.completed_at = observed_at
        row.lease_expires_at = None
        row.bundle_key = None
        row.last_error_code = normalized_error
        row.last_error_detail = detail
        row.updated_at = observed_at
        session.flush()
        return _stored_run(row)


def _bundle_semantic(
    *,
    run: StoredPremarketPrefetchRun,
    attempt_count: int,
    schema_version: str,
    adapter_key: str,
    fetch_started_at: datetime,
    fetched_at: datetime,
    expires_at: datetime,
    quality_state: str,
    coverage: Mapping[str, Any],
    payload_sha256: str,
) -> dict[str, Any]:
    return {
        "slot_key": run.slot_key,
        "attempt_count": attempt_count,
        "schema_version": schema_version,
        "policy_version": run.policy_version,
        "adapter_key": adapter_key,
        "market_date_et": run.market_date_et,
        "previous_session": run.previous_session,
        "universe_key": run.universe_key,
        "universe_sha256": run.universe_sha256,
        "symbols": list(run.symbols),
        "target_as_of": run.target_as_of,
        "fetch_started_at": fetch_started_at,
        "fetched_at": fetched_at,
        "expires_at": expires_at,
        "quality_state": quality_state,
        "coverage": dict(coverage),
        "payload_sha256": payload_sha256,
    }


def _bundle_key(semantic: Mapping[str, Any]) -> str:
    return "rpb_" + canonical_sha256(dict(semantic))


def _bundle_matches_semantic(
    stored: StoredPremarketArtifactBundle,
    semantic: Mapping[str, Any],
) -> bool:
    actual = _bundle_semantic(
        run=StoredPremarketPrefetchRun(
            slot_key=stored.slot_key,
            market_date_et=stored.market_date_et,
            previous_session=stored.previous_session,
            target_as_of=stored.target_as_of,
            hard_deadline_at=stored.expires_at,
            policy_version=stored.policy_version,
            universe_key=stored.universe_key,
            universe_sha256=stored.universe_sha256,
            symbols=stored.symbols,
            state="succeeded",
            attempt_count=stored.attempt_count,
            owner_key=None,
            claimed_at=None,
            lease_expires_at=None,
            completed_at=None,
            bundle_key=stored.bundle_key,
            last_error_code=None,
            last_error_detail=None,
            created_at=stored.created_at,
            updated_at=stored.created_at,
        ),
        attempt_count=stored.attempt_count,
        schema_version=stored.schema_version,
        adapter_key=stored.adapter_key,
        fetch_started_at=stored.fetch_started_at,
        fetched_at=stored.fetched_at,
        expires_at=stored.expires_at,
        quality_state=stored.quality_state,
        coverage=stored.coverage,
        payload_sha256=stored.payload_sha256,
    )
    return canonical_json(actual) == canonical_json(dict(semantic))


def _receipt_causal_order_error(
    bundle: StoredPremarketArtifactBundle,
    run: StoredPremarketPrefetchRun,
) -> Optional[str]:
    if bundle.ingested_at is None:
        return "artifact ingestion receipt has no trusted timestamp"
    receipt_upper_bound = (
        bundle.ingested_at + _DATABASE_TIMESTAMP_RESOLUTION
    )
    if bundle.fetched_at > receipt_upper_bound:
        return "fetched_at cannot follow the database ingestion receipt"
    if (
        run.claimed_at is not None
        and run.claimed_at > receipt_upper_bound
    ):
        return "claimed_at cannot follow the database ingestion receipt"
    if receipt_upper_bound > run.hard_deadline_at:
        return "database ingestion crossed hard_deadline_at"
    return None


def append_premarket_artifact_and_settle(
    slot_key: str,
    *,
    owner_key: str,
    attempt_count: int,
    schema_version: str,
    adapter_key: str,
    fetch_started_at: datetime,
    fetched_at: datetime,
    expires_at: datetime,
    quality_state: str,
    coverage: Mapping[str, Any],
    payload: Mapping[str, Any],
    created_at: Optional[datetime] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> PremarketArtifactAppendResult:
    """Publish a bundle with an after-commit, database-timestamped receipt.

    The artifact and succeeded run commit together first.  A second
    transaction then writes the trusted receipt, which is the formal
    visibility gate.  A crash between the transactions is recoverable and
    remains invisible to formal selectors until a receipt is added.
    """

    key = _required_text(slot_key, "slot_key")
    owner = _required_text(owner_key, "owner_key")
    attempt = int(attempt_count)
    if attempt < 1:
        raise PremarketEvidenceRepositoryError(
            "attempt_count must be positive"
        )
    schema = _required_text(schema_version, "schema_version")
    if schema != PREMARKET_ARTIFACT_SCHEMA_VERSION:
        raise PremarketEvidenceRepositoryError(
            "schema_version is not supported by the artifact validator"
        )
    adapter = _required_text(adapter_key, "adapter_key")
    fetch_started = _utc(fetch_started_at, "fetch_started_at")
    fetched = _utc(fetched_at, "fetched_at")
    expires = _utc(expires_at, "expires_at")
    observed_at = _utc(
        created_at or datetime.now(timezone.utc),
        "created_at",
    )
    if fetch_started > fetched:
        raise PremarketEvidenceRepositoryError(
            "fetch_started_at must not follow fetched_at"
        )
    if fetched > observed_at:
        raise PremarketEvidenceRepositoryError(
            "created_at must not precede fetched_at"
        )
    quality = str(quality_state or "").strip().lower()
    if quality not in _QUALITY_STATES:
        raise PremarketEvidenceRepositoryError(
            "quality_state must be ready, partial, or unavailable"
        )
    coverage_payload = _mapping(coverage, "coverage")
    supplied_payload = _mapping(payload, "payload")
    validated_bundle = _parse_artifact_payload(supplied_payload)
    if not _artifact_evidence_available_by(validated_bundle, fetched):
        raise PremarketEvidenceRepositoryError(
            "artifact evidence_as_of must not follow fetched_at"
        )
    # Persist the validated replay rather than caller ordering/rounding so
    # semantically equivalent retries resolve to one immutable artifact.
    evidence_payload = validated_bundle.to_payload()
    coverage_json = canonical_json(coverage_payload)
    payload_json = canonical_json(evidence_payload)
    payload_sha256 = canonical_sha256(evidence_payload)
    db = db_manager or get_db()
    if not db._is_sqlite_engine:
        raise PremarketEvidenceRepositoryError(
            "trusted premarket artifact receipts currently require SQLite"
        )
    init_premarket_evidence_schema(db)

    created_artifact = False
    staged_bundle_key: Optional[str] = None
    staged_semantic: Optional[Mapping[str, Any]] = None

    # Phase 1 commits the immutable artifact and matching succeeded run.
    # Formal consumers still cannot see it because no trusted receipt exists.
    with db.session_scope() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = _select_run(
            session,
            key,
            lock=True,
            sqlite=True,
        )
        if row is None:
            raise PremarketPrefetchLeaseLostError(
                "prefetch run does not exist"
            )
        current = _stored_run(row)
        if not _artifact_matches_run_contract(
            validated_bundle,
            current,
            quality_state=quality,
            coverage=coverage_payload,
        ):
            raise PremarketEvidenceRepositoryError(
                "payload or coverage does not match the claimed prefetch run"
            )
        semantic = _bundle_semantic(
            run=current,
            attempt_count=attempt,
            schema_version=schema,
            adapter_key=adapter,
            fetch_started_at=fetch_started,
            fetched_at=fetched,
            expires_at=expires,
            quality_state=quality,
            coverage=coverage_payload,
            payload_sha256=payload_sha256,
        )
        bundle_key = _bundle_key(semantic)
        existing_result = session.execute(
            select(
                RegimePremarketArtifactBundle,
                RegimePremarketArtifactIngestion,
            )
            .outerjoin(
                RegimePremarketArtifactIngestion,
                RegimePremarketArtifactIngestion.bundle_key
                == RegimePremarketArtifactBundle.bundle_key,
            )
            .where(
                RegimePremarketArtifactBundle.slot_key == key,
                RegimePremarketArtifactBundle.attempt_count == attempt,
            )
        ).one_or_none()
        if existing_result is not None:
            existing_row, existing_ingestion = existing_result
            existing = _stored_bundle(
                existing_row,
                ingested_at=(
                    existing_ingestion.ingested_at
                    if existing_ingestion is not None
                    else None
                ),
            )
            if (
                existing.bundle_key != bundle_key
                or not _bundle_matches_semantic(existing, semantic)
                or canonical_json(existing.payload) != payload_json
            ):
                raise PremarketArtifactConflictError(
                    "prefetch attempt already contains different evidence"
                )
            if existing_ingestion is not None:
                receipt_error = _receipt_causal_order_error(
                    existing,
                    current,
                )
                if receipt_error is not None:
                    raise PremarketArtifactConflictError(
                        "artifact ingestion receipt violates causal ordering: "
                        f"{receipt_error}"
                    )
            if (
                current.state != "succeeded"
                or current.bundle_key != existing.bundle_key
            ):
                raise PremarketArtifactConflictError(
                    "artifact exists without matching succeeded run"
                )
            if existing_ingestion is not None:
                return PremarketArtifactAppendResult(
                    existing,
                    current,
                    True,
                )
        else:
            _require_owned_live_run(
                row,
                owner_key=owner,
                attempt_count=attempt,
                observed_at=observed_at,
            )
            if (
                fetched >= current.hard_deadline_at
                or observed_at >= current.hard_deadline_at
            ):
                raise PremarketEvidenceRepositoryError(
                    "bundle must be fetched and committed before "
                    "hard_deadline_at"
                )
            if expires < current.target_as_of:
                raise PremarketEvidenceRepositoryError(
                    "bundle cannot expire before target_as_of"
                )

            session.add(
                RegimePremarketArtifactBundle(
                    bundle_key=bundle_key,
                    slot_key=key,
                    attempt_count=attempt,
                    schema_version=schema,
                    policy_version=current.policy_version,
                    adapter_key=adapter,
                    market_date_et=current.market_date_et,
                    previous_session=current.previous_session,
                    universe_key=current.universe_key,
                    universe_sha256=current.universe_sha256,
                    symbols_json=canonical_json(list(current.symbols)),
                    target_as_of=current.target_as_of,
                    fetch_started_at=fetch_started,
                    fetched_at=fetched,
                    expires_at=expires,
                    quality_state=quality,
                    coverage_json=coverage_json,
                    payload_sha256=payload_sha256,
                    payload_json=payload_json,
                    created_at=observed_at,
                )
            )
            row.state = "succeeded"
            row.completed_at = observed_at
            row.lease_expires_at = None
            row.bundle_key = bundle_key
            row.last_error_code = None
            row.last_error_detail = None
            row.updated_at = observed_at
            session.flush()
            created_artifact = True

        staged_bundle_key = bundle_key
        staged_semantic = semantic

    if staged_bundle_key is None or staged_semantic is None:
        raise PremarketEvidenceRepositoryError(
            "artifact staging did not produce a bundle identity"
        )

    # Phase 2 runs only after the artifact + succeeded run commit above.  Its
    # database-generated timestamp is therefore a conservative upper bound on
    # when the evidence first became committed and visible.
    finalization_error: Optional[PremarketEvidenceRepositoryError] = None
    result: Optional[PremarketArtifactAppendResult] = None
    with db.session_scope() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = _select_run(
            session,
            key,
            lock=True,
            sqlite=True,
        )
        artifact_result = session.execute(
            select(
                RegimePremarketArtifactBundle,
                RegimePremarketArtifactIngestion,
            )
            .outerjoin(
                RegimePremarketArtifactIngestion,
                RegimePremarketArtifactIngestion.bundle_key
                == RegimePremarketArtifactBundle.bundle_key,
            )
            .where(
                RegimePremarketArtifactBundle.bundle_key
                == staged_bundle_key
            )
        ).one_or_none()
        if row is None or artifact_result is None:
            raise PremarketArtifactConflictError(
                "staged artifact disappeared before receipt finalization"
            )
        artifact_row, ingestion_row = artifact_result
        current = _stored_run(row)
        if (
            current.state != "succeeded"
            or current.bundle_key != staged_bundle_key
            or current.attempt_count != attempt
        ):
            raise PremarketArtifactConflictError(
                "staged artifact no longer matches its succeeded run"
            )
        if ingestion_row is None:
            session.connection().exec_driver_sql(
                "INSERT INTO regime_premarket_artifact_ingestions "
                "(bundle_key, ingested_at) "
                "VALUES (?, STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW'))",
                (staged_bundle_key,),
            )
            ingestion_row = session.execute(
                select(RegimePremarketArtifactIngestion).where(
                    RegimePremarketArtifactIngestion.bundle_key
                    == staged_bundle_key
                )
            ).scalar_one_or_none()
        if ingestion_row is None:  # pragma: no cover - DB invariant
            raise PremarketEvidenceRepositoryError(
                "database did not create an artifact ingestion receipt"
            )
        stored = _stored_bundle(
            artifact_row,
            ingested_at=ingestion_row.ingested_at,
        )
        if (
            not _bundle_matches_semantic(stored, staged_semantic)
            or canonical_json(stored.payload) != payload_json
        ):
            raise PremarketArtifactConflictError(
                "staged artifact changed before receipt finalization"
            )
        receipt_error = _receipt_causal_order_error(stored, current)
        if receipt_error is not None:
            trusted_ingested_at = stored.ingested_at
            if trusted_ingested_at is None:  # pragma: no cover - guarded above
                raise PremarketEvidenceRepositoryError(receipt_error)
            row.state = "failed"
            row.completed_at = trusted_ingested_at
            row.lease_expires_at = None
            row.bundle_key = None
            row.last_error_code = "artifact_receipt_causal_order_invalid"
            row.last_error_detail = receipt_error
            row.updated_at = trusted_ingested_at
            session.flush()
            finalization_error = PremarketEvidenceRepositoryError(
                receipt_error
            )
        else:
            trusted_ingested_at = stored.ingested_at
            if trusted_ingested_at is None:  # pragma: no cover - guarded above
                raise PremarketEvidenceRepositoryError(
                    "database did not generate artifact ingested_at"
                )
            row.completed_at = trusted_ingested_at
            row.updated_at = trusted_ingested_at
            session.flush()
            result = PremarketArtifactAppendResult(
                stored,
                _stored_run(row),
                not created_artifact,
            )

    if finalization_error is not None:
        raise finalization_error
    if result is None:  # pragma: no cover - defensive
        raise PremarketEvidenceRepositoryError(
            "artifact receipt finalization produced no result"
        )
    return result


def _bundle_integrity_matches(
    bundle: StoredPremarketArtifactBundle,
    run: StoredPremarketPrefetchRun,
) -> bool:
    try:
        if canonical_sha256(bundle.payload) != bundle.payload_sha256:
            return False
        if (
            build_premarket_universe_sha256(
                bundle.universe_key,
                bundle.symbols,
            )
            != bundle.universe_sha256
        ):
            return False
        semantic = _bundle_semantic(
            run=run,
            attempt_count=bundle.attempt_count,
            schema_version=bundle.schema_version,
            adapter_key=bundle.adapter_key,
            fetch_started_at=bundle.fetch_started_at,
            fetched_at=bundle.fetched_at,
            expires_at=bundle.expires_at,
            quality_state=bundle.quality_state,
            coverage=bundle.coverage,
            payload_sha256=bundle.payload_sha256,
        )
        return _bundle_key(semantic) == bundle.bundle_key
    except (TypeError, ValueError):
        return False


def select_formal_premarket_artifact_bundle(
    market_date_et: date,
    previous_session: date,
    *,
    as_of: datetime,
    policy_version: str,
    universe_key: str,
    universe_sha256: str,
    schema_version: str,
    adapter_key: Optional[str] = None,
    expected_payload_sha256: Optional[str] = None,
    max_age_seconds: int = 5 * 60,
    allowed_quality_states: Sequence[str] = ("ready",),
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[StoredPremarketArtifactBundle]:
    """Select only coherent, causally available, fresh formal evidence.

    The selector is deliberately fail closed.  Corrupt JSON/hashes, a
    mismatched succeeded run, evidence unavailable by the consumer's frozen
    ``as_of``, a stale target/expiry, or any policy/date/session/universe
    mismatch results in ``None``.
    """

    market_date_et = _calendar_date(market_date_et, "market_date_et")
    previous_session = _calendar_date(previous_session, "previous_session")
    target = _utc(as_of, "as_of")
    policy = _required_text(policy_version, "policy_version")
    universe = _required_text(universe_key, "universe_key")
    universe_digest = _sha256_text(
        universe_sha256,
        "universe_sha256",
    )
    schema = _required_text(schema_version, "schema_version")
    if schema != PREMARKET_ARTIFACT_SCHEMA_VERSION:
        return None
    adapter = _optional_text(adapter_key)
    expected_payload = (
        _sha256_text(expected_payload_sha256, "expected_payload_sha256")
        if expected_payload_sha256 is not None
        else None
    )
    max_age = int(max_age_seconds)
    if max_age <= 0:
        raise PremarketEvidenceRepositoryError(
            "max_age_seconds must be positive"
        )
    quality_states = {
        str(value or "").strip().lower()
        for value in allowed_quality_states
        if str(value or "").strip()
    }
    if not quality_states or not quality_states.issubset(_QUALITY_STATES):
        raise PremarketEvidenceRepositoryError(
            "allowed_quality_states contains an unsupported value"
        )
    freshness_floor = target - timedelta(seconds=max_age)
    db = db_manager or get_db()
    if not db._is_sqlite_engine:
        return None
    init_premarket_evidence_schema(db)

    with db.session_scope() as session:
        rows = session.execute(
            select(
                RegimePremarketArtifactBundle,
                RegimePremarketArtifactIngestion,
                RegimePremarketPrefetchRun,
            )
            .join(
                RegimePremarketArtifactIngestion,
                RegimePremarketArtifactIngestion.bundle_key
                == RegimePremarketArtifactBundle.bundle_key,
            )
            .join(
                RegimePremarketPrefetchRun,
                RegimePremarketPrefetchRun.slot_key
                == RegimePremarketArtifactBundle.slot_key,
            )
            .where(
                RegimePremarketArtifactBundle.market_date_et
                == market_date_et,
                RegimePremarketArtifactBundle.previous_session
                == previous_session,
                RegimePremarketArtifactBundle.policy_version == policy,
                RegimePremarketArtifactBundle.universe_key == universe,
                RegimePremarketArtifactBundle.universe_sha256
                == universe_digest,
                RegimePremarketArtifactBundle.schema_version == schema,
                RegimePremarketArtifactIngestion.ingested_at <= target,
                RegimePremarketPrefetchRun.state == "succeeded",
            )
            .order_by(
                RegimePremarketArtifactBundle.fetched_at.desc(),
                RegimePremarketArtifactBundle.id.desc(),
            )
        ).all()
        for artifact_row, ingestion_row, run_row in rows:
            try:
                bundle = _stored_bundle(
                    artifact_row,
                    ingested_at=ingestion_row.ingested_at,
                )
                run = _stored_run(run_row)
                validated_payload = PremarketArtifactBundle.from_payload(
                    bundle.payload
                )
            except PremarketEvidenceRepositoryError:
                continue
            except PremarketArtifactValidationError:
                continue
            if not _artifact_evidence_available_by(
                validated_payload,
                bundle.fetched_at,
            ):
                continue
            if any(
                item.evidence_as_of is not None
                and item.evidence_as_of < freshness_floor
                for item in validated_payload.observations
            ):
                continue
            if adapter is not None and bundle.adapter_key != adapter:
                continue
            if bundle.quality_state not in quality_states:
                continue
            if (
                bundle.target_as_of > target
                or run.target_as_of != bundle.target_as_of
            ):
                continue
            if (
                bundle.fetched_at > target
                or bundle.ingested_at is None
                or (
                    bundle.ingested_at + _DATABASE_TIMESTAMP_RESOLUTION
                    > target
                )
            ):
                continue
            if _receipt_causal_order_error(bundle, run) is not None:
                continue
            if bundle.target_as_of < freshness_floor:
                continue
            if bundle.expires_at < target:
                continue
            if (
                run.market_date_et != market_date_et
                or run.previous_session != previous_session
                or run.policy_version != policy
                or run.universe_key != universe
                or run.universe_sha256 != universe_digest
                or run.bundle_key != bundle.bundle_key
                or run.attempt_count != bundle.attempt_count
            ):
                continue
            if not _artifact_matches_run_contract(
                validated_payload,
                run,
                quality_state=bundle.quality_state,
                coverage=bundle.coverage,
            ):
                continue
            if (
                expected_payload is not None
                and bundle.payload_sha256 != expected_payload
            ):
                continue
            if not _bundle_integrity_matches(bundle, run):
                continue
            return bundle
    return None
