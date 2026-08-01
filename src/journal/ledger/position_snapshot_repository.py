# -*- coding: utf-8 -*-
"""Append-only repository for current-only Moomoo option-position evidence.

The repository is intentionally isolated from canonical execution evidence and
PositionEpisode builds.  A confirmed snapshot is a locally bracketed current
observation.  It can become a future continuity anchor only after a separate
boundary/fence policy is implemented; it never proves a historical opening.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import inspect, select

import src.journal.ledger.models  # noqa: F401 - register refresh FK targets
from src.journal.ledger.position_snapshot_models import (
    ConfirmedPositionSnapshot,
    PositionSnapshotArtifact,
    PositionSnapshotMember,
    position_snapshot_guard_trigger_ddl,
)
from src.journal.ledger.refresh_models import JournalRefreshPublication
from src.options.occ_parser import parse_symbol
from src.storage import Base, get_db

__all__ = [
    "DEFAULT_POSITION_SNAPSHOT_ARTIFACT_TTL_MINUTES",
    "MAX_POSITION_SNAPSHOT_ACQUISITION_DURATION",
    "MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS",
    "MAX_POSITION_SNAPSHOT_FUTURE_SKEW",
    "POSITION_SNAPSHOT_TIME_SEMANTICS",
    "PositionSnapshotArtifactRecord",
    "PositionSnapshotConfirmation",
    "PositionSnapshotError",
    "PositionSnapshotMemberRecord",
    "PositionSnapshotLatestState",
    "PositionSnapshotPlan",
    "PositionSnapshotPublicationRecord",
    "PositionSnapshotRecord",
    "confirm_position_snapshot_artifact",
    "get_latest_position_snapshot",
    "get_latest_position_snapshot_state",
    "get_position_snapshot_artifact",
    "get_position_snapshot_detail",
    "init_position_snapshot_schema",
    "list_position_snapshots",
    "load_latest_position_snapshot_in_session",
    "plan_position_snapshot",
    "save_position_snapshot_artifact",
]


POSITION_SNAPSHOT_SCHEMA = "dsa.moomoo.position-snapshot.v1"
POSITION_SNAPSHOT_PARSER_NAME = "moomoo-position-snapshot"
POSITION_SNAPSHOT_PARSER_VERSION = "1.1.0"
POSITION_SNAPSHOT_BINDING_SCHEME = "dsa-journal-account-binding-v1"
DEFAULT_POSITION_SNAPSHOT_ARTIFACT_TTL_MINUTES = 30
MAX_POSITION_SNAPSHOT_FUTURE_SKEW = timedelta(minutes=2)
MAX_POSITION_SNAPSHOT_ACQUISITION_DURATION = timedelta(minutes=5)
MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS = timedelta(minutes=30)
_COST_CONTEXT_SEMANTICS = "broker_display_only_not_realized_pnl"
POSITION_SNAPSHOT_TIME_SEMANTICS = (
    "locally_bracketed_acquisition_interval; broker_as_of_not_provided"
)
_TIME_SEMANTICS = POSITION_SNAPSHOT_TIME_SEMANTICS
_STABILITY_COMPARISON_BASIS = (
    "instrument_position_side_signed_quantity_contracts"
)
_TEMPORAL_POLICY = {
    "max_future_skew_seconds": int(
        MAX_POSITION_SNAPSHOT_FUTURE_SKEW.total_seconds()
    ),
    "max_acquisition_duration_seconds": int(
        MAX_POSITION_SNAPSHOT_ACQUISITION_DURATION.total_seconds()
    ),
    "max_artifact_freshness_seconds": int(
        MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS.total_seconds()
    ),
}
_IMMUTABLE_TABLES = (
    PositionSnapshotArtifact.__tablename__,
    ConfirmedPositionSnapshot.__tablename__,
    PositionSnapshotMember.__tablename__,
)
_SCHEMA_LOCK = threading.Lock()


class PositionSnapshotError(ValueError):
    """Raised when a current-position artifact is malformed or unsafe."""


@dataclass(frozen=True)
class PositionSnapshotMemberRecord:
    symbol: str
    asset_type: str
    underlying: str
    expiry: date
    strike: str
    option_right: str
    currency: str
    position_side: str
    quantity_contracts: str
    signed_quantity_contracts: str
    can_sell_quantity_contracts: Optional[str]
    contract_multiplier: Optional[str]
    contract_multiplier_basis: str
    cost_price: Optional[str]
    cost_price_valid: Optional[bool]
    average_cost: Optional[str]
    diluted_cost: Optional[str]
    cost_context_semantics: str
    source_position_sha256: str
    source_contract_spec_sha256: Optional[str]


@dataclass(frozen=True)
class PositionSnapshotPlan:
    account_key: str
    account_binding_id: str
    expected_account_binding_id: Optional[str]
    account_selection: str
    query_requested_at: datetime
    query_started_at: datetime
    query_completed_at: datetime
    operation_completed_at: datetime
    broker_as_of_at: Optional[datetime]
    retrieval_complete: bool
    position_snapshot_complete: bool
    stability_status: str
    stability: Mapping[str, Any]
    contract_spec_status: str
    analysis_ready: bool
    content_state: str
    position_count: int
    contract_spec_count: int
    total_contracts: str
    counts: Mapping[str, int]
    warnings: tuple[str, ...]
    validation_issues: tuple[str, ...]
    members: tuple[PositionSnapshotMemberRecord, ...]
    artifact_key: str
    preview_key: str
    scope_sha256: str
    source_sha256: str
    evidence_sha256: str
    snapshot_sha256: str
    stability_evidence_sha256: str
    member_set_sha256: str
    temporal_policy: Mapping[str, int]
    refresh_publication_id: Optional[int]
    refresh_publication_key: Optional[str]
    account_continuity_sha256: Optional[str]
    confirm_allowed: bool
    blocking_reasons: tuple[str, ...]
    future_anchor_candidate: bool
    historical_opening_proven: bool = False


@dataclass(frozen=True)
class PositionSnapshotArtifactRecord:
    artifact_id: int
    artifact_key: str
    account_key: str
    account_binding_id: str
    refresh_publication_id: Optional[int]
    refresh_publication_key: Optional[str]
    account_continuity_sha256: Optional[str]
    environment: str
    market: str
    query_started_at: datetime
    query_completed_at: datetime
    operation_completed_at: datetime
    broker_as_of_at: Optional[datetime]
    scope_sha256: str
    source_sha256: str
    evidence_sha256: str
    snapshot_sha256: str
    stability_evidence_sha256: str
    preview_key: str
    retrieval_complete: bool
    position_snapshot_complete: bool
    stability_status: str
    contract_spec_status: str
    position_count: int
    contract_spec_count: int
    confirm_allowed: bool
    expires_at: datetime
    recorded_at: datetime
    payload: Mapping[str, Any]
    historical_opening_proven: bool = False


@dataclass(frozen=True)
class PositionSnapshotRecord:
    snapshot_id: int
    snapshot_key: str
    artifact_id: int
    account_key: str
    account_binding_id: str
    refresh_publication_id: int
    refresh_publication_key: str
    account_continuity_sha256: str
    query_started_at: datetime
    query_completed_at: datetime
    operation_completed_at: datetime
    broker_as_of_at: Optional[datetime]
    scope_sha256: str
    source_sha256: str
    evidence_sha256: str
    snapshot_sha256: str
    stability_evidence_sha256: str
    member_set_sha256: str
    provenance_sha256: str
    acknowledged_future_only: bool
    historical_opening_proven: bool
    cost_context_only: bool
    recorded_at: datetime
    retrieval_complete: bool
    position_snapshot_complete: bool
    stability_status: str
    stability: Mapping[str, Any]
    contract_spec_status: str
    analysis_ready: bool
    content_state: str
    total_contracts: str
    counts: Mapping[str, int]
    warnings: tuple[str, ...]
    validation_issues: tuple[str, ...]
    members: tuple[PositionSnapshotMemberRecord, ...]

    @property
    def future_anchor_candidate(self) -> bool:
        return bool(
            self.acknowledged_future_only
            and self.retrieval_complete
            and self.position_snapshot_complete
            and self.stability_status == "stable"
            and self.analysis_ready
            and not self.historical_opening_proven
        )


@dataclass(frozen=True)
class PositionSnapshotConfirmation:
    artifact: PositionSnapshotArtifactRecord
    snapshot: PositionSnapshotRecord
    duplicate: bool


@dataclass(frozen=True)
class PositionSnapshotPublicationRecord:
    publication_id: int
    publication_key: str
    account_binding: str
    recorded_at: datetime


@dataclass(frozen=True)
class PositionSnapshotLatestState:
    """Snapshot and continuity authority read from one DB transaction."""

    snapshot: Optional[PositionSnapshotRecord]
    publication: Optional[PositionSnapshotPublicationRecord]


def _utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PositionSnapshotError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _db_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PositionSnapshotError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PositionSnapshotError(f"{field_name} is invalid") from exc
    return _utc(parsed, field_name=field_name)


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PositionSnapshotError(f"{field_name} must be an object")
    return value


def _sequence(value: Any, *, field_name: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise PositionSnapshotError(f"{field_name} must be a list")
    return value


def _text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PositionSnapshotError(f"{field_name} must be non-empty text")
    return value.strip()


def _bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PositionSnapshotError(f"{field_name} must be boolean")
    return value


def _integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PositionSnapshotError(f"{field_name} must be a non-negative integer")
    return value


def _canonical_json(value: Any) -> str:
    def _default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return _decimal_output(item)
        if isinstance(item, datetime):
            return _utc(item, field_name="datetime").isoformat()
        if isinstance(item, date):
            return item.isoformat()
        raise TypeError(f"unsupported position snapshot JSON type: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_default,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash(value: Any, *, field_name: str) -> str:
    text = _text(value, field_name=field_name).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise PositionSnapshotError(f"{field_name} must be a lowercase SHA-256")
    return text


def _decimal_output(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _decimal(
    value: Any,
    *,
    field_name: str,
    positive: bool = False,
    non_negative: bool = False,
    optional: bool = False,
) -> Optional[Decimal]:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise PositionSnapshotError(f"{field_name} must be a decimal string")
    text = value.strip()
    if not text:
        raise PositionSnapshotError(f"{field_name} must be a decimal string")
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise PositionSnapshotError(f"{field_name} is invalid") from exc
    if not parsed.is_finite():
        raise PositionSnapshotError(f"{field_name} must be finite")
    if positive and parsed <= 0:
        raise PositionSnapshotError(f"{field_name} must be positive")
    if non_negative and parsed < 0:
        raise PositionSnapshotError(f"{field_name} cannot be negative")
    if _decimal_output(parsed) != text:
        raise PositionSnapshotError(
            f"{field_name} must use canonical decimal spelling"
        )
    return parsed


def _string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    rows = _sequence(value, field_name=field_name)
    result = tuple(_text(item, field_name=field_name) for item in rows)
    if len(set(result)) != len(result):
        raise PositionSnapshotError(f"{field_name} contains duplicates")
    return result


def _normalize_binding(value: Optional[str], *, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _hash(value, field_name=field_name)


def _parse_contract_specs(
    rows: Sequence[Any],
) -> dict[str, Mapping[str, Any]]:
    specs: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _mapping(raw, field_name=f"records.contract_specs[{index}]")
        symbol = _text(row.get("symbol"), field_name="contract_spec.symbol").upper()
        if symbol in specs:
            raise PositionSnapshotError("contract specs contain a duplicate symbol")
        values = [
            _decimal(row.get(name), field_name=f"contract_spec.{name}", positive=True)
            for name in ("lot_size", "option_contract_size", "option_contract_multiplier")
        ]
        assert all(item is not None for item in values)
        resolved = _decimal(
            row.get("resolved_multiplier"),
            field_name="contract_spec.resolved_multiplier",
            positive=True,
        )
        if len(set(values)) != 1 or resolved != values[0]:
            raise PositionSnapshotError("contract spec multiplier fields disagree")
        if _text(row.get("multiplier_basis"), field_name="contract_spec.multiplier_basis") != (
            "moomoo_market_snapshot_three_field_consensus"
        ):
            raise PositionSnapshotError("contract spec multiplier basis is unsupported")
        source_hash = _hash(
            row.get("source_record_sha256"),
            field_name="contract_spec.source_record_sha256",
        )
        unhashed = dict(row)
        unhashed.pop("source_record_sha256", None)
        if _sha256_json(unhashed) != source_hash:
            raise PositionSnapshotError("contract spec source hash does not match")
        specs[symbol] = row
    return specs


def _parse_member(
    raw: Any,
    *,
    index: int,
    specs: Mapping[str, Mapping[str, Any]],
) -> PositionSnapshotMemberRecord:
    row = _mapping(raw, field_name=f"records.positions[{index}]")
    symbol = _text(row.get("symbol"), field_name="position.symbol").upper()
    if _text(row.get("market"), field_name="position.market").upper() != "US":
        raise PositionSnapshotError("position market must be US")
    if _text(row.get("asset_type"), field_name="position.asset_type").lower() != "option":
        raise PositionSnapshotError("position asset type must be option")
    try:
        instrument = parse_symbol(symbol.split(".", 1)[1] if symbol.startswith("US.") else symbol)
    except (ValueError, IndexError) as exc:
        raise PositionSnapshotError("position symbol is not a valid US option") from exc
    if not symbol.startswith("US.") or not instrument.is_option or instrument.option is None:
        raise PositionSnapshotError("position symbol is not a valid US option")
    option = instrument.option
    underlying = _text(row.get("underlying"), field_name="position.underlying").upper()
    expiry_text = _text(row.get("expiry"), field_name="position.expiry")
    try:
        expiry = date.fromisoformat(expiry_text)
    except ValueError as exc:
        raise PositionSnapshotError("position expiry is invalid") from exc
    strike = _decimal(row.get("strike"), field_name="position.strike", positive=True)
    assert strike is not None
    right = _text(row.get("option_right"), field_name="position.option_right").upper()
    if (
        underlying != option.underlying
        or expiry != option.expiry
        or strike != Decimal(str(option.strike))
        or right != option.right
    ):
        raise PositionSnapshotError("position OCC identity fields disagree")
    side = _text(row.get("position_side"), field_name="position.position_side").upper()
    if side not in {"LONG", "SHORT"}:
        raise PositionSnapshotError("position side must be LONG or SHORT")
    quantity = _decimal(
        row.get("quantity_contracts"),
        field_name="position.quantity_contracts",
        positive=True,
    )
    signed = _decimal(
        row.get("signed_quantity_contracts"),
        field_name="position.signed_quantity_contracts",
    )
    assert quantity is not None and signed is not None
    expected_signed = quantity if side == "LONG" else -quantity
    if signed != expected_signed:
        raise PositionSnapshotError("signed position quantity disagrees with side")
    can_sell = _decimal(
        row.get("can_sell_quantity_contracts"),
        field_name="position.can_sell_quantity_contracts",
        non_negative=True,
        optional=True,
    )
    currency = _text(row.get("currency"), field_name="position.currency").upper()
    if currency != "USD":
        raise PositionSnapshotError("position currency must be USD")

    spec = specs.get(symbol)
    multiplier_value = row.get("contract_multiplier")
    multiplier = _decimal(
        multiplier_value,
        field_name="position.contract_multiplier",
        positive=True,
        optional=True,
    )
    basis = _text(
        row.get("contract_multiplier_basis"),
        field_name="position.contract_multiplier_basis",
    )
    spec_hash_value = row.get("contract_spec_source_record_sha256")
    spec_hash = (
        None
        if spec_hash_value is None
        else _hash(spec_hash_value, field_name="position.contract_spec_source_record_sha256")
    )
    if spec is None:
        if multiplier is not None or basis != "unavailable" or spec_hash is not None:
            raise PositionSnapshotError("position contract spec linkage is inconsistent")
    else:
        expected_multiplier = _decimal(
            spec.get("resolved_multiplier"),
            field_name="contract_spec.resolved_multiplier",
            positive=True,
        )
        expected_spec_hash = _hash(
            spec.get("source_record_sha256"),
            field_name="contract_spec.source_record_sha256",
        )
        if multiplier != expected_multiplier or spec_hash != expected_spec_hash:
            raise PositionSnapshotError("position multiplier does not match its contract spec")

    context = _mapping(row.get("broker_cost_context"), field_name="position.broker_cost_context")
    semantics = _text(context.get("semantics"), field_name="position.cost_context.semantics")
    if semantics != _COST_CONTEXT_SEMANTICS:
        raise PositionSnapshotError("position cost context semantics are unsafe")
    cost_values: dict[str, Optional[str]] = {}
    for name in ("cost_price", "average_cost", "diluted_cost"):
        parsed = _decimal(
            context.get(name),
            field_name=f"position.cost_context.{name}",
            optional=True,
        )
        cost_values[name] = None if parsed is None else _decimal_output(parsed)
    cost_price_valid = context.get("cost_price_valid")
    if cost_price_valid is not None and not isinstance(cost_price_valid, bool):
        raise PositionSnapshotError("position.cost_context.cost_price_valid must be boolean or null")

    return PositionSnapshotMemberRecord(
        symbol=symbol,
        asset_type="option",
        underlying=underlying,
        expiry=expiry,
        strike=_decimal_output(strike),
        option_right=right,
        currency=currency,
        position_side=side,
        quantity_contracts=_decimal_output(quantity),
        signed_quantity_contracts=_decimal_output(signed),
        can_sell_quantity_contracts=None if can_sell is None else _decimal_output(can_sell),
        contract_multiplier=None if multiplier is None else _decimal_output(multiplier),
        contract_multiplier_basis=basis,
        cost_price=cost_values["cost_price"],
        cost_price_valid=cost_price_valid,
        average_cost=cost_values["average_cost"],
        diluted_cost=cost_values["diluted_cost"],
        cost_context_semantics=semantics,
        source_position_sha256=_sha256_json(row),
        source_contract_spec_sha256=spec_hash,
    )


def _member_hash_payload(member: PositionSnapshotMemberRecord) -> Mapping[str, Any]:
    return {
        "symbol": member.symbol,
        "underlying": member.underlying,
        "expiry": member.expiry.isoformat(),
        "strike": member.strike,
        "option_right": member.option_right,
        "currency": member.currency,
        "position_side": member.position_side,
        "quantity_contracts": member.quantity_contracts,
        "signed_quantity_contracts": member.signed_quantity_contracts,
        "can_sell_quantity_contracts": member.can_sell_quantity_contracts,
        "contract_multiplier": member.contract_multiplier,
        "contract_multiplier_basis": member.contract_multiplier_basis,
        "cost_context": {
            "cost_price": member.cost_price,
            "cost_price_valid": member.cost_price_valid,
            "average_cost": member.average_cost,
            "diluted_cost": member.diluted_cost,
            "semantics": member.cost_context_semantics,
        },
        "source_position_sha256": member.source_position_sha256,
        "source_contract_spec_sha256": member.source_contract_spec_sha256,
    }


def _instrument_key(member: PositionSnapshotMemberRecord) -> str:
    return _sha256_json(
        {
            "market": "US",
            "asset_type": "option",
            "underlying": member.underlying,
            "expiry": member.expiry,
            "strike": member.strike,
            "option_right": member.option_right,
            "currency": member.currency,
        }
    )


def _member_key(
    *,
    snapshot_key: str,
    instrument_key: str,
    source_position_sha256: str,
) -> str:
    return _sha256_json(
        {
            "snapshot_key": snapshot_key,
            "instrument_key": instrument_key,
            "source_position_sha256": source_position_sha256,
        }
    )


def _parse_boundary_rows(
    value: Any,
    *,
    field_name: str,
) -> tuple[dict[str, str], ...]:
    """Validate canonical double-read rows used to prove stability."""
    raw_rows = _sequence(value, field_name=field_name)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        row = _mapping(raw, field_name=f"{field_name}[{index}]")
        if set(row) != {
            "symbol",
            "position_side",
            "signed_quantity_contracts",
        }:
            raise PositionSnapshotError(
                f"{field_name}[{index}] has unsupported fields"
            )
        symbol = _text(
            row.get("symbol"),
            field_name=f"{field_name}[{index}].symbol",
        ).upper()
        if symbol in seen:
            raise PositionSnapshotError(f"{field_name} contains duplicate symbols")
        try:
            parsed_symbol = parse_symbol(symbol.split(".", 1)[1])
        except (ValueError, IndexError) as exc:
            raise PositionSnapshotError(
                f"{field_name} contains an invalid US option symbol"
            ) from exc
        if (
            not symbol.startswith("US.")
            or not parsed_symbol.is_option
            or parsed_symbol.option is None
        ):
            raise PositionSnapshotError(
                f"{field_name} contains an invalid US option symbol"
            )
        side = _text(
            row.get("position_side"),
            field_name=f"{field_name}[{index}].position_side",
        ).upper()
        if side not in {"LONG", "SHORT"}:
            raise PositionSnapshotError(
                f"{field_name} contains an invalid position side"
            )
        signed = _decimal(
            row.get("signed_quantity_contracts"),
            field_name=(
                f"{field_name}[{index}].signed_quantity_contracts"
            ),
        )
        assert signed is not None
        if signed == 0 or (side == "LONG") != (signed > 0):
            raise PositionSnapshotError(
                f"{field_name} signed quantity disagrees with side"
            )
        seen.add(symbol)
        result.append(
            {
                "symbol": symbol,
                "position_side": side,
                "signed_quantity_contracts": _decimal_output(signed),
            }
        )
    if tuple(row["symbol"] for row in result) != tuple(sorted(seen)):
        raise PositionSnapshotError(f"{field_name} must be sorted by symbol")
    return tuple(result)


def _derive_stability(
    first_rows: Sequence[Mapping[str, str]],
    second_rows: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    first = {row["symbol"]: row for row in first_rows}
    second = {row["symbol"]: row for row in second_rows}
    first_symbols = set(first)
    second_symbols = set(second)
    added = sorted(second_symbols - first_symbols)
    removed = sorted(first_symbols - second_symbols)
    changed = sorted(
        symbol
        for symbol in first_symbols & second_symbols
        if first[symbol] != second[symbol]
    )
    return {
        "stable": not (added or removed or changed),
        "comparison_basis": _STABILITY_COMPARISON_BASIS,
        "added_symbols": added,
        "removed_symbols": removed,
        "changed_symbols": changed,
    }


def _temporal_policy() -> dict[str, int]:
    return dict(_TEMPORAL_POLICY)


def _latest_refresh_publication_row(
    session: Any,
    *,
    account_key: str,
) -> Optional[JournalRefreshPublication]:
    return session.execute(
        select(JournalRefreshPublication)
        .where(
            JournalRefreshPublication.broker == "moomoo",
            JournalRefreshPublication.account_key == account_key,
        )
        .order_by(
            JournalRefreshPublication.recorded_at.desc(),
            JournalRefreshPublication.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def _publication_key(row: JournalRefreshPublication) -> str:
    value = _text(
        row.publication_key,
        field_name="refresh publication key",
    )
    if len(value) > 128:
        raise PositionSnapshotError("refresh publication key is too long")
    return value


def _publication_provenance(
    row: JournalRefreshPublication,
) -> dict[str, Any]:
    return {
        "kind": "journal_refresh_publication_anchor_v1",
        "publication_id": int(row.id),
        "publication_key": _publication_key(row),
        "refresh_artifact_id": int(row.refresh_artifact_id),
        "import_batch_id": int(row.import_batch_id),
        "canonical_set_id": int(row.canonical_set_id),
        "broker": str(row.broker),
        "account_key": str(row.account_key),
        "account_binding_id": _hash(
            row.account_binding,
            field_name="refresh publication account binding",
        ),
        "broker_queried_through": _db_utc(
            row.broker_queried_through
        ),
        "latest_fill_at": (
            None
            if row.latest_fill_at is None
            else _db_utc(row.latest_fill_at)
        ),
        "evidence_published_through": _db_utc(
            row.evidence_published_through
        ),
        "recorded_at": _db_utc(row.recorded_at),
    }


def _continuity_sha256(row: JournalRefreshPublication) -> str:
    return _sha256_json(_publication_provenance(row))


def _bind_plan_to_publication(
    plan: PositionSnapshotPlan,
    publication: Optional[JournalRefreshPublication],
) -> PositionSnapshotPlan:
    publication_id: Optional[int] = None
    publication_key: Optional[str] = None
    continuity_sha256: Optional[str] = None
    if publication is not None:
        publication_id = int(publication.id)
        publication_key = _publication_key(publication)
        continuity_sha256 = _continuity_sha256(publication)
        if str(publication.broker) != "moomoo":
            raise PositionSnapshotError("refresh publication broker is inconsistent")
        if str(publication.account_key) != plan.account_key:
            raise PositionSnapshotError("refresh publication account is inconsistent")
        if _hash(
            publication.account_binding,
            field_name="refresh publication account binding",
        ) != plan.account_binding_id:
            raise PositionSnapshotError(
                "Moomoo account binding differs from the latest confirmed refresh"
            )

    artifact_key = _sha256_json(
        {
            "kind": "journal_position_snapshot_artifact_v2",
            "base_artifact_key": plan.artifact_key,
            "refresh_publication_id": publication_id,
            "refresh_publication_key": publication_key,
            "account_continuity_sha256": continuity_sha256,
            "temporal_policy": plan.temporal_policy,
        }
    )
    preview_key = _sha256_json(
        {
            "kind": "journal_position_snapshot_preview_v2",
            "artifact_key": artifact_key,
            "base_preview_key": plan.preview_key,
            "refresh_publication_id": publication_id,
            "refresh_publication_key": publication_key,
            "account_continuity_sha256": continuity_sha256,
            "confirm_allowed": plan.confirm_allowed,
            "blocking_reasons": plan.blocking_reasons,
            "temporal_policy": plan.temporal_policy,
        }
    )
    return replace(
        plan,
        artifact_key=artifact_key,
        preview_key=preview_key,
        refresh_publication_id=publication_id,
        refresh_publication_key=publication_key,
        account_continuity_sha256=continuity_sha256,
    )


def _enforce_temporal_freshness(
    plan: PositionSnapshotPlan,
    *,
    now: datetime,
) -> None:
    checked_at = _utc(now, field_name="position snapshot policy clock")
    if (
        plan.query_requested_at > checked_at + MAX_POSITION_SNAPSHOT_FUTURE_SKEW
        or plan.operation_completed_at
        > checked_at + MAX_POSITION_SNAPSHOT_FUTURE_SKEW
    ):
        raise PositionSnapshotError(
            "position snapshot timestamps are too far in the future"
        )
    if (
        checked_at - plan.operation_completed_at
        > MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS
    ):
        raise PositionSnapshotError(
            "position snapshot acquisition is stale; run a new read-only check"
        )


def plan_position_snapshot(
    payload: Mapping[str, Any],
    *,
    account_key: str,
    expected_account_binding_id: Optional[str],
) -> PositionSnapshotPlan:
    """Validate a de-identified probe payload and produce a zero-write plan."""
    account_key = _text(account_key, field_name="account_key")
    root = _mapping(payload, field_name="payload")
    if root.get("schema") != POSITION_SNAPSHOT_SCHEMA:
        raise PositionSnapshotError("unsupported current-position snapshot schema")
    if root.get("mode") != "read_only":
        raise PositionSnapshotError("position snapshot mode must be read_only")
    if root.get("journal_database_written") is not False:
        raise PositionSnapshotError("probe payload violates zero-write contract")
    if root.get("trading_actions_performed") is not False:
        raise PositionSnapshotError("probe payload violates no-trade contract")

    account = _mapping(root.get("account"), field_name="account")
    if account.get("environment") != "LIVE" or account.get("market") != "US":
        raise PositionSnapshotError("position snapshot scope must be LIVE US")
    binding = _hash(account.get("binding"), field_name="account.binding")
    expected_binding = _normalize_binding(
        expected_account_binding_id,
        field_name="expected_account_binding_id",
    )
    account_selection = _text(account.get("selection"), field_name="account.selection")
    if account_selection not in {"explicit", "unique_auto"}:
        raise PositionSnapshotError("account selection mode is unsupported")

    acquisition = _mapping(root.get("acquisition"), field_name="acquisition")
    requested_at = _parse_time(acquisition.get("requested_at"), field_name="acquisition.requested_at")
    first = _mapping(acquisition.get("first_read"), field_name="acquisition.first_read")
    second = _mapping(acquisition.get("second_read"), field_name="acquisition.second_read")
    first_started_at = _parse_time(first.get("started_at"), field_name="first_read.started_at")
    first_completed_at = _parse_time(first.get("completed_at"), field_name="first_read.completed_at")
    second_started_at = _parse_time(second.get("started_at"), field_name="second_read.started_at")
    second_completed_at = _parse_time(second.get("completed_at"), field_name="second_read.completed_at")
    operation_completed_at = _parse_time(acquisition.get("completed_at"), field_name="acquisition.completed_at")
    generated_at = _parse_time(root.get("generated_at"), field_name="generated_at")
    if acquisition.get("broker_as_of") is not None:
        raise PositionSnapshotError("broker_as_of must remain null when Moomoo does not provide it")
    if acquisition.get("time_semantics") != _TIME_SEMANTICS:
        raise PositionSnapshotError("position snapshot time semantics are unsupported")
    if not (
        requested_at <= first_started_at <= first_completed_at
        <= second_started_at <= second_completed_at <= operation_completed_at
    ):
        raise PositionSnapshotError("position snapshot acquisition times are out of order")
    if second_completed_at - first_started_at > MAX_POSITION_SNAPSHOT_ACQUISITION_DURATION:
        raise PositionSnapshotError(
            "position snapshot acquisition exceeded the maximum duration"
        )
    if generated_at != operation_completed_at:
        raise PositionSnapshotError("generated_at must equal operation completion")
    boundary_rows: dict[str, tuple[dict[str, str], ...]] = {}
    read_counts: dict[str, dict[str, int]] = {}
    for read, name in ((first, "first_read"), (second, "second_read")):
        rows = _parse_boundary_rows(
            read.get("boundary_rows"),
            field_name=f"{name}.boundary_rows",
        )
        boundary_rows[name] = rows
        boundary_sha256 = _hash(
            read.get("boundary_sha256"),
            field_name=f"{name}.boundary_sha256",
        )
        if _sha256_json(list(rows)) != boundary_sha256:
            raise PositionSnapshotError(f"{name} boundary hash does not match")
        names = (
            "total_rows",
            "recognized_option_positions",
            "filtered_non_us_rows",
            "filtered_non_option_rows",
            "filtered_zero_quantity_rows",
        )
        values = {
            count_name: _integer(
                read.get(count_name),
                field_name=f"{name}.{count_name}",
            )
            for count_name in names
        }
        read_counts[name] = values
        if values["recognized_option_positions"] != len(rows):
            raise PositionSnapshotError(
                f"{name} recognized position count disagrees with boundary rows"
            )
        classified = sum(
            values[count_name]
            for count_name in names
            if count_name != "total_rows"
        )
        if classified > values["total_rows"]:
            raise PositionSnapshotError(
                f"{name} classified row counts exceed total rows"
            )

    supplied_stability = _mapping(
        acquisition.get("stability"),
        field_name="acquisition.stability",
    )
    supplied = {
        "stable": _bool(
            supplied_stability.get("stable"),
            field_name="stability.stable",
        ),
        "comparison_basis": _text(
            supplied_stability.get("comparison_basis"),
            field_name="stability.comparison_basis",
        ),
        **{
            key: list(
                _string_list(
                    supplied_stability.get(key),
                    field_name=f"stability.{key}",
                )
            )
            for key in ("added_symbols", "removed_symbols", "changed_symbols")
        },
    }
    stability = _derive_stability(
        boundary_rows["first_read"],
        boundary_rows["second_read"],
    )
    if supplied != stability:
        raise PositionSnapshotError(
            "position stability summary disagrees with boundary evidence"
        )
    stable = bool(stability["stable"])

    summary = _mapping(root.get("summary"), field_name="summary")
    if summary.get("journal_database_written") is not False or summary.get("trading_actions_performed") is not False:
        raise PositionSnapshotError("summary violates read-only contract")
    if summary.get("environment") != "LIVE" or summary.get("market") != "US" or summary.get("asset_type") != "option":
        raise PositionSnapshotError("summary scope is inconsistent")
    if summary.get("mode") != "moomoo_position_snapshot_probe" or summary.get("ok") is not True:
        raise PositionSnapshotError("probe summary is not successful")
    if _text(
        summary.get("account_selection"),
        field_name="summary.account_selection",
    ) != account_selection:
        raise PositionSnapshotError(
            "summary account selection disagrees with account metadata"
        )
    retrieval_complete = _bool(summary.get("retrieval_complete"), field_name="summary.retrieval_complete")
    position_snapshot_complete = _bool(
        summary.get("position_snapshot_complete"),
        field_name="summary.position_snapshot_complete",
    )
    analysis_ready = _bool(summary.get("analysis_ready"), field_name="summary.analysis_ready")
    if _bool(summary.get("stable"), field_name="summary.stable") != stable:
        raise PositionSnapshotError("summary stability disagrees with acquisition")
    contract_spec_status = _text(
        summary.get("contract_spec_status"),
        field_name="summary.contract_spec_status",
    )
    if contract_spec_status not in {"complete", "not_applicable", "partial", "unavailable"}:
        raise PositionSnapshotError("contract spec status is unsupported")
    validation_issues = _string_list(
        summary.get("validation_issues"),
        field_name="summary.validation_issues",
    )
    warnings = _string_list(summary.get("warnings"), field_name="summary.warnings")
    counts_raw = _mapping(summary.get("counts"), field_name="summary.counts")
    count_names = (
        "positions",
        "contract_specs",
        "first_total_rows",
        "second_total_rows",
        "first_recognized_option_positions",
        "second_recognized_option_positions",
        "first_filtered_non_us_rows",
        "first_filtered_non_option_rows",
        "first_filtered_zero_quantity_rows",
        "second_filtered_zero_quantity_rows",
        "second_filtered_non_us_rows",
        "second_filtered_non_option_rows",
        "validation_issues",
    )
    if set(counts_raw) != set(count_names):
        raise PositionSnapshotError(
            "summary counts must contain exactly the supported fields"
        )
    counts = {
        name: _integer(counts_raw.get(name), field_name=f"summary.counts.{name}")
        for name in count_names
    }
    if counts["validation_issues"] != len(validation_issues):
        raise PositionSnapshotError("validation issue count disagrees with records")
    summary_to_read = {
        "first_total_rows": ("first_read", "total_rows"),
        "second_total_rows": ("second_read", "total_rows"),
        "first_recognized_option_positions": (
            "first_read",
            "recognized_option_positions",
        ),
        "second_recognized_option_positions": (
            "second_read",
            "recognized_option_positions",
        ),
        "first_filtered_non_us_rows": ("first_read", "filtered_non_us_rows"),
        "second_filtered_non_us_rows": (
            "second_read",
            "filtered_non_us_rows",
        ),
        "first_filtered_non_option_rows": (
            "first_read",
            "filtered_non_option_rows",
        ),
        "second_filtered_non_option_rows": (
            "second_read",
            "filtered_non_option_rows",
        ),
        "first_filtered_zero_quantity_rows": (
            "first_read",
            "filtered_zero_quantity_rows",
        ),
        "second_filtered_zero_quantity_rows": (
            "second_read",
            "filtered_zero_quantity_rows",
        ),
    }
    for summary_name, (read_name, read_name_field) in summary_to_read.items():
        if counts[summary_name] != read_counts[read_name][read_name_field]:
            raise PositionSnapshotError(
                f"summary count {summary_name} disagrees with acquisition"
            )

    records = _mapping(root.get("records"), field_name="records")
    position_rows = _sequence(records.get("positions"), field_name="records.positions")
    contract_spec_rows = _sequence(records.get("contract_specs"), field_name="records.contract_specs")
    specs = _parse_contract_specs(contract_spec_rows)
    members = tuple(
        _parse_member(row, index=index, specs=specs)
        for index, row in enumerate(position_rows)
    )
    symbols = tuple(member.symbol for member in members)
    if symbols != tuple(sorted(symbols)) or len(set(symbols)) != len(symbols):
        raise PositionSnapshotError("position members must be unique and sorted")
    if counts["positions"] != len(members) or counts["contract_specs"] != len(specs):
        raise PositionSnapshotError("position or contract spec count disagrees")
    if _bool(summary.get("has_positions"), field_name="summary.has_positions") != bool(members):
        raise PositionSnapshotError("has_positions disagrees with records")
    if read_counts["second_read"]["recognized_option_positions"] != len(members):
        raise PositionSnapshotError("second read position count disagrees with records")
    member_boundary = tuple(
        {
            "symbol": member.symbol,
            "position_side": member.position_side,
            "signed_quantity_contracts": member.signed_quantity_contracts,
        }
        for member in members
    )
    if boundary_rows["second_read"] != member_boundary:
        raise PositionSnapshotError(
            "second read boundary evidence disagrees with position records"
        )

    positions_sha256 = _hash(summary.get("positions_sha256"), field_name="summary.positions_sha256")
    if _sha256_json(position_rows) != positions_sha256:
        raise PositionSnapshotError("positions hash does not match")
    snapshot_sha256 = _hash(summary.get("snapshot_sha256"), field_name="summary.snapshot_sha256")
    expected_snapshot_sha256 = _sha256_json(
        {
            "schema": POSITION_SNAPSHOT_SCHEMA,
            "account_binding": binding,
            "stability": stability,
            "positions": position_rows,
            "contract_specs": contract_spec_rows,
        }
    )
    if snapshot_sha256 != expected_snapshot_sha256:
        raise PositionSnapshotError("snapshot hash does not match")

    technical_complete = bool(
        retrieval_complete
        and stable
        and not validation_issues
        and contract_spec_status in {"complete", "not_applicable"}
    )
    if position_snapshot_complete != technical_complete or analysis_ready != technical_complete:
        raise PositionSnapshotError("snapshot completeness flags disagree with evidence")
    if members and contract_spec_status == "complete" and len(specs) != len(members):
        raise PositionSnapshotError("complete contract specs do not cover all positions")
    if not members and (specs or contract_spec_status != "not_applicable"):
        raise PositionSnapshotError("empty position snapshot has invalid contract spec state")
    if technical_complete and any(member.contract_multiplier is None for member in members):
        raise PositionSnapshotError("complete snapshot has an unproved multiplier")

    member_payloads = [_member_hash_payload(member) for member in members]
    member_set_sha256 = _sha256_json(member_payloads)
    source_sha256 = _sha256_json(root)
    evidence_sha256 = _sha256_json(records)
    scope_sha256 = _sha256_json(
        {
            "schema": POSITION_SNAPSHOT_SCHEMA,
            "broker": "moomoo",
            "account_key": account_key,
            "account_binding_id": binding,
            "binding_scheme": POSITION_SNAPSHOT_BINDING_SCHEME,
            "environment": "LIVE",
            "market": "US",
            "query_started_at": first_started_at,
            "query_completed_at": second_completed_at,
            "operation_completed_at": operation_completed_at,
            "broker_as_of_at": None,
            "time_semantics": _TIME_SEMANTICS,
            "temporal_policy": _temporal_policy(),
        }
    )
    stability_evidence_sha256 = _sha256_json(
        {
            "first_boundary_sha256": first["boundary_sha256"],
            "second_boundary_sha256": second["boundary_sha256"],
            "first_boundary_rows": list(boundary_rows["first_read"]),
            "second_boundary_rows": list(boundary_rows["second_read"]),
            "stability": stability,
        }
    )
    artifact_key = _sha256_json(
        {
            "kind": "journal_position_snapshot_artifact_v1",
            "parser_name": POSITION_SNAPSHOT_PARSER_NAME,
            "parser_version": POSITION_SNAPSHOT_PARSER_VERSION,
            "scope_sha256": scope_sha256,
            "source_sha256": source_sha256,
            "evidence_sha256": evidence_sha256,
            "snapshot_sha256": snapshot_sha256,
            "stability_evidence_sha256": stability_evidence_sha256,
            "temporal_policy": _temporal_policy(),
        }
    )

    blocking: list[str] = []
    if expected_binding is None:
        blocking.append("account_continuity_not_established")
    elif expected_binding != binding:
        blocking.append("account_binding_mismatch")
    if not retrieval_complete:
        blocking.append("position_retrieval_incomplete")
    if not stable:
        blocking.append("position_boundary_unstable")
    if not position_snapshot_complete:
        blocking.append("position_snapshot_incomplete")
    if validation_issues:
        blocking.append("position_validation_issues")
    confirm_allowed = not blocking and analysis_ready
    preview_key = _sha256_json(
        {
            "kind": "journal_position_snapshot_preview_v1",
            "artifact_key": artifact_key,
            "expected_account_binding_id": expected_binding,
            "confirm_allowed": confirm_allowed,
            "blocking_reasons": blocking,
            "temporal_policy": _temporal_policy(),
        }
    )
    total_contracts = _decimal_output(
        sum((abs(Decimal(member.signed_quantity_contracts)) for member in members), Decimal("0"))
    )
    content_state = "empty" if technical_complete and not members else (
        "positions" if technical_complete else "incomplete"
    )
    return PositionSnapshotPlan(
        account_key=account_key,
        account_binding_id=binding,
        expected_account_binding_id=expected_binding,
        account_selection=account_selection,
        query_requested_at=requested_at,
        query_started_at=first_started_at,
        query_completed_at=second_completed_at,
        operation_completed_at=operation_completed_at,
        broker_as_of_at=None,
        retrieval_complete=retrieval_complete,
        position_snapshot_complete=position_snapshot_complete,
        stability_status="stable" if stable else "unstable",
        stability=stability,
        contract_spec_status=contract_spec_status,
        analysis_ready=analysis_ready,
        content_state=content_state,
        position_count=len(members),
        contract_spec_count=len(specs),
        total_contracts=total_contracts,
        counts=counts,
        warnings=warnings,
        validation_issues=validation_issues,
        members=members,
        artifact_key=artifact_key,
        preview_key=preview_key,
        scope_sha256=scope_sha256,
        source_sha256=source_sha256,
        evidence_sha256=evidence_sha256,
        snapshot_sha256=snapshot_sha256,
        stability_evidence_sha256=stability_evidence_sha256,
        member_set_sha256=member_set_sha256,
        temporal_policy=_temporal_policy(),
        refresh_publication_id=None,
        refresh_publication_key=None,
        account_continuity_sha256=None,
        confirm_allowed=confirm_allowed,
        blocking_reasons=tuple(blocking),
        future_anchor_candidate=confirm_allowed,
    )


def init_position_snapshot_schema() -> None:
    """Create snapshot tables and SQLite append-only guards."""
    with _SCHEMA_LOCK:
        db = get_db()
        Base.metadata.create_all(db._engine)
        if db._engine.dialect.name == "sqlite":
            with db._engine.begin() as connection:
                # ``create_all`` does not add columns to an early table that a
                # reload may already have created.  These nullable additive
                # columns preserve that local data; all new writes below fail
                # closed unless the continuity/provenance values are present.
                additions = {
                    PositionSnapshotArtifact.__tablename__: {
                        "position_snapshot_complete": "BOOLEAN",
                        "refresh_publication_id": "INTEGER",
                        "refresh_publication_key": "VARCHAR(128)",
                        "account_continuity_sha256": "VARCHAR(64)",
                    },
                    ConfirmedPositionSnapshot.__tablename__: {
                        "refresh_publication_id": "INTEGER",
                        "refresh_publication_key": "VARCHAR(128)",
                        "account_continuity_sha256": "VARCHAR(64)",
                        "stability_evidence_sha256": "VARCHAR(64)",
                        "provenance_sha256": "VARCHAR(64)",
                    },
                    PositionSnapshotMember.__tablename__: {
                        "provenance_sha256": "VARCHAR(64)",
                    },
                }
                inspector = inspect(connection)
                for table_name, columns in additions.items():
                    existing_columns = {
                        str(column["name"])
                        for column in inspector.get_columns(table_name)
                    }
                    for column_name, sql_type in columns.items():
                        if column_name not in existing_columns:
                            connection.exec_driver_sql(
                                f"ALTER TABLE {table_name} "
                                f"ADD COLUMN {column_name} {sql_type}"
                            )
                for table_name in _IMMUTABLE_TABLES:
                    for operation in ("UPDATE", "DELETE"):
                        connection.exec_driver_sql(
                            position_snapshot_guard_trigger_ddl(
                                table_name,
                                operation,
                            )
                        )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_jv2_position_snapshot_artifact_publication "
                    "ON journal_v2_position_snapshot_artifacts "
                    "(refresh_publication_id)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_jv2_position_snapshot_publication "
                    "ON journal_v2_position_snapshots "
                    "(refresh_publication_id)"
                )


def _payload_from_row(row: PositionSnapshotArtifact) -> Mapping[str, Any]:
    try:
        payload = json.loads(str(row.payload_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PositionSnapshotError("stored position snapshot artifact payload is invalid") from exc
    return _mapping(payload, field_name="stored artifact payload")


def _artifact_record(
    session: Any,
    row: PositionSnapshotArtifact,
) -> PositionSnapshotArtifactRecord:
    payload = _payload_from_row(row)
    publication: Optional[JournalRefreshPublication] = None
    if row.refresh_publication_id is not None:
        publication = session.get(
            JournalRefreshPublication,
            int(row.refresh_publication_id),
        )
        if publication is None:
            raise PositionSnapshotError(
                "stored position snapshot refresh publication is missing"
            )
        if (
            str(row.refresh_publication_key) != _publication_key(publication)
            or str(row.account_continuity_sha256)
            != _continuity_sha256(publication)
        ):
            raise PositionSnapshotError(
                "stored position snapshot publication provenance is inconsistent"
            )
        expected_binding: Optional[str] = _hash(
            publication.account_binding,
            field_name="refresh publication account binding",
        )
    else:
        if (
            row.refresh_publication_key is not None
            or row.account_continuity_sha256 is not None
        ):
            raise PositionSnapshotError(
                "stored position snapshot publication fields are inconsistent"
            )
        expected_binding = None

    base_plan = plan_position_snapshot(
        payload,
        account_key=str(row.account_key),
        expected_account_binding_id=expected_binding,
    )
    trusted_plan = _bind_plan_to_publication(base_plan, publication)
    comparisons = {
        "artifact key": (str(row.artifact_key), trusted_plan.artifact_key),
        "account binding": (
            str(row.account_binding_id),
            trusted_plan.account_binding_id,
        ),
        "scope hash": (str(row.scope_sha256), trusted_plan.scope_sha256),
        "source hash": (str(row.source_sha256), trusted_plan.source_sha256),
        "evidence hash": (
            str(row.evidence_sha256),
            trusted_plan.evidence_sha256,
        ),
        "snapshot hash": (
            str(row.snapshot_sha256),
            trusted_plan.snapshot_sha256,
        ),
        "stability hash": (
            str(row.stability_evidence_sha256),
            trusted_plan.stability_evidence_sha256,
        ),
        "preview key": (str(row.preview_key), trusted_plan.preview_key),
        "query start": (
            _db_utc(row.query_started_at),
            trusted_plan.query_started_at,
        ),
        "query completion": (
            _db_utc(row.query_completed_at),
            trusted_plan.query_completed_at,
        ),
        "operation completion": (
            _db_utc(row.generated_at),
            trusted_plan.operation_completed_at,
        ),
        "retrieval flag": (
            bool(row.retrieval_complete),
            trusted_plan.retrieval_complete,
        ),
        "completeness flag": (
            bool(row.position_snapshot_complete),
            trusted_plan.position_snapshot_complete,
        ),
        "stability status": (
            str(row.stability_status),
            trusted_plan.stability_status,
        ),
        "contract spec status": (
            str(row.contract_spec_status),
            trusted_plan.contract_spec_status,
        ),
        "position count": (
            int(row.position_count),
            trusted_plan.position_count,
        ),
        "contract spec count": (
            int(row.contract_spec_count),
            trusted_plan.contract_spec_count,
        ),
        "confirmation policy": (
            bool(row.confirm_allowed),
            trusted_plan.confirm_allowed,
        ),
    }
    mismatch = next(
        (name for name, pair in comparisons.items() if pair[0] != pair[1]),
        None,
    )
    if mismatch is not None:
        raise PositionSnapshotError(
            f"stored position snapshot artifact {mismatch} is inconsistent"
        )
    if (
        str(row.broker) != "moomoo"
        or str(row.binding_scheme) != POSITION_SNAPSHOT_BINDING_SCHEME
        or str(row.environment) != "LIVE"
        or str(row.market) != "US"
        or str(row.source_schema) != POSITION_SNAPSHOT_SCHEMA
        or str(row.parser_name) != POSITION_SNAPSHOT_PARSER_NAME
        or str(row.parser_version) != POSITION_SNAPSHOT_PARSER_VERSION
        or row.broker_as_of_at is not None
        or bool(row.historical_opening_proven)
    ):
        raise PositionSnapshotError(
            "stored position snapshot artifact header is inconsistent"
        )
    recorded_at = _db_utc(row.recorded_at)
    expires_at = _db_utc(row.expires_at)
    if expires_at <= recorded_at:
        raise PositionSnapshotError(
            "stored position snapshot artifact expiry is inconsistent"
        )

    return PositionSnapshotArtifactRecord(
        artifact_id=int(row.id),
        artifact_key=str(row.artifact_key),
        account_key=str(row.account_key),
        account_binding_id=str(row.account_binding_id),
        refresh_publication_id=(
            None
            if row.refresh_publication_id is None
            else int(row.refresh_publication_id)
        ),
        refresh_publication_key=(
            None
            if row.refresh_publication_key is None
            else str(row.refresh_publication_key)
        ),
        account_continuity_sha256=(
            None
            if row.account_continuity_sha256 is None
            else str(row.account_continuity_sha256)
        ),
        environment=str(row.environment),
        market=str(row.market),
        query_started_at=_db_utc(row.query_started_at),
        query_completed_at=_db_utc(row.query_completed_at),
        operation_completed_at=_db_utc(row.generated_at),
        broker_as_of_at=None,
        scope_sha256=str(row.scope_sha256),
        source_sha256=str(row.source_sha256),
        evidence_sha256=str(row.evidence_sha256),
        snapshot_sha256=str(row.snapshot_sha256),
        stability_evidence_sha256=str(row.stability_evidence_sha256),
        preview_key=str(row.preview_key),
        retrieval_complete=bool(row.retrieval_complete),
        position_snapshot_complete=bool(row.position_snapshot_complete),
        stability_status=str(row.stability_status),
        contract_spec_status=str(row.contract_spec_status),
        position_count=int(row.position_count),
        contract_spec_count=int(row.contract_spec_count),
        confirm_allowed=bool(row.confirm_allowed),
        expires_at=expires_at,
        recorded_at=recorded_at,
        payload=payload,
        historical_opening_proven=False,
    )


def save_position_snapshot_artifact(
    plan: PositionSnapshotPlan,
    payload: Mapping[str, Any],
    *,
    ttl_minutes: int = DEFAULT_POSITION_SNAPSHOT_ARTIFACT_TTL_MINUTES,
) -> PositionSnapshotArtifactRecord:
    """Persist an exact server-owned preview without confirming evidence."""
    if ttl_minutes < 1 or ttl_minutes > 240:
        raise PositionSnapshotError("position snapshot artifact TTL must be between 1 and 240 minutes")
    revalidated = plan_position_snapshot(
        payload,
        account_key=plan.account_key,
        expected_account_binding_id=plan.expected_account_binding_id,
    )
    if revalidated != plan:
        raise PositionSnapshotError("position snapshot plan does not match its payload")
    now = _now_utc()
    _enforce_temporal_freshness(revalidated, now=now)
    init_position_snapshot_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        publication = _latest_refresh_publication_row(
            session,
            account_key=plan.account_key,
        )
        trusted_binding = (
            None
            if publication is None
            else _hash(
                publication.account_binding,
                field_name="refresh publication account binding",
            )
        )
        trusted_plan = _bind_plan_to_publication(
            plan_position_snapshot(
                payload,
                account_key=plan.account_key,
                expected_account_binding_id=trusted_binding,
            ),
            publication,
        )
        _enforce_temporal_freshness(trusted_plan, now=now)
        existing = session.execute(
            select(PositionSnapshotArtifact).where(
                PositionSnapshotArtifact.artifact_key
                == trusted_plan.artifact_key
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _artifact_record(session, existing)
        row = PositionSnapshotArtifact(
            artifact_key=trusted_plan.artifact_key,
            broker="moomoo",
            account_key=trusted_plan.account_key,
            account_binding_id=trusted_plan.account_binding_id,
            binding_scheme=POSITION_SNAPSHOT_BINDING_SCHEME,
            refresh_publication_id=trusted_plan.refresh_publication_id,
            refresh_publication_key=trusted_plan.refresh_publication_key,
            account_continuity_sha256=(
                trusted_plan.account_continuity_sha256
            ),
            environment="LIVE",
            market="US",
            source_schema=POSITION_SNAPSHOT_SCHEMA,
            parser_name=POSITION_SNAPSHOT_PARSER_NAME,
            parser_version=POSITION_SNAPSHOT_PARSER_VERSION,
            query_started_at=trusted_plan.query_started_at,
            query_completed_at=trusted_plan.query_completed_at,
            generated_at=trusted_plan.operation_completed_at,
            broker_as_of_at=None,
            scope_sha256=trusted_plan.scope_sha256,
            source_sha256=trusted_plan.source_sha256,
            evidence_sha256=trusted_plan.evidence_sha256,
            snapshot_sha256=trusted_plan.snapshot_sha256,
            stability_evidence_sha256=(
                trusted_plan.stability_evidence_sha256
            ),
            preview_key=trusted_plan.preview_key,
            retrieval_complete=trusted_plan.retrieval_complete,
            position_snapshot_complete=(
                trusted_plan.position_snapshot_complete
            ),
            stability_status=trusted_plan.stability_status,
            contract_spec_status=trusted_plan.contract_spec_status,
            position_count=trusted_plan.position_count,
            contract_spec_count=trusted_plan.contract_spec_count,
            confirm_allowed=trusted_plan.confirm_allowed,
            historical_opening_proven=False,
            payload_json=_canonical_json(payload),
            expires_at=now + timedelta(minutes=ttl_minutes),
            recorded_at=now,
        )
        session.add(row)
        session.flush()
        return _artifact_record(session, row)


def get_position_snapshot_artifact(
    artifact_id: int,
    *,
    account_key: str,
) -> Optional[PositionSnapshotArtifactRecord]:
    init_position_snapshot_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.execute(
            select(PositionSnapshotArtifact).where(
                PositionSnapshotArtifact.id == artifact_id,
                PositionSnapshotArtifact.broker == "moomoo",
                PositionSnapshotArtifact.account_key == account_key,
            )
        ).scalar_one_or_none()
        return None if row is None else _artifact_record(session, row)


def _member_record(row: PositionSnapshotMember) -> PositionSnapshotMemberRecord:
    symbol = _text(row.symbol, field_name="stored member symbol").upper()
    asset_type = _text(
        row.asset_type,
        field_name="stored member asset type",
    ).lower()
    underlying = _text(
        row.underlying,
        field_name="stored member underlying",
    ).upper()
    if asset_type != "option":
        raise PositionSnapshotError("stored member asset type is inconsistent")
    try:
        parsed_symbol = parse_symbol(symbol.split(".", 1)[1])
    except (ValueError, IndexError) as exc:
        raise PositionSnapshotError("stored member symbol is invalid") from exc
    if (
        not symbol.startswith("US.")
        or not parsed_symbol.is_option
        or parsed_symbol.option is None
    ):
        raise PositionSnapshotError("stored member symbol is invalid")
    option = parsed_symbol.option
    strike = _decimal(row.strike, field_name="stored member strike", positive=True)
    quantity = _decimal(
        row.quantity_contracts,
        field_name="stored member quantity",
        positive=True,
    )
    signed = _decimal(
        row.signed_quantity_contracts,
        field_name="stored member signed quantity",
    )
    can_sell = _decimal(
        row.can_sell_quantity_contracts,
        field_name="stored member can-sell quantity",
        non_negative=True,
        optional=True,
    )
    multiplier = _decimal(
        row.contract_multiplier,
        field_name="stored member contract multiplier",
        positive=True,
    )
    assert strike is not None and quantity is not None
    assert signed is not None and multiplier is not None
    side = _text(row.position_side, field_name="stored member side").upper()
    if side not in {"LONG", "SHORT"}:
        raise PositionSnapshotError("stored member side is inconsistent")
    if signed != (quantity if side == "LONG" else -quantity):
        raise PositionSnapshotError("stored member signed quantity is inconsistent")
    right = _text(row.option_right, field_name="stored member right").upper()
    expiry = row.expiry
    if not isinstance(expiry, date):
        raise PositionSnapshotError("stored member expiry is invalid")
    if (
        underlying != option.underlying
        or expiry != option.expiry
        or strike != Decimal(str(option.strike))
        or right != option.right
    ):
        raise PositionSnapshotError("stored member OCC identity is inconsistent")
    currency = _text(row.currency, field_name="stored member currency").upper()
    if currency != "USD":
        raise PositionSnapshotError("stored member currency is inconsistent")
    cost_values: dict[str, Optional[str]] = {}
    for name in ("cost_price", "average_cost", "diluted_cost"):
        parsed = _decimal(
            getattr(row, name),
            field_name=f"stored member {name}",
            optional=True,
        )
        cost_values[name] = None if parsed is None else _decimal_output(parsed)
    if row.cost_price_valid is not None and not isinstance(
        row.cost_price_valid,
        bool,
    ):
        raise PositionSnapshotError("stored member cost validity is invalid")
    semantics = _text(
        row.cost_context_semantics,
        field_name="stored member cost semantics",
    )
    if semantics != _COST_CONTEXT_SEMANTICS:
        raise PositionSnapshotError("stored member cost semantics are unsafe")
    return PositionSnapshotMemberRecord(
        symbol=symbol,
        asset_type=asset_type,
        underlying=underlying,
        expiry=row.expiry,
        strike=_decimal_output(strike),
        option_right=right,
        currency=currency,
        position_side=side,
        quantity_contracts=_decimal_output(quantity),
        signed_quantity_contracts=_decimal_output(signed),
        can_sell_quantity_contracts=(
            None if can_sell is None else _decimal_output(can_sell)
        ),
        contract_multiplier=_decimal_output(multiplier),
        contract_multiplier_basis=_text(
            row.contract_multiplier_basis,
            field_name="stored member multiplier basis",
        ),
        cost_price=cost_values["cost_price"],
        cost_price_valid=row.cost_price_valid,
        average_cost=cost_values["average_cost"],
        diluted_cost=cost_values["diluted_cost"],
        cost_context_semantics=semantics,
        source_position_sha256=_hash(
            row.source_position_sha256,
            field_name="stored member source position hash",
        ),
        source_contract_spec_sha256=_hash(
            row.source_contract_spec_sha256,
            field_name="stored member source contract spec hash",
        ),
    )


def _provenance(value: Any) -> Mapping[str, Any]:
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PositionSnapshotError("stored position snapshot provenance is invalid") from exc
    return _mapping(payload, field_name="stored snapshot provenance")


def _snapshot_provenance(
    *,
    plan: PositionSnapshotPlan,
    artifact: PositionSnapshotArtifactRecord,
    publication: JournalRefreshPublication,
) -> dict[str, Any]:
    return {
        "kind": "journal_position_snapshot_provenance_v2",
        "source_schema": POSITION_SNAPSHOT_SCHEMA,
        "parser_name": POSITION_SNAPSHOT_PARSER_NAME,
        "parser_version": POSITION_SNAPSHOT_PARSER_VERSION,
        "account_key": plan.account_key,
        "account_binding_id": plan.account_binding_id,
        "account_selection": plan.account_selection,
        "refresh_publication": _publication_provenance(publication),
        "account_continuity_sha256": plan.account_continuity_sha256,
        "artifact_id": artifact.artifact_id,
        "artifact_key": artifact.artifact_key,
        "preview_key": artifact.preview_key,
        "scope_sha256": plan.scope_sha256,
        "source_sha256": plan.source_sha256,
        "evidence_sha256": plan.evidence_sha256,
        "snapshot_sha256": plan.snapshot_sha256,
        "stability_evidence_sha256": plan.stability_evidence_sha256,
        "member_set_sha256": plan.member_set_sha256,
        "query_requested_at": plan.query_requested_at,
        "query_started_at": plan.query_started_at,
        "query_completed_at": plan.query_completed_at,
        "operation_completed_at": plan.operation_completed_at,
        "broker_as_of_at": None,
        "time_semantics": _TIME_SEMANTICS,
        "temporal_policy": plan.temporal_policy,
        "retrieval_complete": plan.retrieval_complete,
        "position_snapshot_complete": plan.position_snapshot_complete,
        "stability_status": plan.stability_status,
        "stability": plan.stability,
        "contract_spec_status": plan.contract_spec_status,
        "analysis_ready": plan.analysis_ready,
        "content_state": plan.content_state,
        "total_contracts": plan.total_contracts,
        "counts": plan.counts,
        "warnings": plan.warnings,
        "validation_issues": plan.validation_issues,
        "future_anchor_candidate": True,
        "acknowledged_future_only": True,
        "historical_opening_proven": False,
        "cost_context_only": True,
    }


def _snapshot_key(
    *,
    artifact: PositionSnapshotArtifactRecord,
    plan: PositionSnapshotPlan,
    provenance_sha256: str,
) -> str:
    return _sha256_json(
        {
            "kind": "journal_position_snapshot_v2",
            "artifact_id": artifact.artifact_id,
            "artifact_key": artifact.artifact_key,
            "preview_key": artifact.preview_key,
            "member_set_sha256": plan.member_set_sha256,
            "refresh_publication_id": plan.refresh_publication_id,
            "refresh_publication_key": plan.refresh_publication_key,
            "account_continuity_sha256": plan.account_continuity_sha256,
            "provenance_sha256": provenance_sha256,
            "acknowledged_future_only": True,
            "temporal_policy": plan.temporal_policy,
        }
    )


def _member_provenance(
    *,
    artifact: PositionSnapshotArtifactRecord,
    snapshot_key: str,
    instrument_key: str,
    member: PositionSnapshotMemberRecord,
) -> dict[str, Any]:
    return {
        "kind": "journal_position_snapshot_member_v2",
        "artifact_id": artifact.artifact_id,
        "artifact_key": artifact.artifact_key,
        "snapshot_key": snapshot_key,
        "instrument_key": instrument_key,
        "source_position_sha256": member.source_position_sha256,
        "source_contract_spec_sha256": member.source_contract_spec_sha256,
        "cost_context_only": True,
    }


def _snapshot_record(
    session: Any,
    row: ConfirmedPositionSnapshot,
    members: Sequence[PositionSnapshotMember],
) -> PositionSnapshotRecord:
    artifact_row = session.get(PositionSnapshotArtifact, int(row.artifact_id))
    if artifact_row is None:
        raise PositionSnapshotError(
            "stored position snapshot artifact is missing"
        )
    artifact = _artifact_record(session, artifact_row)
    if artifact.refresh_publication_id is None:
        raise PositionSnapshotError(
            "confirmed position snapshot lacks a refresh publication"
        )
    publication = session.get(
        JournalRefreshPublication,
        artifact.refresh_publication_id,
    )
    if publication is None:
        raise PositionSnapshotError(
            "confirmed position snapshot refresh publication is missing"
        )
    plan = _bind_plan_to_publication(
        plan_position_snapshot(
            artifact.payload,
            account_key=artifact.account_key,
            expected_account_binding_id=_hash(
                publication.account_binding,
                field_name="refresh publication account binding",
            ),
        ),
        publication,
    )
    provenance = _provenance(row.provenance_json)
    stored_provenance_sha256 = _hash(
        row.provenance_sha256,
        field_name="stored snapshot provenance hash",
    )
    if _sha256_json(provenance) != stored_provenance_sha256:
        raise PositionSnapshotError(
            "stored position snapshot provenance hash is inconsistent"
        )
    expected_provenance = _snapshot_provenance(
        plan=plan,
        artifact=artifact,
        publication=publication,
    )
    if _sha256_json(expected_provenance) != stored_provenance_sha256:
        raise PositionSnapshotError(
            "stored position snapshot provenance disagrees with evidence"
        )
    expected_snapshot_key = _snapshot_key(
        artifact=artifact,
        plan=plan,
        provenance_sha256=stored_provenance_sha256,
    )
    header_comparisons = {
        "snapshot key": (str(row.snapshot_key), expected_snapshot_key),
        "account": (str(row.account_key), artifact.account_key),
        "account binding": (
            str(row.account_binding_id),
            artifact.account_binding_id,
        ),
        "publication id": (
            None
            if row.refresh_publication_id is None
            else int(row.refresh_publication_id),
            artifact.refresh_publication_id,
        ),
        "publication key": (
            None
            if row.refresh_publication_key is None
            else str(row.refresh_publication_key),
            artifact.refresh_publication_key,
        ),
        "continuity hash": (
            None
            if row.account_continuity_sha256 is None
            else str(row.account_continuity_sha256),
            artifact.account_continuity_sha256,
        ),
        "query start": (
            _db_utc(row.query_started_at),
            artifact.query_started_at,
        ),
        "query completion": (
            _db_utc(row.query_completed_at),
            artifact.query_completed_at,
        ),
        "operation completion": (
            _db_utc(row.operation_completed_at),
            artifact.operation_completed_at,
        ),
        "scope hash": (str(row.scope_sha256), artifact.scope_sha256),
        "source hash": (str(row.source_sha256), artifact.source_sha256),
        "evidence hash": (
            str(row.evidence_sha256),
            artifact.evidence_sha256,
        ),
        "snapshot hash": (
            str(row.snapshot_sha256),
            artifact.snapshot_sha256,
        ),
        "stability hash": (
            None
            if row.stability_evidence_sha256 is None
            else str(row.stability_evidence_sha256),
            plan.stability_evidence_sha256,
        ),
        "member set hash": (
            str(row.member_set_sha256),
            plan.member_set_sha256,
        ),
        "member count": (int(row.member_count), len(plan.members)),
    }
    mismatch = next(
        (
            name
            for name, pair in header_comparisons.items()
            if pair[0] != pair[1]
        ),
        None,
    )
    if mismatch is not None:
        raise PositionSnapshotError(
            f"stored position snapshot {mismatch} is inconsistent"
        )
    if (
        str(row.broker) != "moomoo"
        or str(row.binding_scheme) != POSITION_SNAPSHOT_BINDING_SCHEME
        or str(row.environment) != "LIVE"
        or str(row.market) != "US"
        or row.broker_as_of_at is not None
        or not bool(row.acknowledged_future_only)
        or bool(row.historical_opening_proven)
        or not bool(row.cost_context_only)
    ):
        raise PositionSnapshotError(
            "stored position snapshot header is inconsistent"
        )

    member_records = tuple(_member_record(member) for member in members)
    if len(member_records) != int(row.member_count):
        raise PositionSnapshotError("stored position snapshot member count is inconsistent")
    if tuple(member.symbol for member in member_records) != tuple(
        sorted(member.symbol for member in member_records)
    ):
        raise PositionSnapshotError(
            "stored position snapshot members are not sorted"
        )
    if _sha256_json([_member_hash_payload(member) for member in member_records]) != plan.member_set_sha256:
        raise PositionSnapshotError("stored position snapshot member set hash is inconsistent")
    planned_members = {member.symbol: member for member in plan.members}
    for member_row, member in zip(members, member_records):
        planned = planned_members.get(member.symbol)
        if planned is None or _member_hash_payload(planned) != _member_hash_payload(member):
            raise PositionSnapshotError(
                "stored position snapshot member disagrees with artifact evidence"
            )
        instrument_key = _instrument_key(member)
        member_key = _member_key(
            snapshot_key=expected_snapshot_key,
            instrument_key=instrument_key,
            source_position_sha256=member.source_position_sha256,
        )
        if (
            str(member_row.instrument_key) != instrument_key
            or str(member_row.member_key) != member_key
        ):
            raise PositionSnapshotError(
                "stored position snapshot member identity is inconsistent"
            )
        member_provenance = _provenance(member_row.provenance_json)
        member_provenance_sha256 = _hash(
            member_row.provenance_sha256,
            field_name="stored member provenance hash",
        )
        if _sha256_json(member_provenance) != member_provenance_sha256:
            raise PositionSnapshotError(
                "stored position snapshot member provenance hash is inconsistent"
            )
        expected_member_provenance = _member_provenance(
            artifact=artifact,
            snapshot_key=expected_snapshot_key,
            instrument_key=instrument_key,
            member=member,
        )
        if _sha256_json(expected_member_provenance) != member_provenance_sha256:
            raise PositionSnapshotError(
                "stored position snapshot member provenance disagrees with evidence"
            )
    return PositionSnapshotRecord(
        snapshot_id=int(row.id),
        snapshot_key=expected_snapshot_key,
        artifact_id=int(row.artifact_id),
        account_key=str(row.account_key),
        account_binding_id=str(row.account_binding_id),
        refresh_publication_id=int(row.refresh_publication_id),
        refresh_publication_key=str(row.refresh_publication_key),
        account_continuity_sha256=str(row.account_continuity_sha256),
        query_started_at=_db_utc(row.query_started_at),
        query_completed_at=_db_utc(row.query_completed_at),
        operation_completed_at=_db_utc(row.operation_completed_at),
        broker_as_of_at=None,
        scope_sha256=str(row.scope_sha256),
        source_sha256=str(row.source_sha256),
        evidence_sha256=str(row.evidence_sha256),
        snapshot_sha256=str(row.snapshot_sha256),
        stability_evidence_sha256=str(row.stability_evidence_sha256),
        member_set_sha256=str(row.member_set_sha256),
        provenance_sha256=stored_provenance_sha256,
        acknowledged_future_only=bool(row.acknowledged_future_only),
        historical_opening_proven=bool(row.historical_opening_proven),
        cost_context_only=bool(row.cost_context_only),
        recorded_at=_db_utc(row.recorded_at),
        retrieval_complete=plan.retrieval_complete,
        position_snapshot_complete=plan.position_snapshot_complete,
        stability_status=plan.stability_status,
        stability=plan.stability,
        contract_spec_status=plan.contract_spec_status,
        analysis_ready=plan.analysis_ready,
        content_state=plan.content_state,
        total_contracts=plan.total_contracts,
        counts=plan.counts,
        warnings=plan.warnings,
        validation_issues=plan.validation_issues,
        members=member_records,
    )


def _load_snapshot_record(session: Any, row: ConfirmedPositionSnapshot) -> PositionSnapshotRecord:
    members = tuple(
        session.execute(
            select(PositionSnapshotMember)
            .where(PositionSnapshotMember.snapshot_id == row.id)
            .order_by(PositionSnapshotMember.symbol, PositionSnapshotMember.id)
        ).scalars()
    )
    return _snapshot_record(session, row, members)


def load_latest_position_snapshot_in_session(
    session: Any,
    *,
    account_key: str,
) -> Optional[PositionSnapshotRecord]:
    """Strictly validate the latest snapshot in an existing read transaction.

    Unlike the public convenience readers, this helper never initializes a
    schema and never obtains another connection.  Callers that require a
    fail-closed, zero-business-write read can therefore pin snapshot and
    downstream evidence checks to one explicit database transaction.
    """
    normalized_account = _text(account_key, field_name="account_key")
    row = session.execute(
        select(ConfirmedPositionSnapshot)
        .where(
            ConfirmedPositionSnapshot.broker == "moomoo",
            ConfirmedPositionSnapshot.account_key == normalized_account,
        )
        .order_by(
            ConfirmedPositionSnapshot.query_completed_at.desc(),
            ConfirmedPositionSnapshot.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    return None if row is None else _load_snapshot_record(session, row)


def confirm_position_snapshot_artifact(
    artifact_id: int,
    *,
    preview_key: str,
    acknowledge_future_only: bool,
    expected_account_binding_id: str,
    account_key: str,
) -> PositionSnapshotConfirmation:
    """Atomically confirm one non-expired artifact; never touch Episodes."""
    if acknowledge_future_only is not True:
        raise PositionSnapshotError("explicit future-only acknowledgement is required")
    preview_key = _hash(preview_key, field_name="preview_key")
    expected_binding = _normalize_binding(
        expected_account_binding_id,
        field_name="expected_account_binding_id",
    )
    assert expected_binding is not None
    init_position_snapshot_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        artifact_row = session.execute(
            select(PositionSnapshotArtifact).where(
                PositionSnapshotArtifact.id == artifact_id,
                PositionSnapshotArtifact.broker == "moomoo",
                PositionSnapshotArtifact.account_key == account_key,
            )
        ).scalar_one_or_none()
        if artifact_row is None:
            raise PositionSnapshotError("position snapshot artifact does not exist")
        artifact = _artifact_record(session, artifact_row)
        if artifact.preview_key != preview_key:
            raise PositionSnapshotError("position snapshot preview key does not match")
        existing = session.execute(
            select(ConfirmedPositionSnapshot).where(
                ConfirmedPositionSnapshot.artifact_id == artifact_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return PositionSnapshotConfirmation(
                artifact=artifact,
                snapshot=_load_snapshot_record(session, existing),
                duplicate=True,
            )
        if not artifact.confirm_allowed:
            raise PositionSnapshotError(
                "position snapshot artifact was not eligible for confirmation"
            )

        # Caller state is only a stale-request guard.  The transaction derives
        # authority from the latest immutable Journal refresh publication.
        publication = _latest_refresh_publication_row(
            session,
            account_key=account_key,
        )
        if publication is None:
            raise PositionSnapshotError(
                "account continuity is not established by a Journal refresh publication"
            )
        publication_binding = _hash(
            publication.account_binding,
            field_name="refresh publication account binding",
        )
        if expected_binding != publication_binding:
            raise PositionSnapshotError(
                "expected account binding is not the latest Journal refresh publication"
            )
        if (
            artifact.refresh_publication_id != int(publication.id)
            or artifact.refresh_publication_key != _publication_key(publication)
            or artifact.account_continuity_sha256
            != _continuity_sha256(publication)
        ):
            raise PositionSnapshotError(
                "position snapshot preview is not bound to the latest Journal refresh publication"
            )
        plan = _bind_plan_to_publication(
            plan_position_snapshot(
                artifact.payload,
                account_key=account_key,
                expected_account_binding_id=publication_binding,
            ),
            publication,
        )
        if plan.artifact_key != artifact.artifact_key or plan.preview_key != artifact.preview_key:
            raise PositionSnapshotError(
                "stored position snapshot changed after preview"
            )
        if not plan.confirm_allowed or not plan.future_anchor_candidate:
            raise PositionSnapshotError(
                "position snapshot no longer passes confirmation policy"
            )

        now = _now_utc()
        if now >= artifact.expires_at:
            raise PositionSnapshotError(
                "position snapshot preview expired; run a new read-only check"
            )
        _enforce_temporal_freshness(plan, now=now)

        provenance = _snapshot_provenance(
            plan=plan,
            artifact=artifact,
            publication=publication,
        )
        provenance_sha256 = _sha256_json(provenance)
        snapshot_key = _snapshot_key(
            artifact=artifact,
            plan=plan,
            provenance_sha256=provenance_sha256,
        )
        snapshot_row = ConfirmedPositionSnapshot(
            snapshot_key=snapshot_key,
            artifact_id=artifact_id,
            broker="moomoo",
            account_key=account_key,
            account_binding_id=plan.account_binding_id,
            binding_scheme=POSITION_SNAPSHOT_BINDING_SCHEME,
            refresh_publication_id=int(publication.id),
            refresh_publication_key=_publication_key(publication),
            account_continuity_sha256=_continuity_sha256(publication),
            environment="LIVE",
            market="US",
            query_started_at=plan.query_started_at,
            query_completed_at=plan.query_completed_at,
            operation_completed_at=plan.operation_completed_at,
            broker_as_of_at=None,
            scope_sha256=plan.scope_sha256,
            source_sha256=plan.source_sha256,
            evidence_sha256=plan.evidence_sha256,
            snapshot_sha256=plan.snapshot_sha256,
            stability_evidence_sha256=plan.stability_evidence_sha256,
            member_set_sha256=plan.member_set_sha256,
            member_count=len(plan.members),
            acknowledged_future_only=True,
            historical_opening_proven=False,
            cost_context_only=True,
            provenance_json=_canonical_json(provenance),
            provenance_sha256=provenance_sha256,
            recorded_at=now,
        )
        session.add(snapshot_row)
        session.flush()
        for member in plan.members:
            if member.contract_multiplier is None or member.source_contract_spec_sha256 is None:
                raise PositionSnapshotError("confirmed member lacks a proved contract multiplier")
            instrument_key = _instrument_key(member)
            member_key = _member_key(
                snapshot_key=snapshot_key,
                instrument_key=instrument_key,
                source_position_sha256=member.source_position_sha256,
            )
            member_provenance = _member_provenance(
                artifact=artifact,
                snapshot_key=snapshot_key,
                instrument_key=instrument_key,
                member=member,
            )
            session.add(
                PositionSnapshotMember(
                    snapshot_id=snapshot_row.id,
                    member_key=member_key,
                    instrument_key=instrument_key,
                    symbol=member.symbol,
                    asset_type="option",
                    underlying=member.underlying,
                    expiry=member.expiry,
                    strike=member.strike,
                    option_right=member.option_right,
                    currency=member.currency,
                    position_side=member.position_side,
                    quantity_contracts=member.quantity_contracts,
                    signed_quantity_contracts=member.signed_quantity_contracts,
                    can_sell_quantity_contracts=member.can_sell_quantity_contracts,
                    contract_multiplier=member.contract_multiplier,
                    contract_multiplier_basis=member.contract_multiplier_basis,
                    cost_price=member.cost_price,
                    cost_price_valid=member.cost_price_valid,
                    average_cost=member.average_cost,
                    diluted_cost=member.diluted_cost,
                    cost_context_semantics=member.cost_context_semantics,
                    source_position_sha256=member.source_position_sha256,
                    source_contract_spec_sha256=member.source_contract_spec_sha256,
                    provenance_json=_canonical_json(member_provenance),
                    provenance_sha256=_sha256_json(member_provenance),
                    recorded_at=now,
                )
            )
        session.flush()
        return PositionSnapshotConfirmation(
            artifact=artifact,
            snapshot=_load_snapshot_record(session, snapshot_row),
            duplicate=False,
        )


def get_position_snapshot_detail(
    snapshot_id: int,
    *,
    account_key: str,
) -> Optional[PositionSnapshotRecord]:
    init_position_snapshot_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.execute(
            select(ConfirmedPositionSnapshot).where(
                ConfirmedPositionSnapshot.id == snapshot_id,
                ConfirmedPositionSnapshot.broker == "moomoo",
                ConfirmedPositionSnapshot.account_key == account_key,
            )
        ).scalar_one_or_none()
        return None if row is None else _load_snapshot_record(session, row)


def get_latest_position_snapshot(
    account_key: str,
) -> Optional[PositionSnapshotRecord]:
    init_position_snapshot_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.execute(
            select(ConfirmedPositionSnapshot)
            .where(
                ConfirmedPositionSnapshot.broker == "moomoo",
                ConfirmedPositionSnapshot.account_key == account_key,
            )
            .order_by(
                ConfirmedPositionSnapshot.query_completed_at.desc(),
                ConfirmedPositionSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        return None if row is None else _load_snapshot_record(session, row)


def get_latest_position_snapshot_state(
    account_key: str,
) -> PositionSnapshotLatestState:
    """Read latest snapshot and Journal continuity in one DB transaction.

    This aggregate is the authority for API currentness classification.  Two
    independent repository calls could otherwise straddle a newly appended
    Journal refresh publication and briefly label an old account snapshot as
    current.
    """
    account_key = _text(account_key, field_name="account_key")
    init_position_snapshot_schema()
    db = get_db()
    if not db._is_sqlite_engine:
        raise PositionSnapshotError(
            "atomic latest position currentness currently requires SQLite"
        )
    with db.session_scope() as session:
        # Pin both SELECTs to one SQLite read snapshot.  This is deliberately
        # not two convenience-repository calls: a publication append between
        # them must become visible only on the next API request.
        session.connection().exec_driver_sql("BEGIN")
        publication_row = _latest_refresh_publication_row(
            session,
            account_key=account_key,
        )
        snapshot_row = session.execute(
            select(ConfirmedPositionSnapshot)
            .where(
                ConfirmedPositionSnapshot.broker == "moomoo",
                ConfirmedPositionSnapshot.account_key == account_key,
            )
            .order_by(
                ConfirmedPositionSnapshot.query_completed_at.desc(),
                ConfirmedPositionSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        publication = (
            None
            if publication_row is None
            else PositionSnapshotPublicationRecord(
                publication_id=int(publication_row.id),
                publication_key=_publication_key(publication_row),
                account_binding=_hash(
                    publication_row.account_binding,
                    field_name="refresh publication account binding",
                ),
                recorded_at=_db_utc(publication_row.recorded_at),
            )
        )
        snapshot = (
            None
            if snapshot_row is None
            else _load_snapshot_record(session, snapshot_row)
        )
        return PositionSnapshotLatestState(
            snapshot=snapshot,
            publication=publication,
        )


def list_position_snapshots(
    account_key: str,
    *,
    limit: int = 50,
) -> tuple[PositionSnapshotRecord, ...]:
    if limit < 1 or limit > 200:
        raise PositionSnapshotError("position snapshot list limit must be between 1 and 200")
    init_position_snapshot_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = tuple(
            session.execute(
                select(ConfirmedPositionSnapshot)
                .where(
                    ConfirmedPositionSnapshot.broker == "moomoo",
                    ConfirmedPositionSnapshot.account_key == account_key,
                )
                .order_by(
                    ConfirmedPositionSnapshot.query_completed_at.desc(),
                    ConfirmedPositionSnapshot.id.desc(),
                )
                .limit(limit)
            ).scalars()
        )
        return tuple(_load_snapshot_record(session, row) for row in rows)
