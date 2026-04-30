#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long-running daemon that emits live breakout signals via Moomoo KLine_1M.

Usage:
    # Default — read tickers from .env / WATCHLIST_TICKERS, log signals to stdout
    python scripts/run_breakout_live.py

    # Explicit ticker list
    python scripts/run_breakout_live.py --tickers AMZN,NVDA,TSLA

    # Loosen the regime gate
    python scripts/run_breakout_live.py --regime-min 50 --volume-mult 1.0

The script enforces the same prerequisites as Phase A:
    1. ``MOOMOO_OPEND_ENABLED=true``
    2. ``moomoo-api`` SDK installed
    3. OpenD running and logged in

Each detected breakout (after Q1-Q5 filter pass) is printed as a single JSON
line so downstream consumers (Telegram bot, log shipper, etc.) can parse it
trivially. To wire to an internal notifier, import and call
``LiveBreakoutRunner`` directly with your own ``on_signal`` callback.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env so launchd can find MOOMOO_OPEND_ENABLED + STOCK_LIST without
# needing to wire EnvironmentVariables into the plist.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

from src.breakout.live_runner import LiveBreakoutRunner  # noqa: E402


def _resolve_tickers(arg: str | None) -> list[str]:
    if arg:
        return [t.strip().upper() for t in arg.split(",") if t.strip()]
    # Try multiple env-var names (project uses STOCK_LIST as the canonical
    # one; WATCHLIST_TICKERS / STOCK_LIST_US are alternates).
    for name in ("WATCHLIST_TICKERS", "STOCK_LIST_US", "STOCK_LIST"):
        raw = os.environ.get(name) or ""
        if raw.strip():
            return [t.strip().upper() for t in raw.split(",") if t.strip()]
    return []


def _emit(result) -> None:
    """Default callback: dump JSON to stdout."""
    rec = {
        "ticker": result.signal.code,
        "passed": bool(result.passed),
        "reason": result.reason,
        "rejected_at": result.rejected_at,
        "bar_close": result.signal.bar.c,
        "bar_time": result.signal.bar.t.isoformat() if result.signal.bar.t else None,
        "reference_high": result.signal.reference_high,
        "regime_score": result.q1_regime_score,
    }
    if result.q3_volume is not None:
        rec["volume_multiple"] = getattr(result.q3_volume, "ratio", None)
    if result.q5_rs is not None:
        rec["rs_vs_spy"] = getattr(result.q5_rs, "delta", None)
    print(json.dumps(rec, ensure_ascii=False, default=str), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tickers", help="comma-separated ticker list")
    parser.add_argument("--regime-min", type=int, default=55)
    parser.add_argument("--volume-mult", type=float, default=1.2)
    parser.add_argument("--host", default=os.environ.get("MOOMOO_OPEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOOMOO_OPEND_PORT", "11111")))
    parser.add_argument("--passed-only", action="store_true", help="emit only signals that pass Q1-Q5")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s | %(levelname)-7s | %(name)s: %(message)s",
    )

    tickers = _resolve_tickers(args.tickers)
    if not tickers:
        print(
            "no tickers — pass --tickers AMZN,NVDA or set WATCHLIST_TICKERS / STOCK_LIST_US",
            file=sys.stderr,
        )
        return 2

    enabled = (os.environ.get("MOOMOO_OPEND_ENABLED") or "").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        print(
            "MOOMOO_OPEND_ENABLED is not true — set it after launching OpenD",
            file=sys.stderr,
        )
        return 2

    def cb(result):
        if args.passed_only and not result.passed:
            return
        _emit(result)

    runner = LiveBreakoutRunner(
        tickers=tickers,
        on_signal=cb,
        host=args.host,
        port=args.port,
        regime_min=args.regime_min,
        volume_multiple=args.volume_mult,
    )

    # Graceful shutdown on SIGTERM / SIGINT
    def _shutdown(signum, _frame):  # noqa: ANN001
        logging.info("received signal %s — stopping runner", signum)
        runner.stop()

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        runner.run_forever()
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
