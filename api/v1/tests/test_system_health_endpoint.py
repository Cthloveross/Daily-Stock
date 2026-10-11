# -*- coding: utf-8 -*-
"""分层健康端点：单层失败只降级该层，端点本身永不 500。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import opportunities, system_config


def _client(p0_monitor=None, *, signal_scheduler_running=True) -> TestClient:
    app = FastAPI()
    if p0_monitor is not None:
        app.state.intraday_p0_2m_monitor = p0_monitor
        app.state.intraday_signal_scheduler = SimpleNamespace(
            running=signal_scheduler_running,
        )
    app.include_router(system_config.router, prefix="/api/v1/system")
    return TestClient(app)


def _layer(body: dict, name: str) -> dict:
    return next(item for item in body["layers"] if item["layer"] == name)


def _stub_config(
    monkeypatch,
    *,
    premarket=True,
    outcome=True,
    intraday_alerts=False,
    intraday_warm=False,
    p0_enabled=False,
    p0_action=False,
    p0_shadow_mobile=False,
    p0_shadow_credentials=True,
):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            premarket_research_scheduler_enabled=premarket,
            opportunity_outcome_scheduler_enabled=outcome,
            intraday_alerts_enabled=intraday_alerts,
            intraday_refresh_scheduler_enabled=intraday_warm,
            intraday_p0_2m_enabled=p0_enabled,
            intraday_p0_2m_action_enabled=p0_action,
            intraday_p0_2m_shadow_mobile_enabled=p0_shadow_mobile,
            telegram_bot_token=(
                "test-token"
                if p0_shadow_mobile and p0_shadow_credentials
                else None
            ),
            telegram_chat_id=(
                "test-chat"
                if p0_shadow_mobile and p0_shadow_credentials
                else None
            ),
        ),
    )


def _stub_snapshots(monkeypatch, snapshots=()):
    monkeypatch.setattr(
        "src.opportunities.repository.list_snapshots",
        lambda *, limit: tuple(snapshots),
    )


def _stub_maintenance(monkeypatch, run=None):
    monkeypatch.setattr(
        "src.opportunities.maintenance_repository.get_latest_outcome_maintenance",
        lambda: run,
    )


def _stub_schedule(monkeypatch, coverage_through):
    """Pin the official economic schedule coverage so tests never date-rot.

    ``coverage_through=None`` simulates unusable/missing data files.
    """
    monkeypatch.setattr(
        "src.regime.official_schedule.get_cached_official_schedule",
        lambda: (
            None
            if coverage_through is None
            else SimpleNamespace(coverage_through=coverage_through)
        ),
    )


def _opening_health_env(monkeypatch) -> None:
    """Healthy transport baseline so tests isolate the cached opening layer."""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_ENABLED", "false")
    monkeypatch.setattr(
        "src.services.moomoo_runtime.probe_opend_tcp",
        lambda host, port: True,
    )
    monkeypatch.setitem(
        sys.modules,
        "moomoo",
        SimpleNamespace(OpenQuoteContext=object, __version__="test"),
    )
    _stub_config(
        monkeypatch,
        premarket=False,
        outcome=False,
        intraday_alerts=True,
        intraday_warm=True,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))


def _opening_readiness(
    *,
    target_count: int,
    cached_count: int,
    ready_count: int,
    provider_counts: dict[str, int],
    reason_code: str,
) -> dict:
    return {
        "market_date_et": "2026-08-31",
        "observed_at": "2026-08-31T13:15:00+00:00",
        "required_interval": "1m",
        "required_reference_sessions": 20,
        "requested_calendar_days": 45,
        "target_count": target_count,
        "cached_count": cached_count,
        "ready_count": ready_count,
        "provider_counts": provider_counts,
        "reason_code": reason_code,
        "current_stream_ready": True,
        "current_stream_reason": "ready",
    }


def test_all_moomoo_layers_disabled_when_opend_off(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(monkeypatch)
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))

    response = _client().get("/api/v1/system/health-layers")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "system-health-layers/1.0"
    assert _layer(body, "api_process")["state"] == "ok"
    assert _layer(body, "opend_tcp")["state"] == "disabled"
    assert _layer(body, "moomoo_sdk")["state"] == "disabled"
    assert _layer(body, "journal_refresh_config")["state"] == "disabled"
    # 无发布/无维护记录 + scheduler 开启 → degraded，而不是假 ok。
    assert _layer(body, "premarket_publication")["state"] == "degraded"
    assert _layer(body, "outcome_maintenance")["state"] == "degraded"
    assert body["overall"] == "degraded"


def test_opend_down_marks_layer_down_and_overall_down(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_ENABLED", "false")
    monkeypatch.setattr(
        "src.services.moomoo_runtime.probe_opend_tcp", lambda host, port: False
    )
    fake_sdk = SimpleNamespace(OpenQuoteContext=object, __version__="test")
    monkeypatch.setitem(sys.modules, "moomoo", fake_sdk)
    _stub_config(monkeypatch)
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))

    body = _client().get("/api/v1/system/health-layers").json()

    assert _layer(body, "opend_tcp")["state"] == "down"
    assert _layer(body, "moomoo_sdk")["state"] == "ok"
    assert body["overall"] == "down"


def test_journal_config_degraded_lists_specific_problems(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "false")
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_ENV", "SIMULATE")
    monkeypatch.setenv("MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET", "short")
    _stub_config(monkeypatch)
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))

    body = _client().get("/api/v1/system/health-layers").json()
    layer = _layer(body, "journal_refresh_config")

    assert layer["state"] == "degraded"
    assert "MOOMOO_JOURNAL_ENV" in layer["detail"]
    assert "32" in layer["detail"]
    # secret 内容绝不回显。
    assert "short" not in layer["detail"]


def test_snapshot_read_failure_degrades_only_that_layer(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "false")
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))

    def boom(*, limit):
        raise RuntimeError("db locked")

    monkeypatch.setattr("src.opportunities.repository.list_snapshots", boom)

    response = _client().get("/api/v1/system/health-layers")

    assert response.status_code == 200
    body = response.json()
    assert _layer(body, "premarket_publication")["state"] == "unknown"
    assert _layer(body, "api_process")["state"] == "ok"


def test_opening_v2_provider_disabled_when_alerts_are_disabled(monkeypatch):
    _opening_health_env(monkeypatch)
    _stub_config(
        monkeypatch,
        premarket=False,
        outcome=False,
        intraday_alerts=False,
        intraday_warm=True,
    )
    monkeypatch.setattr(
        opportunities,
        "get_opening_v2_provider_readiness",
        lambda: pytest.fail("disabled opening layer must not inspect readiness"),
    )

    response = _client().get("/api/v1/system/health-layers")

    assert response.status_code == 200
    layer = _layer(response.json(), "opening_v2_1m_provider")
    assert layer["state"] == "disabled"
    assert "INTRADAY_ALERTS_ENABLED=false" in layer["detail"]


def test_opening_v2_provider_down_when_warm_scheduler_is_disabled(monkeypatch):
    _opening_health_env(monkeypatch)
    _stub_config(
        monkeypatch,
        premarket=False,
        outcome=False,
        intraday_alerts=True,
        intraday_warm=False,
    )
    monkeypatch.setattr(
        opportunities,
        "get_opening_v2_provider_readiness",
        lambda: pytest.fail("misconfigured opening layer must not inspect cache"),
    )

    response = _client().get("/api/v1/system/health-layers")

    assert response.status_code == 200
    layer = _layer(response.json(), "opening_v2_1m_provider")
    assert layer["state"] == "down"
    assert "INTRADAY_REFRESH_SCHEDULER_ENABLED=false" in layer["detail"]


def test_opening_v2_provider_unknown_without_current_day_cache_and_never_fetches(
    monkeypatch,
):
    _opening_health_env(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_intraday_opening_1m_reference_cache",
        {},
    )
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda day: [])
    monkeypatch.setattr(
        opportunities,
        "_configured_intraday_pinned_tickers",
        lambda: [],
    )
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["TSLA"])
    monkeypatch.setattr(opportunities, "_opening_signal_symbols", ("TSLA",))
    monkeypatch.setattr(
        opportunities,
        "_fetch_intraday_1m_bars",
        lambda *args, **kwargs: pytest.fail(
            "health endpoint must read cached readiness, never fetch bars"
        ),
    )

    response = _client().get("/api/v1/system/health-layers")

    assert response.status_code == 200
    layer = _layer(response.json(), "opening_v2_1m_provider")
    assert layer["state"] == "unknown"
    assert layer["readiness"]["cached_count"] == 0
    assert layer["readiness"]["reason_code"] == "not_observed_today"


@pytest.mark.parametrize(
    ("readiness", "expected_state", "detail_fragment"),
    [
        (
            _opening_readiness(
                target_count=1,
                cached_count=1,
                ready_count=1,
                provider_counts={"MoomooFetcher": 1},
                reason_code="ready",
            ),
            "ok",
            "1/1",
        ),
        (
            _opening_readiness(
                target_count=1,
                cached_count=1,
                ready_count=0,
                provider_counts={"YfinanceFetcher": 1},
                reason_code="yfinance_1m_depth_insufficient",
            ),
            "down",
            "0/1",
        ),
        (
            _opening_readiness(
                target_count=2,
                cached_count=2,
                ready_count=1,
                provider_counts={"MoomooFetcher": 2},
                reason_code="partial_or_incomplete_reference",
            ),
            "degraded",
            "1/2",
        ),
    ],
    ids=["moomoo-20-of-20", "yfinance-insufficient", "partial"],
)
def test_opening_v2_provider_maps_cached_readiness_to_health_state(
    monkeypatch,
    readiness,
    expected_state,
    detail_fragment,
):
    _opening_health_env(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "get_opening_v2_provider_readiness",
        lambda: readiness,
    )

    response = _client().get("/api/v1/system/health-layers")

    assert response.status_code == 200
    layer = _layer(response.json(), "opening_v2_1m_provider")
    assert layer["state"] == expected_state
    assert detail_fragment in layer["detail"]
    assert layer["readiness"] == readiness


def test_opening_v2_readiness_failure_isolated_and_endpoint_stays_200(monkeypatch):
    _opening_health_env(monkeypatch)

    def boom():
        raise RuntimeError("cache corrupt")

    monkeypatch.setattr(
        opportunities,
        "get_opening_v2_provider_readiness",
        boom,
    )

    response = _client().get("/api/v1/system/health-layers")

    assert response.status_code == 200
    body = response.json()
    layer = _layer(body, "opening_v2_1m_provider")
    assert layer["state"] == "unknown"
    assert "RuntimeError" in layer["detail"]
    assert _layer(body, "api_process")["state"] == "ok"


def _economic_layer_env(monkeypatch):
    """Shared quiet baseline so only the schedule layer varies per test."""
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(monkeypatch)
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)


def test_economic_schedule_coverage_ok_when_more_than_30_days_left(monkeypatch):
    _economic_layer_env(monkeypatch)
    coverage_through = date.today() + timedelta(days=60)
    _stub_schedule(monkeypatch, coverage_through)

    body = _client().get("/api/v1/system/health-layers").json()
    layer = _layer(body, "economic_schedule_coverage")

    assert layer["state"] == "ok"
    assert coverage_through.isoformat() in layer["detail"]
    assert layer["coverage_through"] == coverage_through.isoformat()
    assert layer["days_remaining"] == 60


def test_economic_schedule_coverage_warns_within_30_days(monkeypatch):
    _economic_layer_env(monkeypatch)
    coverage_through = date.today() + timedelta(days=30)
    _stub_schedule(monkeypatch, coverage_through)

    body = _client().get("/api/v1/system/health-layers").json()
    layer = _layer(body, "economic_schedule_coverage")

    assert layer["state"] == "degraded"
    assert coverage_through.isoformat() in layer["detail"]
    assert "请在到期前放入下一年度数据文件" in layer["detail"]
    # 单层预警不把整体拖成 down。
    assert body["overall"] == "degraded"


def test_economic_schedule_coverage_down_beyond_coverage(monkeypatch):
    _economic_layer_env(monkeypatch)
    coverage_through = date.today() - timedelta(days=1)
    _stub_schedule(monkeypatch, coverage_through)

    body = _client().get("/api/v1/system/health-layers").json()
    layer = _layer(body, "economic_schedule_coverage")

    assert layer["state"] == "down"
    assert "已超出覆盖范围" in layer["detail"]
    assert layer["days_remaining"] == -1
    assert body["overall"] == "down"


def test_economic_schedule_coverage_down_when_data_files_unusable(monkeypatch):
    _economic_layer_env(monkeypatch)
    _stub_schedule(monkeypatch, None)

    body = _client().get("/api/v1/system/health-layers").json()
    layer = _layer(body, "economic_schedule_coverage")

    assert layer["state"] == "down"
    assert "fail-closed" in layer["detail"]


def test_strict_p0_health_reports_shadow_instead_of_fake_ok(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(
        monkeypatch,
        premarket=False,
        outcome=False,
        intraday_alerts=False,
        intraday_warm=True,
        p0_enabled=True,
        p0_action=False,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))
    monitor = SimpleNamespace(
        health_snapshot=lambda: {
            "schema_version": "intraday_p0_2m_health_v1",
            "running": True,
            "stream_ready": True,
            "symbol_count": 69,
            "universe_hash": "sha256:test",
            "history_market_date_et": "2026-09-10",
            "history_worker_running": False,
            "ready_count": 69,
            "baseline_warming_count": 0,
            "unavailable_count": 0,
            "last_scan_at": "2026-09-10T14:00:00+00:00",
            "scan_age_seconds": 0,
            "last_complete_window_count": 69,
            "action_switch_enabled": False,
            "activation_artifact_configured": False,
            "mobile_enabled": False,
            "mobile_reason": "action_switch_disabled",
            "mobile_eligible_symbol_count": 0,
        }
    )

    body = _client(p0_monitor=monitor).get(
        "/api/v1/system/health-layers"
    ).json()
    layer = _layer(body, "intraday_p0_2m")

    assert layer["state"] == "degraded"
    assert "未校准研究响铃已关闭" in layer["detail"]
    assert "正式 P0 仍为 shadow" in layer["detail"]
    assert layer["readiness"]["ready_count"] == 69


def test_opening_history_ready_does_not_hide_missing_live_cache_binding(monkeypatch):
    _opening_health_env(monkeypatch)
    readiness = _opening_readiness(
        target_count=69, cached_count=69, ready_count=69,
        provider_counts={"MoomooFetcher": 69}, reason_code="ready",
    )
    readiness.update({
        "current_stream_ready": False,
        "current_stream_reason": "opening_subscription_cache_unavailable",
    })
    monkeypatch.setattr(
        opportunities, "get_opening_v2_provider_readiness", lambda: readiness,
    )
    layer = _layer(
        _client().get("/api/v1/system/health-layers").json(),
        "opening_v2_1m_provider",
    )
    assert layer["state"] == "down"
    assert "订阅缓存不可用" in layer["detail"]


@pytest.mark.parametrize(
    ("scan_active", "running", "stream_ready", "expected_state", "expected_detail"),
    [
        (False, True, True, "degraded", "当前时槽不适用"),
        (False, True, False, "degraded", "当前时槽不适用"),
        (False, False, True, "down", "线程未运行"),
        (True, True, False, "down", "订阅未就绪"),
        (True, True, True, "down", "同钟基线未就绪"),
        (None, True, True, "unknown", "不假定当前休市"),
    ],
)
def test_strict_p0_health_closed_window_does_not_mask_real_failures(
    monkeypatch, scan_active, running, stream_ready, expected_state, expected_detail,
):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(
        monkeypatch, premarket=False, outcome=False, intraday_alerts=False,
        intraday_warm=True, p0_enabled=True, p0_action=False,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))
    snapshot = {
        "running": running,
        "stream_ready": stream_ready,
        "symbol_count": 69,
        "scan_window_active": scan_active,
        "history_loaded_count": 69,
        "ready_count": 0,
        "baseline_warming_count": 0,
        "unavailable_count": 69,
        "last_scan_at": "2026-10-09T23:59:05+00:00",
        "scan_age_seconds": 0,
        "mobile_enabled": False,
        "mobile_reason": "action_switch_disabled",
    }

    body = _client(
        p0_monitor=SimpleNamespace(health_snapshot=lambda: snapshot)
    ).get("/api/v1/system/health-layers").json()
    layer = _layer(body, "intraday_p0_2m")

    assert layer["state"] == expected_state
    assert expected_detail in layer["detail"]
    assert layer["readiness"]["scan_window_active"] is scan_active


def test_strict_p0_health_marks_requested_mobile_gate_failure_down(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(
        monkeypatch,
        premarket=False,
        outcome=False,
        intraday_alerts=False,
        intraday_warm=True,
        p0_enabled=True,
        p0_action=True,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))
    snapshot = {
        "running": True,
        "stream_ready": True,
        "symbol_count": 69,
        "ready_count": 69,
        "baseline_warming_count": 0,
        "unavailable_count": 0,
        "last_scan_at": "2026-09-10T14:00:00+00:00",
        "scan_age_seconds": 0,
        "mobile_enabled": False,
        "mobile_reason": "activation_artifact_not_configured",
        "mobile_eligible_symbol_count": 0,
    }

    body = _client(
        p0_monitor=SimpleNamespace(health_snapshot=lambda: snapshot)
    ).get("/api/v1/system/health-layers").json()
    layer = _layer(body, "intraday_p0_2m")

    assert layer["state"] == "down"
    assert "activation_artifact_not_configured" in layer["detail"]
    assert body["overall"] == "down"


def test_strict_p0_health_reports_shadow_research_ringing_enabled(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(
        monkeypatch,
        premarket=False,
        outcome=False,
        intraday_alerts=False,
        intraday_warm=True,
        p0_enabled=True,
        p0_action=False,
        p0_shadow_mobile=True,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))
    snapshot = {
        "running": True,
        "stream_ready": True,
        "symbol_count": 69,
        "ready_count": 69,
        "baseline_warming_count": 0,
        "unavailable_count": 0,
        "last_scan_at": "2026-09-10T14:00:00+00:00",
        "scan_age_seconds": 0,
        "mobile_enabled": False,
        "mobile_reason": "action_switch_disabled",
        "mobile_eligible_symbol_count": 0,
    }

    body = _client(
        p0_monitor=SimpleNamespace(health_snapshot=lambda: snapshot)
    ).get("/api/v1/system/health-layers").json()
    layer = _layer(body, "intraday_p0_2m")

    assert layer["state"] == "degraded"
    assert "未校准研究响铃已启用" in layer["detail"]
    assert "正式 P0 仍为 shadow" in layer["detail"]
    assert layer["readiness"]["mobile_enabled"] is False


def test_strict_p0_health_does_not_claim_research_ringing_without_creds(
    monkeypatch,
):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.delenv("MOOMOO_JOURNAL_REFRESH_ENABLED", raising=False)
    _stub_config(
        monkeypatch,
        premarket=False,
        outcome=False,
        intraday_alerts=False,
        intraday_warm=True,
        p0_enabled=True,
        p0_action=False,
        p0_shadow_mobile=True,
        p0_shadow_credentials=False,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))
    snapshot = {
        "running": True,
        "stream_ready": True,
        "symbol_count": 69,
        "ready_count": 69,
        "baseline_warming_count": 0,
        "unavailable_count": 0,
        "last_scan_at": "2026-09-10T14:00:00+00:00",
        "scan_age_seconds": 0,
        "mobile_enabled": False,
        "mobile_reason": "action_switch_disabled",
        "mobile_eligible_symbol_count": 0,
    }

    body = _client(
        p0_monitor=SimpleNamespace(health_snapshot=lambda: snapshot)
    ).get("/api/v1/system/health-layers").json()
    detail = _layer(body, "intraday_p0_2m")["detail"]

    assert "研究响铃已启用" not in detail
    assert "研究响铃已请求但 Telegram 凭据缺失" in detail


@pytest.mark.parametrize(
    ("active", "scheduler_running", "last_scan", "scan_age", "startup_age", "window_age", "state", "detail"),
    [
        (True, False, True, 0, 30, 300, "down", "调度器未运行"),
        (True, True, True, 120, 300, 300, "degraded", "研究扫描就绪"),
        (True, True, True, 121, 300, 300, "down", "121 秒未完成信号扫描"),
        (False, True, True, 999999, 999999, None, "degraded", "当前时槽不适用"),
        (True, True, False, None, 120, 300, "degraded", "启动宽限 120 秒"),
        (True, True, False, None, 121, 300, "down", "超过 120 秒"),
        (True, True, True, 999999, 999999, 120, "degraded", "当前扫描窗刚开始"),
        (True, True, True, 999999, 999999, 121, "down", "未完成信号扫描"),
    ],
)
def test_strict_p0_health_checks_completed_scan_freshness_and_scheduler(
    monkeypatch, active, scheduler_running, last_scan, scan_age, startup_age, window_age,
    state, detail,
):
    _stub_config(
        monkeypatch, premarket=False, outcome=False,
        intraday_warm=True, p0_enabled=True,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))
    snapshot = {
        "running": True, "stream_ready": True, "symbol_count": 69,
        "ready_count": 69, "scan_window_active": active,
        "last_scan_at": "2026-10-09T23:59:05+00:00" if last_scan else None,
        "scan_age_seconds": scan_age, "startup_age_seconds": startup_age,
        "scan_window_age_seconds": window_age,
    }
    body = _client(
        SimpleNamespace(health_snapshot=lambda: snapshot),
        signal_scheduler_running=scheduler_running,
    ).get("/api/v1/system/health-layers").json()
    layer = _layer(body, "intraday_p0_2m")
    assert layer["state"] == state
    assert detail in layer["detail"]
    assert layer["readiness"]["signal_scheduler_running"] is scheduler_running


@pytest.mark.parametrize(
    ("run_state", "write_error", "expected_state", "detail"),
    [
        ("waiting", None, "ok", "等待首个交易日"),
        ("collecting", None, "ok", "正在收集现场观测"),
        ("completed", None, "ok", "结束不表示已通过校准"),
        ("unknown", None, "unknown", "前瞻运行状态未知"),
        ("collecting", "OSError", "degraded", "写入失败：OSError"),
    ],
)
def test_forward_shadow_health_exposes_lifecycle_and_recording_failure(
    monkeypatch, run_state, write_error, expected_state, detail,
):
    _stub_config(
        monkeypatch, premarket=False, outcome=False,
        intraday_warm=True, p0_enabled=True,
    )
    _stub_snapshots(monkeypatch)
    _stub_maintenance(monkeypatch)
    _stub_schedule(monkeypatch, date.today() + timedelta(days=90))
    snapshot = {
        "running": True, "stream_ready": True, "symbol_count": 69,
        "ready_count": 69, "scan_window_active": False,
        "shadow_run_state": run_state,
        "shadow_start_session": "2026-10-12", "shadow_end_session": "2026-11-06",
        "shadow_write_error": write_error,
        "shadow_last_write_at": "2026-10-12T14:00:05+00:00",
    }
    body = _client(SimpleNamespace(health_snapshot=lambda: snapshot)).get(
        "/api/v1/system/health-layers",
    ).json()
    layer = _layer(body, "intraday_p0_2m_shadow")
    assert layer["state"] == expected_state
    assert detail in layer["detail"]
    assert "最近成功写入" in layer["detail"]
