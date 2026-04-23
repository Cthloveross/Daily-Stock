# -*- coding: utf-8 -*-
"""Mark historical breakout trades as fake or real.

Heuristic: a "breakout_chase" / "retest" option trade is classified as
``was_fake_breakout = True`` when its ``pnl_pct`` is below a configured
threshold within a short hold. The full retest_tracker (live bars) is Phase 1.
Stage 6 uses this conservative outcome-based proxy so users can see their
assumed-fake rate without needing intraday bar history.
"""
from __future__ import annotations

import argparse
import logging

from sqlalchemy import select

from src.journal.models import JournalTrade
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL, init_journal_schema
from src.storage import get_db

logger = logging.getLogger(__name__)

__all__ = ["backfill_fake_breakout"]


def backfill_fake_breakout(
    portfolio_label: str = DEFAULT_PORTFOLIO_LABEL,
    breakout_styles: tuple[str, ...] = ("breakout_chase", "retest"),
    loss_threshold_pct: float = -20.0,
    short_hold_seconds: int = 90 * 60,
    overwrite: bool = False,
) -> int:
    """Flag likely-fake breakouts in the trades table."""
    init_journal_schema()
    db = get_db()
    updated = 0
    with db.session_scope() as session:
        rows = (
            session.execute(
                select(JournalTrade).where(
                    JournalTrade.portfolio_label == portfolio_label,
                    JournalTrade.trade_style.in_(breakout_styles),
                    JournalTrade.status == "closed",
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            if r.was_fake_breakout is not None and not overwrite:
                continue
            pnl_pct = r.pnl_pct if r.pnl_pct is not None else 0.0
            hold = r.hold_seconds or 0
            # Conservative rule: fake iff lost >20% within 90 minutes.
            fake = pnl_pct < loss_threshold_pct and hold < short_hold_seconds
            r.was_fake_breakout = bool(fake)
            updated += 1
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_LABEL)
    parser.add_argument("--loss-threshold", type=float, default=-20.0)
    parser.add_argument("--short-hold-seconds", type=int, default=90 * 60)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = backfill_fake_breakout(
        args.portfolio,
        loss_threshold_pct=args.loss_threshold,
        short_hold_seconds=args.short_hold_seconds,
        overwrite=args.overwrite,
    )
    print(f"Flagged fake-breakout on {n} trades.")


if __name__ == "__main__":
    main()
