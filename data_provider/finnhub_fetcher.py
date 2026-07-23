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
import threading
from datetime import date
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
        self._request_status: dict[str, bool] = {}
        self._request_errors: dict[str, str] = {}
        self._status_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def request_succeeded(self, operation: str) -> Optional[bool]:
        """Return the last request outcome for an operation, if attempted."""
        with self._status_lock:
            return self._request_status.get(operation)

    def last_request_error(self, operation: str) -> Optional[str]:
        with self._status_lock:
            return self._request_errors.get(operation)

    def _record_request(
        self,
        operation: str,
        *,
        succeeded: bool,
        error: Optional[BaseException] = None,
    ) -> None:
        with self._status_lock:
            self._request_status[operation] = succeeded
            if error is None:
                self._request_errors.pop(operation, None)
            else:
                self._request_errors[operation] = (
                    f"{type(error).__name__}: {error}"
                )

    def get_economic_calendar(
        self, from_: Optional[date] = None, to: Optional[date] = None
    ) -> list[dict]:
        if not self.configured:
            logger.warning("Finnhub not configured; economic_calendar returns empty")
            self._record_request("economic_calendar", succeeded=False)
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
            rows = list(resp.json().get("economicCalendar", []) or [])
            self._record_request("economic_calendar", succeeded=True)
            return rows
        except Exception as exc:  # noqa: BLE001
            self._record_request(
                "economic_calendar", succeeded=False, error=exc
            )
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
            self._record_request("earnings_calendar", succeeded=False)
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
            rows = list(resp.json().get("earningsCalendar", []) or [])
            self._record_request("earnings_calendar", succeeded=True)
            return rows
        except Exception as exc:  # noqa: BLE001
            self._record_request(
                "earnings_calendar", succeeded=False, error=exc
            )
            logger.warning("Finnhub earnings_calendar failed: %s", exc)
            return []

    def get_recommendation_trends(self, symbol: str) -> list[dict]:
        if not self.configured:
            logger.warning("Finnhub not configured; recommendation_trends returns empty")
            self._record_request("recommendation_trends", succeeded=False)
            return []
        try:
            resp = requests.get(
                f"{BASE}/stock/recommendation",
                params={"symbol": symbol.upper(), "token": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            rows = list(resp.json() or [])
            self._record_request("recommendation_trends", succeeded=True)
            return rows
        except Exception as exc:  # noqa: BLE001
            self._record_request(
                "recommendation_trends", succeeded=False, error=exc
            )
            logger.warning("Finnhub recommendation_trends(%s) failed: %s", symbol, exc)
            return []
