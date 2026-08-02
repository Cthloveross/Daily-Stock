# -*- coding: utf-8 -*-
"""Pure near-expiry (0–3 DTE) contract-panel assembly.

「临期合约面板」是合约选择支持，不是推荐引擎：本模块只做确定性的
近价内外行权价窗口选择、逐合约点差计算与按到期日分组，不产生任何
合约打分、排名或买卖建议。排序只有中性的 (expiry, strike, right)。

诚实边界：
- ``spread_percent = (ask - bid) / mid``。bid/ask 任一缺失时 mid 与
  spread 一律显式 ``None`` 并附 reason，绝不以 0 回填。
- OI 是 T-1 清算口径；IV 是供应商模型值；这些语义由 API 层的
  limitations 文案对用户显式声明。
- 快照缺行或 ``option_valid`` 无效的合约保留静态行并显式标缺
  （``quote_state=unavailable`` + reason），不整行丢弃，也不伪造报价。
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional

FORMULA_VERSION = "near-expiry-contracts/v1"

# 近价窗口：|strike/spot - 1| <= 5%，并保证现价上下各至少 8 档。
# 两者取并集：低价股按 ±5% 可能不足 8 档时向外扩，高价股 ±5% 覆盖
# 超过 8 档时保留全部带内行权价。窗口只约束请求规模（供应商快照
# 额度），不是流动性或价值判断。
NEAR_MONEY_PERCENT_BAND = 5.0
NEAR_MONEY_MIN_STRIKES_PER_SIDE = 8
STRIKE_WINDOW_BASIS = (
    "abs(strike/spot-1) <= 5% 与现价上下各最近 8 档行权价的并集"
)


def _finite_positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def select_near_money_strikes(
    strikes: Iterable[float],
    spot: float,
) -> list[float]:
    """Return the sorted near-the-money strike window for one expiry.

    并集规则见 ``NEAR_MONEY_PERCENT_BAND`` 注释。``spot`` 非法或没有
    合法行权价时返回空列表（fail closed，不猜测窗口）。
    """

    spot_value = _finite_positive(spot)
    if spot_value is None:
        return []
    cleaned = sorted(
        {value for raw in strikes if (value := _finite_positive(raw)) is not None}
    )
    if not cleaned:
        return []

    # 1e-9 容差让恰好落在 5.0% 边界的行权价保持包含（纯浮点除法会把
    # 95/100-1 算成 -0.050000000000000044 之类的值）。
    band = NEAR_MONEY_PERCENT_BAND / 100.0 + 1e-9
    in_band = [
        strike
        for strike in cleaned
        if abs(strike / spot_value - 1.0) <= band
    ]
    at_or_below = [strike for strike in cleaned if strike <= spot_value]
    above = [strike for strike in cleaned if strike > spot_value]
    nearest_below = at_or_below[-NEAR_MONEY_MIN_STRIKES_PER_SIDE:]
    nearest_above = above[:NEAR_MONEY_MIN_STRIKES_PER_SIDE]
    return sorted(set(in_band) | set(nearest_below) | set(nearest_above))


def compute_mid_and_spread(
    bid: Optional[float],
    ask: Optional[float],
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Return ``(mid, spread_percent, unavailable_reason)``.

    - bid/ask 任一缺失：``(None, None, "bid_or_ask_unavailable")``；
    - mid 不为正（如 bid=ask=0）：``(None, None, "mid_not_positive")``；
    - 交叉盘口（ask < bid）：保留 mid，点差显式标缺而不是给出负数；
    - 正常：``spread_percent = (ask - bid) / mid × 100``。
    """

    if bid is None or ask is None:
        return None, None, "bid_or_ask_unavailable"
    try:
        bid_value = float(bid)
        ask_value = float(ask)
    except (TypeError, ValueError):
        return None, None, "bid_or_ask_unavailable"
    if (
        not math.isfinite(bid_value)
        or not math.isfinite(ask_value)
        or bid_value < 0
        or ask_value < 0
    ):
        return None, None, "bid_or_ask_unavailable"
    mid = (bid_value + ask_value) / 2.0
    if mid <= 0:
        return None, None, "mid_not_positive"
    if ask_value < bid_value:
        return round(mid, 6), None, "crossed_quote"
    return round(mid, 6), round((ask_value - bid_value) / mid * 100.0, 6), None


def _contract_payload(contract: Any, *, is_atm: bool) -> dict[str, Any]:
    bid = getattr(contract, "bid", None)
    ask = getattr(contract, "ask", None)
    snapshot_state = str(getattr(contract, "snapshot_state", "") or "")
    observed = snapshot_state == "observed"
    mid, spread_percent, spread_reason = compute_mid_and_spread(bid, ask)
    if not observed:
        unavailable_reason = (
            "snapshot_invalid" if snapshot_state == "invalid" else "snapshot_missing"
        )
    else:
        unavailable_reason = None
    return {
        "code": str(getattr(contract, "code", "")),
        "right": str(getattr(contract, "right", "")),
        "strike": float(getattr(contract, "strike", 0.0)),
        "expiry": str(getattr(contract, "expiry", "")),
        "dte": int(getattr(contract, "dte", 0)),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_percent": spread_percent,
        "spread_unavailable_reason": spread_reason,
        "last_price": getattr(contract, "last_price", None),
        "session_volume": getattr(contract, "volume", None),
        "open_interest": getattr(contract, "open_interest", None),
        "iv_percent": getattr(contract, "iv_percent", None),
        "delta": getattr(contract, "delta", None),
        "quote_as_of": getattr(contract, "update_time", None),
        "quote_state": "observed" if observed else "unavailable",
        "unavailable_reason": unavailable_reason,
        "is_atm": is_atm,
    }


def build_near_expiry_contract_payload(
    snapshot: Any,
    *,
    max_dte: int,
) -> dict[str, Any]:
    """Assemble one provider snapshot into the per-expiry panel payload.

    分组与状态规则：
    - 每个 (expiry, dte) 一组，组内按 (strike, right, code) 中性排序；
    - 组 state：全部合约有观测报价为 ``ready``，部分为 ``partial``，
      全部标缺为 ``unavailable``（逐到期隔离：一个到期的快照失败不
      影响另一个到期的展示）；
    - 顶层 state：没有任何临期到期日为 ``empty``；全组 ready 且无失败
      批次为 ``ready``；有任一观测报价为 ``partial``；否则 ``unavailable``。
    - ATM 标记：逐到期取距 spot 最近的行权价（并列取较低者），该行权
      价上的 Call/Put 行均标 ``is_atm``；只是位置标记，不是推荐。
    """

    spot = _finite_positive(getattr(snapshot, "spot", None))
    if spot is None:
        raise ValueError("a finite positive underlying spot is required")

    expiries = tuple(getattr(snapshot, "expiries", ()) or ())
    contracts = tuple(getattr(snapshot, "contracts", ()) or ())
    by_expiry: dict[str, list[Any]] = {}
    for contract in contracts:
        expiry = str(getattr(contract, "expiry", "") or "").strip()
        if expiry:
            by_expiry.setdefault(expiry, []).append(contract)

    groups: list[dict[str, Any]] = []
    observed_total = 0
    for expiry, dte in sorted(expiries, key=lambda item: (item[1], item[0])):
        rows = sorted(
            by_expiry.get(expiry, []),
            key=lambda item: (
                float(getattr(item, "strike", 0.0)),
                str(getattr(item, "right", "")),
                str(getattr(item, "code", "")),
            ),
        )
        strikes = sorted(
            {
                value
                for row in rows
                if (value := _finite_positive(getattr(row, "strike", None)))
                is not None
            }
        )
        atm_strike = (
            min(strikes, key=lambda strike: (abs(strike - spot), strike))
            if strikes
            else None
        )
        row_payloads = [
            _contract_payload(
                row,
                is_atm=(
                    atm_strike is not None
                    and float(getattr(row, "strike", 0.0)) == atm_strike
                ),
            )
            for row in rows
        ]
        observed_count = sum(
            1 for row in row_payloads if row["quote_state"] == "observed"
        )
        observed_total += observed_count
        if not row_payloads or observed_count == 0:
            group_state = "unavailable"
        elif observed_count == len(row_payloads):
            group_state = "ready"
        else:
            group_state = "partial"
        groups.append(
            {
                "expiry": expiry,
                "dte": int(dte),
                "state": group_state,
                "contract_count": len(row_payloads),
                "observed_quote_count": observed_count,
                "contracts": row_payloads,
            }
        )

    failed_batches = max(0, int(getattr(snapshot, "failed_batch_count", 0) or 0))
    requested = max(0, int(getattr(snapshot, "requested_contract_count", 0) or 0))
    received = max(0, int(getattr(snapshot, "snapshot_received_count", 0) or 0))
    if not groups:
        state = "empty"
    elif groups and all(group["state"] == "ready" for group in groups) and failed_batches == 0:
        state = "ready"
    elif observed_total > 0:
        state = "partial"
    else:
        state = "unavailable"

    return {
        "state": state,
        "formula_version": FORMULA_VERSION,
        "spot": round(spot, 6),
        "spot_as_of": getattr(snapshot, "spot_as_of", None),
        "max_dte": int(max_dte),
        "strike_window": {
            "percent_band": NEAR_MONEY_PERCENT_BAND,
            "min_strikes_per_side": NEAR_MONEY_MIN_STRIKES_PER_SIDE,
            "basis": STRIKE_WINDOW_BASIS,
        },
        "coverage": {
            "requested_contracts": requested,
            "snapshot_received_contracts": received,
            "observed_contracts": observed_total,
            "missing_contracts": max(0, requested - observed_total),
            "failed_batches": failed_batches,
            "excluded_nonstandard_contracts": max(
                0, int(getattr(snapshot, "excluded_nonstandard_count", 0) or 0)
            ),
            "excluded_unknown_standard_type_contracts": max(
                0,
                int(
                    getattr(snapshot, "excluded_unknown_standard_type_count", 0)
                    or 0
                ),
            ),
        },
        "expiries": groups,
    }
