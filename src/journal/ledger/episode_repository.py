# -*- coding: utf-8 -*-
"""Append-only persistence for deterministic Journal v2 position episodes.

The repository projects only the newest accepted import batch for an account
into the pure episode builder.  It deliberately uses a one-position strategy
container and records that no spread or roll inference has taken place.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Literal, Mapping, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from src.journal.ledger.activation_repository import (
    CANONICAL_SOURCE_KIND,
    SNAPSHOT_FENCE_SOURCE_KIND,
    EpisodeBuildActivationError,
    _resolve_effective_episode_build,
)
from src.journal.ledger.episodes import (
    BUILDER_NAME,
    BUILDER_VERSION,
    CanonicalFillEvidence,
    CanonicalOrderEvidence,
    InstrumentIdentity,
    PositionEpisodeRecord,
    build_position_episodes,
)
from src.journal.ledger.canonical import stable_source_sequence
from src.journal.ledger.models import (
    BrokerExecutionGroupFillLink,
    BrokerExecutionGroupLegObservation,
    BrokerExecutionGroupObservation,
    BrokerFillObservation,
    BrokerOrderObservation,
    CanonicalEvidenceMemberRecord,
    CanonicalEvidenceSetRecord,
    CanonicalExecutionGroupMemberRecord,
    CanonicalExecutionGroupFillLinkRecord,
    CanonicalExecutionGroupLegRecord,
    EpisodeBuild,
    EpisodeBuildCanonicalSource,
    EpisodeBuildSnapshotFenceSource,
    ImportBatch,
    PositionEpisode,
    PositionEpisodeEvidence,
    ReconciliationAttestation,
    ReviewAnnotation,
    StrategyEpisode,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.storage import get_db

__all__ = [
    "CanonicalBoundaryFill",
    "CanonicalBoundaryOrder",
    "VerifiedCanonicalEpisodeEvidenceProjection",
    "VerifiedCanonicalExecutionGroup",
    "EpisodeBuildAppendResult",
    "EpisodeBuildPreview",
    "EpisodeBuildSummary",
    "EpisodeRepositoryError",
    "PositionEpisodeDetail",
    "PositionEpisodeEvidenceItem",
    "PositionEpisodeCaseFocus",
    "PositionEpisodeListItem",
    "PositionEpisodePage",
    "PositionEpisodeReviewQueueCounts",
    "VerifiedCanonicalEvidenceProjection",
    "append_latest_position_episode_build",
    "append_canonical_position_episode_build",
    "get_episode_summary",
    "get_position_episode_page",
    "get_latest_episode_summary",
    "get_latest_position_episode_detail",
    "get_latest_position_episode_page",
    "get_position_episode_detail",
    "list_latest_position_episodes",
    "position_episode_pnl_exclusion_reasons",
    "load_verified_canonical_evidence_projection",
    "load_verified_canonical_episode_evidence_projection",
    "preview_latest_position_episodes",
    "preview_canonical_position_episodes",
]


_STORAGE_QUANTUM = Decimal("0.0000000001")
_SCORE_QUANTUM = Decimal("0.0001")
_AMOUNT_HALF_UNIT_TOLERANCE = Decimal("0.005")
_EXCHANGE_TIMEZONE = "America/New_York"
_GROUPING_METHOD = "one_to_one_no_strategy_inference"
_STRATEGY_TYPE = "single_position_unclassified"
_CSV_SOURCE_KIND = "csv_batch"
# Canonical and snapshot-fence source kinds are defined once in the
# activation repository (this module's import direction points there) and
# re-exported here for the fenced future preview/confirm modules; a formal
# future build is linked by a snapshot-fence source row instead of a
# canonical link.
_CANONICAL_SOURCE_KIND = CANONICAL_SOURCE_KIND
_CANONICAL_PROJECTION_NAME = "persisted_canonical_member_projection"
_CANONICAL_PROJECTION_VERSION = "1.1.0"
_EXECUTION_GROUP_FEE_POLICY = "retained_at_group_scope_not_leg_allocated"


PositionEpisodeCaseFocus = Literal[
    "top_profit",
    "top_loss",
    "largest_fee",
    "longest_hold",
]
PositionEpisodeReviewStatus = Literal[
    "not_started",
    "in_progress",
    "completed",
]
_POSITION_EPISODE_CASE_FOCUSES = {
    "top_profit",
    "top_loss",
    "largest_fee",
    "longest_hold",
    "weakest_evidence",
}
_POSITION_EPISODE_REVIEW_STATUSES = {
    "not_started",
    "in_progress",
    "completed",
}


class EpisodeRepositoryError(ValueError):
    """Raised when evidence cannot be projected or an append is not approved."""


@dataclass(frozen=True)
class CanonicalBoundaryOrder:
    """Builder-aligned canonical order facts needed by a boundary fence."""

    member_id: int
    selected_observation_id: int
    import_batch_id: int
    identity: tuple[str, str, str]
    instrument: InstrumentIdentity
    ordered_at: datetime
    evidence_level: str
    filled_quantity: Optional[Decimal]
    economic_sha256: str


@dataclass(frozen=True)
class CanonicalBoundaryFill:
    """Builder-aligned canonical fill facts needed by a boundary fence."""

    member_id: int
    selected_observation_id: int
    import_batch_id: int
    identity: tuple[str, str, str]
    linked_order_identity: Optional[tuple[str, str, str]]
    execution_group_identity: Optional[tuple[str, str, str]]
    execution_group_member_id: Optional[int]
    instrument: InstrumentIdentity
    filled_at: datetime


@dataclass(frozen=True)
class VerifiedCanonicalEvidenceProjection:
    """Fully replay-verified canonical evidence without building Episodes."""

    canonical_set_id: int
    canonical_set_sha256: str
    source_cutoff_at: datetime
    source_batch_ids: tuple[int, ...]
    orders: tuple[CanonicalBoundaryOrder, ...]
    fills: tuple[CanonicalBoundaryFill, ...]


@dataclass(frozen=True)
class VerifiedCanonicalExecutionGroup:
    """One replay-verified group and the selected fills it owns."""

    identity: tuple[str, str, str]
    member_id: int
    import_batch_id: int
    currency: str
    total_fee: Optional[Decimal]
    selected_fill_ids: tuple[int, ...]


@dataclass(frozen=True)
class VerifiedCanonicalEpisodeEvidenceProjection:
    """Full builder projection produced by one frozen canonical replay.

    ``boundary`` and the builder inputs are intentionally returned together.
    A continuity consumer can therefore classify the same exact evidence that
    a later pure Episode preview consumes without re-reading raw observations
    or running canonical identity selection again.
    """

    canonical_set_key: str
    canonical_member_count: int
    boundary: VerifiedCanonicalEvidenceProjection
    orders: tuple[CanonicalOrderEvidence, ...]
    fills: tuple[CanonicalFillEvidence, ...]
    execution_groups: tuple[VerifiedCanonicalExecutionGroup, ...]
    max_multiplier_proof_residual: Decimal


@dataclass(frozen=True)
class EpisodeBuildPreview:
    """Pure preview of the newest accepted batch before any episode write."""

    batch_id: int
    batch_key: str
    account_key: str
    parser_name: str
    parser_version: str
    source_kind: str
    source_batch_ids: tuple[int, ...]
    canonical_set_id: Optional[int]
    canonical_set_sha256: Optional[str]
    builder_name: str
    builder_version: str
    build_key: str
    builder_config_sha256: str
    evidence_set_sha256: str
    multiplier_amount_tolerance: Decimal
    max_multiplier_proof_residual: Decimal
    status: str
    reconciliation_status: str
    reconciliation_scope: str
    reconciliation_window_start: Optional[datetime]
    reconciliation_window_end: Optional[datetime]
    reconciled_order_count: int
    total_order_count: int
    completeness_score: Decimal
    opening_boundary_policy: str
    partial_reasons: tuple[str, ...]
    source_window_start: datetime
    source_cutoff_at: datetime
    source_event_count: int
    aggregate_order_event_count: int
    detailed_fill_event_count: int
    source_known_fee_total: Decimal
    allocated_known_fee_total: Decimal
    retained_execution_group_fee_total: Decimal
    fee_conservation_by_currency: Mapping[str, Mapping[str, Decimal]]
    fee_conserved: bool
    execution_group_count: int
    group_fee_affected_episode_count: int
    leg_fee_attribution_complete: bool
    unresolved_evidence_count: int
    open_episode_count: int
    closed_episode_count: int
    boundary_unverified_episode_count: int
    left_censored_episode_count: int
    incomplete_episode_count: int
    aggregate_only_episode_count: int
    # Closed cash-flow result conditional on ``opening_boundary_policy``.
    # It is deliberately separate from the verified-boundary headline.
    conditional_closed_episode_count: int
    conditional_realized_pnl_gross: Optional[Decimal]
    conditional_total_fee: Optional[Decimal]
    conditional_realized_pnl_net: Optional[Decimal]
    headline_episode_count: int
    headline_realized_pnl_gross: Optional[Decimal]
    headline_total_fee: Optional[Decimal]
    headline_realized_pnl_net: Optional[Decimal]
    headline_excluded_episode_count: int
    headline_exclusion_counts: Mapping[str, int]
    episodes: tuple[PositionEpisodeRecord, ...]


@dataclass(frozen=True)
class EpisodeBuildAppendResult:
    """Identity and counts for an idempotent episode-build append."""

    build_id: int
    build_key: str
    duplicate: bool
    status: str
    strategy_episode_count: int
    position_episode_count: int
    evidence_allocation_count: int
    opening_boundary_policy: str


@dataclass(frozen=True)
class EpisodeBuildSummary:
    """Stored summary for the newest episode build of an account."""

    build_id: int
    build_key: str
    source_batch_id: int
    source_batch_ids: tuple[int, ...]
    source_kind: str
    canonical_set_id: Optional[int]
    canonical_set_sha256: Optional[str]
    account_key: str
    status: str
    builder_name: str
    builder_version: str
    reconciliation_status: str
    reconciliation_scope: str
    reconciliation_window_start: Optional[datetime]
    reconciliation_window_end: Optional[datetime]
    reconciled_order_count: int
    total_order_count: int
    completeness_score: Decimal
    multiplier_amount_tolerance: Decimal
    max_multiplier_proof_residual: Decimal
    opening_boundary_policy: str
    partial_reasons: tuple[str, ...]
    strategy_episode_count: int
    position_episode_count: int
    evidence_allocation_count: int
    unresolved_evidence_count: int
    open_episode_count: int
    closed_episode_count: int
    boundary_unverified_episode_count: int
    left_censored_episode_count: int
    incomplete_episode_count: int
    aggregate_only_episode_count: int
    # Closed cash-flow result conditional on ``opening_boundary_policy``.
    # It is deliberately separate from the verified-boundary headline.
    conditional_closed_episode_count: int
    conditional_realized_pnl_gross: Optional[Decimal]
    conditional_total_fee: Optional[Decimal]
    conditional_realized_pnl_net: Optional[Decimal]
    headline_episode_count: int
    headline_realized_pnl_gross: Optional[Decimal]
    headline_total_fee: Optional[Decimal]
    headline_realized_pnl_net: Optional[Decimal]
    headline_excluded_episode_count: int
    headline_exclusion_counts: Mapping[str, int]
    source_event_count: int
    aggregate_order_event_count: int
    detailed_fill_event_count: int
    source_known_fee_total: Decimal
    allocated_known_fee_total: Decimal
    retained_execution_group_fee_total: Decimal
    fee_conservation_by_currency: Mapping[str, Mapping[str, Decimal]]
    fee_conserved: bool
    execution_group_count: int
    group_fee_affected_episode_count: int
    leg_fee_attribution_complete: bool
    source_window_start: datetime
    source_cutoff_at: datetime
    recorded_at: datetime


@dataclass(frozen=True)
class PositionEpisodeListItem:
    """User-facing stored position episode without raw broker payloads."""

    episode_id: int
    build_id: int
    strategy_episode_id: int
    episode_key: str
    lineage_key: str
    strategy_type: str
    raw_symbol: str
    asset_type: str
    underlying: str
    expiry: Optional[date]
    strike: Optional[Decimal]
    option_right: Optional[str]
    contract_multiplier: Optional[Decimal]
    contract_multiplier_basis: str
    direction: str
    lifecycle_status: str
    currency: str
    opened_at: datetime
    closed_at: Optional[datetime]
    hold_seconds: Optional[int]
    opened_quantity: Decimal
    closed_quantity: Decimal
    remaining_quantity: Decimal
    average_entry_price: Optional[Decimal]
    average_exit_price: Optional[Decimal]
    realized_pnl_gross: Optional[Decimal]
    total_fee: Optional[Decimal]
    realized_pnl_net: Optional[Decimal]
    construction_basis: str
    left_boundary_verified: bool
    opening_boundary_policy: str
    is_left_censored: bool
    is_right_censored: bool
    completeness_score: Decimal
    completeness_status: str
    review_status: PositionEpisodeReviewStatus = "not_started"
    review_revision: Optional[int] = None
    review_updated_at: Optional[datetime] = None
    group_fee_unallocated: bool = False


@dataclass(frozen=True)
class PositionEpisodeReviewQueueCounts:
    """Latest-review counts within the list's non-review filter scope."""

    pending: int = 0
    in_progress: int = 0
    completed: int = 0

    @property
    def total(self) -> int:
        return self.pending + self.in_progress + self.completed


@dataclass(frozen=True)
class PositionEpisodePage:
    """Stable page of episodes from the account's newest build only."""

    build_id: Optional[int]
    items: tuple[PositionEpisodeListItem, ...]
    total: int
    page: int
    per_page: int
    review_queue: PositionEpisodeReviewQueueCounts = field(
        default_factory=PositionEpisodeReviewQueueCounts
    )


@dataclass(frozen=True)
class PositionEpisodeEvidenceItem:
    """One persisted allocation linked by a real order or fill foreign key."""

    evidence_id: int
    evidence_key: str
    evidence_kind: str
    event_role: str
    allocation_sequence: int
    evidence_time: datetime
    allocated_quantity: Optional[Decimal]
    allocated_fee: Optional[Decimal]
    allocated_cash_flow: Optional[Decimal]
    allocation_ratio: Optional[Decimal]
    broker_order_observation_id: Optional[int]
    broker_fill_observation_id: Optional[int]
    # 成交证据的父订单 id（fill 行经 BrokerFillObservation 联表补出）；
    # 证据行本身受 order XOR fill 约束，不能直接携带两个 id。
    parent_broker_order_observation_id: Optional[int]
    allocation_evidence: Mapping[str, Any]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class PositionEpisodeDetail:
    """Episode plus parsed quality metadata and immutable allocations."""

    episode: PositionEpisodeListItem
    matching_evidence: Mapping[str, Any]
    evidence_summary: Mapping[str, Any]
    completeness: Mapping[str, Any]
    provenance: Mapping[str, Any]
    evidence: tuple[PositionEpisodeEvidenceItem, ...]


@dataclass(frozen=True)
class _PreparedBuild:
    preview: EpisodeBuildPreview
    batch_source_sha256: str
    batch_analysis_level: str
    source_order_ids: frozenset[int]
    source_fill_ids: frozenset[int]
    group_fee_affected_episode_keys: frozenset[str] = frozenset()
    canonical_set_key: Optional[str] = None
    canonical_source_cutoff_at: Optional[datetime] = None
    # Snapshot-fence identity payload for a formal future build.  It is set
    # by the confirm path only and recorded verbatim inside provenance JSON.
    snapshot_fence_provenance: Optional[Mapping[str, Any]] = None


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


_CANONICAL_ROOT_DECIMAL_FIELDS = frozenset(
    {
        "filled_quantity",
        "average_fill_price",
        "amount",
        "total_fee",
        "quantity",
        "price",
        "package_quantity",
        "broker_reported_filled_package_quantity",
        "broker_reported_order_net_price",
        "broker_reported_average_net_price",
        "proved_executed_group_quantity",
        "quantity_ratio",
        "expected_filled_quantity",
    }
)


def _canonical_root_hash_payload(value: Any, *, field_name: str = "") -> Any:
    """Restore the canonical reader's numeric JSON representation.

    Early persistence used ``format(decimal, 'f')`` while the canonical reader
    hashes normalized decimal text.  Frozen roots therefore legitimately hold
    values such as ``"1.0000000000"`` even though the set hash was calculated
    from ``"1"``.  Normalize only schema-defined decimal fields (and fee
    component amounts) so historical roots can be verified without treating
    arbitrary identifiers as numbers.
    """
    if isinstance(value, dict):
        return {
            key: _canonical_root_hash_payload(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        if field_name == "fee_components":
            normalized: list[Any] = []
            for component in value:
                if not isinstance(component, list) or len(component) != 2:
                    raise EpisodeRepositoryError(
                        "canonical fee component payload is invalid"
                    )
                normalized.append(
                    [
                        component[0],
                        _canonical_root_hash_payload(
                            component[1],
                            field_name="fee_component_amount",
                        ),
                    ]
                )
            return normalized
        return [
            _canonical_root_hash_payload(item, field_name=field_name)
            for item in value
        ]
    if value is None or field_name not in (
        _CANONICAL_ROOT_DECIMAL_FIELDS | {"fee_component_amount"}
    ):
        return value
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EpisodeRepositoryError(
            f"canonical {field_name} is not a valid decimal"
        ) from exc
    if not decimal_value.is_finite():
        raise EpisodeRepositoryError(
            f"canonical {field_name} must be finite"
        )
    if decimal_value == 0:
        return "0"
    return format(decimal_value.normalize(), "f")


def _parse_json(value: Optional[str]) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc(value: Optional[datetime], *, field_name: str) -> datetime:
    if value is None:
        raise EpisodeRepositoryError(f"{field_name} is required")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return _utc(value, field_name="timestamp")


def _decimal(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(value)


def _derive_multiplier(
    *,
    asset_type: str,
    amount: Optional[Decimal],
    quantity: Optional[Decimal],
    price: Optional[Decimal],
    evidence_label: str,
    execution_evidence: bool,
) -> tuple[Optional[Decimal], str, Optional[Decimal]]:
    """Prove the asset-appropriate multiplier within broker cent rounding."""
    amount = _decimal(amount)
    quantity = _decimal(quantity)
    price = _decimal(price)
    can_derive = (
        amount is not None
        and abs(amount) > 0
        and quantity is not None
        and price is not None
        and quantity > 0
        and price > 0
    )
    if not can_derive:
        if execution_evidence:
            raise EpisodeRepositoryError(
                f"{evidence_label} cannot prove the contract multiplier "
                "from amount/(quantity*price)"
            )
        return None, "unknown", None
    normalized_type = asset_type.strip().lower()
    candidates = {
        "equity": (Decimal("1"),),
        "option": (Decimal("100"),),
    }.get(normalized_type)
    if candidates is None:
        raise EpisodeRepositoryError(
            f"{evidence_label} has unsupported multiplier asset type: "
            f"{asset_type}"
        )
    observed = abs(amount)
    ranked = sorted(
        (
            (abs(observed - quantity * price * candidate), candidate)
            for candidate in candidates
        ),
        key=lambda item: item[0],
    )
    residual, multiplier = ranked[0]
    if residual > _AMOUNT_HALF_UNIT_TOLERANCE:
        raise EpisodeRepositoryError(
            f"{evidence_label} amount residual {residual} exceeds "
            f"{_AMOUNT_HALF_UNIT_TOLERANCE}"
        )
    return multiplier, "evidence_derived_from_amount", residual


def _instrument_key(
    row: BrokerOrderObservation | BrokerFillObservation,
) -> tuple[str, ...]:
    return (
        str(row.broker).strip().lower(),
        str(row.account_key).strip(),
        str(row.raw_symbol).strip().upper(),
        str(row.asset_type).strip().lower(),
        str(row.underlying).strip().upper(),
        str(row.currency).strip().upper(),
        row.expiry.isoformat() if row.expiry else "",
        format(Decimal(row.strike), "f") if row.strike is not None else "",
        str(row.option_right or "").strip().upper(),
    )


def _multiplier_evidence_map(
    orders: list[BrokerOrderObservation],
    fills: list[BrokerFillObservation],
) -> tuple[dict[tuple[str, ...], Decimal], Decimal]:
    """Unify independently proved fill/aggregate multiplier evidence."""
    proofs: dict[tuple[str, ...], list[Decimal]] = {}
    residuals: list[Decimal] = []
    for row in fills:
        multiplier, _, residual = _derive_multiplier(
            asset_type=str(row.asset_type),
            amount=_decimal(row.amount),
            quantity=_decimal(row.quantity),
            price=_decimal(row.price),
            evidence_label=f"{row.__tablename__}:{row.observation_key}",
            execution_evidence=True,
        )
        if multiplier is not None:
            proofs.setdefault(_instrument_key(row), []).append(multiplier)
        if residual is not None:
            residuals.append(residual)
    for row in orders:
        if str(row.evidence_level).strip().lower() != "aggregate_only":
            continue
        multiplier, _, residual = _derive_multiplier(
            asset_type=str(row.asset_type),
            amount=_decimal(row.order_amount),
            quantity=_decimal(row.order_quantity),
            price=_decimal(row.order_price),
            evidence_label=f"{row.__tablename__}:{row.observation_key}",
            execution_evidence=True,
        )
        if multiplier is not None:
            proofs.setdefault(_instrument_key(row), []).append(multiplier)
        if residual is not None:
            residuals.append(residual)
    result: dict[tuple[str, ...], Decimal] = {}
    for key, values in proofs.items():
        unique = set(values)
        if len(unique) != 1:
            raise EpisodeRepositoryError(
                "contract multiplier evidence disagrees for " + key[2]
            )
        result[key] = values[0]
    return result, max(residuals, default=Decimal("0"))


def _instrument(
    row: BrokerOrderObservation | BrokerFillObservation,
    *,
    multiplier_by_instrument: Mapping[tuple[str, ...], Decimal],
    execution_evidence: bool,
) -> InstrumentIdentity:
    multiplier = multiplier_by_instrument.get(_instrument_key(row))
    basis = "evidence_derived_from_amount" if multiplier is not None else "unknown"
    if (
        str(row.asset_type).strip().lower() == "option"
        and execution_evidence
        and multiplier is None
    ):
        raise EpisodeRepositoryError(
            f"{row.__tablename__}:{row.observation_key} has no proved option "
            "contract multiplier"
        )
    return InstrumentIdentity(
        broker=str(row.broker),
        account_key=str(row.account_key),
        raw_symbol=str(row.raw_symbol),
        asset_type=str(row.asset_type),
        underlying=str(row.underlying),
        currency=str(row.currency),
        expiry=row.expiry,
        strike=_decimal(row.strike),
        option_right=str(row.option_right) if row.option_right else None,
        contract_multiplier=multiplier,
        contract_multiplier_basis=basis,
    )


def _project_order(
    row: BrokerOrderObservation,
    multiplier_by_instrument: Mapping[tuple[str, ...], Decimal],
) -> CanonicalOrderEvidence:
    filled_quantity = _decimal(row.summary_filled_quantity)
    average_price = _decimal(row.summary_average_fill_price)
    execution_evidence = (
        str(row.evidence_level).lower() in {"fill_detail", "aggregate_only"}
        and filled_quantity is not None
        and filled_quantity > 0
    )
    instrument = _instrument(
        row,
        multiplier_by_instrument=multiplier_by_instrument,
        execution_evidence=execution_evidence,
    )
    return CanonicalOrderEvidence(
        observation_id=int(row.id),
        observation_key=str(row.observation_key),
        instrument=instrument,
        side=str(row.side),
        status=str(row.status),
        ordered_at=_utc(row.ordered_at, field_name="ordered_at"),
        source_sequence=int(row.source_row_number or row.id),
        evidence_level=str(row.evidence_level),
        filled_quantity=filled_quantity,
        average_fill_price=average_price,
        amount=None,
        total_fee=_decimal(row.total_fee),
        fee_evidence_status=str(row.fee_evidence_status),
    )


def _project_fill(
    row: BrokerFillObservation,
    multiplier_by_instrument: Mapping[tuple[str, ...], Decimal],
) -> CanonicalFillEvidence:
    quantity = Decimal(row.quantity)
    price = Decimal(row.price)
    instrument = _instrument(
        row,
        multiplier_by_instrument=multiplier_by_instrument,
        execution_evidence=True,
    )
    return CanonicalFillEvidence(
        observation_id=int(row.id),
        observation_key=str(row.observation_key),
        instrument=instrument,
        side=str(row.side),
        filled_at=_utc(row.filled_at, field_name="filled_at"),
        source_sequence=int(row.source_row_number or row.id),
        quantity=quantity,
        price=price,
        amount=_decimal(row.amount),
        total_fee=_decimal(row.total_fee),
        broker_order_observation_id=(
            int(row.broker_order_observation_id)
            if row.broker_order_observation_id is not None
            else None
        ),
    )


def _latest_accepted_batch(session: Any, account_key: str) -> Optional[ImportBatch]:
    return session.execute(
        select(ImportBatch)
        .where(
            ImportBatch.account_key == account_key,
            ImportBatch.status == "accepted",
            # OpenAPI observations are appended as an independent source.
            # They cannot become the Episode source merely by being newer;
            # only a versioned canonical evidence set may combine sources.
            ImportBatch.source_kind == "csv",
        )
        .order_by(ImportBatch.recorded_at.desc(), ImportBatch.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_reconciliation(
    session: Any,
    batch: ImportBatch,
) -> tuple[
    str,
    str,
    Optional[datetime],
    Optional[datetime],
    int,
    int,
]:
    attestation = session.execute(
        select(ReconciliationAttestation)
        .where(ReconciliationAttestation.import_batch_id == batch.id)
        .order_by(
            ReconciliationAttestation.recorded_at.desc(),
            ReconciliationAttestation.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    status = (
        str(attestation.status)
        if attestation is not None
        else str(batch.reconciliation_status)
    )
    payload = _parse_json(
        attestation.reconciliation_json
        if attestation is not None
        else batch.reconciliation_json
    )
    if status == "not_run":
        return (
            status,
            "not_run",
            None,
            None,
            0,
            int(batch.order_observation_count),
        )
    orders = payload.get("orders")
    reconciled = orders.get("statement") if isinstance(orders, dict) else None
    window = payload.get("window")
    try:
        window_start = _utc(
            datetime.fromisoformat(str(window["start"])),
            field_name="reconciliation.window.start",
        )
        window_end = _utc(
            datetime.fromisoformat(str(window["end"])),
            field_name="reconciliation.window.end",
        )
    except (KeyError, TypeError, ValueError):
        window_start = None
        window_end = None
    try:
        reconciled_count = int(reconciled)
        scope = (
            "full_batch"
            if reconciled_count == int(batch.order_observation_count)
            else "partial_window"
        )
    except (TypeError, ValueError):
        reconciled_count = 0
        scope = "unknown"
    return (
        status,
        scope,
        window_start,
        window_end,
        reconciled_count,
        int(batch.order_observation_count),
    )


def _headline_metrics(
    episodes: tuple[PositionEpisodeRecord, ...],
    *,
    group_fee_affected_episode_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Compute an intentionally strict, non-overclaiming headline subset."""
    qualified: list[PositionEpisodeRecord] = []
    counts = {
        "boundary_unverified": 0,
        "open": 0,
        "left_censored": 0,
        "incomplete": 0,
        "pnl_unavailable": 0,
        "group_fee_unallocated": 0,
    }
    excluded = 0
    for episode in episodes:
        reasons: list[str] = []
        if not episode.left_boundary_verified:
            reasons.append("boundary_unverified")
        if episode.lifecycle_status != "closed":
            reasons.append("open")
        if episode.is_left_censored:
            reasons.append("left_censored")
        if episode.completeness_status not in {"exact", "complete"}:
            reasons.append("incomplete")
        if episode.realized_pnl_net is None:
            reasons.append("pnl_unavailable")
        if episode.episode_key in group_fee_affected_episode_keys:
            reasons.append("group_fee_unallocated")
        if reasons:
            excluded += 1
            for reason in set(reasons):
                counts[reason] += 1
        else:
            qualified.append(episode)

    conditional_closed = [
        episode
        for episode in episodes
        if episode.lifecycle_status == "closed"
        and episode.realized_pnl_gross is not None
        and episode.total_fee is not None
        and episode.realized_pnl_net is not None
        and episode.episode_key not in group_fee_affected_episode_keys
    ]

    def _total(
        records: list[PositionEpisodeRecord],
        field_name: str,
    ) -> Optional[Decimal]:
        if not records:
            return None
        values = [getattr(episode, field_name) for episode in records]
        if any(value is None for value in values):
            return None
        total = sum(
            (value for value in values if value is not None),
            Decimal("0"),
        )
        return total.quantize(_STORAGE_QUANTUM)

    return {
        "open_episode_count": sum(
            episode.lifecycle_status == "open" for episode in episodes
        ),
        "closed_episode_count": sum(
            episode.lifecycle_status == "closed" for episode in episodes
        ),
        "boundary_unverified_episode_count": sum(
            not episode.left_boundary_verified for episode in episodes
        ),
        "left_censored_episode_count": sum(
            episode.is_left_censored for episode in episodes
        ),
        "incomplete_episode_count": sum(
            episode.completeness_status not in {"exact", "complete"}
            for episode in episodes
        ),
        "aggregate_only_episode_count": sum(
            any(item.evidence_kind == "order" for item in episode.evidence)
            for episode in episodes
        ),
        "conditional_closed_episode_count": len(conditional_closed),
        "conditional_realized_pnl_gross": _total(
            conditional_closed,
            "realized_pnl_gross",
        ),
        "conditional_total_fee": _total(conditional_closed, "total_fee"),
        "conditional_realized_pnl_net": _total(
            conditional_closed,
            "realized_pnl_net",
        ),
        "headline_episode_count": len(qualified),
        "headline_realized_pnl_gross": _total(
            qualified,
            "realized_pnl_gross",
        ),
        "headline_total_fee": _total(qualified, "total_fee"),
        "headline_realized_pnl_net": _total(qualified, "realized_pnl_net"),
        "headline_excluded_episode_count": excluded,
        "headline_exclusion_counts": counts,
    }


def _evidence_payload(
    orders: list[BrokerOrderObservation],
    fills: list[BrokerFillObservation],
) -> dict[str, Any]:
    order_key_by_id = {int(row.id): str(row.observation_key) for row in orders}
    return {
        "orders": [
            {
                "observation_key": str(row.observation_key),
                "source_record_sha256": str(row.source_record_sha256),
                "source_row_number": row.source_row_number,
                "source_order_id": str(row.source_order_id),
                "evidence_level": str(row.evidence_level),
            }
            for row in sorted(orders, key=lambda item: str(item.observation_key))
        ],
        "fills": [
            {
                "observation_key": str(row.observation_key),
                "source_record_sha256": str(row.source_record_sha256),
                "source_row_number": row.source_row_number,
                "source_deal_id": str(row.source_deal_id),
                "linked_order_key": order_key_by_id.get(
                    int(row.broker_order_observation_id)
                )
                if row.broker_order_observation_id is not None
                else None,
            }
            for row in sorted(fills, key=lambda item: str(item.observation_key))
        ],
    }


def _strict_json_object(value: str, *, field_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EpisodeRepositoryError(f"{field_name} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise EpisodeRepositoryError(f"{field_name} must be a JSON object")
    return parsed


def _canonical_identity(value: Any, *, field_name: str) -> tuple[str, str, str]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise EpisodeRepositoryError(f"{field_name} must contain three values")
    identity = tuple(str(item).strip() for item in value)
    if any(not item for item in identity):
        raise EpisodeRepositoryError(f"{field_name} cannot contain empty values")
    return identity  # type: ignore[return-value]


def _payload_datetime(value: Any, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise EpisodeRepositoryError(f"{field_name} is not an ISO timestamp") from exc
    return _utc(parsed, field_name=field_name)


def _payload_instrument(value: Any) -> InstrumentIdentity:
    if not isinstance(value, dict):
        raise EpisodeRepositoryError("canonical instrument must be an object")
    try:
        expiry = (
            date.fromisoformat(str(value["expiry"]))
            if value.get("expiry")
            else None
        )
        instrument = InstrumentIdentity(
            broker=str(value["broker"]),
            account_key=str(value["account_key"]),
            raw_symbol=str(value["raw_symbol"]),
            asset_type=str(value["asset_type"]),
            underlying=str(value["underlying"]),
            currency=str(value["currency"]),
            expiry=expiry,
            strike=_decimal(value.get("strike")),
            option_right=(
                str(value["option_right"])
                if value.get("option_right")
                else None
            ),
            contract_multiplier=_decimal(value.get("contract_multiplier")),
            contract_multiplier_basis=str(
                value.get("contract_multiplier_basis") or "unknown"
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EpisodeRepositoryError(
            "canonical instrument payload is incomplete"
        ) from exc
    return instrument


def _canonical_multiplier_key(
    instrument: InstrumentIdentity,
) -> tuple[str, ...]:
    """Economic instrument key shared by CSV and namespaced OpenAPI symbols."""
    return (
        instrument.broker.strip().lower(),
        instrument.account_key.strip(),
        instrument.asset_type.strip().lower(),
        instrument.underlying.strip().upper(),
        instrument.currency.strip().upper(),
        instrument.expiry.isoformat() if instrument.expiry else "",
        (
            format(instrument.strike.normalize(), "f")
            if instrument.strike is not None
            else ""
        ),
        (instrument.option_right or "").strip().upper(),
    )


def _selected_multiplier_key(
    row: BrokerOrderObservation | BrokerFillObservation,
) -> tuple[str, ...]:
    return (
        str(row.broker).strip().lower(),
        str(row.account_key).strip(),
        str(row.asset_type).strip().lower(),
        str(row.underlying).strip().upper(),
        str(row.currency).strip().upper(),
        row.expiry.isoformat() if row.expiry else "",
        (
            format(Decimal(row.strike).normalize(), "f")
            if row.strike is not None
            else ""
        ),
        str(row.option_right or "").strip().upper(),
    )


def _canonical_multiplier_evidence(
    order_members: list[tuple[CanonicalEvidenceMemberRecord, dict[str, Any], Any, ImportBatch]],
    fill_members: list[tuple[CanonicalEvidenceMemberRecord, dict[str, Any], Any, ImportBatch]],
) -> tuple[dict[tuple[str, ...], Decimal], Decimal]:
    """Resolve multipliers only from frozen payloads and their selected rows."""
    proofs: dict[tuple[str, ...], set[Decimal]] = {}
    residuals: list[Decimal] = []

    def add_proof(
        key: tuple[str, ...],
        value: Optional[Decimal],
        *,
        label: str,
    ) -> None:
        if value is None:
            return
        normalized = Decimal(value)
        expected = {
            "equity": Decimal("1"),
            "option": Decimal("100"),
        }.get(key[2])
        if expected is None or normalized != expected:
            raise EpisodeRepositoryError(
                f"{label} has unsupported contract multiplier {normalized}"
            )
        proofs.setdefault(key, set()).add(normalized)

    for kind, members in (("order", order_members), ("fill", fill_members)):
        for member, payload, selected, _ in members:
            instrument = _payload_instrument(payload.get("instrument"))
            key = _canonical_multiplier_key(instrument)
            if key != _selected_multiplier_key(selected):
                raise EpisodeRepositoryError(
                    "canonical instrument disagrees with its selected source"
                )
            label = f"canonical {kind} member {member.member_key}"
            add_proof(key, instrument.contract_multiplier, label=label)
            add_proof(
                key,
                _decimal(selected.contract_multiplier),
                label=f"{label} selected source",
            )

            if kind == "fill":
                multiplier, _, residual = _derive_multiplier(
                    asset_type=str(selected.asset_type),
                    amount=_decimal(selected.amount),
                    quantity=_decimal(selected.quantity),
                    price=_decimal(selected.price),
                    evidence_label=label,
                    execution_evidence=False,
                )
            elif str(payload.get("evidence_level") or "").lower() == "aggregate_only":
                # The broker's order_amount is submitted notional, not an
                # execution cash flow.  It is still valid multiplier evidence
                # when it exactly equals order quantity × limit price × M.
                multiplier, _, residual = _derive_multiplier(
                    asset_type=str(selected.asset_type),
                    amount=_decimal(selected.order_amount),
                    quantity=_decimal(selected.order_quantity),
                    price=_decimal(selected.order_price),
                    evidence_label=label,
                    execution_evidence=False,
                )
            else:
                multiplier = None
                residual = None
            add_proof(key, multiplier, label=label)
            if residual is not None:
                residuals.append(residual)

    conflicts = [key for key, values in proofs.items() if len(values) != 1]
    if conflicts:
        raise EpisodeRepositoryError(
            "canonical contract multiplier evidence disagrees"
        )
    return (
        {key: next(iter(values)) for key, values in proofs.items()},
        max(residuals, default=Decimal("0")),
    )


def _resolved_payload_instrument(
    value: Any,
    multiplier_by_instrument: Mapping[tuple[str, ...], Decimal],
    *,
    require_proved_multiplier: bool = True,
) -> InstrumentIdentity:
    instrument = _payload_instrument(value)
    multiplier = multiplier_by_instrument.get(
        _canonical_multiplier_key(instrument)
    )
    if instrument.contract_multiplier is not None:
        if multiplier is not None and multiplier != instrument.contract_multiplier:
            raise EpisodeRepositoryError(
                "canonical contract multiplier evidence disagrees"
            )
        return instrument
    if multiplier is None:
        if (
            require_proved_multiplier
            and instrument.asset_type.strip().lower() == "option"
        ):
            raise EpisodeRepositoryError(
                f"canonical option {instrument.raw_symbol} has no proved multiplier"
            )
        return instrument
    return InstrumentIdentity(
        broker=instrument.broker,
        account_key=instrument.account_key,
        raw_symbol=instrument.raw_symbol,
        asset_type=instrument.asset_type,
        underlying=instrument.underlying,
        currency=instrument.currency,
        expiry=instrument.expiry,
        strike=instrument.strike,
        option_right=instrument.option_right,
        contract_multiplier=multiplier,
        contract_multiplier_basis="evidence_derived_from_amount",
    )


def _canonical_source_batch_ids(row: CanonicalEvidenceSetRecord) -> tuple[int, ...]:
    try:
        parsed = json.loads(str(row.source_batch_ids_json))
        values = tuple(int(item) for item in parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EpisodeRepositoryError(
            "canonical set has invalid source batch IDs"
        ) from exc
    if not values or any(item <= 0 for item in values):
        raise EpisodeRepositoryError("canonical set has no valid source batches")
    if tuple(sorted(set(values))) != values:
        raise EpisodeRepositoryError(
            "canonical source batch IDs must be sorted and unique"
        )
    return values


def _canonical_set_row(
    session: Any,
    account_key: str,
    canonical_set_id: Optional[int],
) -> Optional[CanonicalEvidenceSetRecord]:
    conditions = (
        CanonicalEvidenceSetRecord.broker == "moomoo",
        CanonicalEvidenceSetRecord.account_key == account_key,
    )
    if canonical_set_id is not None:
        return session.execute(
            select(CanonicalEvidenceSetRecord).where(
                *conditions,
                CanonicalEvidenceSetRecord.id == canonical_set_id,
            )
        ).scalar_one_or_none()
    return session.execute(
        select(CanonicalEvidenceSetRecord)
        .where(*conditions, CanonicalEvidenceSetRecord.analysis_ready.is_(True))
        .order_by(
            CanonicalEvidenceSetRecord.recorded_at.desc(),
            CanonicalEvidenceSetRecord.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def _canonical_member_payloads(
    session: Any,
    canonical_set: CanonicalEvidenceSetRecord,
    batches: Mapping[int, ImportBatch],
) -> tuple[
    list[tuple[CanonicalEvidenceMemberRecord, dict[str, Any], Any, ImportBatch]],
    list[tuple[CanonicalEvidenceMemberRecord, dict[str, Any], Any, ImportBatch]],
    list[
        tuple[
            CanonicalExecutionGroupMemberRecord,
            dict[str, Any],
            BrokerExecutionGroupObservation,
            ImportBatch,
        ]
    ],
]:
    root = _strict_json_object(
        str(canonical_set.canonical_payload_json),
        field_name="canonical_payload_json",
    )
    if _sha256_json(_canonical_root_hash_payload(root)) != str(
        canonical_set.canonical_set_sha256
    ):
        raise EpisodeRepositoryError(
            "canonical root payload hash verification failed"
        )
    canonical_hash = str(canonical_set.canonical_set_sha256)
    source_cutoff_at = _utc(
        canonical_set.source_cutoff_at,
        field_name="canonical_set.source_cutoff_at",
    )
    expected_set_key = _sha256_json(
        {
            "account_key": str(canonical_set.account_key),
            "canonical_set_sha256": canonical_hash,
            "reader_name": str(canonical_set.reader_name),
            "reader_version": str(canonical_set.reader_version),
            "source_cutoff_at": source_cutoff_at,
        }
    )
    if str(canonical_set.set_key) != expected_set_key:
        raise EpisodeRepositoryError("canonical set key verification failed")
    if (
        bool(root.get("analysis_ready")) is not True
        or bool(canonical_set.analysis_ready) is not True
    ):
        raise EpisodeRepositoryError("canonical payload is not analysis-ready")
    root_provenance = root.get("provenance")
    if not isinstance(root_provenance, dict):
        raise EpisodeRepositoryError("canonical root provenance is invalid")
    stored_provenance = _strict_json_object(
        str(canonical_set.provenance_json),
        field_name="canonical provenance_json",
    )
    if root_provenance != stored_provenance:
        raise EpisodeRepositoryError(
            "canonical root provenance does not match its stored metadata"
        )
    source_batches = tuple(batches[batch_id] for batch_id in sorted(batches))
    expected_batch_keys = sorted(str(batch.batch_key) for batch in source_batches)
    expected_source_kinds = sorted(
        {str(batch.source_kind) for batch in source_batches}
    )
    raw_orders = root.get("orders")
    raw_fills = root.get("fills")
    legacy_group_free_contract = (
        str(canonical_set.reader_name) == "stable_broker_identity_reader"
        and str(canonical_set.reader_version) == "1.0.0"
        and "execution_groups" not in root
        and "canonical_execution_group_count" not in root_provenance
        and "canonical_execution_group_leg_count" not in root_provenance
    )
    if "execution_groups" not in root and not legacy_group_free_contract:
        raise EpisodeRepositoryError(
            "canonical root execution-group collection is missing"
        )
    raw_groups = root.get("execution_groups", [])
    raw_issues = root.get("issues")
    if (
        not isinstance(raw_orders, list)
        or not isinstance(raw_fills, list)
        or not isinstance(raw_groups, list)
        or not isinstance(raw_issues, list)
    ):
        raise EpisodeRepositoryError("canonical root collections are invalid")
    blocking_issue_count = sum(
        isinstance(issue, dict) and issue.get("severity") == "blocking"
        for issue in raw_issues
    )
    expected_leg_count = sum(
        len(group.get("legs") or [])
        for group in raw_groups
        if isinstance(group, dict)
    )
    metadata_comparisons: dict[str, tuple[Any, Any]] = {
        "reader name": (
            root_provenance.get("reader_name"),
            str(canonical_set.reader_name),
        ),
        "reader version": (
            root_provenance.get("reader_version"),
            str(canonical_set.reader_version),
        ),
        "source batch keys": (
            root_provenance.get("batch_keys"),
            expected_batch_keys,
        ),
        "source kinds": (
            root_provenance.get("source_kinds"),
            expected_source_kinds,
        ),
        "canonical order count": (
            root_provenance.get("canonical_order_count"),
            int(canonical_set.canonical_order_count),
        ),
        "canonical fill count": (
            root_provenance.get("canonical_fill_count"),
            int(canonical_set.canonical_fill_count),
        ),
        "blocking issue count": (
            root_provenance.get("blocking_issue_count"),
            int(canonical_set.blocking_issue_count),
        ),
        "root order count": (
            len(raw_orders),
            int(canonical_set.canonical_order_count),
        ),
        "root fill count": (
            len(raw_fills),
            int(canonical_set.canonical_fill_count),
        ),
        "root blocking issue count": (
            blocking_issue_count,
            int(canonical_set.blocking_issue_count),
        ),
    }
    if not legacy_group_free_contract:
        metadata_comparisons.update(
            {
                "canonical execution-group count": (
                    root_provenance.get(
                        "canonical_execution_group_count"
                    ),
                    len(raw_groups),
                ),
                "canonical execution-group leg count": (
                    root_provenance.get(
                        "canonical_execution_group_leg_count"
                    ),
                    expected_leg_count,
                ),
            }
        )
    mismatch = next(
        (
            name
            for name, (actual, expected) in metadata_comparisons.items()
            if actual != expected
        ),
        None,
    )
    if mismatch is not None:
        raise EpisodeRepositoryError(
            f"canonical root {mismatch} verification failed"
        )
    root_by_kind: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {}
    for kind, key in (("order", "orders"), ("fill", "fills")):
        values = root.get(key)
        if not isinstance(values, list):
            raise EpisodeRepositoryError(f"canonical payload {key} must be a list")
        mapped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for index, payload in enumerate(values):
            if not isinstance(payload, dict):
                raise EpisodeRepositoryError(
                    f"canonical payload {key}[{index}] must be an object"
                )
            identity = _canonical_identity(
                payload.get("identity"),
                field_name=f"canonical {kind} identity",
            )
            if identity in mapped:
                raise EpisodeRepositoryError(
                    f"canonical payload contains duplicate {kind} identity"
                )
            mapped[identity] = payload
        root_by_kind[kind] = mapped

    members = session.execute(
        select(CanonicalEvidenceMemberRecord)
        .where(
            CanonicalEvidenceMemberRecord.canonical_set_id == canonical_set.id
        )
        .order_by(
            CanonicalEvidenceMemberRecord.entity_kind,
            CanonicalEvidenceMemberRecord.id,
        )
    ).scalars().all()
    expected_count = int(canonical_set.canonical_order_count) + int(
        canonical_set.canonical_fill_count
    )
    if len(members) != expected_count:
        raise EpisodeRepositoryError("canonical member count drifted")

    projected: dict[
        str,
        list[tuple[CanonicalEvidenceMemberRecord, dict[str, Any], Any, ImportBatch]],
    ] = {"order": [], "fill": []}
    observed_identities: dict[str, set[tuple[str, str, str]]] = {
        "order": set(),
        "fill": set(),
    }
    for member in members:
        kind = str(member.entity_kind)
        if kind not in projected:
            raise EpisodeRepositoryError("canonical member kind is unsupported")
        payload = _strict_json_object(
            str(member.canonical_payload_json),
            field_name="canonical member payload",
        )
        identity = _canonical_identity(
            payload.get("identity"),
            field_name=f"canonical {kind} member identity",
        )
        root_payload = root_by_kind[kind].get(identity)
        if root_payload is None or root_payload != payload:
            raise EpisodeRepositoryError(
                "canonical member does not match its frozen root payload"
            )
        if identity in observed_identities[kind]:
            raise EpisodeRepositoryError("canonical member identity is duplicated")
        observed_identities[kind].add(identity)
        expected_member_key = _sha256_json(
            {
                "entity_kind": kind,
                "identity": payload["identity"],
                "canonical_payload": payload,
            }
        )
        if str(member.member_key) != expected_member_key:
            raise EpisodeRepositoryError("canonical member key verification failed")

        selected_id = (
            member.selected_order_observation_id
            if kind == "order"
            else member.selected_fill_observation_id
        )
        model = BrokerOrderObservation if kind == "order" else BrokerFillObservation
        selected = session.get(model, selected_id) if selected_id is not None else None
        if selected is None:
            raise EpisodeRepositoryError("canonical selected observation is missing")
        batch = batches.get(int(selected.import_batch_id))
        selected_ref = payload.get("selected_ref")
        if batch is None or not isinstance(selected_ref, dict):
            raise EpisodeRepositoryError("canonical selected source is invalid")
        if (
            str(selected.account_key) != str(canonical_set.account_key)
            or str(selected.broker) != str(canonical_set.broker)
            or str(selected.observation_key) != str(selected_ref.get("observation_key"))
            or str(selected.source_record_sha256)
            != str(selected_ref.get("source_record_sha256"))
            or str(batch.batch_key) != str(selected_ref.get("batch_key"))
            or str(batch.source_kind) != str(selected_ref.get("source_kind"))
        ):
            raise EpisodeRepositoryError(
                "canonical selected observation no longer binds to its source"
            )
        projected[kind].append((member, payload, selected, batch))

    if observed_identities["order"] != set(root_by_kind["order"]):
        raise EpisodeRepositoryError("canonical order members are incomplete")
    if observed_identities["fill"] != set(root_by_kind["fill"]):
        raise EpisodeRepositoryError("canonical fill members are incomplete")

    root_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, payload in enumerate(raw_groups):
        if not isinstance(payload, dict):
            raise EpisodeRepositoryError(
                f"canonical payload execution_groups[{index}] must be an object"
            )
        identity = _canonical_identity(
            payload.get("identity"),
            field_name="canonical execution-group identity",
        )
        if identity in root_groups:
            raise EpisodeRepositoryError(
                "canonical payload contains duplicate execution-group identity"
            )
        root_groups[identity] = payload

    group_members = session.execute(
        select(CanonicalExecutionGroupMemberRecord)
        .where(
            CanonicalExecutionGroupMemberRecord.canonical_set_id
            == canonical_set.id
        )
        .order_by(CanonicalExecutionGroupMemberRecord.id)
    ).scalars().all()
    if len(group_members) != len(root_groups):
        raise EpisodeRepositoryError("canonical execution-group count drifted")
    projected_groups: list[
        tuple[
            CanonicalExecutionGroupMemberRecord,
            dict[str, Any],
            BrokerExecutionGroupObservation,
            ImportBatch,
        ]
    ] = []
    observed_groups: set[tuple[str, str, str]] = set()
    group_member_by_identity: dict[
        tuple[str, str, str], CanonicalExecutionGroupMemberRecord
    ] = {}
    for member in group_members:
        payload = _strict_json_object(
            str(member.canonical_payload_json),
            field_name="canonical execution-group member payload",
        )
        identity = _canonical_identity(
            payload.get("identity"),
            field_name="canonical execution-group member identity",
        )
        if root_groups.get(identity) != payload or identity in observed_groups:
            raise EpisodeRepositoryError(
                "canonical execution-group member does not match its frozen root"
            )
        expected_member_key = _sha256_json(
            {
                "entity_kind": "execution_group",
                "identity": payload["identity"],
                "canonical_payload": payload,
            }
        )
        if (
            str(member.member_key) != expected_member_key
            or str(member.identity_key)
            != "/".join(str(value) for value in identity)
            or str(member.canonical_execution_group_id) != identity[2]
            or str(member.fee_scope_policy)
            != "execution_group_only_no_allocation"
            or _decimal(member.group_total_fee)
            != _decimal(payload.get("total_fee"))
        ):
            raise EpisodeRepositoryError(
                "canonical execution-group member columns drifted"
            )
        selected = session.get(
            BrokerExecutionGroupObservation,
            member.selected_execution_group_observation_id,
        )
        selected_ref = payload.get("selected_ref")
        if selected is None or not isinstance(selected_ref, dict):
            raise EpisodeRepositoryError(
                "canonical execution-group selected source is missing"
            )
        batch = batches.get(int(selected.import_batch_id))
        if (
            batch is None
            or str(selected.account_key) != str(canonical_set.account_key)
            or str(selected.broker) != str(canonical_set.broker)
            or str(selected.observation_key)
            != str(selected_ref.get("observation_key"))
            or str(selected.source_record_sha256)
            != str(selected_ref.get("source_record_sha256"))
            or str(batch.batch_key) != str(selected_ref.get("batch_key"))
            or str(batch.source_kind) != str(selected_ref.get("source_kind"))
        ):
            raise EpisodeRepositoryError(
                "canonical execution-group source binding drifted"
            )
        observed_groups.add(identity)
        group_member_by_identity[identity] = member
        projected_groups.append((member, payload, selected, batch))
    if observed_groups != set(root_groups):
        raise EpisodeRepositoryError(
            "canonical execution-group members are incomplete"
        )

    group_leg_rows = session.execute(
        select(CanonicalExecutionGroupLegRecord)
        .where(
            CanonicalExecutionGroupLegRecord.canonical_set_id
            == canonical_set.id
        )
        .order_by(CanonicalExecutionGroupLegRecord.id)
    ).scalars().all()
    expected_leg_count = sum(
        len(payload.get("legs") or []) for payload in root_groups.values()
    )
    if len(group_leg_rows) != expected_leg_count:
        raise EpisodeRepositoryError("canonical execution-group leg count drifted")
    group_leg_by_identity: dict[
        tuple[tuple[str, str, str], str], CanonicalExecutionGroupLegRecord
    ] = {}
    expected_fill_identities_by_leg: dict[
        tuple[tuple[str, str, str], str], set[tuple[str, str, str]]
    ] = {}
    for group_identity, group_payload in root_groups.items():
        for raw_leg_payload in group_payload.get("legs") or []:
            if not isinstance(raw_leg_payload, dict):
                raise EpisodeRepositoryError(
                    "canonical execution-group leg payload is invalid"
                )
            leg_key = str(raw_leg_payload.get("leg_key") or "").strip()
            raw_fill_identities = raw_leg_payload.get("fill_identities")
            if not leg_key or not isinstance(raw_fill_identities, list):
                raise EpisodeRepositoryError(
                    "canonical execution-group leg membership is invalid"
                )
            expected_fill_identities_by_leg[(group_identity, leg_key)] = {
                _canonical_identity(
                    value,
                    field_name="canonical execution-group leg fill identity",
                )
                for value in raw_fill_identities
            }

    for leg_row in group_leg_rows:
        group_identity = next(
            (
                identity
                for identity, member in group_member_by_identity.items()
                if int(member.id) == int(leg_row.canonical_execution_group_member_id)
            ),
            None,
        )
        if group_identity is None:
            raise EpisodeRepositoryError(
                "canonical execution-group leg has no frozen parent"
            )
        leg_payload = _strict_json_object(
            str(leg_row.canonical_payload_json),
            field_name="canonical execution-group leg payload",
        )
        leg_key = str(leg_payload.get("leg_key") or "").strip()
        root_leg_payload = next(
            (
                item
                for item in root_groups[group_identity].get("legs") or []
                if isinstance(item, dict)
                and str(item.get("leg_key") or "").strip() == leg_key
            ),
            None,
        )
        if (
            root_leg_payload != leg_payload
            or not leg_key
            or str(leg_row.leg_key) != leg_key
            or (group_identity, leg_key) in group_leg_by_identity
        ):
            raise EpisodeRepositoryError(
                "canonical execution-group leg does not match its frozen root"
            )
        selected_ref = leg_payload.get("selected_ref")
        selected_leg = session.get(
            BrokerExecutionGroupLegObservation,
            leg_row.selected_execution_group_leg_observation_id,
        )
        parent_member = group_member_by_identity[group_identity]
        if (
            selected_leg is None
            or not isinstance(selected_ref, dict)
            or int(selected_leg.execution_group_observation_id)
            != int(parent_member.selected_execution_group_observation_id)
            or str(selected_leg.observation_key)
            != str(selected_ref.get("observation_key"))
            or str(selected_leg.source_record_sha256)
            != str(selected_ref.get("source_record_sha256"))
            or str(selected_leg.leg_key) != leg_key
        ):
            raise EpisodeRepositoryError(
                "canonical execution-group leg source binding drifted"
            )
        group_leg_by_identity[(group_identity, leg_key)] = leg_row

    fill_member_by_id = {
        int(member.id): (member, payload, selected)
        for member, payload, selected, _batch in projected["fill"]
    }
    group_link_rows = session.execute(
        select(CanonicalExecutionGroupFillLinkRecord)
        .where(
            CanonicalExecutionGroupFillLinkRecord.canonical_set_id
            == canonical_set.id
        )
        .order_by(CanonicalExecutionGroupFillLinkRecord.id)
    ).scalars().all()
    expected_group_fill_count = sum(
        payload.get("execution_group_identity") is not None
        for _member, payload, _selected, _batch in projected["fill"]
    )
    if len(group_link_rows) != expected_group_fill_count:
        raise EpisodeRepositoryError(
            "canonical execution-group fill-link count drifted"
        )
    linked_fill_identities_by_leg: dict[
        tuple[tuple[str, str, str], str], set[tuple[str, str, str]]
    ] = {key: set() for key in expected_fill_identities_by_leg}
    for link_row in group_link_rows:
        linked = fill_member_by_id.get(int(link_row.canonical_fill_member_id))
        if linked is None:
            raise EpisodeRepositoryError(
                "canonical execution-group link has no canonical fill"
            )
        _fill_member, fill_payload, selected_fill = linked
        group_identity = _canonical_identity(
            fill_payload.get("execution_group_identity"),
            field_name="canonical fill execution-group identity",
        )
        leg_key = str(fill_payload.get("execution_group_leg_key") or "").strip()
        group_member = group_member_by_identity.get(group_identity)
        group_leg = group_leg_by_identity.get((group_identity, leg_key))
        fill_identity = _canonical_identity(
            fill_payload.get("identity"),
            field_name="canonical execution-group fill identity",
        )
        selected_ref = fill_payload.get("execution_group_fill_link_ref")
        expected_link_payload = {
            "execution_group_identity": list(group_identity),
            "execution_group_leg_key": leg_key,
            "fill_identity": list(fill_identity),
            "selected_ref": selected_ref,
            "fee_scope": "execution_group",
        }
        stored_link_payload = _strict_json_object(
            str(link_row.canonical_payload_json),
            field_name="canonical execution-group fill-link payload",
        )
        selected_link = session.get(
            BrokerExecutionGroupFillLink,
            link_row.selected_execution_group_fill_link_id,
        )
        if (
            group_member is None
            or group_leg is None
            or not isinstance(selected_ref, dict)
            or fill_payload.get("linked_order_identity") is not None
            or fill_payload.get("total_fee") is not None
            or int(link_row.canonical_execution_group_member_id)
            != int(group_member.id)
            or int(link_row.canonical_execution_group_leg_id)
            != int(group_leg.id)
            or str(link_row.fee_scope) != "execution_group"
            or str(link_row.link_key) != _sha256_json(expected_link_payload)
            or stored_link_payload != expected_link_payload
            or str(link_row.canonical_deal_id) != fill_identity[2]
            or selected_link is None
            or int(selected_link.broker_fill_observation_id)
            != int(selected_fill.id)
            or int(selected_link.execution_group_observation_id)
            != int(group_member.selected_execution_group_observation_id)
            or int(selected_link.execution_group_leg_observation_id)
            != int(group_leg.selected_execution_group_leg_observation_id)
            or str(selected_link.link_key)
            != str(selected_ref.get("observation_key"))
        ):
            raise EpisodeRepositoryError(
                "canonical execution-group fill linkage drifted"
            )
        linked_fill_identities_by_leg[(group_identity, leg_key)].add(
            fill_identity
        )
    if linked_fill_identities_by_leg != expected_fill_identities_by_leg:
        raise EpisodeRepositoryError(
            "canonical execution-group leg fill membership drifted"
        )
    return projected["order"], projected["fill"], projected_groups


def _canonical_execution_group_context(
    group_members: list[tuple[Any, dict[str, Any], Any, ImportBatch]],
    fill_members: list[tuple[Any, dict[str, Any], Any, ImportBatch]],
) -> tuple[
    dict[str, Decimal],
    dict[int, tuple[tuple[str, str, str], str]],
]:
    """Validate frozen group/fill bindings and retain fees by currency."""
    groups: dict[tuple[str, str, str], set[str]] = {}
    retained_by_currency: dict[str, Decimal] = {}
    for _member, payload, _selected, _batch in group_members:
        identity = _canonical_identity(
            payload.get("identity"),
            field_name="canonical execution-group identity",
        )
        legs = payload.get("legs")
        if not isinstance(legs, list) or len(legs) < 2:
            raise EpisodeRepositoryError(
                "canonical execution group has invalid declared legs"
            )
        leg_keys: set[str] = set()
        for leg in legs:
            if not isinstance(leg, dict):
                raise EpisodeRepositoryError(
                    "canonical execution-group leg must be an object"
                )
            leg_key = str(leg.get("leg_key") or "").strip()
            if not leg_key or leg_key in leg_keys:
                raise EpisodeRepositoryError(
                    "canonical execution-group leg key is invalid"
                )
            leg_keys.add(leg_key)
        groups[identity] = leg_keys

        total_fee = _decimal(payload.get("total_fee"))
        fee_status = str(payload.get("fee_evidence_status") or "").lower()
        proved_quantity = _decimal(
            payload.get("proved_executed_group_quantity")
        )
        if proved_quantity is not None and proved_quantity > 0:
            if total_fee is None or fee_status != "complete":
                raise EpisodeRepositoryError(
                    "executed canonical group lacks exact group-scoped fee"
                )
        if total_fee is not None:
            if total_fee < 0:
                raise EpisodeRepositoryError(
                    "canonical execution-group fee cannot be negative"
                )
            currency = str(payload.get("currency") or "").strip().upper()
            if not currency:
                raise EpisodeRepositoryError(
                    "canonical execution-group fee currency is missing"
                )
            retained_by_currency[currency] = (
                retained_by_currency.get(currency, Decimal("0")) + total_fee
            ).quantize(_STORAGE_QUANTUM)

    fill_bindings: dict[int, tuple[tuple[str, str, str], str]] = {}
    for _member, payload, selected, _batch in fill_members:
        raw_identity = payload.get("execution_group_identity")
        raw_leg_key = payload.get("execution_group_leg_key")
        raw_link = payload.get("execution_group_fill_link_ref")
        if raw_identity is None and raw_leg_key is None and raw_link is None:
            continue
        if raw_identity is None or raw_leg_key is None or not isinstance(raw_link, dict):
            raise EpisodeRepositoryError(
                "canonical execution-group fill binding is incomplete"
            )
        identity = _canonical_identity(
            raw_identity,
            field_name="canonical fill execution-group identity",
        )
        leg_key = str(raw_leg_key).strip()
        if (
            identity not in groups
            or leg_key not in groups[identity]
            or str(raw_link.get("evidence_kind"))
            != "execution_group_fill_link"
            or payload.get("total_fee") is not None
        ):
            raise EpisodeRepositoryError(
                "canonical fill does not bind to one fee-free execution-group leg"
            )
        selected_id = int(selected.id)
        if selected_id in fill_bindings:
            raise EpisodeRepositoryError(
                "canonical fill is assigned to multiple execution groups"
            )
        fill_bindings[selected_id] = (identity, leg_key)
    return retained_by_currency, fill_bindings


def _episode_fee_accounting(
    episodes: tuple[PositionEpisodeRecord, ...],
    *,
    ordinary_source_total: Decimal,
    ordinary_allocated_total: Decimal,
    ordinary_source_by_currency: Optional[Mapping[str, Decimal]] = None,
    retained_group_by_currency: Mapping[str, Decimal],
) -> tuple[
    Decimal,
    Decimal,
    dict[str, dict[str, Decimal]],
    bool,
]:
    ordinary_by_currency: dict[str, Decimal] = {}
    for episode in episodes:
        currency = episode.instrument.currency.strip().upper()
        for allocation in episode.evidence:
            if allocation.allocated_fee is None:
                continue
            ordinary_by_currency[currency] = (
                ordinary_by_currency.get(currency, Decimal("0"))
                + allocation.allocated_fee
            ).quantize(_STORAGE_QUANTUM)
    ordinary_accounted = sum(
        ordinary_by_currency.values(),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    normalized_source_by_currency = {
        str(currency).strip().upper(): Decimal(value).quantize(
            _STORAGE_QUANTUM
        )
        for currency, value in (
            ordinary_source_by_currency or ordinary_by_currency
        ).items()
    }
    ordinary_source_by_currency_total = sum(
        normalized_source_by_currency.values(),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    if (
        ordinary_source_total.quantize(_STORAGE_QUANTUM)
        != ordinary_allocated_total.quantize(_STORAGE_QUANTUM)
        or ordinary_accounted
        != ordinary_allocated_total.quantize(_STORAGE_QUANTUM)
        or ordinary_source_by_currency_total
        != ordinary_source_total.quantize(_STORAGE_QUANTUM)
        or normalized_source_by_currency != ordinary_by_currency
    ):
        raise EpisodeRepositoryError(
            "ordinary canonical fees are not conserved by currency"
        )

    by_currency: dict[str, dict[str, Decimal]] = {}
    currencies = sorted(
        {*ordinary_by_currency, *retained_group_by_currency}
    )
    for currency in currencies:
        ordinary = ordinary_by_currency.get(currency, Decimal("0")).quantize(
            _STORAGE_QUANTUM
        )
        retained = retained_group_by_currency.get(
            currency,
            Decimal("0"),
        ).quantize(_STORAGE_QUANTUM)
        source_known = ordinary + retained
        accounted = ordinary + retained
        by_currency[currency] = {
            "ordinary_source": ordinary,
            "ordinary_allocated": ordinary,
            "retained_execution_group": retained,
            "source_known": source_known,
            "accounted": accounted,
        }
    source_total = sum(
        (values["source_known"] for values in by_currency.values()),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    accounted_total = sum(
        (values["accounted"] for values in by_currency.values()),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    conserved = source_total == accounted_total and all(
        values["source_known"] == values["accounted"]
        for values in by_currency.values()
    )
    return source_total, accounted_total, by_currency, conserved


def _canonical_ordinary_source_fee_by_currency(
    order_members: list[tuple[Any, dict[str, Any], Any, ImportBatch]],
    fill_members: list[tuple[Any, dict[str, Any], Any, ImportBatch]],
) -> dict[str, Decimal]:
    """Reconstruct the builder's ordinary source-fee choice by currency."""
    order_fee_by_identity: dict[tuple[str, str, str], Optional[Decimal]] = {}
    source_by_currency: dict[str, Decimal] = {}

    def add(payload: Mapping[str, Any], fee: Decimal) -> None:
        instrument = payload.get("instrument")
        currency = (
            str(instrument.get("currency") or "").strip().upper()
            if isinstance(instrument, dict)
            else ""
        )
        if not currency:
            raise EpisodeRepositoryError(
                "canonical fee source has no instrument currency"
            )
        source_by_currency[currency] = (
            source_by_currency.get(currency, Decimal("0")) + fee
        ).quantize(_STORAGE_QUANTUM)

    for _member, payload, _selected, _batch in order_members:
        identity = _canonical_identity(
            payload.get("identity"),
            field_name="canonical fee-source order identity",
        )
        fee = _decimal(payload.get("total_fee"))
        order_fee_by_identity[identity] = fee
        if fee is not None:
            add(payload, fee)

    for _member, payload, _selected, _batch in fill_members:
        fee = _decimal(payload.get("total_fee"))
        if fee is None:
            continue
        raw_linked_identity = payload.get("linked_order_identity")
        linked_identity = (
            None
            if raw_linked_identity is None
            else _canonical_identity(
                raw_linked_identity,
                field_name="canonical fee-source linked order identity",
            )
        )
        if (
            linked_identity is None
            or order_fee_by_identity.get(linked_identity) is None
        ):
            add(payload, fee)
    return source_by_currency


def load_verified_canonical_episode_evidence_projection(
    session: Any,
    account_key: str,
    canonical_set_id: int,
    *,
    max_member_count: Optional[int] = None,
) -> VerifiedCanonicalEpisodeEvidenceProjection:
    """Replay-verify one frozen canonical set into boundary and builder facts.

    The boundary fence and the Episode builder must consume the same frozen
    canonical payload.  This helper deliberately reuses the builder's strict
    member/source/group/multiplier verification and returns both projections
    from that one replay.  It never initializes schemas or writes evidence.
    """
    account_key = str(account_key or "").strip()
    if not account_key:
        raise EpisodeRepositoryError("account_key cannot be empty")
    if canonical_set_id <= 0:
        raise EpisodeRepositoryError("canonical_set_id must be positive")
    canonical_set = _canonical_set_row(
        session,
        account_key,
        canonical_set_id,
    )
    if canonical_set is None:
        raise EpisodeRepositoryError("canonical evidence set does not exist")
    if (
        not bool(canonical_set.analysis_ready)
        or int(canonical_set.blocking_issue_count) != 0
    ):
        raise EpisodeRepositoryError("canonical set is not analysis-ready")
    declared_member_count = int(canonical_set.canonical_order_count) + int(
        canonical_set.canonical_fill_count
    )
    if max_member_count is not None and (
        max_member_count <= 0 or declared_member_count > max_member_count
    ):
        raise EpisodeRepositoryError(
            "canonical evidence member count exceeds the preview limit"
        )

    source_batch_ids = _canonical_source_batch_ids(canonical_set)
    batches = {
        batch_id: session.get(ImportBatch, batch_id)
        for batch_id in source_batch_ids
    }
    if any(batch is None for batch in batches.values()):
        raise EpisodeRepositoryError("canonical source batch is missing")
    typed_batches = {
        batch_id: batch
        for batch_id, batch in batches.items()
        if batch is not None
    }
    if any(
        str(batch.account_key) != account_key
        or str(batch.broker) != "moomoo"
        or str(batch.status) != "accepted"
        for batch in typed_batches.values()
    ):
        raise EpisodeRepositoryError(
            "canonical set references a cross-account or unaccepted batch"
        )

    order_members, fill_members, group_members = _canonical_member_payloads(
        session,
        canonical_set,
        typed_batches,
    )
    if len(order_members) != int(canonical_set.canonical_order_count):
        raise EpisodeRepositoryError("canonical order count drifted")
    if len(fill_members) != int(canonical_set.canonical_fill_count):
        raise EpisodeRepositoryError("canonical fill count drifted")
    group_leg_count = sum(
        len(payload.get("legs") or [])
        for _member, payload, _selected, _batch in group_members
    )
    group_fill_link_count = sum(
        payload.get("execution_group_identity") is not None
        for _member, payload, _selected, _batch in fill_members
    )
    replay_member_count = (
        declared_member_count
        + len(group_members)
        + group_leg_count
        + group_fill_link_count
    )
    if (
        max_member_count is not None
        and replay_member_count > max_member_count
    ):
        raise EpisodeRepositoryError(
            "canonical evidence member count exceeds the preview limit"
        )
    _retained_group_fees, group_fill_bindings = (
        _canonical_execution_group_context(group_members, fill_members)
    )
    (
        multiplier_by_instrument,
        max_multiplier_proof_residual,
    ) = _canonical_multiplier_evidence(order_members, fill_members)

    order_id_by_identity: dict[tuple[str, str, str], int] = {}
    boundary_orders: list[CanonicalBoundaryOrder] = []
    builder_orders: list[CanonicalOrderEvidence] = []
    for member, payload, selected, batch in order_members:
        identity = _canonical_identity(
            payload.get("identity"),
            field_name="canonical order identity",
        )
        selected_id = int(selected.id)
        order_id_by_identity[identity] = selected_id
        filled_quantity = _decimal(payload.get("filled_quantity"))
        instrument = _resolved_payload_instrument(
            payload.get("instrument"),
            multiplier_by_instrument,
            require_proved_multiplier=(
                filled_quantity is not None and filled_quantity > 0
            ),
        )
        ordered_at = _payload_datetime(
            payload.get("ordered_at"),
            field_name="canonical order ordered_at",
        )
        evidence_level = str(payload.get("evidence_level") or "")
        economic_sha256 = _sha256_json(
            {
                "instrument": instrument.canonical_payload(),
                "side": str(payload.get("side") or ""),
                "status": str(payload.get("status") or ""),
                "ordered_at": ordered_at,
                "evidence_level": evidence_level,
                "filled_quantity": filled_quantity,
                "average_fill_price": _decimal(
                    payload.get("average_fill_price")
                ),
                "total_fee": _decimal(payload.get("total_fee")),
                "fee_evidence_status": str(
                    payload.get("fee_evidence_status") or "unknown"
                ),
            }
        )
        boundary_orders.append(
            CanonicalBoundaryOrder(
                member_id=int(member.id),
                selected_observation_id=selected_id,
                import_batch_id=int(selected.import_batch_id),
                identity=identity,
                instrument=instrument,
                ordered_at=ordered_at,
                evidence_level=evidence_level,
                filled_quantity=filled_quantity,
                economic_sha256=economic_sha256,
            )
        )
        builder_orders.append(
            CanonicalOrderEvidence(
                observation_id=selected_id,
                observation_key=f"canonical_order_{member.member_key}",
                instrument=instrument,
                side=str(payload.get("side") or ""),
                status=str(payload.get("status") or ""),
                ordered_at=ordered_at,
                source_sequence=stable_source_sequence(
                    source_row_number=selected.source_row_number,
                    source_kind=str(batch.source_kind),
                    observation_key=str(selected.observation_key),
                    source_record_sha256=str(selected.source_record_sha256),
                ),
                evidence_level=evidence_level,
                filled_quantity=filled_quantity,
                average_fill_price=_decimal(
                    payload.get("average_fill_price")
                ),
                # Submitted order notional is audit context, not execution
                # cash flow.  Keep the same policy as the canonical builder.
                amount=None,
                total_fee=_decimal(payload.get("total_fee")),
                fee_evidence_status=str(
                    payload.get("fee_evidence_status") or "unknown"
                ),
            )
        )

    boundary_fills: list[CanonicalBoundaryFill] = []
    builder_fills: list[CanonicalFillEvidence] = []
    group_member_ids = {
        _canonical_identity(
            payload.get("identity"),
            field_name="canonical execution-group identity",
        ): int(member.id)
        for member, payload, _selected, _batch in group_members
    }
    for member, payload, selected, batch in fill_members:
        linked_raw = payload.get("linked_order_identity")
        linked_identity = (
            None
            if linked_raw is None
            else _canonical_identity(
                linked_raw,
                field_name="canonical fill linked order identity",
            )
        )
        linked_order_id = None
        if linked_identity is not None:
            linked_order_id = order_id_by_identity.get(linked_identity)
            if linked_order_id is None:
                raise EpisodeRepositoryError(
                    "canonical fill does not resolve to a selected order member"
                )
        group_binding = group_fill_bindings.get(int(selected.id))
        instrument = _resolved_payload_instrument(
            payload.get("instrument"),
            multiplier_by_instrument,
        )
        filled_at = _payload_datetime(
            payload.get("filled_at"),
            field_name="canonical fill filled_at",
        )
        boundary_fills.append(
            CanonicalBoundaryFill(
                member_id=int(member.id),
                selected_observation_id=int(selected.id),
                import_batch_id=int(selected.import_batch_id),
                identity=_canonical_identity(
                    payload.get("identity"),
                    field_name="canonical fill identity",
                ),
                linked_order_identity=linked_identity,
                execution_group_identity=(
                    None if group_binding is None else group_binding[0]
                ),
                execution_group_member_id=(
                    None
                    if group_binding is None
                    else group_member_ids.get(group_binding[0])
                ),
                instrument=instrument,
                filled_at=filled_at,
            )
        )
        builder_fills.append(
            CanonicalFillEvidence(
                observation_id=int(selected.id),
                observation_key=f"canonical_fill_{member.member_key}",
                instrument=instrument,
                side=str(payload.get("side") or ""),
                filled_at=filled_at,
                source_sequence=stable_source_sequence(
                    source_row_number=selected.source_row_number,
                    source_kind=str(batch.source_kind),
                    observation_key=str(selected.observation_key),
                    source_record_sha256=str(selected.source_record_sha256),
                ),
                quantity=Decimal(str(payload.get("quantity"))),
                price=Decimal(str(payload.get("price"))),
                amount=_decimal(payload.get("amount")),
                total_fee=_decimal(payload.get("total_fee")),
                broker_order_observation_id=linked_order_id,
            )
        )

    fill_ids_by_group: dict[tuple[str, str, str], list[int]] = {}
    for fill_id, (identity, _leg_key) in group_fill_bindings.items():
        fill_ids_by_group.setdefault(identity, []).append(fill_id)
    execution_groups = tuple(
        VerifiedCanonicalExecutionGroup(
            identity=_canonical_identity(
                payload.get("identity"),
                field_name="canonical execution-group identity",
            ),
            member_id=int(member.id),
            import_batch_id=int(selected.import_batch_id),
            currency=str(payload.get("currency") or "").strip().upper(),
            total_fee=_decimal(payload.get("total_fee")),
            selected_fill_ids=tuple(
                sorted(
                    fill_ids_by_group.get(
                        _canonical_identity(
                            payload.get("identity"),
                            field_name="canonical execution-group identity",
                        ),
                        (),
                    )
                )
            ),
        )
        for member, payload, selected, _batch in group_members
    )
    boundary = VerifiedCanonicalEvidenceProjection(
        canonical_set_id=int(canonical_set.id),
        canonical_set_sha256=str(canonical_set.canonical_set_sha256),
        source_cutoff_at=_utc(
            canonical_set.source_cutoff_at,
            field_name="canonical_set.source_cutoff_at",
        ),
        source_batch_ids=source_batch_ids,
        orders=tuple(boundary_orders),
        fills=tuple(boundary_fills),
    )
    return VerifiedCanonicalEpisodeEvidenceProjection(
        canonical_set_key=str(canonical_set.set_key),
        canonical_member_count=replay_member_count,
        boundary=boundary,
        orders=tuple(builder_orders),
        fills=tuple(builder_fills),
        execution_groups=execution_groups,
        max_multiplier_proof_residual=max_multiplier_proof_residual,
    )


def load_verified_canonical_evidence_projection(
    session: Any,
    account_key: str,
    canonical_set_id: int,
) -> VerifiedCanonicalEvidenceProjection:
    """Return the boundary view of one fully replay-verified canonical set."""
    return load_verified_canonical_episode_evidence_projection(
        session,
        account_key,
        canonical_set_id,
    ).boundary


def _prepare_canonical(
    session: Any,
    account_key: str,
    *,
    canonical_set_id: Optional[int],
    assume_flat_if_missing: bool,
) -> Optional[_PreparedBuild]:
    canonical_set = _canonical_set_row(session, account_key, canonical_set_id)
    if canonical_set is None:
        return None
    if (
        not bool(canonical_set.analysis_ready)
        or int(canonical_set.blocking_issue_count) != 0
    ):
        raise EpisodeRepositoryError("canonical set is not analysis-ready")
    source_batch_ids = _canonical_source_batch_ids(canonical_set)
    batches = {
        batch_id: session.get(ImportBatch, batch_id)
        for batch_id in source_batch_ids
    }
    if any(batch is None for batch in batches.values()):
        raise EpisodeRepositoryError("canonical source batch is missing")
    typed_batches = {
        batch_id: batch
        for batch_id, batch in batches.items()
        if batch is not None
    }
    if any(
        str(batch.account_key) != account_key
        or str(batch.broker) != "moomoo"
        or str(batch.status) != "accepted"
        for batch in typed_batches.values()
    ):
        raise EpisodeRepositoryError(
            "canonical set references a cross-account or unaccepted batch"
        )
    csv_batches = sorted(
        (
            batch
            for batch in typed_batches.values()
            if str(batch.source_kind) == "csv"
        ),
        key=lambda item: (
            _utc(item.recorded_at, field_name="batch.recorded_at"),
            int(item.id),
        ),
    )
    if not csv_batches:
        raise EpisodeRepositoryError("canonical set has no CSV history baseline")
    baseline = csv_batches[-1]
    order_members, fill_members, group_members = _canonical_member_payloads(
        session,
        canonical_set,
        typed_batches,
    )
    if len(order_members) != int(canonical_set.canonical_order_count):
        raise EpisodeRepositoryError("canonical order count drifted")
    if len(fill_members) != int(canonical_set.canonical_fill_count):
        raise EpisodeRepositoryError("canonical fill count drifted")
    retained_group_by_currency, group_fill_bindings = (
        _canonical_execution_group_context(group_members, fill_members)
    )
    (
        multiplier_by_instrument,
        max_multiplier_proof_residual,
    ) = _canonical_multiplier_evidence(order_members, fill_members)

    order_id_by_identity: dict[tuple[str, str, str], int] = {}
    canonical_orders: list[CanonicalOrderEvidence] = []
    for member, payload, selected, batch in order_members:
        identity = _canonical_identity(
            payload.get("identity"),
            field_name="canonical order identity",
        )
        selected_id = int(selected.id)
        order_id_by_identity[identity] = selected_id
        filled_quantity = _decimal(payload.get("filled_quantity"))
        canonical_orders.append(
            CanonicalOrderEvidence(
                observation_id=selected_id,
                observation_key=f"canonical_order_{member.member_key}",
                instrument=_resolved_payload_instrument(
                    payload.get("instrument"),
                    multiplier_by_instrument,
                    require_proved_multiplier=(
                        filled_quantity is not None and filled_quantity > 0
                    ),
                ),
                side=str(payload.get("side") or ""),
                status=str(payload.get("status") or ""),
                ordered_at=_payload_datetime(
                    payload.get("ordered_at"),
                    field_name="canonical order ordered_at",
                ),
                source_sequence=stable_source_sequence(
                    source_row_number=selected.source_row_number,
                    source_kind=str(batch.source_kind),
                    observation_key=str(selected.observation_key),
                    source_record_sha256=str(selected.source_record_sha256),
                ),
                evidence_level=str(payload.get("evidence_level") or ""),
                filled_quantity=filled_quantity,
                average_fill_price=_decimal(payload.get("average_fill_price")),
                # Broker CSV order_amount is a submitted amount.  For partial
                # fills it is not execution cash flow, so the builder must use
                # filled quantity × average price × proved multiplier instead.
                amount=None,
                total_fee=_decimal(payload.get("total_fee")),
                fee_evidence_status=str(
                    payload.get("fee_evidence_status") or "unknown"
                ),
            )
        )

    canonical_fills: list[CanonicalFillEvidence] = []
    for member, payload, selected, batch in fill_members:
        linked_identity_raw = payload.get("linked_order_identity")
        linked_order_id = None
        if linked_identity_raw is not None:
            linked_identity = _canonical_identity(
                linked_identity_raw,
                field_name="canonical fill linked order identity",
            )
            linked_order_id = order_id_by_identity.get(linked_identity)
            if linked_order_id is None:
                raise EpisodeRepositoryError(
                    "canonical fill does not resolve to a selected order member"
                )
        canonical_fills.append(
            CanonicalFillEvidence(
                observation_id=int(selected.id),
                observation_key=f"canonical_fill_{member.member_key}",
                instrument=_resolved_payload_instrument(
                    payload.get("instrument"),
                    multiplier_by_instrument,
                ),
                side=str(payload.get("side") or ""),
                filled_at=_payload_datetime(
                    payload.get("filled_at"),
                    field_name="canonical fill filled_at",
                ),
                source_sequence=stable_source_sequence(
                    source_row_number=selected.source_row_number,
                    source_kind=str(batch.source_kind),
                    observation_key=str(selected.observation_key),
                    source_record_sha256=str(selected.source_record_sha256),
                ),
                quantity=Decimal(str(payload.get("quantity"))),
                price=Decimal(str(payload.get("price"))),
                amount=_decimal(payload.get("amount")),
                total_fee=_decimal(payload.get("total_fee")),
                broker_order_observation_id=linked_order_id,
            )
        )

    event_times = [
        item.ordered_at
        for item in canonical_orders
        if item.evidence_level.strip().lower() == "aggregate_only"
    ] + [item.filled_at for item in canonical_fills]
    batch_starts = [
        _utc(batch.window_start, field_name="batch.window_start")
        for batch in typed_batches.values()
        if batch.window_start is not None
    ]
    batch_ends = [
        _utc(batch.window_end, field_name="batch.window_end")
        for batch in typed_batches.values()
        if batch.window_end is not None
    ]
    if not batch_starts or not batch_ends:
        raise EpisodeRepositoryError("canonical source batches have no event window")
    source_window_start = min((*batch_starts, *event_times))
    source_cutoff_at = max((*batch_ends, *event_times))
    canonical_source_cutoff_at = _utc(
        canonical_set.source_cutoff_at,
        field_name="canonical_set.source_cutoff_at",
    )

    config = {
        "source_kind": _CANONICAL_SOURCE_KIND,
        "canonical_projection": {
            "name": _CANONICAL_PROJECTION_NAME,
            "version": _CANONICAL_PROJECTION_VERSION,
            "aggregate_order_amount_policy": "audit_only_not_execution_cash_flow",
            "contract_multiplier_policy": (
                "frozen_payload_or_selected_source_amount_proof"
            ),
            "multiplier_amount_tolerance": _AMOUNT_HALF_UNIT_TOLERANCE,
            "execution_group_fee_policy": _EXECUTION_GROUP_FEE_POLICY,
            "execution_group_count": len(group_members),
        },
        "opening_snapshot_complete": False,
        "assume_flat_if_missing": assume_flat_if_missing,
        "opening_positions": [],
        "exchange_timezone": _EXCHANGE_TIMEZONE,
        "strategy_grouping_policy": _STRATEGY_TYPE,
        "spread_inference": False,
        "roll_inference": False,
    }
    config_sha256 = _sha256_json(config)
    canonical_hash = str(canonical_set.canonical_set_sha256)
    result = build_position_episodes(
        tuple(canonical_orders),
        tuple(canonical_fills),
        source_window_start=source_window_start,
        source_cutoff_at=source_cutoff_at,
        opening_positions=(),
        opening_snapshot_complete=False,
        assume_flat_if_missing=assume_flat_if_missing,
        exchange_timezone=_EXCHANGE_TIMEZONE,
    )
    group_fill_ids = frozenset(group_fill_bindings)
    group_fee_affected_episode_keys = frozenset(
        episode.episode_key
        for episode in result.episodes
        if any(
            allocation.broker_fill_observation_id in group_fill_ids
            for allocation in episode.evidence
        )
    )
    build_key = _sha256_json(
        {
            "source_kind": _CANONICAL_SOURCE_KIND,
            "canonical_set_id": int(canonical_set.id),
            "canonical_set_key": str(canonical_set.set_key),
            "canonical_set_sha256": canonical_hash,
            "canonical_source_cutoff_at": canonical_source_cutoff_at,
            "source_event_window_start": source_window_start,
            "source_event_window_end": source_cutoff_at,
            "builder": {"name": BUILDER_NAME, "version": BUILDER_VERSION},
            "projection": {
                "name": _CANONICAL_PROJECTION_NAME,
                "version": _CANONICAL_PROJECTION_VERSION,
            },
            "builder_config_sha256": config_sha256,
        }
    )

    allocated_keys = {
        allocation.evidence_key for allocation in result.evidence_allocations
    }
    unresolved = max(0, result.source_event_count - len(allocated_keys))
    partial_reasons: list[str] = []
    if result.opening_boundary_policy != "complete_snapshot":
        partial_reasons.append("opening_boundary_unverified")
    if str(baseline.analysis_level) != "exact":
        partial_reasons.append("source_batch_partial")
    if any(
        episode.completeness_status not in {"exact", "complete"}
        for episode in result.episodes
    ):
        partial_reasons.append("episode_quality_partial")
    if not result.episodes:
        partial_reasons.append("no_execution_episodes")
    if unresolved:
        partial_reasons.append("unresolved_execution_evidence")
    if group_members:
        partial_reasons.append("execution_group_fee_retained_unallocated")
    partial_reasons = list(dict.fromkeys(partial_reasons))
    status = "partial" if partial_reasons else "succeeded"
    if result.episodes:
        episode_score = sum(
            (episode.completeness_score for episode in result.episodes),
            Decimal("0"),
        ) / Decimal(len(result.episodes))
    else:
        episode_score = Decimal("0")
    completeness_score = min(
        Decimal(baseline.completeness_score),
        episode_score,
    ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
    (
        source_known_fee_total,
        allocated_known_fee_total,
        fee_conservation_by_currency,
        fee_conserved,
    ) = _episode_fee_accounting(
        result.episodes,
        ordinary_source_total=result.source_known_fee_total,
        ordinary_allocated_total=result.allocated_known_fee_total,
        ordinary_source_by_currency=(
            _canonical_ordinary_source_fee_by_currency(
                order_members,
                fill_members,
            )
        ),
        retained_group_by_currency=retained_group_by_currency,
    )
    if not fee_conserved:
        raise EpisodeRepositoryError("canonical builder did not conserve source fees")
    (
        reconciliation_status,
        reconciliation_scope,
        reconciliation_window_start,
        reconciliation_window_end,
        reconciled_order_count,
        total_order_count,
    ) = _latest_reconciliation(session, baseline)
    headline = _headline_metrics(
        result.episodes,
        group_fee_affected_episode_keys=group_fee_affected_episode_keys,
    )
    retained_execution_group_fee_total = sum(
        retained_group_by_currency.values(),
        Decimal("0"),
    ).quantize(_STORAGE_QUANTUM)
    preview = EpisodeBuildPreview(
        batch_id=int(baseline.id),
        batch_key=str(baseline.batch_key),
        account_key=account_key,
        parser_name=str(baseline.parser_name),
        parser_version=str(baseline.parser_version),
        source_kind=_CANONICAL_SOURCE_KIND,
        source_batch_ids=source_batch_ids,
        canonical_set_id=int(canonical_set.id),
        canonical_set_sha256=canonical_hash,
        builder_name=BUILDER_NAME,
        builder_version=BUILDER_VERSION,
        build_key=build_key,
        builder_config_sha256=config_sha256,
        evidence_set_sha256=canonical_hash,
        multiplier_amount_tolerance=_AMOUNT_HALF_UNIT_TOLERANCE,
        max_multiplier_proof_residual=max_multiplier_proof_residual,
        status=status,
        reconciliation_status=reconciliation_status,
        reconciliation_scope=reconciliation_scope,
        reconciliation_window_start=reconciliation_window_start,
        reconciliation_window_end=reconciliation_window_end,
        reconciled_order_count=reconciled_order_count,
        total_order_count=total_order_count,
        completeness_score=completeness_score,
        opening_boundary_policy=result.opening_boundary_policy,
        partial_reasons=tuple(partial_reasons),
        source_window_start=result.source_window_start,
        source_cutoff_at=result.source_cutoff_at,
        source_event_count=result.source_event_count,
        aggregate_order_event_count=result.aggregate_order_event_count,
        detailed_fill_event_count=result.detailed_fill_event_count,
        source_known_fee_total=source_known_fee_total,
        allocated_known_fee_total=allocated_known_fee_total,
        retained_execution_group_fee_total=(
            retained_execution_group_fee_total
        ),
        fee_conservation_by_currency=fee_conservation_by_currency,
        fee_conserved=fee_conserved,
        execution_group_count=len(group_members),
        group_fee_affected_episode_count=len(
            group_fee_affected_episode_keys
        ),
        leg_fee_attribution_complete=not group_members,
        unresolved_evidence_count=unresolved,
        open_episode_count=headline["open_episode_count"],
        closed_episode_count=headline["closed_episode_count"],
        boundary_unverified_episode_count=headline[
            "boundary_unverified_episode_count"
        ],
        left_censored_episode_count=headline["left_censored_episode_count"],
        incomplete_episode_count=headline["incomplete_episode_count"],
        aggregate_only_episode_count=headline["aggregate_only_episode_count"],
        conditional_closed_episode_count=headline[
            "conditional_closed_episode_count"
        ],
        conditional_realized_pnl_gross=headline[
            "conditional_realized_pnl_gross"
        ],
        conditional_total_fee=headline["conditional_total_fee"],
        conditional_realized_pnl_net=headline[
            "conditional_realized_pnl_net"
        ],
        headline_episode_count=headline["headline_episode_count"],
        headline_realized_pnl_gross=headline[
            "headline_realized_pnl_gross"
        ],
        headline_total_fee=headline["headline_total_fee"],
        headline_realized_pnl_net=headline["headline_realized_pnl_net"],
        headline_excluded_episode_count=headline[
            "headline_excluded_episode_count"
        ],
        headline_exclusion_counts=headline["headline_exclusion_counts"],
        episodes=result.episodes,
    )
    return _PreparedBuild(
        preview=preview,
        batch_source_sha256=str(baseline.source_sha256),
        batch_analysis_level=str(baseline.analysis_level),
        source_order_ids=frozenset(
            int(selected.id) for _, _, selected, _ in order_members
        ),
        source_fill_ids=frozenset(
            int(selected.id) for _, _, selected, _ in fill_members
        ),
        group_fee_affected_episode_keys=group_fee_affected_episode_keys,
        canonical_set_key=str(canonical_set.set_key),
        canonical_source_cutoff_at=canonical_source_cutoff_at,
    )


def _prepare_latest(
    session: Any,
    account_key: str,
    *,
    assume_flat_if_missing: bool,
) -> Optional[_PreparedBuild]:
    batch = _latest_accepted_batch(session, account_key)
    if batch is None:
        return None
    orders = list(
        session.execute(
            select(BrokerOrderObservation)
            .where(BrokerOrderObservation.import_batch_id == batch.id)
            .order_by(BrokerOrderObservation.id)
        ).scalars()
    )
    fills = list(
        session.execute(
            select(BrokerFillObservation)
            .where(BrokerFillObservation.import_batch_id == batch.id)
            .order_by(BrokerFillObservation.id)
        ).scalars()
    )
    if len(orders) != int(batch.order_observation_count):
        raise EpisodeRepositoryError("latest batch order observation count drifted")
    if len(fills) != int(batch.fill_observation_count):
        raise EpisodeRepositoryError("latest batch fill observation count drifted")
    order_ids = frozenset(int(row.id) for row in orders)
    if any(
        row.broker_order_observation_id is not None
        and int(row.broker_order_observation_id) not in order_ids
        for row in fills
    ):
        raise EpisodeRepositoryError("latest batch contains a cross-batch fill link")

    (
        multiplier_by_instrument,
        max_multiplier_proof_residual,
    ) = _multiplier_evidence_map(orders, fills)
    canonical_orders = tuple(
        _project_order(row, multiplier_by_instrument) for row in orders
    )
    canonical_fills = tuple(
        _project_fill(row, multiplier_by_instrument) for row in fills
    )
    event_times = [
        _utc(row.ordered_at, field_name="ordered_at")
        for row in orders
        if str(row.evidence_level).lower() == "aggregate_only"
    ] + [_utc(row.filled_at, field_name="filled_at") for row in fills]
    source_window_start = _utc(batch.window_start, field_name="batch.window_start")
    source_cutoff_at = _utc(batch.window_end, field_name="batch.window_end")
    if event_times:
        source_window_start = min(source_window_start, min(event_times))
        source_cutoff_at = max(source_cutoff_at, max(event_times))

    config = {
        "opening_snapshot_complete": False,
        "assume_flat_if_missing": assume_flat_if_missing,
        "opening_positions": [],
        "exchange_timezone": _EXCHANGE_TIMEZONE,
        "strategy_grouping_policy": _STRATEGY_TYPE,
        "spread_inference": False,
        "roll_inference": False,
        "multiplier_candidates_by_asset_type": {
            "equity": ["1"],
            "option": ["100"],
        },
        "multiplier_amount_tolerance": _AMOUNT_HALF_UNIT_TOLERANCE,
        "max_multiplier_proof_residual": max_multiplier_proof_residual,
    }
    config_sha256 = _sha256_json(config)
    evidence_sha256 = _sha256_json(_evidence_payload(orders, fills))
    result = build_position_episodes(
        canonical_orders,
        canonical_fills,
        source_window_start=source_window_start,
        source_cutoff_at=source_cutoff_at,
        opening_positions=(),
        opening_snapshot_complete=False,
        assume_flat_if_missing=assume_flat_if_missing,
        exchange_timezone=_EXCHANGE_TIMEZONE,
    )
    build_key = _sha256_json(
        {
            "batch": {
                "id": int(batch.id),
                "batch_key": str(batch.batch_key),
                "source_sha256": str(batch.source_sha256),
            },
            "parser": {
                "name": str(batch.parser_name),
                "version": str(batch.parser_version),
            },
            "builder": {"name": BUILDER_NAME, "version": BUILDER_VERSION},
            "builder_config_sha256": config_sha256,
            "evidence_set_sha256": evidence_sha256,
        }
    )

    allocated_keys = {
        allocation.evidence_key for allocation in result.evidence_allocations
    }
    unresolved = max(0, result.source_event_count - len(allocated_keys))
    partial_reasons: list[str] = []
    if result.opening_boundary_policy != "complete_snapshot":
        partial_reasons.append("opening_boundary_unverified")
    if str(batch.analysis_level) != "exact":
        partial_reasons.append("source_batch_partial")
    if any(
        episode.completeness_status not in {"exact", "complete"}
        for episode in result.episodes
    ):
        partial_reasons.append("episode_quality_partial")
    if not result.episodes:
        partial_reasons.append("no_execution_episodes")
    if unresolved:
        partial_reasons.append("unresolved_execution_evidence")
    partial_reasons = list(dict.fromkeys(partial_reasons))
    status = "partial" if partial_reasons else "succeeded"
    if result.episodes:
        episode_score = sum(
            (episode.completeness_score for episode in result.episodes),
            Decimal("0"),
        ) / Decimal(len(result.episodes))
    else:
        episode_score = Decimal("0")
    completeness_score = min(
        Decimal(batch.completeness_score),
        episode_score,
    ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
    (
        source_known_fee_total,
        allocated_known_fee_total,
        fee_conservation_by_currency,
        fee_conserved,
    ) = _episode_fee_accounting(
        result.episodes,
        ordinary_source_total=result.source_known_fee_total,
        ordinary_allocated_total=result.allocated_known_fee_total,
        retained_group_by_currency={},
    )
    if not fee_conserved:
        raise EpisodeRepositoryError("builder did not conserve known source fees")

    (
        reconciliation_status,
        reconciliation_scope,
        reconciliation_window_start,
        reconciliation_window_end,
        reconciled_order_count,
        total_order_count,
    ) = _latest_reconciliation(session, batch)
    headline = _headline_metrics(result.episodes)
    preview = EpisodeBuildPreview(
        batch_id=int(batch.id),
        batch_key=str(batch.batch_key),
        account_key=account_key,
        parser_name=str(batch.parser_name),
        parser_version=str(batch.parser_version),
        source_kind=_CSV_SOURCE_KIND,
        source_batch_ids=(int(batch.id),),
        canonical_set_id=None,
        canonical_set_sha256=None,
        builder_name=BUILDER_NAME,
        builder_version=BUILDER_VERSION,
        build_key=build_key,
        builder_config_sha256=config_sha256,
        evidence_set_sha256=evidence_sha256,
        multiplier_amount_tolerance=_AMOUNT_HALF_UNIT_TOLERANCE,
        max_multiplier_proof_residual=max_multiplier_proof_residual,
        status=status,
        reconciliation_status=reconciliation_status,
        reconciliation_scope=reconciliation_scope,
        reconciliation_window_start=reconciliation_window_start,
        reconciliation_window_end=reconciliation_window_end,
        reconciled_order_count=reconciled_order_count,
        total_order_count=total_order_count,
        completeness_score=completeness_score,
        opening_boundary_policy=result.opening_boundary_policy,
        partial_reasons=tuple(partial_reasons),
        source_window_start=result.source_window_start,
        source_cutoff_at=result.source_cutoff_at,
        source_event_count=result.source_event_count,
        aggregate_order_event_count=result.aggregate_order_event_count,
        detailed_fill_event_count=result.detailed_fill_event_count,
        source_known_fee_total=source_known_fee_total,
        allocated_known_fee_total=allocated_known_fee_total,
        retained_execution_group_fee_total=Decimal("0"),
        fee_conservation_by_currency=fee_conservation_by_currency,
        fee_conserved=fee_conserved,
        execution_group_count=0,
        group_fee_affected_episode_count=0,
        leg_fee_attribution_complete=True,
        unresolved_evidence_count=unresolved,
        open_episode_count=headline["open_episode_count"],
        closed_episode_count=headline["closed_episode_count"],
        boundary_unverified_episode_count=headline[
            "boundary_unverified_episode_count"
        ],
        left_censored_episode_count=headline["left_censored_episode_count"],
        incomplete_episode_count=headline["incomplete_episode_count"],
        aggregate_only_episode_count=headline["aggregate_only_episode_count"],
        conditional_closed_episode_count=headline[
            "conditional_closed_episode_count"
        ],
        conditional_realized_pnl_gross=headline[
            "conditional_realized_pnl_gross"
        ],
        conditional_total_fee=headline["conditional_total_fee"],
        conditional_realized_pnl_net=headline[
            "conditional_realized_pnl_net"
        ],
        headline_episode_count=headline["headline_episode_count"],
        headline_realized_pnl_gross=headline[
            "headline_realized_pnl_gross"
        ],
        headline_total_fee=headline["headline_total_fee"],
        headline_realized_pnl_net=headline["headline_realized_pnl_net"],
        headline_excluded_episode_count=headline[
            "headline_excluded_episode_count"
        ],
        headline_exclusion_counts=headline["headline_exclusion_counts"],
        episodes=result.episodes,
    )
    return _PreparedBuild(
        preview=preview,
        batch_source_sha256=str(batch.source_sha256),
        batch_analysis_level=str(batch.analysis_level),
        source_order_ids=order_ids,
        source_fill_ids=frozenset(int(row.id) for row in fills),
    )


def preview_latest_position_episodes(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    assume_flat_if_missing: bool = True,
) -> Optional[EpisodeBuildPreview]:
    """Preview the newest accepted batch without appending episode rows."""
    account_key = account_key.strip()
    if not account_key:
        raise EpisodeRepositoryError("account_key cannot be empty")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        prepared = _prepare_latest(
            session,
            account_key,
            assume_flat_if_missing=assume_flat_if_missing,
        )
        return prepared.preview if prepared is not None else None


def preview_canonical_position_episodes(
    canonical_set_id: Optional[int] = None,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    assume_flat_if_missing: bool = True,
) -> Optional[EpisodeBuildPreview]:
    """Preview one frozen analysis-ready canonical set without appending rows."""
    account_key = account_key.strip()
    if not account_key:
        raise EpisodeRepositoryError("account_key cannot be empty")
    if canonical_set_id is not None and canonical_set_id <= 0:
        raise EpisodeRepositoryError("canonical_set_id must be positive")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        prepared = _prepare_canonical(
            session,
            account_key,
            canonical_set_id=canonical_set_id,
            assume_flat_if_missing=assume_flat_if_missing,
        )
        return prepared.preview if prepared is not None else None


def _strategy_key(position: PositionEpisodeRecord) -> str:
    return "single_position_" + _sha256_json(
        {"position_episode_key": position.episode_key, "policy": _STRATEGY_TYPE}
    )


def _append_strategy(
    session: Any,
    build_id: int,
    position: PositionEpisodeRecord,
) -> StrategyEpisode:
    strategy_key = _strategy_key(position)
    row = StrategyEpisode(
        episode_build_id=build_id,
        episode_key=strategy_key,
        lineage_key="single_position_lineage_" + _sha256_json(
            {"position_lineage_key": position.lineage_key}
        ),
        broker=position.instrument.broker,
        account_key=position.instrument.account_key,
        underlying=position.instrument.underlying,
        strategy_type=_STRATEGY_TYPE,
        direction=position.direction,
        lifecycle_status=position.lifecycle_status,
        grouping_method=_GROUPING_METHOD,
        grouping_confidence=Decimal("1"),
        roll_chain_key=None,
        continuation_of_lineage_key=None,
        currency=position.instrument.currency,
        opened_at=position.opened_at,
        closed_at=position.closed_at,
        position_count=1,
        leg_count=1,
        opening_cash_flow=position.opening_cash_flow,
        closing_cash_flow=position.closing_cash_flow,
        realized_pnl_gross=position.realized_pnl_gross,
        total_fee=position.total_fee,
        realized_pnl_net=position.realized_pnl_net,
        pnl_precision=(
            "conditional"
            if position.realized_pnl_net is not None
            and not position.left_boundary_verified
            else (
                "exact" if position.realized_pnl_net is not None else "unavailable"
            )
        ),
        max_profit=None,
        max_loss=None,
        grouping_evidence_json=_canonical_json(
            {
                "position_episode_key": position.episode_key,
                "one_to_one": True,
                "strategy_classification": "unclassified",
                "strategy_intent": "unknown",
                "spread_inferred": False,
                "roll_inferred": False,
            }
        ),
        evidence_summary_json=_canonical_json(
            {"allocation_count": len(position.evidence)}
        ),
        completeness_score=position.completeness_score,
        completeness_status=position.completeness_status,
        completeness_json=_canonical_json(
            {
                "inherits_position_episode": True,
                "left_boundary_verified": position.left_boundary_verified,
                "opening_boundary_policy": position.opening_boundary_policy,
            }
        ),
        provenance_json=_canonical_json(
            {
                "builder_name": BUILDER_NAME,
                "builder_version": BUILDER_VERSION,
                "grouping_method": _GROUPING_METHOD,
                "no_spread_or_roll_inference": True,
            }
        ),
    )
    session.add(row)
    session.flush()
    return row


def _append_result(
    session: Any,
    build: EpisodeBuild,
    *,
    duplicate: bool,
    boundary_policy: str,
) -> EpisodeBuildAppendResult:
    evidence_count = int(
        session.execute(
            select(func.count(PositionEpisodeEvidence.id)).where(
                PositionEpisodeEvidence.episode_build_id == build.id
            )
        ).scalar_one()
    )
    return EpisodeBuildAppendResult(
        build_id=int(build.id),
        build_key=str(build.build_key),
        duplicate=duplicate,
        status=str(build.status),
        strategy_episode_count=int(build.strategy_episode_count),
        position_episode_count=int(build.position_episode_count),
        evidence_allocation_count=evidence_count,
        opening_boundary_policy=boundary_policy,
    )


def _append_prepared_build(
    session: Any,
    prepared: _PreparedBuild,
) -> EpisodeBuildAppendResult:
    preview = prepared.preview
    existing = session.execute(
        select(EpisodeBuild).where(EpisodeBuild.build_key == preview.build_key)
    ).scalar_one_or_none()
    if existing is not None:
        link = session.execute(
            select(EpisodeBuildCanonicalSource).where(
                EpisodeBuildCanonicalSource.episode_build_id == existing.id
            )
        ).scalar_one_or_none()
        if preview.source_kind == _CANONICAL_SOURCE_KIND:
            if (
                link is None
                or int(link.canonical_set_id) != preview.canonical_set_id
                or str(link.canonical_set_sha256)
                != preview.canonical_set_sha256
            ):
                raise EpisodeRepositoryError(
                    "existing canonical build has invalid source binding"
                )
        elif link is not None:
            # Neither a CSV build nor a snapshot-fence future build may own a
            # canonical source binding.
            raise EpisodeRepositoryError(
                "existing non-canonical build unexpectedly has a canonical "
                "source binding"
            )
        report = _parse_json(existing.build_report_json)
        return _append_result(
            session,
            existing,
            duplicate=True,
            boundary_policy=str(
                report.get("opening_boundary_policy")
                or preview.opening_boundary_policy
            ),
        )

    build_report = {
        "source_kind": preview.source_kind,
        "source_batch_ids": list(preview.source_batch_ids),
        "canonical_set_id": preview.canonical_set_id,
        "canonical_set_sha256": preview.canonical_set_sha256,
        "opening_boundary_policy": preview.opening_boundary_policy,
        "partial_reasons": list(preview.partial_reasons),
        "source_window_start": preview.source_window_start,
        "source_cutoff_at": preview.source_cutoff_at,
        "source_event_count": preview.source_event_count,
        "aggregate_order_event_count": preview.aggregate_order_event_count,
        "detailed_fill_event_count": preview.detailed_fill_event_count,
        "source_known_fee_total": preview.source_known_fee_total,
        "allocated_known_fee_total": preview.allocated_known_fee_total,
        "retained_execution_group_fee_total": (
            preview.retained_execution_group_fee_total
        ),
        "fee_conservation_by_currency": {
            currency: dict(values)
            for currency, values in preview.fee_conservation_by_currency.items()
        },
        "fee_conserved": preview.fee_conserved,
        "execution_group_count": preview.execution_group_count,
        "group_fee_affected_episode_count": (
            preview.group_fee_affected_episode_count
        ),
        "leg_fee_attribution_complete": (
            preview.leg_fee_attribution_complete
        ),
        "reconciliation_scope": preview.reconciliation_scope,
        "reconciliation_window_start": preview.reconciliation_window_start,
        "reconciliation_window_end": preview.reconciliation_window_end,
        "reconciled_order_count": preview.reconciled_order_count,
        "total_order_count": preview.total_order_count,
        "open_episode_count": preview.open_episode_count,
        "closed_episode_count": preview.closed_episode_count,
        "boundary_unverified_episode_count": (
            preview.boundary_unverified_episode_count
        ),
        "left_censored_episode_count": preview.left_censored_episode_count,
        "incomplete_episode_count": preview.incomplete_episode_count,
        "aggregate_only_episode_count": preview.aggregate_only_episode_count,
        "conditional_result_basis": (
            "closed_known_cash_flows_under_opening_boundary_policy"
        ),
        "conditional_closed_episode_count": (
            preview.conditional_closed_episode_count
        ),
        "conditional_realized_pnl_gross": (
            preview.conditional_realized_pnl_gross
        ),
        "conditional_total_fee": preview.conditional_total_fee,
        "conditional_realized_pnl_net": preview.conditional_realized_pnl_net,
        "headline_episode_count": preview.headline_episode_count,
        "headline_realized_pnl_gross": preview.headline_realized_pnl_gross,
        "headline_total_fee": preview.headline_total_fee,
        "headline_realized_pnl_net": preview.headline_realized_pnl_net,
        "headline_excluded_episode_count": (
            preview.headline_excluded_episode_count
        ),
        "headline_exclusion_counts": dict(preview.headline_exclusion_counts),
        "strategy_policy": _STRATEGY_TYPE,
        "spread_inferred": False,
        "roll_inferred": False,
        "lot_matching_policy": "not_applicable_signed_position_lifecycle",
        "multiplier_amount_tolerance": preview.multiplier_amount_tolerance,
        "max_multiplier_proof_residual": preview.max_multiplier_proof_residual,
    }
    if preview.source_kind == SNAPSHOT_FENCE_SOURCE_KIND:
        if prepared.snapshot_fence_provenance is None:
            raise EpisodeRepositoryError(
                "future build is missing its snapshot-fence provenance"
            )
        provenance = {
            "source_kind": preview.source_kind,
            "source_batch_ids": list(preview.source_batch_ids),
            "target_canonical_set_id": preview.canonical_set_id,
            "target_canonical_set_key": prepared.canonical_set_key,
            "target_canonical_set_sha256": preview.canonical_set_sha256,
            "target_canonical_source_cutoff_at": (
                prepared.canonical_source_cutoff_at
            ),
            "builder_name": preview.builder_name,
            "builder_version": preview.builder_version,
            "builder_config_sha256": preview.builder_config_sha256,
            "evidence_set_sha256": preview.evidence_set_sha256,
            "multiplier_amount_tolerance": (
                preview.multiplier_amount_tolerance
            ),
            "max_multiplier_proof_residual": (
                preview.max_multiplier_proof_residual
            ),
            "snapshot_fence": dict(prepared.snapshot_fence_provenance),
        }
    else:
        provenance = {
            "source_kind": preview.source_kind,
            "source_batch_id": preview.batch_id,
            "source_batch_ids": list(preview.source_batch_ids),
            "source_batch_key": preview.batch_key,
            "source_sha256": prepared.batch_source_sha256,
            "parser_name": preview.parser_name,
            "parser_version": preview.parser_version,
            "canonical_set_id": preview.canonical_set_id,
            "canonical_set_key": prepared.canonical_set_key,
            "canonical_set_sha256": preview.canonical_set_sha256,
            "canonical_source_cutoff_at": prepared.canonical_source_cutoff_at,
            "builder_name": preview.builder_name,
            "builder_version": preview.builder_version,
            "builder_config_sha256": preview.builder_config_sha256,
            "evidence_set_sha256": preview.evidence_set_sha256,
            "multiplier_amount_tolerance": preview.multiplier_amount_tolerance,
            "max_multiplier_proof_residual": (
                preview.max_multiplier_proof_residual
            ),
        }
    if preview.source_kind == _CANONICAL_SOURCE_KIND:
        provenance["canonical_projection"] = {
            "name": _CANONICAL_PROJECTION_NAME,
            "version": _CANONICAL_PROJECTION_VERSION,
            "aggregate_order_amount_policy": (
                "audit_only_not_execution_cash_flow"
            ),
            "execution_group_fee_policy": _EXECUTION_GROUP_FEE_POLICY,
        }
    build = EpisodeBuild(
        build_key=preview.build_key,
        broker="moomoo",
        account_key=preview.account_key,
        builder_name=preview.builder_name,
        builder_version=preview.builder_version,
        builder_config_sha256=preview.builder_config_sha256,
        evidence_set_sha256=preview.evidence_set_sha256,
        source_batch_ids_json=_canonical_json(list(preview.source_batch_ids)),
        source_cutoff_at=preview.source_cutoff_at,
        status=preview.status,
        strategy_episode_count=len(preview.episodes),
        position_episode_count=len(preview.episodes),
        unresolved_evidence_count=preview.unresolved_evidence_count,
        reconciliation_status=preview.reconciliation_status,
        build_report_json=_canonical_json(build_report),
        completeness_score=preview.completeness_score,
        completeness_json=_canonical_json(
            {
                "source_batch_analysis_level": prepared.batch_analysis_level,
                "opening_boundary_policy": preview.opening_boundary_policy,
                "partial_reasons": list(preview.partial_reasons),
                "fee_conserved": preview.fee_conserved,
                "execution_group_count": preview.execution_group_count,
                "group_fee_affected_episode_count": (
                    preview.group_fee_affected_episode_count
                ),
                "leg_fee_attribution_complete": (
                    preview.leg_fee_attribution_complete
                ),
            }
        ),
        provenance_json=_canonical_json(provenance),
    )
    session.add(build)
    session.flush()

    if preview.source_kind == _CANONICAL_SOURCE_KIND:
        if (
            preview.canonical_set_id is None
            or preview.canonical_set_sha256 is None
            or prepared.canonical_source_cutoff_at is None
        ):
            raise EpisodeRepositoryError("canonical build source is incomplete")
        link_key = _sha256_json(
            {
                "episode_build_key": preview.build_key,
                "canonical_set_id": preview.canonical_set_id,
                "canonical_set_sha256": preview.canonical_set_sha256,
                "projection_name": _CANONICAL_PROJECTION_NAME,
                "projection_version": _CANONICAL_PROJECTION_VERSION,
            }
        )
        session.add(
            EpisodeBuildCanonicalSource(
                link_key=link_key,
                episode_build_id=int(build.id),
                canonical_set_id=preview.canonical_set_id,
                canonical_set_sha256=preview.canonical_set_sha256,
                projection_name=_CANONICAL_PROJECTION_NAME,
                projection_version=_CANONICAL_PROJECTION_VERSION,
                canonical_source_cutoff_at=(
                    prepared.canonical_source_cutoff_at
                ),
                source_batch_ids_json=_canonical_json(
                    list(preview.source_batch_ids)
                ),
            )
        )
        session.flush()

    for position in preview.episodes:
        strategy = _append_strategy(session, int(build.id), position)
        position_kwargs = position.as_model_kwargs(
            episode_build_id=int(build.id),
            strategy_episode_id=int(strategy.id),
        )
        if position.episode_key in prepared.group_fee_affected_episode_keys:
            for field_name, additions in (
                (
                    "matching_evidence_json",
                    {
                        "execution_group_fee_scope": (
                            _EXECUTION_GROUP_FEE_POLICY
                        ),
                    },
                ),
                (
                    "evidence_summary_json",
                    {"group_fee_unallocated": True},
                ),
                (
                    "completeness_json",
                    {"leg_fee_attribution_complete": False},
                ),
                (
                    "provenance_json",
                    {
                        "execution_group_fee_policy": (
                            _EXECUTION_GROUP_FEE_POLICY
                        ),
                    },
                ),
            ):
                parsed = _strict_json_object(
                    str(position_kwargs[field_name]),
                    field_name=field_name,
                )
                parsed.update(additions)
                position_kwargs[field_name] = _canonical_json(parsed)
        position_row = PositionEpisode(**position_kwargs)
        session.add(position_row)
        session.flush()
        for allocation in position.evidence:
            order_id = allocation.broker_order_observation_id
            fill_id = allocation.broker_fill_observation_id
            if order_id is not None and order_id not in prepared.source_order_ids:
                raise EpisodeRepositoryError(
                    "episode allocation references an order outside its source set"
                )
            if fill_id is not None and fill_id not in prepared.source_fill_ids:
                raise EpisodeRepositoryError(
                    "episode allocation references a fill outside its source set"
                )
            session.add(
                PositionEpisodeEvidence(
                    **allocation.as_model_kwargs(
                        episode_build_id=int(build.id),
                        position_episode_id=int(position_row.id),
                    )
                )
            )
    session.flush()
    return _append_result(
        session,
        build,
        duplicate=False,
        boundary_policy=preview.opening_boundary_policy,
    )


def _require_boundary_acceptance(
    preview: EpisodeBuildPreview,
    *,
    accept_assumed_flat: bool,
) -> None:
    assumption_used = preview.opening_boundary_policy in {
        "assumed_flat_unverified",
        "mixed_explicit_and_assumed",
    }
    if assumption_used and not accept_assumed_flat:
        raise EpisodeRepositoryError(
            "assumed-flat opening boundary requires explicit acceptance"
        )


def _require_group_fee_scope_acceptance(
    preview: EpisodeBuildPreview,
    *,
    accept_group_fee_scope: bool,
) -> None:
    if (
        preview.group_fee_affected_episode_count > 0
        and not preview.leg_fee_attribution_complete
        and not accept_group_fee_scope
    ):
        raise EpisodeRepositoryError(
            "execution-group fee is exact only at group scope; affected leg "
            "episodes have no fee/net P&L and are excluded from headline; "
            "explicit acceptance is required"
        )


def append_latest_position_episode_build(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    accept_assumed_flat: bool,
) -> EpisodeBuildAppendResult:
    """Append the legacy CSV-based build without selecting canonical sources."""
    account_key = account_key.strip()
    if not account_key:
        raise EpisodeRepositoryError("account_key cannot be empty")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        prepared = _prepare_latest(
            session,
            account_key,
            assume_flat_if_missing=True,
        )
        if prepared is None:
            raise EpisodeRepositoryError("no accepted import batch exists")
        _require_boundary_acceptance(
            prepared.preview,
            accept_assumed_flat=accept_assumed_flat,
        )
        return _append_prepared_build(session, prepared)


def append_canonical_position_episode_build(
    canonical_set_id: int,
    expected_canonical_set_sha256: str,
    expected_build_key: str,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    accept_assumed_flat: bool,
    accept_group_fee_scope: bool = False,
) -> EpisodeBuildAppendResult:
    """Append one explicitly previewed canonical build; never activate it."""
    account_key = account_key.strip()
    if not account_key:
        raise EpisodeRepositoryError("account_key cannot be empty")
    if canonical_set_id <= 0:
        raise EpisodeRepositoryError("canonical_set_id must be positive")
    expected_hash = expected_canonical_set_sha256.strip().lower()
    if len(expected_hash) != 64 or any(
        character not in "0123456789abcdef" for character in expected_hash
    ):
        raise EpisodeRepositoryError(
            "expected_canonical_set_sha256 must be SHA-256"
        )
    expected_key = expected_build_key.strip().lower()
    if len(expected_key) != 64 or any(
        character not in "0123456789abcdef" for character in expected_key
    ):
        raise EpisodeRepositoryError("expected_build_key must be SHA-256")

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prepared = _prepare_canonical(
            session,
            account_key,
            canonical_set_id=canonical_set_id,
            assume_flat_if_missing=True,
        )
        if prepared is None:
            raise EpisodeRepositoryError("canonical evidence set does not exist")
        preview = prepared.preview
        if preview.canonical_set_sha256 != expected_hash:
            raise EpisodeRepositoryError(
                "canonical evidence changed after the confirmed preview"
            )
        if preview.build_key != expected_key:
            raise EpisodeRepositoryError(
                "canonical episode plan changed after the confirmed preview"
            )
        _require_boundary_acceptance(
            preview,
            accept_assumed_flat=accept_assumed_flat,
        )
        _require_group_fee_scope_acceptance(
            preview,
            accept_group_fee_scope=accept_group_fee_scope,
        )
        return _append_prepared_build(session, prepared)


def _latest_build(session: Any, account_key: str) -> Optional[EpisodeBuild]:
    try:
        build, _activation = _resolve_effective_episode_build(
            session,
            account_key,
        )
    except EpisodeBuildActivationError as exc:
        raise EpisodeRepositoryError(str(exc)) from exc
    return build


def _source_batch_ids(build: EpisodeBuild) -> tuple[int, ...]:
    try:
        parsed = json.loads(build.source_batch_ids_json)
        values = tuple(int(value) for value in parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EpisodeRepositoryError("episode build has invalid source batch IDs") from exc
    if not values or any(value <= 0 for value in values):
        raise EpisodeRepositoryError("episode build has invalid source batch IDs")
    return values


def _source_batch_id(build: EpisodeBuild) -> int:
    return _source_batch_ids(build)[0]


def _stored_conditional_closed_metrics(
    session: Any,
    build_id: int,
) -> tuple[int, Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    """Read-only fallback for builds recorded before conditional fields existed."""
    rows = session.execute(
        select(
            PositionEpisode.realized_pnl_gross,
            PositionEpisode.total_fee,
            PositionEpisode.realized_pnl_net,
        ).where(
            PositionEpisode.episode_build_id == build_id,
            PositionEpisode.lifecycle_status == "closed",
            PositionEpisode.realized_pnl_gross.is_not(None),
            PositionEpisode.total_fee.is_not(None),
            PositionEpisode.realized_pnl_net.is_not(None),
        )
    ).all()
    if not rows:
        return 0, None, None, None

    def _sum(index: int) -> Decimal:
        return sum(
            (Decimal(row[index]) for row in rows),
            Decimal("0"),
        ).quantize(_STORAGE_QUANTUM)

    return len(rows), _sum(0), _sum(1), _sum(2)


def _conditional_closed_summary(
    session: Any,
    build: EpisodeBuild,
    report: Mapping[str, Any],
) -> tuple[int, Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    keys = (
        "conditional_closed_episode_count",
        "conditional_realized_pnl_gross",
        "conditional_total_fee",
        "conditional_realized_pnl_net",
    )
    if all(key in report for key in keys):
        count = int(report["conditional_closed_episode_count"])

        def _value(key: str) -> Optional[Decimal]:
            value = report[key]
            return Decimal(str(value)) if value is not None else None

        return (
            count,
            _value("conditional_realized_pnl_gross"),
            _value("conditional_total_fee"),
            _value("conditional_realized_pnl_net"),
        )
    return _stored_conditional_closed_metrics(session, int(build.id))


def _report_datetime(
    report: Mapping[str, Any],
    key: str,
) -> Optional[datetime]:
    value = report.get(key)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return _utc(parsed, field_name=key)


def _episode_summary_for_build(
    session: Any,
    build: EpisodeBuild,
) -> EpisodeBuildSummary:
    report = _parse_json(build.build_report_json)
    (
        conditional_count,
        conditional_gross,
        conditional_fee,
        conditional_net,
    ) = _conditional_closed_summary(session, build, report)
    raw_fee_by_currency = report.get("fee_conservation_by_currency") or {}
    if not isinstance(raw_fee_by_currency, dict):
        raise EpisodeRepositoryError(
            "episode build fee conservation map is invalid"
        )
    fee_conservation_by_currency: dict[str, dict[str, Decimal]] = {}
    for raw_currency, raw_values in raw_fee_by_currency.items():
        if not isinstance(raw_values, dict):
            raise EpisodeRepositoryError(
                "episode build fee conservation row is invalid"
            )
        fee_conservation_by_currency[str(raw_currency)] = {
            str(key): Decimal(str(value))
            for key, value in raw_values.items()
        }
    evidence_count = int(
        session.execute(
            select(func.count(PositionEpisodeEvidence.id)).where(
                PositionEpisodeEvidence.episode_build_id == build.id
            )
        ).scalar_one()
    )
    source_batch_ids = _source_batch_ids(build)
    canonical_link = session.execute(
        select(EpisodeBuildCanonicalSource).where(
            EpisodeBuildCanonicalSource.episode_build_id == build.id
        )
    ).scalar_one_or_none()
    if canonical_link is None:
        fence_link = session.execute(
            select(EpisodeBuildSnapshotFenceSource).where(
                EpisodeBuildSnapshotFenceSource.episode_build_id == build.id
            )
        ).scalar_one_or_none()
        if fence_link is None:
            source_kind = _CSV_SOURCE_KIND
            canonical_set_id = None
            canonical_set_sha256 = None
        else:
            source_kind = SNAPSHOT_FENCE_SOURCE_KIND
            canonical_set_id = int(fence_link.target_canonical_set_id)
            canonical_set_sha256 = str(fence_link.target_canonical_set_sha256)
            if (
                int(report.get("canonical_set_id") or 0) != canonical_set_id
                or str(report.get("canonical_set_sha256") or "")
                != canonical_set_sha256
            ):
                raise EpisodeRepositoryError(
                    "snapshot-fence episode link disagrees with its build"
                )
    else:
        source_kind = _CANONICAL_SOURCE_KIND
        canonical_set_id = int(canonical_link.canonical_set_id)
        canonical_set_sha256 = str(canonical_link.canonical_set_sha256)
        try:
            linked_batch_ids = tuple(
                int(value)
                for value in json.loads(canonical_link.source_batch_ids_json)
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EpisodeRepositoryError(
                "canonical episode link has invalid source batches"
            ) from exc
        if (
            linked_batch_ids != source_batch_ids
            or canonical_set_sha256 != str(build.evidence_set_sha256)
        ):
            raise EpisodeRepositoryError(
                "canonical episode link disagrees with its build"
            )
    return EpisodeBuildSummary(
        build_id=int(build.id),
        build_key=str(build.build_key),
        source_batch_id=source_batch_ids[0],
        source_batch_ids=source_batch_ids,
        source_kind=source_kind,
        canonical_set_id=canonical_set_id,
        canonical_set_sha256=canonical_set_sha256,
        account_key=str(build.account_key),
        status=str(build.status),
        builder_name=str(build.builder_name),
        builder_version=str(build.builder_version),
        reconciliation_status=str(build.reconciliation_status),
        reconciliation_scope=str(
            report.get("reconciliation_scope", "unknown")
        ),
        reconciliation_window_start=_report_datetime(
            report,
            "reconciliation_window_start",
        ),
        reconciliation_window_end=_report_datetime(
            report,
            "reconciliation_window_end",
        ),
        reconciled_order_count=int(
            report.get("reconciled_order_count", 0)
        ),
        total_order_count=int(report.get("total_order_count", 0)),
        completeness_score=Decimal(build.completeness_score),
        multiplier_amount_tolerance=Decimal(
            str(
                report.get(
                    "multiplier_amount_tolerance",
                    _AMOUNT_HALF_UNIT_TOLERANCE,
                )
            )
        ),
        max_multiplier_proof_residual=Decimal(
            str(report.get("max_multiplier_proof_residual", "0"))
        ),
        opening_boundary_policy=str(
            report.get("opening_boundary_policy", "unknown")
        ),
        partial_reasons=tuple(report.get("partial_reasons") or ()),
        strategy_episode_count=int(build.strategy_episode_count),
        position_episode_count=int(build.position_episode_count),
        evidence_allocation_count=evidence_count,
        unresolved_evidence_count=int(build.unresolved_evidence_count),
        open_episode_count=int(report.get("open_episode_count", 0)),
        closed_episode_count=int(report.get("closed_episode_count", 0)),
        boundary_unverified_episode_count=int(
            report.get("boundary_unverified_episode_count", 0)
        ),
        left_censored_episode_count=int(
            report.get("left_censored_episode_count", 0)
        ),
        incomplete_episode_count=int(
            report.get("incomplete_episode_count", 0)
        ),
        aggregate_only_episode_count=int(
            report.get("aggregate_only_episode_count", 0)
        ),
        conditional_closed_episode_count=conditional_count,
        conditional_realized_pnl_gross=conditional_gross,
        conditional_total_fee=conditional_fee,
        conditional_realized_pnl_net=conditional_net,
        headline_episode_count=int(
            report.get("headline_episode_count", 0)
        ),
        headline_realized_pnl_gross=(
            Decimal(str(report["headline_realized_pnl_gross"]))
            if report.get("headline_realized_pnl_gross") is not None
            else None
        ),
        headline_total_fee=(
            Decimal(str(report["headline_total_fee"]))
            if report.get("headline_total_fee") is not None
            else None
        ),
        headline_realized_pnl_net=(
            Decimal(str(report["headline_realized_pnl_net"]))
            if report.get("headline_realized_pnl_net") is not None
            else None
        ),
        headline_excluded_episode_count=int(
            report.get("headline_excluded_episode_count", 0)
        ),
        headline_exclusion_counts=(
            report.get("headline_exclusion_counts") or {}
        ),
        source_event_count=int(report.get("source_event_count", 0)),
        aggregate_order_event_count=int(
            report.get("aggregate_order_event_count", 0)
        ),
        detailed_fill_event_count=int(
            report.get("detailed_fill_event_count", 0)
        ),
        source_known_fee_total=Decimal(
            str(report.get("source_known_fee_total", "0"))
        ),
        allocated_known_fee_total=Decimal(
            str(report.get("allocated_known_fee_total", "0"))
        ),
        retained_execution_group_fee_total=Decimal(
            str(report.get("retained_execution_group_fee_total", "0"))
        ),
        fee_conservation_by_currency=fee_conservation_by_currency,
        fee_conserved=bool(report.get("fee_conserved", False)),
        execution_group_count=int(report.get("execution_group_count", 0)),
        group_fee_affected_episode_count=int(
            report.get("group_fee_affected_episode_count", 0)
        ),
        leg_fee_attribution_complete=bool(
            report.get("leg_fee_attribution_complete", True)
        ),
        source_window_start=_utc(
            datetime.fromisoformat(str(report["source_window_start"])),
            field_name="source_window_start",
        ),
        source_cutoff_at=_utc(
            build.source_cutoff_at,
            field_name="source_cutoff_at",
        ),
        recorded_at=_utc(build.recorded_at, field_name="recorded_at"),
    )


def get_episode_summary(
    build_id: int,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> Optional[EpisodeBuildSummary]:
    """Return one explicit immutable build, including canonical provenance."""
    if build_id <= 0:
        raise EpisodeRepositoryError("build_id must be positive")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = session.execute(
            select(EpisodeBuild).where(
                EpisodeBuild.id == build_id,
                EpisodeBuild.account_key == account_key,
            )
        ).scalar_one_or_none()
        return None if build is None else _episode_summary_for_build(session, build)


def get_latest_episode_summary(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> Optional[EpisodeBuildSummary]:
    """Return the activated canonical build, or the newest CSV fallback."""
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, account_key)
        return None if build is None else _episode_summary_for_build(session, build)


def _position_item(
    position: PositionEpisode,
    strategy: StrategyEpisode,
    review: Optional[ReviewAnnotation] = None,
) -> PositionEpisodeListItem:
    matching = _parse_json(position.matching_evidence_json)
    completeness = _parse_json(position.completeness_json)
    return PositionEpisodeListItem(
        episode_id=int(position.id),
        build_id=int(position.episode_build_id),
        strategy_episode_id=int(position.strategy_episode_id),
        episode_key=str(position.episode_key),
        lineage_key=str(position.lineage_key),
        strategy_type=str(strategy.strategy_type),
        raw_symbol=str(position.raw_symbol),
        asset_type=str(position.asset_type),
        underlying=str(position.underlying),
        expiry=position.expiry,
        strike=_decimal(position.strike),
        option_right=str(position.option_right) if position.option_right else None,
        contract_multiplier=_decimal(position.contract_multiplier),
        contract_multiplier_basis=str(
            matching.get("contract_multiplier_basis", "unknown")
        ),
        direction=str(position.direction),
        lifecycle_status=str(position.lifecycle_status),
        currency=str(position.currency),
        opened_at=_utc(position.opened_at, field_name="opened_at"),
        closed_at=_optional_utc(position.closed_at),
        hold_seconds=(
            int(position.hold_seconds) if position.hold_seconds is not None else None
        ),
        opened_quantity=Decimal(position.opened_quantity),
        closed_quantity=Decimal(position.closed_quantity),
        remaining_quantity=Decimal(position.remaining_quantity),
        average_entry_price=_decimal(position.average_entry_price),
        average_exit_price=_decimal(position.average_exit_price),
        realized_pnl_gross=_decimal(position.realized_pnl_gross),
        total_fee=_decimal(position.total_fee),
        realized_pnl_net=_decimal(position.realized_pnl_net),
        construction_basis=str(position.construction_basis),
        left_boundary_verified=bool(
            completeness.get("left_boundary_verified", False)
        ),
        opening_boundary_policy=str(
            matching.get("opening_boundary_policy", "unknown")
        ),
        is_left_censored=bool(position.is_left_censored),
        is_right_censored=bool(position.is_right_censored),
        completeness_score=Decimal(position.completeness_score),
        completeness_status=str(position.completeness_status),
        review_status=(
            str(review.review_status)  # type: ignore[arg-type]
            if review is not None
            else "not_started"
        ),
        review_revision=(
            int(review.revision) if review is not None else None
        ),
        review_updated_at=(
            _optional_utc(review.created_at) if review is not None else None
        ),
        group_fee_unallocated=(
            matching.get("execution_group_fee_scope")
            == _EXECUTION_GROUP_FEE_POLICY
        ),
    )


def position_episode_pnl_exclusion_reasons(
    item: PositionEpisodeListItem,
    opening_boundary_policy: Optional[str] = None,
) -> tuple[str, ...]:
    """Reasons this episode's P&L is conditional instead of verified.

    An empty result means the episode is ``pnl_summary_eligible``: closed,
    boundary-verified, not left-censored, evidence-complete, with a known
    net P&L and no group-scope fee retention.  The API projection and the
    review-insights aggregation both consume this single definition so the
    verified/conditional split can never drift between surfaces.
    """
    policy = opening_boundary_policy or getattr(
        item,
        "opening_boundary_policy",
        "unknown",
    )
    reasons: list[str] = []
    if not item.left_boundary_verified:
        reasons.append("boundary_unverified")
        if policy in {
            "assumed_flat_unverified",
            "mixed_explicit_and_assumed",
        }:
            reasons.append("assumed_flat_unverified")
    if item.lifecycle_status != "closed":
        reasons.append("open")
    if item.is_left_censored:
        reasons.append("left_censored")
    if item.completeness_status not in {"exact", "complete"}:
        reasons.append("incomplete")
    if item.realized_pnl_net is None:
        reasons.append("pnl_unavailable")
    if getattr(item, "group_fee_unallocated", False):
        reasons.append("group_fee_unallocated")
    return tuple(reasons)


def _position_episode_page_for_build(
    session: Any,
    build: EpisodeBuild,
    *,
    underlying: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    completeness_status: Optional[str] = None,
    case_focus: Optional[PositionEpisodeCaseFocus] = None,
    review_status: Optional[PositionEpisodeReviewStatus] = None,
    page: int = 1,
    per_page: int = 50,
) -> PositionEpisodePage:
    if page < 1:
        raise EpisodeRepositoryError("page must be at least 1")
    if per_page < 1 or per_page > 200:
        raise EpisodeRepositoryError("per_page must be between 1 and 200")
    filters = [PositionEpisode.episode_build_id == build.id]
    if underlying:
        filters.append(PositionEpisode.underlying == underlying.strip().upper())
    if lifecycle_status:
        filters.append(PositionEpisode.lifecycle_status == lifecycle_status)
    if completeness_status:
        filters.append(PositionEpisode.completeness_status == completeness_status)
    if case_focus not in _POSITION_EPISODE_CASE_FOCUSES | {None}:
        raise EpisodeRepositoryError(
            "case_focus must be one of: "
            + ", ".join(sorted(_POSITION_EPISODE_CASE_FOCUSES))
        )
    if case_focus in {"top_profit", "top_loss"}:
        filters.extend(
            (
                PositionEpisode.lifecycle_status == "closed",
                PositionEpisode.realized_pnl_net.is_not(None),
            )
        )
    if review_status not in _POSITION_EPISODE_REVIEW_STATUSES | {None}:
        raise EpisodeRepositoryError(
            "review_status must be one of: "
            + ", ".join(sorted(_POSITION_EPISODE_REVIEW_STATUSES))
        )

    latest_revisions = (
        select(
            ReviewAnnotation.position_episode_id.label("position_episode_id"),
            func.max(ReviewAnnotation.revision).label("revision"),
        )
        .where(
            ReviewAnnotation.account_key == build.account_key,
            ReviewAnnotation.episode_build_id == build.id,
        )
        .group_by(ReviewAnnotation.position_episode_id)
        .subquery()
    )
    latest_review = aliased(ReviewAnnotation, name="latest_review_annotation")
    latest_revision_join = (
        latest_revisions.c.position_episode_id == PositionEpisode.id
    )
    latest_review_join = and_(
        latest_review.account_key == build.account_key,
        latest_review.episode_build_id == build.id,
        latest_review.position_episode_id == PositionEpisode.id,
        latest_review.revision == latest_revisions.c.revision,
    )
    review_status_expression = func.coalesce(
        latest_review.review_status,
        "not_started",
    )

    queue_rows = session.execute(
        select(
            review_status_expression.label("review_status"),
            func.count(PositionEpisode.id),
        )
        .select_from(PositionEpisode)
        .outerjoin(latest_revisions, latest_revision_join)
        .outerjoin(latest_review, latest_review_join)
        .where(*filters)
        .group_by(review_status_expression)
    ).all()
    queue_values = {
        "not_started": 0,
        "in_progress": 0,
        "completed": 0,
    }
    for status_value, count_value in queue_rows:
        normalized_status = str(status_value)
        if normalized_status in queue_values:
            queue_values[normalized_status] = int(count_value)

    filtered = list(filters)
    if review_status is not None:
        filtered.append(review_status_expression == review_status)

    order_by = {
        "top_profit": (
            PositionEpisode.realized_pnl_net.desc(),
            PositionEpisode.opened_at.desc(),
            PositionEpisode.id.desc(),
        ),
        "top_loss": (
            PositionEpisode.realized_pnl_net.asc(),
            PositionEpisode.opened_at.desc(),
            PositionEpisode.id.desc(),
        ),
        "largest_fee": (
            PositionEpisode.total_fee.desc().nulls_last(),
            PositionEpisode.opened_at.desc(),
            PositionEpisode.id.desc(),
        ),
        "longest_hold": (
            PositionEpisode.hold_seconds.desc().nulls_last(),
            PositionEpisode.opened_at.desc(),
            PositionEpisode.id.desc(),
        ),
        "weakest_evidence": (
            PositionEpisode.completeness_score.asc(),
            PositionEpisode.opened_at.desc(),
            PositionEpisode.id.desc(),
        ),
        None: (
            PositionEpisode.opened_at.desc(),
            PositionEpisode.id.desc(),
        ),
    }[case_focus]
    total = int(
        session.execute(
            select(func.count(PositionEpisode.id))
            .select_from(PositionEpisode)
            .outerjoin(latest_revisions, latest_revision_join)
            .outerjoin(latest_review, latest_review_join)
            .where(*filtered)
        ).scalar_one()
    )
    rows = session.execute(
        select(PositionEpisode, StrategyEpisode, latest_review)
        .join(
            StrategyEpisode,
            StrategyEpisode.id == PositionEpisode.strategy_episode_id,
        )
        .outerjoin(latest_revisions, latest_revision_join)
        .outerjoin(latest_review, latest_review_join)
        .where(*filtered)
        .order_by(*order_by)
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return PositionEpisodePage(
        build_id=int(build.id),
        items=tuple(
            _position_item(position, strategy, review)
            for position, strategy, review in rows
        ),
        total=total,
        page=page,
        per_page=per_page,
        review_queue=PositionEpisodeReviewQueueCounts(
            pending=queue_values["not_started"],
            in_progress=queue_values["in_progress"],
            completed=queue_values["completed"],
        ),
    )


def get_position_episode_page(
    build_id: int,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    **kwargs: Any,
) -> PositionEpisodePage:
    """List episodes from one explicitly selected immutable build."""
    if build_id <= 0:
        raise EpisodeRepositoryError("build_id must be positive")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = session.execute(
            select(EpisodeBuild).where(
                EpisodeBuild.id == build_id,
                EpisodeBuild.account_key == account_key,
            )
        ).scalar_one_or_none()
        if build is None:
            page = int(kwargs.get("page", 1))
            per_page = int(kwargs.get("per_page", 50))
            return PositionEpisodePage(None, (), 0, page, per_page)
        return _position_episode_page_for_build(session, build, **kwargs)


def get_latest_position_episode_page(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    **kwargs: Any,
) -> PositionEpisodePage:
    """List the activated canonical build, or the newest CSV fallback."""
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, account_key)
        if build is None:
            page = int(kwargs.get("page", 1))
            per_page = int(kwargs.get("per_page", 50))
            return PositionEpisodePage(None, (), 0, page, per_page)
        return _position_episode_page_for_build(session, build, **kwargs)


def list_latest_position_episodes(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    **kwargs: Any,
) -> PositionEpisodePage:
    """Alias retaining explicit latest-build list semantics."""
    return get_latest_position_episode_page(account_key, **kwargs)


def _position_episode_detail_for_build(
    session: Any,
    build: EpisodeBuild,
    *,
    episode_id: int,
    account_key: str,
) -> Optional[PositionEpisodeDetail]:
    row = session.execute(
        select(PositionEpisode, StrategyEpisode)
        .join(
            StrategyEpisode,
            StrategyEpisode.id == PositionEpisode.strategy_episode_id,
        )
        .where(
            PositionEpisode.id == episode_id,
            PositionEpisode.episode_build_id == build.id,
            PositionEpisode.account_key == account_key,
            StrategyEpisode.episode_build_id == build.id,
            StrategyEpisode.account_key == account_key,
        )
    ).one_or_none()
    if row is None:
        return None
    position, strategy = row
    latest_review = session.execute(
        select(ReviewAnnotation)
        .where(
            ReviewAnnotation.account_key == account_key,
            ReviewAnnotation.episode_build_id == build.id,
            ReviewAnnotation.position_episode_id == position.id,
        )
        .order_by(
            ReviewAnnotation.revision.desc(),
            ReviewAnnotation.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    allocations = session.execute(
        select(PositionEpisodeEvidence)
        .where(
            PositionEpisodeEvidence.episode_build_id == build.id,
            PositionEpisodeEvidence.position_episode_id == position.id,
        )
        .order_by(
            PositionEpisodeEvidence.allocation_sequence,
            PositionEpisodeEvidence.id,
        )
    ).scalars()
    allocation_rows = list(allocations)
    fill_ids = {
        int(item.broker_fill_observation_id)
        for item in allocation_rows
        if item.broker_fill_observation_id is not None
    }
    fill_parent_order_ids: dict[int, Optional[int]] = {}
    if fill_ids:
        fill_parent_order_ids = {
            int(fill_id): (
                int(parent_order_id) if parent_order_id is not None else None
            )
            for fill_id, parent_order_id in session.execute(
                select(
                    BrokerFillObservation.id,
                    BrokerFillObservation.broker_order_observation_id,
                ).where(BrokerFillObservation.id.in_(fill_ids))
            )
        }
    evidence = tuple(
        PositionEpisodeEvidenceItem(
            evidence_id=int(item.id),
            evidence_key=str(item.evidence_key),
            evidence_kind=str(item.evidence_kind),
            event_role=str(item.event_role),
            allocation_sequence=int(item.allocation_sequence),
            evidence_time=_utc(item.evidence_time, field_name="evidence_time"),
            allocated_quantity=_decimal(item.allocated_quantity),
            allocated_fee=_decimal(item.allocated_fee),
            allocated_cash_flow=_decimal(item.allocated_cash_flow),
            allocation_ratio=_decimal(item.allocation_ratio),
            broker_order_observation_id=(
                int(item.broker_order_observation_id)
                if item.broker_order_observation_id is not None
                else None
            ),
            broker_fill_observation_id=(
                int(item.broker_fill_observation_id)
                if item.broker_fill_observation_id is not None
                else None
            ),
            parent_broker_order_observation_id=(
                fill_parent_order_ids.get(int(item.broker_fill_observation_id))
                if item.broker_fill_observation_id is not None
                else None
            ),
            allocation_evidence=_parse_json(item.allocation_evidence_json),
            provenance=_parse_json(item.provenance_json),
        )
        for item in allocation_rows
    )
    return PositionEpisodeDetail(
        episode=_position_item(position, strategy, latest_review),
        matching_evidence=_parse_json(position.matching_evidence_json),
        evidence_summary=_parse_json(position.evidence_summary_json),
        completeness=_parse_json(position.completeness_json),
        provenance=_parse_json(position.provenance_json),
        evidence=evidence,
    )


def get_latest_position_episode_detail(
    episode_id: int,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> Optional[PositionEpisodeDetail]:
    """Return one episode only if it belongs to this account's newest build."""
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, account_key)
        if build is None:
            return None
        return _position_episode_detail_for_build(
            session,
            build,
            episode_id=episode_id,
            account_key=account_key,
        )


def get_position_episode_detail(
    episode_id: int,
    build_id: int,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> Optional[PositionEpisodeDetail]:
    """Return one episode from an explicitly selected immutable build."""
    if episode_id <= 0:
        raise EpisodeRepositoryError("episode_id must be positive")
    if build_id <= 0:
        raise EpisodeRepositoryError("build_id must be positive")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = session.execute(
            select(EpisodeBuild).where(
                EpisodeBuild.id == build_id,
                EpisodeBuild.account_key == account_key,
            )
        ).scalar_one_or_none()
        if build is None:
            return None
        return _position_episode_detail_for_build(
            session,
            build,
            episode_id=episode_id,
            account_key=account_key,
        )
