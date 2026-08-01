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
from src.opportunities import maintenance_repository
from src.services import opportunity_snapshot_service


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
    rows = []
    current = date(2026, 7, 21)
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
                "volume": 2_000 if index == 24 else 1_000,
                "amount": close * 1_000,
            }
        )
    return rows


def _stub_dependencies(monkeypatch):
    manager = _FakeManager()

    def load_history(symbol: str, *, as_of: datetime, manager) -> DailyHistoryInput:
        assert manager is not None
        return DailyHistoryInput(bars=_bars(), source="fixture", fetched_at=as_of)

    monkeypatch.setattr(opportunities, "_create_data_fetcher_manager", lambda: manager)
    monkeypatch.setattr(opportunities, "_load_daily_history", load_history)
    monkeypatch.setattr(
        opportunities,
        "_load_current_regime",
        lambda market_date: {
            "date": market_date,
            "score": 65,
            "label": "standard",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc),
        },
    )
    return manager


def test_daily_endpoint_returns_stable_evidence_contract(monkeypatch):
    _stub_dependencies(monkeypatch)

    response = _client().post(
        "/api/v1/opportunities/daily",
        json={"symbols": ["aapl", "MSFT", "AAPL"], "limit": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["universe"] == ["AAPL", "MSFT"]
    assert body["candidate_count"] == 2
    assert body["candidates"][0]["evidence"]
    assert body["candidates"][0]["style_match"]["status"] == "unknown"
    assert body["ranking_method"] == "rule_based_evidence_count"
    assert body["strategy_validation_state"] == "not_validated"
    assert "score" not in body


def _snapshot_item() -> dict:
    return {
        "schema_version": "opportunity-snapshot/1.0",
        "snapshot_key": "ops_test",
        "market_date_et": "2026-07-22",
        "source_run_id": "opr_test",
        "frozen_at": "2026-07-22T13:00:00+00:00",
        "signal_version": "daily_completed_bars_v1",
        "candidate_count": 1,
        "eligible_candidate_count": 1,
        "validation_eligible": True,
        "eligibility_reasons": [],
        "analysis_quality_eligible": True,
        "analysis_quality_reasons": [],
        "outcome_progress": [
            {
                "horizon_sessions": horizon,
                "eligible_count": 1,
                "mature_count": 0,
                "pending_count": 1,
                "partial_count": 0,
            }
            for horizon in (5, 20)
        ],
        "idempotent_replay": False,
    }


def test_freeze_snapshot_is_explicit_and_uses_current_scan(monkeypatch):
    _stub_dependencies(monkeypatch)
    captured = {}

    def freeze(run):
        captured["run"] = run
        return _snapshot_item()

    monkeypatch.setattr(opportunity_snapshot_service, "freeze_daily_snapshot", freeze)
    response = _client().post(
        "/api/v1/opportunities/snapshots/freeze",
        json={"symbols": ["AAPL"], "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["snapshot_key"] == "ops_test"
    assert captured["run"]["universe"] == ["AAPL"]


def test_ensure_snapshot_uses_safe_automatic_service(monkeypatch):
    _stub_dependencies(monkeypatch)
    captured = {}

    def ensure(run):
        captured["run"] = run
        return {
            "schema_version": "opportunity-snapshot-ensure/1.0",
            "state": "outside_window",
            "market_date_et": run["market_date_et"],
            "snapshot": None,
            "message": "outside official window",
        }

    monkeypatch.setattr(opportunity_snapshot_service, "ensure_daily_snapshot", ensure)
    response = _client().post(
        "/api/v1/opportunities/snapshots/ensure",
        json={"symbols": ["AAPL"], "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "outside_window"
    assert captured["run"]["universe"] == ["AAPL"]


def test_snapshot_list_and_learning_summary_are_read_only(monkeypatch):
    item = _snapshot_item()
    monkeypatch.setattr(
        opportunity_snapshot_service,
        "list_snapshot_items",
        lambda *, limit: {
            "schema_version": "opportunity-snapshot-list/1.0",
            "items": [item],
        },
    )
    monkeypatch.setattr(
        opportunity_snapshot_service,
        "learning_summary",
        lambda: {
            "schema_version": "opportunity-learning/1.0",
            "generated_at": "2026-07-22T13:00:00+00:00",
            "strategy_state": "collecting",
            "auto_adjustment": False,
            "minimum_summary_samples": 20,
            "minimum_investigation_samples": 20,
            "horizons": [
                {
                    "horizon_sessions": horizon,
                    "mature_count": 0,
                    "distinct_signal_sessions": 0,
                    "directional_sample_count": 0,
                    "context_hit_count": None,
                    "context_miss_count": None,
                    "neutral_count": None,
                    "non_directional_count": None,
                    "context_hit_rate_percent": None,
                    "summary_visible": False,
                    "investigation_ready": False,
                }
                for horizon in (5, 20)
            ],
            "limitations": ["underlying only"],
        },
    )
    monkeypatch.setattr(
        maintenance_repository,
        "get_latest_outcome_maintenance",
        lambda: None,
    )
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            opportunity_outcome_scheduler_enabled=True,
        ),
    )

    client = _client()
    snapshots = client.get("/api/v1/opportunities/snapshots?limit=5")
    summary = client.get("/api/v1/opportunities/learning-summary")

    assert snapshots.status_code == 200
    assert snapshots.json()["items"][0]["snapshot_key"] == "ops_test"
    assert summary.status_code == 200
    assert summary.json()["auto_adjustment"] is False
    assert summary.json()["automatic_maintenance_enabled"] is True
    assert summary.json()["minimum_summary_samples"] == 20


_VALID_SNAPSHOT_KEY = "ops_" + "a" * 64


def test_snapshot_detail_returns_frozen_run_verbatim(monkeypatch):
    frozen_run = {
        "run_id": "opr_frozen",
        "market_date_et": "2026-07-22",
        "signal_version": "daily_completed_bars_v1",
        "candidates": [{"ticker": "NVDA", "candidate_id": "cand_1"}],
    }
    captured = {}

    def detail(snapshot_key):
        captured["key"] = snapshot_key
        return {
            "schema_version": "opportunity-snapshot-detail/1.0",
            "snapshot": _snapshot_item(),
            "run": frozen_run,
        }

    monkeypatch.setattr(
        opportunity_snapshot_service, "get_snapshot_detail", detail
    )

    response = _client().get(
        f"/api/v1/opportunities/snapshots/{_VALID_SNAPSHOT_KEY}"
    )

    assert response.status_code == 200
    body = response.json()
    assert captured["key"] == _VALID_SNAPSHOT_KEY
    assert body["schema_version"] == "opportunity-snapshot-detail/1.0"
    assert body["snapshot"]["snapshot_key"] == "ops_test"
    assert body["run"] == frozen_run


def test_snapshot_detail_unknown_key_returns_404(monkeypatch):
    monkeypatch.setattr(
        opportunity_snapshot_service,
        "get_snapshot_detail",
        lambda snapshot_key: None,
    )

    response = _client().get(
        f"/api/v1/opportunities/snapshots/{_VALID_SNAPSHOT_KEY}"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "snapshot_not_found"


def test_snapshot_detail_rejects_malformed_key_without_touching_storage(
    monkeypatch,
):
    def must_not_be_called(snapshot_key):
        raise AssertionError("malformed key must fail closed before storage")

    monkeypatch.setattr(
        opportunity_snapshot_service, "get_snapshot_detail", must_not_be_called
    )

    response = _client().get("/api/v1/opportunities/snapshots/ops_not-a-key")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "snapshot_not_found"


def test_evaluate_unknown_snapshot_returns_404(monkeypatch):
    def missing(_snapshot_key):
        raise opportunity_snapshot_service.OpportunitySnapshotNotFoundError(
            "ops_missing"
        )

    monkeypatch.setattr(opportunity_snapshot_service, "evaluate_snapshot", missing)
    response = _client().post(
        "/api/v1/opportunities/snapshots/ops_missing/evaluate"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "snapshot_not_found"


def test_empty_symbols_fall_back_to_server_stock_list(monkeypatch):
    _stub_dependencies(monkeypatch)
    monkeypatch.setattr(opportunities, "_configured_symbols", lambda: ["qqq", "spy", "QQQ"])

    response = _client().post(
        "/api/v1/opportunities/daily",
        json={"symbols": [], "limit": 2},
    )

    assert response.status_code == 200
    assert response.json()["universe"] == ["QQQ", "SPY"]


def test_option_overview_returns_batch_iv_rank_and_time_bases(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_previous_xnys_session_label",
        lambda _market_date: "2026-07-21",
    )
    monkeypatch.setattr(
        opportunities,
        "_compute_option_overviews_moomoo",
        lambda symbols: {
            "AAPL": SimpleNamespace(
                name="Apple",
                fetched_at=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
                call_volume=451945,
                put_volume=324225,
                call_open_interest=2687511,
                put_open_interest=1971458,
                iv_percent=31.865,
                iv_rank_percent=77.141,
                iv_percentile_percent=88.888,
                previous_iv_percent=32.121,
                hv_30d_percent=36.996,
                hv_30d_percentile=98.015,
                hv_60d_percent=31.782,
                hv_60d_percentile=99.206,
                hv_90d_percent=28.039,
                hv_90d_percentile=95.634,
                hv_120d_percent=27.197,
                hv_120d_percentile=94.047,
                hv_365d_percent=24.506,
                hv_365d_percentile=28.174,
            )
        },
    )

    response = _client().post(
        "/api/v1/opportunities/option-overview",
        json={"symbols": ["aapl", "MSFT"]},
    )

    assert response.status_code == 200
    items = {item["ticker"]: item for item in response.json()["items"]}
    assert items["AAPL"]["state"] == "ready"
    assert items["AAPL"]["iv_rank_percent"] == pytest.approx(77.141)
    assert items["AAPL"]["put_call_volume_ratio"] == pytest.approx(
        324225 / 451945
    )
    assert items["AAPL"]["open_interest_as_of"] == "2026-07-21"
    assert items["MSFT"]["state"] == "unavailable"


def test_loader_exception_degrades_candidate_instead_of_500(monkeypatch):
    monkeypatch.setattr(opportunities, "_create_data_fetcher_manager", _FakeManager)
    monkeypatch.setattr(
        opportunities,
        "_load_daily_history",
        lambda symbol, *, as_of, manager: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(opportunities, "_load_current_regime", lambda market_date: None)

    response = _client().post(
        "/api/v1/opportunities/daily",
        json={"symbols": ["AAPL"], "limit": 1},
    )

    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["research_state"] == "blocked"
    assert next(item for item in candidate["readiness"] if item["domain"] == "daily_history")["state"] == "unavailable"


def test_identical_requests_reuse_ttl_cached_scan(monkeypatch):
    calls = []
    manager = _stub_dependencies(monkeypatch)

    original_loader = opportunities._load_daily_history

    def counted_loader(symbol: str, *, as_of: datetime, manager):
        calls.append(symbol)
        return original_loader(symbol, as_of=as_of, manager=manager)

    monkeypatch.setattr(opportunities, "_load_daily_history", counted_loader)
    client = _client()
    payload = {"symbols": ["AAPL", "MSFT"], "limit": 2}

    first = client.post("/api/v1/opportunities/daily", json=payload)
    second = client.post("/api/v1/opportunities/daily", json=payload)

    assert first.status_code == second.status_code == 200
    assert calls == ["AAPL", "MSFT"] or calls == ["MSFT", "AAPL"]
    assert manager.close_count == 1
    assert second.json()["run_id"] == first.json()["run_id"]


def test_explicit_refresh_bypasses_completed_ttl_entry(monkeypatch):
    calls = []
    manager = _stub_dependencies(monkeypatch)
    original_loader = opportunities._load_daily_history

    def counted_loader(symbol: str, *, as_of: datetime, manager):
        calls.append(symbol)
        return original_loader(symbol, as_of=as_of, manager=manager)

    monkeypatch.setattr(opportunities, "_load_daily_history", counted_loader)
    client = _client()
    payload = {"symbols": ["AAPL"], "limit": 1}

    first = client.post("/api/v1/opportunities/daily", json=payload)
    refreshed = client.post(
        "/api/v1/opportunities/daily",
        json={**payload, "refresh": True},
    )

    assert first.status_code == refreshed.status_code == 200
    assert calls == ["AAPL", "AAPL"]
    assert manager.close_count == 2


def test_expired_ttl_entry_is_recomputed(monkeypatch):
    calls = []
    clock = {"now": 100.0}
    _stub_dependencies(monkeypatch)
    original_loader = opportunities._load_daily_history

    def counted_loader(symbol: str, *, as_of: datetime, manager):
        calls.append(symbol)
        return original_loader(symbol, as_of=as_of, manager=manager)

    monkeypatch.setattr(opportunities, "_load_daily_history", counted_loader)
    monkeypatch.setattr(opportunities, "_cache_now", lambda: clock["now"])
    client = _client()
    payload = {"symbols": ["AAPL"], "limit": 1}

    assert client.post("/api/v1/opportunities/daily", json=payload).status_code == 200
    assert client.post("/api/v1/opportunities/daily", json=payload).status_code == 200
    clock["now"] += opportunities._SCAN_CACHE_TTL_SECONDS + 1
    assert client.post("/api/v1/opportunities/daily", json=payload).status_code == 200

    assert calls == ["AAPL", "AAPL"]


def test_single_flight_shares_one_in_progress_scan(monkeypatch):
    key = ("test-signal", ("AAPL",), 1)
    caller_barrier = threading.Barrier(5)
    factory_started = threading.Event()
    release_factory = threading.Event()
    counter_lock = threading.Lock()
    factory_calls = 0

    # With a zero TTL, late callers would have to recompute.  Therefore one
    # factory call also proves all simultaneous callers joined the flight.
    monkeypatch.setattr(opportunities, "_SCAN_CACHE_TTL_SECONDS", 0.0)

    def factory():
        nonlocal factory_calls
        with counter_lock:
            factory_calls += 1
        factory_started.set()
        assert release_factory.wait(timeout=2)
        return {"shared": True}

    def caller():
        caller_barrier.wait(timeout=2)
        return opportunities._get_or_compute_scan(key, factory)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(caller) for _ in range(4)]
        caller_barrier.wait(timeout=2)
        assert factory_started.wait(timeout=2)
        # Give the other released workers time to observe the in-flight entry.
        time.sleep(0.05)
        release_factory.set()
        results = [future.result(timeout=2) for future in futures]

    assert factory_calls == 1
    assert results == [{"shared": True}] * 4


def test_daily_scan_cache_key_isolated_by_et_market_date():
    assert opportunities._scan_cache_key(
        ["AAPL"],
        1,
        market_date_et=date(2026, 7, 22),
    ) != opportunities._scan_cache_key(
        ["AAPL"],
        1,
        market_date_et=date(2026, 7, 23),
    )


def test_expired_flight_generation_cannot_overwrite_replacement_cache():
    key = ("expired-generation",)
    started = threading.Event()
    release = threading.Event()

    def slow_factory():
        started.set()
        assert release.wait(timeout=2)
        return {"generation": "stale"}

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_future = executor.submit(
            opportunities._get_or_compute_scan,
            key,
            slow_factory,
            lease_seconds=0.05,
            wait_timeout_seconds=0.05,
        )
        assert started.wait(timeout=1)
        time.sleep(0.07)
        replacement = opportunities._get_or_compute_scan(
            key,
            lambda: {"generation": "fresh"},
            lease_seconds=1,
            wait_timeout_seconds=1,
        )
        release.set()
        with pytest.raises(opportunities.OpportunityScanTimeoutError):
            stale_future.result(timeout=1)

    cached = opportunities._get_or_compute_scan(
        key,
        lambda: pytest.fail("fresh generation should remain cached"),
    )
    assert replacement == cached == {"generation": "fresh"}


def test_daily_timeout_is_explicit_retryable_504(monkeypatch):
    monkeypatch.setattr(
        opportunities,
        "_get_or_compute_scan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            opportunities.OpportunityScanTimeoutError("sensitive provider detail")
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/daily",
        json={"symbols": ["AAPL"], "limit": 1},
    )

    assert response.status_code == 504
    assert response.json()["detail"] == {
        "error": "opportunity_scan_timeout",
        "message": "行情研究仍在执行或已超时，请稍后重试。",
        "retryable": True,
        "retry_after_seconds": 2,
    }
    assert "sensitive provider detail" not in response.text


def test_one_request_reuses_manager_and_caps_history_fetch_concurrency(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(opportunities, "_create_data_fetcher_manager", lambda: manager)
    monkeypatch.setattr(opportunities, "_load_current_regime", lambda market_date: None)
    worker_barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    max_active = 0
    seen_managers = []

    def concurrent_loader(symbol: str, *, as_of: datetime, manager):
        nonlocal active, max_active
        with lock:
            seen_managers.append(manager)
            active += 1
            max_active = max(max_active, active)
        try:
            worker_barrier.wait(timeout=2)
            return DailyHistoryInput(bars=_bars(), source="fixture", fetched_at=as_of)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(opportunities, "_load_daily_history", concurrent_loader)

    result = opportunities._execute_daily_scan(
        ["AAPL", "MSFT", "NVDA", "TSLA"], limit=4
    )

    assert result["candidate_count"] == 4
    assert seen_managers == [manager] * 4
    assert max_active == 4
    assert max_active <= opportunities._MAX_FETCH_WORKERS
    assert manager.close_count == 1


def test_mixed_universe_skips_non_us_fetch_without_failing_batch(monkeypatch):
    manager = _FakeManager()
    calls = []
    execute_daily_scan = opportunities._execute_daily_scan
    monkeypatch.setattr(opportunities, "_create_data_fetcher_manager", lambda: manager)
    monkeypatch.setattr(opportunities, "_load_current_regime", lambda market_date: None)
    monkeypatch.setattr(
        opportunities,
        "_execute_daily_scan",
        lambda symbols, limit: execute_daily_scan(
            symbols,
            limit,
            as_of=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
        ),
    )

    def load_history(symbol: str, *, as_of: datetime, manager):
        calls.append(symbol)
        return DailyHistoryInput(bars=_bars(), source="fixture", fetched_at=as_of)

    monkeypatch.setattr(opportunities, "_load_daily_history", load_history)

    response = _client().post(
        "/api/v1/opportunities/daily",
        json={"symbols": ["AAPL", "600519"], "limit": 2},
    )

    assert response.status_code == 200
    by_ticker = {item["ticker"]: item for item in response.json()["candidates"]}
    assert calls == ["AAPL"]
    assert by_ticker["AAPL"]["research_state"] == "research_ready"
    assert by_ticker["600519"]["research_state"] == "blocked"
    assert manager.close_count == 1


def test_option_context_disabled_is_explicitly_not_configured(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: False)
    monkeypatch.setattr(
        opportunities,
        "_compute_atm_iv_moomoo",
        lambda symbol: pytest.fail("disabled integration must not query Moomoo"),
    )

    response = _client().post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["AAPL"]},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["ticker"] == "AAPL"
    assert item["state"] == "not_configured"
    assert item["source"] == "moomoo_openapi"
    assert item["expiry"] is None
    assert item["atm_call_iv_percent"] is None
    assert any("不是 IV Rank" in value for value in item["limitations"])
    assert any("不是异常期权流" in value for value in item["limitations"])
    assert any("不是买卖信号" in value for value in item["limitations"])


def test_option_context_ready_converts_decimal_iv_to_percent(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_atm_iv_moomoo",
        lambda symbol: (0.425, "2026-08-21"),
    )

    response = _client().post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["us.nvda"]},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["ticker"] == "NVDA"
    assert item["state"] == "ready"
    assert item["expiry"] == "2026-08-21"
    assert item["atm_call_iv_percent"] == 42.5
    assert "最近到期" in item["message"]
    assert "Call 单点 IV" in item["message"]
    assert response.json()["market_date_et"]


def test_option_context_missing_and_exception_degrade_per_symbol(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)

    def compute(symbol: str):
        if symbol == "AAPL":
            return None, "2026-08-21"
        if symbol == "MSFT":
            raise RuntimeError("OpenD offline")
        return 0.31, "2026-08-21"

    monkeypatch.setattr(opportunities, "_compute_atm_iv_moomoo", compute)

    response = _client().post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["AAPL", "MSFT", "NVDA"]},
    )

    assert response.status_code == 200
    by_ticker = {item["ticker"]: item for item in response.json()["items"]}
    assert by_ticker["AAPL"]["state"] == "unavailable"
    assert by_ticker["AAPL"]["expiry"] == "2026-08-21"
    assert by_ticker["MSFT"]["state"] == "unavailable"
    assert by_ticker["NVDA"]["state"] == "ready"


def test_option_context_reuses_independent_ttl_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)

    def compute(symbol: str):
        calls.append(symbol)
        return 0.25, "2026-08-21"

    monkeypatch.setattr(opportunities, "_compute_atm_iv_moomoo", compute)
    client = _client()
    payload = {"symbols": ["AAPL", "MSFT"]}

    first = client.post("/api/v1/opportunities/option-context", json=payload)
    second = client.post("/api/v1/opportunities/option-context", json=payload)

    assert first.status_code == second.status_code == 200
    assert calls == ["AAPL", "MSFT"]
    assert second.json()["generated_at"] == first.json()["generated_at"]
    assert second.json()["market_date_et"] == first.json()["market_date_et"]
    assert opportunities._option_context_cache_key(
        "AAPL", True, first.json()["market_date_et"]
    ) != (
        opportunities._scan_cache_key(["AAPL"], 1)
    )


def test_option_context_reuses_cached_symbols_across_overlapping_batches(monkeypatch):
    calls = []
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)

    def compute(symbol: str):
        calls.append(symbol)
        return 0.25, "2026-08-21"

    monkeypatch.setattr(opportunities, "_compute_atm_iv_moomoo", compute)
    client = _client()

    first = client.post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["AAPL", "MSFT"]},
    )
    second = client.post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["MSFT", "NVDA"]},
    )

    assert first.status_code == second.status_code == 200
    assert calls == ["AAPL", "MSFT", "NVDA"]


def test_option_context_enablement_change_does_not_reuse_disabled_cache(monkeypatch):
    enabled = {"value": False}
    calls = []
    monkeypatch.setattr(
        opportunities, "_moomoo_opend_enabled", lambda: enabled["value"]
    )

    def compute(symbol: str):
        calls.append(symbol)
        return 0.2, "2026-08-21"

    monkeypatch.setattr(opportunities, "_compute_atm_iv_moomoo", compute)
    client = _client()
    payload = {"symbols": ["AAPL"]}

    disabled = client.post("/api/v1/opportunities/option-context", json=payload)
    enabled["value"] = True
    ready = client.post("/api/v1/opportunities/option-context", json=payload)

    assert disabled.json()["items"][0]["state"] == "not_configured"
    assert ready.json()["items"][0]["state"] == "ready"
    assert calls == ["AAPL"]


def test_option_context_rejects_more_than_three_or_non_us_symbols():
    client = _client()

    assert client.post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["AAPL", "MSFT", "NVDA", "TSLA"]},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["600519"]},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-context",
        json={"symbols": ["HK.00700"]},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-context",
        json={"symbols": []},
    ).status_code == 422


def _wall_snapshot(*, complete: bool = True, include_gamma: bool = True):
    contracts = (
        SimpleNamespace(
            code="US.TEST260724C105000",
            expiry="2026-07-24",
            right="C",
            strike=105.0,
            open_interest=1_000,
            volume=250,
            gamma=0.02 if include_gamma else None,
            contract_size=100 if include_gamma else None,
            update_time="2026-07-22 10:00:00",
            implied_volatility=0.425,
        ),
        SimpleNamespace(
            code="US.TEST260724P095000",
            expiry="2026-07-24",
            right="P",
            strike=95.0,
            open_interest=1_500,
            volume=300,
            gamma=0.03 if include_gamma else None,
            contract_size=100 if include_gamma else None,
            update_time="2026-07-22 10:00:01",
            implied_volatility=0.51,
        ),
    )
    return SimpleNamespace(
        spot=100.0,
        fetched_at=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
        expiries=("2026-07-24",),
        contracts=contracts,
        requested_contract_count=2 if complete else 3,
        snapshot_received_count=2,
        valid_contract_count=2,
        failed_batch_count=0 if complete else 1,
        excluded_nonstandard_count=1,
        excluded_unknown_standard_type_count=0,
    )


def test_option_walls_disabled_is_explicit_and_never_queries_moomoo(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: False)
    monkeypatch.setattr(
        opportunities,
        "_compute_option_wall_moomoo",
        lambda *args, **kwargs: pytest.fail("disabled integration must not query Moomoo"),
    )

    response = _client().post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": ["AAPL"], "dte_min": 0, "dte_max": 45},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["state"] == "not_configured"
    assert item["walls"]["call_oi"] == []
    assert item["atm_call_iv"]["state"] == "not_configured"
    assert item["atm_call_iv"]["atm_call_iv_percent"] is None
    assert item["scope"]["standard_contracts_only"] is True
    assert any("Dealer GEX" in value for value in item["limitations"])


def test_option_walls_return_ranked_observable_and_gamma_levels(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_atm_iv_moomoo",
        lambda symbol: pytest.fail(
            "wall response must derive ATM IV from its own snapshot"
        ),
    )
    monkeypatch.setattr(
        opportunities,
        "_compute_option_wall_moomoo",
        lambda symbol, *, dte_min, dte_max: _wall_snapshot(),
    )

    response = _client().post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": ["us.nvda"], "dte_min": 0, "dte_max": 45},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["ticker"] == "NVDA"
    assert item["state"] == "ready"
    assert item["walls"]["call_oi"][0]["strike"] == 105
    assert item["walls"]["put_oi"][0]["strike"] == 95
    assert item["walls"]["gross_gamma_concentration"]
    assert item["coverage"]["coverage_percent"] == 100
    assert item["quote_as_of"] == "2026-07-22 10:00:01"
    assert item["atm_call_iv"] == {
        "state": "ready",
        "expiry": "2026-07-24",
        "strike": 105.0,
        "atm_call_iv_percent": 42.5,
        "selection_method": (
            "nearest_expiry_atm_call_from_same_wall_snapshot"
        ),
    }
    assert response.json()["schema_version"] == "option-wall/1.1"


def test_option_walls_keep_oi_visible_when_gamma_or_snapshot_coverage_is_partial(
    monkeypatch,
):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    monkeypatch.setattr(
        opportunities,
        "_compute_option_wall_moomoo",
        lambda symbol, *, dte_min, dte_max: _wall_snapshot(
            complete=False,
            include_gamma=False,
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": ["AAPL"]},
    )

    item = response.json()["items"][0]
    assert item["state"] == "partial"
    assert item["walls"]["call_oi"]
    assert item["walls"]["gross_gamma_concentration"] == []
    assert item["coverage"]["gamma_contracts"] == 0


def test_option_walls_overlap_top_five_scans_and_preserve_request_order(
    monkeypatch,
):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)
    release = threading.Event()
    state_lock = threading.Lock()
    active = 0
    peak_active = 0
    started: list[str] = []

    def compute(symbol, *, dte_min, dte_max):
        nonlocal active, peak_active
        assert (dte_min, dte_max) == (0, 45)
        with state_lock:
            active += 1
            peak_active = max(peak_active, active)
            started.append(symbol)
            if len(started) == 5:
                release.set()
        try:
            assert release.wait(timeout=3)
            return _wall_snapshot()
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(opportunities, "_compute_option_wall_moomoo", compute)
    symbols = ["TSLA", "NVDA", "AAPL", "META", "COIN"]

    response = _client().post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": symbols, "dte_min": 0, "dte_max": 45},
    )

    assert response.status_code == 200
    assert peak_active == 5
    assert [item["ticker"] for item in response.json()["items"]] == symbols


def test_option_walls_validate_symbols_and_dte_range(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: False)
    client = _client()

    assert client.post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL"]},
    ).status_code == 200
    assert client.post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "AMZN"]},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": ["HK.00700"]},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-walls",
        json={"symbols": ["AAPL"], "dte_min": 30, "dte_max": 7},
    ).status_code == 422


def _option_event_snapshot(*, events=None, all_count: int | None = 746):
    if events is None:
        events = (
            {
                "event_id": "moomoo-evt-stable",
                "option_code": "US.AAPL270115P200000",
                "owner_code": "US.AAPL",
                "symbol": "AAPL",
                "fill_time": "2026-07-21 15:44:48",
                "ticker_type": "SELL",
                "price": 1.04,
                "volume": 1000,
                "turnover": 104000.0,
                "option_type": "PUT",
                "strike_price": 200.0,
                "expiry": "2027-01-15",
                "dte": 177,
                "underlying_price": 327.86,
                "bid_price": 1.04,
                "ask_price": 1.06,
                "iv_percent": 40.791,
                "total_volume": 1401,
                "total_open_interest": 26922,
                "vo_ratio_percent": 5.203,
                "delta": -0.026524137,
                "sentiment": "BULLISH",
                "order_types": ["SWEEP", "NORMAL"],
                "strategy_type": "SINGLE_LEG",
            },
        )
    return SimpleNamespace(
        fetched_at=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
        event_as_of=(
            "2026-07-21 15:44:48" if events else None
        ),
        all_count=all_count,
        events=events,
    )


def test_option_events_disabled_is_explicit_and_never_queries_moomoo(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: False)
    monkeypatch.setattr(
        opportunities,
        "_compute_option_events_moomoo",
        lambda *args, **kwargs: pytest.fail(
            "disabled integration must not query Moomoo"
        ),
    )

    response = _client().post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["AAPL"]},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["state"] == "not_configured"
    assert item["events"] == []
    assert item["all_count"] is None
    assert any("不独立证明" in value for value in item["limitations"])
    assert any("不进入今日机会排序" in value for value in item["limitations"])


def test_option_events_return_bounded_provider_classifications(monkeypatch):
    calls = []
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)

    def compute(symbol: str, *, limit: int):
        calls.append((symbol, limit))
        return _option_event_snapshot()

    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", compute)

    response = _client().post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["us.aapl"], "limit_per_symbol": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "option-event/1.0"
    item = body["items"][0]
    assert item["ticker"] == "AAPL"
    assert item["state"] == "ready"
    assert item["event_as_of"] == "2026-07-21 15:44:48"
    assert item["all_count"] == 746
    assert item["events"][0]["event_id"] == "moomoo-evt-stable"
    assert item["events"][0]["iv_percent"] == 40.791
    assert item["events"][0]["vo_ratio_percent"] == 5.203
    assert item["events"][0]["ticker_type"] == "SELL"
    assert item["events"][0]["sentiment"] == "BULLISH"
    assert calls == [("AAPL", 5)]


def test_option_events_distinguish_successful_empty_from_unavailable(monkeypatch):
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)

    def compute(symbol: str, *, limit: int):
        if symbol == "AAPL":
            return _option_event_snapshot(events=(), all_count=0)
        return None

    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", compute)

    response = _client().post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["AAPL", "MSFT"]},
    )

    assert response.status_code == 200
    by_ticker = {item["ticker"]: item for item in response.json()["items"]}
    assert by_ticker["AAPL"]["state"] == "empty"
    assert by_ticker["AAPL"]["all_count"] == 0
    assert by_ticker["MSFT"]["state"] == "unavailable"
    assert by_ticker["MSFT"]["all_count"] is None


def test_option_events_reuse_per_symbol_cache_and_separate_limits(monkeypatch):
    calls = []
    monkeypatch.setattr(opportunities, "_moomoo_opend_enabled", lambda: True)

    def compute(symbol: str, *, limit: int):
        calls.append((symbol, limit))
        return _option_event_snapshot()

    monkeypatch.setattr(opportunities, "_compute_option_events_moomoo", compute)
    client = _client()

    first = client.post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["AAPL", "MSFT"], "limit_per_symbol": 5},
    )
    second = client.post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["MSFT", "NVDA"], "limit_per_symbol": 5},
    )
    third = client.post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["AAPL"], "limit_per_symbol": 3},
    )

    assert first.status_code == second.status_code == third.status_code == 200
    assert calls == [
        ("AAPL", 5),
        ("MSFT", 5),
        ("NVDA", 5),
        ("AAPL", 3),
    ]


def test_option_events_validate_symbols_and_limit():
    client = _client()

    assert client.post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["AAPL", "MSFT", "NVDA", "TSLA"]},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["HK.00700"]},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-events",
        json={"symbols": ["AAPL"], "limit_per_symbol": 11},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/option-events",
        json={"symbols": []},
    ).status_code == 422


def test_request_bounds_are_enforced():
    too_many = [f"T{index}" for index in range(21)]
    client = _client()

    assert client.post(
        "/api/v1/opportunities/daily",
        json={"symbols": too_many, "limit": 10},
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/daily",
        json={"symbols": ["AAPL"], "limit": 16},
    ).status_code == 422


def test_main_v1_router_registers_opportunity_paths():
    from api.v1.router import router

    assert any(route.path == "/api/v1/opportunities/daily" for route in router.routes)
    assert any(
        route.path == "/api/v1/opportunities/option-context"
        for route in router.routes
    )
    assert any(
        route.path == "/api/v1/opportunities/option-walls"
        for route in router.routes
    )
    assert any(
        route.path == "/api/v1/opportunities/option-events"
        for route in router.routes
    )
