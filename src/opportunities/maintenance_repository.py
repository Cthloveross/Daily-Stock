# -*- coding: utf-8 -*-
"""DB lease and status repository for outcome-maintenance scheduling."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.opportunities.maintenance_models import (
    OpportunityOutcomeMaintenanceRun,
)
from src.opportunities.repository import canonical_json, canonical_sha256
from src.storage import Base, DatabaseManager, get_db

__all__ = [
    "OUTCOME_MAINTENANCE_POLICY_VERSION",
    "OutcomeMaintenanceClaim",
    "OutcomeMaintenanceLeaseError",
    "StoredOutcomeMaintenanceRun",
    "build_outcome_maintenance_key",
    "claim_outcome_maintenance",
    "get_latest_outcome_maintenance",
    "init_outcome_maintenance_schema",
    "settle_outcome_maintenance",
]


OUTCOME_MAINTENANCE_POLICY_VERSION = "xnys-close-qualified-raw-path-v2"
_DEFAULT_LEASE_SECONDS = 45 * 60
_MAX_ATTEMPTS = 2
_TERMINAL_STATE = "completed"


class OutcomeMaintenanceLeaseError(RuntimeError):
    """Raised when a stale scheduler owner tries to settle a lease."""


@dataclass(frozen=True)
class StoredOutcomeMaintenanceRun:
    maintenance_key: str
    session_date_et: date
    policy_version: str
    state: str
    attempt_count: int
    owner_key: Optional[str]
    claimed_at: Optional[datetime]
    lease_expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    next_retry_at: Optional[datetime]
    last_error_code: Optional[str]
    result: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OutcomeMaintenanceClaim:
    claimed: bool
    run: StoredOutcomeMaintenanceRun


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _db_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stored(row: OpportunityOutcomeMaintenanceRun) -> StoredOutcomeMaintenanceRun:
    try:
        result = json.loads(row.result_json) if row.result_json else {}
    except (TypeError, json.JSONDecodeError):
        result = {}
    return StoredOutcomeMaintenanceRun(
        maintenance_key=str(row.maintenance_key),
        session_date_et=row.session_date_et,
        policy_version=str(row.policy_version),
        state=str(row.state),
        attempt_count=int(row.attempt_count),
        owner_key=str(row.owner_key) if row.owner_key else None,
        claimed_at=_db_utc(row.claimed_at),
        lease_expires_at=_db_utc(row.lease_expires_at),
        completed_at=_db_utc(row.completed_at),
        next_retry_at=_db_utc(row.next_retry_at),
        last_error_code=(
            str(row.last_error_code) if row.last_error_code else None
        ),
        result=result if isinstance(result, Mapping) else {},
        created_at=_db_utc(row.created_at),
        updated_at=_db_utc(row.updated_at),
    )


def init_outcome_maintenance_schema(
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    db = db_manager or get_db()
    Base.metadata.create_all(
        db._engine,
        tables=(OpportunityOutcomeMaintenanceRun.__table__,),
    )


def build_outcome_maintenance_key(
    session_date_et: date,
    *,
    policy_version: str = OUTCOME_MAINTENANCE_POLICY_VERSION,
) -> str:
    return "oom_" + canonical_sha256(
        {
            "session_date_et": session_date_et.isoformat(),
            "policy_version": str(policy_version),
        }
    )


def _lock_or_create(
    session,
    *,
    db: DatabaseManager,
    session_date_et: date,
    policy_version: str,
    now: datetime,
) -> OpportunityOutcomeMaintenanceRun:
    maintenance_key = build_outcome_maintenance_key(
        session_date_et,
        policy_version=policy_version,
    )
    values = {
        "maintenance_key": maintenance_key,
        "session_date_et": session_date_et,
        "policy_version": policy_version,
        "state": "pending",
        "attempt_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    query = select(OpportunityOutcomeMaintenanceRun).where(
        OpportunityOutcomeMaintenanceRun.maintenance_key == maintenance_key
    )
    if db._is_sqlite_engine:
        row = session.execute(query).scalar_one_or_none()
        if row is None:
            row = OpportunityOutcomeMaintenanceRun(**values)
            session.add(row)
            session.flush()
        return row

    try:
        with session.begin_nested():
            session.add(OpportunityOutcomeMaintenanceRun(**values))
            session.flush()
    except IntegrityError:
        pass
    return session.execute(query.with_for_update()).scalar_one()


def claim_outcome_maintenance(
    session_date_et: date,
    *,
    owner_key: str,
    now: Optional[datetime] = None,
    policy_version: str = OUTCOME_MAINTENANCE_POLICY_VERSION,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    db_manager: Optional[DatabaseManager] = None,
) -> OutcomeMaintenanceClaim:
    """Claim one daily slot, respecting retry time, lease, and attempt budget."""

    normalized_owner = str(owner_key or "").strip()
    normalized_policy = str(policy_version or "").strip()
    if not normalized_owner or not normalized_policy:
        raise ValueError("owner_key and policy_version are required")
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    lease_expires_at = observed_at + timedelta(
        seconds=max(60, int(lease_seconds))
    )
    db = db_manager or get_db()
    init_outcome_maintenance_schema(db)

    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = _lock_or_create(
            session,
            db=db,
            session_date_et=session_date_et,
            policy_version=normalized_policy,
            now=observed_at,
        )
        current = _stored(row)
        lease_live = (
            current.state == "running"
            and current.lease_expires_at is not None
            and current.lease_expires_at > observed_at
        )
        retry_not_due = (
            current.next_retry_at is not None
            and current.next_retry_at > observed_at
        )
        if (
            current.state == _TERMINAL_STATE
            or current.attempt_count >= _MAX_ATTEMPTS
            or lease_live
            or retry_not_due
        ):
            return OutcomeMaintenanceClaim(False, current)

        row.state = "running"
        row.attempt_count = current.attempt_count + 1
        row.owner_key = normalized_owner
        row.claimed_at = observed_at
        row.lease_expires_at = lease_expires_at
        row.completed_at = None
        row.next_retry_at = None
        row.last_error_code = None
        row.result_sha256 = None
        row.result_json = None
        row.updated_at = observed_at
        session.flush()
        return OutcomeMaintenanceClaim(True, _stored(row))


def settle_outcome_maintenance(
    maintenance_key: str,
    *,
    owner_key: str,
    state: str,
    result: Mapping[str, Any],
    now: Optional[datetime] = None,
    next_retry_at: Optional[datetime] = None,
    error_code: Optional[str] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> StoredOutcomeMaintenanceRun:
    """Settle the current owner lease after append-only outcome evaluation."""

    normalized_state = str(state or "").strip().lower()
    if normalized_state not in {"completed", "degraded", "failed"}:
        raise ValueError("state must be completed, degraded, or failed")
    normalized_owner = str(owner_key or "").strip()
    if not normalized_owner:
        raise ValueError("owner_key is required")
    observed_at = _utc(now or datetime.now(timezone.utc), "now")
    retry_at = (
        _utc(next_retry_at, "next_retry_at")
        if next_retry_at is not None
        else None
    )
    payload_json = canonical_json(dict(result))
    payload_sha256 = canonical_sha256(dict(result))
    db = db_manager or get_db()
    init_outcome_maintenance_schema(db)

    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        query = select(OpportunityOutcomeMaintenanceRun).where(
            OpportunityOutcomeMaintenanceRun.maintenance_key
            == str(maintenance_key)
        )
        if not db._is_sqlite_engine:
            query = query.with_for_update()
        row = session.execute(query).scalar_one_or_none()
        if row is None:
            raise OutcomeMaintenanceLeaseError("maintenance run does not exist")
        current = _stored(row)
        if (
            current.state != "running"
            or current.owner_key != normalized_owner
            or current.lease_expires_at is None
            or current.lease_expires_at <= observed_at
        ):
            raise OutcomeMaintenanceLeaseError(
                "maintenance lease is no longer owned by this worker"
            )
        if normalized_state != "completed" and current.attempt_count < _MAX_ATTEMPTS:
            if retry_at is None or retry_at <= observed_at:
                raise ValueError(
                    "degraded or failed first attempt requires a future retry"
                )
        else:
            retry_at = None

        row.state = normalized_state
        row.completed_at = observed_at
        row.lease_expires_at = None
        row.next_retry_at = retry_at
        row.last_error_code = (
            str(error_code or "").strip() or None
        )
        row.result_sha256 = payload_sha256
        row.result_json = payload_json
        row.updated_at = observed_at
        session.flush()
        return _stored(row)


def get_latest_outcome_maintenance(
    *,
    policy_version: str = OUTCOME_MAINTENANCE_POLICY_VERSION,
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[StoredOutcomeMaintenanceRun]:
    db = db_manager or get_db()
    init_outcome_maintenance_schema(db)
    with db.session_scope() as session:
        row = session.execute(
            select(OpportunityOutcomeMaintenanceRun)
            .where(
                OpportunityOutcomeMaintenanceRun.policy_version
                == str(policy_version)
            )
            .order_by(
                OpportunityOutcomeMaintenanceRun.session_date_et.desc(),
                OpportunityOutcomeMaintenanceRun.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        return _stored(row) if row is not None else None
