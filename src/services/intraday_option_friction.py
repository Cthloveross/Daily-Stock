# -*- coding: utf-8 -*-
"""按需生成盘中 P0 候选的 0–7DTE 期权摩擦快照。

本模块只负责「选择哪张合约报价」及报价完整性/新鲜度验证，
不负责判断候选是否接近 P0，也不改变四级提示规则。调用方应先筛出
需要补报价的候选，再用
``enrich_candidate`` / ``enrich_candidates`` / ``enrich_payload`` 生成副本。

确定性选择规则：

* ``up`` → Call（``C``），``down`` → Put（``P``）；
* 只看 0–7DTE，先逐张执行报价、Delta 与新鲜度硬门；
* 合格池按点差、距 |Delta|=0.5、成交量/OI（降序）、DTE、ATM 距离排序；
* 仍并列时按 expiry、strike、contract code 排序，保证重放结果稳定。

报价 fail closed：动态快照、正 bid、非交叉 ask、正 mid、spread、合法 delta
或新鲜的 ``quote_as_of`` 任一不可用，该合约都不得进入合格池。0DTE 点差/
报价龄上限分别为 8%/30 秒，1–7DTE 为 10%/90 秒；没有任何合格合约时
``state`` 是 ``unavailable`` 并列出稳定的拒绝原因。Moomoo 对美股
返回的 naive ``update_time`` 按 ``America/New_York`` 解释，绝不按服务器本地
时区猜测。

缓存只保存原始 chain，而不是方向性结果。因此同一标的同一 ET 日期的 Call
和 Put 选择共享一次 provider 读取，同时不会发生方向串用。完成态短 TTL 加
single-flight 避免同一轮多个候选/调用方重复消耗期权链额度；wall lane 正忙
是推迟而非供应商失败，不做负缓存。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.opportunities.near_expiry_contracts import (
    build_near_expiry_contract_payload,
)
from src.services.alert_tiering import (
    OPTION_FRICTION_MAX_ABS_DELTA,
    OPTION_FRICTION_MAX_QUOTE_AGE_SECONDS,
    OPTION_FRICTION_MAX_SPREAD_PERCENT,
    OPTION_FRICTION_MIN_ABS_DELTA,
    OPTION_FRICTION_SELECTION_BASIS,
    OPTION_FRICTION_SOURCE,
    OPTION_FRICTION_ZERO_DTE_MAX_QUOTE_AGE_SECONDS,
    OPTION_FRICTION_ZERO_DTE_MAX_SPREAD_PERCENT,
    intraday_execution_policy,
)

logger = logging.getLogger(__name__)

MIN_ELIGIBLE_DTE = 0
MAX_ELIGIBLE_DTE = 7
DEFAULT_CACHE_TTL_SECONDS = 30.0
DEFAULT_FAILURE_CACHE_TTL_SECONDS = 5.0
DEFAULT_QUOTE_MAX_AGE_SECONDS = OPTION_FRICTION_MAX_QUOTE_AGE_SECONDS
DEFAULT_SINGLEFLIGHT_WAIT_SECONDS = 5.0
DEFAULT_WALL_LANE_WAIT_SECONDS = 2.5
_NEW_YORK = ZoneInfo("America/New_York")
_DIRECTION_TO_RIGHT = {"up": "C", "down": "P"}


def _default_chain_fetcher(
    ticker: str,
    *,
    max_dte: int,
    ref_date: date,
) -> Any:
    """Lazy default import keeps pure-unit imports independent of the SDK."""

    from data_provider.moomoo_options import fetch_near_expiry_chain_moomoo

    return fetch_near_expiry_chain_moomoo(
        ticker,
        max_dte=max_dte,
        ref_date=ref_date,
        lane_wait_seconds=DEFAULT_WALL_LANE_WAIT_SECONDS,
    )


def _finite(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _finite_positive(value: Any) -> Optional[float]:
    parsed = _finite(value)
    return parsed if parsed is not None and parsed > 0 else None


def _finite_nonnegative(value: Any) -> Optional[float]:
    parsed = _finite(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _nonnegative_int(value: Any) -> Optional[int]:
    """Return an exact non-negative integer, preserving missing as ``None``."""

    parsed = _finite_nonnegative(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _valid_delta(value: Any) -> Optional[float]:
    parsed = _finite(value)
    if parsed is None or not 0 < abs(parsed) <= 1:
        return None
    return parsed


def _aware_utc(moment: datetime) -> datetime:
    """Normalize a service clock value; a naive injected clock means UTC."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_quote_as_of(value: Any) -> Optional[datetime]:
    """Parse Moomoo ``update_time``; naive US-option times are New York time."""

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_NEW_YORK)
    return parsed.astimezone(timezone.utc)


def _format_fetched_at(value: Any, *, fallback: datetime) -> str:
    if isinstance(value, datetime):
        return _aware_utc(value).isoformat()
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        return _aware_utc(parsed).isoformat()
    return fallback.isoformat()


def _normalise_direction(value: Any) -> Optional[str]:
    direction = str(value or "").strip().lower()
    return direction if direction in _DIRECTION_TO_RIGHT else None


def direction_from_candidate(candidate: Mapping[str, Any]) -> Optional[str]:
    """Match alert tiering: confirmed displacement first, opening setup second.

    ``session_bursts.current.direction`` and the most recent leg are deliberately
    ignored.  The prefilter separately proves that a strong leg matches this sign;
    normal P0 contract selection derives its direction from ``recent_displacement``.
    Before 30 minutes exist, a strictly qualified ``opening_impulse`` may supply
    the direction for a **setup-only** quote preview; it does not make that setup P0.
    """

    displacement = candidate.get("recent_displacement")
    if isinstance(displacement, Mapping):
        net_move_atr = _finite(displacement.get("net_move_atr"))
        if net_move_atr is not None and net_move_atr != 0:
            return "up" if net_move_atr > 0 else "down"
    bursts = candidate.get("session_bursts")
    impulse = bursts.get("opening_impulse") if isinstance(bursts, Mapping) else None
    if (
        isinstance(impulse, Mapping)
        and impulse.get("state") == "qualified"
        and impulse.get("alert_qualified") is True
    ):
        direction = str(impulse.get("direction") or "").strip().lower()
        if direction in {"up", "down"}:
            return direction
    return None


def _empty_snapshot(
    *,
    ticker: str,
    direction: Optional[str],
    now: datetime,
    unavailable_reason: str,
    fetched_at: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "unavailable_reason": unavailable_reason,
        "ticker": ticker,
        "direction": direction,
        "contract_code": None,
        "right": _DIRECTION_TO_RIGHT.get(direction or ""),
        "underlying_spot": None,
        "strike": None,
        "expiry": None,
        "dte": None,
        "bid": None,
        "ask": None,
        "mid": None,
        "spread_percent": None,
        "delta": None,
        "volume": None,
        "open_interest": None,
        "iv_percent": None,
        "quote_as_of": None,
        "quote_age_seconds": None,
        "fetched_at": fetched_at or now.isoformat(),
        "source": OPTION_FRICTION_SOURCE,
        "selection_basis": OPTION_FRICTION_SELECTION_BASIS,
        "execution_policy": intraday_execution_policy(now),
        "execution_allowed": None,
    }


def build_option_contract_friction_snapshot(
    snapshot: Any,
    *,
    ticker: str,
    direction: str,
    now: Optional[datetime] = None,
    quote_max_age_seconds: float = DEFAULT_QUOTE_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Select and validate one deterministic directional 0–7DTE contract.

    This pure assembly function accepts the provider dataclass returned by
    :func:`fetch_near_expiry_chain_moomoo`.  Every result has the same field
    shape, including unavailable results, so API/schema and ledger consumers do
    not need exception-specific branches.
    """

    requested_at = _aware_utc(now or datetime.now(timezone.utc))
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_direction = _normalise_direction(direction)
    if not normalized_ticker:
        return _empty_snapshot(
            ticker="",
            direction=normalized_direction,
            now=requested_at,
            unavailable_reason="invalid_ticker",
        )
    if normalized_direction is None:
        return _empty_snapshot(
            ticker=normalized_ticker,
            direction=None,
            now=requested_at,
            unavailable_reason="direction_unavailable",
        )
    try:
        requested_max_age = float(quote_max_age_seconds)
    except (TypeError, ValueError):
        requested_max_age = DEFAULT_QUOTE_MAX_AGE_SECONDS
    if not math.isfinite(requested_max_age) or requested_max_age <= 0:
        requested_max_age = DEFAULT_QUOTE_MAX_AGE_SECONDS
    # The injectable value may tighten tests/operations, but may never relax
    # the versioned 90-second hard gate.
    max_age = min(requested_max_age, DEFAULT_QUOTE_MAX_AGE_SECONDS)

    if snapshot is None:
        return _empty_snapshot(
            ticker=normalized_ticker,
            direction=normalized_direction,
            now=requested_at,
            unavailable_reason="chain_unavailable",
        )

    fetched_at = _format_fetched_at(
        getattr(snapshot, "fetched_at", None), fallback=requested_at
    )
    try:
        payload = build_near_expiry_contract_payload(
            snapshot,
            max_dte=MAX_ELIGIBLE_DTE,
        )
    except Exception:  # noqa: BLE001 - malformed provider payload fails closed
        logger.warning(
            "[intraday-option-friction] malformed chain snapshot for %s",
            normalized_ticker,
        )
        return _empty_snapshot(
            ticker=normalized_ticker,
            direction=normalized_direction,
            now=requested_at,
            unavailable_reason="chain_payload_invalid",
            fetched_at=fetched_at,
        )

    spot = _finite_positive(payload.get("spot"))
    if spot is None:
        return _empty_snapshot(
            ticker=normalized_ticker,
            direction=normalized_direction,
            now=requested_at,
            unavailable_reason="underlying_spot_unavailable",
            fetched_at=fetched_at,
        )

    eligible_groups: list[Mapping[str, Any]] = []
    for group in payload.get("expiries") or ():
        if not isinstance(group, Mapping):
            continue
        try:
            dte = int(group.get("dte"))
        except (TypeError, ValueError):
            continue
        if MIN_ELIGIBLE_DTE <= dte <= MAX_ELIGIBLE_DTE:
            eligible_groups.append(group)
    if not eligible_groups:
        result = _empty_snapshot(
            ticker=normalized_ticker,
            direction=normalized_direction,
            now=requested_at,
            unavailable_reason="no_eligible_0_7dte_expiry",
            fetched_at=fetched_at,
        )
        result["underlying_spot"] = spot
        return result

    expected_right = _DIRECTION_TO_RIGHT[normalized_direction]
    # Ranking is deliberately lexicographic and versioned by
    # OPTION_FRICTION_SELECTION_BASIS.  Missing volume/OI are allowed evidence
    # fields (the hard gates do not require them) but sort after observed values.
    selectable: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    rejection_reasons: set[str] = set()
    matching_right_rows = 0
    for group in eligible_groups:
        group_dte = int(group.get("dte"))
        for row in group.get("contracts") or ():
            if not isinstance(row, Mapping):
                continue
            if str(row.get("right") or "").strip().upper() != expected_right:
                continue
            matching_right_rows += 1

            code = str(row.get("code") or "").strip()
            expiry = str(row.get("expiry") or group.get("expiry") or "").strip()
            strike = _finite_positive(row.get("strike"))
            try:
                row_dte = int(row.get("dte"))
            except (TypeError, ValueError):
                row_dte = -1
            if not code or not expiry or strike is None:
                rejection_reasons.add("contract_identity_unavailable")
                continue
            if row_dte != group_dte or not (
                MIN_ELIGIBLE_DTE <= row_dte <= MAX_ELIGIBLE_DTE
            ):
                rejection_reasons.add("contract_dte_invalid")
                continue
            if row.get("quote_state") != "observed":
                rejection_reasons.add(
                    str(row.get("unavailable_reason") or "option_quote_unavailable")
                )
                continue

            bid = _finite_positive(row.get("bid"))
            ask = _finite_nonnegative(row.get("ask"))
            if bid is None:
                rejection_reasons.add("bid_not_positive")
                continue
            if ask is None:
                rejection_reasons.add("ask_unavailable")
                continue
            if ask < bid:
                rejection_reasons.add("crossed_quote")
                continue
            mid = _finite_positive(row.get("mid"))
            spread = _finite_nonnegative(row.get("spread_percent"))
            if mid is None or spread is None:
                rejection_reasons.add(
                    str(row.get("spread_unavailable_reason") or "option_spread_unavailable")
                )
                continue
            expected_mid = (bid + ask) / 2.0
            expected_spread = (ask - bid) / expected_mid * 100.0
            if (
                abs(mid - expected_mid) > max(1e-6, expected_mid * 1e-4)
                or abs(spread - expected_spread)
                > max(1e-4, expected_spread * 1e-4)
            ):
                rejection_reasons.add("option_spread_inconsistent")
                continue
            spread_limit = (
                OPTION_FRICTION_ZERO_DTE_MAX_SPREAD_PERCENT
                if row_dte == 0
                else OPTION_FRICTION_MAX_SPREAD_PERCENT
            )
            if spread > spread_limit:
                rejection_reasons.add("option_spread_too_wide")
                continue

            delta = _valid_delta(row.get("delta"))
            if delta is None:
                rejection_reasons.add("option_delta_unavailable")
                continue
            if (expected_right == "C" and delta <= 0) or (
                expected_right == "P" and delta >= 0
            ):
                rejection_reasons.add("option_delta_direction_mismatch")
                continue
            abs_delta = abs(delta)
            if not (
                OPTION_FRICTION_MIN_ABS_DELTA
                <= abs_delta
                <= OPTION_FRICTION_MAX_ABS_DELTA
            ):
                rejection_reasons.add("option_delta_out_of_range")
                continue

            quote_as_of = str(row.get("quote_as_of") or "").strip() or None
            if quote_as_of is None:
                rejection_reasons.add("quote_as_of_unavailable")
                continue
            quote_moment = _parse_quote_as_of(quote_as_of)
            if quote_moment is None:
                rejection_reasons.add("quote_as_of_invalid")
                continue
            quote_age = (requested_at - quote_moment).total_seconds()
            if quote_age < 0:
                rejection_reasons.add("quote_as_of_in_future")
                continue
            age_limit = (
                OPTION_FRICTION_ZERO_DTE_MAX_QUOTE_AGE_SECONDS
                if row_dte == 0
                else max_age
            )
            if quote_age > age_limit:
                rejection_reasons.add("quote_stale")
                continue

            volume = _nonnegative_int(row.get("session_volume"))
            open_interest = _nonnegative_int(row.get("open_interest"))
            iv_percent = _finite_positive(row.get("iv_percent"))
            candidate = {
                "contract_code": code,
                "right": expected_right,
                "underlying_spot": spot,
                "strike": strike,
                "expiry": expiry,
                "dte": row_dte,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_percent": spread,
                "delta": delta,
                "volume": volume,
                "open_interest": open_interest,
                "iv_percent": iv_percent,
                "quote_as_of": quote_as_of,
                "quote_age_seconds": round(quote_age, 3),
            }
            rank_key = (
                spread,
                abs(abs_delta - 0.5),
                -volume if volume is not None else math.inf,
                -open_interest if open_interest is not None else math.inf,
                row_dte,
                abs(strike - spot),
                expiry,
                strike,
                code,
            )
            selectable.append((rank_key, candidate))
    if not selectable:
        reason = (
            "matching_right_contract_unavailable"
            if matching_right_rows == 0
            else "no_eligible_contract:"
            + ",".join(sorted(rejection_reasons or {"unknown_filter_rejection"}))
        )
        result = _empty_snapshot(
            ticker=normalized_ticker,
            direction=normalized_direction,
            now=requested_at,
            unavailable_reason=reason,
            fetched_at=fetched_at,
        )
        result["underlying_spot"] = spot
        return result

    _rank_key, selected = min(selectable, key=lambda item: item[0])
    policy = intraday_execution_policy(requested_at)
    result = {
        "state": "ready",
        "unavailable_reason": None,
        "ticker": normalized_ticker,
        "direction": normalized_direction,
        **selected,
        "fetched_at": fetched_at,
        "source": OPTION_FRICTION_SOURCE,
        "selection_basis": OPTION_FRICTION_SELECTION_BASIS,
        "execution_policy": policy,
        "execution_allowed": policy[
            "zero_dte_entry_allowed" if selected["dte"] == 0 else "ordinary_entry_allowed"
        ],
    }
    return result


def build_option_candidate_supplement(
    snapshot: Any,
    *,
    ticker: str,
    direction: str,
    now: Optional[datetime] = None,
    max_candidates: int = 3,
) -> dict[str, Any]:
    """Return up to three quote-qualified 0--7DTE rows, one per expiry.

    This is supplemental context only.  Empty/stale/wide chains return an
    explicit unavailable payload and can never revoke the underlying P0.
    """

    requested_at = _aware_utc(now or datetime.now(timezone.utc))
    policy = intraday_execution_policy(requested_at)
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_direction = _normalise_direction(direction)
    empty = {
        "state": "unavailable",
        "unavailable_reason": "chain_unavailable",
        "ticker": normalized_ticker,
        "direction": normalized_direction,
        "candidates": [],
        "source": OPTION_FRICTION_SOURCE,
        "selection_basis": (
            OPTION_FRICTION_SELECTION_BASIS + ";one_per_expiry;max_three"
        ),
        "fetched_at": requested_at.isoformat(),
        "execution_policy": policy,
    }
    if not normalized_ticker or normalized_direction is None:
        empty["unavailable_reason"] = (
            "invalid_ticker" if not normalized_ticker else "direction_unavailable"
        )
        return empty
    if snapshot is None:
        return empty
    try:
        payload = build_near_expiry_contract_payload(
            snapshot, max_dte=MAX_ELIGIBLE_DTE
        )
    except Exception:  # noqa: BLE001 - malformed provider payload fails closed
        empty["unavailable_reason"] = "chain_payload_invalid"
        return empty
    empty["fetched_at"] = _format_fetched_at(
        getattr(snapshot, "fetched_at", None), fallback=requested_at
    )
    spot = _finite_positive(payload.get("spot"))
    if spot is None:
        empty["unavailable_reason"] = "underlying_spot_unavailable"
        return empty

    wanted_right = _DIRECTION_TO_RIGHT[normalized_direction]
    selected_by_expiry: list[tuple[int, str, tuple[Any, ...], dict[str, Any]]] = []
    rejected: set[str] = set()
    for group in payload.get("expiries") or ():
        if not isinstance(group, Mapping):
            continue
        try:
            group_dte = int(group.get("dte"))
        except (TypeError, ValueError):
            continue
        expiry = str(group.get("expiry") or "").strip()
        if not (MIN_ELIGIBLE_DTE <= group_dte <= MAX_ELIGIBLE_DTE) or not expiry:
            continue
        ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for row in group.get("contracts") or ():
            if not isinstance(row, Mapping):
                continue
            if str(row.get("right") or "").strip().upper() != wanted_right:
                continue
            code = str(row.get("code") or "").strip()
            strike = _finite_positive(row.get("strike"))
            if not code or strike is None or row.get("quote_state") != "observed":
                rejected.add("quote_or_identity_unavailable")
                continue
            bid = _finite_positive(row.get("bid"))
            ask = _finite_nonnegative(row.get("ask"))
            mid = _finite_positive(row.get("mid"))
            spread = _finite_nonnegative(row.get("spread_percent"))
            delta = _valid_delta(row.get("delta"))
            if (
                bid is None
                or ask is None
                or ask < bid
                or mid is None
                or spread is None
                or delta is None
            ):
                rejected.add("quote_or_greeks_invalid")
                continue
            if (wanted_right == "C" and delta <= 0) or (
                wanted_right == "P" and delta >= 0
            ):
                rejected.add("delta_direction_mismatch")
                continue
            if not (
                OPTION_FRICTION_MIN_ABS_DELTA
                <= abs(delta)
                <= OPTION_FRICTION_MAX_ABS_DELTA
            ):
                rejected.add("delta_out_of_range")
                continue
            spread_limit = (
                OPTION_FRICTION_ZERO_DTE_MAX_SPREAD_PERCENT
                if group_dte == 0
                else OPTION_FRICTION_MAX_SPREAD_PERCENT
            )
            if spread > spread_limit:
                rejected.add("spread_too_wide")
                continue
            quote_as_of = str(row.get("quote_as_of") or "").strip()
            quote_moment = _parse_quote_as_of(quote_as_of)
            if quote_moment is None:
                rejected.add("quote_time_unavailable")
                continue
            quote_age = (requested_at - quote_moment).total_seconds()
            age_limit = (
                OPTION_FRICTION_ZERO_DTE_MAX_QUOTE_AGE_SECONDS
                if group_dte == 0
                else OPTION_FRICTION_MAX_QUOTE_AGE_SECONDS
            )
            if quote_age < 0 or quote_age > age_limit:
                rejected.add("quote_stale")
                continue
            volume = _nonnegative_int(row.get("session_volume"))
            open_interest = _nonnegative_int(row.get("open_interest"))
            candidate = {
                "contract_code": code,
                "expiry": expiry,
                "dte": group_dte,
                "right": wanted_right,
                "strike": strike,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_percent": spread,
                "delta": delta,
                "volume": volume,
                "open_interest": open_interest,
                "iv_percent": _finite_positive(row.get("iv_percent")),
                "quote_as_of": quote_as_of,
                "quote_age_seconds": round(quote_age, 3),
                "source": OPTION_FRICTION_SOURCE,
                "execution_allowed": policy[
                    "zero_dte_entry_allowed" if group_dte == 0 else "ordinary_entry_allowed"
                ],
            }
            rank = (
                spread,
                abs(abs(delta) - 0.5),
                -volume if volume is not None else math.inf,
                -open_interest if open_interest is not None else math.inf,
                abs(strike - spot),
                strike,
                code,
            )
            ranked.append((rank, candidate))
        if ranked:
            rank, candidate = min(ranked, key=lambda item: item[0])
            selected_by_expiry.append((group_dte, expiry, rank, candidate))

    if not selected_by_expiry:
        empty["unavailable_reason"] = "no_executable_candidate:" + ",".join(
            sorted(rejected or {"no_direction_matching_0_7dte_contract"})
        )
        return empty
    selected_by_expiry.sort(key=lambda item: (item[0], item[1], item[2]))
    limit = max(1, min(3, int(max_candidates)))
    return {
        **empty,
        "state": "ready",
        "unavailable_reason": None,
        "candidates": [item[3] for item in selected_by_expiry[:limit]],
    }


@dataclass(frozen=True)
class _ChainRead:
    snapshot: Any = None
    unavailable_reason: Optional[str] = None
    cacheable: bool = True


@dataclass(frozen=True)
class _CacheEntry:
    read: _ChainRead
    expires_at: float


@dataclass
class _Flight:
    event: threading.Event
    read: Optional[_ChainRead] = None


class IntradayOptionFrictionService:
    """Thread-safe raw-chain TTL cache plus directional friction assembly."""

    def __init__(
        self,
        fetcher: Optional[Callable[..., Any]] = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        failure_cache_ttl_seconds: float = DEFAULT_FAILURE_CACHE_TTL_SECONDS,
        quote_max_age_seconds: float = DEFAULT_QUOTE_MAX_AGE_SECONDS,
        singleflight_wait_seconds: float = DEFAULT_SINGLEFLIGHT_WAIT_SECONDS,
        clock: Optional[Callable[[], datetime]] = None,
        monotonic: Optional[Callable[[], float]] = None,
    ) -> None:
        self._fetcher = fetcher or _default_chain_fetcher
        self._cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self._failure_cache_ttl_seconds = max(
            0.0, float(failure_cache_ttl_seconds)
        )
        self._quote_max_age_seconds = max(0.001, float(quote_max_age_seconds))
        self._singleflight_wait_seconds = max(
            0.001, float(singleflight_wait_seconds)
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._flights: dict[tuple[str, str], _Flight] = {}

    def clear_cache(self) -> None:
        """Clear completed entries; existing in-flight reads continue normally."""

        with self._lock:
            self._cache.clear()

    def _now(self) -> datetime:
        moment = self._clock()
        if not isinstance(moment, datetime):
            raise TypeError("clock must return datetime")
        return _aware_utc(moment)

    @staticmethod
    def _is_lane_busy(exc: Exception) -> bool:
        return any(
            cls.__name__ == "MoomooWallLaneBusyError"
            for cls in type(exc).__mro__
        )

    def _fetch_chain(self, ticker: str, ref_date: date) -> _ChainRead:
        try:
            snapshot = self._fetcher(
                ticker,
                max_dte=MAX_ELIGIBLE_DTE,
                ref_date=ref_date,
            )
        except Exception as exc:  # noqa: BLE001 - provider failures fail closed
            if self._is_lane_busy(exc):
                return _ChainRead(
                    unavailable_reason="deferred_wall_lane_busy",
                    cacheable=False,
                )
            logger.warning(
                "[intraday-option-friction] chain fetch failed for %s: %s",
                ticker,
                type(exc).__name__,
            )
            return _ChainRead(unavailable_reason="chain_fetch_failed")
        if snapshot is None:
            return _ChainRead(unavailable_reason="chain_unavailable")
        return _ChainRead(snapshot=snapshot)

    def _cached_chain(self, ticker: str, ref_date: date) -> _ChainRead:
        key = (ticker, ref_date.isoformat())
        started = self._monotonic()
        leader = False
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > started:
                return cached.read
            if cached is not None:
                self._cache.pop(key, None)
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight(event=threading.Event())
                self._flights[key] = flight
                leader = True

        if not leader:
            if not flight.event.wait(timeout=self._singleflight_wait_seconds):
                return _ChainRead(
                    unavailable_reason="singleflight_wait_timeout",
                    cacheable=False,
                )
            return flight.read or _ChainRead(
                unavailable_reason="singleflight_result_unavailable",
                cacheable=False,
            )

        read = self._fetch_chain(ticker, ref_date)
        completed = self._monotonic()
        ttl = (
            self._cache_ttl_seconds
            if read.snapshot is not None
            else self._failure_cache_ttl_seconds
        )
        with self._lock:
            if read.cacheable and ttl > 0:
                self._cache[key] = _CacheEntry(
                    read=read,
                    expires_at=completed + ttl,
                )
            flight.read = read
            self._flights.pop(key, None)
            flight.event.set()
        return read

    def get_snapshot(
        self,
        ticker: str,
        direction: str,
        *,
        ref_date: Optional[date] = None,
    ) -> dict[str, Any]:
        """Fetch/cache a raw chain, then select one directional contract."""

        now = self._now()
        normalized_ticker = str(ticker or "").strip().upper()
        normalized_direction = _normalise_direction(direction)
        if not normalized_ticker:
            return _empty_snapshot(
                ticker="",
                direction=normalized_direction,
                now=now,
                unavailable_reason="invalid_ticker",
            )
        if normalized_direction is None:
            return _empty_snapshot(
                ticker=normalized_ticker,
                direction=None,
                now=now,
                unavailable_reason="direction_unavailable",
            )
        target_date = ref_date or now.astimezone(_NEW_YORK).date()
        if not isinstance(target_date, date):
            return _empty_snapshot(
                ticker=normalized_ticker,
                direction=normalized_direction,
                now=now,
                unavailable_reason="invalid_ref_date",
            )

        chain = self._cached_chain(normalized_ticker, target_date)
        # Provider/lane wait time counts toward quote age.  Re-read the clock
        # after the raw-chain operation so a slow fetch cannot make a stale
        # quote look younger than it is.
        now = self._now()
        if chain.snapshot is None:
            return _empty_snapshot(
                ticker=normalized_ticker,
                direction=normalized_direction,
                now=now,
                unavailable_reason=chain.unavailable_reason or "chain_unavailable",
            )
        return build_option_contract_friction_snapshot(
            chain.snapshot,
            ticker=normalized_ticker,
            direction=normalized_direction,
            now=now,
            quote_max_age_seconds=self._quote_max_age_seconds,
        )

    def get_candidate_supplement(
        self,
        ticker: str,
        direction: str,
        *,
        ref_date: Optional[date] = None,
        max_candidates: int = 3,
    ) -> dict[str, Any]:
        """Fetch quote-qualified rows; manual time checks remain separate."""

        now = self._now()
        normalized_ticker = str(ticker or "").strip().upper()
        normalized_direction = _normalise_direction(direction)
        target_date = ref_date or now.astimezone(_NEW_YORK).date()
        if not normalized_ticker or normalized_direction is None:
            return build_option_candidate_supplement(
                None,
                ticker=normalized_ticker,
                direction=normalized_direction or "",
                now=now,
                max_candidates=max_candidates,
            )
        chain = self._cached_chain(normalized_ticker, target_date)
        now = self._now()
        result = build_option_candidate_supplement(
            chain.snapshot,
            ticker=normalized_ticker,
            direction=normalized_direction,
            now=now,
            max_candidates=max_candidates,
        )
        if chain.snapshot is None:
            result["unavailable_reason"] = (
                chain.unavailable_reason or "chain_unavailable"
            )
        return result

    def enrich_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        direction: Optional[str] = None,
        ref_date: Optional[date] = None,
    ) -> dict[str, Any]:
        """Return a candidate copy with additive ``option_contract_friction``."""

        result = dict(candidate)
        ticker = str(candidate.get("ticker") or "").strip().upper()
        resolved_direction = _normalise_direction(direction)
        if direction is None:
            resolved_direction = direction_from_candidate(candidate)
        result["option_contract_friction"] = self.get_snapshot(
            ticker,
            resolved_direction or "",
            ref_date=ref_date,
        )
        return result

    def enrich_candidates(
        self,
        candidates: Iterable[Any],
        *,
        selected_tickers: Optional[Collection[str]] = None,
        ref_date: Optional[date] = None,
    ) -> list[Any]:
        """Return candidate copies; optionally enrich only caller-selected tickers."""

        selected = (
            {
                str(ticker or "").strip().upper()
                for ticker in selected_tickers
                if str(ticker or "").strip()
            }
            if selected_tickers is not None
            else None
        )
        enriched: list[Any] = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                enriched.append(candidate)
                continue
            ticker = str(candidate.get("ticker") or "").strip().upper()
            if selected is not None and ticker not in selected:
                enriched.append(dict(candidate))
                continue
            enriched.append(self.enrich_candidate(candidate, ref_date=ref_date))
        return enriched

    def enrich_payload(
        self,
        payload: Mapping[str, Any],
        *,
        selected_tickers: Optional[Collection[str]] = None,
    ) -> dict[str, Any]:
        """Return a payload copy and preserve non-selected candidates unchanged.

        The method deliberately does not decide what a near-P0 candidate is.
        ``selected_tickers`` is the integration seam for the caller's existing
        condition prefilter.  Omitting it enriches every mapping candidate.
        """

        result = dict(payload)
        raw_market_date = str(payload.get("market_date_et") or "").strip()
        try:
            ref_date = date.fromisoformat(raw_market_date)
        except ValueError:
            ref_date = self._now().astimezone(_NEW_YORK).date()
        result["candidates"] = self.enrich_candidates(
            payload.get("candidates") or (),
            selected_tickers=selected_tickers,
            ref_date=ref_date,
        )
        return result


__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_QUOTE_MAX_AGE_SECONDS",
    "DEFAULT_WALL_LANE_WAIT_SECONDS",
    "MAX_ELIGIBLE_DTE",
    "MIN_ELIGIBLE_DTE",
    "OPTION_FRICTION_SELECTION_BASIS",
    "OPTION_FRICTION_SOURCE",
    "IntradayOptionFrictionService",
    "build_option_candidate_supplement",
    "build_option_contract_friction_snapshot",
    "direction_from_candidate",
]
