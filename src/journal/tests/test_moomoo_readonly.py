from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import src.journal.brokers.moomoo_readonly as readonly_module
from src.journal.brokers.moomoo_readonly import (
    MAX_FEE_BATCH_SIZE,
    MoomooReadonlyError,
    ProbeConfig,
    run_readonly_probe,
)


class _EnumValues:
    REAL = "REAL"
    SIMULATE = "SIMULATE"
    US = "US"
    HK = "HK"
    FUTUINC = "FUTUINC"


SDK = SimpleNamespace(
    RET_OK=0,
    TrdEnv=_EnumValues,
    TrdMarket=_EnumValues,
    SecurityFirm=_EnumValues,
)


class FakeContext:
    def __init__(self, *, accounts, orders, deals, fail_accounts_once=False):
        self.accounts = accounts
        self.orders = orders
        self.deals = deals
        self.fail_accounts_once = fail_accounts_once
        self.account_calls = 0
        self.order_calls = []
        self.deal_calls = []
        self.fee_calls = []
        self.closed = False

    def get_acc_list(self):
        self.account_calls += 1
        if self.fail_accounts_once and self.account_calls == 1:
            return 1, "temporary error"
        return 0, pd.DataFrame(self.accounts)

    def history_order_list_query(self, **kwargs):
        self.order_calls.append(kwargs)
        return 0, pd.DataFrame(self.orders)

    def history_deal_list_query(self, **kwargs):
        self.deal_calls.append(kwargs)
        return 0, pd.DataFrame(self.deals)

    def order_fee_query(self, **kwargs):
        self.fee_calls.append(kwargs)
        rows = [
            {
                "order_id": order_id,
                "fee_amount": 1.25,
                "fee_details": [("Commission", 1.25)],
                "acc_id": "must-not-export",
            }
            for order_id in kwargs["order_id_list"]
        ]
        return 0, pd.DataFrame(rows)

    def close(self):
        self.closed = True


class FakeQuoteContext:
    def __init__(self, *, snapshots=None, fail=False):
        self.snapshots = snapshots or []
        self.fail = fail
        self.snapshot_calls = []
        self.closed = False

    def get_market_snapshot(self, codes):
        self.snapshot_calls.append(list(codes))
        if self.fail:
            return 1, "quote entitlement unavailable"
        requested = set(codes)
        return 0, pd.DataFrame(
            row for row in self.snapshots if row.get("code") in requested
        )

    def close(self):
        self.closed = True


def _config(*, days=8, acc_id=None):
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 7, 1, tzinfo=zone)
    return ProbeConfig(
        start=start,
        end=start + timedelta(days=days),
        env="LIVE",
        market="US",
        acc_id=acc_id,
        retries=1,
        retry_delay=0,
    )


def _real_account(account_id="101"):
    return {
        "acc_id": account_id,
        "trd_env": "REAL",
        "trdmarket_auth": ["US", "HK"],
        "acc_status": "ACTIVE",
        "card_num": "must-not-export",
    }


def _orders(count):
    return [
        {
            "order_id": f"o{index}",
            "code": "US.AAPL260717C00200000",
            "stock_name": "Example",
            "order_market": "US",
            "trd_side": "BUY" if index % 2 == 0 else "SELL",
            "order_type": "NORMAL",
            "order_status": "FILLED_ALL",
            "qty": 1,
            "price": 2.5,
            "create_time": "2026-07-01 09:30:00",
            "updated_time": "2026-07-01 09:31:00",
            "dealt_qty": 1,
            "dealt_avg_price": 2.5,
            "currency": "USD",
            "strategy_type": "SINGLE",
            "combo_legs": [],
            "acc_id": "must-not-export",
        }
        for index in range(count)
    ]


def _deals():
    return [
        {
            "deal_id": "d1",
            "order_id": "o0",
            "code": "US.AAPL260717C00200000",
            "stock_name": "Example",
            "deal_market": "US",
            "trd_side": "BUY",
            "qty": 1,
            "price": 2.5,
            "create_time": "2026-07-01 09:30:30",
            "status": "OK",
            "card_num": "must-not-export",
        }
    ]


def test_live_probe_chunks_dedupes_and_caps_fee_batches():
    ctx = FakeContext(accounts=[_real_account()], orders=_orders(405), deals=_deals())
    result = run_readonly_probe(
        _config(),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert ctx.closed is True
    assert len(ctx.order_calls) == 2
    assert len(ctx.deal_calls) == 2
    assert all(call["trd_env"] == SDK.TrdEnv.REAL for call in ctx.order_calls)
    assert all(call["acc_id"] == 101 for call in ctx.order_calls + ctx.deal_calls)
    assert all(
        datetime.strptime(call["end"], "%Y-%m-%d %H:%M:%S")
        - datetime.strptime(call["start"], "%Y-%m-%d %H:%M:%S")
        <= timedelta(days=7)
        for call in ctx.order_calls
    )
    assert [len(call["order_id_list"]) for call in ctx.fee_calls] == [MAX_FEE_BATCH_SIZE, 5]
    assert all(call["acc_id"] == 101 for call in ctx.fee_calls)

    assert result.summary["journal_database_written"] is False
    assert result.summary["ok"] is True
    assert result.summary["analysis_ready"] is False
    assert result.summary["reconciliation_status"] == "failed"
    assert result.summary["counts"]["orders"] == 405
    assert result.summary["counts"]["fills"] == 1
    assert result.summary["counts"]["fees"] == 405
    assert result.summary["counts"]["option_activity_rows"] == 1
    assert result.summary["deduplicated_rows"] == {
        "orders": 405,
        "fills": 1,
        "fees": 0,
        "contract_specs": 0,
    }
    assert result.summary["fee_totals_by_currency"] == {"USD": 506.25}
    assert result.summary["reconciliation"] == {
        "filled_orders_without_fills": 404,
        "fills_without_orders": 0,
        "filled_quantity_mismatches": 0,
        "fill_code_mismatches": 0,
        "fill_side_mismatches": 0,
        "fill_average_price_mismatches": 0,
        "filled_orders_without_fees": 0,
        "unsupported_combo_orders": 0,
    }
    assert result.summary["warnings"] == [
        "filled_orders_without_fills=404",
    ]

    encoded = json.dumps(result.export_payload)
    assert "acc_id" not in encoded
    assert "card_num" not in encoded
    assert len(result.export_payload["records"]["orders"]) == 405
    assert len(result.export_payload["records"]["deals"]) == 1
    assert len(result.export_payload["records"]["fees"]) == 405


def test_single_option_execution_captures_broker_contract_specification():
    ctx = FakeContext(
        accounts=[_real_account()],
        orders=_orders(1),
        deals=_deals(),
    )
    quote_ctx = FakeQuoteContext(
        snapshots=[{
            "code": "US.AAPL260717C00200000",
            "lot_size": 100,
            "option_contract_size": 100,
            "option_contract_multiplier": 100,
        }]
    )

    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        quote_context_factory=lambda _config, _sdk: quote_ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert result.summary["analysis_ready"] is True
    assert result.summary["contract_spec_status"] == "complete"
    assert result.summary["counts"]["contract_specs"] == 1
    assert quote_ctx.snapshot_calls == [["US.AAPL260717C00200000"]]
    assert result.export_payload["records"]["contract_specs"] == [{
        "code": "US.AAPL260717C00200000",
        "lot_size": 100,
        "option_contract_size": 100,
        "option_contract_multiplier": 100,
    }]


def test_auto_selection_refuses_ambiguous_real_accounts():
    ctx = FakeContext(
        accounts=[_real_account("101"), _real_account("202")],
        orders=[],
        deals=[],
    )

    with pytest.raises(MoomooReadonlyError, match="ambiguous"):
        run_readonly_probe(
            _config(),
            sdk=SDK,
            context_factory=lambda _config, _sdk: ctx,
            tcp_probe=lambda *_args: None,
            sleeper=lambda _seconds: None,
        )
    assert ctx.order_calls == []
    assert ctx.closed is True


def test_explicit_account_is_validated_against_environment_and_market():
    ctx = FakeContext(
        accounts=[
            _real_account("101"),
            {
                "acc_id": "202",
                "trd_env": "SIMULATE",
                "trdmarket_auth": ["US"],
                "acc_status": "ACTIVE",
            },
        ],
        orders=[],
        deals=[],
    )

    with pytest.raises(MoomooReadonlyError, match="not one active account"):
        run_readonly_probe(
            _config(acc_id="202"),
            sdk=SDK,
            context_factory=lambda _config, _sdk: ctx,
            tcp_probe=lambda *_args: None,
            sleeper=lambda _seconds: None,
        )


def test_transient_sdk_error_is_retried_without_exposing_response():
    ctx = FakeContext(
        accounts=[_real_account()],
        orders=[],
        deals=[],
        fail_accounts_once=True,
    )
    sleeps = []
    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=sleeps.append,
    )

    assert result.summary["counts"]["orders"] == 0
    assert ctx.account_calls == 2
    assert sleeps == [0]


def test_tcp_preflight_runs_before_context_construction():
    constructed = []

    def fail_probe(*_args):
        raise MoomooReadonlyError("offline")

    with pytest.raises(MoomooReadonlyError, match="offline"):
        run_readonly_probe(
            _config(days=1),
            sdk=SDK,
            context_factory=lambda *_args: constructed.append(True),
            tcp_probe=fail_probe,
        )
    assert constructed == []


def test_simulate_uses_order_history_only():
    base = _config(days=1)
    config = ProbeConfig(
        start=base.start,
        end=base.end,
        env="SIMULATE",
        market="US",
        retries=0,
    )
    ctx = FakeContext(
        accounts=[
            {
                "acc_id": "303",
                "trd_env": "SIMULATE",
                "trdmarket_auth": ["US"],
                "acc_status": "ACTIVE",
            }
        ],
        orders=_orders(1),
        deals=_deals(),
    )
    result = run_readonly_probe(
        config,
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert ctx.order_calls[0]["trd_env"] == SDK.TrdEnv.SIMULATE
    assert ctx.deal_calls == []
    assert ctx.fee_calls == []
    assert result.summary["counts"]["fills"] == 0
    assert result.summary["counts"]["fees"] == 0
    assert all(value is None for value in result.summary["reconciliation"].values())
    assert result.summary["analysis_ready"] is False
    assert result.summary["reconciliation_status"] == "not_applicable"


def test_live_probe_is_analysis_ready_only_after_full_reconciliation():
    orders = _orders(2)
    deals = [
        {
            "deal_id": f"d{index}",
            "order_id": f"o{index}",
            "code": order["code"],
            "stock_name": order["stock_name"],
            "deal_market": "US",
            "trd_side": order["trd_side"],
            "qty": 1,
            "price": 2.5,
            "create_time": "2026-07-01 09:30:30",
            "status": "OK",
        }
        for index, order in enumerate(orders)
    ]
    ctx = FakeContext(accounts=[_real_account()], orders=orders, deals=deals)

    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert result.summary["analysis_ready"] is True
    assert result.summary["reconciliation_status"] == "passed"
    assert result.summary["warnings"] == []
    assert all(value == 0 for value in result.summary["reconciliation"].values())


def test_live_probe_records_complete_no_activity_window_and_account_binding():
    ctx = FakeContext(accounts=[_real_account()], orders=[], deals=[])
    config = replace(
        _config(days=1),
        account_binding_secret="local-test-secret-that-is-long-enough-1234",
    )

    result = run_readonly_probe(
        config,
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert result.summary["analysis_ready"] is True
    assert result.summary["retrieval_complete"] is True
    assert result.summary["coverage_complete"] is True
    assert result.summary["has_activity"] is False
    assert result.summary["warnings"] == ["no_filled_orders_in_window"]
    binding = result.export_payload["account"]["binding"]
    assert len(binding) == 64
    assert binding.isalnum()
    assert set(result.export_payload["account"]) == {
        "environment",
        "market",
        "selection",
        "binding",
    }


@pytest.mark.parametrize("missing_field", ["strategy_type", "combo_legs"])
def test_nonempty_order_history_requires_combo_capability_fields(missing_field):
    order = _orders(1)[0]
    order.pop(missing_field)
    ctx = FakeContext(accounts=[_real_account()], orders=[order], deals=[])

    with pytest.raises(MoomooReadonlyError, match="combo capability fields"):
        run_readonly_probe(
            _config(days=1),
            sdk=SDK,
            context_factory=lambda _config, _sdk: ctx,
            tcp_probe=lambda *_args: None,
            sleeper=lambda _seconds: None,
        )


def test_sdk_not_applicable_strategy_is_normalized_only_without_combo_legs():
    order = _orders(1)[0]
    order["strategy_type"] = "N/A"
    ctx = FakeContext(accounts=[_real_account()], orders=[order], deals=_deals())

    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert result.export_payload["records"]["orders"][0]["strategy_type"] == "NONE"
    assert result.export_payload["records"]["orders"][0]["combo_legs"] == []


def test_sdk_not_applicable_strategy_with_declared_legs_fails_closed():
    order = _orders(1)[0]
    order.update(
        {
            "strategy_type": "N/A",
            "combo_legs": [
                {"code": "US.AAPL260717C00200000", "trd_side": "BUY", "qty_ratio": 1},
                {"code": "US.AAPL260717C00210000", "trd_side": "SELL", "qty_ratio": 1},
            ],
        }
    )
    ctx = FakeContext(accounts=[_real_account()], orders=[order], deals=[])

    with pytest.raises(MoomooReadonlyError, match="unsupported option strategy"):
        run_readonly_probe(
            _config(days=1),
            sdk=SDK,
            context_factory=lambda _config, _sdk: ctx,
            tcp_probe=lambda *_args: None,
            sleeper=lambda _seconds: None,
        )


def test_empty_order_history_does_not_require_combo_capability_columns():
    ctx = FakeContext(accounts=[_real_account()], orders=[], deals=[])

    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert result.summary["analysis_ready"] is True
    assert result.summary["has_activity"] is False


def test_reconciliation_checks_code_side_and_weighted_average():
    orders = _orders(3)
    deals = [
        {
            "deal_id": f"d{index}",
            "order_id": f"o{index}",
            "code": "US.WRONG" if index == 0 else order["code"],
            "trd_side": "BUY" if index == 1 else order["trd_side"],
            "qty": 1,
            "price": 9.5 if index == 2 else 2.5,
            "create_time": "2026-07-01 09:30:30",
        }
        for index, order in enumerate(orders)
    ]
    ctx = FakeContext(accounts=[_real_account()], orders=orders, deals=deals)

    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    reconciliation = result.summary["reconciliation"]
    assert reconciliation["fill_code_mismatches"] == 1
    assert reconciliation["fill_side_mismatches"] == 1
    assert reconciliation["fill_average_price_mismatches"] == 1
    assert result.summary["analysis_ready"] is False


def test_combo_order_is_structured_reconciled_and_not_a_source_blocker():
    order = _orders(1)[0]
    order.update(
        {
            "code": "US.COMBO",
            "trd_side": "BUY",
            "qty": 2,
            "dealt_qty": 2,
            "dealt_avg_price": 99,
            "strategy_type": "SPREAD",
            "combo_legs": [
                SimpleNamespace(
                    code="US.AAPL260717C00200000",
                    trd_side="BUY",
                    qty_ratio=1,
                    acc_id="must-not-export",
                ),
                SimpleNamespace(
                    code="US.AAPL260717C00210000",
                    trd_side="SELL",
                    qty_ratio=2,
                    acc_id="must-not-export",
                ),
            ],
        }
    )
    deals = [
        {
            "deal_id": "d-buy",
            "order_id": "o0",
            "code": "US.AAPL260717C00200000",
            "deal_market": "US",
            "trd_side": "BUY",
            "qty": 2,
            "price": 3,
            "create_time": "2026-07-01 09:30:30",
            "status": "OK",
        },
        {
            "deal_id": "d-sell",
            "order_id": "o0",
            "code": "US.AAPL260717C00210000",
            "deal_market": "US",
            "trd_side": "SELL",
            "qty": 4,
            "price": 1,
            "create_time": "2026-07-01 09:30:30",
            "status": "OK",
        },
    ]
    ctx = FakeContext(accounts=[_real_account()], orders=[order], deals=deals)
    quote_ctx = FakeQuoteContext(
        snapshots=[
            {
                "code": "US.AAPL260717C00210000",
                "lot_size": 100,
                "option_contract_size": 100,
                "option_contract_multiplier": 100,
                "acc_id": "must-not-export",
            },
            {
                "code": "US.AAPL260717C00200000",
                "lot_size": 100,
                "option_contract_size": 100,
                "option_contract_multiplier": 100,
                "acc_id": "must-not-export",
            },
        ]
    )

    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        quote_context_factory=lambda _config, _sdk: quote_ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert result.summary["analysis_ready"] is True
    assert result.summary["reconciliation_status"] == "passed"
    assert result.summary["reconciliation"] == {
        "filled_orders_without_fills": 0,
        "fills_without_orders": 0,
        "filled_quantity_mismatches": 0,
        "fill_code_mismatches": 0,
        "fill_side_mismatches": 0,
        "fill_average_price_mismatches": 0,
        "filled_orders_without_fees": 0,
        "unsupported_combo_orders": 1,
    }
    assert result.summary["warnings"] == ["execution_group_observations=1"]
    assert result.summary["contract_spec_status"] == "complete"
    assert result.summary["counts"]["contract_specs"] == 2
    assert quote_ctx.snapshot_calls == [[
        "US.AAPL260717C00200000",
        "US.AAPL260717C00210000",
    ]]
    assert quote_ctx.closed is True
    exported_order = result.export_payload["records"]["orders"][0]
    assert exported_order["strategy_type"] == "SPREAD"
    assert exported_order["combo_legs"] == [
        {
            "code": "US.AAPL260717C00200000",
            "trd_side": "BUY",
            "qty_ratio": 1,
        },
        {
            "code": "US.AAPL260717C00210000",
            "trd_side": "SELL",
            "qty_ratio": 2,
        },
    ]
    assert "must-not-export" not in json.dumps(exported_order)
    assert result.export_payload["records"]["contract_specs"] == [
        {
            "code": "US.AAPL260717C00200000",
            "lot_size": 100,
            "option_contract_size": 100,
            "option_contract_multiplier": 100,
        },
        {
            "code": "US.AAPL260717C00210000",
            "lot_size": 100,
            "option_contract_size": 100,
            "option_contract_multiplier": 100,
        },
    ]
    assert "must-not-export" not in json.dumps(
        result.export_payload["records"]["contract_specs"]
    )


def test_combo_contract_snapshot_failure_degrades_without_losing_trade_evidence():
    order = _orders(1)[0]
    order.update(
        {
            "code": "US.COMBO",
            "strategy_type": "SPREAD",
            "combo_legs": [
                {"code": "US.AAPL260717C00200000", "trd_side": "BUY", "qty_ratio": 1},
                {"code": "US.AAPL260717C00210000", "trd_side": "SELL", "qty_ratio": 1},
            ],
        }
    )
    deals = [
        {
            "deal_id": "d-buy",
            "order_id": "o0",
            "code": "US.AAPL260717C00200000",
            "deal_market": "US",
            "trd_side": "BUY",
            "qty": 1,
            "price": 3,
            "create_time": "2026-07-01 09:30:30",
            "status": "OK",
        },
        {
            "deal_id": "d-sell",
            "order_id": "o0",
            "code": "US.AAPL260717C00210000",
            "deal_market": "US",
            "trd_side": "SELL",
            "qty": 1,
            "price": 0.5,
            "create_time": "2026-07-01 09:30:30",
            "status": "OK",
        },
    ]
    ctx = FakeContext(accounts=[_real_account()], orders=[order], deals=deals)
    quote_ctx = FakeQuoteContext(fail=True)

    result = run_readonly_probe(
        _config(days=1),
        sdk=SDK,
        context_factory=lambda _config, _sdk: ctx,
        quote_context_factory=lambda _config, _sdk: quote_ctx,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
    )

    assert result.summary["analysis_ready"] is True
    assert result.summary["reconciliation_status"] == "passed"
    assert result.summary["contract_spec_status"] == "unavailable"
    assert result.summary["warnings"] == [
        "execution_group_observations=1",
        "execution_group_contract_specs_unavailable",
    ]
    assert result.export_payload["records"]["contract_specs"] == []
    assert quote_ctx.closed is True


def test_duplicate_order_id_with_drifting_combo_definition_is_rejected():
    first = _orders(1)[0]
    first.update(
        {
            "strategy_type": "SPREAD",
            "combo_legs": [
                {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 1},
                {"code": "US.MSFT", "trd_side": "SELL", "qty_ratio": 1},
            ],
        }
    )
    second = dict(first)
    second["combo_legs"] = [
        {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 1},
        {"code": "US.MSFT", "trd_side": "SELL", "qty_ratio": 2},
    ]

    class DriftingContext(FakeContext):
        def history_order_list_query(self, **kwargs):
            self.order_calls.append(kwargs)
            row = first if len(self.order_calls) == 1 else second
            return 0, pd.DataFrame([row])

    ctx = DriftingContext(accounts=[_real_account()], orders=[], deals=[])

    with pytest.raises(MoomooReadonlyError, match="drifting combo definition"):
        run_readonly_probe(
            _config(days=8),
            sdk=SDK,
            context_factory=lambda _config, _sdk: ctx,
            tcp_probe=lambda *_args: None,
            sleeper=lambda _seconds: None,
        )


@pytest.mark.parametrize(
    "legs",
    [
        [],
        [{"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 1}],
        [
            {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 1},
            {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 2},
        ],
        [
            {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 0},
            {"code": "US.MSFT", "trd_side": "SELL", "qty_ratio": 1},
        ],
    ],
)
def test_probe_rejects_malformed_combo_leg_contract(legs):
    order = _orders(1)[0]
    order.update({"strategy_type": "SPREAD", "combo_legs": legs})
    ctx = FakeContext(accounts=[_real_account()], orders=[order], deals=[])

    with pytest.raises(MoomooReadonlyError, match="combo"):
        run_readonly_probe(
            _config(days=1),
            sdk=SDK,
            context_factory=lambda _config, _sdk: ctx,
            tcp_probe=lambda *_args: None,
            sleeper=lambda _seconds: None,
        )


def test_probe_config_normalizes_programmatic_window_to_market_timezone():
    config = ProbeConfig(
        start=datetime(2026, 7, 1, 13, 30, tzinfo=ZoneInfo("UTC")),
        end=datetime(2026, 7, 1, 20, 0, tzinfo=ZoneInfo("UTC")),
        market="US",
    )

    assert config.start.isoformat() == "2026-07-01T09:30:00-04:00"
    assert config.end.isoformat() == "2026-07-01T16:00:00-04:00"


def test_probe_config_rejects_unbounded_history_window():
    zone = ZoneInfo("America/New_York")
    with pytest.raises(ValueError, match="cannot exceed"):
        ProbeConfig(
            start=datetime(2025, 1, 1, tzinfo=zone),
            end=datetime(2026, 7, 1, tzinfo=zone),
        )


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
        readonly_module.multiprocessing,
        "get_context",
        lambda method: ProcessContext(),
    )
    config = replace(_config(days=1), overall_timeout=0.001)

    with pytest.raises(MoomooReadonlyError, match="overall timeout"):
        readonly_module._run_readonly_probe_bounded(config)

    assert process.started is True
    assert process.terminated is True


def test_default_context_factory_uses_bounded_sdk_handshake(monkeypatch):
    calls = []

    class TradeContext:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._query_timeout = 12
            self.connect_timeout = None

        def set_sync_query_connect_timeout(self, timeout):
            self.connect_timeout = timeout

    sdk = SimpleNamespace(
        TrdMarket=_EnumValues,
        SecurityFirm=_EnumValues,
        OpenSecTradeContext=TradeContext,
    )
    monkeypatch.setattr(
        readonly_module,
        "ensure_opend_ready",
        lambda host, port, **kwargs: calls.append((host, port, kwargs)),
    )
    monkeypatch.setattr(readonly_module, "suppress_moomoo_sdk_console", lambda: True)

    config = _config(days=1)
    ctx = readonly_module._default_context_factory(config, sdk)

    assert calls == [
        (
            config.host,
            config.port,
            {"tcp_timeout": config.connect_timeout, "ready_timeout": 3.0},
        )
    ]
    assert ctx._query_timeout == config.query_timeout
    assert ctx.connect_timeout == config.query_timeout
