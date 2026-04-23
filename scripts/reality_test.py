# -*- coding: utf-8 -*-
"""Print the 'remove the Top N trades, what's left?' Reality Test to stdout.

Usage:
    python -m scripts.reality_test [--top-n 5] [--since 2026-01-01]
                                   [--portfolio default_moomoo_us]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime

from sqlalchemy import select

from src.journal.analytics import dte_bucket_win_rates, dte_distribution, reality_test
from src.journal.models import JournalTrade
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL, init_journal_schema
from src.storage import get_db


def _load_trades(portfolio_label: str, since: date | None) -> list[dict]:
    db = get_db()
    with db.session_scope() as session:
        stmt = select(JournalTrade).where(JournalTrade.portfolio_label == portfolio_label)
        rows = session.execute(stmt).scalars().all()
        out = []
        for r in rows:
            if since and r.entry_time and r.entry_time.date() < since:
                continue
            out.append(
                {
                    "id": r.id,
                    "underlying": r.underlying,
                    "raw_symbol": r.raw_symbol,
                    "is_option": bool(r.is_option),
                    "status": r.status,
                    "pnl_net": r.pnl_net,
                    "pnl_pct": r.pnl_pct,
                    "entry_time": r.entry_time,
                    "dte_bucket": r.dte_bucket,
                    "dte_at_entry": r.dte_at_entry,
                }
            )
        return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--since", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_LABEL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    init_journal_schema()

    trades = _load_trades(args.portfolio, args.since)
    rt = reality_test(trades, top_n=args.top_n)
    dte_dist = dte_distribution(trades)
    win_rates = dte_bucket_win_rates(trades)

    print("=" * 64)
    print(f"REALITY TEST   portfolio={args.portfolio}   since={args.since or 'all'}")
    print("=" * 64)
    if rt["total_trades"] == 0:
        print("No closed trades in scope.")
        return

    print(f"Total closed trades      : {rt['total_trades']}")
    print(f"Total net PnL            : ${rt['total_pnl_net']:,.2f}")
    print(f"Top {rt['top_n']} net PnL              : ${rt['top_n_pnl_net']:,.2f}", end="")
    if rt["top_n_pct_of_total"] is not None:
        print(f"  ({rt['top_n_pct_of_total']:.1f}% of total)")
    else:
        print()
    print(f"PnL without Top {rt['top_n']}         : ${rt['pnl_without_top_n']:,.2f}")
    print(f"Median net PnL per trade : ${rt['median_pnl_net']:,.2f}")
    print(f"Top {rt['top_n']} trade ids           : {rt['top_n_ids']}")
    print()
    print("DTE distribution :")
    for bucket in ("0DTE", "1-3DTE", "4-7DTE", "8-30DTE", "30+DTE", "equity"):
        if bucket in dte_dist:
            print(f"  {bucket:10s} : {dte_dist[bucket]:4d}")
    print()
    print("Win rate by DTE bucket (closed trades):")
    for bucket in ("0DTE", "1-3DTE", "4-7DTE", "8-30DTE", "30+DTE", "equity"):
        stats = win_rates.get(bucket)
        if not stats or stats["count"] == 0:
            continue
        wr = stats["win_rate"]
        avg = stats["avg_pnl_net"]
        print(
            f"  {bucket:10s}: n={stats['count']:4d}  win_rate="
            f"{(wr * 100 if wr is not None else 0):5.1f}%   avg PnL=${avg:,.2f}"
        )


if __name__ == "__main__":
    main()
