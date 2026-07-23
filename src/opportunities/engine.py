# -*- coding: utf-8 -*-
"""Pure, evidence-first daily opportunity candidate engine.

This module intentionally does not fetch data, call an LLM, or persist
anything.  Callers inject completed daily histories and the currently stored
Regime snapshot.  The output is a research queue, not a trade recommendation:
there is no opaque composite score and unavailable option-flow / dark-pool
inputs remain explicit unknowns.
"""
from __future__ import annotations

import hashlib
import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

SIGNAL_VERSION = "daily_completed_bars_v1"
SCHEMA_VERSION = "1.1"
_NEW_YORK = ZoneInfo("America/New_York")
_CORE_EVIDENCE_COUNT = 7
_MAX_COMPLETED_BAR_AGE_DAYS = 4
_US_OPTION_UNDERLYING_PATTERN = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z])?$")

_RESEARCH_STATE_ORDER = {
    "research_ready": 0,
    "watch_only": 1,
    "context_only": 2,
    "blocked": 3,
}
_STYLE_MATCH_ORDER = {
    "exact": 0,
    "compatible": 1,
    "conflict": 2,
    "unknown": 3,
}
_COMPLETENESS_ORDER = {
    "complete": 0,
    "partial": 1,
    "insufficient": 2,
}


@dataclass(frozen=True)
class DailyHistoryInput:
    """One symbol's loader result, including failure and provenance state."""

    bars: Sequence[Mapping[str, Any]]
    source: Optional[str] = None
    fetched_at: Optional[datetime] = None
    error: Optional[str] = None


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_aware_utc(value).isoformat()


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bar_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_symbols(symbols: Sequence[str]) -> list[str]:
    """Upper-case and de-duplicate symbols without changing first-seen order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = str(raw or "").strip().upper()
        if symbol.startswith("US."):
            symbol = symbol[3:]
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    return normalized


def is_supported_us_option_underlying(symbol: str) -> bool:
    """Return whether the first slice can treat ``symbol`` as a US option underlying.

    This is deliberately a conservative universe-format gate, not proof that
    every matching security currently has listed options.  Numeric CN/HK
    tickers and exchange-prefixed non-US symbols are rejected per candidate.
    """

    normalized = str(symbol or "").strip().upper()
    if normalized.startswith("US."):
        normalized = normalized[3:]
    return bool(_US_OPTION_UNDERLYING_PATTERN.fullmatch(normalized))


def completed_daily_bars(
    bars: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    """Return valid, de-duplicated bars that are complete as of New York time.

    The first slice is a strict prior-session scan: a bar whose date equals
    ``market_date_et`` is excluded even after 16:00 ET.  That keeps an
    after-close click semantically identical to the morning scan and prevents
    a newly forming/finalising daily candle from changing the run type.
    """

    local_as_of = _as_aware_utc(as_of).astimezone(_NEW_YORK)
    local_date = local_as_of.date()
    by_date: dict[date, dict[str, Any]] = {}

    for raw in bars:
        observed_date = _bar_date(raw.get("date"))
        close = _safe_float(raw.get("close"))
        high = _safe_float(raw.get("high"))
        low = _safe_float(raw.get("low"))
        open_ = _safe_float(raw.get("open"))
        if observed_date is None or close is None or close <= 0:
            continue
        if observed_date >= local_date:
            continue
        if high is not None and low is not None and high < low:
            continue

        by_date[observed_date] = {
            "date": observed_date,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": _safe_float(raw.get("volume")),
            "amount": _safe_float(raw.get("amount")),
        }

    return [by_date[key] for key in sorted(by_date)]


def _ema_last(values: Sequence[float], period: int) -> Optional[float]:
    """Return a conventionally seeded EMA, or None without a full seed."""

    if len(values) < period:
        return None
    ema = statistics.fmean(values[:period])
    alpha = 2.0 / (period + 1.0)
    for value in values[period:]:
        ema = alpha * value + (1.0 - alpha) * ema
    return ema


def _observed_at(bar_date: date) -> str:
    return datetime.combine(bar_date, time(16, 0), tzinfo=_NEW_YORK).isoformat()


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


def _readiness(
    domain: str,
    state: str,
    *,
    source: Optional[str],
    as_of: Optional[str],
    actionability: str,
    message: str,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "state": state,
        "source": source,
        "as_of": as_of,
        "actionability": actionability,
        "message": message,
    }


def _gate(
    gate_id: str,
    status: str,
    reason: str,
    evidence_refs: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "status": status,
        "reason": reason,
        "evidence_refs": evidence_refs or [],
    }


def _regime_quality_state(regime: Optional[Mapping[str, Any]]) -> str:
    if not regime:
        return "unavailable"
    explicit = str(regime.get("quality_state") or "").strip().lower()
    if explicit in {"ready", "degraded", "unavailable"}:
        return explicit
    snapshot = regime.get("snapshot")
    if isinstance(snapshot, Mapping):
        from src.regime.quality import assess_regime_snapshot

        return str(assess_regime_snapshot(snapshot)["state"])
    # Backward-compatible injected fixtures and callers that predate snapshot
    # quality metadata are treated as ready. Persisted rows always carry a
    # snapshot and therefore take the conservative branch above.
    return "ready"


def _regime_details(
    regime: Optional[Mapping[str, Any]],
) -> tuple[Any, str, str, str]:
    if not regime:
        return None, "unknown", "unknown", "unavailable"
    label = str(regime.get("label") or "unknown").strip().lower()
    quality_state = _regime_quality_state(regime)
    value = {
        "date": str(regime.get("date")) if regime.get("date") is not None else None,
        "score": regime.get("score"),
        "label": label,
        "version": regime.get("version"),
        "quality_state": quality_state,
    }
    if quality_state == "unavailable" or label == "unavailable":
        return None, "unknown", label, "unavailable"
    if quality_state == "degraded":
        return value, "neutral", label, "degraded"
    if label in {"aggressive", "standard"}:
        return value, "supports", label, "ready"
    if label == "no_trade":
        return value, "contradicts", label, "ready"
    return value, "neutral", label, "ready"


def _build_candidate(
    ticker: str,
    history: DailyHistoryInput,
    regime: Optional[Mapping[str, Any]],
    *,
    as_of: datetime,
    run_id: str,
) -> dict[str, Any]:
    as_of_utc = _as_aware_utc(as_of)
    fetched_at = _iso(history.fetched_at or as_of_utc)
    source = str(history.source or "unknown_provider")
    supported_universe = is_supported_us_option_underlying(ticker)
    bars = completed_daily_bars(history.bars, as_of=as_of_utc)
    latest = bars[-1] if bars else None
    latest_observed_at = _observed_at(latest["date"]) if latest else None
    market_date = as_of_utc.astimezone(_NEW_YORK).date()
    latest_age_days = (market_date - latest["date"]).days if latest else None
    history_fresh = (
        latest_age_days is not None
        and latest_age_days <= _MAX_COMPLETED_BAR_AGE_DAYS
    )

    evidence: list[dict[str, Any]] = []
    setup_tags: list[str] = []
    hard_gates: list[dict[str, Any]] = []
    unknowns = [
        "尚无用户确认的 Playbook，不能声称候选符合个人交易风格。",
        "候选排名尚未接入完整逐笔期权流与 NBBO；Moomoo 异常事件面板不能识别开平仓或真实主动方。",
        "第一阶段未加载 FINRA ATS 延迟周报；即使后续加载也只能作为背景，不能称为实时暗池流。",
    ]
    if not supported_universe:
        unknowns.append("该标的不属于第一阶段支持的美股期权 underlying universe。")

    hard_gates.append(
        _gate(
            "us_options_universe",
            "passed" if supported_universe else "failed",
            (
                "标的符合第一阶段美股期权 underlying 格式边界；是否实际有上市期权仍待期权链验证。"
                if supported_universe
                else "第一阶段仅支持美股期权 underlying；非美股标的保留为 blocked，不影响同批其他候选。"
            ),
        )
    )

    history_ready = len(bars) >= 21
    hard_gates.append(
        _gate(
            "completed_daily_history",
            "passed" if history_ready else "failed",
            (
                f"已取得 {len(bars)} 根已完成日线，可计算 20 日上下文。"
                if history_ready
                else f"仅取得 {len(bars)} 根已完成日线，需要至少 21 根计算当前 bar 与此前 20 日上下文。"
            ),
        )
    )
    hard_gates.append(
        _gate(
            "latest_completed_daily_bar_freshness",
            "passed" if history_fresh else "failed",
            (
                (
                    f"最新完整日线距 market_date_et {latest_age_days} 个自然日；"
                    f"允许上限为 {_MAX_COMPLETED_BAR_AGE_DAYS} 日以覆盖周末和三日休市周末。"
                )
                if latest_age_days is not None
                else "没有可用于 freshness 判断的完整日线。"
            ),
            ([f"{ticker}:last_completed_close"] if latest else []),
        )
    )

    if latest is not None:
        evidence.append(
            _evidence(
                ticker=ticker,
                domain="underlying_daily",
                metric="last_completed_close",
                value=latest["close"],
                unit="price",
                status="neutral",
                source=source,
                observed_at=latest_observed_at,
                fetched_at=fetched_at,
                observation_window="latest_completed_daily_bar",
            )
        )

    previous = bars[-2] if len(bars) >= 2 else None
    daily_return: Optional[float] = None
    if latest is not None and previous is not None and previous["close"] > 0:
        daily_return = (latest["close"] / previous["close"] - 1.0) * 100.0
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="underlying_daily",
            metric="completed_day_return",
            value=daily_return,
            unit="percent",
            status="neutral" if daily_return is not None else "unknown",
            source=source,
            observed_at=latest_observed_at,
            fetched_at=fetched_at,
            observation_window="latest_vs_previous_completed_close",
            quality_state="observed" if daily_return is not None else "missing",
        )
    )

    closes = [bar["close"] for bar in bars]
    ema8 = _ema_last(closes, 8)
    ema13 = _ema_last(closes, 13)
    trend_context = "unknown"
    trend_status = "unknown"
    if latest is not None and ema8 is not None and ema13 is not None:
        if latest["close"] > ema8 > ema13:
            trend_context = "bullish"
            trend_status = "supports"
            setup_tags.append("close_above_ema8_above_ema13")
        elif latest["close"] < ema8 < ema13:
            trend_context = "bearish"
            trend_status = "supports"
            setup_tags.append("close_below_ema8_below_ema13")
        else:
            trend_context = "mixed"
            trend_status = "neutral"
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="technical_structure",
            metric="ema8_ema13_alignment",
            value=(
                {"context": trend_context, "close": latest["close"], "ema8": ema8, "ema13": ema13}
                if latest is not None and ema8 is not None and ema13 is not None
                else None
            ),
            unit=None,
            status=trend_status,
            source=source,
            observed_at=latest_observed_at,
            fetched_at=fetched_at,
            observation_window="all_available_completed_bars_with_full_ema_seed",
            quality_state="derived" if trend_context != "unknown" else "missing",
            limitations=["EMA 是已完成日线的确定性派生值，不证明交易 edge。"],
        )
    )

    prior_20 = bars[-21:-1] if len(bars) >= 21 else []
    range_context = "unknown"
    range_status = "unknown"
    prior_high = max((bar["high"] for bar in prior_20 if bar["high"] is not None), default=None)
    prior_low = min((bar["low"] for bar in prior_20 if bar["low"] is not None), default=None)
    if latest is not None and prior_high is not None and prior_low is not None:
        if latest["close"] > prior_high:
            range_context = "breakout"
            range_status = "supports"
            setup_tags.append("daily_close_above_prior_20d_high")
        elif latest["close"] < prior_low:
            range_context = "breakdown"
            range_status = "supports"
            setup_tags.append("daily_close_below_prior_20d_low")
        else:
            range_context = "inside_range"
            range_status = "neutral"
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="technical_structure",
            metric="prior_20d_range_position",
            value=(
                {"context": range_context, "prior_high": prior_high, "prior_low": prior_low}
                if range_context != "unknown"
                else None
            ),
            unit=None,
            status=range_status,
            source=source,
            observed_at=latest_observed_at,
            fetched_at=fetched_at,
            observation_window="latest_completed_close_vs_prior_20_completed_sessions",
            quality_state="derived" if range_context != "unknown" else "missing",
        )
    )

    prior_volumes = [bar["volume"] for bar in prior_20 if bar["volume"] is not None and bar["volume"] >= 0]
    volume_ratio: Optional[float] = None
    if latest is not None and latest["volume"] is not None and prior_volumes:
        median_volume = statistics.median(prior_volumes)
        if median_volume > 0:
            volume_ratio = latest["volume"] / median_volume
    volume_status = "supports" if volume_ratio is not None and volume_ratio > 1.0 else "neutral"
    if volume_ratio is not None and volume_ratio > 1.0:
        setup_tags.append("volume_above_prior_20d_median")
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="underlying_activity",
            metric="volume_vs_prior_20d_median",
            value=volume_ratio,
            unit="ratio",
            status=volume_status if volume_ratio is not None else "unknown",
            source=source,
            observed_at=latest_observed_at,
            fetched_at=fetched_at,
            observation_window="latest_completed_volume_vs_prior_20_completed_sessions",
            quality_state="derived" if volume_ratio is not None else "missing",
        )
    )

    def _dollar_volume(bar: Mapping[str, Any]) -> tuple[Optional[float], str]:
        amount = _safe_float(bar.get("amount"))
        if amount is not None and amount > 0:
            # YfinanceFetcher standardises `amount` as close × volume rather
            # than an exchange-reported turnover field.  Preserve that
            # distinction even though the normalised row is non-empty.
            if "yfinance" in source.lower():
                return amount, "derived"
            return amount, "observed"
        volume = _safe_float(bar.get("volume"))
        close = _safe_float(bar.get("close"))
        if volume is not None and volume >= 0 and close is not None:
            return close * volume, "derived"
        return None, "missing"

    latest_dollar, latest_dollar_quality = _dollar_volume(latest or {})
    prior_dollar_inputs = [_dollar_volume(bar) for bar in prior_20]
    prior_dollars = [value for value, _ in prior_dollar_inputs if value is not None]
    dollar_ratio: Optional[float] = None
    if latest_dollar is not None and prior_dollars:
        median_dollar = statistics.median(prior_dollars)
        if median_dollar > 0:
            dollar_ratio = latest_dollar / median_dollar
    dollar_input_is_proxy = latest_dollar_quality == "derived" or any(
        quality == "derived"
        for value, quality in prior_dollar_inputs
        if value is not None
    )
    dollar_status = (
        "supports"
        if dollar_ratio is not None and dollar_ratio > 1.0 and not dollar_input_is_proxy
        else "neutral"
    )
    if dollar_ratio is not None and dollar_ratio > 1.0 and not dollar_input_is_proxy:
        setup_tags.append("dollar_volume_above_prior_20d_median")
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="underlying_activity",
            metric="dollar_volume_vs_prior_20d_median",
            value=dollar_ratio,
            unit="ratio",
            status=dollar_status if dollar_ratio is not None else "unknown",
            source=source,
            observed_at=latest_observed_at,
            fetched_at=fetched_at,
            observation_window="latest_completed_dollar_volume_vs_prior_20_completed_sessions",
            # A ratio is always derived, even when its amount inputs were
            # exchange/provider reported.
            quality_state="derived" if dollar_ratio is not None else "missing",
            limitations=(
                [
                    (
                        "YfinanceFetcher 的 amount 是 close × volume 派生值，不是交易所报告成交额。"
                        if "yfinance" in source.lower()
                        else "数据源未提供 amount，使用 close × volume 作为成交额代理。"
                    )
                ]
                if dollar_input_is_proxy
                else []
            ),
        )
    )

    regime_value, regime_status, regime_label, regime_quality = _regime_details(regime)
    regime_generated_at = _iso(regime.get("generated_at")) if regime and isinstance(regime.get("generated_at"), datetime) else None
    evidence.append(
        _evidence(
            ticker=ticker,
            domain="market_regime",
            metric="stored_regime",
            value=regime_value,
            unit=None,
            status=regime_status,
            source="regime_scores",
            observed_at=(str(regime.get("date")) if regime and regime.get("date") is not None else None),
            fetched_at=_iso(as_of_utc),
            observation_window="current_market_date",
            quality_state=(
                "observed"
                if regime_quality == "ready"
                else "degraded"
                if regime_quality == "degraded"
                else "missing"
            ),
            limitations=["Regime 是版本化市场上下文，不单独证明个股机会。"],
        )
    )

    if regime and regime_quality == "ready":
        hard_gates.extend(
            [
                _gate("current_regime_available", "passed", f"已读取 {regime_label} Regime。", [f"{ticker}:stored_regime"]),
                _gate(
                    "regime_allows_new_risk",
                    "failed" if regime_label == "no_trade" else "passed",
                    (
                        "当前存储 Regime 为 no_trade；候选仅保留观察，不升级为研究就绪。"
                        if regime_label == "no_trade"
                        else f"当前存储 Regime 为 {regime_label}。"
                    ),
                    [f"{ticker}:stored_regime"],
                ),
            ]
        )
    elif regime:
        quality_label = "数据降级" if regime_quality == "degraded" else "核心输入不可用"
        hard_gates.extend(
            [
                _gate(
                    "current_regime_available",
                    "unknown",
                    f"已存储 Regime {quality_label}，不作为权威市场门禁。",
                ),
                _gate(
                    "regime_allows_new_risk",
                    "unknown",
                    "Regime 输入质量未通过，不能据其数值判断是否允许新增风险。",
                ),
            ]
        )
        unknowns.append(
            f"当日 Regime {quality_label}；基础量价结构仍可查看，但市场风险门禁保持未知。"
        )
    else:
        hard_gates.extend(
            [
                _gate("current_regime_available", "unknown", "当前交易日没有已存储 Regime。"),
                _gate("regime_allows_new_risk", "unknown", "缺少当前 Regime，不能判断市场环境门禁。"),
            ]
        )
        unknowns.append("当前交易日没有已存储 Regime，候选只能进入观察队列。")

    hard_gates.append(
        _gate(
            "confirmed_playbook_available",
            "unknown",
            "尚无用户确认并版本化的 Playbook；第一阶段不执行个人风格硬门禁。",
        )
    )

    trend_direction = trend_context if trend_context in {"bullish", "bearish"} else None
    range_direction = (
        "bullish"
        if range_context == "breakout"
        else "bearish"
        if range_context == "breakdown"
        else None
    )
    structure_directions = {
        direction
        for direction in (trend_direction, range_direction)
        if direction is not None
    }
    structure_conflict = len(structure_directions) > 1
    coherent_structure = bool(structure_directions) and not structure_conflict
    structure_refs = [
        f"{ticker}:ema8_ema13_alignment",
        f"{ticker}:prior_20d_range_position",
    ]
    structure_support_count = int(trend_status == "supports") + int(
        range_status == "supports"
    )

    activity_refs = [
        f"{ticker}:volume_vs_prior_20d_median",
        f"{ticker}:dollar_volume_vs_prior_20d_median",
    ]
    activity_support_count = int(volume_status == "supports") + int(
        dollar_status == "supports"
    )
    activity_observed = volume_ratio is not None or dollar_ratio is not None

    if structure_conflict:
        directional_context = "mixed"
    elif range_direction is not None:
        directional_context = range_direction
    elif trend_direction is not None:
        directional_context = trend_direction
    else:
        directional_context = "mixed" if latest is not None else "unknown"

    hard_gates.extend(
        [
            _gate(
                "directional_structure_confirmed",
                (
                    "passed"
                    if coherent_structure
                    else "failed"
                    if structure_conflict or latest is not None
                    else "unknown"
                ),
                (
                    f"已完成日线提供 {directional_context} 方向结构，"
                    f"{structure_support_count} 项结构证据支持。"
                    if coherent_structure
                    else "EMA 与 20 日区间给出相反方向，结构冲突，只能等待进一步确认。"
                    if structure_conflict
                    else "已完成日线尚未形成 EMA 排列或 20 日突破/跌破方向结构。"
                    if latest is not None
                    else "没有可用于方向结构判断的已完成日线。"
                ),
                structure_refs,
            ),
            _gate(
                "underlying_activity_confirmed",
                (
                    "passed"
                    if activity_support_count > 0
                    else "failed"
                    if activity_observed
                    else "unknown"
                ),
                (
                    f"{activity_support_count} 项独立量能/成交额证据高于此前 20 日中位数。"
                    if activity_support_count > 0
                    else "量能/成交额数据可用，但没有独立指标高于此前 20 日中位数。"
                    if activity_observed
                    else "量能/成交额数据不足，无法确认价格结构。"
                ),
                activity_refs,
            ),
        ]
    )

    # ``research_ready`` means the deterministic evidence packet is worth
    # opening for further research.  It is deliberately not an entry signal,
    # probability claim, personal-Playbook match, or permission to trade.
    if not supported_universe:
        research_state = "blocked"
        research_state_reason = "标的不在当前美股期权 underlying 研究范围内。"
    elif not history_ready:
        research_state = "blocked"
        research_state_reason = (
            "完整日线不足 21 根，核心结构和量能窗口无法可靠计算。"
        )
    elif not history_fresh:
        research_state = "context_only"
        research_state_reason = "最新完整日线超过新鲜度上限，仅保留历史背景。"
    elif regime and regime_quality == "ready" and regime_label == "no_trade":
        research_state = "context_only"
        research_state_reason = (
            "权威 Regime 为 no_trade；即使个股结构存在，也只保留背景观察。"
        )
    elif coherent_structure and activity_support_count > 0:
        research_state = "research_ready"
        research_state_reason = (
            f"已完成日线与新鲜度门禁通过，{directional_context} 结构和"
            "独立量能/成交额确认同时成立；可进入重点研究，但不是交易指令。"
        )
    elif coherent_structure or activity_support_count > 0:
        research_state = "watch_only"
        research_state_reason = (
            "结构与量能尚未同时确认，或方向证据仍有冲突；保留观察，等待另一侧证据。"
        )
    else:
        research_state = "context_only"
        research_state_reason = (
            "方向结构为混合/未形成，且没有独立量能或成交额支持，仅保留市场背景。"
        )

    hard_gates.append(
        _gate(
            "research_evidence_ready",
            (
                "passed"
                if research_state == "research_ready"
                else "unknown"
                if research_state == "watch_only"
                else "failed"
            ),
            research_state_reason,
            structure_refs + activity_refs,
        )
    )

    available_count = sum(1 for item in evidence if item["value"] is not None)
    if available_count >= _CORE_EVIDENCE_COUNT:
        completeness_state = "complete"
    elif available_count > 0:
        completeness_state = "partial"
    else:
        completeness_state = "insufficient"

    if history_ready and history_fresh:
        history_state = "ready"
    elif history_ready:
        history_state = "stale"
    else:
        history_state = "partial" if bars else "unavailable"
    history_message = (
        f"{len(bars)} 根已完成日线，最新为 {latest['date'].isoformat()}."
        if latest
        else (history.error or "没有可用的已完成日线。")
    )
    readiness = [
        _readiness(
            "universe",
            "ready" if supported_universe else "unavailable",
            source="us_option_underlying_format_v1",
            as_of=_iso(as_of_utc),
            actionability="research_scope",
            message=(
                "第一阶段支持美股期权 underlying；实际期权可用性尚待后续期权链验证。"
                if supported_universe
                else "非美股标的不在第一阶段机会扫描范围内。"
            ),
        ),
        _readiness(
            "daily_history",
            history_state,
            source=history.source,
            as_of=latest_observed_at,
            actionability="research_input",
            message=history_message,
        ),
        _readiness(
            "regime",
            (
                "ready"
                if regime and regime_quality == "ready"
                else "partial"
                if regime and regime_quality == "degraded"
                else "unavailable"
            ),
            source="regime_scores" if regime else None,
            as_of=regime_generated_at or (str(regime.get("date")) if regime else None),
            actionability="research_context",
            message=(
                f"已读取 {regime_label} Regime。"
                if regime and regime_quality == "ready"
                else "已存储 Regime 的部分输入缺失，仅作临时背景。"
                if regime and regime_quality == "degraded"
                else "已存储 Regime 的 SPY/VIX 核心输入不完整，不作为市场门禁。"
                if regime
                else "当前交易日没有已存储 Regime。"
            ),
        ),
        _readiness(
            "options_flow",
            "not_configured",
            source=None,
            as_of=None,
            actionability="not_available",
            message=(
                "候选排名尚未接入完整逐笔期权流与 NBBO；"
                "独立 Moomoo 异常事件面板不等于方向或开平仓证据。"
            ),
        ),
        _readiness(
            "dark_pool",
            "not_configured",
            source=None,
            as_of=None,
            actionability="background_only",
            message="未加载 FINRA ATS 延迟周报；该类数据即使接入也只能作为延迟背景。",
        ),
    ]

    supporting_count = sum(1 for item in evidence if item["status"] == "supports")
    candidate_hash = hashlib.sha256(f"{run_id}|{ticker}".encode("utf-8")).hexdigest()[:16]
    return {
        "candidate_id": f"opc_{candidate_hash}",
        "ticker": ticker,
        "research_state": research_state,
        "directional_context": directional_context,
        "setup_tags": sorted(set(setup_tags)),
        "last_completed_bar_at": latest_observed_at,
        # Keep the validation anchor explicit.  Downstream snapshot code must
        # never reverse-engineer it from a derived EMA evidence payload.
        "reference_session_date": latest["date"].isoformat() if latest else None,
        "reference_close": latest["close"] if latest else None,
        "reference_price_basis": "prior_completed_close",
        "source": history.source,
        "style_match": {
            "status": "unknown",
            "source": "unverified",
            "matched_rules": [],
            "conflicting_rules": [],
            "unknown_fields": [
                "setup",
                "direction",
                "strategy_structure",
                "dte",
                "delta",
                "entry_session",
                "holding_horizon",
                "event_policy",
                "liquidity_rules",
            ],
        },
        "hard_gates": hard_gates,
        "evidence": evidence,
        "readiness": readiness,
        "supporting_evidence_count": supporting_count,
        "data_completeness": {
            "state": completeness_state,
            "available_count": available_count,
            "expected_count": _CORE_EVIDENCE_COUNT,
        },
        "unknowns": unknowns,
    }


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    style = candidate.get("style_match") or {}
    completeness = candidate.get("data_completeness") or {}
    return (
        _RESEARCH_STATE_ORDER.get(str(candidate.get("research_state")), 99),
        _STYLE_MATCH_ORDER.get(str(style.get("status")), 99),
        -int(candidate.get("supporting_evidence_count") or 0),
        _COMPLETENESS_ORDER.get(str(completeness.get("state")), 99),
        str(candidate.get("ticker") or ""),
    )


def build_daily_opportunity_run(
    *,
    symbols: Sequence[str],
    histories: Mapping[str, DailyHistoryInput],
    regime: Optional[Mapping[str, Any]],
    as_of: datetime,
    limit: int,
) -> dict[str, Any]:
    """Build a deterministic daily opportunity research run from injected data."""

    normalized = normalize_symbols(symbols)
    if not 1 <= limit <= 15:
        raise ValueError("limit must be between 1 and 15")
    if len(normalized) > 20:
        raise ValueError("symbols must contain at most 20 unique values")

    as_of_utc = _as_aware_utc(as_of)
    market_date = as_of_utc.astimezone(_NEW_YORK).date()
    regime_quality = _regime_quality_state(regime)
    fingerprint = "|".join(
        [SIGNAL_VERSION, as_of_utc.isoformat(), *normalized]
    )
    run_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    run_id = f"opr_{market_date.isoformat()}_{run_hash}"

    all_candidates = [
        _build_candidate(
            ticker,
            histories.get(ticker, DailyHistoryInput(bars=(), error="loader result missing")),
            regime,
            as_of=as_of_utc,
            run_id=run_id,
        )
        for ticker in normalized
    ]
    all_candidates.sort(key=_candidate_sort_key)

    history_states = {
        item["state"]
        for candidate in all_candidates
        for item in candidate["readiness"]
        if item["domain"] == "daily_history"
    }
    if history_states == {"ready"}:
        daily_state = "ready"
    elif history_states == {"stale"}:
        daily_state = "stale"
    elif "ready" in history_states or "partial" in history_states:
        daily_state = "partial"
    elif "stale" in history_states:
        daily_state = "partial"
    else:
        daily_state = "unavailable"

    candidates = all_candidates[:limit]

    run_readiness = [
        _readiness(
            "daily_history",
            daily_state,
            source="per_candidate",
            as_of=as_of_utc.isoformat(),
            actionability="research_input",
            message=(
                "候选严格只使用 market_date_et 之前的完整日线（T-1 或更早）；"
                "即使收盘后运行也不纳入当日日线，每个标的保留独立来源和失败状态。"
            ),
        ),
        _readiness(
            "regime",
            (
                "ready"
                if regime and regime_quality == "ready"
                else "partial"
                if regime and regime_quality == "degraded"
                else "unavailable"
            ),
            source="regime_scores" if regime else None,
            as_of=(str(regime.get("date")) if regime else None),
            actionability="research_context",
            message=(
                "读取当前交易日已存储 Regime，不在本接口重算或写入。"
                if regime and regime_quality == "ready"
                else "已存储 Regime 的部分输入缺失，仅作临时市场背景。"
                if regime and regime_quality == "degraded"
                else "已存储 Regime 的 SPY/VIX 核心输入不完整，不作为市场门禁。"
                if regime
                else "当前交易日没有已存储 Regime。"
            ),
        ),
        _readiness(
            "options_flow",
            "not_configured",
            source=None,
            as_of=None,
            actionability="not_available",
            message=(
                "候选排名尚未接入完整逐笔期权流与 NBBO；"
                "右侧 Moomoo 异常事件仅作独立研究上下文。"
            ),
        ),
        _readiness(
            "dark_pool",
            "not_configured",
            source=None,
            as_of=None,
            actionability="background_only",
            message="第一阶段未加载延迟 ATS 周报，且该域不得伪装成实时暗池信号。",
        ),
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_type": "morning_prior_close",
        "market_date_et": market_date.isoformat(),
        "as_of": as_of_utc.isoformat(),
        "generated_at": as_of_utc.isoformat(),
        "signal_version": SIGNAL_VERSION,
        "ranking_method": "rule_based_evidence_count",
        "strategy_validation_state": "not_validated",
        "strategy_validation_message": (
            "当前排序是确定性研究队列，不是胜率或预期收益模型；"
            "尚未积累冻结信号后的 5 日/20 日 MFE、MAE 与相对 SPY 结果。"
        ),
        "universe": normalized,
        "requested_limit": limit,
        "candidate_count": len(candidates),
        "run_readiness": run_readiness,
        "candidates": candidates,
    }
