# -*- coding: utf-8 -*-
"""Pure contracts for one canonical XNYS premarket research cycle.

This module owns no network clients and performs no persistence.  It resolves
the exact XNYS session/window and validates that Regime and completed-daily-bar
inputs were knowable for the same research cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from src.regime.quality import assess_regime_snapshot

__all__ = [
    "CANONICAL_CYCLE_VERSION",
    "CANONICAL_FREEZE_POLICY_VERSION",
    "CANONICAL_SCOPE_KEY",
    "CanonicalPremarketWindow",
    "PremarketCalendarError",
    "PremarketQualityAssessment",
    "assess_canonical_inputs",
    "assess_regime_for_cycle",
    "resolve_canonical_premarket_window",
]


CANONICAL_CYCLE_VERSION = "canonical_premarket_cycle_v1"
CANONICAL_FREEZE_POLICY_VERSION = "canonical_premarket_xnys_v2"
CANONICAL_SCOPE_KEY = "canonical_premarket_research"
_NEW_YORK = ZoneInfo("America/New_York")
_WINDOW_FROM_OPEN = timedelta(minutes=45)
_WINDOW_UNTIL_OPEN = timedelta(minutes=10)
_REGIME_DOMAINS = ("spy", "vix", "events", "sectors", "prev_day", "premarket")
_CORE_REGIME_DOMAINS = ("spy", "vix")


class PremarketCalendarError(RuntimeError):
    """Raised when XNYS cannot resolve an exact session/window."""


@dataclass(frozen=True)
class CanonicalPremarketWindow:
    """Resolved window for the current New York calendar date."""

    market_date_et: date
    is_xnys_session: bool
    state: str
    previous_session: Optional[date] = None
    regular_open_at: Optional[datetime] = None
    window_start_at: Optional[datetime] = None
    window_end_at: Optional[datetime] = None


@dataclass(frozen=True)
class PremarketQualityAssessment:
    """A deterministic publish gate, separate from outcome eligibility."""

    state: str
    reasons: tuple[str, ...]
    regime_state: str
    regime_domain_states: Mapping[str, str]
    fresh_candidate_count: int
    unavailable_candidate_count: int


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _session_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    converted = getattr(value, "date", None)
    if callable(converted):
        result = converted()
        if isinstance(result, date):
            return result
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise PremarketCalendarError("XNYS returned an invalid session label") from exc


def _calendar_timestamp(value: Any, *, field: str) -> datetime:
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(converted, datetime):
        raise PremarketCalendarError(f"XNYS {field} did not return a datetime")
    if converted.tzinfo is None or converted.utcoffset() is None:
        raise PremarketCalendarError(f"XNYS {field} returned a naive datetime")
    return converted.astimezone(timezone.utc)


def _default_xnys_calendar():
    try:
        import exchange_calendars as xcals

        return xcals.get_calendar("XNYS")
    except Exception as exc:  # noqa: BLE001 - calendar failures must fail closed
        raise PremarketCalendarError("XNYS calendar is unavailable") from exc


def resolve_canonical_premarket_window(
    now: datetime,
    *,
    calendar: Any = None,
) -> CanonicalPremarketWindow:
    """Resolve ``[regular open - 45m, regular open - 10m)`` for today ET."""

    now_utc = _aware_utc(now, field="now")
    market_date = now_utc.astimezone(_NEW_YORK).date()
    xnys = calendar if calendar is not None else _default_xnys_calendar()
    try:
        is_session = bool(xnys.is_session(market_date))
    except Exception as exc:  # noqa: BLE001
        raise PremarketCalendarError(
            f"XNYS could not validate session {market_date.isoformat()}"
        ) from exc
    if not is_session:
        return CanonicalPremarketWindow(
            market_date_et=market_date,
            is_xnys_session=False,
            state="non_session",
        )

    try:
        previous_session = _session_date(xnys.previous_session(market_date))
        regular_open = _calendar_timestamp(
            xnys.session_open(market_date),
            field="session_open",
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, PremarketCalendarError):
            raise
        raise PremarketCalendarError(
            f"XNYS could not resolve session {market_date.isoformat()}"
        ) from exc

    window_start = regular_open - _WINDOW_FROM_OPEN
    window_end = regular_open - _WINDOW_UNTIL_OPEN
    if now_utc < window_start:
        state = "before_window"
    elif now_utc < window_end:
        state = "in_window"
    else:
        state = "after_window"
    return CanonicalPremarketWindow(
        market_date_et=market_date,
        is_xnys_session=True,
        state=state,
        previous_session=previous_session,
        regular_open_at=regular_open,
        window_start_at=window_start,
        window_end_at=window_end,
    )


def _coerce_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def assess_regime_for_cycle(
    regime: Optional[Mapping[str, Any]],
    *,
    market_date_et: date,
    cycle_as_of: datetime,
) -> tuple[str, tuple[str, ...], Mapping[str, str]]:
    """Validate same-day, causally available Regime with ready SPY and VIX."""

    reasons: list[str] = []
    if not isinstance(regime, Mapping):
        return (
            "blocked",
            ("regime_missing",),
            {domain: "unavailable" for domain in _REGIME_DOMAINS},
        )
    if _coerce_date(regime.get("date")) != market_date_et:
        reasons.append("regime_market_date_mismatch")

    generated_at = regime.get("generated_at")
    if not isinstance(generated_at, datetime):
        reasons.append("regime_generated_at_missing")
    elif generated_at.tzinfo is None or generated_at.utcoffset() is None:
        reasons.append("regime_generated_at_naive")
    elif generated_at.astimezone(timezone.utc) > _aware_utc(
        cycle_as_of,
        field="cycle_as_of",
    ):
        reasons.append("regime_generated_after_cycle_as_of")

    quality = assess_regime_snapshot(
        regime.get("snapshot") if isinstance(regime.get("snapshot"), Mapping) else {}
    )
    raw_states = quality.get("domain_states")
    domain_states = {
        domain: str((raw_states or {}).get(domain) or "unavailable")
        for domain in _REGIME_DOMAINS
    }
    for domain in _CORE_REGIME_DOMAINS:
        if domain_states[domain] != "ready":
            reasons.append(f"regime_core_{domain}_not_ready")

    if reasons:
        return "blocked", tuple(sorted(set(reasons))), domain_states
    if any(domain_states[domain] != "ready" for domain in _REGIME_DOMAINS):
        return "degraded", (), domain_states
    return "ready", (), domain_states


def assess_canonical_inputs(
    run_payload: Mapping[str, Any],
    regime: Optional[Mapping[str, Any]],
    *,
    window: CanonicalPremarketWindow,
    cycle_as_of: datetime,
) -> PremarketQualityAssessment:
    """Assess Regime causality and exact previous-XNYS-session daily freshness."""

    regime_state, regime_reasons, domain_states = assess_regime_for_cycle(
        regime,
        market_date_et=window.market_date_et,
        cycle_as_of=cycle_as_of,
    )
    reasons = list(regime_reasons)
    if _coerce_date(run_payload.get("market_date_et")) != window.market_date_et:
        reasons.append("run_market_date_mismatch")

    candidates: Sequence[Mapping[str, Any]] = tuple(
        item
        for item in (run_payload.get("candidates") or ())
        if isinstance(item, Mapping)
    )
    fresh = 0
    unavailable = 0
    for candidate in candidates:
        reference_session = _coerce_date(candidate.get("reference_session_date"))
        if str(candidate.get("research_state") or "") == "blocked":
            unavailable += 1
            continue
        if reference_session != window.previous_session:
            reasons.append(
                "candidate_reference_session_mismatch:"
                + str(candidate.get("ticker") or "unknown")
            )
            continue
        fresh += 1

    if not candidates:
        reasons.append("no_candidates")
    if fresh == 0:
        reasons.append("no_fresh_candidates")
    blocking_reasons = [
        reason
        for reason in reasons
        if reason != "candidate_unavailable"
    ]
    if blocking_reasons:
        state = "blocked"
    elif regime_state == "degraded" or unavailable:
        state = "degraded"
    else:
        state = "ready"

    return PremarketQualityAssessment(
        state=state,
        reasons=tuple(sorted(set(reasons))),
        regime_state=regime_state,
        regime_domain_states=domain_states,
        fresh_candidate_count=fresh,
        unavailable_candidate_count=unavailable,
    )
