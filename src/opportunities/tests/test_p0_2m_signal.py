from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pytest

from src.opportunities.p0_2m_signal import (
    P0_2M_SIGNAL_VERSION,
    classify_p0_session,
    compute_p0_2m_signal,
    is_duplicate_p0_episode,
)

ET = ZoneInfo("America/New_York")
TARGET_DATE = "2026-09-10"
TARGET = date.fromisoformat(TARGET_DATE)


def _bar(start: datetime, open_: float, close: float, volume: float) -> dict:
    return {
        "date": start.isoformat(),
        "open": open_,
        "high": max(open_, close) + 0.01,
        "low": min(open_, close) - 0.01,
        "close": close,
        "volume": volume,
    }


def _linear_bars(
    day: date,
    *,
    start_minute: int,
    count: int,
    start_price: float,
    end_price: float,
    volume: float,
) -> list[dict]:
    prices = [
        start_price + (end_price - start_price) * index / count
        for index in range(count + 1)
    ]
    midnight = datetime(day.year, day.month, day.day, tzinfo=ET)
    return [
        _bar(
            midnight + timedelta(minutes=start_minute + 2 * index),
            prices[index],
            prices[index + 1],
            volume,
        )
        for index in range(count)
    ]


def _prior_sessions(count: int = 60) -> list[date]:
    calendar = xcals.get_calendar("XNYS")
    cursor = calendar.date_to_session(TARGET, direction="none")
    sessions: list[date] = []
    for _ in range(count):
        cursor = calendar.previous_session(cursor)
        sessions.append(cursor.date())
    sessions.reverse()
    return sessions


def _regular_fixture(
    *,
    displacement: float = 0.60,
    reference_count: int = 60,
) -> tuple[list[dict], list[dict]]:
    # Keep the final four-bar impulse at roughly +0.40 while varying the full
    # 18-minute displacement.  This isolates the forming/strict ATR boundary
    # from the already-passed short-window gates.
    impulse_start_price = 100.0 + displacement - 0.40
    current = _linear_bars(
        TARGET,
        start_minute=9 * 60 + 30,
        count=5,
        start_price=100.0,
        end_price=impulse_start_price,
        volume=100.0,
    )
    current.extend(
        _linear_bars(
            TARGET,
            start_minute=9 * 60 + 40,
            count=4,
            start_price=impulse_start_price,
            end_price=100.0 + displacement,
            volume=200.0,
        )
    )

    references: list[dict] = []
    sessions = _prior_sessions()
    for index, session in enumerate(sessions[-reference_count:]):
        # 57/60 historical absolute impulses are below the current impulse and
        # three are above it, giving an empirical percentile of exactly 0.95.
        historical_log_move = 0.002 if index < reference_count - 3 else 0.008
        references.extend(
            _linear_bars(
                session,
                start_minute=9 * 60 + 40,
                count=4,
                start_price=100.0,
                end_price=100.0 * math.exp(historical_log_move),
                volume=100.0,
            )
        )
    return current, references


def _compute(current: list[dict], references: list[dict], **overrides) -> dict:
    kwargs = {
        "reference_bars": references,
        "market_date_et": TARGET_DATE,
        "as_of": datetime(2026, 9, 10, 9, 48, 5, tzinfo=ET),
        "prior_daily_atr14": 1.0,
        "symbol": "TSLA",
        "source": "moomoo_1m_exact_2m",
        "universe_hash": "sha256:test",
    }
    kwargs.update(overrides)
    return compute_p0_2m_signal(current, **kwargs)


def test_strict_regular_signal_uses_8m_percentile_rvol_and_18m_atr():
    current, references = _regular_fixture()

    evidence = _compute(current, references)

    assert evidence["state"] == "qualified"
    assert evidence["alert_qualified"] is True
    assert evidence["alert_tier"] == "P0"
    assert evidence["signal_version"] == P0_2M_SIGNAL_VERSION
    assert evidence["session_scope"] == "regular"
    assert evidence["direction"] == "up"
    assert evidence["price_percentile"] == 0.95
    assert evidence["rvol_8m"] == 2.0
    assert evidence["displacement_atr"] == pytest.approx(0.6)
    assert evidence["baseline_count"] == 60
    assert evidence["source"] == "moomoo_1m_exact_2m"
    assert evidence["data_quality"] == {"status": "complete", "issues": []}
    assert evidence["option_candidates"] == []
    research = evidence["research_fields"]
    assert research["path_efficiency_8m"]["state"] == "ready"
    assert 0 < research["path_efficiency_8m"]["value"] <= 1
    assert research["vwap_distance_8m_percent"] == {
        "state": "unavailable",
        "value": None,
        "reason": "dollar_turnover_unavailable",
        "basis": "(C4/(sum(amount)/sum(volume))-1)*100;research_only",
    }
    assert research["market_residual_8m"]["state"] == "unavailable"
    assert research["market_residual_8m"]["reason"] == (
        "benchmark_model_not_configured"
    )
    assert "burst_score" not in evidence


def test_research_vwap_field_records_but_never_changes_signal_gates():
    current, references = _regular_fixture()
    for row in current:
        row["amount"] = row["volume"] * (row["open"] + row["close"]) / 2

    evidence = _compute(current, references)

    assert evidence["state"] == "qualified"
    assert evidence["research_fields"]["vwap_distance_8m_percent"][
        "state"
    ] == "ready"
    assert isinstance(
        evidence["research_fields"]["vwap_distance_8m_percent"]["value"],
        float,
    )


def test_displacement_between_point_three_and_point_five_is_forming_only():
    current, references = _regular_fixture(displacement=0.40)

    evidence = _compute(current, references)

    assert evidence["state"] == "forming"
    assert evidence["alert_qualified"] is False
    assert evidence["alert_tier"] is None
    assert evidence["displacement_atr"] == pytest.approx(0.4)
    assert evidence["failed_checks"] == ["displacement_atr_below_0.50"]


def test_price_and_volume_without_point_three_atr_is_not_even_forming():
    current, references = _regular_fixture(displacement=0.29)

    evidence = _compute(current, references)

    assert evidence["state"] == "not_qualified"
    assert evidence["alert_qualified"] is False
    assert evidence["price_percentile"] == 0.95
    assert evidence["rvol_8m"] == 2.0
    assert evidence["displacement_atr"] == pytest.approx(0.29)


def test_fewer_than_sixty_complete_same_slot_sessions_fails_closed():
    current, references = _regular_fixture(reference_count=59)

    evidence = _compute(current, references)

    assert evidence["state"] == "unavailable"
    assert evidence["alert_qualified"] is False
    assert evidence["baseline_count"] == 59
    assert evidence["unavailable_reason"] == (
        "fewer_than_60_complete_same_slot_baselines"
    )


def test_early_close_inside_prior_sixty_is_not_replaced_by_an_older_day():
    target = date(2026, 12, 1)
    target_date = target.isoformat()
    impulse_start_price = 100.2
    current = _linear_bars(
        target,
        start_minute=15 * 60 + 42,
        count=5,
        start_price=100.0,
        end_price=impulse_start_price,
        volume=100.0,
    )
    current.extend(
        _linear_bars(
            target,
            start_minute=15 * 60 + 52,
            count=4,
            start_price=impulse_start_price,
            end_price=100.6,
            volume=200.0,
        )
    )

    calendar = xcals.get_calendar("XNYS")
    cursor = calendar.date_to_session(target, direction="none")
    prior_61: list[date] = []
    for _ in range(61):
        cursor = calendar.previous_session(cursor)
        prior_61.append(cursor.date())
    references: list[dict] = []
    for session in prior_61:
        references.extend(
            _linear_bars(
                session,
                start_minute=15 * 60 + 52,
                count=4,
                start_price=100.0,
                end_price=100.2,
                volume=100.0,
            )
        )

    evidence = compute_p0_2m_signal(
        current,
        reference_bars=references,
        market_date_et=target_date,
        as_of=datetime(2026, 12, 1, 16, 0, 5, tzinfo=ET),
        prior_daily_atr14=1.0,
        symbol="TSLA",
        source="moomoo_1m_exact_2m",
        universe_hash="sha256:test",
    )

    # 2026-11-27 is an XNYS early close.  Its 15:52 clock slot is not RTH,
    # so the declared prior-60-trading-day baseline has only 59 matches.  The
    # 61st older session in ``references`` must not be pulled into the window.
    assert evidence["state"] == "unavailable"
    assert evidence["unavailable_reason"] == (
        "fewer_than_60_prior_matching_xnys_sessions"
    )


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "malformed"])
def test_current_18m_gap_duplicate_or_malformed_bar_fails_closed(mutation: str):
    current, references = _regular_fixture()
    target_start = datetime(2026, 9, 10, 9, 36, tzinfo=ET).isoformat()
    selected = next(row for row in current if row["date"] == target_start)
    if mutation == "missing":
        current.remove(selected)
    elif mutation == "duplicate":
        current.append(dict(selected))
    else:
        selected["high"] = selected["low"] - 1.0

    evidence = _compute(current, references)

    assert evidence["state"] == "unavailable"
    assert evidence["alert_qualified"] is False
    assert evidence["unavailable_reason"] == (
        "current_18m_window_missing_duplicate_or_invalid"
    )


def test_forming_two_minute_bar_is_never_used():
    current, references = _regular_fixture()
    current.append(
        _bar(datetime(2026, 9, 10, 9, 48, tzinfo=ET), 100.6, 110.0, 99_000)
    )

    evidence = _compute(
        current,
        references,
        as_of=datetime(2026, 9, 10, 9, 49, 59, tzinfo=ET),
    )

    assert evidence["state"] == "qualified"
    assert evidence["window_end"].startswith("2026-09-10T09:48:00")
    assert evidence["trigger_price"] == pytest.approx(100.6)


def test_target_and_future_rows_never_enter_the_baseline():
    current, references = _regular_fixture()
    references.extend(
        _linear_bars(
            TARGET,
            start_minute=9 * 60 + 40,
            count=4,
            start_price=100.0,
            end_price=200.0,
            volume=999_999.0,
        )
    )
    references.extend(
        _linear_bars(
            date(2026, 9, 11),
            start_minute=9 * 60 + 40,
            count=4,
            start_price=100.0,
            end_price=200.0,
            volume=999_999.0,
        )
    )

    evidence = _compute(current, references)

    assert evidence["state"] == "qualified"
    assert evidence["price_percentile"] == 0.95
    assert evidence["rvol_8m"] == 2.0


def test_direction_mismatch_blocks_strict_p0():
    current, references = _regular_fixture()
    # Preserve a positive 8m impulse but make the full 18m displacement down.
    for row in current[:5]:
        shift = 1.0
        row["open"] += shift
        row["high"] += shift
        row["low"] += shift
        row["close"] += shift

    evidence = _compute(current, references)

    assert evidence["direction"] == "up"
    assert evidence["displacement_atr"] == pytest.approx(0.4)
    assert evidence["state"] == "not_qualified"
    assert "impulse_displacement_direction_mismatch" in evidence["failed_checks"]


def test_qualified_premarket_signal_is_p0x():
    regular_current, regular_references = _regular_fixture()
    current = []
    # Reuse prices/volumes but place the exact 18m window at 09:02--09:20.
    for row in regular_current:
        moved = dict(row)
        moved["date"] = (
            datetime.fromisoformat(row["date"]) - timedelta(minutes=28)
        ).isoformat()
        current.append(moved)
    references = []
    for row in regular_references:
        moved = dict(row)
        moved["date"] = (
            datetime.fromisoformat(row["date"]) - timedelta(minutes=28)
        ).isoformat()
        references.append(moved)

    evidence = _compute(
        current,
        references,
        as_of=datetime(2026, 9, 10, 9, 20, 5, tzinfo=ET),
    )

    assert evidence["state"] == "qualified"
    assert evidence["session_scope"] == "premarket"
    assert evidence["alert_tier"] == "P0X"


def test_session_classifier_rejects_cross_boundary_and_handles_afterhours():
    assert classify_p0_session(
        datetime(2026, 9, 10, 15, 42, tzinfo=ET),
        datetime(2026, 9, 10, 16, 0, tzinfo=ET),
    ) == "regular"
    assert classify_p0_session(
        datetime(2026, 9, 10, 16, 0, tzinfo=ET),
        datetime(2026, 9, 10, 16, 18, tzinfo=ET),
    ) == "afterhours"
    assert classify_p0_session(
        datetime(2026, 9, 10, 15, 50, tzinfo=ET),
        datetime(2026, 9, 10, 16, 8, tzinfo=ET),
    ) is None


def test_same_symbol_same_direction_is_suppressed_only_inside_thirty_minutes():
    prior = {
        "symbol": "tsla",
        "direction": "up",
        "window_end": datetime(2026, 9, 10, 10, 0, tzinfo=ET).isoformat(),
    }
    assert is_duplicate_p0_episode(
        symbol="TSLA",
        direction="up",
        window_end=datetime(2026, 9, 10, 10, 29, 59, tzinfo=ET),
        previous_events=[prior],
    )
    assert not is_duplicate_p0_episode(
        symbol="TSLA",
        direction="up",
        window_end=datetime(2026, 9, 10, 10, 30, tzinfo=ET),
        previous_events=[prior],
    )
    assert not is_duplicate_p0_episode(
        symbol="TSLA",
        direction="down",
        window_end=datetime(2026, 9, 10, 10, 10, tzinfo=ET),
        previous_events=[prior],
    )
