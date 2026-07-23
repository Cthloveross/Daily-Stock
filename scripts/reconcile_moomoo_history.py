#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reconcile a Moomoo history CSV with a read-only API export.

This command is strictly read-only: it parses the two supplied files, prints
only a de-identified aggregate JSON summary, and never opens or writes the
Journal database.  It intentionally omits source paths, filenames, broker
order/deal identifiers, and row-level records from standard output.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.journal.brokers.moomoo_statement import (  # noqa: E402
    MoomooStatementError,
    parse_statement,
    reconcile_statement_with_readonly_export,
)


class _ArgumentError(ValueError):
    """Argument parsing failed without allowing argparse to exit with code 2."""


class _SafeInputError(ValueError):
    """Input failure whose message is safe to include in aggregate output."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="Moomoo history CSV to parse",
    )
    parser.add_argument(
        "--readonly-export",
        type=Path,
        required=True,
        help="JSON produced by the read-only Moomoo probe",
    )
    return parser


def _emit_error(code: str, message: str) -> None:
    print(
        json.dumps(
            {
                "analysis_ready": False,
                "error": {"code": code, "message": message},
                "journal_database_written": False,
                "mode": "read_only_reconciliation",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _read_csv(path: Path) -> bytes:
    try:
        return path.expanduser().read_bytes()
    except OSError as exc:
        raise _SafeInputError("CSV input could not be read") from exc


def _read_export(path: Path) -> Mapping[str, Any]:
    try:
        content = path.expanduser().read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise _SafeInputError(
            "read-only export input could not be read"
        ) from exc
    try:
        payload = json.loads(content, parse_float=Decimal)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _SafeInputError(
            "read-only export is not valid JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise _SafeInputError(
            "read-only export must contain a JSON object"
        )
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgumentError:
        _emit_error("invalid_arguments", "invalid command arguments")
        return 1

    try:
        statement = parse_statement(_read_csv(args.csv))
        export = _read_export(args.readonly_export)
        reconciliation = reconcile_statement_with_readonly_export(
            statement,
            export,
        )
    except _SafeInputError as exc:
        _emit_error("input_error", str(exc))
        return 1
    except MoomooStatementError:
        _emit_error("input_error", "input data could not be reconciled")
        return 1
    except (TypeError, ValueError, KeyError):
        _emit_error("input_error", "input data could not be reconciled")
        return 1

    print(
        json.dumps(
            {
                "statement": statement.summary(),
                "reconciliation": reconciliation.summary(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if reconciliation.analysis_ready else 2


if __name__ == "__main__":
    sys.exit(main())
