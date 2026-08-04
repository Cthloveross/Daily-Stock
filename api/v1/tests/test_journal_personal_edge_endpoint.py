# -*- coding: utf-8 -*-
"""API contract for GET /journal/v2/personal-edge (个人画像回灌)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.journal.ledger.episode_repository import EpisodeRepositoryError


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PATH", str(tmp_path / "journal_personal_edge_api.db")
    )
    import src.config as config_mod
    import src.storage as storage
    from api.v1.endpoints import journal_reviews

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    journal_reviews._reset_personal_edge_cache()
    yield
    journal_reviews._reset_personal_edge_cache()
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from api.v1.endpoints import journal_reviews

    app = FastAPI()
    app.include_router(journal_reviews.router, prefix="/api/v1/journal")
    return TestClient(app)


URL = "/api/v1/journal/v2/personal-edge"


def _seed():
    from src.journal.tests.test_personal_edge import _seed_build

    return _seed_build(
        [
            {
                "underlying": "AAA",
                "opened_at": datetime(2026, 4, day, 14, 0, tzinfo=timezone.utc),
                "hold_seconds": 4_000,
                "realized_pnl_net": Decimal("100") if day % 2 else Decimal("-40"),
                "total_fee": Decimal("1"),
                "dte_at_entry": 1,
            }
            for day in range(1, 6)
        ]
    )


def test_not_built_state_is_honest():
    client = _client()
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "journal-personal-edge/1.0"
    assert body["data_state"] == "not_built"
    assert body["account_key"] == "default_moomoo_us"
    assert body["build_id"] is None
    assert body["underlyings"] == []
    assert body["monthly"] == []


def test_ready_contract_recomputes_from_default_build():
    build_id = _seed()
    client = _client()
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["data_state"] == "ready"
    assert body["build_id"] == build_id
    assert body["source_kind"] == "csv_batch"
    assert body["computed_at"] is not None
    assert body["first_opened_at"].startswith("2026-04-01")
    assert body["last_closed_at"] is not None
    assert body["closed_episode_count"] == 5
    assert body["underlying_min_episode_count"] == 5

    assert body["underlyings"] == [
        {
            "underlying": "AAA",
            "n": 5,
            "net": 220.0,
            "win_rate": 0.6,
            "fees": 5.0,
        }
    ]
    hold = {item["bucket"]: item for item in body["hold_time_buckets"]}
    assert hold["1-3h"]["n"] == 5
    assert hold["1-3h"]["net"] == 220.0
    assert hold["1-3h"]["avg_win"] == 100.0
    assert hold["1-3h"]["avg_loss"] == -40.0
    assert hold["<10m"]["n"] == 0
    assert hold["<10m"]["win_rate"] is None

    dte = {item["bucket"]: item for item in body["dte_buckets"]}
    assert dte["1-3"]["n"] == 5
    assert body["dte_unknown"]["n"] == 0
    assert body["monthly"] == [
        {
            "month": "2026-04",
            "n": 5,
            "net": 220.0,
            "fees": 5.0,
            "win_rate": 0.6,
        }
    ]
    assert body["month_basis"] == "opened_at_utc_minus_4_approximation"
    assert any("内生性" in line for line in body["limitations"])
    assert any("UTC−4" in line for line in body["limitations"])


def test_discipline_block_is_additive_and_fails_closed():
    """规模与频率是追加字段：既有字段一字不改，缺分母的比率 null + 原因。"""
    _seed()
    client = _client()
    body = client.get(URL).json()

    # 既有合同原样保留（additive-only）。
    assert body["schema_version"] == "journal-personal-edge/1.0"
    assert body["closed_episode_count"] == 5
    assert body["monthly"] == [
        {"month": "2026-04", "n": 5, "net": 220.0, "fees": 5.0, "win_rate": 0.6}
    ]
    assert body["underlyings"][0]["underlying"] == "AAA"
    assert body["month_basis"] == "opened_at_utc_minus_4_approximation"

    discipline = body["discipline"]
    assert discipline["body_trim_count"] == 5
    assert discipline["body_min_episode_count"] == 15
    row = discipline["monthly"][0]
    assert row["month"] == "2026-04"
    assert row["n"] == 5
    assert row["trading_day_count"] == 5
    assert row["trades_per_day"] == 1.0
    # 该 fixture 不带 opening_cash_flow：仓位与每美元回报显式缺席，绝不以 0 冒充。
    assert row["median_premium_at_risk"] is None
    assert row["total_premium_at_risk"] is None
    assert row["premium_reason"] is not None
    assert row["pnl_per_dollar_risked"] is None
    assert row["pnl_per_dollar_risked_reason"] is not None
    assert row["body_pnl"] is None
    assert "15" in row["body_pnl_reason"]
    assert row["exact_fill_share"] == 1.0
    assert row["has_reconstructed_fills"] is False


def test_discipline_window_reports_size_and_frequency():
    from src.journal.tests.test_personal_edge import _seed_build

    _seed_build(
        [
            {
                "underlying": "AAA",
                "opened_at": datetime(2026, 7, day, 14, 0, tzinfo=timezone.utc),
                "hold_seconds": 1_200,
                "realized_pnl_net": Decimal("100") if day % 2 else Decimal("-60"),
                "total_fee": Decimal("1"),
                "dte_at_entry": 0 if day % 2 else 3,
                "opening_cash_flow": Decimal("-2000"),
                "has_exact_fill_times": day != 1,
            }
            for day in range(1, 5)
        ]
    )
    client = _client()
    body = client.get(URL).json()
    window = body["discipline"]["current_window"]

    assert window["requested_trading_days"] == 20
    assert window["start_date"] == "2026-07-01"
    assert window["end_date"] == "2026-07-04"
    assert window["n"] == 4
    assert window["trading_day_count"] == 4
    assert window["trades_per_day"] == 1.0
    assert window["median_premium_at_risk"] == 2000.0
    assert window["total_premium_at_risk"] == 8000.0
    # (100 - 60 + 100 - 60) / 8000 = 0.01
    assert window["pnl_per_dollar_risked"] == 0.01
    assert window["median_episode_pnl"] == 20.0
    assert window["zero_dte_share"] == 0.5
    # 4 笔里 1 笔 has_exact_fill_times=0 → 0.75，重建标志保留供 UI 连续性使用。
    assert window["exact_fill_share"] == 0.75
    assert window["has_reconstructed_fills"] is True
    # 口径可比性由 fill_detailed_share 判定，与 exact_fill_share 是两件事。
    assert "fill_detailed_share" in window
    assert "basis_break" in window
    assert any("fill_allocations" in line for line in body["limitations"])
    assert any("audit_only_not_execution_cash_flow" in line for line in body["limitations"])


def test_ten_minute_cache_serves_and_reset_clears():
    _seed()
    client = _client()
    first = client.get(URL)
    assert first.status_code == 200

    from api.v1.endpoints import journal_reviews

    def _boom(_account_key, **_kwargs):
        raise EpisodeRepositoryError("must not recompute inside the TTL")

    original = journal_reviews.get_personal_edge_stats
    journal_reviews.get_personal_edge_stats = _boom
    try:
        cached = client.get(URL)
        assert cached.status_code == 200
        assert cached.json() == first.json()

        journal_reviews._reset_personal_edge_cache()
        recomputed = client.get(URL)
        assert recomputed.status_code == 422
    finally:
        journal_reviews.get_personal_edge_stats = original


def test_invalid_account_key_is_rejected():
    client = _client()
    assert client.get(URL, params={"account_key": "bad key"}).status_code == 422


def _compliance_seed():
    """Clean-basis rows covering每一条车道 + 一笔仍未平仓的过夜持仓。"""
    from src.journal.tests.test_personal_edge import _seed_build

    def spec(day, hour, pnl, dte, hold=1_200, **extra):
        base = {
            "underlying": "AAA",
            "opened_at": datetime(2026, 5, day, hour, 0, tzinfo=timezone.utc),
            "hold_seconds": hold,
            "realized_pnl_net": Decimal(pnl),
            "total_fee": Decimal("1"),
            "dte_at_entry": dte,
            "opening_cash_flow": Decimal("1000"),
            "evidence_summary_json": (
                '{"allocation_count":3,"fill_allocations":3,"order_allocations":0}'
            ),
        }
        base.update(extra)
        return base

    return _seed_build(
        [
            spec(4, 14, "100", 0),          # ET 10:00 → 日内合规
            spec(5, 17, "-30", 0),          # ET 13:00 → 午后 0DTE 违规
            spec(6, 14, "300", 4, 90_000),  # 4DTE 跨日 → 过夜合规
            spec(7, 14, "-20", 2),          # 1-3DTE → 违规
            spec(8, 14, "-50", 5),          # 4-7DTE 当日平 → 违规
            spec(11, 14, "10", 30, 90_000),  # ≥8DTE 隔夜 → 规则未覆盖
            {
                "underlying": "AAA",
                "opened_at": datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
                "lifecycle_status": "open",
                "realized_pnl_net": None,
                "dte_at_entry": 5,
            },
        ]
    )


def test_rule_compliance_block_is_additive_and_keeps_existing_fields():
    """车道遵守度是追加字段：既有合同一字不改。"""
    _seed()
    client = _client()
    body = client.get(URL).json()

    # 既有合同原样保留（additive-only）。
    assert body["schema_version"] == "journal-personal-edge/1.0"
    assert body["data_state"] == "ready"
    assert body["closed_episode_count"] == 5
    assert body["monthly"] == [
        {"month": "2026-04", "n": 5, "net": 220.0, "fees": 5.0, "win_rate": 0.6}
    ]
    assert body["underlyings"][0]["underlying"] == "AAA"
    assert body["month_basis"] == "opened_at_utc_minus_4_approximation"
    assert body["discipline"]["body_trim_count"] == 5
    assert body["discipline"]["body_min_episode_count"] == 15

    compliance = body["rule_compliance"]
    assert compliance["rule_set_id"] == "v2"
    assert compliance["adopted_at"] == "2026-08-05"
    assert compliance["clean_basis_start"] == "2026-04-21"
    assert compliance["exclude_top_n"] == 5
    assert compliance["exclude_top_n_min_episode_count"] == 15
    assert compliance["intraday_lane_dte"] == 0
    assert compliance["intraday_lane_et_cutoff_hour"] == 12
    assert compliance["overnight_lane_min_dte"] == 4
    assert compliance["overnight_lane_max_dte"] == 7
    assert compliance["overnight_lane_weak_entry_et_hours"] == [11, 13]
    assert compliance["daily_budget"]["intraday_ticket_limit"] == 6
    assert compliance["daily_budget"]["overnight_concurrent_limit"] == 3
    # 判定与规则编号随每一条车道行下发（不发以车道 id 为键的字典：Web 层的深层
    # camelCase 会改写字典键，规则 id 必须只以「值」的形式过网）。
    lanes = {lane["key"]: lane for lane in compliance["all_history"]["lanes"]}
    assert lanes["overnight_4_7"]["verdict"] == "compliant"
    assert lanes["late_0dte"]["verdict"] == "violation"
    assert lanes["late_0dte"]["rule_id"] == "V2-C③"
    assert "lane_verdicts" not in compliance
    assert "lane_rule_ids" not in compliance
    assert any("不构成建议" in line for line in compliance["limitations"])
    assert any("只读，不下单" in line for line in compliance["limitations"])


def test_rule_compliance_lane_and_verdict_payload():
    _compliance_seed()
    client = _client()
    compliance = client.get(URL).json()["rule_compliance"]

    assert compliance["population_n"] == 6
    history = compliance["all_history"]
    assert history["state"] == "ready"
    assert history["start_date"] == "2026-04-21"
    lanes = {lane["key"]: lane for lane in history["lanes"]}
    assert lanes["intraday_0dte"]["n"] == 1
    assert lanes["intraday_0dte"]["verdict"] == "compliant"
    assert lanes["intraday_0dte"]["rule_id"] == "V2-A"
    assert lanes["late_0dte"]["n"] == 1
    assert lanes["overnight_4_7"]["n"] == 1
    assert lanes["dte_1_3"]["n"] == 1
    assert lanes["bought_time_unused"]["n"] == 1
    assert lanes["other"]["n"] == 1
    assert lanes["unknown"]["n"] == 0
    # 空桶不是 0%：比率缺席 + 原因。
    assert lanes["unknown"]["gross_pct"] is None
    assert lanes["unknown"]["ratio_reason"] is not None

    verdicts = {item["key"]: item for item in history["verdicts"]}
    assert verdicts["compliant"]["n"] == 2
    # (100 + 300) + 2 费用 = 402，除以 2000。
    assert verdicts["compliant"]["gross_pct"] == pytest.approx(0.201)
    assert verdicts["violation"]["n"] == 3
    # (−30 − 20 − 50) + 3 = −97，除以 3000。
    assert verdicts["violation"]["gross_pct"] == pytest.approx(-0.032333)
    # 样本不足时剔尾读数缺席，绝不给截断样本的数字。
    assert verdicts["violation"]["gross_pct_excluding_top_n"] is None
    assert "15" in verdicts["violation"]["excluding_top_n_reason"]

    budget = compliance["daily_budget"]
    assert budget["as_of_trading_day"] == "2026-05-11"
    assert budget["overnight_open_count"] == 1


def test_rule_compliance_since_adoption_is_empty_and_says_so():
    _compliance_seed()
    client = _client()
    forward = client.get(URL).json()["rule_compliance"]["since_adoption"]
    assert forward["state"] == "no_episodes_since_adoption"
    assert forward["n"] == 0
    assert "2026-08-05" in forward["state_reason"]
    assert all(lane["n"] == 0 for lane in forward["lanes"])
    assert all(lane["gross_pct"] is None for lane in forward["lanes"])


def test_rule_compliance_since_query_moves_only_the_forward_slice():
    _compliance_seed()
    client = _client()
    body = client.get(URL, params={"since": "2026-05-07"}).json()
    compliance = body["rule_compliance"]
    assert compliance["adopted_at"] == "2026-05-07"
    # 全历史切片不受影响。
    assert compliance["all_history"]["n"] == 6
    forward = compliance["since_adoption"]
    assert forward["state"] == "ready"
    assert forward["start_date"] == "2026-05-07"
    # 5/7、5/8、5/11 三笔（ET 口径同日）。
    assert forward["n"] == 3


def test_rule_compliance_since_query_is_cached_per_cutoff():
    """不同 since 不得共用缓存条目（否则前向切片会串味）。"""
    _compliance_seed()
    client = _client()
    default_body = client.get(URL).json()
    moved_body = client.get(URL, params={"since": "2026-05-07"}).json()
    assert default_body["rule_compliance"]["since_adoption"]["n"] == 0
    assert moved_body["rule_compliance"]["since_adoption"]["n"] == 3
    # 再取一次默认值：仍是空前向切片，没有被上一次请求污染。
    assert client.get(URL).json()["rule_compliance"]["since_adoption"]["n"] == 0


def test_rule_compliance_rejects_a_malformed_since():
    _compliance_seed()
    client = _client()
    assert client.get(URL, params={"since": "20260805"}).status_code == 422
    assert client.get(URL, params={"since": "not-a-date"}).status_code == 422


def test_not_built_leaves_rule_compliance_absent_rather_than_zeroed():
    client = _client()
    body = client.get(URL).json()
    assert body["data_state"] == "not_built"
    assert body["rule_compliance"] is None
