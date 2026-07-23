# -*- coding: utf-8 -*-
"""Tests for loss-aware Moomoo statement parsing and reconciliation."""
from __future__ import annotations

import copy
import csv
import io
from decimal import Decimal
from typing import Any, Callable

import pytest

from src.journal.brokers.moomoo_statement import (
    MoomooStatementError,
    parse_statement,
    reconcile_statement_with_readonly_export,
)


HEADERS = (
    "Side",
    "Symbol",
    "Name",
    "Order Price",
    "Order Qty",
    "Order Amount",
    "Status",
    "Filled@Avg Price",
    "Order Time",
    "Order Type",
    "Time-in-Force",
    "Allow Pre-Market",
    "Session",
    "Trigger price",
    "Position Opening",
    "Markets",
    "Currency",
    "Order Source",
    "Fill Qty",
    "Fill Price",
    "Fill Amount",
    "Fill Time",
    "Markets",
    "Currency",
    "Counterparty",
    "Remarks",
    "Commission",
    "Platform Fees",
    "Trading Activity Fees",
    "Options Regulatory Fees",
    "OCC Fees",
    "Contract Fees",
    "SEC Fees",
    "Consolidated Audit Trail Fees",
    "Settlement Fees",
    "Total",
)

assert len(HEADERS) == 36


def _blank_row() -> list[str]:
    return [""] * len(HEADERS)


def _set(
    row: list[str],
    name: str,
    value: Any,
    *,
    occurrence: int = 0,
) -> None:
    indexes = [index for index, header in enumerate(HEADERS) if header == name]
    row[indexes[occurrence]] = str(value)


def _filled_detail_order_row() -> list[str]:
    row = _blank_row()
    values = {
        "Side": "Buy",
        "Symbol": "US.EXAMPLE260717C00200000",
        "Name": "Deidentified option",
        "Order Price": "2.55",
        "Order Qty": "2",
        "Order Amount": "510",
        "Status": "Filled",
        "Filled@Avg Price": "2@2.55",
        "Order Time": "Jul 20, 2026 09:30:00",
        "Order Type": "Limit",
        "Time-in-Force": "Day",
        "Session": "Regular",
        "Markets": "ORDER_MARKET",
        "Currency": "ORDER_CCY",
        "Order Source": "Synthetic fixture",
        "Commission": "0.20",
        "Platform Fees": "0.40",
        "Trading Activity Fees": "0.01",
        "Options Regulatory Fees": "0.03",
        "OCC Fees": "0.02",
        "Contract Fees": "0.05",
        "SEC Fees": "0",
        "Consolidated Audit Trail Fees": "0.0004",
        "Settlement Fees": "0.01",
        "Total": "0.7204",
    }
    for name, value in values.items():
        _set(row, name, value)
    _set_fill(row)
    return row


def _set_fill(row: list[str]) -> None:
    values = {
        "Fill Qty": "1",
        "Fill Price": "2.55",
        "Fill Amount": "255",
        "Fill Time": "Jul 20, 2026 09:30:01",
        "Counterparty": "DEIDENTIFIED",
        "Remarks": "same execution evidence",
    }
    for name, value in values.items():
        _set(row, name, value)
    _set(row, "Markets", "FILL_MARKET", occurrence=1)
    _set(row, "Currency", "FILL_CCY", occurrence=1)


def _identical_fill_continuation_row() -> list[str]:
    row = _blank_row()
    _set_fill(row)
    return row


def _aggregate_only_order_row() -> list[str]:
    row = _blank_row()
    values = {
        "Side": "Sell",
        "Symbol": "US.EXAMPLE260717P00190000",
        "Name": "Older deidentified option",
        "Order Price": "3.10",
        "Order Qty": "1",
        "Order Amount": "310",
        "Status": "Filled",
        "Filled@Avg Price": "1@3.10",
        "Order Time": "Jul 20, 2026 10:30:00",
        "Order Type": "Limit",
        "Time-in-Force": "Day",
        "Session": "Regular",
        "Markets": "ORDER_MARKET",
        "Currency": "ORDER_CCY",
        "Commission": "0.10",
        "Platform Fees": "0.20",
        "Consolidated Audit Trail Fees": "0.0002",
        "Total": "0.3002",
    }
    for name, value in values.items():
        _set(row, name, value)
    return row


def _csv_bytes(*rows: list[str], headers: tuple[str, ...] = HEADERS) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _statement():
    return parse_statement(
        _csv_bytes(
            _filled_detail_order_row(),
            _identical_fill_continuation_row(),
            _aggregate_only_order_row(),
        )
    )


def _fee_details() -> list[tuple[str, float]]:
    return [
        ("Commission", 0.20),
        ("Platform Fees", 0.40),
        ("Trading Activity Fees", 0.01),
        ("Options Regulatory Fees", 0.03),
        ("OCC Fees", 0.02),
        ("Contract Fees", 0.05),
        ("SEC Fees", 0.0),
        ("Consolidated Audit Trail Fees", 0.0004),
        ("Settlement Fees", 0.01),
    ]


def _readonly_export() -> dict[str, Any]:
    return {
        "schema": "dsa.moomoo.readonly-export.v1",
        "mode": "read_only",
        "journal_database_written": False,
        "window": {
            "start": "2026-07-20T09:00:00-04:00",
            "end": "2026-07-20T09:59:59-04:00",
            "timezone": "America/New_York",
        },
        "summary": {"analysis_ready": True},
        "records": {
            "orders": [
                {
                    "order_id": "synthetic-order-1",
                    "code": "US.EXAMPLE260717C00200000",
                    "trd_side": "BUY",
                    "qty": 2,
                    "create_time": "2026-07-20 09:30:00",
                    "order_status": "FILLED_ALL",
                    "dealt_qty": 2,
                    "dealt_avg_price": 2.55,
                }
            ],
            "deals": [
                {
                    "deal_id": "synthetic-deal-1",
                    "order_id": "synthetic-order-1",
                    "qty": 1,
                    "price": 2.55,
                },
                {
                    "deal_id": "synthetic-deal-2",
                    "order_id": "synthetic-order-1",
                    "qty": 1,
                    "price": 2.55,
                },
            ],
            "fees": [
                {
                    "order_id": "synthetic-order-1",
                    "fee_amount": 0.7204,
                    "fee_details": _fee_details(),
                }
            ],
        },
    }


def test_parse_preserves_duplicate_headers_and_identical_fill_rows():
    result = _statement()

    assert result.rows_total == 3
    assert len(result.orders) == 2
    detailed, aggregate = result.orders

    assert detailed.market == "ORDER_MARKET"
    assert detailed.currency == "ORDER_CCY"
    assert len(detailed.fills) == 2
    assert [fill.market for fill in detailed.fills] == [
        "FILL_MARKET",
        "FILL_MARKET",
    ]
    assert [fill.currency for fill in detailed.fills] == [
        "FILL_CCY",
        "FILL_CCY",
    ]
    assert [fill.source_row for fill in detailed.fills] == [2, 3]
    assert detailed.detailed_filled_quantity == Decimal("2")
    assert detailed.detailed_average_price == Decimal("2.55")
    assert detailed.evidence_level == "fill_detail"
    assert detailed.evidence_warnings == ()

    assert dict(detailed.fee_components)[
        "Consolidated Audit Trail Fees"
    ] == Decimal("0.0004")
    assert detailed.total_fee == Decimal("0.7204")

    assert aggregate.evidence_level == "aggregate_only"
    assert aggregate.summary_filled_quantity == Decimal("1")
    assert aggregate.summary_average_price == Decimal("3.10")
    assert aggregate.fills == ()

    assert result.summary() == {
        "parser_version": "moomoo-statement-v2",
        "rows_total": 3,
        "orders_total": 2,
        "status_counts": {"FILLED": 2},
        "filled_orders": 2,
        "detail_backed_filled_orders": 1,
        "aggregate_only_filled_orders": 1,
        "inconsistent_filled_orders": 0,
        "fill_records": 2,
        "orphan_fill_rows": 0,
        "filled_fee_total": "1.0206",
        "detail_backed_fee_total": "0.7204",
        "aggregate_only_fee_total": "0.3002",
        "order_time_range": {
            "first": "2026-07-20T09:30:00-04:00",
            "last": "2026-07-20T10:30:00-04:00",
        },
        "fill_detail_time_range": {
            "first": "2026-07-20T09:30:01-04:00",
            "last": "2026-07-20T09:30:01-04:00",
        },
        "warnings": [],
    }


def test_header_only_statement_has_zero_orders():
    result = parse_statement(_csv_bytes())

    assert result.rows_total == 0
    assert result.orders == ()
    assert result.summary()["orders_total"] == 0


def test_cancelled_order_with_partial_fill_keeps_execution_evidence():
    row = _filled_detail_order_row()
    _set(row, "Status", "Cancelled")
    _set(row, "Filled@Avg Price", "1@2.55")

    result = parse_statement(_csv_bytes(row))
    order = result.orders[0]

    assert order.status == "CANCELLED"
    assert order.has_execution_evidence is True
    assert order.evidence_level == "fill_detail"
    assert len(order.fills) == 1
    assert result.summary()["filled_orders"] == 1
    assert result.summary()["filled_fee_total"] == "0.7204"


def test_cancelled_zero_summary_remains_not_filled():
    row = _filled_detail_order_row()
    _set(row, "Status", "Cancelled")
    _set(row, "Filled@Avg Price", "0@0.00")
    _set(row, "Fill Qty", "")
    _set(row, "Fill Price", "")
    _set(row, "Fill Amount", "")
    _set(row, "Fill Time", "")

    result = parse_statement(_csv_bytes(row))
    order = result.orders[0]

    assert order.has_execution_evidence is False
    assert order.evidence_level == "not_filled"
    assert result.summary()["filled_orders"] == 0
    assert result.summary()["aggregate_only_filled_orders"] == 0


def test_missing_required_header_is_rejected():
    headers = tuple(header for header in HEADERS if header != "Total")

    with pytest.raises(MoomooStatementError, match=r"required headers: Total"):
        parse_statement(_csv_bytes(headers=headers))


def test_matching_readonly_export_is_analysis_ready():
    result = reconcile_statement_with_readonly_export(
        _statement(), _readonly_export()
    )

    assert result.analysis_ready is True
    assert result.statement_orders == 1
    assert result.api_orders == 1
    assert result.matched_orders == 1
    assert result.matched_filled_orders == 1
    assert result.statement_only_orders == 0
    assert result.api_only_orders == 0
    assert result.status_mismatches == 0
    assert result.fill_count_mismatches == 0
    assert result.filled_quantity_mismatches == 0
    assert result.fill_vwap_mismatches == 0
    assert result.fee_total_mismatches == 0
    assert result.fee_component_mismatches == 0
    assert result.ambiguous_identity_keys == 0
    assert result.statement_fee_total == Decimal("0.7204")
    assert result.api_fee_total == Decimal("0.7204")
    assert result.warnings == ()


def test_statement_internal_inconsistency_blocks_cross_source_ready():
    main = _filled_detail_order_row()
    _set(main, "Filled@Avg Price", "3@2.55")
    statement = parse_statement(
        _csv_bytes(main, _identical_fill_continuation_row())
    )

    result = reconcile_statement_with_readonly_export(
        statement, _readonly_export()
    )

    assert statement.orders[0].evidence_level == "inconsistent"
    assert result.analysis_ready is False
    assert "statement_evidence_not_analysis_ready" in result.warnings


def test_unlinked_api_fee_and_aggregate_fee_difference_block_ready():
    payload = copy.deepcopy(_readonly_export())
    payload["records"]["fees"].append(
        {
            "order_id": "orphan-order",
            "fee_amount": 99,
            "fee_details": [],
        }
    )

    result = reconcile_statement_with_readonly_export(_statement(), payload)

    assert result.analysis_ready is False
    assert result.statement_fee_total == Decimal("0.7204")
    assert result.api_fee_total == Decimal("99.7204")
    assert "api_export_record_links_invalid" in result.warnings
    assert "aggregate_fee_total_mismatch" in result.warnings


def test_partial_cancel_cannot_be_skipped_when_api_reports_zero_dealt_qty():
    row = _filled_detail_order_row()
    _set(row, "Status", "Cancelled")
    _set(row, "Filled@Avg Price", "1@2.55")
    statement = parse_statement(_csv_bytes(row))
    payload = copy.deepcopy(_readonly_export())
    payload["records"]["orders"][0]["order_status"] = "CANCELLED_ALL"
    payload["records"]["orders"][0]["dealt_qty"] = 0
    payload["records"]["deals"] = []

    result = reconcile_statement_with_readonly_export(statement, payload)

    assert statement.orders[0].has_execution_evidence is True
    assert result.analysis_ready is False
    assert result.fill_count_mismatches == 1
    assert result.filled_quantity_mismatches == 1


def _change_status(payload: dict[str, Any]) -> None:
    payload["records"]["orders"][0]["order_status"] = "CANCELLED_ALL"


def _remove_fill(payload: dict[str, Any]) -> None:
    payload["records"]["deals"].pop()


def _change_filled_quantity(payload: dict[str, Any]) -> None:
    payload["records"]["deals"][0]["qty"] = 0.5


def _change_fill_price(payload: dict[str, Any]) -> None:
    payload["records"]["deals"][0]["price"] = 2.65


def _change_fee_total(payload: dict[str, Any]) -> None:
    payload["records"]["fees"][0]["fee_amount"] = 0.8204


def _change_fee_component(payload: dict[str, Any]) -> None:
    payload["records"]["fees"][0]["fee_details"][7] = (
        "Consolidated Audit Trail Fees",
        0.1004,
    )


@pytest.mark.parametrize(
    ("mutate", "mismatch_attribute"),
    [
        (_change_status, "status_mismatches"),
        (_remove_fill, "fill_count_mismatches"),
        (_change_filled_quantity, "filled_quantity_mismatches"),
        (_change_fill_price, "fill_vwap_mismatches"),
        (_change_fee_total, "fee_total_mismatches"),
        (_change_fee_component, "fee_component_mismatches"),
    ],
    ids=(
        "status",
        "fill-count",
        "filled-quantity",
        "fill-vwap",
        "fee-total",
        "fee-component",
    ),
)
def test_any_cross_source_difference_blocks_analysis(
    mutate: Callable[[dict[str, Any]], None], mismatch_attribute: str
):
    payload = copy.deepcopy(_readonly_export())
    mutate(payload)

    result = reconcile_statement_with_readonly_export(_statement(), payload)

    assert result.analysis_ready is False
    assert getattr(result, mismatch_attribute) == 1
    assert "cross_source_reconciliation_failed" in result.warnings
