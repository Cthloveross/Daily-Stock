# -*- coding: utf-8 -*-
"""Regime snapshot quality assessment.

The numeric Regime score is only meaningful when its market inputs are
present.  Quality metadata lives inside ``snapshot_json`` so existing
``regime_scores`` tables do not need a destructive migration and downstream
readers (including the opportunity board) can fail closed.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

__all__ = [
    "CORE_REGIME_DOMAINS",
    "REGIME_DOMAINS",
    "assess_regime_snapshot",
]


REGIME_DOMAINS = ("spy", "vix", "events", "sectors", "prev_day", "premarket")
CORE_REGIME_DOMAINS = ("spy", "vix")
_DOMAIN_STATES = {"ready", "degraded", "unavailable"}


def _finite_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    return number > 0 if positive else True


def _explicit_state(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    state = str(payload.get("_status") or "").strip().lower()
    return state if state in _DOMAIN_STATES else None


def _numeric_domain_state(
    payload: Any,
    required: tuple[str, ...],
    *,
    positive: tuple[str, ...] = (),
) -> str:
    explicit = _explicit_state(payload)
    if explicit is not None:
        return explicit
    if not isinstance(payload, Mapping) or not payload:
        return "unavailable"
    valid = sum(
        1
        for key in required
        if _finite_number(payload.get(key), positive=key in positive)
    )
    if valid == len(required):
        return "ready"
    return "degraded" if valid else "unavailable"


def _events_state(payload: Any) -> str:
    explicit = _explicit_state(payload)
    if explicit is not None:
        return explicit
    if not isinstance(payload, Mapping) or not payload:
        return "unavailable"
    required = (
        "fomc_today",
        "cpi_today",
        "nfp_today",
        "earnings_count_watchlist",
        "tariff_headline_today",
    )
    if all(key in payload for key in required):
        return "ready"
    return "degraded"


def _sector_state(payload: Any) -> str:
    explicit = _explicit_state(payload)
    if explicit is not None:
        return explicit
    if not isinstance(payload, Mapping) or not payload:
        return "unavailable"
    if not _finite_number(payload.get("sectors_above_ma20")):
        return "unavailable"
    total = payload.get("total_sectors_seen")
    if _finite_number(total) and int(total) >= 11:
        return "ready"
    # Legacy snapshots did not always persist coverage.  Keep them readable,
    # but do not call their breadth score fully observed.
    return "degraded"


def _premarket_state(payload: Any) -> str:
    explicit = _explicit_state(payload)
    if explicit is not None:
        return explicit
    if not isinstance(payload, Mapping) or not payload:
        return "unavailable"
    required = ("spy_pre_pct", "watchlist_up_5pct", "watchlist_down_5pct")
    if not all(_finite_number(payload.get(key)) for key in required):
        return "degraded"
    # Older payloads used the same all-zero object both for a genuinely flat
    # premarket and for "Alpaca unavailable".  That ambiguity is irrecoverable,
    # so legacy premarket data is conservatively degraded.
    return "degraded"


def _infer_domain_states(snapshot: Mapping[str, Any]) -> dict[str, str]:
    return {
        "spy": _numeric_domain_state(
            snapshot.get("spy"),
            ("close", "ma20", "ma50", "pct_change_5d"),
            positive=("close", "ma20", "ma50"),
        ),
        "vix": _numeric_domain_state(
            snapshot.get("vix"),
            ("level", "pct_change_5d"),
            positive=("level",),
        ),
        "events": _events_state(snapshot.get("events")),
        "sectors": _sector_state(snapshot.get("sectors")),
        "prev_day": _numeric_domain_state(
            snapshot.get("prev_day"),
            ("close_vs_high_pct", "prev_day_range_pct"),
        ),
        "premarket": _premarket_state(snapshot.get("premarket")),
    }


def assess_regime_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return normalized, conservative quality metadata for a snapshot.

    ``unavailable`` means at least one core market input (SPY or VIX) is not
    complete, so the published score must not be treated as a market regime.
    ``degraded`` means core inputs are complete but one or more supporting
    dimensions are partial/missing.  Only ``ready`` is authoritative.
    """

    source = snapshot if isinstance(snapshot, Mapping) else {}
    existing = source.get("quality")
    if isinstance(existing, Mapping):
        existing_states = existing.get("domain_states")
        if isinstance(existing_states, Mapping):
            normalized_states = {
                domain: (
                    str(existing_states.get(domain)).strip().lower()
                    if str(existing_states.get(domain)).strip().lower() in _DOMAIN_STATES
                    else "unavailable"
                )
                for domain in REGIME_DOMAINS
            }
        else:
            normalized_states = _infer_domain_states(source)
    else:
        normalized_states = _infer_domain_states(source)

    incomplete = [
        domain for domain in REGIME_DOMAINS if normalized_states[domain] != "ready"
    ]
    missing = [
        domain for domain in REGIME_DOMAINS if normalized_states[domain] == "unavailable"
    ]
    core_incomplete = [
        domain
        for domain in CORE_REGIME_DOMAINS
        if normalized_states[domain] != "ready"
    ]

    if core_incomplete:
        state = "unavailable"
        reason = (
            "Core market inputs are incomplete: "
            + ", ".join(core_incomplete)
            + ". The numeric total is not an actionable Regime score."
        )
    elif incomplete:
        state = "degraded"
        reason = (
            "Core market inputs are available, but supporting domains are "
            "incomplete: " + ", ".join(incomplete) + "."
        )
    else:
        state = "ready"
        reason = "All six Regime input domains are available."

    return {
        "schema_version": "regime-quality-v1",
        "state": state,
        "authoritative": state == "ready",
        "core_domains": list(CORE_REGIME_DOMAINS),
        "domain_states": normalized_states,
        "missing_domains": missing,
        "incomplete_domains": incomplete,
        "reason": reason,
    }
