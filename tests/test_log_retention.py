# -*- coding: utf-8 -*-
"""跨日期日志 retention 的安全边界测试。

关键合同：只删本前缀按日期命名的文件；当天文件、归档目录、其他前缀、
非日志文件一概不碰；默认（0/0）完全关闭；删除失败不中断。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.logging_config import cleanup_old_logs, setup_logging


def _day(offset: int) -> str:
    return (date.today() - timedelta(days=offset)).strftime("%Y%m%d")


def _touch(path, size: int = 10) -> None:
    path.write_bytes(b"x" * size)


@pytest.fixture()
def log_dir(tmp_path):
    return tmp_path / "logs"


def test_disabled_by_default_deletes_nothing(log_dir):
    log_dir.mkdir()
    _touch(log_dir / f"api_server_{_day(400)}.log")

    deleted = cleanup_old_logs(str(log_dir), "api_server")

    assert deleted == []
    assert (log_dir / f"api_server_{_day(400)}.log").exists()


def test_deletes_only_matching_dated_files_older_than_retention(log_dir):
    log_dir.mkdir()
    old_main = log_dir / f"api_server_{_day(31)}.log"
    old_debug = log_dir / f"api_server_debug_{_day(31)}.log"
    old_backup = log_dir / f"api_server_{_day(31)}.log.2"
    fresh = log_dir / f"api_server_{_day(5)}.log"
    today = log_dir / f"api_server_{_day(0)}.log"
    other_prefix = log_dir / f"stock_analysis_{_day(90)}.log"
    wrapper_log = log_dir / "launchagent.uvicorn.out.log"
    gz_archive = log_dir / f"api_server_{_day(90)}.log.gz"
    for path in (old_main, old_debug, old_backup, fresh, today, other_prefix, wrapper_log, gz_archive):
        _touch(path)

    deleted = cleanup_old_logs(str(log_dir), "api_server", retention_days=30)

    assert sorted(p.name for p in deleted) == sorted(
        [old_main.name, old_debug.name, old_backup.name]
    )
    assert fresh.exists() and today.exists()
    assert other_prefix.exists() and wrapper_log.exists() and gz_archive.exists()


def test_never_recurses_into_archive_subdirectory(log_dir):
    archive = log_dir / "archive" / "incident"
    archive.mkdir(parents=True)
    archived = archive / f"api_server_{_day(400)}.log"
    _touch(archived)

    deleted = cleanup_old_logs(
        str(log_dir), "api_server", retention_days=1, max_total_bytes=1
    )

    assert deleted == []
    assert archived.exists()


def test_size_cap_evicts_oldest_dates_first_and_spares_today(log_dir):
    log_dir.mkdir()
    oldest = log_dir / f"api_server_{_day(3)}.log"
    middle = log_dir / f"api_server_{_day(2)}.log"
    newest = log_dir / f"api_server_{_day(1)}.log"
    today = log_dir / f"api_server_{_day(0)}.log"
    for path in (oldest, middle, newest):
        _touch(path, size=100)
    _touch(today, size=10_000)

    deleted = cleanup_old_logs(
        str(log_dir), "api_server", max_total_bytes=150
    )

    # 今天的文件不参与统计也不被删除；从最旧开始删到剩余 <= 上限。
    assert [p.name for p in deleted] == [oldest.name, middle.name]
    assert newest.exists() and today.exists()


def test_unlink_failure_warns_and_continues(log_dir, monkeypatch):
    log_dir.mkdir()
    stubborn = log_dir / f"api_server_{_day(40)}.log"
    doomed = log_dir / f"api_server_{_day(50)}.log"
    _touch(stubborn)
    _touch(doomed)

    from pathlib import Path
    original_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self.name == stubborn.name:
            raise OSError("busy")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    deleted = cleanup_old_logs(str(log_dir), "api_server", retention_days=30)

    assert [p.name for p in deleted] == [doomed.name]
    assert stubborn.exists()


def test_setup_logging_applies_env_retention(log_dir, monkeypatch):
    log_dir.mkdir()
    stale = log_dir / f"unit_test_{_day(45)}.log"
    _touch(stale)
    monkeypatch.setenv("LOG_RETENTION_DAYS", "30")
    monkeypatch.setenv("LOG_RETENTION_MAX_TOTAL_MB", "0")

    setup_logging(log_prefix="unit_test", log_dir=str(log_dir))

    assert not stale.exists()
    assert (log_dir / f"unit_test_{_day(0)}.log").exists()


def test_setup_logging_ignores_malformed_env(log_dir, monkeypatch):
    log_dir.mkdir()
    stale = log_dir / f"unit_test_{_day(45)}.log"
    _touch(stale)
    monkeypatch.setenv("LOG_RETENTION_DAYS", "not-a-number")
    monkeypatch.delenv("LOG_RETENTION_MAX_TOTAL_MB", raising=False)

    setup_logging(log_prefix="unit_test", log_dir=str(log_dir))

    assert stale.exists()
