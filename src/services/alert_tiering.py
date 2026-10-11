# -*- coding: utf-8 -*-
"""四级推送协议的纯判定层（蓝图 17 §2.2，Phase B）。

本模块只做机械判定：输入是预热扫描载荷里**已有的字段** + 注入的上下文
快照（gate / 持仓 / 当日已实现盈亏），输出是分级决定；不发送、不落库、
不取数。发送与记账在 :mod:`src.services.intraday_opportunity_alerter`。

当前 P0/P0-X 只评估严格 2m 数据完整、
8m 同钟价格分位、8m RVOL 和同向 18m/0.5 ATR 四项。预算只控制发送；
Regime、DTE、财报、合约摩擦和旧 ET 时段不改变 signal fact。
R6 当日触线锁存或读数未知时，保留标的信号事实，但关闭合约补充与行动性措辞。
财报与 Regime 只记录为风险上下文；开盘 1m 与严格 2m 各自独立判定。

"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from src.opportunities.intraday_top import EARNINGS_WINDOW_DAYS
from src.opportunities.p0_2m_signal import (
    P0_2M_PRICE_PERCENTILE_MIN,
    P0_2M_REFERENCE_SESSIONS,
    P0_2M_RVOL_MIN,
    P0_2M_SIGNAL_VERSION,
    P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
)
from src.services.xnys_session_clock import resolve_xnys_session_clock

__all__ = [
    "EARNINGS_LONG_PREMIUM_BLOCK_TEXT",
    "EARNINGS_VETO_LANE_INTRADAY_SAME_DAY",
    "INTRADAY_ENTRY_START_ET",
    "INTRADAY_LANE_RULE_ID",
    "OPTION_FRICTION_MAX_QUOTE_AGE_SECONDS",
    "OPTION_FRICTION_ZERO_DTE_MAX_QUOTE_AGE_SECONDS",
    "OPTION_FRICTION_MAX_SPREAD_PERCENT",
    "OPTION_FRICTION_ZERO_DTE_MAX_SPREAD_PERCENT",
    "OPTION_FRICTION_MIN_ABS_DELTA",
    "OPTION_FRICTION_MAX_ABS_DELTA",
    "OPTION_FRICTION_SELECTION_BASIS",
    "OPTION_FRICTION_SOURCE",
    "OPENING_ALERT_DAILY_BUDGET",
    "P0_AUTO_TIGHTEN_ENABLED",
    "P0_2M_CONDITION_NAMES",
    "P0_DAILY_BUDGET",
    "P0_PASS_RATE_TIGHTEN_THRESHOLD",
    "P0_PASS_RATE_WINDOW",
    "P0_WEEKLY_BUDGET",
    "P0X_DAILY_BUDGET_PER_SESSION",
    "P0X_WEEKLY_BUDGET",
    "P1_DAILY_BUDGET",
    "R6_FUSE_DAILY_LOSS_USD",
    "ConditionResult",
    "TierContext",
    "TierEngineState",
    "earnings_veto",
    "evaluate_p0_2m_conditions",
    "intraday_execution_policy",
    "opening_probe_profile",
    "opening_probe_direction",
    "opportunity_telegram_window",
    "fuse_state",
    "power_hour_window",
    "resolve_fuse_state",
    "same_day_exit_deadline_et",
]

_NEW_YORK = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# 蓝图 §2.2 硬预算与常量（机械规则，非预测）。
# ---------------------------------------------------------------------------
P0_DAILY_BUDGET = 2
P0_WEEKLY_BUDGET = 7
P0X_DAILY_BUDGET_PER_SESSION = 1
P0X_WEEKLY_BUDGET = 5
P1_DAILY_BUDGET = 3
# P1-O（开盘异动响铃）使用独立预算：它是 setup 发现层，不挤占普通 P1
# 近失/风控提醒，也不消耗 P0 当日 2 次手机预算。多票同刻合并为一条消息。
OPENING_ALERT_DAILY_BUDGET = 3

#: 唯一活动策略：0-7DTE 都只是合约期限，持仓周期必须是同一 ET 交易日。
INTRADAY_LANE_RULE_ID = "INTRA-0-7"

#: 同日策略的保守开仓/离场时间。精确分钟是工程风控参数，待真实成交校准。
INTRADAY_ENTRY_START_ET = (9, 45)

#: 同日手动执行的财报事件上下文；不改变标的信号事实。
EARNINGS_VETO_LANE_INTRADAY_SAME_DAY = "intraday_same_day_0_7dte"

#: 1-7DTE 候选合约报价的最大可接受年龄；0DTE 使用更严格的 30 秒门槛。
OPTION_FRICTION_MAX_QUOTE_AGE_SECONDS = 90.0
OPTION_FRICTION_ZERO_DTE_MAX_QUOTE_AGE_SECONDS = 30.0
OPTION_FRICTION_MAX_SPREAD_PERCENT = 10.0
OPTION_FRICTION_ZERO_DTE_MAX_SPREAD_PERCENT = 8.0
OPTION_FRICTION_MIN_ABS_DELTA = 0.35
OPTION_FRICTION_MAX_ABS_DELTA = 0.65
OPTION_FRICTION_SOURCE = "moomoo_near_expiry_chain"
OPTION_FRICTION_SELECTION_BASIS = (
    "intraday-option-friction/v2:direction_up_call_down_put;eligible_dte_0_7;"
    "hard_bid_spread_delta_freshness;rank_spread_delta_volume_oi_dte_atm"
)

#: R6 灾备保险丝宽线（doc 16 §5.2 暂定 $12k；与 gate 不叠加、只作灾备）。
R6_FUSE_DAILY_LOSS_USD = 12_000.0

#: P0 自动收紧（蓝图：passed 率滚动 20 条 >70% → 阈值收紧）。**当前只
#: 记账与展示，不自动改阈值**：把自我修改闸在这个常量后面，翻开前必须
#: 有人工决定与文档记录——系统绝不静默收紧自己的阈值。
P0_PASS_RATE_WINDOW = 20
P0_PASS_RATE_TIGHTEN_THRESHOLD = 0.70
P0_AUTO_TIGHTEN_ENABLED = False

#: 改进 #3 红字（证据：de Silva, Smith & So 2026, Rev. Finance——强：
#: 均亏 5-9%，高预期波动事件 10-14%）。只警示、不禁止：系统只读。
EARNINGS_LONG_PREMIUM_BLOCK_TEXT = (
    "财报前买权利金是文献最确定亏损模式之一（IV 买贵+点差+拖延平仓）"
    "——de Silva, Smith & So (2026, Rev. Finance)：平均亏损 5-9%，"
    "高预期波动事件 10-14%"
)

# 新 P0 的判定只有四个信号事实。预算、Regime、财报、DTE、合约摩擦与旧
# 09:45--15:15 时间窗都不是信号条件；其中预算只在发送阶段裁决。
P0_2M_CONDITION_NAMES: dict[int, str] = {
    1: "严格 2m 数据与 60 日同钟基线完整",
    2: "8m 价格异常达到同钟 P95",
    3: "8m 相对成交量达到 1.5",
    4: "18m 同向位移达到前一交易日 ATR14 的 0.5",
}


@dataclass(frozen=True)
class ConditionResult:
    """One mechanically evaluated P0 condition."""

    condition_id: int
    name: str
    status: str  # pass | fail | missing
    detail: str

    def as_json(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class OpenPositionFact:
    """One open-position fact for the same-day flatten reminder."""

    raw_symbol: str
    underlying: str
    opened_et_date: Optional[str]
    dte_at_entry: Optional[int]


@dataclass(frozen=True)
class TierContext:
    """Injected context snapshot; ``None`` fields are honest 标缺.

    由提示器的 worker 线程按 TTL 刷新（绝不在预热线程取数）；判定用的是
    最近一次快照，``as_of`` 随行记账。
    """

    gate_state: Optional[Mapping[str, Any]] = None
    open_positions: Optional[tuple[OpenPositionFact, ...]] = None
    daily_realized_pnl: Optional[float] = None
    as_of: Optional[str] = None


@dataclass
class TierEngineState:
    """Per-ET-day mutable engine state (owned by the alerter, reset daily)."""

    p0_delivered_today: int = 0
    p0_delivered_week: int = 0
    p0x_delivered_today_by_session: dict[str, int] = field(
        default_factory=lambda: {"premarket": 0, "afterhours": 0}
    )
    p0x_delivered_week: int = 0
    p0_2m_shadow_research_delivered_today: int = 0
    p0_2m_shadow_research_delivered_week: int = 0
    p0x_2m_shadow_research_delivered_today_by_session: dict[str, int] = field(
        default_factory=lambda: {"premarket": 0, "afterhours": 0}
    )
    p0x_2m_shadow_research_delivered_week: int = 0
    p0_episode_last_window: dict[str, str] = field(default_factory=dict)
    p1_delivered_today: int = 0
    opening_delivered_today: int = 0
    #: R6 保险丝的当日锁存位（「当日不可上调」）：一旦确证触线即置位，
    #: 当日盈亏回升或数据断供都不清除；ET 日期翻转随整个 state 重建复位，
    #: 重启后由提示器从台账的触线通告 decision 行重建（re-latch）。
    fuse_latched_today: bool = False


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def et_moment(as_of: Any) -> Optional[datetime]:
    """Parse the payload ``as_of`` into an aware ET moment, or None."""
    try:
        moment = datetime.fromisoformat(str(as_of))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(_NEW_YORK)


# ---------------------------------------------------------------------------
# 严格 2m 四项信号事实
# ---------------------------------------------------------------------------


def _p0_2m_cond(
    condition_id: int, status: str, detail: str
) -> ConditionResult:
    return ConditionResult(
        condition_id=condition_id,
        name=P0_2M_CONDITION_NAMES[condition_id],
        status=status,
        detail=detail,
    )


def evaluate_p0_2m_conditions(
    candidate: Mapping[str, Any],
) -> list[ConditionResult]:
    """Evaluate only the four frozen signal facts for strict 2m P0/P0X.

    The returned list intentionally excludes delivery budgets, Regime,
    earnings, option availability/friction, DTE and wall-clock entry limits.
    Budgets and validated activation control sending separately.  The other
    contexts only annotate risk or manual execution, never the signal facts.
    """

    evidence = candidate.get("p0_signal_evidence_v2")
    if not isinstance(evidence, Mapping):
        return [
            _p0_2m_cond(index, "missing", "p0_signal_evidence_v2 缺失")
            for index in P0_2M_CONDITION_NAMES
        ]

    signal_version = str(evidence.get("signal_version") or "")
    quality = evidence.get("data_quality")
    quality_status = (
        str(quality.get("status") or "")
        if isinstance(quality, Mapping)
        else ""
    )
    try:
        baseline_count = int(evidence.get("baseline_count") or 0)
    except (TypeError, ValueError):
        baseline_count = 0
    if (
        signal_version == P0_2M_SIGNAL_VERSION
        and quality_status == "complete"
        and baseline_count >= P0_2M_REFERENCE_SESSIONS
    ):
        data_condition = _p0_2m_cond(
            1,
            "pass",
            f"exact 2m 完整；同钟基线 {baseline_count}/{P0_2M_REFERENCE_SESSIONS}",
        )
    else:
        issues = (
            ",".join(str(item) for item in (quality.get("issues") or ()))
            if isinstance(quality, Mapping)
            else "data_quality_missing"
        )
        data_condition = _p0_2m_cond(
            1,
            "missing",
            str(evidence.get("unavailable_reason") or issues or "严格 2m 数据不完整"),
        )

    percentile = _finite(evidence.get("price_percentile"))
    if percentile is None:
        percentile_condition = _p0_2m_cond(2, "missing", "price_percentile 标缺")
    elif percentile >= P0_2M_PRICE_PERCENTILE_MIN:
        percentile_condition = _p0_2m_cond(
            2, "pass", f"同钟经验分位 P{percentile * 100:.1f}"
        )
    else:
        percentile_condition = _p0_2m_cond(
            2,
            "fail",
            f"同钟经验分位 P{percentile * 100:.1f} < P{P0_2M_PRICE_PERCENTILE_MIN * 100:.0f}",
        )

    rvol = _finite(evidence.get("rvol_8m"))
    if rvol is None:
        rvol_condition = _p0_2m_cond(3, "missing", "rvol_8m 标缺")
    elif rvol >= P0_2M_RVOL_MIN:
        rvol_condition = _p0_2m_cond(3, "pass", f"8m RVOL {rvol:.2f}x")
    else:
        rvol_condition = _p0_2m_cond(
            3, "fail", f"8m RVOL {rvol:.2f}x < {P0_2M_RVOL_MIN:.2f}x"
        )

    displacement = _finite(evidence.get("displacement_atr"))
    direction = str(evidence.get("direction") or "")
    failed_checks = {
        str(item) for item in (evidence.get("failed_checks") or ())
    }
    direction_matches = (
        direction in {"up", "down"}
        and "impulse_displacement_direction_mismatch" not in failed_checks
    )
    if displacement is None:
        displacement_condition = _p0_2m_cond(
            4, "missing", "displacement_atr 标缺"
        )
    elif direction_matches and displacement >= P0_2M_STRICT_DISPLACEMENT_ATR_MIN:
        displacement_condition = _p0_2m_cond(
            4,
            "pass",
            f"18m 与 8m 同向；位移 {displacement:.2f} ATR",
        )
    else:
        mismatch = "、方向不一致" if not direction_matches else ""
        displacement_condition = _p0_2m_cond(
            4,
            "fail",
            f"18m 位移 {displacement:.2f} ATR < "
            f"{P0_2M_STRICT_DISPLACEMENT_ATR_MIN:.2f}{mismatch}",
        )

    return [
        data_condition,
        percentile_condition,
        rvol_condition,
        displacement_condition,
    ]


def opening_probe_profile(
    payload: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Optional[Mapping[str, Any]]:
    """Return one fresh qualified opening profile, progressive before final.

    09:31--09:35 的 sequential 画像只能在其 checkpoint 所在的墙钟分钟
    发出，防止慢请求在下一分钟补响过时证据。09:35 也只读
    09:30--09:34 五根已完成 1m，不再读原生 5m。它们都是 P1-O 发现层。
    """

    if str(payload.get("session_state") or "") != "regular" or str(
        payload.get("quote_session_scope") or ""
    ) != "current_session":
        return None
    # ``as_of`` is captured before the opening fast-path performs I/O.  When
    # the observer supplies its actual receipt time, it is authoritative for
    # the checkpoint's own wall-clock minute so a slow request cannot ring using
    # a stale request timestamp.  A present-but-invalid value fails closed.
    observed_at = (
        payload.get("alert_observed_at")
        if "alert_observed_at" in payload
        else payload.get("as_of")
    )
    moment = et_moment(observed_at)
    if moment is None:
        return None
    if moment.date().isoformat() != str(payload.get("market_date_et") or ""):
        return None
    bursts = candidate.get("session_bursts")
    if not isinstance(bursts, Mapping):
        return None
    progressive = bursts.get("opening_progressive")
    if (
        isinstance(progressive, Mapping)
        and progressive.get("state") == "qualified"
        and progressive.get("alert_qualified") is True
    ):
        try:
            checkpoint = int(progressive.get("checkpoint"))
            confirmation_points = [
                int(value)
                for value in (progressive.get("confirmation_points") or ())
            ]
        except (TypeError, ValueError):
            checkpoint = -1
            confirmation_points = []
        checkpoint_label = str(
            progressive.get("checkpoint_label")
            or progressive.get("trigger_end_et")
            or ""
        )
        try:
            hour_text, minute_text = checkpoint_label.split(":", 1)
            checkpoint_minute = int(hour_text) * 60 + int(minute_text)
        except (TypeError, ValueError):
            checkpoint_minute = -1
        early_progressive_is_valid = (
            progressive.get("rule_version") == "opening_impulse_v2"
            and 2 <= checkpoint <= 4
            and progressive.get("confirmation_mode") == "consecutive_1m"
            and confirmation_points == [checkpoint - 1, checkpoint]
            and progressive.get("current_checkpoint_pass") is True
            and progressive.get("direction") in {"up", "down"}
            and checkpoint_label == f"09:{30 + checkpoint:02d}"
            and moment.hour * 60 + moment.minute == checkpoint_minute
        )
        final_progressive_is_valid = (
            progressive.get("rule_version") == "opening_impulse_v2"
            and checkpoint == 5
            and progressive.get("confirmation_mode") == "completed_1m_cumulative"
            and confirmation_points == [5]
            and progressive.get("current_checkpoint_pass") is True
            and progressive.get("direction") in {"up", "down"}
            and progressive.get("bar_start_et") == "09:30"
            and checkpoint_label == "09:35"
            and moment.hour * 60 + moment.minute == 9 * 60 + 35
        )
        if early_progressive_is_valid or final_progressive_is_valid:
            return progressive
    return None


def opening_probe_direction(
    payload: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Optional[str]:
    """Fresh opening setup direction; independent of the P0 dual window."""

    impulse = opening_probe_profile(payload, candidate)
    if impulse is None:
        return None
    direction = str(impulse.get("direction") or "").strip().lower()
    return direction if direction in {"up", "down"} else None


def _minutes(value: tuple[int, int]) -> int:
    return value[0] * 60 + value[1]


def _minute_label(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def intraday_execution_policy(as_of: Any) -> dict[str, Any]:
    """Calendar-backed manual time checks, never an underlying signal gate.

    ``*_entry_allowed`` means only that the manual time window is open, not
    that a trade is permitted.  Calendar/input uncertainty stays explicit.
    Expiry is bounded by the next policy transition so clients need not copy
    any ET wall-clock or early-close rules.
    """
    moment = et_moment(as_of)
    policy = {
        "policy_version": "intraday_execution_policy_v1",
        "state": "unavailable",
        "as_of": moment.isoformat() if moment is not None else None,
        "expires_at": (
            (moment + timedelta(seconds=120)).isoformat()
            if moment is not None else None
        ),
        "market_date_et": moment.date().isoformat() if moment is not None else None,
        "reason": "as_of_unparseable_or_naive",
        "entry_start_et": None,
        "ordinary_entry_cutoff_et": None,
        "zero_dte_entry_cutoff_et": None,
        "exit_deadline_et": None,
        "close_et": None,
        "ordinary_entry_allowed": None,
        "zero_dte_entry_allowed": None,
    }
    if moment is None:
        return policy
    clock = resolve_xnys_session_clock(moment)
    if clock.state == "unavailable":
        policy["reason"] = getattr(clock, "unavailable_reason", None) or "xnys_clock_unavailable"
        return policy
    if clock.state != "session" or clock.close_minute_et is None:
        policy.update(
            state="closed", reason="not_xnys_session",
            ordinary_entry_allowed=False, zero_dte_entry_allowed=False,
        )
        return policy
    entry_start = _minutes(INTRADAY_ENTRY_START_ET)
    close = clock.close_minute_et
    ordinary_cutoff, zero_cutoff = close - 45, close - 90
    minute = moment.hour * 60 + moment.minute
    policy.update(
        state="closed" if minute >= close else "ready",
        reason=(
            "xnys_session_closed" if minute >= close else
            "before_entry_start" if minute < entry_start else
            "all_entry_windows_closed" if minute >= ordinary_cutoff else
            "zero_dte_entry_closed" if minute >= zero_cutoff else "entry_windows_open"
        ),
        entry_start_et=_minute_label(entry_start),
        ordinary_entry_cutoff_et=_minute_label(ordinary_cutoff),
        zero_dte_entry_cutoff_et=_minute_label(zero_cutoff),
        exit_deadline_et=_minute_label(close - 15),
        close_et=_minute_label(close),
        ordinary_entry_allowed=entry_start <= minute < ordinary_cutoff,
        zero_dte_entry_allowed=entry_start <= minute < zero_cutoff,
    )
    boundaries = [entry_start, zero_cutoff, ordinary_cutoff, close - 15, close]
    next_boundaries = [
        moment.replace(hour=value // 60, minute=value % 60, second=0, microsecond=0)
        for value in boundaries if value > minute
    ]
    expiry = moment + timedelta(seconds=120)
    if next_boundaries:
        expiry = min(expiry, min(next_boundaries))
    policy["expires_at"] = expiry.isoformat()
    return policy


def same_day_exit_deadline_et(
    payload: Mapping[str, Any],
) -> Optional[tuple[int, int]]:
    """Return close-15m for the exact XNYS session, or ``None``.

    Normal sessions resolve to 15:45 ET; a 13:00 half day resolves to 12:45.
    Holiday or unavailable calendar facts never fall back to 15:45.
    """

    deadline = intraday_execution_policy(payload.get("as_of"))["exit_deadline_et"]
    if deadline is None:
        return None
    hour, minute = deadline.split(":")
    return int(hour), int(minute)


def opportunity_telegram_window(payload: Mapping[str, Any]) -> bool:
    """Whether opportunity-class Telegram delivery is inside live XNYS.

    This is deliberately independent from the payload's approximate
    ``session_state``.  A holiday, unavailable calendar read, premarket, or
    the instant at/after the exact session close all fail closed.  Safety
    notifications use their own policy and must not call this gate.
    """

    observed_at = (
        payload.get("alert_observed_at")
        if "alert_observed_at" in payload
        else payload.get("as_of")
    )
    moment = et_moment(observed_at)
    if moment is None:
        return False
    clock = resolve_xnys_session_clock(moment)
    return (
        clock.state == "session"
        and getattr(clock, "phase", None) == "regular"
    )


# ---------------------------------------------------------------------------
# 财报拦截 / 保险丝 / 尾盘同日清仓窗口
# ---------------------------------------------------------------------------


def earnings_veto(
    candidate: Mapping[str, Any],
    *,
    lane: str = EARNINGS_VETO_LANE_INTRADAY_SAME_DAY,
) -> tuple[str, str]:
    """同日长权利金候选的财报事件日判定。

    返回 ``(verdict, detail)``；verdict ∈ ``clear`` / ``unknown`` /
    ``blackout``。INTRA-0-7 必须同一 ET 交易日平仓，所以只有
    ``days_to_earnings == 0`` 才报告当日事件风险；未来事件只作为上下文，不能继续
    沿用已退役隔夜策略的“会跨过财报”假设。unknown 明确标缺；三种状态都不
    改变严格 2m 或开盘 1m 信号，调用方仅用于风险警告。

    ``within_blackout`` 是单一生产方（``intraday_top`` 财报临近区块）的
    布尔契约；防御性检查：非布尔值＝契约漂移 → ``unknown``（未知≠安全），
    绝不把 truthy 杂质当命中、也绝不当清白。
    """
    proximity = candidate.get("earnings_proximity")
    if not isinstance(proximity, Mapping) or proximity.get("state") != "ready":
        reason = (
            str(proximity.get("unavailable_reason") or "财报日历不可得")
            if isinstance(proximity, Mapping)
            else "载荷无财报临近读数"
        )
        return "unknown", f"{reason}（未知≠安全）"
    within = proximity.get("within_blackout")
    if not isinstance(within, bool):
        return (
            "unknown",
            "within_blackout 读数非布尔（生产方契约漂移）（未知≠安全）",
        )
    days = proximity.get("days_to_earnings")
    days_int = (
        days if isinstance(days, int) and not isinstance(days, bool) else None
    )
    if days_int == 0:
        return "blackout", f"今日财报 · {EARNINGS_LONG_PREMIUM_BLOCK_TEXT}"
    if within and days_int is None:
        return "unknown", "财报临近但精确天数标缺，无法确认是否为当日财报（未知≠安全）"
    if days_int is not None and 0 < days_int <= EARNINGS_WINDOW_DAYS:
        return (
            "clear",
            f"财报在 {days_int} 天后；INTRA-0-7 同日平仓不跨事件（IV/点差仍由摩擦门处理）",
        )
    return "clear", "今日无已知财报事件"


def fuse_state(context: TierContext) -> str:
    """R6 灾备保险丝的**瞬时读数**：``triggered`` / ``not_triggered`` /
    ``missing``。

    这是无记忆的纯读数；生产判定必须走 :func:`resolve_fuse_state`（当日
    锁存），否则盈亏回升/断供会让保险丝当日解锁（缺陷 D2）。缺失只表示
    读不到，不证明未触线。
    """
    pnl = context.daily_realized_pnl
    if pnl is None:
        return "missing"
    return "triggered" if pnl <= -R6_FUSE_DAILY_LOSS_USD else "not_triggered"


def resolve_fuse_state(context: TierContext, state: TierEngineState) -> str:
    """R6 保险丝的当日锁存判定（「当日不可上调」，触线通告承诺明日复位）。

    精确语义（对照蓝图「缺失＝维持现状」的可实现化定义）：

    * 瞬时读数 ``triggered`` → 置位 ``state.fuse_latched_today`` 并返回
      ``triggered``；
    * 已锁存（本日曾确证触线）→ 无论此后盈亏回升（例如平掉盈利单抬高
      当日合计）还是数据断供（provider 异常/None），一律维持
      ``triggered``——合约补充与行动性措辞不因数据波动在当日恢复；
    * 未锁存 + 缺失 → ``missing``：缺数据不能证明触线或安全；保留标的
      异动事实，调用方关闭合约补充与行动性措辞，不改写信号分级；
    * 未锁存 + ``not_triggered`` → 原样返回。

    锁存随 ``TierEngineState`` 的 ET 日翻转清零（明日复位）；重启后由
    提示器从台账的 ``r6_fuse_notice`` decision 行重建（re-latch）。
    """
    raw = fuse_state(context)
    if raw == "triggered":
        state.fuse_latched_today = True
    if state.fuse_latched_today:
        return "triggered"
    return raw


def power_hour_window(payload: Mapping[str, Any]) -> bool:
    """P1-b 尾盘窗口：XNYS 当日真实 close 前 30 分钟至 close。"""
    moment = et_moment(payload.get("as_of"))
    if moment is None:
        return False
    clock = resolve_xnys_session_clock(moment)
    if clock.state != "session" or clock.close_minute_et is None:
        return False
    minutes = moment.hour * 60 + moment.minute
    start = clock.close_minute_et - 30
    end = clock.close_minute_et
    return start <= minutes < end
