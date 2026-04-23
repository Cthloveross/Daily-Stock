# -*- coding: utf-8 -*-
"""IV Rank tests with yfinance mocked out."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.options.iv_rank import compute_atm_iv, compute_iv_rank


class FakeChain:
    def __init__(self, calls_df):
        self.calls = calls_df
        self.puts = calls_df  # irrelevant for ATM IV extraction


def _mock_ticker(expirations=None, info=None, calls=None, history=None):
    tkr = MagicMock()
    tkr.options = expirations or []
    tkr.info = info or {}
    if calls is not None:
        tkr.option_chain.return_value = FakeChain(calls)
    if history is not None:
        tkr.history.return_value = history
    return tkr


class TestComputeAtmIv:
    def test_no_yfinance_returns_none(self):
        with patch("src.options.iv_rank._get_yfinance_ticker", return_value=None):
            iv, exp = compute_atm_iv("NVDA")
            assert iv is None
            assert exp == ""

    def test_no_expirations_returns_none(self):
        tkr = _mock_ticker(expirations=[])
        with patch("src.options.iv_rank._get_yfinance_ticker", return_value=tkr):
            iv, exp = compute_atm_iv("NVDA")
            assert iv is None

    def test_picks_nearest_future_expiry(self):
        import pandas as pd

        today = date.today()
        future = today.replace(year=today.year + 1).isoformat()
        past = today.replace(year=today.year - 1).isoformat()
        calls_df = pd.DataFrame(
            {"strike": [95.0, 100.0, 105.0], "impliedVolatility": [0.40, 0.35, 0.45]}
        )
        tkr = _mock_ticker(
            expirations=[past, future],
            info={"regularMarketPrice": 100.0},
            calls=calls_df,
        )
        with patch("src.options.iv_rank._get_yfinance_ticker", return_value=tkr):
            iv, exp = compute_atm_iv("NVDA")
            assert iv == pytest.approx(0.35)
            assert exp == future


class TestComputeIvRank:
    def test_fallback_when_no_history(self):
        with patch("src.options.iv_rank._get_yfinance_ticker", return_value=None):
            res = compute_iv_rank("NVDA")
            assert res.rank_pct is None
            assert res.sample_size == 0
            assert res.source == "hv_fallback"

    def test_rank_with_mocked_hv_series(self):
        # Make ATM IV lookup return None so the rank collapses to pure-HV comparison
        # against the current HV value (last element of the series), giving rank = 100%.
        with patch("src.options.iv_rank.compute_atm_iv", return_value=(None, "")):
            fake_series = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
            with patch(
                "src.options.iv_rank._compute_hv",
                return_value=(fake_series[-1], fake_series),
            ):
                res = compute_iv_rank("NVDA", days_window=252)
                assert res.rank_pct == pytest.approx(100.0)
                assert res.sample_size == len(fake_series)
