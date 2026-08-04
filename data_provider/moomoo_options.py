# -*- coding: utf-8 -*-
"""Moomoo OpenAPI option-chain fetcher.

Phase C of the Moomoo integration. Replaces the yfinance option chain (which
has known gaps: missing strikes, no Greeks, IV occasionally 0) with Moomoo's
static ``get_option_chain`` contract list plus ``get_market_snapshot`` for the
dynamic quote, open-interest, IV and Greek fields.  Moomoo explicitly documents
``get_option_chain`` as static metadata only; treating that DataFrame as a
quote snapshot silently turns the dynamic fields into zero.

Surface
-------
- :func:`fetch_chain_via_moomoo(symbol, expiry)` →
  ``list[OptionQuote]`` (same dataclass as the yfinance fetcher)
- :func:`compute_atm_iv_moomoo(symbol, ref_date)` →
  ``(atm_iv: float | None, expiry: str)``
- :func:`get_expirations_moomoo(symbol)` → ``list[str]`` ISO dates
- :func:`fetch_option_wall_snapshot_moomoo(symbol, dte_min, dte_max, ref_date)`
  → read-only strike-level OI / volume / gamma inputs with explicit coverage
- :func:`fetch_option_events_moomoo(symbol, limit)` → recent, provider-labelled
  unusual option transactions from the quote-only event feed
- :func:`fetch_near_expiry_chain_moomoo(symbol, max_dte, ref_date)` →
  near-the-money contract rows (bid/ask/last/volume/OI/IV/delta, per-field
  nullable) for expiries within ``max_dte`` days, for the read-only
  contract-selection panel
- :func:`fetch_expiry_availability_moomoo(symbol, max_dte, ref_date)` →
  today's ``(expiry, dte)`` pairs only (no chain window, no snapshot batch);
  the cheapest read that answers "does a 0DTE exist for this ticker today"

All public quote helpers short-circuit to a no-op (returning empty / None) when
``MOOMOO_OPEND_ENABLED!=true`` so callers can do ``moomoo first → yfinance
fallback`` without conditional branching at every call site.

Caveats
-------
- Moomoo IV is in **percent form** in the API (e.g. ``20.0`` = 20%). Chain and
  ATM-IV helpers convert it to decimal (0.20) for :mod:`src.options.iv_rank`;
  option-event records expose the provider value explicitly as ``iv_percent``.
- Strike date format from ``get_option_expiration_date`` is ``YYYY-MM-DD``.
- Equity option codes look like ``US.AAPL250620C250000``; we extract the
  numeric strike via the ``strike_price`` column directly, never parse the
  symbol string.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, List, Optional
from zoneinfo import ZoneInfo

from src.services.moomoo_runtime import (
    MoomooRuntimeError,
    create_ready_quote_context,
    probe_opend_tcp,
    quote_context_is_ready,
)

logger = logging.getLogger(__name__)


# Reuse the OptionQuote dataclass shape already used downstream so we can
# drop these into existing pipelines without a converter step.
try:  # pragma: no cover — local import path stable
    from data_provider.options_chain import OptionQuote, _classify_moneyness
except Exception:  # pragma: no cover
    @dataclass(frozen=True)
    class OptionQuote:  # type: ignore[no-redef]
        underlying: str
        expiry: str
        right: str
        strike: float
        bid: float
        ask: float
        last: float
        volume: int
        open_interest: int
        implied_volatility: float
        delta: Optional[float]
        dte: int
        moneyness: str

    def _classify_moneyness(right: str, strike: float, spot: float) -> str:  # type: ignore[no-redef]
        tol = spot * 0.005
        if abs(strike - spot) <= tol:
            return "ATM"
        if right == "C":
            return "ITM" if strike < spot else "OTM"
        return "ITM" if strike > spot else "OTM"


def _enabled() -> bool:
    return (os.environ.get("MOOMOO_OPEND_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _endpoint() -> tuple[str, int]:
    host = (os.environ.get("MOOMOO_OPEND_HOST") or "127.0.0.1").strip()
    try:
        port = int((os.environ.get("MOOMOO_OPEND_PORT") or "11111").strip())
    except ValueError:
        port = 11111
    return host, port


# Singleton OpenQuoteContext for option queries — reused across calls within
# the same process to avoid re-handshaking with OpenD on every request.
_ctx_lock = threading.RLock()
_ctx_singleton = None

# Unusual option events use a separate QuoteContext lane.  Full option-wall
# snapshots intentionally hold ``_ctx_lock`` across several SDK calls so their
# context cannot be closed mid-scan.  Sharing that lane would make the small,
# first-page event query wait behind thousands of wall snapshots.  Keep the
# same readiness/reconnect/fail-closed rules, but isolate the two read-only
# workloads so event freshness is not coupled to wall-scan latency.
_event_ctx_lock = threading.RLock()
_event_ctx_singleton = None

_SNAPSHOT_BATCH_SIZE = 400
_WALL_CONTEXT_MAX_LANES = 5
_WALL_CONTEXT_LEASE_WAIT_SECONDS = 30.0
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass
class _WallContextLane:
    """One reusable, exclusively leased QuoteContext for a wall scan."""

    ctx: Any = None
    lock: Any = None
    in_use: bool = False

    def __post_init__(self) -> None:
        if self.lock is None:
            self.lock = threading.RLock()


_wall_ctx_condition = threading.Condition(threading.RLock())
_wall_ctx_lanes: list[_WallContextLane] = []


@dataclass(frozen=True)
class _OptionSnapshotResult:
    """Dynamic option snapshots plus an explicit completeness boundary."""

    snapshots: dict[str, dict]
    requested_codes: tuple[str, ...]
    failed_batch_count: int = 0

    @property
    def missing_codes(self) -> tuple[str, ...]:
        return tuple(
            code for code in self.requested_codes if code not in self.snapshots
        )

    @property
    def complete(self) -> bool:
        return (
            bool(self.requested_codes)
            and self.failed_batch_count == 0
            and not self.missing_codes
        )


@dataclass(frozen=True)
class MoomooOptionWallContract:
    """One valid strike-level input for an option-wall model.

    ``volume`` and ``open_interest`` are required observed snapshot fields.
    IV, gamma and contract size remain nullable because older OpenD builds or
    quote permissions may omit them.  ``implied_volatility`` is normalized to
    decimal form (``0.20`` = 20%) so the same dynamic wall snapshot can also
    provide ATM Call IV without another option-chain request.  No
    dealer-position sign is inferred here.
    """

    code: str
    expiry: str
    dte: int
    right: str
    strike: float
    volume: int
    open_interest: int
    gamma: Optional[float]
    contract_size: Optional[int]
    update_time: Optional[str]
    implied_volatility: Optional[float] = None


@dataclass(frozen=True)
class MoomooOptionWallSnapshot:
    """Standard-contract-only observations with explicit coverage counters."""

    symbol: str
    spot: float
    fetched_at: datetime
    expiries: tuple[str, ...]
    contracts: tuple[MoomooOptionWallContract, ...]
    requested_contract_count: int
    snapshot_received_count: int
    valid_contract_count: int
    failed_batch_count: int
    excluded_nonstandard_count: int
    excluded_unknown_standard_type_count: int


@dataclass(frozen=True)
class MoomooOptionEvent:
    """One Moomoo-classified unusual option transaction.

    ``ticker_type`` and ``sentiment`` are provider classifications only.  They
    do not identify open/close intent, the counterparty, or dealer inventory.
    Nullable fields stay nullable when the SDK omits or invalidates a value.
    """

    event_id: str
    option_code: str
    owner_code: Optional[str]
    symbol: Optional[str]
    fill_time: Optional[str]
    ticker_type: Optional[str]
    price: Optional[float]
    volume: Optional[int]
    turnover: Optional[float]
    option_type: Optional[str]
    strike_price: Optional[float]
    expiry: Optional[str]
    dte: Optional[int]
    underlying_price: Optional[float]
    bid_price: Optional[float]
    ask_price: Optional[float]
    iv_percent: Optional[float]
    total_volume: Optional[int]
    total_open_interest: Optional[int]
    vo_ratio_percent: Optional[float]
    delta: Optional[float]
    sentiment: Optional[str]
    order_types: tuple[str, ...]
    strategy_type: Optional[str]


@dataclass(frozen=True)
class MoomooOptionEventSnapshot:
    """A single-underlying, first-page read from ``get_option_event``."""

    symbol: str
    fetched_at: datetime
    event_as_of: Optional[str]
    all_count: Optional[int]
    events: tuple[MoomooOptionEvent, ...]


@dataclass(frozen=True)
class MoomooOptionUnderlyingOverview:
    """Provider-reported option summary for one underlying.

    Moomoo documents option volume as the current session's cumulative
    activity and open interest as T-1 clearing data.  The adapter deliberately
    keeps those observations separate and preserves IV/HV values in the
    provider's percent form (``31.2`` means ``31.2%``).
    """

    symbol: str
    name: Optional[str]
    fetched_at: datetime
    call_volume: Optional[int]
    put_volume: Optional[int]
    call_open_interest: Optional[int]
    put_open_interest: Optional[int]
    iv_percent: Optional[float]
    iv_rank_percent: Optional[float]
    iv_percentile_percent: Optional[float]
    previous_iv_percent: Optional[float]
    hv_30d_percent: Optional[float]
    hv_30d_percentile: Optional[float]
    hv_60d_percent: Optional[float]
    hv_60d_percentile: Optional[float]
    hv_90d_percent: Optional[float]
    hv_90d_percentile: Optional[float]
    hv_120d_percent: Optional[float]
    hv_120d_percentile: Optional[float]
    hv_365d_percent: Optional[float]
    hv_365d_percentile: Optional[float]


@dataclass(frozen=True)
class MoomooNearExpiryContract:
    """One near-the-money contract row for the near-expiry (0–3 DTE) panel.

    静态字段（code/expiry/dte/right/strike）来自期权链元数据；动态报价
    字段逐字段 nullable：快照缺行、``option_valid`` 无效或字段非法时保持
    ``None``，绝不以 0 冒充报价。``snapshot_state`` 记录该行动态快照的
    观测状态（``observed`` / ``missing`` / ``invalid``）。本行只是读数，
    不携带任何打分或推荐语义。
    """

    code: str
    expiry: str
    dte: int
    right: str
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    last_price: Optional[float]
    volume: Optional[int]
    open_interest: Optional[int]
    iv_percent: Optional[float]
    delta: Optional[float]
    update_time: Optional[str]
    snapshot_state: str


@dataclass(frozen=True)
class MoomooNearExpiryChainSnapshot:
    """Near-the-money contracts for expiries within ``max_dte``, with coverage.

    ``expiries`` 是 ``(expiry_iso, dte)`` 元组；为空表示该标的在窗口内
    没有临期到期日（诚实空态，不是失败）。OI 为 T-1 清算口径、IV 为
    供应商百分数模型值，由消费方负责时间口径标注。
    """

    symbol: str
    spot: float
    spot_as_of: Optional[str]
    fetched_at: datetime
    max_dte: int
    expiries: tuple[tuple[str, int], ...]
    contracts: tuple[MoomooNearExpiryContract, ...]
    requested_contract_count: int
    snapshot_received_count: int
    failed_batch_count: int
    excluded_nonstandard_count: int
    excluded_unknown_standard_type_count: int


@dataclass(frozen=True)
class MoomooExpiryAvailability:
    """今日某标的在 0..``max_dte`` 天内的期权到期日（车道可用性输入）。

    ``expiries`` 是按 ``(dte, expiry)`` 升序的 ``(expiry_iso, dte)`` 元组；
    为空表示该标的今日窗口内确实没有到期日（诚实空态，不是失败——失败由
    :func:`fetch_expiry_availability_moomoo` 返回 ``None`` 表达）。只承载
    到期日元数据，不含任何报价、打分或推荐语义。
    """

    symbol: str
    market_date: str
    max_dte: int
    expiries: tuple[tuple[str, int], ...]
    fetched_at: datetime


@dataclass(frozen=True)
class MoomooUnderlyingSessionQuote:
    """One quote-only equity snapshot row for intraday plan tracking.

    ``volume`` and ``turnover`` are the provider's *current-session cumulative*
    figures; ``high_price``/``low_price`` are session extremes and
    ``update_time`` is the provider's own quote timestamp.  Fields the
    snapshot omits or invalidates stay ``None`` — they are never zero-filled
    so downstream indicators can fail closed per field.
    """

    symbol: str
    fetched_at: datetime
    last_price: Optional[float]
    open_price: Optional[float]
    high_price: Optional[float]
    low_price: Optional[float]
    prev_close_price: Optional[float]
    volume: Optional[int]
    turnover: Optional[float]
    update_time: Optional[str]
    # 美股盘前专用字段（additive）：盘前时段常规字段仍指向上一常规时段，
    # 真实盘前变动只在 pre_* 字段里（pre_change_rate 为相对上一常规收盘的
    # 百分比，可为负）。快照缺列或值非法一律 None，绝不 0 回填。
    pre_price: Optional[float] = None
    pre_change_rate: Optional[float] = None
    pre_volume: Optional[int] = None
    pre_turnover: Optional[float] = None


def fetch_underlying_session_quotes_moomoo(
    symbols: list[str] | tuple[str, ...],
) -> dict[str, MoomooUnderlyingSessionQuote]:
    """Batch-read live US-underlying session quotes via one snapshot call.

    Reuses the same ``get_market_snapshot`` machinery the option walls use for
    their spot read, on the shared quote context under ``_ctx_lock`` (one
    bounded call for at most a handful of codes — no wall lane is consumed).
    Quote-only: never subscribes, unlocks trading, or places orders.  Disabled
    integration, SDK/connection failures, or malformed rows fail closed by
    returning an empty/partial mapping.
    """

    if not _enabled():
        return {}

    requested: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        try:
            code = _to_moomoo_underlying(str(raw))
        except ValueError:
            continue
        if not code.startswith("US.") or code in seen:
            continue
        requested.append(code)
        seen.add(code)
    if not requested:
        return {}

    try:
        from moomoo import RET_OK
    except ImportError:
        return {}

    try:
        with _ctx_lock:
            ctx = _get_ctx()
            if ctx is None:
                return {}
            ret, frame = ctx.get_market_snapshot(requested)
        if ret != RET_OK or frame is None or not hasattr(frame, "iterrows"):
            logger.warning(
                "[moomoo_options] underlying session snapshot unavailable: %s",
                _brief_detail((ret, frame)),
            )
            return {}
    except Exception as exc:  # noqa: BLE001 - quote-only provider boundary
        logger.warning(
            "[moomoo_options] underlying session snapshot failed: %s",
            exc,
        )
        return {}

    fetched_at = datetime.now(timezone.utc)
    requested_set = set(requested)
    result: dict[str, MoomooUnderlyingSessionQuote] = {}
    for _, row in frame.iterrows():
        item = row.to_dict() if hasattr(row, "to_dict") else dict(row)
        code = (_safe_text(item.get("code")) or "").upper()
        if code not in requested_set:
            continue
        symbol = code[3:]
        result[symbol] = MoomooUnderlyingSessionQuote(
            symbol=symbol,
            fetched_at=fetched_at,
            last_price=_valid_positive_float(item.get("last_price")),
            open_price=_valid_positive_float(item.get("open_price")),
            high_price=_valid_positive_float(item.get("high_price")),
            low_price=_valid_positive_float(item.get("low_price")),
            prev_close_price=_valid_positive_float(item.get("prev_close_price")),
            volume=_valid_nonnegative_int(item.get("volume")),
            turnover=_valid_nonnegative_float(item.get("turnover")),
            update_time=_safe_text(item.get("update_time")),
            pre_price=_valid_positive_float(item.get("pre_price")),
            pre_change_rate=_safe_float(item.get("pre_change_rate")),
            pre_volume=_valid_nonnegative_int(item.get("pre_volume")),
            pre_turnover=_valid_nonnegative_float(item.get("pre_turnover")),
        )
    return result


def _is_alive(ctx) -> bool:
    """Inspect connection state without issuing a blocking SDK query."""
    return quote_context_is_ready(ctx)


def _get_ctx():
    """Lazy-create + cache the OpenQuoteContext, with reconnect on dead conn."""
    global _ctx_singleton
    if not _enabled():
        return None
    try:
        from moomoo import OpenQuoteContext  # noqa: F401  (probe real symbol)
    except ImportError:
        logger.warning("[moomoo_options] SDK not installed; returning None")
        return None
    with _ctx_lock:
        # Health-check the cached ctx; reconnect if OpenD bounced.
        host, port = _endpoint()
        if _ctx_singleton is not None and (
            not probe_opend_tcp(host, port)
            or not _is_alive(_ctx_singleton)
        ):
            logger.info("[moomoo_options] cached ctx dead, reconnecting")
            try:
                _ctx_singleton.close()
            except Exception:  # noqa: BLE001
                pass
            _ctx_singleton = None
        if _ctx_singleton is None:
            try:
                _ctx_singleton = create_ready_quote_context(host=host, port=port)
            except MoomooRuntimeError as exc:
                logger.warning("[moomoo_options] OpenD connect failed: %s", exc)
                return None
        return _ctx_singleton


def _get_event_ctx():
    """Lazy-create the dedicated quote-only context for option-event reads."""

    global _event_ctx_singleton
    if not _enabled():
        return None
    try:
        from moomoo import OpenQuoteContext  # noqa: F401  (probe real symbol)
    except ImportError:
        logger.warning("[moomoo_options] SDK not installed; returning None")
        return None
    with _event_ctx_lock:
        host, port = _endpoint()
        if _event_ctx_singleton is not None and (
            not probe_opend_tcp(host, port)
            or not _is_alive(_event_ctx_singleton)
        ):
            logger.info("[moomoo_options] cached event ctx dead, reconnecting")
            try:
                _event_ctx_singleton.close()
            except Exception:  # noqa: BLE001
                pass
            _event_ctx_singleton = None
        if _event_ctx_singleton is None:
            try:
                _event_ctx_singleton = create_ready_quote_context(
                    host=host,
                    port=port,
                )
            except MoomooRuntimeError as exc:
                logger.warning(
                    "[moomoo_options] OpenD event connect failed: %s",
                    exc,
                )
                return None
        return _event_ctx_singleton


def _claim_wall_context_lane() -> Optional[_WallContextLane]:
    """Reserve one bounded wall lane without serializing provider I/O."""

    deadline = time.monotonic() + _WALL_CONTEXT_LEASE_WAIT_SECONDS
    with _wall_ctx_condition:
        while True:
            for lane in _wall_ctx_lanes:
                if not lane.in_use:
                    lane.in_use = True
                    return lane
            if len(_wall_ctx_lanes) < _WALL_CONTEXT_MAX_LANES:
                lane = _WallContextLane(in_use=True)
                _wall_ctx_lanes.append(lane)
                return lane
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "[moomoo_options] option-wall context lanes remained busy "
                    "for %.1fs",
                    _WALL_CONTEXT_LEASE_WAIT_SECONDS,
                )
                return None
            _wall_ctx_condition.wait(timeout=remaining)


def _release_wall_context_lane(lane: _WallContextLane) -> None:
    with _wall_ctx_condition:
        lane.in_use = False
        _wall_ctx_condition.notify()


@contextmanager
def _lease_wall_context() -> Iterator[Optional[tuple[Any, Any]]]:
    """Lease a reusable QuoteContext dedicated to one full wall scan.

    A Top-5 request can otherwise take roughly five times the slowest symbol:
    each symbol needs several independent 400-code snapshot batches.  Dedicated
    lanes let those read-only scans overlap while keeping every SDK context
    exclusive to one worker.  Five lanes stay within the endpoint's five-symbol
    contract; provider failures still return ``None`` and never synthesize data.
    """

    if not _enabled():
        yield None
        return
    try:
        from moomoo import OpenQuoteContext  # noqa: F401  (probe real symbol)
    except ImportError:
        logger.warning("[moomoo_options] SDK not installed; returning None")
        yield None
        return

    lane = _claim_wall_context_lane()
    if lane is None:
        yield None
        return

    try:
        with lane.lock:
            host, port = _endpoint()
            if lane.ctx is not None and (
                not probe_opend_tcp(host, port) or not _is_alive(lane.ctx)
            ):
                logger.info("[moomoo_options] cached wall ctx dead, reconnecting")
                try:
                    lane.ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                lane.ctx = None
            if lane.ctx is None:
                try:
                    lane.ctx = create_ready_quote_context(host=host, port=port)
                except MoomooRuntimeError as exc:
                    logger.warning(
                        "[moomoo_options] OpenD wall context connect failed: %s",
                        exc,
                    )
                    yield None
                    return
            yield lane.ctx, lane.lock
    finally:
        _release_wall_context_lane(lane)


def _reset_wall_context_pool_for_tests() -> None:
    """Close idle wall contexts between deterministic pool tests."""

    with _wall_ctx_condition:
        if any(lane.in_use for lane in _wall_ctx_lanes):
            raise RuntimeError("cannot reset wall context pool while lanes are in use")
        lanes = list(_wall_ctx_lanes)
        _wall_ctx_lanes.clear()
    for lane in lanes:
        if lane.ctx is None:
            continue
        try:
            lane.ctx.close()
        except Exception:  # noqa: BLE001
            pass


def _to_moomoo_underlying(symbol: str) -> str:
    """``AAPL`` → ``US.AAPL`` (matches MoomooFetcher's convention)."""
    s = (symbol or "").strip().upper()
    if not s:
        raise ValueError("empty symbol")
    if "." in s and s.split(".")[0] in {"US", "HK", "SH", "SZ", "BJ"}:
        return s
    return f"US.{s}"


def get_expirations_moomoo(symbol: str) -> List[str]:
    """Return option-expiry ISO dates for ``symbol`` (sorted ascending).

    Returns ``[]`` when Moomoo is not enabled / OpenD unreachable.
    """
    try:
        from moomoo import RET_OK

        # Readiness, reconnect/close and the query share one lifecycle lock so
        # another thread cannot close this context while the SDK call is active.
        with _ctx_lock:
            ctx = _get_ctx()
            if ctx is None:
                return []
            ret, data = ctx.get_option_expiration_date(
                code=_to_moomoo_underlying(symbol)
            )
        if ret != RET_OK or data is None or data.empty:
            return []
        col = "strike_time"
        if col not in data.columns:
            return []
        out: List[str] = []
        for v in data[col].tolist():
            try:
                out.append(str(v).split(" ")[0])
            except Exception:  # noqa: BLE001
                continue
        out.sort()
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[moomoo_options] expirations(%s) failed: %s", symbol, exc)
        return []


def _spot_for_classification(symbol: str) -> Optional[float]:
    """Best-effort spot price via Moomoo snapshot. Used only for moneyness label."""
    try:
        from moomoo import RET_OK

        with _ctx_lock:
            ctx = _get_ctx()
            if ctx is None:
                return None
            return _spot_from_ctx(ctx, symbol, RET_OK)
    except Exception:  # noqa: BLE001
        return None


def _spot_from_ctx(
    ctx,
    symbol: str,
    ret_ok,
    *,
    context_lock=None,
) -> Optional[float]:
    """Read a finite positive underlying spot from an already leased context."""
    spot, _ = _spot_with_time_from_ctx(
        ctx,
        symbol,
        ret_ok,
        context_lock=context_lock,
    )
    return spot


def _spot_with_time_from_ctx(
    ctx,
    symbol: str,
    ret_ok,
    *,
    context_lock=None,
) -> tuple[Optional[float], Optional[str]]:
    """Read ``(spot, provider update_time)`` from an already leased context.

    ``update_time`` 缺失时保持 ``None``，不用本地时钟冒充供应商时点。
    """
    lock = context_lock or _ctx_lock
    with lock:
        ret, data = ctx.get_market_snapshot([_to_moomoo_underlying(symbol)])
    if ret != ret_ok or data is None or data.empty:
        return None, None
    row = data.iloc[0]
    last = _safe_float(row.get("last_price"))
    if last is None or last <= 0:
        return None, None
    return last, _safe_text(row.get("update_time"))


def _get_static_chain_frame(ctx, underlying: str, expiry: str, ret_ok):
    """Read one expiry's static contract metadata under the context lock."""
    try:
        with _ctx_lock:
            ret, data = ctx.get_option_chain(
                code=underlying,
                start=expiry,
                end=expiry,
            )
        if ret != ret_ok or data is None or data.empty:
            logger.debug(
                "[moomoo_options] empty static chain for %s %s",
                underlying,
                expiry,
            )
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("[moomoo_options] get_option_chain failed: %s", exc)
        return None


def _get_static_chain_range_frame(
    ctx,
    underlying: str,
    start_date: date,
    end_date: date,
    ret_ok,
    *,
    context_lock=None,
):
    """Read one option-chain date range of at most 30 calendar days."""

    if end_date < start_date or (end_date - start_date).days >= 30:
        raise ValueError("option-chain range must contain at most 30 days")
    try:
        lock = context_lock or _ctx_lock
        with lock:
            ret, data = ctx.get_option_chain(
                code=underlying,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
            )
        if ret != ret_ok or data is None:
            logger.warning(
                "[moomoo_options] option-wall chain range unavailable for "
                "%s %s..%s (ret=%s, detail=%s)",
                underlying,
                start_date,
                end_date,
                ret,
                _brief_detail(data),
            )
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[moomoo_options] option-wall chain range failed for %s "
            "%s..%s: %s",
            underlying,
            start_date,
            end_date,
            exc,
        )
        return None


def _static_contracts(data) -> list[dict]:
    """Return only well-formed contracts from static chain metadata."""
    contracts: list[dict] = []
    for _, row in data.iterrows():
        item = row.to_dict()
        code = str(item.get("code") or "").strip()
        right_raw = str(item.get("option_type") or "").strip().upper()
        right = (
            "C"
            if right_raw == "CALL"
            else "P"
            if right_raw == "PUT"
            else ""
        )
        strike = _safe_float(item.get("strike_price"))
        if not code or not right or strike is None or strike <= 0:
            continue
        contracts.append({"code": code, "right": right, "strike": strike})
    return contracts


def fetch_chain_via_moomoo(symbol: str, expiry: str) -> List[OptionQuote]:
    """Fetch the option chain for one expiry via Moomoo.

    Args:
        symbol: equity ticker (``AAPL`` / ``US.AAPL``).
        expiry: ISO date (``YYYY-MM-DD``).

    Returns ``[]`` on any failure or when not enabled.
    """
    try:
        from moomoo import RET_OK
    except ImportError:
        return []

    underlying = _to_moomoo_underlying(symbol)
    with _ctx_lock:
        ctx = _get_ctx()
        if ctx is None:
            return []

        data = _get_static_chain_frame(ctx, underlying, expiry, RET_OK)
        if data is None:
            return []
        contracts = _static_contracts(data)
        if not contracts:
            return []

        snapshot_result = _get_option_snapshots(
            ctx,
            [contract["code"] for contract in contracts],
            RET_OK,
        )
        if not snapshot_result.snapshots:
            logger.debug(
                "[moomoo_options] no dynamic snapshots for %s %s; "
                "refusing to present static chain rows as live quotes",
                underlying,
                expiry,
            )
            return []

        spot = _spot_from_ctx(ctx, symbol, RET_OK)
        try:
            exp_d = date.fromisoformat(expiry)
            fallback_dte = max(
                0,
                (exp_d - _new_york_market_date()).days,
            )
        except ValueError:
            fallback_dte = 0

        out: List[OptionQuote] = []
        omitted = 0
        for contract in contracts:
            snapshot = snapshot_result.snapshots.get(contract["code"])
            if snapshot is None or not _snapshot_option_valid(snapshot):
                omitted += 1
                continue

            bid = _safe_float(snapshot.get("bid_price"))
            ask = _safe_float(snapshot.get("ask_price"))
            last = _safe_float(snapshot.get("last_price"))
            volume = _safe_int(snapshot.get("volume"))
            open_interest = _safe_int(
                _first_present(
                    snapshot,
                    "option_open_interest",
                    "open_interest",
                )
            )
            iv_pct = _safe_float(
                _first_present(
                    snapshot,
                    "option_implied_volatility",
                    "implied_volatility",
                )
            )

            # ``OptionQuote`` cannot represent missing numeric values. Omit an
            # incomplete contract rather than manufacturing a live-looking 0.
            if (
                bid is None
                or ask is None
                or last is None
                or volume is None
                or open_interest is None
                or iv_pct is None
                or min(bid, ask, last, iv_pct) < 0
                or volume < 0
                or open_interest < 0
            ):
                omitted += 1
                continue

            official_dte = _safe_int(
                snapshot.get("option_expiry_date_distance")
            )
            dte = (
                max(0, official_dte)
                if official_dte is not None
                else fallback_dte
            )
            strike = contract["strike"]
            right = contract["right"]
            moneyness = (
                _classify_moneyness(right, strike, spot)
                if spot is not None and spot > 0
                else ""
            )
            out.append(
                OptionQuote(
                    underlying=symbol.upper(),
                    expiry=expiry,
                    right=right,
                    strike=strike,
                    bid=bid,
                    ask=ask,
                    last=last,
                    volume=volume,
                    open_interest=open_interest,
                    implied_volatility=iv_pct / 100.0,
                    delta=_safe_float(
                        _first_present(snapshot, "option_delta", "delta")
                    ),
                    dte=dte,
                    moneyness=moneyness,
                )
            )

        if omitted:
            logger.debug(
                "[moomoo_options] omitted %s/%s incomplete option snapshots "
                "for %s %s",
                omitted,
                len(contracts),
                underlying,
                expiry,
            )
        return out


def _get_option_snapshots(
    ctx,
    codes: List[str],
    ret_ok,
    *,
    context_lock=None,
) -> _OptionSnapshotResult:
    """Fetch dynamic option fields in documented batches of at most 400.

    Missing and failed batches remain explicit. General chain callers may use
    the valid subset, while ATM-IV lookup requests one exact code and requires
    ``complete`` before using it.
    """
    clean_codes = tuple(dict.fromkeys(code for code in codes if code))
    requested = set(clean_codes)
    snapshots: dict[str, dict] = {}
    failed_batch_count = 0
    lock = context_lock or _ctx_lock
    with lock:
        for start in range(0, len(clean_codes), _SNAPSHOT_BATCH_SIZE):
            batch = list(clean_codes[start : start + _SNAPSHOT_BATCH_SIZE])
            try:
                ret, frame = ctx.get_market_snapshot(batch)
            except Exception as exc:  # noqa: BLE001
                failed_batch_count += 1
                logger.warning(
                    "[moomoo_options] option snapshot batch failed "
                    "(requested=%s): %s",
                    len(batch),
                    exc,
                )
                continue
            if ret != ret_ok or frame is None or frame.empty:
                failed_batch_count += 1
                logger.warning(
                    "[moomoo_options] option snapshot batch unavailable "
                    "(requested=%s, ret=%s, detail=%s)",
                    len(batch),
                    ret,
                    _brief_detail(frame),
                )
                continue
            for _, row in frame.iterrows():
                item = row.to_dict()
                code = str(item.get("code") or "").strip()
                if code and code in requested:
                    snapshots[code] = item

    result = _OptionSnapshotResult(
        snapshots=snapshots,
        requested_codes=clean_codes,
        failed_batch_count=failed_batch_count,
    )
    if result.missing_codes:
        logger.debug(
            "[moomoo_options] dynamic snapshot coverage incomplete: "
            "received=%s requested=%s failed_batches=%s",
            len(result.snapshots),
            len(result.requested_codes),
            result.failed_batch_count,
        )
    return result


def _brief_detail(value, limit: int = 240) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _snapshot_option_valid(snapshot: dict) -> bool:
    value = snapshot.get("option_valid")
    if isinstance(value, bool):
        return value
    number = _safe_float(value)
    if number is not None:
        return number == 1.0
    return str(value or "").strip().lower() in {"true", "yes", "on"}


def _new_york_market_date() -> date:
    return datetime.now(tz=_NEW_YORK).date()


def _first_present(mapping: dict, *keys: str):
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _safe_float(v) -> Optional[float]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        value = float(v)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _safe_int(v) -> Optional[int]:
    value = _safe_float(v)
    if value is None or not value.is_integer():
        return None
    return int(value)


def _safe_text(value) -> Optional[str]:
    """Return a non-empty provider value without inventing a timestamp."""

    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            text = value.isoformat()
        except Exception:  # noqa: BLE001 - provider scalar compatibility
            text = str(value)
    else:
        text = str(value)
    text = text.strip()
    return text or None


def _normalise_option_standard_type(value) -> Optional[str]:
    """Normalise SDK enum/string values while preserving an unknown state."""

    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    enum_name = getattr(value, "name", None)
    text = str(enum_name if enum_name is not None else value).strip().upper()
    if not text or text in {"N/A", "NA", "NAN", "NONE", "NULL", "UNKNOWN"}:
        return None
    # Enum string representations can be ``OptionStandardType.NON_STANDARD``.
    text = text.rsplit(".", 1)[-1].replace("-", "_").replace(" ", "_")
    return text


def _wall_static_contracts(
    data,
    *,
    target_date: date,
    dte_min: int,
    dte_max: int,
    allowed_expiries: set[str],
) -> tuple[list[dict], int, int]:
    """Extract strictly ranged wall inputs from each row's ``strike_time``."""

    contracts: list[dict] = []
    excluded_nonstandard = 0
    unknown_standard_type = 0
    for _, row in data.iterrows():
        item = row.to_dict()
        expiry_date = _safe_iso_date(
            str(item.get("strike_time") or "").split(" ")[0]
        )
        if expiry_date is None or expiry_date < target_date:
            continue
        expiry = expiry_date.isoformat()
        dte = (expiry_date - target_date).days
        if (
            expiry not in allowed_expiries
            or dte < dte_min
            or dte > dte_max
        ):
            continue

        standard_type = _normalise_option_standard_type(
            item.get("option_standard_type")
        )
        if standard_type in {"NON_STANDARD", "NONSTANDARD"}:
            excluded_nonstandard += 1
            continue
        if standard_type != "STANDARD":
            # A standard-only wall must fail closed.  Older SDK rows without
            # this field or new unknown enum values are counted separately,
            # never relabelled STANDARD.
            unknown_standard_type += 1
            continue

        code = str(item.get("code") or "").strip()
        right_raw = str(item.get("option_type") or "").strip().upper()
        right = (
            "C"
            if right_raw == "CALL"
            else "P"
            if right_raw == "PUT"
            else ""
        )
        strike = _safe_float(item.get("strike_price"))
        if not code or not right or strike is None or strike <= 0:
            continue
        contracts.append(
            {
                "code": code,
                "expiry": expiry,
                "dte": dte,
                "right": right,
                "strike": strike,
                "standard_type": standard_type,
            }
        )
    return contracts, excluded_nonstandard, unknown_standard_type


def _option_chain_windows(
    *,
    target_date: date,
    dte_min: int,
    dte_max: int,
    selected_expiries: set[str],
) -> list[tuple[date, date]]:
    """Build only occupied, non-overlapping range calls capped at 30 days."""

    parsed_selected = {
        parsed
        for expiry in selected_expiries
        if (parsed := _safe_iso_date(expiry)) is not None
    }
    windows: list[tuple[date, date]] = []
    cursor = target_date + timedelta(days=dte_min)
    final_date = target_date + timedelta(days=dte_max)
    while cursor <= final_date:
        window_end = min(cursor + timedelta(days=29), final_date)
        if any(cursor <= expiry <= window_end for expiry in parsed_selected):
            windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def _expiration_dates_from_ctx(ctx, underlying: str, ret_ok) -> Optional[list[str]]:
    """Read expiration metadata from an already leased quote context."""

    try:
        ret, data = ctx.get_option_expiration_date(code=underlying)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[moomoo_options] option-wall expiration query failed for %s: %s",
            underlying,
            exc,
        )
        return None
    if ret != ret_ok or data is None:
        logger.warning(
            "[moomoo_options] option-wall expirations unavailable for %s "
            "(ret=%s, detail=%s)",
            underlying,
            ret,
            _brief_detail(data),
        )
        return None
    if data.empty:
        return []
    if "strike_time" not in data.columns:
        logger.warning(
            "[moomoo_options] option-wall expiration response for %s lacks "
            "strike_time",
            underlying,
        )
        return None
    expiries = {
        parsed.isoformat()
        for value in data["strike_time"].tolist()
        if (parsed := _safe_iso_date(str(value).split(" ")[0])) is not None
    }
    return sorted(expiries)


def fetch_option_wall_snapshot_moomoo(
    symbol: str,
    dte_min: int = 0,
    dte_max: int = 45,
    ref_date: Optional[date] = None,
) -> Optional[MoomooOptionWallSnapshot]:
    """Fetch read-only strike-level inputs for option-wall analysis.

    The function performs only quote metadata and market-snapshot queries.  It
    does not subscribe to streams, unlock trading, place/cancel orders, or infer
    dealer positioning.  Explicit ``NON_STANDARD`` contracts are excluded;
    rows without ``option_standard_type`` are excluded and counted separately;
    they are never relabelled as standard.
    """

    if not isinstance(dte_min, int) or not isinstance(dte_max, int):
        raise ValueError("dte_min and dte_max must be integers")
    if dte_min < 0 or dte_max < dte_min:
        raise ValueError("require 0 <= dte_min <= dte_max")
    if not _enabled():
        return None

    try:
        from moomoo import RET_OK
    except ImportError:
        return None

    target_date = ref_date or _new_york_market_date()
    if not isinstance(target_date, date):
        raise ValueError("ref_date must be a date")
    normalized_symbol = str(symbol or "").strip().upper()
    underlying = _to_moomoo_underlying(normalized_symbol)

    try:
        with _lease_wall_context() as leased:
            if leased is None:
                return None
            ctx, context_lock = leased

            available_expiries = _expiration_dates_from_ctx(
                ctx,
                underlying,
                RET_OK,
            )
            if available_expiries is None:
                return None

            selected: list[tuple[str, int]] = []
            for expiry in available_expiries:
                parsed = _safe_iso_date(expiry)
                if parsed is None or parsed < target_date:
                    continue
                dte = (parsed - target_date).days
                if dte_min <= dte <= dte_max:
                    selected.append((expiry, dte))

            spot = _spot_from_ctx(
                ctx,
                normalized_symbol,
                RET_OK,
                context_lock=context_lock,
            )
            if spot is None or spot <= 0:
                return None

            requested_by_code: dict[str, dict] = {}
            excluded_nonstandard_count = 0
            unknown_standard_type_count = 0
            failed_chain_range_count = 0
            selected_expiry_set = {expiry for expiry, _ in selected}
            chain_windows = _option_chain_windows(
                target_date=target_date,
                dte_min=dte_min,
                dte_max=dte_max,
                selected_expiries=selected_expiry_set,
            )
            for window_start, window_end in chain_windows:
                frame = _get_static_chain_range_frame(
                    ctx,
                    underlying,
                    window_start,
                    window_end,
                    RET_OK,
                    context_lock=context_lock,
                )
                if frame is None:
                    failed_chain_range_count += 1
                    continue
                static_contracts, excluded, unknown = _wall_static_contracts(
                    frame,
                    target_date=target_date,
                    dte_min=dte_min,
                    dte_max=dte_max,
                    allowed_expiries=selected_expiry_set,
                )
                excluded_nonstandard_count += excluded
                unknown_standard_type_count += unknown
                for contract in static_contracts:
                    requested_by_code.setdefault(contract["code"], contract)

            requested_codes = list(requested_by_code)
            snapshot_result = _get_option_snapshots(
                ctx,
                requested_codes,
                RET_OK,
                context_lock=context_lock,
            )

            contracts: list[MoomooOptionWallContract] = []
            for code, static in requested_by_code.items():
                dynamic = snapshot_result.snapshots.get(code)
                if dynamic is None or not _snapshot_option_valid(dynamic):
                    continue
                volume = _safe_int(dynamic.get("volume"))
                open_interest = _safe_int(
                    _first_present(
                        dynamic,
                        "option_open_interest",
                        "open_interest",
                    )
                )
                if (
                    volume is None
                    or open_interest is None
                    or volume < 0
                    or open_interest < 0
                ):
                    continue

                gamma = _safe_float(
                    _first_present(dynamic, "option_gamma", "gamma")
                )
                if gamma is not None and gamma < 0:
                    gamma = None
                iv_percent = _safe_float(
                    _first_present(
                        dynamic,
                        "option_implied_volatility",
                        "implied_volatility",
                    )
                )
                implied_volatility = (
                    iv_percent / 100.0
                    if iv_percent is not None and iv_percent > 0
                    else None
                )
                contract_size = _safe_int(
                    _first_present(
                        dynamic,
                        "option_contract_size",
                        "contract_size",
                    )
                )
                if contract_size is not None and contract_size <= 0:
                    contract_size = None
                update_time = _safe_text(dynamic.get("update_time"))

                contracts.append(
                    MoomooOptionWallContract(
                        code=code,
                        expiry=static["expiry"],
                        dte=static["dte"],
                        right=static["right"],
                        strike=static["strike"],
                        volume=volume,
                        open_interest=open_interest,
                        gamma=gamma,
                        contract_size=contract_size,
                        update_time=update_time,
                        implied_volatility=implied_volatility,
                    )
                )

        contracts.sort(
            key=lambda item: (item.expiry, item.strike, item.right, item.code)
        )
        if unknown_standard_type_count:
            logger.info(
                "[moomoo_options] option-wall %s excluded %s contracts with "
                "unknown option_standard_type",
                normalized_symbol,
                unknown_standard_type_count,
            )
        return MoomooOptionWallSnapshot(
            symbol=normalized_symbol,
            spot=spot,
            fetched_at=datetime.now(timezone.utc),
            expiries=tuple(expiry for expiry, _ in selected),
            contracts=tuple(contracts),
            requested_contract_count=len(requested_codes),
            snapshot_received_count=len(snapshot_result.snapshots),
            valid_contract_count=len(contracts),
            failed_batch_count=(
                failed_chain_range_count + snapshot_result.failed_batch_count
            ),
            excluded_nonstandard_count=excluded_nonstandard_count,
            excluded_unknown_standard_type_count=unknown_standard_type_count,
        )
    except Exception as exc:  # noqa: BLE001 - quote failures degrade to unavailable
        logger.warning(
            "[moomoo_options] option-wall snapshot(%s) failed: %s",
            normalized_symbol,
            exc,
        )
        return None


def fetch_expiry_availability_moomoo(
    symbol: str,
    max_dte: int = 7,
    ref_date: Optional[date] = None,
) -> Optional[MoomooExpiryAvailability]:
    """Return today's ``(expiry, dte)`` pairs within ``max_dte`` for one symbol.

    「今日车道可用性」的最省额度读法：与
    :func:`fetch_near_expiry_chain_moomoo` **共用同一条读取路径的第一步**
    （同一个独占 wall QuoteContext lane + 同一个
    ``_expiration_dates_from_ctx``），但在拿到到期日元数据后就停下——
    不发 ``get_option_chain`` 日期窗口、不取 underlying 快照、不发任何
    ``get_market_snapshot`` 批次。回答「今天有没有 0DTE」只需要到期日，
    不需要任何一张合约的报价。

    为什么需要它（V2-E）：用户干净口径历史里「周二/周四亏钱」的星期效应
    实为合约可用性造成的合约选择问题——主要标的（NVDA/TSLA/MU/AAPL）周
    一/三/五到期，周二/周四没有 0DTE 时退而买 1-3DTE，而 1DTE 当日平
    −4.97%（n=313，胜率 25.2%）是全样本最差桶。星期规则本身不可靠（假日、
    节前特殊到期都会让它失效），因此逐日从真实链元数据推导。

    fail closed：开关未启用、SDK 缺失、lane 租不到、元数据查询失败一律
    返回 ``None``（由调用方标为 unknown）；窗口内没有到期日返回空
    ``expiries`` 的读数（诚实空态，不是失败）。
    """

    if not isinstance(max_dte, int) or isinstance(max_dte, bool):
        raise ValueError("max_dte must be an integer")
    if not 0 <= max_dte <= 7:
        raise ValueError("require 0 <= max_dte <= 7")
    if not _enabled():
        return None

    try:
        from moomoo import RET_OK
    except ImportError:
        return None

    target_date = ref_date or _new_york_market_date()
    if not isinstance(target_date, date):
        raise ValueError("ref_date must be a date")
    normalized_symbol = str(symbol or "").strip().upper()
    underlying = _to_moomoo_underlying(normalized_symbol)
    if not underlying.startswith("US."):
        return None

    try:
        with _lease_wall_context() as leased:
            if leased is None:
                return None
            ctx, _context_lock = leased
            available_expiries = _expiration_dates_from_ctx(
                ctx,
                underlying,
                RET_OK,
            )
        if available_expiries is None:
            return None
        selected: list[tuple[str, int]] = []
        for expiry in available_expiries:
            parsed = _safe_iso_date(expiry)
            if parsed is None or parsed < target_date:
                continue
            dte = (parsed - target_date).days
            if dte <= max_dte:
                selected.append((expiry, dte))
        selected.sort(key=lambda item: (item[1], item[0]))
        return MoomooExpiryAvailability(
            symbol=normalized_symbol,
            market_date=target_date.isoformat(),
            max_dte=max_dte,
            expiries=tuple(selected),
            fetched_at=datetime.now(timezone.utc),
        )
    except Exception as exc:  # noqa: BLE001 - metadata failures degrade to unknown
        logger.warning(
            "[moomoo_options] expiry availability(%s) failed: %s",
            normalized_symbol,
            exc,
        )
        return None


def fetch_near_expiry_chain_moomoo(
    symbol: str,
    max_dte: int = 3,
    ref_date: Optional[date] = None,
) -> Optional[MoomooNearExpiryChainSnapshot]:
    """Fetch near-the-money contract rows for expiries within ``max_dte``.

    合约选择支持的数据读取，仅使用 Quote 元数据与市场快照：不订阅、
    不解锁交易、不下单，也不推断任何合约优劣。

    额度成本（受 §2.1 记录的 10 次链查询 / 30 秒与 60 次快照 / 30 秒
    约束）：1 次 ``get_option_expiration_date`` + 1 次 ``get_option_chain``
    日期窗口（``max_dte`` ≤ 7 < 30 天，恒为单窗口）+ 1 次 underlying
    快照 + 近价窗口合约的 ``get_market_snapshot``（每到期日 Call/Put 各
    ≤ 现价上下 8 档或 ±5% 带内档位，典型 ≤ 2 个到期日合计远小于单批
    400 上限，即 1 个快照批次）。

    与期权墙一致：显式 ``NON_STANDARD`` 合约排除，缺 ``option_standard_type``
    的行单独计数排除、绝不改标 STANDARD。窗口内没有到期日时返回空
    ``expiries`` 的快照（诚实空态）；OpenD 不可达、spot 缺失或链窗口
    失败时返回 ``None``（fail closed）。
    """

    if not isinstance(max_dte, int) or isinstance(max_dte, bool):
        raise ValueError("max_dte must be an integer")
    if not 0 <= max_dte <= 7:
        raise ValueError("require 0 <= max_dte <= 7")
    if not _enabled():
        return None

    try:
        from moomoo import RET_OK
    except ImportError:
        return None

    from src.opportunities.near_expiry_contracts import select_near_money_strikes

    target_date = ref_date or _new_york_market_date()
    if not isinstance(target_date, date):
        raise ValueError("ref_date must be a date")
    normalized_symbol = str(symbol or "").strip().upper()
    underlying = _to_moomoo_underlying(normalized_symbol)
    if not underlying.startswith("US."):
        return None

    try:
        with _lease_wall_context() as leased:
            if leased is None:
                return None
            ctx, context_lock = leased

            available_expiries = _expiration_dates_from_ctx(
                ctx,
                underlying,
                RET_OK,
            )
            if available_expiries is None:
                return None
            selected: list[tuple[str, int]] = []
            for expiry in available_expiries:
                parsed = _safe_iso_date(expiry)
                if parsed is None or parsed < target_date:
                    continue
                dte = (parsed - target_date).days
                if dte <= max_dte:
                    selected.append((expiry, dte))

            spot, spot_as_of = _spot_with_time_from_ctx(
                ctx,
                normalized_symbol,
                RET_OK,
                context_lock=context_lock,
            )
            if spot is None or spot <= 0:
                return None

            fetched_at = datetime.now(timezone.utc)
            if not selected:
                return MoomooNearExpiryChainSnapshot(
                    symbol=normalized_symbol,
                    spot=spot,
                    spot_as_of=spot_as_of,
                    fetched_at=fetched_at,
                    max_dte=max_dte,
                    expiries=(),
                    contracts=(),
                    requested_contract_count=0,
                    snapshot_received_count=0,
                    failed_batch_count=0,
                    excluded_nonstandard_count=0,
                    excluded_unknown_standard_type_count=0,
                )

            selected_expiry_set = {expiry for expiry, _ in selected}
            frame = _get_static_chain_range_frame(
                ctx,
                underlying,
                target_date,
                target_date + timedelta(days=max_dte),
                RET_OK,
                context_lock=context_lock,
            )
            if frame is None:
                return None
            static_contracts, excluded_nonstandard, unknown_standard = (
                _wall_static_contracts(
                    frame,
                    target_date=target_date,
                    dte_min=0,
                    dte_max=max_dte,
                    allowed_expiries=selected_expiry_set,
                )
            )

            by_expiry: dict[str, list[dict]] = {}
            for static in static_contracts:
                by_expiry.setdefault(static["expiry"], []).append(static)
            requested_by_code: dict[str, dict] = {}
            for expiry, _dte in selected:
                rows = by_expiry.get(expiry, [])
                chosen = set(
                    select_near_money_strikes(
                        [row["strike"] for row in rows],
                        spot,
                    )
                )
                for row in rows:
                    if row["strike"] in chosen:
                        requested_by_code.setdefault(row["code"], row)

            snapshot_result = _get_option_snapshots(
                ctx,
                list(requested_by_code),
                RET_OK,
                context_lock=context_lock,
            )

        contracts: list[MoomooNearExpiryContract] = []
        for code, static in requested_by_code.items():
            dynamic = snapshot_result.snapshots.get(code)
            if dynamic is None:
                snapshot_state = "missing"
            elif not _snapshot_option_valid(dynamic):
                snapshot_state = "invalid"
            else:
                snapshot_state = "observed"
            if snapshot_state != "observed":
                contracts.append(
                    MoomooNearExpiryContract(
                        code=code,
                        expiry=static["expiry"],
                        dte=static["dte"],
                        right=static["right"],
                        strike=static["strike"],
                        bid=None,
                        ask=None,
                        last_price=None,
                        volume=None,
                        open_interest=None,
                        iv_percent=None,
                        delta=None,
                        update_time=None,
                        snapshot_state=snapshot_state,
                    )
                )
                continue

            delta = _safe_float(_first_present(dynamic, "option_delta", "delta"))
            if delta is not None and not -1 <= delta <= 1:
                delta = None
            contracts.append(
                MoomooNearExpiryContract(
                    code=code,
                    expiry=static["expiry"],
                    dte=static["dte"],
                    right=static["right"],
                    strike=static["strike"],
                    bid=_valid_nonnegative_float(dynamic.get("bid_price")),
                    ask=_valid_nonnegative_float(dynamic.get("ask_price")),
                    last_price=_valid_positive_float(dynamic.get("last_price")),
                    volume=_valid_nonnegative_int(dynamic.get("volume")),
                    open_interest=_valid_nonnegative_int(
                        _first_present(
                            dynamic,
                            "option_open_interest",
                            "open_interest",
                        )
                    ),
                    iv_percent=_valid_positive_float(
                        _first_present(
                            dynamic,
                            "option_implied_volatility",
                            "implied_volatility",
                        )
                    ),
                    delta=delta,
                    update_time=_safe_text(dynamic.get("update_time")),
                    snapshot_state="observed",
                )
            )

        contracts.sort(
            key=lambda item: (item.expiry, item.strike, item.right, item.code)
        )
        return MoomooNearExpiryChainSnapshot(
            symbol=normalized_symbol,
            spot=spot,
            spot_as_of=spot_as_of,
            fetched_at=datetime.now(timezone.utc),
            max_dte=max_dte,
            expiries=tuple(selected),
            contracts=tuple(contracts),
            requested_contract_count=len(requested_by_code),
            snapshot_received_count=len(snapshot_result.snapshots),
            failed_batch_count=snapshot_result.failed_batch_count,
            excluded_nonstandard_count=excluded_nonstandard,
            excluded_unknown_standard_type_count=unknown_standard,
        )
    except Exception as exc:  # noqa: BLE001 - quote failures degrade to unavailable
        logger.warning(
            "[moomoo_options] near-expiry chain(%s) failed: %s",
            normalized_symbol,
            exc,
        )
        return None


def _normalise_option_event_enum(value) -> Optional[str]:
    """Return a stable SDK enum label while preserving unknown as missing."""

    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    enum_name = getattr(value, "name", None)
    text = str(enum_name if enum_name is not None else value).strip().upper()
    if not text or text in {"N/A", "NA", "NAN", "NONE", "NULL", "UNKNOWN"}:
        return None
    return text.rsplit(".", 1)[-1].replace("-", "_").replace(" ", "_")


def _normalise_option_event_order_types(value) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    normalised: list[str] = []
    for raw in raw_values:
        item = _normalise_option_event_enum(raw)
        if item is not None and item not in normalised:
            normalised.append(item)
    return tuple(normalised)


def _valid_nonnegative_float(value) -> Optional[float]:
    parsed = _safe_float(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _valid_positive_float(value) -> Optional[float]:
    parsed = _safe_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _valid_nonnegative_int(value) -> Optional[int]:
    parsed = _safe_int(value)
    return parsed if parsed is not None and parsed >= 0 else None


def fetch_option_underlying_overviews_moomoo(
    symbols: list[str] | tuple[str, ...],
) -> dict[str, MoomooOptionUnderlyingOverview]:
    """Batch-read Moomoo's quote-only option overview for US underlyings.

    This uses only ``OpenQuoteContext.get_option_underlying_overview``.  It
    never constructs a trade context, unlocks trading, or calls an order API.
    Missing SDK support, permissions, connection state, or malformed provider
    rows fail closed by returning an empty/partial mapping.
    """

    if not _enabled():
        return {}

    requested: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        try:
            code = _to_moomoo_underlying(str(raw))
        except ValueError:
            continue
        if not code.startswith("US.") or code in seen:
            continue
        requested.append(code)
        seen.add(code)
    if not requested:
        return {}

    try:
        from moomoo import RET_OK
    except (ImportError, AttributeError):
        return {}

    try:
        with _ctx_lock:
            ctx = _get_ctx()
            if ctx is None:
                return {}
            getter = getattr(ctx, "get_option_underlying_overview", None)
            if not callable(getter):
                logger.info(
                    "[moomoo_options] installed SDK lacks "
                    "get_option_underlying_overview"
                )
                return {}
            ret, frame = getter(requested)
        if ret != RET_OK or frame is None or not hasattr(frame, "iterrows"):
            logger.warning(
                "[moomoo_options] option overview unavailable: %s",
                _brief_detail((ret, frame)),
            )
            return {}
    except Exception as exc:  # noqa: BLE001 - quote-only provider boundary
        logger.warning("[moomoo_options] option overview failed: %s", exc)
        return {}

    fetched_at = datetime.now(timezone.utc)
    result: dict[str, MoomooOptionUnderlyingOverview] = {}
    requested_set = set(requested)
    for _, row in frame.iterrows():
        item = row.to_dict() if hasattr(row, "to_dict") else dict(row)
        code = (_safe_text(item.get("code")) or "").upper()
        if code not in requested_set:
            continue
        symbol = code[3:]
        result[symbol] = MoomooOptionUnderlyingOverview(
            symbol=symbol,
            name=_safe_text(item.get("name")),
            fetched_at=fetched_at,
            call_volume=_valid_nonnegative_int(item.get("call_volume")),
            put_volume=_valid_nonnegative_int(item.get("put_volume")),
            call_open_interest=_valid_nonnegative_int(
                item.get("call_open_interest")
            ),
            put_open_interest=_valid_nonnegative_int(
                item.get("put_open_interest")
            ),
            iv_percent=_valid_nonnegative_float(item.get("iv")),
            iv_rank_percent=_valid_nonnegative_float(item.get("iv_rank")),
            iv_percentile_percent=_valid_nonnegative_float(
                item.get("iv_percentile")
            ),
            previous_iv_percent=_valid_nonnegative_float(item.get("pre_iv")),
            hv_30d_percent=_valid_nonnegative_float(item.get("hv_30d")),
            hv_30d_percentile=_valid_nonnegative_float(
                item.get("hv_30d_percentile")
            ),
            hv_60d_percent=_valid_nonnegative_float(item.get("hv_60d")),
            hv_60d_percentile=_valid_nonnegative_float(
                item.get("hv_60d_percentile")
            ),
            hv_90d_percent=_valid_nonnegative_float(item.get("hv_90d")),
            hv_90d_percentile=_valid_nonnegative_float(
                item.get("hv_90d_percentile")
            ),
            hv_120d_percent=_valid_nonnegative_float(item.get("hv_120d")),
            hv_120d_percentile=_valid_nonnegative_float(
                item.get("hv_120d_percentile")
            ),
            hv_365d_percent=_valid_nonnegative_float(item.get("hv_365d")),
            hv_365d_percentile=_valid_nonnegative_float(
                item.get("hv_365d_percentile")
            ),
        )
    return result


def _option_event_expiry(value) -> Optional[str]:
    text = _safe_text(value)
    if text is None:
        return None
    parsed = _safe_iso_date(text.split(" ", 1)[0])
    return parsed.isoformat() if parsed is not None else None


def _option_event_id(item: dict, *, order_types: tuple[str, ...]) -> str:
    """Build a repeatable identifier from provider-observed transaction data."""

    identity = {
        "option_code": _safe_text(item.get("option_code")),
        "fill_timestamp": _safe_float(item.get("fill_timestamp")),
        "fill_time": _safe_text(item.get("fill_time")),
        "ticker_type": _normalise_option_event_enum(item.get("ticker_type")),
        "price": _safe_float(item.get("price")),
        "volume": _safe_int(item.get("volume")),
        "turnover": _safe_float(item.get("turnover")),
        "order_types": order_types,
        "strategy_type": _normalise_option_event_enum(
            item.get("strategy_type")
        ),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"moomoo-evt-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _option_event_rows(frame) -> Optional[list[dict]]:
    """Accept the current DataFrame and bounded older/future row containers."""

    if frame is None:
        return None
    if hasattr(frame, "iterrows"):
        return [
            row.to_dict() if hasattr(row, "to_dict") else dict(row)
            for _, row in frame.iterrows()
        ]
    if isinstance(frame, (list, tuple)):
        rows: list[dict] = []
        for row in frame:
            if isinstance(row, dict):
                rows.append(dict(row))
            elif hasattr(row, "to_dict"):
                rows.append(row.to_dict())
            else:
                return None
        return rows
    return None


def _unpack_option_event_result(
    result,
    *,
    ret_ok,
) -> Optional[tuple[list[dict], Optional[int]]]:
    """Unpack the 10.9 dict payload plus known legacy direct-frame shape."""

    if not isinstance(result, (tuple, list)) or len(result) < 2:
        return None
    if result[0] != ret_ok:
        return None

    payload = result[1]
    all_count_raw = None
    if isinstance(payload, dict):
        frame = payload.get("event_list")
        all_count_raw = payload.get("all_count")
        if frame is None and _safe_int(all_count_raw) == 0:
            return [], 0
    else:
        # Older SDKs may expose the event frame directly and append pagination
        # metadata.  Do not mistake the current dict payload for a DataFrame.
        frame = payload
        if len(result) >= 4:
            all_count_raw = result[3]

    rows = _option_event_rows(frame)
    if rows is None:
        return None
    all_count = _valid_nonnegative_int(all_count_raw)
    return rows, all_count


def _build_option_event(
    item: dict,
    *,
    requested_symbol: str,
    requested_owner_code: str,
) -> Optional[MoomooOptionEvent]:
    option_code = _safe_text(item.get("option_code"))
    if option_code is None:
        return None

    owner_code = _safe_text(item.get("owner_code"))
    if owner_code is not None:
        owner_code = owner_code.upper()
        if owner_code != requested_owner_code:
            return None
    row_symbol = _safe_text(item.get("symbol"))
    if row_symbol is not None:
        row_symbol = row_symbol.upper()
        if row_symbol != requested_symbol:
            return None

    order_types = _normalise_option_event_order_types(
        item.get("order_type_list")
    )
    delta = _safe_float(item.get("delta"))
    if delta is not None and not -1 <= delta <= 1:
        delta = None
    dte = _valid_nonnegative_int(item.get("dte"))
    vo_ratio = _valid_nonnegative_float(item.get("vo_ratio"))

    return MoomooOptionEvent(
        event_id=_option_event_id(item, order_types=order_types),
        option_code=option_code,
        owner_code=owner_code,
        symbol=row_symbol,
        fill_time=_safe_text(item.get("fill_time")),
        ticker_type=_normalise_option_event_enum(item.get("ticker_type")),
        price=_valid_positive_float(item.get("price")),
        volume=_valid_nonnegative_int(item.get("volume")),
        turnover=_valid_nonnegative_float(item.get("turnover")),
        option_type=_normalise_option_event_enum(item.get("option_type")),
        strike_price=_valid_positive_float(item.get("strike_price")),
        expiry=_option_event_expiry(item.get("strike_time")),
        dte=dte,
        underlying_price=_valid_positive_float(item.get("underlying_price")),
        bid_price=_valid_nonnegative_float(item.get("bid_price")),
        ask_price=_valid_nonnegative_float(item.get("ask_price")),
        # get_option_event.iv is already percent-form (40.791 = 40.791%).
        iv_percent=_valid_nonnegative_float(item.get("iv")),
        total_volume=_valid_nonnegative_int(item.get("total_volume")),
        total_open_interest=_valid_nonnegative_int(
            item.get("total_open_interest")
        ),
        # vo_ratio is a decimal volume/OI ratio (0.05203 = 5.203%).
        vo_ratio_percent=(vo_ratio * 100.0 if vo_ratio is not None else None),
        delta=delta,
        sentiment=_normalise_option_event_enum(item.get("sentiment")),
        order_types=order_types,
        strategy_type=_normalise_option_event_enum(item.get("strategy_type")),
    )


def fetch_option_events_moomoo(
    symbol: str,
    limit: int = 5,
) -> Optional[MoomooOptionEventSnapshot]:
    """Read the first page of Moomoo's unusual option transactions.

    Only ``OpenQuoteContext.get_option_event`` is used.  The provider's BUY /
    SELL and BULLISH / BEARISH labels are preserved as classifications; this
    adapter never converts them into open/close, counterparty, or dealer-flow
    claims.  ``None`` means the query was unavailable, while a snapshot with an
    empty ``events`` tuple means the query succeeded and observed no rows.
    """

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise ValueError("limit must be an integer between 1 and 10")
    if not _enabled():
        return None

    owner_code = _to_moomoo_underlying(symbol)
    if not owner_code.startswith("US."):
        return None
    normalized_symbol = owner_code[3:]
    try:
        from moomoo import (
            RET_OK,
            EventIndicatorType,
            OptionEventFilter,
            OptionMarket,
        )
    except (ImportError, AttributeError):
        return None

    try:
        with _event_ctx_lock:
            ctx = _get_event_ctx()
            if ctx is None:
                return None
            get_option_event = getattr(ctx, "get_option_event", None)
            if not callable(get_option_event):
                logger.info(
                    "[moomoo_options] installed SDK lacks get_option_event"
                )
                return None
            owner_filter = OptionEventFilter(
                EventIndicatorType.OWNER_LIST,
                security_list=[owner_code],
            )
            raw_result = get_option_event(
                OptionMarket.US_SECURITY,
                count=limit,
                filter_list=[owner_filter],
            )

        unpacked = _unpack_option_event_result(raw_result, ret_ok=RET_OK)
        if unpacked is None:
            logger.warning(
                "[moomoo_options] unusual option events unavailable for %s: %s",
                owner_code,
                _brief_detail(raw_result),
            )
            return None
        rows, all_count = unpacked
        events = tuple(
            event
            for row in rows[:limit]
            if (
                event := _build_option_event(
                    row,
                    requested_symbol=normalized_symbol,
                    requested_owner_code=owner_code,
                )
            )
            is not None
        )
        if rows and not events:
            logger.warning(
                "[moomoo_options] unusual option events for %s contained no "
                "usable contract rows",
                owner_code,
            )
            return None
        if not rows and all_count not in {None, 0}:
            logger.warning(
                "[moomoo_options] unusual option event count/frame mismatch "
                "for %s (all_count=%s)",
                owner_code,
                all_count,
            )
            return None
        event_as_of = max(
            (event.fill_time for event in events if event.fill_time is not None),
            default=None,
        )
        return MoomooOptionEventSnapshot(
            symbol=normalized_symbol,
            fetched_at=datetime.now(timezone.utc),
            event_as_of=event_as_of,
            all_count=all_count,
            events=events,
        )
    except Exception as exc:  # noqa: BLE001 - per-symbol graceful degradation
        logger.warning(
            "[moomoo_options] unusual option events(%s) failed: %s",
            owner_code,
            exc,
        )
        return None


def compute_atm_iv_moomoo(
    symbol: str, ref_date: Optional[date] = None
) -> tuple[Optional[float], str]:
    """Return ``(atm_iv_decimal, chosen_expiry_iso)`` via Moomoo.

    The ATM path reads one exact contract snapshot instead of selecting from a
    best-effort full-chain subset. A failed or missing target snapshot therefore
    fails closed and lets the caller fall back to another provider.
    """
    if not _enabled():
        return None, ""
    expirations = get_expirations_moomoo(symbol)
    parsed_expirations = sorted(
        (parsed, raw)
        for raw in expirations
        if (parsed := _safe_iso_date(raw)) is not None
    )
    if not parsed_expirations:
        return None, ""

    target = ref_date or _new_york_market_date()
    chosen = next(
        (raw for parsed, raw in parsed_expirations if parsed >= target),
        None,
    )
    if chosen is None:
        # Never present an expired contract as the current ATM-IV context.
        return None, ""

    try:
        from moomoo import RET_OK
    except ImportError:
        return None, chosen

    underlying = _to_moomoo_underlying(symbol)
    with _ctx_lock:
        ctx = _get_ctx()
        if ctx is None:
            return None, chosen
        spot = _spot_from_ctx(ctx, symbol, RET_OK)
        if spot is None or spot <= 0:
            return None, chosen
        data = _get_static_chain_frame(ctx, underlying, chosen, RET_OK)
        if data is None:
            return None, chosen
        calls = [
            contract
            for contract in _static_contracts(data)
            if contract["right"] == "C"
        ]
        if not calls:
            return None, chosen
        atm = min(calls, key=lambda contract: abs(contract["strike"] - spot))
        snapshot_result = _get_option_snapshots(ctx, [atm["code"]], RET_OK)
        if not snapshot_result.complete:
            logger.info(
                "[moomoo_options] exact ATM snapshot unavailable for %s %s; "
                "falling back",
                symbol,
                chosen,
            )
            return None, chosen
        snapshot = snapshot_result.snapshots.get(atm["code"])
        if snapshot is None or not _snapshot_option_valid(snapshot):
            return None, chosen
        iv_pct = _safe_float(
            _first_present(
                snapshot,
                "option_implied_volatility",
                "implied_volatility",
            )
        )
        if iv_pct is None or iv_pct <= 0:
            logger.info(
                "[moomoo_options] %s ATM call at %s has no valid IV; "
                "falling back",
                symbol,
                chosen,
            )
            return None, chosen
        return iv_pct / 100.0, chosen


def _safe_iso_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None
