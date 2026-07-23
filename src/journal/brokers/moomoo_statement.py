# -*- coding: utf-8 -*-
"""Loss-aware parser and reconciliation for Moomoo history CSV exports.

Moomoo's history export contains two different evidence levels:

* recent filled orders include one or more fill-detail rows;
* older filled orders may retain only ``Filled@Avg Price`` (``qty@price``).

The legacy :mod:`src.journal.brokers.moomoo_us` parser intentionally emits
only orders backed by fill details because the FIFO matcher needs timestamps.
This module preserves *all* order evidence and makes the difference explicit.
It also reconciles the CSV against a read-only OpenAPI export without writing
to the Journal database.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

__all__ = [
    "CSV_PARSER_VERSION",
    "MoomooStatementError",
    "StatementFill",
    "StatementOrder",
    "StatementParseResult",
    "StatementApiMatch",
    "StatementReconciliation",
    "parse_statement",
    "reconcile_statement_with_readonly_export",
]


CSV_PARSER_VERSION = "moomoo-statement-v2"
READONLY_EXPORT_SCHEMA = "dsa.moomoo.readonly-export.v1"
ET = ZoneInfo("America/New_York")

_REQUIRED_HEADERS = {
    "Side",
    "Symbol",
    "Order Qty",
    "Status",
    "Filled@Avg Price",
    "Order Time",
    "Fill Qty",
    "Fill Price",
    "Fill Time",
    "Total",
}

_FEE_COLUMNS = (
    "Commission",
    "Platform Fees",
    "SEC Fees",
    "Trading Activity Fees",
    "Options Regulatory Fees",
    "OCC Fees",
    "Contract Fees",
    "Consolidated Audit Trail Fees",
    "Settlement Fees",
)

_TIME_FORMATS = (
    "%b %d, %Y %H:%M:%S",
    "%b %d, %Y %H:%M",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)


class MoomooStatementError(ValueError):
    """Raised when a history file cannot be interpreted safely."""


@dataclass(frozen=True)
class StatementFill:
    """One fill-detail record from the CSV."""

    source_row: int
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    amount: Optional[Decimal] = None
    market: str = ""
    currency: str = ""
    counterparty: str = ""
    remarks: str = ""


@dataclass(frozen=True)
class StatementOrder:
    """One CSV order plus its available fill evidence."""

    source_row: int
    derived_order_id: str
    symbol: str
    name: str
    side: str
    status: str
    order_quantity: Decimal
    order_price: Optional[Decimal]
    order_price_text: str
    order_amount: Optional[Decimal]
    order_time: datetime
    order_type: str
    time_in_force: str
    session: str
    market: str
    currency: str
    summary_filled_quantity: Optional[Decimal]
    summary_average_price: Optional[Decimal]
    fills: tuple[StatementFill, ...]
    fee_components: tuple[tuple[str, Decimal], ...]
    total_fee: Decimal
    evidence_level: str
    evidence_warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"

    @property
    def has_execution_evidence(self) -> bool:
        """Return true for full or partial executions, regardless of terminal status."""
        return bool(self.fills) or (
            self.summary_filled_quantity is not None
            and self.summary_filled_quantity > 0
            and self.summary_average_price is not None
        )

    @property
    def detailed_filled_quantity(self) -> Decimal:
        return sum((fill.quantity for fill in self.fills), Decimal("0"))

    @property
    def detailed_average_price(self) -> Optional[Decimal]:
        quantity = self.detailed_filled_quantity
        if quantity <= 0:
            return None
        value = sum(
            (fill.quantity * fill.price for fill in self.fills), Decimal("0")
        )
        return value / quantity


@dataclass(frozen=True)
class StatementParseResult:
    """Parsed statement plus a compact, non-row-level quality summary."""

    source_sha256: str
    parser_version: str
    rows_total: int
    headers: tuple[str, ...]
    orders: tuple[StatementOrder, ...]
    orphan_fill_rows: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        # A cancelled/failed terminal order can still contain partial fills.
        # Treat execution evidence, not only terminal status, as the economic
        # population while retaining status_counts for the broker state.
        filled = [
            order
            for order in self.orders
            if order.is_filled or order.has_execution_evidence
        ]
        detail_backed = [
            order for order in filled if order.evidence_level == "fill_detail"
        ]
        aggregate_only = [
            order for order in filled if order.evidence_level == "aggregate_only"
        ]
        inconsistent = [
            order
            for order in self.orders
            if order.evidence_level == "inconsistent"
        ]
        order_times = [order.order_time for order in self.orders]
        fill_times = [
            fill.filled_at for order in self.orders for fill in order.fills
        ]
        status_counts = Counter(order.status for order in self.orders)
        return {
            "parser_version": self.parser_version,
            "rows_total": self.rows_total,
            "orders_total": len(self.orders),
            "status_counts": dict(sorted(status_counts.items())),
            "filled_orders": len(filled),
            "detail_backed_filled_orders": len(detail_backed),
            "aggregate_only_filled_orders": len(aggregate_only),
            "inconsistent_filled_orders": len(inconsistent),
            "fill_records": sum(len(order.fills) for order in self.orders),
            "orphan_fill_rows": self.orphan_fill_rows,
            "filled_fee_total": _decimal_text(
                sum((order.total_fee for order in filled), Decimal("0"))
            ),
            "detail_backed_fee_total": _decimal_text(
                sum((order.total_fee for order in detail_backed), Decimal("0"))
            ),
            "aggregate_only_fee_total": _decimal_text(
                sum((order.total_fee for order in aggregate_only), Decimal("0"))
            ),
            "order_time_range": _time_range(order_times),
            "fill_detail_time_range": _time_range(fill_times),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, repr=False)
class StatementApiMatch:
    """Internal order identity bridge; excluded from public summaries."""

    statement_order_id: str
    broker_order_id: str


@dataclass(frozen=True)
class StatementReconciliation:
    """Cross-source reconciliation result for one overlapping window."""

    analysis_ready: bool
    window_start: datetime
    window_end: datetime
    statement_orders: int
    api_orders: int
    matched_orders: int
    statement_only_orders: int
    api_only_orders: int
    matched_filled_orders: int
    status_mismatches: int
    fill_count_mismatches: int
    filled_quantity_mismatches: int
    fill_vwap_mismatches: int
    fee_total_mismatches: int
    fee_component_mismatches: int
    ambiguous_identity_keys: int
    statement_fee_total: Decimal
    api_fee_total: Decimal
    matches: tuple[StatementApiMatch, ...] = field(default_factory=tuple, repr=False)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        return {
            "analysis_ready": self.analysis_ready,
            "window": {
                "start": self.window_start.isoformat(),
                "end": self.window_end.isoformat(),
            },
            "orders": {
                "statement": self.statement_orders,
                "api": self.api_orders,
                "matched": self.matched_orders,
                "statement_only": self.statement_only_orders,
                "api_only": self.api_only_orders,
                "ambiguous_identity_keys": self.ambiguous_identity_keys,
            },
            "matched_filled_orders": self.matched_filled_orders,
            "mismatches": {
                "status": self.status_mismatches,
                "fill_count": self.fill_count_mismatches,
                "filled_quantity": self.filled_quantity_mismatches,
                "fill_vwap": self.fill_vwap_mismatches,
                "fee_total": self.fee_total_mismatches,
                "fee_component": self.fee_component_mismatches,
            },
            "fees": {
                "statement": _reconciled_decimal_text(self.statement_fee_total),
                "api": _reconciled_decimal_text(self.api_fee_total),
                "difference": _reconciled_decimal_text(
                    self.statement_fee_total - self.api_fee_total
                ),
            },
            "warnings": list(self.warnings),
        }


class _HeaderMap:
    def __init__(self, headers: Iterable[str]):
        positions: dict[str, list[int]] = defaultdict(list)
        for index, name in enumerate(headers):
            positions[name.strip()].append(index)
        self._positions = dict(positions)

    @property
    def names(self) -> set[str]:
        return set(self._positions)

    def get(self, row: list[str], name: str, occurrence: int = 0) -> str:
        indexes = self._positions.get(name, [])
        if occurrence >= len(indexes):
            return ""
        index = indexes[occurrence]
        if index >= len(row):
            return ""
        return row[index].strip()


def _parse_decimal(value: Any, *, allow_missing: bool = True) -> Optional[Decimal]:
    text = str(value or "").strip().replace(",", "").replace("$", "")
    if text.lower() in {
        "",
        "--",
        "n/a",
        "na",
        "market",
        "market price",
        "mkt",
    }:
        if allow_missing:
            return None
        raise MoomooStatementError("required numeric value is missing")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise MoomooStatementError(f"invalid numeric value: {value!r}") from exc
    if not number.is_finite():
        raise MoomooStatementError(f"non-finite numeric value: {value!r}")
    return number


def _decimal_or_zero(value: Any) -> Decimal:
    parsed = _parse_decimal(value)
    return parsed if parsed is not None else Decimal("0")


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _reconciled_decimal_text(value: Decimal) -> str:
    """Suppress sub-micro float artefacts introduced by SDK JSON numbers."""
    return _decimal_text(value.quantize(Decimal("0.000001")))


def _parse_time(value: str, *, timezone: ZoneInfo = ET) -> datetime:
    text = value.strip()
    for suffix in (" ET", " EDT", " EST"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone)
        except ValueError:
            continue
    raise MoomooStatementError(f"invalid timestamp: {value!r}")


def _normalise_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(".", 1)[1] if text.startswith("US.") else text


def _normalise_side(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    aliases = {
        "SHORT_SELL": "SELL_SHORT",
        "BUY_TO_COVER": "BUY_BACK",
    }
    return aliases.get(text, text)


def _normalise_statement_status(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    return {
        "FILLED": "FILLED",
        "EXECUTED": "FILLED",
        "COMPLETE": "FILLED",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
        "FAILED": "FAILED",
    }.get(text, text or "UNKNOWN")


def _normalise_api_status(value: Any) -> str:
    text = str(value or "").strip().upper()
    return {
        "FILLED_ALL": "FILLED",
        "FILLED_PART": "PARTIALLY_FILLED",
        "CANCELLED_ALL": "CANCELLED",
        "CANCELLED_PART": "CANCELLED",
    }.get(text, text or "UNKNOWN")


def _parse_fill_summary(value: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
    text = value.strip()
    if not text:
        return None, None
    if "@" not in text:
        # Older exports displayed only the average price.  Detail-backed rows
        # remain exact because quantity comes from their fill records.
        return None, _parse_decimal(text, allow_missing=False)
    quantity_text, price_text = text.rsplit("@", 1)
    return (
        _parse_decimal(quantity_text, allow_missing=False),
        _parse_decimal(price_text, allow_missing=False),
    )


def _matches_display_precision(actual: Decimal, displayed: Decimal) -> bool:
    """Compare a computed value with a broker value rounded for CSV display."""
    exponent = displayed.as_tuple().exponent
    quantum = Decimal("1").scaleb(exponent)
    tolerance = max(Decimal("0.000000001"), abs(quantum) / 2)
    return abs(actual - displayed) <= tolerance


def _derived_order_id(
    *,
    symbol: str,
    side: str,
    quantity: Decimal,
    order_time: datetime,
) -> str:
    canonical = "|".join(
        (
            symbol,
            side,
            _decimal_text(quantity),
            order_time.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"),
        )
    )
    return "moomoo_csv_" + hashlib.sha256(canonical.encode()).hexdigest()[:20]


def _time_range(values: list[datetime]) -> dict[str, Optional[str]]:
    if not values:
        return {"first": None, "last": None}
    return {"first": min(values).isoformat(), "last": max(values).isoformat()}


def _build_order(
    *,
    source_row: int,
    row: list[str],
    headers: _HeaderMap,
    fills: list[StatementFill],
) -> StatementOrder:
    symbol = _normalise_symbol(headers.get(row, "Symbol"))
    side = _normalise_side(headers.get(row, "Side"))
    status = _normalise_statement_status(headers.get(row, "Status"))
    order_quantity = _parse_decimal(
        headers.get(row, "Order Qty"), allow_missing=False
    )
    assert order_quantity is not None
    order_time = _parse_time(headers.get(row, "Order Time"))
    summary_quantity, summary_price = _parse_fill_summary(
        headers.get(row, "Filled@Avg Price")
    )
    fee_components = tuple(
        (name, _decimal_or_zero(headers.get(row, name))) for name in _FEE_COLUMNS
    )
    evidence_warnings: list[str] = []
    evidence_level = "not_filled"
    if fills:
        detail_quantity = sum(
            (fill.quantity for fill in fills), Decimal("0")
        )
        detail_value = sum(
            (fill.quantity * fill.price for fill in fills), Decimal("0")
        )
        detail_average = detail_value / detail_quantity
        if summary_price is None:
            evidence_warnings.append("missing_filled_summary")
        elif summary_quantity is not None and detail_quantity != summary_quantity:
            evidence_warnings.append("filled_quantity_mismatch")
        elif not _matches_display_precision(detail_average, summary_price):
            evidence_warnings.append("filled_average_price_mismatch")
        evidence_level = (
            "inconsistent" if evidence_warnings else "fill_detail"
        )
    elif summary_price is not None:
        if summary_quantity is not None and summary_quantity <= 0:
            if status == "FILLED":
                evidence_warnings.append("non_positive_filled_summary_quantity")
                evidence_level = "inconsistent"
            else:
                evidence_level = "not_filled"
        elif summary_quantity is None:
            if status == "FILLED":
                summary_quantity = order_quantity
                evidence_warnings.append("summary_quantity_inferred_from_order")
                evidence_level = "aggregate_only"
            else:
                evidence_warnings.append("partial_fill_quantity_missing")
                evidence_level = "inconsistent"
        else:
            evidence_level = "aggregate_only"
    elif status == "FILLED":
        evidence_level = "inconsistent"
        evidence_warnings.append("missing_fill_evidence")

    return StatementOrder(
        source_row=source_row,
        derived_order_id=_derived_order_id(
            symbol=symbol,
            side=side,
            quantity=order_quantity,
            order_time=order_time,
        ),
        symbol=symbol,
        name=headers.get(row, "Name"),
        side=side,
        status=status,
        order_quantity=order_quantity,
        order_price=_parse_decimal(headers.get(row, "Order Price")),
        order_price_text=headers.get(row, "Order Price"),
        order_amount=_parse_decimal(headers.get(row, "Order Amount")),
        order_time=order_time,
        order_type=headers.get(row, "Order Type"),
        time_in_force=headers.get(row, "Time-in-Force"),
        session=headers.get(row, "Session"),
        market=headers.get(row, "Markets", 0),
        currency=headers.get(row, "Currency", 0) or "USD",
        summary_filled_quantity=summary_quantity,
        summary_average_price=summary_price,
        fills=tuple(fills),
        fee_components=fee_components,
        total_fee=_decimal_or_zero(headers.get(row, "Total")),
        evidence_level=evidence_level,
        evidence_warnings=tuple(evidence_warnings),
    )


def parse_statement(content: bytes) -> StatementParseResult:
    """Parse a complete Moomoo history export without discarding old orders."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MoomooStatementError("CSV must be UTF-8 encoded") from exc

    reader = csv.reader(io.StringIO(text))
    try:
        raw_headers = next(reader)
    except StopIteration as exc:
        raise MoomooStatementError("CSV is empty") from exc
    headers = tuple(name.strip() for name in raw_headers)
    header_map = _HeaderMap(headers)
    missing = sorted(_REQUIRED_HEADERS - header_map.names)
    if missing:
        raise MoomooStatementError(
            "CSV is missing required headers: " + ", ".join(missing)
        )

    orders: list[StatementOrder] = []
    current_row: Optional[list[str]] = None
    current_source_row: Optional[int] = None
    current_fills: list[StatementFill] = []
    orphan_fill_rows = 0
    rows_total = 0

    def flush_current() -> None:
        nonlocal current_row, current_source_row, current_fills
        if current_row is None or current_source_row is None:
            return
        orders.append(
            _build_order(
                source_row=current_source_row,
                row=current_row,
                headers=header_map,
                fills=current_fills,
            )
        )
        current_row = None
        current_source_row = None
        current_fills = []

    for source_row, row in enumerate(reader, start=2):
        rows_total += 1
        if len(row) != len(headers):
            raise MoomooStatementError(
                f"row {source_row} has {len(row)} columns; expected {len(headers)}"
            )
        is_main = bool(
            header_map.get(row, "Side") and header_map.get(row, "Symbol")
        )
        if is_main:
            flush_current()
            current_row = row
            current_source_row = source_row

        fill_quantity_text = header_map.get(row, "Fill Qty")
        if not fill_quantity_text:
            continue
        if current_row is None:
            orphan_fill_rows += 1
            continue
        quantity = _parse_decimal(fill_quantity_text, allow_missing=False)
        price = _parse_decimal(
            header_map.get(row, "Fill Price"), allow_missing=False
        )
        assert quantity is not None and price is not None
        if quantity <= 0:
            raise MoomooStatementError(
                f"row {source_row} has a non-positive fill quantity"
            )
        current_fills.append(
            StatementFill(
                source_row=source_row,
                quantity=quantity,
                price=price,
                amount=_parse_decimal(header_map.get(row, "Fill Amount")),
                filled_at=_parse_time(header_map.get(row, "Fill Time")),
                market=header_map.get(row, "Markets", 1),
                currency=header_map.get(row, "Currency", 1),
                counterparty=header_map.get(row, "Counterparty"),
                remarks=header_map.get(row, "Remarks"),
            )
        )

    flush_current()
    derived_ids = Counter(order.derived_order_id for order in orders)
    collisions = sum(count - 1 for count in derived_ids.values() if count > 1)
    warnings: list[str] = []
    if collisions:
        warnings.append(f"derived_order_id_collisions={collisions}")
    if orphan_fill_rows:
        warnings.append(f"orphan_fill_rows={orphan_fill_rows}")

    return StatementParseResult(
        source_sha256=hashlib.sha256(content).hexdigest(),
        parser_version=CSV_PARSER_VERSION,
        rows_total=rows_total,
        headers=headers,
        orders=tuple(orders),
        orphan_fill_rows=orphan_fill_rows,
        warnings=tuple(warnings),
    )


def _api_decimal(value: Any) -> Decimal:
    parsed = _parse_decimal(value)
    return parsed if parsed is not None else Decimal("0")


def _api_time(value: Any, timezone: ZoneInfo) -> datetime:
    text = str(value or "").strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(
                microsecond=0, tzinfo=timezone
            )
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MoomooStatementError(f"invalid API timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    else:
        parsed = parsed.astimezone(timezone)
    return parsed.replace(microsecond=0)


def _api_order_key(row: Mapping[str, Any], timezone: ZoneInfo) -> tuple[Any, ...]:
    return (
        _normalise_symbol(row.get("code")),
        _normalise_side(row.get("trd_side")),
        _api_decimal(row.get("qty")),
        _api_time(row.get("create_time"), timezone),
    )


def _statement_order_key(order: StatementOrder) -> tuple[Any, ...]:
    return (
        order.symbol,
        order.side,
        order.order_quantity,
        order.order_time.replace(microsecond=0),
    )


def _fee_components_from_api(row: Mapping[str, Any]) -> dict[str, Decimal]:
    result = {name: Decimal("0") for name in _FEE_COLUMNS}
    details = row.get("fee_details") or []
    if not isinstance(details, list):
        return result
    for item in details:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name = str(item[0])
        if name in result:
            result[name] = _api_decimal(item[1])
    return result


def reconcile_statement_with_readonly_export(
    statement: StatementParseResult,
    payload: Mapping[str, Any],
) -> StatementReconciliation:
    """Reconcile overlapping CSV and read-only OpenAPI order evidence.

    CSV has no broker order/deal IDs, so order identity uses a four-part key:
    normalized symbol, side, requested quantity and second-level order time.
    Ambiguous keys are reported and never auto-linked.
    """
    if payload.get("schema") != READONLY_EXPORT_SCHEMA:
        raise MoomooStatementError("unsupported read-only export schema")
    if payload.get("mode") != "read_only":
        raise MoomooStatementError("export mode must be read_only")
    if payload.get("journal_database_written") is not False:
        raise MoomooStatementError(
            "export does not prove that the Journal database stayed untouched"
        )
    window = payload.get("window")
    records = payload.get("records")
    if not isinstance(window, Mapping) or not isinstance(records, Mapping):
        raise MoomooStatementError("export is missing window or records")
    try:
        timezone = ZoneInfo(str(window["timezone"]))
        window_start = datetime.fromisoformat(str(window["start"])).astimezone(
            timezone
        )
        window_end = datetime.fromisoformat(str(window["end"])).astimezone(
            timezone
        )
    except (KeyError, ValueError) as exc:
        raise MoomooStatementError("export window is invalid") from exc

    api_orders = list(records.get("orders") or [])
    api_deals = list(records.get("deals") or [])
    api_fees = list(records.get("fees") or [])
    if not all(isinstance(row, Mapping) for row in api_orders + api_deals + api_fees):
        raise MoomooStatementError("export records must be objects")

    statement_window_orders = [
        order
        for order in statement.orders
        if window_start <= order.order_time <= window_end
    ]
    statement_window_inconsistent = sum(
        order.evidence_level == "inconsistent"
        for order in statement_window_orders
    )
    statement_quality_ok = not (
        statement_window_inconsistent
        or statement.orphan_fill_rows
        or statement.warnings
    )
    statement_by_key: dict[tuple[Any, ...], list[StatementOrder]] = defaultdict(list)
    api_by_key: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for order in statement_window_orders:
        statement_by_key[_statement_order_key(order)].append(order)
    for row in api_orders:
        api_by_key[_api_order_key(row, timezone)].append(row)

    all_keys = set(statement_by_key) | set(api_by_key)
    ambiguous_keys = sum(
        1
        for key in all_keys
        if len(statement_by_key.get(key, [])) > 1
        or len(api_by_key.get(key, [])) > 1
    )
    matched_pairs: list[tuple[StatementOrder, Mapping[str, Any]]] = []
    for key in all_keys:
        statement_rows = statement_by_key.get(key, [])
        api_rows = api_by_key.get(key, [])
        if len(statement_rows) == len(api_rows) == 1:
            matched_pairs.append((statement_rows[0], api_rows[0]))

    api_deals_by_order: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in api_deals:
        api_deals_by_order[str(row.get("order_id") or "")].append(row)
    api_fee_by_order = {
        str(row.get("order_id") or ""): row for row in api_fees
    }
    api_order_ids = [str(row.get("order_id") or "") for row in api_orders]
    api_order_id_set = set(api_order_ids)
    api_deal_ids = [str(row.get("deal_id") or "") for row in api_deals]
    api_fee_order_ids = [str(row.get("order_id") or "") for row in api_fees]
    api_record_links_ok = not (
        any(not value for value in api_order_ids + api_deal_ids + api_fee_order_ids)
        or len(api_order_ids) != len(api_order_id_set)
        or len(api_deal_ids) != len(set(api_deal_ids))
        or len(api_fee_order_ids) != len(set(api_fee_order_ids))
        or any(
            str(row.get("order_id") or "") not in api_order_id_set
            for row in api_deals + api_fees
        )
    )

    status_mismatches = 0
    fill_count_mismatches = 0
    quantity_mismatches = 0
    vwap_mismatches = 0
    fee_total_mismatches = 0
    fee_component_mismatches = 0
    matched_filled_orders = 0
    matches: list[StatementApiMatch] = []
    statement_fee_total = Decimal("0")
    api_fee_total = sum(
        (_api_decimal(row.get("fee_amount")) for row in api_fees), Decimal("0")
    )

    for statement_order, api_order in matched_pairs:
        broker_order_id = str(api_order.get("order_id") or "")
        if broker_order_id:
            matches.append(
                StatementApiMatch(
                    statement_order_id=statement_order.derived_order_id,
                    broker_order_id=broker_order_id,
                )
            )
        if statement_order.status != _normalise_api_status(
            api_order.get("order_status")
        ):
            status_mismatches += 1

        deals = api_deals_by_order.get(broker_order_id, [])
        api_quantity = sum(
            (_api_decimal(row.get("qty")) for row in deals), Decimal("0")
        )
        api_filled_quantity = _api_decimal(api_order.get("dealt_qty"))
        statement_quantity = (
            statement_order.detailed_filled_quantity
            if statement_order.fills
            else statement_order.summary_filled_quantity or Decimal("0")
        )
        statement_has_execution = statement_quantity > 0
        api_has_execution = api_filled_quantity > 0 or api_quantity > 0
        if not statement_has_execution and not api_has_execution:
            continue

        matched_filled_orders += 1
        if statement_has_execution:
            statement_fee_total += statement_order.total_fee
        if len(statement_order.fills) != len(deals):
            fill_count_mismatches += 1
        if (
            statement_quantity != api_quantity
            or api_filled_quantity != api_quantity
        ):
            quantity_mismatches += 1
        if statement_quantity > 0 and api_quantity > 0:
            statement_vwap = (
                statement_order.detailed_average_price
                if statement_order.fills
                else statement_order.summary_average_price
            )
            api_vwap = sum(
                (
                    _api_decimal(row.get("qty"))
                    * _api_decimal(row.get("price"))
                    for row in deals
                ),
                Decimal("0"),
            ) / api_quantity
            if statement_vwap is None or (
                abs(statement_vwap - api_vwap) > Decimal("0.000001")
            ):
                vwap_mismatches += 1

        fee_row = api_fee_by_order.get(broker_order_id)
        if fee_row is None:
            fee_total_mismatches += 1
            fee_component_mismatches += 1
            continue
        if abs(
            statement_order.total_fee - _api_decimal(fee_row.get("fee_amount"))
        ) > Decimal("0.000001"):
            fee_total_mismatches += 1
        statement_components = dict(statement_order.fee_components)
        api_components = _fee_components_from_api(fee_row)
        if any(
            abs(statement_components.get(name, Decimal("0")) - api_components[name])
            > Decimal("0.000001")
            for name in _FEE_COLUMNS
        ):
            fee_component_mismatches += 1

    matched_orders = len(matched_pairs)
    statement_only = len(statement_window_orders) - matched_orders
    api_only = len(api_orders) - matched_orders
    mismatch_total = sum(
        (
            statement_only,
            api_only,
            status_mismatches,
            fill_count_mismatches,
            quantity_mismatches,
            vwap_mismatches,
            fee_total_mismatches,
            fee_component_mismatches,
            ambiguous_keys,
        )
    )
    api_summary = payload.get("summary")
    api_ready = bool(
        isinstance(api_summary, Mapping) and api_summary.get("analysis_ready") is True
    )
    aggregate_fee_totals_match = (
        abs(statement_fee_total - api_fee_total) <= Decimal("0.000001")
    )
    warnings: list[str] = []
    if not api_ready:
        warnings.append("api_export_not_analysis_ready")
    if not statement_quality_ok:
        warnings.append("statement_evidence_not_analysis_ready")
    if not api_record_links_ok:
        warnings.append("api_export_record_links_invalid")
    if not aggregate_fee_totals_match:
        warnings.append("aggregate_fee_total_mismatch")
    if mismatch_total:
        warnings.append("cross_source_reconciliation_failed")

    return StatementReconciliation(
        analysis_ready=(
            api_ready
            and statement_quality_ok
            and api_record_links_ok
            and aggregate_fee_totals_match
            and mismatch_total == 0
            and matched_orders > 0
        ),
        window_start=window_start,
        window_end=window_end,
        statement_orders=len(statement_window_orders),
        api_orders=len(api_orders),
        matched_orders=matched_orders,
        statement_only_orders=statement_only,
        api_only_orders=api_only,
        matched_filled_orders=matched_filled_orders,
        status_mismatches=status_mismatches,
        fill_count_mismatches=fill_count_mismatches,
        filled_quantity_mismatches=quantity_mismatches,
        fill_vwap_mismatches=vwap_mismatches,
        fee_total_mismatches=fee_total_mismatches,
        fee_component_mismatches=fee_component_mismatches,
        ambiguous_identity_keys=ambiguous_keys,
        statement_fee_total=statement_fee_total,
        api_fee_total=api_fee_total,
        matches=tuple(matches),
        warnings=tuple(warnings),
    )


def reconciliation_summary_json(
    statement: StatementParseResult,
    reconciliation: StatementReconciliation,
) -> str:
    """Return the intentionally de-identified CLI/report payload."""
    return json.dumps(
        {
            "statement": statement.summary(),
            "reconciliation": reconciliation.summary(),
        },
        ensure_ascii=False,
        indent=2,
    )
