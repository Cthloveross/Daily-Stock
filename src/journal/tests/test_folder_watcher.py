# -*- coding: utf-8 -*-
"""Folder watcher handler tests (no watchdog observer involved)."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "journal"
    / "moomoo_inline_sample.csv"
)


def test_non_csv_skipped(tmp_path):
    from src.journal.folder_watcher import MoomooCsvHandler

    handler = MoomooCsvHandler(processed_dir=tmp_path / "done")
    (tmp_path / "done").mkdir()
    target = tmp_path / "something.txt"
    target.write_text("irrelevant")
    assert handler.handle_path(target) is None


def test_wrong_prefix_skipped(tmp_path):
    from src.journal.folder_watcher import MoomooCsvHandler

    handler = MoomooCsvHandler(processed_dir=tmp_path / "done")
    (tmp_path / "done").mkdir()
    target = tmp_path / "random.csv"
    target.write_text("foo,bar")
    assert handler.handle_path(target) is None


def test_moomoo_csv_is_ingested_and_moved(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "watcher.db"))
    monkeypatch.setenv("DS_WATCHER_SETTLE_SECONDS", "0")
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()

    from src.journal.folder_watcher import MoomooCsvHandler

    inbox = tmp_path / "inbox"
    processed = tmp_path / "done"
    inbox.mkdir()
    processed.mkdir()
    target = inbox / "History-test.csv"
    target.write_bytes(FIXTURE.read_bytes())

    handler = MoomooCsvHandler(processed_dir=processed)
    summary = handler.handle_path(target)
    assert summary is not None
    assert summary["status"] == "ok"
    assert summary["inserted"] >= 5
    assert summary["trades_total"] >= 2
    assert not target.exists()  # moved
    moved = list(processed.iterdir())
    assert len(moved) == 1

    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def test_second_drop_of_same_file_is_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "watcher.db"))
    monkeypatch.setenv("DS_WATCHER_SETTLE_SECONDS", "0")
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()

    from src.journal.folder_watcher import MoomooCsvHandler

    inbox = tmp_path / "inbox"
    processed = tmp_path / "done"
    inbox.mkdir()
    processed.mkdir()

    handler = MoomooCsvHandler(processed_dir=processed)

    def _drop(name: str):
        target = inbox / name
        target.write_bytes(FIXTURE.read_bytes())
        return handler.handle_path(target)

    first = _drop("History-1.csv")
    second = _drop("History-2.csv")
    assert first["status"] == "ok"
    assert second["status"] == "duplicate"

    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()
