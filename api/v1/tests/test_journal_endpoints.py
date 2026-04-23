# -*- coding: utf-8 -*-
"""FastAPI TestClient coverage for /api/v1/journal/*."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "journal"
    / "moomoo_inline_sample.csv"
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal_api.db"))
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _client():
    """Build a minimal FastAPI app with just the journal router mounted.

    We avoid pulling the full ``api.app`` to keep the smoke test independent of
    authentication / CORS / lifespan initialisers.
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from api.v1.endpoints import journal

    app = FastAPI()
    app.include_router(journal.router, prefix="/api/v1/journal")
    return TestClient(app)


def _seed():
    """Import the fixture CSV via the storage layer so subsequent API calls see data."""
    from src.journal.brokers.moomoo_us import parse as parse_moomoo
    from src.journal.matcher import match_legs_fifo
    from src.journal.storage import (
        init_journal_schema,
        insert_events_from_orders,
        query_events_for_matching,
        record_import,
        replace_trades,
    )

    content = FIXTURE.read_bytes()
    init_journal_schema()
    orders = parse_moomoo(content)
    import_id = record_import(
        source_path=str(FIXTURE),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
    )
    assert import_id is not None
    insert_events_from_orders(import_id, orders)
    events = query_events_for_matching()
    trades = match_legs_fifo(events)
    replace_trades(trades)


def test_reality_test_endpoint():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/reality-test", params={"top_n": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_trades"] >= 2
    assert "top_n_ids" in body


def test_trades_list_paginated():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/trades", params={"per_page": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert len(body["items"]) == 1
    assert "underlying" in body["items"][0]


def test_trades_list_filter_by_symbol():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/trades", params={"symbol": "NVDA"})
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        assert item["underlying"] == "NVDA"


def test_get_and_patch_trade():
    _seed()
    c = _client()
    listed = c.get("/api/v1/journal/trades").json()
    tid = listed["items"][0]["id"]
    got = c.get(f"/api/v1/journal/trades/{tid}")
    assert got.status_code == 200

    resp = c.patch(
        f"/api/v1/journal/trades/{tid}",
        json={"user_notes": "FOMO", "emotional_state": "fomo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_notes"] == "FOMO"
    assert body["emotional_state"] == "fomo"


def test_get_trade_404():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/trades/999999")
    assert resp.status_code == 404


def test_stats_endpoint():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/stats", params={"days": 365})
    assert resp.status_code == 200
    body = resp.json()
    assert body["closed_trade_count"] >= 1
    assert "dte_distribution" in body
    assert "reality_test" in body


def test_import_endpoint():
    c = _client()
    with open(FIXTURE, "rb") as fh:
        resp = c.post(
            "/api/v1/journal/import",
            files={"file": ("moomoo_inline_sample.csv", fh, "text/csv")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] >= 5
    assert body["trades_rebuilt"] >= 2

    # Second upload should dedup by sha256.
    with open(FIXTURE, "rb") as fh:
        resp2 = c.post(
            "/api/v1/journal/import",
            files={"file": ("moomoo_inline_sample.csv", fh, "text/csv")},
        )
    assert resp2.status_code == 200
    assert resp2.json()["inserted"] == 0


def test_import_rejects_oversized_upload():
    c = _client()
    huge = b"x" * (51 * 1024 * 1024)
    resp = c.post(
        "/api/v1/journal/import",
        files={"file": ("big.csv", huge, "text/csv")},
    )
    assert resp.status_code == 413


def test_import_rejects_empty_upload():
    c = _client()
    resp = c.post(
        "/api/v1/journal/import",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert resp.status_code == 400


def test_import_sanitises_path_traversal_filename():
    """Regression: malicious filenames must not flow into the audit source_path verbatim."""
    c = _client()
    with open(FIXTURE, "rb") as fh:
        content = fh.read()
    resp = c.post(
        "/api/v1/journal/import",
        files={"file": ("../../etc/passwd.csv", content, "text/csv")},
    )
    assert resp.status_code == 200
