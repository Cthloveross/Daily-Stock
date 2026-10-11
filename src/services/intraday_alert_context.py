# -*- coding: utf-8 -*-
"""提示器上下文的默认提供方（Phase B）：只读、有界、可注入替换。

这些函数只在提示器的 **worker 线程**按 TTL 被调用（见
:class:`src.services.intraday_opportunity_alerter.IntradayOpportunityAlerter`
的上下文快照机制），绝不在预热线程执行；任何异常由调用方折叠为「上下文
标缺」——标缺一律降级，绝不冒充。

gate 状态复用 journal edge panel 的 ``compute_gate``。第一次或 30 分钟
市场缓存过期时，它可能在提示器 worker 内刷新日线；因此不会阻塞盘中预热
扫描。取数失败由 ``compute_gate`` 返回明确的 fail-closed 红灯，不会把
缺失数据冒充成 0/1DTE 绿灯。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

__all__ = [
    "default_daily_realized_pnl_provider",
    "default_gate_state_provider",
    "default_open_positions_provider",
]

_ET = ZoneInfo("America/New_York")


def default_gate_state_provider():
    """返回 0/1DTE P0 所需的可校验 regime gate。

    计算逻辑和 ``/journal/edge-panel`` 共用同一实现与 30 分钟日线缓存；调用
    发生在提示器 worker 线程。市场数据不可用时返回 fail-closed 结构，而
    不是 ``None``，从而让台账明确记录红灯原因。
    """

    from src.journal.edge_panel import compute_gate

    return compute_gate()


def _et_day(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_ET).strftime("%Y-%m-%d")


def _build_is_fresh_for_et_date(build, et_date: str) -> bool:
    """Require both build creation and source cutoff to belong to ``et_date``.

    An EpisodeBuild is a reconstruction artifact, not a live broker snapshot.
    A previous-day build that happens to contain no open position / no realized
    loss cannot be used as positive proof of today's flat/PnL state.
    """

    try:
        date.fromisoformat(str(et_date))
        recorded_at = build.recorded_at
        source_cutoff_at = build.source_cutoff_at
        return (
            isinstance(recorded_at, datetime)
            and isinstance(source_cutoff_at, datetime)
            and _et_day(recorded_at) == et_date
            and _et_day(source_cutoff_at) == et_date
        )
    except (AttributeError, TypeError, ValueError):
        return False


def default_open_positions_provider():
    """当前激活 build 的未平仓回合（供 15:30 同日清仓提醒）。

    无 build 或 build 不是当日新鲜证据 → ``None``（标缺）；只有当日 build
    无持仓才返回空 tuple。它仍是 Journal 快照，不冒充实时券商持仓。
    """
    from src.journal.ledger.episode_repository import _latest_build
    from src.journal.ledger.models import PositionEpisode
    from src.journal.ledger.repository import (
        DEFAULT_LEDGER_ACCOUNT_KEY,
        init_ledger_schema,
    )
    from src.services.alert_tiering import OpenPositionFact
    from src.storage import get_db

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, DEFAULT_LEDGER_ACCOUNT_KEY)
        today_et = datetime.now(_ET).strftime("%Y-%m-%d")
        if build is None or not _build_is_fresh_for_et_date(build, today_et):
            return None
        rows = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == int(build.id),
                PositionEpisode.account_key == str(build.account_key),
                PositionEpisode.lifecycle_status == "open",
            )
        ).scalars().all()
        facts = []
        for row in rows:
            facts.append(
                OpenPositionFact(
                    raw_symbol=str(row.raw_symbol),
                    underlying=str(row.underlying),
                    opened_et_date=(
                        _et_day(row.opened_at)
                        if row.opened_at is not None
                        else None
                    ),
                    dte_at_entry=(
                        int(row.dte_at_entry)
                        if row.dte_at_entry is not None
                        else None
                    ),
                )
            )
        facts.sort(key=lambda fact: fact.raw_symbol)
        return tuple(facts)


def default_daily_realized_pnl_provider(et_date: str) -> Optional[float]:
    """当日（ET）已平仓回合的已知净盈亏合计（R6 保险丝输入）。

    无 build 或 build 不是目标 ET 日的新鲜证据 → ``None``（标缺：保险丝
    不触发也不冒充「未触线」）；只有当日 build 明确无平仓才返回 ``0.0``。
    盈亏缺失的回合不计入（只汇总已知
    值——保险丝因此只可能**晚**触发、不会被虚构的亏损误触发）。

    有界性：SQL 侧先按 ``closed_at`` 的保守 UTC 窗口（ET 日 X 的成交必落
    在 ``[X 00:00Z, X+2 00:00Z)``，覆盖 EST/EDT 偏移）预过滤，绝不全量
    扫描整个 build；精确的 ET 日归属仍由 Python 侧 ``_et_day`` 判定
    （SQL 窗口只是粗筛，DST 边界不靠字符串比较裁决）。
    """
    from src.journal.ledger.episode_repository import _latest_build
    from src.journal.ledger.models import PositionEpisode
    from src.journal.ledger.repository import (
        DEFAULT_LEDGER_ACCOUNT_KEY,
        init_ledger_schema,
    )
    from src.storage import get_db

    try:
        day = date.fromisoformat(str(et_date))
    except (TypeError, ValueError):
        # 无效日期无法界定「当日」→ 标缺（调用方按缺失降级，不冒充零）。
        return None
    window_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=2)

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, DEFAULT_LEDGER_ACCOUNT_KEY)
        if build is None or not _build_is_fresh_for_et_date(build, str(et_date)):
            return None
        rows = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == int(build.id),
                PositionEpisode.account_key == str(build.account_key),
                PositionEpisode.lifecycle_status == "closed",
                PositionEpisode.closed_at.isnot(None),
                PositionEpisode.closed_at >= window_start,
                PositionEpisode.closed_at < window_end,
            )
        ).scalars()
        total = 0.0
        for row in rows:
            if row.realized_pnl_net is None or row.closed_at is None:
                continue
            if _et_day(row.closed_at) != str(et_date):
                continue
            total += float(row.realized_pnl_net)
        return total
