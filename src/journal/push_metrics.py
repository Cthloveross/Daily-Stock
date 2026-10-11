# -*- coding: utf-8 -*-
"""推送台账 × 回合台账的机械关联（蓝图 17 Phase B）。

两个消费面：

* 过程指标 1「推送依从率」（日终复盘 S2）：今日新开回合里，进场前同日
  同标的已有已投递 P0/P1 推送的比例；
* P0 passed 率（滚动 20 条，仅复盘展示）：已投递严格 2m P0 中同日
  同标的**没有**跟随新开回合的比例——这是 acted/passed 的**机械代理**，
  不是 S1 人工清账（acted/passed/missed 人工标注是后续交付）。

诚实边界（两个消费面原样携带，测试锁定）：机械关联只证明「行动相关」，
不证明因果——无法区分「看到推送后进场」与「本来就要进场」；也无法识别
「打了推送以外的标的但灵感来自推送」。缺台账即标缺，绝不冒充。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.journal.ledger.push_ledger_repository import (
    StoredPushEvent,
    list_push_events,
)
from src.services.alert_tiering import (
    P0_AUTO_TIGHTEN_ENABLED,
    P0_PASS_RATE_TIGHTEN_THRESHOLD,
    P0_PASS_RATE_WINDOW,
)

__all__ = [
    "PUSH_JOIN_LIMITATIONS",
    "P0PassRateResult",
    "PushComplianceResult",
    "compute_p0_pass_rate",
    "compute_push_compliance",
]

_ET = ZoneInfo("America/New_York")
_TELEGRAM_CHANNELS = {"telegram", "telegram_silent"}

PUSH_JOIN_LIMITATIONS: tuple[str, ...] = (
    "机械关联（同 ET 日 + 同标的 + 推送时刻不晚于开仓时刻）：只证明行动"
    "相关，不证明因果——无法区分「看到推送后进场」与「本来就要进场」",
    "「已投递」按层定义：P0/P1 都是 Telegram 层，必须存在 telegram_ok "
    "的 delivery 行才算投递（decision 行只证明「决定发」，不证明送达）",
    "P0 passed 率是机械代理（无同日跟随回合＝passed），不是 S1 人工"
    "acted/passed/missed 清账；人工清账落地后以人工值为准",
    "推送台账自 Phase B 起记账；此前日期无台账＝标缺，不回填",
)


@dataclass(frozen=True)
class PushComplianceResult:
    """Metric-1 assembly for one ET date."""

    status: str  # ready | missing
    reason: Optional[str]
    ratio: Optional[float]
    pushed_episodes: int
    total_episodes: int
    pushes_total: int
    pushes_followed: int
    limitations: tuple[str, ...] = PUSH_JOIN_LIMITATIONS


@dataclass(frozen=True)
class P0PassRateResult:
    """Trailing mechanical strict-2m P0 passed-rate (display only)."""

    status: str  # ready | missing
    reason: Optional[str]
    window: int
    delivered: int
    passed: int
    rate: Optional[float]
    threshold: float = P0_PASS_RATE_TIGHTEN_THRESHOLD
    auto_tighten_enabled: bool = P0_AUTO_TIGHTEN_ENABLED
    limitations: tuple[str, ...] = PUSH_JOIN_LIMITATIONS


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _et_day(value: datetime) -> str:
    return _utc(value).astimezone(_ET).strftime("%Y-%m-%d")


def _next_et_midnight_utc(value: datetime) -> datetime:
    """UTC instant immediately after ``value``'s ET calendar day."""

    next_day = _utc(value).astimezone(_ET).date() + timedelta(days=1)
    return datetime(
        next_day.year,
        next_day.month,
        next_day.day,
        tzinfo=_ET,
    ).astimezone(timezone.utc)


def _delivered(events: Sequence[StoredPushEvent], tiers: set[str]) -> list[
    StoredPushEvent
]:
    """已投递的 P0/P1 推送（诚实口径，D8 修正）。

    「已投递」按层定义：P0/P1 都是 Telegram 层，一条 decision 行只有在同
    ``message_key`` 存在 ``telegram_ok=true`` 的 delivery 行时才算已投递
    ——decision 行证明「决定发」，delivery 行才证明「送达」。没有
    message_key、发送失败（telegram_ok=false）或 delivery 行缺失的决定
    一律不计（宁少不虚）。P2（web-only）本就不属于本指标。
    """
    first_success_at: dict[str, datetime] = {}
    for event in events:
        if (
            event.record_kind != "delivery"
            or event.telegram_ok is not True
            or not event.message_key
        ):
            continue
        delivered_at = _utc(event.ts)
        previous = first_success_at.get(event.message_key)
        if previous is None or delivered_at < previous:
            first_success_at[event.message_key] = delivered_at
    return [
        # Downstream temporal joins intentionally read ``event.ts``.  Replace
        # the decision timestamp with the first successful delivery timestamp:
        # a decision made before an episode but delivered after it was not an
        # alert the user could have followed.
        replace(event, ts=first_success_at[event.message_key])
        for event in events
        if event.record_kind == "decision"
        and event.tier in tiers
        and not event.suppressed
        and event.delivery_channel in _TELEGRAM_CHANNELS
        and event.ticker
        and event.message_key in first_success_at
    ]


def compute_push_compliance(
    et_date: str,
    opened_episodes: Sequence[tuple[str, Optional[datetime]]],
) -> PushComplianceResult:
    """过程指标 1：今日新开回合的推送依从率。

    ``opened_episodes``＝今日新开回合的 ``(underlying, opened_at)``。判定：
    该回合的 underlying 当日存在**开仓前**已投递（送达确证，见
    :func:`_delivered`）的 P0/P1 推送 → 计入分子（opened_at 缺失时放宽为
    同日存在推送——如实进 reason）。``pushes_followed`` 按**回合侧去重**：
    一笔回合至多满足一条推送（按推送时刻先到先配），同标的 P0+P1 两条
    推送不会因同一笔回合各记一次跟随；逐推送的原始命中数不再另报。
    台账当日无任何 decision 行＝提示器未运行 → 标缺，绝不把「没记账」
    说成「全 freelance」。
    """
    try:
        events = list_push_events(et_date)
    except Exception as exc:  # noqa: BLE001 - metric must not 500 the flow
        return PushComplianceResult(
            status="missing",
            reason=f"推送台账读取失败（{type(exc).__name__}）",
            ratio=None,
            pushed_episodes=0,
            total_episodes=len(opened_episodes),
            pushes_total=0,
            pushes_followed=0,
        )
    if not any(event.record_kind == "decision" for event in events):
        return PushComplianceResult(
            status="missing",
            reason="推送台账当日无记录（提示器未运行或未启用）；标缺不冒充",
            ratio=None,
            pushed_episodes=0,
            total_episodes=len(opened_episodes),
            pushes_total=0,
            pushes_followed=0,
        )
    pushes = _delivered(events, {"P0", "P1"})
    earliest_push: dict[str, datetime] = {}
    for event in pushes:
        ticker = str(event.ticker).strip().upper()
        moment = _utc(event.ts)
        if ticker not in earliest_push or moment < earliest_push[ticker]:
            earliest_push[ticker] = moment

    total = len(opened_episodes)
    if total == 0:
        return PushComplianceResult(
            status="missing",
            reason=(
                f"今日无新开回合（已投递 P0/P1 推送 {len(pushes)} 条；"
                "acted/passed 语义待 S1 人工清账）"
            ),
            ratio=None,
            pushed_episodes=0,
            total_episodes=0,
            pushes_total=len(pushes),
            pushes_followed=0,
        )

    pushed_episodes = 0
    imprecise_joins = 0
    for underlying, opened_at in opened_episodes:
        symbol = str(underlying or "").strip().upper()
        push_at = earliest_push.get(symbol)
        if push_at is None:
            continue
        if opened_at is None:
            pushed_episodes += 1
            imprecise_joins += 1
        elif push_at <= _utc(opened_at):
            pushed_episodes += 1

    # 回合侧去重：一笔回合至多配一条推送（推送按时刻升序，先到先配）。
    followed = 0
    used_episode_indices: set[int] = set()
    for event in sorted(pushes, key=lambda item: _utc(item.ts)):
        ticker = str(event.ticker).strip().upper()
        moment = _utc(event.ts)
        for index, (underlying, opened_at) in enumerate(opened_episodes):
            if index in used_episode_indices:
                continue
            if str(underlying or "").strip().upper() != ticker:
                continue
            if opened_at is None or _utc(opened_at) >= moment:
                used_episode_indices.add(index)
                followed += 1
                break

    reason = (
        f"已投递（送达确证）P0/P1 推送 {len(pushes)} 条，其中 {followed} 条"
        "同日同标的有跟随新开回合（回合侧去重的机械关联，非因果）"
    )
    if imprecise_joins:
        reason += f"；{imprecise_joins} 笔回合缺开仓时刻，放宽为同日关联"
    return PushComplianceResult(
        status="ready",
        reason=reason,
        ratio=pushed_episodes / total,
        pushed_episodes=pushed_episodes,
        total_episodes=total,
        pushes_total=len(pushes),
        pushes_followed=followed,
    )


#: 窗口回填的分批上限（有界迭代护栏，D8）：最多扫 8 批 × max(50, 2×窗口)
#: 行；再多说明台账历史远超窗口所需，按已取到的实际窗口如实报告。
_P0_WINDOW_FETCH_MAX_BATCHES = 8


def _recent_delivered_p0(
    limit: int,
    *,
    as_of_et_date: Optional[str] = None,
) -> list[StoredPushEvent]:
    """最近 ``limit`` 条已投递 P0（keyset 分批回扫，窗口填满或历史扫尽为止）。

    decision 行只有在相同 ``message_key`` 存在 ``telegram_ok=true`` 的
    delivery 行时才进入窗口；失败或孤儿 decision 不能冒充「已投递」。
    分页仍按 decision id 做 keyset，每批只核验该批候选 key，保持查询有界。

    旧实现单次取 ``limit*2`` 行再过滤，行内非 telegram/无 ticker 的比例
    一高，窗口就悄悄取不满而历史里明明还有——D8 修正为有界循环：按 id
    降序 keyset 翻页，直到凑满窗口、历史扫尽或达到批次上限。
    """
    from src.journal.ledger.push_models import PushEvent
    from src.journal.ledger.repository import init_ledger_schema
    from src.journal.ledger.push_ledger_repository import _stored
    from src.storage import get_db

    target = max(1, int(limit))
    batch_size = max(50, target * 2)
    init_ledger_schema()
    db = get_db()
    delivered: list[StoredPushEvent] = []
    last_id: Optional[int] = None
    with db.session_scope() as session:
        for _ in range(_P0_WINDOW_FETCH_MAX_BATCHES):
            query = select(PushEvent).where(
                PushEvent.record_kind == "decision",
                PushEvent.tier == "P0",
                # Never mix retired seven-gate / 5m decisions into the new
                # strict 2m cohort.  The immutable rule id is authoritative.
                PushEvent.rule == "p0_2m_candidate",
                PushEvent.suppressed == False,  # noqa: E712 - SQL comparison
            )
            if as_of_et_date is not None:
                query = query.where(PushEvent.et_date <= as_of_et_date)
            if last_id is not None:
                query = query.where(PushEvent.id < last_id)
            rows = (
                session.execute(
                    query.order_by(PushEvent.id.desc()).limit(batch_size)
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            last_id = int(rows[-1].id)
            candidates = [
                event
                for event in (_stored(row) for row in rows)
                if event.delivery_channel in _TELEGRAM_CHANNELS
                and event.ticker
                and event.message_key
            ]
            message_pairs = {
                (event.et_date, event.message_key) for event in candidates
            }
            first_success_at: dict[tuple[str, str], datetime] = {}
            if message_pairs:
                message_keys = {
                    message_key for _et_date, message_key in message_pairs
                }
                delivery_query = select(
                    PushEvent.et_date,
                    PushEvent.message_key,
                    PushEvent.ts,
                ).where(
                    PushEvent.record_kind == "delivery",
                    PushEvent.telegram_ok == True,  # noqa: E712
                    PushEvent.message_key.in_(message_keys),
                )
                if as_of_et_date is not None:
                    delivery_query = delivery_query.where(
                        PushEvent.et_date <= as_of_et_date
                    )
                for row_et_date, message_key, delivered_at in (
                    session.execute(delivery_query).all()
                ):
                    if not message_key:
                        continue
                    key = (str(row_et_date), str(message_key))
                    if key not in message_pairs:
                        continue
                    moment = _utc(delivered_at)
                    previous = first_success_at.get(key)
                    if previous is None or moment < previous:
                        first_success_at[key] = moment
            delivered.extend(
                replace(
                    event,
                    ts=first_success_at[(event.et_date, event.message_key)],
                )
                for event in candidates
                if (event.et_date, event.message_key) in first_success_at
            )
            if len(delivered) >= target or len(rows) < batch_size:
                break
    return delivered[:target]


def compute_p0_pass_rate(
    *,
    window: int = P0_PASS_RATE_WINDOW,
    account_key: Optional[str] = None,
    as_of_et_date: Optional[str] = None,
) -> P0PassRateResult:
    """滚动 P0 passed 率（机械代理）：已投递 P0 无同日跟随回合＝passed。

    展示与记账用；**不**自动收紧 P95 / 1.5x / 0.50 ATR 冻结阈值。
    """
    from src.journal.ledger.episode_repository import _latest_build
    from src.journal.ledger.models import PositionEpisode
    from src.journal.ledger.repository import (
        DEFAULT_LEDGER_ACCOUNT_KEY,
        init_ledger_schema,
    )
    from src.storage import get_db

    if as_of_et_date is not None:
        try:
            date.fromisoformat(as_of_et_date)
        except (TypeError, ValueError):
            return P0PassRateResult(
                status="missing",
                reason="P0 passed 率的 as_of_et_date 不是有效 YYYY-MM-DD 日期",
                window=window,
                delivered=0,
                passed=0,
                rate=None,
            )
    try:
        delivered = _recent_delivered_p0(
            window,
            as_of_et_date=as_of_et_date,
        )
    except Exception as exc:  # noqa: BLE001 - display metric must not raise
        return P0PassRateResult(
            status="missing",
            reason=f"推送台账读取失败（{type(exc).__name__}）",
            window=window,
            delivered=0,
            passed=0,
            rate=None,
        )
    if not delivered:
        return P0PassRateResult(
            status="missing",
            reason="尚无严格 2m 四条件并成功送达的 RTH P0"
            "（发送预算 ≤2/日、滚动 7 日 ≤7；缺失条件见今日提示台账）",
            window=window,
            delivered=0,
            passed=0,
            rate=None,
        )

    resolved_account = account_key or DEFAULT_LEDGER_ACCOUNT_KEY
    init_ledger_schema()
    db = get_db()
    opened: list[tuple[str, str, datetime]] = []  # (underlying, et_day, at)
    with db.session_scope() as session:
        build = _latest_build(session, resolved_account)
        if build is None:
            return P0PassRateResult(
                status="missing",
                reason=(
                    "回合台账无 EpisodeBuild，无法判断已投递 P0 是否有同日"
                    "跟随回合；标缺不冒充 passed"
                ),
                window=window,
                delivered=len(delivered),
                passed=0,
                rate=None,
            )

        # A P0 is classified as ``passed`` only after the episode build covers
        # the rest of that alert's ET calendar day.  A merely post-alert cutoff
        # can still miss a later same-day entry and would create a false pass.
        required_cutoff = max(_next_et_midnight_utc(event.ts) for event in delivered)
        source_cutoff = _utc(build.source_cutoff_at)
        if source_cutoff < required_cutoff:
            return P0PassRateResult(
                status="missing",
                reason=(
                    "最新 EpisodeBuild 的 source_cutoff_at 尚未覆盖已投递 P0 "
                    f"所在 ET 日（需覆盖至 {required_cutoff.isoformat()}）；"
                    "标缺不冒充 passed"
                ),
                window=window,
                delivered=len(delivered),
                passed=0,
                rate=None,
            )

        rows = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == int(build.id),
                PositionEpisode.account_key == str(build.account_key),
                PositionEpisode.opened_at.isnot(None),
            )
        ).scalars()
        for row in rows:
            opened_at = _utc(row.opened_at)
            opened.append(
                (
                    str(row.underlying).strip().upper(),
                    _et_day(opened_at),
                    opened_at,
                )
            )

    passed = 0
    for event in delivered:
        ticker = str(event.ticker).strip().upper()
        moment = _utc(event.ts)
        followed = any(
            underlying == ticker
            and et_day == event.et_date
            and opened_at >= moment
            for underlying, et_day, opened_at in opened
        )
        if not followed:
            passed += 1
    # 实际窗口如实报告（D8）：历史不足目标窗口时不冒充满窗统计。
    reason = (
        None
        if len(delivered) >= window
        else (
            f"滚动窗口目标 {window} 条，台账历史仅有 {len(delivered)} 条"
            f"已投递 P0——实际窗口 {len(delivered)} 条"
        )
    )
    return P0PassRateResult(
        status="ready",
        reason=reason,
        window=window,
        delivered=len(delivered),
        passed=passed,
        rate=passed / len(delivered),
    )
