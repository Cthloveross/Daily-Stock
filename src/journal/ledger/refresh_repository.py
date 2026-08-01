# -*- coding: utf-8 -*-
"""Server-owned, explicitly confirmed read-only Moomoo refresh artifacts."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from src.journal.brokers.moomoo_openapi_export import (
    MoomooOpenApiExportError,
    OpenApiExportPreview,
    parse_openapi_export,
)
from src.core.trading_calendar import get_effective_trading_date
from src.journal.ledger.activation_repository import (
    get_episode_build_activation_state,
)
from src.journal.ledger.models import (
    BrokerExecutionGroupObservation,
    BrokerFillObservation,
    BrokerOrderObservation,
    CanonicalEvidenceSetRecord,
    EpisodeBuild,
    EpisodeBuildCanonicalSource,
    EpisodeBuildSnapshotFenceSource,
    ImportBatch,
)
from src.journal.ledger.openapi_repository import (
    OpenApiConfirmResult,
    OpenApiImportPlan,
    OpenApiPlanError,
    confirm_openapi_import_plan,
    plan_openapi_import,
)
from src.journal.ledger.refresh_models import (
    JournalRefreshArtifact,
    JournalRefreshPublication,
)
from src.storage import Base, get_db

__all__ = [
    "DEFAULT_REFRESH_ARTIFACT_TTL_MINUTES",
    "DEFAULT_REFRESH_OVERLAP_DAYS",
    "REFRESH_APPEND_ONLY_GUARD_MESSAGE",
    "refresh_guard_trigger_ddl",
    "refresh_guard_trigger_name",
    "JournalRefreshArtifactSnapshot",
    "JournalRefreshConfirmation",
    "JournalRefreshError",
    "JournalRefreshPublicationSnapshot",
    "JournalRefreshStatusSnapshot",
    "append_refresh_publication",
    "confirm_refresh_artifact",
    "get_latest_refresh_publication",
    "get_journal_refresh_status",
    "get_refresh_artifact",
    "init_refresh_schema",
    "load_verified_refresh_publication_in_session",
    "save_refresh_artifact",
    "suggest_refresh_window",
]


DEFAULT_REFRESH_ARTIFACT_TTL_MINUTES = 30
DEFAULT_REFRESH_OVERLAP_DAYS = 7
_SCHEMA_LOCK = threading.Lock()
_IMMUTABLE_TABLES = (
    JournalRefreshArtifact.__tablename__,
    JournalRefreshPublication.__tablename__,
)

# Single authoritative deny-trigger DDL for the refresh artifact tables.
# ``artifact_gc`` recreates the DELETE trigger from this exact text after a
# controlled in-transaction delete; never hand-copy the trigger body elsewhere
# (New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md §3 step 7).
REFRESH_APPEND_ONLY_GUARD_MESSAGE = "journal refresh rows are append-only"


def refresh_guard_trigger_name(table_name: str, operation: str) -> str:
    """Deterministic UPDATE/DELETE deny-trigger name for one refresh table."""
    return f"trg_{table_name}_{operation.lower()}_immutable"


def refresh_guard_trigger_ddl(table_name: str, operation: str) -> str:
    """Authoritative CREATE TRIGGER DDL guarding one refresh table."""
    trigger = refresh_guard_trigger_name(table_name, operation)
    return (
        f"CREATE TRIGGER IF NOT EXISTS {trigger} "
        f"BEFORE {operation} ON {table_name} "
        "BEGIN SELECT RAISE(ABORT, "
        f"'{REFRESH_APPEND_ONLY_GUARD_MESSAGE}'); END"
    )


class JournalRefreshError(ValueError):
    """Raised when a refresh artifact is stale, unsafe, or inconsistent."""


@dataclass(frozen=True)
class JournalRefreshArtifactSnapshot:
    artifact_id: int
    artifact_key: str
    account_key: str
    account_binding: str
    environment: str
    market: str
    window_start: datetime
    window_end: datetime
    generated_at: datetime
    source_sha256: str
    evidence_sha256: str
    preview_key: str
    confirm_allowed: bool
    expires_at: datetime
    recorded_at: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class JournalRefreshPublicationSnapshot:
    publication_id: int
    publication_key: str
    refresh_artifact_id: int
    import_batch_id: int
    canonical_set_id: int
    account_key: str
    account_binding: str
    broker_queried_through: datetime
    latest_fill_at: Optional[datetime]
    evidence_published_through: datetime
    import_duplicate: bool
    recorded_at: datetime


@dataclass(frozen=True)
class JournalRefreshConfirmation:
    artifact: JournalRefreshArtifactSnapshot
    publication: JournalRefreshPublicationSnapshot
    import_result: OpenApiConfirmResult


@dataclass(frozen=True)
class JournalRefreshStatusSnapshot:
    freshness_state: str
    pending_stage: str
    expected_complete_through: datetime
    broker_queried_through: Optional[datetime]
    latest_fill_at: Optional[datetime]
    evidence_published_through: Optional[datetime]
    publication_recorded_at: Optional[datetime]
    latest_artifact_id: Optional[int]
    latest_artifact_confirm_allowed: Optional[bool]
    latest_artifact_expires_at: Optional[datetime]
    latest_canonical_set_id: Optional[int]
    latest_canonical_set_sha256: Optional[str]
    latest_canonical_source_through: Optional[datetime]
    latest_canonical_build_id: Optional[int]
    latest_canonical_build_key: Optional[str]
    latest_canonical_build_source_through: Optional[datetime]
    active_selection_source: str
    active_activation_id: Optional[int]
    active_build_id: Optional[int]
    active_build_key: Optional[str]
    active_canonical_set_id: Optional[int]
    active_source_through: Optional[datetime]
    account_bound: bool


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    def _default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(f"unsupported refresh JSON value: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_default,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def init_refresh_schema() -> None:
    """Create refresh tables and SQLite guards without touching old evidence."""
    with _SCHEMA_LOCK:
        db = get_db()
        Base.metadata.create_all(db._engine)
        if db._engine.dialect.name == "sqlite":
            with db._engine.begin() as connection:
                for table_name in _IMMUTABLE_TABLES:
                    for operation in ("UPDATE", "DELETE"):
                        connection.exec_driver_sql(
                            refresh_guard_trigger_ddl(table_name, operation)
                        )


def _artifact_snapshot(row: JournalRefreshArtifact) -> JournalRefreshArtifactSnapshot:
    try:
        payload = json.loads(str(row.payload_json), parse_float=Decimal)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JournalRefreshError("stored refresh artifact payload is invalid") from exc
    if not isinstance(payload, Mapping):
        raise JournalRefreshError("stored refresh artifact payload is invalid")
    return JournalRefreshArtifactSnapshot(
        artifact_id=int(row.id),
        artifact_key=str(row.artifact_key),
        account_key=str(row.account_key),
        account_binding=str(row.account_binding),
        environment=str(row.environment),
        market=str(row.market),
        window_start=_db_utc(row.window_start),
        window_end=_db_utc(row.window_end),
        generated_at=_db_utc(row.generated_at),
        source_sha256=str(row.source_sha256),
        evidence_sha256=str(row.evidence_sha256),
        preview_key=str(row.preview_key),
        confirm_allowed=bool(row.confirm_allowed),
        expires_at=_db_utc(row.expires_at),
        recorded_at=_db_utc(row.recorded_at),
        payload=payload,
    )


def _publication_snapshot(
    row: JournalRefreshPublication,
) -> JournalRefreshPublicationSnapshot:
    return JournalRefreshPublicationSnapshot(
        publication_id=int(row.id),
        publication_key=str(row.publication_key),
        refresh_artifact_id=int(row.refresh_artifact_id),
        import_batch_id=int(row.import_batch_id),
        canonical_set_id=int(row.canonical_set_id),
        account_key=str(row.account_key),
        account_binding=str(row.account_binding),
        broker_queried_through=_db_utc(row.broker_queried_through),
        latest_fill_at=(
            None if row.latest_fill_at is None else _db_utc(row.latest_fill_at)
        ),
        evidence_published_through=_db_utc(row.evidence_published_through),
        import_duplicate=bool(row.import_duplicate),
        recorded_at=_db_utc(row.recorded_at),
    )


def load_verified_refresh_publication_in_session(
    session: Any,
    *,
    publication_id: int,
    account_key: str,
) -> tuple[JournalRefreshArtifactSnapshot, JournalRefreshPublicationSnapshot]:
    """Strictly replay one published OpenD refresh without opening a DB.

    This reader is intentionally session-scoped so continuity-sensitive
    callers can validate the artifact, import batch, canonical receipt, and
    publication inside one externally pinned read transaction.  It never
    initializes schema or writes data.
    """
    normalized_account = str(account_key or "").strip()
    if not normalized_account:
        raise JournalRefreshError("account_key cannot be empty")
    if publication_id <= 0:
        raise JournalRefreshError("publication_id must be positive")

    publication_row = session.get(JournalRefreshPublication, publication_id)
    if publication_row is None:
        raise JournalRefreshError("refresh publication does not exist")
    artifact_row = session.get(
        JournalRefreshArtifact,
        int(publication_row.refresh_artifact_id),
    )
    batch = session.get(ImportBatch, int(publication_row.import_batch_id))
    canonical = session.get(
        CanonicalEvidenceSetRecord,
        int(publication_row.canonical_set_id),
    )
    if artifact_row is None or batch is None or canonical is None:
        raise JournalRefreshError(
            "refresh publication provenance is incomplete"
        )

    artifact = _artifact_snapshot(artifact_row)
    publication = _publication_snapshot(publication_row)
    try:
        preview = parse_openapi_export(artifact.payload)
    except MoomooOpenApiExportError as exc:
        raise JournalRefreshError(
            "stored refresh artifact payload failed strict replay"
        ) from exc
    metadata = preview.metadata
    expected_artifact_key = _sha256_json(
        {
            "kind": "journal_refresh_artifact_v1",
            "account_key": normalized_account,
            "account_binding": metadata.account_binding,
            "source_sha256": metadata.source_sha256,
            "preview_key": artifact.preview_key,
        }
    )
    expected_batch_key = _sha256_json(
        {
            "account_key": normalized_account,
            "broker": "moomoo",
            "parser_batch_key": metadata.batch_key,
        }
    )
    expected_publication_key = _sha256_json(
        {
            "kind": "journal_refresh_publication_v1",
            "artifact_key": artifact.artifact_key,
            "import_batch_id": publication.import_batch_id,
            "canonical_set_id": publication.canonical_set_id,
            "canonical_set_sha256": str(canonical.canonical_set_sha256),
        }
    )
    try:
        provenance = json.loads(str(batch.provenance_json))
        source_batch_ids = tuple(
            int(value) for value in json.loads(str(canonical.source_batch_ids_json))
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JournalRefreshError(
            "refresh publication provenance JSON is invalid"
        ) from exc
    if not isinstance(provenance, dict):
        raise JournalRefreshError(
            "refresh import batch provenance is invalid"
        )
    if tuple(sorted(set(source_batch_ids))) != source_batch_ids:
        raise JournalRefreshError(
            "refresh canonical source batches are invalid"
        )

    ordinary_order_count = int(
        session.scalar(
            select(func.count(BrokerOrderObservation.id)).where(
                BrokerOrderObservation.import_batch_id == batch.id
            )
        )
        or 0
    )
    execution_group_count = int(
        session.scalar(
            select(func.count(BrokerExecutionGroupObservation.id)).where(
                BrokerExecutionGroupObservation.import_batch_id == batch.id
            )
        )
        or 0
    )
    fill_count = int(
        session.scalar(
            select(func.count(BrokerFillObservation.id)).where(
                BrokerFillObservation.import_batch_id == batch.id
            )
        )
        or 0
    )
    latest_fill_at = max(
        (item.filled_at for item in preview.fills),
        default=None,
    )
    latest_fill_at = (
        None
        if latest_fill_at is None
        else _utc(latest_fill_at, field_name="latest_fill_at")
    )
    comparisons = {
        "publication broker": (str(publication_row.broker), "moomoo"),
        "publication account": (
            publication.account_key,
            normalized_account,
        ),
        "publication binding": (
            publication.account_binding,
            artifact.account_binding,
        ),
        "artifact account": (artifact.account_key, normalized_account),
        "artifact broker": (str(artifact_row.broker), "moomoo"),
        "artifact environment": (artifact.environment, "LIVE"),
        "artifact market": (artifact.market, "US"),
        "artifact confirm policy": (artifact.confirm_allowed, True),
        "payload environment": (metadata.environment, "LIVE"),
        "payload market": (metadata.market, "US"),
        "payload binding": (
            metadata.account_binding,
            artifact.account_binding,
        ),
        "payload analysis readiness": (metadata.analysis_ready, True),
        "payload analysis level": (metadata.analysis_level, "exact"),
        "payload reconciliation": (
            metadata.reconciliation_status,
            "passed",
        ),
        "payload retrieval completeness": (
            metadata.retrieval_complete,
            True,
        ),
        "payload coverage completeness": (metadata.coverage_complete, True),
        "artifact source hash": (
            metadata.source_sha256,
            artifact.source_sha256,
        ),
        "artifact evidence hash": (
            metadata.evidence_sha256,
            artifact.evidence_sha256,
        ),
        "artifact window start": (
            _utc(metadata.window_start, field_name="window_start"),
            artifact.window_start,
        ),
        "artifact window end": (
            _utc(metadata.window_end, field_name="window_end"),
            artifact.window_end,
        ),
        "artifact generation": (
            _utc(metadata.generated_at, field_name="generated_at"),
            artifact.generated_at,
        ),
        "artifact key": (artifact.artifact_key, expected_artifact_key),
        "batch key": (str(batch.batch_key), expected_batch_key),
        "batch broker": (str(batch.broker), "moomoo"),
        "batch account": (str(batch.account_key), normalized_account),
        "batch kind": (str(batch.source_kind), "openapi"),
        "batch schema": (str(batch.source_schema), metadata.source_schema),
        "batch source hash": (
            str(batch.source_sha256),
            metadata.source_sha256,
        ),
        "batch parser name": (str(batch.parser_name), metadata.parser_name),
        "batch parser version": (
            str(batch.parser_version),
            metadata.parser_version,
        ),
        "batch status": (str(batch.status), "accepted"),
        "batch readiness": (bool(batch.analysis_ready), True),
        "batch analysis level": (str(batch.analysis_level), "exact"),
        "batch reconciliation": (str(batch.reconciliation_status), "passed"),
        "batch window start": (
            _db_utc(batch.window_start),
            artifact.window_start,
        ),
        "batch window end": (_db_utc(batch.window_end), artifact.window_end),
        "batch order count": (
            int(batch.order_observation_count),
            len(preview.orders),
        ),
        "batch fill count": (
            int(batch.fill_observation_count),
            len(preview.fills),
        ),
        "stored order rows": (
            ordinary_order_count + execution_group_count,
            len(preview.orders),
        ),
        "stored fill rows": (fill_count, len(preview.fills)),
        "batch evidence provenance": (
            str(provenance.get("evidence_sha256")),
            metadata.evidence_sha256,
        ),
        "batch parser provenance": (
            str(provenance.get("parser_batch_key")),
            metadata.batch_key,
        ),
        "batch binding provenance": (
            str(provenance.get("account_binding")),
            artifact.account_binding,
        ),
        "canonical broker": (str(canonical.broker), "moomoo"),
        "canonical account": (str(canonical.account_key), normalized_account),
        "canonical readiness": (bool(canonical.analysis_ready), True),
        "canonical blocking issues": (
            int(canonical.blocking_issue_count),
            0,
        ),
        "canonical batch membership": (
            publication.import_batch_id in source_batch_ids,
            True,
        ),
        "publication key": (
            publication.publication_key,
            expected_publication_key,
        ),
        "publication watermark": (
            publication.broker_queried_through,
            artifact.window_end,
        ),
        "publication latest fill": (publication.latest_fill_at, latest_fill_at),
        "publication canonical cutoff": (
            publication.evidence_published_through,
            _db_utc(canonical.source_cutoff_at),
        ),
        "publication coverage order": (
            publication.evidence_published_through
            >= publication.broker_queried_through,
            True,
        ),
    }
    mismatch = next(
        (name for name, values in comparisons.items() if values[0] != values[1]),
        None,
    )
    if mismatch is not None:
        raise JournalRefreshError(
            f"stored refresh publication {mismatch} is inconsistent"
        )
    return artifact, publication


def get_latest_refresh_publication(
    account_key: str,
) -> Optional[JournalRefreshPublicationSnapshot]:
    account_key = account_key.strip()
    init_refresh_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.execute(
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
        return None if row is None else _publication_snapshot(row)


def _require_account_continuity(
    session: Any,
    *,
    account_key: str,
    account_binding: str,
    window_start: datetime,
    window_end: datetime,
) -> None:
    latest_csv = session.execute(
        select(ImportBatch)
        .where(
            ImportBatch.broker == "moomoo",
            ImportBatch.account_key == account_key,
            ImportBatch.source_kind == "csv",
            ImportBatch.status == "accepted",
            ImportBatch.window_end.is_not(None),
        )
        .order_by(
            ImportBatch.window_end.desc(),
            ImportBatch.recorded_at.desc(),
            ImportBatch.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if latest_csv is None:
        raise JournalRefreshError("import the confirmed Moomoo CSV baseline first")

    latest = session.execute(
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
    if latest is not None and str(latest.account_binding) != account_binding:
        raise JournalRefreshError(
            "Moomoo account binding differs from the last confirmed refresh"
        )

    anchor = _db_utc(latest_csv.window_end)
    if latest is not None:
        anchor = max(anchor, _db_utc(latest.broker_queried_through))
    normalized_start = _utc(window_start, field_name="window_start")
    normalized_end = _utc(window_end, field_name="window_end")
    if normalized_start > anchor or normalized_end < anchor:
        raise JournalRefreshError(
            "refresh window is discontinuous; it must overlap the latest "
            "confirmed Journal watermark"
        )


def save_refresh_artifact(
    preview: OpenApiExportPreview,
    payload: Mapping[str, Any],
    plan: OpenApiImportPlan,
    *,
    account_key: str,
    ttl_minutes: int = DEFAULT_REFRESH_ARTIFACT_TTL_MINUTES,
) -> JournalRefreshArtifactSnapshot:
    """Persist a server-owned preview artifact; no evidence rows are written."""
    if ttl_minutes < 1 or ttl_minutes > 240:
        raise JournalRefreshError("refresh artifact TTL must be between 1 and 240 minutes")
    binding = (preview.metadata.account_binding or "").strip().lower()
    if len(binding) != 64:
        raise JournalRefreshError("direct refresh requires a stable account binding")
    if preview.metadata.environment != "LIVE" or preview.metadata.market != "US":
        raise JournalRefreshError("Journal refresh accepts only the LIVE US account scope")
    if not preview.metadata.retrieval_complete or not preview.metadata.coverage_complete:
        raise JournalRefreshError("OpenD did not prove complete query coverage")

    now = datetime.now(timezone.utc)
    artifact_key = _sha256_json(
        {
            "kind": "journal_refresh_artifact_v1",
            "account_key": account_key,
            "account_binding": binding,
            "source_sha256": preview.metadata.source_sha256,
            "preview_key": plan.preview_key,
        }
    )
    payload_json = _canonical_json(payload)
    init_refresh_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _require_account_continuity(
            session,
            account_key=account_key,
            account_binding=binding,
            window_start=preview.metadata.window_start,
            window_end=preview.metadata.window_end,
        )
        existing = session.execute(
            select(JournalRefreshArtifact).where(
                JournalRefreshArtifact.artifact_key == artifact_key
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _artifact_snapshot(existing)
        row = JournalRefreshArtifact(
            artifact_key=artifact_key,
            broker="moomoo",
            account_key=account_key,
            account_binding=binding,
            environment=preview.metadata.environment,
            market=preview.metadata.market,
            window_start=_utc(preview.metadata.window_start, field_name="window_start"),
            window_end=_utc(preview.metadata.window_end, field_name="window_end"),
            generated_at=_utc(preview.metadata.generated_at, field_name="generated_at"),
            source_sha256=preview.metadata.source_sha256,
            evidence_sha256=preview.metadata.evidence_sha256,
            preview_key=plan.preview_key,
            confirm_allowed=plan.confirm_allowed,
            payload_json=payload_json,
            expires_at=now + timedelta(minutes=ttl_minutes),
            recorded_at=now,
        )
        session.add(row)
        session.flush()
        return _artifact_snapshot(row)


def get_refresh_artifact(
    artifact_id: int,
    *,
    account_key: str,
) -> Optional[JournalRefreshArtifactSnapshot]:
    init_refresh_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.execute(
            select(JournalRefreshArtifact).where(
                JournalRefreshArtifact.id == artifact_id,
                JournalRefreshArtifact.broker == "moomoo",
                JournalRefreshArtifact.account_key == account_key,
            )
        ).scalar_one_or_none()
        return None if row is None else _artifact_snapshot(row)


def append_refresh_publication(
    artifact: JournalRefreshArtifactSnapshot,
    preview: OpenApiExportPreview,
    plan: OpenApiImportPlan,
    result: OpenApiConfirmResult,
) -> JournalRefreshPublicationSnapshot:
    """Append the post-commit receipt; retrying fills a prior crash gap safely."""
    latest_fill_at = max(
        (item.filled_at for item in preview.fills),
        default=None,
    )
    publication_key = _sha256_json(
        {
            "kind": "journal_refresh_publication_v1",
            "artifact_key": artifact.artifact_key,
            "import_batch_id": result.import_batch_id,
            "canonical_set_id": result.canonical_set_id,
            "canonical_set_sha256": result.canonical_set_sha256,
        }
    )
    init_refresh_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _require_account_continuity(
            session,
            account_key=artifact.account_key,
            account_binding=artifact.account_binding,
            window_start=preview.metadata.window_start,
            window_end=preview.metadata.window_end,
        )
        existing = session.execute(
            select(JournalRefreshPublication).where(
                JournalRefreshPublication.refresh_artifact_id
                == artifact.artifact_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _publication_snapshot(existing)
        row = JournalRefreshPublication(
            publication_key=publication_key,
            refresh_artifact_id=artifact.artifact_id,
            import_batch_id=result.import_batch_id,
            canonical_set_id=result.canonical_set_id,
            broker="moomoo",
            account_key=artifact.account_key,
            account_binding=artifact.account_binding,
            broker_queried_through=_utc(
                preview.metadata.window_end,
                field_name="window_end",
            ),
            latest_fill_at=(
                None
                if latest_fill_at is None
                else _utc(latest_fill_at, field_name="latest_fill_at")
            ),
            evidence_published_through=_utc(
                plan.source_cutoff_at,
                field_name="source_cutoff_at",
            ),
            import_duplicate=result.duplicate,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            with db.session_scope() as retry_session:
                existing = retry_session.execute(
                    select(JournalRefreshPublication).where(
                        JournalRefreshPublication.refresh_artifact_id
                        == artifact.artifact_id
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise
                return _publication_snapshot(existing)
        return _publication_snapshot(row)


def confirm_refresh_artifact(
    artifact_id: int,
    *,
    preview_key: str,
    acknowledge_partial_window: bool,
    account_key: str,
) -> JournalRefreshConfirmation:
    """Re-plan and confirm exactly one non-expired server-owned artifact."""
    artifact = get_refresh_artifact(artifact_id, account_key=account_key)
    if artifact is None:
        raise JournalRefreshError("refresh artifact does not exist")
    if datetime.now(timezone.utc) >= artifact.expires_at:
        raise JournalRefreshError("refresh preview expired; run a new read-only refresh")
    if artifact.preview_key != preview_key.strip().lower():
        raise JournalRefreshError("refresh preview key does not match the artifact")

    preview = parse_openapi_export(artifact.payload)
    if preview.metadata.account_binding != artifact.account_binding:
        raise JournalRefreshError("stored refresh account binding is inconsistent")
    plan = plan_openapi_import(preview, artifact.payload, account_key=account_key)
    if plan.preview_key != artifact.preview_key:
        raise JournalRefreshError(
            "Journal evidence changed after preview; run a new read-only refresh"
        )
    try:
        result = confirm_openapi_import_plan(
            preview,
            artifact.payload,
            preview_key=preview_key,
            acknowledge_partial_window=acknowledge_partial_window,
            account_key=account_key,
        )
    except OpenApiPlanError as exc:
        raise JournalRefreshError(str(exc)) from exc
    publication = append_refresh_publication(artifact, preview, plan, result)
    return JournalRefreshConfirmation(
        artifact=artifact,
        publication=publication,
        import_result=result,
    )


def suggest_refresh_window(
    account_key: str,
    *,
    now: Optional[datetime] = None,
    overlap_days: int = DEFAULT_REFRESH_OVERLAP_DAYS,
) -> tuple[datetime, datetime]:
    """Choose a continuous window ending at the latest completed XNYS close.

    A Journal refresh is a daily evidence checkpoint, not an intraday activity
    feed.  Ending at wall-clock ``now`` used to mix the current session's fills
    with order fees that Moomoo had not published yet.  That made the formal
    watermark disagree with the actual query and blocked an otherwise complete
    prior-day refresh.  The end boundary now shares the exact exchange-session
    close used by refresh status.
    """
    if overlap_days < 1 or overlap_days > 30:
        raise JournalRefreshError("refresh overlap must be between 1 and 30 days")
    zone = ZoneInfo("America/New_York")
    end = _latest_completed_us_session_close(now)
    db = get_db()
    if not inspect(db._engine).has_table(ImportBatch.__tablename__):
        raise JournalRefreshError("import the confirmed Moomoo CSV baseline first")
    init_refresh_schema()
    with db.session_scope() as session:
        latest_csv = session.execute(
            select(ImportBatch)
            .where(
                ImportBatch.broker == "moomoo",
                ImportBatch.account_key == account_key,
                ImportBatch.source_kind == "csv",
                ImportBatch.status == "accepted",
                ImportBatch.window_end.is_not(None),
            )
            .order_by(
                ImportBatch.window_end.desc(),
                ImportBatch.recorded_at.desc(),
                ImportBatch.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if latest_csv is None or latest_csv.window_end is None:
            raise JournalRefreshError("import the confirmed Moomoo CSV baseline first")
        publication = session.execute(
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
        anchor = _db_utc(latest_csv.window_end)
        if publication is not None:
            anchor = max(anchor, _db_utc(publication.broker_queried_through))
    anchor_local = anchor.astimezone(zone)
    if anchor_local > end + timedelta(minutes=5):
        raise JournalRefreshError("stored Journal coverage is later than the local clock")
    start = anchor_local - timedelta(days=overlap_days)
    if end - start > timedelta(days=366):
        raise JournalRefreshError(
            "refresh gap exceeds the bounded OpenD history window; import a newer CSV baseline"
        )
    if start >= end:
        start = end - timedelta(days=overlap_days)
    return start, end


def _latest_completed_us_session_close(
    now: Optional[datetime] = None,
) -> datetime:
    """Return the exact close of the latest fully completed XNYS session.

    The exchange calendar is mandatory here.  Falling back to a weekday or a
    hard-coded 16:00 boundary could include an unfinished session, miss a US
    holiday, or misstate an early close.  A daily evidence query therefore
    fails closed if the authoritative session calendar is unavailable.
    """
    zone = ZoneInfo("America/New_York")
    if now is None:
        market_now = datetime.now(zone)
    else:
        if now.tzinfo is None or now.utcoffset() is None:
            raise JournalRefreshError("refresh clock must include a timezone")
        market_now = now.astimezone(zone)

    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS")
        local_date = market_now.date()
        if calendar.is_session(local_date):
            session = calendar.date_to_session(local_date, direction="none")
            close = calendar.session_close(session)
            close_utc = _utc(close, field_name="session_close")
            if market_now.astimezone(timezone.utc) < close_utc:
                session = calendar.previous_session(session)
        else:
            session = calendar.date_to_session(local_date, direction="previous")
        close = calendar.session_close(session)
        if hasattr(close, "to_pydatetime"):
            close = close.to_pydatetime()
        completed_close = _utc(close, field_name="session_close").astimezone(zone)
    except JournalRefreshError:
        raise
    except Exception as exc:  # noqa: BLE001 - evidence cutoff must fail closed
        raise JournalRefreshError(
            "cannot resolve the latest completed XNYS session"
        ) from exc

    if completed_close.astimezone(timezone.utc) > market_now.astimezone(timezone.utc):
        raise JournalRefreshError(
            "resolved XNYS evidence cutoff is later than the refresh clock"
        )
    return completed_close.replace(microsecond=0)


def _expected_us_evidence_through(now: Optional[datetime] = None) -> datetime:
    try:
        return _latest_completed_us_session_close(now).astimezone(timezone.utc)
    except JournalRefreshError:
        # Status remains observable if the optional calendar dependency is
        # unavailable, but the write-capable refresh preview above still fails
        # closed and cannot claim this fallback as queried evidence.
        zone = ZoneInfo("America/New_York")
        market_now = (now or datetime.now(zone)).astimezone(zone)
        session_date = get_effective_trading_date("us", current_time=market_now)
        return datetime.combine(
            session_date,
            time(hour=16),
            tzinfo=zone,
        ).astimezone(timezone.utc)


def get_journal_refresh_status(
    account_key: str,
    *,
    now: Optional[datetime] = None,
) -> JournalRefreshStatusSnapshot:
    """Aggregate immutable acquisition, evidence, build, and activation facts."""
    account_key = account_key.strip()
    if not account_key:
        raise JournalRefreshError("account_key cannot be empty")
    init_refresh_schema()
    db = get_db()
    with db.session_scope() as session:
        artifact = session.execute(
            select(JournalRefreshArtifact)
            .where(
                JournalRefreshArtifact.broker == "moomoo",
                JournalRefreshArtifact.account_key == account_key,
            )
            .order_by(
                JournalRefreshArtifact.recorded_at.desc(),
                JournalRefreshArtifact.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        publication = session.execute(
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
        canonical_set = session.execute(
            select(CanonicalEvidenceSetRecord)
            .where(
                CanonicalEvidenceSetRecord.broker == "moomoo",
                CanonicalEvidenceSetRecord.account_key == account_key,
                CanonicalEvidenceSetRecord.analysis_ready.is_(True),
            )
            .order_by(
                CanonicalEvidenceSetRecord.recorded_at.desc(),
                CanonicalEvidenceSetRecord.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        canonical_build_row = session.execute(
            select(EpisodeBuild, EpisodeBuildCanonicalSource)
            .join(
                EpisodeBuildCanonicalSource,
                EpisodeBuildCanonicalSource.episode_build_id == EpisodeBuild.id,
            )
            .where(EpisodeBuild.account_key == account_key)
            .order_by(EpisodeBuild.recorded_at.desc(), EpisodeBuild.id.desc())
            .limit(1)
        ).one_or_none()
        artifact_values = (
            None
            if artifact is None
            else {
                "id": int(artifact.id),
                "confirm_allowed": bool(artifact.confirm_allowed),
                "expires_at": _db_utc(artifact.expires_at),
                "recorded_at": _db_utc(artifact.recorded_at),
            }
        )
        publication_snapshot = (
            None if publication is None else _publication_snapshot(publication)
        )
        canonical_values = (
            None
            if canonical_set is None
            else {
                "id": int(canonical_set.id),
                "sha256": str(canonical_set.canonical_set_sha256),
                "source_through": _db_utc(canonical_set.source_cutoff_at),
            }
        )
        canonical_build_values = (
            None
            if canonical_build_row is None
            else {
                "id": int(canonical_build_row[0].id),
                "key": str(canonical_build_row[0].build_key),
                "source_through": _db_utc(
                    canonical_build_row[0].source_cutoff_at
                ),
                "canonical_set_id": int(canonical_build_row[1].canonical_set_id),
            }
        )

    activation = get_episode_build_activation_state(account_key)
    active_source_through = None
    active_fence_targets_latest_canonical_set = False
    if activation.current_build_id is not None:
        with db.session_scope() as session:
            active_build = session.get(EpisodeBuild, activation.current_build_id)
            if active_build is not None:
                active_source_through = _db_utc(active_build.source_cutoff_at)
            if canonical_values is not None:
                fence_link = session.execute(
                    select(EpisodeBuildSnapshotFenceSource).where(
                        EpisodeBuildSnapshotFenceSource.episode_build_id
                        == activation.current_build_id
                    )
                ).scalar_one_or_none()
                # An active snapshot-fence build derives exactly from its
                # frozen target canonical set (windowed after the snapshot
                # boundary), so it covers the latest canonical evidence when
                # its target IS that latest set.
                active_fence_targets_latest_canonical_set = (
                    fence_link is not None
                    and int(fence_link.target_canonical_set_id)
                    == canonical_values["id"]
                    and str(fence_link.target_canonical_set_sha256)
                    == canonical_values["sha256"]
                )
    expected = _expected_us_evidence_through(now)
    artifact_recorded_at = (
        None if artifact_values is None else artifact_values["recorded_at"]
    )
    publication_recorded_at = (
        None if publication_snapshot is None else publication_snapshot.recorded_at
    )
    unpublished_artifact = artifact_values is not None and (
        publication_recorded_at is None
        or artifact_recorded_at > publication_recorded_at
    )

    if unpublished_artifact and not bool(artifact_values["confirm_allowed"]):
        freshness_state = "evidence_blocked"
        pending_stage = "refresh"
    elif publication_snapshot is None:
        freshness_state = "never_synced"
        pending_stage = "confirm" if unpublished_artifact else "refresh"
    elif publication_snapshot.broker_queried_through < expected:
        freshness_state = "stale"
        pending_stage = "confirm" if unpublished_artifact else "refresh"
    elif canonical_values is None:
        freshness_state = "evidence_blocked"
        pending_stage = "refresh"
    elif (
        canonical_build_values is None
        or canonical_build_values["canonical_set_id"] != canonical_values["id"]
    ):
        freshness_state = "evidence_current"
        pending_stage = "build"
    elif (
        activation.selection_source != "activation"
        or activation.canonical_set_id != canonical_values["id"]
        or (
            activation.current_build_id != canonical_build_values["id"]
            and not active_fence_targets_latest_canonical_set
        )
    ):
        freshness_state = "build_ready"
        pending_stage = "activate"
    else:
        freshness_state = "current"
        pending_stage = "none"

    return JournalRefreshStatusSnapshot(
        freshness_state=freshness_state,
        pending_stage=pending_stage,
        expected_complete_through=expected,
        broker_queried_through=(
            None
            if publication_snapshot is None
            else publication_snapshot.broker_queried_through
        ),
        latest_fill_at=(
            None if publication_snapshot is None else publication_snapshot.latest_fill_at
        ),
        evidence_published_through=(
            None
            if publication_snapshot is None
            else publication_snapshot.evidence_published_through
        ),
        publication_recorded_at=publication_recorded_at,
        latest_artifact_id=(
            None if artifact_values is None else artifact_values["id"]
        ),
        latest_artifact_confirm_allowed=(
            None if artifact_values is None else artifact_values["confirm_allowed"]
        ),
        latest_artifact_expires_at=(
            None if artifact_values is None else artifact_values["expires_at"]
        ),
        latest_canonical_set_id=(
            None if canonical_values is None else canonical_values["id"]
        ),
        latest_canonical_set_sha256=(
            None if canonical_values is None else canonical_values["sha256"]
        ),
        latest_canonical_source_through=(
            None if canonical_values is None else canonical_values["source_through"]
        ),
        latest_canonical_build_id=(
            None
            if canonical_build_values is None
            else canonical_build_values["id"]
        ),
        latest_canonical_build_key=(
            None
            if canonical_build_values is None
            else canonical_build_values["key"]
        ),
        latest_canonical_build_source_through=(
            None
            if canonical_build_values is None
            else canonical_build_values["source_through"]
        ),
        active_selection_source=activation.selection_source,
        active_activation_id=activation.current_activation_id,
        active_build_id=activation.current_build_id,
        active_build_key=activation.current_build_key,
        active_canonical_set_id=activation.canonical_set_id,
        active_source_through=active_source_through,
        account_bound=(publication_snapshot is not None),
    )
