# -*- coding: utf-8 -*-
"""SQLite schema for options caches (option_chains_cache, iv_snapshots).

Stage 1 only creates the tables; writes land in Stage 10 (Agent option tool
caches chain snapshots) and Phase 1 (daily IV snapshot cron).

Connection / path convention follows the rest of the repo: uses the same DB
file that ``src.storage`` binds to, resolving via ``src.storage.get_db_path``
when available, falling back to ``data/daily_stock.db``.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DDL_OPTION_CHAINS_CACHE = """
CREATE TABLE IF NOT EXISTS option_chains_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying  TEXT NOT NULL,
    expiry      DATE NOT NULL,
    right       TEXT NOT NULL,       -- 'C' or 'P' or 'BOTH'
    fetched_at  TIMESTAMP NOT NULL,
    spot_at_fetch REAL,
    chain_json  TEXT NOT NULL,
    UNIQUE(underlying, expiry, right, fetched_at)
)
"""

_DDL_IV_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS iv_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying   TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    atm_iv       REAL,
    ref_expiry   TEXT,
    ref_dte      INTEGER,
    spot         REAL,
    UNIQUE(underlying, snapshot_date)
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_option_chains_underlying_expiry "
    "ON option_chains_cache(underlying, expiry)",
    "CREATE INDEX IF NOT EXISTS idx_iv_snapshots_underlying_date "
    "ON iv_snapshots(underlying, snapshot_date)",
]


def _resolve_db_path() -> Path:
    """Find the canonical SQLite file, deferring to src.storage when present."""
    try:
        from src.storage import get_db_path  # type: ignore

        return Path(get_db_path())
    except Exception:  # noqa: BLE001
        pass
    # Fall back to repo-standard location.
    return Path("data") / "daily_stock.db"


def init_options_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create option_chains_cache + iv_snapshots if missing. Idempotent."""
    close_after = False
    if conn is None:
        db = _resolve_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        close_after = True
    try:
        cur = conn.cursor()
        cur.execute(_DDL_OPTION_CHAINS_CACHE)
        cur.execute(_DDL_IV_SNAPSHOTS)
        for stmt in _DDL_INDEXES:
            cur.execute(stmt)
        conn.commit()
        logger.info("options schema ready at %s", _resolve_db_path())
    finally:
        if close_after:
            conn.close()
