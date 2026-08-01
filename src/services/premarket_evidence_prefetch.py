# -*- coding: utf-8 -*-
"""Cancelable child-process transport for read-only Moomoo premarket data.

The child process owns exactly one ``MoomooFetcher`` and never receives a
database handle or path.  It returns a small, allow-listed observation over a
one-way pipe.  Lease ownership, artifact validation, and all SQLite writes
remain parent-process responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import multiprocessing
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_MESSAGE_VERSION = "premarket-evidence-worker/1.0"
_POLL_INTERVAL_SECONDS = 0.1
_JOIN_TIMEOUT_SECONDS = 1.0
_HEARTBEAT_INTERVAL_SECONDS = 15.0
_MAX_SYMBOLS = 16

_READY_STATUSES = {"ready", "degraded", "unavailable"}
_SAFE_REASONS = {
    "unsupported_market",
    "future_market_date",
    "premarket_not_started_or_no_completed_bar",
    "previous_xnys_session_unresolved",
    "no_completed_premarket_bar",
    "premarket_timestamp_missing",
    "premarket_close_missing",
    "previous_close_last_close_missing",
    "premarket_bar_stale",
    "provider_unavailable",
    "provider_request_failed",
    "adapter_contract_invalid",
    "adapter_reason_unrecognized",
}


class _PrefetchCancelledBeforeStart(RuntimeError):
    pass


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if (
        not symbol
        or len(symbol) > 16
        or not all(char.isalnum() or char in {".", "-", "^"} for char in symbol)
    ):
        raise ValueError("symbols must be non-empty canonical market symbols")
    return symbol


def _finite_number(value: Any, *, positive: bool = False) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _safe_error_code(value: Any, default: str) -> str:
    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate) > 96
        or not all(char.isalnum() or char == "_" for char in candidate)
    ):
        return default
    return candidate


@dataclass(frozen=True)
class PremarketWorkerRequest:
    """Pickle-safe request passed to the provider-only child process."""

    market_date_et: date
    target_as_of: datetime
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        target = _aware_utc(self.target_as_of, "target_as_of")
        normalized = tuple(dict.fromkeys(_normalize_symbol(item) for item in self.symbols))
        if not normalized or len(normalized) > _MAX_SYMBOLS:
            raise ValueError(f"symbols must contain 1-{_MAX_SYMBOLS} unique items")
        if target.astimezone(_ET).date() != self.market_date_et:
            raise ValueError("target_as_of must belong to market_date_et in ET")
        object.__setattr__(self, "target_as_of", target)
        object.__setattr__(self, "symbols", normalized)

    def to_wire(self) -> dict[str, Any]:
        return {
            "message_version": _MESSAGE_VERSION,
            "market_date_et": self.market_date_et.isoformat(),
            "target_as_of": self.target_as_of.isoformat(),
            "symbols": list(self.symbols),
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "PremarketWorkerRequest":
        if payload.get("message_version") != _MESSAGE_VERSION:
            raise ValueError("unsupported worker message version")
        return cls(
            market_date_et=date.fromisoformat(str(payload["market_date_et"])),
            target_as_of=datetime.fromisoformat(
                str(payload["target_as_of"]).replace("Z", "+00:00")
            ),
            symbols=tuple(payload.get("symbols") or ()),
        )


@dataclass(frozen=True)
class PremarketWorkerResult:
    """Bounded parent-side result; it is not yet a persisted artifact."""

    state: str
    observations: tuple[Mapping[str, Any], ...]
    error_code: Optional[str]
    duration_seconds: float
    exit_code: Optional[int]


def _unavailable_observation(
    request: PremarketWorkerRequest,
    symbol: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "market_date_et": request.market_date_et.isoformat(),
        "requested_as_of": request.target_as_of.isoformat(),
        "evidence_as_of": None,
        "price": None,
        "previous_close": None,
        "previous_close_date": None,
        "pct_change": None,
        "status": "unavailable",
        "reason": reason if reason in _SAFE_REASONS else "adapter_reason_unrecognized",
        "source": "MoomooFetcher",
    }


def _sanitize_moomoo_observation(
    request: PremarketWorkerRequest,
    symbol: str,
    raw: Any,
) -> dict[str, Any]:
    """Project an SDK result onto the fixed, non-sensitive pipe contract."""

    if not isinstance(raw, Mapping):
        return _unavailable_observation(
            request,
            symbol,
            "adapter_contract_invalid",
        )
    status = str(raw.get("_status") or "").strip().lower()
    reason = str(raw.get("_reason") or "").strip()
    if status not in _READY_STATUSES:
        status = "unavailable"
        reason = "adapter_contract_invalid"
    elif reason and reason not in _SAFE_REASONS:
        reason = "adapter_reason_unrecognized"

    price = _finite_number(raw.get("price"), positive=True)
    previous_close = _finite_number(raw.get("previous_close"), positive=True)
    pct_change = _finite_number(raw.get("pct_change"))
    evidence_as_of = str(raw.get("as_of") or "").strip() or None
    previous_close_date = (
        str(raw.get("previous_close_date") or "").strip() or None
    )
    if status == "ready" and (
        price is None
        or previous_close is None
        or pct_change is None
        or evidence_as_of is None
        or previous_close_date is None
    ):
        status = "degraded"
        reason = "adapter_contract_invalid"

    return {
        "symbol": symbol,
        "market_date_et": request.market_date_et.isoformat(),
        "requested_as_of": request.target_as_of.isoformat(),
        "evidence_as_of": evidence_as_of,
        "price": price,
        "previous_close": previous_close,
        "previous_close_date": previous_close_date,
        "pct_change": pct_change,
        "status": status,
        "reason": reason or None,
        "source": "MoomooFetcher",
    }


def _send_pipe_message(connection: Any, payload: Mapping[str, Any]) -> None:
    try:
        connection.send(dict(payload))
    except (BrokenPipeError, EOFError, OSError):
        return


def _moomoo_prefetch_worker(
    send_connection: Any,
    request_payload: Mapping[str, Any],
) -> None:
    """Spawn target. Deliberately has no repository or DB dependency."""

    fetcher = None
    try:
        request = PremarketWorkerRequest.from_wire(request_payload)
        from data_provider.moomoo_fetcher import MoomooFetcher

        fetcher = MoomooFetcher()
        available = bool(
            getattr(fetcher, "enabled", False)
            and getattr(fetcher, "priority", 99) < 99
        )
        for symbol in request.symbols:
            if not available:
                observation = _unavailable_observation(
                    request,
                    symbol,
                    "provider_unavailable",
                )
            else:
                try:
                    raw = fetcher.get_premarket(
                        symbol,
                        target_date=request.market_date_et,
                        as_of=request.target_as_of,
                    )
                    observation = _sanitize_moomoo_observation(
                        request,
                        symbol,
                        raw,
                    )
                except Exception:  # noqa: BLE001 - no raw exception crosses pipe
                    observation = _unavailable_observation(
                        request,
                        symbol,
                        "provider_request_failed",
                    )
            _send_pipe_message(
                send_connection,
                {
                    "message_version": _MESSAGE_VERSION,
                    "type": "observation",
                    "observation": observation,
                },
            )
        _send_pipe_message(
            send_connection,
            {
                "message_version": _MESSAGE_VERSION,
                "type": "completed",
            },
        )
    except BaseException as exc:  # noqa: BLE001 - child must report safely
        _send_pipe_message(
            send_connection,
            {
                "message_version": _MESSAGE_VERSION,
                "type": "error",
                "error_code": _safe_error_code(
                    type(exc).__name__,
                    "worker_failed",
                ),
            },
        )
    finally:
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:  # noqa: BLE001 - child is already terminating
                pass
        try:
            send_connection.close()
        except Exception:  # noqa: BLE001
            pass


def _terminate_process(process: Any, *, join_timeout_seconds: float) -> None:
    if process is None:
        return
    try:
        alive = bool(process.is_alive())
    except Exception:  # noqa: BLE001
        alive = False
    if alive:
        try:
            process.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            process.join(timeout=max(0.0, join_timeout_seconds))
        except Exception:  # noqa: BLE001
            pass
    try:
        alive = bool(process.is_alive())
    except Exception:  # noqa: BLE001
        alive = False
    if alive:
        kill = getattr(process, "kill", None)
        if callable(kill):
            try:
                kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            process.join(timeout=max(0.0, join_timeout_seconds))
        except Exception:  # noqa: BLE001
            pass


class SpawnedMoomooPrefetchRunner:
    """Own at most one spawned child and enforce a hard wall-clock deadline."""

    def __init__(
        self,
        *,
        context_factory: Callable[[str], Any] = multiprocessing.get_context,
        worker_target: Callable[..., None] = _moomoo_prefetch_worker,
        poll_interval_seconds: float = _POLL_INTERVAL_SECONDS,
        join_timeout_seconds: float = _JOIN_TIMEOUT_SECONDS,
        heartbeat_interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_interval_seconds <= 0 or join_timeout_seconds < 0:
            raise ValueError("poll interval must be positive and join timeout non-negative")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._context_factory = context_factory
        self._worker_target = worker_target
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._join_timeout_seconds = float(join_timeout_seconds)
        self._heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self._utc_now = utc_now
        self._monotonic = monotonic
        self._active_lock = threading.Lock()
        self._active_process: Any = None
        self._cancel_event = threading.Event()

    def cancel_active(self) -> None:
        """Request cancellation and synchronously reap the active child."""

        self._cancel_event.set()
        with self._active_lock:
            process = self._active_process
        _terminate_process(
            process,
            join_timeout_seconds=self._join_timeout_seconds,
        )

    def reset_cancellation(self) -> None:
        """Allow a lifecycle owner to reuse the runner after a clean restart."""

        with self._active_lock:
            if self._active_process is not None:
                raise RuntimeError(
                    "cannot reset cancellation while a child is active"
                )
            self._cancel_event.clear()

    def run(
        self,
        request: PremarketWorkerRequest,
        *,
        hard_deadline_at: datetime,
        heartbeat: Optional[Callable[[datetime], None]] = None,
    ) -> PremarketWorkerResult:
        deadline = _aware_utc(hard_deadline_at, "hard_deadline_at")
        started_at = _aware_utc(self._utc_now(), "utc_now")
        if deadline <= started_at:
            return PremarketWorkerResult(
                state="timed_out",
                observations=(),
                error_code="hard_deadline_elapsed",
                duration_seconds=0.0,
                exit_code=None,
            )

        context = self._context_factory("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=self._worker_target,
            args=(send_connection, request.to_wire()),
            name="dsa-moomoo-premarket-prefetch",
            daemon=True,
        )
        started_monotonic = self._monotonic()
        last_heartbeat = started_monotonic
        observations: dict[str, Mapping[str, Any]] = {}
        completed = False
        state = "failed"
        error_code: Optional[str] = None
        exit_code: Optional[int] = None
        try:
            with self._active_lock:
                if self._active_process is not None:
                    raise RuntimeError("a premarket child process is already active")
                if self._cancel_event.is_set():
                    raise _PrefetchCancelledBeforeStart
                self._active_process = process
            process.start()
            try:
                send_connection.close()
            except Exception:  # noqa: BLE001
                pass

            while True:
                observed_at = _aware_utc(self._utc_now(), "utc_now")
                if self._cancel_event.is_set():
                    state = "failed"
                    error_code = "scheduler_stopped"
                    break
                if observed_at >= deadline:
                    state = "timed_out"
                    error_code = "worker_timeout"
                    break

                elapsed = self._monotonic() - last_heartbeat
                if heartbeat is not None and elapsed >= self._heartbeat_interval_seconds:
                    heartbeat(observed_at)
                    last_heartbeat = self._monotonic()

                wait_seconds = min(
                    self._poll_interval_seconds,
                    max(0.0, (deadline - observed_at).total_seconds()),
                )
                try:
                    has_message = bool(receive_connection.poll(wait_seconds))
                except (EOFError, OSError):
                    has_message = False
                if has_message:
                    try:
                        message = receive_connection.recv()
                    except (EOFError, OSError):
                        message = None
                    if self._cancel_event.is_set():
                        state = "failed"
                        error_code = "scheduler_stopped"
                        break
                    if not isinstance(message, Mapping):
                        state = "failed"
                        error_code = "worker_protocol_violation"
                        break
                    if message.get("message_version") != _MESSAGE_VERSION:
                        state = "failed"
                        error_code = "worker_protocol_violation"
                        break
                    message_type = message.get("type")
                    if message_type == "observation":
                        observation = message.get("observation")
                        if not isinstance(observation, Mapping):
                            state = "failed"
                            error_code = "worker_protocol_violation"
                            break
                        try:
                            symbol = _normalize_symbol(observation.get("symbol"))
                        except ValueError:
                            state = "failed"
                            error_code = "worker_protocol_violation"
                            break
                        if symbol not in request.symbols or symbol in observations:
                            state = "failed"
                            error_code = "worker_protocol_violation"
                            break
                        observations[symbol] = dict(observation)
                    elif message_type == "completed":
                        completed = True
                        state = "succeeded"
                        break
                    elif message_type == "error":
                        state = "failed"
                        error_code = _safe_error_code(
                            message.get("error_code"),
                            "worker_failed",
                        )
                        break
                    else:
                        state = "failed"
                        error_code = "worker_protocol_violation"
                        break

                if not process.is_alive():
                    if self._cancel_event.is_set():
                        state = "failed"
                        error_code = "scheduler_stopped"
                        break
                    # Drain at most one final message sent immediately before exit.
                    try:
                        if receive_connection.poll(0):
                            continue
                    except (EOFError, OSError):
                        pass
                    if not completed:
                        state = "failed"
                        error_code = "worker_exited_without_completion"
                    break

            if state == "succeeded":
                process.join(timeout=self._join_timeout_seconds)
                if process.is_alive():
                    state = "failed"
                    error_code = "worker_did_not_exit"
            if state != "succeeded":
                _terminate_process(
                    process,
                    join_timeout_seconds=self._join_timeout_seconds,
                )
            exit_code = getattr(process, "exitcode", None)
            if state == "succeeded" and exit_code not in {0, None}:
                state = "failed"
                error_code = "worker_exit_nonzero"
        except _PrefetchCancelledBeforeStart:
            _terminate_process(
                process,
                join_timeout_seconds=self._join_timeout_seconds,
            )
            state = "failed"
            error_code = "scheduler_stopped"
            exit_code = getattr(process, "exitcode", None)
        except Exception as exc:
            _terminate_process(
                process,
                join_timeout_seconds=self._join_timeout_seconds,
            )
            state = "failed"
            error_code = _safe_error_code(type(exc).__name__, "worker_failed")
            exit_code = getattr(process, "exitcode", None)
        finally:
            try:
                receive_connection.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                send_connection.close()
            except Exception:  # noqa: BLE001
                pass
            with self._active_lock:
                if self._active_process is process:
                    self._active_process = None
            close_process = getattr(process, "close", None)
            try:
                process_alive = bool(process.is_alive())
            except Exception:  # noqa: BLE001
                process_alive = False
            if callable(close_process) and not process_alive:
                try:
                    close_process()
                except Exception:  # noqa: BLE001
                    pass

        ordered = tuple(
            observations[symbol]
            for symbol in request.symbols
            if symbol in observations
        )
        return PremarketWorkerResult(
            state=state,
            observations=ordered,
            error_code=error_code,
            duration_seconds=max(0.0, self._monotonic() - started_monotonic),
            exit_code=exit_code,
        )


__all__ = [
    "PremarketWorkerRequest",
    "PremarketWorkerResult",
    "SpawnedMoomooPrefetchRunner",
]
