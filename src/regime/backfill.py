# -*- coding: utf-8 -*-
"""Backfill regime scores for the past N trading days.

Usage:
    python -m src.regime.backfill --days 90
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from src.regime.classifier import compute_regime_score


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ok = 0
    for i in range(args.days, -1, -1):
        d = date.today() - timedelta(days=i)
        if not _is_weekday(d):
            continue
        try:
            res = compute_regime_score(target_date=d, save_to_db=True)
            print(f"{d}  score={res.score:+4d}  label={res.label}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"{d}  FAILED: {exc}")
    print(f"\nDone. {ok} trading days written.")


if __name__ == "__main__":
    main()
