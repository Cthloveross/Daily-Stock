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
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from src.opportunities.engine import _ema_last
from src.opportunities.intraday import (
    VOLUME_PACE_BASIS,
    VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
)
from src.opportunities.intraday_bursts import (
    BURST_BASIS,
    BURST_LIMITATIONS,
    BURST_SUPPORT_MIN,
    unavailable_burst_profile,
)

# v2: 波段爆发（15 分钟推力×量比）成为盘中主排序信号；聚合证据退居次序。
INTRADAY_TOP_SIGNAL_VERSION = "intraday_session_evidence_v2"
INTRADAY_TOP_SCHEMA_VERSION = "intraday-top/1.0"

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
GAP_ATR_MULTIPLE_SUPPORT_MIN = 0.75
GAP_PERCENT_SUPPORT_MIN = 1.5
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
) -> dict[str, Any]:
    """Build one intraday candidate with the board's evidence contract shape."""

    as_of_text = _iso(as_of)
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
) -> dict[str, Any]:
    """Assemble the deterministic intraday Top-N research run."""

    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")

    quote_session_scope = (
        QUOTE_SCOPE_LATEST_PRIOR if session_state == "closed" else QUOTE_SCOPE_CURRENT
    )
    quote_session_label = (
        "最近一个交易时段" if quote_session_scope == QUOTE_SCOPE_LATEST_PRIOR else "当前交易时段"
    )

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
        )
        for ticker in symbols
    ]
    # 盘前/盘中/盘后按当前爆发分优先；休市退回 v1 证据计数排序，但候选仍
    # 携带最近一个交易时段的波段列表（晚间复盘可见「今日走了几波」）。
    burst_ranked = quote_session_scope == QUOTE_SCOPE_CURRENT
    candidates.sort(
        key=_candidate_burst_sort_key if burst_ranked else _candidate_sort_key
    )
    ranking_method = (
        RANKING_METHOD_BURST_FIRST if burst_ranked else RANKING_METHOD_EVIDENCE_COUNT
    )

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
        "quote_session_scope": quote_session_scope,
        "quote_session_label": quote_session_label,
        "signal_version": INTRADAY_TOP_SIGNAL_VERSION,
        "ranking_method": ranking_method,
        "statistics_track": INTRADAY_STATISTICS_TRACK,
        "moomoo_enabled": bool(moomoo_enabled),
        "universe": list(symbols),
        "unsupported_symbols": list(unsupported_symbols),
        "requested_limit": limit,
        "candidate_count": min(len(candidates), limit),
        "candidates": candidates[:limit],
        "recent_option_events": collect_recent_option_events(option_event_items),
        "limitations": list(INTRADAY_TOP_LIMITATIONS),
    }
