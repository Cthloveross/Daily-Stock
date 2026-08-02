# -*- coding: utf-8 -*-
"""Contract tests for POST /api/v1/opportunities/intraday-top and the pulse.

The intraday board is rolling research: it never freezes a version, never
writes snapshots/qualification/outcomes, and every option-event figure is a
provider classification count — never an inferred direction.  These tests pin
the response contract, fail-closed behaviour, closed-session labelling and
the 60s cache / single-flight machinery.
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
# 2026-07-25 is a Saturday → closed session.
_SATURDAY_NOW = datetime(2026, 7, 25, 14, 30, tzinfo=timezone.utc)


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
    """25 completed weekday bars ending 2026-07-27; ATR14 = 1.5, median = 1000."""

    rows = []
    current = date(2026, 7, 27)
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


def _event_payload(ticker: str, sentiments: list[str]) -> SimpleNamespace:
    events = [
        {
            "event_id": f"evt_{ticker}_{index}",
            "option_code": f"US.{ticker}260918C100000",
            "owner_code": f"US.{ticker}",
            "symbol": ticker,
            "fill_time": f"2026-07-28 10:1{index}:00",
            "ticker_type": "BUY",
            "price": 5.0,
            "volume": 100,
            "turnover": 50_000.0 + index,
            "option_type": "CALL",
            "strike_price": 100.0,
            "expiry": "2026-09-18",
            "dte": 52,
            "underlying_price": 130.0,
            "bid_price": 4.9,
            "ask_price": 5.1,
            "iv_percent": 45.0,
            "total_volume": 1_000,
            "total_open_interest": 5_000,
            "vo_ratio_percent": 20.0,
            "delta": 0.5,
            "sentiment": sentiments[index],
            "order_types": ("SWEEP",),
            "strategy_type": "SINGLE_LEG",
        }
        for index in range(len(sentiments))
    ]
    return SimpleNamespace(
        symbol=ticker,
        fetched_at=datetime(2026, 7, 28, 14, 30, 6, tzinfo=timezone.utc),
        event_as_of=events[-1]["fill_time"] if events else None,
        all_count=len(events),
        events=tuple(SimpleNamespace(**event) for event in events),
    )


def _stub_daily_loader(monkeypatch, *, now: datetime = _FIXED_NOW) -> _FakeManager:
    manager = _FakeManager()

    def load_history(symbol: str, *, as_of: datetime, manager) -> DailyHistoryInput:
        return DailyHistoryInput(bars=_bars(), source="fixture", fetched_at=as_of)

    monkeypatch.setattr(opportunities, "_create_data_fetcher_manager", lambda: manager)
    monkeypatch.setattr(opportunities, "_load_daily_history", load_history)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    return manager


def _stub_events(monkeypatch, sentiments_by_symbol: dict[str, list[str]]):
    def fetch(symbol: str, *, limit: int):
        return _event_payload(symbol, sentiments_by_symbol.get(symbol, []))

    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", fetch)


def test_contract_regular_session_full_row(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": ["BULLISH", "BULLISH", "BULLISH"]})

    response = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["us.nvda", "600519"], "limit": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "intraday-top/1.0"
    assert body["signal_version"] == "intraday_session_evidence_v1"
    assert body["ranking_method"] == "rule_based_evidence_count"
    # 不冻结、不入统计的显式标记。
    assert body["statistics_track"] == "none_intraday_v1_unscored"
    assert body["session_state"] == "regular"
    assert body["quote_session_scope"] == "current_session"
    assert body["quote_session_label"] == "当前交易时段"
    assert body["universe"] == ["NVDA"]
    assert body["unsupported_symbols"] == ["600519"]
    assert any("不冻结" in text for text in body["limitations"])
    assert any("不推断开平仓" in text for text in body["limitations"])

    item = body["candidates"][0]
    assert item["ticker"] == "NVDA"
    assert item["last_price"] == 130.5
    assert item["quote_as_of"] == "2026-07-28 10:30:04"
    # 缺口：128 vs 日线加载器前收 124 → +3.2258%，ATR 1.5 → 2.67×。
    assert item["gap_basis"] == "session_open_vs_prior_completed_close_daily_loader"
    assert item["gap_percent"] == pytest.approx(3.225806, abs=1e-4)
    assert item["gap_atr_multiple"] == pytest.approx(4.0 / 1.5, abs=1e-4)
    # 量能节奏 2.5×（全日中位口径）。
    assert item["volume_pace_ratio"] == pytest.approx(2.5, abs=1e-6)
    assert (
        item["volume_pace_basis"]
        == "session_cumulative_vs_prior_20_session_full_day_median"
    )
    # 波幅扩张 (131 − 127.5)/1.5 = 2.33×。
    assert item["atr_range_expansion"] == pytest.approx(3.5 / 1.5, abs=1e-4)
    # 快照前收口径的当日涨跌。
    assert item["session_change_percent"] == pytest.approx(5.241935, abs=1e-4)
    assert item["session_change_basis"] == "moomoo_snapshot_prev_close"
    # 期权异动聚合：3 条、偏多、最大单，均为供应商分类计数。
    activity = item["option_activity"]
    assert activity["count"] == 3
    assert activity["dominant_sentiment"] == "bullish"
    assert activity["max_single_turnover"] == 50_002.0
    assert activity["event_as_of"] == "2026-07-28 10:12:00"
    # 证据合同：每条证据带来源 / as-of / 口径 / limitations。
    for evidence in item["evidence"]:
        assert evidence["source"]
        assert evidence["observation_window"]
    assert item["research_state"] == "active"
    # 上一日结构仅背景。
    assert item["prior_day_context"]["prior_close"] == 124.0
    # 异动 feed：跨标的、最新在前、透传供应商分类。
    assert body["recent_option_events"][0]["fill_time"] == "2026-07-28 10:12:00"
    assert body["recent_option_events"][0]["sentiment"] == "BULLISH"


def test_disabled_is_fail_closed_and_never_queries_moomoo(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("must not query Moomoo when disabled")

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", forbidden)
    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", forbidden)

    response = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["NVDA"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["moomoo_enabled"] is False
    item = body["candidates"][0]
    assert item["research_state"] == "insufficient"
    assert item["last_price"] is None
    assert item["option_activity"]["state"] == "not_configured"
    assert item["option_activity"]["dominant_sentiment"] == "unknown"
    # 日线派生背景仍诚实可用。
    assert item["atr14"] == pytest.approx(1.5, abs=1e-6)
    assert item["prior_day_context"]["prior_close"] == 124.0


def test_missing_quote_symbol_is_insufficient_not_zero(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities, "_fetch_underlying_session_quotes", lambda symbols: {}
    )
    _stub_events(monkeypatch, {})

    response = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["NVDA"]},
    )
    assert response.status_code == 200
    item = response.json()["candidates"][0]
    assert item["research_state"] == "insufficient"
    assert item["last_price"] is None
    assert item["gap_unavailable_reason"] == "quote_unavailable"
    assert item["vwap_position"] == "unknown"


def test_closed_session_labels_last_session_and_snapshot_gap_basis(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch, now=_SATURDAY_NOW)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": []})

    response = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["NVDA"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_state"] == "closed"
    assert body["quote_session_scope"] == "latest_prior_session"
    assert body["quote_session_label"] == "最近一个交易时段"
    item = body["candidates"][0]
    # 休市：缺口分母切换为快照自带前收并显式标注，不用日线前收伪装。
    assert item["gap_basis"] == "session_open_vs_moomoo_snapshot_prev_close"
    assert item["gap_percent"] == pytest.approx(3.225806, abs=1e-4)
    gap_evidence = next(
        entry for entry in item["evidence"] if entry["metric"] == "session_gap_percent"
    )
    assert gap_evidence["observation_window"] == "latest_completed_trading_session"


def test_ttl_cache_shares_and_refresh_bypasses(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    quote_calls: list[tuple[str, ...]] = []

    def counted_quotes(symbols):
        quote_calls.append(tuple(symbols))
        return {"NVDA": _quote()}

    monkeypatch.setattr(
        opportunities, "_fetch_underlying_session_quotes", counted_quotes
    )
    _stub_events(monkeypatch, {"NVDA": []})
    client = _client()
    payload = {"symbols": ["NVDA"]}

    first = client.post("/api/v1/opportunities/intraday-top", json=payload)
    second = client.post("/api/v1/opportunities/intraday-top", json=payload)
    refreshed = client.post(
        "/api/v1/opportunities/intraday-top", json={**payload, "refresh": True}
    )
    assert first.status_code == second.status_code == refreshed.status_code == 200
    assert len(quote_calls) == 2
    assert second.json()["generated_at"] == first.json()["generated_at"]


def test_cached_entry_uses_60s_ttl(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities, "_fetch_underlying_session_quotes", lambda symbols: {}
    )
    _stub_events(monkeypatch, {})
    client = _client()
    response = client.post(
        "/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]}
    )
    assert response.status_code == 200
    with opportunities._scan_cache_lock:
        entry = next(
            entry
            for key, entry in opportunities._scan_cache.items()
            if key[0] == "intraday_top"
        )
    remaining = entry.expires_at - opportunities._cache_now()
    assert remaining > opportunities._SCAN_CACHE_TTL_SECONDS
    assert remaining <= opportunities._INTRADAY_TOP_CACHE_TTL_SECONDS + 1.0


def test_single_flight_shares_one_in_progress_scan(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
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
    _stub_events(monkeypatch, {"NVDA": []})
    client = _client()

    def caller():
        return client.post(
            "/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]}
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


def test_event_failure_degrades_one_symbol_only(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {symbol: _quote(symbol) for symbol in symbols},
    )

    def flaky(symbol: str, *, limit: int):
        if symbol == "TSLA":
            raise RuntimeError("event lane down")
        return _event_payload(symbol, ["BULLISH", "BULLISH", "BULLISH"])

    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", flaky)

    response = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["NVDA", "TSLA"]},
    )
    assert response.status_code == 200
    by_ticker = {
        item["ticker"]: item for item in response.json()["candidates"]
    }
    assert by_ticker["NVDA"]["option_activity"]["state"] == "ready"
    assert by_ticker["TSLA"]["option_activity"]["state"] == "unavailable"
    assert by_ticker["TSLA"]["option_activity"]["dominant_sentiment"] == "unknown"
    # 行情行仍然可用：单一异动失败不拖垮该标的的盘中行。
    assert by_ticker["TSLA"]["last_price"] == 130.5


def test_empty_symbols_falls_back_to_server_stock_list(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["NVDA", "AAPL"])

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    assert response.json()["universe"] == ["NVDA", "AAPL"]


def test_empty_symbols_without_stock_list_is_422(monkeypatch):
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: [])
    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "empty_universe"


def test_request_bounds():
    client = _client()
    too_many = client.post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": [f"SY{index}" for index in range(21)]},
    )
    assert too_many.status_code == 422
    bad_limit = client.post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["NVDA"], "limit": 11},
    )
    assert bad_limit.status_code == 422


def test_intraday_pulse_disabled_and_degraded_vix(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: _FIXED_NOW)
    disabled = _client().get("/api/v1/opportunities/intraday-pulse")
    assert disabled.status_code == 200
    body = disabled.json()
    assert body["schema_version"] == "intraday-pulse/1.0"
    assert [item["ticker"] for item in body["items"]] == ["SPY", "QQQ", "VIX"]
    assert all(item["state"] == "not_configured" for item in body["items"])
    assert all(item["last_price"] is None for item in body["items"])

    opportunities._reset_scan_cache_for_tests()
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")

    def quotes(symbols):
        # VIX 不可得：隔离请求返回空，不影响 SPY/QQQ。
        return {
            symbol: _quote(symbol, last_price=500.0, prev_close_price=490.0)
            for symbol in symbols
            if symbol in {"SPY", "QQQ"}
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    enabled = _client().get("/api/v1/opportunities/intraday-pulse")
    assert enabled.status_code == 200
    items = {item["ticker"]: item for item in enabled.json()["items"]}
    assert items["SPY"]["state"] == "ready"
    assert items["SPY"]["change_percent"] == pytest.approx(2.040816, abs=1e-4)
    assert items["SPY"]["change_basis"] == "moomoo_snapshot_prev_close"
    assert items["VIX"]["state"] == "unavailable"
    assert items["VIX"]["last_price"] is None
