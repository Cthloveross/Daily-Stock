# -*- coding: utf-8 -*-
"""Agent tool: get the latest Regime Score.

Returns a plain dict compatible with the Agent orchestrator and its JSON
serialization layer. All numeric fields are included so the LLM can quote
exact numbers without calculating.
"""
from __future__ import annotations

from datetime import date

from src.regime.storage import get_regime_score


def get_regime_score_tool(target_date: str | None = None) -> dict:
    """Look up the saved regime score for ``target_date`` (YYYY-MM-DD, defaults today).

    Returns ``{"found": False, ...}`` when nothing is stored — the caller / skill
    is expected to treat that as "regime unknown, remain conservative".
    """
    d = date.fromisoformat(target_date) if target_date else date.today()
    row = get_regime_score(d)
    if row is None:
        return {"found": False, "date": d.isoformat(), "hint": "No regime score stored; run src.regime.cli or recompute endpoint."}
    return {"found": True, **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}}
