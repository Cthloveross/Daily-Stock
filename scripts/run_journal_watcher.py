# -*- coding: utf-8 -*-
"""Long-running watcher for broker CSV drops.

Usage:
    python -m scripts.run_journal_watcher [--portfolio default_moomoo_us]

Install requirement beforehand: `pip install watchdog>=3.0`.
"""
from __future__ import annotations

import argparse
import logging

from src.journal.folder_watcher import start_watching
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_LABEL)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start_watching(portfolio=args.portfolio)


if __name__ == "__main__":
    main()
