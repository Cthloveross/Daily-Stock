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

规模与频率纪律（``discipline`` block, 2026-08-04）
------------------------------------------------

Why this block exists — a full-sample forensic study of the user's own 1,554
option episodes (2026-03-04→07-31, episode build #3, 5m bars from their own
Moomoo feed) asked why April→July P&L decayed, and found the three usual
suspects are statistically flat:

* entry geometry — chase share 52.0% → 56.6%, χ² month × class p=0.105
  (no detectable decay);
* market follow-through — median forward-30m MFE 0.370 → 0.341, Kruskal
  p=0.337 across all five months (the months are indistinguishable);
* stop discipline — median loss −22.6% → −24.4% of premium (stable).

What did change is size, frequency and per-dollar edge:

* return per dollar risked collapsed ~24x (5.83% → 0.24%), a step down right
  after April;
* median premium at risk per trade 2.4x ($4,097 → $9,695); total premium
  cycled $1.81M → $5.89M;
* trades/day +44% (16.5 → 23.7); 0DTE share 26.7% → 46.7%; median contracts
  per trade 15 → 32;
* P&L concentrated into tail winners — stripping each month's 5 best and 5
  worst trades leaves Apr +$51,709 / May −$23,918 / Jun −$49,517 /
  Jul −$99,456, and median episode P&L fell −$385 → −$1,297.

Provenance honesty: April's headline rests largely on Apr 1–24, where fills
are RECONSTRUCTED (``has_exact_fill_times=0``); restricted to exact-fill
episodes the per-dollar figures are Apr 0.72% / May 0.95% / Jun 0.16% /
Jul 0.24%.  So ``exact_fill_share`` ships per row and every month below 1.0
must be labelled by consumers.

The metric that would have caught this in May — and that the workstation did
not show anywhere — is per-dollar edge next to size and frequency.  This block
recomputes it from the same default build, monthly and over the trailing
``DISCIPLINE_WINDOW_TRADING_DAYS`` trading days, so the user can see where
they are now against their own history.  It stays descriptive: it is a mirror,
not advice, and every ratio fails closed (null + reason) rather than showing a
number its denominator cannot support.
"""
from __future__ import annotations

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

# Fail-closed reasons for every derived discipline ratio (surfaced verbatim).
REASON_NO_TRADING_DAY = "窗口内无入场交易日"
REASON_NO_PREMIUM = "无可用开仓现金流（opening_cash_flow 缺失）"
REASON_ZERO_PREMIUM = "开仓风险金额合计为 0"
REASON_NO_DTE = "无已知进场 DTE"
REASON_NO_SAMPLE = "无样本"

# Mandatory honesty strings — consumers surface these verbatim.
PERSONAL_EDGE_LIMITATIONS = (
    "持仓时长与结果存在内生性（止损单天然短），描述统计不是因果结论，不构成建议",
    "月度口径按 ET≈UTC−4 近似换算 opened_at（UTC）；2026-03 月初为 EST（UTC−5），"
    "近午夜边界样本可能偏移 ±1 小时",
    "部分回合的成交明细为重建（has_exact_fill_times=0，集中在 2026-04 前半段）："
    "这些月份的时点、持仓时长与每美元回报只能作参考，不能与全精确成交的月份等量齐观；"
    "各行以 exact_fill_share 如实标注",
    "规模与频率的派生比率一律 fail-closed：分母为 0、无可用开仓现金流或样本不足"
    f"（本体盈亏需 ≥{DISCIPLINE_BODY_MIN_EPISODE_COUNT} 笔）时返回 null 并给出原因，"
    "绝不以 0 或截断样本冒充",
)

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

    def __init__(self) -> None:
        self.pnl = []
        self.premium = []
        self.premium_missing = 0
        self.trading_days = set()
        self.zero_dte_count = 0
        self.dte_known_count = 0
        self.exact_fill_count = 0

    def add(self, episode: "_DisciplineEpisode") -> None:
        self.pnl.append(episode.pnl)
        if episode.premium is None:
            self.premium_missing += 1
        else:
            self.premium.append(episode.premium)
        self.trading_days.add(episode.trading_day)
        if episode.dte is not None:
            self.dte_known_count += 1
            if episode.dte == 0:
                self.zero_dte_count += 1
        if episode.has_exact_fill_times:
            self.exact_fill_count += 1


@dataclass(frozen=True)
class _DisciplineEpisode:
    """One closed, P&L-verified episode reduced to its discipline inputs."""

    trading_day: str
    month: str
    pnl: Decimal
    premium: Optional[Decimal]
    dte: Optional[int]
    has_exact_fill_times: bool


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
    )


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
) -> Optional[PersonalEdgeResult]:
    """Aggregate the default build's closed episodes into personal stats.

    Returns ``None`` when the account has no episode build.  The whole read
    happens in one session and issues only SELECT statements.
    """
    if min_underlying_episode_count < 1:
        raise EpisodeRepositoryError(
            "min_underlying_episode_count must be positive"
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

    for row in rows:
        if str(row.lifecycle_status) != "closed":
            excluded_open_count += 1
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
        month_basis=MONTH_BASIS_UTC_MINUS_4,
        limitations=PERSONAL_EDGE_LIMITATIONS,
    )
