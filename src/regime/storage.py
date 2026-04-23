# -*- coding: utf-8 -*-
"""Regime storage: save / fetch RegimeScore rows."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select

from src.regime.models import RegimeScore
from src.storage import Base, get_db

logger = logging.getLogger(__name__)

__all__ = [
    "init_regime_schema",
    "save_regime_score",
    "get_regime_score",
    "get_recent_scores",
]


def init_regime_schema() -> None:
    db = get_db()
    Base.metadata.create_all(db._engine)


def save_regime_score(result) -> None:
    """Upsert a regime score row keyed by date.

    Accepts :class:`RegimeResult` (duck-typed via attributes).
    """
    init_regime_schema()
    db = get_db()
    with db.session_scope() as session:
        existing = session.get(RegimeScore, result.date)
        payload = {
            "score": int(result.score),
            "label": result.label,
            "d1_direction": int(result.d1_direction),
            "d2_volatility": int(result.d2_volatility),
            "d3_macro_penalty": int(result.d3_macro_penalty),
            "d4_sector": int(result.d4_sector),
            "d5_prev_day": int(result.d5_prev_day),
            "d6_premarket": int(result.d6_premarket),
            "snapshot_json": json.dumps(result.snapshot, default=str),
            "version": getattr(result, "version", "v1"),
        }
        if existing is None:
            session.add(RegimeScore(date=result.date, **payload))
        else:
            for k, v in payload.items():
                setattr(existing, k, v)


def get_regime_score(target_date: date) -> Optional[dict]:
    init_regime_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.get(RegimeScore, target_date)
        if row is None:
            return None
        return _row_to_dict(row)


def get_recent_scores(days: int = 30) -> list[dict]:
    init_regime_schema()
    cutoff = date.today() - timedelta(days=days)
    db = get_db()
    with db.session_scope() as session:
        rows = (
            session.execute(
                select(RegimeScore).where(RegimeScore.date >= cutoff).order_by(RegimeScore.date.desc())
            )
            .scalars()
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: RegimeScore) -> dict:
    return {
        "date": row.date,
        "score": row.score,
        "label": row.label,
        "action_hint": None,  # derived by classifier at read time if needed
        "d1_direction": row.d1_direction,
        "d2_volatility": row.d2_volatility,
        "d3_macro_penalty": row.d3_macro_penalty,
        "d4_sector": row.d4_sector,
        "d5_prev_day": row.d5_prev_day,
        "d6_premarket": row.d6_premarket,
        "snapshot": json.loads(row.snapshot_json) if row.snapshot_json else {},
        "version": row.version,
        "generated_at": row.generated_at,
        "user_perceived_quality": row.user_perceived_quality,
        "user_did_trade": row.user_did_trade,
    }
