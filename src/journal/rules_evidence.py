# -*- coding: utf-8 -*-
"""「交易纪律」页的证据读数：零写入，纯 SELECT，与车道遵守度同一干净口径。

这个模块存在的理由
------------------
用户手上有一批**只在对话里出现过、代码与页面里都看不到**的取证结论（合约
价格甜蜜区、DTE × 持有方式、时段、星期 × 0DTE 可用性、手续费门槛）。规则
写在 Playbook 里，但支撑规则的数字没有一个固定的、可复算的落点，于是每次
都要重新口算，且前端一旦硬编码就会和口径漂移。

本模块把这些表变成**后端可复算的读数**，前端只渲染、不算数、不硬编码。

口径（与 ``personal_edge.RULE_COMPLIANCE_*`` 完全一致，共用同一份定义）
----------------------------------------------------------------------
样本 ＝ 当前默认 build 的 PositionEpisode 中同时满足：

* ``lifecycle_status == 'closed'`` 且 ``realized_pnl_net`` 已知；
* ``evidence_summary_json.fill_allocations > 0``（由明细成交构建）——汇总
  ORDER 口径的风险金额分母被管线自身标记为 ``audit_only_not_execution_
  cash_flow``，混入会系统性抬高每美元读数；
* ``opening_cash_flow`` 已知（风险金额 ＝ ``ABS(opening_cash_flow)``）；
* ET 入场日 ≥ :data:`~src.journal.personal_edge.RULE_COMPLIANCE_CLEAN_BASIS_START`。

在 build #3 上该口径给出 n=1,407。**毛口径 ＝ ``realized_pnl_net + total_fee``**；
每美元读数一律为「合计金额 / 合计风险金额」的**金额加权**，不是逐笔平均——
两者会显著不同，混用会得出互相矛盾的结论。

诚实边界
--------
* 全部为**描述统计**，不是建议、不是预测、不构成投资意见；
* 样本只覆盖 2026-04→07 单一行情段，规则由这段样本**样本内**推出，
  前向验证在 ``/journal`` 的规则遵守度面板；
* 分母为 0、样本缺失一律返回 ``None`` + 原因，绝不以 0 冒充；
* 仓位与回撤块呈现的是「**你选择的参数 + 它们的算术含义**」，
  不是推荐值，也不对未来频率作任何承诺。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select

from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    _latest_build,
)
from src.journal.ledger.models import EpisodeBuild, PositionEpisode
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.ledger.review_insights import _build_source_kind
from src.journal.personal_edge import (
    DISCIPLINE_BODY_MIN_EPISODE_COUNT,
    RULE_COMPLIANCE_CLEAN_BASIS_REASON,
    RULE_COMPLIANCE_CLEAN_BASIS_START,
    RULE_SET_V2_ADOPTED_AT,
    _et_hour_utc_minus_4,
    _trading_day_utc_minus_4,
    _utc,
    classify_rule_lane,
    fill_detailed_from_evidence_summary,
)
from src.storage import get_db

__all__ = [
    "CONTRACT_PRICE_BANDS",
    "CORRELATED_CLUSTER_NOTE",
    "OVERNIGHT_GAP_NOTE",
    "POSITION_PARAMS_FRAMING",
    "RULES_EVIDENCE_BANNER",
    "RULES_EVIDENCE_LIMITATIONS",
    "RULES_EVIDENCE_SCHEMA_VERSION",
    "ChosenPositionParams",
    "RulesEvidenceResult",
    "get_rules_evidence",
]


RULES_EVIDENCE_SCHEMA_VERSION = "journal-rules-evidence/1.0"

#: 页面顶部横幅：这一页是什么、不是什么。前端逐字渲染，不改写。
RULES_EVIDENCE_BANNER = (
    "这一页是你自己的历史统计，不是建议；规则由这段样本推出，"
    "前向验证见 /journal 规则遵守度"
)

RULES_EVIDENCE_LIMITATIONS = (
    "全部为描述统计，不是建议、不是预测，也不构成投资意见。",
    "样本只覆盖 2026-04→07 单一行情段（单一 regime），换一段行情结论可能不成立。",
    "规则由这段样本推出，属样本内拟合；样本内显著不等于前向有效，"
    f"前向验证以规则采纳日 {RULE_SET_V2_ADOPTED_AT} 之后的回合为准。",
    "每美元读数为金额加权（合计盈亏 / 合计风险金额），与逐笔平均不同，两者不可混读。",
    "毛口径 = realized_pnl_net + total_fee；净口径已扣费。费用是恒定过路费，"
    "毛口径低于费用门槛即净口径为负。",
    RULE_COMPLIANCE_CLEAN_BASIS_REASON,
)

#: 合约价格分档（``average_entry_price``，单位美元/张）。
#: 边界策略统一为 ``[lower, upper)``：下含上不含，避免 $2.00 这类整数价重复计数。
CONTRACT_PRICE_BANDS: tuple[tuple[str, Optional[str], Optional[str]], ...] = (
    ("<$1", None, "1"),
    ("$1-2", "1", "2"),
    ("$2-4", "2", "4"),
    ("$4-8", "4", "8"),
    (">$8", "8", None),
)

CONTRACT_PRICE_HEADLINE = (
    "同样金额买便宜合约＝张数多＝手续费按张收，$1 以下净 −14%。"
)

CONTRACT_PRICE_BOUNDARY_POLICY = "lower_inclusive_upper_exclusive"

#: 持有方式：按 ET 交易日判定，平仓日 == 开仓日为「当日平」，否则为「过夜」。
HOLD_STYLE_BASIS = "compare_et_trading_day_of_close_against_open"

#: DTE × 持有方式的两条车道证据。``(标签, dte 下限, dte 上限, 持有方式)``
DTE_HOLD_LANES: tuple[tuple[str, int, int, str], ...] = (
    ("0DTE 当日平", 0, 0, "intraday"),
    ("1-3DTE 当日平", 1, 3, "intraday"),
    ("4-7DTE 当日平", 4, 7, "intraday"),
    ("4-7DTE 过夜", 4, 7, "overnight"),
)

#: 剔除最好 N 笔（按净盈亏金额降序）后的稳健读数，用于识别「靠少数几笔撑住」。
DTE_HOLD_EXCLUDE_TOP_N = 5
#: 剔尾读数的样本门槛：与车道遵守度/规模纪律共用同一 n≥15 门槛
#: （personal_edge._exclude_top_n_readings 的口径），低于门槛返回 null + 原因，
#: 绝不以截断样本冒充稳健读数。
DTE_HOLD_EX_TOP_N_MIN_EPISODE_COUNT = DISCIPLINE_BODY_MIN_EPISODE_COUNT

#: 星期表刻意排除缺 DTE 的回合：缺 DTE 无法判定它属于哪条车道，
#: 混入会让「周二/周四没有 0DTE 可用」这个因果读错。
WEEKDAY_LABELS: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五")

WEEKDAY_HEADLINE = (
    "「周二/周四亏钱」不是星期效应，是合约可用性造成的合约选择问题："
    "主要标的周一/三/五到期，周二/周四没有 0DTE，于是退而买 1-3DTE。"
)

#: 手续费门槛计算器的默认锚点（仅作占位，用户可在前端自行改动；不落库、不猜账户规模）。
FEE_CALCULATOR_DEFAULT_TICKET_USD = 3000
FEE_CALCULATOR_DEFAULT_TICKETS_PER_DAY = 4
#: 一个月按 21 个 ET 交易日折算（不接交易所假日日历，仅作量级换算）。
FEE_CALCULATOR_TRADING_DAYS_PER_MONTH = 21

FEE_THRESHOLD_NOTE = (
    "费率是恒定过路费：它不随行情好坏变化，只随「下注金额 × 笔数」放大。"
    "计算器的输入只在浏览器里，不落库；系统不知道也不猜你的账户规模。"
)

#: 仓位与回撤：用户**自己选定**的参数。此处只回显与做算术，不推荐、不校验优劣。
POSITION_PARAMS_FRAMING = "你选择的参数 + 它们的含义"

#: 触发算术的三档亏损严重度。归零＝−100%；其余两档取**全样本单笔回报**的
#: 低位分位数，定义写在这里，前端不重算。
SEVERITY_TIERS: tuple[tuple[str, str, Optional[int]], ...] = (
    ("归零", "单笔亏掉全部权利金（−100%）", None),
    ("严重亏损", "全样本单笔净回报 p5", 5),
    ("平庸亏损", "全样本单笔净回报 p25", 25),
)

CORRELATED_CLUSTER_NOTE = (
    "这些标的同属一个高相关簇（同一波 AI / 半导体 / 大盘科技行情）："
    "同时持有 2 个＝实质上是一个双倍仓位，日熔断额度会被一次行情同时吃掉。"
)

OVERNIGHT_GAP_NOTE = (
    "每日熔断保护不了过夜仓位：熔断在盘中按已实现亏损触发，"
    "而跳空发生在开盘竞价，止损单无法在跳空之间成交。"
)

_PERCENT_QUANTUM = Decimal("0.0001")


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceCell:
    """一行读数：样本量 + 三个口径 + 显式缺席原因。"""

    label: str
    n: int
    gross_pct: Optional[float] = None
    net_pct: Optional[float] = None
    fee_pct: Optional[float] = None
    win_rate_pct: Optional[float] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class PriceBandRow(EvidenceCell):
    lower: Optional[float] = None
    upper: Optional[float] = None


@dataclass(frozen=True)
class DteHoldRow(EvidenceCell):
    dte_min: int = 0
    dte_max: int = 0
    hold_style: str = "intraday"
    excluded_top_n: int = DTE_HOLD_EXCLUDE_TOP_N
    ex_top_n_gross_pct: Optional[float] = None
    ex_top_n_n: int = 0
    ex_top_n_reason: Optional[str] = None


@dataclass(frozen=True)
class HourRow(EvidenceCell):
    et_hour: int = 0


@dataclass(frozen=True)
class WeekdayRow(EvidenceCell):
    weekday: int = 0
    zero_dte_n: int = 0
    dte_1_3_n: int = 0


@dataclass(frozen=True)
class FeeThreshold:
    """恒定过路费：一个费率 + 一个前端计算器的输入锚点。"""

    fee_pct_of_premium: Optional[float]
    n: int
    reason: Optional[str]
    default_ticket_usd: int
    default_tickets_per_day: int
    trading_days_per_month: int
    note: str


@dataclass(frozen=True)
class SeverityTier:
    label: str
    definition: str
    loss_pct: Optional[float]
    tickets_to_breaker: Optional[float]
    reason: Optional[str] = None


@dataclass(frozen=True)
class ChosenPositionParams:
    """用户自己选定的参数（回显，不是推荐）。"""

    ticket_usd: int
    daily_breaker_usd: int
    max_concurrent: int


@dataclass(frozen=True)
class PositionEvidence:
    framing: str
    params: ChosenPositionParams
    severity_tiers: tuple[SeverityTier, ...]
    observed_breach_day_count: int
    observed_trading_day_count: int
    observed_breach_one_per_days: Optional[float]
    observed_median_tickets_per_day: Optional[float]
    observed_cadence_caveat: str
    historical_max_drawdown_pct: Optional[float]
    historical_drawdown_sizing_pct: float
    drawdown_caveat: str
    reason: Optional[str] = None


@dataclass(frozen=True)
class ClusterTicker:
    ticker: str
    n: int


@dataclass(frozen=True)
class CorrelationEvidence:
    tickers: tuple[ClusterTicker, ...]
    compliant_episode_count: int
    max_concurrent: int
    note: str


@dataclass(frozen=True)
class RulesEvidenceResult:
    account_key: str
    build_id: int
    build_key: str
    source_kind: str
    computed_at: datetime
    clean_basis_start: str
    clean_basis_reason: str
    rule_set_adopted_at: str
    sample_episode_count: int
    excluded_before_clean_basis: int
    excluded_aggregate_or_unknown_basis: int
    excluded_missing_premium: int
    excluded_not_closed_or_missing_pnl: int
    #: 样本内缺 total_fee 的回合数：这些回合仍进净口径/胜率，但从毛口径与
    #: 费率的分子分母中排除（0 回填会把毛口径冒充成净口径）。
    fee_unknown_count: int
    first_trading_day: Optional[str]
    last_trading_day: Optional[str]
    banner: str
    price_band_headline: str
    price_band_boundary_policy: str
    price_bands: tuple[PriceBandRow, ...]
    hold_style_basis: str
    dte_hold_lanes: tuple[DteHoldRow, ...]
    et_hours: tuple[HourRow, ...]
    weekday_headline: str
    weekdays: tuple[WeekdayRow, ...]
    fee_threshold: FeeThreshold
    position: PositionEvidence
    correlation: CorrelationEvidence
    overnight_gap_note: str
    limitations: tuple[str, ...] = field(default=RULES_EVIDENCE_LIMITATIONS)


# ---------------------------------------------------------------------------
# 内部计算
# ---------------------------------------------------------------------------


@dataclass
class _Episode:
    underlying: str
    trading_day: str
    close_trading_day: Optional[str]
    et_hour: int
    weekday: int
    dte: Optional[int]
    lane: str
    pnl: Decimal
    #: ``None`` ＝ 缺 total_fee。费用缺席只计缺席：0 回填会同时低估费率、
    #: 把毛口径（= 净 + 费）冒充成净口径。
    fee: Optional[Decimal]
    risk: Decimal
    entry_price: Optional[Decimal]
    opened_at: datetime
    closed_at: Optional[datetime]

    def realized_at(self) -> datetime:
        """权益变化发生在**平仓**时刻；缺 closed_at 时退回开仓时刻。"""

        return self.closed_at or self.opened_at


def _pct(numerator: Decimal, denominator: Decimal) -> Optional[float]:
    if denominator == 0:
        return None
    value = (numerator / denominator) * Decimal("100")
    return float(value.quantize(_PERCENT_QUANTUM))


def _aggregate(members: list[_Episode]) -> tuple[
    Optional[float], Optional[float], Optional[float], Optional[float], Optional[str]
]:
    """(gross%, net%, fee%, win%, reason) —— 金额加权，分母为 0 时 fail closed。

    净口径与胜率不依赖费用，按全部成员计算；毛口径（= 净 + 费）与费率依赖
    ``total_fee``，只在**费用已知**的子集上计算——缺 total_fee 的回合从这两个
    比率的分子分母中一并排除，绝不按 0 计（0 回填会同时低估费率、把毛口径
    冒充成净口径）。排除计数由结果级 ``fee_unknown_count`` 如实报告。
    """

    if not members:
        return None, None, None, None, "无样本"
    risk = sum((item.risk for item in members), Decimal("0"))
    if risk == 0:
        return None, None, None, None, "该分组风险金额合计为 0"
    pnl = sum((item.pnl for item in members), Decimal("0"))
    wins = sum(1 for item in members if item.pnl > 0)
    fee_known = [item for item in members if item.fee is not None]
    fee_risk = sum((item.risk for item in fee_known), Decimal("0"))
    if fee_known and fee_risk > 0:
        fee_total = sum((item.fee for item in fee_known), Decimal("0"))
        pnl_known = sum((item.pnl for item in fee_known), Decimal("0"))
        gross_pct = _pct(pnl_known + fee_total, fee_risk)
        fee_pct = _pct(fee_total, fee_risk)
    else:
        gross_pct = None
        fee_pct = None
    return (
        gross_pct,
        _pct(pnl, risk),
        fee_pct,
        round(wins / len(members) * 100.0, 4),
        None,
    )


def _percentile(sorted_values: list[Decimal], percentile: int) -> Optional[Decimal]:
    """线性插值分位数（与 ``numpy.percentile`` 默认 ``linear`` 一致）。"""

    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = Decimal(percentile) / Decimal("100") * Decimal(len(sorted_values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = position - Decimal(lower_index)
    lower = sorted_values[lower_index]
    return lower + (sorted_values[upper_index] - lower) * weight


def _price_bands(members: list[_Episode]) -> tuple[PriceBandRow, ...]:
    rows: list[PriceBandRow] = []
    for label, lower_raw, upper_raw in CONTRACT_PRICE_BANDS:
        lower = Decimal(lower_raw) if lower_raw is not None else None
        upper = Decimal(upper_raw) if upper_raw is not None else None
        bucket = [
            item
            for item in members
            if item.entry_price is not None
            and (lower is None or item.entry_price >= lower)
            and (upper is None or item.entry_price < upper)
        ]
        gross, net, fee, win, reason = _aggregate(bucket)
        rows.append(
            PriceBandRow(
                label=label,
                n=len(bucket),
                gross_pct=gross,
                net_pct=net,
                fee_pct=fee,
                win_rate_pct=win,
                reason=reason,
                lower=float(lower) if lower is not None else None,
                upper=float(upper) if upper is not None else None,
            )
        )
    return tuple(rows)


def _is_intraday(item: _Episode) -> bool:
    return (
        item.close_trading_day is not None
        and item.close_trading_day == item.trading_day
    )


def _dte_hold_lanes(members: list[_Episode]) -> tuple[DteHoldRow, ...]:
    rows: list[DteHoldRow] = []
    for label, dte_min, dte_max, hold_style in DTE_HOLD_LANES:
        bucket = [
            item
            for item in members
            if item.dte is not None
            and dte_min <= item.dte <= dte_max
            and (
                _is_intraday(item)
                if hold_style == "intraday"
                else (item.close_trading_day is not None and not _is_intraday(item))
            )
        ]
        gross, net, fee, win, reason = _aggregate(bucket)

        # 剔除最好 N 笔（按净盈亏金额）后的稳健读数。样本门槛与车道遵守度
        # 共用同一 n≥15（DTE_HOLD_EX_TOP_N_MIN_EPISODE_COUNT）：低于门槛时
        # 剔尾读数不成立，返回 null + 原因，绝不以截断样本冒充。
        trimmed = sorted(bucket, key=lambda item: item.pnl, reverse=True)[
            DTE_HOLD_EXCLUDE_TOP_N:
        ]
        ex_gross, _net, _fee, _win, ex_reason = _aggregate(trimmed)
        if len(bucket) < DTE_HOLD_EX_TOP_N_MIN_EPISODE_COUNT:
            ex_gross = None
            ex_reason = (
                f"样本 {len(bucket)} 笔 < "
                f"{DTE_HOLD_EX_TOP_N_MIN_EPISODE_COUNT} 笔，剔除最好 "
                f"{DTE_HOLD_EXCLUDE_TOP_N} 笔后读数不成立"
            )
        rows.append(
            DteHoldRow(
                label=label,
                n=len(bucket),
                gross_pct=gross,
                net_pct=net,
                fee_pct=fee,
                win_rate_pct=win,
                reason=reason,
                dte_min=dte_min,
                dte_max=dte_max,
                hold_style=hold_style,
                ex_top_n_gross_pct=ex_gross,
                ex_top_n_n=len(trimmed),
                ex_top_n_reason=ex_reason,
            )
        )
    return tuple(rows)


def _et_hours(members: list[_Episode]) -> tuple[HourRow, ...]:
    hours = sorted({item.et_hour for item in members})
    rows: list[HourRow] = []
    for hour in hours:
        bucket = [item for item in members if item.et_hour == hour]
        gross, net, fee, win, reason = _aggregate(bucket)
        rows.append(
            HourRow(
                label=f"{hour:02d}:00 ET",
                n=len(bucket),
                gross_pct=gross,
                net_pct=net,
                fee_pct=fee,
                win_rate_pct=win,
                reason=reason,
                et_hour=hour,
            )
        )
    return tuple(rows)


def _weekdays(members: list[_Episode]) -> tuple[WeekdayRow, ...]:
    # 缺 DTE 的回合排除：无法判定车道，混入会让「0DTE 可用性」这个因读错。
    known = [item for item in members if item.dte is not None]
    rows: list[WeekdayRow] = []
    for index, label in enumerate(WEEKDAY_LABELS):
        bucket = [item for item in known if item.weekday == index]
        gross, net, fee, win, reason = _aggregate(bucket)
        rows.append(
            WeekdayRow(
                label=label,
                n=len(bucket),
                gross_pct=gross,
                net_pct=net,
                fee_pct=fee,
                win_rate_pct=win,
                reason=reason,
                weekday=index,
                zero_dte_n=sum(1 for item in bucket if item.dte == 0),
                dte_1_3_n=sum(
                    1 for item in bucket if item.dte is not None and 1 <= item.dte <= 3
                ),
            )
        )
    return tuple(rows)


def _fee_threshold(members: list[_Episode]) -> FeeThreshold:
    gross, _net, fee_pct, _win, reason = _aggregate(members)
    del gross
    # 费率的样本量按「费用已知」的子集报告：缺 total_fee 的回合不进这个比率，
    # n 里也不冒充它们进了。
    fee_known = [item for item in members if item.fee is not None]
    if members and not fee_known and reason is None:
        reason = "全部样本缺 total_fee，费率标缺"
    return FeeThreshold(
        fee_pct_of_premium=fee_pct,
        n=len(fee_known),
        reason=reason,
        default_ticket_usd=FEE_CALCULATOR_DEFAULT_TICKET_USD,
        default_tickets_per_day=FEE_CALCULATOR_DEFAULT_TICKETS_PER_DAY,
        trading_days_per_month=FEE_CALCULATOR_TRADING_DAYS_PER_MONTH,
        note=FEE_THRESHOLD_NOTE,
    )


def _position_evidence(
    members: list[_Episode],
    *,
    params: ChosenPositionParams,
) -> PositionEvidence:
    returns = sorted(item.pnl / item.risk for item in members)
    breaker_units = (
        Decimal(params.daily_breaker_usd) / Decimal(params.ticket_usd)
        if params.ticket_usd
        else None
    )

    tiers: list[SeverityTier] = []
    for label, definition, percentile in SEVERITY_TIERS:
        if percentile is None:
            loss = Decimal("-1")
        else:
            loss = _percentile(returns, percentile) or Decimal("0")
        if loss >= 0 or breaker_units is None:
            tiers.append(
                SeverityTier(
                    label=label,
                    definition=definition,
                    loss_pct=float(loss * 100) if loss < 0 else None,
                    tickets_to_breaker=None,
                    reason="该分位数不是亏损，无法折算触发笔数",
                )
            )
            continue
        tiers.append(
            SeverityTier(
                label=label,
                definition=definition,
                loss_pct=float((loss * Decimal("100")).quantize(_PERCENT_QUANTUM)),
                tickets_to_breaker=float(
                    (breaker_units / abs(loss)).quantize(Decimal("0.1"))
                ),
            )
        )

    # 观察到的熔断频率：把当日全部回合按选定单笔金额换算成美元后合计。
    by_day: dict[str, list[Decimal]] = {}
    for item in members:
        by_day.setdefault(item.trading_day, []).append(item.pnl / item.risk)
    breach_days = [
        day
        for day, values in by_day.items()
        if Decimal(params.ticket_usd) * sum(values, Decimal("0"))
        <= Decimal(-params.daily_breaker_usd)
    ]
    counts = sorted(len(values) for values in by_day.values())
    median_tickets = (
        float(_percentile([Decimal(value) for value in counts], 50) or Decimal("0"))
        if counts
        else None
    )

    # 累计最大回撤：单一真实历史路径（不是模拟分布的中位数），按固定比例仓位复利。
    # 排序取**平仓时刻**（权益真正变化的时点），并以开仓时刻与标的做确定性 tie-break：
    # 乘法路径的最大回撤对顺序敏感，含糊的排序会让同一份样本给出不同读数。
    sizing = Decimal("0.10")
    ordered = sorted(
        members,
        key=lambda item: (item.realized_at(), item.opened_at, item.underlying),
    )
    equity = Decimal("1")
    peak = Decimal("1")
    max_drawdown: Optional[Decimal] = None
    for item in ordered:
        equity *= Decimal("1") + sizing * (item.pnl / item.risk)
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (equity - peak) / peak
            if max_drawdown is None or drawdown < max_drawdown:
                max_drawdown = drawdown

    return PositionEvidence(
        framing=POSITION_PARAMS_FRAMING,
        params=params,
        severity_tiers=tuple(tiers),
        observed_breach_day_count=len(breach_days),
        observed_trading_day_count=len(by_day),
        observed_breach_one_per_days=(
            round(len(by_day) / len(breach_days), 1) if breach_days else None
        ),
        observed_median_tickets_per_day=median_tickets,
        observed_cadence_caveat=(
            "这个触发频率绑定的是**历史下单节奏**（本样本每日中位 "
            f"{median_tickets:.0f} 笔）。「最多 {params.max_concurrent} 个并发」"
            "会大幅降低每日笔数，因此该频率不能直接搬到新规则下——"
            "它描述过去，不预测未来。"
            if median_tickets is not None
            else "样本内无可用交易日，无法给出触发频率"
        ),
        historical_max_drawdown_pct=(
            float((max_drawdown * Decimal("100")).quantize(_PERCENT_QUANTUM))
            if max_drawdown is not None
            else None
        ),
        historical_drawdown_sizing_pct=float(sizing * 100),
        drawdown_caveat=(
            "每日熔断与累计回撤是两件不同的事：熔断限制的是**单日**已实现亏损，"
            "累计回撤是**多日连续**下滑的叠加结果，日熔断不设上限地约束不了它。"
            "这里的回撤是本样本这一条真实历史路径按固定比例仓位复利的结果，"
            "只有一条路径，不是模拟分布的中位数，也不是对未来回撤的估计。"
        ),
        reason=None if members else "无样本",
    )


def _correlation(members: list[_Episode], *, max_concurrent: int) -> CorrelationEvidence:
    compliant = [
        item for item in members if item.lane in ("intraday_0dte", "overnight_4_7")
    ]
    counts: dict[str, int] = {}
    for item in compliant:
        counts[item.underlying] = counts.get(item.underlying, 0) + 1
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))[:6]
    return CorrelationEvidence(
        tickers=tuple(ClusterTicker(ticker=name, n=count) for name, count in ordered),
        compliant_episode_count=len(compliant),
        max_concurrent=max_concurrent,
        note=CORRELATED_CLUSTER_NOTE,
    )


def get_rules_evidence(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    build_id: Optional[int] = None,
    ticket_usd: int = FEE_CALCULATOR_DEFAULT_TICKET_USD,
    daily_breaker_usd: int = 6000,
    max_concurrent: int = 2,
) -> Optional[RulesEvidenceResult]:
    """把干净口径样本聚合成「交易纪律」页的全部证据表。

    Returns ``None`` when the account has no episode build.  The whole read
    happens in one session and issues only SELECT statements.

    ``build_id`` 显式指定要读的 build；缺省时走与其余 journal 读数**完全相同**
    的默认解析（``_latest_build`` ＝ 已激活 build，无激活记录时回落到最新 CSV
    build）。这一点必须显式：本仓库当前没有任何 activation 记录，默认解析因此
    落在 CSV build #1 上，而用户手上那批取证数字出自 canonical build #3——
    两者样本量与区间都不同。结果里始终回显 ``build_id``/``build_key``，
    页面据此显示「这页在读哪个 build」，绝不静默替换。
    """

    if ticket_usd <= 0 or daily_breaker_usd <= 0 or max_concurrent <= 0:
        raise EpisodeRepositoryError(
            "ticket_usd / daily_breaker_usd / max_concurrent must all be positive"
        )
    if build_id is not None and build_id <= 0:
        raise EpisodeRepositoryError("build_id must be a positive integer")

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if build_id is None:
            build = _latest_build(session, account_key)
        else:
            build = session.execute(
                select(EpisodeBuild).where(
                    EpisodeBuild.id == build_id,
                    EpisodeBuild.account_key == account_key,
                )
            ).scalar_one_or_none()
            if build is None:
                raise EpisodeRepositoryError(
                    f"episode build {build_id} not found for this account"
                )
        if build is None:
            return None
        build_id = int(build.id)
        build_key = str(build.build_key)
        source_kind = _build_source_kind(session, build_id)
        resolved_account_key = str(build.account_key)

        rows = session.execute(
            select(
                PositionEpisode.underlying,
                PositionEpisode.lifecycle_status,
                PositionEpisode.opened_at,
                PositionEpisode.closed_at,
                PositionEpisode.realized_pnl_net,
                PositionEpisode.total_fee,
                PositionEpisode.dte_at_entry,
                PositionEpisode.opening_cash_flow,
                PositionEpisode.average_entry_price,
                PositionEpisode.evidence_summary_json,
            ).where(
                PositionEpisode.episode_build_id == build_id,
                PositionEpisode.account_key == resolved_account_key,
            )
        ).all()

    members: list[_Episode] = []
    excluded_before_clean_basis = 0
    excluded_basis = 0
    excluded_missing_premium = 0
    excluded_not_closed_or_missing_pnl = 0

    for row in rows:
        if str(row.lifecycle_status) != "closed" or row.realized_pnl_net is None:
            excluded_not_closed_or_missing_pnl += 1
            continue
        if row.opened_at is None:
            excluded_not_closed_or_missing_pnl += 1
            continue

        opened_at = _utc(row.opened_at)
        trading_day = _trading_day_utc_minus_4(opened_at)
        if trading_day < RULE_COMPLIANCE_CLEAN_BASIS_START:
            excluded_before_clean_basis += 1
            continue
        if fill_detailed_from_evidence_summary(row.evidence_summary_json) is not True:
            excluded_basis += 1
            continue
        if row.opening_cash_flow is None:
            excluded_missing_premium += 1
            continue
        risk = abs(Decimal(row.opening_cash_flow))
        if risk == 0:
            excluded_missing_premium += 1
            continue

        close_trading_day = (
            _trading_day_utc_minus_4(_utc(row.closed_at))
            if row.closed_at is not None
            else None
        )
        dte = int(row.dte_at_entry) if row.dte_at_entry is not None else None
        et_hour = _et_hour_utc_minus_4(opened_at)
        members.append(
            _Episode(
                underlying=str(row.underlying).strip().upper(),
                trading_day=trading_day,
                close_trading_day=close_trading_day,
                et_hour=et_hour,
                weekday=datetime.fromisoformat(trading_day).weekday(),
                dte=dte,
                lane=classify_rule_lane(
                    dte=dte,
                    opened_et_hour=et_hour,
                    opened_trading_day=trading_day,
                    closed_trading_day=close_trading_day,
                ),
                pnl=Decimal(row.realized_pnl_net),
                # 费用缺席只计缺席：不以 0 冒充过路费（见 _Episode.fee）。
                fee=(
                    Decimal(row.total_fee) if row.total_fee is not None else None
                ),
                risk=risk,
                entry_price=(
                    Decimal(row.average_entry_price)
                    if row.average_entry_price is not None
                    else None
                ),
                opened_at=opened_at,
                closed_at=(
                    _utc(row.closed_at) if row.closed_at is not None else None
                ),
            )
        )

    trading_days = sorted({item.trading_day for item in members})
    params = ChosenPositionParams(
        ticket_usd=ticket_usd,
        daily_breaker_usd=daily_breaker_usd,
        max_concurrent=max_concurrent,
    )

    fee_unknown_count = sum(1 for item in members if item.fee is None)
    limitations = RULES_EVIDENCE_LIMITATIONS
    if fee_unknown_count:
        limitations = limitations + (
            f"{fee_unknown_count} 个回合缺 total_fee：仍进净口径与胜率，"
            "但已从毛口径与费率的分子分母中排除——费用缺席不以 0 冒充。",
        )

    return RulesEvidenceResult(
        account_key=resolved_account_key,
        build_id=build_id,
        build_key=build_key,
        source_kind=source_kind,
        computed_at=datetime.now().astimezone(),
        clean_basis_start=RULE_COMPLIANCE_CLEAN_BASIS_START,
        clean_basis_reason=RULE_COMPLIANCE_CLEAN_BASIS_REASON,
        rule_set_adopted_at=RULE_SET_V2_ADOPTED_AT,
        sample_episode_count=len(members),
        excluded_before_clean_basis=excluded_before_clean_basis,
        excluded_aggregate_or_unknown_basis=excluded_basis,
        excluded_missing_premium=excluded_missing_premium,
        excluded_not_closed_or_missing_pnl=excluded_not_closed_or_missing_pnl,
        fee_unknown_count=fee_unknown_count,
        first_trading_day=trading_days[0] if trading_days else None,
        last_trading_day=trading_days[-1] if trading_days else None,
        banner=RULES_EVIDENCE_BANNER,
        price_band_headline=CONTRACT_PRICE_HEADLINE,
        price_band_boundary_policy=CONTRACT_PRICE_BOUNDARY_POLICY,
        price_bands=_price_bands(members),
        hold_style_basis=HOLD_STYLE_BASIS,
        dte_hold_lanes=_dte_hold_lanes(members),
        et_hours=_et_hours(members),
        weekday_headline=WEEKDAY_HEADLINE,
        weekdays=_weekdays(members),
        fee_threshold=_fee_threshold(members),
        position=_position_evidence(members, params=params),
        correlation=_correlation(members, max_concurrent=max_concurrent),
        overnight_gap_note=OVERNIGHT_GAP_NOTE,
        limitations=limitations,
    )
