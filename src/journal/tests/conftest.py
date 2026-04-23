# -*- coding: utf-8 -*-
"""Journal test harness: isolate DB per test using a temp SQLite file."""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def isolated_sqlite(tmp_path, monkeypatch):
    """Redirect DATABASE_PATH to a tmp SQLite file and reset config + DB singletons."""
    db_file = tmp_path / "journal_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))

    # Reset Config singleton so it re-reads DATABASE_PATH.
    import src.config as config_mod

    config_mod.Config.reset_instance()
    # Reset the DatabaseManager singleton so the new URL takes effect.
    import src.storage as storage

    storage.DatabaseManager.reset_instance()
    yield db_file
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()
