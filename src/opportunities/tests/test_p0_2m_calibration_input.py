from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import pytest

from scripts.calibrate_p0_2m import main as calibrate_main
from src.opportunities.exact_2m import aggregate_exact_2m_ohlcv
from src.opportunities.p0_2m_calibration import calibrate_p0_2m, compute_universe_hash, FROZEN_SIGNAL_THRESHOLDS
from src.opportunities.p0_2m_calibration_input import (
    P0_2M_INPUT_PRODUCER_VERSION,
    P0_2M_RAW_EXPORT_SCHEMA_VERSION,
    _prior_matching_sessions,
    _scan_regular_day,
    build_calibration_input_from_export_dir,
    review_saved_adjustment,
    is_auditable_adjustment_check,
    replay_saved_cases,
    _bar_map,
    _outcome_20m,
)
from src.opportunities.p0_2m_signal import compute_p0_2m_signal, P0_2M_SIGNAL_VERSION


_ET = ZoneInfo("America/New_York")


def _selected_sessions(end: str):
    calendar = xcals.get_calendar("XNYS")
    values = calendar.sessions_in_range("2025-01-01", end)
    assert len(values) >= 200
    return [stamp.date() for stamp in values[-200:]]


def _write_one_symbol_export(tmp_path, *, bar_label: str = "end"):
    sessions = _selected_sessions("2025-12-01")
    minute_rows = []
    for index, day in enumerate(sessions):
        if index < 140:
            total_move = 0.08
            volume = 100.0
        elif index < 180:
            total_move = 2.0
            volume = 1000.0
        else:
            total_move = 4.0
            volume = 3000.0
        price = 100.0
        for minute_index in range(18):
            start = datetime(
                day.year, day.month, day.day, 9, 30, tzinfo=_ET
            ) + timedelta(minutes=minute_index)
            step = total_move / 8 if minute_index >= 10 else 0.0
            close = price + step
            timestamp = start + timedelta(minutes=1) if bar_label == "end" else start
            minute_rows.append(
                {
                    "date": timestamp.isoformat(),
                    "open": price,
                    "high": max(price, close),
                    "low": min(price, close),
                    "close": close,
                    "volume": volume,
                }
            )
            price = close
        # A completed bar ending exactly E+20 supplies the forward outcome
        # without filling the intervening gap or leaking into signal inputs.
        future_open = price
        for minute_index in range(2):
            start = datetime(
                day.year, day.month, day.day, 10, 6, tzinfo=_ET
            ) + timedelta(minutes=minute_index)
            close = future_open * (1.005 if minute_index == 0 else 1.01)
            timestamp = start + timedelta(minutes=1) if bar_label == "end" else start
            minute_rows.append(
                {
                    "date": timestamp.isoformat(),
                    "open": future_open,
                    "high": max(future_open, close),
                    "low": min(future_open, close),
                    "close": close,
                    "volume": volume,
                }
            )
            future_open = close

    daily_rows = [
        {
            "date": day.isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000,
        }
        for day in sessions
    ]
    pd.DataFrame(minute_rows).to_csv(tmp_path / "TSLA_1m.csv", index=False)
    pd.DataFrame(daily_rows).to_csv(tmp_path / "TSLA_daily.csv", index=False)
    manifest = {
        "schema_version": P0_2M_RAW_EXPORT_SCHEMA_VERSION,
        # Freeze the 180-day calibration at t0; the following 20 sessions are
        # retrospective diagnostics only, never prospective shadow evidence.
        "selection_end_session": sessions[179].isoformat(),
        "offline_replay_end_session": sessions[-1].isoformat(),
        "source": "MoomooFetcher",
        "minute_bar_label": bar_label,
        "minute_timezone": "America/New_York",
        "universe": ["TSLA"],
        "adjustment": {
            "autype": "QFQ", "consistency_verified": True,
            "snapshot_at": "2025-12-02T12:00:00+00:00",
            "verification_basis": "deterministic_unadjusted_test_fixture",
        },
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return sessions


def _collected_two_symbol_export(tmp_path):
    sessions = _write_one_symbol_export(tmp_path)
    for suffix in ("1m", "daily"):
        (tmp_path / f"META_{suffix}.csv").write_bytes((tmp_path / f"TSLA_{suffix}.csv").read_bytes())
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    universe = ["TSLA", "META"]
    manifest.update({
        "universe": universe, "universe_hash": compute_universe_hash(universe),
        "signal_version": P0_2M_SIGNAL_VERSION,
        "signal_thresholds": dict(FROZEN_SIGNAL_THRESHOLDS),
        "historical_sessions": [day.isoformat() for day in sessions[:180]],
        "files": {}, "collection": {"status": "complete", "symbols": {}},
    })
    manifest["adjustment"]["consistency_verified"] = False
    for symbol in universe:
        manifest["files"][symbol] = {}
        for kind, suffix in (("minute", "1m"), ("daily", "daily")):
            file = tmp_path / f"{symbol}_{suffix}.csv"
            manifest["files"][symbol][kind] = file.name
            manifest["files"][symbol][f"{kind}_sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        verified = symbol == "META"
        manifest["collection"]["symbols"][symbol] = {"adjustment_check": {
            "consistency_verified": verified, "checked_sessions": 2,
            "reasons": [] if verified else ["corporate_actions_require_point_in_time_reconstruction"],
            "corporate_actions": [],
        }}
    path.write_text(json.dumps(manifest))
    return manifest


def test_per_symbol_adjustment_failure_preserves_denominator_and_other_evidence(tmp_path):
    _collected_two_symbol_export(tmp_path)
    payload = build_calibration_input_from_export_dir(tmp_path)
    assert payload["producer_metadata"]["derivation_errors"] == []
    coverage = {s: [r for r in payload["coverage"] if r["symbol"] == s] for s in ("TSLA", "META")}
    assert len(coverage["TSLA"]) == len(coverage["META"]) == 60
    assert all(r["complete_minutes"] == 0 and r["observed_complete_minutes"] > 0 for r in coverage["TSLA"])
    assert all(r["complete_minutes"] == r["observed_complete_minutes"] > 0 for r in coverage["META"])
    assert [r["eligible_minutes"] for r in coverage["TSLA"]] == [r["eligible_minutes"] for r in coverage["META"]]
    for field in ("events", "atr_only_events", "development_events", "development_atr_only_events"):
        assert all(row["symbol"] == "META" for row in payload[field])
    assert payload["events"] and payload["atr_only_events"]
    excluded = payload["producer_metadata"]["per_symbol"]["TSLA"]
    assert excluded["evidence_eligible"] is False
    assert excluded["raw_event_counts"]["holdout"]["strict"] > 0
    assert calibrate_p0_2m(payload, n_boot=5)["gate_results"]["input_integrity"]["passed"] is True


def test_all_explicit_adjustment_failures_are_not_structural_corruption(tmp_path):
    manifest = _collected_two_symbol_export(tmp_path)
    manifest["collection"]["symbols"]["META"]["adjustment_check"] = dict(manifest["collection"]["symbols"]["TSLA"]["adjustment_check"])
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    payload = build_calibration_input_from_export_dir(tmp_path)
    assert payload["producer_metadata"]["derivation_errors"] == []
    assert all(row["complete_minutes"] == 0 for row in payload["coverage"])
    assert payload["events"] == payload["atr_only_events"] == []


@pytest.mark.parametrize("broken", ["check", "reason", "hash_missing", "hash_mismatch", "identity"])
def test_collection_missing_or_corrupt_provenance_remains_global_error(tmp_path, broken):
    manifest = _collected_two_symbol_export(tmp_path)
    if broken == "check":
        del manifest["collection"]["symbols"]["TSLA"]["adjustment_check"]
    elif broken == "reason":
        manifest["collection"]["symbols"]["TSLA"]["adjustment_check"]["reasons"] = []
    elif broken == "hash_missing":
        del manifest["files"]["TSLA"]["minute_sha256"]
    elif broken == "hash_mismatch":
        manifest["files"]["TSLA"]["minute_sha256"] = "a" * 64
    else:
        manifest["universe_hash"] = "b" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    payload = build_calibration_input_from_export_dir(tmp_path)
    assert payload["producer_metadata"]["derivation_errors"]
    assert calibrate_p0_2m(payload, n_boot=5)["gate_results"]["input_integrity"]["passed"] is False


def test_legacy_export_still_requires_verified_snapshot(tmp_path):
    _write_one_symbol_export(tmp_path)
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["adjustment"]
    path.write_text(json.dumps(manifest))
    payload = build_calibration_input_from_export_dir(tmp_path)
    assert "adjustment_consistency_not_verified" in payload["producer_metadata"]["derivation_errors"]
    assert all(row["complete_minutes"] == 0 for row in payload["coverage"])


def test_zero_volume_exact_bars_are_structural_but_not_signal_coverage(tmp_path):
    sessions = _write_one_symbol_export(tmp_path)
    path = tmp_path / "TSLA_1m.csv"
    frame = pd.read_csv(path)
    frame.loc[frame["date"].str.startswith(sessions[0].isoformat()), "volume"] = 0
    frame.to_csv(path, index=False)
    payload = build_calibration_input_from_export_dir(tmp_path)
    first = next(row for row in payload["coverage"] if row["session_date"] == sessions[0].isoformat())
    assert first["structurally_complete_minutes"] == 20
    assert first["observed_complete_minutes"] == first["complete_minutes"] == 0
    assert first["eligible_minutes"] == 390


def _multiplicative_review_inputs():
    days = [datetime(2026, 1, day, tzinfo=_ET).date() for day in (23, 26)]
    minutes, daily = [], []
    for day_index, day in enumerate(days):
        scale = 0.5 if day_index == 0 else 1.0
        rows = []
        for index in range(390):
            price = 100 + index * 0.01
            rows.append({"date": (datetime.combine(day, datetime.min.time(), tzinfo=_ET) + timedelta(hours=9, minutes=31 + index)).isoformat(),
                         "open": price, "high": price + 0.02, "low": price - 0.01,
                         "close": price + 0.01, "volume": 100., "amount": (price + 0.005) / scale * 100})
        daily.append({"date": day.isoformat(), "open": rows[0]["open"], "close": rows[-1]["close"],
                      "high": max(r["high"] for r in rows), "low": min(r["low"] for r in rows),
                      "volume": sum(r["volume"] for r in rows), "amount": sum(r["amount"] for r in rows)})
        minutes.extend(rows)
    minute, day_frame = pd.DataFrame(minutes), pd.DataFrame(daily)
    bars = _bar_map(aggregate_exact_2m_ohlcv(minute, source="MoomooFetcher", as_of=datetime(2026, 1, 27, 20, tzinfo=_ET)))
    check = {"consistency_verified": False, "checked_sessions": 2,
             "reasons": ["corporate_actions_require_point_in_time_reconstruction"],
             "corporate_actions": [{"ex_div_date": "2026-01-26", "forward_adj_factorA": 0.5, "forward_adj_factorB": 0.0,
                                    "per_cash_div": 100., **dict.fromkeys(("split_ratio", "join_base", "join_ert", "per_share_div_ratio",
                                    "per_share_trans_ratio", "allotment_ratio", "stk_spo_ratio", "spin_off_ratio", "add_base", "add_ert",
                                    "transfer_base", "transfer_ert"))}]}
    return minute, day_frame, {"check": check, "historical_sessions": days, "bars": bars,
                               "snapshot_at": "2026-01-27T20:00:00-05:00", "calendar": xcals.get_calendar("XNYS")}


def test_saved_multiplicative_factors_require_real_price_volume_and_scale_checks():
    minute, daily, kwargs = _multiplicative_review_inputs()
    review = review_saved_adjustment(minute, daily, **kwargs)
    assert review["consistency_verified"] is True
    assert kwargs["check"]["consistency_verified"] is False  # original evidence untouched
    assert review["multiplicative_review"]["price_sessions_checked"] == 2
    assert review["multiplicative_review"]["volume_sessions_checked"] == 2
    assert review["multiplicative_review"]["scale_invariance_verified"] is True
    assert is_auditable_adjustment_check(review) is True
    review["corporate_actions"][0]["forward_adj_factorA"] = 0.25
    assert is_auditable_adjustment_check(review) is False


@pytest.mark.parametrize("broken", ["affine", "factor", "minute_volume", "daily_volume", "amount", "price",
                                    "daily_nan_open", "daily_nan_close", "minute_nan_amount", "minute_negative_volume",
                                    "split", "unknown_action"])
def test_adjustment_review_does_not_promote_unverified_units_or_affine_prices(broken):
    minute, daily, kwargs = _multiplicative_review_inputs()
    if broken == "affine":
        kwargs["check"]["corporate_actions"][0]["forward_adj_factorB"] = -0.01
    elif broken == "factor":
        kwargs["check"]["corporate_actions"][0]["forward_adj_factorA"] = None
    elif broken == "minute_volume":
        minute["volume"] *= 10
    elif broken == "daily_volume":
        daily["volume"] *= 10
    elif broken == "amount":
        minute = minute.drop(columns="amount")
    elif broken.startswith("daily_nan_"):
        daily.loc[0, broken.removeprefix("daily_nan_")] = float("nan")
    elif broken == "minute_nan_amount":
        minute.loc[0, "amount"] = float("nan")
    elif broken == "minute_negative_volume":
        minute.loc[0, "volume"] = -1
    elif broken == "split":
        kwargs["check"]["corporate_actions"][0]["split_ratio"] = 2
    elif broken == "unknown_action":
        kwargs["check"]["corporate_actions"][0].pop("split_ratio")
    else:
        daily.loc[0, "close"] += 1
    review = review_saved_adjustment(minute, daily, **kwargs)
    assert review["consistency_verified"] is False
    assert review["reasons"]


def test_outcome_missing_pending_and_half_day_censoring_are_distinct():
    end = datetime(2026, 11, 27, 12, 40, tzinfo=_ET)
    closed = end.replace(hour=13, minute=0)
    args = dict(end=end, session_close=closed, trigger_price=100., direction="down")
    assert _outcome_20m({}, **args)["outcome_status"] == "missing"
    assert _outcome_20m({}, **args, as_of=end)["outcome_status"] == "pending"
    assert _outcome_20m({}, **{**args, "end": end + timedelta(minutes=2)})["outcome_status"] == "session_censored"
    result = _outcome_20m({end + timedelta(minutes=18): {"close": 99}}, **args)
    assert result["outcome_status"] == "matured"
    assert result["direction_adjusted_return_20m"] == pytest.approx(0.01)


def test_raw_case_replay_checks_every_window_and_raw_hashes(tmp_path):
    manifest = _collected_two_symbol_export(tmp_path)
    day = manifest["historical_sessions"][140]
    replay = replay_saved_cases(tmp_path, cases=(("TSLA", day),))
    assert replay["live_batch_equivalent"] is True
    assert replay["activation_evidence"] is False
    assert len(replay["cases"][0]["windows"]) == 195
    assert replay["cases"][0]["first_strict_crossing"] is not None
    path = tmp_path / "TSLA_1m.csv"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="replay_raw_file_hash_mismatch"):
        replay_saved_cases(tmp_path, cases=(("TSLA", day),))


def test_raw_export_builds_locked_split_events_and_offline_replay(tmp_path):
    sessions = _write_one_symbol_export(tmp_path)

    payload = build_calibration_input_from_export_dir(tmp_path)

    assert len(payload["historical_sessions"]) == 180
    assert payload["shadow_sessions"] == []
    assert payload["historical_sessions"] == [
        day.isoformat() for day in sessions[:180]
    ]
    assert payload["events"]
    assert payload["atr_only_events"]
    assert "development_events" in payload
    assert "development_atr_only_events" in payload
    assert payload["producer_metadata"]["per_symbol"]["TSLA"][
        "development_scan_totals"
    ]
    assert all(row["session_date"] in payload["historical_sessions"][60:140]
               for row in payload["development_events"])
    assert all(row["session_date"] in payload["historical_sessions"][140:]
               for row in payload["events"])
    event = payload["events"][0]
    assert event["baseline_count"] == 60
    assert event["baseline_last_date"] < event["session_date"]
    assert event["direction_adjusted_return_20m"] > 0
    assert event["price_percentile"] >= 0.95
    assert event["rvol_8m"] >= 1.5
    assert event["displacement_atr"] >= 0.5
    assert payload["shadow"] == []
    offline_shadow = payload["producer_metadata"]["offline_shadow_replay"]
    assert offline_shadow["activation_evidence"] is False
    assert offline_shadow["sessions"] == [
        day.isoformat() for day in sessions[-20:]
    ]
    assert len(offline_shadow["rows"]) == 20
    assert any(row["would_notify"] for row in offline_shadow["rows"])
    assert all(
        row["telegram_latency_evidence"] == "missing_no_live_enqueue_log"
        for row in offline_shadow["rows"]
    )
    assert (
        payload["producer_metadata"]["producer_version"]
        == P0_2M_INPUT_PRODUCER_VERSION
    )

    raw = pd.read_csv(tmp_path / "TSLA_1m.csv")
    exact = aggregate_exact_2m_ohlcv(
        raw,
        source="MoomooFetcher",
        as_of=datetime(
            sessions[-1].year,
            sessions[-1].month,
            sessions[-1].day,
            20,
            1,
            tzinfo=_ET,
        ),
    )
    production = compute_p0_2m_signal(
        exact.to_dict(orient="records"),
        reference_bars=exact.to_dict(orient="records"),
        market_date_et=event["session_date"],
        as_of=event["window_end"],
        prior_daily_atr14=event["prior_daily_atr14"],
        symbol="TSLA",
        source="MoomooFetcher",
        universe_hash=event["universe_hash"],
    )
    assert production["alert_qualified"] is True
    for field in (
        "impulse_8m_log",
        "price_percentile",
        "rvol_8m",
        "displacement_atr",
    ):
        assert production[field] == event[field]

    artifact = calibrate_p0_2m(payload, n_boot=20)
    assert artifact["gate_results"]["holdout_signal_evidence_integrity"][
        "passed"
    ] is True
    assert artifact["gate_results"]["atr_only_comparator_evidence_integrity"][
        "passed"
    ] is True
    assert artifact["gate_results"]["shadow_enqueue_latency"]["passed"] is False
    assert artifact["gate_results"]["forward_shadow_20_sessions"]["passed"] is False
    assert artifact["shadow"]["latency_sample_count"] == 0
    assert artifact["activation_ready"] is False
    assert artifact["development"]["collected"] is True
    assert artifact["development"]["activation_evidence"] is False
    assert artifact["economic_edge_verified"] is False
    assert artifact["validated_session_scopes"] == ["regular"]
    assert artifact["input_provenance"]["shadow_observation_mode"] == "not_collected"

    # Fabricating a small latency field cannot turn a historical replay into
    # the required prospective 20-session observation.
    for row in offline_shadow["rows"]:
        if row["would_notify"]:
            row["telegram_enqueue_latency_seconds"] = 1.0
    replay_with_latency = calibrate_p0_2m(payload, n_boot=20)
    assert replay_with_latency["gate_results"]["forward_shadow_20_sessions"][
        "passed"
    ] is False
    assert replay_with_latency["activation_ready"] is False


def test_raw_export_rejects_source_label_mismatch(tmp_path):
    _write_one_symbol_export(tmp_path, bar_label="start")

    with pytest.raises(ValueError, match="source_bar_label_contract_mismatch"):
        build_calibration_input_from_export_dir(tmp_path)


def test_raw_export_checks_declared_hashes_and_adjustment(tmp_path):
    _write_one_symbol_export(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = {"TSLA": {"minute_sha256": "0" * 64}}
    manifest["adjustment"]["consistency_verified"] = False
    manifest_path.write_text(json.dumps(manifest))
    payload = build_calibration_input_from_export_dir(tmp_path)
    errors = payload["producer_metadata"]["derivation_errors"]
    assert "adjustment_consistency_not_verified" in errors
    assert "TSLA:minute:minute_file_hash_mismatch" in errors
    assert payload["events"] == []
    assert len(payload["coverage"]) == 60
    assert all(row["complete_minutes"] == 0 for row in payload["coverage"])


def test_raw_builder_does_not_replace_half_day_inside_prior_sixty():
    calendar = xcals.get_calendar("XNYS")
    target_day = datetime(2026, 12, 1, tzinfo=_ET).date()
    end = datetime(2026, 12, 1, 15, 0, tzinfo=_ET)

    selected = _prior_matching_sessions(
        calendar,
        target_day=target_day,
        end=end,
    )
    cursor = calendar.date_to_session(target_day, direction="none")
    exact_prior_sixty = []
    for _ in range(60):
        cursor = calendar.previous_session(cursor)
        exact_prior_sixty.append(cursor.date())
    exact_prior_sixty.reverse()

    assert datetime(2026, 11, 27, tzinfo=_ET).date() in exact_prior_sixty
    assert datetime(2026, 11, 27, tzinfo=_ET).date() not in selected
    assert selected == [
        day
        for day in exact_prior_sixty
        if day != datetime(2026, 11, 27, tzinfo=_ET).date()
    ]
    assert len(selected) == 59


def test_replay_windows_and_forward_outcomes_never_cross_rth_boundary(monkeypatch):
    import src.opportunities.p0_2m_calibration_input as producer

    calendar = xcals.get_calendar("XNYS")
    target = datetime(2026, 9, 10, tzinfo=_ET).date()
    dates = [stamp.date() for stamp in calendar.sessions_window(target, -61)]
    bars = {}
    for day in dates:
        start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=_ET)
        for index in range(230):
            opened = 100 + index * 0.2 if day == target else 100
            bars[start + timedelta(minutes=2 * index)] = {
                "open": opened, "close": opened + (0.2 if day == target else 0.01),
                "volume": 1000 if day == target else 100,
            }
    monkeypatch.setattr(producer, "_prior_atr14", lambda *args: (1.0, None))
    events, comparator, _ = _scan_regular_day(
        symbol="TSLA", day=target, bars=bars, daily={}, source="MoomooFetcher",
        universe_hash="test", calendar=calendar, baseline_session_cache={},
    )
    assert events and comparator
    reference_cache = {}
    cached = _scan_regular_day(
        symbol="TSLA", day=target, bars=bars, daily={}, source="MoomooFetcher",
        universe_hash="test", calendar=calendar, baseline_session_cache={},
        reference_window_cache=reference_cache,
    )
    assert cached[:2] == (events, comparator)
    assert reference_cache
    # Reusing references changes neither signal facts nor forward outcomes.
    again = _scan_regular_day(
        symbol="TSLA", day=target, bars=bars, daily={}, source="MoomooFetcher",
        universe_hash="test", calendar=calendar, baseline_session_cache={},
        reference_window_cache=reference_cache,
    )
    assert again == cached
    for rows in (events, comparator):
        assert min(datetime.fromisoformat(row["window_end"]).time()
                   for row in rows) >= datetime(2026, 9, 10, 9, 48).time()
        late = [row for row in rows
                if datetime.fromisoformat(row["window_end"]).hour == 16]
        assert late
        assert all(row["direction_adjusted_return_20m"] is None for row in late)


def test_future_20m_price_changes_outcome_not_signal_inputs(tmp_path):
    _write_one_symbol_export(tmp_path)
    original = build_calibration_input_from_export_dir(tmp_path)
    first = original["events"][0]

    frame = pd.read_csv(tmp_path / "TSLA_1m.csv")
    event_day = first["session_date"]
    future_mask = frame["date"].astype(str).str.startswith(
        (f"{event_day}T10:07", f"{event_day}T10:08")
    )
    frame.loc[future_mask, "close"] = frame.loc[future_mask, "open"] * 1.05
    frame.loc[future_mask, "high"] = frame.loc[future_mask, ["open", "close"]].max(
        axis=1
    )
    frame.loc[future_mask, "low"] = frame.loc[future_mask, ["open", "close"]].min(
        axis=1
    )
    frame.to_csv(tmp_path / "TSLA_1m.csv", index=False)

    changed = build_calibration_input_from_export_dir(tmp_path)["events"][0]
    for field in (
        "window_end",
        "trigger_price",
        "impulse_8m_log",
        "price_percentile",
        "rvol_8m",
        "displacement_atr",
    ):
        assert changed[field] == first[field]
    assert (
        changed["direction_adjusted_return_20m"]
        != first["direction_adjusted_return_20m"]
    )


def test_missing_symbol_files_are_explicit_derivation_errors(tmp_path):
    sessions = _selected_sessions("2025-12-01")
    manifest = {
        "schema_version": P0_2M_RAW_EXPORT_SCHEMA_VERSION,
        "selection_end_session": sessions[179].isoformat(),
        "source": "MoomooFetcher",
        "minute_bar_label": "end",
        "minute_timezone": "America/New_York",
        "universe": ["TSLA"],
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    payload = build_calibration_input_from_export_dir(tmp_path)
    errors = payload["producer_metadata"]["derivation_errors"]
    assert any("TSLA:minute" in item for item in errors)
    assert any("TSLA:daily" in item for item in errors)

    artifact = calibrate_p0_2m(payload, n_boot=10)
    gate = artifact["gate_results"]["input_integrity"]
    assert gate["passed"] is False
    assert gate["actual"]["producer_derivation_errors"] == errors


def test_cli_accepts_raw_export_and_writes_both_review_artifacts(tmp_path, capsys):
    _write_one_symbol_export(tmp_path)
    derived = tmp_path / "derived.json"
    artifact_path = tmp_path / "artifact.json"

    code = calibrate_main(
        [
            "--raw-export-dir",
            str(tmp_path),
            "--derived-input-output",
            str(derived),
            "--output",
            str(artifact_path),
            "--bootstrap-samples",
            "10",
        ]
    )

    assert code == 1
    assert derived.exists()
    assert artifact_path.exists()
    summary = json.loads(capsys.readouterr().out)
    assert summary["activation_ready"] is False
    assert summary["derived_input_output"] == str(derived.resolve())
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["shadow"]["observation_mode"] == "not_collected"
