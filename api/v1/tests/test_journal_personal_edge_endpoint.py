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
