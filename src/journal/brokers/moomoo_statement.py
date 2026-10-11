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
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

__all__ = [
    "CSV_PARSER_VERSION",
    "MoomooStatementError",
    "StatementComboLeg",
    "StatementFill",
    "StatementOrder",
    "StatementParseResult",
    "StatementApiMatch",
    "StatementReconciliation",
    "parse_statement",
    "reconcile_statement_with_readonly_export",
]


CSV_PARSER_VERSION = "moomoo-statement-v3"
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
class StatementComboLeg:
    """One combo leg display row nested under a combo parent order.

    These rows repeat the broker's own leg display (symbol, side, stated
    quantity and fill records).  They carry no status, order time or fee
    columns, so they are retained as evidence on the parent and are never
    promoted to standalone single-leg orders.
    """

    source_row: int
    symbol: str
    name: str
    side: str
    order_quantity: Optional[Decimal]
    fills: tuple[StatementFill, ...] = ()


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
    # Combo (multi-leg spread) parent orders.  ``order_quantity`` and the
    # ``Filled@Avg Price`` summary of such rows are stated in combo units,
    # not shares or contracts, and ``total_fee`` is the broker's group-scope
    # fee.  Leg-level economics are deliberately NOT reconstructed here.
    order_kind: str = "single"
    combo_unit_quantity: Optional[Decimal] = None
    combo_underlying: Optional[str] = None
    combo_expiry: Optional[date] = None
    combo_option_right: Optional[str] = None
    combo_strikes_text: Optional[str] = None
    combo_legs: tuple[StatementComboLeg, ...] = ()

    @property
    def is_combo_parent(self) -> bool:
        return self.order_kind == "combo_parent"

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
        combo_parents = [
            order for order in self.orders if order.is_combo_parent
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
            "combo_parent_orders": len(combo_parents),
            "combo_parent_leg_rows": sum(
                len(order.combo_legs) for order in combo_parents
            ),
            "combo_parent_fee_total": _decimal_text(
                sum((order.total_fee for order in combo_parents), Decimal("0"))
            ),
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
    overlap_api_only_orders: int = 0
    incremental_api_only_orders: int = 0

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
                "overlap_api_only": self.overlap_api_only_orders,
                "incremental_api_only": self.incremental_api_only_orders,
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


_UNIT_QUANTITY_RE = re.compile(r"^([0-9][0-9,.]*)\s*unit\(s\)$", re.IGNORECASE)

# Combo spread display symbols such as ``MU260731P745/760``.  The strike
# fragment is a broker display value, not an OCC strike code, so it is kept
# verbatim instead of being decoded into a fake single strike.
_COMBO_SPREAD_SYMBOL_RE = re.compile(
    r"^(?P<underlying>[A-Z][A-Z.]{0,9})"
    r"(?P<expiry>\d{6})"
    r"(?P<right>[CP])"
    r"(?P<strikes>\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)+)$"
)


def _parse_order_quantity(value: str) -> tuple[Decimal, bool]:
    """Parse an order quantity, detecting combo-unit ``N unit(s)`` values."""
    text = value.strip()
    match = _UNIT_QUANTITY_RE.match(text)
    if match:
        quantity = _parse_decimal(match.group(1), allow_missing=False)
        assert quantity is not None
        return quantity, True
    quantity = _parse_decimal(text, allow_missing=False)
    assert quantity is not None
    return quantity, False


def _parse_combo_spread_symbol(symbol: str) -> Optional[dict[str, Any]]:
    match = _COMBO_SPREAD_SYMBOL_RE.match(symbol.strip().upper())
    if match is None:
        return None
    try:
        expiry = datetime.strptime(match.group("expiry"), "%y%m%d").date()
    except ValueError:
        return None
    return {
        "underlying": match.group("underlying"),
        "expiry": expiry,
        "option_right": match.group("right"),
        "strikes_text": match.group("strikes"),
    }


def _parse_fill_summary(
    value: str,
) -> tuple[Optional[Decimal], Optional[Decimal], bool]:
    text = value.strip()
    if not text:
        return None, None, False
    if "@" not in text:
        # Older exports displayed only the average price.  Detail-backed rows
        # remain exact because quantity comes from their fill records.
        return None, _parse_decimal(text, allow_missing=False), False
    quantity_text, price_text = text.rsplit("@", 1)
    unit_match = _UNIT_QUANTITY_RE.match(quantity_text.strip())
    if unit_match:
        return (
            _parse_decimal(unit_match.group(1), allow_missing=False),
            _parse_decimal(price_text, allow_missing=False),
            True,
        )
    return (
        _parse_decimal(quantity_text, allow_missing=False),
        _parse_decimal(price_text, allow_missing=False),
        False,
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
    combo_legs: tuple[StatementComboLeg, ...] = (),
) -> StatementOrder:
    symbol = _normalise_symbol(headers.get(row, "Symbol"))
    side = _normalise_side(headers.get(row, "Side"))
    status = _normalise_statement_status(headers.get(row, "Status"))
    order_quantity, quantity_in_combo_units = _parse_order_quantity(
        headers.get(row, "Order Qty")
    )
    order_time = _parse_time(headers.get(row, "Order Time"))
    summary_quantity, summary_price, summary_in_combo_units = (
        _parse_fill_summary(headers.get(row, "Filled@Avg Price"))
    )
    fee_components = tuple(
        (name, _decimal_or_zero(headers.get(row, name))) for name in _FEE_COLUMNS
    )
    is_combo_parent = (
        quantity_in_combo_units or summary_in_combo_units or "/" in symbol
    )
    if is_combo_parent:
        return _build_combo_parent_order(
            source_row=source_row,
            row=row,
            headers=headers,
            fills=fills,
            combo_legs=combo_legs,
            symbol=symbol,
            side=side,
            status=status,
            order_quantity=order_quantity,
            quantity_in_combo_units=quantity_in_combo_units,
            order_time=order_time,
            summary_quantity=summary_quantity,
            summary_price=summary_price,
            summary_in_combo_units=summary_in_combo_units,
            fee_components=fee_components,
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


def _build_combo_parent_order(
    *,
    source_row: int,
    row: list[str],
    headers: _HeaderMap,
    fills: list[StatementFill],
    combo_legs: tuple[StatementComboLeg, ...],
    symbol: str,
    side: str,
    status: str,
    order_quantity: Decimal,
    quantity_in_combo_units: bool,
    order_time: datetime,
    summary_quantity: Optional[Decimal],
    summary_price: Optional[Decimal],
    summary_in_combo_units: bool,
    fee_components: tuple[tuple[str, Decimal], ...],
) -> StatementOrder:
    """Classify a combo (multi-leg spread) parent row without disguising it.

    The parent quantity and the ``Filled@Avg Price`` summary are combo-unit
    values, the fee tail is the broker's group-scope total, and no contract
    multiplier or per-leg allocation is ever derived here.
    """
    evidence_warnings: list[str] = []
    if not quantity_in_combo_units:
        evidence_warnings.append("combo_unit_quantity_missing")
    if summary_price is not None and not summary_in_combo_units:
        evidence_warnings.append("combo_summary_not_in_units")
    if status == "FILLED" and summary_price is None:
        evidence_warnings.append("missing_filled_summary")
    if (
        status == "FILLED"
        and summary_quantity is not None
        and summary_quantity <= 0
    ):
        evidence_warnings.append("non_positive_filled_summary_quantity")
    if fills:
        # Fill rows directly under the parent (outside any leg display row)
        # have no defined combo semantics; keep them but flag the shape.
        evidence_warnings.append("combo_parent_direct_fill_rows")
    for leg in combo_legs:
        if leg.order_quantity is not None and leg.fills:
            leg_filled = sum(
                (fill.quantity for fill in leg.fills), Decimal("0")
            )
            if leg_filled != leg.order_quantity:
                evidence_warnings.append("combo_leg_fill_quantity_mismatch")
                break
    combo_spec = _parse_combo_spread_symbol(symbol)
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
        evidence_level="combo_parent",
        evidence_warnings=tuple(evidence_warnings),
        order_kind="combo_parent",
        combo_unit_quantity=(
            order_quantity if quantity_in_combo_units else None
        ),
        combo_underlying=(
            None if combo_spec is None else combo_spec["underlying"]
        ),
        combo_expiry=None if combo_spec is None else combo_spec["expiry"],
        combo_option_right=(
            None if combo_spec is None else combo_spec["option_right"]
        ),
        combo_strikes_text=(
            None if combo_spec is None else combo_spec["strikes_text"]
        ),
        combo_legs=combo_legs,
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
    current_is_combo_parent = False
    current_fills: list[StatementFill] = []
    current_legs: list[dict[str, Any]] = []
    orphan_fill_rows = 0
    rows_total = 0

    def flush_current() -> None:
        nonlocal current_row, current_source_row, current_fills
        nonlocal current_is_combo_parent, current_legs
        if current_row is None or current_source_row is None:
            return
        orders.append(
            _build_order(
                source_row=current_source_row,
                row=current_row,
                headers=header_map,
                fills=current_fills,
                combo_legs=tuple(
                    StatementComboLeg(
                        source_row=leg["source_row"],
                        symbol=leg["symbol"],
                        name=leg["name"],
                        side=leg["side"],
                        order_quantity=leg["order_quantity"],
                        fills=tuple(leg["fills"]),
                    )
                    for leg in current_legs
                ),
            )
        )
        current_row = None
        current_source_row = None
        current_is_combo_parent = False
        current_fills = []
        current_legs = []

    def _row_is_combo_parent(row: list[str]) -> bool:
        quantity_text = header_map.get(row, "Order Qty").strip()
        if _UNIT_QUANTITY_RE.match(quantity_text):
            return True
        summary_text = header_map.get(row, "Filled@Avg Price").strip()
        if "@" in summary_text and _UNIT_QUANTITY_RE.match(
            summary_text.rsplit("@", 1)[0].strip()
        ):
            return True
        return "/" in _normalise_symbol(header_map.get(row, "Symbol"))

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
            # Combo parents are followed by leg display rows that repeat
            # Side/Symbol but have no order status or order time of their
            # own.  Those rows are leg evidence, not new orders.
            if (
                current_is_combo_parent
                and not header_map.get(row, "Status")
                and not header_map.get(row, "Order Time")
            ):
                current_legs.append(
                    {
                        "source_row": source_row,
                        "symbol": _normalise_symbol(
                            header_map.get(row, "Symbol")
                        ),
                        "name": header_map.get(row, "Name"),
                        "side": _normalise_side(header_map.get(row, "Side")),
                        "order_quantity": _parse_decimal(
                            header_map.get(row, "Order Qty")
                        ),
                        "fills": [],
                    }
                )
            else:
                flush_current()
                current_row = row
                current_source_row = source_row
                current_is_combo_parent = _row_is_combo_parent(row)

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
        fill = StatementFill(
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
        if current_legs:
            current_legs[-1]["fills"].append(fill)
        else:
            current_fills.append(fill)

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
    *,
    baseline_window_end: Optional[datetime] = None,
) -> StatementReconciliation:
    """Reconcile overlapping CSV and read-only OpenAPI order evidence.

    CSV has no broker order/deal IDs, so order identity uses a four-part key:
    normalized symbol, side, requested quantity and second-level order time.
    Ambiguous overlap keys are reported and never auto-linked.  When an aware
    ``baseline_window_end`` is supplied, unmatched broker-ID-backed orders
    strictly after it are classified as an internally validated incremental
    tail instead of an overlap mismatch.
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
    baseline_end: Optional[datetime] = None
    if baseline_window_end is not None:
        if (
            baseline_window_end.tzinfo is None
            or baseline_window_end.utcoffset() is None
        ):
            raise MoomooStatementError(
                "baseline_window_end must be timezone-aware"
            )
        baseline_end = baseline_window_end.astimezone(timezone)

    api_orders = list(records.get("orders") or [])
    api_deals = list(records.get("deals") or [])
    api_fees = list(records.get("fees") or [])
    if not all(isinstance(row, Mapping) for row in api_orders + api_deals + api_fees):
        raise MoomooStatementError("export records must be objects")

    # Combo parents have no CSV-provable leg-level truth and are stored as
    # audit-only parent observations, so they are excluded here explicitly
    # instead of being matched as if they were ordinary single-leg orders.
    statement_window_combo_parents = sum(
        order.is_combo_parent
        and window_start <= order.order_time <= window_end
        for order in statement.orders
    )
    statement_window_orders = [
        order
        for order in statement.orders
        if not order.is_combo_parent
        and window_start <= order.order_time <= window_end
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
    matched_pairs: list[tuple[StatementOrder, Mapping[str, Any]]] = []
    matched_keys: set[tuple[Any, ...]] = set()
    for key in all_keys:
        statement_rows = statement_by_key.get(key, [])
        api_rows = api_by_key.get(key, [])
        if len(statement_rows) == len(api_rows) == 1:
            matched_pairs.append((statement_rows[0], api_rows[0]))
            matched_keys.add(key)

    unmatched_api_rows = [
        row
        for key, rows in api_by_key.items()
        if key not in matched_keys
        for row in rows
    ]
    if baseline_end is None:
        incremental_api_rows: list[Mapping[str, Any]] = []
    else:
        incremental_api_rows = [
            row
            for row in unmatched_api_rows
            if _api_time(row.get("create_time"), timezone) > baseline_end
        ]
    incremental_api_row_ids = {id(row) for row in incremental_api_rows}
    # The CSV-derived four-part key is needed only inside the overlap.  Tail
    # rows already carry broker-stable order IDs, so two legitimate tail
    # orders with the same symbol/side/quantity/second are not ambiguous.
    ambiguous_keys = sum(
        1
        for key in all_keys
        if len(statement_by_key.get(key, [])) > 1
        or (
            len(api_by_key.get(key, [])) > 1
            and any(
                id(row) not in incremental_api_row_ids
                for row in api_by_key.get(key, [])
            )
        )
    )
    incremental_api_only = len(incremental_api_rows)
    overlap_api_only = len(unmatched_api_rows) - incremental_api_only
    incremental_api_order_ids = {
        str(row.get("order_id") or "")
        for row in incremental_api_rows
        if str(row.get("order_id") or "")
    }

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
    # Fees for a broker-only tail are internally validated by the strict
    # OpenAPI parser.  Cross-source fee equality applies only to the overlap;
    # retaining every other fee here also keeps orphan/overlap-only evidence
    # visible to the existing blocking checks.
    api_fee_total = sum(
        (
            _api_decimal(row.get("fee_amount"))
            for row in api_fees
            if str(row.get("order_id") or "")
            not in incremental_api_order_ids
        ),
        Decimal("0"),
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
            overlap_api_only,
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
    if statement_window_combo_parents:
        warnings.append(
            "csv_combo_parent_orders_excluded_from_reconciliation="
            f"{statement_window_combo_parents}"
        )
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
            and (
                matched_orders > 0
                or (
                    len(statement_window_orders) == 0
                    and overlap_api_only == 0
                )
            )
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
        overlap_api_only_orders=overlap_api_only,
        incremental_api_only_orders=incremental_api_only,
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
