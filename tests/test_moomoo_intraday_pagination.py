from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

import data_provider.moomoo_fetcher as moomoo_fetcher_module
from data_provider.base import DataFetchError
from data_provider.moomoo_fetcher import MoomooFetcher
from src.services.moomoo_runtime import MOOMOO_RPC_BREAKER


@pytest.fixture(autouse=True)
def _reset_moomoo_breaker():
    """共享断路器状态不得跨用例泄漏（含用桩故意制造的失败）。"""

    MOOMOO_RPC_BREAKER.reset_for_tests()
    yield
    MOOMOO_RPC_BREAKER.reset_for_tests()



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


# --- G-7e：RPC 与健康检查 close() 持同一把生命周期锁 + 断路器快速失败 --------


def test_close_waits_for_in_flight_realtime_rpc(monkeypatch) -> None:
    """close() 不得在另一线程的 RPC 执行中途关闭同一个 ctx（锁内 RPC）。"""

    import threading
    from concurrent.futures import ThreadPoolExecutor

    _install_sdk(monkeypatch)
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class _BlockingCtx:
        def get_market_snapshot(self, codes):
            started.set()
            assert release.wait(timeout=2)
            return 0, pd.DataFrame(
                [{"code": "US.AAPL", "last_price": 100.0, "volume": 10}]
            )

        def close(self):
            closed.set()

    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher.enabled = True
    fetcher._sdk_ok = True
    fetcher.host, fetcher.port = "127.0.0.1", 11111
    fetcher._ctx = _BlockingCtx()
    monkeypatch.setattr(
        moomoo_fetcher_module, "probe_opend_tcp", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        moomoo_fetcher_module, "quote_context_is_ready", lambda ctx: True
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        rpc_future = pool.submit(fetcher.get_realtime_quote, "AAPL")
        assert started.wait(timeout=2)
        close_future = pool.submit(fetcher.close)
        # RPC 持锁期间 close() 必须等待，绝不中途拆连接。
        assert closed.wait(timeout=0.2) is False
        release.set()
        assert rpc_future.result(timeout=2) is not None
        close_future.result(timeout=2)
        assert closed.is_set()
    assert fetcher._ctx is None


def test_history_kline_fails_fast_while_breaker_is_open(monkeypatch) -> None:
    """断路器打开：history kline 直接 DataFetchError（交给下游数据源 fallback）。"""

    _install_sdk(monkeypatch)
    for _ in range(3):
        MOOMOO_RPC_BREAKER.record_failure("request timeout")
    context = _PagedContext([])

    with pytest.raises(DataFetchError, match="断路器"):
        _fetch(context)
    assert context.calls == []


def test_realtime_quote_fails_fast_while_breaker_is_open(monkeypatch) -> None:
    _install_sdk(monkeypatch)
    for _ in range(3):
        MOOMOO_RPC_BREAKER.record_failure("request timeout")

    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher.enabled = True
    fetcher._sdk_ok = True
    fetcher._get_ctx = lambda: pytest.fail(
        "open breaker must not reach the quote context"
    )

    assert fetcher.get_realtime_quote("AAPL", log_final_failure=False) is None
