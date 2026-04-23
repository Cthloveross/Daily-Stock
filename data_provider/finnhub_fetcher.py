# -*- coding: utf-8 -*-
"""Finnhub REST client.

Public endpoints used:
    - /calendar/economic              (macro events)
    - /calendar/earnings              (earnings dates)
    - /stock/recommendation           (analyst ratings trend)

Without ``FINNHUB_API_KEY`` every method returns an empty iterable + warning.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

__all__ = ["FinnhubFetcher"]


BASE = "https://finnhub.io/api/v1"


class FinnhubFetcher:
    """Minimal Finnhub client for Regime + later Agent tools."""

    name = "FinnhubFetcher"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 8.0):
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def get_economic_calendar(
        self, from_: Optional[date] = None, to: Optional[date] = None
    ) -> list[dict]:
        if not self.configured:
            logger.warning("Finnhub not configured; economic_calendar returns empty")
            return []
        params = {"token": self.api_key}
        if from_:
            params["from"] = from_.isoformat()
        if to:
            params["to"] = to.isoformat()
        try:
            resp = requests.get(
                f"{BASE}/calendar/economic", params=params, timeout=self.timeout
            )
            resp.raise_for_status()
            return list(resp.json().get("economicCalendar", []) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Finnhub economic_calendar failed: %s", exc)
            return []

    def get_earnings_calendar(
        self,
        from_: Optional[date] = None,
        to: Optional[date] = None,
        symbol: Optional[str] = None,
    ) -> list[dict]:
        if not self.configured:
            logger.warning("Finnhub not configured; earnings_calendar returns empty")
            return []
        params = {"token": self.api_key}
        if from_:
            params["from"] = from_.isoformat()
        if to:
            params["to"] = to.isoformat()
        if symbol:
            params["symbol"] = symbol.upper()
        try:
            resp = requests.get(
                f"{BASE}/calendar/earnings", params=params, timeout=self.timeout
            )
            resp.raise_for_status()
            return list(resp.json().get("earningsCalendar", []) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Finnhub earnings_calendar failed: %s", exc)
            return []

    def get_recommendation_trends(self, symbol: str) -> list[dict]:
        if not self.configured:
            logger.warning("Finnhub not configured; recommendation_trends returns empty")
            return []
        try:
            resp = requests.get(
                f"{BASE}/stock/recommendation",
                params={"symbol": symbol.upper(), "token": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return list(resp.json() or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Finnhub recommendation_trends(%s) failed: %s", symbol, exc)
            return []
