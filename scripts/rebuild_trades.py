# -*- coding: utf-8 -*-
"""Rebuild the journal_trades table from journal_orders via FIFO matching.

Usage:
    python -m scripts.rebuild_trades [--portfolio default_moomoo_us]
"""
from __future__ import annotations

import argparse
import logging

from src.journal.matcher import match_legs_fifo
from src.journal.storage import (
    DEFAULT_PORTFOLIO_LABEL,
    init_journal_schema,
    query_events_for_matching,
    replace_trades,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_LABEL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_journal_schema()
    events = query_events_for_matching(portfolio_label=args.portfolio)
    trades = match_legs_fifo(events)
    written = replace_trades(trades, portfolio_label=args.portfolio)
    closed = sum(1 for t in trades if t["status"] == "closed")
    open_ = sum(1 for t in trades if t["status"] == "open")
    print(f"Events: {len(events)}  Trades written: {written}  (closed={closed}, open={open_})")


if __name__ == "__main__":
    main()
