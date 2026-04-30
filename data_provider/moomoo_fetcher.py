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
import os
import threading
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote
from .us_index_mapping import is_us_stock_code, get_us_index_yf_symbol

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


# Map our internal interval string → moomoo `KLType` attribute name.
_INTRADAY_KTYPE: dict[str, str] = {
    "1m": "K_1M",
    "5m": "K_5M",
    "15m": "K_15M",
    "30m": "K_30M",
    "60m": "K_60M",
    "1h": "K_60M",
}


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
        """Cheap health probe: ping `get_global_state`. None / exception = dead."""
        if self._ctx is None:
            return False
        try:
            from moomoo import RET_OK

            ret, _data = self._ctx.get_global_state()
            return ret == RET_OK
        except Exception:  # noqa: BLE001
            return False

    def _get_ctx(self):
        """Lazy-create + cache the OpenQuoteContext.

        Health-checks on each call — if the cached context is dead (e.g.
        OpenD bounced), tear it down and reconnect. Thread-safe.
        """
        if not self.enabled or not self._sdk_ok:
            raise DataFetchError("MoomooFetcher 未启用或 SDK 未安装")
        with self._ctx_lock:
            if self._ctx is not None and not self._is_ctx_alive():
                logger.info("[MoomooFetcher] cached ctx dead, reconnecting")
                try:
                    self._ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ctx = None
            if self._ctx is None:
                from moomoo import OpenQuoteContext

                try:
                    self._ctx = OpenQuoteContext(host=self.host, port=self.port)
                except Exception as exc:  # noqa: BLE001
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
        try:
            ret, data, _page_key = ctx.request_history_kline(
                code=mcode,
                start=start.isoformat(),
                end=end.isoformat(),
                ktype=ktype,
                autype=AuType.QFQ,
                fields=[KL_FIELD.ALL],
                max_count=1000,
                extended_time=is_us,
            )
        except Exception as exc:  # noqa: BLE001
            raise DataFetchError(f"Moomoo intraday request raised: {exc}") from exc
        if ret != RET_OK:
            raise DataFetchError(f"Moomoo intraday failed: {data}")
        if data is None or data.empty:
            raise DataFetchError(
                f"Moomoo returned empty intraday for {mcode} interval={interval}"
            )
        return self._normalize_intraday(data, stock_code)

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
            ts = pd.to_datetime(df["date"], errors="coerce")
            df["date"] = ts.apply(lambda x: x.isoformat() if pd.notna(x) else None)
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = (df["close"].pct_change() * 100).fillna(0).round(2)
        df["code"] = stock_code
        keep = ["code"] + STANDARD_COLUMNS
        df = df[[c for c in keep if c in df.columns]]
        return df.dropna(subset=["close"]).reset_index(drop=True)

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
