# -*- coding: utf-8 -*-
"""watchlist v1 两层扫描合同测试（INTRADAY_WATCHLIST → 宽层快照 + 异动闸门）。

回归锁定：未配置清单 / 显式 symbols 时行为与既有单层扫描逐字节一致
（universe_scan 恒为 null，候选无 scan_tier / deep_lane_reason）。两层模式下：
每轮仅 1 次批量快照；异动闸门按 |涨跌幅|→成交额 晋升前 K 档；当日冻结盘前
计划标的始终占深度位；宽层行只有快照字段，绝不虚构深度层读数。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.v1.endpoints import opportunities
from api.v1.tests.test_intraday_top_endpoint import (
    _FIXED_NOW,
    _five_minute_bars,
    _quote,
    _stub_daily_loader,
    _stub_earnings,
    _stub_events,
)

# 2026-07-28 is a Tuesday; 12:30 UTC = 08:30 ET (EDT) → premarket phase.
_PREMARKET_NOW = datetime(2026, 7, 28, 12, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_opportunity_scan_cache():
    opportunities._reset_scan_cache_for_tests()
    yield
    opportunities._reset_scan_cache_for_tests()


@pytest.fixture(autouse=True)
def _no_real_plan_reads(monkeypatch):
    """默认无今日冻结计划；单测绝不读真实机会快照数据库。"""

    monkeypatch.setattr(opportunities, "_todays_plan_tickers", lambda market_date_et: [])


@pytest.fixture(autouse=True)
def _no_real_pinned_tickers(monkeypatch):
    """默认无用户钉选（与真实 .env 隔离）；钉选用例显式覆写。"""

    monkeypatch.setattr(
        opportunities, "_configured_intraday_pinned_tickers", lambda: []
    )


@pytest.fixture(autouse=True)
def _no_real_expiry_availability_reads(monkeypatch):
    """默认车道可用性链元数据不可得；单测绝不打真实 Moomoo 到期日接口。

    默认返回 None ⇒ 逐标的 unavailable ⇒ day_type=unknown（fail closed），
    与「Moomoo 未启用」路径同形状。需要真实形状的用例显式覆写本桩。
    """

    monkeypatch.setattr(
        opportunities,
        "_compute_expiry_availability_moomoo",
        lambda symbol, *, max_dte: None,
    )


def _stub_expiry_availability(
    monkeypatch, expiries_by_symbol: dict[str, list[tuple[str, int]] | None]
) -> list[str]:
    """Replace the per-symbol expiry-metadata read with a call-recording stub."""

    calls: list[str] = []

    def compute(symbol: str, *, max_dte: int):
        calls.append(symbol)
        rows = expiries_by_symbol.get(symbol, None)
        if rows is None:
            return None
        return SimpleNamespace(
            symbol=symbol,
            market_date="2026-08-04",
            max_dte=max_dte,
            expiries=tuple(rows),
            fetched_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(
        opportunities, "_compute_expiry_availability_moomoo", compute
    )
    return calls


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(opportunities.router, prefix="/api/v1/opportunities")
    return TestClient(app)


def _watchlist(monkeypatch, symbols: list[str], *, deep_lane_max: int = 12) -> None:
    monkeypatch.setattr(
        opportunities, "_configured_intraday_watchlist", lambda: list(symbols)
    )
    monkeypatch.setattr(
        opportunities, "_configured_deep_lane_max", lambda: deep_lane_max
    )


def test_unset_watchlist_keeps_stock_list_path_identical(monkeypatch):
    """清单未配置：空 symbols 仍回退 STOCK_LIST，响应无任何两层字段。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, [])
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["NVDA"])
    quote_batches: list[tuple[str, ...]] = []

    def quotes(symbols):
        quote_batches.append(tuple(symbols))
        return {"NVDA": _quote()}

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {"NVDA": []})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    body = response.json()
    # 单层路径逐字节回归：universe=STOCK_LIST、快照批次不变、无两层字段。
    assert body["universe"] == ["NVDA"]
    assert body["universe_scan"] is None
    assert quote_batches == [("NVDA", "SPY")]
    item = body["candidates"][0]
    assert item["scan_tier"] is None
    assert item["deep_lane_reason"] is None


def test_explicit_symbols_bypass_two_tier_even_with_watchlist(monkeypatch):
    """显式 symbols（客户端覆写）永远走单层路径，清单配置不改变其行为。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)

    def forbidden():
        raise AssertionError("watchlist must not be consulted for explicit symbols")

    monkeypatch.setattr(
        opportunities, "_configured_intraday_watchlist", forbidden
    )
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": []})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": ["NVDA"]}
    )
    assert response.status_code == 200
    assert response.json()["universe_scan"] is None


def test_two_tier_single_snapshot_and_movers_gate_ranking(monkeypatch):
    """一次批量快照覆盖全清单；闸门按 |涨跌幅| 主序、成交额次序晋升前 K 档。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    # AAA/DDD 同 |涨跌| 1%：AAA 成交额更大排前；EEE 快照未解析（诚实标缺）。
    _watchlist(monkeypatch, ["AAA", "BBB", "CCC", "DDD", "EEE"], deep_lane_max=2)
    quote_batches: list[tuple[str, ...]] = []

    def quotes(symbols):
        quote_batches.append(tuple(symbols))
        return {
            "AAA": _quote("AAA", last_price=101.0, prev_close_price=100.0, turnover=9_000_000.0),
            "BBB": _quote("BBB", last_price=105.0, prev_close_price=100.0, turnover=1_000_000.0),
            "CCC": _quote("CCC", last_price=94.0, prev_close_price=100.0, turnover=2_000_000.0),
            "DDD": _quote("DDD", last_price=101.0, prev_close_price=100.0, turnover=500_000.0),
            "SPY": _quote("SPY", last_price=500.0, prev_close_price=490.0, volume=1_000, turnover=499_000.0),
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})
    earnings_calls = _stub_earnings(monkeypatch, [])
    five_minute_calls: list[str] = []

    def counted_bars(symbol: str):
        five_minute_calls.append(symbol)
        return _five_minute_bars(symbol)

    monkeypatch.setattr(opportunities, "_fetch_intraday_5m_bars", counted_bars)

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    body = response.json()

    # 一轮只有一次批量快照：全清单 + SPY 并入同一批。
    assert quote_batches == [("AAA", "BBB", "CCC", "DDD", "EEE", "SPY")]
    # 财报仍是一次区间调用覆盖整个 universe。
    assert len(earnings_calls) == 1

    # universe=宽层实际扫描的全部标的；候选=深度层（|−6%| CCC > |+5%| BBB）。
    assert body["universe"] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert sorted(item["ticker"] for item in body["candidates"]) == ["BBB", "CCC"]
    scan = body["universe_scan"]
    assert scan["mode"] == "watchlist_two_tier"
    assert scan["gate_basis"] == "abs_change_percent_then_turnover_v1"
    assert scan["watchlist_total"] == 5
    assert scan["watchlist_truncated"] is False
    assert scan["scanned_total"] == 5
    assert scan["deep_lane_count"] == 2
    assert scan["deep_lane_max"] == 2
    assert scan["deep_lane"] == [
        {"ticker": "CCC", "promoted_by": "mover_rank", "mover_rank": 1},
        {"ticker": "BBB", "promoted_by": "mover_rank", "mover_rank": 2},
    ]
    assert scan["plan_always_include"] == []
    assert scan["gated_out_count"] == 3
    assert scan["snapshot_unresolved_symbols"] == ["EEE"]
    assert scan["day_promotion_cap_reached"] is False

    # 宽层行按 |涨跌| 降序、标缺行最后；同 |涨跌| 按成交额 AAA > DDD。
    snapshot_only = scan["snapshot_only"]
    assert [row["ticker"] for row in snapshot_only] == ["AAA", "DDD", "EEE"]
    aaa = snapshot_only[0]
    assert aaa["state"] == "ready"
    assert aaa["change_percent"] == pytest.approx(1.0, abs=1e-6)
    assert aaa["turnover"] == pytest.approx(9_000_000.0)
    # 诚实合同：宽层行绝不携带深度层字段（缺席，而不是 null 占位）。
    for row in snapshot_only:
        for forbidden_key in (
            "session_bursts",
            "setup_match",
            "market_alignment",
            "earnings_proximity",
            "option_activity",
            "supporting_evidence_count",
        ):
            assert forbidden_key not in row
    eee = snapshot_only[-1]
    assert eee["state"] == "unavailable"
    assert eee["unavailable_reason"] == "snapshot_missing"

    # 深度层管线只对晋升标的执行（5m K 线只取 2 档）。
    assert sorted(five_minute_calls) == ["BBB", "CCC"]
    for item in body["candidates"]:
        assert item["scan_tier"] == "deep"
        assert item["deep_lane_reason"]["promoted_by"] == "mover_rank"
        assert item["deep_lane_reason"]["basis"] == "abs_change_percent_then_turnover_v1"
    ranks = {
        item["ticker"]: item["deep_lane_reason"]["mover_rank"]
        for item in body["candidates"]
    }
    assert ranks == {"CCC": 1, "BBB": 2}
    assert any("两层扫描" in text for text in body["limitations"])


def test_two_tier_plan_tickers_always_included_and_deduped(monkeypatch):
    """计划钉选始终占深度位、不占 K 名额；计划内标的不重复参与异动排名。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, ["AAA", "BBB", "CCC"], deep_lane_max=1)
    # PPP 不在清单：计划钉选仍须并入同一批快照并进入深度层。
    monkeypatch.setattr(
        opportunities, "_todays_plan_tickers", lambda market_date_et: ["PPP", "BBB"]
    )
    quote_batches: list[tuple[str, ...]] = []

    def quotes(symbols):
        quote_batches.append(tuple(symbols))
        return {
            "AAA": _quote("AAA", last_price=101.0, prev_close_price=100.0, turnover=100.0),
            "BBB": _quote("BBB", last_price=110.0, prev_close_price=100.0, turnover=200.0),
            "CCC": _quote("CCC", last_price=103.0, prev_close_price=100.0, turnover=300.0),
            "PPP": _quote("PPP", last_price=50.0, prev_close_price=50.0, turnover=10.0),
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    scan = response.json()["universe_scan"]
    # 计划钉选并入同一批快照（仍是一次请求）。
    assert quote_batches == [("AAA", "BBB", "CCC", "PPP", "SPY")]
    # 深度层 = 计划（PPP、BBB）+ 异动 Top1（CCC，BBB 已被计划钉选去重）。
    assert scan["deep_lane"] == [
        {"ticker": "PPP", "promoted_by": "plan_always_include", "mover_rank": None},
        {"ticker": "BBB", "promoted_by": "plan_always_include", "mover_rank": None},
        {"ticker": "CCC", "promoted_by": "mover_rank", "mover_rank": 1},
    ]
    # K 只约束异动名额：deep_lane_count(3) > deep_lane_max(1) 是合同内行为。
    assert scan["deep_lane_count"] == 3
    assert scan["deep_lane_max"] == 1
    assert scan["plan_always_include"] == ["PPP", "BBB"]
    by_ticker = {
        item["ticker"]: item for item in response.json()["candidates"]
    }
    assert by_ticker["PPP"]["deep_lane_reason"]["promoted_by"] == "plan_always_include"
    assert by_ticker["CCC"]["deep_lane_reason"]["mover_rank"] == 1


def test_two_tier_day_promotion_cap_gates_new_symbols_honestly(monkeypatch):
    """每日新晋升去重标的上限（额度护栏）：触顶后新标的只留宽层并显式标注。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, ["AAA", "BBB"], deep_lane_max=5)
    monkeypatch.setattr(opportunities, "_INTRADAY_DEEP_DAILY_DISTINCT_CAP", 1)
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {
            "AAA": _quote("AAA", last_price=110.0, prev_close_price=100.0, turnover=100.0),
            "BBB": _quote("BBB", last_price=105.0, prev_close_price=100.0, turnover=100.0),
        },
    )
    _stub_events(monkeypatch, {})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    scan = response.json()["universe_scan"]
    assert [entry["ticker"] for entry in scan["deep_lane"]] == ["AAA"]
    assert scan["day_promotion_cap"] == 1
    assert scan["day_promotion_cap_reached"] is True
    assert [row["ticker"] for row in scan["snapshot_only"]] == ["BBB"]


def test_two_tier_disabled_moomoo_keeps_wide_rows_and_plan_lane_honest(monkeypatch):
    """Moomoo 未启用：不发快照请求，宽层逐行显式 not_configured，深度层只剩计划。"""

    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, ["AAA", "BBB"])
    monkeypatch.setattr(
        opportunities, "_todays_plan_tickers", lambda market_date_et: ["AAA"]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("must not query Moomoo when disabled")

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", forbidden)
    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", forbidden)

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    body = response.json()
    scan = body["universe_scan"]
    assert scan["deep_lane"] == [
        {"ticker": "AAA", "promoted_by": "plan_always_include", "mover_rank": None}
    ]
    # 未启用 ≠ 快照未解析：unresolved 只描述「已请求但供应商无返回行」。
    assert scan["snapshot_unresolved_symbols"] == []
    assert [row["ticker"] for row in scan["snapshot_only"]] == ["BBB"]
    assert scan["snapshot_only"][0]["state"] == "unavailable"
    assert scan["snapshot_only"][0]["unavailable_reason"] == "moomoo_not_configured"
    # 深度候选按既有 fail-closed 合同：缺现价 = 数据不足，绝不以 0 冒充。
    item = body["candidates"][0]
    assert item["ticker"] == "AAA"
    assert item["research_state"] == "insufficient"
    assert item["last_price"] is None


def test_two_tier_watchlist_truncation_is_explicit(monkeypatch):
    """清单超出服务端上限：显式截断 + watchlist_truncated 标注，绝不无声丢弃。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    monkeypatch.setattr(opportunities, "_INTRADAY_WATCHLIST_MAX_SYMBOLS", 2)
    _watchlist(monkeypatch, ["AAA", "BBB", "CCC"])
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {},
    )
    _stub_events(monkeypatch, {})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    scan = response.json()["universe_scan"]
    assert scan["watchlist_total"] == 3
    assert scan["watchlist_truncated"] is True
    assert scan["scanned_total"] == 2


def test_configured_deep_lane_max_clamps_to_bounds(monkeypatch):
    """INTRADAY_DEEP_LANE_MAX 服务端兜底钳制 1..20，垃圾值回退默认 12。"""

    import src.config as config_module

    class _FakeConfig:
        def __init__(self, value):
            self.intraday_deep_lane_max = value

    for raw, expected in ((0, 1), (99, 20), (12, 12), ("garbage", 12), (None, 12)):
        monkeypatch.setattr(
            config_module, "get_config", lambda raw=raw: _FakeConfig(raw)
        )
        assert opportunities._configured_deep_lane_max() == expected


def test_premarket_gate_ranks_by_pre_fields_and_never_promotes_missing_rows(
    monkeypatch,
):
    """盘前时段 + 盘前字段可得：闸门按 |盘前涨跌|→盘前成交额 晋升。

    常规快照字段在盘前仍指向上一常规时段（EEE 上一时段 |+9%| 是全场最大），
    但盘前口径下 EEE 缺 pre_* 字段——绝不可被晋升；宽层剩余行也按盘前口径
    降序、缺盘前字段的行恒排最后。
    """

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch, now=_PREMARKET_NOW)
    _watchlist(monkeypatch, ["AAA", "BBB", "CCC", "DDD", "EEE"], deep_lane_max=2)

    def quotes(symbols):
        return {
            # 常规字段全部是上一常规时段读数；真实盘前变动只在 pre_*。
            # BBB 盘前 −4%（负值合法）| CCC/DDD 同 |2%|，CCC 盘前成交额更大。
            "AAA": _quote("AAA", last_price=101.0, prev_close_price=100.0, turnover=9_000_000.0, pre_change_rate=0.5, pre_turnover=100_000.0),
            "BBB": _quote("BBB", last_price=105.0, prev_close_price=100.0, turnover=1_000_000.0, pre_change_rate=-4.0, pre_turnover=2_000_000.0),
            "CCC": _quote("CCC", last_price=94.0, prev_close_price=100.0, turnover=2_000_000.0, pre_change_rate=2.0, pre_turnover=5_000_000.0),
            "DDD": _quote("DDD", last_price=101.0, prev_close_price=100.0, turnover=500_000.0, pre_change_rate=2.0, pre_turnover=1_000_000.0),
            # EEE：上一时段 |+9%| 全场最大，但无任何盘前字段。
            "EEE": _quote("EEE", last_price=109.0, prev_close_price=100.0, turnover=8_000_000.0),
            "SPY": _quote("SPY", last_price=500.0, prev_close_price=490.0, volume=1_000, turnover=499_000.0),
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})
    _stub_earnings(monkeypatch, [])

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    body = response.json()
    scan = body["universe_scan"]

    # 盘前口径显式声明，且无任何降级警示。
    assert scan["gate_basis"] == "premarket_pre_price_change_then_pre_turnover_v1"
    assert scan["gate_warnings"] == []
    # 晋升：BBB |−4%| 第一、CCC |2%|（盘前成交额 > DDD）第二；EEE 绝不晋升。
    assert scan["deep_lane"] == [
        {"ticker": "BBB", "promoted_by": "mover_rank", "mover_rank": 1},
        {"ticker": "CCC", "promoted_by": "mover_rank", "mover_rank": 2},
    ]
    for item in body["candidates"]:
        assert item["deep_lane_reason"]["basis"] == (
            "premarket_pre_price_change_then_pre_turnover_v1"
        )

    # 宽层剩余行按盘前口径降序：DDD |2%| > AAA |0.5%|；EEE 缺盘前字段恒最后。
    snapshot_only = scan["snapshot_only"]
    assert [row["ticker"] for row in snapshot_only] == ["DDD", "AAA", "EEE"]
    ddd = snapshot_only[0]
    assert ddd["pre_change_percent"] == pytest.approx(2.0)
    assert ddd["pre_turnover"] == pytest.approx(1_000_000.0)
    eee = snapshot_only[-1]
    assert eee["pre_change_percent"] is None
    assert eee["pre_turnover"] is None
    # 常规字段照旧透传（additive）：EEE 仍如实带上一时段涨跌。
    assert eee["change_percent"] == pytest.approx(9.0, abs=1e-6)


def test_premarket_gate_without_pre_fields_falls_back_with_explicit_warning(
    monkeypatch,
):
    """盘前时段但整批快照无盘前字段：显式回退常规口径 + 警示，绝不静默。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch, now=_PREMARKET_NOW)
    _watchlist(monkeypatch, ["AAA", "BBB", "CCC"], deep_lane_max=2)

    def quotes(symbols):
        return {
            "AAA": _quote("AAA", last_price=101.0, prev_close_price=100.0, turnover=9_000_000.0),
            "BBB": _quote("BBB", last_price=105.0, prev_close_price=100.0, turnover=1_000_000.0),
            "CCC": _quote("CCC", last_price=94.0, prev_close_price=100.0, turnover=2_000_000.0),
            "SPY": _quote("SPY", last_price=500.0, prev_close_price=490.0, volume=1_000, turnover=499_000.0),
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})
    _stub_earnings(monkeypatch, [])

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    scan = response.json()["universe_scan"]
    # 回退口径 + 显式警示：排序此时反映的是上一常规时段。
    assert scan["gate_basis"] == "abs_change_percent_then_turnover_v1"
    assert (
        "premarket_fields_unavailable_ranking_reflects_prior_session"
        in scan["gate_warnings"]
    )
    # 排名按常规口径：CCC |−6%| > BBB |+5%|。
    assert scan["deep_lane"] == [
        {"ticker": "CCC", "promoted_by": "mover_rank", "mover_rank": 1},
        {"ticker": "BBB", "promoted_by": "mover_rank", "mover_rank": 2},
    ]


def test_regular_session_gate_ignores_pre_fields_and_keeps_legacy_basis(
    monkeypatch,
):
    """常规时段：即便快照带盘前字段，闸门仍按常规口径（冷启动回退 v1）。

    v2 起常规时段冷启动（无动量历史）显式携带 warming-up 警示——绝不静默；
    盘前字段在常规时段依旧被无视。
    """

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)  # 默认 _FIXED_NOW = 10:30 ET 常规时段。
    _watchlist(monkeypatch, ["AAA", "BBB", "CCC"], deep_lane_max=2)

    def quotes(symbols):
        return {
            # AAA 带巨大盘前读数：常规时段必须被闸门无视。
            "AAA": _quote("AAA", last_price=101.0, prev_close_price=100.0, turnover=9_000_000.0, pre_change_rate=99.0, pre_turnover=9_000_000_000.0),
            "BBB": _quote("BBB", last_price=105.0, prev_close_price=100.0, turnover=1_000_000.0),
            "CCC": _quote("CCC", last_price=94.0, prev_close_price=100.0, turnover=2_000_000.0),
            "SPY": _quote("SPY", last_price=500.0, prev_close_price=490.0, volume=1_000, turnover=499_000.0),
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})
    _stub_earnings(monkeypatch, [])

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    body = response.json()
    scan = body["universe_scan"]
    assert scan["gate_basis"] == "abs_change_percent_then_turnover_v1"
    # 冷启动（无动量历史）＝显式警示；盘前警示绝不出现在常规时段。
    assert scan["gate_warnings"] == [
        "momentum_history_warming_up_ranking_by_day_change"
    ]
    # 常规口径排名：CCC |−6%| > BBB |+5%|；AAA 的盘前读数不参与。
    assert scan["deep_lane"] == [
        {"ticker": "CCC", "promoted_by": "mover_rank", "mover_rank": 1},
        {"ticker": "BBB", "promoted_by": "mover_rank", "mover_rank": 2},
    ]
    for item in body["candidates"]:
        assert item["deep_lane_reason"]["basis"] == (
            "abs_change_percent_then_turnover_v1"
        )
    # 宽层剩余行仍按常规 |涨跌| 排序（AAA 唯一剩余行，携带盘前字段只是透传）。
    assert [row["ticker"] for row in scan["snapshot_only"]] == ["AAA"]
    assert scan["snapshot_only"][0]["pre_change_percent"] == pytest.approx(99.0)


def test_two_tier_pinned_tickers_always_deep_deduped_and_off_quota(monkeypatch):
    """用户钉选（INTRADAY_PINNED_TICKERS）始终占深度位、不占 K 名额。

    钉选与计划钉选去重（计划优先标注）、钉选清单自身去重、清单外钉选并入
    同一批快照；K=1 仍能额外晋升 1 档异动标的（钉选不挤占异动名额）。
    """

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, ["AAA", "BBB"], deep_lane_max=1)
    # NVDA 不在清单（须并入同一批快照）；BBB 同时在清单；重复项须去重；
    # NVDA 同时是计划标的 → 计划钉选优先标注。
    monkeypatch.setattr(
        opportunities,
        "_configured_intraday_pinned_tickers",
        lambda: ["NVDA", "BBB", "NVDA"],
    )
    monkeypatch.setattr(
        opportunities, "_todays_plan_tickers", lambda market_date_et: ["PPP", "NVDA"]
    )
    quote_batches: list[tuple[str, ...]] = []

    def quotes(symbols):
        quote_batches.append(tuple(symbols))
        return {
            "AAA": _quote("AAA", last_price=101.0, prev_close_price=100.0, turnover=100.0),
            "BBB": _quote("BBB", last_price=105.0, prev_close_price=100.0, turnover=200.0),
            "NVDA": _quote("NVDA", last_price=100.2, prev_close_price=100.0, turnover=300.0),
            "PPP": _quote("PPP", last_price=50.0, prev_close_price=50.0, turnover=10.0),
        }

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    body = response.json()
    scan = body["universe_scan"]
    # 计划 + 钉选并入同一批快照（仍是一次请求）。
    assert quote_batches == [("AAA", "BBB", "PPP", "NVDA", "SPY")]
    # 深度层 = 计划（PPP、NVDA）+ 用户钉选（BBB，去重后）+ 异动 Top1（AAA）。
    assert scan["deep_lane"] == [
        {"ticker": "PPP", "promoted_by": "plan_always_include", "mover_rank": None},
        {"ticker": "NVDA", "promoted_by": "plan_always_include", "mover_rank": None},
        {"ticker": "BBB", "promoted_by": "user_pinned", "mover_rank": None},
        {"ticker": "AAA", "promoted_by": "mover_rank", "mover_rank": 1},
    ]
    # 钉选不占 K 名额：K=1 全额留给异动晋升（deep_lane_count > K 合同内）。
    assert scan["deep_lane_count"] == 4
    assert scan["deep_lane_max"] == 1
    assert scan["plan_always_include"] == ["PPP", "NVDA"]
    assert scan["user_pinned"] == ["BBB"]
    by_ticker = {item["ticker"]: item for item in body["candidates"]}
    assert by_ticker["BBB"]["deep_lane_reason"]["promoted_by"] == "user_pinned"
    assert by_ticker["BBB"]["deep_lane_reason"]["mover_rank"] is None
    assert by_ticker["NVDA"]["deep_lane_reason"]["promoted_by"] == (
        "plan_always_include"
    )
    assert by_ticker["AAA"]["deep_lane_reason"]["mover_rank"] == 1


def test_pinned_without_watchlist_keeps_single_tier_path_identical(monkeypatch):
    """清单未配置时钉选完全不生效：单层路径与现状逐字节一致。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, [])
    monkeypatch.setattr(
        opportunities,
        "_configured_intraday_pinned_tickers",
        lambda: ["NVDA", "TSLA"],
    )
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["NVDA"])
    quote_batches: list[tuple[str, ...]] = []

    def quotes(symbols):
        quote_batches.append(tuple(symbols))
        return {"NVDA": _quote()}

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {"NVDA": []})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["universe"] == ["NVDA"]
    assert body["universe_scan"] is None
    assert quote_batches == [("NVDA", "SPY")]
    assert body["candidates"][0]["scan_tier"] is None
    assert body["candidates"][0]["deep_lane_reason"] is None


def test_momentum_history_feed_and_mom15_window_math():
    """mom15 数学合同：12–18 分钟回看窗内取「最老」样本，窗外一律 None。"""

    t0 = 1_000_000.0
    feed = opportunities._feed_intraday_momentum_history
    mom15 = opportunities._intraday_mom15

    def rows(price):
        return [{"ticker": "AAA", "last_price": price}]

    feed(rows(100.0), market_date_et="2026-07-28", now_epoch=t0)
    # 冷启动：唯一样本不足 12 分钟 → None（不足龄 ≠ 0 动量）。
    assert mom15("AAA", 101.0, now_epoch=t0) is None
    assert mom15("AAA", 101.0, now_epoch=t0 + 11 * 60) is None
    # 12–18 分钟窗内可计算：(102/100 − 1)×100 = 2.0。
    assert mom15("AAA", 102.0, now_epoch=t0 + 13 * 60) == pytest.approx(2.0)
    # 多样本时取窗内「最老」样本：t0 样本（16 分钟）优先于 t0+2min 样本。
    feed(rows(110.0), market_date_et="2026-07-28", now_epoch=t0 + 120)
    assert mom15("AAA", 103.0, now_epoch=t0 + 16 * 60) == pytest.approx(3.0)
    # t0 样本老于 18 分钟被跳过，改用 t0+2min 样本（恰在窗上限）→ 基准 110。
    assert mom15("AAA", 110.0, now_epoch=t0 + 20 * 60) == pytest.approx(0.0)
    # 全部样本过老 → None（样本过期 ≠ 平静）。
    assert mom15("AAA", 110.0, now_epoch=t0 + 40 * 60) is None
    # 无历史 / 缺现价 / 非法现价 → None。
    assert mom15("BBB", 100.0, now_epoch=t0 + 13 * 60) is None
    assert mom15("AAA", None, now_epoch=t0 + 13 * 60) is None
    assert mom15("AAA", 0.0, now_epoch=t0 + 13 * 60) is None


def test_momentum_history_cleared_on_et_date_rollover():
    """ET 日期切换即整体清空：绝不跨日比价（隔夜跳空 ≠ 15 分钟动量）。"""

    t0 = 5_000.0
    feed = opportunities._feed_intraday_momentum_history
    mom15 = opportunities._intraday_mom15

    feed(
        [{"ticker": "AAA", "last_price": 100.0}],
        market_date_et="2026-07-28",
        now_epoch=t0,
    )
    feed(
        [{"ticker": "AAA", "last_price": 120.0}],
        market_date_et="2026-07-29",
        now_epoch=t0 + 13 * 60,
    )
    assert opportunities._intraday_momentum_history_date == "2026-07-29"
    assert list(opportunities._intraday_momentum_history["AAA"]) == [
        (t0 + 13 * 60, 120.0)
    ]
    # 未清空的话 t0 样本（13 分钟前，窗内）会给出 +30%；清空后无足龄样本。
    assert mom15("AAA", 130.0, now_epoch=t0 + 13 * 60) is None


def test_momentum_gate_two_sub_quotas_with_warmup_then_v2_ranking(monkeypatch):
    """闸门 v2 合同（2026-08-03 校准场景）：动量子额度让「正在动」的标的晋升。

    第一轮＝冷启动：显式回退 v1（|当日涨跌|）并携带 warming-up 警示，普涨
    跳空日的 GAP（+8% 后横盘）排第一。15 分钟后第二轮：mom15 可得 →
    gate_basis=momentum15m_then_day_change_v2，ceil(2K/3)=2 档动量位给
    NVDA（15 分钟 +2.48%）与 MOM（−1.99%），剩余 1 档当日涨跌位兜底 GAP
    （谁今天最大仍可见）；mom15=0 的横盘标的绝不占动量位。
    """

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, ["GAP", "NVDA", "MOM", "SLOW"], deep_lane_max=3)
    clock = {"now": _FIXED_NOW}
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: clock["now"])
    prices = {"GAP": 108.0, "NVDA": 101.0, "MOM": 100.5, "SLOW": 100.2}

    def quotes(symbols):
        result = {
            symbol: _quote(
                symbol,
                last_price=prices[symbol],
                prev_close_price=100.0,
                turnover=1_000_000.0,
            )
            for symbol in prices
        }
        result["SPY"] = _quote(
            "SPY", last_price=500.0, prev_close_price=490.0, volume=1_000, turnover=499_000.0
        )
        return result

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})
    _stub_earnings(monkeypatch, [])

    first = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert first.status_code == 200
    scan_first = first.json()["universe_scan"]
    # 冷启动：回退 v1 + 显式警示；|当日涨跌| 排名让跳空标的 GAP 占第一。
    assert scan_first["gate_basis"] == "abs_change_percent_then_turnover_v1"
    assert scan_first["gate_warnings"] == [
        "momentum_history_warming_up_ranking_by_day_change"
    ]
    assert scan_first["deep_lane"] == [
        {"ticker": "GAP", "promoted_by": "mover_rank", "mover_rank": 1},
        {"ticker": "NVDA", "promoted_by": "mover_rank", "mover_rank": 2},
        {"ticker": "MOM", "promoted_by": "mover_rank", "mover_rank": 3},
    ]

    # 15 分钟后：GAP/SLOW 横盘（mom15=0），NVDA +2.48%、MOM −1.99% 在动。
    clock["now"] = _FIXED_NOW + timedelta(minutes=15)
    prices.update({"NVDA": 103.5, "MOM": 98.5})
    second = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": [], "refresh": True},
    )
    assert second.status_code == 200
    body = second.json()
    scan_second = body["universe_scan"]
    assert scan_second["gate_basis"] == "momentum15m_then_day_change_v2"
    assert scan_second["gate_warnings"] == []
    # 动量位（2 档）：NVDA、MOM（|mom15| 降序）；当日涨跌位（1 档）：GAP。
    assert scan_second["deep_lane"] == [
        {"ticker": "NVDA", "promoted_by": "mover_rank", "mover_rank": 1},
        {"ticker": "MOM", "promoted_by": "mover_rank", "mover_rank": 2},
        {"ticker": "GAP", "promoted_by": "mover_rank", "mover_rank": 3},
    ]
    for item in body["candidates"]:
        assert item["deep_lane_reason"]["basis"] == (
            "momentum15m_then_day_change_v2"
        )
    # SLOW（横盘 + 涨跌最小）只保留宽层快照行。
    assert [row["ticker"] for row in scan_second["snapshot_only"]] == ["SLOW"]


def test_day_ledger_keeps_rotated_out_symbols_with_last_deep_payload(monkeypatch):
    """今日深扫账本：曾深扫标的被轮换出深度层后以最后一次深扫摘要保留。

    2026-08-03 实盘缺口：ORCL 09:40 记录强波段后被后续 movers 挤出可见
    深度层，波段整段消失。合同：账本行携带最后一次深扫的分级波段/形态/
    涨跌与 as-of，state=rotated_out；当前深度层标的绝不重复出现在账本。
    """

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, ["AAA", "BBB"], deep_lane_max=1)
    clock = {"now": _FIXED_NOW}
    monkeypatch.setattr(opportunities, "_intraday_now", lambda: clock["now"])
    prices = {"AAA": 110.0, "BBB": 101.0}

    def quotes(symbols):
        result = {
            symbol: _quote(
                symbol,
                last_price=prices[symbol],
                prev_close_price=100.0,
                turnover=1_000_000.0,
            )
            for symbol in prices
        }
        result["SPY"] = _quote(
            "SPY", last_price=500.0, prev_close_price=490.0, volume=1_000, turnover=499_000.0
        )
        return result

    monkeypatch.setattr(opportunities, "_fetch_underlying_session_quotes", quotes)
    _stub_events(monkeypatch, {})
    _stub_earnings(monkeypatch, [])

    first = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert first.status_code == 200
    body_first = first.json()
    scan_first = body_first["universe_scan"]
    assert [entry["ticker"] for entry in scan_first["deep_lane"]] == ["AAA"]
    # 首轮：深度层标的不入账本（账本只收「已轮换出」的标的）。
    assert scan_first["day_ledger"] == []
    assert scan_first["day_ledger_basis"] == (
        "in_process_since_service_start_resets_on_restart"
    )
    aaa_first = body_first["candidates"][0]
    assert aaa_first["ticker"] == "AAA"
    first_legs = aaa_first["session_bursts"]["legs"]
    assert first_legs, "fixture 会话必须产生分级波段供账本合同断言"
    first_matched = aaa_first["setup_match"]["matched_setups"]
    first_change = aaa_first["session_change_percent"]

    # 30 分钟后（动量样本已过期，回退 v1 排名）：BBB +9% 挤掉 AAA。
    clock["now"] = _FIXED_NOW + timedelta(minutes=30)
    prices.update({"AAA": 100.5, "BBB": 109.0})
    second = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": [], "refresh": True},
    )
    assert second.status_code == 200
    body_second = second.json()
    scan_second = body_second["universe_scan"]
    assert [entry["ticker"] for entry in scan_second["deep_lane"]] == ["BBB"]
    # 账本恰好一行：AAA 携带第一轮深扫的波段/形态/涨跌与 as-of，不被刷新。
    assert len(scan_second["day_ledger"]) == 1
    entry = scan_second["day_ledger"][0]
    assert entry["ticker"] == "AAA"
    assert entry["state"] == "rotated_out"
    assert entry["last_seen_at"] == _FIXED_NOW.isoformat()
    assert entry["session_bursts_legs"] == first_legs
    assert entry["setup_matched_setups"] == first_matched
    assert entry["last_change_percent"] == pytest.approx(first_change)
    # 当前深度层标的（BBB）绝不重复出现在账本。
    assert all(row["ticker"] != "BBB" for row in scan_second["day_ledger"])


# --- 今日车道可用性（V2-E）--------------------------------------------------


def _lane_quotes(symbols_by_price: dict[str, float]):
    def quotes(symbols):
        result = {
            symbol: _quote(
                symbol,
                last_price=price,
                prev_close_price=100.0,
                turnover=1_000_000.0,
            )
            for symbol, price in symbols_by_price.items()
        }
        result["SPY"] = _quote(
            "SPY",
            last_price=500.0,
            prev_close_price=490.0,
            volume=1_000,
            turnover=499_000.0,
        )
        return result

    return quotes


def _run_two_tier(monkeypatch, watchlist: list[str], prices: dict[str, float]):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, watchlist, deep_lane_max=len(watchlist))
    monkeypatch.setattr(
        opportunities, "_fetch_underlying_session_quotes", _lane_quotes(prices)
    )
    _stub_events(monkeypatch, {})
    _stub_earnings(monkeypatch, [])
    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    return response.json()


def test_lane_availability_overnight_only_on_a_no_zero_dte_day(monkeypatch):
    """2026-08-04（周二）真实形状：NVDA 1/3/6DTE、AAOI 仅 3DTE → 过夜日。

    这正是 V2-E 描述的场景：日内车道关闭，不得退而买 1-3DTE。
    """

    calls = _stub_expiry_availability(
        monkeypatch,
        {
            "NVDA": [("2026-08-05", 1), ("2026-08-07", 3), ("2026-08-10", 6)],
            "AAOI": [("2026-08-07", 3)],
        },
    )
    body = _run_two_tier(
        monkeypatch, ["NVDA", "AAOI"], {"NVDA": 103.0, "AAOI": 102.0}
    )

    lane = body["lane_availability"]
    assert lane["day_type"] == "overnight_only"
    assert lane["basis"] == "per_ticker_option_expiry_metadata_within_0_7_dte_v1"
    assert lane["checked_scope"] == "intraday_deep_lane_tickers"
    assert lane["zero_dte_tickers"] == []
    assert lane["readable_count"] == 2
    assert lane["unavailable_count"] == 0
    assert lane["max_dte"] == 7
    by_ticker = {item["ticker"]: item for item in lane["tickers"]}
    assert by_ticker["NVDA"]["available_dte_list"] == [1, 3, 6]
    assert by_ticker["NVDA"]["has_zero_dte"] is False
    assert by_ticker["AAOI"]["available_dte_list"] == [3]
    # 只对深度层标的读取，不向宽层扇出。
    assert sorted(calls) == ["AAOI", "NVDA"]


def test_lane_availability_intraday_available_lists_zero_dte_tickers(monkeypatch):
    _stub_expiry_availability(
        monkeypatch,
        {
            "NVDA": [("2026-08-04", 0), ("2026-08-07", 3)],
            "AAOI": [("2026-08-07", 3)],
        },
    )
    body = _run_two_tier(
        monkeypatch, ["NVDA", "AAOI"], {"NVDA": 103.0, "AAOI": 102.0}
    )

    lane = body["lane_availability"]
    assert lane["day_type"] == "intraday_available"
    assert lane["zero_dte_tickers"] == ["NVDA"]


def test_lane_availability_unknown_when_any_chain_unreadable(monkeypatch):
    """fail closed：没查到 0DTE 且有标的读不到 → unknown，不冒充过夜日。"""

    _stub_expiry_availability(
        monkeypatch,
        {"NVDA": [("2026-08-05", 1)], "AAOI": None},
    )
    body = _run_two_tier(
        monkeypatch, ["NVDA", "AAOI"], {"NVDA": 103.0, "AAOI": 102.0}
    )

    lane = body["lane_availability"]
    assert lane["day_type"] == "unknown"
    assert lane["unavailable_count"] == 1
    by_ticker = {item["ticker"]: item for item in lane["tickers"]}
    assert by_ticker["AAOI"]["has_zero_dte"] is None
    assert by_ticker["AAOI"]["unavailable_reason"] == (
        "option_expiry_metadata_unavailable"
    )


def test_lane_availability_moomoo_disabled_is_unknown_without_provider_reads(
    monkeypatch,
):
    monkeypatch.delenv("MOOMOO_OPEND_ENABLED", raising=False)
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, ["NVDA"], deep_lane_max=1)
    # Moomoo 关闭时闸门无排序输入；用计划钉选保证深度层非空。
    monkeypatch.setattr(
        opportunities, "_todays_plan_tickers", lambda market_date_et: ["NVDA"]
    )

    def forbidden(symbol, *, max_dte):
        raise AssertionError("disabled Moomoo must not trigger expiry reads")

    monkeypatch.setattr(
        opportunities, "_compute_expiry_availability_moomoo", forbidden
    )
    _stub_events(monkeypatch, {})
    _stub_earnings(monkeypatch, [])

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    lane = response.json()["lane_availability"]
    assert lane["day_type"] == "unknown"
    assert lane["tickers"][0]["unavailable_reason"] == "moomoo_opend_not_enabled"


def test_lane_availability_caches_per_symbol_across_polls(monkeypatch):
    """同一 ET 日内的第二轮轮询不再重复读取到期日元数据（额度护栏）。"""

    calls = _stub_expiry_availability(
        monkeypatch, {"NVDA": [("2026-08-05", 1)], "AAOI": [("2026-08-07", 3)]}
    )
    _run_two_tier(monkeypatch, ["NVDA", "AAOI"], {"NVDA": 103.0, "AAOI": 102.0})
    assert sorted(calls) == ["AAOI", "NVDA"]

    second = _client().post(
        "/api/v1/opportunities/intraday-top",
        json={"symbols": [], "refresh": True},
    )
    assert second.status_code == 200
    assert second.json()["lane_availability"]["day_type"] == "overnight_only"
    # 第二轮零新增供应商读取。
    assert sorted(calls) == ["AAOI", "NVDA"]


def test_lane_availability_defers_beyond_the_per_run_fetch_budget(monkeypatch):
    """单轮新增读取超预算的标的记为 deferred（unavailable），结论退回 unknown。"""

    monkeypatch.setattr(opportunities, "_LANE_AVAILABILITY_MAX_NEW_FETCHES", 1)
    calls = _stub_expiry_availability(
        monkeypatch, {"NVDA": [("2026-08-05", 1)], "AAOI": [("2026-08-07", 3)]}
    )
    body = _run_two_tier(
        monkeypatch, ["NVDA", "AAOI"], {"NVDA": 103.0, "AAOI": 102.0}
    )

    lane = body["lane_availability"]
    assert len(calls) == 1
    assert lane["day_type"] == "unknown"
    assert lane["deferred_tickers"]
    deferred = lane["deferred_tickers"][0]
    by_ticker = {item["ticker"]: item for item in lane["tickers"]}
    assert by_ticker[deferred]["unavailable_reason"] == (
        "deferred_provider_quota_budget"
    )


def test_single_tier_path_has_no_lane_availability_block(monkeypatch):
    """单层（现状）模式恒为 null——lane_availability 是两层模式的 additive 字段。"""

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    _stub_daily_loader(monkeypatch)
    _watchlist(monkeypatch, [])
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["NVDA"])
    monkeypatch.setattr(
        opportunities,
        "_fetch_underlying_session_quotes",
        lambda symbols: {"NVDA": _quote()},
    )
    _stub_events(monkeypatch, {"NVDA": []})

    response = _client().post(
        "/api/v1/opportunities/intraday-top", json={"symbols": []}
    )
    assert response.status_code == 200
    assert response.json()["lane_availability"] is None
