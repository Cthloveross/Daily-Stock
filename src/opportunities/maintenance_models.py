# -*- coding: utf-8 -*-
"""Operational claim state for automatic opportunity-outcome maintenance.

The rows in this module are scheduler coordination state, not research
evidence.  Frozen snapshots and candidate outcomes remain append-only in
``src.opportunities.models``.  This table is intentionally mutable so a
crashed worker can release or recover a bounded lease without inventing a new
market observation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.storage import Base

__all__ = ["OpportunityOutcomeMaintenanceRun"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OpportunityOutcomeMaintenanceRun(Base):
    """One DB-coordinated maintenance slot for a completed XNYS session."""

    __tablename__ = "opportunity_outcome_maintenance_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    maintenance_key = Column(String(128), nullable=False)
    session_date_et = Column(Date, nullable=False)
    policy_version = Column(String(64), nullable=False)
    state = Column(String(24), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    owner_key = Column(String(128))
    claimed_at = Column(DateTime(timezone=True))
    lease_expires_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    next_retry_at = Column(DateTime(timezone=True))
    last_error_code = Column(String(128))
    result_sha256 = Column(String(64))
    result_json = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "maintenance_key",
            name="uq_opportunity_outcome_maintenance_key",
        ),
        UniqueConstraint(
            "session_date_et",
            "policy_version",
            name="uq_opportunity_outcome_maintenance_session_policy",
        ),
        CheckConstraint(
            "state IN ('pending', 'running', 'completed', 'degraded', 'failed')",
            name="ck_opportunity_outcome_maintenance_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 2",
            name="ck_opportunity_outcome_maintenance_attempts",
        ),
        Index(
            "ix_opportunity_outcome_maintenance_session",
            "session_date_et",
            "policy_version",
        ),
    )
