# -*- coding: utf-8 -*-
"""推送台账仓库（蓝图 17 §三(e)-4，Phase B）：append-only 读写。

写入方是提示器的 worker 线程（dispatch 前落库，蓝图 §三(d)）；读取方是
今日提示流 API、日终复盘的过程指标 1、以及提示器重启后的去重状态重建。
所有行不可改写（deny trigger）；发送结果以 ``record_kind='delivery'`` 的
第二行追加。
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select

from src.journal.ledger.push_models import PushEvent
from src.journal.ledger.repository import init_ledger_schema
from src.storage import get_db

__all__ = [
    "PushDayState",
    "PushDecisionInput",
    "PushDeliveryInput",
    "PushLedgerError",
    "P0_2M_SHADOW_RESEARCH_RULE",
    "StoredPushEvent",
    "append_push_events",
    "count_delivered_p0_since",
    "count_delivered_p0_2m_shadow_research_since",
    "count_delivered_p0x_since",
    "list_push_events",
    "load_push_day_state",
    "new_push_id",
]

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_TIERS = {"P0X", "P0", "P1", "P2", "P3", "system"}
_VALID_SESSION_SCOPES = {"premarket", "regular", "afterhours"}
_P0X_SESSION_SCOPES = {"premarket", "afterhours"}
_VALID_CHANNELS = {
    "telegram",
    "telegram_silent",
    "web_only",
    "ledger_only",
    "none",
}
_TELEGRAM_CHANNELS = {"telegram", "telegram_silent"}
# Risk-control notices must be delivered even after the opportunity budgets
# are full.  They still rebuild dedupe state, but never consume P1/global
# opportunity capacity after a process restart.
_SAFETY_EXEMPT_RULES = {"r6_fuse_notice", "same_day_flatten_reminder"}
_OPENING_PRIMARY_RULES = {
    "opening_impulse_v1",
    "opening_impulse_v2",
    "opening_impulse_sequential_v1",
}
# P1-O 的静默合约补充占全局 Telegram 行数（防骚扰），但不占普通 P1
# near-miss 预算。重启重建必须与同一进程内的发送时计数保持一致。
_P1_BUDGET_EXEMPT_RULES = {"opening_option_update"}
P0_2M_SHADOW_RESEARCH_RULE = "p0_2m_shadow_research"


class PushLedgerError(ValueError):
    """Raised when a push-ledger payload violates the persistence contract."""


def new_push_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class PushDecisionInput:
    """One alert decision destined for an immutable ledger row."""

    push_id: str
    et_date: str
    ts: datetime
    tier: str
    rule: str
    ticker: Optional[str]
    text: str
    delivery_channel: str
    dedupe_key: tuple[str, ...] = ()
    conditions: Optional[Sequence[dict[str, Any]]] = None
    lane_rule_id: Optional[str] = None
    playbook_ref: Optional[str] = None
    option_mid: Optional[float] = None
    option_spread_pct: Optional[float] = None
    gate_state: Optional[str] = None
    suppressed: bool = False
    suppressed_reason: Optional[str] = None
    message_key: Optional[str] = None
    session_scope: Optional[str] = None


@dataclass(frozen=True)
class PushDeliveryInput:
    """One Telegram delivery outcome row (references decisions by message)."""

    push_id: str
    et_date: str
    ts: datetime
    tier: str
    message_key: str
    telegram_ok: bool
    text: str = ""
    channel: str = "telegram"


@dataclass(frozen=True)
class StoredPushEvent:
    event_id: int
    push_id: str
    record_kind: str
    et_date: str
    ts: datetime
    tier: str
    rule: str
    ticker: Optional[str]
    text: str
    dedupe_key: tuple[str, ...]
    conditions: tuple[dict[str, Any], ...]
    lane_rule_id: Optional[str]
    playbook_ref: Optional[str]
    option_mid: Optional[float]
    option_spread_pct: Optional[float]
    gate_state: Optional[str]
    delivery_channel: str
    suppressed: bool
    suppressed_reason: Optional[str]
    message_key: Optional[str]
    telegram_ok: Optional[bool]
    created_at: datetime
    session_scope: Optional[str] = None


@dataclass
class PushDayState:
    """Rebuilt in-process state for one ET day (restart-safe dedupe)."""

    seen_keys: set[tuple[str, ...]] = field(default_factory=set)
    telegram_alert_count: int = 0
    p0_delivered: int = 0
    p1_delivered: int = 0
    opening_delivered: int = 0
    # P0X 是盘外独立预算，绝不并入旧 P0 计数。三键固定初始化可让调用方
    # 无条件读取；模型约束保证 regular 始终为 0。
    p0x_delivered: int = 0
    p0x_delivered_by_session: dict[str, int] = field(
        default_factory=lambda: {
            "premarket": 0,
            "regular": 0,
            "afterhours": 0,
        }
    )
    # 未校准研究提醒使用独立预算。它们以 tier=P2、独立 rule 入账，绝不
    # 混入正式 P0/P0X 或 passed-rate 的计数。
    p0_2m_shadow_research_delivered: int = 0
    p0x_2m_shadow_research_delivered_by_session: dict[str, int] = field(
        default_factory=lambda: {
            "premarket": 0,
            "regular": 0,
            "afterhours": 0,
        }
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_date(et_date: str) -> str:
    if not _ISO_DATE.fullmatch(str(et_date or "")):
        raise PushLedgerError("et_date must be an ET calendar date (YYYY-MM-DD)")
    return str(et_date)


def _stored(row: PushEvent) -> StoredPushEvent:
    def _parse_list(raw: Any) -> tuple[Any, ...]:
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            return ()
        return tuple(parsed) if isinstance(parsed, list) else ()

    dedupe = tuple(
        str(item) for item in _parse_list(row.dedupe_key_json or "[]")
    )
    conditions = tuple(
        item
        for item in _parse_list(row.conditions_json or "[]")
        if isinstance(item, dict)
    )
    return StoredPushEvent(
        event_id=int(row.id),
        push_id=str(row.push_id),
        record_kind=str(row.record_kind),
        et_date=str(row.et_date),
        ts=_utc(row.ts),
        tier=str(row.tier),
        session_scope=(
            str(row.session_scope) if row.session_scope is not None else None
        ),
        rule=str(row.rule),
        ticker=str(row.ticker) if row.ticker is not None else None,
        text=str(row.text),
        dedupe_key=dedupe,
        conditions=conditions,
        lane_rule_id=(
            str(row.lane_rule_id) if row.lane_rule_id is not None else None
        ),
        playbook_ref=(
            str(row.playbook_ref) if row.playbook_ref is not None else None
        ),
        option_mid=float(row.option_mid) if row.option_mid is not None else None,
        option_spread_pct=(
            float(row.option_spread_pct)
            if row.option_spread_pct is not None
            else None
        ),
        gate_state=str(row.gate_state) if row.gate_state is not None else None,
        delivery_channel=str(row.delivery_channel),
        suppressed=bool(row.suppressed),
        suppressed_reason=(
            str(row.suppressed_reason)
            if row.suppressed_reason is not None
            else None
        ),
        message_key=(
            str(row.message_key) if row.message_key is not None else None
        ),
        telegram_ok=(
            bool(row.telegram_ok) if row.telegram_ok is not None else None
        ),
        created_at=_utc(row.created_at),
    )


def _prepare_push_event_rows(
    decisions: Iterable[PushDecisionInput] = (),
    deliveries: Iterable[PushDeliveryInput] = (),
) -> list[PushEvent]:
    """Validate inputs and build ORM rows without opening a transaction.

    校验即拒绝（绝不静默修正）：tier/channel 必须在枚举内；被抑制行必须
    带原因（表级 CheckConstraint 双保险）。
    """
    rows: list[PushEvent] = []
    for item in decisions:
        if item.tier not in _VALID_TIERS:
            raise PushLedgerError(f"unknown tier: {item.tier!r}")
        if (
            item.session_scope is not None
            and item.session_scope not in _VALID_SESSION_SCOPES
        ):
            raise PushLedgerError(
                f"unknown session_scope: {item.session_scope!r}"
            )
        if item.tier == "P0X" and item.session_scope not in _P0X_SESSION_SCOPES:
            raise PushLedgerError(
                "P0X decisions require session_scope='premarket' or "
                "'afterhours'"
            )
        if item.delivery_channel not in _VALID_CHANNELS:
            raise PushLedgerError(
                f"unknown delivery channel: {item.delivery_channel!r}"
            )
        if item.suppressed and not (item.suppressed_reason or "").strip():
            raise PushLedgerError(
                "suppressed decisions must carry a suppressed_reason"
            )
        rows.append(
            PushEvent(
                push_id=str(item.push_id),
                record_kind="decision",
                et_date=_require_date(item.et_date),
                ts=_utc(item.ts),
                tier=item.tier,
                session_scope=item.session_scope,
                rule=str(item.rule),
                ticker=item.ticker,
                text=str(item.text),
                dedupe_key_json=json.dumps(
                    [str(part) for part in item.dedupe_key],
                    ensure_ascii=False,
                ),
                conditions_json=(
                    json.dumps(list(item.conditions), ensure_ascii=False)
                    if item.conditions is not None
                    else None
                ),
                lane_rule_id=item.lane_rule_id,
                playbook_ref=item.playbook_ref,
                option_mid=item.option_mid,
                option_spread_pct=item.option_spread_pct,
                gate_state=item.gate_state,
                delivery_channel=item.delivery_channel,
                suppressed=bool(item.suppressed),
                suppressed_reason=item.suppressed_reason,
                message_key=item.message_key,
                telegram_ok=None,
            )
        )
    for outcome in deliveries:
        if outcome.tier not in _VALID_TIERS:
            raise PushLedgerError(f"unknown tier: {outcome.tier!r}")
        if outcome.channel not in _TELEGRAM_CHANNELS:
            raise PushLedgerError(
                f"delivery rows are telegram-only, got: {outcome.channel!r}"
            )
        rows.append(
            PushEvent(
                push_id=str(outcome.push_id),
                record_kind="delivery",
                et_date=_require_date(outcome.et_date),
                ts=_utc(outcome.ts),
                tier=outcome.tier,
                session_scope=None,
                rule="telegram_delivery",
                ticker=None,
                text=str(outcome.text),
                dedupe_key_json=None,
                conditions_json=None,
                delivery_channel=outcome.channel,
                suppressed=False,
                suppressed_reason=None,
                message_key=str(outcome.message_key),
                telegram_ok=bool(outcome.telegram_ok),
            )
        )
    return rows


def append_push_events(
    decisions: Iterable[PushDecisionInput] = (),
    deliveries: Iterable[PushDeliveryInput] = (),
) -> int:
    """Append decision/delivery rows in one transaction; returns row count."""

    rows = _prepare_push_event_rows(decisions, deliveries)
    if not rows:
        return 0
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        for row in rows:
            session.add(row)
        session.flush()
    return len(rows)


def list_push_events(
    et_date: str,
    *,
    record_kind: Optional[str] = None,
) -> tuple[StoredPushEvent, ...]:
    """All immutable rows of one ET date, oldest first."""
    _require_date(et_date)
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        query = select(PushEvent).where(PushEvent.et_date == et_date)
        if record_kind is not None:
            query = query.where(PushEvent.record_kind == record_kind)
        rows = session.execute(
            query.order_by(PushEvent.id.asc())
        ).scalars()
        return tuple(_stored(row) for row in rows)


def load_push_day_state(et_date: str) -> PushDayState:
    """重启后的当日状态重建（修复文档注明的「重启重复提示」缺陷）。

    每一条已落库的 decision 都对应一次真实入队，因此它们的去重键全部计入
    ``seen``（发送失败也不重投）。预算统计成功 delivery；若 decision 已持久化
    但没有任何 delivery 结果，也保守视为已占用——进程可能恰好在 Telegram
    成功后、delivery 追加前退出，释放它会使重启后的 P0/P0X/P1 预算超发。
    只有明确 ``telegram_ok=false`` 且没有成功结果时才释放预算。双窗确认的
    逐 tick 状态不重建——重启后重新要求连续两窗，宁严不宽（不确定降级）。
    """
    state = PushDayState()
    events = list_push_events(et_date)
    delivery_outcomes: dict[str, set[bool]] = {}
    for event in events:
        if event.record_kind != "delivery" or not event.message_key:
            continue
        if event.telegram_ok is None:
            continue
        delivery_outcomes.setdefault(event.message_key, set()).add(
            bool(event.telegram_ok)
        )
    for event in events:
        if event.record_kind != "decision":
            continue
        if event.dedupe_key:
            state.seen_keys.add(event.dedupe_key)
        if (
            event.suppressed
            or event.tier == "system"
            or event.rule in _SAFETY_EXEMPT_RULES
        ):
            continue
        outcomes = delivery_outcomes.get(event.message_key or "", set())
        budget_consumed = bool(
            event.message_key
            and (True in outcomes or not outcomes)
        )
        if event.delivery_channel in _TELEGRAM_CHANNELS and budget_consumed:
            state.telegram_alert_count += 1
            if event.rule == P0_2M_SHADOW_RESEARCH_RULE:
                if event.session_scope == "regular":
                    state.p0_2m_shadow_research_delivered += 1
                elif (
                    event.session_scope
                    in state.p0x_2m_shadow_research_delivered_by_session
                ):
                    state.p0x_2m_shadow_research_delivered_by_session[
                        event.session_scope
                    ] += 1
            elif event.tier == "P0X":
                state.p0x_delivered += 1
                if event.session_scope in state.p0x_delivered_by_session:
                    state.p0x_delivered_by_session[event.session_scope] += 1
            elif event.rule in _OPENING_PRIMARY_RULES:
                state.opening_delivered += 1
                if event.ticker:
                    option_state_key = (
                        "opening_option_update"
                        if event.option_mid is not None
                        else "opening_option_pending"
                    )
                    state.seen_keys.add((option_state_key, event.ticker))
            elif event.rule == "opening_option_update" and event.ticker:
                state.seen_keys.discard(
                    ("opening_option_pending", event.ticker)
                )
            elif event.tier == "P0":
                state.p0_delivered += 1
            elif (
                event.tier == "P1"
                and event.rule not in _P1_BUDGET_EXEMPT_RULES
            ):
                state.p1_delivered += 1
    return state


def _count_delivered_tier_since(
    et_date: str,
    *,
    tier: str,
    days: int,
    rule: Optional[str] = None,
    session_scopes: Optional[set[str]] = None,
) -> int:
    """Trailing budget count, including indeterminate post-send outcomes.

    A persisted Telegram decision with no delivery row is conservatively
    occupied.  This closes the crash window between an external send and the
    append-only delivery receipt; an explicit false delivery still releases
    the slot.
    """
    _require_date(et_date)
    window_days = max(0, int(days))
    if window_days == 0:
        return 0
    end = date.fromisoformat(et_date)
    start = (end - timedelta(days=window_days - 1)).isoformat()
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        delivery_outcomes: dict[tuple[str, str], set[bool]] = {}
        delivery_rows = session.execute(
            select(
                PushEvent.et_date,
                PushEvent.message_key,
                PushEvent.telegram_ok,
            ).where(
                PushEvent.record_kind == "delivery",
                PushEvent.et_date >= start,
                PushEvent.et_date <= et_date,
                PushEvent.message_key.isnot(None),
            )
        ).all()
        for row_et_date, message_key, telegram_ok in delivery_rows:
            if not message_key or telegram_ok is None:
                continue
            pair = (str(row_et_date), str(message_key))
            delivery_outcomes.setdefault(pair, set()).add(bool(telegram_ok))
        conditions = [
            PushEvent.record_kind == "decision",
            PushEvent.tier == tier,
            PushEvent.suppressed == False,  # noqa: E712 - SQL comparison
            PushEvent.et_date >= start,
            PushEvent.et_date <= et_date,
        ]
        if rule is not None:
            conditions.append(PushEvent.rule == rule)
        if session_scopes is not None:
            conditions.append(PushEvent.session_scope.in_(session_scopes))
        decisions = session.execute(
            select(PushEvent).where(*conditions)
        ).scalars()
        return sum(
            1
            for row in decisions
            if str(row.delivery_channel) in _TELEGRAM_CHANNELS
            and row.message_key
            and (
                True
                in delivery_outcomes.get(
                    (str(row.et_date), str(row.message_key)), set()
                )
                or (
                    str(row.et_date),
                    str(row.message_key),
                )
                not in delivery_outcomes
            )
        )


def count_delivered_p0_since(et_date: str, *, days: int = 7) -> int:
    """Trailing conservative budget count for regular-session P0."""
    return _count_delivered_tier_since(et_date, tier="P0", days=days)


def count_delivered_p0_2m_shadow_research_since(
    et_date: str,
    *,
    session_scope: str,
    days: int = 7,
) -> int:
    """Count the independent uncalibrated research-notification budget.

    ``session_scope='regular'`` is the RTH lane. ``'extended'`` combines the
    premarket and after-hours lanes for their shared weekly cap.  These rows
    are deliberately tier P2 with a distinct rule, so no formal P0/P0X query
    or passed-rate cohort can consume them.
    """

    if session_scope == "regular":
        scopes = {"regular"}
    elif session_scope == "extended":
        scopes = {"premarket", "afterhours"}
    else:
        raise PushLedgerError(
            "shadow research session_scope must be 'regular' or 'extended'"
        )
    return _count_delivered_tier_since(
        et_date,
        tier="P2",
        days=days,
        rule=P0_2M_SHADOW_RESEARCH_RULE,
        session_scopes=scopes,
    )


def count_delivered_p0x_since(et_date: str, *, days: int = 7) -> int:
    """Trailing conservative budget count for P0X decisions.

    P0X has an independent weekly budget.  Regular P0 decisions therefore do
    not contribute to this count (and vice versa).
    """
    return _count_delivered_tier_since(et_date, tier="P0X", days=days)
