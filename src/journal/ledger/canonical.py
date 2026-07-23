# -*- coding: utf-8 -*-
"""Pure cross-batch canonical reader for Journal v2 broker observations.

The append-only observation tables intentionally retain every source view.  A
consumer must therefore canonicalize observations before reconstructing
positions, otherwise overlapping CSV/API batches can count one execution more
than once.  This module is the database-free boundary for that operation.

Identity rules are deliberately narrow:

* orders are grouped only by ``broker/account_key/source_order_id``;
* fills are grouped only by ``broker/account_key/source_deal_id``;
* a weak identity is accepted only when the same group also contains a stable
  broker/reconciled identity, or when the caller explicitly promotes rows from
  one selected immutable snapshot to ``selected_batch_stable``;
* detailed fills replace an aggregate order event for the same stable order;
* conflicts, orphan fills, and inconsistent fee evidence are returned as
  blocking issues instead of being silently resolved.

The input dataclasses mirror the useful fields on
``BrokerOrderObservation``/``BrokerFillObservation`` plus immutable batch
provenance supplied by their joined ``ImportBatch``.  Canonical outputs expose
the existing ``episodes.Canonical*Evidence`` objects so the position builder
can consume them without another lossy projection.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from src.journal.ledger.episodes import (
    CanonicalFillEvidence,
    CanonicalOrderEvidence,
    InstrumentIdentity,
)

__all__ = [
    "CANONICAL_READER_NAME",
    "CANONICAL_READER_VERSION",
    "CanonicalEvidenceSet",
    "CanonicalFill",
    "CanonicalIssue",
    "CanonicalOrder",
    "CanonicalProvenance",
    "CanonicalizationBlockedError",
    "CanonicalizationInputError",
    "stable_source_sequence",
    "EvidenceRef",
    "FillObservationInput",
    "OrderObservationInput",
    "canonicalize_observations",
]


CANONICAL_READER_NAME = "stable_broker_identity_reader"
CANONICAL_READER_VERSION = "1.0.0"
_ZERO = Decimal("0")
_VWAP_ABSOLUTE_TOLERANCE = Decimal("0.0001")
_VWAP_RELATIVE_TOLERANCE = Decimal("0.000001")
_PRICE_TOLERANCE = Decimal("0.00000001")
_QUANTITY_TOLERANCE = Decimal("0.00000001")
_AMOUNT_TOLERANCE = Decimal("0.005")
_MARKET_SYMBOL_PREFIXES = frozenset({"US", "HK", "SH", "SZ"})
_FILL_SET_ROLES = frozenset({"ordinary", "authoritative", "attested_shadow"})
_STABLE_IDENTITY_STRENGTHS = frozenset(
    {
        "stable",
        "strong",
        "broker",
        "broker_id",
        "broker_strong",
        "broker_stable",
        "reconciled_stable",
        "derived_strong",
        # This strength is assigned only by the audited projection layer after
        # it has selected exactly one immutable CSV snapshot.  It is stable
        # inside that canonical evidence set, but deliberately does not claim
        # a broker identity or authorize cross-batch fill matching.
        "selected_batch_stable",
    }
)
_API_SOURCE_KINDS = frozenset({"api", "openapi", "moomoo_openapi"})


class CanonicalizationInputError(ValueError):
    """Raised when a neutral observation violates the reader contract."""


class CanonicalizationBlockedError(ValueError):
    """Raised when a caller explicitly requires an analysis-ready set."""


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CanonicalizationInputError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _require_decimal(
    value: Optional[Decimal],
    *,
    field_name: str,
    allow_none: bool = True,
) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, Decimal):
        raise CanonicalizationInputError(
            f"{field_name} must be Decimal, not {type(value).__name__}"
        )


def _validate_common(value: Any, *, kind: str) -> None:
    if not isinstance(value.observation_id, int) or value.observation_id <= 0:
        raise CanonicalizationInputError(f"{kind} observation_id must be positive")
    if not isinstance(value.import_batch_id, int) or value.import_batch_id <= 0:
        raise CanonicalizationInputError(f"{kind} import_batch_id must be positive")
    for field_name in (
        "batch_key",
        "source_kind",
        "observation_key",
        "source_record_sha256",
        "broker",
        "account_key",
        "raw_symbol",
        "asset_type",
        "underlying",
        "side",
        "currency",
    ):
        if not str(getattr(value, field_name)).strip():
            raise CanonicalizationInputError(f"{kind} {field_name} cannot be empty")
    _aware_utc(value.recorded_at, field_name=f"{kind}.recorded_at")
    if value.source_updated_at is not None:
        _aware_utc(
            value.source_updated_at,
            field_name=f"{kind}.source_updated_at",
        )
    if value.source_row_number is not None and value.source_row_number < 0:
        raise CanonicalizationInputError(
            f"{kind} source_row_number cannot be negative"
        )
    for field_name in ("strike", "contract_multiplier"):
        _require_decimal(getattr(value, field_name), field_name=f"{kind}.{field_name}")
    if value.contract_multiplier is not None and value.contract_multiplier <= 0:
        raise CanonicalizationInputError(
            f"{kind} contract_multiplier must be positive"
        )
    multiplier_basis = value.contract_multiplier_basis.strip().lower()
    if value.contract_multiplier is not None and multiplier_basis == "unknown":
        raise CanonicalizationInputError(
            f"{kind} contract_multiplier requires explicit provenance"
        )
    if value.contract_multiplier is None and multiplier_basis != "unknown":
        raise CanonicalizationInputError(
            f"{kind} contract_multiplier_basis requires a multiplier"
        )


@dataclass(frozen=True)
class OrderObservationInput:
    """Database-neutral projection of one order observation plus batch facts."""

    observation_id: int
    import_batch_id: int
    batch_key: str
    source_kind: str
    observation_key: str
    source_record_sha256: str
    broker: str
    account_key: str
    source_order_id: str
    identity_strength: str
    raw_symbol: str
    asset_type: str
    underlying: str
    side: str
    status: str
    currency: str
    ordered_at: datetime
    order_quantity: Decimal
    evidence_level: str
    fee_evidence_status: str
    recorded_at: datetime
    expiry: Optional[date] = None
    strike: Optional[Decimal] = None
    option_right: Optional[str] = None
    contract_multiplier: Optional[Decimal] = None
    contract_multiplier_basis: str = "unknown"
    source_row_number: Optional[int] = None
    source_updated_at: Optional[datetime] = None
    order_price: Optional[Decimal] = None
    order_amount: Optional[Decimal] = None
    summary_filled_quantity: Optional[Decimal] = None
    summary_average_fill_price: Optional[Decimal] = None
    total_fee: Optional[Decimal] = None
    # Supplied only by a separately audited identity-link layer.  It lets a
    # CSV-derived source ID group with the broker ID without overwriting either
    # immutable source observation.
    canonical_order_id: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_common(self, kind="order")
        _aware_utc(self.ordered_at, field_name="order.ordered_at")
        for field_name in (
            "order_quantity",
            "order_price",
            "order_amount",
            "summary_filled_quantity",
            "summary_average_fill_price",
            "total_fee",
        ):
            _require_decimal(
                getattr(self, field_name),
                field_name=f"order.{field_name}",
                allow_none=field_name != "order_quantity",
            )
        if self.order_quantity < 0:
            raise CanonicalizationInputError("order_quantity cannot be negative")
        if self.canonical_order_id is not None and not self.canonical_order_id.strip():
            raise CanonicalizationInputError("canonical_order_id cannot be empty")
        for field_name in (
            "order_price",
            "summary_filled_quantity",
            "summary_average_fill_price",
            "total_fee",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise CanonicalizationInputError(
                    f"order.{field_name} cannot be negative"
                )


@dataclass(frozen=True)
class FillObservationInput:
    """Database-neutral projection of one detailed execution observation."""

    observation_id: int
    import_batch_id: int
    batch_key: str
    source_kind: str
    observation_key: str
    source_record_sha256: str
    broker: str
    account_key: str
    source_order_id: Optional[str]
    source_deal_id: str
    identity_strength: str
    raw_symbol: str
    asset_type: str
    underlying: str
    side: str
    currency: str
    filled_at: datetime
    quantity: Decimal
    price: Decimal
    recorded_at: datetime
    expiry: Optional[date] = None
    strike: Optional[Decimal] = None
    option_right: Optional[str] = None
    contract_multiplier: Optional[Decimal] = None
    contract_multiplier_basis: str = "unknown"
    source_row_number: Optional[int] = None
    source_updated_at: Optional[datetime] = None
    amount: Optional[Decimal] = None
    total_fee: Optional[Decimal] = None
    # These aliases are outputs of a future persisted identity-link layer, not
    # fuzzy matches performed by this reader.
    canonical_order_id: Optional[str] = None
    canonical_deal_id: Optional[str] = None
    # A persisted order-level attestation can mark the complete API set as
    # authoritative and the economically equivalent CSV weak-fill set as a
    # shadow.  This preserves set-level provenance without inventing unsafe
    # one-to-one deal links.
    fill_set_role: str = "ordinary"
    fill_set_attestation_key: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_common(self, kind="fill")
        _aware_utc(self.filled_at, field_name="fill.filled_at")
        for field_name in ("quantity", "price", "amount", "total_fee"):
            _require_decimal(
                getattr(self, field_name),
                field_name=f"fill.{field_name}",
                allow_none=field_name in {"amount", "total_fee"},
            )
        if self.quantity <= 0:
            raise CanonicalizationInputError("fill quantity must be positive")
        if self.price < 0:
            raise CanonicalizationInputError("fill price cannot be negative")
        if self.total_fee is not None and self.total_fee < 0:
            raise CanonicalizationInputError("fill total_fee cannot be negative")
        for field_name in ("canonical_order_id", "canonical_deal_id"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise CanonicalizationInputError(f"{field_name} cannot be empty")
        role = self.fill_set_role.strip().lower()
        if role not in _FILL_SET_ROLES:
            raise CanonicalizationInputError("fill_set_role is unsupported")
        if role == "ordinary":
            if self.fill_set_attestation_key is not None:
                raise CanonicalizationInputError(
                    "ordinary fill cannot carry a fill_set_attestation_key"
                )
        elif (
            self.canonical_order_id is None
            or self.fill_set_attestation_key is None
            or not self.fill_set_attestation_key.strip()
        ):
            raise CanonicalizationInputError(
                "attested fill-set rows require canonical_order_id and "
                "fill_set_attestation_key"
            )


def _normalized_source_kind(value: str) -> str:
    normalized = value.strip().lower()
    return "openapi" if normalized in _API_SOURCE_KINDS else normalized


def _normalized_side(value: str) -> str:
    return value.strip().upper().replace(" ", "_")


def _normalized_status(value: str) -> str:
    return value.strip().upper().replace(" ", "_")


def _normalized_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip().upper()
    return stripped or None


def _normalized_symbol(value: Optional[str]) -> Optional[str]:
    normalized = _normalized_optional(value)
    if normalized is None or "." not in normalized:
        return normalized
    prefix, remainder = normalized.split(".", 1)
    if prefix in _MARKET_SYMBOL_PREFIXES and remainder:
        return remainder
    return normalized


def _decimal_text(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _vwap_tolerance(*values: Decimal) -> Decimal:
    """Match the broker/display precision used by source reconciliation."""
    reference = max((abs(value) for value in values), default=_ZERO)
    return max(
        _VWAP_ABSOLUTE_TOLERANCE,
        reference * _VWAP_RELATIVE_TOLERANCE,
    )


def stable_source_sequence(
    *,
    source_row_number: Optional[int],
    source_kind: str,
    observation_key: str,
    source_record_sha256: str,
) -> int:
    """Return the deterministic event tie-breaker used by canonical builds."""
    if source_row_number is not None:
        return int(source_row_number)
    payload = "|".join(
        (
            _normalized_source_kind(source_kind),
            observation_key.strip(),
            source_record_sha256.strip().lower(),
        )
    )
    # Seven bytes stay within SQLite's signed integer range while providing a
    # deterministic tie-breaker independent of local insertion IDs.
    return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:7], "big")


def _stable_source_sequence(
    value: OrderObservationInput | FillObservationInput,
) -> int:
    return stable_source_sequence(
        source_row_number=value.source_row_number,
        source_kind=value.source_kind,
        observation_key=value.observation_key,
        source_record_sha256=value.source_record_sha256,
    )


def _canonical_json(value: Any) -> str:
    def _default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return _decimal_text(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"unsupported canonical type: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_default,
    )


@dataclass(frozen=True)
class EvidenceRef:
    """One immutable source row contributing to a canonical economic fact."""

    evidence_kind: str
    observation_id: int
    import_batch_id: int
    batch_key: str
    source_kind: str
    observation_key: str
    source_record_sha256: str

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (
            self.evidence_kind,
            self.batch_key,
            self.source_kind,
            self.observation_key,
            self.source_record_sha256,
            str(self.import_batch_id),
            str(self.observation_id),
        )

    def canonical_payload(self) -> dict[str, str]:
        """Stable provenance payload; local database integer IDs are excluded."""
        return {
            "evidence_kind": self.evidence_kind,
            "batch_key": self.batch_key,
            "source_kind": self.source_kind,
            "observation_key": self.observation_key,
            "source_record_sha256": self.source_record_sha256,
        }


def _ref(value: OrderObservationInput | FillObservationInput) -> EvidenceRef:
    return EvidenceRef(
        evidence_kind="order" if isinstance(value, OrderObservationInput) else "fill",
        observation_id=value.observation_id,
        import_batch_id=value.import_batch_id,
        batch_key=value.batch_key.strip(),
        source_kind=_normalized_source_kind(value.source_kind),
        observation_key=value.observation_key.strip(),
        source_record_sha256=value.source_record_sha256.strip().lower(),
    )


def _refs(values: Iterable[OrderObservationInput | FillObservationInput]) -> tuple[EvidenceRef, ...]:
    return tuple(sorted({_ref(value) for value in values}, key=lambda item: item.sort_key))


def _identity_is_stable(value: str) -> bool:
    return value.strip().lower() in _STABLE_IDENTITY_STRENGTHS


def _order_identity(value: OrderObservationInput) -> tuple[str, str, str]:
    source_id = (value.canonical_order_id or value.source_order_id).strip()
    if not source_id:
        source_id = f"__invalid_order_observation_{value.observation_id}"
    return (
        value.broker.strip().lower(),
        value.account_key.strip(),
        source_id,
    )


def _fill_identity(value: FillObservationInput) -> tuple[str, str, str]:
    source_id = (value.canonical_deal_id or value.source_deal_id).strip()
    if not source_id:
        source_id = f"__invalid_fill_observation_{value.observation_id}"
    return (
        value.broker.strip().lower(),
        value.account_key.strip(),
        source_id,
    )


def _linked_order_identity(value: FillObservationInput) -> Optional[tuple[str, str, str]]:
    linked_id = value.canonical_order_id or value.source_order_id
    if linked_id is None or not linked_id.strip():
        return None
    return (
        value.broker.strip().lower(),
        value.account_key.strip(),
        linked_id.strip(),
    )


def _source_priority(value: str) -> int:
    normalized = _normalized_source_kind(value)
    if normalized == "openapi":
        return 3
    if normalized == "csv":
        return 2
    return 1


def _observation_priority(
    value: OrderObservationInput | FillObservationInput,
) -> tuple[Any, ...]:
    if value.source_updated_at is not None:
        updated = value.source_updated_at
    elif (
        _normalized_source_kind(value.source_kind) == "openapi"
        and _identity_is_stable(value.identity_strength)
    ):
        # Broker-stable executions are immutable by broker deal ID.  Local
        # ingestion time must not decide which copy wins when overlapping API
        # windows repeat the exact same deal; pre-write planning cannot know
        # that future database timestamp.  The stable EvidenceRef below is the
        # deterministic tie-breaker shared by plan and committed replay.
        updated = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        updated = value.recorded_at
    detail_priority = 0
    fee_priority = int(value.total_fee is not None)
    if isinstance(value, OrderObservationInput):
        detail_priority = {
            "fill_detail": 2,
            "aggregate_only": 1,
        }.get(value.evidence_level.strip().lower(), 0)
        fee_priority += int(value.fee_evidence_status.strip().lower() == "complete")
    return (
        _source_priority(value.source_kind),
        detail_priority,
        fee_priority,
        _aware_utc(updated, field_name="observation priority timestamp"),
        _ref(value).sort_key,
    )


def _preferred(
    values: Sequence[OrderObservationInput | FillObservationInput],
) -> OrderObservationInput | FillObservationInput:
    return max(values, key=_observation_priority)


def _canonical_instrument_value(value: Any, field_name: str) -> Any:
    if field_name == "broker":
        return str(value).strip().lower()
    if field_name == "raw_symbol":
        return _normalized_symbol(value)
    if field_name in {"underlying", "currency", "option_right"}:
        return _normalized_optional(value)
    if field_name in {"asset_type", "contract_multiplier_basis"}:
        return str(value).strip().lower() if value is not None else None
    return value


_INSTRUMENT_FIELDS = (
    "broker",
    "account_key",
    "raw_symbol",
    "asset_type",
    "underlying",
    "currency",
    "expiry",
    "strike",
    "option_right",
    "contract_multiplier",
)


def _resolved_instrument(
    values: Sequence[OrderObservationInput | FillObservationInput],
    *,
    preferred: OrderObservationInput | FillObservationInput,
) -> InstrumentIdentity:
    resolved: dict[str, Any] = {}
    ordered = sorted(values, key=_observation_priority, reverse=True)
    for field_name in _INSTRUMENT_FIELDS:
        candidates = [
            _canonical_instrument_value(getattr(item, field_name), field_name)
            for item in ordered
        ]
        resolved[field_name] = next(
            (candidate for candidate in candidates if candidate is not None),
            None,
        )
    multiplier = resolved["contract_multiplier"]
    multiplier_source = next(
        (item for item in ordered if item.contract_multiplier is not None),
        None,
    )
    basis = (
        multiplier_source.contract_multiplier_basis.strip().lower()
        if multiplier_source is not None
        else "unknown"
    )
    if multiplier is None:
        basis = "unknown"
    return InstrumentIdentity(
        broker=resolved["broker"],
        account_key=resolved["account_key"],
        raw_symbol=resolved["raw_symbol"],
        asset_type=resolved["asset_type"],
        underlying=resolved["underlying"],
        currency=resolved["currency"],
        expiry=resolved["expiry"],
        strike=resolved["strike"],
        option_right=resolved["option_right"],
        contract_multiplier=multiplier,
        contract_multiplier_basis=basis,
    )


@dataclass(frozen=True)
class CanonicalIssue:
    """A deterministic issue explaining why evidence is not analysis-ready."""

    code: str
    entity_kind: str
    entity_key: str
    message: str
    evidence_refs: tuple[EvidenceRef, ...]
    field_name: Optional[str] = None
    severity: str = "blocking"

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (
            self.severity,
            self.code,
            self.entity_kind,
            self.entity_key,
            self.field_name or "",
            *(ref.observation_key for ref in self.evidence_refs),
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "entity_kind": self.entity_kind,
            "entity_key": self.entity_key,
            "field_name": self.field_name,
            "message": self.message,
            "evidence_refs": [
                ref.canonical_payload() for ref in self.evidence_refs
            ],
        }


def _entity_text(identity: tuple[str, str, str]) -> str:
    return "/".join(identity)


def _append_issue(
    issues: list[CanonicalIssue],
    *,
    code: str,
    entity_kind: str,
    identity: tuple[str, str, str],
    message: str,
    evidence_refs: tuple[EvidenceRef, ...],
    field_name: Optional[str] = None,
) -> None:
    issue = CanonicalIssue(
        code=code,
        entity_kind=entity_kind,
        entity_key=_entity_text(identity),
        message=message,
        evidence_refs=evidence_refs,
        field_name=field_name,
    )
    if issue not in issues:
        issues.append(issue)


def _distinct_values(
    values: Sequence[OrderObservationInput | FillObservationInput],
    field_name: str,
) -> set[Any]:
    result: set[Any] = set()
    for item in values:
        if field_name == "source_order_id" and isinstance(item, FillObservationInput):
            value = item.canonical_order_id or item.source_order_id
        else:
            value = getattr(item, field_name)
        if value is None:
            continue
        if isinstance(value, datetime):
            value = _aware_utc(value, field_name=field_name)
            if field_name in {"ordered_at", "filled_at"}:
                # CSV timestamps are second precision while OpenAPI retains
                # fractional seconds.  An explicit identity link is stronger
                # evidence than this representational precision difference.
                value = value.replace(microsecond=0)
        elif field_name == "side":
            value = _normalized_side(value)
        elif field_name == "source_order_id":
            value = str(value).strip()
        elif field_name in _INSTRUMENT_FIELDS:
            value = _canonical_instrument_value(value, field_name)
        result.add(value)
    return result


def _values_conflict(
    values: Sequence[OrderObservationInput | FillObservationInput],
    field_name: str,
) -> bool:
    distinct = _distinct_values(values, field_name)
    if len(distinct) <= 1:
        return False
    tolerance = {
        "order_quantity": _QUANTITY_TOLERANCE,
        "quantity": _QUANTITY_TOLERANCE,
        "order_price": _PRICE_TOLERANCE,
        "price": _PRICE_TOLERANCE,
        "amount": _AMOUNT_TOLERANCE,
    }.get(field_name)
    if tolerance is not None and all(isinstance(item, Decimal) for item in distinct):
        decimal_values = tuple(distinct)
        return max(decimal_values) - min(decimal_values) > tolerance
    return True


def _check_conflicting_fields(
    issues: list[CanonicalIssue],
    *,
    entity_kind: str,
    identity: tuple[str, str, str],
    values: Sequence[OrderObservationInput | FillObservationInput],
    field_names: Sequence[str],
    issue_code: str,
) -> None:
    refs = _refs(values)
    for field_name in field_names:
        if _values_conflict(values, field_name):
            _append_issue(
                issues,
                code=issue_code,
                entity_kind=entity_kind,
                identity=identity,
                field_name=field_name,
                message=(
                    f"same stable {entity_kind} identity has conflicting "
                    f"{field_name} values"
                ),
                evidence_refs=refs,
            )


@dataclass(frozen=True)
class _SelectedFill:
    identity: tuple[str, str, str]
    selected: FillObservationInput
    observations: tuple[FillObservationInput, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    linked_order_identity: Optional[tuple[str, str, str]]
    total_fee: Optional[Decimal]


@dataclass(frozen=True)
class CanonicalOrder:
    """One order identity and its builder-ready canonical representation."""

    identity: tuple[str, str, str]
    source_order_id: str
    evidence: CanonicalOrderEvidence
    selected_ref: EvidenceRef
    evidence_refs: tuple[EvidenceRef, ...]
    shadowed_fill_evidence_refs: tuple[EvidenceRef, ...]
    detailed_fill_count: int
    aggregate_shadowed: bool

    def canonical_payload(self) -> dict[str, Any]:
        item = self.evidence
        return {
            "identity": self.identity,
            "instrument": item.instrument.canonical_payload(),
            "side": item.side,
            "status": item.status,
            "ordered_at": item.ordered_at,
            "evidence_level": item.evidence_level,
            "filled_quantity": item.filled_quantity,
            "average_fill_price": item.average_fill_price,
            "amount": item.amount,
            "total_fee": item.total_fee,
            "fee_evidence_status": item.fee_evidence_status,
            "detailed_fill_count": self.detailed_fill_count,
            "aggregate_shadowed": self.aggregate_shadowed,
            "selected_ref": self.selected_ref.canonical_payload(),
            "evidence_refs": [ref.canonical_payload() for ref in self.evidence_refs],
            "shadowed_fill_evidence_refs": [
                ref.canonical_payload()
                for ref in self.shadowed_fill_evidence_refs
            ],
        }


@dataclass(frozen=True)
class CanonicalFill:
    """One deal identity and its builder-ready canonical representation."""

    identity: tuple[str, str, str]
    source_deal_id: str
    source_order_id: Optional[str]
    evidence: CanonicalFillEvidence
    selected_ref: EvidenceRef
    evidence_refs: tuple[EvidenceRef, ...]

    def canonical_payload(self) -> dict[str, Any]:
        item = self.evidence
        return {
            "identity": self.identity,
            "source_order_id": self.source_order_id,
            "instrument": item.instrument.canonical_payload(),
            "side": item.side,
            "filled_at": item.filled_at,
            "quantity": item.quantity,
            "price": item.price,
            "amount": item.amount,
            "total_fee": item.total_fee,
            "linked_order_identity": (
                None
                if self.source_order_id is None
                else (self.identity[0], self.identity[1], self.source_order_id)
            ),
            "selected_ref": self.selected_ref.canonical_payload(),
            "evidence_refs": [ref.canonical_payload() for ref in self.evidence_refs],
        }


@dataclass(frozen=True)
class CanonicalProvenance:
    """Auditable counts and immutable source scope for one reader result."""

    reader_name: str
    reader_version: str
    batch_keys: tuple[str, ...]
    source_kinds: tuple[str, ...]
    input_order_observation_count: int
    input_fill_observation_count: int
    canonical_order_count: int
    canonical_fill_count: int
    duplicate_order_observation_count: int
    duplicate_fill_observation_count: int
    shadowed_fill_observation_count: int
    shadowed_aggregate_order_count: int
    blocking_issue_count: int

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "reader_name": self.reader_name,
            "reader_version": self.reader_version,
            "batch_keys": self.batch_keys,
            "source_kinds": self.source_kinds,
            "input_order_observation_count": self.input_order_observation_count,
            "input_fill_observation_count": self.input_fill_observation_count,
            "canonical_order_count": self.canonical_order_count,
            "canonical_fill_count": self.canonical_fill_count,
            "duplicate_order_observation_count": self.duplicate_order_observation_count,
            "duplicate_fill_observation_count": self.duplicate_fill_observation_count,
            "shadowed_fill_observation_count": self.shadowed_fill_observation_count,
            "shadowed_aggregate_order_count": self.shadowed_aggregate_order_count,
            "blocking_issue_count": self.blocking_issue_count,
        }


@dataclass(frozen=True)
class CanonicalEvidenceSet:
    """Deterministic canonical facts plus all retained provenance and issues."""

    orders: tuple[CanonicalOrder, ...]
    fills: tuple[CanonicalFill, ...]
    issues: tuple[CanonicalIssue, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    provenance: CanonicalProvenance
    analysis_ready: bool
    canonical_set_sha256: str

    @property
    def episode_orders(self) -> tuple[CanonicalOrderEvidence, ...]:
        return tuple(item.evidence for item in self.orders)

    @property
    def episode_fills(self) -> tuple[CanonicalFillEvidence, ...]:
        return tuple(item.evidence for item in self.fills)

    def require_analysis_ready(self) -> "CanonicalEvidenceSet":
        if not self.analysis_ready:
            codes = ", ".join(sorted({issue.code for issue in self.issues}))
            raise CanonicalizationBlockedError(
                f"canonical evidence is not analysis-ready: {codes}"
            )
        return self


def _group_orders(
    observations: Sequence[OrderObservationInput],
) -> dict[tuple[str, str, str], list[OrderObservationInput]]:
    grouped: dict[tuple[str, str, str], list[OrderObservationInput]] = {}
    for item in observations:
        grouped.setdefault(_order_identity(item), []).append(item)
    return grouped


def _deduplicated_fill_rows(
    observations: Sequence[FillObservationInput],
) -> tuple[FillObservationInput, ...]:
    grouped: dict[tuple[str, str, str], list[FillObservationInput]] = {}
    for item in observations:
        grouped.setdefault(_fill_identity(item), []).append(item)
    selected: list[FillObservationInput] = []
    for identity in sorted(grouped):
        item = _preferred(tuple(grouped[identity]))
        assert isinstance(item, FillObservationInput)
        selected.append(item)
    return tuple(selected)


def _resolve_attested_fill_sets(
    observations: Sequence[FillObservationInput],
    issues: list[CanonicalIssue],
) -> tuple[
    tuple[FillObservationInput, ...],
    dict[tuple[str, str, str], tuple[EvidenceRef, ...]],
    int,
]:
    """Shadow weak CSV fill sets only after an explicit order attestation."""
    ordinary: list[FillObservationInput] = []
    attested_by_order: dict[
        tuple[str, str, str], list[FillObservationInput]
    ] = {}
    for item in observations:
        role = item.fill_set_role.strip().lower()
        if role == "ordinary":
            ordinary.append(item)
            continue
        linked = _linked_order_identity(item)
        assert linked is not None  # validated by FillObservationInput
        attested_by_order.setdefault(linked, []).append(item)

    active = list(ordinary)
    shadow_refs_by_order: dict[
        tuple[str, str, str], tuple[EvidenceRef, ...]
    ] = {}
    shadowed_count = 0
    for order_identity in sorted(attested_by_order):
        values = tuple(attested_by_order[order_identity])
        authoritative = tuple(
            item
            for item in values
            if item.fill_set_role.strip().lower() == "authoritative"
        )
        shadows = tuple(
            item
            for item in values
            if item.fill_set_role.strip().lower() == "attested_shadow"
        )
        keys = {
            item.fill_set_attestation_key.strip()
            for item in values
            if item.fill_set_attestation_key is not None
        }
        refs = _refs(values)
        shadow_refs_by_order[order_identity] = _refs(shadows)
        shadowed_count += len(shadows)

        _check_conflicting_fields(
            issues,
            entity_kind="order_fill_set",
            identity=order_identity,
            values=values,
            field_names=(*_INSTRUMENT_FIELDS, "side"),
            issue_code="fill_set_economic_conflict",
        )
        if len(keys) != 1 or not authoritative or not shadows:
            _append_issue(
                issues,
                code="invalid_fill_set_attestation",
                entity_kind="order_fill_set",
                identity=order_identity,
                message=(
                    "fill-set shadowing requires one shared attestation key "
                    "and both authoritative and shadow observations"
                ),
                evidence_refs=refs,
            )
            active.extend(authoritative)
            continue

        authoritative_rows = _deduplicated_fill_rows(authoritative)
        shadow_rows = _deduplicated_fill_rows(shadows)
        authoritative_quantity = sum(
            (item.quantity for item in authoritative_rows),
            _ZERO,
        )
        shadow_quantity = sum(
            (item.quantity for item in shadow_rows),
            _ZERO,
        )
        authoritative_vwap = (
            sum(
                (item.quantity * item.price for item in authoritative_rows),
                _ZERO,
            )
            / authoritative_quantity
        )
        shadow_vwap = (
            sum(
                (item.quantity * item.price for item in shadow_rows),
                _ZERO,
            )
            / shadow_quantity
        )
        if (
            len(authoritative_rows) != len(shadow_rows)
            or abs(authoritative_quantity - shadow_quantity) > _QUANTITY_TOLERANCE
            or abs(authoritative_vwap - shadow_vwap)
            > _vwap_tolerance(authoritative_vwap, shadow_vwap)
        ):
            _append_issue(
                issues,
                code="fill_set_attestation_mismatch",
                entity_kind="order_fill_set",
                identity=order_identity,
                message=(
                    "authoritative and shadow fill sets disagree on "
                    "deduplicated count, quantity or VWAP"
                ),
                evidence_refs=refs,
            )
        # Even a failed attestation never promotes weak shadow rows to
        # canonical executions.  The blocking issue retains all refs while the
        # broker-backed set remains available for diagnosis.
        active.extend(authoritative)

    return tuple(active), shadow_refs_by_order, shadowed_count


def _select_fills(
    observations: Sequence[FillObservationInput],
    issues: list[CanonicalIssue],
) -> dict[tuple[str, str, str], _SelectedFill]:
    grouped: dict[tuple[str, str, str], list[FillObservationInput]] = {}
    for item in observations:
        grouped.setdefault(_fill_identity(item), []).append(item)

    result: dict[tuple[str, str, str], _SelectedFill] = {}
    for identity in sorted(grouped):
        values = tuple(grouped[identity])
        refs = _refs(values)
        if identity[2].startswith("__invalid_fill_observation_"):
            _append_issue(
                issues,
                code="missing_stable_deal_id",
                entity_kind="fill",
                identity=identity,
                message="fill has no stable source_deal_id",
                evidence_refs=refs,
                field_name="source_deal_id",
            )
        if not any(_identity_is_stable(item.identity_strength) for item in values):
            _append_issue(
                issues,
                code="unstable_fill_identity",
                entity_kind="fill",
                identity=identity,
                message="fill identity is not backed by a stable broker/reconciled ID",
                evidence_refs=refs,
                field_name="identity_strength",
            )
        _check_conflicting_fields(
            issues,
            entity_kind="fill",
            identity=identity,
            values=values,
            field_names=(
                "source_order_id",
                *_INSTRUMENT_FIELDS,
                "side",
                "filled_at",
                "quantity",
                "price",
                "amount",
            ),
            issue_code="fill_economic_conflict",
        )
        fee_values = _distinct_values(values, "total_fee")
        if len(fee_values) > 1:
            _append_issue(
                issues,
                code="fee_total_conflict",
                entity_kind="fill",
                identity=identity,
                message="duplicate fill observations disagree on total_fee",
                evidence_refs=refs,
                field_name="total_fee",
            )
        selected = _preferred(values)
        assert isinstance(selected, FillObservationInput)
        linked_identities = {
            linked
            for linked in (_linked_order_identity(item) for item in values)
            if linked is not None
        }
        linked = next(iter(linked_identities)) if len(linked_identities) == 1 else None
        total_fee = selected.total_fee
        if len(fee_values) == 1:
            total_fee = next(iter(fee_values))
        result[identity] = _SelectedFill(
            identity=identity,
            selected=selected,
            observations=values,
            evidence_refs=refs,
            linked_order_identity=linked,
            total_fee=total_fee,
        )
    return result


def _is_authoritative_summary(value: OrderObservationInput) -> bool:
    if value.evidence_level.strip().lower() == "aggregate_only":
        return True
    return _normalized_status(value.status) in {
        "FILLED",
        "FILLED_ALL",
        "CANCELLED_PART",
    }


def _coalesced(
    values: Sequence[OrderObservationInput],
    field_name: str,
) -> Any:
    for item in sorted(values, key=_observation_priority, reverse=True):
        value = getattr(item, field_name)
        if value is not None:
            return value
    return None


def _canonical_order_records(
    order_observations: Sequence[OrderObservationInput],
    fill_selections: dict[tuple[str, str, str], _SelectedFill],
    shadowed_fill_refs_by_order: dict[
        tuple[str, str, str], tuple[EvidenceRef, ...]
    ],
    issues: list[CanonicalIssue],
) -> tuple[tuple[CanonicalOrder, ...], dict[tuple[str, str, str], CanonicalOrder]]:
    groups = _group_orders(order_observations)
    fills_by_order: dict[tuple[str, str, str], list[_SelectedFill]] = {}
    for fill in fill_selections.values():
        if fill.linked_order_identity is not None:
            fills_by_order.setdefault(fill.linked_order_identity, []).append(fill)

    result: list[CanonicalOrder] = []
    by_identity: dict[tuple[str, str, str], CanonicalOrder] = {}
    for identity in sorted(groups):
        values = tuple(groups[identity])
        refs = _refs(values)
        if identity[2].startswith("__invalid_order_observation_"):
            _append_issue(
                issues,
                code="missing_stable_order_id",
                entity_kind="order",
                identity=identity,
                message="order has no stable source_order_id",
                evidence_refs=refs,
                field_name="source_order_id",
            )
        if not any(_identity_is_stable(item.identity_strength) for item in values):
            _append_issue(
                issues,
                code="unstable_order_identity",
                entity_kind="order",
                identity=identity,
                message="order identity is not backed by a stable broker/reconciled ID",
                evidence_refs=refs,
                field_name="identity_strength",
            )
        _check_conflicting_fields(
            issues,
            entity_kind="order",
            identity=identity,
            values=values,
            field_names=(
                *_INSTRUMENT_FIELDS,
                "side",
                "ordered_at",
                "order_quantity",
                "order_price",
            ),
            issue_code="order_economic_conflict",
        )
        fee_values = _distinct_values(values, "total_fee")
        if len(fee_values) > 1:
            _append_issue(
                issues,
                code="fee_total_conflict",
                entity_kind="order",
                identity=identity,
                message="duplicate order observations disagree on total_fee",
                evidence_refs=refs,
                field_name="total_fee",
            )

        linked_fills = sorted(
            fills_by_order.get(identity, []),
            key=lambda item: item.identity,
        )
        aggregate_values = [
            item
            for item in values
            if item.evidence_level.strip().lower() == "aggregate_only"
        ]
        if linked_fills:
            selected = _preferred(values)
        elif aggregate_values:
            selected = _preferred(aggregate_values)
        else:
            selected = _preferred(values)
        assert isinstance(selected, OrderObservationInput)

        instrument_inputs: list[OrderObservationInput | FillObservationInput] = [
            *values
        ]
        instrument_inputs.extend(fill.selected for fill in linked_fills)
        _check_conflicting_fields(
            issues,
            entity_kind="order",
            identity=identity,
            values=instrument_inputs,
            field_names=(*_INSTRUMENT_FIELDS, "side"),
            issue_code="order_fill_conflict",
        )
        instrument = _resolved_instrument(instrument_inputs, preferred=selected)

        if linked_fills:
            filled_quantity = sum(
                (fill.selected.quantity for fill in linked_fills),
                _ZERO,
            )
            weighted = sum(
                (
                    fill.selected.quantity * fill.selected.price
                    for fill in linked_fills
                ),
                _ZERO,
            )
            average_price = weighted / filled_quantity
            evidence_level = "fill_detail"
            amount = None
            for item in values:
                if not _is_authoritative_summary(item):
                    continue
                if (
                    item.summary_filled_quantity is not None
                    and item.summary_filled_quantity != filled_quantity
                ):
                    _append_issue(
                        issues,
                        code="fill_summary_mismatch",
                        entity_kind="order",
                        identity=identity,
                        message="detailed fill quantity disagrees with final order summary",
                        evidence_refs=tuple(
                            sorted(
                                {*refs, *(ref for fill in linked_fills for ref in fill.evidence_refs)},
                                key=lambda ref: ref.sort_key,
                            )
                        ),
                        field_name="summary_filled_quantity",
                    )
                if (
                    item.summary_average_fill_price is not None
                    and abs(item.summary_average_fill_price - average_price)
                    > _vwap_tolerance(
                        item.summary_average_fill_price,
                        average_price,
                    )
                ):
                    _append_issue(
                        issues,
                        code="fill_summary_mismatch",
                        entity_kind="order",
                        identity=identity,
                        message="detailed fill VWAP disagrees with final order summary",
                        evidence_refs=tuple(
                            sorted(
                                {*refs, *(ref for fill in linked_fills for ref in fill.evidence_refs)},
                                key=lambda ref: ref.sort_key,
                            )
                        ),
                        field_name="summary_average_fill_price",
                    )
        else:
            evidence_level = selected.evidence_level.strip().lower()
            filled_quantity = _coalesced(values, "summary_filled_quantity")
            average_price = _coalesced(values, "summary_average_fill_price")
            amount = _coalesced(values, "order_amount")
            if any(
                item.evidence_level.strip().lower() == "fill_detail"
                and (item.summary_filled_quantity or _ZERO) > 0
                for item in values
            ):
                _append_issue(
                    issues,
                    code="missing_detail_fills",
                    entity_kind="order",
                    identity=identity,
                    message="detail-backed filled order has no canonical fills",
                    evidence_refs=refs,
                )

        total_fee = selected.total_fee
        if len(fee_values) == 1:
            total_fee = next(iter(fee_values))
        fee_status = selected.fee_evidence_status.strip().lower()
        if total_fee is not None:
            fee_status = "complete"
        order_evidence = CanonicalOrderEvidence(
            observation_id=selected.observation_id,
            observation_key=selected.observation_key,
            instrument=instrument,
            side=_normalized_side(selected.side),
            status=_normalized_status(selected.status),
            ordered_at=_aware_utc(selected.ordered_at, field_name="ordered_at"),
            source_sequence=_stable_source_sequence(selected),
            evidence_level=evidence_level,
            filled_quantity=filled_quantity,
            average_fill_price=average_price,
            amount=amount,
            total_fee=total_fee,
            fee_evidence_status=fee_status,
        )
        canonical = CanonicalOrder(
            identity=identity,
            source_order_id=identity[2],
            evidence=order_evidence,
            selected_ref=_ref(selected),
            evidence_refs=refs,
            shadowed_fill_evidence_refs=shadowed_fill_refs_by_order.get(
                identity,
                (),
            ),
            detailed_fill_count=len(linked_fills),
            aggregate_shadowed=bool(linked_fills and aggregate_values),
        )
        result.append(canonical)
        by_identity[identity] = canonical
    return tuple(result), by_identity


def _canonical_fill_records(
    selections: dict[tuple[str, str, str], _SelectedFill],
    orders: dict[tuple[str, str, str], CanonicalOrder],
    issues: list[CanonicalIssue],
) -> tuple[CanonicalFill, ...]:
    result: list[CanonicalFill] = []
    for identity in sorted(selections):
        selection = selections[identity]
        selected = selection.selected
        order = (
            orders.get(selection.linked_order_identity)
            if selection.linked_order_identity is not None
            else None
        )
        if order is None:
            _append_issue(
                issues,
                code="orphan_fill",
                entity_kind="fill",
                identity=identity,
                message="fill does not resolve to a canonical order in the same broker/account",
                evidence_refs=selection.evidence_refs,
                field_name="source_order_id",
            )
            instrument = _resolved_instrument(
                selection.observations,
                preferred=selected,
            )
            order_observation_id = None
            source_order_id = (
                selection.linked_order_identity[2]
                if selection.linked_order_identity is not None
                else None
            )
        else:
            instrument = order.evidence.instrument
            order_observation_id = order.evidence.observation_id
            source_order_id = order.source_order_id
        evidence = CanonicalFillEvidence(
            observation_id=selected.observation_id,
            observation_key=selected.observation_key,
            instrument=instrument,
            side=_normalized_side(selected.side),
            filled_at=_aware_utc(selected.filled_at, field_name="filled_at"),
            source_sequence=_stable_source_sequence(selected),
            quantity=selected.quantity,
            price=selected.price,
            amount=selected.amount,
            total_fee=selection.total_fee,
            broker_order_observation_id=order_observation_id,
        )
        result.append(
            CanonicalFill(
                identity=identity,
                source_deal_id=identity[2],
                source_order_id=source_order_id,
                evidence=evidence,
                selected_ref=_ref(selected),
                evidence_refs=selection.evidence_refs,
            )
        )
    return tuple(result)


def _check_linked_fees(
    orders: Sequence[CanonicalOrder],
    fills: Sequence[CanonicalFill],
    issues: list[CanonicalIssue],
) -> None:
    fills_by_order: dict[tuple[str, str, str], list[CanonicalFill]] = {}
    for fill in fills:
        if fill.source_order_id is None:
            continue
        key = (fill.identity[0], fill.identity[1], fill.source_order_id)
        fills_by_order.setdefault(key, []).append(fill)
    for order in orders:
        linked = fills_by_order.get(order.identity, [])
        if not linked:
            continue
        direct_fees = [fill.evidence.total_fee for fill in linked]
        populated = [fee for fee in direct_fees if fee is not None]
        all_refs = tuple(
            sorted(
                {
                    *order.evidence_refs,
                    *(ref for fill in linked for ref in fill.evidence_refs),
                },
                key=lambda ref: ref.sort_key,
            )
        )
        if populated and len(populated) != len(direct_fees):
            _append_issue(
                issues,
                code="partial_fill_fee_evidence",
                entity_kind="order",
                identity=order.identity,
                message="linked fills have only partially populated fee evidence",
                evidence_refs=all_refs,
                field_name="total_fee",
            )
            continue
        if (
            order.evidence.total_fee is not None
            and len(populated) == len(direct_fees)
            and sum(populated, _ZERO) != order.evidence.total_fee
        ):
            _append_issue(
                issues,
                code="fee_total_mismatch",
                entity_kind="order",
                identity=order.identity,
                message="sum of canonical fill fees disagrees with order total_fee",
                evidence_refs=all_refs,
                field_name="total_fee",
            )


def canonicalize_observations(
    order_observations: Iterable[OrderObservationInput],
    fill_observations: Iterable[FillObservationInput],
) -> CanonicalEvidenceSet:
    """Merge overlapping immutable observations into one auditable fact set.

    Input ordering never affects output ordering or ``canonical_set_sha256``.
    Database writes are intentionally outside this function.  A blocked result
    still contains the deterministic selected facts for diagnosis, but callers
    must check ``analysis_ready`` (or call ``require_analysis_ready``) before
    handing the evidence to an episode build.
    """
    order_inputs = tuple(order_observations)
    fill_inputs = tuple(fill_observations)
    issues: list[CanonicalIssue] = []

    (
        active_fill_inputs,
        shadowed_fill_refs_by_order,
        shadowed_fill_observation_count,
    ) = _resolve_attested_fill_sets(fill_inputs, issues)
    fill_selections = _select_fills(active_fill_inputs, issues)
    orders, order_by_identity = _canonical_order_records(
        order_inputs,
        fill_selections,
        shadowed_fill_refs_by_order,
        issues,
    )
    fills = _canonical_fill_records(
        fill_selections,
        order_by_identity,
        issues,
    )
    _check_linked_fees(orders, fills, issues)

    issues_tuple = tuple(sorted(set(issues), key=lambda issue: issue.sort_key))
    all_refs = tuple(
        sorted(
            {_ref(item) for item in (*order_inputs, *fill_inputs)},
            key=lambda item: item.sort_key,
        )
    )
    batch_keys = tuple(sorted({item.batch_key for item in all_refs}))
    source_kinds = tuple(sorted({item.source_kind for item in all_refs}))
    provenance = CanonicalProvenance(
        reader_name=CANONICAL_READER_NAME,
        reader_version=CANONICAL_READER_VERSION,
        batch_keys=batch_keys,
        source_kinds=source_kinds,
        input_order_observation_count=len(order_inputs),
        input_fill_observation_count=len(fill_inputs),
        canonical_order_count=len(orders),
        canonical_fill_count=len(fills),
        duplicate_order_observation_count=max(0, len(order_inputs) - len(orders)),
        duplicate_fill_observation_count=max(
            0,
            len(active_fill_inputs) - len(fills),
        ),
        shadowed_fill_observation_count=shadowed_fill_observation_count,
        shadowed_aggregate_order_count=sum(
            item.aggregate_shadowed for item in orders
        ),
        blocking_issue_count=sum(
            issue.severity == "blocking" for issue in issues_tuple
        ),
    )
    analysis_ready = provenance.blocking_issue_count == 0
    payload = {
        "analysis_ready": analysis_ready,
        "provenance": provenance.canonical_payload(),
        "orders": [item.canonical_payload() for item in orders],
        "fills": [item.canonical_payload() for item in fills],
        "issues": [item.canonical_payload() for item in issues_tuple],
    }
    canonical_hash = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return CanonicalEvidenceSet(
        orders=orders,
        fills=fills,
        issues=issues_tuple,
        evidence_refs=all_refs,
        provenance=provenance,
        analysis_ready=analysis_ready,
        canonical_set_sha256=canonical_hash,
    )
