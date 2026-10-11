# -*- coding: utf-8 -*-
"""Mutable Telegram delivery outbox persistence and restart recovery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from src.journal.ledger.telegram_outbox_repository import (
    TelegramOutboxWrite,
    TelegramOutboxError,
    append_push_decisions_with_telegram_outbox,
    delete_telegram_outbox,
    list_expired_pending_telegram_outbox,
    list_pending_telegram_outbox,
    list_succeeded_telegram_outbox,
    list_telegram_outbox_for_date,
    mark_telegram_outbox_success,
    mark_telegram_outbox_terminal,
    reschedule_telegram_outbox,
    upsert_pending_telegram_outbox,
)
from src.journal.ledger.push_ledger_repository import (
    PushDecisionInput,
    PushDeliveryInput,
    append_push_events,
    list_push_events,
)

NOW = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)


def _enqueue(message_key: str = "message-1", **overrides):
    payload = dict(
        message_key=message_key,
        et_date="2026-09-18",
        tier="P2",
        text="【P0研究响铃】NVDA 向上",
        silent=False,
        expires_at=NOW + timedelta(minutes=30),
        next_attempt_at=NOW + timedelta(minutes=1),
        attempt_count=1,
        now=NOW,
    )
    payload.update(overrides)
    return upsert_pending_telegram_outbox(**payload)


def test_pending_round_trip_and_idempotent_upsert():
    first = _enqueue()
    assert first.state == "pending"
    assert first.attempt_count == 1
    assert first.created_at == NOW

    refreshed = _enqueue(
        text="更新后的完整消息",
        silent=True,
        attempt_count=0,
        next_attempt_at=NOW + timedelta(minutes=2),
        now=NOW + timedelta(seconds=30),
    )
    assert refreshed.message_key == first.message_key
    assert refreshed.text == "更新后的完整消息"
    assert refreshed.silent is True
    assert refreshed.attempt_count == 1
    assert refreshed.created_at == NOW
    assert refreshed.updated_at == NOW + timedelta(seconds=30)
    assert len(list_pending_telegram_outbox(now=NOW)) == 1


def test_daily_outbox_read_includes_final_states_without_other_days():
    _enqueue("pending")
    _enqueue("succeeded")
    _enqueue("terminal")
    _enqueue("other-day", et_date="2026-09-17")
    mark_telegram_outbox_success("succeeded", now=NOW)
    mark_telegram_outbox_terminal("terminal", now=NOW)

    jobs = list_telegram_outbox_for_date("2026-09-18")
    assert [(job.message_key, job.state) for job in jobs] == [
        ("pending", "pending"),
        ("succeeded", "succeeded"),
        ("terminal", "terminal"),
    ]
    assert list_telegram_outbox_for_date("2026-09-16") == ()


def test_list_pending_filters_expired_and_optionally_not_yet_due():
    _enqueue("due", next_attempt_at=NOW - timedelta(seconds=1))
    _enqueue("future", next_attempt_at=NOW + timedelta(minutes=3))
    _enqueue(
        "expired",
        expires_at=NOW - timedelta(seconds=1),
        next_attempt_at=NOW - timedelta(minutes=1),
    )

    assert [
        item.message_key for item in list_pending_telegram_outbox(now=NOW)
    ] == ["due", "future"]
    assert [
        item.message_key
        for item in list_pending_telegram_outbox(now=NOW, due_only=True)
    ] == ["due"]
    assert [
        item.message_key
        for item in list_expired_pending_telegram_outbox(now=NOW)
    ] == ["expired"]


def test_reschedule_then_success_records_attempts_and_stops_retry():
    _enqueue(next_attempt_at=NOW)
    assert reschedule_telegram_outbox(
        "message-1",
        next_attempt_at=NOW + timedelta(minutes=2),
        now=NOW + timedelta(seconds=2),
    )
    pending = list_pending_telegram_outbox(now=NOW)[0]
    assert pending.attempt_count == 2
    assert pending.next_attempt_at == NOW + timedelta(minutes=2)

    assert mark_telegram_outbox_success(
        "message-1",
        delivered_text="延迟补发后的实际正文",
        now=NOW + timedelta(minutes=2),
    )
    assert list_pending_telegram_outbox(now=NOW) == ()
    assert not reschedule_telegram_outbox(
        "message-1", next_attempt_at=NOW + timedelta(minutes=3), now=NOW
    )

    # Final rows are idempotency barriers and are never silently resurrected.
    final = _enqueue(text="must not replace final state")
    assert final.state == "succeeded"
    assert final.text != "must not replace final state"
    missing_receipt = list_succeeded_telegram_outbox()
    assert [row.message_key for row in missing_receipt] == ["message-1"]
    assert missing_receipt[0].text == "延迟补发后的实际正文"
    append_push_events(
        deliveries=(
            PushDeliveryInput(
                push_id="b" * 32,
                et_date="2026-09-18",
                ts=NOW,
                tier="P2",
                message_key="message-1",
                telegram_ok=True,
                text="延迟补发后的实际正文",
            ),
        )
    )
    assert list_succeeded_telegram_outbox() == ()


def test_terminal_transition_and_explicit_delete_are_mutable():
    _enqueue()
    assert mark_telegram_outbox_terminal("message-1", now=NOW)
    assert list_pending_telegram_outbox(now=NOW) == ()
    assert delete_telegram_outbox("message-1")
    assert not delete_telegram_outbox("message-1")
    assert _enqueue().state == "pending"


def test_decision_and_outbox_commit_atomically():
    decision = PushDecisionInput(
        push_id="a" * 32,
        et_date="2026-09-18",
        ts=NOW,
        tier="P0",
        rule="p0_2m_candidate",
        ticker="NVDA",
        text="NVDA strict 2m",
        delivery_channel="telegram",
        message_key="atomic-message",
        session_scope="regular",
    )
    write = TelegramOutboxWrite(
        message_key="atomic-message",
        et_date="2026-09-18",
        tier="P0",
        text="完整手机正文",
        silent=False,
        expires_at=NOW + timedelta(minutes=6),
        next_attempt_at=NOW + timedelta(seconds=15),
    )

    stored = append_push_decisions_with_telegram_outbox(
        (decision,), (write,), now=NOW
    )

    assert [row.state for row in stored] == ["pending"]
    assert [event.message_key for event in list_push_events("2026-09-18")] == [
        "atomic-message"
    ]
    assert [row.message_key for row in list_pending_telegram_outbox(now=NOW)] == [
        "atomic-message"
    ]

    # A constraint failure rolls back the second outbox row with the duplicate
    # immutable push id; the two durability records cannot split-brain.
    bad_write = TelegramOutboxWrite(
        message_key="must-rollback",
        et_date="2026-09-18",
        tier="P0",
        text="must not survive",
        silent=False,
        expires_at=NOW + timedelta(minutes=6),
        next_attempt_at=NOW + timedelta(seconds=15),
    )
    with pytest.raises(IntegrityError):
        append_push_decisions_with_telegram_outbox(
            (decision,), (bad_write,), now=NOW
        )
    assert "must-rollback" not in {
        row.message_key for row in list_pending_telegram_outbox(now=NOW)
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("message_key", "", "message_key"),
        ("et_date", "09/18/2026", "et_date"),
        ("tier", "P9", "unknown tier"),
        ("text", " ", "text"),
        ("attempt_count", -1, "non-negative"),
        ("expires_at", datetime(2026, 9, 18), "timezone"),
    ],
)
def test_invalid_payloads_fail_closed(field, value, message):
    with pytest.raises(TelegramOutboxError, match=message):
        _enqueue(**{field: value})
