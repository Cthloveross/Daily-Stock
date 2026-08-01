from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.v1.endpoints import opportunities
from src.opportunities.premarket import PremarketCalendarError
from src.services import premarket_research_service
from src.storage import DatabaseManager


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(opportunities.router, prefix="/api/v1/opportunities")
    return TestClient(app)


@pytest.fixture(autouse=True)
def deterministic_scheduler_flag(monkeypatch):
    """Keep endpoint contract assertions independent of the developer .env."""

    monkeypatch.setattr(
        opportunities,
        "_premarket_scheduler_enabled",
        lambda: False,
    )


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "premarket_api.db"))

    import src.config as config_module

    monkeypatch.setattr(
        config_module.Config,
        "_parse_stock_email_groups",
        classmethod(lambda _cls: []),
    )
    config_module.Config.reset_instance()
    DatabaseManager.reset_instance()
    yield DatabaseManager.get_instance()
    DatabaseManager.reset_instance()
    config_module.Config.reset_instance()


def _response(symbols, limit, *, state="ready_to_run"):
    return {
        "schema_version": "canonical-premarket-cycle/1.0",
        "cycle_version": "canonical_premarket_cycle_v1",
        "freeze_policy_version": "canonical_premarket_xnys_v2",
        "scope_key": "canonical_premarket_research",
        "cycle_key": "pmr_fixture",
        "state": state,
        "quality": "unknown",
        "market_date_et": "2026-07-23",
        "previous_session": "2026-07-22",
        "regular_open_at": "2026-07-23T13:30:00+00:00",
        "window_start_at": "2026-07-23T12:45:00+00:00",
        "window_end_at": "2026-07-23T13:20:00+00:00",
        "latest_start_at": "2026-07-23T13:18:00+00:00",
        "universe": list(symbols),
        "requested_limit": limit,
        "stages": [],
        "regime_quality": {},
        "quality_reasons": [],
        "run": None,
        "snapshot": None,
        "idempotent_replay": False,
        "message": "fixture",
    }


def _persisted(symbols=("SPY", "QQQ"), limit=2):
    return SimpleNamespace(
        symbols=tuple(symbols),
        requested_limit=limit,
        source="persisted",
        universe_version_key="opu_fixture",
    )


def test_premarket_status_uses_persisted_pool_without_starting_run(monkeypatch):
    calls = []
    monkeypatch.setattr(
        opportunities,
        "_persisted_premarket_universe",
        lambda: _persisted(),
    )

    def status(symbols, limit, **kwargs):
        calls.append((symbols, limit, kwargs))
        return _response(symbols, limit)

    monkeypatch.setattr(
        premarket_research_service,
        "status_canonical_premarket_research",
        status,
    )
    monkeypatch.setattr(
        premarket_research_service,
        "run_canonical_premarket_research",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("status must not run provider work")
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/premarket/status",
        json={"symbols": ["aapl", "AAPL", "msft"], "limit": 5},
    )

    assert response.status_code == 200
    assert calls == [
        (
            ["SPY", "QQQ"],
            2,
            {
                "universe_source": "persisted",
                "universe_version_key": "opu_fixture",
                "scheduler_enabled": False,
            },
        )
    ]
    assert response.json()["state"] == "ready_to_run"
    assert response.json()["latest_start_at"] == "2026-07-23T13:18:00+00:00"


def test_premarket_run_requires_manual_true(monkeypatch):
    monkeypatch.setattr(
        opportunities,
        "_persisted_premarket_universe",
        lambda: _persisted(),
    )
    monkeypatch.setattr(
        premarket_research_service,
        "status_canonical_premarket_research",
        lambda symbols, limit, **_kwargs: _response(symbols, limit),
    )
    monkeypatch.setattr(
        premarket_research_service,
        "run_canonical_premarket_research",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual=false must not run provider work")
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/premarket/run",
        json={"symbols": ["AAPL"], "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["universe"] == ["SPY", "QQQ"]


def test_manual_run_passes_server_scan_runner_and_persisted_pool(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        opportunities,
        "_persisted_premarket_universe",
        lambda: _persisted(),
    )

    def run(symbols, limit, **kwargs):
        captured.update(
            symbols=symbols,
            limit=limit,
            **kwargs,
        )
        return _response(symbols, limit, state="running")

    monkeypatch.setattr(
        premarket_research_service,
        "run_canonical_premarket_research",
        run,
    )

    response = _client().post(
        "/api/v1/opportunities/premarket/run",
        json={"symbols": ["AAPL"], "limit": 5, "manual": True},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "running"
    assert captured == {
        "symbols": ["SPY", "QQQ"],
        "limit": 2,
        "scan_runner": opportunities._execute_daily_scan,
        "universe_source": "persisted",
        "universe_version_key": "opu_fixture",
        "trigger": "manual",
        "scheduler_enabled": False,
    }


def test_premarket_calendar_failure_is_redacted_503(monkeypatch):
    monkeypatch.setattr(
        opportunities,
        "_persisted_premarket_universe",
        lambda: _persisted(),
    )
    monkeypatch.setattr(
        premarket_research_service,
        "status_canonical_premarket_research",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PremarketCalendarError(
                "sensitive https://provider.invalid/?token=secret"
            )
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/premarket/status",
        json={"symbols": ["AAPL"], "limit": 1},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error": "xnys_calendar_unavailable",
        "message": "XNYS 交易日历暂时不可用。",
    }
    assert "provider.invalid" not in response.text


def test_universe_get_suggestion_is_not_active_until_put(
    isolated_db,
    monkeypatch,
):
    monkeypatch.setattr(
        opportunities,
        "_configured_symbols",
        lambda: ["AAPL", "MSFT"],
    )

    suggestion = _client().get(
        "/api/v1/opportunities/premarket/universe"
    )
    status_before = _client().post(
        "/api/v1/opportunities/premarket/status",
        json={"symbols": ["TSLA"], "limit": 1},
    )
    saved = _client().put(
        "/api/v1/opportunities/premarket/universe",
        json={"symbols": ["aapl", "MSFT", "AAPL"], "limit": 2},
    )
    active = _client().get(
        "/api/v1/opportunities/premarket/universe"
    )

    assert suggestion.status_code == 200
    assert suggestion.json()["configured"] is False
    assert suggestion.json()["source"] == "stock_list_fallback"
    assert suggestion.json()["symbols"] == ["AAPL", "MSFT"]
    assert status_before.status_code == 200
    assert status_before.json()["state"] == "research_pool_missing"
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["source"] == "persisted"
    assert active.json()["configured"] is True
    assert active.json()["universe_version_key"] == saved.json()[
        "universe_version_key"
    ]
