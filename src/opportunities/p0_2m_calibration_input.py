# -*- coding: utf-8 -*-
"""Derive the locked P0 calibration input from provider bar exports.

This module is deliberately offline.  It reads one manifest plus per-symbol
1-minute and daily CSV files, applies the production exact-2m aggregation
contract, and emits the input consumed by :mod:`p0_2m_calibration`.

The export does *not* prove Telegram enqueue latency.  A bars-only shadow row
therefore records that latency evidence is missing.  The downstream locked
gate remains false instead of treating replay time as delivery time.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from src.opportunities.exact_2m import (
    EXACT_2M_AGGREGATION_CONTRACT,
    aggregate_exact_2m_ohlcv,
)
from src.opportunities.intraday import ATR14_METHOD, compute_atr14
from src.opportunities.intraday_bursts import BAR_LABEL_END_SOURCES
from src.opportunities.p0_2m_calibration import (
    DEFAULT_ASSET_CLASS_BY_SYMBOL,
    DEFAULT_P0_UNIVERSE_69,
    FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS,
    FROZEN_SIGNAL_THRESHOLDS,
    P0_2M_ATR_ONLY_COMPARATOR_BASIS,
    P0_2M_ATR_ONLY_COMPARATOR_VERSION,
    P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
    compute_universe_hash,
)
from src.opportunities.p0_2m_signal import (
    P0_2M_EPISODE_MINUTES,
    P0_2M_IMPULSE_BARS,
    P0_2M_REFERENCE_SESSIONS,
    P0_2M_SIGNAL_BASIS,
    P0_2M_SIGNAL_VERSION,
    P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
    is_duplicate_p0_episode,
    compute_p0_2m_window_metrics,
    summarize_p0_2m_impulse,
    compute_p0_2m_signal,
)

P0_2M_RAW_EXPORT_SCHEMA_VERSION = "p0_2m_raw_export_v1"
P0_2M_INPUT_PRODUCER_VERSION = "p0_2m_input_producer_v1"
_ET = ZoneInfo("America/New_York")
_HISTORICAL_SESSION_COUNT = 180
_SHADOW_SESSION_COUNT = 20
_DAILY_LOOKBACK_CALENDAR_DAYS = 140
_MINUTE_COLUMNS = {"date", "open", "high", "low", "close", "volume"}
_DAILY_COLUMNS = {"date", "high", "low", "close"}

__all__ = [
    "P0_2M_INPUT_PRODUCER_VERSION",
    "P0_2M_RAW_EXPORT_SCHEMA_VERSION",
    "build_calibration_input_from_export_dir",
    "is_auditable_adjustment_check",
    "review_saved_adjustment",
    "replay_saved_cases",
]


def is_auditable_adjustment_check(value: Any) -> bool:
    """An explicit per-symbol failure is evidence, not corrupted metadata."""
    if not isinstance(value, Mapping):
        return False
    verified = value.get("consistency_verified")
    reasons = value.get("reasons")
    checked = value.get("checked_sessions")
    if (
        not isinstance(verified, bool)
        or not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)
        or not isinstance(checked, int) or isinstance(checked, bool) or checked < 0
        or not isinstance(value.get("corporate_actions"), list)
    ):
        return False
    if not verified:
        return bool(reasons)
    if checked != 2 or reasons:
        return False
    review = value.get("multiplicative_review")
    if review is None:
        return True  # Existing collector checks remain readable.
    return (isinstance(review, Mapping)
            and review.get("schema_version") == "p0_2m_multiplicative_review_v1"
            and review.get("scale_invariance_verified") is True
            and isinstance(review.get("price_sessions_checked"), int)
            and review["price_sessions_checked"] >= 2
            and review.get("volume_sessions_checked") == review["price_sessions_checked"]
            and isinstance(review.get("common_scales_checked"), int)
            and review["common_scales_checked"] >= 1
            and review.get("saved_factors_sha256") == hashlib.sha256(json.dumps(
                value["corporate_actions"], sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest())


def review_saved_adjustment(
    minute: pd.DataFrame, daily: pd.DataFrame, *, check: Mapping[str, Any],
    historical_sessions: Sequence[date], snapshot_at: str,
    bars: Mapping[datetime, Mapping[str, Any]], calendar: Any,
) -> dict[str, Any]:
    """Recheck saved QFQ evidence without editing its manifest or downloading.

    Pure positive multiplicative factors do not change same-day log returns,
    within-day outcomes, or displacement / identically scaled ATR. Affine
    factors cannot use this proof and remain excluded. Volume is not assumed
    adjusted: independently aggregated turnover/volume must agree with the
    saved factors' unadjusted price range in both resolutions on every
    comparable session. This is a unit/price-basis audit, not an edge claim.
    """
    result = dict(check)
    reasons = [str(x) for x in check.get("reasons", ())
               if x != "corporate_actions_require_point_in_time_reconstruction"]
    review: dict[str, Any] = {
        "schema_version": "p0_2m_multiplicative_review_v1",
        "method": "positive_common_scale_invariance_and_saved_turnover_volume_price_ranges",
        "price_sessions_checked": 0, "volume_sessions_checked": 0,
        "scale_invariance_verified": False,
        "volume_basis": "provider_trade_volume_units_checked_against_unadjusted_turnover",
        "cross_action_volume_policy": "cash_distributions_only_within_historical_volume_baseline",
        "limitations": ["not_a_point_in_time_absolute_price_reconstruction", "not_independent_return_evidence"],
    }
    result["multiplicative_review"] = review
    actions = check.get("corporate_actions")
    if not is_auditable_adjustment_check(check) or not isinstance(actions, list):
        reasons.append("saved_adjustment_evidence_unverifiable")
    required = {"date", "open", "high", "low", "close", "volume", "amount"}
    if not required.issubset(minute.columns) or not required.issubset(daily.columns):
        reasons.append("volume_price_basis_columns_missing")
    else:
        for name, frame in (("minute", minute), ("daily", daily)):
            numeric = frame.loc[:, ["open", "high", "low", "close", "volume", "amount"]].apply(pd.to_numeric, errors="coerce")
            finite = not (numeric.isna() | numeric.isin([float("inf"), float("-inf")])).any().any()
            valid = (finite
                     and (numeric[["open", "high", "low", "close"]] > 0).all().all()
                     and (numeric["high"] >= numeric[["open", "close", "low"]].max(axis=1)).all()
                     and (numeric["low"] <= numeric[["open", "close", "high"]].min(axis=1)).all()
                     and (numeric[["volume", "amount"]] >= 0).all().all()
                     and ((numeric["volume"] == 0) == (numeric["amount"] == 0)).all())
            if not valid or frame.empty:
                reasons.append(f"{name}_price_volume_values_invalid")
    try:
        snapshot = pd.Timestamp(snapshot_at)
        if snapshot.tzinfo is None:
            raise ValueError("naive snapshot")
        snapshot_day = snapshot.tz_convert(_ET).date()
        first_daily = date.fromisoformat(str(daily["date"].min())[:10])
        factors = []
        for item in actions or ():
            if not isinstance(item, Mapping):
                raise ValueError("saved_factor_not_object")
            day = date.fromisoformat(str(item.get("ex_div_date"))[:10])
            if not first_daily <= day <= snapshot_day:
                continue
            a, b = _finite(item.get("forward_adj_factorA")), _finite(item.get("forward_adj_factorB"))
            if (isinstance(item.get("forward_adj_factorA"), bool)
                    or isinstance(item.get("forward_adj_factorB"), bool)
                    or a is None or a <= 0 or b is None or b != 0):
                reasons.append("nonmultiplicative_or_invalid_saved_factor")
                continue
            if historical_sessions and historical_sessions[0] < day <= historical_sessions[-1] and a != 1:
                # Raw trade shares are not split-adjusted just because prices
                # are QFQ. Cash distributions leave share units unchanged;
                # a split/join or unknown action cannot prove cross-date RVOL
                # comparability. Do not silently change only offline volume.
                share_fields = ("split_ratio", "join_base", "join_ert", "per_share_div_ratio",
                                "per_share_trans_ratio", "allotment_ratio", "stk_spo_ratio",
                                "spin_off_ratio", "add_base", "add_ert", "transfer_base", "transfer_ert")
                cash = _finite(item.get("per_cash_div"))
                if (cash is None or cash <= 0
                        or any(key not in item or (item[key] is not None and _finite(item[key]) != 0) for key in share_fields)):
                    reasons.append("cross_action_volume_basis_unverified")
            factors.append((day, a))
        if len({day for day, _ in factors}) != len(factors):
            reasons.append("duplicate_saved_factor_date")
        review["relevant_factor_count"] = len(factors)
        review["saved_factors_sha256"] = hashlib.sha256(json.dumps(actions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (TypeError, ValueError, KeyError, OverflowError):
        reasons.append("saved_factor_or_snapshot_unverifiable")
        factors = []
    if reasons:
        result.update(consistency_verified=False, reasons=list(dict.fromkeys(reasons)))
        return result

    frame = minute.copy()
    stamps = pd.to_datetime(frame["date"], utc=True).dt.tz_convert(_ET)
    frame["_stamp"] = stamps
    frame["_day"] = stamps.dt.date
    groups = {day: rows for day, rows in frame.groupby("_day", sort=False)}
    daily_groups = {str(day)[:10]: rows for day, rows in daily.groupby("date", sort=False)}
    checked_days: set[date] = set()
    scales: set[float] = {1.0}
    for day in historical_sessions:
        rows, daily_rows = groups.get(day), daily_groups.get(day.isoformat())
        if rows is None or daily_rows is None or len(daily_rows) != 1:
            continue  # Missing history stays in the original coverage denominator.
        opened, closed = _schedule(calendar, day)
        rth = rows.loc[(rows["_stamp"] > opened) & (rows["_stamp"] <= closed)].sort_values("_stamp")
        if rth.empty or rth.iloc[0]["_stamp"] != opened + timedelta(minutes=1) or rth.iloc[-1]["_stamp"] != closed:
            continue
        row = daily_rows.iloc[0]
        tolerance = max(0.01, abs(float(row["close"])) * 1e-6)
        if (abs(float(rth.iloc[0]["open"]) - float(row["open"])) > tolerance
                or abs(float(rth.iloc[-1]["close"]) - float(row["close"])) > tolerance
                or float(rth["high"].max()) > float(row["high"]) + tolerance
                or float(rth["low"].min()) < float(row["low"]) - tolerance):
            reasons.append(f"minute_daily_price_basis_mismatch:{day}")
        review["price_sessions_checked"] += 1
        scale = math.prod(a for action_day, a in factors if action_day > day)
        if not math.isfinite(scale) or scale <= 0:
            reasons.append(f"invalid_cumulative_factor:{day}")
            continue
        scales.add(scale)
        low, high = float(rows["low"].min()) / scale, float(rows["high"].max()) / scale
        values = [float(rows["volume"].sum()), float(rows["amount"].sum()), float(row["volume"]), float(row["amount"])]
        # Full-session turnover is intentional: the provider daily volume need
        # not equal RTH-only volume, and an equality check would invent a gate.
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or not all(low - tolerance <= amount / volume <= high + tolerance
                           for volume, amount in ((values[0], values[1]), (values[2], values[3])))):
            reasons.append(f"minute_daily_volume_price_unit_mismatch:{day}")
        review["volume_sessions_checked"] += 1
        checked_days.add(day)
    if not historical_sessions or not {historical_sessions[0], historical_sessions[-1]}.issubset(checked_days):
        reasons.append("price_volume_basis_endpoints_unverifiable")

    sample = None
    for day in historical_sessions:
        opened, _closed = _schedule(calendar, day)
        sample = _window(bars, opened + timedelta(minutes=18), 9)
        if sample is not None:
            break
    if sample is None:
        reasons.append("scale_invariance_window_missing")
    else:
        atr = max(float(row["high"]) for row in sample) - min(float(row["low"]) for row in sample)
        atr = max(atr, float(sample[0]["open"]) * 0.01)
        impulse, volume = summarize_p0_2m_impulse(sample[-4:])
        kwargs = {"reference_impulses": [impulse / 2] * 60, "reference_volumes": [volume / 2] * 60}
        original = compute_p0_2m_window_metrics(sample, prior_daily_atr14=atr, **kwargs)
        invariant = True
        for scale in scales:
            transformed = [{**row, **{key: float(row[key]) / scale for key in ("open", "high", "low", "close")}} for row in sample]
            actual = compute_p0_2m_window_metrics(transformed, prior_daily_atr14=atr / scale, **kwargs)
            for key in ("impulse_8m_log", "impulse_8m_percent", "displacement_atr", "displacement_18m_percent", "rvol_8m", "price_percentile"):
                invariant &= math.isclose(original[key], actual[key], rel_tol=1e-10, abs_tol=1e-10)
            invariant &= original["alert_qualified"] == actual["alert_qualified"]
        review["scale_invariance_verified"] = bool(invariant)
        review["common_scales_checked"] = len(scales)
        if not invariant:
            reasons.append("common_scale_invariance_failed")
    result.update(consistency_verified=not reasons, reasons=list(dict.fromkeys(reasons)))
    return result


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_export_path(root: Path, relative: Any) -> Path:
    value = str(relative or "").strip()
    if not value:
        raise ValueError("empty_export_file_path")
    path = (root / value).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"export_file_outside_root:{value}")
    return path


def _read_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("raw_export_manifest_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"raw_export_manifest_invalid:{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("raw_export_manifest_must_be_object")
    manifest = dict(payload)
    if manifest.get("schema_version") != P0_2M_RAW_EXPORT_SCHEMA_VERSION:
        raise ValueError("raw_export_schema_version_mismatch")
    return manifest


def _normalize_universe(value: Any) -> tuple[str, ...]:
    raw = value if isinstance(value, list) else list(DEFAULT_P0_UNIVERSE_69)
    symbols = tuple(str(item or "").strip().upper() for item in raw)
    if not symbols or any(not symbol for symbol in symbols):
        raise ValueError("raw_export_universe_contains_blank")
    if len(set(symbols)) != len(symbols):
        raise ValueError("raw_export_universe_contains_duplicate")
    return symbols


def _historical_sessions(calendar: Any, end_value: Any) -> list[date]:
    try:
        end_day = date.fromisoformat(str(end_value))
        end_session = calendar.date_to_session(end_day, direction="none")
        candidates = calendar.sessions_in_range(
            end_day - timedelta(days=450), end_session
        )
    except Exception as exc:
        raise ValueError("selection_end_session_not_xnys") from exc
    days = [stamp.date() for stamp in candidates]
    if len(days) < _HISTORICAL_SESSION_COUNT:
        raise ValueError("fewer_than_180_xnys_sessions_before_selection_end")
    return days[-_HISTORICAL_SESSION_COUNT:]


def _offline_replay_sessions(
    calendar: Any,
    *,
    historical_end: date,
    replay_end_value: Any,
) -> list[date]:
    if replay_end_value in (None, ""):
        return []
    try:
        replay_end = date.fromisoformat(str(replay_end_value))
        replay_end_session = calendar.date_to_session(replay_end, direction="none")
        first = calendar.next_session(historical_end)
        sessions = calendar.sessions_in_range(first, replay_end_session)
    except Exception as exc:
        raise ValueError("replay_end_session_not_xnys") from exc
    days = [stamp.date() for stamp in sessions]
    if len(days) != _SHADOW_SESSION_COUNT or days[-1] != replay_end:
        raise ValueError("replay_end_must_be_exactly_20_xnys_sessions_after_selection_end")
    return days


def _normalize_minute_timestamps(
    frame: pd.DataFrame,
    *,
    timezone_name: str,
) -> tuple[pd.DataFrame, int]:
    normalized: list[Optional[str]] = []
    invalid = 0
    for raw in frame["date"].tolist():
        try:
            parsed = pd.Timestamp(raw)
            if pd.isna(parsed):
                raise ValueError("timestamp_is_na")
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(
                    timezone_name, ambiguous="raise", nonexistent="raise"
                )
            else:
                parsed = parsed.tz_convert(timezone_name)
            moment = parsed.to_pydatetime()
            if moment.second or moment.microsecond:
                raise ValueError("timestamp_not_minute_aligned")
            normalized.append(moment.isoformat())
        except (TypeError, ValueError):
            normalized.append(None)
            invalid += 1
    result = frame.copy()
    result["date"] = normalized
    return result, invalid


def _load_minute_frame(
    path: Path,
    *,
    timezone_name: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_csv(path)
    missing = sorted(_MINUTE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"minute_columns_missing:{','.join(missing)}")
    normalized, invalid_timestamps = _normalize_minute_timestamps(
        frame, timezone_name=timezone_name
    )
    return normalized, {
        "raw_rows": len(frame),
        "invalid_timestamp_rows": invalid_timestamps,
        "sha256": _sha256(path),
    }


def _valid_daily_row(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    try:
        day = date.fromisoformat(str(row.get("date") or "")[:10])
    except ValueError:
        return None
    high = _finite(row.get("high"))
    low = _finite(row.get("low"))
    close = _finite(row.get("close"))
    if (
        high is None
        or low is None
        or close is None
        or high < low
        or min(high, low, close) <= 0
        or not low <= close <= high
    ):
        return None
    return {
        "date": day.isoformat(),
        "high": high,
        "low": low,
        "close": close,
    }


def _load_daily_map(path: Path) -> tuple[dict[date, dict[str, Any]], dict[str, Any]]:
    frame = pd.read_csv(path)
    missing = sorted(_DAILY_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"daily_columns_missing:{','.join(missing)}")
    parsed = [_valid_daily_row(row) for row in frame.to_dict(orient="records")]
    valid = [row for row in parsed if row is not None]
    counts = Counter(date.fromisoformat(row["date"]) for row in valid)
    duplicate_days = {day for day, count in counts.items() if count != 1}
    result = {
        date.fromisoformat(row["date"]): row
        for row in valid
        if date.fromisoformat(row["date"]) not in duplicate_days
    }
    return result, {
        "raw_rows": len(frame),
        "invalid_rows": len(frame) - len(valid),
        "duplicate_session_count": len(duplicate_days),
        "usable_sessions": len(result),
        "sha256": _sha256(path),
    }


@lru_cache(maxsize=1024)
def _schedule(calendar: Any, day: date) -> tuple[datetime, datetime]:
    opened = calendar.session_open(day).to_pydatetime().astimezone(_ET)
    closed = calendar.session_close(day).to_pydatetime().astimezone(_ET)
    return opened, closed


def _bar_map(frame: pd.DataFrame) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        moment = datetime.fromisoformat(str(row["date"])).astimezone(_ET)
        result[moment] = dict(row)
    return result


def _window(
    bars: Mapping[datetime, Mapping[str, Any]],
    end: datetime,
    count: int,
) -> Optional[list[Mapping[str, Any]]]:
    start = end - timedelta(minutes=2 * count)
    rows: list[Mapping[str, Any]] = []
    for index in range(count):
        row = bars.get(start + timedelta(minutes=2 * index))
        # The exact aggregator permits a structurally valid zero-volume bar,
        # while the production P0 evidence contract rejects it.  Preserve the
        # stricter signal boundary here instead of treating it as a baseline.
        if row is None or (_finite(row.get("volume")) or 0.0) <= 0:
            return None
        rows.append(row)
    return rows


def _rth_endpoints(calendar: Any, day: date) -> list[datetime]:
    opened, closed = _schedule(calendar, day)
    count = int((closed - opened).total_seconds() // 120)
    return [opened + timedelta(minutes=2 * index) for index in range(1, count + 1)]


def _prior_matching_sessions(
    calendar: Any,
    *,
    target_day: date,
    end: datetime,
) -> list[date]:
    # Match the production contract exactly: inspect the previous 60 XNYS
    # sessions and retain only those where this RTH clock slot exists.  A
    # half-day inside that fixed lookback may therefore leave 59 observations
    # and fail closed; it must never be replaced with an older 61st session.
    target_session = calendar.date_to_session(target_day, direction="none")
    cursor = target_session
    candidates: list[date] = []
    for _ in range(P0_2M_REFERENCE_SESSIONS):
        cursor = calendar.previous_session(cursor)
        candidates.append(cursor.date())
    candidates.reverse()
    selected: list[date] = []
    for day in candidates:
        opened, closed = _schedule(calendar, day)
        candidate_end = datetime(
            day.year,
            day.month,
            day.day,
            end.hour,
            end.minute,
            tzinfo=_ET,
        )
        if opened <= candidate_end - timedelta(minutes=8) and candidate_end <= closed:
            selected.append(day)
    return selected


def _prior_atr14(
    calendar: Any,
    daily: Mapping[date, Mapping[str, Any]],
    target_day: date,
) -> tuple[Optional[float], Optional[str]]:
    start = target_day - timedelta(days=_DAILY_LOOKBACK_CALENDAR_DAYS)
    sessions = [
        stamp.date()
        for stamp in calendar.sessions_in_range(
            start, calendar.previous_session(target_day)
        )
    ]
    consecutive_suffix: list[Mapping[str, Any]] = []
    for day in reversed(sessions):
        row = daily.get(day)
        if row is None:
            break
        consecutive_suffix.append(row)
    consecutive_suffix.reverse()
    result = compute_atr14(consecutive_suffix)
    return result.value, result.unavailable_reason


def _direction_adjusted_return(
    bars: Mapping[datetime, Mapping[str, Any]],
    *,
    end: datetime,
    trigger_price: float,
    direction: str,
) -> Optional[float]:
    # The bar starting at E+18 closes exactly 20 minutes after signal end E.
    future = bars.get(end + timedelta(minutes=18))
    if future is None:
        return None
    future_close = _finite(future.get("close"))
    if future_close is None or future_close <= 0 or trigger_price <= 0:
        return None
    signed = future_close / trigger_price - 1.0
    return signed if direction == "up" else -signed


def _outcome_20m(
    bars: Mapping[datetime, Mapping[str, Any]], *, end: datetime,
    session_close: datetime, trigger_price: float, direction: str,
    as_of: Optional[datetime] = None,
) -> dict[str, Any]:
    target = end + timedelta(minutes=20)
    if target > session_close:
        status, value = "session_censored", None
    elif as_of is not None and target > as_of:
        status, value = "pending", None
    else:
        value = _direction_adjusted_return(bars, end=end, trigger_price=trigger_price, direction=direction)
        status = "matured" if value is not None else "missing"
    return {
        "outcome_status": status,
        "outcome_window_end": target.isoformat(),
        "direction_adjusted_return_20m": round(value, 10) if value is not None else None,
    }


def _scan_regular_day(
    *,
    symbol: str,
    day: date,
    bars: Mapping[datetime, Mapping[str, Any]],
    daily: Mapping[date, Mapping[str, Any]],
    source: str,
    universe_hash: str,
    calendar: Any,
    baseline_session_cache: dict[tuple[date, int, int], tuple[date, ...]],
    reference_window_cache: Optional[
        dict[datetime, Optional[tuple[float, float]]]
    ] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    signal_rows: list[dict[str, Any]] = []
    comparator_rows: list[dict[str, Any]] = []
    stats = {
        "rth_endpoints": 0,
        "complete_18m_windows": 0,
        "complete_60_session_baselines": 0,
        "atr_ready_windows": 0,
    }
    atr14, _atr_reason = _prior_atr14(calendar, daily, day)
    opened, closed = _schedule(calendar, day)
    references = reference_window_cache if reference_window_cache is not None else {}
    for end in _rth_endpoints(calendar, day):
        stats["rth_endpoints"] += 1
        if end - timedelta(minutes=18) < opened:
            continue
        displacement_rows = _window(bars, end, 9)
        if displacement_rows is None:
            continue
        stats["complete_18m_windows"] += 1
        if atr14 is None or atr14 <= 0:
            continue
        stats["atr_ready_windows"] += 1

        impulse_rows = displacement_rows[-P0_2M_IMPULSE_BARS:]
        metrics = compute_p0_2m_window_metrics(displacement_rows, prior_daily_atr14=atr14)
        impulse_open = float(impulse_rows[0]["open"])
        trigger_price = metrics["trigger_price"]
        impulse_log = metrics["impulse_8m_log"]
        displacement_atr = metrics["displacement_atr"]
        displacement_percent = metrics["displacement_18m_percent"]
        direction = metrics["direction"]
        displacement_direction = metrics["displacement_direction"]
        displacement_outcome = _outcome_20m(
            bars, end=end, session_close=closed, trigger_price=trigger_price,
            direction=displacement_direction,
        )

        if (
            displacement_direction in {"up", "down"}
            and displacement_atr >= P0_2M_STRICT_DISPLACEMENT_ATR_MIN
        ):
            comparator_rows.append(
                {
                    "symbol": symbol,
                    "session_date": day.isoformat(),
                    "window_end": end.isoformat(),
                    "session_scope": "regular",
                    "direction": displacement_direction,
                    "state": "qualified",
                    "qualified": True,
                    "comparator_version": P0_2M_ATR_ONLY_COMPARATOR_VERSION,
                    "comparator_basis": P0_2M_ATR_ONLY_COMPARATOR_BASIS,
                    "universe_hash": universe_hash,
                    "source": source,
                    "data_quality": {"status": "complete", "issues": []},
                    "failed_checks": [],
                    "thresholds": dict(FROZEN_ATR_ONLY_COMPARATOR_THRESHOLDS),
                    "trigger_price": round(trigger_price, 6),
                    "displacement_18m_percent": round(displacement_percent, 6),
                    "displacement_atr": round(displacement_atr, 6),
                    "prior_daily_atr14": round(atr14, 6),
                    **displacement_outcome,
                }
            )

        cache_key = (day, end.hour, end.minute)
        baseline_days = list(baseline_session_cache.get(cache_key, ()))
        if not baseline_days:
            baseline_days = _prior_matching_sessions(
                calendar, target_day=day, end=end
            )
            baseline_session_cache[cache_key] = tuple(baseline_days)
        if len(baseline_days) != P0_2M_REFERENCE_SESSIONS:
            continue
        reference_impulses: list[float] = []
        reference_volumes: list[float] = []
        for baseline_day in baseline_days:
            baseline_end = datetime(
                baseline_day.year,
                baseline_day.month,
                baseline_day.day,
                end.hour,
                end.minute,
                tzinfo=_ET,
            )
            if baseline_end not in references:
                reference_rows = _window(bars, baseline_end, P0_2M_IMPULSE_BARS)
                references[baseline_end] = (
                    summarize_p0_2m_impulse(reference_rows)
                    if reference_rows is not None else None
                )
            reference = references[baseline_end]
            if reference is None:
                break
            reference_impulses.append(reference[0])
            reference_volumes.append(reference[1])
        if len(reference_impulses) != P0_2M_REFERENCE_SESSIONS:
            continue
        median_volume = float(statistics.median(reference_volumes))
        if median_volume <= 0:
            continue
        stats["complete_60_session_baselines"] += 1
        metrics = compute_p0_2m_window_metrics(
            displacement_rows, prior_daily_atr14=atr14,
            reference_impulses=reference_impulses, reference_volumes=reference_volumes,
        )
        price_percentile, rvol_8m = metrics["price_percentile"], metrics["rvol_8m"]
        if not metrics["alert_qualified"]:
            continue
        signal_outcome = _outcome_20m(
            bars, end=end, session_close=closed, trigger_price=trigger_price,
            direction=direction,
        )
        signal_rows.append(
            {
                "symbol": symbol,
                "session_date": day.isoformat(),
                "window_end": end.isoformat(),
                "session_scope": "regular",
                "direction": direction,
                "state": "qualified",
                "alert_qualified": True,
                "alert_tier": "P0",
                "signal_version": P0_2M_SIGNAL_VERSION,
                "universe_hash": universe_hash,
                "source": source,
                "data_quality": {"status": "complete", "issues": []},
                "failed_checks": [],
                "thresholds": dict(FROZEN_SIGNAL_THRESHOLDS),
                "basis": P0_2M_SIGNAL_BASIS,
                "baseline_count": len(reference_impulses),
                "baseline_first_date": baseline_days[0].isoformat(),
                "baseline_last_date": baseline_days[-1].isoformat(),
                "trigger_price": round(trigger_price, 6),
                "impulse_8m_log": round(impulse_log, 8),
                "impulse_8m_percent": round(
                    (trigger_price / impulse_open - 1.0) * 100.0, 6
                ),
                "price_percentile": round(price_percentile, 6),
                "rvol_8m": round(rvol_8m, 6),
                "reference_median_volume_8m": round(median_volume, 6),
                "displacement_18m_percent": round(displacement_percent, 6),
                "displacement_atr": round(displacement_atr, 6),
                "prior_daily_atr14": round(atr14, 6),
                **signal_outcome,
            }
        )
    return signal_rows, comparator_rows, stats


def _deduped_signal_count(rows: Sequence[Mapping[str, Any]]) -> int:
    accepted: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.get("window_end") or "")):
        if is_duplicate_p0_episode(
            symbol=str(row.get("symbol") or ""),
            direction=str(row.get("direction") or ""),
            window_end=row.get("window_end"),
            previous_events=accepted,
            cooldown_minutes=P0_2M_EPISODE_MINUTES,
        ):
            continue
        accepted.append(dict(row))
    return len(accepted)


def _asset_classes(
    manifest: Mapping[str, Any], universe: Sequence[str]
) -> dict[str, str]:
    overrides = manifest.get("asset_classes")
    provided = overrides if isinstance(overrides, Mapping) else {}
    return {
        symbol: str(provided.get(symbol) or DEFAULT_ASSET_CLASS_BY_SYMBOL.get(symbol) or "unknown")
        for symbol in universe
    }


def replay_saved_cases(
    path: str | Path, *, cases: Sequence[tuple[str, str]] = (
        ("TSLA", "2026-08-31"), ("META", "2026-09-01"), ("META", "2026-09-10"),
    ),
) -> dict[str, Any]:
    """Audit every RTH endpoint from hashed raw bars through live and batch paths.

    This is an explicit regression replay, not independent selection or
    calibration evidence. It never trusts the legacy hand-entered case summary.
    """
    root = Path(path).expanduser().resolve()
    manifest = _read_manifest(root)
    source = str(manifest.get("source") or "")
    expected_label = "end" if source in BAR_LABEL_END_SOURCES else "start"
    if manifest.get("minute_bar_label") != expected_label:
        raise ValueError("source_bar_label_contract_mismatch")
    calendar = xcals.get_calendar("XNYS")
    universe_hash = compute_universe_hash(_normalize_universe(manifest.get("universe")))
    cache: dict[str, Any] = {}
    output = []
    for symbol, day_text in cases:
        day = date.fromisoformat(day_text)
        if symbol not in cache:
            files = manifest.get("files", {}).get(symbol, {})
            minute_path = _safe_export_path(root, files.get("minute", f"{symbol}_1m.csv"))
            daily_path = _safe_export_path(root, files.get("daily", f"{symbol}_daily.csv"))
            for kind, file in (("minute", minute_path), ("daily", daily_path)):
                if _sha256(file) != files.get(f"{kind}_sha256"):
                    raise ValueError(f"replay_raw_file_hash_mismatch:{symbol}:{kind}")
            minute, _ = _load_minute_frame(minute_path, timezone_name=str(manifest.get("minute_timezone")))
            as_of = datetime.combine(date.fromisoformat(str(manifest["selection_end_session"])), time(20, 1), tzinfo=_ET)
            bars = _bar_map(aggregate_exact_2m_ohlcv(minute, source=source, as_of=as_of, calendar=calendar))
            daily, _ = _load_daily_map(daily_path)
            cache[symbol] = (bars, daily)
        bars, daily = cache[symbol]
        atr, _reason = _prior_atr14(calendar, daily, day)
        batch, _comparator, _stats = _scan_regular_day(
            symbol=symbol, day=day, bars=bars, daily=daily, source=source,
            universe_hash=universe_hash, calendar=calendar, baseline_session_cache={},
        )
        by_end = {row["window_end"]: row for row in batch}
        windows, mismatches = [], []
        for end in _rth_endpoints(calendar, day):
            current = [row for index in range(9) if (row := bars.get(end - timedelta(minutes=18 - 2 * index))) is not None]
            prior_days = _prior_matching_sessions(calendar, target_day=day, end=end)
            references = []
            for prior in prior_days:
                reference_end = end.replace(year=prior.year, month=prior.month, day=prior.day)
                references.extend(row for index in range(4) if (row := bars.get(reference_end - timedelta(minutes=8 - 2 * index))) is not None)
            live = compute_p0_2m_signal(
                current, reference_bars=references, market_date_et=day_text, as_of=end,
                prior_daily_atr14=atr, symbol=symbol, source=source,
                universe_hash=universe_hash, calendar=calendar,
            )
            fields = ("window_end", "state", "alert_qualified", "direction", "trigger_price",
                      "impulse_8m_log", "price_percentile", "rvol_8m", "displacement_atr",
                      "baseline_count", "failed_checks", "unavailable_reason")
            windows.append({key: live.get(key) for key in fields})
            offline = by_end.get(end.isoformat())
            if bool(live["alert_qualified"]) != (offline is not None):
                mismatches.append(end.isoformat())
            elif offline is not None and any(live[key] != offline[key] for key in (
                "direction", "trigger_price", "impulse_8m_log", "price_percentile", "rvol_8m", "displacement_atr", "baseline_count",
            )):
                mismatches.append(end.isoformat())
        qualified = [row for row in windows if row["alert_qualified"]]
        output.append({"symbol": symbol, "market_date_et": day_text,
                       "first_strict_crossing": qualified[0] if qualified else None,
                       "qualified_window_count": len(qualified), "windows": windows,
                       "live_batch_equivalent": not mismatches, "mismatch_window_ends": mismatches})
    return {"schema_version": "p0_2m_raw_case_replay_v1", "activation_evidence": False,
            "manifest_sha256": _sha256(root / "manifest.json"),
            "signal_version": P0_2M_SIGNAL_VERSION, "universe_hash": universe_hash,
            "cases": output, "live_batch_equivalent": all(row["live_batch_equivalent"] for row in output)}


def build_calibration_input_from_export_dir(
    path: str | Path, *, recheck_adjustment: bool = False,
) -> dict[str, Any]:
    """Build a locked calibration envelope from one raw export directory.

    Required ``manifest.json`` fields are ``schema_version``,
    ``selection_end_session``, ``source``, ``minute_bar_label`` and
    ``minute_timezone``.  Per-symbol files default to ``<SYMBOL>_1m.csv`` and
    ``<SYMBOL>_daily.csv`` and may be overridden through ``files``.
    """

    root = Path(path).expanduser().resolve()
    manifest = _read_manifest(root)
    universe = _normalize_universe(manifest.get("universe"))
    source = str(manifest.get("source") or "").strip()
    label = str(manifest.get("minute_bar_label") or "").strip().lower()
    timezone_name = str(manifest.get("minute_timezone") or "").strip()
    if not source:
        raise ValueError("raw_export_source_missing")
    if label not in {"start", "end"}:
        raise ValueError("minute_bar_label_must_be_start_or_end")
    expected_label = "end" if source in BAR_LABEL_END_SOURCES else "start"
    if label != expected_label:
        raise ValueError(
            f"source_bar_label_contract_mismatch:{source}:{label}!={expected_label}"
        )
    if timezone_name != "America/New_York":
        raise ValueError("minute_timezone_must_be_America/New_York")

    calendar = xcals.get_calendar("XNYS")
    historical = _historical_sessions(
        calendar, manifest.get("selection_end_session")
    )
    holdout = historical[-40:]
    development = historical[60:140]
    replay_days = _offline_replay_sessions(
        calendar,
        historical_end=historical[-1],
        replay_end_value=manifest.get("offline_replay_end_session"),
    )
    universe_hash = compute_universe_hash(universe)
    available_end = replay_days[-1] if replay_days else historical[-1]
    as_of = datetime.combine(available_end, time(20, 1), tzinfo=_ET)

    files = manifest.get("files") if isinstance(manifest.get("files"), Mapping) else {}
    coverage: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    comparator: list[dict[str, Any]] = []
    development_events: list[dict[str, Any]] = []
    development_comparator: list[dict[str, Any]] = []
    offline_shadow_replay: list[dict[str, Any]] = []
    derivation_errors: list[str] = []
    adjustment = manifest.get("adjustment")
    snapshot_metadata_valid = (
        isinstance(adjustment, Mapping)
        and adjustment.get("autype") == "QFQ"
        and isinstance(adjustment.get("consistency_verified"), bool)
        and bool(adjustment.get("verification_basis"))
        and bool(adjustment.get("snapshot_at"))
    )
    collected = "collection" in manifest
    collection = manifest.get("collection")
    collected_symbols = collection.get("symbols") if isinstance(collection, Mapping) else None
    collection_identity_valid = (
        isinstance(collection, Mapping)
        and collection.get("status") == "complete"
        and isinstance(collected_symbols, Mapping)
        and set(collected_symbols) == set(universe)
        and set(files) == set(universe)
        and manifest.get("universe_hash") == universe_hash
        and manifest.get("signal_version") == P0_2M_SIGNAL_VERSION
        and manifest.get("signal_thresholds") == FROZEN_SIGNAL_THRESHOLDS
        and manifest.get("historical_sessions") == [day.isoformat() for day in historical]
        and source == "MoomooFetcher"
    )
    if collected and not collection_identity_valid:
        derivation_errors.append("collection_identity_or_completeness_invalid")
    legacy_verified = snapshot_metadata_valid and adjustment.get("consistency_verified") is True
    if not snapshot_metadata_valid or (not collected and not legacy_verified):
        derivation_errors.append("adjustment_consistency_not_verified")
    per_symbol: dict[str, Any] = {}
    baseline_session_cache: dict[tuple[date, int, int], tuple[date, ...]] = {}
    valid_bar_days = set(historical) | set(replay_days)

    for symbol in universe:
        configured = files.get(symbol) if isinstance(files.get(symbol), Mapping) else {}
        minute_relative = configured.get("minute") or f"{symbol}_1m.csv"
        daily_relative = configured.get("daily") or f"{symbol}_daily.csv"
        minute_path = _safe_export_path(root, minute_relative)
        daily_path = _safe_export_path(root, daily_relative)
        symbol_report: dict[str, Any] = {
            "minute_file": str(minute_path.relative_to(root)),
            "daily_file": str(daily_path.relative_to(root)),
        }
        evidence_reasons: list[str] = []
        if collected:
            collected_symbol = collected_symbols.get(symbol) if isinstance(collected_symbols, Mapping) else None
            check = collected_symbol.get("adjustment_check") if isinstance(collected_symbol, Mapping) else None
            check_auditable = is_auditable_adjustment_check(check)
            if not check_auditable:
                derivation_errors.append(f"{symbol}:adjustment_check_unverifiable")
                evidence_reasons.append("adjustment_check_unverifiable")
            elif not check["consistency_verified"]:
                evidence_reasons.extend(check["reasons"])
            evidence_eligible = bool(
                collection_identity_valid and snapshot_metadata_valid
                and check_auditable and check["consistency_verified"]
            )
            symbol_report["adjustment"] = dict(check) if isinstance(check, Mapping) else None
            if not collection_identity_valid or not snapshot_metadata_valid:
                evidence_reasons.append("collection_snapshot_provenance_invalid")
        else:
            evidence_eligible = bool(legacy_verified)
            symbol_report["adjustment"] = dict(adjustment) if isinstance(adjustment, Mapping) else None
            if not evidence_eligible:
                evidence_reasons.append("legacy_adjustment_consistency_not_verified")
        try:
            minute_frame, minute_report = _load_minute_frame(
                minute_path, timezone_name=timezone_name
            )
            if collected and not re.fullmatch(r"[a-f0-9]{64}", str(configured.get("minute_sha256") or "")):
                raise ValueError("minute_file_hash_missing_or_invalid")
            if (
                configured.get("minute_sha256")
                and configured["minute_sha256"] != minute_report["sha256"]
            ):
                raise ValueError("minute_file_hash_mismatch")
            exact = aggregate_exact_2m_ohlcv(
                minute_frame,
                source=source,
                as_of=as_of,
                calendar=calendar,
            )
            bars = _bar_map(exact)
            symbol_report["minute"] = minute_report
            symbol_report["exact_2m_bar_count"] = len(bars)
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            derivation_errors.append(f"{symbol}:minute:{exc}")
            bars = {}
            symbol_report["minute_error"] = str(exc)
            evidence_eligible = False
            evidence_reasons.append(f"minute:{exc}")
        try:
            daily, daily_report = _load_daily_map(daily_path)
            if collected and not re.fullmatch(r"[a-f0-9]{64}", str(configured.get("daily_sha256") or "")):
                raise ValueError("daily_file_hash_missing_or_invalid")
            if (
                configured.get("daily_sha256")
                and configured["daily_sha256"] != daily_report["sha256"]
            ):
                raise ValueError("daily_file_hash_mismatch")
            symbol_report["daily"] = daily_report
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            derivation_errors.append(f"{symbol}:daily:{exc}")
            daily = {}
            symbol_report["daily_error"] = str(exc)
            evidence_eligible = False
            evidence_reasons.append(f"daily:{exc}")

        if (recheck_adjustment and collected and check_auditable
                and "minute_error" not in symbol_report and "daily_error" not in symbol_report):
            symbol_report["original_adjustment"] = dict(check)
            try:
                check = review_saved_adjustment(
                    minute_frame, pd.read_csv(daily_path), check=check,
                    historical_sessions=historical,
                    snapshot_at=str(adjustment.get("snapshot_at") or ""),
                    bars=bars, calendar=calendar,
                )
            except (TypeError, ValueError, KeyError, OverflowError) as exc:
                check = {**check, "consistency_verified": False,
                         "reasons": [f"adjustment_review_unverifiable:{type(exc).__name__}"]}
            evidence_reasons = list(check["reasons"])
            evidence_eligible = bool(collection_identity_valid and snapshot_metadata_valid and check["consistency_verified"])
            if not collection_identity_valid or not snapshot_metadata_valid:
                evidence_reasons.append("collection_snapshot_provenance_invalid")
            symbol_report["adjustment"] = check
        symbol_report["evidence_eligible"] = evidence_eligible
        symbol_report["evidence_exclusion_reasons"] = evidence_reasons

        rth_bar_counts: Counter[date] = Counter()
        structural_bar_counts: Counter[date] = Counter()
        for start, bar in bars.items():
            if start.date() not in valid_bar_days:
                continue
            opened, closed = _schedule(calendar, start.date())
            if opened <= start and start + timedelta(minutes=2) <= closed:
                structural_bar_counts[start.date()] += 1
                if (_finite(bar.get("volume")) or 0.0) > 0:
                    rth_bar_counts[start.date()] += 1

        for day in historical[:60]:
            opened, closed = _schedule(calendar, day)
            coverage.append(
                {
                    "symbol": symbol,
                    "session_date": day.isoformat(),
                    # Conservative strict-2m completeness: if either source
                    # minute is unusable, neither minute in that pair counts.
                    "complete_minutes": rth_bar_counts[day] * 2 if evidence_eligible else 0,
                    "observed_complete_minutes": rth_bar_counts[day] * 2,
                    "structurally_complete_minutes": structural_bar_counts[day] * 2,
                    "evidence_eligible": evidence_eligible,
                    "exclusion_reasons": evidence_reasons,
                    "eligible_minutes": int((closed - opened).total_seconds() // 60),
                }
            )

        scan_totals: Counter[str] = Counter()
        development_totals: Counter[str] = Counter()
        raw_event_counts = {
            "development": {"strict": 0, "atr_only": 0},
            "holdout": {"strict": 0, "atr_only": 0},
        }
        # Per-symbol only: reuse immutable reference windows across dates,
        # never borrow another ticker's values or cache current decisions.
        reference_window_cache: dict[datetime, Optional[tuple[float, float]]] = {}
        for day in development + holdout:
            strict_rows, comparator_rows, stats = _scan_regular_day(
                symbol=symbol,
                day=day,
                bars=bars,
                daily=daily,
                source=source,
                universe_hash=universe_hash,
                calendar=calendar,
                baseline_session_cache=baseline_session_cache,
                reference_window_cache=reference_window_cache,
            )
            if day in development:
                raw_event_counts["development"]["strict"] += len(strict_rows)
                raw_event_counts["development"]["atr_only"] += len(comparator_rows)
                if evidence_eligible:
                    development_events.extend(strict_rows)
                    development_comparator.extend(comparator_rows)
                development_totals.update(stats)
            else:
                raw_event_counts["holdout"]["strict"] += len(strict_rows)
                raw_event_counts["holdout"]["atr_only"] += len(comparator_rows)
                if evidence_eligible:
                    events.extend(strict_rows)
                    comparator.extend(comparator_rows)
                scan_totals.update(stats)

        for day in replay_days:
            strict_rows, _comparator_rows, stats = _scan_regular_day(
                symbol=symbol,
                day=day,
                bars=bars,
                daily=daily,
                source=source,
                universe_hash=universe_hash,
                calendar=calendar,
                baseline_session_cache=baseline_session_cache,
                reference_window_cache=reference_window_cache,
            )
            scan_totals.update(stats)
            opened, closed = _schedule(calendar, day)
            expected = int((closed - opened).total_seconds() // 120)
            available = rth_bar_counts[day]
            would_notify_count = _deduped_signal_count(strict_rows)
            row: dict[str, Any] = {
                "symbol": symbol,
                "session_date": day.isoformat(),
                "session_scope": "regular",
                "expected_evaluations": expected,
                "available_evaluations": available,
                "duplicate_notification_count": 0,
                "unfinished_bar_trigger_count": 0,
                "would_notify": would_notify_count > 0,
                "would_notify_count": would_notify_count,
                "observation_mode": "offline_static_replay",
                "evidence_eligible": evidence_eligible,
                "telegram_latency_evidence": "missing_no_live_enqueue_log",
            }
            offline_shadow_replay.append(row)

        symbol_report["scan_totals"] = dict(scan_totals)
        symbol_report["development_scan_totals"] = dict(development_totals)
        symbol_report["raw_event_counts"] = raw_event_counts
        per_symbol[symbol] = symbol_report

    manifest_path = root / "manifest.json"
    return {
        "schema_version": P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
        "signal_version": P0_2M_SIGNAL_VERSION,
        "universe_hash": universe_hash,
        "universe": list(universe),
        "asset_classes": _asset_classes(manifest, universe),
        "historical_sessions": [day.isoformat() for day in historical],
        # The prospective period starts only after this envelope is frozen.
        # Offline future bars, when explicitly supplied, remain diagnostics.
        "shadow_sessions": [],
        "shadow_observation_mode": "not_collected",
        "coverage": coverage,
        "events": events,
        "atr_only_events": comparator,
        "development_events": development_events,
        "development_atr_only_events": development_comparator,
        "shadow": [],
        "producer_metadata": {
            "producer_version": P0_2M_INPUT_PRODUCER_VERSION,
            "raw_export_schema_version": P0_2M_RAW_EXPORT_SCHEMA_VERSION,
            "manifest_sha256": _sha256(manifest_path),
            "frozen_at": manifest.get("frozen_at"),
            "selection_end_session": historical[-1].isoformat(),
            "source": source,
            "minute_bar_label": label,
            "minute_timezone": timezone_name,
            "adjustment": manifest.get("adjustment"),
            "adjustment_validation_scope": "per_symbol_collection" if collected else "legacy_snapshot",
            "adjustment_review_requested": recheck_adjustment,
            "historical_independence": "not_established_preselected_cases",
            "economic_edge_verified": False,
            "exact_2m_aggregation_contract": EXACT_2M_AGGREGATION_CONTRACT,
            "atr14_method": ATR14_METHOD,
            "daily_loader_lookback_calendar_days": _DAILY_LOOKBACK_CALENDAR_DAYS,
            "outcome_basis": "close_at_window_end_plus_20m_over_trigger_close",
            "shadow_observation_mode": "not_collected",
            "telegram_latency_evidence": "missing_no_live_enqueue_log",
            "offline_shadow_replay": {
                "observation_mode": "offline_static_replay",
                "activation_evidence": False,
                "sessions": [day.isoformat() for day in replay_days],
                "rows": offline_shadow_replay,
            },
            "derivation_errors": derivation_errors,
            "per_symbol": per_symbol,
        },
    }
