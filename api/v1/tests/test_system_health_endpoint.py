# -*- coding: utf-8 -*-
"""分层健康端点：单层失败只降级该层，端点本身永不 500。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import system_config


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(system_config.router, prefix="/api/v1/system")
    return TestClient(app)


def _layer(body: dict, name: str) -> dict:
    return next(item for item in body["layers"] if item["layer"] == name)


def _stub_config(monkeypatch, *, premarket=True, outcome=True):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            premarket_research_scheduler_enabled=premarket,
            opportunity_outcome_scheduler_enabled=outcome,
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
