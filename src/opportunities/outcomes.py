# -*- coding: utf-8 -*-
"""Pure, no-lookahead outcome calculations for frozen opportunity candidates.

The module deliberately evaluates the underlying price path only.  It does
not infer an option-trade fill, option P&L, execution decision, or causal
attribution.  All calendar resolution uses XNYS sessions and fails closed.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence


SCHEMA_VERSION = "opportunity-outcomes/1.1"
FORMULA_VERSION = "xnys-frozen-close-next-open-path/v2"
HORIZON_THRESHOLDS_PCT = {5: 0.5, 20: 1.0}

_DIRECTIONS = {"LONG", "SHORT", "MIXED", "UNKNOWN"}
_DIRECTION_SIGN = {"LONG": 1.0, "SHORT": -1.0}

STATE_INELIGIBLE = "INELIGIBLE"
STATE_PENDING = "PENDING"
STATE_DATA_GAP = "DATA_GAP"
STATE_PARTIAL = "PARTIAL"
STATE_COMPLETE = "COMPLETE"

LABEL_CONTEXT_HIT = "CONTEXT_HIT"
LABEL_CONTEXT_MISS = "CONTEXT_MISS"
LABEL_NEUTRAL = "NEUTRAL"
LABEL_NON_DIRECTIONAL = "NON_DIRECTIONAL"
LABEL_DIRECTION_UNKNOWN = "DIRECTION_UNKNOWN"


class OutcomeCalendarError(RuntimeError):
    """Raised when the XNYS calendar cannot provide an exact session answer."""


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _session_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            converted = value.to_pydatetime()
            if isinstance(converted, datetime):
                return converted.date()
        except Exception:  # noqa: BLE001 - invalid injected value
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _require_session_date(value: Any, *, field: str) -> date:
    result = _session_date(value)
    if result is None:
        raise ValueError(f"{field} must be an ISO date or date value")
    return result


def _calendar_datetime(value: Any, *, field: str) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise OutcomeCalendarError(f"XNYS {field} did not return a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise OutcomeCalendarError(f"XNYS {field} returned a naive datetime")
    return value.astimezone(timezone.utc)


def _default_xnys_calendar():
    try:
        import exchange_calendars as xcals

        return xcals.get_calendar("XNYS")
    except Exception as exc:  # noqa: BLE001 - calendar is a hard requirement
        raise OutcomeCalendarError("XNYS calendar is unavailable") from exc


def _is_session(calendar: Any, session: date) -> bool:
    try:
        return bool(calendar.is_session(session))
    except Exception as exc:  # noqa: BLE001 - fail closed on calendar errors
        raise OutcomeCalendarError(
            f"XNYS could not validate session {session.isoformat()}"
        ) from exc


def _next_session(calendar: Any, session: date) -> date:
    try:
        value = calendar.next_session(session)
    except Exception as exc:  # noqa: BLE001 - fail closed on calendar errors
        raise OutcomeCalendarError(
            f"XNYS could not resolve the session after {session.isoformat()}"
        ) from exc
    result = _session_date(value)
    if result is None:
        raise OutcomeCalendarError("XNYS next_session returned an invalid value")
    return result


def _session_open(calendar: Any, session: date) -> datetime:
    try:
        value = calendar.session_open(session)
    except Exception as exc:  # noqa: BLE001 - fail closed on calendar errors
        raise OutcomeCalendarError(
            f"XNYS could not resolve the open for {session.isoformat()}"
        ) from exc
    return _calendar_datetime(value, field="session_open")


def _session_close(calendar: Any, session: date) -> datetime:
    try:
        value = calendar.session_close(session)
    except Exception as exc:  # noqa: BLE001 - fail closed on calendar errors
        raise OutcomeCalendarError(
            f"XNYS could not resolve the close for {session.isoformat()}"
        ) from exc
    return _calendar_datetime(value, field="session_close")


def _finite_positive(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def _index_bars(
    bars: Optional[Sequence[Mapping[str, Any]]],
) -> tuple[dict[date, Mapping[str, Any]], set[date]]:
    indexed: dict[date, Mapping[str, Any]] = {}
    duplicates: set[date] = set()
    for raw in bars or ():
        if not isinstance(raw, Mapping):
            continue
        session = _session_date(raw.get("session", raw.get("date")))
        if session is None:
            continue
        if session in indexed:
            duplicates.add(session)
        indexed[session] = raw
    return indexed, duplicates


def _price(
    bars: Mapping[date, Mapping[str, Any]],
    duplicates: set[date],
    session: date,
    field: str,
) -> Optional[float]:
    if session in duplicates:
        return None
    row = bars.get(session)
    if row is None:
        return None
    return _finite_positive(row.get(field))


def _pct_change(end: float, start: float) -> float:
    return ((end / start) - 1.0) * 100.0


def _rounded(value: Optional[float]) -> Optional[float]:
    return round(value, 8) if value is not None else None


def _prices_match(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-8)


def _append_gap(gaps: list[str], value: str) -> None:
    if value not in gaps:
        gaps.append(value)


def _empty_horizon(
    *,
    horizon: int,
    reference_close: float,
    spy_reference_close: Optional[float],
    entry_session: date,
    target_session: date,
    target_close_at: datetime,
    state: str,
) -> dict[str, Any]:
    return {
        "horizon_sessions": horizon,
        "reference_close": _rounded(reference_close),
        "refetched_reference_close": None,
        "entry_session": entry_session.isoformat(),
        "entry_open": None,
        "target_session": target_session.isoformat(),
        "target_close_at": target_close_at.isoformat(),
        "end_close": None,
        "max_high": None,
        "min_low": None,
        "spy_reference_close": _rounded(spy_reference_close),
        "spy_refetched_reference_close": None,
        "spy_entry_open": None,
        "spy_end_close": None,
        "threshold_pct": HORIZON_THRESHOLDS_PCT[horizon],
        "state": state,
        "label": None,
        "raw_close_return_pct": None,
        "signed_close_return_pct": None,
        "raw_entry_return_pct": None,
        "signed_entry_return_pct": None,
        "mfe_pct": None,
        "mae_pct": None,
        "spy_close_return_pct": None,
        "raw_excess_spy_pct": None,
        "signed_excess_spy_pct": None,
        "data_gaps": [],
    }


def _direction_label(direction: str, signed_return: Optional[float], threshold: float) -> Optional[str]:
    if direction == "MIXED":
        return LABEL_NON_DIRECTIONAL
    if direction == "UNKNOWN":
        return LABEL_DIRECTION_UNKNOWN
    if signed_return is None:
        return None
    epsilon = 1e-12
    if signed_return >= threshold - epsilon:
        return LABEL_CONTEXT_HIT
    if signed_return <= -threshold + epsilon:
        return LABEL_CONTEXT_MISS
    return LABEL_NEUTRAL


def _path_metrics(
    *,
    direction: str,
    entry_price: Optional[float],
    sessions: Sequence[date],
    stock_bars: Mapping[date, Mapping[str, Any]],
    stock_duplicates: set[date],
    gaps: list[str],
) -> tuple[
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
]:
    highs: list[float] = []
    lows: list[float] = []
    path_valid = True
    for session in sessions:
        if session not in stock_bars or session in stock_duplicates:
            _append_gap(gaps, f"stock_session:{session.isoformat()}")
            path_valid = False
            continue
        high = _price(stock_bars, stock_duplicates, session, "high")
        low = _price(stock_bars, stock_duplicates, session, "low")
        if high is None or low is None or high < low:
            _append_gap(gaps, f"stock_ohlc:{session.isoformat()}")
            path_valid = False
            continue
        highs.append(high)
        lows.append(low)

    if (
        not path_valid
        or len(highs) != len(sessions)
    ):
        return None, None, None, None

    max_high = max(highs)
    min_low = min(lows)
    if direction not in _DIRECTION_SIGN or entry_price is None:
        return max_high, min_low, None, None

    if direction == "LONG":
        mfe = max(0.0, _pct_change(max_high, entry_price))
        mae = min(0.0, _pct_change(min_low, entry_price))
    else:
        mfe = max(0.0, -_pct_change(min_low, entry_price))
        mae = min(0.0, -_pct_change(max_high, entry_price))
    return max_high, min_low, mfe, mae


def _strict_spy_metrics(
    *,
    expected_sessions: Sequence[date],
    signal_session: date,
    entry_session: date,
    target_session: date,
    spy_reference_close: Optional[float],
    stock_bars: Mapping[date, Mapping[str, Any]],
    stock_duplicates: set[date],
    spy_bars: Mapping[date, Mapping[str, Any]],
    spy_duplicates: set[date],
    gaps: list[str],
) -> dict[str, Optional[float]]:
    aligned = True
    for session in expected_sessions:
        if session not in stock_bars or session in stock_duplicates:
            _append_gap(gaps, f"relative_stock_session:{session.isoformat()}")
            aligned = False
        if session not in spy_bars or session in spy_duplicates:
            _append_gap(gaps, f"spy_session:{session.isoformat()}")
            aligned = False
    spy_refetched_start = _price(
        spy_bars,
        spy_duplicates,
        signal_session,
        "close",
    )
    spy_entry = _price(spy_bars, spy_duplicates, entry_session, "open")
    spy_end = _price(spy_bars, spy_duplicates, target_session, "close")

    if spy_reference_close is None:
        _append_gap(gaps, "spy_reference_close")
    if spy_refetched_start is None:
        _append_gap(gaps, "spy_reference_close_refetch_missing")
    elif (
        spy_reference_close is not None
        and not _prices_match(spy_reference_close, spy_refetched_start)
    ):
        _append_gap(gaps, "spy_reference_revision_mismatch")
    if spy_entry is None:
        _append_gap(gaps, "spy_entry_open")
    if spy_end is None:
        _append_gap(gaps, "spy_end_close")

    close_return = None
    if aligned and spy_reference_close is not None and spy_end is not None:
        close_return = _pct_change(spy_end, spy_reference_close)
    return {
        "refetched_reference_close": spy_refetched_start,
        "entry_open": spy_entry,
        "end_close": spy_end,
        "close_return": close_return,
    }


def _evaluate_horizon(
    *,
    horizon: int,
    target_session: date,
    target_close_at: datetime,
    path_sessions: Sequence[date],
    signal_session: date,
    entry_session: date,
    reference_close: float,
    spy_reference_close: Optional[float],
    direction: str,
    eligible: bool,
    evaluated_at: datetime,
    stock_bars: Mapping[date, Mapping[str, Any]],
    stock_duplicates: set[date],
    spy_bars: Mapping[date, Mapping[str, Any]],
    spy_duplicates: set[date],
) -> dict[str, Any]:
    if not eligible:
        return _empty_horizon(
            horizon=horizon,
            reference_close=reference_close,
            spy_reference_close=spy_reference_close,
            entry_session=entry_session,
            target_session=target_session,
            target_close_at=target_close_at,
            state=STATE_INELIGIBLE,
        )
    if evaluated_at < target_close_at:
        return _empty_horizon(
            horizon=horizon,
            reference_close=reference_close,
            spy_reference_close=spy_reference_close,
            entry_session=entry_session,
            target_session=target_session,
            target_close_at=target_close_at,
            state=STATE_PENDING,
        )

    result = _empty_horizon(
        horizon=horizon,
        reference_close=reference_close,
        spy_reference_close=spy_reference_close,
        entry_session=entry_session,
        target_session=target_session,
        target_close_at=target_close_at,
        state=STATE_COMPLETE,
    )
    gaps: list[str] = result["data_gaps"]

    refetched_reference_close = _price(
        stock_bars,
        stock_duplicates,
        signal_session,
        "close",
    )
    end_close = _price(
        stock_bars,
        stock_duplicates,
        target_session,
        "close",
    )
    result["refetched_reference_close"] = _rounded(
        refetched_reference_close
    )
    result["end_close"] = _rounded(end_close)
    if refetched_reference_close is None:
        _append_gap(gaps, "reference_close_refetch_missing")
    elif not _prices_match(reference_close, refetched_reference_close):
        _append_gap(gaps, "reference_revision_mismatch")
    if end_close is None:
        _append_gap(gaps, "stock_target_close")

    entry_open = _price(
        stock_bars,
        stock_duplicates,
        entry_session,
        "open",
    )
    result["entry_open"] = _rounded(entry_open)
    if entry_open is None:
        _append_gap(gaps, "stock_entry_open")

    max_high, min_low, mfe, mae = _path_metrics(
        direction=direction,
        entry_price=entry_open,
        sessions=path_sessions,
        stock_bars=stock_bars,
        stock_duplicates=stock_duplicates,
        gaps=gaps,
    )
    result["max_high"] = _rounded(max_high)
    result["min_low"] = _rounded(min_low)
    result["mfe_pct"] = _rounded(mfe)
    result["mae_pct"] = _rounded(mae)

    expected_sessions = [signal_session, *path_sessions]
    spy_metrics = _strict_spy_metrics(
        expected_sessions=expected_sessions,
        signal_session=signal_session,
        entry_session=entry_session,
        target_session=target_session,
        spy_reference_close=spy_reference_close,
        stock_bars=stock_bars,
        stock_duplicates=stock_duplicates,
        spy_bars=spy_bars,
        spy_duplicates=spy_duplicates,
        gaps=gaps,
    )
    result["spy_refetched_reference_close"] = _rounded(
        spy_metrics["refetched_reference_close"]
    )
    result["spy_entry_open"] = _rounded(spy_metrics["entry_open"])
    result["spy_end_close"] = _rounded(spy_metrics["end_close"])

    if end_close is None:
        result["state"] = STATE_DATA_GAP
        return result

    raw_close_return = _pct_change(end_close, reference_close)
    sign = _DIRECTION_SIGN.get(direction)
    signed_close_return = sign * raw_close_return if sign is not None else None
    result["raw_close_return_pct"] = _rounded(raw_close_return)
    result["signed_close_return_pct"] = _rounded(signed_close_return)
    result["label"] = _direction_label(
        direction,
        signed_close_return,
        HORIZON_THRESHOLDS_PCT[horizon],
    )

    raw_entry_return = (
        _pct_change(end_close, entry_open)
        if entry_open is not None
        else None
    )
    signed_entry_return = (
        sign * raw_entry_return
        if sign is not None and raw_entry_return is not None
        else None
    )
    result["raw_entry_return_pct"] = _rounded(raw_entry_return)
    result["signed_entry_return_pct"] = _rounded(signed_entry_return)

    spy_close_return = spy_metrics["close_return"]
    raw_excess = (
        raw_close_return - spy_close_return
        if spy_close_return is not None
        else None
    )
    signed_excess = (
        sign * raw_excess
        if sign is not None and raw_excess is not None
        else None
    )
    result["spy_close_return_pct"] = _rounded(spy_close_return)
    result["raw_excess_spy_pct"] = _rounded(raw_excess)
    result["signed_excess_spy_pct"] = _rounded(signed_excess)

    if gaps:
        result["state"] = STATE_PARTIAL
    return result


def evaluate_opportunity_outcomes(
    *,
    signal_session: date | str,
    generated_at: datetime,
    reference_close: float,
    direction: str,
    stock_bars: Sequence[Mapping[str, Any]],
    spy_bars: Optional[Sequence[Mapping[str, Any]]] = None,
    spy_reference_close: Optional[float] = None,
    calendar: Any = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Evaluate deterministic 5/20-session outcomes for one frozen candidate.

    ``signal_session`` and ``reference_close`` are frozen signal-time facts.
    Returns always use that frozen close as their denominator; a refetched
    signal-session close is only an audit check and never silently replaces it.
    The entry proxy is the next XNYS regular-session open.  The candidate is
    sample eligible only when frozen after the signal close and strictly before
    that next open.  Missing sessions are never replaced by a later bar.

    ``calendar`` and ``now`` are injectable for deterministic tests.  Without
    an injected calendar, the function requires ``exchange_calendars`` and the
    XNYS calendar; calendar failures raise :class:`OutcomeCalendarError`.
    """

    normalized_direction = str(direction or "").strip().upper()
    if normalized_direction not in _DIRECTIONS:
        raise ValueError(
            "direction must be LONG, SHORT, MIXED, or UNKNOWN"
        )

    frozen_reference_close = _finite_positive(reference_close)
    if frozen_reference_close is None:
        raise ValueError("reference_close must be a finite positive number")
    frozen_spy_reference_close = None
    if spy_reference_close is not None:
        frozen_spy_reference_close = _finite_positive(spy_reference_close)
        if frozen_spy_reference_close is None:
            raise ValueError(
                "spy_reference_close must be a finite positive number"
            )

    signal = _require_session_date(signal_session, field="signal_session")
    generated_utc = _aware_utc(generated_at, field="generated_at")
    evaluated_utc = _aware_utc(
        now if now is not None else datetime.now(timezone.utc),
        field="now",
    )
    if evaluated_utc < generated_utc:
        raise ValueError("now must be at or after generated_at")
    xnys = calendar if calendar is not None else _default_xnys_calendar()

    if not _is_session(xnys, signal):
        raise ValueError(f"signal_session is not an XNYS session: {signal}")

    signal_close_at = _session_close(xnys, signal)
    entry_session = _next_session(xnys, signal)
    entry_open_at = _session_open(xnys, entry_session)

    path_20: list[date] = []
    current = signal
    for _ in range(20):
        current = _next_session(xnys, current)
        path_20.append(current)
    targets = {5: path_20[4], 20: path_20[19]}

    if generated_utc < signal_close_at:
        eligible = False
        ineligibility_reason = "signal_session_not_complete_at_freeze"
    elif generated_utc >= entry_open_at:
        eligible = False
        ineligibility_reason = "not_frozen_before_entry_open"
    else:
        eligible = True
        ineligibility_reason = None

    stock_index, stock_duplicates = _index_bars(stock_bars)
    spy_index, spy_duplicates = _index_bars(spy_bars)

    horizons: dict[str, dict[str, Any]] = {}
    for horizon in (5, 20):
        target = targets[horizon]
        horizons[f"{horizon}d"] = _evaluate_horizon(
            horizon=horizon,
            target_session=target,
            target_close_at=_session_close(xnys, target),
            path_sessions=path_20[:horizon],
            signal_session=signal,
            entry_session=entry_session,
            reference_close=frozen_reference_close,
            spy_reference_close=frozen_spy_reference_close,
            direction=normalized_direction,
            eligible=eligible,
            evaluated_at=evaluated_utc,
            stock_bars=stock_index,
            stock_duplicates=stock_duplicates,
            spy_bars=spy_index,
            spy_duplicates=spy_duplicates,
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "formula_version": FORMULA_VERSION,
        "generated_at": generated_utc.isoformat(),
        "evaluated_at": evaluated_utc.isoformat(),
        "direction": normalized_direction,
        "reference_close": _rounded(frozen_reference_close),
        "spy_reference_close": _rounded(frozen_spy_reference_close),
        "eligible": eligible,
        "ineligibility_reason": ineligibility_reason,
        "schedule": {
            "signal_session": signal.isoformat(),
            "signal_close_at": signal_close_at.isoformat(),
            "entry_session": entry_session.isoformat(),
            "entry_open_at": entry_open_at.isoformat(),
            "x5_session": targets[5].isoformat(),
            "x20_session": targets[20].isoformat(),
        },
        "horizons": horizons,
    }
