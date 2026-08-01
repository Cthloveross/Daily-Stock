from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone
import multiprocessing
import threading
import time
from zoneinfo import ZoneInfo
import pytest

from src.services.premarket_evidence_prefetch import (
    PremarketWorkerRequest,
    SpawnedMoomooPrefetchRunner,
    _MESSAGE_VERSION,
    _moomoo_prefetch_worker,
)


UTC = timezone.utc
MARKET_DATE = date(2026, 7, 24)
TARGET = datetime(2026, 7, 24, 13, 8, tzinfo=UTC)


class _PipeEndpoint:
    def __init__(self, messages=()):
        self.messages = deque(messages)
        self.sent = []
        self.closed = False

    def poll(self, _timeout=0):
        return bool(self.messages)

    def recv(self):
        if not self.messages:
            raise EOFError
        return self.messages.popleft()

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(
        self,
        *,
        target,
        args,
        name,
        daemon,
        remain_alive_after_terminate=False,
    ):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False
        self.alive = False
        self.exitcode = None
        self.terminated = False
        self.killed = False
        self.remain_alive_after_terminate = remain_alive_after_terminate

    def start(self):
        self.started = True
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        if self.killed or (
            self.terminated and not self.remain_alive_after_terminate
        ):
            self.alive = False
            self.exitcode = -15
        elif not self.terminated:
            self.alive = False
            self.exitcode = 0

    def terminate(self):
        self.terminated = True
        if not self.remain_alive_after_terminate:
            self.alive = False
            self.exitcode = -15

    def kill(self):
        self.killed = True
        self.alive = False
        self.exitcode = -9


class _FakeContext:
    def __init__(self, messages=(), *, remain_alive_after_terminate=False):
        self.receive = _PipeEndpoint(messages)
        self.send = _PipeEndpoint()
        self.process = None
        self.remain_alive_after_terminate = remain_alive_after_terminate

    def Pipe(self, duplex=False):
        assert duplex is False
        return self.receive, self.send

    def Process(self, **kwargs):
        self.process = _FakeProcess(
            **kwargs,
            remain_alive_after_terminate=self.remain_alive_after_terminate,
        )
        return self.process


def _observation(symbol: str):
    return {
        "symbol": symbol,
        "market_date_et": MARKET_DATE.isoformat(),
        "requested_as_of": TARGET.isoformat(),
        "evidence_as_of": (TARGET - timedelta(minutes=1)).isoformat(),
        "price": 101.0,
        "previous_close": 100.0,
        "previous_close_date": "2026-07-23",
        "pct_change": 1.0,
        "status": "ready",
        "reason": None,
        "source": "MoomooFetcher",
    }


def _message(message_type: str, **payload):
    return {
        "message_version": _MESSAGE_VERSION,
        "type": message_type,
        **payload,
    }


def _spawn_success_worker(send_connection, request_payload):
    target = request_payload["target_as_of"]
    market_date = request_payload["market_date_et"]
    send_connection.send(
        _message(
            "observation",
            observation={
                "symbol": "SPY",
                "market_date_et": market_date,
                "requested_as_of": target,
                "evidence_as_of": target,
                "price": 101.0,
                "previous_close": 100.0,
                "previous_close_date": "2026-07-23",
                "pct_change": 1.0,
                "status": "ready",
                "reason": None,
                "source": "MoomooFetcher",
            },
        )
    )
    send_connection.send(_message("completed"))
    send_connection.close()


def _spawn_hanging_worker(_send_connection, _request_payload):
    while True:
        time.sleep(1)


def _request():
    return PremarketWorkerRequest(
        market_date_et=MARKET_DATE,
        target_as_of=TARGET,
        symbols=("spy", "AAPL", "AAPL"),
    )


def test_request_is_normalized_and_rejects_a_cross_market_date_target():
    request = _request()

    assert request.symbols == ("SPY", "AAPL")
    assert PremarketWorkerRequest.from_wire(request.to_wire()) == request

    with pytest.raises(ValueError, match="market_date_et"):
        PremarketWorkerRequest(
            market_date_et=date(2026, 7, 23),
            target_as_of=TARGET,
            symbols=("SPY",),
        )


def test_parent_accepts_ordered_observations_and_completed_message():
    context = _FakeContext(
        (
            _message("observation", observation=_observation("SPY")),
            _message("observation", observation=_observation("AAPL")),
            _message("completed"),
        )
    )
    runner = SpawnedMoomooPrefetchRunner(
        context_factory=lambda method: (
            context if method == "spawn" else None
        ),
        utc_now=lambda: TARGET,
    )

    result = runner.run(
        _request(),
        hard_deadline_at=TARGET + timedelta(minutes=3),
    )

    assert result.state == "succeeded"
    assert result.error_code is None
    assert [item["symbol"] for item in result.observations] == ["SPY", "AAPL"]
    assert context.process.started is True
    assert context.process.daemon is True
    assert context.process.terminated is False


def test_duplicate_or_unexpected_symbol_fails_closed_and_terminates_child():
    context = _FakeContext(
        (
            _message("observation", observation=_observation("SPY")),
            _message("observation", observation=_observation("SPY")),
        )
    )
    runner = SpawnedMoomooPrefetchRunner(
        context_factory=lambda _method: context,
        utc_now=lambda: TARGET,
    )

    result = runner.run(
        _request(),
        hard_deadline_at=TARGET + timedelta(minutes=3),
    )

    assert result.state == "failed"
    assert result.error_code == "worker_protocol_violation"
    assert context.process.terminated is True


def test_hard_deadline_terminates_then_kills_a_stuck_child():
    context = _FakeContext(remain_alive_after_terminate=True)
    clock_values = iter(
        (
            TARGET,
            TARGET,
            TARGET + timedelta(seconds=2),
        )
    )
    runner = SpawnedMoomooPrefetchRunner(
        context_factory=lambda _method: context,
        utc_now=lambda: next(clock_values),
        poll_interval_seconds=0.001,
        join_timeout_seconds=0.001,
    )

    result = runner.run(
        PremarketWorkerRequest(
            market_date_et=MARKET_DATE,
            target_as_of=TARGET,
            symbols=("SPY",),
        ),
        hard_deadline_at=TARGET + timedelta(seconds=1),
    )

    assert result.state == "timed_out"
    assert result.error_code == "worker_timeout"
    assert context.process.terminated is True
    assert context.process.killed is True
    assert context.process.is_alive() is False


def test_parent_heartbeats_without_logging_or_changing_worker_payload():
    context = _FakeContext(
        (
            _message("observation", observation=_observation("SPY")),
            _message("completed"),
        )
    )
    monotonic_values = iter((0.0, 16.0, 16.0, 16.1, 16.2, 16.3))
    heartbeats = []
    runner = SpawnedMoomooPrefetchRunner(
        context_factory=lambda _method: context,
        heartbeat_interval_seconds=15,
        monotonic=lambda: next(monotonic_values),
        utc_now=lambda: TARGET,
    )

    result = runner.run(
        PremarketWorkerRequest(
            market_date_et=MARKET_DATE,
            target_as_of=TARGET,
            symbols=("SPY",),
        ),
        hard_deadline_at=TARGET + timedelta(minutes=3),
        heartbeat=heartbeats.append,
    )

    assert result.state == "succeeded"
    assert heartbeats == [TARGET]


def test_child_returns_only_allowlisted_fields_and_never_touches_database(
    monkeypatch,
):
    calls = []

    class FakeFetcher:
        enabled = True
        priority = 2

        def get_premarket(self, symbol, *, target_date, as_of):
            calls.append((symbol, target_date, as_of))
            return {
                "symbol": symbol,
                "requested_as_of": as_of.isoformat(),
                "market_date": target_date.isoformat(),
                "as_of": (as_of - timedelta(minutes=1)).isoformat(),
                "price": 101.0,
                "previous_close": 100.0,
                "previous_close_date": "2026-07-23",
                "pct_change": 1.0,
                "_status": "ready",
                "_reason": None,
                "_secret_sdk_payload": "must-not-cross-pipe",
            }

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(
        "data_provider.moomoo_fetcher.MoomooFetcher",
        FakeFetcher,
    )
    monkeypatch.setattr(
        "src.storage.get_db",
        lambda: (_ for _ in ()).throw(AssertionError("child touched DB")),
    )
    pipe = _PipeEndpoint()

    _moomoo_prefetch_worker(pipe, _request().to_wire())

    assert len(pipe.sent) == 3
    observation = pipe.sent[0]["observation"]
    assert set(observation) == {
        "symbol",
        "market_date_et",
        "requested_as_of",
        "evidence_as_of",
        "price",
        "previous_close",
        "previous_close_date",
        "pct_change",
        "status",
        "reason",
        "source",
    }
    assert "secret" not in repr(pipe.sent).lower()
    assert calls[-1] == "closed"


def test_child_redacts_provider_exception_text(monkeypatch):
    class FailingFetcher:
        enabled = True
        priority = 2

        def get_premarket(self, *_args, **_kwargs):
            raise RuntimeError("secret-account-and-url")

        def close(self):
            return None

    monkeypatch.setattr(
        "data_provider.moomoo_fetcher.MoomooFetcher",
        FailingFetcher,
    )
    pipe = _PipeEndpoint()

    _moomoo_prefetch_worker(
        pipe,
        PremarketWorkerRequest(
            market_date_et=MARKET_DATE,
            target_as_of=TARGET,
            symbols=("SPY",),
        ).to_wire(),
    )

    assert pipe.sent[0]["observation"]["reason"] == "provider_request_failed"
    assert "secret-account-and-url" not in repr(pipe.sent)


def test_constructor_rejects_unbounded_poll_or_heartbeat_settings():
    with pytest.raises(ValueError):
        SpawnedMoomooPrefetchRunner(poll_interval_seconds=0)
    with pytest.raises(ValueError):
        SpawnedMoomooPrefetchRunner(heartbeat_interval_seconds=0)


def test_cancellation_before_process_registration_cannot_be_cleared_by_run():
    context = _FakeContext((_message("completed"),))
    runner = SpawnedMoomooPrefetchRunner(
        context_factory=lambda _method: context,
        utc_now=lambda: TARGET,
    )
    runner.cancel_active()

    cancelled = runner.run(
        PremarketWorkerRequest(
            market_date_et=MARKET_DATE,
            target_as_of=TARGET,
            symbols=("SPY",),
        ),
        hard_deadline_at=TARGET + timedelta(minutes=1),
    )

    assert cancelled.state == "failed"
    assert cancelled.error_code == "scheduler_stopped"
    assert context.process.started is False


def test_real_spawn_process_completes_and_is_reaped():
    before = {child.pid for child in multiprocessing.active_children()}
    now = datetime.now(UTC)
    request = PremarketWorkerRequest(
        market_date_et=now.astimezone(
            ZoneInfo("America/New_York")
        ).date(),
        target_as_of=now,
        symbols=("SPY",),
    )
    runner = SpawnedMoomooPrefetchRunner(
        worker_target=_spawn_success_worker,
    )

    result = runner.run(
        request,
        hard_deadline_at=now + timedelta(seconds=5),
    )

    assert result.state == "succeeded"
    assert result.exit_code == 0
    assert [item["symbol"] for item in result.observations] == ["SPY"]
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_real_spawn_hang_is_killed_without_a_zombie():
    before = {child.pid for child in multiprocessing.active_children()}
    now = datetime.now(UTC)
    request = PremarketWorkerRequest(
        market_date_et=now.astimezone(
            ZoneInfo("America/New_York")
        ).date(),
        target_as_of=now,
        symbols=("SPY",),
    )
    runner = SpawnedMoomooPrefetchRunner(
        worker_target=_spawn_hanging_worker,
        poll_interval_seconds=0.02,
        join_timeout_seconds=0.2,
    )

    result = runner.run(
        request,
        hard_deadline_at=now + timedelta(seconds=0.25),
    )

    assert result.state == "timed_out"
    assert result.error_code == "worker_timeout"
    assert result.exit_code in {-15, -9}
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_cancel_during_real_active_child_reports_shutdown_and_reaps():
    before = {child.pid for child in multiprocessing.active_children()}
    now = datetime.now(UTC)
    request = PremarketWorkerRequest(
        market_date_et=now.astimezone(
            ZoneInfo("America/New_York")
        ).date(),
        target_as_of=now,
        symbols=("SPY",),
    )
    runner = SpawnedMoomooPrefetchRunner(
        worker_target=_spawn_hanging_worker,
        poll_interval_seconds=0.02,
        join_timeout_seconds=0.2,
    )
    result_holder = {}
    thread = threading.Thread(
        target=lambda: result_holder.setdefault(
            "result",
            runner.run(
                request,
                hard_deadline_at=now + timedelta(seconds=5),
            ),
        )
    )
    thread.start()
    wait_deadline = time.monotonic() + 2
    while time.monotonic() < wait_deadline:
        with runner._active_lock:
            active = runner._active_process
        if active is not None and active.is_alive():
            break
        time.sleep(0.01)
    else:
        raise AssertionError("spawned child did not become active")

    runner.cancel_active()
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert result_holder["result"].state == "failed"
    assert result_holder["result"].error_code == "scheduler_stopped"
    assert {child.pid for child in multiprocessing.active_children()} == before
