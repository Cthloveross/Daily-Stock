# -*- coding: utf-8 -*-
"""Append-only repository for premarket universe versions and cycle attempts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.opportunities.cycle_models import (
    OpportunityPremarketCycleAttempt,
    OpportunityPremarketCycleEvent,
    OpportunityPremarketCycleSlot,
    OpportunityResearchUniverseVersion,
)
from src.opportunities.engine import (
    is_supported_us_option_underlying,
    normalize_symbols,
)
from src.opportunities.models import OpportunitySnapshotRun
from src.opportunities.repository import (
    SnapshotAppendResult,
    StoredSnapshot,
    canonical_json,
    canonical_sha256,
    get_snapshot,
    init_opportunity_schema,
)
from src.storage import Base, DatabaseManager, get_db


_APPEND_ONLY_TABLES = (
    OpportunityResearchUniverseVersion.__tablename__,
    OpportunityPremarketCycleSlot.__tablename__,
    OpportunityPremarketCycleAttempt.__tablename__,
    OpportunityPremarketCycleEvent.__tablename__,
)
_TERMINAL_STATES = {
    "published",
    "blocked",
    "failed",
    "window_closed",
}
_MAX_SYMBOLS = 20
_MIN_ATTEMPT_LEASE_SECONDS = 90
_DEFAULT_ATTEMPT_LEASE_SECONDS = 120


class PremarketCycleRepositoryError(ValueError):
    """Base error for invalid or conflicting orchestration facts."""


class PremarketAttemptLeaseLostError(PremarketCycleRepositoryError):
    """Raised when a stale attempt owner tries to append or publish."""


class PremarketPublicationDeadlineError(PremarketCycleRepositoryError):
    """Raised when the lock-protected publication check reaches its deadline."""


class PremarketPublicationContractError(PremarketCycleRepositoryError):
    """Raised when a publication factory violates canonical provenance."""


SnapshotPublicationFactory = Callable[
    [Session, datetime],
    SnapshotAppendResult,
]
PublicationAuditFactory = Callable[
    [Session, StoredSnapshot, "CycleAttemptStatus"],
    None,
]


@dataclass(frozen=True)
class StoredResearchUniverse:
    universe_version_key: str
    scope_key: str
    symbols: tuple[str, ...]
    requested_limit: int
    source: str
    created_at: datetime


@dataclass(frozen=True)
class StoredCycleAttempt:
    attempt_key: str
    cycle_key: str
    market_date_et: date
    scope_key: str
    freeze_policy_version: str
    cycle_version: str
    universe_version_key: Optional[str]
    universe_source: str
    universe: tuple[str, ...]
    requested_limit: int
    trigger: str
    owner_key: str
    recovered_from_attempt_key: Optional[str]
    hard_deadline_at: datetime
    started_at: datetime


@dataclass(frozen=True)
class StoredCycleEvent:
    sequence: int
    event_type: str
    stage: Optional[str]
    state: str
    observed_at: datetime
    lease_expires_at: Optional[datetime]
    actor_key: str
    error_code: Optional[str]
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class CycleAttemptStatus:
    attempt: StoredCycleAttempt
    state: str
    stages: tuple[Mapping[str, Any], ...]
    events: tuple[StoredCycleEvent, ...]
    lease_expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_code: Optional[str]
    recoverable: bool


@dataclass(frozen=True)
class CycleClaimResult:
    claimed: bool
    attempt: StoredCycleAttempt
    status: CycleAttemptStatus


@dataclass(frozen=True)
class PremarketPublicationResult:
    snapshot: SnapshotAppendResult
    status: CycleAttemptStatus
    published_at: datetime


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise PremarketCycleRepositoryError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PremarketCycleRepositoryError(
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
    normalized = tuple(normalize_symbols(values))
    if not normalized:
        raise PremarketCycleRepositoryError("universe must not be empty")
    if len(normalized) > _MAX_SYMBOLS:
        raise PremarketCycleRepositoryError(
            f"universe must contain at most {_MAX_SYMBOLS} symbols"
        )
    unsupported = tuple(
        symbol
        for symbol in normalized
        if not is_supported_us_option_underlying(symbol)
    )
    if unsupported:
        raise PremarketCycleRepositoryError(
            "universe contains unsupported US option underlyings: "
            + ", ".join(unsupported)
        )
    return normalized


def _limit(value: int) -> int:
    normalized = int(value)
    if not 1 <= normalized <= 15:
        raise PremarketCycleRepositoryError(
            "requested_limit must be between 1 and 15"
        )
    return normalized


def init_premarket_cycle_schema(
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    db = db_manager or get_db()
    tables = (
        OpportunityResearchUniverseVersion.__table__,
        OpportunityPremarketCycleSlot.__table__,
        OpportunityPremarketCycleAttempt.__table__,
        OpportunityPremarketCycleEvent.__table__,
    )
    Base.metadata.create_all(db._engine, tables=tables)
    if db._engine.dialect.name == "sqlite":
        with db._engine.begin() as connection:
            for table_name in _APPEND_ONLY_TABLES:
                for operation in ("UPDATE", "DELETE"):
                    connection.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS "
                        f"trg_{table_name}_{operation.lower()}_immutable "
                        f"BEFORE {operation} ON {table_name} "
                        "BEGIN SELECT RAISE(ABORT, "
                        "'premarket cycle rows are append-only'); END"
                    )


def _stored_universe(row: OpportunityResearchUniverseVersion) -> StoredResearchUniverse:
    return StoredResearchUniverse(
        universe_version_key=str(row.universe_version_key),
        scope_key=str(row.scope_key),
        symbols=tuple(json.loads(row.symbols_json)),
        requested_limit=int(row.requested_limit),
        source=str(row.source),
        created_at=_db_utc(row.created_at),
    )


def append_research_universe(
    symbols: Sequence[str],
    requested_limit: int,
    *,
    scope_key: str,
    source: str = "api",
    created_at: Optional[datetime] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> tuple[StoredResearchUniverse, bool]:
    """Append a universe revision, or return the identical latest version."""

    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    normalized = _symbols(symbols)
    limit = _limit(requested_limit)
    scope = str(scope_key or "").strip()
    normalized_source = str(source or "").strip()
    if not scope or not normalized_source:
        raise PremarketCycleRepositoryError("scope_key and source are required")
    observed_at = _utc(
        created_at or datetime.now(timezone.utc),
        "created_at",
    )
    symbols_json = canonical_json(list(normalized))
    symbols_sha256 = hashlib.sha256(symbols_json.encode("utf-8")).hexdigest()

    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        latest = session.execute(
            select(OpportunityResearchUniverseVersion)
            .where(OpportunityResearchUniverseVersion.scope_key == scope)
            .order_by(OpportunityResearchUniverseVersion.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if (
            latest is not None
            and str(latest.symbols_sha256) == symbols_sha256
            and int(latest.requested_limit) == limit
        ):
            return _stored_universe(latest), True
        version_key = "opu_" + canonical_sha256(
            {
                "scope_key": scope,
                "symbols": list(normalized),
                "requested_limit": limit,
                "created_at": observed_at,
                "previous_version_key": (
                    latest.universe_version_key if latest is not None else None
                ),
            }
        )
        row = OpportunityResearchUniverseVersion(
            universe_version_key=version_key,
            scope_key=scope,
            symbols_sha256=symbols_sha256,
            symbols_json=symbols_json,
            requested_limit=limit,
            source=normalized_source,
            created_at=observed_at,
        )
        session.add(row)
        session.flush()
        return _stored_universe(row), False


def get_latest_research_universe(
    *,
    scope_key: str,
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[StoredResearchUniverse]:
    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    with db.get_session() as session:
        row = session.execute(
            select(OpportunityResearchUniverseVersion)
            .where(OpportunityResearchUniverseVersion.scope_key == scope_key)
            .order_by(OpportunityResearchUniverseVersion.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return None if row is None else _stored_universe(row)


def _stored_attempt(row: OpportunityPremarketCycleAttempt) -> StoredCycleAttempt:
    return StoredCycleAttempt(
        attempt_key=str(row.attempt_key),
        cycle_key=str(row.cycle_key),
        market_date_et=row.market_date_et,
        scope_key=str(row.scope_key),
        freeze_policy_version=str(row.freeze_policy_version),
        cycle_version=str(row.cycle_version),
        universe_version_key=row.universe_version_key,
        universe_source=str(row.universe_source),
        universe=tuple(json.loads(row.universe_json)),
        requested_limit=int(row.requested_limit),
        trigger=str(row.trigger),
        owner_key=str(row.owner_key),
        recovered_from_attempt_key=row.recovered_from_attempt_key,
        hard_deadline_at=_db_utc(row.hard_deadline_at),
        started_at=_db_utc(row.started_at),
    )


def _stored_event(row: OpportunityPremarketCycleEvent) -> StoredCycleEvent:
    return StoredCycleEvent(
        sequence=int(row.sequence),
        event_type=str(row.event_type),
        stage=row.stage,
        state=str(row.state),
        observed_at=_db_utc(row.observed_at),
        lease_expires_at=_db_utc(row.lease_expires_at),
        actor_key=str(row.actor_key),
        error_code=row.error_code,
        payload=json.loads(row.payload_json or "{}"),
    )


def _events_for(session, attempt_id: int) -> tuple[StoredCycleEvent, ...]:
    rows = session.execute(
        select(OpportunityPremarketCycleEvent)
        .where(OpportunityPremarketCycleEvent.attempt_id == attempt_id)
        .order_by(OpportunityPremarketCycleEvent.sequence)
    ).scalars()
    return tuple(_stored_event(row) for row in rows)


def _status(
    attempt: StoredCycleAttempt,
    events: tuple[StoredCycleEvent, ...],
    *,
    now: datetime,
) -> CycleAttemptStatus:
    latest = events[-1]
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.event_type == "attempt_finished"
        ),
        None,
    )
    lease_expires_at = latest.lease_expires_at
    if terminal is not None:
        state = terminal.state
        error_code = terminal.error_code
        completed_at = terminal.observed_at
        recoverable = state in {"blocked", "failed", "window_closed"}
    elif lease_expires_at is not None and lease_expires_at <= now:
        state = "failed"
        error_code = "attempt_lease_expired"
        completed_at = lease_expires_at
        recoverable = True
    else:
        state = "running"
        error_code = None
        completed_at = None
        recoverable = False

    stage_rows: dict[str, dict[str, Any]] = {}
    stage_order: list[str] = []
    for event in events:
        if not event.stage:
            continue
        if event.stage not in stage_rows:
            stage_order.append(event.stage)
            stage_rows[event.stage] = {
                "name": event.stage,
                "state": event.state,
                "started_at": event.observed_at.isoformat(),
                "completed_at": None,
                "error_code": event.error_code,
            }
        stage = stage_rows[event.stage]
        stage["state"] = event.state
        stage["error_code"] = event.error_code
        if event.event_type == "stage_finished":
            stage["completed_at"] = event.observed_at.isoformat()

    return CycleAttemptStatus(
        attempt=attempt,
        state=state,
        stages=tuple(stage_rows[name] for name in stage_order),
        events=events,
        lease_expires_at=lease_expires_at,
        completed_at=completed_at,
        error_code=error_code,
        recoverable=recoverable,
    )


def get_latest_cycle_attempt_status(
    cycle_key: str,
    *,
    now: Optional[datetime] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[CycleAttemptStatus]:
    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    with db.get_session() as session:
        row = session.execute(
            select(OpportunityPremarketCycleAttempt)
            .where(OpportunityPremarketCycleAttempt.cycle_key == cycle_key)
            .order_by(OpportunityPremarketCycleAttempt.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        attempt = _stored_attempt(row)
        return _status(attempt, _events_for(session, int(row.id)), now=observed_at)


def count_cycle_attempts(
    cycle_key: str,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> int:
    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    with db.get_session() as session:
        return int(
            session.execute(
                select(func.count(OpportunityPremarketCycleAttempt.id)).where(
                    OpportunityPremarketCycleAttempt.cycle_key == cycle_key
                )
            ).scalar_one()
        )


def _event_row(
    *,
    attempt_id: int,
    attempt_key: str,
    sequence: int,
    event_type: str,
    stage: Optional[str],
    state: str,
    observed_at: datetime,
    lease_expires_at: Optional[datetime],
    actor_key: str,
    error_code: Optional[str],
    payload: Mapping[str, Any],
) -> OpportunityPremarketCycleEvent:
    payload_json = canonical_json(dict(payload))
    event_key = "ope_" + canonical_sha256(
        {
            "attempt_key": attempt_key,
            "sequence": sequence,
            "event_type": event_type,
            "stage": stage,
            "state": state,
            "observed_at": observed_at,
        }
    )
    return OpportunityPremarketCycleEvent(
        event_key=event_key,
        attempt_id=attempt_id,
        sequence=sequence,
        event_type=event_type,
        stage=stage,
        state=state,
        observed_at=observed_at,
        lease_expires_at=lease_expires_at,
        actor_key=actor_key,
        error_code=error_code,
        payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        payload_json=payload_json,
    )


def _lock_cycle_slot(
    session,
    *,
    db: DatabaseManager,
    cycle_key: str,
    market_date_et: date,
    scope_key: str,
    freeze_policy_version: str,
    cycle_version: str,
    created_at: datetime,
) -> OpportunityPremarketCycleSlot:
    """Create-or-read the cycle lock target, then hold it through the claim."""

    slot_values = {
        "cycle_key": cycle_key,
        "market_date_et": market_date_et,
        "scope_key": scope_key,
        "freeze_policy_version": freeze_policy_version,
        "cycle_version": cycle_version,
        "created_at": created_at,
    }
    if db._is_sqlite_engine:
        # BEGIN IMMEDIATE is acquired by the caller before this read.
        slot = session.execute(
            select(OpportunityPremarketCycleSlot).where(
                OpportunityPremarketCycleSlot.cycle_key == cycle_key
            )
        ).scalar_one_or_none()
        if slot is None:
            slot = OpportunityPremarketCycleSlot(**slot_values)
            session.add(slot)
            session.flush()
    else:
        # SELECT FOR UPDATE cannot lock an absent row.  A unique insert inside
        # a savepoint first materializes the lock target.  A racing insert
        # blocks/fails on the unique key, rolls back only the savepoint, and
        # then re-reads the winner's row in this transaction.
        try:
            with session.begin_nested():
                session.add(OpportunityPremarketCycleSlot(**slot_values))
                session.flush()
        except IntegrityError:
            pass
        query = select(OpportunityPremarketCycleSlot).where(
            OpportunityPremarketCycleSlot.cycle_key == cycle_key
        )
        slot = session.execute(query.with_for_update()).scalar_one()

    expected = (
        market_date_et,
        scope_key,
        freeze_policy_version,
        cycle_version,
    )
    actual = (
        slot.market_date_et,
        str(slot.scope_key),
        str(slot.freeze_policy_version),
        str(slot.cycle_version),
    )
    if actual != expected:
        raise PremarketCycleRepositoryError(
            "cycle_key is already bound to different immutable metadata"
        )
    return slot


def _lock_existing_cycle_slot(
    session,
    *,
    db: DatabaseManager,
    cycle_key: str,
) -> OpportunityPremarketCycleSlot:
    """Hold the common cycle writer lock before attempt/event decisions."""

    query = select(OpportunityPremarketCycleSlot).where(
        OpportunityPremarketCycleSlot.cycle_key == cycle_key
    )
    if not db._is_sqlite_engine:
        query = query.with_for_update()
    slot = session.execute(query).scalar_one_or_none()
    if slot is None:
        raise PremarketCycleRepositoryError("cycle slot does not exist")
    return slot


def claim_cycle_attempt(
    *,
    cycle_key: str,
    market_date_et: date,
    scope_key: str,
    freeze_policy_version: str,
    cycle_version: str,
    universe: Sequence[str],
    requested_limit: int,
    universe_source: str,
    universe_version_key: Optional[str],
    trigger: str,
    owner_key: str,
    hard_deadline_at: datetime,
    now: Optional[datetime] = None,
    lease_seconds: int = _DEFAULT_ATTEMPT_LEASE_SECONDS,
    max_attempts: int = 2,
    db_manager: Optional[DatabaseManager] = None,
) -> CycleClaimResult:
    """Atomically claim a cycle or return the still-live current attempt."""

    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    deadline = _utc(hard_deadline_at, "hard_deadline_at")
    normalized_universe = _symbols(universe)
    limit = _limit(requested_limit)
    if observed_at >= deadline:
        raise PremarketCycleRepositoryError("hard deadline has passed")
    lease_expires = min(
        observed_at
        + timedelta(
            seconds=max(
                _MIN_ATTEMPT_LEASE_SECONDS,
                int(lease_seconds),
            )
        ),
        deadline,
    )

    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _lock_cycle_slot(
            session,
            db=db,
            cycle_key=cycle_key,
            market_date_et=market_date_et,
            scope_key=scope_key,
            freeze_policy_version=freeze_policy_version,
            cycle_version=cycle_version,
            created_at=observed_at,
        )
        query = (
            select(OpportunityPremarketCycleAttempt)
            .where(OpportunityPremarketCycleAttempt.cycle_key == cycle_key)
            .order_by(OpportunityPremarketCycleAttempt.id.desc())
            .limit(1)
        )
        if not db._is_sqlite_engine:
            query = query.with_for_update()
        latest_row = session.execute(query).scalar_one_or_none()
        recovered_from = None
        if latest_row is not None:
            latest_attempt = _stored_attempt(latest_row)
            latest_events = _events_for(session, int(latest_row.id))
            latest_status = _status(
                latest_attempt,
                latest_events,
                now=observed_at,
            )
            if latest_status.state == "published":
                return CycleClaimResult(False, latest_attempt, latest_status)
            if latest_status.state == "running":
                return CycleClaimResult(False, latest_attempt, latest_status)
            attempt_count = len(
                session.execute(
                    select(OpportunityPremarketCycleAttempt.id).where(
                        OpportunityPremarketCycleAttempt.cycle_key == cycle_key
                    )
                ).scalars().all()
            )
            if attempt_count >= max(1, int(max_attempts)):
                return CycleClaimResult(False, latest_attempt, latest_status)
            recovered_from = latest_attempt.attempt_key
            if (
                latest_events[-1].event_type != "attempt_finished"
                and latest_status.error_code == "attempt_lease_expired"
            ):
                sequence = latest_events[-1].sequence + 1
                session.add(
                    _event_row(
                        attempt_id=int(latest_row.id),
                        attempt_key=latest_attempt.attempt_key,
                        sequence=sequence,
                        event_type="attempt_finished",
                        stage=None,
                        state="failed",
                        observed_at=observed_at,
                        lease_expires_at=None,
                        actor_key=owner_key,
                        error_code="attempt_lease_expired",
                        payload={"recovered_by": owner_key},
                    )
                )
            normalized_universe = latest_attempt.universe
            limit = latest_attempt.requested_limit
            universe_source = latest_attempt.universe_source
            universe_version_key = latest_attempt.universe_version_key

        universe_json = canonical_json(list(normalized_universe))
        input_payload = {
            "cycle_key": cycle_key,
            "market_date_et": market_date_et,
            "universe": list(normalized_universe),
            "requested_limit": limit,
            "universe_source": universe_source,
            "universe_version_key": universe_version_key,
            "trigger": trigger,
        }
        input_json = canonical_json(input_payload)
        attempt_key = "opa_" + canonical_sha256(
            {
                **input_payload,
                "owner_key": owner_key,
                "started_at": observed_at,
                "recovered_from": recovered_from,
            }
        )
        row = OpportunityPremarketCycleAttempt(
            attempt_key=attempt_key,
            cycle_key=cycle_key,
            market_date_et=market_date_et,
            scope_key=scope_key,
            freeze_policy_version=freeze_policy_version,
            cycle_version=cycle_version,
            universe_version_key=universe_version_key,
            universe_source=universe_source,
            universe_sha256=hashlib.sha256(
                universe_json.encode("utf-8")
            ).hexdigest(),
            universe_json=universe_json,
            requested_limit=limit,
            trigger=str(trigger or "").strip(),
            owner_key=str(owner_key or "").strip(),
            recovered_from_attempt_key=recovered_from,
            hard_deadline_at=deadline,
            started_at=observed_at,
            input_sha256=hashlib.sha256(input_json.encode("utf-8")).hexdigest(),
            input_json=input_json,
        )
        if not row.trigger or not row.owner_key:
            raise PremarketCycleRepositoryError(
                "trigger and owner_key are required"
            )
        session.add(row)
        session.flush()
        session.add(
            _event_row(
                attempt_id=int(row.id),
                attempt_key=attempt_key,
                sequence=1,
                event_type="attempt_started",
                stage=None,
                state="running",
                observed_at=observed_at,
                lease_expires_at=lease_expires,
                actor_key=owner_key,
                error_code=None,
                payload={},
            )
        )
        session.flush()
        attempt = _stored_attempt(row)
        events = _events_for(session, int(row.id))
        return CycleClaimResult(
            True,
            attempt,
            _status(attempt, events, now=observed_at),
        )


def append_cycle_event(
    attempt_key: str,
    *,
    owner_key: str,
    event_type: str,
    state: str,
    stage: Optional[str] = None,
    error_code: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
    lease_seconds: int = _DEFAULT_ATTEMPT_LEASE_SECONDS,
    db_manager: Optional[DatabaseManager] = None,
) -> CycleAttemptStatus:
    """Append an owner event only while this attempt still holds the lease."""

    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        cycle_key_value = session.execute(
            select(OpportunityPremarketCycleAttempt.cycle_key).where(
                OpportunityPremarketCycleAttempt.attempt_key == attempt_key
            )
        ).scalar_one_or_none()
        if cycle_key_value is None:
            raise PremarketCycleRepositoryError("attempt does not exist")
        _lock_existing_cycle_slot(
            session,
            db=db,
            cycle_key=str(cycle_key_value),
        )
        row_query = select(OpportunityPremarketCycleAttempt).where(
            OpportunityPremarketCycleAttempt.attempt_key == attempt_key
        )
        if not db._is_sqlite_engine:
            row_query = row_query.with_for_update()
        row = session.execute(row_query).scalar_one()
        attempt = _stored_attempt(row)
        latest_attempt_row = session.execute(
            select(OpportunityPremarketCycleAttempt)
            .where(
                OpportunityPremarketCycleAttempt.cycle_key
                == attempt.cycle_key
            )
            .order_by(OpportunityPremarketCycleAttempt.id.desc())
            .limit(1)
        ).scalar_one()
        if int(latest_attempt_row.id) != int(row.id) or attempt.owner_key != owner_key:
            raise PremarketAttemptLeaseLostError(
                "attempt is no longer the current owner"
            )
        events = _events_for(session, int(row.id))
        latest = events[-1]
        if latest.event_type == "attempt_finished":
            raise PremarketAttemptLeaseLostError("attempt is already terminal")
        terminal = event_type == "attempt_finished"
        allow_late_failure_audit = terminal and state != "published"
        if (
            latest.lease_expires_at is None
            or latest.lease_expires_at <= observed_at
        ) and not allow_late_failure_audit:
            raise PremarketAttemptLeaseLostError("attempt lease has expired")
        lease_expires = (
            None
            if terminal
            else min(
                observed_at
                + timedelta(
                    seconds=max(
                        _MIN_ATTEMPT_LEASE_SECONDS,
                        int(lease_seconds),
                    )
                ),
                attempt.hard_deadline_at,
            )
        )
        if not terminal and lease_expires <= observed_at:
            raise PremarketAttemptLeaseLostError(
                "attempt hard deadline has passed"
            )
        session.add(
            _event_row(
                attempt_id=int(row.id),
                attempt_key=attempt.attempt_key,
                sequence=latest.sequence + 1,
                event_type=event_type,
                stage=stage,
                state=state,
                observed_at=observed_at,
                lease_expires_at=lease_expires,
                actor_key=owner_key,
                error_code=error_code,
                payload=dict(payload or {}),
            )
        )
        session.flush()
        return _status(
            attempt,
            _events_for(session, int(row.id)),
            now=observed_at,
        )


def publish_cycle_snapshot_atomically(
    cycle_key: str,
    attempt_key: str,
    *,
    owner_key: str,
    snapshot_factory: SnapshotPublicationFactory,
    guard_clock: Callable[[], datetime],
    stage_payload: Optional[Mapping[str, Any]] = None,
    terminal_payload: Optional[Mapping[str, Any]] = None,
    publication_audit_factory: Optional[PublicationAuditFactory] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> PremarketPublicationResult:
    """Commit the canonical snapshot and published audit as one transaction.

    The guard clock is called only after the cycle-wide writer lock is held.
    ``snapshot_factory`` then receives that authoritative publication time and
    the transaction-owned session.  It must append the snapshot through that
    session and return its :class:`SnapshotAppendResult`; it must not commit.
    Any deadline, ownership, lease, snapshot, provenance, stage, terminal,
    optional local publication-audit, or commit failure rolls back all writes
    made by this call. The optional audit callback must only derive and append
    local facts from the supplied immutable snapshot/status; provider access
    is outside this transaction contract.
    """

    if not callable(guard_clock):
        raise PremarketCycleRepositoryError("guard_clock must be callable")
    if not callable(snapshot_factory):
        raise PremarketCycleRepositoryError(
            "snapshot_factory must be callable"
        )
    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    init_opportunity_schema(db)

    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _lock_existing_cycle_slot(
            session,
            db=db,
            cycle_key=cycle_key,
        )

        # This must happen after the writer/slot lock.  A caller that waited
        # behind another transaction therefore re-observes the real deadline.
        published_at = _utc(guard_clock(), "guard_clock")

        attempt_query = select(OpportunityPremarketCycleAttempt).where(
            OpportunityPremarketCycleAttempt.attempt_key == attempt_key
        )
        if not db._is_sqlite_engine:
            attempt_query = attempt_query.with_for_update()
        row = session.execute(attempt_query).scalar_one_or_none()
        if row is None:
            raise PremarketCycleRepositoryError("attempt does not exist")
        attempt = _stored_attempt(row)
        if attempt.cycle_key != cycle_key:
            raise PremarketAttemptLeaseLostError(
                "attempt does not belong to the locked cycle"
            )
        if published_at >= attempt.hard_deadline_at:
            raise PremarketPublicationDeadlineError(
                "canonical publication reached its hard deadline"
            )

        latest_row = session.execute(
            select(OpportunityPremarketCycleAttempt)
            .where(OpportunityPremarketCycleAttempt.cycle_key == cycle_key)
            .order_by(OpportunityPremarketCycleAttempt.id.desc())
            .limit(1)
        ).scalar_one()
        if (
            int(latest_row.id) != int(row.id)
            or attempt.owner_key != owner_key
        ):
            raise PremarketAttemptLeaseLostError(
                "attempt is no longer the current owner"
            )
        events = _events_for(session, int(row.id))
        latest_event = events[-1]
        if latest_event.event_type == "attempt_finished":
            raise PremarketAttemptLeaseLostError(
                "attempt is already terminal"
            )
        if (
            latest_event.lease_expires_at is None
            or latest_event.lease_expires_at <= published_at
        ):
            raise PremarketAttemptLeaseLostError(
                "attempt lease has expired"
            )

        snapshot = snapshot_factory(session, published_at)
        if not isinstance(snapshot, SnapshotAppendResult):
            raise PremarketPublicationContractError(
                "snapshot_factory must return SnapshotAppendResult"
            )
        stored_snapshot = get_snapshot(
            snapshot.snapshot_key,
            db_manager=db,
            session=session,
        )
        if stored_snapshot is None:
            raise PremarketPublicationContractError(
                "snapshot_factory returned without appending a snapshot"
            )
        if (
            stored_snapshot.id != snapshot.snapshot_run_id
            or len(stored_snapshot.candidates) != snapshot.candidate_count
            or stored_snapshot.payload_sha256 != snapshot.payload_sha256
        ):
            raise PremarketPublicationContractError(
                "snapshot_factory result does not match the persisted bundle"
            )
        if (
            stored_snapshot.market_date_et != attempt.market_date_et
            or stored_snapshot.scope_key != attempt.scope_key
            or stored_snapshot.freeze_policy_version
            != attempt.freeze_policy_version
            or tuple(
                json.loads(
                    session.execute(
                        select(
                            OpportunitySnapshotRun.universe_json
                        ).where(
                            OpportunitySnapshotRun.id
                            == stored_snapshot.id
                        )
                    ).scalar_one()
                )
            )
            # The pool version preserves user order for display and ranking
            # input, while snapshot persistence canonicalizes the same scope.
            # Publication therefore compares canonical membership, not
            # presentation order.
            != tuple(sorted(attempt.universe))
            or stored_snapshot.requested_limit
            != attempt.requested_limit
        ):
            raise PremarketPublicationContractError(
                "snapshot does not match the immutable attempt scope"
            )
        expected_timestamp = published_at.isoformat()
        canonical_cycle = (
            stored_snapshot.payload.get("canonical_cycle") or {}
        )
        snapshot_meta = (
            stored_snapshot.payload.get("snapshot_meta") or {}
        )
        candidate_timestamps = tuple(
            (
                candidate.payload.get("outcome_validation") or {}
            ).get("frozen_at")
            for candidate in stored_snapshot.candidates
        )
        if (
            _db_utc(stored_snapshot.frozen_at) != published_at
            or canonical_cycle.get("published_at")
            != expected_timestamp
            or snapshot_meta.get("frozen_at") != expected_timestamp
            or any(
                value != expected_timestamp
                for value in candidate_timestamps
            )
        ):
            raise PremarketPublicationContractError(
                "snapshot provenance must use the lock-protected "
                "publication timestamp"
            )
        if (
            canonical_cycle.get("cycle_key") != cycle_key
            or canonical_cycle.get("attempt_key") != attempt_key
        ):
            raise PremarketPublicationContractError(
                "snapshot canonical_cycle does not identify this attempt"
            )

        # Snapshot preparation and database flushing may themselves consume
        # the final seconds of the publication window. Re-observe time while
        # still holding the cycle writer lock and fail closed before either
        # audit event is appended. The first guard remains the immutable
        # provenance timestamp; this second guard protects commit eligibility.
        precommit_at = _utc(guard_clock(), "guard_clock")
        if precommit_at >= attempt.hard_deadline_at:
            raise PremarketPublicationDeadlineError(
                "canonical publication reached its hard deadline "
                "before commit"
            )
        if (
            latest_event.lease_expires_at is None
            or latest_event.lease_expires_at <= precommit_at
        ):
            raise PremarketAttemptLeaseLostError(
                "attempt lease expired before publication commit"
            )

        snapshot_facts = {"snapshot_key": snapshot.snapshot_key}
        completed_payload = {
            **dict(stage_payload or {}),
            **snapshot_facts,
        }
        published_payload = {
            **dict(terminal_payload or {}),
            **snapshot_facts,
        }
        completed_event = _event_row(
            attempt_id=int(row.id),
            attempt_key=attempt.attempt_key,
            sequence=latest_event.sequence + 1,
            event_type="stage_finished",
            stage="persist_snapshot",
            state="completed",
            observed_at=precommit_at,
            lease_expires_at=min(
                precommit_at
                + timedelta(seconds=_DEFAULT_ATTEMPT_LEASE_SECONDS),
                attempt.hard_deadline_at,
            ),
            actor_key=owner_key,
            error_code=None,
            payload=completed_payload,
        )
        session.add(completed_event)
        session.flush()
        session.add(
            _event_row(
                attempt_id=int(row.id),
                attempt_key=attempt.attempt_key,
                sequence=latest_event.sequence + 2,
                event_type="attempt_finished",
                stage=None,
                state="published",
                observed_at=precommit_at,
                lease_expires_at=None,
                actor_key=owner_key,
                error_code=None,
                payload=published_payload,
            )
        )
        session.flush()
        status = _status(
            attempt,
            _events_for(session, int(row.id)),
            now=precommit_at,
        )
        if publication_audit_factory is not None:
            publication_audit_factory(
                session,
                stored_snapshot,
                status,
            )
        return PremarketPublicationResult(
            snapshot=snapshot,
            status=status,
            published_at=published_at,
        )


def assert_cycle_attempt_owner(
    attempt_key: str,
    *,
    owner_key: str,
    now: Optional[datetime] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    """Fail unless the attempt is latest, non-terminal, and lease-live."""

    status = get_latest_cycle_attempt_status(
        cycle_key=_attempt_cycle_key(
            attempt_key,
            db_manager=db_manager,
        ),
        now=now,
        db_manager=db_manager,
    )
    if (
        status is None
        or status.attempt.attempt_key != attempt_key
        or status.attempt.owner_key != owner_key
        or status.state != "running"
    ):
        raise PremarketAttemptLeaseLostError(
            "attempt is no longer the current lease owner"
        )


def _attempt_cycle_key(
    attempt_key: str,
    *,
    db_manager: Optional[DatabaseManager],
) -> str:
    db = db_manager or get_db()
    init_premarket_cycle_schema(db)
    with db.get_session() as session:
        value = session.execute(
            select(OpportunityPremarketCycleAttempt.cycle_key).where(
                OpportunityPremarketCycleAttempt.attempt_key == attempt_key
            )
        ).scalar_one_or_none()
        if value is None:
            raise PremarketCycleRepositoryError("attempt does not exist")
        return str(value)
