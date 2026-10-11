# -*- coding: utf-8 -*-
"""Immutable live-forward telemetry for the strict two-minute P0 lane.

These tables are deliberately separate from the user-facing push feed.  A
frozen enrollment identifies the prospective 20-session run.  Endpoint,
completed-evaluation and notification-attempt events are separate immutable
rows, so a later valid evaluation cannot rewrite an earlier data miss and a
restart/retry cannot hide a duplicate queue attempt.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)

from src.storage import Base

__all__ = [
    "P02mActivationArtifactAttestation",
    "P02mShadowEnrollment",
    "P02mShadowExportAttestation",
    "P02mShadowObservation",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class P02mShadowObservation(Base):
    """One append-only live-forward RTH telemetry event."""

    __tablename__ = "journal_v2_p0_2m_shadow_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # ``enrollment_key`` is the immutable run identity.  The same signal and
    # universe may have more than one prospective run after an operational
    # failure; observations from overlapping calendar windows must never be
    # merged or deduplicated across those runs.
    enrollment_key = Column(String(64), nullable=False)
    observation_key = Column(String(64), nullable=False)
    record_kind = Column(String(32), nullable=False)
    notification_attempt_id = Column(String(64), nullable=False)
    observation_mode = Column(String(32), nullable=False)
    symbol = Column(String(16), nullable=False)
    et_date = Column(String(10), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    # Added prospectively only.  Old observations remain NULL; no price backfill.
    window_close = Column(Numeric(24, 10))
    universe_hash = Column(String(80), nullable=False)
    signal_version = Column(String(48), nullable=False)
    session_scope = Column(String(16), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    max_component_end = Column(DateTime(timezone=True))
    data_available = Column(Boolean, nullable=False)
    evaluation_available = Column(Boolean, nullable=False)
    evaluation_state = Column(String(32), nullable=False)
    qualified = Column(Boolean, nullable=False)
    would_notify = Column(Boolean, nullable=False)
    notification_decision_available = Column(Boolean, nullable=False)
    duplicate_episode_suppressed = Column(Boolean, nullable=False)
    direction = Column(String(8))
    source = Column(String(64))
    unavailable_reason = Column(String(160))
    data_quality_json = Column(Text, nullable=False)
    unfinished_bar_trigger = Column(Boolean, nullable=False, default=False)
    observer_queue_accepted = Column(Boolean)
    observer_queue_enqueued_at = Column(DateTime(timezone=True))
    observer_queue_latency_seconds = Column(Numeric(12, 3))
    telegram_enqueue_evidence_mode = Column(String(48))
    telegram_enqueued_at = Column(DateTime(timezone=True))
    telegram_enqueue_latency_seconds = Column(Numeric(12, 3))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "enrollment_key",
            "symbol",
            "et_date",
            "window_end",
            "universe_hash",
            "signal_version",
            "record_kind",
            "notification_attempt_id",
            name="uq_jv2_p0_2m_shadow_endpoint",
        ),
        UniqueConstraint(
            "observation_key", name="uq_jv2_p0_2m_shadow_observation_key"
        ),
        CheckConstraint(
            "observation_mode = 'live_forward_shadow'",
            name="ck_jv2_p0_2m_shadow_live_only",
        ),
        CheckConstraint(
            "record_kind IN ("
            "'endpoint_observation', 'completed_evaluation', "
            "'notification_attempt'"
            ")",
            name="ck_jv2_p0_2m_shadow_record_kind",
        ),
        CheckConstraint(
            "telegram_enqueue_evidence_mode IS NULL OR "
            "telegram_enqueue_evidence_mode IN ("
            "'counterfactual_no_send_same_queue', "
            "'actual_telegram_queue_receipt'"
            ")",
            name="ck_jv2_p0_2m_shadow_enqueue_mode",
        ),
        CheckConstraint(
            "session_scope = 'regular'",
            name="ck_jv2_p0_2m_shadow_rth_only",
        ),
        CheckConstraint(
            "direction IS NULL OR direction IN ('up', 'down', 'flat')",
            name="ck_jv2_p0_2m_shadow_direction",
        ),
        Index(
            "ix_jv2_p0_2m_shadow_day_symbol",
            "et_date",
            "symbol",
            "window_end",
        ),
        Index(
            "ix_jv2_p0_2m_shadow_identity",
            "enrollment_key",
            "universe_hash",
            "signal_version",
            "et_date",
        ),
    )


class P02mShadowEnrollment(Base):
    """Frozen prospective 20-XNYS-session enrollment for one universe."""

    __tablename__ = "journal_v2_p0_2m_shadow_enrollments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enrollment_key = Column(String(64), nullable=False)
    run_request_id = Column(String(64), nullable=False)
    restart_reason = Column(String(160), nullable=False)
    supersedes_enrollment_key = Column(String(64))
    observation_mode = Column(String(32), nullable=False)
    universe_hash = Column(String(80), nullable=False)
    signal_version = Column(String(48), nullable=False)
    universe_json = Column(Text, nullable=False)
    sessions_json = Column(Text, nullable=False)
    start_session = Column(String(10), nullable=False)
    end_session = Column(String(10), nullable=False)
    enrolled_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "enrollment_key", name="uq_jv2_p0_2m_shadow_enrollment_key"
        ),
        UniqueConstraint(
            "universe_hash",
            "signal_version",
            "run_request_id",
            name="uq_jv2_p0_2m_shadow_enrollment_run_request",
        ),
        CheckConstraint(
            "observation_mode = 'live_forward_shadow'",
            name="ck_jv2_p0_2m_shadow_enrollment_live_only",
        ),
        Index(
            "ix_jv2_p0_2m_shadow_enrollment_identity",
            "universe_hash",
            "signal_version",
            "enrolled_at",
        ),
    )


class P02mShadowExportAttestation(Base):
    """One DB-issued receipt for an immutable live-shadow JSON export."""

    __tablename__ = "journal_v2_p0_2m_shadow_export_attestations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attestation_id = Column(String(64), nullable=False)
    export_sha256 = Column(String(64), nullable=False)
    attestation_schema_version = Column(String(64), nullable=False)
    authority = Column(String(64), nullable=False)
    observation_mode = Column(String(32), nullable=False)
    universe_hash = Column(String(80), nullable=False)
    signal_version = Column(String(48), nullable=False)
    enrollment_key = Column(String(64), nullable=False)
    exported_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "attestation_id", name="uq_jv2_p0_2m_shadow_export_attestation_id"
        ),
        CheckConstraint(
            "observation_mode = 'live_forward_shadow'",
            name="ck_jv2_p0_2m_shadow_export_live_only",
        ),
        Index(
            "ix_jv2_p0_2m_shadow_export_identity",
            "universe_hash",
            "signal_version",
            "exported_at",
        ),
    )


class P02mActivationArtifactAttestation(Base):
    """DB-issued binding for one exact activation artifact digest."""

    __tablename__ = "journal_v2_p0_2m_activation_artifact_attestations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attestation_id = Column(String(64), nullable=False)
    artifact_sha256 = Column(String(64), nullable=False)
    attestation_schema_version = Column(String(64), nullable=False)
    authority = Column(String(64), nullable=False)
    calibration_schema_version = Column(String(64), nullable=False)
    universe_hash = Column(String(80), nullable=False)
    signal_version = Column(String(48), nullable=False)
    shadow_binding_sha256 = Column(String(64), nullable=False)
    source_export_attestation_id = Column(String(64), nullable=False)
    source_export_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "attestation_id",
            name="uq_jv2_p0_2m_activation_artifact_attestation_id",
        ),
        UniqueConstraint(
            "artifact_sha256",
            name="uq_jv2_p0_2m_activation_artifact_sha256",
        ),
        Index(
            "ix_jv2_p0_2m_activation_artifact_identity",
            "universe_hash",
            "signal_version",
            "created_at",
        ),
    )


def _guard_ddl(table_name: str, operation: str) -> str:
    # Lazy import avoids a module-import cycle while repository.py registers
    # every append-only model on the shared metadata.
    from src.journal.ledger.repository import ledger_guard_trigger_ddl

    return ledger_guard_trigger_ddl(table_name, operation)


def _install_guards(target, connection, **_kwargs) -> None:
    if connection.dialect.name != "sqlite":
        return
    for operation in ("UPDATE", "DELETE"):
        connection.exec_driver_sql(_guard_ddl(target.name, operation))


event.listen(P02mShadowObservation.__table__, "after_create", _install_guards)
event.listen(P02mShadowEnrollment.__table__, "after_create", _install_guards)
event.listen(
    P02mShadowExportAttestation.__table__, "after_create", _install_guards
)
event.listen(
    P02mActivationArtifactAttestation.__table__,
    "after_create",
    _install_guards,
)


def _repair_guards(_metadata, connection, **_kwargs) -> None:
    if connection.dialect.name != "sqlite":
        return
    existing = set(inspect(connection).get_table_names())
    for table_name in (
        P02mShadowObservation.__tablename__,
        P02mShadowEnrollment.__tablename__,
        "journal_v2_p0_2m_shadow_observations_pre_run_identity",
        "journal_v2_p0_2m_shadow_enrollments_pre_run_identity",
        P02mShadowExportAttestation.__tablename__,
        P02mActivationArtifactAttestation.__tablename__,
    ):
        if table_name not in existing:
            continue
        for operation in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(_guard_ddl(table_name, operation))


event.listen(Base.metadata, "after_create", _repair_guards)
