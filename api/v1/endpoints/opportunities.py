# -*- coding: utf-8 -*-
"""Daily opportunity research endpoint.

The endpoint loads daily bars and the stored Regime snapshot, then delegates
all signal logic to the pure ``src.opportunities`` engine.  It never calls an
LLM, writes a signal record, or invokes a broker trading action.
"""
from __future__ import annotations

import copy
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from api.v1.schemas.opportunities import (
    DailyOpportunityRequest,
    DailyOpportunityResponse,
    OpportunityLearningSummaryResponse,
    OpportunitySnapshotDetailResponse,
    OpportunitySnapshotEnsureResponse,
    OpportunitySnapshotEvaluationResponse,
    OpportunitySnapshotItem,
    OpportunitySnapshotListResponse,
    OptionContextRequest,
    OptionContextResponse,
    OptionOverviewRequest,
    OptionOverviewResponse,
    OptionEventRequest,
    OptionEventResponse,
    OptionWallRequest,
    OptionWallResponse,
    PremarketCycleRequest,
    PremarketCycleResponse,
    PremarketUniversePutRequest,
    PremarketUniverseResponse,
)
from src.opportunities.repository import SnapshotConflictError
from src.opportunities.engine import (
    SIGNAL_VERSION,
    DailyHistoryInput,
    build_daily_opportunity_run,
    is_supported_us_option_underlying,
    normalize_symbols,
)
from src.opportunities.option_walls import (
    ATM_CALL_IV_METHOD as OPTION_WALL_ATM_CALL_IV_METHOD,
    FORMULA_VERSION as OPTION_WALL_FORMULA_VERSION,
    build_option_wall_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_NEW_YORK = ZoneInfo("America/New_York")
_MAX_FETCH_WORKERS = 4
_MAX_OPTION_WALL_WORKERS = 5
_SCAN_CACHE_TTL_SECONDS = 30.0
_SCAN_CACHE_MAX_ENTRIES = 32
_SCAN_FLIGHT_LEASE_SECONDS = 30.0
_SCAN_FOLLOWER_WAIT_SECONDS = 30.0
_OPTION_CONTEXT_VERSION = "nearest_expiry_atm_call_iv_v1"
_OPTION_CONTEXT_SOURCE = "moomoo_openapi"
_OPTION_OVERVIEW_VERSION = "moomoo_option_underlying_overview_v1"
_OPTION_OVERVIEW_SOURCE = "moomoo_openapi"
_OPTION_WALL_VERSION = "observable_option_walls_v1_1"
_OPTION_WALL_SOURCE = "moomoo_openapi"
_OPTION_EVENT_VERSION = "moomoo_unusual_option_events_v1"
_OPTION_EVENT_SOURCE = "moomoo_openapi"
_OPTION_CONTEXT_LIMITATIONS = (
    "仅为 Moomoo 最近到期合约中最接近现价的 Call 单点隐含波动率。",
    "不是 IV Rank 或 IV Percentile，不包含历史隐含波动率分布。",
    "不是异常期权流或期权大单检测，不推断成交方向、开平仓或买卖意图。",
    "仅作研究上下文，不是买卖信号。",
)
_OPTION_OVERVIEW_LIMITATIONS = (
    "Call/Put Volume 是 Moomoo 当前交易时段累计活动，不代表开仓、平仓或方向性资金流。",
    "Open Interest 是 T-1 清算后的未平仓合约总量，本身不代表看多或看空。",
    "IV、IV Rank、IV Percentile 与 HV 均为供应商模型/统计值；IV 不提供涨跌方向。",
    "Put/Call 比率只描述活动或存量结构，不是独立买卖信号。",
)
_OPTION_WALL_LIMITATIONS = (
    "OI 是清算后的未平仓合约总量，不显示买卖方、开平仓或做市商库存方向。",
    "Volume 是当日累计成交量，不代表新增仓位仍然存在。",
    "Gamma 集中墙使用绝对 Gamma，只表示风险集中度，不是真实 Dealer GEX。",
    "墙位不保证支撑、阻力、钉仓、突破或反转，仅作期权结构研究上下文。",
)
_OPTION_WALL_ASSUMPTIONS = (
    "Gamma concentration = abs(gamma) × OI × contract size × spot² × 1%。",
    "不从公开 OI 推断 dealer 净多或净空 Gamma。",
)
_OPTION_EVENT_LIMITATIONS = (
    "仅返回 Moomoo get_option_event 为该标的识别的最近一页异动成交，不是完整逐笔期权流。",
    "ticker_type 与 sentiment 是 Moomoo 分类，不独立证明主动买卖、开平仓或真实交易意图。",
    "公开成交无法识别对手方或 dealer 仓位，不据此推断 dealer 方向。",
    "此数据暂不进入今日机会排序，仅作候选标的研究上下文，不是买卖信号。",
)
_OPTION_EVENT_FIELDS = (
    "event_id",
    "option_code",
    "owner_code",
    "symbol",
    "fill_time",
    "ticker_type",
    "price",
    "volume",
    "turnover",
    "option_type",
    "strike_price",
    "expiry",
    "dte",
    "underlying_price",
    "bid_price",
    "ask_price",
    "iv_percent",
    "total_volume",
    "total_open_interest",
    "vo_ratio_percent",
    "delta",
    "sentiment",
    "order_types",
    "strategy_type",
)


@dataclass
class _ScanCacheEntry:
    expires_at: float
    result: dict[str, Any]


@dataclass
class _ScanFlight:
    generation: int
    started_at: float
    deadline_at: float
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[dict[str, Any]] = None
    error: Optional[BaseException] = None


class OpportunityScanTimeoutError(TimeoutError):
    """Raised when a shared provider scan exceeds its bounded lease."""


_scan_cache_lock = threading.RLock()
_scan_cache: dict[tuple[Any, ...], _ScanCacheEntry] = {}
_scan_flights: dict[tuple[Any, ...], _ScanFlight] = {}
_scan_flight_generation = 0


def _cache_now() -> float:
    return time.monotonic()


def _scan_cache_key(
    symbols: list[str],
    limit: int,
    market_date_et: Optional[date | str] = None,
) -> tuple[Any, ...]:
    market_date = market_date_et or datetime.now(timezone.utc).astimezone(
        _NEW_YORK
    ).date()
    return (SIGNAL_VERSION, str(market_date), tuple(symbols), int(limit))


def _option_context_cache_key(
    symbol: str,
    enabled: bool,
    market_date_et: str,
) -> tuple[Any, ...]:
    """Cache one symbol so overlapping three-symbol batches share quota work."""

    return (
        "option_context",
        _OPTION_CONTEXT_VERSION,
        bool(enabled),
        symbol,
        market_date_et,
    )


def _option_overview_cache_key(
    symbols: list[str],
    enabled: bool,
    market_date_et: str,
) -> tuple[Any, ...]:
    return (
        "option_overview",
        _OPTION_OVERVIEW_VERSION,
        bool(enabled),
        tuple(symbols),
        market_date_et,
    )


def _option_wall_cache_key(
    symbol: str,
    enabled: bool,
    market_date_et: str,
    dte_min: int,
    dte_max: int,
) -> tuple[Any, ...]:
    return (
        "option_wall",
        _OPTION_WALL_VERSION,
        bool(enabled),
        symbol,
        market_date_et,
        int(dte_min),
        int(dte_max),
    )


def _option_event_cache_key(
    symbol: str,
    enabled: bool,
    market_date_et: str,
    limit_per_symbol: int,
) -> tuple[Any, ...]:
    return (
        "option_event",
        _OPTION_EVENT_VERSION,
        bool(enabled),
        symbol,
        market_date_et,
        int(limit_per_symbol),
    )


def _reset_scan_cache_for_tests() -> None:
    """Clear completed entries between deterministic tests.

    Production code never calls this helper.
    """

    with _scan_cache_lock:
        _scan_cache.clear()
        for flight in _scan_flights.values():
            if flight.error is None:
                flight.error = OpportunityScanTimeoutError(
                    "opportunity scan reset during test"
                )
            flight.event.set()
        _scan_flights.clear()


def _prune_scan_cache(now: float) -> None:
    expired = [key for key, entry in _scan_cache.items() if entry.expires_at <= now]
    for key in expired:
        _scan_cache.pop(key, None)
    while len(_scan_cache) >= _SCAN_CACHE_MAX_ENTRIES:
        oldest_key = min(_scan_cache, key=lambda item: _scan_cache[item].expires_at)
        _scan_cache.pop(oldest_key, None)


def _get_or_compute_scan(
    key: tuple[Any, ...],
    factory: Callable[[], dict[str, Any]],
    *,
    bypass_cache: bool = False,
    wait_timeout_seconds: float = _SCAN_FOLLOWER_WAIT_SECONDS,
    lease_seconds: float = _SCAN_FLIGHT_LEASE_SECONDS,
) -> dict[str, Any]:
    """Return a cached scan or share one in-flight computation per key.

    ``bypass_cache`` skips only a completed TTL entry.  A matching in-flight
    request is still shared so repeated refresh clicks cannot multiply provider
    traffic.
    """

    if wait_timeout_seconds <= 0 or lease_seconds <= 0:
        raise ValueError("scan wait and lease must be positive")

    global _scan_flight_generation
    now = _cache_now()
    with _scan_cache_lock:
        _prune_scan_cache(now)
        cached = _scan_cache.get(key)
        if not bypass_cache and cached is not None and cached.expires_at > now:
            return copy.deepcopy(cached.result)

        flight = _scan_flights.get(key)
        if flight is not None and flight.deadline_at <= now:
            flight.error = OpportunityScanTimeoutError(
                "opportunity scan exceeded its lease"
            )
            _scan_flights.pop(key, None)
            flight.event.set()
            flight = None
        is_leader = flight is None
        if flight is None:
            _scan_flight_generation += 1
            flight = _ScanFlight(
                generation=_scan_flight_generation,
                started_at=now,
                deadline_at=now + lease_seconds,
            )
            _scan_flights[key] = flight

    if not is_leader:
        remaining = min(
            wait_timeout_seconds,
            max(0.0, flight.deadline_at - _cache_now()),
        )
        completed = flight.event.wait(timeout=remaining)
        if not completed:
            now = _cache_now()
            with _scan_cache_lock:
                current = _scan_flights.get(key)
                if current is flight and now >= flight.deadline_at:
                    flight.error = OpportunityScanTimeoutError(
                        "opportunity scan exceeded its lease"
                    )
                    _scan_flights.pop(key, None)
                    flight.event.set()
            raise OpportunityScanTimeoutError(
                "opportunity scan is still running; retry shortly"
            )
        if flight.error is not None:
            raise flight.error
        if flight.result is None:
            raise RuntimeError("opportunity scan single-flight completed without a result")
        return copy.deepcopy(flight.result)

    try:
        result = factory()
    except BaseException as exc:
        with _scan_cache_lock:
            if _scan_flights.get(key) is flight:
                _scan_flights.pop(key, None)
            if flight.error is None:
                flight.error = exc
            flight.event.set()
        raise

    stored = copy.deepcopy(result)
    completion_time = _cache_now()
    with _scan_cache_lock:
        current = _scan_flights.get(key)
        if current is not flight or completion_time > flight.deadline_at:
            if current is flight:
                _scan_flights.pop(key, None)
            if flight.error is None:
                flight.error = OpportunityScanTimeoutError(
                    "opportunity scan completed after its lease"
                )
            flight.event.set()
            raise flight.error
        _prune_scan_cache(completion_time)
        _scan_cache[key] = _ScanCacheEntry(
            expires_at=completion_time + _SCAN_CACHE_TTL_SECONDS,
            result=stored,
        )
        flight.result = stored
        _scan_flights.pop(key, None)
        flight.event.set()
    return copy.deepcopy(stored)


def _scan_timeout_response(exc: OpportunityScanTimeoutError) -> HTTPException:
    return HTTPException(
        status_code=504,
        detail={
            "error": "opportunity_scan_timeout",
            "message": "行情研究仍在执行或已超时，请稍后重试。",
            "retryable": True,
            "retry_after_seconds": 2,
        },
    )


def _configured_symbols() -> list[str]:
    from src.config import get_config

    configured = getattr(get_config(), "stock_list", None) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    return normalize_symbols([str(item) for item in configured])[:20]


def _premarket_scheduler_enabled() -> bool:
    """Expose the configured host state without making it a page-load trigger."""

    from src.config import get_config

    return bool(
        getattr(get_config(), "premarket_research_scheduler_enabled", False)
    )


def _persisted_premarket_universe():
    from src.services.premarket_research_service import (
        resolve_premarket_research_universe,
    )

    return resolve_premarket_research_universe(
        include_fallback_suggestion=False,
    )


def _create_data_fetcher_manager():
    from data_provider.base import DataFetcherManager

    return DataFetcherManager()


def _load_daily_history(
    symbol: str,
    *,
    as_of: datetime,
    manager,
) -> DailyHistoryInput:
    """Load one symbol through a request-scoped shared DataFetcherManager."""

    try:
        from src.services.stock_service import StockService

        frame, source = manager.get_daily_data(symbol, days=80)
        rows = (
            [StockService._row_to_kline(row, intraday=False) for _, row in frame.iterrows()]
            if frame is not None and not frame.empty
            else []
        )
        return DailyHistoryInput(
            bars=tuple(rows),
            source=source,
            fetched_at=as_of,
            error=None if rows else "daily history returned no rows",
        )
    except Exception as exc:  # noqa: BLE001 - per-symbol graceful degradation
        logger.debug(
            "[opportunities] daily history unavailable symbol=%s error_type=%s",
            symbol,
            type(exc).__name__,
        )
        return DailyHistoryInput(
            bars=(),
            source=None,
            fetched_at=as_of,
            error=f"provider_error:{type(exc).__name__}",
        )


def _load_current_regime(market_date: date) -> Optional[dict[str, Any]]:
    """Read the exact market-date Regime row without creating or mutating it."""

    try:
        from src.regime.models import RegimeScore
        from src.regime.storage import _row_to_dict
        from src.storage import get_db

        db = get_db()
        session = db.get_session()
        try:
            row = session.execute(
                select(RegimeScore).where(RegimeScore.date == market_date).limit(1)
            ).scalar_one_or_none()
            return _row_to_dict(row) if row is not None else None
        finally:
            # A plain SELECT must not pass through the repository's committing
            # session_scope; close the read-only session without a commit.
            session.close()
    except Exception as exc:  # noqa: BLE001 - missing table/storage is an explicit unavailable state
        logger.warning("[opportunities] stored Regime unavailable for %s: %s", market_date, exc)
        return None


def _load_histories_gracefully(
    symbols: list[str],
    *,
    as_of: datetime,
    manager,
) -> dict[str, DailyHistoryInput]:
    histories: dict[str, DailyHistoryInput] = {
        symbol: DailyHistoryInput(
            bars=(),
            source=None,
            fetched_at=as_of,
            error="unsupported_non_us_options_universe",
        )
        for symbol in symbols
        if not is_supported_us_option_underlying(symbol)
    }
    supported = [symbol for symbol in symbols if is_supported_us_option_underlying(symbol)]
    if not supported:
        return histories

    def load(symbol: str) -> DailyHistoryInput:
        try:
            return _load_daily_history(symbol, as_of=as_of, manager=manager)
        except Exception as exc:  # noqa: BLE001 - injected loaders may also fail
            logger.debug(
                "[opportunities] injected daily loader failed symbol=%s error_type=%s",
                symbol,
                type(exc).__name__,
            )
            return DailyHistoryInput(
                bars=(),
                source=None,
                fetched_at=as_of,
                error=f"provider_error:{type(exc).__name__}",
            )

    worker_count = min(_MAX_FETCH_WORKERS, len(supported))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="opportunity-bars") as executor:
        futures = {executor.submit(load, symbol): symbol for symbol in supported}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except Exception as exc:  # pragma: no cover - load itself is fail-open
                logger.debug(
                    "[opportunities] worker failed symbol=%s error_type=%s",
                    symbol,
                    type(exc).__name__,
                )
                histories[symbol] = DailyHistoryInput(
                    bars=(),
                    source=None,
                    fetched_at=as_of,
                    error=f"provider_error:{type(exc).__name__}",
                )
    return histories


_AUTO_REGIME = object()


def _execute_daily_scan(
    symbols: list[str],
    limit: int,
    *,
    as_of: Optional[datetime] = None,
    regime: Any = _AUTO_REGIME,
) -> dict[str, Any]:
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    market_date = as_of.astimezone(_NEW_YORK).date()
    supported = any(is_supported_us_option_underlying(symbol) for symbol in symbols)
    manager = None
    try:
        if supported:
            manager = _create_data_fetcher_manager()
            histories = _load_histories_gracefully(symbols, as_of=as_of, manager=manager)
        else:
            histories = _load_histories_gracefully(symbols, as_of=as_of, manager=None)
    except Exception as exc:  # noqa: BLE001 - manager creation must not fail the batch
        logger.info(
            "[opportunities] daily data manager unavailable error_type=%s",
            type(exc).__name__,
        )
        histories = {
            symbol: DailyHistoryInput(
                bars=(),
                source=None,
                fetched_at=as_of,
                error=f"provider_error:{type(exc).__name__}",
            )
            for symbol in symbols
        }
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as exc:  # noqa: BLE001 - best-effort read-only cleanup
                logger.debug(
                    "[opportunities] data manager close failed error_type=%s",
                    type(exc).__name__,
                )

    selected_regime = (
        _load_current_regime(market_date) if regime is _AUTO_REGIME else regime
    )
    return build_daily_opportunity_run(
        symbols=symbols,
        histories=histories,
        regime=selected_regime,
        as_of=as_of,
        limit=limit,
    )


def _moomoo_opend_enabled() -> bool:
    return (os.environ.get("MOOMOO_OPEND_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _compute_atm_iv_moomoo(symbol: str) -> tuple[Optional[float], str]:
    """Call the existing Quote-only Moomoo option context adapter."""

    from data_provider.moomoo_options import compute_atm_iv_moomoo

    return compute_atm_iv_moomoo(symbol)


def _compute_option_overviews_moomoo(symbols: list[str]):
    """Batch-read provider option overview through a QuoteContext only."""

    from data_provider.moomoo_options import (
        fetch_option_underlying_overviews_moomoo,
    )

    return fetch_option_underlying_overviews_moomoo(symbols)


def _previous_xnys_session_label(market_date: date) -> Optional[str]:
    """Return the latest XNYS session strictly before ``market_date``."""

    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS")
        sessions = calendar.sessions_in_range(
            market_date - timedelta(days=10),
            market_date,
        )
        candidates = [
            value.date()
            for value in sessions
            if value.date() < market_date
        ]
        return candidates[-1].isoformat() if candidates else None
    except Exception as exc:  # noqa: BLE001 - label absence is explicit
        logger.debug("[opportunities] previous XNYS session unavailable: %s", exc)
        return None


def _ratio(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _difference(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None or right is None:
        return None
    value = float(left) - float(right)
    return round(value, 6) if math.isfinite(value) else None


def _empty_option_overview_item(
    *,
    ticker: str,
    state: str,
    fetched_at: datetime,
    market_date_et: str,
    open_interest_as_of: Optional[str],
    message: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "name": None,
        "state": state,
        "source": _OPTION_OVERVIEW_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "session_volume_date": market_date_et,
        "open_interest_as_of": open_interest_as_of,
        "volume_basis": "current_session_cumulative",
        "open_interest_basis": "prior_clearing_session",
        "volatility_basis": "provider_snapshot",
        "call_volume": None,
        "put_volume": None,
        "put_call_volume_ratio": None,
        "call_open_interest": None,
        "put_open_interest": None,
        "put_call_open_interest_ratio": None,
        "iv_percent": None,
        "iv_rank_percent": None,
        "iv_percentile_percent": None,
        "previous_iv_percent": None,
        "iv_change_points": None,
        "hv_30d_percent": None,
        "hv_30d_percentile": None,
        "hv_60d_percent": None,
        "hv_60d_percentile": None,
        "hv_90d_percent": None,
        "hv_90d_percentile": None,
        "hv_120d_percent": None,
        "hv_120d_percentile": None,
        "hv_365d_percent": None,
        "hv_365d_percentile": None,
        "iv_hv30_spread_points": None,
        "message": message,
        "limitations": list(_OPTION_OVERVIEW_LIMITATIONS),
    }


def _execute_option_overview(
    symbols: list[str], *, enabled: bool
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date = requested_at.astimezone(_NEW_YORK).date()
    market_date_et = market_date.isoformat()
    oi_as_of = _previous_xnys_session_label(market_date)
    if not enabled:
        items = [
            _empty_option_overview_item(
                ticker=symbol,
                state="not_configured",
                fetched_at=requested_at,
                market_date_et=market_date_et,
                open_interest_as_of=oi_as_of,
                message="MOOMOO_OPEND_ENABLED 未启用；未读取期权概览。",
            )
            for symbol in symbols
        ]
    else:
        try:
            overviews = _compute_option_overviews_moomoo(symbols)
        except Exception as exc:  # noqa: BLE001 - fail one batch closed
            logger.debug("[opportunities] option overview unavailable: %s", exc)
            overviews = {}
        items = []
        for symbol in symbols:
            item = overviews.get(symbol)
            if item is None:
                items.append(
                    _empty_option_overview_item(
                        ticker=symbol,
                        state="unavailable",
                        fetched_at=requested_at,
                        market_date_et=market_date_et,
                        open_interest_as_of=oi_as_of,
                        message=(
                            "Moomoo 未返回该标的的批量期权概览；"
                            "未以 ATM 单点或历史波动率冒充。"
                        ),
                    )
                )
                continue
            values = {
                field: getattr(item, field, None)
                for field in (
                    "call_volume",
                    "put_volume",
                    "call_open_interest",
                    "put_open_interest",
                    "iv_percent",
                    "iv_rank_percent",
                    "iv_percentile_percent",
                    "previous_iv_percent",
                    "hv_30d_percent",
                    "hv_30d_percentile",
                    "hv_60d_percent",
                    "hv_60d_percentile",
                    "hv_90d_percent",
                    "hv_90d_percentile",
                    "hv_120d_percent",
                    "hv_120d_percentile",
                    "hv_365d_percent",
                    "hv_365d_percentile",
                )
            }
            fetched_at = getattr(item, "fetched_at", requested_at)
            fetched_at_text = (
                fetched_at.isoformat()
                if isinstance(fetched_at, datetime)
                else requested_at.isoformat()
            )
            items.append(
                {
                    "ticker": symbol,
                    "name": getattr(item, "name", None),
                    "state": "ready",
                    "source": _OPTION_OVERVIEW_SOURCE,
                    "fetched_at": fetched_at_text,
                    "session_volume_date": market_date_et,
                    "open_interest_as_of": oi_as_of,
                    "volume_basis": "current_session_cumulative",
                    "open_interest_basis": "prior_clearing_session",
                    "volatility_basis": "provider_snapshot",
                    **values,
                    "put_call_volume_ratio": _ratio(
                        values["put_volume"], values["call_volume"]
                    ),
                    "put_call_open_interest_ratio": _ratio(
                        values["put_open_interest"],
                        values["call_open_interest"],
                    ),
                    "iv_change_points": _difference(
                        values["iv_percent"], values["previous_iv_percent"]
                    ),
                    "iv_hv30_spread_points": _difference(
                        values["iv_percent"], values["hv_30d_percent"]
                    ),
                    "message": (
                        "批量概览已读取：Volume 为当前交易时段累计，"
                        "OI 为上一清算日，IV/HV 为供应商统计快照。"
                    ),
                    "limitations": list(_OPTION_OVERVIEW_LIMITATIONS),
                }
            )
    return {
        "schema_version": "option-overview/1.0",
        "generated_at": requested_at.isoformat(),
        "market_date_et": market_date_et,
        "items": items,
    }


def _compute_option_wall_moomoo(
    symbol: str,
    *,
    dte_min: int,
    dte_max: int,
):
    """Read a normalized full-chain snapshot without broker trade actions."""

    from data_provider.moomoo_options import fetch_option_wall_snapshot_moomoo

    return fetch_option_wall_snapshot_moomoo(
        symbol,
        dte_min=dte_min,
        dte_max=dte_max,
    )


def _compute_option_events_moomoo(symbol: str, *, limit: int):
    """Read Moomoo-classified option events through the Quote-only adapter."""

    from data_provider.moomoo_options import fetch_option_events_moomoo

    return fetch_option_events_moomoo(symbol, limit=limit)


def _option_context_item(
    ticker: str,
    *,
    enabled: bool,
    fetched_at: datetime,
) -> dict[str, Any]:
    limitations = list(_OPTION_CONTEXT_LIMITATIONS)
    fetched_at_text = fetched_at.isoformat()
    if not enabled:
        return {
            "ticker": ticker,
            "state": "not_configured",
            "source": _OPTION_CONTEXT_SOURCE,
            "fetched_at": fetched_at_text,
            "expiry": None,
            "atm_call_iv_percent": None,
            "message": (
                "MOOMOO_OPEND_ENABLED 未启用；未请求 Moomoo 最近到期 ATM Call IV。"
            ),
            "limitations": limitations,
        }

    expiry: Optional[str] = None
    try:
        iv_decimal, raw_expiry = _compute_atm_iv_moomoo(ticker)
        expiry = str(raw_expiry or "").strip() or None
        iv_value = float(iv_decimal) if iv_decimal is not None else None
        if (
            iv_value is not None
            and math.isfinite(iv_value)
            and iv_value > 0
            and expiry is not None
        ):
            return {
                "ticker": ticker,
                "state": "ready",
                "source": _OPTION_CONTEXT_SOURCE,
                "fetched_at": fetched_at_text,
                "expiry": expiry,
                "atm_call_iv_percent": round(iv_value * 100.0, 6),
                "message": (
                    f"已读取最近到期 {expiry} 最接近现价的 Call 单点 IV；"
                    "仅作研究上下文。"
                ),
                "limitations": limitations,
            }
    except Exception as exc:  # noqa: BLE001 - one symbol must not fail the batch
        logger.debug(
            "[opportunities] Moomoo ATM Call IV unavailable for %s: %s",
            ticker,
            exc,
        )

    return {
        "ticker": ticker,
        "state": "unavailable",
        "source": _OPTION_CONTEXT_SOURCE,
        "fetched_at": fetched_at_text,
        "expiry": expiry,
        "atm_call_iv_percent": None,
        "message": (
            "Moomoo OpenAPI 未返回有效的最近到期 ATM Call 单点 IV；"
            "未使用其他来源冒充或回填。"
        ),
        "limitations": limitations,
    }


def _execute_option_context(
    symbols: list[str], *, enabled: bool
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    items = [
        _get_or_compute_scan(
            _option_context_cache_key(symbol, enabled, market_date_et),
            lambda symbol=symbol: _option_context_item(
                symbol,
                enabled=enabled,
                fetched_at=datetime.now(timezone.utc),
            ),
        )
        for symbol in symbols
    ]
    generated_at = max(
        (str(item["fetched_at"]) for item in items),
        default=requested_at.isoformat(),
    )
    return {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "market_date_et": market_date_et,
        "items": items,
    }


def _empty_option_wall_payload(
    *,
    ticker: str,
    state: str,
    fetched_at: datetime,
    dte_min: int,
    dte_max: int,
    message: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "state": state,
        "source": _OPTION_WALL_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "quote_as_of": None,
        "formula_version": OPTION_WALL_FORMULA_VERSION,
        "spot": None,
        "atm_call_iv": {
            "state": (
                "not_configured"
                if state == "not_configured"
                else "unavailable"
            ),
            "expiry": None,
            "strike": None,
            "atm_call_iv_percent": None,
            "selection_method": OPTION_WALL_ATM_CALL_IV_METHOD,
        },
        "scope": {
            "dte_min": dte_min,
            "dte_max": dte_max,
            "expiries": [],
            "standard_contracts_only": True,
        },
        "coverage": {
            "requested_contracts": 0,
            "snapshot_received_contracts": 0,
            "valid_contracts": 0,
            "coverage_percent": 0.0,
            "failed_batches": 0,
            "excluded_nonstandard_contracts": 0,
            "excluded_unknown_standard_type_contracts": 0,
            "gamma_contracts": 0,
        },
        "walls": {
            "call_oi": [],
            "put_oi": [],
            "call_volume": [],
            "put_volume": [],
            "call_gamma_concentration": [],
            "put_gamma_concentration": [],
            "gross_gamma_concentration": [],
        },
        "message": message,
        "assumptions": list(_OPTION_WALL_ASSUMPTIONS),
        "limitations": list(_OPTION_WALL_LIMITATIONS),
    }


def _option_wall_item(
    ticker: str,
    *,
    enabled: bool,
    fetched_at: datetime,
    dte_min: int,
    dte_max: int,
) -> dict[str, Any]:
    if not enabled:
        return _empty_option_wall_payload(
            ticker=ticker,
            state="not_configured",
            fetched_at=fetched_at,
            dte_min=dte_min,
            dte_max=dte_max,
            message="MOOMOO_OPEND_ENABLED 未启用；未读取期权链墙位。",
        )

    try:
        snapshot = _compute_option_wall_moomoo(
            ticker,
            dte_min=dte_min,
            dte_max=dte_max,
        )
        if snapshot is None:
            raise ValueError("Moomoo did not return a usable option-chain snapshot")
        payload = build_option_wall_payload(
            snapshot,
            dte_min=dte_min,
            dte_max=dte_max,
        )
        coverage = payload["coverage"]
        walls = payload["walls"]
        has_observed_walls = bool(walls["call_oi"] or walls["put_oi"])
        if not has_observed_walls:
            raise ValueError("snapshot contained no positive open-interest wall")

        complete = (
            coverage["requested_contracts"] > 0
            and coverage["coverage_percent"] == 100.0
            and coverage["failed_batches"] == 0
            and coverage["gamma_contracts"] == coverage["valid_contracts"]
        )
        state = "ready" if complete else "partial"
        snapshot_fetched_at = getattr(snapshot, "fetched_at", fetched_at)
        if isinstance(snapshot_fetched_at, datetime):
            fetched_at_text = snapshot_fetched_at.isoformat()
        else:
            fetched_at_text = str(snapshot_fetched_at or fetched_at.isoformat())
        message = (
            f"聚合 {dte_min}–{dte_max} DTE 标准合约；"
            f"有效快照 {coverage['valid_contracts']}/{coverage['requested_contracts']}。"
        )
        if state == "partial":
            message += " OI/Volume 墙仍可观察，Gamma 或快照覆盖不完整。"
        return {
            "ticker": ticker,
            "state": state,
            "source": _OPTION_WALL_SOURCE,
            "fetched_at": fetched_at_text,
            **payload,
            "message": message,
            "assumptions": list(_OPTION_WALL_ASSUMPTIONS),
            "limitations": list(_OPTION_WALL_LIMITATIONS),
        }
    except Exception as exc:  # noqa: BLE001 - one symbol must not fail the batch
        logger.debug("[opportunities] option walls unavailable for %s: %s", ticker, exc)
        return _empty_option_wall_payload(
            ticker=ticker,
            state="unavailable",
            fetched_at=fetched_at,
            dte_min=dte_min,
            dte_max=dte_max,
            message=(
                "Moomoo OpenAPI 未返回可用的完整链墙位数据；"
                "未以默认值、旧数据或第三方估算回填。"
            ),
        )


def _execute_option_walls(
    symbols: list[str],
    *,
    enabled: bool,
    dte_min: int,
    dte_max: int,
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    started_at = time.monotonic()

    def load_one(symbol: str) -> dict[str, Any]:
        return _get_or_compute_scan(
            _option_wall_cache_key(
                symbol,
                enabled,
                market_date_et,
                dte_min,
                dte_max,
            ),
            lambda symbol=symbol: _option_wall_item(
                symbol,
                enabled=enabled,
                fetched_at=datetime.now(timezone.utc),
                dte_min=dte_min,
                dte_max=dte_max,
            ),
        )

    if enabled and len(symbols) > 1:
        # One full 0–45 DTE scan usually needs several 400-contract provider
        # batches.  Dedicated QuoteContext lanes make independent underlyings
        # overlap; stable index placement preserves the request order.
        items: list[Optional[dict[str, Any]]] = [None] * len(symbols)
        with ThreadPoolExecutor(
            max_workers=min(_MAX_OPTION_WALL_WORKERS, len(symbols)),
            thread_name_prefix="option-wall",
        ) as executor:
            futures = {
                executor.submit(load_one, symbol): index
                for index, symbol in enumerate(symbols)
            }
            for future in as_completed(futures):
                items[futures[future]] = future.result()
        ordered_items = [item for item in items if item is not None]
    else:
        ordered_items = [load_one(symbol) for symbol in symbols]

    logger.debug(
        "[opportunities] option-wall batch completed symbols=%s duration_ms=%s",
        len(symbols),
        round((time.monotonic() - started_at) * 1000),
    )
    generated_at = max(
        (str(item["fetched_at"]) for item in ordered_items),
        default=requested_at.isoformat(),
    )
    return {
        "schema_version": "option-wall/1.1",
        "generated_at": generated_at,
        "market_date_et": market_date_et,
        "items": ordered_items,
    }


def _empty_option_event_payload(
    *,
    ticker: str,
    state: str,
    fetched_at: datetime,
    message: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "state": state,
        "source": _OPTION_EVENT_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "event_as_of": None,
        "all_count": None,
        "events": [],
        "message": message,
        "limitations": list(_OPTION_EVENT_LIMITATIONS),
    }


def _serialise_option_event(event) -> dict[str, Any]:
    if isinstance(event, dict):
        return {field_name: event.get(field_name) for field_name in _OPTION_EVENT_FIELDS}
    return {
        field_name: getattr(event, field_name, None)
        for field_name in _OPTION_EVENT_FIELDS
    }


def _option_event_item(
    ticker: str,
    *,
    enabled: bool,
    fetched_at: datetime,
    limit_per_symbol: int,
) -> dict[str, Any]:
    if not enabled:
        return _empty_option_event_payload(
            ticker=ticker,
            state="not_configured",
            fetched_at=fetched_at,
            message=(
                "MOOMOO_OPEND_ENABLED 未启用；未读取 Moomoo 异常期权成交。"
            ),
        )

    try:
        snapshot = _compute_option_events_moomoo(
            ticker,
            limit=limit_per_symbol,
        )
        if snapshot is None:
            raise ValueError("Moomoo did not return an option-event snapshot")
        raw_events = tuple(getattr(snapshot, "events", ()) or ())
        events = [_serialise_option_event(event) for event in raw_events]
        snapshot_fetched_at = getattr(snapshot, "fetched_at", fetched_at)
        fetched_at_text = (
            snapshot_fetched_at.isoformat()
            if isinstance(snapshot_fetched_at, datetime)
            else str(snapshot_fetched_at or fetched_at.isoformat())
        )
        all_count = getattr(snapshot, "all_count", None)
        state = "ready" if events else "empty"
        if state == "ready":
            message = (
                f"返回 {len(events)} 条最近异动成交；Moomoo 当前筛选总数 "
                f"{all_count if all_count is not None else '未知'}。"
            )
        else:
            message = "Moomoo 查询成功，但该标的当前未返回异动成交。"
        return {
            "ticker": ticker,
            "state": state,
            "source": _OPTION_EVENT_SOURCE,
            "fetched_at": fetched_at_text,
            "event_as_of": getattr(snapshot, "event_as_of", None),
            "all_count": all_count,
            "events": events,
            "message": message,
            "limitations": list(_OPTION_EVENT_LIMITATIONS),
        }
    except Exception as exc:  # noqa: BLE001 - one symbol must not fail the batch
        logger.debug(
            "[opportunities] unusual option events unavailable for %s: %s",
            ticker,
            exc,
        )
        return _empty_option_event_payload(
            ticker=ticker,
            state="unavailable",
            fetched_at=fetched_at,
            message=(
                "Moomoo OpenAPI 未返回可用的异常期权成交；"
                "未以旧数据、默认值或第三方估算回填。"
            ),
        )


def _execute_option_events(
    symbols: list[str],
    *,
    enabled: bool,
    limit_per_symbol: int,
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    items = [
        _get_or_compute_scan(
            _option_event_cache_key(
                symbol,
                enabled,
                market_date_et,
                limit_per_symbol,
            ),
            lambda symbol=symbol: _option_event_item(
                symbol,
                enabled=enabled,
                fetched_at=datetime.now(timezone.utc),
                limit_per_symbol=limit_per_symbol,
            ),
        )
        for symbol in symbols
    ]
    generated_at = max(
        (str(item["fetched_at"]) for item in items),
        default=requested_at.isoformat(),
    )
    return {
        "schema_version": "option-event/1.0",
        "generated_at": generated_at,
        "market_date_et": market_date_et,
        "items": items,
    }


@router.post("/daily", response_model=DailyOpportunityResponse)
def daily_opportunities(payload: DailyOpportunityRequest) -> DailyOpportunityResponse:
    """Return an evidence-first daily research list with no opaque total score."""

    symbols = payload.symbols or _configured_symbols()
    symbols = normalize_symbols(symbols)[:20]
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_universe",
                "message": "symbols 为空且服务端 STOCK_LIST 未配置。",
            },
        )

    key = _scan_cache_key(symbols, payload.limit)
    try:
        result = _get_or_compute_scan(
            key,
            lambda: _execute_daily_scan(symbols, payload.limit),
            bypass_cache=payload.refresh,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return DailyOpportunityResponse.model_validate(result)


@router.get("/premarket/universe", response_model=PremarketUniverseResponse)
def get_premarket_research_universe() -> PremarketUniverseResponse:
    """Read the persisted research pool or a non-active STOCK_LIST suggestion."""

    from src.services.premarket_research_service import (
        resolve_premarket_research_universe,
    )

    persisted = resolve_premarket_research_universe(
        include_fallback_suggestion=False,
    )
    if persisted is not None:
        return PremarketUniverseResponse(
            configured=True,
            universe_version_key=persisted.universe_version_key,
            source="persisted",
            symbols=list(persisted.symbols),
            limit=persisted.requested_limit,
            created_at=(
                persisted.created_at.isoformat()
                if persisted.created_at is not None
                else None
            ),
            message="已读取服务端持久化研究池。",
        )

    suggestion = resolve_premarket_research_universe(
        configured_symbols=_configured_symbols(),
        include_fallback_suggestion=True,
    )
    if suggestion is None:
        return PremarketUniverseResponse(
            configured=False,
            source="unavailable",
            symbols=[],
            limit=5,
            message=(
                "尚未配置正式研究池；当前也没有可展示的美股 STOCK_LIST 建议。"
            ),
        )
    return PremarketUniverseResponse(
        configured=False,
        source=suggestion.source,
        symbols=list(suggestion.symbols),
        limit=suggestion.requested_limit,
        message=(
            "这是未启用的 STOCK_LIST 建议；必须显式保存后才会用于盘前研究。"
        ),
    )


@router.put("/premarket/universe", response_model=PremarketUniverseResponse)
def put_premarket_research_universe(
    payload: PremarketUniversePutRequest,
) -> PremarketUniverseResponse:
    """Append one immutable, explicit server-side research-pool revision."""

    from src.opportunities.cycle_repository import append_research_universe
    from src.opportunities.premarket import CANONICAL_SCOPE_KEY

    symbols = normalize_symbols(payload.symbols)
    unsupported = [
        symbol
        for symbol in symbols
        if not is_supported_us_option_underlying(symbol)
    ]
    if not symbols or unsupported:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_premarket_universe",
                "message": "盘前研究池只接受受支持的美股期权标的。",
                "unsupported_symbols": unsupported,
            },
        )
    stored, duplicate = append_research_universe(
        symbols,
        payload.limit,
        scope_key=CANONICAL_SCOPE_KEY,
        source="api",
    )
    return PremarketUniverseResponse(
        configured=True,
        universe_version_key=stored.universe_version_key,
        source="persisted",
        symbols=list(stored.symbols),
        limit=stored.requested_limit,
        created_at=stored.created_at.isoformat(),
        duplicate=duplicate,
        message=(
            "该研究池版本已存在，未重复写入。"
            if duplicate
            else "已保存新的正式研究池版本；后续周期会从该版本开始。"
        ),
    )


@router.post("/premarket/status", response_model=PremarketCycleResponse)
def premarket_research_status(
    payload: PremarketCycleRequest,
) -> PremarketCycleResponse:
    """Read the exact canonical cycle state without starting provider work."""

    from src.opportunities.premarket import PremarketCalendarError
    from src.services.premarket_research_service import (
        status_canonical_premarket_research,
    )

    universe = _persisted_premarket_universe()
    symbols = list(universe.symbols) if universe is not None else []
    limit = universe.requested_limit if universe is not None else payload.limit
    universe_source = universe.source if universe is not None else "unavailable"
    universe_version_key = (
        universe.universe_version_key if universe is not None else None
    )
    try:
        result = status_canonical_premarket_research(
            symbols,
            limit,
            universe_source=universe_source,
            universe_version_key=universe_version_key,
            scheduler_enabled=_premarket_scheduler_enabled(),
        )
    except PremarketCalendarError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "xnys_calendar_unavailable",
                "message": "XNYS 交易日历暂时不可用。",
            },
        ) from exc
    return PremarketCycleResponse.model_validate(result)


@router.post("/premarket/run", response_model=PremarketCycleResponse)
def run_premarket_research(
    payload: PremarketCycleRequest,
) -> PremarketCycleResponse:
    """Explicitly run, or otherwise only read, the canonical cycle."""

    from src.opportunities.premarket import PremarketCalendarError
    from src.services.premarket_research_service import (
        run_canonical_premarket_research,
    )

    if not payload.manual:
        return premarket_research_status(payload)

    universe = _persisted_premarket_universe()
    symbols = list(universe.symbols) if universe is not None else []
    limit = universe.requested_limit if universe is not None else payload.limit
    universe_source = universe.source if universe is not None else "unavailable"
    universe_version_key = (
        universe.universe_version_key if universe is not None else None
    )
    try:
        result = run_canonical_premarket_research(
            symbols,
            limit,
            scan_runner=_execute_daily_scan,
            universe_source=universe_source,
            universe_version_key=universe_version_key,
            trigger="manual",
            scheduler_enabled=_premarket_scheduler_enabled(),
        )
    except PremarketCalendarError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "xnys_calendar_unavailable",
                "message": "XNYS 交易日历暂时不可用。",
            },
        ) from exc
    return PremarketCycleResponse.model_validate(result)


@router.post("/snapshots/freeze", response_model=OpportunitySnapshotItem)
def freeze_opportunity_snapshot(
    payload: DailyOpportunityRequest,
) -> OpportunitySnapshotItem:
    """Explicitly freeze the first immutable research snapshot for a daily slot."""

    from src.services.opportunity_snapshot_service import freeze_daily_snapshot

    symbols = normalize_symbols(payload.symbols or _configured_symbols())[:20]
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_universe",
                "message": "symbols 为空且服务端 STOCK_LIST 未配置。",
            },
        )
    key = _scan_cache_key(symbols, payload.limit)
    try:
        run = _get_or_compute_scan(
            key,
            lambda: _execute_daily_scan(symbols, payload.limit),
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    try:
        result = freeze_daily_snapshot(run)
    except SnapshotConflictError as exc:
        # This should be rare because the service checks the stable slot first;
        # retain an explicit conflict if two different first writers race.
        raise HTTPException(
            status_code=409,
            detail={"error": "immutable_snapshot_conflict", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_snapshot_input", "message": str(exc)},
        ) from exc
    return OpportunitySnapshotItem.model_validate(result)


@router.post(
    "/snapshots/ensure",
    response_model=OpportunitySnapshotEnsureResponse,
)
def ensure_opportunity_snapshot(
    payload: DailyOpportunityRequest,
) -> OpportunitySnapshotEnsureResponse:
    """Idempotently save the official pre-open research version when valid."""

    from src.services.opportunity_snapshot_service import ensure_daily_snapshot

    symbols = normalize_symbols(payload.symbols or _configured_symbols())[:20]
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_universe",
                "message": "symbols 为空且服务端 STOCK_LIST 未配置。",
            },
        )
    key = _scan_cache_key(symbols, payload.limit)
    try:
        run = _get_or_compute_scan(
            key,
            lambda: _execute_daily_scan(symbols, payload.limit),
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    try:
        result = ensure_daily_snapshot(run)
    except SnapshotConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "immutable_snapshot_conflict", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_snapshot_input", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - automatic tracking is non-blocking
        logger.error("[opportunity-snapshot] automatic ensure unavailable: %s", exc)
        result = {
            "schema_version": "opportunity-snapshot-ensure/1.0",
            "state": "unavailable",
            "market_date_et": str(run.get("market_date_et") or ""),
            "snapshot": None,
            "message": "结果跟踪暂时不可用；今日研究清单仍可正常查看。",
        }
    return OpportunitySnapshotEnsureResponse.model_validate(result)


@router.get("/snapshots", response_model=OpportunitySnapshotListResponse)
def opportunity_snapshots(
    limit: int = Query(10, ge=1, le=30),
) -> OpportunitySnapshotListResponse:
    """List immutable research snapshots without mutating outcome state."""

    from src.services.opportunity_snapshot_service import list_snapshot_items

    return OpportunitySnapshotListResponse.model_validate(
        list_snapshot_items(limit=limit)
    )


@router.get(
    "/snapshots/{snapshot_key}",
    response_model=OpportunitySnapshotDetailResponse,
)
def opportunity_snapshot_detail(snapshot_key: str) -> OpportunitySnapshotDetailResponse:
    """Return one immutable snapshot with its frozen run payload, read-only."""

    from src.services.opportunity_snapshot_service import get_snapshot_detail

    if not re.fullmatch(r"ops_[0-9a-f]{64}", snapshot_key):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "snapshot_not_found",
                "message": "snapshot_key 格式不合法或不存在。",
            },
        )
    try:
        result = get_snapshot_detail(snapshot_key)
    except Exception as exc:  # noqa: BLE001 - bounded retryable API state
        logger.error(
            "[opportunity-snapshot] snapshot detail unavailable: %s",
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "snapshot_detail_unavailable",
                "message": "快照读取暂不可用；冻结证据未被修改，请稍后重试。",
            },
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "snapshot_not_found",
                "message": "指定 snapshot_key 不存在。",
            },
        )
    return OpportunitySnapshotDetailResponse.model_validate(result)


@router.post(
    "/snapshots/{snapshot_key}/evaluate",
    response_model=OpportunitySnapshotEvaluationResponse,
)
def evaluate_opportunity_snapshot(
    snapshot_key: str,
) -> OpportunitySnapshotEvaluationResponse:
    """Append only due, target-session-complete underlying outcomes."""

    from src.services.opportunity_snapshot_service import (
        OpportunitySnapshotNotFoundError,
        evaluate_snapshot,
    )

    try:
        result = evaluate_snapshot(snapshot_key)
    except OpportunitySnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "snapshot_not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_outcome_input", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - expose a bounded retryable API state
        logger.error(
            "[opportunity-outcomes] snapshot evaluation unavailable: %s",
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "outcome_evaluation_unavailable",
                "message": "结果评估暂不可用；冻结证据未被修改，请稍后重试。",
            },
        ) from exc
    return OpportunitySnapshotEvaluationResponse.model_validate(result)


@router.get(
    "/learning-summary",
    response_model=OpportunityLearningSummaryResponse,
)
def opportunity_learning_summary() -> OpportunityLearningSummaryResponse:
    """Return guarded descriptive statistics; never change ranking weights."""

    from src.config import get_config
    from src.opportunities.maintenance_repository import (
        OUTCOME_MAINTENANCE_POLICY_VERSION,
        get_latest_outcome_maintenance,
    )
    from src.services.opportunity_snapshot_service import learning_summary

    result = learning_summary()
    result["automatic_maintenance_enabled"] = bool(
        getattr(
            get_config(),
            "opportunity_outcome_scheduler_enabled",
            False,
        )
    )
    result["maintenance_policy_version"] = (
        OUTCOME_MAINTENANCE_POLICY_VERSION
    )
    latest = get_latest_outcome_maintenance()
    if latest is not None:
        result["latest_maintenance"] = {
            "session_date_et": latest.session_date_et.isoformat(),
            "policy_version": latest.policy_version,
            "state": latest.state,
            "attempt_count": latest.attempt_count,
            "completed_at": (
                latest.completed_at.isoformat()
                if latest.completed_at is not None
                else None
            ),
            "next_retry_at": (
                latest.next_retry_at.isoformat()
                if latest.next_retry_at is not None
                else None
            ),
            "due_snapshot_count": int(
                latest.result.get("due_snapshot_count") or 0
            ),
            "inserted_outcomes": int(
                latest.result.get("inserted_outcomes") or 0
            ),
            "data_gap_horizons": int(
                latest.result.get("data_gap_horizons") or 0
            ),
            "last_error_code": latest.last_error_code,
        }
    return OpportunityLearningSummaryResponse.model_validate(result)


@router.post("/option-overview", response_model=OptionOverviewResponse)
def option_overview(payload: OptionOverviewRequest) -> OptionOverviewResponse:
    """Return one batch of time-labelled, quote-only option summary metrics."""

    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    key = _option_overview_cache_key(
        payload.symbols,
        _moomoo_opend_enabled(),
        market_date_et,
    )
    result = _get_or_compute_scan(
        key,
        lambda: _execute_option_overview(
            payload.symbols,
            enabled=_moomoo_opend_enabled(),
        ),
    )
    return OptionOverviewResponse.model_validate(result)


@router.post("/option-context", response_model=OptionContextResponse)
def option_context(payload: OptionContextRequest) -> OptionContextResponse:
    """Return Quote-only nearest-expiry ATM Call IV context from Moomoo.

    This endpoint does not calculate IV Rank, inspect unusual option flow, infer
    trade direction, place orders, or emit a buy/sell signal.
    """

    symbols = payload.symbols
    enabled = _moomoo_opend_enabled()
    result = _execute_option_context(symbols, enabled=enabled)
    return OptionContextResponse.model_validate(result)


@router.post("/option-walls", response_model=OptionWallResponse)
def option_walls(payload: OptionWallRequest) -> OptionWallResponse:
    """Return walls plus ATM Call IV derived from the same dynamic snapshot.

    Public open interest does not identify dealer positioning.  Consequently
    this endpoint never labels the unsigned concentration as true dealer GEX,
    never calculates a fake gamma flip, and never invokes a trade API.  The ATM
    field reuses already fetched contracts and does not issue another option
    chain request.
    """

    result = _execute_option_walls(
        payload.symbols,
        enabled=_moomoo_opend_enabled(),
        dte_min=payload.dte_min,
        dte_max=payload.dte_max,
    )
    return OptionWallResponse.model_validate(result)


@router.post("/option-events", response_model=OptionEventResponse)
def option_events(payload: OptionEventRequest) -> OptionEventResponse:
    """Return recent Moomoo-classified unusual option transactions.

    The endpoint is Quote-only, requests only the latest bounded page, does not
    infer open/close or dealer direction, and does not alter candidate ranking.
    """

    result = _execute_option_events(
        payload.symbols,
        enabled=_moomoo_opend_enabled(),
        limit_per_symbol=payload.limit_per_symbol,
    )
    return OptionEventResponse.model_validate(result)
