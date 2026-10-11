# -*- coding: utf-8 -*-
"""Deterministic orchestration tests for the strict 2m P0 monitor."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import threading
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from src.opportunities.p0_2m_signal import P0_2M_SIGNAL_VERSION
from src.services import intraday_p0_2m_monitor as monitor_module
from src.services.intraday_p0_2m_monitor import IntradayP02mMonitor
from src.services.moomoo_runtime import MoomooCircuitBreaker


UTC = timezone.utc
ET = ZoneInfo("America/New_York")
_NOW = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)  # 10:00 ET
_MARKET_DATE = "2026-09-10"


def _enrollment(*, key="a" * 64, first_session=_MARKET_DATE):
    import exchange_calendars as xcals

    sessions = xcals.get_calendar("XNYS").sessions_window(first_session, 20)
    return {
        "enrollment_key": key,
        "sessions": tuple(day.date().isoformat() for day in sessions),
    }


def _monitor(
    symbols: list[str],
    observed: list[dict[str, Any]],
    **kwargs: Any,
) -> IntradayP02mMonitor:
    return IntradayP02mMonitor(
        symbols,
        lambda payload: observed.append(dict(payload)),
        clock=lambda: _NOW,
        **kwargs,
    )


def _history(
    *,
    state: str = "ready",
    reason: str | None = None,
    bars: tuple[dict[str, Any], ...] = (),
) -> monitor_module._HistoryState:
    return monitor_module._HistoryState(
        bars=bars,
        source="MoomooFetcher",
        prior_daily_atr14=5.0 if state == "ready" else None,
        market_date_et=_MARKET_DATE,
        state=state,
        unavailable_reason=reason,
    )


def _without_history_thread(
    monkeypatch: pytest.MonkeyPatch,
    monitor: IntradayP02mMonitor,
) -> None:
    monkeypatch.setattr(monitor, "_ensure_history_worker", lambda _date: None)


def _complete_evidence(window_end: str) -> dict[str, Any]:
    return {
        "state": "qualified",
        "alert_qualified": True,
        "alert_tier": "P0",
        "signal_version": P0_2M_SIGNAL_VERSION,
        "session_scope": "regular",
        "window_end": window_end,
        "source": "MoomooFetcher",
        "data_quality": {"status": "complete", "issues": []},
    }


def _current_2m_bars(*, start_minute=40, end_minute=60) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "date": f"2026-09-10T09:{minute:02d}:00-04:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 100.0,
        }
        for minute in range(start_minute, end_minute, 2)
    )


@lru_cache(maxsize=2)
def _complete_reference_bars(market_date_et=_MARKET_DATE):
    import exchange_calendars as xcals

    calendar = xcals.get_calendar("XNYS")
    cursor = calendar.date_to_session(date.fromisoformat(market_date_et))
    rows = []
    for _ in range(60):
        cursor = calendar.previous_session(cursor)
        for minute in range(240, 1200, 2):
            start = datetime.combine(cursor.date(), datetime.min.time(), ET)
            rows.append(
                {
                    "date": (start + timedelta(minutes=minute)).isoformat(),
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 100.0,
                }
            )
    return tuple(rows)


def test_full_69_symbol_scan_is_never_truncated(monkeypatch):
    symbols = [f"T{i:02d}" for i in range(69)]
    observed: list[dict[str, Any]] = []
    monitor = _monitor(symbols, observed)
    _without_history_thread(monkeypatch, monitor)

    result = monitor.scan_once()

    assert result["candidate_count"] == 69
    assert result["baseline_warming_count"] == 69
    assert len(observed) == 1
    payload = observed[0]
    assert [row["ticker"] for row in payload["candidates"]] == symbols
    assert payload["universe_scan"] == {
        "mode": "p0_2m_full_watchlist",
        "watchlist_total": 69,
        "watchlist_scanned": 69,
        "watchlist_truncated": False,
        "universe_hash": monitor.universe_hash,
    }


def test_health_reports_live_warm_progress_before_first_scan():
    observed: list[dict[str, Any]] = []
    monitor = _monitor(["READY", "BAD", "WARM"], observed)
    monitor._subscribed = True
    monitor._histories = {
        "READY": _history(),
        "BAD": _history(
            state="unavailable",
            reason="exact_2m_history_empty",
        ),
    }

    health = monitor.health_snapshot()

    assert health["last_scan_at"] is None
    assert health["ready_count"] == 1
    assert health["baseline_warming_count"] == 1
    assert health["unavailable_count"] == 1


@pytest.mark.parametrize(
    ("observed", "expected_active"),
    [
        (datetime(2026, 10, 10, 10, 0, tzinfo=ET), False),  # Saturday
        (datetime(2026, 9, 7, 10, 0, tzinfo=ET), False),  # Labor Day
        (datetime(2026, 9, 10, 3, 59, tzinfo=ET), False),
        (datetime(2026, 9, 10, 4, 0, tzinfo=ET), True),
        (datetime(2026, 9, 10, 19, 59, tzinfo=ET), True),
        (datetime(2026, 9, 10, 20, 0, tzinfo=ET), False),
    ],
)
def test_health_distinguishes_closed_scan_window_from_missing_baseline(
    observed, expected_active,
):
    monitor = IntradayP02mMonitor(
        ["TSLA"], lambda _payload: True, clock=lambda: observed
    )
    monitor._histories = {"TSLA": _history()}
    monitor._subscribed = True

    snapshot = monitor.health_snapshot()

    assert snapshot["scan_window_active"] is expected_active
    assert snapshot["history_loaded_count"] == 1
    if not expected_active:
        assert snapshot["ready_count"] == 0
        assert snapshot["scan_window_age_seconds"] is None
    else:
        assert snapshot["scan_window_age_seconds"] == (
            observed - observed.replace(hour=4, minute=0, second=0)
        ).total_seconds()


@pytest.mark.parametrize("raises", [False, True])
def test_health_unknown_calendar_is_not_reported_as_a_closed_window(monkeypatch, raises):
    from types import SimpleNamespace
    from src.services import xnys_session_clock

    def unknown_calendar(_now):
        if raises:
            raise RuntimeError("calendar reader unavailable")
        return SimpleNamespace(state="unavailable")

    monkeypatch.setattr(xnys_session_clock, "resolve_xnys_session_clock", unknown_calendar)
    monitor = _monitor(["TSLA"], [])
    monitor._histories = {"TSLA": _history()}
    monitor._subscribed = True

    snapshot = monitor.health_snapshot()

    assert snapshot["scan_window_active"] is None
    assert snapshot["ready_count"] == 0
    assert snapshot["history_loaded_count"] == 1


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        (datetime(2026, 9, 10, 17, 0, tzinfo=ET), "2026-09-10"),
        (datetime(2026, 9, 10, 21, 0, tzinfo=ET), "2026-09-11"),
        (datetime(2026, 9, 11, 21, 0, tzinfo=ET), "2026-09-14"),
        (datetime(2026, 9, 7, 12, 0, tzinfo=ET), "2026-09-08"),
    ],
)
def test_history_target_prewarms_the_next_actionable_session(
    observed: datetime,
    expected: str,
):
    assert IntradayP02mMonitor._history_target_market_date(observed) == expected


@pytest.mark.parametrize(
    ("market_date_et", "as_of_date_et", "expected_start", "expected_days"),
    [
        ("2026-09-10", date(2026, 9, 10), date(2026, 6, 15), 87),
        ("2026-09-14", date(2026, 9, 13), date(2026, 6, 17), 88),
    ],
)
def test_live_history_span_starts_at_exact_sixtieth_prior_xnys_session(
    market_date_et: str,
    as_of_date_et: date,
    expected_start: date,
    expected_days: int,
):
    days = monitor_module._intraday_history_calendar_days(
        market_date_et,
        as_of_date_et=as_of_date_et,
    )

    assert days == expected_days
    assert as_of_date_et - timedelta(days=days) == expected_start
    assert days < 150


def test_default_history_loader_stops_after_intraday_provenance_fallback(
    monkeypatch,
):
    from src.services import stock_service as stock_service_module

    calls: list[tuple[str, int]] = []

    class _StockService:
        def get_history_data(
            self,
            _symbol,
            *,
            period,
            days,
            include_stock_name,
        ):
            calls.append((period, days))
            assert include_stock_name is False
            if period != "2m":
                raise AssertionError("daily fallback must not run")
            return {
                "data": ({"date": "2026-09-09T09:30:00-04:00"},),
                "source": "YfinanceFetcher",
                "aggregation_contract": "session_anchored_exact_2m_v2",
            }

    monkeypatch.setattr(stock_service_module, "StockService", _StockService)
    monkeypatch.setattr(
        monitor_module,
        "_intraday_history_calendar_days",
        lambda _market_date: 87,
    )

    loaded = monitor_module._default_history_loader("TSLA", _MARKET_DATE)

    assert calls == [("2m", 87)]
    assert loaded["source"] == "YfinanceFetcher"
    assert loaded["prior_daily_atr14"] is None


def test_afterhours_scan_does_not_replace_next_session_prewarm(monkeypatch):
    now = datetime(2026, 9, 10, 21, 0, tzinfo=ET)
    observed: list[dict[str, Any]] = []
    requested_dates: list[str] = []
    monitor = IntradayP02mMonitor(
        ["TSLA"],
        lambda payload: observed.append(dict(payload)),
        clock=lambda: now,
    )
    monkeypatch.setattr(
        monitor,
        "_ensure_history_worker",
        lambda market_date_et: requested_dates.append(market_date_et),
    )

    monitor.scan_once()

    assert requested_dates == ["2026-09-11"]
    assert observed[0]["market_date_et"] == "2026-09-10"


def test_complete_window_is_emitted_once_until_window_end_advances(
    monkeypatch,
):
    observed: list[dict[str, Any]] = []
    monitor = _monitor(["TSLA"], observed)
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"TSLA": _history()}
    monitor._subscribed = True
    next_window = {"value": "2026-09-10T09:48:00-04:00"}

    monkeypatch.setattr(
        monitor_module,
        "compute_p0_2m_signal",
        lambda *_args, **_kwargs: _complete_evidence(next_window["value"]),
    )

    first = monitor.scan_once()
    duplicate = monitor.scan_once()
    next_window["value"] = "2026-09-10T09:50:00-04:00"
    advanced = monitor.scan_once()

    assert first["candidate_count"] == 1
    assert duplicate["candidate_count"] == 0
    assert advanced["candidate_count"] == 1
    assert len(observed) == 2
    assert [
        payload["candidates"][0]["p0_signal_evidence_v2"]["window_end"]
        for payload in observed
    ] == [
        "2026-09-10T09:48:00-04:00",
        "2026-09-10T09:50:00-04:00",
    ]


def test_warming_unavailable_and_stream_down_all_fail_closed(monkeypatch):
    observed: list[dict[str, Any]] = []
    monitor = _monitor(["WARM", "BAD", "DOWN"], observed)
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {
        "BAD": _history(
            state="unavailable",
            reason="exact_2m_history_empty",
        ),
        "DOWN": _history(),
    }
    monitor._subscribed = False

    result = monitor.scan_once()

    assert result == {
        "candidate_count": 3,
        "ready_count": 0,
        "baseline_warming_count": 1,
        "unavailable_count": 2,
        "mobile_enabled": False,
        "mobile_reason": "action_switch_disabled",
        "observer_accepted": True,
    }
    evidence = {
        row["ticker"]: row["p0_signal_evidence_v2"]
        for row in observed[0]["candidates"]
    }
    assert evidence["WARM"]["state"] == "baseline_warming"
    assert evidence["WARM"]["unavailable_reason"] == "baseline_warming"
    assert evidence["BAD"]["state"] == "shadow"
    assert evidence["BAD"]["unavailable_reason"] == "exact_2m_history_empty"
    assert evidence["DOWN"]["state"] == "shadow"
    assert (
        evidence["DOWN"]["unavailable_reason"]
        == "live_1m_subscription_unavailable"
    )
    assert all(item["alert_qualified"] is False for item in evidence.values())
    assert all(
        item["data_quality"]["status"] == "incomplete"
        for item in evidence.values()
    )
    health = monitor.health_snapshot()
    assert health["schema_version"] == "intraday_p0_2m_health_v1"
    assert health["last_scan_at"] == _NOW.isoformat()
    assert health["stream_ready"] is False
    assert health["baseline_warming_count"] == 1
    assert health["unavailable_count"] == 2
    assert health["mobile_reason"] == "action_switch_disabled"


def test_mobile_activation_requires_switch_artifact_and_exact_identity(
    monkeypatch,
    tmp_path,
):
    observed: list[dict[str, Any]] = []
    disabled = _monitor(["TSLA"], observed)
    assert disabled._activation_status() == (False, "action_switch_disabled")

    no_channel = _monitor(
        ["TSLA"],
        observed,
        action_enabled=True,
        delivery_ready=False,
        delivery_unavailable_reason="telegram_credentials_missing",
    )
    assert no_channel._activation_status() == (
        False,
        "telegram_credentials_missing",
    )

    missing = _monitor(["TSLA"], observed, action_enabled=True)
    assert missing._activation_status() == (
        False,
        "activation_artifact_not_configured",
    )

    artifact = tmp_path / "activation.json"
    artifact.write_text("{}", encoding="utf-8")
    enabled = _monitor(
        ["TSLA"],
        observed,
        action_enabled=True,
        activation_artifact=str(artifact),
    )
    called: dict[str, Any] = {}

    def validate(path: Path, **kwargs: Any):
        called["path"] = path
        called.update(kwargs)
        return True, "ok", ("TSLA",)

    monkeypatch.setattr(
        "src.opportunities.p0_2m_calibration.validated_mobile_eligible_symbols",
        validate,
    )

    assert enabled._activation_status() == (True, "ok")
    assert called == {
        "path": artifact,
        "expected_universe_hash": enabled.universe_hash,
        "expected_signal_version": P0_2M_SIGNAL_VERSION,
    }

    monkeypatch.setattr(
        "src.opportunities.p0_2m_calibration.validated_mobile_eligible_symbols",
        lambda *_args, **_kwargs: (
            False,
            "gate_failed:shadow_availability",
            (),
        ),
    )
    assert enabled._activation_status() == (
        False,
        "gate_failed:shadow_availability",
    )


@pytest.mark.parametrize("scope", ["regular", "premarket", "afterhours"])
def test_scan_marks_mobile_eligibility_per_symbol_from_validated_artifact(
    monkeypatch,
    tmp_path,
    scope,
):
    observed: list[dict[str, Any]] = []
    artifact = tmp_path / "activation.json"
    artifact.write_text("{}", encoding="utf-8")
    monitor = _monitor(
        ["TSLA", "TQQQ"],
        observed,
        action_enabled=True,
        activation_artifact=str(artifact),
    )
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"TSLA": _history(), "TQQQ": _history()}
    monitor._subscribed = True
    monkeypatch.setattr(
        "src.opportunities.p0_2m_calibration.validated_mobile_eligible_symbols",
        lambda *_args, **_kwargs: (True, "ok", ("TSLA",)),
    )
    monkeypatch.setattr(
        monitor_module,
        "compute_p0_2m_signal",
        lambda *_args, symbol, **_kwargs: {
            **_complete_evidence("2026-09-10T09:48:00-04:00"),
            "session_scope": scope,
        },
    )

    result = monitor.scan_once()

    assert result["mobile_enabled"] is True
    rows = {row["ticker"]: row for row in observed[0]["candidates"]}
    assert rows["TSLA"]["p0_mobile_eligible"] is (scope == "regular")
    assert rows["TQQQ"]["p0_mobile_eligible"] is False
    assert observed[0]["p0_mobile_delivery"]["eligible_symbol_count"] == 1


def test_stream_revision_replaces_same_provider_minute_before_exact_2m_merge():
    observed: list[dict[str, Any]] = []
    monitor = _monitor(["TSLA"], observed)
    monitor._handle_stream_rows(
        [
            {
                "code": "US.TSLA",
                "time_key": "2026-09-10 09:31:00",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 100,
                "turnover": 10_000,
            },
            {
                "code": "US.TSLA",
                "time_key": "2026-09-10 09:31:00",
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 150,
                "turnover": 15_000,
            },
            {
                "code": "US.TSLA",
                "time_key": "2026-09-10 09:32:00",
                "open": 101,
                "high": 103,
                "low": 100,
                "close": 102,
                "volume": 200,
                "turnover": 20_000,
            },
        ]
    )

    assert len(monitor._stream_rows["TSLA"]) == 2
    bars = monitor._merged_current_bars(
        "TSLA",
        _history(),
        as_of=datetime(2026, 9, 10, 9, 33, tzinfo=ET),
        market_date_et=_MARKET_DATE,
    )

    assert bars == [
        {
            "date": "2026-09-10T09:30:00-04:00",
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 102.0,
            "volume": 350.0,
            "amount": 35_000.0,
            "pct_chg": 0.0,
        }
    ]


def test_rejected_observer_retries_the_same_completed_window(monkeypatch):
    calls: list[str] = []

    def observer(payload):
        window_end = payload["candidates"][0]["p0_signal_evidence_v2"][
            "window_end"
        ]
        calls.append(window_end)
        return len(calls) > 1

    monitor = IntradayP02mMonitor(
        ["TSLA"],
        observer,
        clock=lambda: _NOW,
    )
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"TSLA": _history()}
    monitor._subscribed = True
    monkeypatch.setattr(
        monitor_module,
        "compute_p0_2m_signal",
        lambda *_args, **_kwargs: _complete_evidence(
            "2026-09-10T09:48:00-04:00"
        ),
    )

    assert monitor.scan_once()["observer_accepted"] is False
    assert "TSLA" not in monitor._last_complete_window
    assert monitor.scan_once()["observer_accepted"] is True
    assert monitor._last_complete_window["TSLA"] == (
        "2026-09-10T09:48:00-04:00"
    )
    assert calls == [
        "2026-09-10T09:48:00-04:00",
        "2026-09-10T09:48:00-04:00",
    ]


def test_scan_health_uses_completion_time_not_start_time(monkeypatch):
    clock = {"now": _NOW}

    def observer(_payload):
        clock["now"] += timedelta(seconds=15)
        return True

    monitor = IntradayP02mMonitor(["TSLA"], observer, clock=lambda: clock["now"])
    _without_history_thread(monkeypatch, monitor)
    monitor._started_at = _NOW - timedelta(seconds=20)
    monitor.scan_once()
    clock["now"] += timedelta(seconds=5)

    health = monitor.health_snapshot()

    assert health["last_scan_at"] == _NOW.isoformat()
    assert health["scan_completed_at"] == (_NOW + timedelta(seconds=15)).isoformat()
    assert health["scan_age_seconds"] == 5
    assert health["startup_age_seconds"] == 40


def test_completed_1m_cache_is_detached_current_and_never_fetches(monkeypatch):
    monitor = _monitor(["TSLA"], [])
    monitor._subscribed = True
    monitor._stream_rows["TSLA"] = {
        label: {"date": label, "close": 100}
        for label in (
            "2026-09-09 09:31:00", "2026-09-10 09:31:00",
            "2026-09-10 09:32:00", "2026-09-10 09:33:00",
        )
    }
    monkeypatch.setattr(
        monitor, "_history_loader",
        lambda *_args: pytest.fail("opening cache must never fetch history"),
    )
    now = datetime(2026, 9, 10, 9, 32, 5, tzinfo=ET)

    rows, source = monitor.get_completed_1m_bars("TSLA", as_of=now)

    assert source == "MoomooFetcher"
    assert [row["date"] for row in rows] == [
        "2026-09-10 09:31:00", "2026-09-10 09:32:00",
    ]
    rows[0]["close"] = 999
    assert monitor._stream_rows["TSLA"]["2026-09-10 09:31:00"]["close"] == 100
    monitor._subscribed = False
    assert monitor.get_completed_1m_bars("TSLA", as_of=now) == ([], None)
    with pytest.raises(ValueError, match="timezone-aware"):
        monitor.get_completed_1m_bars("TSLA", as_of=now.replace(tzinfo=None))


def test_shadow_run_lifecycle_keeps_final_endpoint_grace():
    enrollment = _enrollment()
    clock = {"now": datetime(2026, 9, 10, 9, 29, tzinfo=ET)}
    monitor = IntradayP02mMonitor(
        ["TSLA"], lambda _payload: True, clock=lambda: clock["now"],
        shadow_enroller=lambda **_kwargs: enrollment,
    )
    monitor._ensure_shadow_enrollment(clock["now"])
    health = monitor.health_snapshot()
    assert health["shadow_run_state"] == "waiting"
    assert health["shadow_start_session"] == enrollment["sessions"][0]
    assert health["shadow_end_session"] == enrollment["sessions"][-1]
    clock["now"] = clock["now"].replace(hour=9, minute=30)
    assert monitor.health_snapshot()["shadow_run_state"] == "collecting"
    last_close = datetime.fromisoformat(enrollment["sessions"][-1]).replace(
        hour=16, tzinfo=ET,
    )
    clock["now"] = last_close + timedelta(seconds=75)
    assert monitor.health_snapshot()["shadow_run_state"] == "collecting"
    clock["now"] += timedelta(seconds=1)
    assert monitor.health_snapshot()["shadow_run_state"] == "completed"
    assert monitor.health_snapshot()["shadow_enrollment_ready"] is False


def test_expired_shadow_run_skips_writes_but_preserves_research_scan(monkeypatch):
    observed, recorded = [], []
    monitor = _monitor(
        ["TSLA"], observed,
        shadow_enroller=lambda **_kwargs: _enrollment(first_session="2026-07-01"),
        shadow_recorder=lambda rows: recorded.extend(rows),
    )
    _without_history_thread(monkeypatch, monitor)

    assert monitor.scan_once()["observer_accepted"] is True
    assert len(observed) == 1
    assert recorded == []
    assert monitor.health_snapshot()["shadow_run_state"] == "completed"


def test_enrollment_read_failure_clears_stale_ready_and_key(monkeypatch):
    monitor = _monitor(
        ["TSLA"], [], shadow_enroller=lambda **_kwargs: _enrollment(),
    )
    monitor._ensure_shadow_enrollment(_NOW)
    assert monitor.health_snapshot()["shadow_enrollment_ready"] is True

    def broken(**_kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(monitor, "_shadow_enroller", broken)
    monitor._ensure_shadow_enrollment(_NOW)
    health = monitor.health_snapshot()
    assert health["shadow_enrollment_ready"] is False
    assert health["shadow_enrollment_key"] is None
    assert health["shadow_run_state"] == "unknown"
    assert health["shadow_enrollment_error"] == "RuntimeError"


def test_shadow_write_failure_is_visible_and_recovers_without_blocking_signals(monkeypatch):
    state = {"fail": True}

    def recorder(_rows):
        if state["fail"]:
            raise OSError("database unavailable")

    monitor = _monitor(["META"], [], shadow_recorder=recorder)
    _without_history_thread(monkeypatch, monitor)
    assert monitor.scan_once()["observer_accepted"] is True
    first = monitor.health_snapshot()
    assert first["shadow_write_error"] == "OSError"
    assert first["shadow_last_write_failed_at"] == _NOW.isoformat()
    assert first["shadow_last_write_at"] is None
    state["fail"] = False
    assert monitor.scan_once()["observer_accepted"] is True
    recovered = monitor.health_snapshot()
    assert recovered["shadow_write_error"] is None
    assert recovered["shadow_last_write_at"] == _NOW.isoformat()
    assert recovered["shadow_last_write_failed_at"] == first["shadow_last_write_failed_at"]


def test_live_shadow_receipt_records_qualified_queue_latency(monkeypatch):
    now = datetime(2026, 9, 10, 14, 0, 20, tzinfo=UTC)  # 10:00:20 ET
    recorded = []

    def observer(_payload):
        return {
            "accepted": True,
            "observer_queue_enqueued_at": now.isoformat(),
            "candidate_outcomes": [
                {
                    "symbol": "META",
                    "window_end": "2026-09-10T10:00:00-04:00",
                    "notification_decision_available": True,
                    "would_notify": True,
                    "duplicate_episode_suppressed": False,
                    "notification_attempt_id": "probe-message-1",
                    "telegram_enqueue_evidence_mode": (
                        "counterfactual_no_send_same_queue"
                    ),
                    "telegram_queue_accepted": True,
                }
            ],
        }

    monitor = IntradayP02mMonitor(
        ["META"],
        observer,
        clock=lambda: now,
        shadow_recorder=lambda rows: recorded.extend(rows),
    )
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"META": _history(bars=_current_2m_bars())}
    monitor._subscribed = True
    monkeypatch.setattr(
        monitor_module,
        "compute_p0_2m_signal",
        lambda *_args, **_kwargs: _complete_evidence(
            "2026-09-10T10:00:00-04:00"
        ),
    )

    result = monitor.scan_once()

    assert result["observer_accepted"] is True
    assert [row.record_kind for row in recorded] == [
        "endpoint_observation",
        "completed_evaluation",
        "notification_attempt",
    ]
    evaluation = recorded[1]
    assert evaluation.symbol == "META"
    assert evaluation.qualified is True and evaluation.would_notify is True
    assert evaluation.notification_decision_available is True
    assert evaluation.window_close == 100.5
    assert evaluation.data_quality["window_close_basis"]["basis_verified"] is False
    assert recorded[0].window_close is None
    assert recorded[2].window_close is None
    attempt = recorded[2]
    assert attempt.notification_attempt_id == "probe-message-1"
    assert attempt.observer_queue_accepted is True
    assert attempt.observer_queue_enqueued_at == now
    assert attempt.telegram_enqueued_at == now
    assert attempt.telegram_enqueue_evidence_mode == (
        "counterfactual_no_send_same_queue"
    )


def test_shadow_observations_are_explicitly_bound_to_latest_enrollment(monkeypatch):
    now = datetime(2026, 9, 10, 14, 0, 20, tzinfo=UTC)
    recorded = []
    active = {"key": "a" * 64}

    monitor = IntradayP02mMonitor(
        ["META"],
        lambda _payload: True,
        clock=lambda: now,
        shadow_recorder=lambda rows: recorded.extend(rows),
        shadow_enroller=lambda **_kwargs: _enrollment(key=active["key"]),
    )
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"META": _history(bars=_current_2m_bars())}
    monitor._subscribed = True
    monkeypatch.setattr(
        monitor_module,
        "compute_p0_2m_signal",
        lambda *_args, **_kwargs: _complete_evidence(
            "2026-09-10T10:00:00-04:00"
        ),
    )

    monitor.scan_once()
    first_count = len(recorded)
    active["key"] = "b" * 64
    monitor.scan_once()

    assert first_count > 0
    assert {row.enrollment_key for row in recorded[:first_count]} == {"a" * 64}
    assert {row.enrollment_key for row in recorded[first_count:]} == {"b" * 64}
    assert monitor.health_snapshot()["shadow_enrollment_key"] == "b" * 64


def test_shadow_keeps_completed_nonqualified_endpoint_price(monkeypatch):
    now = datetime(2026, 9, 10, 14, 0, 20, tzinfo=UTC)
    recorded = []
    monitor = IntradayP02mMonitor(
        ["META"], lambda _payload: True, clock=lambda: now,
        shadow_recorder=lambda rows: recorded.extend(rows),
    )
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"META": _history(bars=_current_2m_bars())}
    monitor._subscribed = True
    evidence = {
        **_complete_evidence("2026-09-10T10:00:00-04:00"),
        "state": "not_qualified", "alert_qualified": False,
    }
    monkeypatch.setattr(monitor_module, "compute_p0_2m_signal", lambda *_a, **_kw: evidence)
    monitor.scan_once()
    evaluation = next(row for row in recorded if row.record_kind == "completed_evaluation")
    assert evaluation.qualified is False
    assert evaluation.would_notify is False
    assert evaluation.window_close == 100.5
    assert evaluation.data_quality["window_close_basis"]["source"] == "MoomooFetcher"


def test_structural_pending_requires_exact_bars_from_rth_open(monkeypatch):
    now = datetime(2026, 9, 10, 13, 36, 5, tzinfo=UTC)  # 09:36:05 ET
    structural = {
        "state": "pending",
        "alert_qualified": False,
        "alert_tier": None,
        "signal_version": P0_2M_SIGNAL_VERSION,
        "session_scope": "regular",
        "window_end": "2026-09-10T09:36:00-04:00",
        "unavailable_reason": (
            "fewer_than_18_completed_minutes_in_session_stratum"
        ),
        "data_quality": {"status": "incomplete", "issues": []},
    }

    complete_rows = []
    complete = IntradayP02mMonitor(
        ["META"],
        lambda _payload: True,
        clock=lambda: now,
        shadow_recorder=lambda rows: complete_rows.extend(rows),
    )
    _without_history_thread(monkeypatch, complete)
    complete._histories = {
        "META": _history(bars=_current_2m_bars(start_minute=30, end_minute=36))
    }
    complete._subscribed = True
    monkeypatch.setattr(
        monitor_module,
        "compute_p0_2m_signal",
        lambda *_args, **_kwargs: structural,
    )
    complete.scan_once()
    assert [row.record_kind for row in complete_rows] == [
        "endpoint_observation",
        "completed_evaluation",
    ]
    assert complete_rows[0].data_available is True
    assert complete_rows[1].evaluation_available is True
    assert complete_rows[1].max_component_end == datetime.fromisoformat(
        "2026-09-10T09:36:00-04:00"
    )

    missing_rows = []
    missing = IntradayP02mMonitor(
        ["META"],
        lambda _payload: True,
        clock=lambda: now,
        shadow_recorder=lambda rows: missing_rows.extend(rows),
    )
    _without_history_thread(monkeypatch, missing)
    bars_with_gap = tuple(
        row
        for row in _current_2m_bars(start_minute=30, end_minute=36)
        if "09:32:00" not in row["date"]
    )
    missing._histories = {"META": _history(bars=bars_with_gap)}
    missing._subscribed = True
    missing.scan_once()
    assert [row.record_kind for row in missing_rows] == [
        "endpoint_observation"
    ]
    assert missing_rows[0].data_available is False


def test_endpoint_probe_and_later_completed_evaluation_are_separate(monkeypatch):
    clock = {"now": datetime(2026, 9, 10, 14, 0, 5, tzinfo=UTC)}
    recorded = []

    def observer(payload):
        evidence = payload["candidates"][0]["p0_signal_evidence_v2"]
        if evidence.get("alert_qualified") is not True:
            return {"accepted": True, "candidate_outcomes": []}
        return {
            "accepted": True,
            "observer_queue_enqueued_at": clock["now"].isoformat(),
            "candidate_outcomes": [
                {
                    "symbol": "META",
                    "window_end": evidence["window_end"],
                    "notification_decision_available": True,
                    "would_notify": True,
                    "duplicate_episode_suppressed": False,
                    "notification_attempt_id": "probe-retry",
                    "telegram_enqueue_evidence_mode": (
                        "counterfactual_no_send_same_queue"
                    ),
                    "telegram_queue_accepted": True,
                }
            ],
        }

    monitor = IntradayP02mMonitor(
        ["META"],
        observer,
        clock=lambda: clock["now"],
        shadow_recorder=lambda rows: recorded.extend(rows),
    )
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"META": _history()}
    monitor._subscribed = True
    complete = {"value": False}
    monkeypatch.setattr(
        monitor,
        "_merged_current_bars",
        lambda *_args, **_kwargs: _current_2m_bars(
            end_minute=60 if complete["value"] else 58
        ),
    )

    def compute(*_args, **_kwargs):
        evidence = _complete_evidence("2026-09-10T10:00:00-04:00")
        if not complete["value"]:
            evidence.update(
                state="shadow",
                alert_qualified=False,
                data_quality={
                    "status": "incomplete",
                    "issues": ["latest_2m_missing"],
                },
            )
        return evidence

    monkeypatch.setattr(monitor_module, "compute_p0_2m_signal", compute)
    monitor.scan_once()
    complete["value"] = True
    clock["now"] = datetime(2026, 9, 10, 14, 1, 5, tzinfo=UTC)
    monitor.scan_once()

    assert [row.record_kind for row in recorded] == [
        "endpoint_observation",
        "endpoint_observation",
        "completed_evaluation",
        "notification_attempt",
    ]
    assert recorded[0].data_available is False
    assert recorded[2].data_available is True
    assert recorded[3].observer_queue_enqueued_at == clock["now"]


def test_rth_close_endpoint_expires_after_second_probe_window():
    within_retry = datetime(2026, 9, 10, 20, 1, 5, tzinfo=UTC)
    expired = datetime(2026, 9, 10, 20, 1, 16, tzinfo=UTC)
    assert IntradayP02mMonitor._latest_completed_rth_endpoint(
        within_retry
    ) == datetime.fromisoformat("2026-09-10T16:00:00-04:00")
    assert IntradayP02mMonitor._latest_completed_rth_endpoint(expired) is None


def test_shadow_recorder_failure_never_blocks_signal(monkeypatch):
    now = datetime(2026, 9, 10, 14, 0, 20, tzinfo=UTC)

    def broken_recorder(_rows):
        raise RuntimeError("disk unavailable")

    monitor = IntradayP02mMonitor(
        ["META"],
        lambda _payload: {
            "accepted": True,
            "observer_queue_enqueued_at": now.isoformat(),
            "candidate_outcomes": [
                {
                    "symbol": "META",
                    "window_end": "2026-09-10T10:00:00-04:00",
                    "notification_decision_available": True,
                    "would_notify": True,
                    "duplicate_episode_suppressed": False,
                }
            ],
        },
        clock=lambda: now,
        shadow_recorder=broken_recorder,
    )
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"META": _history()}
    monitor._subscribed = True
    monkeypatch.setattr(
        monitor_module,
        "compute_p0_2m_signal",
        lambda *_args, **_kwargs: _complete_evidence(
            "2026-09-10T10:00:00-04:00"
        ),
    )

    result = monitor.scan_once()

    assert result["observer_accepted"] is True
    assert monitor._last_complete_window["META"] == (
        "2026-09-10T10:00:00-04:00"
    )


def test_scan_passes_only_indexed_same_clock_reference_slice(monkeypatch):
    captured: dict[str, Any] = {}
    reference_by_minute = {
        minute: tuple(
            {
                "date": f"2026-06-{(index % 28) + 1:02d}T"
                f"{minute // 60:02d}:{minute % 60:02d}:00-04:00"
            }
            for index in range(60)
        )
        for minute in (9 * 60 + 52, 9 * 60 + 54, 9 * 60 + 56, 9 * 60 + 58)
    }
    current = tuple(
        {
            "date": f"2026-09-10T09:{minute:02d}:00-04:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
        }
        for minute in range(42, 60, 2)
    )
    history = monitor_module._HistoryState(
        bars=tuple({"date": f"unused-{index}"} for index in range(20_000)),
        source="MoomooFetcher",
        prior_daily_atr14=5.0,
        market_date_et=_MARKET_DATE,
        state="ready",
        reference_by_start_minute=reference_by_minute,
        current_session_bars=current,
    )
    monitor = _monitor(["META"], [])
    _without_history_thread(monkeypatch, monitor)
    monitor._histories = {"META": history}
    monitor._subscribed = True

    def compute(current_bars, *, reference_bars, **_kwargs):
        captured["current_count"] = len(current_bars)
        captured["reference_count"] = len(reference_bars)
        return _complete_evidence("2026-09-10T10:00:00-04:00")

    monkeypatch.setattr(monitor_module, "compute_p0_2m_signal", compute)

    monitor.scan_once()

    assert captured == {"current_count": 9, "reference_count": 60 * 4}


def test_history_index_compacts_reference_and_restores_signal_fields(
    monkeypatch,
):
    bars = tuple(
        {
            "date": f"2026-09-09T09:{minute:02d}:00-04:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 200.0,
            "amount": 20_100.0,
            "change_percent": 99.0,
        }
        for minute in (52, 54, 56, 58)
    )

    def reject_scalar_pandas_parse(*_args, **_kwargs):
        raise AssertionError("normal exact-bar ISO time used pd.Timestamp")

    monkeypatch.setattr(
        monitor_module.pd,
        "Timestamp",
        reject_scalar_pandas_parse,
    )

    reference, current = IntradayP02mMonitor._history_indexes(
        bars, _MARKET_DATE
    )
    history = monitor_module._HistoryState(
        bars=(),
        source="MoomooFetcher",
        prior_daily_atr14=5.0,
        market_date_et=_MARKET_DATE,
        state="ready",
        reference_by_start_minute=reference,
        current_session_bars=current,
    )

    restored = IntradayP02mMonitor._reference_slice(
        history,
        as_of=datetime(2026, 9, 10, 10, 0, tzinfo=ET),
    )

    assert len(restored) == 4
    assert all(
        isinstance(row, monitor_module._CompactReferenceBar)
        for rows in reference.values()
        for row in rows
    )
    assert restored[0] == {
        "date": "2026-09-09T09:52:00-04:00",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 200.0,
        "amount": 20_100.0,
    }


@pytest.mark.parametrize("second_load_fails", [False, True])
def test_one_history_bar_is_loaded_but_not_ready_and_retries_are_bounded(
    monkeypatch, second_load_fails,
):
    calls = []
    observed = []
    row = {
        "date": "2026-09-09T09:52:00-04:00",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 100.0,
    }

    def loader(symbol, _market_date):
        calls.append(symbol)
        if second_load_fails and len(calls) == 2:
            raise RuntimeError("temporary retry failure must retain partial cache")
        return {
            "bars": (row,),
            "source": "MoomooFetcher",
            "aggregation_contract": "session_anchored_exact_2m_v2",
            "prior_daily_atr14": 5.0,
        }

    monitor = _monitor(["TSLA"], observed, history_loader=loader)
    monitor._history_market_date = _MARKET_DATE
    monitor._subscribed = True
    monotonic = {"value": 0.0}
    monkeypatch.setattr(
        monitor_module.time, "monotonic", lambda: monotonic["value"]
    )
    for expected_calls in (1, 2, 3):
        monitor._ensure_history_worker(_MARKET_DATE)
        worker = monitor._history_thread
        if worker is not None:
            worker.join(timeout=2)
            assert not worker.is_alive()
        assert len(calls) == expected_calls
        monitor._ensure_history_worker(_MARKET_DATE)
        assert len(calls) == expected_calls  # no per-tick reload
        monotonic["value"] += 61.0

    monitor._ensure_history_worker(_MARKET_DATE)
    assert len(calls) == 3
    assert monitor._histories["TSLA"].state == "partial"
    health = monitor.health_snapshot()
    assert health["history_loaded_count"] == 1
    assert health["ready_count"] == 0
    assert health["baseline_retry_exhausted_count"] == 1
    assert monitor.scan_once()["ready_count"] == 0
    assert observed[0]["candidates"][0]["p0_signal_evidence_v2"][
        "alert_qualified"
    ] is False


def test_missing_late_clock_slot_retries_even_when_early_slot_is_ready(monkeypatch):
    rows = _complete_reference_bars()
    missing = next(row["date"] for row in rows if "09:52:00" in row["date"])
    incomplete = tuple(row for row in rows if row["date"] != missing)
    attempts = []

    def loader(symbol, _market_date):
        attempts.append(symbol)
        return {
            "bars": (incomplete if len(attempts) == 1 else rows)
            + _current_2m_bars(start_minute=42),
            "source": "MoomooFetcher",
            "aggregation_contract": "session_anchored_exact_2m_v2",
            "prior_daily_atr14": 5.0,
        }

    observed = []
    monitor = _monitor(["TSLA"], observed, history_loader=loader)
    _without_history_thread(monkeypatch, monitor)
    monitor._subscribed = True
    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)
    history = monitor._histories["TSLA"]
    assert history.state == "partial"
    assert history.baseline_count_by_end_minute[9 * 60 + 48] == 60
    assert history.baseline_count_by_end_minute[10 * 60] == 59
    assert monitor._history_ready_at(history, _NOW) is False
    assert monitor.scan_once()["ready_count"] == 0
    evidence = observed[0]["candidates"][0]["p0_signal_evidence_v2"]
    assert evidence["baseline_count"] == 59
    assert evidence["unavailable_reason"] == (
        "fewer_than_60_complete_same_slot_baselines"
    )
    assert evidence["alert_qualified"] is False

    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)
    assert attempts == ["TSLA", "TSLA"]
    assert monitor._histories["TSLA"].state == "ready"
    assert monitor._history_ready_at(monitor._histories["TSLA"], _NOW) is True


@pytest.mark.parametrize("defect", ["duplicate", "invalid_volume", "future"])
def test_history_baseline_audit_rejects_duplicate_invalid_and_future_rows(defect):
    rows = list(_complete_reference_bars())
    chosen = next(index for index, row in enumerate(rows) if "09:52:00" in row["date"])
    if defect == "duplicate":
        rows.append(dict(rows[chosen]))
    elif defect == "invalid_volume":
        rows[chosen] = {**rows[chosen], "volume": 0}
    else:
        rows[chosen] = {**rows[chosen], "date": "2026-09-11T09:52:00-04:00"}
    indexed, _current = IntradayP02mMonitor._history_indexes(rows, _MARKET_DATE)

    coverage, requires_retry = IntradayP02mMonitor._history_baseline_coverage(
        indexed, _MARKET_DATE
    )

    assert coverage[10 * 60] == 59
    assert requires_retry is True


def test_half_day_scope_shortage_is_not_replaced_or_retried():
    target = "2026-12-01"  # prior 60 sessions include the Nov 27 13:00 close
    calls = []

    def loader(symbol, _market_date):
        calls.append(symbol)
        return {
            "bars": _complete_reference_bars(target),
            "source": "MoomooFetcher",
            "aggregation_contract": "session_anchored_exact_2m_v2",
            "prior_daily_atr14": 5.0,
        }

    now = datetime(2026, 12, 1, 14, 0, tzinfo=ET)
    monitor = IntradayP02mMonitor(
        ["TSLA"], lambda _payload: True, clock=lambda: now, history_loader=loader
    )
    monitor._subscribed = True
    monitor._warm_histories(target, monitor._history_generation)
    history = monitor._histories["TSLA"]
    assert history.state == "ready"  # all obtainable provider bars are present
    assert history.baseline_count_by_end_minute[10 * 60] == 60
    assert history.baseline_count_by_end_minute[14 * 60] == 59
    assert monitor.health_snapshot()["history_loaded_count"] == 1
    assert monitor.health_snapshot()["ready_count"] == 0
    monitor._warm_histories(target, monitor._history_generation)
    assert calls == ["TSLA"]


@pytest.mark.parametrize("hour, minute", [(4, 6), (9, 36), (16, 6)])
def test_first_18_minutes_are_pending_not_a_failed_history(monkeypatch, hour, minute):
    observed = []
    now = datetime(2026, 9, 10, hour, minute, tzinfo=ET)
    monitor = IntradayP02mMonitor(
        ["TSLA"],
        lambda payload: observed.append(payload),
        clock=lambda: now,
        history_loader=lambda *_args: {
            "bars": _complete_reference_bars(),
            "source": "MoomooFetcher",
            "aggregation_contract": "session_anchored_exact_2m_v2",
            "prior_daily_atr14": 5.0,
        },
    )
    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)
    _without_history_thread(monkeypatch, monitor)
    monitor._subscribed = True

    result = monitor.scan_once()

    assert result["ready_count"] == 1
    assert result["unavailable_count"] == 0
    evidence = observed[0]["candidates"][0]["p0_signal_evidence_v2"]
    assert evidence["state"] == "pending"
    assert evidence["unavailable_reason"] == (
        "fewer_than_18_completed_minutes_in_session_stratum"
    )


def test_session_boundary_keeps_the_previous_completed_slot_readiness():
    history = monitor_module._HistoryState(
        bars=(),
        source="MoomooFetcher",
        prior_daily_atr14=5.0,
        market_date_et=_MARKET_DATE,
        state="partial",
        baseline_count_by_end_minute={16 * 60: 60, 16 * 60 + 18: 0},
    )

    assert IntradayP02mMonitor._history_ready_at(
        history, datetime(2026, 9, 10, 16, 0, 5, tzinfo=ET)
    ) is True
    assert IntradayP02mMonitor._history_ready_at(
        history, datetime(2026, 9, 10, 16, 2, 5, tzinfo=ET)
    ) is False


def test_history_warm_retries_transient_failure_without_reloading_ready_symbol(
    monkeypatch,
):
    attempts = {"TSLA": 0, "META": 0}
    first_meta_started = threading.Event()
    first_meta_release = threading.Event()
    retry_meta_started = threading.Event()
    retry_meta_release = threading.Event()

    def loader(symbol, market_date_et):
        attempts[symbol] += 1
        if symbol == "META" and attempts[symbol] == 1:
            first_meta_started.set()
            assert first_meta_release.wait(timeout=2)
            raise RuntimeError("temporary OpenD failure")
        if symbol == "META" and attempts[symbol] == 2:
            retry_meta_started.set()
            assert retry_meta_release.wait(timeout=2)
        return {
            "bars": _complete_reference_bars(market_date_et),
            "source": "MoomooFetcher",
            "aggregation_contract": "session_anchored_exact_2m_v2",
            "prior_daily_atr14": 5.0,
        }

    monitor = IntradayP02mMonitor(
        ["TSLA", "META"],
        lambda _payload: True,
        clock=lambda: _NOW,
        history_loader=loader,
    )
    monitor._ensure_history_worker(_MARKET_DATE)
    assert first_meta_started.wait(timeout=2)
    first_worker = monitor._history_thread
    assert first_worker is not None
    first_meta_release.set()
    first_worker.join(timeout=2)
    assert monitor._histories["TSLA"].state == "ready"
    assert monitor._histories["TSLA"].bars == ()
    assert monitor._histories["TSLA"].current_session_bars is not None
    assert monitor._histories["META"].state == "unavailable"

    monkeypatch.setattr(monitor_module.time, "monotonic", lambda: 10_000.0)
    monitor._history_next_retry_monotonic = 0.0
    monitor._ensure_history_worker(_MARKET_DATE)
    assert retry_meta_started.wait(timeout=2)
    retry_worker = monitor._history_thread
    assert retry_worker is not None
    retry_meta_release.set()
    retry_worker.join(timeout=2)

    assert attempts == {"TSLA": 1, "META": 2}
    assert monitor._histories["META"].state == "ready"


def test_history_warm_marks_whole_batch_unavailable_when_provider_is_down(
    monkeypatch,
):
    attempts: list[str] = []
    monitor = IntradayP02mMonitor(
        ["TSLA", "META", "NVDA"],
        lambda _payload: True,
        clock=lambda: _NOW,
        history_loader=lambda symbol, _date: attempts.append(symbol),
    )
    monkeypatch.setattr(
        monitor,
        "_history_provider_unavailable_reason",
        lambda *, probe_tcp: "opend_tcp_unreachable",
    )

    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)

    assert attempts == []
    assert set(monitor._histories) == {"TSLA", "META", "NVDA"}
    assert {
        history.unavailable_reason for history in monitor._histories.values()
    } == {"history_batch_stopped:opend_tcp_unreachable"}


def test_history_provider_allows_the_breaker_half_open_probe_after_cooldown(
    monkeypatch,
):
    monotonic = {"value": 0.0}
    breaker = MoomooCircuitBreaker(
        failure_threshold=1,
        cooldown_seconds=60.0,
        clock=lambda: monotonic["value"],
    )
    breaker.record_failure("request timeout")
    attempts: list[str] = []

    def loader(symbol, market_date_et):
        attempts.append(symbol)
        breaker.check()
        breaker.record_success()
        return {
            "bars": _complete_reference_bars(market_date_et),
            "source": "MoomooFetcher",
            "aggregation_contract": "session_anchored_exact_2m_v2",
            "prior_daily_atr14": 5.0,
        }

    monitor = IntradayP02mMonitor(
        ["TSLA"],
        lambda _payload: True,
        clock=lambda: _NOW,
        history_loader=loader,
    )
    monitor._uses_default_history_loader = True
    monkeypatch.setattr(monitor_module, "MOOMOO_RPC_BREAKER", breaker)
    monkeypatch.setattr(monitor_module, "probe_opend_tcp", lambda *_args: True)

    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)
    assert attempts == []
    assert monitor._histories["TSLA"].unavailable_reason == (
        "history_batch_stopped:moomoo_circuit_open"
    )

    monotonic["value"] = 60.0
    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)

    assert attempts == ["TSLA"]
    assert monitor._histories["TSLA"].state == "ready"
    assert breaker.is_tripped is False


def test_history_warm_stops_remaining_symbols_after_provider_failure_and_resumes(
    monkeypatch,
):
    attempts: list[str] = []
    provider_down = {"value": False}

    def loader(symbol, market_date_et):
        attempts.append(symbol)
        if symbol == "META" and attempts.count("META") == 1:
            provider_down["value"] = True
            raise RuntimeError("OpenD transport failed")
        return {
            "bars": _complete_reference_bars(market_date_et),
            "source": "MoomooFetcher",
            "aggregation_contract": "session_anchored_exact_2m_v2",
            "prior_daily_atr14": 5.0,
        }

    monitor = IntradayP02mMonitor(
        ["TSLA", "META", "NVDA"],
        lambda _payload: True,
        clock=lambda: _NOW,
        history_loader=loader,
    )
    monkeypatch.setattr(
        monitor,
        "_history_provider_unavailable_reason",
        lambda *, probe_tcp: (
            "moomoo_circuit_open" if provider_down["value"] else None
        ),
    )

    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)

    assert attempts == ["TSLA", "META"]
    assert monitor._histories["TSLA"].state == "ready"
    assert monitor._histories["META"].unavailable_reason == (
        "history_load_failed:RuntimeError"
    )
    assert monitor._histories["NVDA"].unavailable_reason == (
        "history_batch_stopped:moomoo_circuit_open"
    )

    provider_down["value"] = False
    monitor._warm_histories(_MARKET_DATE, monitor._history_generation)

    assert attempts == ["TSLA", "META", "META", "NVDA"]
    assert all(history.state == "ready" for history in monitor._histories.values())


def test_stream_supervisor_short_reconnect_does_not_ring_a_notice_pair(monkeypatch):
    observed: list[dict[str, Any]] = []
    notices: list[str] = []
    monitor = _monitor(["TSLA"], observed, system_notifier=notices.append)
    attempts = {"count": 0}
    waits: list[float] = []
    monotonic = {"value": 0.0}
    monkeypatch.setattr(
        monitor_module.time, "monotonic", lambda: monotonic["value"]
    )

    class _BoundedEvent:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, timeout):
            waits.append(timeout)
            monotonic["value"] += timeout
            if len(waits) >= 2:
                self.stopped = True
            return self.stopped

    monitor._stop_event = _BoundedEvent()  # type: ignore[assignment]
    monkeypatch.setattr(
        monitor,
        "_stream_healthy",
        lambda: attempts["count"] >= 2,
    )
    monkeypatch.setattr(monitor, "_close_stream", lambda: None)

    def connect():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("OpenD unavailable")

    monkeypatch.setattr(monitor, "_connect_stream", connect)

    monitor._stream_supervisor()

    assert attempts["count"] == 2
    assert waits == [
        monitor_module._STREAM_RECONNECT_SECONDS,
        monitor_module._STREAM_HEALTH_SECONDS,
    ]
    assert notices == []


def test_stream_notices_require_continuous_down_and_recovery(monkeypatch):
    clock = {"value": 0.0}
    notices = []
    monitor = _monitor(
        ["TSLA"], [], system_notifier=lambda text: notices.append((clock["value"], text))
    )
    monkeypatch.setattr(monitor_module.time, "monotonic", lambda: clock["value"])
    for at, healthy in (
        (0, False), (59, False), (60, False), (70, False),
        (80, True), (100, True),
        (105, False),  # recovery interrupted: do not emit a second outage
        (110, True), (139, True), (140, True), (150, True),
    ):
        clock["value"] = at
        monitor._update_stream_notice(healthy)

    assert [at for at, _text in notices] == [60, 140]
    assert "连续不可用或无法确认至少60秒" in notices[0][1]
    assert "连续确认可用至少30秒" in notices[1][1]
    assert "不补发中断期间的旧信号" in notices[1][1]
    assert monitor._stream_down_notified is False


def test_health_reader_exception_fails_closed_and_does_not_claim_recovery(monkeypatch):
    clock = {"value": 0.0}
    notices = []
    monitor = _monitor(["TSLA"], [], system_notifier=notices.append)
    monitor._subscribed = True
    monkeypatch.setattr(monitor_module.time, "monotonic", lambda: clock["value"])

    class _BoundedEvent:
        def is_set(self):
            return clock["value"] >= 70

        def wait(self, timeout):
            clock["value"] += timeout

    def unknown_health():
        raise RuntimeError("health reader unavailable")

    def failed_connect():
        assert monitor._subscribed is False
        raise RuntimeError("cannot reconnect")

    monitor._stop_event = _BoundedEvent()
    monkeypatch.setattr(monitor, "_stream_healthy", unknown_health)
    monkeypatch.setattr(monitor, "_connect_stream", failed_connect)

    monitor._stream_supervisor()

    assert monitor._subscribed is False
    assert len(notices) == 1
    assert "连续不可用或无法确认至少60秒" in notices[0]


def test_successful_connect_does_not_immediately_claim_stable_recovery(monkeypatch):
    clock = {"value": 0.0}
    notices = []
    monitor = _monitor(["TSLA"], [], system_notifier=notices.append)
    monitor._stream_down_notified = True
    monkeypatch.setattr(monitor_module.time, "monotonic", lambda: clock["value"])

    class _OneLoopEvent:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, timeout):
            clock["value"] += timeout
            self.stopped = True

    def connect():
        monitor._subscribed = True

    monitor._stop_event = _OneLoopEvent()
    monkeypatch.setattr(monitor, "_stream_healthy", lambda: False)
    monkeypatch.setattr(monitor, "_connect_stream", connect)
    monitor._stream_supervisor()

    assert monitor._subscribed is True
    assert notices == []
    assert monitor._stream_down_notified is True
