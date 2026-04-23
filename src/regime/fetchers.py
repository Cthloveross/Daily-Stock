# -*- coding: utf-8 -*-
"""Regime data aggregator.

Knits together Alpaca / yfinance / Finnhub into the dict shape the scorers
consume. Each getter is defensive: missing data returns a sane default so the
classifier still produces a number, just with lower signal quality.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["RegimeDataFetcher"]


# 11 S&P sector ETFs.
SECTOR_ETFS = [
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
]
DEFENSIVE_SECTORS = {"XLP", "XLU", "XLV"}


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
    """Aggregates data needed for the six Regime scorers."""

    # Sentinels to distinguish "caller passed X=None explicitly" from "use default".
    _UNSET = object()

    def __init__(
        self,
        alpaca=_UNSET,
        finnhub=_UNSET,
        yf=_UNSET,
    ):
        # yfinance: lazy import unless caller wires something else.
        self.yf = _safe_import_yf() if yf is self._UNSET else yf

        # Alpaca: auto-instantiate from env when caller doesn't pass anything.
        # If the key env vars are missing, AlpacaFetcher stays `configured=False`
        # and scorer `score_premarket_activity` falls back to zeros gracefully.
        if alpaca is self._UNSET:
            try:
                from data_provider.alpaca_fetcher import AlpacaFetcher

                self.alpaca = AlpacaFetcher()
            except Exception as exc:  # noqa: BLE001
                logger.warning("AlpacaFetcher auto-init failed: %s", exc)
                self.alpaca = None
        else:
            self.alpaca = alpaca

        # Finnhub: same pattern.
        if finnhub is self._UNSET:
            try:
                from data_provider.finnhub_fetcher import FinnhubFetcher

                self.finnhub = FinnhubFetcher()
            except Exception as exc:  # noqa: BLE001
                logger.warning("FinnhubFetcher auto-init failed: %s", exc)
                self.finnhub = None
        else:
            self.finnhub = finnhub

    # --- individual getters -----------------------------------------------

    def get_spy_snapshot(self, target_date: date) -> dict:
        """SPY close + MA20 + MA50 + 5d % change."""
        closes = self._daily_closes("SPY", target_date, lookback_days=90)
        if not closes:
            return {}
        return {
            "close": closes[-1],
            "ma20": _sma(closes, 20),
            "ma50": _sma(closes, 50),
            "pct_change_5d": _pct_change_series(closes, 5),
        }

    def get_vix(self, target_date: date) -> dict:
        closes = self._daily_closes("^VIX", target_date, lookback_days=20)
        if not closes:
            return {}
        return {
            "level": closes[-1],
            "pct_change_5d": _pct_change_series(closes, 5),
        }

    def get_macro_events(self, target_date: date, watchlist: list[str]) -> dict:
        """Today's macro flags for scoring AND a 7-day US agenda for display."""
        events = {
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
                # Today's flags drive the score.
                econ_today = self.finnhub.get_economic_calendar(target_date, target_date)
                for ev in econ_today:
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

                # Next 7 days: keep only US + (medium | high) impact for display.
                agenda: list[dict] = []
                for offset in range(8):
                    d = target_date + timedelta(days=offset)
                    # Re-use today's call to save one round trip.
                    econ = econ_today if offset == 0 else self.finnhub.get_economic_calendar(d, d)
                    for ev in econ:
                        if (ev.get("country") or "").upper() not in ("US", "USA"):
                            continue
                        impact = (ev.get("impact") or "").lower()
                        if impact not in ("medium", "high"):
                            continue
                        agenda.append({
                            "date": d.isoformat(),
                            "time": ev.get("time"),
                            "event": ev.get("event"),
                            "impact": impact,
                            "estimate": ev.get("estimate"),
                            "prev": ev.get("prev"),
                        })
                events["us_agenda"] = agenda

                # Earnings (today + watchlist hit count for scoring).
                earnings_today = self.finnhub.get_earnings_calendar(target_date, target_date)
                watchlist_upper = {s.upper() for s in watchlist}
                count = sum(
                    1 for e in earnings_today if (e.get("symbol") or "").upper() in watchlist_upper
                )
                events["earnings_count_watchlist"] = count

                # Watchlist earnings over next 7 days for the display panel.
                earnings_week: list[dict] = []
                for offset in range(8):
                    d = target_date + timedelta(days=offset)
                    rows = earnings_today if offset == 0 else self.finnhub.get_earnings_calendar(d, d)
                    for e in rows:
                        sym = (e.get("symbol") or "").upper()
                        if sym in watchlist_upper:
                            earnings_week.append({
                                "date": d.isoformat(),
                                "symbol": sym,
                                "hour": e.get("hour"),  # bmo / amc
                                "eps_estimate": e.get("epsEstimate"),
                            })
                events["watchlist_earnings"] = earnings_week
            except Exception as exc:  # noqa: BLE001
                logger.warning("finnhub macro events failed: %s", exc)
        return events

    def get_sector_performance(self, target_date: date, lookback_days: int = 5) -> dict:
        sectors_above = 0
        defensive_strength = 0
        total_sectors = 0
        for etf in SECTOR_ETFS:
            closes = self._daily_closes(etf, target_date, lookback_days=30)
            if not closes:
                continue
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
        }

    def get_prev_day_structure(self, target_date: date) -> dict:
        """SPY prior-day close-vs-high + range."""
        yf = self.yf
        if yf is None:
            return {}
        try:
            tkr = yf.Ticker("SPY")
            # 14-day lookback to survive 3-day holiday weekends (e.g. MLK / Memorial Day).
            hist = tkr.history(start=target_date - timedelta(days=14), end=target_date + timedelta(days=1))
            if hist is None or hist.empty:
                return {}
            # Pick the last bar strictly before target_date.
            import pandas as pd  # local import to keep module optional

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
            low, high, close = float(last["Low"]), float(last["High"]), float(last["Close"])
            rng = high - low
            close_vs_high = (close - low) / rng if rng else 0.0
            prior_close_value = close
            prev_day_range_pct = (rng / prior_close_value * 100.0) if prior_close_value else 0.0
            return {
                "close_vs_high_pct": max(0.0, min(1.0, close_vs_high)),
                "prev_day_range_pct": prev_day_range_pct,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("prev_day structure failed: %s", exc)
            return {}

    def get_premarket_activity(
        self, watchlist: list[str], target_date: date
    ) -> dict:
        """Premarket SPY + watchlist movers. Alpaca preferred; otherwise empty.

        The ``movers`` field (new) holds a per-symbol pct move for UI heatmap;
        scorers keep using the aggregated ``watchlist_up_5pct`` / ``..._down_5pct``
        counts so their behaviour is unchanged.
        """
        spy_pre_pct = 0.0
        up = 0
        down = 0
        movers: list[dict] = []
        if self.alpaca is not None and getattr(self.alpaca, "configured", False):
            try:
                spy_bar = self.alpaca.get_premarket("SPY")
                if spy_bar:
                    open_ = float(spy_bar.get("o") or 0)
                    close_ = float(spy_bar.get("c") or 0)
                    if open_:
                        spy_pre_pct = (close_ - open_) / open_ * 100.0
                for sym in watchlist[:20]:
                    bar = self.alpaca.get_premarket(sym)
                    if not bar:
                        movers.append({"symbol": sym, "pct": None, "close": None})
                        continue
                    o = float(bar.get("o") or 0)
                    c = float(bar.get("c") or 0)
                    if not o:
                        movers.append({"symbol": sym, "pct": None, "close": c or None})
                        continue
                    move = (c - o) / o * 100.0
                    if move >= 5.0:
                        up += 1
                    elif move <= -5.0:
                        down += 1
                    movers.append({
                        "symbol": sym,
                        "pct": round(move, 3),
                        "close": round(c, 2),
                    })
            except Exception as exc:  # noqa: BLE001
                logger.warning("Alpaca premarket failed: %s", exc)
        return {
            "spy_pre_pct": spy_pre_pct,
            "watchlist_up_5pct": up,
            "watchlist_down_5pct": down,
            "movers": movers,
        }

    # --- helpers ------------------------------------------------------

    def _daily_closes(self, symbol: str, target_date: date, lookback_days: int) -> list[float]:
        """Return daily close prices up to (and including) ``target_date``."""
        yf = self.yf
        if yf is None:
            return []
        try:
            tkr = yf.Ticker(symbol)
            hist = tkr.history(
                start=target_date - timedelta(days=int(lookback_days * 1.6) + 5),
                end=target_date + timedelta(days=1),
            )
            if hist is None or hist.empty:
                return []
            hist = hist.sort_index()
            # Keep only rows with date <= target_date.
            closes = [float(v) for v in hist["Close"].dropna().tolist()]
            return closes[-lookback_days:] if len(closes) >= lookback_days else closes
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance history(%s) failed: %s", symbol, exc)
            return []
