# -*- coding: utf-8 -*-
"""盘中机会提示器：严格 2m P0/P0-X + 持久化推送台账。

Current contract (2026-09-10): P0/P0-X comes only from versioned strict 2m
evidence.  RTH P0 is capped at 2/day and 7/week; extended-hours P0-X is capped
at one per session/day and 5/week.  The old 5m burst/displacement fields are
legacy display data and cannot affect alerting, ranking, or tiering.  P1-O uses
completed 1m bars throughout, including its five-minute cumulative checkpoint.

当前分级出口：

* **P0/P0-X 标的事实**：四项严格 2m 条件成立；正式发送另需通过校准与
  前瞻 shadow 的 activation 证据。盘外当前只进入网页/研究提醒，不启用正式响铃；
* **P1-O 开盘异动**（Telegram 响铃，≤3 个候选/日）：09:32--09:34
  需要相邻两次同向顺序确认，09:35 用五根完整 1m 累计确认；它是待确认
  setup，不是 P0；
* **P1 风险/补充级**（Telegram 静默）：P1-O 的异步报价补充、真实收盘前
  30 分钟的同日平仓提醒与 R6 触线通告；安全通知不占普通机会预算。
  R6 按 ET 日锁存，触线或读数未知不改变标的事实，但关闭合约与行动性措辞；
* **P2 观察/研究级**：普通观察仅页面；显式开启的未校准 strict-2m 研究
  提醒使用独立手机预算，不冒充正式 P0 或已验证收益；
* **P3 日志级**（仅入库）：被抑制/降级的候选，全部带原因落台账——
  **无静默丢弃**（蓝图硬红线）；
* 系统通道（watchdog / ``notify_system``）：照旧 Telegram、不占任何预算。

推送台账（``journal_v2_push_events``，append-only）：每条分级决定 dispatch
前落库（蓝图 §三(d)），发送结果以 delivery 行追加。重启后当日去重状态从
台账重建；台账不可读时拒绝本次观察并保持可重试，不凭空重置去重或预算。

节奏合同：detection 不访问行情/期权网络；strict-2m observer 只有在 decision
与可恢复 outbox 原子提交后才确认 accepted，队列满或写入失败回滚本 tick
的去重与预算，使同一窗口仍可重检。其余台账写入与 Telegram 发送在独立
daemon 线程消化有界队列；每 ET 日首次观察还须恢复当日持久状态。上下文快照
（gate / 持仓 / 当日已实现盈亏）也只在该线程按 TTL 刷新，绝不在预热线程
取数。任何异常都不上抛。

诚实合同（不变）：只陈述载荷里已有的数字与其既有口径，不重算、不预测、
不给方向判断；每条消息末尾固定「事实描述，非买卖信号」；黑名单标的只
标注不压制；措辞禁用「聪明钱/主力/大资金」式归因（测试锁定）。
"""
from __future__ import annotations

import logging
import heapq
import queue
import threading
import time as time_module
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from src.opportunities.blacklist import (
    BLACKLIST_ALERT_TAG,
    BLACKLIST_TICKER_SET,
)
from src.opportunities.intraday_bursts import (
    DISPLACEMENT_ATR_BASIS_DAILY,
    DISPLACEMENT_SURVIVAL_LINE_ATR,
    LEG_MEDIUM_MIN_SCORE,
)
from src.journal.ledger.push_ledger_repository import (
    P0_2M_SHADOW_RESEARCH_RULE,
)
from src.services.alert_tiering import (
    INTRADAY_LANE_RULE_ID,
    OPENING_ALERT_DAILY_BUDGET,
    P0_DAILY_BUDGET,
    P0_WEEKLY_BUDGET,
    P0X_DAILY_BUDGET_PER_SESSION,
    P0X_WEEKLY_BUDGET,
    P1_DAILY_BUDGET,
    TierContext,
    TierEngineState,
    evaluate_p0_2m_conditions,
    intraday_execution_policy,
    opening_probe_direction,
    opening_probe_profile,
    opportunity_telegram_window,
    power_hour_window,
    resolve_fuse_state,
    same_day_exit_deadline_et,
)
from src.services.xnys_session_clock import resolve_xnys_session_clock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v1 提示阈值（documented heuristics，与扫描面板同源，不是验证过的边际）。
# ---------------------------------------------------------------------------
# 规则 1（盘前异动）：|pre_change_percent| ≥ 2.0（Moomoo 快照盘前口径，
# 常规字段在盘前仍指向上一常规时段——见 G-12，绝不用 change_percent 冒充）。
PREMARKET_ABS_CHANGE_MIN = 2.0
# 规则 2（放量爆发）：当前 15 分钟窗口 vol_norm ≥ 2.0 且爆发分 ≥ 中波段线
# （LEG_MEDIUM_MIN_SCORE=2.5，单一真源在 intraday_bursts，不另设平行常量）。
VOLUME_ALERT_VOL_NORM_MIN = 2.0
VOLUME_ALERT_MIN_SCORE = LEG_MEDIUM_MIN_SCORE
# 规则 4（位移达标）：|net_move_atr| 首次达到 ±0.5 ATR 参考线（v8 更正后它
# 只描述过去 30 分钟，不承载前向信息）；优先读载荷自带的 survival_line_atr。
DISPLACEMENT_ALERT_LINE_ATR = DISPLACEMENT_SURVIVAL_LINE_ATR
# 规则 6（盘前期权大单）：候选 option_activity.max_single_turnover ≥ $1M。
# 阈值是 v1 启发式圆整数（与 /intraday 盘前期权异常面板同值），不是验证过
# 的边际。盘前读到的异动页是上一时段的成交（盘前绝大多数期权无成交），
# 文案如实标注口径；文案不做资金身份归因（异动来源可能是对冲/库存流）。
PREMARKET_LARGE_PRINT_MIN_TURNOVER = 1_000_000.0

ALERT_FOOTER = "事实描述，非买卖信号"
DEFAULT_MAX_ALERTS_PER_DAY = 20
_QUEUE_MAXSIZE_DEFAULT = 8
_CONTEXT_REFRESH_SECONDS_DEFAULT = 300.0
_OPTION_SUPPLEMENT_QUEUE_MAXSIZE = 8
_DELIVERY_RETRY_QUEUE_MAXSIZE_DEFAULT = 32
# ``TelegramSender`` already performs three short HTTP attempts.  These are
# process-level retries for a longer transient outage; keep the whole window
# bounded because a day-trading alert becomes actively misleading when stale.
_DELIVERY_RETRY_DELAYS_SECONDS_DEFAULT = (15.0, 45.0, 120.0, 120.0)
# Strict intraday evidence ages quickly.  Keep a hard six-minute ceiling; in
# the ordinary failure path the four scheduled waits total five minutes.  A
# longer outage must not turn a stale crossing into an apparently live ring.
_DELIVERY_RETRY_TTL_SECONDS_DEFAULT = 6.0 * 60.0
_NEW_YORK = ZoneInfo("America/New_York")

_DIRECTION_LABELS = {"up": "向上", "down": "向下", "flat": "持平"}

#: 四级出口的消息头（P2/P3 不成消息，只入库/上页面）。
_TIER_HEADERS = {
    # P0 是严格标的信号事实，不是下单指令。R6 触发/标缺时
    # 仍会发送这条事实，因此消息头也不得暗示“行动”。
    "P0": "P0 标的信号",
    "P0X": "P0-X 盘外异动",
    "P1": "P1 预备级 · 静默",
    "P1O": "P1-O 开盘异动 · 待确认 · 响铃",
    "P1O_UPDATE": "P1-O 合约补充 · 静默",
    "P0_RESEARCH": "严格 2m 研究观察 · 尚未校准 · 不可据此行动 · 响铃",
}

# 这些风险通告不占机会提示预算；失败时也没有可释放的 reservation。
_SAFETY_EXEMPT_RULES = frozenset(
    {"r6_fuse_notice", "same_day_flatten_reminder"}
)
_OPENING_PRIMARY_RULES = frozenset(
    {
        "opening_impulse_v1",
        "opening_impulse_v2",
        "opening_impulse_sequential_v1",
    }
)
_OPENING_RULES = _OPENING_PRIMARY_RULES | {"opening_option_update"}
_OPENING_CANONICAL_DEDUPE_RULE = "opening_setup_v2"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


@dataclass(frozen=True)
class IntradayAlert:
    """One factual alert line, already fully rendered (blacklist tag included)."""

    rule: str
    ticker: Optional[str]
    text: str
    # 该条对应的去重键：队列满被丢弃时用于回滚 _seen，让事实下一 tick 可重检。
    dedupe_key: tuple[str, ...] = ()


def _tagged(ticker: str, text: str) -> str:
    if ticker in BLACKLIST_TICKER_SET:
        return f"{text} {BLACKLIST_ALERT_TAG}"
    return text


def _iter_premarket_rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """深度层候选在前（异动最强的标的已被晋升，宽层 snapshot_only 不再含它们；
    候选上的 ``pre_change_percent`` 是两层扫描从同一批快照原样带过来的），
    宽层剩余行在后（已按 |pre_change| 降序）。"""

    for candidate in payload.get("candidates") or ():
        if isinstance(candidate, Mapping):
            yield candidate
    universe_scan = payload.get("universe_scan")
    if isinstance(universe_scan, Mapping):
        for row in universe_scan.get("snapshot_only") or ():
            if isinstance(row, Mapping):
                yield row


def detect_intraday_alerts(
    payload: Mapping[str, Any],
    *,
    seen: set[tuple[str, ...]],
) -> list[IntradayAlert]:
    """兼容上下文事实检测；``seen`` 为当日去重键集合（就地登记）。

    这些事实是 **P2 观察级**（仅页面提示流 + 台账），不直推 Telegram。
    标有 ``session_bursts.legacy=true`` 的 5m burst/displacement 不再检测：
    它们只供 API/复盘兼容展示。其余检测
    只读载荷已有字段，数字与口径原样透传，不重算（唯一的乘法是位移美元
    换算 ``net_move_atr × atr14``，与前端扫描表同一显示口径，且仅在
    ``atr_basis == "atr14_daily"`` 时进行，其余标尺只报 ATR 值）。
    """

    alerts: list[IntradayAlert] = []
    session_state = str(payload.get("session_state") or "")
    quote_scope = str(payload.get("quote_session_scope") or "")
    candidates = [
        candidate
        for candidate in (payload.get("candidates") or ())
        if isinstance(candidate, Mapping)
    ]

    def _emit(key: tuple[str, ...], alert: IntradayAlert) -> None:
        if key in seen:
            return
        seen.add(key)
        alerts.append(replace(alert, dedupe_key=key))

    # -- 规则 5：0-7DTE 日内池可用性（policy_state 一旦不再 unknown）------
    # 深度层轮换可能让可用性盘中翻转，所以去重键包含具体值；值变化时发更正。
    # legacy day_type 只作为旧服务兼容输入，绝不再把“无 0DTE”解释成日内关闭。
    lane = payload.get("lane_availability")
    if isinstance(lane, Mapping):
        policy_state = str(lane.get("policy_state") or "")
        if not policy_state:
            readable_rows = [
                row
                for row in (lane.get("tickers") or ())
                if isinstance(row, Mapping) and row.get("state") == "ready"
            ]
            eligible = any(
                any(isinstance(dte, int) and not isinstance(dte, bool) and 0 <= dte <= 7
                    for dte in (row.get("available_dte_list") or ()))
                for row in readable_rows
            )
            if eligible:
                policy_state = "available"
            elif readable_rows and len(readable_rows) == len(lane.get("tickers") or ()):
                policy_state = "unavailable"
            else:
                policy_state = "unknown"
        _POLICY_TEXTS = {
            "available": "今日 0-7DTE 日内合约池有可用到期；所有候选必须当日平仓",
            "unavailable": "今日深度层标的在 0-7DTE 窗口内无可用到期",
        }
        if policy_state in _POLICY_TEXTS:
            previously_alerted = any(
                k[0] == "intraday_policy"
                and k != ("intraday_policy", policy_state)
                for k in seen
            )
            text = _POLICY_TEXTS[policy_state]
            if previously_alerted:
                text = f"日内池更正：{text}（深度层轮换后重新判定）"
            _emit(
                ("intraday_policy", policy_state),
                IntradayAlert(rule="intraday_policy", ticker=None, text=text),
            )
        # unknown：未知≠没有，先不提醒，等到能判定为止。

    # -- 规则 1：高开/盘前异动（仅盘前时段；预热窗口天然从 09:00 ET 起）----
    if session_state == "premarket":
        for row in _iter_premarket_rows(payload):
            ticker = str(row.get("ticker") or "").strip().upper()
            pre_change = _finite(row.get("pre_change_percent"))
            if not ticker or pre_change is None:
                continue
            if abs(pre_change) < PREMARKET_ABS_CHANGE_MIN:
                continue
            _emit(
                ("premarket_move", ticker),
                IntradayAlert(
                    rule="premarket_move",
                    ticker=ticker,
                    text=_tagged(ticker, f"{ticker} 盘前 {pre_change:+.1f}%"),
                ),
            )

        # -- 规则 6：盘前期权大单（仅盘前；每标的每日一次）------------------
        # 只读候选已带的 option_activity.max_single_turnover（深度层逐标的
        # 异动页聚合）；盘前该页承载的是上一时段成交，文案如实声明口径。
        # 供应商分类不随大单转述（dominant_sentiment 是整页多数，不是该笔
        # 的分类，转述会张冠李戴）；大单只说明「有人愿付大额权利金」，
        # 不归因身份、不暗示方向（改进 #5 语义）。
        for candidate in candidates:
            ticker = str(candidate.get("ticker") or "").strip().upper()
            activity = candidate.get("option_activity")
            if not ticker or not isinstance(activity, Mapping):
                continue
            max_turnover = _finite(activity.get("max_single_turnover"))
            if (
                max_turnover is None
                or max_turnover < PREMARKET_LARGE_PRINT_MIN_TURNOVER
            ):
                continue
            _emit(
                ("premarket_large_print", ticker),
                IntradayAlert(
                    rule="premarket_large_print",
                    ticker=ticker,
                    text=_tagged(
                        ticker,
                        f"{ticker} 期权大单 ${max_turnover / 1e6:.1f}M"
                        "（上一时段异动页最大单笔；来源可能是对冲/库存流，"
                        "不构成方向）",
                    ),
                ),
            )

    # -- 规则 2：放量（仅常规时段；当前 15 分钟窗口读数）--------------------
    if session_state == "regular":
        for candidate in candidates:
            ticker = str(candidate.get("ticker") or "").strip().upper()
            bursts = candidate.get("session_bursts")
            if not ticker or not isinstance(bursts, Mapping):
                continue
            if bursts.get("legacy") is True:
                continue
            if bursts.get("state") != "ready":
                continue
            current = bursts.get("current")
            if not isinstance(current, Mapping):
                continue
            vol_norm = _finite(current.get("vol_norm"))
            score = _finite(current.get("score"))
            thrust = _finite(current.get("thrust_percent"))
            if vol_norm is None or score is None or thrust is None:
                continue
            if vol_norm < VOLUME_ALERT_VOL_NORM_MIN or score < VOLUME_ALERT_MIN_SCORE:
                continue
            _emit(
                ("volume_burst", ticker),
                IntradayAlert(
                    rule="volume_burst",
                    ticker=ticker,
                    text=_tagged(
                        ticker,
                        f"{ticker} 放量 {vol_norm:.1f}× · "
                        f"15分推力 {thrust:+.1f}% · 爆发分 {score:.1f}",
                    ),
                ),
            )

    # -- 规则 3/4 只在「当前交易时段」口径下检测：休市载荷展示的是最近一个
    # 已结束时段的波段/位移，提醒它们会把昨天说成现在。-----------------------
    if quote_scope != "current_session":
        return alerts

    # -- 规则 3：强波段入账（新出现的 grade=strong 波段，一段一次）-----------
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").strip().upper()
        bursts = candidate.get("session_bursts")
        if not ticker or not isinstance(bursts, Mapping):
            continue
        if bursts.get("legacy") is True:
            continue
        for leg in bursts.get("legs") or ():
            if not isinstance(leg, Mapping) or leg.get("grade") != "strong":
                continue
            start_et = str(leg.get("start_et") or "")
            score = _finite(leg.get("score"))
            if not start_et or score is None:
                continue
            direction = _DIRECTION_LABELS.get(
                str(leg.get("direction") or ""), "方向未知"
            )
            _emit(
                ("strong_leg", ticker, start_et),
                IntradayAlert(
                    rule="strong_leg",
                    ticker=ticker,
                    text=_tagged(
                        ticker,
                        f"{ticker} 强波段 {start_et} 分 {score:.0f} {direction}",
                    ),
                ),
            )

    # -- 规则 4：近 30 分位移达 ±0.5 ATR（每标的每方向每日一次）--------------
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").strip().upper()
        bursts = candidate.get("session_bursts")
        if isinstance(bursts, Mapping) and bursts.get("legacy") is True:
            continue
        displacement = candidate.get("recent_displacement")
        if not ticker or not isinstance(displacement, Mapping):
            continue
        if displacement.get("state") != "ready":
            continue
        net = _finite(displacement.get("net_move_atr"))
        if net is None:
            continue
        line = _finite(displacement.get("survival_line_atr"))
        if line is None or line <= 0:
            line = DISPLACEMENT_ALERT_LINE_ATR
        if net >= line:
            direction = "up"
        elif net <= -line:
            direction = "down"
        else:
            continue
        # 美元换算沿用前端扫描表同一口径：仅日线 ATR14 标尺（atr14_daily）
        # 可换算（net × atr14）；其余标尺不可横向比较，只报 ATR 值。
        dollars: Optional[float] = None
        atr14 = _finite(candidate.get("atr14"))
        if (
            displacement.get("atr_basis") == DISPLACEMENT_ATR_BASIS_DAILY
            and atr14 is not None
            and atr14 > 0
        ):
            dollars = net * atr14
        if dollars is not None:
            sign = "+" if dollars >= 0 else "-"
            text = (
                f"{ticker} 近30分位移 {sign}${abs(dollars):.2f}"
                f"（{net:+.2f} ATR）"
            )
        else:
            text = f"{ticker} 近30分位移 {net:+.2f} ATR"
        _emit(
            ("displacement", ticker, direction),
            IntradayAlert(
                rule="displacement",
                ticker=ticker,
                text=_tagged(ticker, text),
            ),
        )

    return alerts


def _et_clock_label(as_of: Optional[str]) -> Optional[str]:
    try:
        moment = datetime.fromisoformat(str(as_of))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return f"{moment.astimezone(_NEW_YORK):%H:%M} ET"


def format_alert_message(
    alerts: list[IntradayAlert],
    *,
    as_of: Optional[str],
    extra_lines: Optional[list[str]] = None,
    header_label: str = "盘中提示",
) -> str:
    """一批提示合并为一条消息；页脚每条消息只出现一次。"""

    clock = _et_clock_label(as_of)
    header = f"【{header_label} · {clock}】" if clock else f"【{header_label}】"
    lines = [header]
    lines.extend(alert.text for alert in alerts)
    lines.extend(extra_lines or [])
    lines.append(ALERT_FOOTER)
    return "\n".join(lines)


def format_p0_2m_alert_line(
    ticker: str,
    evidence: Mapping[str, Any],
    *,
    option_state: str = "pending",
) -> str:
    """Render the factual first message for one strict 2m crossing."""

    direction = str(evidence.get("direction") or "")
    direction_label = _DIRECTION_LABELS.get(direction, direction or "方向标缺")
    trigger = _finite(evidence.get("trigger_price"))
    impulse = _finite(evidence.get("impulse_8m_percent"))
    displacement_pct = _finite(evidence.get("displacement_18m_percent"))
    percentile = _finite(evidence.get("price_percentile"))
    rvol = _finite(evidence.get("rvol_8m"))
    displacement_atr = _finite(evidence.get("displacement_atr"))
    atr14 = _finite(evidence.get("prior_daily_atr14"))
    baseline_count = int(evidence.get("baseline_count") or 0)
    scope = str(evidence.get("session_scope") or "")
    window_end_text = str(evidence.get("window_end") or "")
    try:
        window_end = datetime.fromisoformat(window_end_text.replace("Z", "+00:00"))
        if window_end.tzinfo is None:
            raise ValueError("naive")
        window_end = window_end.astimezone(_NEW_YORK)
    except (TypeError, ValueError):
        window_end = None

    close_detail = "距常规收盘未知"
    if window_end is not None:
        clock = resolve_xnys_session_clock(window_end)
        if clock.state == "session" and clock.close_minute_et is not None:
            end_minute = window_end.hour * 60 + window_end.minute
            remaining = clock.close_minute_et - end_minute
            close_detail = (
                f"距常规收盘 {remaining}m"
                if remaining >= 0
                else f"常规收盘后 {abs(remaining)}m"
            )

    parts = [
        f"{ticker} {direction_label} · 触发价 "
        + (f"${trigger:.2f}" if trigger is not None else "标缺"),
        "8m " + (f"{impulse:+.2f}%" if impulse is not None else "标缺"),
        "18m "
        + (f"{displacement_pct:+.2f}%" if displacement_pct is not None else "标缺")
        + (f" / {displacement_atr:.2f} ATR" if displacement_atr is not None else ""),
        "同钟分位 "
        + (f"P{percentile * 100:.1f}" if percentile is not None else "标缺")
        + f"（{baseline_count}日）",
        "8m RVOL " + (f"{rvol:.2f}x" if rvol is not None else "标缺"),
        "前一交易日 ATR14 " + (f"${atr14:.2f}" if atr14 is not None else "标缺"),
        close_detail,
        "数据时间 " + (f"{window_end:%H:%M} ET" if window_end is not None else "标缺"),
    ]
    if scope in {"premarket", "afterhours"}:
        parts.append("盘外仅作正股信息提醒，不声称期权可执行")
    elif option_state == "pending":
        parts.append("0–7DTE 期权快照异步补充中；无合约不撤销本信号")
    elif option_state == "unavailable":
        parts.append("暂无报价合格期权快照；标的信号保持成立")
    elif option_state == "research_withheld":
        parts.append("未校准研究观察不查询或补充期权合约")
    return _tagged(ticker, " ｜ ".join(parts))


# ---------------------------------------------------------------------------
# 分级决定与出站消息（worker 线程消费的工作项）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OutboundMessage:
    message_key: str
    tier: str
    text: str
    silent: bool
    expires_at: Optional[datetime] = None
    # A live-forward P0 probe is deliberately put on the same bounded worker
    # queue and assembled by the same formatter as a real Telegram message.
    # The worker must never hand it to an external sender or write a delivery.
    probe_only: bool = False
    probe_dedupe_keys: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class _OptionSupplementRequest:
    et_date: str
    ticker: str
    direction: str
    window_end: str


@dataclass(frozen=True)
class _WorkItem:
    et_date: str
    decisions: tuple[Any, ...]  # PushDecisionInput（延迟导入，见 _ledger）
    messages: tuple[_OutboundMessage, ...]
    option_supplements: tuple[_OptionSupplementRequest, ...] = ()
    persisted_at: Optional[datetime] = None
    outbox_states: tuple[tuple[str, str], ...] = ()


@dataclass(order=True, frozen=True)
class _DeliveryRetry:
    """One already-persisted Telegram message awaiting another send attempt."""

    due_monotonic: float
    sequence: int
    item: _WorkItem = field(compare=False)
    message: _OutboundMessage = field(compare=False)
    first_failed_at: datetime = field(compare=False)
    deadline: datetime = field(compare=False)
    retry_index: int = field(compare=False)


class RepositoryPushLedger:
    """默认台账实现：journal_v2_push_events（append-only）。"""

    def append(self, decisions=(), deliveries=()) -> None:
        from src.journal.ledger.push_ledger_repository import append_push_events

        append_push_events(decisions, deliveries)

    def append_with_retries(self, decisions, retries, *, now: datetime):
        from src.journal.ledger.telegram_outbox_repository import (
            TelegramOutboxWrite,
            append_push_decisions_with_telegram_outbox,
        )

        return append_push_decisions_with_telegram_outbox(
            decisions,
            tuple(TelegramOutboxWrite(**retry) for retry in retries),
            now=now,
        )

    def load_day_state(self, et_date: str):
        from src.journal.ledger.push_ledger_repository import load_push_day_state

        return load_push_day_state(et_date)

    def count_delivered_p0_week(self, et_date: str) -> int:
        from src.journal.ledger.push_ledger_repository import (
            count_delivered_p0_since,
        )

        return count_delivered_p0_since(et_date, days=7)

    def count_delivered_p0x_week(self, et_date: str) -> int:
        from src.journal.ledger.push_ledger_repository import (
            count_delivered_p0x_since,
        )

        return count_delivered_p0x_since(et_date, days=7)

    def count_delivered_p0_2m_shadow_research_week(
        self, et_date: str, *, session_scope: str
    ) -> int:
        from src.journal.ledger.push_ledger_repository import (
            count_delivered_p0_2m_shadow_research_since,
        )

        return count_delivered_p0_2m_shadow_research_since(
            et_date,
            session_scope=session_scope,
            days=7,
        )

    def upsert_retry(self, **kwargs):
        from src.journal.ledger.telegram_outbox_repository import (
            upsert_pending_telegram_outbox,
        )

        return upsert_pending_telegram_outbox(**kwargs)

    def reschedule_retry(self, message_key: str, **kwargs) -> bool:
        from src.journal.ledger.telegram_outbox_repository import (
            reschedule_telegram_outbox,
        )

        return reschedule_telegram_outbox(message_key, **kwargs)

    def mark_retry_success(self, message_key: str, **kwargs) -> bool:
        from src.journal.ledger.telegram_outbox_repository import (
            mark_telegram_outbox_success,
        )

        return mark_telegram_outbox_success(message_key, **kwargs)

    def mark_retry_terminal(self, message_key: str, **kwargs) -> bool:
        from src.journal.ledger.telegram_outbox_repository import (
            mark_telegram_outbox_terminal,
        )

        return mark_telegram_outbox_terminal(message_key, **kwargs)

    def load_pending_retries(self, *, now: datetime):
        from src.journal.ledger.telegram_outbox_repository import (
            list_pending_telegram_outbox,
        )

        return list_pending_telegram_outbox(now=now)

    def load_expired_retries(self, *, now: datetime):
        from src.journal.ledger.telegram_outbox_repository import (
            list_expired_pending_telegram_outbox,
        )

        return list_expired_pending_telegram_outbox(now=now)

    def load_succeeded_retries(self):
        from src.journal.ledger.telegram_outbox_repository import (
            list_succeeded_telegram_outbox,
        )

        return list_succeeded_telegram_outbox()

    def load_message_decisions(self, et_date: str, message_key: str):
        from src.journal.ledger.push_ledger_repository import list_push_events

        return tuple(
            event
            for event in list_push_events(et_date, record_kind="decision")
            if event.message_key == message_key
        )

    def message_has_successful_delivery(
        self, et_date: str, message_key: str
    ) -> bool:
        from src.journal.ledger.push_ledger_repository import list_push_events

        return any(
            event.record_kind == "delivery"
            and event.message_key == message_key
            and event.telegram_ok is True
            for event in list_push_events(et_date)
        )


class IntradayOpportunityAlerter:
    """预热载荷观察者：检测/分级在调用线程，发送与落库在独立 worker 线程。

    ``observe`` 是给 ``warm_default_intraday_top_scan(payload_observer=...)``
    的回调：**永不抛出、永不阻塞**（有界队列 ``put_nowait``；队列满时丢弃
    本 tick 工作项并**回滚去重键与预算计数**，同一事实下一 tick 可重检——
    预热节奏永远优先；被丢弃的工作项未及落库，队列满事件本身记日志）。
    诚实边界：消息一旦出队，decision 必须先落库，否则禁止外部发送；发送
    失败（如 Telegram 网络错误）会进入独立的有界重试 worker，期间保留预算
    reservation；成功才完成，过期/耗尽后才释放。pending 尝试不写失败
    receipt；只在成功或确定终止时追加 delivery 证据。opening checkpoint
    仍以 ``expires_at`` 为硬边界，绝不迟到补发。
    发送后 delivery 追加失败则由重启恢复逻辑保守视为预算已占用，避免外部
    已送达却重复超发。
    """

    def __init__(
        self,
        send_text: Callable[[str], Optional[bool]],
        *,
        send_silent_text: Optional[Callable[[str], Optional[bool]]] = None,
        send_text_with_deadline: Optional[
            Callable[[str, datetime], Optional[bool]]
        ] = None,
        max_alerts_per_day: int = DEFAULT_MAX_ALERTS_PER_DAY,
        queue_maxsize: int = _QUEUE_MAXSIZE_DEFAULT,
        async_send: bool = True,
        thread_name: str = "dsa-intraday-alerts",
        ledger: Optional[Any] = None,
        gate_state_provider: Optional[Callable[[], Optional[Mapping[str, Any]]]] = None,
        open_positions_provider: Optional[Callable[[], Optional[tuple]]] = None,
        daily_pnl_provider: Optional[Callable[[str], Optional[float]]] = None,
        context_refresh_seconds: float = _CONTEXT_REFRESH_SECONDS_DEFAULT,
        now_provider: Optional[Callable[[], datetime]] = None,
        option_supplement_provider: Optional[
            Callable[[str, str], Mapping[str, Any]]
        ] = None,
        p0_shadow_mobile_enabled: bool = False,
        delivery_retry_delays_seconds: Sequence[float] = (
            _DELIVERY_RETRY_DELAYS_SECONDS_DEFAULT
        ),
        delivery_retry_ttl_seconds: float = (
            _DELIVERY_RETRY_TTL_SECONDS_DEFAULT
        ),
        delivery_retry_queue_maxsize: int = (
            _DELIVERY_RETRY_QUEUE_MAXSIZE_DEFAULT
        ),
    ) -> None:
        try:
            cap = int(max_alerts_per_day)
        except (TypeError, ValueError):
            cap = DEFAULT_MAX_ALERTS_PER_DAY
        self._max_alerts_per_day = max(1, cap)
        self._send_text = send_text
        self._send_silent_text = send_silent_text
        self._send_text_with_deadline = send_text_with_deadline
        self._now_provider = now_provider or (
            lambda: datetime.now(timezone.utc)
        )
        self._async_send = bool(async_send)
        self._queue: "queue.Queue[_WorkItem]" = queue.Queue(
            maxsize=max(1, int(queue_maxsize))
        )
        self._thread_name = thread_name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._context_thread: Optional[threading.Thread] = None
        # 同步测试/一次性模式会在 observe 持锁期间就地完成发送；失败回收
        # reservation 需要重入同一把锁，RLock 可保持异步生产语义不变。
        self._lock = threading.RLock()
        self._ledger = ledger if ledger is not None else RepositoryPushLedger()
        self._gate_state_provider = gate_state_provider
        self._open_positions_provider = open_positions_provider
        self._daily_pnl_provider = daily_pnl_provider
        self._context_refresh_seconds = max(1.0, float(context_refresh_seconds))
        self._context = TierContext()
        self._context_refreshed_at = 0.0
        self._option_supplement_provider = option_supplement_provider
        self._p0_shadow_mobile_enabled = bool(p0_shadow_mobile_enabled)
        self._option_queue: "queue.Queue[_OptionSupplementRequest]" = queue.Queue(
            maxsize=_OPTION_SUPPLEMENT_QUEUE_MAXSIZE
        )
        self._option_thread: Optional[threading.Thread] = None
        self._delivery_retry_delays = tuple(
            max(0.0, float(delay))
            for delay in delivery_retry_delays_seconds
        )
        self._delivery_retry_ttl_seconds = max(
            0.0, float(delivery_retry_ttl_seconds)
        )
        self._delivery_retry_queue_maxsize = max(
            1, int(delivery_retry_queue_maxsize)
        )
        self._delivery_retry_condition = threading.Condition()
        self._delivery_retry_heap: list[_DeliveryRetry] = []
        self._delivery_retry_pending_keys: set[str] = set()
        self._delivery_retry_persistent_keys: set[str] = set()
        self._delivery_retry_sequence = 0
        self._delivery_retry_thread: Optional[threading.Thread] = None
        # 当日状态（进程内；ET 日期翻转清零；台账重建见 _rebuild_day_state）。
        self._day: Optional[str] = None
        self._seen: set[tuple[str, ...]] = set()
        self._sent_count = 0
        self._cap_notice_sent = False
        self._tier_state = TierEngineState()
        self._day_state_rebuilt = False
        # quiet-unless-incident：仅状态变化时记日志（逐 family 各记一份
        # 指纹，避免「台账健康/发送失败」两族状态交替时来回刷屏）。
        self._last_fingerprints: dict[str, str] = {}

    @property
    def max_alerts_per_day(self) -> int:
        return self._max_alerts_per_day

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def p0_shadow_mobile_enabled(self) -> bool:
        """Effective research-delivery state after startup credential gating."""

        return self._p0_shadow_mobile_enabled

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self._async_send or self.running:
            return
        # API lifecycle and lazy producer startup may race.  Use the existing
        # reentrant lock because observe can already hold it when dispatching.
        with self._lock:
            if self.running:
                return
            self._stop_event.clear()
            self._restore_delivery_retries()
            self._thread = threading.Thread(
                target=self._drain, name=self._thread_name, daemon=True
            )
            self._delivery_retry_thread = threading.Thread(
                target=self._drain_delivery_retries,
                name=f"{self._thread_name}-delivery-retries",
                daemon=True,
            )
            self._delivery_retry_thread.start()
            self._thread.start()
            if self._option_supplement_provider is not None:
                self._option_thread = threading.Thread(
                    target=self._drain_option_supplements,
                    name=f"{self._thread_name}-option-supplements",
                    daemon=True,
                )
                self._option_thread.start()
            if any(
                provider is not None
                for provider in (
                    self._gate_state_provider,
                    self._open_positions_provider,
                    self._daily_pnl_provider,
                )
            ):
                # 上下文 provider 可能触发有界但较慢的 DB/日线读取；它与发送
                # worker 物理分离，09:35 已入队的 P1-O 不会被一次 gate 刷新挡住。
                self._context_thread = threading.Thread(
                    target=self._refresh_context_loop,
                    name=f"{self._thread_name}-context",
                    daemon=True,
                )
                self._context_thread.start()
            logger.info(
                "[intraday-alerts] started max_per_day=%d", self._max_alerts_per_day
            )

    def stop(self, *, join_timeout_seconds: Optional[float] = 2.0) -> None:
        """有界收尾：发送链（Telegram 重试）可长达数秒，daemon 线程不拖关停。"""

        self._stop_event.set()
        threads = [
            thread
            for thread in (
                self._thread,
                self._delivery_retry_thread,
                self._context_thread,
                self._option_thread,
            )
            if thread is not None
        ]
        if not threads:
            return
        with self._delivery_retry_condition:
            self._delivery_retry_condition.notify_all()
        timeout = (
            None
            if join_timeout_seconds is None
            else max(0.0, float(join_timeout_seconds))
        )
        for thread in threads:
            thread.join(timeout=timeout)
        live = [thread.name for thread in threads if thread.is_alive()]
        if live:
            logger.warning(
                "[intraday-alerts] stop timed out; daemon workers still running: %s",
                ",".join(live),
            )
            return
        with self._delivery_retry_condition:
            self._delivery_retry_heap.clear()
            self._delivery_retry_pending_keys.clear()
            self._delivery_retry_persistent_keys.clear()
        self._thread = None
        self._delivery_retry_thread = None
        self._context_thread = None
        self._option_thread = None
        logger.info("[intraday-alerts] stopped")

    # -- context（worker 线程按 TTL 刷新；观察线程只读快照）-----------------
    def _refresh_context(self, *, force: bool = False) -> None:
        now = time_module.monotonic()
        if (
            not force
            and now - self._context_refreshed_at < self._context_refresh_seconds
        ):
            return
        gate_state = None
        open_positions = None
        daily_pnl = None
        et_date = self._day or datetime.now(_NEW_YORK).strftime("%Y-%m-%d")
        try:
            if self._gate_state_provider is not None:
                gate_state = self._gate_state_provider()
        except Exception:  # noqa: BLE001 - context is best-effort, 标缺降级
            gate_state = None
        try:
            if self._open_positions_provider is not None:
                open_positions = self._open_positions_provider()
        except Exception:  # noqa: BLE001
            open_positions = None
        try:
            if self._daily_pnl_provider is not None:
                daily_pnl = self._daily_pnl_provider(et_date)
        except Exception:  # noqa: BLE001
            daily_pnl = None
        with self._lock:
            self._context = TierContext(
                gate_state=gate_state,
                open_positions=(
                    tuple(open_positions) if open_positions is not None else None
                ),
                daily_realized_pnl=daily_pnl,
                as_of=datetime.now(timezone.utc).isoformat(),
            )
            self._context_refreshed_at = now

    def _refresh_context_loop(self) -> None:
        """独立刷新原子上下文快照；绝不占用 Telegram 发送 worker。"""

        while not self._stop_event.is_set():
            self._refresh_context()
            self._stop_event.wait(
                min(self._context_refresh_seconds, 1.0)
            )

    # -- observation -------------------------------------------------------
    def notify_system(self, text: str) -> None:
        """系统级通知（如行情通道断开/恢复）：直接入队，不占每日提示上限。

        通道健康属于「系统在不在工作」，与市场事实提示是两类信息——被日
        上限压掉一条「通道断了」的通知是不可接受的。系统通知同样落台账
        （tier=system），但不占 P0/P1 预算与全局上限。
        """

        try:
            from src.journal.ledger.push_ledger_repository import (
                PushDecisionInput,
                new_push_id,
            )

            now = datetime.now(timezone.utc)
            et_date = self._day or now.astimezone(_NEW_YORK).strftime("%Y-%m-%d")
            message_key = new_push_id()
            decision = PushDecisionInput(
                push_id=new_push_id(),
                et_date=et_date,
                ts=now,
                tier="system",
                rule="system_notice",
                ticker=None,
                text=str(text),
                delivery_channel="telegram",
                message_key=message_key,
            )
            item = _WorkItem(
                et_date=et_date,
                decisions=(decision,),
                messages=(
                    _OutboundMessage(
                        message_key=message_key,
                        tier="system",
                        text=f"【系统】{text}",
                        silent=False,
                    ),
                ),
            )
            if not self._dispatch(item):
                # D7：系统通知没有可回滚的去重键、也没有已落库的台账行
                # （decision 行随工作项一起被丢弃）——这行日志就是文档化
                # 的唯一痕迹，每次丢弃都记（不做指纹去重）。
                logger.warning(
                    "[intraday-alerts] system notice dropped (queue full); "
                    "no ledger row exists for it — this log line is the "
                    "only trace: %s",
                    str(text)[:120],
                )
        except Exception:  # noqa: BLE001 - 通知永不外抛
            logger.warning("[intraday-alerts] system notice failed", exc_info=True)

    def observe(self, payload: Mapping[str, Any]) -> bool:
        """Accept one payload; return ``False`` only when it must be retried.

        Exceptions remain contained, but the strict 2m producer can now avoid
        committing a completed ``window_end`` when the bounded queue is full.
        """

        try:
            return self._observe(payload)
        except Exception as exc:  # noqa: BLE001 - warm cadence first, always
            self._note_state(
                f"observe:error:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] observe failed error_type=%s; alerts skipped "
                "this tick",
                type(exc).__name__,
            )
            return False

    def observe_p0_2m(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Accept strict-2m evidence and return an auditable queue receipt.

        ``observe`` remains boolean-compatible for legacy callers.  The
        strict-2m monitor uses this richer sibling so forward shadow can prove
        whether each qualified crossing was first, a 30-minute duplicate, or
        policy-suppressed before the work-queue write.
        """

        try:
            result = self._observe(payload, capture_p0_receipt=True)
            if isinstance(result, Mapping):
                return result
        except Exception as exc:  # noqa: BLE001 - same containment as observe
            self._note_state(
                f"observe_p0_2m:error:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] strict-2m observe failed error_type=%s; "
                "alerts skipped this tick",
                type(exc).__name__,
            )
        return {
            "accepted": False,
            "observer_queue_enqueued_at": None,
            "candidate_outcomes": [],
        }

    def observe_opening(self, payload: Mapping[str, Any]) -> bool:
        """轻量 09:35 回调：只装配 P1-O，先于期权链与完整 Top 扫描。"""

        try:
            return self._observe(payload, opening_only=True)
        except Exception as exc:  # noqa: BLE001 - fast path must never break warm loop
            self._note_state(
                f"opening_observe:error:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] opening fast-path observe failed "
                "error_type=%s; full scan will retry",
                type(exc).__name__,
            )
            return False

    def _rebuild_day_state(self, market_date: str) -> bool:
        """当日状态重建（重启防重复；每 ET 日仅一次有界台账读取）。"""

        try:
            day_state = self._ledger.load_day_state(market_date)
            week_p0 = self._ledger.count_delivered_p0_week(market_date)
            count_p0x = getattr(
                self._ledger, "count_delivered_p0x_week", None
            )
            week_p0x = count_p0x(market_date) if callable(count_p0x) else 0
            count_shadow = getattr(
                self._ledger,
                "count_delivered_p0_2m_shadow_research_week",
                None,
            )
            week_shadow_p0 = (
                count_shadow(market_date, session_scope="regular")
                if callable(count_shadow)
                else 0
            )
            week_shadow_p0x = (
                count_shadow(market_date, session_scope="extended")
                if callable(count_shadow)
                else 0
            )
        except Exception as exc:  # noqa: BLE001 - 台账不可读 → 诚实降级
            self._note_state(
                f"day_state:rebuild_failed:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] push-ledger day-state rebuild failed "
                "error_type=%s; observations rejected until persisted "
                "dedupe and budgets can be restored",
                type(exc).__name__,
            )
            return False
        self._seen |= day_state.seen_keys
        # Rolling upgrade compatibility: an alert written by the old 5m-only
        # build must suppress the new progressive setup (and vice versa).
        for seen_key in tuple(self._seen):
            if (
                len(seen_key) >= 3
                and seen_key[0] in _OPENING_PRIMARY_RULES
                and seen_key[2] == "09:30"
            ):
                self._seen.add(
                    (_OPENING_CANONICAL_DEDUPE_RULE, seen_key[1], "09:30")
                )
        self._sent_count = max(self._sent_count, day_state.telegram_alert_count)
        self._tier_state.p0_delivered_today = day_state.p0_delivered
        self._tier_state.p1_delivered_today = day_state.p1_delivered
        self._tier_state.opening_delivered_today = getattr(
            day_state, "opening_delivered", 0
        )
        self._tier_state.p0_delivered_week = week_p0
        self._tier_state.p0x_delivered_today_by_session = {
            "premarket": int(
                getattr(day_state, "p0x_delivered_by_session", {}).get(
                    "premarket", 0
                )
            ),
            "afterhours": int(
                getattr(day_state, "p0x_delivered_by_session", {}).get(
                    "afterhours", 0
                )
            ),
        }
        self._tier_state.p0x_delivered_week = int(week_p0x)
        self._tier_state.p0_2m_shadow_research_delivered_today = int(
            getattr(day_state, "p0_2m_shadow_research_delivered", 0)
        )
        self._tier_state.p0_2m_shadow_research_delivered_week = int(
            week_shadow_p0
        )
        self._tier_state.p0x_2m_shadow_research_delivered_today_by_session = {
            "premarket": int(
                getattr(
                    day_state,
                    "p0x_2m_shadow_research_delivered_by_session",
                    {},
                ).get("premarket", 0)
            ),
            "afterhours": int(
                getattr(
                    day_state,
                    "p0x_2m_shadow_research_delivered_by_session",
                    {},
                ).get("afterhours", 0)
            ),
        }
        self._tier_state.p0x_2m_shadow_research_delivered_week = int(
            week_shadow_p0x
        )
        for seen_key in tuple(self._seen):
            if len(seen_key) not in {4, 5} or seen_key[0] != "p0_2m_episode":
                continue
            if len(seen_key) == 5:
                _rule, ticker, session_scope, direction, window_end = seen_key
            else:
                _rule, ticker, direction, window_end = seen_key
                session_scope = None
            # ISO timestamps with offsets are not safely ordered as raw text;
            # compare aware instants and retain the newest accepted episode.
            try:
                candidate_at = datetime.fromisoformat(
                    str(window_end).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                continue
            if candidate_at.tzinfo is None:
                continue
            if session_scope is None:
                # Legacy keys lack scope. A completed bar ending exactly at
                # 09:30/real close belongs to the session immediately before
                # that boundary, not the clock phase after its end label.
                legacy_clock = resolve_xnys_session_clock(
                    candidate_at - timedelta(microseconds=1)
                )
                session_scope = getattr(legacy_clock, "phase", None)
                if session_scope not in {"premarket", "regular", "afterhours"}:
                    self._note_state(
                        "day_state:legacy_episode_scope_unavailable",
                        logging.WARNING,
                        "[intraday-alerts] legacy strict-2m episode scope "
                        "cannot be restored; observations rejected until "
                        "the XNYS session can be verified",
                    )
                    return False
            if session_scope not in {"premarket", "regular", "afterhours"}:
                continue
            self._seen.add(
                ("p0_2m_episode", ticker, session_scope, direction, window_end)
            )
            identity = f"{session_scope}:{ticker}:{direction}"
            previous = self._tier_state.p0_episode_last_window.get(identity)
            previous_at = (
                datetime.fromisoformat(previous.replace("Z", "+00:00"))
                if previous
                else None
            )
            if previous_at is None or (
                previous_at.tzinfo is not None and candidate_at > previous_at
            ):
                self._tier_state.p0_episode_last_window[identity] = str(
                    window_end
                )
        if ("r6_fuse_notice",) in day_state.seen_keys:
            # D2：R6 当日锁存的重启重建——触线通告 decision 行的去重键就
            # 是「本 ET 日曾确证触线」的台账证据；重启后 re-latch，保证
            # 「当日不可上调」跨进程成立（明日随日期翻转复位）。
            self._tier_state.fuse_latched_today = True
        if self._sent_count >= self._max_alerts_per_day:
            self._cap_notice_sent = True
        self._day_state_rebuilt = True
        return True

    def _observe(
        self,
        payload: Mapping[str, Any],
        *,
        opening_only: bool = False,
        capture_p0_receipt: bool = False,
    ) -> Any:
        if not isinstance(payload, Mapping):
            return True
        market_date = str(payload.get("market_date_et") or "").strip()
        if not market_date:
            # 没有 ET 日期就没有诚实的去重口径：安静跳过本 tick。
            return True
        if not self._async_send:
            # 同步模式（测试/一次性）：观察线程即工作线程，就地刷新上下文。
            self._refresh_context(force=True)
        with self._lock:
            if market_date != self._day:
                self._day = market_date
                self._seen.clear()
                self._sent_count = 0
                self._cap_notice_sent = False
                self._tier_state = TierEngineState()
                self._day_state_rebuilt = False
            if not self._day_state_rebuilt:
                if not self._rebuild_day_state(market_date):
                    if capture_p0_receipt:
                        return self._p0_observer_receipt(
                            payload, item=None, accepted=False, enqueued_at=None
                        )
                    return False
            context = self._context

            # 回滚快照（队列满时恢复）。
            seen_before = set(self._seen)
            sent_before = self._sent_count
            cap_before = self._cap_notice_sent
            p0_today_before = self._tier_state.p0_delivered_today
            p0_week_before = self._tier_state.p0_delivered_week
            p0x_today_before = dict(
                self._tier_state.p0x_delivered_today_by_session
            )
            p0x_week_before = self._tier_state.p0x_delivered_week
            shadow_p0_today_before = (
                self._tier_state.p0_2m_shadow_research_delivered_today
            )
            shadow_p0_week_before = (
                self._tier_state.p0_2m_shadow_research_delivered_week
            )
            shadow_p0x_today_before = dict(
                self._tier_state.p0x_2m_shadow_research_delivered_today_by_session
            )
            shadow_p0x_week_before = (
                self._tier_state.p0x_2m_shadow_research_delivered_week
            )
            p0_episode_before = dict(
                self._tier_state.p0_episode_last_window
            )
            p1_before = self._tier_state.p1_delivered_today
            opening_before = self._tier_state.opening_delivered_today

            item = self._build_work_item(
                payload,
                market_date,
                context,
                opening_only=opening_only,
            )
            if item is None:
                if capture_p0_receipt:
                    return self._p0_observer_receipt(
                        payload, item=None, accepted=True, enqueued_at=None
                    )
                return True
            enqueued = self._dispatch(item, durable=capture_p0_receipt)
            enqueued_at = None
            if enqueued:
                enqueued_at = self._now_provider()
                if enqueued_at.tzinfo is None:
                    enqueued_at = enqueued_at.replace(tzinfo=timezone.utc)
                else:
                    enqueued_at = enqueued_at.astimezone(timezone.utc)
            if not enqueued:
                # 队列满：回滚本 tick 的去重键与预算计数，让同一事实下一
                # tick 重检——否则一次拥塞就把当日这些提示永久吞掉。
                self._seen.clear()
                self._seen.update(seen_before)
                self._sent_count = sent_before
                self._cap_notice_sent = cap_before
                self._tier_state.p0_delivered_today = p0_today_before
                self._tier_state.p0_delivered_week = p0_week_before
                self._tier_state.p0x_delivered_today_by_session = (
                    p0x_today_before
                )
                self._tier_state.p0x_delivered_week = p0x_week_before
                self._tier_state.p0_2m_shadow_research_delivered_today = (
                    shadow_p0_today_before
                )
                self._tier_state.p0_2m_shadow_research_delivered_week = (
                    shadow_p0_week_before
                )
                self._tier_state.p0x_2m_shadow_research_delivered_today_by_session = (
                    shadow_p0x_today_before
                )
                self._tier_state.p0x_2m_shadow_research_delivered_week = (
                    shadow_p0x_week_before
                )
                self._tier_state.p0_episode_last_window = p0_episode_before
                self._tier_state.p1_delivered_today = p1_before
                self._tier_state.opening_delivered_today = opening_before
            if capture_p0_receipt:
                return self._p0_observer_receipt(
                    payload,
                    item=item,
                    accepted=enqueued,
                    enqueued_at=enqueued_at,
                )
            return enqueued

    def _p0_observer_receipt(
        self,
        payload: Mapping[str, Any],
        *,
        item: Optional[_WorkItem],
        accepted: bool,
        enqueued_at: Optional[datetime],
    ) -> dict[str, Any]:
        """Project per-candidate dedupe/policy facts from one work item."""

        decisions = tuple(item.decisions) if item is not None else ()
        messages = tuple(item.messages) if item is not None else ()
        probe_message_by_key = {
            dedupe_key: message
            for message in messages
            if message.probe_only
            for dedupe_key in message.probe_dedupe_keys
        }
        actual_message_keys = {
            message.message_key for message in messages if not message.probe_only
        }
        outcomes: list[dict[str, Any]] = []
        for candidate in payload.get("candidates") or ():
            if not isinstance(candidate, Mapping):
                continue
            ticker = str(candidate.get("ticker") or "").strip().upper()
            evidence = candidate.get("p0_signal_evidence_v2")
            if not ticker or not isinstance(evidence, Mapping):
                continue
            if (
                evidence.get("alert_qualified") is not True
                or evidence.get("session_scope") != "regular"
            ):
                continue
            window_end = str(evidence.get("window_end") or "")
            matching = [
                decision
                for decision in decisions
                if getattr(decision, "rule", None) == "p0_2m_candidate"
                and str(getattr(decision, "ticker", "") or "").upper()
                == ticker
                and window_end
                in tuple(
                    str(part)
                    for part in getattr(decision, "dedupe_key", ())
                )
            ]
            decision = matching[-1] if matching else None
            research_matching = [
                research_decision
                for research_decision in decisions
                if getattr(research_decision, "rule", None)
                == P0_2M_SHADOW_RESEARCH_RULE
                and str(
                    getattr(research_decision, "ticker", "") or ""
                ).upper()
                == ticker
                and window_end
                in tuple(
                    str(part)
                    for part in getattr(
                        research_decision, "dedupe_key", ()
                    )
                )
            ]
            research_decision = (
                research_matching[-1] if research_matching else None
            )
            reason = (
                str(getattr(decision, "suppressed_reason", "") or "")
                if decision is not None
                else None
            )
            shadow_only = bool(
                reason and reason.startswith("p0_2m_shadow:")
            )
            unsuppressed_telegram = bool(
                decision is not None
                and getattr(decision, "delivery_channel", None) == "telegram"
                and not bool(getattr(decision, "suppressed", False))
            )
            decision_key = tuple(getattr(decision, "dedupe_key", ()))
            probe_message = probe_message_by_key.get(decision_key)
            actual_message_key = (
                str(getattr(decision, "message_key", "") or "")
                if decision is not None
                else ""
            )
            probe_mode = bool(
                shadow_only
                and probe_message is not None
                and self._async_send
            )
            actual_mode = bool(
                unsuppressed_telegram
                and actual_message_key in actual_message_keys
                and self._async_send
            )
            research_message_key = (
                str(getattr(research_decision, "message_key", "") or "")
                if research_decision is not None
                else ""
            )
            research_actual_mode = bool(
                research_decision is not None
                and getattr(research_decision, "delivery_channel", None)
                == "telegram"
                and not bool(
                    getattr(research_decision, "suppressed", False)
                )
                and research_message_key in actual_message_keys
                and self._async_send
            )
            attempt_id: Optional[str] = None
            evidence_mode: Optional[str] = None
            if research_actual_mode:
                attempt_id = research_message_key
                evidence_mode = "actual_telegram_queue_receipt"
            elif probe_mode:
                attempt_id = probe_message.message_key
                evidence_mode = "counterfactual_no_send_same_queue"
            elif actual_mode:
                attempt_id = actual_message_key
                evidence_mode = "actual_telegram_queue_receipt"
            outcomes.append(
                {
                    "symbol": ticker,
                    "window_end": window_end,
                    "notification_decision_available": decision is not None,
                    # Synchronous test mode has no bounded Telegram worker
                    # queue, so it cannot claim queue-SLA evidence.
                    "would_notify": evidence_mode is not None,
                    "duplicate_episode_suppressed": (
                        reason == "same_symbol_direction_30m_episode"
                    ),
                    "suppressed_reason": reason,
                    "notification_attempt_id": attempt_id,
                    "telegram_enqueue_evidence_mode": evidence_mode,
                    "telegram_queue_accepted": bool(
                        accepted and evidence_mode is not None
                    ),
                }
            )
        return {
            "accepted": bool(accepted),
            "observer_queue_enqueued_at": (
                enqueued_at.isoformat() if enqueued_at is not None else None
            ),
            "candidate_outcomes": outcomes,
        }

    # -- 分级装配 -----------------------------------------------------------
    def _build_work_item(
        self,
        payload: Mapping[str, Any],
        market_date: str,
        context: TierContext,
        *,
        opening_only: bool = False,
    ) -> Optional[_WorkItem]:
        from src.journal.ledger.push_ledger_repository import (
            PushDecisionInput,
            new_push_id,
        )

        now = self._now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        as_of = payload.get("as_of")
        state = self._tier_state
        opportunity_window_open = opportunity_telegram_window(payload)
        exit_deadline = same_day_exit_deadline_et(payload)
        exit_deadline_label = (
            f"{exit_deadline[0]:02d}:{exit_deadline[1]:02d} ET"
            if exit_deadline is not None
            else "XNYS 当日收盘前 15 分钟"
        )
        # R6 保险丝：当日锁存判定（触线后当日不可上调；缺失语义见
        # alert_tiering.resolve_fuse_state docstring）。锁存位在队列满回滚
        # 时**有意不回滚**：宁可多降级一 tick，绝不因拥塞把触线状态放行；
        # 触线通告的去重键会随回滚复原，下一 tick 重发并落库。
        fuse = resolve_fuse_state(context, state)

        decisions: list[PushDecisionInput] = []
        opening_lines: list[IntradayAlert] = []
        opening_update_lines: list[IntradayAlert] = []
        opening_message_expires_at: Optional[datetime] = None
        p0_lines: list[IntradayAlert] = []
        p0_probe_lines: list[IntradayAlert] = []
        p0_probe_keys: list[tuple[str, ...]] = []
        p0_shadow_research_lines: list[IntradayAlert] = []
        p1_lines: list[IntradayAlert] = []
        option_supplement_requests: list[_OptionSupplementRequest] = []

        def _decision(
            *,
            tier: str,
            rule: str,
            ticker: Optional[str],
            text: str,
            delivery: str,
            dedupe_key: tuple[str, ...] = (),
            conditions: Optional[list[dict[str, Any]]] = None,
            suppressed: bool = False,
            suppressed_reason: Optional[str] = None,
            gate_label: Optional[str] = None,
            lane_rule_id: Optional[str] = None,
            message_key: Optional[str] = None,
            option_contract: Optional[Mapping[str, Any]] = None,
            session_scope: Optional[str] = None,
        ) -> PushDecisionInput:
            option_mid = None
            option_spread_pct = None
            if isinstance(option_contract, Mapping):
                if option_contract.get("state") == "ready":
                    parsed_mid = _finite(option_contract.get("mid"))
                    parsed_spread = _finite(
                        option_contract.get("spread_percent")
                    )
                    if (
                        parsed_mid is not None
                        and parsed_mid > 0
                        and parsed_spread is not None
                        and parsed_spread >= 0
                    ):
                        option_mid = parsed_mid
                        option_spread_pct = parsed_spread
            record = PushDecisionInput(
                push_id=new_push_id(),
                et_date=market_date,
                ts=now,
                tier=tier,
                rule=rule,
                ticker=ticker,
                text=text,
                delivery_channel=delivery,
                dedupe_key=dedupe_key,
                conditions=conditions,
                lane_rule_id=lane_rule_id,
                option_mid=option_mid,
                option_spread_pct=option_spread_pct,
                gate_state=gate_label,
                suppressed=suppressed,
                suppressed_reason=suppressed_reason,
                message_key=message_key,
                session_scope=session_scope,
            )
            decisions.append(record)
            return record

        def _opening_messages() -> list[_OutboundMessage]:
            messages: list[_OutboundMessage] = []
            for lines, channel, header, silent in (
                (opening_lines, "telegram", _TIER_HEADERS["P1O"], False),
                (
                    opening_update_lines,
                    "telegram_silent",
                    _TIER_HEADERS["P1O_UPDATE"],
                    True,
                ),
            ):
                if not lines:
                    continue
                message_key = new_push_id()
                line_rules = {alert.rule for alert in lines}
                for record_index, record in enumerate(decisions):
                    if (
                        record.rule in line_rules
                        and record.delivery_channel == channel
                        and not record.suppressed
                        and record.message_key is None
                    ):
                        decisions[record_index] = replace(
                            record, message_key=message_key
                        )
                messages.append(
                    _OutboundMessage(
                        message_key=message_key,
                        tier="P1",
                        text=format_alert_message(
                            lines,
                            as_of=as_of,
                            header_label=header,
                        ),
                        silent=silent,
                        expires_at=(
                            opening_message_expires_at
                            if channel == "telegram"
                            else None
                        ),
                    )
                )
            return messages

        # -- Exact-2m P0/P0X: signal facts first; delivery policy second -----
        if payload.get("schema_version") == "intraday_p0_2m_scan_v1":
            mobile_gate = payload.get("p0_mobile_delivery")
            mobile_enabled = bool(
                isinstance(mobile_gate, Mapping)
                and mobile_gate.get("enabled") is True
            )
            mobile_reason = (
                str(mobile_gate.get("reason") or "activation_gate_closed")
                if isinstance(mobile_gate, Mapping)
                else "activation_gate_missing"
            )

            for candidate in payload.get("candidates") or ():
                if not isinstance(candidate, Mapping):
                    continue
                ticker = str(candidate.get("ticker") or "").strip().upper()
                evidence = candidate.get("p0_signal_evidence_v2")
                if not ticker or not isinstance(evidence, Mapping):
                    continue
                candidate_mobile_eligible = (
                    candidate.get("p0_mobile_eligible") is True
                )
                evidence = dict(evidence)
                window_end = str(evidence.get("window_end") or "")
                signal_state = str(evidence.get("state") or "unavailable")
                session_scope = str(evidence.get("session_scope") or "") or None
                direction = str(evidence.get("direction") or "")
                data_quality = evidence.get("data_quality")
                data_quality_complete = bool(
                    isinstance(data_quality, Mapping)
                    and data_quality.get("status") == "complete"
                )
                conditions = evaluate_p0_2m_conditions(candidate)
                conditions_json = [condition.as_json() for condition in conditions]
                conditions_json.append(
                    {
                        "condition_id": "p0_2m_evidence",
                        "name": "版本化 2m 信号证据",
                        "status": "pass"
                        if data_quality_complete
                        else "missing",
                        "detail": str(evidence.get("basis") or ""),
                        "evidence": evidence,
                    }
                )

                if signal_state in {
                    "baseline_warming",
                    "shadow",
                    "unavailable",
                    "pending",
                }:
                    reason = str(
                        evidence.get("unavailable_reason") or signal_state
                    )
                    key = ("p0_2m_state", ticker, signal_state, reason)
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                    _decision(
                        tier="P3",
                        rule="p0_2m_data_state",
                        ticker=ticker,
                        text=f"{ticker} 2m P0 数据状态：{signal_state}（{reason}）",
                        delivery="ledger_only",
                        dedupe_key=key,
                        conditions=conditions_json,
                        suppressed=True,
                        suppressed_reason=reason[:128],
                        session_scope=session_scope,
                    )
                    continue

                if signal_state == "not_qualified":
                    # A full universe produces roughly 33k completed windows
                    # across 04:00--20:00.  Per-window negative rows would bury
                    # actual crossings and make the daily feed unbounded.  The
                    # monitor exposes aggregate coverage/health instead; only
                    # forming, qualified, and state-transition facts enter the
                    # push ledger.
                    continue

                if signal_state == "forming":
                    key = ("p0_2m_forming", ticker, window_end)
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                    line = format_p0_2m_alert_line(
                        ticker, evidence, option_state="pending"
                    )
                    _decision(
                        tier="P2",
                        rule="p0_2m_forming",
                        ticker=ticker,
                        text=f"{line} ｜ 形成中：18m 位移尚未达到 0.50 ATR",
                        delivery="web_only",
                        dedupe_key=key,
                        conditions=conditions_json,
                        session_scope=session_scope,
                    )
                    continue

                expected_tier = "P0" if session_scope == "regular" else "P0X"
                tier = str(evidence.get("alert_tier") or "")
                all_signal_checks_pass = all(
                    condition.status == "pass" for condition in conditions
                )
                if (
                    signal_state != "qualified"
                    or evidence.get("alert_qualified") is not True
                    or tier != expected_tier
                    or direction not in {"up", "down"}
                    or session_scope not in {"premarket", "regular", "afterhours"}
                    or not all_signal_checks_pass
                ):
                    key = ("p0_2m_contract_invalid", ticker, window_end)
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                    _decision(
                        tier="P3",
                        rule="p0_2m_contract_invalid",
                        ticker=ticker,
                        text=f"{ticker} 2m P0 生产方契约不一致，已 fail closed",
                        delivery="ledger_only",
                        dedupe_key=key,
                        conditions=conditions_json,
                        suppressed=True,
                        suppressed_reason="p0_2m_evidence_contract_invalid",
                        session_scope=session_scope,
                    )
                    continue

                episode_identity = f"{session_scope}:{ticker}:{direction}"
                prior_window = state.p0_episode_last_window.get(episode_identity)
                duplicate_episode = False
                if prior_window:
                    try:
                        prior_end = datetime.fromisoformat(
                            prior_window.replace("Z", "+00:00")
                        )
                        current_end = datetime.fromisoformat(
                            window_end.replace("Z", "+00:00")
                        )
                        if prior_end.tzinfo is not None and current_end.tzinfo is not None:
                            elapsed = (current_end - prior_end).total_seconds()
                            duplicate_episode = 0 <= elapsed < 30 * 60
                    except (TypeError, ValueError):
                        duplicate_episode = False

                if duplicate_episode:
                    key = (
                        "p0_2m_episode_suppressed",
                        ticker,
                        session_scope,
                        direction,
                        window_end,
                    )
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                    _decision(
                        tier=tier,
                        rule="p0_2m_candidate",
                        ticker=ticker,
                        text=format_p0_2m_alert_line(ticker, evidence),
                        delivery="web_only",
                        dedupe_key=key,
                        conditions=conditions_json,
                        suppressed=True,
                        suppressed_reason="same_symbol_direction_30m_episode",
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        session_scope=session_scope,
                    )
                    continue

                key = ("p0_2m_episode", ticker, session_scope, direction, window_end)
                if key in self._seen:
                    continue
                self._seen.add(key)
                state.p0_episode_last_window[episode_identity] = window_end
                option_state = str(
                    candidate.get("option_supplement_state") or "pending"
                )
                if fuse in {"triggered", "missing"}:
                    option_state = "withheld_by_r6"
                line = format_p0_2m_alert_line(
                    ticker,
                    evidence,
                    option_state=option_state,
                )
                if fuse == "triggered":
                    line += " ｜ R6 保险丝已触发：仅发送事实，禁止行动性措辞/合约补充"
                elif fuse == "missing":
                    line += " ｜ R6 状态标缺：仅发送事实，禁止行动性措辞/合约补充"

                suppressed_reason: Optional[str] = None
                if session_scope != "regular":
                    # The current calibration and live-forward protocol only
                    # validate RTH. Extended facts retain the research path.
                    suppressed_reason = "p0_2m_shadow:extended_session_not_validated"
                elif not mobile_enabled:
                    suppressed_reason = f"p0_2m_shadow:{mobile_reason}"[:128]
                elif not candidate_mobile_eligible:
                    suppressed_reason = "p0_2m_shadow:symbol_not_mobile_eligible"
                elif tier == "P0" and (
                    state.p0_delivered_today >= P0_DAILY_BUDGET
                    or state.p0_delivered_week >= P0_WEEKLY_BUDGET
                ):
                    suppressed_reason = "p0_delivery_budget_exhausted"

                if suppressed_reason is not None:
                    _decision(
                        tier=tier,
                        rule="p0_2m_candidate",
                        ticker=ticker,
                        text=line,
                        delivery="web_only",
                        dedupe_key=key,
                        conditions=conditions_json,
                        suppressed=True,
                        suppressed_reason=suppressed_reason,
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        session_scope=session_scope,
                    )
                    if (
                        tier == "P0"
                        and suppressed_reason.startswith("p0_2m_shadow:")
                    ):
                        p0_probe_lines.append(
                            IntradayAlert(
                                rule="p0_2m_candidate",
                                ticker=ticker,
                                text=line,
                                dedupe_key=key,
                            )
                        )
                        p0_probe_keys.append(key)
                    if (
                        self._p0_shadow_mobile_enabled
                        and suppressed_reason.startswith("p0_2m_shadow:")
                    ):
                        research_key = (
                            P0_2M_SHADOW_RESEARCH_RULE,
                            ticker,
                            direction,
                            window_end,
                        )
                        self._seen.add(research_key)
                        research_line = format_p0_2m_alert_line(
                            ticker,
                            evidence,
                            option_state="research_withheld",
                        )
                        if fuse == "triggered":
                            research_line += (
                                " ｜ R6 保险丝已触发：仅发送事实，"
                                "禁止行动性措辞/合约补充"
                            )
                        elif fuse == "missing":
                            research_line += (
                                " ｜ R6 状态标缺：仅发送事实，"
                                "禁止行动性措辞/合约补充"
                            )
                        research_line += (
                            " ｜ 尚未完成前瞻校准；仅供研究观察，"
                            "不可据此交易"
                        )
                        research_budget_reason: Optional[str] = None
                        research_by_session = (
                            state.p0x_2m_shadow_research_delivered_today_by_session
                        )
                        if tier == "P0" and (
                            state.p0_2m_shadow_research_delivered_today
                            >= P0_DAILY_BUDGET
                            or state.p0_2m_shadow_research_delivered_week
                            >= P0_WEEKLY_BUDGET
                        ):
                            research_budget_reason = (
                                "p0_2m_shadow_research_rth_budget_exhausted"
                            )
                        elif tier == "P0X" and (
                            research_by_session.get(session_scope or "", 0)
                            >= P0X_DAILY_BUDGET_PER_SESSION
                            or state.p0x_2m_shadow_research_delivered_week
                            >= P0X_WEEKLY_BUDGET
                        ):
                            research_budget_reason = (
                                "p0_2m_shadow_research_p0x_budget_exhausted"
                            )
                        if research_budget_reason is not None:
                            _decision(
                                tier="P2",
                                rule=P0_2M_SHADOW_RESEARCH_RULE,
                                ticker=ticker,
                                text=research_line,
                                delivery="telegram",
                                dedupe_key=research_key,
                                conditions=conditions_json,
                                suppressed=True,
                                suppressed_reason=research_budget_reason,
                                lane_rule_id=INTRADAY_LANE_RULE_ID,
                                session_scope=session_scope,
                            )
                        elif not self._reserve_cap_slot():
                            _decision(
                                tier="P2",
                                rule=P0_2M_SHADOW_RESEARCH_RULE,
                                ticker=ticker,
                                text=research_line,
                                delivery="telegram",
                                dedupe_key=research_key,
                                conditions=conditions_json,
                                suppressed=True,
                                suppressed_reason="daily_cap_reached",
                                lane_rule_id=INTRADAY_LANE_RULE_ID,
                                session_scope=session_scope,
                            )
                        else:
                            if tier == "P0":
                                state.p0_2m_shadow_research_delivered_today += 1
                                state.p0_2m_shadow_research_delivered_week += 1
                            else:
                                research_by_session[session_scope or ""] = (
                                    research_by_session.get(
                                        session_scope or "", 0
                                    )
                                    + 1
                                )
                                state.p0x_2m_shadow_research_delivered_week += 1
                            p0_shadow_research_lines.append(
                                IntradayAlert(
                                    rule=P0_2M_SHADOW_RESEARCH_RULE,
                                    ticker=ticker,
                                    text=research_line,
                                    dedupe_key=research_key,
                                )
                            )
                            _decision(
                                tier="P2",
                                rule=P0_2M_SHADOW_RESEARCH_RULE,
                                ticker=ticker,
                                text=research_line,
                                delivery="telegram",
                                dedupe_key=research_key,
                                conditions=conditions_json,
                                lane_rule_id=INTRADAY_LANE_RULE_ID,
                                session_scope=session_scope,
                            )
                    continue

                if not self._reserve_cap_slot():
                    _decision(
                        tier=tier,
                        rule="p0_2m_candidate",
                        ticker=ticker,
                        text=line,
                        delivery="telegram",
                        dedupe_key=key,
                        conditions=conditions_json,
                        suppressed=True,
                        suppressed_reason="daily_cap_reached",
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        session_scope=session_scope,
                    )
                    continue

                if tier == "P0":
                    state.p0_delivered_today += 1
                    state.p0_delivered_week += 1
                    p0_lines.append(
                        IntradayAlert(
                            rule="p0_2m_candidate",
                            ticker=ticker,
                            text=line,
                            dedupe_key=key,
                        )
                    )
                    if (
                        self._option_supplement_provider is not None
                        and fuse == "not_triggered"
                    ):
                        option_supplement_requests.append(
                            _OptionSupplementRequest(
                                et_date=market_date,
                                ticker=ticker,
                                direction=direction,
                                window_end=window_end,
                            )
                        )
                _decision(
                    tier=tier,
                    rule="p0_2m_candidate",
                    ticker=ticker,
                    text=line,
                    delivery="telegram",
                    dedupe_key=key,
                    conditions=conditions_json,
                    lane_rule_id=INTRADAY_LANE_RULE_ID,
                    session_scope=session_scope,
                )

            if fuse == "triggered":
                fuse_key = ("r6_fuse_notice",)
                if fuse_key not in self._seen:
                    self._seen.add(fuse_key)
                    pnl = context.daily_realized_pnl
                    fuse_line = (
                        f"R6 保险丝触线：当日已实现亏损 ${abs(pnl):,.0f} 已达宽线；"
                        "严格 2m P0/P0-X 信号仍如实入账并发送事实，"
                        "但行动性措辞与期权补充已关闭，"
                        "明日复位"
                        if pnl is not None
                        else (
                            "R6 保险丝触线；严格 2m 信号仍如实入账"
                            "并发送事实，行动性措辞与期权补充已关闭"
                        )
                    )
                    self._emit_p1(
                        decisions_builder=_decision,
                        lines=p1_lines,
                        rule="r6_fuse_notice",
                        ticker=None,
                        line=fuse_line,
                        dedupe_key=fuse_key,
                        conditions=None,
                        gate_label=None,
                        state=state,
                        safety_exempt=True,
                    )

            if not decisions:
                return None
            messages: list[_OutboundMessage] = []
            if p0_probe_lines:
                message_key = new_push_id()
                probe_key_set = set(p0_probe_keys)
                for index, record in enumerate(decisions):
                    if (
                        record.rule == "p0_2m_candidate"
                        and tuple(record.dedupe_key) in probe_key_set
                        and record.suppressed
                    ):
                        decisions[index] = replace(
                            record, message_key=message_key
                        )
                messages.append(
                    _OutboundMessage(
                        message_key=message_key,
                        tier="P0",
                        text=format_alert_message(
                            p0_probe_lines,
                            as_of=as_of,
                            header_label=_TIER_HEADERS["P0"],
                        ),
                        silent=False,
                        probe_only=True,
                        probe_dedupe_keys=tuple(p0_probe_keys),
                    )
                )
            if p0_shadow_research_lines:
                message_key = new_push_id()
                for index, record in enumerate(decisions):
                    if (
                        record.rule == P0_2M_SHADOW_RESEARCH_RULE
                        and record.delivery_channel == "telegram"
                        and not record.suppressed
                    ):
                        decisions[index] = replace(
                            record, message_key=message_key
                        )
                messages.append(
                    _OutboundMessage(
                        message_key=message_key,
                        tier="P2",
                        text=format_alert_message(
                            p0_shadow_research_lines,
                            as_of=as_of,
                            header_label=_TIER_HEADERS["P0_RESEARCH"],
                        ),
                        silent=False,
                    )
                )
            for tier, lines in (("P0", p0_lines),):
                if not lines:
                    continue
                message_key = new_push_id()
                for index, record in enumerate(decisions):
                    if (
                        record.tier == tier
                        and record.delivery_channel == "telegram"
                        and not record.suppressed
                    ):
                        decisions[index] = replace(record, message_key=message_key)
                messages.append(
                    _OutboundMessage(
                        message_key=message_key,
                        tier=tier,
                        text=format_alert_message(
                            lines,
                            as_of=as_of,
                            header_label=_TIER_HEADERS[tier],
                        ),
                        silent=False,
                    )
                )
            if p1_lines:
                message_key = new_push_id()
                for index, record in enumerate(decisions):
                    if (
                        record.tier == "P1"
                        and record.delivery_channel == "telegram_silent"
                        and not record.suppressed
                    ):
                        decisions[index] = replace(record, message_key=message_key)
                messages.append(
                    _OutboundMessage(
                        message_key=message_key,
                        tier="P1",
                        text=format_alert_message(
                            p1_lines,
                            as_of=as_of,
                            header_label=_TIER_HEADERS["P1"],
                        ),
                        silent=True,
                    )
                )
            return _WorkItem(
                et_date=market_date,
                decisions=tuple(decisions),
                messages=tuple(messages),
                option_supplements=tuple(option_supplement_requests),
            )

        # -- P1-O：顺序 1m（09:35 仍只用完整 1m 累计）的开盘发现层 ----------
        # 先收集、按画像质量排序，再应用 3/day 预算；绝不按候选遍历顺序先到
        # 先得。第一声不等待期权链，完整扫描拿到合约后可补一条静默快照。
        opening_candidates: list[
            tuple[float, str, Mapping[str, Any], str, Mapping[str, Any]]
        ] = []
        for candidate in payload.get("candidates") or ():
            if not isinstance(candidate, Mapping):
                continue
            direction = opening_probe_direction(payload, candidate)
            if direction is None:
                continue
            ticker = str(candidate.get("ticker") or "").strip().upper()
            impulse = opening_probe_profile(payload, candidate)
            if not ticker or not isinstance(impulse, Mapping):
                continue
            score = _finite(impulse.get("quality_score")) or 0.0
            opening_candidates.append(
                (score, ticker, candidate, direction, impulse)
            )
        opening_candidates.sort(key=lambda item: (-item[0], item[1]))

        for _score, ticker, candidate, direction, impulse in opening_candidates:
            key = (_OPENING_CANONICAL_DEDUPE_RULE, ticker, "09:30")
            pending_key = ("opening_option_pending", ticker)
            update_key = ("opening_option_update", ticker)
            option_contract = candidate.get("option_contract_friction")
            option_ready = (
                isinstance(option_contract, Mapping)
                and option_contract.get("state") == "ready"
            )
            confirmation_mode = str(impulse.get("confirmation_mode") or "")
            is_progressive = confirmation_mode == "consecutive_1m"
            is_completed_1m_final = (
                confirmation_mode == "completed_1m_cumulative"
            )
            rule = str(impulse.get("rule_version") or "").strip()
            if rule not in _OPENING_PRIMARY_RULES:
                rule = "opening_impulse_v2" if is_progressive else "opening_impulse_v1"
            checkpoint_label = str(
                impulse.get("checkpoint_label")
                or impulse.get("bar_end_et")
                or "09:35"
            )
            confirmation_labels = [
                str(value)
                for value in (impulse.get("confirmation_labels") or ())
            ]
            evidence_name = (
                f"开盘累计至 {checkpoint_label} 的顺序同进度异常"
                if is_progressive
                else "09:30–09:35 五根严格完整 1m 的累计异常"
            )
            evidence = [
                {
                    "condition_id": rule,
                    "name": evidence_name,
                    "status": "pass",
                    "detail": (
                        "严格研究门同向连续确认 "
                        + "/".join(confirmation_labels)
                        + "；首根 5m 尚未完成"
                        if is_progressive
                        else (
                            "五根严格完整 1m 累计证据满足研究门；"
                            "不读取原生 5m 确认"
                            if is_completed_1m_final
                            else "严格研究门全部满足；待 opening-range 后续确认"
                        )
                    ),
                    "evidence": dict(impulse) if isinstance(impulse, Mapping) else {},
                }
            ]

            if key not in self._seen:
                self._seen.add(key)
                line = self._format_opening_alert_line(
                    payload,
                    candidate,
                    direction=direction,
                    fuse=fuse,
                )
                if not opportunity_window_open:
                    _decision(
                        tier="P2",
                        rule=rule,
                        ticker=ticker,
                        text=line,
                        delivery="web_only",
                        dedupe_key=key,
                        conditions=evidence,
                        suppressed=True,
                        suppressed_reason="xnys_opportunity_window_closed",
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        option_contract=option_contract,
                    )
                    continue
                if state.opening_delivered_today >= OPENING_ALERT_DAILY_BUDGET:
                    _decision(
                        tier="P1",
                        rule=rule,
                        ticker=ticker,
                        text=line,
                        delivery="telegram",
                        dedupe_key=key,
                        conditions=evidence,
                        suppressed=True,
                        suppressed_reason="opening_budget_exhausted",
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        option_contract=option_contract,
                    )
                    continue
                if not self._reserve_cap_slot():
                    _decision(
                        tier="P1",
                        rule=rule,
                        ticker=ticker,
                        text=line,
                        delivery="telegram",
                        dedupe_key=key,
                        conditions=evidence,
                        suppressed=True,
                        suppressed_reason="daily_cap_reached",
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        option_contract=option_contract,
                    )
                    continue
                state.opening_delivered_today += 1
                opening_lines.append(
                    IntradayAlert(
                        rule=rule,
                        ticker=ticker,
                        text=line,
                        dedupe_key=key,
                    )
                )
                if is_progressive or is_completed_1m_final:
                    try:
                        checkpoint_end = datetime.fromisoformat(
                            f"{market_date}T{checkpoint_label}:00"
                        ).replace(tzinfo=_NEW_YORK)
                        candidate_expiry = checkpoint_end + timedelta(minutes=1)
                        if (
                            opening_message_expires_at is None
                            or candidate_expiry < opening_message_expires_at
                        ):
                            opening_message_expires_at = candidate_expiry
                    except ValueError:
                        # opening_probe_profile already validates the label;
                        # this is a final defensive fail-closed guard.
                        opening_message_expires_at = now
                if option_ready and fuse == "not_triggered":
                    self._seen.add(update_key)
                else:
                    self._seen.add(pending_key)
                _decision(
                    tier="P1",
                    rule=rule,
                    ticker=ticker,
                    text=line,
                    delivery="telegram",
                    dedupe_key=key,
                    conditions=evidence,
                    lane_rule_id=INTRADAY_LANE_RULE_ID,
                    option_contract=(
                        option_contract if fuse == "not_triggered" else None
                    ),
                )
                continue

            if (
                not opening_only
                and pending_key in self._seen
                and update_key not in self._seen
                and option_ready
                and fuse == "not_triggered"
            ):
                self._seen.add(update_key)
                self._seen.discard(pending_key)
                update_line = self._format_opening_option_update(
                    ticker,
                    option_contract,
                    as_of=payload.get("alert_observed_at") or payload.get("as_of"),
                )
                if not opportunity_window_open:
                    _decision(
                        tier="P2",
                        rule="opening_option_update",
                        ticker=ticker,
                        text=update_line,
                        delivery="web_only",
                        dedupe_key=update_key,
                        conditions=evidence,
                        suppressed=True,
                        suppressed_reason="xnys_opportunity_window_closed",
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        option_contract=option_contract,
                    )
                    continue
                if not self._reserve_cap_slot():
                    _decision(
                        tier="P1",
                        rule="opening_option_update",
                        ticker=ticker,
                        text=update_line,
                        delivery="telegram_silent",
                        dedupe_key=update_key,
                        conditions=evidence,
                        suppressed=True,
                        suppressed_reason="daily_cap_reached",
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        option_contract=option_contract,
                    )
                    continue
                opening_update_lines.append(
                    IntradayAlert(
                        rule="opening_option_update",
                        ticker=ticker,
                        text=update_line,
                        dedupe_key=update_key,
                    )
                )
                _decision(
                    tier="P1",
                    rule="opening_option_update",
                    ticker=ticker,
                    text=update_line,
                    delivery="telegram_silent",
                    dedupe_key=update_key,
                    conditions=evidence,
                    lane_rule_id=INTRADAY_LANE_RULE_ID,
                    option_contract=option_contract,
                )

        if opening_only:
            if not decisions:
                return None
            # 先生成消息并把 message_key 回填到 decision，再冻结 tuple。
            # Python 会从左到右求值构造参数；若在 ``tuple(decisions)`` 之后
            # 才调用 _opening_messages，首报 decision 会永久缺 message_key，
            # 成功 delivery 无法关联，失败 reservation 也无法准确释放。
            opening_messages = tuple(_opening_messages())
            return _WorkItem(
                et_date=market_date,
                decisions=tuple(decisions),
                messages=opening_messages,
            )

        # -- P2：非 legacy 上下文事实（仅页面提示流 + 台账；不推 Telegram）--
        facts = detect_intraday_alerts(payload, seen=self._seen)
        for alert in facts:
            _decision(
                tier="P2",
                rule=alert.rule,
                ticker=alert.ticker,
                text=alert.text,
                delivery="web_only",
                dedupe_key=alert.dedupe_key,
            )

        # Legacy 5m P0 is retired; historical rows are restored by
        # _rebuild_day_state, not re-evaluated by the live notification path.

        # -- P1(c)：R6 保险丝触线通告（触线本身要送达，静默一次/日）---------
        if fuse == "triggered":
            key = ("r6_fuse_notice",)
            if key not in self._seen:
                self._seen.add(key)
                pnl = context.daily_realized_pnl
                line = (
                    f"R6 保险丝触线：当日已实现亏损 ${abs(pnl):,.0f} 已达宽线；"
                    "标的 P0/P1-O 事实仍可发送，但行动性措辞和期权合约补充关闭；"
                    "明日复位（灾备记账，非预测）"
                ) if pnl is not None else (
                    "R6 保险丝触线；标的异常事实仍可发送，"
                    "行动性措辞和期权合约补充关闭"
                )
                self._emit_p1(
                    decisions_builder=_decision,
                    lines=p1_lines,
                    rule="r6_fuse_notice",
                    ticker=None,
                    line=line,
                    dedupe_key=key,
                    conditions=None,
                    gate_label=None,
                    state=state,
                    safety_exempt=True,
                )

        # -- P1(b)：同日平仓提醒（每个交易日，15:30 起；一次/日）------------
        if power_hour_window(payload):
            key = ("same_day_flatten_reminder",)
            if key not in self._seen:
                if context.open_positions is None:
                    # 服务在尾盘重启时，Telegram worker 可能先于 context
                    # provider 完成首轮刷新。标缺只用独立键记一次，绝不能
                    # 抢占正式提醒键；快照随后就绪且确有仓位时仍必须提醒。
                    missing_key = ("same_day_flatten_context_missing",)
                    if missing_key not in self._seen:
                        self._seen.add(missing_key)
                        _decision(
                            tier="P3",
                            rule="same_day_flatten_reminder",
                            ticker=None,
                            text=(
                                "同日平仓提醒：Journal 未平仓快照不可得（标缺）；"
                                "无法确认是否仍有仓位，须自行核对券商"
                            ),
                            delivery="ledger_only",
                            dedupe_key=missing_key,
                            suppressed=True,
                            suppressed_reason="open_positions_unavailable",
                        )
                elif context.open_positions:
                    self._seen.add(key)
                    held = "、".join(
                        fact.raw_symbol for fact in context.open_positions[:6]
                    )
                    text = (
                        f"同日平仓提醒：Journal 显示未平仓 {len(context.open_positions)} 笔"
                        f"（{held}）· INTRA-0-7 不允许隔夜 · 请在 "
                        f"{exit_deadline_label} 前完成平仓并核对券商"
                    )
                    self._emit_p1(
                        decisions_builder=_decision,
                        lines=p1_lines,
                        rule="same_day_flatten_reminder",
                        ticker=None,
                        line=text,
                        dedupe_key=key,
                        conditions=None,
                        gate_label=None,
                        state=state,
                        safety_exempt=True,
                    )

        if not decisions:
            return None

        # -- 出站消息组装 ----------------------------------------------------
        messages: list[_OutboundMessage] = _opening_messages()
        cap_extra: list[str] = []
        if self._cap_notice_sent and (
            opening_lines or p1_lines
        ):
            cap_extra.append(
                f"今日提示已达上限 {self._max_alerts_per_day} 条"
            )
        if p1_lines:
            message_key = new_push_id()
            for record_index, record in enumerate(decisions):
                if (
                    record.tier == "P1"
                    and record.delivery_channel == "telegram_silent"
                    and not record.suppressed
                ):
                    decisions[record_index] = replace(
                        record, message_key=message_key
                    )
            messages.append(
                _OutboundMessage(
                    message_key=message_key,
                    tier="P1",
                    text=format_alert_message(
                        p1_lines,
                        as_of=as_of,
                        extra_lines=cap_extra or None,
                        header_label=_TIER_HEADERS["P1"],
                    ),
                    silent=True,
                )
            )
        return _WorkItem(
            et_date=market_date,
            decisions=tuple(decisions),
            messages=tuple(messages),
        )

    @staticmethod
    def _format_opening_alert_line(
        payload: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        direction: str,
        fuse: str,
    ) -> str:
        """手机首报：事实、异常度与独立报价/手动时窗参考。"""

        ticker = str(candidate.get("ticker") or "").strip().upper()
        selected_profile = opening_probe_profile(payload, candidate)
        impulse = (
            selected_profile
            if isinstance(selected_profile, Mapping)
            else {}
        )
        confirmation_mode = str(impulse.get("confirmation_mode") or "")
        is_progressive = confirmation_mode == "consecutive_1m"
        is_completed_1m_final = (
            confirmation_mode == "completed_1m_cumulative"
        )
        checkpoint_label = str(
            impulse.get("checkpoint_label")
            or impulse.get("bar_end_et")
            or "09:35"
        )
        confirmation_labels = [
            str(value)
            for value in (impulse.get("confirmation_labels") or ())
        ]
        try:
            checkpoint = int(impulse.get("checkpoint") or 5)
        except (TypeError, ValueError):
            checkpoint = 5

        def number(key: str) -> Optional[float]:
            return _finite(impulse.get(key))

        open_ = number("open")
        high = number("high")
        low = number("low")
        close = number("close")
        body_percent = number("body_percent")
        body_atr = number("body_atr")
        range_atr = number("range_atr")
        close_location = number("directional_close_location")
        volume = number("volume")
        dollar_volume = number("dollar_volume")
        median_volume = number("reference_median_volume")
        rvol = number("opening_volume_rvol_20d")
        volume_pct = number("volume_percentile_20d")
        range_ratio = number("opening_range_ratio_20d")
        range_pct = number("range_percentile_20d")
        gap = number("gap_percent")
        prior_close = number("prior_close")
        closed_vs_prior = number("closed_vs_prior_percent")
        opening_mid = number("opening_mid")
        try:
            reference_count = int(
                impulse.get("reference_session_count") or 0
            )
        except (TypeError, ValueError):
            reference_count = 0
        arrow = "↑" if direction == "up" else "↓"

        def price(value: Optional[float]) -> str:
            return "标缺" if value is None else f"{value:.2f}"

        def compact_volume(value: Optional[float]) -> str:
            if value is None:
                return "标缺"
            if value >= 1_000_000:
                return f"{value / 1_000_000:.2f}M"
            if value >= 1_000:
                return f"{value / 1_000:.0f}K"
            return f"{value:.0f}"

        if is_progressive:
            confirmation_copy = (
                "/".join(confirmation_labels) or "连续两次"
            )
            headline = (
                f"{ticker} {arrow} 开盘累计 {checkpoint}m"
                f"（09:30–{checkpoint_label}）同向连续确认 "
                f"{confirmation_copy} · 严格工程门已过，尚非 P0"
            )
            reference_copy = "同进度中位"
        elif is_completed_1m_final:
            headline = (
                f"{ticker} {arrow} 开盘前 5 根完整 1m 累计异常"
                "（09:30–09:35）· 严格工程门已过，尚非 P0"
            )
            reference_copy = "同槽中位"
        else:
            # opening_probe_profile normally prevents this branch.  Keep the
            # wording fail-safe if a future producer changes its contract.
            headline = f"{ticker} {arrow} 开盘异常证据 · 尚非 P0"
            reference_copy = "同进度中位"

        lines = [
            headline,
            "价格："
            f"O {price(open_)} · H {price(high)} · L {price(low)} · C {price(close)}"
            f" · 累计中点 {price(opening_mid)}",
            "力度："
            + (
                f"实体 {body_percent:+.2f}% / {body_atr:.2f} ATR"
                if body_percent is not None and body_atr is not None
                else "实体标缺"
            )
            + (
                f" · 全幅 {range_atr:.2f} ATR"
                if range_atr is not None
                else " · 全幅标缺"
            )
            + (
                f" · 收盘位于方向端 {close_location * 100:.0f}%"
                if close_location is not None
                else " · 收盘位置标缺"
            ),
            "量能："
            f"{compact_volume(volume)}"
            + (
                f" · 估算名义额 ${compact_volume(dollar_volume)}"
                if dollar_volume is not None
                else ""
            )
            + f" · 过去 {reference_count} 日{reference_copy} "
            f"{compact_volume(median_volume)}"
            + (f" · RVOL {rvol:.2f}×" if rvol is not None else " · RVOL 标缺")
            + (
                f" / P{volume_pct * 100:.0f}"
                if volume_pct is not None
                else ""
            ),
            "历史异常度："
            + (
                f"区间为{reference_copy} {range_ratio:.2f}×"
                if range_ratio is not None
                else f"{reference_copy}区间标缺"
            )
            + (
                f" / P{range_pct * 100:.0f}"
                if range_pct is not None
                else ""
            ),
        ]
        if gap is not None and prior_close is not None:
            reclaim = impulse.get("reclaimed_prior_close") is True
            close_scope = (
                f"开盘累计至 {checkpoint_label} "
                if is_progressive or is_completed_1m_final
                else "首根"
            )
            lines.append(
                f"开盘结构：Gap {gap:+.2f}% · 前收 {prior_close:.2f} · "
                + (
                    f"{close_scope}收复前收（收盘相对前收 {closed_vs_prior:+.2f}%）"
                    if reclaim and closed_vs_prior is not None
                    else (
                        f"{close_scope}收盘相对前收 {closed_vs_prior:+.2f}%"
                        if closed_vs_prior is not None
                        else "收复状态标缺"
                    )
                )
            )

        market_context = payload.get("market_context")
        if isinstance(market_context, Mapping) and market_context.get("state") == "ready":
            spy_position = str(market_context.get("vwap_position") or "unknown")
            spy_label = {
                "above": "SPY 在会话 VWAP 上方",
                "below": "SPY 在会话 VWAP 下方",
                "flat": "SPY 贴近会话 VWAP",
            }.get(spy_position, "SPY VWAP 位置未知")
            lines.append(f"市场：{spy_label}")

        option_contract = candidate.get("option_contract_friction")
        if fuse != "not_triggered":
            lines.append(
                ("R6：当日保险丝已触发" if fuse == "triggered" else "R6：状态未知")
                + "；本条只报告标的异常事实，"
                "不展示或补充期权合约"
            )
        elif isinstance(option_contract, Mapping) and option_contract.get("state") == "ready":
            lines.append(
                "0–7DTE 报价合格参考（非执行许可）："
                + IntradayOpportunityAlerter._option_contract_copy(option_contract)
            )
        else:
            reason = (
                str(option_contract.get("unavailable_reason") or "quote_pending")
                if isinstance(option_contract, Mapping)
                else "首报不等待期权链"
            )
            lines.append(
                f"0–7DTE：暂无报价合格快照（{reason}）；首报不等待合约"
            )

        anchor_scope = (
            "累计" if is_progressive or is_completed_1m_final else "开盘证据"
        )
        if fuse == "not_triggered":
            lines.append(
                "观察锚（价格事实）："
                f"方向侧 {price(high if direction == 'up' else low)}；"
                f"{anchor_scope}中点 {price(opening_mid)} / 反侧 "
                f"{price(low if direction == 'up' else high)}"
            )
            lines.append(IntradayOpportunityAlerter._execution_policy_copy(
                intraday_execution_policy(payload.get("alert_observed_at") or payload.get("as_of"))
            ))
        status_copy = (
            "顺序开盘异常已触发；首根完整 5m 尚未完成；"
            "不等待 30 分钟位移，也不把重复刷新当新分钟确认"
            if is_progressive
            else (
                "开盘异常已由五根严格完整 1m 累计证据触发；"
                "未读取原生 5m，也不等待 30 分钟位移"
                if is_completed_1m_final
                else (
                    "开盘异常已触发；不等待 30 分钟位移，"
                    "也不把重复刷新当新 K 线确认"
                )
            )
        )
        lines.append(
            "状态："
            + status_copy
            + (
                "；R6 当日保险丝已触发，本条事实仍发送，行动性措辞/合约补充关闭"
                if fuse == "triggered"
                else ""
            )
        )
        return _tagged(ticker, "\n".join(lines))

    @staticmethod
    def _option_contract_copy(option_contract: Mapping[str, Any]) -> str:
        code = str(option_contract.get("contract_code") or "合约代码标缺")
        dte = option_contract.get("dte")
        right = str(option_contract.get("right") or "?")
        bid = _finite(option_contract.get("bid"))
        ask = _finite(option_contract.get("ask"))
        mid = _finite(option_contract.get("mid"))
        spread = _finite(option_contract.get("spread_percent"))
        delta = _finite(option_contract.get("delta"))
        age = _finite(option_contract.get("quote_age_seconds"))
        volume = option_contract.get("volume")
        open_interest = option_contract.get("open_interest")
        pieces = [f"{code} · {dte}DTE {right}"]
        if bid is not None and ask is not None and mid is not None:
            pieces.append(f"Bid/Ask/Mid ${bid:.2f}/${ask:.2f}/${mid:.2f}")
        if spread is not None:
            pieces.append(f"点差 {spread:.2f}%")
        if delta is not None:
            pieces.append(f"Delta {delta:+.3f}")
        if age is not None:
            pieces.append(f"报价龄 {age:.0f}s")
        if isinstance(volume, int) and not isinstance(volume, bool):
            pieces.append(f"当日量 {volume}")
        if isinstance(open_interest, int) and not isinstance(open_interest, bool):
            pieces.append(f"T-1 OI {open_interest}")
        return " · ".join(pieces) + " · 仅报价参考，不是执行许可"

    @staticmethod
    def _execution_policy_copy(policy: Mapping[str, Any]) -> str:
        if policy.get("state") == "unavailable":
            return "手动执行时窗：交易时钟未知，不能确认符合执行规则"
        if policy.get("close_et") is None:
            return "手动执行时窗：非 XNYS 交易日，关闭"
        ordinary = "窗内" if policy.get("ordinary_entry_allowed") is True else "关闭"
        zero = "窗内" if policy.get("zero_dte_entry_allowed") is True else "关闭"
        return (
            f"手动执行时窗：1–7DTE {ordinary} / 0DTE {zero}；"
            f"起点 {policy.get('entry_start_et')} ET · "
            f"1–7DTE 截止 {policy.get('ordinary_entry_cutoff_et')} · "
            f"0DTE 截止 {policy.get('zero_dte_entry_cutoff_et')} · "
            f"同日退出 {policy.get('exit_deadline_et')} ET；仅时钟核对，非交易许可"
        )

    @staticmethod
    def _format_opening_option_update(
        ticker: str,
        option_contract: Mapping[str, Any],
        *,
        as_of: Any = None,
    ) -> str:
        return (
            f"{ticker} 开盘 setup 的 0–7DTE 合约快照已就绪："
            f"{IntradayOpportunityAlerter._option_contract_copy(option_contract)}；"
            "这是报价合格参考，不是执行许可，也不代表 opening range 已突破"
            + "\n" + IntradayOpportunityAlerter._execution_policy_copy(
                intraday_execution_policy(as_of)
            )
        )

    def _emit_p1(
        self,
        *,
        decisions_builder: Callable[..., Any],
        lines: list[IntradayAlert],
        rule: str,
        ticker: Optional[str],
        line: str,
        dedupe_key: tuple[str, ...],
        conditions: Optional[list[dict[str, Any]]],
        gate_label: Optional[str],
        state: TierEngineState,
        option_contract: Optional[Mapping[str, Any]] = None,
        lane_rule_id: Optional[str] = None,
        safety_exempt: bool = False,
    ) -> None:
        """Emit a silent P1.

        Opportunity near-misses obey the P1/day and global alert budgets.
        Safety notices (R6 fuse and the 15:30 same-day flatten reminder) pass
        ``safety_exempt=True`` so earlier opportunities cannot consume the
        notification capacity needed to reduce an open-position risk.
        """
        if self._send_silent_text is None:
            decisions_builder(
                tier="P1",
                rule=rule,
                ticker=ticker,
                text=line,
                delivery="web_only",
                dedupe_key=dedupe_key,
                conditions=conditions,
                suppressed=True,
                suppressed_reason="silent_channel_unavailable",
                gate_label=gate_label,
                option_contract=option_contract,
                lane_rule_id=lane_rule_id,
            )
            return
        if not safety_exempt and state.p1_delivered_today >= P1_DAILY_BUDGET:
            decisions_builder(
                tier="P1",
                rule=rule,
                ticker=ticker,
                text=line,
                delivery="telegram_silent",
                dedupe_key=dedupe_key,
                conditions=conditions,
                suppressed=True,
                suppressed_reason="p1_budget_exhausted",
                gate_label=gate_label,
                option_contract=option_contract,
                lane_rule_id=lane_rule_id,
            )
            return
        if not safety_exempt and not self._reserve_cap_slot():
            decisions_builder(
                tier="P1",
                rule=rule,
                ticker=ticker,
                text=line,
                delivery="telegram_silent",
                dedupe_key=dedupe_key,
                conditions=conditions,
                suppressed=True,
                suppressed_reason="daily_cap_reached",
                gate_label=gate_label,
                option_contract=option_contract,
                lane_rule_id=lane_rule_id,
            )
            return
        if not safety_exempt:
            state.p1_delivered_today += 1
        lines.append(
            IntradayAlert(rule=rule, ticker=ticker, text=line, dedupe_key=dedupe_key)
        )
        decisions_builder(
            tier="P1",
            rule=rule,
            ticker=ticker,
            text=line,
            delivery="telegram_silent",
            dedupe_key=dedupe_key,
            conditions=conditions,
            gate_label=gate_label,
            option_contract=option_contract,
            lane_rule_id=lane_rule_id,
        )

    def _reserve_cap_slot(self) -> bool:
        """全局 Telegram 行上限（legacy ``INTRADAY_ALERTS_MAX_PER_DAY``）。

        P0/P1 各自的硬预算远小于该上限，正常永不触顶；触顶时行为与旧版
        一致：随首个越界批附一条上限说明，之后当日 telegram 出口关闭
        （P2/P3 台账照常）。
        """
        if self._sent_count >= self._max_alerts_per_day:
            self._cap_notice_sent = True
            return False
        self._sent_count += 1
        if self._sent_count >= self._max_alerts_per_day:
            self._cap_notice_sent = True
        return True

    # -- delivery ----------------------------------------------------------
    def _dispatch(self, item: _WorkItem, *, durable: bool = False) -> bool:
        if not self._async_send:
            return self._process(item)
        if not self.running:
            self.start()
        try:
            # Serialize every producer (including system notices).  Once a
            # slot is available, a consumer can only free more capacity while
            # strict-2m decisions/outbox are committed before acceptance.
            with self._lock:
                if self._queue.full():
                    raise queue.Full
                if durable:
                    prepared = self._persist_work_item(item)
                    if prepared is None:
                        return False
                    item = prepared
                self._queue.put_nowait(item)
            # D7：成功入队即清除 queue 族指纹——每**次**新的拥塞事件都要
            # 记到日志（否则第一次拥塞之后的重复丢弃全部不可见）。
            self._last_fingerprints.pop("queue", None)
            return True
        except queue.Full:
            self._note_state(
                "queue:full",
                logging.WARNING,
                "[intraday-alerts] send queue full; dropping this tick's "
                "work item (dedupe keys rolled back so facts can re-detect)",
            )
            return False

    def _drain(self) -> None:
        # Shutdown is graceful for work already accepted by ``_dispatch``:
        # setting the stop flag prevents another idle wait, but cannot discard
        # queued decisions/messages that the caller was already told were
        # accepted.  The lifecycle join remains bounded by ``stop``.
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(
                    timeout=0.1 if self._stop_event.is_set() else 0.5
                )
            except queue.Empty:
                continue
            self._process(item)

    def _utc_now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _persist_delivery_retry_candidate(
        self,
        item: _WorkItem,
        message: _OutboundMessage,
        *,
        now: datetime,
    ) -> str:
        """Persist before send and return a small explicit state."""

        writer = getattr(self._ledger, "upsert_retry", None)
        if (
            not callable(writer)
            or not self._async_send
            or message.expires_at is not None
            or not self._delivery_retry_delays
            or self._delivery_retry_ttl_seconds <= 0.0
        ):
            return "not_required"
        deadline = self._retry_deadline(message, now)
        try:
            stored = writer(
                message_key=message.message_key,
                et_date=item.et_date,
                tier=message.tier,
                text=message.text,
                silent=message.silent,
                expires_at=deadline,
                next_attempt_at=(
                    now
                    + timedelta(seconds=self._delivery_retry_delays[0])
                ),
                attempt_count=0,
                now=now,
            )
            if str(getattr(stored, "state", "")) != "pending":
                return "already_final"
        except Exception as exc:  # noqa: BLE001 - immediate send still works
            self._note_state(
                f"delivery_retry:persist_failed:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] retry outbox persist failed; using "
                "in-process retry only error_type=%s",
                type(exc).__name__,
            )
            return "failed"
        self._delivery_retry_persistent_keys.add(message.message_key)
        return "pending"

    def _mark_delivery_retry_success(
        self,
        message_key: str,
        *,
        now: datetime,
        delivered_text: Optional[str] = None,
    ) -> None:
        marker = getattr(self._ledger, "mark_retry_success", None)
        if message_key in self._delivery_retry_persistent_keys and callable(marker):
            try:
                marker(
                    message_key,
                    now=now,
                    delivered_text=delivered_text,
                )
            except Exception as exc:  # noqa: BLE001
                self._note_state(
                    f"delivery_retry:success_mark_failed:{type(exc).__name__}",
                    logging.WARNING,
                    "[intraday-alerts] retry outbox success mark failed "
                    "error_type=%s",
                    type(exc).__name__,
                )
                return
        self._delivery_retry_persistent_keys.discard(message_key)

    def _mark_delivery_retry_terminal(
        self, message_key: str, *, now: datetime
    ) -> None:
        marker = getattr(self._ledger, "mark_retry_terminal", None)
        if message_key in self._delivery_retry_persistent_keys and callable(marker):
            try:
                marker(message_key, now=now)
            except Exception as exc:  # noqa: BLE001
                self._note_state(
                    f"delivery_retry:terminal_mark_failed:{type(exc).__name__}",
                    logging.WARNING,
                    "[intraday-alerts] retry outbox terminal mark failed "
                    "error_type=%s",
                    type(exc).__name__,
                )
                return
        self._delivery_retry_persistent_keys.discard(message_key)

    def _restore_delivery_retries(self) -> None:
        """Recover unexpired retry jobs before worker threads start."""

        load_pending = getattr(self._ledger, "load_pending_retries", None)
        if not callable(load_pending):
            return
        now = self._utc_now()
        has_success = getattr(
            self._ledger, "message_has_successful_delivery", None
        )
        load_succeeded = getattr(
            self._ledger, "load_succeeded_retries", None
        )
        if callable(load_succeeded) and callable(has_success):
            try:
                succeeded_rows = tuple(load_succeeded())
            except Exception as exc:  # noqa: BLE001
                succeeded_rows = ()
                self._note_state(
                    f"delivery_retry:success_load_failed:{type(exc).__name__}",
                    logging.WARNING,
                    "[intraday-alerts] succeeded retry outbox read failed "
                    "error_type=%s",
                    type(exc).__name__,
                )
            for stored in succeeded_rows:
                try:
                    receipt_exists = has_success(
                        str(stored.et_date), str(stored.message_key)
                    )
                except Exception as exc:  # noqa: BLE001
                    self._note_state(
                        "delivery_retry:success_receipt_check_failed:"
                        f"{type(exc).__name__}",
                        logging.WARNING,
                        "[intraday-alerts] success receipt recovery check "
                        "failed error_type=%s",
                        type(exc).__name__,
                    )
                    continue
                if receipt_exists:
                    continue
                self._append_delivery(
                    str(stored.et_date),
                    _OutboundMessage(
                        message_key=str(stored.message_key),
                        tier=str(stored.tier),
                        text=str(stored.text),
                        silent=bool(stored.silent),
                    ),
                    True,
                )
        load_expired = getattr(self._ledger, "load_expired_retries", None)
        if callable(load_expired):
            try:
                expired = tuple(load_expired(now=now))
            except Exception as exc:  # noqa: BLE001
                expired = ()
                self._note_state(
                    f"delivery_retry:expired_load_failed:{type(exc).__name__}",
                    logging.WARNING,
                    "[intraday-alerts] expired retry outbox read failed "
                    "error_type=%s",
                    type(exc).__name__,
                )
            for stored in expired:
                message = _OutboundMessage(
                    message_key=str(stored.message_key),
                    tier=str(stored.tier),
                    text=str(stored.text),
                    silent=bool(stored.silent),
                )
                self._delivery_retry_persistent_keys.add(message.message_key)
                if self._append_delivery(
                    str(stored.et_date), message, False
                ):
                    self._mark_delivery_retry_terminal(
                        message.message_key, now=now
                    )
        try:
            pending_rows = tuple(load_pending(now=now))
        except Exception as exc:  # noqa: BLE001
            self._note_state(
                f"delivery_retry:restore_failed:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] retry outbox restore failed "
                "error_type=%s",
                type(exc).__name__,
            )
            return
        load_decisions = getattr(self._ledger, "load_message_decisions", None)
        for stored in pending_rows[: self._delivery_retry_queue_maxsize]:
            message_key = str(stored.message_key)
            if callable(has_success):
                try:
                    if has_success(str(stored.et_date), message_key):
                        self._delivery_retry_persistent_keys.add(message_key)
                        self._mark_delivery_retry_success(message_key, now=now)
                        continue
                except Exception:  # noqa: BLE001 - recovery remains conservative
                    pass
            decisions = ()
            if callable(load_decisions):
                try:
                    decisions = tuple(
                        load_decisions(str(stored.et_date), message_key)
                    )
                except Exception:  # noqa: BLE001
                    decisions = ()
            message = _OutboundMessage(
                message_key=message_key,
                tier=str(stored.tier),
                text=str(stored.text),
                silent=bool(stored.silent),
            )
            item = _WorkItem(
                et_date=str(stored.et_date),
                decisions=decisions,
                messages=(message,),
            )
            due_seconds = max(
                0.0,
                (stored.next_attempt_at - now).total_seconds(),
            )
            self._delivery_retry_sequence += 1
            heapq.heappush(
                self._delivery_retry_heap,
                _DeliveryRetry(
                    due_monotonic=time_module.monotonic() + due_seconds,
                    sequence=self._delivery_retry_sequence,
                    item=item,
                    message=message,
                    first_failed_at=stored.created_at,
                    deadline=stored.expires_at,
                    retry_index=max(0, int(stored.attempt_count) - 1),
                ),
            )
            self._delivery_retry_pending_keys.add(message_key)
            self._delivery_retry_persistent_keys.add(message_key)
        if pending_rows:
            logger.info(
                "[intraday-alerts] restored %d pending Telegram retries",
                min(
                    len(pending_rows), self._delivery_retry_queue_maxsize
                ),
            )

    def _retry_deadline(
        self,
        message: _OutboundMessage,
        first_failed_at: datetime,
    ) -> datetime:
        deadline = first_failed_at + timedelta(
            seconds=self._delivery_retry_ttl_seconds
        )
        if message.expires_at is not None:
            expiry = message.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            else:
                expiry = expiry.astimezone(timezone.utc)
            deadline = min(deadline, expiry)
        return deadline

    def _schedule_delivery_retry(
        self,
        item: _WorkItem,
        message: _OutboundMessage,
        *,
        first_failed_at: datetime,
        deadline: Optional[datetime] = None,
        retry_index: int = 0,
        already_pending: bool = False,
    ) -> bool:
        """Schedule one bounded retry without blocking the scan/send worker."""

        if (
            not self._async_send
            or self._stop_event.is_set()
            # Opening checkpoints are valid for one exact wall-clock minute.
            # The sender already has its own short HTTP retry chain and a
            # process-level delayed attempt cannot guarantee completion before
            # this hard expiry, so never enqueue one here.
            or message.expires_at is not None
            or retry_index >= len(self._delivery_retry_delays)
            or self._delivery_retry_ttl_seconds <= 0.0
        ):
            return False
        delay = self._delivery_retry_delays[retry_index]
        now = self._utc_now()
        hard_deadline = deadline or self._retry_deadline(
            message, first_failed_at
        )
        if hard_deadline.tzinfo is None:
            hard_deadline = hard_deadline.replace(tzinfo=timezone.utc)
        else:
            hard_deadline = hard_deadline.astimezone(timezone.utc)
        due_at = now + timedelta(seconds=delay)
        # A retry due exactly at expiry is already stale.  This is especially
        # important for the minute-scoped 09:32--09:34 opening checkpoints.
        if due_at >= hard_deadline:
            return False
        with self._delivery_retry_condition:
            if (
                not already_pending
                and message.message_key in self._delivery_retry_pending_keys
            ):
                return True
            if len(self._delivery_retry_heap) >= self._delivery_retry_queue_maxsize:
                self._note_state(
                    "delivery_retry:queue_full",
                    logging.WARNING,
                    "[intraday-alerts] delivery retry queue full; message "
                    "reached terminal failure",
                )
                return False
            if message.message_key in self._delivery_retry_persistent_keys:
                reschedule = getattr(self._ledger, "reschedule_retry", None)
                if callable(reschedule):
                    try:
                        if not reschedule(
                            message.message_key,
                            next_attempt_at=due_at,
                            now=now,
                        ):
                            return False
                    except Exception as exc:  # noqa: BLE001
                        self._note_state(
                            "delivery_retry:reschedule_failed:"
                            f"{type(exc).__name__}",
                            logging.WARNING,
                            "[intraday-alerts] retry outbox reschedule failed; "
                            "continuing in-process error_type=%s",
                            type(exc).__name__,
                        )
            self._delivery_retry_sequence += 1
            heapq.heappush(
                self._delivery_retry_heap,
                _DeliveryRetry(
                    due_monotonic=time_module.monotonic() + delay,
                    sequence=self._delivery_retry_sequence,
                    item=item,
                    message=message,
                    first_failed_at=first_failed_at,
                    deadline=hard_deadline,
                    retry_index=retry_index,
                ),
            )
            self._delivery_retry_pending_keys.add(message.message_key)
            self._delivery_retry_condition.notify()
        self._note_state(
            "delivery_retry:scheduled",
            logging.WARNING,
            "[intraday-alerts] telegram message queued for bounded retry",
        )
        return True

    def _finish_delivery_retry(self, message_key: str) -> None:
        with self._delivery_retry_condition:
            self._delivery_retry_pending_keys.discard(message_key)

    def _drain_delivery_retries(self) -> None:
        """Deliver due retries; stop wakes immediately instead of waiting TTL."""

        while True:
            with self._delivery_retry_condition:
                while (
                    not self._delivery_retry_heap
                    and not self._stop_event.is_set()
                ):
                    self._delivery_retry_condition.wait(timeout=0.5)
                if self._stop_event.is_set():
                    return
                pending = self._delivery_retry_heap[0]
                wait_seconds = pending.due_monotonic - time_module.monotonic()
                if wait_seconds > 0.0:
                    self._delivery_retry_condition.wait(timeout=wait_seconds)
                    continue
                heapq.heappop(self._delivery_retry_heap)

            try:
                now = self._utc_now()
                if now >= pending.deadline:
                    appended = self._append_delivery(
                        pending.item.et_date,
                        pending.message,
                        False,
                    )
                    if appended:
                        self._mark_delivery_retry_terminal(
                            pending.message.message_key,
                            now=now,
                        )
                    self._finish_delivery_retry(pending.message.message_key)
                    if appended:
                        self._release_failed_reservations(
                            pending.item, pending.message
                        )
                    continue
                elapsed_seconds = max(
                    0,
                    int((now - pending.first_failed_at).total_seconds()),
                )
                retry_message = replace(
                    pending.message,
                    text=(
                        "⚠️ Telegram 网络延迟补发（约 "
                        f"{elapsed_seconds}s）；以正文数据时间为准，"
                        "信号可能已失效，勿追价。\n"
                        f"{pending.message.text}"
                    ),
                )
                ok = self._deliver(retry_message, deadline=pending.deadline)
                if ok is True:
                    self._mark_delivery_retry_success(
                        pending.message.message_key,
                        now=self._utc_now(),
                        delivered_text=retry_message.text,
                    )
                    self._append_delivery(
                        pending.item.et_date, retry_message, True
                    )
                    self._finish_delivery_retry(pending.message.message_key)
                    if pending.message.tier == "P0":
                        for request in pending.item.option_supplements:
                            self._schedule_option_supplement(request)
                    continue
                if ok is None:
                    # Outcome unknown means the HTTP response may have been
                    # lost after Telegram accepted the message.  Stop here:
                    # an automatic retry would create a duplicate phone ring.
                    self._mark_delivery_retry_terminal(
                        pending.message.message_key,
                        now=self._utc_now(),
                    )
                    self._finish_delivery_retry(pending.message.message_key)
                    continue
                scheduled = self._schedule_delivery_retry(
                    pending.item,
                    pending.message,
                    first_failed_at=pending.first_failed_at,
                    deadline=pending.deadline,
                    retry_index=pending.retry_index + 1,
                    already_pending=True,
                )
                if scheduled:
                    continue
                appended = self._append_delivery(
                    pending.item.et_date,
                    retry_message,
                    False,
                )
                if appended:
                    self._mark_delivery_retry_terminal(
                        pending.message.message_key,
                        now=self._utc_now(),
                    )
                self._finish_delivery_retry(pending.message.message_key)
                if appended:
                    self._release_failed_reservations(
                        pending.item, pending.message
                    )
            except Exception:  # noqa: BLE001 - retry worker must stay alive
                logger.warning(
                    "[intraday-alerts] delivery retry worker failed; "
                    "pending outbox retained for restart recovery",
                    exc_info=True,
                )
                self._finish_delivery_retry(pending.message.message_key)

    def _persist_work_item(
        self,
        item: _WorkItem,
        *,
        decisions: Optional[tuple[Any, ...]] = None,
    ) -> Optional[_WorkItem]:
        """Commit decisions and recoverable messages before accepting a window."""

        if item.persisted_at is not None:
            return item
        effective_decisions = item.decisions if decisions is None else decisions
        persisted_at = self._utc_now()
        atomic_outbox_states: dict[str, str] = {}
        retryable_messages = tuple(
            message
            for message in item.messages
            if not message.probe_only
            and self._async_send
            and message.expires_at is None
            and bool(self._delivery_retry_delays)
            and self._delivery_retry_ttl_seconds > 0.0
        )
        atomic_writer = getattr(self._ledger, "append_with_retries", None)
        if retryable_messages and callable(atomic_writer):
            retry_specs = []
            for message in retryable_messages:
                retry_specs.append(
                    {
                        "message_key": message.message_key,
                        "et_date": item.et_date,
                        "tier": message.tier,
                        "text": message.text,
                        "silent": message.silent,
                        "expires_at": self._retry_deadline(
                            message, persisted_at
                        ),
                        "next_attempt_at": (
                            persisted_at
                            + timedelta(
                                seconds=self._delivery_retry_delays[0]
                            )
                        ),
                        "attempt_count": 0,
                    }
                )
            try:
                stored_rows = tuple(
                    atomic_writer(
                        effective_decisions,
                        tuple(retry_specs),
                        now=persisted_at,
                    )
                )
                if len(stored_rows) != len(retryable_messages):
                    raise RuntimeError("retry outbox row-count mismatch")
            except Exception as exc:  # noqa: BLE001 - fail closed before send
                self._note_state(
                    f"ledger:failed:{type(exc).__name__}",
                    logging.WARNING,
                    "[intraday-alerts] atomic decision/outbox append failed "
                    "error_type=%s; external send suppressed",
                    type(exc).__name__,
                )
                return None
            for message, stored in zip(retryable_messages, stored_rows):
                state = str(getattr(stored, "state", ""))
                atomic_outbox_states[message.message_key] = state
                if state == "pending":
                    self._delivery_retry_persistent_keys.add(
                        message.message_key
                    )
            self._note_state(
                "ledger:ok",
                logging.INFO,
                "[intraday-alerts] push ledger healthy",
            )
        elif not self._append_ledger(decisions=effective_decisions):
            # decision 若未能持久化就禁止外部发送；否则进程在 Telegram
            # 成功后重启时没有任何去重/预算证据，会制造重复 P0。
            return None
        return replace(
            item,
            persisted_at=persisted_at,
            outbox_states=tuple(atomic_outbox_states.items()),
        )

    def _process(self, item: _WorkItem) -> bool:
        # Early sequential checkpoints are useful only inside their exact
        # wall-clock minute.  Telegram retries or an older queued message must
        # never turn a 09:32 observation into a late 09:34 phone ring.
        now = self._utc_now()
        expired_keys = {
            message.message_key
            for message in item.messages
            if self._async_send
            and message.expires_at is not None
            and now >= message.expires_at.astimezone(timezone.utc)
        }
        effective_decisions = tuple(
            replace(
                decision,
                suppressed=True,
                suppressed_reason="opening_checkpoint_expired_before_send",
            )
            if (
                getattr(decision, "message_key", None) in expired_keys
                and not bool(getattr(decision, "suppressed", False))
            )
            else decision
            for decision in item.decisions
        )
        prepared = self._persist_work_item(item, decisions=effective_decisions)
        if prepared is None:
            for message in item.messages:
                if not message.probe_only:
                    self._release_failed_reservations(item, message)
            return False
        item = prepared
        first_attempt_at_by_key = {
            key: item.persisted_at for key, _state in item.outbox_states
        }
        atomic_outbox_states = dict(item.outbox_states)
        p0_primary_delivered = False
        for message in item.messages:
            if message.probe_only:
                # Counterfactual live-shadow probes prove bounded-queue
                # acceptance only.  They never call Telegram, never create a
                # delivery row and never consume a real notification budget.
                continue
            if message.message_key in expired_keys:
                self._note_state(
                    "opening:expired_before_send",
                    logging.WARNING,
                    "[intraday-alerts] stale opening checkpoint suppressed "
                    "before Telegram delivery",
                )
                self._release_failed_reservations(item, message)
                continue
            first_attempt_at = first_attempt_at_by_key.get(
                message.message_key
            )
            if first_attempt_at is None:
                first_attempt_at = self._utc_now()
            stored_state = atomic_outbox_states.get(message.message_key)
            if stored_state is not None:
                outbox_state = (
                    "pending"
                    if stored_state == "pending"
                    else "already_final"
                )
            else:
                outbox_state = self._persist_delivery_retry_candidate(
                    item,
                    message,
                    now=first_attempt_at,
                )
            if outbox_state == "already_final":
                self._note_state(
                    "delivery_retry:already_final",
                    logging.INFO,
                    "[intraday-alerts] Telegram message already finalized; "
                    "duplicate external send suppressed",
                )
                continue
            if outbox_state == "failed":
                # A real non-opening alert must be recoverable before it can
                # leave the process.  Since no request was made, a definitive
                # false receipt safely releases this reservation.
                if self._append_delivery(item.et_date, message, False):
                    self._release_failed_reservations(item, message)
                continue
            hard_deadline = self._retry_deadline(message, first_attempt_at)
            if outbox_state == "pending" and self._utc_now() >= hard_deadline:
                if self._append_delivery(item.et_date, message, False):
                    self._mark_delivery_retry_terminal(
                        message.message_key, now=self._utc_now(),
                    )
                    self._release_failed_reservations(item, message)
                continue
            ok = self._deliver(
                message,
                deadline=hard_deadline if outbox_state == "pending" else None,
            )
            if ok is True:
                self._mark_delivery_retry_success(
                    message.message_key,
                    now=self._utc_now(),
                    delivered_text=message.text,
                )
                self._append_delivery(item.et_date, message, True)
                if message.tier == "P0":
                    p0_primary_delivered = True
                continue
            if ok is None:
                # The request may have reached Telegram even though its HTTP
                # response did not reach us.  Finalize the outbox without a
                # false receipt or retry to avoid duplicate phone rings.
                self._mark_delivery_retry_terminal(
                    message.message_key,
                    now=self._utc_now(),
                )
                continue
            if self._schedule_delivery_retry(
                item,
                message,
                first_failed_at=first_attempt_at,
                deadline=hard_deadline,
            ):
                continue
            if (
                self._stop_event.is_set()
                and message.message_key
                in self._delivery_retry_persistent_keys
            ):
                # Graceful restart: leave the durable row pending so the new
                # process can recover it instead of declaring a false failure.
                continue
            if self._append_delivery(item.et_date, message, False):
                self._mark_delivery_retry_terminal(
                    message.message_key,
                    now=self._utc_now(),
                )
                self._release_failed_reservations(item, message)
        if p0_primary_delivered:
            for request in item.option_supplements:
                self._schedule_option_supplement(request)
        return True

    def _schedule_option_supplement(
        self, request: _OptionSupplementRequest
    ) -> None:
        """Queue post-primary option work without blocking the first alert."""

        if self._option_supplement_provider is None:
            return
        if not self._async_send:
            self._process_option_supplement(request)
            return
        try:
            self._option_queue.put_nowait(request)
        except queue.Full:
            self._note_state(
                "option_supplement:queue_full",
                logging.WARNING,
                "[intraday-alerts] option supplement queue full; primary P0 "
                "remains delivered",
            )
            self._record_option_supplement_unavailable(
                request,
                reason="option_supplement_queue_full",
            )

    def _record_option_supplement_unavailable(
        self,
        request: _OptionSupplementRequest,
        *,
        reason: str,
    ) -> None:
        """Persist a visible P2 failure without blocking the primary P0.

        This path intentionally does not start an unbounded fallback thread or
        retry a saturated queue.  The underlying signal is already delivered;
        the web feed records why no option snapshot followed.
        """

        try:
            from src.journal.ledger.push_ledger_repository import (
                PushDecisionInput,
                new_push_id,
            )

            now = self._now_provider()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            else:
                now = now.astimezone(timezone.utc)
            text = (
                f"{request.ticker} R6 状态已改变：合约补充关闭（{reason}）；"
                "仅保留标的 P0 事实"
                if reason.startswith("r6_") else
                f"{request.ticker} 暂无报价合格期权快照（{reason}）；"
                "标的 P0 不撤销"
            )
            self._append_ledger(
                decisions=(
                    PushDecisionInput(
                        push_id=new_push_id(),
                        et_date=request.et_date,
                        ts=now,
                        tier="P2",
                        rule="p0_2m_option_update",
                        ticker=request.ticker,
                        text=text,
                        delivery_channel="web_only",
                        dedupe_key=(
                            "p0_2m_option_update_unavailable",
                            request.ticker,
                            request.direction,
                            request.window_end,
                            reason,
                        ),
                        conditions=[
                            {
                                "condition_id": "option_candidates",
                                "name": "0–7DTE 方向匹配候选",
                                "status": "missing",
                                "detail": reason,
                                "option_candidates": [],
                            }
                        ],
                        lane_rule_id=INTRADAY_LANE_RULE_ID,
                        suppressed=False,
                        suppressed_reason=None,
                        session_scope="regular",
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - primary P0 remains valid
            self._note_state(
                f"option_supplement:failure_record:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] option supplement failure record could "
                "not be assembled error_type=%s",
                type(exc).__name__,
            )

    def _drain_option_supplements(self) -> None:
        while not self._stop_event.is_set() or not self._option_queue.empty():
            try:
                request = self._option_queue.get(
                    timeout=0.1 if self._stop_event.is_set() else 0.5
                )
            except queue.Empty:
                continue
            self._process_option_supplement(request)

    def _process_option_supplement(
        self, request: _OptionSupplementRequest
    ) -> None:
        """Fetch and silently append up to three direction-matched contracts."""

        provider = self._option_supplement_provider
        if provider is None:
            return
        with self._lock:
            fuse = resolve_fuse_state(self._context, self._tier_state)
        if fuse != "not_triggered":
            self._record_option_supplement_unavailable(
                request, reason=f"r6_{fuse}:contract_context_withheld",
            )
            return
        try:
            supplement = provider(request.ticker, request.direction)
        except Exception as exc:  # noqa: BLE001 - never revoke primary signal
            supplement = {
                "state": "unavailable",
                "unavailable_reason": f"option_supplement_failed:{type(exc).__name__}",
                "candidates": [],
            }
        if not isinstance(supplement, Mapping):
            supplement = {
                "state": "unavailable",
                "unavailable_reason": "option_supplement_payload_invalid",
                "candidates": [],
            }
        # The risk snapshot may change while an asynchronous chain read waits.
        # Never leak contract context after the first factual alert was sent.
        with self._lock:
            fuse = resolve_fuse_state(self._context, self._tier_state)
        if fuse != "not_triggered":
            self._record_option_supplement_unavailable(
                request, reason=f"r6_{fuse}:contract_context_withheld",
            )
            return
        policy = intraday_execution_policy(self._now_provider())
        raw_candidates = supplement.get("candidates")
        if not isinstance(raw_candidates, Sequence) or isinstance(
            raw_candidates, (str, bytes)
        ):
            raw_candidates = ()
        candidates = [
            dict(row)
            for row in raw_candidates[:3]
            if isinstance(row, Mapping)
        ]
        for candidate in candidates:
            candidate["execution_allowed"] = policy[
                "zero_dte_entry_allowed"
                if candidate.get("dte") == 0 else "ordinary_entry_allowed"
            ]
        lines = [
            f"{request.ticker} { _DIRECTION_LABELS.get(request.direction, request.direction) }"
            f" · 标的 P0 数据时间 {request.window_end}"
        ]
        if not candidates:
            reason = str(
                supplement.get("unavailable_reason")
                or "no_executable_option_snapshot"
            )
            lines.append(f"暂无报价合格期权快照（{reason}）；标的 P0 不撤销")
        else:
            for row in candidates:
                code = str(row.get("contract_code") or "")
                expiry = str(row.get("expiry") or "")
                dte = row.get("dte")
                right = str(row.get("right") or "")
                strike = _finite(row.get("strike"))
                mid = _finite(row.get("mid"))
                spread = _finite(row.get("spread_percent"))
                delta = _finite(row.get("delta"))
                lines.append(
                    f"{expiry} · {dte}DTE {right} · {code} · "
                    f"Strike ${strike:.2f} · Mid ${mid:.2f} · "
                    f"点差 {spread:.2f}% · Delta {delta:+.3f} · 仅报价合格参考"
                    if None not in (strike, mid, spread, delta)
                    else f"{expiry} · {dte}DTE · {code}（报价字段标缺）"
                )
        lines.append(self._execution_policy_copy(policy))
        lines.append("报价合格不等于可执行；仍需核对风险、事件与券商实时盘口")
        text = format_alert_message(
            [
                IntradayAlert(
                    rule="p0_2m_option_update",
                    ticker=request.ticker,
                    text="\n".join(lines),
                )
            ],
            as_of=self._now_provider().isoformat(),
            header_label="P0 0–7DTE 候选补充 · 静默",
        )
        try:
            from src.journal.ledger.push_ledger_repository import (
                PushDecisionInput,
                PushDeliveryInput,
                new_push_id,
            )

            now = self._now_provider()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            else:
                now = now.astimezone(timezone.utc)
            message_key = new_push_id()
            decision = PushDecisionInput(
                push_id=new_push_id(),
                et_date=request.et_date,
                ts=now,
                tier="P2",
                rule="p0_2m_option_update",
                ticker=request.ticker,
                text=text,
                delivery_channel="telegram_silent",
                dedupe_key=(
                    "p0_2m_option_update",
                    request.ticker,
                    request.direction,
                    request.window_end,
                ),
                conditions=[
                    {
                        "condition_id": "option_candidates",
                        "name": "0–7DTE 方向匹配候选",
                        "status": "pass" if candidates else "missing",
                        "detail": str(
                            supplement.get("unavailable_reason") or "ready"
                        ),
                        "option_candidates": candidates,
                        "execution_policy": policy,
                    }
                ],
                lane_rule_id=INTRADAY_LANE_RULE_ID,
                message_key=message_key,
                session_scope="regular",
            )
            if not self._append_ledger(decisions=(decision,)):
                return
            outbound = _OutboundMessage(
                message_key=message_key,
                tier="P2",
                text=text,
                silent=True,
            )
            ok = self._deliver(outbound)
            delivery = PushDeliveryInput(
                push_id=new_push_id(),
                et_date=request.et_date,
                ts=self._now_provider(),
                tier="P2",
                message_key=message_key,
                telegram_ok=ok,
                text=text,
                channel="telegram_silent",
            )
            self._append_ledger(deliveries=(delivery,))
        except Exception:  # noqa: BLE001 - primary signal already delivered
            logger.warning(
                "[intraday-alerts] option supplement persistence/send failed",
                exc_info=True,
            )

    def _release_failed_reservations(
        self,
        item: _WorkItem,
        message: _OutboundMessage,
    ) -> None:
        """释放确定失败消息预占的机会预算，但保留事实去重。

        Telegram sender 自身已经完成有界 HTTP 重试；这里处理最终 ``False``。
        decision/delivery 仍以 append-only 方式保留完整失败证据，同一事实不在
        每个 warm tick 重复骚扰；只让本日后续**不同**机会继续有预算。
        """

        matching = [
            decision
            for decision in item.decisions
            if getattr(decision, "message_key", None) == message.message_key
            and not bool(getattr(decision, "suppressed", False))
        ]
        reserved = [
            decision
            for decision in matching
            if getattr(decision, "delivery_channel", None)
            in {"telegram", "telegram_silent"}
            and getattr(decision, "tier", None) != "system"
            and getattr(decision, "rule", None) not in _SAFETY_EXEMPT_RULES
        ]
        if not reserved:
            return

        opening_count = sum(
            getattr(decision, "rule", None) in _OPENING_PRIMARY_RULES
            for decision in reserved
        )
        p0_count = sum(
            getattr(decision, "tier", None) == "P0"
            for decision in reserved
        )
        p0x_reserved = [
            decision
            for decision in reserved
            if getattr(decision, "tier", None) == "P0X"
        ]
        shadow_research_reserved = [
            decision
            for decision in reserved
            if getattr(decision, "rule", None)
            == P0_2M_SHADOW_RESEARCH_RULE
        ]
        shadow_research_p0_count = sum(
            getattr(decision, "session_scope", None) == "regular"
            for decision in shadow_research_reserved
        )
        shadow_research_p0x_reserved = [
            decision
            for decision in shadow_research_reserved
            if getattr(decision, "session_scope", None)
            in {"premarket", "afterhours"}
        ]
        p1_count = sum(
            getattr(decision, "tier", None) == "P1"
            and getattr(decision, "rule", None) not in _OPENING_RULES
            for decision in reserved
        )
        with self._lock:
            current_day = self._day
        if item.et_date != current_day:
            # 午夜附近旧日消息可能在新日状态重建后才明确失败。不能直接减
            # 当前日计数，也不能盲减周计数（新日重建可能已看到 false
            # delivery）；从 append-only 台账重算当前滚动窗口最安全。
            if current_day:
                self._refresh_weekly_budget_counts(current_day)
            return
        with self._lock:
            # 日期可能在上面的无锁台账读取前后再次翻转；此时下一次观察会
            # 为新日期重建，旧 reservation 不得回退另一天的日预算。
            if item.et_date != self._day:
                return
            self._sent_count = max(0, self._sent_count - len(reserved))
            self._tier_state.opening_delivered_today = max(
                0,
                self._tier_state.opening_delivered_today - opening_count,
            )
            self._tier_state.p0_delivered_today = max(
                0,
                self._tier_state.p0_delivered_today - p0_count,
            )
            self._tier_state.p0_delivered_week = max(
                0,
                self._tier_state.p0_delivered_week - p0_count,
            )
            for decision in p0x_reserved:
                scope = str(getattr(decision, "session_scope", None) or "")
                if scope in self._tier_state.p0x_delivered_today_by_session:
                    self._tier_state.p0x_delivered_today_by_session[scope] = max(
                        0,
                        self._tier_state.p0x_delivered_today_by_session[scope] - 1,
                    )
            self._tier_state.p0x_delivered_week = max(
                0,
                self._tier_state.p0x_delivered_week - len(p0x_reserved),
            )
            self._tier_state.p0_2m_shadow_research_delivered_today = max(
                0,
                self._tier_state.p0_2m_shadow_research_delivered_today
                - shadow_research_p0_count,
            )
            self._tier_state.p0_2m_shadow_research_delivered_week = max(
                0,
                self._tier_state.p0_2m_shadow_research_delivered_week
                - shadow_research_p0_count,
            )
            research_by_session = (
                self._tier_state.p0x_2m_shadow_research_delivered_today_by_session
            )
            for decision in shadow_research_p0x_reserved:
                scope = str(getattr(decision, "session_scope", None) or "")
                research_by_session[scope] = max(
                    0, research_by_session.get(scope, 0) - 1
                )
            self._tier_state.p0x_2m_shadow_research_delivered_week = max(
                0,
                self._tier_state.p0x_2m_shadow_research_delivered_week
                - len(shadow_research_p0x_reserved),
            )
            self._tier_state.p1_delivered_today = max(
                0,
                self._tier_state.p1_delivered_today - p1_count,
            )
            self._cap_notice_sent = (
                self._sent_count >= self._max_alerts_per_day
            )

    def _refresh_weekly_budget_counts(self, market_date: str) -> None:
        """Rebuild rolling P0/P0X counts after a cross-date send outcome."""

        try:
            p0_count = int(self._ledger.count_delivered_p0_week(market_date))
            count_p0x = getattr(
                self._ledger, "count_delivered_p0x_week", None
            )
            p0x_count = int(count_p0x(market_date)) if callable(count_p0x) else 0
            count_shadow = getattr(
                self._ledger,
                "count_delivered_p0_2m_shadow_research_week",
                None,
            )
            shadow_p0_count = (
                int(count_shadow(market_date, session_scope="regular"))
                if callable(count_shadow)
                else 0
            )
            shadow_p0x_count = (
                int(count_shadow(market_date, session_scope="extended"))
                if callable(count_shadow)
                else 0
            )
        except Exception as exc:  # noqa: BLE001 - keep prior conservative counts
            self._note_state(
                f"weekly_budget:refresh_failed:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] cross-date weekly budget refresh failed "
                "error_type=%s; keeping conservative counts",
                type(exc).__name__,
            )
            return
        with self._lock:
            if self._day != market_date:
                return
            self._tier_state.p0_delivered_week = max(0, p0_count)
            self._tier_state.p0x_delivered_week = max(0, p0x_count)
            self._tier_state.p0_2m_shadow_research_delivered_week = max(
                0, shadow_p0_count
            )
            self._tier_state.p0x_2m_shadow_research_delivered_week = max(
                0, shadow_p0x_count
            )

    def _append_ledger(self, *, decisions=(), deliveries=()) -> bool:
        try:
            self._ledger.append(decisions, deliveries)
            self._note_state(
                "ledger:ok",
                logging.INFO,
                "[intraday-alerts] push ledger healthy",
            )
            return True
        except Exception as exc:  # noqa: BLE001 - caller chooses fail-closed action
            self._note_state(
                f"ledger:failed:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] push-ledger append failed error_type=%s; "
                "external send suppressed for this work item",
                type(exc).__name__,
            )
            return False

    def _append_delivery(
        self, et_date: str, message: _OutboundMessage, ok: bool
    ) -> bool:
        try:
            from src.journal.ledger.push_ledger_repository import (
                PushDeliveryInput,
                new_push_id,
            )

            self._ledger.append(
                (),
                (
                    PushDeliveryInput(
                        push_id=new_push_id(),
                        et_date=et_date,
                        ts=datetime.now(timezone.utc),
                        tier=message.tier,
                        message_key=message.message_key,
                        telegram_ok=ok,
                        # Keep the complete payload for incident review.  The
                        # old header-only receipt made a failed real alert
                        # indistinguishable from a synthetic channel test.
                        text=message.text,
                        channel="telegram_silent" if message.silent else "telegram",
                    ),
                ),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._note_state(
                f"ledger:failed:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] push-ledger delivery append failed "
                "error_type=%s",
                type(exc).__name__,
            )
            return False

    def _deliver(
        self, message: _OutboundMessage, *, deadline: Optional[datetime] = None,
    ) -> Optional[bool]:
        error_type: Optional[str] = None
        sender = (
            self._send_silent_text
            if message.silent and self._send_silent_text is not None
            else self._send_text
        )
        # Inline replay/one-off mode keeps the existing historical-checkpoint
        # contract. Live async delivery always enforces checkpoint expiry;
        # an explicit durable deadline is enforced in either mode.
        cutoff = message.expires_at if self._async_send else None
        if deadline is not None:
            cutoff = deadline if cutoff is None else min(cutoff, deadline)
        if cutoff is not None and self._utc_now() >= cutoff:
            return False  # no external request was made
        try:
            if (
                cutoff is not None
                and not message.silent
                and self._send_text_with_deadline is not None
            ):
                raw_result = self._send_text_with_deadline(
                    message.text,
                    cutoff,
                )
            else:
                raw_result = sender(message.text)
            ok = None if raw_result is None else bool(raw_result)
        except Exception as exc:  # noqa: BLE001 - sender must never kill us
            ok = False
            error_type = type(exc).__name__
        if ok is True:
            self._note_state(
                "send:healthy",
                logging.INFO,
                "[intraday-alerts] telegram delivery healthy",
            )
        elif ok is None:
            self._note_state(
                "send:indeterminate",
                logging.WARNING,
                "[intraday-alerts] telegram delivery outcome indeterminate; "
                "automatic retry suppressed to avoid duplicates",
            )
        else:
            self._note_state(
                f"send:failed:{error_type or 'returned_false'}",
                logging.WARNING,
                "[intraday-alerts] telegram send failed reason=%s; staying "
                "quiet until the state changes",
                error_type or "returned_false",
            )
        return ok

    def _note_state(
        self, fingerprint: str, level: int, message: str, *args: Any
    ) -> None:
        family = fingerprint.split(":", 1)[0]
        if self._last_fingerprints.get(family) == fingerprint:
            return
        logger.log(level, message, *args)
        self._last_fingerprints[family] = fingerprint


__all__ = [
    "ALERT_FOOTER",
    "DEFAULT_MAX_ALERTS_PER_DAY",
    "IntradayAlert",
    "IntradayOpportunityAlerter",
    "PREMARKET_ABS_CHANGE_MIN",
    "PREMARKET_LARGE_PRINT_MIN_TURNOVER",
    "RepositoryPushLedger",
    "VOLUME_ALERT_MIN_SCORE",
    "VOLUME_ALERT_VOL_NORM_MIN",
    "detect_intraday_alerts",
    "format_alert_message",
]
