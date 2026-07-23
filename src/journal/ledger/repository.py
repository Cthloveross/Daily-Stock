# -*- coding: utf-8 -*-
"""Append-only persistence for the Journal v2 broker-evidence ledger.

The repository never updates or deletes ledger rows.  Re-importing identical
source bytes with the same parser contract returns the existing batch.  CSV
orders with aggregate-only execution evidence are preserved as order
observations and never converted into synthetic fills.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import and_, select

from src.journal.brokers.moomoo_openapi_export import OpenApiExportPreview

from src.journal.brokers.moomoo_statement import (
    StatementOrder,
    StatementParseResult,
    StatementReconciliation,
)
from src.journal.ledger.models import (
    BrokerFillObservation,
    BrokerFeeObservation,
    BrokerOrderObservation,
    CanonicalEvidenceIssueRecord,
    CanonicalEvidenceMemberRecord,
    CanonicalEvidenceProvenanceRecord,
    CanonicalEvidenceSetRecord,
    DealIdentityLink,
    ImportBatch,
    OrderFillSetAttestation,
    OrderIdentityLink,
    ReconciliationAttestation,
)
from src.options.occ_parser import parse_symbol
from src.storage import Base, get_db

__all__ = [
    "DEFAULT_LEDGER_ACCOUNT_KEY",
    "LedgerImportError",
    "LedgerImportResult",
    "LedgerOpenApiImportResult",
    "LedgerAttestationResult",
    "LedgerDataHealth",
    "OrderIdentityLinkResult",
    "DealIdentityLinkResult",
    "FillSetAttestationResult",
    "CanonicalObservationInputs",
    "CanonicalSetPersistenceResult",
    "OpenApiCanonicalBundleResult",
    "append_reconciliation_attestation",
    "append_openapi_batch",
    "append_order_identity_link",
    "append_deal_identity_link",
    "append_order_fill_set_attestation",
    "load_canonical_observation_inputs",
    "append_canonical_evidence_set",
    "append_openapi_canonical_bundle",
    "get_latest_canonical_evidence_set",
    "get_latest_data_health",
    "init_ledger_schema",
    "import_statement_batch",
]


DEFAULT_LEDGER_ACCOUNT_KEY = "default_moomoo_us"
STATEMENT_SOURCE_SCHEMA = "moomoo.history.csv.v1"
_APPEND_ONLY_TABLE_NAMES = (
    "journal_v2_import_batches",
    "journal_v2_reconciliation_attestations",
    "journal_v2_broker_order_observations",
    "journal_v2_broker_fill_observations",
    "journal_v2_broker_fee_observations",
    "journal_v2_order_identity_links",
    "journal_v2_deal_identity_links",
    "journal_v2_order_fill_set_attestations",
    "journal_v2_canonical_evidence_sets",
    "journal_v2_canonical_evidence_members",
    "journal_v2_canonical_evidence_issues",
    "journal_v2_canonical_evidence_provenance",
    "journal_v2_episode_builds",
    "journal_v2_episode_build_canonical_sources",
    "journal_v2_strategy_episodes",
    "journal_v2_position_episodes",
    "journal_v2_position_episode_evidence",
)


class LedgerImportError(ValueError):
    """Raised before a blocked or unconfirmed partial batch can be written."""


@dataclass(frozen=True)
class LedgerImportResult:
    batch_id: int
    batch_key: str
    duplicate: bool
    analysis_level: str
    order_observations: int
    fill_observations: int
    journal_legacy_written: bool = False


@dataclass(frozen=True)
class LedgerOpenApiImportResult:
    batch_id: int
    batch_key: str
    duplicate: bool
    analysis_level: str
    order_observations: int
    fill_observations: int
    fee_observations: int
    journal_legacy_written: bool = False


@dataclass(frozen=True)
class LedgerAttestationResult:
    attestation_id: int
    duplicate: bool
    status: str


@dataclass(frozen=True)
class LedgerDataHealth:
    batch_id: int
    analysis_level: str
    reconciliation_status: str
    reconciliation_scope: str
    reconciliation_window_start: Optional[datetime]
    reconciliation_window_end: Optional[datetime]
    reconciled_order_observations: int
    completeness_score: Decimal
    order_observations: int
    fill_observations: int
    aggregate_only_filled_orders: int
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    recorded_at: datetime


@dataclass(frozen=True)
class OrderIdentityLinkResult:
    link_id: int
    link_key: str
    duplicate: bool
    canonical_order_id: str


@dataclass(frozen=True)
class FillSetAttestationResult:
    attestation_id: int
    attestation_key: str
    duplicate: bool
    status: str


@dataclass(frozen=True)
class CanonicalObservationInputs:
    """Database-neutral observations ready for the pure canonical reader."""

    orders: tuple[Any, ...]
    fills: tuple[Any, ...]
    source_batch_ids: tuple[int, ...]
    csv_baseline_batch_id: Optional[int]


@dataclass(frozen=True)
class CanonicalSetPersistenceResult:
    canonical_set_id: int
    set_key: str
    canonical_set_sha256: str
    duplicate: bool
    analysis_ready: bool
    issue_count: int


@dataclass(frozen=True)
class DealIdentityLinkResult:
    link_id: int
    link_key: str
    duplicate: bool
    canonical_order_id: str
    canonical_deal_id: str


@dataclass(frozen=True)
class OpenApiCanonicalBundleResult:
    import_result: LedgerOpenApiImportResult
    order_identity_links: tuple[OrderIdentityLinkResult, ...]
    deal_identity_links: tuple[DealIdentityLinkResult, ...]
    fill_set_attestations: tuple[FillSetAttestationResult, ...]
    canonical_set: CanonicalSetPersistenceResult
    source_batch_ids: tuple[int, ...]
    csv_baseline_batch_id: int


def _utc_from_database(value: Optional[datetime]) -> Optional[datetime]:
    """Restore the UTC contract after SQLite strips timezone offsets."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _reconciliation_health(
    batch: ImportBatch,
    status: str,
    reconciliation_json: str,
) -> tuple[str, Optional[datetime], Optional[datetime], int]:
    """Describe whether a passed attestation covers all or part of a batch."""
    if status == "not_run":
        return "not_run", None, None, 0
    try:
        payload = json.loads(reconciliation_json)
        window = payload.get("window") or {}
        orders = payload.get("orders") or {}
        start = datetime.fromisoformat(str(window["start"]))
        end = datetime.fromisoformat(str(window["end"]))
        reconciled_orders = int(orders.get("statement", 0))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "unknown", None, None, 0
    scope = (
        "full_batch"
        if reconciled_orders == int(batch.order_observation_count)
        else "partial_window"
    )
    return scope, start, end, reconciled_orders


def get_latest_data_health(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> Optional[LedgerDataHealth]:
    """Return the newest accepted v2 evidence batch without exposing IDs."""
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        batch = session.execute(
            select(ImportBatch)
            .where(
                ImportBatch.account_key == account_key,
                ImportBatch.status == "accepted",
                # Data Health remains anchored to the complete statement
                # snapshot until a versioned cross-source canonical reader is
                # available.  A later OpenAPI observation batch covers only a
                # bounded window and must not silently replace the CSV-wide
                # health denominator.
                ImportBatch.source_kind == "csv",
            )
            .order_by(ImportBatch.recorded_at.desc(), ImportBatch.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if batch is None:
            return None
        attestation = session.execute(
            select(ReconciliationAttestation)
            .where(ReconciliationAttestation.import_batch_id == batch.id)
            .order_by(
                ReconciliationAttestation.recorded_at.desc(),
                ReconciliationAttestation.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        reconciliation_status = (
            str(attestation.status)
            if attestation is not None
            else str(batch.reconciliation_status)
        )
        reconciliation_json = (
            str(attestation.reconciliation_json)
            if attestation is not None
            else str(batch.reconciliation_json)
        )
        try:
            completeness = json.loads(batch.completeness_json)
        except (TypeError, json.JSONDecodeError):
            completeness = {}
        (
            reconciliation_scope,
            reconciliation_window_start,
            reconciliation_window_end,
            reconciled_orders,
        ) = _reconciliation_health(
            batch,
            reconciliation_status,
            reconciliation_json,
        )
        return LedgerDataHealth(
            batch_id=int(batch.id),
            analysis_level=str(batch.analysis_level),
            reconciliation_status=reconciliation_status,
            reconciliation_scope=reconciliation_scope,
            reconciliation_window_start=reconciliation_window_start,
            reconciliation_window_end=reconciliation_window_end,
            reconciled_order_observations=reconciled_orders,
            completeness_score=Decimal(batch.completeness_score),
            order_observations=int(batch.order_observation_count),
            fill_observations=int(batch.fill_observation_count),
            aggregate_only_filled_orders=int(
                completeness.get("aggregate_only_filled_orders", 0)
            ),
            window_start=_utc_from_database(batch.window_start),
            window_end=_utc_from_database(batch.window_end),
            recorded_at=_utc_from_database(batch.recorded_at),
        )


def init_ledger_schema() -> None:
    """Create only missing tables; existing legacy Journal rows are untouched."""
    db = get_db()
    Base.metadata.create_all(db._engine)
    if db._engine.dialect.name == "sqlite":
        with db._engine.begin() as connection:
            for table_name in _APPEND_ONLY_TABLE_NAMES:
                for operation in ("UPDATE", "DELETE"):
                    trigger_name = (
                        f"trg_{table_name}_{operation.lower()}_immutable"
                    )
                    connection.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                        f"BEFORE {operation} ON {table_name} "
                        "BEGIN SELECT RAISE(ABORT, "
                        "'journal v2 rows are append-only'); END"
                    )


@contextmanager
def _ledger_session(existing_session: Any = None):
    """Reuse a caller transaction or open the repository's normal scope."""
    if existing_session is not None:
        yield existing_session
        return
    db = get_db()
    with db.session_scope() as session:
        yield session


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


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise LedgerImportError("source timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _analysis_level(statement: StatementParseResult) -> str:
    summary = statement.summary()
    if (
        summary["orders_total"] == 0
        or summary["inconsistent_filled_orders"]
        or summary["orphan_fill_rows"]
        or statement.warnings
    ):
        return "blocked"
    if summary["aggregate_only_filled_orders"]:
        return "partial"
    return "exact"


def _completeness_score(statement: StatementParseResult) -> Decimal:
    summary = statement.summary()
    filled = int(summary["filled_orders"])
    if filled <= 0:
        return Decimal("0")
    detailed = int(summary["detail_backed_filled_orders"])
    return (Decimal(detailed) / Decimal(filled)).quantize(Decimal("0.0001"))


def _batch_key(statement: StatementParseResult, account_key: str) -> str:
    return _sha256_json(
        {
            "account_key": account_key,
            "broker": "moomoo",
            "parser_version": statement.parser_version,
            "source_sha256": statement.source_sha256,
        }
    )


def _instrument_fields(order: StatementOrder) -> dict[str, Any]:
    try:
        instrument = parse_symbol(order.symbol)
    except ValueError as exc:
        raise LedgerImportError(
            f"unrecognized instrument on source row {order.source_row}"
        ) from exc
    if instrument.is_option and instrument.option is not None:
        return {
            "raw_symbol": instrument.raw_symbol,
            "asset_type": "option",
            "underlying": instrument.underlying,
            "expiry": instrument.option.expiry,
            "strike": Decimal(str(instrument.option.strike)),
            "option_right": instrument.option.right,
            # Moomoo's CSV does not state the multiplier.  Do not silently
            # turn the common 100 convention into broker-sourced evidence.
            "contract_multiplier": None,
        }
    return {
        "raw_symbol": instrument.raw_symbol,
        "asset_type": "equity",
        "underlying": instrument.underlying,
        "expiry": None,
        "strike": None,
        "option_right": None,
        "contract_multiplier": None,
    }


def _normalized_broker_symbol(value: str) -> str:
    """Strip a market namespace while retaining the broker's raw field."""
    normalized = value.strip().upper()
    if "." in normalized:
        prefix, remainder = normalized.split(".", 1)
        if prefix in {"US", "HK", "SH", "SZ"} and remainder:
            return remainder
    return normalized


def _openapi_instrument_fields(raw_symbol: str) -> dict[str, Any]:
    symbol = _normalized_broker_symbol(raw_symbol)
    try:
        instrument = parse_symbol(symbol)
    except ValueError as exc:
        raise LedgerImportError(
            f"unrecognized OpenAPI instrument: {raw_symbol}"
        ) from exc
    if instrument.is_option and instrument.option is not None:
        return {
            "raw_symbol": raw_symbol.strip().upper(),
            "asset_type": "option",
            "underlying": instrument.underlying,
            "expiry": instrument.option.expiry,
            "strike": Decimal(str(instrument.option.strike)),
            "option_right": instrument.option.right,
            # The read-only export does not contain a contract definition.
            "contract_multiplier": None,
        }
    return {
        "raw_symbol": raw_symbol.strip().upper(),
        "asset_type": "equity",
        "underlying": instrument.underlying,
        "expiry": None,
        "strike": None,
        "option_right": None,
        "contract_multiplier": None,
    }


def _order_record(order: StatementOrder) -> dict[str, Any]:
    return {
        "derived_order_id": order.derived_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "status": order.status,
        "order_quantity": order.order_quantity,
        "order_price_text": order.order_price_text,
        "order_amount": order.order_amount,
        "order_time": order.order_time,
        "summary_filled_quantity": order.summary_filled_quantity,
        "summary_average_price": order.summary_average_price,
        "total_fee": order.total_fee,
        "fee_components": dict(order.fee_components),
        "evidence_level": order.evidence_level,
        "evidence_warnings": list(order.evidence_warnings),
    }


def _fill_key(order: StatementOrder, fill: Any) -> str:
    return "moomoo_csv_fill_" + _sha256_json(
        {
            "order_id": order.derived_order_id,
            "source_row": fill.source_row,
            "quantity": fill.quantity,
            "price": fill.price,
            "filled_at": fill.filled_at,
        }
    )[:20]


def _reconciliation_payload(
    reconciliation: Optional[StatementReconciliation],
) -> tuple[str, dict[str, Any]]:
    if reconciliation is None:
        return "not_run", {"status": "not_run"}
    summary = reconciliation.summary()
    status = "passed" if reconciliation.analysis_ready else "failed"
    return status, summary


def _validate_optional_sha256(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise LedgerImportError("reconciliation source hash must be SHA-256")
    return normalized


def _append_reconciliation_attestation(
    session: Any,
    batch: ImportBatch,
    reconciliation: StatementReconciliation,
    source_sha256: Optional[str],
) -> LedgerAttestationResult:
    source_sha256 = _validate_optional_sha256(source_sha256)
    if source_sha256 is None:
        raise LedgerImportError(
            "reconciliation attestation requires the read-only export SHA-256"
        )
    _validate_attestation_binding(session, batch, reconciliation)
    status, payload = _reconciliation_payload(reconciliation)
    reconciliation_sha256 = _sha256_json(payload)
    key = _sha256_json(
        {
            "batch_key": batch.batch_key,
            "source_sha256": source_sha256,
            "reconciliation_sha256": reconciliation_sha256,
        }
    )
    existing = session.execute(
        select(ReconciliationAttestation).where(
            ReconciliationAttestation.attestation_key == key
        )
    ).scalar_one_or_none()
    if existing is not None:
        return LedgerAttestationResult(
            attestation_id=int(existing.id),
            duplicate=True,
            status=str(existing.status),
        )
    row = ReconciliationAttestation(
        attestation_key=key,
        import_batch_id=batch.id,
        broker=str(batch.broker),
        account_key=str(batch.account_key),
        source_kind="openapi_readonly_export",
        source_sha256=source_sha256,
        reconciliation_sha256=reconciliation_sha256,
        window_start=_to_utc(reconciliation.window_start),
        window_end=_to_utc(reconciliation.window_end),
        status=status,
        matched_order_count=int(reconciliation.matched_orders),
        reconciliation_json=_canonical_json(payload),
    )
    session.add(row)
    session.flush()
    return LedgerAttestationResult(
        attestation_id=int(row.id),
        duplicate=False,
        status=status,
    )


def append_reconciliation_attestation(
    batch_id: int,
    reconciliation: StatementReconciliation,
    *,
    source_sha256: str,
) -> LedgerAttestationResult:
    """Append later cross-source evidence without mutating its CSV batch."""
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        batch = session.get(ImportBatch, batch_id)
        if batch is None:
            raise LedgerImportError("import batch does not exist")
        return _append_reconciliation_attestation(
            session,
            batch,
            reconciliation,
            source_sha256,
        )


def _validate_attestation_binding(
    session: Any,
    batch: ImportBatch,
    reconciliation: StatementReconciliation,
) -> None:
    """Prove that an attestation's statement side belongs to this batch."""
    window_start = _to_utc(reconciliation.window_start)
    window_end = _to_utc(reconciliation.window_end)
    batch_start = _utc_from_database(batch.window_start)
    batch_end = _utc_from_database(batch.window_end)
    if batch_start is None or batch_end is None:
        raise LedgerImportError("import batch has no evidence window")
    if window_end < batch_start or window_start > batch_end:
        raise LedgerImportError(
            "reconciliation window does not overlap the import batch"
        )
    rows = session.execute(
        select(
            BrokerOrderObservation.source_order_id,
            BrokerOrderObservation.ordered_at,
        ).where(BrokerOrderObservation.import_batch_id == batch.id)
    ).all()
    window_order_ids = {
        str(source_order_id)
        for source_order_id, ordered_at in rows
        if ordered_at is not None
        and window_start <= _utc_from_database(ordered_at) <= window_end
    }
    matches = reconciliation.matches
    statement_match_ids = [match.statement_order_id for match in matches]
    broker_match_ids = [match.broker_order_id for match in matches]
    if reconciliation.statement_orders != len(window_order_ids):
        raise LedgerImportError(
            "reconciliation statement count does not match the batch window"
        )
    if (
        reconciliation.matched_orders != len(matches)
        or len(statement_match_ids) != len(set(statement_match_ids))
        or len(broker_match_ids) != len(set(broker_match_ids))
        or any(not value for value in statement_match_ids + broker_match_ids)
        or not set(statement_match_ids).issubset(window_order_ids)
    ):
        raise LedgerImportError(
            "reconciliation order identities do not bind to the import batch"
        )
    if reconciliation.analysis_ready and (
        reconciliation.statement_only_orders != 0
        or reconciliation.api_only_orders != 0
        or reconciliation.statement_orders != reconciliation.api_orders
        or reconciliation.matched_orders != reconciliation.statement_orders
        or set(statement_match_ids) != window_order_ids
    ):
        raise LedgerImportError(
            "passed reconciliation does not cover every order in its batch window"
        )


def import_statement_batch(
    statement: StatementParseResult,
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    allow_partial: bool = False,
    reconciliation: Optional[StatementReconciliation] = None,
    reconciliation_source_sha256: Optional[str] = None,
) -> LedgerImportResult:
    """Persist one immutable Moomoo CSV observation batch.

    ``allow_partial`` is required when older filled orders have only aggregate
    quantity/average-price evidence.  Blocked input is never persisted.
    """
    account_key = account_key.strip()
    if not account_key:
        raise LedgerImportError("account_key cannot be empty")
    level = _analysis_level(statement)
    if level == "blocked":
        raise LedgerImportError("statement evidence is blocked")
    if level == "partial" and not allow_partial:
        raise LedgerImportError(
            "statement contains aggregate-only evidence; preview and explicitly "
            "allow a partial import"
        )

    init_ledger_schema()
    key = _batch_key(statement, account_key)
    db = get_db()
    with db.session_scope() as session:
        existing = session.execute(
            select(ImportBatch).where(ImportBatch.batch_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            if reconciliation is not None:
                _append_reconciliation_attestation(
                    session,
                    existing,
                    reconciliation,
                    reconciliation_source_sha256,
                )
            return LedgerImportResult(
                batch_id=int(existing.id),
                batch_key=key,
                duplicate=True,
                analysis_level=str(existing.analysis_level),
                order_observations=int(existing.order_observation_count),
                fill_observations=int(existing.fill_observation_count),
            )

        order_times = [order.order_time for order in statement.orders]
        fill_count = sum(len(order.fills) for order in statement.orders)
        summary = statement.summary()
        reconciliation_status, reconciliation_json = _reconciliation_payload(
            reconciliation
        )
        score = _completeness_score(statement)
        batch = ImportBatch(
            batch_key=key,
            broker="moomoo",
            account_key=account_key,
            source_kind="csv",
            source_schema=STATEMENT_SOURCE_SCHEMA,
            source_sha256=statement.source_sha256,
            parser_name="moomoo_statement",
            parser_version=statement.parser_version,
            window_start=_to_utc(min(order_times)),
            window_end=_to_utc(max(order_times)),
            source_timezone="America/New_York",
            status="accepted",
            analysis_level=level,
            analysis_ready=level in {"exact", "partial"},
            order_observation_count=len(statement.orders),
            fill_observation_count=fill_count,
            rejected_record_count=0,
            reconciliation_status=reconciliation_status,
            reconciliation_json=_canonical_json(reconciliation_json),
            completeness_score=score,
            completeness_json=_canonical_json(
                {
                    "detail_backed_filled_orders": summary[
                        "detail_backed_filled_orders"
                    ],
                    "aggregate_only_filled_orders": summary[
                        "aggregate_only_filled_orders"
                    ],
                    "inconsistent_filled_orders": summary[
                        "inconsistent_filled_orders"
                    ],
                }
            ),
            warnings_json=_canonical_json(summary["warnings"]),
            provenance_json=_canonical_json(
                {
                    "broker": "moomoo",
                    "source_kind": "csv",
                    "source_schema": STATEMENT_SOURCE_SCHEMA,
                    "source_sha256": statement.source_sha256,
                    "parser_version": statement.parser_version,
                    "source_path_retained": False,
                }
            ),
        )
        session.add(batch)
        session.flush()

        for order in statement.orders:
            instrument = _instrument_fields(order)
            order_record = _order_record(order)
            order_score = {
                "fill_detail": Decimal("1"),
                "aggregate_only": Decimal("0.6500"),
                "not_filled": Decimal("1"),
            }.get(order.evidence_level, Decimal("0"))
            fee_status = (
                "complete" if order.has_execution_evidence else "not_applicable"
            )
            order_row = BrokerOrderObservation(
                import_batch_id=batch.id,
                observation_key=order.derived_order_id,
                broker="moomoo",
                account_key=account_key,
                source_order_id=order.derived_order_id,
                identity_strength="derived_strong",
                source_row_number=order.source_row,
                source_updated_at=None,
                **instrument,
                side=order.side,
                status=order.status,
                order_type=order.order_type or None,
                time_in_force=order.time_in_force or None,
                session=order.session or None,
                currency=order.currency or "USD",
                ordered_at=_to_utc(order.order_time),
                order_quantity=order.order_quantity,
                order_price=order.order_price,
                order_amount=order.order_amount,
                summary_filled_quantity=order.summary_filled_quantity,
                summary_average_fill_price=order.summary_average_price,
                total_fee=order.total_fee,
                fee_components_json=_canonical_json(dict(order.fee_components)),
                evidence_level=order.evidence_level,
                fee_evidence_status=fee_status,
                evidence_json=_canonical_json(
                    {
                        "warnings": list(order.evidence_warnings),
                        "fill_record_count": len(order.fills),
                        "summary_quantity": order.summary_filled_quantity,
                        "summary_average_price": order.summary_average_price,
                    }
                ),
                completeness_score=order_score,
                completeness_json=_canonical_json(
                    {
                        "fill_evidence": order.evidence_level,
                        "fee_evidence": fee_status,
                    }
                ),
                provenance_json=_canonical_json(
                    {
                        "batch_key": key,
                        "source": "moomoo_csv",
                        "identity_basis": "symbol_side_quantity_order_time",
                    }
                ),
                source_record_sha256=_sha256_json(order_record),
                raw_payload_json=None,
            )
            session.add(order_row)
            session.flush()

            for fill in order.fills:
                fill_key = _fill_key(order, fill)
                fill_record = {
                    "order_id": order.derived_order_id,
                    "source_row": fill.source_row,
                    "quantity": fill.quantity,
                    "price": fill.price,
                    "amount": fill.amount,
                    "filled_at": fill.filled_at,
                    "currency": fill.currency or order.currency,
                }
                session.add(
                    BrokerFillObservation(
                        import_batch_id=batch.id,
                        broker_order_observation_id=order_row.id,
                        observation_key=fill_key,
                        broker="moomoo",
                        account_key=account_key,
                        source_order_id=order.derived_order_id,
                        source_deal_id=fill_key,
                        identity_strength="derived_weak",
                        source_row_number=fill.source_row,
                        **instrument,
                        side=order.side,
                        activity_kind="trade_fill",
                        source_status=None,
                        filled_at=_to_utc(fill.filled_at),
                        quantity=fill.quantity,
                        price=fill.price,
                        amount=fill.amount,
                        currency=fill.currency or order.currency or "USD",
                        total_fee=None,
                        fee_components_json=None,
                        fee_allocation_method=None,
                        evidence_level="fill_detail",
                        evidence_json=_canonical_json(
                            {
                                "source_time_semantics": "csv_fill_time",
                                "source_row": fill.source_row,
                            }
                        ),
                        completeness_score=Decimal("1"),
                        completeness_json=_canonical_json(
                            {
                                "quantity": "exact",
                                "price": "exact",
                                "time": "csv_fill_time",
                                "fee": "order_level_only",
                            }
                        ),
                        provenance_json=_canonical_json(
                            {
                                "batch_key": key,
                                "source": "moomoo_csv",
                                "identity_basis": "derived_row_evidence",
                            }
                        ),
                        source_record_sha256=_sha256_json(fill_record),
                        raw_payload_json=None,
                    )
                )

        if reconciliation is not None:
            _append_reconciliation_attestation(
                session,
                batch,
                reconciliation,
                reconciliation_source_sha256,
            )

        return LedgerImportResult(
            batch_id=int(batch.id),
            batch_key=key,
            duplicate=False,
            analysis_level=level,
            order_observations=len(statement.orders),
            fill_observations=fill_count,
        )


def _require_confirmed(confirmed: bool, operation: str) -> None:
    if confirmed is not True:
        raise LedgerImportError(
            f"{operation} requires explicit confirmation before database write"
        )


def _storage_openapi_batch_key(
    preview: OpenApiExportPreview,
    account_key: str,
) -> str:
    return _sha256_json(
        {
            "account_key": account_key,
            "broker": "moomoo",
            "parser_batch_key": preview.metadata.batch_key,
        }
    )


def _openapi_observation_key(kind: str, stable_id: str) -> str:
    return f"moomoo_openapi_{kind}_" + _sha256_json(
        {"kind": kind, "stable_id": stable_id}
    )[:32]


def _as_decimal(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(value)


def _multiplier_instrument_key(value: Any) -> tuple[Any, ...]:
    return (
        str(value.asset_type).strip().lower(),
        str(value.underlying).strip().upper(),
        value.expiry,
        _as_decimal(value.strike),
        None
        if value.option_right is None
        else str(value.option_right).strip().upper(),
        str(value.currency).strip().upper(),
    )


def _multiplier_from_amount(
    amount: Any,
    quantity: Any,
    price: Any,
) -> Optional[Decimal]:
    amount_value = _as_decimal(amount)
    quantity_value = _as_decimal(quantity)
    price_value = _as_decimal(price)
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
    candidates = (Decimal("1"), Decimal("100"))
    candidate = min(
        candidates,
        key=lambda item: abs(observed - denominator * item),
    )
    if abs(observed - denominator * candidate) > Decimal("0.005"):
        return None
    return candidate


def _evidence_multiplier_map(
    orders: Sequence[BrokerOrderObservation],
    fills: Sequence[BrokerFillObservation],
) -> dict[tuple[Any, ...], Decimal]:
    proofs: dict[tuple[Any, ...], set[Decimal]] = {}
    for row in fills:
        value = _multiplier_from_amount(row.amount, row.quantity, row.price)
        if value is not None:
            proofs.setdefault(_multiplier_instrument_key(row), set()).add(value)
    for row in orders:
        value = _multiplier_from_amount(
            row.order_amount,
            row.summary_filled_quantity or row.order_quantity,
            row.summary_average_fill_price or row.order_price,
        )
        if value is not None:
            proofs.setdefault(_multiplier_instrument_key(row), set()).add(value)
    if any(len(values) != 1 for values in proofs.values()):
        raise LedgerImportError("contract multiplier evidence disagrees")
    return {key: next(iter(values)) for key, values in proofs.items()}


def _same_timestamp(left: datetime, right: datetime) -> bool:
    left_utc = _utc_from_database(left)
    right_utc = _utc_from_database(right)
    assert left_utc is not None and right_utc is not None
    return left_utc == right_utc


def _validate_openapi_cross_batch_conflicts(
    session: Any,
    preview: OpenApiExportPreview,
    account_key: str,
) -> None:
    """Block stable broker identities that disagree on economic facts."""
    incoming_orders = {
        order.source_order_id: order for order in preview.orders
    }
    existing_orders = session.execute(
        select(BrokerOrderObservation)
        .join(ImportBatch, ImportBatch.id == BrokerOrderObservation.import_batch_id)
        .where(
            BrokerOrderObservation.broker == "moomoo",
            BrokerOrderObservation.account_key == account_key,
            ImportBatch.source_kind == "openapi",
        )
    ).scalars().all()
    for existing in existing_orders:
        incoming = incoming_orders.get(str(existing.source_order_id))
        if incoming is None:
            continue
        incoming_instrument = _openapi_instrument_fields(incoming.raw_symbol)
        conflicts = (
            _normalized_broker_symbol(str(existing.raw_symbol))
            != _normalized_broker_symbol(incoming.raw_symbol)
            or str(existing.asset_type) != incoming_instrument["asset_type"]
            or str(existing.underlying) != incoming_instrument["underlying"]
            or str(existing.side).upper() != incoming.side.upper()
            or Decimal(existing.order_quantity) != incoming.order_quantity
            or str(existing.currency).upper() != incoming.currency.upper()
            or not _same_timestamp(existing.ordered_at, incoming.ordered_at)
        )
        if conflicts:
            raise LedgerImportError(
                "OpenAPI order identity conflicts with previously accepted evidence: "
                f"{incoming.source_order_id}"
            )

    incoming_fills = {
        fill.source_deal_id: fill for fill in preview.fills
    }
    existing_fills = session.execute(
        select(BrokerFillObservation)
        .join(ImportBatch, ImportBatch.id == BrokerFillObservation.import_batch_id)
        .where(
            BrokerFillObservation.broker == "moomoo",
            BrokerFillObservation.account_key == account_key,
            ImportBatch.source_kind == "openapi",
        )
    ).scalars().all()
    for existing in existing_fills:
        incoming = incoming_fills.get(str(existing.source_deal_id))
        if (
            incoming is not None
            and str(existing.source_record_sha256)
            != incoming.source_record_sha256
        ):
            raise LedgerImportError(
                "OpenAPI deal identity conflicts with previously accepted evidence: "
                f"{incoming.source_deal_id}"
            )

    incoming_fees = {
        fee.source_order_id: fee for fee in preview.fees
    }
    existing_fees = session.execute(
        select(BrokerFeeObservation)
        .join(ImportBatch, ImportBatch.id == BrokerFeeObservation.import_batch_id)
        .where(
            BrokerFeeObservation.broker == "moomoo",
            BrokerFeeObservation.account_key == account_key,
            ImportBatch.source_kind == "openapi",
        )
    ).scalars().all()
    for existing in existing_fees:
        incoming = incoming_fees.get(str(existing.source_order_id))
        if (
            incoming is not None
            and str(existing.source_record_sha256)
            != incoming.source_record_sha256
        ):
            raise LedgerImportError(
                "OpenAPI fee evidence conflicts with a previously accepted order: "
                f"{incoming.source_order_id}"
            )


def append_openapi_batch(
    preview: OpenApiExportPreview,
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    confirmed: bool = False,
    _session: Any = None,
) -> LedgerOpenApiImportResult:
    """Append one strictly parsed read-only export after explicit confirmation.

    Replaying economically identical evidence is idempotent even when a new
    probe has a different ``generated_at`` and therefore a different complete
    source hash.  The parser's evidence batch key plus the local account scope
    form the storage identity.
    """
    _require_confirmed(confirmed, "OpenAPI import")
    if not isinstance(preview, OpenApiExportPreview):
        raise LedgerImportError("OpenAPI import requires a parsed preview")
    account_key = account_key.strip()
    if not account_key:
        raise LedgerImportError("account_key cannot be empty")
    metadata = preview.metadata
    if not metadata.analysis_ready or metadata.analysis_level != "exact":
        raise LedgerImportError("OpenAPI evidence is blocked and cannot be imported")
    if metadata.reconciliation_status != "passed":
        raise LedgerImportError("OpenAPI reconciliation must pass before import")
    if metadata.journal_database_written is not False:
        raise LedgerImportError("OpenAPI preview does not preserve the read-only boundary")
    if (
        metadata.order_observation_count != len(preview.orders)
        or metadata.fill_observation_count != len(preview.fills)
        or metadata.fee_observation_count != len(preview.fees)
    ):
        raise LedgerImportError("OpenAPI preview counts are internally inconsistent")
    if any(fee.total_fee < 0 for fee in preview.fees):
        raise LedgerImportError("OpenAPI total fee cannot be negative")

    if _session is None:
        init_ledger_schema()
    storage_key = _storage_openapi_batch_key(preview, account_key)
    with _ledger_session(_session) as session:
        existing = session.execute(
            select(ImportBatch).where(ImportBatch.batch_key == storage_key)
        ).scalar_one_or_none()
        if existing is not None:
            if (
                str(existing.account_key) != account_key
                or str(existing.source_kind) != "openapi"
                or str(existing.source_schema) != metadata.source_schema
            ):
                raise LedgerImportError("OpenAPI batch key collides with another source")
            return LedgerOpenApiImportResult(
                batch_id=int(existing.id),
                batch_key=storage_key,
                duplicate=True,
                analysis_level=str(existing.analysis_level),
                order_observations=int(existing.order_observation_count),
                fill_observations=int(existing.fill_observation_count),
                fee_observations=len(
                    session.execute(
                        select(BrokerFeeObservation).where(
                            BrokerFeeObservation.import_batch_id == existing.id
                        )
                    ).scalars().all()
                ),
            )

        _validate_openapi_cross_batch_conflicts(session, preview, account_key)
        reconciliation = metadata.reconciliation.as_dict()
        batch = ImportBatch(
            batch_key=storage_key,
            broker="moomoo",
            account_key=account_key,
            source_kind="openapi",
            source_schema=metadata.source_schema,
            source_sha256=metadata.source_sha256,
            parser_name=metadata.parser_name,
            parser_version=metadata.parser_version,
            window_start=_to_utc(metadata.window_start),
            window_end=_to_utc(metadata.window_end),
            source_timezone=metadata.source_timezone,
            status="accepted",
            analysis_level=metadata.analysis_level,
            analysis_ready=True,
            order_observation_count=len(preview.orders),
            fill_observation_count=len(preview.fills),
            rejected_record_count=metadata.rejected_record_count,
            reconciliation_status=metadata.reconciliation_status,
            reconciliation_json=_canonical_json(reconciliation),
            completeness_score=Decimal("1"),
            completeness_json=_canonical_json(
                {
                    "orders": len(preview.orders),
                    "fills": len(preview.fills),
                    "fees": len(preview.fees),
                    "rejected": metadata.rejected_record_count,
                }
            ),
            warnings_json=_canonical_json(metadata.warnings),
            provenance_json=_canonical_json(
                {
                    "broker": "moomoo",
                    "source_kind": "openapi",
                    "source_schema": metadata.source_schema,
                    "source_sha256": metadata.source_sha256,
                    "evidence_sha256": metadata.evidence_sha256,
                    "parser_batch_key": metadata.batch_key,
                    "parser_name": metadata.parser_name,
                    "parser_version": metadata.parser_version,
                    "acquired_at": metadata.generated_at,
                    "environment": metadata.environment,
                    "market": metadata.market,
                    "account_selection": metadata.account_selection,
                    "journal_database_write_confirmed": True,
                    "source_path_retained": False,
                }
            ),
        )
        session.add(batch)
        session.flush()

        fee_by_order = {
            fee.source_order_id: fee for fee in preview.fees
        }
        order_rows: dict[str, BrokerOrderObservation] = {}
        for order in preview.orders:
            instrument = _openapi_instrument_fields(order.raw_symbol)
            fee = fee_by_order.get(order.source_order_id)
            has_fills = order.summary_filled_quantity > 0
            order_row = BrokerOrderObservation(
                import_batch_id=batch.id,
                observation_key=_openapi_observation_key(
                    "order", order.source_order_id
                ),
                broker="moomoo",
                account_key=account_key,
                source_order_id=order.source_order_id,
                identity_strength="broker_stable",
                source_row_number=None,
                source_updated_at=_to_utc(order.source_updated_at),
                **instrument,
                side=order.side,
                status=order.status,
                order_type=order.order_type or None,
                time_in_force=order.time_in_force or None,
                session=order.session or None,
                currency=order.currency,
                ordered_at=_to_utc(order.ordered_at),
                order_quantity=order.order_quantity,
                order_price=order.order_price,
                # The read-only API does not expose notional or contract
                # multiplier.  Keep this NULL instead of inventing an amount.
                order_amount=None,
                summary_filled_quantity=order.summary_filled_quantity,
                summary_average_fill_price=order.summary_average_fill_price,
                total_fee=None if fee is None else fee.total_fee,
                fee_components_json=(
                    None
                    if fee is None
                    else _canonical_json(dict(fee.fee_components))
                ),
                evidence_level="fill_detail" if has_fills else "not_filled",
                fee_evidence_status=(
                    "complete" if fee is not None else "not_applicable"
                ),
                evidence_json=_canonical_json(
                    {
                        "market": order.market,
                        "stock_name": order.stock_name,
                        "fill_outside_rth": order.fill_outside_rth,
                        "summary_quantity": order.summary_filled_quantity,
                        "summary_average_price": (
                            order.summary_average_fill_price
                        ),
                    }
                ),
                completeness_score=Decimal("1"),
                completeness_json=_canonical_json(
                    {
                        "order_identity": "broker_stable",
                        "fill_evidence": (
                            "broker_deal_detail" if has_fills else "not_applicable"
                        ),
                        "fee_evidence": (
                            "broker_order_fee" if fee is not None else "not_applicable"
                        ),
                    }
                ),
                provenance_json=_canonical_json(
                    {
                        "batch_key": storage_key,
                        "parser_batch_key": metadata.batch_key,
                        "source": "moomoo_openapi_readonly",
                        "identity_basis": "broker_order_id",
                    }
                ),
                source_record_sha256=order.source_record_sha256,
                raw_payload_json=None,
            )
            session.add(order_row)
            session.flush()
            order_rows[order.source_order_id] = order_row

        for fill in preview.fills:
            parent = order_rows[fill.source_order_id]
            instrument = _openapi_instrument_fields(fill.raw_symbol)
            session.add(
                BrokerFillObservation(
                    import_batch_id=batch.id,
                    broker_order_observation_id=parent.id,
                    observation_key=_openapi_observation_key(
                        "fill", fill.source_deal_id
                    ),
                    broker="moomoo",
                    account_key=account_key,
                    source_order_id=fill.source_order_id,
                    source_deal_id=fill.source_deal_id,
                    identity_strength="broker_stable",
                    source_row_number=None,
                    **instrument,
                    side=fill.side,
                    activity_kind="trade_fill",
                    source_status=fill.source_status,
                    filled_at=_to_utc(fill.filled_at),
                    quantity=fill.quantity,
                    price=fill.price,
                    # The export does not provide an execution notional or a
                    # contract multiplier; do not manufacture one here.
                    amount=None,
                    currency=fill.currency,
                    total_fee=None,
                    fee_components_json=None,
                    fee_allocation_method=None,
                    evidence_level="broker_fill_detail",
                    evidence_json=_canonical_json(
                        {
                            "market": fill.market,
                            "stock_name": fill.stock_name,
                            "source_time_semantics": "broker_deal_create_time",
                        }
                    ),
                    completeness_score=Decimal("1"),
                    completeness_json=_canonical_json(
                        {
                            "identity": "broker_deal_id",
                            "quantity": "exact",
                            "price": "exact",
                            "time": "broker_deal_create_time",
                            "fee": "order_level_only",
                        }
                    ),
                    provenance_json=_canonical_json(
                        {
                            "batch_key": storage_key,
                            "parser_batch_key": metadata.batch_key,
                            "source": "moomoo_openapi_readonly",
                            "identity_basis": "broker_deal_id",
                        }
                    ),
                    source_record_sha256=fill.source_record_sha256,
                    raw_payload_json=None,
                )
            )

        for fee in preview.fees:
            parent = order_rows[fee.source_order_id]
            session.add(
                BrokerFeeObservation(
                    import_batch_id=batch.id,
                    broker_order_observation_id=parent.id,
                    observation_key=_openapi_observation_key(
                        "fee", fee.source_order_id
                    ),
                    broker="moomoo",
                    account_key=account_key,
                    source_order_id=fee.source_order_id,
                    currency=str(parent.currency),
                    total_fee=fee.total_fee,
                    fee_components_json=_canonical_json(
                        dict(fee.fee_components)
                    ),
                    evidence_json=_canonical_json(
                        {"component_count": len(fee.fee_components)}
                    ),
                    provenance_json=_canonical_json(
                        {
                            "batch_key": storage_key,
                            "parser_batch_key": metadata.batch_key,
                            "source": "moomoo_openapi_order_fee_query",
                            "identity_basis": "broker_order_id",
                        }
                    ),
                    source_record_sha256=fee.source_record_sha256,
                )
            )

        return LedgerOpenApiImportResult(
            batch_id=int(batch.id),
            batch_key=storage_key,
            duplicate=False,
            analysis_level=metadata.analysis_level,
            order_observations=len(preview.orders),
            fill_observations=len(preview.fills),
            fee_observations=len(preview.fees),
        )


def _order_economic_identity(row: BrokerOrderObservation) -> tuple[Any, ...]:
    ordered_at = _utc_from_database(row.ordered_at)
    assert ordered_at is not None
    return (
        str(row.broker).strip().lower(),
        str(row.account_key).strip(),
        _normalized_broker_symbol(str(row.raw_symbol)),
        str(row.asset_type).strip().lower(),
        str(row.underlying).strip().upper(),
        str(row.side).strip().upper(),
        str(row.currency).strip().upper(),
        Decimal(row.order_quantity),
        ordered_at.replace(microsecond=0),
    )


def append_order_identity_link(
    order_observation_id: int,
    counterpart_order_observation_id: int,
    *,
    canonical_order_id: str,
    link_method: str = "reconciled_economic_identity_v1",
    confirmed: bool = False,
    _session: Any = None,
) -> OrderIdentityLinkResult:
    """Link a derived order observation to one broker-stable observation.

    The repository validates the economic identity itself and refuses fuzzy or
    cross-account aliases.  Existing links are immutable: an exact retry is
    idempotent while a request to relink an observation is blocked.
    """
    _require_confirmed(confirmed, "order identity link")
    canonical_order_id = canonical_order_id.strip()
    link_method = link_method.strip()
    if not canonical_order_id or not link_method:
        raise LedgerImportError("identity link fields cannot be empty")
    if order_observation_id == counterpart_order_observation_id:
        raise LedgerImportError("identity link requires two distinct observations")

    if _session is None:
        init_ledger_schema()
    with _ledger_session(_session) as session:
        source = session.get(BrokerOrderObservation, order_observation_id)
        counterpart = session.get(
            BrokerOrderObservation,
            counterpart_order_observation_id,
        )
        if source is None or counterpart is None:
            raise LedgerImportError("identity link observation does not exist")
        existing = session.execute(
            select(OrderIdentityLink).where(
                OrderIdentityLink.broker_order_observation_id == source.id
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                str(existing.canonical_order_id) == canonical_order_id
                and int(existing.counterpart_order_observation_id)
                == int(counterpart.id)
                and str(existing.link_method) == link_method
            ):
                return OrderIdentityLinkResult(
                    link_id=int(existing.id),
                    link_key=str(existing.link_key),
                    duplicate=True,
                    canonical_order_id=canonical_order_id,
                )
            raise LedgerImportError(
                "order observation already has a different immutable identity link"
            )
        counterpart_link = session.execute(
            select(OrderIdentityLink).where(
                OrderIdentityLink.counterpart_order_observation_id
                == counterpart.id
            )
        ).scalar_one_or_none()
        if counterpart_link is not None:
            raise LedgerImportError(
                "broker order observation is already linked one-to-one"
            )

        counterpart_batch = session.get(
            ImportBatch, counterpart.import_batch_id
        )
        source_batch = session.get(ImportBatch, source.import_batch_id)
        if counterpart_batch is None or source_batch is None:
            raise LedgerImportError("identity link import batch does not exist")
        if str(counterpart_batch.source_kind) != "openapi":
            raise LedgerImportError(
                "identity link counterpart must be broker-stable OpenAPI evidence"
            )
        if str(counterpart.identity_strength) != "broker_stable":
            raise LedgerImportError("identity link counterpart is not broker-stable")
        if str(counterpart.source_order_id) != canonical_order_id:
            raise LedgerImportError(
                "canonical_order_id must equal the broker-stable counterpart ID"
            )
        if _order_economic_identity(source) != _order_economic_identity(counterpart):
            raise LedgerImportError(
                "order observations conflict on instrument, side, quantity, "
                "currency or order time"
            )

        link_key = _sha256_json(
            {
                "source_batch_key": source_batch.batch_key,
                "source_observation_key": source.observation_key,
                "counterpart_batch_key": counterpart_batch.batch_key,
                "counterpart_observation_key": counterpart.observation_key,
                "canonical_order_id": canonical_order_id,
                "link_method": link_method,
            }
        )
        row = OrderIdentityLink(
            link_key=link_key,
            broker_order_observation_id=source.id,
            counterpart_order_observation_id=counterpart.id,
            broker=str(source.broker),
            account_key=str(source.account_key),
            canonical_order_id=canonical_order_id,
            link_method=link_method,
            identity_strength="reconciled_stable",
            confidence=Decimal("1"),
            evidence_json=_canonical_json(
                {
                    "economic_identity": _order_economic_identity(source),
                    "source_record_sha256": source.source_record_sha256,
                    "counterpart_record_sha256": (
                        counterpart.source_record_sha256
                    ),
                }
            ),
            provenance_json=_canonical_json(
                {
                    "source_batch_key": source_batch.batch_key,
                    "counterpart_batch_key": counterpart_batch.batch_key,
                    "link_method": link_method,
                    "in_place_source_mutation": False,
                }
            ),
        )
        session.add(row)
        session.flush()
        return OrderIdentityLinkResult(
            link_id=int(row.id),
            link_key=link_key,
            duplicate=False,
            canonical_order_id=canonical_order_id,
        )


def _fill_economic_identity(row: BrokerFillObservation) -> tuple[Any, ...]:
    filled_at = _utc_from_database(row.filled_at)
    assert filled_at is not None
    return (
        str(row.broker).strip().lower(),
        str(row.account_key).strip(),
        _normalized_broker_symbol(str(row.raw_symbol)),
        str(row.asset_type).strip().lower(),
        str(row.underlying).strip().upper(),
        str(row.side).strip().upper(),
        str(row.currency).strip().upper(),
        Decimal(row.quantity),
        Decimal(row.price),
        filled_at.replace(microsecond=0),
    )


def append_deal_identity_link(
    fill_observation_id: int,
    counterpart_fill_observation_id: int,
    *,
    canonical_order_id: str,
    canonical_deal_id: str,
    link_method: str = "unique_exact_execution_identity_v1",
    confirmed: bool = False,
    _session: Any = None,
) -> DealIdentityLinkResult:
    """Append a strict one-to-one deal alias; ambiguous fills are rejected."""
    _require_confirmed(confirmed, "deal identity link")
    canonical_order_id = canonical_order_id.strip()
    canonical_deal_id = canonical_deal_id.strip()
    link_method = link_method.strip()
    if not canonical_order_id or not canonical_deal_id or not link_method:
        raise LedgerImportError("deal identity link fields cannot be empty")
    if fill_observation_id == counterpart_fill_observation_id:
        raise LedgerImportError("deal identity link requires distinct observations")

    if _session is None:
        init_ledger_schema()
    with _ledger_session(_session) as session:
        source = session.get(BrokerFillObservation, fill_observation_id)
        counterpart = session.get(
            BrokerFillObservation,
            counterpart_fill_observation_id,
        )
        if source is None or counterpart is None:
            raise LedgerImportError("deal identity link observation does not exist")
        existing = session.execute(
            select(DealIdentityLink).where(
                DealIdentityLink.broker_fill_observation_id == source.id
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                int(existing.counterpart_fill_observation_id)
                == int(counterpart.id)
                and str(existing.canonical_order_id) == canonical_order_id
                and str(existing.canonical_deal_id) == canonical_deal_id
                and str(existing.link_method) == link_method
            ):
                return DealIdentityLinkResult(
                    link_id=int(existing.id),
                    link_key=str(existing.link_key),
                    duplicate=True,
                    canonical_order_id=canonical_order_id,
                    canonical_deal_id=canonical_deal_id,
                )
            raise LedgerImportError(
                "fill observation already has a different immutable deal link"
            )

        counterpart_link = session.execute(
            select(DealIdentityLink).where(
                DealIdentityLink.counterpart_fill_observation_id
                == counterpart.id
            )
        ).scalar_one_or_none()
        if counterpart_link is not None:
            raise LedgerImportError(
                "broker deal observation is already linked one-to-one"
            )
        source_batch = session.get(ImportBatch, source.import_batch_id)
        counterpart_batch = session.get(
            ImportBatch, counterpart.import_batch_id
        )
        if source_batch is None or counterpart_batch is None:
            raise LedgerImportError("deal identity link import batch does not exist")
        if (
            str(source_batch.source_kind) != "csv"
            or str(counterpart_batch.source_kind) != "openapi"
            or str(counterpart.identity_strength) != "broker_stable"
        ):
            raise LedgerImportError(
                "deal link roles must be CSV derived and OpenAPI broker-stable"
            )
        if str(counterpart.source_deal_id) != canonical_deal_id:
            raise LedgerImportError(
                "canonical_deal_id must equal the broker-stable counterpart ID"
            )
        order_link = session.execute(
            select(OrderIdentityLink).where(
                OrderIdentityLink.broker_order_observation_id
                == source.broker_order_observation_id,
                OrderIdentityLink.counterpart_order_observation_id
                == counterpart.broker_order_observation_id,
                OrderIdentityLink.canonical_order_id == canonical_order_id,
            )
        ).scalar_one_or_none()
        if order_link is None:
            raise LedgerImportError(
                "deal identity link requires its audited parent order link"
            )
        signature = _fill_economic_identity(source)
        if signature != _fill_economic_identity(counterpart):
            raise LedgerImportError(
                "deal observations conflict on instrument, side, quantity, "
                "price, currency or execution time"
            )
        source_siblings = session.execute(
            select(BrokerFillObservation).where(
                BrokerFillObservation.broker_order_observation_id
                == source.broker_order_observation_id
            )
        ).scalars().all()
        counterpart_siblings = session.execute(
            select(BrokerFillObservation).where(
                BrokerFillObservation.broker_order_observation_id
                == counterpart.broker_order_observation_id
            )
        ).scalars().all()
        if (
            sum(_fill_economic_identity(item) == signature for item in source_siblings)
            != 1
            or sum(
                _fill_economic_identity(item) == signature
                for item in counterpart_siblings
            )
            != 1
        ):
            raise LedgerImportError(
                "deal identity is ambiguous within its parent order fill set"
            )

        link_key = _sha256_json(
            {
                "source_batch_key": source_batch.batch_key,
                "source_observation_key": source.observation_key,
                "counterpart_batch_key": counterpart_batch.batch_key,
                "counterpart_observation_key": counterpart.observation_key,
                "canonical_order_id": canonical_order_id,
                "canonical_deal_id": canonical_deal_id,
                "link_method": link_method,
            }
        )
        row = DealIdentityLink(
            link_key=link_key,
            broker_fill_observation_id=source.id,
            counterpart_fill_observation_id=counterpart.id,
            broker=str(source.broker),
            account_key=str(source.account_key),
            canonical_order_id=canonical_order_id,
            canonical_deal_id=canonical_deal_id,
            link_method=link_method,
            identity_strength="reconciled_stable",
            confidence=Decimal("1"),
            evidence_json=_canonical_json(
                {
                    "economic_identity": signature,
                    "source_record_sha256": source.source_record_sha256,
                    "counterpart_record_sha256": counterpart.source_record_sha256,
                }
            ),
            provenance_json=_canonical_json(
                {
                    "parent_order_identity_link_key": order_link.link_key,
                    "source_batch_key": source_batch.batch_key,
                    "counterpart_batch_key": counterpart_batch.batch_key,
                    "link_method": link_method,
                }
            ),
        )
        session.add(row)
        session.flush()
        return DealIdentityLinkResult(
            link_id=int(row.id),
            link_key=link_key,
            duplicate=False,
            canonical_order_id=canonical_order_id,
            canonical_deal_id=canonical_deal_id,
        )


def _fill_set_summary(
    fills: Sequence[BrokerFillObservation],
) -> tuple[str, int, Decimal, Decimal]:
    if not fills:
        raise LedgerImportError("fill-set attestation requires detailed fills")
    quantity = sum((Decimal(fill.quantity) for fill in fills), Decimal("0"))
    if quantity <= 0:
        raise LedgerImportError("fill-set attestation quantity must be positive")
    vwap = (
        sum(
            (
                Decimal(fill.quantity) * Decimal(fill.price)
                for fill in fills
            ),
            Decimal("0"),
        )
        / quantity
    )
    set_hash = _sha256_json(
        {
            "fills": sorted(
                (
                    str(fill.observation_key),
                    str(fill.source_record_sha256),
                )
                for fill in fills
            )
        }
    )
    return set_hash, len(fills), quantity, vwap


def append_order_fill_set_attestation(
    authoritative_order_observation_id: int,
    shadow_order_observation_id: int,
    *,
    canonical_order_id: str,
    method: str = "order_fill_set_economic_equivalence_v1",
    confirmed: bool = False,
    _session: Any = None,
) -> FillSetAttestationResult:
    """Append proof that API fills authoritatively shadow one CSV fill set."""
    _require_confirmed(confirmed, "order fill-set attestation")
    canonical_order_id = canonical_order_id.strip()
    method = method.strip()
    if not canonical_order_id or not method:
        raise LedgerImportError("fill-set attestation fields cannot be empty")
    if authoritative_order_observation_id == shadow_order_observation_id:
        raise LedgerImportError("fill-set attestation requires distinct orders")

    if _session is None:
        init_ledger_schema()
    with _ledger_session(_session) as session:
        authoritative = session.get(
            BrokerOrderObservation,
            authoritative_order_observation_id,
        )
        shadow = session.get(
            BrokerOrderObservation,
            shadow_order_observation_id,
        )
        if authoritative is None or shadow is None:
            raise LedgerImportError("fill-set order observation does not exist")
        authoritative_batch = session.get(
            ImportBatch, authoritative.import_batch_id
        )
        shadow_batch = session.get(ImportBatch, shadow.import_batch_id)
        if authoritative_batch is None or shadow_batch is None:
            raise LedgerImportError("fill-set import batch does not exist")
        if (
            str(authoritative_batch.source_kind) != "openapi"
            or str(shadow_batch.source_kind) != "csv"
        ):
            raise LedgerImportError(
                "fill-set roles must be OpenAPI authoritative and CSV shadow"
            )
        if str(authoritative.source_order_id) != canonical_order_id:
            raise LedgerImportError(
                "canonical_order_id must equal the authoritative broker order ID"
            )
        link = session.execute(
            select(OrderIdentityLink).where(
                OrderIdentityLink.broker_order_observation_id == shadow.id,
                OrderIdentityLink.counterpart_order_observation_id
                == authoritative.id,
                OrderIdentityLink.canonical_order_id == canonical_order_id,
            )
        ).scalar_one_or_none()
        if link is None:
            raise LedgerImportError(
                "fill-set attestation requires an audited order identity link"
            )

        existing_for_order = session.execute(
            select(OrderFillSetAttestation).where(
                OrderFillSetAttestation.broker == authoritative.broker,
                OrderFillSetAttestation.account_key == authoritative.account_key,
                OrderFillSetAttestation.canonical_order_id == canonical_order_id,
            )
        ).scalar_one_or_none()
        authoritative_fills = session.execute(
            select(BrokerFillObservation).where(
                BrokerFillObservation.broker_order_observation_id
                == authoritative.id
            )
        ).scalars().all()
        shadow_fills = session.execute(
            select(BrokerFillObservation).where(
                BrokerFillObservation.broker_order_observation_id == shadow.id
            )
        ).scalars().all()
        authoritative_summary = _fill_set_summary(authoritative_fills)
        shadow_summary = _fill_set_summary(shadow_fills)
        (authoritative_hash, authoritative_count, authoritative_qty, authoritative_vwap) = (
            authoritative_summary
        )
        shadow_hash, shadow_count, shadow_qty, shadow_vwap = shadow_summary
        if authoritative_count != shadow_count:
            raise LedgerImportError("fill sets conflict on execution count")
        if abs(authoritative_qty - shadow_qty) > Decimal("0.00000001"):
            raise LedgerImportError("fill sets conflict on executed quantity")
        vwap_tolerance = max(
            Decimal("0.0001"),
            max(abs(authoritative_vwap), abs(shadow_vwap))
            * Decimal("0.000001"),
        )
        if abs(authoritative_vwap - shadow_vwap) > vwap_tolerance:
            raise LedgerImportError("fill sets conflict on execution VWAP")

        attestation_key = _sha256_json(
            {
                "canonical_order_id": canonical_order_id,
                "authoritative_fill_set_sha256": authoritative_hash,
                "shadow_fill_set_sha256": shadow_hash,
                "method": method,
            }
        )
        if existing_for_order is not None:
            if str(existing_for_order.attestation_key) == attestation_key:
                return FillSetAttestationResult(
                    attestation_id=int(existing_for_order.id),
                    attestation_key=attestation_key,
                    duplicate=True,
                    status=str(existing_for_order.status),
                )
            raise LedgerImportError(
                "canonical order already has a different fill-set attestation"
            )

        row = OrderFillSetAttestation(
            attestation_key=attestation_key,
            broker=str(authoritative.broker),
            account_key=str(authoritative.account_key),
            canonical_order_id=canonical_order_id,
            authoritative_order_observation_id=authoritative.id,
            shadow_order_observation_id=shadow.id,
            authoritative_fill_set_sha256=authoritative_hash,
            shadow_fill_set_sha256=shadow_hash,
            authoritative_fill_count=authoritative_count,
            shadow_fill_count=shadow_count,
            authoritative_quantity=authoritative_qty,
            shadow_quantity=shadow_qty,
            authoritative_vwap=authoritative_vwap,
            shadow_vwap=shadow_vwap,
            method=method,
            status="passed",
            evidence_json=_canonical_json(
                {
                    "authoritative": {
                        "count": authoritative_count,
                        "quantity": authoritative_qty,
                        "vwap": authoritative_vwap,
                    },
                    "shadow": {
                        "count": shadow_count,
                        "quantity": shadow_qty,
                        "vwap": shadow_vwap,
                    },
                }
            ),
            provenance_json=_canonical_json(
                {
                    "identity_link_key": link.link_key,
                    "authoritative_batch_key": authoritative_batch.batch_key,
                    "shadow_batch_key": shadow_batch.batch_key,
                    "method": method,
                }
            ),
        )
        session.add(row)
        session.flush()
        return FillSetAttestationResult(
            attestation_id=int(row.id),
            attestation_key=attestation_key,
            duplicate=False,
            status="passed",
        )


def load_canonical_observation_inputs(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    source_cutoff_at: Optional[datetime] = None,
    _session: Any = None,
) -> CanonicalObservationInputs:
    """Read accepted observations plus audited aliases/attestations.

    No fuzzy matching occurs here.  Only append-only ``OrderIdentityLink`` and
    ``OrderFillSetAttestation`` rows are projected into the pure canonical
    reader's alias and shadowing fields.
    """
    from src.journal.ledger.canonical import (
        FillObservationInput,
        OrderObservationInput,
    )

    account_key = account_key.strip()
    if not account_key:
        raise LedgerImportError("account_key cannot be empty")
    cutoff = None if source_cutoff_at is None else _to_utc(source_cutoff_at)
    if _session is None:
        init_ledger_schema()
    with _ledger_session(_session) as session:
        batches = session.execute(
            select(ImportBatch).where(
                ImportBatch.broker == "moomoo",
                ImportBatch.account_key == account_key,
                ImportBatch.status == "accepted",
            )
        ).scalars().all()
        eligible_batches = [
            batch
            for batch in batches
            if cutoff is None
            or (
                _utc_from_database(batch.recorded_at) is not None
                and _utc_from_database(batch.recorded_at) <= cutoff
            )
        ]
        csv_batches = sorted(
            (
                batch
                for batch in eligible_batches
                if str(batch.source_kind) == "csv"
            ),
            key=lambda batch: (
                _utc_from_database(batch.recorded_at),
                int(batch.id),
            ),
            reverse=True,
        )
        csv_baseline = csv_batches[0] if csv_batches else None
        # The canonical scope is one explicit statement baseline plus every
        # accepted OpenAPI observation batch visible at the cutoff.  Older CSV
        # batches are immutable history, not additional economic events.
        selected_batches = [
            batch
            for batch in eligible_batches
            if str(batch.source_kind) == "openapi"
        ]
        if csv_baseline is not None:
            selected_batches.append(csv_baseline)
        batch_by_id = {int(batch.id): batch for batch in selected_batches}
        batch_ids = tuple(sorted(batch_by_id))
        if not batch_ids:
            return CanonicalObservationInputs(
                orders=(),
                fills=(),
                source_batch_ids=(),
                csv_baseline_batch_id=None,
            )

        order_rows = [
            row
            for row in session.execute(
                select(BrokerOrderObservation).where(
                    BrokerOrderObservation.account_key == account_key
                )
            ).scalars().all()
            if int(row.import_batch_id) in batch_by_id
        ]
        fill_rows = [
            row
            for row in session.execute(
                select(BrokerFillObservation).where(
                    BrokerFillObservation.account_key == account_key
                )
            ).scalars().all()
            if int(row.import_batch_id) in batch_by_id
        ]
        multiplier_by_instrument = _evidence_multiplier_map(
            order_rows,
            fill_rows,
        )

        def _multiplier_fields(row: Any) -> tuple[Optional[Decimal], str]:
            broker_value = _as_decimal(row.contract_multiplier)
            if broker_value is not None:
                return broker_value, "broker_stated"
            derived = multiplier_by_instrument.get(
                _multiplier_instrument_key(row)
            )
            if derived is not None:
                return derived, "evidence_derived_from_amount"
            return None, "unknown"

        selected_fill_ids = {int(row.id) for row in fill_rows}
        selected_order_ids = {int(row.id) for row in order_rows}
        links = [
            row
            for row in session.execute(
                select(OrderIdentityLink).where(
                    OrderIdentityLink.account_key == account_key
                )
            ).scalars().all()
            if int(row.broker_order_observation_id) in selected_order_ids
        ]
        canonical_order_by_observation = {
            int(link.broker_order_observation_id): str(link.canonical_order_id)
            for link in links
        }
        deal_links = [
            row
            for row in session.execute(
                select(DealIdentityLink).where(
                    DealIdentityLink.account_key == account_key
                )
            ).scalars().all()
            if int(row.broker_fill_observation_id) in selected_fill_ids
        ]
        deal_link_by_observation = {
            int(link.broker_fill_observation_id): link for link in deal_links
        }
        attestations = session.execute(
            select(OrderFillSetAttestation).where(
                OrderFillSetAttestation.account_key == account_key,
                OrderFillSetAttestation.status == "passed",
            )
        ).scalars().all()
        fill_set_roles: dict[int, tuple[str, str, str]] = {}
        for attestation in attestations:
            authoritative_id = int(
                attestation.authoritative_order_observation_id
            )
            shadow_id = int(attestation.shadow_order_observation_id)
            if (
                authoritative_id not in selected_order_ids
                or shadow_id not in selected_order_ids
            ):
                continue
            values = (
                str(attestation.canonical_order_id),
                str(attestation.attestation_key),
            )
            fill_set_roles[authoritative_id] = ("authoritative", *values)
            fill_set_roles[shadow_id] = ("attested_shadow", *values)

        orders = []
        for row in order_rows:
            batch = batch_by_id[int(row.import_batch_id)]
            ordered_at = _utc_from_database(row.ordered_at)
            recorded_at = _utc_from_database(row.recorded_at)
            assert ordered_at is not None and recorded_at is not None
            multiplier, multiplier_basis = _multiplier_fields(row)
            orders.append(
                OrderObservationInput(
                    observation_id=int(row.id),
                    import_batch_id=int(row.import_batch_id),
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
                    ordered_at=ordered_at,
                    order_quantity=Decimal(row.order_quantity),
                    evidence_level=str(row.evidence_level),
                    fee_evidence_status=str(row.fee_evidence_status),
                    recorded_at=recorded_at,
                    expiry=row.expiry,
                    strike=_as_decimal(row.strike),
                    option_right=(
                        None if row.option_right is None else str(row.option_right)
                    ),
                    contract_multiplier=multiplier,
                    contract_multiplier_basis=multiplier_basis,
                    source_row_number=row.source_row_number,
                    source_updated_at=_utc_from_database(row.source_updated_at),
                    order_price=_as_decimal(row.order_price),
                    order_amount=_as_decimal(row.order_amount),
                    summary_filled_quantity=_as_decimal(
                        row.summary_filled_quantity
                    ),
                    summary_average_fill_price=_as_decimal(
                        row.summary_average_fill_price
                    ),
                    total_fee=_as_decimal(row.total_fee),
                    canonical_order_id=canonical_order_by_observation.get(
                        int(row.id)
                    ),
                )
            )

        fills = []
        for row in fill_rows:
            batch = batch_by_id[int(row.import_batch_id)]
            filled_at = _utc_from_database(row.filled_at)
            recorded_at = _utc_from_database(row.recorded_at)
            assert filled_at is not None and recorded_at is not None
            multiplier, multiplier_basis = _multiplier_fields(row)
            role = fill_set_roles.get(int(row.broker_order_observation_id or 0))
            canonical_order_id = canonical_order_by_observation.get(
                int(row.broker_order_observation_id or 0)
            )
            deal_link = deal_link_by_observation.get(int(row.id))
            canonical_deal_id = (
                None if deal_link is None else str(deal_link.canonical_deal_id)
            )
            if deal_link is not None:
                canonical_order_id = str(deal_link.canonical_order_id)
            if role is not None:
                role_name, role_order_id, attestation_key = role
                canonical_order_id = role_order_id
            else:
                role_name = "ordinary"
                attestation_key = None
            identity_strength = str(row.identity_strength)
            if (
                csv_baseline is not None
                and int(row.import_batch_id) == int(csv_baseline.id)
                and identity_strength.strip().lower() == "derived_weak"
                and int(row.broker_order_observation_id or 0)
                not in canonical_order_by_observation
            ):
                # The reader selects exactly one immutable CSV baseline.  A
                # weak row outside every persisted OpenAPI order link is
                # therefore stable inside this canonical set, without claiming
                # a broker deal identity or enabling cross-batch matching.
                identity_strength = "selected_batch_stable"
            fills.append(
                FillObservationInput(
                    observation_id=int(row.id),
                    import_batch_id=int(row.import_batch_id),
                    batch_key=str(batch.batch_key),
                    source_kind=str(batch.source_kind),
                    observation_key=str(row.observation_key),
                    source_record_sha256=str(row.source_record_sha256),
                    broker=str(row.broker),
                    account_key=str(row.account_key),
                    source_order_id=(
                        None
                        if row.source_order_id is None
                        else str(row.source_order_id)
                    ),
                    source_deal_id=str(row.source_deal_id),
                    identity_strength=identity_strength,
                    raw_symbol=str(row.raw_symbol),
                    asset_type=str(row.asset_type),
                    underlying=str(row.underlying),
                    side=str(row.side),
                    currency=str(row.currency),
                    filled_at=filled_at,
                    quantity=Decimal(row.quantity),
                    price=Decimal(row.price),
                    recorded_at=recorded_at,
                    expiry=row.expiry,
                    strike=_as_decimal(row.strike),
                    option_right=(
                        None if row.option_right is None else str(row.option_right)
                    ),
                    contract_multiplier=multiplier,
                    contract_multiplier_basis=multiplier_basis,
                    source_row_number=row.source_row_number,
                    source_updated_at=None,
                    amount=_as_decimal(row.amount),
                    total_fee=_as_decimal(row.total_fee),
                    canonical_order_id=canonical_order_id,
                    canonical_deal_id=canonical_deal_id,
                    fill_set_role=role_name,
                    fill_set_attestation_key=attestation_key,
                )
            )

        return CanonicalObservationInputs(
            orders=tuple(orders),
            fills=tuple(fills),
            source_batch_ids=batch_ids,
            csv_baseline_batch_id=(
                None if csv_baseline is None else int(csv_baseline.id)
            ),
        )


def _canonical_set_payload(evidence_set: Any) -> dict[str, Any]:
    return {
        "analysis_ready": bool(evidence_set.analysis_ready),
        "provenance": evidence_set.provenance.canonical_payload(),
        "orders": [item.canonical_payload() for item in evidence_set.orders],
        "fills": [item.canonical_payload() for item in evidence_set.fills],
        "issues": [item.canonical_payload() for item in evidence_set.issues],
    }


def append_canonical_evidence_set(
    evidence_set: Any,
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    source_cutoff_at: datetime,
    confirmed: bool = False,
    allow_blocked: bool = False,
    _session: Any = None,
) -> CanonicalSetPersistenceResult:
    """Persist a replayable, versioned canonical result and all provenance."""
    from src.journal.ledger.canonical import CanonicalEvidenceSet

    _require_confirmed(confirmed, "canonical evidence set")
    if not isinstance(evidence_set, CanonicalEvidenceSet):
        raise LedgerImportError("canonical persistence requires CanonicalEvidenceSet")
    if not evidence_set.analysis_ready and not allow_blocked:
        raise LedgerImportError(
            "canonical evidence contains blocking conflicts; persist only with "
            "allow_blocked=True for diagnostics"
        )
    account_key = account_key.strip()
    if not account_key:
        raise LedgerImportError("account_key cannot be empty")
    cutoff = _to_utc(source_cutoff_at)
    canonical_hash = str(evidence_set.canonical_set_sha256).strip().lower()
    if len(canonical_hash) != 64 or any(
        character not in "0123456789abcdef" for character in canonical_hash
    ):
        raise LedgerImportError("canonical_set_sha256 must be SHA-256")

    source_batch_ids = tuple(
        sorted({int(ref.import_batch_id) for ref in evidence_set.evidence_refs})
    )
    if not source_batch_ids:
        raise LedgerImportError("canonical evidence set has no source observations")
    set_key = _sha256_json(
        {
            "account_key": account_key,
            "canonical_set_sha256": canonical_hash,
            "reader_name": evidence_set.provenance.reader_name,
            "reader_version": evidence_set.provenance.reader_version,
            "source_cutoff_at": cutoff,
        }
    )

    if _session is None:
        init_ledger_schema()
    with _ledger_session(_session) as session:
        existing = session.execute(
            select(CanonicalEvidenceSetRecord).where(
                CanonicalEvidenceSetRecord.set_key == set_key
            )
        ).scalar_one_or_none()
        if existing is not None:
            return CanonicalSetPersistenceResult(
                canonical_set_id=int(existing.id),
                set_key=set_key,
                canonical_set_sha256=canonical_hash,
                duplicate=True,
                analysis_ready=bool(existing.analysis_ready),
                issue_count=int(existing.blocking_issue_count),
            )

        batches = {
            batch_id: session.get(ImportBatch, batch_id)
            for batch_id in source_batch_ids
        }
        if any(batch is None for batch in batches.values()):
            raise LedgerImportError("canonical evidence references a missing batch")
        if any(
            str(batch.account_key) != account_key
            or str(batch.broker) != "moomoo"
            or str(batch.status) != "accepted"
            for batch in batches.values()
            if batch is not None
        ):
            raise LedgerImportError(
                "canonical evidence references an unaccepted or cross-account batch"
            )

        observation_rows: dict[tuple[str, int], Any] = {}
        for ref in evidence_set.evidence_refs:
            if ref.evidence_kind == "order":
                row = session.get(BrokerOrderObservation, ref.observation_id)
            elif ref.evidence_kind == "fill":
                row = session.get(BrokerFillObservation, ref.observation_id)
            else:
                raise LedgerImportError("canonical evidence kind is unsupported")
            if (
                row is None
                or int(row.import_batch_id) != int(ref.import_batch_id)
                or str(row.observation_key) != ref.observation_key
                or str(row.source_record_sha256) != ref.source_record_sha256
                or str(row.account_key) != account_key
            ):
                raise LedgerImportError(
                    "canonical provenance does not bind to the stored observation"
                )
            observation_rows[(ref.evidence_kind, ref.observation_id)] = row

        payload = _canonical_set_payload(evidence_set)
        row = CanonicalEvidenceSetRecord(
            set_key=set_key,
            canonical_set_sha256=canonical_hash,
            broker="moomoo",
            account_key=account_key,
            reader_name=evidence_set.provenance.reader_name,
            reader_version=evidence_set.provenance.reader_version,
            source_cutoff_at=cutoff,
            source_batch_ids_json=_canonical_json(source_batch_ids),
            analysis_ready=bool(evidence_set.analysis_ready),
            canonical_order_count=len(evidence_set.orders),
            canonical_fill_count=len(evidence_set.fills),
            blocking_issue_count=sum(
                issue.severity == "blocking" for issue in evidence_set.issues
            ),
            canonical_payload_json=_canonical_json(payload),
            provenance_json=_canonical_json(
                evidence_set.provenance.canonical_payload()
            ),
        )
        session.add(row)
        session.flush()

        for entity_kind, members in (
            ("order", evidence_set.orders),
            ("fill", evidence_set.fills),
        ):
            for member in members:
                selected = member.selected_ref
                selected_row = observation_rows.get(
                    (entity_kind, selected.observation_id)
                )
                if selected_row is None:
                    raise LedgerImportError(
                        "canonical selected observation is absent from provenance"
                    )
                member_payload = member.canonical_payload()
                identity_key = "/".join(str(value) for value in member.identity)
                member_key = _sha256_json(
                    {
                        "entity_kind": entity_kind,
                        "identity": member.identity,
                        "canonical_payload": member_payload,
                    }
                )
                session.add(
                    CanonicalEvidenceMemberRecord(
                        canonical_set_id=row.id,
                        member_key=member_key,
                        entity_kind=entity_kind,
                        identity_key=identity_key,
                        selected_order_observation_id=(
                            int(selected_row.id) if entity_kind == "order" else None
                        ),
                        selected_fill_observation_id=(
                            int(selected_row.id) if entity_kind == "fill" else None
                        ),
                        canonical_payload_json=_canonical_json(member_payload),
                        provenance_json=_canonical_json(
                            {
                                "selected_ref": selected.canonical_payload(),
                                "evidence_refs": [
                                    ref.canonical_payload()
                                    for ref in member.evidence_refs
                                ],
                            }
                        ),
                    )
                )

        for issue in evidence_set.issues:
            issue_payload = issue.canonical_payload()
            issue_key = _sha256_json(issue_payload)
            session.add(
                CanonicalEvidenceIssueRecord(
                    canonical_set_id=row.id,
                    issue_key=issue_key,
                    severity=issue.severity,
                    code=issue.code,
                    entity_kind=issue.entity_kind,
                    entity_key=issue.entity_key,
                    field_name=issue.field_name,
                    issue_json=_canonical_json(issue_payload),
                    evidence_refs_json=_canonical_json(
                        [
                            ref.canonical_payload()
                            for ref in issue.evidence_refs
                        ]
                    ),
                )
            )

        for ref in evidence_set.evidence_refs:
            source = observation_rows[(ref.evidence_kind, ref.observation_id)]
            ref_payload = ref.canonical_payload()
            provenance_key = _sha256_json(ref_payload)
            session.add(
                CanonicalEvidenceProvenanceRecord(
                    canonical_set_id=row.id,
                    provenance_key=provenance_key,
                    import_batch_id=ref.import_batch_id,
                    broker_order_observation_id=(
                        int(source.id) if ref.evidence_kind == "order" else None
                    ),
                    broker_fill_observation_id=(
                        int(source.id) if ref.evidence_kind == "fill" else None
                    ),
                    broker_fee_observation_id=None,
                    evidence_kind=ref.evidence_kind,
                    evidence_role="canonical_input",
                    evidence_ref_json=_canonical_json(ref_payload),
                )
            )

        fee_rows = session.execute(
            select(BrokerFeeObservation).where(
                BrokerFeeObservation.account_key == account_key
            )
        ).scalars().all()
        for fee in fee_rows:
            if int(fee.import_batch_id) not in source_batch_ids:
                continue
            fee_payload = {
                "evidence_kind": "fee",
                "batch_key": str(batches[int(fee.import_batch_id)].batch_key),
                "source_kind": str(batches[int(fee.import_batch_id)].source_kind),
                "observation_key": str(fee.observation_key),
                "source_record_sha256": str(fee.source_record_sha256),
            }
            session.add(
                CanonicalEvidenceProvenanceRecord(
                    canonical_set_id=row.id,
                    provenance_key=_sha256_json(fee_payload),
                    import_batch_id=int(fee.import_batch_id),
                    broker_order_observation_id=None,
                    broker_fill_observation_id=None,
                    broker_fee_observation_id=int(fee.id),
                    evidence_kind="fee",
                    evidence_role="fee_input",
                    evidence_ref_json=_canonical_json(fee_payload),
                )
            )

        session.flush()
        return CanonicalSetPersistenceResult(
            canonical_set_id=int(row.id),
            set_key=set_key,
            canonical_set_sha256=canonical_hash,
            duplicate=False,
            analysis_ready=bool(evidence_set.analysis_ready),
            issue_count=len(evidence_set.issues),
        )


def append_openapi_canonical_bundle(
    preview: OpenApiExportPreview,
    *,
    order_identity_matches: Sequence[tuple[int, str]],
    deal_identity_matches: Sequence[tuple[int, str]] = (),
    fill_set_shadow_order_observation_ids: Sequence[int] = (),
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    source_cutoff_at: Optional[datetime] = None,
    expected_canonical_set_sha256: Optional[str] = None,
    confirmed: bool = False,
) -> OpenApiCanonicalBundleResult:
    """Atomically append OpenAPI evidence, links, attestations and canonical set.

    Match tuples use an already persisted CSV observation ID followed by the
    broker-stable order/deal ID in ``preview``.  Any invalid identity,
    ambiguous deal, fill-set mismatch or blocked canonical result rolls back
    the entire transaction, including the new OpenAPI batch.
    """
    from src.journal.ledger.canonical import (
        CanonicalizationBlockedError,
        canonicalize_observations,
    )

    _require_confirmed(confirmed, "OpenAPI canonical bundle")
    account_key = account_key.strip()
    if not account_key:
        raise LedgerImportError("account_key cannot be empty")
    if len({item[0] for item in order_identity_matches}) != len(
        order_identity_matches
    ) or len({item[1] for item in order_identity_matches}) != len(
        order_identity_matches
    ):
        raise LedgerImportError("order identity matches must be one-to-one")
    if len({item[0] for item in deal_identity_matches}) != len(
        deal_identity_matches
    ) or len({item[1] for item in deal_identity_matches}) != len(
        deal_identity_matches
    ):
        raise LedgerImportError("deal identity matches must be one-to-one")

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            # Serialize the plan-bound append from its first read.  A second
            # concurrent confirmation waits, then observes the first one's
            # immutable rows and returns the normal duplicate result instead
            # of racing a unique constraint after both passed existence checks.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        csv_baseline = session.execute(
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
        if csv_baseline is None:
            raise LedgerImportError(
                "OpenAPI canonical bundle requires an accepted CSV baseline"
            )

        import_result = append_openapi_batch(
            preview,
            account_key=account_key,
            confirmed=True,
            _session=session,
        )
        # ``DatabaseManager`` sessions intentionally disable autoflush.  Make
        # the newly appended fills/fees visible to the rest of this same
        # atomic bundle even when every order link is an overlap reuse.
        session.flush()
        api_orders = session.execute(
            select(BrokerOrderObservation).where(
                BrokerOrderObservation.import_batch_id == import_result.batch_id
            )
        ).scalars().all()
        api_order_by_source_id = {
            str(row.source_order_id): row for row in api_orders
        }
        order_link_results: list[OrderIdentityLinkResult] = []
        for csv_observation_id, broker_order_id in order_identity_matches:
            csv_order = session.get(
                BrokerOrderObservation, csv_observation_id
            )
            if (
                csv_order is None
                or int(csv_order.import_batch_id) != int(csv_baseline.id)
            ):
                raise LedgerImportError(
                    "order identity match must reference the selected CSV baseline"
                )
            api_order = api_order_by_source_id.get(str(broker_order_id))
            if api_order is None:
                raise LedgerImportError(
                    "order identity match does not reference this OpenAPI batch"
                )
            existing_order_link = session.execute(
                select(OrderIdentityLink).where(
                    OrderIdentityLink.broker_order_observation_id == csv_order.id
                )
            ).scalar_one_or_none()
            if existing_order_link is not None:
                if str(existing_order_link.canonical_order_id) != str(
                    broker_order_id
                ):
                    raise LedgerImportError(
                        "CSV order already resolves to a different broker order"
                    )
                # Overlapping read-only windows can observe the same broker
                # order again in a new immutable batch.  The existing CSV ->
                # broker identity proof remains sufficient; the new OpenAPI
                # row is already broker-stable and needs no second alias.
                order_link_results.append(
                    OrderIdentityLinkResult(
                        link_id=int(existing_order_link.id),
                        link_key=str(existing_order_link.link_key),
                        duplicate=True,
                        canonical_order_id=str(broker_order_id),
                    )
                )
                continue
            order_link_results.append(
                append_order_identity_link(
                    int(csv_order.id),
                    int(api_order.id),
                    canonical_order_id=str(broker_order_id),
                    confirmed=True,
                    _session=session,
                )
            )

        api_fills = session.execute(
            select(BrokerFillObservation).where(
                BrokerFillObservation.import_batch_id == import_result.batch_id
            )
        ).scalars().all()
        api_fill_by_source_id = {
            str(row.source_deal_id): row for row in api_fills
        }
        deal_link_results: list[DealIdentityLinkResult] = []
        for csv_observation_id, broker_deal_id in deal_identity_matches:
            csv_fill = session.get(BrokerFillObservation, csv_observation_id)
            if (
                csv_fill is None
                or int(csv_fill.import_batch_id) != int(csv_baseline.id)
            ):
                raise LedgerImportError(
                    "deal identity match must reference the selected CSV baseline"
                )
            api_fill = api_fill_by_source_id.get(str(broker_deal_id))
            if api_fill is None:
                raise LedgerImportError(
                    "deal identity match does not reference this OpenAPI batch"
                )
            existing_deal_link = session.execute(
                select(DealIdentityLink).where(
                    DealIdentityLink.broker_fill_observation_id == csv_fill.id
                )
            ).scalar_one_or_none()
            if existing_deal_link is not None:
                if (
                    str(existing_deal_link.canonical_order_id)
                    != str(api_fill.source_order_id)
                    or str(existing_deal_link.canonical_deal_id)
                    != str(broker_deal_id)
                ):
                    raise LedgerImportError(
                        "CSV fill already resolves to a different broker deal"
                    )
                deal_link_results.append(
                    DealIdentityLinkResult(
                        link_id=int(existing_deal_link.id),
                        link_key=str(existing_deal_link.link_key),
                        duplicate=True,
                        canonical_order_id=str(api_fill.source_order_id),
                        canonical_deal_id=str(broker_deal_id),
                    )
                )
                continue
            deal_link_results.append(
                append_deal_identity_link(
                    int(csv_fill.id),
                    int(api_fill.id),
                    canonical_order_id=str(api_fill.source_order_id),
                    canonical_deal_id=str(broker_deal_id),
                    confirmed=True,
                    _session=session,
                )
            )

        fill_set_results: list[FillSetAttestationResult] = []
        for shadow_order_id in fill_set_shadow_order_observation_ids:
            shadow = session.get(BrokerOrderObservation, shadow_order_id)
            if (
                shadow is None
                or int(shadow.import_batch_id) != int(csv_baseline.id)
            ):
                raise LedgerImportError(
                    "fill-set shadow must reference the selected CSV baseline"
                )
            existing_fill_set = session.execute(
                select(OrderFillSetAttestation).where(
                    OrderFillSetAttestation.shadow_order_observation_id
                    == shadow.id
                )
            ).scalar_one_or_none()
            if existing_fill_set is not None:
                fill_set_results.append(
                    FillSetAttestationResult(
                        attestation_id=int(existing_fill_set.id),
                        attestation_key=str(existing_fill_set.attestation_key),
                        duplicate=True,
                        status=str(existing_fill_set.status),
                    )
                )
                continue
            link = session.execute(
                select(OrderIdentityLink).where(
                    OrderIdentityLink.broker_order_observation_id == shadow.id
                )
            ).scalar_one_or_none()
            if link is None:
                raise LedgerImportError(
                    "fill-set shadow requires an order identity match in the bundle"
                )
            fill_set_results.append(
                append_order_fill_set_attestation(
                    int(link.counterpart_order_observation_id),
                    int(shadow.id),
                    canonical_order_id=str(link.canonical_order_id),
                    confirmed=True,
                    _session=session,
                )
            )

        if source_cutoff_at is None:
            accepted_batches = session.execute(
                select(ImportBatch).where(
                    ImportBatch.broker == "moomoo",
                    ImportBatch.account_key == account_key,
                    ImportBatch.status == "accepted",
                    ImportBatch.source_kind.in_(("csv", "openapi")),
                )
            ).scalars().all()
            cutoff_values = [
                _utc_from_database(batch.recorded_at)
                for batch in accepted_batches
            ]
            cutoff = max(value for value in cutoff_values if value is not None)
        else:
            cutoff = _to_utc(source_cutoff_at)

        inputs = load_canonical_observation_inputs(
            account_key,
            source_cutoff_at=cutoff,
            _session=session,
        )
        if inputs.csv_baseline_batch_id != int(csv_baseline.id):
            raise LedgerImportError("canonical input baseline changed during import")
        if import_result.batch_id not in inputs.source_batch_ids:
            raise LedgerImportError(
                "source_cutoff_at excludes the confirmed OpenAPI batch"
            )
        canonical = canonicalize_observations(inputs.orders, inputs.fills)
        try:
            canonical.require_analysis_ready()
        except CanonicalizationBlockedError as exc:
            raise LedgerImportError(str(exc)) from exc
        if (
            expected_canonical_set_sha256 is not None
            and canonical.canonical_set_sha256
            != expected_canonical_set_sha256.strip().lower()
        ):
            raise LedgerImportError(
                "canonical evidence changed after the confirmed preview"
            )
        canonical_result = append_canonical_evidence_set(
            canonical,
            account_key=account_key,
            source_cutoff_at=cutoff,
            confirmed=True,
            _session=session,
        )
        return OpenApiCanonicalBundleResult(
            import_result=import_result,
            order_identity_links=tuple(order_link_results),
            deal_identity_links=tuple(deal_link_results),
            fill_set_attestations=tuple(fill_set_results),
            canonical_set=canonical_result,
            source_batch_ids=inputs.source_batch_ids,
            csv_baseline_batch_id=int(csv_baseline.id),
        )


def get_latest_canonical_evidence_set(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    analysis_ready_only: bool = True,
) -> Optional[CanonicalEvidenceSetRecord]:
    """Return the latest immutable canonical-set metadata/payload record."""
    init_ledger_schema()
    conditions = [
        CanonicalEvidenceSetRecord.broker == "moomoo",
        CanonicalEvidenceSetRecord.account_key == account_key,
    ]
    if analysis_ready_only:
        conditions.append(CanonicalEvidenceSetRecord.analysis_ready.is_(True))
    db = get_db()
    with db.session_scope() as session:
        row = session.execute(
            select(CanonicalEvidenceSetRecord)
            .where(and_(*conditions))
            .order_by(
                CanonicalEvidenceSetRecord.recorded_at.desc(),
                CanonicalEvidenceSetRecord.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            session.expunge(row)
        return row
