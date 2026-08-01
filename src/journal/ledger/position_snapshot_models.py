# -*- coding: utf-8 -*-
"""Append-only persistence models for confirmed current-position evidence.

These tables intentionally do not participate in canonical trade-history or
episode activation.  A confirmed row proves only what the broker returned in
one locally bracketed *current* position acquisition.  In particular, a row
must never be interpreted as proof of a historical opening position.

Decimal values are stored as canonical strings.  That preserves exact broker
quantities and option multipliers across SQLite and other database backends;
the repository validates their numeric domains before writing them.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    DDL,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)

from src.storage import Base

# ``PositionSnapshotArtifact`` carries a foreign key to
# ``journal_v2_refresh_publications``; register those models on the shared
# metadata so any ``create_all`` involving this module resolves the target.
import src.journal.ledger.refresh_models as _refresh_models  # noqa: E402,F401

__all__ = [
    "POSITION_SNAPSHOT_APPEND_ONLY_GUARD_MESSAGE",
    "PositionSnapshotArtifact",
    "ConfirmedPositionSnapshot",
    "PositionSnapshotMember",
    "position_snapshot_guard_trigger_ddl",
    "position_snapshot_guard_trigger_name",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Single authoritative deny-trigger DDL for the snapshot tables.  The model
# hooks, the repository repair path, and ``artifact_gc``'s in-transaction
# trigger recreation must all use this exact text; never hand-copy the trigger
# body elsewhere (New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md §3 step 7).
POSITION_SNAPSHOT_APPEND_ONLY_GUARD_MESSAGE = (
    "position snapshot rows are append-only"
)


def position_snapshot_guard_trigger_name(table_name: str, operation: str) -> str:
    """Deterministic UPDATE/DELETE deny-trigger name for one snapshot table."""
    return f"trg_{table_name}_{operation.lower()}_immutable"


def position_snapshot_guard_trigger_ddl(table_name: str, operation: str) -> str:
    """Authoritative CREATE TRIGGER DDL guarding one snapshot table."""
    trigger = position_snapshot_guard_trigger_name(table_name, operation)
    return (
        f"CREATE TRIGGER IF NOT EXISTS {trigger} "
        f"BEFORE {operation} ON {table_name} "
        "BEGIN SELECT RAISE(ABORT, "
        f"'{POSITION_SNAPSHOT_APPEND_ONLY_GUARD_MESSAGE}'); END"
    )


class PositionSnapshotArtifact(Base):
    """Short-lived, server-owned preview of one broker acquisition.

    The exact normalized payload is retained so confirmation can re-parse and
    re-hash server-side evidence instead of trusting a browser round trip.
    """

    __tablename__ = "journal_v2_position_snapshot_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_key = Column(String(64), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    account_binding_id = Column(String(64), nullable=False)
    binding_scheme = Column(String(64), nullable=False)
    # Nullable for previews produced before Journal continuity exists and for
    # additive migration of early local development databases.  Repository
    # policy requires all three fields together for confirmable artifacts.
    refresh_publication_id = Column(
        Integer,
        ForeignKey("journal_v2_refresh_publications.id"),
    )
    refresh_publication_key = Column(String(128))
    account_continuity_sha256 = Column(String(64))
    environment = Column(String(16), nullable=False)
    market = Column(String(16), nullable=False)
    source_schema = Column(String(96), nullable=False)
    parser_name = Column(String(96), nullable=False)
    parser_version = Column(String(32), nullable=False)

    query_started_at = Column(DateTime(timezone=True), nullable=False)
    query_completed_at = Column(DateTime(timezone=True), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    # Moomoo's position_list_query does not currently expose a broker as-of
    # timestamp.  Keep the column explicit and nullable instead of inventing
    # one from the local completion clock.
    broker_as_of_at = Column(DateTime(timezone=True))

    scope_sha256 = Column(String(64), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    stability_evidence_sha256 = Column(String(64), nullable=False)
    preview_key = Column(String(64), nullable=False)

    retrieval_complete = Column(Boolean, nullable=False)
    position_snapshot_complete = Column(Boolean, nullable=False)
    stability_status = Column(String(24), nullable=False)
    contract_spec_status = Column(String(24), nullable=False)
    position_count = Column(Integer, nullable=False)
    contract_spec_count = Column(Integer, nullable=False)
    confirm_allowed = Column(Boolean, nullable=False)
    historical_opening_proven = Column(Boolean, nullable=False, default=False)

    payload_json = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "artifact_key",
            name="uq_jv2_position_snapshot_artifact_key",
        ),
        CheckConstraint(
            "query_completed_at >= query_started_at",
            name="ck_jv2_position_snapshot_artifact_window",
        ),
        CheckConstraint(
            "generated_at >= query_completed_at",
            name="ck_jv2_position_snapshot_artifact_operation_window",
        ),
        CheckConstraint(
            "broker = 'moomoo' AND environment = 'LIVE' AND market = 'US'",
            name="ck_jv2_position_snapshot_artifact_scope",
        ),
        CheckConstraint(
            "expires_at > recorded_at",
            name="ck_jv2_position_snapshot_artifact_expiry",
        ),
        CheckConstraint(
            "position_count >= 0 AND contract_spec_count >= 0",
            name="ck_jv2_position_snapshot_artifact_counts",
        ),
        CheckConstraint(
            "length(artifact_key) = 64 "
            "AND length(account_binding_id) = 64 "
            "AND length(scope_sha256) = 64 "
            "AND length(source_sha256) = 64 "
            "AND length(evidence_sha256) = 64 "
            "AND length(snapshot_sha256) = 64 "
            "AND length(stability_evidence_sha256) = 64 "
            "AND length(preview_key) = 64",
            name="ck_jv2_position_snapshot_artifact_hashes",
        ),
        CheckConstraint(
            "(refresh_publication_id IS NULL "
            "AND refresh_publication_key IS NULL "
            "AND account_continuity_sha256 IS NULL) OR "
            "(refresh_publication_id IS NOT NULL "
            "AND length(refresh_publication_key) BETWEEN 1 AND 128 "
            "AND length(account_continuity_sha256) = 64)",
            name="ck_jv2_position_snapshot_artifact_continuity",
        ),
        CheckConstraint(
            "broker_as_of_at IS NULL",
            name="ck_jv2_position_snapshot_artifact_broker_asof_unknown",
        ),
        CheckConstraint(
            "historical_opening_proven = 0",
            name="ck_jv2_position_snapshot_artifact_not_historical",
        ),
        Index(
            "ix_jv2_position_snapshot_artifact_account_recorded",
            "broker",
            "account_key",
            "recorded_at",
        ),
        Index(
            "ix_jv2_position_snapshot_artifact_publication",
            "refresh_publication_id",
        ),
    )


class ConfirmedPositionSnapshot(Base):
    """Immutable confirmation receipt for one current-position artifact."""

    __tablename__ = "journal_v2_position_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(64), nullable=False)
    artifact_id = Column(
        Integer,
        ForeignKey("journal_v2_position_snapshot_artifacts.id"),
        nullable=False,
    )
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    account_binding_id = Column(String(64), nullable=False)
    binding_scheme = Column(String(64), nullable=False)
    # These are nullable at the DDL layer only so an early local table can be
    # migrated additively.  Every new confirmation requires and writes them.
    refresh_publication_id = Column(
        Integer,
        ForeignKey("journal_v2_refresh_publications.id"),
    )
    refresh_publication_key = Column(String(128))
    account_continuity_sha256 = Column(String(64))
    environment = Column(String(16), nullable=False)
    market = Column(String(16), nullable=False)

    query_started_at = Column(DateTime(timezone=True), nullable=False)
    query_completed_at = Column(DateTime(timezone=True), nullable=False)
    operation_completed_at = Column(DateTime(timezone=True), nullable=False)
    broker_as_of_at = Column(DateTime(timezone=True))
    scope_sha256 = Column(String(64), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    stability_evidence_sha256 = Column(String(64))
    member_set_sha256 = Column(String(64), nullable=False)
    member_count = Column(Integer, nullable=False)

    acknowledged_future_only = Column(Boolean, nullable=False)
    historical_opening_proven = Column(Boolean, nullable=False, default=False)
    cost_context_only = Column(Boolean, nullable=False, default=True)
    provenance_json = Column(Text, nullable=False)
    provenance_sha256 = Column(String(64))
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_key",
            name="uq_jv2_position_snapshot_key",
        ),
        UniqueConstraint(
            "artifact_id",
            name="uq_jv2_position_snapshot_artifact",
        ),
        CheckConstraint(
            "query_completed_at >= query_started_at",
            name="ck_jv2_position_snapshot_window",
        ),
        CheckConstraint(
            "operation_completed_at >= query_completed_at",
            name="ck_jv2_position_snapshot_operation_window",
        ),
        CheckConstraint(
            "broker = 'moomoo' AND environment = 'LIVE' AND market = 'US'",
            name="ck_jv2_position_snapshot_scope",
        ),
        CheckConstraint(
            "member_count >= 0",
            name="ck_jv2_position_snapshot_member_count",
        ),
        CheckConstraint(
            "length(snapshot_key) = 64 "
            "AND length(account_binding_id) = 64 "
            "AND length(scope_sha256) = 64 "
            "AND length(source_sha256) = 64 "
            "AND length(evidence_sha256) = 64 "
            "AND length(snapshot_sha256) = 64 "
            "AND (stability_evidence_sha256 IS NULL "
            "OR length(stability_evidence_sha256) = 64) "
            "AND length(member_set_sha256) = 64",
            name="ck_jv2_position_snapshot_hashes",
        ),
        CheckConstraint(
            "refresh_publication_id IS NULL OR "
            "(length(refresh_publication_key) BETWEEN 1 AND 128 "
            "AND length(account_continuity_sha256) = 64 "
            "AND length(provenance_sha256) = 64)",
            name="ck_jv2_position_snapshot_continuity",
        ),
        CheckConstraint(
            "acknowledged_future_only = 1",
            name="ck_jv2_position_snapshot_future_ack",
        ),
        CheckConstraint(
            "historical_opening_proven = 0",
            name="ck_jv2_position_snapshot_not_historical",
        ),
        CheckConstraint(
            "cost_context_only = 1",
            name="ck_jv2_position_snapshot_cost_context_only",
        ),
        CheckConstraint(
            "broker_as_of_at IS NULL",
            name="ck_jv2_position_snapshot_broker_asof_unknown",
        ),
        Index(
            "ix_jv2_position_snapshot_account_recorded",
            "broker",
            "account_key",
            "recorded_at",
        ),
        Index(
            "ix_jv2_position_snapshot_publication",
            "refresh_publication_id",
        ),
    )


class PositionSnapshotMember(Base):
    """One option position observed in a confirmed current snapshot.

    Broker cost fields are retained as display-only context.  There are no
    realized-P&L columns and this table is not a fill or cost-basis ledger.
    """

    __tablename__ = "journal_v2_position_snapshot_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(
        Integer,
        ForeignKey("journal_v2_position_snapshots.id"),
        nullable=False,
    )
    member_key = Column(String(64), nullable=False)
    instrument_key = Column(String(64), nullable=False)
    symbol = Column(String(96), nullable=False)
    asset_type = Column(String(24), nullable=False)
    underlying = Column(String(32), nullable=False)
    expiry = Column(Date, nullable=False)
    strike = Column(Text, nullable=False)
    option_right = Column(String(8), nullable=False)
    currency = Column(String(8), nullable=False)
    position_side = Column(String(16), nullable=False)
    quantity_contracts = Column(Text, nullable=False)
    signed_quantity_contracts = Column(Text, nullable=False)
    can_sell_quantity_contracts = Column(Text)
    contract_multiplier = Column(Text, nullable=False)
    contract_multiplier_basis = Column(String(96), nullable=False)

    cost_price = Column(Text)
    cost_price_valid = Column(Boolean)
    average_cost = Column(Text)
    diluted_cost = Column(Text)
    cost_context_semantics = Column(String(96), nullable=False)

    source_position_sha256 = Column(String(64), nullable=False)
    source_contract_spec_sha256 = Column(String(64), nullable=False)
    provenance_json = Column(Text, nullable=False)
    provenance_sha256 = Column(String(64))
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "member_key",
            name="uq_jv2_position_snapshot_member_key",
        ),
        UniqueConstraint(
            "snapshot_id",
            "instrument_key",
            name="uq_jv2_position_snapshot_instrument",
        ),
        CheckConstraint(
            "position_side IN ('LONG', 'SHORT')",
            name="ck_jv2_position_snapshot_member_side",
        ),
        CheckConstraint(
            "asset_type = 'option'",
            name="ck_jv2_position_snapshot_member_asset",
        ),
        CheckConstraint(
            "currency = 'USD'",
            name="ck_jv2_position_snapshot_member_currency",
        ),
        CheckConstraint(
            "option_right IN ('C', 'P')",
            name="ck_jv2_position_snapshot_member_right",
        ),
        CheckConstraint(
            "quantity_contracts <> '' "
            "AND signed_quantity_contracts <> '' "
            "AND contract_multiplier <> '' "
            "AND strike <> ''",
            name="ck_jv2_position_snapshot_member_decimals",
        ),
        CheckConstraint(
            "length(member_key) = 64 "
            "AND length(instrument_key) = 64 "
            "AND length(source_position_sha256) = 64 "
            "AND length(source_contract_spec_sha256) = 64 "
            "AND (provenance_sha256 IS NULL "
            "OR length(provenance_sha256) = 64)",
            name="ck_jv2_position_snapshot_member_hashes",
        ),
        Index(
            "ix_jv2_position_snapshot_member_snapshot",
            "snapshot_id",
            "symbol",
        ),
        Index(
            "ix_jv2_position_snapshot_member_underlying_expiry",
            "underlying",
            "expiry",
        ),
    )


# ``DatabaseManager`` calls ``Base.metadata.create_all`` before any repository
# method is necessarily used.  Table-level hooks therefore install each
# SQLite append-only guard as soon as its table is created, including partial
# ``create_all(tables=...)`` calls.  The repository repeats the same
# idempotent DDL to repair databases whose tables predate this hook.
for _snapshot_model in (
    PositionSnapshotArtifact,
    ConfirmedPositionSnapshot,
    PositionSnapshotMember,
):
    _snapshot_table_name = _snapshot_model.__tablename__
    for _snapshot_operation in ("UPDATE", "DELETE"):
        event.listen(
            _snapshot_model.__table__,
            "after_create",
            DDL(
                position_snapshot_guard_trigger_ddl(
                    _snapshot_table_name,
                    _snapshot_operation,
                )
            ).execute_if(dialect="sqlite"),
        )


def _repair_snapshot_guards_after_create_all(
    _metadata,
    connection,
    **_kwargs,
) -> None:
    """Install guards for snapshot tables that predate this process.

    Table-level hooks cover newly created tables.  This metadata-level repair
    also runs when ``create_all(checkfirst=True)`` finds an existing database,
    while skipping snapshot tables absent from a partial-schema database.
    """
    if connection.dialect.name != "sqlite":
        return
    existing_tables = set(inspect(connection).get_table_names())
    for snapshot_model in (
        PositionSnapshotArtifact,
        ConfirmedPositionSnapshot,
        PositionSnapshotMember,
    ):
        table_name = snapshot_model.__tablename__
        if table_name not in existing_tables:
            continue
        for operation in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                position_snapshot_guard_trigger_ddl(table_name, operation)
            )


event.listen(
    Base.metadata,
    "after_create",
    _repair_snapshot_guards_after_create_all,
)
