# -*- coding: utf-8 -*-
"""Repository for the mutable Telegram delivery retry outbox."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import delete, select, update

from src.journal.ledger.repository import init_ledger_schema
from src.journal.ledger.telegram_outbox_models import TelegramDeliveryOutbox
from src.storage import get_db

__all__ = [
    "TelegramOutboxError",
    "TelegramOutboxItem",
    "TelegramOutboxWrite",
    "append_push_decisions_with_telegram_outbox",
    "delete_telegram_outbox",
    "list_expired_pending_telegram_outbox",
    "list_pending_telegram_outbox",
    "list_succeeded_telegram_outbox",
    "list_telegram_outbox_for_date",
    "mark_telegram_outbox_success",
    "mark_telegram_outbox_terminal",
    "reschedule_telegram_outbox",
    "upsert_pending_telegram_outbox",
]

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_TIERS = {"P0X", "P0", "P1", "P2", "P3", "system"}


class TelegramOutboxError(ValueError):
    """Raised when an outbox payload violates the persistence contract."""


@dataclass(frozen=True)
class TelegramOutboxItem:
    message_key: str
    et_date: str
    tier: str
    text: str
    silent: bool
    expires_at: datetime
    next_attempt_at: datetime
    attempt_count: int
    state: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TelegramOutboxWrite:
    """One pending outbox row committed atomically with push decisions."""

    message_key: str
    et_date: str
    tier: str
    text: str
    silent: bool
    expires_at: datetime
    next_attempt_at: datetime
    attempt_count: int = 0


def _utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TelegramOutboxError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise TelegramOutboxError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _loaded_utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata even for DateTime(timezone=True).
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validated_key(message_key: str) -> str:
    normalized = str(message_key or "").strip()
    if not normalized:
        raise TelegramOutboxError("message_key must not be empty")
    if len(normalized) > 64:
        raise TelegramOutboxError("message_key must be at most 64 characters")
    return normalized


def _validated_date(et_date: str) -> str:
    normalized = str(et_date or "")
    if not _ISO_DATE.fullmatch(normalized):
        raise TelegramOutboxError(
            "et_date must be an ET calendar date (YYYY-MM-DD)"
        )
    return normalized


def _validated_tier(tier: str) -> str:
    normalized = str(tier or "")
    if normalized not in _VALID_TIERS:
        raise TelegramOutboxError(f"unknown tier: {normalized!r}")
    return normalized


def _stored(row: TelegramDeliveryOutbox) -> TelegramOutboxItem:
    return TelegramOutboxItem(
        message_key=str(row.message_key),
        et_date=str(row.et_date),
        tier=str(row.tier),
        text=str(row.text),
        silent=bool(row.silent),
        expires_at=_loaded_utc(row.expires_at),
        next_attempt_at=_loaded_utc(row.next_attempt_at),
        attempt_count=int(row.attempt_count),
        state=str(row.state),
        created_at=_loaded_utc(row.created_at),
        updated_at=_loaded_utc(row.updated_at),
    )


def upsert_pending_telegram_outbox(
    *,
    message_key: str,
    et_date: str,
    tier: str,
    text: str,
    silent: bool,
    expires_at: datetime,
    next_attempt_at: datetime,
    attempt_count: int = 0,
    now: Optional[datetime] = None,
) -> TelegramOutboxItem:
    """Create or refresh a pending job without resurrecting a final row.

    Repeating the same ``message_key`` while it is pending updates its payload
    and schedule.  Its attempt count never moves backwards.  A succeeded or
    terminal row is returned unchanged, preserving message-level idempotency.
    """
    key = _validated_key(message_key)
    date_value = _validated_date(et_date)
    tier_value = _validated_tier(tier)
    text_value = str(text or "")
    if not text_value.strip():
        raise TelegramOutboxError("text must not be empty")
    try:
        attempts = int(attempt_count)
    except (TypeError, ValueError) as exc:
        raise TelegramOutboxError("attempt_count must be an integer") from exc
    if attempts < 0:
        raise TelegramOutboxError("attempt_count must be non-negative")
    expiry = _utc(expires_at, field_name="expires_at")
    next_attempt = _utc(next_attempt_at, field_name="next_attempt_at")
    current = _utc(now or datetime.now(timezone.utc), field_name="now")

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.execute(
            select(TelegramDeliveryOutbox).where(
                TelegramDeliveryOutbox.message_key == key
            )
        ).scalar_one_or_none()
        if row is None:
            row = TelegramDeliveryOutbox(
                message_key=key,
                et_date=date_value,
                tier=tier_value,
                text=text_value,
                silent=bool(silent),
                expires_at=expiry,
                next_attempt_at=next_attempt,
                attempt_count=attempts,
                state="pending",
                created_at=current,
                updated_at=current,
            )
            session.add(row)
        elif row.state == "pending":
            row.et_date = date_value
            row.tier = tier_value
            row.text = text_value
            row.silent = bool(silent)
            row.expires_at = expiry
            row.next_attempt_at = next_attempt
            row.attempt_count = max(int(row.attempt_count), attempts)
            row.updated_at = current
        session.flush()
        return _stored(row)


def append_push_decisions_with_telegram_outbox(
    decisions: Iterable[object],
    writes: Sequence[TelegramOutboxWrite],
    *,
    now: Optional[datetime] = None,
) -> tuple[TelegramOutboxItem, ...]:
    """Atomically append immutable decisions and create their retry jobs.

    This closes the crash window where a decision existed but its real phone
    message had not yet entered durable retry storage.
    """

    from src.journal.ledger.push_ledger_repository import (
        _prepare_push_event_rows,
    )

    current = _utc(now or datetime.now(timezone.utc), field_name="now")
    decision_rows = _prepare_push_event_rows(decisions, ())
    normalized: list[dict[str, object]] = []
    for write in writes:
        try:
            attempts = int(write.attempt_count)
        except (TypeError, ValueError) as exc:
            raise TelegramOutboxError(
                "attempt_count must be an integer"
            ) from exc
        if attempts < 0:
            raise TelegramOutboxError(
                "attempt_count must be non-negative"
            )
        text_value = str(write.text or "")
        if not text_value.strip():
            raise TelegramOutboxError("text must not be empty")
        normalized.append(
            {
                "message_key": _validated_key(write.message_key),
                "et_date": _validated_date(write.et_date),
                "tier": _validated_tier(write.tier),
                "text": text_value,
                "silent": bool(write.silent),
                "expires_at": _utc(
                    write.expires_at, field_name="expires_at"
                ),
                "next_attempt_at": _utc(
                    write.next_attempt_at,
                    field_name="next_attempt_at",
                ),
                "attempt_count": attempts,
            }
        )

    init_ledger_schema()
    db = get_db()
    stored_rows: list[TelegramDeliveryOutbox] = []
    with db.session_scope() as session:
        session.add_all(decision_rows)
        for values in normalized:
            key = str(values["message_key"])
            row = session.execute(
                select(TelegramDeliveryOutbox).where(
                    TelegramDeliveryOutbox.message_key == key
                )
            ).scalar_one_or_none()
            if row is None:
                row = TelegramDeliveryOutbox(
                    **values,
                    state="pending",
                    created_at=current,
                    updated_at=current,
                )
                session.add(row)
            elif row.state == "pending":
                row.et_date = str(values["et_date"])
                row.tier = str(values["tier"])
                row.text = str(values["text"])
                row.silent = bool(values["silent"])
                row.expires_at = values["expires_at"]
                row.next_attempt_at = values["next_attempt_at"]
                row.attempt_count = max(
                    int(row.attempt_count),
                    int(values["attempt_count"]),
                )
                row.updated_at = current
            stored_rows.append(row)
        session.flush()
        return tuple(_stored(row) for row in stored_rows)


def list_telegram_outbox_for_date(et_date: str) -> tuple[TelegramOutboxItem, ...]:
    """Read all operational delivery states for one feed day in one query."""

    date_value = _validated_date(et_date)
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(TelegramDeliveryOutbox)
            .where(TelegramDeliveryOutbox.et_date == date_value)
            .order_by(TelegramDeliveryOutbox.id.asc())
        ).scalars()
        return tuple(_stored(row) for row in rows)


def list_pending_telegram_outbox(
    *,
    now: Optional[datetime] = None,
    due_only: bool = False,
    limit: int = 100,
) -> tuple[TelegramOutboxItem, ...]:
    """Return unexpired pending jobs, earliest scheduled attempt first."""
    current = _utc(now or datetime.now(timezone.utc), field_name="now")
    bounded_limit = int(limit)
    if bounded_limit <= 0:
        return ()
    bounded_limit = min(bounded_limit, 1000)

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        query = select(TelegramDeliveryOutbox).where(
            TelegramDeliveryOutbox.state == "pending",
            TelegramDeliveryOutbox.expires_at > current,
        )
        if due_only:
            query = query.where(
                TelegramDeliveryOutbox.next_attempt_at <= current
            )
        rows = session.execute(
            query.order_by(
                TelegramDeliveryOutbox.next_attempt_at.asc(),
                TelegramDeliveryOutbox.id.asc(),
            ).limit(bounded_limit)
        ).scalars()
        return tuple(_stored(row) for row in rows)


def list_expired_pending_telegram_outbox(
    *,
    now: Optional[datetime] = None,
    limit: int = 100,
) -> tuple[TelegramOutboxItem, ...]:
    """Return pending jobs whose hard delivery deadline has passed."""

    current = _utc(now or datetime.now(timezone.utc), field_name="now")
    bounded_limit = int(limit)
    if bounded_limit <= 0:
        return ()
    bounded_limit = min(bounded_limit, 1000)
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(TelegramDeliveryOutbox)
            .where(
                TelegramDeliveryOutbox.state == "pending",
                TelegramDeliveryOutbox.expires_at <= current,
            )
            .order_by(
                TelegramDeliveryOutbox.expires_at.asc(),
                TelegramDeliveryOutbox.id.asc(),
            )
            .limit(bounded_limit)
        ).scalars()
        return tuple(_stored(row) for row in rows)


def list_succeeded_telegram_outbox(
    *,
    limit: int = 1000,
) -> tuple[TelegramOutboxItem, ...]:
    """Return succeeded jobs that still lack an immutable success receipt."""

    bounded_limit = int(limit)
    if bounded_limit <= 0:
        return ()
    bounded_limit = min(bounded_limit, 1000)
    init_ledger_schema()
    from src.journal.ledger.push_models import PushEvent

    db = get_db()
    with db.session_scope() as session:
        receipt_exists = (
            select(PushEvent.id)
            .where(
                PushEvent.record_kind == "delivery",
                PushEvent.message_key
                == TelegramDeliveryOutbox.message_key,
                PushEvent.telegram_ok.is_(True),
            )
            .exists()
        )
        rows = session.execute(
            select(TelegramDeliveryOutbox)
            .where(
                TelegramDeliveryOutbox.state == "succeeded",
                ~receipt_exists,
            )
            .order_by(TelegramDeliveryOutbox.id.desc())
            .limit(bounded_limit)
        ).scalars()
        return tuple(_stored(row) for row in rows)


def reschedule_telegram_outbox(
    message_key: str,
    *,
    next_attempt_at: datetime,
    now: Optional[datetime] = None,
) -> bool:
    """Record one failed application-level attempt and schedule the next."""
    key = _validated_key(message_key)
    next_attempt = _utc(next_attempt_at, field_name="next_attempt_at")
    current = _utc(now or datetime.now(timezone.utc), field_name="now")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        result = session.execute(
            update(TelegramDeliveryOutbox)
            .where(
                TelegramDeliveryOutbox.message_key == key,
                TelegramDeliveryOutbox.state == "pending",
            )
            .values(
                next_attempt_at=next_attempt,
                attempt_count=TelegramDeliveryOutbox.attempt_count + 1,
                updated_at=current,
            )
        )
        return bool(result.rowcount)


def mark_telegram_outbox_success(
    message_key: str,
    *,
    delivered_text: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Record one successful attempt and make the job non-retryable."""
    key = _validated_key(message_key)
    current = _utc(now or datetime.now(timezone.utc), field_name="now")
    values: dict[str, object] = {
        "state": "succeeded",
        "attempt_count": TelegramDeliveryOutbox.attempt_count + 1,
        "updated_at": current,
    }
    if delivered_text is not None:
        normalized_text = str(delivered_text)
        if not normalized_text.strip():
            raise TelegramOutboxError("delivered_text must not be empty")
        values["text"] = normalized_text
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        result = session.execute(
            update(TelegramDeliveryOutbox)
            .where(
                TelegramDeliveryOutbox.message_key == key,
                TelegramDeliveryOutbox.state == "pending",
            )
            .values(**values)
        )
        return bool(result.rowcount)


def mark_telegram_outbox_terminal(
    message_key: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Stop retrying a pending job without counting another send attempt."""
    key = _validated_key(message_key)
    current = _utc(now or datetime.now(timezone.utc), field_name="now")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        result = session.execute(
            update(TelegramDeliveryOutbox)
            .where(
                TelegramDeliveryOutbox.message_key == key,
                TelegramDeliveryOutbox.state == "pending",
            )
            .values(state="terminal", updated_at=current)
        )
        return bool(result.rowcount)


def delete_telegram_outbox(message_key: str) -> bool:
    """Delete an operational row after its immutable receipt is persisted."""
    key = _validated_key(message_key)
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        result = session.execute(
            delete(TelegramDeliveryOutbox).where(
                TelegramDeliveryOutbox.message_key == key
            )
        )
        return bool(result.rowcount)
