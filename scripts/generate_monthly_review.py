# -*- coding: utf-8 -*-
"""Generate the AI monthly retrospective for a given YYYY-MM.

Usage:
    python -m scripts.generate_monthly_review --month 2026-03
    python -m scripts.generate_monthly_review --month 2026-03 --dry-run
"""
from __future__ import annotations

import argparse
import logging

from src.journal.monthly_review import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--portfolio", default="default_moomoo_us")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM call, print stats only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run(args.month, portfolio=args.portfolio, dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
