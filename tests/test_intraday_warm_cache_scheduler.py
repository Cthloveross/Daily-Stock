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
    INTRADAY_WARM_INTERVAL_FLOOR_SECONDS,
    IntradayWarmCacheScheduler,
    IntradayWarmTickResult,
    execute_intraday_warm_tick,
)

UTC = timezone.utc
# 2026-07-25 周六（休市）；2026-07-28 周二。ET 为 EDT（UTC-4）。
_SATURDAY_REGULAR_HOURS = datetime(2026, 7, 25, 14, 30, tzinfo=UTC)  # 周六 10:30 ET
_TUESDAY_NIGHT = datetime(2026, 7, 28, 7, 0, tzinfo=UTC)  # 周二 03:00 ET（04:00 前）
_TUESDAY_LATE = datetime(2026, 7, 29, 0, 30, tzinfo=UTC)  # 周二 20:30 ET（20:00 后）
_TUESDAY_PREMARKET = datetime(2026, 7, 28, 8, 30, tzinfo=UTC)  # 04:30 ET（窗口外）
_TUESDAY_PRE_OPEN = datetime(2026, 7, 28, 13, 10, tzinfo=UTC)  # 09:10 ET（盘前，窗口内）
_TUESDAY_REGULAR = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)  # 10:30 ET（窗口内）
_TUESDAY_LATE_CLOSE = datetime(2026, 7, 28, 20, 10, tzinfo=UTC)  # 16:10 ET（收盘后 10 分钟，窗口内）
_TUESDAY_AFTERHOURS = datetime(2026, 7, 28, 23, 30, tzinfo=UTC)  # 19:30 ET（延长时段但窗口外）


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


def test_tick_warms_only_inside_the_0900_1615_et_window():
    """预热窗口＝09:00–16:15 ET（对抗复核收窄）：窗口内预热，延长时段
    的其余部分（凌晨盘前 / 晚间盘后）idle 零请求，防止无人观看时自主
    消耗 30/日深扫额度与假日空转。"""

    for now, expected_state in (
        (_TUESDAY_PRE_OPEN, "premarket"),
        (_TUESDAY_REGULAR, "regular"),
        (_TUESDAY_LATE_CLOSE, "afterhours"),
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

    # 延长时段但窗口外：状态非 closed，仍必须 idle 且零调用。
    for now, expected_state in (
        (_TUESDAY_PREMARKET, "premarket"),
        (_TUESDAY_AFTERHOURS, "afterhours"),
    ):
        calls = []
        result = execute_intraday_warm_tick(
            warm_runner=lambda: calls.append("warm") or _warmed_summary(),
            now=now,
        )
        assert result.action == "idle"
        assert result.state == expected_state
        assert calls == []


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


def test_fastapi_lifespan_starts_and_stops_warm_scheduler(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FakeWarmScheduler:
        def __init__(self, tick, *, interval_seconds):
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
