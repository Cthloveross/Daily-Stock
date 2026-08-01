from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import threading

import pytest

from src.services.opportunity_outcome_scheduler import (
    CanonicalOpportunityOutcomeScheduler,
    OutcomeMaintenanceTickResult,
    execute_outcome_maintenance_tick,
)
from src.storage import DatabaseManager


UTC = timezone.utc


class FakeCalendar:
    def __init__(self, closes: dict[date, datetime]):
        self.closes = closes

    def is_session(self, session: date) -> bool:
        return session in self.closes

    def session_close(self, session: date) -> datetime:
        return self.closes[session]


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PATH",
        str(tmp_path / "outcome_scheduler.db"),
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


def _result(*, gaps: int = 0, inserted: int = 0):
    return {
        "due_snapshot_count": 1,
        "failed_snapshot_count": 0,
        "inserted_outcomes": inserted,
        "data_gap_horizons": gaps,
    }


def test_tick_waits_until_exact_session_close_plus_thirty_minutes(isolated_db):
    session = date(2026, 7, 23)
    calendar = FakeCalendar(
        {session: datetime(2026, 7, 23, 20, 0, tzinfo=UTC)}
    )
    calls = []

    result = execute_outcome_maintenance_tick(
        owner_key="worker-a",
        evaluator=lambda **kwargs: calls.append(kwargs) or _result(),
        now=datetime(2026, 7, 23, 20, 29, 59, tzinfo=UTC),
        calendar=calendar,
        db_manager=isolated_db,
    )

    assert result.action == "idle"
    assert result.state == "waiting_settlement"
    assert calls == []


def test_tick_claims_once_and_replay_does_not_call_provider(isolated_db):
    session = date(2026, 7, 23)
    now = datetime(2026, 7, 23, 20, 30, tzinfo=UTC)
    calendar = FakeCalendar(
        {session: datetime(2026, 7, 23, 20, 0, tzinfo=UTC)}
    )
    calls = []

    first = execute_outcome_maintenance_tick(
        owner_key="worker-a",
        evaluator=lambda **kwargs: calls.append(kwargs) or _result(inserted=3),
        now=now,
        calendar=calendar,
        db_manager=isolated_db,
    )
    replay = execute_outcome_maintenance_tick(
        owner_key="worker-b",
        evaluator=lambda **kwargs: calls.append(kwargs) or _result(inserted=9),
        now=now,
        calendar=calendar,
        db_manager=isolated_db,
    )

    assert first.action == "evaluated"
    assert first.state == "completed"
    assert first.inserted_outcomes == 3
    assert replay.action == "idle"
    assert replay.state == "completed"
    assert len(calls) == 1


def test_degraded_first_attempt_waits_for_close_plus_four_and_half_hours(
    isolated_db,
):
    session = date(2026, 7, 23)
    close = datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
    calendar = FakeCalendar({session: close})
    calls = []

    first = execute_outcome_maintenance_tick(
        owner_key="worker-a",
        evaluator=lambda **_kwargs: calls.append("first") or _result(gaps=1),
        now=datetime(2026, 7, 23, 20, 30, tzinfo=UTC),
        calendar=calendar,
        db_manager=isolated_db,
    )
    early = execute_outcome_maintenance_tick(
        owner_key="worker-b",
        evaluator=lambda **_kwargs: calls.append("early") or _result(),
        now=datetime(2026, 7, 23, 23, 59, tzinfo=UTC),
        calendar=calendar,
        db_manager=isolated_db,
    )
    second = execute_outcome_maintenance_tick(
        owner_key="worker-b",
        evaluator=lambda **_kwargs: calls.append("second") or _result(inserted=1),
        now=datetime(2026, 7, 24, 0, 30, tzinfo=UTC),
        calendar=calendar,
        db_manager=isolated_db,
    )

    assert first.state == "degraded"
    assert early.action == "idle"
    assert second.action == "evaluated"
    assert second.state == "completed"
    assert calls == ["first", "second"]


def test_scheduler_never_competes_with_canonical_premarket_window(isolated_db):
    previous = date(2026, 7, 22)
    calendar = FakeCalendar(
        {previous: datetime(2026, 7, 22, 20, 0, tzinfo=UTC)}
    )
    calls = []

    result = execute_outcome_maintenance_tick(
        owner_key="worker-a",
        evaluator=lambda **kwargs: calls.append(kwargs) or _result(),
        # 09:12 ET during July daylight time.
        now=datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
        calendar=calendar,
        db_manager=isolated_db,
    )

    assert result.state == "premarket_protected"
    assert calls == []


def test_early_close_uses_exchange_close_not_fixed_wall_clock(isolated_db):
    session = date(2026, 7, 3)
    calendar = FakeCalendar(
        {session: datetime(2026, 7, 3, 17, 0, tzinfo=UTC)}
    )
    calls = []

    result = execute_outcome_maintenance_tick(
        owner_key="worker-a",
        evaluator=lambda **kwargs: calls.append(kwargs) or _result(),
        now=datetime(2026, 7, 3, 17, 30, tzinfo=UTC),
        calendar=calendar,
        db_manager=isolated_db,
    )

    assert result.action == "evaluated"
    assert len(calls) == 1


def test_daemon_stops_without_repeating_idle_logs_or_work():
    ticked = threading.Event()

    def tick():
        ticked.set()
        return OutcomeMaintenanceTickResult(
            action="idle",
            state="waiting_settlement",
            session_date_et=None,
            attempt_count=0,
            due_snapshot_count=0,
            inserted_outcomes=0,
            data_gap_horizons=0,
            error_code=None,
        )

    scheduler = CanonicalOpportunityOutcomeScheduler(
        tick,
        interval_seconds=60,
    )
    scheduler.start()
    assert ticked.wait(timeout=1)
    scheduler.stop(join_timeout_seconds=1)

    assert scheduler.running is False
