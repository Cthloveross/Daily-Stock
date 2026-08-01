# -*- coding: utf-8 -*-
"""Append-only Playbook tables (contract slice C-2, L2/L3).

``PlaybookCandidate`` (L2) is a user-authored observation the user explicitly
saved from the zero-write L1 insights view (or wrote free-form).  Its evidence
snapshot is frozen at creation time and never rewritten.

``PlaybookRule`` (L3) is an append-only version chain per ``lineage_key``:
version 1 is created only by an explicit user promotion, and retirement
appends a new version row with ``status='retired'`` — rows are never updated
or deleted.  Rules never feed back into any scoring, ranking, or AI prompt;
they are only the user's own decision checklist.
"""
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

__all__ = [
    "PlaybookCandidate",
    "PlaybookRule",
]


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for newly recorded immutable rows."""
    return datetime.now(timezone.utc)


class PlaybookCandidate(Base):
    """One immutable, user-created playbook candidate (L2).

    ``candidate_key`` hashes the user content plus the source-bucket echo, so
    replaying the same explicit request is idempotent.  The evidence snapshot
    freezes the referenced insights bucket (build identity, member episode
    ids, latest annotation revisions, counts incl. the conditional split,
    thresholds, as-of) at creation time; later data changes never rewrite it.
    """

    __tablename__ = "journal_v2_playbook_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_key = Column(String(64), nullable=False)
    account_key = Column(String(64), nullable=False)
    title = Column(String(120), nullable=False)
    rule_text = Column(Text, nullable=False)
    # NULL for free-form candidates that reference no insights bucket.
    source_bucket_json = Column(Text)
    evidence_snapshot_json = Column(Text, nullable=False)
    evidence_snapshot_sha256 = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("candidate_key", name="uq_jv2_playbook_candidate_key"),
        CheckConstraint(
            "length(candidate_key) = 64",
            name="ck_jv2_playbook_candidate_key",
        ),
        CheckConstraint(
            "length(title) > 0 AND length(rule_text) > 0",
            name="ck_jv2_playbook_candidate_content",
        ),
        CheckConstraint(
            "length(evidence_snapshot_sha256) = 64",
            name="ck_jv2_playbook_candidate_snapshot_hash",
        ),
        Index(
            "ix_jv2_playbook_candidate_account_created",
            "account_key",
            "created_at",
        ),
    )


class PlaybookRule(Base):
    """One immutable version of a promoted playbook rule (L3).

    ``lineage_key`` is stable across versions of one rule.  The chain mirrors
    ``EpisodeBuildActivation``: version 1 has no previous row, every later
    version references exactly one previous row, and a previous row can be
    extended only once.  ``evidence_snapshot_json`` is re-frozen at promotion
    time; a retirement row copies the promoted snapshot verbatim.
    """

    __tablename__ = "journal_v2_playbook_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_key = Column(String(64), nullable=False)
    lineage_key = Column(String(64), nullable=False)
    account_key = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)  # active / retired
    promoted_from_candidate_id = Column(
        Integer,
        ForeignKey("journal_v2_playbook_candidates.id"),
        nullable=False,
    )
    previous_rule_id = Column(
        Integer,
        ForeignKey("journal_v2_playbook_rules.id"),
    )
    title = Column(String(120), nullable=False)
    rule_text = Column(Text, nullable=False)
    evidence_snapshot_json = Column(Text, nullable=False)
    evidence_snapshot_sha256 = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("rule_key", name="uq_jv2_playbook_rule_key"),
        UniqueConstraint(
            "lineage_key",
            "version",
            name="uq_jv2_playbook_rule_lineage_version",
        ),
        UniqueConstraint(
            "previous_rule_id",
            name="uq_jv2_playbook_rule_previous",
        ),
        CheckConstraint(
            "length(rule_key) = 64 AND length(lineage_key) = 64",
            name="ck_jv2_playbook_rule_keys",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_jv2_playbook_rule_version",
        ),
        CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_jv2_playbook_rule_status",
        ),
        CheckConstraint(
            "(version = 1 AND previous_rule_id IS NULL "
            "AND status = 'active') "
            "OR (version > 1 AND previous_rule_id IS NOT NULL)",
            name="ck_jv2_playbook_rule_chain",
        ),
        CheckConstraint(
            "length(title) > 0 AND length(rule_text) > 0",
            name="ck_jv2_playbook_rule_content",
        ),
        CheckConstraint(
            "length(evidence_snapshot_sha256) = 64",
            name="ck_jv2_playbook_rule_snapshot_hash",
        ),
        Index(
            "ix_jv2_playbook_rule_account_lineage",
            "account_key",
            "lineage_key",
            "version",
        ),
        Index(
            "ix_jv2_playbook_rule_candidate",
            "promoted_from_candidate_id",
        ),
    )
