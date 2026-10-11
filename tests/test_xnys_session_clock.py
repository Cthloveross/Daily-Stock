from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.services import xnys_session_clock
from src.services.xnys_session_clock import resolve_xnys_session_clock

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("now", "phase"),
    [
        (datetime(2026, 8, 31, 9, 29, tzinfo=ET), "premarket"),
        (datetime(2026, 8, 31, 9, 30, tzinfo=ET), "regular"),
        (datetime(2026, 8, 31, 15, 59, tzinfo=ET), "regular"),
        (datetime(2026, 8, 31, 16, 0, tzinfo=ET), "afterhours"),
    ],
)
def test_regular_xnys_session_uses_exact_schedule(now, phase):
    result = resolve_xnys_session_clock(now)

    assert result.state == "session"
    assert result.phase == phase
    assert result.market_date_et == date(2026, 8, 31)
    assert result.open_minute_et == 9 * 60 + 30
    assert result.close_minute_et == 16 * 60
    assert result.open_at_et == datetime(2026, 8, 31, 9, 30, tzinfo=ET)
    assert result.close_at_et == datetime(2026, 8, 31, 16, 0, tzinfo=ET)
    assert result.is_early_close is False
    assert result.unavailable_reason is None


def test_real_xnys_half_day_returns_1300_close():
    # Friday after US Thanksgiving 2026 is an official XNYS early close.
    result = resolve_xnys_session_clock(
        datetime(2026, 11, 27, 12, 0, tzinfo=ET)
    )

    assert result.state == "session"
    assert result.phase == "regular"
    assert result.open_minute_et == 9 * 60 + 30
    assert result.close_minute_et == 13 * 60
    assert result.close_at_et == datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    assert result.is_early_close is True

    at_close = resolve_xnys_session_clock(
        datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    )
    assert at_close.phase == "afterhours"


def test_real_xnys_holiday_has_no_invented_schedule():
    # US Labor Day 2026.
    result = resolve_xnys_session_clock(
        datetime(2026, 9, 7, 10, 0, tzinfo=ET)
    )

    assert result.state == "holiday"
    assert result.phase == "holiday"
    assert result.open_minute_et is None
    assert result.close_minute_et is None
    assert result.open_at_et is None
    assert result.close_at_et is None
    assert result.is_early_close is None
    assert result.unavailable_reason is None


class _BrokenCalendar:
    def is_session(self, _market_date):
        raise RuntimeError("calendar backend failed")


def test_calendar_loader_failure_is_explicitly_unavailable(monkeypatch):
    def fail_to_load():
        raise ImportError("exchange_calendars missing")

    monkeypatch.setattr(
        xnys_session_clock,
        "_default_xnys_calendar",
        fail_to_load,
    )
    result = resolve_xnys_session_clock(
        datetime(2026, 8, 31, 10, 0, tzinfo=ET)
    )

    assert result.state == "unavailable"
    assert result.open_minute_et is None
    assert result.close_minute_et is None
    assert result.unavailable_reason == "xnys_calendar_unavailable:ImportError"


def test_calendar_lookup_failure_is_explicitly_unavailable():
    result = resolve_xnys_session_clock(
        datetime(2026, 8, 31, 10, 0, tzinfo=ET),
        calendar=_BrokenCalendar(),
    )

    assert result.state == "unavailable"
    assert result.phase == "unavailable"
    assert result.open_minute_et is None
    assert result.close_minute_et is None
    assert result.is_early_close is None
    assert result.unavailable_reason == "xnys_session_lookup_failed:RuntimeError"


class _MalformedScheduleCalendar:
    def is_session(self, _market_date):
        return True

    def session_open(self, _market_date):
        return datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc)

    def session_close(self, _market_date):
        return datetime(2026, 8, 31, 16, 0)  # deliberately naive


def test_malformed_schedule_does_not_fall_back_to_1600():
    result = resolve_xnys_session_clock(
        datetime(2026, 8, 31, 10, 0, tzinfo=ET),
        calendar=_MalformedScheduleCalendar(),
    )

    assert result.state == "unavailable"
    assert result.open_minute_et is None
    assert result.close_minute_et is None
    assert result.unavailable_reason == "xnys_schedule_unavailable:ValueError"


def test_naive_now_fails_closed_before_calendar_lookup():
    result = resolve_xnys_session_clock(datetime(2026, 8, 31, 10, 0))

    assert result.state == "unavailable"
    assert result.market_date_et == date(2026, 8, 31)
    assert result.open_minute_et is None
    assert result.close_minute_et is None
    assert result.unavailable_reason == "now_not_timezone_aware"
