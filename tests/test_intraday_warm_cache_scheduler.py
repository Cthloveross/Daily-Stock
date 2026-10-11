# -*- coding: utf-8 -*-
"""Deterministic tests for the intraday warm-cache scheduler.

覆盖：ET 时钟/工作日闸门（假时钟）、失败折叠（断路器打开等）不刷屏、
间隔地板钳制、host 生命周期与 FastAPI lifespan 的开/关接线。预热与端点
single-flight 的 join-not-duplicate 行为在
``api/v1/tests/test_intraday_top_endpoint.py`` 中以慢工厂模式验证。
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from api.app import create_app
from src.services.intraday_warm_cache_scheduler import (
    INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS,
    INTRADAY_WARM_INTERVAL_FLOOR_SECONDS,
    IntradayWarmCacheScheduler,
    IntradayWarmTickResult,
    crossed_5m_close_probe,
    execute_intraday_warm_tick,
    opening_pending_retry_delay,
    resolve_intraday_signal_symbols,
    seconds_until_next_5m_close_probe,
)

UTC = timezone.utc


def test_signal_universe_is_uncapped_watchlist_first_and_stock_list_fallback():
    from src.opportunities.p0_2m_calibration import DEFAULT_P0_UNIVERSE_69

    configured = SimpleNamespace(
        intraday_watchlist=[*DEFAULT_P0_UNIVERSE_69, "us.tsla", "HK00700"],
        stock_list=["SPY"],
    )
    assert resolve_intraday_signal_symbols(configured) == DEFAULT_P0_UNIVERSE_69
    configured.intraday_watchlist = []
    configured.stock_list = ",".join(DEFAULT_P0_UNIVERSE_69)
    assert resolve_intraday_signal_symbols(configured) == DEFAULT_P0_UNIVERSE_69
# 2026-07-25 周六（休市）；2026-07-28 周二。ET 为 EDT（UTC-4）。
_SATURDAY_REGULAR_HOURS = datetime(2026, 7, 25, 14, 30, tzinfo=UTC)  # 周六 10:30 ET
_TUESDAY_NIGHT = datetime(2026, 7, 28, 7, 0, tzinfo=UTC)  # 周二 03:00 ET（04:00 前）
_TUESDAY_LATE = datetime(2026, 7, 29, 0, 30, tzinfo=UTC)  # 周二 20:30 ET（20:00 后）
_TUESDAY_PREMARKET = datetime(2026, 7, 28, 8, 30, tzinfo=UTC)  # 04:30 ET（窗口内）
_TUESDAY_PRE_OPEN = datetime(2026, 7, 28, 13, 10, tzinfo=UTC)  # 09:10 ET（盘前，窗口内）
_TUESDAY_REGULAR = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)  # 10:30 ET（窗口内）
_TUESDAY_LATE_CLOSE = datetime(2026, 7, 28, 20, 10, tzinfo=UTC)  # 16:10 ET（收盘后 10 分钟，窗口内）
_TUESDAY_AFTERHOURS = datetime(2026, 7, 28, 23, 30, tzinfo=UTC)  # 19:30 ET（窗口内）


def _warmed_summary():
    return {
        "led_flight": True,
        "generated_in_seconds": 1.5,
        "generated_by": "warm_scheduler",
    }


def test_tick_idles_outside_the_weekday_premarket_to_afterhours_window():
    calls = []
    for now in (_SATURDAY_REGULAR_HOURS, _TUESDAY_NIGHT, _TUESDAY_LATE):
        result = execute_intraday_warm_tick(
            warm_runner=lambda: calls.append("warm") or _warmed_summary(),
            now=now,
        )
        assert result.action == "idle"
        assert result.state == "closed"
        assert result.error_code is None
    # 窗口外绝不触发预热，也就绝不发供应商请求。
    assert calls == []


def test_tick_warms_full_0400_2000_extended_session():
    """P0-X 需要完整盘前/盘后覆盖；XNYS 假日仍由日历零请求。"""

    for now, expected_state in (
        (_TUESDAY_PRE_OPEN, "premarket"),
        (_TUESDAY_REGULAR, "regular"),
        (_TUESDAY_LATE_CLOSE, "afterhours"),
        (_TUESDAY_PREMARKET, "premarket"),
        (_TUESDAY_AFTERHOURS, "afterhours"),
    ):
        calls = []
        result = execute_intraday_warm_tick(
            warm_runner=lambda: calls.append("warm") or _warmed_summary(),
            now=now,
        )
        assert result.action == "warmed"
        assert result.state == expected_state
        assert result.led_flight is True
        assert result.generated_in_seconds == 1.5
        assert calls == ["warm"]

def test_tick_folds_warm_failure_and_keeps_cadence():
    """断路器打开等失败只折叠为 error_code，绝不抛出；下一 tick 照常预热。"""

    calls = 0

    def breaker_open_fast_fail():
        nonlocal calls
        calls += 1
        raise RuntimeError("moomoo rpc breaker open")

    first = execute_intraday_warm_tick(
        warm_runner=breaker_open_fast_fail, now=_TUESDAY_REGULAR
    )
    second = execute_intraday_warm_tick(
        warm_runner=breaker_open_fast_fail, now=_TUESDAY_REGULAR
    )

    assert first.action == second.action == "failed"
    assert first.error_code == "RuntimeError"
    assert first.state == "regular"
    assert calls == 2


def test_tick_uses_xnys_holiday_and_half_day_close_without_guessing():
    calls = []
    holiday = execute_intraday_warm_tick(
        warm_runner=lambda: calls.append("holiday") or _warmed_summary(),
        now=datetime(2026, 9, 7, 14, 0, tzinfo=UTC),  # Labor Day 10:00 ET
    )
    assert holiday.action == "idle"
    assert holiday.state == "closed"
    assert calls == []

    before_half_day_end = execute_intraday_warm_tick(
        warm_runner=lambda: calls.append("half-day") or _warmed_summary(),
        now=datetime(2026, 11, 27, 18, 10, tzinfo=UTC),  # 13:10 ET
    )
    after_half_day_end = execute_intraday_warm_tick(
        warm_runner=lambda: calls.append("too-late") or _warmed_summary(),
        now=datetime(2026, 11, 27, 18, 16, tzinfo=UTC),  # 13:16 ET
    )
    assert before_half_day_end.action == "warmed"
    assert before_half_day_end.state == "afterhours"
    # 半日市 13:00 后属于 afterhours；P0-X 继续到 20:00。
    assert after_half_day_end.action == "warmed"
    assert after_half_day_end.state == "afterhours"
    assert calls == ["half-day", "too-late"]


def test_tick_fails_closed_when_xnys_clock_is_unavailable():
    result = execute_intraday_warm_tick(
        warm_runner=_warmed_summary,
        now=_TUESDAY_REGULAR,
        session_clock_reader=lambda _now: SimpleNamespace(
            state="unavailable",
            phase="unavailable",
            close_minute_et=None,
            unavailable_reason="xnys_schedule_unavailable:RuntimeError",
        ),
    )
    assert result.action == "failed"
    assert result.state == "closed"
    assert result.error_code == "xnys_schedule_unavailable:RuntimeError"


def test_tick_propagates_opening_pending_count_for_fast_retry():
    result = execute_intraday_warm_tick(
        warm_runner=lambda: {
            **_warmed_summary(),
            "opening_pending_count": 2,
        },
        now=datetime(2026, 8, 31, 13, 35, 5, tzinfo=UTC),
    )
    assert result.action == "warmed"
    assert result.opening_pending_count == 2


def test_page_warming_defers_only_during_the_opening_fast_window():
    calls = []
    for minute in (30, 31, 32, 33, 34, 35):
        result = execute_intraday_warm_tick(
            warm_runner=lambda: calls.append(minute) or _warmed_summary(),
            now=datetime(2026, 8, 31, 13, minute, 5, tzinfo=UTC),
            defer_during_opening=True,
        )
        assert result.action == "idle"
        assert result.state == "regular"
        assert result.opening_pending_count == 0
    assert calls == []

    # The independent fast host still runs during the same checkpoint.
    fast = execute_intraday_warm_tick(
        warm_runner=lambda: calls.append("fast") or _warmed_summary(),
        now=datetime(2026, 8, 31, 13, 33, 5, tzinfo=UTC),
    )
    resumed = execute_intraday_warm_tick(
        warm_runner=lambda: calls.append("page") or _warmed_summary(),
        now=datetime(2026, 8, 31, 13, 36, 0, tzinfo=UTC),
        defer_during_opening=True,
    )
    assert fast.action == resumed.action == "warmed"
    assert calls == ["fast", "page"]


def test_opening_minute_failure_uses_same_bounded_retry_marker():
    def failed_runner():
        raise RuntimeError("provider unavailable")

    opening = execute_intraday_warm_tick(
        warm_runner=failed_runner,
        now=datetime(2026, 8, 31, 13, 35, 5, tzinfo=UTC),
    )
    later = execute_intraday_warm_tick(
        warm_runner=failed_runner,
        now=datetime(2026, 8, 31, 13, 36, 5, tzinfo=UTC),
    )

    assert opening.action == later.action == "failed"
    assert opening.opening_pending_count == 1
    assert later.opening_pending_count == 0


def test_tick_rejects_a_naive_clock():
    with pytest.raises(ValueError):
        execute_intraday_warm_tick(
            warm_runner=_warmed_summary,
            now=datetime(2026, 7, 28, 14, 30),  # naive → 拒绝，不猜时区
        )


def test_interval_floor_clamps_values_below_the_lease():
    idle_tick = lambda: IntradayWarmTickResult(  # noqa: E731 - fixture tick
        action="idle",
        state="closed",
        led_flight=False,
        generated_in_seconds=None,
        error_code=None,
    )
    assert (
        IntradayWarmCacheScheduler(idle_tick, interval_seconds=10).interval_seconds
        == INTRADAY_WARM_INTERVAL_FLOOR_SECONDS
    )
    assert (
        IntradayWarmCacheScheduler(idle_tick, interval_seconds=0).interval_seconds
        == INTRADAY_WARM_INTERVAL_FLOOR_SECONDS
    )
    # 合法值保持原样，不被地板改写。
    assert (
        IntradayWarmCacheScheduler(idle_tick, interval_seconds=45).interval_seconds
        == 45.0
    )
    assert (
        IntradayWarmCacheScheduler(idle_tick).interval_seconds
        == INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS
        == 60.0
    )


def test_scheduler_aligns_to_every_completed_minute_plus_five_seconds():
    # 09:34:45 ET → 09:35:05 ET，不能机械等 180 秒。
    assert seconds_until_next_5m_close_probe(
        datetime(2026, 8, 31, 13, 34, 45, tzinfo=UTC),
        interval_seconds=180,
    ) == 20.0
    # 刚过 probe 后对齐下一分钟 +5s；严格 2m 层自行跳过无新窗口的 tick。
    assert seconds_until_next_5m_close_probe(
        datetime(2026, 8, 31, 13, 35, 6, tzinfo=UTC),
        interval_seconds=180,
    ) == 59.0
    assert seconds_until_next_5m_close_probe(
        datetime(2026, 8, 31, 13, 38, 6, tzinfo=UTC),
        interval_seconds=180,
    ) == 59.0


def test_scheduler_aligns_each_progressive_opening_minute_plus_five_seconds():
    # 09:31:06 ET → 09:32:05 ET；关键窗不在 09:31:51 启动无意义慢扫描。
    assert seconds_until_next_5m_close_probe(
        datetime(2026, 8, 31, 13, 31, 6, tzinfo=UTC),
        interval_seconds=180,
    ) == 59.0
    # 09:31:50 ET → 同一个 09:32:05 checkpoint。
    assert seconds_until_next_5m_close_probe(
        datetime(2026, 8, 31, 13, 31, 50, tzinfo=UTC),
        interval_seconds=180,
    ) == 15.0
    # 关键窗前仍由 5m probe 把循环准时带到 09:30:05。
    assert seconds_until_next_5m_close_probe(
        datetime(2026, 8, 31, 13, 29, 50, tzinfo=UTC),
        interval_seconds=180,
    ) == 15.0


def test_slow_tick_crossing_bar_probe_requires_immediate_fresh_retry():
    assert crossed_5m_close_probe(
        datetime(2026, 8, 31, 13, 34, 50, tzinfo=UTC),
        datetime(2026, 8, 31, 13, 35, 10, tzinfo=UTC),
    ) is True
    assert crossed_5m_close_probe(
        datetime(2026, 8, 31, 13, 35, 6, tzinfo=UTC),
        datetime(2026, 8, 31, 13, 35, 20, tzinfo=UTC),
    ) is False
    assert crossed_5m_close_probe(
        datetime(2026, 8, 31, 13, 31, 50, tzinfo=UTC),
        datetime(2026, 8, 31, 13, 32, 7, tzinfo=UTC),
    ) is True


def test_opening_pending_uses_four_bounded_two_second_retries():
    now = datetime(2026, 8, 31, 13, 35, 5, tzinfo=UTC)
    used = 0
    for expected_used in range(1, 5):
        delay, used = opening_pending_retry_delay(
            now,
            pending_count=1,
            retries_used=used,
            default_wait_seconds=180.0,
        )
        assert delay == 2.0
        assert used == expected_used

    delay, used = opening_pending_retry_delay(
        now,
        pending_count=1,
        retries_used=used,
        default_wait_seconds=180.0,
    )
    assert delay == 180.0
    assert used == 4


def test_opening_pending_retry_resets_when_bar_is_ready_and_stays_in_0935():
    delay, used = opening_pending_retry_delay(
        datetime(2026, 8, 31, 13, 35, 10, tzinfo=UTC),
        pending_count=0,
        retries_used=3,
        default_wait_seconds=180.0,
    )
    assert (delay, used) == (180.0, 0)


def test_each_progressive_checkpoint_can_use_bounded_pending_retry():
    for minute in (31, 32, 33, 34, 35):
        delay, used = opening_pending_retry_delay(
            datetime(2026, 8, 31, 13, minute, 5, tzinfo=UTC),
            pending_count=1,
            retries_used=0,
            default_wait_seconds=180.0,
        )
        assert (delay, used) == (2.0, 1)

    delay, used = opening_pending_retry_delay(
        datetime(2026, 8, 31, 13, 36, 0, tzinfo=UTC),
        pending_count=1,
        retries_used=0,
        default_wait_seconds=180.0,
    )
    assert (delay, used) == (180.0, 0)


def test_host_logs_one_info_per_state_change_and_recovery(caplog):
    scheduler = IntradayWarmCacheScheduler(
        lambda: IntradayWarmTickResult(
            action="idle",
            state="closed",
            led_flight=False,
            generated_in_seconds=None,
            error_code=None,
        )
    )
    failed = IntradayWarmTickResult(
        action="failed",
        state="regular",
        led_flight=False,
        generated_in_seconds=None,
        error_code="MoomooRpcBreakerOpen",
    )
    warmed = IntradayWarmTickResult(
        action="warmed",
        state="regular",
        led_flight=True,
        generated_in_seconds=2.0,
        error_code=None,
    )

    with caplog.at_level(
        logging.INFO, logger="src.services.intraday_warm_cache_scheduler"
    ):
        scheduler._log_transition(failed)
        scheduler._log_transition(failed)  # 同一指纹：不再记录
        scheduler._log_transition(warmed)  # 恢复：记录一次
        scheduler._log_transition(warmed)  # 稳态：不再记录

    failure_lines = [
        record
        for record in caplog.records
        if "warm failed" in record.getMessage()
    ]
    recovery_lines = [
        record
        for record in caplog.records
        if "warm loop active" in record.getMessage()
    ]
    assert len(failure_lines) == 1
    # 失败按 INFO 记录（quiet-unless-incident：断路器打开是预期状态）。
    assert failure_lines[0].levelno == logging.INFO
    assert len(recovery_lines) == 1


def test_daemon_loop_runs_tick_and_stops_cleanly():
    ticked = threading.Event()

    def tick():
        ticked.set()
        return IntradayWarmTickResult(
            action="idle",
            state="closed",
            led_flight=False,
            generated_in_seconds=None,
            error_code=None,
        )

    scheduler = IntradayWarmCacheScheduler(tick, interval_seconds=60)
    scheduler.start()
    assert ticked.wait(timeout=1)
    scheduler.stop(join_timeout_seconds=1)
    assert scheduler.running is False


def test_page_host_uses_its_interval_without_minute_catchup_or_opening_retry():
    waits = []
    scheduler = IntradayWarmCacheScheduler(
        lambda: IntradayWarmTickResult(
            action="warmed", state="regular", led_flight=True,
            generated_in_seconds=75.0, error_code=None,
            opening_pending_count=2,
        ),
        interval_seconds=180,
        align_to_minute_closes=False,
    )

    class StopAfterWait:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            waits.append(seconds)
            self.stopped = True

    scheduler._stop_event = StopAfterWait()
    scheduler._run()
    assert waits == [180.0]
    assert scheduler._opening_pending_retries == 0


def test_fastapi_lifespan_starts_and_stops_warm_scheduler(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FakeWarmScheduler:
        def __init__(
            self, tick, *, interval_seconds, channel_watch=None,
            channel_notifier=None, align_to_minute_closes=True,
        ):
            assert align_to_minute_closes is False
            calls.append(("init", callable(tick), interval_seconds))

        def start(self):
            calls.append(("start", True))

        def stop(self):
            calls.append(("stop", True))

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            intraday_refresh_scheduler_enabled=True,
            intraday_refresh_interval_seconds=45,
        ),
    )
    monkeypatch.setattr(
        "src.services.intraday_warm_cache_scheduler.IntradayWarmCacheScheduler",
        FakeWarmScheduler,
    )

    with TestClient(create_app(static_dir=tmp_path / "no-static")) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == [
        ("init", True, 45),
        ("start", True),
        ("stop", True),
    ]


def test_fastapi_lifespan_keeps_warm_scheduler_disabled_by_default(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(),  # 未配置任何 scheduler 开关 → 全部关闭
    )
    monkeypatch.setattr(
        "src.services.intraday_warm_cache_scheduler.IntradayWarmCacheScheduler",
        lambda *args, **kwargs: pytest.fail(
            "warm scheduler must stay off unless explicitly enabled"
        ),
    )

    app = create_app(static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert not hasattr(app.state, "intraday_warm_cache_scheduler")
        assert not hasattr(app.state, "intraday_signal_scheduler")


def test_lifespan_signal_clock_is_independent_of_blocked_page_warming(
    monkeypatch, tmp_path: Path,
):
    from src.opportunities.p0_2m_calibration import DEFAULT_P0_UNIVERSE_69

    calls = []
    hosts = {}
    received_symbols = []
    page_entered = threading.Event()
    release_page = threading.Event()

    class FakeScheduler:
        def __init__(self, tick, **kwargs):
            self.tick = tick
            self.name = kwargs.get("thread_name", "page")
            self.options = kwargs
            hosts[self.name] = self

        def start(self):
            calls.append(f"start:{self.name}")

        def stop(self):
            calls.append(f"stop:{self.name}")

    class FakeAlerter:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            calls.append("start:alerter")

        def stop(self):
            calls.append("stop:alerter")

        def observe(self, payload):
            calls.append("observe:page")

        def observe_opening(self, payload):
            calls.append("observe:opening")

        def observe_p0_2m(self, payload):
            return {"accepted": True}

    class FakeMonitor:
        def __init__(self, *args, **kwargs):
            received_symbols.append(tuple(args[0]))

        def start(self):
            calls.append("start:monitor")

        def stop(self):
            calls.append("stop:monitor")

        def scan_once(self):
            calls.append("scan:2m")
            return {"candidate_count": 1}

        def get_completed_1m_bars(self, symbol, *, as_of):
            return [], "MoomooFetcher"

        def health_snapshot(self):
            return {"stream_ready": True}

    def slow_page(payload_observer=None, payload_enricher=None):
        page_entered.set()
        assert release_page.wait(timeout=2)
        calls.append("scan:page")
        assert payload_enricher is None
        payload_observer({"candidates": []})
        return _warmed_summary()

    def opening(payload_observer=None, **kwargs):
        assert kwargs["target_symbols"] == DEFAULT_P0_UNIVERSE_69
        if kwargs.get("prewarm_only"):
            calls.append("prewarm:opening")
            return {"action": "idle", "candidate_count": 0}
        assert kwargs["current_1m_reader"].__self__.__class__ is FakeMonitor
        calls.append("scan:opening")
        payload_observer({"candidates": []})
        return {"pending_count": 0, "retryable_pending_count": 0}

    config = SimpleNamespace(
        intraday_refresh_scheduler_enabled=True,
        intraday_refresh_interval_seconds=180,
        intraday_alerts_enabled=True,
        intraday_p0_2m_enabled=True,
        intraday_watchlist=DEFAULT_P0_UNIVERSE_69,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr(
        "src.services.intraday_warm_cache_scheduler.IntradayWarmCacheScheduler",
        FakeScheduler,
    )
    monkeypatch.setattr(
        "src.services.intraday_warm_cache_scheduler.execute_intraday_warm_tick",
        lambda **kwargs: execute_intraday_warm_tick(now=_TUESDAY_REGULAR, **kwargs),
    )
    monkeypatch.setattr(
        "src.services.intraday_opportunity_alerter.IntradayOpportunityAlerter",
        FakeAlerter,
    )
    monkeypatch.setattr(
        "src.services.intraday_p0_2m_monitor.IntradayP02mMonitor", FakeMonitor,
    )
    monkeypatch.setattr(
        "api.v1.endpoints.opportunities.warm_default_intraday_top_scan", slow_page,
    )
    monkeypatch.setattr(
        "api.v1.endpoints.opportunities.warm_opening_impulse_scan", opening,
    )

    app = create_app(static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert received_symbols == [DEFAULT_P0_UNIVERSE_69]
        assert app.state.intraday_signal_symbols == DEFAULT_P0_UNIVERSE_69
        # Runtime config changes must not silently alter the subscribed/hash
        # identity while an existing lifespan is still running.
        config.intraday_watchlist = ("SPY",)
        fast = hosts["dsa-intraday-signal-clock"]
        page = hosts["page"]
        assert fast.options["interval_seconds"] == 60
        assert page.options["interval_seconds"] == 180
        assert page.options["align_to_minute_closes"] is False
        assert page.options.get("channel_notifier") is None

        page_thread = threading.Thread(target=page.tick)
        page_thread.start()
        try:
            assert page_entered.wait(timeout=1)
            result = fast.tick()
            assert result.action == "warmed"
            assert calls.count("scan:2m") == calls.count("scan:opening") == 1
            assert "scan:page" not in calls
        finally:
            release_page.set()
            page_thread.join(timeout=1)
        assert not page_thread.is_alive()
        assert calls.count("scan:page") == 1
        # The page completion cannot evaluate the same fast signals again.
        assert calls.count("scan:2m") == calls.count("scan:opening") == 1

    assert calls.index("stop:dsa-intraday-signal-clock") < calls.index("stop:page")
    assert calls.index("stop:page") < calls.index("stop:monitor") < calls.index("stop:alerter")
    assert not hasattr(app.state, "intraday_signal_scheduler")
    assert not hasattr(app.state, "intraday_warm_cache_scheduler")
    assert not hasattr(app.state, "intraday_p0_2m_monitor")
    assert not hasattr(app.state, "intraday_signal_symbols")


def test_channel_watchdog_notifies_on_down_and_recovery_once_each():
    """通道看门狗（G-31）：断开→通知一次，持续断开不重复，恢复→通知一次；
    watch/notifier 异常吞噬绝不进循环。idle tick 不评估。"""

    notices: list[str] = []
    down_flag = {"down": False}
    warmed = IntradayWarmTickResult(
        action="warmed", state="regular", led_flight=True,
        generated_in_seconds=1.0, error_code=None,
    )
    idle = IntradayWarmTickResult(
        action="idle", state="closed", led_flight=False,
        generated_in_seconds=None, error_code=None,
    )
    scheduler = IntradayWarmCacheScheduler(
        lambda: warmed,
        channel_watch=lambda: down_flag["down"],
        channel_notifier=notices.append,
    )
    # 直接驱动内部方法（循环行为由 daemon 测试覆盖）。
    scheduler._watch_channel()          # 基线：正常，无通知
    assert notices == []
    down_flag["down"] = True
    scheduler._watch_channel()          # 断开 → 通知
    scheduler._watch_channel()          # 仍断开 → 不重复
    assert len(notices) == 1 and "行情通道断开" in notices[0]
    down_flag["down"] = False
    scheduler._watch_channel()          # 恢复 → 通知
    assert len(notices) == 2 and "已恢复" in notices[1]
    # notifier 异常：吞噬不崩溃

    def bad_notify(_text: str) -> None:
        raise RuntimeError("boom")

    bad = IntradayWarmCacheScheduler(
        lambda: warmed,
        channel_watch=lambda: True,
        channel_notifier=bad_notify,
    )
    bad._watch_channel()                # 不抛
    # watch 异常：视为未知，不通知不崩溃

    def bad_watch() -> bool:
        raise RuntimeError("watch boom")

    silent = IntradayWarmCacheScheduler(
        lambda: warmed,
        channel_watch=bad_watch,
        channel_notifier=notices.append,
    )
    silent._watch_channel()
    assert len(notices) == 2
    # 未接线（默认 None）：零行为
    IntradayWarmCacheScheduler(lambda: idle)._watch_channel()
