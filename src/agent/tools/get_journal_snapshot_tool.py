# -*- coding: utf-8 -*-
"""Agent tool: snapshot the user's recent Journal state."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from src.journal.analytics import reality_test
from src.journal.models import JournalPhaseState, JournalTrade
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL, init_journal_schema
from src.storage import get_db


def get_journal_snapshot_tool(
    days: int = 30,
    portfolio: Optional[str] = None,
) -> dict:
    """Return recent trades count / win rate / DTE share / phase / quick reality test.

    Designed to be small enough that LLMs can cite individual numbers without
    hitting token limits.
    """
    portfolio = portfolio or DEFAULT_PORTFOLIO_LABEL
    init_journal_schema()
    since = date.today() - timedelta(days=days)
    db = get_db()
    with db.session_scope() as session:
        trades = (
            session.execute(
                select(JournalTrade)
                .where(JournalTrade.portfolio_label == portfolio)
                .where(JournalTrade.entry_time >= datetime.combine(since, datetime.min.time()))
            )
            .scalars()
            .all()
        )
        phase_row = session.get(JournalPhaseState, 1)
        current_phase = int(phase_row.phase) if phase_row else 0
        # Weekly 0DTE count for "quota near limit" warnings used by option_trader skill.
        one_week_ago = datetime.combine(date.today() - timedelta(days=7), datetime.min.time())
        zero_dte_week = (
            session.execute(
                select(func.count(JournalTrade.id))
                .where(JournalTrade.portfolio_label == portfolio)
                .where(JournalTrade.dte_bucket == "0DTE")
                .where(JournalTrade.entry_time >= one_week_ago)
            )
            .scalar_one()
        )

        trade_dicts = [
            {
                "id": t.id,
                "status": t.status,
                "pnl_net": t.pnl_net,
                "dte_bucket": t.dte_bucket,
                "trade_style": t.trade_style,
            }
            for t in trades
        ]

    closed = [t for t in trade_dicts if t["status"] == "closed" and t["pnl_net"] is not None]
    wins = sum(1 for t in closed if t["pnl_net"] > 0)
    by_bucket: dict[str, int] = {}
    for t in trade_dicts:
        b = t.get("dte_bucket") or "equity"
        by_bucket[b] = by_bucket.get(b, 0) + 1

    return {
        "window_days": days,
        "total_trades": len(trade_dicts),
        "closed_trades": len(closed),
        "win_rate": (wins / len(closed)) if closed else None,
        "dte_counts": by_bucket,
        "zero_dte_trades_last_week": int(zero_dte_week or 0),
        "reality_test": reality_test(trade_dicts, top_n=5),
        "current_phase": current_phase,
    }
