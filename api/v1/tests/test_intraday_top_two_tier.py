# -*- coding: utf-8 -*-
"""watchlist v1 两层扫描合同测试（INTRADAY_WATCHLIST → 宽层快照 + 异动闸门）。

回归锁定：未配置清单 / 显式 symbols 时行为与既有单层扫描逐字节一致
（universe_scan 恒为 null，候选无 scan_tier / deep_lane_reason）。两层模式下：
每轮仅 1 次批量快照；异动闸门按 |涨跌幅|→成交额 晋升前 K 档；当日冻结盘前
计划标的始终占深度位；宽层行只有快照字段，绝不虚构深度层读数。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.v1.endpoints import opportunities
from api.v1.tests.test_intraday_top_endpoint import (
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
    """常规时段：即便快照带盘前字段，闸门仍按既有常规口径，无警示。"""

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
    assert scan["gate_warnings"] == []
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
