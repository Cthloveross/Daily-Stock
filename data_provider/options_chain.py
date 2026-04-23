# -*- coding: utf-8 -*-
"""yfinance-backed option-chain fetcher with in-memory + SQLite caching.

Stage 1 deliverable. Consumed by:
    - src/agent/tools/get_option_chain.py (Stage 10)
    - src/lab/leap_explorer.py (Phase 1)

References: New-docs/04_OPTION_SUPPORT_EXTENSION.md §4.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["OptionQuote", "OptionsChainFetcher", "get_leap_candidates"]


@dataclass(frozen=True)
class OptionQuote:
    """A single option contract quote snapshot."""

    underlying: str
    expiry: str  # ISO 'YYYY-MM-DD'
    right: str  # 'C' or 'P'
    strike: float
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float
    delta: Optional[float]  # yfinance exposes bid/ask only; delta is computed lazily
    dte: int
    moneyness: str  # 'ITM' / 'ATM' / 'OTM'


def _import_yfinance():
    try:
        import yfinance as yf  # type: ignore

        return yf
    except ImportError:
        logger.warning("yfinance not installed; option chain fetches will return empty")
        return None


def _spot_from_ticker(tkr) -> Optional[float]:
    """Best-effort spot lookup with multiple fallbacks."""
    try:
        info = tkr.info or {}
    except Exception:  # noqa: BLE001
        info = {}
    for key in ("regularMarketPrice", "currentPrice", "previousClose"):
        val = info.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    try:
        hist = tkr.history(period="5d")
        if hist is not None and not hist.empty and "Close" in hist:
            return float(hist["Close"].iloc[-1])
    except Exception:  # noqa: BLE001
        pass
    return None


def _classify_moneyness(right: str, strike: float, spot: float) -> str:
    tol = spot * 0.005
    if abs(strike - spot) <= tol:
        return "ATM"
    if right == "C":
        return "ITM" if strike < spot else "OTM"
    return "ITM" if strike > spot else "OTM"


class OptionsChainFetcher:
    """Fetches option chains and caches results.

    In-memory TTL cache keyed by ``(underlying, expiry, right)``. Separate
    persistence into ``option_chains_cache`` is optional and off by default to
    keep unit tests fast; call ``persist_cache=True`` when a caller wants the
    snapshot on disk.
    """

    def __init__(self, cache_ttl_seconds: int = 600):
        self._cache: dict[tuple[str, str, str], tuple[float, List[OptionQuote]]] = {}
        self._cache_ttl = cache_ttl_seconds

    # -- public API ---------------------------------------------------

    def get_expirations(self, symbol: str) -> List[str]:
        """Return yfinance expiration strings for ``symbol`` (sorted ascending)."""
        yf = _import_yfinance()
        if yf is None:
            return []
        try:
            tkr = yf.Ticker(symbol)
            return list(tkr.options or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_expirations(%s) failed: %s", symbol, exc)
            return []

    def get_chain(
        self,
        symbol: str,
        expiry: str,
        right: Optional[str] = None,
        persist_cache: bool = False,
    ) -> List[OptionQuote]:
        """Return option contracts for ``symbol`` at ``expiry``.

        ``right`` filters 'C' | 'P'; None returns both.
        """
        cache_key = (symbol.upper(), expiry, right or "BOTH")
        hit = self._cache.get(cache_key)
        now = time.time()
        if hit and (now - hit[0]) < self._cache_ttl:
            return hit[1]

        yf = _import_yfinance()
        if yf is None:
            return []

        try:
            tkr = yf.Ticker(symbol)
            chain = tkr.option_chain(expiry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("option_chain(%s, %s) failed: %s", symbol, expiry, exc)
            return []

        spot = _spot_from_ticker(tkr) or 0.0
        quotes: List[OptionQuote] = []
        try:
            exp_date = date.fromisoformat(expiry)
            dte = (exp_date - date.today()).days
        except ValueError:
            dte = 0

        def rows_of(df, side: str) -> Iterable:
            if df is None or df.empty:
                return []
            return df.itertuples(index=False)

        wanted = {"C", "P"} if right is None else {right}
        if "C" in wanted:
            for row in rows_of(chain.calls, "C"):
                quotes.append(_row_to_quote(symbol, expiry, "C", row, spot, dte))
        if "P" in wanted:
            for row in rows_of(chain.puts, "P"):
                quotes.append(_row_to_quote(symbol, expiry, "P", row, spot, dte))

        self._cache[cache_key] = (now, quotes)

        if persist_cache:
            self._persist(symbol, expiry, right or "BOTH", spot, quotes)

        return quotes

    def find_leap_candidates(
        self,
        symbol: str,
        delta_min: float = 0.70,
        delta_max: float = 0.85,
        min_dte: int = 270,
        right: str = "C",
    ) -> List[OptionQuote]:
        """Return call candidates inside ``(delta_min, delta_max)`` with DTE >= ``min_dte``.

        Delta is approximated from moneyness when yfinance doesn't provide it
        (default). Callers needing precise delta should recompute via
        :func:`src.options.black_scholes.compute_greeks` with an IV input.
        """
        expirations = self.get_expirations(symbol)
        candidates: List[OptionQuote] = []
        for exp in expirations:
            try:
                exp_date = date.fromisoformat(exp)
            except ValueError:
                continue
            if (exp_date - date.today()).days < min_dte:
                continue
            quotes = self.get_chain(symbol, exp, right=right)
            for q in quotes:
                # Use moneyness proxy when delta is missing.
                delta_proxy = q.delta
                if delta_proxy is None:
                    # ITM calls skew high delta; use a simple linear proxy.
                    if q.moneyness == "ITM":
                        delta_proxy = 0.70
                    elif q.moneyness == "ATM":
                        delta_proxy = 0.50
                    else:
                        delta_proxy = 0.30
                if delta_min <= delta_proxy <= delta_max:
                    candidates.append(q)
        return candidates

    # -- internals ----------------------------------------------------

    def _persist(
        self,
        underlying: str,
        expiry: str,
        right: str,
        spot: float,
        quotes: List[OptionQuote],
    ) -> None:
        try:
            import sqlite3

            from src.options.storage import _resolve_db_path, init_options_schema
        except Exception as exc:  # noqa: BLE001
            logger.warning("options storage unavailable: %s", exc)
            return
        db = _resolve_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        try:
            init_options_schema(conn=conn)
            conn.execute(
                "INSERT OR IGNORE INTO option_chains_cache "
                "(underlying, expiry, right, fetched_at, spot_at_fetch, chain_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    underlying.upper(),
                    expiry,
                    right,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    spot,
                    json.dumps([asdict(q) for q in quotes]),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _row_to_quote(
    symbol: str,
    expiry: str,
    right: str,
    row,
    spot: float,
    dte: int,
) -> OptionQuote:
    strike = float(getattr(row, "strike", 0.0) or 0.0)
    bid = float(getattr(row, "bid", 0.0) or 0.0)
    ask = float(getattr(row, "ask", 0.0) or 0.0)
    last = float(getattr(row, "lastPrice", 0.0) or 0.0)
    volume_raw = getattr(row, "volume", 0) or 0
    oi_raw = getattr(row, "openInterest", 0) or 0
    iv = float(getattr(row, "impliedVolatility", 0.0) or 0.0)
    try:
        volume = int(volume_raw) if not math.isnan(float(volume_raw)) else 0
    except (TypeError, ValueError):
        volume = 0
    try:
        oi = int(oi_raw) if not math.isnan(float(oi_raw)) else 0
    except (TypeError, ValueError):
        oi = 0
    moneyness = _classify_moneyness(right, strike, spot) if spot > 0 else "OTM"
    return OptionQuote(
        underlying=symbol.upper(),
        expiry=expiry,
        right=right,
        strike=strike,
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
        open_interest=oi,
        implied_volatility=iv,
        delta=None,
        dte=dte,
        moneyness=moneyness,
    )


def get_leap_candidates(symbol: str, **kwargs) -> list[dict]:
    """Module-level shortcut returning plain dicts (for tool layers)."""
    fetcher = OptionsChainFetcher()
    return [asdict(q) for q in fetcher.find_leap_candidates(symbol, **kwargs)]
