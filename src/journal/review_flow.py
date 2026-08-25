# -*- coding: utf-8 -*-
"""引导式日终复盘流的领域逻辑（蓝图 17 §三(a)，Phase A）。

职责：把当日（ET 自然日）的激活 build 回合收拢成 S0-S4 步进器需要的全部
服务端判定——车道判定（复用 :func:`classify_rule_lane`）、四象限落格、
七项过程指标的可自动化部分、持仓出场预登记状态、休息日检票。前端只收集
人的输入，不复算任何判定。

硬红线（与蓝图一致，测试锁定）：

* 盲评优先：密封并主动揭示之前，本模块的 GET 装配结果**不携带任何盈亏
  字段**；:func:`build_reveal_summary` 只对已密封且已记录揭示的会话计算。
* 缺失即标缺：Phase B 才点亮的指标（推送依从率、有效点差支付）永远标缺，
  绝不用手填冒充自动值。
* 禁用指标清单（:data:`BANNED_REVIEW_METRICS`）进代码常量，UI 永不渲染。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.journal.ledger.daily_review_repository import (
    StoredDailyReviewSession,
    get_latest_daily_review_session,
    list_latest_daily_review_sessions,
)
from src.journal.ledger.episode_repository import _latest_build
from src.journal.ledger.models import PositionEpisode, ReviewAnnotation
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.personal_edge import (
    RULE_COMPLIANCE_LANE_RULE_IDS,
    RULE_COMPLIANCE_LANE_VERDICTS,
    classify_rule_lane,
)
from src.storage import get_db

__all__ = [
    "BANNED_REVIEW_METRICS",
    "EXIT_PLAN_REGISTRATION_TAG",
    "FEE_BASELINE_RATIO",
    "MISTAKE_VOCABULARY",
    "PROCESS_METRIC_NAMES",
    "REVIEW_FLOW_LIMITATIONS",
    "ExcursionScatterItem",
    "list_excursion_scatter",
    "DailyEpisodeVerdict",
    "DailyReviewFlowData",
    "OpenPositionExitPlan",
    "ProcessMetric",
    "RevealEpisode",
    "RevealSummary",
    "build_reveal_summary",
    "classify_decision_quadrant",
    "get_daily_review_flow",
    "get_episode_review_verdict",
]

# ---------------------------------------------------------------------------
# 常量（蓝图 §三(c)/(e)）
# ---------------------------------------------------------------------------

# F4：费用基线 ≈ 1.25% 权利金/笔（券商账单实证）。
FEE_BASELINE_RATIO = 0.0125

# 七项过程指标（蓝图 §三(a) 表）。id 固定，任何消费面不得改号。
PROCESS_METRIC_NAMES: dict[int, str] = {
    1: "推送依从率",
    2: "结构依从率（合规车道风险占比）",
    3: "出场规则依从率（Phase A＝出场计划在册率）",
    4: "有效点差支付",
    5: "费用占比（对照 1.25% 基线）",
    6: "决策日志完整率",
    7: "休息纪律",
}

# 禁用指标清单（蓝图 §三(c)，进代码常量，UI 永不渲染）。这些词一旦出现在
# 复盘界面即违反已证事实 F3/F6：合成分把 +30R 的单当波动惩罚；hold-time
# 优化是构造性循环论证；MAE 分位止损截掉的恰是右尾。
BANNED_REVIEW_METRICS: tuple[str, ...] = (
    "sqn",
    "zella_score",
    "composite_consistency_score",
    "sharpe_of_trades",
    "single_trade_score",
    "hold_time_bucket_edge",
    "mae_derived_stop",
    "time_of_day_entry_optimizer",
)

# 损耗归因轨固定 mistake 词表（蓝图 §三(b)，初版；纯常量 + labels 校验）。
MISTAKE_VOCABULARY: tuple[str, ...] = (
    "freelance_no_push",
    "chase_after_expiry",
    "late_0dte",
    "early_exit_fear",
    "size_overrun",
    "revenge_add",
    "plan_absent",
)

# 每个消费面都必须原样携带的边界（同 PERSONAL_EDGE_LIMITATIONS 约定）。
REVIEW_FLOW_LIMITATIONS: tuple[str, ...] = (
    "盲评优先：会话密封并主动点击揭示之前，本接口不返回任何盈亏字段；"
    "揭示顺序（reveal_after_seal 与时间戳）本身入库",
    "车道判定纯机械（classify_rule_lane）：只读 DTE、ET 入场小时与 ET 自然日，"
    "不声称知道进场当时的意图",
    "过程指标缺失即标缺（推送依从率、有效点差支付待 Phase B/C），"
    "手填自评永不冒充自动值",
    "这是对本人自身历史的过程记账，不是因果结论、不是信号、不构成建议；"
    "本系统只读，不下单",
)

_QUADRANT_LABELS: dict[str, str] = {
    "deserved_win": "应得的赢",
    "bad_luck": "坏运气",
    "undeserved_win": "侥幸",
    "deserved_loss": "应得的输",
    "not_judged": "不判定",
}


def classify_decision_quadrant(
    verdict: str,
    pnl_net: Optional[Decimal],
) -> str:
    """Duke 四象限自动落格（过程轴 × 结果轴，永不合成一个分数）。

    合规∧盈利 → 应得的赢；合规∧亏损 → 坏运气（记录后放行，不改规则）；
    违规∧盈利 → **侥幸**（标红，月度首看其趋势）；违规∧亏损 → 应得的输
    （学习价值最高）。uncovered/unknown 不硬归类，盈亏缺失或恰为 0 也不
    判定——标缺绝不冒充。
    """
    if verdict not in {"compliant", "violation"} or pnl_net is None:
        return "not_judged"
    if pnl_net > 0:
        return "deserved_win" if verdict == "compliant" else "undeserved_win"
    if pnl_net < 0:
        return "bad_luck" if verdict == "compliant" else "deserved_loss"
    return "not_judged"


def quadrant_label(quadrant: str) -> str:
    return _QUADRANT_LABELS.get(quadrant, quadrant)


# ---------------------------------------------------------------------------
# 装配结果的数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyEpisodeVerdict:
    """One episode of the day, reduced to blind-safe verdict facts (无盈亏)."""

    episode_id: int
    raw_symbol: str
    underlying: str
    direction: str
    lifecycle_status: str
    opened_at: datetime
    closed_at: Optional[datetime]
    dte_at_entry: Optional[int]
    lane: str
    rule_id: str
    verdict: str
    opened_today: bool
    closed_today: bool
    needs_ack: bool
    acked: bool


@dataclass(frozen=True)
class OpenPositionExitPlan:
    episode_id: int
    raw_symbol: str
    underlying: str
    opened_at: datetime
    dte_at_entry: Optional[int]
    has_exit_plan: bool
    exit_plan_text: str
    exit_plan_registered_at: Optional[datetime]


@dataclass(frozen=True)
class ProcessMetric:
    metric_id: int
    name: str
    basis: str  # auto | manual | missing
    value_ratio: Optional[float]
    value_text: Optional[str]
    numerator: Optional[float]
    denominator: Optional[float]
    reason: Optional[str]
    manual_allowed: bool
    manual_value: Optional[str]


@dataclass(frozen=True)
class DailyReviewFlowData:
    account_key: str
    et_date: str
    build_id: int
    build_key: str
    session: Optional[StoredDailyReviewSession]
    rest_day_candidate: bool
    episodes_today: tuple[DailyEpisodeVerdict, ...]
    open_positions: tuple[OpenPositionExitPlan, ...]
    process_metrics: tuple[ProcessMetric, ...]
    rest_streak: int


@dataclass(frozen=True)
class RevealEpisode:
    episode_id: int
    raw_symbol: str
    verdict: str
    quadrant: str
    quadrant_label: str
    pnl_net: Optional[float]


@dataclass(frozen=True)
class RevealSummary:
    et_date: str
    closed_episode_count: int
    pnl_known_count: int
    total_net: Optional[float]
    quadrant_counts: dict[str, int]
    episodes: tuple[RevealEpisode, ...]


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


# 复核修复 6：复盘流的 ET 换算用真实 America/New_York（DST 正确），不再沿用
# personal_edge 的 UTC−4 近似（该近似仍是 personal_edge 自身文档化口径，
# 这里只切换 review-flow 调用路径）。EST 时段（11 月—3 月）下 UTC−4 会把
# 入场小时多算 1 小时、把 04:00–05:00 UTC 划错自然日。
_ET_ZONE = ZoneInfo("America/New_York")


def _et_moment(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_ET_ZONE)


def _et_hour(value: datetime) -> int:
    """真实 ET 入场小时（DST 感知）。"""
    return _et_moment(value).hour


def _et_day(value: datetime) -> str:
    """真实 ET 自然日（DST 感知）。"""
    return _et_moment(value).strftime("%Y-%m-%d")


def today_et_date() -> str:
    """真实 America/New_York 的今天（DST 感知）。"""
    return datetime.now(_ET_ZONE).strftime("%Y-%m-%d")


def _lane_of(row: Any) -> str:
    opened_at = row.opened_at
    closed_at = row.closed_at
    return classify_rule_lane(
        dte=(int(row.dte_at_entry) if row.dte_at_entry is not None else None),
        opened_et_hour=(
            _et_hour(opened_at) if opened_at is not None else None
        ),
        opened_trading_day=(
            _et_day(opened_at) if opened_at is not None else None
        ),
        closed_trading_day=(
            _et_day(closed_at) if closed_at is not None else None
        ),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# 复核修复 9：S3 出场预登记写 annotation 链时打这个 tag（写入来源留痕）。
# 指标 6 的排除本身按字段集判定（见 _entry_side_filled），对打标机制之前的
# 历史修订同样成立。
EXIT_PLAN_REGISTRATION_TAG = "exit_plan_registration"


def _entry_side_filled(row: ReviewAnnotation) -> bool:
    """指标 6 的「进场决策日志」判定（复核修复 9，规则文档化）：

    只认 ``setup_thesis`` / ``entry_trigger`` / ``position_rationale`` 至少
    一项非空。``invalidation_plan`` 不再计入——S3 出场预登记只写这一个字段
    （其余字段原样抄自既有修订），若它也算「进场决策日志」，登记出场计划
    就会顺带抬高指标 6。出场计划的在册率由指标 3 负责，不重复计。
    """
    return bool(
        str(row.setup_thesis).strip()
        or str(row.entry_trigger).strip()
        or str(row.position_rationale).strip()
    )


@dataclass(frozen=True)
class _EpisodeSnap:
    """Plain per-episode snapshot detached from the ORM session."""

    id: int
    raw_symbol: str
    underlying: str
    direction: str
    lifecycle_status: str
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]
    dte_at_entry: Optional[int]
    opening_cash_flow: Optional[Decimal]
    total_fee: Optional[Decimal]


def _snap(row: PositionEpisode) -> _EpisodeSnap:
    return _EpisodeSnap(
        id=int(row.id),
        raw_symbol=str(row.raw_symbol),
        underlying=str(row.underlying),
        direction=str(row.direction),
        lifecycle_status=str(row.lifecycle_status),
        opened_at=_utc(row.opened_at) if row.opened_at is not None else None,
        closed_at=_utc(row.closed_at) if row.closed_at is not None else None,
        dte_at_entry=(
            int(row.dte_at_entry) if row.dte_at_entry is not None else None
        ),
        opening_cash_flow=(
            Decimal(row.opening_cash_flow)
            if row.opening_cash_flow is not None
            else None
        ),
        total_fee=(
            Decimal(row.total_fee) if row.total_fee is not None else None
        ),
    )


def _missing_metric(
    metric_id: int,
    reason: str,
    *,
    manual_allowed: bool = False,
    manual_value: Optional[str] = None,
    basis: str = "missing",
) -> ProcessMetric:
    return ProcessMetric(
        metric_id=metric_id,
        name=PROCESS_METRIC_NAMES[metric_id],
        basis=basis,
        value_ratio=None,
        value_text=None,
        numerator=None,
        denominator=None,
        reason=reason,
        manual_allowed=manual_allowed,
        manual_value=manual_value,
    )


def _auto_metric(
    metric_id: int,
    *,
    value_ratio: Optional[float],
    value_text: Optional[str] = None,
    numerator: Optional[float] = None,
    denominator: Optional[float] = None,
    reason: Optional[str] = None,
) -> ProcessMetric:
    return ProcessMetric(
        metric_id=metric_id,
        name=PROCESS_METRIC_NAMES[metric_id],
        basis="auto",
        value_ratio=value_ratio,
        value_text=value_text,
        numerator=numerator,
        denominator=denominator,
        reason=reason,
        manual_allowed=False,
        manual_value=None,
    )


def get_daily_review_flow(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    et_date: Optional[str] = None,
) -> Optional[DailyReviewFlowData]:
    """Assemble the blind-safe daily flow for one ET date.

    Returns ``None`` when the account has no episode build.  只发 SELECT；
    响应里**没有任何盈亏字段**（结果揭示走 :func:`build_reveal_summary`，
    且仅对已密封并已记录揭示的会话）。
    """
    target_date = et_date or today_et_date()
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, account_key)
        if build is None:
            return None
        build_id = int(build.id)
        build_key = str(build.build_key)
        resolved_account = str(build.account_key)

    # 会话与休息纪律先读（各自开只读 scope，避免嵌套长事务）。
    latest_session = get_latest_daily_review_session(
        account_key=resolved_account, et_date=target_date
    )
    recent_sessions = list_latest_daily_review_sessions(
        account_key=resolved_account, limit=120
    )
    rest_streak = 0
    for stored in recent_sessions:
        if stored.et_date >= target_date:
            continue
        if stored.session_kind == "rest_day" and stored.sealed_at is not None:
            rest_streak += 1
        else:
            break
    acked_ids: set[int] = set()
    manual_by_metric: dict[int, str] = {}
    if latest_session is not None:
        acked_ids = {
            int(item.get("position_episode_id", 0))
            for item in latest_session.violation_acks
        }
        for item in latest_session.process_scores:
            if item.get("basis") == "manual" and item.get("value") is not None:
                manual_by_metric[int(item["metric_id"])] = str(item["value"])

    with db.session_scope() as session:
        rows = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == build_id,
                PositionEpisode.account_key == resolved_account,
            )
        ).scalars().all()

        episode_ids_today: list[int] = []
        verdicts: list[DailyEpisodeVerdict] = []
        open_rows: list[_EpisodeSnap] = []
        opened_today_rows: list[_EpisodeSnap] = []
        closed_today_rows: list[_EpisodeSnap] = []
        for orm_row in rows:
            row = _snap(orm_row)
            opened_day = (
                _et_day(row.opened_at)
                if row.opened_at is not None
                else None
            )
            closed_day = (
                _et_day(row.closed_at)
                if row.closed_at is not None
                else None
            )
            opened_today = opened_day == target_date
            closed_today = closed_day == target_date
            if row.lifecycle_status == "open":
                open_rows.append(row)
            if not opened_today and not closed_today:
                continue
            if opened_today:
                opened_today_rows.append(row)
            if closed_today:
                closed_today_rows.append(row)
            lane = _lane_of(row)
            verdict = RULE_COMPLIANCE_LANE_VERDICTS[lane]
            episode_ids_today.append(row.id)
            verdicts.append(
                DailyEpisodeVerdict(
                    episode_id=row.id,
                    raw_symbol=row.raw_symbol,
                    underlying=row.underlying,
                    direction=row.direction,
                    lifecycle_status=row.lifecycle_status,
                    opened_at=row.opened_at,
                    closed_at=row.closed_at,
                    dte_at_entry=row.dte_at_entry,
                    lane=lane,
                    rule_id=RULE_COMPLIANCE_LANE_RULE_IDS[lane],
                    verdict=verdict,
                    opened_today=opened_today,
                    closed_today=closed_today,
                    needs_ack=(verdict == "violation" and opened_today),
                    acked=False,
                )
            )

        # 注解（出场预登记 + 决策日志完整率）一次性读进来，转成纯数据。
        relevant_ids = set(episode_ids_today) | {row.id for row in open_rows}
        annotations_by_episode: dict[int, list[dict[str, Any]]] = {}
        if relevant_ids:
            annotation_rows = session.execute(
                select(ReviewAnnotation)
                .where(
                    ReviewAnnotation.account_key == resolved_account,
                    ReviewAnnotation.episode_build_id == build_id,
                    ReviewAnnotation.position_episode_id.in_(
                        sorted(relevant_ids)
                    ),
                )
                .order_by(
                    ReviewAnnotation.position_episode_id.asc(),
                    ReviewAnnotation.revision.asc(),
                )
            ).scalars()
            for annotation in annotation_rows:
                annotations_by_episode.setdefault(
                    int(annotation.position_episode_id), []
                ).append(
                    {
                        "invalidation_plan": str(annotation.invalidation_plan),
                        "created_at": _utc(annotation.created_at),
                        "entry_filled": _entry_side_filled(annotation),
                    }
                )

    verdicts = [
        replace(verdict, acked=verdict.episode_id in acked_ids)
        for verdict in verdicts
    ]

    rest_day_candidate = not verdicts

    # --- 出场预登记状态 ----------------------------------------------------
    open_positions: list[OpenPositionExitPlan] = []
    for row in open_rows:
        plans = [
            item
            for item in annotations_by_episode.get(row.id, [])
            if item["invalidation_plan"].strip()
        ]
        latest_plan = plans[-1] if plans else None
        open_positions.append(
            OpenPositionExitPlan(
                episode_id=row.id,
                raw_symbol=row.raw_symbol,
                underlying=row.underlying,
                opened_at=row.opened_at,
                dte_at_entry=row.dte_at_entry,
                has_exit_plan=latest_plan is not None,
                exit_plan_text=(
                    latest_plan["invalidation_plan"]
                    if latest_plan is not None
                    else ""
                ),
                exit_plan_registered_at=(
                    latest_plan["created_at"]
                    if latest_plan is not None
                    else None
                ),
            )
        )
    open_positions.sort(key=lambda item: item.episode_id)

    # --- 七项过程指标 ------------------------------------------------------
    metrics: list[ProcessMetric] = []

    # 1 推送依从率 —— Phase B 才有 push ledger，标缺（可手填自评）。
    metrics.append(
        _missing_metric(
            1,
            "push ledger 未建（Phase B 起自动）；手填仅作自评，不冒充自动值",
            manual_allowed=True,
            manual_value=manual_by_metric.get(1),
            basis="manual" if 1 in manual_by_metric else "missing",
        )
    )

    # 2 结构依从率：今日新开回合按 |opening_cash_flow| 加权的合规风险占比。
    if not opened_today_rows:
        metrics.append(_missing_metric(2, "今日无新开回合"))
    else:
        compliant_risk = Decimal("0")
        total_risk = Decimal("0")
        missing_premium = 0
        unjudged = 0
        for row in opened_today_rows:
            if row.opening_cash_flow is None:
                missing_premium += 1
                continue
            premium = abs(Decimal(row.opening_cash_flow))
            lane = _lane_of(row)
            verdict = RULE_COMPLIANCE_LANE_VERDICTS[lane]
            if verdict not in {"compliant", "violation"}:
                unjudged += 1
                continue
            total_risk += premium
            if verdict == "compliant":
                compliant_risk += premium
        if total_risk <= 0:
            reason = "今日可判定回合的风险金额合计为 0"
            if missing_premium:
                reason += f"；{missing_premium} 笔缺开仓现金流"
            metrics.append(_missing_metric(2, reason))
        else:
            note_parts = []
            if missing_premium:
                note_parts.append(f"{missing_premium} 笔缺开仓现金流未计入")
            if unjudged:
                note_parts.append(f"{unjudged} 笔 uncovered/unknown 不判定")
            metrics.append(
                _auto_metric(
                    2,
                    value_ratio=float(compliant_risk / total_risk),
                    numerator=float(compliant_risk),
                    denominator=float(total_risk),
                    reason="；".join(note_parts) or None,
                )
            )

    # 3 出场规则依从率（Phase A＝出场计划在册率：平仓前是否已有在册计划）。
    if not closed_today_rows:
        metrics.append(_missing_metric(3, "今日无平仓回合"))
    else:
        first_session_date = min(
            (stored.et_date for stored in recent_sessions),
            default=None,
        )
        registered = 0
        for row in closed_today_rows:
            closed_at = row.closed_at
            if closed_at is None:
                continue
            for item in annotations_by_episode.get(row.id, []):
                if (
                    item["invalidation_plan"].strip()
                    and item["created_at"] < closed_at
                ):
                    registered += 1
                    break
        if registered == 0 and first_session_date is None:
            metrics.append(
                _missing_metric(
                    3,
                    "出场预登记自今日启用；此前平仓的回合标缺（自登记首日起可判）",
                )
            )
        else:
            metrics.append(
                _auto_metric(
                    3,
                    value_ratio=registered / len(closed_today_rows),
                    numerator=float(registered),
                    denominator=float(len(closed_today_rows)),
                    reason="规则出场 vs 情绪出场的机判需结构化计划（Phase B）；"
                    "本值只回答「平仓前是否已有在册出场计划」",
                )
            )

    # 4 有效点差支付 —— 需下单时 mid 快照，Phase B/C，标缺。
    metrics.append(
        _missing_metric(
            4,
            "需下单时 mid 快照（Phase B/C 起自动）；手填仅作自评",
            manual_allowed=True,
            manual_value=manual_by_metric.get(4),
            basis="manual" if 4 in manual_by_metric else "missing",
        )
    )

    # 5 费用占比：今日平仓回合 total_fee / |opening_cash_flow| 对照 1.25%。
    if not closed_today_rows:
        metrics.append(_missing_metric(5, "今日无平仓回合"))
    else:
        fee_total = Decimal("0")
        premium_total = Decimal("0")
        excluded = 0
        for row in closed_today_rows:
            if row.total_fee is None or row.opening_cash_flow is None:
                excluded += 1
                continue
            fee_total += Decimal(row.total_fee)
            premium_total += abs(Decimal(row.opening_cash_flow))
        if premium_total <= 0:
            reason = "今日平仓回合费用/权利金不可比（缺费用或缺开仓现金流）"
            metrics.append(_missing_metric(5, reason))
        else:
            metrics.append(
                _auto_metric(
                    5,
                    value_ratio=float(fee_total / premium_total),
                    numerator=float(fee_total),
                    denominator=float(premium_total),
                    value_text=f"基线 {FEE_BASELINE_RATIO:.2%}（F4）",
                    reason=(
                        f"{excluded} 笔缺费用或开仓现金流未计入"
                        if excluded
                        else None
                    ),
                )
            )

    # 6 决策日志完整率：今日新开回合有「当日进场侧 annotation」的比例。
    # 计数规则见 _entry_side_filled：S3 出场预登记修订与「仅出场计划」修订
    # 不计入（复核修复 9），出场预登记走指标 3，不重复抬高指标 6。
    if not opened_today_rows:
        metrics.append(_missing_metric(6, "今日无新开回合"))
    else:
        logged = 0
        for row in opened_today_rows:
            for item in annotations_by_episode.get(row.id, []):
                if (
                    _et_day(item["created_at"]) == target_date
                    and item["entry_filled"]
                ):
                    logged += 1
                    break
        metrics.append(
            _auto_metric(
                6,
                value_ratio=logged / len(opened_today_rows),
                numerator=float(logged),
                denominator=float(len(opened_today_rows)),
                reason="仅计进场侧字段（thesis/trigger/rationale）非空的当日"
                "修订；S3 出场预登记与单独出场计划不计入",
            )
        )

    # 7 休息纪律：无机会日零交易 + 连续休息计数。
    metrics.append(
        _auto_metric(
            7,
            value_ratio=None,
            value_text=(
                f"今日{'无' if rest_day_candidate else '有'}交易活动；"
                f"已连续 {rest_streak} 个已密封休息日"
            ),
            numerator=float(rest_streak),
            denominator=None,
        )
    )

    return DailyReviewFlowData(
        account_key=resolved_account,
        et_date=target_date,
        build_id=build_id,
        build_key=build_key,
        session=latest_session,
        rest_day_candidate=rest_day_candidate,
        episodes_today=tuple(verdicts),
        open_positions=tuple(open_positions),
        process_metrics=tuple(metrics),
        rest_streak=rest_streak,
    )


def build_reveal_summary(
    account_key: str,
    *,
    build_id: int,
    et_date: str,
) -> RevealSummary:
    """结果揭示（仅在会话已密封且已记录揭示后由端点调用）。

    只描述当日平仓回合：四象限落格 + 已知净盈亏合计。盈亏缺失的回合
    如实计入 ``closed_episode_count`` 但不进合计（标缺不冒充 0）。
    """
    init_ledger_schema()
    db = get_db()
    episodes: list[RevealEpisode] = []
    total_net = Decimal("0")
    pnl_known = 0
    quadrant_counts: dict[str, int] = {
        key: 0 for key in _QUADRANT_LABELS
    }
    with db.session_scope() as session:
        rows = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == build_id,
                PositionEpisode.account_key == account_key,
                PositionEpisode.lifecycle_status == "closed",
            )
        ).scalars().all()
        for orm_row in rows:
            if orm_row.closed_at is None:
                continue
            if (
                _et_day(_utc(orm_row.closed_at))
                != et_date
            ):
                continue
            row = _snap(orm_row)
            pnl = (
                Decimal(orm_row.realized_pnl_net)
                if orm_row.realized_pnl_net is not None
                else None
            )
            lane = _lane_of(row)
            verdict = RULE_COMPLIANCE_LANE_VERDICTS[lane]
            quadrant = classify_decision_quadrant(verdict, pnl)
            quadrant_counts[quadrant] += 1
            if pnl is not None:
                total_net += pnl
                pnl_known += 1
            episodes.append(
                RevealEpisode(
                    episode_id=row.id,
                    raw_symbol=row.raw_symbol,
                    verdict=verdict,
                    quadrant=quadrant,
                    quadrant_label=quadrant_label(quadrant),
                    pnl_net=float(pnl) if pnl is not None else None,
                )
            )
    episodes.sort(key=lambda item: item.episode_id)
    return RevealSummary(
        et_date=et_date,
        closed_episode_count=len(episodes),
        pnl_known_count=pnl_known,
        total_net=float(total_net) if pnl_known else None,
        quadrant_counts=quadrant_counts,
        episodes=tuple(episodes),
    )


@dataclass(frozen=True)
class ExcursionScatterItem:
    """One point of the read-only MAE% × R diagnostic scatter（只看形态）。"""

    episode_id: int
    raw_symbol: str
    hold_structure: str  # intraday | overnight | unknown
    status: str
    mae_underlying_pct: Optional[float]
    mfe_underlying_pct: Optional[float]
    mae_atr: Optional[float]
    mfe_atr: Optional[float]
    r_multiple: Optional[float]
    r_missing_reason: Optional[str]


def list_excursion_scatter(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    build_id: Optional[int] = None,
) -> Optional[tuple[int, tuple[ExcursionScatterItem, ...]]]:
    """Join stored excursions with episode R for the diagnostic scatter.

    R = realized_pnl_net / |opening_cash_flow|；任一缺失 → R 标缺（带原因），
    点仍返回（偏移诊断与 R 各自诚实）。返回 ``(build_id, items)``；无 build
    时 ``None``。只读诊断——散点永远与
    :data:`src.journal.excursions.EXCURSION_DISCLAIMER` 同屏。
    """
    from src.journal.ledger.excursion_repository import list_episode_excursions

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if build_id is None:
            build = _latest_build(session, account_key)
            if build is None:
                return None
            build_id = int(build.id)
    excursions = list_episode_excursions(episode_build_id=build_id)
    if not excursions:
        return build_id, ()
    episode_ids = [item.position_episode_id for item in excursions]
    snaps: dict[int, _EpisodeSnap] = {}
    with db.session_scope() as session:
        rows = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == build_id,
                PositionEpisode.id.in_(episode_ids),
            )
        ).scalars()
        pnl_by_id: dict[int, Optional[Decimal]] = {}
        for orm_row in rows:
            snaps[int(orm_row.id)] = _snap(orm_row)
            pnl_by_id[int(orm_row.id)] = (
                Decimal(orm_row.realized_pnl_net)
                if orm_row.realized_pnl_net is not None
                else None
            )
    items: list[ExcursionScatterItem] = []
    for stored in excursions:
        snap = snaps.get(stored.position_episode_id)
        if snap is None:
            continue
        if snap.opened_at is not None and snap.closed_at is not None:
            hold_structure = (
                "intraday"
                if _et_day(snap.opened_at)
                == _et_day(snap.closed_at)
                else "overnight"
            )
        else:
            hold_structure = "unknown"
        pnl = pnl_by_id.get(stored.position_episode_id)
        premium = (
            abs(snap.opening_cash_flow)
            if snap.opening_cash_flow is not None
            else None
        )
        if pnl is None:
            r_multiple, r_reason = None, "净盈亏未知"
        elif premium is None or premium == 0:
            r_multiple, r_reason = None, "开仓现金流缺失或为 0，R 标缺"
        else:
            r_multiple, r_reason = float(pnl / premium), None
        items.append(
            ExcursionScatterItem(
                episode_id=stored.position_episode_id,
                raw_symbol=snap.raw_symbol,
                hold_structure=hold_structure,
                status=stored.status,
                mae_underlying_pct=stored.mae_underlying_pct,
                mfe_underlying_pct=stored.mfe_underlying_pct,
                mae_atr=stored.mae_atr,
                mfe_atr=stored.mfe_atr,
                r_multiple=r_multiple,
                r_missing_reason=r_reason,
            )
        )
    return build_id, tuple(items)


@dataclass(frozen=True)
class EpisodeReviewVerdict:
    """Per-trade verdict block for 区①/③/④（单笔深潜）。"""

    episode_id: int
    build_id: int
    lane: str
    rule_id: str
    verdict: str
    quadrant: str
    quadrant_label: str
    counterfactual_compliant_excluded: Optional[bool]
    counterfactual_compliant_text: str
    counterfactual_gate_status: str
    counterfactual_gate_reason: str


def get_episode_review_verdict(
    episode_id: int,
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    build_id: Optional[int] = None,
) -> Optional[EpisodeReviewVerdict]:
    """Mechanical verdict + the two blueprint counterfactuals for one episode.

    反事实只有两个、永不叠加：A「仅合规车道」可机械计算（violation 单在
    反事实中被移除）；B「gate 执行」在 gate 逐日记录回填（E-4/Phase C）
    之前标缺——绝不现算冒充历史 gate 状态。
    """
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if build_id is None:
            build = _latest_build(session, account_key)
            if build is None:
                return None
            build_id = int(build.id)
        row = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.id == episode_id,
                PositionEpisode.episode_build_id == build_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        lane = _lane_of(row)
        verdict = RULE_COMPLIANCE_LANE_VERDICTS[lane]
        pnl = (
            Decimal(row.realized_pnl_net)
            if row.realized_pnl_net is not None
            and str(row.lifecycle_status) == "closed"
            else None
        )
    quadrant = classify_decision_quadrant(verdict, pnl)
    if verdict == "violation":
        excluded: Optional[bool] = True
        if pnl is not None:
            compliant_text = (
                "在「仅合规车道」反事实中本单被移除"
                f"（贡献 {float(pnl):+,.2f}）。移除式反事实假设无替代行为，"
                "是对本人历史的记账，非预测。"
            )
        else:
            compliant_text = (
                "在「仅合规车道」反事实中本单被移除（净盈亏未知，贡献标缺）。"
            )
    elif verdict == "compliant":
        excluded = False
        compliant_text = "本单为合规车道，在「仅合规车道」反事实中保留。"
    else:
        excluded = None
        compliant_text = (
            "车道未覆盖或不可判定：不硬归类，也不进入反事实。"
        )
    return EpisodeReviewVerdict(
        episode_id=int(episode_id),
        build_id=int(build_id),
        lane=lane,
        rule_id=RULE_COMPLIANCE_LANE_RULE_IDS[lane],
        verdict=verdict,
        quadrant=quadrant,
        quadrant_label=quadrant_label(quadrant),
        counterfactual_compliant_excluded=excluded,
        counterfactual_compliant_text=compliant_text,
        counterfactual_gate_status="missing",
        counterfactual_gate_reason=(
            "gate 逐日记录未回填（E-4 / Phase C）：历史 gate 状态不可机械"
            "重放——标缺，不现算冒充"
        ),
    )
