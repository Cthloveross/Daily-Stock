# -*- coding: utf-8 -*-
"""Zero-write PositionEpisode preview behind a verified snapshot fence.

The preview deliberately re-evaluates the caller's fence and builds from the
same frozen canonical projection inside one explicit SQLite read transaction.
It never initializes schemas, persists a plan, appends Episodes, or changes an
activation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping, Optional

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from src.journal.ledger.episode_repository import (
    SNAPSHOT_FENCE_SOURCE_KIND,
    VerifiedCanonicalEpisodeEvidenceProjection,
    VerifiedCanonicalExecutionGroup,
)
from src.journal.ledger.episodes import (
    BUILDER_NAME,
    BUILDER_VERSION,
    CanonicalFillEvidence,
    CanonicalOrderEvidence,
    EpisodeBuildError,
    InstrumentIdentity,
    OpeningPosition,
    PositionEpisodeRecord,
    build_position_episodes,
)
from src.journal.ledger.position_snapshot_continuity import (
    POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION,
    PositionSnapshotContinuityError,
    assess_position_snapshot_continuity_in_session,
    open_position_snapshot_continuity_session,
)
from src.journal.ledger.position_snapshot_repository import (
    PositionSnapshotMemberRecord,
)


__all__ = [
    "FUTURE_POSITION_EPISODE_MAX_CANONICAL_MEMBERS",
    "FUTURE_POSITION_EPISODE_PROJECTION_NAME",
    "FUTURE_POSITION_EPISODE_PROJECTION_VERSION",
    "FuturePositionEpisodeBuildCounts",
    "FuturePositionEpisodeBuildPreview",
    "FuturePositionEpisodePreviewError",
    "preview_fenced_position_episodes",
    "preview_fenced_position_episodes_in_session",
]


FUTURE_POSITION_EPISODE_PROJECTION_NAME = (
    "position_snapshot_fenced_canonical_projection"
)
FUTURE_POSITION_EPISODE_PROJECTION_VERSION = "1.0.0"
FUTURE_POSITION_EPISODE_MAX_CANONICAL_MEMBERS = 25_000
_SCOPE = "moomoo_live_us_options"
_IDENTITY_POLICY = "occ_semantic_identity_padded_symbol_v1"
_BOUNDARY_POLICY = "strictly_after_snapshot_operation_completed_at"
_COST_POLICY = "snapshot_cost_context_excluded_left_censored"
_GROUP_FEE_POLICY = "retained_at_group_scope_not_leg_allocated"
_STORAGE_QUANTUM = Decimal("0.0000000001")


class FuturePositionEpisodePreviewError(ValueError):
    """Raised when a fenced preview cannot be produced without assumptions."""


@dataclass(frozen=True)
class FuturePositionEpisodeBuildCounts:
    canonical_member_count: int
    opening_position_count: int
    opening_contract_count: Decimal
    post_boundary_order_event_count: int
    post_boundary_fill_event_count: int
    supporting_order_count: int
    excluded_non_option_event_count: int
    execution_group_count: int
    source_event_count: int
    planned_position_episode_count: int
    planned_open_episode_count: int
    planned_closed_episode_count: int
    left_censored_episode_count: int
    right_censored_episode_count: int
    headline_episode_count: int
    headline_excluded_episode_count: int
    group_fee_affected_episode_count: int


@dataclass(frozen=True)
class FuturePositionEpisodeBuildPreview:
    projection_name: str
    projection_version: str
    continuity_policy_version: str
    account_key: str
    scope: str
    fence_key: str
    snapshot_id: int
    snapshot_key: str
    target_publication_id: int
    target_publication_key: str
    target_canonical_set_id: int
    target_canonical_set_sha256: str
    canonical_source_batch_ids: tuple[int, ...]
    publication_source_batch_ids: tuple[int, ...]
    boundary_at: datetime
    source_cutoff_at: datetime
    builder_name: str
    builder_version: str
    builder_config_sha256: str
    evidence_set_sha256: str
    planned_build_key: str
    opening_boundary_policy: str
    max_multiplier_proof_residual: Decimal
    source_known_fee_total: Decimal
    accounted_known_fee_total: Decimal
    retained_execution_group_fee_total: Decimal
    fee_conservation_by_currency: Mapping[str, Mapping[str, Decimal]]
    fee_conserved: bool
    headline_realized_pnl_gross: Optional[Decimal]
    headline_total_fee: Optional[Decimal]
    headline_realized_pnl_net: Optional[Decimal]
    counts: FuturePositionEpisodeBuildCounts
    warnings: tuple[str, ...]
    episodes: tuple[PositionEpisodeRecord, ...]
    preview_only: bool = True
    default_will_change: bool = False
    evidence_written: bool = False
    trading_action_performed: bool = False


def _canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise TypeError("non-finite Decimal is not canonical")
            return "0" if item == 0 else format(item.normalize(), "f")
        if isinstance(item, datetime):
            if item.tzinfo is None:
                raise TypeError("naive datetime is not canonical")
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, tuple):
            return list(item)
        raise TypeError(
            f"unsupported future preview value: {type(item).__name__}"
        )

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _decimal(value: Any, *, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FuturePositionEpisodePreviewError(
            f"{field_name} is not a valid decimal"
        ) from exc
    if not parsed.is_finite():
        raise FuturePositionEpisodePreviewError(
            f"{field_name} must be finite"
        )
    return parsed


def _semantic_key(
    instrument: InstrumentIdentity,
) -> tuple[str, str, date, Decimal, str, str]:
    if (
        instrument.broker.strip().lower() != "moomoo"
        or instrument.asset_type.strip().lower() != "option"
        or instrument.expiry is None
        or instrument.strike is None
        or not instrument.option_right
    ):
        raise FuturePositionEpisodePreviewError(
            "future preview received an invalid option instrument"
        )
    strike = _decimal(
        instrument.strike,
        field_name="canonical option strike",
    )
    if strike <= 0:
        raise FuturePositionEpisodePreviewError(
            "canonical option strike must be positive"
        )
    return (
        instrument.account_key.strip(),
        instrument.underlying.strip().upper(),
        instrument.expiry,
        strike,
        instrument.option_right.strip().upper(),
        instrument.currency.strip().upper(),
    )


def _snapshot_semantic_key(
    member: PositionSnapshotMemberRecord,
    account_key: str,
) -> tuple[str, str, date, Decimal, str, str]:
    if member.asset_type.strip().lower() != "option":
        raise FuturePositionEpisodePreviewError(
            "snapshot contains an out-of-scope non-option member"
        )
    strike = _decimal(member.strike, field_name="snapshot option strike")
    if strike <= 0:
        raise FuturePositionEpisodePreviewError(
            "snapshot option strike must be positive"
        )
    return (
        account_key,
        member.underlying.strip().upper(),
        member.expiry,
        strike,
        member.option_right.strip().upper(),
        member.currency.strip().upper(),
    )


def _padded_occ_symbol(
    key: tuple[str, str, date, Decimal, str, str],
) -> str:
    _account_key, underlying, expiry, strike, right, _currency = key
    scaled = strike * Decimal("1000")
    integral = scaled.to_integral_value()
    if scaled != integral or integral <= 0 or integral > 99_999_999:
        raise FuturePositionEpisodePreviewError(
            "option strike cannot be represented by the OCC identity policy"
        )
    return (
        f"{underlying}{expiry.strftime('%y%m%d')}{right}"
        f"{int(integral):08d}"
    )


def _is_option(instrument: InstrumentIdentity) -> bool:
    return instrument.asset_type.strip().lower() == "option"


def _representative_instruments(
    *,
    account_key: str,
    members: tuple[PositionSnapshotMemberRecord, ...],
    orders: tuple[CanonicalOrderEvidence, ...],
    fills: tuple[CanonicalFillEvidence, ...],
) -> tuple[
    dict[tuple[str, str, date, Decimal, str, str], InstrumentIdentity],
    dict[tuple[str, str, date, Decimal, str, str], PositionSnapshotMemberRecord],
]:
    snapshot_by_key: dict[
        tuple[str, str, date, Decimal, str, str],
        PositionSnapshotMemberRecord,
    ] = {}
    for member in members:
        key = _snapshot_semantic_key(member, account_key)
        if key in snapshot_by_key:
            raise FuturePositionEpisodePreviewError(
                "snapshot contains duplicate semantic option identity"
            )
        snapshot_by_key[key] = member

    candidates: dict[
        tuple[str, str, date, Decimal, str, str],
        list[InstrumentIdentity],
    ] = {}
    for item in (*orders, *fills):
        key = _semantic_key(item.instrument)
        if key[0] != account_key:
            raise FuturePositionEpisodePreviewError(
                "canonical option belongs to another account"
            )
        candidates.setdefault(key, []).append(item.instrument)

    representatives: dict[
        tuple[str, str, date, Decimal, str, str],
        InstrumentIdentity,
    ] = {}
    for key in sorted({*snapshot_by_key, *candidates}):
        values = candidates.get(key, [])
        multipliers = {
            _decimal(
                value.contract_multiplier,
                field_name="canonical option contract multiplier",
            )
            for value in values
            if value.contract_multiplier is not None
        }
        member = snapshot_by_key.get(key)
        if member is not None:
            snapshot_multiplier = _decimal(
                member.contract_multiplier,
                field_name="snapshot option contract multiplier",
            )
            multipliers.add(snapshot_multiplier)
            basis = "broker_stated"
        else:
            snapshot_multiplier = None
            bases = {
                value.contract_multiplier_basis.strip().lower()
                for value in values
                if value.contract_multiplier is not None
            }
            basis = (
                "evidence_derived_from_amount"
                if "evidence_derived_from_amount" in bases
                else min(bases, default="unknown")
            )
        if len(multipliers) != 1:
            raise FuturePositionEpisodePreviewError(
                "semantic option identity has conflicting multipliers"
            )
        multiplier = next(iter(multipliers), None)
        if multiplier is None or multiplier <= 0 or basis == "unknown":
            raise FuturePositionEpisodePreviewError(
                "semantic option identity has no proved multiplier"
            )
        representatives[key] = InstrumentIdentity(
            broker="moomoo",
            account_key=account_key,
            raw_symbol=_padded_occ_symbol(key),
            asset_type="option",
            underlying=key[1],
            currency=key[5],
            expiry=key[2],
            strike=key[3],
            option_right=key[4],
            contract_multiplier=multiplier,
            contract_multiplier_basis=basis,
        )
    return representatives, snapshot_by_key


def _normalize_instruments(
    *,
    account_key: str,
    members: tuple[PositionSnapshotMemberRecord, ...],
    orders: tuple[CanonicalOrderEvidence, ...],
    fills: tuple[CanonicalFillEvidence, ...],
    boundary_at: datetime,
) -> tuple[
    tuple[OpeningPosition, ...],
    tuple[CanonicalOrderEvidence, ...],
    tuple[CanonicalFillEvidence, ...],
]:
    representatives, snapshot_by_key = _representative_instruments(
        account_key=account_key,
        members=members,
        orders=orders,
        fills=fills,
    )
    normalized_orders = tuple(
        replace(item, instrument=representatives[_semantic_key(item.instrument)])
        for item in orders
    )
    normalized_fills = tuple(
        replace(item, instrument=representatives[_semantic_key(item.instrument)])
        for item in fills
    )
    opening_positions = tuple(
        OpeningPosition(
            instrument=representatives[key],
            signed_quantity=_decimal(
                member.signed_quantity_contracts,
                field_name="snapshot signed option quantity",
            ),
            observed_at=boundary_at,
            # Broker cost fields are display context only.  Supplying any of
            # them here would fabricate pre-boundary cash flow semantics.
            average_price=None,
        )
        for key, member in sorted(snapshot_by_key.items())
    )
    return opening_positions, normalized_orders, normalized_fills


def _window_projection(
    projection: VerifiedCanonicalEpisodeEvidenceProjection,
    *,
    publication_source_batch_ids: tuple[int, ...],
    boundary_at: datetime,
    source_cutoff_at: datetime,
) -> tuple[
    tuple[CanonicalOrderEvidence, ...],
    tuple[CanonicalFillEvidence, ...],
    tuple[VerifiedCanonicalExecutionGroup, ...],
    int,
    int,
]:
    boundary_order_by_id = {
        item.selected_observation_id: item
        for item in projection.boundary.orders
    }
    boundary_fill_by_id = {
        item.selected_observation_id: item
        for item in projection.boundary.fills
    }
    builder_order_by_id = {item.observation_id: item for item in projection.orders}
    if set(boundary_order_by_id) != set(builder_order_by_id):
        raise FuturePositionEpisodePreviewError(
            "boundary and builder order projections disagree"
        )
    if {item.observation_id for item in projection.fills} != set(
        boundary_fill_by_id
    ):
        raise FuturePositionEpisodePreviewError(
            "boundary and builder fill projections disagree"
        )

    publication_batches = set(publication_source_batch_ids)
    post_fills: list[CanonicalFillEvidence] = []
    excluded_non_option = 0
    for fill in projection.fills:
        if fill.filled_at <= boundary_at:
            continue
        if fill.filled_at > source_cutoff_at:
            raise FuturePositionEpisodePreviewError(
                "post-boundary fill exceeds the target canonical cutoff"
            )
        boundary_fill = boundary_fill_by_id[fill.observation_id]
        if boundary_fill.import_batch_id not in publication_batches:
            raise FuturePositionEpisodePreviewError(
                "post-boundary fill is outside the publication batch chain"
            )
        if not _is_option(fill.instrument):
            excluded_non_option += 1
            continue
        post_fills.append(fill)

    post_orders: list[CanonicalOrderEvidence] = []
    for order in projection.orders:
        if order.evidence_level.strip().lower() != "aggregate_only":
            continue
        if order.filled_quantity is None or order.filled_quantity <= 0:
            continue
        if order.ordered_at <= boundary_at:
            continue
        if order.ordered_at > source_cutoff_at:
            raise FuturePositionEpisodePreviewError(
                "post-boundary aggregate order exceeds the canonical cutoff"
            )
        boundary_order = boundary_order_by_id[order.observation_id]
        if boundary_order.import_batch_id not in publication_batches:
            raise FuturePositionEpisodePreviewError(
                "post-boundary aggregate order is outside the publication chain"
            )
        if not _is_option(order.instrument):
            excluded_non_option += 1
            continue
        post_orders.append(order)

    post_fill_ids = {item.observation_id for item in post_fills}
    supporting_order_ids = {
        item.broker_order_observation_id
        for item in post_fills
        if item.broker_order_observation_id is not None
    }
    all_fills_by_order: dict[int, set[int]] = {}
    for fill in projection.fills:
        if fill.broker_order_observation_id is not None:
            all_fills_by_order.setdefault(
                fill.broker_order_observation_id,
                set(),
            ).add(fill.observation_id)
    supporting_orders: list[CanonicalOrderEvidence] = []
    for order_id in sorted(supporting_order_ids):
        order = builder_order_by_id.get(order_id)
        if order is None:
            raise FuturePositionEpisodePreviewError(
                "post-boundary fill has no frozen parent order"
            )
        if order.evidence_level.strip().lower() != "fill_detail":
            raise FuturePositionEpisodePreviewError(
                "post-boundary fill parent is not detail-backed"
            )
        if all_fills_by_order.get(order_id, set()) - post_fill_ids:
            raise FuturePositionEpisodePreviewError(
                "a parent order fill set crosses the preview boundary or scope"
            )
        if not _is_option(order.instrument):
            raise FuturePositionEpisodePreviewError(
                "option fill is linked to an out-of-scope parent order"
            )
        supporting_orders.append(order)

    included_groups: list[VerifiedCanonicalExecutionGroup] = []
    for group in projection.execution_groups:
        group_fill_ids = set(group.selected_fill_ids)
        if not group_fill_ids.intersection(post_fill_ids):
            continue
        if not group_fill_ids or not group_fill_ids.issubset(post_fill_ids):
            raise FuturePositionEpisodePreviewError(
                "an execution group crosses the preview boundary or scope"
            )
        if group.total_fee is None or not group.currency:
            raise FuturePositionEpisodePreviewError(
                "included execution group has no exact group-scoped fee"
            )
        included_groups.append(group)

    included_orders = tuple(
        sorted(
            (*post_orders, *supporting_orders),
            key=lambda item: (item.source_sequence, item.observation_key),
        )
    )
    included_fills = tuple(
        sorted(
            post_fills,
            key=lambda item: (item.source_sequence, item.observation_key),
        )
    )
    return (
        included_orders,
        included_fills,
        tuple(included_groups),
        len(supporting_orders),
        excluded_non_option,
    )


def _fee_summary(
    episodes: tuple[PositionEpisodeRecord, ...],
    groups: tuple[VerifiedCanonicalExecutionGroup, ...],
) -> tuple[
    Decimal,
    Decimal,
    Decimal,
    dict[str, dict[str, Decimal]],
    bool,
    frozenset[str],
]:
    ordinary_by_currency: dict[str, Decimal] = {}
    group_fill_ids = {
        fill_id for group in groups for fill_id in group.selected_fill_ids
    }
    affected_episode_keys: set[str] = set()
    for episode in episodes:
        currency = episode.instrument.currency.strip().upper()
        for allocation in episode.evidence:
            if allocation.broker_fill_observation_id in group_fill_ids:
                affected_episode_keys.add(episode.episode_key)
            if allocation.allocated_fee is not None:
                ordinary_by_currency[currency] = (
                    ordinary_by_currency.get(currency, Decimal("0"))
                    + allocation.allocated_fee
                ).quantize(_STORAGE_QUANTUM)

    retained_by_currency: dict[str, Decimal] = {}
    for group in groups:
        assert group.total_fee is not None
        retained_by_currency[group.currency] = (
            retained_by_currency.get(group.currency, Decimal("0"))
            + group.total_fee
        ).quantize(_STORAGE_QUANTUM)

    by_currency: dict[str, dict[str, Decimal]] = {}
    for currency in sorted({*ordinary_by_currency, *retained_by_currency}):
        ordinary = ordinary_by_currency.get(
            currency,
            Decimal("0"),
        ).quantize(_STORAGE_QUANTUM)
        retained = retained_by_currency.get(
            currency,
            Decimal("0"),
        ).quantize(_STORAGE_QUANTUM)
        source_known = ordinary + retained
        by_currency[currency] = {
            "ordinary_source": ordinary,
            "ordinary_allocated": ordinary,
            "retained_execution_group": retained,
            "source_known": source_known,
            "accounted": source_known,
        }
    source_total = sum(
        (values["source_known"] for values in by_currency.values()),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    accounted_total = sum(
        (values["accounted"] for values in by_currency.values()),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    retained_total = sum(
        retained_by_currency.values(),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    conserved = source_total == accounted_total
    return (
        source_total,
        accounted_total,
        retained_total,
        by_currency,
        conserved,
        frozenset(affected_episode_keys),
    )


def _total(
    episodes: tuple[PositionEpisodeRecord, ...],
    field_name: str,
) -> Optional[Decimal]:
    if not episodes:
        return None
    values = [getattr(episode, field_name) for episode in episodes]
    if any(value is None for value in values):
        return None
    return sum(
        (value for value in values if value is not None),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)


@dataclass(frozen=True)
class _FencedPreviewComputation:
    """One in-session preview plus the internals the confirm path persists.

    Only ``preview`` is part of the public read contract.  The remaining
    fields let the formal build append reuse the exact computed evidence
    identity without re-deriving it outside the same transaction.
    """

    preview: FuturePositionEpisodeBuildPreview
    group_fee_affected_episode_keys: frozenset[str]
    source_order_ids: frozenset[int]
    source_fill_ids: frozenset[int]
    target_canonical_set_key: str
    snapshot_member_set_sha256: str
    snapshot_provenance_sha256: str


def _validated_preview_request(
    account_key: str,
    expected_fence_key: str,
) -> tuple[str, str]:
    normalized_account = str(account_key or "").strip()
    normalized_fence = str(expected_fence_key or "").strip()
    if not normalized_account or len(normalized_account) > 64:
        raise FuturePositionEpisodePreviewError("account_key is invalid")
    if len(normalized_fence) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_fence
    ):
        raise FuturePositionEpisodePreviewError(
            "expected_fence_key must be a lowercase SHA-256"
        )
    return normalized_account, normalized_fence


def _compute_fenced_position_episode_preview(
    connection: Connection,
    session: Session,
    account_key: str,
    expected_fence_key: str,
    *,
    require_readonly_session: bool = True,
) -> _FencedPreviewComputation:
    """Run the pure preview core inside the caller's pinned transaction."""
    normalized_account, normalized_fence = _validated_preview_request(
        account_key,
        expected_fence_key,
    )
    try:
            context = assess_position_snapshot_continuity_in_session(
                connection,
                session,
                normalized_account,
                include_episode_projection=True,
                max_member_count=(
                    FUTURE_POSITION_EPISODE_MAX_CANONICAL_MEMBERS
                ),
                require_readonly_session=require_readonly_session,
            )
            assessment = context.assessment
            if (
                assessment.status != "ready"
                or not assessment.ready_for_episode_build
                or assessment.fence_key is None
            ):
                raise FuturePositionEpisodePreviewError(
                    "latest position continuity fence is not ready"
                )
            if normalized_fence != assessment.fence_key:
                raise FuturePositionEpisodePreviewError(
                    "position continuity fence changed; request a new preview"
                )
            snapshot = context.snapshot
            projection = context.target_projection
            if snapshot is None or projection is None:
                raise FuturePositionEpisodePreviewError(
                    "ready continuity context is missing frozen evidence"
                )
            if (
                assessment.snapshot_id != snapshot.snapshot_id
                or assessment.snapshot_key != snapshot.snapshot_key
                or assessment.target_canonical_set_id
                != projection.boundary.canonical_set_id
                or assessment.target_canonical_set_sha256
                != projection.boundary.canonical_set_sha256
                or assessment.boundary_at is None
                or assessment.target_publication_id is None
                or assessment.target_publication_key is None
            ):
                raise FuturePositionEpisodePreviewError(
                    "ready continuity context has inconsistent frozen identity"
                )
            canonical_member_count = projection.canonical_member_count
            if (
                canonical_member_count
                > FUTURE_POSITION_EPISODE_MAX_CANONICAL_MEMBERS
            ):
                raise FuturePositionEpisodePreviewError(
                    "canonical evidence member count exceeds the preview limit"
                )
            boundary_at = assessment.boundary_at
            source_cutoff_at = projection.boundary.source_cutoff_at
            if source_cutoff_at < boundary_at:
                raise FuturePositionEpisodePreviewError(
                    "target canonical cutoff precedes the snapshot boundary"
                )

            (
                projected_orders,
                projected_fills,
                projected_groups,
                supporting_order_count,
                excluded_non_option_count,
            ) = _window_projection(
                projection,
                publication_source_batch_ids=assessment.source_batch_ids,
                boundary_at=boundary_at,
                source_cutoff_at=source_cutoff_at,
            )
            (
                opening_positions,
                normalized_orders,
                normalized_fills,
            ) = _normalize_instruments(
                account_key=normalized_account,
                members=snapshot.members,
                orders=projected_orders,
                fills=projected_fills,
                boundary_at=boundary_at,
            )

            config = {
                "source_kind": SNAPSHOT_FENCE_SOURCE_KIND,
                "scope": _SCOPE,
                "boundary_inclusion_policy": _BOUNDARY_POLICY,
                "instrument_identity_policy": _IDENTITY_POLICY,
                "opening_cost_policy": _COST_POLICY,
                "execution_group_fee_policy": _GROUP_FEE_POLICY,
                "opening_snapshot_complete": True,
                "assume_flat_if_missing": False,
                "exchange_timezone": "America/New_York",
                "strategy_grouping_policy": "single_position_unclassified",
                "spread_inference": False,
                "roll_inference": False,
                "projection": {
                    "name": FUTURE_POSITION_EPISODE_PROJECTION_NAME,
                    "version": FUTURE_POSITION_EPISODE_PROJECTION_VERSION,
                },
            }
            builder_config_sha256 = _sha256(config)
            evidence_set_sha256 = _sha256(
                {
                    "fence_key": assessment.fence_key,
                    "snapshot": {
                        "id": snapshot.snapshot_id,
                        "key": snapshot.snapshot_key,
                        "member_set_sha256": snapshot.member_set_sha256,
                        "provenance_sha256": snapshot.provenance_sha256,
                    },
                    "target_canonical": {
                        "id": projection.boundary.canonical_set_id,
                        "key": projection.canonical_set_key,
                        "sha256": projection.boundary.canonical_set_sha256,
                    },
                    "boundary_at": boundary_at,
                    "source_cutoff_at": source_cutoff_at,
                    "opening_positions": tuple(
                        {
                            "instrument": item.instrument.canonical_payload(),
                            "signed_quantity": item.signed_quantity,
                        }
                        for item in opening_positions
                    ),
                    "order_observation_ids": tuple(
                        item.observation_id for item in normalized_orders
                    ),
                    "fill_observation_ids": tuple(
                        item.observation_id for item in normalized_fills
                    ),
                    "execution_group_member_ids": tuple(
                        item.member_id for item in projected_groups
                    ),
                }
            )
            result = build_position_episodes(
                normalized_orders,
                normalized_fills,
                source_window_start=boundary_at,
                source_cutoff_at=source_cutoff_at,
                opening_positions=opening_positions,
                opening_snapshot_complete=True,
                assume_flat_if_missing=False,
                exchange_timezone="America/New_York",
            )
            if result.opening_boundary_policy != "complete_snapshot":
                raise FuturePositionEpisodePreviewError(
                    "builder did not preserve the complete snapshot boundary"
                )
            if any(not episode.left_boundary_verified for episode in result.episodes):
                raise FuturePositionEpisodePreviewError(
                    "builder emitted a boundary-unverified future episode"
                )
            if any(
                episode.opening_boundary_policy == "explicit_opening_position"
                and not episode.is_left_censored
                for episode in result.episodes
            ):
                raise FuturePositionEpisodePreviewError(
                    "snapshot opening episode lost its left-censored status"
                )
            expected_evidence_keys = {
                item.observation_key for item in normalized_orders
                if item.evidence_level.strip().lower() == "aggregate_only"
            } | {item.observation_key for item in normalized_fills}
            allocated_evidence_keys = {
                allocation.evidence_key
                for episode in result.episodes
                for allocation in episode.evidence
            }
            if expected_evidence_keys != allocated_evidence_keys:
                raise FuturePositionEpisodePreviewError(
                    "builder did not allocate the exact boundary evidence set"
                )

            (
                source_known_fee_total,
                accounted_known_fee_total,
                retained_group_fee_total,
                fee_by_currency,
                fee_conserved,
                group_affected_keys,
            ) = _fee_summary(result.episodes, projected_groups)
            if (
                result.source_known_fee_total
                != result.allocated_known_fee_total
                or not fee_conserved
            ):
                raise FuturePositionEpisodePreviewError(
                    "future preview did not conserve known source fees"
                )

            qualified = tuple(
                episode
                for episode in result.episodes
                if episode.lifecycle_status == "closed"
                and episode.left_boundary_verified
                and not episode.is_left_censored
                and episode.completeness_status in {"exact", "complete"}
                and episode.realized_pnl_net is not None
                and episode.episode_key not in group_affected_keys
            )
            warnings: list[str] = []
            if any(item.is_left_censored for item in result.episodes):
                warnings.append("opening_positions_remain_left_censored")
            if projected_groups:
                warnings.append("execution_group_fee_retained_unallocated")
            if excluded_non_option_count:
                warnings.append("non_option_evidence_excluded_by_scope")
            if result.source_event_count == 0:
                warnings.append("no_post_boundary_execution_events")

            # The planned build key must freeze every identity input listed in
            # the formal future-build contract: snapshot id/key, fence, target
            # canonical id/key/sha256/cutoff, boundary, builder identity and
            # config, and the projection identity.
            planned_build_key = _sha256(
                {
                    "source_kind": SNAPSHOT_FENCE_SOURCE_KIND,
                    "fence_key": assessment.fence_key,
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_key": snapshot.snapshot_key,
                    "target_publication_id": assessment.target_publication_id,
                    "target_publication_key": (
                        assessment.target_publication_key
                    ),
                    "target_canonical_set_id": (
                        projection.boundary.canonical_set_id
                    ),
                    "target_canonical_set_key": projection.canonical_set_key,
                    "target_canonical_set_sha256": (
                        projection.boundary.canonical_set_sha256
                    ),
                    "source_window_start": boundary_at,
                    "source_window_end": source_cutoff_at,
                    "evidence_set_sha256": evidence_set_sha256,
                    "builder": {
                        "name": BUILDER_NAME,
                        "version": BUILDER_VERSION,
                    },
                    "builder_config_sha256": builder_config_sha256,
                    "projection": {
                        "name": FUTURE_POSITION_EPISODE_PROJECTION_NAME,
                        "version": (
                            FUTURE_POSITION_EPISODE_PROJECTION_VERSION
                        ),
                    },
                }
            )
            counts = FuturePositionEpisodeBuildCounts(
                canonical_member_count=canonical_member_count,
                opening_position_count=len(opening_positions),
                opening_contract_count=sum(
                    (abs(item.signed_quantity) for item in opening_positions),
                    Decimal("0"),
                ).quantize(_STORAGE_QUANTUM),
                post_boundary_order_event_count=sum(
                    item.evidence_level.strip().lower() == "aggregate_only"
                    for item in normalized_orders
                ),
                post_boundary_fill_event_count=len(normalized_fills),
                supporting_order_count=supporting_order_count,
                excluded_non_option_event_count=excluded_non_option_count,
                execution_group_count=len(projected_groups),
                source_event_count=result.source_event_count,
                planned_position_episode_count=len(result.episodes),
                planned_open_episode_count=sum(
                    item.lifecycle_status == "open" for item in result.episodes
                ),
                planned_closed_episode_count=sum(
                    item.lifecycle_status == "closed" for item in result.episodes
                ),
                left_censored_episode_count=sum(
                    item.is_left_censored for item in result.episodes
                ),
                right_censored_episode_count=sum(
                    item.is_right_censored for item in result.episodes
                ),
                headline_episode_count=len(qualified),
                headline_excluded_episode_count=(
                    len(result.episodes) - len(qualified)
                ),
                group_fee_affected_episode_count=len(group_affected_keys),
            )
            preview = FuturePositionEpisodeBuildPreview(
                projection_name=FUTURE_POSITION_EPISODE_PROJECTION_NAME,
                projection_version=FUTURE_POSITION_EPISODE_PROJECTION_VERSION,
                continuity_policy_version=(
                    POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION
                ),
                account_key=normalized_account,
                scope=_SCOPE,
                fence_key=assessment.fence_key,
                snapshot_id=snapshot.snapshot_id,
                snapshot_key=snapshot.snapshot_key,
                target_publication_id=assessment.target_publication_id,
                target_publication_key=assessment.target_publication_key,
                target_canonical_set_id=projection.boundary.canonical_set_id,
                target_canonical_set_sha256=(
                    projection.boundary.canonical_set_sha256
                ),
                canonical_source_batch_ids=(
                    projection.boundary.source_batch_ids
                ),
                publication_source_batch_ids=assessment.source_batch_ids,
                boundary_at=boundary_at,
                source_cutoff_at=source_cutoff_at,
                builder_name=BUILDER_NAME,
                builder_version=BUILDER_VERSION,
                builder_config_sha256=builder_config_sha256,
                evidence_set_sha256=evidence_set_sha256,
                planned_build_key=planned_build_key,
                opening_boundary_policy=result.opening_boundary_policy,
                max_multiplier_proof_residual=(
                    projection.max_multiplier_proof_residual
                ),
                source_known_fee_total=source_known_fee_total,
                accounted_known_fee_total=accounted_known_fee_total,
                retained_execution_group_fee_total=(
                    retained_group_fee_total
                ),
                fee_conservation_by_currency=fee_by_currency,
                fee_conserved=fee_conserved,
                headline_realized_pnl_gross=_total(
                    qualified,
                    "realized_pnl_gross",
                ),
                headline_total_fee=_total(qualified, "total_fee"),
                headline_realized_pnl_net=_total(
                    qualified,
                    "realized_pnl_net",
                ),
                counts=counts,
                warnings=tuple(warnings),
                episodes=result.episodes,
            )
            return _FencedPreviewComputation(
                preview=preview,
                group_fee_affected_episode_keys=group_affected_keys,
                source_order_ids=frozenset(
                    item.observation_id for item in normalized_orders
                ),
                source_fill_ids=frozenset(
                    item.observation_id for item in normalized_fills
                ),
                target_canonical_set_key=projection.canonical_set_key,
                snapshot_member_set_sha256=snapshot.member_set_sha256,
                snapshot_provenance_sha256=snapshot.provenance_sha256,
            )
    except FuturePositionEpisodePreviewError:
        raise
    except PositionSnapshotContinuityError as exc:
        raise FuturePositionEpisodePreviewError(str(exc)) from exc
    except EpisodeBuildError as exc:
        raise FuturePositionEpisodePreviewError(str(exc)) from exc


def preview_fenced_position_episodes_in_session(
    connection: Connection,
    session: Session,
    account_key: str,
    expected_fence_key: str,
) -> FuturePositionEpisodeBuildPreview:
    """Compute the fenced preview inside the caller's pinned read session."""
    return _compute_fenced_position_episode_preview(
        connection,
        session,
        account_key,
        expected_fence_key,
    ).preview


def preview_fenced_position_episodes(
    account_key: str,
    expected_fence_key: str,
) -> FuturePositionEpisodeBuildPreview:
    """Build a deterministic in-memory preview after CAS-checking the fence."""
    _validated_preview_request(account_key, expected_fence_key)
    try:
        with open_position_snapshot_continuity_session() as (
            connection,
            session,
        ):
            return _compute_fenced_position_episode_preview(
                connection,
                session,
                account_key,
                expected_fence_key,
            ).preview
    except FuturePositionEpisodePreviewError:
        raise
    except PositionSnapshotContinuityError as exc:
        raise FuturePositionEpisodePreviewError(str(exc)) from exc
    except EpisodeBuildError as exc:
        raise FuturePositionEpisodePreviewError(str(exc)) from exc
