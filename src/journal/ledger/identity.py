# -*- coding: utf-8 -*-
"""Pure identity-link and fill-set attestation projection for Journal v2.

Moomoo's CSV statement does not contain broker order/deal IDs.  The existing
statement/OpenAPI reconciliation can nevertheless prove an order link from a
unique four-part key (instrument, side, requested quantity and second-level
order time).  This module turns *only a passed reconciliation* into explicit
aliases consumed by :mod:`src.journal.ledger.canonical`.

Deal identity is deliberately stricter than order identity.  CSV/API fills are
linked one-to-one only when every row has a unique, exact economic fingerprint
within the already-linked order.  Timestamps are never rounded or used to
break ties.  If a complete fill set agrees on count, quantity and VWAP but the
individual rows are ambiguous, the broker-ID-backed OpenAPI set is marked
``authoritative`` and the CSV set is retained as an ``attested_shadow``.

This is a database-neutral projection: it imports no storage, ORM, SDK or trade
module and it never mutates the immutable source observations.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from src.journal.brokers.moomoo_statement import StatementReconciliation
from src.journal.ledger.canonical import (
    FillObservationInput,
    OrderObservationInput,
)

__all__ = [
    "IDENTITY_PROJECTION_NAME",
    "IDENTITY_PROJECTION_VERSION",
    "DealIdentityLink",
    "AcceptedCsvBatchSelection",
    "FillSetAttestation",
    "FillSetSummary",
    "IdentityProjection",
    "IdentityProjectionError",
    "ObservationProvenance",
    "OrderIdentityLink",
    "project_reconciled_identities",
]


IDENTITY_PROJECTION_NAME = "moomoo_reconciled_identity_projection"
IDENTITY_PROJECTION_VERSION = "1.0.0"
_API_SOURCE_KINDS = frozenset({"api", "openapi", "moomoo_openapi"})
_QUANTITY_TOLERANCE = Decimal("0.00000001")
_VWAP_TOLERANCE = Decimal("0.000001")
_ZERO = Decimal("0")


class IdentityProjectionError(ValueError):
    """Raised when reconciliation evidence cannot support a safe projection."""


@dataclass(frozen=True)
class AcceptedCsvBatchSelection:
    """One caller-selected, already-accepted full CSV import batch.

    Acceptance is a persistence-layer decision and is intentionally not
    inferred here.  Supplying this DTO is the caller's explicit assertion that
    the batch is the single accepted CSV baseline for the canonical read.
    """

    import_batch_id: int
    batch_key: str

    def __post_init__(self) -> None:
        if self.import_batch_id <= 0:
            raise IdentityProjectionError(
                "selected CSV import_batch_id must be positive"
            )
        if not self.batch_key.strip():
            raise IdentityProjectionError("selected CSV batch_key cannot be empty")


def _source_kind(value: str) -> str:
    normalized = value.strip().lower()
    return "openapi" if normalized in _API_SOURCE_KINDS else normalized


def _scope(value: OrderObservationInput | FillObservationInput) -> tuple[str, str]:
    return value.broker.strip().lower(), value.account_key.strip()


def _normalise_symbol(value: str) -> str:
    normalized = value.strip().upper()
    if "." in normalized:
        prefix, remainder = normalized.split(".", 1)
        if prefix in {"US", "HK", "SH", "SZ"} and remainder:
            return remainder
    return normalized


def _normalise_side(value: str) -> str:
    normalized = value.strip().upper().replace(" ", "_")
    return {
        "SHORT_SELL": "SELL_SHORT",
        "BUY_TO_COVER": "BUY_BACK",
    }.get(normalized, normalized)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_key(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode()).hexdigest()
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class ObservationProvenance:
    """Stable source reference; local database integer IDs are informational."""

    observation_id: int
    import_batch_id: int
    batch_key: str
    source_kind: str
    observation_key: str
    source_record_sha256: str

    @classmethod
    def from_observation(
        cls,
        value: OrderObservationInput | FillObservationInput,
    ) -> "ObservationProvenance":
        return cls(
            observation_id=value.observation_id,
            import_batch_id=value.import_batch_id,
            batch_key=value.batch_key,
            source_kind=_source_kind(value.source_kind),
            observation_key=value.observation_key,
            source_record_sha256=value.source_record_sha256.lower(),
        )

    @property
    def stable_key(self) -> tuple[str, ...]:
        return (
            self.batch_key,
            self.source_kind,
            self.observation_key,
            self.source_record_sha256,
        )

    def stable_payload(self) -> dict[str, str]:
        return {
            "batch_key": self.batch_key,
            "source_kind": self.source_kind,
            "observation_key": self.observation_key,
            "source_record_sha256": self.source_record_sha256,
        }


def _refs(
    values: Iterable[OrderObservationInput | FillObservationInput],
) -> tuple[ObservationProvenance, ...]:
    unique = {ObservationProvenance.from_observation(value) for value in values}
    return tuple(sorted(unique, key=lambda item: item.stable_key))


@dataclass(frozen=True)
class OrderIdentityLink:
    """A CSV-derived order ID linked to one broker order ID."""

    link_key: str
    broker: str
    account_key: str
    statement_order_id: str
    broker_order_id: str
    proof_kind: str
    statement_evidence: tuple[ObservationProvenance, ...]
    api_evidence: tuple[ObservationProvenance, ...]


@dataclass(frozen=True)
class DealIdentityLink:
    """An exact, unique CSV/API fill link inside a proven order link."""

    link_key: str
    broker: str
    account_key: str
    canonical_order_id: str
    statement_deal_id: str
    broker_deal_id: str
    quantity: Decimal
    price: Decimal
    proof_kind: str
    statement_evidence: tuple[ObservationProvenance, ...]
    api_evidence: tuple[ObservationProvenance, ...]


@dataclass(frozen=True)
class FillSetSummary:
    """Deduplicated economic totals used by an order-level attestation."""

    count: int
    quantity: Decimal
    notional: Decimal
    vwap: Optional[Decimal]

    def stable_payload(self) -> dict[str, Optional[str] | int]:
        return {
            "count": self.count,
            "quantity": _decimal_text(self.quantity),
            "notional": _decimal_text(self.notional),
            "vwap": None if self.vwap is None else _decimal_text(self.vwap),
        }


@dataclass(frozen=True)
class FillSetAttestation:
    """Order-level proof that OpenAPI replaces an ambiguous CSV fill set."""

    attestation_key: str
    broker: str
    account_key: str
    canonical_order_id: str
    statement_order_id: str
    csv_summary: FillSetSummary
    api_summary: FillSetSummary
    proof_kind: str
    statement_evidence: tuple[ObservationProvenance, ...]
    api_evidence: tuple[ObservationProvenance, ...]


@dataclass(frozen=True)
class IdentityProjection:
    """Canonical-reader inputs plus the explicit proof objects behind aliases."""

    orders: tuple[OrderObservationInput, ...]
    fills: tuple[FillObservationInput, ...]
    order_links: tuple[OrderIdentityLink, ...]
    deal_links: tuple[DealIdentityLink, ...]
    fill_set_attestations: tuple[FillSetAttestation, ...]
    selected_csv_batch: Optional[AcceptedCsvBatchSelection]
    selected_batch_stable_fill_count: int


def _project_selected_csv_batch_fills(
    *,
    orders: Sequence[OrderObservationInput],
    fills: Sequence[FillObservationInput],
    selection: Optional[AcceptedCsvBatchSelection],
    reconciliation: StatementReconciliation,
) -> tuple[list[FillObservationInput], int]:
    """Scope weak CSV fill identities to one explicit accepted full batch.

    ``selected_batch_stable`` is *not* a broker identity and must never be
    interpreted as cross-batch evidence.  It only says a deterministic CSV
    fill key is unique enough inside the one full batch selected by the caller.
    Fills belonging to reconciled overlap orders remain weak here; they are
    handled later by broker deal links or authoritative/shadow attestations.
    """

    projected = list(fills)
    if selection is None:
        return projected, 0

    csv_rows: tuple[OrderObservationInput | FillObservationInput, ...] = tuple(
        value
        for value in (*orders, *fills)
        if _source_kind(value.source_kind) == "csv"
    )
    if not csv_rows:
        raise IdentityProjectionError("selected CSV batch has no observations")
    csv_batch_refs = {
        (value.import_batch_id, value.batch_key) for value in csv_rows
    }
    expected_ref = (selection.import_batch_id, selection.batch_key)
    if csv_batch_refs != {expected_ref}:
        raise IdentityProjectionError(
            "identity projection requires exactly the selected accepted CSV batch"
        )

    overlap_order_ids = {
        match.statement_order_id for match in reconciliation.matches
    }
    promoted = 0
    for index, value in enumerate(projected):
        if (
            _source_kind(value.source_kind) != "csv"
            or value.import_batch_id != selection.import_batch_id
            or value.batch_key != selection.batch_key
            or value.source_order_id in overlap_order_ids
            or value.identity_strength.strip().lower() != "derived_weak"
        ):
            continue
        # The full CSV baseline extends beyond the partial OpenAPI probe.  Only
        # rows strictly outside that reconciliation window receive this local
        # single-batch strength; in-window rows need broker-backed proof.
        if reconciliation.window_start <= value.filled_at <= reconciliation.window_end:
            continue
        projected[index] = replace(
            value,
            identity_strength="selected_batch_stable",
        )
        promoted += 1
    return projected, promoted


def _validate_reconciliation(reconciliation: StatementReconciliation) -> None:
    if not reconciliation.analysis_ready:
        raise IdentityProjectionError(
            "identity projection requires an analysis-ready reconciliation"
        )
    count_fields = (
        "statement_orders",
        "api_orders",
        "matched_orders",
        "statement_only_orders",
        "api_only_orders",
        "overlap_api_only_orders",
        "incremental_api_only_orders",
    )
    if any(getattr(reconciliation, field_name) < 0 for field_name in count_fields):
        raise IdentityProjectionError(
            "analysis-ready reconciliation contains negative order counts"
        )
    if (
        reconciliation.api_only_orders
        != reconciliation.overlap_api_only_orders
        + reconciliation.incremental_api_only_orders
    ):
        raise IdentityProjectionError(
            "analysis-ready reconciliation has unclassified API-only orders"
        )
    if (
        reconciliation.statement_orders
        != reconciliation.matched_orders + reconciliation.statement_only_orders
        or reconciliation.api_orders
        != reconciliation.matched_orders + reconciliation.api_only_orders
    ):
        raise IdentityProjectionError(
            "analysis-ready reconciliation contains inconsistent order counts"
        )
    mismatch_fields = (
        "statement_only_orders",
        "overlap_api_only_orders",
        "status_mismatches",
        "fill_count_mismatches",
        "filled_quantity_mismatches",
        "fill_vwap_mismatches",
        "fee_total_mismatches",
        "fee_component_mismatches",
        "ambiguous_identity_keys",
    )
    mismatches = [
        field_name
        for field_name in mismatch_fields
        if getattr(reconciliation, field_name) != 0
    ]
    if mismatches:
        raise IdentityProjectionError(
            "analysis-ready reconciliation contains mismatches: "
            + ", ".join(mismatches)
        )
    if len(reconciliation.matches) != reconciliation.matched_orders:
        raise IdentityProjectionError(
            "reconciliation match evidence is incomplete"
        )
    empty_overlap = (
        reconciliation.statement_orders == 0
        and reconciliation.overlap_api_only_orders == 0
    )
    if reconciliation.matched_orders <= 0 and not empty_overlap:
        raise IdentityProjectionError("reconciliation contains no proven order links")
    statement_ids = [item.statement_order_id for item in reconciliation.matches]
    broker_ids = [item.broker_order_id for item in reconciliation.matches]
    if (
        any(not value.strip() for value in statement_ids + broker_ids)
        or len(statement_ids) != len(set(statement_ids))
        or len(broker_ids) != len(set(broker_ids))
    ):
        raise IdentityProjectionError(
            "reconciliation order links are empty or not one-to-one"
        )


def _source_rows(
    values: Sequence[OrderObservationInput | FillObservationInput],
    *,
    source_kind: str,
    source_order_id: str,
    scope: Optional[tuple[str, str]] = None,
) -> tuple[OrderObservationInput | FillObservationInput, ...]:
    return tuple(
        value
        for value in values
        if _source_kind(value.source_kind) == source_kind
        and value.source_order_id == source_order_id
        and (scope is None or _scope(value) == scope)
    )


def _deduplicate_fills(
    values: Sequence[FillObservationInput],
) -> tuple[FillObservationInput, ...]:
    grouped: dict[str, list[FillObservationInput]] = defaultdict(list)
    for value in values:
        if not value.source_deal_id.strip():
            raise IdentityProjectionError("fill source_deal_id cannot be empty")
        grouped[value.source_deal_id].append(value)

    result: list[FillObservationInput] = []
    for source_deal_id in sorted(grouped):
        rows = grouped[source_deal_id]
        economic_keys = {
            (
                _normalise_symbol(row.raw_symbol),
                _normalise_side(row.side),
                row.currency.strip().upper(),
                row.quantity,
                row.price,
            )
            for row in rows
        }
        if len(economic_keys) != 1:
            raise IdentityProjectionError(
                f"duplicate fill identity has conflicting economics: {source_deal_id}"
            )
        result.append(
            max(
                rows,
                key=lambda row: (
                    row.source_updated_at or row.recorded_at,
                    row.batch_key,
                    row.observation_key,
                ),
            )
        )
    return tuple(result)


def _fill_summary(values: Sequence[FillObservationInput]) -> FillSetSummary:
    quantity = sum((value.quantity for value in values), _ZERO)
    notional = sum((value.quantity * value.price for value in values), _ZERO)
    return FillSetSummary(
        count=len(values),
        quantity=quantity,
        notional=notional,
        vwap=None if quantity == 0 else notional / quantity,
    )


def _same_fill_set(
    csv_rows: Sequence[FillObservationInput],
    api_rows: Sequence[FillObservationInput],
) -> tuple[FillSetSummary, FillSetSummary]:
    csv_summary = _fill_summary(csv_rows)
    api_summary = _fill_summary(api_rows)
    csv_instruments = {
        (
            _normalise_symbol(row.raw_symbol),
            _normalise_side(row.side),
            row.currency.strip().upper(),
        )
        for row in csv_rows
    }
    api_instruments = {
        (
            _normalise_symbol(row.raw_symbol),
            _normalise_side(row.side),
            row.currency.strip().upper(),
        )
        for row in api_rows
    }
    vwap_matches = (
        csv_summary.vwap is None
        and api_summary.vwap is None
    ) or (
        csv_summary.vwap is not None
        and api_summary.vwap is not None
        and abs(csv_summary.vwap - api_summary.vwap) <= _VWAP_TOLERANCE
    )
    if (
        len(csv_instruments) != 1
        or csv_instruments != api_instruments
        or csv_summary.count != api_summary.count
        or abs(csv_summary.quantity - api_summary.quantity) > _QUANTITY_TOLERANCE
        or not vwap_matches
    ):
        raise IdentityProjectionError(
            "reconciled fill sets disagree on instrument, count, quantity or VWAP"
        )
    return csv_summary, api_summary


def _fill_fingerprint(value: FillObservationInput) -> tuple[Any, ...]:
    # Deliberately excludes filled_at: CSV strips fractional seconds, and time
    # proximity is not independent proof of a broker deal identity.
    return (
        _normalise_symbol(value.raw_symbol),
        _normalise_side(value.side),
        value.currency.strip().upper(),
        value.quantity,
        value.price,
    )


def _unique_fill_bijection(
    csv_rows: Sequence[FillObservationInput],
    api_rows: Sequence[FillObservationInput],
) -> Optional[tuple[tuple[FillObservationInput, FillObservationInput], ...]]:
    csv_by_key: dict[tuple[Any, ...], list[FillObservationInput]] = defaultdict(list)
    api_by_key: dict[tuple[Any, ...], list[FillObservationInput]] = defaultdict(list)
    for value in csv_rows:
        csv_by_key[_fill_fingerprint(value)].append(value)
    for value in api_rows:
        api_by_key[_fill_fingerprint(value)].append(value)
    if set(csv_by_key) != set(api_by_key):
        return None
    if any(
        len(csv_by_key[key]) != 1 or len(api_by_key[key]) != 1
        for key in csv_by_key
    ):
        return None
    pairs = tuple(
        (csv_by_key[key][0], api_by_key[key][0])
        for key in sorted(csv_by_key, key=lambda item: tuple(map(str, item)))
    )
    # Time is never used to choose between otherwise ambiguous deals, but it
    # remains a consistency veto for a proposed one-to-one alias.  CSV stores
    # seconds while OpenAPI retains fractions; a different whole second is
    # therefore not safe deal-identity evidence.  The caller will retain the
    # economically equivalent rows through an order-level fill-set attestation
    # and keep the API timestamps as authoritative instead.
    if any(
        csv.filled_at.replace(microsecond=0)
        != api.filled_at.replace(microsecond=0)
        for csv, api in pairs
    ):
        return None
    return pairs


def _link_payload(
    *,
    scope: tuple[str, str],
    statement_order_id: str,
    broker_order_id: str,
    statement_refs: Sequence[ObservationProvenance],
    api_refs: Sequence[ObservationProvenance],
) -> dict[str, Any]:
    return {
        "projection": IDENTITY_PROJECTION_NAME,
        "version": IDENTITY_PROJECTION_VERSION,
        "broker": scope[0],
        "account_key": scope[1],
        "statement_order_id": statement_order_id,
        "broker_order_id": broker_order_id,
        "statement_evidence": [item.stable_payload() for item in statement_refs],
        "api_evidence": [item.stable_payload() for item in api_refs],
    }


def project_reconciled_identities(
    *,
    reconciliation: StatementReconciliation,
    order_observations: Iterable[OrderObservationInput],
    fill_observations: Iterable[FillObservationInput],
    selected_csv_batch: Optional[AcceptedCsvBatchSelection] = None,
) -> IdentityProjection:
    """Project strict order/deal links and ambiguous fill-set attestations.

    Unmatched observations pass through unchanged.  A matched order is
    projected only when the reconciliation is fully analysis-ready and both
    source identities resolve to exactly one broker/account scope.
    """

    _validate_reconciliation(reconciliation)
    orders = tuple(order_observations)
    fills = tuple(fill_observations)
    projected_orders = list(orders)
    projected_fills, selected_batch_stable_fill_count = (
        _project_selected_csv_batch_fills(
            orders=orders,
            fills=fills,
            selection=selected_csv_batch,
            reconciliation=reconciliation,
        )
    )
    order_links: list[OrderIdentityLink] = []
    deal_links: list[DealIdentityLink] = []
    attestations: list[FillSetAttestation] = []

    for match in sorted(
        reconciliation.matches,
        key=lambda item: (item.broker_order_id, item.statement_order_id),
    ):
        csv_order_rows = tuple(
            value
            for value in _source_rows(
                orders,
                source_kind="csv",
                source_order_id=match.statement_order_id,
            )
            if isinstance(value, OrderObservationInput)
        )
        api_order_rows = tuple(
            value
            for value in _source_rows(
                orders,
                source_kind="openapi",
                source_order_id=match.broker_order_id,
            )
            if isinstance(value, OrderObservationInput)
        )
        if not csv_order_rows or not api_order_rows:
            raise IdentityProjectionError(
                "proven order link is missing CSV or OpenAPI observations"
            )
        csv_scopes = {_scope(value) for value in csv_order_rows}
        api_scopes = {_scope(value) for value in api_order_rows}
        if len(csv_scopes) != 1 or csv_scopes != api_scopes:
            raise IdentityProjectionError(
                "proven order link does not resolve to one broker/account scope"
            )
        scope = next(iter(csv_scopes))
        csv_order_refs = _refs(csv_order_rows)
        api_order_refs = _refs(api_order_rows)
        order_payload = _link_payload(
            scope=scope,
            statement_order_id=match.statement_order_id,
            broker_order_id=match.broker_order_id,
            statement_refs=csv_order_refs,
            api_refs=api_order_refs,
        )
        order_links.append(
            OrderIdentityLink(
                link_key=_hash_key("order_link", order_payload),
                broker=scope[0],
                account_key=scope[1],
                statement_order_id=match.statement_order_id,
                broker_order_id=match.broker_order_id,
                proof_kind="passed_statement_openapi_reconciliation",
                statement_evidence=csv_order_refs,
                api_evidence=api_order_refs,
            )
        )
        for index, value in enumerate(projected_orders):
            if (
                _scope(value) == scope
                and (
                    (
                        _source_kind(value.source_kind) == "csv"
                        and value.source_order_id == match.statement_order_id
                    )
                    or (
                        _source_kind(value.source_kind) == "openapi"
                        and value.source_order_id == match.broker_order_id
                    )
                )
            ):
                projected_orders[index] = replace(
                    value,
                    canonical_order_id=match.broker_order_id,
                )

        csv_fill_rows_all = tuple(
            value
            for value in _source_rows(
                fills,
                source_kind="csv",
                source_order_id=match.statement_order_id,
                scope=scope,
            )
            if isinstance(value, FillObservationInput)
        )
        api_fill_rows_all = tuple(
            value
            for value in _source_rows(
                fills,
                source_kind="openapi",
                source_order_id=match.broker_order_id,
                scope=scope,
            )
            if isinstance(value, FillObservationInput)
        )
        if not csv_fill_rows_all and not api_fill_rows_all:
            continue
        if not csv_fill_rows_all or not api_fill_rows_all:
            raise IdentityProjectionError(
                "reconciled executed order is missing one source fill set"
            )
        csv_fill_rows = _deduplicate_fills(csv_fill_rows_all)
        api_fill_rows = _deduplicate_fills(api_fill_rows_all)
        csv_summary, api_summary = _same_fill_set(csv_fill_rows, api_fill_rows)
        bijection = _unique_fill_bijection(csv_fill_rows, api_fill_rows)

        if bijection is not None:
            broker_by_csv_id = {
                csv_row.source_deal_id: api_row.source_deal_id
                for csv_row, api_row in bijection
            }
            for csv_row, api_row in bijection:
                csv_refs = _refs(
                    value
                    for value in csv_fill_rows_all
                    if value.source_deal_id == csv_row.source_deal_id
                )
                api_refs = _refs(
                    value
                    for value in api_fill_rows_all
                    if value.source_deal_id == api_row.source_deal_id
                )
                payload = {
                    **order_payload,
                    "statement_deal_id": csv_row.source_deal_id,
                    "broker_deal_id": api_row.source_deal_id,
                    "quantity": _decimal_text(csv_row.quantity),
                    "price": _decimal_text(csv_row.price),
                    "statement_fill_evidence": [
                        item.stable_payload() for item in csv_refs
                    ],
                    "api_fill_evidence": [item.stable_payload() for item in api_refs],
                }
                deal_links.append(
                    DealIdentityLink(
                        link_key=_hash_key("deal_link", payload),
                        broker=scope[0],
                        account_key=scope[1],
                        canonical_order_id=match.broker_order_id,
                        statement_deal_id=csv_row.source_deal_id,
                        broker_deal_id=api_row.source_deal_id,
                        quantity=csv_row.quantity,
                        price=csv_row.price,
                        proof_kind="unique_exact_economic_fingerprint_within_order",
                        statement_evidence=csv_refs,
                        api_evidence=api_refs,
                    )
                )
            for index, value in enumerate(projected_fills):
                if _scope(value) != scope:
                    continue
                if (
                    _source_kind(value.source_kind) == "csv"
                    and value.source_order_id == match.statement_order_id
                ):
                    projected_fills[index] = replace(
                        value,
                        canonical_order_id=match.broker_order_id,
                        canonical_deal_id=broker_by_csv_id[value.source_deal_id],
                    )
                elif (
                    _source_kind(value.source_kind) == "openapi"
                    and value.source_order_id == match.broker_order_id
                ):
                    projected_fills[index] = replace(
                        value,
                        canonical_order_id=match.broker_order_id,
                        canonical_deal_id=value.source_deal_id,
                    )
            continue

        csv_fill_refs = _refs(csv_fill_rows_all)
        api_fill_refs = _refs(api_fill_rows_all)
        attestation_payload = {
            **order_payload,
            "csv_summary": csv_summary.stable_payload(),
            "api_summary": api_summary.stable_payload(),
            "statement_fill_evidence": [
                item.stable_payload() for item in csv_fill_refs
            ],
            "api_fill_evidence": [item.stable_payload() for item in api_fill_refs],
        }
        attestation_key = _hash_key("fill_set", attestation_payload)
        attestations.append(
            FillSetAttestation(
                attestation_key=attestation_key,
                broker=scope[0],
                account_key=scope[1],
                canonical_order_id=match.broker_order_id,
                statement_order_id=match.statement_order_id,
                csv_summary=csv_summary,
                api_summary=api_summary,
                proof_kind="equivalent_count_quantity_vwap_without_deal_bijection",
                statement_evidence=csv_fill_refs,
                api_evidence=api_fill_refs,
            )
        )
        for index, value in enumerate(projected_fills):
            if _scope(value) != scope:
                continue
            if (
                _source_kind(value.source_kind) == "csv"
                and value.source_order_id == match.statement_order_id
            ):
                projected_fills[index] = replace(
                    value,
                    canonical_order_id=match.broker_order_id,
                    fill_set_role="attested_shadow",
                    fill_set_attestation_key=attestation_key,
                )
            elif (
                _source_kind(value.source_kind) == "openapi"
                and value.source_order_id == match.broker_order_id
            ):
                projected_fills[index] = replace(
                    value,
                    canonical_order_id=match.broker_order_id,
                    canonical_deal_id=value.source_deal_id,
                    fill_set_role="authoritative",
                    fill_set_attestation_key=attestation_key,
                )

    return IdentityProjection(
        orders=tuple(projected_orders),
        fills=tuple(projected_fills),
        order_links=tuple(order_links),
        deal_links=tuple(deal_links),
        fill_set_attestations=tuple(attestations),
        selected_csv_batch=selected_csv_batch,
        selected_batch_stable_fill_count=selected_batch_stable_fill_count,
    )
