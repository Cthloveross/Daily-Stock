# -*- coding: utf-8 -*-
"""Immutable server-owned artifacts for the read-only Journal refresh flow."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
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

__all__ = ["JournalRefreshArtifact", "JournalRefreshPublication"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JournalRefreshArtifact(Base):
    """One bounded OpenD acquisition plus the exact zero-evidence-write plan.

    The payload is kept server-side so confirmation cannot silently substitute
    a different browser upload.  It contains only the probe's allowlisted trade
    history fields and a one-way account binding; the raw account ID is absent.
    """

    __tablename__ = "journal_v2_refresh_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_key = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    account_binding = Column(String(64), nullable=False)
    environment = Column(String(16), nullable=False)
    market = Column(String(16), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    preview_key = Column(String(64), nullable=False)
    confirm_allowed = Column(Boolean, nullable=False)
    payload_json = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint("artifact_key", name="uq_jv2_refresh_artifact_key"),
        CheckConstraint(
            "window_end > window_start",
            name="ck_jv2_refresh_artifact_window",
        ),
        CheckConstraint(
            "expires_at > recorded_at",
            name="ck_jv2_refresh_artifact_expiry",
        ),
        CheckConstraint(
            "length(account_binding) = 64 "
            "AND length(source_sha256) = 64 "
            "AND length(evidence_sha256) = 64 "
            "AND length(preview_key) = 64",
            name="ck_jv2_refresh_artifact_hashes",
        ),
        Index(
            "ix_jv2_refresh_artifact_account_recorded",
            "broker",
            "account_key",
            "recorded_at",
        ),
    )


class JournalRefreshPublication(Base):
    """Commit receipt proving which completed query became Journal evidence."""

    __tablename__ = "journal_v2_refresh_publications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    publication_key = Column(String(128), nullable=False)
    refresh_artifact_id = Column(
        Integer,
        ForeignKey("journal_v2_refresh_artifacts.id"),
        nullable=False,
    )
    import_batch_id = Column(
        Integer,
        ForeignKey("journal_v2_import_batches.id"),
        nullable=False,
    )
    canonical_set_id = Column(
        Integer,
        ForeignKey("journal_v2_canonical_evidence_sets.id"),
        nullable=False,
    )
    broker = Column(String(32), nullable=False)
    account_key = Column(String(64), nullable=False)
    account_binding = Column(String(64), nullable=False)
    broker_queried_through = Column(DateTime(timezone=True), nullable=False)
    latest_fill_at = Column(DateTime(timezone=True))
    evidence_published_through = Column(DateTime(timezone=True), nullable=False)
    import_duplicate = Column(Boolean, nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "publication_key",
            name="uq_jv2_refresh_publication_key",
        ),
        UniqueConstraint(
            "refresh_artifact_id",
            name="uq_jv2_refresh_publication_artifact",
        ),
        CheckConstraint(
            "evidence_published_through >= broker_queried_through",
            name="ck_jv2_refresh_publication_coverage",
        ),
        CheckConstraint(
            "length(account_binding) = 64",
            name="ck_jv2_refresh_publication_binding",
        ),
        Index(
            "ix_jv2_refresh_publication_account_recorded",
            "broker",
            "account_key",
            "recorded_at",
        ),
    )
