# -*- coding: utf-8 -*-
"""Phase A 深度复盘表（蓝图 17 §三(e) 1/2/3）。

三张表：

* ``journal_v2_daily_review_sessions`` —— 引导式日终复盘会话，append-only
  修订链（同 ReviewAnnotation 约定：revision + previous_id + content_sha256，
  deny trigger 拒绝 UPDATE/DELETE）。密封（sealed_at）后只允许追加「揭示」
  修订；``revealed_after_seal`` 与时间戳本身就是数据（盲评顺序留痕）。
* ``journal_v2_episode_excursions`` —— 每回合 MAE/MFE（标的 5m 近似）的
  append-only 记录；``(episode_build_id, position_episode_id, code_version,
  attempt)`` 唯一。同版本幂等重算＝直接返回既有最优行；仅当 bars 后到且重算
  结果**严格更好**（status 等级 ready>partial>missing_bars 或覆盖更高）时，
  以 attempt+1 追加新行（旧行永不改写），读取取最优/最新 attempt；口径变更
  递增 code_version 追加新行。``attempt`` 列于 2026-08-25 通过重建表迁移加入
  （CREATE TABLE new + copy，旧行 attempt=1，append-only 语义保持）。
* ``market_5m_bars`` —— 标的 5m bar 的本地持久层（bar **开始**时间口径）。
  这是市场数据缓存而非账本：只插入已完结的 bar、同 ``(symbol, bar_start_at)``
  幂等跳过，不装 deny trigger（供应商修正时允许人工重建），但仓库层从不
  UPDATE。它的存在让偏移覆盖率随时间趋近 100%（yfinance 5m 仅回溯 ~60 天）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from src.storage import Base

__all__ = [
    "DailyReviewSession",
    "EpisodeExcursion",
    "Market5mBar",
]


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for newly recorded immutable rows."""
    return datetime.now(timezone.utc)


class DailyReviewSession(Base):
    """One immutable revision of a guided daily close-review session."""

    __tablename__ = "journal_v2_daily_review_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_key = Column(String(64), nullable=False)
    et_date = Column(String(10), nullable=False)
    session_kind = Column(String(16), nullable=False)
    revision = Column(Integer, nullable=False)
    previous_session_id = Column(
        Integer,
        ForeignKey("journal_v2_daily_review_sessions.id"),
    )
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
    )
    started_at = Column(DateTime(timezone=True), nullable=False)
    sealed_at = Column(DateTime(timezone=True))
    revealed_at = Column(DateTime(timezone=True))
    revealed_after_seal = Column(Boolean, nullable=False, default=False)
    steps_json = Column(Text, nullable=False)
    process_scores_json = Column(Text, nullable=False)
    violation_acks_json = Column(Text, nullable=False)
    note = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "account_key",
            "et_date",
            "revision",
            name="uq_jv2_daily_review_revision",
        ),
        UniqueConstraint(
            "previous_session_id",
            name="uq_jv2_daily_review_previous",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_jv2_daily_review_revision",
        ),
        CheckConstraint(
            "session_kind IN ('trading_day', 'rest_day')",
            name="ck_jv2_daily_review_kind",
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_jv2_daily_review_hash",
        ),
        CheckConstraint(
            # 揭示只能发生在密封之后；未密封会话不可能带揭示标记。
            "revealed_after_seal = 0 OR sealed_at IS NOT NULL",
            name="ck_jv2_daily_review_reveal_after_seal",
        ),
        Index(
            "ix_jv2_daily_review_scope",
            "account_key",
            "et_date",
            "revision",
        ),
    )


class EpisodeExcursion(Base):
    """MAE/MFE for one closed episode, underlying-5m approximation."""

    __tablename__ = "journal_v2_episode_excursions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_key = Column(String(64), nullable=False)
    episode_build_id = Column(
        Integer,
        ForeignKey("journal_v2_episode_builds.id"),
        nullable=False,
    )
    position_episode_id = Column(
        Integer,
        ForeignKey("journal_v2_position_episodes.id"),
        nullable=False,
    )
    code_version = Column(String(32), nullable=False)
    attempt = Column(Integer, nullable=False, default=1, server_default="1")
    source = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False)
    status_reason = Column(Text)
    exposure = Column(Integer)
    u0 = Column(Numeric(28, 10))
    u0_at = Column(DateTime(timezone=True))
    u0_flag = Column(String(48))
    mfe_underlying_pct = Column(Numeric(18, 10))
    mfe_at = Column(DateTime(timezone=True))
    mae_underlying_pct = Column(Numeric(18, 10))
    mae_at = Column(DateTime(timezone=True))
    mae_before_mfe = Column(Boolean)
    atr14 = Column(Numeric(28, 10))
    mfe_atr = Column(Numeric(18, 10))
    mae_atr = Column(Numeric(18, 10))
    bars_used = Column(Integer, nullable=False, default=0)
    coverage_start = Column(DateTime(timezone=True))
    coverage_end = Column(DateTime(timezone=True))
    missing_sessions_json = Column(Text, nullable=False)
    computed_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "episode_build_id",
            "position_episode_id",
            "code_version",
            "attempt",
            name="uq_jv2_excursion_episode_version",
        ),
        CheckConstraint(
            "status IN ('ready', 'partial', 'missing_bars', 'not_applicable')",
            name="ck_jv2_excursion_status",
        ),
        CheckConstraint(
            "exposure IS NULL OR exposure IN (-1, 1)",
            name="ck_jv2_excursion_exposure",
        ),
        CheckConstraint(
            "bars_used >= 0",
            name="ck_jv2_excursion_bars_used",
        ),
        CheckConstraint(
            "attempt >= 1",
            name="ck_jv2_excursion_attempt",
        ),
        Index(
            "ix_jv2_excursion_scope",
            "account_key",
            "episode_build_id",
            "status",
        ),
    )


class Market5mBar(Base):
    """One completed underlying 5m bar (start-time convention), persisted."""

    __tablename__ = "market_5m_bars"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False)
    bar_start_at = Column(DateTime(timezone=True), nullable=False)
    session_date_et = Column(String(10), nullable=False)
    open = Column(Numeric(28, 10), nullable=False)
    high = Column(Numeric(28, 10), nullable=False)
    low = Column(Numeric(28, 10), nullable=False)
    close = Column(Numeric(28, 10), nullable=False)
    volume = Column(Numeric(28, 10))
    source = Column(String(48))
    fetched_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "bar_start_at",
            name="uq_market_5m_bar",
        ),
        CheckConstraint(
            "high >= low AND open > 0 AND close > 0",
            name="ck_market_5m_bar_ohlc",
        ),
        Index(
            "ix_market_5m_bar_session",
            "symbol",
            "session_date_et",
        ),
    )
