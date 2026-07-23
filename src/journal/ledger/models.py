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

__all__ = [
    "ImportBatch",
    "ReconciliationAttestation",
    "BrokerOrderObservation",
    "BrokerFillObservation",
    "BrokerFeeObservation",
    "OrderIdentityLink",
    "DealIdentityLink",
    "OrderFillSetAttestation",
    "CanonicalEvidenceSetRecord",
    "CanonicalEvidenceMemberRecord",
    "CanonicalEvidenceIssueRecord",
    "CanonicalEvidenceProvenanceRecord",
    "EpisodeBuild",
    "StrategyEpisode",
    "PositionEpisode",
    "PositionEpisodeEvidence",
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
