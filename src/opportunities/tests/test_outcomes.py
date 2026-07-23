from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import exchange_calendars as xcals
import pytest

from src.opportunities.outcomes import (
    HORIZON_THRESHOLDS_PCT,
    OutcomeCalendarError,
    evaluate_opportunity_outcomes,
)


@pytest.fixture(scope="module")
def xnys():
    return xcals.get_calendar("XNYS")


def _as_datetime(value) -> datetime:
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    assert isinstance(converted, datetime)
    return converted.astimezone(timezone.utc)


def _session_dates(calendar, signal_session: date) -> list[date]:
    return [value.date() for value in calendar.sessions_window(signal_session, 21)]


def _bars(sessions: list[date], *, close: float = 100.0) -> list[dict]:
    return [
        {
            "date": session.isoformat(),
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000.0,
        }
        for session in sessions
    ]


def _update_bar(bars: list[dict], session: date, **values) -> None:
    row = next(item for item in bars if item["date"] == session.isoformat())
    row.update(values)


def _eligible_generated_at(calendar, signal_session: date) -> datetime:
    signal_close = _as_datetime(calendar.session_close(signal_session))
    entry = calendar.next_session(signal_session)
    entry_open = _as_datetime(calendar.session_open(entry))
    return signal_close + (entry_open - signal_close) / 2


def _after_session_close(calendar, session: date) -> datetime:
    return _as_datetime(calendar.session_close(session)) + timedelta(minutes=1)


def _evaluate(
    calendar,
    *,
    signal_session: date,
    direction: str = "LONG",
    stock_bars: list[dict],
    spy_bars: list[dict] | None,
    now: datetime,
    generated_at: datetime | None = None,
    reference_close: float = 100.0,
    spy_reference_close: float | None = 100.0,
):
    return evaluate_opportunity_outcomes(
        signal_session=signal_session,
        generated_at=(
            generated_at
            if generated_at is not None
            else _eligible_generated_at(calendar, signal_session)
        ),
        reference_close=reference_close,
        direction=direction,
        stock_bars=stock_bars,
        spy_bars=spy_bars,
        spy_reference_close=spy_reference_close,
        calendar=calendar,
        now=now,
    )


def test_xnys_schedule_skips_holiday_and_uses_exact_5_20_sessions(xnys):
    signal = date(2026, 7, 2)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    _update_bar(stock, sessions[5], close=100.5)
    _update_bar(stock, sessions[20], close=101.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        now=_after_session_close(xnys, sessions[20]),
    )

    assert result["schedule"] == {
        "signal_session": "2026-07-02",
        "signal_close_at": "2026-07-02T20:00:00+00:00",
        "entry_session": "2026-07-06",
        "entry_open_at": "2026-07-06T13:30:00+00:00",
        "x5_session": "2026-07-10",
        "x20_session": "2026-07-31",
    }
    assert HORIZON_THRESHOLDS_PCT == {5: 0.5, 20: 1.0}
    assert result["horizons"]["5d"]["label"] == "CONTEXT_HIT"
    assert result["horizons"]["20d"]["label"] == "CONTEXT_HIT"
    assert result["horizons"]["5d"]["state"] == "COMPLETE"
    assert result["horizons"]["20d"]["state"] == "COMPLETE"
    assert result["horizons"]["5d"]["raw_close_return_pct"] == pytest.approx(0.5)
    assert result["horizons"]["20d"]["raw_close_return_pct"] == pytest.approx(1.0)
    expected_prices = {
        "reference_close": 100.0,
        "entry_session": "2026-07-06",
        "entry_open": 100.0,
        "target_session": "2026-07-10",
        "end_close": 100.5,
        "max_high": 101.0,
        "min_low": 99.0,
        "spy_reference_close": 100.0,
        "spy_entry_open": 100.0,
        "spy_end_close": 100.0,
    }
    horizon_5d = result["horizons"]["5d"]
    assert {key: horizon_5d[key] for key in expected_prices} == expected_prices


def test_frozen_references_drive_returns_and_revision_mismatch_is_partial(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions, close=200.0)
    _update_bar(stock, signal, close=105.0)
    _update_bar(stock, sessions[3], high=111.0, low=95.0)
    _update_bar(stock, sessions[5], close=110.0, high=110.0)
    _update_bar(spy, signal, close=205.0)
    _update_bar(spy, sessions[5], close=220.0, high=220.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        reference_close=100.0,
        spy_reference_close=200.0,
        now=_after_session_close(xnys, sessions[5]),
    )
    horizon = result["horizons"]["5d"]

    assert horizon["state"] == "PARTIAL"
    assert horizon["reference_close"] == 100.0
    assert horizon["refetched_reference_close"] == 105.0
    assert horizon["raw_close_return_pct"] == pytest.approx(10.0)
    assert horizon["entry_session"] == sessions[1].isoformat()
    assert horizon["entry_open"] == 100.0
    assert horizon["target_session"] == sessions[5].isoformat()
    assert horizon["end_close"] == 110.0
    assert horizon["max_high"] == 111.0
    assert horizon["min_low"] == 95.0
    assert horizon["spy_reference_close"] == 200.0
    assert horizon["spy_refetched_reference_close"] == 205.0
    assert horizon["spy_entry_open"] == 200.0
    assert horizon["spy_end_close"] == 220.0
    assert horizon["spy_close_return_pct"] == pytest.approx(10.0)
    assert horizon["raw_excess_spy_pct"] == pytest.approx(0.0)
    assert "reference_revision_mismatch" in horizon["data_gaps"]
    assert "spy_reference_revision_mismatch" in horizon["data_gaps"]


def test_freeze_must_be_strictly_before_next_regular_open(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    entry_open = _as_datetime(xnys.session_open(sessions[1]))
    after_x5 = _after_session_close(xnys, sessions[5])

    eligible = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        generated_at=entry_open - timedelta(microseconds=1),
        now=after_x5,
    )
    ineligible = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        generated_at=entry_open,
        now=after_x5,
    )

    assert eligible["eligible"] is True
    assert ineligible["eligible"] is False
    assert ineligible["ineligibility_reason"] == "not_frozen_before_entry_open"
    assert ineligible["horizons"]["5d"]["state"] == "INELIGIBLE"
    assert ineligible["horizons"]["5d"]["label"] is None


def test_xnys_early_close_controls_signal_completion(xnys):
    signal = date(2026, 11, 27)  # Black Friday, 13:00 ET close.
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    close_at = _as_datetime(xnys.session_close(signal))
    now = _after_session_close(xnys, sessions[5])

    before_close = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        generated_at=close_at - timedelta(seconds=1),
        now=now,
    )
    after_close = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        generated_at=close_at + timedelta(seconds=1),
        now=now,
    )

    assert close_at == datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    assert before_close["ineligibility_reason"] == "signal_session_not_complete_at_freeze"
    assert after_close["eligible"] is True


def test_target_bar_is_pending_until_exact_session_close(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    target_close = _as_datetime(xnys.session_close(sessions[5]))

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        now=target_close - timedelta(microseconds=1),
    )

    horizon = result["horizons"]["5d"]
    assert horizon["state"] == "PENDING"
    assert horizon["raw_close_return_pct"] is None
    assert horizon["label"] is None


@pytest.mark.parametrize(
    ("target_close", "expected_label"),
    [
        (99.5, "CONTEXT_MISS"),
        (100.499, "NEUTRAL"),
    ],
)
def test_long_5d_threshold_boundaries(xnys, target_close, expected_label):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    _update_bar(stock, sessions[5], close=target_close)

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        now=_after_session_close(xnys, sessions[5]),
    )

    assert result["horizons"]["5d"]["label"] == expected_label


def test_short_signed_returns_and_excursions_are_directionally_symmetric(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    _update_bar(stock, sessions[2], high=105.0)
    _update_bar(stock, sessions[3], low=90.0)
    _update_bar(stock, sessions[5], close=99.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        direction="SHORT",
        stock_bars=stock,
        spy_bars=spy,
        now=_after_session_close(xnys, sessions[5]),
    )
    horizon = result["horizons"]["5d"]

    assert horizon["raw_close_return_pct"] == pytest.approx(-1.0)
    assert horizon["signed_close_return_pct"] == pytest.approx(1.0)
    assert horizon["signed_entry_return_pct"] == pytest.approx(1.0)
    assert horizon["mfe_pct"] == pytest.approx(10.0)
    assert horizon["mae_pct"] == pytest.approx(-5.0)
    assert horizon["label"] == "CONTEXT_HIT"


@pytest.mark.parametrize(
    ("direction", "label"),
    [
        ("MIXED", "NON_DIRECTIONAL"),
        ("UNKNOWN", "DIRECTION_UNKNOWN"),
    ],
)
def test_mixed_and_unknown_keep_raw_returns_without_direction_metrics(
    xnys,
    direction,
    label,
):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    _update_bar(stock, sessions[5], close=102.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        direction=direction,
        stock_bars=stock,
        spy_bars=spy,
        now=_after_session_close(xnys, sessions[5]),
    )
    horizon = result["horizons"]["5d"]

    assert horizon["state"] == "COMPLETE"
    assert horizon["raw_close_return_pct"] == pytest.approx(2.0)
    assert horizon["raw_entry_return_pct"] == pytest.approx(2.0)
    assert horizon["signed_close_return_pct"] is None
    assert horizon["signed_entry_return_pct"] is None
    assert horizon["signed_excess_spy_pct"] is None
    assert horizon["mfe_pct"] is None
    assert horizon["mae_pct"] is None
    assert horizon["label"] == label


def test_missing_entry_open_and_path_ohlc_degrade_only_path_metrics(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    _update_bar(stock, sessions[1], open=None)
    _update_bar(stock, sessions[3], high=None)
    _update_bar(stock, sessions[5], close=102.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        now=_after_session_close(xnys, sessions[5]),
    )
    horizon = result["horizons"]["5d"]

    assert horizon["state"] == "PARTIAL"
    assert horizon["label"] == "CONTEXT_HIT"
    assert horizon["raw_close_return_pct"] == pytest.approx(2.0)
    assert horizon["raw_entry_return_pct"] is None
    assert horizon["mfe_pct"] is None
    assert horizon["mae_pct"] is None
    assert "stock_entry_open" in horizon["data_gaps"]
    assert f"stock_ohlc:{sessions[3].isoformat()}" in horizon["data_gaps"]


def test_spy_requires_strictly_aligned_sessions_not_just_endpoints(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    missing_session = sessions[3]
    spy = [row for row in spy if row["date"] != missing_session.isoformat()]
    _update_bar(stock, sessions[5], close=102.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        now=_after_session_close(xnys, sessions[5]),
    )
    horizon = result["horizons"]["5d"]

    assert horizon["state"] == "PARTIAL"
    assert horizon["raw_close_return_pct"] == pytest.approx(2.0)
    assert horizon["spy_close_return_pct"] is None
    assert horizon["raw_excess_spy_pct"] is None
    assert horizon["signed_excess_spy_pct"] is None
    assert f"spy_session:{missing_session.isoformat()}" in horizon["data_gaps"]


def test_spy_return_never_falls_back_to_refetched_signal_close(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions, close=200.0)
    _update_bar(stock, sessions[5], close=102.0)
    _update_bar(spy, sessions[5], close=220.0, high=220.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        spy_reference_close=None,
        now=_after_session_close(xnys, sessions[5]),
    )
    horizon = result["horizons"]["5d"]

    assert horizon["state"] == "PARTIAL"
    assert horizon["spy_reference_close"] is None
    assert horizon["spy_refetched_reference_close"] == 200.0
    assert horizon["spy_end_close"] == 220.0
    assert horizon["spy_close_return_pct"] is None
    assert horizon["raw_excess_spy_pct"] is None
    assert "spy_reference_close" in horizon["data_gaps"]


def test_missing_exact_target_close_is_data_gap_and_never_shifts_forward(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)
    spy = _bars(sessions)
    _update_bar(stock, sessions[5], close=None)
    _update_bar(stock, sessions[6], close=120.0)

    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=stock,
        spy_bars=spy,
        now=_after_session_close(xnys, sessions[6]),
    )
    horizon = result["horizons"]["5d"]

    assert horizon["target_session"] == sessions[5].isoformat()
    assert horizon["state"] == "DATA_GAP"
    assert horizon["raw_close_return_pct"] is None
    assert horizon["label"] is None
    assert horizon["data_gaps"] == ["stock_target_close"]


def test_calendar_errors_fail_closed_and_naive_times_are_rejected(xnys):
    class BrokenCalendar:
        def is_session(self, _session):
            raise RuntimeError("calendar unavailable")

    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    stock = _bars(sessions)

    with pytest.raises(OutcomeCalendarError, match="could not validate"):
        evaluate_opportunity_outcomes(
            signal_session=signal,
            generated_at=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
            reference_close=100.0,
            direction="LONG",
            stock_bars=stock,
            calendar=BrokenCalendar(),
            now=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_opportunity_outcomes(
            signal_session=signal,
            generated_at=datetime(2026, 7, 22, 12),
            reference_close=100.0,
            direction="LONG",
            stock_bars=stock,
            calendar=xnys,
            now=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )


def test_default_calendar_resolves_xnys_without_external_state(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    result = evaluate_opportunity_outcomes(
        signal_session=signal,
        generated_at=_eligible_generated_at(xnys, signal),
        reference_close=100.0,
        direction="LONG",
        stock_bars=_bars(sessions),
        spy_bars=_bars(sessions),
        spy_reference_close=100.0,
        now=_after_session_close(xnys, sessions[5]),
    )

    assert result["schedule"]["entry_session"] == "2026-07-22"
    assert result["schedule"]["x5_session"] == "2026-07-28"


def test_payload_never_emits_trade_or_causal_attribution_categories(xnys):
    signal = date(2026, 7, 21)
    sessions = _session_dates(xnys, signal)
    result = _evaluate(
        xnys,
        signal_session=signal,
        stock_bars=_bars(sessions),
        spy_bars=_bars(sessions),
        now=_after_session_close(xnys, sessions[20]),
    )
    payload = json.dumps(result, sort_keys=True)

    for forbidden in (
        "TRUE_POSITIVE",
        "FALSE_POSITIVE",
        "MISSED_OPPORTUNITY",
        "REGIME_MISMATCH",
    ):
        assert forbidden not in payload
