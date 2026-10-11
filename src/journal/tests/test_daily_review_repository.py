# -*- coding: utf-8 -*-
"""Append-only daily review session chain (blueprint 17 §三(a)/(e)-2)."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from src.journal.ledger.daily_review_repository import (
    DailyReviewRepositoryError,
    DailyReviewSealedError,
    DailyReviewSessionInput,
    append_daily_review_session,
    get_latest_daily_review_session,
    list_daily_review_session_history,
    list_latest_daily_review_sessions,
)
from src.journal.ledger.repository import init_ledger_schema
from src.storage import get_db


def _input(**overrides) -> DailyReviewSessionInput:
    payload = {
        "et_date": "2026-08-24",
        "session_kind": "trading_day",
        "steps": {"s0": True},
        "process_scores": (
            {"metric_id": 2, "basis": "auto", "value": 0.5},
        ),
        "violation_acks": (),
        "note": "",
        "sealed": False,
        "revealed_after_seal": False,
    }
    payload.update(overrides)
    return DailyReviewSessionInput(**payload)


def test_revision_chain_and_idempotent_replay():
    first = append_daily_review_session(_input())
    assert first.duplicate is False
    assert first.session.revision == 1
    assert first.session.previous_session_id is None

    replay = append_daily_review_session(_input())
    assert replay.duplicate is True
    assert replay.session.session_id == first.session.session_id

    second = append_daily_review_session(
        _input(steps={"s0": True, "s1": True})
    )
    assert second.duplicate is False
    assert second.session.revision == 2
    assert second.session.previous_session_id == first.session.session_id
    assert second.session.started_at == first.session.started_at

    latest = get_latest_daily_review_session(et_date="2026-08-24")
    assert latest is not None and latest.revision == 2
    history = list_daily_review_session_history(et_date="2026-08-24")
    assert [item.revision for item in history] == [2, 1]


def test_seal_freezes_content_and_reveal_is_a_separate_revision():
    append_daily_review_session(_input())
    sealed = append_daily_review_session(_input(sealed=True, note="收工"))
    assert sealed.session.sealed_at is not None
    assert sealed.session.revealed_after_seal is False

    # 密封后改内容 → 拒绝。
    with pytest.raises(DailyReviewSealedError):
        append_daily_review_session(
            _input(sealed=True, note="改主意了")
        )
    # 撤销密封 → 拒绝。
    with pytest.raises(DailyReviewSealedError):
        append_daily_review_session(_input(sealed=False, note="收工"))

    # 揭示＝独立修订，冻结内容逐字节一致。
    revealed = append_daily_review_session(
        _input(sealed=True, note="收工", revealed_after_seal=True)
    )
    assert revealed.session.revision == 3
    assert revealed.session.revealed_after_seal is True
    assert revealed.session.revealed_at is not None
    assert revealed.session.sealed_at == sealed.session.sealed_at

    # 揭示不可撤销。
    with pytest.raises(DailyReviewSealedError):
        append_daily_review_session(
            _input(sealed=True, note="收工", revealed_after_seal=False)
        )


def test_reveal_requires_a_previously_sealed_revision():
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(
            _input(sealed=True, revealed_after_seal=True)
        )
    append_daily_review_session(_input())
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(
            _input(sealed=True, revealed_after_seal=True)
        )


def test_reveal_without_seal_flag_rejected_at_normalization():
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(
            _input(sealed=False, revealed_after_seal=True)
        )


def test_validation_failures():
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(_input(et_date="24-08-2026"))
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(_input(session_kind="weekend"))
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(
            _input(violation_acks=({"position_episode_id": 5, "ack_text": ""},))
        )
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(
            _input(process_scores=({"metric_id": 9, "basis": "auto"},))
        )
    with pytest.raises(DailyReviewRepositoryError):
        append_daily_review_session(
            _input(process_scores=({"metric_id": 2, "basis": "guessed"},))
        )


def test_rest_day_sessions_and_latest_per_date_listing():
    append_daily_review_session(
        _input(et_date="2026-08-21", session_kind="rest_day", sealed=True)
    )
    append_daily_review_session(_input(et_date="2026-08-24"))
    append_daily_review_session(
        _input(et_date="2026-08-24", steps={"s0": True, "s1": True})
    )
    sessions = list_latest_daily_review_sessions()
    assert [item.et_date for item in sessions] == ["2026-08-24", "2026-08-21"]
    assert sessions[0].revision == 2
    assert sessions[1].session_kind == "rest_day"


def test_deny_trigger_blocks_update_and_delete():
    result = append_daily_review_session(_input())
    init_ledger_schema()
    db = get_db()
    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.connection().exec_driver_sql(
                "UPDATE journal_v2_daily_review_sessions SET note = 'x' "
                f"WHERE id = {result.session.session_id}"
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.connection().exec_driver_sql(
                "DELETE FROM journal_v2_daily_review_sessions "
                f"WHERE id = {result.session.session_id}"
            )
    history = list_daily_review_session_history(et_date="2026-08-24")
    assert len(history) == 1
