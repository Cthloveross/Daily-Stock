# -*- coding: utf-8 -*-
"""Low-noise, server-owned maturation of frozen opportunity outcomes.

The scheduler wakes relative to the actual XNYS session close, never before
the primary settlement delay, and never during the canonical premarket
research window.  A database lease coordinates multiple app workers.  The
evaluator itself performs a local due preflight before any provider request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import threading
from typing import Any, Callable, Mapping, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.opportunities.maintenance_repository import (
    OUTCOME_MAINTENANCE_POLICY_VERSION,
    claim_outcome_maintenance,
    settle_outcome_maintenance,
)
from src.services.opportunity_snapshot_service import evaluate_due_snapshots

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_PRIMARY_DELAY = timedelta(minutes=30)
_RETRY_DELAY = timedelta(hours=4, minutes=30)
_RETRY_FLOOR = timedelta(minutes=5)
_LOOKBACK_DAYS = 14
_PROTECTED_START_ET = time(8, 45)
_PROTECTED_END_ET = time(9, 30)

OutcomeEvaluator = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class OutcomeMaintenanceSchedule:
    session_date_et: date
    session_close_at: datetime
    primary_at: datetime
    retry_at: datetime


@dataclass(frozen=True)
class OutcomeMaintenanceTickResult:
    action: str
    state: str
    session_date_et: Optional[str]
    attempt_count: int
    due_snapshot_count: int
    inserted_outcomes: int
    data_gap_horizons: int
    error_code: Optional[str]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outcome scheduler clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _calendar_datetime(value: Any) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise RuntimeError("XNYS session_close returned an invalid value")
    return _aware_utc(value)


def _default_xnys_calendar():
    try:
        import exchange_calendars as xcals

        return xcals.get_calendar("XNYS")
    except Exception as exc:  # noqa: BLE001 - fail closed without exact sessions
        raise RuntimeError("XNYS calendar is unavailable") from exc


def _latest_due_schedule(
    now: datetime,
    *,
    calendar: Any = None,
) -> Optional[OutcomeMaintenanceSchedule]:
    observed_at = _aware_utc(now)
    calendar = calendar or _default_xnys_calendar()
    observed_date_et = observed_at.astimezone(_ET).date()
    for offset in range(_LOOKBACK_DAYS + 1):
        session_date = observed_date_et - timedelta(days=offset)
        try:
            if not bool(calendar.is_session(session_date)):
                continue
            session_close = _calendar_datetime(
                calendar.session_close(session_date)
            )
        except Exception as exc:  # noqa: BLE001 - exact calendar is mandatory
            raise RuntimeError(
                f"XNYS session resolution failed for {session_date.isoformat()}"
            ) from exc
        primary_at = session_close + _PRIMARY_DELAY
        if primary_at > observed_at:
            continue
        return OutcomeMaintenanceSchedule(
            session_date_et=session_date,
            session_close_at=session_close,
            primary_at=primary_at,
            retry_at=session_close + _RETRY_DELAY,
        )
    return None


def _protected_premarket_window(now: datetime) -> bool:
    local_time = _aware_utc(now).astimezone(_ET).time().replace(tzinfo=None)
    return _PROTECTED_START_ET <= local_time < _PROTECTED_END_ET


def _tick_result(
    *,
    action: str,
    state: str,
    schedule: Optional[OutcomeMaintenanceSchedule],
    attempt_count: int = 0,
    result: Optional[Mapping[str, Any]] = None,
    error_code: Optional[str] = None,
) -> OutcomeMaintenanceTickResult:
    result = result or {}
    return OutcomeMaintenanceTickResult(
        action=action,
        state=state,
        session_date_et=(
            schedule.session_date_et.isoformat() if schedule is not None else None
        ),
        attempt_count=attempt_count,
        due_snapshot_count=int(result.get("due_snapshot_count") or 0),
        inserted_outcomes=int(result.get("inserted_outcomes") or 0),
        data_gap_horizons=int(result.get("data_gap_horizons") or 0),
        error_code=error_code,
    )


def execute_outcome_maintenance_tick(
    *,
    owner_key: str,
    evaluator: OutcomeEvaluator = evaluate_due_snapshots,
    now: Optional[datetime] = None,
    calendar: Any = None,
    db_manager=None,
) -> OutcomeMaintenanceTickResult:
    """Claim and settle at most one completed-session maintenance slot."""

    observed_at = _aware_utc(now or datetime.now(timezone.utc))
    if _protected_premarket_window(observed_at):
        return _tick_result(
            action="idle",
            state="premarket_protected",
            schedule=None,
        )
    schedule = _latest_due_schedule(observed_at, calendar=calendar)
    if schedule is None:
        return _tick_result(
            action="idle",
            state="waiting_settlement",
            schedule=None,
        )

    claim = claim_outcome_maintenance(
        schedule.session_date_et,
        owner_key=owner_key,
        now=observed_at,
        policy_version=OUTCOME_MAINTENANCE_POLICY_VERSION,
        db_manager=db_manager,
    )
    if not claim.claimed:
        return _tick_result(
            action="idle",
            state=claim.run.state,
            schedule=schedule,
            attempt_count=claim.run.attempt_count,
            result=claim.run.result,
            error_code=claim.run.last_error_code,
        )

    attempt_count = claim.run.attempt_count
    try:
        result = dict(
            evaluator(
                db_manager=db_manager,
                evaluated_at=observed_at,
            )
        )
    except Exception as exc:  # noqa: BLE001 - preserve host and redact details
        error_code = type(exc).__name__
        retry_at = max(
            schedule.retry_at,
            observed_at + _RETRY_FLOOR,
        )
        settled = settle_outcome_maintenance(
            claim.run.maintenance_key,
            owner_key=owner_key,
            state="failed",
            result={
                "schema_version": "opportunity-outcome-maintenance/1.0",
                "evaluated_at": observed_at.isoformat(),
                "error_type": error_code,
            },
            now=observed_at,
            next_retry_at=retry_at,
            error_code=error_code,
            db_manager=db_manager,
        )
        return _tick_result(
            action="failed",
            state=settled.state,
            schedule=schedule,
            attempt_count=settled.attempt_count,
            error_code=error_code,
        )

    degraded = (
        int(result.get("failed_snapshot_count") or 0) > 0
        or int(result.get("data_gap_horizons") or 0) > 0
    )
    state = "degraded" if degraded else "completed"
    retry_at = (
        max(schedule.retry_at, observed_at + _RETRY_FLOOR)
        if degraded
        else None
    )
    settled = settle_outcome_maintenance(
        claim.run.maintenance_key,
        owner_key=owner_key,
        state=state,
        result=result,
        now=observed_at,
        next_retry_at=retry_at,
        error_code=(
            "outcome_data_gap"
            if int(result.get("data_gap_horizons") or 0) > 0
            else "snapshot_evaluation_failed"
            if int(result.get("failed_snapshot_count") or 0) > 0
            else None
        ),
        db_manager=db_manager,
    )
    return _tick_result(
        action="evaluated",
        state=settled.state,
        schedule=schedule,
        attempt_count=settled.attempt_count,
        result=result,
        error_code=settled.last_error_code,
    )


class CanonicalOpportunityOutcomeScheduler:
    """Bounded daemon host; the database remains the execution authority."""

    def __init__(
        self,
        tick: Optional[Callable[[], OutcomeMaintenanceTickResult]] = None,
        *,
        interval_seconds: float = 60.0,
        thread_name: str = "dsa-opportunity-outcomes",
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._owner_key = f"outcome-scheduler-{uuid4().hex}"
        self._tick = tick or (
            lambda: execute_outcome_maintenance_tick(owner_key=self._owner_key)
        )
        self._interval_seconds = float(interval_seconds)
        self._thread_name = thread_name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_error_fingerprint: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=self._thread_name,
            daemon=True,
        )
        self._thread.start()
        logger.info("[outcome-scheduler] started")

    def stop(self, *, join_timeout_seconds: Optional[float] = None) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        if thread is threading.current_thread():
            logger.warning(
                "[outcome-scheduler] stop requested from scheduler thread"
            )
            return
        timeout = (
            None
            if join_timeout_seconds is None
            else max(0.0, float(join_timeout_seconds))
        )
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning(
                "[outcome-scheduler] stop timed out; worker is still draining"
            )
            return
        self._thread = None
        logger.info("[outcome-scheduler] stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self._tick()
                if result.action == "evaluated":
                    logger.info(
                        "[outcome-scheduler] settled session=%s state=%s "
                        "attempt=%s due=%s inserted=%s gaps=%s",
                        result.session_date_et,
                        result.state,
                        result.attempt_count,
                        result.due_snapshot_count,
                        result.inserted_outcomes,
                        result.data_gap_horizons,
                    )
                elif result.action == "failed":
                    fingerprint = str(result.error_code or "unknown")
                    if fingerprint != self._last_error_fingerprint:
                        logger.error(
                            "[outcome-scheduler] failed session=%s "
                            "attempt=%s error_type=%s",
                            result.session_date_et,
                            result.attempt_count,
                            fingerprint,
                        )
                    self._last_error_fingerprint = fingerprint
                else:
                    self._last_error_fingerprint = None
            except Exception as exc:  # noqa: BLE001 - keep daemon alive
                fingerprint = type(exc).__name__
                if fingerprint != self._last_error_fingerprint:
                    logger.error(
                        "[outcome-scheduler] tick failed error_type=%s",
                        fingerprint,
                    )
                    self._last_error_fingerprint = fingerprint
            self._stop_event.wait(self._interval_seconds)


__all__ = [
    "CanonicalOpportunityOutcomeScheduler",
    "OutcomeMaintenanceSchedule",
    "OutcomeMaintenanceTickResult",
    "execute_outcome_maintenance_tick",
]
