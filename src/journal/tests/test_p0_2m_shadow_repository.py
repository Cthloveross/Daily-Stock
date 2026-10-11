# -*- coding: utf-8 -*-
"""Strict-2m append-only live-forward telemetry tests."""
from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pytest
from sqlalchemy import Index, MetaData, Table, UniqueConstraint, create_engine, text

from src.journal.ledger import p0_2m_shadow_repository as repository
from src.journal.ledger.p0_2m_shadow_models import (
    P02mShadowEnrollment,
    P02mShadowExportAttestation,
    P02mShadowObservation,
)
from src.journal.ledger.p0_2m_shadow_repository import (
    LIVE_FORWARD_SHADOW_MODE,
    P02mShadowObservationInput,
    append_p0_2m_shadow_observations,
    ensure_p0_2m_live_shadow_enrollment,
    export_p0_2m_live_shadow_rows,
    list_p0_2m_shadow_observations,
    start_new_p0_2m_live_shadow_run,
    validate_p0_2m_live_shadow_export_attestation,
)
from src.journal.ledger.repository import (
    _APPEND_ONLY_TABLE_NAMES,
    ledger_guard_trigger_ddl,
    ledger_guard_trigger_name,
)
from src.opportunities.p0_2m_calibration import (
    DEFAULT_P0_UNIVERSE_69,
    DEFAULT_P0_UNIVERSE_HASH,
    _shadow_report,
)
from src.opportunities.p0_2m_signal import P0_2M_SIGNAL_VERSION
from src.storage import DatabaseManager, get_db

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
_END = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)  # 10:00 ET half-day


def _twenty_sessions_ending(day: date) -> list[str]:
    calendar = xcals.get_calendar("XNYS")
    sessions = calendar.sessions_in_range(day - timedelta(days=45), day)
    return [stamp.date().isoformat() for stamp in sessions[-20:]]


def _v1_shadow_metadata():
    metadata = MetaData()
    enrollment = Table(
        P02mShadowEnrollment.__tablename__,
        metadata,
        *[
            column._copy()
            for column in P02mShadowEnrollment.__table__.columns
            if column.name
            not in {
                "run_request_id",
                "restart_reason",
                "supersedes_enrollment_key",
            }
        ],
        UniqueConstraint("enrollment_key"),
        UniqueConstraint("universe_hash", "signal_version"),
    )
    observation = Table(
        P02mShadowObservation.__tablename__,
        metadata,
        *[
            column._copy()
            for column in P02mShadowObservation.__table__.columns
            if column.name not in {"enrollment_key", "window_close"}
        ],
        UniqueConstraint("observation_key"),
        UniqueConstraint(
            "symbol",
            "et_date",
            "window_end",
            "universe_hash",
            "signal_version",
            "record_kind",
            "notification_attempt_id",
        ),
    )
    Index(
        "ix_jv2_p0_2m_shadow_enrollment_identity",
        enrollment.c.universe_hash,
        enrollment.c.signal_version,
    )
    Index(
        "ix_jv2_p0_2m_shadow_day_symbol",
        observation.c.et_date,
        observation.c.symbol,
        observation.c.window_end,
    )
    Index(
        "ix_jv2_p0_2m_shadow_identity",
        observation.c.universe_hash,
        observation.c.signal_version,
        observation.c.et_date,
    )
    return metadata, enrollment, observation


def _enroll(monkeypatch):
    sessions = _twenty_sessions_ending(date(2026, 11, 27))
    first_day = date.fromisoformat(sessions[0])
    enrolled_at = datetime.combine(first_day, time(9), ET).astimezone(UTC)
    clock = {"now": enrolled_at}
    monkeypatch.setattr(repository, "_utc_now", lambda: clock["now"])
    enrollment = ensure_p0_2m_live_shadow_enrollment(
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        enrollment_started_at=enrolled_at,
    )
    assert list(enrollment.sessions) == sessions
    assert len(enrollment.sessions) == 20
    return clock, sessions, enrollment


def _common(
    *,
    symbol="META",
    window_end=_END,
    observed_at=None,
    direction="down",
):
    observed = observed_at or window_end + timedelta(seconds=20)
    return dict(
        symbol=symbol,
        et_date=window_end.astimezone(ET).date().isoformat(),
        window_end=window_end,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        signal_version=P0_2M_SIGNAL_VERSION,
        observed_at=observed,
        max_component_end=window_end,
        direction=direction,
        source="MoomooFetcher",
        data_quality={"status": "complete", "issues": []},
    )


def _endpoint(*, data_available=True, **kwargs):
    return P02mShadowObservationInput(
        **_common(**kwargs),
        record_kind="endpoint_observation",
        data_available=data_available,
        evaluation_available=False,
        evaluation_state="endpoint_observed",
        qualified=False,
        would_notify=False,
        notification_decision_available=False,
    )


def _evaluation(
    *,
    qualified=True,
    would_notify=True,
    notification_decision_available=True,
    duplicate_episode_suppressed=False,
    **kwargs,
):
    return P02mShadowObservationInput(
        **_common(**kwargs),
        record_kind="completed_evaluation",
        data_available=True,
        evaluation_available=True,
        evaluation_state="qualified" if qualified else "not_qualified",
        qualified=qualified,
        would_notify=would_notify,
        notification_decision_available=notification_decision_available,
        duplicate_episode_suppressed=duplicate_episode_suppressed,
    )


def _attempt(*, attempt_id, accepted=True, **kwargs):
    common = _common(**kwargs)
    enqueued_at = common["observed_at"] if accepted else None
    return P02mShadowObservationInput(
        **common,
        record_kind="notification_attempt",
        notification_attempt_id=attempt_id,
        data_available=True,
        evaluation_available=True,
        evaluation_state="qualified",
        qualified=True,
        would_notify=True,
        notification_decision_available=True,
        observer_queue_accepted=accepted,
        observer_queue_enqueued_at=enqueued_at,
        telegram_enqueue_evidence_mode=(
            "counterfactual_no_send_same_queue"
        ),
        telegram_enqueued_at=enqueued_at,
    )


def _append_at(clock, *rows):
    clock["now"] = max(row.observed_at for row in rows)
    return append_p0_2m_shadow_observations(list(rows))


def _append_bundle(
    clock,
    *,
    window_end=_END,
    symbol="META",
    qualified=True,
    would_notify=True,
    notification_decision_available=True,
    duplicate_episode_suppressed=False,
    attempt_id=None,
    observed_at=None,
):
    kwargs = dict(
        symbol=symbol,
        window_end=window_end,
        observed_at=observed_at,
    )
    rows = [
        _endpoint(**kwargs),
        _evaluation(
            qualified=qualified,
            would_notify=would_notify,
            notification_decision_available=notification_decision_available,
            duplicate_episode_suppressed=duplicate_episode_suppressed,
            **kwargs,
        ),
    ]
    if attempt_id is not None:
        rows.append(_attempt(attempt_id=attempt_id, **kwargs))
    return _append_at(clock, *rows)


def _priced_evaluation(price, **kwargs):
    row = _evaluation(**kwargs)
    return replace(
        row,
        window_close=price,
        data_quality={
            **row.data_quality,
            "window_close_basis": {
                "source": row.source,
                "price": "completed_exact_2m_close",
                "adjustment": "same_session_provider_basis",
                "session_scope": "regular",
                "basis_verified": False,
                "limitation": "provider_adjustment_mode_not_explicit",
            },
        },
    )


def _export(clock, sessions):
    return export_p0_2m_live_shadow_rows(
        session_dates=sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )


def test_forward_outcomes_use_exact_prices_before_budget_and_bind_diagnostics(monkeypatch):
    clock, sessions, _ = _enroll(monkeypatch)
    for minute, price, qualified, direction, notify in (
        (0, 100, True, "up", False),
        (10, 102, True, "up", False),  # same-direction duplicate
        (12, 100, True, "down", True),  # independent opposite direction
        (20, 110, False, "up", False),  # exact t+20, never needs to qualify
        (32, 120, True, "up", False),  # new episode anchored at t, not t+10
        (52, 126, False, "up", False),
    ):
        end = _END + timedelta(minutes=minute)
        _append_at(
            clock,
            _endpoint(window_end=end),
            _priced_evaluation(
                price, window_end=end, qualified=qualified,
                direction=direction, would_notify=notify,
            ),
        )
    exported = _export(clock, sessions)
    row = next(row for row in exported["shadow"] if row["symbol"] == "META"
               and row["session_date"] == "2026-11-27")
    diagnostics = row["diagnostics"]
    assert diagnostics["schema_version"] == "p0_2m_shadow_outcomes_v1"
    assert diagnostics["activation_evidence"] is False
    assert diagnostics["qualified_endpoint_count"] == 4
    assert diagnostics["event_count"] == 3
    assert diagnostics["deduplicated_qualified_count"] == 1
    assert diagnostics["status_counts"] == {
        "matured": 3, "session_censored": 0, "missing": 0, "pending": 0,
    }
    assert [event["direction_adjusted_return_20m"]
            for event in diagnostics["events"]] == pytest.approx([0.1, -0.2, 0.05])
    assert len(diagnostics["would_notify_events"]) == 1
    assert diagnostics["would_notify_events"][0]["direction"] == "down"
    assert diagnostics["notification_events"] == []
    assert diagnostics["events"][0]["would_notify"] is False
    assert diagnostics["events"][0]["window_close_basis"]["basis_verified"] is False
    bound_hash = exported["export_attestation"]["export_sha256"]
    assert repository.compute_p0_2m_live_shadow_export_sha256(exported) == bound_hash
    diagnostics["events"][0]["direction_adjusted_return_20m"] = 99
    assert repository.compute_p0_2m_live_shadow_export_sha256(exported) != bound_hash


def test_forward_outcomes_pending_missing_half_day_and_null_legacy_prices(monkeypatch):
    clock, sessions, _ = _enroll(monkeypatch)
    _append_at(clock, _endpoint(), _priced_evaluation(100))
    exported = _export(clock, sessions)
    row = next(row for row in exported["shadow"] if row["symbol"] == "META"
               and row["session_date"] == "2026-11-27")
    assert row["diagnostics"]["events"][0]["outcome_status"] == "pending"

    # Nearby endpoints and another symbol's exact endpoint cannot fill t+20.
    for minute, symbol in ((18, "META"), (22, "META"), (20, "AAPL")):
        end = _END + timedelta(minutes=minute)
        _append_at(clock, _endpoint(window_end=end, symbol=symbol),
                   _priced_evaluation(95, window_end=end, symbol=symbol,
                                      qualified=False, would_notify=False))
    late = _END.replace(hour=17, minute=50)  # 12:50 ET, actual close is 13:00
    _append_at(clock, _endpoint(window_end=late),
               _priced_evaluation(100, window_end=late))
    legacy = _END + timedelta(minutes=40)
    _append_at(clock, _endpoint(window_end=legacy), _evaluation(window_end=legacy))
    clock["now"] = late + timedelta(seconds=20)
    exported = _export(clock, sessions)
    row = next(row for row in exported["shadow"] if row["symbol"] == "META"
               and row["session_date"] == "2026-11-27")
    events = row["diagnostics"]["events"]
    assert [event["outcome_status"] for event in events] == [
        "missing", "missing", "session_censored",
    ]
    assert [event["outcome_reason"] for event in events] == [
        "exact_t_plus_20_evaluation_unavailable", "entry_price_missing",
        "t_plus_20_after_rth_close",
    ]
    assert all(event["direction_adjusted_return_20m"] is None for event in events)


def test_forward_notification_subset_requires_real_accepted_queue_receipt(monkeypatch):
    clock, sessions, _ = _enroll(monkeypatch)
    _append_at(
        clock, _endpoint(), _priced_evaluation(100),
        _attempt(attempt_id="counterfactual-probe"),
        replace(
            _attempt(attempt_id="rejected-real-attempt", accepted=False),
            telegram_enqueue_evidence_mode="actual_telegram_queue_receipt",
        ),
    )
    actual_end = _END + timedelta(minutes=4)
    _append_at(
        clock, _endpoint(window_end=actual_end, direction="up"),
        _priced_evaluation(100, window_end=actual_end, direction="up"),
        replace(
            _attempt(attempt_id="real-research-alert", window_end=actual_end, direction="up"),
            telegram_enqueue_evidence_mode="actual_telegram_queue_receipt",
        ),
    )
    for end in (_END + timedelta(minutes=20), actual_end + timedelta(minutes=20)):
        _append_at(clock, _endpoint(window_end=end),
                   _priced_evaluation(105, window_end=end, qualified=False, would_notify=False))
    exported = _export(clock, sessions)
    row = next(row for row in exported["shadow"] if row["symbol"] == "META"
               and row["session_date"] == "2026-11-27")
    diagnostics = row["diagnostics"]
    assert diagnostics["event_count"] == 2
    assert diagnostics["would_notify_event_count"] == 2
    assert diagnostics["notification_event_count"] == 1
    actual = diagnostics["notification_events"][0]
    assert actual["notification_attempt_ids"] == ["real-research-alert"]
    assert actual["phone_delivery_verified"] is False
    assert actual["direction_adjusted_return_20m"] == pytest.approx(0.05)
    assert diagnostics["notification_status_counts"]["matured"] == 1


def test_forward_outcomes_fail_closed_on_price_basis_or_run_mismatch(monkeypatch):
    clock, sessions, original = _enroll(monkeypatch)
    _append_at(clock, _endpoint(), _priced_evaluation(100))
    end = _END + timedelta(minutes=20)
    future = _priced_evaluation(90, window_end=end, qualified=False, would_notify=False)
    quality = dict(future.data_quality)
    quality["window_close_basis"] = {
        **quality["window_close_basis"], "adjustment": "different_provider_basis",
    }
    _append_at(clock, _endpoint(window_end=end), replace(future, data_quality=quality))
    exported = _export(clock, sessions)
    row = next(row for row in exported["shadow"] if row["symbol"] == "META"
               and row["session_date"] == "2026-11-27")
    assert row["diagnostics"]["events"][0]["outcome_reason"] == "price_basis_mismatch"
    stored = list_p0_2m_shadow_observations(
        et_dates=["2026-11-27"], symbols=["META"],
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    entries = {item.window_end.astimezone(ET).isoformat(): item for item in stored
               if item.record_kind == "completed_evaluation"}
    current = entries[_END.astimezone(ET).isoformat()]
    entries[end.astimezone(ET).isoformat()] = replace(
        current, window_end=end, window_close=90, enrollment_key="f" * 64,
    )
    diagnostics = repository._forward_outcome_diagnostics(
        evaluations=entries, qualified={_END.isoformat(): current},
        session_close=_END.replace(hour=18), exported_at=clock["now"],
    )
    assert diagnostics["events"][0]["outcome_reason"] == "price_basis_mismatch"
    assert original.enrollment_key != "f" * 64


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_shadow_price_requires_finite_positive_completed_evidence(monkeypatch, price):
    clock, _sessions, _ = _enroll(monkeypatch)
    with pytest.raises(ValueError, match="finite positive"):
        _append_at(clock, _priced_evaluation(price))
    with pytest.raises(ValueError, match="complete final evaluation"):
        _append_at(clock, replace(_priced_evaluation(100), max_component_end=None))


def test_append_only_stages_restart_dedupe_and_attempt_identity(monkeypatch):
    clock, sessions, _enrollment = _enroll(monkeypatch)
    endpoint = _endpoint()
    evaluation = _evaluation()
    attempt = _attempt(attempt_id="message-1")
    result = _append_at(clock, endpoint, evaluation, attempt)
    assert (result.inserted_count, result.duplicate_count) == (3, 0)

    DatabaseManager.reset_instance()
    retried = replace(
        endpoint,
        observed_at=endpoint.observed_at + timedelta(seconds=40),
        data_available=False,
    )
    result = _append_at(clock, retried, _attempt(attempt_id="message-2"))
    assert (result.inserted_count, result.duplicate_count) == (1, 1)
    result = _append_at(clock, _attempt(attempt_id="message-2"))
    assert (result.inserted_count, result.duplicate_count) == (0, 1)

    rows = list_p0_2m_shadow_observations(
        et_dates=[endpoint.et_date],
        symbols=[endpoint.symbol],
        universe_hash=endpoint.universe_hash,
    )
    assert len(rows) == 4
    stored_endpoint = next(
        row for row in rows if row.record_kind == "endpoint_observation"
    )
    assert stored_endpoint.observed_at == endpoint.observed_at
    assert stored_endpoint.data_available is True
    assert {
        row.notification_attempt_id
        for row in rows
        if row.record_kind == "notification_attempt"
    } == {"message-1", "message-2"}
    export_p0_2m_live_shadow_rows(
        session_dates=sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    assert "journal_v2_p0_2m_shadow_export_attestations" in (
        _APPEND_ONLY_TABLE_NAMES
    )
    assert "journal_v2_p0_2m_activation_artifact_attestations" in (
        _APPEND_ONLY_TABLE_NAMES
    )

    db = get_db()
    for statement in (
        "UPDATE journal_v2_p0_2m_shadow_observations SET qualified = 0",
        "DELETE FROM journal_v2_p0_2m_shadow_observations",
        "DELETE FROM journal_v2_p0_2m_shadow_enrollments",
        "UPDATE journal_v2_p0_2m_shadow_export_attestations "
        "SET authority = 'forged'",
        "DELETE FROM journal_v2_p0_2m_shadow_export_attestations",
    ):
        with pytest.raises(Exception, match="append-only"):
            with db.session_scope() as session:
                session.execute(text(statement))


def test_export_half_day_missing_windows_latency_and_component_audit(monkeypatch):
    clock, sessions, enrollment = _enroll(monkeypatch)
    _append_bundle(clock, attempt_id="message-1")
    _append_bundle(
        clock,
        window_end=_END + timedelta(minutes=2),
        qualified=True,
        would_notify=False,
        duplicate_episode_suppressed=True,
    )
    early_observed = _END + timedelta(minutes=3, seconds=59)
    _append_at(
        clock,
        _endpoint(
            symbol="TSLA",
            window_end=_END + timedelta(minutes=4),
            observed_at=early_observed,
        ),
        _evaluation(
            symbol="TSLA",
            window_end=_END + timedelta(minutes=4),
            observed_at=early_observed,
            qualified=False,
            would_notify=False,
        ),
    )

    exported = export_p0_2m_live_shadow_rows(
        session_dates=sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    assert exported["shadow_observation_mode"] == LIVE_FORWARD_SHADOW_MODE
    assert exported["expected_first_session"] == sessions[0]
    assert exported["enrollment"]["sessions"] == sessions
    assert exported["enrollment"]["enrolled_at"] == (
        enrollment.enrolled_at.isoformat()
    )
    assert validate_p0_2m_live_shadow_export_attestation(
        exported["export_attestation"],
        expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    ) == (True, "ok")
    forged_receipt = dict(exported["export_attestation"])
    forged_receipt["export_sha256"] = "0" * 64
    assert validate_p0_2m_live_shadow_export_attestation(
        forged_receipt,
        expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )[0] is False
    assert len(exported["shadow"]) == 20 * 69
    rows = {
        (row["symbol"], row["session_date"]): row
        for row in exported["shadow"]
    }
    meta = rows[("META", "2026-11-27")]
    assert meta["expected_evaluations"] == 105
    assert meta["recorded_endpoint_count"] == 2
    assert meta["completed_evaluation_count"] == 2
    assert meta["missing_endpoint_count"] == 103
    assert meta["duplicate_notification_count"] == 0
    assert meta["duplicate_episode_suppressed_count"] == 1
    assert meta["latency_sample_count"] == 1
    assert meta["telegram_enqueue_latency_seconds"] == 20.0
    assert meta["telegram_enqueue_evidence_mode"] == (
        "counterfactual_no_send_same_queue"
    )

    tsla = rows[("TSLA", "2026-11-27")]
    assert tsla["unfinished_bar_trigger_count"] >= 1
    assert tsla["available_evaluations"] == 0
    absent = rows[("AAPL", "2026-11-27")]
    assert absent["telemetry_status"] == "missing_symbol_day"
    assert absent["available_evaluations"] == 0
    assert absent["missing_endpoint_count"] == 105


def test_export_mixed_actual_and_probe_receipts_remains_trusted(monkeypatch):
    clock, sessions, _enrollment = _enroll(monkeypatch)
    _append_bundle(clock, attempt_id="probe-message")
    next_end = _END + timedelta(minutes=32)
    actual = replace(
        _attempt(
            attempt_id="actual-research-message",
            window_end=next_end,
            observed_at=next_end + timedelta(seconds=30),
        ),
        telegram_enqueue_evidence_mode="actual_telegram_queue_receipt",
    )
    _append_at(
        clock,
        _endpoint(window_end=next_end),
        _evaluation(window_end=next_end),
        actual,
    )

    exported = export_p0_2m_live_shadow_rows(
        session_dates=sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    row = next(
        item for item in exported["shadow"]
        if item["symbol"] == "META" and item["session_date"] == "2026-11-27"
    )
    assert row["accepted_notification_attempt_count"] == 2
    assert row["duplicate_notification_count"] == 0
    assert row["telegram_enqueue_latency_seconds"] == 30.0
    assert row["telegram_enqueue_evidence_mode"] == "actual_telegram_queue_receipt"
    assert row["telegram_enqueue_evidence_modes"] == [
        "actual_telegram_queue_receipt", "counterfactual_no_send_same_queue",
    ]
    assert validate_p0_2m_live_shadow_export_attestation(
        exported["export_attestation"],
        expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    ) == (True, "ok")
    report = _shadow_report(
        exported["shadow"],
        shadow_dates=[date.fromisoformat(day) for day in sessions],
        universe=DEFAULT_P0_UNIVERSE_69,
    )
    assert report["invalid_enqueue_evidence_mode_count"] == 0
    # This partial trace cannot manufacture complete 20-day availability.
    assert report["data_availability"] < 0.99


def test_unaccepted_actual_receipt_cannot_supply_latency_evidence(monkeypatch):
    clock, sessions, _enrollment = _enroll(monkeypatch)
    actual = replace(
        _attempt(attempt_id="not-enqueued", accepted=False),
        telegram_enqueue_evidence_mode="actual_telegram_queue_receipt",
    )
    _append_at(clock, _endpoint(), _evaluation(), actual)

    exported = export_p0_2m_live_shadow_rows(
        session_dates=sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    row = next(
        item for item in exported["shadow"]
        if item["symbol"] == "META" and item["session_date"] == "2026-11-27"
    )
    assert row["accepted_notification_attempt_count"] == 0
    assert row["telegram_enqueue_evidence_modes"] == []
    assert row["missing_latency_window_ends"] == [
        "2026-11-27T10:00:00-05:00",
    ]
    assert "telegram_enqueue_latency_seconds" not in row


def test_export_attestation_requires_a_real_enrollment_row(monkeypatch):
    clock, _sessions, enrollment = _enroll(monkeypatch)
    fake_enrollment = replace(enrollment, enrollment_key="f" * 64)

    with pytest.raises(ValueError, match="not DB-backed"):
        repository._record_export_attestation(
            export_sha256="b" * 64,
            enrollment=fake_enrollment,
            exported_at=clock["now"],
        )

    db = repository._ensure_schema()
    with db.session_scope() as session:
        session.add(
            P02mShadowExportAttestation(
                attestation_id="a" * 64,
                export_sha256="b" * 64,
                attestation_schema_version=(
                    repository.P0_2M_SHADOW_EXPORT_ATTESTATION_SCHEMA_VERSION
                ),
                authority=repository.P0_2M_SHADOW_EXPORT_ATTESTATION_AUTHORITY,
                observation_mode=LIVE_FORWARD_SHADOW_MODE,
                universe_hash=DEFAULT_P0_UNIVERSE_HASH,
                signal_version=P0_2M_SIGNAL_VERSION,
                enrollment_key="f" * 64,
                exported_at=clock["now"],
                created_at=clock["now"],
            )
        )
    forged_receipt = {
        "schema_version": (
            repository.P0_2M_SHADOW_EXPORT_ATTESTATION_SCHEMA_VERSION
        ),
        "authority": repository.P0_2M_SHADOW_EXPORT_ATTESTATION_AUTHORITY,
        "attestation_id": "a" * 64,
        "export_sha256": "b" * 64,
        "observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
        "signal_version": P0_2M_SIGNAL_VERSION,
        "enrollment_key": "f" * 64,
        "exported_at": clock["now"].isoformat(),
    }
    assert validate_p0_2m_live_shadow_export_attestation(
        forged_receipt,
        expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    ) == (False, "live_shadow_export_enrollment_not_found")


def test_export_duplicate_count_uses_attempts_and_missing_decision_fails_closed(
    monkeypatch,
):
    clock, sessions, _enrollment = _enroll(monkeypatch)
    _append_bundle(clock, attempt_id="message-1")
    _append_bundle(
        clock,
        window_end=_END + timedelta(minutes=10),
        attempt_id="message-2",
    )
    _append_bundle(
        clock,
        window_end=_END + timedelta(minutes=35),
        attempt_id="message-3",
    )
    _append_bundle(
        clock,
        symbol="TSLA",
        window_end=_END + timedelta(minutes=12),
        qualified=True,
        would_notify=False,
        notification_decision_available=False,
    )
    exported = export_p0_2m_live_shadow_rows(
        session_dates=sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    rows = {
        (row["symbol"], row["session_date"]): row
        for row in exported["shadow"]
    }
    assert rows[("META", "2026-11-27")]["duplicate_notification_count"] == 1
    tsla = rows[("TSLA", "2026-11-27")]
    assert tsla["duplicate_notification_count"] is None
    assert tsla["missing_notification_decision_window_ends"] == [
        "2026-11-27T10:12:00-05:00"
    ]


def test_initial_data_miss_can_gain_separate_completed_stage_within_sla(
    monkeypatch,
):
    clock, sessions, _enrollment = _enroll(monkeypatch)
    first = _endpoint(
        data_available=False,
        observed_at=_END + timedelta(seconds=5),
    )
    _append_at(clock, first)
    completed_at = _END + timedelta(seconds=65)
    _append_at(
        clock,
        replace(first, observed_at=completed_at, data_available=True),
        _evaluation(observed_at=completed_at),
        _attempt(attempt_id="message-late-data", observed_at=completed_at),
    )
    exported = export_p0_2m_live_shadow_rows(
        session_dates=sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    row = next(
        item
        for item in exported["shadow"]
        if item["symbol"] == "META"
        and item["session_date"] == "2026-11-27"
    )
    assert row["recorded_endpoint_count"] == 1
    assert row["available_evaluations"] == 1
    assert row["initial_data_unavailable_window_ends"] == [
        "2026-11-27T10:00:00-05:00"
    ]
    assert row["telegram_enqueue_latency_seconds"] == 65.0


def test_repository_rejects_nonlive_backfill_and_nonfrozen_export(monkeypatch):
    clock, sessions, _enrollment = _enroll(monkeypatch)
    clock["now"] = _END + timedelta(minutes=10)
    with pytest.raises(ValueError, match="live-forward write"):
        append_p0_2m_shadow_observations([_endpoint()])
    clock["now"] = _END + timedelta(seconds=20)
    with pytest.raises(ValueError, match="too far"):
        append_p0_2m_shadow_observations(
            [
                _endpoint(
                    window_end=_END + timedelta(minutes=10),
                    observed_at=clock["now"],
                )
            ]
        )
    with pytest.raises(ValueError):
        export_p0_2m_live_shadow_rows(
            session_dates=list(reversed(sessions)),
            symbols=DEFAULT_P0_UNIVERSE_69,
            universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        )


@pytest.mark.parametrize("clock_time", [time(9, 30), time(10, 0)])
def test_enrollment_during_rth_waits_until_the_session_has_closed(
    monkeypatch, clock_time
):
    during_rth = datetime.combine(date(2026, 11, 27), clock_time, ET)
    monkeypatch.setattr(repository, "_utc_now", lambda: during_rth.astimezone(UTC))

    with pytest.raises(ValueError, match="waits for the current XNYS session"):
        ensure_p0_2m_live_shadow_enrollment(
            symbols=DEFAULT_P0_UNIVERSE_69,
            universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            enrollment_started_at=during_rth,
        )


def test_rth_restart_recovers_an_existing_frozen_enrollment(monkeypatch):
    clock, sessions, original = _enroll(monkeypatch)
    during_rth = datetime.fromisoformat(f"{sessions[0]}T10:00:00").replace(
        tzinfo=ET
    )
    clock["now"] = during_rth.astimezone(UTC)

    recovered = ensure_p0_2m_live_shadow_enrollment(
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        enrollment_started_at=during_rth,
    )

    assert recovered.sessions == original.sessions
    assert recovered.enrolled_at == original.enrolled_at


def test_failed_run_is_retained_and_explicit_rerun_is_isolated(monkeypatch):
    clock, sessions, original = _enroll(monkeypatch)
    calendar = xcals.get_calendar("XNYS")
    first_day = date.fromisoformat(sessions[0])
    first_close = calendar.session_close(first_day).to_pydatetime().astimezone(ET)
    restarted_at = (first_close + timedelta(minutes=5)).astimezone(UTC)
    clock["now"] = restarted_at

    restarted = start_new_p0_2m_live_shadow_run(
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        restart_request_id="opend-outage-2026-11",
        restart_reason="OpenD outage made the first run incomplete",
        enrollment_started_at=restarted_at,
    )

    assert restarted.enrollment_key != original.enrollment_key
    assert restarted.supersedes_enrollment_key == original.enrollment_key
    assert restarted.sessions[0] == sessions[1]

    # The same incident request is idempotent even when retried during RTH.
    retry_at = datetime.combine(
        date.fromisoformat(restarted.sessions[0]), time(10), ET
    ).astimezone(UTC)
    clock["now"] = retry_at
    recovered = ensure_p0_2m_live_shadow_enrollment(
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        enrollment_started_at=retry_at,
    )
    assert recovered.enrollment_key == restarted.enrollment_key
    retried = start_new_p0_2m_live_shadow_run(
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        restart_request_id="opend-outage-2026-11",
        restart_reason="OpenD outage made the first run incomplete",
        enrollment_started_at=retry_at,
    )
    assert retried.enrollment_key == restarted.enrollment_key

    with pytest.raises(ValueError, match="waits for the current XNYS session"):
        start_new_p0_2m_live_shadow_run(
            symbols=DEFAULT_P0_UNIVERSE_69,
            universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            restart_request_id="hindsight-second-run",
            restart_reason="must not freeze after seeing a partial RTH day",
            enrollment_started_at=retry_at,
        )

    overlap_end = retry_at
    observed_at = overlap_end + timedelta(seconds=20)
    old_fact = replace(
        _endpoint(
            data_available=False,
            window_end=overlap_end,
            observed_at=observed_at,
        ),
        enrollment_key=original.enrollment_key,
    )
    new_fact = _endpoint(
        data_available=True,
        window_end=overlap_end,
        observed_at=observed_at,
    )
    _append_at(clock, old_fact)
    _append_at(clock, new_fact)

    old_rows = list_p0_2m_shadow_observations(
        et_dates=[restarted.sessions[0]],
        symbols=["META"],
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        enrollment_key=original.enrollment_key,
    )
    new_rows = list_p0_2m_shadow_observations(
        et_dates=[restarted.sessions[0]],
        symbols=["META"],
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        enrollment_key=restarted.enrollment_key,
    )
    assert len(old_rows) == len(new_rows) == 1
    assert old_rows[0].data_available is False
    assert new_rows[0].data_available is True
    assert old_rows[0].observation_key != new_rows[0].observation_key

    exported = export_p0_2m_live_shadow_rows(
        session_dates=restarted.sessions,
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        enrollment_key=restarted.enrollment_key,
    )
    assert exported["enrollment"]["enrollment_key"] == restarted.enrollment_key
    assert exported["enrollment"]["supersedes_enrollment_key"] == (
        original.enrollment_key
    )
    meta = next(
        row
        for row in exported["shadow"]
        if row["symbol"] == "META"
        and row["session_date"] == restarted.sessions[0]
    )
    assert meta["recorded_endpoint_count"] == 1
    assert meta["initial_data_unavailable_window_ends"] == []


def test_same_window_replacement_is_explicit_preserved_and_idempotent(monkeypatch):
    clock, _sessions, original = _enroll(monkeypatch)
    clock["now"] += timedelta(minutes=5)
    arguments = dict(
        symbols=DEFAULT_P0_UNIVERSE_69, universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        restart_request_id="preopen-frozen-input-repair",
        restart_reason="replace incorrect frozen input before any observations",
        enrollment_started_at=clock["now"],
    )
    replacement = start_new_p0_2m_live_shadow_run(**arguments)
    assert replacement.sessions == original.sessions
    assert replacement.enrollment_key != original.enrollment_key
    assert replacement.supersedes_enrollment_key == original.enrollment_key
    assert start_new_p0_2m_live_shadow_run(**arguments) == replacement
    recovered = ensure_p0_2m_live_shadow_enrollment(
        symbols=DEFAULT_P0_UNIVERSE_69, universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        enrollment_started_at=clock["now"],
    )
    assert recovered.enrollment_key == replacement.enrollment_key
    with pytest.raises(repository.P02mShadowConflictError, match="different restart_reason"):
        start_new_p0_2m_live_shadow_run(**{**arguments, "restart_reason": "changed reason"})
    with pytest.raises(ValueError, match="restart_reason"):
        start_new_p0_2m_live_shadow_run(**{**arguments, "restart_reason": ""})
    with get_db().session_scope() as session:
        assert session.execute(text(
            "SELECT COUNT(*) FROM journal_v2_p0_2m_shadow_enrollments"
        )).scalar_one() == 2
        with pytest.raises(Exception, match="append-only"):
            session.execute(text("DELETE FROM journal_v2_p0_2m_shadow_enrollments"))


def test_same_window_replacement_rejects_any_observations(monkeypatch):
    clock, sessions, original = _enroll(monkeypatch)
    end = datetime.combine(date.fromisoformat(sessions[0]), time(9, 32), ET)
    _append_at(clock, _endpoint(window_end=end))
    # Even a backdated test clock cannot make observed evidence replaceable.
    clock["now"] = original.enrolled_at + timedelta(minutes=5)
    with pytest.raises(repository.P02mShadowConflictError, match="zero observations"):
        start_new_p0_2m_live_shadow_run(
            symbols=DEFAULT_P0_UNIVERSE_69, universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            restart_request_id="cannot-erase-observed-run", restart_reason="repair",
            enrollment_started_at=clock["now"],
        )


def test_concurrent_same_window_requests_form_one_chain(monkeypatch):
    clock, _sessions, original = _enroll(monkeypatch)
    clock["now"] += timedelta(minutes=5)

    def register(index):
        return start_new_p0_2m_live_shadow_run(
            symbols=DEFAULT_P0_UNIVERSE_69, universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            restart_request_id=f"concurrent-{index}", restart_reason="preopen repair",
            enrollment_started_at=clock["now"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, (1, 2)))
    keys = {item.enrollment_key for item in results}
    predecessors = {item.supersedes_enrollment_key for item in results}
    assert len(keys) == 2
    assert original.enrollment_key in predecessors
    assert len(predecessors & keys) == 1
    assert register(1).enrollment_key == results[0].enrollment_key


def test_preopen_request_cannot_register_after_waiting_past_open(monkeypatch):
    clock, sessions, _ = _enroll(monkeypatch)
    opening = xcals.get_calendar("XNYS").session_open(sessions[0]).to_pydatetime()
    requested_at = opening - timedelta(seconds=10)
    clock["now"] = requested_at
    original_loader = repository._load_enrollment

    def delayed_loader(*args, **kwargs):
        result = original_loader(*args, **kwargs)
        clock["now"] = opening + timedelta(seconds=1)
        return result

    monkeypatch.setattr(repository, "_load_enrollment", delayed_loader)
    with pytest.raises(ValueError, match="created_at follows first open"):
        start_new_p0_2m_live_shadow_run(
            symbols=DEFAULT_P0_UNIVERSE_69, universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            restart_request_id="waited-past-open", restart_reason="repair",
            enrollment_started_at=requested_at,
        )


@pytest.mark.parametrize("inject_failure", [False, True])
def test_run_aware_schema_upgrade_keeps_rows_null_prices_and_guards(
    isolated_sqlite, monkeypatch, inject_failure
):
    metadata = MetaData()
    legacy_enrollment = P02mShadowEnrollment.__table__.to_metadata(metadata)
    legacy_enrollment.append_constraint(UniqueConstraint(
        "universe_hash", "signal_version", "start_session", "end_session",
        name="uq_jv2_p0_2m_shadow_enrollment_window",
    ))
    legacy_observation = Table(
        P02mShadowObservation.__tablename__, metadata,
        *[column._copy() for column in P02mShadowObservation.__table__.columns
          if column.name != "window_close"],
        UniqueConstraint("observation_key"),
    )
    sessions = _twenty_sessions_ending(date(2026, 11, 27))
    enrolled_at = datetime.combine(date.fromisoformat(sessions[0]), time(9), ET)
    engine = create_engine(f"sqlite:///{isolated_sqlite}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(legacy_enrollment.insert().values(
            id=1, enrollment_key="a" * 64, run_request_id="original",
            restart_reason="initial_enrollment", supersedes_enrollment_key=None,
            observation_mode=LIVE_FORWARD_SHADOW_MODE,
            universe_hash=DEFAULT_P0_UNIVERSE_HASH, signal_version=P0_2M_SIGNAL_VERSION,
            universe_json=json.dumps(list(DEFAULT_P0_UNIVERSE_69)),
            sessions_json=json.dumps(sessions), start_session=sessions[0], end_session=sessions[-1],
            enrolled_at=enrolled_at.astimezone(UTC), created_at=enrolled_at.astimezone(UTC),
        ))
        connection.execute(legacy_observation.insert().values(
            id=1, enrollment_key="a" * 64, observation_key="b" * 64,
            record_kind="completed_evaluation", notification_attempt_id="first",
            observation_mode=LIVE_FORWARD_SHADOW_MODE, symbol="META", et_date="2026-11-27",
            window_end=_END, universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            signal_version=P0_2M_SIGNAL_VERSION, session_scope="regular",
            observed_at=_END, max_component_end=_END, data_available=True,
            evaluation_available=True, evaluation_state="qualified", qualified=True,
            would_notify=False, notification_decision_available=True,
            duplicate_episode_suppressed=False, direction="down", source="MoomooFetcher",
            data_quality_json='{"status":"complete"}', unfinished_bar_trigger=False,
            created_at=_END,
        ))
        for table_name in (legacy_enrollment.name, legacy_observation.name):
            for operation in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(ledger_guard_trigger_ddl(table_name, operation))
    engine.dispose()
    monkeypatch.setattr(repository, "_READY_ENGINES", set())
    if inject_failure:
        def fail_after_rebuild(_index):
            raise RuntimeError("injected failure after enrollment rebuild")

        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(repository, "CreateIndex", fail_after_rebuild)
            with pytest.raises(RuntimeError, match="after enrollment rebuild"):
                repository._ensure_schema()
        with get_db()._engine.connect() as connection:
            # Both DDL changes must roll back; old audit guards stay active.
            columns = {row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(journal_v2_p0_2m_shadow_observations)"
            )}
            assert "window_close" not in columns
            with pytest.raises(Exception, match="append-only"):
                connection.execute(text("DELETE FROM journal_v2_p0_2m_shadow_enrollments"))
            assert not connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_migration'"
            )).all()
    db = repository._ensure_schema()
    repository._migrate_shadow_outcome_schema(db._engine)
    with db._engine.connect() as connection:
        assert connection.execute(text(
            "SELECT window_close FROM journal_v2_p0_2m_shadow_observations WHERE id=1"
        )).scalar_one() is None
        assert connection.execute(text(
            "SELECT run_request_id FROM journal_v2_p0_2m_shadow_enrollments WHERE id=1"
        )).scalar_one() == "original"
        assert not connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_migration'"
        )).all()
        for statement in (
            "UPDATE journal_v2_p0_2m_shadow_observations SET window_close=999",
            "DELETE FROM journal_v2_p0_2m_shadow_enrollments",
        ):
            with pytest.raises(Exception, match="append-only"):
                connection.execute(text(statement))
        unique_columns = []
        for index in connection.exec_driver_sql(
            "PRAGMA index_list(journal_v2_p0_2m_shadow_enrollments)"
        ).all():
            if index[2]:
                unique_columns.append(tuple(row[2] for row in connection.exec_driver_sql(
                    f'PRAGMA index_info("{index[1]}")'
                ).all()))
        assert ("universe_hash", "signal_version", "start_session", "end_session") not in unique_columns
        assert ("enrollment_key",) in unique_columns
        assert ("universe_hash", "signal_version", "run_request_id") in unique_columns


def test_v1_schema_migration_retains_and_guards_original_audit(
    isolated_sqlite, monkeypatch
):
    metadata, legacy_enrollment, legacy_observation = _v1_shadow_metadata()

    sessions = _twenty_sessions_ending(date(2026, 11, 27))
    first_day = date.fromisoformat(sessions[0])
    enrolled_at = datetime.combine(first_day, time(9), ET).astimezone(UTC)
    window_end = datetime.combine(first_day, time(10), ET).astimezone(UTC)
    old_key = "a" * 64
    old_observation_identity = {
        "observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "symbol": "META",
        "et_date": sessions[0],
        "window_end": window_end.isoformat(),
        "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
        "signal_version": P0_2M_SIGNAL_VERSION,
        "record_kind": "endpoint_observation",
        "notification_attempt_id": "first",
    }
    old_observation_key = hashlib.sha256(
        repository._canonical_json(old_observation_identity).encode()
    ).hexdigest()
    old_engine = create_engine(f"sqlite:///{isolated_sqlite}")
    metadata.create_all(old_engine)
    with old_engine.begin() as connection:
        connection.execute(
            legacy_enrollment.insert().values(
                id=1,
                enrollment_key=old_key,
                observation_mode=LIVE_FORWARD_SHADOW_MODE,
                universe_hash=DEFAULT_P0_UNIVERSE_HASH,
                signal_version=P0_2M_SIGNAL_VERSION,
                universe_json=json.dumps(list(DEFAULT_P0_UNIVERSE_69)),
                sessions_json=json.dumps(sessions),
                start_session=sessions[0],
                end_session=sessions[-1],
                enrolled_at=enrolled_at,
                created_at=enrolled_at,
            )
        )
        connection.execute(
            legacy_observation.insert().values(
                id=1,
                observation_key=old_observation_key,
                record_kind="endpoint_observation",
                notification_attempt_id="first",
                observation_mode=LIVE_FORWARD_SHADOW_MODE,
                symbol="META",
                et_date=sessions[0],
                window_end=window_end,
                universe_hash=DEFAULT_P0_UNIVERSE_HASH,
                signal_version=P0_2M_SIGNAL_VERSION,
                session_scope="regular",
                observed_at=window_end + timedelta(seconds=20),
                max_component_end=window_end,
                data_available=False,
                evaluation_available=False,
                evaluation_state="endpoint_observed",
                qualified=False,
                would_notify=False,
                notification_decision_available=False,
                duplicate_episode_suppressed=False,
                direction="down",
                source="MoomooFetcher",
                unavailable_reason="live_1m_subscription_unavailable",
                data_quality_json="{}",
                unfinished_bar_trigger=False,
                created_at=window_end + timedelta(seconds=20),
            )
        )
        for table_name in (
            P02mShadowEnrollment.__tablename__,
            P02mShadowObservation.__tablename__,
        ):
            for operation in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    ledger_guard_trigger_ddl(table_name, operation)
                )
    old_engine.dispose()

    monkeypatch.setattr(repository, "_READY_ENGINES", set())
    db = repository._ensure_schema()
    # Re-entering from the retained v1 tables is idempotent, which is also the
    # recovery path after a process stops between destination creation/copy.
    repository._migrate_shadow_run_identity_schema(db._engine)
    with db._engine.connect() as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        assert repository._LEGACY_ENROLLMENT_TABLE in tables
        assert repository._LEGACY_OBSERVATION_TABLE in tables
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM "
                + repository._LEGACY_ENROLLMENT_TABLE
            )
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM "
                + repository._LEGACY_OBSERVATION_TABLE
            )
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT enrollment_key FROM "
                "journal_v2_p0_2m_shadow_observations"
            )
        ).scalar_one() == old_key

        for statement in (
            "DELETE FROM " + repository._LEGACY_ENROLLMENT_TABLE,
            "UPDATE "
            + repository._LEGACY_OBSERVATION_TABLE
            + " SET qualified = 1",
        ):
            with pytest.raises(Exception, match="append-only"):
                connection.execute(text(statement))

    observed_at = window_end + timedelta(seconds=20)
    monkeypatch.setattr(repository, "_utc_now", lambda: observed_at)
    retry = replace(
        _endpoint(
            data_available=False,
            window_end=window_end,
            observed_at=observed_at,
        ),
        enrollment_key=old_key,
    )
    assert append_p0_2m_shadow_observations([retry]) == (
        repository.P02mShadowAppendResult(0, 1)
    )

    # A conflicting destination id must be detected before the schema is
    # considered ready; retained v1 evidence remains the authority.
    with db._engine.begin() as connection:
        trigger_name = ledger_guard_trigger_name(
            P02mShadowObservation.__tablename__, "UPDATE"
        )
        connection.exec_driver_sql(f"DROP TRIGGER {trigger_name}")
        connection.execute(
            text(
                "UPDATE journal_v2_p0_2m_shadow_observations "
                "SET enrollment_key='cccccccccccccccccccccccccccccccc"
                "cccccccccccccccccccccccccccccccc' WHERE id=1"
            )
        )
        connection.exec_driver_sql(
            ledger_guard_trigger_ddl(
                P02mShadowObservation.__tablename__, "UPDATE"
            )
        )
    with pytest.raises(RuntimeError, match="copy mismatch"):
        repository._migrate_shadow_run_identity_schema(db._engine)


def test_v1_migration_rename_failure_rolls_back_guard_drop(
    isolated_sqlite, monkeypatch
):
    metadata, legacy_enrollment, _legacy_observation = _v1_shadow_metadata()
    sessions = _twenty_sessions_ending(date(2026, 11, 27))
    first_day = date.fromisoformat(sessions[0])
    enrolled_at = datetime.combine(first_day, time(9), ET).astimezone(UTC)
    old_engine = create_engine(f"sqlite:///{isolated_sqlite}")
    metadata.create_all(old_engine)
    with old_engine.begin() as connection:
        connection.execute(
            legacy_enrollment.insert().values(
                id=1,
                enrollment_key="a" * 64,
                observation_mode=LIVE_FORWARD_SHADOW_MODE,
                universe_hash=DEFAULT_P0_UNIVERSE_HASH,
                signal_version=P0_2M_SIGNAL_VERSION,
                universe_json=json.dumps(list(DEFAULT_P0_UNIVERSE_69)),
                sessions_json=json.dumps(sessions),
                start_session=sessions[0],
                end_session=sessions[-1],
                enrolled_at=enrolled_at,
                created_at=enrolled_at,
            )
        )
        for table_name in (
            P02mShadowEnrollment.__tablename__,
            P02mShadowObservation.__tablename__,
        ):
            for operation in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    ledger_guard_trigger_ddl(table_name, operation)
                )
    old_engine.dispose()

    import src.journal.ledger.repository as ledger_repository

    original_guard_ddl = ledger_repository.ledger_guard_trigger_ddl

    def fail_after_second_rename(table_name, operation):
        if table_name == repository._LEGACY_OBSERVATION_TABLE:
            raise RuntimeError("injected failure after second rename")
        return original_guard_ddl(table_name, operation)

    monkeypatch.setattr(
        ledger_repository,
        "ledger_guard_trigger_ddl",
        fail_after_second_rename,
    )
    monkeypatch.setattr(repository, "_READY_ENGINES", set())
    with pytest.raises(RuntimeError, match="injected failure"):
        repository._ensure_schema()

    db = get_db()
    with db._engine.connect() as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        assert P02mShadowEnrollment.__tablename__ in tables
        assert P02mShadowObservation.__tablename__ in tables
        assert repository._LEGACY_ENROLLMENT_TABLE not in tables
        assert repository._LEGACY_OBSERVATION_TABLE not in tables
        trigger_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name IN (:enrollment_table, :observation_table)"
            ),
            {
                "enrollment_table": P02mShadowEnrollment.__tablename__,
                "observation_table": P02mShadowObservation.__tablename__,
            },
        ).scalar_one()
        assert trigger_count == 4
        with pytest.raises(Exception, match="append-only"):
            connection.execute(
                text(
                    "UPDATE journal_v2_p0_2m_shadow_enrollments "
                    "SET start_session='2099-01-01' WHERE id=1"
                )
            )
