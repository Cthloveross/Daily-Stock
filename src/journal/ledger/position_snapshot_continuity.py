# -*- coding: utf-8 -*-
"""Read-only continuity fence for future PositionEpisode boundaries.

The evaluator deliberately does not initialize schemas, save artifacts, build
episodes, or activate a build.  It opens the existing SQLite database in
``mode=ro`` and enables ``query_only`` before reading any evidence.  A
confirmed current-position snapshot may be stale or tied to a superseded
publication here: those are currentness concepts, while this module proves a
historical, forward-only boundary using a later publication chain.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Generator, Iterable, Literal, Mapping, Optional
from urllib.parse import quote

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.config import get_config
from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    VerifiedCanonicalEvidenceProjection,
    VerifiedCanonicalEpisodeEvidenceProjection,
    load_verified_canonical_evidence_projection,
    load_verified_canonical_episode_evidence_projection,
)
from src.journal.ledger.position_snapshot_repository import (
    PositionSnapshotError,
    PositionSnapshotMemberRecord,
    PositionSnapshotRecord,
    load_latest_position_snapshot_in_session,
)
from src.journal.ledger.refresh_repository import (
    JournalRefreshArtifactSnapshot,
    JournalRefreshError,
    load_verified_refresh_publication_in_session,
)
from src.storage import DatabaseManager


__all__ = [
    "POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION",
    "PositionSnapshotContinuityAssessment",
    "PositionSnapshotContinuityContext",
    "PositionSnapshotContinuityCounts",
    "PositionSnapshotContinuityError",
    "PositionSnapshotContinuityReason",
    "assess_latest_position_snapshot_continuity",
    "assess_position_snapshot_continuity_in_session",
    "open_position_snapshot_continuity_session",
]


POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION = (
    "position-snapshot-continuity-fence/1.1"
)
PositionSnapshotContinuityStatus = Literal[
    "no_snapshot",
    "awaiting_refresh",
    "blocked",
    "ready",
]

_REQUIRED_HASH_LENGTH = 64
_WRITE_AUTHOR_ACTIONS = {
    value
    for name in (
        "SQLITE_INSERT",
        "SQLITE_UPDATE",
        "SQLITE_DELETE",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_ALTER_TABLE",
        "SQLITE_REINDEX",
        "SQLITE_ANALYZE",
        "SQLITE_ATTACH",
        "SQLITE_DETACH",
    )
    if (value := getattr(sqlite3, name, None)) is not None
}


class PositionSnapshotContinuityError(ValueError):
    """Raised when stored evidence cannot be read or verified safely."""


@dataclass(frozen=True)
class PositionSnapshotContinuityCounts:
    snapshot_member_count: int = 0
    chain_publication_count: int = 0
    chain_batch_count: int = 0
    target_order_count: int = 0
    target_fill_count: int = 0
    guard_fill_count: int = 0
    pre_boundary_fill_count: int = 0
    post_boundary_fill_count: int = 0
    aggregate_changed_pre_boundary_count: int = 0
    straddling_order_count: int = 0
    straddling_group_count: int = 0
    unbacked_post_boundary_count: int = 0
    option_identity_mismatch_count: int = 0


@dataclass(frozen=True)
class PositionSnapshotContinuityReason:
    code: str
    message: str
    entity_kind: Optional[str] = None
    entity_ids: tuple[int, ...] = ()
    count: int = 1


@dataclass(frozen=True)
class PositionSnapshotContinuityAssessment:
    policy_version: str
    status: PositionSnapshotContinuityStatus
    ready_for_episode_build: bool
    fence_key: Optional[str]
    account_key: str
    snapshot_id: Optional[int]
    snapshot_key: Optional[str]
    anchor_publication_id: Optional[int]
    anchor_publication_key: Optional[str]
    target_publication_id: Optional[int]
    target_publication_key: Optional[str]
    target_canonical_set_id: Optional[int]
    target_canonical_set_sha256: Optional[str]
    boundary_at: Optional[datetime]
    guard_started_at: Optional[datetime]
    guard_completed_at: Optional[datetime]
    publication_ids: tuple[int, ...]
    source_batch_ids: tuple[int, ...]
    counts: PositionSnapshotContinuityCounts
    reasons: tuple[PositionSnapshotContinuityReason, ...]


@dataclass(frozen=True)
class PositionSnapshotContinuityContext:
    """Assessment plus the verified evidence loaded in the same transaction."""

    assessment: PositionSnapshotContinuityAssessment
    snapshot: Optional[PositionSnapshotRecord]
    target_projection: Optional[VerifiedCanonicalEpisodeEvidenceProjection]


def _resolve_database_path() -> Path:
    """Resolve the active file database without constructing a DB manager."""
    instance = getattr(DatabaseManager, "_instance", None)
    if instance is not None and getattr(instance, "_initialized", False):
        engine = getattr(instance, "_engine", None)
        if engine is None or engine.url.get_backend_name() != "sqlite":
            raise PositionSnapshotContinuityError(
                "continuity assessment currently requires SQLite"
            )
        database = str(engine.url.database or "").strip()
    else:
        database = str(get_config().database_path or "").strip()
    if not database or database.lower() == ":memory:":
        raise PositionSnapshotContinuityError(
            "continuity assessment requires an existing SQLite file"
        )
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise PositionSnapshotContinuityError(
            "Journal database does not exist; initialize it before assessment"
        )
    return path


def _deny_writes(
    action: int,
    _arg1: Optional[str],
    _arg2: Optional[str],
    _database: Optional[str],
    _trigger: Optional[str],
) -> int:
    return sqlite3.SQLITE_DENY if action in _WRITE_AUTHOR_ACTIONS else sqlite3.SQLITE_OK


def _open_readonly_database(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.set_authorizer(_deny_writes)
    return connection


@contextmanager
def _open_readonly_session(
    path: Path,
) -> Generator[tuple[Connection, Session], None, None]:
    """Open one explicit, query-only SQLite snapshot for the whole fence."""
    uri = (
        "sqlite+pysqlite:///file:"
        f"{quote(str(path), safe='/')}?mode=ro&uri=true"
    )
    engine = create_engine(uri, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _configure_readonly_connection(
        dbapi_connection: sqlite3.Connection,
        _connection_record: Any,
    ) -> None:
        dbapi_connection.execute("PRAGMA query_only=ON")
        dbapi_connection.set_authorizer(_deny_writes)

    try:
        with engine.connect() as connection:
            # Python's sqlite3 does not begin a transaction for SELECT.  Pin
            # every snapshot/publication/canonical read to one WAL snapshot.
            connection.exec_driver_sql("BEGIN")
            with Session(
                bind=connection,
                autoflush=False,
                expire_on_commit=False,
            ) as session:
                yield connection, session
    finally:
        engine.dispose()


@contextmanager
def open_position_snapshot_continuity_session(
) -> Generator[tuple[Connection, Session], None, None]:
    """Open the shared read-only transaction used by fences and previews."""
    path = _resolve_database_path()
    with _open_readonly_session(path) as value:
        yield value


def _table_exists(connection: Connection, table_name: str) -> bool:
    row = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).first()
    return row is not None


def _require_tables(
    connection: Connection,
    table_names: Iterable[str],
) -> None:
    missing = [name for name in table_names if not _table_exists(connection, name)]
    if missing:
        raise PositionSnapshotContinuityError(
            "continuity evidence tables are unavailable: " + ", ".join(missing)
        )


def _require_pinned_readonly_session(
    connection: Connection,
    session: Session,
    *,
    require_query_only: bool = True,
) -> None:
    """Reject misuse of the in-session evaluator on a mutable or split bind.

    ``require_query_only=False`` is reserved for the explicit future-build
    confirm transaction: a write transaction can never be ``query_only``, so
    that caller proves the zero-business-write property by test instead.  The
    session must still be pinned to the exact same open SQLite transaction.
    """
    session_bound = session.get_bind() is connection
    if not session_bound and not require_query_only:
        # Engine-bound write sessions expose their live connection via
        # ``Session.connection()``; require it to be the caller's connection.
        try:
            session_bound = session.connection() is connection
        except SQLAlchemyError:
            session_bound = False
    if (
        connection.closed
        or connection.dialect.name != "sqlite"
        or not session_bound
    ):
        raise PositionSnapshotContinuityError(
            "continuity assessment requires one bound SQLite connection"
        )
    if not connection.in_transaction():
        raise PositionSnapshotContinuityError(
            "continuity assessment requires an explicit read transaction"
        )
    if not require_query_only:
        return
    try:
        query_only = int(
            connection.exec_driver_sql("PRAGMA query_only").scalar_one()
        )
    except (TypeError, ValueError, sqlite3.Error, SQLAlchemyError) as exc:
        raise PositionSnapshotContinuityError(
            "continuity assessment cannot verify SQLite query_only"
        ) from exc
    if query_only != 1:
        raise PositionSnapshotContinuityError(
            "continuity assessment requires SQLite query_only"
        )


def _utc(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PositionSnapshotContinuityError(f"{field_name} is missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PositionSnapshotContinuityError(
            f"{field_name} is not a valid timestamp"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any, *, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PositionSnapshotContinuityError(
            f"{field_name} is not a valid decimal"
        ) from exc
    if not parsed.is_finite():
        raise PositionSnapshotContinuityError(f"{field_name} must be finite")
    return parsed


def _optional_decimal(value: Any) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _normalized_decimal_text(value: Any) -> str:
    parsed = _optional_decimal(value)
    if parsed is None:
        return ""
    if parsed == 0:
        return "0"
    return format(parsed.normalize(), "f")


def _is_hash(value: Any) -> bool:
    text = str(value or "")
    return len(text) == _REQUIRED_HASH_LENGTH and all(
        character in "0123456789abcdef" for character in text
    )


def _canonical_json(value: Any) -> str:
    def _default(item: Any) -> Any:
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, Decimal):
            return format(item.normalize(), "f")
        if isinstance(item, tuple):
            return list(item)
        raise TypeError(f"unsupported continuity value: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_default,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _reason(
    code: str,
    message: str,
    *,
    entity_kind: Optional[str] = None,
    entity_ids: Iterable[int] = (),
    count: Optional[int] = None,
) -> PositionSnapshotContinuityReason:
    ids = tuple(sorted({int(value) for value in entity_ids}))
    return PositionSnapshotContinuityReason(
        code=code,
        message=message,
        entity_kind=entity_kind,
        entity_ids=ids,
        count=(len(ids) if count is None and ids else int(count or 1)),
    )


def _empty_assessment(
    account_key: str,
    reason: PositionSnapshotContinuityReason,
) -> PositionSnapshotContinuityAssessment:
    return PositionSnapshotContinuityAssessment(
        policy_version=POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION,
        status="no_snapshot",
        ready_for_episode_build=False,
        fence_key=None,
        account_key=account_key,
        snapshot_id=None,
        snapshot_key=None,
        anchor_publication_id=None,
        anchor_publication_key=None,
        target_publication_id=None,
        target_publication_key=None,
        target_canonical_set_id=None,
        target_canonical_set_sha256=None,
        boundary_at=None,
        guard_started_at=None,
        guard_completed_at=None,
        publication_ids=(),
        source_batch_ids=(),
        counts=PositionSnapshotContinuityCounts(),
        reasons=(reason,),
    )


def _snapshot_and_members(
    connection: Connection,
    session: Session,
    account_key: str,
) -> Optional[PositionSnapshotRecord]:
    _require_tables(connection, ("journal_v2_position_snapshots",))
    snapshot = load_latest_position_snapshot_in_session(
        session,
        account_key=account_key,
    )
    if snapshot is None:
        return None
    if not snapshot.future_anchor_candidate or snapshot.broker_as_of_at is not None:
        raise PositionSnapshotContinuityError(
            "confirmed position snapshot is not a complete stable future boundary"
        )
    return snapshot


def _publication_chain(
    connection: Connection,
    session: Session,
    snapshot: PositionSnapshotRecord,
    guard_started_at: datetime,
    boundary_at: datetime,
) -> tuple[
    Mapping[str, Any],
    tuple[JournalRefreshArtifactSnapshot, ...],
    tuple[Mapping[str, Any], ...],
    list[PositionSnapshotContinuityReason],
]:
    _require_tables(
        connection,
        ("journal_v2_refresh_publications", "journal_v2_refresh_artifacts"),
    )
    anchor = connection.exec_driver_sql(
        "SELECT * FROM journal_v2_refresh_publications WHERE id=?",
        (snapshot.refresh_publication_id,),
    ).mappings().first()
    if anchor is None:
        raise PositionSnapshotContinuityError(
            "snapshot anchor publication is missing"
        )
    if (
        anchor["broker"] != "moomoo"
        or anchor["account_key"] != snapshot.account_key
        or anchor["account_binding"] != snapshot.account_binding_id
        or anchor["publication_key"] != snapshot.refresh_publication_key
    ):
        raise PositionSnapshotContinuityError(
            "snapshot anchor publication identity is inconsistent"
        )
    _anchor_artifact, verified_anchor = (
        load_verified_refresh_publication_in_session(
            session,
            publication_id=int(anchor["id"]),
            account_key=snapshot.account_key,
        )
    )
    if (
        verified_anchor.publication_key != snapshot.refresh_publication_key
        or verified_anchor.account_binding != snapshot.account_binding_id
    ):
        raise PositionSnapshotContinuityError(
            "snapshot anchor refresh provenance is inconsistent"
        )
    publications = tuple(
        connection.exec_driver_sql(
            """
            SELECT * FROM journal_v2_refresh_publications
            WHERE broker='moomoo' AND account_key=? AND id>?
            ORDER BY recorded_at, id
            """,
            (snapshot.account_key, int(anchor["id"])),
        ).mappings().all()
    )
    if not publications:
        return anchor, (), (), []

    reasons: list[PositionSnapshotContinuityReason] = []
    artifacts: list[JournalRefreshArtifactSnapshot] = []
    previous_watermark = _utc(
        anchor["broker_queried_through"],
        field_name="anchor.broker_queried_through",
    )
    for publication in publications:
        artifact, verified_publication = (
            load_verified_refresh_publication_in_session(
                session,
                publication_id=int(publication["id"]),
                account_key=snapshot.account_key,
            )
        )
        artifacts.append(artifact)
        window_start = artifact.window_start
        window_end = artifact.window_end
        watermark = _utc(
            publication["broker_queried_through"],
            field_name="publication.broker_queried_through",
        )
        if (
            verified_publication.account_binding
            != snapshot.account_binding_id
            or verified_publication.account_key != snapshot.account_key
            or artifact.account_binding != snapshot.account_binding_id
            or artifact.account_key != snapshot.account_key
            or artifact.environment != "LIVE"
            or artifact.market != "US"
            or not artifact.confirm_allowed
            or not _is_hash(artifact.source_sha256)
            or not _is_hash(artifact.evidence_sha256)
        ):
            reasons.append(
                _reason(
                    "publication_binding_mismatch",
                    "a later refresh is not the same confirmed LIVE/US account",
                    entity_kind="publication",
                    entity_ids=(int(publication["id"]),),
                )
            )
        if window_start > previous_watermark or window_end != watermark:
            reasons.append(
                _reason(
                    "publication_chain_gap",
                    "later refresh artifacts do not overlap the prior watermark",
                    entity_kind="publication",
                    entity_ids=(int(publication["id"]),),
                )
            )
        previous_watermark = max(previous_watermark, watermark)

    target = publications[-1]
    target_watermark = _utc(
        target["broker_queried_through"],
        field_name="target.broker_queried_through",
    )
    if artifacts:
        first_start = artifacts[0].window_start
        if first_start > guard_started_at or target_watermark < boundary_at:
            reasons.append(
                _reason(
                    "publication_chain_gap",
                    "later refresh coverage does not bracket the acquisition guard",
                    entity_kind="publication",
                    entity_ids=tuple(int(row["id"]) for row in publications),
                    count=len(publications),
                )
            )
    return anchor, publications, tuple(artifacts), reasons


def _snapshot_instrument_identity(
    member: PositionSnapshotMemberRecord,
) -> tuple[str, str, str, str, str, str]:
    return (
        member.asset_type.strip().lower(),
        member.underlying.strip().upper(),
        member.expiry.isoformat(),
        _normalized_decimal_text(member.strike),
        member.option_right.strip().upper(),
        member.currency.strip().upper(),
    )


def _canonical_instrument_identity(instrument: Any) -> tuple[str, str, str, str, str, str]:
    return (
        str(instrument.asset_type).strip().lower(),
        str(instrument.underlying).strip().upper(),
        instrument.expiry.isoformat() if instrument.expiry else "",
        _normalized_decimal_text(instrument.strike),
        str(instrument.option_right or "").strip().upper(),
        str(instrument.currency).strip().upper(),
    )


def _canonical_checks(
    connection: Connection,
    session: Session,
    *,
    snapshot: PositionSnapshotRecord,
    anchor: Mapping[str, Any],
    publications: tuple[Mapping[str, Any], ...],
    guard_started_at: datetime,
    boundary_at: datetime,
    include_episode_projection: bool = False,
    max_member_count: Optional[int] = None,
) -> tuple[
    VerifiedCanonicalEvidenceProjection,
    Optional[VerifiedCanonicalEpisodeEvidenceProjection],
    PositionSnapshotContinuityCounts,
    list[PositionSnapshotContinuityReason],
]:
    _require_tables(
        connection,
        (
            "journal_v2_canonical_evidence_sets",
            "journal_v2_canonical_evidence_members",
            "journal_v2_import_batches",
            "journal_v2_broker_order_observations",
            "journal_v2_broker_fill_observations",
            "journal_v2_canonical_execution_group_members",
            "journal_v2_canonical_execution_group_legs",
            "journal_v2_canonical_execution_group_fill_links",
        ),
    )
    target_publication = publications[-1]
    target_projection = None
    if include_episode_projection:
        target_projection = (
            load_verified_canonical_episode_evidence_projection(
                session,
                snapshot.account_key,
                int(target_publication["canonical_set_id"]),
                max_member_count=max_member_count,
            )
        )
        target = target_projection.boundary
    else:
        target = load_verified_canonical_evidence_projection(
            session,
            snapshot.account_key,
            int(target_publication["canonical_set_id"]),
        )
    reasons: list[PositionSnapshotContinuityReason] = []
    if (
        not _is_hash(target.canonical_set_sha256)
        or target.source_cutoff_at < boundary_at
        or _utc(
            target_publication["evidence_published_through"],
            field_name="evidence_published_through",
        )
        < boundary_at
    ):
        reasons.append(
            _reason(
                "target_canonical_unavailable",
                "the later publication has no analysis-ready canonical evidence through the boundary",
                entity_kind="canonical_set",
                entity_ids=(target.canonical_set_id,),
            )
        )
    canonical_batches = set(target.source_batch_ids)
    chain_batch_ids = {int(row["import_batch_id"]) for row in publications}
    if not chain_batch_ids.issubset(canonical_batches):
        reasons.append(
            _reason(
                "target_canonical_unavailable",
                "target canonical evidence omits a later published refresh batch",
                entity_kind="canonical_set",
                entity_ids=(target.canonical_set_id,),
            )
        )
    anchor_projection = load_verified_canonical_evidence_projection(
        session,
        snapshot.account_key,
        int(anchor["canonical_set_id"]),
    )
    anchor_orders = {
        row.identity: row.economic_sha256 for row in anchor_projection.orders
    }

    guard_fill_ids: list[int] = []
    pre_boundary_fill_count = 0
    post_boundary_fill_count = 0
    unbacked_ids: list[int] = []
    fill_times_by_order: dict[
        tuple[str, str, str], tuple[int, list[datetime]]
    ] = {}
    snapshot_by_instrument: dict[
        tuple[str, str, str, str, str, str],
        PositionSnapshotMemberRecord,
    ] = {}
    for member in snapshot.members:
        identity = _snapshot_instrument_identity(member)
        if identity in snapshot_by_instrument:
            raise PositionSnapshotContinuityError(
                "position snapshot contains duplicate semantic option identity"
            )
        snapshot_by_instrument[identity] = member
    mismatch_ids: list[int] = []
    group_times: dict[
        tuple[str, str, str], tuple[int, list[datetime]]
    ] = {}
    for row in target.fills:
        filled_at = row.filled_at
        observation_id = row.selected_observation_id
        if guard_started_at <= filled_at <= boundary_at:
            guard_fill_ids.append(observation_id)
        if filled_at <= boundary_at:
            pre_boundary_fill_count += 1
        else:
            post_boundary_fill_count += 1
            if row.import_batch_id not in chain_batch_ids:
                unbacked_ids.append(observation_id)
            member = snapshot_by_instrument.get(
                _canonical_instrument_identity(row.instrument)
            )
            if member is not None:
                multiplier = row.instrument.contract_multiplier
                member_multiplier = _decimal(
                    member.contract_multiplier,
                    field_name="snapshot contract multiplier",
                )
                if multiplier is None or multiplier != member_multiplier:
                    mismatch_ids.append(observation_id)
        if row.linked_order_identity is not None:
            representative = observation_id
            current = fill_times_by_order.setdefault(
                row.linked_order_identity,
                (representative, []),
            )
            current[1].append(filled_at)
        if row.execution_group_identity is not None:
            if row.execution_group_member_id is None:
                raise PositionSnapshotContinuityError(
                    "canonical execution group has no frozen member identity"
                )
            current_group = group_times.setdefault(
                row.execution_group_identity,
                (row.execution_group_member_id, []),
            )
            current_group[1].append(filled_at)

    aggregate_ids: list[int] = []
    for row in target.orders:
        quantity = row.filled_quantity or Decimal("0")
        if quantity <= 0:
            continue
        ordered_at = row.ordered_at
        observation_id = row.selected_observation_id
        if ordered_at > boundary_at and row.import_batch_id not in chain_batch_ids:
            unbacked_ids.append(observation_id)
        if row.evidence_level == "aggregate_only" and ordered_at <= boundary_at:
            before = anchor_orders.get(row.identity)
            if before is None or before != row.economic_sha256:
                aggregate_ids.append(observation_id)
        if ordered_at > boundary_at:
            member = snapshot_by_instrument.get(
                _canonical_instrument_identity(row.instrument)
            )
            if member is not None:
                multiplier = row.instrument.contract_multiplier
                member_multiplier = _decimal(
                    member.contract_multiplier,
                    field_name="snapshot contract multiplier",
                )
                if multiplier is None or multiplier != member_multiplier:
                    mismatch_ids.append(observation_id)

    straddling_order_ids = [
        representative
        for _key, (representative, times) in sorted(fill_times_by_order.items())
        if min(times) <= boundary_at < max(times)
    ]
    straddling_group_ids = [
        representative
        for _identity, (representative, times) in group_times.items()
        if min(times) <= boundary_at < max(times)
    ]

    if guard_fill_ids:
        reasons.append(
            _reason(
                "guard_fill_detected",
                "a detailed fill occurred inside the snapshot acquisition guard",
                entity_kind="fill",
                entity_ids=guard_fill_ids,
            )
        )
    if aggregate_ids:
        reasons.append(
            _reason(
                "aggregate_changed_pre_boundary",
                "an aggregate-only execution changed without a precise fill time before the boundary",
                entity_kind="order",
                entity_ids=aggregate_ids,
            )
        )
    if straddling_order_ids:
        reasons.append(
            _reason(
                "straddling_order",
                "a detailed order fill set straddles the proposed boundary",
                entity_kind="order_fill_set",
                entity_ids=straddling_order_ids,
            )
        )
    if straddling_group_ids:
        reasons.append(
            _reason(
                "straddling_group",
                "a combo execution group straddles the proposed boundary",
                entity_kind="execution_group",
                entity_ids=straddling_group_ids,
            )
        )
    if unbacked_ids:
        reasons.append(
            _reason(
                "unbacked_post_boundary",
                "post-boundary selected evidence is outside the published refresh batch chain",
                entity_kind="observation",
                entity_ids=unbacked_ids,
            )
        )
    if mismatch_ids:
        reasons.append(
            _reason(
                "option_identity_mismatch",
                "snapshot and post-boundary option identity or multiplier differ",
                entity_kind="observation",
                entity_ids=mismatch_ids,
            )
        )

    counts = PositionSnapshotContinuityCounts(
        snapshot_member_count=len(snapshot.members),
        chain_publication_count=len(publications),
        chain_batch_count=len(chain_batch_ids),
        target_order_count=len(target.orders),
        target_fill_count=len(target.fills),
        guard_fill_count=len(set(guard_fill_ids)),
        pre_boundary_fill_count=pre_boundary_fill_count,
        post_boundary_fill_count=post_boundary_fill_count,
        aggregate_changed_pre_boundary_count=len(set(aggregate_ids)),
        straddling_order_count=len(straddling_order_ids),
        straddling_group_count=len(straddling_group_ids),
        unbacked_post_boundary_count=len(set(unbacked_ids)),
        option_identity_mismatch_count=len(set(mismatch_ids)),
    )
    return target, target_projection, counts, reasons


def _counts_payload(counts: PositionSnapshotContinuityCounts) -> Mapping[str, int]:
    return {field.name: int(getattr(counts, field.name)) for field in fields(counts)}


def assess_position_snapshot_continuity_in_session(
    connection: Connection,
    session: Session,
    account_key: str,
    *,
    include_episode_projection: bool = False,
    max_member_count: Optional[int] = None,
    require_readonly_session: bool = True,
) -> PositionSnapshotContinuityContext:
    """Assess one account using the caller's already-pinned read transaction."""
    normalized_account = str(account_key or "").strip()
    if not normalized_account or len(normalized_account) > 64:
        raise PositionSnapshotContinuityError("account_key is invalid")
    _require_pinned_readonly_session(
        connection,
        session,
        require_query_only=require_readonly_session,
    )
    try:
        snapshot = _snapshot_and_members(
            connection,
            session,
            normalized_account,
        )
        if snapshot is None:
            return PositionSnapshotContinuityContext(
                assessment=_empty_assessment(
                    normalized_account,
                    _reason(
                        "no_confirmed_snapshot",
                        "no confirmed current-position snapshot exists",
                    ),
                ),
                snapshot=None,
                target_projection=None,
            )
        guard_started_at = snapshot.query_started_at
        boundary_at = snapshot.operation_completed_at
        anchor, publications, artifacts, chain_reasons = _publication_chain(
            connection,
            session,
            snapshot,
            guard_started_at,
            boundary_at,
        )
        base_counts = PositionSnapshotContinuityCounts(
            snapshot_member_count=len(snapshot.members),
            chain_publication_count=len(publications),
            chain_batch_count=len(
                {int(row["import_batch_id"]) for row in publications}
            ),
        )
        if not publications:
            return PositionSnapshotContinuityContext(
                assessment=PositionSnapshotContinuityAssessment(
                    policy_version=POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION,
                    status="awaiting_refresh",
                    ready_for_episode_build=False,
                    fence_key=None,
                    account_key=normalized_account,
                    snapshot_id=snapshot.snapshot_id,
                    snapshot_key=snapshot.snapshot_key,
                    anchor_publication_id=int(anchor["id"]),
                    anchor_publication_key=str(anchor["publication_key"]),
                    target_publication_id=None,
                    target_publication_key=None,
                    target_canonical_set_id=None,
                    target_canonical_set_sha256=None,
                    boundary_at=boundary_at,
                    guard_started_at=guard_started_at,
                    guard_completed_at=boundary_at,
                    publication_ids=(),
                    source_batch_ids=(),
                    counts=base_counts,
                    reasons=(
                        _reason(
                            "no_later_publication",
                            "a confirmed refresh after the snapshot is required",
                        ),
                    ),
                ),
                snapshot=snapshot,
                target_projection=None,
            )

        (
            target,
            target_projection,
            counts,
            canonical_reasons,
        ) = _canonical_checks(
            connection,
            session,
            snapshot=snapshot,
            anchor=anchor,
            publications=publications,
            guard_started_at=guard_started_at,
            boundary_at=boundary_at,
            include_episode_projection=include_episode_projection,
            max_member_count=max_member_count,
        )
        reasons = tuple(chain_reasons + canonical_reasons)
        ready = not reasons
        publication_ids = tuple(int(row["id"]) for row in publications)
        source_batch_ids = tuple(
            sorted({int(row["import_batch_id"]) for row in publications})
        )
        target_publication = publications[-1]
        canonical_hash = target.canonical_set_sha256
        fence_key = None
        if ready:
            fence_key = _sha256(
                {
                    "kind": "position_snapshot_continuity_fence_v1",
                    "policy_version": POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION,
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_key": snapshot.snapshot_key,
                    "anchor_publication_id": int(anchor["id"]),
                    "anchor_publication_key": str(anchor["publication_key"]),
                    "publication_ids": publication_ids,
                    "publication_keys": tuple(
                        str(row["publication_key"]) for row in publications
                    ),
                    "source_batch_ids": source_batch_ids,
                    "refresh_artifacts": tuple(
                        {
                            "artifact_key": row.artifact_key,
                            "source_sha256": row.source_sha256,
                            "evidence_sha256": row.evidence_sha256,
                        }
                        for row in artifacts
                    ),
                    "target_publication_id": int(target_publication["id"]),
                    "target_canonical_set_id": target.canonical_set_id,
                    "target_canonical_set_sha256": canonical_hash,
                    "target_canonical_source_cutoff_at": (
                        target.source_cutoff_at
                    ),
                    "guard_started_at": guard_started_at,
                    "boundary_at": boundary_at,
                    "counts": _counts_payload(counts),
                }
            )
        assessment = PositionSnapshotContinuityAssessment(
            policy_version=POSITION_SNAPSHOT_CONTINUITY_POLICY_VERSION,
            status="ready" if ready else "blocked",
            ready_for_episode_build=ready,
            fence_key=fence_key,
            account_key=normalized_account,
            snapshot_id=snapshot.snapshot_id,
            snapshot_key=snapshot.snapshot_key,
            anchor_publication_id=int(anchor["id"]),
            anchor_publication_key=str(anchor["publication_key"]),
            target_publication_id=int(target_publication["id"]),
            target_publication_key=str(target_publication["publication_key"]),
            target_canonical_set_id=target.canonical_set_id,
            target_canonical_set_sha256=canonical_hash,
            boundary_at=boundary_at,
            guard_started_at=guard_started_at,
            guard_completed_at=boundary_at,
            publication_ids=publication_ids,
            source_batch_ids=source_batch_ids,
            counts=counts,
            reasons=reasons,
        )
        return PositionSnapshotContinuityContext(
            assessment=assessment,
            snapshot=snapshot,
            target_projection=target_projection if ready else None,
        )
    except PositionSnapshotContinuityError:
        raise
    except (
        PositionSnapshotError,
        EpisodeRepositoryError,
        JournalRefreshError,
    ) as exc:
        raise PositionSnapshotContinuityError(str(exc)) from exc
    except (sqlite3.Error, SQLAlchemyError) as exc:
        raise PositionSnapshotContinuityError(
            "cannot read the continuity evidence database"
        ) from exc


def assess_latest_position_snapshot_continuity(
    account_key: str,
) -> PositionSnapshotContinuityAssessment:
    """Prove, without writes, whether the latest snapshot has a safe fence."""
    try:
        with open_position_snapshot_continuity_session() as (
            connection,
            session,
        ):
            return assess_position_snapshot_continuity_in_session(
                connection,
                session,
                account_key,
            ).assessment
    except PositionSnapshotContinuityError:
        raise
    except (sqlite3.Error, SQLAlchemyError) as exc:
        raise PositionSnapshotContinuityError(
            "cannot read the continuity evidence database"
        ) from exc
