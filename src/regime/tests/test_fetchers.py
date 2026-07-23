# -*- coding: utf-8 -*-
"""Data-fetcher unit tests (everything mocked)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from src.regime.fetchers import RegimeDataFetcher, SECTOR_ETFS


class TestSpySnapshot:
    def test_empty_when_yf_missing(self):
        f = RegimeDataFetcher(yf=None)
        assert f.get_spy_snapshot(date(2026, 4, 17)) == {}

    def test_computes_ma_and_5d(self):
        # 60 closes with a gentle uptrend.
        closes = [100.0 + i * 0.1 for i in range(60)]
        import pandas as pd

        hist = pd.DataFrame({"Close": closes})
        tkr = MagicMock()
        tkr.history.return_value = hist
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = tkr
        f = RegimeDataFetcher(yf=yf_mock)
        snap = f.get_spy_snapshot(date(2026, 4, 17))
        assert "close" in snap
        assert snap["ma20"] is not None
        assert snap["pct_change_5d"] > 0
        assert snap["_status"] == "ready"


class TestVix:
    def test_vix_history_used(self):
        import pandas as pd

        hist = pd.DataFrame({"Close": [12.0] * 10 + [15.0]})
        tkr = MagicMock()
        tkr.history.return_value = hist
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = tkr
        f = RegimeDataFetcher(yf=yf_mock)
        snap = f.get_vix(date(2026, 4, 17))
        assert snap["level"] == 15.0
        assert snap["pct_change_5d"] > 0
        assert snap["_status"] == "ready"


class TestMacroEvents:
    def test_no_finnhub_returns_empty_defaults(self):
        f = RegimeDataFetcher(finnhub=None, manager=None)
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["fomc_today"] is False
        assert ev["cpi_today"] is False
        assert ev["earnings_count_watchlist"] == 0
        assert ev["_status"] == "unavailable"

    def test_fomc_detected(self):
        finnhub = MagicMock()
        finnhub.configured = True
        finnhub.get_economic_calendar.return_value = [
            {"event": "Federal Funds Rate", "country": "US"},
        ]
        finnhub.get_earnings_calendar.return_value = [
            {"symbol": "NVDA"},
            {"symbol": "AAPL"},
        ]
        f = RegimeDataFetcher(finnhub=finnhub, manager=None)
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["fomc_today"] is True
        assert ev["earnings_count_watchlist"] == 1
        finnhub.get_economic_calendar.assert_called_once_with(
            date(2026, 4, 17), date(2026, 4, 24)
        )
        finnhub.get_earnings_calendar.assert_called_once_with(
            date(2026, 4, 17), date(2026, 4, 24)
        )

    def test_non_us_cpi_ignored(self):
        """Regression: Canadian CPI release must NOT trigger d3 penalty for US regime."""
        finnhub = MagicMock()
        finnhub.configured = True
        finnhub.get_economic_calendar.return_value = [
            {"event": "CPI Common YoY", "country": "CA"},
            {"event": "CPI Median YoY", "country": "CA"},
            {"event": "BoC Interest Rate Decision", "country": "CA"},
        ]
        finnhub.get_earnings_calendar.return_value = []
        f = RegimeDataFetcher(finnhub=finnhub, manager=None)
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["cpi_today"] is False
        assert ev["fomc_today"] is False

    def test_permission_failure_is_unavailable_not_a_clean_calendar(self):
        class ForbiddenFinnhub:
            configured = True

            def __init__(self):
                self.calls = []

            def get_economic_calendar(self, from_, to):
                self.calls.append(("economic", from_, to))
                return []

            def get_earnings_calendar(self, from_, to):
                self.calls.append(("earnings", from_, to))
                return []

            def request_succeeded(self, operation):
                return False

        finnhub = ForbiddenFinnhub()
        f = RegimeDataFetcher(
            finnhub=finnhub,
            manager=None,
            request_budget_seconds=5,
        )
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["_status"] == "unavailable"
        assert ev["fomc_today"] is False
        assert [call[0] for call in finnhub.calls] == ["economic", "earnings"]

    def test_finnhub_adapter_records_http_permission_failure(self):
        import requests

        from data_provider.finnhub_fetcher import FinnhubFetcher

        response = MagicMock()
        response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        with patch(
            "data_provider.finnhub_fetcher.requests.get",
            return_value=response,
        ):
            finnhub = FinnhubFetcher(api_key="test", timeout=0.1)
            rows = finnhub.get_economic_calendar(
                date(2026, 4, 17), date(2026, 4, 24)
            )

        assert rows == []
        assert finnhub.request_succeeded("economic_calendar") is False
        assert "403 Forbidden" in (
            finnhub.last_request_error("economic_calendar") or ""
        )


class TestSectorPerformance:
    def test_counts_sectors_above_ma20(self):
        import pandas as pd

        # Two scenarios: half of sectors above MA20.
        def make_history(above_ma: bool):
            if above_ma:
                closes = list(range(100, 130))  # trending up, last > MA20
            else:
                closes = list(range(130, 100, -1))  # trending down
            return pd.DataFrame({"Close": [float(c) for c in closes]})

        yf_mock = MagicMock()

        counter = {"n": 0}

        def ticker_side_effect(sym):
            counter["n"] += 1
            t = MagicMock()
            # First 6 symbols "above MA20", next 5 below.
            t.history.return_value = make_history(counter["n"] <= 6)
            return t

        yf_mock.Ticker.side_effect = ticker_side_effect
        f = RegimeDataFetcher(yf=yf_mock)
        perf = f.get_sector_performance(date(2026, 4, 17))
        assert perf["sectors_above_ma20"] == 6
        assert perf["total_sectors_seen"] == 11
        assert perf["_status"] == "ready"


class TestPrevDay:
    def test_prev_day_structure(self):
        import pandas as pd

        idx = pd.date_range(end="2026-04-16", periods=6, freq="D")
        hist = pd.DataFrame(
            {
                "Low": [490.0] * 6,
                "High": [510.0] * 6,
                "Close": [508.0] * 5 + [508.0],
            },
            index=idx,
        )
        tkr = MagicMock()
        tkr.history.return_value = hist
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = tkr
        f = RegimeDataFetcher(yf=yf_mock)
        prev = f.get_prev_day_structure(date(2026, 4, 17))
        assert "close_vs_high_pct" in prev
        assert 0.85 <= prev["close_vs_high_pct"] <= 1.0
        assert prev["_status"] == "ready"


class TestPremarket:
    def test_unconfigured_source_is_explicitly_unavailable(self):
        f = RegimeDataFetcher(alpaca=None, manager=None)
        snapshot = f.get_premarket_activity(["SPY", "NVDA"], date(2026, 4, 17))
        assert snapshot["_status"] == "unavailable"
        assert snapshot["spy_pre_pct"] == 0.0


class TestMoomooFirstProviderPath:
    @staticmethod
    def _frame(rows=70):
        import pandas as pd

        dates = pd.date_range(end="2026-04-17", periods=rows, freq="B")
        closes = [100.0 + idx for idx in range(rows)]
        return pd.DataFrame(
            {
                "date": dates,
                "open": [value - 1 for value in closes],
                "high": [value + 1 for value in closes],
                "low": [value - 2 for value in closes],
                "close": closes,
                "volume": [1_000_000] * rows,
            }
        )

    def test_reuses_one_moomoo_spy_frame_for_direction_and_prev_day(self):
        class FakeMoomoo:
            name = "MoomooFetcher"
            priority = 2

            def __init__(self):
                self.calls = []

            def get_daily_data(self, **kwargs):
                self.calls.append(kwargs["stock_code"])
                return TestMoomooFirstProviderPath._frame()

        class FakeManager:
            def __init__(self, fetcher):
                self.fetcher = fetcher

            def _get_fetchers_snapshot(self):
                return [self.fetcher]

            def _call_fetcher_method(self, fetcher, method, **kwargs):
                return getattr(fetcher, method)(**kwargs)

        moomoo = FakeMoomoo()
        f = RegimeDataFetcher(
            yf=None,
            manager=FakeManager(moomoo),
            alpaca=None,
            finnhub=None,
        )
        spy = f.get_spy_snapshot(date(2026, 4, 17))
        prev = f.get_prev_day_structure(date(2026, 4, 17))

        assert spy["_source"] == "MoomooFetcher"
        assert prev["_source"] == "MoomooFetcher"
        assert moomoo.calls.count("SPY") == 1

    def test_sector_breadth_uses_moomoo_without_yfinance_fanout(self):
        class FakeMoomoo:
            name = "MoomooFetcher"
            priority = 2

            def get_daily_data(self, **kwargs):
                return TestMoomooFirstProviderPath._frame()

        class FakeManager:
            def __init__(self):
                self.fetcher = FakeMoomoo()

            def _get_fetchers_snapshot(self):
                return [self.fetcher]

            def _call_fetcher_method(self, fetcher, method, **kwargs):
                return getattr(fetcher, method)(**kwargs)

        yf_mock = MagicMock()
        f = RegimeDataFetcher(
            yf=yf_mock,
            manager=FakeManager(),
            alpaca=None,
            finnhub=None,
        )
        sectors = f.get_sector_performance(date(2026, 4, 17))

        assert sectors["_status"] == "ready"
        assert set(sectors["_sources"]) == set(SECTOR_ETFS)
        assert set(sectors["_sources"].values()) == {"MoomooFetcher"}
        yf_mock.Ticker.assert_not_called()

    def test_no_moomoo_does_not_fan_out_sector_yfinance_requests(self):
        class EmptyManager:
            def _get_fetchers_snapshot(self):
                return []

        yf_mock = MagicMock()
        f = RegimeDataFetcher(
            yf=yf_mock,
            manager=EmptyManager(),
            alpaca=None,
            finnhub=None,
        )
        sectors = f.get_sector_performance(date(2026, 4, 17))

        assert sectors["_status"] == "unavailable"
        assert sectors["total_sectors_seen"] == 0
        yf_mock.Ticker.assert_not_called()

    def test_vix_uses_official_cboe_fallback_when_moomoo_has_no_symbol(self):
        class MissingVixMoomoo:
            name = "MoomooFetcher"
            priority = 2

            def get_daily_data(self, **kwargs):
                raise RuntimeError("Unknown stock. VIX")

        class FakeManager:
            def __init__(self):
                self.fetcher = MissingVixMoomoo()

            def _get_fetchers_snapshot(self):
                return [self.fetcher]

            def _call_fetcher_method(self, fetcher, method, **kwargs):
                return getattr(fetcher, method)(**kwargs)

        response = MagicMock()
        response.text = (
            "DATE,OPEN,HIGH,LOW,CLOSE\n"
            "04/08/2026,18,19,17,18\n"
            "04/09/2026,17,18,16,17\n"
            "04/10/2026,16,17,15,16\n"
            "04/13/2026,15,16,14,15\n"
            "04/14/2026,14,15,13,14\n"
            "04/15/2026,13,14,12,13\n"
            "04/16/2026,12,13,11,12\n"
            "04/17/2026,11,12,10,11\n"
        )
        response.raise_for_status.return_value = None
        yf_mock = MagicMock()
        with patch(
            "requests.get",
            return_value=response,
        ):
            f = RegimeDataFetcher(
                yf=yf_mock,
                manager=FakeManager(),
                alpaca=None,
                finnhub=None,
            )
            vix = f.get_vix(date(2026, 4, 17))

        assert vix["_source"] == "Cboe"
        assert vix["_status"] == "ready"
        assert vix["level"] == 11.0
        yf_mock.Ticker.assert_not_called()
