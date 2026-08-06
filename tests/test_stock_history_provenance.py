"""Offline coverage for stock-history provenance metadata."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import data_provider.base as data_provider_base
from api.v1.endpoints import stocks as stocks_endpoint
from src.services.stock_service import StockService


class _DailyManager:
    def __init__(self, frame: pd.DataFrame, source: str):
        self.frame = frame
        self.source = source
        self.requested_days = None

    def get_daily_data(self, stock_code: str, *, days: int):
        assert stock_code == "AAPL"
        self.requested_days = days
        return self.frame, self.source

    def get_stock_name(self, stock_code: str):
        assert stock_code == "AAPL"
        return "Apple"


def _service() -> StockService:
    """History methods do not need the repository initialized."""
    return StockService.__new__(StockService)


def _intraday_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": "AAPL",
                "date": "2026-07-21T09:30:00-04:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
            }
        ]
    )


def test_daily_history_preserves_source_and_computes_returned_coverage(
    monkeypatch,
):
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-18",
                "open": 2,
                "high": 3,
                "low": 1,
                "close": 2.5,
            },
            {
                "date": "2026-07-21",
                "open": 3,
                "high": 4,
                "low": 2,
                "close": 3.5,
            },
        ]
    )
    manager = _DailyManager(frame, "YfinanceFetcher")
    monkeypatch.setattr(data_provider_base, "DataFetcherManager", lambda: manager)

    result = _service().get_history_data("AAPL", period="daily", days=30)

    assert result["source"] == "YfinanceFetcher"
    assert result["coverage_start"] == "2026-07-18"
    assert result["coverage_end"] == "2026-07-21"
    assert result["last_bar_at"] == "2026-07-21"
    assert manager.requested_days == 30


def test_us_daily_manager_prefers_enabled_moomoo_before_remote_sources():
    class _Fetcher:
        def __init__(self, name: str, priority: int, result: pd.DataFrame):
            self.name = name
            self.priority = priority
            self.result = result
            self.calls = []

        def get_daily_data(self, **kwargs):
            self.calls.append(kwargs)
            return self.result

    frame = _intraday_frame()
    moomoo = _Fetcher("MoomooFetcher", 1, frame)
    yfinance = _Fetcher("YfinanceFetcher", 4, frame)
    longbridge = _Fetcher("LongbridgeFetcher", 5, frame)
    manager = data_provider_base.DataFetcherManager.__new__(
        data_provider_base.DataFetcherManager
    )
    manager._fetchers = [yfinance, longbridge, moomoo]

    result, source = manager.get_daily_data("AAPL", days=80)

    assert source == "MoomooFetcher"
    assert result is frame
    assert moomoo.calls == [
        {
            "stock_code": "AAPL",
            "start_date": None,
            "end_date": None,
            "days": 80,
        }
    ]
    assert yfinance.calls == []
    assert longbridge.calls == []


def test_us_daily_manager_falls_back_after_moomoo_failure():
    class _MoomooFetcher:
        name = "MoomooFetcher"
        priority = 1

        def __init__(self):
            self.calls = 0

        def get_daily_data(self, **_kwargs):
            self.calls += 1
            raise data_provider_base.DataFetchError("OpenD unavailable")

    class _YfinanceFetcher:
        name = "YfinanceFetcher"
        priority = 4

        def __init__(self, frame: pd.DataFrame):
            self.frame = frame
            self.calls = 0

        def get_daily_data(self, **_kwargs):
            self.calls += 1
            return self.frame

    class _LongbridgeFetcher:
        name = "LongbridgeFetcher"
        priority = 5

        def __init__(self):
            self.calls = 0

        def get_daily_data(self, **_kwargs):
            self.calls += 1
            raise AssertionError("Longbridge should not run after Yahoo succeeds")

    frame = _intraday_frame()
    moomoo = _MoomooFetcher()
    yfinance = _YfinanceFetcher(frame)
    longbridge = _LongbridgeFetcher()
    manager = data_provider_base.DataFetcherManager.__new__(
        data_provider_base.DataFetcherManager
    )
    manager._fetchers = [moomoo, yfinance, longbridge]

    result, source = manager.get_daily_data("AAPL", days=80)

    assert source == "YfinanceFetcher"
    assert result is frame
    assert moomoo.calls == 1
    assert yfinance.calls == 1
    assert longbridge.calls == 0


def test_us_daily_manager_skips_shelved_moomoo():
    class _Fetcher:
        def __init__(self, name: str, priority: int, result: pd.DataFrame):
            self.name = name
            self.priority = priority
            self.result = result
            self.calls = 0

        def get_daily_data(self, **_kwargs):
            self.calls += 1
            return self.result

    frame = _intraday_frame()
    moomoo = _Fetcher("MoomooFetcher", 99, frame)
    yfinance = _Fetcher("YfinanceFetcher", 4, frame)
    manager = data_provider_base.DataFetcherManager.__new__(
        data_provider_base.DataFetcherManager
    )
    manager._fetchers = [yfinance, moomoo]

    _, source = manager.get_daily_data("AAPL", days=80)

    assert source == "YfinanceFetcher"
    assert moomoo.calls == 0
    assert yfinance.calls == 1


def test_resampled_history_coverage_uses_final_trimmed_bars(monkeypatch):
    dates = pd.date_range("2026-06-29", "2026-07-17", freq="D")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": range(1, len(dates) + 1),
            "high": range(2, len(dates) + 2),
            "low": range(0, len(dates)),
            "close": range(1, len(dates) + 1),
            "volume": [10] * len(dates),
        }
    )
    manager = _DailyManager(frame, "LongbridgeFetcher")
    monkeypatch.setattr(data_provider_base, "DataFetcherManager", lambda: manager)

    result = _service().get_history_data("AAPL", period="weekly", days=2)

    assert [item["date"] for item in result["data"]] == [
        "2026-07-10",
        "2026-07-17",
    ]
    assert result["source"] == "LongbridgeFetcher"
    assert result["coverage_start"] == "2026-07-10"
    assert result["coverage_end"] == "2026-07-17"
    assert result["last_bar_at"] == "2026-07-17"
    assert manager.requested_days == 200


def test_empty_history_does_not_claim_source_or_coverage(monkeypatch):
    manager = _DailyManager(pd.DataFrame(), "YfinanceFetcher")
    monkeypatch.setattr(data_provider_base, "DataFetcherManager", lambda: manager)

    result = _service().get_history_data("AAPL", period="daily", days=30)

    assert result["data"] == []
    assert result["source"] is None
    assert result["coverage_start"] is None
    assert result["coverage_end"] is None
    assert result["last_bar_at"] is None


def test_intraday_history_preserves_source_and_iso_coverage(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-21T09:30:00-04:00",
                "open": 2,
                "high": 3,
                "low": 1,
                "close": 2.5,
            },
            {
                "date": "2026-07-21T09:35:00-04:00",
                "open": 2.5,
                "high": 4,
                "low": 2,
                "close": 3.5,
            },
        ]
    )

    class _IntradayManager:
        def get_intraday_data(self, stock_code: str, *, interval: str, days: int):
            assert (stock_code, interval, days) == ("AAPL", "5m", 1)
            return frame, "MoomooFetcher"

        def get_stock_name(self, stock_code: str):
            assert stock_code == "AAPL"
            return "Apple"

    monkeypatch.setattr(
        data_provider_base,
        "DataFetcherManager",
        _IntradayManager,
    )

    result = _service().get_history_data("AAPL", period="5m", days=1)

    assert result["source"] == "MoomooFetcher"
    assert result["coverage_start"] == "2026-07-21T09:30:00-04:00"
    assert result["coverage_end"] == "2026-07-21T09:35:00-04:00"
    assert result["last_bar_at"] == "2026-07-21T09:35:00-04:00"


@pytest.mark.parametrize("period", ["15m", "30m"])
def test_native_intraday_periods_preserve_interval_timezone_and_provenance(
    monkeypatch,
    period,
):
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-21T09:30:00-04:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
            },
            {
                "date": "2026-07-21T10:00:00-04:00",
                "open": 10.5,
                "high": 12,
                "low": 10,
                "close": 11.5,
            },
        ]
    )

    class _IntradayManager:
        requested = None

        def get_intraday_data(self, stock_code: str, *, interval: str, days: int):
            self.requested = (stock_code, interval, days)
            return frame, "MoomooFetcher"

        def get_stock_name(self, stock_code: str):
            assert stock_code == "AAPL"
            return "Apple"

    manager = _IntradayManager()
    monkeypatch.setattr(
        data_provider_base,
        "DataFetcherManager",
        lambda: manager,
    )

    result = _service().get_history_data("AAPL", period=period, days=3)

    assert manager.requested == ("AAPL", period, 3)
    assert result["period"] == period
    assert result["source"] == "MoomooFetcher"
    assert result["coverage_start"] == "2026-07-21T09:30:00-04:00"
    assert result["coverage_end"] == "2026-07-21T10:00:00-04:00"
    assert result["derived_from_period"] is None
    assert result["aggregation_method"] is None
    assert [bar["date"] for bar in result["data"]] == frame["date"].tolist()


@pytest.mark.parametrize("period", ["15m", "30m"])
def test_intraday_manager_prefers_moomoo_and_forwards_native_period(period):
    class _Fetcher:
        def __init__(self, name, priority):
            self.name = name
            self.priority = priority
            self.calls = []

        def fetch_intraday(self, **kwargs):
            self.calls.append(kwargs)
            return _intraday_frame()

    moomoo = _Fetcher("MoomooFetcher", 1)
    yfinance = _Fetcher("YfinanceFetcher", 4)
    manager = data_provider_base.DataFetcherManager.__new__(
        data_provider_base.DataFetcherManager
    )
    manager._fetchers = [yfinance, moomoo]

    result, source = manager.get_intraday_data(
        "AAPL",
        interval=period,
        days=3,
    )

    assert source == "MoomooFetcher"
    assert result["date"].tolist() == ["2026-07-21T09:30:00-04:00"]
    assert moomoo.calls == [
        {"stock_code": "AAPL", "interval": period, "days": 3}
    ]
    assert yfinance.calls == []


@pytest.mark.parametrize("period", ["15m", "30m"])
def test_intraday_manager_falls_back_to_yfinance_for_native_period(period):
    class _MoomooFetcher:
        name = "MoomooFetcher"
        priority = 1

        def fetch_intraday(self, **_kwargs):
            raise data_provider_base.DataFetchError("OpenD unavailable")

    class _YfinanceFetcher:
        name = "YfinanceFetcher"
        priority = 4

        def __init__(self):
            self.calls = []

        def fetch_intraday(self, **kwargs):
            self.calls.append(kwargs)
            return _intraday_frame()

    yfinance = _YfinanceFetcher()
    manager = data_provider_base.DataFetcherManager.__new__(
        data_provider_base.DataFetcherManager
    )
    manager._fetchers = [_MoomooFetcher(), yfinance]

    result, source = manager.get_intraday_data(
        "AAPL",
        interval=period,
        days=3,
    )

    assert source == "YfinanceFetcher"
    assert result["date"].tolist() == ["2026-07-21T09:30:00-04:00"]
    assert yfinance.calls == [
        {"stock_code": "AAPL", "interval": period, "days": 3}
    ]


def test_two_minute_history_derives_timezone_aware_ohlcv_from_one_minute(
    monkeypatch,
):
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-21T09:30:00-04:00",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 100,
                "amount": 1000,
            },
            {
                "date": "2026-07-21T09:31:00-04:00",
                "open": 11,
                "high": 13,
                "low": 10,
                "close": 12,
                "volume": 150,
                "amount": 1800,
            },
            {
                "date": "2026-07-21T09:32:00-04:00",
                "open": 12,
                "high": 14,
                "low": 11,
                "close": 13,
                "volume": 200,
                "amount": 2600,
            },
            {
                "date": "2026-07-21T11:59:00-04:00",
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20.5,
                "volume": 50,
                "amount": 1025,
            },
            {
                "date": "2026-07-21T13:00:00-04:00",
                "open": 30,
                "high": 31,
                "low": 29,
                "close": 30.5,
                "volume": 60,
                "amount": 1830,
            },
        ]
    )

    class _IntradayManager:
        requested = None

        def get_intraday_data(self, stock_code: str, *, interval: str, days: int):
            self.requested = (stock_code, interval, days)
            return frame, "MoomooFetcher"

        def get_stock_name(self, stock_code: str):
            return "Apple"

    manager = _IntradayManager()
    monkeypatch.setattr(
        data_provider_base,
        "DataFetcherManager",
        lambda: manager,
    )

    result = _service().get_history_data("AAPL", period="2m", days=1)

    assert manager.requested == ("AAPL", "1m", 1)
    assert result["period"] == "2m"
    assert result["source"] == "MoomooFetcher"
    assert result["derived_from_period"] == "1m"
    assert result["aggregation_method"] == "time_bucket_2m_ohlcv"
    assert result["coverage_start"] == "2026-07-21T09:30:00-04:00"
    assert result["coverage_end"] == "2026-07-21T13:00:00-04:00"

    bars = result["data"]
    assert [bar["date"] for bar in bars] == [
        "2026-07-21T09:30:00-04:00",
        "2026-07-21T09:32:00-04:00",
        "2026-07-21T11:58:00-04:00",
        "2026-07-21T13:00:00-04:00",
    ]
    assert bars[0] == {
        "date": "2026-07-21T09:30:00-04:00",
        "open": 10.0,
        "high": 13.0,
        "low": 9.0,
        "close": 12.0,
        "volume": 250.0,
        "amount": 2800.0,
        "change_percent": 0.0,
    }
    assert bars[1]["change_percent"] == 8.33
    assert bars[2]["close"] == 20.5
    assert bars[3]["open"] == 30.0


def test_two_minute_history_preserves_market_offsets_across_dst(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "date": "2026-03-06T09:30:00-05:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
            },
            {
                "date": "2026-03-09T09:30:00-04:00",
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20.5,
            },
        ]
    )

    class _IntradayManager:
        def get_intraday_data(self, *_args, **_kwargs):
            return frame, "MoomooFetcher"

        def get_stock_name(self, _stock_code: str):
            return "Apple"

    monkeypatch.setattr(
        data_provider_base,
        "DataFetcherManager",
        _IntradayManager,
    )

    result = _service().get_history_data("AAPL", period="2m", days=7)

    assert [bar["date"] for bar in result["data"]] == [
        "2026-03-06T09:30:00-05:00",
        "2026-03-09T09:30:00-04:00",
    ]


def test_empty_two_minute_history_still_discloses_derivation(monkeypatch):
    class _IntradayManager:
        def get_intraday_data(self, stock_code: str, *, interval: str, days: int):
            assert (stock_code, interval, days) == ("AAPL", "1m", 1)
            return pd.DataFrame(), "MoomooFetcher"

    monkeypatch.setattr(
        data_provider_base,
        "DataFetcherManager",
        _IntradayManager,
    )

    result = _service().get_history_data("AAPL", period="2m", days=1)

    assert result["period"] == "2m"
    assert result["data"] == []
    assert result["source"] is None
    assert result["coverage_start"] is None
    assert result["derived_from_period"] == "1m"
    assert result["aggregation_method"] == "time_bucket_2m_ohlcv"


def test_history_endpoint_maps_optional_provenance(monkeypatch):
    class _FakeService:
        def get_history_data(self, **_kwargs):
            return {
                "stock_code": "AAPL",
                "stock_name": "Apple",
                "period": "daily",
                "source": "YfinanceFetcher",
                "coverage_start": "2026-07-18",
                "coverage_end": "2026-07-21",
                "last_bar_at": "2026-07-21",
                "data": [],
            }

    monkeypatch.setattr(stocks_endpoint, "StockService", _FakeService)
    app = FastAPI()
    app.include_router(stocks_endpoint.router, prefix="/api/v1/stocks")

    response = TestClient(app).get("/api/v1/stocks/AAPL/history")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "YfinanceFetcher"
    assert body["coverage_start"] == "2026-07-18"
    assert body["coverage_end"] == "2026-07-21"
    assert body["last_bar_at"] == "2026-07-21"
    assert body["derived_from_period"] is None
    assert body["aggregation_method"] is None


def test_history_endpoint_accepts_two_minute_derived_period(monkeypatch):
    class _FakeService:
        def get_history_data(self, **kwargs):
            assert kwargs == {
                "stock_code": "AAPL",
                "period": "2m",
                "days": 1,
            }
            return {
                "stock_code": "AAPL",
                "stock_name": "Apple",
                "period": "2m",
                "source": "MoomooFetcher",
                "coverage_start": "2026-07-21T09:30:00-04:00",
                "coverage_end": "2026-07-21T09:30:00-04:00",
                "last_bar_at": "2026-07-21T09:30:00-04:00",
                "derived_from_period": "1m",
                "aggregation_method": "time_bucket_2m_ohlcv",
                "data": [],
            }

    monkeypatch.setattr(stocks_endpoint, "StockService", _FakeService)
    app = FastAPI()
    app.include_router(stocks_endpoint.router, prefix="/api/v1/stocks")

    response = TestClient(app).get(
        "/api/v1/stocks/AAPL/history",
        params={"period": "2m", "days": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["period"] == "2m"
    assert body["derived_from_period"] == "1m"
    assert body["aggregation_method"] == "time_bucket_2m_ohlcv"


@pytest.mark.parametrize("period", ["15m", "30m"])
def test_history_endpoint_accepts_native_intraday_periods(monkeypatch, period):
    class _FakeService:
        def get_history_data(self, **kwargs):
            assert kwargs == {
                "stock_code": "AAPL",
                "period": period,
                "days": 3,
            }
            return {
                "stock_code": "AAPL",
                "stock_name": "Apple",
                "period": period,
                "source": "MoomooFetcher",
                "coverage_start": "2026-07-21T09:30:00-04:00",
                "coverage_end": "2026-07-21T10:00:00-04:00",
                "last_bar_at": "2026-07-21T10:00:00-04:00",
                "derived_from_period": None,
                "aggregation_method": None,
                "data": [],
            }

    monkeypatch.setattr(stocks_endpoint, "StockService", _FakeService)
    app = FastAPI()
    app.include_router(stocks_endpoint.router, prefix="/api/v1/stocks")

    response = TestClient(app).get(
        "/api/v1/stocks/AAPL/history",
        params={"period": period, "days": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["period"] == period
    assert body["source"] == "MoomooFetcher"
    assert body["derived_from_period"] is None
    assert body["aggregation_method"] is None


def test_history_reuses_one_process_wide_fetcher_manager(monkeypatch):
    """G-7d：轮询/每标的不再各建一个 DataFetcherManager（= 各开一条 OpenD 连接）。

    共享 manager 按类身份缓存：同一个（monkeypatch 后的）类只构建一次；
    换一个类（新一次 monkeypatch）自动重建，测试之间互不串味。
    """

    import src.services.stock_service as stock_service_module

    frame = pd.DataFrame(
        [{"date": "2026-07-21", "open": 3, "high": 4, "low": 2, "close": 3.5}]
    )
    constructions = []

    class _CountingManager(_DailyManager):
        def __init__(self):
            super().__init__(frame, "YfinanceFetcher")
            constructions.append(self)

    monkeypatch.setattr(
        data_provider_base, "DataFetcherManager", _CountingManager
    )

    service = _service()
    first = service.get_history_data("AAPL", period="daily", days=30)
    second = service.get_history_data("AAPL", period="daily", days=30)

    assert first["source"] == second["source"] == "YfinanceFetcher"
    assert len(constructions) == 1
    assert (
        stock_service_module._get_shared_fetcher_manager() is constructions[0]
    )

    # 换一个类（模拟下一个测试重新 monkeypatch）：立即重建，不复用旧实例。
    class _ReplacementManager(_CountingManager):
        pass

    monkeypatch.setattr(
        data_provider_base, "DataFetcherManager", _ReplacementManager
    )
    rebuilt = stock_service_module._get_shared_fetcher_manager()
    assert isinstance(rebuilt, _ReplacementManager)
    assert rebuilt is not constructions[0]
