# -*- coding: utf-8 -*-
"""Alpaca REST client (no SDK).

Provides:
    - get_bars(symbol, timeframe, start, end) -> list[dict]
    - get_news(symbols, limit) -> list[dict]        (Benzinga feed)
    - get_premarket(symbol) -> dict  (completed premarket bar vs prior close)

When ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY`` are missing, every method
gracefully returns an empty result and logs a warning — no exceptions. This
keeps Phase 0 functional even without an Alpaca account.

Reference: New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md.
"""
from __future__ import annotations

import logging
import math
import os
import threading
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

__all__ = ["AlpacaFetcher"]


BARS_BASE = "https://data.alpaca.markets/v2/stocks"
NEWS_BASE = "https://data.alpaca.markets/v1beta1/news"
_NEW_YORK = ZoneInfo("America/New_York")
_PREMARKET_OPEN = datetime_time(hour=4)
_REGULAR_OPEN = datetime_time(hour=9, minute=30)
_MAX_PREMARKET_STALENESS = timedelta(minutes=5)


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
        self._request_status: dict[str, bool] = {}
        self._request_errors: dict[str, str] = {}
        self._status_lock = threading.Lock()

    # -- public --------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def request_succeeded(self, operation: str) -> Optional[bool]:
        """Return the most recent outcome without changing list-return APIs."""
        with self._status_lock:
            return self._request_status.get(operation)

    def last_request_error(self, operation: str) -> Optional[str]:
        with self._status_lock:
            return self._request_errors.get(operation)

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
        rows, _reason = self._request_bars(
            symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
        )
        return rows

    def _request_bars(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime],
        limit: int,
    ) -> tuple[list[dict], Optional[str]]:
        """Return bars plus a provider-state reason for evidence-aware callers."""
        if not self.configured:
            logger.warning("Alpaca not configured; get_bars(%s) returns empty", symbol)
            self._record_request(
                "bars",
                succeeded=False,
                error_text="not_configured",
            )
            return [], "not_configured"
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
            rows = list(resp.json().get("bars", []) or [])
            self._record_request("bars", succeeded=True)
            return rows, None
        except Exception as exc:  # noqa: BLE001
            reason = _classify_request_error(exc)
            safe_error = self._safe_error_text(exc)
            self._record_request(
                "bars",
                succeeded=False,
                error_text=safe_error,
            )
            logger.warning(
                "Alpaca get_bars(%s) failed (%s): %s",
                symbol,
                reason,
                safe_error,
            )
            return [], reason

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

    def get_premarket(
        self,
        symbol: str,
        *,
        target_date: Optional[date] = None,
        as_of: Optional[datetime] = None,
    ) -> dict:
        """Return a proved premarket move relative to the prior session close.

        Only completed 1-minute bars in the explicit New York 04:00–09:30
        session are eligible.  The percentage is measured against the latest
        *completed trading day* before ``target_date``; a minute bar's own open
        is deliberately never used as that reference.

        ``as_of`` is primarily a deterministic testing/backfill seam.  For the
        current market date, omitted ``as_of`` means the current UTC time and
        the in-progress minute is excluded.  Historical dates use the full
        completed premarket window.  Partial evidence is returned with
        ``_status='degraded'`` and ``pct_change=None`` so callers cannot mistake
        an unknown move for a flat market.
        """
        if not self.configured:
            logger.warning("Alpaca not configured; get_premarket(%s) returns empty", symbol)
            return {}

        observed_at = _aware_utc(as_of or datetime.now(timezone.utc))
        observed_market_date = observed_at.astimezone(_NEW_YORK).date()
        session_date = target_date or observed_market_date
        session_start = datetime.combine(
            session_date,
            _PREMARKET_OPEN,
            tzinfo=_NEW_YORK,
        )
        session_end = datetime.combine(
            session_date,
            _REGULAR_OPEN,
            tzinfo=_NEW_YORK,
        )

        base = {
            "symbol": symbol.upper(),
            "market_date": session_date.isoformat(),
            "session_start": session_start.isoformat(),
            "session_end": session_end.isoformat(),
            "requested_as_of": observed_at.isoformat(),
            "_source": self.name,
        }
        if session_date > observed_market_date:
            return {
                **base,
                "price": None,
                "previous_close": None,
                "previous_close_date": None,
                "pct_change": None,
                "as_of": None,
                "_status": "unavailable",
                "_reason": "future_market_date",
            }

        effective_end = (
            min(observed_at.astimezone(_NEW_YORK), session_end)
            if session_date == observed_market_date
            else session_end
        )
        # A bar timestamp denotes the beginning of its minute.  Flooring the
        # cutoff and filtering strictly below it excludes the live partial bar.
        completed_bar_cutoff = effective_end.replace(second=0, microsecond=0)
        if completed_bar_cutoff <= session_start:
            return {
                **base,
                "price": None,
                "previous_close": None,
                "previous_close_date": None,
                "pct_change": None,
                "as_of": None,
                "_status": "unavailable",
                "_reason": "premarket_not_started_or_no_completed_bar",
            }

        minute_bars, minute_request_reason = self._request_bars(
            symbol,
            timeframe="1Min",
            start=session_start,
            end=completed_bar_cutoff,
            limit=10000,
        )
        if minute_request_reason is not None:
            return {
                **base,
                "price": None,
                "previous_close": None,
                "previous_close_date": None,
                "pct_change": None,
                "as_of": None,
                "_status": "unavailable",
                "_reason": f"premarket_minute_{minute_request_reason}",
            }
        eligible_minutes = _bars_in_window(
            minute_bars,
            start=session_start,
            end=completed_bar_cutoff,
        )
        if not eligible_minutes:
            return {
                **base,
                "price": None,
                "previous_close": None,
                "previous_close_date": None,
                "pct_change": None,
                "as_of": None,
                "_status": "unavailable",
                "_reason": "no_completed_premarket_bar",
            }

        latest_timestamp, latest_bar = eligible_minutes[-1]
        latest_price = _positive_float(latest_bar.get("c"))
        evidence_as_of = min(
            latest_timestamp + timedelta(minutes=1),
            completed_bar_cutoff,
        )
        if latest_price is None:
            return {
                **base,
                **latest_bar,
                "price": None,
                "previous_close": None,
                "previous_close_date": None,
                "pct_change": None,
                "as_of": evidence_as_of.astimezone(timezone.utc).isoformat(),
                "_status": "degraded",
                "_reason": "premarket_close_missing",
            }

        expected_previous_session = _previous_xnys_session(session_date)
        if expected_previous_session is None:
            return {
                **base,
                **latest_bar,
                "price": latest_price,
                "previous_close": None,
                "previous_close_date": None,
                "pct_change": None,
                "as_of": evidence_as_of.astimezone(timezone.utc).isoformat(),
                "_status": "degraded",
                "_reason": "previous_xnys_session_unresolved",
            }

        daily_start = datetime.combine(
            session_date - timedelta(days=14),
            datetime_time.min,
            tzinfo=_NEW_YORK,
        )
        daily_bars, daily_request_reason = self._request_bars(
            symbol,
            timeframe="1Day",
            start=daily_start,
            end=session_start,
            limit=15,
        )
        if daily_request_reason is not None:
            return {
                **base,
                **latest_bar,
                "price": latest_price,
                "previous_close": None,
                "previous_close_date": None,
                "pct_change": None,
                "as_of": evidence_as_of.astimezone(timezone.utc).isoformat(),
                "_status": "degraded",
                "_reason": f"previous_close_{daily_request_reason}",
            }
        prior_daily = [
            (bar_timestamp, bar)
            for bar_timestamp, bar in _timestamped_bars(daily_bars)
            if (
                bar_timestamp.astimezone(_NEW_YORK).date()
                == expected_previous_session
            )
        ]
        prior_daily.sort(key=lambda item: item[0])
        previous_bar = prior_daily[-1] if prior_daily else None
        previous_close = (
            _positive_float(previous_bar[1].get("c"))
            if previous_bar is not None
            else None
        )
        previous_close_date = (
            previous_bar[0].astimezone(_NEW_YORK).date().isoformat()
            if previous_bar is not None
            else None
        )
        if previous_close is None:
            return {
                **base,
                **latest_bar,
                "price": latest_price,
                "previous_close": None,
                "previous_close_date": previous_close_date,
                "pct_change": None,
                "as_of": evidence_as_of.astimezone(timezone.utc).isoformat(),
                "_status": "degraded",
                "_reason": "previous_completed_close_missing",
            }

        if completed_bar_cutoff - evidence_as_of > _MAX_PREMARKET_STALENESS:
            return {
                **base,
                **latest_bar,
                "price": latest_price,
                "previous_close": previous_close,
                "previous_close_date": previous_close_date,
                "pct_change": None,
                "as_of": evidence_as_of.astimezone(timezone.utc).isoformat(),
                "_status": "degraded",
                "_reason": "premarket_bar_stale",
            }

        return {
            **base,
            **latest_bar,
            "price": latest_price,
            "previous_close": previous_close,
            "previous_close_date": previous_close_date,
            "pct_change": (
                (latest_price - previous_close) / previous_close * 100.0
            ),
            "as_of": evidence_as_of.astimezone(timezone.utc).isoformat(),
            "_status": "ready",
            "_reason": None,
        }

    # -- internals -----------------------------------------------------

    def _auth_headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
        }

    def _record_request(
        self,
        operation: str,
        *,
        succeeded: bool,
        error_text: Optional[str] = None,
    ) -> None:
        with self._status_lock:
            self._request_status[operation] = succeeded
            if error_text is None:
                self._request_errors.pop(operation, None)
            else:
                self._request_errors[operation] = error_text[:500]

    def _safe_error_text(self, error: BaseException) -> str:
        text = f"{type(error).__name__}: {error}"
        for secret in (self.api_key, self.api_secret):
            if secret:
                text = text.replace(str(secret), "<redacted>")
        return text[:500]


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bar_timestamp(bar: dict) -> Optional[datetime]:
    raw = bar.get("t")
    if isinstance(raw, datetime):
        return _aware_utc(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return _aware_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _timestamped_bars(bars: list[dict]) -> list[tuple[datetime, dict]]:
    result: list[tuple[datetime, dict]] = []
    for bar in bars:
        timestamp = _bar_timestamp(bar)
        if timestamp is not None:
            result.append((timestamp, bar))
    return result


def _bars_in_window(
    bars: list[dict],
    *,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, dict]]:
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    result = [
        (timestamp, bar)
        for timestamp, bar in _timestamped_bars(bars)
        if start_utc <= timestamp < end_utc
    ]
    result.sort(key=lambda item: item[0])
    return result


def _positive_float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _classify_request_error(error: BaseException) -> str:
    if isinstance(error, requests.Timeout):
        return "timeout"
    status_code = getattr(getattr(error, "response", None), "status_code", None)
    text = str(error).lower()
    if status_code in {401, 403} or "401" in text or "403" in text:
        return "permission_denied"
    if status_code == 429 or "429" in text:
        return "rate_limited"
    return "request_failed"


def _previous_xnys_session(session_date: date) -> Optional[date]:
    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS")
        if not calendar.is_session(session_date):
            return None
        value = calendar.previous_session(session_date)
        converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if isinstance(converted, datetime):
            return converted.date()
        if isinstance(converted, date):
            return converted
        return date.fromisoformat(str(value)[:10])
    except Exception as exc:  # noqa: BLE001 - reference dates must fail closed
        logger.warning(
            "Alpaca could not resolve previous XNYS session for %s: %s",
            session_date,
            exc,
        )
        return None
