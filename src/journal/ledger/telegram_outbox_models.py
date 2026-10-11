# -*- coding: utf-8 -*-
"""Mutable operational outbox for delayed Telegram delivery.

Unlike ``journal_v2_push_events`` this table is not evidence: workers update
its rows while retrying and may delete completed rows after the immutable
delivery receipt has been appended to the push ledger.  It therefore must not
be added to the Journal v2 append-only guard list.
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
    String,
    Text,
    UniqueConstraint,
)

from src.storage import Base

__all__ = ["TelegramDeliveryOutbox"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TelegramDeliveryOutbox(Base):
    """One mutable retry job, idempotently keyed by the outbound message."""

    __tablename__ = "telegram_delivery_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_key = Column(String(64), nullable=False)
    et_date = Column(String(10), nullable=False)
    tier = Column(String(8), nullable=False)
    text = Column(Text, nullable=False)
    silent = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    state = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    __table_args__ = (
        UniqueConstraint("message_key", name="uq_telegram_outbox_message_key"),
        CheckConstraint(
            "tier IN ('P0X', 'P0', 'P1', 'P2', 'P3', 'system')",
            name="ck_telegram_outbox_tier",
        ),
        CheckConstraint(
            "state IN ('pending', 'succeeded', 'terminal')",
            name="ck_telegram_outbox_state",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_telegram_outbox_attempt_count",
        ),
        Index(
            "ix_telegram_outbox_pending_due",
            "state",
            "next_attempt_at",
        ),
        Index("ix_telegram_outbox_expiry", "expires_at"),
    )
