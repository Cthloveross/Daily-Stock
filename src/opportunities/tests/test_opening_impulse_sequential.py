from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from src.opportunities.opening_impulse import compute_opening_impulse_profile
from src.opportunities.opening_impulse_sequential import (
    OPENING_IMPULSE_SEQUENTIAL_RULE_VERSION,
    compute_sequential_opening_impulse_profile,
)

ET = ZoneInfo("America/New_York")
TARGET = "2026-08-31"


def _prior_sessions(count: int = 20) -> list[str]:
    calendar = xcals.get_calendar("XNYS")
    session = calendar.date_to_session(TARGET, direction="none")
    result: list[str] = []
    for _ in range(count):
        session = calendar.previous_session(session)
        result.append(session.date().isoformat())
    result.reverse()
    return result


def _minute_bar(
    day: str,
    offset: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> dict:
    return {
        "date": datetime(2026, 1, 1, 9, 30, tzinfo=ET)
        .replace(
            year=int(day[:4]),
            month=int(day[5:7]),
            day=int(day[8:10]),
            minute=30 + offset,
        )
        .isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _reference_minutes(days: list[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for day in days or _prior_sessions():
        for offset in range(5):
            open_ = 100.0 + offset * 0.1
            rows.append(
                _minute_bar(
                    day,
                    offset,
                    open_=open_,
                    high=open_ + 0.2,
                    low=open_ - 0.1,
                    close=open_ + 0.1,
                    volume=100.0,
                )
            )
    return rows


def _strong_up_minutes() -> list[dict]:
    rows: list[dict] = []
    for offset in range(5):
        open_ = 100.0 + offset * 0.9
        rows.append(
            _minute_bar(
                TARGET,
                offset,
                open_=open_,
                high=open_ + 1.0,
                low=open_ - 0.05,
                close=open_ + 0.9,
                volume=250.0,
            )
        )
    return rows


def _weak_minutes() -> list[dict]:
    return [
        _minute_bar(
            TARGET,
            offset,
            open_=100.0 + offset * 0.1,
            high=100.25 + offset * 0.1,
            low=99.95 + offset * 0.1,
            close=100.1 + offset * 0.1,
            volume=50.0,
        )
        for offset in range(5)
    ]


def _compute(current: list[dict], *, minute: int, second: int = 0, **kwargs):
    return compute_sequential_opening_impulse_profile(
        current,
        reference_1m_bars=_reference_minutes(),
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, minute, second, tzinfo=ET),
        atr14=2.0,
        prior_close=99.5,
        **kwargs,
    )


def test_earliest_alert_is_0932_after_two_same_direction_passes():
    rows = _strong_up_minutes()

    first = _compute(rows, minute=31)
    assert first["state"] == "pending"
    assert first["alert_qualified"] is False
    assert first["current_checkpoint_pass"] is True
    assert first["retryable_pending"] is False
    assert first["confirmation_points"] == [1]
    assert first["unavailable_reason"] == "awaiting_consecutive_confirmation"
    assert first["reference_median_volume"] == 100.0
    assert first["opening_volume_rvol_20d"] == 2.5

    second = _compute(rows, minute=32)
    assert second["state"] == "qualified"
    assert second["alert_qualified"] is True
    assert second["direction"] == "up"
    assert second["checkpoint"] == 2
    assert second["checkpoint_label"] == "09:32"
    assert second["confirmation_points"] == [1, 2]
    assert second["confirmation_labels"] == ["09:31", "09:32"]
    assert second["confirmation_mode"] == "consecutive_1m"
    assert second["reference_median_volume"] == 200.0
    assert second["opening_volume_rvol_20d"] == 2.5
    assert second["rule_version"] == OPENING_IMPULSE_SEQUENTIAL_RULE_VERSION
    assert [item["checkpoint"] for item in second["completed_checkpoints"]] == [
        1,
        2,
    ]

    # A later observer must describe evidence ending in its own checkpoint,
    # not replay the already-stale 09:31/09:32 pair forever.
    third = _compute(rows, minute=33)
    assert third["checkpoint"] == 3
    assert third["checkpoint_label"] == "09:33"
    assert third["confirmation_labels"] == ["09:32", "09:33"]


def test_adjacent_passes_with_opposite_directions_do_not_confirm():
    rows = [
        _minute_bar(
            TARGET,
            0,
            open_=100.0,
            high=100.85,
            low=99.95,
            close=100.8,
            volume=250.0,
        ),
        _minute_bar(
            TARGET,
            1,
            open_=100.8,
            high=100.85,
            low=97.95,
            close=98.0,
            volume=250.0,
        ),
    ]

    profile = _compute(rows, minute=32)

    assert [item["passed"] for item in profile["completed_checkpoints"]] == [
        True,
        True,
    ]
    assert [item["direction"] for item in profile["completed_checkpoints"]] == [
        "up",
        "down",
    ]
    assert profile["state"] == "pending"
    assert profile["alert_qualified"] is False
    assert profile["confirmation_points"] == [2]
    assert profile["current_checkpoint_pass"] is True


def test_future_minutes_are_ignored_at_each_checkpoint():
    rows = _weak_minutes()
    # A spectacular 09:32-start bar is present in the provider payload, but it
    # cannot affect the profile observed when only 09:30 and 09:31 are closed.
    rows[2] = _minute_bar(
        TARGET,
        2,
        open_=100.2,
        high=110.0,
        low=100.1,
        close=109.5,
        volume=100_000.0,
    )

    profile = _compute(rows, minute=32)

    assert profile["state"] == "not_qualified"
    assert profile["checkpoint"] == 2
    assert profile["high"] < 101.0
    assert [item["checkpoint"] for item in profile["completed_checkpoints"]] == [
        1,
        2,
    ]


def test_missing_completed_current_minute_is_retryable_but_single_pass_is_not():
    missing = _compute([], minute=31, second=2)
    assert missing["state"] == "pending"
    assert missing["retryable_pending"] is True
    assert missing["current_checkpoint_pass"] is None
    assert missing["unavailable_reason"] == "completed_1m_checkpoint_not_available"

    waiting = _compute(_strong_up_minutes(), minute=31, second=2)
    assert waiting["state"] == "pending"
    assert waiting["retryable_pending"] is False
    assert waiting["current_checkpoint_pass"] is True
    assert waiting["unavailable_reason"] == "awaiting_consecutive_confirmation"


def test_malformed_individual_minute_fails_closed_before_aggregation():
    current = _strong_up_minutes()
    # A later minute still makes the cumulative envelope look plausible, but
    # this source row is individually impossible and must not be hidden by it.
    current[0]["high"] = current[0]["close"] - 0.01

    profile = _compute(current, minute=32)

    assert profile["state"] == "pending"
    assert profile["alert_qualified"] is False
    assert profile["retryable_pending"] is True
    assert profile["unavailable_reason"] == "completed_1m_checkpoint_not_available"

    references = _reference_minutes()
    references[0]["low"] = float("nan")
    unavailable = compute_sequential_opening_impulse_profile(
        _strong_up_minutes(),
        reference_1m_bars=references,
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 31, tzinfo=ET),
        atr14=2.0,
    )
    assert unavailable["state"] == "unavailable"
    assert unavailable["unavailable_reason"] == (
        "exact_prior_20_checkpoint_history_incomplete"
    )


def test_exact_prior_20_sessions_do_not_backfill_a_missing_session():
    exact_days = _prior_sessions()
    references = _reference_minutes(exact_days)
    missing_day = exact_days[-3]
    references = [
        row
        for row in references
        if not (row["date"].startswith(missing_day) and "T09:30:00" in row["date"])
    ]
    older = xcals.get_calendar("XNYS").previous_session(exact_days[0])
    references.extend(_reference_minutes([older.date().isoformat()]))

    profile = compute_sequential_opening_impulse_profile(
        _strong_up_minutes(),
        reference_1m_bars=references,
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 31, tzinfo=ET),
        atr14=2.0,
    )

    assert profile["state"] == "unavailable"
    assert profile["reference_session_count"] == 19
    assert profile["retryable_pending"] is False
    assert profile["unavailable_reason"] == (
        "exact_prior_20_checkpoint_history_incomplete"
    )


def _native_v1_profile() -> dict:
    current = {
        "date": f"{TARGET}T09:30:00-04:00",
        "open": 100.0,
        "high": 102.0,
        "low": 100.0,
        "close": 101.8,
        "volume": 1_000.0,
    }
    references = [
        {
            "date": f"{day}T09:30:00-04:00",
            "open": 100.0,
            "high": 100.4,
            "low": 99.9,
            "close": 100.2,
            "volume": 500.0,
        }
        for day in _prior_sessions()
    ]
    return compute_opening_impulse_profile(
        [current],
        reference_bars=references,
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 35, 2, tzinfo=ET),
        atr14=2.0,
        prior_close=99.5,
    )


def test_0935_five_completed_1m_confirm_once_and_ignore_native_v1():
    native = _native_v1_profile()
    assert native["state"] == "qualified"

    # Strong 1m prefixes also pass earlier pairs; at 09:35 the five completed
    # 1m rows are authoritative and the compatibility native profile is ignored.
    profile = _compute(
        _strong_up_minutes(), minute=35, second=2, native_5m_profile=native
    )

    assert profile["state"] == "qualified"
    assert profile["alert_qualified"] is True
    assert profile["rule_version"] == OPENING_IMPULSE_SEQUENTIAL_RULE_VERSION
    assert profile["checkpoint"] == 5
    assert profile["checkpoint_label"] == "09:35"
    assert profile["confirmation_points"] == [5]
    assert profile["confirmation_mode"] == "completed_1m_cumulative"
    assert profile["current_checkpoint_pass"] is True


def test_0935_missing_completed_1m_is_retryable_and_native_cannot_substitute():
    native = _native_v1_profile()
    missing = _compute(
        _strong_up_minutes()[:-1],
        minute=35,
        second=2,
        native_5m_profile=native,
    )
    assert missing["state"] == "pending"
    assert missing["retryable_pending"] is True
    assert missing["unavailable_reason"] == "completed_1m_checkpoint_not_available"

    # Even a future-looking native 5m profile cannot qualify or veto the v2 path.
    future_native = _native_v1_profile()
    future_native["completed_as_of"] = "2026-08-31T09:35:03-04:00"
    weak = _compute(
        _weak_minutes(),
        minute=35,
        second=2,
        native_5m_profile=future_native,
    )
    assert weak["state"] == "not_qualified"
    assert weak["alert_qualified"] is False
    assert weak["retryable_pending"] is False
    assert weak["unavailable_reason"] is None
