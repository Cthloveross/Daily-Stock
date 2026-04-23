# -*- coding: utf-8 -*-
"""Regime Classifier main entry: compose fetchers + scorers -> RegimeResult."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from src.regime.fetchers import RegimeDataFetcher
from src.regime.scorers import (
    score_macro_penalty,
    score_market_direction,
    score_premarket_activity,
    score_prev_day_structure,
    score_sector_rotation,
    score_volatility,
)

__all__ = ["RegimeResult", "RegimeDataFetcher", "classify", "compute_regime_score"]

logger = logging.getLogger(__name__)


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


def classify(score: int, *, aggressive: int = 75, standard: int = 55, cautious: int = 35) -> tuple[str, str]:
    """Map a numeric score onto the four action labels."""
    if score >= aggressive:
        return "aggressive", "Full size within plan; breakouts > retests."
    if score >= standard:
        return "standard", "Standard risk; wait for retests, skip chases."
    if score >= cautious:
        return "cautious", "Half size; retests only; no 0DTE."
    return "no_trade", "Stand aside today; paper-trade instead."


def compute_regime_score(
    target_date: Optional[date] = None,
    watchlist: Optional[list[str]] = None,
    save_to_db: bool = True,
    thresholds: Optional[dict] = None,
) -> RegimeResult:
    """End-to-end: fetch data -> 6 scorers -> classify -> persist.

    Thresholds default to (75, 55, 35) but can be overridden for backtesting.
    """
    if target_date is None:
        target_date = date.today()
    if watchlist is None:
        watchlist = _default_watchlist()

    fetcher = RegimeDataFetcher()
    spy = fetcher.get_spy_snapshot(target_date)
    vix = fetcher.get_vix(target_date)
    events = fetcher.get_macro_events(target_date, watchlist)
    sectors = fetcher.get_sector_performance(target_date)
    prev_day = fetcher.get_prev_day_structure(target_date)
    premarket = fetcher.get_premarket_activity(watchlist, target_date)

    d1 = score_market_direction(spy)
    d2 = score_volatility(vix)
    d3 = score_macro_penalty(events)
    d4 = score_sector_rotation(sectors)
    d5 = score_prev_day_structure(prev_day)
    d6 = score_premarket_activity(premarket)
    total = d1 + d2 + d3 + d4 + d5 + d6

    th = thresholds or {}
    label, action_hint = classify(
        total,
        aggressive=int(th.get("aggressive", 75)),
        standard=int(th.get("standard", 55)),
        cautious=int(th.get("cautious", 35)),
    )

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
        snapshot={
            "spy": spy,
            "vix": vix,
            "events": events,
            "sectors": sectors,
            "prev_day": prev_day,
            "premarket": premarket,
        },
    )

    if save_to_db:
        from src.regime.storage import save_regime_score

        save_regime_score(result)

    return result


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
