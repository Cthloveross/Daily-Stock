# -*- coding: utf-8 -*-
"""推送台账只读端点（蓝图 17 §2.2 Phase B）。

Exposes:
    GET /api/v1/journal/push-ledger/daily

「今日提示流」（/intraday 页面）与疲劳仪表的唯一数据源：逐条分级决定
（含被抑制的候选与原因——无静默丢弃）、四级硬预算用量、P0 passed 率
（机械代理，仅展示；自动收紧闸在代码常量后，见 alert_tiering）。只读。
"""
from __future__ import annotations

import logging
import re
from datetime import date as date_type, datetime, timezone
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.journal_push import (
    P0PassRateModel,
    PushBudgetModel,
    PushLedgerDailyResponse,
    PushLedgerEventModel,
    PushDeliveryStatus,
)
from src.journal.ledger.push_ledger_repository import (
    count_delivered_p0_since,
    count_delivered_p0x_since,
    count_delivered_p0_2m_shadow_research_since,
    list_push_events,
    load_push_day_state,
    StoredPushEvent,
)
from src.journal.ledger.telegram_outbox_repository import (
    TelegramOutboxItem,
    list_telegram_outbox_for_date,
)
from src.journal.push_metrics import compute_p0_pass_rate
from src.services.alert_tiering import (
    OPENING_ALERT_DAILY_BUDGET,
    P0_DAILY_BUDGET,
    P0_WEEKLY_BUDGET,
    P0X_DAILY_BUDGET_PER_SESSION,
    P0X_WEEKLY_BUDGET,
    P1_DAILY_BUDGET,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ET = ZoneInfo("America/New_York")

_FEED_LIMITATIONS = [
    "推送台账是 append-only 记账：每条分级决定（含被抑制/降级候选及原因，"
    "无静默丢弃）与 Telegram 发送结果各自成行，绝不改写",
    "普通 P2 观察级只在本页出现、不推 Telegram；例外是已成立 P0 的异步期权补充，"
    "以及显式开启的未校准 strict-2m 研究响铃；二者均不改变 underlying P0 或正式指标；"
    "P3 仅入库、不在用户主 feed 展示",
    "P1-O 顺序开盘候选属于 P1 发现层但使用 Telegram 响铃：09:32 最早双确认，"
    "09:35 也只使用五根严格完整 1 分钟累计证据；是否响铃以 delivery_channel 为准，且使用独立"
    "每日 3 票预算",
    "P0/P0-X 是异常事实分级；P0-X 仅表示盘前/盘后正股异动，不声称期权可执行。"
    "预算只控制 Telegram 发送，不改变信号是否成立",
    "P0 passed 率是机械代理（无同日跟随回合＝passed），非人工 acted/passed/"
    "missed 清账；阈值自动收紧当前关闭（代码常量），任何收紧需人工决定",
    "事实描述，非买卖信号；系统只读、不下单",
    "发送状态按 message_key 关联既有回执与 outbox；Telegram 已接收不等于手机"
    "响铃或已读；无确定回执的终止/过期任务仅标结果未确认",
]


def _p0_2m_evidence(
    conditions: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Extract the immutable strict-2m evidence into a typed API field."""

    for condition in conditions:
        if str(condition.get("condition_id") or "") != "p0_2m_evidence":
            continue
        evidence = condition.get("evidence")
        if isinstance(evidence, Mapping):
            return evidence
    return None


def _delivery_status(
    event: StoredPushEvent,
    outcomes: Mapping[str, set[bool]],
    outbox: Mapping[str, TelegramOutboxItem],
    now: datetime,
) -> PushDeliveryStatus:
    """Project delivery evidence without rewriting append-only decisions."""

    if (
        event.suppressed
        or event.delivery_channel not in {"telegram", "telegram_silent"}
        or not event.message_key
    ):
        return "not_requested"
    receipts = outcomes.get(event.message_key, set())
    job = outbox.get(event.message_key)
    if True in receipts or (job is not None and job.state == "succeeded"):
        return "succeeded"
    if job is not None and job.state == "pending":
        return "pending" if job.expires_at > now else "expired_unconfirmed"
    if False in receipts:
        return "failed"
    # A terminal job with no receipt may have sent the request, or may have
    # stopped before sending.  Neither phone receipt nor failure can be inferred.
    return "indeterminate"


@router.get("/push-ledger/daily", response_model=PushLedgerDailyResponse)
def get_push_ledger_daily(
    date: Optional[str] = Query(None, description="ET 日期 YYYY-MM-DD，缺省今天"),
) -> PushLedgerDailyResponse:
    """一天的推送台账全量（今日提示流 + 预算仪表 + P0 passed 率）。"""
    et_date = date or datetime.now(_ET).strftime("%Y-%m-%d")
    if not _ISO_DATE.fullmatch(et_date):
        raise HTTPException(status_code=422, detail="date 必须是 YYYY-MM-DD")
    try:
        date_type.fromisoformat(et_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="date 必须是真实存在的 YYYY-MM-DD 日历日期",
        ) from exc

    events = list_push_events(et_date)
    day_state = load_push_day_state(et_date)
    p0_week = count_delivered_p0_since(et_date, days=7)
    p0x_week = count_delivered_p0x_since(et_date, days=7)
    pass_rate = compute_p0_pass_rate(as_of_et_date=et_date)
    delivery_outcomes: dict[str, set[bool]] = {}
    for event in events:
        if (
            event.record_kind == "delivery"
            and event.message_key
            and event.telegram_ok is not None
        ):
            delivery_outcomes.setdefault(event.message_key, set()).add(
                event.telegram_ok
            )
    outbox = {
        item.message_key: item
        for item in list_telegram_outbox_for_date(et_date)
    }
    projection_time = datetime.now(timezone.utc)

    try:
        from src.config import get_config

        telegram_cap = int(
            getattr(get_config(), "intraday_alerts_max_per_day", 20) or 20
        )
    except Exception:  # noqa: BLE001 - display-only bound
        telegram_cap = 20

    return PushLedgerDailyResponse(
        et_date=et_date,
        events=[
            PushLedgerEventModel(
                push_id=event.push_id,
                record_kind=event.record_kind,  # type: ignore[arg-type]
                et_date=event.et_date,
                ts=event.ts.isoformat(),
                tier=event.tier,  # type: ignore[arg-type]
                session_scope=event.session_scope,  # type: ignore[arg-type]
                rule=event.rule,
                ticker=event.ticker,
                text=event.text,
                delivery_channel=event.delivery_channel,  # type: ignore[arg-type]
                suppressed=event.suppressed,
                suppressed_reason=event.suppressed_reason,
                gate_state=event.gate_state,
                lane_rule_id=event.lane_rule_id,
                option_mid=event.option_mid,
                option_spread_pct=event.option_spread_pct,
                message_key=event.message_key,
                telegram_ok=event.telegram_ok,
                delivery_status=_delivery_status(
                    event, delivery_outcomes, outbox, projection_time
                ),
                conditions=list(event.conditions),
                p0_signal_evidence_v2=_p0_2m_evidence(event.conditions),
            )
            for event in events
        ],
        budgets=PushBudgetModel(
            opening_used_today=day_state.opening_delivered,
            opening_daily_budget=OPENING_ALERT_DAILY_BUDGET,
            p0_used_today=day_state.p0_delivered,
            p0_daily_budget=P0_DAILY_BUDGET,
            p0_used_week=p0_week,
            p0_weekly_budget=P0_WEEKLY_BUDGET,
            p0x_used_today_by_session=dict(
                day_state.p0x_delivered_by_session
            ),
            p0x_daily_budget_by_session={
                "premarket": P0X_DAILY_BUDGET_PER_SESSION,
                "regular": 0,
                "afterhours": P0X_DAILY_BUDGET_PER_SESSION,
            },
            p0x_used_week=p0x_week,
            p0x_weekly_budget=P0X_WEEKLY_BUDGET,
            p1_used_today=day_state.p1_delivered,
            p1_daily_budget=P1_DAILY_BUDGET,
            telegram_lines_used=day_state.telegram_alert_count,
            telegram_lines_cap=telegram_cap,
            research_rth_used_today=day_state.p0_2m_shadow_research_delivered,
            research_rth_daily_budget=P0_DAILY_BUDGET,
            research_rth_used_week=count_delivered_p0_2m_shadow_research_since(
                et_date, session_scope="regular", days=7
            ),
            research_rth_weekly_budget=P0_WEEKLY_BUDGET,
            research_extended_used_today_by_session=dict(
                day_state.p0x_2m_shadow_research_delivered_by_session
            ),
            research_extended_daily_budget_by_session={
                "premarket": P0X_DAILY_BUDGET_PER_SESSION,
                "regular": 0,
                "afterhours": P0X_DAILY_BUDGET_PER_SESSION,
            },
            research_extended_used_week=count_delivered_p0_2m_shadow_research_since(
                et_date, session_scope="extended", days=7
            ),
            research_extended_weekly_budget=P0X_WEEKLY_BUDGET,
        ),
        p0_pass_rate=P0PassRateModel(
            status=pass_rate.status,  # type: ignore[arg-type]
            reason=pass_rate.reason,
            window=pass_rate.window,
            delivered=pass_rate.delivered,
            passed=pass_rate.passed,
            rate=pass_rate.rate,
            threshold=pass_rate.threshold,
            auto_tighten_enabled=pass_rate.auto_tighten_enabled,
            limitations=list(pass_rate.limitations),
        ),
        limitations=list(_FEED_LIMITATIONS),
    )
