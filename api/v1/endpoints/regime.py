# -*- coding: utf-8 -*-
"""Regime REST endpoints."""
from __future__ import annotations

import logging
import time
from datetime import date
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.regime import RegimeHistoryResponse, RegimeScoreItem
from src.regime.classifier import classify, compute_regime_score
from src.regime.storage import get_recent_scores, get_regime_score

logger = logging.getLogger(__name__)
router = APIRouter()

# Lightweight in-process throttle for /recompute. Phase 0 has a single user
# so an in-memory guard is enough to prevent accidental hammer-clicking from
# exhausting yfinance / Finnhub quotas.
_RECOMPUTE_COOLDOWN_SEC = 60.0
_recompute_lock = Lock()
_last_recompute_at: float = 0.0


def _with_action_hint(item: dict) -> dict:
    label = item.get("label") or ""
    if label:
        # Classifier returns (label, hint). Use the same mapping.
        _, hint = classify(int(item.get("score") or 0))
        item["action_hint"] = hint
    return item


@router.get("/today", response_model=Optional[RegimeScoreItem])
def get_today():
    row = get_regime_score(date.today())
    if row is None:
        return None
    return RegimeScoreItem(**_with_action_hint(row))


@router.get("/history", response_model=RegimeHistoryResponse)
def get_history(days: int = Query(30, ge=1, le=365)):
    rows = [RegimeScoreItem(**_with_action_hint(r)) for r in get_recent_scores(days=days)]
    return RegimeHistoryResponse(count=len(rows), items=rows)


@router.post("/recompute", response_model=RegimeScoreItem)
def recompute_today():
    global _last_recompute_at
    with _recompute_lock:
        elapsed = time.time() - _last_recompute_at
        if elapsed < _RECOMPUTE_COOLDOWN_SEC:
            raise HTTPException(
                status_code=429,
                detail=f"Recompute cooldown active; retry in {int(_RECOMPUTE_COOLDOWN_SEC - elapsed)}s",
            )
        _last_recompute_at = time.time()
    try:
        res = compute_regime_score(save_to_db=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("regime recompute failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    row = get_regime_score(res.date)
    if row is None:
        raise HTTPException(status_code=500, detail="recompute did not persist")
    return RegimeScoreItem(**_with_action_hint(row))
