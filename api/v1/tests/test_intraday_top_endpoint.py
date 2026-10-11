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
    assert body["signal_version"] == "intraday_session_evidence_v9"
    # 盘中主排序 = 波段爆发分优先。
    assert body["ranking_method"] == "rule_based_evidence_count"
    # 不冻结、不入统计的显式标记。
    assert body["statistics_track"] == "none_intraday_v1_unscored"
    assert body["session_state"] == "regular"
    # 10:30 ET 只映射为中性的上午流动性/波动环境。
    assert body["session_phase"] == "prime"
    assert body["session_phase_label"] == "10:00–11:00 · 上午流动性与波动环境"
    assert body["session_phase_hint_basis"] == "america_new_york_liquidity_context_neutral_v2"
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
    # v7 ATR 标尺可比性：走日线 ATR14 的行显式标「可比」，且回退计数缺席。
    assert displacement["atr_scale_comparability"] == "daily_atr14"
    assert displacement["atr_prior_session_count"] is None
    # v7 哑火形态：末窗效率 |103 − 100| ÷ (103.5 − 99.5) = 0.75 < 0.9、
    # 量比 3.0 ≥ 2.0 → 两个条件都不满足，如实给出**两个**未满足原因。
    fizzle = item["fizzle_flag"]
    assert fizzle["state"] == "not_flagged"
    assert fizzle["stratum"] == "intraday"
    assert fizzle["efficiency"] == pytest.approx(0.75, abs=1e-6)
    assert fizzle["vol_norm"] == pytest.approx(3.0, abs=1e-6)
    assert fizzle["reason"] == "efficiency_below_0.9+vol_norm_at_or_above_2.0"
    # 冻结的研究参考数字随行下发（供 UI 原样展示，不在前端硬编码）。
    assert fizzle["reference"]["n_in"] == 291
    assert fizzle["reference"]["n_out"] == 177
    assert fizzle["reference"]["in_sample_rate"] == pytest.approx(0.268)
    assert fizzle["reference"]["out_of_sample_rate"] == pytest.approx(0.254)
    assert fizzle["reference"]["base_rate_in"] == pytest.approx(0.478)
    assert fizzle["reference"]["base_rate_out"] == pytest.approx(0.505)
    # burst profile 内同一读数（单一真源），候选行只是把它提到顶层。
    assert bursts["fizzle_flag"] == fizzle
    # additive-only：v6 的既有字段一个不少、语义不变。
    for legacy_key in (
        "session_bursts",
        "recent_displacement",
        "setup_match",
        "market_alignment",
        "earnings_proximity",
        "option_activity",
        "evidence",
    ):
        assert legacy_key in item
    # 哑火形态是标注：不进 supports 计数，也不出现在 evidence 列表里。
    assert all(entry["metric"] != "fizzle_flag" for entry in item["evidence"])
    # 否定性结论与「不做什么」写在 limitations 里，用户能直接读到。
    assert any("起速那一刻分不出方向" in text for text in body["limitations"])
    assert any("不是卖出信号" in text for text in body["limitations"])
    assert any("起速幅度分档" in text for text in body["limitations"])
    assert any(
        "3.74 个窗口" in text for text in item["session_bursts"]["limitations"]
    )
    # 位移是 additive 标注：不进 supports 计数，也不出现在 evidence 列表里。
    assert all(
        entry["metric"] != "recent_displacement" for entry in item["evidence"]
    )
    assert any("0.5 ATR 仍按你自己 766 笔回合" in text for text in body["limitations"])
    # v8：位移 limitation 必须携带循环性更正，且不得再有存活框架的读法。
    displacement_line = next(
        text for text in body["limitations"] if "近 30 分钟位移" in text
    )
    assert "【v8 更正】" in displacement_line
    assert "按构造就是循环的" in displacement_line
    assert "37.6 个百分点" in displacement_line and "0.1 个百分点" in displacement_line
    assert "不含任何前向信息" in displacement_line
    assert "不要把 0.5 读成「越过就能活下来」" in displacement_line
    assert "经验「活下来」线" not in displacement_line
    # v8：K 线时间戳口径修正与收盘竞价口径性质如实声明在波段证据的 limitations 里。
    burst_limitations = next(
        entry["limitations"]
        for entry in item["evidence"]
        if entry["metric"] == "session_momentum_burst"
    )
    assert any("K 线时间戳口径修正（v8）" in text for text in burst_limitations)
    assert any("25/25 个标的-交易日逐分钱吻合" in text for text in burst_limitations)
    assert any("收盘集合竞价的口径性质（v8）" in text for text in burst_limitations)
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
    assert burst_evidence["status"] == "neutral"
    assert burst_evidence["actionability"] == "legacy_context"
    assert item["session_bursts"]["legacy"] is True
    assert item["session_bursts"]["legacy_usage"] == (
        "api_compatibility_and_research_display_only"
    )
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
    assert body["ranking_method"] == "rule_based_evidence_count"
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


def test_execution_policy_updates_even_while_research_payload_is_cached(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)
    now = {"value": datetime.fromisoformat("2026-07-28T14:29:30-04:00")}
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now["value"])
    client = _client()
    first = client.post("/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]})
    assert first.status_code == 200
    first_policy = first.json()["execution_policy"]
    assert first_policy["zero_dte_entry_allowed"] is True
    assert first_policy["expires_at"] == "2026-07-28T14:30:00-04:00"
    now["value"] += timedelta(seconds=31)
    second = client.post("/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]})
    assert second.status_code == 200
    assert second.json()["generated_at"] == first.json()["generated_at"]
    assert second.json()["execution_policy"]["zero_dte_entry_allowed"] is False


def test_execution_policy_half_day_api_does_not_reuse_normal_close(monkeypatch):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch, now=datetime.fromisoformat("2026-11-27T12:50:00-05:00"))
    response = _client().post("/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]})
    assert response.status_code == 200
    policy = response.json()["execution_policy"]
    assert policy["ordinary_entry_allowed"] is False
    assert policy["close_et"] == "13:00"
    assert policy["exit_deadline_et"] == "12:45"


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
    # v7：没有窗口就没有形态可谈——显式标缺，绝不冒充「没命中」。
    assert item["fizzle_flag"]["state"] == "unavailable"
    assert item["fizzle_flag"]["reason"] == "burst_profile_unavailable"
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


def test_opening_same_slot_reference_is_loaded_once_and_exposed(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": []})
    calls: list[tuple[str, int]] = []

    reference_rows = []
    exact_days = opportunities._exact_prior_xnys_session_labels("2026-07-28")
    assert len(exact_days) == 20
    for day in exact_days:
        reference_rows.append(
            {
                "date": f"{day}T09:30:00-04:00",
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.6,
                "volume": 1_000_000.0,
            }
        )

    def fetch(symbol: str, *, days: int = 4):
        calls.append((symbol, days))
        if days == opportunities._INTRADAY_OPENING_REFERENCE_FETCH_DAYS:
            return reference_rows, "fixture_5m"
        return _five_minute_session("2026-07-28", bar_count=12), "fixture_5m"

    monkeypatch.setattr(opportunities, "_fetch_intraday_5m_bars", fetch)
    client = _client()
    payload = {"symbols": ["NVDA"]}
    first = client.post("/api/v1/opportunities/intraday-top", json=payload)
    refreshed = client.post(
        "/api/v1/opportunities/intraday-top", json={**payload, "refresh": True}
    )
    assert first.status_code == refreshed.status_code == 200
    opening = first.json()["candidates"][0]["session_bursts"]["opening_impulse"]
    assert opening["state"] == "not_qualified"
    assert opening["reference_session_count"] == 20
    assert opening["bar_start_et"] == "09:30"
    assert calls == [
        ("NVDA", opportunities._INTRADAY_OPENING_REFERENCE_FETCH_DAYS),
        ("NVDA", opportunities._INTRADAY_BURST_FETCH_DAYS),
    ]


@pytest.mark.parametrize(
    ("second", "retryable_pending_count"),
    [(5, 2), (15, 0)],
)
def test_opening_fast_path_observes_plan_and_pinned_before_full_scan(
    monkeypatch,
    second,
    retryable_pending_count,
):
    now = datetime(2026, 8, 31, 13, 35, second, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda day: ["TSLA"])
    monkeypatch.setattr(
        opportunities, "_configured_intraday_pinned_tickers", lambda: ["NVDA"]
    )
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: [])
    monkeypatch.setattr(
        opportunities,
        "_intraday_daily_inputs",
        lambda symbols, **kwargs: {
            symbol: {"atr14": 10.0, "prior_close": 100.0}
            for symbol in symbols
        },
    )

    def profiles(symbols, **kwargs):
        return {
            symbol: {
                "state": "pending",
                "alert_qualified": False,
                "rule_version": "opening_impulse_v2",
                "checkpoint": 5,
                "checkpoint_label": "09:35",
                "retryable_pending": retryable_pending_count > 0,
            }
            for symbol in symbols
        }

    monkeypatch.setattr(
        opportunities, "_load_intraday_opening_progressive_profiles", profiles
    )
    observed = []
    summary = opportunities.warm_opening_impulse_scan(
        observed.append, target_symbols=("TSLA", "NVDA"),
    )

    assert summary == {
        "action": "observed",
        "candidate_count": 2,
        "pending_count": 2,
        "retryable_pending_count": retryable_pending_count,
        "qualified_count": 0,
        "opening_critical_window": True,
    }
    assert len(observed) == 1
    assert [row["ticker"] for row in observed[0]["candidates"]] == [
        "TSLA",
        "NVDA",
    ]
    assert observed[0]["opening_fast_path"] is True
    assert observed[0]["as_of"] == now.isoformat()
    assert observed[0]["alert_observed_at"] == now.isoformat()


def test_opening_fast_path_prewarms_only_1m_history_before_0935(monkeypatch):
    now = datetime(2026, 8, 31, 13, 15, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda day: [])
    monkeypatch.setattr(
        opportunities, "_configured_intraday_pinned_tickers", lambda: []
    )
    monkeypatch.setattr(
        opportunities, "_configured_symbols", lambda: ["TSLA", "NVDA"]
    )
    daily_calls = []
    reference_calls = []
    reference_1m_calls = []
    monkeypatch.setattr(
        opportunities,
        "_intraday_daily_inputs",
        lambda symbols, **kwargs: daily_calls.append(list(symbols)) or {},
    )
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_opening_reference_history",
        lambda symbols, **kwargs: reference_calls.append(list(symbols)) or {},
    )
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_opening_1m_reference_history",
        lambda symbols, **kwargs: reference_1m_calls.append(list(symbols)) or {},
    )
    observed = []

    summary = opportunities.warm_opening_impulse_scan(
        observed.append, target_symbols=("TSLA", "NVDA"), prewarm_only=True,
    )

    assert summary == {
        "action": "prewarmed",
        "candidate_count": 2,
        "opening_critical_window": False,
    }
    assert daily_calls == [["TSLA", "NVDA"]]
    assert reference_calls == []  # retired native-5m lane must not delay opening
    assert reference_1m_calls == [["TSLA", "NVDA"]]
    assert observed == []


def test_progressive_opening_provider_lag_is_retryable_and_observed(monkeypatch):
    now = datetime(2026, 8, 31, 13, 32, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda day: ["TSLA"])
    monkeypatch.setattr(
        opportunities, "_configured_intraday_pinned_tickers", lambda: []
    )
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: [])
    monkeypatch.setattr(
        opportunities,
        "_intraday_daily_inputs",
        lambda symbols, **kwargs: {
            "TSLA": {"atr14": 10.0, "prior_close": 100.0}
        },
    )
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_opening_1m_reference_history",
        lambda symbols, **kwargs: {
            "TSLA": {
                "bars": [],
                "source": "fixture_1m",
                "reference_session_count": 20,
                "unavailable_reason": None,
            }
        },
    )

    def provider_lag(*args, **kwargs):
        raise TimeoutError("1m bar has not settled")

    monkeypatch.setattr(opportunities, "_fetch_intraday_1m_bars", provider_lag)
    observed = []

    summary = opportunities.warm_opening_impulse_scan(
        observed.append, target_symbols=("TSLA",), current_1m_reader=provider_lag,
    )

    assert summary == {
        "action": "observed",
        "candidate_count": 1,
        "pending_count": 1,
        "retryable_pending_count": 1,
        "qualified_count": 0,
        "opening_critical_window": True,
    }
    profile = observed[0]["candidates"][0]["session_bursts"][
        "opening_progressive"
    ]
    assert profile["state"] == "pending"
    assert profile["checkpoint"] == 1
    assert profile["retryable_pending"] is True
    assert profile["unavailable_reason"] == "history_1m_unavailable:TimeoutError"


def test_fetch_intraday_1m_normalizes_moomoo_end_labels_by_one_minute(
    monkeypatch,
):
    calls = []

    def history(_self, symbol, *, period, days, include_stock_name):
        calls.append((symbol, period, days, include_stock_name))
        return {
            "source": "MoomooFetcher",
            "data": [
                {
                    "date": "2026-08-31T09:31:00-04:00",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.5,
                    "close": 100.8,
                    "volume": 1_000,
                }
            ],
        }

    monkeypatch.setattr(
        "src.services.stock_service.StockService.get_history_data",
        history,
    )

    bars, source = opportunities._fetch_intraday_1m_bars("TSLA", days=3)

    assert source == "MoomooFetcher"
    assert calls == [("TSLA", "1m", 3, False)]
    assert bars[0]["date"] == "2026-08-31T09:30:00-04:00"


def _opening_one_minute_session(day: str) -> list[dict]:
    return [
        {
            "date": f"{day}T09:{minute:02d}:00-04:00",
            "open": 100.0,
            "high": 100.5,
            "low": 99.8,
            "close": 100.3,
            "volume": 1_000,
        }
        for minute in range(30, 35)
    ]


def test_opening_1m_reference_count_never_backfills_an_older_session():
    exact_days = list(
        opportunities._exact_prior_xnys_session_labels("2026-08-31")
    )
    assert len(exact_days) == 20
    complete = [
        bar
        for day in exact_days
        for bar in _opening_one_minute_session(day)
    ]
    assert opportunities._opening_1m_reference_session_count(
        complete,
        market_date_et="2026-08-31",
    ) == 20

    missing_day = exact_days[-3]
    with_gap = [
        bar for bar in complete if not bar["date"].startswith(missing_day)
    ]
    older_day = opportunities._exact_prior_xnys_session_labels(exact_days[0])[-1]
    with_gap.extend(_opening_one_minute_session(older_day))

    assert opportunities._opening_1m_reference_session_count(
        with_gap,
        market_date_et="2026-08-31",
    ) == 19


def test_opening_1m_reference_cache_keeps_only_exact_opening_minutes(
    monkeypatch,
):
    exact_days = list(
        opportunities._exact_prior_xnys_session_labels("2026-08-31")
    )
    bars = [
        bar
        for day in exact_days
        for bar in _opening_one_minute_session(day)
    ]
    # Provider responses may contain every regular/extended minute.  Neither
    # an irrelevant regular-session row nor an older complete session belongs
    # in the day cache used by the sequential detector.
    bars.append(
        {
            "date": f"{exact_days[-1]}T12:00:00-04:00",
            "open": 100.0,
            "high": 100.5,
            "low": 99.8,
            "close": 100.3,
            "volume": 1_000,
        }
    )
    older_day = opportunities._exact_prior_xnys_session_labels(exact_days[0])[-1]
    bars.extend(_opening_one_minute_session(older_day))
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(
        opportunities,
        "_fetch_intraday_1m_bars",
        lambda symbol, **kwargs: (bars, "MoomooFetcher"),
    )

    result = opportunities._load_intraday_opening_1m_reference_history(
        ["TSLA"],
        market_date_et="2026-08-31",
    )

    cached = result["TSLA"]
    assert cached["reference_session_count"] == 20
    assert len(cached["bars"]) == 100
    assert {row["date"][:10] for row in cached["bars"]} == set(exact_days)
    assert all("T09:3" in row["date"] for row in cached["bars"])


def _put_opening_v2_readiness_cache(
    symbol: str,
    *,
    source: str,
    reference_session_count: int,
    expires_at: float = 200.0,
) -> None:
    with opportunities._scan_cache_lock:
        opportunities._intraday_opening_1m_reference_cache[
            (symbol, "2026-08-31")
        ] = opportunities._ScanCacheEntry(
            expires_at=expires_at,
            result={
                "bars": [],
                "source": source,
                "reference_session_count": reference_session_count,
                "unavailable_reason": None,
            },
        )


def test_opening_v2_readiness_is_unknown_without_current_day_cache(monkeypatch):
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(
        opportunities,
        "_fetch_intraday_1m_bars",
        lambda *args, **kwargs: pytest.fail(
            "readiness inspection must never perform provider I/O"
        ),
    )

    readiness = opportunities.get_opening_v2_provider_readiness(
        market_date_et="2026-08-31",
        target_symbols=["TSLA"],
    )

    assert readiness["target_count"] == 1
    assert readiness["cached_count"] == 0
    assert readiness["ready_count"] == 0
    assert readiness["provider_counts"] == {}
    assert readiness["reason_code"] == "not_observed_today"


def test_opening_v2_readiness_requires_moomoo_with_all_20_sessions(monkeypatch):
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    _put_opening_v2_readiness_cache(
        "TSLA",
        source="MoomooFetcher",
        reference_session_count=20,
    )

    readiness = opportunities.get_opening_v2_provider_readiness(
        market_date_et="2026-08-31",
        target_symbols=["TSLA"],
    )

    assert readiness["required_interval"] == "1m"
    assert readiness["required_reference_sessions"] == 20
    assert readiness["target_count"] == 1
    assert readiness["cached_count"] == 1
    assert readiness["ready_count"] == 1
    assert readiness["provider_counts"] == {"MoomooFetcher": 1}
    assert readiness["reason_code"] == "ready"


def test_opening_v2_readiness_rejects_yfinance_even_if_count_claims_20(
    monkeypatch,
):
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    # Lock the provider contract independently of row count: Yahoo's 1m
    # retention is seven calendar days and can never be the v2 baseline.
    _put_opening_v2_readiness_cache(
        "TSLA",
        source="YfinanceFetcher",
        reference_session_count=20,
    )

    readiness = opportunities.get_opening_v2_provider_readiness(
        market_date_et="2026-08-31",
        target_symbols=["TSLA"],
    )

    assert readiness["cached_count"] == 1
    assert readiness["ready_count"] == 0
    assert readiness["provider_counts"] == {"YfinanceFetcher": 1}
    assert readiness["reason_code"] == "yfinance_1m_depth_insufficient"


def test_opening_v2_readiness_is_partial_when_only_some_targets_are_ready(
    monkeypatch,
):
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    _put_opening_v2_readiness_cache(
        "TSLA",
        source="MoomooFetcher",
        reference_session_count=20,
    )
    _put_opening_v2_readiness_cache(
        "META",
        source="MoomooFetcher",
        reference_session_count=19,
    )

    readiness = opportunities.get_opening_v2_provider_readiness(
        market_date_et="2026-08-31",
        target_symbols=["TSLA", "META"],
    )

    assert readiness["target_count"] == 2
    assert readiness["cached_count"] == 2
    assert readiness["ready_count"] == 1
    assert readiness["provider_counts"] == {"MoomooFetcher": 2}
    assert readiness["reason_code"] == "partial_or_incomplete_reference"


def test_progressive_opening_rejects_reference_current_source_mismatch(
    monkeypatch,
):
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_opening_1m_reference_history",
        lambda symbols, **kwargs: {
            "TSLA": {
                "bars": [],
                "source": "MoomooFetcher",
                "reference_session_count": 20,
                "unavailable_reason": None,
            }
        },
    )
    monkeypatch.setattr(
        opportunities,
        "_fetch_intraday_1m_bars",
        lambda symbol, **kwargs: ([], "YFinanceFetcher"),
    )
    monkeypatch.setattr(
        opportunities,
        "compute_sequential_opening_impulse_profile",
        lambda *args, **kwargs: {
            "state": "qualified",
            "alert_qualified": True,
            "direction": "up",
            "rule_version": "opening_impulse_v2",
            "checkpoint": 2,
            "checkpoint_label": "09:32",
        },
    )

    profiles = opportunities._load_intraday_opening_progressive_profiles(
        ["TSLA"],
        market_date_et="2026-08-31",
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 13, 32, 5, tzinfo=timezone.utc),
        atr14_by_symbol={"TSLA": 10.0},
        prior_close_by_symbol={"TSLA": 100.0},
    )

    profile = profiles["TSLA"]
    assert profile["state"] == "unavailable"
    assert profile["alert_qualified"] is False
    reason = str(profile.get("unavailable_reason") or "")
    assert "source" in reason and "mismatch" in reason


def test_opening_fast_path_uses_0935_completed_1m_v2(monkeypatch):
    now = datetime(2026, 8, 31, 13, 35, 5, tzinfo=timezone.utc)
    progressive_profile = {
        "state": "qualified",
        "alert_qualified": True,
        "rule_version": "opening_impulse_v2",
        "bar_start_et": "09:30",
        "bar_end_et": "09:35",
        "completed_as_of": "2026-08-31T09:35:02-04:00",
        "reference_session_count": 20,
        "direction": "up",
        "quality_score": 4.2,
        "checkpoint": 5,
        "checkpoint_label": "09:35",
        "confirmation_points": [5],
        "confirmation_mode": "completed_1m_cumulative",
        "retryable_pending": False,
    }
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda day: ["TSLA"])
    monkeypatch.setattr(
        opportunities, "_configured_intraday_pinned_tickers", lambda: []
    )
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: [])
    monkeypatch.setattr(
        opportunities,
        "_intraday_daily_inputs",
        lambda symbols, **kwargs: {
            "TSLA": {"atr14": 10.0, "prior_close": 100.0}
        },
    )
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_opening_progressive_profiles",
        lambda symbols, **kwargs: {"TSLA": progressive_profile},
    )
    observed = []

    summary = opportunities.warm_opening_impulse_scan(
        observed.append, target_symbols=("TSLA",),
    )

    progressive = observed[0]["candidates"][0]["session_bursts"][
        "opening_progressive"
    ]
    assert "opening_impulse" not in observed[0]["candidates"][0]["session_bursts"]
    assert progressive["state"] == "qualified"
    assert progressive["alert_qualified"] is True
    assert progressive["rule_version"] == "opening_impulse_v2"
    assert progressive["checkpoint"] == 5
    assert progressive["checkpoint_label"] == "09:35"
    assert progressive["confirmation_points"] == [5]
    assert progressive["confirmation_mode"] == "completed_1m_cumulative"
    assert summary["qualified_count"] == 1


@pytest.mark.parametrize("minute", [32, 33, 35])
def test_opening_full_69_uses_subscribed_1m_without_history_rpc(monkeypatch, minute):
    from src.opportunities.p0_2m_calibration import DEFAULT_P0_UNIVERSE_69

    symbols = DEFAULT_P0_UNIVERSE_69
    now = datetime(2026, 8, 31, 13, minute, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda day: [])
    monkeypatch.setattr(opportunities, "_configured_intraday_pinned_tickers", lambda: [])
    for name in ("_fetch_intraday_1m_bars", "_intraday_daily_inputs"):
        monkeypatch.setattr(
            opportunities, name,
            lambda *args, **kwargs: pytest.fail("critical opening must use caches only"),
        )
    references = [
        row for day in opportunities._exact_prior_xnys_session_labels("2026-08-31")
        for row in _opening_one_minute_session(day)
    ]
    for symbol in symbols:
        _put_opening_v2_readiness_cache(
            symbol, source="MoomooFetcher", reference_session_count=20,
        )
        opportunities._intraday_opening_1m_reference_cache[
            (symbol, "2026-08-31")
        ].result["bars"] = references
        opportunities._intraday_daily_cache[(symbol, "2026-08-31")] = (
            opportunities._ScanCacheEntry(
                expires_at=200.0, result={"atr14": 2.0, "prior_close": 99.5},
            )
        )
    raw_current = [
        {
            "date": f"2026-08-31T09:{31 + offset:02d}:00-04:00",
            "open": 100.0 + offset * 0.9,
            "high": 101.0 + offset * 0.9,
            "low": 99.95 + offset * 0.9,
            "close": 100.9 + offset * 0.9,
            "volume": 2_500,
        }
        for offset in range(5)
    ]
    reads = []

    def subscribed(symbol, *, as_of):
        assert as_of == now
        reads.append(symbol)
        # Includes future minutes deliberately: normalization + the strict
        # completed-bar filter must still restrict the active checkpoint.
        return raw_current, "MoomooFetcher"

    opportunities.set_opening_signal_universe(
        symbols, current_1m_reader=subscribed,
        stream_health_reader=lambda: {"stream_ready": True},
    )
    observed = []
    summary = opportunities.warm_opening_impulse_scan(observed.append)
    readiness = opportunities.get_opening_v2_provider_readiness(
        market_date_et="2026-08-31",
    )
    assert summary["candidate_count"] == summary["qualified_count"] == 69
    assert set(reads) == set(symbols) and len(reads) == 69
    assert [row["ticker"] for row in observed[0]["candidates"]] == list(symbols)
    assert all(
        row["session_bursts"]["opening_progressive"]["checkpoint"] == minute - 30
        for row in observed[0]["candidates"]
    )
    assert readiness["target_count"] == readiness["ready_count"] == 69
    assert readiness["current_stream_ready"] is True
    assert readiness["current_stream_reason"] == "ready"


def test_opening_prewarms_full_69_before_0900_and_cache_survives_rollover(monkeypatch):
    from src.opportunities.p0_2m_calibration import DEFAULT_P0_UNIVERSE_69

    symbols = DEFAULT_P0_UNIVERSE_69
    now = datetime(2026, 8, 31, 8, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    days = set(opportunities._exact_prior_xnys_session_labels("2026-08-31"))
    days.update(opportunities._exact_prior_xnys_session_labels("2026-09-01"))
    bars = [row for day in sorted(days) for row in _opening_one_minute_session(day)]
    reads = []
    monkeypatch.setattr(
        opportunities, "_fetch_intraday_1m_bars",
        lambda symbol, **kwargs: reads.append(symbol) or (bars, "MoomooFetcher"),
    )
    daily_calls = []
    monkeypatch.setattr(
        opportunities, "_intraday_daily_inputs",
        lambda targets, **kwargs: daily_calls.append(tuple(targets)) or {},
    )
    summary = opportunities.warm_opening_impulse_scan(
        target_symbols=symbols, prewarm_only=True,
    )
    assert summary["action"] == "prewarmed"
    assert summary["candidate_count"] == 69
    assert daily_calls == [symbols]
    opportunities._load_intraday_opening_1m_reference_history(
        list(symbols), market_date_et="2026-09-01",
    )
    assert len(reads) == 138
    for day in ("2026-08-31", "2026-09-01"):
        cached = opportunities._load_intraday_opening_1m_reference_history(
            list(symbols), market_date_et=day, cache_only=True,
        )
        assert len(cached) == 69
        assert all(row["reference_session_count"] == 20 for row in cached.values())
    opportunities.warm_opening_impulse_scan(target_symbols=symbols, prewarm_only=True)
    assert len(reads) == 138  # repeated prewarm does not refetch intact prefixes


def test_opening_daily_cache_retains_69_current_and_next_session_entries(monkeypatch):
    from src.opportunities.p0_2m_calibration import DEFAULT_P0_UNIVERSE_69

    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    for day in ("2026-08-31", "2026-09-01"):
        for symbol in DEFAULT_P0_UNIVERSE_69:
            opportunities._prune_intraday_daily_cache(100.0)
            opportunities._intraday_daily_cache[(symbol, day)] = (
                opportunities._ScanCacheEntry(expires_at=200.0, result={"atr14": 2.0})
            )
    for day in ("2026-08-31", "2026-09-01"):
        assert len(opportunities._cached_opening_daily_inputs(
            list(DEFAULT_P0_UNIVERSE_69), market_date_et=day,
        )) == 69


def test_opening_missing_subscription_or_baseline_fails_closed_without_rpc(monkeypatch):
    now = datetime(2026, 8, 31, 13, 33, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda day: [])
    monkeypatch.setattr(opportunities, "_configured_intraday_pinned_tickers", lambda: [])
    monkeypatch.setattr(
        opportunities, "_fetch_intraday_1m_bars",
        lambda *args, **kwargs: pytest.fail("cache misses must never trigger opening RPC"),
    )
    opportunities.set_opening_signal_universe(("TSLA",))
    observed = []
    summary = opportunities.warm_opening_impulse_scan(observed.append)
    assert summary["qualified_count"] == 0
    assert observed[0]["candidates"][0]["session_bursts"]["opening_progressive"][
        "alert_qualified"
    ] is False
    readiness = opportunities.get_opening_v2_provider_readiness(
        market_date_et="2026-08-31",
    )
    assert readiness["current_stream_ready"] is False
    assert readiness["current_stream_reason"] == "opening_subscription_cache_unavailable"


@pytest.mark.parametrize("count", [1, 69])
def test_opening_calculation_failure_isolated_to_one_symbol(monkeypatch, count):
    from src.opportunities.p0_2m_calibration import DEFAULT_P0_UNIVERSE_69

    symbols = DEFAULT_P0_UNIVERSE_69 if count == 69 else ("TSLA",)
    failed_symbol = symbols[0]
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    for symbol in symbols:
        _put_opening_v2_readiness_cache(
            symbol, source="MoomooFetcher", reference_session_count=20,
        )

    def calculate(*args, **kwargs):
        if kwargs["atr14"] == 2.0:
            raise ValueError("one symbol computation failed")
        return {"state": "qualified", "alert_qualified": True, "direction": "up"}

    monkeypatch.setattr(opportunities, "compute_sequential_opening_impulse_profile", calculate)
    profiles = opportunities._load_intraday_opening_progressive_profiles(
        list(symbols), market_date_et="2026-08-31", quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 13, 33, 5, tzinfo=timezone.utc),
        atr14_by_symbol={symbol: 2.0 if symbol == failed_symbol else 3.0 for symbol in symbols},
        current_1m_reader=lambda *args, **kwargs: ([], "MoomooFetcher"), cache_only=True,
    )
    assert set(profiles) == set(symbols)
    failed = profiles[failed_symbol]
    assert failed["state"] == "unavailable"
    assert failed["alert_qualified"] is False
    assert failed["unavailable_reason"] == "opening_1m_profile_compute_failed:ValueError"
    assert failed["checkpoint"] == 3
    assert sum(profile["alert_qualified"] for profile in profiles.values()) == count - 1


@pytest.mark.parametrize("minute", [30, 32, 35])
def test_page_opening_window_reads_legacy_cache_without_cold_history(monkeypatch, minute):
    now = datetime(2026, 8, 31, 13, minute, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    for name in ("_load_intraday_opening_reference_history", "_fetch_intraday_5m_bars"):
        monkeypatch.setattr(
            opportunities, name,
            lambda *args, **kwargs: pytest.fail("critical page must not fetch 5m history"),
        )
    opportunities._intraday_burst_cache[("TSLA", "2026-08-31", "current_session")] = (
        opportunities._ScanCacheEntry(
            expires_at=200.0,
            result={"profile": {"state": "ready", "legacy": True}, "bars": [{"cached": True}]},
        )
    )
    profiles, bars = opportunities._load_intraday_burst_profiles(
        ["TSLA", "NVDA"], market_date_et="2026-08-31",
        quote_session_scope="current_session", as_of=now,
    )
    assert profiles["TSLA"] == {"state": "ready", "legacy": True}
    assert bars["TSLA"] == [{"cached": True}]
    assert profiles["NVDA"]["state"] == "unavailable"
    assert profiles["NVDA"]["unavailable_reason"] == "opening_critical_window_cache_not_ready"
    assert bars["NVDA"] == []


def test_page_history_resumes_at_0936_and_when_request_predates_window(monkeypatch):
    now = datetime(2026, 8, 31, 13, 36, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    calls = []
    monkeypatch.setattr(
        opportunities, "_load_intraday_opening_reference_history",
        lambda *args, **kwargs: calls.append("reference") or {},
    )
    monkeypatch.setattr(
        opportunities, "_fetch_intraday_5m_bars",
        lambda *args, **kwargs: calls.append("current") or ([], "MoomooFetcher"),
    )
    opportunities._load_intraday_burst_profiles(
        ["TSLA"], market_date_et="2026-08-31", quote_session_scope="current_session", as_of=now,
    )
    assert calls == ["reference", "current"]
    assert opportunities._opening_critical_window_active(
        now.replace(minute=29),
    ) is False
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now.replace(minute=32))
    assert opportunities._opening_critical_window_active(now.replace(minute=29)) is True


def test_page_daily_history_missing_in_opening_window_is_explicit_not_fetched(monkeypatch):
    now = datetime(2026, 8, 31, 13, 32, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(
        opportunities, "_create_data_fetcher_manager",
        lambda: pytest.fail("critical page must not create a cold daily provider"),
    )
    opportunities._intraday_daily_cache[("TSLA", "2026-08-31")] = opportunities._ScanCacheEntry(
        expires_at=200.0, result={"atr14": 2.0, "source": "MoomooFetcher"},
    )
    daily = opportunities._intraday_daily_inputs(
        ["TSLA", "NVDA"], as_of=now, market_date_et="2026-08-31",
    )
    assert daily["TSLA"]["atr14"] == 2.0
    assert daily["NVDA"]["atr14"] is None
    assert daily["NVDA"]["atr14_unavailable_reason"] == "opening_critical_window_cache_not_ready"
    monkeypatch.setattr(opportunities, "_fetch_intraday_1m_bars", lambda *args, **kwargs: pytest.fail(
        "prewarm that crosses into opening must not start a cold prefix request"
    ))
    assert opportunities._load_intraday_opening_1m_reference_history(
        ["NVDA"], market_date_et="2026-08-31",
    ) == {}


@pytest.mark.parametrize(
    ("session_count", "expected_ttl"),
    [
        (19, opportunities._INTRADAY_OPENING_REFERENCE_FAILURE_TTL_SECONDS),
        (20, opportunities._INTRADAY_OPENING_REFERENCE_CACHE_TTL_SECONDS),
    ],
)
def test_opening_reference_cache_uses_short_ttl_until_20_valid_slots(
    monkeypatch,
    session_count,
    expected_ttl,
):
    days = []
    cursor = date(2026, 8, 28)
    while len(days) < session_count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    bars = []
    for day in days:
        bars.extend(_five_minute_session(day))

    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(
        opportunities,
        "_fetch_intraday_5m_bars",
        lambda symbol, **kwargs: (bars, "fixture_5m"),
    )

    result = opportunities._load_intraday_opening_reference_history(
        ["TSLA"],
        market_date_et="2026-08-31",
    )

    assert result["TSLA"]["reference_session_count"] == session_count
    entry = opportunities._intraday_opening_reference_cache[
        ("TSLA", "2026-08-31")
    ]
    assert entry.expires_at == 100.0 + expected_ttl


def test_legacy_burst_profile_does_not_synthesize_progressive_after_opening(
    monkeypatch,
):
    now = datetime(2026, 8, 31, 13, 36, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: now)
    key = ("TSLA", "2026-08-31", "current_session")
    with opportunities._scan_cache_lock:
        opportunities._intraday_burst_cache[key] = opportunities._ScanCacheEntry(
            expires_at=999.0,
            result={
                "profile": {
                    "state": "ready",
                    "opening_impulse": {"state": "pending"},
                },
                "bars": [{"cached": True}],
            },
        )
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_opening_reference_history",
        lambda symbols, **kwargs: {"TSLA": {"bars": [], "source": "fixture"}},
    )
    fetch_calls = []
    monkeypatch.setattr(
        opportunities,
        "_fetch_intraday_5m_bars",
        lambda symbol, **kwargs: fetch_calls.append(symbol) or ([], "fixture"),
    )
    monkeypatch.setattr(
        opportunities,
        "compute_session_burst_profile",
        lambda *args, **kwargs: {"state": "insufficient_bars"},
    )
    monkeypatch.setattr(
        opportunities,
        "compute_opening_impulse_profile",
        lambda *args, **kwargs: {
            "state": "qualified",
            "alert_qualified": True,
            "rule_version": "opening_impulse_v1",
            "bar_start_et": "09:30",
            "bar_end_et": "09:35",
            "completed_as_of": "2026-08-31T09:35:02-04:00",
            "reference_session_count": 20,
            "direction": "up",
        },
    )

    profiles, bars = opportunities._load_intraday_burst_profiles(
        ["TSLA"],
        market_date_et="2026-08-31",
        quote_session_scope="current_session",
        as_of=now,
        atr14_by_symbol={"TSLA": 10.0},
        prior_close_by_symbol={"TSLA": 100.0},
    )

    assert fetch_calls == ["TSLA"]
    assert profiles["TSLA"]["opening_impulse"]["state"] == "qualified"
    assert "opening_progressive" not in profiles["TSLA"]
    assert bars["TSLA"] == []


def test_burst_profile_cache_refreshes_on_new_completed_5m_boundary(monkeypatch):
    key = ("TSLA", "2026-08-31", "current_session")
    with opportunities._scan_cache_lock:
        opportunities._intraday_burst_cache[key] = opportunities._ScanCacheEntry(
            expires_at=999.0,
            result={
                "profile": {
                    "state": "ready",
                    "current": {"end_et": "10:00"},
                    "opening_impulse": {"state": "not_qualified"},
                },
                "bars": [{"cached": True}],
            },
        )
    monkeypatch.setattr(opportunities, "_cache_now", lambda: 100.0)
    monkeypatch.setattr(
        opportunities,
        "_load_intraday_opening_reference_history",
        lambda symbols, **kwargs: {"TSLA": {"bars": [], "source": "fixture"}},
    )
    fetch_calls = []
    monkeypatch.setattr(
        opportunities,
        "_fetch_intraday_5m_bars",
        lambda symbol, **kwargs: fetch_calls.append(symbol) or ([], "fixture"),
    )
    monkeypatch.setattr(
        opportunities,
        "compute_session_burst_profile",
        lambda *args, **kwargs: {
            "state": "ready",
            "current": {"end_et": "10:05"},
        },
    )
    monkeypatch.setattr(
        opportunities,
        "compute_opening_impulse_profile",
        lambda *args, **kwargs: {"state": "not_qualified"},
    )

    profiles, _bars = opportunities._load_intraday_burst_profiles(
        ["TSLA"],
        market_date_et="2026-08-31",
        quote_session_scope="current_session",
        as_of=datetime(2026, 8, 31, 14, 5, 5, tzinfo=timezone.utc),
        atr14_by_symbol={"TSLA": 10.0},
        prior_close_by_symbol={"TSLA": 100.0},
    )

    assert fetch_calls == ["TSLA"]
    assert profiles["TSLA"]["current"]["end_et"] == "10:05"


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
    # 中性时段上下文与 Moomoo 无关（纯 ET 时钟）：未配置时同样如实标注。
    assert body["session_phase"] == "prime"
    assert body["session_phase_label"] == "10:00–11:00 · 上午流动性与波动环境"
    assert body["session_phase_hint_basis"] == "america_new_york_liquidity_context_neutral_v2"
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
    # 2026-07-28 17:30 UTC = 13:30 ET → 中性的午间环境标签。
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
    assert body["session_phase_label"] == "13:00–14:00 · 午间流动性与波动环境"
    assert "默认观望" not in body["session_phase_label"]
    assert any("系统标注，用户过滤" in text for text in body["limitations"])


# ---------------------------------------------------------------------------
# 服务端预热循环 + 延迟诊断（generated_in_seconds / served_from，additive）
# ---------------------------------------------------------------------------


def _stub_default_universe(monkeypatch):
    """固定「清单未配置 → STOCK_LIST=[NVDA]」的默认轮询路径（单层）。"""

    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(opportunities, "_configured_intraday_watchlist", lambda: [])
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["NVDA"])


def test_latency_fields_fresh_then_cache_reports_original_generation_time(
    monkeypatch,
):
    """G-latency：fresh 响应带工厂墙钟耗时；缓存命中报告原始耗时不变。"""

    _stub_default_universe(monkeypatch)
    client = _client()

    first = client.post("/api/v1/opportunities/intraday-top", json={})
    second = client.post("/api/v1/opportunities/intraday-top", json={})

    assert first.status_code == second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["served_from"] == "fresh"
    assert isinstance(first_body["generated_in_seconds"], float)
    assert first_body["generated_in_seconds"] >= 0.0
    # 缓存命中：served_from 翻转为 cache，但耗时仍是原始生成耗时。
    assert second_body["served_from"] == "cache"
    assert second_body["generated_in_seconds"] == first_body["generated_in_seconds"]
    # 除 additive 延迟字段外载荷逐字节一致（同一缓存条目）。
    for field in ("generated_at", "run_id", "candidates", "universe"):
        assert second_body[field] == first_body[field]


def test_warm_scan_populates_the_default_poll_key_as_warm_cache(monkeypatch):
    """预热结果与请求驱动结果同缓存同 TTL：默认轮询直接命中 warm_cache。"""

    _stub_default_universe(monkeypatch)

    summary = opportunities.warm_default_intraday_top_scan()
    assert summary["led_flight"] is True
    assert summary["generated_by"] == "warm_scheduler"
    assert isinstance(summary["generated_in_seconds"], float)

    response = _client().post("/api/v1/opportunities/intraday-top", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["served_from"] == "warm_cache"
    assert body["generated_in_seconds"] == pytest.approx(
        round(summary["generated_in_seconds"], 3), abs=1e-9
    )
    # 载荷本身不特殊化：仍是完整合同（schema/候选/诚实字段照旧）。
    assert body["schema_version"] == "intraday-top/1.0"
    assert body["universe"] == ["NVDA"]
    assert body["moomoo_enabled"] is False


def test_warm_tick_joins_user_led_flight_instead_of_duplicating(monkeypatch):
    """G-warm-join：用户在 leader 时预热只加入等待，绝不并发第二个工厂。"""

    monkeypatch.setattr(opportunities, "_intraday_now", lambda: _FIXED_NOW)
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: False)
    monkeypatch.setattr(
        opportunities, "_configured_intraday_watchlist", lambda: ["NVDA"]
    )
    monkeypatch.setattr(
        opportunities, "_configured_intraday_pinned_tickers", lambda: []
    )
    monkeypatch.setattr(opportunities, "_configured_deep_lane_max", lambda: 8)

    started = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    factory_calls = 0

    def slow_two_tier(*_args, **_kwargs):
        nonlocal factory_calls
        with counter_lock:
            factory_calls += 1
        started.set()
        assert release.wait(timeout=5)
        return {"payload": "user-led"}

    monkeypatch.setattr(
        opportunities, "_execute_intraday_top_two_tier", slow_two_tier
    )

    key, factory = opportunities._intraday_top_key_and_factory([], [], 5)
    user_meta: dict = {}

    def user_leg():
        return opportunities._get_or_compute_scan(
            key,
            factory,
            ttl_seconds=opportunities._INTRADAY_TOP_CACHE_TTL_SECONDS,
            lease_seconds=opportunities._INTRADAY_TOP_LEASE_SECONDS,
            wait_timeout_seconds=opportunities._INTRADAY_TOP_LEASE_SECONDS,
            meta_out=user_meta,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        user = executor.submit(user_leg)
        assert started.wait(timeout=2)
        warm = executor.submit(opportunities.warm_default_intraday_top_scan)
        # 给预热线程时间加入在途 flight（与既有单飞测试同一模式）。
        time.sleep(0.05)
        release.set()
        user_result = user.result(timeout=5)
        warm_summary = warm.result(timeout=5)

    assert factory_calls == 1
    assert user_result == {"payload": "user-led"}
    assert user_meta["led_flight"] is True
    # 预热加入了用户 leader 的 flight：既没抢 leader 也没另起工厂。
    assert warm_summary["led_flight"] is False
    assert warm_summary["generated_by"] == "request"
