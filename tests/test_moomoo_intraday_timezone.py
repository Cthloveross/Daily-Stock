from __future__ import annotations

import pandas as pd

from data_provider.moomoo_fetcher import MoomooFetcher


def _normalize(stock_code: str, *time_keys: str) -> list[str]:
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    frame = pd.DataFrame(
        {
            "time_key": list(time_keys),
            "open": [100.0] * len(time_keys),
            "high": [101.0] * len(time_keys),
            "low": [99.0] * len(time_keys),
            "close": [100.5] * len(time_keys),
            "volume": [10] * len(time_keys),
        }
    )

    return fetcher._normalize_intraday(frame, stock_code)["date"].tolist()


def test_us_market_time_includes_dst_aware_exchange_offset() -> None:
    assert _normalize(
        "AAPL",
        "2026-07-20 14:35:00",
        "2026-01-15 14:35:00",
    ) == [
        "2026-07-20T14:35:00-04:00",
        "2026-01-15T14:35:00-05:00",
    ]


def test_hong_kong_and_mainland_market_times_include_local_offset() -> None:
    assert _normalize("hk00700", "2026-07-20 14:35:00") == [
        "2026-07-20T14:35:00+08:00"
    ]
    assert _normalize("600519", "2026-07-20 14:35:00") == [
        "2026-07-20T14:35:00+08:00"
    ]
    assert _normalize("000001", "2026-07-20 14:35:00") == [
        "2026-07-20T14:35:00+08:00"
    ]
    assert _normalize("830799", "2026-07-20 14:35:00") == [
        "2026-07-20T14:35:00+08:00"
    ]


def test_timezone_aware_input_is_converted_to_exchange_time() -> None:
    assert _normalize(
        "AAPL",
        "2026-07-20 14:30:00",
        "2026-07-20T18:35:00+00:00",
    ) == [
        "2026-07-20T14:30:00-04:00",
        "2026-07-20T14:35:00-04:00",
    ]


def test_invalid_intraday_time_is_not_exposed_as_a_bar() -> None:
    assert _normalize("AAPL", "not-a-time", "2026-07-20 14:35:00") == [
        "2026-07-20T14:35:00-04:00"
    ]
