# -*- coding: utf-8 -*-
"""今日车道可用性（day-type）的纯判定层。

回答一个问题：**今天这批标的到底有没有 0DTE 可用**——有则日内车道
（V2-A）成立，没有则日内车道关闭、只剩过夜车道（V2-B）或不做。

为什么需要这一层（写给未来的自己）
--------------------------------------
用户自身干净口径历史（build #3，2026-04-21 起、仅 ``fill_allocations>0``
的明细成交回合，n=1,407；毛口径＝``realized_pnl_net + total_fee``，
风险＝``ABS(opening_cash_flow)``）的取证结论：**「周二/周四亏钱」不是
星期效应，是合约可用性造成的合约选择问题**。

- 按星期毛口径：周一 +5.61%（n=263，0DTE 141 笔）、周三 +7.99%（259，118）、
  周五 +3.29%（284，209）全部盈利；**周二 −2.61%（271，0DTE 仅 36 笔而
  1-3DTE 达 206 笔）、周四 −3.74%（313，0DTE 52 / 1-3DTE 213）为唯二亏损日**。
- 成因：本人主要标的（NVDA/TSLA/MU/AAPL 等）为周一/三/五到期，多数中小盘
  仅周五到期；周二/周四没有 0DTE，于是退而买 1-3DTE。
- 逐 DTE 当日平：0DTE +3.44%（n=556，剔尾 +1.49%），而 **1DTE −4.97%
  （313，剔尾 −8.01%，胜率 25.2%）**、3DTE −5.19%（66，剔尾 −9.88%）——
  1DTE 当日平是全样本最差桶，它不是 0DTE 的替代品。
- 隔夜持有：4DTE +38.88%（30，剔尾 +16.33%，胜率 60.0%）、7DTE +33.35%
  （21，剔尾 +21.63%，胜率 66.7%）。
- 周二/周四真做的 0DTE 里 QQQ 占 43/88 笔（毛 −0.45%），而 QQQ 属本人历史
  负边际标的。
- 行为缺口：恰恰在过夜车道是唯一好选择的两天，本人几乎没用它——周二 1 笔、
  周四 11 笔。

以上已写入 Playbook 候选「V2-E · 按合约可用性决定今天做不做日内
（周二/周四＝过夜日）」。本模块是 V2-E 的盘面读数层。

判定纪律
--------
- **不硬编码星期规则**：星期只是历史里的相关现象，真正的因是「今天这个
  标的有没有到期日」。假日、节前特殊到期、标的新增周二/周四到期都会让
  星期规则失效——因此一律从当日真实期权链元数据推导。
- **fail closed**：链读不到＝``unknown`` + 原因，**绝不**以「读不到」冒充
  「今天没有 0DTE」。反向也成立：只要有一个标的确证存在 0DTE，就是
  ``intraday_available``，不因为别的标的读不到而退回 unknown。
- 本模块只描述可用性，不打分、不排序、不推荐、不下单。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

FORMULA_VERSION = "lane-availability/v1"

#: 车道可用性观察窗口：0..7 DTE 覆盖日内车道（0DTE）与过夜车道（4-7DTE）
#: 两条车道的全部合法期限，同时是 Moomoo 链窗口的单窗口上限（<30 天）。
LANE_AVAILABILITY_MAX_DTE = 7

#: 判定依据（随响应回显，供前端与文档引用同一句话）。
DAY_TYPE_BASIS = (
    "per_ticker_option_expiry_metadata_within_0_7_dte_v1"
)

DAY_TYPE_LIMITATIONS = (
    "车道可用性只回答「今天有没有 0DTE 可用」，不评价标的、不预测方向、不构成建议。",
    "到期日来自 Moomoo 期权链元数据；任一标的链读不到即显式 unknown，绝不以「读不到」冒充「没有 0DTE」。",
    "只覆盖今日深度层中有界的前若干个标的（超出上限或本轮额度预算的标的列在 deferred_tickers），不代表全部 universe 今日没有 0DTE。",
    "DTE 以 America/New_York 交易日为基准按自然日计算，未接入交易所假日日历。",
)

TickerAvailability = dict[str, Any]


def _normalise_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def build_ticker_availability(
    ticker: str,
    expiries: Optional[Iterable[Any]],
    *,
    max_dte: int = LANE_AVAILABILITY_MAX_DTE,
    unavailable_reason: Optional[str] = None,
) -> TickerAvailability:
    """把一个标的的 ``(expiry, dte)`` 元数据压成可用性读数。

    ``expiries`` 接受 ``(expiry_iso, dte)`` 二元组或带 ``expiry``/``dte``
    键的 mapping（临期合约面板的分组载荷可直接喂入）。

    - ``expiries is None``：链不可读 → ``state="unavailable"``、
      ``has_zero_dte=None``（**未知，不是「没有」**）+ 原因；
    - ``expiries`` 为空序列：链可读但窗口内确实没有到期日 → ``state="ready"``、
      ``has_zero_dte=False``（诚实空态，不是失败）；
    - 其余：按 0..max_dte 过滤去重后升序输出 ``available_dte_list``。
    """

    symbol = _normalise_ticker(ticker)
    upper = max(0, int(max_dte))
    if expiries is None:
        return {
            "ticker": symbol,
            "state": "unavailable",
            "has_zero_dte": None,
            "available_dte_list": [],
            "expiries": [],
            "unavailable_reason": (
                unavailable_reason or "option_expiry_metadata_unavailable"
            ),
        }

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in expiries:
        if isinstance(raw, Mapping):
            expiry_text = str(raw.get("expiry") or "").strip()
            dte_raw = raw.get("dte")
        elif isinstance(raw, (tuple, list)) and len(raw) >= 2:
            expiry_text = str(raw[0] or "").strip()
            dte_raw = raw[1]
        else:
            continue
        try:
            dte = int(dte_raw)
        except (TypeError, ValueError):
            continue
        if not expiry_text or dte < 0 or dte > upper:
            continue
        key = (expiry_text, dte)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"expiry": expiry_text, "dte": dte})

    rows.sort(key=lambda row: (row["dte"], row["expiry"]))
    dte_list = sorted({row["dte"] for row in rows})
    return {
        "ticker": symbol,
        "state": "ready",
        "has_zero_dte": 0 in dte_list,
        "available_dte_list": dte_list,
        "expiries": rows,
        "unavailable_reason": None,
    }


def derive_day_type(
    tickers: Sequence[TickerAvailability],
) -> tuple[str, str, list[str]]:
    """从逐标的可用性聚合出 ``(day_type, reason, zero_dte_tickers)``。

    三态，顺序即优先级：

    1. 任一标的**确证**存在 0DTE → ``intraday_available``。正向证据不因
       别的标的读不到而作废（读不到的标的只可能再增加 0DTE，不会减少）。
    2. 没有任何 0DTE 且**全部标的都读到了链** → ``overnight_only``。这是
       V2-E 说的「过夜日」：日内车道关闭，只剩过夜车道或不做。
    3. 其余（一个标的都没查、或没查到 0DTE 但存在读不到的标的）→
       ``unknown`` + 原因。**未知不等于「今天没有 0DTE」**，也不等于安全。
    """

    zero_dte = [
        str(item.get("ticker") or "")
        for item in tickers
        if item.get("state") == "ready" and item.get("has_zero_dte") is True
    ]
    if zero_dte:
        return (
            "intraday_available",
            f"{len(zero_dte)} 个深度层标的今日有 0DTE 到期（{'、'.join(zero_dte)}）",
            zero_dte,
        )

    if not tickers:
        return (
            "unknown",
            "今日没有可查的深度层标的，无法判定车道可用性",
            [],
        )

    unavailable = [
        str(item.get("ticker") or "")
        for item in tickers
        if item.get("state") != "ready"
    ]
    if unavailable:
        return (
            "unknown",
            (
                f"{len(unavailable)} 个标的的期权到期日读不到"
                f"（{'、'.join(unavailable)}），未知≠「今天没有 0DTE」"
            ),
            [],
        )
    return (
        "overnight_only",
        f"{len(tickers)} 个深度层标的今日均无 0DTE 到期，日内车道关闭（V2-E）",
        [],
    )


def build_lane_availability(
    tickers: Sequence[TickerAvailability],
    *,
    max_dte: int = LANE_AVAILABILITY_MAX_DTE,
    market_date_et: str,
    checked_scope: str,
    skipped_tickers: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """组装 ``lane_availability`` 区块（intraday-top 响应的 additive 字段）。

    ``skipped_tickers`` 是因供应商额度预算被推迟到下一轮的标的：它们同样
    以 ``unavailable`` 计入 ``tickers``，因此不会让 ``overnight_only`` 被
    误判——只会把结论保持在诚实的 ``unknown``。
    """

    ordered = list(tickers)
    day_type, reason, zero_dte = derive_day_type(ordered)
    readable = [item for item in ordered if item.get("state") == "ready"]
    return {
        "formula_version": FORMULA_VERSION,
        "market_date_et": str(market_date_et),
        "max_dte": max(0, int(max_dte)),
        "day_type": day_type,
        "day_type_reason": reason,
        "basis": DAY_TYPE_BASIS,
        "checked_scope": checked_scope,
        "checked_count": len(ordered),
        "readable_count": len(readable),
        "unavailable_count": len(ordered) - len(readable),
        "zero_dte_tickers": zero_dte,
        "deferred_tickers": [
            _normalise_ticker(item) for item in (skipped_tickers or [])
        ],
        "tickers": ordered,
        "limitations": list(DAY_TYPE_LIMITATIONS),
    }
