# -*- coding: utf-8 -*-
"""Excursion rows (append-only, idempotent per code_version) + 5m bar store."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError

from src.journal.excursions import (
    ExcursionEpisode,
    compute_excursion,
)
from src.journal.ledger.episode_repository import (
    append_latest_position_episode_build,
    get_latest_position_episode_page,
)
from src.journal.ledger.excursion_repository import (
    append_episode_excursion,
    get_episode_excursion,
    is_strictly_better,
    list_episode_excursions,
    load_market_5m_bars,
    persist_market_5m_bars,
)
from src.journal.ledger.repository import (
    import_statement_batch,
    init_ledger_schema,
)
from src.journal.tests.test_episode_repository import _detailed_statement
from src.opportunities.intraday_bursts import filter_regular_session_bars
from src.storage import get_db

ET = ZoneInfo("America/New_York")


def _seed_episode() -> tuple[int, int]:
    import_statement_batch(_detailed_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=10)
    return build.build_id, page.items[0].episode_id


def _raw_bars() -> list[dict]:
    rows = []
    for minute, ohlc in [
        (30, (100.0, 101.0, 99.0, 100.5)),
        (35, (100.5, 104.0, 100.0, 103.5)),
    ]:
        start = datetime(2026, 7, 20, 9, minute, tzinfo=ET)
        rows.append(
            {
                "date": start.isoformat(),
                "open": ohlc[0],
                "high": ohlc[1],
                "low": ohlc[2],
                "close": ohlc[3],
                "volume": 1000,
            }
        )
    return rows


def _result():
    return compute_excursion(
        ExcursionEpisode(
            asset_type="option",
            direction="long",
            option_right="C",
            opened_at=datetime(2026, 7, 20, 9, 30, tzinfo=ET),
            closed_at=datetime(2026, 7, 20, 10, 30, tzinfo=ET),
        ),
        _raw_bars(),
    )


def test_append_is_idempotent_per_code_version_and_recomputable_by_version():
    build_id, episode_id = _seed_episode()
    first = append_episode_excursion(
        _result(), episode_build_id=build_id, position_episode_id=episode_id
    )
    assert first.created is True
    replay = append_episode_excursion(
        _result(), episode_build_id=build_id, position_episode_id=episode_id
    )
    assert replay.created is False
    assert replay.excursion.excursion_id == first.excursion.excursion_id

    # 口径变更 → 新 code_version 追加新行，旧行保留。
    upgraded = append_episode_excursion(
        _result(),
        episode_build_id=build_id,
        position_episode_id=episode_id,
        code_version="underlying-5m/2.0-test",
    )
    assert upgraded.created is True

    stored = get_episode_excursion(
        episode_build_id=build_id, position_episode_id=episode_id
    )
    assert stored is not None
    assert stored.status == "partial" or stored.status == "ready"
    # 开仓 09:30 整 → U0 就是 09:30 bar 的 open（100），MFE = 104/100−1。
    assert stored.mfe_underlying_pct == pytest.approx(0.04, abs=1e-6)
    assert list_episode_excursions(episode_build_id=build_id)


def test_timestamps_round_trip_as_utc():
    """复核修复 3：aware ET 进 → 存 UTC → API 读出同一时刻（UTC 标注）。"""
    from datetime import timezone

    build_id, episode_id = _seed_episode()
    result = _result()
    assert result.u0_at is not None and result.u0_at.tzinfo is not None
    append_episode_excursion(
        result, episode_build_id=build_id, position_episode_id=episode_id
    )
    stored = get_episode_excursion(
        episode_build_id=build_id, position_episode_id=episode_id
    )
    assert stored is not None
    # 同一时刻（跨时区相等），且序列化口径就是 UTC。
    assert stored.u0_at == result.u0_at
    assert stored.u0_at.tzinfo == timezone.utc
    assert stored.u0_at.hour == 13 and stored.u0_at.minute == 30  # 09:30 ET
    assert stored.coverage_start == result.coverage_start
    assert stored.coverage_end == result.coverage_end
    if result.mfe_at is not None:
        assert stored.mfe_at == result.mfe_at


def test_retry_appends_only_strictly_better_attempts():
    """复核修复 5：瞬时失败不被永久冻结，但无提升绝不写（真幂等）。"""
    build_id, episode_id = _seed_episode()
    empty = compute_excursion(
        ExcursionEpisode(
            asset_type="option",
            direction="long",
            option_right="C",
            opened_at=datetime(2026, 7, 20, 9, 30, tzinfo=ET),
            closed_at=datetime(2026, 7, 20, 10, 30, tzinfo=ET),
        ),
        [],  # 抓取瞬时失败：无 bar
    )
    first = append_episode_excursion(
        empty,
        episode_build_id=build_id,
        position_episode_id=episode_id,
        allow_retry=True,
    )
    assert first.created is True
    assert first.excursion.status == "missing_bars"
    assert first.excursion.attempt == 1

    # 重跑但仍无新 bar → 零写。
    replay = append_episode_excursion(
        empty,
        episode_build_id=build_id,
        position_episode_id=episode_id,
        allow_retry=True,
    )
    assert replay.created is False
    assert replay.excursion.excursion_id == first.excursion.excursion_id

    # bars 后到 → 严格更好 → attempt=2 追加；读取取最优行。
    better = _result()
    assert is_strictly_better(better, first.excursion)
    retried = append_episode_excursion(
        better,
        episode_build_id=build_id,
        position_episode_id=episode_id,
        allow_retry=True,
    )
    assert retried.created is True
    assert retried.excursion.attempt == 2

    stored = get_episode_excursion(
        episode_build_id=build_id, position_episode_id=episode_id
    )
    assert stored is not None
    assert stored.attempt == 2
    assert stored.status in {"ready", "partial"}
    listed = list_episode_excursions(episode_build_id=build_id)
    assert [item.attempt for item in listed
            if item.position_episode_id == episode_id] == [2]

    # 默认（allow_retry=False）保持旧语义：幂等返回最优行，零写。
    frozen = append_episode_excursion(
        empty, episode_build_id=build_id, position_episode_id=episode_id
    )
    assert frozen.created is False
    assert frozen.excursion.attempt == 2


def test_excursion_rows_are_append_only():
    build_id, episode_id = _seed_episode()
    stored = append_episode_excursion(
        _result(), episode_build_id=build_id, position_episode_id=episode_id
    ).excursion
    init_ledger_schema()
    db = get_db()
    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.connection().exec_driver_sql(
                "UPDATE journal_v2_episode_excursions SET status = 'ready' "
                f"WHERE id = {stored.excursion_id}"
            )


def test_attempt_column_migration_rebuilds_old_table(isolated_sqlite):
    """复核修复 5 的一次性迁移：旧表（无 attempt）重建为新唯一键，
    行原样保留（attempt=1），deny trigger 重装，append-only 语义不破坏。"""
    import sqlite3

    # 用旧 DDL 手工造一张 1.0 时代的表 + 一行数据。
    conn = sqlite3.connect(str(isolated_sqlite))
    conn.execute(
        """
        CREATE TABLE journal_v2_episode_excursions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_key VARCHAR(64) NOT NULL,
            episode_build_id INTEGER NOT NULL,
            position_episode_id INTEGER NOT NULL,
            code_version VARCHAR(32) NOT NULL,
            source VARCHAR(32) NOT NULL,
            status VARCHAR(24) NOT NULL,
            status_reason TEXT,
            exposure INTEGER,
            u0 NUMERIC(28, 10),
            u0_at DATETIME,
            u0_flag VARCHAR(48),
            mfe_underlying_pct NUMERIC(18, 10),
            mfe_at DATETIME,
            mae_underlying_pct NUMERIC(18, 10),
            mae_at DATETIME,
            mae_before_mfe BOOLEAN,
            atr14 NUMERIC(28, 10),
            mfe_atr NUMERIC(18, 10),
            mae_atr NUMERIC(18, 10),
            bars_used INTEGER NOT NULL,
            coverage_start DATETIME,
            coverage_end DATETIME,
            missing_sessions_json TEXT NOT NULL,
            computed_at DATETIME NOT NULL,
            CONSTRAINT uq_jv2_excursion_episode_version
                UNIQUE (episode_build_id, position_episode_id, code_version)
        )
        """
    )
    # 旧表的显式索引：RENAME 不改索引名，迁移必须先删否则 create_all 撞名。
    conn.execute(
        "CREATE INDEX ix_jv2_excursion_scope ON journal_v2_episode_excursions "
        "(account_key, episode_build_id, status)"
    )
    conn.execute(
        "INSERT INTO journal_v2_episode_excursions "
        "(account_key, episode_build_id, position_episode_id, code_version, "
        "source, status, bars_used, missing_sessions_json, computed_at) "
        "VALUES ('default_moomoo_us', 1, 42, 'underlying-5m/1.0', "
        "'underlying_5m', 'missing_bars', 0, '[]', '2026-08-25 00:00:00')"
    )
    conn.commit()
    conn.close()

    init_ledger_schema()

    conn = sqlite3.connect(str(isolated_sqlite))
    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(journal_v2_episode_excursions)"
        )
    }
    assert "attempt" in columns
    rows = conn.execute(
        "SELECT position_episode_id, code_version, attempt, status "
        "FROM journal_v2_episode_excursions"
    ).fetchall()
    assert rows == [(42, "underlying-5m/1.0", 1, "missing_bars")]
    # 旧过渡表不残留。
    leftovers = conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE "
        "'journal_v2_episode_excursions_pre_attempt'"
    ).fetchall()
    assert leftovers == []
    # deny trigger 重装（UPDATE 被拒）。
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE journal_v2_episode_excursions SET status='ready'"
        )
    conn.close()


def test_bar_store_skips_incomplete_and_duplicate_bars():
    bars = filter_regular_session_bars(_raw_bars())
    assert len(bars) == 2
    # now 落在第二根 bar 结束之前 → 只有第一根是「已完结」。
    mid_bar_now = datetime(2026, 7, 20, 13, 38, tzinfo=timezone.utc)
    written, skipped = persist_market_5m_bars(
        "nvda", bars, source="test", now=mid_bar_now
    )
    assert (written, skipped) == (1, 1)
    # 之后重放：第二根补写，第一根幂等跳过。
    written, skipped = persist_market_5m_bars(
        "NVDA",
        bars,
        source="test",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    assert (written, skipped) == (1, 1)

    loaded = load_market_5m_bars(
        "NVDA", start_session_et="2026-07-20", end_session_et="2026-07-20"
    )
    assert len(loaded) == 2
    assert loaded[0]["open"] == 100.0
    # 读出的行可直接回流纯函数。
    assert len(filter_regular_session_bars(loaded)) == 2
