# -*- coding: utf-8 -*-
"""Pure "styleMatch v1" setup-shape detection（形态相似度标注）on 5m bars.

回答用户的核心问题「我的交易能根据你的推荐来操作吗」的诚实版本：系统不做
推荐，只标注**当前时段的几何形状**与用户自己 Playbook 里三个 setup（S1/S2/S3）
的相似度。本模块永不抓取数据——调用方注入已经为波段爆发取回的同一批 5m
K 线与 G-2 会话快照派生字段（开/高/低/现价/参考前收/VWAP）以及 v3 大盘上下
文（SPY 会话 VWAP 位置），输出一个确定性的 SetupMatchProfile：

- 每个 setup 恰好一个状态：``matched`` / ``partial`` / ``not_matched`` /
  ``unavailable``（K 线或输入不足时显式给原因，绝不猜测）；
- 一个候选可以同时相似多个 setup，全部状态都暴露；
- 标签**只是标注**：不参与排序、不隐藏行、不是买卖信号（与 v3「系统标注，
  用户过滤」同一设计规则）。

v1 检测的诚实边界（每个 setup 的 basis 与 limitations 显式写明检查了什么、
没检查什么）：

- **S1 十五分钟低点抬高突破**：仅检查 5m→15m 聚合后的 swing low 连续抬高
  （≥2 个）+ 现价或其后 15m 收盘突破抬高区间的最高点。用户完整 setup 要求
  的 5m/2m 回踩 8/13 EMA 企稳追进与 2 分钟级加速度，本 v1 完全不检查。
- **S2 跳空高开托举**：仅检查跳空支持（与缺口证据同阈值）+ 最低价未回补
  参考前收 + 现价不低于会话 VWAP。第一根 5m/2m 下探托举、8/13 EMA 不破与
  二次确认轻仓/加仓纪律，本 v1 完全不检查。
- **S3 高开遇阻回落（做空 setup）**：仅检查跳空支持 + 现价跌破开盘价或
  VWAP + 大盘走弱（SPY 处于会话 VWAP 下方）。SPY 未走弱或标缺时最多记
  partial（「形态似 S3 但大盘未走弱」），阻力位识别与 2m/1m 速度降级离场
  时机本 v1 完全不检查。

修改任何阈值或几何规则必须同步升级 intraday-top 的 ``signal_version``。
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from src.opportunities.intraday_bursts import split_burst_session_bars

STYLE_MATCH_VERSION = "style_match_v1"

# ---------------------------------------------------------------------------
# 缺口支持阈值：与盘中缺口证据完全同一常量（单一真源在本模块，
# ``intraday_top`` 以 GAP_ATR_MULTIPLE_SUPPORT_MIN / GAP_PERCENT_SUPPORT_MIN
# 别名导入）。S2/S3 的「跳空高开」不发明新阈值：
# 开盘价对参考前收 ≥ 0.75 × ATR14，或缺口百分比 ≥ 1.5%（ATR 不可用回退）。
# ---------------------------------------------------------------------------
SETUP_GAP_ATR_MULTIPLE_MIN = 0.75
SETUP_GAP_PERCENT_MIN = 1.5

# S1 v1 几何常量：5m 聚合到 15m（按 09:30 ET 栅格），swing low 需要左右各一
# 根 15m 邻居，因此不足 3 根 15m K 线时 S1 显式 unavailable。
SETUP_15M_BUCKET_MINUTES = 15
S1_MIN_15M_BARS = 3
# 低点抬高：尾部连续抬高的 swing low 至少 2 个。
S1_MIN_RISING_SWING_LOWS = 2

SETUP_MATCH_BASIS = (
    "session_5m_bars_aggregated_to_15m_grid_plus_session_quote_geometry"
)
S1_BASIS = "15m_rising_swing_lows_then_break_above_structure_high"
S2_BASIS = "gap_up_support_hold_above_reference_close_and_session_vwap"
S3_BASIS = "gap_up_rejection_below_open_or_vwap_requires_spy_below_vwap"

SETUP_KEYS = ("S1", "S2", "S3")
SETUP_LABELS = {
    "S1": "S1 低点抬高",
    "S2": "S2 跳空托举",
    "S3": "S3 高开遇阻",
}
SETUP_TITLES = {
    "S1": "十五分钟低点抬高突破",
    "S2": "跳空高开托举",
    "S3": "高开遇阻回落（做空）",
}
_SETUP_BASES = {"S1": S1_BASIS, "S2": S2_BASIS, "S3": S3_BASIS}

SETUP_MATCH_LIMITATIONS = (
    "形态相似度为 v1 几何检测：5m K 线按 09:30 ET 栅格聚合到 15m 近似，"
    "非 2m/1m 确认帧；形态相似 ≠ 可交易，不是买卖信号。",
    "S1 只检查 15m swing low 连续抬高 + 突破结构高点，不含你的进场时机要求"
    "（5m/2m 回踩 8/13 EMA 企稳追进、2 分钟级加速度）。",
    "S2 只检查跳空支持（0.75×ATR 或 1.5%，与缺口证据同阈值）+ 最低价未回补"
    "参考前收 + 现价不低于会话 VWAP 近似值，不含第一根下探托举、8/13 EMA "
    "不破与二次确认的轻仓/加仓纪律。",
    "S3 只检查跳空支持 + 现价跌破开盘价或 VWAP + SPY 处于会话 VWAP 下方，"
    "不含阻力位识别与 2m/1m 速度降级离场时机；SPY 未走弱或标缺只记 partial。",
    "形态标签只是与你 Playbook setup 的相似度观察：不参与排序、不隐藏行、"
    "不改变研究状态；Playbook 对应关系为只读展示，规则不反哺任何评分。",
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _price(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


def _bucket_label(bucket_index: int) -> str:
    total = 9 * 60 + 30 + bucket_index * SETUP_15M_BUCKET_MINUTES
    return f"{total // 60:02d}:{total % 60:02d}"


def aggregate_15m_bars(
    session_bars: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate one session's filtered 5m rows onto the 09:30 ET 15m grid.

    ``session_bars`` are :func:`filter_regular_session_bars` rows (sorted,
    ``start_et`` aware datetimes).  Buckets with missing 5m bars still form a
    15m bar from what exists — ``bar_count_5m`` keeps the honesty visible.
    """

    buckets: dict[int, list[Mapping[str, Any]]] = {}
    for bar in session_bars:
        start = bar["start_et"]
        minutes = start.hour * 60 + start.minute - (9 * 60 + 30)
        if minutes < 0:
            continue
        buckets.setdefault(minutes // SETUP_15M_BUCKET_MINUTES, []).append(bar)
    rows: list[dict[str, Any]] = []
    for index in sorted(buckets):
        chunk = buckets[index]
        rows.append(
            {
                "start_et": _bucket_label(index),
                "end_et": _bucket_label(index + 1),
                "open": chunk[0]["open"],
                "high": max(bar["high"] for bar in chunk),
                "low": min(bar["low"] for bar in chunk),
                "close": chunk[-1]["close"],
                "bar_count_5m": len(chunk),
            }
        )
    return rows


def _swing_lows(bars_15m: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Interior bars whose low is strictly below both neighbours' lows."""

    lows: list[dict[str, Any]] = []
    for index in range(1, len(bars_15m) - 1):
        bar = bars_15m[index]
        if (
            bar["low"] < bars_15m[index - 1]["low"]
            and bar["low"] < bars_15m[index + 1]["low"]
        ):
            lows.append({"index": index, "start_et": bar["start_et"], "low": bar["low"]})
    return lows


def _trailing_rising_run(swings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Longest suffix of swing lows where every low is above the previous."""

    run: list[dict[str, Any]] = []
    for swing in swings:
        if run and swing["low"] > run[-1]["low"]:
            run.append(swing)
        else:
            run = [swing]
    return run


def _setup_row(
    key: str,
    state: str,
    reason: str,
    evidence_lines: Sequence[str],
    playbook_refs: Optional[Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    ref = (playbook_refs or {}).get(key)
    return {
        "setup_key": key,
        "label": SETUP_LABELS[key],
        "title": SETUP_TITLES[key],
        "state": state,
        "reason": reason,
        "evidence_lines": list(evidence_lines),
        "basis": _SETUP_BASES[key],
        "playbook": dict(ref) if isinstance(ref, Mapping) else None,
    }


def _detect_s1(
    bars_15m: Sequence[Mapping[str, Any]],
    *,
    had_bars: bool,
    last_price: Optional[float],
) -> tuple[str, str, list[str]]:
    """S1 十五分钟低点抬高突破：swing low 连续抬高 + 突破结构高点。"""

    if not had_bars:
        return "unavailable", "无该交易时段的常规时段 5m K 线，S1 无法评估。", []
    if len(bars_15m) < S1_MIN_15M_BARS:
        return (
            "unavailable",
            f"15 分钟 K 线不足 {S1_MIN_15M_BARS} 根（当前 {len(bars_15m)} 根），"
            "无法识别 swing low。",
            [],
        )
    swings = _swing_lows(bars_15m)
    run = _trailing_rising_run(swings)
    if len(run) < S1_MIN_RISING_SWING_LOWS:
        if len(swings) < S1_MIN_RISING_SWING_LOWS:
            return (
                "not_matched",
                f"可识别的 15m swing low 不足 {S1_MIN_RISING_SWING_LOWS} 个。",
                [],
            )
        return (
            "not_matched",
            "15m swing low 未连续抬高（最近一个低点不高于前一个）。",
            [
                "低点序列 "
                + " → ".join(
                    f"{swing['start_et']} {_price(swing['low'])}" for swing in swings[-3:]
                ),
            ],
        )
    first_index = run[0]["index"]
    last_index = run[-1]["index"]
    structure_high = max(
        bar["high"] for bar in bars_15m[first_index : last_index + 1]
    )
    lows_line = "低点序列 " + " → ".join(
        f"{swing['start_et']} {_price(swing['low'])}" for swing in run
    )
    level_line = (
        f"结构高点 {_price(structure_high)}"
        f"（{bars_15m[first_index]['start_et']}–{bars_15m[last_index]['end_et']}）"
    )
    break_line: Optional[str] = None
    for bar in bars_15m[last_index + 1 :]:
        if bar["close"] > structure_high:
            break_line = (
                f"突破 {bar['end_et']} 收 {_price(bar['close'])}"
                f" > {_price(structure_high)}"
            )
            break
    if break_line is None and last_price is not None and last_price > structure_high:
        break_line = f"突破：现价 {_price(last_price)} > {_price(structure_high)}"
    if break_line is None:
        return (
            "partial",
            "低点抬高成立但尚未突破结构高点。",
            [lows_line, level_line],
        )
    return (
        "matched",
        "15m 低点连续抬高且已突破结构高点（几何相似，非进场确认）。",
        [lows_line, level_line, break_line],
    )


def _gap_up_support(
    gap_percent: Optional[float], gap_atr_multiple: Optional[float]
) -> Optional[bool]:
    """与缺口证据同阈值的「向上跳空支持」；缺输入返回 None（未知）。"""

    if gap_percent is None:
        return None
    if gap_percent <= 0:
        return False
    return (
        gap_atr_multiple is not None
        and gap_atr_multiple >= SETUP_GAP_ATR_MULTIPLE_MIN
    ) or gap_percent >= SETUP_GAP_PERCENT_MIN


def _gap_line(
    gap_percent: Optional[float], gap_atr_multiple: Optional[float]
) -> str:
    text = f"跳空 +{gap_percent:.2f}%" if gap_percent is not None else "跳空标缺"
    if gap_atr_multiple is not None:
        text += f"（{gap_atr_multiple:.2f}×ATR）"
    return text


def _detect_s2(
    *,
    gap_percent: Optional[float],
    gap_atr_multiple: Optional[float],
    session_low: Optional[float],
    gap_reference_close: Optional[float],
    last_price: Optional[float],
    vwap: Optional[float],
) -> tuple[str, str, list[str]]:
    """S2 跳空高开托举：跳空支持 + 低点未回补 + 现价不低于 VWAP。"""

    gap_up = _gap_up_support(gap_percent, gap_atr_multiple)
    if gap_up is None:
        return "unavailable", "缺口标缺（无开盘价或参考前收），S2 无法评估。", []
    if not gap_up:
        return (
            "not_matched",
            "无达到阈值的向上跳空（需 ≥0.75×ATR 或 ≥1.5%，与缺口证据同阈值）。",
            [_gap_line(gap_percent, gap_atr_multiple)],
        )
    hold_low: Optional[bool] = None
    if session_low is not None and gap_reference_close is not None:
        hold_low = session_low > gap_reference_close
    hold_vwap: Optional[bool] = None
    if last_price is not None and vwap is not None:
        hold_vwap = last_price >= vwap
    if hold_low is None and hold_vwap is None:
        return (
            "unavailable",
            "托举输入标缺（最低价/参考前收与现价/VWAP 均不可用）。",
            [_gap_line(gap_percent, gap_atr_multiple)],
        )
    lines = [_gap_line(gap_percent, gap_atr_multiple)]
    if hold_low is True:
        lines.append(
            f"最低 {_price(session_low)} > 参考前收 {_price(gap_reference_close)}"
            "（缺口未回补）"
        )
    elif hold_low is False:
        lines.append(
            f"最低 {_price(session_low)} ≤ 参考前收 {_price(gap_reference_close)}"
            "（缺口已回补）"
        )
    if hold_vwap is True:
        lines.append(f"现价 {_price(last_price)} ≥ VWAP {_price(vwap)}")
    elif hold_vwap is False:
        lines.append(f"现价 {_price(last_price)} < VWAP {_price(vwap)}")
    if hold_low is True and hold_vwap is True:
        return (
            "matched",
            "跳空高开且已托住：缺口未回补、现价不低于会话 VWAP"
            "（几何相似，非托举细节确认）。",
            lines,
        )
    if hold_low is True or hold_vwap is True:
        missing = []
        if hold_low is not True:
            missing.append(
                "缺口已回补" if hold_low is False else "最低价/参考前收标缺"
            )
        if hold_vwap is not True:
            missing.append(
                "现价低于 VWAP" if hold_vwap is False else "现价/VWAP 标缺"
            )
        return (
            "partial",
            "跳空高开但托举只成立一半：" + "；".join(missing) + "。",
            lines,
        )
    return "not_matched", "跳空高开后未托住：缺口回补且现价低于 VWAP。", lines


def _detect_s3(
    *,
    gap_percent: Optional[float],
    gap_atr_multiple: Optional[float],
    last_price: Optional[float],
    session_open: Optional[float],
    vwap: Optional[float],
    spy_vwap_position: Optional[str],
) -> tuple[str, str, list[str]]:
    """S3 高开遇阻回落：跳空支持 + 现价跌破开盘/VWAP + SPY 走弱。"""

    gap_up = _gap_up_support(gap_percent, gap_atr_multiple)
    if gap_up is None:
        return "unavailable", "缺口标缺（无开盘价或参考前收），S3 无法评估。", []
    if not gap_up:
        return (
            "not_matched",
            "无达到阈值的向上跳空（需 ≥0.75×ATR 或 ≥1.5%，与缺口证据同阈值）。",
            [_gap_line(gap_percent, gap_atr_multiple)],
        )
    if last_price is None:
        return "unavailable", "缺现价快照，无法判断高开后是否回落。", []
    reject_open: Optional[bool] = (
        last_price < session_open if session_open is not None else None
    )
    reject_vwap: Optional[bool] = (
        last_price < vwap if vwap is not None else None
    )
    if reject_open is None and reject_vwap is None:
        return (
            "unavailable",
            "回落输入标缺（开盘价与 VWAP 均不可用）。",
            [_gap_line(gap_percent, gap_atr_multiple)],
        )
    lines = [_gap_line(gap_percent, gap_atr_multiple)]
    rejected = reject_open is True or reject_vwap is True
    if reject_open is True:
        lines.append(f"现价 {_price(last_price)} < 开盘 {_price(session_open)}")
    if reject_vwap is True:
        lines.append(f"现价 {_price(last_price)} < VWAP {_price(vwap)}")
    if not rejected:
        return (
            "not_matched",
            "高开后未见回落（现价未跌破开盘价或 VWAP）。",
            lines,
        )
    if spy_vwap_position == "below":
        lines.append("SPY 处于会话 VWAP 下方（大盘走弱）")
        return (
            "matched",
            "高开遇阻回落且大盘走弱（几何相似；这是你的做空 setup，"
            "非离场时机确认）。",
            lines,
        )
    if spy_vwap_position in {"above", "flat"}:
        lines.append("SPY 未处于会话 VWAP 下方")
        return "partial", "形态似 S3 但大盘未走弱（SPY 未处于 VWAP 下方）。", lines
    lines.append("SPY 会话 VWAP 位置标缺")
    return (
        "partial",
        "形态似 S3 但大盘状态标缺（无法确认 SPY 走弱）。",
        lines,
    )


def compute_setup_match_profile(
    bars: Optional[Sequence[Mapping[str, Any]]],
    *,
    market_date_et: str,
    quote_session_scope: str,
    last_price: Optional[float] = None,
    session_open: Optional[float] = None,
    session_low: Optional[float] = None,
    gap_percent: Optional[float] = None,
    gap_atr_multiple: Optional[float] = None,
    gap_reference_close: Optional[float] = None,
    vwap: Optional[float] = None,
    spy_vwap_position: Optional[str] = None,
    playbook_refs: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Build one candidate's SetupMatchProfile（纯函数，fail-closed）。

    ``bars`` are the same raw 5m rows already fetched for the burst lane
    (zero new requests); ``latest_prior_session`` scope evaluates the newest
    session on/before ``market_date_et``（休市复盘的 as-of 口径，与波段爆发
    一致，``session_date_et`` 如实标注该时段）。Quote-derived inputs reuse the
    candidate's own gap/VWAP derivations so every threshold stays单一真源。
    """

    last_price = _finite(last_price)
    session_open = _finite(session_open)
    session_low = _finite(session_low)
    gap_percent = _finite(gap_percent)
    gap_atr_multiple = _finite(gap_atr_multiple)
    gap_reference_close = _finite(gap_reference_close)
    vwap = _finite(vwap)

    target_date: Optional[str] = None
    session_bars: list[dict[str, Any]] = []
    if bars:
        target_date, session_bars, _prior = split_burst_session_bars(
            bars,
            market_date_et=market_date_et,
            quote_session_scope=quote_session_scope,
        )
    bars_15m = aggregate_15m_bars(session_bars)

    s1_state, s1_reason, s1_lines = _detect_s1(
        bars_15m,
        had_bars=bool(session_bars),
        last_price=last_price,
    )
    s2_state, s2_reason, s2_lines = _detect_s2(
        gap_percent=gap_percent,
        gap_atr_multiple=gap_atr_multiple,
        session_low=session_low,
        gap_reference_close=gap_reference_close,
        last_price=last_price,
        vwap=vwap,
    )
    s3_state, s3_reason, s3_lines = _detect_s3(
        gap_percent=gap_percent,
        gap_atr_multiple=gap_atr_multiple,
        last_price=last_price,
        session_open=session_open,
        vwap=vwap,
        spy_vwap_position=spy_vwap_position,
    )

    setups = [
        _setup_row("S1", s1_state, s1_reason, s1_lines, playbook_refs),
        _setup_row("S2", s2_state, s2_reason, s2_lines, playbook_refs),
        _setup_row("S3", s3_state, s3_reason, s3_lines, playbook_refs),
    ]
    matched = [row["setup_key"] for row in setups if row["state"] == "matched"]
    partial = [row["setup_key"] for row in setups if row["state"] == "partial"]
    all_unavailable = all(row["state"] == "unavailable" for row in setups)

    return {
        "state": "unavailable" if all_unavailable else "ready",
        "style_match_version": STYLE_MATCH_VERSION,
        "quote_session_scope": quote_session_scope,
        "session_date_et": target_date,
        "bar_count_5m": len(session_bars),
        "bar_count_15m": len(bars_15m),
        "matched_setups": matched,
        "partial_setups": partial,
        "setups": setups,
        "basis": SETUP_MATCH_BASIS,
        "unavailable_reason": (
            "no_usable_bars_and_missing_quote_inputs" if all_unavailable else None
        ),
        "limitations": list(SETUP_MATCH_LIMITATIONS),
    }
