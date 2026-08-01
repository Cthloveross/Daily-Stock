# -*- coding: utf-8 -*-
"""Deterministic tests for Alpaca premarket reference semantics."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from data_provider.alpaca_fetcher import AlpacaFetcher


def _response_for(bars):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"bars": bars}
    return response


def test_premarket_uses_prior_completed_daily_close_and_completed_minutes():
    minute_bars = [
        # Outside the proved 04:00 ET window.
        {"t": "2026-04-17T07:59:00Z", "o": 80.0, "c": 80.0},
        {"t": "2026-04-17T08:00:00Z", "o": 100.5, "c": 101.0},
        {"t": "2026-04-17T13:11:00Z", "o": 101.9, "c": 102.0},
        # 09:12 ET is still in progress at the requested 09:12:45 cutoff.
        {"t": "2026-04-17T13:12:00Z", "o": 102.0, "c": 120.0},
        # Regular-session bar must never be eligible.
        {"t": "2026-04-17T13:30:00Z", "o": 130.0, "c": 130.0},
    ]
    daily_bars = [
        {"t": "2026-04-15T04:00:00Z", "c": 99.0},
        {"t": "2026-04-16T04:00:00Z", "c": 100.0},
        # A provider returning a current-date row must not change the reference.
        {"t": "2026-04-17T04:00:00Z", "c": 50.0},
    ]

    def request_side_effect(*_args, **kwargs):
        timeframe = kwargs["params"]["timeframe"]
        return _response_for(minute_bars if timeframe == "1Min" else daily_bars)

    with patch(
        "data_provider.alpaca_fetcher.requests.get",
        side_effect=request_side_effect,
    ) as request:
        result = AlpacaFetcher(api_key="key", api_secret="secret").get_premarket(
            "spy",
            target_date=date(2026, 4, 17),
            as_of=datetime(2026, 4, 17, 13, 12, 45, tzinfo=timezone.utc),
        )

    assert result["_status"] == "ready"
    assert result["price"] == 102.0
    assert result["previous_close"] == 100.0
    assert result["previous_close_date"] == "2026-04-16"
    assert result["pct_change"] == 2.0
    assert result["as_of"] == "2026-04-17T13:12:00+00:00"
    # This would be about 0.1% under the old, incorrect minute-open formula.
    assert result["pct_change"] != (102.0 - 101.9) / 101.9 * 100.0

    minute_params = request.call_args_list[0].kwargs["params"]
    assert minute_params["start"] == "2026-04-17T08:00:00Z"
    assert minute_params["end"] == "2026-04-17T13:12:00Z"
    daily_params = request.call_args_list[1].kwargs["params"]
    assert daily_params["end"] == "2026-04-17T08:00:00Z"


def test_premarket_missing_prior_close_is_explicitly_degraded():
    responses = [
        _response_for([
            {"t": "2026-04-17T13:11:00Z", "o": 101.9, "c": 102.0},
        ]),
        _response_for([]),
    ]
    with patch(
        "data_provider.alpaca_fetcher.requests.get",
        side_effect=responses,
    ):
        result = AlpacaFetcher(api_key="key", api_secret="secret").get_premarket(
            "SPY",
            target_date=date(2026, 4, 17),
            as_of=datetime(2026, 4, 17, 13, 12, 45, tzinfo=timezone.utc),
        )

    assert result["_status"] == "degraded"
    assert result["_reason"] == "previous_completed_close_missing"
    assert result["price"] == 102.0
    assert result["previous_close"] is None
    assert result["pct_change"] is None


def test_premarket_rejects_a_non_previous_session_daily_bar():
    responses = [
        _response_for([
            {"t": "2026-04-17T13:11:00Z", "o": 101.9, "c": 102.0},
        ]),
        # April 16 is the exact prior XNYS session; an older close is not an
        # acceptable substitute because it could manufacture a false gap.
        _response_for([{"t": "2026-04-15T04:00:00Z", "c": 100.0}]),
    ]
    with patch(
        "data_provider.alpaca_fetcher.requests.get",
        side_effect=responses,
    ):
        result = AlpacaFetcher(api_key="key", api_secret="secret").get_premarket(
            "SPY",
            target_date=date(2026, 4, 17),
            as_of=datetime(2026, 4, 17, 13, 12, 45, tzinfo=timezone.utc),
        )

    assert result["_status"] == "degraded"
    assert result["_reason"] == "previous_completed_close_missing"
    assert result["pct_change"] is None


def test_premarket_rejects_a_stale_last_minute():
    responses = [
        _response_for([
            {"t": "2026-04-17T12:59:00Z", "o": 101.9, "c": 102.0},
        ]),
        _response_for([{"t": "2026-04-16T04:00:00Z", "c": 100.0}]),
    ]
    with patch(
        "data_provider.alpaca_fetcher.requests.get",
        side_effect=responses,
    ):
        result = AlpacaFetcher(api_key="key", api_secret="secret").get_premarket(
            "SPY",
            target_date=date(2026, 4, 17),
            as_of=datetime(2026, 4, 17, 13, 12, 45, tzinfo=timezone.utc),
        )

    assert result["_status"] == "degraded"
    assert result["_reason"] == "premarket_bar_stale"
    assert result["as_of"] == "2026-04-17T13:00:00+00:00"
    assert result["pct_change"] is None


def test_premarket_before_0400_et_does_not_make_a_request():
    with patch("data_provider.alpaca_fetcher.requests.get") as request:
        result = AlpacaFetcher(api_key="key", api_secret="secret").get_premarket(
            "SPY",
            target_date=date(2026, 4, 17),
            as_of=datetime(2026, 4, 17, 7, 59, tzinfo=timezone.utc),
        )

    assert result["_status"] == "unavailable"
    assert result["_reason"] == "premarket_not_started_or_no_completed_bar"
    assert result["pct_change"] is None
    request.assert_not_called()


def test_premarket_permission_failure_is_not_reported_as_no_bar():
    response = MagicMock()
    response.raise_for_status.side_effect = RuntimeError("403 Forbidden")
    with patch(
        "data_provider.alpaca_fetcher.requests.get",
        return_value=response,
    ):
        fetcher = AlpacaFetcher(api_key="key", api_secret="secret")
        result = fetcher.get_premarket(
            "SPY",
            target_date=date(2026, 4, 17),
            as_of=datetime(2026, 4, 17, 13, 12, 45, tzinfo=timezone.utc),
        )

    assert result["_status"] == "unavailable"
    assert result["_reason"] == "premarket_minute_permission_denied"
    assert result["pct_change"] is None
    assert fetcher.request_succeeded("bars") is False
    assert "403 Forbidden" in (fetcher.last_request_error("bars") or "")


def test_unconfigured_premarket_remains_a_safe_empty_fallback():
    fetcher = AlpacaFetcher(api_key="", api_secret="")
    with patch.dict(
        "os.environ",
        {"APCA_API_KEY_ID": "", "APCA_API_SECRET_KEY": ""},
        clear=False,
    ):
        fetcher.api_key = None
        fetcher.api_secret = None
        assert fetcher.get_premarket("SPY") == {}
