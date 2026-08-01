# -*- coding: utf-8 -*-
"""
MoomooFetcher - Moomoo OpenAPI (OpenD daemon)
===============================================

Read-only market data via Moomoo OpenAPI. Routes through a local OpenD
daemon (default ``127.0.0.1:11111``) which the user must launch and log in
to manually with their Moomoo account.

Capabilities (Phase A):
- Daily K-line / weekly / monthly via ``request_history_kline``
- Intraday K-line (1m / 5m / 15m / 30m / 60m) for US/HK
- Realtime quote via ``get_market_snapshot`` (single call, no subscription)

Future (see New-docs/integrations/moomoo-roadmap.md):
- Phase B: live order/position sync into journal
- Phase C: option chain + IV (replace yfinance options path)

Configuration (env or src/config.py):
    MOOMOO_OPEND_ENABLED=true|false   # default false; off = fetcher skips itself
    MOOMOO_OPEND_HOST=127.0.0.1
    MOOMOO_OPEND_PORT=11111
    MOOMOO_PRIORITY=2                 # default 2 (between Tushare(1) and akshare(3))

Requires the optional ``moomoo-api`` Python SDK (>= 10.4.6408). Install with:

    pip install moomoo-api

If the SDK is not installed or OPEND_ENABLED is false, this fetcher boots in
"shelf" mode (priority 99) and the manager naturally skips it without error.
"""
from __future__ import annotations

import logging
import math
import os
import threading
from datetime import (
    date,
    datetime,
    time as datetime_time,
    timedelta,
    timezone,
)
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.moomoo_runtime import (
    MoomooRuntimeError,
    create_ready_quote_context,
    probe_opend_tcp,
    quote_context_is_ready,
)

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote
from .us_index_mapping import is_us_stock_code, get_us_index_yf_symbol

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive_float(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _moomoo_market_timestamp(value: object) -> Optional[datetime]:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(_NEW_YORK)
    else:
        timestamp = timestamp.tz_convert(_NEW_YORK)
    converted = timestamp.to_pydatetime()
    return converted if isinstance(converted, datetime) else None


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
            "Moomoo could not resolve previous XNYS session for %s: %s",
            session_date,
            exc,
        )
        return None


def _premarket_unavailable(
    base: dict,
    reason: str,
    *,
    status: str = "unavailable",
) -> dict:
    return {
        **base,
        "price": None,
        "previous_close": None,
        "previous_close_date": None,
        "pct_change": None,
        "as_of": None,
        "_status": status,
        "_reason": reason,
    }


# Map our internal interval string → moomoo `KLType` attribute name.
_INTRADAY_KTYPE: dict[str, str] = {
    "1m": "K_1M",
    "5m": "K_5M",
    "15m": "K_15M",
    "30m": "K_30M",
    "60m": "K_60M",
    "1h": "K_60M",
}

# Bound one logical history request so a malformed/repeating continuation key
# cannot spin forever or accumulate unbounded data in memory.  At 1,000 bars
# per page this still permits up to 128,000 intraday bars in one response.
_INTRADAY_MAX_PAGES = 128

# Moomoo's ``time_key`` is expressed in the exchange's local wall-clock time
# and does not carry an offset. Preserve that market-time meaning before the
# value crosses the API boundary so browser clients can compare it to UTC
# execution timestamps without guessing from the viewer's local timezone.
_MARKET_TIMEZONES: dict[str, str] = {
    "US": "America/New_York",
    "HK": "Asia/Shanghai",
    "SH": "Asia/Shanghai",
    "SZ": "Asia/Shanghai",
    "BJ": "Asia/Shanghai",
}
_NEW_YORK = ZoneInfo("America/New_York")
_PREMARKET_OPEN = datetime_time(hour=4)
_REGULAR_OPEN = datetime_time(hour=9, minute=30)
_MAX_PREMARKET_STALENESS = timedelta(minutes=5)


class MoomooFetcher(BaseFetcher):
    """Moomoo OpenAPI fetcher. Off by default; shelves itself when not configured."""

    name = "MoomooFetcher"

    def __init__(self) -> None:
        self.host = (os.environ.get("MOOMOO_OPEND_HOST") or "127.0.0.1").strip()
        try:
            self.port = int((os.environ.get("MOOMOO_OPEND_PORT") or "11111").strip())
        except ValueError:
            self.port = 11111
        self.enabled = _bool_env("MOOMOO_OPEND_ENABLED", False)
        try:
            requested_priority = int(os.environ.get("MOOMOO_PRIORITY", "2") or "2")
        except ValueError:
            requested_priority = 2
        self._requested_priority = requested_priority
        self.priority = 99  # park at the back until we confirm SDK + enabled

        self._ctx: Optional[object] = None
        self._ctx_lock = threading.RLock()
        self._sdk_ok = False

        if not self.enabled:
            logger.info(
                "[MoomooFetcher] disabled (MOOMOO_OPEND_ENABLED!=true), shelving at priority 99"
            )
            return

        # Lazy probe: only import SDK when explicitly enabled, to avoid a hard
        # dependency at import time for users who don't use moomoo at all.
        # IMPORTANT: `import moomoo` alone is not enough — Python may resolve
        # an empty namespace-package directory of the same name. Probe for a
        # real symbol (`OpenQuoteContext`) to confirm the actual SDK is loaded.
        try:
            from moomoo import OpenQuoteContext  # noqa: F401
        except ImportError:
            logger.warning(
                "[MoomooFetcher] enabled but `moomoo-api` SDK not installed. "
                "`pip install moomoo-api` then restart. Shelving at priority 99."
            )
            return
        except Exception as exc:  # noqa: BLE001 - optional SDK must fail open
            logger.warning(
                "[MoomooFetcher] enabled but SDK initialization failed (%s); "
                "shelving at priority 99.",
                type(exc).__name__,
            )
            return

        self._sdk_ok = True
        self.priority = self._requested_priority
        logger.info(
            "[MoomooFetcher] enabled OpenD=%s:%s priority=%d",
            self.host,
            self.port,
            self.priority,
        )

    # ------------------------------------------------------------------
    # OpenQuoteContext lifecycle
    # ------------------------------------------------------------------
    def _is_ctx_alive(self) -> bool:
        """Inspect connection state without issuing a blocking SDK query."""
        return quote_context_is_ready(self._ctx)

    def _get_ctx(self):
        """Lazy-create + cache the OpenQuoteContext.

        Health-checks on each call — if the cached context is dead (e.g.
        OpenD bounced), tear it down and reconnect. Thread-safe.
        """
        if not self.enabled or not self._sdk_ok:
            raise DataFetchError("MoomooFetcher 未启用或 SDK 未安装")
        with self._ctx_lock:
            if self._ctx is not None and (
                not probe_opend_tcp(self.host, self.port)
                or not self._is_ctx_alive()
            ):
                logger.info("[MoomooFetcher] cached ctx dead, reconnecting")
                try:
                    self._ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ctx = None
            if self._ctx is None:
                try:
                    self._ctx = create_ready_quote_context(
                        host=self.host,
                        port=self.port,
                    )
                except MoomooRuntimeError as exc:
                    raise DataFetchError(
                        f"无法连接 OpenD ({self.host}:{self.port})：{exc}"
                    ) from exc
            return self._ctx

    def close(self) -> None:
        """Tear down the OpenD connection. Safe to call repeatedly."""
        with self._ctx_lock:
            if self._ctx is not None:
                try:
                    self._ctx.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[MoomooFetcher] close error (ignored): %s", exc)
                self._ctx = None

    # ------------------------------------------------------------------
    # Code conversion
    # ------------------------------------------------------------------
    def _to_moomoo_code(self, stock_code: str) -> str:
        """Convert our canonical code → moomoo dotted format.

        Examples:
            'AAPL'      -> 'US.AAPL'
            'hk00700'   -> 'HK.00700'
            'HK00700'   -> 'HK.00700'
            '0700.HK'   -> 'HK.00700'
            '600519'    -> 'SH.600519'
            '000001'    -> 'SZ.000001'
            'SPX' (US index) -> 'US.SPX' (best effort)
        """
        code = (stock_code or "").strip().upper()
        if not code:
            raise ValueError("empty stock code")
        # already prefixed
        if "." in code and code.split(".")[0] in {"US", "HK", "SH", "SZ", "BJ"}:
            return code
        # `0700.HK` style
        if code.endswith(".HK"):
            num = code.split(".")[0].lstrip("0").zfill(5)
            return f"HK.{num}"
        if code.endswith(".SS") or code.endswith(".SH"):
            return f"SH.{code.split('.')[0]}"
        if code.endswith(".SZ"):
            return f"SZ.{code.split('.')[0]}"
        # `HK00700` style
        if code.startswith("HK") and code[2:].isdigit():
            num = code[2:].lstrip("0").zfill(5)
            return f"HK.{num}"
        # US index (^GSPC etc.) — moomoo uses US.NDX / US.SPY etc.; pass through
        yf_symbol, _ = get_us_index_yf_symbol(code)
        if yf_symbol:
            # Best-effort — moomoo's index symbology differs; user should verify
            return f"US.{code}"
        # Plain US ticker (1-5 letters, optional .B / .A suffix)
        if is_us_stock_code(code):
            return f"US.{code}"
        # CN A-shares
        if code.isdigit() and len(code) == 6:
            if code.startswith(("600", "601", "603", "688", "689")):
                return f"SH.{code}"
            if code.startswith(("000", "001", "002", "003", "300", "301")):
                return f"SZ.{code}"
            if code.startswith(("4", "8", "9")):
                return f"BJ.{code}"
        # Fallback: treat as US (most common case via this fetcher)
        return f"US.{code}"

    # ------------------------------------------------------------------
    # Daily K-line (BaseFetcher abstract methods)
    # ------------------------------------------------------------------
    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        from moomoo import KLType, AuType, KL_FIELD, RET_OK

        ctx = self._get_ctx()
        mcode = self._to_moomoo_code(stock_code)
        logger.info(
            "[Moomoo] history_kline daily code=%s start=%s end=%s",
            mcode,
            start_date,
            end_date,
        )
        try:
            ret, data, _page_key = ctx.request_history_kline(
                code=mcode,
                start=start_date,
                end=end_date,
                ktype=KLType.K_DAY,
                autype=AuType.QFQ,
                fields=[KL_FIELD.ALL],
                max_count=1000,
            )
        except Exception as exc:  # noqa: BLE001
            raise DataFetchError(f"Moomoo daily request raised: {exc}") from exc

        if ret != RET_OK:
            raise DataFetchError(f"Moomoo history_kline failed: {data}")
        if data is None or data.empty:
            raise DataFetchError(f"Moomoo returned empty for {mcode}")
        return data

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """Normalize moomoo daily K-line to our STANDARD_COLUMNS shape."""
        df = df.copy()
        df = df.rename(
            columns={
                "time_key": "date",
                "turnover": "amount",
                "change_rate": "pct_chg",
            }
        )
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = (df["close"].pct_change() * 100).fillna(0).round(2)
        df["code"] = stock_code
        keep = ["code"] + STANDARD_COLUMNS
        df = df[[c for c in keep if c in df.columns]]
        return df.dropna(subset=["close"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Evidence-aware US premarket snapshot
    # ------------------------------------------------------------------
    def get_premarket(
        self,
        symbol: str,
        *,
        target_date: Optional[date] = None,
        as_of: Optional[datetime] = None,
    ) -> dict:
        """Return one proved US premarket move using unadjusted Moomoo bars.

        The method deliberately does not reuse ``fetch_intraday``: Regime
        evidence needs a fixed 04:00–09:30 ET window, one frozen ``as_of``,
        exclusion of the in-progress minute, and Moomoo's unadjusted
        ``last_close`` reference for the exact previous XNYS session.
        """
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
        mcode = self._to_moomoo_code(symbol)
        base = {
            "symbol": symbol.upper(),
            "market_date": session_date.isoformat(),
            "session_start": session_start.isoformat(),
            "session_end": session_end.isoformat(),
            "requested_as_of": observed_at.isoformat(),
            "_source": self.name,
        }
        if not mcode.startswith("US."):
            return _premarket_unavailable(base, "unsupported_market")
        if session_date > observed_market_date:
            return _premarket_unavailable(base, "future_market_date")

        effective_end = (
            min(observed_at.astimezone(_NEW_YORK), session_end)
            if session_date == observed_market_date
            else session_end
        )
        completed_bar_cutoff = effective_end.replace(second=0, microsecond=0)
        if completed_bar_cutoff <= session_start:
            return _premarket_unavailable(
                base,
                "premarket_not_started_or_no_completed_bar",
            )

        expected_previous_session = _previous_xnys_session(session_date)
        if expected_previous_session is None:
            return _premarket_unavailable(
                base,
                "previous_xnys_session_unresolved",
                status="degraded",
            )

        from moomoo import AuType, KLType, KL_FIELD, RET_OK

        ctx = self._get_ctx()
        common_request = {
            "code": mcode,
            "start": session_date.isoformat(),
            "end": session_date.isoformat(),
            "ktype": KLType.K_1M,
            "autype": AuType.NONE,
            "fields": [KL_FIELD.ALL],
            "max_count": 1000,
            "extended_time": True,
        }
        frames: list[pd.DataFrame] = []
        page_req_key = None
        seen_page_keys: set[tuple[str, str]] = set()

        for page_number in range(1, _INTRADAY_MAX_PAGES + 1):
            request = dict(common_request)
            if page_req_key is not None:
                request["page_req_key"] = page_req_key
            try:
                ret, data, next_page_key = ctx.request_history_kline(**request)
            except Exception as exc:  # noqa: BLE001
                raise DataFetchError(
                    f"Moomoo premarket page {page_number} request raised: {exc}"
                ) from exc
            if ret != RET_OK:
                raise DataFetchError(
                    f"Moomoo premarket page {page_number} failed: {data}"
                )
            if data is not None and not data.empty:
                frames.append(data.copy())
            elif next_page_key is not None:
                raise DataFetchError(
                    "Moomoo premarket returned an empty page with a continuation key"
                )
            if next_page_key is None:
                break
            key_identity = (type(next_page_key).__name__, repr(next_page_key))
            if key_identity in seen_page_keys:
                raise DataFetchError(
                    "Moomoo premarket returned a repeated continuation key"
                )
            seen_page_keys.add(key_identity)
            page_req_key = next_page_key
        else:
            raise DataFetchError(
                "Moomoo premarket exceeded the safe pagination limit "
                f"({_INTRADAY_MAX_PAGES} pages)"
            )

        if not frames:
            return _premarket_unavailable(base, "no_completed_premarket_bar")
        combined = pd.concat(frames, ignore_index=True)
        if "time_key" not in combined.columns:
            return _premarket_unavailable(
                base,
                "premarket_timestamp_missing",
                status="degraded",
            )

        eligible: list[tuple[datetime, dict]] = []
        for row in combined.to_dict(orient="records"):
            timestamp = _moomoo_market_timestamp(row.get("time_key"))
            if (
                timestamp is not None
                and session_start <= timestamp < completed_bar_cutoff
            ):
                eligible.append((timestamp, row))
        eligible.sort(key=lambda item: item[0])
        if not eligible:
            return _premarket_unavailable(base, "no_completed_premarket_bar")

        latest_timestamp, latest_bar = eligible[-1]
        latest_price = _positive_float(latest_bar.get("close"))
        evidence_as_of = min(
            latest_timestamp + timedelta(minutes=1),
            completed_bar_cutoff,
        )
        evidence_as_of_utc = evidence_as_of.astimezone(timezone.utc).isoformat()
        if latest_price is None:
            return {
                **_premarket_unavailable(
                    base,
                    "premarket_close_missing",
                    status="degraded",
                ),
                "as_of": evidence_as_of_utc,
            }

        previous_close = None
        previous_close_evidence_at = None
        for timestamp, row in reversed(eligible):
            candidate = _positive_float(row.get("last_close"))
            if candidate is not None:
                previous_close = candidate
                previous_close_evidence_at = timestamp
                break
        if previous_close is None:
            return {
                **_premarket_unavailable(
                    base,
                    "previous_close_last_close_missing",
                    status="degraded",
                ),
                "price": latest_price,
                "previous_close_date": expected_previous_session.isoformat(),
                "as_of": evidence_as_of_utc,
            }

        if completed_bar_cutoff - evidence_as_of > _MAX_PREMARKET_STALENESS:
            return {
                **base,
                "price": latest_price,
                "previous_close": previous_close,
                "previous_close_date": expected_previous_session.isoformat(),
                "previous_close_field": "last_close",
                "previous_close_evidence_at": (
                    previous_close_evidence_at.isoformat()
                    if previous_close_evidence_at is not None
                    else None
                ),
                "pct_change": None,
                "as_of": evidence_as_of_utc,
                "_status": "degraded",
                "_reason": "premarket_bar_stale",
            }

        return {
            **base,
            "price": latest_price,
            "previous_close": previous_close,
            "previous_close_date": expected_previous_session.isoformat(),
            "previous_close_field": "last_close",
            "previous_close_evidence_at": (
                previous_close_evidence_at.isoformat()
                if previous_close_evidence_at is not None
                else None
            ),
            "pct_change": (
                (latest_price - previous_close) / previous_close * 100.0
            ),
            "as_of": evidence_as_of_utc,
            "_status": "ready",
            "_reason": None,
        }

    # ------------------------------------------------------------------
    # Intraday K-line (mirrors YfinanceFetcher.fetch_intraday signature)
    # ------------------------------------------------------------------
    def fetch_intraday(self, stock_code: str, interval: str, days: int = 30) -> pd.DataFrame:
        ktype_attr = _INTRADAY_KTYPE.get(interval)
        if ktype_attr is None:
            raise ValueError(
                f"Moomoo intraday: unsupported interval '{interval}'. "
                f"Supported: {sorted(_INTRADAY_KTYPE)}"
            )
        from moomoo import KLType, AuType, KL_FIELD, RET_OK

        ktype = getattr(KLType, ktype_attr)
        ctx = self._get_ctx()
        mcode = self._to_moomoo_code(stock_code)
        end = datetime.now().date()
        start = end - timedelta(days=max(1, days))
        is_us = mcode.startswith("US.")
        logger.info(
            "[Moomoo] history_kline intraday code=%s interval=%s start=%s end=%s ext=%s",
            mcode,
            interval,
            start.isoformat(),
            end.isoformat(),
            is_us,
        )
        common_request = {
            "code": mcode,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "ktype": ktype,
            "autype": AuType.QFQ,
            "fields": [KL_FIELD.ALL],
            "max_count": 1000,
            "extended_time": is_us,
        }
        frames: list[pd.DataFrame] = []
        page_req_key = None
        seen_page_keys: set[tuple[str, str]] = set()
        page_count = 0

        for page_number in range(1, _INTRADAY_MAX_PAGES + 1):
            request = dict(common_request)
            if page_req_key is not None:
                request["page_req_key"] = page_req_key
            try:
                ret, data, next_page_key = ctx.request_history_kline(**request)
            except Exception as exc:  # noqa: BLE001
                raise DataFetchError(
                    f"Moomoo intraday page {page_number} request raised: {exc}"
                ) from exc
            if ret != RET_OK:
                raise DataFetchError(
                    f"Moomoo intraday page {page_number} failed: {data}"
                )
            if data is not None and not data.empty:
                frames.append(data.copy())
            elif next_page_key is not None:
                raise DataFetchError(
                    "Moomoo intraday returned an empty page with a continuation key"
                )

            page_count = page_number
            if next_page_key is None:
                break

            # SDK documents page keys as bytes.  A type+repr identity remains
            # safe for equivalent bytes-like keys without requiring hashability.
            key_identity = (type(next_page_key).__name__, repr(next_page_key))
            if key_identity in seen_page_keys:
                raise DataFetchError(
                    "Moomoo intraday returned a repeated continuation key"
                )
            seen_page_keys.add(key_identity)
            page_req_key = next_page_key
        else:
            raise DataFetchError(
                "Moomoo intraday exceeded the safe pagination limit "
                f"({_INTRADAY_MAX_PAGES} pages)"
            )

        if not frames:
            raise DataFetchError(
                f"Moomoo returned empty intraday for {mcode} interval={interval}"
            )
        combined = pd.concat(frames, ignore_index=True)
        if "time_key" not in combined.columns:
            raise DataFetchError("Moomoo intraday response is missing time_key")
        combined = (
            combined.drop_duplicates(subset=["time_key"], keep="last")
            .sort_values("time_key", kind="stable")
            .reset_index(drop=True)
        )
        logger.info(
            "[Moomoo] history_kline intraday complete code=%s interval=%s "
            "pages=%d rows=%d",
            mcode,
            interval,
            page_count,
            len(combined),
        )
        return self._normalize_intraday(combined, stock_code)

    def _normalize_intraday(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """For intraday we keep ISO 8601 datetime in `date` column (matches yfinance fetcher)."""
        df = df.copy()
        df = df.rename(
            columns={
                "time_key": "date",
                "turnover": "amount",
                "change_rate": "pct_chg",
            }
        )
        if "date" in df.columns:
            market = self._to_moomoo_code(stock_code).split(".", 1)[0]
            market_tz = ZoneInfo(_MARKET_TIMEZONES.get(market, "UTC"))

            def _market_iso(value: object) -> Optional[str]:
                timestamp = pd.to_datetime(value, errors="coerce")
                if pd.isna(timestamp):
                    return None
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize(market_tz)
                else:
                    timestamp = timestamp.tz_convert(market_tz)
                return timestamp.isoformat()

            df["date"] = df["date"].apply(_market_iso)
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = (df["close"].pct_change() * 100).fillna(0).round(2)
        df["code"] = stock_code
        keep = ["code"] + STANDARD_COLUMNS
        df = df[[c for c in keep if c in df.columns]]
        return df.dropna(subset=["date", "close"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Realtime quote — single ticker via market_snapshot (no subscription)
    # ------------------------------------------------------------------
    def get_realtime_quote(
        self,
        stock_code: str,
        *,
        log_final_failure: bool = True,
    ) -> Optional[UnifiedRealtimeQuote]:
        if not self.enabled or not self._sdk_ok:
            return None
        try:
            from moomoo import RET_OK

            ctx = self._get_ctx()
            mcode = self._to_moomoo_code(stock_code)
            ret, data = ctx.get_market_snapshot([mcode])
            if ret != RET_OK or data is None or data.empty:
                if log_final_failure:
                    logger.info("[Moomoo] snapshot %s empty: %s", mcode, data)
                return None
            row = data.iloc[0].to_dict()

            def _f(*keys):
                for k in keys:
                    v = row.get(k)
                    if v is not None and v == v:  # NaN-safe
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            return None
                return None

            # Moomoo snapshot does NOT return change_val/change_rate — derive
            # them from last_price + prev_close_price (verified empirically:
            # the SDK 10.4.6408 schema only has price-point fields).
            last = _f("last_price", "cur_price")
            pre = _f("prev_close_price")
            chg_amt = (last - pre) if (last is not None and pre not in (None, 0)) else None
            chg_pct = (chg_amt / pre * 100.0) if (chg_amt is not None and pre) else None

            return UnifiedRealtimeQuote(
                code=stock_code,
                name=str(row.get("name") or "").strip(),
                source=RealtimeSource.MOOMOO,
                price=last,
                change_amount=chg_amt,
                change_pct=chg_pct,
                open_price=_f("open_price"),
                high=_f("high_price"),
                low=_f("low_price"),
                pre_close=pre,
                volume=int(row.get("volume") or 0) or None,
                amount=_f("turnover"),
                amplitude=_f("amplitude"),
                volume_ratio=_f("volume_ratio"),
                turnover_rate=_f("turnover_rate"),
                pe_ratio=_f("pe_ratio"),
                pb_ratio=_f("pb_ratio"),
                total_mv=_f("total_market_val"),
                circ_mv=_f("circular_market_val"),
                high_52w=_f("highest52weeks_price"),
                low_52w=_f("lowest52weeks_price"),
            )
        except Exception as exc:  # noqa: BLE001
            if log_final_failure:
                logger.warning("[Moomoo] get_realtime_quote %s failed: %s", stock_code, exc)
            return None
