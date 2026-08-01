# -*- coding: utf-8 -*-
"""DB-aware planning and atomic confirmation for Moomoo OpenAPI evidence.

The strict parser proves that one de-identified export is internally sound.
This module answers the separate question that matters to the journal: how
that bounded API window relates to the newest accepted CSV baseline.  Planning
is read-only.  Confirmation is bound to the exact plan key and delegates one
atomic append to the ledger repository.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError

from src.journal.brokers.moomoo_openapi_export import OpenApiExportPreview
from src.journal.brokers.moomoo_statement import (
    StatementFill,
    StatementOrder,
    StatementParseResult,
    StatementReconciliation,
    reconcile_statement_with_readonly_export,
)
from src.journal.ledger.canonical import (
    CANONICAL_READER_NAME,
    CANONICAL_READER_VERSION,
    CanonicalEvidenceSet,
    ExecutionGroupLegObservationInput,
    ExecutionGroupObservationInput,
    FillObservationInput,
    OrderObservationInput,
    canonicalize_observations,
)
from src.journal.ledger.identity import (
    IDENTITY_PROJECTION_NAME,
    IDENTITY_PROJECTION_VERSION,
    AcceptedCsvBatchSelection,
    IdentityProjection,
    IdentityProjectionError,
    project_reconciled_identities,
)
from src.journal.ledger.models import (
    BrokerFillObservation,
    BrokerOrderObservation,
    CanonicalEvidenceSetRecord,
    DealIdentityLink,
    ImportBatch,
    OrderFillSetAttestation,
    OrderIdentityLink,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    LedgerImportError,
    load_canonical_observation_inputs,
)
from src.options.occ_parser import parse_symbol
from src.storage import get_db

__all__ = [
    "OpenApiCanonicalImpact",
    "OpenApiConfirmResult",
    "OpenApiImportPlan",
    "OpenApiPlanError",
    "OpenApiPlanIssue",
    "confirm_openapi_import_plan",
    "plan_openapi_import",
]


_MULTIPLIER_TOLERANCE = Decimal("0.005")
_CANDIDATE_MULTIPLIERS = (Decimal("1"), Decimal("100"))
_QUANTITY_TOLERANCE = Decimal("0.000001")
_EXECUTION_GROUP_PROJECTION_NAME = "moomoo_openapi_execution_group_projection"
_EXECUTION_GROUP_PROJECTION_VERSION = "1"


class OpenApiPlanError(ValueError):
    """Raised when a stale, blocked, or unacknowledged plan is confirmed."""


@dataclass(frozen=True)
class OpenApiPlanIssue:
    code: str
    severity: str
    entity_kind: str
    entity_key: str
    message: str
    field_name: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "entity_kind": self.entity_kind,
            "entity_key": self.entity_key,
            "field_name": self.field_name,
            "message": self.message,
        }


@dataclass(frozen=True)
class OpenApiCanonicalImpact:
    input_order_observations: int
    input_fill_observations: int
    canonical_orders: int
    canonical_fills: int
    duplicate_order_observations: int
    duplicate_fill_observations: int
    shadowed_csv_fills: int
    shadowed_aggregate_orders: int
    blocking_issues: int
    analysis_ready: bool
    canonical_set_sha256: str
    input_execution_group_observations: int = 0
    canonical_execution_groups: int = 0
    canonical_execution_group_legs: int = 0
    duplicate_execution_group_observations: int = 0

    @classmethod
    def from_evidence_set(cls, value: CanonicalEvidenceSet) -> "OpenApiCanonicalImpact":
        provenance = value.provenance
        return cls(
            input_order_observations=provenance.input_order_observation_count,
            input_fill_observations=provenance.input_fill_observation_count,
            canonical_orders=provenance.canonical_order_count,
            canonical_fills=provenance.canonical_fill_count,
            duplicate_order_observations=provenance.duplicate_order_observation_count,
            duplicate_fill_observations=provenance.duplicate_fill_observation_count,
            shadowed_csv_fills=provenance.shadowed_fill_observation_count,
            shadowed_aggregate_orders=provenance.shadowed_aggregate_order_count,
            blocking_issues=provenance.blocking_issue_count,
            analysis_ready=value.analysis_ready,
            canonical_set_sha256=value.canonical_set_sha256,
            input_execution_group_observations=(
                getattr(
                    provenance,
                    "input_execution_group_observation_count",
                    0,
                )
            ),
            canonical_execution_groups=(
                getattr(provenance, "canonical_execution_group_count", 0)
            ),
            canonical_execution_group_legs=(
                getattr(
                    provenance,
                    "canonical_execution_group_leg_count",
                    0,
                )
            ),
            duplicate_execution_group_observations=(
                getattr(
                    provenance,
                    "duplicate_execution_group_observation_count",
                    0,
                )
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_order_observations": self.input_order_observations,
            "input_fill_observations": self.input_fill_observations,
            "canonical_orders": self.canonical_orders,
            "canonical_fills": self.canonical_fills,
            "duplicate_order_observations": self.duplicate_order_observations,
            "duplicate_fill_observations": self.duplicate_fill_observations,
            "shadowed_csv_fills": self.shadowed_csv_fills,
            "shadowed_aggregate_orders": self.shadowed_aggregate_orders,
            "blocking_issues": self.blocking_issues,
            "analysis_ready": self.analysis_ready,
            "canonical_set_sha256": self.canonical_set_sha256,
            "input_execution_group_observations": (
                self.input_execution_group_observations
            ),
            "canonical_execution_groups": self.canonical_execution_groups,
            "canonical_execution_group_legs": (
                self.canonical_execution_group_legs
            ),
            "duplicate_execution_group_observations": (
                self.duplicate_execution_group_observations
            ),
        }


@dataclass(frozen=True)
class OpenApiImportPlan:
    preview_key: str
    confirm_allowed: bool
    source_scope: Mapping[str, Any]
    base_scope: Optional[Mapping[str, Any]]
    coverage: Mapping[str, Any]
    write_plan: Mapping[str, Any]
    canonical_impact: OpenApiCanonicalImpact
    issues: tuple[OpenApiPlanIssue, ...]
    warnings: tuple[str, ...]
    scope_is_full_batch: bool
    source_cutoff_at: datetime
    reconciliation: Optional[StatementReconciliation]
    identity_projection: Optional[IdentityProjection]
    canonical_evidence_set: Optional[CanonicalEvidenceSet]
    journal_database_written: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "preview_key": self.preview_key,
            "confirm_allowed": self.confirm_allowed,
            "source_scope": dict(self.source_scope),
            "base_scope": None if self.base_scope is None else dict(self.base_scope),
            "coverage": dict(self.coverage),
            "write_plan": dict(self.write_plan),
            "canonical_impact": self.canonical_impact.as_dict(),
            "issues": [item.as_dict() for item in self.issues],
            "warnings": list(self.warnings),
            "scope_is_full_batch": self.scope_is_full_batch,
            "journal_database_written": False,
        }


@dataclass(frozen=True)
class OpenApiConfirmResult:
    status: str
    duplicate: bool
    import_batch_id: int
    canonical_set_id: int
    canonical_set_sha256: str
    scope: str
    appended: Mapping[str, int]
    canonical: Mapping[str, Any]
    message: str
    legacy_journal_written: bool = False
    episode_build_triggered: bool = False
    trading_action_performed: bool = False


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _utc(value: Optional[datetime], *, field_name: str) -> datetime:
    if value is None:
        raise OpenApiPlanError(f"{field_name} is required")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_decimal(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(value)


def _parse_json_object(value: Optional[str]) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalized_symbol(value: str) -> str:
    text = str(value).strip().upper()
    if "." in text:
        prefix, remainder = text.split(".", 1)
        if prefix in {"US", "HK", "SH", "SZ"} and remainder:
            return remainder
    return text


def _instrument_fields(raw_symbol: str) -> dict[str, Any]:
    instrument = parse_symbol(_normalized_symbol(raw_symbol))
    if instrument.is_option and instrument.option is not None:
        return {
            "raw_symbol": instrument.raw_symbol,
            "asset_type": "option",
            "underlying": instrument.underlying,
            "expiry": instrument.option.expiry,
            "strike": Decimal(str(instrument.option.strike)),
            "option_right": instrument.option.right,
        }
    return {
        "raw_symbol": instrument.raw_symbol,
        "asset_type": "equity",
        "underlying": instrument.underlying,
        "expiry": None,
        "strike": None,
        "option_right": None,
    }


def _instrument_key(value: Any) -> tuple[Any, ...]:
    return (
        str(value.asset_type).strip().lower(),
        str(value.underlying).strip().upper(),
        value.expiry,
        _optional_decimal(value.strike),
        None if value.option_right is None else str(value.option_right).strip().upper(),
        str(value.currency).strip().upper(),
    )


def _derived_multiplier(
    *, amount: Any, quantity: Any, price: Any
) -> Optional[Decimal]:
    amount_value = _optional_decimal(amount)
    quantity_value = _optional_decimal(quantity)
    price_value = _optional_decimal(price)
    if (
        amount_value is None
        or quantity_value is None
        or price_value is None
        or quantity_value <= 0
        or price_value <= 0
    ):
        return None
    denominator = quantity_value * price_value
    observed = abs(amount_value)
    candidate = min(
        _CANDIDATE_MULTIPLIERS,
        key=lambda item: abs(observed - denominator * item),
    )
    if abs(observed - denominator * candidate) > _MULTIPLIER_TOLERANCE:
        return None
    return candidate


def _multiplier_map(
    orders: Sequence[BrokerOrderObservation],
    fills: Sequence[BrokerFillObservation],
) -> dict[tuple[Any, ...], Decimal]:
    values: dict[tuple[Any, ...], set[Decimal]] = {}
    for row in fills:
        multiplier = _derived_multiplier(
            amount=row.amount,
            quantity=row.quantity,
            price=row.price,
        )
        if multiplier is not None:
            values.setdefault(_instrument_key(row), set()).add(multiplier)
    for row in orders:
        multiplier = _derived_multiplier(
            amount=row.order_amount,
            quantity=row.summary_filled_quantity or row.order_quantity,
            price=row.summary_average_fill_price or row.order_price,
        )
        if multiplier is not None:
            values.setdefault(_instrument_key(row), set()).add(multiplier)
    conflicts = [key for key, candidates in values.items() if len(candidates) > 1]
    if conflicts:
        raise OpenApiPlanError("contract multiplier evidence disagrees")
    return {key: next(iter(candidates)) for key, candidates in values.items()}


def _batch_inputs(
    batch: ImportBatch,
    orders: Sequence[BrokerOrderObservation],
    fills: Sequence[BrokerFillObservation],
) -> tuple[tuple[OrderObservationInput, ...], tuple[FillObservationInput, ...]]:
    multipliers = _multiplier_map(orders, fills)

    def multiplier_fields(row: Any) -> dict[str, Any]:
        multiplier = multipliers.get(_instrument_key(row))
        return {
            "contract_multiplier": multiplier,
            "contract_multiplier_basis": (
                "evidence_derived_from_amount" if multiplier is not None else "unknown"
            ),
        }

    order_inputs = tuple(
        OrderObservationInput(
            observation_id=int(row.id),
            import_batch_id=int(batch.id),
            batch_key=str(batch.batch_key),
            source_kind=str(batch.source_kind),
            observation_key=str(row.observation_key),
            source_record_sha256=str(row.source_record_sha256),
            broker=str(row.broker),
            account_key=str(row.account_key),
            source_order_id=str(row.source_order_id),
            identity_strength=str(row.identity_strength),
            raw_symbol=str(row.raw_symbol),
            asset_type=str(row.asset_type),
            underlying=str(row.underlying),
            side=str(row.side),
            status=str(row.status),
            currency=str(row.currency),
            ordered_at=_utc(row.ordered_at, field_name="ordered_at"),
            order_quantity=Decimal(row.order_quantity),
            evidence_level=str(row.evidence_level),
            fee_evidence_status=str(row.fee_evidence_status),
            recorded_at=_utc(row.recorded_at, field_name="recorded_at"),
            expiry=row.expiry,
            strike=_optional_decimal(row.strike),
            option_right=None if row.option_right is None else str(row.option_right),
            **multiplier_fields(row),
            source_row_number=row.source_row_number,
            source_updated_at=(
                None
                if row.source_updated_at is None
                else _utc(row.source_updated_at, field_name="source_updated_at")
            ),
            order_price=_optional_decimal(row.order_price),
            order_amount=_optional_decimal(row.order_amount),
            summary_filled_quantity=_optional_decimal(row.summary_filled_quantity),
            summary_average_fill_price=_optional_decimal(row.summary_average_fill_price),
            total_fee=_optional_decimal(row.total_fee),
        )
        for row in orders
    )
    fill_inputs = tuple(
        FillObservationInput(
            observation_id=int(row.id),
            import_batch_id=int(batch.id),
            batch_key=str(batch.batch_key),
            source_kind=str(batch.source_kind),
            observation_key=str(row.observation_key),
            source_record_sha256=str(row.source_record_sha256),
            broker=str(row.broker),
            account_key=str(row.account_key),
            source_order_id=None if row.source_order_id is None else str(row.source_order_id),
            source_deal_id=str(row.source_deal_id),
            identity_strength=str(row.identity_strength),
            raw_symbol=str(row.raw_symbol),
            asset_type=str(row.asset_type),
            underlying=str(row.underlying),
            side=str(row.side),
            currency=str(row.currency),
            filled_at=_utc(row.filled_at, field_name="filled_at"),
            quantity=Decimal(row.quantity),
            price=Decimal(row.price),
            recorded_at=_utc(row.recorded_at, field_name="recorded_at"),
            expiry=row.expiry,
            strike=_optional_decimal(row.strike),
            option_right=None if row.option_right is None else str(row.option_right),
            **multiplier_fields(row),
            source_row_number=row.source_row_number,
            amount=_optional_decimal(row.amount),
            total_fee=_optional_decimal(row.total_fee),
        )
        for row in fills
    )
    return order_inputs, fill_inputs


def _statement_from_rows(
    batch: ImportBatch,
    orders: Sequence[BrokerOrderObservation],
    fills: Sequence[BrokerFillObservation],
) -> StatementParseResult:
    fills_by_order: dict[int, list[BrokerFillObservation]] = {}
    for fill in fills:
        if fill.broker_order_observation_id is not None:
            fills_by_order.setdefault(int(fill.broker_order_observation_id), []).append(fill)
    statement_orders: list[StatementOrder] = []
    for row in orders:
        fee_components = _parse_json_object(row.fee_components_json)
        evidence = _parse_json_object(row.evidence_json)
        statement_fills = tuple(
            StatementFill(
                source_row=int(fill.source_row_number or fill.id),
                quantity=Decimal(fill.quantity),
                price=Decimal(fill.price),
                filled_at=_utc(fill.filled_at, field_name="filled_at"),
                amount=_optional_decimal(fill.amount),
                market="US",
                currency=str(fill.currency),
            )
            for fill in sorted(
                fills_by_order.get(int(row.id), []),
                key=lambda item: (item.filled_at, item.id),
            )
        )
        statement_orders.append(
            StatementOrder(
                source_row=int(row.source_row_number or row.id),
                derived_order_id=str(row.source_order_id),
                symbol=_normalized_symbol(str(row.raw_symbol)),
                name="",
                side=str(row.side),
                status=str(row.status),
                order_quantity=Decimal(row.order_quantity),
                order_price=_optional_decimal(row.order_price),
                order_price_text=(
                    "" if row.order_price is None else format(Decimal(row.order_price), "f")
                ),
                order_amount=_optional_decimal(row.order_amount),
                order_time=_utc(row.ordered_at, field_name="ordered_at"),
                order_type=str(row.order_type or ""),
                time_in_force=str(row.time_in_force or ""),
                session=str(row.session or ""),
                market="US",
                currency=str(row.currency),
                summary_filled_quantity=_optional_decimal(row.summary_filled_quantity),
                summary_average_price=_optional_decimal(row.summary_average_fill_price),
                fills=statement_fills,
                fee_components=tuple(
                    sorted((str(name), Decimal(str(amount))) for name, amount in fee_components.items())
                ),
                total_fee=Decimal(row.total_fee or 0),
                evidence_level=str(row.evidence_level),
                evidence_warnings=tuple(str(item) for item in evidence.get("warnings", [])),
            )
        )
    return StatementParseResult(
        source_sha256=str(batch.source_sha256),
        parser_version=str(batch.parser_version),
        rows_total=len(orders) + len(fills),
        headers=(),
        orders=tuple(statement_orders),
        orphan_fill_rows=0,
        warnings=(),
    )


def _storage_batch_key(preview: OpenApiExportPreview, account_key: str) -> str:
    return _sha256_json(
        {
            "account_key": account_key,
            "broker": "moomoo",
            "parser_batch_key": preview.metadata.batch_key,
        }
    )


def _observation_key(kind: str, stable_id: str) -> str:
    return f"moomoo_openapi_{kind}_" + _sha256_json(
        {"kind": kind, "stable_id": stable_id}
    )[:32]


def _is_execution_group_order(value: Any) -> bool:
    """Return whether a broker parent declares a multi-leg execution group."""
    return bool(getattr(value, "combo_legs", ()))


def _execution_group_leg_stable_id(
    source_group_id: str,
    raw_symbol: str,
    side: str,
    quantity_ratio: Decimal,
) -> str:
    return "|".join(
        (
            source_group_id,
            raw_symbol.strip().upper(),
            side.strip().upper(),
            format(quantity_ratio, "f"),
        )
    )


def _execution_group_leg_key(
    source_group_id: str,
    raw_symbol: str,
    side: str,
    quantity_ratio: Decimal,
) -> str:
    return _observation_key(
        "execution_group_leg",
        _execution_group_leg_stable_id(
            source_group_id,
            raw_symbol,
            side,
            quantity_ratio,
        ),
    )


def _execution_group_fill_link_key(
    source_group_id: str,
    source_deal_id: str,
    execution_group_leg_key: str,
) -> str:
    return _observation_key(
        "execution_group_fill_link",
        "|".join(
            (source_group_id, source_deal_id, execution_group_leg_key)
        ),
    )


def _execution_group_fill_link_sha256(
    source_group_id: str,
    source_deal_id: str,
    execution_group_leg_key: str,
) -> str:
    return _sha256_json(
        {
            "source_execution_group_id": source_group_id,
            "source_deal_id": source_deal_id,
            "execution_group_leg_key": execution_group_leg_key,
            "link_method": "broker_parent_id_and_declared_leg_identity",
        }
    )


def _execution_group_leg_sha256(
    *,
    source_group_id: str,
    parent_source_record_sha256: str,
    leg_key: str,
    raw_symbol: str,
    side: str,
    quantity_ratio: Decimal,
    contract_multiplier: Optional[Decimal],
    contract_multiplier_basis: str,
    contract_spec_source_record_sha256: Optional[str],
) -> str:
    return _sha256_json(
        {
            "source_execution_group_id": source_group_id,
            "parent_source_record_sha256": parent_source_record_sha256,
            "leg_key": leg_key,
            "raw_symbol": raw_symbol.strip().upper(),
            "side": side.strip().upper(),
            "quantity_ratio": quantity_ratio,
            "contract_multiplier": contract_multiplier,
            "contract_multiplier_basis": contract_multiplier_basis,
            "contract_spec_source_record_sha256": (
                contract_spec_source_record_sha256
            ),
        }
    )


def _contract_specs_by_symbol(
    preview: OpenApiExportPreview,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for spec in preview.contract_specs:
        key = _normalized_symbol(spec.raw_symbol)
        existing = result.get(key)
        if (
            existing is not None
            and Decimal(existing.resolved_multiplier)
            != Decimal(spec.resolved_multiplier)
        ):
            raise OpenApiPlanError(
                f"contract multiplier evidence disagrees for {key}"
            )
        result[key] = spec
    return result


def _api_inputs(
    preview: OpenApiExportPreview,
    *,
    account_key: str,
    virtual_batch_id: int,
    multiplier_by_instrument: Mapping[tuple[Any, ...], Decimal],
) -> tuple[
    tuple[OrderObservationInput, ...],
    tuple[FillObservationInput, ...],
    tuple[ExecutionGroupObservationInput, ...],
]:
    metadata = preview.metadata
    batch_key = _storage_batch_key(preview, account_key)
    fee_by_order = {item.source_order_id: item for item in preview.fees}
    contract_specs = _contract_specs_by_symbol(preview)
    group_orders = {
        item.source_order_id: item
        for item in preview.orders
        if _is_execution_group_order(item)
    }

    def multiplier_fields(
        *,
        raw_symbol: str,
        instrument: Mapping[str, Any],
        currency: str,
        allow_derived: bool = True,
    ) -> tuple[Optional[Decimal], str, Optional[str]]:
        spec = contract_specs.get(_normalized_symbol(raw_symbol))
        if spec is not None:
            return (
                Decimal(spec.resolved_multiplier),
                "broker_stated",
                str(spec.source_record_sha256),
            )
        if not allow_derived:
            return None, "unknown", None
        lookup = (
            instrument["asset_type"],
            str(instrument["underlying"]).upper(),
            instrument["expiry"],
            instrument["strike"],
            instrument["option_right"],
            currency.upper(),
        )
        multiplier = multiplier_by_instrument.get(lookup)
        return (
            multiplier,
            (
                "evidence_derived_from_amount"
                if multiplier is not None
                else "unknown"
            ),
            None,
        )

    order_inputs: list[OrderObservationInput] = []
    for index, row in enumerate(preview.orders, start=1):
        if (
            row.source_order_id in group_orders
            or not row.combo_definition_available
        ):
            continue
        instrument = _instrument_fields(row.raw_symbol)
        multiplier, multiplier_basis, _ = multiplier_fields(
            raw_symbol=row.raw_symbol,
            instrument=instrument,
            currency=row.currency,
        )
        fee = fee_by_order.get(row.source_order_id)
        order_inputs.append(
            OrderObservationInput(
                observation_id=2_000_000_000 + index,
                import_batch_id=virtual_batch_id,
                batch_key=batch_key,
                source_kind="openapi",
                observation_key=_observation_key("order", row.source_order_id),
                source_record_sha256=row.source_record_sha256,
                broker="moomoo",
                account_key=account_key,
                source_order_id=row.source_order_id,
                identity_strength="broker_stable",
                **instrument,
                side=row.side,
                status=row.status,
                currency=row.currency,
                ordered_at=_utc(row.ordered_at, field_name="ordered_at"),
                order_quantity=row.order_quantity,
                evidence_level=(
                    "fill_detail" if row.summary_filled_quantity > 0 else "not_filled"
                ),
                fee_evidence_status=("complete" if fee is not None else "not_applicable"),
                recorded_at=_utc(metadata.generated_at, field_name="generated_at"),
                contract_multiplier=multiplier,
                contract_multiplier_basis=multiplier_basis,
                source_updated_at=_utc(row.source_updated_at, field_name="updated_at"),
                order_price=row.order_price,
                order_amount=None,
                summary_filled_quantity=row.summary_filled_quantity,
                summary_average_fill_price=row.summary_average_fill_price,
                total_fee=None if fee is None else fee.total_fee,
            )
        )

    group_inputs: list[ExecutionGroupObservationInput] = []
    group_leg_by_identity: dict[tuple[str, str, str], tuple[str, Any]] = {}
    next_leg_id = 5_000_000_000
    for group_index, row in enumerate(
        sorted(group_orders.values(), key=lambda item: item.source_order_id),
        start=1,
    ):
        group_observation_id = 4_000_000_000 + group_index
        legs: list[ExecutionGroupLegObservationInput] = []
        for leg in row.combo_legs:
            next_leg_id += 1
            instrument = _instrument_fields(leg.raw_symbol)
            multiplier, multiplier_basis, spec_sha256 = multiplier_fields(
                raw_symbol=leg.raw_symbol,
                instrument=instrument,
                currency=row.currency,
                allow_derived=False,
            )
            leg_key = _execution_group_leg_key(
                row.source_order_id,
                leg.raw_symbol,
                leg.side,
                leg.quantity_ratio,
            )
            leg_input = ExecutionGroupLegObservationInput(
                observation_id=next_leg_id,
                import_batch_id=virtual_batch_id,
                batch_key=batch_key,
                source_kind="openapi",
                observation_key=leg_key,
                source_record_sha256=_execution_group_leg_sha256(
                    source_group_id=row.source_order_id,
                    parent_source_record_sha256=row.source_record_sha256,
                    leg_key=leg_key,
                    raw_symbol=leg.raw_symbol,
                    side=leg.side,
                    quantity_ratio=leg.quantity_ratio,
                    contract_multiplier=multiplier,
                    contract_multiplier_basis=multiplier_basis,
                    contract_spec_source_record_sha256=spec_sha256,
                ),
                broker="moomoo",
                account_key=account_key,
                group_observation_id=group_observation_id,
                leg_key=leg_key,
                **instrument,
                side=leg.side,
                currency=row.currency,
                quantity_ratio=leg.quantity_ratio,
                recorded_at=_utc(
                    metadata.generated_at,
                    field_name="generated_at",
                ),
                contract_multiplier=multiplier,
                contract_multiplier_basis=multiplier_basis,
                source_updated_at=_utc(
                    row.source_updated_at,
                    field_name="updated_at",
                ),
            )
            legs.append(leg_input)
            group_leg_by_identity[
                (
                    row.source_order_id,
                    _normalized_symbol(leg.raw_symbol),
                    leg.side.strip().upper(),
                )
            ] = (leg_key, leg_input)
        fee = fee_by_order.get(row.source_order_id)
        group_inputs.append(
            ExecutionGroupObservationInput(
                observation_id=group_observation_id,
                import_batch_id=virtual_batch_id,
                batch_key=batch_key,
                source_kind="openapi",
                observation_key=_observation_key(
                    "execution_group",
                    row.source_order_id,
                ),
                source_record_sha256=row.source_record_sha256,
                broker="moomoo",
                account_key=account_key,
                source_group_id=row.source_order_id,
                identity_strength="broker_stable",
                environment=metadata.environment,
                raw_group_symbol=row.raw_symbol,
                group_side=row.side,
                strategy_type=row.strategy_type or "UNKNOWN",
                status=row.status,
                currency=row.currency,
                ordered_at=_utc(row.ordered_at, field_name="ordered_at"),
                source_updated_at=_utc(
                    row.source_updated_at,
                    field_name="updated_at",
                ),
                package_quantity=row.order_quantity,
                filled_package_quantity=row.summary_filled_quantity,
                order_net_price=row.order_price,
                average_net_price=row.summary_average_fill_price,
                total_fee=None if fee is None else fee.total_fee,
                fee_evidence_status=(
                    "complete" if fee is not None else "missing"
                ),
                recorded_at=_utc(
                    metadata.generated_at,
                    field_name="generated_at",
                ),
                legs=tuple(legs),
                fee_components=(
                    () if fee is None else tuple(fee.fee_components)
                ),
                fee_observation_id=(
                    None if fee is None else 7_000_000_000 + group_index
                ),
                fee_observation_key=(
                    None
                    if fee is None
                    else _observation_key(
                        "execution_group_fee",
                        row.source_order_id,
                    )
                ),
                fee_source_record_sha256=(
                    None if fee is None else fee.source_record_sha256
                ),
            )
        )

    fill_inputs: list[FillObservationInput] = []
    for index, row in enumerate(preview.fills, start=1):
        instrument = _instrument_fields(row.raw_symbol)
        multiplier, multiplier_basis, _ = multiplier_fields(
            raw_symbol=row.raw_symbol,
            instrument=instrument,
            currency=row.currency,
            allow_derived=row.source_order_id not in group_orders,
        )
        group_leg = group_leg_by_identity.get(
            (
                row.source_order_id,
                _normalized_symbol(row.raw_symbol),
                row.side.strip().upper(),
            )
        )
        fill_link_key = (
            None
            if group_leg is None
            else _execution_group_fill_link_key(
                row.source_order_id,
                row.source_deal_id,
                group_leg[0],
            )
        )
        fill_inputs.append(
            FillObservationInput(
                observation_id=3_000_000_000 + index,
                import_batch_id=virtual_batch_id,
                batch_key=batch_key,
                source_kind="openapi",
                observation_key=_observation_key("fill", row.source_deal_id),
                source_record_sha256=row.source_record_sha256,
                broker="moomoo",
                account_key=account_key,
                source_order_id=row.source_order_id,
                source_deal_id=row.source_deal_id,
                identity_strength="broker_stable",
                **instrument,
                side=row.side,
                currency=row.currency,
                filled_at=_utc(row.filled_at, field_name="filled_at"),
                quantity=row.quantity,
                price=row.price,
                recorded_at=_utc(metadata.generated_at, field_name="generated_at"),
                contract_multiplier=multiplier,
                contract_multiplier_basis=multiplier_basis,
                amount=None,
                total_fee=None,
                execution_group_id=(
                    row.source_order_id
                    if row.source_order_id in group_orders
                    and group_leg is not None
                    else None
                ),
                execution_group_leg_key=(
                    None if group_leg is None else group_leg[0]
                ),
                execution_group_fill_link_id=(
                    None if group_leg is None else 6_000_000_000 + index
                ),
                execution_group_fill_link_key=fill_link_key,
                execution_group_fill_link_sha256=(
                    None
                    if fill_link_key is None
                    else _execution_group_fill_link_sha256(
                        row.source_order_id,
                        row.source_deal_id,
                        group_leg[0],
                    )
                ),
            )
        )
    return tuple(order_inputs), tuple(fill_inputs), tuple(group_inputs)


def _empty_impact(preview: OpenApiExportPreview) -> OpenApiCanonicalImpact:
    group_orders = tuple(
        item for item in preview.orders if _is_execution_group_order(item)
    )
    ordinary_orders = tuple(
        item
        for item in preview.orders
        if item.combo_definition_available
        and not _is_execution_group_order(item)
    )
    return OpenApiCanonicalImpact(
        input_order_observations=len(ordinary_orders),
        input_fill_observations=len(preview.fills),
        canonical_orders=0,
        canonical_fills=0,
        duplicate_order_observations=0,
        duplicate_fill_observations=0,
        shadowed_csv_fills=0,
        shadowed_aggregate_orders=0,
        blocking_issues=1,
        analysis_ready=False,
        canonical_set_sha256="",
        input_execution_group_observations=len(group_orders),
    )


def _execution_group_source_counts(
    preview: OpenApiExportPreview,
) -> dict[str, int]:
    group_orders = tuple(
        item for item in preview.orders if _is_execution_group_order(item)
    )
    ordinary_order_count = sum(
        item.combo_definition_available
        and not _is_execution_group_order(item)
        for item in preview.orders
    )
    group_ids = {item.source_order_id for item in group_orders}
    return {
        "ordinary_order_observations": ordinary_order_count,
        "unclassified_parent_observations": (
            len(preview.orders) - ordinary_order_count - len(group_orders)
        ),
        "execution_group_observations": len(group_orders),
        "execution_group_leg_observations": sum(
            len(item.combo_legs) for item in group_orders
        ),
        "execution_group_fill_links": sum(
            item.source_order_id in group_ids for item in preview.fills
        ),
        "execution_group_fee_observations": sum(
            item.source_order_id in group_ids for item in preview.fees
        ),
    }


def _empty_write_plan() -> dict[str, Any]:
    return {
        "already_imported": False,
        "order_observations": 0,
        "ordinary_order_observations": 0,
        "unclassified_parent_observations": 0,
        "fill_observations": 0,
        "fee_observations": 0,
        "execution_group_observations": 0,
        "execution_group_leg_observations": 0,
        "execution_group_fill_links": 0,
        "execution_group_fee_observations": 0,
        "order_identity_links": 0,
        "deal_identity_links": 0,
        "fill_set_attestations": 0,
        "canonical_sets": 0,
    }


def _execution_group_plan_issues(
    preview: OpenApiExportPreview,
    *,
    base_start: datetime,
    base_end: datetime,
) -> tuple[OpenApiPlanIssue, ...]:
    """Prove the narrow execution-group shape allowed by the first release."""
    issues: list[OpenApiPlanIssue] = []
    fee_by_order = {item.source_order_id: item for item in preview.fees}
    fills_by_order: dict[str, list[Any]] = {}
    for fill in preview.fills:
        fills_by_order.setdefault(fill.source_order_id, []).append(fill)
    contract_specs = _contract_specs_by_symbol(preview)

    for row in preview.orders:
        if not row.combo_definition_available:
            issues.append(
                OpenApiPlanIssue(
                    code="combo_definition_capability_unproved",
                    severity="blocking",
                    entity_kind="source_capability",
                    entity_key=_observation_key(
                        "combo_capability",
                        row.source_order_id,
                    ),
                    message=(
                        "The source row does not prove that strategy_type and "
                        "combo_legs were exposed by OpenD."
                    ),
                    field_name="combo_legs",
                )
            )
        if not _is_execution_group_order(row):
            continue

        group_key = _observation_key(
            "execution_group",
            row.source_order_id,
        )
        ordered_at = _utc(row.ordered_at, field_name="combo.ordered_at")
        if ordered_at < base_start:
            issues.append(
                OpenApiPlanIssue(
                    code="combo_outside_csv_baseline_unprovable",
                    severity="blocking",
                    entity_kind="execution_group",
                    entity_key=group_key,
                    message=(
                        "The execution group predates the accepted CSV baseline; "
                        "cross-source group identity is not proved."
                    ),
                    field_name="ordered_at",
                )
            )
        elif ordered_at <= base_end:
            issues.append(
                OpenApiPlanIssue(
                    code="combo_csv_overlap_unprovable",
                    severity="blocking",
                    entity_kind="execution_group",
                    entity_key=group_key,
                    message=(
                        "The execution group overlaps the CSV baseline; the CSV "
                        "cannot prove that several legs share one broker parent."
                    ),
                    field_name="ordered_at",
                )
            )

        if row.status.strip().upper() != "FILLED_ALL":
            issues.append(
                OpenApiPlanIssue(
                    code="execution_group_partial_semantics_unproved",
                    severity="blocking",
                    entity_kind="execution_group",
                    entity_key=group_key,
                    message=(
                        "Initial execution-group support accepts only terminal "
                        "FILLED_ALL broker evidence."
                    ),
                    field_name="status",
                )
            )
        if (
            abs(row.summary_filled_quantity - row.order_quantity)
            > _QUANTITY_TOLERANCE
        ):
            issues.append(
                OpenApiPlanIssue(
                    code="execution_group_package_quantity_incomplete",
                    severity="blocking",
                    entity_kind="execution_group",
                    entity_key=group_key,
                    message=(
                        "Filled package quantity does not equal the requested "
                        "package quantity."
                    ),
                    field_name="summary_filled_quantity",
                )
            )
        if row.source_order_id not in fee_by_order:
            issues.append(
                OpenApiPlanIssue(
                    code="execution_group_fee_missing",
                    severity="blocking",
                    entity_kind="execution_group",
                    entity_key=group_key,
                    message=(
                        "A completed execution group requires one exact "
                        "group-level broker fee observation."
                    ),
                    field_name="total_fee",
                )
            )

        declared_legs = {
            (
                _normalized_symbol(leg.raw_symbol),
                leg.side.strip().upper(),
            ): leg
            for leg in row.combo_legs
        }
        fill_quantities: dict[tuple[str, str], Decimal] = {}
        unassigned_fill = False
        for fill in fills_by_order.get(row.source_order_id, []):
            identity = (
                _normalized_symbol(fill.raw_symbol),
                fill.side.strip().upper(),
            )
            if identity not in declared_legs:
                unassigned_fill = True
                continue
            fill_quantities[identity] = (
                fill_quantities.get(identity, Decimal("0")) + fill.quantity
            )
        if unassigned_fill:
            issues.append(
                OpenApiPlanIssue(
                    code="execution_group_fill_unassigned",
                    severity="blocking",
                    entity_kind="execution_group",
                    entity_key=group_key,
                    message=(
                        "One or more broker fills do not map to exactly one "
                        "declared execution-group leg."
                    ),
                    field_name="combo_legs",
                )
            )
        for identity, leg in declared_legs.items():
            expected = row.order_quantity * leg.quantity_ratio
            if (
                abs(fill_quantities.get(identity, Decimal("0")) - expected)
                > _QUANTITY_TOLERANCE
            ):
                issues.append(
                    OpenApiPlanIssue(
                        code="execution_group_leg_quantity_mismatch",
                        severity="blocking",
                        entity_kind="execution_group",
                        entity_key=_execution_group_leg_key(
                            row.source_order_id,
                            leg.raw_symbol,
                            leg.side,
                            leg.quantity_ratio,
                        ),
                        message=(
                            "Leg fill quantity does not equal package quantity "
                            "multiplied by its declared ratio."
                        ),
                        field_name="quantity",
                    )
                )
            instrument = _instrument_fields(leg.raw_symbol)
            if (
                instrument["asset_type"] == "option"
                and _normalized_symbol(leg.raw_symbol) not in contract_specs
            ):
                issues.append(
                    OpenApiPlanIssue(
                        code="execution_group_contract_multiplier_unproved",
                        severity="blocking",
                        entity_kind="execution_group_leg",
                        entity_key=_execution_group_leg_key(
                            row.source_order_id,
                            leg.raw_symbol,
                            leg.side,
                            leg.quantity_ratio,
                        ),
                        message=(
                            "Executed option leg has no frozen broker contract "
                            "multiplier evidence."
                        ),
                        field_name="contract_multiplier",
                    )
                )
    return tuple(issues)


def _plan_without_csv_baseline(
    preview: OpenApiExportPreview,
    *,
    account_key: str,
    source_scope: Mapping[str, Any],
) -> OpenApiImportPlan:
    issue = OpenApiPlanIssue(
        code="no_accepted_csv_baseline",
        severity="blocking",
        entity_kind="batch",
        entity_key=account_key,
        message="No accepted CSV baseline exists for this account.",
    )
    preview_key = _sha256_json(
        {
            "account_key": account_key,
            "evidence_sha256": preview.metadata.evidence_sha256,
            "issue": issue.code,
        }
    )
    cutoff = _utc(preview.metadata.window_end, field_name="window_end")
    return OpenApiImportPlan(
        preview_key=preview_key,
        confirm_allowed=False,
        source_scope=source_scope,
        base_scope=None,
        coverage={
            "scope": "unavailable",
            "matched_orders": 0,
            "csv_only_in_window": 0,
            "api_only_orders": len(preview.orders),
            "overlap_api_only_orders": 0,
            "incremental_api_only_orders": 0,
            "ambiguous_identity_keys": 0,
            "covered_base_orders": 0,
            "base_orders": 0,
            "coverage_ratio": "0",
            "outside_unverified_orders": 0,
        },
        write_plan=_empty_write_plan(),
        canonical_impact=_empty_impact(preview),
        issues=(issue,),
        warnings=tuple(preview.metadata.warnings),
        scope_is_full_batch=False,
        source_cutoff_at=cutoff,
        reconciliation=None,
        identity_projection=None,
        canonical_evidence_set=None,
    )


def plan_openapi_import(
    preview: OpenApiExportPreview,
    payload: Mapping[str, Any],
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> OpenApiImportPlan:
    """Build a deterministic, zero-write plan against the latest CSV batch."""
    if not isinstance(preview, OpenApiExportPreview):
        raise OpenApiPlanError("plan requires a strictly parsed OpenAPI preview")
    account_key = account_key.strip()
    if not account_key:
        raise OpenApiPlanError("account_key cannot be empty")
    metadata = preview.metadata
    source_scope = {
        "environment": metadata.environment,
        "market": metadata.market,
        "account_selection": metadata.account_selection,
        "account_bound": metadata.account_binding is not None,
        "window_start": metadata.window_start,
        "window_end": metadata.window_end,
        "source_timezone": metadata.source_timezone,
        "order_observations": len(preview.orders),
        "fill_observations": len(preview.fills),
        "fee_observations": len(preview.fees),
        "contract_spec_observations": len(preview.contract_specs),
        **_execution_group_source_counts(preview),
        "fee_totals_by_currency": {},
    }
    order_currency = {item.source_order_id: item.currency for item in preview.orders}
    fee_totals: dict[str, Decimal] = {}
    for fee in preview.fees:
        currency = order_currency[fee.source_order_id]
        fee_totals[currency] = fee_totals.get(currency, Decimal("0")) + fee.total_fee
    source_scope["fee_totals_by_currency"] = {
        key: format(value, "f") for key, value in sorted(fee_totals.items())
    }

    db = get_db()
    if not inspect(db._engine).has_table(ImportBatch.__tablename__):
        return _plan_without_csv_baseline(
            preview,
            account_key=account_key,
            source_scope=source_scope,
        )
    issues: list[OpenApiPlanIssue] = []
    with db.session_scope() as session:
        base = session.execute(
            select(ImportBatch)
            .where(
                ImportBatch.broker == "moomoo",
                ImportBatch.account_key == account_key,
                ImportBatch.source_kind == "csv",
                ImportBatch.status == "accepted",
            )
            .order_by(ImportBatch.recorded_at.desc(), ImportBatch.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if base is None:
            return _plan_without_csv_baseline(
                preview,
                account_key=account_key,
                source_scope=source_scope,
            )
        base_start = _utc(base.window_start, field_name="base.window_start")
        base_end = _utc(base.window_end, field_name="base.window_end")
        issues.extend(
            _execution_group_plan_issues(
                preview,
                base_start=base_start,
                base_end=base_end,
            )
        )

        orders = tuple(
            session.execute(
                select(BrokerOrderObservation)
                .where(BrokerOrderObservation.import_batch_id == base.id)
                .order_by(BrokerOrderObservation.id)
            ).scalars()
        )
        fills = tuple(
            session.execute(
                select(BrokerFillObservation)
                .where(BrokerFillObservation.import_batch_id == base.id)
                .order_by(BrokerFillObservation.id)
            ).scalars()
        )
        max_batch_id = int(
            session.execute(select(func.max(ImportBatch.id))).scalar_one() or 0
        )
        existing_api = session.execute(
            select(ImportBatch).where(
                ImportBatch.batch_key == _storage_batch_key(preview, account_key)
            )
        ).scalar_one_or_none()

        statement = _statement_from_rows(base, orders, fills)
        reconciliation = reconcile_statement_with_readonly_export(
            statement,
            payload,
            baseline_window_end=base_end,
        )
        accepted_api_batches = session.execute(
            select(ImportBatch).where(
                ImportBatch.broker == "moomoo",
                ImportBatch.account_key == account_key,
                ImportBatch.source_kind == "openapi",
                ImportBatch.status == "accepted",
            )
        ).scalars().all()
        prior_account_bindings: set[str] = set()
        for batch in accepted_api_batches:
            try:
                provenance = json.loads(str(batch.provenance_json))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            binding = str(provenance.get("account_binding") or "").strip().lower()
            if binding:
                prior_account_bindings.add(binding)
        current_binding = (metadata.account_binding or "").strip().lower()
        if reconciliation.incremental_api_only_orders and not current_binding:
            issues.append(
                OpenApiPlanIssue(
                    code="incremental_tail_account_unbound",
                    severity="blocking",
                    entity_kind="account",
                    entity_key=account_key,
                    message=(
                        "Incremental broker-only orders require a stable "
                        "account binding from the server-owned OpenD refresh."
                    ),
                )
            )
        if prior_account_bindings and (
            current_binding not in prior_account_bindings
            or len(prior_account_bindings) != 1
        ):
            issues.append(
                OpenApiPlanIssue(
                    code="openapi_account_binding_conflict",
                    severity="blocking",
                    entity_kind="account",
                    entity_key=account_key,
                    message=(
                        "This OpenD account binding differs from previously "
                        "accepted broker evidence."
                    ),
                )
            )
        persisted_inputs = load_canonical_observation_inputs(
            account_key,
            _session=session,
        )
        if persisted_inputs.csv_baseline_batch_id != int(base.id):
            raise OpenApiPlanError(
                "canonical input baseline changed while planning the import"
            )
        multiplier_by_instrument = _multiplier_map(orders, fills)
        if existing_api is None:
            api_orders, api_fills, api_groups = _api_inputs(
                preview,
                account_key=account_key,
                virtual_batch_id=max(max_batch_id + 1, 1),
                multiplier_by_instrument=multiplier_by_instrument,
            )
        else:
            # Re-planning an already accepted export must use its persisted
            # observations exactly once.  Adding a second virtual copy would
            # change provenance counts and break idempotent confirmation.
            api_orders, api_fills, api_groups = (), (), ()

        projection: Optional[IdentityProjection] = None
        evidence_set: Optional[CanonicalEvidenceSet] = None
        try:
            projection = project_reconciled_identities(
                reconciliation=reconciliation,
                # Confirmation canonicalizes the latest CSV baseline plus all
                # accepted OpenAPI batches.  Plan against the same cumulative
                # evidence scope, then add this upload only when it is new.
                order_observations=(*persisted_inputs.orders, *api_orders),
                fill_observations=(*persisted_inputs.fills, *api_fills),
                selected_csv_batch=AcceptedCsvBatchSelection(
                    import_batch_id=int(base.id),
                    batch_key=str(base.batch_key),
                ),
            )
            evidence_set = canonicalize_observations(
                projection.orders,
                projection.fills,
                execution_group_observations=(
                    *getattr(persisted_inputs, "execution_groups", ()),
                    *api_groups,
                ),
            )
        except IdentityProjectionError as exc:
            issues.append(
                OpenApiPlanIssue(
                    code="cross_source_identity_blocked",
                    severity="blocking",
                    entity_kind="reconciliation",
                    entity_key=str(base.batch_key),
                    message=str(exc),
                )
            )

        if evidence_set is not None:
            for item in evidence_set.issues:
                issues.append(
                    OpenApiPlanIssue(
                        code=item.code,
                        severity=item.severity,
                        entity_kind=item.entity_kind,
                        entity_key=item.entity_key,
                        field_name=item.field_name,
                        message=item.message,
                    )
                )
            impact = OpenApiCanonicalImpact.from_evidence_set(evidence_set)
            unproved_options = [
                item
                for item in evidence_set.fills
                if item.evidence.instrument.asset_type.lower() == "option"
                and item.evidence.instrument.contract_multiplier is None
                and item.evidence.amount is None
            ]
            if unproved_options:
                issues.append(
                    OpenApiPlanIssue(
                        code="contract_multiplier_unproved",
                        severity="blocking",
                        entity_kind="instrument",
                        entity_key="option",
                        message=(
                            "Selected API option fills have neither observed notional "
                            "nor an evidence-derived contract multiplier."
                        ),
                    )
                )
                impact = OpenApiCanonicalImpact(
                    **{
                        **impact.__dict__,
                        "blocking_issues": impact.blocking_issues + 1,
                        "analysis_ready": False,
                    }
                )
        else:
            impact = _empty_impact(preview)

        if any(item.severity == "blocking" for item in issues):
            impact = OpenApiCanonicalImpact(
                **{
                    **impact.__dict__,
                    "blocking_issues": max(
                        impact.blocking_issues,
                        sum(item.severity == "blocking" for item in issues),
                    ),
                    "analysis_ready": False,
                }
            )

        planned_order_links = 0
        planned_deal_links = 0
        planned_fill_set_attestations = 0
        link_state_issues: list[OpenApiPlanIssue] = []
        if projection is not None:
            existing_order_links = session.execute(
                select(OrderIdentityLink).where(
                    OrderIdentityLink.account_key == account_key
                )
            ).scalars().all()
            order_link_by_csv_id = {
                int(row.broker_order_observation_id): row
                for row in existing_order_links
            }
            for item in projection.order_links:
                csv_id = int(item.statement_evidence[0].observation_id)
                existing = order_link_by_csv_id.get(csv_id)
                if existing is None:
                    planned_order_links += 1
                elif str(existing.canonical_order_id) != item.broker_order_id:
                    link_state_issues.append(
                        OpenApiPlanIssue(
                            code="existing_order_identity_conflict",
                            severity="blocking",
                            entity_kind="order",
                            entity_key=item.statement_order_id,
                            message=(
                                "The selected CSV order already resolves to a "
                                "different immutable broker order ID."
                            ),
                        )
                    )

            existing_deal_links = session.execute(
                select(DealIdentityLink).where(
                    DealIdentityLink.account_key == account_key
                )
            ).scalars().all()
            deal_link_by_csv_id = {
                int(row.broker_fill_observation_id): row
                for row in existing_deal_links
            }
            for item in projection.deal_links:
                csv_id = int(item.statement_evidence[0].observation_id)
                existing = deal_link_by_csv_id.get(csv_id)
                if existing is None:
                    planned_deal_links += 1
                elif (
                    str(existing.canonical_order_id) != item.canonical_order_id
                    or str(existing.canonical_deal_id) != item.broker_deal_id
                ):
                    link_state_issues.append(
                        OpenApiPlanIssue(
                            code="existing_deal_identity_conflict",
                            severity="blocking",
                            entity_kind="fill",
                            entity_key=item.statement_deal_id,
                            message=(
                                "The selected CSV fill already resolves to a "
                                "different immutable broker deal ID."
                            ),
                        )
                    )

            existing_fill_sets = session.execute(
                select(OrderFillSetAttestation).where(
                    OrderFillSetAttestation.account_key == account_key
                )
            ).scalars().all()
            fill_set_by_shadow_id = {
                int(row.shadow_order_observation_id): row
                for row in existing_fill_sets
            }
            fill_set_by_canonical_id = {
                str(row.canonical_order_id): row for row in existing_fill_sets
            }
            csv_order_id_by_statement_id = {
                item.statement_order_id: int(
                    item.statement_evidence[0].observation_id
                )
                for item in projection.order_links
            }
            for item in projection.fill_set_attestations:
                csv_order_id = csv_order_id_by_statement_id[
                    item.statement_order_id
                ]
                existing = fill_set_by_shadow_id.get(csv_order_id)
                if existing is not None:
                    if str(existing.canonical_order_id) != item.canonical_order_id:
                        link_state_issues.append(
                            OpenApiPlanIssue(
                                code="existing_fill_set_identity_conflict",
                                severity="blocking",
                                entity_kind="order",
                                entity_key=item.statement_order_id,
                                message=(
                                    "The CSV fill-set shadow already belongs to "
                                    "a different canonical broker order."
                                ),
                            )
                        )
                    continue
                if item.canonical_order_id in fill_set_by_canonical_id:
                    link_state_issues.append(
                        OpenApiPlanIssue(
                            code="fill_set_baseline_refresh_requires_migration",
                            severity="blocking",
                            entity_kind="order",
                            entity_key=item.statement_order_id,
                            message=(
                                "A prior CSV baseline already has an immutable "
                                "fill-set attestation for this broker order."
                            ),
                        )
                    )
                    continue
                planned_fill_set_attestations += 1

        if link_state_issues:
            issues.extend(link_state_issues)
            impact = OpenApiCanonicalImpact(
                **{
                    **impact.__dict__,
                    "blocking_issues": (
                        impact.blocking_issues + len(link_state_issues)
                    ),
                    "analysis_ready": False,
                }
            )

        cutoff = max(base_end, _utc(metadata.window_end, field_name="window_end"))
        scope_is_full = reconciliation.matched_orders == len(orders)
        if reconciliation.incremental_api_only_orders > 0:
            scope = "incremental_tail"
        else:
            scope = "full_batch" if scope_is_full else "partial_window"
        ratio = (
            Decimal(reconciliation.matched_orders) / Decimal(len(orders))
            if orders
            else Decimal("0")
        )
        base_scope = {
            "batch_id": int(base.id),
            "batch_key": str(base.batch_key),
            "window_start": base_start,
            "window_end": base_end,
            "order_observations": len(orders),
            "fill_observations": len(fills),
            "window_order_observations": reconciliation.statement_orders,
        }
        coverage = {
            "scope": scope,
            "matched_orders": reconciliation.matched_orders,
            "csv_only_in_window": reconciliation.statement_only_orders,
            "api_only_orders": reconciliation.api_only_orders,
            "overlap_api_only_orders": (
                reconciliation.overlap_api_only_orders
            ),
            "incremental_api_only_orders": (
                reconciliation.incremental_api_only_orders
            ),
            "ambiguous_identity_keys": reconciliation.ambiguous_identity_keys,
            "covered_base_orders": reconciliation.matched_orders,
            "base_orders": len(orders),
            "coverage_ratio": format(ratio.quantize(Decimal("0.0001")), "f"),
            "outside_unverified_orders": max(0, len(orders) - reconciliation.matched_orders),
        }
        existing_set = None
        if impact.canonical_set_sha256:
            existing_set = session.execute(
                select(CanonicalEvidenceSetRecord).where(
                    CanonicalEvidenceSetRecord.account_key == account_key,
                    CanonicalEvidenceSetRecord.canonical_set_sha256
                    == impact.canonical_set_sha256,
                )
            ).scalar_one_or_none()
        already_imported = existing_api is not None and existing_set is not None
        source_counts = _execution_group_source_counts(preview)
        write_plan = {
            "already_imported": already_imported,
            "order_observations": 0 if existing_api is not None else len(preview.orders),
            "ordinary_order_observations": (
                0
                if existing_api is not None
                else source_counts["ordinary_order_observations"]
            ),
            "unclassified_parent_observations": (
                0
                if existing_api is not None
                else source_counts["unclassified_parent_observations"]
            ),
            "fill_observations": 0 if existing_api is not None else len(preview.fills),
            "fee_observations": 0 if existing_api is not None else len(preview.fees),
            "execution_group_observations": (
                0
                if existing_api is not None
                else source_counts["execution_group_observations"]
            ),
            "execution_group_leg_observations": (
                0
                if existing_api is not None
                else source_counts["execution_group_leg_observations"]
            ),
            "execution_group_fill_links": (
                0
                if existing_api is not None
                else source_counts["execution_group_fill_links"]
            ),
            "execution_group_fee_observations": (
                0
                if existing_api is not None
                else source_counts["execution_group_fee_observations"]
            ),
            "order_identity_links": (
                0 if already_imported else planned_order_links
            ),
            "deal_identity_links": (
                0 if already_imported else planned_deal_links
            ),
            "fill_set_attestations": (
                0 if already_imported else planned_fill_set_attestations
            ),
            "canonical_sets": 0 if existing_set is not None else int(evidence_set is not None),
        }
        plan_payload = {
            "account_key": account_key,
            "base_batch_key": str(base.batch_key),
            "base_batch_recorded_at": _utc(base.recorded_at, field_name="base.recorded_at"),
            "openapi_evidence_sha256": metadata.evidence_sha256,
            "source_cutoff_at": cutoff,
            "reconciliation": reconciliation.summary(),
            "identity_projection": {
                "name": IDENTITY_PROJECTION_NAME,
                "version": IDENTITY_PROJECTION_VERSION,
                "order_links": [] if projection is None else [item.link_key for item in projection.order_links],
                "deal_links": [] if projection is None else [item.link_key for item in projection.deal_links],
                "fill_set_attestations": (
                    []
                    if projection is None
                    else [item.attestation_key for item in projection.fill_set_attestations]
                ),
            },
            "execution_group_projection": {
                "name": _EXECUTION_GROUP_PROJECTION_NAME,
                "version": _EXECUTION_GROUP_PROJECTION_VERSION,
                "group_observation_keys": [
                    item.observation_key
                    for item in sorted(
                        api_groups,
                        key=lambda value: value.observation_key,
                    )
                ],
                "leg_observation_keys": sorted(
                    leg.observation_key
                    for item in api_groups
                    for leg in item.legs
                ),
                "fill_link_keys": sorted(
                    item.execution_group_fill_link_key
                    for item in api_fills
                    if item.execution_group_fill_link_key is not None
                ),
                "fee_observation_keys": sorted(
                    item.fee_observation_key
                    for item in api_groups
                    if item.fee_observation_key is not None
                ),
                "fee_scope": "execution_group_only",
            },
            "canonical_reader": {
                "name": CANONICAL_READER_NAME,
                "version": CANONICAL_READER_VERSION,
                "canonical_set_sha256": impact.canonical_set_sha256,
            },
        }
        preview_key = _sha256_json(plan_payload)
        confirm_allowed = (
            metadata.analysis_ready
            and reconciliation.analysis_ready
            and evidence_set is not None
            and impact.analysis_ready
            and not any(item.severity == "blocking" for item in issues)
        )
        return OpenApiImportPlan(
            preview_key=preview_key,
            confirm_allowed=confirm_allowed,
            source_scope=source_scope,
            base_scope=base_scope,
            coverage=coverage,
            write_plan=write_plan,
            canonical_impact=impact,
            issues=tuple(issues),
            warnings=tuple(dict.fromkeys((*metadata.warnings, *reconciliation.warnings))),
            scope_is_full_batch=scope_is_full,
            source_cutoff_at=cutoff,
            reconciliation=reconciliation,
            identity_projection=projection,
            canonical_evidence_set=evidence_set,
        )


def confirm_openapi_import_plan(
    preview: OpenApiExportPreview,
    payload: Mapping[str, Any],
    *,
    preview_key: str,
    acknowledge_partial_window: bool,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> OpenApiConfirmResult:
    """Re-plan, reject stale confirmation, then atomically append the bundle."""
    plan = plan_openapi_import(preview, payload, account_key=account_key)
    if preview_key.strip() != plan.preview_key:
        raise OpenApiPlanError("stale_preview")
    if not plan.confirm_allowed or plan.identity_projection is None:
        raise OpenApiPlanError("plan_contains_blocking_issues")
    if not plan.scope_is_full_batch and acknowledge_partial_window is not True:
        raise OpenApiPlanError("partial_window_requires_explicit_acknowledgement")

    # Imported lazily so the pure/read-only planning path never reaches a write
    # helper.  The repository owns the single transaction and idempotency race.
    try:
        from src.journal.ledger.repository import append_openapi_canonical_bundle
    except ImportError as exc:  # pragma: no cover - integration guard
        raise OpenApiPlanError("atomic_openapi_bundle_writer_unavailable") from exc

    order_observation_by_statement_id = {
        item.statement_order_id: item.statement_evidence[0].observation_id
        for item in plan.identity_projection.order_links
    }
    order_matches = tuple(
        (item.statement_evidence[0].observation_id, item.broker_order_id)
        for item in plan.identity_projection.order_links
    )
    deal_matches = tuple(
        (item.statement_evidence[0].observation_id, item.broker_deal_id)
        for item in plan.identity_projection.deal_links
    )
    fill_set_shadow_order_ids = tuple(
        order_observation_by_statement_id[item.statement_order_id]
        for item in plan.identity_projection.fill_set_attestations
    )
    try:
        result = append_openapi_canonical_bundle(
            preview,
            order_identity_matches=order_matches,
            deal_identity_matches=deal_matches,
            fill_set_shadow_order_observation_ids=fill_set_shadow_order_ids,
            # Let the atomic writer choose the exact committed visibility
            # cutoff.  The expected hash below still binds all economic facts.
            source_cutoff_at=None,
            expected_canonical_set_sha256=(
                plan.canonical_impact.canonical_set_sha256
            ),
            account_key=account_key,
            confirmed=True,
        )
    except LedgerImportError as exc:
        raise OpenApiPlanError(str(exc)) from exc
    except (IntegrityError, OperationalError) as exc:
        raise OpenApiPlanError(
            "concurrent_confirmation_conflict; re-plan and retry"
        ) from exc

    import_result = result.import_result
    canonical_result = result.canonical_set
    execution_group_observations = int(
        getattr(import_result, "execution_group_observations", 0)
    )
    execution_group_leg_observations = int(
        getattr(import_result, "execution_group_leg_observations", 0)
    )
    execution_group_fill_links = int(
        getattr(import_result, "execution_group_fill_links", 0)
    )
    execution_group_fee_observations = int(
        getattr(import_result, "execution_group_fee_observations", 0)
    )
    appended = {
        "orders": 0 if import_result.duplicate else import_result.order_observations,
        "ordinary_orders": (
            0
            if import_result.duplicate
            else import_result.order_observations
            - execution_group_observations
        ),
        "fills": 0 if import_result.duplicate else import_result.fill_observations,
        "fees": 0 if import_result.duplicate else import_result.fee_observations,
        "execution_groups": (
            0
            if import_result.duplicate
            else execution_group_observations
        ),
        "execution_group_legs": (
            0
            if import_result.duplicate
            else execution_group_leg_observations
        ),
        "execution_group_fill_links": (
            0
            if import_result.duplicate
            else execution_group_fill_links
        ),
        "execution_group_fees": (
            0
            if import_result.duplicate
            else execution_group_fee_observations
        ),
        "order_links": sum(not item.duplicate for item in result.order_identity_links),
        "deal_links": sum(not item.duplicate for item in result.deal_identity_links),
        "fill_set_attestations": sum(
            not item.duplicate for item in result.fill_set_attestations
        ),
        "canonical_sets": int(not canonical_result.duplicate),
    }
    impact = plan.canonical_impact
    duplicate = not any(appended.values())
    return OpenApiConfirmResult(
        status="already_present" if duplicate else "appended",
        duplicate=duplicate,
        import_batch_id=int(import_result.batch_id),
        canonical_set_id=int(canonical_result.canonical_set_id),
        canonical_set_sha256=str(canonical_result.canonical_set_sha256),
        scope=str(plan.coverage["scope"]),
        appended=appended,
        canonical={
            "orders": impact.canonical_orders,
            "fills": impact.canonical_fills,
            "execution_groups": impact.canonical_execution_groups,
            "execution_group_legs": impact.canonical_execution_group_legs,
            "shadowed_csv_fills": impact.shadowed_csv_fills,
            "shadowed_aggregate_orders": impact.shadowed_aggregate_orders,
            "blocking_issues": impact.blocking_issues,
            "analysis_ready": impact.analysis_ready,
        },
        message=(
            "OpenAPI evidence already present; no rows were added."
            if duplicate
            else "OpenAPI read-only evidence and canonical provenance appended."
        ),
    )
