# -*- coding: utf-8 -*-
"""Regime CLI: one-shot score for a given date.

Usage:
    python -m src.regime.cli                    # today
    python -m src.regime.cli --date 2026-04-17  # specific date
    python -m src.regime.cli --no-save --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime

from src.regime.classifier import compute_regime_score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    result = compute_regime_score(target_date=args.date, save_to_db=not args.no_save)

    print("=" * 64)
    print(f"Regime Score  date={result.date}  score={result.score}  label={result.label}")
    print("=" * 64)
    print(f"  d1 market direction : {result.d1_direction:+d}")
    print(f"  d2 volatility       : {result.d2_volatility:+d}")
    print(f"  d3 macro penalty    : {result.d3_macro_penalty:+d}")
    print(f"  d4 sector rotation  : {result.d4_sector:+d}")
    print(f"  d5 prev-day struct  : {result.d5_prev_day:+d}")
    print(f"  d6 premarket        : {result.d6_premarket:+d}")
    print()
    print(f"Action hint : {result.action_hint}")
    if args.verbose:
        print()
        print("Snapshot (raw inputs):")
        print(json.dumps(result.snapshot, indent=2, default=str))


if __name__ == "__main__":
    main()
