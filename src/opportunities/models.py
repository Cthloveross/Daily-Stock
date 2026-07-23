# -*- coding: utf-8 -*-
"""Immutable persistence models for frozen opportunity research runs.

These tables deliberately use their own ``opportunity_*`` namespace.  They
contain research snapshots and forward market outcomes, not broker orders,
fills, positions, or any other Journal fact.  Corrections are represented by
new version/state rows; repository-installed SQLite triggers reject UPDATE and
DELETE operations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
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
    "OpportunitySnapshotRun",
    "OpportunitySnapshotCandidate",
    "OpportunityCandidateOutcome",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OpportunitySnapshotRun(Base):
    """One immutable, ex-ante daily opportunity research snapshot."""

    __tablename__ = "opportunity_snapshot_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(128), nullable=False)
    market_date_et = Column(Date, nullable=False)
    run_type = Column(String(32), nullable=False)
    signal_version = Column(String(64), nullable=False)
    schema_version = Column(String(32), nullable=False)
    freeze_policy_version = Column(String(64), nullable=False)
    playbook_version = Column(String(64), nullable=False)
    scope_key = Column(String(64), nullable=False)

    universe_sha256 = Column(String(64), nullable=False)
    universe_json = Column(Text, nullable=False)
    requested_limit = Column(Integer, nullable=False)

    # The engine's run_id is retained for provenance only.  It contains as_of
    # and is therefore intentionally not the durable idempotency key.
    source_run_id = Column(String(128), nullable=False)
    ranking_method = Column(String(64), nullable=False)
    strategy_validation_state = Column(String(32), nullable=False)
    as_of = Column(DateTime(timezone=True), nullable=False)
    frozen_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    validation_eligible = Column(Boolean, nullable=False, default=False)
    eligibility_reasons_json = Column(Text, nullable=False)

    payload_sha256 = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_key", name="uq_opportunity_snapshot_key"),
        CheckConstraint(
            "requested_limit > 0", name="ck_opportunity_snapshot_limit"
        ),
        Index(
            "ix_opportunity_snapshot_scope_date",
            "scope_key",
            "market_date_et",
            "frozen_at",
        ),
        Index("ix_opportunity_snapshot_payload", "payload_sha256"),
    )


class OpportunitySnapshotCandidate(Base):
    """One ranked candidate contained in a frozen research snapshot."""

    __tablename__ = "opportunity_snapshot_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_run_id = Column(
        Integer,
        ForeignKey("opportunity_snapshot_runs.id"),
        nullable=False,
    )
    candidate_key = Column(String(128), nullable=False)
    source_candidate_id = Column(String(128))
    ticker = Column(String(32), nullable=False)
    rank = Column(Integer, nullable=False)
    research_state = Column(String(32), nullable=False)
    directional_context = Column(String(16), nullable=False)
    supporting_evidence_count = Column(Integer, nullable=False, default=0)
    data_completeness_state = Column(String(16), nullable=False)

    # This is a causal prior-completed-session reference, never an asserted
    # executable entry.  It is nullable for blocked/incomplete candidates.
    reference_session_date = Column(Date)
    reference_close = Column(Numeric(28, 10))
    reference_price_basis = Column(String(64))
    reference_source = Column(String(128))

    validation_eligible = Column(Boolean, nullable=False, default=False)
    eligibility_reasons_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "candidate_key", name="uq_opportunity_snapshot_candidate_key"
        ),
        UniqueConstraint(
            "snapshot_run_id",
            "ticker",
            name="uq_opportunity_snapshot_candidate_ticker",
        ),
        UniqueConstraint(
            "snapshot_run_id",
            "rank",
            name="uq_opportunity_snapshot_candidate_rank",
        ),
        CheckConstraint("rank > 0", name="ck_opportunity_candidate_rank"),
        CheckConstraint(
            "supporting_evidence_count >= 0",
            name="ck_opportunity_candidate_evidence_count",
        ),
        CheckConstraint(
            "reference_close IS NULL OR reference_close > 0",
            name="ck_opportunity_candidate_reference_close",
        ),
        Index(
            "ix_opportunity_candidate_ticker_date",
            "ticker",
            "reference_session_date",
        ),
    )


class OpportunityCandidateOutcome(Base):
    """Immutable 5/20-session underlying outcome for one frozen candidate.

    ``partial`` is an append-only terminal observation for a target session
    whose close exists but whose entry-path or SPY context is incomplete.  A
    later fully sourced ``complete`` row may be appended; repeated partial
    revisions are intentionally not supported.
    """

    __tablename__ = "opportunity_candidate_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_candidate_id = Column(
        Integer,
        ForeignKey("opportunity_snapshot_candidates.id"),
        nullable=False,
    )
    horizon_sessions = Column(Integer, nullable=False)
    evaluator_version = Column(String(64), nullable=False)
    result_state = Column(String(16), nullable=False)  # complete / partial
    context_label = Column(String(24), nullable=False)

    reference_session_date = Column(Date)
    reference_close = Column(Numeric(28, 10))
    refetched_reference_close = Column(Numeric(28, 10))
    entry_session_date = Column(Date)
    entry_open = Column(Numeric(28, 10))
    target_session_date = Column(Date)
    target_close_at = Column(DateTime(timezone=True))
    window_start_date = Column(Date)
    window_end_date = Column(Date, nullable=False)
    end_close = Column(Numeric(28, 10), nullable=False)
    max_high = Column(Numeric(28, 10))
    min_low = Column(Numeric(28, 10))

    # Raw returns remain useful for every directional context.  Signed and
    # favorable/adverse fields are nullable and repository validation forbids
    # them for mixed/unknown contexts.
    close_return_pct = Column(Numeric(20, 8))
    entry_proxy_return_pct = Column(Numeric(20, 8))
    max_high_return_pct = Column(Numeric(20, 8))
    min_low_return_pct = Column(Numeric(20, 8))
    signed_close_return_pct = Column(Numeric(20, 8))
    signed_entry_return_pct = Column(Numeric(20, 8))
    mfe_pct = Column(Numeric(20, 8))
    mae_pct = Column(Numeric(20, 8))

    benchmark_ticker = Column(String(16), nullable=False, default="SPY")
    spy_reference_close = Column(Numeric(28, 10))
    spy_refetched_reference_close = Column(Numeric(28, 10))
    spy_entry_open = Column(Numeric(28, 10))
    spy_end_close = Column(Numeric(28, 10))
    spy_close_return_pct = Column(Numeric(20, 8))
    spy_entry_proxy_return_pct = Column(Numeric(20, 8))
    excess_close_return_pct = Column(Numeric(20, 8))
    excess_entry_proxy_return_pct = Column(Numeric(20, 8))
    signed_excess_spy_pct = Column(Numeric(20, 8))

    input_bars_sha256 = Column(String(64))
    benchmark_bars_sha256 = Column(String(64))
    provenance_json = Column(Text, nullable=False)
    result_sha256 = Column(String(64), nullable=False)
    result_json = Column(Text, nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_candidate_id",
            "horizon_sessions",
            "evaluator_version",
            "result_state",
            name="uq_opportunity_candidate_outcome_slot",
        ),
        CheckConstraint(
            "horizon_sessions IN (5, 20)",
            name="ck_opportunity_outcome_horizon",
        ),
        CheckConstraint(
            "result_state IN ('complete', 'partial')",
            name="ck_opportunity_outcome_state",
        ),
        CheckConstraint(
            "context_label IN ("
            "'CONTEXT_HIT', 'CONTEXT_MISS', 'NEUTRAL', "
            "'NON_DIRECTIONAL', 'DIRECTION_UNKNOWN')",
            name="ck_opportunity_outcome_context_label",
        ),
        CheckConstraint(
            "reference_close IS NULL OR reference_close > 0",
            name="ck_opportunity_outcome_reference_close",
        ),
        CheckConstraint(
            "entry_open IS NULL OR entry_open > 0",
            name="ck_opportunity_outcome_entry_open",
        ),
        CheckConstraint(
            "end_close > 0", name="ck_opportunity_outcome_end_close"
        ),
        CheckConstraint(
            "window_start_date IS NULL OR window_end_date >= window_start_date",
            name="ck_opportunity_outcome_window",
        ),
        CheckConstraint(
            "target_session_date IS NULL OR target_session_date = window_end_date",
            name="ck_opportunity_outcome_target_session",
        ),
        Index(
            "ix_opportunity_outcome_horizon_date",
            "horizon_sessions",
            "window_end_date",
        ),
    )
