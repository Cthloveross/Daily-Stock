# -*- coding: utf-8 -*-
"""Moomoo OpenAPI option-chain fetcher.

Phase C of the Moomoo integration. Replaces the yfinance option chain (which
has known gaps: missing strikes, no Greeks, IV occasionally 0) with Moomoo's
``get_option_chain`` — that endpoint already returns server-computed
``implied_volatility`` and Greeks (delta/gamma/vega/theta/rho), so we don't
have to reverse-solve Black-Scholes.

Surface
-------
- :func:`fetch_chain_via_moomoo(symbol, expiry)` →
  ``list[OptionQuote]`` (same dataclass as the yfinance fetcher)
- :func:`compute_atm_iv_moomoo(symbol, ref_date)` →
  ``(atm_iv: float | None, expiry: str)``
- :func:`get_expirations_moomoo(symbol)` → ``list[str]`` ISO dates

All three short-circuit to a no-op (returning empty / None) when
``MOOMOO_OPEND_ENABLED!=true`` so callers can do ``moomoo first → yfinance
fallback`` without conditional branching at every call site.

Caveats
-------
- Moomoo IV is in **percent form** in the API (e.g. ``20.0`` = 20%); we
  convert to decimal (0.20) to match the yfinance convention used by
  :mod:`src.options.iv_rank`.
- Strike date format from ``get_option_expiration_date`` is ``YYYY-MM-DD``.
- Equity option codes look like ``US.AAPL250620C250000``; we extract the
  numeric strike via the ``strike_price`` column directly, never parse the
  symbol string.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

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


def _is_alive(ctx) -> bool:
    """Cheap health probe: ping global state. False on any failure."""
    if ctx is None:
        return False
    try:
        from moomoo import RET_OK

        ret, _ = ctx.get_global_state()
        return ret == RET_OK
    except Exception:  # noqa: BLE001
        return False


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
        if _ctx_singleton is not None and not _is_alive(_ctx_singleton):
            logger.info("[moomoo_options] cached ctx dead, reconnecting")
            try:
                _ctx_singleton.close()
            except Exception:  # noqa: BLE001
                pass
            _ctx_singleton = None
        if _ctx_singleton is None:
            host, port = _endpoint()
            try:
                from moomoo import OpenQuoteContext

                _ctx_singleton = OpenQuoteContext(host=host, port=port)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[moomoo_options] OpenD connect failed: %s", exc)
                return None
        return _ctx_singleton


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
    ctx = _get_ctx()
    if ctx is None:
        return []
    try:
        from moomoo import RET_OK

        ret, data = ctx.get_option_expiration_date(code=_to_moomoo_underlying(symbol))
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
    ctx = _get_ctx()
    if ctx is None:
        return None
    try:
        from moomoo import RET_OK

        ret, data = ctx.get_market_snapshot([_to_moomoo_underlying(symbol)])
        if ret != RET_OK or data is None or data.empty:
            return None
        last = data.iloc[0].get("last_price")
        return float(last) if last not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def fetch_chain_via_moomoo(symbol: str, expiry: str) -> List[OptionQuote]:
    """Fetch the option chain for one expiry via Moomoo.

    Args:
        symbol: equity ticker (``AAPL`` / ``US.AAPL``).
        expiry: ISO date (``YYYY-MM-DD``).

    Returns ``[]`` on any failure or when not enabled.
    """
    ctx = _get_ctx()
    if ctx is None:
        return []
    try:
        from moomoo import RET_OK
    except ImportError:
        return []

    underlying = _to_moomoo_underlying(symbol)
    try:
        ret, data = ctx.get_option_chain(code=underlying, start=expiry, end=expiry)
        if ret != RET_OK or data is None or data.empty:
            logger.info("[moomoo_options] empty chain for %s %s: %s", underlying, expiry, data)
            return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[moomoo_options] get_option_chain failed: %s", exc)
        return []

    spot = _spot_for_classification(symbol)
    today = date.today()
    try:
        exp_d = date.fromisoformat(expiry)
        dte = max(0, (exp_d - today).days)
    except ValueError:
        dte = 0

    out: List[OptionQuote] = []
    for _, row in data.iterrows():
        d = row.to_dict()
        right_raw = str(d.get("option_type") or "").upper()
        right = "C" if right_raw == "CALL" else "P" if right_raw == "PUT" else right_raw[:1]
        strike = float(d.get("strike_price") or 0) or 0.0
        if strike <= 0:
            continue
        # Moomoo IV comes as percent (20 = 20%) — normalise to decimal.
        iv_pct = d.get("implied_volatility")
        try:
            iv = float(iv_pct) / 100.0 if iv_pct not in (None, "") else 0.0
        except (TypeError, ValueError):
            iv = 0.0

        moneyness = (
            _classify_moneyness(right, strike, spot) if spot else ""
        )
        out.append(
            OptionQuote(
                underlying=symbol.upper(),
                expiry=expiry,
                right=right,
                strike=strike,
                bid=float(d.get("bid_price") or 0) or 0.0,
                ask=float(d.get("ask_price") or 0) or 0.0,
                last=float(d.get("last_price") or 0) or 0.0,
                volume=int(d.get("volume") or 0),
                open_interest=int(d.get("open_interest") or 0),
                implied_volatility=iv,
                delta=_safe_float(d.get("delta")),
                dte=dte,
                moneyness=moneyness,
            )
        )
    return out


def _safe_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_atm_iv_moomoo(
    symbol: str, ref_date: Optional[date] = None
) -> tuple[Optional[float], str]:
    """Return ``(atm_iv_decimal, chosen_expiry_iso)`` via Moomoo.

    Drop-in replacement for :func:`src.options.iv_rank.compute_atm_iv` —
    same return shape so callers can fall back without conversion.
    """
    if not _enabled():
        return None, ""
    expirations = get_expirations_moomoo(symbol)
    if not expirations:
        return None, ""

    target = ref_date or date.today()
    chosen = next(
        (e for e in expirations if _safe_iso_date(e) and _safe_iso_date(e) >= target),
        expirations[-1],
    )

    chain = fetch_chain_via_moomoo(symbol, chosen)
    if not chain:
        return None, chosen
    spot = _spot_for_classification(symbol)
    if spot is None or spot <= 0:
        return None, chosen
    # Pick ATM call by minimising |strike - spot|.
    calls = [q for q in chain if q.right == "C" and q.strike > 0]
    if not calls:
        return None, chosen
    atm = min(calls, key=lambda q: abs(q.strike - spot))
    iv = atm.implied_volatility
    if not iv or iv <= 0:
        # Moomoo only populates IV during market hours for LV1+ permissions.
        # Outside RTH the chain still returns rows but IV/Greeks are 0. We
        # signal "no IV here" so iv_rank.py falls back to yfinance instead
        # of treating 0 as a legit value.
        logger.info(
            "[moomoo_options] %s ATM call at %s has IV=%s (likely outside RTH "
            "or LV1 permissions don't push live greeks); falling back",
            symbol, chosen, iv,
        )
        return None, chosen
    return iv, chosen


def _safe_iso_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None
