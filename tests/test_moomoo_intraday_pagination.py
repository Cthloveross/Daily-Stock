from __future__ import annotations

import sys
import threading
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

import data_provider.moomoo_fetcher as moomoo_fetcher_module
from data_provider.base import DataFetchError
from data_provider.moomoo_fetcher import MoomooFetcher, MoomooHistoryEmptyError
from src.services.moomoo_runtime import MOOMOO_RPC_BREAKER


@pytest.fixture(autouse=True)
def _reset_moomoo_breaker():
    """共享断路器状态不得跨用例泄漏（含用桩故意制造的失败）。"""

    MOOMOO_RPC_BREAKER.reset_for_tests()
    moomoo_fetcher_module._reset_history_kline_pacer_for_tests()
    yield
    MOOMOO_RPC_BREAKER.reset_for_tests()
    moomoo_fetcher_module._reset_history_kline_pacer_for_tests()


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
        K_DAY="K_DAY",
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


def test_explicit_intraday_dates_are_stable_and_guard_every_page(monkeypatch):
    _install_sdk(monkeypatch)
    context = _PagedContext([
        (0, _frame(("2025-01-02 14:35:00", 10)), b"page-2"),
        (0, _frame(("2025-01-02 14:40:00", 11)), None),
    ])
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: context
    checks = []
    result = fetcher.fetch_intraday(
        "AAPL", "5m", start="2025-01-02", end="2025-01-03",
        before_request=lambda: checks.append(True),
    )
    assert len(checks) == 2
    assert all(row["start"] == "2025-01-02" and row["end"] == "2025-01-03" for row in context.calls)
    assert result.attrs["page_count"] == 2
    assert result.attrs["adjustment"] == "QFQ"


@pytest.mark.parametrize("bounds", [
    {"start": "2025-01-02"}, {"end": "2025-01-02"},
    {"start": "2025-01-03", "end": "2025-01-02"},
    {"start": "not-a-date", "end": "2025-01-02"},
])
def test_explicit_intraday_rejects_invalid_bounds_before_request(monkeypatch, bounds):
    _install_sdk(monkeypatch)
    context = _PagedContext([])
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: context
    with pytest.raises(ValueError):
        fetcher.fetch_intraday("AAPL", "5m", **bounds)
    assert context.calls == []


def test_intraday_guard_stops_pagination_without_accepting_partial_rows(monkeypatch):
    _install_sdk(monkeypatch)
    context = _PagedContext([(0, _frame(("2025-01-02 14:35:00", 10)), b"page-2")])
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: context

    def guard():
        if context.calls:
            raise ValueError("scan window started")

    with pytest.raises(ValueError, match="scan window started"):
        fetcher.fetch_intraday("AAPL", "5m", before_request=guard)
    assert len(context.calls) == 1


def test_history_quota_normalizes_existing_codes_without_history_download(monkeypatch):
    _install_sdk(monkeypatch)
    calls = []

    class Context:
        def get_history_kl_quota(self, **kwargs):
            calls.append(kwargs)
            return 0, (2, 67, [{"code": "US.AAPL"}, {"code": "US.TSLA"}])

    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: Context()
    assert fetcher.get_history_quota() == {
        "used_quota": 2, "remain_quota": 67,
        "downloaded_codes": ["US.AAPL", "US.TSLA"],
    }
    assert calls == [{"get_detail": True}]


@pytest.mark.parametrize("daily", [False, True])
def test_only_successfully_completed_empty_history_has_gap_exception(monkeypatch, daily):
    _install_sdk(monkeypatch)
    context = _PagedContext([(0, pd.DataFrame(), None)])
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: context
    with pytest.raises(MoomooHistoryEmptyError):
        if daily:
            fetcher._fetch_raw_data("AAPL", "2025-01-02", "2025-01-03")
        else:
            fetcher.fetch_intraday("AAPL", "5m")


@pytest.mark.parametrize("response", [
    (1, "no permission", None),
    (0, pd.DataFrame(), b"next-page"),
])
@pytest.mark.parametrize("daily", [False, True])
def test_permission_or_incomplete_pagination_is_not_empty_history(monkeypatch, response, daily):
    _install_sdk(monkeypatch)
    context = _PagedContext([response])
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: context
    with pytest.raises(DataFetchError) as captured:
        if daily:
            fetcher._fetch_raw_data("AAPL", "2025-01-02", "2025-01-03")
        else:
            fetcher.fetch_intraday("AAPL", "5m")
    assert not isinstance(captured.value, MoomooHistoryEmptyError)


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
    assert result["_source_time_key_count"].tolist() == [1, 2, 1]
    assert len(context.calls) == 2
    assert "page_req_key" not in context.calls[0]
    assert context.calls[1]["page_req_key"] == b"page-2"
    assert all(call["extended_time"] is True for call in context.calls)
    assert all(call["max_count"] == 1000 for call in context.calls)
    assert all(call["ktype"] == expected_ktype for call in context.calls)


def test_intraday_normalization_uses_iso_fast_path(monkeypatch) -> None:
    frame = _frame(
        ("2026-07-01 14:30:00", 10),
        ("2026-07-01 14:31:00", 11),
    )
    fetcher = MoomooFetcher.__new__(MoomooFetcher)

    def reject_scalar_pandas_parse(*_args, **_kwargs):
        raise AssertionError("normal Moomoo ISO time used pd.to_datetime")

    monkeypatch.setattr(
        moomoo_fetcher_module.pd,
        "to_datetime",
        reject_scalar_pandas_parse,
    )

    result = fetcher._normalize_intraday(frame, "AAPL")

    assert result["date"].tolist() == [
        "2026-07-01T14:30:00-04:00",
        "2026-07-01T14:31:00-04:00",
    ]


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


def test_history_rpc_paces_continuations_and_distinct_first_pages(
    monkeypatch,
) -> None:
    _install_sdk(monkeypatch)
    observed_time = {"value": 100.0}
    sleeps: list[float] = []

    monkeypatch.setattr(
        moomoo_fetcher_module.time,
        "monotonic",
        lambda: observed_time["value"],
    )

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        observed_time["value"] += seconds

    monkeypatch.setattr(moomoo_fetcher_module.time, "sleep", advance)
    first_context = _PagedContext(
        [
            (0, _frame(("2026-07-01 14:30:00", 10)), b"page-2"),
            (0, _frame(("2026-07-01 14:35:00", 11)), None),
        ]
    )
    second_context = _PagedContext(
        [(0, _frame(("2026-07-01 14:40:00", 12)), None)]
    )

    _fetch(first_context)
    _fetch(second_context)

    assert sleeps == pytest.approx([0.05, 0.55])


def test_history_rpc_is_single_concurrency_across_fetcher_instances(
    monkeypatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import time as stdlib_time

    _install_sdk(monkeypatch)
    monkeypatch.setattr(
        moomoo_fetcher_module,
        "_HISTORY_FIRST_PAGE_MIN_INTERVAL_SECONDS",
        0.0,
    )
    monkeypatch.setattr(
        moomoo_fetcher_module,
        "_HISTORY_CONTINUATION_MIN_INTERVAL_SECONDS",
        0.0,
    )
    first_started = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    state = {"active": 0, "max_active": 0, "calls": 0}

    class _BlockingContext:
        def request_history_kline(self, **_kwargs):
            with state_lock:
                state["active"] += 1
                state["calls"] += 1
                state["max_active"] = max(
                    state["max_active"], state["active"]
                )
                call_number = state["calls"]
            if call_number == 1:
                first_started.set()
                assert release_first.wait(timeout=2)
            with state_lock:
                state["active"] -= 1
            return 0, _frame(("2026-07-01 14:30:00", 10)), None

    first = MoomooFetcher.__new__(MoomooFetcher)
    second = MoomooFetcher.__new__(MoomooFetcher)
    first._get_ctx = lambda: _BlockingContext()
    second._get_ctx = lambda: _BlockingContext()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            first._history_kline_page,
            {"code": "US.AAPL"},
        )
        assert first_started.wait(timeout=2)
        second_future = pool.submit(
            second._history_kline_page,
            {"code": "US.META"},
        )
        stdlib_time.sleep(0.05)
        with state_lock:
            assert state["calls"] == 1
        release_first.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert state == {"active": 0, "max_active": 1, "calls": 2}


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
