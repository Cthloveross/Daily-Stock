from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

import data_provider.moomoo_fetcher as moomoo_fetcher_module
from data_provider.base import DataFetchError
from data_provider.moomoo_fetcher import MoomooFetcher


class _PagedContext:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request_history_kline(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _install_sdk(monkeypatch) -> None:
    sdk = ModuleType("moomoo")
    sdk.KLType = SimpleNamespace(
        K_5M="K_5M",
        K_15M="K_15M",
        K_30M="K_30M",
    )
    sdk.AuType = SimpleNamespace(QFQ="QFQ")
    sdk.KL_FIELD = SimpleNamespace(ALL="ALL")
    sdk.RET_OK = 0
    monkeypatch.setitem(sys.modules, "moomoo", sdk)


def _frame(*rows: tuple[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time_key": time_key,
                "open": close - 0.5,
                "high": close + 0.5,
                "low": close - 1,
                "close": close,
                "volume": 10,
            }
            for time_key, close in rows
        ]
    )


def _fetch(context: _PagedContext, interval: str = "5m") -> pd.DataFrame:
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: context
    return fetcher.fetch_intraday("AAPL", interval, days=24)


@pytest.mark.parametrize(
    ("interval", "expected_ktype"),
    [
        ("5m", "K_5M"),
        ("15m", "K_15M"),
        ("30m", "K_30M"),
    ],
)
def test_intraday_pagination_merges_deduplicates_and_sorts(
    monkeypatch,
    interval: str,
    expected_ktype: str,
) -> None:
    _install_sdk(monkeypatch)
    context = _PagedContext(
        [
            (
                0,
                _frame(
                    ("2026-07-01 14:35:00", 11),
                    ("2026-07-01 14:30:00", 10),
                ),
                b"page-2",
            ),
            (
                0,
                _frame(
                    ("2026-07-01 14:40:00", 12),
                    ("2026-07-01 14:35:00", 99),
                ),
                None,
            ),
        ]
    )

    result = _fetch(context, interval)

    assert result["date"].tolist() == [
        "2026-07-01T14:30:00-04:00",
        "2026-07-01T14:35:00-04:00",
        "2026-07-01T14:40:00-04:00",
    ]
    assert result["close"].tolist() == [10, 99, 12]
    assert len(context.calls) == 2
    assert "page_req_key" not in context.calls[0]
    assert context.calls[1]["page_req_key"] == b"page-2"
    assert all(call["extended_time"] is True for call in context.calls)
    assert all(call["max_count"] == 1000 for call in context.calls)
    assert all(call["ktype"] == expected_ktype for call in context.calls)


def test_intraday_pagination_rejects_a_failed_later_page(monkeypatch) -> None:
    _install_sdk(monkeypatch)
    context = _PagedContext(
        [
            (0, _frame(("2026-07-01 14:30:00", 10)), b"page-2"),
            (1, "permission denied", None),
        ]
    )

    with pytest.raises(DataFetchError, match="page 2 failed"):
        _fetch(context)

    assert len(context.calls) == 2
    assert context.calls[1]["page_req_key"] == b"page-2"
    assert context.calls[1]["extended_time"] is True


def test_intraday_pagination_rejects_repeated_continuation_key(
    monkeypatch,
) -> None:
    _install_sdk(monkeypatch)
    context = _PagedContext(
        [
            (0, _frame(("2026-07-01 14:30:00", 10)), b"repeat"),
            (0, _frame(("2026-07-01 14:35:00", 11)), b"repeat"),
        ]
    )

    with pytest.raises(DataFetchError, match="repeated continuation key"):
        _fetch(context)

    assert len(context.calls) == 2


def test_intraday_pagination_enforces_page_cap(monkeypatch) -> None:
    _install_sdk(monkeypatch)
    monkeypatch.setattr(moomoo_fetcher_module, "_INTRADAY_MAX_PAGES", 2)
    context = _PagedContext(
        [
            (0, _frame(("2026-07-01 14:30:00", 10)), b"page-2"),
            (0, _frame(("2026-07-01 14:35:00", 11)), b"page-3"),
        ]
    )

    with pytest.raises(DataFetchError, match="safe pagination limit"):
        _fetch(context)

    assert len(context.calls) == 2
