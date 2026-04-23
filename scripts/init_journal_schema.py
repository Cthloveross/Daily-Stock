# -*- coding: utf-8 -*-
"""Create all Journal tables (idempotent).

Usage:
    python -m scripts.init_journal_schema
"""
from __future__ import annotations

import logging

from src.journal.storage import init_journal_schema


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_journal_schema()
    print("Journal schema initialised.")


if __name__ == "__main__":
    main()
