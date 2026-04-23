# -*- coding: utf-8 -*-
"""Data-fetcher unit tests (everything mocked)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from src.regime.fetchers import RegimeDataFetcher


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


class TestMacroEvents:
    def test_no_finnhub_returns_empty_defaults(self):
        f = RegimeDataFetcher(finnhub=None)
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["fomc_today"] is False
        assert ev["cpi_today"] is False
        assert ev["earnings_count_watchlist"] == 0

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
        f = RegimeDataFetcher(finnhub=finnhub)
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["fomc_today"] is True
        assert ev["earnings_count_watchlist"] == 1

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
        f = RegimeDataFetcher(finnhub=finnhub)
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["cpi_today"] is False
        assert ev["fomc_today"] is False


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
