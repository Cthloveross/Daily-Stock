# -*- coding: utf-8 -*-
"""Schema init idempotency tests."""
from __future__ import annotations

import sqlite3

from src.options.storage import init_options_schema


def _tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def test_init_options_schema_creates_tables(tmp_path):
    db = tmp_path / "opts.db"
    conn = sqlite3.connect(str(db))
    try:
        init_options_schema(conn=conn)
        tables = _tables(conn)
        assert "option_chains_cache" in tables
        assert "iv_snapshots" in tables
    finally:
        conn.close()


def test_init_options_schema_idempotent(tmp_path):
    db = tmp_path / "opts.db"
    conn = sqlite3.connect(str(db))
    try:
        init_options_schema(conn=conn)
        init_options_schema(conn=conn)
        init_options_schema(conn=conn)
        # No exception, and tables still present with expected structure.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(option_chains_cache)")}
        assert {
            "id",
            "underlying",
            "expiry",
            "right",
            "fetched_at",
            "spot_at_fetch",
            "chain_json",
        }.issubset(cols)
    finally:
        conn.close()
