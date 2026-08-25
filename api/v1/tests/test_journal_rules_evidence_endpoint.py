# -*- coding: utf-8 -*-
"""API contract for GET /journal/v2/rules-evidence（交易纪律证据页）。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PATH", str(tmp_path / "journal_rules_evidence_api.db")
    )
    import src.config as config_mod
    import src.storage as storage
    from api.v1.endpoints import journal_reviews

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    journal_reviews._reset_rules_evidence_cache()
    yield
    journal_reviews._reset_rules_evidence_cache()
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from api.v1.endpoints import journal_reviews

    app = FastAPI()
    app.include_router(journal_reviews.router, prefix="/api/v1/journal")
    return TestClient(app)


URL = "/api/v1/journal/v2/rules-evidence"


def _seed():
    """Two cheap-contract losers and two mid-priced winners, all clean basis."""

    from src.journal.tests.test_personal_edge import _seed_build

    return _seed_build(
        [
            {
                "underlying": "NVDA",
                # 2026-05-04 is a Monday; ET = UTC−4 → 10:00 ET
                "opened_at": datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc),
                "hold_seconds": 3_600,
                "realized_pnl_net": Decimal("-500"),
                "total_fee": Decimal("60"),
                "dte_at_entry": 0,
                "opening_cash_flow": Decimal("-1000"),
                "average_entry_price": Decimal("0.50"),
            },
            {
                "underlying": "NVDA",
                "opened_at": datetime(2026, 5, 4, 15, 0, tzinfo=timezone.utc),
                "hold_seconds": 3_600,
                "realized_pnl_net": Decimal("-300"),
                "total_fee": Decimal("40"),
                "dte_at_entry": 0,
                "opening_cash_flow": Decimal("-1000"),
                "average_entry_price": Decimal("0.80"),
            },
            {
                "underlying": "TSLA",
                "opened_at": datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
                "hold_seconds": 3_600,
                "realized_pnl_net": Decimal("200"),
                "total_fee": Decimal("12"),
                "dte_at_entry": 0,
                "opening_cash_flow": Decimal("-1000"),
                "average_entry_price": Decimal("3.00"),
            },
            {
                "underlying": "TSLA",
                # 4-7DTE held overnight (hold 2 days)
                "opened_at": datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
                "hold_seconds": 172_800,
                "realized_pnl_net": Decimal("400"),
                "total_fee": Decimal("10"),
                "dte_at_entry": 5,
                "opening_cash_flow": Decimal("-1000"),
                "average_entry_price": Decimal("5.00"),
            },
        ]
    )


def test_not_built_account_is_explicit_and_carries_no_fake_zeros():
    response = _client().get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["data_state"] == "not_built"
    assert body["sample_episode_count"] == 0
    assert body["price_bands"] == []
    assert body["position"] is None
    assert body["fee_threshold"] is None


def test_ready_response_carries_every_evidence_table():
    _seed()

    response = _client().get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "journal-rules-evidence/1.0"
    assert body["data_state"] == "ready"
    assert body["sample_episode_count"] == 4
    assert body["build_id"] >= 1
    assert body["first_trading_day"] == "2026-05-04"
    assert body["last_trading_day"] == "2026-05-06"
    # 每张表都在，前端不需要自己算任何一个数。
    assert len(body["price_bands"]) == 5
    assert len(body["dte_hold_lanes"]) == 4
    assert len(body["weekdays"]) == 5
    assert body["et_hours"]
    assert body["fee_threshold"]["fee_pct_of_premium"] is not None
    assert body["position"]["severity_tiers"]
    assert body["correlation"]["tickers"]


def test_banner_and_limitations_ship_verbatim():
    _seed()

    body = _client().get(URL).json()

    assert body["banner"] == (
        "这一页是你自己的历史统计，不是建议；规则由这段样本推出，"
        "前向验证见 /journal 规则遵守度"
    )
    joined = "\n".join(body["limitations"])
    assert "描述统计" in joined
    assert "单一 regime" in joined
    assert "样本内拟合" in joined


def test_price_bands_expose_the_fee_drag_on_cheap_contracts():
    _seed()

    bands = {row["label"]: row for row in _client().get(URL).json()["price_bands"]}

    # 两笔便宜合约：risk 2,000、净 −800、费用 100
    assert bands["<$1"]["n"] == 2
    assert bands["<$1"]["net_pct"] == pytest.approx(-40.0)
    assert bands["<$1"]["fee_pct"] == pytest.approx(5.0)
    assert bands["<$1"]["gross_pct"] == pytest.approx(-35.0)
    assert bands["$2-4"]["n"] == 1
    assert bands["$4-8"]["n"] == 1
    # 空档位显式给原因，不以 0 冒充。
    assert bands["$1-2"]["n"] == 0
    assert bands["$1-2"]["net_pct"] is None
    assert bands["$1-2"]["reason"] == "无样本"
    assert bands["<$1"]["lower"] is None and bands["<$1"]["upper"] == 1.0


def test_dte_hold_lanes_separate_same_day_from_overnight():
    _seed()

    lanes = {
        row["label"]: row
        for row in _client().get(URL).json()["dte_hold_lanes"]
    }

    assert lanes["0DTE 当日平"]["n"] == 3
    assert lanes["4-7DTE 过夜"]["n"] == 1
    assert lanes["4-7DTE 过夜"]["hold_style"] == "overnight"
    assert lanes["4-7DTE 过夜"]["gross_pct"] == pytest.approx(41.0)
    # 剔尾在样本不足时 fail closed 而不是给一个假数。
    assert lanes["4-7DTE 过夜"]["ex_top_n_gross_pct"] is None
    assert lanes["4-7DTE 过夜"]["ex_top_n_reason"]


def test_weekday_table_reports_zero_dte_availability():
    _seed()

    weekdays = {row["label"]: row for row in _client().get(URL).json()["weekdays"]}

    assert weekdays["周一"]["n"] == 2
    assert weekdays["周一"]["zero_dte_n"] == 2
    assert weekdays["周三"]["n"] == 2
    # 周二无样本 → 显式原因。
    assert weekdays["周二"]["n"] == 0
    assert weekdays["周二"]["reason"] == "无样本"


def test_position_block_echoes_chosen_params_and_never_recommends():
    _seed()

    body = _client().get(
        URL,
        params={
            "ticket_usd": 3000,
            "daily_breaker_usd": 6000,
            "max_concurrent": 2,
        },
    ).json()
    position = body["position"]

    assert position["framing"] == "你选择的参数 + 它们的含义"
    assert position["params"] == {
        "ticket_usd": 3000,
        "daily_breaker_usd": 6000,
        "max_concurrent": 2,
    }
    tiers = {tier["label"]: tier for tier in position["severity_tiers"]}
    # 归零档永远是 breaker / ticket，与样本无关。
    assert tiers["归零"]["tickets_to_breaker"] == pytest.approx(2.0)
    assert "累计回撤" in position["drawdown_caveat"]
    assert body["overnight_gap_note"]
    assert "保护不了过夜仓位" in body["overnight_gap_note"]


def test_chosen_params_change_the_arithmetic_but_not_the_sample():
    _seed()

    tight = _client().get(URL, params={"daily_breaker_usd": 3000}).json()
    wide = _client().get(URL, params={"daily_breaker_usd": 12000}).json()

    assert tight["sample_episode_count"] == wide["sample_episode_count"]
    tight_tiers = {t["label"]: t for t in tight["position"]["severity_tiers"]}
    wide_tiers = {t["label"]: t for t in wide["position"]["severity_tiers"]}
    assert tight_tiers["归零"]["tickets_to_breaker"] == pytest.approx(1.0)
    assert wide_tiers["归零"]["tickets_to_breaker"] == pytest.approx(4.0)


def test_correlation_block_counts_only_compliant_lanes():
    _seed()

    correlation = _client().get(URL).json()["correlation"]

    tickers = {row["ticker"]: row["n"] for row in correlation["tickers"]}
    # 三笔 0DTE 都在 ET 12:00 前开仓 → 合规日内车道；4-7DTE 过夜 → 合规过夜车道。
    assert tickers == {"NVDA": 2, "TSLA": 2}
    assert correlation["compliant_episode_count"] == 4
    assert "双倍仓位" in correlation["note"]


def test_unknown_build_id_is_a_422_not_a_silent_fallback():
    _seed()

    response = _client().get(URL, params={"build_id": 9_999})

    assert response.status_code == 422
    assert "9999" in response.json()["detail"]


def test_fee_calculator_anchors_travel_from_the_backend():
    _seed()

    fee = _client().get(URL).json()["fee_threshold"]

    # 前端不得硬编码任何一个：默认锚点与月度换算天数都由后端下发。
    assert fee["default_ticket_usd"] == 3000
    assert fee["default_tickets_per_day"] == 4
    assert fee["trading_days_per_month"] == 21
    assert "不落库" in fee["note"]


def test_missing_total_fee_is_reported_not_zero_filled():
    """缺 total_fee 的回合从毛口径/费率中排除并计数，绝不以 0 费用冒充。"""
    from src.journal.tests.test_personal_edge import _seed_build

    _seed_build(
        [
            {
                "underlying": "NVDA",
                "opened_at": datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc),
                "hold_seconds": 3_600,
                "realized_pnl_net": Decimal("-100"),
                "total_fee": Decimal("30"),
                "dte_at_entry": 0,
                "opening_cash_flow": Decimal("-1000"),
                "average_entry_price": Decimal("0.50"),
            },
            {
                "underlying": "NVDA",
                "opened_at": datetime(2026, 5, 4, 15, 0, tzinfo=timezone.utc),
                "hold_seconds": 3_600,
                "realized_pnl_net": Decimal("100"),
                "total_fee": None,
                "dte_at_entry": 0,
                "opening_cash_flow": Decimal("-1000"),
                "average_entry_price": Decimal("0.60"),
            },
        ]
    )

    body = _client().get(URL).json()

    assert body["sample_episode_count"] == 2
    assert body["fee_unknown_count"] == 1
    # 费率的样本量按「费用已知」子集报告。
    assert body["fee_threshold"]["n"] == 1
    assert body["fee_threshold"]["fee_pct_of_premium"] == pytest.approx(3.0)
    # 排除口径写进 limitations，读者不需要猜。
    assert any("total_fee" in line for line in body["limitations"])
    # 分档 n 计全部成员（净口径覆盖全部），毛口径只在费用已知子集上算：
    # (−100+30)/1000 = −7%，而不是 0 回填后的 (0+30)/2000。
    bands = {row["label"]: row for row in body["price_bands"]}
    assert bands["<$1"]["n"] == 2
    assert bands["<$1"]["net_pct"] == pytest.approx(0.0)
    assert bands["<$1"]["gross_pct"] == pytest.approx(-7.0)


def test_activating_a_newer_build_busts_the_rules_evidence_cache():
    """默认 build 解析进缓存键：换默认 build 后绝不再端出旧 build 的数字。"""
    from src.journal.tests.test_personal_edge import _seed_build

    first_build = _seed()
    client = _client()
    assert client.get(URL).json()["build_id"] == first_build

    second_build = _seed_build(
        [
            {
                "underlying": "AMD",
                "opened_at": datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc),
                "hold_seconds": 3_600,
                "realized_pnl_net": Decimal("50"),
                "total_fee": Decimal("5"),
                "dte_at_entry": 0,
                "opening_cash_flow": Decimal("-1000"),
                "average_entry_price": Decimal("2.50"),
            }
        ]
    )

    # 不清缓存、TTL 之内再取：必须立即读到新的默认 build。
    body = client.get(URL).json()
    assert body["build_id"] == second_build
    assert body["sample_episode_count"] == 1
