from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.journal.brokers.moomoo_position_snapshot as snapshot_module
from src.journal.brokers.moomoo_position_snapshot import (
    MoomooPositionSnapshotError,
    PositionSnapshotConfig,
    run_position_snapshot_probe,
)
from src.journal.brokers.moomoo_readonly import (
    ProbeConfig,
    _SelectedAccount,
    _build_payload,
)


class _EnumValues:
    REAL = "REAL"
    US = "US"
    FUTUINC = "FUTUINC"


SDK = SimpleNamespace(
    RET_OK=0,
    TrdEnv=_EnumValues,
    TrdMarket=_EnumValues,
    SecurityFirm=_EnumValues,
)

SECRET = "position-snapshot-test-secret-at-least-32-bytes"


class FakeTradeContext:
    def __init__(
        self,
        *,
        reads: list[list[dict]],
        accounts: list[dict] | None = None,
        failed_position_calls: set[int] | None = None,
    ) -> None:
        self.reads = reads
        self.accounts = accounts or [_real_account()]
        self.failed_position_calls = failed_position_calls or set()
        self.position_calls: list[dict] = []
        self.closed = False

    def get_acc_list(self):
        return 0, list(self.accounts)

    def position_list_query(self, **kwargs):
        call_index = len(self.position_calls)
        self.position_calls.append(kwargs)
        if call_index in self.failed_position_calls:
            return 1, "raw account response must stay private"
        successful_call_index = call_index - sum(
            failed < call_index for failed in self.failed_position_calls
        )
        rows = self.reads[min(successful_call_index, len(self.reads) - 1)]
        return 0, list(rows)

    def close(self):
        self.closed = True


class FakeQuoteContext:
    def __init__(self, rows: list[dict] | None = None, *, fail: bool = False):
        self.rows = rows or []
        self.fail = fail
        self.calls: list[list[str]] = []
        self.closed = False

    def get_market_snapshot(self, codes):
        self.calls.append(list(codes))
        if self.fail:
            return 1, "raw quote entitlement response must stay private"
        requested = set(codes)
        return 0, [row for row in self.rows if row.get("code") in requested]

    def close(self):
        self.closed = True


def _real_account(account_id: str = "101") -> dict:
    return {
        "acc_id": account_id,
        "trd_env": "REAL",
        "trdmarket_auth": ["US"],
        "acc_status": "ACTIVE",
        "card_num": "must-not-export",
    }


def _position(
    symbol: str = "US.AAPL260717C00200000",
    *,
    side: str = "LONG",
    qty: object = "2.00",
    market: str = "US",
    average_cost: object = "1.25",
    diluted_cost: object = "1.10",
) -> dict:
    return {
        "position_side": side,
        "code": symbol,
        "stock_name": "must-not-export",
        "position_market": market,
        "qty": qty,
        "can_sell_qty": qty,
        "currency": "USD",
        "cost_price": average_cost,
        "cost_price_valid": True,
        "average_cost": average_cost,
        "diluted_cost": diluted_cost,
        "nominal_price": "999.99",
        "market_val": "999999",
        "unrealized_pl": "1234",
        "realized_pl": "5678",
        "acc_id": "must-not-export",
        "position_id": "must-not-export",
    }


def _contract_spec(
    symbol: str = "US.AAPL260717C00200000",
    *,
    lot_size: object = "100.0",
    contract_size: object = 100,
    multiplier: object = "100.00",
) -> dict:
    return {
        "code": symbol,
        "lot_size": lot_size,
        "option_contract_size": contract_size,
        "option_contract_multiplier": multiplier,
        "position_id": "must-not-export",
    }


def _clock(start: datetime | None = None):
    base = start or datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    values = iter(base + timedelta(milliseconds=index) for index in range(20))
    return lambda: next(values)


def _config(**kwargs) -> PositionSnapshotConfig:
    return PositionSnapshotConfig(
        account_binding_secret=SECRET,
        retries=kwargs.pop("retries", 0),
        retry_delay=kwargs.pop("retry_delay", 0),
        **kwargs,
    )


def _run(
    ctx: FakeTradeContext,
    quote_ctx: FakeQuoteContext,
    *,
    config: PositionSnapshotConfig | None = None,
    clock=None,
):
    return run_position_snapshot_probe(
        config or _config(),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        quote_context_factory=lambda _config, _sdk: quote_ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
        clock=clock or _clock(),
    )


def test_stable_snapshot_uses_two_server_reads_and_safe_option_contract():
    first = _position(average_cost="1.20", diluted_cost="-0.50")
    second = _position(qty="2.0", average_cost="1.30", diluted_cost="-0.40")
    # These volatile values differ but are outside both the payload and the
    # double-read boundary comparison.
    first["nominal_price"] = "10.00"
    second["nominal_price"] = "10.50"
    first["market_val"] = "2000"
    second["market_val"] = "2100"
    ctx = FakeTradeContext(reads=[[first], [second]])
    quote_ctx = FakeQuoteContext([_contract_spec()])

    result = _run(ctx, quote_ctx)

    assert ctx.closed is True
    assert quote_ctx.closed is True
    assert ctx.position_calls == [
        {
            "position_market": SDK.TrdMarket.US,
            "trd_env": SDK.TrdEnv.REAL,
            "acc_id": 101,
            "refresh_cache": True,
            "show_option_strategy_view": False,
        },
        {
            "position_market": SDK.TrdMarket.US,
            "trd_env": SDK.TrdEnv.REAL,
            "acc_id": 101,
            "refresh_cache": True,
            "show_option_strategy_view": False,
        },
    ]
    assert quote_ctx.calls == [["US.AAPL260717C00200000"]]
    assert result.summary["analysis_ready"] is True
    assert result.summary["stable"] is True
    assert result.summary["position_snapshot_complete"] is True
    assert result.summary["contract_spec_status"] == "complete"

    position = result.export_payload["records"]["positions"][0]
    assert position == {
        "symbol": "US.AAPL260717C00200000",
        "market": "US",
        "asset_type": "option",
        "underlying": "AAPL",
        "expiry": "2026-07-17",
        "strike": "200",
        "option_right": "C",
        "position_side": "LONG",
        "quantity_contracts": "2",
        "signed_quantity_contracts": "2",
        "can_sell_quantity_contracts": "2",
        "currency": "USD",
        "broker_cost_context": {
            "cost_price": "1.3",
            "cost_price_valid": True,
            "average_cost": "1.3",
            "diluted_cost": "-0.4",
            "semantics": "broker_display_only_not_realized_pnl",
        },
        "contract_multiplier": "100",
        "contract_multiplier_basis": (
            "moomoo_market_snapshot_three_field_consensus"
        ),
        "contract_spec_source_record_sha256": (
            result.export_payload["records"]["contract_specs"][0][
                "source_record_sha256"
            ]
        ),
    }
    assert result.export_payload["schema"] == "dsa.moomoo.position-snapshot.v1"
    acquisition = result.export_payload["acquisition"]
    assert acquisition["broker_as_of"] is None
    assert "broker_as_of_not_provided" in acquisition["time_semantics"]
    assert acquisition["first_read"]["started_at"] < acquisition["first_read"]["completed_at"]
    assert acquisition["second_read"]["started_at"] < acquisition["second_read"]["completed_at"]
    expected_boundary = [
        {
            "symbol": "US.AAPL260717C00200000",
            "position_side": "LONG",
            "signed_quantity_contracts": "2",
        }
    ]
    assert acquisition["first_read"]["boundary_rows"] == expected_boundary
    assert acquisition["second_read"]["boundary_rows"] == expected_boundary
    assert result.summary["counts"]["first_recognized_option_positions"] == 1
    assert result.summary["counts"]["second_recognized_option_positions"] == 1

    encoded = json.dumps(result.export_payload)
    for forbidden in (
        "acc_id",
        "position_id",
        "card_num",
        "stock_name",
        "nominal_price",
        "market_val",
        "unrealized_pl",
        "realized_pl",
        "must-not-export",
    ):
        assert forbidden not in encoded


def test_hmac_binding_matches_journal_domain_without_exporting_account_id():
    ctx = FakeTradeContext(reads=[[], []])
    quote_ctx = FakeQuoteContext()
    result = _run(ctx, quote_ctx)

    expected = hmac.new(
        SECRET.encode("utf-8"),
        b"dsa-journal-account-binding-v1|LIVE|US|101",
        hashlib.sha256,
    ).hexdigest()
    assert result.export_payload["account"] == {
        "environment": "LIVE",
        "market": "US",
        "selection": "unique_auto",
        "binding": expected,
    }
    assert "101" not in json.dumps(result.export_payload)

    readonly_config = ProbeConfig(
        start=datetime(2026, 7, 29, tzinfo=timezone.utc),
        end=datetime(2026, 7, 30, tzinfo=timezone.utc),
        account_binding_secret=SECRET,
    )
    readonly_result = _build_payload(
        config=readonly_config,
        selected=_SelectedAccount(sdk_acc_id=101, selection="unique_auto"),
        orders=[],
        deals=[],
        fees=[],
        contract_specs=[],
        contract_spec_status="not_applicable",
        duplicates={"orders": 0, "deals": 0, "fees": 0, "contract_specs": 0},
        window_count=1,
        fee_batch_count=0,
    )
    assert readonly_result.export_payload["account"]["binding"] == expected


def test_short_position_has_negative_signed_contract_quantity():
    row = _position(
        "US.TSLA260717P00300000",
        side="SHORT",
        qty="3.000",
    )
    ctx = FakeTradeContext(reads=[[row], [row]])
    quote_ctx = FakeQuoteContext([_contract_spec(row["code"])])

    result = _run(ctx, quote_ctx)

    position = result.export_payload["records"]["positions"][0]
    assert position["quantity_contracts"] == "3"
    assert position["signed_quantity_contracts"] == "-3"
    assert position["position_side"] == "SHORT"


def test_non_us_and_equity_rows_are_filtered_without_polluting_option_snapshot():
    option = _position()
    equity = _position("US.AAPL")
    non_us = _position(
        "HK.TCH260717C00200000",
        market="HK",
    )
    rows = [non_us, equity, option]
    ctx = FakeTradeContext(reads=[rows, list(reversed(rows))])
    quote_ctx = FakeQuoteContext([_contract_spec()])

    result = _run(ctx, quote_ctx)

    assert result.summary["analysis_ready"] is True
    assert result.summary["counts"]["positions"] == 1
    assert result.summary["counts"]["second_filtered_non_us_rows"] == 1
    assert result.summary["counts"]["second_filtered_non_option_rows"] == 1
    assert [row["symbol"] for row in result.export_payload["records"]["positions"]] == [
        "US.AAPL260717C00200000"
    ]


def test_stable_empty_result_is_a_complete_zero_option_snapshot():
    ctx = FakeTradeContext(reads=[[], []])
    quote_ctx = FakeQuoteContext()

    result = _run(ctx, quote_ctx)

    assert result.summary["analysis_ready"] is True
    assert result.summary["position_snapshot_complete"] is True
    assert result.summary["has_positions"] is False
    assert result.summary["contract_spec_status"] == "not_applicable"
    assert result.export_payload["records"] == {
        "positions": [],
        "contract_specs": [],
    }
    assert quote_ctx.calls == []


@pytest.mark.parametrize(
    ("rows", "issue"),
    [
        (
            [[_position(side="NET")], [_position(side="NET")]],
            "unknown_position_side",
        ),
        (
            [
                [_position(), _position()],
                [_position(), _position()],
            ],
            "duplicate_instrument",
        ),
        (
            [[_position(qty="-1")], [_position(qty="-1")]],
            "invalid_contract_quantity",
        ),
        (
            [[_position(qty="NaN")], [_position(qty="NaN")]],
            "invalid_contract_quantity",
        ),
    ],
)
def test_invalid_boundary_rows_fail_analysis_without_guessing(rows, issue):
    ctx = FakeTradeContext(reads=rows)
    quote_ctx = FakeQuoteContext()

    result = _run(ctx, quote_ctx)

    assert result.summary["retrieval_complete"] is True
    assert result.summary["analysis_ready"] is False
    assert result.summary["position_snapshot_complete"] is False
    assert result.export_payload["records"]["positions"] == []
    assert any(issue in item for item in result.summary["validation_issues"])


def test_zero_quantity_cache_rows_are_safely_filtered_from_stable_snapshot():
    active = _position()
    cached_zero = _position(
        "US.MSFT260717P00400000",
        side="UNSUPPORTED_BUT_INACTIVE",
        qty="0.000",
    )
    cached_zero["currency"] = "CAD"
    ctx = FakeTradeContext(
        reads=[
            [cached_zero, active],
            [active, cached_zero],
        ]
    )
    quote_ctx = FakeQuoteContext([_contract_spec()])

    result = _run(ctx, quote_ctx)

    assert result.summary["analysis_ready"] is True
    assert result.summary["position_snapshot_complete"] is True
    assert result.summary["validation_issues"] == []
    assert result.summary["counts"]["first_filtered_zero_quantity_rows"] == 1
    assert result.summary["counts"]["second_filtered_zero_quantity_rows"] == 1
    assert result.export_payload["acquisition"]["first_read"][
        "filtered_zero_quantity_rows"
    ] == 1
    assert result.export_payload["acquisition"]["second_read"][
        "filtered_zero_quantity_rows"
    ] == 1
    assert [
        row["symbol"] for row in result.export_payload["records"]["positions"]
    ] == ["US.AAPL260717C00200000"]


def test_non_usd_option_position_fails_analysis_closed():
    row = _position()
    row["currency"] = "CAD"
    ctx = FakeTradeContext(reads=[[row], [row]])
    quote_ctx = FakeQuoteContext()

    result = _run(ctx, quote_ctx)

    assert result.summary["analysis_ready"] is False
    assert result.export_payload["records"]["positions"] == []
    assert result.summary["validation_issues"] == [
        "first_read:unsupported_currency:US.AAPL260717C00200000",
        "second_read:unsupported_currency:US.AAPL260717C00200000",
    ]


def test_symbol_side_or_signed_quantity_drift_fails_double_read_stability():
    first = _position(qty="1")
    second = _position(qty="2")
    ctx = FakeTradeContext(reads=[[first], [second]])
    quote_ctx = FakeQuoteContext([_contract_spec()])

    result = _run(ctx, quote_ctx)

    stability = result.export_payload["acquisition"]["stability"]
    assert stability == {
        "stable": False,
        "comparison_basis": (
            "instrument_position_side_signed_quantity_contracts"
        ),
        "added_symbols": [],
        "removed_symbols": [],
        "changed_symbols": ["US.AAPL260717C00200000"],
    }
    assert result.summary["analysis_ready"] is False
    assert result.summary["contract_spec_status"] == "complete"
    assert "position_boundary_changed_between_reads" in result.summary["warnings"]


@pytest.mark.parametrize(
    "quote_rows",
    [
        [],
        [_contract_spec(contract_size="50")],
        [_contract_spec(multiplier=None)],
    ],
)
def test_missing_or_inconsistent_contract_spec_fails_analysis(quote_rows):
    row = _position()
    ctx = FakeTradeContext(reads=[[row], [row]])
    quote_ctx = FakeQuoteContext(quote_rows)

    result = _run(ctx, quote_ctx)

    assert result.summary["analysis_ready"] is False
    assert result.summary["position_snapshot_complete"] is False
    assert result.summary["contract_spec_status"] in {"partial", "unavailable"}
    assert result.summary["validation_issues"] == [
        "missing_or_invalid_contract_spec:US.AAPL260717C00200000"
    ]
    position = result.export_payload["records"]["positions"][0]
    assert position["contract_multiplier"] is None
    assert position["contract_multiplier_basis"] == "unavailable"


def test_quote_transport_failure_is_safe_and_marks_specs_unavailable():
    row = _position()
    ctx = FakeTradeContext(reads=[[row], [row]])
    quote_ctx = FakeQuoteContext(fail=True)

    result = _run(ctx, quote_ctx)

    assert result.summary["contract_spec_status"] == "unavailable"
    assert result.summary["analysis_ready"] is False
    assert "raw quote entitlement" not in json.dumps(result.export_payload)


def test_order_and_decimal_spelling_do_not_change_boundary_or_snapshot_hash():
    apple_first = _position(qty="2.000", average_cost="1.2500")
    apple_second = _position(qty=2, average_cost="1.25")
    tesla_first = _position(
        "US.TSLA260717P00300000",
        side="SHORT",
        qty="1.0",
    )
    tesla_second = _position(
        "US.TSLA260717P00300000",
        side="SHORT",
        qty=1,
    )
    specs = [_contract_spec(tesla_first["code"]), _contract_spec()]

    first_ctx = FakeTradeContext(
        reads=[
            [tesla_first, apple_first],
            [apple_second, tesla_second],
        ]
    )
    second_ctx = FakeTradeContext(
        reads=[
            [apple_second, tesla_second],
            [tesla_first, apple_first],
        ]
    )
    first_result = _run(first_ctx, FakeQuoteContext(specs))
    second_result = _run(second_ctx, FakeQuoteContext(list(reversed(specs))))

    assert first_result.summary["analysis_ready"] is True
    assert second_result.summary["analysis_ready"] is True
    assert first_result.summary["snapshot_sha256"] == second_result.summary[
        "snapshot_sha256"
    ]
    assert first_result.summary["positions_sha256"] == second_result.summary[
        "positions_sha256"
    ]
    assert [
        row["symbol"]
        for row in first_result.export_payload["records"]["positions"]
    ] == [
        "US.AAPL260717C00200000",
        "US.TSLA260717P00300000",
    ]


def test_position_query_retries_and_never_exposes_raw_sdk_error():
    row = _position()
    ctx = FakeTradeContext(
        reads=[[row], [row]],
        failed_position_calls={0},
    )
    quote_ctx = FakeQuoteContext([_contract_spec()])

    result = _run(ctx, quote_ctx, config=_config(retries=1))

    assert result.summary["analysis_ready"] is True
    assert len(ctx.position_calls) == 3
    assert all(call["refresh_cache"] is True for call in ctx.position_calls)
    assert "raw account response" not in json.dumps(result.export_payload)


def test_second_query_failure_closes_context_and_normalizes_error_message():
    row = _position()
    ctx = FakeTradeContext(
        reads=[[row], [row]],
        failed_position_calls={1},
    )
    quote_ctx = FakeQuoteContext([_contract_spec()])

    with pytest.raises(MoomooPositionSnapshotError) as exc_info:
        _run(ctx, quote_ctx)

    assert ctx.closed is True
    assert "second current position query failed" in str(exc_info.value)
    assert "raw account response" not in str(exc_info.value)


def test_config_is_fixed_to_live_us_and_requires_binding_secret():
    with pytest.raises(ValueError, match="env=LIVE"):
        _config(env="SIMULATE")
    with pytest.raises(ValueError, match="market=US"):
        _config(market="HK")
    with pytest.raises(ValueError, match="at least 32"):
        PositionSnapshotConfig(account_binding_secret="too-short")


def test_bounded_worker_is_terminated_after_overall_timeout(monkeypatch):
    class Receiver:
        def poll(self, _timeout):
            return False

        def close(self):
            pass

    class Sender:
        def close(self):
            pass

    class Process:
        def __init__(self):
            self.alive = True
            self.started = False
            self.terminated = False

        def start(self):
            self.started = True

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    process = Process()

    class ProcessContext:
        def Pipe(self, duplex=False):
            assert duplex is False
            return Receiver(), Sender()

        def Process(self, **_kwargs):
            return process

    monkeypatch.setattr(
        snapshot_module.multiprocessing,
        "get_context",
        lambda method: ProcessContext(),
    )
    config = replace(_config(), overall_timeout=0.001)

    with pytest.raises(MoomooPositionSnapshotError, match="overall timeout"):
        snapshot_module._run_position_snapshot_probe_bounded(config)

    assert process.started is True
    assert process.terminated is True


def test_module_has_no_storage_dependency_or_trade_action_reference():
    source = Path(snapshot_module.__file__).read_text(encoding="utf-8")
    assert "src.storage" not in source
    assert "journal.ledger" not in source
    for forbidden in (
        "unlock_trade",
        "place_order",
        "place_combo_order",
        "modify_order",
        "change_order",
        "cancel_all_order",
    ):
        assert forbidden not in source
