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

    def _boom(_account_key):
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
