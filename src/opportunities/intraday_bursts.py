# -*- coding: utf-8 -*-
"""Pure rolling 15-minute momentum-burst (波段爆发) detection on 5m bars.

This module never fetches data.  Callers inject a symbol's raw 5m bars (the
same rows the ``/stocks/{code}/history?period=5m`` loader returns, ``date`` as
timezone-aware ISO 8601 strings) and get back a deterministic per-session
burst profile:

- **normalisation medians**: ``median_bar_range`` = median(high−low) and
  ``median_bar_volume`` = median(volume) over the current session's bars so
  far; when fewer than :data:`MEDIAN_FALLBACK_MIN_BARS` bars exist today the
  prior session's bars supply both medians (explicit ``median_basis`` label);
- **rolling window**: for each 3-bar (15-minute) window,
  ``thrust_norm = |close_end − open_start| / median_bar_range``,
  ``vol_norm = window volume / (3 × median_bar_volume)``,
  ``burst_score = thrust_norm × vol_norm``, direction = sign of the thrust;
- **current burst** = the latest window; **session legs** = the top distinct
  windows (score ≥ :data:`LEG_MIN_SCORE`, window starts ≥
  :data:`LEG_MIN_GAP_MINUTES` apart, at most :data:`LEG_MAX_COUNT`).

Calibration basis (2026-07-31 regular session, legs labelled by the user,
bars captured from the real history loader into
``tests/fixtures/intraday_5m_2026-07-31_regular.json``):

===========================  =========  ======================================
labelled leg                 score      note
===========================  =========  ======================================
MU    09:45 plunge (down)      35.5     −5.2% in 15min @ 4.6× volume
AMZN  09:30 opening wave (up)  18.7     @ 5.6× volume
NVDA  09:40 wave (down)         9.3     first of two labelled waves
NVDA  15:15 wave (up)           8.6     late-day second wave
GOOGL best window (09:30)      15.2     v1 failure case: full-session
                                        aggregates ranked GOOGL first even
                                        though its best 15min burst is far
                                        below MU's plunge
===========================  =========  ======================================

``LEG_MIN_SCORE = 8.0`` is the largest round number every labelled leg still
clears (binding constraint: NVDA 15:15 at 8.6).  ``BURST_SUPPORT_MIN = 6.0``
sits one notch below so a developing burst flags "supports" up to one bar
before it fully clears the leg bar (NVDA's 09:35 run-up window scored 6.3).
Both are documented v2 heuristics for research prioritisation — not validated
edges, not signals; changing either must bump the intraday-top
``signal_version``.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

_NEW_YORK = ZoneInfo("America/New_York")

# 滚动窗口：3 根 5 分钟 K 线 = 15 分钟。
BURST_WINDOW_BARS = 3
BURST_BAR_MINUTES = 5
BURST_WINDOW_MINUTES = BURST_WINDOW_BARS * BURST_BAR_MINUTES
# 当日 K 线不足 6 根时（开盘前 30 分钟内），中位数基准回退到上一交易时段，
# 避免用 2-3 根开盘 K 线自证归一化分母。
MEDIAN_FALLBACK_MIN_BARS = 6
# 记为「强波段」的最低 burst_score（校准依据见模块 docstring 表格：
# 2026-07-31 用户标注的暴动波全部 ≥8.56）。
LEG_MIN_SCORE = 8.0
# 记为「中波段」的最低 burst_score。v2 分级校准：2026-08-03 上午 NVDA
# 09:55→10:25 的真实可交易推升（用户实时指认「这一波应该有记录」）峰值
# 5.03、持续段 2.5-3.1，而同日无波时段窗口 <1.1 —— 2.5 收录该波全程且
# 不触及静默期。中波段只是记录与展示分级，不改变 supports 口径。
LEG_MEDIUM_MIN_SCORE = 2.5
# 当前窗口记为 supports 的最低 burst_score（比 LEG_MIN_SCORE 低一档，
# 让正在发展中的爆发提前一根 K 线亮起）。
BURST_SUPPORT_MIN = 6.0
# 两个独立波段的窗口起点至少相隔 30 分钟，否则视为同一波并合并。
LEG_MIN_GAP_MINUTES = 30
# 单一交易时段最多报告 4 个波段（强弱合计，强波段优先保留）。
LEG_MAX_COUNT = 4

BURST_BASIS = "rolling_15m_thrust_over_median_range_times_volume_ratio"
MEDIAN_BASIS_CURRENT = "current_session_bars_so_far"
MEDIAN_BASIS_PRIOR = "prior_session_fallback"

# v3 速度分级：相邻两个滚动 15 分钟窗口的爆发分之差。用户纪律（Playbook
# 候选 R1）是「日内只交易加速；2 分钟级速度的波在 1 分钟速度熄火时离场」——
# 本仓库的 K 线是 5 分钟粒度，诚实 v1 只能给出 5m 窗口级的加速/减速近似，
# 不是 1m/2m 秒级速度；减速标签是用户自己的离场提示，不是系统信号。
SPEED_BASIS = "consecutive_rolling_15m_window_burst_score_delta_5m_bars"

BURST_LIMITATIONS = (
    "波段爆发＝15 分钟推力（|收−开| ÷ 当日 5 分钟 K 线波幅中位数）×量比"
    "（窗口量 ÷ 3×5 分钟量中位数）；强波段阈值（≥8.0）按 2026-07-31 用户"
    "标注暴动样本校准、中波段阈值（≥2.5）按 2026-08-03 NVDA 上午持续推升"
    "校准，是确定性研究度量，不是买卖信号。",
    "仅统计正股 09:30–16:00 ET 常规时段 5 分钟 K 线；盘前盘后不参与。",
    "开盘前 30 分钟（当日不足 6 根 K 线）中位数基准回退上一交易时段并显式标注。",
    "速度分级＝相邻两个 15 分钟窗口爆发分之差（5m K 线近似，非 1m/2m 秒级速度）；"
    "「减速」对应用户自身纪律 R1 的离场提示，不是系统买卖信号。",
)


def compute_speed_state(
    windows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """v3 speed grade from the last two rolling windows, fail-closed.

    ``accelerating`` = latest window score > previous window score,
    ``decelerating`` = latest < previous, ``flat`` = equal.  Fewer than two
    windows or a missing score on either side is ``unknown`` with an explicit
    reason — never a fabricated ``flat``.
    """

    if len(windows) < 2:
        return {
            "state": "unknown",
            "current_score": None,
            "previous_score": None,
            "delta": None,
            "basis": SPEED_BASIS,
            "unavailable_reason": "fewer_than_2_windows",
        }
    current = _finite(windows[-1].get("score"))
    previous = _finite(windows[-2].get("score"))
    if current is None or previous is None:
        return {
            "state": "unknown",
            "current_score": current,
            "previous_score": previous,
            "delta": None,
            "basis": SPEED_BASIS,
            "unavailable_reason": "missing_window_score",
        }
    delta = round(current - previous, 6)
    if delta > 0:
        state = "accelerating"
    elif delta < 0:
        state = "decelerating"
    else:
        state = "flat"
    return {
        "state": state,
        "current_score": current,
        "previous_score": previous,
        "delta": delta,
        "basis": SPEED_BASIS,
        "unavailable_reason": None,
    }


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_bar_start_et(value: Any) -> Optional[datetime]:
    """Parse one bar's ISO timestamp into an aware America/New_York datetime."""

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        # 无时区的时间无法诚实换算到 ET；丢弃而不是猜测。
        return None
    return parsed.astimezone(_NEW_YORK)


def filter_regular_session_bars(
    bars: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only 09:30 ≤ ET start < 16:00 bars with a usable OHLC, sorted."""

    rows: list[dict[str, Any]] = []
    for bar in bars or ():
        start = _parse_bar_start_et(bar.get("date"))
        if start is None:
            continue
        minutes = start.hour * 60 + start.minute
        if not (9 * 60 + 30 <= minutes < 16 * 60):
            continue
        open_ = _finite(bar.get("open"))
        high = _finite(bar.get("high"))
        low = _finite(bar.get("low"))
        close = _finite(bar.get("close"))
        if open_ is None or high is None or low is None or close is None:
            continue
        if open_ <= 0 or high < low:
            continue
        volume = _finite(bar.get("volume"))
        rows.append(
            {
                "start_et": start,
                "session_date_et": start.date().isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume if volume is not None and volume >= 0 else None,
            }
        )
    rows.sort(key=lambda row: row["start_et"])
    return rows


def split_burst_session_bars(
    bars: Sequence[Mapping[str, Any]],
    *,
    market_date_et: str,
    quote_session_scope: str,
) -> tuple[Optional[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split raw 5m bars into (target session date, its bars, prior-session bars).

    ``current_session`` scope targets the ET market date itself (premarket may
    legitimately have zero bars yet); ``latest_prior_session`` scope (closed
    nights/weekends) targets the newest session with bars on or before the
    market date, so the evening/weekend review still shows that session's legs.
    """

    rows = filter_regular_session_bars(bars)
    dates = sorted({row["session_date_et"] for row in rows})
    if quote_session_scope == "current_session":
        target_date: Optional[str] = market_date_et
    else:
        eligible = [item for item in dates if item <= market_date_et]
        target_date = eligible[-1] if eligible else None
    if target_date is None:
        return None, [], []
    prior_dates = [item for item in dates if item < target_date]
    prior_date = prior_dates[-1] if prior_dates else None
    current = [row for row in rows if row["session_date_et"] == target_date]
    prior = (
        [row for row in rows if row["session_date_et"] == prior_date]
        if prior_date
        else []
    )
    return target_date, current, prior


def _median_or_none(values: Sequence[float]) -> Optional[float]:
    cleaned = [value for value in values if value is not None and value > 0]
    if not cleaned:
        return None
    return float(statistics.median(cleaned))


def _session_medians(
    session_bars: Sequence[Mapping[str, Any]],
    prior_bars: Sequence[Mapping[str, Any]],
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """(median_bar_range, median_bar_volume, basis) with early-session fallback."""

    if len(session_bars) >= MEDIAN_FALLBACK_MIN_BARS:
        basis_bars: Sequence[Mapping[str, Any]] = session_bars
        basis = MEDIAN_BASIS_CURRENT
    elif prior_bars:
        basis_bars = prior_bars
        basis = MEDIAN_BASIS_PRIOR
    elif session_bars:
        # 无上一时段可回退：只能用当日已有 K 线，仍显式标注口径。
        basis_bars = session_bars
        basis = MEDIAN_BASIS_CURRENT
    else:
        return None, None, None
    median_range = _median_or_none(
        [bar["high"] - bar["low"] for bar in basis_bars]
    )
    median_volume = _median_or_none(
        [bar["volume"] for bar in basis_bars if bar.get("volume") is not None]
    )
    return median_range, median_volume, basis


def _et_label(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _window_row(
    chunk: Sequence[Mapping[str, Any]],
    median_range: Optional[float],
    median_volume: Optional[float],
) -> dict[str, Any]:
    open_start = chunk[0]["open"]
    close_end = chunk[-1]["close"]
    thrust = close_end - open_start
    thrust_percent = round(thrust / open_start * 100.0, 6)
    thrust_norm: Optional[float] = None
    if median_range is not None and median_range > 0:
        thrust_norm = round(abs(thrust) / median_range, 6)
    volumes = [bar.get("volume") for bar in chunk]
    vol_norm: Optional[float] = None
    if (
        median_volume is not None
        and median_volume > 0
        and all(value is not None for value in volumes)
    ):
        vol_norm = round(
            sum(volumes) / (BURST_WINDOW_BARS * median_volume), 6
        )
    score: Optional[float] = None
    if thrust_norm is not None and vol_norm is not None:
        score = round(thrust_norm * vol_norm, 6)
    if thrust > 0:
        direction = "up"
    elif thrust < 0:
        direction = "down"
    else:
        direction = "flat"
    start = chunk[0]["start_et"]
    return {
        "start_et": _et_label(start),
        "end_et": _window_end_label(chunk[-1]["start_et"]),
        "thrust_percent": thrust_percent,
        "thrust_norm": thrust_norm,
        "vol_norm": vol_norm,
        "score": score,
        "direction": direction,
        "_start_minutes": start.hour * 60 + start.minute,
    }


def _window_end_label(last_bar_start: datetime) -> str:
    total = last_bar_start.hour * 60 + last_bar_start.minute + BURST_BAR_MINUTES
    return f"{total // 60:02d}:{total % 60:02d}"


def compute_burst_windows(
    session_bars: Sequence[Mapping[str, Any]],
    median_range: Optional[float],
    median_volume: Optional[float],
) -> list[dict[str, Any]]:
    """All rolling 3-bar windows over one session's regular bars, in order."""

    if len(session_bars) < BURST_WINDOW_BARS:
        return []
    return [
        _window_row(
            session_bars[index : index + BURST_WINDOW_BARS],
            median_range,
            median_volume,
        )
        for index in range(len(session_bars) - BURST_WINDOW_BARS + 1)
    ]


def select_distinct_legs(windows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Top distinct windows: score ≥ LEG_MEDIUM_MIN_SCORE, starts ≥30min apart, ≤4.

    Greedy by descending score so one violent move keeps its single best
    window instead of flooding the list with overlapping neighbours; the
    result is re-sorted chronologically for display.  v2 分级：每个波段带
    ``grade``（strong ≥ LEG_MIN_SCORE / medium ≥ LEG_MEDIUM_MIN_SCORE）——
    强波段是 2026-07-31 校准的暴动口径，中波段收录像 2026-08-03 NVDA
    上午那样的持续推升；贪心按分数降序，强波段天然优先占据名额。
    """

    picked: list[Mapping[str, Any]] = []
    ordered = sorted(
        (row for row in windows if row.get("score") is not None),
        key=lambda row: (-row["score"], row["_start_minutes"]),
    )
    for row in ordered:
        if row["score"] < LEG_MEDIUM_MIN_SCORE:
            break
        if any(
            abs(row["_start_minutes"] - other["_start_minutes"]) < LEG_MIN_GAP_MINUTES
            for other in picked
        ):
            continue
        picked.append(row)
        if len(picked) >= LEG_MAX_COUNT:
            break
    picked.sort(key=lambda row: row["_start_minutes"])
    legs = []
    for row in picked:
        leg = _public_window(row)
        leg["grade"] = "strong" if row["score"] >= LEG_MIN_SCORE else "medium"
        legs.append(leg)
    return legs


def _public_window(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def unavailable_burst_profile(
    reason: str,
    *,
    source: Optional[str] = None,
    fetched_at: Optional[str] = None,
) -> dict[str, Any]:
    """Explicit fail-closed profile: no bars means no burst claims at all."""

    return {
        "state": "unavailable",
        "session_date_et": None,
        "bar_count": 0,
        "median_bar_range": None,
        "median_bar_volume": None,
        "median_basis": None,
        "window_minutes": BURST_WINDOW_MINUTES,
        "current": None,
        "legs": [],
        # 无 K 线时速度同样未知：绝不以「持平」冒充观测值。
        "speed": compute_speed_state(()),
        "unavailable_reason": reason,
        "source": source,
        "fetched_at": fetched_at,
        "basis": BURST_BASIS,
        "limitations": list(BURST_LIMITATIONS),
    }


def compute_session_burst_profile(
    bars: Sequence[Mapping[str, Any]],
    *,
    market_date_et: str,
    quote_session_scope: str,
    source: Optional[str] = None,
    fetched_at: Optional[str] = None,
) -> dict[str, Any]:
    """Full burst profile for one symbol from raw 5m history rows.

    Deterministic and fail-closed: missing/short/unusable inputs produce an
    explicit ``state`` + ``unavailable_reason`` instead of estimated values.
    """

    target_date, session_bars, prior_bars = split_burst_session_bars(
        bars,
        market_date_et=market_date_et,
        quote_session_scope=quote_session_scope,
    )
    base = unavailable_burst_profile(
        "no_regular_session_bars", source=source, fetched_at=fetched_at
    )
    base["session_date_et"] = target_date
    if target_date is None:
        return base
    base["bar_count"] = len(session_bars)
    if not session_bars:
        base["state"] = "insufficient_bars"
        base["unavailable_reason"] = "no_session_bars_yet"
        return base

    median_range, median_volume, median_basis = _session_medians(
        session_bars, prior_bars
    )
    base["median_bar_range"] = median_range
    base["median_bar_volume"] = median_volume
    base["median_basis"] = median_basis
    if median_range is None or median_volume is None:
        base["state"] = "unavailable"
        base["unavailable_reason"] = (
            "zero_or_missing_median_bar_range"
            if median_range is None
            else "zero_or_missing_median_bar_volume"
        )
        return base

    windows = compute_burst_windows(session_bars, median_range, median_volume)
    if not windows:
        base["state"] = "insufficient_bars"
        base["unavailable_reason"] = (
            f"fewer_than_{BURST_WINDOW_BARS}_session_bars"
        )
        return base

    base["state"] = "ready"
    base["unavailable_reason"] = None
    base["current"] = _public_window(windows[-1])
    base["legs"] = select_distinct_legs(windows)
    base["speed"] = compute_speed_state(windows)
    return base
