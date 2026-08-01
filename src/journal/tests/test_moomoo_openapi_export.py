# -*- coding: utf-8 -*-
"""Tests for the pure Moomoo read-only export parser."""
from __future__ import annotations

import copy
import json
from datetime import timezone
from decimal import Decimal

import pytest

from src.journal.brokers.moomoo_openapi_export import (
    OPENAPI_EXPORT_PARSER_NAME,
    OPENAPI_EXPORT_PARSER_VERSION,
    MoomooOpenApiExportError,
    OpenApiExportPreview,
    parse_openapi_export,
    parse_readonly_export,
)


def _window() -> dict[str, object]:
    return {
        "start": "2026-07-01T09:00:00-04:00",
        "end": "2026-07-01T16:00:00-04:00",
        "timezone": "America/New_York",
        "chunks": 1,
        "max_chunk_days": 7,
    }


def _order(
    order_id: str,
    *,
    code: str,
    create_time: str,
    side: str,
    status: str,
    dealt_qty: int,
    dealt_avg_price: float,
) -> dict[str, object]:
    return {
        "order_id": order_id,
        "code": code,
        "stock_name": "Deidentified instrument",
        "order_market": "US",
        "trd_side": side,
        "order_type": "NORMAL",
        "order_status": status,
        "qty": 1,
        "price": 2.5,
        "create_time": create_time,
        "updated_time": create_time,
        "dealt_qty": dealt_qty,
        "dealt_avg_price": dealt_avg_price,
        "time_in_force": "DAY",
        "fill_outside_rth": "N/A",
        "session": "RTH",
        "currency": "USD",
    }


def _payload() -> dict[str, object]:
    window = _window()
    reconciliation = {
        "filled_orders_without_fills": 0,
        "fills_without_orders": 0,
        "filled_quantity_mismatches": 0,
        "fill_code_mismatches": 0,
        "fill_side_mismatches": 0,
        "fill_average_price_mismatches": 0,
        "filled_orders_without_fees": 0,
    }
    return {
        "schema": "dsa.moomoo.readonly-export.v1",
        "generated_at": "2026-07-01T20:01:00+00:00",
        "mode": "read_only",
        "journal_database_written": False,
        "account": {
            "environment": "LIVE",
            "market": "US",
            "selection": "unique_auto",
        },
        "window": window,
        "summary": {
            "ok": True,
            "analysis_ready": True,
            "reconciliation_status": "passed",
            "warnings": [],
            "mode": "moomoo_readonly_probe",
            "journal_database_written": False,
            "environment": "LIVE",
            "market": "US",
            "account_selection": "unique_auto",
            "window": copy.deepcopy(window),
            "counts": {
                "orders": 2,
                "filled_orders": 1,
                "fills": 1,
                "fees": 1,
                "unique_instruments": 1,
                "option_activity_rows": 1,
            },
            "activity_sides": {"buy": 1, "sell": 0, "other": 0},
            "fee_totals_by_currency": {"USD": 1.25},
            "activity_time_range": {
                "first": "2026-07-01 09:30:01",
                "last": "2026-07-01 09:30:01",
            },
            "deduplicated_rows": {"orders": 0, "fills": 0, "fees": 0},
            "reconciliation": reconciliation,
            "fee_batches": 1,
        },
        "records": {
            "orders": [
                _order(
                    "o-filled",
                    code="US.AAPL260717C00200000",
                    create_time="2026-07-01 09:30:00",
                    side="BUY",
                    status="FILLED_ALL",
                    dealt_qty=1,
                    dealt_avg_price=2.5,
                ),
                _order(
                    "o-cancelled",
                    code="US.MSFT",
                    create_time="2026-07-01 10:00:00",
                    side="SELL",
                    status="CANCELLED_ALL",
                    dealt_qty=0,
                    dealt_avg_price=0.0,
                ),
            ],
            "deals": [
                {
                    "deal_id": "d-filled",
                    "order_id": "o-filled",
                    "code": "US.AAPL260717C00200000",
                    "stock_name": "Deidentified instrument",
                    "deal_market": "US",
                    "trd_side": "BUY",
                    "qty": 1,
                    "price": 2.5,
                    "create_time": "2026-07-01 09:30:01",
                    "status": "OK",
                }
            ],
            "fees": [
                {
                    "order_id": "o-filled",
                    "fee_amount": 1.25,
                    "fee_details": [
                        ["Platform Fees", 0.25],
                        ["Commission", 1.0],
                    ],
                }
            ],
        },
    }


def _combo_payload() -> dict[str, object]:
    payload = _payload()
    records = _records(payload)
    order = records["orders"][0]
    order.update(
        {
            "code": "US.COMBO",
            "trd_side": "BUY",
            "dealt_avg_price": 99,
            "strategy_type": "SPREAD",
            "combo_legs": [
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
            ],
        }
    )
    records["deals"] = [
        {
            "deal_id": "d-buy",
            "order_id": "o-filled",
            "code": "US.AAPL260717C00200000",
            "stock_name": "Deidentified instrument",
            "deal_market": "US",
            "trd_side": "BUY",
            "qty": 1,
            "price": 3,
            "create_time": "2026-07-01 09:30:01",
            "status": "OK",
        },
        {
            "deal_id": "d-sell",
            "order_id": "o-filled",
            "code": "US.AAPL260717C00210000",
            "stock_name": "Deidentified instrument",
            "deal_market": "US",
            "trd_side": "SELL",
            "qty": 2,
            "price": 1,
            "create_time": "2026-07-01 09:30:01",
            "status": "OK",
        },
    ]
    records["contract_specs"] = [
        {
            "code": "US.AAPL260717C00210000",
            "lot_size": 100,
            "option_contract_size": 100,
            "option_contract_multiplier": 100,
        },
        {
            "code": "US.AAPL260717C00200000",
            "lot_size": 100,
            "option_contract_size": 100,
            "option_contract_multiplier": 100,
        },
    ]
    summary = _summary(payload)
    summary["analysis_ready"] = True
    summary["reconciliation_status"] = "passed"
    summary["warnings"] = ["execution_group_observations=1"]
    summary["contract_spec_status"] = "complete"
    summary["counts"].update(
        {
            "fills": 2,
            "unique_instruments": 2,
            "option_activity_rows": 2,
            "contract_specs": 2,
        }
    )
    summary["deduplicated_rows"]["contract_specs"] = 0
    summary["activity_sides"] = {"buy": 1, "sell": 1, "other": 0}
    summary["reconciliation"]["unsupported_combo_orders"] = 1
    return payload


def _records(payload: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    return payload["records"]  # type: ignore[return-value]


def _summary(payload: dict[str, object]) -> dict[str, object]:
    return payload["summary"]  # type: ignore[return-value]


def test_valid_export_becomes_typed_deidentified_preview() -> None:
    result = parse_openapi_export(_payload())

    assert isinstance(result, OpenApiExportPreview)
    assert result.metadata.parser_name == OPENAPI_EXPORT_PARSER_NAME
    assert result.metadata.parser_version == OPENAPI_EXPORT_PARSER_VERSION
    assert result.metadata.analysis_ready is True
    assert result.metadata.analysis_level == "exact"
    assert result.metadata.journal_database_written is False
    assert result.metadata.generated_at.tzinfo is timezone.utc
    assert result.metadata.window_start.isoformat() == "2026-07-01T09:00:00-04:00"
    assert result.metadata.window_end.isoformat() == "2026-07-01T16:00:00-04:00"
    assert len(result.metadata.source_sha256) == 64
    assert len(result.metadata.evidence_sha256) == 64
    assert len(result.metadata.batch_key) == 64

    assert [order.source_order_id for order in result.orders] == [
        "o-filled",
        "o-cancelled",
    ]
    assert result.orders[0].order_quantity == Decimal("1")
    assert result.orders[0].fill_outside_rth is None
    assert result.orders[0].combo_definition_available is False
    assert result.fills[0].source_deal_id == "d-filled"
    assert result.fills[0].currency == "USD"
    assert result.fees[0].fee_components == (
        ("Commission", Decimal("1.0")),
        ("Platform Fees", Decimal("0.25")),
    )
    assert all(len(row.source_record_sha256) == 64 for row in result.orders)
    assert all(len(row.source_record_sha256) == 64 for row in result.fills)
    assert all(len(row.source_record_sha256) == 64 for row in result.fees)

    public_summary = result.summary()
    assert public_summary["counts"] == {
        "orders": 2,
        "fills": 1,
        "fees": 1,
        "contract_specs": 0,
        "rejected": 0,
    }
    encoded = json.dumps(public_summary)
    assert "o-filled" not in encoded
    assert "d-filled" not in encoded


def test_combo_order_is_typed_reconciled_and_not_a_source_blocker() -> None:
    result = parse_openapi_export(_combo_payload())

    combo = result.orders[0]
    assert combo.strategy_type == "SPREAD"
    assert [
        (leg.raw_symbol, leg.side, leg.quantity_ratio)
        for leg in combo.combo_legs
    ] == [
        ("US.AAPL260717C00200000", "BUY", Decimal("1")),
        ("US.AAPL260717C00210000", "SELL", Decimal("2")),
    ]
    assert result.metadata.analysis_ready is True
    assert result.metadata.analysis_level == "exact"
    assert result.metadata.reconciliation_status == "passed"
    assert result.metadata.reconciliation.unsupported_combo_orders == 1
    assert result.metadata.reconciliation.fill_code_mismatches == 0
    assert result.metadata.reconciliation.fill_side_mismatches == 0
    assert result.metadata.reconciliation.filled_quantity_mismatches == 0
    assert result.metadata.reconciliation.fill_average_price_mismatches == 0
    assert result.metadata.warnings == (
        "execution_group_observations=1",
    )


def test_contract_specs_are_typed_and_hash_order_is_stable() -> None:
    baseline_payload = _combo_payload()
    reordered_payload = copy.deepcopy(baseline_payload)
    _records(reordered_payload)["contract_specs"].reverse()

    baseline = parse_openapi_export(baseline_payload)
    reordered = parse_openapi_export(reordered_payload)

    assert [spec.raw_symbol for spec in baseline.contract_specs] == [
        "US.AAPL260717C00200000",
        "US.AAPL260717C00210000",
    ]
    assert all(spec.lot_size == Decimal("100") for spec in baseline.contract_specs)
    assert all(
        spec.option_contract_size == Decimal("100")
        for spec in baseline.contract_specs
    )
    assert all(
        spec.option_contract_multiplier == Decimal("100")
        and spec.resolved_multiplier == Decimal("100")
        and len(spec.source_record_sha256) == 64
        for spec in baseline.contract_specs
    )
    assert baseline.metadata.contract_spec_status == "complete"
    assert baseline.metadata.contract_spec_observation_count == 2
    assert baseline.metadata.source_sha256 == reordered.metadata.source_sha256
    assert baseline.metadata.evidence_sha256 == reordered.metadata.evidence_sha256
    assert baseline.metadata.batch_key == reordered.metadata.batch_key


def test_legacy_combo_only_blocker_is_accepted_and_normalized() -> None:
    payload = _combo_payload()
    records = _records(payload)
    records.pop("contract_specs")
    summary = _summary(payload)
    summary["analysis_ready"] = False
    summary["reconciliation_status"] = "failed"
    summary["warnings"] = ["combo_order_requires_group_projection=1"]
    summary.pop("contract_spec_status")
    summary["counts"].pop("contract_specs")
    summary["deduplicated_rows"].pop("contract_specs")

    result = parse_openapi_export(payload)

    assert result.metadata.analysis_ready is True
    assert result.metadata.analysis_level == "exact"
    assert result.metadata.reconciliation_status == "passed"
    assert result.metadata.warnings == ("execution_group_observations=1",)
    assert result.metadata.contract_spec_status == "not_available"
    assert result.contract_specs == ()


def test_combo_hashes_use_normalized_leg_order_and_leg_ratios() -> None:
    first = _combo_payload()
    reordered = copy.deepcopy(first)
    _records(reordered)["orders"][0]["combo_legs"].reverse()
    changed = copy.deepcopy(first)
    _records(changed)["orders"][0]["combo_legs"][1]["qty_ratio"] = 3
    _records(changed)["deals"][1]["qty"] = 3

    baseline = parse_openapi_export(first)
    same = parse_openapi_export(reordered)
    different = parse_openapi_export(changed)

    assert baseline.metadata.source_sha256 == same.metadata.source_sha256
    assert baseline.metadata.evidence_sha256 == same.metadata.evidence_sha256
    assert baseline.metadata.source_sha256 != different.metadata.source_sha256
    assert baseline.metadata.evidence_sha256 != different.metadata.evidence_sha256


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: _records(payload)["orders"][0].update(
                combo_legs=[
                    {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 1}
                ]
            ),
            "at least two legs",
        ),
        (
            lambda payload: _records(payload)["orders"][0]["combo_legs"][0].update(
                qty_ratio=0
            ),
            "greater than zero",
        ),
        (
            lambda payload: _records(payload)["orders"][0]["combo_legs"][1].update(
                code="US.AAPL260717C00200000", trd_side="BUY"
            ),
            "duplicate code/side",
        ),
    ],
)
def test_combo_definition_validation_fails_closed(mutate, message) -> None:
    payload = _combo_payload()
    mutate(payload)

    with pytest.raises(MoomooOpenApiExportError, match=message):
        parse_openapi_export(payload)


@pytest.mark.parametrize("present_field", ["strategy_type", "combo_legs"])
def test_new_combo_capability_fields_must_appear_as_a_pair(present_field) -> None:
    payload = _payload()
    order = _records(payload)["orders"][0]
    order[present_field] = "SINGLE" if present_field == "strategy_type" else []

    with pytest.raises(MoomooOpenApiExportError, match="must appear together"):
        parse_openapi_export(payload)


@pytest.mark.parametrize(
    ("strategy_type", "legs", "message"),
    [
        ("SPREAD", [], "required for SPREAD"),
        ("NONE", [
            {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 1},
            {"code": "US.MSFT", "trd_side": "SELL", "qty_ratio": 1},
        ], "not allowed for NONE"),
        ("SINGLE", [
            {"code": "US.AAPL", "trd_side": "BUY", "qty_ratio": 1},
            {"code": "US.MSFT", "trd_side": "SELL", "qty_ratio": 1},
        ], "not allowed for SINGLE"),
        ("N/A", [], "strategy_type is unsupported"),
        ("FUTURE_UNKNOWN", [], "strategy_type is unsupported"),
    ],
)
def test_strategy_type_and_declared_legs_must_be_consistent(
    strategy_type, legs, message
) -> None:
    payload = _payload()
    order = _records(payload)["orders"][0]
    order.update({"strategy_type": strategy_type, "combo_legs": legs})

    with pytest.raises(MoomooOpenApiExportError, match=message):
        parse_openapi_export(payload)


def test_current_single_strategy_capability_is_accepted_without_legs() -> None:
    payload = _payload()
    for order in _records(payload)["orders"]:
        order.update({"strategy_type": "SINGLE", "combo_legs": []})

    result = parse_openapi_export(payload)

    assert all(order.strategy_type == "SINGLE" for order in result.orders)
    assert all(order.combo_legs == () for order in result.orders)
    assert all(order.combo_definition_available for order in result.orders)


def test_legacy_absence_and_explicit_empty_combo_capability_hash_differently() -> None:
    legacy = _payload()
    capable = copy.deepcopy(legacy)
    for order in _records(capable)["orders"]:
        order.update({"strategy_type": "NONE", "combo_legs": []})

    old = parse_openapi_export(legacy)
    current = parse_openapi_export(capable)

    assert all(not order.combo_definition_available for order in old.orders)
    assert all(order.combo_definition_available for order in current.orders)
    assert old.metadata.evidence_sha256 != current.metadata.evidence_sha256
    assert old.metadata.source_sha256 != current.metadata.source_sha256
    assert old.metadata.batch_key != current.metadata.batch_key


def test_combo_reconciliation_checks_each_declared_leg_quantity() -> None:
    payload = _combo_payload()
    _records(payload)["deals"][1]["qty"] = 1
    summary = _summary(payload)
    summary["analysis_ready"] = False
    summary["reconciliation_status"] = "failed"
    summary["reconciliation"]["filled_quantity_mismatches"] = 1
    summary["warnings"] = [
        "filled_quantity_mismatches=1",
        "execution_group_observations=1",
    ]

    result = parse_openapi_export(payload)

    assert result.metadata.reconciliation.filled_quantity_mismatches == 1
    assert result.metadata.reconciliation.fill_average_price_mismatches == 0


def test_combo_time_skew_guard_matches_a_declared_leg_not_parent_fields() -> None:
    payload = _combo_payload()
    order = _records(payload)["orders"][0]
    order["create_time"] = "2026-07-01 09:30:00.474"
    order["updated_time"] = "2026-07-01 09:30:01.099"
    for deal in _records(payload)["deals"]:
        deal["create_time"] = "2026-07-01 09:30:00.100"
    _summary(payload)["activity_time_range"] = {
        "first": "2026-07-01 09:30:00.100",
        "last": "2026-07-01 09:30:00.100",
    }

    result = parse_openapi_export(payload)

    assert result.metadata.warnings == (
        "execution_group_observations=1",
        "broker_fill_precedes_order_create_within_1s=2",
    )

    tampered = copy.deepcopy(payload)
    _records(tampered)["deals"][0]["code"] = "US.NOT_A_DECLARED_LEG"
    with pytest.raises(MoomooOpenApiExportError, match="guarded broker timestamp"):
        parse_openapi_export(tampered)


def test_broker_four_digit_fractional_second_is_normalized() -> None:
    payload = _payload()
    _records(payload)["orders"][0]["updated_time"] = (
        "2026-07-01 09:30:00.1000"
    )

    result = parse_openapi_export(payload)

    assert result.orders[0].source_updated_at.microsecond == 100000


def test_guarded_subsecond_fill_order_skew_is_preserved_and_audited() -> None:
    payload = _payload()
    order = _records(payload)["orders"][0]
    deal = _records(payload)["deals"][0]
    order["create_time"] = "2026-07-01 09:30:00.474"
    order["updated_time"] = "2026-07-01 09:30:01.099"
    deal["create_time"] = "2026-07-01 09:30:00.1000"
    _summary(payload)["activity_time_range"] = {
        "first": deal["create_time"],
        "last": deal["create_time"],
    }

    result = parse_openapi_export(payload)

    assert result.metadata.analysis_ready is True
    assert result.fills[0].filled_at.microsecond == 100000
    assert result.fills[0].filled_at < result.orders[0].ordered_at
    assert result.metadata.warnings == (
        "broker_fill_precedes_order_create_within_1s=1",
    )
    assert result.summary()["warnings"] == [
        "broker_fill_precedes_order_create_within_1s=1"
    ]


@pytest.mark.parametrize(
    ("order_time", "updated_time", "deal_time", "deal_change"),
    [
        (
            "2026-07-01 09:30:01.101",
            "2026-07-01 09:30:01.200",
            "2026-07-01 09:30:00.100",
            {},
        ),
        (
            "2026-07-01 09:30:00.474",
            "2026-07-01 09:30:01.101",
            "2026-07-01 09:30:00.100",
            {},
        ),
        (
            "2026-07-01 09:30:00.474",
            "2026-07-01 09:30:01.099",
            "2026-07-01 09:30:00.100",
            {"status": "CHANGED"},
        ),
        (
            "2026-07-01 09:30:00.474",
            "2026-07-01 09:30:01.099",
            "2026-07-01 09:30:00.100",
            {"code": "US.MSFT260717C00200000"},
        ),
        (
            "2026-07-01 09:30:00.474",
            "2026-07-01 09:30:01.099",
            "2026-07-01 09:30:00.100",
            {"trd_side": "SELL"},
        ),
    ],
)
def test_fill_order_skew_outside_guardrails_still_fails_closed(
    order_time: str,
    updated_time: str,
    deal_time: str,
    deal_change: dict[str, str],
) -> None:
    payload = _payload()
    order = _records(payload)["orders"][0]
    deal = _records(payload)["deals"][0]
    order["create_time"] = order_time
    order["updated_time"] = updated_time
    deal["create_time"] = deal_time
    deal.update(deal_change)

    with pytest.raises(
        MoomooOpenApiExportError,
        match="precedes the parent order outside the guarded",
    ):
        parse_openapi_export(payload)


def test_binary_float_noise_is_removed_without_erasing_small_values() -> None:
    payload = _payload()
    fee = _records(payload)["fees"][0]
    fee["fee_amount"] = 1.2500000000000002
    fee["fee_details"][0][1] = 0.25000000000000006
    fee["fee_details"].append(["Tiny supported fee", 0.0000000000004])
    fee["fee_amount"] += 0.0000000000004
    _summary(payload)["fee_totals_by_currency"]["USD"] = fee["fee_amount"]

    result = parse_openapi_export(payload)

    assert result.fees[0].total_fee == Decimal("1.2500000000004")
    assert dict(result.fees[0].fee_components)["Platform Fees"] == Decimal(
        "0.25"
    )
    assert dict(result.fees[0].fee_components)["Tiny supported fee"] == Decimal(
        "0.0000000000004"
    )

    payload = _payload()
    _records(payload)["fees"][0]["fee_amount"] = 1.2499999999999998
    assert parse_openapi_export(payload).fees[0].total_fee == Decimal("1.25")


def test_compatibility_alias_returns_the_same_parse_contract() -> None:
    payload = _payload()

    assert parse_readonly_export(payload) == parse_openapi_export(payload)


def test_hashes_are_deterministic_across_record_order_and_number_spelling() -> None:
    first = _payload()
    second = copy.deepcopy(first)
    _records(second)["orders"].reverse()
    _records(second)["orders"][1]["qty"] = 1.0
    _records(second)["orders"][1]["dealt_qty"] = 1.0
    _records(second)["deals"][0]["qty"] = 1.0

    left = parse_openapi_export(first)
    right = parse_openapi_export(second)

    assert left.metadata.source_sha256 == right.metadata.source_sha256
    assert left.metadata.evidence_sha256 == right.metadata.evidence_sha256
    assert left.metadata.batch_key == right.metadata.batch_key
    assert left.orders == right.orders


def test_json_float_and_decimal_loaders_have_identical_contract() -> None:
    payload = _payload()
    fee = _records(payload)["fees"][0]
    fee["fee_amount"] = 1.2500000000000002
    fee["fee_details"][0][1] = 0.25000000000000006
    _summary(payload)["fee_totals_by_currency"]["USD"] = fee["fee_amount"]
    encoded = json.dumps(payload)

    float_loaded = json.loads(encoded)
    decimal_loaded = json.loads(encoded, parse_float=Decimal)
    left = parse_openapi_export(float_loaded)
    right = parse_openapi_export(decimal_loaded)

    assert left == right
    assert left.metadata.source_sha256 == right.metadata.source_sha256
    assert left.metadata.evidence_sha256 == right.metadata.evidence_sha256
    assert left.metadata.batch_key == right.metadata.batch_key


def test_reacquisition_time_changes_only_source_hash_not_evidence_batch() -> None:
    first = _payload()
    second = copy.deepcopy(first)
    second["generated_at"] = "2026-07-01T20:02:00Z"

    left = parse_openapi_export(first)
    right = parse_openapi_export(second)

    assert left.metadata.source_sha256 != right.metadata.source_sha256
    assert left.metadata.evidence_sha256 == right.metadata.evidence_sha256
    assert left.metadata.batch_key == right.metadata.batch_key


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update(schema="future.v2"), "unsupported.*schema"),
        (lambda payload: payload.update(mode="write"), "mode must be read_only"),
        (
            lambda payload: payload.update(journal_database_written=True),
            "database stayed untouched",
        ),
        (
            lambda payload: payload["account"].update(environment="PAPER"),
            "environment is unsupported",
        ),
        (
            lambda payload: payload["account"].update(selection="first_account"),
            "selection is unsupported",
        ),
    ],
)
def test_rejects_unsupported_or_non_readonly_envelopes(mutate, message) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(MoomooOpenApiExportError, match=message):
        parse_openapi_export(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["window"].update(
                timezone="UTC"
            ),
            "timezone conflicts",
        ),
        (
            lambda payload: payload["window"].update(
                start="2026-07-01T09:00:00"
            ),
            "must include a UTC offset",
        ),
        (
            lambda payload: payload["window"].update(
                start="2026-07-01T09:00:00+00:00"
            ),
            "offsets do not match",
        ),
        (
            lambda payload: payload["window"].update(
                start="2026-07-01T17:00:00-04:00"
            ),
            "start must be earlier",
        ),
        (
            lambda payload: payload["window"].update(max_chunk_days=8),
            "max_chunk_days is unsupported",
        ),
    ],
)
def test_rejects_invalid_timezone_or_window_contract(mutate, message) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(MoomooOpenApiExportError, match=message):
        parse_openapi_export(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("records"), "missing required fields: records"),
        (
            lambda payload: payload.update(secret_account_id="not-allowed"),
            "unsupported fields: secret_account_id",
        ),
        (
            lambda payload: _records(payload)["orders"][0].pop("currency"),
            "missing required fields: currency",
        ),
        (
            lambda payload: _records(payload)["deals"][0].update(acc_id="leak"),
            "unsupported fields: acc_id",
        ),
    ],
)
def test_rejects_missing_or_unwhitelisted_fields(mutate, message) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(MoomooOpenApiExportError, match=message):
        parse_openapi_export(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: _records(payload)["orders"][0].update(order_id=""),
            "not a valid stable ID",
        ),
        (
            lambda payload: _records(payload)["orders"][0].update(order_id=1.0),
            "stable string or integer ID",
        ),
        (
            lambda payload: _records(payload)["orders"][1].update(
                order_id="o-filled"
            ),
            "duplicate stable order IDs",
        ),
        (
            lambda payload: _records(payload)["deals"].append(
                copy.deepcopy(_records(payload)["deals"][0])
            ),
            "duplicate stable deal IDs",
        ),
        (
            lambda payload: _records(payload)["fees"].append(
                copy.deepcopy(_records(payload)["fees"][0])
            ),
            "duplicate stable fee order IDs",
        ),
    ],
)
def test_rejects_missing_unstable_or_duplicate_broker_ids(mutate, message) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(MoomooOpenApiExportError, match=message):
        parse_openapi_export(payload)


@pytest.mark.parametrize("record_kind", ["deals", "fees"])
def test_rejects_orphan_fill_and_fee_references(record_kind: str) -> None:
    payload = _payload()
    _records(payload)[record_kind][0]["order_id"] = "not-exported"

    with pytest.raises(MoomooOpenApiExportError, match="does not reference"):
        parse_openapi_export(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: _records(payload)["fees"][0].update(
                fee_amount=1.26
            ),
            "does not equal.*component total",
        ),
        (
            lambda payload: _records(payload)["fees"][0]["fee_details"].append(
                ["Commission", 0]
            ),
            "duplicate component names",
        ),
        (
            lambda payload: _records(payload)["fees"][0].update(
                fee_amount=float("nan")
            ),
            "finite decimal number",
        ),
        (
            lambda payload: _records(payload)["deals"][0].update(qty=0),
            "must be greater than zero",
        ),
    ],
)
def test_rejects_invalid_financial_values(mutate, message) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(MoomooOpenApiExportError, match=message):
        parse_openapi_export(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: _summary(payload)["counts"].update(orders=99),
        lambda payload: _summary(payload).update(analysis_ready=False),
        lambda payload: _summary(payload)["reconciliation"].update(
            filled_quantity_mismatches=1
        ),
        lambda payload: _summary(payload)["activity_sides"].update(buy=2),
        lambda payload: _summary(payload)["fee_totals_by_currency"].update(
            USD=99
        ),
    ],
)
def test_rejects_tampered_summary_facts(mutate) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(MoomooOpenApiExportError, match="summary"):
        parse_openapi_export(payload)


def test_structurally_valid_missing_fill_stays_parseable_but_blocked() -> None:
    payload = _payload()
    _records(payload)["deals"] = []
    summary = _summary(payload)
    summary["analysis_ready"] = False
    summary["reconciliation_status"] = "failed"
    summary["warnings"] = ["filled_orders_without_fills=1"]
    summary["counts"]["fills"] = 0
    summary["activity_time_range"] = {
        "first": "2026-07-01 09:30:00",
        "last": "2026-07-01 09:30:00",
    }
    summary["reconciliation"]["filled_orders_without_fills"] = 1

    result = parse_openapi_export(payload)

    assert result.metadata.analysis_ready is False
    assert result.metadata.analysis_level == "blocked"
    assert result.metadata.reconciliation.filled_orders_without_fills == 1
    assert result.metadata.warnings == ("filled_orders_without_fills=1",)


def test_structurally_valid_missing_fee_stays_parseable_but_blocked() -> None:
    payload = _payload()
    _records(payload)["fees"] = []
    summary = _summary(payload)
    summary["analysis_ready"] = False
    summary["reconciliation_status"] = "failed"
    summary["warnings"] = ["filled_orders_without_fees=1"]
    summary["counts"]["fees"] = 0
    summary["fee_totals_by_currency"] = {}
    summary["reconciliation"]["filled_orders_without_fees"] = 1

    result = parse_openapi_export(payload)

    assert result.metadata.analysis_ready is False
    assert result.fees == ()
    assert result.metadata.reconciliation.filled_orders_without_fees == 1


def test_complete_live_window_without_activity_is_exact_coverage_evidence() -> None:
    payload = _payload()
    payload["account"]["binding"] = "d" * 64
    records = _records(payload)
    records["orders"] = []
    records["deals"] = []
    records["fees"] = []
    summary = _summary(payload)
    summary.update(
        {
            "retrieval_complete": True,
            "coverage_complete": True,
            "has_activity": False,
            "analysis_ready": True,
            "reconciliation_status": "passed",
            "warnings": ["no_filled_orders_in_window"],
            "activity_sides": {"buy": 0, "sell": 0, "other": 0},
            "activity_time_range": {"first": None, "last": None},
            "fee_totals_by_currency": {},
            "fee_batches": 0,
        }
    )
    summary["counts"] = {
        "orders": 0,
        "filled_orders": 0,
        "fills": 0,
        "fees": 0,
        "unique_instruments": 0,
        "option_activity_rows": 0,
    }

    result = parse_openapi_export(payload)

    assert result.metadata.analysis_ready is True
    assert result.metadata.retrieval_complete is True
    assert result.metadata.coverage_complete is True
    assert result.metadata.has_activity is False
    assert result.metadata.account_binding == "d" * 64


def test_simulate_export_has_typed_not_applicable_reconciliation() -> None:
    payload = _payload()
    payload["account"]["environment"] = "SIMULATE"
    records = _records(payload)
    records["deals"] = []
    records["fees"] = []
    summary = _summary(payload)
    summary["analysis_ready"] = False
    summary["reconciliation_status"] = "not_applicable"
    summary["warnings"] = [
        "simulate_history_has_no_live_fill_or_fee_reconciliation"
    ]
    summary["environment"] = "SIMULATE"
    summary["counts"]["fills"] = 0
    summary["counts"]["fees"] = 0
    summary["activity_time_range"] = {
        "first": "2026-07-01 09:30:00",
        "last": "2026-07-01 09:30:00",
    }
    summary["fee_totals_by_currency"] = {}
    summary["reconciliation"] = {
        name: None for name in summary["reconciliation"]
    }
    summary["fee_batches"] = 0

    result = parse_openapi_export(payload)

    assert result.metadata.analysis_level == "blocked"
    assert result.fills == ()
    assert result.fees == ()
    assert all(
        value is None
        for value in result.metadata.reconciliation.as_dict().values()
    )


def test_simulate_export_rejects_live_fill_evidence() -> None:
    payload = _payload()
    payload["account"]["environment"] = "SIMULATE"

    with pytest.raises(MoomooOpenApiExportError, match="SIMULATE exports"):
        parse_openapi_export(payload)
