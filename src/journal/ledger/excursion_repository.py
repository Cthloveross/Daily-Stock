# -*- coding: utf-8 -*-
"""Persistence for episode excursions and the local 5m-bar store.

* ``journal_v2_episode_excursions`` 是 append-only：唯一键
  ``(build_id, episode_id, code_version, attempt)``。默认重算＝幂等返回既有
  最优行；仅当调用方显式 ``allow_retry=True`` 且新结果**严格更好**
  （status 等级 ready>partial>missing_bars>not_applicable，或同级且覆盖的
  bar 数更多）时，以 attempt+1 追加新行——旧行永不改写，无新 bar 时重跑
  零写。读取一律取最优/最新 attempt。口径变更递增
  ``EXCURSION_CODE_VERSION`` 追加新行。
* 时间戳约定（2026-08-25 复核修复 3）：所有 datetime 在插入前统一转 UTC
  （SQLite 存 wall-clock，无时区标注），读取时按 UTC 贴标——存的是什么就
  返回什么。``underlying-5m/1.0`` 的历史行是 ET wall-clock 存储的，保留为
  历史记录，默认读路径（当前 code_version）不再触及。
* ``market_5m_bars`` 是市场数据持久层：只写**已完结**的常规时段 bar，
  ``(symbol, bar_start_at)`` 冲突时跳过（先到先得，绝不覆写）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select

from src.journal.excursions import (
    EXCURSION_CODE_VERSION,
    EXCURSION_SOURCE,
    ExcursionResult,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.ledger.review_flow_models import EpisodeExcursion, Market5mBar
from src.storage import get_db

__all__ = [
    "ExcursionAppendResult",
    "StoredEpisodeExcursion",
    "append_episode_excursion",
    "get_episode_excursion",
    "is_strictly_better",
    "list_episode_excursions",
    "load_market_5m_bars",
    "persist_market_5m_bars",
]

_BAR_MINUTES = 5

# status 等级：重试是否「严格更好」的排序依据（复核修复 5）。
_STATUS_RANK = {
    "ready": 3,
    "partial": 2,
    "missing_bars": 1,
    "not_applicable": 0,
}


@dataclass(frozen=True)
class StoredEpisodeExcursion:
    """One immutable excursion row, safe for API projection."""

    excursion_id: int
    account_key: str
    episode_build_id: int
    position_episode_id: int
    code_version: str
    attempt: int
    source: str
    status: str
    status_reason: Optional[str]
    exposure: Optional[int]
    u0: Optional[float]
    u0_at: Optional[datetime]
    u0_flag: Optional[str]
    mfe_underlying_pct: Optional[float]
    mfe_at: Optional[datetime]
    mae_underlying_pct: Optional[float]
    mae_at: Optional[datetime]
    mae_before_mfe: Optional[bool]
    atr14: Optional[float]
    mfe_atr: Optional[float]
    mae_atr: Optional[float]
    bars_used: int
    coverage_start: Optional[datetime]
    coverage_end: Optional[datetime]
    missing_sessions: tuple[str, ...]
    computed_at: datetime


@dataclass(frozen=True)
class ExcursionAppendResult:
    excursion: StoredEpisodeExcursion
    created: bool


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    """UTC 归一：aware → 换算到 UTC；naive → 按 UTC 贴标。

    写路径必须先经此转换（SQLite 存 wall-clock），读路径据此把存储的
    UTC wall-clock 原样返回为 aware UTC——不做任何二次换算。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _stored(row: EpisodeExcursion) -> StoredEpisodeExcursion:
    try:
        missing = tuple(
            str(item) for item in json.loads(str(row.missing_sessions_json))
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        missing = ()
    return StoredEpisodeExcursion(
        excursion_id=int(row.id),
        account_key=str(row.account_key),
        episode_build_id=int(row.episode_build_id),
        position_episode_id=int(row.position_episode_id),
        code_version=str(row.code_version),
        attempt=int(row.attempt),
        source=str(row.source),
        status=str(row.status),
        status_reason=(
            str(row.status_reason) if row.status_reason is not None else None
        ),
        exposure=int(row.exposure) if row.exposure is not None else None,
        u0=_float(row.u0),
        u0_at=_utc(row.u0_at),
        u0_flag=str(row.u0_flag) if row.u0_flag is not None else None,
        mfe_underlying_pct=_float(row.mfe_underlying_pct),
        mfe_at=_utc(row.mfe_at),
        mae_underlying_pct=_float(row.mae_underlying_pct),
        mae_at=_utc(row.mae_at),
        mae_before_mfe=(
            bool(row.mae_before_mfe) if row.mae_before_mfe is not None else None
        ),
        atr14=_float(row.atr14),
        mfe_atr=_float(row.mfe_atr),
        mae_atr=_float(row.mae_atr),
        bars_used=int(row.bars_used),
        coverage_start=_utc(row.coverage_start),
        coverage_end=_utc(row.coverage_end),
        missing_sessions=missing,
        computed_at=_utc(row.computed_at),
    )


def _rank(status: str) -> int:
    return _STATUS_RANK.get(str(status), -1)


def _best(
    rows: Sequence[StoredEpisodeExcursion],
) -> StoredEpisodeExcursion:
    """最优/最新 attempt：status 等级优先，其次覆盖 bar 数，再次 attempt。"""
    return max(
        rows,
        key=lambda row: (_rank(row.status), row.bars_used, row.attempt),
    )


def is_strictly_better(
    result: ExcursionResult,
    stored: StoredEpisodeExcursion,
) -> bool:
    """重试是否严格优于既有最优行（复核修复 5：无提升绝不写）。"""
    new_rank, old_rank = _rank(result.status), _rank(stored.status)
    if new_rank != old_rank:
        return new_rank > old_rank
    return int(result.bars_used) > int(stored.bars_used)


def append_episode_excursion(
    result: ExcursionResult,
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    episode_build_id: int,
    position_episode_id: int,
    code_version: str = EXCURSION_CODE_VERSION,
    source: str = EXCURSION_SOURCE,
    allow_retry: bool = False,
) -> ExcursionAppendResult:
    """Append one excursion row, idempotent per (build, episode, version).

    默认（``allow_retry=False``）：同 key 已有行 → 幂等返回既有最优行，
    零写。``allow_retry=True``（回填脚本重试瞬时失败用）：仅当 ``result``
    严格优于既有最优行时，以 ``attempt=max+1`` 追加新行；否则同样零写。
    """
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        existing_rows = [
            _stored(row)
            for row in session.execute(
                select(EpisodeExcursion).where(
                    EpisodeExcursion.episode_build_id == episode_build_id,
                    EpisodeExcursion.position_episode_id
                    == position_episode_id,
                    EpisodeExcursion.code_version == code_version,
                )
            ).scalars()
        ]
        attempt = 1
        if existing_rows:
            best = _best(existing_rows)
            if not allow_retry or not is_strictly_better(result, best):
                return ExcursionAppendResult(excursion=best, created=False)
            attempt = max(row.attempt for row in existing_rows) + 1
        row = EpisodeExcursion(
            account_key=account_key,
            episode_build_id=episode_build_id,
            position_episode_id=position_episode_id,
            code_version=code_version,
            attempt=attempt,
            source=source,
            status=result.status,
            status_reason=result.status_reason,
            exposure=result.exposure,
            u0=result.u0,
            u0_at=_utc(result.u0_at),
            u0_flag=result.u0_flag,
            mfe_underlying_pct=result.mfe_underlying_pct,
            mfe_at=_utc(result.mfe_at),
            mae_underlying_pct=result.mae_underlying_pct,
            mae_at=_utc(result.mae_at),
            mae_before_mfe=result.mae_before_mfe,
            atr14=result.atr14,
            mfe_atr=result.mfe_atr,
            mae_atr=result.mae_atr,
            bars_used=result.bars_used,
            coverage_start=_utc(result.coverage_start),
            coverage_end=_utc(result.coverage_end),
            missing_sessions_json=json.dumps(
                list(result.missing_sessions), ensure_ascii=False
            ),
        )
        session.add(row)
        session.flush()
        return ExcursionAppendResult(excursion=_stored(row), created=True)


def get_episode_excursion(
    *,
    episode_build_id: int,
    position_episode_id: int,
    code_version: str = EXCURSION_CODE_VERSION,
) -> Optional[StoredEpisodeExcursion]:
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = [
            _stored(row)
            for row in session.execute(
                select(EpisodeExcursion).where(
                    EpisodeExcursion.episode_build_id == episode_build_id,
                    EpisodeExcursion.position_episode_id
                    == position_episode_id,
                    EpisodeExcursion.code_version == code_version,
                )
            ).scalars()
        ]
        return _best(rows) if rows else None


def list_episode_excursions(
    *,
    episode_build_id: int,
    code_version: str = EXCURSION_CODE_VERSION,
) -> tuple[StoredEpisodeExcursion, ...]:
    """每个 episode 只返回最优/最新 attempt 的一行（按 episode id 升序）。"""
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(EpisodeExcursion)
            .where(
                EpisodeExcursion.episode_build_id == episode_build_id,
                EpisodeExcursion.code_version == code_version,
            )
            .order_by(EpisodeExcursion.position_episode_id.asc())
        ).scalars()
        by_episode: dict[int, list[StoredEpisodeExcursion]] = {}
        for row in rows:
            stored = _stored(row)
            by_episode.setdefault(stored.position_episode_id, []).append(
                stored
            )
        return tuple(
            _best(candidates)
            for _episode_id, candidates in sorted(by_episode.items())
        )


def persist_market_5m_bars(
    symbol: str,
    bars: Sequence[Mapping[str, Any]],
    *,
    source: Optional[str] = None,
    now: Optional[datetime] = None,
) -> tuple[int, int]:
    """Insert completed RTH 5m bars, skipping existing (symbol, start) rows.

    ``bars`` 是 :func:`filter_regular_session_bars` 的输出行（``start_et``
    aware datetime + OHLC float）。只插入 **已完结** 的 bar（start + 5min ≤
    now），绝不让盘中未完结 bar 冻结进持久层。返回 ``(written, skipped)``。
    """
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be non-empty")
    moment = now if now is not None else datetime.now(timezone.utc)
    init_ledger_schema()
    db = get_db()
    written = 0
    skipped = 0
    with db.session_scope() as session:
        existing_starts = {
            _utc(value)
            for value in session.execute(
                select(Market5mBar.bar_start_at).where(
                    Market5mBar.symbol == normalized_symbol
                )
            ).scalars()
        }
        for bar in bars:
            start_et = bar.get("start_et")
            if not isinstance(start_et, datetime) or start_et.tzinfo is None:
                skipped += 1
                continue
            start_utc = start_et.astimezone(timezone.utc)
            if start_utc + timedelta(minutes=_BAR_MINUTES) > moment:
                # 未完结 bar：宁可下次再写，也不持久化半根 K 线。
                skipped += 1
                continue
            if start_utc in existing_starts:
                skipped += 1
                continue
            try:
                open_ = float(bar["open"])
                high = float(bar["high"])
                low = float(bar["low"])
                close = float(bar["close"])
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if open_ <= 0 or close <= 0 or high < low:
                skipped += 1
                continue
            volume = bar.get("volume")
            session.add(
                Market5mBar(
                    symbol=normalized_symbol,
                    bar_start_at=start_utc,
                    session_date_et=str(bar.get("session_date_et") or "")
                    or start_et.date().isoformat(),
                    open=Decimal(str(open_)),
                    high=Decimal(str(high)),
                    low=Decimal(str(low)),
                    close=Decimal(str(close)),
                    volume=(
                        Decimal(str(volume)) if volume is not None else None
                    ),
                    source=source,
                )
            )
            existing_starts.add(start_utc)
            written += 1
    return written, skipped


def load_market_5m_bars(
    symbol: str,
    *,
    start_session_et: str,
    end_session_et: str,
) -> list[dict[str, Any]]:
    """Read persisted bars as raw-bar dicts for :func:`compute_excursion`.

    返回行形如 ``{"date": ISO(UTC), "open": …, "high": …, "low": …,
    "close": …, "volume": …}``（时间戳＝bar 开始时间，与计算口径一致）。
    """
    normalized_symbol = str(symbol or "").strip().upper()
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(Market5mBar)
            .where(
                Market5mBar.symbol == normalized_symbol,
                Market5mBar.session_date_et >= start_session_et,
                Market5mBar.session_date_et <= end_session_et,
            )
            .order_by(Market5mBar.bar_start_at.asc())
        ).scalars()
        return [
            {
                "date": _utc(row.bar_start_at).isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": (
                    float(row.volume) if row.volume is not None else None
                ),
            }
            for row in rows
        ]
