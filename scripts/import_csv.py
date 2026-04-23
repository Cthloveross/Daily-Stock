# -*- coding: utf-8 -*-
"""Import a broker CSV into the journal and rebuild trades.

Usage:
    python -m scripts.import_csv --csv tests/fixtures/journal/moomoo_sample.csv \
        --broker moomoo_us
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.journal.brokers.moomoo_us import parse as parse_moomoo
from src.journal.matcher import match_legs_fifo
from src.journal.storage import (
    DEFAULT_PORTFOLIO_LABEL,
    init_journal_schema,
    insert_events_from_orders,
    query_events_for_matching,
    record_import,
    replace_trades,
)

logger = logging.getLogger("import_csv")

_PARSERS = {"moomoo_us": parse_moomoo}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--broker", default="moomoo_us", choices=list(_PARSERS.keys()))
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_LABEL)
    parser.add_argument("--no-rebuild", action="store_true", help="Skip FIFO rebuild")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    init_journal_schema()
    content = args.csv.read_bytes()
    orders = _PARSERS[args.broker](content)
    logger.info(
        "Parsed %d filled orders (%d options)",
        len(orders),
        sum(1 for o in orders if o.instrument and o.instrument.is_option),
    )

    import_id = record_import(
        source_path=str(args.csv.resolve()),
        content=content,
        broker=args.broker,
        rows_total=len(orders),
        portfolio_label=args.portfolio,
    )
    if import_id is None:
        print("CSV already imported (sha256 match); skipping event insertion.")
    else:
        inserted, skipped = insert_events_from_orders(
            import_id, orders, portfolio_label=args.portfolio
        )
        print(f"Imported {inserted} new events ({skipped} dupes skipped).")

    if args.no_rebuild:
        return
    events = query_events_for_matching(portfolio_label=args.portfolio)
    trades = match_legs_fifo(events)
    written = replace_trades(trades, portfolio_label=args.portfolio)
    closed = sum(1 for t in trades if t["status"] == "closed")
    open_ = sum(1 for t in trades if t["status"] == "open")
    print(f"Rebuilt {written} trades ({closed} closed, {open_} open).")


if __name__ == "__main__":
    main()
