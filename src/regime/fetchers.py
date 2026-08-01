# -*- coding: utf-8 -*-
"""Bounded Regime market-data aggregator.

Daily market structure is Moomoo-first, with the official Cboe VIX history and
one bounded SPY yfinance call as narrow fallbacks.  Alpaca premarket and
Finnhub calendars are optional supporting domains.  Every getter preserves
missing/partial inputs through ``_status`` metadata; the classifier's quality
contract decides whether a score is ready, provisional, or unavailable rather
than turning missing data into an authoritative number.
"""
from __future__ import annotations

import logging
import math
import queue
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = ["RegimeDataFetcher"]


# 11 S&P sector ETFs.
SECTOR_ETFS = [
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
]
DEFENSIVE_SECTORS = {"XLP", "XLU", "XLV"}
_CBOE_VIX_HISTORY_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
_MOOMOO_CAPABILITY_MISS_TTL_SECONDS = 600.0
_moomoo_capability_misses: dict[str, float] = {}
_moomoo_capability_lock = threading.Lock()


def _safe_import_yf():
    try:
        import yfinance as yf  # type: ignore

        return yf
    except ImportError:
        logger.warning("yfinance not installed")
        return None


def _pct_change_series(closes: list[float], window: int) -> float:
    if len(closes) < window + 1 or closes[-window - 1] == 0:
        return 0.0
    return (closes[-1] - closes[-window - 1]) / closes[-window - 1] * 100.0


def _sma(values: list[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return statistics.fmean(values[-window:])


class RegimeDataFetcher:
    """Aggregate six Regime domains within one request-scoped work budget."""

    # Sentinels to distinguish "caller passed X=None explicitly" from "use default".
    _UNSET = object()

    def __init__(
        self,
        alpaca=_UNSET,
        finnhub=_UNSET,
        yf=_UNSET,
        manager=_UNSET,
        request_budget_seconds: float = 18.0,
    ):
        yf_was_explicit = yf is not self._UNSET
        # yfinance: lazy import unless caller wires something else.
        self.yf = _safe_import_yf() if not yf_was_explicit else yf
        self._deadline = time.monotonic() + max(1.0, float(request_budget_seconds))
        self._daily_cache: dict[tuple[str, date, int], tuple[Any, Optional[str]]] = {}
        self._manager_owned = False

        # Production uses the same provider layer as the rest of the project,
        # including its enabled MoomooFetcher and shared QuoteContext.  Tests
        # that explicitly inject yfinance retain the small legacy seam and do
        # not initialize the full provider graph.
        if manager is self._UNSET and not yf_was_explicit:
            try:
                from data_provider.base import DataFetcherManager

                self.manager = DataFetcherManager()
                self._manager_owned = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Regime DataFetcherManager init failed: %s", exc)
                self.manager = None
        else:
            self.manager = None if manager is self._UNSET else manager

        # Alpaca: auto-instantiate from env when caller doesn't pass anything.
        # If the key env vars are missing, AlpacaFetcher stays `configured=False`
        # and scorer `score_premarket_activity` falls back to zeros gracefully.
        if alpaca is self._UNSET:
            try:
                from data_provider.alpaca_fetcher import AlpacaFetcher

                self.alpaca = AlpacaFetcher(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("AlpacaFetcher auto-init failed: %s", exc)
                self.alpaca = None
        else:
            self.alpaca = alpaca

        # Finnhub: same pattern.
        if finnhub is self._UNSET:
            try:
                from data_provider.finnhub_fetcher import FinnhubFetcher

                self.finnhub = FinnhubFetcher(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("FinnhubFetcher auto-init failed: %s", exc)
                self.finnhub = None
        else:
            self.finnhub = finnhub

    def close(self) -> None:
        """Release manager-owned provider resources."""
        if self._manager_owned and self.manager is not None:
            try:
                self.manager.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Regime provider close failed: %s", exc)
        self.manager = None

    def _remaining_budget(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def _bounded_call(
        self,
        fn: Callable[[], Any],
        *,
        timeout_seconds: float,
        label: str,
    ) -> tuple[bool, Any]:
        """Run an optional remote fallback without holding the request open.

        Only standalone remote clients use this helper.  Moomoo calls retain
        their SDK-enforced synchronous query timeout so its shared context is
        never closed while a detached worker is still using it.
        """

        timeout = min(max(0.0, timeout_seconds), self._remaining_budget())
        if timeout <= 0:
            return False, None
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result_queue.put((True, fn()))
            except Exception as exc:  # noqa: BLE001
                result_queue.put((False, exc))

        worker = threading.Thread(
            target=run,
            name=f"regime-{label}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            logger.warning("Regime optional fallback timed out: %s (%.1fs)", label, timeout)
            return False, None
        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return False, None

    def _enabled_moomoo_fetcher(self):
        manager = self.manager
        snapshot = getattr(manager, "_get_fetchers_snapshot", None)
        if manager is None or not callable(snapshot):
            return None
        try:
            for fetcher in snapshot():
                if (
                    getattr(fetcher, "name", "") == "MoomooFetcher"
                    and getattr(fetcher, "priority", 99) < 99
                ):
                    return fetcher
        except Exception as exc:  # noqa: BLE001
            logger.debug("Regime Moomoo discovery failed: %s", exc)
        return None

    def _fetch_moomoo_daily(
        self,
        symbol: str,
        target_date: date,
        lookback_days: int,
    ):
        with _moomoo_capability_lock:
            miss_until = _moomoo_capability_misses.get(symbol.upper(), 0.0)
            if miss_until > time.monotonic():
                return None
            if miss_until:
                _moomoo_capability_misses.pop(symbol.upper(), None)
        fetcher = self._enabled_moomoo_fetcher()
        if fetcher is None or self._remaining_budget() <= 0:
            return None
        start_date = target_date - timedelta(days=max(10, int(lookback_days * 1.8)))
        call = getattr(self.manager, "_call_fetcher_method", None)
        if callable(call):
            return call(
                fetcher,
                "get_daily_data",
                stock_code=symbol,
                start_date=start_date.isoformat(),
                end_date=target_date.isoformat(),
                days=lookback_days,
            )
        return fetcher.get_daily_data(
            stock_code=symbol,
            start_date=start_date.isoformat(),
            end_date=target_date.isoformat(),
            days=lookback_days,
        )

    def _fetch_yfinance_daily(
        self,
        symbol: str,
        target_date: date,
        lookback_days: int,
    ):
        yf = self.yf
        if yf is None:
            return None
        yf_symbol = "^VIX" if symbol.upper() in {"VIX", "^VIX"} else symbol

        def fetch():
            ticker = yf.Ticker(yf_symbol)
            return ticker.history(
                start=target_date - timedelta(days=int(lookback_days * 1.8) + 5),
                end=target_date + timedelta(days=1),
            )

        ok, value = self._bounded_call(
            fetch,
            timeout_seconds=3.0,
            label=f"yfinance-{yf_symbol}",
        )
        if not ok:
            if isinstance(value, Exception):
                logger.info("Regime yfinance fallback failed for %s: %s", yf_symbol, value)
            return None
        return value

    def _fetch_cboe_vix_daily(
        self,
        target_date: date,
        lookback_days: int,
    ):
        """Read the official Cboe daily VIX close file with a hard timeout."""
        timeout = min(3.0, self._remaining_budget())
        if timeout <= 0:
            return None
        try:
            import pandas as pd
            import requests

            response = requests.get(_CBOE_VIX_HISTORY_URL, timeout=timeout)
            response.raise_for_status()
            frame = pd.read_csv(StringIO(response.text))
            normalized = {str(column).strip().upper(): column for column in frame.columns}
            date_column = normalized.get("DATE")
            close_column = normalized.get("CLOSE")
            if date_column is None or close_column is None:
                raise ValueError("Cboe VIX CSV missing DATE/CLOSE columns")
            result = pd.DataFrame(
                {
                    "date": pd.to_datetime(frame[date_column], errors="coerce"),
                    "close": pd.to_numeric(frame[close_column], errors="coerce"),
                }
            ).dropna()
            result = result[result["date"].dt.date <= target_date]
            if result.empty:
                return None
            return result.sort_values("date").tail(max(lookback_days, 6)).reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            logger.info("Regime official Cboe VIX fallback failed: %s", exc)
            return None

    def _daily_frame(
        self,
        symbol: str,
        target_date: date,
        lookback_days: int,
    ) -> tuple[Any, Optional[str]]:
        key = (symbol.upper(), target_date, lookback_days)
        if key in self._daily_cache:
            return self._daily_cache[key]

        frame = None
        source: Optional[str] = None
        if self.manager is not None:
            try:
                frame = self._fetch_moomoo_daily(symbol, target_date, lookback_days)
                if frame is not None and not frame.empty:
                    source = "MoomooFetcher"
            except Exception as exc:  # noqa: BLE001
                if "unknown stock" in str(exc).lower():
                    with _moomoo_capability_lock:
                        _moomoo_capability_misses[symbol.upper()] = (
                            time.monotonic()
                            + _MOOMOO_CAPABILITY_MISS_TTL_SECONDS
                        )
                logger.info("Regime Moomoo daily unavailable for %s: %s", symbol, exc)

        if (
            (frame is None or frame.empty)
            and self.manager is not None
            and symbol.upper() in {"VIX", "^VIX"}
        ):
            frame = self._fetch_cboe_vix_daily(target_date, lookback_days)
            if frame is not None and not frame.empty:
                source = "Cboe"

        # Remote fallback is deliberately limited to the two core market
        # series.  Eleven sector ETFs must not fan out into eleven slow Yahoo
        # requests when the local read-only source is unavailable.
        allow_remote_fallback = (
            self.manager is None or symbol.upper() == "SPY"
        )
        if (frame is None or frame.empty) and allow_remote_fallback:
            frame = self._fetch_yfinance_daily(symbol, target_date, lookback_days)
            if frame is not None and not frame.empty:
                source = "yfinance"

        result = (frame, source)
        self._daily_cache[key] = result
        return result

    # --- individual getters -----------------------------------------------

    def get_spy_snapshot(self, target_date: date) -> dict:
        """SPY close + MA20 + MA50 + 5d % change."""
        closes = self._daily_closes("SPY", target_date, lookback_days=90)
        if not closes:
            return {}
        _frame, source = self._daily_frame("SPY", target_date, 90)
        ma20 = _sma(closes, 20)
        ma50 = _sma(closes, 50)
        return {
            "close": closes[-1],
            "ma20": ma20,
            "ma50": ma50,
            "pct_change_5d": _pct_change_series(closes, 5),
            "_status": "ready" if ma20 is not None and ma50 is not None and len(closes) >= 51 else "degraded",
            "_observations": len(closes),
            "_source": source,
        }

    def get_vix(self, target_date: date) -> dict:
        closes = self._daily_closes("VIX", target_date, lookback_days=20)
        if not closes:
            return {}
        _frame, source = self._daily_frame("VIX", target_date, 20)
        return {
            "level": closes[-1],
            "pct_change_5d": _pct_change_series(closes, 5),
            "_status": "ready" if len(closes) >= 6 else "degraded",
            "_observations": len(closes),
            "_source": source,
        }

    def get_macro_events(self, target_date: date, watchlist: list[str]) -> dict:
        """Today's macro flags for scoring AND a 7-day US agenda for display."""
        events = {
            "_status": "unavailable",
            "_readiness": {
                "economic_calendar": "unavailable",
                "earnings_calendar": "unavailable",
            },
            "fomc_today": False,
            "cpi_today": False,
            "nfp_today": False,
            "earnings_count_watchlist": 0,
            "tariff_headline_today": False,
            # Rich display-only fields — scorers don't use these.
            "us_agenda": [],           # list of {date, time, event, impact}
            "watchlist_earnings": [],  # list of {date, symbol}
        }
        if self.finnhub and getattr(self.finnhub, "configured", False):
            try:
                if self._remaining_budget() <= 0:
                    return events
                window_end = target_date + timedelta(days=7)
                # Finnhub accepts date ranges.  Two bounded range requests
                # replace the previous 16 per-day calls.
                economic_rows = self.finnhub.get_economic_calendar(
                    target_date, window_end
                )
                economic_ok = self._finnhub_request_succeeded(
                    "economic_calendar"
                )
                earnings_attempted = self._remaining_budget() > 0
                earnings_rows = []
                if earnings_attempted:
                    earnings_rows = self.finnhub.get_earnings_calendar(
                        target_date, window_end
                    )
                earnings_ok = (
                    self._finnhub_request_succeeded("earnings_calendar")
                    if earnings_attempted
                    else False
                )
                events["_readiness"] = {
                    "economic_calendar": (
                        "ready" if economic_ok else "unavailable"
                    ),
                    "earnings_calendar": (
                        "ready" if earnings_ok else "unavailable"
                    ),
                }

                def row_date(row: dict) -> Optional[date]:
                    raw = row.get("date") or row.get("time")
                    if raw is None:
                        return None
                    try:
                        return date.fromisoformat(str(raw)[:10])
                    except (TypeError, ValueError):
                        return None

                # Today's flags drive the score.  Rows without a date are kept
                # compatible with older mocks/providers and treated as today's.
                for ev in economic_rows if economic_ok else []:
                    event_date = row_date(ev)
                    if event_date is not None and event_date != target_date:
                        continue
                    country = (ev.get("country") or "").upper()
                    if country not in ("US", "USA", ""):
                        continue
                    label = (ev.get("event") or "").lower()
                    if "federal funds rate" in label or "fomc" in label:
                        events["fomc_today"] = True
                    if "cpi" in label or "consumer price" in label:
                        events["cpi_today"] = True
                    if "nonfarm" in label or "nfp" in label:
                        events["nfp_today"] = True

                agenda: list[dict] = []
                for ev in economic_rows if economic_ok else []:
                    if (ev.get("country") or "").upper() not in ("US", "USA"):
                        continue
                    impact = (ev.get("impact") or "").lower()
                    if impact not in ("medium", "high"):
                        continue
                    event_date = row_date(ev) or target_date
                    agenda.append({
                        "date": event_date.isoformat(),
                        "time": ev.get("time"),
                        "event": ev.get("event"),
                        "impact": impact,
                        "estimate": ev.get("estimate"),
                        "prev": ev.get("prev"),
                    })
                events["us_agenda"] = agenda

                watchlist_upper = {s.upper() for s in watchlist}
                count = sum(
                    1
                    for row in earnings_rows if earnings_ok
                    if (
                        (row_date(row) in {None, target_date})
                        and (row.get("symbol") or "").upper() in watchlist_upper
                    )
                )
                events["earnings_count_watchlist"] = count
                events["watchlist_earnings"] = [
                    {
                        "date": (row_date(row) or target_date).isoformat(),
                        "symbol": (row.get("symbol") or "").upper(),
                        "hour": row.get("hour"),
                        "eps_estimate": row.get("epsEstimate"),
                    }
                    for row in earnings_rows if earnings_ok
                    if (row.get("symbol") or "").upper() in watchlist_upper
                ]
                events["_status"] = (
                    "ready"
                    if economic_ok and earnings_ok
                    else "degraded"
                    if economic_ok or earnings_ok
                    else "unavailable"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("finnhub macro events failed: %s", exc)
        return events

    def _finnhub_request_succeeded(self, operation: str) -> bool:
        """Read optional provider request status without breaking old adapters."""
        status_reader = getattr(self.finnhub, "request_succeeded", None)
        if not callable(status_reader):
            return True
        try:
            status = status_reader(operation)
        except Exception:  # noqa: BLE001
            return False
        # ``None`` means an older/test adapter cannot report status.  Only an
        # explicit False proves the provider rejected or failed the request.
        return status is not False

    def get_sector_performance(self, target_date: date, lookback_days: int = 5) -> dict:
        sectors_above = 0
        defensive_strength = 0
        total_sectors = 0
        sources: dict[str, str] = {}
        for etf in SECTOR_ETFS:
            if self._remaining_budget() <= 0:
                break
            closes = self._daily_closes(etf, target_date, lookback_days=30)
            if not closes:
                continue
            _frame, source = self._daily_frame(etf, target_date, 30)
            if source:
                sources[etf] = source
            ma20 = _sma(closes, 20)
            if ma20 is None:
                continue
            total_sectors += 1
            if closes[-1] > ma20:
                sectors_above += 1
                if etf in DEFENSIVE_SECTORS:
                    defensive_strength += 1
        # Defensive leadership means 2+ defensive ETFs above MA20 while others aren't.
        return {
            "sectors_above_ma20": sectors_above,
            "defensive_leaders": defensive_strength >= 2 and sectors_above <= 6,
            "total_sectors_seen": total_sectors,
            "_status": (
                "ready"
                if total_sectors == len(SECTOR_ETFS)
                else "degraded"
                if total_sectors > 0
                else "unavailable"
            ),
            "_sources": sources,
        }

    def get_prev_day_structure(self, target_date: date) -> dict:
        """SPY prior-day close-vs-high + range."""
        hist, source = self._daily_frame("SPY", target_date, 90)
        if hist is None or hist.empty:
            return {}
        try:
            # Pick the last bar strictly before target_date.
            import pandas as pd  # local import to keep module optional

            hist = hist.copy()
            if "date" in hist.columns:
                hist["date"] = pd.to_datetime(hist["date"], errors="coerce")
                hist = hist.dropna(subset=["date"]).set_index("date")
            idx = hist.index
            if hasattr(idx, "tz_localize"):
                try:
                    hist = hist.tz_localize(None) if idx.tz is not None else hist
                except Exception:
                    pass
            hist = hist.sort_index()
            prior = hist[hist.index.date < target_date]
            if prior.empty:
                return {}
            last = prior.iloc[-1]
            low = float(last.get("low", last.get("Low")))
            high = float(last.get("high", last.get("High")))
            close = float(last.get("close", last.get("Close")))
            rng = high - low
            close_vs_high = (close - low) / rng if rng else 0.0
            prior_close_value = close
            prev_day_range_pct = (rng / prior_close_value * 100.0) if prior_close_value else 0.0
            return {
                "close_vs_high_pct": max(0.0, min(1.0, close_vs_high)),
                "prev_day_range_pct": prev_day_range_pct,
                "_status": "ready",
                "_source": source,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("prev_day structure failed: %s", exc)
            return {}

    def get_premarket_activity(
        self,
        watchlist: list[str],
        target_date: date,
        *,
        as_of: Optional[datetime] = None,
    ) -> dict:
        """Premarket SPY + watchlist movers relative to prior daily closes.

        The ``movers`` field holds a per-symbol pct move for the UI. Unknown
        evidence remains ``None`` rather than becoming a synthetic 0% move.
        All Alpaca calls in this request receive the same frozen ``as_of``.

        ``MoomooFetcher.get_premarket`` exists as a proved adapter, but is not
        wired here yet: the synchronous SDK's cold history call can exceed this
        request's bounded work budget and cannot be cancelled safely.
        """
        frozen_as_of = _aware_utc(as_of or datetime.now(timezone.utc))
        spy_pre_pct: Optional[float] = None
        up = 0
        down = 0
        movers: list[dict] = []
        spy_observed = False
        partial_observed = False
        observed_symbols = 0
        spy_as_of: Optional[str] = None
        spy_previous_close: Optional[float] = None
        reasons: list[str] = []
        attempted_sources: list[str] = []
        if (
            self.alpaca is not None
            and getattr(self.alpaca, "configured", False)
            and self._remaining_budget() > 0
        ):
            attempted_sources.append("Alpaca")
            try:
                premarket_deadline = min(
                    self._deadline,
                    time.monotonic() + 3.0,
                )
                spy_bar = self.alpaca.get_premarket(
                    "SPY",
                    target_date=target_date,
                    as_of=frozen_as_of,
                )
                if spy_bar:
                    partial_observed = (
                        partial_observed
                        or _has_premarket_evidence(spy_bar)
                    )
                    spy_as_of = spy_bar.get("as_of")
                    spy_previous_close = _optional_float(
                        spy_bar.get("previous_close")
                    )
                    spy_pre_pct = _optional_float(spy_bar.get("pct_change"))
                    spy_observed = (
                        spy_bar.get("_status") == "ready"
                        and spy_pre_pct is not None
                        and spy_previous_close is not None
                    )
                    reason = spy_bar.get("_reason")
                    if reason:
                        reasons.append(f"SPY:{reason}")
                mover_symbols = watchlist[:5] if spy_observed else []
                for sym in mover_symbols:
                    if time.monotonic() >= premarket_deadline:
                        reasons.append("watchlist:request_budget_exhausted")
                        break
                    bar = (
                        spy_bar
                        if sym.upper() == "SPY"
                        else self.alpaca.get_premarket(
                            sym,
                            target_date=target_date,
                            as_of=frozen_as_of,
                        )
                    )
                    if not bar:
                        movers.append({
                            "symbol": sym,
                            "pct": None,
                            "close": None,
                            "as_of": None,
                            "previous_close": None,
                        })
                        reasons.append(f"{sym}:source_unavailable")
                        continue
                    partial_observed = (
                        partial_observed
                        or _has_premarket_evidence(bar)
                    )
                    move = _optional_float(bar.get("pct_change"))
                    close = _optional_float(bar.get("price"))
                    previous_close = _optional_float(bar.get("previous_close"))
                    if (
                        bar.get("_status") != "ready"
                        or move is None
                        or close is None
                        or previous_close is None
                    ):
                        movers.append({
                            "symbol": sym,
                            "pct": None,
                            "close": close,
                            "as_of": bar.get("as_of"),
                            "previous_close": previous_close,
                        })
                        reason = bar.get("_reason") or "incomplete_reference"
                        reasons.append(f"{sym}:{reason}")
                        continue
                    observed_symbols += 1
                    if move >= 5.0:
                        up += 1
                    elif move <= -5.0:
                        down += 1
                    movers.append({
                        "symbol": sym,
                        "pct": round(move, 3),
                        "close": round(close, 2),
                        "as_of": bar.get("as_of"),
                        "previous_close": round(previous_close, 4),
                    })
            except Exception as exc:  # noqa: BLE001
                logger.warning("Alpaca premarket failed: %s", exc)
                reasons.append("alpaca_request_failed")
        status = (
            "ready"
            if (
                spy_observed
                and len(watchlist) <= 5
                and observed_symbols == len(mover_symbols)
            )
            else "degraded"
            if spy_observed or observed_symbols > 0 or partial_observed
            else "unavailable"
        )
        return {
            "spy_pre_pct": spy_pre_pct,
            "watchlist_up_5pct": up,
            "watchlist_down_5pct": down,
            "movers": movers,
            "_status": status,
            "_observed_symbols": observed_symbols,
            "_requested_symbols": min(len(watchlist), 5) if spy_observed else 0,
            "_watchlist_size": len(watchlist),
            "_source": "Alpaca" if partial_observed else None,
            "_attempted_sources": attempted_sources,
            "_as_of": spy_as_of,
            "_requested_as_of": frozen_as_of.isoformat(),
            "_spy_previous_close": spy_previous_close,
            "_reason": (
                None
                if status == "ready"
                else reasons[0]
                if reasons
                else "watchlist_coverage_capped"
                if spy_observed and len(watchlist) > 5
                else "alpaca_premarket_incomplete"
                if attempted_sources
                else "no_premarket_provider_available"
            ),
            "_reasons": list(dict.fromkeys(reasons)),
        }

    # --- helpers ------------------------------------------------------

    def _daily_closes(self, symbol: str, target_date: date, lookback_days: int) -> list[float]:
        """Return daily close prices up to (and including) ``target_date``."""
        try:
            hist, _source = self._daily_frame(symbol, target_date, lookback_days)
            if hist is None or hist.empty:
                return []
            hist = _daily_frame_through(hist, target_date)
            if hist.empty:
                return []
            close_col = "close" if "close" in hist.columns else "Close"
            closes = [float(v) for v in hist[close_col].dropna().tolist()]
            return closes[-lookback_days:] if len(closes) >= lookback_days else closes
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance history(%s) failed: %s", symbol, exc)
            return []


def _optional_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _has_premarket_evidence(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if any(
        _optional_float(payload.get(key)) is not None
        for key in ("price", "previous_close", "pct_change", "c", "o")
    ):
        return True
    return bool(payload.get("as_of") or payload.get("t"))


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("as_of must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(timezone.utc)


def _daily_frame_through(frame: Any, target_date: date):
    """Return only observations provably dated on or before ``target_date``.

    Moomoo/Cboe frames carry a date column while yfinance normally uses a
    ``DatetimeIndex``.  Undated legacy/test frames retain their historical
    behavior because there is no timestamp with which to prove a future leak.
    """
    import pandas as pd

    filtered = frame.copy()
    date_column = next(
        (column for column in ("date", "Date") if column in filtered.columns),
        None,
    )
    if date_column is not None:
        observation_dates = filtered[date_column].map(_daily_observation_date)
        eligible = observation_dates.map(
            lambda observed: observed is not None and observed <= target_date
        )
        filtered = filtered.loc[eligible].copy()
        if filtered.empty:
            return filtered
        filtered["_regime_observation_date"] = observation_dates.loc[eligible]
        filtered = filtered.sort_values("_regime_observation_date")
        return filtered.drop(columns=["_regime_observation_date"])

    if isinstance(filtered.index, pd.DatetimeIndex):
        eligible = [
            observed is not None and observed <= target_date
            for observed in (
                _daily_observation_date(value) for value in filtered.index
            )
        ]
        filtered = filtered.loc[eligible]
    return filtered.sort_index()


def _daily_observation_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        import pandas as pd

        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    return timestamp.date()
