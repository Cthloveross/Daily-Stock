from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.opportunities.opening_impulse import compute_opening_impulse_profile

ET = ZoneInfo("America/New_York")
TARGET = "2026-08-31"


def _bar(
    day: str,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> dict:
    return {
        "date": datetime.fromisoformat(f"{day}T09:30:00").replace(
            tzinfo=ET
        ).isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _references(count: int = 20) -> list[dict]:
    target = datetime(2026, 8, 31, tzinfo=ET)
    rows = []
    for index in range(count):
        day = (target - timedelta(days=count - index)).date().isoformat()
        # 18/20 volumes below TSLA and 19/20 ranges at/below TSLA.
        volume = 1_000_000 if index < 18 else 2_000_000
        bar_range = 3.0 if index < 19 else 10.0
        rows.append(
            _bar(
                day,
                open_=300.0,
                high=300.0 + bar_range,
                low=300.0,
                close=301.5,
                volume=volume,
            )
        )
    return rows


def _tsla_bar() -> dict:
    return _bar(
        TARGET,
        open_=347.21,
        high=354.855,
        low=347.15,
        close=352.56,
        volume=1_644_899,
    )


def test_first_bar_is_never_used_before_it_is_complete():
    profile = compute_opening_impulse_profile(
        [_tsla_bar()],
        reference_bars=_references(),
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 34, 59, tzinfo=ET),
        atr14=13.05,
        prior_close=348.75,
    )
    assert profile["state"] == "pending"
    assert profile["alert_qualified"] is False
    assert profile["unavailable_reason"] == "first_5m_bar_not_completed"


def test_malformed_completed_first_bar_does_not_trigger_opening_impulse():
    malformed = _tsla_bar()
    malformed["high"] = malformed["close"] - 0.01

    profile = compute_opening_impulse_profile(
        [malformed],
        reference_bars=_references(),
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 35, 5, tzinfo=ET),
        atr14=13.05,
        prior_close=348.75,
    )

    assert profile["state"] == "pending"
    assert profile["alert_qualified"] is False
    assert profile["unavailable_reason"] == "completed_first_5m_bar_not_available"


def test_tsla_2026_08_31_opening_bar_qualifies_without_lookahead():
    references = _references()
    # Target/future rows deliberately contaminate the long fetch; strict date
    # filtering must keep both out of the 20-session denominator.
    references.extend(
        [
            _bar(
                TARGET,
                open_=1,
                high=100,
                low=1,
                close=100,
                volume=999_999_999,
            ),
            _bar(
                "2026-09-01",
                open_=1,
                high=100,
                low=1,
                close=100,
                volume=999_999_999,
            ),
        ]
    )
    profile = compute_opening_impulse_profile(
        [_tsla_bar()],
        reference_bars=references,
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 35, 5, tzinfo=ET),
        atr14=13.05,
        prior_close=348.75,
    )

    assert profile["state"] == "qualified"
    assert profile["alert_qualified"] is True
    assert profile["direction"] == "up"
    assert profile["reference_session_count"] == 20
    assert profile["body_percent"] == pytest.approx(1.540854, abs=1e-6)
    assert profile["body_atr"] == pytest.approx(0.409962, abs=1e-6)
    assert profile["range_atr"] == pytest.approx(0.590421, abs=1e-6)
    assert profile["directional_close_location"] == pytest.approx(
        0.702141, abs=1e-6
    )
    assert profile["volume_percentile_20d"] == 0.9
    assert profile["range_percentile_20d"] == 0.95
    assert profile["gap_percent"] == pytest.approx(-0.441577, abs=1e-6)
    assert profile["reclaimed_prior_close"] is True
    assert profile["failed_checks"] == []


def test_fewer_than_20_same_slot_sessions_fails_closed():
    profile = compute_opening_impulse_profile(
        [_tsla_bar()],
        reference_bars=_references(19),
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 35, 5, tzinfo=ET),
        atr14=13.05,
    )
    assert profile["state"] == "unavailable"
    assert profile["reference_session_count"] == 19
    assert profile["unavailable_reason"] == "fewer_than_20_prior_opening_slots"


def test_weak_or_long_wick_first_bar_is_not_qualified():
    weak = _bar(
        TARGET,
        open_=100.0,
        high=102.0,
        low=98.0,
        close=100.2,
        volume=1_644_899,
    )
    profile = compute_opening_impulse_profile(
        [weak],
        reference_bars=_references(),
        market_date_et=TARGET,
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 9, 35, 5, tzinfo=ET),
        atr14=13.05,
    )
    assert profile["state"] == "not_qualified"
    assert "body_atr_below_0.30" in profile["failed_checks"]
    assert "body_efficiency_below_0.60" in profile["failed_checks"]
