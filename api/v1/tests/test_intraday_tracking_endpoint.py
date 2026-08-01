# -*- coding: utf-8 -*-
"""Contract tests for POST /api/v1/opportunities/intraday-tracking.

The endpoint tracks the live session against the frozen premarket plan.  It
must stay read-only research: no re-ranking, no buy/sell signal, and every
missing input must fail closed with an explicit reason instead of a fake 0.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.v1.endpoints import opportunities
from src.opportunities.engine import DailyHistoryInput

# 2026-07-28 is a Tuesday; 14:30 UTC = 10:30 ET (EDT) → regular session.
_FIXED_NOW = datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc)


class _FakeManager:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


@pytest.fixture(autouse=True)
def _clear_opportunity_scan_cache():
    opportunities._reset_scan_cache_for_tests()
    yield
    opportunities._reset_scan_cache_for_tests()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(opportunities.router, prefix="/api/v1/opportunities")
    return TestClient(app)


def _bars() -> list[dict]:
    """25 completed weekday bars ending 2026-07-21, flat 1_000 volume.

    Every bar: high = close + 0.5, low = close − 0.5, close climbs by 1, so
    TR = max(1.0, |c+0.5 − prev_c|, |c−0.5 − prev_c|) = 1.5 for every step and
    Wilder ATR14 = 1.5 exactly.  20-session median full-day volume = 1_000.
    """

    rows = []
    current = date(2026, 7, 21)
    dates = []
    while len(dates) < 25:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    for index, observed_date in enumerate(reversed(dates)):
        close = 100.0 + index
        rows.append(
            {
                "date": observed_date.isoformat(),
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1_000,
                "amount": close * 1_000,
            }
        )
    return rows


def _quote(symbol: str = "NVDA", **overrides) -> SimpleNamespace:
    base = dict(
        symbol=symbol,
        fetched_at=datetime(2026, 7, 28, 14, 30, 5, tzinfo=timezone.utc),
        last_price=130.5,
        open_price=128.0,
        high_price=131.0,
        low_price=127.5,
        prev_close_price=124.0,
        volume=2_500,
        turnover=326_250.0,
        update_time="2026-07-28 10:30:04",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _stub_daily_loader(monkeypatch) -> _FakeManager:
    manager = _FakeManager()

    def load_history(symbol: str, *, as_of: datetime, manager) -> DailyHistoryInput:
        return DailyHistoryInput(bars=_bars(), source="fixture", fetched_at=as_of)

    monkeypatch.setattr(opportunities, "_create_data_fetcher_manager", lambda: manager)
    monkeypatch.setattr(opportunities, "_load_daily_history", load_history)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: _FIXED_NOW)
    return manager


def test_disabled_is_explicitly_not_configured_and_never_queries_moomoo(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)

    def forbidden(symbols):
        raise AssertionError("must not query Moomoo when disabled")

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", forbidden)

    response = _client().post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": ["NVDA"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "intraday-tracking/1.0"
    assert body["session_state"] == "regular"
    assert body["session_state_basis"] == "america_new_york_clock_v1"
    assert body["tracking_basis"] == "frozen_premarket_plan_readonly"
    assert body["market_date_et"] == "2026-07-28"

    item = body["items"][0]
    assert item["state"] == "not_configured"
    # Live fields must be explicit nulls, never fake zeros.
    for field in (
        "last_price",
        "session_high",
        "session_low",
        "session_volume",
        "session_turnover",
        "vwap",
        "volume_pace_ratio",
        "quote_as_of",
    ):
        assert item[field] is None
    assert item["vwap_unavailable_reason"] == "moomoo_not_configured"
    assert item["volume_pace_unavailable_reason"] == "moomoo_not_configured"
    # Daily-history-derived context stays available and honest.
    assert item["atr14"] == pytest.approx(1.5, abs=1e-6)
    assert item["atr14_method"] == "wilder_smoothing_14_daily_completed_bars"
    assert item["atr14_source"] == "fixture"


def test_ready_item_returns_vwap_pace_atr_and_as_of(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )

    response = _client().post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": ["us.nvda"]},
    )
    assert response.status_code == 200
    body = response.json()
    item = body["items"][0]
    assert item["ticker"] == "NVDA"
    assert item["state"] == "ready"
    assert item["last_price"] == 130.5
    assert item["session_high"] == 131.0
    assert item["session_low"] == 127.5
    assert item["session_volume"] == 2_500
    assert item["session_turnover"] == 326_250.0
    assert item["quote_as_of"] == "2026-07-28 10:30:04"
    # VWAP approximation = turnover / volume, with its basis label.
    assert item["vwap"] == pytest.approx(130.5, abs=1e-6)
    assert item["vwap_basis"] == "session_turnover_over_volume"
    assert item["vwap_unavailable_reason"] is None
    # Pace vs full-day median: 2_500 / 1_000 = 2.5, labelled as full-day basis.
    assert item["volume_pace_ratio"] == pytest.approx(2.5, abs=1e-6)
    assert (
        item["volume_pace_basis"]
        == "session_cumulative_vs_prior_20_session_full_day_median"
    )
    assert item["prior_20d_median_volume"] == 1_000.0
    assert item["atr14"] == pytest.approx(1.5, abs=1e-6)
    assert item["atr14_last_bar_date"] == "2026-07-21"
    assert body["session_state"] == "regular"
    assert any("不生成买卖信号" in text for text in item["limitations"])


def test_provider_missing_symbol_degrades_to_unavailable_without_zeros(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {},
    )

    response = _client().post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": ["NVDA"]},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["state"] == "unavailable"
    assert item["last_price"] is None
    assert item["session_volume"] is None
    assert item["vwap"] is None
    assert item["vwap_unavailable_reason"] == "quote_unavailable"
    assert item["volume_pace_unavailable_reason"] == "quote_unavailable"


def test_missing_volume_marks_vwap_and_pace_with_reasons(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote(volume=None)},
    )

    response = _client().post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": ["NVDA"]},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["state"] == "partial"
    assert item["last_price"] == 130.5
    assert item["vwap"] is None
    assert item["vwap_unavailable_reason"] == "missing_or_nonpositive_volume"
    assert item["volume_pace_ratio"] is None
    assert item["volume_pace_unavailable_reason"] == "missing_session_volume"


@pytest.mark.parametrize(
    ("now_utc", "expected_state"),
    [
        # 12:00 UTC Tuesday = 08:00 ET → premarket.
        (datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc), "premarket"),
        # 21:30 UTC Tuesday = 17:30 ET → afterhours.
        (datetime(2026, 7, 28, 21, 30, tzinfo=timezone.utc), "afterhours"),
        # Saturday mid-morning ET → closed.
        (datetime(2026, 7, 25, 14, 30, tzinfo=timezone.utc), "closed"),
    ],
)
def test_session_state_follows_new_york_clock(monkeypatch, now_utc, expected_state):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now_utc)

    response = _client().post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": ["NVDA"]},
    )
    assert response.status_code == 200
    assert response.json()["session_state"] == expected_state


def test_identical_requests_reuse_ttl_cache_and_refresh_bypasses(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    manager = _stub_daily_loader(monkeypatch)
    quote_calls: list[tuple[str, ...]] = []

    def counted_quotes(symbols):
        quote_calls.append(tuple(symbols))
        return {"NVDA": _quote()}

    monkeypatch.setattr(
        opportunities, "_fetch_underlying_session_quotes", counted_quotes
    )
    client = _client()
    payload = {"symbols": ["NVDA"]}

    first = client.post("/api/v1/opportunities/intraday-tracking", json=payload)
    second = client.post("/api/v1/opportunities/intraday-tracking", json=payload)
    refreshed = client.post(
        "/api/v1/opportunities/intraday-tracking",
        json={**payload, "refresh": True},
    )

    assert first.status_code == second.status_code == refreshed.status_code == 200
    # TTL cache absorbed the duplicate; explicit refresh recomputed once more.
    assert len(quote_calls) == 2
    # The daily-history memo kept the refresh from re-running the daily
    # providers: one manager lifecycle for all three requests.
    assert manager.close_count == 1
    assert second.json()["generated_at"] == first.json()["generated_at"]


def test_single_flight_shares_one_in_progress_tracking_scan(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(opportunities, "_SCAN_CACHE_TTL_SECONDS", 0.0)
    started = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    quote_calls = 0

    def slow_quotes(symbols):
        nonlocal quote_calls
        with counter_lock:
            quote_calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"NVDA": _quote()}

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", slow_quotes)
    client = _client()

    def caller():
        return client.post(
            "/api/v1/opportunities/intraday-tracking",
            json={"symbols": ["NVDA"]},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(caller) for _ in range(2)]
        assert started.wait(timeout=2)
        time.sleep(0.05)
        release.set()
        responses = [future.result(timeout=5) for future in futures]

    assert all(response.status_code == 200 for response in responses)
    assert quote_calls == 1
    assert responses[0].json() == responses[1].json()


def test_request_bounds_and_symbol_normalization():
    client = _client()
    too_many = client.post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META"]},
    )
    assert too_many.status_code == 422
    non_us = client.post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": ["600519"]},
    )
    assert non_us.status_code == 422
    empty = client.post(
        "/api/v1/opportunities/intraday-tracking",
        json={"symbols": []},
    )
    assert empty.status_code == 422
