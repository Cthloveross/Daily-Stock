# -*- coding: utf-8 -*-
"""Fail-closed, side-effect-free XNYS clock resolution.

The rest of the application has several wall-clock approximations for the US
market.  This module intentionally contains no scheduler or notification
policy: it only resolves the New York market date against the authoritative
``exchange_calendars`` XNYS schedule and returns immutable facts.

Any non-session date (including weekends) is reported as ``holiday``.  If the
calendar cannot answer exactly, the result is ``unavailable`` and no 09:30 or
16:00 fallback is manufactured.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_REGULAR_XNYS_CLOSE_MINUTE_ET = 16 * 60

XNYS_DAY_STATES = ("session", "holiday", "unavailable")
XNYS_PHASES = ("premarket", "regular", "afterhours", "holiday", "unavailable")


@dataclass(frozen=True)
class XNYSSessionClock:
    """Exact XNYS facts for the ET date containing ``now``.

    ``open_minute_et`` and ``close_minute_et`` are minutes after ET midnight
    (09:30 = 570, 13:00 = 780, 16:00 = 960).  They are ``None`` for holidays
    and unavailable calendar reads, preventing callers from silently treating
    an unknown day as a normal full session.
    """

    state: Literal["session", "holiday", "unavailable"]
    phase: Literal[
        "premarket", "regular", "afterhours", "holiday", "unavailable"
    ]
    market_date_et: date
    open_minute_et: Optional[int]
    close_minute_et: Optional[int]
    open_at_et: Optional[datetime]
    close_at_et: Optional[datetime]
    is_early_close: Optional[bool]
    unavailable_reason: Optional[str]


def _unavailable(market_date: date, reason: str) -> XNYSSessionClock:
    return XNYSSessionClock(
        state="unavailable",
        phase="unavailable",
        market_date_et=market_date,
        open_minute_et=None,
        close_minute_et=None,
        open_at_et=None,
        close_at_et=None,
        is_early_close=None,
        unavailable_reason=reason,
    )


def _default_xnys_calendar() -> Any:
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS")


def _aware_et(value: Any, *, field: str) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise TypeError(f"{field}_not_datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}_not_timezone_aware")
    return value.astimezone(_ET)


def _minute_of_day(value: datetime, *, field: str) -> int:
    if value.second != 0 or value.microsecond != 0:
        raise ValueError(f"{field}_not_minute_aligned")
    return value.hour * 60 + value.minute


def resolve_xnys_session_clock(
    now: datetime,
    *,
    calendar: Any = None,
) -> XNYSSessionClock:
    """Resolve the XNYS session/holiday and exact ET open/close for ``now``.

    The function never guesses a schedule.  Invalid input and every calendar
    import, lookup, or schedule-shape failure return an explicit
    ``state="unavailable"`` result.
    """

    if not isinstance(now, datetime):
        # There is no trustworthy ET date to attach to a non-datetime value.
        # ``date.min`` is an explicit sentinel, not a market-date guess.
        return _unavailable(date.min, "now_not_datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        return _unavailable(now.date(), "now_not_timezone_aware")

    observed_et = now.astimezone(_ET)
    market_date = observed_et.date()
    try:
        xnys = calendar if calendar is not None else _default_xnys_calendar()
    except Exception as exc:  # noqa: BLE001 - exact calendar is mandatory
        return _unavailable(
            market_date,
            f"xnys_calendar_unavailable:{type(exc).__name__}",
        )

    try:
        is_session = bool(xnys.is_session(market_date))
    except Exception as exc:  # noqa: BLE001 - do not guess session/holiday
        return _unavailable(
            market_date,
            f"xnys_session_lookup_failed:{type(exc).__name__}",
        )

    if not is_session:
        return XNYSSessionClock(
            state="holiday",
            phase="holiday",
            market_date_et=market_date,
            open_minute_et=None,
            close_minute_et=None,
            open_at_et=None,
            close_at_et=None,
            is_early_close=None,
            unavailable_reason=None,
        )

    try:
        open_at_et = _aware_et(
            xnys.session_open(market_date), field="session_open"
        )
        close_at_et = _aware_et(
            xnys.session_close(market_date), field="session_close"
        )
        if open_at_et.date() != market_date or close_at_et.date() != market_date:
            raise ValueError("session_schedule_date_mismatch")
        if close_at_et <= open_at_et:
            raise ValueError("session_close_not_after_open")
        open_minute = _minute_of_day(open_at_et, field="session_open")
        close_minute = _minute_of_day(close_at_et, field="session_close")
    except Exception as exc:  # noqa: BLE001 - malformed schedule is unavailable
        return _unavailable(
            market_date,
            f"xnys_schedule_unavailable:{type(exc).__name__}",
        )

    if observed_et < open_at_et:
        phase: Literal["premarket", "regular", "afterhours"] = "premarket"
    elif observed_et < close_at_et:
        phase = "regular"
    else:
        phase = "afterhours"

    return XNYSSessionClock(
        state="session",
        phase=phase,
        market_date_et=market_date,
        open_minute_et=open_minute,
        close_minute_et=close_minute,
        open_at_et=open_at_et,
        close_at_et=close_at_et,
        is_early_close=close_minute < _REGULAR_XNYS_CLOSE_MINUTE_ET,
        unavailable_reason=None,
    )


__all__ = [
    "XNYS_DAY_STATES",
    "XNYS_PHASES",
    "XNYSSessionClock",
    "resolve_xnys_session_clock",
]
