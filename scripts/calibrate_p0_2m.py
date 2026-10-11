#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect, audit, and explicitly enroll the fixed exact-2m P0 experiment.

The default command performs no market-data fetch, configuration write, or
notification action. Explicit collection and enrollment modes are separate
from calibration. It accepts either a derived JSON/JSONL envelope or a raw export
directory containing a manifest plus per-symbol 1m/daily CSV files.  A raw
bars-only export cannot prove Telegram enqueue latency, so its activation gate
is intentionally false until real forward-shadow delivery evidence exists.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.journal.ledger.p0_2m_shadow_repository import (  # noqa: E402
    LIVE_FORWARD_SHADOW_MODE,
    P0_2M_SHADOW_EXPORT_SCHEMA_VERSION,
    attest_p0_2m_activation_artifact,
    compute_p0_2m_live_shadow_export_sha256,
    validate_p0_2m_shadow_enrollment_boundary,
    validate_p0_2m_live_shadow_export_attestation,
    start_new_p0_2m_live_shadow_run,
    prospective_p0_2m_shadow_sessions,
)
from src.opportunities.p0_2m_calibration import (  # noqa: E402
    DEFAULT_P0_UNIVERSE_69,
    DEFAULT_ASSET_CLASS_BY_SYMBOL,
    FROZEN_SIGNAL_THRESHOLDS,
    P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
    calibrate_p0_2m,
    compute_live_shadow_binding_sha256,
    compute_universe_hash,
    split_historical_sessions,
    validate_activation_artifact,
)
from src.opportunities.p0_2m_calibration_input import (  # noqa: E402
    build_calibration_input_from_export_dir,
    P0_2M_RAW_EXPORT_SCHEMA_VERSION,
    P0_2M_INPUT_PRODUCER_VERSION,
    is_auditable_adjustment_check,
    replay_saved_cases,
)
from src.opportunities.p0_2m_signal import P0_2M_SIGNAL_VERSION  # noqa: E402


_JSONL_COLLECTIONS = {
    "coverage": "coverage",
    "event": "events",
    "atr_only_event": "atr_only_events",
    "shadow": "shadow",
}
_ET = ZoneInfo("America/New_York")
_COLLECTION_SESSION_COUNT = 180
_COLLECTION_CHUNK_SESSIONS = 20


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _assert_collection_time() -> None:
    """Do not compete with the live 04:00–20:00 ET scan, including paging."""
    now = _now_utc().astimezone(_ET)
    if xcals.get_calendar("XNYS").is_session(now.date()) and time(4) <= now.time() < time(20):
        raise ValueError("history_collection_paused_during_0400_2000_ET_scan_window")


def _collection_manifest(root: Path, selection_end: str | None) -> dict[str, Any]:
    calendar = xcals.get_calendar("XNYS")
    now = _now_utc()
    latest = calendar.date_to_session(now.astimezone(_ET).date(), direction="previous")
    if calendar.session_close(latest).to_pydatetime() >= now:
        latest = calendar.previous_session(latest)
    path = root / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("collection_manifest_must_be_object")
        selected = str(manifest.get("selection_end_session"))
        if selection_end is not None and selected != selection_end:
            raise ValueError("collection_selection_end_is_frozen")
    else:
        if root.exists() and any(root.iterdir()):
            raise ValueError("new_collection_directory_must_be_empty")
        selected = selection_end or latest.date().isoformat()
        manifest = {}
    target = calendar.date_to_session(date.fromisoformat(selected), direction="none")
    if target > latest:
        raise ValueError("selection_end_session_must_be_completed")
    sessions = [stamp.date().isoformat() for stamp in calendar.sessions_window(target, -_COLLECTION_SESSION_COUNT)]
    identity = {
        "schema_version": P0_2M_RAW_EXPORT_SCHEMA_VERSION,
        "selection_end_session": selected,
        "source": "MoomooFetcher",
        "minute_bar_label": "end",
        "minute_timezone": "America/New_York",
        "universe": list(DEFAULT_P0_UNIVERSE_69),
        "universe_hash": compute_universe_hash(DEFAULT_P0_UNIVERSE_69),
        "signal_version": P0_2M_SIGNAL_VERSION,
        "signal_thresholds": dict(FROZEN_SIGNAL_THRESHOLDS),
        "historical_sessions": sessions,
    }
    if manifest:
        if any(manifest.get(key) != value for key, value in identity.items()):
            raise ValueError("collection_identity_does_not_match_frozen_manifest")
        if manifest.get("adjustment", {}).get("autype") != "QFQ":
            raise ValueError("collection_adjustment_mismatch")
        return manifest
    return {
        **identity,
        "frozen_at": now.isoformat(),
        "historical_independence": "exploratory_contamination",
        "adjustment": {
            "autype": "QFQ", "snapshot_at": now.isoformat(),
            "consistency_verified": False,
            "verification_basis": "collection_incomplete",
        },
        "files": {},
        "collection": {"status": "collecting", "symbols": {}},
    }


def _validate_collected_files(root: Path, manifest: Mapping[str, Any]) -> set[str]:
    completed = set(manifest.get("collection", {}).get("symbols", {}))
    if not completed.issubset(set(DEFAULT_P0_UNIVERSE_69)):
        raise ValueError("collection_contains_unfrozen_symbol")
    for symbol in completed:
        files = manifest.get("files", {}).get(symbol, {})
        for kind, suffix in (("minute", "1m"), ("daily", "daily")):
            expected = f"{symbol}_{suffix}.csv"
            path = root / expected
            if files.get(kind) != expected or path.is_symlink() or not path.is_file():
                raise ValueError(f"collection_file_missing_or_unsafe:{symbol}:{kind}")
            if _sha256_file(path) != files.get(f"{kind}_sha256"):
                raise ValueError(f"collection_file_hash_mismatch:{symbol}:{kind}")
    return completed


def _validate_minute_range(frame, start: str, end: str) -> None:
    import pandas as pd

    if frame.empty or not {"date", "open", "high", "low", "close", "volume", "_source_time_key_count"}.issubset(frame.columns):
        raise ValueError("provider_minute_frame_empty_or_missing_provenance")
    for value in frame["date"]:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            raise ValueError("provider_minute_timestamp_has_no_timezone")
        market_day = stamp.tz_convert(_ET).date().isoformat()
        if not start <= market_day <= end:
            raise ValueError("provider_minute_outside_requested_range")


def _preflight_collection(fetcher, manifest: Mapping[str, Any], completed: set[str]) -> dict[str, Any]:
    _assert_collection_time()
    quota = fetcher.get_history_quota()
    already = set(quota["downloaded_codes"])
    missing = [s for s in DEFAULT_P0_UNIVERSE_69 if s not in completed and f"US.{s}" not in already]
    if len(missing) > quota["remain_quota"]:
        raise ValueError(f"history_quota_insufficient:need={len(missing)}:remaining={quota['remain_quota']}")
    representatives: dict[str, str] = {}
    for symbol in DEFAULT_P0_UNIVERSE_69:
        representatives.setdefault(DEFAULT_ASSET_CLASS_BY_SYMBOL[symbol], symbol)
    probes = []
    sessions = manifest["historical_sessions"]
    for asset_class, symbol in representatives.items():
        for day in (sessions[0], sessions[-1]):
            frame = fetcher.fetch_intraday(symbol, "1m", start=day, end=day, before_request=_assert_collection_time)
            _validate_minute_range(frame, day, day)
            probes.append({"symbol": symbol, "asset_class": asset_class, "session": day, "rows": len(frame)})
    return {"checked_at": _now_utc().isoformat(), "quota": quota, "new_symbol_quota_needed": len(missing), "probes": probes}


def _atomic_write_csv(path: Path, frame) -> None:
    if path.is_symlink():
        raise ValueError("collection_output_must_not_be_symlink")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _adjustment_check(fetcher, symbol: str, minute, daily, manifest) -> dict[str, Any]:
    """Check price bases and preserve, rather than assume away, action risk."""
    import pandas as pd

    reasons: list[str] = []
    checked_sessions = 0
    calendar = xcals.get_calendar("XNYS")
    indexed = minute.assign(_time=pd.to_datetime(minute["date"], utc=True)).set_index("_time")
    for day in (manifest["historical_sessions"][0], manifest["historical_sessions"][-1]):
        rows = daily.loc[daily["date"].astype(str).str[:10] == day]
        opened, closed = calendar.session_open(day), calendar.session_close(day)
        rth = indexed.loc[(indexed.index > opened) & (indexed.index <= closed)]
        if len(rows) != 1 or rth.empty or rth.index[0] != opened + pd.Timedelta(minutes=1) or rth.index[-1] != closed:
            reasons.append(f"price_basis_sample_incomplete:{day}")
            continue
        row = rows.iloc[0]
        tolerance = max(0.01, abs(float(row["close"])) * 1e-6)
        if (abs(float(rth.iloc[0]["open"]) - float(row["open"])) > tolerance
                or abs(float(rth.iloc[-1]["close"]) - float(row["close"])) > tolerance
                or float(rth["high"].max()) > float(row["high"]) + tolerance
                or float(rth["low"].min()) < float(row["low"]) - tolerance):
            reasons.append(f"minute_daily_price_basis_mismatch:{day}")
        checked_sessions += 1
    actions: list[dict[str, Any]] = []
    try:
        _assert_collection_time()
        actions = fetcher.get_adjustment_events(symbol)
        earliest_daily = str(daily["date"].min())[:10]
        for action in actions:
            action_day = str(action.get("ex_div_date") or "")[:10]
            try:
                date.fromisoformat(action_day)
            except ValueError:
                reasons.append("corporate_action_date_unverifiable")
                break
            if action_day >= earliest_daily:
                reasons.append("corporate_actions_require_point_in_time_reconstruction")
                break
    except Exception as exc:
        reasons.append(f"corporate_action_evidence_unavailable:{type(exc).__name__}")
    return {"consistency_verified": checked_sessions == 2 and not reasons,
            "checked_sessions": checked_sessions, "reasons": reasons,
            "corporate_actions": actions}


def collect_moomoo_history(root: Path, *, selection_end: str | None = None, preflight_only: bool = False, fetcher=None) -> dict[str, Any]:
    """Resume at symbol boundaries; no partial data or DB row is accepted."""
    import pandas as pd
    from data_provider.moomoo_fetcher import MoomooFetcher, MoomooHistoryEmptyError

    root = root.expanduser().resolve()
    _assert_collection_time()
    manifest = _collection_manifest(root, selection_end)
    completed = _validate_collected_files(root, manifest)
    if not preflight_only and len(completed) == len(DEFAULT_P0_UNIVERSE_69) and manifest["collection"].get("status") == "complete":
        return {"mode": "collection", "selection_end_session": manifest["selection_end_session"], "completed_symbols": len(completed), "adjustment_consistency_verified": manifest["adjustment"]["consistency_verified"]}
    owned_fetcher = fetcher is None
    if owned_fetcher:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        fetcher = MoomooFetcher()
        if not fetcher.enabled or not fetcher._sdk_ok:
            raise ValueError("moomoo_collection_requires_enabled_usable_provider")
    try:
        preflight = _preflight_collection(fetcher, manifest, completed)
        if preflight_only:
            return {"mode": "preflight", "selection_end_session": manifest["selection_end_session"], "universe_hash": manifest["universe_hash"], "completed_symbols": len(completed), **preflight}
        root.mkdir(parents=True, exist_ok=True)
        manifest["collection"]["preflight"] = preflight
        _atomic_write_json(root / "manifest.json", manifest)
        sessions = manifest["historical_sessions"]
        for symbol in DEFAULT_P0_UNIVERSE_69:
            if symbol in completed:
                continue
            frames, chunks = [], []
            for offset in range(0, len(sessions), _COLLECTION_CHUNK_SESSIONS):
                block = sessions[offset:offset + _COLLECTION_CHUNK_SESSIONS]
                try:
                    frame = fetcher.fetch_intraday(symbol, "1m", start=block[0], end=block[-1], before_request=_assert_collection_time)
                except MoomooHistoryEmptyError:
                    chunks.append({"start": block[0], "end": block[-1], "rows": 0, "status": "provider_empty"})
                    continue
                _validate_minute_range(frame, block[0], block[-1])
                frames.append(frame)
                chunks.append({"start": block[0], "end": block[-1], "rows": len(frame), "pages": frame.attrs.get("page_count"), "status": "downloaded"})
            minute = (
                pd.concat(frames, ignore_index=True).sort_values("date", kind="stable")
                if frames else pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "_source_time_key_count"])
            )
            _assert_collection_time()
            daily_start = (date.fromisoformat(sessions[0]) - timedelta(days=140)).isoformat()
            daily_status = "downloaded"
            try:
                daily_raw = fetcher._fetch_raw_data(symbol, daily_start, sessions[-1])
                daily = fetcher._normalize_data(daily_raw, symbol)
            except MoomooHistoryEmptyError:
                daily = pd.DataFrame(columns=["date", "open", "high", "low", "close"])
                daily_status = "provider_empty"
            if (daily.empty and daily_status != "provider_empty") or not {"date", "open", "high", "low", "close"}.issubset(daily.columns):
                raise ValueError(f"daily_history_empty_or_invalid:{symbol}")
            for value in daily["date"]:
                day = date.fromisoformat(str(value)[:10]).isoformat()
                if not daily_start <= day <= sessions[-1]:
                    raise ValueError(f"provider_daily_outside_requested_range:{symbol}")
            adjustment = _adjustment_check(fetcher, symbol, minute, daily, manifest)
            minute_path, daily_path = root / f"{symbol}_1m.csv", root / f"{symbol}_daily.csv"
            _atomic_write_csv(minute_path, minute)
            _atomic_write_csv(daily_path, daily)
            manifest["files"][symbol] = {"minute": minute_path.name, "daily": daily_path.name,
                                         "minute_sha256": _sha256_file(minute_path), "daily_sha256": _sha256_file(daily_path)}
            manifest["collection"]["symbols"][symbol] = {"completed_at": _now_utc().isoformat(), "minute_rows": len(minute), "daily_rows": len(daily), "daily_status": daily_status, "chunks": chunks, "adjustment_check": adjustment}
            _atomic_write_json(root / "manifest.json", manifest)
        verified = all(row["adjustment_check"]["consistency_verified"] for row in manifest["collection"]["symbols"].values())
        manifest["adjustment"].update({"consistency_verified": verified, "verification_basis": "minute_daily_endpoints_and_provider_corporate_actions" if verified else "see_collection_symbols_adjustment_check_reasons"})
        manifest["collection"]["status"] = "complete"
        _atomic_write_json(root / "manifest.json", manifest)
        return {"mode": "collection", "selection_end_session": manifest["selection_end_session"], "completed_symbols": len(manifest["collection"]["symbols"]), "adjustment_consistency_verified": verified}
    finally:
        if owned_fetcher:
            fetcher.close()


def start_shadow_from_input(path: Path, *, reason: str) -> dict[str, Any]:
    """Bind one explicit prospective run to the exact frozen input bytes."""
    if not reason.strip():
        raise ValueError("start_shadow_requires_restart_reason")
    input_hash = _sha256_file(path)
    payload = load_input(path)
    if _sha256_file(path) != input_hash:
        raise ValueError("frozen_input_changed_during_enrollment")
    if payload.get("schema_version") != P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION:
        raise ValueError("start_shadow_input_schema_mismatch")
    universe = _normalise_universe(payload.get("universe"), field_name="frozen input universe")
    if universe != tuple(DEFAULT_P0_UNIVERSE_69):
        raise ValueError("start_shadow_requires_frozen_69_symbol_universe")
    if payload.get("universe_hash") != compute_universe_hash(universe) or payload.get("signal_version") != P0_2M_SIGNAL_VERSION:
        raise ValueError("start_shadow_input_identity_mismatch")
    split = split_historical_sessions(payload.get("historical_sessions") or ())
    if [len(split[key]) for key in ("baseline", "development", "holdout")] != [60, 80, 40] or split["invalid_dates"]:
        raise ValueError("start_shadow_requires_complete_60_80_40_input")
    observed = _now_utc()
    prospective_p0_2m_shadow_sessions(observed)
    calendar = xcals.get_calendar("XNYS")
    if observed <= calendar.session_close(split["holdout"][-1]).to_pydatetime():
        raise ValueError("shadow_enrollment_must_follow_holdout_close")
    metadata = payload.get("producer_metadata") or {}
    if metadata.get("derivation_errors") != []:
        raise ValueError("start_shadow_rejects_derivation_errors")
    if (metadata.get("producer_version") != P0_2M_INPUT_PRODUCER_VERSION
            or metadata.get("raw_export_schema_version") != P0_2M_RAW_EXPORT_SCHEMA_VERSION
            or metadata.get("source") != "MoomooFetcher"
            or metadata.get("selection_end_session") != split["holdout"][-1]
            or not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("manifest_sha256") or ""))):
        raise ValueError("start_shadow_requires_frozen_raw_export_provenance")
    frozen = _aware_timestamp(metadata.get("frozen_at"), field_name="frozen_at")
    if not calendar.session_close(split["holdout"][-1]).to_pydatetime() < frozen <= observed:
        raise ValueError("start_shadow_invalid_freeze_boundary")
    adjustment = metadata.get("adjustment") or {}
    adjustment_scope = metadata.get("adjustment_validation_scope")
    if adjustment_scope not in {None, "legacy_snapshot", "per_symbol_collection"}:
        raise ValueError("start_shadow_adjustment_scope_unknown")
    per_symbol_adjustment = adjustment_scope == "per_symbol_collection"
    if adjustment.get("autype") != "QFQ" or (
        not per_symbol_adjustment and adjustment.get("consistency_verified") is not True
    ):
        raise ValueError("start_shadow_adjustment_unverified")
    if per_symbol_adjustment and (
        not isinstance(adjustment.get("consistency_verified"), bool)
        or not adjustment.get("snapshot_at") or not adjustment.get("verification_basis")
    ):
        raise ValueError("start_shadow_adjustment_snapshot_missing")
    if set(metadata.get("per_symbol", {})) != set(universe):
        raise ValueError("start_shadow_symbol_provenance_incomplete")
    for symbol in universe:
        provenance = metadata.get("per_symbol", {}).get(symbol, {})
        if any(not re.fullmatch(r"[a-f0-9]{64}", str(provenance.get(kind, {}).get("sha256") or "")) for kind in ("minute", "daily")):
            raise ValueError(f"start_shadow_missing_raw_file_provenance:{symbol}")
        if per_symbol_adjustment:
            check = provenance.get("adjustment")
            if (
                not is_auditable_adjustment_check(check)
                or provenance.get("evidence_eligible") is not check["consistency_verified"]
                or provenance.get("evidence_exclusion_reasons") != check["reasons"]
            ):
                raise ValueError(f"start_shadow_symbol_adjustment_unverifiable:{symbol}")
    record = start_new_p0_2m_live_shadow_run(
        symbols=universe, universe_hash=compute_universe_hash(universe),
        signal_version=P0_2M_SIGNAL_VERSION, restart_request_id=input_hash,
        restart_reason=reason, enrollment_started_at=observed,
    )
    return asdict(record)


def _merge_meta(payload: dict[str, Any], row: Mapping[str, Any]) -> None:
    for key in (
        "schema_version",
        "universe",
        "asset_classes",
        "historical_sessions",
        "shadow_sessions",
        "shadow_observation_mode",
        "shadow_enrollment",
    ):
        if key in row:
            payload[key] = row[key]


def load_input(path: Path) -> dict[str, Any]:
    """Load one JSON envelope or typed JSONL records."""

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".jsonl":
        loaded = json.loads(text)
        if isinstance(loaded, Mapping):
            return dict(loaded)
        if not isinstance(loaded, list):
            raise ValueError("JSON input must be an object or typed row array")
        rows = loaded
    else:
        rows = [
            json.loads(line)
            for raw in text.splitlines()
            if (line := raw.strip()) and not line.startswith("#")
        ]

    payload: dict[str, Any] = {
        "schema_version": P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
        "coverage": [],
        "events": [],
        "atr_only_events": [],
        "shadow": [],
    }
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("each JSONL row must be an object")
        kind = str(row.get("kind") or row.get("record_type") or "").strip().lower()
        if kind in {"meta", "metadata"}:
            _merge_meta(payload, row)
            continue
        collection = _JSONL_COLLECTIONS.get(kind)
        if collection is None:
            raise ValueError(f"unsupported JSONL record kind: {kind or '<missing>'}")
        clean = dict(row)
        clean.pop("kind", None)
        clean.pop("record_type", None)
        payload[collection].append(clean)
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_live_shadow_input(path: Path) -> dict[str, Any]:
    """Load one JSON envelope emitted by the live-forward exporter."""

    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError("live shadow input must be one JSON object")
    return dict(loaded)


def _normalise_universe(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a symbol array")
    symbols = tuple(str(item or "").strip().upper() for item in value)
    if not symbols or any(not symbol for symbol in symbols):
        raise ValueError(f"{field_name} contains a blank symbol")
    if len(set(symbols)) != len(symbols):
        raise ValueError(f"{field_name} contains duplicate symbols")
    return symbols


def _validate_live_shadow_sessions(
    values: Any,
    *,
    historical_sessions: Any,
) -> list[str]:
    if not isinstance(values, list) or len(values) != 20:
        raise ValueError("live shadow export must contain exactly 20 sessions")
    try:
        parsed = [date.fromisoformat(str(value)) for value in values]
    except ValueError as exc:
        raise ValueError("live shadow session must be YYYY-MM-DD") from exc
    if len(set(parsed)) != len(parsed) or parsed != sorted(parsed):
        raise ValueError("live shadow sessions must be unique and chronological")
    calendar = xcals.get_calendar("XNYS")
    if any(not bool(calendar.is_session(day)) for day in parsed):
        raise ValueError("live shadow sessions must all be XNYS sessions")

    split = split_historical_sessions(historical_sessions or ())
    if (
        len(split["baseline"]) != 60
        or len(split["development"]) != 80
        or len(split["holdout"]) != 40
        or split["invalid_dates"]
    ):
        raise ValueError("base input does not contain a valid locked 60/80/40 split")
    holdout_end = date.fromisoformat(split["holdout"][-1])
    if parsed[0] <= holdout_end:
        raise ValueError(
            "live shadow sessions must be the next 20 consecutive XNYS "
            "sessions from its enrolled start, and start after the locked holdout"
        )
    expected = [
        stamp.date()
        for stamp in calendar.sessions_window(
            calendar.date_to_session(parsed[0], direction="none"), 20
        )
    ]
    if len(expected) != 20 or parsed != expected:
        raise ValueError(
            "live shadow sessions must be the next 20 consecutive XNYS "
            "sessions from its enrolled start, and start after the locked holdout"
        )
    return [day.isoformat() for day in parsed]


def _aware_timestamp(value: Any, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"live shadow enrollment {field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"live shadow enrollment {field_name} must be timezone-aware")
    return parsed


def _validate_live_shadow_enrollment(
    value: Any,
    *,
    universe: tuple[str, ...],
    universe_hash: str,
    shadow_sessions: list[str],
    historical_sessions: Any,
    attested_enrollment_key: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("live shadow export enrollment is missing")
    if value.get("observation_mode") != LIVE_FORWARD_SHADOW_MODE:
        raise ValueError("live shadow enrollment observation_mode mismatch")
    if value.get("signal_version") != P0_2M_SIGNAL_VERSION:
        raise ValueError("live shadow enrollment signal_version mismatch")
    if str(value.get("universe_hash") or "") != universe_hash:
        raise ValueError("live shadow enrollment universe_hash mismatch")
    enrollment_universe = _normalise_universe(
        value.get("universe"), field_name="live shadow enrollment universe"
    )
    if len(enrollment_universe) != len(DEFAULT_P0_UNIVERSE_69):
        raise ValueError("live shadow enrollment must contain exactly 69 symbols")
    if enrollment_universe != universe:
        raise ValueError("live shadow enrollment universe mismatch")
    enrollment_sessions = value.get("sessions")
    if not isinstance(enrollment_sessions, list) or enrollment_sessions != shadow_sessions:
        raise ValueError("live shadow enrollment sessions mismatch")
    enrollment_key = str(value.get("enrollment_key") or "").strip()
    # Exports issued before run-aware enrollment did not repeat the key inside
    # the enrollment object.  Their DB attestation already binds it, so inject
    # that attested value for backward compatibility; any present value must
    # match exactly.
    if not enrollment_key:
        enrollment_key = attested_enrollment_key
    if not re.fullmatch(r"[0-9a-f]{64}", enrollment_key):
        raise ValueError("live shadow enrollment_key is invalid")
    if enrollment_key != attested_enrollment_key:
        raise ValueError("live shadow enrollment_key does not match attestation")
    run_request_id = str(value.get("run_request_id") or "").strip() or None
    restart_reason = str(value.get("restart_reason") or "").strip() or None
    supersedes = (
        str(value.get("supersedes_enrollment_key") or "").strip() or None
    )
    if run_request_id is not None and not re.fullmatch(
        r"[A-Za-z0-9._:-]{1,64}", run_request_id
    ):
        raise ValueError("live shadow enrollment run_request_id is invalid")
    if supersedes is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", supersedes):
            raise ValueError("live shadow supersedes_enrollment_key is invalid")
        if supersedes == enrollment_key:
            raise ValueError("live shadow enrollment cannot supersede itself")
        if run_request_id is None or restart_reason is None:
            raise ValueError("superseding live shadow enrollment metadata is incomplete")

    enrolled_at = _aware_timestamp(
        value.get("enrolled_at"), field_name="enrolled_at"
    )
    created_at = _aware_timestamp(value.get("created_at"), field_name="created_at")
    split = split_historical_sessions(historical_sessions or ())
    holdout_end = date.fromisoformat(split["holdout"][-1])
    calendar = xcals.get_calendar("XNYS")
    holdout_close = calendar.session_close(holdout_end).to_pydatetime()
    if enrolled_at <= holdout_close:
        raise ValueError("live shadow enrollment must be after the holdout close")
    validate_p0_2m_shadow_enrollment_boundary(
        enrolled_at=enrolled_at,
        created_at=created_at,
        sessions=shadow_sessions,
    )

    result = deepcopy(dict(value))
    result.update(
        observation_mode=LIVE_FORWARD_SHADOW_MODE,
        universe_hash=universe_hash,
        signal_version=P0_2M_SIGNAL_VERSION,
        universe=list(universe),
        sessions=list(shadow_sessions),
        enrollment_key=enrollment_key,
        enrolled_at=enrolled_at.isoformat(),
        created_at=created_at.isoformat(),
    )
    return result


def merge_live_shadow_input(
    calibration_input: Mapping[str, Any],
    live_shadow: Mapping[str, Any],
    *,
    source_sha256: str | None = None,
    base_input_sha256: str | None = None,
) -> dict[str, Any]:
    """Merge an identity-matched live exporter payload into calibration input.

    Quality/coverage defects remain rows for the calibration gates to report;
    identity, provenance mode, and pre-registration boundary mismatches are
    rejected before any existing shadow fields are replaced.
    """

    if not isinstance(calibration_input, Mapping):
        raise TypeError("calibration input must be an object")
    if not isinstance(live_shadow, Mapping):
        raise TypeError("live shadow input must be an object")
    if (
        live_shadow.get("schema_version")
        != P0_2M_SHADOW_EXPORT_SCHEMA_VERSION
    ):
        raise ValueError("live shadow export schema_version mismatch")
    if live_shadow.get("shadow_observation_mode") != LIVE_FORWARD_SHADOW_MODE:
        raise ValueError("live shadow export is not live_forward_shadow")
    if live_shadow.get("signal_version") != P0_2M_SIGNAL_VERSION:
        raise ValueError("live shadow signal_version mismatch")
    base_universe = _normalise_universe(
        list(calibration_input.get("universe") or DEFAULT_P0_UNIVERSE_69),
        field_name="base universe",
    )
    exported_universe = _normalise_universe(
        live_shadow.get("universe"), field_name="live shadow universe"
    )
    if exported_universe != base_universe:
        raise ValueError("live shadow universe does not exactly match base universe")
    expected_hash = compute_universe_hash(base_universe)
    base_hash = calibration_input.get("universe_hash")
    if base_hash is not None and str(base_hash) != expected_hash:
        raise ValueError("base universe_hash does not match base universe")
    if str(live_shadow.get("universe_hash") or "") != expected_hash:
        raise ValueError("live shadow universe_hash does not match base universe")
    base_signal_version = calibration_input.get("signal_version")
    if (
        base_signal_version is not None
        and str(base_signal_version) != P0_2M_SIGNAL_VERSION
    ):
        raise ValueError("base signal_version does not match strict 2m runtime")

    shadow_sessions = _validate_live_shadow_sessions(
        live_shadow.get("shadow_sessions"),
        historical_sessions=calibration_input.get("historical_sessions"),
    )
    export_attestation = live_shadow.get("export_attestation")
    if not isinstance(export_attestation, Mapping):
        raise ValueError("live shadow export attestation is missing")
    attested_enrollment_key = str(
        export_attestation.get("enrollment_key") or ""
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", attested_enrollment_key):
        raise ValueError("live shadow export attestation enrollment_key is invalid")
    enrollment = _validate_live_shadow_enrollment(
        live_shadow.get("enrollment"),
        universe=base_universe,
        universe_hash=expected_hash,
        shadow_sessions=shadow_sessions,
        historical_sessions=calibration_input.get("historical_sessions"),
        attested_enrollment_key=attested_enrollment_key,
    )
    run_request_id = str(enrollment.get("run_request_id") or "")
    if re.fullmatch(r"[0-9a-f]{64}", run_request_id):
        if base_input_sha256 is None:
            raise ValueError("hash-bound shadow requires the frozen --input file; raw-export reconstruction is not the enrolled input")
        if base_input_sha256 != run_request_id:
            raise ValueError("live shadow base_input_sha256 does not match enrolled input")
    shadow_rows = live_shadow.get("shadow")
    if not isinstance(shadow_rows, list):
        raise ValueError("live shadow export shadow must be an array")
    session_set = set(shadow_sessions)
    universe_set = set(base_universe)
    merged_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(shadow_rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"live shadow row {index} must be an object")
        row = dict(raw)
        if row.get("observation_mode") != LIVE_FORWARD_SHADOW_MODE:
            raise ValueError(f"live shadow row {index} is not live_forward_shadow")
        if row.get("signal_version") != P0_2M_SIGNAL_VERSION:
            raise ValueError(f"live shadow row {index} signal_version mismatch")
        if str(row.get("universe_hash") or "") != expected_hash:
            raise ValueError(f"live shadow row {index} universe_hash mismatch")
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol not in universe_set or symbol != row.get("symbol"):
            raise ValueError(f"live shadow row {index} symbol mismatch")
        if str(row.get("session_date") or "") not in session_set:
            raise ValueError(f"live shadow row {index} session_date mismatch")
        if row.get("session_scope") not in {"regular", "rth"}:
            raise ValueError(f"live shadow row {index} session_scope mismatch")
        row_enrollment_key = str(row.get("enrollment_key") or "").strip()
        if row_enrollment_key and row_enrollment_key != attested_enrollment_key:
            raise ValueError(f"live shadow row {index} enrollment_key mismatch")
        row["enrollment_key"] = attested_enrollment_key
        merged_rows.append(row)

    actual_export_sha256 = compute_p0_2m_live_shadow_export_sha256(live_shadow)
    if export_attestation.get("export_sha256") != actual_export_sha256:
        raise ValueError("live shadow export attestation hash mismatch")
    attested, attestation_reason = validate_p0_2m_live_shadow_export_attestation(
        export_attestation,
        expected_universe_hash=expected_hash,
        expected_signal_version=P0_2M_SIGNAL_VERSION,
    )
    if not attested:
        raise ValueError(
            f"live shadow export attestation rejected: {attestation_reason}"
        )

    result = deepcopy(dict(calibration_input))
    result["shadow_observation_mode"] = LIVE_FORWARD_SHADOW_MODE
    result["shadow_sessions"] = shadow_sessions
    result["shadow_enrollment"] = enrollment
    result["shadow"] = merged_rows
    shadow_binding_sha256 = compute_live_shadow_binding_sha256(result)
    metadata = (
        dict(result.get("producer_metadata"))
        if isinstance(result.get("producer_metadata"), Mapping)
        else {}
    )
    # Calibration treats a present producer metadata object as an explicit
    # derivation audit record; preserve the required empty-error collection
    # for a precomputed base that did not previously have producer metadata.
    metadata.setdefault("derivation_errors", [])
    metadata["live_shadow_merge"] = {
        "schema_version": P0_2M_SHADOW_EXPORT_SCHEMA_VERSION,
        "observation_mode": LIVE_FORWARD_SHADOW_MODE,
        "signal_version": P0_2M_SIGNAL_VERSION,
        "universe_hash": expected_hash,
        "enrollment_key": attested_enrollment_key,
        "enrollment": enrollment,
        "source_sha256": source_sha256,
        "base_input_sha256": base_input_sha256,
        "live_shadow_export_sha256": actual_export_sha256,
        "shadow_binding_sha256": shadow_binding_sha256,
        "export_attestation": dict(export_attestation),
    }
    result["producer_metadata"] = metadata
    return result


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the fixed 60/80/40 historical split and 20-session "
            "forward shadow gates for exact-2m P0. Offline by default; "
            "explicit collection/enrollment never enables mobile delivery."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        type=Path,
        help="Already-derived JSON or typed JSONL calibration envelope",
    )
    source.add_argument(
        "--raw-export-dir",
        type=Path,
        help=(
            "Directory with manifest.json and per-symbol <SYMBOL>_1m.csv / "
            "<SYMBOL>_daily.csv exports; collection requires --collect-moomoo"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--collect-moomoo", action="store_true", help="Collect/resume the frozen 69-symbol history outside scan hours, then exit")
    mode.add_argument("--preflight-moomoo", action="store_true", help="Read quota and six history samples without saving files")
    mode.add_argument("--start-shadow", action="store_true", help="Explicitly enroll a new forward run bound to --input; writes the journal enrollment only")
    mode.add_argument("--replay-cases", action="store_true", help="Replay frozen TSLA/META cases from hashed raw files through every live/batch RTH endpoint; no fetch or enrollment")
    parser.add_argument("--selection-end-session", help="Fixed completed XNYS t0 for collection; defaults to latest completed day on first collection")
    parser.add_argument("--restart-reason", help="Required explanation for --start-shadow; the prior run is retained")
    parser.add_argument("--recheck-adjustment", action="store_true", help="Offline saved-factor/price/volume/invariance review while deriving --raw-export-dir; never edits raw exports")
    parser.add_argument(
        "--derived-input-output",
        type=Path,
        help=(
            "Optional path for the auditable derived input envelope. "
            "Useful with --raw-export-dir before reviewing the artifact."
        ),
    )
    parser.add_argument(
        "--live-shadow-input",
        type=Path,
        help=(
            "Optional p0_2m_live_shadow_export_v1 JSON from the append-only "
            "live-forward exporter. Identity, persisted enrollment, and the "
            "exact next-20-XNYS-session block are verified before it replaces "
            "the base shadow fields."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional artifact path. Omit to print the full artifact to stdout.",
    )
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=4000,
        help="Seeded daily-cluster bootstrap replicates (default: 4000)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Bootstrap RNG seed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.bootstrap_samples < 1:
        print("error: --bootstrap-samples must be positive", file=sys.stderr)
        return 2
    collecting = args.collect_moomoo or args.preflight_moomoo
    if ((collecting and args.raw_export_dir is None)
            or (args.start_shadow and args.input is None)
            or (args.selection_end_session is not None and not collecting)
            or (args.restart_reason is not None and not args.start_shadow)
            or (args.recheck_adjustment and (args.raw_export_dir is None or collecting or args.start_shadow))
            or (args.replay_cases and (args.raw_export_dir is None or args.recheck_adjustment or args.derived_input_output or args.live_shadow_input))
            or ((collecting or args.start_shadow) and (args.output or args.derived_input_output or args.live_shadow_input))):
        print("error: collection needs --raw-export-dir; enrollment needs --input; mode-specific flags cannot be mixed", file=sys.stderr)
        return 2
    if args.replay_cases:
        try:
            result = replay_saved_cases(args.raw_export_dir)
            if args.output is not None:
                _atomic_write_json(args.output, result)
            else:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result["live_batch_equivalent"] else 1
        except (OSError, TypeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if collecting or args.start_shadow:
        try:
            result = (
                collect_moomoo_history(args.raw_export_dir, selection_end=args.selection_end_session, preflight_only=args.preflight_moomoo)
                if collecting else start_shadow_from_input(args.input.expanduser().resolve(), reason=args.restart_reason or "")
            )
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    try:
        base_input_sha256 = None
        if args.raw_export_dir is not None:
            calibration_input = build_calibration_input_from_export_dir(
                args.raw_export_dir.expanduser().resolve(),
                recheck_adjustment=args.recheck_adjustment,
            )
        else:
            base_input_path = args.input.expanduser().resolve()
            base_input_sha256 = _sha256_file(base_input_path)
            calibration_input = load_input(base_input_path)
            if _sha256_file(base_input_path) != base_input_sha256:
                raise ValueError("frozen_input_changed_during_read")
        producer_metadata = calibration_input.get("producer_metadata")
        producer_metadata = producer_metadata if isinstance(producer_metadata, Mapping) else {}
        existing_merge = producer_metadata.get("live_shadow_merge")
        if args.live_shadow_input is not None and "live_shadow_merge" in producer_metadata:
            raise ValueError("--live-shadow-input requires an unmerged frozen base --input; nested shadow merges are not allowed")
        existing_enrollments = (
            calibration_input.get("shadow_enrollment"),
            existing_merge.get("enrollment") if isinstance(existing_merge, Mapping) else None,
        )
        if args.live_shadow_input is None and any(
            isinstance(enrollment, Mapping)
            and re.fullmatch(r"[0-9a-f]{64}", str(enrollment.get("run_request_id") or ""))
            for enrollment in existing_enrollments
        ):
            # A saved merge's self-reported hash cannot authenticate its current
            # historical rows. Re-read the enrolled bytes and merge the
            # independently attested export on every calibration invocation.
            raise ValueError("hash-bound shadow requires frozen base --input plus independent --live-shadow-input; premerged input cannot be calibrated")
        if args.live_shadow_input is not None:
            live_shadow_path = args.live_shadow_input.expanduser().resolve()
            live_shadow = load_live_shadow_input(live_shadow_path)
            calibration_input = merge_live_shadow_input(
                calibration_input,
                live_shadow,
                source_sha256=_sha256_file(live_shadow_path),
                base_input_sha256=base_input_sha256,
            )
        if args.derived_input_output is not None:
            _atomic_write_json(args.derived_input_output, calibration_input)
        artifact = calibrate_p0_2m(
            calibration_input,
            n_boot=args.bootstrap_samples,
            seed=args.seed,
        )
        if artifact["activation_ready"] is True:
            attest_p0_2m_activation_artifact(artifact)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output is not None:
        try:
            _atomic_write_json(args.output, artifact)
        except OSError as exc:
            print(f"error: could not write artifact: {exc}", file=sys.stderr)
            return 2
        valid, reason = validate_activation_artifact(
            args.output,
            expected_universe_hash=artifact["universe_hash"],
            expected_signal_version=artifact["signal_version"],
        )
        print(
            json.dumps(
                {
                    "output": str(args.output.expanduser().resolve()),
                    "derived_input_output": (
                        str(args.derived_input_output.expanduser().resolve())
                        if args.derived_input_output is not None
                        else None
                    ),
                    "live_shadow_input": (
                        str(args.live_shadow_input.expanduser().resolve())
                        if args.live_shadow_input is not None
                        else None
                    ),
                    "activation_ready": artifact["activation_ready"],
                    "runtime_validation": {"valid": valid, "reason": reason},
                    "universe_hash": artifact["universe_hash"],
                    "failed_gates": [
                        name
                        for name, result in artifact["gate_results"].items()
                        if result["passed"] is not True
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if artifact["activation_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
