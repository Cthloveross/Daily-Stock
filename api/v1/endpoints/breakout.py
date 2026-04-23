# -*- coding: utf-8 -*-
"""Breakout REST endpoints."""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from api.v1.schemas.regime import BreakoutSignalItem, BreakoutSignalsResponse
from src.journal.models import JournalTrade
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL, init_journal_schema
from src.storage import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/signals", response_model=BreakoutSignalsResponse)
def list_breakout_signals(
    limit: int = Query(20, ge=1, le=200),
    only_fake: Optional[bool] = Query(None),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> BreakoutSignalsResponse:
    """Return the most recent trades classified as breakout_chase / retest.

    Optionally filter to ones flagged as fake (for "how often do I get caught?" views).
    """
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        stmt = (
            select(JournalTrade)
            .where(
                JournalTrade.portfolio_label == portfolio,
                JournalTrade.trade_style.in_(["breakout_chase", "retest"]),
            )
            .order_by(JournalTrade.entry_time.desc().nulls_last(), JournalTrade.id.desc())
            .limit(limit)
        )
        rows = session.execute(stmt).scalars().all()
        items: list[BreakoutSignalItem] = []
        for r in rows:
            if only_fake is True and r.was_fake_breakout is not True:
                continue
            if only_fake is False and r.was_fake_breakout is True:
                continue
            items.append(
                BreakoutSignalItem(
                    trade_id=r.id,
                    underlying=r.underlying,
                    entry_time=r.entry_time,
                    trade_style=r.trade_style,
                    was_fake_breakout=r.was_fake_breakout,
                    pnl_net=r.pnl_net,
                    regime_score_at_entry=r.regime_score_at_entry,
                    breakout_volume_mult=r.breakout_volume_mult,
                    rs_vs_spy=r.rs_vs_spy,
                )
            )
        return BreakoutSignalsResponse(count=len(items), items=items)
