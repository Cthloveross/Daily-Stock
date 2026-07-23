# -*- coding: utf-8 -*-
"""Regime storage: save / fetch RegimeScore rows."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from src.regime.models import RegimeScore
from src.regime.quality import assess_regime_snapshot
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
            # Refreshing today's row must also refresh the evidence timestamp.
            # SQLite stores this as a naive wall clock, so persist UTC by
            # contract and restore the UTC offset on read.
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None),
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
    # Import lazily to keep the storage/classifier dependency acyclic.
    from src.regime.classifier import current_market_date

    cutoff = current_market_date() - timedelta(days=days)
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
    snapshot = json.loads(row.snapshot_json) if row.snapshot_json else {}
    # Old rows remain readable without a schema migration.  The inferred
    # quality is conservative (especially for ambiguous all-zero premarket
    # payloads) and is added only to the returned object, not written here.
    snapshot["quality"] = assess_regime_snapshot(snapshot)
    generated_at = row.generated_at
    if generated_at is not None:
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        else:
            generated_at = generated_at.astimezone(timezone.utc)

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
        "snapshot": snapshot,
        "version": row.version,
        "generated_at": generated_at,
        "user_perceived_quality": row.user_perceived_quality,
        "user_did_trade": row.user_did_trade,
    }
