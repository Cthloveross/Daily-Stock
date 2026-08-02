# -*- coding: utf-8 -*-
"""Contract tests for POST /api/v1/opportunities/near-expiry-contracts."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.v1.endpoints import opportunities


@pytest.fixture(autouse=True)
def _clear_opportunity_scan_cache():
    opportunities._reset_scan_cache_for_tests()
    yield
    opportunities._reset_scan_cache_for_tests()


@pytest.fixture(autouse=True)
def _stub_earnings_calendar_not_configured(monkeypatch):
    """默认：财报日历按未配置处理（显式 unavailable），单测绝不打真实 Finnhub。"""

    monkeypatch.setattr(
        opportunities,
        "_fetch_earnings_calendar_rows",
        lambda from_date, to_date: (None, "finnhub_not_configured"),
    )


def _market_date_et() -> date:
    """与 endpoint 相同口径的 ET 交易日（真实 now → America/New_York）。"""

    return datetime.now(timezone.utc).astimezone(opportunities._NEW_YORK).date()


def _stub_earnings_rows(monkeypatch, rows: list[dict]) -> list[tuple[date, date]]:
    """Replace the shared earnings range fetch with a call-recording stub."""

    calls: list[tuple[date, date]] = []

    def fetch(from_date: date, to_date: date):
        calls.append((from_date, to_date))
        return rows, None

    monkeypatch.setattr(opportunities, "_fetch_earnings_calendar_rows", fetch)
    return calls


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(opportunities.router, prefix="/api/v1/opportunities")
    return TestClient(app)


def _contract(**overrides) -> SimpleNamespace:
    base = {
        "code": "US.MU260804C100000",
        "expiry": "2026-08-04",
        "dte": 2,
        "right": "C",
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.2,
        "last_price": 1.1,
        "volume": 321,
        "open_interest": 1500,
        "iv_percent": 52.5,
        "delta": 0.51,
        "update_time": "2026-08-04 15:59:58",
        "snapshot_state": "observed",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _chain_snapshot(**overrides) -> SimpleNamespace:
    base = {
        "symbol": "MU",
        "spot": 100.0,
        "spot_as_of": "2026-08-04 15:59:59",
        "fetched_at": datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
        "max_dte": 3,
        "expiries": (("2026-08-04", 2),),
        "contracts": (_contract(),),
        "requested_contract_count": 1,
        "snapshot_received_count": 1,
        "failed_batch_count": 0,
        "excluded_nonstandard_count": 0,
        "excluded_unknown_standard_type_count": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_disabled_is_explicit_and_never_queries_moomoo(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: False)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda *args, **kwargs: pytest.fail(
            "disabled integration must not query Moomoo"
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "near-expiry-contracts/1.0"
    item = body["item"]
    assert item["state"] == "not_configured"
    assert item["spot"] is None
    assert item["expiries"] == []
    assert item["max_dte"] == 3
    assert any("不构成合约推荐" in value for value in item["limitations"])
    assert any("券商实时盘口" in value for value in item["limitations"])


def test_ready_response_carries_contract_rows_and_honest_bases(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(),
    )
    monkeypatch.setattr(
        opportunities,
        "_previous_xnys_session_label",
        lambda market_date: "2026-07-31",
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "us.mu", "max_dte": 3},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["ticker"] == "MU"
    assert item["state"] == "ready"
    assert item["spot"] == 100.0
    assert item["spot_as_of"] == "2026-08-04 15:59:59"
    assert item["open_interest_as_of"] == "2026-07-31"
    assert item["open_interest_basis"] == "prior_clearing_session"
    assert item["strike_window"]["percent_band"] == 5.0
    assert item["strike_window"]["min_strikes_per_side"] == 8
    group = item["expiries"][0]
    assert group["expiry"] == "2026-08-04"
    assert group["dte"] == 2
    assert group["state"] == "ready"
    row = group["contracts"][0]
    assert row["bid"] == 1.0 and row["ask"] == 1.2
    assert row["mid"] == pytest.approx(1.1)
    assert row["spread_percent"] == pytest.approx(18.181818, abs=1e-6)
    assert row["open_interest"] == 1500
    assert row["iv_percent"] == 52.5
    assert row["quote_as_of"] == "2026-08-04 15:59:58"
    assert row["quote_state"] == "observed"
    assert row["is_atm"] is True


def test_missing_bid_ask_stays_null_never_zero(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(
            contracts=(_contract(bid=None, ask=None),),
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    )

    assert response.status_code == 200
    row = response.json()["item"]["expiries"][0]["contracts"][0]
    assert row["bid"] is None
    assert row["ask"] is None
    assert row["mid"] is None
    assert row["spread_percent"] is None
    assert row["spread_unavailable_reason"] == "bid_or_ask_unavailable"


def test_per_expiry_isolation_survives_partial_snapshot_failure(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    contracts = (
        _contract(code="US.MU260802C100000", expiry="2026-08-02", dte=0),
        _contract(
            code="US.MU260804C100000",
            expiry="2026-08-04",
            dte=2,
            bid=None,
            ask=None,
            last_price=None,
            volume=None,
            open_interest=None,
            iv_percent=None,
            delta=None,
            update_time=None,
            snapshot_state="missing",
        ),
    )
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(
            expiries=(("2026-08-02", 0), ("2026-08-04", 2)),
            contracts=contracts,
            requested_contract_count=2,
            snapshot_received_count=1,
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["state"] == "partial"
    states = {group["expiry"]: group["state"] for group in item["expiries"]}
    assert states == {"2026-08-02": "ready", "2026-08-04": "unavailable"}
    missing = item["expiries"][1]["contracts"][0]
    assert missing["quote_state"] == "unavailable"
    assert missing["unavailable_reason"] == "snapshot_missing"


def test_empty_window_is_honest_empty_state_not_failure(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(
            expiries=(),
            contracts=(),
            requested_contract_count=0,
            snapshot_received_count=0,
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU", "max_dte": 0},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["state"] == "empty"
    assert item["expiries"] == []
    assert "诚实空态" in item["message"]


def test_adapter_none_degrades_to_unavailable_without_fabrication(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: None,
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "NVDA"},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["state"] == "unavailable"
    assert item["spot"] is None
    assert item["expiries"] == []


def test_identical_requests_reuse_ttl_cached_result(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    calls = {"count": 0}

    def compute(symbol, *, max_dte):
        calls["count"] += 1
        return _chain_snapshot()

    monkeypatch.setattr(
        opportunities, "_compute_near_expiry_chain_moomoo", compute
    )

    client = _client()
    payload = {"symbol": "MU", "max_dte": 3}
    first = client.post(
        "/api/v1/opportunities/near-expiry-contracts", json=payload
    )
    second = client.post(
        "/api/v1/opportunities/near-expiry-contracts", json=payload
    )

    assert first.status_code == 200 and second.status_code == 200
    assert calls["count"] == 1
    assert first.json()["item"] == second.json()["item"]


def test_explicit_refresh_bypasses_completed_ttl_entry(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    calls = {"count": 0}

    def compute(symbol, *, max_dte):
        calls["count"] += 1
        return _chain_snapshot()

    monkeypatch.setattr(
        opportunities, "_compute_near_expiry_chain_moomoo", compute
    )

    client = _client()
    assert client.post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    ).status_code == 200
    assert client.post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU", "refresh": True},
    ).status_code == 200
    assert calls["count"] == 2


def test_single_flight_shares_one_in_progress_read(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    release = threading.Event()
    calls = {"count": 0}

    def compute(symbol, *, max_dte):
        calls["count"] += 1
        assert release.wait(timeout=5), "leader release timed out"
        return _chain_snapshot()

    monkeypatch.setattr(
        opportunities, "_compute_near_expiry_chain_moomoo", compute
    )

    client = _client()
    payload = {"symbol": "MU", "max_dte": 3}
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            client.post,
            "/api/v1/opportunities/near-expiry-contracts",
            json=payload,
        )
        second = executor.submit(
            client.post,
            "/api/v1/opportunities/near-expiry-contracts",
            json=payload,
        )
        release.set()
        responses = [first.result(timeout=10), second.result(timeout=10)]

    assert all(response.status_code == 200 for response in responses)
    assert calls["count"] == 1


def test_max_dte_isolates_cache_keys(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    seen: list[int] = []

    def compute(symbol, *, max_dte):
        seen.append(max_dte)
        return _chain_snapshot(max_dte=max_dte)

    monkeypatch.setattr(
        opportunities, "_compute_near_expiry_chain_moomoo", compute
    )

    client = _client()
    assert client.post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU", "max_dte": 3},
    ).status_code == 200
    assert client.post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU", "max_dte": 5},
    ).status_code == 200
    assert seen == [3, 5]


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "600519"},
        {"symbol": "HK.00700"},
        {"symbol": "TOOLONGSYM"},
        {"symbol": ""},
        {"symbol": "MU", "max_dte": 8},
        {"symbol": "MU", "max_dte": -1},
        {},
    ],
)
def test_invalid_symbol_or_max_dte_is_422(monkeypatch, payload):
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda *args, **kwargs: pytest.fail(
            "invalid requests must not query Moomoo"
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts", json=payload
    )

    assert response.status_code == 422


def test_earnings_blackout_is_surfaced_on_the_contract_panel(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(),
    )
    market_date = _market_date_et()
    earnings_date = market_date + timedelta(days=2)
    _stub_earnings_rows(
        monkeypatch,
        [{"symbol": "MU", "date": earnings_date.isoformat()}],
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    )

    assert response.status_code == 200
    proximity = response.json()["item"]["earnings_proximity"]
    assert proximity["state"] == "ready"
    assert proximity["earnings_date"] == earnings_date.isoformat()
    assert proximity["days_to_earnings"] == 2
    assert proximity["within_blackout"] is True
    assert proximity["blackout_days"] == 3
    assert proximity["window_days"] == 5
    assert proximity["basis"] == "finnhub_earnings_calendar_forward_window"


def test_earnings_outside_blackout_stays_unflagged_but_ready(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(),
    )
    market_date = _market_date_et()
    earnings_date = market_date + timedelta(days=5)
    _stub_earnings_rows(
        monkeypatch,
        [
            # 窗口内但在回避窗（≤3 天）之外 → ready 且不标注。
            {"symbol": "MU", "date": earnings_date.isoformat()},
            # 其他标的的财报绝不串到本 underlying。
            {"symbol": "NVDA", "date": market_date.isoformat()},
        ],
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    )

    assert response.status_code == 200
    proximity = response.json()["item"]["earnings_proximity"]
    assert proximity["state"] == "ready"
    assert proximity["earnings_date"] == earnings_date.isoformat()
    assert proximity["days_to_earnings"] == 5
    assert proximity["within_blackout"] is False


def test_earnings_calendar_unavailable_never_implies_safe(monkeypatch):
    # autouse fixture 已把日历打成 finnhub_not_configured；面板自身照常返回。
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(),
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["state"] == "ready"
    proximity = item["earnings_proximity"]
    assert proximity["state"] == "unavailable"
    assert proximity["within_blackout"] is None
    assert proximity["days_to_earnings"] is None
    assert proximity["earnings_date"] is None
    assert proximity["unavailable_reason"] == "finnhub_not_configured"


def test_earnings_field_present_even_when_moomoo_disabled(monkeypatch):
    """财报临近与 Moomoo 可用性正交：not_configured 面板也带该字段。"""

    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: False)
    market_date = _market_date_et()
    _stub_earnings_rows(
        monkeypatch,
        [{"symbol": "MU", "date": market_date.isoformat()}],
    )

    response = _client().post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU"},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["state"] == "not_configured"
    proximity = item["earnings_proximity"]
    assert proximity["state"] == "ready"
    assert proximity["days_to_earnings"] == 0
    assert proximity["within_blackout"] is True


def test_earnings_calendar_cache_is_shared_with_scan_lane(monkeypatch):
    """扫描表已加载日历时，临期面板复用同一份缓存，不发第二次区间调用。"""

    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_near_expiry_chain_moomoo",
        lambda symbol, *, max_dte: _chain_snapshot(),
    )
    market_date = _market_date_et()
    calls = _stub_earnings_rows(
        monkeypatch,
        [{"symbol": "MU", "date": (market_date + timedelta(days=1)).isoformat()}],
    )

    # 扫描表车道加载日历的唯一入口就是这条共享函数（见 _execute_intraday_top）。
    warmed = opportunities._load_intraday_earnings_calendar(market_date.isoformat())
    assert warmed["state"] == "ready"
    assert len(calls) == 1

    client = _client()
    first = client.post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU", "max_dte": 3},
    )
    # 不同 max_dte → 面板结果缓存 miss，强制重新装配；日历仍不得重拉。
    second = client.post(
        "/api/v1/opportunities/near-expiry-contracts",
        json={"symbol": "MU", "max_dte": 2},
    )

    assert first.status_code == 200 and second.status_code == 200
    assert len(calls) == 1
    assert first.json()["item"]["earnings_proximity"]["within_blackout"] is True
    assert second.json()["item"]["earnings_proximity"]["within_blackout"] is True
