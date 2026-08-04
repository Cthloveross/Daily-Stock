# -*- coding: utf-8 -*-
"""Zero-write personal-edge aggregation over the default episode build.

个人画像回灌盘面：把用户自己的已平仓回合（当前默认 build）按标的、持仓时长、
DTE 与月份做纯 SELECT 描述统计，供 /intraday 扫描表「你的战绩」列与临期合约
面板的 DTE 提示消费。

Hard boundaries (same family as ``review_insights``):

* pure SELECT derivation over the effective default build — the same build
  resolution the journal endpoints already use (``_latest_build``); no rows
  are written and nothing is cached here (the API layer owns its TTL cache);
* only closed episodes with a known ``realized_pnl_net`` enter any stat;
  open episodes and closed episodes without net P&L are counted separately
  and never enter the math (缺席即缺席，不以 0 冒充);
* per-underlying stats fail closed below ``min_underlying_episode_count``
  (default 5) — small samples are reported as excluded counts only;
* monthly buckets convert UTC ``opened_at`` with a fixed UTC−4 approximation
  and say so (early-March EST is UTC−5; boundary rows may shift ±1 hour);
* everything is descriptive, not causal — the mandatory limitation strings
  ship inside the result so every consumer renders the caveat verbatim.

规模与频率纪律（``discipline`` block, 2026-08-04；口径订正 2026-08-04 晚）
--------------------------------------------------------------------

Why this block exists — a full-sample forensic study of the user's own option
episodes (2026-03-04→07-31, episode build #3) asked why April→July P&L
"decayed", and found the three usual suspects statistically flat:

* entry geometry — chase share 52.0% → 56.6%, χ² month × class p=0.105;
* market follow-through — median forward-30m MFE 0.370 → 0.341, Kruskal
  p=0.337 across all five months (the months are indistinguishable);
* stop discipline — median loss −22.6% → −24.4% of premium (stable).

口径订正（本次改动的理由）
~~~~~~~~~~~~~~~~~~~~~~~~~~
A follow-up study invalidated the headline this block used to imply.  The
monthly ``pnl_per_dollar_risked`` fall (4.85% Apr → −0.19% Jul) is **75%
measurement artifact**, and shipping it as one continuous trend line is
actively misleading:

* 245 of build #3's episodes carry ``evidence_summary_json.fill_allocations
  == 0`` — built from aggregate ORDER rows with no detailed fills.  They are
  exactly March + Apr 1–20.  The Apr 20/21 boundary is ~90 days before the
  2026-07-21 CSV export, i.e. the broker's detailed-fill retention window —
  not a market or behaviour boundary;
* aggregate-only episodes show gross edge 9.64% vs 1.77% for fill-detailed
  ones (5.4x).  **Within April alone: 9.53% vs 2.42%** — same trader, same
  month, same market.  That is the signature of an understated denominator
  (4.08% of aggregate rows return >+200% vs 1.84% of fill rows; median
  allocation count 2 vs 3).  The canonical projection already flags the cause:
  ``aggregate_order_amount_policy = "audit_only_not_execution_cash_flow"``;
* on the apples-to-apples window (Apr 20 – Jul 31) the gross per-dollar edge
  is 2.42 / 2.58 / 2.00 / 1.22%, ALL pairwise permutation tests p ≥ 0.756,
  Kruskal p=0.887, daily edge vs calendar time rho=+0.126 p=0.295.  **There is
  no measurable erosion.**  Equal-weighted per trade, July is second-best.

What IS real: risk per trade +68% ($6,760 → $11,340) while dollar P&L per
trade stayed flat ($163 → $138); and the fee toll is constant at ~119–138 bp
of premium ($3.29–3.31 round-turn per contract every month), which at ~1.2%
gross edge makes July's NET edge −0.19%.  And the single most honest number:
over the clean window the top 5 of 1,410 trades produced 80% of gross P&L —
excluding each month's top 5 the per-dollar edge is NEGATIVE in all four
months (Apr −3.53% / May −0.91% / Jun −0.86% / Jul −1.77%).

Consequences encoded here
~~~~~~~~~~~~~~~~~~~~~~~~~
* the provenance discriminator is ``evidence_summary_json.fill_allocations``,
  NOT ``has_exact_fill_times``.  The causal question is "was this episode built
  from detailed fills?", and the two fields disagree (3 April rows in build #3
  are fill-detailed yet flagged ``has_exact_fill_times=0``).  Both ship:
  ``fill_detailed_share`` governs comparability, ``exact_fill_share`` stays for
  continuity — see ``FILL_DETAILED_GOVERNS`` for the rule consumers apply;
* every month that is not 100% fill-detailed carries ``basis_break=True`` +
  ``basis_break_reason``, so consumers render a BROKEN series instead of
  blending incomparable denominators into one trend line;
* the two decision-relevant readings ship additively:
  ``fee_pct_of_premium_at_risk`` (the constant toll) next to
  ``gross_pct_of_premium_at_risk`` (net + fees) so "above/below the toll" is
  visible at a glance, and ``pnl_per_dollar_excluding_top_n`` /
  ``gross_pct_excluding_top_n`` — the per-dollar analogue of ``body_pnl``.

This block recomputes everything from the same default build, monthly and over
the trailing ``DISCIPLINE_WINDOW_TRADING_DAYS`` trading days.  It stays
descriptive: it is a mirror, not advice, and every ratio fails closed (null +
reason) rather than showing a number its denominator cannot support.

车道遵守度（``rule_compliance`` block, 2026-08-04）
--------------------------------------------------

Why this block exists — the user derived a two-lane rule set ("规则 v2",
Playbook candidates 「V2-0」…「V2-D」) from their own clean-basis history and
adopted it.  This block is the **forward falsification instrument**: it
classifies every closed episode into a lane purely mechanically and reports
合规单 vs 违规单 per-dollar edge, so the rule set can be confirmed or refuted
by the user's own subsequent trades rather than by argument.

Clean basis (``RULE_COMPLIANCE_CLEAN_BASIS_START``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The population is the same clean basis the rules were derived on: closed
episodes with a known ``realized_pnl_net`` **and** a known
``opening_cash_flow``, built from detailed fills
(``evidence_summary_json.fill_allocations > 0``) and opened on/after
2026-04-21 ET.  Everything before that boundary came from aggregate ORDER rows
whose risk denominator the pipeline itself marks
``audit_only_not_execution_cash_flow`` — mixing it in would silently inflate
every per-dollar reading (see 口径订正 above).  ``risk`` is
``ABS(opening_cash_flow)`` and ``gross`` is ``realized_pnl_net + total_fee``,
the exact definitions the rule set quotes.

The evidence the lanes encode (build #3, n=1,407)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **同一 4-7DTE 合约**：隔夜持有 gross **+34.13%** (n=65, win 61.5%, toll
  1.55%, 剔除最好 3 笔仍 +22.27%、最好 5 笔仍 +19.13% — the **only**
  tail-robust bucket in the whole sample); 当日平掉 **−4.04%** (n=57, win
  22.8%, 剔除最好 5 笔 −10.28%);
* **0DTE 日内** +3.44% (n=556, toll 1.76%, 剔除最好 5 笔 +0.41% → 尾部驱动);
  按 ET 小时 9 点 +10.6% (n=160)、10 点 +2.7%、11 点 +4.9%，而 **12:00 之后
  −8.19%** (剔除最好 5 笔 −15.54%);
* **1-3DTE**：当日平 −2.94% (n=469, 剔除最好 5 笔 −6.45%)，隔夜 +6.98% 但剔除
  最好 5 笔 −4.03%（纯尾部驱动）——两头都不占，整段排除;
* **隔夜进场时段**：ET 11:00–12:00 (−1.76%) 与 13:00–14:00 (−2.99%) 明显偏弱，
  其余时段 +21%…+36%;
* **仓位纪律**：单笔风险 p25–p90 为 5,180–15,750（最大 70,375）；70 个交易日中
  51% 为亏损日；最差单日 −82,130 恰好发生在投入最大的一天（498,743）；峰值累计
  +205,619 之后最大回撤 −163,621。

Hard boundaries of this block
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* classification is **purely mechanical** — only ``dte_at_entry``, the ET hour
  of ``opened_at``, the ET dates of ``opened_at``/``closed_at`` and
  ``ABS(opening_cash_flow)`` are read.  It cannot know what the user *intended*
  at entry, so it never claims to;
* the exclude-top-N readings reuse the discipline block's machinery
  (:func:`_exclude_top_n_readings`) and its ``n >= 15`` gate verbatim — a bucket
  below the gate returns null + reason, never a truncated number;
* two slices ship side by side: ``all_history`` (the whole clean basis, i.e.
  how the rules were derived) and ``since_adoption`` (from
  ``RULE_SET_V2_ADOPTED_AT``, the forward test).  The forward slice is
  legitimately empty at first and says so explicitly
  (``no_episodes_since_adoption``) instead of rendering zeros;
* everything is descriptive: a single trader, a single market regime
  (2026-04→07, SPY rising — overnight longs were structurally favoured), n=65
  for the star bucket, and overnight gap risk is under-represented in that
  window.  **Not advice, not a signal, and no order is ever placed.**
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Optional

from sqlalchemy import select

from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    _latest_build,
)
from src.journal.ledger.models import PositionEpisode
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.ledger.review_insights import _build_source_kind
from src.storage import get_db

__all__ = [
    "DEFAULT_MIN_UNDERLYING_EPISODE_COUNT",
    "DISCIPLINE_BODY_MIN_EPISODE_COUNT",
    "DISCIPLINE_BODY_TRIM_COUNT",
    "DISCIPLINE_WINDOW_TRADING_DAYS",
    "DTE_BUCKETS",
    "FILL_DETAILED_GOVERNS",
    "INTRADAY_LANE_DAILY_TICKET_LIMIT",
    "INTRADAY_LANE_DTE",
    "INTRADAY_LANE_ET_CUTOFF_HOUR",
    "OVERNIGHT_LANE_CONCURRENT_LIMIT",
    "OVERNIGHT_LANE_MAX_DTE",
    "OVERNIGHT_LANE_MIN_DTE",
    "OVERNIGHT_LANE_WEAK_ENTRY_ET_HOURS",
    "REASON_FEE_MISSING",
    "REASON_NO_PREMIUM",
    "REASON_ZERO_PREMIUM",
    "RULE_COMPLIANCE_CLEAN_BASIS_REASON",
    "RULE_COMPLIANCE_CLEAN_BASIS_START",
    "RULE_COMPLIANCE_LANE_RULE_IDS",
    "RULE_COMPLIANCE_LANE_VERDICTS",
    "RULE_COMPLIANCE_LIMITATIONS",
    "RULE_LANES",
    "RULE_SET_V2_ADOPTED_AT",
    "RULE_SET_V2_ID",
    "RULE_VERDICTS",
    "RuleComplianceDailyBudget",
    "RuleComplianceLaneStat",
    "RuleComplianceSlice",
    "RuleComplianceStat",
    "classify_rule_lane",
    "fill_detailed_from_evidence_summary",
    "HOLD_TIME_BUCKETS",
    "MONTH_BASIS_UTC_MINUS_4",
    "PERSONAL_EDGE_LIMITATIONS",
    "PERSONAL_EDGE_SCHEMA_VERSION",
    "PersonalEdgeDiscipline",
    "PersonalEdgeDisciplineMonth",
    "PersonalEdgeDisciplineStats",
    "PersonalEdgeDisciplineWindow",
    "PersonalEdgeDteBucket",
    "PersonalEdgeHoldBucket",
    "PersonalEdgeMonthlyBucket",
    "PersonalEdgeResult",
    "PersonalEdgeRuleCompliance",
    "PersonalEdgeUnderlyingStat",
    "get_personal_edge_stats",
]


PERSONAL_EDGE_SCHEMA_VERSION = "journal-personal-edge/1.0"
DEFAULT_MIN_UNDERLYING_EPISODE_COUNT = 5
MONTH_BASIS_UTC_MINUS_4 = "opened_at_utc_minus_4_approximation"

# 规模与频率：当前窗口取 build 内实际存在的最后 N 个（有入场的）交易日；
# 「本体盈亏」去掉当月最好/最差各 5 笔，样本 <15 笔时不成立（返回 null + 原因）。
DISCIPLINE_WINDOW_TRADING_DAYS = 20
DISCIPLINE_BODY_TRIM_COUNT = 5
DISCIPLINE_BODY_MIN_EPISODE_COUNT = 15

# 剔除最好 N 笔＝与 body_pnl 同一个 N（去掉尾部赢家后的每美元回报）。
DISCIPLINE_EXCLUDE_TOP_N = DISCIPLINE_BODY_TRIM_COUNT

# Fail-closed reasons for every derived discipline ratio (surfaced verbatim).
REASON_NO_TRADING_DAY = "窗口内无入场交易日"
REASON_NO_PREMIUM = "无可用开仓现金流（opening_cash_flow 缺失）"
REASON_ZERO_PREMIUM = "开仓风险金额合计为 0"
REASON_NO_DTE = "无已知进场 DTE"
REASON_NO_SAMPLE = "无样本"
REASON_FEE_MISSING = "部分回合缺 total_fee，费用门槛与毛口径无法如实计算"

# 哪一个字段说了算：口径可比性由「是否由明细成交构建」决定，不是 has_exact_fill_times。
FILL_DETAILED_GOVERNS = (
    "口径可比性由 fill_detailed_share 判定（evidence_summary_json.fill_allocations>0，"
    "即该回合确由明细成交构建）；exact_fill_share 为 has_exact_fill_times 均值、"
    "仅作历史连续性保留。两者会不一致——build #3 有 3 笔 4 月回合由明细成交构建但"
    "has_exact_fill_times=0——不一致时以 fill_detailed_share 为准"
)

# Mandatory honesty strings — consumers surface these verbatim.
PERSONAL_EDGE_LIMITATIONS = (
    "持仓时长与结果存在内生性（止损单天然短），描述统计不是因果结论，不构成建议",
    "月度口径按 ET≈UTC−4 近似换算 opened_at（UTC）；2026-03 月初为 EST（UTC−5），"
    "近午夜边界样本可能偏移 ±1 小时",
    "部分回合由汇总 ORDER 行构建（evidence_summary_json.fill_allocations=0，无明细成交，"
    "集中在 2026-03 与 2026-04-20 之前）：其风险金额分母被低估，管线自身把该金额标记为"
    "aggregate_order_amount_policy=audit_only_not_execution_cash_flow，"
    "因此这些月份的每美元口径与后续全明细月份**不可比**——basis_break=true 的月份"
    "必须断开显示，不得与后续月份连成一条趋势线",
    "汇总口径与明细口径的差异是分母假象而非边际衰减：同在 2026-04 内，汇总口径毛每美元 "
    "9.53%、明细口径 2.42%（同一人、同一月、同一行情）；两个 20/21 日边界≈券商明细成交"
    "保留窗口（导出日前约 90 天），不是行情或行为边界",
    FILL_DETAILED_GOVERNS,
    "规模与频率的派生比率一律 fail-closed：分母为 0、无可用开仓现金流、缺 total_fee 或"
    f"样本不足（本体盈亏与剔除最好 {DISCIPLINE_EXCLUDE_TOP_N} 笔均需 ≥"
    f"{DISCIPLINE_BODY_MIN_EPISODE_COUNT} 笔）时返回 null 并给出原因，绝不以 0 或截断样本冒充",
    "每美元回报为净口径（已扣费）；毛口径 = 净 + 费用，费用门槛 fee_pct_of_premium_at_risk "
    "是恒定过路费——毛口径低于门槛即净口径为负",
)

# ---------------------------------------------------------------------------
# 车道遵守度（rule_compliance）常量：本人规则 v2 的机器可判定部分
#
# 这些常量是 Playbook 候选「V2-0」…「V2-D」里数字的唯一代码真源；UI 文案引用
# 规则编号，数值一律从这里（经端点）读出来，前端不硬编码。
# ---------------------------------------------------------------------------
RULE_SET_V2_ID = "v2"

# 规则采纳日（ET 自然日，用户的下一个交易日）。它是「前向验证」的起点：
# 在这一天之前的回合只说明规则从哪里推出来，之后的回合才能证伪或确认它。
RULE_SET_V2_ADOPTED_AT = "2026-08-05"

# 干净口径起点：4/20–21 是券商明细成交保留窗口的边界（CSV 导出日前约 90 天），
# 不是行情或行为边界；更早的回合由汇总 ORDER 行构建，风险金额分母被低估。
RULE_COMPLIANCE_CLEAN_BASIS_START = "2026-04-21"
RULE_COMPLIANCE_CLEAN_BASIS_REASON = (
    "样本＝已平仓、净盈亏与开仓现金流均已知、且由明细成交构建"
    "（evidence_summary_json.fill_allocations>0）、ET 入场日 ≥ "
    f"{RULE_COMPLIANCE_CLEAN_BASIS_START} 的回合；更早的回合由汇总 ORDER 行构建，"
    "风险金额分母被管线自身标记 audit_only_not_execution_cash_flow（仅供审计，"
    "不是执行现金流），混入会系统性抬高每美元读数，故整段排除"
)

# V2-A：日内车道＝仅 0DTE，ET 12:00 之后不开新的 0DTE。
INTRADAY_LANE_DTE = 0
INTRADAY_LANE_ET_CUTOFF_HOUR = 12
# V2-B：过夜车道＝4-7DTE，至少持有到下一交易日。
OVERNIGHT_LANE_MIN_DTE = 4
OVERNIGHT_LANE_MAX_DTE = 7
# V2-B：隔夜单在这两个 ET 小时明显偏弱（−1.76% / −2.99%），其余时段 +21%…+36%。
OVERNIGHT_LANE_WEAK_ENTRY_ET_HOURS: tuple[int, ...] = (11, 13)
# V2-D③：日内单每日最多 6 笔；同时持有的过夜单不超过 3 个。
INTRADAY_LANE_DAILY_TICKET_LIMIT = 6
OVERNIGHT_LANE_CONCURRENT_LIMIT = 3

# 车道标识（合规两条 + 违规三条 + 规则未覆盖 + 不可判定）。
RULE_LANES: tuple[str, ...] = (
    "intraday_0dte",
    "overnight_4_7",
    "dte_1_3",
    "bought_time_unused",
    "late_0dte",
    "other",
    "unknown",
)

# 判定：合规 / 违规 / 规则未覆盖 / 不可判定。
# `other`＝ ≥8DTE 隔夜——两条车道规则都没有覆盖它，既不算合规也不算违规。
# `unknown`＝缺 DTE 或缺日期，无法机械判定：缺席即缺席，绝不并入 `other`。
RULE_COMPLIANCE_LANE_VERDICTS: dict[str, str] = {
    "intraday_0dte": "compliant",
    "overnight_4_7": "compliant",
    "dte_1_3": "violation",
    "bought_time_unused": "violation",
    "late_0dte": "violation",
    "other": "uncovered",
    "unknown": "unknown",
}
RULE_VERDICTS: tuple[str, ...] = (
    "compliant",
    "violation",
    "uncovered",
    "unknown",
)

# 每条车道对应的规则编号（UI 文案必须引用它，不得自行改写规则内容）。
RULE_COMPLIANCE_LANE_RULE_IDS: dict[str, str] = {
    "intraday_0dte": "V2-A",
    "overnight_4_7": "V2-B",
    "dte_1_3": "V2-C①",
    "bought_time_unused": "V2-C②",
    "late_0dte": "V2-C③",
    "other": "V2-0",
    "unknown": "V2-0",
}

# 每一个消费面都必须原文携带的边界（与 PERSONAL_EDGE_LIMITATIONS 同一约定）。
RULE_COMPLIANCE_LIMITATIONS: tuple[str, ...] = (
    "样本窗口仅 2026-04→07 一个市场状态（SPY 上行），隔夜多头在该状态下天然占优，"
    "下跌市可能完全不同",
    "唯一尾部稳健的组合（4-7DTE 隔夜）n=65 偏小，隔夜跳空风险在该窗口内未被充分体现",
    "车道判定纯机械：只读 dte_at_entry、opened_at 的 ET 小时、opened_at/closed_at 的"
    "ET 自然日与 ABS(opening_cash_flow)——它无从知道进场当时的意图，因此也不声称知道",
    "这是对本人自身历史的描述统计与规则遵守度记账，不是因果结论、不是信号、不构成建议；"
    "本系统只读，不下单",
    RULE_COMPLIANCE_CLEAN_BASIS_REASON,
)

# Fail-closed reasons for the compliance block.
REASON_COMPLIANCE_NO_SAMPLE = "该车道在本区间无样本"
REASON_COMPLIANCE_ZERO_RISK = "该车道风险金额合计为 0"


# (label, min_seconds inclusive, max_seconds exclusive; None = unbounded)
HOLD_TIME_BUCKETS: tuple[tuple[str, int, Optional[int]], ...] = (
    ("<10m", 0, 600),
    ("10-30m", 600, 1_800),
    ("30-60m", 1_800, 3_600),
    ("1-3h", 3_600, 10_800),
    ("3-6h", 10_800, 21_600),
    ("6h-1d", 21_600, 86_400),
    (">1d", 86_400, None),
)

# (label, min_dte inclusive, max_dte inclusive; None = unbounded)
DTE_BUCKETS: tuple[tuple[str, int, Optional[int]], ...] = (
    ("0", 0, 0),
    ("1-3", 1, 3),
    ("4-7", 4, 7),
    ("8-30", 8, 30),
    (">30", 31, None),
)


def fill_detailed_from_evidence_summary(raw: object) -> Optional[bool]:
    """回合是否**由明细成交构建**——口径可比性的因果判据。

    Reads ``evidence_summary_json.fill_allocations``: ``>0`` means the episode
    was built from detailed FILL rows; ``0`` means it came from aggregate ORDER
    rows only, whose amount the canonical projection itself marks
    ``audit_only_not_execution_cash_flow`` (an understated risk denominator).

    Fails closed to ``None`` (= provenance unknown, never assumed clean) for
    every shape that cannot answer the question: NULL, empty/blank text,
    invalid JSON, a non-object payload, a missing ``fill_allocations`` key
    (older rows and test fixtures write ``{}``), a bool (``True``/``False`` are
    ``int`` subclasses in Python and must not read as 1/0 allocations), a
    non-integer number, or a negative count.  Extra keys are ignored — build #3
    carries ``group_fee_unallocated`` on 2 rows and must still parse.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return None
    else:
        payload = raw
    if not isinstance(payload, dict):
        return None
    if "fill_allocations" not in payload:
        return None
    value = payload["fill_allocations"]
    # bool 是 int 的子类：True 绝不能被读成「1 笔明细成交」。
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value > 0


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_RATE_QUANTUM = Decimal("0.0001")
_AMOUNT_QUANTUM = Decimal("0.01")
# 每美元回报量级很小（0.24% = 0.0024），需要比胜率更细的量化步长。
_RATIO_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class PersonalEdgeUnderlyingStat:
    """Per-underlying stats; only present at/above the sample threshold."""

    underlying: str
    n: int
    net: float
    win_rate: float
    fees: float


@dataclass(frozen=True)
class PersonalEdgeHoldBucket:
    """One hold-duration bucket over ``hold_seconds``."""

    bucket: str
    n: int
    net: float
    win_rate: Optional[float]
    avg_win: Optional[float]
    avg_loss: Optional[float]


@dataclass(frozen=True)
class PersonalEdgeDteBucket:
    """One DTE-at-entry bucket (options only; unknown reported separately)."""

    bucket: str
    n: int
    net: float
    win_rate: Optional[float]


@dataclass(frozen=True)
class PersonalEdgeMonthlyBucket:
    """One calendar month (ET≈UTC−4 approximation of ``opened_at``)."""

    month: str
    n: int
    net: float
    fees: float
    win_rate: Optional[float]


@dataclass(frozen=True)
class PersonalEdgeDisciplineStats:
    """规模与频率纪律读数（月度或当前窗口共用同一套口径）.

    Every ratio is null-with-reason rather than 0 when its denominator cannot
    support it; ``exact_fill_share`` below 1 marks reconstructed fills.
    """

    n: int
    trading_day_count: int
    trades_per_day: Optional[float]
    trades_per_day_reason: Optional[str]
    premium_known_count: int
    premium_missing_count: int
    median_premium_at_risk: Optional[float]
    total_premium_at_risk: Optional[float]
    premium_reason: Optional[str]
    net_pnl: float
    pnl_per_dollar_risked: Optional[float]
    pnl_per_dollar_risked_reason: Optional[str]
    median_episode_pnl: Optional[float]
    body_pnl: Optional[float]
    body_episode_count: Optional[int]
    body_pnl_reason: Optional[str]
    dte_known_count: int
    zero_dte_share: Optional[float]
    zero_dte_reason: Optional[str]
    exact_fill_share: Optional[float]
    has_reconstructed_fills: bool
    # --- 口径来源（additive 2026-08-04）：fill_detailed_share 说了算 ---------
    fill_detailed_count: int
    aggregate_only_count: int
    fill_provenance_unknown_count: int
    fill_detailed_share: Optional[float]
    basis_break: bool
    basis_break_reason: Optional[str]
    # --- 费用门槛与毛口径（additive 2026-08-04）-----------------------------
    fees_total: Optional[float]
    fees_missing_count: int
    fee_pct_of_premium_at_risk: Optional[float]
    fee_pct_of_premium_at_risk_reason: Optional[str]
    gross_pct_of_premium_at_risk: Optional[float]
    gross_pct_of_premium_at_risk_reason: Optional[str]
    # --- 剔除最好 N 笔后的每美元回报（additive 2026-08-04）------------------
    pnl_per_dollar_excluding_top_n: Optional[float]
    gross_pct_excluding_top_n: Optional[float]
    excluding_top_n_count: Optional[int]
    excluding_top_n_reason: Optional[str]


@dataclass(frozen=True)
class PersonalEdgeDisciplineMonth:
    """One calendar month of discipline readings (same月度口径 as ``monthly``)."""

    month: str
    stats: PersonalEdgeDisciplineStats


@dataclass(frozen=True)
class PersonalEdgeDisciplineWindow:
    """Trailing-N-trading-day window over the days present in the build."""

    requested_trading_days: int
    start_date: Optional[str]
    end_date: Optional[str]
    stats: PersonalEdgeDisciplineStats


@dataclass(frozen=True)
class PersonalEdgeDiscipline:
    """规模与频率监控：per-dollar edge 与仓位/频率并排，月度 + 当前窗口。"""

    monthly: tuple[PersonalEdgeDisciplineMonth, ...]
    current_window: PersonalEdgeDisciplineWindow
    body_trim_count: int
    body_min_episode_count: int
    exclude_top_n: int
    fill_detailed_governs: str


@dataclass(frozen=True)
class RuleComplianceStat:
    """One compliance bucket (a lane, or a verdict rollup over lanes).

    ``key`` is the lane id (``RULE_LANES``) or the verdict id
    (``RULE_VERDICTS``); both use the identical math so 合规单 vs 违规单 is
    directly comparable with the per-lane rows above it.

    ``gross`` = Σ(``realized_pnl_net`` + ``total_fee``), ``risk`` =
    Σ``ABS(opening_cash_flow)`` — the exact definitions the rule set quotes.
    Every ratio is null-with-reason rather than 0 when its denominator cannot
    support it.
    """

    key: str
    n: int
    risk: Optional[float]
    net: Optional[float]
    gross: Optional[float]
    gross_pct: Optional[float]
    toll_pct: Optional[float]
    win_rate: Optional[float]
    gross_pct_excluding_top_n: Optional[float]
    excluding_top_n_count: Optional[int]
    excluding_top_n_reason: Optional[str]
    ratio_reason: Optional[str]


@dataclass(frozen=True)
class RuleComplianceLaneStat(RuleComplianceStat):
    """A lane bucket, tagged with its verdict and the rule id it comes from."""

    verdict: str
    rule_id: str


@dataclass(frozen=True)
class RuleComplianceSlice:
    """One time slice of the clean basis (all-history or since-adoption).

    ``state`` is ``ready`` when the slice has members, ``no_episodes`` for an
    empty all-history slice and ``no_episodes_since_adoption`` for an empty
    forward slice — the forward slice is legitimately empty right after
    adoption, and it says so instead of rendering a zeroed table.
    """

    state: str
    state_reason: Optional[str]
    start_date: Optional[str]
    n: int
    lanes: tuple[RuleComplianceLaneStat, ...]
    verdicts: tuple[RuleComplianceStat, ...]


@dataclass(frozen=True)
class RuleComplianceDailyBudget:
    """V2-D 的两个额度读数，取自同一 build（因此带明确的 as-of 日）。

    ``as_of_trading_day`` 是 build 内最后一个有入场的 ET 自然日。消费端必须把它
    和「今天」对照：build 不含今日时读数是**过期**的，应显式标缺而不是显示 0。
    """

    as_of_trading_day: Optional[str]
    intraday_ticket_count: Optional[int]
    intraday_ticket_limit: int
    intraday_reason: Optional[str]
    overnight_open_count: Optional[int]
    overnight_concurrent_limit: int
    overnight_reason: Optional[str]
    overnight_unknown_dte_open_count: int


@dataclass(frozen=True)
class PersonalEdgeRuleCompliance:
    """车道遵守度：把本人规则 v2 变成可前向证伪的记账。"""

    rule_set_id: str
    adopted_at: str
    clean_basis_start: str
    clean_basis_reason: str
    population_n: int
    excluded_before_clean_basis_count: int
    excluded_aggregate_or_unknown_basis_count: int
    excluded_missing_premium_count: int
    exclude_top_n: int
    exclude_top_n_min_episode_count: int
    intraday_lane_dte: int
    intraday_lane_et_cutoff_hour: int
    overnight_lane_min_dte: int
    overnight_lane_max_dte: int
    overnight_lane_weak_entry_et_hours: tuple[int, ...]
    all_history: RuleComplianceSlice
    since_adoption: RuleComplianceSlice
    daily_budget: RuleComplianceDailyBudget
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class PersonalEdgeResult:
    """Derived read over one immutable default build; nothing persisted."""

    build_id: int
    build_key: str
    source_kind: str
    account_key: str
    computed_at: datetime
    first_opened_at: Optional[datetime]
    last_closed_at: Optional[datetime]
    closed_episode_count: int
    excluded_open_count: int
    excluded_missing_pnl_count: int
    underlying_min_episode_count: int
    underlyings: tuple[PersonalEdgeUnderlyingStat, ...]
    small_sample_underlying_count: int
    hold_time_buckets: tuple[PersonalEdgeHoldBucket, ...]
    hold_unknown_count: int
    dte_buckets: tuple[PersonalEdgeDteBucket, ...]
    dte_unknown: PersonalEdgeDteBucket
    monthly: tuple[PersonalEdgeMonthlyBucket, ...]
    discipline: PersonalEdgeDiscipline
    # additive（2026-08-04）：车道遵守度前向统计。
    rule_compliance: PersonalEdgeRuleCompliance
    month_basis: str
    limitations: tuple[str, ...]


@dataclass
class _Members:
    """Accumulator for one bucket's verified net-P&L members."""

    pnl: list[Decimal]
    fees: Decimal

    def __init__(self) -> None:
        self.pnl = []
        self.fees = Decimal("0")


@dataclass
class _DisciplineMembers:
    """Accumulator for one discipline bucket (a month or the trailing window)."""

    pnl: list[Decimal]
    premium: list[Decimal]
    premium_missing: int
    trading_days: set[str]
    zero_dte_count: int
    dte_known_count: int
    exact_fill_count: int
    fees: list[Decimal]
    fees_missing: int
    fill_detailed_count: int
    aggregate_only_count: int
    fill_unknown_count: int
    # 剔除最好 N 笔需要逐笔配对的（净盈亏, 费用, 风险金额），只收风险金额已知的回合。
    priced: list[tuple[Decimal, Optional[Decimal], Decimal]]

    def __init__(self) -> None:
        self.pnl = []
        self.premium = []
        self.premium_missing = 0
        self.trading_days = set()
        self.zero_dte_count = 0
        self.dte_known_count = 0
        self.exact_fill_count = 0
        self.fees = []
        self.fees_missing = 0
        self.fill_detailed_count = 0
        self.aggregate_only_count = 0
        self.fill_unknown_count = 0
        self.priced = []

    def add(self, episode: "_DisciplineEpisode") -> None:
        self.pnl.append(episode.pnl)
        if episode.premium is None:
            self.premium_missing += 1
        else:
            self.premium.append(episode.premium)
            self.priced.append((episode.pnl, episode.fee, episode.premium))
        if episode.fee is None:
            self.fees_missing += 1
        else:
            self.fees.append(episode.fee)
        self.trading_days.add(episode.trading_day)
        if episode.dte is not None:
            self.dte_known_count += 1
            if episode.dte == 0:
                self.zero_dte_count += 1
        if episode.has_exact_fill_times:
            self.exact_fill_count += 1
        if episode.fill_detailed is None:
            self.fill_unknown_count += 1
        elif episode.fill_detailed:
            self.fill_detailed_count += 1
        else:
            self.aggregate_only_count += 1


@dataclass(frozen=True)
class _DisciplineEpisode:
    """One closed, P&L-verified episode reduced to its discipline inputs."""

    trading_day: str
    month: str
    pnl: Decimal
    premium: Optional[Decimal]
    dte: Optional[int]
    has_exact_fill_times: bool
    fee: Optional[Decimal]
    # None＝口径来源不可判定（fail closed，绝不当作明细成交）。
    fill_detailed: Optional[bool]


def _amount(value: Decimal) -> float:
    return float(value.quantize(_AMOUNT_QUANTUM, rounding=ROUND_HALF_EVEN))


def _win_rate(pnl: list[Decimal]) -> Optional[float]:
    if not pnl:
        return None
    wins = sum(1 for value in pnl if value > 0)
    rate = (Decimal(wins) / Decimal(len(pnl))).quantize(
        _RATE_QUANTUM, rounding=ROUND_HALF_EVEN
    )
    return float(rate)


def _mean(values: list[Decimal]) -> Optional[float]:
    if not values:
        return None
    return _amount(sum(values, Decimal("0")) / Decimal(len(values)))


def _hold_bucket_label(hold_seconds: int) -> str:
    for label, lower, upper in HOLD_TIME_BUCKETS:
        if hold_seconds >= lower and (upper is None or hold_seconds < upper):
            return label
    return HOLD_TIME_BUCKETS[-1][0]


def _dte_bucket_label(dte: int) -> Optional[str]:
    if dte < 0:
        # Defensive: a negative DTE is bad data, not "expired counts as 0".
        return None
    for label, lower, upper in DTE_BUCKETS:
        if dte >= lower and (upper is None or dte <= upper):
            return label
    return None


def _median(values: list[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


@dataclass(frozen=True)
class _ExcludeTopNReadings:
    """剔除最好 N 笔后的每美元回报（净/毛）+ 剩余笔数 + 不成立原因。"""

    net_pct: Optional[float]
    gross_pct: Optional[float]
    count: Optional[int]
    reason: Optional[str]


def _exclude_top_n_readings(
    priced: list[tuple[Decimal, Optional[Decimal], Decimal]],
) -> _ExcludeTopNReadings:
    """剔除最好 N 笔后的每美元回报——``body_pnl`` 的每美元版本。

    ``priced`` 是逐笔配对的 ``(净盈亏, 费用或 None, 风险金额)``，只收风险金额
    已知的回合，因此分子分母天然取同一子集。按净盈亏排序去掉最好的
    ``DISCIPLINE_EXCLUDE_TOP_N`` 笔后重算。

    Fail-closed 三处：样本（按「风险金额已知」计）低于
    ``DISCIPLINE_BODY_MIN_EPISODE_COUNT`` 时整体不成立；剩余风险金额为 0 时不成立；
    剩余回合里只要有一笔缺 ``total_fee``，净口径仍成立而毛口径缺席——过路费绝不
    以 0 冒充。

    规模与频率（``_discipline_stats``）与车道遵守度（``_compliance_stat``）共用这
    一份实现与同一个 n≥15 门槛，两处读数因此严格同口径。
    """
    priced_n = len(priced)
    if priced_n < DISCIPLINE_BODY_MIN_EPISODE_COUNT:
        return _ExcludeTopNReadings(
            net_pct=None,
            gross_pct=None,
            count=None,
            reason=(
                f"风险金额已知样本 {priced_n} 笔 < "
                f"{DISCIPLINE_BODY_MIN_EPISODE_COUNT} 笔，"
                f"剔除最好 {DISCIPLINE_EXCLUDE_TOP_N} 笔后不成立"
            ),
        )
    remaining = sorted(priced, key=lambda item: item[0])[
        : priced_n - DISCIPLINE_EXCLUDE_TOP_N
    ]
    remaining_premium = sum((item[2] for item in remaining), Decimal("0"))
    if remaining_premium == 0:
        return _ExcludeTopNReadings(
            net_pct=None, gross_pct=None, count=None, reason=REASON_ZERO_PREMIUM
        )
    remaining_pnl = sum((item[0] for item in remaining), Decimal("0"))
    net_pct = float(
        (remaining_pnl / remaining_premium).quantize(
            _RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    )
    if any(item[1] is None for item in remaining):
        # 净口径仍成立，毛口径缺费用即缺席。
        return _ExcludeTopNReadings(
            net_pct=net_pct,
            gross_pct=None,
            count=len(remaining),
            reason=REASON_FEE_MISSING,
        )
    remaining_fee = sum(
        (item[1] for item in remaining if item[1] is not None), Decimal("0")
    )
    gross_pct = float(
        ((remaining_pnl + remaining_fee) / remaining_premium).quantize(
            _RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    )
    return _ExcludeTopNReadings(
        net_pct=net_pct,
        gross_pct=gross_pct,
        count=len(remaining),
        reason=None,
    )


def _discipline_stats(state: _DisciplineMembers) -> PersonalEdgeDisciplineStats:
    """Reduce one bucket to discipline readings, failing closed everywhere."""
    n = len(state.pnl)
    trading_day_count = len(state.trading_days)

    trades_per_day: Optional[float] = None
    trades_per_day_reason: Optional[str] = None
    if trading_day_count == 0:
        trades_per_day_reason = REASON_NO_TRADING_DAY
    else:
        trades_per_day = float(
            (Decimal(n) / Decimal(trading_day_count)).quantize(
                _AMOUNT_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )

    premium_total = sum(state.premium, Decimal("0"))
    median_premium: Optional[float] = None
    total_premium: Optional[float] = None
    premium_reason: Optional[str] = None
    if not state.premium:
        premium_reason = REASON_NO_PREMIUM
    else:
        median_value = _median(state.premium)
        median_premium = _amount(median_value) if median_value is not None else None
        total_premium = _amount(premium_total)

    net_pnl = sum(state.pnl, Decimal("0"))
    pnl_per_dollar: Optional[float] = None
    pnl_per_dollar_reason: Optional[str] = None
    if not state.premium:
        pnl_per_dollar_reason = REASON_NO_PREMIUM
    elif premium_total == 0:
        pnl_per_dollar_reason = REASON_ZERO_PREMIUM
    else:
        pnl_per_dollar = float(
            (net_pnl / premium_total).quantize(
                _RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )

    median_pnl_value = _median(state.pnl)
    median_episode_pnl = (
        _amount(median_pnl_value) if median_pnl_value is not None else None
    )

    body_pnl: Optional[float] = None
    body_episode_count: Optional[int] = None
    body_pnl_reason: Optional[str] = None
    if n < DISCIPLINE_BODY_MIN_EPISODE_COUNT:
        body_pnl_reason = (
            f"样本 {n} 笔 < {DISCIPLINE_BODY_MIN_EPISODE_COUNT} 笔，"
            f"去掉最好/最差各 {DISCIPLINE_BODY_TRIM_COUNT} 笔后不成立"
        )
    else:
        ordered = sorted(state.pnl)
        body = ordered[DISCIPLINE_BODY_TRIM_COUNT:-DISCIPLINE_BODY_TRIM_COUNT]
        body_episode_count = len(body)
        body_pnl = _amount(sum(body, Decimal("0")))

    zero_dte_share: Optional[float] = None
    zero_dte_reason: Optional[str] = None
    if state.dte_known_count == 0:
        zero_dte_reason = REASON_NO_DTE
    else:
        zero_dte_share = float(
            (
                Decimal(state.zero_dte_count) / Decimal(state.dte_known_count)
            ).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_EVEN)
        )

    exact_fill_share: Optional[float] = None
    if n > 0:
        exact_fill_share = float(
            (Decimal(state.exact_fill_count) / Decimal(n)).quantize(
                _RATE_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )

    # --- 口径来源：分母取桶内**全部**回合，不可判定的回合拉低 share（fail closed）---
    fill_detailed_share: Optional[float] = None
    if n > 0:
        fill_detailed_share = float(
            (Decimal(state.fill_detailed_count) / Decimal(n)).quantize(
                _RATE_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )
    basis_break = n > 0 and state.fill_detailed_count < n
    basis_break_reason: Optional[str] = None
    if basis_break:
        parts: list[str] = []
        if state.aggregate_only_count:
            parts.append(
                f"{state.aggregate_only_count}/{n} 笔由汇总 ORDER 行构建"
                "（fill_allocations=0，无明细成交）"
            )
        if state.fill_unknown_count:
            parts.append(
                f"{state.fill_unknown_count}/{n} 笔口径来源不可判定"
                "（evidence_summary_json 缺 fill_allocations 或无法解析）"
            )
        basis_break_reason = (
            "；".join(parts)
            + "：风险金额分母被低估（管线自身标记 audit_only_not_execution_cash_flow），"
            "本区间每美元口径不可与全明细区间比较，须断开显示"
        )

    # --- 费用门槛与毛口径：缺任一 total_fee 即整体 fail-closed，不以 0 冒充过路费 ---
    fees_total: Optional[float] = None
    fee_pct: Optional[float] = None
    fee_pct_reason: Optional[str] = None
    gross_pct: Optional[float] = None
    gross_pct_reason: Optional[str] = None
    fee_sum = sum(state.fees, Decimal("0"))
    if state.fees_missing == 0 and state.fees:
        fees_total = _amount(fee_sum)
    if n == 0:
        fee_pct_reason = gross_pct_reason = REASON_NO_SAMPLE
    elif state.fees_missing > 0:
        fee_pct_reason = gross_pct_reason = (
            f"{state.fees_missing}/{n} 笔缺 total_fee：{REASON_FEE_MISSING}"
        )
    elif not state.premium:
        fee_pct_reason = gross_pct_reason = REASON_NO_PREMIUM
    elif premium_total == 0:
        fee_pct_reason = gross_pct_reason = REASON_ZERO_PREMIUM
    else:
        # 与 pnl_per_dollar_risked 同一约定（分子取桶内全部回合，分母取已知风险金额
        # 合计），因此恒等式 gross = net + fee 在同一行上严格成立。
        fee_pct = float(
            (fee_sum / premium_total).quantize(
                _RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )
        gross_pct = float(
            ((net_pnl + fee_sum) / premium_total).quantize(
                _RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )

    # --- 剔除最好 N 笔后的每美元回报（body_pnl 的每美元版本）------------------
    # 与车道遵守度共用同一份实现与同一个 n≥15 门槛（_exclude_top_n_readings）。
    excluded = _exclude_top_n_readings(state.priced)
    excl_net = excluded.net_pct
    excl_gross = excluded.gross_pct
    excl_count = excluded.count
    excl_reason = excluded.reason

    return PersonalEdgeDisciplineStats(
        n=n,
        trading_day_count=trading_day_count,
        trades_per_day=trades_per_day,
        trades_per_day_reason=trades_per_day_reason,
        premium_known_count=len(state.premium),
        premium_missing_count=state.premium_missing,
        median_premium_at_risk=median_premium,
        total_premium_at_risk=total_premium,
        premium_reason=premium_reason,
        net_pnl=_amount(net_pnl),
        pnl_per_dollar_risked=pnl_per_dollar,
        pnl_per_dollar_risked_reason=pnl_per_dollar_reason,
        median_episode_pnl=median_episode_pnl,
        body_pnl=body_pnl,
        body_episode_count=body_episode_count,
        body_pnl_reason=body_pnl_reason,
        dte_known_count=state.dte_known_count,
        zero_dte_share=zero_dte_share,
        zero_dte_reason=zero_dte_reason,
        exact_fill_share=exact_fill_share,
        has_reconstructed_fills=(
            exact_fill_share is not None and exact_fill_share < 1.0
        ),
        fill_detailed_count=state.fill_detailed_count,
        aggregate_only_count=state.aggregate_only_count,
        fill_provenance_unknown_count=state.fill_unknown_count,
        fill_detailed_share=fill_detailed_share,
        basis_break=basis_break,
        basis_break_reason=basis_break_reason,
        fees_total=fees_total,
        fees_missing_count=state.fees_missing,
        fee_pct_of_premium_at_risk=fee_pct,
        fee_pct_of_premium_at_risk_reason=fee_pct_reason,
        gross_pct_of_premium_at_risk=gross_pct,
        gross_pct_of_premium_at_risk_reason=gross_pct_reason,
        pnl_per_dollar_excluding_top_n=excl_net,
        gross_pct_excluding_top_n=excl_gross,
        excluding_top_n_count=excl_count,
        excluding_top_n_reason=excl_reason,
    )


def _build_discipline(
    episodes: list[_DisciplineEpisode],
    *,
    window_trading_days: int = DISCIPLINE_WINDOW_TRADING_DAYS,
) -> PersonalEdgeDiscipline:
    """月度 + 当前窗口的规模与频率读数（窗口＝build 内实际存在的最后 N 个交易日）."""
    by_month: dict[str, _DisciplineMembers] = {}
    for episode in episodes:
        by_month.setdefault(episode.month, _DisciplineMembers()).add(episode)

    all_days = sorted({episode.trading_day for episode in episodes})
    window_days = set(all_days[-window_trading_days:]) if all_days else set()
    window_state = _DisciplineMembers()
    for episode in episodes:
        if episode.trading_day in window_days:
            window_state.add(episode)

    ordered_window_days = sorted(window_days)
    return PersonalEdgeDiscipline(
        monthly=tuple(
            PersonalEdgeDisciplineMonth(
                month=month,
                stats=_discipline_stats(state),
            )
            for month, state in sorted(by_month.items())
        ),
        current_window=PersonalEdgeDisciplineWindow(
            requested_trading_days=window_trading_days,
            start_date=ordered_window_days[0] if ordered_window_days else None,
            end_date=ordered_window_days[-1] if ordered_window_days else None,
            stats=_discipline_stats(window_state),
        ),
        body_trim_count=DISCIPLINE_BODY_TRIM_COUNT,
        body_min_episode_count=DISCIPLINE_BODY_MIN_EPISODE_COUNT,
        exclude_top_n=DISCIPLINE_EXCLUDE_TOP_N,
        fill_detailed_governs=FILL_DETAILED_GOVERNS,
    )


# ---------------------------------------------------------------------------
# 车道遵守度（rule_compliance）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ComplianceEpisode:
    """One clean-basis closed episode reduced to its lane-classification inputs."""

    lane: str
    opened_trading_day: str
    pnl: Decimal
    fee: Optional[Decimal]
    premium: Decimal


def classify_rule_lane(
    *,
    dte: Optional[int],
    opened_et_hour: Optional[int],
    opened_trading_day: Optional[str],
    closed_trading_day: Optional[str],
) -> str:
    """Classify one episode into a 规则 v2 车道, purely mechanically.

    The decision order is fixed and total (every input maps to exactly one of
    ``RULE_LANES``):

    1. ``dte`` unknown or negative → ``unknown`` （负 DTE 是坏数据，不当作 0）;
    2. ``dte == 0`` → needs the ET entry hour: unknown → ``unknown``;
       hour ``>= INTRADAY_LANE_ET_CUTOFF_HOUR`` (12) → ``late_0dte`` (V2-C③);
       otherwise → ``intraday_0dte`` (V2-A);
    3. ``1 <= dte <= 3`` → ``dte_1_3`` (V2-C①) — excluded at any hour, held any
       length of time: 当日平 −2.94%，隔夜 +6.98% 但剔除最好 5 笔 −4.03%;
    4. ``dte >= 4`` → needs both ET dates: either unknown → ``unknown``;
       closed on the same (or an earlier) date → ``bought_time_unused``
       (V2-C②, 买了时间却不用); closed on a later date → ``overnight_4_7``
       (V2-B) when ``4 <= dte <= 7``, else ``other`` (≥8DTE 隔夜，两条车道都没有
       覆盖它).

    Boundaries this pins down: DTE 0 → 日内, 1 and 3 → 违规, 4 and 7 → 过夜,
    8 → 未覆盖; ET hour 11 → 日内合规 / 12 → 违规; same-date close vs later-date
    close is by **ET calendar date**, not by hold duration — a 20-hour hold that
    opens and closes on the same ET date is still a same-day close.

    Note it reads only stored facts.  It cannot know the user's intent at
    entry, so ``bought_time_unused`` is an outcome label, not a mind-read.
    """
    if dte is None or dte < 0:
        return "unknown"
    if dte == INTRADAY_LANE_DTE:
        if opened_et_hour is None:
            return "unknown"
        if opened_et_hour >= INTRADAY_LANE_ET_CUTOFF_HOUR:
            return "late_0dte"
        return "intraday_0dte"
    if 1 <= dte <= 3:
        return "dte_1_3"
    if opened_trading_day is None or closed_trading_day is None:
        return "unknown"
    if closed_trading_day <= opened_trading_day:
        return "bought_time_unused"
    if OVERNIGHT_LANE_MIN_DTE <= dte <= OVERNIGHT_LANE_MAX_DTE:
        return "overnight_4_7"
    return "other"


def _compliance_stat(key: str, members: list[_ComplianceEpisode]) -> RuleComplianceStat:
    """Reduce one bucket to its per-dollar readings, failing closed everywhere."""
    n = len(members)
    if n == 0:
        return RuleComplianceStat(
            key=key,
            n=0,
            risk=None,
            net=None,
            gross=None,
            gross_pct=None,
            toll_pct=None,
            win_rate=None,
            gross_pct_excluding_top_n=None,
            excluding_top_n_count=None,
            excluding_top_n_reason=REASON_COMPLIANCE_NO_SAMPLE,
            ratio_reason=REASON_COMPLIANCE_NO_SAMPLE,
        )

    pnl_total = sum((item.pnl for item in members), Decimal("0"))
    risk_total = sum((item.premium for item in members), Decimal("0"))
    fees_missing = sum(1 for item in members if item.fee is None)
    fee_total = sum(
        (item.fee for item in members if item.fee is not None), Decimal("0")
    )

    gross_pct: Optional[float] = None
    toll_pct: Optional[float] = None
    gross_amount: Optional[float] = None
    ratio_reason: Optional[str] = None
    if fees_missing > 0:
        # 毛口径 = 净 + 费用：缺一笔费用整条读数即缺席，绝不以 0 冒充过路费。
        ratio_reason = f"{fees_missing}/{n} 笔缺 total_fee：{REASON_FEE_MISSING}"
    elif risk_total == 0:
        ratio_reason = REASON_COMPLIANCE_ZERO_RISK
    else:
        gross_amount = _amount(pnl_total + fee_total)
        gross_pct = float(
            ((pnl_total + fee_total) / risk_total).quantize(
                _RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )
        toll_pct = float(
            (fee_total / risk_total).quantize(
                _RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
            )
        )

    # 剔除最好 N 笔：复用规模与频率同一份实现与同一个 n≥15 门槛。
    excluded = _exclude_top_n_readings(
        [(item.pnl, item.fee, item.premium) for item in members]
    )
    return RuleComplianceStat(
        key=key,
        n=n,
        risk=_amount(risk_total),
        net=_amount(pnl_total),
        gross=gross_amount,
        gross_pct=gross_pct,
        toll_pct=toll_pct,
        win_rate=_win_rate([item.pnl for item in members]),
        gross_pct_excluding_top_n=excluded.gross_pct,
        excluding_top_n_count=excluded.count,
        excluding_top_n_reason=excluded.reason,
        ratio_reason=ratio_reason,
    )


def _compliance_lane_stat(
    lane: str, members: list[_ComplianceEpisode]
) -> RuleComplianceLaneStat:
    """Same math as :func:`_compliance_stat`, tagged with verdict + rule id."""
    base = _compliance_stat(lane, members)
    return RuleComplianceLaneStat(
        key=base.key,
        n=base.n,
        risk=base.risk,
        net=base.net,
        gross=base.gross,
        gross_pct=base.gross_pct,
        toll_pct=base.toll_pct,
        win_rate=base.win_rate,
        gross_pct_excluding_top_n=base.gross_pct_excluding_top_n,
        excluding_top_n_count=base.excluding_top_n_count,
        excluding_top_n_reason=base.excluding_top_n_reason,
        ratio_reason=base.ratio_reason,
        verdict=RULE_COMPLIANCE_LANE_VERDICTS[lane],
        rule_id=RULE_COMPLIANCE_LANE_RULE_IDS[lane],
    )


def _compliance_slice(
    members: list[_ComplianceEpisode],
    *,
    start_date: Optional[str],
    empty_state: str,
    empty_reason: str,
) -> RuleComplianceSlice:
    """Build one slice: per-lane buckets plus verdict rollups over the same rows."""
    by_lane: dict[str, list[_ComplianceEpisode]] = {lane: [] for lane in RULE_LANES}
    by_verdict: dict[str, list[_ComplianceEpisode]] = {
        verdict: [] for verdict in RULE_VERDICTS
    }
    for episode in members:
        by_lane[episode.lane].append(episode)
        by_verdict[RULE_COMPLIANCE_LANE_VERDICTS[episode.lane]].append(episode)

    return RuleComplianceSlice(
        state="ready" if members else empty_state,
        state_reason=None if members else empty_reason,
        start_date=start_date,
        n=len(members),
        lanes=tuple(
            _compliance_lane_stat(lane, by_lane[lane]) for lane in RULE_LANES
        ),
        verdicts=tuple(
            _compliance_stat(verdict, by_verdict[verdict])
            for verdict in RULE_VERDICTS
        ),
    )


def _build_rule_compliance(
    members: list[_ComplianceEpisode],
    *,
    adopted_at: str,
    excluded_before_clean_basis_count: int,
    excluded_aggregate_or_unknown_basis_count: int,
    excluded_missing_premium_count: int,
    daily_budget: RuleComplianceDailyBudget,
) -> PersonalEdgeRuleCompliance:
    """All-history + since-adoption slices over the same clean-basis rows."""
    forward = [
        episode for episode in members if episode.opened_trading_day >= adopted_at
    ]
    return PersonalEdgeRuleCompliance(
        rule_set_id=RULE_SET_V2_ID,
        adopted_at=adopted_at,
        clean_basis_start=RULE_COMPLIANCE_CLEAN_BASIS_START,
        clean_basis_reason=RULE_COMPLIANCE_CLEAN_BASIS_REASON,
        population_n=len(members),
        excluded_before_clean_basis_count=excluded_before_clean_basis_count,
        excluded_aggregate_or_unknown_basis_count=(
            excluded_aggregate_or_unknown_basis_count
        ),
        excluded_missing_premium_count=excluded_missing_premium_count,
        exclude_top_n=DISCIPLINE_EXCLUDE_TOP_N,
        exclude_top_n_min_episode_count=DISCIPLINE_BODY_MIN_EPISODE_COUNT,
        # 判定与规则编号随每一条车道行下发（见 RuleComplianceLaneStat）——不另发
        # 以车道 id 为键的字典：Web 层的深层 camelCase 会改写字典键，规则 id 必须
        # 只以「值」的形式过网。
        intraday_lane_dte=INTRADAY_LANE_DTE,
        intraday_lane_et_cutoff_hour=INTRADAY_LANE_ET_CUTOFF_HOUR,
        overnight_lane_min_dte=OVERNIGHT_LANE_MIN_DTE,
        overnight_lane_max_dte=OVERNIGHT_LANE_MAX_DTE,
        overnight_lane_weak_entry_et_hours=OVERNIGHT_LANE_WEAK_ENTRY_ET_HOURS,
        all_history=_compliance_slice(
            members,
            start_date=RULE_COMPLIANCE_CLEAN_BASIS_START,
            empty_state="no_episodes",
            empty_reason=(
                "干净口径样本为空："
                f"该 build 内没有 ET 入场日 ≥ {RULE_COMPLIANCE_CLEAN_BASIS_START} "
                "且由明细成交构建、风险金额已知的已平仓回合"
            ),
        ),
        since_adoption=_compliance_slice(
            forward,
            start_date=adopted_at,
            empty_state="no_episodes_since_adoption",
            empty_reason=(
                f"规则采纳日（{adopted_at}，ET）之后尚无已平仓回合进入该 build："
                "前向样本为空是事实，不以 0 冒充读数"
            ),
        ),
        daily_budget=daily_budget,
        limitations=RULE_COMPLIANCE_LIMITATIONS,
    )


def _et_hour_utc_minus_4(moment: datetime) -> int:
    """ET≈UTC−4 近似的入场小时（与月度/交易日口径同一换算）."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment.astimezone(timezone.utc) - timedelta(hours=4)).hour


def _trading_day_utc_minus_4(opened_at: datetime) -> str:
    """ET≈UTC−4 近似的入场自然日（与月度口径同一换算，边界注记见 limitations）."""
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    shifted = opened_at.astimezone(timezone.utc) - timedelta(hours=4)
    return shifted.strftime("%Y-%m-%d")


def _month_key_utc_minus_4(opened_at: datetime) -> str:
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    shifted = opened_at.astimezone(timezone.utc) - timedelta(hours=4)
    return shifted.strftime("%Y-%m")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_personal_edge_stats(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    *,
    min_underlying_episode_count: int = DEFAULT_MIN_UNDERLYING_EPISODE_COUNT,
    rule_compliance_since: str = RULE_SET_V2_ADOPTED_AT,
) -> Optional[PersonalEdgeResult]:
    """Aggregate the default build's closed episodes into personal stats.

    Returns ``None`` when the account has no episode build.  The whole read
    happens in one session and issues only SELECT statements.

    ``rule_compliance_since`` (ET ``YYYY-MM-DD``) moves the forward slice of the
    车道遵守度 block; it defaults to ``RULE_SET_V2_ADOPTED_AT`` and never affects
    the all-history slice or any pre-existing field.
    """
    if min_underlying_episode_count < 1:
        raise EpisodeRepositoryError(
            "min_underlying_episode_count must be positive"
        )
    if not _ISO_DATE.fullmatch(rule_compliance_since):
        raise EpisodeRepositoryError(
            "rule_compliance_since must be an ET calendar date (YYYY-MM-DD)"
        )
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, account_key)
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
                PositionEpisode.hold_seconds,
                PositionEpisode.realized_pnl_net,
                PositionEpisode.total_fee,
                PositionEpisode.dte_at_entry,
                PositionEpisode.opening_cash_flow,
                PositionEpisode.has_exact_fill_times,
                PositionEpisode.evidence_summary_json,
            ).where(
                PositionEpisode.episode_build_id == build_id,
                PositionEpisode.account_key == resolved_account_key,
            )
        ).all()

    excluded_open_count = 0
    excluded_missing_pnl_count = 0
    first_opened_at: Optional[datetime] = None
    last_closed_at: Optional[datetime] = None

    by_underlying: dict[str, _Members] = {}
    by_hold: dict[str, _Members] = {
        label: _Members() for label, _lower, _upper in HOLD_TIME_BUCKETS
    }
    hold_unknown_count = 0
    by_dte: dict[str, _Members] = {
        label: _Members() for label, _lower, _upper in DTE_BUCKETS
    }
    dte_unknown = _Members()
    by_month: dict[str, _Members] = {}
    discipline_episodes: list[_DisciplineEpisode] = []
    closed_episode_count = 0

    # --- 车道遵守度累加器（干净口径子集 + V2-D 额度读数）--------------------
    compliance_episodes: list[_ComplianceEpisode] = []
    compliance_excluded_before_clean_basis = 0
    compliance_excluded_basis = 0
    compliance_excluded_missing_premium = 0
    budget_last_trading_day: Optional[str] = None
    # 逐日 0DTE 开仓笔数（含 ET 12:00 后开的违规单——它们同样占用当日额度）。
    budget_intraday_by_day: dict[str, int] = {}
    budget_overnight_open = 0
    budget_overnight_unknown_dte_open = 0

    for row in rows:
        if str(row.lifecycle_status) != "closed":
            excluded_open_count += 1
            # V2-D③ 的「当前过夜持仓」＝该 build 内仍未平仓的 4-7DTE 回合。
            if row.dte_at_entry is None:
                budget_overnight_unknown_dte_open += 1
            elif (
                OVERNIGHT_LANE_MIN_DTE
                <= int(row.dte_at_entry)
                <= OVERNIGHT_LANE_MAX_DTE
            ):
                budget_overnight_open += 1
            if row.opened_at is not None:
                open_day = _trading_day_utc_minus_4(_utc(row.opened_at))
                if (
                    budget_last_trading_day is None
                    or open_day > budget_last_trading_day
                ):
                    budget_last_trading_day = open_day
                if (
                    row.dte_at_entry is not None
                    and int(row.dte_at_entry) == INTRADAY_LANE_DTE
                ):
                    budget_intraday_by_day[open_day] = (
                        budget_intraday_by_day.get(open_day, 0) + 1
                    )
            continue
        if row.realized_pnl_net is None:
            excluded_missing_pnl_count += 1
            continue
        closed_episode_count += 1
        pnl = Decimal(row.realized_pnl_net)
        fee = Decimal(row.total_fee) if row.total_fee is not None else Decimal("0")
        opened_at = _utc(row.opened_at)
        if first_opened_at is None or opened_at < first_opened_at:
            first_opened_at = opened_at
        if row.closed_at is not None:
            closed_at = _utc(row.closed_at)
            if last_closed_at is None or closed_at > last_closed_at:
                last_closed_at = closed_at

        underlying = str(row.underlying).strip().upper()
        state = by_underlying.setdefault(underlying, _Members())
        state.pnl.append(pnl)
        state.fees += fee

        if row.hold_seconds is None:
            hold_unknown_count += 1
        else:
            hold_state = by_hold[_hold_bucket_label(int(row.hold_seconds))]
            hold_state.pnl.append(pnl)
            hold_state.fees += fee

        if row.dte_at_entry is None:
            dte_unknown.pnl.append(pnl)
            dte_unknown.fees += fee
        else:
            dte_label = _dte_bucket_label(int(row.dte_at_entry))
            if dte_label is None:
                dte_unknown.pnl.append(pnl)
                dte_unknown.fees += fee
            else:
                dte_state = by_dte[dte_label]
                dte_state.pnl.append(pnl)
                dte_state.fees += fee

        month_key = _month_key_utc_minus_4(opened_at)
        month_state = by_month.setdefault(month_key, _Members())
        month_state.pnl.append(pnl)
        month_state.fees += fee

        # 规模与频率：开仓现金流缺失只计缺席，绝不以 0 冒充风险金额。
        premium = (
            abs(Decimal(row.opening_cash_flow))
            if row.opening_cash_flow is not None
            else None
        )
        discipline_episodes.append(
            _DisciplineEpisode(
                trading_day=_trading_day_utc_minus_4(opened_at),
                month=month_key,
                pnl=pnl,
                premium=premium,
                dte=(
                    int(row.dte_at_entry)
                    if row.dte_at_entry is not None
                    else None
                ),
                has_exact_fill_times=bool(row.has_exact_fill_times),
                # 费用缺失只计缺席：过路费不能以 0 冒充（费用桶另有 0 兜底口径）。
                fee=(
                    Decimal(row.total_fee) if row.total_fee is not None else None
                ),
                fill_detailed=fill_detailed_from_evidence_summary(
                    row.evidence_summary_json
                ),
            )
        )

        # --- 车道遵守度：只收「干净口径」子集，其余逐类计缺席 ----------------
        opened_trading_day = _trading_day_utc_minus_4(opened_at)
        dte_value = (
            int(row.dte_at_entry) if row.dte_at_entry is not None else None
        )
        if dte_value == INTRADAY_LANE_DTE:
            budget_intraday_by_day[opened_trading_day] = (
                budget_intraday_by_day.get(opened_trading_day, 0) + 1
            )
        if (
            budget_last_trading_day is None
            or opened_trading_day > budget_last_trading_day
        ):
            budget_last_trading_day = opened_trading_day

        if opened_trading_day < RULE_COMPLIANCE_CLEAN_BASIS_START:
            compliance_excluded_before_clean_basis += 1
        elif fill_detailed_from_evidence_summary(row.evidence_summary_json) is not True:
            # 汇总 ORDER 口径与「不可判定」同样出局：分母不可比即不进车道统计。
            compliance_excluded_basis += 1
        elif premium is None:
            compliance_excluded_missing_premium += 1
        else:
            compliance_episodes.append(
                _ComplianceEpisode(
                    lane=classify_rule_lane(
                        dte=dte_value,
                        opened_et_hour=_et_hour_utc_minus_4(opened_at),
                        opened_trading_day=opened_trading_day,
                        closed_trading_day=(
                            _trading_day_utc_minus_4(_utc(row.closed_at))
                            if row.closed_at is not None
                            else None
                        ),
                    ),
                    opened_trading_day=opened_trading_day,
                    pnl=pnl,
                    fee=(
                        Decimal(row.total_fee)
                        if row.total_fee is not None
                        else None
                    ),
                    premium=premium,
                )
            )

    qualifying = {
        underlying: state
        for underlying, state in by_underlying.items()
        if len(state.pnl) >= min_underlying_episode_count
    }
    underlyings = tuple(
        PersonalEdgeUnderlyingStat(
            underlying=underlying,
            n=len(state.pnl),
            net=_amount(sum(state.pnl, Decimal("0"))),
            # Threshold-gated buckets always have members, so the rate exists.
            win_rate=_win_rate(state.pnl) or 0.0,
            fees=_amount(state.fees),
        )
        for underlying, state in sorted(
            qualifying.items(),
            key=lambda entry: (-sum(entry[1].pnl, Decimal("0")), entry[0]),
        )
    )

    hold_buckets = tuple(
        PersonalEdgeHoldBucket(
            bucket=label,
            n=len(state.pnl),
            net=_amount(sum(state.pnl, Decimal("0"))),
            win_rate=_win_rate(state.pnl),
            avg_win=_mean([value for value in state.pnl if value > 0]),
            avg_loss=_mean([value for value in state.pnl if value < 0]),
        )
        for label, state in (
            (label, by_hold[label]) for label, _lower, _upper in HOLD_TIME_BUCKETS
        )
    )

    def _dte_bucket(label: str, state: _Members) -> PersonalEdgeDteBucket:
        return PersonalEdgeDteBucket(
            bucket=label,
            n=len(state.pnl),
            net=_amount(sum(state.pnl, Decimal("0"))),
            win_rate=_win_rate(state.pnl),
        )

    dte_buckets = tuple(
        _dte_bucket(label, by_dte[label])
        for label, _lower, _upper in DTE_BUCKETS
    )

    monthly = tuple(
        PersonalEdgeMonthlyBucket(
            month=month,
            n=len(state.pnl),
            net=_amount(sum(state.pnl, Decimal("0"))),
            fees=_amount(state.fees),
            win_rate=_win_rate(state.pnl),
        )
        for month, state in sorted(by_month.items())
    )

    return PersonalEdgeResult(
        build_id=build_id,
        build_key=build_key,
        source_kind=source_kind,
        account_key=resolved_account_key,
        computed_at=datetime.now(timezone.utc),
        first_opened_at=first_opened_at,
        last_closed_at=last_closed_at,
        closed_episode_count=closed_episode_count,
        excluded_open_count=excluded_open_count,
        excluded_missing_pnl_count=excluded_missing_pnl_count,
        underlying_min_episode_count=min_underlying_episode_count,
        underlyings=underlyings,
        small_sample_underlying_count=len(by_underlying) - len(qualifying),
        hold_time_buckets=hold_buckets,
        hold_unknown_count=hold_unknown_count,
        dte_buckets=dte_buckets,
        dte_unknown=_dte_bucket("unknown", dte_unknown),
        monthly=monthly,
        discipline=_build_discipline(discipline_episodes),
        rule_compliance=_build_rule_compliance(
            compliance_episodes,
            adopted_at=rule_compliance_since,
            excluded_before_clean_basis_count=(
                compliance_excluded_before_clean_basis
            ),
            excluded_aggregate_or_unknown_basis_count=compliance_excluded_basis,
            excluded_missing_premium_count=compliance_excluded_missing_premium,
            daily_budget=RuleComplianceDailyBudget(
                as_of_trading_day=budget_last_trading_day,
                intraday_ticket_count=(
                    budget_intraday_by_day.get(budget_last_trading_day, 0)
                    if budget_last_trading_day is not None
                    else None
                ),
                intraday_ticket_limit=INTRADAY_LANE_DAILY_TICKET_LIMIT,
                intraday_reason=(
                    None
                    if budget_last_trading_day is not None
                    else "该 build 内没有任何带入场时间的回合，无法定位最后一个交易日"
                ),
                overnight_open_count=(
                    budget_overnight_open
                    if budget_last_trading_day is not None
                    else None
                ),
                overnight_concurrent_limit=OVERNIGHT_LANE_CONCURRENT_LIMIT,
                overnight_reason=(
                    None
                    if budget_last_trading_day is not None
                    else "该 build 内没有任何带入场时间的回合，无法定位最后一个交易日"
                ),
                overnight_unknown_dte_open_count=(
                    budget_overnight_unknown_dte_open
                ),
            ),
        ),
        month_basis=MONTH_BASIS_UTC_MINUS_4,
        limitations=PERSONAL_EDGE_LIMITATIONS,
    )
