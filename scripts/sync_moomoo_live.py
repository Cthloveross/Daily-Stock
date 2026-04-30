#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull Moomoo trade history into the journal pipeline.

Usage:
    # Default: last 7 days, SIMULATE on US, default portfolio
    python scripts/sync_moomoo_live.py

    # Custom window
    python scripts/sync_moomoo_live.py --start 2026-04-01 --end 2026-04-29

    # Real account (requires MOOMOO_TRADE_ENV=LIVE and an unlocked OpenD)
    MOOMOO_OPEND_ENABLED=true MOOMOO_TRADE_ENV=LIVE python scripts/sync_moomoo_live.py

Exit codes: 0 = success, 1 = MoomooLiveError, 2 = unexpected error.
Cron-friendly: prints a single JSON line at the end so output is parseable.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

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

from src.journal.brokers.moomoo_live import MoomooLiveError  # noqa: E402
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL  # noqa: E402
from src.services.moomoo_sync_service import sync_live_orders  # noqa: E402


def _parse_dt(s: str) -> datetime:
    # Accept date-only or full ISO datetime
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"unrecognised datetime format: {s!r}")


def main() -> int:
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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s | %(levelname)-7s | %(name)s: %(message)s",
    )

    try:
        result = sync_live_orders(
            start=args.start,
            end=args.end,
            window_days=args.days,
            trd_env=args.env,
            market=args.market,
            portfolio=args.portfolio,
        )
    except MoomooLiveError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "kind": "MoomooLiveError"}, ensure_ascii=False))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc), "kind": "Unexpected"}, ensure_ascii=False))
        if args.verbose:
            raise
        return 2

    payload = {"ok": True, **result.to_dict()}
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
