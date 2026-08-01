# -*- coding: utf-8 -*-
"""Persistence models for server-owned premarket evidence prefetch.

The run row is mutable coordination state: a worker may claim, heartbeat, and
settle one deterministic slot.  The artifact and its database-timestamped
ingestion receipt are immutable research evidence.  Their SQLite update/delete
guards are installed by
``init_premarket_evidence_schema`` in :mod:`src.regime.premarket_repository`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from src.storage import Base

__all__ = [
    "RegimePremarketArtifactBundle",
    "RegimePremarketArtifactIngestion",
    "RegimePremarketPrefetchRun",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RegimePremarketPrefetchRun(Base):
    """Mutable lease and terminal state for one deterministic prefetch slot."""

    __tablename__ = "regime_premarket_prefetch_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slot_key = Column(String(128), nullable=False)
    market_date_et = Column(Date, nullable=False)
    previous_session = Column(Date, nullable=False)
    target_as_of = Column(DateTime(timezone=True), nullable=False)
    hard_deadline_at = Column(DateTime(timezone=True), nullable=False)
    policy_version = Column(String(64), nullable=False)
    universe_key = Column(String(128), nullable=False)
    universe_sha256 = Column(String(64), nullable=False)
    symbols_json = Column(Text, nullable=False)

    state = Column(String(24), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    owner_key = Column(String(128))
    claimed_at = Column(DateTime(timezone=True))
    lease_expires_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    bundle_key = Column(String(128))
    last_error_code = Column(String(128))
    last_error_detail = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "slot_key",
            name="uq_regime_premarket_prefetch_slot_key",
        ),
        CheckConstraint(
            "state IN ("
            "'pending', 'running', 'succeeded', "
            "'timed_out', 'failed', 'missed')",
            name="ck_regime_premarket_prefetch_state",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_regime_premarket_prefetch_attempt_count",
        ),
        CheckConstraint(
            "previous_session < market_date_et",
            name="ck_regime_premarket_prefetch_previous_session",
        ),
        CheckConstraint(
            "hard_deadline_at >= target_as_of",
            name="ck_regime_premarket_prefetch_deadline",
        ),
        CheckConstraint(
            "("
            "state = 'running' "
            "AND owner_key IS NOT NULL "
            "AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND completed_at IS NULL "
            "AND bundle_key IS NULL"
            ") OR ("
            "state != 'running' "
            "AND lease_expires_at IS NULL"
            ")",
            name="ck_regime_premarket_prefetch_lease_shape",
        ),
        CheckConstraint(
            "("
            "state = 'succeeded' "
            "AND completed_at IS NOT NULL "
            "AND bundle_key IS NOT NULL"
            ") OR ("
            "state != 'succeeded' "
            "AND bundle_key IS NULL"
            ")",
            name="ck_regime_premarket_prefetch_bundle_shape",
        ),
        Index(
            "ix_regime_premarket_prefetch_date_policy",
            "market_date_et",
            "policy_version",
            "universe_key",
        ),
        Index(
            "ix_regime_premarket_prefetch_state_lease",
            "state",
            "lease_expires_at",
        ),
    )


class RegimePremarketArtifactBundle(Base):
    """One append-only, coherent premarket evidence bundle."""

    __tablename__ = "regime_premarket_artifact_bundles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bundle_key = Column(String(128), nullable=False)
    slot_key = Column(
        String(128),
        ForeignKey("regime_premarket_prefetch_runs.slot_key"),
        nullable=False,
    )
    attempt_count = Column(Integer, nullable=False)

    schema_version = Column(String(64), nullable=False)
    policy_version = Column(String(64), nullable=False)
    adapter_key = Column(String(128), nullable=False)
    market_date_et = Column(Date, nullable=False)
    previous_session = Column(Date, nullable=False)
    universe_key = Column(String(128), nullable=False)
    universe_sha256 = Column(String(64), nullable=False)
    symbols_json = Column(Text, nullable=False)

    target_as_of = Column(DateTime(timezone=True), nullable=False)
    fetch_started_at = Column(DateTime(timezone=True), nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    quality_state = Column(String(24), nullable=False)
    coverage_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "bundle_key",
            name="uq_regime_premarket_artifact_bundle_key",
        ),
        UniqueConstraint(
            "slot_key",
            "attempt_count",
            name="uq_regime_premarket_artifact_slot_attempt",
        ),
        CheckConstraint(
            "attempt_count > 0",
            name="ck_regime_premarket_artifact_attempt_count",
        ),
        CheckConstraint(
            "previous_session < market_date_et",
            name="ck_regime_premarket_artifact_previous_session",
        ),
        CheckConstraint(
            "quality_state IN ('ready', 'partial', 'unavailable')",
            name="ck_regime_premarket_artifact_quality",
        ),
        CheckConstraint(
            "fetch_started_at <= fetched_at",
            name="ck_regime_premarket_artifact_fetch_order",
        ),
        CheckConstraint(
            "target_as_of <= expires_at",
            name="ck_regime_premarket_artifact_expiry",
        ),
        Index(
            "ix_regime_premarket_artifact_formal_selection",
            "market_date_et",
            "previous_session",
            "policy_version",
            "universe_key",
            "target_as_of",
        ),
        Index(
            "ix_regime_premarket_artifact_slot",
            "slot_key",
            "attempt_count",
        ),
    )


class RegimePremarketArtifactIngestion(Base):
    """Database-timestamped receipt for one immutable artifact bundle.

    Keeping the trusted receipt in a companion table makes the migration
    additive for databases that already created the artifact table.  The
    repository never accepts ``ingested_at`` from callers.
    """

    __tablename__ = "regime_premarket_artifact_ingestions"

    bundle_key = Column(
        String(128),
        ForeignKey("regime_premarket_artifact_bundles.bundle_key"),
        primary_key=True,
        nullable=False,
    )
    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        Index(
            "ix_regime_premarket_artifact_ingestion_time",
            "ingested_at",
        ),
    )
