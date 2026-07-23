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
        "rejected": 0,
    }
    encoded = json.dumps(public_summary)
    assert "o-filled" not in encoded
    assert "d-filled" not in encoded


def test_broker_four_digit_fractional_second_is_normalized() -> None:
    payload = _payload()
    _records(payload)["orders"][0]["updated_time"] = (
        "2026-07-01 09:30:00.1000"
    )

    result = parse_openapi_export(payload)

    assert result.orders[0].source_updated_at.microsecond == 100000


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
