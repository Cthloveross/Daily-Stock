# -*- coding: utf-8 -*-
"""首根完整 5 分钟 K 线的开盘异动画像（纯函数、无取数）。

这个模块只回答一个窄问题：09:30--09:35 ET 的第一根常规时段 K 线，
相对该标的过去 20 个交易日的**同一时间槽**是否异常。它是开盘价格发现的
候选探测器，不是入场信号；后续是否突破 opening range、期权报价能否执行，
由下游独立判断。

为什么不用「上一日全部 5m K 线中位数」：成交量、波动率与点差都有稳定的
日内季节性，开盘天然高于午间。把开盘量与全天普通时段比较会系统性制造
假阳性，因此本模块只做同钟（09:30--09:35）历史比较。
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from src.opportunities.intraday_bursts import filter_regular_session_bars

_NEW_YORK = ZoneInfo("America/New_York")

OPENING_IMPULSE_RULE_VERSION = "opening_impulse_v1"
OPENING_IMPULSE_BASIS = (
    "first_completed_0930_0935_et_bar_vs_prior_20_same_slot;"
    "range_atr_gte_0.40;body_atr_gte_0.30;body_efficiency_gte_0.60;"
    "directional_close_location_gte_0.68;volume_rvol_gte_1.00;"
    "volume_percentile_gte_0.80;range_percentile_gte_0.90"
)
OPENING_REFERENCE_SESSIONS = 20
OPENING_BAR_MINUTES = 5
OPENING_BAR_START_MINUTES_ET = 9 * 60 + 30
OPENING_BAR_END_MINUTES_ET = 9 * 60 + 35

# 这些是基于专业做法与本地小样本回放得到的**初始研究门**，不是论文证明的
# 最优参数。更改时必须重跑时间有序回放并提升 rule version。
OPENING_RANGE_ATR_MIN = 0.40
OPENING_BODY_ATR_MIN = 0.30
OPENING_BODY_EFFICIENCY_MIN = 0.60
OPENING_DIRECTIONAL_CLOSE_LOCATION_MIN = 0.68
OPENING_VOLUME_RVOL_MIN = 1.00
OPENING_VOLUME_PERCENTILE_MIN = 0.80
OPENING_RANGE_PERCENTILE_MIN = 0.90

OPENING_IMPULSE_LIMITATIONS = (
    "第一根 5 分钟线只用于识别开盘异动候选，不证明后续延续，也不是 Call/Put 入场信号。",
    "历史基准只使用目标日之前 20 个交易日的 09:30--09:35 ET 同一时间槽，避免开盘 U 型季节性造成假阳性。",
    "当前阈值是研究起点：在 2026-08-03--2026-08-31 的 15 标的小样本中严格级约 0.57 次/日；样本小且看过 TSLA，不能视为已验证 alpha。",
    "K 线为供应商 5 分钟聚合；历史数据可能事后修订，实时到达延迟无法由收盘后回放复原。",
)

__all__ = [
    "OPENING_IMPULSE_BASIS",
    "OPENING_IMPULSE_RULE_VERSION",
    "OPENING_REFERENCE_SESSIONS",
    "compute_opening_impulse_profile",
    "unavailable_opening_impulse_profile",
]


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _aware_et(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(_NEW_YORK)


def _percentile_rank(values: Sequence[float], current: float) -> Optional[float]:
    cleaned = [float(value) for value in values if value is not None and value >= 0]
    if not cleaned:
        return None
    return round(sum(value <= current for value in cleaned) / len(cleaned), 6)


def unavailable_opening_impulse_profile(
    reason: str,
    *,
    state: str = "unavailable",
    reference_session_count: int = 0,
) -> dict[str, Any]:
    """返回显式标缺/待收盘画像；绝不把缺输入回填成未触发。"""

    return {
        "state": state,
        "alert_qualified": False,
        "rule_version": OPENING_IMPULSE_RULE_VERSION,
        "bar_start_et": "09:30",
        "bar_end_et": "09:35",
        "completed_as_of": None,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": None,
        "dollar_volume": None,
        "direction": None,
        "body_percent": None,
        "body_atr": None,
        "range_atr": None,
        "body_efficiency": None,
        "directional_close_location": None,
        "opening_volume_rvol_20d": None,
        "opening_range_ratio_20d": None,
        "opening_body_ratio_20d": None,
        "volume_percentile_20d": None,
        "range_percentile_20d": None,
        "body_percentile_20d": None,
        "reference_session_count": reference_session_count,
        "reference_median_volume": None,
        "reference_median_range": None,
        "reference_median_abs_body": None,
        "prior_close": None,
        "gap_percent": None,
        "closed_vs_prior_percent": None,
        "reclaimed_prior_close": None,
        "opening_mid": None,
        "quality_score": None,
        "failed_checks": [],
        "thresholds": {
            "reference_sessions": OPENING_REFERENCE_SESSIONS,
            "range_atr_min": OPENING_RANGE_ATR_MIN,
            "body_atr_min": OPENING_BODY_ATR_MIN,
            "body_efficiency_min": OPENING_BODY_EFFICIENCY_MIN,
            "directional_close_location_min": OPENING_DIRECTIONAL_CLOSE_LOCATION_MIN,
            "volume_rvol_min": OPENING_VOLUME_RVOL_MIN,
            "volume_percentile_min": OPENING_VOLUME_PERCENTILE_MIN,
            "range_percentile_min": OPENING_RANGE_PERCENTILE_MIN,
        },
        "unavailable_reason": reason,
        "basis": OPENING_IMPULSE_BASIS,
        "limitations": list(OPENING_IMPULSE_LIMITATIONS),
    }


def _opening_rows(
    bars: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """每个 ET 交易日只保留 09:30 开始的第一根，重复行以后到者覆盖。"""

    rows: dict[str, dict[str, Any]] = {}
    for row in filter_regular_session_bars(bars):
        start = row["start_et"]
        if start.hour * 60 + start.minute != OPENING_BAR_START_MINUTES_ET:
            continue
        rows[row["session_date_et"]] = row
    return rows


def compute_opening_impulse_profile(
    current_bars: Sequence[Mapping[str, Any]],
    *,
    reference_bars: Sequence[Mapping[str, Any]],
    market_date_et: str,
    quote_session_scope: str,
    as_of: Any,
    atr14: Any,
    prior_close: Any = None,
) -> dict[str, Any]:
    """计算首根完整 5m 开盘异动画像，所有历史比较严格排除目标日。

    ``current_bars`` 是短窗口实时读取；``reference_bars`` 是盘前预热并按日
    缓存的长历史。两者都必须已经过供应商 bar-label 归一化，时间戳表示 bar
    **开始**。即使供应商提前暴露正在形成的 09:30 bar，本函数也会在
    ``as_of < 09:35 ET`` 时返回 ``pending``，杜绝偷看未完结 K 线。
    """

    if quote_session_scope != "current_session":
        return unavailable_opening_impulse_profile("not_current_session")
    observed_at = _aware_et(as_of)
    if observed_at is None:
        return unavailable_opening_impulse_profile("as_of_unparseable_or_naive")
    if observed_at.date().isoformat() != market_date_et:
        return unavailable_opening_impulse_profile("as_of_market_date_mismatch")

    first_bar_end = observed_at.replace(
        hour=9, minute=35, second=0, microsecond=0
    )
    if observed_at < first_bar_end:
        result = unavailable_opening_impulse_profile(
            "first_5m_bar_not_completed", state="pending"
        )
        result["completed_as_of"] = observed_at.isoformat()
        return result

    current_by_date = _opening_rows(current_bars)
    opening = current_by_date.get(market_date_et)
    if opening is None:
        result = unavailable_opening_impulse_profile(
            "completed_first_5m_bar_not_available", state="pending"
        )
        result["completed_as_of"] = observed_at.isoformat()
        return result

    # 长历史与短窗口可能重叠。先装长历史、再让短窗口覆盖；之后严格只用
    # date < target 的行，目标日与任何未来日期不可能进入分母。
    history_by_date = _opening_rows(reference_bars)
    history_by_date.update(
        {
            day: row
            for day, row in current_by_date.items()
            if day < market_date_et
        }
    )
    prior_dates = sorted(day for day in history_by_date if day < market_date_et)
    selected_dates = prior_dates[-OPENING_REFERENCE_SESSIONS:]
    references = [history_by_date[day] for day in selected_dates]
    if len(references) < OPENING_REFERENCE_SESSIONS:
        result = unavailable_opening_impulse_profile(
            "fewer_than_20_prior_opening_slots",
            reference_session_count=len(references),
        )
        result["completed_as_of"] = observed_at.isoformat()
        return result

    atr = _finite(atr14)
    if atr is None or atr <= 0:
        result = unavailable_opening_impulse_profile(
            "atr14_unavailable",
            reference_session_count=len(references),
        )
        result["completed_as_of"] = observed_at.isoformat()
        return result

    open_ = float(opening["open"])
    high = float(opening["high"])
    low = float(opening["low"])
    close = float(opening["close"])
    volume = _finite(opening.get("volume"))
    bar_range = high - low
    body = close - open_
    if volume is None or volume <= 0:
        return unavailable_opening_impulse_profile(
            "opening_volume_unavailable",
            reference_session_count=len(references),
        )
    if bar_range <= 0:
        return unavailable_opening_impulse_profile(
            "opening_high_equals_low",
            reference_session_count=len(references),
        )

    reference_volumes = [
        float(row["volume"])
        for row in references
        if _finite(row.get("volume")) is not None
        and float(row["volume"]) > 0
    ]
    reference_ranges = [float(row["high"] - row["low"]) for row in references]
    reference_bodies = [
        abs(float(row["close"] - row["open"])) for row in references
    ]
    if len(reference_volumes) < OPENING_REFERENCE_SESSIONS:
        return unavailable_opening_impulse_profile(
            "historical_opening_volume_incomplete",
            reference_session_count=len(reference_volumes),
        )

    median_volume = float(statistics.median(reference_volumes))
    median_range = float(statistics.median(reference_ranges))
    median_body = float(statistics.median(reference_bodies))
    if median_volume <= 0 or median_range <= 0 or median_body <= 0:
        return unavailable_opening_impulse_profile(
            "historical_opening_denominator_nonpositive",
            reference_session_count=len(references),
        )

    if body > 0:
        direction = "up"
        directional_close_location = (close - low) / bar_range
    elif body < 0:
        direction = "down"
        directional_close_location = (high - close) / bar_range
    else:
        direction = "flat"
        directional_close_location = 0.5

    abs_body = abs(body)
    body_atr = abs_body / atr
    range_atr = bar_range / atr
    body_efficiency = abs_body / bar_range
    volume_rvol = volume / median_volume
    range_ratio = bar_range / median_range
    body_ratio = abs_body / median_body
    volume_percentile = _percentile_rank(reference_volumes, volume)
    range_percentile = _percentile_rank(reference_ranges, bar_range)
    body_percentile = _percentile_rank(reference_bodies, abs_body)

    failed_checks: list[str] = []
    checks = (
        (direction in {"up", "down"}, "opening_body_is_flat"),
        (range_atr >= OPENING_RANGE_ATR_MIN, "range_atr_below_0.40"),
        (body_atr >= OPENING_BODY_ATR_MIN, "body_atr_below_0.30"),
        (
            body_efficiency >= OPENING_BODY_EFFICIENCY_MIN,
            "body_efficiency_below_0.60",
        ),
        (
            directional_close_location
            >= OPENING_DIRECTIONAL_CLOSE_LOCATION_MIN,
            "directional_close_location_below_0.68",
        ),
        (volume_rvol >= OPENING_VOLUME_RVOL_MIN, "opening_rvol_below_1.00"),
        (
            volume_percentile is not None
            and volume_percentile >= OPENING_VOLUME_PERCENTILE_MIN,
            "opening_volume_percentile_below_0.80",
        ),
        (
            range_percentile is not None
            and range_percentile >= OPENING_RANGE_PERCENTILE_MIN,
            "opening_range_percentile_below_0.90",
        ),
    )
    for passed, reason in checks:
        if not passed:
            failed_checks.append(reason)

    previous_close = _finite(prior_close)
    gap_percent = None
    closed_vs_prior_percent = None
    reclaimed_prior_close = None
    if previous_close is not None and previous_close > 0:
        gap_percent = (open_ / previous_close - 1.0) * 100.0
        closed_vs_prior_percent = (close / previous_close - 1.0) * 100.0
        reclaimed_prior_close = (
            (open_ < previous_close <= close)
            if direction == "up"
            else (open_ > previous_close >= close)
            if direction == "down"
            else False
        )

    # 仅用于同批多候选的确定性提示排序，不是概率或预期收益。
    quality_score = (
        range_atr
        + body_atr
        + body_efficiency
        + directional_close_location
        + (volume_percentile or 0.0)
        + (range_percentile or 0.0)
    )
    result = unavailable_opening_impulse_profile(
        "not_qualified" if failed_checks else "qualified",
        state="not_qualified" if failed_checks else "qualified",
        reference_session_count=len(references),
    )
    result.update(
        alert_qualified=not failed_checks,
        completed_as_of=observed_at.isoformat(),
        open=round(open_, 6),
        high=round(high, 6),
        low=round(low, 6),
        close=round(close, 6),
        volume=round(volume, 6),
        dollar_volume=round(close * volume, 2),
        direction=direction,
        body_percent=round(body / open_ * 100.0, 6),
        body_atr=round(body_atr, 6),
        range_atr=round(range_atr, 6),
        body_efficiency=round(body_efficiency, 6),
        directional_close_location=round(directional_close_location, 6),
        opening_volume_rvol_20d=round(volume_rvol, 6),
        opening_range_ratio_20d=round(range_ratio, 6),
        opening_body_ratio_20d=round(body_ratio, 6),
        volume_percentile_20d=volume_percentile,
        range_percentile_20d=range_percentile,
        body_percentile_20d=body_percentile,
        reference_median_volume=round(median_volume, 6),
        reference_median_range=round(median_range, 6),
        reference_median_abs_body=round(median_body, 6),
        prior_close=(round(previous_close, 6) if previous_close is not None else None),
        gap_percent=(round(gap_percent, 6) if gap_percent is not None else None),
        closed_vs_prior_percent=(
            round(closed_vs_prior_percent, 6)
            if closed_vs_prior_percent is not None
            else None
        ),
        reclaimed_prior_close=reclaimed_prior_close,
        opening_mid=round((high + low) / 2.0, 6),
        quality_score=round(quality_score, 6),
        failed_checks=failed_checks,
        unavailable_reason=None if not failed_checks else "thresholds_not_met",
    )
    return result
