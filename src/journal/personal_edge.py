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
    "DTE_BUCKETS",
    "HOLD_TIME_BUCKETS",
    "MONTH_BASIS_UTC_MINUS_4",
    "PERSONAL_EDGE_LIMITATIONS",
    "PERSONAL_EDGE_SCHEMA_VERSION",
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

# Mandatory honesty strings — consumers surface these verbatim.
PERSONAL_EDGE_LIMITATIONS = (
    "持仓时长与结果存在内生性（止损单天然短），描述统计不是因果结论，不构成建议",
    "月度口径按 ET≈UTC−4 近似换算 opened_at（UTC）；2026-03 月初为 EST（UTC−5），"
    "近午夜边界样本可能偏移 ±1 小时",
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

        month_state = by_month.setdefault(
            _month_key_utc_minus_4(opened_at), _Members()
        )
        month_state.pnl.append(pnl)
        month_state.fees += fee

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
        month_basis=MONTH_BASIS_UTC_MINUS_4,
        limitations=PERSONAL_EDGE_LIMITATIONS,
    )
