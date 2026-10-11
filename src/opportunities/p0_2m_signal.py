# -*- coding: utf-8 -*-
"""Strict two-minute P0/P0X signal evidence (pure computation).

The module deliberately does not fetch, resample, rank a universe, select an
option contract, or send a notification.  Callers inject already normalised,
start-labelled two-minute bars.  Every price/volume baseline uses the exact
same eight-minute ET clock slot from prior XNYS sessions; the target session
and future rows can never enter the denominator.

This is an anomaly contract, not a return forecast.  In particular, the old
five-minute ``burst_score`` product is not accepted or consulted here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import statistics
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

_ET = ZoneInfo("America/New_York")
_PREMARKET_OPEN_MINUTE_ET = 4 * 60
_EXTENDED_CLOSE_MINUTE_ET = 20 * 60

P0_2M_SIGNAL_VERSION = "p0_2m_signal_v1"
P0_2M_BAR_MINUTES = 2
P0_2M_IMPULSE_BARS = 4
P0_2M_DISPLACEMENT_BARS = 9
P0_2M_REFERENCE_SESSIONS = 60
P0_2M_PRICE_PERCENTILE_MIN = 0.95
P0_2M_RVOL_MIN = 1.50
P0_2M_FORMING_DISPLACEMENT_ATR_MIN = 0.30
P0_2M_STRICT_DISPLACEMENT_ATR_MIN = 0.50
P0_2M_EPISODE_MINUTES = 30

P0_2M_SIGNAL_BASIS = (
    "four_exact_completed_2m_bars_log_close_over_open;"
    "absolute_impulse_empirical_cdf_vs_60_prior_xnys_same_session_same_clock;"
    "eight_minute_volume_vs_prior_same_slot_median;"
    "nine_exact_completed_2m_bars_net_displacement_over_prior_daily_atr14;"
    "price_percentile_gte_0.95;rvol_gte_1.50;"
    "same_direction;strict_displacement_atr_gte_0.50"
)
P0_2M_SIGNAL_LIMITATIONS = (
    "The rule detects an unusual realised move; it does not establish forward-return edge.",
    "All input bars must be provider-normalised, start-labelled, exact two-minute bars.",
    "P0X identifies an extended-hours underlying event; it does not imply an executable equity-option market.",
)

__all__ = [
    "P0_2M_EPISODE_MINUTES",
    "P0_2M_FORMING_DISPLACEMENT_ATR_MIN",
    "P0_2M_PRICE_PERCENTILE_MIN",
    "P0_2M_REFERENCE_SESSIONS",
    "P0_2M_RVOL_MIN",
    "P0_2M_SIGNAL_BASIS",
    "P0_2M_SIGNAL_VERSION",
    "P0_2M_STRICT_DISPLACEMENT_ATR_MIN",
    "classify_p0_session",
    "compute_p0_2m_signal",
    "compute_p0_2m_window_metrics",
    "summarize_p0_2m_impulse",
    "is_duplicate_p0_episode",
    "p0_episode_key",
    "unavailable_p0_2m_signal",
]


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _aware_et(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(_ET)


def _minute_of_day(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def classify_p0_session(
    window_start: Any,
    window_end: Any,
    *,
    regular_open_minute_et: int = 9 * 60 + 30,
    regular_close_minute_et: int = 16 * 60,
) -> Optional[str]:
    """Classify a complete ET interval as premarket, regular, or afterhours.

    An interval crossing a session boundary is intentionally unclassifiable.
    ``window_end`` is the exclusive bar end, so a 09:12--09:30 interval is
    premarket while 09:30--09:48 is regular.
    """

    start = _aware_et(window_start)
    end = _aware_et(window_end)
    if start is None or end is None or end <= start or start.date() != end.date():
        return None
    if start.second or start.microsecond or end.second or end.microsecond:
        return None
    start_minute = _minute_of_day(start)
    end_minute = _minute_of_day(end)
    if _PREMARKET_OPEN_MINUTE_ET <= start_minute and end_minute <= regular_open_minute_et:
        return "premarket"
    if regular_open_minute_et <= start_minute and end_minute <= regular_close_minute_et:
        return "regular"
    if regular_close_minute_et <= start_minute and end_minute <= _EXTENDED_CLOSE_MINUTE_ET:
        return "afterhours"
    return None


def _thresholds() -> dict[str, Any]:
    return {
        "bar_minutes": P0_2M_BAR_MINUTES,
        "impulse_bars": P0_2M_IMPULSE_BARS,
        "displacement_bars": P0_2M_DISPLACEMENT_BARS,
        "reference_sessions": P0_2M_REFERENCE_SESSIONS,
        "price_percentile_min": P0_2M_PRICE_PERCENTILE_MIN,
        "rvol_8m_min": P0_2M_RVOL_MIN,
        "forming_displacement_atr_min": P0_2M_FORMING_DISPLACEMENT_ATR_MIN,
        "strict_displacement_atr_min": P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
    }


def _empty_research_fields(reason: str) -> dict[str, Any]:
    """Non-gating diagnostics are explicit even when they cannot be computed."""

    return {
        "path_efficiency_8m": {
            "state": "unavailable",
            "value": None,
            "reason": reason,
            "basis": (
                "abs(C4-O1)/(abs(C1-O1)+sum(abs(Ci-Ci-1)));research_only"
            ),
        },
        "vwap_distance_8m_percent": {
            "state": "unavailable",
            "value": None,
            "reason": reason,
            "basis": "(C4/(sum(amount)/sum(volume))-1)*100;research_only",
        },
        "market_residual_8m": {
            "state": "unavailable",
            "value": None,
            "reason": "benchmark_model_not_configured",
            "basis": "research_only;not_a_gate",
        },
    }


def unavailable_p0_2m_signal(
    reason: str,
    *,
    state: str = "unavailable",
    symbol: Optional[str] = None,
    universe_hash: Optional[str] = None,
    session_scope: Optional[str] = None,
    source: Optional[str] = None,
    baseline_count: int = 0,
    window_end: Optional[str] = None,
    option_candidates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a versioned fail-closed evidence payload."""

    return {
        "state": state,
        "alert_qualified": False,
        "alert_tier": None,
        "signal_version": P0_2M_SIGNAL_VERSION,
        "symbol": str(symbol or "").strip().upper() or None,
        "universe_hash": str(universe_hash or "").strip() or None,
        "session_scope": session_scope,
        "window_start": None,
        "impulse_window_start": None,
        "displacement_window_start": None,
        "window_end": window_end,
        "direction": None,
        "trigger_price": None,
        "impulse_8m_log": None,
        "impulse_8m_percent": None,
        "price_percentile": None,
        "rvol_8m": None,
        "reference_median_volume_8m": None,
        "displacement_18m_percent": None,
        "displacement_atr": None,
        "prior_daily_atr14": None,
        "baseline_count": int(baseline_count),
        "baseline_first_date": None,
        "baseline_last_date": None,
        "source": str(source or "").strip() or None,
        "data_quality": {"status": "incomplete", "issues": [reason]},
        "option_candidates": [dict(item) for item in option_candidates],
        "research_fields": _empty_research_fields("signal_window_unavailable"),
        "failed_checks": [],
        "unavailable_reason": reason,
        "thresholds": _thresholds(),
        "basis": P0_2M_SIGNAL_BASIS,
        "limitations": list(P0_2M_SIGNAL_LIMITATIONS),
    }


def _normalise_bar(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    start = _aware_et(row.get("date"))
    open_ = _finite(row.get("open"))
    high = _finite(row.get("high"))
    low = _finite(row.get("low"))
    close = _finite(row.get("close"))
    volume = _finite(row.get("volume"))
    amount = _finite(row.get("amount"))
    if (
        start is None
        or start.second
        or start.microsecond
        or open_ is None
        or high is None
        or low is None
        or close is None
        or volume is None
        or min(open_, high, low, close) <= 0
        or volume <= 0
        or high < low
        or high < max(open_, close)
        or low > min(open_, close)
    ):
        return None
    return {
        "start_et": start,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        # Dollar turnover is not a signal gate.  Keep it only when usable so
        # the research-only VWAP diagnostic can state ready/unavailable.
        "amount": amount if amount is not None and amount >= 0 else None,
    }


def _research_fields_for_impulse(
    rows: Sequence[Mapping[str, Any]],
    *,
    impulse_open: float,
    trigger_price: float,
) -> dict[str, Any]:
    research = _empty_research_fields("input_not_available")

    prior = impulse_open
    travelled = 0.0
    for row in rows:
        close = float(row["close"])
        travelled += abs(close - prior)
        prior = close
    if travelled > 0:
        research["path_efficiency_8m"] = {
            "state": "ready",
            "value": round(abs(trigger_price - impulse_open) / travelled, 6),
            "reason": None,
            "basis": (
                "abs(C4-O1)/(abs(C1-O1)+sum(abs(Ci-Ci-1)));research_only"
            ),
        }
    else:
        research["path_efficiency_8m"]["reason"] = "zero_path_length"

    amounts = [_finite(row.get("amount")) for row in rows]
    total_volume = sum(float(row["volume"]) for row in rows)
    if (
        total_volume > 0
        and all(amount is not None and amount >= 0 for amount in amounts)
    ):
        vwap = sum(float(amount) for amount in amounts if amount is not None) / total_volume
        if vwap > 0:
            research["vwap_distance_8m_percent"] = {
                "state": "ready",
                "value": round((trigger_price / vwap - 1.0) * 100.0, 6),
                "reason": None,
                "basis": "(C4/(sum(amount)/sum(volume))-1)*100;research_only",
            }
    else:
        research["vwap_distance_8m_percent"]["reason"] = (
            "dollar_turnover_unavailable"
        )
    return research


def _bar_index(
    bars: Sequence[Mapping[str, Any]],
) -> dict[datetime, list[Optional[dict[str, Any]]]]:
    """Retain duplicate and malformed rows so exact windows fail closed."""

    indexed: dict[datetime, list[Optional[dict[str, Any]]]] = {}
    for raw in bars or ():
        start = _aware_et(raw.get("date"))
        if start is None:
            continue
        indexed.setdefault(start, []).append(_normalise_bar(raw))
    return indexed


def _exact_window(
    indexed: Mapping[datetime, Sequence[Optional[Mapping[str, Any]]]],
    starts: Sequence[datetime],
) -> Optional[list[Mapping[str, Any]]]:
    rows: list[Mapping[str, Any]] = []
    for start in starts:
        matches = list(indexed.get(start, ()))
        if len(matches) != 1 or matches[0] is None:
            return None
        rows.append(matches[0])
    return rows


def _session_schedule(calendar: Any, session: Any) -> tuple[datetime, datetime]:
    open_at = _aware_et(calendar.session_open(session))
    close_at = _aware_et(calendar.session_close(session))
    if open_at is None or close_at is None or close_at <= open_at:
        raise ValueError("invalid_xnys_schedule")
    return open_at, close_at


def _latest_completed_end(
    observed_at: datetime,
    *,
    open_at: datetime,
    close_at: datetime,
) -> Optional[datetime]:
    premarket_at = observed_at.replace(hour=4, minute=0, second=0, microsecond=0)
    extended_close_at = observed_at.replace(
        hour=20, minute=0, second=0, microsecond=0
    )
    if observed_at < premarket_at or observed_at >= extended_close_at:
        return None
    if observed_at < open_at:
        anchor = premarket_at
    elif observed_at < close_at:
        anchor = open_at
    else:
        anchor = close_at
    completed = int((observed_at - anchor).total_seconds() // 120)
    # During the first two minutes of a new stratum, its boundary is also the
    # completed endpoint of the preceding stratum.  The full-window classifier
    # below decides which side owns the evidence.
    return anchor + timedelta(minutes=completed * P0_2M_BAR_MINUTES)


def _matching_prior_sessions(
    calendar: Any,
    *,
    target_session: Any,
    window_start: datetime,
    window_end: datetime,
    session_scope: str,
) -> list[date]:
    """Resolve matching clock slots inside exactly the prior 60 sessions.

    The lookback is defined in trading days, not in a variable number of
    matching observations.  An early-close session whose same clock time falls
    in a different session scope is therefore omitted and makes the downstream
    60-observation baseline fail closed; it must not be replaced with an older
    day outside the declared lookback.
    """

    selected: list[date] = []
    cursor = target_session
    for _ in range(P0_2M_REFERENCE_SESSIONS):
        cursor = calendar.previous_session(cursor)
        open_at, close_at = _session_schedule(calendar, cursor)
        historical_start = window_start.replace(
            year=cursor.year, month=cursor.month, day=cursor.day
        )
        historical_end = window_end.replace(
            year=cursor.year, month=cursor.month, day=cursor.day
        )
        scope = classify_p0_session(
            historical_start,
            historical_end,
            regular_open_minute_et=_minute_of_day(open_at),
            regular_close_minute_et=_minute_of_day(close_at),
        )
        if scope == session_scope:
            selected.append(cursor.date())
    selected.reverse()
    return selected


def _starts_ending_at(end: datetime, count: int) -> list[datetime]:
    first = end - timedelta(minutes=count * P0_2M_BAR_MINUTES)
    return [
        first + timedelta(minutes=index * P0_2M_BAR_MINUTES)
        for index in range(count)
    ]


def _percentile_rank(values: Sequence[float], current: float) -> float:
    return sum(value <= current for value in values) / len(values)


def summarize_p0_2m_impulse(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    """Summarize four already validated exact bars; shared by live and replay."""
    if len(rows) != P0_2M_IMPULSE_BARS:
        raise ValueError("impulse_requires_four_exact_bars")
    return (
        abs(math.log(float(rows[-1]["close"]) / float(rows[0]["open"]))),
        sum(float(row["volume"]) for row in rows),
    )


def compute_p0_2m_window_metrics(
    rows: Sequence[Mapping[str, Any]], *, prior_daily_atr14: float,
    reference_impulses: Sequence[float] = (), reference_volumes: Sequence[float] = (),
) -> dict[str, Any]:
    """One numerical rule for live and offline callers after exact-window validation.

    Empty references permit the frozen ATR-only comparator to be evaluated;
    they never qualify strict P0. No timestamps or future observations are
    selected here: each caller retains its own audited window acquisition.
    """
    if len(rows) != P0_2M_DISPLACEMENT_BARS or not math.isfinite(prior_daily_atr14) or prior_daily_atr14 <= 0:
        raise ValueError("metrics_require_nine_exact_bars_and_positive_prior_atr")
    impulse = rows[-P0_2M_IMPULSE_BARS:]
    opened, price = float(impulse[0]["open"]), float(impulse[-1]["close"])
    displacement_open = float(rows[0]["open"])
    impulse_log = math.log(price / opened)
    displacement = price - displacement_open
    direction = "up" if impulse_log > 0 else "down" if impulse_log < 0 else "flat"
    displacement_direction = "up" if displacement > 0 else "down" if displacement < 0 else "flat"
    displacement_atr = abs(displacement) / prior_daily_atr14
    same_direction = direction in {"up", "down"} and direction == displacement_direction
    ready = len(reference_impulses) == len(reference_volumes) == P0_2M_REFERENCE_SESSIONS
    median_volume = float(statistics.median(reference_volumes)) if ready else None
    percentile = _percentile_rank(reference_impulses, abs(impulse_log)) if ready else None
    rvol = sum(float(row["volume"]) for row in impulse) / median_volume if median_volume and median_volume > 0 else None
    failed = []
    if percentile is None or percentile < P0_2M_PRICE_PERCENTILE_MIN:
        failed.append("price_percentile_below_0.95")
    if rvol is None or rvol < P0_2M_RVOL_MIN:
        failed.append("rvol_8m_below_1.50")
    if not same_direction:
        failed.append("impulse_displacement_direction_mismatch")
    short_pass = not failed
    if displacement_atr < P0_2M_STRICT_DISPLACEMENT_ATR_MIN:
        failed.append("displacement_atr_below_0.50")
    qualified = not failed
    forming = short_pass and P0_2M_FORMING_DISPLACEMENT_ATR_MIN <= displacement_atr < P0_2M_STRICT_DISPLACEMENT_ATR_MIN
    return {
        "trigger_price": price, "impulse_8m_log": impulse_log,
        "impulse_8m_percent": (price / opened - 1.0) * 100.0,
        "displacement_18m_percent": (price / displacement_open - 1.0) * 100.0,
        "displacement_atr": displacement_atr, "direction": direction,
        "displacement_direction": displacement_direction, "same_direction": same_direction,
        "price_percentile": percentile, "rvol_8m": rvol,
        "reference_median_volume_8m": median_volume, "alert_qualified": qualified,
        "state": "qualified" if qualified else "forming" if forming else "not_qualified",
        "failed_checks": failed,
    }


def compute_p0_2m_signal(
    current_bars: Sequence[Mapping[str, Any]],
    *,
    reference_bars: Sequence[Mapping[str, Any]],
    market_date_et: str,
    as_of: Any,
    prior_daily_atr14: Any,
    symbol: str,
    source: str,
    universe_hash: Optional[str] = None,
    option_candidates: Sequence[Mapping[str, Any]] = (),
    calendar: Any = None,
) -> dict[str, Any]:
    """Compute one strict P0/P0X evidence row from completed two-minute bars.

    ``price_percentile`` is an empirical CDF on the 0--1 scale.  Exactly 60
    target-day-exclusive baselines are mandatory.  The latest bar expected at
    ``as_of`` and all preceding bars in the 18-minute window must exist exactly
    once; no gap, duplicate, malformed row, or forming bar is tolerated.
    """

    observed_at = _aware_et(as_of)
    try:
        market_date = date.fromisoformat(str(market_date_et))
    except (TypeError, ValueError):
        market_date = None
    common = {
        "symbol": symbol,
        "universe_hash": universe_hash,
        "source": source,
        "option_candidates": option_candidates,
    }
    if observed_at is None:
        return unavailable_p0_2m_signal("as_of_unparseable_or_naive", **common)
    if market_date is None:
        return unavailable_p0_2m_signal("market_date_invalid", **common)
    if observed_at.date() != market_date:
        return unavailable_p0_2m_signal("as_of_market_date_mismatch", **common)

    try:
        xnys = calendar if calendar is not None else xcals.get_calendar("XNYS")
        target_session = xnys.date_to_session(market_date, direction="none")
        open_at, close_at = _session_schedule(xnys, target_session)
    except Exception:
        return unavailable_p0_2m_signal("xnys_schedule_unavailable", **common)

    expected_end = _latest_completed_end(
        observed_at,
        open_at=open_at,
        close_at=close_at,
    )
    if expected_end is None:
        return unavailable_p0_2m_signal("outside_extended_session", **common)
    displacement_starts = _starts_ending_at(
        expected_end, P0_2M_DISPLACEMENT_BARS
    )
    displacement_start = displacement_starts[0]
    session_scope = classify_p0_session(
        displacement_start,
        expected_end,
        regular_open_minute_et=_minute_of_day(open_at),
        regular_close_minute_et=_minute_of_day(close_at),
    )
    window_end_iso = expected_end.isoformat()
    if session_scope is None:
        return unavailable_p0_2m_signal(
            "fewer_than_18_completed_minutes_in_session_stratum",
            state="pending",
            session_scope=None,
            window_end=window_end_iso,
            **common,
        )

    current_index = _bar_index(current_bars)
    displacement_rows = _exact_window(current_index, displacement_starts)
    if displacement_rows is None:
        return unavailable_p0_2m_signal(
            "current_18m_window_missing_duplicate_or_invalid",
            session_scope=session_scope,
            window_end=window_end_iso,
            **common,
        )
    impulse_rows = displacement_rows[-P0_2M_IMPULSE_BARS:]
    impulse_start = impulse_rows[0]["start_et"]

    atr14 = _finite(prior_daily_atr14)
    if atr14 is None or atr14 <= 0:
        return unavailable_p0_2m_signal(
            "prior_daily_atr14_unavailable",
            session_scope=session_scope,
            window_end=window_end_iso,
            **common,
        )

    try:
        baseline_dates = _matching_prior_sessions(
            xnys,
            target_session=target_session,
            window_start=impulse_start,
            window_end=expected_end,
            session_scope=session_scope,
        )
    except Exception:
        return unavailable_p0_2m_signal(
            "prior_xnys_schedule_unavailable",
            session_scope=session_scope,
            window_end=window_end_iso,
            **common,
        )
    if len(baseline_dates) < P0_2M_REFERENCE_SESSIONS:
        return unavailable_p0_2m_signal(
            "fewer_than_60_prior_matching_xnys_sessions",
            session_scope=session_scope,
            window_end=window_end_iso,
            **common,
        )

    reference_index = _bar_index(reference_bars)
    reference_impulses: list[float] = []
    reference_volumes: list[float] = []
    valid_baseline_dates: list[str] = []
    for baseline_date in baseline_dates:
        historical_end = expected_end.replace(
            year=baseline_date.year,
            month=baseline_date.month,
            day=baseline_date.day,
        )
        rows = _exact_window(
            reference_index,
            _starts_ending_at(historical_end, P0_2M_IMPULSE_BARS),
        )
        if rows is None:
            continue
        reference_impulse, reference_volume = summarize_p0_2m_impulse(rows)
        reference_impulses.append(reference_impulse)
        reference_volumes.append(reference_volume)
        valid_baseline_dates.append(baseline_date.isoformat())

    baseline_count = len(valid_baseline_dates)
    if baseline_count < P0_2M_REFERENCE_SESSIONS:
        return unavailable_p0_2m_signal(
            "fewer_than_60_complete_same_slot_baselines",
            session_scope=session_scope,
            baseline_count=baseline_count,
            window_end=window_end_iso,
            **common,
        )

    reference_median_volume = float(statistics.median(reference_volumes))
    if reference_median_volume <= 0:
        return unavailable_p0_2m_signal(
            "reference_median_volume_nonpositive",
            session_scope=session_scope,
            baseline_count=baseline_count,
            window_end=window_end_iso,
            **common,
        )

    impulse_open = float(impulse_rows[0]["open"])
    metrics = compute_p0_2m_window_metrics(
        displacement_rows, prior_daily_atr14=atr14,
        reference_impulses=reference_impulses, reference_volumes=reference_volumes,
    )
    qualified, state = metrics["alert_qualified"], metrics["state"]
    trigger_price = metrics["trigger_price"]
    result = unavailable_p0_2m_signal(
        "qualified" if qualified else "thresholds_not_met",
        state=state,
        session_scope=session_scope,
        baseline_count=baseline_count,
        window_end=window_end_iso,
        **common,
    )
    result.update(
        alert_qualified=qualified,
        alert_tier=("P0" if session_scope == "regular" else "P0X") if qualified else None,
        window_start=displacement_start.isoformat(),
        impulse_window_start=impulse_start.isoformat(),
        displacement_window_start=displacement_start.isoformat(),
        direction=metrics["direction"],
        trigger_price=round(trigger_price, 6),
        impulse_8m_log=round(metrics["impulse_8m_log"], 8),
        impulse_8m_percent=round(metrics["impulse_8m_percent"], 6),
        price_percentile=round(metrics["price_percentile"], 6),
        rvol_8m=round(metrics["rvol_8m"], 6),
        reference_median_volume_8m=round(reference_median_volume, 6),
        displacement_18m_percent=round(metrics["displacement_18m_percent"], 6),
        displacement_atr=round(metrics["displacement_atr"], 6),
        prior_daily_atr14=round(atr14, 6),
        baseline_first_date=valid_baseline_dates[0],
        baseline_last_date=valid_baseline_dates[-1],
        data_quality={"status": "complete", "issues": []},
        research_fields=_research_fields_for_impulse(
            impulse_rows,
            impulse_open=impulse_open,
            trigger_price=trigger_price,
        ),
        failed_checks=metrics["failed_checks"],
        unavailable_reason=None if qualified else "thresholds_not_met",
    )
    return result


def p0_episode_key(symbol: str, direction: str) -> str:
    """Return the stable identity used by a 30-minute direction episode."""

    return f"{P0_2M_SIGNAL_VERSION}:{str(symbol).strip().upper()}:{direction}"


def is_duplicate_p0_episode(
    *,
    symbol: str,
    direction: str,
    window_end: Any,
    previous_events: Sequence[Mapping[str, Any]],
    cooldown_minutes: int = P0_2M_EPISODE_MINUTES,
) -> bool:
    """Whether a same-symbol/same-direction event exists in the trailing window."""

    candidate_end = _aware_et(window_end)
    if candidate_end is None or cooldown_minutes <= 0:
        return False
    wanted_key = p0_episode_key(symbol, direction)
    for event in previous_events or ():
        prior_end = _aware_et(event.get("window_end"))
        if prior_end is None:
            continue
        event_key = p0_episode_key(
            str(event.get("symbol") or ""), str(event.get("direction") or "")
        )
        elapsed = (candidate_end - prior_end).total_seconds() / 60.0
        if event_key == wanted_key and 0 <= elapsed < cooldown_minutes:
            return True
    return False
