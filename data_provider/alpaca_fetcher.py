# -*- coding: utf-8 -*-
"""Alpaca REST client (no SDK).

Provides:
    - get_bars(symbol, timeframe, start, end) -> list[dict]
    - get_news(symbols, limit) -> list[dict]        (Benzinga feed)
    - get_premarket(symbol) -> dict                   (latest premarket trade)

When ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY`` are missing, every method
gracefully returns an empty result and logs a warning — no exceptions. This
keeps Phase 0 functional even without an Alpaca account.

Reference: New-docs/10 (planned) / 06_REGIME_CLASSIFIER.md.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

__all__ = ["AlpacaFetcher"]


BARS_BASE = "https://data.alpaca.markets/v2/stocks"
NEWS_BASE = "https://data.alpaca.markets/v1beta1/news"


class AlpacaFetcher:
    """Minimal Alpaca client used by the Regime classifier and future Agent tools."""

    name = "AlpacaFetcher"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        timeout: float = 8.0,
    ):
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.api_secret = api_secret or os.getenv("APCA_API_SECRET_KEY")
        self.timeout = timeout

    # -- public --------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Historical bars.

        ``timeframe`` accepts Alpaca's strings (``'1Min'``, ``'5Min'``, ``'1Hour'``,
        ``'1Day'`` etc). Returns an empty list when the client is unconfigured
        or the request fails.
        """
        if not self.configured:
            logger.warning("Alpaca not configured; get_bars(%s) returns empty", symbol)
            return []
        params = {"timeframe": timeframe, "limit": limit}
        if start:
            params["start"] = _iso(start)
        if end:
            params["end"] = _iso(end)
        try:
            resp = requests.get(
                f"{BARS_BASE}/{symbol.upper()}/bars",
                params=params,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return list(resp.json().get("bars", []) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Alpaca get_bars(%s) failed: %s", symbol, exc)
            return []

    def get_news(
        self,
        symbols: list[str],
        limit: int = 50,
        start: Optional[datetime] = None,
    ) -> list[dict]:
        """Benzinga news feed filtered by symbols (each item has source + url)."""
        if not self.configured:
            logger.warning("Alpaca not configured; get_news(%s) returns empty", symbols)
            return []
        params = {
            "symbols": ",".join(s.upper() for s in symbols),
            "limit": limit,
            "sort": "desc",
        }
        if start:
            params["start"] = _iso(start)
        try:
            resp = requests.get(
                NEWS_BASE,
                params=params,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return list(resp.json().get("news", []) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Alpaca get_news failed: %s", exc)
            return []

    def get_premarket(self, symbol: str) -> dict:
        """Most recent premarket 1Min bar (or last trade). Returns {} on failure."""
        if not self.configured:
            logger.warning("Alpaca not configured; get_premarket(%s) returns empty", symbol)
            return {}
        # Convenience query: fetch last 5 1Min bars; caller picks the latest.
        bars = self.get_bars(symbol, timeframe="1Min", limit=5)
        return bars[-1] if bars else {}

    # -- internals -----------------------------------------------------

    def _auth_headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
        }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
