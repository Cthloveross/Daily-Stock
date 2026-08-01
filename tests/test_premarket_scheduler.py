# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from starlette.testclient import TestClient

from api.app import create_app
from src.services.premarket_research_service import ResolvedPremarketUniverse
from src.services.premarket_scheduler import (
    CanonicalPremarketScheduler,
    PremarketSchedulerTickResult,
    execute_premarket_scheduler_tick,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 22, 13, 12, tzinfo=UTC)
UNIVERSE = ResolvedPremarketUniverse(
    symbols=("NVDA", "AAPL"),
    requested_limit=2,
    source="persisted",
    universe_version_key="opu_fixture",
)


def _status(state: str, **overrides):
    return {
        "state": state,
        "cycle_key": "pmr_fixture",
        "attempt_key": None,
        "error_code": None,
        "recoverable": False,
        "next_scheduled_at": None,
        **overrides,
    }


def test_tick_does_not_run_provider_without_a_persisted_pool():
    calls = []

    result = execute_premarket_scheduler_tick(
        scan_runner=lambda *_args, **_kwargs: calls.append("scan"),
        now=NOW,
        universe_loader=lambda: None,
        status_reader=lambda symbols, limit, **kwargs: (
            calls.append((symbols, limit, kwargs["scheduler_enabled"]))
            or _status("research_pool_missing")
        ),
        cycle_runner=lambda *_args, **_kwargs: calls.append("run"),
    )

    assert result.action == "idle"
    assert calls == [((), 5, True)]


def test_tick_executes_one_due_attempt_with_scheduler_trigger():
    captured = {}

    def run(symbols, limit, **kwargs):
        captured.update(
            symbols=tuple(symbols),
            limit=limit,
            trigger=kwargs["trigger"],
            version=kwargs["universe_version_key"],
        )
        return _status("published", attempt_key="opa_fixture")

    result = execute_premarket_scheduler_tick(
        scan_runner=lambda *_args, **_kwargs: {},
        now=NOW,
        universe_loader=lambda: UNIVERSE,
        status_reader=lambda *_args, **_kwargs: _status("ready_to_run"),
        cycle_runner=run,
    )

    assert result.action == "attempted"
    assert result.state == "published"
    assert captured == {
        "symbols": ("NVDA", "AAPL"),
        "limit": 2,
        "trigger": "scheduler",
        "version": "opu_fixture",
    }


def test_tick_waits_until_persisted_retry_time():
    calls = []
    result = execute_premarket_scheduler_tick(
        scan_runner=lambda *_args, **_kwargs: {},
        now=NOW,
        universe_loader=lambda: UNIVERSE,
        status_reader=lambda *_args, **_kwargs: _status(
            "failed",
            recoverable=True,
            next_scheduled_at="2026-07-22T13:17:00+00:00",
        ),
        cycle_runner=lambda *_args, **_kwargs: calls.append("run"),
    )

    assert result.action == "idle"
    assert calls == []


def test_daemon_loop_stops_without_repeating_idle_logs_or_work():
    ticked = threading.Event()

    def tick():
        ticked.set()
        return PremarketSchedulerTickResult(
            action="idle",
            state="waiting_window",
            cycle_key="pmr_fixture",
            attempt_key=None,
            error_code=None,
        )

    scheduler = CanonicalPremarketScheduler(tick, interval_seconds=60)
    scheduler.start()
    assert ticked.wait(timeout=1)
    scheduler.stop(join_timeout_seconds=1)

    assert scheduler.running is False


def test_blocking_tick_is_drained_before_lifecycle_stop_allows_restart():
    tick_started = threading.Event()
    release_tick = threading.Event()
    stop_completed = threading.Event()
    calls = []

    def tick():
        calls.append("tick")
        tick_started.set()
        release_tick.wait(timeout=2)
        return PremarketSchedulerTickResult(
            action="idle",
            state="running",
            cycle_key="pmr_fixture",
            attempt_key="opa_fixture",
            error_code=None,
        )

    scheduler = CanonicalPremarketScheduler(tick, interval_seconds=60)
    scheduler.start()
    assert tick_started.wait(timeout=1)

    stopper = threading.Thread(
        target=lambda: (scheduler.stop(), stop_completed.set()),
    )
    stopper.start()
    assert stop_completed.wait(timeout=0.05) is False
    assert scheduler.running is True

    scheduler.start()
    assert calls == ["tick"]

    release_tick.set()
    assert stop_completed.wait(timeout=1)
    stopper.join(timeout=1)
    assert scheduler.running is False


def test_fastapi_lifespan_starts_and_stops_enabled_scheduler(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FakeScheduler:
        def __init__(self, tick):
            calls.append(("init", callable(tick)))

        def start(self):
            calls.append(("start", True))

        def stop(self):
            calls.append(("stop", True))

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            premarket_research_scheduler_enabled=True,
        ),
    )
    monkeypatch.setattr(
        "src.services.premarket_scheduler.CanonicalPremarketScheduler",
        FakeScheduler,
    )

    with TestClient(create_app(static_dir=tmp_path / "no-static")) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == [
        ("init", True),
        ("start", True),
        ("stop", True),
    ]


def test_fastapi_lifespan_starts_and_stops_outcome_scheduler(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FakeOutcomeScheduler:
        def __init__(self):
            calls.append(("init", True))

        def start(self):
            calls.append(("start", True))

        def stop(self):
            calls.append(("stop", True))

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            premarket_research_scheduler_enabled=False,
            opportunity_outcome_scheduler_enabled=True,
        ),
    )
    monkeypatch.setattr(
        "src.services.opportunity_outcome_scheduler."
        "CanonicalOpportunityOutcomeScheduler",
        FakeOutcomeScheduler,
    )

    with TestClient(create_app(static_dir=tmp_path / "no-static")) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == [
        ("init", True),
        ("start", True),
        ("stop", True),
    ]


def test_fastapi_lifespan_starts_and_stops_shadow_evidence_scheduler(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FakeEvidenceScheduler:
        def __init__(self):
            calls.append(("init", True))

        def start(self):
            calls.append(("start", True))

        def stop(self):
            calls.append(("stop", True))

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            moomoo_premarket_prefetch_enabled=True,
            premarket_research_scheduler_enabled=False,
            opportunity_outcome_scheduler_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "src.regime.premarket_repository.init_premarket_evidence_schema",
        lambda: calls.append(("schema", True)),
    )
    monkeypatch.setattr(
        "src.services.premarket_evidence_scheduler."
        "MoomooPremarketEvidenceScheduler",
        FakeEvidenceScheduler,
    )

    with TestClient(create_app(static_dir=tmp_path / "no-static")) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == [
        ("schema", True),
        ("init", True),
        ("start", True),
        ("stop", True),
    ]


def test_fastapi_lifespan_keeps_shadow_scheduler_disabled_by_default(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            moomoo_premarket_prefetch_enabled=False,
            premarket_research_scheduler_enabled=False,
            opportunity_outcome_scheduler_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "src.regime.premarket_repository.init_premarket_evidence_schema",
        lambda: calls.append("schema"),
    )
    monkeypatch.setattr(
        "src.services.premarket_evidence_scheduler."
        "MoomooPremarketEvidenceScheduler",
        lambda: calls.append("scheduler"),
    )

    app = create_app(static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert not hasattr(
            app.state,
            "moomoo_premarket_prefetch_scheduler",
        )

    assert calls == []


def test_fastapi_lifespan_cleans_started_scheduler_when_later_start_fails(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FakeEvidenceScheduler:
        def start(self):
            calls.append("evidence-start")

        def stop(self):
            calls.append("evidence-stop")

    class FailingOfficialScheduler:
        def __init__(self, tick):
            assert callable(tick)

        def start(self):
            calls.append("official-start")
            raise RuntimeError("official scheduler failed")

        def stop(self):
            calls.append("official-stop")

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            moomoo_premarket_prefetch_enabled=True,
            premarket_research_scheduler_enabled=True,
            opportunity_outcome_scheduler_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "src.regime.premarket_repository.init_premarket_evidence_schema",
        lambda: calls.append("schema"),
    )
    monkeypatch.setattr(
        "src.services.premarket_evidence_scheduler."
        "MoomooPremarketEvidenceScheduler",
        FakeEvidenceScheduler,
    )
    monkeypatch.setattr(
        "src.services.premarket_scheduler.CanonicalPremarketScheduler",
        FailingOfficialScheduler,
    )

    with pytest.raises(RuntimeError, match="official scheduler failed"):
        with TestClient(create_app(static_dir=tmp_path / "no-static")):
            pass

    assert calls == [
        "schema",
        "evidence-start",
        "official-start",
        "evidence-stop",
        "official-stop",
    ]


def test_fastapi_lifespan_isolates_scheduler_stop_failures(
    monkeypatch,
    tmp_path: Path,
):
    calls = []

    class FailingEvidenceScheduler:
        def start(self):
            calls.append("evidence-start")

        def stop(self):
            calls.append("evidence-stop")
            raise RuntimeError("evidence stop failed")

    class OfficialScheduler:
        def __init__(self, tick):
            assert callable(tick)

        def start(self):
            calls.append("official-start")

        def stop(self):
            calls.append("official-stop")

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            moomoo_premarket_prefetch_enabled=True,
            premarket_research_scheduler_enabled=True,
            opportunity_outcome_scheduler_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "src.regime.premarket_repository.init_premarket_evidence_schema",
        lambda: calls.append("schema"),
    )
    monkeypatch.setattr(
        "src.services.premarket_evidence_scheduler."
        "MoomooPremarketEvidenceScheduler",
        FailingEvidenceScheduler,
    )
    monkeypatch.setattr(
        "src.services.premarket_scheduler.CanonicalPremarketScheduler",
        OfficialScheduler,
    )

    app = create_app(static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == [
        "schema",
        "evidence-start",
        "official-start",
        "evidence-stop",
        "official-stop",
    ]
    assert not hasattr(app.state, "moomoo_premarket_prefetch_scheduler")
    assert not hasattr(app.state, "premarket_research_scheduler")
    assert not hasattr(app.state, "system_config_service")
