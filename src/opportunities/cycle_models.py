# -*- coding: utf-8 -*-
"""Append-only persistence models for canonical premarket orchestration."""
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
)

from src.storage import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OpportunityResearchUniverseVersion(Base):
    """One immutable server-side research-universe revision."""

    __tablename__ = "opportunity_research_universe_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    universe_version_key = Column(String(128), nullable=False)
    scope_key = Column(String(64), nullable=False)
    symbols_sha256 = Column(String(64), nullable=False)
    symbols_json = Column(Text, nullable=False)
    requested_limit = Column(Integer, nullable=False)
    source = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "universe_version_key",
            name="uq_opportunity_research_universe_version_key",
        ),
        CheckConstraint(
            "requested_limit > 0",
            name="ck_opportunity_research_universe_limit",
        ),
        Index(
            "ix_opportunity_research_universe_scope_created",
            "scope_key",
            "created_at",
            "id",
        ),
    )


class OpportunityPremarketCycleSlot(Base):
    """One immutable lock target for a canonical market-date cycle."""

    __tablename__ = "opportunity_premarket_cycle_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_key = Column(String(128), nullable=False)
    market_date_et = Column(Date, nullable=False)
    scope_key = Column(String(64), nullable=False)
    freeze_policy_version = Column(String(64), nullable=False)
    cycle_version = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "cycle_key",
            name="uq_opportunity_premarket_cycle_slot_key",
        ),
        Index(
            "ix_opportunity_premarket_cycle_slot_date",
            "scope_key",
            "market_date_et",
        ),
    )


class OpportunityPremarketCycleAttempt(Base):
    """Immutable root for one claimed execution attempt."""

    __tablename__ = "opportunity_premarket_cycle_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attempt_key = Column(String(128), nullable=False)
    cycle_key = Column(String(128), nullable=False)
    market_date_et = Column(Date, nullable=False)
    scope_key = Column(String(64), nullable=False)
    freeze_policy_version = Column(String(64), nullable=False)
    cycle_version = Column(String(64), nullable=False)
    universe_version_key = Column(String(128))
    universe_source = Column(String(32), nullable=False)
    universe_sha256 = Column(String(64), nullable=False)
    universe_json = Column(Text, nullable=False)
    requested_limit = Column(Integer, nullable=False)
    trigger = Column(String(24), nullable=False)
    owner_key = Column(String(128), nullable=False)
    recovered_from_attempt_key = Column(String(128))
    hard_deadline_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    input_sha256 = Column(String(64), nullable=False)
    input_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "attempt_key",
            name="uq_opportunity_premarket_attempt_key",
        ),
        CheckConstraint(
            "requested_limit > 0",
            name="ck_opportunity_premarket_attempt_limit",
        ),
        Index(
            "ix_opportunity_premarket_attempt_cycle",
            "cycle_key",
            "started_at",
            "id",
        ),
        Index(
            "ix_opportunity_premarket_attempt_date",
            "scope_key",
            "market_date_et",
            "started_at",
        ),
    )


class OpportunityPremarketCycleEvent(Base):
    """One immutable stage, lease, or terminal event for an attempt."""

    __tablename__ = "opportunity_premarket_cycle_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(128), nullable=False)
    attempt_id = Column(
        Integer,
        ForeignKey("opportunity_premarket_cycle_attempts.id"),
        nullable=False,
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(32), nullable=False)
    stage = Column(String(64))
    state = Column(String(24), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    lease_expires_at = Column(DateTime(timezone=True))
    actor_key = Column(String(128), nullable=False)
    error_code = Column(String(128))
    payload_sha256 = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "event_key",
            name="uq_opportunity_premarket_event_key",
        ),
        UniqueConstraint(
            "attempt_id",
            "sequence",
            name="uq_opportunity_premarket_event_sequence",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_opportunity_premarket_event_sequence",
        ),
        Index(
            "ix_opportunity_premarket_event_attempt",
            "attempt_id",
            "sequence",
        ),
    )
