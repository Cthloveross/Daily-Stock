from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider import moomoo_options


class _OptionEventFilter:
    def __init__(
        self,
        indicator_type="N/A",
        value_list=None,
        interval_min=None,
        interval_max=None,
        min_inclusive=True,
        max_inclusive=True,
        string_value_list=None,
        security_list=None,
    ):
        self.indicator_type = indicator_type
        self.security_list = security_list


def _install_fake_moomoo(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "moomoo",
        SimpleNamespace(
            RET_OK=0,
            EventIndicatorType=SimpleNamespace(OWNER_LIST="OWNER_LIST"),
            OptionEventFilter=_OptionEventFilter,
            OptionMarket=SimpleNamespace(US_SECURITY="US_SECURITY"),
            OpenQuoteContext=object,
        ),
    )


def _event_row(**overrides) -> dict:
    row = {
        "option_code": "US.AAPL270115P200000",
        "owner_code": "US.AAPL",
        "symbol": "AAPL",
        "fill_time": "2026-07-21 15:44:48",
        "fill_timestamp": 1784663088.0,
        "ticker_type": "SELL",
        "price": 1.04,
        "volume": 1000,
        "turnover": 104000.0,
        "option_type": "PUT",
        "strike_price": 200.0,
        "strike_time": "2027-01-15",
        "dte": 177,
        "underlying_price": 327.86,
        "bid_price": 1.04,
        "ask_price": 1.06,
        "iv": 40.791,
        "total_volume": 1401,
        "total_open_interest": 26922,
        "vo_ratio": 0.05203,
        "delta": -0.026524137,
        "sentiment": "BULLISH",
        "order_type_list": ["SWEEP", "NORMAL"],
        "strategy_type": "SINGLE_LEG",
    }
    row.update(overrides)
    return row


class _EventContext:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_option_event(self, market, **kwargs):
        self.calls.append((market, kwargs))
        return self.result


def _prepare(monkeypatch, ctx) -> None:
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "_get_event_ctx", lambda: ctx)


def test_option_event_reads_current_dict_payload_and_preserves_units(monkeypatch):
    frame = pd.DataFrame([_event_row()])
    ctx = _EventContext(
        (
            0,
            {
                "event_list": frame,
                "next_page": '{"NewId":1}',
                "all_count": 746,
            },
        )
    )
    _prepare(monkeypatch, ctx)

    first = moomoo_options.fetch_option_events_moomoo("AAPL", limit=5)
    second = moomoo_options.fetch_option_events_moomoo("AAPL", limit=5)

    assert first is not None
    assert second is not None
    assert first.all_count == 746
    assert first.event_as_of == "2026-07-21 15:44:48"
    assert len(first.events) == 1
    event = first.events[0]
    assert event.event_id == second.events[0].event_id
    assert event.event_id.startswith("moomoo-evt-")
    # SDK get_option_event.iv is already in percent form.
    assert event.iv_percent == 40.791
    # SDK vo_ratio is decimal volume/OI, so the API-facing value is percent.
    assert event.vo_ratio_percent == pytest.approx(5.203)
    assert event.order_types == ("SWEEP", "NORMAL")
    assert event.expiry == "2027-01-15"
    assert event.delta == pytest.approx(-0.026524137)

    market, kwargs = ctx.calls[0]
    assert market == "US_SECURITY"
    assert kwargs["count"] == 5
    owner_filter = kwargs["filter_list"][0]
    assert owner_filter.indicator_type == "OWNER_LIST"
    assert owner_filter.security_list == ["US.AAPL"]


def test_option_event_supports_legacy_direct_frame_shape(monkeypatch):
    ctx = _EventContext((0, pd.DataFrame([_event_row()]), "cursor", 123))
    _prepare(monkeypatch, ctx)

    snapshot = moomoo_options.fetch_option_events_moomoo("US.AAPL", limit=1)

    assert snapshot is not None
    assert snapshot.all_count == 123
    assert len(snapshot.events) == 1


def test_option_event_successful_empty_payload_is_not_unavailable(monkeypatch):
    ctx = _EventContext(
        (0, {"event_list": pd.DataFrame(), "next_page": None, "all_count": 0})
    )
    _prepare(monkeypatch, ctx)

    snapshot = moomoo_options.fetch_option_events_moomoo("AAPL")

    assert snapshot is not None
    assert snapshot.all_count == 0
    assert snapshot.event_as_of is None
    assert snapshot.events == ()


def test_option_event_missing_fields_degrade_and_wrong_symbol_rows_are_omitted(
    monkeypatch,
):
    frame = pd.DataFrame(
        [
            _event_row(
                price=float("nan"),
                iv=None,
                vo_ratio=-1,
                delta=2,
                order_type_list=None,
                strike_time="not-a-date",
            ),
            _event_row(option_code="US.MSFT260821C500000", owner_code="US.MSFT"),
            _event_row(option_code=None),
        ]
    )
    ctx = _EventContext((0, {"event_list": frame, "all_count": 3}))
    _prepare(monkeypatch, ctx)

    snapshot = moomoo_options.fetch_option_events_moomoo("AAPL")

    assert snapshot is not None
    assert len(snapshot.events) == 1
    event = snapshot.events[0]
    assert event.price is None
    assert event.iv_percent is None
    assert event.vo_ratio_percent is None
    assert event.delta is None
    assert event.expiry is None
    assert event.order_types == ()


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame([_event_row(option_code=None)]),
        pd.DataFrame(),
    ],
)
def test_option_event_does_not_report_malformed_nonzero_payload_as_empty(
    monkeypatch,
    frame,
):
    ctx = _EventContext((0, {"event_list": frame, "all_count": 1}))
    _prepare(monkeypatch, ctx)

    assert moomoo_options.fetch_option_events_moomoo("AAPL") is None


@pytest.mark.parametrize(
    "result",
    [
        (1, "provider unavailable"),
        (0, {"all_count": 2}),
        (0, "not a row container"),
        (0,),
    ],
)
def test_option_event_malformed_or_failed_result_fails_closed(monkeypatch, result):
    ctx = _EventContext(result)
    _prepare(monkeypatch, ctx)

    assert moomoo_options.fetch_option_events_moomoo("AAPL") is None


def test_option_event_missing_sdk_method_and_disabled_state_fail_closed(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "_get_event_ctx", lambda: object())

    assert moomoo_options.fetch_option_events_moomoo("AAPL") is None

    monkeypatch.setattr(moomoo_options, "_enabled", lambda: False)
    monkeypatch.setattr(
        moomoo_options,
        "_get_event_ctx",
        lambda: pytest.fail("disabled provider must not open a context"),
    )
    assert moomoo_options.fetch_option_events_moomoo("AAPL") is None


def test_option_event_query_does_not_wait_for_main_context_lane(monkeypatch):
    ctx = _EventContext(
        (0, {"event_list": pd.DataFrame([_event_row()]), "all_count": 1})
    )
    _prepare(monkeypatch, ctx)
    main_lane_acquired = threading.Event()
    release_main_lane = threading.Event()

    def hold_main_lane() -> None:
        with moomoo_options._ctx_lock:
            main_lane_acquired.set()
            assert release_main_lane.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_main_lane)
        assert main_lane_acquired.wait(timeout=2)
        event_query = executor.submit(
            moomoo_options.fetch_option_events_moomoo,
            "AAPL",
            1,
        )
        try:
            snapshot = event_query.result(timeout=2)
        finally:
            release_main_lane.set()
        holder.result(timeout=2)

    assert snapshot is not None
    assert len(snapshot.events) == 1


def test_event_context_lane_reconnects_without_mutating_main_lane(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "_endpoint", lambda: ("127.0.0.1", 11111))
    monkeypatch.setattr(moomoo_options, "probe_opend_tcp", lambda *args: False)

    class ClosableContext:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    stale_event_ctx = ClosableContext()
    fresh_event_ctx = object()
    main_ctx = object()
    create_calls = []

    def create_context(*, host: str, port: int):
        create_calls.append((host, port))
        return fresh_event_ctx

    monkeypatch.setattr(moomoo_options, "_ctx_singleton", main_ctx)
    monkeypatch.setattr(moomoo_options, "_event_ctx_singleton", stale_event_ctx)
    monkeypatch.setattr(moomoo_options, "create_ready_quote_context", create_context)

    result = moomoo_options._get_event_ctx()

    assert result is fresh_event_ctx
    assert stale_event_ctx.close_count == 1
    assert moomoo_options._event_ctx_singleton is fresh_event_ctx
    assert moomoo_options._ctx_singleton is main_ctx
    assert create_calls == [("127.0.0.1", 11111)]


def test_event_context_lane_fails_closed_when_ready_context_is_unavailable(
    monkeypatch,
):
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "_event_ctx_singleton", None)

    def unavailable_context(*, host: str, port: int):
        raise moomoo_options.MoomooRuntimeError(f"offline at {host}:{port}")

    monkeypatch.setattr(
        moomoo_options,
        "create_ready_quote_context",
        unavailable_context,
    )

    assert moomoo_options._get_event_ctx() is None
    assert moomoo_options._event_ctx_singleton is None


@pytest.mark.parametrize("limit", [0, 11, 1.5, True])
def test_option_event_validates_bounded_limit(limit):
    with pytest.raises(ValueError, match="between 1 and 10"):
        moomoo_options.fetch_option_events_moomoo("AAPL", limit=limit)
