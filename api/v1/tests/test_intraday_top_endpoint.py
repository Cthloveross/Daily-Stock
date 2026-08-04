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


def _five_minute_session(date_text: str, *, bar_count: int = 78, burst_tail: bool = False):
    """Flat 5m regular-session bars; optional 3-bar burst tail (score 9.0)."""

    rows = []
    for index in range(bar_count):
        minutes = 9 * 60 + 30 + index * 5
        rows.append(
            {
                "date": f"{date_text}T{minutes // 60:02d}:{minutes % 60:02d}:00-04:00",
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
                "volume": 1_000.0,
            }
        )
    if burst_tail and bar_count >= 3:
        # 末窗推力 103 − 100 = 3 → thrust_norm 3；量 3000/バー → vol_norm 3；
        # score = 9.0（确定性，便于合同断言）。
        for offset, (open_, close) in enumerate(((100.0, 101.0), (101.0, 102.0), (102.0, 103.0))):
            row = rows[bar_count - 3 + offset]
            row.update(open=open_, close=close, high=close + 0.5, low=open_ - 0.5, volume=3_000.0)
    return rows


def _five_minute_bars(symbol: str):
    """Sessions covering both the Tuesday-regular and Saturday-closed tests."""

    return (
        _five_minute_session("2026-07-23")
        + _five_minute_session("2026-07-24", burst_tail=True)
        + _five_minute_session("2026-07-27")
        + _five_minute_session("2026-07-28", bar_count=12, burst_tail=True)
    ), "fixture_5m"


def _stub_daily_loader(monkeypatch, *, now: datetime = _FIXED_NOW) -> _FakeManager:
    manager = _FakeManager()

    def load_history(symbol: str, *, as_of: datetime, manager) -> DailyHistoryInput:
        return DailyHistoryInput(bars=_bars(), source="fixture", fetched_at=as_of)

    monkeypatch.setattr(opportunities, "_create_data_fetcher_manager", lambda: manager)
    monkeypatch.setattr(opportunities, "_load_daily_history", load_history)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_fetch_intraday_5m_bars", _five_minute_bars)
    # 默认：财报日历按未配置处理（显式 unavailable），单测绝不打真实 Finnhub。
    monkeypatch.setattr(
        opportunities,
        "_fetch_earnings_calendar_rows",
        lambda from_date, to_date: (None, "finnhub_not_configured"),
    )
    # 默认：Playbook 只读对应关系为空（单测绝不读真实 journal_v2 数据库）。
    monkeypatch.setattr(opportunities, "_load_intraday_playbook_refs", lambda: {})
    return manager


def _stub_earnings(monkeypatch, rows_by_call: list):
    """Replace the earnings range fetch with a call-recording stub."""

    calls: list[tuple[date, date]] = []

    def fetch(from_date: date, to_date: date):
        calls.append((from_date, to_date))
        return rows_by_call, None

    monkeypatch.setattr(opportunities, "_fetch_earnings_calendar_rows", fetch)
    return calls


def _stub_events(monkeypatch, sentiments_by_symbol: dict[str, list[str]]):
    def fetch(symbol: str, *, limit: int):
        return _event_payload(symbol, sentiments_by_symbol.get(symbol, []))

    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", fetch)


def test_contract_regular_session_full_row(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    quote_batches: list[tuple[str, ...]] = []

    def quotes(symbols):
        quote_batches.append(tuple(symbols))
        return {
            "NVDA": _quote(),
            # SPY 并入同一批：vwap 499 < last 500 → above（供大盘对齐）。
            "SPY": _quote(
                "SPY",
                last_price=500.0,
                prev_close_price=490.0,
                volume=1_000,
                turnover=499_000.0,
            ),
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {"NVDA": ["BULLISH", "BULLISH", "BULLISH"]})
    _stub_earnings(
        monkeypatch, [{"symbol": "NVDA", "date": "2026-07-30", "hour": "amc"}]
    )

    response = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["us.nvda", "600519"], "limit": 5},
    )
    # SPY 只是并入既有批次，不新增快照请求次数。
    assert quote_batches == [("NVDA", "SPY")]
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "intraday-top/1.0"
    assert body["signal_version"] == "intraday_session_evidence_v6"
    # 盘中主排序 = 波段爆发分优先。
    assert body["ranking_method"] == "burst_score_first_then_evidence_count"
    # 不冻结、不入统计的显式标记。
    assert body["statistics_track"] == "none_intraday_v1_unscored"
    assert body["session_state"] == "regular"
    # v3 时段上下文：10:30 ET = 主战场（用户历史纪律提示，硬编码 v1 文案）。
    assert body["session_phase"] == "prime"
    assert "主战场" in body["session_phase_label"]
    assert body["session_phase_hint_basis"] == "user_trading_history_hardcoded_v1"
    assert body["quote_session_scope"] == "current_session"
    assert body["quote_session_label"] == "当前交易时段"
    assert body["universe"] == ["NVDA"]
    assert body["unsupported_symbols"] == ["600519"]
    assert any("不冻结" in text for text in body["limitations"])
    assert any("不推断开平仓" in text for text in body["limitations"])
    assert any("系统标注，用户过滤" in text for text in body["limitations"])

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
    # 波段爆发（v2 主信号）：末窗 9.0 分（确定性 stub），当日波段含 10:15。
    bursts = item["session_bursts"]
    assert bursts["state"] == "ready"
    assert bursts["session_date_et"] == "2026-07-28"
    assert bursts["current"]["score"] == pytest.approx(9.0, abs=1e-6)
    assert bursts["current"]["direction"] == "up"
    assert [leg["start_et"] for leg in bursts["legs"]] == ["10:15"]
    # v3 速度分级：末窗 9.0 > 前一窗（burst tail 前奏）→ 加速。
    assert bursts["speed"]["state"] == "accelerating"
    assert bursts["speed"]["current_score"] == pytest.approx(9.0, abs=1e-6)
    assert bursts["speed"]["delta"] > 0
    # v3 财报临近：07-30 距 07-28 两天 → 回避窗内（用户规则，仅标注）。
    proximity = item["earnings_proximity"]
    assert proximity["state"] == "ready"
    assert proximity["days_to_earnings"] == 2
    assert proximity["earnings_date"] == "2026-07-30"
    assert proximity["within_blackout"] is True
    # v3 大盘对齐：候选爆发 up + SPY VWAP 上方 → 顺势（标注，不参与排序）。
    assert body["market_context"]["state"] == "ready"
    assert body["market_context"]["vwap_position"] == "above"
    assert item["market_alignment"]["state"] == "aligned"
    # v4 形态相似度（styleMatch v1）：+3.2% 跳空、最低 127.5 未回补前收 124、
    # 现价 130.5 ≥ VWAP 130.5 → S2 matched；平坦 5m fixture 无 swing low →
    # S1 not_matched；现价未跌破开盘/VWAP → S3 not_matched。纯标注，不进计数。
    setup_match = item["setup_match"]
    assert setup_match["style_match_version"] == "style_match_v1"
    assert setup_match["state"] == "ready"
    assert setup_match["matched_setups"] == ["S2"]
    setup_states = {
        row["setup_key"]: row["state"] for row in setup_match["setups"]
    }
    assert setup_states == {
        "S1": "not_matched",
        "S2": "matched",
        "S3": "not_matched",
    }
    assert setup_match["bar_count_15m"] == 4
    # v6 近 30 分钟位移：复用同一批 5m K 线，ATR 标尺＝日线 ATR14（1.5）。
    # 窗口＝当日 12 根 K 线的最后 6 根：首根开盘 100.0（30 分钟前的价格）、
    # 末收 103.0、窗口最高 103.5、最低 99.5。
    displacement = item["recent_displacement"]
    assert displacement["state"] == "ready"
    assert displacement["window_minutes"] == 30
    assert displacement["bar_count"] == 12
    assert displacement["atr_basis"] == "atr14_daily"
    assert displacement["survival_line_atr"] == 0.5
    assert displacement["net_move_atr"] == pytest.approx(3.0 / 1.5, abs=1e-4)
    assert displacement["high_excursion_atr"] == pytest.approx(3.5 / 1.5, abs=1e-4)
    assert displacement["low_excursion_atr"] == pytest.approx(-0.5 / 1.5, abs=1e-4)
    assert displacement["abs_range_atr"] == pytest.approx(4.0 / 1.5, abs=1e-4)
    assert displacement["unavailable_reason"] is None
    # 位移是 additive 标注：不进 supports 计数，也不出现在 evidence 列表里。
    assert all(
        entry["metric"] != "recent_displacement" for entry in item["evidence"]
    )
    assert any("0.5 ATR 是你自己 766 笔回合" in text for text in body["limitations"])
    assert any(
        "不是预测、不是买卖信号" in text for text in item["limitations"]
    )
    # Playbook 对应关系默认 stub 为空：徽标如实缺 Playbook 标注。
    assert all(row["playbook"] is None for row in setup_match["setups"])
    assert any("形态相似 ≠ 可交易" in text for text in body["limitations"])
    burst_evidence = next(
        entry
        for entry in item["evidence"]
        if entry["metric"] == "session_momentum_burst"
    )
    assert burst_evidence["status"] == "supports"
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
    # v3 时段上下文：周六休市如实标「休市 · 复盘时段」，不冒充任何盘中时段。
    assert body["session_phase"] == "closed"
    assert "休市" in body["session_phase_label"]
    assert body["quote_session_scope"] == "latest_prior_session"
    assert body["quote_session_label"] == "最近一个交易时段"
    # 休市：排序退回 v1 证据计数。
    assert body["ranking_method"] == "burst_score_first_then_evidence_count"
    item = body["candidates"][0]
    # 休市：缺口分母切换为快照自带前收并显式标注，不用日线前收伪装。
    assert item["gap_basis"] == "session_open_vs_moomoo_snapshot_prev_close"
    assert item["gap_percent"] == pytest.approx(3.225806, abs=1e-4)
    gap_evidence = next(
        entry for entry in item["evidence"] if entry["metric"] == "session_gap_percent"
    )
    assert gap_evidence["observation_window"] == "latest_completed_trading_session"
    # 仍附最近一个交易时段（2026-07-24 周五）的波段：晚间复盘可见走了几波。
    bursts = item["session_bursts"]
    assert bursts["state"] == "ready"
    assert bursts["session_date_et"] == "2026-07-24"
    assert [leg["start_et"] for leg in bursts["legs"]] == ["15:45"]
    # v3 速度分级在休市同样描述最近一个交易时段的末两个窗口。
    assert bursts["speed"]["state"] == "accelerating"
    # 财报日历默认未配置：财报列显式标缺，within_blackout 保持 None（未知）。
    proximity = item["earnings_proximity"]
    assert proximity["state"] == "unavailable"
    assert proximity["unavailable_reason"] == "finnhub_not_configured"
    assert proximity["within_blackout"] is None
    # v4 形态相似度在休市同样按最近一个交易时段评估并如实标注 as-of。
    setup_match = item["setup_match"]
    assert setup_match["quote_session_scope"] == "latest_prior_session"
    assert setup_match["session_date_et"] == "2026-07-24"


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


def test_burst_fetch_failure_is_isolated_and_never_blocks_aggregates(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": ["BULLISH", "BULLISH", "BULLISH"]})

    def broken(symbol: str):
        raise RuntimeError("5m lane down")

    monkeypatch.setattr(opportunities, "_fetch_intraday_5m_bars", broken)

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]}
    )
    assert response.status_code == 200
    item = response.json()["candidates"][0]
    bursts = item["session_bursts"]
    assert bursts["state"] == "unavailable"
    assert bursts["unavailable_reason"] == "history_5m_unavailable:RuntimeError"
    assert bursts["current"] is None and bursts["legs"] == []
    burst_evidence = next(
        entry
        for entry in item["evidence"]
        if entry["metric"] == "session_momentum_burst"
    )
    assert burst_evidence["status"] == "unknown"
    # 聚合证据不受影响：缺口/量能/波幅照常，行仍是盘中活跃。
    assert item["gap_percent"] == pytest.approx(3.225806, abs=1e-4)
    assert item["volume_pace_ratio"] == pytest.approx(2.5, abs=1e-6)
    assert item["research_state"] == "active"
    # v4 形态相似度：5m 缺失只让 S1 显式 unavailable，
    # 纯快照几何的 S2 仍照常评估（缺口托举 matched）。
    setup_states = {
        row["setup_key"]: row["state"] for row in item["setup_match"]["setups"]
    }
    assert setup_states["S1"] == "unavailable"
    assert setup_states["S2"] == "matched"


def test_playbook_refs_attach_to_setup_badges_read_only(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": []})
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_playbook_refs",
        lambda: {
            "S2": {
                "setup_key": "S2",
                "candidate_key": "b" * 64,
                "status": "candidate",
                "title": "S2 · 跳空高开托举（轻仓试错，需二次确认）",
            }
        },
    )

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]}
    )
    assert response.status_code == 200
    setups = {
        row["setup_key"]: row
        for row in response.json()["candidates"][0]["setup_match"]["setups"]
    }
    # S2 matched 且带只读 Playbook 对应（候选）；其余 setup 无 Playbook 标注。
    assert setups["S2"]["state"] == "matched"
    assert setups["S2"]["playbook"]["status"] == "candidate"
    assert setups["S2"]["playbook"]["candidate_key"] == "b" * 64
    assert setups["S1"]["playbook"] is None
    assert setups["S3"]["playbook"] is None


def test_burst_bars_cached_per_symbol_across_refresh(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": []})
    fetch_calls: list[str] = []

    def counted(symbol: str):
        fetch_calls.append(symbol)
        return _five_minute_bars(symbol)

    monkeypatch.setattr(opportunities, "_fetch_intraday_5m_bars", counted)
    client = _client()
    payload = {"symbols": ["NVDA"]}
    first = client.post("/api/v1/opportunities/intraday-top", json=payload)
    refreshed = client.post(
        "/api/v1/opportunities/intraday-top", json={**payload, "refresh": True}
    )
    assert first.status_code == refreshed.status_code == 200
    # refresh 只绕过响应级 TTL；逐标的 5m K 线 60 秒缓存仍命中（一次读取）。
    assert fetch_calls == ["NVDA"]


def test_earnings_calendar_single_range_call_cached_for_the_day(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote(), "TSLA": _quote("TSLA")},
    )
    _stub_events(monkeypatch, {})
    calls = _stub_earnings(
        monkeypatch, [{"symbol": "TSLA", "date": "2026-07-29"}]
    )
    client = _client()

    first = client.post(
        "/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA", "TSLA"]}
    )
    refreshed = client.post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["NVDA", "TSLA"], "refresh": True},
    )
    other_universe = client.post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": ["NVDA"], "refresh": True},
    )
    assert first.status_code == refreshed.status_code == other_universe.status_code == 200
    # 一天一次区间调用覆盖全部 universe：refresh 与不同标的集合都命中缓存。
    assert len(calls) == 1
    assert calls[0] == (date(2026, 7, 28), date(2026, 8, 2))
    by_ticker = {
        item["ticker"]: item for item in first.json()["candidates"]
    }
    assert by_ticker["TSLA"]["earnings_proximity"]["days_to_earnings"] == 1
    assert by_ticker["TSLA"]["earnings_proximity"]["within_blackout"] is True
    # 日历成功 + 窗口内无该标的财报＝诚实的 False，而不是标缺。
    assert by_ticker["NVDA"]["earnings_proximity"]["state"] == "ready"
    assert by_ticker["NVDA"]["earnings_proximity"]["within_blackout"] is False


def test_earnings_calendar_failure_is_marked_never_faked_safe(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {})

    def broken(from_date, to_date):
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(opportunities, "_fetch_earnings_calendar_rows", broken)
    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]}
    )
    assert response.status_code == 200
    item = response.json()["candidates"][0]
    proximity = item["earnings_proximity"]
    assert proximity["state"] == "unavailable"
    assert proximity["unavailable_reason"] == "finnhub_error:RuntimeError"
    assert proximity["within_blackout"] is None
    # 财报失败不影响行情行与其余证据。
    assert item["last_price"] == 130.5


def test_empty_symbols_falls_back_to_server_stock_list(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)
    # 固定「清单未配置」：本测试锁定的是 STOCK_LIST 回退路径本身，
    # 不能被运行机器 .env 里的 INTRADAY_WATCHLIST 干扰。
    monkeypatch.setattr(opportunities, "_configured_intraday_watchlist", lambda: [])
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["NVDA", "AAPL"])

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    assert response.json()["universe"] == ["NVDA", "AAPL"]


def test_empty_symbols_without_stock_list_is_422(monkeypatch):
    monkeypatch.setattr(opportunities, "_configured_intraday_watchlist", lambda: [])
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
    # v3 时段上下文与 Moomoo 无关（纯 ET 时钟）：未配置时同样如实标注。
    assert body["session_phase"] == "prime"
    assert "主战场" in body["session_phase_label"]
    assert body["session_phase_hint_basis"] == "user_trading_history_hardcoded_v1"
    assert [item["ticker"] for item in body["items"]] == ["SPY", "QQQ", "VIX"]
    assert all(item["state"] == "not_configured" for item in body["items"])
    assert all(item["last_price"] is None for item in body["items"])
    assert all(item["vwap_position"] == "unknown" for item in body["items"])

    opportunities._reset_scan_cache_for_tests()
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")

    def quotes(symbols):
        # VIX 不可得：隔离请求返回空，不影响 SPY/QQQ。
        return {
            symbol: _quote(
                symbol,
                last_price=500.0,
                prev_close_price=490.0,
                volume=1_000,
                turnover=499_000.0,  # vwap 499 < last 500 → above
            )
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
    # v3：SPY 会话 VWAP 位置（累计额/量近似）供大盘情绪参照。
    assert items["SPY"]["vwap"] == pytest.approx(499.0, abs=1e-6)
    assert items["SPY"]["vwap_position"] == "above"
    assert items["VIX"]["state"] == "unavailable"
    assert items["VIX"]["last_price"] is None
    assert items["VIX"]["vwap_position"] == "unknown"


def test_intraday_pulse_session_phase_noise_window(monkeypatch):
    # 2026-07-28 17:30 UTC = 13:30 ET → 噪音时段（用户历史净亏损，默认观望）。
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    monkeypatch.setattr(
        opportunities,
        "_intraday_now",
        lambda: datetime(2026, 7, 28, 17, 30, tzinfo=timezone.utc),
    )
    response = _client().get("/api/v1/opportunities/intraday-pulse")
    assert response.status_code == 200
    body = response.json()
    assert body["session_phase"] == "noise"
    assert "默认观望" in body["session_phase_label"]
    assert any("系统标注，用户过滤" in text for text in body["limitations"])
