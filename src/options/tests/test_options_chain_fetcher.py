# -*- coding: utf-8 -*-
"""Tests for data_provider.options_chain with yfinance mocked."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from data_provider.options_chain import OptionsChainFetcher


class _FakeChain:
    def __init__(self, calls_df, puts_df):
        self.calls = calls_df
        self.puts = puts_df


def _mock_yfinance(expirations, calls_df, puts_df, spot=100.0):
    yf = MagicMock()
    tkr = MagicMock()
    tkr.options = list(expirations)
    tkr.info = {"regularMarketPrice": spot}
    tkr.option_chain.return_value = _FakeChain(calls_df, puts_df)
    yf.Ticker.return_value = tkr
    return yf, tkr


class TestGetChain:
    def test_empty_when_yfinance_missing(self):
        with patch("data_provider.options_chain._import_yfinance", return_value=None):
            fetcher = OptionsChainFetcher()
            assert fetcher.get_chain("NVDA", "2027-01-15") == []

    def test_returns_calls_and_puts(self):
        import pandas as pd

        calls = pd.DataFrame(
            {
                "strike": [90.0, 100.0, 110.0],
                "bid": [11.5, 3.2, 0.8],
                "ask": [11.8, 3.4, 1.0],
                "lastPrice": [11.6, 3.3, 0.9],
                "volume": [100, 200, 50],
                "openInterest": [1000, 2000, 500],
                "impliedVolatility": [0.40, 0.35, 0.45],
            }
        )
        puts = pd.DataFrame(
            {
                "strike": [90.0, 100.0, 110.0],
                "bid": [0.5, 2.5, 10.5],
                "ask": [0.7, 2.7, 10.8],
                "lastPrice": [0.6, 2.6, 10.7],
                "volume": [30, 80, 10],
                "openInterest": [400, 1500, 300],
                "impliedVolatility": [0.38, 0.33, 0.42],
            }
        )
        yf, _ = _mock_yfinance(["2027-01-15"], calls, puts, spot=100.0)
        with patch("data_provider.options_chain._import_yfinance", return_value=yf):
            fetcher = OptionsChainFetcher()
            quotes = fetcher.get_chain("NVDA", "2027-01-15")
            assert len(quotes) == 6
            rights = {q.right for q in quotes}
            assert rights == {"C", "P"}
            atm_call = next(q for q in quotes if q.right == "C" and q.strike == 100.0)
            assert atm_call.moneyness == "ATM"
            assert atm_call.underlying == "NVDA"
            assert atm_call.implied_volatility == pytest.approx(0.35)

    def test_filter_by_right(self):
        import pandas as pd

        calls = pd.DataFrame(
            {
                "strike": [100.0],
                "bid": [3.0],
                "ask": [3.2],
                "lastPrice": [3.1],
                "volume": [10],
                "openInterest": [20],
                "impliedVolatility": [0.3],
            }
        )
        puts = calls.copy()
        yf, _ = _mock_yfinance(["2027-01-15"], calls, puts)
        with patch("data_provider.options_chain._import_yfinance", return_value=yf):
            quotes = OptionsChainFetcher().get_chain("NVDA", "2027-01-15", right="C")
            assert all(q.right == "C" for q in quotes)
            assert len(quotes) == 1

    def test_cache_hit_skips_second_fetch(self):
        import pandas as pd

        calls = pd.DataFrame(
            {
                "strike": [100.0],
                "bid": [3.0],
                "ask": [3.2],
                "lastPrice": [3.1],
                "volume": [10],
                "openInterest": [20],
                "impliedVolatility": [0.3],
            }
        )
        puts = calls.copy()
        yf, tkr = _mock_yfinance(["2027-01-15"], calls, puts)
        with patch("data_provider.options_chain._import_yfinance", return_value=yf):
            fetcher = OptionsChainFetcher()
            fetcher.get_chain("NVDA", "2027-01-15")
            fetcher.get_chain("NVDA", "2027-01-15")
            # Second call should short-circuit the cache; yfinance's option_chain
            # should only have been hit once.
            assert tkr.option_chain.call_count == 1


class TestLeapCandidates:
    def test_deep_itm_picked_up(self):
        import pandas as pd

        future = (date.today() + timedelta(days=300)).isoformat()
        calls = pd.DataFrame(
            {
                "strike": [80.0, 100.0, 120.0],
                "bid": [20.0, 5.0, 0.5],
                "ask": [20.5, 5.2, 0.7],
                "lastPrice": [20.3, 5.1, 0.6],
                "volume": [100, 100, 10],
                "openInterest": [1000, 1000, 100],
                "impliedVolatility": [0.3, 0.3, 0.3],
            }
        )
        puts = calls.copy()
        yf, _ = _mock_yfinance([future], calls, puts, spot=100.0)
        with patch("data_provider.options_chain._import_yfinance", return_value=yf):
            fetcher = OptionsChainFetcher()
            picks = fetcher.find_leap_candidates("NVDA", min_dte=270)
            # ITM call (strike 80, spot 100) should pass the 0.70 proxy.
            assert any(p.strike == 80.0 for p in picks)
            # Deep OTM should not.
            assert all(p.strike != 120.0 for p in picks)
