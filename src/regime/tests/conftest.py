# -*- coding: utf-8 -*-
"""Regime test harness: isolated SQLite per test."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_sqlite(tmp_path, monkeypatch):
    db_file = tmp_path / "regime_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))

    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield db_file
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()
