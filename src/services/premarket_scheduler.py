# -*- coding: utf-8 -*-
"""Low-noise host for the server-owned XNYS premarket research cycle.

The tick contract is deterministic and delegates due-time, attempt-budget,
lease, and publication checks to ``premarket_research_service``. The thread is
only a local wake-up mechanism; the database lease remains authoritative when
reloads or multiple local workers overlap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
from typing import Any, Callable, Mapping, Optional

from src.services.premarket_research_service import (
    ResolvedPremarketUniverse,
    resolve_premarket_research_universe,
    run_canonical_premarket_research,
    status_canonical_premarket_research,
)

logger = logging.getLogger(__name__)

ScanRunner = Callable[..., dict[str, Any]]
UniverseLoader = Callable[[], Optional[ResolvedPremarketUniverse]]
StatusReader = Callable[..., dict[str, Any]]
CycleRunner = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class PremarketSchedulerTickResult:
    """One scheduler observation without duplicating provider payloads."""

    action: str
    state: str
    cycle_key: Optional[str]
    attempt_key: Optional[str]
    error_code: Optional[str]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _tick_result(
    action: str,
    status: Mapping[str, Any],
) -> PremarketSchedulerTickResult:
    return PremarketSchedulerTickResult(
        action=action,
        state=str(status.get("state") or "unknown"),
        cycle_key=(
            str(status["cycle_key"])
            if status.get("cycle_key") is not None
            else None
        ),
        attempt_key=(
            str(status["attempt_key"])
            if status.get("attempt_key") is not None
            else None
        ),
        error_code=(
            str(status["error_code"])
            if status.get("error_code") is not None
            else None
        ),
    )


def execute_premarket_scheduler_tick(
    *,
    scan_runner: ScanRunner,
    now: Optional[datetime] = None,
    calendar: Any = None,
    db_manager=None,
    universe_loader: Optional[UniverseLoader] = None,
    status_reader: StatusReader = status_canonical_premarket_research,
    cycle_runner: CycleRunner = run_canonical_premarket_research,
) -> PremarketSchedulerTickResult:
    """Observe once and execute at most one due, DB-claimed attempt."""

    observed_at = _aware_utc(now or datetime.now(timezone.utc))
    universe = (
        universe_loader()
        if universe_loader is not None
        else resolve_premarket_research_universe(
            include_fallback_suggestion=False,
            db_manager=db_manager,
        )
    )
    symbols = universe.symbols if universe is not None else ()
    requested_limit = universe.requested_limit if universe is not None else 5
    universe_source = universe.source if universe is not None else "unavailable"
    universe_version_key = (
        universe.universe_version_key if universe is not None else None
    )
    status = status_reader(
        symbols,
        requested_limit,
        now=observed_at,
        calendar=calendar,
        db_manager=db_manager,
        universe_source=universe_source,
        universe_version_key=universe_version_key,
        scheduler_enabled=True,
    )

    due = status.get("state") == "ready_to_run"
    if status.get("state") in {"blocked", "failed"} and status.get("recoverable"):
        raw_next = status.get("next_scheduled_at")
        if isinstance(raw_next, str) and raw_next:
            try:
                next_at = datetime.fromisoformat(raw_next.replace("Z", "+00:00"))
                due = _aware_utc(next_at) <= observed_at
            except ValueError:
                due = False
    if not due:
        return _tick_result("idle", status)

    result = cycle_runner(
        symbols,
        requested_limit,
        scan_runner=scan_runner,
        now=observed_at,
        calendar=calendar,
        db_manager=db_manager,
        universe_source=universe_source,
        universe_version_key=universe_version_key,
        trigger="scheduler",
        scheduler_enabled=True,
    )
    return _tick_result("attempted", result)


class CanonicalPremarketScheduler:
    """A bounded daemon loop that is quiet unless work or an incident occurs."""

    def __init__(
        self,
        tick: Callable[[], PremarketSchedulerTickResult],
        *,
        interval_seconds: float = 30.0,
        thread_name: str = "dsa-premarket-research",
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._tick = tick
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
        logger.info("[premarket-scheduler] started")

    def stop(
        self,
        *,
        join_timeout_seconds: Optional[float] = None,
    ) -> None:
        """Stop the host without abandoning an in-flight tick.

        Lifespan shutdown uses the default unbounded join so a replacement app
        cannot start beside a provider tick left by the previous lifespan.
        Tests and diagnostic callers may supply a finite timeout; in that case
        the live thread reference is retained and ``start`` remains a no-op.
        """

        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        if thread is threading.current_thread():
            logger.warning(
                "[premarket-scheduler] stop requested from scheduler thread; "
                "waiting for loop exit is delegated to the lifecycle owner"
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
                "[premarket-scheduler] stop timed out; scheduler thread is "
                "still draining"
            )
            return
        self._thread = None
        logger.info("[premarket-scheduler] stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self._tick()
                self._last_error_fingerprint = None
                if result.action == "attempted":
                    logger.info(
                        "[premarket-scheduler] attempt settled state=%s "
                        "cycle_key=%s attempt_key=%s error_code=%s",
                        result.state,
                        result.cycle_key,
                        result.attempt_key,
                        result.error_code,
                    )
            except Exception as exc:  # noqa: BLE001 - keep host alive, redact payload
                fingerprint = type(exc).__name__
                if fingerprint != self._last_error_fingerprint:
                    logger.error(
                        "[premarket-scheduler] tick failed error_type=%s",
                        fingerprint,
                    )
                    self._last_error_fingerprint = fingerprint
            self._stop_event.wait(self._interval_seconds)
