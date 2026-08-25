# -*- coding: utf-8 -*-
"""Deterministic tests for the 今日车道可用性（day-type / V2-E）pure module.

锁定三件事：
1. 逐标的可用性对「有 0DTE / 没有 0DTE / 链读不到」三种输入的三态输出；
2. 聚合 day_type 的优先级（正向证据 > 全可读无 0DTE > 未知），特别是
   **绝不**把「读不到」聚合成 overnight_only；
3. 不含任何星期规则——同一批到期日在任何日期下给出同一结论。
"""
from __future__ import annotations

from src.opportunities.lane_availability import (
    DAY_TYPE_BASIS,
    FORMULA_VERSION,
    LANE_AVAILABILITY_MAX_DTE,
    build_lane_availability,
    build_ticker_availability,
    derive_day_type,
)


def _aggregate(items, **overrides):
    kwargs = {
        "max_dte": LANE_AVAILABILITY_MAX_DTE,
        "market_date_et": "2026-08-04",
        "checked_scope": "intraday_deep_lane_tickers",
    }
    kwargs.update(overrides)
    return build_lane_availability(items, **kwargs)


def test_ticker_with_zero_dte_reports_has_zero_dte_true():
    """QQQ 口径：窗口内含 0DTE → has_zero_dte=True，DTE 列表去重升序。"""

    item = build_ticker_availability(
        "qqq",
        [("2026-08-04", 0), ("2026-08-06", 2), ("2026-08-05", 1)],
    )
    assert item["ticker"] == "QQQ"
    assert item["state"] == "ready"
    assert item["has_zero_dte"] is True
    assert item["available_dte_list"] == [0, 1, 2]
    assert item["expiries"][0] == {"expiry": "2026-08-04", "dte": 0}
    assert item["unavailable_reason"] is None


def test_ticker_without_zero_dte_reports_false_not_unknown():
    """NVDA 周二口径：1/3/6DTE 但无 0DTE → False（确证没有），不是未知。"""

    item = build_ticker_availability(
        "NVDA",
        [("2026-08-05", 1), ("2026-08-07", 3), ("2026-08-10", 6)],
    )
    assert item["state"] == "ready"
    assert item["has_zero_dte"] is False
    assert item["available_dte_list"] == [1, 3, 6]


def test_ticker_with_empty_expiries_is_honest_empty_not_failure():
    """窗口内一个到期日都没有：链可读 → ready + False，不是 unavailable。"""

    item = build_ticker_availability("AAOI", [])
    assert item["state"] == "ready"
    assert item["has_zero_dte"] is False
    assert item["available_dte_list"] == []


def test_chain_unavailable_yields_unknown_never_no_zero_dte():
    """链读不到：has_zero_dte 显式 None + 原因，绝不冒充「今天没有 0DTE」。"""

    item = build_ticker_availability(
        "MU", None, unavailable_reason="option_expiry_metadata_unavailable"
    )
    assert item["state"] == "unavailable"
    assert item["has_zero_dte"] is None
    assert item["available_dte_list"] == []
    assert item["unavailable_reason"] == "option_expiry_metadata_unavailable"


def test_expiries_outside_window_and_malformed_rows_are_dropped():
    """越界 DTE / 负 DTE / 缺字段行一律丢弃，不外推、不猜。"""

    item = build_ticker_availability(
        "TSLA",
        [
            ("2026-08-05", 1),
            ("2026-09-18", 45),  # 超出 max_dte
            ("2026-08-03", -1),  # 已过期
            ("", 0),  # 无到期日文本
            ("2026-08-07", "x"),  # DTE 不可解析
            {"expiry": "2026-08-07", "dte": 3},  # mapping 形状同样接受
        ],
    )
    assert item["available_dte_list"] == [1, 3]


def test_mapping_rows_from_near_expiry_groups_are_accepted():
    """临期合约面板的分组载荷（含额外键）可直接喂入，零转换。"""

    item = build_ticker_availability(
        "AAPL",
        [
            {"expiry": "2026-08-05", "dte": 1, "state": "ready", "contracts": []},
            {"expiry": "2026-08-05", "dte": 1},  # 重复行去重
        ],
    )
    assert item["available_dte_list"] == [1]
    assert item["expiries"] == [{"expiry": "2026-08-05", "dte": 1}]


def test_day_type_intraday_available_when_any_ticker_has_zero_dte():
    day_type, reason, zero = derive_day_type(
        [
            build_ticker_availability("NVDA", [("2026-08-05", 1)]),
            build_ticker_availability("QQQ", [("2026-08-04", 0)]),
        ]
    )
    assert day_type == "intraday_available"
    assert zero == ["QQQ"]
    assert "QQQ" in reason


def test_day_type_intraday_available_survives_partial_unknowns():
    """正向证据不因别的标的读不到而作废（读不到只会再增加 0DTE）。"""

    day_type, _reason, zero = derive_day_type(
        [
            build_ticker_availability("QQQ", [("2026-08-04", 0)]),
            build_ticker_availability("MU", None),
        ]
    )
    assert day_type == "intraday_available"
    assert zero == ["QQQ"]


def test_day_type_overnight_only_when_all_readable_and_no_zero_dte():
    """2026-08-04（周二）真实形状：全部可读、全部无 0DTE → 过夜日。"""

    day_type, reason, zero = derive_day_type(
        [
            build_ticker_availability(
                "NVDA", [("2026-08-05", 1), ("2026-08-07", 3), ("2026-08-10", 6)]
            ),
            build_ticker_availability("AAOI", [("2026-08-07", 3)]),
        ]
    )
    assert day_type == "overnight_only"
    assert zero == []
    assert "V2-E" in reason


def test_day_type_unknown_when_no_zero_dte_and_any_chain_unreadable():
    """关键 fail-closed：一个读不到就不敢说过夜日——未知≠「今天没有 0DTE」。"""

    day_type, reason, zero = derive_day_type(
        [
            build_ticker_availability("NVDA", [("2026-08-05", 1)]),
            build_ticker_availability("MU", None),
        ]
    )
    assert day_type == "unknown"
    assert zero == []
    assert "MU" in reason


def test_day_type_unknown_when_all_chains_unreadable():
    day_type, _reason, _zero = derive_day_type(
        [
            build_ticker_availability("NVDA", None),
            build_ticker_availability("MU", None),
        ]
    )
    assert day_type == "unknown"


def test_day_type_unknown_when_no_tickers_checked():
    day_type, reason, _zero = derive_day_type([])
    assert day_type == "unknown"
    assert "无法判定" in reason


def test_aggregate_payload_shape_and_counts():
    payload = _aggregate(
        [
            build_ticker_availability("QQQ", [("2026-08-04", 0)]),
            build_ticker_availability("NVDA", [("2026-08-05", 1)]),
            build_ticker_availability("MU", None),
        ],
        skipped_tickers=["hood"],
    )
    assert payload["formula_version"] == FORMULA_VERSION
    assert payload["basis"] == DAY_TYPE_BASIS
    assert payload["market_date_et"] == "2026-08-04"
    assert payload["max_dte"] == LANE_AVAILABILITY_MAX_DTE
    assert payload["day_type"] == "intraday_available"
    assert payload["checked_count"] == 3
    assert payload["readable_count"] == 2
    assert payload["unavailable_count"] == 1
    assert payload["zero_dte_tickers"] == ["QQQ"]
    assert payload["deferred_tickers"] == ["HOOD"]
    assert [item["ticker"] for item in payload["tickers"]] == ["QQQ", "NVDA", "MU"]
    assert payload["limitations"]


def test_aggregate_is_weekday_independent():
    """同一批到期日在任何 ET 日期下结论一致——本模块不含星期规则。"""

    items = [build_ticker_availability("NVDA", [("2026-08-05", 1)])]
    tuesday = _aggregate(items, market_date_et="2026-08-04")
    monday = _aggregate(items, market_date_et="2026-08-03")
    assert tuesday["day_type"] == monday["day_type"] == "overnight_only"
