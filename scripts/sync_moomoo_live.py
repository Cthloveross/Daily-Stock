#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy Moomoo-to-journal writer (intentionally paused).

Usage:
    # This command now exits safely and points to the read-only probe.
    python scripts/sync_moomoo_live.py

The previous implementation selected an account by position, omitted order
fees and wrote partially reconciled rows into the Journal database.  It must
not run until it is replaced by the bounded, provenance-aware import pipeline.

Exit code 3 means the legacy writer is paused.  The command never creates an
SDK context and never writes the Journal database.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure project root on path when invoked directly.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env when invoked outside a shell that already source'd it (e.g.
# launchd's clean environment).
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

from src.journal.storage import DEFAULT_PORTFOLIO_LABEL  # noqa: E402


def _parse_dt(s: str) -> datetime:
    # Accept date-only or full ISO datetime
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"unrecognised datetime format: {s!r}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=_parse_dt, help="window start (default: end - 7d)")
    parser.add_argument("--end", type=_parse_dt, help="window end (default: now UTC)")
    parser.add_argument("--days", type=int, default=7, help="when --start omitted, look back N days")
    parser.add_argument("--env", choices=["SIMULATE", "LIVE"], help="override MOOMOO_TRADE_ENV")
    parser.add_argument("--market", default="US", choices=["US", "HK", "CN"])
    parser.add_argument(
        "--portfolio",
        default=DEFAULT_PORTFOLIO_LABEL,
        help=f"journal portfolio label (default: {DEFAULT_PORTFOLIO_LABEL})",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.parse_args(argv)
    print(
        json.dumps(
            {
                "ok": False,
                "kind": "LegacySyncPaused",
                "journal_database_written": False,
                "error": (
                    "Legacy Moomoo journal sync is paused; use "
                    "scripts/probe_moomoo_readonly.py for a bounded, read-only export."
                ),
            },
            ensure_ascii=False,
        )
    )
    return 3


if __name__ == "__main__":
    sys.exit(main())
