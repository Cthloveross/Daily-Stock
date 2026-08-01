# -*- coding: utf-8 -*-
"""Pure parser for the de-identified Moomoo read-only export.

The input contract is produced by :mod:`moomoo_readonly` and contains only
whitelisted historical order, fill and fee fields.  This module deliberately
does not import Journal storage, repositories, SQLAlchemy or the Moomoo SDK.
Parsing therefore cannot write a database or perform a trade action.

The parser is intentionally strict.  Schema drift, malformed timestamps,
unstable broker IDs, duplicate identities, orphan fills/fees and internally
inconsistent summary facts are rejected before a later persistence layer can
see them.  A structurally valid but incomplete probe (for example a fee still
missing during the broker's settlement delay) remains parseable with
``analysis_ready=False`` and the probe's deterministic warnings.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.options.occ_parser import parse_symbol

__all__ = [
    "OPENAPI_EXPORT_PARSER_NAME",
    "OPENAPI_EXPORT_PARSER_VERSION",
    "READONLY_EXPORT_SCHEMA",
    "MoomooOpenApiExportError",
    "OpenApiBatchMetadata",
    "OpenApiExportPreview",
    "OpenApiComboLegObservation",
    "OpenApiContractSpecObservation",
    "OpenApiFeeObservation",
    "OpenApiFillObservation",
    "OpenApiOrderObservation",
    "OpenApiReconciliation",
    "parse_openapi_export",
    "parse_readonly_export",
]


READONLY_EXPORT_SCHEMA = "dsa.moomoo.readonly-export.v1"
OPENAPI_EXPORT_PARSER_NAME = "moomoo_openapi_export"
OPENAPI_EXPORT_PARSER_VERSION = "1.3.0"
_FEE_AMOUNT_TOLERANCE = Decimal("0.000001")
_QUANTITY_TOLERANCE = Decimal("0.00000001")
_BROKER_PARENT_TIME_SKEW_TOLERANCE = timedelta(seconds=1)
_BROKER_PARENT_TIME_SKEW_WARNING = (
    "broker_fill_precedes_order_create_within_1s"
)
_MAX_STABLE_ID_LENGTH = 128
_MAX_FEE_BATCH_SIZE = 400
_BROKER_FRACTIONAL_TIME = re.compile(
    r"^(?P<prefix>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r"\.(?P<fraction>\d{1,6})(?P<suffix>Z|[+-]\d{2}:\d{2})?$"
)

_MARKET_TIMEZONES = {
    "US": "America/New_York",
    "HK": "Asia/Hong_Kong",
}

_TOP_LEVEL_FIELDS = {
    "schema",
    "generated_at",
    "mode",
    "journal_database_written",
    "account",
    "window",
    "summary",
    "records",
}
_ACCOUNT_FIELDS = {"environment", "market", "selection"}
_OPTIONAL_ACCOUNT_FIELDS = {"binding"}
_WINDOW_FIELDS = {
    "start",
    "end",
    "timezone",
    "chunks",
    "max_chunk_days",
}
_RECORD_CONTAINER_FIELDS = {"orders", "deals", "fees"}
_OPTIONAL_RECORD_CONTAINER_FIELDS = {"contract_specs"}
_ORDER_FIELDS = {
    "order_id",
    "code",
    "stock_name",
    "order_market",
    "trd_side",
    "order_type",
    "order_status",
    "qty",
    "price",
    "create_time",
    "updated_time",
    "dealt_qty",
    "dealt_avg_price",
    "time_in_force",
    "fill_outside_rth",
    "session",
    "currency",
}
_OPTIONAL_ORDER_FIELDS = {"strategy_type", "combo_legs"}
_COMBO_LEG_FIELDS = {"code", "trd_side", "qty_ratio"}
_OPTION_STRATEGY_TYPES = {
    "NONE",
    "SINGLE",
    "COVERED",
    "SPREAD",
    "STRADDLE",
    "STRANGLE",
    "COLLAR",
    "BUTTERFLY",
    "CONDOR",
    "IRON_BUTTERFLY",
    "IRON_CONDOR",
    "CALENDAR_SPREAD",
    "DIAGONAL_SPREAD",
    "CUSTOM",
}
_SINGLE_LEG_STRATEGY_TYPES = {"NONE", "SINGLE"}
_DEAL_FIELDS = {
    "deal_id",
    "order_id",
    "code",
    "stock_name",
    "deal_market",
    "trd_side",
    "qty",
    "price",
    "create_time",
    "status",
}
_FEE_FIELDS = {"order_id", "fee_amount", "fee_details"}
_CONTRACT_SPEC_FIELDS = {
    "code",
    "lot_size",
    "option_contract_size",
    "option_contract_multiplier",
}
_SUMMARY_FIELDS = {
    "ok",
    "analysis_ready",
    "reconciliation_status",
    "warnings",
    "mode",
    "journal_database_written",
    "environment",
    "market",
    "account_selection",
    "window",
    "counts",
    "activity_sides",
    "fee_totals_by_currency",
    "activity_time_range",
    "deduplicated_rows",
    "reconciliation",
    "fee_batches",
}
_OPTIONAL_SUMMARY_FIELDS = {
    "retrieval_complete",
    "coverage_complete",
    "has_activity",
    "contract_spec_status",
}
_SUMMARY_COUNT_FIELDS = {
    "orders",
    "filled_orders",
    "fills",
    "fees",
    "unique_instruments",
    "option_activity_rows",
}
_OPTIONAL_SUMMARY_COUNT_FIELDS = {"contract_specs"}
_ACTIVITY_SIDE_FIELDS = {"buy", "sell", "other"}
_TIME_RANGE_FIELDS = {"first", "last"}
_DEDUPLICATION_FIELDS = {"orders", "fills", "fees"}
_OPTIONAL_DEDUPLICATION_FIELDS = {"contract_specs"}
_RECONCILIATION_FIELDS = {
    "filled_orders_without_fills",
    "fills_without_orders",
    "filled_quantity_mismatches",
    "fill_code_mismatches",
    "fill_side_mismatches",
    "fill_average_price_mismatches",
    "filled_orders_without_fees",
}
_OPTIONAL_RECONCILIATION_FIELDS = {"unsupported_combo_orders"}


class MoomooOpenApiExportError(ValueError):
    """Raised when a read-only export cannot be interpreted safely."""


@dataclass(frozen=True)
class OpenApiComboLegObservation:
    """One normalized leg declared by a broker combo order."""

    raw_symbol: str
    side: str
    quantity_ratio: Decimal


@dataclass(frozen=True)
class OpenApiOrderObservation:
    """One immutable broker order observation from the read-only export."""

    source_order_id: str
    raw_symbol: str
    stock_name: str
    market: str
    side: str
    order_type: str
    status: str
    order_quantity: Decimal
    order_price: Optional[Decimal]
    ordered_at: datetime
    source_updated_at: datetime
    summary_filled_quantity: Decimal
    summary_average_fill_price: Optional[Decimal]
    time_in_force: str
    fill_outside_rth: Optional[bool]
    session: str
    currency: str
    source_record_sha256: str
    strategy_type: str = ""
    combo_legs: tuple[OpenApiComboLegObservation, ...] = ()
    combo_definition_available: bool = True


@dataclass(frozen=True)
class OpenApiFillObservation:
    """One immutable, broker-ID-backed execution observation."""

    source_deal_id: str
    source_order_id: str
    raw_symbol: str
    stock_name: str
    market: str
    side: str
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    source_status: str
    currency: str
    source_record_sha256: str


@dataclass(frozen=True)
class OpenApiFeeObservation:
    """Order-level fee evidence returned by ``order_fee_query``."""

    source_order_id: str
    total_fee: Decimal
    fee_components: tuple[tuple[str, Decimal], ...]
    source_record_sha256: str


@dataclass(frozen=True)
class OpenApiContractSpecObservation:
    """Frozen quote evidence for one option contract multiplier."""

    raw_symbol: str
    lot_size: Decimal
    option_contract_size: Decimal
    option_contract_multiplier: Decimal
    resolved_multiplier: Decimal
    source_record_sha256: str


@dataclass(frozen=True)
class OpenApiReconciliation:
    """Typed copy of the probe's independently recomputed quality checks."""

    filled_orders_without_fills: Optional[int]
    fills_without_orders: Optional[int]
    filled_quantity_mismatches: Optional[int]
    fill_code_mismatches: Optional[int]
    fill_side_mismatches: Optional[int]
    fill_average_price_mismatches: Optional[int]
    filled_orders_without_fees: Optional[int]
    unsupported_combo_orders: Optional[int] = None

    def as_dict(self) -> dict[str, Optional[int]]:
        return {
            name: getattr(self, name)
            for name in sorted(
                _RECONCILIATION_FIELDS | _OPTIONAL_RECONCILIATION_FIELDS
            )
        }


@dataclass(frozen=True)
class OpenApiBatchMetadata:
    """Canonical, account-deidentified metadata for a parsed source batch."""

    source_schema: str
    source_sha256: str
    evidence_sha256: str
    batch_key: str
    parser_name: str
    parser_version: str
    generated_at: datetime
    environment: str
    market: str
    account_selection: str
    window_start: datetime
    window_end: datetime
    source_timezone: str
    window_chunks: int
    max_chunk_days: int
    analysis_ready: bool
    analysis_level: str
    reconciliation_status: str
    reconciliation: OpenApiReconciliation
    warnings: tuple[str, ...]
    order_observation_count: int
    fill_observation_count: int
    fee_observation_count: int
    rejected_record_count: int = 0
    journal_database_written: bool = False
    retrieval_complete: bool = True
    coverage_complete: bool = True
    has_activity: bool = True
    account_binding: Optional[str] = None
    contract_spec_observation_count: int = 0
    contract_spec_status: str = "not_available"


@dataclass(frozen=True)
class OpenApiExportPreview:
    """Pure parse result; constructing it has no persistence side effects."""

    metadata: OpenApiBatchMetadata
    orders: tuple[OpenApiOrderObservation, ...]
    fills: tuple[OpenApiFillObservation, ...]
    fees: tuple[OpenApiFeeObservation, ...]
    contract_specs: tuple[OpenApiContractSpecObservation, ...] = ()

    def summary(self) -> dict[str, Any]:
        """Return a de-identified preview that contains no broker row IDs."""
        metadata = self.metadata
        return {
            "schema": metadata.source_schema,
            "parser": {
                "name": metadata.parser_name,
                "version": metadata.parser_version,
            },
            "source_sha256": metadata.source_sha256,
            "evidence_sha256": metadata.evidence_sha256,
            "batch_key": metadata.batch_key,
            "environment": metadata.environment,
            "market": metadata.market,
            "window": {
                "start": metadata.window_start.isoformat(),
                "end": metadata.window_end.isoformat(),
                "timezone": metadata.source_timezone,
            },
            "analysis_ready": metadata.analysis_ready,
            "analysis_level": metadata.analysis_level,
            "retrieval_complete": metadata.retrieval_complete,
            "coverage_complete": metadata.coverage_complete,
            "has_activity": metadata.has_activity,
            "reconciliation_status": metadata.reconciliation_status,
            "reconciliation": metadata.reconciliation.as_dict(),
            "warnings": list(metadata.warnings),
            "counts": {
                "orders": len(self.orders),
                "fills": len(self.fills),
                "fees": len(self.fees),
                "contract_specs": len(self.contract_specs),
                "rejected": metadata.rejected_record_count,
            },
            "contract_spec_status": metadata.contract_spec_status,
            "journal_database_written": False,
        }


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MoomooOpenApiExportError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise MoomooOpenApiExportError(f"{path} contains a non-string field name")
    return value


def _validate_fields(
    value: Mapping[str, Any],
    expected: set[str],
    path: str,
    *,
    optional: Optional[set[str]] = None,
) -> None:
    optional = optional or set()
    missing = sorted(expected - set(value))
    unsupported = sorted(set(value) - expected - optional)
    if missing:
        raise MoomooOpenApiExportError(
            f"{path} is missing required fields: {', '.join(missing)}"
        )
    if unsupported:
        raise MoomooOpenApiExportError(
            f"{path} contains unsupported fields: {', '.join(unsupported)}"
        )


def _require_rows(value: Any, path: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise MoomooOpenApiExportError(f"{path} must be an array")
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(value):
        rows.append(_require_mapping(row, f"{path}[{index}]"))
    return rows


def _require_text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise MoomooOpenApiExportError(f"{path} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise MoomooOpenApiExportError(f"{path} cannot be empty")
    return text


def _enum_text(value: Any, path: str) -> str:
    text = _require_text(value, path).upper()
    return text.rsplit(".", 1)[-1]


def _stable_id(value: Any, path: str) -> str:
    if isinstance(value, bool):
        raise MoomooOpenApiExportError(f"{path} must be a stable string or integer ID")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise MoomooOpenApiExportError(f"{path} must be a stable string or integer ID")
    if (
        not text
        or text.lower() in {"nan", "none", "null"}
        or len(text) > _MAX_STABLE_ID_LENGTH
        or any(ord(character) < 32 for character in text)
    ):
        raise MoomooOpenApiExportError(f"{path} is not a valid stable ID")
    return text


def _normalize_decimal_representation(number: Decimal) -> Decimal:
    """Remove tails attributable to the probe's binary-float transport.

    The same JSON may be loaded as Python ``float`` or directly as ``Decimal``.
    Normalization therefore depends on the numeric value, not the caller's
    loader type.  The tolerance mirrors eight ULPs of the finite IEEE-754
    representation used by the Moomoo SDK/pandas export path.  Legitimate tiny
    values remain because their distance from a shorter decimal is many ULPs.
    """
    try:
        approximate = float(number)
    except (OverflowError, ValueError):
        return number
    if not math.isfinite(approximate):
        return number
    tolerance = Decimal(str(math.ulp(approximate) * 8))
    for places in range(17):
        try:
            candidate = number.quantize(Decimal(1).scaleb(-places))
        except InvalidOperation:
            continue
        if abs(number - candidate) <= tolerance:
            return candidate.normalize()
    return number


def _decimal(
    value: Any,
    path: str,
    *,
    allow_none: bool = False,
    minimum: Optional[Decimal] = None,
    strictly_positive: bool = False,
) -> Optional[Decimal]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise MoomooOpenApiExportError(f"{path} must be a finite decimal number")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise MoomooOpenApiExportError(
            f"{path} must be a finite decimal number"
        ) from None
    if not number.is_finite():
        raise MoomooOpenApiExportError(f"{path} must be a finite decimal number")
    # SDK/pandas values arrive through JSON as binary floats.  Normalize the
    # representation for every loader type so ``float`` and ``Decimal`` JSON
    # loaders produce identical observations and batch identities.
    number = _normalize_decimal_representation(number)
    if minimum is not None and number < minimum:
        raise MoomooOpenApiExportError(f"{path} is below its supported minimum")
    if strictly_positive and number <= 0:
        raise MoomooOpenApiExportError(f"{path} must be greater than zero")
    return number


def _nonnegative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MoomooOpenApiExportError(f"{path} must be a non-negative integer")
    return value


def _required_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise MoomooOpenApiExportError(f"{path} must be a boolean")
    return value


def _optional_bool(value: Any, path: str) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().upper()
        if text in {"TRUE", "YES", "1"}:
            return True
        if text in {"FALSE", "NO", "0"}:
            return False
        if text in {"", "N/A", "NA", "NONE"}:
            return None
    raise MoomooOpenApiExportError(
        f"{path} must be true, false or an explicit unavailable marker"
    )


def _parse_aware_iso(value: Any, path: str) -> datetime:
    text = _require_text(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise MoomooOpenApiExportError(f"{path} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MoomooOpenApiExportError(f"{path} must include a UTC offset")
    return parsed


def _parse_source_time(value: Any, path: str, zone: ZoneInfo) -> datetime:
    text = _require_text(value, path)
    # Moomoo can emit millisecond text with a non-standard fourth digit such
    # as ``.1000``.  Python 3.10's ``datetime.fromisoformat`` accepts only
    # specific fractional widths, so normalize any broker precision from one
    # through six digits to microseconds without changing its value.
    fractional = _BROKER_FRACTIONAL_TIME.fullmatch(text)
    if fractional is not None:
        text = (
            f"{fractional.group('prefix')}."
            f"{fractional.group('fraction').ljust(6, '0')}"
            f"{fractional.group('suffix') or ''}"
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise MoomooOpenApiExportError(f"{path} has an invalid broker timestamp") from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    normalized = parsed.astimezone(zone)
    if parsed.utcoffset() != normalized.utcoffset():
        raise MoomooOpenApiExportError(
            f"{path} offset does not match the declared source timezone"
        )
    return normalized


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _canonical_value(value: Any, path: str = "payload") -> Any:
    """Create a JSON-safe, type-preserving canonical projection."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise MoomooOpenApiExportError(
                f"{path} contains a timezone-naive datetime"
            )
        return {"$datetime": value.isoformat()}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise MoomooOpenApiExportError(f"{path} contains a non-finite number")
        return {
            "$number": _decimal_text(_normalize_decimal_representation(value))
        }
    if isinstance(value, int):
        return {"$number": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MoomooOpenApiExportError(f"{path} contains a non-finite number")
        normalized = _normalize_decimal_representation(Decimal(str(value)))
        return {"$number": _decimal_text(normalized)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise MoomooOpenApiExportError(
                f"{path} contains a non-string field name"
            )
        return {
            key: _canonical_value(item, f"{path}.{key}")
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise MoomooOpenApiExportError(
        f"{path} contains unsupported value type {type(value).__name__}"
    )


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _order_projection(order: OpenApiOrderObservation) -> dict[str, Any]:
    return {
        "order_id": order.source_order_id,
        "code": order.raw_symbol,
        "stock_name": order.stock_name,
        "order_market": order.market,
        "trd_side": order.side,
        "order_type": order.order_type,
        "order_status": order.status,
        "qty": order.order_quantity,
        "price": order.order_price,
        "create_time": order.ordered_at,
        "updated_time": order.source_updated_at,
        "dealt_qty": order.summary_filled_quantity,
        "dealt_avg_price": order.summary_average_fill_price,
        "time_in_force": order.time_in_force,
        "fill_outside_rth": order.fill_outside_rth,
        "session": order.session,
        "currency": order.currency,
        "strategy_type": order.strategy_type,
        "combo_legs": [
            {
                "code": leg.raw_symbol,
                "trd_side": leg.side,
                "qty_ratio": leg.quantity_ratio,
            }
            for leg in order.combo_legs
        ],
        "combo_definition_available": order.combo_definition_available,
    }


def _fill_projection(fill: OpenApiFillObservation) -> dict[str, Any]:
    return {
        "deal_id": fill.source_deal_id,
        "order_id": fill.source_order_id,
        "code": fill.raw_symbol,
        "stock_name": fill.stock_name,
        "deal_market": fill.market,
        "trd_side": fill.side,
        "qty": fill.quantity,
        "price": fill.price,
        "create_time": fill.filled_at,
        "status": fill.source_status,
        "currency": fill.currency,
    }


def _fee_projection(fee: OpenApiFeeObservation) -> dict[str, Any]:
    return {
        "order_id": fee.source_order_id,
        "fee_amount": fee.total_fee,
        "fee_details": list(fee.fee_components),
    }


def _contract_spec_projection(
    spec: OpenApiContractSpecObservation,
) -> dict[str, Any]:
    return {
        "code": spec.raw_symbol,
        "lot_size": spec.lot_size,
        "option_contract_size": spec.option_contract_size,
        "option_contract_multiplier": spec.option_contract_multiplier,
        "resolved_multiplier": spec.resolved_multiplier,
    }


def _parse_contract_spec(
    row: Mapping[str, Any],
    index: int,
) -> OpenApiContractSpecObservation:
    path = f"records.contract_specs[{index}]"
    _validate_fields(row, _CONTRACT_SPEC_FIELDS, path)
    raw_symbol = _require_text(row["code"], f"{path}.code").upper()
    if not _is_option_code(raw_symbol):
        raise MoomooOpenApiExportError(
            f"{path}.code must identify an option contract"
        )
    values: dict[str, Decimal] = {}
    for field_name in (
        "lot_size",
        "option_contract_size",
        "option_contract_multiplier",
    ):
        parsed = _decimal(
            row[field_name],
            f"{path}.{field_name}",
            minimum=Decimal("0"),
            strictly_positive=True,
        )
        assert parsed is not None
        values[field_name] = parsed
    if len(set(values.values())) != 1:
        raise MoomooOpenApiExportError(
            f"{path} contract-size fields disagree"
        )
    resolved = values["option_contract_multiplier"]
    payload = {
        "raw_symbol": raw_symbol,
        **values,
        "resolved_multiplier": resolved,
    }
    return OpenApiContractSpecObservation(
        **payload,
        source_record_sha256=_sha256_json(payload),
    )


def _parse_order(
    row: Mapping[str, Any],
    index: int,
    *,
    zone: ZoneInfo,
    market: str,
    window_start: datetime,
    window_end: datetime,
) -> OpenApiOrderObservation:
    path = f"records.orders[{index}]"
    _validate_fields(
        row,
        _ORDER_FIELDS,
        path,
        optional=_OPTIONAL_ORDER_FIELDS,
    )
    source_order_id = _stable_id(row["order_id"], f"{path}.order_id")
    raw_symbol = _require_text(row["code"], f"{path}.code").upper()
    row_market = _enum_text(row["order_market"], f"{path}.order_market")
    if row_market != market:
        raise MoomooOpenApiExportError(f"{path}.order_market conflicts with account.market")
    quantity = _decimal(
        row["qty"], f"{path}.qty", minimum=Decimal("0"), strictly_positive=True
    )
    dealt_quantity = _decimal(
        row["dealt_qty"], f"{path}.dealt_qty", minimum=Decimal("0")
    )
    assert quantity is not None and dealt_quantity is not None
    if dealt_quantity - quantity > _QUANTITY_TOLERANCE:
        raise MoomooOpenApiExportError(f"{path}.dealt_qty exceeds order quantity")
    order_price = _decimal(
        row["price"], f"{path}.price", allow_none=True, minimum=Decimal("0")
    )
    average_price = _decimal(
        row["dealt_avg_price"],
        f"{path}.dealt_avg_price",
        allow_none=True,
        minimum=Decimal("0"),
    )
    if dealt_quantity > 0 and (average_price is None or average_price <= 0):
        raise MoomooOpenApiExportError(
            f"{path}.dealt_avg_price must be positive for an executed order"
        )
    ordered_at = _parse_source_time(row["create_time"], f"{path}.create_time", zone)
    updated_at = _parse_source_time(row["updated_time"], f"{path}.updated_time", zone)
    if not window_start <= ordered_at <= window_end:
        raise MoomooOpenApiExportError(f"{path}.create_time is outside the export window")
    if updated_at < ordered_at:
        raise MoomooOpenApiExportError(f"{path}.updated_time precedes create_time")
    currency = _require_text(row["currency"], f"{path}.currency").upper()
    has_strategy_type = "strategy_type" in row
    has_combo_legs = "combo_legs" in row
    if has_strategy_type != has_combo_legs:
        raise MoomooOpenApiExportError(
            f"{path}.strategy_type and combo_legs must appear together"
        )
    strategy_type = ""
    if has_strategy_type:
        strategy_type = _enum_text(
            row["strategy_type"], f"{path}.strategy_type"
        )
        if strategy_type not in _OPTION_STRATEGY_TYPES:
            raise MoomooOpenApiExportError(
                f"{path}.strategy_type is unsupported"
            )
    combo_legs_value = row.get("combo_legs", [])
    if not isinstance(combo_legs_value, (list, tuple)):
        raise MoomooOpenApiExportError(f"{path}.combo_legs must be an array")
    combo_legs: list[OpenApiComboLegObservation] = []
    combo_identities: set[tuple[str, str]] = set()
    for leg_index, raw_leg in enumerate(combo_legs_value):
        leg_path = f"{path}.combo_legs[{leg_index}]"
        leg = _require_mapping(raw_leg, leg_path)
        _validate_fields(leg, _COMBO_LEG_FIELDS, leg_path)
        leg_symbol = _require_text(leg["code"], f"{leg_path}.code").upper()
        leg_side = _enum_text(leg["trd_side"], f"{leg_path}.trd_side")
        leg_ratio = _decimal(
            leg["qty_ratio"],
            f"{leg_path}.qty_ratio",
            minimum=Decimal("0"),
            strictly_positive=True,
        )
        assert leg_ratio is not None
        identity = (leg_symbol, leg_side)
        if identity in combo_identities:
            raise MoomooOpenApiExportError(
                f"{path}.combo_legs contains duplicate code/side identities"
            )
        combo_identities.add(identity)
        combo_legs.append(
            OpenApiComboLegObservation(
                raw_symbol=leg_symbol,
                side=leg_side,
                quantity_ratio=leg_ratio,
            )
        )
    if combo_legs and len(combo_legs) < 2:
        raise MoomooOpenApiExportError(
            f"{path}.combo_legs must contain at least two legs"
        )
    if has_strategy_type:
        if strategy_type in _SINGLE_LEG_STRATEGY_TYPES and combo_legs:
            raise MoomooOpenApiExportError(
                f"{path}.combo_legs are not allowed for {strategy_type}"
            )
        if strategy_type not in _SINGLE_LEG_STRATEGY_TYPES and not combo_legs:
            raise MoomooOpenApiExportError(
                f"{path}.combo_legs are required for {strategy_type}"
            )
    normalized_combo_legs = tuple(
        sorted(
            combo_legs,
            key=lambda leg: (
                leg.raw_symbol,
                leg.side,
                leg.quantity_ratio,
            ),
        )
    )
    values = {
        "source_order_id": source_order_id,
        "raw_symbol": raw_symbol,
        "stock_name": _require_text(
            row["stock_name"], f"{path}.stock_name", allow_empty=True
        ),
        "market": row_market,
        "side": _enum_text(row["trd_side"], f"{path}.trd_side"),
        "order_type": _enum_text(row["order_type"], f"{path}.order_type"),
        "status": _enum_text(row["order_status"], f"{path}.order_status"),
        "order_quantity": quantity,
        "order_price": order_price,
        "ordered_at": ordered_at,
        "source_updated_at": updated_at,
        "summary_filled_quantity": dealt_quantity,
        "summary_average_fill_price": average_price,
        "time_in_force": _enum_text(
            row["time_in_force"], f"{path}.time_in_force"
        ),
        "fill_outside_rth": _optional_bool(
            row["fill_outside_rth"], f"{path}.fill_outside_rth"
        ),
        "session": _enum_text(row["session"], f"{path}.session"),
        "currency": currency,
        "strategy_type": strategy_type,
        "combo_legs": normalized_combo_legs,
        "combo_definition_available": has_strategy_type,
    }
    record_hash = _sha256_json(
        {
            **values,
            "combo_legs": [
                {
                    "code": leg.raw_symbol,
                    "trd_side": leg.side,
                    "qty_ratio": leg.quantity_ratio,
                }
                for leg in normalized_combo_legs
            ],
        }
    )
    return OpenApiOrderObservation(
        **values,
        source_record_sha256=record_hash,
    )


def _parse_fill(
    row: Mapping[str, Any],
    index: int,
    *,
    zone: ZoneInfo,
    market: str,
    window_start: datetime,
    window_end: datetime,
    orders_by_id: Mapping[str, OpenApiOrderObservation],
) -> OpenApiFillObservation:
    path = f"records.deals[{index}]"
    _validate_fields(row, _DEAL_FIELDS, path)
    source_deal_id = _stable_id(row["deal_id"], f"{path}.deal_id")
    source_order_id = _stable_id(row["order_id"], f"{path}.order_id")
    parent = orders_by_id.get(source_order_id)
    if parent is None:
        raise MoomooOpenApiExportError(f"{path}.order_id does not reference an exported order")
    row_market = _enum_text(row["deal_market"], f"{path}.deal_market")
    if row_market != market:
        raise MoomooOpenApiExportError(f"{path}.deal_market conflicts with account.market")
    raw_symbol = _require_text(row["code"], f"{path}.code").upper()
    side = _enum_text(row["trd_side"], f"{path}.trd_side")
    source_status = _enum_text(row["status"], f"{path}.status")
    filled_at = _parse_source_time(row["create_time"], f"{path}.create_time", zone)
    if not window_start <= filled_at <= window_end:
        raise MoomooOpenApiExportError(f"{path}.create_time is outside the export window")
    if filled_at < parent.ordered_at:
        # The OpenAPI contract exposes order creation and transaction time as
        # separate broker records.  A real LIVE sample showed one sub-second
        # inversion linked by stable IDs and acknowledged by a prompt parent
        # update.  Full quantity/price reconciliation still runs below and can
        # independently keep the evidence analysis-blocked.
        # Keep this exception deliberately narrow: the parent must acknowledge
        # the execution within the same one-second guardrail and the immutable
        # identity/instrument/direction/status facts must already agree.
        identity_matches_parent = (
            (raw_symbol, side)
            in {(leg.raw_symbol, leg.side) for leg in parent.combo_legs}
            if parent.combo_legs
            else raw_symbol == parent.raw_symbol and side == parent.side
        )
        if (
            parent.ordered_at - filled_at > _BROKER_PARENT_TIME_SKEW_TOLERANCE
            or parent.source_updated_at - filled_at
            > _BROKER_PARENT_TIME_SKEW_TOLERANCE
            or parent.summary_filled_quantity <= 0
            or not identity_matches_parent
            or source_status != "OK"
        ):
            raise MoomooOpenApiExportError(
                f"{path}.create_time precedes the parent order outside the "
                "guarded broker timestamp tolerance"
            )
    quantity = _decimal(
        row["qty"], f"{path}.qty", minimum=Decimal("0"), strictly_positive=True
    )
    price = _decimal(row["price"], f"{path}.price", minimum=Decimal("0"))
    assert quantity is not None and price is not None
    values = {
        "source_deal_id": source_deal_id,
        "source_order_id": source_order_id,
        "raw_symbol": raw_symbol,
        "stock_name": _require_text(
            row["stock_name"], f"{path}.stock_name", allow_empty=True
        ),
        "market": row_market,
        "side": side,
        "quantity": quantity,
        "price": price,
        "filled_at": filled_at,
        "source_status": source_status,
        "currency": parent.currency,
    }
    record_hash = _sha256_json(values)
    return OpenApiFillObservation(
        **values,
        source_record_sha256=record_hash,
    )


def _fee_components(value: Any, path: str) -> tuple[tuple[str, Decimal], ...]:
    if not isinstance(value, (list, tuple)):
        raise MoomooOpenApiExportError(f"{path} must be an array of name/amount pairs")
    components: dict[str, Decimal] = {}
    for index, pair in enumerate(value):
        pair_path = f"{path}[{index}]"
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise MoomooOpenApiExportError(
                f"{pair_path} must contain exactly a name and an amount"
            )
        name = _require_text(pair[0], f"{pair_path}[0]")
        if name in components:
            raise MoomooOpenApiExportError(f"{path} contains duplicate component names")
        amount = _decimal(pair[1], f"{pair_path}[1]")
        assert amount is not None
        components[name] = amount
    return tuple(sorted(components.items()))


def _parse_fee(
    row: Mapping[str, Any],
    index: int,
    *,
    orders_by_id: Mapping[str, OpenApiOrderObservation],
) -> OpenApiFeeObservation:
    path = f"records.fees[{index}]"
    _validate_fields(row, _FEE_FIELDS, path)
    source_order_id = _stable_id(row["order_id"], f"{path}.order_id")
    parent = orders_by_id.get(source_order_id)
    if parent is None:
        raise MoomooOpenApiExportError(f"{path}.order_id does not reference an exported order")
    if parent.summary_filled_quantity <= 0:
        raise MoomooOpenApiExportError(f"{path} references an order without execution evidence")
    total_fee = _decimal(row["fee_amount"], f"{path}.fee_amount")
    assert total_fee is not None
    components = _fee_components(row["fee_details"], f"{path}.fee_details")
    component_total = sum((amount for _, amount in components), Decimal("0"))
    if abs(total_fee - component_total) > _FEE_AMOUNT_TOLERANCE:
        raise MoomooOpenApiExportError(
            f"{path}.fee_amount does not equal its fee component total"
        )
    values = {
        "source_order_id": source_order_id,
        "total_fee": total_fee,
        "fee_components": components,
    }
    return OpenApiFeeObservation(
        **values,
        source_record_sha256=_sha256_json(values),
    )


def _ensure_unique(values: Sequence[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise MoomooOpenApiExportError(f"duplicate stable {label} IDs are not supported")


def _is_option_code(code: str) -> bool:
    text = code.upper().split(".", 1)[-1]
    try:
        return parse_symbol(text).is_option
    except ValueError:
        return False


def _reconcile(
    orders: Sequence[OpenApiOrderObservation],
    fills: Sequence[OpenApiFillObservation],
    fees: Sequence[OpenApiFeeObservation],
) -> OpenApiReconciliation:
    orders_by_id = {order.source_order_id: order for order in orders}
    filled_orders = {
        order.source_order_id: order
        for order in orders
        if order.summary_filled_quantity > 0
    }
    fills_by_order: dict[str, list[OpenApiFillObservation]] = {}
    for fill in fills:
        fills_by_order.setdefault(fill.source_order_id, []).append(fill)

    quantity_mismatches = 0
    code_mismatches = 0
    side_mismatches = 0
    average_price_mismatches = 0
    unsupported_combo_orders = sum(
        bool(order.combo_legs) for order in filled_orders.values()
    )
    for source_order_id, order in filled_orders.items():
        order_fills = fills_by_order.get(source_order_id, [])
        if not order_fills:
            continue
        if order.combo_legs:
            declared = {
                (leg.raw_symbol, leg.side): leg.quantity_ratio
                for leg in order.combo_legs
            }
            actual: dict[tuple[str, str], Decimal] = {}
            for fill in order_fills:
                identity = (fill.raw_symbol, fill.side)
                actual[identity] = actual.get(identity, Decimal("0")) + fill.quantity
            declared_codes = {symbol for symbol, _side in declared}
            if any(symbol not in declared_codes for symbol, _side in actual):
                code_mismatches += 1
            if any(
                identity not in declared and identity[0] in declared_codes
                for identity in actual
            ):
                side_mismatches += 1
            if set(actual) != set(declared) or any(
                abs(
                    actual.get(identity, Decimal("0"))
                    - order.summary_filled_quantity * ratio
                )
                > _QUANTITY_TOLERANCE
                for identity, ratio in declared.items()
            ):
                quantity_mismatches += 1
            # A combo parent's quantity and average price are group-unit/net
            # values.  They cannot be checked against a flat sum/VWAP of legs.
            continue
        if any(fill.raw_symbol != order.raw_symbol for fill in order_fills):
            code_mismatches += 1
        if any(fill.side != order.side for fill in order_fills):
            side_mismatches += 1
        fill_quantity = sum((fill.quantity for fill in order_fills), Decimal("0"))
        if abs(fill_quantity - order.summary_filled_quantity) > _QUANTITY_TOLERANCE:
            quantity_mismatches += 1
        if fill_quantity > 0:
            actual_average = sum(
                (fill.quantity * fill.price for fill in order_fills), Decimal("0")
            ) / fill_quantity
            expected_average = order.summary_average_fill_price or Decimal("0")
            tolerance = max(
                Decimal("0.0001"),
                abs(expected_average) * Decimal("0.000001"),
            )
            if abs(expected_average - actual_average) > tolerance:
                average_price_mismatches += 1

    fee_order_ids = {fee.source_order_id for fee in fees}
    return OpenApiReconciliation(
        filled_orders_without_fills=len(set(filled_orders) - set(fills_by_order)),
        fills_without_orders=sum(
            source_order_id not in orders_by_id for source_order_id in fills_by_order
        ),
        filled_quantity_mismatches=quantity_mismatches,
        fill_code_mismatches=code_mismatches,
        fill_side_mismatches=side_mismatches,
        fill_average_price_mismatches=average_price_mismatches,
        filled_orders_without_fees=len(set(filled_orders) - fee_order_ids),
        unsupported_combo_orders=unsupported_combo_orders,
    )


def _validate_summary(
    summary: Mapping[str, Any],
    *,
    account: Mapping[str, Any],
    window: Mapping[str, Any],
    environment: str,
    market: str,
    orders: Sequence[OpenApiOrderObservation],
    fills: Sequence[OpenApiFillObservation],
    fees: Sequence[OpenApiFeeObservation],
    contract_specs: Sequence[OpenApiContractSpecObservation],
    reconciliation: OpenApiReconciliation,
    zone: ZoneInfo,
) -> tuple[bool, str, tuple[str, ...], bool, bool, bool, str]:
    _validate_fields(
        summary,
        _SUMMARY_FIELDS,
        "summary",
        optional=_OPTIONAL_SUMMARY_FIELDS,
    )
    if summary["ok"] is not True:
        raise MoomooOpenApiExportError("summary.ok must prove a completed retrieval")
    if summary["mode"] != "moomoo_readonly_probe":
        raise MoomooOpenApiExportError("summary.mode is unsupported")
    if summary["journal_database_written"] is not False:
        raise MoomooOpenApiExportError(
            "summary does not prove that the Journal database stayed untouched"
        )
    if summary["environment"] != environment or summary["market"] != market:
        raise MoomooOpenApiExportError("summary account scope conflicts with account metadata")
    if summary["account_selection"] != account["selection"]:
        raise MoomooOpenApiExportError("summary account selection conflicts with account metadata")
    summary_window = _require_mapping(summary["window"], "summary.window")
    _validate_fields(summary_window, _WINDOW_FIELDS, "summary.window")
    if _sha256_json(summary_window) != _sha256_json(window):
        raise MoomooOpenApiExportError("summary.window conflicts with the export window")

    counts = _require_mapping(summary["counts"], "summary.counts")
    _validate_fields(
        counts,
        _SUMMARY_COUNT_FIELDS,
        "summary.counts",
        optional=_OPTIONAL_SUMMARY_COUNT_FIELDS,
    )
    parsed_counts = {
        name: _nonnegative_int(counts[name], f"summary.counts.{name}")
        for name in _SUMMARY_COUNT_FIELDS
    }
    filled_orders = [order for order in orders if order.summary_filled_quantity > 0]
    activity: Sequence[OpenApiFillObservation | OpenApiOrderObservation]
    activity = fills if fills else filled_orders
    expected_counts = {
        "orders": len(orders),
        "filled_orders": len(filled_orders),
        "fills": len(fills),
        "fees": len(fees),
        "unique_instruments": len({item.raw_symbol for item in activity}),
        "option_activity_rows": sum(
            1 for item in activity if _is_option_code(item.raw_symbol)
        ),
    }
    if parsed_counts != expected_counts:
        raise MoomooOpenApiExportError("summary.counts do not match parsed observations")
    if "contract_specs" in counts:
        if _nonnegative_int(
            counts["contract_specs"], "summary.counts.contract_specs"
        ) != len(contract_specs):
            raise MoomooOpenApiExportError(
                "summary.counts.contract_specs does not match parsed observations"
            )
    elif contract_specs:
        raise MoomooOpenApiExportError(
            "summary.counts omits present contract specifications"
        )

    sides = _require_mapping(summary["activity_sides"], "summary.activity_sides")
    _validate_fields(sides, _ACTIVITY_SIDE_FIELDS, "summary.activity_sides")
    parsed_sides = {
        name: _nonnegative_int(sides[name], f"summary.activity_sides.{name}")
        for name in _ACTIVITY_SIDE_FIELDS
    }
    expected_sides = {"buy": 0, "sell": 0, "other": 0}
    for item in activity:
        key = "buy" if "BUY" in item.side else "sell" if "SELL" in item.side else "other"
        expected_sides[key] += 1
    if parsed_sides != expected_sides:
        raise MoomooOpenApiExportError("summary.activity_sides do not match parsed observations")

    activity_range = _require_mapping(
        summary["activity_time_range"], "summary.activity_time_range"
    )
    _validate_fields(activity_range, _TIME_RANGE_FIELDS, "summary.activity_time_range")
    activity_times = [
        item.filled_at if isinstance(item, OpenApiFillObservation) else item.ordered_at
        for item in activity
    ]
    expected_first = min(activity_times) if activity_times else None
    expected_last = max(activity_times) if activity_times else None
    for name, expected in (("first", expected_first), ("last", expected_last)):
        actual_value = activity_range[name]
        if expected is None:
            if actual_value is not None:
                raise MoomooOpenApiExportError(
                    "summary.activity_time_range conflicts with parsed observations"
                )
        elif _parse_source_time(
            actual_value, f"summary.activity_time_range.{name}", zone
        ) != expected:
            raise MoomooOpenApiExportError(
                "summary.activity_time_range conflicts with parsed observations"
            )

    fee_totals = _require_mapping(
        summary["fee_totals_by_currency"], "summary.fee_totals_by_currency"
    )
    orders_by_id = {order.source_order_id: order for order in orders}
    expected_fee_totals: dict[str, Decimal] = {}
    for fee in fees:
        currency = orders_by_id[fee.source_order_id].currency
        expected_fee_totals[currency] = (
            expected_fee_totals.get(currency, Decimal("0")) + fee.total_fee
        )
    parsed_fee_totals: dict[str, Decimal] = {}
    for currency, amount in fee_totals.items():
        normalized_currency = _require_text(
            currency, "summary.fee_totals_by_currency key"
        ).upper()
        parsed_amount = _decimal(
            amount, f"summary.fee_totals_by_currency.{normalized_currency}"
        )
        assert parsed_amount is not None
        parsed_fee_totals[normalized_currency] = parsed_amount
    if set(parsed_fee_totals) != set(expected_fee_totals) or any(
        abs(parsed_fee_totals[currency] - expected_fee_totals[currency])
        > _FEE_AMOUNT_TOLERANCE
        for currency in expected_fee_totals
    ):
        raise MoomooOpenApiExportError(
            "summary.fee_totals_by_currency do not match fee observations"
        )

    deduplicated = _require_mapping(
        summary["deduplicated_rows"], "summary.deduplicated_rows"
    )
    _validate_fields(
        deduplicated,
        _DEDUPLICATION_FIELDS,
        "summary.deduplicated_rows",
        optional=_OPTIONAL_DEDUPLICATION_FIELDS,
    )
    for name in _DEDUPLICATION_FIELDS | _OPTIONAL_DEDUPLICATION_FIELDS:
        if name not in deduplicated:
            continue
        _nonnegative_int(deduplicated[name], f"summary.deduplicated_rows.{name}")

    summary_reconciliation = _require_mapping(
        summary["reconciliation"], "summary.reconciliation"
    )
    _validate_fields(
        summary_reconciliation,
        _RECONCILIATION_FIELDS,
        "summary.reconciliation",
        optional=_OPTIONAL_RECONCILIATION_FIELDS,
    )
    if environment == "LIVE":
        parsed_reconciliation = {
            name: _nonnegative_int(
                summary_reconciliation[name], f"summary.reconciliation.{name}"
            )
            for name in (_RECONCILIATION_FIELDS | _OPTIONAL_RECONCILIATION_FIELDS)
            if name in summary_reconciliation
        }
        observed_reconciliation = reconciliation.as_dict()
        if any(
            parsed_reconciliation[name] != observed_reconciliation[name]
            for name in parsed_reconciliation
        ) or (
            (observed_reconciliation.get("unsupported_combo_orders") or 0) > 0
            and "unsupported_combo_orders" not in parsed_reconciliation
        ):
            raise MoomooOpenApiExportError(
                "summary.reconciliation does not match parsed observations"
            )
        failures = {
            name: value
            for name, value in observed_reconciliation.items()
            if name != "unsupported_combo_orders" and value not in (None, 0)
        }
        expected_status = "passed" if not failures else "failed"
        coverage_fields_present = any(
            name in summary
            for name in {"retrieval_complete", "coverage_complete", "has_activity"}
        )
        expected_ready = (
            not failures if coverage_fields_present else bool(filled_orders) and not failures
        )
        expected_warnings = [
            f"{name}={value}"
            for name, value in sorted(failures.items())
        ]
        execution_group_count = int(
            observed_reconciliation.get("unsupported_combo_orders") or 0
        )
        combo_codes = {
            leg.raw_symbol
            for order in orders
            for leg in order.combo_legs
        }
        spec_codes = {item.raw_symbol for item in contract_specs}
        if not combo_codes:
            expected_contract_spec_status = "not_applicable"
        elif combo_codes <= spec_codes:
            expected_contract_spec_status = "complete"
        elif spec_codes:
            expected_contract_spec_status = "partial"
        else:
            expected_contract_spec_status = "unavailable"
        contract_spec_status = str(
            summary.get(
                "contract_spec_status",
                "not_available" if execution_group_count else "not_applicable",
            )
        ).strip().lower()
        allowed_contract_statuses = {
            expected_contract_spec_status,
            "not_available",
        }
        if contract_spec_status not in allowed_contract_statuses:
            raise MoomooOpenApiExportError(
                "summary.contract_spec_status conflicts with parsed specifications"
            )
        if execution_group_count:
            expected_warnings.append(
                f"execution_group_observations={execution_group_count}"
            )
            if contract_spec_status not in {"complete", "not_available"}:
                expected_warnings.append(
                    f"execution_group_contract_specs_{contract_spec_status}"
                )
        if not filled_orders:
            expected_warnings.append("no_filled_orders_in_window")
        expected_fee_batches = (
            math.ceil(len(filled_orders) / _MAX_FEE_BATCH_SIZE)
            if filled_orders
            else 0
        )
    else:
        if any(value is not None for value in summary_reconciliation.values()):
            raise MoomooOpenApiExportError(
                "SIMULATE reconciliation values must be null"
            )
        expected_status = "not_applicable"
        expected_ready = False
        expected_warnings = [
            "simulate_history_has_no_live_fill_or_fee_reconciliation"
        ]
        contract_spec_status = str(
            summary.get("contract_spec_status", "not_available")
        ).strip().lower()
        expected_fee_batches = 0

    analysis_ready = _required_bool(summary["analysis_ready"], "summary.analysis_ready")
    retrieval_complete = _required_bool(
        summary.get("retrieval_complete", True),
        "summary.retrieval_complete",
    )
    coverage_complete = _required_bool(
        summary.get("coverage_complete", retrieval_complete),
        "summary.coverage_complete",
    )
    has_activity = _required_bool(
        summary.get("has_activity", bool(activity)),
        "summary.has_activity",
    )
    if has_activity != bool(activity):
        raise MoomooOpenApiExportError(
            "summary.has_activity does not match parsed observations"
        )
    if analysis_ready and (not retrieval_complete or not coverage_complete):
        raise MoomooOpenApiExportError(
            "analysis-ready export must prove complete retrieval coverage"
        )
    reconciliation_status = _require_text(
        summary["reconciliation_status"], "summary.reconciliation_status"
    )
    warnings_value = summary["warnings"]
    if not isinstance(warnings_value, (list, tuple)):
        raise MoomooOpenApiExportError("summary.warnings must be an array")
    warnings = tuple(
        _require_text(item, f"summary.warnings[{index}]")
        for index, item in enumerate(warnings_value)
    )
    fee_batches = _nonnegative_int(summary["fee_batches"], "summary.fee_batches")
    legacy_combo_count = int(
        reconciliation.unsupported_combo_orders or 0
    ) if environment == "LIVE" else 0
    legacy_combo_warnings = [
        *[
            item
            for item in expected_warnings
            if not item.startswith("execution_group_")
        ],
        *(
            [f"combo_order_requires_group_projection={legacy_combo_count}"]
            if legacy_combo_count
            else []
        ),
    ]
    legacy_combo_shape = (
        legacy_combo_count > 0
        and expected_ready is True
        and analysis_ready is False
        and reconciliation_status == "failed"
        and list(warnings) == legacy_combo_warnings
    )
    current_shape = (
        analysis_ready == expected_ready
        and reconciliation_status == expected_status
        and list(warnings) == expected_warnings
    )
    if (not current_shape and not legacy_combo_shape) or fee_batches != expected_fee_batches:
        raise MoomooOpenApiExportError(
            "summary readiness facts do not match parsed observations"
        )
    return (
        expected_ready,
        expected_status,
        tuple(expected_warnings),
        retrieval_complete,
        coverage_complete,
        has_activity,
        contract_spec_status,
    )


def parse_openapi_export(payload: Mapping[str, Any]) -> OpenApiExportPreview:
    """Parse one ``readonly-export.v1`` payload without any external writes.

    The returned observations use stable broker order/deal IDs and aware
    source-market timestamps.  ``source_sha256`` fingerprints the complete
    normalized export, including ``generated_at``; ``evidence_sha256`` omits
    retrieval time and summary duplication so callers can compare equivalent
    evidence across independent read-only acquisitions.
    """
    payload = _require_mapping(payload, "payload")
    _validate_fields(payload, _TOP_LEVEL_FIELDS, "payload")
    if payload["schema"] != READONLY_EXPORT_SCHEMA:
        raise MoomooOpenApiExportError("unsupported read-only export schema")
    if payload["mode"] != "read_only":
        raise MoomooOpenApiExportError("export mode must be read_only")
    if payload["journal_database_written"] is not False:
        raise MoomooOpenApiExportError(
            "export does not prove that the Journal database stayed untouched"
        )

    generated_at = _parse_aware_iso(payload["generated_at"], "generated_at")
    account = _require_mapping(payload["account"], "account")
    _validate_fields(
        account,
        _ACCOUNT_FIELDS,
        "account",
        optional=_OPTIONAL_ACCOUNT_FIELDS,
    )
    environment = _enum_text(account["environment"], "account.environment")
    if environment not in {"LIVE", "SIMULATE"}:
        raise MoomooOpenApiExportError("account.environment is unsupported")
    market = _enum_text(account["market"], "account.market")
    if market not in _MARKET_TIMEZONES:
        raise MoomooOpenApiExportError("account.market is unsupported")
    selection = _require_text(account["selection"], "account.selection")
    if selection not in {"explicit", "unique_auto"}:
        raise MoomooOpenApiExportError("account.selection is unsupported")
    account_binding = None
    if "binding" in account:
        account_binding = _require_text(account["binding"], "account.binding").lower()
        if len(account_binding) != 64 or any(
            character not in "0123456789abcdef" for character in account_binding
        ):
            raise MoomooOpenApiExportError(
                "account.binding must be a lowercase SHA-256 HMAC"
            )

    window = _require_mapping(payload["window"], "window")
    _validate_fields(window, _WINDOW_FIELDS, "window")
    source_timezone = _require_text(window["timezone"], "window.timezone")
    if source_timezone != _MARKET_TIMEZONES[market]:
        raise MoomooOpenApiExportError("window.timezone conflicts with account.market")
    try:
        zone = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError:
        raise MoomooOpenApiExportError("window.timezone is unsupported") from None
    window_start_raw = _parse_aware_iso(window["start"], "window.start")
    window_end_raw = _parse_aware_iso(window["end"], "window.end")
    window_start = window_start_raw.astimezone(zone)
    window_end = window_end_raw.astimezone(zone)
    if (
        window_start_raw.utcoffset() != window_start.utcoffset()
        or window_end_raw.utcoffset() != window_end.utcoffset()
    ):
        raise MoomooOpenApiExportError(
            "window offsets do not match the declared source timezone"
        )
    if window_start >= window_end:
        raise MoomooOpenApiExportError("window.start must be earlier than window.end")
    window_chunks = _nonnegative_int(window["chunks"], "window.chunks")
    if window_chunks <= 0:
        raise MoomooOpenApiExportError("window.chunks must be greater than zero")
    max_chunk_days = _nonnegative_int(
        window["max_chunk_days"], "window.max_chunk_days"
    )
    if max_chunk_days != 7:
        raise MoomooOpenApiExportError("window.max_chunk_days is unsupported")

    records = _require_mapping(payload["records"], "records")
    _validate_fields(
        records,
        _RECORD_CONTAINER_FIELDS,
        "records",
        optional=_OPTIONAL_RECORD_CONTAINER_FIELDS,
    )
    order_rows = _require_rows(records["orders"], "records.orders")
    deal_rows = _require_rows(records["deals"], "records.deals")
    fee_rows = _require_rows(records["fees"], "records.fees")
    contract_spec_rows = _require_rows(
        records.get("contract_specs", []),
        "records.contract_specs",
    )

    orders = tuple(
        _parse_order(
            row,
            index,
            zone=zone,
            market=market,
            window_start=window_start,
            window_end=window_end,
        )
        for index, row in enumerate(order_rows)
    )
    _ensure_unique([order.source_order_id for order in orders], "order")
    orders_by_id = {order.source_order_id: order for order in orders}
    fills = tuple(
        _parse_fill(
            row,
            index,
            zone=zone,
            market=market,
            window_start=window_start,
            window_end=window_end,
            orders_by_id=orders_by_id,
        )
        for index, row in enumerate(deal_rows)
    )
    _ensure_unique([fill.source_deal_id for fill in fills], "deal")
    parent_time_skew_count = sum(
        fill.filled_at < orders_by_id[fill.source_order_id].ordered_at
        for fill in fills
    )
    fees = tuple(
        _parse_fee(row, index, orders_by_id=orders_by_id)
        for index, row in enumerate(fee_rows)
    )
    _ensure_unique([fee.source_order_id for fee in fees], "fee order")
    contract_specs = tuple(
        _parse_contract_spec(row, index)
        for index, row in enumerate(contract_spec_rows)
    )
    _ensure_unique(
        [item.raw_symbol for item in contract_specs],
        "contract specification",
    )
    if environment == "SIMULATE" and (fills or fees):
        raise MoomooOpenApiExportError(
            "SIMULATE exports cannot contain live fill or fee observations"
        )

    ordered_orders = tuple(
        sorted(orders, key=lambda item: (item.ordered_at, item.source_order_id))
    )
    ordered_fills = tuple(
        sorted(fills, key=lambda item: (item.filled_at, item.source_deal_id))
    )
    ordered_fees = tuple(sorted(fees, key=lambda item: item.source_order_id))
    ordered_contract_specs = tuple(
        sorted(contract_specs, key=lambda item: item.raw_symbol)
    )
    observed_reconciliation = _reconcile(
        ordered_orders, ordered_fills, ordered_fees
    )
    reconciliation = (
        observed_reconciliation
        if environment == "LIVE"
        else OpenApiReconciliation(
            filled_orders_without_fills=None,
            fills_without_orders=None,
            filled_quantity_mismatches=None,
            fill_code_mismatches=None,
            fill_side_mismatches=None,
            fill_average_price_mismatches=None,
            filled_orders_without_fees=None,
        )
    )
    summary = _require_mapping(payload["summary"], "summary")
    (
        analysis_ready,
        reconciliation_status,
        warnings,
        retrieval_complete,
        coverage_complete,
        has_activity,
        contract_spec_status,
    ) = _validate_summary(
        summary,
        account=account,
        window=window,
        environment=environment,
        market=market,
        orders=ordered_orders,
        fills=ordered_fills,
        fees=ordered_fees,
        contract_specs=ordered_contract_specs,
        reconciliation=observed_reconciliation,
        zone=zone,
    )
    if parent_time_skew_count:
        warnings = (
            *warnings,
            f"{_BROKER_PARENT_TIME_SKEW_WARNING}={parent_time_skew_count}",
        )

    evidence_projection = {
        "schema": READONLY_EXPORT_SCHEMA,
        "mode": "read_only",
        "account": {
            "environment": environment,
            "market": market,
            "selection": selection,
            **({"binding": account_binding} if account_binding else {}),
        },
        "window": {
            "start": window_start,
            "end": window_end,
            "timezone": source_timezone,
        },
        "orders": [_order_projection(order) for order in ordered_orders],
        "fills": [_fill_projection(fill) for fill in ordered_fills],
        "fees": [_fee_projection(fee) for fee in ordered_fees],
        "contract_specs": [
            _contract_spec_projection(item)
            for item in ordered_contract_specs
        ],
    }
    evidence_sha256 = _sha256_json(evidence_projection)
    normalized_source = {
        **evidence_projection,
        "generated_at": generated_at.astimezone(timezone.utc),
        "window": {
            **evidence_projection["window"],
            "chunks": window_chunks,
            "max_chunk_days": max_chunk_days,
        },
        "summary": summary,
        "journal_database_written": False,
    }
    source_sha256 = _sha256_json(normalized_source)
    batch_key = _sha256_json(
        {
            "broker": "moomoo",
            "source_schema": READONLY_EXPORT_SCHEMA,
            "parser_name": OPENAPI_EXPORT_PARSER_NAME,
            "parser_version": OPENAPI_EXPORT_PARSER_VERSION,
            # Retrieval time belongs to source provenance, not economic
            # identity.  A fresh read of identical broker evidence must
            # therefore resolve to the same account-neutral parser batch key.
            "evidence_sha256": evidence_sha256,
        }
    )
    metadata = OpenApiBatchMetadata(
        source_schema=READONLY_EXPORT_SCHEMA,
        source_sha256=source_sha256,
        evidence_sha256=evidence_sha256,
        batch_key=batch_key,
        parser_name=OPENAPI_EXPORT_PARSER_NAME,
        parser_version=OPENAPI_EXPORT_PARSER_VERSION,
        generated_at=generated_at.astimezone(timezone.utc),
        environment=environment,
        market=market,
        account_selection=selection,
        window_start=window_start,
        window_end=window_end,
        source_timezone=source_timezone,
        window_chunks=window_chunks,
        max_chunk_days=max_chunk_days,
        analysis_ready=analysis_ready,
        analysis_level="exact" if analysis_ready else "blocked",
        reconciliation_status=reconciliation_status,
        reconciliation=reconciliation,
        warnings=warnings,
        order_observation_count=len(ordered_orders),
        fill_observation_count=len(ordered_fills),
        fee_observation_count=len(ordered_fees),
        retrieval_complete=retrieval_complete,
        coverage_complete=coverage_complete,
        has_activity=has_activity,
        account_binding=account_binding,
        contract_spec_observation_count=len(ordered_contract_specs),
        contract_spec_status=contract_spec_status,
    )
    return OpenApiExportPreview(
        metadata=metadata,
        orders=ordered_orders,
        fills=ordered_fills,
        fees=ordered_fees,
        contract_specs=ordered_contract_specs,
    )


def parse_readonly_export(payload: Mapping[str, Any]) -> OpenApiExportPreview:
    """Compatibility spelling for callers centered on the probe contract."""
    return parse_openapi_export(payload)
