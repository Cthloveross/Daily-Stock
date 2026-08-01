# -*- coding: utf-8 -*-
"""Append-only receipts for the short-lived artifact GC (contract F-2a).

Deleting rows from evidence-adjacent storage must leave an immutable receipt
in the same culture as ingestion / publication receipts.  The table is listed
in ``repository._APPEND_ONLY_TABLE_NAMES`` so ``init_ledger_schema`` installs
its SQLite UPDATE/DELETE deny triggers; see
``New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md`` §3.1 for column semantics.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.storage import Base

__all__ = ["ARTIFACT_GC_RECEIPT_TABLE_NAME", "ArtifactGcReceipt"]


ARTIFACT_GC_RECEIPT_TABLE_NAME = "journal_v2_artifact_gc_receipts"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactGcReceipt(Base):
    """One immutable record of a bounded artifact GC run for one table.

    ``deleted_ids_json`` stores the ordered candidate id list (equal to the
    deleted ids for ``apply`` runs; for ``dry_run`` receipts it records the
    candidates that WOULD be deleted while ``deleted_count`` stays 0).
    """

    __tablename__ = ARTIFACT_GC_RECEIPT_TABLE_NAME

    id = Column(Integer, primary_key=True, autoincrement=True)
    receipt_key = Column(String(64), nullable=False)
    run_kind = Column(String(16), nullable=False)
    table_name = Column(String(64), nullable=False)
    predicate_version = Column(String(64), nullable=False)
    grace_days = Column(Integer, nullable=False)
    batch_limit = Column(Integer, nullable=False)
    candidate_count = Column(Integer, nullable=False)
    deleted_count = Column(Integer, nullable=False)
    payload_bytes_reclaimed = Column(Integer, nullable=False)
    deleted_ids_json = Column(Text, nullable=False)
    deleted_ids_sha256 = Column(String(64), nullable=False)
    backup_path = Column(Text)
    backup_sha256 = Column(String(64))
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint("receipt_key", name="uq_jv2_artifact_gc_receipt_key"),
        CheckConstraint(
            "run_kind IN ('dry_run', 'apply')",
            name="ck_jv2_artifact_gc_receipt_kind",
        ),
        CheckConstraint(
            "run_kind <> 'dry_run' OR deleted_count = 0",
            name="ck_jv2_artifact_gc_receipt_dry_run_zero",
        ),
        CheckConstraint(
            "table_name IN ("
            "'journal_v2_refresh_artifacts', "
            "'journal_v2_position_snapshot_artifacts')",
            name="ck_jv2_artifact_gc_receipt_table",
        ),
        CheckConstraint(
            "grace_days >= 1 AND batch_limit >= 1",
            name="ck_jv2_artifact_gc_receipt_params",
        ),
        CheckConstraint(
            "candidate_count >= 0 AND deleted_count >= 0 "
            "AND deleted_count <= candidate_count "
            "AND payload_bytes_reclaimed >= 0",
            name="ck_jv2_artifact_gc_receipt_counts",
        ),
        CheckConstraint(
            "length(receipt_key) = 64 AND length(deleted_ids_sha256) = 64",
            name="ck_jv2_artifact_gc_receipt_hashes",
        ),
        CheckConstraint(
            "run_kind <> 'apply' OR (backup_path IS NOT NULL "
            "AND length(backup_sha256) = 64)",
            name="ck_jv2_artifact_gc_receipt_backup",
        ),
        CheckConstraint(
            "completed_at >= started_at",
            name="ck_jv2_artifact_gc_receipt_window",
        ),
    )
