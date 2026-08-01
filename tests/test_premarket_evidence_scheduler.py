from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import threading
from zoneinfo import ZoneInfo

import pytest

from src.regime.fetchers import RegimeDataFetcher
from src.regime.premarket_repository import get_premarket_artifact_bundle
from src.services.premarket_evidence_prefetch import PremarketWorkerResult
from src.services.premarket_evidence_scheduler import (
    MoomooPremarketEvidenceScheduler,
    PremarketEvidenceTickResult,
    execute_premarket_evidence_tick,
    resolve_premarket_evidence_schedule,
)
from src.services.premarket_research_service import ResolvedPremarketUniverse
from src.storage import DatabaseManager


UTC = timezone.utc
MARKET_DATE = date(2026, 7, 24)
PREVIOUS_SESSION = date(2026, 7, 23)
SESSION_OPEN = datetime(2026, 7, 24, 13, 30, tzinfo=UTC)
TARGET = datetime(2026, 7, 24, 13, 8, tzinfo=UTC)


class FakeCalendar:
    def __init__(
        self,
        *,
        session: bool = True,
        market_date: date = MARKET_DATE,
        previous_session: date = PREVIOUS_SESSION,
        session_open: datetime = SESSION_OPEN,
    ):
        self.session = session
        self.market_date = market_date
        self.prior_session = previous_session
        self.open_at = session_open

    def is_session(self, value):
        return self.session and value == self.market_date

    def session_open(self, value):
        assert value == self.market_date
        return self.open_at

    def previous_session(self, value):
        assert value == self.market_date
        return self.prior_session


UNIVERSE = ResolvedPremarketUniverse(
    symbols=("AAPL", "NVDA"),
    requested_limit=2,
    source="persisted",
    universe_version_key="opu_shadow_fixture",
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PATH",
        str(tmp_path / "scheduler_integration.db"),
    )
    import src.config as config_module

    monkeypatch.setattr(
        config_module.Config,
        "_parse_stock_email_groups",
        classmethod(lambda _cls: []),
    )
    config_module.Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    yield db
    DatabaseManager.reset_instance()
    config_module.Config.reset_instance()


def _run(state="running", **overrides):
    payload = {
        "slot_key": "rpp_fixture",
        "state": state,
        "attempt_count": 1,
        "bundle_key": None,
        "last_error_code": None,
        "completed_at": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _observation(
    symbol: str,
    price: float = 101.0,
    *,
    market_date: date = MARKET_DATE,
    previous_session: date = PREVIOUS_SESSION,
    target: datetime = TARGET,
):
    return {
        "symbol": symbol,
        "market_date_et": market_date.isoformat(),
        "requested_as_of": target.isoformat(),
        "evidence_as_of": (target - timedelta(minutes=1)).isoformat(),
        "price": price,
        "previous_close": 100.0,
        "previous_close_date": previous_session.isoformat(),
        "pct_change": price - 100.0,
        "status": "ready",
        "reason": None,
        "source": "MoomooFetcher",
    }


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.requests = []
        self.cancelled = False

    def run(self, request, *, hard_deadline_at, heartbeat):
        self.requests.append((request, hard_deadline_at))
        heartbeat(request.target_as_of + timedelta(seconds=20))
        return self.result

    def cancel_active(self):
        self.cancelled = True

    def reset_cancellation(self):
        self.cancelled = False


def test_schedule_is_xnys_relative_and_rejects_late_start():
    calendar = FakeCalendar()

    waiting = resolve_premarket_evidence_schedule(
        TARGET - timedelta(seconds=1),
        calendar=calendar,
    )
    ready = resolve_premarket_evidence_schedule(
        TARGET,
        calendar=calendar,
    )
    late = resolve_premarket_evidence_schedule(
        TARGET + timedelta(minutes=1),
        calendar=calendar,
    )
    missed = resolve_premarket_evidence_schedule(
        TARGET + timedelta(minutes=3),
        calendar=calendar,
    )

    assert waiting.window_state == "waiting_window"
    assert ready.window_state == "ready_to_run"
    assert ready.target_as_of == TARGET
    assert ready.latest_start_at == TARGET + timedelta(minutes=1)
    assert ready.hard_deadline_at == TARGET + timedelta(minutes=3)
    assert ready.expires_at == TARGET + timedelta(minutes=5)
    assert late.window_state == "late_start_rejected"
    assert missed.window_state == "window_missed"
    assert resolve_premarket_evidence_schedule(
        TARGET,
        calendar=FakeCalendar(session=False),
    ) is None


@pytest.mark.parametrize(
    ("observed_at", "expected_target"),
    (
        (
            datetime(2026, 3, 6, 14, 8, tzinfo=UTC),
            datetime(2026, 3, 6, 14, 8, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 9, 13, 8, tzinfo=UTC),
            datetime(2026, 3, 9, 13, 8, tzinfo=UTC),
        ),
        (
            datetime(2026, 11, 27, 14, 8, tzinfo=UTC),
            datetime(2026, 11, 27, 14, 8, tzinfo=UTC),
        ),
    ),
)
def test_real_xnys_calendar_handles_dst_and_early_close(
    observed_at,
    expected_target,
):
    schedule = resolve_premarket_evidence_schedule(observed_at)

    assert schedule is not None
    assert schedule.target_as_of == expected_target
    assert schedule.window_state == "ready_to_run"


def test_real_xnys_calendar_rejects_non_session():
    assert (
        resolve_premarket_evidence_schedule(
            datetime(2026, 7, 4, 13, 8, tzinfo=UTC),
        )
        is None
    )


def test_waiting_or_missing_pool_never_claims_or_spawns():
    calls = []
    runner = FakeRunner(
        PremarketWorkerResult("succeeded", (), None, 0.1, 0)
    )

    waiting = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET - timedelta(seconds=1),
        calendar=FakeCalendar(),
        runner=runner,
        universe_loader=lambda: calls.append("universe") or UNIVERSE,
        claim_fn=lambda *_args, **_kwargs: calls.append("claim"),
    )
    missing = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET,
        calendar=FakeCalendar(),
        runner=runner,
        universe_loader=lambda: None,
        claim_fn=lambda *_args, **_kwargs: calls.append("claim"),
    )

    assert waiting.state == "waiting_window"
    assert missing.state == "research_pool_missing"
    assert calls == []
    assert runner.requests == []


def test_late_wakeup_does_not_spawn_or_backfill_stale_evidence():
    calls = []
    runner = FakeRunner(
        PremarketWorkerResult("succeeded", (), None, 0.1, 0)
    )

    result = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET + timedelta(minutes=1),
        calendar=FakeCalendar(),
        runner=runner,
        universe_loader=lambda: UNIVERSE,
        claim_fn=lambda *_args, **_kwargs: calls.append("claim"),
        missed_fn=lambda *_args, **_kwargs: calls.append("missed"),
    )

    assert result.state == "late_start_rejected"
    assert result.error_code == "latest_start_elapsed"
    assert calls == []
    assert runner.requests == []


def test_due_tick_claims_once_builds_ready_bundle_and_heartbeats():
    calls = []
    runner = FakeRunner(
        PremarketWorkerResult(
            state="succeeded",
            observations=(
                _observation("AAPL", 102.0),
                _observation("NVDA", 103.0),
                _observation("SPY", 101.0),
            ),
            error_code=None,
            duration_seconds=1.0,
            exit_code=0,
        )
    )

    def claim(*args, **kwargs):
        calls.append(("claim", args, kwargs))
        return SimpleNamespace(claimed=True, run=_run())

    def heartbeat(*args, **kwargs):
        calls.append(("heartbeat", args, kwargs))
        return _run()

    def append(*args, **kwargs):
        calls.append(("append", args, kwargs))
        assert kwargs["quality_state"] == "ready"
        assert kwargs["coverage"]["available_count"] == 3
        assert kwargs["payload"]["required_universe"] == [
            "AAPL",
            "NVDA",
            "SPY",
        ]
        return SimpleNamespace(
            run=_run("succeeded", bundle_key="rpb_fixture"),
            bundle=SimpleNamespace(bundle_key="rpb_fixture"),
            duplicate=False,
        )

    result = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET + timedelta(seconds=5),
        calendar=FakeCalendar(),
        runner=runner,
        utc_now=lambda: TARGET + timedelta(seconds=30),
        universe_loader=lambda: UNIVERSE,
        claim_fn=claim,
        heartbeat_fn=heartbeat,
        append_fn=append,
    )

    assert result.action == "settled"
    assert result.state == "succeeded"
    assert result.quality == "ready"
    assert result.available_count == 3
    assert result.requested_count == 3
    assert [item[0] for item in calls] == ["claim", "heartbeat", "append"]
    request, deadline = runner.requests[0]
    assert request.symbols == ("AAPL", "NVDA", "SPY")
    assert request.target_as_of == TARGET
    assert deadline == TARGET + timedelta(minutes=3)


def test_completed_child_with_missing_symbol_persists_partial_bundle():
    captured = {}
    runner = FakeRunner(
        PremarketWorkerResult(
            state="succeeded",
            observations=(
                _observation("SPY"),
                _observation("AAPL"),
            ),
            error_code=None,
            duration_seconds=1.0,
            exit_code=0,
        )
    )

    def append(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            run=_run("succeeded", bundle_key="rpb_partial"),
            bundle=SimpleNamespace(bundle_key="rpb_partial"),
        )

    result = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET,
        calendar=FakeCalendar(),
        runner=runner,
        utc_now=lambda: TARGET + timedelta(seconds=30),
        universe_loader=lambda: UNIVERSE,
        claim_fn=lambda *_args, **_kwargs: SimpleNamespace(
            claimed=True,
            run=_run(),
        ),
        heartbeat_fn=lambda *_args, **_kwargs: _run(),
        append_fn=append,
    )

    assert result.quality == "partial"
    assert result.available_count == 2
    unavailable = {
        item["symbol"]: item["unavailable_reason"]
        for item in captured["payload"]["observations"]
        if item["unavailable_reason"]
    }
    assert unavailable == {"NVDA": "worker_unavailable"}


def test_timeout_settles_without_publishing_an_artifact():
    calls = []
    runner = FakeRunner(
        PremarketWorkerResult(
            state="timed_out",
            observations=(_observation("SPY"),),
            error_code="worker_timeout",
            duration_seconds=180.0,
            exit_code=-9,
        )
    )

    def settle(*args, **kwargs):
        calls.append((args, kwargs))
        return _run("timed_out", last_error_code="worker_timeout")

    result = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET,
        calendar=FakeCalendar(),
        runner=runner,
        utc_now=lambda: TARGET + timedelta(minutes=3),
        universe_loader=lambda: UNIVERSE,
        claim_fn=lambda *_args, **_kwargs: SimpleNamespace(
            claimed=True,
            run=_run(),
        ),
        heartbeat_fn=lambda *_args, **_kwargs: _run(),
        settle_fn=settle,
        append_fn=lambda *_args, **_kwargs: calls.append("append"),
    )

    assert result.state == "timed_out"
    assert result.error_code == "worker_timeout"
    assert calls[0][1]["state"] == "timed_out"
    assert "append" not in calls


def test_missed_window_persists_one_auditable_slot_without_spawning():
    runner = FakeRunner(
        PremarketWorkerResult("succeeded", (), None, 0.1, 0)
    )
    calls = []

    def missed(*args, **kwargs):
        calls.append((args, kwargs))
        return _run(
            "missed",
            attempt_count=0,
            completed_at=TARGET + timedelta(minutes=3),
            last_error_code="hard_deadline_missed",
        )

    result = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET + timedelta(minutes=3),
        calendar=FakeCalendar(),
        runner=runner,
        universe_loader=lambda: UNIVERSE,
        missed_fn=missed,
    )
    replay = execute_premarket_evidence_tick(
        owner_key="worker-a",
        now=TARGET + timedelta(minutes=4),
        calendar=FakeCalendar(),
        runner=runner,
        universe_loader=lambda: UNIVERSE,
        missed_fn=missed,
    )

    assert result.action == "missed"
    assert result.state == "missed"
    assert replay.action == "idle"
    assert replay.state == "missed"
    assert len(calls) == 2
    assert runner.requests == []


def test_real_repository_integration_is_atomic_and_replay_does_not_spawn(
    isolated_db,
):
    live_target = datetime.now(UTC).replace(microsecond=0) - timedelta(
        seconds=30
    )
    live_market_date = live_target.astimezone(
        ZoneInfo("America/New_York")
    ).date()
    live_previous_session = live_market_date - timedelta(days=1)
    live_session_open = live_target + timedelta(minutes=22)
    live_calendar = FakeCalendar(
        market_date=live_market_date,
        previous_session=live_previous_session,
        session_open=live_session_open,
    )
    runner = FakeRunner(
        PremarketWorkerResult(
            state="succeeded",
            observations=(
                _observation(
                    "SPY",
                    market_date=live_market_date,
                    previous_session=live_previous_session,
                    target=live_target,
                ),
                _observation(
                    "AAPL",
                    market_date=live_market_date,
                    previous_session=live_previous_session,
                    target=live_target,
                ),
                _observation(
                    "NVDA",
                    market_date=live_market_date,
                    previous_session=live_previous_session,
                    target=live_target,
                ),
            ),
            error_code=None,
            duration_seconds=1.0,
            exit_code=0,
        )
    )
    kwargs = {
        "owner_key": "worker-a",
        "now": live_target + timedelta(seconds=5),
        "calendar": live_calendar,
        "db_manager": isolated_db,
        "runner": runner,
        "utc_now": lambda: datetime.now(UTC),
        "universe_loader": lambda: UNIVERSE,
    }

    first = execute_premarket_evidence_tick(**kwargs)
    replay = execute_premarket_evidence_tick(**kwargs)

    assert first.state == "succeeded"
    assert first.quality == "ready"
    assert first.bundle_key
    stored = get_premarket_artifact_bundle(
        first.bundle_key,
        db_manager=isolated_db,
    )
    assert stored is not None
    assert stored.payload["quality"] == "ready"
    assert stored.payload_sha256
    assert replay.action == "idle"
    assert replay.state == "succeeded"
    assert len(runner.requests) == 1


def test_scheduler_stop_cancels_active_child_and_remains_low_noise():
    ticked = threading.Event()
    runner = FakeRunner(
        PremarketWorkerResult("succeeded", (), None, 0.0, 0)
    )

    def tick():
        ticked.set()
        return PremarketEvidenceTickResult(
            action="idle",
            state="waiting_window",
            market_date_et=MARKET_DATE.isoformat(),
            slot_key=None,
            bundle_key=None,
            attempt_count=0,
            quality=None,
            requested_count=0,
            available_count=0,
            error_code=None,
        )

    scheduler = MoomooPremarketEvidenceScheduler(
        tick,
        runner=runner,
        interval_seconds=60,
    )
    scheduler.start()
    assert ticked.wait(timeout=1)
    scheduler.stop(join_timeout_seconds=1)

    assert runner.cancelled is True
    assert scheduler.running is False


def test_existing_regime_fetcher_does_not_consume_shadow_artifacts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.regime.premarket_repository."
        "select_formal_premarket_artifact_bundle",
        lambda *_args, **_kwargs: calls.append("shadow-read"),
    )
    fetcher = RegimeDataFetcher(
        alpaca=None,
        finnhub=None,
        yf=None,
        manager=None,
    )

    result = fetcher.get_premarket_activity(
        ["AAPL"],
        MARKET_DATE,
        as_of=TARGET,
    )

    assert result["_status"] == "unavailable"
    assert result["_attempted_sources"] == []
    assert calls == []
