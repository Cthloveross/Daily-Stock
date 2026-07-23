#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect Moomoo trade history without writing the journal database.

Standard output contains a de-identified aggregate summary only.  Passing
``--output`` writes a separate JSON export with whitelisted order, fill and
fee fields for subsequent analysis.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

from src.journal.brokers.moomoo_readonly import (  # noqa: E402
    MARKET_TIMEZONES,
    MoomooReadonlyError,
    ProbeConfig,
    run_readonly_probe,
)


def _parse_local_datetime(value: str, *, market: str, is_end: bool) -> datetime:
    zone = MARKET_TIMEZONES[market]
    text = value.strip()
    try:
        if len(text) == 10:
            parsed_date = datetime.strptime(text, "%Y-%m-%d").date()
            parsed_time = time(23, 59, 59) if is_end else time.min
            return datetime.combine(parsed_date, parsed_time, tzinfo=zone)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid datetime {value!r}; use YYYY-MM-DD or ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _write_export(path: Path, payload: dict, *, overwrite: bool) -> None:
    target = path.expanduser().resolve()
    if target.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {target}")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {target.parent}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists() and not overwrite:
            raise FileExistsError(f"output already exists: {target}")
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _safe_error(message: str) -> dict:
    return {
        "ok": False,
        "mode": "moomoo_readonly_probe",
        "journal_database_written": False,
        "error": message,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", choices=["LIVE", "SIMULATE"], default="LIVE")
    parser.add_argument("--market", choices=["US", "HK"], default="US")
    parser.add_argument("--days", type=int, default=30, help="lookback when --start is omitted")
    parser.add_argument("--start", help="market-local YYYY-MM-DD or ISO-8601")
    parser.add_argument("--end", help="market-local YYYY-MM-DD or ISO-8601")
    parser.add_argument(
        "--acc-id",
        help="stable account ID; omitted only when the target account is unique",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MOOMOO_OPEND_HOST", "127.0.0.1"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MOOMOO_OPEND_PORT", "11111")),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--connect-timeout", type=float, default=0.5, help=argparse.SUPPRESS)
    parser.add_argument("--query-timeout", type=float, default=15.0)
    parser.add_argument(
        "--overall-timeout",
        type=float,
        default=180.0,
        help="hard deadline for the isolated read-only worker (seconds)",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--output", type=Path, help="write whitelisted records to this JSON file")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow replacing an existing --output file",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")

    zone = MARKET_TIMEZONES[args.market]
    try:
        end = (
            _parse_local_datetime(args.end, market=args.market, is_end=True)
            if args.end
            else datetime.now(zone)
        )
        start = (
            _parse_local_datetime(args.start, market=args.market, is_end=False)
            if args.start
            else end - timedelta(days=args.days)
        )
        config = ProbeConfig(
            start=start,
            end=end,
            env=args.env,
            market=args.market,
            host=args.host,
            port=args.port,
            acc_id=args.acc_id,
            connect_timeout=args.connect_timeout,
            query_timeout=args.query_timeout,
            overall_timeout=args.overall_timeout,
            retries=args.retries,
        )
    except (ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))

    try:
        result = run_readonly_probe(config)
        if args.output:
            _write_export(args.output, result.export_payload, overwrite=args.overwrite)
        summary = dict(result.summary)
        summary["export_written"] = bool(args.output)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except MoomooReadonlyError as exc:
        print(json.dumps(_safe_error(str(exc)), ensure_ascii=False, sort_keys=True))
        return 1
    except (FileExistsError, FileNotFoundError, OSError) as exc:
        print(json.dumps(_safe_error(str(exc)), ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
