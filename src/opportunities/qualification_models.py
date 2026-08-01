# -*- coding: utf-8 -*-
"""Append-only persistence models for versioned opportunity qualification."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
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


class OpportunitySnapshotQualificationAssessment(Base):
    """One policy-versioned assessment of immutable snapshot-level axes."""

    __tablename__ = "opportunity_snapshot_qualification_assessments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_key = Column(String(128), nullable=False)
    snapshot_run_id = Column(
        Integer,
        ForeignKey("opportunity_snapshot_runs.id"),
        nullable=False,
    )
    policy_version = Column(String(64), nullable=False)
    publication_state = Column(String(32), nullable=False)
    analysis_quality_state = Column(String(24), nullable=False)

    publication_cycle_key = Column(String(128))
    publication_attempt_key = Column(String(128))
    publication_event_sequence = Column(Integer)
    publication_observed_at = Column(DateTime(timezone=True))

    reason_codes_json = Column(Text, nullable=False)
    facts_sha256 = Column(String(64), nullable=False)
    assessment_sha256 = Column(String(64), nullable=False)
    assessment_json = Column(Text, nullable=False)
    assessed_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "assessment_key",
            name="uq_opportunity_snapshot_qualification_key",
        ),
        UniqueConstraint(
            "snapshot_run_id",
            "policy_version",
            name="uq_opportunity_snapshot_qualification_slot",
        ),
        CheckConstraint(
            "publication_state IN ("
            "'canonical_published', 'audit_frozen', 'legacy_unverified')",
            name="ck_opportunity_snapshot_qualification_publication",
        ),
        CheckConstraint(
            "analysis_quality_state IN ("
            "'ready', 'degraded', 'blocked', 'unassessed')",
            name="ck_opportunity_snapshot_qualification_analysis",
        ),
        CheckConstraint(
            "("
            "publication_state = 'canonical_published' "
            "AND publication_cycle_key IS NOT NULL "
            "AND publication_attempt_key IS NOT NULL "
            "AND publication_event_sequence IS NOT NULL "
            "AND publication_event_sequence > 0 "
            "AND publication_observed_at IS NOT NULL"
            ") OR ("
            "publication_state != 'canonical_published' "
            "AND publication_cycle_key IS NULL "
            "AND publication_attempt_key IS NULL "
            "AND publication_event_sequence IS NULL "
            "AND publication_observed_at IS NULL"
            ")",
            name="ck_opportunity_snapshot_qualification_proof",
        ),
        Index(
            "ix_opportunity_snapshot_qualification_policy",
            "policy_version",
            "publication_state",
            "analysis_quality_state",
        ),
    )


class OpportunityCandidateTrackAssessment(Base):
    """One candidate's qualification under one immutable analysis track."""

    __tablename__ = "opportunity_candidate_track_assessments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_key = Column(String(128), nullable=False)
    snapshot_qualification_id = Column(
        Integer,
        ForeignKey("opportunity_snapshot_qualification_assessments.id"),
        nullable=False,
    )
    snapshot_candidate_id = Column(
        Integer,
        ForeignKey("opportunity_snapshot_candidates.id"),
        nullable=False,
    )
    policy_version = Column(String(64), nullable=False)
    track_key = Column(String(64), nullable=False)
    causal_window_state = Column(String(24), nullable=False)
    observation_state = Column(String(16), nullable=False)
    qualification_state = Column(String(16), nullable=False)

    reason_codes_json = Column(Text, nullable=False)
    facts_sha256 = Column(String(64), nullable=False)
    assessment_sha256 = Column(String(64), nullable=False)
    assessment_json = Column(Text, nullable=False)
    assessed_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "assessment_key",
            name="uq_opportunity_candidate_track_assessment_key",
        ),
        UniqueConstraint(
            "snapshot_candidate_id",
            "policy_version",
            "track_key",
            name="uq_opportunity_candidate_track_assessment_slot",
        ),
        CheckConstraint(
            "causal_window_state IN ("
            "'prospective', 'retrospective', 'premature', 'unverified')",
            name="ck_opportunity_candidate_track_causal",
        ),
        CheckConstraint(
            "observation_state IN ('ready', 'partial', 'unavailable')",
            name="ck_opportunity_candidate_track_observation",
        ),
        CheckConstraint(
            "qualification_state IN ('qualified', 'excluded', 'unverified')",
            name="ck_opportunity_candidate_track_qualification",
        ),
        CheckConstraint(
            "length(track_key) > 0",
            name="ck_opportunity_candidate_track_key",
        ),
        Index(
            "ix_opportunity_candidate_track_policy_state",
            "policy_version",
            "track_key",
            "qualification_state",
        ),
        Index(
            "ix_opportunity_candidate_track_snapshot",
            "snapshot_qualification_id",
            "track_key",
        ),
    )


__all__ = [
    "OpportunityCandidateTrackAssessment",
    "OpportunitySnapshotQualificationAssessment",
]
