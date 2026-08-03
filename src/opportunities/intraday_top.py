# -*- coding: utf-8 -*-
"""Pure, evidence-first intraday Top-N research builder (日内 Top 5).

This module never fetches data.  Callers inject the same G-2 session snapshot
fields (:mod:`src.opportunities.intraday` consumers), the daily-loader derived
context (ATR14 / prior median volume / prior close / prior 20d range / EMA)
and the already-serialised Moomoo option-event payloads.  The output is a
rolling intraday research queue, deliberately different from the weekly board:

- it re-ranks continuously during the session (nothing is frozen);
- it never writes snapshots, qualification rows or 5D/20D outcomes
  (``statistics_track = none_intraday_v1_unscored``);
- option events are provider classifications only — they are surfaced as
  activity evidence, never as direction proof or open/close inference;
- every threshold below is a documented v1 heuristic, not a validated edge.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from src.opportunities.engine import _ema_last
from src.opportunities.intraday import (
    SESSION_PHASE_HINT_BASIS,
    VOLUME_PACE_BASIS,
    VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
    market_session_phase,
    session_phase_label,
)
from src.opportunities.intraday_bursts import (
    BURST_BASIS,
    BURST_LIMITATIONS,
    BURST_SUPPORT_MIN,
    unavailable_burst_profile,
)
from src.opportunities.intraday_setups import (
    SETUP_GAP_ATR_MULTIPLE_MIN,
    SETUP_GAP_PERCENT_MIN,
    compute_setup_match_profile,
)

# v2: 波段爆发（15 分钟推力×量比）成为盘中主排序信号；聚合证据退居次序。
# v3: 新增四类「上下文信号」——时段上下文（用户历史纪律提示）、财报临近标记、
# 大盘对齐（候选爆发方向 vs SPY VWAP 位置）、速度分级（相邻窗口爆发分之差）。
# 全部是标注（labels），不参与排序、不隐藏行、不阻断任何操作：系统标注，
# 用户过滤。
# v4: styleMatch v1（形态相似度）——把当前时段几何形状与用户自己的三个
# Playbook setup（S1 低点抬高突破 / S2 跳空托举 / S3 高开遇阻）做纯形状对比，
# 输出 matched/partial/not_matched/unavailable 四态标注（src/opportunities/
# intraday_setups.py）。同样只是标注：不参与排序、不隐藏行、不是信号。
INTRADAY_TOP_SIGNAL_VERSION = "intraday_session_evidence_v5"
INTRADAY_TOP_SCHEMA_VERSION = "intraday-top/1.0"

# ---------------------------------------------------------------------------
# v3 财报临近（earnings proximity）。用户纪律：财报日及临近数日不交易（权利金
# 过贵）。3 天阈值是按该规则硬编码的 v1 启发式；5 天窗口给「临近但未进入
# 回避窗」留出可见提前量。日历不可得时显式 unavailable，绝不以「无财报」冒充。
# ---------------------------------------------------------------------------
EARNINGS_BLACKOUT_DAYS = 3
EARNINGS_WINDOW_DAYS = 5
EARNINGS_PROXIMITY_BASIS = "finnhub_earnings_calendar_forward_window"

# v3 大盘对齐：候选当前爆发方向 vs SPY 会话 VWAP 位置（累计额/量近似）。
# 用户纪律：setup 显式依赖大盘情绪（SPY 方向 / VWAP）。
MARKET_ALIGNMENT_BASIS = (
    "candidate_current_burst_direction_vs_spy_session_vwap_position"
)
MARKET_CONTEXT_TICKER = "SPY"
_NEW_YORK_TZ = ZoneInfo("America/New_York")

# 盘中（盘前/盘中/盘后，quote_session_scope=current_session）：按当前爆发分
# 优先排序；休市（closed）：退回 v1 证据计数排序，但仍附带最近一个交易时段
# 的波段列表（晚间复盘可见「今日走了几波」）。
RANKING_METHOD_BURST_FIRST = "burst_score_first_then_evidence_count"
RANKING_METHOD_EVIDENCE_COUNT = "rule_based_evidence_count"

# The intraday board is a rolling research surface: it must never be confused
# with the frozen premarket plan.  It writes no snapshot, no qualification and
# no outcome rows, hence this fixed marker on every response.
INTRADAY_STATISTICS_TRACK = "none_intraday_v1_unscored"

# ---------------------------------------------------------------------------
# v1 ranking heuristics (documented constants, deterministic, unvalidated).
# Each one marks an *independent* piece of intraday evidence as "supports".
# They are starting-point heuristics for research prioritisation only — none
# of them has been validated as a trading edge, and changing any of them must
# bump INTRADAY_TOP_SIGNAL_VERSION.
# ---------------------------------------------------------------------------
# 量能节奏：当日累计成交量 ≥ 前 20 个完整交易日全日中位数的 1.5 倍。
# 口径与 G-2 相同（未按盘中时点折算），因此开盘初段达到 1.5× 本身已显著。
VOLUME_PACE_SUPPORT_MIN = 1.5
# 缺口：|开盘价 − 参考前收| ≥ 0.75 × ATR14（ATR 标准化幅度），
# 或 |缺口百分比| ≥ 1.5%（ATR 不可用时的绝对回退口径）。
# 单一真源在 intraday_setups（v4 styleMatch 的 S2/S3 复用同一阈值），
# 此处保留原名别名，语义与数值不变。
GAP_ATR_MULTIPLE_SUPPORT_MIN = SETUP_GAP_ATR_MULTIPLE_MIN
GAP_PERCENT_SUPPORT_MIN = SETUP_GAP_PERCENT_MIN
# 波幅扩张：当日 (session high − session low) ≥ 1.0 × ATR14，
# 表示当日已走出不少于一个典型日波幅。
RANGE_EXPANSION_SUPPORT_MIN = 1.0
# 期权异动：最近一页（有界，≤10 条）异动成交 ≥ 3 条，且 Moomoo sentiment
# 多数方向非中性。它只陈述活跃度 + 供应商分类偏向，不推断开平仓。
OPTION_EVENT_COUNT_SUPPORT_MIN = 3
# 研究状态门槛：≥ 2 项独立盘中证据支持才标为「盘中活跃」，否则为「观察」。
ACTIVE_SUPPORT_MIN = 2

# Gap denominator provenance labels.  During a live weekday session the daily
# loader's latest completed bar is exactly the prior session, matching the
# weekly board's evidence philosophy.  During ``closed`` (nights/weekends) the
# loader's latest completed bar IS the session the snapshot still shows, so
# using it would compute "Friday open vs Friday close" — dishonest.  The
# builder therefore switches to the snapshot's own session-consistent
# ``prev_close`` and labels the switch explicitly.
GAP_BASIS_DAILY_LOADER = "session_open_vs_prior_completed_close_daily_loader"
GAP_BASIS_SNAPSHOT_PREV_CLOSE = "session_open_vs_moomoo_snapshot_prev_close"

SESSION_CHANGE_BASIS = "moomoo_snapshot_prev_close"

QUOTE_SCOPE_CURRENT = "current_session"
QUOTE_SCOPE_LATEST_PRIOR = "latest_prior_session"

_STATE_ORDER = {"active": 0, "watch": 1, "insufficient": 2}

INTRADAY_TOP_LIMITATIONS = (
    "盘中滚动研究：结果随行情持续变化，不冻结任何版本，也不能事后重建。",
    "不写入机会快照、qualification 或 5D/20D 结果统计（statistics_track=none_intraday_v1_unscored）。",
    "期权异动为 Moomoo 分类计数：不推断开平仓，不证明真实主动买卖方向。",
    "盘中排序以 15 分钟波段爆发分（推力×量比，v2 启发式阈值按 2026-07-31 标注样本校准）优先，"
    "证据计数次之；休市退回证据计数排序。两者都不是胜率或预期收益模型，不是买卖信号。",
    "量能节奏对比 20 个交易日全日中位数，未按盘中时点折算；VWAP 为累计额/量近似。",
    "v3 上下文信号（时段/财报/大盘/速度）只是标注，不参与排序、不隐藏行、不阻断操作：系统标注，用户过滤。",
    "时段提示为用户自身 1,653 笔已平仓交易统计的硬编码 v1 文案（Playbook 候选 R1/R3），不是市场统计或买卖信号；"
    "时段由 America/New_York 时钟判定，未接入交易所假日日历。",
    f"财报临近＝Finnhub 财报日历前向 {EARNINGS_WINDOW_DAYS} 天窗口；≤{EARNINGS_BLACKOUT_DAYS} 天标注"
    "「期权贵」是用户自身回避规则的 v1 启发式；日历不可得时显式标缺，绝不以「无财报」冒充。",
    "大盘对齐＝候选当前爆发方向 vs SPY 会话 VWAP 位置；SPY VWAP 为累计成交额 ÷ 累计成交量近似，"
    "非逐笔官方 VWAP，任一侧缺失即标缺。",
    "速度分级＝相邻两个 15 分钟窗口爆发分之差（5m K 线近似，非 1m/2m 秒级速度）；"
    "「减速」对应用户纪律 R1 的离场提示，不是系统信号。",
    "v4 形态相似度（styleMatch v1）＝当前时段几何形状 vs 你自己的三个 Playbook "
    "setup（S1 低点抬高突破 / S2 跳空托举 / S3 高开遇阻）的纯形状对比：5m K 线"
    "聚合到 15m 近似 + 会话快照派生字段，非 2m/1m 确认帧，不含 8/13 EMA 托举与"
    "回踩企稳细节；形态相似 ≠ 可交易，只是标注：不参与排序、不隐藏行、不是信号。",
    "形态标签的 Playbook 对应关系（候选/已晋升）为只读展示；Playbook 规则不反哺"
    "任何评分、排序或提示词。",
)

_RECENT_EVENT_FIELDS = (
    "event_id",
    "option_code",
    "fill_time",
    "ticker_type",
    "price",
    "volume",
    "turnover",
    "option_type",
    "strike_price",
    "expiry",
    "dte",
    "sentiment",
    "order_types",
    "strategy_type",
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_positive(value: Any) -> Optional[float]:
    number = _finite(value)
    return number if number is not None and number > 0 else None


@dataclass(frozen=True)
class IntradayQuoteInput:
    """Session snapshot fields the G-2 quote fetch already provides."""

    last_price: Optional[float] = None
    session_open: Optional[float] = None
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    prev_close: Optional[float] = None
    session_volume: Optional[float] = None
    session_turnover: Optional[float] = None
    quote_as_of: Optional[str] = None
    fetched_at: Optional[datetime] = None
    source: str = "moomoo_openapi"


@dataclass(frozen=True)
class IntradayDailyContext:
    """Completed-daily-bar derivations from the shared board loader."""

    atr14: Optional[float] = None
    atr14_last_bar_date: Optional[str] = None
    atr14_unavailable_reason: Optional[str] = None
    prior_median_volume: Optional[float] = None
    median_unavailable_reason: Optional[str] = None
    prior_close: Optional[float] = None
    prior_close_date: Optional[str] = None
    prior_high_20d: Optional[float] = None
    prior_low_20d: Optional[float] = None
    ema8: Optional[float] = None
    ema13: Optional[float] = None
    source: Optional[str] = None


def compute_intraday_daily_context(
    bars: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive prior-close / 20d-range / EMA context from completed bars.

    ``bars`` are :func:`src.opportunities.engine.completed_daily_bars` rows in
    chronological order.  The 20-session range window is the most recent 20
    completed bars (the range today's session is trading against), unlike the
    weekly engine's ``bars[-21:-1]`` window which excludes its own latest bar.
    Missing inputs stay ``None`` — nothing is estimated.
    """

    if not bars:
        return {
            "prior_close": None,
            "prior_close_date": None,
            "prior_high_20d": None,
            "prior_low_20d": None,
            "ema8": None,
            "ema13": None,
        }
    latest = bars[-1]
    window = bars[-20:]
    highs = [
        high for bar in window if (high := _finite(bar.get("high"))) is not None
    ]
    lows = [low for bar in window if (low := _finite(bar.get("low"))) is not None]
    closes = [
        close
        for bar in bars
        if (close := _finite_positive(bar.get("close"))) is not None
    ]
    latest_date = latest.get("date")
    return {
        "prior_close": _finite_positive(latest.get("close")),
        "prior_close_date": (
            latest_date.isoformat()
            if hasattr(latest_date, "isoformat")
            else (str(latest_date) if latest_date else None)
        ),
        # Only meaningful with the full 20-session window; a shorter window
        # would silently change the metric's meaning.
        "prior_high_20d": max(highs) if len(window) >= 20 and len(highs) == 20 else None,
        "prior_low_20d": min(lows) if len(window) >= 20 and len(lows) == 20 else None,
        "ema8": _ema_last(closes, 8),
        "ema13": _ema_last(closes, 13),
    }


def _parse_iso_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def compute_earnings_proximity(
    ticker: str,
    calendar: Optional[Mapping[str, Any]],
    *,
    market_date_et: str,
) -> dict[str, Any]:
    """v3 earnings-proximity context label for one candidate, fail-closed.

    ``calendar`` is the shared per-ET-date earnings snapshot
    (``state`` + ``dates_by_symbol`` + provenance).  Semantics:

    - ``state=ready`` + ``earnings_date`` set: the earliest earnings date in
      the forward :data:`EARNINGS_WINDOW_DAYS`-day window (0 = today);
      ``within_blackout`` is True when ``days_to_earnings ≤ 3``（用户回避规则，
      v1 启发式：财报临近权利金过贵）。
    - ``state=ready`` + ``earnings_date=None``: the calendar was fetched and
      shows no earnings inside the window（诚实的「窗口内无财报」）。
    - ``state=unavailable``: the calendar could not be fetched;
      ``within_blackout`` stays ``None`` — never a fake "safe".
    """

    base: dict[str, Any] = {
        "state": "unavailable",
        "days_to_earnings": None,
        "earnings_date": None,
        "within_blackout": None,
        "blackout_days": EARNINGS_BLACKOUT_DAYS,
        "window_days": EARNINGS_WINDOW_DAYS,
        "basis": EARNINGS_PROXIMITY_BASIS,
        "source": (calendar or {}).get("source") or "finnhub",
        "fetched_at": (calendar or {}).get("fetched_at"),
        "unavailable_reason": None,
    }
    if calendar is None:
        base["unavailable_reason"] = "earnings_calendar_missing"
        return base
    if calendar.get("state") != "ready":
        base["unavailable_reason"] = (
            calendar.get("unavailable_reason") or "earnings_calendar_unavailable"
        )
        return base
    market_date = _parse_iso_date(market_date_et)
    if market_date is None:
        base["unavailable_reason"] = "invalid_market_date"
        return base

    dates_by_symbol = calendar.get("dates_by_symbol") or {}
    window_end = market_date + timedelta(days=EARNINGS_WINDOW_DAYS)
    in_window = sorted(
        parsed
        for raw in (dates_by_symbol.get(ticker) or ())
        if (parsed := _parse_iso_date(raw)) is not None
        and market_date <= parsed <= window_end
    )
    base["state"] = "ready"
    if not in_window:
        base["within_blackout"] = False
        return base
    earliest = in_window[0]
    days = (earliest - market_date).days
    base["days_to_earnings"] = days
    base["earnings_date"] = earliest.isoformat()
    base["within_blackout"] = days <= EARNINGS_BLACKOUT_DAYS
    return base


def compute_market_context(
    spy_quote: Optional[IntradayQuoteInput],
    *,
    moomoo_enabled: bool,
) -> dict[str, Any]:
    """v3 SPY market context: session VWAP position from one quote snapshot.

    VWAP is the same honest approximation as every other panel（累计成交额 ÷
    累计成交量），explicitly labelled; any missing input keeps the context
    ``unavailable`` with a reason instead of guessing the market's side.
    """

    base: dict[str, Any] = {
        "ticker": MARKET_CONTEXT_TICKER,
        "state": "unavailable",
        "last_price": None,
        "vwap": None,
        "vwap_position": "unknown",
        "vwap_basis": VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
        "quote_as_of": None,
        "fetched_at": None,
        "source": "moomoo_openapi",
        "unavailable_reason": None,
    }
    if not moomoo_enabled:
        base["state"] = "not_configured"
        base["unavailable_reason"] = "moomoo_not_configured"
        return base
    if spy_quote is None:
        base["unavailable_reason"] = "spy_quote_unavailable"
        return base
    base["quote_as_of"] = spy_quote.quote_as_of
    base["fetched_at"] = _iso(spy_quote.fetched_at)
    base["source"] = spy_quote.source or "moomoo_openapi"
    last_price = _finite_positive(spy_quote.last_price)
    base["last_price"] = last_price
    turnover = _finite_positive(spy_quote.session_turnover)
    volume = _finite_positive(spy_quote.session_volume)
    if turnover is None or volume is None:
        base["unavailable_reason"] = (
            "missing_or_nonpositive_turnover"
            if volume is not None
            else "missing_or_nonpositive_volume"
            if turnover is not None
            else "missing_turnover_and_volume"
        )
        return base
    vwap = round(turnover / volume, 6)
    base["vwap"] = vwap
    if last_price is None:
        base["unavailable_reason"] = "missing_spy_last_price"
        return base
    if last_price > vwap:
        base["vwap_position"] = "above"
    elif last_price < vwap:
        base["vwap_position"] = "below"
    else:
        base["vwap_position"] = "flat"
    base["state"] = "ready"
    return base


def compute_market_alignment(
    burst_profile: Optional[Mapping[str, Any]],
    market_context: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """v3 alignment label: candidate burst direction vs SPY VWAP position.

    - burst ``up``  + SPY above VWAP → ``aligned``（顺势）
    - burst ``down`` + SPY below VWAP → ``aligned``
    - opposite pairing → ``against``（逆势）
    - any flat / unknown / missing side → ``unknown``（标缺）with a reason.

    A pure context label（用户纪律：setup 依赖大盘情绪）——it never enters the
    ranking keys and never hides a row.
    """

    base: dict[str, Any] = {
        "state": "unknown",
        "burst_direction": None,
        "spy_vwap_position": None,
        "basis": MARKET_ALIGNMENT_BASIS,
        "unavailable_reason": None,
    }
    current = (
        burst_profile.get("current")
        if isinstance(burst_profile, Mapping)
        and burst_profile.get("state") == "ready"
        else None
    )
    direction = (
        str(current.get("direction") or "") if isinstance(current, Mapping) else ""
    )
    if direction not in {"up", "down", "flat"}:
        base["unavailable_reason"] = "burst_direction_unavailable"
        return base
    base["burst_direction"] = direction
    spy_position = (
        str(market_context.get("vwap_position") or "")
        if isinstance(market_context, Mapping)
        and market_context.get("state") == "ready"
        else ""
    )
    if spy_position not in {"above", "below", "flat"}:
        base["unavailable_reason"] = (
            str((market_context or {}).get("unavailable_reason") or "")
            or "spy_vwap_position_unavailable"
        )
        return base
    base["spy_vwap_position"] = spy_position
    if direction == "flat" or spy_position == "flat":
        base["unavailable_reason"] = "flat_side_has_no_direction"
        return base
    aligned = (direction == "up" and spy_position == "above") or (
        direction == "down" and spy_position == "below"
    )
    base["state"] = "aligned" if aligned else "against"
    return base


def summarise_option_events(item: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate one serialised option-event payload into honest counts.

    ``dominant_sentiment`` is the strict plurality of Moomoo's own
    ``sentiment`` labels: ``bullish`` / ``bearish`` / ``neutral``.  Any tie —
    including bullish-vs-bearish — is reported as ``mixed`` and an empty
    classification set as ``unknown``; the aggregation never invents a
    direction the provider did not assign, and it never claims open/close
    intent.
    """

    state = str((item or {}).get("state") or "unavailable")
    events = list((item or {}).get("events") or [])
    counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
    unclassified = 0
    max_turnover: Optional[float] = None
    for event in events:
        sentiment = str(
            (event.get("sentiment") if isinstance(event, Mapping) else None) or ""
        ).strip().upper()
        if sentiment in counts:
            counts[sentiment] += 1
        else:
            unclassified += 1
        turnover = _finite(
            event.get("turnover") if isinstance(event, Mapping) else None
        )
        if turnover is not None and turnover >= 0:
            max_turnover = turnover if max_turnover is None else max(max_turnover, turnover)

    classified_total = sum(counts.values())
    if classified_total == 0:
        dominant = "unknown"
    else:
        top = max(counts.values())
        leaders = [name for name, value in counts.items() if value == top]
        if len(leaders) == 1:
            dominant = leaders[0].lower()
        else:
            dominant = "mixed"

    return {
        "state": state,
        "count": len(events),
        "all_count": (item or {}).get("all_count"),
        "bullish_count": counts["BULLISH"],
        "bearish_count": counts["BEARISH"],
        "neutral_count": counts["NEUTRAL"],
        "unclassified_count": unclassified,
        "dominant_sentiment": dominant,
        "max_single_turnover": max_turnover,
        "event_as_of": (item or {}).get("event_as_of"),
        "fetched_at": (item or {}).get("fetched_at"),
        "source": (item or {}).get("source") or "moomoo_openapi",
        "limitations": list((item or {}).get("limitations") or ()),
    }


def collect_recent_option_events(
    items_by_symbol: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Flatten per-symbol event pages into one newest-first bounded feed.

    Events without a provider ``fill_time`` cannot be ordered honestly and are
    dropped rather than sorted to an invented position.
    """

    flattened: list[dict[str, Any]] = []
    for symbol, item in items_by_symbol.items():
        for event in (item or {}).get("events") or []:
            if not isinstance(event, Mapping):
                continue
            fill_time = str(event.get("fill_time") or "").strip()
            if not fill_time:
                continue
            row = {field: event.get(field) for field in _RECENT_EVENT_FIELDS}
            row["ticker"] = symbol
            row["order_types"] = list(event.get("order_types") or ())
            flattened.append(row)
    # Provider fill_time is a uniform "YYYY-MM-DD HH:MM:SS[.fff]" string, so
    # lexicographic descending order equals newest-first; event_id breaks ties
    # deterministically.
    flattened.sort(
        key=lambda row: (str(row.get("fill_time")), str(row.get("event_id") or "")),
        reverse=True,
    )
    return flattened[: max(0, limit)]


def _evidence(
    *,
    ticker: str,
    domain: str,
    metric: str,
    value: Any,
    unit: Optional[str],
    status: str,
    source: str,
    observed_at: Optional[str],
    fetched_at: Optional[str],
    observation_window: str,
    quality_state: str = "observed",
    actionability: str = "research_input",
    limitations: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "evidence_id": f"{ticker}:{metric}",
        "domain": domain,
        "metric": metric,
        "value": value,
        "unit": unit,
        "status": status,
        "source": source,
        "observed_at": observed_at,
        "published_at": None,
        "fetched_at": fetched_at,
        "observation_window": observation_window,
        "quality_state": quality_state,
        "actionability": actionability,
        "limitations": limitations or [],
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def build_intraday_top_candidate(
    ticker: str,
    *,
    quote: Optional[IntradayQuoteInput],
    daily: IntradayDailyContext,
    option_events: Optional[Mapping[str, Any]],
    as_of: datetime,
    quote_session_scope: str,
    moomoo_enabled: bool,
    burst_profile: Optional[Mapping[str, Any]] = None,
    earnings_calendar: Optional[Mapping[str, Any]] = None,
    market_context: Optional[Mapping[str, Any]] = None,
    market_date_et: Optional[str] = None,
    setup_bars: Optional[Sequence[Mapping[str, Any]]] = None,
    playbook_refs: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Build one intraday candidate with the board's evidence contract shape."""

    as_of_text = _iso(as_of)
    resolved_market_date_et = (
        market_date_et or as_of.astimezone(_NEW_YORK_TZ).date().isoformat()
    )
    events = summarise_option_events(option_events)
    quote_source = quote.source if quote is not None else "moomoo_openapi"
    quote_fetched_at = _iso(quote.fetched_at) if quote is not None else None
    quote_as_of = quote.quote_as_of if quote is not None else None
    session_window = (
        "current_trading_session"
        if quote_session_scope == QUOTE_SCOPE_CURRENT
        else "latest_completed_trading_session"
    )
    session_label = (
        "当前交易时段" if quote_session_scope == QUOTE_SCOPE_CURRENT else "最近一个交易时段"
    )

    last_price = _finite_positive(quote.last_price) if quote else None
    session_open = _finite_positive(quote.session_open) if quote else None
    session_high = _finite_positive(quote.session_high) if quote else None
    session_low = _finite_positive(quote.session_low) if quote else None
    snapshot_prev_close = _finite_positive(quote.prev_close) if quote else None
    session_volume = _finite(quote.session_volume) if quote else None
    session_turnover = _finite(quote.session_turnover) if quote else None

    evidence: list[dict[str, Any]] = []

    # -- 1. session last price (anchor; never a support by itself) -----------
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="intraday_session",
            metric="session_last_price",
            value=last_price,
            unit="price",
            status="neutral" if last_price is not None else "unknown",
            source=quote_source,
            observed_at=quote_as_of,
            fetched_at=quote_fetched_at or as_of_text,
            observation_window=session_window,
            quality_state="observed" if last_price is not None else "missing",
        )
    )

    session_change_percent: Optional[float] = None
    if last_price is not None and snapshot_prev_close is not None:
        session_change_percent = round(
            (last_price / snapshot_prev_close - 1.0) * 100.0, 6
        )

    # -- 2. momentum burst（波段爆发，v2 主信号） ------------------------------
    # 5m K 线来自共享历史加载器，与 Moomoo 快照互相独立：任何一侧失败都只让
    # 自己显式标缺，绝不拖垮另一侧的证据。
    bursts = dict(
        burst_profile
        if burst_profile is not None
        else unavailable_burst_profile("burst_input_missing")
    )
    burst_current = bursts.get("current") or None
    burst_score = _finite(
        burst_current.get("score") if isinstance(burst_current, Mapping) else None
    )
    burst_observed = bursts.get("state") == "ready" and burst_score is not None
    burst_supports = burst_observed and burst_score >= BURST_SUPPORT_MIN
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="intraday_session",
            metric="session_momentum_burst",
            value=(
                {
                    "current": dict(burst_current)
                    if isinstance(burst_current, Mapping)
                    else None,
                    "leg_count": len(bursts.get("legs") or ()),
                    "session_date_et": bursts.get("session_date_et"),
                    "median_basis": bursts.get("median_basis"),
                }
                if burst_observed
                else None
            ),
            unit="ratio",
            status="supports" if burst_supports else ("neutral" if burst_observed else "unknown"),
            source=bursts.get("source") or "history_5m_loader",
            observed_at=bursts.get("session_date_et"),
            fetched_at=bursts.get("fetched_at") or as_of_text,
            observation_window=BURST_BASIS,
            quality_state="derived" if burst_observed else "missing",
            limitations=list(BURST_LIMITATIONS),
        )
    )

    # -- 3. gap vs prior close ------------------------------------------------
    if quote_session_scope == QUOTE_SCOPE_CURRENT:
        gap_reference_close = daily.prior_close
        gap_basis = GAP_BASIS_DAILY_LOADER
        gap_reference_date = daily.prior_close_date
        gap_missing_reference_reason = "missing_prior_completed_close"
        gap_limitations = [
            "缺口分母为共享日线加载器的上一完整交易日收盘，与周内榜同源；缺口不预测方向延续。",
        ]
    else:
        # Closed session: the loader's latest completed bar IS the session the
        # snapshot still shows, so switch to the snapshot's session-consistent
        # prev_close instead of computing "Friday open vs Friday close".
        gap_reference_close = snapshot_prev_close
        gap_basis = GAP_BASIS_SNAPSHOT_PREV_CLOSE
        gap_reference_date = None
        gap_missing_reference_reason = "missing_snapshot_prev_close"
        gap_limitations = [
            "休市时段：日线加载器的最新完整日线即快照所示交易时段，缺口分母改用快照自带前收并显式标注。",
            "缺口不预测方向延续。",
        ]

    gap_percent: Optional[float] = None
    gap_atr_multiple: Optional[float] = None
    gap_unavailable_reason: Optional[str] = None
    if quote is None or (session_open is None and last_price is None):
        gap_unavailable_reason = "quote_unavailable" if quote is None else "missing_session_open"
    elif session_open is None:
        gap_unavailable_reason = "missing_session_open"
    elif gap_reference_close is None:
        gap_unavailable_reason = gap_missing_reference_reason
    else:
        gap_percent = round((session_open / gap_reference_close - 1.0) * 100.0, 6)
        if daily.atr14 is not None and daily.atr14 > 0:
            gap_atr_multiple = round(
                abs(session_open - gap_reference_close) / daily.atr14, 6
            )
    gap_supports = gap_percent is not None and (
        (gap_atr_multiple is not None and gap_atr_multiple >= GAP_ATR_MULTIPLE_SUPPORT_MIN)
        or abs(gap_percent) >= GAP_PERCENT_SUPPORT_MIN
    )
    gap_direction: Optional[str] = None
    if gap_percent is not None and gap_percent > 0:
        gap_direction = "up"
    elif gap_percent is not None and gap_percent < 0:
        gap_direction = "down"
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="intraday_session",
            metric="session_gap_percent",
            value=(
                {
                    "gap_percent": gap_percent,
                    "gap_atr_multiple": gap_atr_multiple,
                    "session_open": session_open,
                    "reference_close": gap_reference_close,
                    "reference_close_date": gap_reference_date,
                    "basis": gap_basis,
                }
                if gap_percent is not None
                else None
            ),
            unit="percent",
            status="supports" if gap_supports else ("neutral" if gap_percent is not None else "unknown"),
            source=quote_source,
            observed_at=quote_as_of,
            fetched_at=quote_fetched_at or as_of_text,
            observation_window=session_window,
            quality_state="derived" if gap_percent is not None else "missing",
            limitations=gap_limitations,
        )
    )

    # -- 4. volume pace (G-2 helper output, full-day-median basis) ------------
    volume_pace_ratio: Optional[float] = None
    volume_pace_unavailable_reason: Optional[str] = None
    if quote is None:
        volume_pace_unavailable_reason = "quote_unavailable"
    elif daily.prior_median_volume is None or daily.prior_median_volume <= 0:
        volume_pace_unavailable_reason = (
            daily.median_unavailable_reason or "missing_prior_median_volume"
        )
    elif session_volume is None or session_volume < 0:
        volume_pace_unavailable_reason = "missing_session_volume"
    else:
        volume_pace_ratio = round(session_volume / daily.prior_median_volume, 6)
    pace_supports = (
        volume_pace_ratio is not None and volume_pace_ratio >= VOLUME_PACE_SUPPORT_MIN
    )
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="intraday_session",
            metric="session_volume_pace",
            value=volume_pace_ratio,
            unit="ratio",
            status="supports" if pace_supports else ("neutral" if volume_pace_ratio is not None else "unknown"),
            source=quote_source,
            observed_at=quote_as_of,
            fetched_at=quote_fetched_at or as_of_text,
            observation_window=VOLUME_PACE_BASIS,
            quality_state="derived" if volume_pace_ratio is not None else "missing",
            limitations=[
                "对比 20 个完整交易日的全日成交量中位数，未按盘中时点折算；开盘初段比值偏低属正常。",
            ],
        )
    )

    # -- 5. VWAP position, support only when aligned with gap direction -------
    vwap: Optional[float] = None
    vwap_unavailable_reason: Optional[str] = None
    turnover_value = _finite_positive(session_turnover)
    volume_value = _finite_positive(session_volume)
    if quote is None:
        vwap_unavailable_reason = "quote_unavailable"
    elif turnover_value is None and volume_value is None:
        vwap_unavailable_reason = "missing_turnover_and_volume"
    elif turnover_value is None:
        vwap_unavailable_reason = "missing_or_nonpositive_turnover"
    elif volume_value is None:
        vwap_unavailable_reason = "missing_or_nonpositive_volume"
    else:
        vwap = round(turnover_value / volume_value, 6)
    if vwap is None or last_price is None:
        vwap_position = "unknown"
    elif last_price > vwap:
        vwap_position = "above"
    elif last_price < vwap:
        vwap_position = "below"
    else:
        vwap_position = "flat"
    vwap_aligned = (
        gap_direction == "up" and vwap_position == "above"
    ) or (gap_direction == "down" and vwap_position == "below")
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="intraday_session",
            metric="session_vwap_position",
            value=(
                {"vwap": vwap, "position": vwap_position, "gap_direction": gap_direction}
                if vwap_position != "unknown"
                else None
            ),
            unit=None,
            status="supports" if vwap_aligned else ("neutral" if vwap_position != "unknown" else "unknown"),
            source=quote_source,
            observed_at=quote_as_of,
            fetched_at=quote_fetched_at or as_of_text,
            observation_window=VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
            quality_state="derived" if vwap_position != "unknown" else "missing",
            limitations=[
                "VWAP 为当日累计成交额 ÷ 累计成交量的近似值，非逐笔官方 VWAP；仅当方向与缺口一致时计为支持。",
            ],
        )
    )

    # -- 6. ATR range expansion ----------------------------------------------
    atr_range_expansion: Optional[float] = None
    range_expansion_unavailable_reason: Optional[str] = None
    if quote is None:
        range_expansion_unavailable_reason = "quote_unavailable"
    elif session_high is None or session_low is None or session_high < session_low:
        range_expansion_unavailable_reason = "missing_session_high_low"
    elif daily.atr14 is None or daily.atr14 <= 0:
        range_expansion_unavailable_reason = (
            daily.atr14_unavailable_reason or "missing_atr14"
        )
    else:
        atr_range_expansion = round((session_high - session_low) / daily.atr14, 6)
    range_supports = (
        atr_range_expansion is not None
        and atr_range_expansion >= RANGE_EXPANSION_SUPPORT_MIN
    )
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="intraday_session",
            metric="session_range_atr_expansion",
            value=(
                {
                    "expansion": atr_range_expansion,
                    "session_high": session_high,
                    "session_low": session_low,
                    "atr14": daily.atr14,
                    "atr14_last_bar_date": daily.atr14_last_bar_date,
                }
                if atr_range_expansion is not None
                else None
            ),
            unit="ratio",
            status="supports" if range_supports else ("neutral" if atr_range_expansion is not None else "unknown"),
            source=quote_source,
            observed_at=quote_as_of,
            fetched_at=quote_fetched_at or as_of_text,
            observation_window="session_high_low_range_vs_wilder_atr14_completed_bars",
            quality_state="derived" if atr_range_expansion is not None else "missing",
            limitations=[
                "ATR14 基于已完成日线的 Wilder 平滑，不含当日未完成 K 线。",
            ],
        )
    )

    # -- 7. option-event activity (provider classification counts only) -------
    events_observed = events["state"] in {"ready", "empty"}
    event_supports = (
        events["count"] >= OPTION_EVENT_COUNT_SUPPORT_MIN
        and events["dominant_sentiment"] in {"bullish", "bearish"}
    )
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="options_flow",
            metric="option_event_activity",
            value=(
                {
                    "count": events["count"],
                    "all_count": events["all_count"],
                    "dominant_sentiment": events["dominant_sentiment"],
                    "bullish_count": events["bullish_count"],
                    "bearish_count": events["bearish_count"],
                    "neutral_count": events["neutral_count"],
                    "max_single_turnover": events["max_single_turnover"],
                }
                if events_observed
                else None
            ),
            unit=None,
            status="supports" if event_supports else ("neutral" if events_observed else "unknown"),
            source=events["source"],
            observed_at=events["event_as_of"],
            fetched_at=events["fetched_at"] or as_of_text,
            observation_window="latest_bounded_option_event_page",
            quality_state="observed" if events_observed else "missing",
            limitations=[
                "sentiment/ticker_type 是 Moomoo 分类，不推断开平仓，不证明真实主动买卖方向。",
                "仅统计最近一页有界异动成交，不是完整逐笔期权流。",
            ],
        )
    )

    # -- 8. prior-day structure: CONTEXT ONLY, never a ranking support --------
    range_position: Optional[str] = None
    if last_price is not None and daily.prior_high_20d is not None and daily.prior_low_20d is not None:
        if last_price > daily.prior_high_20d:
            range_position = "above_prior_20d_high"
        elif last_price < daily.prior_low_20d:
            range_position = "below_prior_20d_low"
        else:
            range_position = "inside_prior_20d_range"
    ema_alignment: Optional[str] = None
    if daily.prior_close is not None and daily.ema8 is not None and daily.ema13 is not None:
        if daily.prior_close > daily.ema8 > daily.ema13:
            ema_alignment = "bullish"
        elif daily.prior_close < daily.ema8 < daily.ema13:
            ema_alignment = "bearish"
        else:
            ema_alignment = "mixed"
    prior_context_value = {
        "prior_close": daily.prior_close,
        "prior_close_date": daily.prior_close_date,
        "prior_high_20d": daily.prior_high_20d,
        "prior_low_20d": daily.prior_low_20d,
        "range_position": range_position,
        "ema_alignment": ema_alignment,
    }
    prior_context_observed = daily.prior_close is not None
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="technical_structure",
            metric="prior_day_structure_context",
            value=prior_context_value if prior_context_observed else None,
            unit=None,
            status="neutral" if prior_context_observed else "unknown",
            source=daily.source or "unknown_provider",
            observed_at=daily.prior_close_date,
            fetched_at=as_of_text,
            observation_window="latest_completed_daily_bars",
            quality_state="derived" if prior_context_observed else "missing",
            actionability="research_context",
            limitations=[
                "上一完整交易日结构仅作背景（research_context），不参与盘中排序计数。",
            ],
        )
    )

    # -- 9. v4 styleMatch v1（形态相似度）：纯标注，绝不进入 supports 计数 ----
    # 复用同一批 5m K 线（波段爆发通道已取回，零新增请求）与本函数自己的
    # 缺口/VWAP 派生值；SPY 侧只取 v3 大盘上下文的会话 VWAP 位置。
    spy_vwap_position = (
        str(market_context.get("vwap_position") or "") or None
        if isinstance(market_context, Mapping)
        and market_context.get("state") == "ready"
        else None
    )
    setup_match = compute_setup_match_profile(
        setup_bars,
        market_date_et=resolved_market_date_et,
        quote_session_scope=quote_session_scope,
        last_price=last_price,
        session_open=session_open,
        session_low=session_low,
        gap_percent=gap_percent,
        gap_atr_multiple=gap_atr_multiple,
        gap_reference_close=gap_reference_close,
        vwap=vwap,
        spy_vwap_position=spy_vwap_position,
        playbook_refs=playbook_refs,
    )

    supporting_count = sum(
        1
        for item in evidence
        if item["status"] == "supports" and item["actionability"] == "research_input"
    )

    # Fail closed: without a usable live last price the row cannot claim any
    # intraday research state — it is 数据不足, regardless of daily context.
    if last_price is None:
        research_state = "insufficient"
        state_reason = (
            "MOOMOO_OPEND_ENABLED 未启用，无实时快照可用。"
            if not moomoo_enabled
            else "缺少可用现价快照；该标的按数据不足处理，不参与盘中排序结论。"
        )
    elif supporting_count >= ACTIVE_SUPPORT_MIN:
        research_state = "active"
        state_reason = (
            f"{supporting_count} 项独立盘中证据支持（{session_label}）；"
            "仅为研究优先级，不是买卖信号。"
        )
    else:
        research_state = "watch"
        state_reason = (
            f"仅 {supporting_count} 项盘中证据支持；保留观察，等待更多独立确认。"
        )

    return {
        "ticker": ticker,
        "research_state": research_state,
        "state_reason": state_reason,
        "supporting_evidence_count": supporting_count,
        "source": quote_source,
        "fetched_at": quote_fetched_at or as_of_text,
        "quote_as_of": quote_as_of,
        "last_price": last_price,
        "session_open": session_open,
        "session_high": session_high,
        "session_low": session_low,
        "session_change_percent": session_change_percent,
        "session_change_basis": SESSION_CHANGE_BASIS,
        "gap_percent": gap_percent,
        "gap_atr_multiple": gap_atr_multiple,
        "gap_basis": gap_basis,
        "gap_unavailable_reason": gap_unavailable_reason,
        "volume_pace_ratio": volume_pace_ratio,
        "volume_pace_basis": VOLUME_PACE_BASIS,
        "volume_pace_unavailable_reason": volume_pace_unavailable_reason,
        "vwap": vwap,
        "vwap_position": vwap_position,
        "vwap_basis": VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
        "vwap_unavailable_reason": vwap_unavailable_reason,
        "atr14": daily.atr14,
        "atr14_last_bar_date": daily.atr14_last_bar_date,
        "atr_range_expansion": atr_range_expansion,
        "range_expansion_unavailable_reason": range_expansion_unavailable_reason,
        "session_bursts": bursts,
        # v3 上下文信号：只是标注，不参与 supporting_evidence_count 或排序。
        "earnings_proximity": compute_earnings_proximity(
            ticker,
            earnings_calendar,
            market_date_et=resolved_market_date_et,
        ),
        "market_alignment": compute_market_alignment(bursts, market_context),
        # v4 styleMatch v1：与你的 S1/S2/S3 setup 的形状相似度，仅作标注。
        "setup_match": setup_match,
        "option_activity": {
            "state": events["state"],
            "count": events["count"],
            "all_count": events["all_count"],
            "bullish_count": events["bullish_count"],
            "bearish_count": events["bearish_count"],
            "neutral_count": events["neutral_count"],
            "unclassified_count": events["unclassified_count"],
            "dominant_sentiment": events["dominant_sentiment"],
            "max_single_turnover": events["max_single_turnover"],
            "event_as_of": events["event_as_of"],
            "fetched_at": events["fetched_at"],
            "source": events["source"],
            "limitations": events["limitations"],
        },
        "prior_day_context": prior_context_value,
        "evidence": evidence,
        "message": state_reason,
        "limitations": list(INTRADAY_TOP_LIMITATIONS),
    }


def _candidate_current_burst_score(candidate: Mapping[str, Any]) -> Optional[float]:
    bursts = candidate.get("session_bursts")
    if not isinstance(bursts, Mapping) or bursts.get("state") != "ready":
        return None
    current = bursts.get("current")
    if not isinstance(current, Mapping):
        return None
    return _finite(current.get("score"))


def _candidate_best_burst_score(candidate: Mapping[str, Any]) -> Optional[float]:
    """会话内最强波段分：取当前窗口与全部已识别波段的最大值。

    休市复盘视角下「今天谁走出过最强的波」比收盘时点的末窗口分更有意义；
    盘中该值与当前窗口分共同决定排序上限。缺失时返回 None，不以 0 冒充。
    """

    bursts = candidate.get("session_bursts")
    if not isinstance(bursts, Mapping) or bursts.get("state") != "ready":
        return None
    scores: list[float] = []
    current = bursts.get("current")
    if isinstance(current, Mapping):
        value = _finite(current.get("score"))
        if value is not None:
            scores.append(value)
    legs = bursts.get("legs")
    if isinstance(legs, Sequence):
        for leg in legs:
            if isinstance(leg, Mapping):
                value = _finite(leg.get("score"))
                if value is not None:
                    scores.append(value)
    return max(scores) if scores else None


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """v1 ordering: research state, then supports count, then ticker."""

    return (
        _STATE_ORDER.get(str(candidate.get("research_state")), 99),
        -int(candidate.get("supporting_evidence_count") or 0),
        str(candidate.get("ticker") or ""),
    )


def _candidate_burst_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """v2 in-session ordering: current burst score first, then supports/state.

    A missing/unavailable burst never fabricates a score of 0 — those rows
    simply fall back behind every scored row, ordered by the v1 key.
    """

    score = _candidate_current_burst_score(candidate)
    return (
        0 if score is not None else 1,
        -(score if score is not None else 0.0),
        -int(candidate.get("supporting_evidence_count") or 0),
        _STATE_ORDER.get(str(candidate.get("research_state")), 99),
        str(candidate.get("ticker") or ""),
    )


def _candidate_session_best_burst_sort_key(
    candidate: Mapping[str, Any],
) -> tuple[Any, ...]:
    """休市排序：最近交易时段的最强波段分优先，缺失退回 v1 序。"""

    score = _candidate_best_burst_score(candidate)
    return (
        0 if score is not None else 1,
        -(score if score is not None else 0.0),
        -int(candidate.get("supporting_evidence_count") or 0),
        _STATE_ORDER.get(str(candidate.get("research_state")), 99),
        str(candidate.get("ticker") or ""),
    )


def build_intraday_top_run(
    *,
    symbols: Sequence[str],
    unsupported_symbols: Sequence[str],
    quotes: Mapping[str, IntradayQuoteInput],
    dailies: Mapping[str, IntradayDailyContext],
    option_event_items: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
    market_date_et: str,
    session_state: str,
    session_state_basis: str,
    limit: int,
    moomoo_enabled: bool,
    burst_profiles: Optional[Mapping[str, Mapping[str, Any]]] = None,
    earnings_calendar: Optional[Mapping[str, Any]] = None,
    spy_quote: Optional[IntradayQuoteInput] = None,
    setup_bars: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    playbook_refs: Optional[Mapping[str, Mapping[str, Any]]] = None,
    include_all_candidates: bool = False,
) -> dict[str, Any]:
    """Assemble the deterministic intraday Top-N research run.

    ``include_all_candidates=True``（watchlist 两层模式的深度层使用）返回全部
    已排序候选而不按 ``limit`` 截断：深度层名单本身已由异动闸门有界（K +
    计划钉选），再按 ``limit`` 截断会造成「已做深度分析却无声消失」的不诚实
    截断。``requested_limit`` 仍如实回显请求值。默认 ``False`` 保持既有合同。
    """

    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")

    quote_session_scope = (
        QUOTE_SCOPE_LATEST_PRIOR if session_state == "closed" else QUOTE_SCOPE_CURRENT
    )
    quote_session_label = (
        "最近一个交易时段" if quote_session_scope == QUOTE_SCOPE_LATEST_PRIOR else "当前交易时段"
    )

    # v3 上下文：SPY 大盘上下文与时段标签都在这里统一派生一次。
    market_context = compute_market_context(
        spy_quote, moomoo_enabled=moomoo_enabled
    )
    session_phase = market_session_phase(as_of)

    candidates = [
        build_intraday_top_candidate(
            ticker,
            quote=quotes.get(ticker),
            daily=dailies.get(ticker) or IntradayDailyContext(),
            option_events=option_event_items.get(ticker),
            as_of=as_of,
            quote_session_scope=quote_session_scope,
            moomoo_enabled=moomoo_enabled,
            burst_profile=(burst_profiles or {}).get(ticker),
            earnings_calendar=earnings_calendar,
            market_context=market_context,
            market_date_et=market_date_et,
            setup_bars=(setup_bars or {}).get(ticker),
            playbook_refs=playbook_refs,
        )
        for ticker in symbols
    ]
    # 盘前/盘中/盘后按当前爆发分优先；休市按「最近交易时段的最强波段分」
    # 排序（复盘视角：今天谁走出过最强的波），两种口径都不冻结、不入统计。
    burst_ranked = quote_session_scope == QUOTE_SCOPE_CURRENT
    candidates.sort(
        key=(
            _candidate_burst_sort_key
            if burst_ranked
            else _candidate_session_best_burst_sort_key
        )
    )
    ranking_method = RANKING_METHOD_BURST_FIRST

    as_of_text = _iso(as_of)
    fingerprint = "|".join(
        [INTRADAY_TOP_SIGNAL_VERSION, str(as_of_text), *symbols]
    )
    run_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

    return {
        "schema_version": INTRADAY_TOP_SCHEMA_VERSION,
        "run_id": f"itr_{market_date_et}_{run_hash}",
        "generated_at": as_of_text,
        "as_of": as_of_text,
        "market_date_et": market_date_et,
        "session_state": session_state,
        "session_state_basis": session_state_basis,
        "session_phase": session_phase,
        "session_phase_label": session_phase_label(session_phase),
        "session_phase_hint_basis": SESSION_PHASE_HINT_BASIS,
        "quote_session_scope": quote_session_scope,
        "quote_session_label": quote_session_label,
        "market_context": market_context,
        "signal_version": INTRADAY_TOP_SIGNAL_VERSION,
        "ranking_method": ranking_method,
        "statistics_track": INTRADAY_STATISTICS_TRACK,
        "moomoo_enabled": bool(moomoo_enabled),
        "universe": list(symbols),
        "unsupported_symbols": list(unsupported_symbols),
        "requested_limit": limit,
        "candidate_count": (
            len(candidates) if include_all_candidates else min(len(candidates), limit)
        ),
        "candidates": (
            list(candidates) if include_all_candidates else candidates[:limit]
        ),
        "recent_option_events": collect_recent_option_events(option_event_items),
        "limitations": list(INTRADAY_TOP_LIMITATIONS),
    }
