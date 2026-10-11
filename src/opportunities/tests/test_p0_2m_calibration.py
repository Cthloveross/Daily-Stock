from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import exchange_calendars as xcals
import pytest
from sqlalchemy import text

from scripts.calibrate_p0_2m import (
    load_input,
    main as calibrate_main,
    merge_live_shadow_input,
)
from src.journal.ledger import p0_2m_shadow_repository as shadow_repository
from src.journal.ledger.p0_2m_shadow_models import P02mShadowEnrollment
from src.journal.ledger.p0_2m_shadow_repository import (
    LIVE_FORWARD_SHADOW_MODE,
    P0_2M_SHADOW_EXPORT_SCHEMA_VERSION,
    P02mShadowEnrollmentRecord,
    attest_p0_2m_activation_artifact,
    compute_p0_2m_live_shadow_export_sha256,
)
from src.opportunities.p0_2m_calibration import (
    DEFAULT_ASSET_CLASS_BY_SYMBOL,
    DEFAULT_P0_UNIVERSE_69,
    DEFAULT_P0_UNIVERSE_HASH,
    FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS,
    FROZEN_SIGNAL_THRESHOLDS,
    P0_2M_ATR_ONLY_COMPARATOR_BASIS,
    P0_2M_ATR_ONLY_COMPARATOR_VERSION,
    P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
    REQUIRED_ACTIVATION_GATES,
    _dedupe_events,
    _simulate_budget,
    _outcome_status,
    calibrate_p0_2m,
    compute_live_shadow_binding_sha256,
    compute_universe_hash,
    split_historical_sessions,
    validated_mobile_eligible_symbols,
    validate_activation_artifact,
)
from src.opportunities.p0_2m_signal import P0_2M_SIGNAL_VERSION
from src.storage import get_db


def test_deduplication_keeps_last_accepted_crossing_per_symbol_direction():
    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    rows = [
        {"symbol": symbol, "direction": direction,
         "window_end": (start + timedelta(minutes=minute)).isoformat()}
        for symbol, direction, minute in (
            ("TSLA", "up", 0), ("TSLA", "up", 28),
            ("META", "up", 27), ("TSLA", "down", 29),
            ("TSLA", "up", 30), ("TSLA", "up", 32),
            ("TSLA", "up", 60),
        )
    ]
    assert _dedupe_events(list(reversed(rows))) == [rows[i] for i in (0, 2, 3, 4, 6)]


@pytest.fixture(autouse=True)
def isolated_attestation_db(tmp_path, monkeypatch):
    """Keep exporter receipts out of the developer's configured journal DB."""

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "p0_calibration_test.db"))
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _sessions(count: int = 200):
    calendar = xcals.get_calendar("XNYS")
    values = [
        stamp.date()
        for stamp in calendar.sessions_in_range("2025-01-02", "2026-06-30")
    ]
    assert len(values) >= count
    return values[:count]


def _rth_2m_evaluations(day) -> int:
    calendar = xcals.get_calendar("XNYS")
    seconds = (calendar.session_close(day) - calendar.session_open(day)).total_seconds()
    assert seconds % 120 == 0
    return int(seconds // 120)


def _rehash(artifact: dict) -> dict:
    artifact.pop("artifact_sha256", None)
    canonical = json.dumps(
        artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    artifact["artifact_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return artifact


def _attest(artifact: dict) -> dict:
    attest_p0_2m_activation_artifact(artifact)
    return artifact


def _passing_payload() -> dict:
    sessions = _sessions(230)
    calendar = xcals.get_calendar("XNYS")
    shadow_sessions = [
        stamp.date()
        for stamp in calendar.sessions_in_range("2025-11-03", "2025-12-01")
    ][:20]
    assert len(shadow_sessions) == 20
    assert any(_rth_2m_evaluations(day) == 105 for day in shadow_sessions)
    shadow_start_index = sessions.index(shadow_sessions[0])
    historical = sessions[shadow_start_index - 180 : shadow_start_index]
    assert len(historical) == 180
    baseline = historical[:60]
    holdout = historical[-40:]

    coverage = [
        {
            "symbol": symbol,
            "session_date": day.isoformat(),
            "complete_minutes": 390,
            "eligible_minutes": 390,
        }
        for symbol in DEFAULT_P0_UNIVERSE_69
        for day in baseline
    ]
    events = []
    comparator = []
    for index, day in enumerate(holdout):
        symbol = DEFAULT_P0_UNIVERSE_69[index % len(DEFAULT_P0_UNIVERSE_69)]
        common = {
            "symbol": symbol,
            "session_date": day.isoformat(),
            "window_end": f"{day.isoformat()}T10:00:00-04:00",
            "session_scope": "regular",
            "direction": "up",
        }
        events.append(
            {
                **common,
                "state": "qualified",
                "alert_qualified": True,
                "signal_version": P0_2M_SIGNAL_VERSION,
                "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
                "data_quality": {"status": "complete", "issues": []},
                "source": "MoomooFetcher",
                "failed_checks": [],
                "thresholds": dict(FROZEN_SIGNAL_THRESHOLDS),
                "baseline_count": 60,
                "baseline_last_date": historical[139 + index].isoformat(),
                "impulse_8m_log": 0.006,
                "displacement_18m_percent": 0.6,
                "price_percentile": 0.95,
                "rvol_8m": 1.5,
                "displacement_atr": 0.5,
                "direction_adjusted_return_20m": 0.02,
            }
        )
        comparator.append(
            {
                **common,
                "state": "qualified",
                "qualified": True,
                "comparator_version": P0_2M_ATR_ONLY_COMPARATOR_VERSION,
                "comparator_basis": P0_2M_ATR_ONLY_COMPARATOR_BASIS,
                "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
                "source": "MoomooFetcher",
                "data_quality": {"status": "complete", "issues": []},
                "failed_checks": [],
                "thresholds": dict(FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS),
                "displacement_atr": 0.5,
                "direction_adjusted_return_20m": 0.01,
            }
        )
    shadow = []
    for day in shadow_sessions:
        expected_evaluations = _rth_2m_evaluations(day)
        for symbol in DEFAULT_P0_UNIVERSE_69:
            shadow.append(
                {
                    "symbol": symbol,
                    "session_date": day.isoformat(),
                    "session_scope": "regular",
                    "expected_evaluations": expected_evaluations,
                    "available_evaluations": expected_evaluations,
                    "duplicate_notification_count": 0,
                    "unfinished_bar_trigger_count": 0,
                    "telegram_enqueue_evidence_mode": (
                        "counterfactual_no_send_same_queue"
                    ),
                    "would_notify": False,
                }
            )
    shadow[0].update(
        would_notify=True,
        telegram_enqueue_latency_seconds=20.0,
    )
    enrolled_at = calendar.session_close(holdout[-1]).to_pydatetime() + timedelta(
        minutes=30
    )
    created_at = enrolled_at + timedelta(seconds=5)
    assert created_at <= calendar.session_open(shadow_sessions[0]).to_pydatetime()
    return {
        "schema_version": P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
        "universe": list(DEFAULT_P0_UNIVERSE_69),
        "historical_sessions": [day.isoformat() for day in historical],
        "shadow_sessions": [day.isoformat() for day in shadow_sessions],
        "shadow_observation_mode": "live_forward_shadow",
        "shadow_enrollment": {
            "observation_mode": LIVE_FORWARD_SHADOW_MODE,
            "enrollment_key": "e" * 64,
            "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
            "signal_version": P0_2M_SIGNAL_VERSION,
            "universe": list(DEFAULT_P0_UNIVERSE_69),
            "sessions": [day.isoformat() for day in shadow_sessions],
            "enrolled_at": enrolled_at.isoformat(),
            "created_at": created_at.isoformat(),
        },
        "coverage": coverage,
        "events": events,
        "atr_only_events": comparator,
        "shadow": shadow,
    }


def _live_shadow_export(
    payload: dict | None = None, *, legacy_without_run_keys: bool = False
) -> dict:
    source = payload or _passing_payload()
    rows = []
    for row in source["shadow"]:
        exported_row = {
            **row,
            "observation_mode": LIVE_FORWARD_SHADOW_MODE,
            "signal_version": P0_2M_SIGNAL_VERSION,
            "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
        }
        if not legacy_without_run_keys:
            exported_row["enrollment_key"] = "e" * 64
        else:
            exported_row.pop("enrollment_key", None)
        rows.append(exported_row)
    exported_at = datetime(2025, 12, 2, 1, 0, tzinfo=timezone.utc)
    enrollment_payload = deepcopy(source["shadow_enrollment"])
    if legacy_without_run_keys:
        enrollment_payload.pop("enrollment_key", None)
        enrollment_payload.pop("run_request_id", None)
        enrollment_payload.pop("restart_reason", None)
        enrollment_payload.pop("supersedes_enrollment_key", None)
    result = {
        "schema_version": P0_2M_SHADOW_EXPORT_SCHEMA_VERSION,
        "shadow_observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "signal_version": P0_2M_SIGNAL_VERSION,
        "universe": list(DEFAULT_P0_UNIVERSE_69),
        "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
        "shadow_sessions": list(source["shadow_sessions"]),
        "enrollment": enrollment_payload,
        "exported_at": exported_at.isoformat(),
        "shadow": rows,
    }
    enrollment = source["shadow_enrollment"]
    enrolled_at = datetime.fromisoformat(enrollment["enrolled_at"])
    created_at = datetime.fromisoformat(enrollment["created_at"])
    run_request_id = str(enrollment.get("run_request_id") or "test-initial")
    restart_reason = str(enrollment.get("restart_reason") or "test enrollment")
    db = shadow_repository._ensure_schema()
    with db.session_scope() as session:
        session.add(
            P02mShadowEnrollment(
                enrollment_key="e" * 64,
                run_request_id=run_request_id,
                restart_reason=restart_reason,
                supersedes_enrollment_key=enrollment.get(
                    "supersedes_enrollment_key"
                ),
                observation_mode=LIVE_FORWARD_SHADOW_MODE,
                universe_hash=DEFAULT_P0_UNIVERSE_HASH,
                signal_version=P0_2M_SIGNAL_VERSION,
                universe_json=json.dumps(list(DEFAULT_P0_UNIVERSE_69)),
                sessions_json=json.dumps(list(source["shadow_sessions"])),
                start_session=source["shadow_sessions"][0],
                end_session=source["shadow_sessions"][-1],
                enrolled_at=enrolled_at,
                created_at=created_at,
            )
        )
    result["export_attestation"] = shadow_repository._record_export_attestation(
        export_sha256=compute_p0_2m_live_shadow_export_sha256(result),
        enrollment=P02mShadowEnrollmentRecord(
            universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            signal_version=P0_2M_SIGNAL_VERSION,
            universe=tuple(DEFAULT_P0_UNIVERSE_69),
            sessions=tuple(source["shadow_sessions"]),
            enrolled_at=enrolled_at,
            created_at=created_at,
            enrollment_key="e" * 64,
            run_request_id=run_request_id,
            restart_reason=restart_reason,
            supersedes_enrollment_key=enrollment.get(
                "supersedes_enrollment_key"
            ),
        ),
        exported_at=exported_at,
    )
    return result


def _trusted_passing_payload(payload: dict | None = None) -> dict:
    complete = payload or _passing_payload()
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []
    return merge_live_shadow_input(
        base,
        _live_shadow_export(complete),
        source_sha256="a" * 64,
    )


def test_universe_hash_is_normalized_and_order_independent():
    reversed_lower = [symbol.lower() for symbol in reversed(DEFAULT_P0_UNIVERSE_69)]
    assert compute_universe_hash(reversed_lower) == DEFAULT_P0_UNIVERSE_HASH
    assert len(DEFAULT_P0_UNIVERSE_69) == 69


def test_split_uses_most_recent_180_sessions_without_overlap():
    sessions = _sessions(205)
    split = split_historical_sessions([day.isoformat() for day in sessions[:205]])
    assert len(split["baseline"]) == 60
    assert len(split["development"]) == 80
    assert len(split["holdout"]) == 40
    assert split["ignored_older_session_count"] == 25
    selected = split["baseline"] + split["development"] + split["holdout"]
    assert selected == [day.isoformat() for day in sessions[25:205]]
    assert len(set(selected)) == 180


def test_all_fixed_gates_can_pass_and_runtime_validation_needs_no_recompute(tmp_path):
    artifact = calibrate_p0_2m(
        _trusted_passing_payload(),
        n_boot=200,
        seed=7,
        generated_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    assert artifact["activation_ready"] is True
    assert all(result["passed"] is True for result in artifact["gate_results"].values())
    assert set(artifact["gate_results"]) == set(REQUIRED_ACTIVATION_GATES)
    assert artifact["mobile_eligible_symbols"] == list(DEFAULT_P0_UNIVERSE_69)
    assert artifact["holdout"]["deduped_event_count"] == 40
    assert 3 <= artifact["holdout"]["pre_budget_weekly_median"] <= 7
    assert artifact["holdout"]["budget"]["suppression_rate"] == 0
    assert artifact["holdout"]["direction_adjusted_return_20m_median"] > 0
    assert artifact["holdout"]["atr_only_comparator"]["ci90_low"] >= 0
    assert set(artifact["strata"]) == set(DEFAULT_ASSET_CLASS_BY_SYMBOL.values())
    assert all(row["state"] == "validated" for row in artifact["strata"].values())
    assert artifact["shadow"]["expected_symbol_session_rows"] == 69 * 20
    assert artifact["shadow"]["valid_symbol_session_rows"] == 69 * 20
    assert artifact["shadow"]["expected_evaluations_by_session"]["2025-11-28"] == 105
    assert artifact["shadow"]["expected_evaluations"] == 69 * (19 * 195 + 105)

    _attest(artifact)
    path = tmp_path / "activation.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    assert validate_activation_artifact(
        path,
        expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        expected_signal_version=P0_2M_SIGNAL_VERSION,
    ) == (True, "ok")


def test_artifact_never_certifies_extended_sessions_or_profitability():
    artifact = calibrate_p0_2m(_trusted_passing_payload(), n_boot=20)
    assert artifact["validated_session_scopes"] == ["regular"]
    assert artifact["economic_edge_verified"] is False
    assert artifact["historical_independence"] == "not_established_preselected_cases"
    artifact["validated_session_scopes"].append("premarket")
    _rehash(artifact)
    assert validate_activation_artifact(
        artifact, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "validation_scope_mismatch")


def test_negative_asset_class_is_not_mobile_eligible():
    payload = _trusted_passing_payload()
    for row in payload["events"]:
        if DEFAULT_ASSET_CLASS_BY_SYMBOL[row["symbol"]] == "leveraged_inverse_etf":
            row["direction_adjusted_return_20m"] = -0.01
    artifact = calibrate_p0_2m(payload, n_boot=20)
    assert artifact["strata"]["leveraged_inverse_etf"]["state"] == "shadow"
    assert all(DEFAULT_ASSET_CLASS_BY_SYMBOL[symbol] != "leveraged_inverse_etf"
               for symbol in artifact["mobile_eligible_symbols"])


def test_self_labelled_shadow_without_db_export_receipt_cannot_activate():
    artifact = calibrate_p0_2m(_passing_payload(), n_boot=20, seed=3)

    assert artifact["activation_ready"] is False
    provenance_gate = artifact["gate_results"]["trusted_live_shadow_provenance"]
    assert provenance_gate["passed"] is False
    assert "live_shadow_merge_provenance_missing" in provenance_gate["actual"][
        "issues"
    ]
    assert validate_activation_artifact(
        artifact,
        expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH,
        expected_signal_version=P0_2M_SIGNAL_VERSION,
    ) == (False, "live_shadow_provenance_missing")


def test_live_shadow_export_merges_into_locked_base_and_can_pass_gates():
    complete = _passing_payload()
    live = _live_shadow_export(complete)
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []

    merged = merge_live_shadow_input(base, live, source_sha256="a" * 64)

    assert base["shadow"] == []
    assert merged["shadow_observation_mode"] == LIVE_FORWARD_SHADOW_MODE
    assert merged["shadow_sessions"] == complete["shadow_sessions"]
    assert merged["shadow_enrollment"] == live["enrollment"]
    assert merged["shadow"] == live["shadow"]
    assert merged["producer_metadata"]["derivation_errors"] == []
    assert merged["producer_metadata"]["live_shadow_merge"]["source_sha256"] == (
        "a" * 64
    )
    artifact = calibrate_p0_2m(merged, n_boot=100)
    assert artifact["gate_results"]["forward_shadow_20_sessions"]["passed"] is True
    assert artifact["gate_results"]["shadow_enqueue_latency"]["passed"] is True
    assert artifact["activation_ready"] is True


def test_hash_enrollment_requires_matching_base_hash_in_calibration_provenance():
    complete = _passing_payload()
    complete["shadow_enrollment"]["run_request_id"] = "a" * 64
    live = _live_shadow_export(complete)
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []
    merged = merge_live_shadow_input(base, live, base_input_sha256="a" * 64)
    assert calibrate_p0_2m(merged, n_boot=100)["gate_results"]["trusted_live_shadow_provenance"]["passed"]
    merged["producer_metadata"]["live_shadow_merge"]["base_input_sha256"] = "b" * 64
    gate = calibrate_p0_2m(merged, n_boot=100)["gate_results"]["trusted_live_shadow_provenance"]
    assert gate["passed"] is False
    assert "live_shadow_base_input_hash_mismatch" in gate["actual"]["issues"]


def test_attested_initial_export_without_repeated_run_keys_stays_compatible():
    complete = _passing_payload()
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []
    legacy_export = _live_shadow_export(
        complete, legacy_without_run_keys=True
    )

    merged = merge_live_shadow_input(base, legacy_export)
    artifact = calibrate_p0_2m(merged, n_boot=100)

    assert merged["shadow_enrollment"]["enrollment_key"] == "e" * 64
    assert {row["enrollment_key"] for row in merged["shadow"]} == {"e" * 64}
    assert artifact["activation_ready"] is True


def test_superseding_shadow_run_may_start_later_and_still_pass():
    complete = _passing_payload()
    calendar = xcals.get_calendar("XNYS")
    old_dates = [date.fromisoformat(value) for value in complete["shadow_sessions"]]
    first_label = calendar.date_to_session(old_dates[0], direction="none")
    delayed_dates = [
        stamp.date()
        for stamp in calendar.sessions_window(first_label, 25)[5:]
    ]
    assert len(delayed_dates) == 20
    date_map = dict(zip(old_dates, delayed_dates))
    for row in complete["shadow"]:
        replacement = date_map[date.fromisoformat(row["session_date"])]
        row["session_date"] = replacement.isoformat()
        row["expected_evaluations"] = _rth_2m_evaluations(replacement)
        row["available_evaluations"] = _rth_2m_evaluations(replacement)
    complete["shadow_sessions"] = [day.isoformat() for day in delayed_dates]
    previous = calendar.previous_session(delayed_dates[0])
    enrolled_at = calendar.session_close(previous).to_pydatetime() + timedelta(
        minutes=30
    )
    complete["shadow_enrollment"].update(
        sessions=list(complete["shadow_sessions"]),
        enrolled_at=enrolled_at.isoformat(),
        created_at=(enrolled_at + timedelta(seconds=5)).isoformat(),
        run_request_id="opend-outage-rerun",
        restart_reason="prior live run was incomplete after an OpenD outage",
        supersedes_enrollment_key="d" * 64,
    )

    live = _live_shadow_export(complete)
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []
    merged = merge_live_shadow_input(base, live)
    artifact = calibrate_p0_2m(merged, n_boot=100)

    assert merged["shadow_sessions"][0] == delayed_dates[0].isoformat()
    assert artifact["gate_results"]["forward_shadow_20_sessions"]["passed"] is True
    assert artifact["activation_ready"] is True


def test_rth_enrollment_cannot_claim_a_later_twenty_session_run():
    complete = _passing_payload()
    calendar = xcals.get_calendar("XNYS")
    old_dates = [date.fromisoformat(value) for value in complete["shadow_sessions"]]
    first_label = calendar.date_to_session(old_dates[0], direction="none")
    delayed_dates = [
        stamp.date()
        for stamp in calendar.sessions_window(first_label, 25)[5:]
    ]
    date_map = dict(zip(old_dates, delayed_dates))
    for row in complete["shadow"]:
        replacement = date_map[date.fromisoformat(row["session_date"])]
        row["session_date"] = replacement.isoformat()
        row["expected_evaluations"] = _rth_2m_evaluations(replacement)
        row["available_evaluations"] = _rth_2m_evaluations(replacement)
    complete["shadow_sessions"] = [day.isoformat() for day in delayed_dates]
    rth_enrollment = datetime.combine(
        old_dates[0], datetime.min.time(), timezone.utc
    ).replace(hour=15)  # 10:00 ET
    complete["shadow_enrollment"].update(
        sessions=list(complete["shadow_sessions"]),
        enrolled_at=rth_enrollment.isoformat(),
        created_at=(rth_enrollment + timedelta(seconds=5)).isoformat(),
        run_request_id="forged-delayed-rerun",
        restart_reason="must not skip observed sessions",
        supersedes_enrollment_key="d" * 64,
    )

    with pytest.raises(ValueError, match="current XNYS session"):
        _live_shadow_export(complete)

    artifact = calibrate_p0_2m(complete, n_boot=100)
    enrollment_gate = artifact["gate_results"]["forward_shadow_20_sessions"][
        "actual"
    ]["enrollment"]
    assert "enrollment_during_rth" in enrollment_gate["issues"]
    assert artifact["activation_ready"] is False


def test_enrollment_persistence_over_five_minutes_cannot_activate():
    complete = _passing_payload()
    enrolled_at = datetime.fromisoformat(
        complete["shadow_enrollment"]["enrolled_at"]
    )
    complete["shadow_enrollment"]["created_at"] = (
        enrolled_at + timedelta(seconds=301)
    ).isoformat()
    with pytest.raises(ValueError, match="live-write skew"):
        _live_shadow_export(complete)


def test_post_merge_row_run_key_tamper_cannot_activate():
    complete = _passing_payload()
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []
    merged = merge_live_shadow_input(base, _live_shadow_export(complete))
    for row in merged["shadow"]:
        row["enrollment_key"] = "f" * 64
    merged["producer_metadata"]["live_shadow_merge"][
        "shadow_binding_sha256"
    ] = compute_live_shadow_binding_sha256(merged)

    artifact = calibrate_p0_2m(merged, n_boot=100)

    gate = artifact["gate_results"]["trusted_live_shadow_provenance"]
    assert gate["passed"] is False
    assert "live_shadow_row_enrollment_key_binding_mismatch" in gate["actual"][
        "issues"
    ]
    assert artifact["activation_ready"] is False


def test_real_exporter_shape_merges_and_preserves_missing_telemetry_gates(
    monkeypatch,
):
    complete = _passing_payload()
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []
    monkeypatch.setattr(
        shadow_repository,
        "list_p0_2m_shadow_observations",
        lambda **_kwargs: [],
    )
    enrollment = complete["shadow_enrollment"]
    monkeypatch.setattr(
        shadow_repository,
        "_load_enrollment",
        lambda *_args, **_kwargs: shadow_repository.P02mShadowEnrollmentRecord(
            universe_hash=DEFAULT_P0_UNIVERSE_HASH,
            signal_version=P0_2M_SIGNAL_VERSION,
            universe=tuple(DEFAULT_P0_UNIVERSE_69),
            sessions=tuple(complete["shadow_sessions"]),
            enrolled_at=datetime.fromisoformat(enrollment["enrolled_at"]),
            created_at=datetime.fromisoformat(enrollment["created_at"]),
            enrollment_key="e" * 64,
        ),
    )
    exported = shadow_repository.export_p0_2m_live_shadow_rows(
        session_dates=complete["shadow_sessions"],
        symbols=DEFAULT_P0_UNIVERSE_69,
        universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )

    merged = merge_live_shadow_input(base, exported)
    artifact = calibrate_p0_2m(merged, n_boot=100)

    assert len(merged["shadow"]) == 69 * 20
    assert artifact["gate_results"]["forward_shadow_20_sessions"]["passed"] is True
    assert artifact["gate_results"]["shadow_aggregate_coverage"]["passed"] is True
    assert artifact["gate_results"]["shadow_availability"]["passed"] is False
    assert artifact["gate_results"]["shadow_enqueue_latency"]["passed"] is False
    assert artifact["activation_ready"] is False


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    [
        ("schema_version", "offline_replay_v1", "schema_version mismatch"),
        ("shadow_observation_mode", "offline_static_replay", "not live_forward"),
        ("signal_version", "legacy", "signal_version mismatch"),
        ("universe_hash", "sha256:wrong", "universe_hash"),
        ("universe", list(reversed(DEFAULT_P0_UNIVERSE_69)), "universe does not"),
    ],
)
def test_live_shadow_merge_rejects_top_level_identity_mismatch(
    field, replacement, error
):
    base = _passing_payload()
    live = _live_shadow_export(base)
    live[field] = replacement

    with pytest.raises(ValueError, match=error):
        merge_live_shadow_input(base, live)


def test_live_shadow_merge_requires_exporter_enrollment():
    base = _passing_payload()
    live = _live_shadow_export(base)
    live.pop("enrollment")

    with pytest.raises(ValueError, match="enrollment is missing"):
        merge_live_shadow_input(base, live)


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    [
        ("observation_mode", "offline_static_replay", "not live_forward"),
        ("signal_version", "legacy", "signal_version mismatch"),
        ("universe_hash", "sha256:wrong", "universe_hash mismatch"),
        ("enrollment_key", "f" * 64, "enrollment_key mismatch"),
        ("symbol", "UNKNOWN", "symbol mismatch"),
        ("session_scope", "premarket", "session_scope mismatch"),
    ],
)
def test_live_shadow_merge_rejects_row_identity_mismatch(field, replacement, error):
    base = _passing_payload()
    live = _live_shadow_export(base)
    live["shadow"][0][field] = replacement

    with pytest.raises(ValueError, match=error):
        merge_live_shadow_input(base, live)


def test_live_shadow_merge_rejects_sessions_before_locked_holdout():
    base = _passing_payload()
    live = _live_shadow_export(base)
    live["shadow_sessions"] = list(base["historical_sessions"][-20:])

    with pytest.raises(ValueError, match="next 20 consecutive XNYS"):
        merge_live_shadow_input(base, live)


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    [
        ("observation_mode", "offline_static_replay", "observation_mode"),
        ("signal_version", "legacy", "signal_version"),
        ("universe_hash", "sha256:wrong", "universe_hash"),
        ("universe", list(reversed(DEFAULT_P0_UNIVERSE_69)), "universe mismatch"),
        ("enrollment_key", "f" * 64, "does not match attestation"),
        ("sessions", [], "sessions mismatch"),
        (
            "enrolled_at",
            "2025-11-03T15:00:00+00:00",
            "current XNYS session",
        ),
        ("created_at", "2025-11-03T15:00:00+00:00", "live-write skew"),
    ],
)
def test_live_shadow_merge_rejects_enrollment_mismatch(
    field, replacement, error
):
    base = _passing_payload()
    live = _live_shadow_export(base)
    live["enrollment"][field] = replacement

    with pytest.raises(ValueError, match=error):
        merge_live_shadow_input(base, live)


def test_live_shadow_merge_rejects_enrollment_before_holdout_close():
    base = _passing_payload()
    live = _live_shadow_export(base)
    live["enrollment"]["enrolled_at"] = "2025-10-31T19:00:00+00:00"

    with pytest.raises(ValueError, match="after the holdout close"):
        merge_live_shadow_input(base, live)


def test_live_shadow_merge_rejects_a_skipped_post_holdout_session():
    base = _passing_payload()
    live = _live_shadow_export(base)
    calendar = xcals.get_calendar("XNYS")
    last = date.fromisoformat(live["shadow_sessions"][-1])
    replacement = calendar.sessions_in_range(
        last + timedelta(days=1), last + timedelta(days=10)
    )[0].date().isoformat()
    live["shadow_sessions"][-1] = replacement

    with pytest.raises(ValueError, match="next 20 consecutive XNYS"):
        merge_live_shadow_input(base, live)


def test_calibration_gate_rejects_a_nonconsecutive_shadow_block():
    payload = _passing_payload()
    old_last = payload["shadow_sessions"][-1]
    calendar = xcals.get_calendar("XNYS")
    last = date.fromisoformat(old_last)
    replacement = calendar.sessions_in_range(
        last + timedelta(days=1), last + timedelta(days=10)
    )[0].date()
    payload["shadow_sessions"][-1] = replacement.isoformat()
    expected = _rth_2m_evaluations(replacement)
    for row in payload["shadow"]:
        if row["session_date"] == old_last:
            row["session_date"] = replacement.isoformat()
            row["expected_evaluations"] = expected
            row["available_evaluations"] = expected

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["forward_shadow_20_sessions"]
    assert gate["passed"] is False
    assert gate["actual"]["consecutive_from_holdout"] is False
    assert artifact["activation_ready"] is False


@pytest.mark.parametrize(
    ("mutation", "issue"),
    [
        ("missing", "shadow_enrollment_missing"),
        ("naive", "enrolled_at_missing_or_naive"),
        ("late", "enrollment_during_rth"),
    ],
)
def test_calibration_gate_requires_prospective_enrollment(mutation, issue):
    payload = _passing_payload()
    if mutation == "missing":
        payload.pop("shadow_enrollment")
    elif mutation == "naive":
        payload["shadow_enrollment"]["enrolled_at"] = "2025-10-31T20:30:00"
    else:
        payload["shadow_enrollment"]["enrolled_at"] = (
            "2025-11-03T15:00:00+00:00"
        )

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["forward_shadow_20_sessions"]
    assert gate["passed"] is False
    assert issue in gate["actual"]["enrollment"]["issues"]
    assert artifact["activation_ready"] is False


def test_cli_merges_live_shadow_export_before_writing_derived_input(
    tmp_path, capsys
):
    complete = _passing_payload()
    base = deepcopy(complete)
    base["shadow_sessions"] = []
    base["shadow_observation_mode"] = "not_collected"
    base["shadow"] = []
    live = _live_shadow_export(complete)
    base_path = tmp_path / "base.json"
    live_path = tmp_path / "live.json"
    derived_path = tmp_path / "derived.json"
    artifact_path = tmp_path / "artifact.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    live_path.write_text(json.dumps(live), encoding="utf-8")

    exit_code = calibrate_main(
        [
            "--input",
            str(base_path),
            "--live-shadow-input",
            str(live_path),
            "--derived-input-output",
            str(derived_path),
            "--output",
            str(artifact_path),
            "--bootstrap-samples",
            "100",
        ]
    )

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["activation_ready"] is True
    assert summary["runtime_validation"] == {"valid": True, "reason": "ok"}
    assert summary["live_shadow_input"] == str(live_path.resolve())
    derived = json.loads(derived_path.read_text(encoding="utf-8"))
    assert derived["shadow_observation_mode"] == LIVE_FORWARD_SHADOW_MODE
    assert derived["shadow_enrollment"] == live["enrollment"]
    assert derived["shadow"] == live["shadow"]
    assert derived["producer_metadata"]["live_shadow_merge"]["source_sha256"] == (
        hashlib.sha256(live_path.read_bytes()).hexdigest()
    )


def test_only_holdout_events_are_scored():
    payload = _passing_payload()
    development_day = payload["historical_sessions"][100]
    for index in range(100):
        payload["events"].append(
            {
                "symbol": DEFAULT_P0_UNIVERSE_69[index % 69],
                "session_date": development_day,
                "window_end": f"{development_day}T{10 + index // 60:02d}:{index % 60:02d}:00-04:00",
                "session_scope": "regular",
                "direction": "up",
                "alert_qualified": True,
                "signal_version": P0_2M_SIGNAL_VERSION,
                "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
                "data_quality": {"status": "complete", "issues": []},
                "baseline_count": 60,
                "baseline_last_date": payload["historical_sessions"][139],
                "price_percentile": 0.95,
                "rvol_8m": 1.5,
                "displacement_atr": 0.5,
                "direction_adjusted_return_20m": -1,
            }
        )
    artifact = calibrate_p0_2m(payload, n_boot=100)
    assert artifact["holdout"]["deduped_event_count"] == 40
    assert artifact["holdout"]["direction_adjusted_return_20m_median"] == 0.02


def test_55_ready_symbols_fail_global_gate_but_preserve_per_stratum_states():
    payload = _passing_payload()
    removed = set(DEFAULT_P0_UNIVERSE_69[-14:])
    payload["coverage"] = [
        row for row in payload["coverage"] if row["symbol"] not in removed
    ]
    artifact = calibrate_p0_2m(payload, n_boot=100)
    assert artifact["coverage"]["ready_symbol_count"] == 55
    assert artifact["gate_results"]["baseline_coverage"]["passed"] is False
    assert artifact["activation_ready"] is False
    assert artifact["strata"]["leveraged_inverse_etf"]["state"] == "validated"
    assert artifact["strata"]["stock"]["state"] == "validated"
    assert artifact["strata"]["ordinary_etf"]["state"] == "validated"
    assert not (set(artifact["mobile_eligible_symbols"]) & removed)
    assert len(artifact["mobile_eligible_symbols"]) == 55


def test_budget_simulation_fails_when_more_than_twenty_percent_would_be_suppressed():
    payload = _passing_payload()
    holdout_days = payload["historical_sessions"][-40:]
    events = []
    for day_index, day in enumerate(holdout_days):
        for slot in range(3):
            symbol = DEFAULT_P0_UNIVERSE_69[(day_index * 3 + slot) % 69]
            events.append(
                {
                    "symbol": symbol,
                    "session_date": day,
                    "window_end": f"{day}T{10 + slot:02d}:00:00-04:00",
                    "session_scope": "regular",
                    "direction": "up" if slot % 2 == 0 else "down",
                    "state": "qualified",
                    "alert_qualified": True,
                    "signal_version": P0_2M_SIGNAL_VERSION,
                    "universe_hash": DEFAULT_P0_UNIVERSE_HASH,
                    "data_quality": {"status": "complete", "issues": []},
                    "source": "MoomooFetcher",
                    "failed_checks": [],
                    "thresholds": dict(FROZEN_SIGNAL_THRESHOLDS),
                    "baseline_count": 60,
                    "baseline_last_date": payload["historical_sessions"][139 + day_index],
                    "impulse_8m_log": 0.006 if slot % 2 == 0 else -0.006,
                    "displacement_18m_percent": 0.6 if slot % 2 == 0 else -0.6,
                    "price_percentile": 0.95,
                    "rvol_8m": 1.5,
                    "displacement_atr": 0.5,
                    "direction_adjusted_return_20m": 0.02,
                }
            )
    payload["events"] = events
    artifact = calibrate_p0_2m(payload, n_boot=100)
    assert artifact["holdout"]["budget"]["suppression_rate"] > 0.20
    assert artifact["gate_results"]["budget_suppression"]["passed"] is False
    assert artifact["activation_ready"] is False


def test_shadow_duplicate_unfinished_low_availability_and_slow_enqueue_fail_closed():
    payload = _passing_payload()
    for row in payload["shadow"][:20]:
        row["available_evaluations"] = 0
    payload["shadow"][0].update(
        duplicate_notification_count=1,
        unfinished_bar_trigger_count=1,
        telegram_enqueue_latency_seconds=76,
    )
    artifact = calibrate_p0_2m(payload, n_boot=100)
    gates = artifact["gate_results"]
    assert gates["shadow_integrity"]["passed"] is False
    assert gates["shadow_availability"]["passed"] is False
    assert gates["shadow_enqueue_latency"]["passed"] is False
    assert artifact["activation_ready"] is False


def test_holdout_signal_baseline_must_be_strictly_prior():
    payload = _passing_payload()
    payload["events"][0]["baseline_last_date"] = payload["events"][0][
        "session_date"
    ]
    artifact = calibrate_p0_2m(payload, n_boot=100)
    gate = artifact["gate_results"]["holdout_signal_evidence_integrity"]
    assert gate["passed"] is False
    assert "baseline_not_strictly_prior" in gate["actual"]["examples"][0]["issues"]
    assert artifact["activation_ready"] is False


@pytest.mark.parametrize(
    ("field", "issue"),
    [
        ("alert_qualified", "alert_qualified_not_explicit_true"),
        ("state", "state_not_explicit_qualified"),
        ("source", "source_missing"),
        ("window_end", "window_end_missing_or_naive"),
        ("failed_checks", "failed_checks_not_explicit_empty"),
        ("thresholds", "thresholds_mismatch"),
    ],
)
def test_holdout_signal_contract_fields_are_explicit_and_fail_closed(field, issue):
    payload = _passing_payload()
    payload["events"][0].pop(field)

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["holdout_signal_evidence_integrity"]
    assert gate["passed"] is False
    assert issue in gate["actual"]["examples"][0]["issues"]
    assert artifact["activation_ready"] is False


@pytest.mark.parametrize(
    ("impulse", "displacement", "direction", "issue"),
    [
        (0.0, 0.6, "up", "impulse_8m_log_missing_or_zero"),
        (0.006, 0.0, "up", "displacement_18m_percent_missing_or_zero"),
        (0.006, -0.6, "up", "impulse_displacement_direction_mismatch"),
        (-0.006, -0.6, "up", "direction_not_consistent_with_signed_moves"),
    ],
)
def test_holdout_signal_signed_moves_must_be_nonzero_same_direction(
    impulse, displacement, direction, issue
):
    payload = _passing_payload()
    payload["events"][0].update(
        impulse_8m_log=impulse,
        displacement_18m_percent=displacement,
        direction=direction,
    )

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["holdout_signal_evidence_integrity"]
    assert gate["passed"] is False
    assert issue in gate["actual"]["examples"][0]["issues"]


@pytest.mark.parametrize(
    ("field", "issue"),
    [
        ("qualified", "qualified_not_explicit_true"),
        ("state", "state_not_explicit_qualified"),
        ("comparator_version", "comparator_version_mismatch"),
        ("comparator_basis", "comparator_basis_mismatch"),
        ("source", "source_missing"),
        ("window_end", "window_end_missing_or_naive"),
        ("failed_checks", "failed_checks_not_explicit_empty"),
        ("thresholds", "thresholds_mismatch"),
    ],
)
def test_atr_only_comparator_contract_and_provenance_fail_closed(field, issue):
    payload = _passing_payload()
    payload["atr_only_events"][0].pop(field)

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["atr_only_comparator_evidence_integrity"]
    assert gate["passed"] is False
    assert issue in gate["actual"]["examples"][0]["issues"]
    assert artifact["activation_ready"] is False


def test_frozen_universe_rejects_a_different_known_69_symbol_set():
    payload = _passing_payload()
    payload["universe"][-1] = "NEWCO"
    payload["asset_classes"] = {"NEWCO": "stock"}
    for row in payload["coverage"]:
        if row["symbol"] == "CBRS":
            row["symbol"] = "NEWCO"
    for collection in ("events", "atr_only_events", "shadow"):
        for row in payload[collection]:
            if row["symbol"] == "CBRS":
                row["symbol"] = "NEWCO"
    alternate_hash = compute_universe_hash(payload["universe"])
    for collection in ("events", "atr_only_events"):
        for row in payload[collection]:
            row["universe_hash"] = alternate_hash
    artifact = calibrate_p0_2m(payload, n_boot=100)
    assert artifact["unknown_symbols"] == []
    assert artifact["gate_results"]["known_asset_classes"]["passed"] is True
    assert artifact["gate_results"]["fixed_universe_69"]["passed"] is False
    assert artifact["activation_ready"] is False


def test_exactly_56_ready_symbols_shadows_failed_symbols_not_their_entire_class():
    payload = _passing_payload()
    stock_symbols = [
        symbol
        for symbol in DEFAULT_P0_UNIVERSE_69
        if DEFAULT_ASSET_CLASS_BY_SYMBOL[symbol] == "stock"
    ]
    removed = set(stock_symbols[:13])
    payload["coverage"] = [
        row for row in payload["coverage"] if row["symbol"] not in removed
    ]

    artifact = calibrate_p0_2m(_trusted_passing_payload(payload), n_boot=100)

    assert artifact["coverage"]["ready_symbol_count"] == 56
    assert artifact["gate_results"]["baseline_coverage"]["passed"] is True
    assert "stratified_reporting_complete" not in artifact["gate_results"]
    assert artifact["strata"]["stock"]["state"] == "validated"
    assert set(artifact["strata"]["stock"]["excluded_symbols"]) == removed
    assert artifact["strata"]["ordinary_etf"]["state"] == "validated"
    assert artifact["strata"]["leveraged_inverse_etf"]["state"] == "validated"
    assert artifact["activation_ready"] is True
    assert not (set(artifact["mobile_eligible_symbols"]) & removed)
    assert set(stock_symbols) - removed <= set(artifact["mobile_eligible_symbols"])


@pytest.mark.parametrize("end,censored", [
    ("2026-09-18T15:58:00-04:00", True),
    ("2026-11-27T12:58:00-05:00", True),
    ("2026-09-18T15:40:00-04:00", False),
])
def test_outcome_censoring_uses_actual_close_without_removing_signals(end, censored):
    row = {"window_end": end, "session_date": end[:10], "direction_adjusted_return_20m": None}
    assert _outcome_status(row) == ("session_censored" if censored else "missing")
    row["outcome_status"] = "session_censored"
    assert _outcome_status(row) == ("session_censored" if censored else "invalid")


def test_censored_event_retains_frequency_but_does_not_poison_20m_gate():
    payload = _passing_payload()
    original = payload["events"][0]
    original["window_end"] = original["session_date"] + "T15:58:00-04:00"
    original["direction_adjusted_return_20m"] = None
    original["outcome_status"] = "session_censored"
    artifact = calibrate_p0_2m(payload, n_boot=10)
    assert artifact["holdout"]["deduped_event_count"] == len(payload["events"])
    assert artifact["holdout"]["outcome_status_counts"]["session_censored"] == 1
    assert artifact["gate_results"]["positive_20m_direction_adjusted_median"]["passed"] is True
    original["window_end"] = original["session_date"] + "T10:00:00-04:00"
    assert calibrate_p0_2m(payload, n_boot=10)["gate_results"]["positive_20m_direction_adjusted_median"]["passed"] is False


@pytest.mark.parametrize("status", ["missing", "pending", "session_censored"])
def test_comparator_incomplete_or_falsely_censored_outcome_cannot_disappear_from_ci(status):
    payload = _passing_payload()
    payload["atr_only_events"][0].update(direction_adjusted_return_20m=None, outcome_status=status)
    artifact = calibrate_p0_2m(payload, n_boot=10)
    assert artifact["holdout"]["atr_only_comparator"]["cluster_days"] >= 20
    assert artifact["gate_results"]["atr_only_relative_cluster_ci90"]["passed"] is False


def test_forward_outcome_report_separates_all_signals_plans_and_real_queue_receipts():
    payload = _passing_payload()
    row = payload["shadow"][0]
    event = {"window_end": row["session_date"] + "T10:00:00-05:00", "direction": "up",
             "outcome_status": "matured", "direction_adjusted_return_20m": 0.01}
    row["diagnostics"] = {"schema_version": "p0_2m_shadow_outcomes_v1", "activation_evidence": False,
                          "events": [event, {**event, "window_end": row["session_date"] + "T11:00:00-05:00"}],
                          "would_notify_events": [event], "notification_events": []}
    merged = _trusted_passing_payload(payload)
    artifact = calibrate_p0_2m(merged, n_boot=10)
    report = artifact["forward_outcomes"]
    assert report["source_provenance_verified"] is True
    assert report["activation_evidence"] is False
    assert report["populations"]["events"]["event_count"] == 2
    assert report["populations"]["would_notify_events"]["event_count"] == 1
    assert report["populations"]["notification_events"]["event_count"] == 0
    assert report["populations"]["events"]["direction_adjusted_return_20m_median"] == 0.01
    assert "not_phone_delivery" in report["notification_population"]
    merged["shadow"][0]["diagnostics"]["notification_events"] = [event]
    tampered = calibrate_p0_2m(merged, n_boot=10)
    assert tampered["forward_outcomes"]["source_provenance_verified"] is False
    assert tampered["gate_results"]["trusted_live_shadow_provenance"]["passed"] is False


def test_class_without_holdout_events_stays_shadow_without_blocking_other_classes():
    payload = _passing_payload()
    leveraged = {
        symbol
        for symbol in DEFAULT_P0_UNIVERSE_69
        if DEFAULT_ASSET_CLASS_BY_SYMBOL[symbol] == "leveraged_inverse_etf"
    }
    payload["events"] = [
        row for row in payload["events"] if row["symbol"] not in leveraged
    ]

    artifact = calibrate_p0_2m(_trusted_passing_payload(payload), n_boot=100)

    assert artifact["activation_ready"] is True
    assert artifact["strata"]["leveraged_inverse_etf"]["state"] == "shadow"
    assert leveraged.isdisjoint(artifact["mobile_eligible_symbols"])
    _attest(artifact)
    assert validate_activation_artifact(
        artifact, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (True, "ok")
    valid, reason, eligible = validated_mobile_eligible_symbols(
        artifact,
        expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH,
    )
    assert (valid, reason) == (True, "ok")
    assert leveraged.isdisjoint(eligible)
    assert set(eligible) == set(artifact["mobile_eligible_symbols"])


def test_shadow_requires_every_symbol_session_aggregate():
    payload = _passing_payload()
    payload["shadow"].pop()

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["shadow_aggregate_coverage"]
    assert gate["passed"] is False
    assert gate["actual"]["missing_symbol_session_rows"] == 1


@pytest.mark.parametrize(
    "field",
    [
        "expected_evaluations",
        "available_evaluations",
        "duplicate_notification_count",
        "unfinished_bar_trigger_count",
    ],
)
def test_shadow_aggregate_counts_must_be_explicit(field):
    payload = _passing_payload()
    payload["shadow"][0].pop(field)

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["shadow_aggregate_coverage"]
    assert gate["passed"] is False
    assert gate["actual"]["invalid_aggregate_rows"] == 1


def test_shadow_aggregate_counts_reject_numeric_strings():
    payload = _passing_payload()
    payload["shadow"][0]["expected_evaluations"] = "195"

    artifact = calibrate_p0_2m(payload, n_boot=100)

    assert artifact["gate_results"]["shadow_aggregate_coverage"]["passed"] is False


def test_attested_actual_queue_receipts_can_satisfy_latency_gate():
    payload = _passing_payload()
    payload["shadow"][0].update(
        telegram_enqueue_evidence_mode="actual_telegram_queue_receipt",
        telegram_enqueue_evidence_modes=[
            "actual_telegram_queue_receipt", "counterfactual_no_send_same_queue",
        ],
    )
    trusted = _trusted_passing_payload(payload)
    artifact = calibrate_p0_2m(trusted, n_boot=100)

    assert artifact["gate_results"]["trusted_live_shadow_provenance"]["passed"] is True
    assert artifact["gate_results"]["shadow_enqueue_latency"]["passed"] is True
    assert artifact["activation_ready"] is True


@pytest.mark.parametrize("modes", [
    ["actual_telegram_queue_receipt", "offline_static_replay"],
    ["counterfactual_no_send_same_queue"],
    "actual_telegram_queue_receipt",
])
def test_queue_mode_collection_cannot_claim_untrusted_or_inconsistent_sources(modes):
    payload = _passing_payload()
    payload["shadow"][0].update(
        telegram_enqueue_evidence_mode="actual_telegram_queue_receipt",
        telegram_enqueue_evidence_modes=modes,
    )
    artifact = calibrate_p0_2m(payload, n_boot=100)

    assert artifact["gate_results"]["shadow_aggregate_coverage"]["passed"] is False
    assert artifact["shadow"]["invalid_enqueue_evidence_mode_count"] == 1
    assert artifact["activation_ready"] is False


@pytest.mark.parametrize("evidence_mode", [None, "offline_static_replay"])
def test_shadow_enqueue_evidence_mode_must_be_explicit_and_trusted(evidence_mode):
    payload = _passing_payload()
    row = payload["shadow"][0]
    if evidence_mode is None:
        row.pop("telegram_enqueue_evidence_mode")
    else:
        row["telegram_enqueue_evidence_mode"] = evidence_mode
    # A plausible number cannot rescue absent or untrusted provenance.
    row["would_notify"] = True
    row["telegram_enqueue_latency_seconds"] = 10.0

    artifact = calibrate_p0_2m(payload, n_boot=100)

    aggregate_gate = artifact["gate_results"]["shadow_aggregate_coverage"]
    latency_gate = artifact["gate_results"]["shadow_enqueue_latency"]
    assert aggregate_gate["passed"] is False
    assert aggregate_gate["actual"]["invalid_enqueue_evidence_modes"] == 1
    assert latency_gate["passed"] is False
    assert latency_gate["actual"]["invalid_evidence_modes"] == 1
    assert artifact["activation_ready"] is False


def test_shadow_expected_count_must_match_xnys_2m_rth_windows():
    payload = _passing_payload()
    half_day_row = next(
        row for row in payload["shadow"] if row["session_date"] == "2025-11-28"
    )
    assert half_day_row["expected_evaluations"] == 105
    half_day_row["expected_evaluations"] = 195

    artifact = calibrate_p0_2m(payload, n_boot=100)

    gate = artifact["gate_results"]["shadow_aggregate_coverage"]
    assert gate["passed"] is False
    assert "expected_evaluations_mismatch" in gate["actual"]["examples"][0]["issues"]


def test_budget_uses_runtime_trailing_seven_calendar_days_not_iso_week():
    rows = []
    slots = {
        "2026-08-25": 2,
        "2026-08-26": 2,
        "2026-08-27": 2,
        "2026-08-28": 1,
        "2026-08-31": 1,
    }
    for day, count in slots.items():
        for index in range(count):
            rows.append(
                {
                    "session_date": day,
                    "window_end": f"{day}T10:{index:02d}:00-04:00",
                }
            )

    report = _simulate_budget(rows)

    assert report["weekly_budget_window"] == "trailing_7_calendar_days_inclusive"
    assert report["delivered"] == 7
    assert report["suppressed"] == 1


def test_runtime_validator_rejects_hash_and_gate_tampering():
    artifact = calibrate_p0_2m(_trusted_passing_payload(), n_boot=100)
    assert validate_activation_artifact(
        artifact, expected_universe_hash="sha256:wrong"
    ) == (False, "universe_hash_mismatch")

    tampered = deepcopy(artifact)
    tampered["gate_results"]["shadow_integrity"]["passed"] = False
    assert validate_activation_artifact(
        tampered, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "artifact_hash_mismatch")

    missing_hash = deepcopy(artifact)
    missing_hash.pop("artifact_sha256")
    assert validate_activation_artifact(
        missing_hash, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "artifact_hash_missing")


def test_runtime_requires_exact_db_attestation_after_self_rehash():
    artifact = calibrate_p0_2m(_trusted_passing_payload(), n_boot=100)
    assert validate_activation_artifact(
        artifact, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "activation_artifact_attestation_not_found")

    _attest(artifact)
    assert validate_activation_artifact(
        artifact, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (True, "ok")
    db = get_db()
    for statement in (
        "UPDATE journal_v2_p0_2m_activation_artifact_attestations "
        "SET authority = 'forged'",
        "DELETE FROM journal_v2_p0_2m_activation_artifact_attestations",
    ):
        with pytest.raises(Exception, match="append-only"):
            with db.session_scope() as session:
                session.execute(text(statement))

    forged = deepcopy(artifact)
    forged["shadow"]["duplicate_notification_count"] = 1
    assert forged["gate_results"]["shadow_integrity"]["passed"] is True
    _rehash(forged)
    assert validate_activation_artifact(
        forged, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "activation_artifact_attestation_not_found")


def test_runtime_validator_requires_the_complete_fixed_gate_set():
    artifact = calibrate_p0_2m(_trusted_passing_payload(), n_boot=100)
    missing_gate = deepcopy(artifact)
    missing_gate["gate_results"].pop("atr_only_comparator_evidence_integrity")
    _rehash(missing_gate)
    assert validate_activation_artifact(
        missing_gate, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "gate_results_incomplete")

    extra_gate = deepcopy(artifact)
    extra_gate["gate_results"]["invented_gate"] = {
        "passed": True,
        "actual": True,
        "threshold": "invented",
    }
    _rehash(extra_gate)
    assert validate_activation_artifact(
        extra_gate, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "gate_results_incomplete")


def test_runtime_validator_recomputes_mobile_eligible_symbols_from_strata():
    artifact = calibrate_p0_2m(_trusted_passing_payload(), n_boot=100)
    artifact["mobile_eligible_symbols"].pop()
    _rehash(artifact)

    assert validate_activation_artifact(
        artifact, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, "mobile_eligible_symbols_mismatch")


@pytest.mark.parametrize(
    ("field", "replacement", "reason"),
    [
        ("signal_thresholds", {}, "signal_thresholds_mismatch"),
        ("atr_only_comparator_version", "fake", "comparator_version_mismatch"),
        ("atr_only_comparator_basis", "fake", "comparator_basis_mismatch"),
        ("atr_only_comparator_thresholds", {}, "comparator_thresholds_mismatch"),
    ],
)
def test_runtime_validator_locks_signal_and_comparator_contract(
    field, replacement, reason
):
    artifact = calibrate_p0_2m(_passing_payload(), n_boot=100)
    artifact[field] = replacement
    _rehash(artifact)

    assert validate_activation_artifact(
        artifact, expected_universe_hash=DEFAULT_P0_UNIVERSE_HASH
    ) == (False, reason)


def test_jsonl_loader_requires_typed_rows(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "meta",
                        "historical_sessions": ["2025-01-02"],
                        "shadow_sessions": ["2025-09-01"],
                        "shadow_observation_mode": "live_forward_shadow",
                    }
                ),
                json.dumps(
                    {
                        "kind": "coverage",
                        "symbol": "TSLA",
                        "session_date": "2025-01-02",
                        "complete_minutes": 390,
                        "eligible_minutes": 390,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_input(path)
    assert loaded["schema_version"] == P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION
    assert loaded["coverage"][0]["symbol"] == "TSLA"
    assert loaded["historical_sessions"] == ["2025-01-02"]
    assert loaded["shadow_observation_mode"] == "live_forward_shadow"
