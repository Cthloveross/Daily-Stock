# -*- coding: utf-8 -*-
"""Append-only Journal v2 ledger models.

The v2 tables deliberately live beside, rather than replace, the legacy
``journal_*`` tables.  Rows in this module are immutable observations or
versioned build products: corrections are represented by a new import batch
or episode build, never by an ``is_current`` flag or an in-place rewrite.

JSON columns use canonical JSON text so their exact bytes can participate in
content hashes.  Import and storage services are responsible for canonical
serialization and for enforcing the append-only write policy.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from src.storage import Base

# Register the position-snapshot tables on the shared metadata so that
# ``EpisodeBuildSnapshotFenceSource``'s foreign key to
# ``journal_v2_position_snapshots`` always resolves during ``create_all``.
import src.journal.ledger.position_snapshot_models as _position_snapshot_models  # noqa: F401

__all__ = [
    "ImportBatch",
    "ReconciliationAttestation",
    "BrokerOrderObservation",
    "BrokerFillObservation",
    "BrokerFeeObservation",
    "BrokerExecutionGroupObservation",
    "BrokerExecutionGroupLegObservation",
    "BrokerExecutionGroupFillLink",
    "BrokerExecutionGroupFeeObservation",
    "OrderIdentityLink",
    "DealIdentityLink",
    "OrderFillSetAttestation",
    "CanonicalEvidenceSetRecord",
    "CanonicalEvidenceMemberRecord",
    "CanonicalEvidenceIssueRecord",
    "CanonicalEvidenceProvenanceRecord",
    "CanonicalExecutionGroupMemberRecord",
    "CanonicalExecutionGroupLegRecord",
    "CanonicalExecutionGroupFillLinkRecord",
    "CanonicalExecutionGroupProvenanceRecord",
    "EpisodeBuild",
    "EpisodeBuildActivation",
    "EpisodeBuildSnapshotFenceSource",
    "StrategyEpisode",
    "PositionEpisode",
    "PositionEpisodeEvidence",
    "ReviewAnnotation",
]


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for newly recorded immutable rows."""
    return datetime.now(timezone.utc)


class ImportBatch(Base):
    """One finalized, immutable acquisition/parsing result.

    ``batch_key`` is a caller-computed hash over the source content and parser
    contract.  No local path or original filename is retained here.  API and
    CSV inputs therefore share the same provenance boundary without leaking a
    workstation-specific locator.
    """

    __tablename__ = "journal_v2_import_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_kind = Column(String(32), nullable=False)  # csv / openapi
    source_schema = Column(String(64), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    parser_name = Column(String(64), nullable=False)
    parser_version = Column(String(64), nullable=False)

    window_start = Column(DateTime(timezone=True))
    window_end = Column(DateTime(timezone=True))
    source_timezone = Column(String(64))
    status = Column(String(24), nullable=False)  # succeeded / partial / failed
    analysis_level = Column(String(24), nullable=False)
    analysis_ready = Column(Boolean, nullable=False, default=False)
    order_observation_count = Column(Integer, nullable=False, default=0)
    fill_observation_count = Column(Integer, nullable=False, default=0)
    rejected_record_count = Column(Integer, nullable=False, default=0)

    reconciliation_status = Column(String(32), nullable=False)
    reconciliation_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_json = Column(Text, nullable=False)
    warnings_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("batch_key", name="uq_jv2_import_batch_key"),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_import_completeness",
        ),
        CheckConstraint(
            "window_end IS NULL OR window_start IS NULL "
            "OR window_end >= window_start",
            name="ck_jv2_import_window",
        ),
        CheckConstraint(
            "order_observation_count >= 0 "
            "AND fill_observation_count >= 0 "
            "AND rejected_record_count >= 0",
            name="ck_jv2_import_counts",
        ),
        Index(
            "ix_jv2_import_account_recorded",
            "broker",
            "account_key",
            "recorded_at",
        ),
        Index("ix_jv2_import_source_hash", "source_sha256"),
    )


class ReconciliationAttestation(Base):
    """An immutable cross-source check attached to an existing import batch.

    A reconciliation can arrive after the CSV itself was imported.  Keeping it
    in a separate append-only table lets a duplicate source batch gain new
    evidence without mutating the original ``ImportBatch`` row.
    """

    __tablename__ = "journal_v2_reconciliation_attestations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attestation_key = Column(String(128), nullable=False)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_kind = Column(String(32), nullable=False)
    source_sha256 = Column(String(64))
    reconciliation_sha256 = Column(String(64), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(24), nullable=False)
    matched_order_count = Column(Integer, nullable=False)
    reconciliation_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "attestation_key", name="uq_jv2_reconciliation_attestation_key"
        ),
        CheckConstraint(
            "window_end >= window_start", name="ck_jv2_reconciliation_window"
        ),
        CheckConstraint(
            "matched_order_count >= 0", name="ck_jv2_reconciliation_count"
        ),
        Index(
            "ix_jv2_reconciliation_batch_recorded",
            "import_batch_id",
            "recorded_at",
        ),
    )


class BrokerOrderObservation(Base):
    """A broker order exactly as observed in one import batch.

    Multiple batches may observe the same ``source_order_id`` at different
    lifecycle stages.  They remain separate rows.  ``aggregate_only`` CSV
    evidence is valid here and intentionally has no synthetic fill row.
    """

    __tablename__ = "journal_v2_broker_order_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    observation_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_order_id = Column(String(128), nullable=False)
    identity_strength = Column(String(24), nullable=False)
    source_row_number = Column(Integer)
    source_updated_at = Column(DateTime(timezone=True))

    raw_symbol = Column(String(64), nullable=False)
    asset_type = Column(String(24), nullable=False)
    underlying = Column(String(32), nullable=False)
    expiry = Column(Date)
    strike = Column(Numeric(28, 10))
    option_right = Column(String(8))
    contract_multiplier = Column(Numeric(20, 8))

    side = Column(String(24), nullable=False)
    status = Column(String(32), nullable=False)
    order_type = Column(String(32))
    time_in_force = Column(String(24))
    session = Column(String(24))
    currency = Column(String(8), nullable=False)
    ordered_at = Column(DateTime(timezone=True), nullable=False)
    order_quantity = Column(Numeric(28, 10), nullable=False)
    order_price = Column(Numeric(28, 10))
    order_amount = Column(Numeric(28, 10))
    summary_filled_quantity = Column(Numeric(28, 10))
    summary_average_fill_price = Column(Numeric(28, 10))
    total_fee = Column(Numeric(28, 10))
    fee_components_json = Column(Text)

    evidence_level = Column(String(32), nullable=False)
    fee_evidence_status = Column(String(24), nullable=False)
    evidence_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    source_record_sha256 = Column(String(64), nullable=False)
    raw_payload_json = Column(Text)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "import_batch_id",
            "observation_key",
            name="uq_jv2_order_batch_observation",
        ),
        CheckConstraint(
            "order_quantity >= 0", name="ck_jv2_order_quantity"
        ),
        CheckConstraint(
            "summary_filled_quantity IS NULL "
            "OR summary_filled_quantity >= 0",
            name="ck_jv2_order_filled_quantity",
        ),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_order_completeness",
        ),
        Index(
            "ix_jv2_order_source_id",
            "broker",
            "account_key",
            "source_order_id",
            "recorded_at",
        ),
        Index(
            "ix_jv2_order_instrument_time",
            "account_key",
            "underlying",
            "ordered_at",
        ),
    )


class BrokerFillObservation(Base):
    """One detailed broker execution observation.

    A fill may remain unlinked to an order observation when the source itself
    is orphaned.  Order-level fees are not silently allocated to fills;
    ``total_fee`` stays NULL until an explicit allocation method exists.
    """

    __tablename__ = "journal_v2_broker_fill_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    broker_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
    )
    observation_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_order_id = Column(String(128))
    source_deal_id = Column(String(128), nullable=False)
    identity_strength = Column(String(24), nullable=False)
    source_row_number = Column(Integer)

    raw_symbol = Column(String(64), nullable=False)
    asset_type = Column(String(24), nullable=False)
    underlying = Column(String(32), nullable=False)
    expiry = Column(Date)
    strike = Column(Numeric(28, 10))
    option_right = Column(String(8))
    contract_multiplier = Column(Numeric(20, 8))

    side = Column(String(24), nullable=False)
    activity_kind = Column(String(32), nullable=False, default="trade_fill")
    source_status = Column(String(32))
    filled_at = Column(DateTime(timezone=True), nullable=False)
    quantity = Column(Numeric(28, 10), nullable=False)
    price = Column(Numeric(28, 10), nullable=False)
    amount = Column(Numeric(28, 10))
    currency = Column(String(8), nullable=False)
    total_fee = Column(Numeric(28, 10))
    fee_components_json = Column(Text)
    fee_allocation_method = Column(String(32))

    evidence_level = Column(String(32), nullable=False)
    evidence_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    source_record_sha256 = Column(String(64), nullable=False)
    raw_payload_json = Column(Text)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "import_batch_id",
            "observation_key",
            name="uq_jv2_fill_batch_observation",
        ),
        CheckConstraint("quantity > 0", name="ck_jv2_fill_quantity"),
        CheckConstraint("price >= 0", name="ck_jv2_fill_price"),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_fill_completeness",
        ),
        Index(
            "ix_jv2_fill_source_id",
            "broker",
            "account_key",
            "source_deal_id",
            "recorded_at",
        ),
        Index(
            "ix_jv2_fill_order_observation",
            "broker_order_observation_id",
        ),
        Index(
            "ix_jv2_fill_order_id",
            "broker",
            "account_key",
            "source_order_id",
            "filled_at",
        ),
        Index(
            "ix_jv2_fill_instrument_time",
            "account_key",
            "underlying",
            "filled_at",
        ),
    )


class BrokerFeeObservation(Base):
    """One immutable order-level fee observation from a broker source.

    Fees remain first-class observations instead of being represented only on
    an order row.  The order copy is a convenient canonical-reader projection;
    this row preserves the independently hashed broker response and component
    breakdown used to produce it.
    """

    __tablename__ = "journal_v2_broker_fee_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    broker_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
        nullable=False,
    )
    observation_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_order_id = Column(String(128), nullable=False)
    currency = Column(String(8), nullable=False)
    total_fee = Column(Numeric(28, 10), nullable=False)
    fee_components_json = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    source_record_sha256 = Column(String(64), nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "import_batch_id",
            "observation_key",
            name="uq_jv2_fee_batch_observation",
        ),
        UniqueConstraint(
            "import_batch_id",
            "source_order_id",
            name="uq_jv2_fee_batch_order",
        ),
        CheckConstraint(
            "total_fee >= 0",
            name="ck_jv2_fee_total_nonnegative",
        ),
        Index(
            "ix_jv2_fee_source_order",
            "broker",
            "account_key",
            "source_order_id",
            "recorded_at",
        ),
        Index(
            "ix_jv2_fee_order_observation",
            "broker_order_observation_id",
        ),
    )


class BrokerExecutionGroupObservation(Base):
    """One broker combo parent observed in an immutable import batch.

    A combo parent is an execution container, not a tradable instrument.  Its
    package quantity and broker-reported prices are retained for audit, while
    economic reconstruction uses only the linked, instrument-specific fills.
    Group fees deliberately live in ``BrokerExecutionGroupFeeObservation``.
    """

    __tablename__ = "journal_v2_broker_execution_group_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    observation_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_execution_group_id = Column(String(128), nullable=False)
    identity_strength = Column(String(24), nullable=False)
    source_row_number = Column(Integer)
    source_updated_at = Column(DateTime(timezone=True))

    raw_strategy_type = Column(String(64), nullable=False)
    strategy_type = Column(String(64), nullable=False)
    raw_parent_symbol = Column(String(128), nullable=False)
    parent_side = Column(String(24), nullable=False)
    status = Column(String(32), nullable=False)
    order_type = Column(String(32))
    time_in_force = Column(String(24))
    session = Column(String(24))
    fill_outside_rth = Column(Boolean)
    currency = Column(String(8), nullable=False)
    ordered_at = Column(DateTime(timezone=True), nullable=False)

    # Parent qty is a number of combo packages.  ``dealt_quantity`` and both
    # price fields preserve the broker response only; they are not leg-level
    # quantities/prices and must not be used for PositionEpisode economics.
    group_order_quantity = Column(Numeric(28, 10), nullable=False)
    broker_reported_dealt_quantity = Column(Numeric(28, 10), nullable=False)
    broker_reported_order_price = Column(Numeric(28, 10))
    broker_reported_net_average_price = Column(Numeric(28, 10))
    parent_quantity_semantics = Column(String(48), nullable=False)
    parent_price_semantics = Column(String(48), nullable=False)

    evidence_level = Column(String(32), nullable=False)
    fee_evidence_status = Column(String(24), nullable=False)
    evidence_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    source_record_sha256 = Column(String(64), nullable=False)
    raw_payload_json = Column(Text)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "import_batch_id",
            "observation_key",
            name="uq_jv2_group_batch_observation",
        ),
        UniqueConstraint(
            "import_batch_id",
            "source_execution_group_id",
            name="uq_jv2_group_batch_source_id",
        ),
        CheckConstraint(
            "group_order_quantity > 0",
            name="ck_jv2_group_order_quantity",
        ),
        CheckConstraint(
            "broker_reported_dealt_quantity >= 0",
            name="ck_jv2_group_dealt_quantity",
        ),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_group_completeness",
        ),
        Index(
            "ix_jv2_group_source_id",
            "broker",
            "account_key",
            "source_execution_group_id",
            "recorded_at",
        ),
        Index(
            "ix_jv2_group_account_time",
            "account_key",
            "ordered_at",
        ),
    )


class BrokerExecutionGroupLegObservation(Base):
    """One declared instrument leg belonging to a combo parent observation."""

    __tablename__ = "journal_v2_broker_execution_group_leg_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    execution_group_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_observations.id"),
        nullable=False,
    )
    observation_key = Column(String(128), nullable=False)
    leg_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_execution_group_id = Column(String(128), nullable=False)
    leg_index = Column(Integer, nullable=False)

    raw_symbol = Column(String(64), nullable=False)
    asset_type = Column(String(24), nullable=False)
    underlying = Column(String(32), nullable=False)
    expiry = Column(Date)
    strike = Column(Numeric(28, 10))
    option_right = Column(String(8))
    contract_multiplier = Column(Numeric(20, 8))
    contract_multiplier_basis = Column(String(32), nullable=False)
    side = Column(String(24), nullable=False)
    quantity_ratio = Column(Numeric(28, 10), nullable=False)
    currency = Column(String(8), nullable=False)

    evidence_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    source_record_sha256 = Column(String(64), nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "import_batch_id",
            "observation_key",
            name="uq_jv2_group_leg_batch_observation",
        ),
        UniqueConstraint(
            "execution_group_observation_id",
            "leg_key",
            name="uq_jv2_group_leg_key",
        ),
        UniqueConstraint(
            "execution_group_observation_id",
            "leg_index",
            name="uq_jv2_group_leg_index",
        ),
        UniqueConstraint(
            "execution_group_observation_id",
            "raw_symbol",
            "side",
            name="uq_jv2_group_leg_instrument_side",
        ),
        CheckConstraint("leg_index >= 0", name="ck_jv2_group_leg_index"),
        CheckConstraint(
            "quantity_ratio > 0",
            name="ck_jv2_group_leg_quantity_ratio",
        ),
        CheckConstraint(
            "(contract_multiplier IS NULL "
            "AND contract_multiplier_basis = 'unknown') "
            "OR (contract_multiplier > 0 "
            "AND contract_multiplier_basis IN "
            "('asset_definition', 'broker_stated', "
            "'evidence_derived_from_amount'))",
            name="ck_jv2_group_leg_multiplier",
        ),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_group_leg_completeness",
        ),
        Index(
            "ix_jv2_group_leg_group",
            "execution_group_observation_id",
            "leg_index",
        ),
        Index(
            "ix_jv2_group_leg_instrument",
            "account_key",
            "underlying",
            "expiry",
        ),
    )


class BrokerExecutionGroupFillLink(Base):
    """Audited parent/leg assignment for one broker fill observation."""

    __tablename__ = "journal_v2_broker_execution_group_fill_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    execution_group_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_observations.id"),
        nullable=False,
    )
    execution_group_leg_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_leg_observations.id"),
        nullable=False,
    )
    broker_fill_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_fill_observations.id"),
        nullable=False,
    )
    link_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_execution_group_id = Column(String(128), nullable=False)
    source_deal_id = Column(String(128), nullable=False)
    link_method = Column(String(64), nullable=False)
    identity_strength = Column(String(24), nullable=False)
    evidence_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("link_key", name="uq_jv2_group_fill_link_key"),
        UniqueConstraint(
            "broker_fill_observation_id",
            name="uq_jv2_group_fill_observation",
        ),
        UniqueConstraint(
            "execution_group_observation_id",
            "source_deal_id",
            name="uq_jv2_group_fill_source_deal",
        ),
        Index(
            "ix_jv2_group_fill_group",
            "execution_group_observation_id",
            "execution_group_leg_observation_id",
        ),
        Index(
            "ix_jv2_group_fill_fill",
            "broker_fill_observation_id",
        ),
    )


class BrokerExecutionGroupFeeObservation(Base):
    """One immutable fee fact retained only at execution-group scope."""

    __tablename__ = "journal_v2_broker_execution_group_fee_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    execution_group_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_observations.id"),
        nullable=False,
    )
    observation_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    source_execution_group_id = Column(String(128), nullable=False)
    currency = Column(String(8), nullable=False)
    total_fee = Column(Numeric(28, 10), nullable=False)
    fee_components_json = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    source_record_sha256 = Column(String(64), nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "import_batch_id",
            "observation_key",
            name="uq_jv2_group_fee_batch_observation",
        ),
        UniqueConstraint(
            "import_batch_id",
            "source_execution_group_id",
            name="uq_jv2_group_fee_batch_source",
        ),
        UniqueConstraint(
            "execution_group_observation_id",
            name="uq_jv2_group_fee_group",
        ),
        CheckConstraint(
            "total_fee >= 0",
            name="ck_jv2_group_fee_total_nonnegative",
        ),
        Index(
            "ix_jv2_group_fee_source",
            "broker",
            "account_key",
            "source_execution_group_id",
            "recorded_at",
        ),
        Index(
            "ix_jv2_group_fee_group",
            "execution_group_observation_id",
        ),
    )


class OrderIdentityLink(Base):
    """Audited alias from one observation ID to a stable broker order ID."""

    __tablename__ = "journal_v2_order_identity_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_key = Column(String(128), nullable=False)
    broker_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
        nullable=False,
    )
    counterpart_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
    )
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    canonical_order_id = Column(String(128), nullable=False)
    link_method = Column(String(64), nullable=False)
    identity_strength = Column(String(24), nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    evidence_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("link_key", name="uq_jv2_order_identity_link_key"),
        UniqueConstraint(
            "broker_order_observation_id",
            name="uq_jv2_order_identity_observation",
        ),
        UniqueConstraint(
            "counterpart_order_observation_id",
            name="uq_jv2_order_identity_counterpart",
        ),
        CheckConstraint(
            "counterpart_order_observation_id IS NULL "
            "OR counterpart_order_observation_id != broker_order_observation_id",
            name="ck_jv2_order_identity_distinct_counterpart",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_jv2_order_identity_confidence",
        ),
        Index(
            "ix_jv2_order_identity_canonical",
            "broker",
            "account_key",
            "canonical_order_id",
        ),
    )


class DealIdentityLink(Base):
    """Audited one-to-one alias from a derived fill to a broker deal ID."""

    __tablename__ = "journal_v2_deal_identity_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_key = Column(String(128), nullable=False)
    broker_fill_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_fill_observations.id"),
        nullable=False,
    )
    counterpart_fill_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_fill_observations.id"),
        nullable=False,
    )
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    canonical_order_id = Column(String(128), nullable=False)
    canonical_deal_id = Column(String(128), nullable=False)
    link_method = Column(String(64), nullable=False)
    identity_strength = Column(String(24), nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    evidence_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("link_key", name="uq_jv2_deal_identity_link_key"),
        UniqueConstraint(
            "broker_fill_observation_id",
            name="uq_jv2_deal_identity_observation",
        ),
        UniqueConstraint(
            "counterpart_fill_observation_id",
            name="uq_jv2_deal_identity_counterpart",
        ),
        CheckConstraint(
            "counterpart_fill_observation_id != broker_fill_observation_id",
            name="ck_jv2_deal_identity_distinct_counterpart",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_jv2_deal_identity_confidence",
        ),
        Index(
            "ix_jv2_deal_identity_canonical",
            "broker",
            "account_key",
            "canonical_order_id",
            "canonical_deal_id",
        ),
    )


class OrderFillSetAttestation(Base):
    """Order-level proof that one detailed fill set shadows another set."""

    __tablename__ = "journal_v2_order_fill_set_attestations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attestation_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    canonical_order_id = Column(String(128), nullable=False)
    authoritative_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
        nullable=False,
    )
    shadow_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
        nullable=False,
    )
    authoritative_fill_set_sha256 = Column(String(64), nullable=False)
    shadow_fill_set_sha256 = Column(String(64), nullable=False)
    authoritative_fill_count = Column(Integer, nullable=False)
    shadow_fill_count = Column(Integer, nullable=False)
    authoritative_quantity = Column(Numeric(28, 10), nullable=False)
    shadow_quantity = Column(Numeric(28, 10), nullable=False)
    authoritative_vwap = Column(Numeric(28, 10), nullable=False)
    shadow_vwap = Column(Numeric(28, 10), nullable=False)
    method = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False)
    evidence_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "attestation_key", name="uq_jv2_order_fill_set_attestation_key"
        ),
        UniqueConstraint(
            "authoritative_order_observation_id",
            "shadow_order_observation_id",
            name="uq_jv2_order_fill_set_pair",
        ),
        UniqueConstraint(
            "broker",
            "account_key",
            "canonical_order_id",
            name="uq_jv2_order_fill_set_canonical_order",
        ),
        CheckConstraint(
            "authoritative_order_observation_id != shadow_order_observation_id",
            name="ck_jv2_order_fill_set_distinct_orders",
        ),
        CheckConstraint(
            "authoritative_fill_count > 0 AND shadow_fill_count > 0",
            name="ck_jv2_order_fill_set_counts",
        ),
        CheckConstraint(
            "authoritative_quantity > 0 AND shadow_quantity > 0 "
            "AND authoritative_vwap >= 0 AND shadow_vwap >= 0",
            name="ck_jv2_order_fill_set_values",
        ),
        Index(
            "ix_jv2_order_fill_set_canonical",
            "broker",
            "account_key",
            "canonical_order_id",
        ),
    )


class CanonicalEvidenceSetRecord(Base):
    """Versioned immutable output of the cross-batch canonical reader."""

    __tablename__ = "journal_v2_canonical_evidence_sets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    set_key = Column(String(128), nullable=False)
    canonical_set_sha256 = Column(String(64), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    reader_name = Column(String(64), nullable=False)
    reader_version = Column(String(64), nullable=False)
    source_cutoff_at = Column(DateTime(timezone=True), nullable=False)
    source_batch_ids_json = Column(Text, nullable=False)
    analysis_ready = Column(Boolean, nullable=False)
    canonical_order_count = Column(Integer, nullable=False)
    canonical_fill_count = Column(Integer, nullable=False)
    blocking_issue_count = Column(Integer, nullable=False)
    canonical_payload_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("set_key", name="uq_jv2_canonical_set_key"),
        CheckConstraint(
            "canonical_order_count >= 0 AND canonical_fill_count >= 0 "
            "AND blocking_issue_count >= 0",
            name="ck_jv2_canonical_set_counts",
        ),
        Index(
            "ix_jv2_canonical_set_account_recorded",
            "broker",
            "account_key",
            "recorded_at",
        ),
        Index(
            "ix_jv2_canonical_set_hash",
            "canonical_set_sha256",
        ),
    )


class CanonicalEvidenceMemberRecord(Base):
    """Replayable canonical order/fill member with its selected source row."""

    __tablename__ = "journal_v2_canonical_evidence_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    member_key = Column(String(128), nullable=False)
    entity_kind = Column(String(16), nullable=False)
    identity_key = Column(String(384), nullable=False)
    selected_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
    )
    selected_fill_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_fill_observations.id"),
    )
    canonical_payload_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_set_id",
            "member_key",
            name="uq_jv2_canonical_member_key",
        ),
        CheckConstraint(
            "(entity_kind = 'order' "
            "AND selected_order_observation_id IS NOT NULL "
            "AND selected_fill_observation_id IS NULL) "
            "OR (entity_kind = 'fill' "
            "AND selected_order_observation_id IS NULL "
            "AND selected_fill_observation_id IS NOT NULL)",
            name="ck_jv2_canonical_member_selected_source",
        ),
        Index(
            "ix_jv2_canonical_member_set",
            "canonical_set_id",
            "entity_kind",
        ),
    )


class CanonicalEvidenceIssueRecord(Base):
    """One deterministic issue emitted for a canonical evidence set."""

    __tablename__ = "journal_v2_canonical_evidence_issues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    issue_key = Column(String(128), nullable=False)
    severity = Column(String(24), nullable=False)
    code = Column(String(64), nullable=False)
    entity_kind = Column(String(32), nullable=False)
    entity_key = Column(String(384), nullable=False)
    field_name = Column(String(64))
    issue_json = Column(Text, nullable=False)
    evidence_refs_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_set_id",
            "issue_key",
            name="uq_jv2_canonical_issue_key",
        ),
        Index("ix_jv2_canonical_issue_set", "canonical_set_id", "severity"),
    )


class CanonicalEvidenceProvenanceRecord(Base):
    """One immutable source-observation membership in a canonical set."""

    __tablename__ = "journal_v2_canonical_evidence_provenance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    provenance_key = Column(String(128), nullable=False)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    broker_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
    )
    broker_fill_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_fill_observations.id"),
    )
    broker_fee_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_fee_observations.id"),
    )
    evidence_kind = Column(String(32), nullable=False)
    evidence_role = Column(String(32), nullable=False)
    evidence_ref_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_set_id",
            "provenance_key",
            name="uq_jv2_canonical_provenance_key",
        ),
        CheckConstraint(
            "(CASE WHEN broker_order_observation_id IS NULL THEN 0 ELSE 1 END "
            "+ CASE WHEN broker_fill_observation_id IS NULL THEN 0 ELSE 1 END "
            "+ CASE WHEN broker_fee_observation_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_jv2_canonical_provenance_one_source",
        ),
        Index(
            "ix_jv2_canonical_provenance_set",
            "canonical_set_id",
            "evidence_kind",
        ),
    )


class CanonicalExecutionGroupMemberRecord(Base):
    """One frozen canonical combo execution group.

    The parent package fields remain audit-only.  ``proved_executed_group_quantity``
    is derived from balanced leg fills, never from the broker parent ``dealt_qty``.
    The fee projection remains group-scoped and is not a leg allocation.
    """

    __tablename__ = "journal_v2_canonical_execution_group_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    member_key = Column(String(128), nullable=False)
    identity_key = Column(String(384), nullable=False)
    canonical_execution_group_id = Column(String(128), nullable=False)
    selected_execution_group_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_observations.id"),
        nullable=False,
    )

    strategy_type = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    currency = Column(String(8), nullable=False)
    group_order_quantity = Column(Numeric(28, 10), nullable=False)
    broker_reported_dealt_quantity = Column(Numeric(28, 10), nullable=False)
    broker_reported_order_price = Column(Numeric(28, 10))
    broker_reported_net_average_price = Column(Numeric(28, 10))
    proved_executed_group_quantity = Column(Numeric(28, 10))
    parent_quantity_semantics = Column(String(48), nullable=False)
    parent_price_semantics = Column(String(48), nullable=False)

    group_total_fee = Column(Numeric(28, 10))
    group_fee_components_json = Column(Text)
    fee_evidence_status = Column(String(24), nullable=False)
    fee_scope_policy = Column(String(48), nullable=False)

    canonical_payload_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_set_id",
            "member_key",
            name="uq_jv2_canonical_group_member_key",
        ),
        UniqueConstraint(
            "canonical_set_id",
            "identity_key",
            name="uq_jv2_canonical_group_identity",
        ),
        UniqueConstraint(
            "canonical_set_id",
            "selected_execution_group_observation_id",
            name="uq_jv2_canonical_group_selected_source",
        ),
        CheckConstraint(
            "group_order_quantity > 0",
            name="ck_jv2_canonical_group_order_quantity",
        ),
        CheckConstraint(
            "broker_reported_dealt_quantity >= 0",
            name="ck_jv2_canonical_group_dealt_quantity",
        ),
        CheckConstraint(
            "proved_executed_group_quantity IS NULL "
            "OR proved_executed_group_quantity >= 0",
            name="ck_jv2_canonical_group_executed_quantity",
        ),
        CheckConstraint(
            "group_total_fee IS NULL OR group_total_fee >= 0",
            name="ck_jv2_canonical_group_fee",
        ),
        CheckConstraint(
            "(group_total_fee IS NULL AND group_fee_components_json IS NULL) "
            "OR (group_total_fee IS NOT NULL "
            "AND group_fee_components_json IS NOT NULL)",
            name="ck_jv2_canonical_group_fee_pair",
        ),
        Index(
            "ix_jv2_canonical_group_set",
            "canonical_set_id",
            "canonical_execution_group_id",
        ),
        Index(
            "ix_jv2_canonical_group_selected",
            "selected_execution_group_observation_id",
        ),
    )


class CanonicalExecutionGroupLegRecord(Base):
    """One frozen, fee-free leg inside a canonical execution group."""

    __tablename__ = "journal_v2_canonical_execution_group_legs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    canonical_execution_group_member_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_execution_group_members.id"),
        nullable=False,
    )
    selected_execution_group_leg_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_leg_observations.id"),
        nullable=False,
    )
    leg_key = Column(String(128), nullable=False)
    identity_key = Column(String(384), nullable=False)
    leg_index = Column(Integer, nullable=False)

    raw_symbol = Column(String(64), nullable=False)
    asset_type = Column(String(24), nullable=False)
    underlying = Column(String(32), nullable=False)
    expiry = Column(Date)
    strike = Column(Numeric(28, 10))
    option_right = Column(String(8))
    contract_multiplier = Column(Numeric(20, 8))
    contract_multiplier_basis = Column(String(32), nullable=False)
    side = Column(String(24), nullable=False)
    quantity_ratio = Column(Numeric(28, 10), nullable=False)
    executed_quantity = Column(Numeric(28, 10), nullable=False)
    proved_executed_group_quantity = Column(Numeric(28, 10))
    fill_count = Column(Integer, nullable=False)
    currency = Column(String(8), nullable=False)

    canonical_payload_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_execution_group_member_id",
            "leg_key",
            name="uq_jv2_canonical_group_leg_key",
        ),
        UniqueConstraint(
            "canonical_execution_group_member_id",
            "leg_index",
            name="uq_jv2_canonical_group_leg_index",
        ),
        UniqueConstraint(
            "canonical_execution_group_member_id",
            "identity_key",
            name="uq_jv2_canonical_group_leg_identity",
        ),
        UniqueConstraint(
            "canonical_set_id",
            "selected_execution_group_leg_observation_id",
            name="uq_jv2_canonical_group_leg_selected",
        ),
        CheckConstraint(
            "leg_index >= 0",
            name="ck_jv2_canonical_group_leg_index",
        ),
        CheckConstraint(
            "quantity_ratio > 0",
            name="ck_jv2_canonical_group_leg_ratio",
        ),
        CheckConstraint(
            "(executed_quantity = 0 AND fill_count = 0) "
            "OR (executed_quantity > 0 AND fill_count > 0)",
            name="ck_jv2_canonical_group_leg_execution",
        ),
        CheckConstraint(
            "proved_executed_group_quantity IS NULL "
            "OR proved_executed_group_quantity >= 0",
            name="ck_jv2_canonical_group_leg_group_quantity",
        ),
        CheckConstraint(
            "(contract_multiplier IS NULL "
            "AND contract_multiplier_basis = 'unknown') "
            "OR (contract_multiplier > 0 "
            "AND contract_multiplier_basis IN "
            "('asset_definition', 'broker_stated', "
            "'evidence_derived_from_amount'))",
            name="ck_jv2_canonical_group_leg_multiplier",
        ),
        Index(
            "ix_jv2_canonical_group_leg_group",
            "canonical_execution_group_member_id",
            "leg_index",
        ),
        Index(
            "ix_jv2_canonical_group_leg_instrument",
            "canonical_set_id",
            "underlying",
            "expiry",
        ),
    )


class CanonicalExecutionGroupFillLinkRecord(Base):
    """Frozen binding from a canonical fill member to one canonical group leg."""

    __tablename__ = "journal_v2_canonical_execution_group_fill_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    canonical_execution_group_member_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_execution_group_members.id"),
        nullable=False,
    )
    canonical_execution_group_leg_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_execution_group_legs.id"),
        nullable=False,
    )
    canonical_fill_member_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_members.id"),
        nullable=False,
    )
    selected_execution_group_fill_link_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_fill_links.id"),
        nullable=False,
    )
    link_key = Column(String(128), nullable=False)
    canonical_deal_id = Column(String(128), nullable=False)
    link_method = Column(String(64), nullable=False)
    fee_scope = Column(String(32), nullable=False)
    canonical_payload_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_set_id",
            "link_key",
            name="uq_jv2_canonical_group_fill_link_key",
        ),
        UniqueConstraint(
            "canonical_set_id",
            "canonical_fill_member_id",
            name="uq_jv2_canonical_group_fill_member",
        ),
        UniqueConstraint(
            "canonical_set_id",
            "selected_execution_group_fill_link_id",
            name="uq_jv2_canonical_group_fill_selected",
        ),
        UniqueConstraint(
            "canonical_execution_group_member_id",
            "canonical_deal_id",
            name="uq_jv2_canonical_group_fill_deal",
        ),
        CheckConstraint(
            "fee_scope = 'execution_group'",
            name="ck_jv2_canonical_group_fill_fee_scope",
        ),
        Index(
            "ix_jv2_canonical_group_fill_group",
            "canonical_execution_group_member_id",
            "canonical_execution_group_leg_id",
        ),
        Index(
            "ix_jv2_canonical_group_fill_member",
            "canonical_fill_member_id",
        ),
    )


class CanonicalExecutionGroupProvenanceRecord(Base):
    """One raw group/leg/link/fee source retained by a canonical group."""

    __tablename__ = "journal_v2_canonical_execution_group_provenance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    canonical_execution_group_member_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_execution_group_members.id"),
        nullable=False,
    )
    provenance_key = Column(String(128), nullable=False)
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    execution_group_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_observations.id"),
    )
    execution_group_leg_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_leg_observations.id"),
    )
    execution_group_fill_link_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_fill_links.id"),
    )
    execution_group_fee_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_execution_group_fee_observations.id"),
    )
    evidence_kind = Column(String(32), nullable=False)
    evidence_role = Column(String(32), nullable=False)
    evidence_ref_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "canonical_set_id",
            "provenance_key",
            name="uq_jv2_canonical_group_provenance_key",
        ),
        CheckConstraint(
            "(CASE WHEN execution_group_observation_id IS NULL THEN 0 ELSE 1 END "
            "+ CASE WHEN execution_group_leg_observation_id IS NULL THEN 0 ELSE 1 END "
            "+ CASE WHEN execution_group_fill_link_id IS NULL THEN 0 ELSE 1 END "
            "+ CASE WHEN execution_group_fee_observation_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_jv2_canonical_group_provenance_one_source",
        ),
        CheckConstraint(
            "(evidence_kind = 'execution_group' "
            "AND execution_group_observation_id IS NOT NULL) "
            "OR (evidence_kind = 'execution_group_leg' "
            "AND execution_group_leg_observation_id IS NOT NULL) "
            "OR (evidence_kind = 'execution_group_fill_link' "
            "AND execution_group_fill_link_id IS NOT NULL) "
            "OR (evidence_kind = 'execution_group_fee' "
            "AND execution_group_fee_observation_id IS NOT NULL)",
            name="ck_jv2_canonical_group_provenance_kind",
        ),
        Index(
            "ix_jv2_canonical_group_provenance_set",
            "canonical_set_id",
            "evidence_kind",
        ),
        Index(
            "ix_jv2_canonical_group_provenance_member",
            "canonical_execution_group_member_id",
            "evidence_role",
        ),
    )


class EpisodeBuild(Base):
    """One immutable, reproducible episode reconstruction result."""

    __tablename__ = "journal_v2_episode_builds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    build_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    builder_name = Column(String(64), nullable=False)
    builder_version = Column(String(64), nullable=False)
    builder_config_sha256 = Column(String(64), nullable=False)
    evidence_set_sha256 = Column(String(64), nullable=False)
    source_batch_ids_json = Column(Text, nullable=False)
    source_cutoff_at = Column(DateTime(timezone=True), nullable=False)

    status = Column(String(24), nullable=False)  # succeeded / partial / failed
    strategy_episode_count = Column(Integer, nullable=False, default=0)
    position_episode_count = Column(Integer, nullable=False, default=0)
    unresolved_evidence_count = Column(Integer, nullable=False, default=0)
    reconciliation_status = Column(String(32), nullable=False)
    build_report_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("build_key", name="uq_jv2_episode_build_key"),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_build_completeness",
        ),
        CheckConstraint(
            "strategy_episode_count >= 0 "
            "AND position_episode_count >= 0 "
            "AND unresolved_evidence_count >= 0",
            name="ck_jv2_build_counts",
        ),
        Index(
            "ix_jv2_build_account_recorded",
            "broker",
            "account_key",
            "recorded_at",
        ),
        Index("ix_jv2_build_evidence_hash", "evidence_set_sha256"),
    )


class EpisodeBuildCanonicalSource(Base):
    """Immutable binding from one EpisodeBuild to its frozen canonical set."""

    __tablename__ = "journal_v2_episode_build_canonical_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_key = Column(String(128), nullable=False)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    canonical_set_sha256 = Column(String(64), nullable=False)
    projection_name = Column(String(64), nullable=False)
    projection_version = Column(String(64), nullable=False)
    canonical_source_cutoff_at = Column(DateTime(timezone=True), nullable=False)
    source_batch_ids_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("link_key", name="uq_jv2_episode_canonical_link_key"),
        UniqueConstraint(
            "episode_build_id",
            name="uq_jv2_episode_canonical_build",
        ),
        CheckConstraint(
            "length(canonical_set_sha256) = 64",
            name="ck_jv2_episode_canonical_hash",
        ),
        Index(
            "ix_jv2_episode_canonical_set",
            "canonical_set_id",
        ),
    )


class EpisodeBuildSnapshotFenceSource(Base):
    """Immutable binding from one EpisodeBuild to its verified snapshot fence.

    A row proves which confirmed current-position snapshot, continuity fence,
    and frozen target canonical set produced one formal future Episode build.
    ``projection_name``/``projection_version`` mirror the fenced preview
    projection constants; the append path passes them in rather than
    re-declaring literals here.
    """

    __tablename__ = "journal_v2_episode_build_snapshot_fence_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_key = Column(String(128), nullable=False)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    snapshot_id = Column(
        Integer,
        ForeignKey("journal_v2_position_snapshots.id"),
        nullable=False,
    )
    snapshot_key = Column(String(64), nullable=False)
    fence_key = Column(String(64), nullable=False)
    continuity_policy_version = Column(String(64), nullable=False)
    target_publication_id = Column(Integer, nullable=False)
    target_publication_key = Column(String(128), nullable=False)
    target_canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    target_canonical_set_sha256 = Column(String(64), nullable=False)
    source_cutoff_at = Column(DateTime(timezone=True), nullable=False)
    boundary_at = Column(DateTime(timezone=True), nullable=False)
    projection_name = Column(String(64), nullable=False)
    projection_version = Column(String(64), nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "link_key",
            name="uq_jv2_episode_snapshot_fence_link_key",
        ),
        UniqueConstraint(
            "episode_build_id",
            name="uq_jv2_episode_snapshot_fence_build",
        ),
        CheckConstraint(
            "length(target_canonical_set_sha256) = 64",
            name="ck_jv2_episode_snapshot_fence_hash",
        ),
        CheckConstraint(
            "length(fence_key) = 64",
            name="ck_jv2_episode_snapshot_fence_key",
        ),
        Index(
            "ix_jv2_episode_snapshot_fence_snapshot",
            "snapshot_id",
        ),
        Index(
            "ix_jv2_episode_snapshot_fence_target_set",
            "target_canonical_set_id",
        ),
    )


class EpisodeBuildActivation(Base):
    """One immutable event selecting the effective canonical Episode build.

    Selection changes are represented by appending another event.  The chain
    may therefore move from build A to B and later back to A without mutating
    either an Episode build or a prior activation record.
    """

    __tablename__ = "journal_v2_episode_build_activations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activation_key = Column(String(64), nullable=False)
    account_key = Column(String(64), nullable=False)
    activation_sequence = Column(Integer, nullable=False)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    episode_build_key = Column(String(64), nullable=False)
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    canonical_set_sha256 = Column(String(64), nullable=False)
    previous_activation_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_build_activations.id"),
    )
    previous_episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
    )
    activated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "activation_key",
            name="uq_jv2_episode_build_activation_key",
        ),
        UniqueConstraint(
            "account_key",
            "activation_sequence",
            name="uq_jv2_episode_build_activation_sequence",
        ),
        UniqueConstraint(
            "previous_activation_id",
            name="uq_jv2_episode_build_activation_previous",
        ),
        CheckConstraint(
            "activation_sequence >= 1",
            name="ck_jv2_episode_build_activation_sequence",
        ),
        CheckConstraint(
            "length(activation_key) = 64 "
            "AND length(episode_build_key) = 64 "
            "AND length(canonical_set_sha256) = 64",
            name="ck_jv2_episode_build_activation_hashes",
        ),
        CheckConstraint(
            "(activation_sequence = 1 AND previous_activation_id IS NULL) "
            "OR (activation_sequence > 1 "
            "AND previous_activation_id IS NOT NULL)",
            name="ck_jv2_episode_build_activation_chain",
        ),
        Index(
            "ix_jv2_episode_build_activation_account",
            "account_key",
            "activation_sequence",
        ),
        Index(
            "ix_jv2_episode_build_activation_target",
            "episode_build_id",
        ),
    )


class StrategyEpisode(Base):
    """A versioned strategy lifecycle built from one evidence set."""

    __tablename__ = "journal_v2_strategy_episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    episode_key = Column(String(128), nullable=False)
    lineage_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)

    underlying = Column(String(32), nullable=False)
    strategy_type = Column(String(64), nullable=False)
    direction = Column(String(32))
    lifecycle_status = Column(String(32), nullable=False)
    grouping_method = Column(String(32), nullable=False)
    grouping_confidence = Column(Numeric(5, 4), nullable=False)
    roll_chain_key = Column(String(128))
    continuation_of_lineage_key = Column(String(128))
    currency = Column(String(8), nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True))
    position_count = Column(Integer, nullable=False)
    leg_count = Column(Integer, nullable=False)

    opening_cash_flow = Column(Numeric(28, 10))
    closing_cash_flow = Column(Numeric(28, 10))
    realized_pnl_gross = Column(Numeric(28, 10))
    total_fee = Column(Numeric(28, 10))
    realized_pnl_net = Column(Numeric(28, 10))
    pnl_precision = Column(String(24), nullable=False)
    max_profit = Column(Numeric(28, 10))
    max_loss = Column(Numeric(28, 10))

    grouping_evidence_json = Column(Text, nullable=False)
    evidence_summary_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_status = Column(String(24), nullable=False)
    completeness_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "episode_build_id",
            "episode_key",
            name="uq_jv2_strategy_build_episode",
        ),
        CheckConstraint(
            "closed_at IS NULL OR closed_at >= opened_at",
            name="ck_jv2_strategy_window",
        ),
        CheckConstraint(
            "position_count >= 0 AND leg_count >= 0",
            name="ck_jv2_strategy_counts",
        ),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_strategy_completeness",
        ),
        CheckConstraint(
            "grouping_confidence >= 0 AND grouping_confidence <= 1",
            name="ck_jv2_strategy_grouping_confidence",
        ),
        Index(
            "ix_jv2_strategy_account_opened",
            "account_key",
            "opened_at",
        ),
        Index(
            "ix_jv2_strategy_underlying_opened",
            "underlying",
            "opened_at",
        ),
        Index("ix_jv2_strategy_lineage", "lineage_key"),
    )


class PositionEpisode(Base):
    """A single instrument's economic lifecycle inside a strategy."""

    __tablename__ = "journal_v2_position_episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    strategy_episode_id = Column(
        Integer,
        ForeignKey("journal_v2_strategy_episodes.id"),
        nullable=False,
    )
    episode_key = Column(String(128), nullable=False)
    lineage_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)

    raw_symbol = Column(String(64), nullable=False)
    asset_type = Column(String(24), nullable=False)
    underlying = Column(String(32), nullable=False)
    expiry = Column(Date)
    strike = Column(Numeric(28, 10))
    option_right = Column(String(8))
    contract_multiplier = Column(Numeric(20, 8))

    direction = Column(String(24), nullable=False)
    lifecycle_status = Column(String(32), nullable=False)
    currency = Column(String(8), nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True))
    hold_seconds = Column(BigInteger)
    opened_quantity = Column(Numeric(28, 10), nullable=False)
    max_absolute_quantity = Column(Numeric(28, 10), nullable=False)
    closed_quantity = Column(Numeric(28, 10), nullable=False)
    remaining_quantity = Column(Numeric(28, 10), nullable=False)
    average_entry_price = Column(Numeric(28, 10))
    average_exit_price = Column(Numeric(28, 10))
    opening_cash_flow = Column(Numeric(28, 10))
    closing_cash_flow = Column(Numeric(28, 10))
    realized_pnl_gross = Column(Numeric(28, 10))
    total_fee = Column(Numeric(28, 10))
    realized_pnl_net = Column(Numeric(28, 10))
    dte_at_entry = Column(Integer)
    close_reason = Column(String(64))
    construction_basis = Column(String(32), nullable=False)
    is_left_censored = Column(Boolean, nullable=False, default=False)
    is_right_censored = Column(Boolean, nullable=False, default=False)
    has_exact_fill_times = Column(Boolean, nullable=False, default=False)
    has_exact_fill_prices = Column(Boolean, nullable=False, default=False)
    has_complete_fees = Column(Boolean, nullable=False, default=False)

    matching_evidence_json = Column(Text, nullable=False)
    evidence_summary_json = Column(Text, nullable=False)
    completeness_score = Column(Numeric(5, 4), nullable=False)
    completeness_status = Column(String(24), nullable=False)
    completeness_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "episode_build_id",
            "episode_key",
            name="uq_jv2_position_build_episode",
        ),
        CheckConstraint(
            "closed_at IS NULL OR closed_at >= opened_at",
            name="ck_jv2_position_window",
        ),
        CheckConstraint(
            "opened_quantity >= 0 AND max_absolute_quantity >= 0 "
            "AND closed_quantity >= 0 "
            "AND remaining_quantity >= 0",
            name="ck_jv2_position_quantities",
        ),
        CheckConstraint(
            "completeness_score >= 0 AND completeness_score <= 1",
            name="ck_jv2_position_completeness",
        ),
        Index(
            "ix_jv2_position_strategy",
            "strategy_episode_id",
            "opened_at",
        ),
        Index(
            "ix_jv2_position_instrument",
            "account_key",
            "raw_symbol",
            "opened_at",
        ),
        Index("ix_jv2_position_lineage", "lineage_key"),
    )


class PositionEpisodeEvidence(Base):
    """Versioned allocation of one raw observation to a position episode."""

    __tablename__ = "journal_v2_position_episode_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    position_episode_id = Column(
        Integer,
        ForeignKey("journal_v2_position_episodes.id"),
        nullable=False,
    )
    broker_order_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_order_observations.id"),
    )
    broker_fill_observation_id = Column(
        Integer,
        ForeignKey("journal_v2_broker_fill_observations.id"),
    )
    evidence_key = Column(String(128), nullable=False)
    evidence_kind = Column(String(32), nullable=False)  # order / fill
    event_role = Column(String(32), nullable=False)  # open / add / reduce / close
    allocation_sequence = Column(Integer, nullable=False)
    evidence_time = Column(DateTime(timezone=True), nullable=False)
    allocated_quantity = Column(Numeric(28, 10))
    allocated_fee = Column(Numeric(28, 10))
    allocated_cash_flow = Column(Numeric(28, 10))
    allocation_ratio = Column(Numeric(12, 10))
    allocation_evidence_json = Column(Text, nullable=False)
    provenance_json = Column(Text, nullable=False)
    recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "episode_build_id",
            "position_episode_id",
            "evidence_key",
            name="uq_jv2_position_evidence_key",
        ),
        CheckConstraint(
            "(broker_order_observation_id IS NOT NULL "
            "AND broker_fill_observation_id IS NULL) "
            "OR (broker_order_observation_id IS NULL "
            "AND broker_fill_observation_id IS NOT NULL)",
            name="ck_jv2_evidence_one_source",
        ),
        CheckConstraint(
            "(evidence_kind = 'order' "
            "AND broker_order_observation_id IS NOT NULL) "
            "OR (evidence_kind = 'fill' "
            "AND broker_fill_observation_id IS NOT NULL)",
            name="ck_jv2_evidence_kind_source",
        ),
        CheckConstraint(
            "allocation_sequence >= 0",
            name="ck_jv2_evidence_sequence",
        ),
        CheckConstraint(
            "allocated_quantity IS NULL OR allocated_quantity >= 0",
            name="ck_jv2_evidence_quantity",
        ),
        CheckConstraint(
            "allocation_ratio IS NULL "
            "OR (allocation_ratio >= 0 AND allocation_ratio <= 1)",
            name="ck_jv2_evidence_ratio",
        ),
        Index(
            "ix_jv2_evidence_position",
            "position_episode_id",
            "allocation_sequence",
        ),
        Index(
            "ix_jv2_evidence_order_observation",
            "broker_order_observation_id",
        ),
        Index(
            "ix_jv2_evidence_fill_observation",
            "broker_fill_observation_id",
        ),
    )


class ReviewAnnotation(Base):
    """One immutable user-authored revision for a PositionEpisode review.

    Review annotations are intentionally stored beside, but remain
    semantically separate from, broker evidence and deterministic Episode
    facts.  An edit appends the next revision and links it to the previous
    annotation; no AI response is written through this model.
    """

    __tablename__ = "journal_v2_review_annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_key = Column(String(64), nullable=False)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    position_episode_id = Column(
        Integer,
        ForeignKey("journal_v2_position_episodes.id"),
        nullable=False,
    )
    revision = Column(Integer, nullable=False)
    review_status = Column(String(24), nullable=False)

    setup_thesis = Column(Text, nullable=False)
    entry_trigger = Column(Text, nullable=False)
    invalidation_plan = Column(Text, nullable=False)
    position_rationale = Column(Text, nullable=False)
    exit_reason = Column(Text, nullable=False)
    post_trade_reflection = Column(Text, nullable=False)
    tags_json = Column(Text, nullable=False)
    error_types_json = Column(Text, nullable=False)

    content_sha256 = Column(String(64), nullable=False)
    previous_annotation_id = Column(
        Integer,
        ForeignKey("journal_v2_review_annotations.id"),
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "account_key",
            "episode_build_id",
            "position_episode_id",
            "revision",
            name="uq_jv2_review_annotation_revision",
        ),
        UniqueConstraint(
            "previous_annotation_id",
            name="uq_jv2_review_annotation_previous",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_jv2_review_annotation_revision",
        ),
        CheckConstraint(
            "review_status IN ('in_progress', 'completed')",
            name="ck_jv2_review_annotation_status",
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_jv2_review_annotation_hash",
        ),
        Index(
            "ix_jv2_review_annotation_scope",
            "account_key",
            "episode_build_id",
            "position_episode_id",
            "revision",
        ),
        Index(
            "ix_jv2_review_annotation_queue",
            "account_key",
            "episode_build_id",
            "review_status",
        ),
    )
