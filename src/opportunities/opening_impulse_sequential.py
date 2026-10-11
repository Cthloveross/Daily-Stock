# -*- coding: utf-8 -*-
"""Sequential 09:31--09:35 opening-impulse profile (pure computation).

The input one-minute bars must already use the provider-normalised **bar start**
timestamp convention.  At checkpoint ``N`` (09:30 + N minutes), only the first
``N`` completed one-minute bars are aggregated.  Their volume and range are
compared with the exact same cumulative interval from the preceding 20 XNYS
sessions.  This prevents both future leakage and opening intraday-seasonality
leakage.

Checkpoints 1--4 require two adjacent, same-direction strict passes.  At 09:35
the fifth completed one-minute bar is aggregated with the preceding four and
evaluated once; no native five-minute bar is consulted.  Consequently the
earliest possible alert is 09:32 ET.  The module fetches no market data and
does not retain state between calls.
"""
from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from src.opportunities.intraday_bursts import filter_regular_session_bars
from src.opportunities.opening_impulse import (
    OPENING_REFERENCE_SESSIONS,
    compute_opening_impulse_profile,
    unavailable_opening_impulse_profile,
)

_NEW_YORK = ZoneInfo("America/New_York")
_OPEN_MINUTE_ET = 9 * 60 + 30
_EARLY_CHECKPOINTS = (1, 2, 3, 4)
_FINAL_CHECKPOINT = 5

OPENING_IMPULSE_SEQUENTIAL_RULE_VERSION = "opening_impulse_v2"
OPENING_IMPULSE_SEQUENTIAL_BASIS = (
    "sequential_completed_1m_opening_cumulative_vs_exact_prior_20_xnys_same_elapsed;"
    "09:31-09:34_two_consecutive_same_direction_strict_passes;"
    "09:35_five_exact_completed_1m_single_confirmation;"
    "strict_thresholds_identical_to_opening_impulse_v1"
)
OPENING_IMPULSE_SEQUENTIAL_LIMITATIONS = (
    "09:31--09:34 uses completed, start-labelled 1m bars only; a forming or missing bar is never inferred.",
    "Each early checkpoint uses the exact preceding 20 XNYS sessions at the same cumulative elapsed minute.",
    "Two adjacent strict passes must have the same direction; 09:35 instead uses one strict five-completed-1m cumulative confirmation.",
    "This is an opening-anomaly candidate detector, not a trade-entry or return forecast.",
)

__all__ = [
    "OPENING_IMPULSE_SEQUENTIAL_BASIS",
    "OPENING_IMPULSE_SEQUENTIAL_RULE_VERSION",
    "compute_sequential_opening_impulse_profile",
    "unavailable_sequential_opening_impulse_profile",
]


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
    return moment.astimezone(_NEW_YORK)


def _checkpoint_label(checkpoint: Optional[int]) -> Optional[str]:
    if checkpoint is None:
        return None
    return f"09:{30 + checkpoint:02d}"


def _latest_completed_checkpoint(observed_at: datetime) -> int:
    elapsed = observed_at.hour * 60 + observed_at.minute - _OPEN_MINUTE_ET
    return max(0, min(_FINAL_CHECKPOINT, elapsed))


def _prior_xnys_sessions(market_date_et: str) -> Optional[list[str]]:
    """Return the exact preceding 20 XNYS session labels, oldest first."""

    try:
        target = date.fromisoformat(market_date_et)
        calendar = xcals.get_calendar("XNYS")
        session = calendar.date_to_session(target, direction="none")
        labels: list[str] = []
        for _ in range(OPENING_REFERENCE_SESSIONS):
            session = calendar.previous_session(session)
            labels.append(session.date().isoformat())
    except Exception:
        return None
    labels.reverse()
    return labels


def _minute_rows_by_session(
    bars: Sequence[Mapping[str, Any]],
) -> dict[str, dict[int, list[dict[str, Any]]]]:
    """Index valid opening 1m rows; retain duplicates so they fail closed."""

    indexed: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for row in filter_regular_session_bars(bars):
        start = row["start_et"]
        offset = start.hour * 60 + start.minute - _OPEN_MINUTE_ET
        if not 0 <= offset < _FINAL_CHECKPOINT:
            continue
        indexed.setdefault(row["session_date_et"], {}).setdefault(
            offset, []
        ).append(row)
    return indexed


def _aggregate_exact(
    indexed: Mapping[str, Mapping[int, Sequence[Mapping[str, Any]]]],
    session_date: str,
    checkpoint: int,
) -> Optional[dict[str, Any]]:
    by_minute = indexed.get(session_date, {})
    selected: list[Mapping[str, Any]] = []
    for offset in range(checkpoint):
        matches = list(by_minute.get(offset, ()))
        if len(matches) != 1 or not _valid_minute_bar(matches[0]):
            return None
        selected.append(matches[0])
    volume = sum(float(row["volume"]) for row in selected)
    if volume <= 0:
        return None
    first = selected[0]
    last = selected[-1]
    return {
        "date": datetime.fromisoformat(f"{session_date}T09:30:00")
        .replace(tzinfo=_NEW_YORK)
        .isoformat(),
        "open": float(first["open"]),
        "high": max(float(row["high"]) for row in selected),
        "low": min(float(row["low"]) for row in selected),
        "close": float(last["close"]),
        "volume": volume,
    }


def _valid_minute_bar(row: Mapping[str, Any]) -> bool:
    """Reject one malformed source minute before cumulative aggregation.

    A later valid minute must not conceal an earlier impossible high/low or
    non-finite value inside an otherwise plausible cumulative OHLC envelope.
    Flat ``high == low`` prints remain valid.
    """

    try:
        open_ = float(row.get("open"))
        high = float(row.get("high"))
        low = float(row.get("low"))
        close = float(row.get("close"))
        volume = float(row.get("volume"))
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) for value in (open_, high, low, close, volume))
        and min(open_, high, low, close) > 0
        and volume > 0
        and high >= low
        and high >= max(open_, close)
        and low <= min(open_, close)
    )


def _checkpoint_summary(
    checkpoint: int,
    *,
    profile: Optional[Mapping[str, Any]] = None,
    state: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    raw_state = str(profile.get("state")) if profile is not None else state
    passed = bool(profile and profile.get("alert_qualified"))
    try:
        reference_count = int(profile.get("reference_session_count") or 0) if profile else 0
    except (TypeError, ValueError):
        reference_count = 0
    return {
        "checkpoint": checkpoint,
        "checkpoint_label": _checkpoint_label(checkpoint),
        "state": raw_state,
        "passed": passed,
        "direction": profile.get("direction") if profile is not None else None,
        "reference_session_count": reference_count,
        "failed_checks": (
            list(profile.get("failed_checks") or ()) if profile is not None else []
        ),
        "unavailable_reason": (
            profile.get("unavailable_reason") if profile is not None else reason
        ),
    }


def _enrich(
    profile: Mapping[str, Any],
    *,
    checkpoint: Optional[int],
    completed_checkpoints: Sequence[Mapping[str, Any]],
    confirmation_points: Sequence[int] = (),
    confirmation_mode: Optional[str] = None,
    current_checkpoint_pass: Optional[bool] = None,
    retryable_pending: bool = False,
) -> dict[str, Any]:
    result = dict(profile)
    result.update(
        rule_version=OPENING_IMPULSE_SEQUENTIAL_RULE_VERSION,
        basis=OPENING_IMPULSE_SEQUENTIAL_BASIS,
        limitations=list(OPENING_IMPULSE_SEQUENTIAL_LIMITATIONS),
        checkpoint=checkpoint,
        checkpoint_label=_checkpoint_label(checkpoint),
        completed_checkpoints=[dict(item) for item in completed_checkpoints],
        confirmation_points=list(confirmation_points),
        confirmation_labels=[
            _checkpoint_label(item) for item in confirmation_points
        ],
        confirmation_mode=confirmation_mode,
        current_checkpoint_pass=current_checkpoint_pass,
        retryable_pending=bool(retryable_pending),
    )
    if checkpoint is not None:
        result["bar_end_et"] = _checkpoint_label(checkpoint)
    return result


def unavailable_sequential_opening_impulse_profile(
    reason: str,
    *,
    state: str = "unavailable",
    reference_session_count: int = 0,
    checkpoint: Optional[int] = None,
    completed_checkpoints: Sequence[Mapping[str, Any]] = (),
    current_checkpoint_pass: Optional[bool] = None,
    retryable_pending: bool = False,
) -> dict[str, Any]:
    """Return a v1-compatible, explicitly unavailable sequential profile."""

    profile = unavailable_opening_impulse_profile(
        reason,
        state=state,
        reference_session_count=reference_session_count,
    )
    return _enrich(
        profile,
        checkpoint=checkpoint,
        completed_checkpoints=completed_checkpoints,
        current_checkpoint_pass=current_checkpoint_pass,
        retryable_pending=retryable_pending,
    )


def _early_profile(
    current: Mapping[str, Any],
    references: Sequence[Mapping[str, Any]],
    *,
    market_date_et: str,
    observed_at: datetime,
    checkpoint: int,
    atr14: Any,
    prior_close: Any,
) -> dict[str, Any]:
    """Reuse v1's strict calculation after exact cumulative rows are selected."""

    internal_completed_at = datetime.fromisoformat(
        f"{market_date_et}T09:35:00"
    ).replace(tzinfo=_NEW_YORK)
    profile = compute_opening_impulse_profile(
        [current],
        reference_bars=references,
        market_date_et=market_date_et,
        quote_session_scope="current_session",
        as_of=internal_completed_at,
        atr14=atr14,
        prior_close=prior_close,
    )
    profile["completed_as_of"] = observed_at.isoformat()
    profile["bar_end_et"] = _checkpoint_label(checkpoint)
    return profile


def compute_sequential_opening_impulse_profile(
    current_1m_bars: Sequence[Mapping[str, Any]],
    *,
    reference_1m_bars: Sequence[Mapping[str, Any]],
    market_date_et: str,
    quote_session_scope: str,
    as_of: Any,
    atr14: Any,
    prior_close: Any = None,
    native_5m_profile: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Compute the sequential opening profile without fetching or retaining data.

    ``retryable_pending`` distinguishes provider settlement lag from normal
    sequential waiting.  It is true only when a bar that should already be
    complete is absent/invalid.  A single early strict pass waiting for its
    next checkpoint is pending but not retryable within the same minute.

    ``native_5m_profile`` remains an ignored compatibility argument so older
    callers do not break during rollout.  It can never qualify or veto v2.
    """

    if quote_session_scope != "current_session":
        return unavailable_sequential_opening_impulse_profile("not_current_session")
    observed_at = _aware_et(as_of)
    if observed_at is None:
        return unavailable_sequential_opening_impulse_profile(
            "as_of_unparseable_or_naive"
        )
    if observed_at.date().isoformat() != market_date_et:
        return unavailable_sequential_opening_impulse_profile(
            "as_of_market_date_mismatch"
        )

    latest = _latest_completed_checkpoint(observed_at)
    if latest == 0:
        result = unavailable_sequential_opening_impulse_profile(
            "opening_checkpoint_not_completed", state="pending"
        )
        result["completed_as_of"] = observed_at.isoformat()
        return result

    _ = native_5m_profile

    expected_reference_dates = _prior_xnys_sessions(market_date_et)
    if expected_reference_dates is None:
        return unavailable_sequential_opening_impulse_profile(
            "exact_prior_20_xnys_sessions_unavailable",
            checkpoint=min(latest, _FINAL_CHECKPOINT),
        )

    current_index = _minute_rows_by_session(current_1m_bars)
    reference_index = _minute_rows_by_session(reference_1m_bars)
    completed: list[dict[str, Any]] = []
    previous_profile: Optional[dict[str, Any]] = None

    checkpoints = (
        (_FINAL_CHECKPOINT,)
        if latest >= _FINAL_CHECKPOINT
        else _EARLY_CHECKPOINTS[:latest]
    )
    for checkpoint in checkpoints:
        current = _aggregate_exact(current_index, market_date_et, checkpoint)
        if current is None:
            completed.append(
                _checkpoint_summary(
                    checkpoint,
                    state="pending",
                    reason="completed_1m_checkpoint_not_available",
                )
            )
            result = unavailable_sequential_opening_impulse_profile(
                "completed_1m_checkpoint_not_available",
                state="pending",
                checkpoint=checkpoint,
                completed_checkpoints=completed,
                retryable_pending=True,
            )
            result["completed_as_of"] = observed_at.isoformat()
            return result

        references: list[dict[str, Any]] = []
        for session_date in expected_reference_dates:
            reference = _aggregate_exact(
                reference_index, session_date, checkpoint
            )
            if reference is not None:
                references.append(reference)
        if len(references) != OPENING_REFERENCE_SESSIONS:
            completed.append(
                _checkpoint_summary(
                    checkpoint,
                    state="unavailable",
                    reason="exact_prior_20_checkpoint_history_incomplete",
                )
            )
            result = unavailable_sequential_opening_impulse_profile(
                "exact_prior_20_checkpoint_history_incomplete",
                reference_session_count=len(references),
                checkpoint=checkpoint,
                completed_checkpoints=completed,
            )
            result["completed_as_of"] = observed_at.isoformat()
            return result

        current_profile = _early_profile(
            current,
            references,
            market_date_et=market_date_et,
            observed_at=observed_at,
            checkpoint=checkpoint,
            atr14=atr14,
            prior_close=prior_close,
        )
        completed.append(_checkpoint_summary(checkpoint, profile=current_profile))
        current_pass = bool(current_profile.get("alert_qualified"))
        if checkpoint == _FINAL_CHECKPOINT:
            current_profile["state"] = (
                "qualified" if current_pass else "not_qualified"
            )
            current_profile["alert_qualified"] = current_pass
            current_profile["unavailable_reason"] = None
            return _enrich(
                current_profile,
                checkpoint=checkpoint,
                completed_checkpoints=completed,
                confirmation_points=(checkpoint,) if current_pass else (),
                confirmation_mode=(
                    "completed_1m_cumulative" if current_pass else None
                ),
                current_checkpoint_pass=current_pass,
                retryable_pending=False,
            )
        previous_pass = bool(previous_profile and previous_profile.get("alert_qualified"))
        direction = current_profile.get("direction")
        if (
            checkpoint >= 2
            and checkpoint == latest
            and current_pass
            and previous_pass
            and direction in {"up", "down"}
            and previous_profile is not None
            and previous_profile.get("direction") == direction
        ):
            current_profile["state"] = "qualified"
            current_profile["alert_qualified"] = True
            current_profile["unavailable_reason"] = None
            return _enrich(
                current_profile,
                checkpoint=checkpoint,
                completed_checkpoints=completed,
                confirmation_points=(checkpoint - 1, checkpoint),
                confirmation_mode="consecutive_1m",
                current_checkpoint_pass=True,
            )
        previous_profile = current_profile

    if previous_profile is None:
        # Only reachable after an early fail-closed branch added above.
        return unavailable_sequential_opening_impulse_profile(
            "completed_1m_checkpoint_not_available",
            state="pending",
            checkpoint=latest,
            completed_checkpoints=completed,
            retryable_pending=True,
        )

    current_pass = bool(previous_profile.get("alert_qualified"))
    if current_pass:
        previous_profile["state"] = "pending"
        previous_profile["alert_qualified"] = False
        previous_profile["unavailable_reason"] = "awaiting_consecutive_confirmation"
        return _enrich(
            previous_profile,
            checkpoint=latest,
            completed_checkpoints=completed,
            confirmation_points=(latest,),
            current_checkpoint_pass=True,
            retryable_pending=False,
        )
    return _enrich(
        previous_profile,
        checkpoint=latest,
        completed_checkpoints=completed,
        current_checkpoint_pass=False,
        retryable_pending=False,
    )
