# -*- coding: utf-8 -*-
"""Pure, deterministic PositionEpisode reconstruction for Journal v2.

The builder consumes a canonical, already de-duplicated view of immutable
broker order/fill observations.  It deliberately has no database dependency:
callers can inspect the complete build product before appending a versioned
``EpisodeBuild`` and its children.

Important evidence rules:

* detailed fills are the only source of fill-timestamp events;
* an ``aggregate_only`` order becomes an order-summary event at its order
  timestamp and never becomes a synthetic fill;
* order-level fees are allocated to detailed fills by quantity, with the final
  residual assigned explicitly so the Decimal total is conserved;
* a reversal is split between the closing and opening episodes, again with an
  explicit residual allocation;
* an unknown opening balance is never silently treated as flat.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


__all__ = [
    "BUILDER_NAME",
    "BUILDER_VERSION",
    "CanonicalFillEvidence",
    "CanonicalOrderEvidence",
    "EpisodeBuildError",
    "EvidenceAllocationRecord",
    "InstrumentIdentity",
    "OpeningPosition",
    "PositionEpisodeBuildResult",
    "PositionEpisodeRecord",
    "build_position_episodes",
]


BUILDER_NAME = "signed_position_episode_builder"
BUILDER_VERSION = "1.1.0"
_STORAGE_QUANTUM = Decimal("0.0000000001")
_SCORE_QUANTUM = Decimal("0.0001")
_BROKER_AMOUNT_TOLERANCE = Decimal("0.005")
_ZERO = Decimal("0")

_BUY_SIDES = {
    "BUY",
    "BUY_BACK",
    "BUY_TO_COVER",
    "BUY_TO_OPEN",
    "BUY_TO_CLOSE",
}
_SELL_SIDES = {
    "SELL",
    "SELL_SHORT",
    "SHORT_SELL",
    "SELL_TO_OPEN",
    "SELL_TO_CLOSE",
}
_MULTIPLIER_BASES = {
    "asset_definition",
    "broker_stated",
    "evidence_derived_from_amount",
    "unknown",
}


class EpisodeBuildError(ValueError):
    """Raised when canonical evidence cannot support an honest reconstruction."""


def _decimal_text(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    if value == 0:
        return "0"
    # SQLite Numeric columns restore a fixed scale while parser DTOs retain
    # source scale.  Instrument identity must not change merely because the
    # same strike is represented as 200, 200.0, or 200.0000000000.
    return format(value.normalize(), "f")


def _datetime_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise EpisodeBuildError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    def _default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"unsupported canonical value: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_default,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_STORAGE_QUANTUM, rounding=ROUND_HALF_EVEN)


def _weighted_average(numerator: Decimal, quantity: Decimal) -> Decimal:
    if quantity == 0:
        raise EpisodeBuildError("cannot average a zero quantity")
    return _quantize(numerator / quantity)


def _allocate_total(
    total: Optional[Decimal], quantities: Sequence[Decimal]
) -> tuple[Optional[Decimal], ...]:
    """Allocate a total and put the exact storage-scale residual last."""
    if not quantities:
        return ()
    if any(quantity <= 0 for quantity in quantities):
        raise EpisodeBuildError("allocation quantities must be positive")
    if total is None:
        return tuple(None for _ in quantities)
    total = _quantize(total)
    quantity_total = sum(quantities, _ZERO)
    allocated: list[Decimal] = []
    remaining = total
    for quantity in quantities[:-1]:
        value = _quantize(total * quantity / quantity_total)
        allocated.append(value)
        remaining -= value
    allocated.append(_quantize(remaining))
    if sum(allocated, _ZERO) != total:
        raise EpisodeBuildError("Decimal residual allocation did not conserve total")
    return tuple(allocated)


def _normalized_side(side: str) -> tuple[str, int]:
    normalized = side.strip().upper().replace(" ", "_")
    if normalized in _BUY_SIDES:
        return normalized, 1
    if normalized in _SELL_SIDES:
        return normalized, -1
    raise EpisodeBuildError(f"unsupported trade side: {side!r}")


@dataclass(frozen=True)
class InstrumentIdentity:
    """Canonical instrument/account identity used as the grouping key."""

    broker: str
    account_key: str
    raw_symbol: str
    asset_type: str
    underlying: str
    currency: str
    expiry: Optional[date] = None
    strike: Optional[Decimal] = None
    option_right: Optional[str] = None
    contract_multiplier: Optional[Decimal] = None
    contract_multiplier_basis: str = "unknown"

    def __post_init__(self) -> None:
        for field_name in (
            "broker",
            "account_key",
            "raw_symbol",
            "asset_type",
            "underlying",
            "currency",
        ):
            if not str(getattr(self, field_name)).strip():
                raise EpisodeBuildError(f"instrument {field_name} cannot be empty")
        if self.contract_multiplier is not None and self.contract_multiplier <= 0:
            raise EpisodeBuildError("contract_multiplier must be positive")
        multiplier_basis = self.contract_multiplier_basis.strip().lower()
        if multiplier_basis not in _MULTIPLIER_BASES:
            raise EpisodeBuildError("unsupported contract_multiplier_basis")
        if self.contract_multiplier is None and multiplier_basis != "unknown":
            raise EpisodeBuildError(
                "contract_multiplier_basis requires a multiplier value"
            )
        if self.contract_multiplier is not None and multiplier_basis == "unknown":
            raise EpisodeBuildError(
                "contract multiplier value requires explicit provenance"
            )
        if self.asset_type.lower() == "option":
            if self.expiry is None or self.strike is None or not self.option_right:
                raise EpisodeBuildError("option identity is missing expiry/strike/right")

    @property
    def canonical_key(self) -> tuple[str, ...]:
        return (
            self.broker.strip().lower(),
            self.account_key.strip(),
            self.raw_symbol.strip().upper(),
            self.asset_type.strip().lower(),
            self.underlying.strip().upper(),
            self.currency.strip().upper(),
            self.expiry.isoformat() if self.expiry else "",
            _decimal_text(self.strike) or "",
            (self.option_right or "").strip().upper(),
            _decimal_text(self.contract_multiplier) or "",
            self.contract_multiplier_basis.strip().lower(),
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "broker": self.canonical_key[0],
            "account_key": self.canonical_key[1],
            "raw_symbol": self.canonical_key[2],
            "asset_type": self.canonical_key[3],
            "underlying": self.canonical_key[4],
            "currency": self.canonical_key[5],
            "expiry": self.canonical_key[6] or None,
            "strike": self.canonical_key[7] or None,
            "option_right": self.canonical_key[8] or None,
            "contract_multiplier": self.canonical_key[9] or None,
            "contract_multiplier_basis": self.canonical_key[10],
        }


@dataclass(frozen=True)
class CanonicalOrderEvidence:
    """Canonical projection of one BrokerOrderObservation.

    Only ``aggregate_only`` orders create trade events.  ``fill_detail``
    orders supply authoritative order-level fees and linkage for their fills.
    Non-filled observations may be present and are ignored economically.
    """

    observation_id: int
    observation_key: str
    instrument: InstrumentIdentity
    side: str
    status: str
    ordered_at: datetime
    source_sequence: int
    evidence_level: str
    filled_quantity: Optional[Decimal]
    average_fill_price: Optional[Decimal]
    amount: Optional[Decimal]
    total_fee: Optional[Decimal]
    fee_evidence_status: str

    def __post_init__(self) -> None:
        if self.observation_id <= 0 or not self.observation_key:
            raise EpisodeBuildError("order observation identity is invalid")
        _datetime_utc(self.ordered_at, field_name="ordered_at")
        _normalized_side(self.side)
        if self.source_sequence < 0:
            raise EpisodeBuildError("source_sequence cannot be negative")
        if self.filled_quantity is not None and self.filled_quantity < 0:
            raise EpisodeBuildError("filled_quantity cannot be negative")
        if self.average_fill_price is not None and self.average_fill_price < 0:
            raise EpisodeBuildError("average_fill_price cannot be negative")
        if self.total_fee is not None and self.total_fee < 0:
            raise EpisodeBuildError("total_fee cannot be negative")


@dataclass(frozen=True)
class CanonicalFillEvidence:
    """Canonical projection of one BrokerFillObservation."""

    observation_id: int
    observation_key: str
    instrument: InstrumentIdentity
    side: str
    filled_at: datetime
    source_sequence: int
    quantity: Decimal
    price: Decimal
    amount: Optional[Decimal]
    total_fee: Optional[Decimal]
    broker_order_observation_id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.observation_id <= 0 or not self.observation_key:
            raise EpisodeBuildError("fill observation identity is invalid")
        _datetime_utc(self.filled_at, field_name="filled_at")
        _normalized_side(self.side)
        if self.source_sequence < 0:
            raise EpisodeBuildError("source_sequence cannot be negative")
        if self.quantity <= 0:
            raise EpisodeBuildError("fill quantity must be positive")
        if self.price < 0:
            raise EpisodeBuildError("fill price cannot be negative")
        if self.total_fee is not None and self.total_fee < 0:
            raise EpisodeBuildError("total_fee cannot be negative")


@dataclass(frozen=True)
class OpeningPosition:
    """A non-zero position observed at the left edge of the source window."""

    instrument: InstrumentIdentity
    signed_quantity: Decimal
    observed_at: datetime
    average_price: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if self.signed_quantity == 0:
            raise EpisodeBuildError("opening position must be non-zero")
        _datetime_utc(self.observed_at, field_name="opening observed_at")
        if self.average_price is not None and self.average_price < 0:
            raise EpisodeBuildError("opening average_price cannot be negative")


@dataclass(frozen=True)
class EvidenceAllocationRecord:
    """One persistable order/fill allocation for a built position episode."""

    episode_key: str
    evidence_key: str
    evidence_kind: str
    event_role: str
    allocation_sequence: int
    evidence_time: datetime
    allocated_quantity: Decimal
    allocated_fee: Optional[Decimal]
    allocated_cash_flow: Optional[Decimal]
    allocation_ratio: Decimal
    broker_order_observation_id: Optional[int]
    broker_fill_observation_id: Optional[int]
    timing_precision: str
    fee_allocation_method: str
    cash_flow_method: str

    def as_model_kwargs(
        self,
        *,
        episode_build_id: int,
        position_episode_id: int,
    ) -> dict[str, Any]:
        """Return columns accepted by PositionEpisodeEvidence."""
        return {
            "episode_build_id": episode_build_id,
            "position_episode_id": position_episode_id,
            "broker_order_observation_id": self.broker_order_observation_id,
            "broker_fill_observation_id": self.broker_fill_observation_id,
            "evidence_key": self.evidence_key,
            "evidence_kind": self.evidence_kind,
            "event_role": self.event_role,
            "allocation_sequence": self.allocation_sequence,
            "evidence_time": self.evidence_time,
            "allocated_quantity": self.allocated_quantity,
            "allocated_fee": self.allocated_fee,
            "allocated_cash_flow": self.allocated_cash_flow,
            "allocation_ratio": self.allocation_ratio,
            "allocation_evidence_json": _canonical_json(
                {
                    "timing_precision": self.timing_precision,
                    "fee_allocation_method": self.fee_allocation_method,
                    "cash_flow_method": self.cash_flow_method,
                    "contract_multiplier_basis": (
                        "not_applicable"
                        if self.cash_flow_method == "broker_observed_amount"
                        else "see_position_episode"
                    ),
                }
            ),
            "provenance_json": _canonical_json(
                {
                    "builder_name": BUILDER_NAME,
                    "builder_version": BUILDER_VERSION,
                    "episode_key": self.episode_key,
                }
            ),
        }


@dataclass(frozen=True)
class PositionEpisodeRecord:
    """A deterministic PositionEpisode build product, ready for persistence."""

    episode_key: str
    lineage_key: str
    instrument: InstrumentIdentity
    direction: str
    lifecycle_status: str
    opened_at: datetime
    closed_at: Optional[datetime]
    hold_seconds: Optional[int]
    opened_quantity: Decimal
    max_absolute_quantity: Decimal
    closed_quantity: Decimal
    remaining_quantity: Decimal
    average_entry_price: Optional[Decimal]
    average_exit_price: Optional[Decimal]
    opening_cash_flow: Optional[Decimal]
    closing_cash_flow: Optional[Decimal]
    realized_pnl_gross: Optional[Decimal]
    total_fee: Optional[Decimal]
    realized_pnl_net: Optional[Decimal]
    dte_at_entry: Optional[int]
    close_reason: Optional[str]
    construction_basis: str
    left_boundary_verified: bool
    opening_boundary_policy: str
    is_left_censored: bool
    is_right_censored: bool
    has_exact_fill_times: bool
    has_exact_fill_prices: bool
    has_complete_fees: bool
    completeness_score: Decimal
    completeness_status: str
    evidence: tuple[EvidenceAllocationRecord, ...]

    def as_model_kwargs(
        self,
        *,
        episode_build_id: int,
        strategy_episode_id: int,
    ) -> dict[str, Any]:
        """Return columns accepted by the PositionEpisode ORM model."""
        evidence_summary = {
            "allocation_count": len(self.evidence),
            "order_allocations": sum(
                item.evidence_kind == "order" for item in self.evidence
            ),
            "fill_allocations": sum(
                item.evidence_kind == "fill" for item in self.evidence
            ),
        }
        completeness = {
            "lifecycle_complete": not self.is_right_censored,
            "left_boundary_complete": (
                self.left_boundary_verified and not self.is_left_censored
            ),
            "left_boundary_verified": self.left_boundary_verified,
            "fill_times_exact": self.has_exact_fill_times,
            "fill_prices_exact": self.has_exact_fill_prices,
            "fees_complete": self.has_complete_fees,
            "cash_flows_complete": self.realized_pnl_gross is not None,
        }
        return {
            "episode_build_id": episode_build_id,
            "strategy_episode_id": strategy_episode_id,
            "episode_key": self.episode_key,
            "lineage_key": self.lineage_key,
            "broker": self.instrument.broker,
            "account_key": self.instrument.account_key,
            "raw_symbol": self.instrument.raw_symbol,
            "asset_type": self.instrument.asset_type,
            "underlying": self.instrument.underlying,
            "expiry": self.instrument.expiry,
            "strike": self.instrument.strike,
            "option_right": self.instrument.option_right,
            "contract_multiplier": self.instrument.contract_multiplier,
            "direction": self.direction,
            "lifecycle_status": self.lifecycle_status,
            "currency": self.instrument.currency,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "hold_seconds": self.hold_seconds,
            "opened_quantity": self.opened_quantity,
            "max_absolute_quantity": self.max_absolute_quantity,
            "closed_quantity": self.closed_quantity,
            "remaining_quantity": self.remaining_quantity,
            "average_entry_price": self.average_entry_price,
            "average_exit_price": self.average_exit_price,
            "opening_cash_flow": self.opening_cash_flow,
            "closing_cash_flow": self.closing_cash_flow,
            "realized_pnl_gross": self.realized_pnl_gross,
            "total_fee": self.total_fee,
            "realized_pnl_net": self.realized_pnl_net,
            "dte_at_entry": self.dte_at_entry,
            "close_reason": self.close_reason,
            "construction_basis": self.construction_basis,
            "is_left_censored": self.is_left_censored,
            "is_right_censored": self.is_right_censored,
            "has_exact_fill_times": self.has_exact_fill_times,
            "has_exact_fill_prices": self.has_exact_fill_prices,
            "has_complete_fees": self.has_complete_fees,
            "matching_evidence_json": _canonical_json(
                {
                    "method": "signed_position_zero_crossing",
                    "version": BUILDER_VERSION,
                    "reversal_policy": "close_then_open",
                    "cost_basis": "episode_weighted_average",
                    "left_boundary_verified": self.left_boundary_verified,
                    "opening_boundary_policy": self.opening_boundary_policy,
                    "contract_multiplier_basis": (
                        self.instrument.contract_multiplier_basis
                    ),
                }
            ),
            "evidence_summary_json": _canonical_json(evidence_summary),
            "completeness_score": self.completeness_score,
            "completeness_status": self.completeness_status,
            "completeness_json": _canonical_json(completeness),
            "provenance_json": _canonical_json(
                {
                    "builder_name": BUILDER_NAME,
                    "builder_version": BUILDER_VERSION,
                    "left_boundary_verified": self.left_boundary_verified,
                    "opening_boundary_policy": self.opening_boundary_policy,
                    "contract_multiplier_basis": (
                        self.instrument.contract_multiplier_basis
                    ),
                }
            ),
        }


@dataclass(frozen=True)
class PositionEpisodeBuildResult:
    """Pure builder output plus cross-episode conservation evidence."""

    episodes: tuple[PositionEpisodeRecord, ...]
    source_event_count: int
    aggregate_order_event_count: int
    detailed_fill_event_count: int
    source_known_fee_total: Decimal
    allocated_known_fee_total: Decimal
    opening_snapshot_complete: bool
    opening_boundary_policy: str
    source_window_start: datetime
    source_cutoff_at: datetime

    @property
    def evidence_allocations(self) -> tuple[EvidenceAllocationRecord, ...]:
        return tuple(
            allocation
            for episode in self.episodes
            for allocation in episode.evidence
        )


@dataclass(frozen=True)
class _TradeEvent:
    instrument: InstrumentIdentity
    evidence_kind: str
    observation_id: int
    evidence_key: str
    side: str
    occurred_at: datetime
    source_sequence: int
    quantity: Decimal
    price: Decimal
    amount: Optional[Decimal]
    fee: Optional[Decimal]
    timing_precision: str
    price_precision: str
    fee_allocation_method: str

    @property
    def signed_quantity(self) -> Decimal:
        _, sign = _normalized_side(self.side)
        return self.quantity * sign


@dataclass
class _EpisodeAccumulator:
    instrument: InstrumentIdentity
    ordinal: int
    direction: str
    opened_at: datetime
    opening_anchor: str
    left_boundary_verified: bool
    opening_boundary_policy: str
    is_left_censored: bool
    current_signed_quantity: Decimal
    opened_quantity: Decimal
    max_absolute_quantity: Decimal
    closed_quantity: Decimal = _ZERO
    entry_price_numerator: Decimal = _ZERO
    entry_price_complete: bool = True
    exit_price_numerator: Decimal = _ZERO
    exit_price_complete: bool = True
    opening_cash_flow_value: Decimal = _ZERO
    opening_cash_flow_complete: bool = True
    closing_cash_flow_value: Decimal = _ZERO
    closing_cash_flow_complete: bool = True
    fee_value: Decimal = _ZERO
    fee_complete: bool = True
    exact_fill_times: bool = True
    exact_fill_prices: bool = True
    basis_kinds: set[str] = field(default_factory=set)
    evidence: list[EvidenceAllocationRecord] = field(default_factory=list)


def _event_notional(event: _TradeEvent) -> Optional[Decimal]:
    if event.amount is not None:
        observed = _quantize(abs(event.amount))
        multiplier = event.instrument.contract_multiplier
        if multiplier is not None:
            expected = _quantize(event.quantity * event.price * multiplier)
            if abs(observed - expected) > _BROKER_AMOUNT_TOLERANCE:
                raise EpisodeBuildError(
                    "observed amount is inconsistent with quantity, price, "
                    "and contract multiplier"
                )
        return observed
    multiplier = event.instrument.contract_multiplier
    if multiplier is not None:
        return _quantize(event.quantity * event.price * multiplier)
    if event.instrument.asset_type.strip().lower() == "equity":
        return _quantize(event.quantity * event.price)
    return None


def _cash_flow_method(event: _TradeEvent) -> str:
    if event.amount is not None:
        return "broker_observed_amount"
    if event.instrument.contract_multiplier is not None:
        return "quantity_price_multiplier"
    if event.instrument.asset_type.strip().lower() == "equity":
        return "quantity_price_asset_unit"
    return "missing"


def _make_events(
    orders: Sequence[CanonicalOrderEvidence],
    fills: Sequence[CanonicalFillEvidence],
) -> tuple[_TradeEvent, ...]:
    order_by_id: dict[int, CanonicalOrderEvidence] = {}
    order_keys: set[str] = set()
    for order in orders:
        if order.observation_id in order_by_id or order.observation_key in order_keys:
            raise EpisodeBuildError("canonical order observations are not unique")
        order_by_id[order.observation_id] = order
        order_keys.add(order.observation_key)

    fill_ids: set[int] = set()
    fill_keys: set[str] = set()
    fills_by_order: dict[int, list[CanonicalFillEvidence]] = {}
    for fill in fills:
        if fill.observation_id in fill_ids or fill.observation_key in fill_keys:
            raise EpisodeBuildError("canonical fill observations are not unique")
        fill_ids.add(fill.observation_id)
        fill_keys.add(fill.observation_key)
        if fill.broker_order_observation_id is not None:
            fills_by_order.setdefault(fill.broker_order_observation_id, []).append(fill)

    fee_by_fill_id: dict[int, Optional[Decimal]] = {}
    fee_method_by_fill_id: dict[int, str] = {}
    for order_id, linked_fills in fills_by_order.items():
        order = order_by_id.get(order_id)
        if order is None:
            for fill in linked_fills:
                fee_by_fill_id[fill.observation_id] = fill.total_fee
                fee_method_by_fill_id[fill.observation_id] = (
                    "fill_direct" if fill.total_fee is not None else "missing"
                )
            continue
        if order.instrument != linked_fills[0].instrument:
            raise EpisodeBuildError("linked order/fill instrument mismatch")
        for fill in linked_fills:
            if fill.instrument != order.instrument:
                raise EpisodeBuildError("linked order/fill instrument mismatch")
            if _normalized_side(fill.side)[1] != _normalized_side(order.side)[1]:
                raise EpisodeBuildError("linked order/fill side mismatch")
        normalized_level = order.evidence_level.strip().lower()
        if normalized_level == "aggregate_only":
            raise EpisodeBuildError(
                "aggregate-only order cannot have canonical fill observations"
            )
        if normalized_level != "fill_detail":
            raise EpisodeBuildError(
                "fill observation is linked to a non-detail order"
            )
        linked_fills.sort(
            key=lambda item: (
                _datetime_utc(item.filled_at, field_name="filled_at"),
                item.source_sequence,
                item.observation_key,
            )
        )
        if (
            order.filled_quantity is not None
            and sum((fill.quantity for fill in linked_fills), _ZERO)
            != order.filled_quantity
        ):
            raise EpisodeBuildError("linked fill quantity does not match order summary")
        direct_fees = [fill.total_fee for fill in linked_fills]
        if order.total_fee is not None:
            if any(value is not None for value in direct_fees):
                if any(value is None for value in direct_fees):
                    raise EpisodeBuildError("fill fees are only partially populated")
                if sum((value for value in direct_fees if value is not None), _ZERO) \
                        != order.total_fee:
                    raise EpisodeBuildError("fill fees disagree with order total fee")
                allocations = tuple(direct_fees)
                method = "fill_direct_reconciled_to_order"
            else:
                allocations = _allocate_total(
                    order.total_fee,
                    [fill.quantity for fill in linked_fills],
                )
                method = "order_quantity_pro_rata_residual"
        else:
            allocations = tuple(direct_fees)
            method = "fill_direct" if all(
                value is not None for value in direct_fees
            ) else "missing"
        for fill, allocation in zip(linked_fills, allocations):
            fee_by_fill_id[fill.observation_id] = allocation
            fee_method_by_fill_id[fill.observation_id] = method

    events: list[_TradeEvent] = []
    for order in orders:
        normalized_level = order.evidence_level.strip().lower()
        linked = fills_by_order.get(order.observation_id, [])
        if normalized_level == "fill_detail":
            if (
                (order.filled_quantity or _ZERO) > 0
                and not linked
            ):
                raise EpisodeBuildError(
                    "detail-backed filled order has no canonical fills"
                )
            continue
        if normalized_level != "aggregate_only":
            continue
        if not order.status.strip().upper().startswith("FILLED"):
            raise EpisodeBuildError(
                "aggregate-only execution evidence must have filled status"
            )
        if linked:
            raise EpisodeBuildError(
                "aggregate-only order cannot have canonical fill observations"
            )
        if (
            order.filled_quantity is None
            or order.filled_quantity <= 0
            or order.average_fill_price is None
        ):
            raise EpisodeBuildError(
                "aggregate-only order lacks quantity or average-price evidence"
            )
        events.append(
            _TradeEvent(
                instrument=order.instrument,
                evidence_kind="order",
                observation_id=order.observation_id,
                evidence_key=order.observation_key,
                side=order.side,
                occurred_at=_datetime_utc(
                    order.ordered_at,
                    field_name="ordered_at",
                ),
                source_sequence=order.source_sequence,
                quantity=order.filled_quantity,
                price=order.average_fill_price,
                amount=order.amount,
                fee=order.total_fee,
                timing_precision="order_time_proxy",
                price_precision="aggregate_average",
                fee_allocation_method=(
                    "order_total" if order.total_fee is not None else "missing"
                ),
            )
        )

    for fill in fills:
        if fill.broker_order_observation_id is None:
            fee = fill.total_fee
            fee_method = "fill_direct" if fee is not None else "missing"
        else:
            fee = fee_by_fill_id.get(fill.observation_id)
            fee_method = fee_method_by_fill_id.get(fill.observation_id, "missing")
        events.append(
            _TradeEvent(
                instrument=fill.instrument,
                evidence_kind="fill",
                observation_id=fill.observation_id,
                evidence_key=fill.observation_key,
                side=fill.side,
                occurred_at=_datetime_utc(
                    fill.filled_at,
                    field_name="filled_at",
                ),
                source_sequence=fill.source_sequence,
                quantity=fill.quantity,
                price=fill.price,
                amount=fill.amount,
                fee=fee,
                timing_precision="fill_time",
                price_precision="fill_price",
                fee_allocation_method=fee_method,
            )
        )

    return tuple(
        sorted(
            events,
            key=lambda item: (
                item.instrument.canonical_key,
                item.occurred_at,
                item.source_sequence,
                item.evidence_kind,
                item.evidence_key,
            ),
        )
    )


def _start_episode(
    instrument: InstrumentIdentity,
    *,
    ordinal: int,
    signed_quantity: Decimal,
    opened_at: datetime,
    opening_anchor: str,
    left_boundary_verified: bool,
    opening_boundary_policy: str,
    left_censored: bool,
    average_price: Optional[Decimal] = None,
    direction: Optional[str] = None,
) -> _EpisodeAccumulator:
    absolute_quantity = abs(signed_quantity)
    resolved_direction = direction
    if signed_quantity != 0:
        resolved_direction = "long" if signed_quantity > 0 else "short"
    if resolved_direction not in {"long", "short"}:
        raise EpisodeBuildError("zero-balance episode requires a direction")
    accumulator = _EpisodeAccumulator(
        instrument=instrument,
        ordinal=ordinal,
        direction=resolved_direction,
        opened_at=opened_at,
        opening_anchor=opening_anchor,
        left_boundary_verified=left_boundary_verified,
        opening_boundary_policy=opening_boundary_policy,
        is_left_censored=left_censored,
        current_signed_quantity=signed_quantity,
        opened_quantity=absolute_quantity,
        max_absolute_quantity=absolute_quantity,
    )
    if left_censored:
        accumulator.basis_kinds.add("opening_balance")
        accumulator.exact_fill_times = False
        accumulator.exact_fill_prices = False
        accumulator.opening_cash_flow_complete = False
        accumulator.fee_complete = False
        if average_price is None:
            accumulator.entry_price_complete = False
        else:
            accumulator.entry_price_numerator = average_price * absolute_quantity
    return accumulator


def _construction_basis(
    kinds: set[str],
    *,
    left_boundary_verified: bool,
) -> str:
    if not left_boundary_verified:
        return "trade_flow_assumed_flat"
    if kinds == {"fill"}:
        return "fills"
    if kinds == {"order"}:
        return "aggregate_orders"
    if kinds == {"opening_balance"}:
        return "opening_balance"
    if "opening_balance" in kinds:
        return "opening_balance_mixed"
    return "mixed_evidence"


def _episode_keys(accumulator: _EpisodeAccumulator) -> tuple[str, str]:
    identity = accumulator.instrument.canonical_payload()
    lineage_key = _hash(
        {
            "identity": identity,
            "direction": accumulator.direction,
            "opening_anchor": accumulator.opening_anchor,
        }
    )
    episode_key = _hash(
        {
            "builder": BUILDER_VERSION,
            "identity": identity,
            "direction": accumulator.direction,
            "ordinal": accumulator.ordinal,
            "opening_anchor": accumulator.opening_anchor,
        }
    )
    return episode_key, lineage_key


def _finalize_episode(
    accumulator: _EpisodeAccumulator,
    *,
    closed_at: Optional[datetime],
    close_reason: Optional[str],
    exchange_timezone: ZoneInfo,
) -> PositionEpisodeRecord:
    right_censored = closed_at is None
    episode_key, lineage_key = _episode_keys(accumulator)
    average_entry = (
        _weighted_average(
            accumulator.entry_price_numerator,
            accumulator.opened_quantity,
        )
        if accumulator.entry_price_complete and accumulator.opened_quantity > 0
        else None
    )
    average_exit = (
        _weighted_average(
            accumulator.exit_price_numerator,
            accumulator.closed_quantity,
        )
        if accumulator.exit_price_complete and accumulator.closed_quantity > 0
        else None
    )
    opening_cash_flow = (
        _quantize(accumulator.opening_cash_flow_value)
        if accumulator.opening_cash_flow_complete
        else None
    )
    closing_cash_flow = (
        _quantize(accumulator.closing_cash_flow_value)
        if accumulator.closing_cash_flow_complete
        else None
    )
    total_fee = (
        _quantize(accumulator.fee_value)
        if accumulator.fee_complete
        else None
    )
    fully_closed = closed_at is not None and accumulator.current_signed_quantity == 0
    gross = (
        _quantize(opening_cash_flow + closing_cash_flow)
        if fully_closed
        and opening_cash_flow is not None
        and closing_cash_flow is not None
        else None
    )
    net = (
        _quantize(gross - total_fee)
        if gross is not None and total_fee is not None
        else None
    )
    exact_times = accumulator.exact_fill_times and not accumulator.is_left_censored
    exact_prices = accumulator.exact_fill_prices and not accumulator.is_left_censored
    dimensions = (
        (
            accumulator.left_boundary_verified
            and not accumulator.is_left_censored
        ),
        not right_censored,
        exact_times,
        exact_prices,
        accumulator.fee_complete,
        gross is not None,
    )
    completeness_score = (
        Decimal(sum(dimensions)) / Decimal(len(dimensions))
    ).quantize(_SCORE_QUANTUM)
    dte = None
    if accumulator.instrument.expiry is not None:
        opened_date = accumulator.opened_at.astimezone(exchange_timezone).date()
        dte = (accumulator.instrument.expiry - opened_date).days
    hold_seconds = None
    if closed_at is not None:
        hold_seconds = int((closed_at - accumulator.opened_at).total_seconds())
    episode = PositionEpisodeRecord(
        episode_key=episode_key,
        lineage_key=lineage_key,
        instrument=accumulator.instrument,
        direction=accumulator.direction,
        lifecycle_status="open" if right_censored else "closed",
        opened_at=accumulator.opened_at,
        closed_at=closed_at,
        hold_seconds=hold_seconds,
        opened_quantity=_quantize(accumulator.opened_quantity),
        max_absolute_quantity=_quantize(accumulator.max_absolute_quantity),
        closed_quantity=_quantize(accumulator.closed_quantity),
        remaining_quantity=_quantize(abs(accumulator.current_signed_quantity)),
        average_entry_price=average_entry,
        average_exit_price=average_exit,
        opening_cash_flow=opening_cash_flow,
        closing_cash_flow=closing_cash_flow,
        realized_pnl_gross=gross,
        total_fee=total_fee,
        realized_pnl_net=net,
        dte_at_entry=dte,
        close_reason=close_reason,
        construction_basis=_construction_basis(
            accumulator.basis_kinds,
            left_boundary_verified=accumulator.left_boundary_verified,
        ),
        left_boundary_verified=accumulator.left_boundary_verified,
        opening_boundary_policy=accumulator.opening_boundary_policy,
        is_left_censored=accumulator.is_left_censored,
        is_right_censored=right_censored,
        has_exact_fill_times=exact_times,
        has_exact_fill_prices=exact_prices,
        has_complete_fees=accumulator.fee_complete,
        completeness_score=completeness_score,
        completeness_status=(
            "exact" if all(dimensions) else "partial"
        ),
        evidence=(),
    )
    evidence = tuple(
        EvidenceAllocationRecord(
            episode_key=episode.episode_key,
            evidence_key=item.evidence_key,
            evidence_kind=item.evidence_kind,
            event_role=item.event_role,
            allocation_sequence=item.allocation_sequence,
            evidence_time=item.evidence_time,
            allocated_quantity=item.allocated_quantity,
            allocated_fee=item.allocated_fee,
            allocated_cash_flow=item.allocated_cash_flow,
            allocation_ratio=item.allocation_ratio,
            broker_order_observation_id=item.broker_order_observation_id,
            broker_fill_observation_id=item.broker_fill_observation_id,
            timing_precision=item.timing_precision,
            fee_allocation_method=item.fee_allocation_method,
            cash_flow_method=item.cash_flow_method,
        )
        for item in accumulator.evidence
    )
    return replace(episode, evidence=evidence)


def _append_allocation(
    accumulator: _EpisodeAccumulator,
    event: _TradeEvent,
    *,
    signed_quantity: Decimal,
    role: str,
    allocated_fee: Optional[Decimal],
    allocated_notional: Optional[Decimal],
    allocation_ratio: Decimal,
) -> None:
    absolute_quantity = abs(signed_quantity)
    is_entry = role in {"open", "add"}
    cash_flow = None
    if allocated_notional is not None:
        cash_flow = _quantize(
            allocated_notional * (-1 if signed_quantity > 0 else 1)
        )
    if is_entry:
        accumulator.opened_quantity += absolute_quantity
        accumulator.entry_price_numerator += event.price * absolute_quantity
        if cash_flow is None:
            accumulator.opening_cash_flow_complete = False
        else:
            accumulator.opening_cash_flow_value += cash_flow
    else:
        accumulator.closed_quantity += absolute_quantity
        accumulator.exit_price_numerator += event.price * absolute_quantity
        if cash_flow is None:
            accumulator.closing_cash_flow_complete = False
        else:
            accumulator.closing_cash_flow_value += cash_flow
    if allocated_fee is None:
        accumulator.fee_complete = False
    else:
        accumulator.fee_value += allocated_fee
    if event.timing_precision != "fill_time":
        accumulator.exact_fill_times = False
    if event.price_precision != "fill_price":
        accumulator.exact_fill_prices = False
    accumulator.basis_kinds.add(event.evidence_kind)
    accumulator.current_signed_quantity += signed_quantity
    accumulator.max_absolute_quantity = max(
        accumulator.max_absolute_quantity,
        abs(accumulator.current_signed_quantity),
    )
    accumulator.evidence.append(
        EvidenceAllocationRecord(
            episode_key="pending",
            evidence_key=event.evidence_key,
            evidence_kind=event.evidence_kind,
            event_role=role,
            allocation_sequence=len(accumulator.evidence),
            evidence_time=event.occurred_at,
            allocated_quantity=_quantize(absolute_quantity),
            allocated_fee=allocated_fee,
            allocated_cash_flow=cash_flow,
            allocation_ratio=allocation_ratio,
            broker_order_observation_id=(
                event.observation_id if event.evidence_kind == "order" else None
            ),
            broker_fill_observation_id=(
                event.observation_id if event.evidence_kind == "fill" else None
            ),
            timing_precision=event.timing_precision,
            fee_allocation_method=event.fee_allocation_method,
            cash_flow_method=_cash_flow_method(event),
        )
    )


def build_position_episodes(
    orders: Iterable[CanonicalOrderEvidence],
    fills: Iterable[CanonicalFillEvidence],
    *,
    source_window_start: datetime,
    source_cutoff_at: datetime,
    opening_positions: Iterable[OpeningPosition] = (),
    opening_snapshot_complete: bool,
    assume_flat_if_missing: bool = False,
    exchange_timezone: str = "America/New_York",
) -> PositionEpisodeBuildResult:
    """Build ``0 -> non-zero -> 0`` lifecycles from canonical evidence.

    ``opening_snapshot_complete`` is intentionally mandatory.  If false, every
    instrument with events must have an explicit ``OpeningPosition`` unless
    the caller deliberately enables ``assume_flat_if_missing``.  Assumed-flat
    episodes remain partial and carry ``left_boundary_verified=False``; the
    builder never upgrades that policy to an observed opening snapshot.
    """
    window_start = _datetime_utc(
        source_window_start,
        field_name="source_window_start",
    )
    cutoff_at = _datetime_utc(
        source_cutoff_at,
        field_name="source_cutoff_at",
    )
    if cutoff_at < window_start:
        raise EpisodeBuildError("source_cutoff_at precedes source_window_start")
    try:
        exchange_tz = ZoneInfo(exchange_timezone)
    except ZoneInfoNotFoundError as exc:
        raise EpisodeBuildError(
            f"unknown exchange timezone: {exchange_timezone}"
        ) from exc

    canonical_orders = tuple(orders)
    canonical_fills = tuple(fills)
    events = _make_events(canonical_orders, canonical_fills)
    opening_by_instrument: dict[tuple[str, ...], OpeningPosition] = {}
    instruments: dict[tuple[str, ...], InstrumentIdentity] = {}
    for opening in opening_positions:
        observed_at = _datetime_utc(
            opening.observed_at,
            field_name="opening observed_at",
        )
        if observed_at != window_start:
            raise EpisodeBuildError(
                "opening position must be observed at source_window_start"
            )
        key = opening.instrument.canonical_key
        if key in opening_by_instrument:
            raise EpisodeBuildError("duplicate opening position instrument")
        opening_by_instrument[key] = opening
        instruments[key] = opening.instrument
    for event in events:
        if event.occurred_at < window_start or event.occurred_at > cutoff_at:
            raise EpisodeBuildError("trade event is outside the source window")
        key = event.instrument.canonical_key
        existing = instruments.get(key)
        if existing is not None and existing != event.instrument:
            raise EpisodeBuildError("conflicting canonical instrument metadata")
        instruments[key] = event.instrument
    missing_keys = {
        event.instrument.canonical_key
        for event in events
        if event.instrument.canonical_key not in opening_by_instrument
    }
    if not opening_snapshot_complete and missing_keys and not assume_flat_if_missing:
        missing = sorted(
            instruments[key].raw_symbol for key in missing_keys
        )
        if missing:
            raise EpisodeBuildError(
                "opening snapshot is incomplete for: " + ", ".join(missing)
            )

    if opening_snapshot_complete:
        opening_boundary_policy = "complete_snapshot"
    elif missing_keys:
        opening_boundary_policy = (
            "mixed_explicit_and_assumed"
            if opening_by_instrument
            else "assumed_flat_unverified"
        )
    else:
        opening_boundary_policy = "explicit_opening_positions"
    boundary_verified_by_instrument = {
        key: opening_snapshot_complete or key in opening_by_instrument
        for key in instruments
    }

    active: dict[tuple[str, ...], _EpisodeAccumulator] = {}
    ordinals: dict[tuple[str, ...], int] = {}
    completed: list[PositionEpisodeRecord] = []
    for key, opening in sorted(opening_by_instrument.items()):
        ordinals[key] = 1
        active[key] = _start_episode(
            opening.instrument,
            ordinal=1,
            signed_quantity=opening.signed_quantity,
            opened_at=window_start,
            opening_anchor=f"left_boundary:{window_start.isoformat()}",
            left_boundary_verified=True,
            opening_boundary_policy="explicit_opening_position",
            left_censored=True,
            average_price=opening.average_price,
        )

    for event in events:
        key = event.instrument.canonical_key
        delta = event.signed_quantity
        accumulator = active.get(key)
        current = accumulator.current_signed_quantity if accumulator else _ZERO
        reversal = current != 0 and current * delta < 0 and abs(delta) > abs(current)
        if reversal:
            part_quantities = (abs(current), abs(delta) - abs(current))
        else:
            part_quantities = (abs(delta),)
        fee_parts = _allocate_total(event.fee, part_quantities)
        notional_parts = _allocate_total(_event_notional(event), part_quantities)
        ratio_parts = _allocate_total(Decimal("1"), part_quantities)

        for part_index, absolute_quantity in enumerate(part_quantities):
            signed_part = absolute_quantity * (Decimal("1") if delta > 0 else -1)
            accumulator = active.get(key)
            current = accumulator.current_signed_quantity if accumulator else _ZERO
            if current == 0:
                ordinal = ordinals.get(key, 0) + 1
                ordinals[key] = ordinal
                accumulator = _start_episode(
                    event.instrument,
                    ordinal=ordinal,
                    signed_quantity=_ZERO,
                    opened_at=event.occurred_at,
                    opening_anchor=f"evidence:{event.evidence_key}",
                    left_boundary_verified=(
                        boundary_verified_by_instrument.get(key, False)
                    ),
                    opening_boundary_policy=(
                        "verified_trade_flow"
                        if boundary_verified_by_instrument.get(key, False)
                        else "assumed_flat_unverified"
                    ),
                    left_censored=False,
                    direction="long" if signed_part > 0 else "short",
                )
                active[key] = accumulator
                role = "open"
            elif current * signed_part > 0:
                role = "add"
            elif abs(signed_part) == abs(current):
                role = "close"
            else:
                role = "reduce"

            _append_allocation(
                accumulator,
                event,
                signed_quantity=signed_part,
                role=role,
                allocated_fee=fee_parts[part_index],
                allocated_notional=notional_parts[part_index],
                allocation_ratio=ratio_parts[part_index] or _ZERO,
            )
            if accumulator.current_signed_quantity == 0:
                completed.append(
                    _finalize_episode(
                        accumulator,
                        closed_at=event.occurred_at,
                        close_reason=(
                            "reversed"
                            if reversal and part_index == 0
                            else "position_flattened"
                        ),
                        exchange_timezone=exchange_tz,
                    )
                )
                del active[key]

    for key in sorted(active):
        completed.append(
            _finalize_episode(
                active[key],
                closed_at=None,
                close_reason=None,
                exchange_timezone=exchange_tz,
            )
        )
    completed.sort(
        key=lambda episode: (
            episode.opened_at,
            episode.instrument.canonical_key,
            episode.episode_key,
        )
    )
    known_fee_total = sum(
        (event.fee for event in events if event.fee is not None),
        _ZERO,
    )
    allocated_fee_total = sum(
        (
            allocation.allocated_fee
            for episode in completed
            for allocation in episode.evidence
            if allocation.allocated_fee is not None
        ),
        _ZERO,
    )
    if _quantize(known_fee_total) != _quantize(allocated_fee_total):
        raise EpisodeBuildError("known fee allocations do not conserve source total")
    return PositionEpisodeBuildResult(
        episodes=tuple(completed),
        source_event_count=len(events),
        aggregate_order_event_count=sum(
            event.evidence_kind == "order" for event in events
        ),
        detailed_fill_event_count=sum(
            event.evidence_kind == "fill" for event in events
        ),
        source_known_fee_total=_quantize(known_fee_total),
        allocated_known_fee_total=_quantize(allocated_fee_total),
        opening_snapshot_complete=opening_snapshot_complete,
        opening_boundary_policy=opening_boundary_policy,
        source_window_start=window_start,
        source_cutoff_at=cutoff_at,
    )
