# -*- coding: utf-8 -*-
"""CLI for the explicit, bounded Journal artifact GC (contract F-2a).

Frozen design: ``New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md``.  The default
mode is a zero-write dry-run that opens the SQLite database read-only
(``mode=ro``) and prints the candidate ids, counts, and payload bytes for
the two in-scope preview artifact tables.  Nothing else in this repository
deletes these rows — there is no startup sweep, scheduler, or API endpoint.

Usage:
    python -m scripts.artifact_gc                       # dry-run, read-only
    python -m scripts.artifact_gc --table refresh
    python -m scripts.artifact_gc --receipt             # dry-run + receipt row
    python -m scripts.artifact_gc --apply \
        --backup-path data/backups/stock_analysis-YYYYMMDD.db

``--apply`` refuses to run without ``--backup-path``; the backup must exist,
be a valid SQLite file, and pass ``PRAGMA integrity_check`` before any row
is deleted (contract §4).  Deletion happens in one BEGIN IMMEDIATE
transaction per table with an immutable receipt appended to
``journal_v2_artifact_gc_receipts``.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.journal.ledger.artifact_gc import (
    ARTIFACT_GC_TABLES,
    DEFAULT_ARTIFACT_GC_BATCH_LIMIT,
    DEFAULT_ARTIFACT_GC_GRACE_DAYS,
    ArtifactGcError,
    ArtifactGcReport,
    run_artifact_gc,
)

_SQLITE_URL_PREFIX = "sqlite:///"
_TABLE_ALIASES = {
    "refresh": "journal_v2_refresh_artifacts",
    "position-snapshot": "journal_v2_position_snapshot_artifacts",
    "journal_v2_refresh_artifacts": "journal_v2_refresh_artifacts",
    "journal_v2_position_snapshot_artifacts": (
        "journal_v2_position_snapshot_artifacts"
    ),
}


def _default_db_path() -> str:
    """Resolve the configured SQLite file; fail closed on anything else."""
    from src.config import get_config

    db_url = get_config().get_db_url()
    if not db_url.startswith(_SQLITE_URL_PREFIX):
        raise ArtifactGcError(
            "artifact GC only supports SQLite databases; configured "
            f"database URL is not sqlite:///...: {db_url}"
        )
    return db_url[len(_SQLITE_URL_PREFIX):]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="artifact_gc",
        description=(
            "Bounded GC for expired, never-confirmed, non-latest Journal "
            "preview artifacts (dry-run by default; read-only)."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite database file (default: the configured DATABASE_PATH)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "actually delete candidates inside one guarded transaction per "
            "table; REQUIRES --backup-path"
        ),
    )
    parser.add_argument(
        "--backup-path",
        default=None,
        help=(
            "same-day verified SQLite backup file; validated (exists, SQLite "
            "header, PRAGMA integrity_check=ok) before any delete"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_ARTIFACT_GC_BATCH_LIMIT,
        help="max candidates per table per run (default: %(default)s)",
    )
    parser.add_argument(
        "--grace-days",
        type=int,
        default=DEFAULT_ARTIFACT_GC_GRACE_DAYS,
        help=(
            "days past expiry before a row becomes a candidate "
            "(default: %(default)s; minimum 1)"
        ),
    )
    parser.add_argument(
        "--table",
        action="append",
        choices=sorted(_TABLE_ALIASES),
        default=None,
        help=(
            "restrict to one table (repeatable); default: both in-scope "
            "tables"
        ),
    )
    parser.add_argument(
        "--receipt",
        action="store_true",
        help=(
            "dry-run only: also append a dry_run receipt row (opens the "
            "database read-write for that single insert)"
        ),
    )
    return parser


def _print_report(report: ArtifactGcReport) -> None:
    mode = "apply" if report.run_kind == "apply" else "dry-run"
    print(
        f"artifact_gc {mode} db={report.db_path} "
        f"grace_days={report.grace_days} limit={report.batch_limit} "
        f"cutoff_utc={report.cutoff_utc.isoformat()}"
    )
    if report.backup_path is not None:
        print(
            f"  backup={report.backup_path} sha256={report.backup_sha256}"
        )
    for result in report.results:
        ids = list(result.candidate_ids)
        line = (
            f"  {result.table_name}: candidates={result.candidate_count} "
            f"deleted={result.deleted_count} "
            f"payload_bytes={result.payload_bytes} ids={ids}"
        )
        if result.receipt_id is not None:
            line += f" receipt_id={result.receipt_id}"
        print(line)
    if report.run_kind != "apply":
        print(
            "  (dry-run: no rows were deleted; re-run with --apply "
            "--backup-path <verified-backup> to delete)"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.apply and args.backup_path is None:
        parser.error(
            "--apply requires --backup-path (a same-day verified SQLite "
            "backup); see New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md §4"
        )
    if args.apply and args.receipt:
        parser.error("--receipt is a dry-run option; --apply always writes receipts")
    tables = None
    if args.table:
        tables = []
        for name in args.table:
            resolved = _TABLE_ALIASES[name]
            if resolved not in tables:
                tables.append(resolved)
    try:
        db_path = args.db if args.db is not None else _default_db_path()
        report = run_artifact_gc(
            db_path,
            apply=bool(args.apply),
            backup_path=args.backup_path,
            tables=tables,
            grace_days=args.grace_days,
            batch_limit=args.limit,
            write_dry_run_receipt=bool(args.receipt),
        )
    except ArtifactGcError as exc:
        print(f"artifact_gc: refused: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0


# Keep the alias table honest against the core module's scope tuple.
assert set(_TABLE_ALIASES.values()) == set(ARTIFACT_GC_TABLES)


if __name__ == "__main__":
    sys.exit(main())
