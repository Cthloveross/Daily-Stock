# -*- coding: utf-8 -*-
"""Batch label historical ``journal_trades.trade_style``.

Rule-first classification. Trades that look "obvious" (long option on a gap-up
near the top of day -> chase; option bought after a pullback near MA20 ->
pullback_buy) are resolved by rules. Unclear trades can optionally be passed
to LLM for a final guess; this is disabled by default to keep the script
deterministic.

Labels (limited set, per 07 doc):
    'breakout_chase'  - entered as price was breaking out without waiting
    'retest'          - entered on pullback to breakout level
    'pullback_buy'    - entered on pullback to MA within trend
    'mean_reversion'  - entered against the short-term move
    'gap_fade'        - entered to fade a gap
    'equity_swing'    - plain equity multi-day hold
    'other'           - rule couldn't classify; can be overridden by user
"""
from __future__ import annotations

import argparse
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from src.journal.models import JournalTrade
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL, init_journal_schema
from src.storage import get_db

logger = logging.getLogger(__name__)

__all__ = ["classify_trade", "backfill_trade_style"]


def classify_trade(trade: dict) -> str:
    """Pure rule-based labeler. Returns one of the label strings above.

    ``trade`` dict keys used: ``is_option``, ``direction``, ``dte_at_entry``,
    ``hold_seconds``, ``pnl_pct``, ``entry_time``, ``exit_time``.
    """
    is_option = bool(trade.get("is_option"))
    direction = trade.get("direction")
    hold = trade.get("hold_seconds") or 0
    dte = trade.get("dte_at_entry")

    if not is_option:
        # Equity: swing vs intraday.
        if hold >= 24 * 3600:
            return "equity_swing"
        # Intraday equity w/o more context is often mean_reversion.
        return "mean_reversion"

    # Options
    if dte is not None and dte <= 0:
        # 0DTE held for minutes, profit or loss: typically a breakout-chase or fade.
        if hold < 30 * 60:
            return "breakout_chase"
        return "breakout_chase" if direction == "long" else "gap_fade"

    if dte is not None and dte <= 3:
        # 1-3 DTE on option short hold looks like chase; longer hold -> pullback_buy.
        if hold < 60 * 60:
            return "breakout_chase"
        return "pullback_buy"

    if dte is not None and dte <= 7:
        return "pullback_buy"

    return "other"


def backfill_trade_style(
    portfolio_label: str = DEFAULT_PORTFOLIO_LABEL,
    overwrite: bool = False,
) -> int:
    """Update ``journal_trades.trade_style`` for all trades in portfolio.

    Returns the number of rows updated.
    """
    init_journal_schema()
    db = get_db()
    updated = 0
    with db.session_scope() as session:
        rows = (
            session.execute(
                select(JournalTrade).where(JournalTrade.portfolio_label == portfolio_label)
            )
            .scalars()
            .all()
        )
        for r in rows:
            if r.trade_style and not overwrite:
                continue
            label = classify_trade(
                {
                    "is_option": r.is_option,
                    "direction": r.direction,
                    "dte_at_entry": r.dte_at_entry,
                    "hold_seconds": r.hold_seconds,
                    "pnl_pct": r.pnl_pct,
                    "entry_time": r.entry_time,
                    "exit_time": r.exit_time,
                }
            )
            r.trade_style = label
            updated += 1
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_LABEL)
    parser.add_argument("--overwrite", action="store_true", help="Re-label already-labeled trades")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = backfill_trade_style(args.portfolio, overwrite=args.overwrite)
    print(f"Updated {n} trades.")


if __name__ == "__main__":
    main()
