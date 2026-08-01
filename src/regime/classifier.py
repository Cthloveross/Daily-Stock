# -*- coding: utf-8 -*-
"""Regime Classifier main entry: compose fetchers + scorers -> RegimeResult."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from src.regime.fetchers import RegimeDataFetcher
from src.regime.quality import assess_regime_snapshot
from src.regime.scorers import (
    score_macro_penalty,
    score_market_direction,
    score_premarket_activity,
    score_prev_day_structure,
    score_sector_rotation,
    score_volatility,
)

__all__ = [
    "RegimeResult",
    "RegimeDataFetcher",
    "classify",
    "compute_regime_score",
    "current_market_date",
]

logger = logging.getLogger(__name__)
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass
class RegimeResult:
    """Full regime score breakdown for one trading day."""

    date: date
    score: int
    label: str
    action_hint: str
    d1_direction: int
    d2_volatility: int
    d3_macro_penalty: int
    d4_sector: int
    d5_prev_day: int
    d6_premarket: int
    snapshot: dict = field(default_factory=dict)
    version: str = "v1"
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def classify(score: int, *, aggressive: int = 75, standard: int = 55, cautious: int = 35) -> tuple[str, str]:
    """Map a numeric score onto the four action labels."""
    if score >= aggressive:
        return "aggressive", "Full size within plan; breakouts > retests."
    if score >= standard:
        return "standard", "Standard risk; wait for retests, skip chases."
    if score >= cautious:
        return "cautious", "Half size; retests only; no 0DTE."
    return "no_trade", "Stand aside today; paper-trade instead."


def current_market_date(now: Optional[datetime] = None) -> date:
    """Return the US equity market date, independent of server timezone."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(_NEW_YORK).date()


def compute_regime_score(
    target_date: Optional[date] = None,
    watchlist: Optional[list[str]] = None,
    save_to_db: bool = True,
    thresholds: Optional[dict] = None,
    as_of: Optional[datetime] = None,
) -> RegimeResult:
    """End-to-end: fetch data -> 6 scorers -> classify -> persist.

    Thresholds default to (75, 55, 35) but can be overridden for backtesting.
    ``as_of`` freezes every premarket provider read to one timezone-aware UTC
    instant.  Omitting it preserves the live behavior and captures the clock
    once at the beginning of this computation.
    """
    frozen_as_of = _aware_utc(
        as_of or datetime.now(timezone.utc),
        field_name="as_of",
    )
    if target_date is None:
        target_date = current_market_date(frozen_as_of)
    if watchlist is None:
        watchlist = _default_watchlist()

    fetcher = RegimeDataFetcher()
    try:
        # Local price inputs first.  Supporting remote calendars/premarket
        # cannot consume the request budget before SPY/VIX are observed.
        spy = fetcher.get_spy_snapshot(target_date)
        vix = fetcher.get_vix(target_date)
        sectors = fetcher.get_sector_performance(target_date)
        prev_day = fetcher.get_prev_day_structure(target_date)
        premarket = fetcher.get_premarket_activity(
            watchlist,
            target_date,
            as_of=frozen_as_of,
        )
        events = fetcher.get_macro_events(target_date, watchlist)
    finally:
        close_fetcher = getattr(fetcher, "close", None)
        if callable(close_fetcher):
            close_fetcher()

    d1 = score_market_direction(spy)
    d2 = score_volatility(vix)
    d3 = score_macro_penalty(events)
    d4 = score_sector_rotation(sectors)
    d5 = score_prev_day_structure(prev_day)
    d6 = score_premarket_activity(premarket)
    component_total = d1 + d2 + d3 + d4 + d5 + d6

    snapshot = {
        "spy": spy,
        "vix": vix,
        "events": events,
        "sectors": sectors,
        "prev_day": prev_day,
        "premarket": premarket,
    }
    quality = assess_regime_snapshot(snapshot)
    if quality["state"] == "unavailable":
        # Keep the database's non-null integer contract and make existing
        # score-only safety gates fail closed.  Consumers must use the quality
        # metadata rather than interpret this sentinel as a bearish reading.
        total = 0
        label = "unavailable"
        action_hint = (
            "Regime unavailable: core SPY/VIX inputs are incomplete. "
            "Do not use this value as a trading gate."
        )
        quality["partial_total_suppressed"] = component_total
    else:
        total = component_total

        th = thresholds or {}
        label, action_hint = classify(
            total,
            aggressive=int(th.get("aggressive", 75)),
            standard=int(th.get("standard", 55)),
            cautious=int(th.get("cautious", 35)),
        )
        if quality["state"] == "degraded":
            action_hint = (
                "Provisional Regime: supporting inputs are incomplete. "
                "Treat the numeric bucket as context only; it must not "
                "authorize or block a trade."
            )

    snapshot["quality"] = quality

    result = RegimeResult(
        date=target_date,
        score=total,
        label=label,
        action_hint=action_hint,
        d1_direction=d1,
        d2_volatility=d2,
        d3_macro_penalty=d3,
        d4_sector=d4,
        d5_prev_day=d5,
        d6_premarket=d6,
        snapshot=snapshot,
        version="v2",
        generated_at=datetime.now(timezone.utc),
    )

    if save_to_db:
        from src.regime.storage import save_regime_score

        save_regime_score(result)

    return result


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _default_watchlist() -> list[str]:
    """Fallback watchlist when caller doesn't pass one.

    Reads from the config's ``stock_list``. Handles both ``list[str]`` (what
    src.config returns after parsing the env var) and the legacy raw comma-
    separated string form.
    """
    try:
        from src.config import get_config

        cfg = get_config()
        raw = getattr(cfg, "stock_list", None)
        if raw:
            if isinstance(raw, (list, tuple)):
                return [str(s).strip().upper() for s in raw if str(s).strip()]
            # Fallback: treat as comma-separated string.
            return [s.strip().upper() for s in str(raw).split(",") if s.strip()]
    except Exception:  # noqa: BLE001
        pass
    return ["SPY", "QQQ", "NVDA", "AAPL", "TSLA"]
