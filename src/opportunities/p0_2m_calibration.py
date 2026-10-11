# -*- coding: utf-8 -*-
"""Locked calibration contract for the exact two-minute P0 signal.

The module is intentionally offline and pure.  It consumes exported coverage,
event, comparator, and forward-shadow observations; it never fetches quotes,
changes configuration, or enables mobile delivery.  A runtime caller may trust
an artifact only through :func:`validate_activation_artifact`.

Input payload (``p0_2m_calibration_input_v1``) keys:

``universe``
    Frozen symbol list.  Omission selects :data:`DEFAULT_P0_UNIVERSE_69`.
``asset_classes``
    Optional symbol -> ``stock`` / ``ordinary_etf`` /
    ``leveraged_inverse_etf`` mapping.  The frozen universe has a built-in map;
    any other unmapped symbol is ``unknown`` and remains shadow-only.
``historical_sessions``
    At least 180 XNYS session dates.  The most recent 180 are locked as
    60 baseline + 80 development + 40 holdout sessions.
``coverage``
    Per-symbol/per-day rows with ``complete_minutes`` and
    ``eligible_minutes`` (or ``minute_completeness``).  All 60 baseline dates
    and >=98% aggregate minute completeness are required for a symbol.
``events``
    Exact-2m signal rows.  A row may be a flattened signal payload or contain
    one under ``evidence``.  Holdout evaluation uses only qualified RTH rows.
    ``direction_adjusted_return_20m`` is the signed 20-minute forward return.
``atr_only_events``
    Qualified RTH events from the comparator that applies only the 0.5-ATR
    displacement condition, with the same forward-return field.
``shadow_sessions`` / ``shadow``
    Exactly 20 consecutive XNYS sessions beginning after holdout plus scan
    observations.  Shadow rows expose availability counts,
    duplicate/unfinished-bar violations, a trusted enqueue-evidence mode, and
    latency (directly or through ``window_end``/``telegram_enqueued_at``).
``shadow_observation_mode``
    Must be the explicit value ``live_forward_shadow``.  Historical/static
    replay can populate diagnostics but can never satisfy the prospective
    shadow gate.
``shadow_enrollment``
    Prospective enrollment copied from the append-only live exporter.  Its
    identity and exact 20-session block must match the input; aware
    enrollment/persistence timestamps must fall after holdout close and no
    later than the first shadow open.

No threshold selection is performed on the locked holdout.  All gates are
fixed constants below; a failing or unprovable gate is ``False``.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
from typing import Any, Iterable, Mapping, Optional, Sequence

import exchange_calendars as xcals

from src.opportunities.p0_2m_signal import (
    P0_2M_BAR_MINUTES,
    P0_2M_DISPLACEMENT_BARS,
    P0_2M_EPISODE_MINUTES,
    P0_2M_FORMING_DISPLACEMENT_ATR_MIN,
    P0_2M_IMPULSE_BARS,
    P0_2M_PRICE_PERCENTILE_MIN,
    P0_2M_REFERENCE_SESSIONS,
    P0_2M_RVOL_MIN,
    P0_2M_SIGNAL_VERSION,
    P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
    is_duplicate_p0_episode,
)

P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION = "p0_2m_calibration_input_v1"
P0_2M_CALIBRATION_SCHEMA_VERSION = "p0_2m_calibration_v1"

BASELINE_SESSION_COUNT = 60
DEVELOPMENT_SESSION_COUNT = 80
HOLDOUT_SESSION_COUNT = 40
HISTORICAL_SESSION_COUNT = (
    BASELINE_SESSION_COUNT + DEVELOPMENT_SESSION_COUNT + HOLDOUT_SESSION_COUNT
)
SHADOW_SESSION_COUNT = 20
LIVE_FORWARD_SHADOW_MODE = "live_forward_shadow"

EXPECTED_UNIVERSE_SIZE = 69
MIN_BASELINE_READY_SYMBOLS = 56
MIN_MINUTE_COMPLETENESS = 0.98
MIN_HOLDOUT_EVENTS = 30
MIN_HOLDOUT_EVENT_DAYS = 20
MIN_WEEKLY_MEDIAN_EVENTS = 3.0
MAX_WEEKLY_MEDIAN_EVENTS = 7.0
MAX_BUDGET_SUPPRESSION_RATE = 0.20
MAX_SINGLE_SYMBOL_SHARE = 0.20
MIN_SHADOW_AVAILABILITY = 0.99
MAX_TELEGRAM_ENQUEUE_LATENCY_SECONDS = 75.0
P0_DAILY_BUDGET = 2
P0_WEEKLY_BUDGET = 7

# Both receipts are issued by the same production observer after accepting an
# actual outbound message or a no-send probe into its bounded Telegram queue.
# The append-only export attestation and exact shadow binding remain required;
# naming a mode or supplying an offline timer alone is not trusted evidence.
TRUSTED_TELEGRAM_ENQUEUE_EVIDENCE_MODES = frozenset(
    {"counterfactual_no_send_same_queue", "actual_telegram_queue_receipt"}
)

P0_2M_ATR_ONLY_COMPARATOR_VERSION = "p0_2m_atr_only_comparator_v1"
P0_2M_ATR_ONLY_COMPARATOR_BASIS = (
    "nine_exact_completed_2m_bars_net_displacement_over_prior_daily_atr14;"
    "absolute_displacement_atr_gte_0.50;regular_session_only"
)

FROZEN_SIGNAL_THRESHOLDS = {
    "bar_minutes": P0_2M_BAR_MINUTES,
    "impulse_bars": P0_2M_IMPULSE_BARS,
    "displacement_bars": P0_2M_DISPLACEMENT_BARS,
    "reference_sessions": P0_2M_REFERENCE_SESSIONS,
    "price_percentile_min": P0_2M_PRICE_PERCENTILE_MIN,
    "rvol_8m_min": P0_2M_RVOL_MIN,
    "forming_displacement_atr_min": P0_2M_FORMING_DISPLACEMENT_ATR_MIN,
    "strict_displacement_atr_min": P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
}
FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS = {
    "strict_displacement_atr_min": P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
}

ASSET_CLASS_STOCK = "stock"
ASSET_CLASS_ETF = "ordinary_etf"
ASSET_CLASS_LEVERAGED = "leveraged_inverse_etf"
EXPECTED_ASSET_CLASSES = (
    ASSET_CLASS_STOCK,
    ASSET_CLASS_ETF,
    ASSET_CLASS_LEVERAGED,
)

# Frozen from INTRADAY_WATCHLIST on 2026-09-10.  Hashing treats this as a set,
# but the original order is retained in artifacts for human review.
DEFAULT_P0_UNIVERSE_69 = (
    "FXI", "TQQQ", "IWM", "YINN", "QQQ", "SMH", "SOXX", "GLD", "SLV",
    "BITO", "FNGS", "MARA", "MSTR", "COIN", "HOOD", "CRCL", "PLTR",
    "META", "ORCL", "CRM", "FIG", "MSFT", "ADBE", "TEAM", "NOW", "GOOGL",
    "AMZN", "RBLX", "NFLX", "PDD", "BABA", "DJT", "JD", "FIGR", "SNOW",
    "AMD", "NVDA", "INTC", "TSM", "AVGO", "CRWV", "MU", "SNDK", "DELL",
    "ASML", "QCOM", "MRVL", "LITE", "AAOI", "NBIS", "NOK", "SMCI", "ARM",
    "ALAB", "AAPL", "TSLA", "RKLB", "WDC", "ASTS", "BE", "IREN", "GEV",
    "IBM", "AMKR", "GLW", "SKHY", "DRAM", "SPCX", "CBRS",
)

_ORDINARY_ETFS = {
    "FXI", "IWM", "QQQ", "SMH", "SOXX", "GLD", "SLV", "BITO", "FNGS",
    "SKHY", "DRAM", "SPCX", "CBRS",
}
_LEVERAGED_INVERSE_ETFS = {"TQQQ", "YINN"}
DEFAULT_ASSET_CLASS_BY_SYMBOL = {
    symbol: (
        ASSET_CLASS_LEVERAGED
        if symbol in _LEVERAGED_INVERSE_ETFS
        else ASSET_CLASS_ETF
        if symbol in _ORDINARY_ETFS
        else ASSET_CLASS_STOCK
    )
    for symbol in DEFAULT_P0_UNIVERSE_69
}

__all__ = [
    "ASSET_CLASS_ETF",
    "ASSET_CLASS_LEVERAGED",
    "ASSET_CLASS_STOCK",
    "DEFAULT_ASSET_CLASS_BY_SYMBOL",
    "DEFAULT_P0_UNIVERSE_69",
    "DEFAULT_P0_UNIVERSE_HASH",
    "EXPECTED_ASSET_CLASSES",
    "P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION",
    "P0_2M_CALIBRATION_SCHEMA_VERSION",
    "P0_2M_ATR_ONLY_COMPARATOR_BASIS",
    "P0_2M_ATR_ONLY_COMPARATOR_VERSION",
    "FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS",
    "FROZEN_SIGNAL_THRESHOLDS",
    "REQUIRED_ACTIVATION_GATES",
    "calibrate_p0_2m",
    "compute_live_shadow_binding_sha256",
    "compute_universe_hash",
    "split_historical_sessions",
    "validated_mobile_eligible_symbols",
    "validate_activation_artifact",
]


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_universe(symbols: Iterable[Any]) -> tuple[str, ...]:
    normalized = tuple(_normalize_symbol(item) for item in symbols)
    if not normalized or any(not item for item in normalized):
        raise ValueError("universe_contains_blank_symbol")
    if len(set(normalized)) != len(normalized):
        raise ValueError("universe_contains_duplicate_symbol")
    return normalized


def compute_universe_hash(symbols: Iterable[Any]) -> str:
    """Return an order-independent deterministic SHA-256 universe identity."""

    normalized = _normalize_universe(symbols)
    canonical = json.dumps(sorted(normalized), ensure_ascii=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_live_shadow_binding_sha256(payload: Mapping[str, Any]) -> str:
    """Bind the exact forward-shadow material consumed by calibration."""

    material = {
        "shadow_observation_mode": payload.get("shadow_observation_mode"),
        "shadow_sessions": payload.get("shadow_sessions"),
        "shadow_enrollment": payload.get("shadow_enrollment"),
        "shadow": payload.get("shadow"),
        "universe": payload.get("universe"),
        "universe_hash": payload.get("universe_hash"),
    }
    canonical = json.dumps(
        material,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


DEFAULT_P0_UNIVERSE_HASH = compute_universe_hash(DEFAULT_P0_UNIVERSE_69)

REQUIRED_ACTIVATION_GATES = (
    "input_integrity",
    "fixed_universe_69",
    "historical_split_60_80_40",
    "holdout_signal_evidence_integrity",
    "atr_only_comparator_evidence_integrity",
    "baseline_coverage",
    "holdout_sample",
    "weekly_frequency",
    "budget_suppression",
    "single_symbol_concentration",
    "positive_20m_direction_adjusted_median",
    "atr_only_relative_cluster_ci90",
    "trusted_live_shadow_provenance",
    "forward_shadow_20_sessions",
    "shadow_aggregate_coverage",
    "shadow_integrity",
    "shadow_availability",
    "shadow_enqueue_latency",
    "known_asset_classes",
)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _xnys_session_dates(values: Iterable[Any]) -> tuple[list[date], list[str]]:
    materialized = list(values)
    parsed = sorted(
        {item for raw in materialized if (item := _parse_date(raw)) is not None}
    )
    invalid = [str(raw) for raw in materialized if _parse_date(raw) is None]
    if not parsed:
        return [], invalid
    try:
        calendar = xcals.get_calendar("XNYS")
        valid = {stamp.date() for stamp in calendar.sessions_in_range(parsed[0], parsed[-1])}
    except Exception:
        return [], invalid + ["xnys_calendar_unavailable"]
    non_sessions = [item.isoformat() for item in parsed if item not in valid]
    return [item for item in parsed if item in valid], invalid + non_sessions


def _xnys_session_block(start: date, count: int) -> list[date]:
    """Return ``count`` consecutive XNYS sessions including ``start``."""

    if count <= 0:
        return []
    try:
        calendar = xcals.get_calendar("XNYS")
        first = calendar.date_to_session(start, direction="none")
        return [stamp.date() for stamp in calendar.sessions_window(first, count)]
    except Exception:
        return []


def _shadow_enrollment_report(
    value: Any,
    *,
    universe: Sequence[str],
    universe_hash: str,
    shadow_dates: Sequence[date],
    holdout_dates: Sequence[date],
) -> dict[str, Any]:
    issues: list[str] = []
    if not isinstance(value, Mapping):
        return {
            "valid": False,
            "issues": ["shadow_enrollment_missing"],
            "enrolled_at": None,
            "created_at": None,
        }

    if value.get("observation_mode") != LIVE_FORWARD_SHADOW_MODE:
        issues.append("observation_mode_mismatch")
    if value.get("signal_version") != P0_2M_SIGNAL_VERSION:
        issues.append("signal_version_mismatch")
    if str(value.get("universe_hash") or "") != universe_hash:
        issues.append("universe_hash_mismatch")
    enrollment_key = str(value.get("enrollment_key") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", enrollment_key):
        issues.append("enrollment_key_missing_or_invalid")
    run_request_id = str(value.get("run_request_id") or "").strip() or None
    restart_reason = str(value.get("restart_reason") or "").strip() or None
    supersedes = (
        str(value.get("supersedes_enrollment_key") or "").strip() or None
    )
    if run_request_id is not None and not re.fullmatch(
        r"[A-Za-z0-9._:-]{1,64}", run_request_id
    ):
        issues.append("run_request_id_invalid")
    if supersedes is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", supersedes):
            issues.append("supersedes_enrollment_key_invalid")
        elif supersedes == enrollment_key:
            issues.append("enrollment_supersedes_itself")
        if run_request_id is None or restart_reason is None:
            issues.append("superseding_enrollment_metadata_incomplete")
    enrollment_universe = value.get("universe")
    if not isinstance(enrollment_universe, list):
        issues.append("universe_missing_or_invalid")
    else:
        try:
            normalized = _normalize_universe(enrollment_universe)
        except ValueError:
            issues.append("universe_missing_or_invalid")
        else:
            if normalized != tuple(universe) or len(normalized) != EXPECTED_UNIVERSE_SIZE:
                issues.append("universe_mismatch")
    expected_sessions = [item.isoformat() for item in shadow_dates]
    enrollment_sessions = value.get("sessions")
    if not isinstance(enrollment_sessions, list) or enrollment_sessions != expected_sessions:
        issues.append("sessions_mismatch")

    enrolled_at = _parse_datetime(value.get("enrolled_at"))
    created_at = _parse_datetime(value.get("created_at"))
    if enrolled_at is None:
        issues.append("enrolled_at_missing_or_naive")
    if created_at is None:
        issues.append("created_at_missing_or_naive")
    holdout_close: Optional[datetime] = None
    if holdout_dates and shadow_dates:
        try:
            calendar = xcals.get_calendar("XNYS")
            holdout_close = calendar.session_close(
                holdout_dates[-1]
            ).to_pydatetime()
        except Exception:
            issues.append("xnys_enrollment_boundary_unavailable")
    else:
        issues.append("enrollment_boundary_sessions_missing")
    if enrolled_at is not None and holdout_close is not None:
        if enrolled_at <= holdout_close:
            issues.append("enrolled_at_not_after_holdout_close")
    if enrolled_at is not None and created_at is not None and shadow_dates:
        try:
            from src.journal.ledger.p0_2m_shadow_repository import (
                validate_p0_2m_shadow_enrollment_boundary,
            )

            validate_p0_2m_shadow_enrollment_boundary(
                enrolled_at=enrolled_at,
                created_at=created_at,
                sessions=expected_sessions,
            )
        except ValueError as exc:
            message = str(exc)
            if "current XNYS session" in message:
                issues.append("enrollment_during_rth")
            elif "timestamp boundary" in message:
                issues.append("sessions_not_derived_from_enrollment")
            elif "precedes enrolled_at" in message:
                issues.append("created_at_precedes_enrolled_at")
            elif "live-write skew" in message:
                issues.append("enrollment_persistence_exceeds_live_write_skew")
            elif "first open" in message:
                issues.append("created_at_after_first_shadow_open")
            else:
                issues.append("xnys_enrollment_boundary_unavailable")
        except Exception:
            issues.append("xnys_enrollment_boundary_unavailable")

    return {
        "valid": not issues,
        "issues": issues,
        "enrolled_at": enrolled_at.isoformat() if enrolled_at is not None else None,
        "created_at": created_at.isoformat() if created_at is not None else None,
        "enrollment_key": enrollment_key or None,
        "run_request_id": run_request_id,
        "restart_reason": restart_reason,
        "supersedes_enrollment_key": supersedes,
        "expected_sessions": expected_sessions,
    }


def split_historical_sessions(session_dates: Iterable[Any]) -> dict[str, Any]:
    """Lock the most recent 180 valid XNYS dates into the prescribed split."""

    valid, invalid = _xnys_session_dates(list(session_dates))
    selected = valid[-HISTORICAL_SESSION_COUNT:]
    if len(selected) < HISTORICAL_SESSION_COUNT:
        baseline: list[date] = []
        development: list[date] = []
        holdout: list[date] = []
    else:
        baseline = selected[:BASELINE_SESSION_COUNT]
        development = selected[
            BASELINE_SESSION_COUNT : BASELINE_SESSION_COUNT + DEVELOPMENT_SESSION_COUNT
        ]
        holdout = selected[-HOLDOUT_SESSION_COUNT:]
    return {
        "valid_session_count": len(valid),
        "invalid_dates": invalid,
        "ignored_older_session_count": max(0, len(valid) - len(selected)),
        "baseline": [item.isoformat() for item in baseline],
        "development": [item.isoformat() for item in development],
        "holdout": [item.isoformat() for item in holdout],
    }


def _row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if key in row:
        return row[key]
    evidence = row.get("evidence")
    if isinstance(evidence, Mapping) and key in evidence:
        return evidence[key]
    return default


def _row_date(row: Mapping[str, Any]) -> Optional[date]:
    return _parse_date(
        _row_value(row, "session_date")
        or _row_value(row, "market_date_et")
        or _row_value(row, "window_end")
    )


def _is_regular(row: Mapping[str, Any]) -> bool:
    scope = str(_row_value(row, "session_scope", "")).strip().lower()
    return scope in {"regular", "rth"}


def _is_qualified(row: Mapping[str, Any]) -> bool:
    return (
        _row_value(row, "alert_qualified", None) is True
        and _row_value(row, "state", None) == "qualified"
    )


def _is_comparator_qualified(row: Mapping[str, Any]) -> bool:
    return (
        _row_value(row, "qualified", None) is True
        and _row_value(row, "state", None) == "qualified"
    )


def _is_explicit_empty_failed_checks(value: Any) -> bool:
    return isinstance(value, list) and not value


def _matches_frozen_thresholds(value: Any, expected: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        return False
    for key, expected_value in expected.items():
        actual = value.get(key)
        if isinstance(actual, bool):
            return False
        if isinstance(expected_value, (int, float)):
            if not isinstance(actual, (int, float)):
                return False
            number = _finite(actual)
            if number is None or number != float(expected_value):
                return False
        elif actual != expected_value:
            return False
    return True


def _complete_data_quality(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("status") == "complete"
        and isinstance(value.get("issues"), list)
        and not value.get("issues")
    )


def _nonblank_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _direction_adjusted_return(row: Mapping[str, Any]) -> Optional[float]:
    for key in (
        "direction_adjusted_return_20m",
        "forward_20m_direction_adjusted_return",
        "forward_return_20m_direction_adjusted",
    ):
        value = _finite(_row_value(row, key))
        if value is not None:
            return value
    return None


def _qualified_event_evidence_issues(
    rows: Sequence[Mapping[str, Any]],
    *,
    holdout_dates: set[date],
    universe_hash: str,
) -> list[dict[str, Any]]:
    """Audit provenance and no-lookahead facts for every exported RTH event."""

    issues: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        event_day = _row_date(row)
        if event_day not in holdout_dates:
            continue
        scope = str(_row_value(row, "session_scope", "")).strip().lower()
        if scope in {"premarket", "afterhours"}:
            continue
        reasons: list[str] = []
        if scope not in {"regular", "rth"}:
            reasons.append("session_scope_not_explicit_regular")
        if _row_value(row, "alert_qualified", None) is not True:
            reasons.append("alert_qualified_not_explicit_true")
        if _row_value(row, "state", None) != "qualified":
            reasons.append("state_not_explicit_qualified")
        if _row_value(row, "signal_version") != P0_2M_SIGNAL_VERSION:
            reasons.append("signal_version_mismatch")
        if _row_value(row, "universe_hash") != universe_hash:
            reasons.append("universe_hash_mismatch")
        data_quality = _row_value(row, "data_quality")
        if not _complete_data_quality(data_quality):
            reasons.append("data_quality_not_complete")
        if not _nonblank_text(_row_value(row, "source")):
            reasons.append("source_missing")
        if not _is_explicit_empty_failed_checks(_row_value(row, "failed_checks")):
            reasons.append("failed_checks_not_explicit_empty")
        if not _matches_frozen_thresholds(
            _row_value(row, "thresholds"), FROZEN_SIGNAL_THRESHOLDS
        ):
            reasons.append("thresholds_mismatch")
        baseline_count = _finite(_row_value(row, "baseline_count"))
        if baseline_count is None or baseline_count < P0_2M_REFERENCE_SESSIONS:
            reasons.append("baseline_count_below_60")
        baseline_last = _parse_date(_row_value(row, "baseline_last_date"))
        if baseline_last is None or event_day is None or baseline_last >= event_day:
            reasons.append("baseline_not_strictly_prior")
        percentile = _finite(_row_value(row, "price_percentile"))
        if percentile is None or percentile < P0_2M_PRICE_PERCENTILE_MIN:
            reasons.append("price_percentile_below_0.95")
        rvol = _finite(_row_value(row, "rvol_8m"))
        if rvol is None or rvol < P0_2M_RVOL_MIN:
            reasons.append("rvol_8m_below_1.50")
        displacement = _finite(_row_value(row, "displacement_atr"))
        if displacement is None or displacement < P0_2M_STRICT_DISPLACEMENT_ATR_MIN:
            reasons.append("displacement_atr_below_0.50")
        impulse = _finite(_row_value(row, "impulse_8m_log"))
        displacement_percent = _finite(
            _row_value(row, "displacement_18m_percent")
        )
        direction = str(_row_value(row, "direction", "")).strip().lower()
        if impulse is None or impulse == 0:
            reasons.append("impulse_8m_log_missing_or_zero")
        if displacement_percent is None or displacement_percent == 0:
            reasons.append("displacement_18m_percent_missing_or_zero")
        if (
            impulse is not None
            and impulse != 0
            and displacement_percent is not None
            and displacement_percent != 0
        ):
            if (impulse > 0) != (displacement_percent > 0):
                reasons.append("impulse_displacement_direction_mismatch")
            expected_direction = "up" if impulse > 0 else "down"
            if direction != expected_direction:
                reasons.append("direction_not_consistent_with_signed_moves")
        elif direction not in {"up", "down"}:
            reasons.append("direction_missing_or_invalid")
        if _event_window_end(row) is None:
            reasons.append("window_end_missing_or_naive")
        if reasons:
            issues.append(
                {
                    "row_index": index,
                    "symbol": _normalize_symbol(_row_value(row, "symbol")),
                    "session_date": event_day.isoformat() if event_day else None,
                    "issues": reasons,
                }
            )
    return issues


def _qualified_comparator_evidence_issues(
    rows: Sequence[Mapping[str, Any]],
    *,
    holdout_dates: set[date],
    universe_hash: str,
) -> list[dict[str, Any]]:
    """Require an explicit frozen ATR-only comparator contract."""

    issues: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        event_day = _row_date(row)
        if event_day not in holdout_dates:
            continue
        reasons: list[str] = []
        scope = str(_row_value(row, "session_scope", "")).strip().lower()
        if scope not in {"regular", "rth"}:
            reasons.append("session_scope_not_explicit_regular")
        if _row_value(row, "qualified", None) is not True:
            reasons.append("qualified_not_explicit_true")
        if _row_value(row, "state", None) != "qualified":
            reasons.append("state_not_explicit_qualified")
        if (
            _row_value(row, "comparator_version")
            != P0_2M_ATR_ONLY_COMPARATOR_VERSION
        ):
            reasons.append("comparator_version_mismatch")
        if _row_value(row, "comparator_basis") != P0_2M_ATR_ONLY_COMPARATOR_BASIS:
            reasons.append("comparator_basis_mismatch")
        if _row_value(row, "universe_hash") != universe_hash:
            reasons.append("universe_hash_mismatch")
        if not _nonblank_text(_row_value(row, "source")):
            reasons.append("source_missing")
        if not _complete_data_quality(_row_value(row, "data_quality")):
            reasons.append("data_quality_not_complete")
        if not _is_explicit_empty_failed_checks(_row_value(row, "failed_checks")):
            reasons.append("failed_checks_not_explicit_empty")
        if not _matches_frozen_thresholds(
            _row_value(row, "thresholds"),
            FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS,
        ):
            reasons.append("thresholds_mismatch")
        displacement = _finite(_row_value(row, "displacement_atr"))
        if displacement is None or displacement < P0_2M_STRICT_DISPLACEMENT_ATR_MIN:
            reasons.append("displacement_atr_below_0.50")
        if _event_window_end(row) is None:
            reasons.append("window_end_missing_or_naive")
        if reasons:
            issues.append(
                {
                    "row_index": index,
                    "symbol": _normalize_symbol(_row_value(row, "symbol")),
                    "session_date": event_day.isoformat() if event_day else None,
                    "issues": reasons,
                }
            )
    return issues


def _event_window_end(row: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_datetime(_row_value(row, "window_end"))


def _dedupe_events(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    ordered = sorted(rows, key=lambda row: _event_window_end(row) or datetime.max.replace(tzinfo=timezone.utc))
    accepted: list[Mapping[str, Any]] = []
    last_accepted: dict[tuple[str, str], dict[str, Any]] = {}
    for row in ordered:
        symbol = _normalize_symbol(_row_value(row, "symbol"))
        direction = str(_row_value(row, "direction", "")).strip().lower()
        window_end = _event_window_end(row)
        if not symbol or direction not in {"up", "down"} or window_end is None:
            continue
        key = (symbol, direction)
        previous = last_accepted.get(key)
        if is_duplicate_p0_episode(
            symbol=symbol,
            direction=direction,
            window_end=window_end,
            previous_events=[previous] if previous is not None else [],
            cooldown_minutes=P0_2M_EPISODE_MINUTES,
        ):
            continue
        accepted.append(row)
        # Rows are chronological; only the last accepted crossing for this
        # ticker/direction can suppress the next one. Suppressed rows do not
        # move the cooldown forward.
        last_accepted[key] = {"symbol": symbol, "direction": direction, "window_end": window_end}
    return accepted


def _percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _clustered_median_difference_ci90(
    p0_rows: Sequence[Mapping[str, Any]],
    baseline_rows: Sequence[Mapping[str, Any]],
    *,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    p0_by_day: dict[date, list[float]] = defaultdict(list)
    baseline_by_day: dict[date, list[float]] = defaultdict(list)
    for row in p0_rows:
        day = _row_date(row)
        value = _direction_adjusted_return(row)
        if day is not None and value is not None and _outcome_status(row) == "matured":
            p0_by_day[day].append(value)
    for row in baseline_rows:
        day = _row_date(row)
        value = _direction_adjusted_return(row)
        if day is not None and value is not None and _outcome_status(row) == "matured":
            baseline_by_day[day].append(value)
    days = sorted(set(p0_by_day) | set(baseline_by_day))
    observed_p0 = [value for day in days for value in p0_by_day.get(day, ())]
    observed_baseline = [value for day in days for value in baseline_by_day.get(day, ())]
    if not days or not observed_p0 or not observed_baseline or n_boot < 1:
        return {
            "cluster_days": len(days),
            "p0_n": len(observed_p0),
            "baseline_n": len(observed_baseline),
            "observed_median_difference": None,
            "ci90_low": None,
            "ci90_high": None,
            "bootstrap_samples": int(max(0, n_boot)),
            "seed": int(seed),
        }
    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(n_boot):
        sampled_days = rng.choices(days, k=len(days))
        p0_values = [value for day in sampled_days for value in p0_by_day.get(day, ())]
        baseline_values = [
            value for day in sampled_days for value in baseline_by_day.get(day, ())
        ]
        if p0_values and baseline_values:
            differences.append(
                float(statistics.median(p0_values))
                - float(statistics.median(baseline_values))
            )
    return {
        "cluster_days": len(days),
        "p0_n": len(observed_p0),
        "baseline_n": len(observed_baseline),
        "observed_median_difference": (
            float(statistics.median(observed_p0))
            - float(statistics.median(observed_baseline))
        ),
        "ci90_low": _percentile(differences, 0.05),
        "ci90_high": _percentile(differences, 0.95),
        "bootstrap_samples": len(differences),
        "seed": int(seed),
    }


def _coverage_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    universe: Sequence[str],
    baseline_dates: set[date],
) -> dict[str, Any]:
    by_symbol_day: dict[tuple[str, date], list[float]] = defaultdict(lambda: [0.0, 0.0])
    invalid_rows = 0
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        day = _row_date(row)
        if symbol not in universe or day not in baseline_dates:
            continue
        eligible = _finite(row.get("eligible_minutes"))
        complete = _finite(row.get("complete_minutes"))
        if eligible is None or complete is None:
            fraction = _finite(row.get("minute_completeness"))
            if fraction is not None:
                eligible, complete = 1.0, fraction
        if (
            eligible is None
            or complete is None
            or eligible <= 0
            or complete < 0
            or complete > eligible
        ):
            invalid_rows += 1
            continue
        bucket = by_symbol_day[(symbol, day)]
        bucket[0] += complete
        bucket[1] += eligible

    symbols: dict[str, Any] = {}
    ready: list[str] = []
    for symbol in universe:
        daily = [
            values
            for (candidate, day), values in by_symbol_day.items()
            if candidate == symbol and day in baseline_dates
        ]
        complete = sum(values[0] for values in daily)
        eligible = sum(values[1] for values in daily)
        completeness = complete / eligible if eligible > 0 else None
        covered_dates = len(daily)
        is_ready = (
            covered_dates == BASELINE_SESSION_COUNT
            and completeness is not None
            and completeness >= MIN_MINUTE_COMPLETENESS
        )
        if is_ready:
            ready.append(symbol)
        symbols[symbol] = {
            "covered_baseline_sessions": covered_dates,
            "complete_minutes": complete,
            "eligible_minutes": eligible,
            "minute_completeness": completeness,
            "baseline_ready": is_ready,
        }
    return {
        "ready_symbol_count": len(ready),
        "ready_symbols": ready,
        "invalid_row_count": invalid_rows,
        "symbols": symbols,
    }


def _full_xnys_weeks(days: Sequence[date]) -> dict[tuple[int, int], list[date]]:
    selected = set(days)
    grouped: dict[tuple[int, int], list[date]] = defaultdict(list)
    if not days:
        return grouped
    calendar = xcals.get_calendar("XNYS")
    for day in days:
        iso = day.isocalendar()
        grouped[(iso.year, iso.week)].append(day)
    full: dict[tuple[int, int], list[date]] = {}
    for key, observed in grouped.items():
        monday = date.fromisocalendar(key[0], key[1], 1)
        friday = monday + timedelta(days=4)
        expected = {
            stamp.date() for stamp in calendar.sessions_in_range(monday, friday)
        }
        if expected and expected.issubset(selected):
            full[key] = sorted(observed)
    return full


def _simulate_budget(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    daily: Counter[date] = Counter()
    delivered_days: list[date] = []
    delivered = 0
    suppressed = 0
    for row in sorted(rows, key=lambda item: _event_window_end(item) or datetime.max.replace(tzinfo=timezone.utc)):
        day = _row_date(row)
        if day is None:
            continue
        trailing_start = day - timedelta(days=6)
        trailing_delivered = sum(
            trailing_start <= delivered_day <= day
            for delivered_day in delivered_days
        )
        if (
            daily[day] >= P0_DAILY_BUDGET
            or trailing_delivered >= P0_WEEKLY_BUDGET
        ):
            suppressed += 1
            continue
        daily[day] += 1
        delivered_days.append(day)
        delivered += 1
    total = delivered + suppressed
    return {
        "daily_budget": P0_DAILY_BUDGET,
        "weekly_budget": P0_WEEKLY_BUDGET,
        "weekly_budget_window": "trailing_7_calendar_days_inclusive",
        "delivered": delivered,
        "suppressed": suppressed,
        "suppression_rate": suppressed / total if total else None,
    }


def _outcome_status(row: Mapping[str, Any]) -> str:
    """Validate declared censoring against XNYS, never turn missing data into a win."""
    end = _event_window_end(row)
    day = _row_date(row)
    value = _direction_adjusted_return(row)
    declared = _row_value(row, "outcome_status")
    try:
        calendar = xcals.get_calendar("XNYS")
        closed = calendar.session_close(day).to_pydatetime()
        opened = calendar.session_open(day).to_pydatetime()
        censored = bool(end is not None and opened < end <= closed and end + timedelta(minutes=20) > closed)
    except Exception:
        censored = False
    if censored:
        return "session_censored" if value is None and declared in {None, "session_censored"} else "invalid"
    if declared == "session_censored":
        return "invalid"
    if value is not None:
        return "matured" if declared in {None, "matured"} else "invalid"
    return str(declared) if declared in {"missing", "pending"} else "missing" if declared is None else "invalid"


def _outcome_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(_outcome_status(row) for row in rows)
    return {status: counts[status] for status in ("matured", "session_censored", "missing", "pending", "invalid")}


def _forward_outcomes_report(rows: Sequence[Mapping[str, Any]], *, provenance_verified: bool) -> dict[str, Any]:
    """Describe attested live diagnostics without adding an activation gate."""
    populations: dict[str, list[Mapping[str, Any]]] = {
        "events": [], "would_notify_events": [], "notification_events": [],
    }
    included = invalid = 0
    for row in rows:
        diagnostic = row.get("diagnostics")
        if diagnostic is None:
            continue
        if (not isinstance(diagnostic, Mapping)
                or diagnostic.get("schema_version") != "p0_2m_shadow_outcomes_v1"
                or diagnostic.get("activation_evidence") is not False):
            invalid += 1
            continue
        included += 1
        for name, population in populations.items():
            values = diagnostic.get(name, ())
            if not isinstance(values, (list, tuple)) or any(not isinstance(value, Mapping) for value in values):
                invalid += 1
                continue
            population.extend({**value, "symbol": row.get("symbol"), "session_date": row.get("session_date")} for value in values)
    summary: dict[str, Any] = {}
    for name, population in populations.items():
        returns = [_direction_adjusted_return(row) for row in population if _outcome_status(row) == "matured"]
        summary[name] = {
            "event_count": len(population), "outcome_status_counts": _outcome_counts(population),
            "direction_adjusted_return_20m_median": float(statistics.median(returns)) if returns else None,
        }
    return {
        "schema_version": "p0_2m_forward_outcome_report_v1", "activation_evidence": False,
        "source_provenance_verified": provenance_verified, "collected_symbol_session_count": included,
        "invalid_diagnostic_count": invalid, "populations": summary,
        "notification_population": "actual_telegram_queue_receipt_accepted_not_phone_delivery",
        "economic_edge_verified": False,
        "limitations": ["provider_adjustment_mode_not_explicit", "no_independent_ATR_only_comparator_recorded"],
    }


def _event_report(
    event_rows: Sequence[Mapping[str, Any]],
    comparator_rows: Sequence[Mapping[str, Any]],
    *,
    holdout_dates: Sequence[date],
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    holdout = set(holdout_dates)
    candidates = [
        row
        for row in event_rows
        if _row_date(row) in holdout and _is_regular(row) and _is_qualified(row)
    ]
    deduped = _dedupe_events(candidates)
    comparator_candidates = [
        row
        for row in comparator_rows
        if _row_date(row) in holdout
        and _is_regular(row)
        and _is_comparator_qualified(row)
        and (_finite(_row_value(row, "displacement_atr")) or 0.0)
        >= P0_2M_STRICT_DISPLACEMENT_ATR_MIN
    ]
    comparator = _dedupe_events(comparator_candidates)
    full_weeks = _full_xnys_weeks(holdout_dates)
    event_counts = Counter(_row_date(row) for row in deduped)
    weekly_counts = [
        sum(event_counts.get(day, 0) for day in days)
        for _, days in sorted(full_weeks.items())
    ]
    returns = [
        value
        for row in deduped
        if _outcome_status(row) == "matured"
        and (value := _direction_adjusted_return(row)) is not None
    ]
    by_symbol = Counter(_normalize_symbol(_row_value(row, "symbol")) for row in deduped)
    max_symbol = by_symbol.most_common(1)[0] if by_symbol else (None, 0)
    return {
        "raw_qualified_event_count": len(candidates),
        "deduped_event_count": len(deduped),
        "duplicate_episode_count": len(candidates) - len(deduped),
        "independent_rth_event_days": len({day for row in deduped if (day := _row_date(row))}),
        "complete_week_count": len(weekly_counts),
        "pre_budget_weekly_counts": weekly_counts,
        "pre_budget_weekly_median": (
            float(statistics.median(weekly_counts)) if weekly_counts else None
        ),
        "budget": _simulate_budget(deduped),
        "symbol_event_counts": dict(sorted(by_symbol.items())),
        "largest_symbol": max_symbol[0],
        "largest_symbol_share": max_symbol[1] / len(deduped) if deduped else None,
        "matured_20m_outcome_count": len(returns),
        "outcome_status_counts": _outcome_counts(deduped),
        "comparator_outcome_status_counts": _outcome_counts(comparator),
        "comparator_event_count": len(comparator),
        "direction_adjusted_return_20m_median": (
            float(statistics.median(returns)) if returns else None
        ),
        "atr_only_comparator": _clustered_median_difference_ci90(
            deduped,
            comparator,
            n_boot=n_boot,
            seed=seed,
        ),
        "_deduped_rows": deduped,
    }


def _explicit_nonnegative_int(row: Mapping[str, Any], key: str) -> Optional[int]:
    if (
        key not in row
        or isinstance(row.get(key), bool)
        or not isinstance(row.get(key), (int, float))
    ):
        return None
    value = _finite(row.get(key))
    if value is None or value < 0 or not value.is_integer():
        return None
    return int(value)


def _xnys_rth_2m_evaluation_count(day: date) -> Optional[int]:
    try:
        calendar = xcals.get_calendar("XNYS")
        duration_seconds = (
            calendar.session_close(day) - calendar.session_open(day)
        ).total_seconds()
    except Exception:
        return None
    bar_seconds = P0_2M_BAR_MINUTES * 60
    if duration_seconds <= 0 or duration_seconds % bar_seconds != 0:
        return None
    return int(duration_seconds // bar_seconds)


def _shadow_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    shadow_dates: Sequence[date],
    universe: Sequence[str],
) -> dict[str, Any]:
    selected = set(shadow_dates)
    relevant = [
        row
        for row in rows
        if _row_date(row) in selected
        and str(_row_value(row, "session_scope", "")).strip().lower()
        not in {"premarket", "afterhours"}
    ]
    expected_by_day = {
        day: _xnys_rth_2m_evaluation_count(day) for day in shadow_dates
    }
    expected_pairs = {
        (symbol, day) for symbol in universe for day in shadow_dates
    }
    by_pair: dict[tuple[str, date], list[Mapping[str, Any]]] = defaultdict(list)
    for row in relevant:
        day = _row_date(row)
        symbol = _normalize_symbol(_row_value(row, "symbol"))
        if day is not None and symbol in universe:
            by_pair[(symbol, day)].append(row)

    expected = sum(
        expected_by_day.get(day) or 0 for _, day in expected_pairs
    )
    available = 0
    duplicate_count = 0
    unfinished_count = 0
    latencies: list[float] = []
    invalid_latency_count = 0
    notification_expected_count = 0
    missing_pairs = sorted(expected_pairs - set(by_pair))
    duplicate_pairs = sorted(pair for pair, values in by_pair.items() if len(values) != 1)
    invalid_aggregate_rows: list[dict[str, Any]] = []
    invalid_enqueue_evidence_mode_count = 0
    valid_rows: list[Mapping[str, Any]] = []

    for symbol, day in sorted(expected_pairs, key=lambda item: (item[1], item[0])):
        pair_rows = by_pair.get((symbol, day), ())
        if len(pair_rows) != 1:
            continue
        row = pair_rows[0]
        reasons: list[str] = []
        scope = str(_row_value(row, "session_scope", "")).strip().lower()
        if scope not in {"regular", "rth"}:
            reasons.append("session_scope_not_explicit_regular")
        expected_for_day = expected_by_day.get(day)
        row_expected = _explicit_nonnegative_int(row, "expected_evaluations")
        if expected_for_day is None or row_expected != expected_for_day:
            reasons.append("expected_evaluations_mismatch")
        row_available = _explicit_nonnegative_int(row, "available_evaluations")
        if (
            row_available is None
            or expected_for_day is None
            or row_available > expected_for_day
        ):
            reasons.append("available_evaluations_invalid")
        row_duplicate = _explicit_nonnegative_int(
            row, "duplicate_notification_count"
        )
        if row_duplicate is None:
            reasons.append("duplicate_notification_count_missing_or_invalid")
        row_unfinished = _explicit_nonnegative_int(
            row, "unfinished_bar_trigger_count"
        )
        if row_unfinished is None:
            reasons.append("unfinished_bar_trigger_count_missing_or_invalid")
        enqueue_evidence_mode = str(
            row.get("telegram_enqueue_evidence_mode") or ""
        ).strip()
        source_modes = row.get(
            "telegram_enqueue_evidence_modes", [enqueue_evidence_mode]
        )
        source_modes_valid = bool(
            isinstance(source_modes, list)
            and all(
                isinstance(mode, str)
                and mode in TRUSTED_TELEGRAM_ENQUEUE_EVIDENCE_MODES
                for mode in source_modes
            )
            and (
                enqueue_evidence_mode in source_modes
                if source_modes
                else enqueue_evidence_mode == "counterfactual_no_send_same_queue"
            )
        )
        if (
            enqueue_evidence_mode not in TRUSTED_TELEGRAM_ENQUEUE_EVIDENCE_MODES
            or not source_modes_valid
        ):
            reasons.append("telegram_enqueue_evidence_mode_missing_or_untrusted")
            invalid_enqueue_evidence_mode_count += 1
        if reasons:
            invalid_aggregate_rows.append(
                {
                    "symbol": symbol,
                    "session_date": day.isoformat(),
                    "issues": reasons,
                }
            )
            continue
        available += int(row_available)
        duplicate_count += int(row_duplicate)
        unfinished_count += int(row_unfinished)
        valid_rows.append(row)

    for row in valid_rows:
        expects_notification = (
            row.get("would_notify") is True
            or row.get("alert_qualified") is True
            or "telegram_enqueue_latency_seconds" in row
            or "telegram_enqueued_at" in row
        )
        notification_expected_count += int(expects_notification)
        latency = _finite(row.get("telegram_enqueue_latency_seconds"))
        if latency is None:
            end = _parse_datetime(_row_value(row, "window_end"))
            enqueued = _parse_datetime(row.get("telegram_enqueued_at"))
            if end is not None and enqueued is not None:
                latency = (enqueued - end).total_seconds()
        if latency is not None:
            if latency < 0:
                invalid_latency_count += 1
            else:
                latencies.append(latency)
    return {
        "expected_symbol_session_rows": len(expected_pairs),
        "valid_symbol_session_rows": len(valid_rows),
        "missing_symbol_session_row_count": len(missing_pairs),
        "duplicate_symbol_session_row_count": len(duplicate_pairs),
        "invalid_aggregate_row_count": len(invalid_aggregate_rows),
        "invalid_enqueue_evidence_mode_count": (
            invalid_enqueue_evidence_mode_count
        ),
        "aggregate_issue_examples": [
            *[
                {
                    "symbol": symbol,
                    "session_date": day.isoformat(),
                    "issues": ["missing_symbol_session_row"],
                }
                for symbol, day in missing_pairs[:5]
            ],
            *[
                {
                    "symbol": symbol,
                    "session_date": day.isoformat(),
                    "issues": ["duplicate_symbol_session_row"],
                }
                for symbol, day in duplicate_pairs[:5]
            ],
            *invalid_aggregate_rows[:10],
        ][:10],
        "expected_evaluations_by_session": {
            day.isoformat(): count for day, count in expected_by_day.items()
        },
        "rth_session_count": len(
            {day for row in valid_rows if (day := _row_date(row))}
        ),
        "observation_count": len(relevant),
        "expected_evaluations": expected,
        "available_evaluations": available,
        "data_availability": available / expected if expected > 0 else None,
        "duplicate_notification_count": duplicate_count,
        "unfinished_bar_trigger_count": unfinished_count,
        "latency_sample_count": len(latencies),
        "notification_expected_count": notification_expected_count,
        "telegram_enqueue_latency_p95_seconds": _percentile(latencies, 0.95),
        "telegram_enqueue_latency_max_seconds": max(latencies) if latencies else None,
        "invalid_latency_count": invalid_latency_count,
        "_valid_rows": valid_rows,
    }


def _live_shadow_provenance_report(
    payload: Mapping[str, Any], *, universe_hash: str
) -> dict[str, Any]:
    """Verify the merged shadow binding against a DB-issued export receipt."""

    issues: list[str] = []
    producer = payload.get("producer_metadata")
    merge = (
        producer.get("live_shadow_merge")
        if isinstance(producer, Mapping)
        else None
    )
    if not isinstance(merge, Mapping):
        return {
            "valid": False,
            "issues": ["live_shadow_merge_provenance_missing"],
            "shadow_binding_sha256": None,
            "export_attestation": None,
        }
    if merge.get("observation_mode") != LIVE_FORWARD_SHADOW_MODE:
        issues.append("live_shadow_merge_observation_mode_mismatch")
    if merge.get("signal_version") != P0_2M_SIGNAL_VERSION:
        issues.append("live_shadow_merge_signal_version_mismatch")
    if str(merge.get("universe_hash") or "") != universe_hash:
        issues.append("live_shadow_merge_universe_hash_mismatch")
    enrollment = payload.get("shadow_enrollment")
    request_id = str(enrollment.get("run_request_id") or "") if isinstance(enrollment, Mapping) else ""
    if re.fullmatch(r"[0-9a-f]{64}", request_id):
        if merge.get("base_input_sha256") != request_id:
            issues.append("live_shadow_base_input_hash_mismatch")
    claimed_binding = str(merge.get("shadow_binding_sha256") or "").strip()
    expected_binding = compute_live_shadow_binding_sha256(payload)
    if not re.fullmatch(r"[0-9a-f]{64}", claimed_binding):
        issues.append("live_shadow_merge_binding_missing_or_invalid")
    elif claimed_binding != expected_binding:
        issues.append("live_shadow_merge_binding_mismatch")
    attestation = merge.get("export_attestation")
    claimed_export_hash = str(
        merge.get("live_shadow_export_sha256") or ""
    ).strip()
    if not isinstance(attestation, Mapping):
        issues.append("live_shadow_export_attestation_missing")
    elif str(attestation.get("export_sha256") or "") != claimed_export_hash:
        issues.append("live_shadow_export_hash_binding_mismatch")
    else:
        attested_enrollment_key = str(
            attestation.get("enrollment_key") or ""
        ).strip()
        merged_enrollment = merge.get("enrollment")
        payload_enrollment = payload.get("shadow_enrollment")
        if not re.fullmatch(r"[0-9a-f]{64}", attested_enrollment_key):
            issues.append("live_shadow_enrollment_key_missing_or_invalid")
        elif (
            str(merge.get("enrollment_key") or "").strip()
            != attested_enrollment_key
            or not isinstance(merged_enrollment, Mapping)
            or str(merged_enrollment.get("enrollment_key") or "").strip()
            != attested_enrollment_key
            or not isinstance(payload_enrollment, Mapping)
            or str(payload_enrollment.get("enrollment_key") or "").strip()
            != attested_enrollment_key
        ):
            issues.append("live_shadow_enrollment_key_binding_mismatch")
        shadow_rows = payload.get("shadow")
        if not isinstance(shadow_rows, list) or any(
            not isinstance(row, Mapping)
            or str(row.get("enrollment_key") or "").strip()
            != attested_enrollment_key
            for row in shadow_rows
        ):
            issues.append("live_shadow_row_enrollment_key_binding_mismatch")
        try:
            from src.journal.ledger.p0_2m_shadow_repository import (
                validate_p0_2m_live_shadow_export_attestation,
            )

            valid, reason = validate_p0_2m_live_shadow_export_attestation(
                attestation,
                expected_universe_hash=universe_hash,
                expected_signal_version=P0_2M_SIGNAL_VERSION,
            )
        except Exception as exc:  # noqa: BLE001 - provenance must fail closed
            valid, reason = False, f"attestation_check_failed:{type(exc).__name__}"
        if not valid:
            issues.append(str(reason))
    return {
        "valid": not issues,
        "issues": issues,
        "shadow_binding_sha256": expected_binding,
        "export_attestation": dict(attestation)
        if isinstance(attestation, Mapping)
        else None,
    }


def _gate(actual: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {"passed": bool(passed), "actual": actual, "threshold": threshold}


def _asset_classes(
    universe: Sequence[str], overrides: Mapping[str, Any]
) -> tuple[dict[str, str], list[str]]:
    aliases = {
        "stock": ASSET_CLASS_STOCK,
        "equity": ASSET_CLASS_STOCK,
        "etf": ASSET_CLASS_ETF,
        "ordinary_etf": ASSET_CLASS_ETF,
        "leveraged_etf": ASSET_CLASS_LEVERAGED,
        "inverse_etf": ASSET_CLASS_LEVERAGED,
        "leveraged_inverse_etf": ASSET_CLASS_LEVERAGED,
    }
    normalized_overrides = {
        _normalize_symbol(symbol): aliases.get(str(value).strip().lower(), "unknown")
        for symbol, value in overrides.items()
    }
    resolved: dict[str, str] = {}
    unknown: list[str] = []
    for symbol in universe:
        classification = normalized_overrides.get(
            symbol, DEFAULT_ASSET_CLASS_BY_SYMBOL.get(symbol, "unknown")
        )
        resolved[symbol] = classification
        if classification == "unknown":
            unknown.append(symbol)
    return resolved, unknown


def _stratified_report(
    *,
    universe: Sequence[str],
    classifications: Mapping[str, str],
    coverage: Mapping[str, Any],
    event_rows: Sequence[Mapping[str, Any]],
    shadow_rows: Sequence[Mapping[str, Any]],
    shadow_session_count: int,
) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    for asset_class in EXPECTED_ASSET_CLASSES:
        symbols = [symbol for symbol in universe if classifications.get(symbol) == asset_class]
        ready = [
            symbol
            for symbol in symbols
            if coverage["symbols"].get(symbol, {}).get("baseline_ready") is True
        ]
        symbol_set = set(ready)
        class_events = [
            row for row in event_rows if _normalize_symbol(_row_value(row, "symbol")) in symbol_set
        ]
        class_returns = [
            value
            for row in class_events
            if _outcome_status(row) == "matured"
            and (value := _direction_adjusted_return(row)) is not None
        ]
        class_shadow = [
            row for row in shadow_rows if _normalize_symbol(_row_value(row, "symbol")) in symbol_set
        ]
        baseline_complete = bool(ready)
        outcome_counts = _outcome_counts(class_events)
        outcomes_complete = bool(
            class_returns and not any(outcome_counts[status] for status in ("missing", "pending", "invalid"))
        )
        positive_outcomes = bool(
            class_returns and statistics.median(class_returns) > 0
        )
        shadow_complete = bool(
            ready and shadow_session_count > 0
            and len(class_shadow) == len(ready) * shadow_session_count
        )
        class_expected = sum(
            _explicit_nonnegative_int(row, "expected_evaluations") or 0
            for row in class_shadow
        )
        class_available = sum(
            _explicit_nonnegative_int(row, "available_evaluations") or 0
            for row in class_shadow
        )
        class_availability = (
            class_available / class_expected if class_expected else None
        )
        class_data_ready = (
            class_availability is not None
            and class_availability >= MIN_SHADOW_AVAILABILITY
        )
        diagnostic_complete = bool(
            baseline_complete and outcomes_complete and positive_outcomes
            and shadow_complete and class_data_ready
        )
        reports[asset_class] = {
            "state": "validated" if diagnostic_complete else "shadow",
            "symbols": symbols,
            "symbol_count": len(symbols),
            "baseline_ready_symbol_count": len(ready),
            "eligible_symbols": ready,
            "excluded_symbols": [symbol for symbol in symbols if symbol not in symbol_set],
            "holdout_event_count": len(class_events),
            "matured_20m_outcome_count": len(class_returns),
            "outcome_status_counts": outcome_counts,
            "direction_adjusted_return_20m_median": (
                float(statistics.median(class_returns)) if class_returns else None
            ),
            "shadow_observation_count": len(class_shadow),
            "shadow_data_availability": class_availability,
            "validation_checks": {
                "eligible_symbols_baseline_ready": baseline_complete,
                "holdout_events_and_outcomes_complete": outcomes_complete,
                "positive_20m_direction_adjusted_median": positive_outcomes,
                "full_shadow_symbol_session_coverage": shadow_complete,
                "shadow_availability": class_data_ready,
            },
            "diagnostic_complete": diagnostic_complete,
        }
    return reports


def calibrate_p0_2m(
    payload: Mapping[str, Any],
    *,
    n_boot: int = 4000,
    seed: int = 42,
    generated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Evaluate all locked activation gates and return a signed-off artifact.

    ``activation_ready`` is true only when every emitted gate is explicitly
    true.  Missing evidence never receives a neutral/default pass.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("payload_must_be_mapping")
    universe = _normalize_universe(payload.get("universe") or DEFAULT_P0_UNIVERSE_69)
    universe_hash = compute_universe_hash(universe)
    classifications, unknown_symbols = _asset_classes(
        universe,
        payload.get("asset_classes") if isinstance(payload.get("asset_classes"), Mapping) else {},
    )

    split = split_historical_sessions(payload.get("historical_sessions") or ())
    baseline_dates = {_parse_date(item) for item in split["baseline"]}
    baseline_dates.discard(None)
    holdout_dates = [_parse_date(item) for item in split["holdout"]]
    holdout_dates = [item for item in holdout_dates if item is not None]

    raw_coverage = list(payload.get("coverage", ()))
    raw_events = list(payload.get("events", ()))
    raw_comparator = list(payload.get("atr_only_events", ()))
    raw_shadow = list(payload.get("shadow", ()))
    coverage_rows = [row for row in raw_coverage if isinstance(row, Mapping)]
    event_rows = [row for row in raw_events if isinstance(row, Mapping)]
    comparator_rows = [row for row in raw_comparator if isinstance(row, Mapping)]
    shadow_rows = [row for row in raw_shadow if isinstance(row, Mapping)]
    universe_set = set(universe)
    out_of_universe = {
        "coverage": sum(
            _normalize_symbol(row.get("symbol")) not in universe_set
            for row in coverage_rows
        ),
        "events": sum(
            _normalize_symbol(_row_value(row, "symbol")) not in universe_set
            for row in event_rows
        ),
        "atr_only_events": sum(
            _normalize_symbol(_row_value(row, "symbol")) not in universe_set
            for row in comparator_rows
        ),
        "shadow": sum(
            _normalize_symbol(_row_value(row, "symbol")) not in universe_set
            for row in shadow_rows
        ),
    }
    invalid_record_types = (
        len(raw_coverage) - len(coverage_rows)
        + len(raw_events) - len(event_rows)
        + len(raw_comparator) - len(comparator_rows)
        + len(raw_shadow) - len(shadow_rows)
    )
    coverage_rows = [
        row for row in coverage_rows if _normalize_symbol(row.get("symbol")) in universe_set
    ]
    event_rows = [
        row
        for row in event_rows
        if _normalize_symbol(_row_value(row, "symbol")) in universe_set
    ]
    comparator_rows = [
        row
        for row in comparator_rows
        if _normalize_symbol(_row_value(row, "symbol")) in universe_set
    ]
    shadow_rows = [
        row
        for row in shadow_rows
        if _normalize_symbol(_row_value(row, "symbol")) in universe_set
    ]

    signal_evidence_issues = _qualified_event_evidence_issues(
        event_rows,
        holdout_dates=set(holdout_dates),
        universe_hash=universe_hash,
    )
    comparator_evidence_issues = _qualified_comparator_evidence_issues(
        comparator_rows,
        holdout_dates=set(holdout_dates),
        universe_hash=universe_hash,
    )

    coverage = _coverage_report(
        coverage_rows,
        universe=universe,
        baseline_dates=baseline_dates,
    )
    events = _event_report(
        event_rows,
        comparator_rows,
        holdout_dates=holdout_dates,
        n_boot=max(0, int(n_boot)),
        seed=int(seed),
    )
    producer_metadata = (
        dict(payload.get("producer_metadata"))
        if isinstance(payload.get("producer_metadata"), Mapping)
        else None
    )
    development_dates = [
        day for value in split["development"]
        if (day := _parse_date(value)) is not None
    ]
    development = _event_report(
        [row for row in payload.get("development_events", ())
         if isinstance(row, Mapping)],
        [row for row in payload.get("development_atr_only_events", ())
         if isinstance(row, Mapping)],
        holdout_dates=development_dates,
        n_boot=max(0, int(n_boot)),
        seed=int(seed),
    )
    development.pop("_deduped_rows", None)
    development["activation_evidence"] = False
    development["collected"] = "development_events" in payload

    raw_shadow_sessions = list(payload.get("shadow_sessions") or ())
    shadow_dates, invalid_shadow_dates = _xnys_session_dates(raw_shadow_sessions)
    selected_shadow_dates = shadow_dates[-SHADOW_SESSION_COUNT:]
    declared_shadow_dates = [_parse_date(value) for value in raw_shadow_sessions]
    declared_start = (
        declared_shadow_dates[0]
        if declared_shadow_dates and declared_shadow_dates[0] is not None
        else None
    )
    expected_shadow_dates = (
        _xnys_session_block(declared_start, SHADOW_SESSION_COUNT)
        if declared_start is not None
        else []
    )
    starts_after_holdout = bool(
        holdout_dates
        and declared_start is not None
        and declared_start > holdout_dates[-1]
    )
    consecutive_shadow_sessions = bool(
        len(raw_shadow_sessions) == SHADOW_SESSION_COUNT
        and not invalid_shadow_dates
        and starts_after_holdout
        and declared_shadow_dates == expected_shadow_dates
    )
    shadow_observation_mode = str(
        payload.get("shadow_observation_mode")
        or (
            producer_metadata.get("shadow_observation_mode")
            if producer_metadata is not None
            else ""
        )
        or ""
    ).strip()
    shadow_enrollment = _shadow_enrollment_report(
        payload.get("shadow_enrollment"),
        universe=universe,
        universe_hash=universe_hash,
        shadow_dates=expected_shadow_dates,
        holdout_dates=holdout_dates,
    )
    forward_shadow = bool(
        consecutive_shadow_sessions
        and shadow_observation_mode == LIVE_FORWARD_SHADOW_MODE
        and shadow_enrollment["valid"] is True
    )
    shadow = _shadow_report(
        shadow_rows,
        shadow_dates=selected_shadow_dates,
        universe=universe,
    )
    live_shadow_provenance = _live_shadow_provenance_report(
        payload, universe_hash=universe_hash
    )
    forward_outcomes = _forward_outcomes_report(
        shadow["_valid_rows"], provenance_verified=live_shadow_provenance["valid"] is True,
    )

    stratified = _stratified_report(
        universe=universe,
        classifications=classifications,
        coverage=coverage,
        event_rows=events.pop("_deduped_rows"),
        shadow_rows=shadow.pop("_valid_rows"),
        shadow_session_count=len(selected_shadow_dates),
    )
    mobile_eligible_symbols = [
        symbol
        for symbol in universe
        if coverage["symbols"].get(symbol, {}).get("baseline_ready") is True
        and stratified.get(classifications.get(symbol), {}).get("state")
        == "validated"
    ]

    weekly_median = events["pre_budget_weekly_median"]
    suppression_rate = events["budget"]["suppression_rate"]
    symbol_share = events["largest_symbol_share"]
    return_median = events["direction_adjusted_return_20m_median"]
    ci_low = events["atr_only_comparator"]["ci90_low"]
    availability = shadow["data_availability"]
    latency_max = shadow["telegram_enqueue_latency_max_seconds"]

    source_version_mismatches = sum(
        1
        for row in event_rows
        if _row_value(row, "signal_version") != P0_2M_SIGNAL_VERSION
    )
    derivation_errors = (
        producer_metadata.get("derivation_errors", ())
        if producer_metadata is not None
        else ()
    )
    derivation_errors_valid = isinstance(derivation_errors, list) and all(
        _nonblank_text(item) for item in derivation_errors
    )
    if producer_metadata is None:
        derivation_errors_valid = True
        derivation_errors = []
    gates = {
        "input_integrity": _gate(
            {
                "invalid_historical_dates": split["invalid_dates"],
                "invalid_shadow_dates": invalid_shadow_dates,
                "invalid_coverage_rows": coverage["invalid_row_count"],
                "signal_version_mismatches": source_version_mismatches,
                "invalid_record_types": invalid_record_types,
                "out_of_universe_rows": out_of_universe,
                "input_schema_version": payload.get("schema_version"),
                "producer_derivation_errors": list(derivation_errors)
                if derivation_errors_valid
                else "invalid_error_collection",
            },
            "all invalid/mismatch counts == 0",
            not split["invalid_dates"]
            and not invalid_shadow_dates
            and coverage["invalid_row_count"] == 0
            and source_version_mismatches == 0
            and invalid_record_types == 0
            and sum(out_of_universe.values()) == 0
            and derivation_errors_valid
            and not derivation_errors
            and payload.get("schema_version")
            == P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
        ),
        "fixed_universe_69": _gate(
            {
                "symbol_count": len(universe),
                "universe_hash": universe_hash,
                "frozen_universe_hash": DEFAULT_P0_UNIVERSE_HASH,
            },
            (
                f"exact frozen {EXPECTED_UNIVERSE_SIZE}-symbol set with hash "
                f"{DEFAULT_P0_UNIVERSE_HASH}"
            ),
            len(universe) == EXPECTED_UNIVERSE_SIZE
            and universe_hash == DEFAULT_P0_UNIVERSE_HASH,
        ),
        "historical_split_60_80_40": _gate(
            {
                "baseline": len(split["baseline"]),
                "development": len(split["development"]),
                "holdout": len(split["holdout"]),
            },
            "baseline == 60 and development == 80 and holdout == 40",
            len(split["baseline"]) == BASELINE_SESSION_COUNT
            and len(split["development"]) == DEVELOPMENT_SESSION_COUNT
            and len(split["holdout"]) == HOLDOUT_SESSION_COUNT,
        ),
        "holdout_signal_evidence_integrity": _gate(
            {
                "invalid_event_count": len(signal_evidence_issues),
                "examples": signal_evidence_issues[:10],
            },
            (
                "all qualified holdout rows use the exact signal/universe version, "
                "complete data, 60 strictly-prior baselines, and frozen thresholds"
            ),
            not signal_evidence_issues,
        ),
        "atr_only_comparator_evidence_integrity": _gate(
            {
                "invalid_event_count": len(comparator_evidence_issues),
                "examples": comparator_evidence_issues[:10],
                "required_version": P0_2M_ATR_ONLY_COMPARATOR_VERSION,
                "required_basis": P0_2M_ATR_ONLY_COMPARATOR_BASIS,
            },
            (
                "all holdout comparator rows explicitly identify the frozen "
                "ATR-only comparator, source, qualified state, empty failures, "
                "complete data, exact threshold, and aware window_end"
            ),
            not comparator_evidence_issues,
        ),
        "baseline_coverage": _gate(
            coverage["ready_symbol_count"],
            f">= {MIN_BASELINE_READY_SYMBOLS}/{EXPECTED_UNIVERSE_SIZE} symbols, each >= {MIN_MINUTE_COMPLETENESS:.0%}",
            coverage["ready_symbol_count"] >= MIN_BASELINE_READY_SYMBOLS,
        ),
        "holdout_sample": _gate(
            {
                "events": events["deduped_event_count"],
                "rth_days": events["independent_rth_event_days"],
            },
            f"events >= {MIN_HOLDOUT_EVENTS} and RTH days >= {MIN_HOLDOUT_EVENT_DAYS}",
            events["deduped_event_count"] >= MIN_HOLDOUT_EVENTS
            and events["independent_rth_event_days"] >= MIN_HOLDOUT_EVENT_DAYS,
        ),
        "weekly_frequency": _gate(
            weekly_median,
            f"{MIN_WEEKLY_MEDIAN_EVENTS:g} <= complete-week median <= {MAX_WEEKLY_MEDIAN_EVENTS:g}",
            weekly_median is not None
            and MIN_WEEKLY_MEDIAN_EVENTS <= weekly_median <= MAX_WEEKLY_MEDIAN_EVENTS,
        ),
        "budget_suppression": _gate(
            suppression_rate,
            f"<= {MAX_BUDGET_SUPPRESSION_RATE:.0%}",
            suppression_rate is not None
            and suppression_rate <= MAX_BUDGET_SUPPRESSION_RATE,
        ),
        "single_symbol_concentration": _gate(
            {"symbol": events["largest_symbol"], "share": symbol_share},
            f"<= {MAX_SINGLE_SYMBOL_SHARE:.0%}",
            symbol_share is not None and symbol_share <= MAX_SINGLE_SYMBOL_SHARE,
        ),
        "positive_20m_direction_adjusted_median": _gate(
            {
                "median": return_median,
                "matured": events["matured_20m_outcome_count"],
                "events": events["deduped_event_count"],
            },
            "median > 0 and every non-session-censored holdout event has a matured 20m outcome",
            return_median is not None
            and return_median > 0
            and not any(events["outcome_status_counts"][status] for status in ("missing", "pending", "invalid")),
        ),
        "atr_only_relative_cluster_ci90": _gate(
            {
                "ci90_low": ci_low,
                "cluster_days": events["atr_only_comparator"]["cluster_days"],
                "bootstrap_samples": events["atr_only_comparator"][
                    "bootstrap_samples"
                ],
                "outcome_status_counts": events["comparator_outcome_status_counts"],
            },
            f"CI90 lower >= 0 with >= {MIN_HOLDOUT_EVENT_DAYS} clustered days, all bootstrap draws, and complete non-censored comparator outcomes",
            ci_low is not None
            and ci_low >= 0
            and events["atr_only_comparator"]["cluster_days"]
            >= MIN_HOLDOUT_EVENT_DAYS
            and events["atr_only_comparator"]["bootstrap_samples"]
            == max(0, int(n_boot))
            and not any(events["comparator_outcome_status_counts"][status] for status in ("missing", "pending", "invalid")),
        ),
        "trusted_live_shadow_provenance": _gate(
            {
                "valid": live_shadow_provenance["valid"],
                "issues": live_shadow_provenance["issues"],
                "shadow_binding_sha256": live_shadow_provenance[
                    "shadow_binding_sha256"
                ],
            },
            "DB-issued append-only export attestation and exact shadow binding",
            live_shadow_provenance["valid"] is True,
        ),
        "forward_shadow_20_sessions": _gate(
            {
                "declared_sessions": len(raw_shadow_sessions),
                "valid_declared_sessions": len(shadow_dates),
                "observed_sessions": shadow["rth_session_count"],
                "observation_mode": shadow_observation_mode or None,
                "consecutive_from_holdout": consecutive_shadow_sessions,
                "starts_after_holdout": starts_after_holdout,
                "enrollment": shadow_enrollment,
                "expected_sessions": [
                    item.isoformat() for item in expected_shadow_dates
                ],
                "invalid_dates": invalid_shadow_dates,
            },
            (
                f"exactly {SHADOW_SESSION_COUNT} consecutive XNYS sessions "
                "beginning after holdout, observations on every session, and "
                f"mode == {LIVE_FORWARD_SHADOW_MODE}, backed by a pre-open "
                "prospective enrollment"
            ),
            forward_shadow and shadow["rth_session_count"] == SHADOW_SESSION_COUNT,
        ),
        "shadow_aggregate_coverage": _gate(
            {
                "expected_symbol_session_rows": shadow[
                    "expected_symbol_session_rows"
                ],
                "valid_symbol_session_rows": shadow["valid_symbol_session_rows"],
                "missing_symbol_session_rows": shadow[
                    "missing_symbol_session_row_count"
                ],
                "duplicate_symbol_session_rows": shadow[
                    "duplicate_symbol_session_row_count"
                ],
                "invalid_aggregate_rows": shadow["invalid_aggregate_row_count"],
                "invalid_enqueue_evidence_modes": shadow[
                    "invalid_enqueue_evidence_mode_count"
                ],
                "examples": shadow["aggregate_issue_examples"],
            },
            (
                f"exactly one explicit aggregate for every {EXPECTED_UNIVERSE_SIZE} x "
                f"{SHADOW_SESSION_COUNT} symbol/session pair; expected evaluations "
                "must match XNYS exact-2m RTH windows and enqueue evidence mode "
                "must be explicitly trusted"
            ),
            shadow["expected_symbol_session_rows"]
            == EXPECTED_UNIVERSE_SIZE * SHADOW_SESSION_COUNT
            and shadow["valid_symbol_session_rows"]
            == shadow["expected_symbol_session_rows"]
            and shadow["missing_symbol_session_row_count"] == 0
            and shadow["duplicate_symbol_session_row_count"] == 0
            and shadow["invalid_aggregate_row_count"] == 0,
        ),
        "shadow_integrity": _gate(
            {
                "duplicate_notifications": shadow["duplicate_notification_count"],
                "unfinished_bar_triggers": shadow["unfinished_bar_trigger_count"],
            },
            "both == 0",
            shadow["duplicate_notification_count"] == 0
            and shadow["unfinished_bar_trigger_count"] == 0,
        ),
        "shadow_availability": _gate(
            availability,
            f">= {MIN_SHADOW_AVAILABILITY:.0%}",
            availability is not None and availability >= MIN_SHADOW_AVAILABILITY,
        ),
        "shadow_enqueue_latency": _gate(
            {
                "p95_seconds": shadow["telegram_enqueue_latency_p95_seconds"],
                "max_seconds": latency_max,
                "samples": shadow["latency_sample_count"],
                "expected_samples": shadow["notification_expected_count"],
                "invalid_evidence_modes": shadow[
                    "invalid_enqueue_evidence_mode_count"
                ],
            },
            (
                "trusted queue evidence for every aggregate; all expected "
                f"samples present and max <= {MAX_TELEGRAM_ENQUEUE_LATENCY_SECONDS:g}s"
            ),
            latency_max is not None
            and latency_max <= MAX_TELEGRAM_ENQUEUE_LATENCY_SECONDS
            and shadow["latency_sample_count"] > 0
            and shadow["latency_sample_count"]
            == shadow["notification_expected_count"]
            and shadow["invalid_latency_count"] == 0
            and shadow["invalid_enqueue_evidence_mode_count"] == 0,
        ),
        "known_asset_classes": _gate(
            unknown_symbols,
            "no unknown symbols",
            not unknown_symbols,
        ),
    }
    activation_ready = all(item["passed"] is True for item in gates.values())

    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at_must_be_timezone_aware")
    artifact = {
        "schema_version": P0_2M_CALIBRATION_SCHEMA_VERSION,
        "input_schema_version": str(
            payload.get("schema_version") or P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION
        ),
        "signal_version": P0_2M_SIGNAL_VERSION,
        "signal_thresholds": dict(FROZEN_SIGNAL_THRESHOLDS),
        "atr_only_comparator_version": P0_2M_ATR_ONLY_COMPARATOR_VERSION,
        "atr_only_comparator_basis": P0_2M_ATR_ONLY_COMPARATOR_BASIS,
        "atr_only_comparator_thresholds": dict(
            FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS
        ),
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "input_provenance": producer_metadata,
        "shadow_source_binding_sha256": live_shadow_provenance[
            "shadow_binding_sha256"
        ],
        "universe_hash": universe_hash,
        "universe": list(universe),
        "asset_classes": classifications,
        "unknown_symbols": unknown_symbols,
        "mobile_eligible_symbols": mobile_eligible_symbols,
        "validated_session_scopes": ["regular"],
        "validation_scope": "fixed_rule_detection_and_delivery",
        "historical_independence": "not_established_preselected_cases",
        "economic_edge_verified": False,
        "split": split,
        "coverage": coverage,
        "development": development,
        "forward_outcomes": forward_outcomes,
        "holdout": events,
        "shadow": {
            **shadow,
            "sessions": [item.isoformat() for item in selected_shadow_dates],
            "forward_after_holdout": forward_shadow,
            "observation_mode": shadow_observation_mode or None,
            "enrollment": shadow_enrollment,
        },
        "strata": stratified,
        "gate_results": gates,
        "activation_ready": activation_ready,
        "mobile_delivery_effect": "none",
        "notes": [
            "Artifact generation never changes configuration or enables Telegram.",
            "A false or missing gate keeps exact-2m results in web/ledger shadow only.",
            "Historical thresholds are frozen, but previously selected examples prevent claiming an untouched holdout.",
            "The artifact validates RTH detection and delivery only, not extended sessions or economic profitability.",
        ],
    }
    canonical_for_hash = json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    artifact["artifact_sha256"] = hashlib.sha256(canonical_for_hash.encode("utf-8")).hexdigest()
    return artifact


def validate_activation_artifact(
    payload_or_path: Mapping[str, Any] | str | Path | None,
    *,
    expected_universe_hash: str,
    expected_signal_version: str = P0_2M_SIGNAL_VERSION,
) -> tuple[bool, str]:
    """Validate a completed artifact without rerunning any statistics."""

    if payload_or_path is None:
        return False, "artifact_missing"
    if isinstance(payload_or_path, Mapping):
        payload: Any = payload_or_path
    else:
        try:
            path = Path(payload_or_path).expanduser()
            if not path.is_file():
                return False, "artifact_missing"
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False, "artifact_unreadable"
    if not isinstance(payload, Mapping):
        return False, "artifact_not_object"
    if payload.get("schema_version") != P0_2M_CALIBRATION_SCHEMA_VERSION:
        return False, "schema_version_mismatch"
    if (
        expected_signal_version != P0_2M_SIGNAL_VERSION
        or payload.get("signal_version") != P0_2M_SIGNAL_VERSION
    ):
        return False, "signal_version_mismatch"
    expected_artifact_hash = str(payload.get("artifact_sha256") or "").strip()
    if not expected_artifact_hash:
        return False, "artifact_hash_missing"
    unhashed = dict(payload)
    unhashed.pop("artifact_sha256", None)
    canonical = json.dumps(
        unhashed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    actual_artifact_hash = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    if actual_artifact_hash != expected_artifact_hash:
        return False, "artifact_hash_mismatch"
    if payload.get("input_schema_version") != P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION:
        return False, "input_schema_version_mismatch"
    if (
        payload.get("validated_session_scopes") != ["regular"]
        or payload.get("validation_scope") != "fixed_rule_detection_and_delivery"
        or payload.get("economic_edge_verified") is not False
    ):
        return False, "validation_scope_mismatch"
    if not _matches_frozen_thresholds(
        payload.get("signal_thresholds"), FROZEN_SIGNAL_THRESHOLDS
    ):
        return False, "signal_thresholds_mismatch"
    if (
        payload.get("atr_only_comparator_version")
        != P0_2M_ATR_ONLY_COMPARATOR_VERSION
    ):
        return False, "comparator_version_mismatch"
    if payload.get("atr_only_comparator_basis") != P0_2M_ATR_ONLY_COMPARATOR_BASIS:
        return False, "comparator_basis_mismatch"
    if not _matches_frozen_thresholds(
        payload.get("atr_only_comparator_thresholds"),
        FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS,
    ):
        return False, "comparator_thresholds_mismatch"
    if expected_universe_hash != DEFAULT_P0_UNIVERSE_HASH:
        return False, "universe_hash_mismatch"
    if payload.get("universe_hash") != DEFAULT_P0_UNIVERSE_HASH:
        return False, "universe_hash_mismatch"
    provenance = payload.get("input_provenance")
    live_merge = (
        provenance.get("live_shadow_merge")
        if isinstance(provenance, Mapping)
        else None
    )
    if not isinstance(live_merge, Mapping):
        return False, "live_shadow_provenance_missing"
    shadow_binding = str(
        payload.get("shadow_source_binding_sha256") or ""
    ).strip()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", shadow_binding)
        or live_merge.get("shadow_binding_sha256") != shadow_binding
    ):
        return False, "live_shadow_binding_mismatch"
    attestation = live_merge.get("export_attestation")
    if not isinstance(attestation, Mapping):
        return False, "live_shadow_export_attestation_missing"
    attested_enrollment_key = str(
        attestation.get("enrollment_key") or ""
    ).strip()
    provenance_enrollment = live_merge.get("enrollment")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", attested_enrollment_key)
        or str(live_merge.get("enrollment_key") or "").strip()
        != attested_enrollment_key
        or not isinstance(provenance_enrollment, Mapping)
        or str(provenance_enrollment.get("enrollment_key") or "").strip()
        != attested_enrollment_key
    ):
        return False, "live_shadow_enrollment_key_binding_mismatch"
    if str(attestation.get("export_sha256") or "") != str(
        live_merge.get("live_shadow_export_sha256") or ""
    ):
        return False, "live_shadow_export_hash_binding_mismatch"
    try:
        from src.journal.ledger.p0_2m_shadow_repository import (
            validate_p0_2m_live_shadow_export_attestation,
        )

        attested, attestation_reason = (
            validate_p0_2m_live_shadow_export_attestation(
                attestation,
                expected_universe_hash=expected_universe_hash,
                expected_signal_version=expected_signal_version,
            )
        )
    except Exception as exc:  # noqa: BLE001 - runtime must fail closed
        return False, f"live_shadow_attestation_check_failed:{type(exc).__name__}"
    if not attested:
        return False, str(attestation_reason)
    universe = payload.get("universe")
    if not isinstance(universe, list):
        return False, "universe_contents_missing"
    try:
        if compute_universe_hash(universe) != DEFAULT_P0_UNIVERSE_HASH:
            return False, "universe_contents_mismatch"
    except ValueError:
        return False, "universe_contents_invalid"
    gates = payload.get("gate_results")
    if not isinstance(gates, Mapping) or not gates:
        return False, "gate_results_missing"
    if set(gates) != set(REQUIRED_ACTIVATION_GATES):
        return False, "gate_results_incomplete"
    for name in REQUIRED_ACTIVATION_GATES:
        result = gates[name]
        if not isinstance(result, Mapping) or result.get("passed") is not True:
            return False, f"gate_failed:{name}"
    if payload.get("unknown_symbols") not in ([], ()):
        return False, "unknown_asset_class"
    strata = payload.get("strata")
    if not isinstance(strata, Mapping) or set(strata) != set(EXPECTED_ASSET_CLASSES):
        return False, "stratified_report_incomplete"
    asset_classes = payload.get("asset_classes")
    coverage = payload.get("coverage")
    coverage_symbols = coverage.get("symbols") if isinstance(coverage, Mapping) else None
    if not isinstance(asset_classes, Mapping) or not isinstance(coverage_symbols, Mapping):
        return False, "mobile_eligibility_evidence_missing"
    for name in EXPECTED_ASSET_CLASSES:
        report = strata.get(name)
        if not isinstance(report, Mapping):
            return False, "stratified_report_incomplete"
        state = report.get("state")
        diagnostic_complete = report.get("diagnostic_complete")
        if state not in {"validated", "shadow"}:
            return False, "stratified_state_invalid"
        if (state == "validated") is not (diagnostic_complete is True):
            return False, "stratified_state_mismatch"
        expected_symbols = [
            symbol for symbol in universe if asset_classes.get(symbol) == name
        ]
        if report.get("symbols") != expected_symbols:
            return False, "stratified_symbols_mismatch"
    mobile_symbols = payload.get("mobile_eligible_symbols")
    if not isinstance(mobile_symbols, list):
        return False, "mobile_eligible_symbols_missing"
    expected_mobile_symbols = [
        symbol
        for symbol in universe
        if isinstance(coverage_symbols.get(symbol), Mapping)
        and coverage_symbols[symbol].get("baseline_ready") is True
        and isinstance(strata.get(asset_classes.get(symbol)), Mapping)
        and strata[asset_classes[symbol]].get("state") == "validated"
    ]
    if mobile_symbols != expected_mobile_symbols:
        return False, "mobile_eligible_symbols_mismatch"
    if payload.get("activation_ready") is not True:
        return False, "activation_not_ready"
    try:
        from src.journal.ledger.p0_2m_shadow_repository import (
            validate_p0_2m_activation_artifact_attestation,
        )

        artifact_attested, artifact_attestation_reason = (
            validate_p0_2m_activation_artifact_attestation(payload)
        )
    except Exception as exc:  # noqa: BLE001 - runtime must fail closed
        return False, (
            "activation_artifact_attestation_check_failed:"
            f"{type(exc).__name__}"
        )
    if not artifact_attested:
        return False, str(artifact_attestation_reason)
    return True, "ok"


def validated_mobile_eligible_symbols(
    payload_or_path: Mapping[str, Any] | str | Path | None,
    *,
    expected_universe_hash: str,
    expected_signal_version: str = P0_2M_SIGNAL_VERSION,
) -> tuple[bool, str, tuple[str, ...]]:
    """Return the per-symbol mobile scope from one validated artifact snapshot.

    Runtime callers must not turn the artifact's global readiness bit into
    permission for every symbol: a failed asset-class stratum intentionally
    remains shadow-only.  A path is read once and the same in-memory object is
    both validated and used to extract the eligible set, avoiding a TOCTOU gap.
    """

    if payload_or_path is None:
        return False, "artifact_missing", ()
    if isinstance(payload_or_path, Mapping):
        payload: Any = dict(payload_or_path)
    else:
        try:
            path = Path(payload_or_path).expanduser()
            if not path.is_file():
                return False, "artifact_missing", ()
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False, "artifact_unreadable", ()
    if not isinstance(payload, Mapping):
        return False, "artifact_not_object", ()

    valid, reason = validate_activation_artifact(
        payload,
        expected_universe_hash=expected_universe_hash,
        expected_signal_version=expected_signal_version,
    )
    if not valid:
        return False, reason, ()
    mobile_symbols = payload.get("mobile_eligible_symbols")
    if not isinstance(mobile_symbols, list):
        # This is already checked by the validator; retain a defensive branch
        # so a future validator change cannot open the runtime gate.
        return False, "mobile_eligible_symbols_missing", ()
    return True, "ok", tuple(str(item).strip().upper() for item in mobile_symbols)
