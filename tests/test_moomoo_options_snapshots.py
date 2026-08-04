from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timezone
from threading import Event, Lock
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider import moomoo_options


class _QuoteContext:
    def __init__(self, chain: pd.DataFrame, snapshots: pd.DataFrame):
        self.chain = chain
        self.snapshots = snapshots
        self.snapshot_calls: list[list[str]] = []

    def get_option_chain(self, **_kwargs):
        return 0, self.chain

    def get_market_snapshot(self, codes):
        requested = list(codes)
        self.snapshot_calls.append(requested)
        return 0, self.snapshots[self.snapshots["code"].isin(requested)]


class _WallQuoteContext(_QuoteContext):
    def __init__(
        self,
        chains_by_expiry: dict[str, pd.DataFrame],
        snapshots: pd.DataFrame,
        expirations: list[str],
    ):
        super().__init__(pd.DataFrame(), snapshots)
        frames: list[pd.DataFrame] = []
        for expiry, frame in chains_by_expiry.items():
            prepared = frame.copy()
            if "strike_time" not in prepared.columns:
                prepared["strike_time"] = expiry
            frames.append(prepared)
        self.chain = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame()
        )
        self.expirations = expirations
        self.chain_calls: list[tuple[str, str]] = []

    def get_option_expiration_date(self, **_kwargs):
        return 0, pd.DataFrame(
            [{"strike_time": expiry} for expiry in self.expirations]
        )

    def get_option_chain(self, **kwargs):
        start = kwargs["start"]
        end = kwargs["end"]
        self.chain_calls.append((start, end))
        if self.chain.empty:
            return 0, self.chain
        strike_dates = self.chain["strike_time"].astype(str).str[:10]
        return 0, self.chain[(strike_dates >= start) & (strike_dates <= end)]


def _install_fake_moomoo(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "moomoo",
        SimpleNamespace(RET_OK=0, OpenQuoteContext=object),
    )


def _chain_row(
    code: str = "US.NVDA260821C180000",
    strike: float = 180.0,
    **overrides,
):
    row = {
        "code": code,
        "option_type": "CALL",
        "strike_price": strike,
    }
    row.update(overrides)
    return row


def _snapshot_row(code: str = "US.NVDA260821C180000", **overrides):
    row = {
        "code": code,
        "option_valid": True,
        "bid_price": 5.1,
        "ask_price": 5.3,
        "last_price": 5.2,
        "volume": 321,
        "option_open_interest": 1234,
        "option_implied_volatility": 42.5,
        "option_delta": 0.57,
        "option_expiry_date_distance": 31,
    }
    row.update(overrides)
    return row


def _prepare_wall_context(monkeypatch, ctx, *, spot: float = 181.0):
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)

    @contextmanager
    def lease_test_context():
        yield ctx, moomoo_options._ctx_lock

    monkeypatch.setattr(
        moomoo_options,
        "_lease_wall_context",
        lease_test_context,
    )
    monkeypatch.setattr(
        moomoo_options,
        "_spot_from_ctx",
        lambda _ctx, _symbol, _ret_ok, **_kwargs: spot,
    )


class _OverviewContext:
    def __init__(self, frame: pd.DataFrame, ret: int = 0):
        self.frame = frame
        self.ret = ret
        self.calls: list[list[str]] = []

    def get_option_underlying_overview(self, codes):
        self.calls.append(list(codes))
        return self.ret, self.frame


def test_option_underlying_overview_preserves_provider_time_bases(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    frame = pd.DataFrame(
        [
            {
                "code": "US.AAPL",
                "name": "Apple",
                "call_volume": 451945,
                "put_volume": 324225,
                "call_open_interest": 2687511,
                "put_open_interest": 1971458,
                "iv": 31.865,
                "iv_rank": 77.141,
                "iv_percentile": 88.888,
                "pre_iv": 32.121,
                "hv_30d": 36.996,
                "hv_30d_percentile": 98.015,
                "hv_60d": 31.782,
                "hv_60d_percentile": 99.206,
                "hv_90d": 28.039,
                "hv_90d_percentile": 95.634,
                "hv_120d": 27.197,
                "hv_120d_percentile": 94.047,
                "hv_365d": 24.506,
                "hv_365d_percentile": 28.174,
            }
        ]
    )
    ctx = _OverviewContext(frame)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)

    result = moomoo_options.fetch_option_underlying_overviews_moomoo(
        ["aapl", "US.AAPL", "AAPL"]
    )

    assert ctx.calls == [["US.AAPL"]]
    assert set(result) == {"AAPL"}
    item = result["AAPL"]
    assert item.call_volume == 451945
    assert item.put_open_interest == 1971458
    assert item.iv_percent == pytest.approx(31.865)
    assert item.iv_rank_percent == pytest.approx(77.141)
    assert item.iv_percentile_percent == pytest.approx(88.888)
    assert item.hv_30d_percent == pytest.approx(36.996)


def test_option_underlying_overview_fails_closed_on_provider_error(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    ctx = _OverviewContext(pd.DataFrame(), ret=-1)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)

    assert moomoo_options.fetch_option_underlying_overviews_moomoo(["AAPL"]) == {}


class _SessionSnapshotContext:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls: list[list[str]] = []

    def get_market_snapshot(self, codes):
        self.calls.append(list(codes))
        return 0, self.frame


def test_underlying_session_quote_parses_pre_fields_present_absent_invalid(
    monkeypatch,
):
    """盘前字段解析：present 保留（负 pre_change_rate 合法）、缺列/非法一律 None。

    Moomoo 盘前时段常规字段仍指向上一常规时段，真实盘前变动只在 pre_*：
    pre_change_rate 为相对上一常规收盘的百分比、可为负；pre_price 零/负、
    pre_volume/pre_turnover 负值、无法解析的值全部保持 None，绝不 0 回填。
    """

    _install_fake_moomoo(monkeypatch)
    frame = pd.DataFrame(
        [
            {
                # 盘前字段齐全：负 pre_change_rate 是合法读数。
                "code": "US.NVDA",
                "last_price": 130.5,
                "prev_close_price": 124.0,
                "pre_price": 128.7,
                "pre_change_rate": -2.35,
                "pre_volume": 12_000,
                "pre_turnover": 1_218_000.0,
            },
            {
                # 快照缺盘前列（pandas 混排下为 NaN）：全部 None。
                "code": "US.MU",
                "last_price": 100.5,
                "prev_close_price": 99.0,
            },
            {
                # 值非法：pre_price=0、pre_change_rate 不可解析、量额为负。
                "code": "US.AMD",
                "last_price": 150.0,
                "prev_close_price": 149.0,
                "pre_price": 0.0,
                "pre_change_rate": "not_a_number",
                "pre_volume": -5,
                "pre_turnover": -1.0,
            },
            {
                # pre_price 为负 → None；同行合法的 pre_change_rate 照常保留。
                "code": "US.TSLA",
                "last_price": 300.0,
                "prev_close_price": 305.0,
                "pre_price": -3.0,
                "pre_change_rate": -1.2,
                "pre_volume": 800,
                "pre_turnover": 240_000.0,
            },
        ]
    )
    ctx = _SessionSnapshotContext(frame)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)

    result = moomoo_options.fetch_underlying_session_quotes_moomoo(
        ["NVDA", "MU", "AMD", "TSLA"]
    )

    assert ctx.calls == [["US.NVDA", "US.MU", "US.AMD", "US.TSLA"]]
    assert set(result) == {"NVDA", "MU", "AMD", "TSLA"}

    nvda = result["NVDA"]
    # 常规字段照旧解析（additive：盘前字段不改变既有语义）。
    assert nvda.last_price == pytest.approx(130.5)
    assert nvda.pre_price == pytest.approx(128.7)
    assert nvda.pre_change_rate == pytest.approx(-2.35)
    assert nvda.pre_volume == 12_000
    assert nvda.pre_turnover == pytest.approx(1_218_000.0)

    mu = result["MU"]
    assert mu.pre_price is None
    assert mu.pre_change_rate is None
    assert mu.pre_volume is None
    assert mu.pre_turnover is None

    amd = result["AMD"]
    assert amd.pre_price is None  # 零价不是价格。
    assert amd.pre_change_rate is None
    assert amd.pre_volume is None
    assert amd.pre_turnover is None

    tsla = result["TSLA"]
    assert tsla.pre_price is None  # 负价不是价格。
    assert tsla.pre_change_rate == pytest.approx(-1.2)
    assert tsla.pre_volume == 800
    assert tsla.pre_turnover == pytest.approx(240_000.0)


def test_chain_joins_static_contracts_with_dynamic_snapshots(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    chain = pd.DataFrame([_chain_row()])
    snapshots = pd.DataFrame([_snapshot_row()])
    ctx = _QuoteContext(chain, snapshots)
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)
    monkeypatch.setattr(
        moomoo_options,
        "_spot_from_ctx",
        lambda _ctx, _symbol, _ret_ok: 181.0,
    )

    quotes = moomoo_options.fetch_chain_via_moomoo("NVDA", "2026-08-21")

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.bid == 5.1
    assert quote.ask == 5.3
    assert quote.volume == 321
    assert quote.open_interest == 1234
    assert quote.implied_volatility == 0.425
    assert quote.delta == 0.57
    assert quote.dte == 31
    assert ctx.snapshot_calls == [["US.NVDA260821C180000"]]


def test_chain_does_not_turn_static_rows_into_zero_quotes(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    chain = pd.DataFrame([_chain_row()])
    ctx = _QuoteContext(chain, pd.DataFrame(columns=["code"]))
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)

    assert moomoo_options.fetch_chain_via_moomoo("NVDA", "2026-08-21") == []


def test_snapshot_requests_respect_the_400_code_limit():
    codes = [f"US.TEST{i:03d}" for i in range(401)]
    snapshots = pd.DataFrame([{"code": code} for code in codes])
    ctx = _QuoteContext(pd.DataFrame(), snapshots)

    result = moomoo_options._get_option_snapshots(ctx, codes, 0)

    assert len(result.snapshots) == 401
    assert result.complete is True
    assert [len(call) for call in ctx.snapshot_calls] == [400, 1]


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("bid_price", None),
        ("ask_price", float("nan")),
        ("last_price", float("inf")),
        ("volume", None),
        ("option_open_interest", float("nan")),
        ("option_implied_volatility", float("inf")),
    ],
)
def test_chain_omits_missing_or_non_finite_dynamic_fields(
    monkeypatch,
    field,
    invalid,
):
    _install_fake_moomoo(monkeypatch)
    ctx = _QuoteContext(
        pd.DataFrame([_chain_row()]),
        pd.DataFrame([_snapshot_row(**{field: invalid})]),
    )
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)
    monkeypatch.setattr(
        moomoo_options,
        "_spot_from_ctx",
        lambda _ctx, _symbol, _ret_ok: 181.0,
    )

    assert moomoo_options.fetch_chain_via_moomoo(
        "NVDA",
        "2026-08-21",
    ) == []


@pytest.mark.parametrize("option_valid", [None, False, 0, "false"])
def test_chain_requires_explicit_valid_option_snapshot(
    monkeypatch,
    option_valid,
):
    _install_fake_moomoo(monkeypatch)
    ctx = _QuoteContext(
        pd.DataFrame([_chain_row()]),
        pd.DataFrame([_snapshot_row(option_valid=option_valid)]),
    )
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)
    monkeypatch.setattr(
        moomoo_options,
        "_spot_from_ctx",
        lambda _ctx, _symbol, _ret_ok: 181.0,
    )

    assert moomoo_options.fetch_chain_via_moomoo(
        "NVDA",
        "2026-08-21",
    ) == []


def test_real_zero_quote_fields_are_not_confused_with_missing(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    ctx = _QuoteContext(
        pd.DataFrame([_chain_row()]),
        pd.DataFrame(
            [
                _snapshot_row(
                    bid_price=0,
                    last_price=0,
                    volume=0,
                    option_open_interest=0,
                )
            ]
        ),
    )
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)
    monkeypatch.setattr(
        moomoo_options,
        "_spot_from_ctx",
        lambda _ctx, _symbol, _ret_ok: 181.0,
    )

    quote = moomoo_options.fetch_chain_via_moomoo(
        "NVDA",
        "2026-08-21",
    )[0]

    assert quote.bid == 0
    assert quote.last == 0
    assert quote.volume == 0
    assert quote.open_interest == 0


def test_snapshot_result_marks_a_failed_batch_incomplete():
    codes = [f"US.TEST{i:03d}" for i in range(401)]
    snapshots = pd.DataFrame([{"code": code} for code in codes])

    class _SecondBatchFails(_QuoteContext):
        def get_market_snapshot(self, requested_codes):
            requested = list(requested_codes)
            self.snapshot_calls.append(requested)
            if len(self.snapshot_calls) == 2:
                return 1, "provider unavailable"
            return 0, self.snapshots[self.snapshots["code"].isin(requested)]

    ctx = _SecondBatchFails(pd.DataFrame(), snapshots)

    result = moomoo_options._get_option_snapshots(ctx, codes, 0)

    assert len(result.snapshots) == 400
    assert result.failed_batch_count == 1
    assert result.complete is False
    assert result.missing_codes == ("US.TEST400",)


def test_option_wall_snapshot_filters_dte_and_nonstandard_and_keeps_coverage(
    monkeypatch,
):
    expiry_0dte = "2026-07-22"
    expiry_14dte = "2026-08-05"
    standard_code = "US.NVDA260722C180000"
    nonstandard_code = "US.NVDA260722P180000"
    unknown_type_code = "US.NVDA260722P175000"
    invalid_oi_code = "US.NVDA260805C185000"
    nullable_greeks_code = "US.NVDA260805P170000"
    ctx = _WallQuoteContext(
        {
            expiry_0dte: pd.DataFrame(
                [
                    _chain_row(
                        standard_code,
                        180.0,
                        option_standard_type="STANDARD",
                    ),
                    _chain_row(
                        nonstandard_code,
                        180.0,
                        option_type="PUT",
                        option_standard_type="OptionStandardType.NON_STANDARD",
                    ),
                    _chain_row(
                        unknown_type_code,
                        175.0,
                        option_type="PUT",
                        option_standard_type=None,
                    ),
                ]
            ),
            expiry_14dte: pd.DataFrame(
                [
                    _chain_row(
                        invalid_oi_code,
                        185.0,
                        option_standard_type="STANDARD",
                    ),
                    _chain_row(
                        nullable_greeks_code,
                        170.0,
                        option_type="PUT",
                        option_standard_type="STANDARD",
                    ),
                ]
            ),
        },
        pd.DataFrame(
            [
                _snapshot_row(
                    standard_code,
                    option_gamma=0.0123,
                    option_contract_size=100,
                    update_time="2026-07-22 10:01:02",
                ),
                _snapshot_row(
                    unknown_type_code,
                    option_gamma=None,
                    option_contract_size=None,
                    update_time="2026-07-22 10:01:03",
                ),
                _snapshot_row(invalid_oi_code, option_open_interest=None),
                _snapshot_row(
                    nullable_greeks_code,
                    option_gamma=None,
                    option_contract_size=None,
                    update_time=None,
                ),
            ]
        ),
        ["2026-07-19", expiry_0dte, expiry_14dte, "2026-09-30"],
    )
    _prepare_wall_context(monkeypatch, ctx)

    result = moomoo_options.fetch_option_wall_snapshot_moomoo(
        "NVDA",
        dte_min=0,
        dte_max=45,
        ref_date=date(2026, 7, 22),
    )

    assert isinstance(result, moomoo_options.MoomooOptionWallSnapshot)
    assert result.symbol == "NVDA"
    assert result.spot == 181.0
    assert result.fetched_at.tzinfo == timezone.utc
    assert result.expiries == (expiry_0dte, expiry_14dte)
    assert ctx.chain_calls == [("2026-07-22", "2026-08-20")]
    assert result.requested_contract_count == 3
    assert result.snapshot_received_count == 3
    assert result.valid_contract_count == 2
    assert result.failed_batch_count == 0
    assert result.excluded_nonstandard_count == 1
    assert result.excluded_unknown_standard_type_count == 1
    assert nonstandard_code not in {item.code for item in result.contracts}
    assert unknown_type_code not in {item.code for item in result.contracts}

    by_code = {item.code: item for item in result.contracts}
    standard = by_code[standard_code]
    assert standard == moomoo_options.MoomooOptionWallContract(
        code=standard_code,
        expiry=expiry_0dte,
        dte=0,
        right="C",
        strike=180.0,
        volume=321,
        open_interest=1234,
        gamma=0.0123,
        contract_size=100,
        update_time="2026-07-22 10:01:02",
        implied_volatility=0.425,
    )
    assert by_code[nullable_greeks_code].dte == 14
    assert by_code[nullable_greeks_code].contract_size is None
    assert by_code[nullable_greeks_code].implied_volatility == pytest.approx(
        0.425
    )
    assert invalid_oi_code not in by_code


@pytest.mark.parametrize("value", [None, "N/A", "UNKNOWN", float("nan")])
def test_option_wall_standard_type_unknown_sentinels_fail_closed(value):
    assert moomoo_options._normalise_option_standard_type(value) is None


def test_option_wall_uses_at_most_two_chain_ranges_for_default_45_dte(
    monkeypatch,
):
    expiries = ["2026-07-22", "2026-08-05", "2026-08-21", "2026-09-05"]
    chains: dict[str, pd.DataFrame] = {}
    snapshots: list[dict] = []
    for index, expiry in enumerate(expiries, start=1):
        code = f"US.NVDA{expiry.replace('-', '')}C{index:06d}"
        chains[expiry] = pd.DataFrame(
            [
                _chain_row(
                    code,
                    175.0 + index,
                    option_standard_type="STANDARD",
                )
            ]
        )
        snapshots.append(_snapshot_row(code))
    ctx = _WallQuoteContext(chains, pd.DataFrame(snapshots), expiries)
    _prepare_wall_context(monkeypatch, ctx)

    result = moomoo_options.fetch_option_wall_snapshot_moomoo(
        "NVDA",
        ref_date=date(2026, 7, 22),
    )

    assert result is not None
    assert result.expiries == tuple(expiries)
    assert ctx.chain_calls == [
        ("2026-07-22", "2026-08-20"),
        ("2026-08-21", "2026-09-05"),
    ]
    assert result.requested_contract_count == 4
    assert result.snapshot_received_count == 4
    assert result.valid_contract_count == 4


def test_option_wall_reports_failed_chain_range_as_partial_coverage(monkeypatch):
    expiries = ["2026-07-22", "2026-08-21"]
    first_code = "US.NVDA260722C180000"
    second_code = "US.NVDA260821C185000"

    class _SecondChainRangeFails(_WallQuoteContext):
        def get_option_chain(self, **kwargs):
            start = kwargs["start"]
            end = kwargs["end"]
            self.chain_calls.append((start, end))
            if len(self.chain_calls) == 2:
                return 1, "provider unavailable"
            strike_dates = self.chain["strike_time"].astype(str).str[:10]
            return 0, self.chain[(strike_dates >= start) & (strike_dates <= end)]

    ctx = _SecondChainRangeFails(
        {
            expiries[0]: pd.DataFrame(
                [_chain_row(first_code, option_standard_type="STANDARD")]
            ),
            expiries[1]: pd.DataFrame(
                [_chain_row(second_code, option_standard_type="STANDARD")]
            ),
        },
        pd.DataFrame([_snapshot_row(first_code), _snapshot_row(second_code)]),
        expiries,
    )
    _prepare_wall_context(monkeypatch, ctx)

    result = moomoo_options.fetch_option_wall_snapshot_moomoo(
        "NVDA",
        ref_date=date(2026, 7, 22),
    )

    assert result is not None
    assert result.requested_contract_count == 1
    assert result.snapshot_received_count == 1
    assert result.valid_contract_count == 1
    assert result.failed_batch_count == 1
    assert [item.code for item in result.contracts] == [first_code]


def test_option_wall_snapshot_does_not_fall_back_to_expired_expiry(monkeypatch):
    expired = "2026-07-18"
    expired_code = "US.NVDA260718C180000"
    ctx = _WallQuoteContext(
        {expired: pd.DataFrame([_chain_row(expired_code)])},
        pd.DataFrame([_snapshot_row(expired_code)]),
        [expired],
    )
    _prepare_wall_context(monkeypatch, ctx)

    result = moomoo_options.fetch_option_wall_snapshot_moomoo(
        "NVDA",
        dte_min=0,
        dte_max=45,
        ref_date=date(2026, 7, 22),
    )

    assert result is not None
    assert result.expiries == ()
    assert result.contracts == ()
    assert result.requested_contract_count == 0
    assert result.snapshot_received_count == 0
    assert ctx.chain_calls == []
    assert ctx.snapshot_calls == []


def test_option_wall_snapshot_preserves_partial_batch_coverage(monkeypatch):
    expiry = "2026-08-21"
    codes = [f"US.NVDA260821C{i:06d}" for i in range(1, 402)]
    chain = pd.DataFrame(
        [
            _chain_row(
                code,
                float(index),
                option_standard_type="STANDARD",
            )
            for index, code in enumerate(codes, start=1)
        ]
    )
    snapshots = pd.DataFrame([_snapshot_row(code) for code in codes])

    class _SecondWallBatchFails(_WallQuoteContext):
        def get_market_snapshot(self, requested_codes):
            requested = list(requested_codes)
            self.snapshot_calls.append(requested)
            if len(self.snapshot_calls) == 2:
                return 1, "provider unavailable"
            return 0, self.snapshots[self.snapshots["code"].isin(requested)]

    ctx = _SecondWallBatchFails({expiry: chain}, snapshots, [expiry])
    _prepare_wall_context(monkeypatch, ctx)

    result = moomoo_options.fetch_option_wall_snapshot_moomoo(
        "NVDA",
        dte_min=0,
        dte_max=45,
        ref_date=date(2026, 7, 22),
    )

    assert result is not None
    assert result.requested_contract_count == 401
    assert result.snapshot_received_count == 400
    assert result.valid_contract_count == 400
    assert result.failed_batch_count == 1
    assert [len(call) for call in ctx.snapshot_calls] == [400, 1]


def test_option_wall_snapshot_validates_bounds_and_enablement(monkeypatch):
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: False)
    monkeypatch.setattr(
        moomoo_options,
        "_get_ctx",
        lambda: pytest.fail("disabled option wall must not create a context"),
    )

    assert moomoo_options.fetch_option_wall_snapshot_moomoo("NVDA") is None
    with pytest.raises(ValueError, match="0 <= dte_min <= dte_max"):
        moomoo_options.fetch_option_wall_snapshot_moomoo(
            "NVDA",
            dte_min=10,
            dte_max=5,
        )
    with pytest.raises(ValueError, match="must be integers"):
        moomoo_options.fetch_option_wall_snapshot_moomoo(
            "NVDA",
            dte_min=0.5,  # type: ignore[arg-type]
        )


def test_atm_iv_fetches_exact_contract_and_fails_closed_when_missing(
    monkeypatch,
):
    _install_fake_moomoo(monkeypatch)
    atm_code = "US.NVDA260821C180000"
    far_code = "US.NVDA260821C220000"
    ctx = _QuoteContext(
        pd.DataFrame(
            [
                _chain_row(atm_code, 180.0),
                _chain_row(far_code, 220.0),
            ]
        ),
        pd.DataFrame([_snapshot_row(far_code)]),
    )
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(
        moomoo_options,
        "get_expirations_moomoo",
        lambda _symbol: ["2026-08-21"],
    )
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)
    monkeypatch.setattr(
        moomoo_options,
        "_spot_from_ctx",
        lambda _ctx, _symbol, _ret_ok: 181.0,
    )

    iv, expiry = moomoo_options.compute_atm_iv_moomoo(
        "NVDA",
        ref_date=date(2026, 7, 22),
    )

    assert iv is None
    assert expiry == "2026-08-21"
    assert ctx.snapshot_calls == [[atm_code]]


def test_atm_iv_does_not_fall_back_to_an_expired_contract(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(
        moomoo_options,
        "get_expirations_moomoo",
        lambda _symbol: ["2026-07-11", "2026-07-18"],
    )
    monkeypatch.setattr(
        moomoo_options,
        "_get_ctx",
        lambda: pytest.fail("expired expirations must not reach quote context"),
    )

    assert moomoo_options.compute_atm_iv_moomoo(
        "NVDA",
        ref_date=date(2026, 7, 22),
    ) == (None, "")


def test_atm_expiry_selection_uses_new_york_market_date(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    code = "US.NVDA260722C180000"
    ctx = _QuoteContext(
        pd.DataFrame([_chain_row(code, 180.0)]),
        pd.DataFrame([_snapshot_row(code)]),
    )
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(
        moomoo_options,
        "get_expirations_moomoo",
        lambda _symbol: ["2026-07-22", "2026-07-29"],
    )
    monkeypatch.setattr(
        moomoo_options,
        "_new_york_market_date",
        lambda: date(2026, 7, 22),
    )
    monkeypatch.setattr(moomoo_options, "_get_ctx", lambda: ctx)
    monkeypatch.setattr(
        moomoo_options,
        "_spot_from_ctx",
        lambda _ctx, _symbol, _ret_ok: 181.0,
    )

    iv, expiry = moomoo_options.compute_atm_iv_moomoo("NVDA")

    assert iv == 0.425
    assert expiry == "2026-07-22"


def test_context_cannot_close_while_expiration_query_is_in_flight(monkeypatch):
    _install_fake_moomoo(monkeypatch)
    query_started = Event()
    release_query = Event()
    closed = Event()
    reconnect_started = Event()

    class _BlockingContext:
        alive = True

        def get_option_expiration_date(self, **_kwargs):
            query_started.set()
            assert release_query.wait(timeout=2)
            return 0, pd.DataFrame([{"strike_time": "2026-08-21"}])

        def close(self):
            closed.set()

    old_ctx = _BlockingContext()
    replacement = SimpleNamespace(alive=True)
    monkeypatch.setattr(moomoo_options, "_ctx_singleton", old_ctx)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "probe_opend_tcp", lambda *_args: True)
    monkeypatch.setattr(
        moomoo_options,
        "_is_alive",
        lambda ctx: bool(getattr(ctx, "alive", False)),
    )
    monkeypatch.setattr(
        moomoo_options,
        "create_ready_quote_context",
        lambda **_kwargs: replacement,
    )

    def reconnect():
        reconnect_started.set()
        return moomoo_options._get_ctx()

    with ThreadPoolExecutor(max_workers=2) as pool:
        query_future = pool.submit(
            moomoo_options.get_expirations_moomoo,
            "NVDA",
        )
        assert query_started.wait(timeout=2)
        old_ctx.alive = False
        reconnect_future = pool.submit(reconnect)
        assert reconnect_started.wait(timeout=2)
        assert closed.wait(timeout=0.1) is False
        release_query.set()

        assert query_future.result(timeout=2) == ["2026-08-21"]
        assert reconnect_future.result(timeout=2) is replacement
        assert closed.is_set()


def test_option_wall_context_pool_leases_five_exclusive_reusable_lanes(
    monkeypatch,
):
    _install_fake_moomoo(monkeypatch)
    moomoo_options._reset_wall_context_pool_for_tests()
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    monkeypatch.setattr(moomoo_options, "probe_opend_tcp", lambda *_args: True)
    monkeypatch.setattr(moomoo_options, "_is_alive", lambda _ctx: True)

    created: list[SimpleNamespace] = []
    created_lock = Lock()

    def create_context(**_kwargs):
        context = SimpleNamespace(close=lambda: None)
        with created_lock:
            created.append(context)
        return context

    monkeypatch.setattr(
        moomoo_options,
        "create_ready_quote_context",
        create_context,
    )
    all_leased = Event()
    release = Event()
    leased_ids: list[int] = []
    leased_lock = Lock()

    def hold_lane():
        with moomoo_options._lease_wall_context() as leased:
            assert leased is not None
            context, _lock = leased
            with leased_lock:
                leased_ids.append(id(context))
                if len(leased_ids) == 5:
                    all_leased.set()
            assert release.wait(timeout=3)
            return id(context)

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(hold_lane) for _ in range(5)]
            assert all_leased.wait(timeout=3)
            assert len(created) == 5
            assert len(set(leased_ids)) == 5
            release.set()
            first_ids = {future.result(timeout=3) for future in futures}

        with moomoo_options._lease_wall_context() as reused:
            assert reused is not None
            reused_context, _lock = reused
            assert id(reused_context) in first_ids
        assert len(created) == 5
    finally:
        release.set()
        moomoo_options._reset_wall_context_pool_for_tests()


# --- 今日车道可用性（V2-E）：最省额度的到期日元数据读取 ----------------------


class _ExpiryOnlyContext(_WallQuoteContext):
    """到期日元数据可读，但链窗口/快照一旦被调用即判定测试失败。"""

    def get_option_chain(self, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("availability read must not query the option chain")

    def get_market_snapshot(self, codes):  # pragma: no cover - must not run
        raise AssertionError("availability read must not request snapshots")


def test_expiry_availability_reads_only_expiration_metadata(monkeypatch):
    """只发 get_option_expiration_date：不发链窗口、不取 spot、不发快照批次。"""

    ctx = _ExpiryOnlyContext(
        {},
        pd.DataFrame(),
        ["2026-08-05", "2026-08-07", "2026-08-10", "2026-09-18"],
    )
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)

    @contextmanager
    def lease_test_context():
        yield ctx, moomoo_options._ctx_lock

    monkeypatch.setattr(moomoo_options, "_lease_wall_context", lease_test_context)

    def forbidden_spot(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("availability read must not fetch an underlying spot")

    monkeypatch.setattr(
        moomoo_options, "_spot_with_time_from_ctx", forbidden_spot
    )

    result = moomoo_options.fetch_expiry_availability_moomoo(
        "NVDA", max_dte=7, ref_date=date(2026, 8, 4)
    )

    assert result is not None
    assert result.symbol == "NVDA"
    assert result.market_date == "2026-08-04"
    # 2026-09-18 超窗口被排除；输出按 (dte, expiry) 升序。
    assert result.expiries == (
        ("2026-08-05", 1),
        ("2026-08-07", 3),
        ("2026-08-10", 6),
    )
    assert ctx.chain_calls == []


def test_expiry_availability_zero_dte_is_reported_for_same_day_expiry(monkeypatch):
    ctx = _ExpiryOnlyContext({}, pd.DataFrame(), ["2026-08-04", "2026-08-07"])
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)

    @contextmanager
    def lease_test_context():
        yield ctx, moomoo_options._ctx_lock

    monkeypatch.setattr(moomoo_options, "_lease_wall_context", lease_test_context)

    result = moomoo_options.fetch_expiry_availability_moomoo(
        "QQQ", max_dte=7, ref_date=date(2026, 8, 4)
    )

    assert result is not None
    assert result.expiries[0] == ("2026-08-04", 0)


def test_expiry_availability_empty_window_is_honest_empty_not_none(monkeypatch):
    """窗口内没有到期日：空 expiries（诚实空态），不是 None（失败）。"""

    ctx = _ExpiryOnlyContext({}, pd.DataFrame(), ["2026-09-18"])
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)

    @contextmanager
    def lease_test_context():
        yield ctx, moomoo_options._ctx_lock

    monkeypatch.setattr(moomoo_options, "_lease_wall_context", lease_test_context)

    result = moomoo_options.fetch_expiry_availability_moomoo(
        "AAOI", max_dte=7, ref_date=date(2026, 8, 4)
    )

    assert result is not None
    assert result.expiries == ()


def test_expiry_availability_fails_closed_on_metadata_error(monkeypatch):
    class _BrokenContext(_ExpiryOnlyContext):
        def get_option_expiration_date(self, **_kwargs):
            return 1, "rate limited"

    ctx = _BrokenContext({}, pd.DataFrame(), [])
    _install_fake_moomoo(monkeypatch)
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)

    @contextmanager
    def lease_test_context():
        yield ctx, moomoo_options._ctx_lock

    monkeypatch.setattr(moomoo_options, "_lease_wall_context", lease_test_context)

    assert (
        moomoo_options.fetch_expiry_availability_moomoo(
            "MU", max_dte=7, ref_date=date(2026, 8, 4)
        )
        is None
    )


def test_expiry_availability_validates_bounds_and_enablement(monkeypatch):
    monkeypatch.setattr(moomoo_options, "_enabled", lambda: False)
    assert moomoo_options.fetch_expiry_availability_moomoo("MU") is None

    monkeypatch.setattr(moomoo_options, "_enabled", lambda: True)
    _install_fake_moomoo(monkeypatch)
    with pytest.raises(ValueError):
        moomoo_options.fetch_expiry_availability_moomoo("MU", max_dte=8)
    with pytest.raises(ValueError):
        moomoo_options.fetch_expiry_availability_moomoo("MU", max_dte=-1)
