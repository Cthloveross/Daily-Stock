# -*- coding: utf-8 -*-
"""Regression tests for bounded Moomoo OpenD runtime behavior."""
from __future__ import annotations

import logging
import builtins
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from api.v1.endpoints import system_config
from data_provider import moomoo_options
from data_provider.moomoo_fetcher import MoomooFetcher
from src.breakout import live_runner
from src.journal.brokers import moomoo_live
from src.services import moomoo_runtime


class _FakeQuoteContext:
    def __init__(self, status: str) -> None:
        self.status = status
        self.close_calls = 0
        self.sync_query_connect_timeouts = []

    def set_sync_query_connect_timeout(self, timeout: float) -> None:
        self.sync_query_connect_timeouts.append(timeout)

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture(autouse=True)
def _reset_sdk_runtime_config(monkeypatch):
    """Keep once-per-process configuration tests independent of test order."""
    monkeypatch.setattr(moomoo_runtime, "_sdk_config_result", None)


def _install_fake_quote_sdk(monkeypatch, *, status: str):
    module = ModuleType("moomoo")
    created = []
    console_values = []
    daemon_values = []

    class ContextStatus:
        READY = "READY"

    class SysConfig:
        @staticmethod
        def enable_console_log(value) -> None:
            console_values.append(value)

        @staticmethod
        def set_all_thread_daemon(value) -> None:
            daemon_values.append(value)

    def open_quote_context(**kwargs):
        ctx = _FakeQuoteContext(status)
        ctx.kwargs = kwargs
        created.append(ctx)
        return ctx

    module.ContextStatus = ContextStatus
    module.SysConfig = SysConfig
    module.OpenQuoteContext = open_quote_context
    monkeypatch.setitem(sys.modules, "moomoo", module)
    return created, console_values, daemon_values


def test_offline_tcp_probe_prevents_sdk_context_construction(monkeypatch) -> None:
    created, _, _ = _install_fake_quote_sdk(monkeypatch, status="READY")
    monkeypatch.setattr(moomoo_runtime, "probe_opend_tcp", lambda *_, **__: False)

    with pytest.raises(moomoo_runtime.MoomooRuntimeError, match="not reachable"):
        moomoo_runtime.create_ready_quote_context("127.0.0.1", 11111)

    assert created == []


def test_async_quote_context_timeout_closes_context(monkeypatch) -> None:
    created, console_values, daemon_values = _install_fake_quote_sdk(
        monkeypatch,
        status="WAIT_RECONNECT",
    )
    monkeypatch.setattr(moomoo_runtime, "probe_opend_tcp", lambda *_, **__: True)

    with pytest.raises(moomoo_runtime.MoomooRuntimeError, match="did not become READY"):
        moomoo_runtime.create_ready_quote_context(
            "127.0.0.1",
            11111,
            ready_timeout=0,
        )

    assert len(created) == 1
    assert created[0].kwargs["is_async_connect"] is True
    assert created[0].close_calls == 1
    assert console_values == [False]
    assert daemon_values == [True]


def test_sdk_runtime_configuration_is_thread_safe_and_once_only(monkeypatch) -> None:
    module = ModuleType("moomoo")
    console_values = []
    daemon_values = []

    class SysConfig:
        @staticmethod
        def enable_console_log(value) -> None:
            console_values.append(value)

        @staticmethod
        def set_all_thread_daemon(value) -> None:
            daemon_values.append(value)

    module.SysConfig = SysConfig
    monkeypatch.setitem(sys.modules, "moomoo", module)

    common_module = ModuleType("moomoo.common")
    logger_module = ModuleType("moomoo.common.ft_logger")
    sdk_logger = SimpleNamespace(
        file_level=logging.DEBUG,
        fileHandler=SimpleNamespace(backupCount=20),
    )
    logger_module.logger = sdk_logger
    monkeypatch.setitem(sys.modules, "moomoo.common", common_module)
    monkeypatch.setitem(sys.modules, "moomoo.common.ft_logger", logger_module)

    start = threading.Barrier(12)
    results = []

    def configure() -> None:
        start.wait()
        results.append(moomoo_runtime.configure_moomoo_sdk_runtime())

    threads = [threading.Thread(target=configure) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert results == [True] * 12
    assert console_values == [False]
    assert daemon_values == [True]
    assert sdk_logger.file_level == logging.WARNING
    assert sdk_logger.fileHandler.backupCount == 3


def test_sdk_file_logging_remains_best_effort_when_public_api_changes(
    monkeypatch,
) -> None:
    module = ModuleType("moomoo")
    daemon_calls = []

    class SysConfig:
        @staticmethod
        def enable_console_log(value) -> None:
            pass

        @staticmethod
        def set_all_thread_daemon(value) -> None:
            daemon_calls.append(value)
            raise AttributeError("renamed in future SDK")

    module.SysConfig = SysConfig
    monkeypatch.setitem(sys.modules, "moomoo", module)

    common_module = ModuleType("moomoo.common")
    logger_module = ModuleType("moomoo.common.ft_logger")
    sdk_logger = SimpleNamespace(
        file_level=logging.DEBUG,
        fileHandler=SimpleNamespace(backupCount=20),
    )
    logger_module.logger = sdk_logger
    monkeypatch.setitem(sys.modules, "moomoo.common", common_module)
    monkeypatch.setitem(sys.modules, "moomoo.common.ft_logger", logger_module)

    assert not moomoo_runtime.configure_moomoo_sdk_runtime()
    assert not moomoo_runtime.configure_moomoo_sdk_runtime()
    assert daemon_calls == [True]
    assert sdk_logger.file_level == logging.WARNING
    assert sdk_logger.fileHandler.backupCount == 3


def test_ready_quote_context_is_returned_and_preflight_closes_it(monkeypatch) -> None:
    created, _, _ = _install_fake_quote_sdk(monkeypatch, status="READY")
    monkeypatch.setattr(moomoo_runtime, "probe_opend_tcp", lambda *_, **__: True)

    ctx = moomoo_runtime.create_ready_quote_context("127.0.0.1", 11111)

    assert ctx is created[0]
    assert ctx.close_calls == 0
    assert ctx.sync_query_connect_timeouts == [
        moomoo_runtime.DEFAULT_SYNC_QUERY_CONNECT_TIMEOUT_SECONDS
    ]

    moomoo_runtime.ensure_opend_ready("127.0.0.1", 11111)

    assert len(created) == 2
    assert created[1].close_calls == 1
    assert created[1].sync_query_connect_timeouts == [
        moomoo_runtime.DEFAULT_SYNC_QUERY_CONNECT_TIMEOUT_SECONDS
    ]
    ctx.close()


def test_ready_quote_context_fails_closed_without_sync_query_timeout(
    monkeypatch,
) -> None:
    module = ModuleType("moomoo")
    created = []

    class ContextStatus:
        READY = "READY"

    class ContextWithoutQueryTimeout:
        status = ContextStatus.READY

        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    def open_quote_context(**_kwargs):
        ctx = ContextWithoutQueryTimeout()
        created.append(ctx)
        return ctx

    module.ContextStatus = ContextStatus
    module.OpenQuoteContext = open_quote_context
    monkeypatch.setitem(sys.modules, "moomoo", module)
    monkeypatch.setattr(moomoo_runtime, "probe_opend_tcp", lambda *_, **__: True)
    monkeypatch.setattr(
        moomoo_runtime,
        "configure_moomoo_sdk_runtime",
        lambda: True,
    )

    with pytest.raises(
        moomoo_runtime.MoomooRuntimeError,
        match="cannot bound synchronous query reconnects",
    ):
        moomoo_runtime.create_ready_quote_context("127.0.0.1", 11111)

    assert len(created) == 1
    assert created[0].close_calls == 1


def test_quote_readiness_check_does_not_issue_sdk_query(monkeypatch) -> None:
    _install_fake_quote_sdk(monkeypatch, status="READY")
    ctx = SimpleNamespace(
        status="READY",
        get_global_state=Mock(side_effect=AssertionError("must not be called")),
    )

    assert moomoo_runtime.quote_context_is_ready(ctx)
    ctx.get_global_state.assert_not_called()


def test_tcp_probe_is_bounded_and_closes_socket(monkeypatch) -> None:
    fake_socket = Mock()
    fake_socket.__enter__ = Mock(return_value=fake_socket)
    fake_socket.__exit__ = Mock(return_value=False)
    create_connection = Mock(return_value=fake_socket)
    monkeypatch.setattr(moomoo_runtime.socket, "create_connection", create_connection)

    assert moomoo_runtime.probe_opend_tcp("127.0.0.1", 11111, timeout=0.25)
    create_connection.assert_called_once_with(("127.0.0.1", 11111), timeout=0.25)
    fake_socket.__exit__.assert_called_once()

    create_connection.side_effect = OSError("offline")
    assert not moomoo_runtime.probe_opend_tcp("127.0.0.1", 11111, timeout=0.25)


def test_moomoo_status_uses_tcp_only_without_context_or_manager(monkeypatch) -> None:
    module = ModuleType("moomoo")
    context_constructor = Mock(side_effect=AssertionError("must not be called"))
    module.OpenQuoteContext = context_constructor
    module.__version__ = "test-sdk"
    monkeypatch.setitem(sys.modules, "moomoo", module)
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_OPEND_HOST", "127.0.0.1")
    monkeypatch.setenv("MOOMOO_OPEND_PORT", "11111")
    tcp_probe = Mock(return_value=True)
    monkeypatch.setattr(system_config, "probe_opend_tcp", tcp_probe)

    import data_provider.base as base_module

    manager_constructor = Mock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr(base_module, "DataFetcherManager", manager_constructor)

    result = system_config.get_moomoo_status()

    assert result["connected"] is True
    assert result["sdk_installed"] is True
    assert result["sdk_version"] == "test-sdk"
    assert result["read_only"] is True
    assert result["probe_level"] == "tcp"
    tcp_probe.assert_called_once_with("127.0.0.1", 11111)
    context_constructor.assert_not_called()
    manager_constructor.assert_not_called()


def test_disabled_moomoo_status_keeps_read_only_probe_metadata(monkeypatch) -> None:
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "false")

    result = system_config.get_moomoo_status()

    assert result["enabled"] is False
    assert result["connected"] is False
    assert result["read_only"] is True
    assert result["probe_level"] == "tcp"


def test_market_fetcher_uses_bounded_context_factory(monkeypatch) -> None:
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher.host = "127.0.0.1"
    fetcher.port = 11111
    fetcher.enabled = True
    fetcher._sdk_ok = True
    fetcher._ctx = None
    fetcher._ctx_lock = threading.RLock()
    ctx = object()
    factory = Mock(return_value=ctx)
    monkeypatch.setattr("data_provider.moomoo_fetcher.create_ready_quote_context", factory)

    assert fetcher._get_ctx() is ctx
    factory.assert_called_once_with(host="127.0.0.1", port=11111)


def test_market_fetcher_shelves_when_optional_sdk_initialization_fails(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "moomoo":
            raise PermissionError("SDK logger directory is not writable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    fetcher = MoomooFetcher()

    assert fetcher._sdk_ok is False
    assert fetcher.priority == 99


def test_options_fetcher_uses_bounded_context_factory(monkeypatch) -> None:
    module = ModuleType("moomoo")
    module.OpenQuoteContext = object
    monkeypatch.setitem(sys.modules, "moomoo", module)
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setattr(moomoo_options, "_ctx_singleton", None)
    ctx = object()
    factory = Mock(return_value=ctx)
    monkeypatch.setattr(moomoo_options, "create_ready_quote_context", factory)

    assert moomoo_options._get_ctx() is ctx
    factory.assert_called_once_with(host="127.0.0.1", port=11111)

    monkeypatch.setattr(moomoo_options, "_ctx_singleton", None)


def test_breakout_runner_uses_bounded_context_factory(monkeypatch) -> None:
    module = ModuleType("moomoo")

    class CurKlineHandlerBase:
        pass

    module.CurKlineHandlerBase = CurKlineHandlerBase
    module.RET_OK = 0
    module.SubType = SimpleNamespace(K_1M="K_1M")
    module.Session = SimpleNamespace(ALL="ALL")
    monkeypatch.setitem(sys.modules, "moomoo", module)

    class FakeContext:
        def set_handler(self, handler) -> None:
            self.handler = handler

        def subscribe(self, *args, **kwargs):
            return 0, "ok"

        def unsubscribe_all(self) -> None:
            pass

        def close(self) -> None:
            pass

    ctx = FakeContext()
    factory = Mock(return_value=ctx)
    monkeypatch.setattr(live_runner, "create_ready_quote_context", factory)
    runner = live_runner.LiveBreakoutRunner(tickers=["AAPL"], on_signal=lambda _: None)

    runner.start()

    assert runner._ctx is ctx
    assert runner._subscribed.is_set()
    factory.assert_called_once_with(host="127.0.0.1", port=11111)
    runner.stop()
    assert not runner._subscribed.is_set()


@pytest.mark.parametrize("failure_point", ["handler", "subscribe_return", "subscribe_raise"])
def test_breakout_start_failure_closes_context_and_stays_unhealthy(
    monkeypatch,
    failure_point: str,
) -> None:
    module = ModuleType("moomoo")

    class CurKlineHandlerBase:
        pass

    module.CurKlineHandlerBase = CurKlineHandlerBase
    module.RET_OK = 0
    module.SubType = SimpleNamespace(K_1M="K_1M")
    module.Session = SimpleNamespace(ALL="ALL")
    monkeypatch.setitem(sys.modules, "moomoo", module)

    class FakeContext:
        def __init__(self) -> None:
            self.close_calls = 0

        def set_handler(self, handler) -> None:
            if failure_point == "handler":
                raise RuntimeError("handler failed")
            self.handler = handler

        def subscribe(self, *args, **kwargs):
            if failure_point == "subscribe_raise":
                raise RuntimeError("subscribe raised")
            if failure_point == "subscribe_return":
                return 1, "subscribe rejected"
            return 0, "ok"

        def close(self) -> None:
            self.close_calls += 1

    ctx = FakeContext()
    monkeypatch.setattr(
        live_runner,
        "create_ready_quote_context",
        Mock(return_value=ctx),
    )
    monkeypatch.setattr(live_runner, "probe_opend_tcp", Mock(return_value=True))
    monkeypatch.setattr(
        live_runner,
        "quote_context_is_ready",
        Mock(return_value=True),
    )
    runner = live_runner.LiveBreakoutRunner(tickers=["AAPL"], on_signal=lambda _: None)

    with pytest.raises(RuntimeError):
        runner.start()

    assert ctx.close_calls == 1
    assert runner._ctx is None
    assert not runner._subscribed.is_set()
    assert not runner._running.is_set()
    assert not runner._ctx_alive()


def test_breakout_health_requires_successful_subscription(monkeypatch) -> None:
    runner = live_runner.LiveBreakoutRunner(tickers=["AAPL"], on_signal=lambda _: None)
    runner._ctx = object()
    monkeypatch.setattr(live_runner, "probe_opend_tcp", Mock(return_value=True))
    monkeypatch.setattr(
        live_runner,
        "quote_context_is_ready",
        Mock(return_value=True),
    )

    assert not runner._ctx_alive()

    runner._subscribed.set()
    assert runner._ctx_alive()


def test_trade_context_runs_bounded_preflight_first(monkeypatch) -> None:
    module = ModuleType("moomoo")
    events = []

    class OpenSecTradeContext:
        def __init__(self, **kwargs) -> None:
            events.append(("trade", kwargs))

    module.OpenSecTradeContext = OpenSecTradeContext
    module.SecurityFirm = SimpleNamespace(FUTUINC="FUTUINC")
    module.TrdMarket = SimpleNamespace(US="US")
    monkeypatch.setitem(sys.modules, "moomoo", module)
    monkeypatch.setattr(
        moomoo_live,
        "ensure_opend_ready",
        lambda **kwargs: events.append(("preflight", kwargs)),
    )

    ctx = moomoo_live._ctx_open("127.0.0.1", 11111, "US")

    assert isinstance(ctx, OpenSecTradeContext)
    assert events[0] == (
        "preflight",
        {"host": "127.0.0.1", "port": 11111},
    )
    assert events[1][0] == "trade"


def test_trade_context_is_not_created_when_preflight_fails(monkeypatch) -> None:
    module = ModuleType("moomoo")
    trade_constructor = Mock(side_effect=AssertionError("must not be called"))
    module.OpenSecTradeContext = trade_constructor
    module.SecurityFirm = SimpleNamespace(FUTUINC="FUTUINC")
    module.TrdMarket = SimpleNamespace(US="US")
    monkeypatch.setitem(sys.modules, "moomoo", module)

    def fail_preflight(**kwargs) -> None:
        raise moomoo_runtime.MoomooRuntimeError("offline")

    monkeypatch.setattr(moomoo_live, "ensure_opend_ready", fail_preflight)

    with pytest.raises(moomoo_live.MoomooLiveError, match="preflight failed"):
        moomoo_live._ctx_open("127.0.0.1", 11111, "US")

    trade_constructor.assert_not_called()


# ---------------------------------------------------------------------------
# Shared consecutive-failure circuit breaker (fake clock, fully deterministic)
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(clock: _FakeClock) -> moomoo_runtime.MoomooCircuitBreaker:
    return moomoo_runtime.MoomooCircuitBreaker(
        failure_threshold=3,
        cooldown_seconds=60.0,
        probe_ttl_seconds=30.0,
        clock=clock,
    )


def test_breaker_opens_after_three_consecutive_failures_and_fails_fast() -> None:
    clock = _FakeClock()
    breaker = _breaker(clock)

    breaker.record_failure("timeout #1")
    breaker.check()  # two failures: still closed
    breaker.record_failure("timeout #2")
    breaker.check()
    breaker.record_failure("timeout #3")

    assert breaker.is_open() is True
    with pytest.raises(moomoo_runtime.MoomooCircuitOpenError):
        breaker.check()
    # Fail fast for the whole cooldown window.
    clock.advance(59.9)
    with pytest.raises(moomoo_runtime.MoomooCircuitOpenError):
        breaker.check()


def test_breaker_success_resets_the_consecutive_counter() -> None:
    clock = _FakeClock()
    breaker = _breaker(clock)

    breaker.record_failure("timeout")
    breaker.record_failure("timeout")
    breaker.record_success()
    breaker.record_failure("timeout")
    breaker.record_failure("timeout")

    # Never three in a row: still closed.
    breaker.check()
    assert breaker.is_open() is False


def test_breaker_admits_exactly_one_probe_after_cooldown() -> None:
    clock = _FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure("timeout")

    clock.advance(60.0)
    breaker.check()  # first caller becomes the half-open probe
    with pytest.raises(moomoo_runtime.MoomooCircuitOpenError, match="probe"):
        breaker.check()  # concurrent caller still fails fast

    breaker.record_success()  # probe succeeded: breaker closes
    breaker.check()
    assert breaker.is_open() is False


def test_breaker_failed_probe_restarts_the_full_cooldown() -> None:
    clock = _FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure("timeout")

    clock.advance(60.0)
    breaker.check()  # probe admitted
    breaker.record_failure("still wedged")

    with pytest.raises(moomoo_runtime.MoomooCircuitOpenError):
        breaker.check()
    clock.advance(59.9)
    with pytest.raises(moomoo_runtime.MoomooCircuitOpenError):
        breaker.check()
    clock.advance(0.2)
    breaker.check()  # next probe admitted after the fresh cooldown


def test_breaker_dangling_probe_expires_and_hands_off() -> None:
    """探针路径提前返回（未 record_*）时靠探针租约自愈，绝不卡死半开态。"""

    clock = _FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure("timeout")

    clock.advance(60.0)
    breaker.check()  # probe admitted but never reports back
    clock.advance(30.0)  # probe TTL expires
    breaker.check()  # a new caller takes over the probe slot


def test_transport_detail_classification_ignores_business_rejections() -> None:
    assert moomoo_runtime.is_transport_failure_detail("request timeout") is True
    assert moomoo_runtime.is_transport_failure_detail("连接超时") is True
    assert moomoo_runtime.is_transport_failure_detail("disconnected") is True
    assert moomoo_runtime.is_transport_failure_detail("Unknown stock") is False
    assert moomoo_runtime.is_transport_failure_detail("rate limited") is False
    assert moomoo_runtime.is_transport_failure_detail(None) is False


def test_breaker_is_tripped_reflects_outage_lifecycle():
    """G-31 看门狗观测 is_tripped：closed→False，连续失败达阈值→True，
    冷却到期仍 True（与 is_open() 的区别所在），仅探针成功闭合→False。"""

    clock = {"t": 0.0}
    breaker = moomoo_runtime.MoomooCircuitBreaker(
        failure_threshold=2, cooldown_seconds=10.0, clock=lambda: clock["t"]
    )
    assert breaker.is_tripped is False
    breaker.record_failure("connection lost")
    assert breaker.is_tripped is False
    breaker.record_failure("connection lost")
    assert breaker.is_tripped is True
    # 观测多次不改变状态、不占探针。
    assert breaker.is_tripped is True
    clock["t"] = 11.0
    breaker.check()  # 冷却结束：本调用成为探针（放行）
    breaker.record_success()
    assert breaker.is_tripped is False
