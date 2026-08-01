# -*- coding: utf-8 -*-
"""Default-off scheduler for shadow Moomoo premarket evidence.

This producer is intentionally isolated from the formal Regime consumer.  It
uses one XNYS-relative window, a database lease, and a cancelable child
process.  A normally completed child always yields an exact-coverage
``ready``/``partial``/``unavailable`` audit bundle; a crash or timeout only
settles operational state and never invents market evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
import threading
from typing import Any, Callable, Mapping, Optional, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.regime.premarket_artifacts import (
    DEFAULT_MAX_FRESHNESS_MINUTES,
    PREMARKET_ARTIFACT_SCHEMA_VERSION,
    PremarketArtifactBundle,
    PremarketArtifactValidationError,
    PremarketObservation,
    build_premarket_artifact_bundle,
    normalize_prefetch_symbols,
)
from src.regime.premarket_repository import (
    append_premarket_artifact_and_settle,
    claim_premarket_prefetch_run,
    heartbeat_premarket_prefetch_run,
    mark_premarket_prefetch_missed,
    settle_premarket_prefetch_run,
)
from src.services.premarket_evidence_prefetch import (
    PremarketWorkerRequest,
    PremarketWorkerResult,
    SpawnedMoomooPrefetchRunner,
)
from src.services.premarket_research_service import (
    ResolvedPremarketUniverse,
    resolve_premarket_research_universe,
)

logger = logging.getLogger(__name__)

PREFETCH_POLICY_VERSION = "moomoo-shadow-open-minus-22m-v1"
PREFETCH_ADAPTER_KEY = "MoomooFetcher.get_premarket/v1"
_ET = ZoneInfo("America/New_York")
_TARGET_BEFORE_OPEN = timedelta(minutes=22)
_LATEST_START_BEFORE_OPEN = timedelta(minutes=21)
_HARD_DEADLINE_BEFORE_OPEN = timedelta(minutes=19)
_ARTIFACT_FRESHNESS = timedelta(
    minutes=DEFAULT_MAX_FRESHNESS_MINUTES
)
_MAX_RESEARCH_SYMBOLS = 5

UniverseLoader = Callable[[], Optional[ResolvedPremarketUniverse]]


@dataclass(frozen=True)
class PremarketEvidenceSchedule:
    market_date_et: date
    previous_session: date
    session_open_at: datetime
    target_as_of: datetime
    latest_start_at: datetime
    hard_deadline_at: datetime
    expires_at: datetime
    window_state: str


@dataclass(frozen=True)
class PremarketEvidenceTickResult:
    action: str
    state: str
    market_date_et: Optional[str]
    slot_key: Optional[str]
    bundle_key: Optional[str]
    attempt_count: int
    quality: Optional[str]
    requested_count: int
    available_count: int
    error_code: Optional[str]


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _calendar_datetime(value: Any, field_name: str) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise RuntimeError(f"XNYS {field_name} returned an invalid value")
    return _aware_utc(value, field_name)


def _calendar_date(value: Any, field_name: str) -> date:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"XNYS {field_name} returned an invalid value") from exc


def _default_xnys_calendar():
    try:
        import exchange_calendars as xcals

        return xcals.get_calendar("XNYS")
    except Exception as exc:  # noqa: BLE001 - exact session timing is mandatory
        raise RuntimeError("XNYS calendar is unavailable") from exc


def resolve_premarket_evidence_schedule(
    now: datetime,
    *,
    calendar: Any = None,
) -> Optional[PremarketEvidenceSchedule]:
    """Resolve today's one-shot shadow window from the exact XNYS open."""

    observed_at = _aware_utc(now, "now")
    market_date = observed_at.astimezone(_ET).date()
    calendar = calendar or _default_xnys_calendar()
    try:
        if not bool(calendar.is_session(market_date)):
            return None
        session_open = _calendar_datetime(
            calendar.session_open(market_date),
            "session_open",
        )
        previous_session = _calendar_date(
            calendar.previous_session(market_date),
            "previous_session",
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on calendar drift
        raise RuntimeError(
            f"XNYS premarket schedule failed for {market_date.isoformat()}"
        ) from exc

    target = session_open - _TARGET_BEFORE_OPEN
    latest_start = session_open - _LATEST_START_BEFORE_OPEN
    hard_deadline = session_open - _HARD_DEADLINE_BEFORE_OPEN
    if observed_at < target:
        state = "waiting_window"
    elif observed_at < latest_start:
        state = "ready_to_run"
    elif observed_at < hard_deadline:
        state = "late_start_rejected"
    else:
        state = "window_missed"
    return PremarketEvidenceSchedule(
        market_date_et=market_date,
        previous_session=previous_session,
        session_open_at=session_open,
        target_as_of=target,
        latest_start_at=latest_start,
        hard_deadline_at=hard_deadline,
        expires_at=target + _ARTIFACT_FRESHNESS,
        window_state=state,
    )


def _tick_result(
    *,
    action: str,
    state: str,
    schedule: Optional[PremarketEvidenceSchedule],
    run: Any = None,
    bundle: Any = None,
    quality: Optional[str] = None,
    requested_count: int = 0,
    available_count: int = 0,
    error_code: Optional[str] = None,
) -> PremarketEvidenceTickResult:
    return PremarketEvidenceTickResult(
        action=action,
        state=state,
        market_date_et=(
            schedule.market_date_et.isoformat()
            if schedule is not None
            else None
        ),
        slot_key=(
            str(run.slot_key)
            if run is not None and getattr(run, "slot_key", None)
            else None
        ),
        bundle_key=(
            str(bundle.bundle_key)
            if bundle is not None and getattr(bundle, "bundle_key", None)
            else (
                str(run.bundle_key)
                if run is not None and getattr(run, "bundle_key", None)
                else None
            )
        ),
        attempt_count=(
            int(getattr(run, "attempt_count", 0)) if run is not None else 0
        ),
        quality=quality,
        requested_count=int(requested_count),
        available_count=int(available_count),
        error_code=(
            str(error_code)
            if error_code
            else (
                str(run.last_error_code)
                if run is not None and getattr(run, "last_error_code", None)
                else None
            )
        ),
    )


def _resolve_universe(
    loader: Optional[UniverseLoader],
    *,
    db_manager=None,
) -> Optional[ResolvedPremarketUniverse]:
    return (
        loader()
        if loader is not None
        else resolve_premarket_research_universe(
            include_fallback_suggestion=False,
            db_manager=db_manager,
        )
    )


def _parse_worker_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("worker datetime is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware_utc(parsed, "worker datetime")


def _unavailable_reason(payload: Mapping[str, Any], worker_state: str) -> str:
    if worker_state == "timed_out":
        return "request_timeout"
    if worker_state != "succeeded":
        return "worker_unavailable"
    reason = str(payload.get("reason") or "").strip()
    if reason == "premarket_bar_stale":
        return "stale_evidence"
    if reason == "provider_unavailable":
        return "provider_unavailable"
    if reason == "provider_request_failed":
        return "worker_unavailable"
    if reason == "unsupported_market":
        return "symbol_not_found"
    if reason in {
        "premarket_timestamp_missing",
        "premarket_close_missing",
        "previous_close_last_close_missing",
        "adapter_contract_invalid",
        "adapter_reason_unrecognized",
    }:
        return "invalid_quote"
    return "no_quote"


def _artifact_observations(
    *,
    schedule: PremarketEvidenceSchedule,
    symbols: Sequence[str],
    worker_result: PremarketWorkerResult,
) -> tuple[PremarketObservation, ...]:
    received = {
        str(item.get("symbol") or "").strip().upper(): item
        for item in worker_result.observations
        if isinstance(item, Mapping)
    }
    observations: list[PremarketObservation] = []
    for symbol in symbols:
        payload = received.get(symbol)
        if payload is None:
            missing_reason = (
                "request_timeout"
                if worker_result.state == "timed_out"
                else "worker_unavailable"
            )
            observations.append(
                PremarketObservation.unavailable(
                    symbol=symbol,
                    market_date_et=schedule.market_date_et,
                    previous_session=schedule.previous_session,
                    requested_as_of=schedule.target_as_of,
                    reason=missing_reason,
                )
            )
            continue
        try:
            if (
                worker_result.state != "succeeded"
                or payload.get("status") != "ready"
                or payload.get("source") != "MoomooFetcher"
                or payload.get("market_date_et")
                != schedule.market_date_et.isoformat()
                or _parse_worker_datetime(payload.get("requested_as_of"))
                != schedule.target_as_of
                or date.fromisoformat(str(payload.get("previous_close_date")))
                != schedule.previous_session
            ):
                raise ValueError("worker observation is unavailable")
            evidence_at = _parse_worker_datetime(payload.get("evidence_as_of"))
            if (
                evidence_at > schedule.target_as_of
                or schedule.target_as_of - evidence_at
                > _ARTIFACT_FRESHNESS
            ):
                raise PremarketArtifactValidationError(
                    "worker evidence is stale or future"
                )
            observation = PremarketObservation.available(
                symbol=symbol,
                market_date_et=schedule.market_date_et,
                previous_session=schedule.previous_session,
                requested_as_of=schedule.target_as_of,
                evidence_as_of=evidence_at,
                price=payload.get("price"),
                previous_close=payload.get("previous_close"),
                change_pct=payload.get("pct_change"),
            )
        except Exception:  # noqa: BLE001 - one bad quote becomes explicit absence
            reason = (
                "stale_evidence"
                if str(payload.get("reason") or "") == "premarket_bar_stale"
                else _unavailable_reason(payload, worker_result.state)
            )
            if payload.get("status") == "ready" and reason == "no_quote":
                reason = "invalid_quote"
            observation = PremarketObservation.unavailable(
                symbol=symbol,
                market_date_et=schedule.market_date_et,
                previous_session=schedule.previous_session,
                requested_as_of=schedule.target_as_of,
                reason=reason,
            )
        observations.append(observation)
    return tuple(observations)


def _coverage(bundle: PremarketArtifactBundle) -> dict[str, Any]:
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


def execute_premarket_evidence_tick(
    *,
    owner_key: str,
    now: Optional[datetime] = None,
    calendar: Any = None,
    db_manager=None,
    universe_loader: Optional[UniverseLoader] = None,
    runner: Optional[SpawnedMoomooPrefetchRunner] = None,
    utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    claim_fn: Callable[..., Any] = claim_premarket_prefetch_run,
    heartbeat_fn: Callable[..., Any] = heartbeat_premarket_prefetch_run,
    append_fn: Callable[..., Any] = append_premarket_artifact_and_settle,
    settle_fn: Callable[..., Any] = settle_premarket_prefetch_run,
    missed_fn: Callable[..., Any] = mark_premarket_prefetch_missed,
) -> PremarketEvidenceTickResult:
    """Execute at most one DB-claimed shadow prefetch attempt."""

    observed_at = _aware_utc(now or utc_now(), "now")
    schedule = resolve_premarket_evidence_schedule(
        observed_at,
        calendar=calendar,
    )
    if schedule is None:
        return _tick_result(
            action="idle",
            state="non_session",
            schedule=None,
        )
    if schedule.window_state == "waiting_window":
        return _tick_result(
            action="idle",
            state=schedule.window_state,
            schedule=schedule,
        )

    universe = _resolve_universe(
        universe_loader,
        db_manager=db_manager,
    )
    if (
        universe is None
        or not universe.universe_version_key
        or not universe.symbols
    ):
        return _tick_result(
            action="idle",
            state="research_pool_missing",
            schedule=schedule,
            error_code="research_pool_missing",
        )
    limit = max(
        1,
        min(
            _MAX_RESEARCH_SYMBOLS,
            int(universe.requested_limit or _MAX_RESEARCH_SYMBOLS),
        ),
    )
    symbols = normalize_prefetch_symbols(universe.symbols[:limit])
    universe_key = str(universe.universe_version_key)

    if schedule.window_state == "late_start_rejected":
        return _tick_result(
            action="idle",
            state=schedule.window_state,
            schedule=schedule,
            requested_count=len(symbols),
            error_code="latest_start_elapsed",
        )
    if schedule.window_state == "window_missed":
        run = missed_fn(
            schedule.market_date_et,
            schedule.previous_session,
            target_as_of=schedule.target_as_of,
            hard_deadline_at=schedule.hard_deadline_at,
            policy_version=PREFETCH_POLICY_VERSION,
            universe_key=universe_key,
            symbols=symbols,
            now=observed_at,
            db_manager=db_manager,
        )
        newly_settled = (
            getattr(run, "completed_at", None) is not None
            and _aware_utc(run.completed_at, "completed_at")
            == observed_at
        )
        return _tick_result(
            action="missed" if newly_settled else "idle",
            state=run.state,
            schedule=schedule,
            run=run,
            requested_count=len(symbols),
        )

    claim = claim_fn(
        schedule.market_date_et,
        schedule.previous_session,
        target_as_of=schedule.target_as_of,
        hard_deadline_at=schedule.hard_deadline_at,
        policy_version=PREFETCH_POLICY_VERSION,
        universe_key=universe_key,
        symbols=symbols,
        owner_key=owner_key,
        now=observed_at,
        max_attempts=2,
        db_manager=db_manager,
    )
    if not claim.claimed:
        return _tick_result(
            action="idle",
            state=claim.run.state,
            schedule=schedule,
            run=claim.run,
            requested_count=len(symbols),
        )

    run = claim.run
    active_runner = runner or SpawnedMoomooPrefetchRunner()
    fetch_started_at = observed_at

    def heartbeat(heartbeat_at: datetime) -> None:
        heartbeat_fn(
            run.slot_key,
            owner_key=owner_key,
            attempt_count=run.attempt_count,
            now=heartbeat_at,
            db_manager=db_manager,
        )

    worker_result = active_runner.run(
        PremarketWorkerRequest(
            market_date_et=schedule.market_date_et,
            target_as_of=schedule.target_as_of,
            symbols=tuple(symbols),
        ),
        hard_deadline_at=schedule.hard_deadline_at,
        heartbeat=heartbeat,
    )
    fetched_at = _aware_utc(utc_now(), "utc_now")
    if worker_result.state != "succeeded" or fetched_at >= schedule.hard_deadline_at:
        terminal_state = (
            "timed_out"
            if worker_result.state == "timed_out"
            or fetched_at >= schedule.hard_deadline_at
            else "failed"
        )
        error_code = (
            "completed_after_deadline"
            if worker_result.state == "succeeded"
            and fetched_at >= schedule.hard_deadline_at
            else worker_result.error_code
            or "worker_failed"
        )
        settled = settle_fn(
            run.slot_key,
            owner_key=owner_key,
            attempt_count=run.attempt_count,
            state=terminal_state,
            error_code=error_code,
            now=fetched_at,
            db_manager=db_manager,
        )
        return _tick_result(
            action="settled",
            state=settled.state,
            schedule=schedule,
            run=settled,
            requested_count=len(symbols),
            available_count=len(worker_result.observations),
            error_code=error_code,
        )

    observations = _artifact_observations(
        schedule=schedule,
        symbols=symbols,
        worker_result=worker_result,
    )
    bundle = build_premarket_artifact_bundle(
        market_date_et=schedule.market_date_et,
        previous_session=schedule.previous_session,
        target_as_of=schedule.target_as_of,
        required_universe=symbols,
        observations=observations,
    )
    coverage = _coverage(bundle)
    appended = append_fn(
        run.slot_key,
        owner_key=owner_key,
        attempt_count=run.attempt_count,
        schema_version=PREMARKET_ARTIFACT_SCHEMA_VERSION,
        adapter_key=PREFETCH_ADAPTER_KEY,
        fetch_started_at=fetch_started_at,
        fetched_at=fetched_at,
        expires_at=schedule.expires_at,
        quality_state=bundle.quality,
        coverage=coverage,
        payload=bundle.to_payload(),
        created_at=fetched_at,
        db_manager=db_manager,
    )
    return _tick_result(
        action="settled",
        state=appended.run.state,
        schedule=schedule,
        run=appended.run,
        bundle=appended.bundle,
        quality=bundle.quality,
        requested_count=coverage["requested_count"],
        available_count=coverage["available_count"],
    )


class MoomooPremarketEvidenceScheduler:
    """Low-noise daemon host with synchronous child cancellation on stop."""

    def __init__(
        self,
        tick: Optional[Callable[[], PremarketEvidenceTickResult]] = None,
        *,
        runner: Optional[SpawnedMoomooPrefetchRunner] = None,
        interval_seconds: float = 30.0,
        thread_name: str = "dsa-moomoo-premarket-evidence",
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._runner = runner or SpawnedMoomooPrefetchRunner()
        self._owner_key = f"premarket-evidence-{uuid4().hex}"
        self._tick = tick or (
            lambda: execute_premarket_evidence_tick(
                owner_key=self._owner_key,
                runner=self._runner,
            )
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
        self._runner.reset_cancellation()
        self._thread = threading.Thread(
            target=self._run,
            name=self._thread_name,
            daemon=True,
        )
        self._thread.start()
        logger.info("[premarket-evidence] scheduler started shadow_only=true")

    def stop(self, *, join_timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        self._runner.cancel_active()
        thread = self._thread
        if thread is None:
            return
        if thread is threading.current_thread():
            logger.warning("[premarket-evidence] stop requested from scheduler thread")
            return
        thread.join(timeout=max(0.0, float(join_timeout_seconds)))
        if thread.is_alive():
            logger.warning(
                "[premarket-evidence] scheduler stop timed out after child cancellation"
            )
            return
        self._thread = None
        logger.info("[premarket-evidence] scheduler stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self._tick()
                if result.action in {"settled", "missed"}:
                    logger.info(
                        "[premarket-evidence] settled market_date=%s state=%s "
                        "quality=%s coverage=%s/%s error_code=%s",
                        result.market_date_et,
                        result.state,
                        result.quality,
                        result.available_count,
                        result.requested_count,
                        result.error_code,
                    )
                self._last_error_fingerprint = None
            except Exception as exc:  # noqa: BLE001 - preserve host, redact payload
                fingerprint = type(exc).__name__
                if fingerprint != self._last_error_fingerprint:
                    logger.error(
                        "[premarket-evidence] tick failed error_type=%s",
                        fingerprint,
                    )
                    self._last_error_fingerprint = fingerprint
            self._stop_event.wait(self._interval_seconds)


__all__ = [
    "MoomooPremarketEvidenceScheduler",
    "PREFETCH_ADAPTER_KEY",
    "PREFETCH_POLICY_VERSION",
    "PremarketEvidenceSchedule",
    "PremarketEvidenceTickResult",
    "execute_premarket_evidence_tick",
    "resolve_premarket_evidence_schedule",
]
