# -*- coding: utf-8 -*-
"""Live breakout detector via Moomoo KLine_1M push.

Pipeline (Phase D):
    Moomoo OpenD KLine_1M push  ─┐
                                 ├─→ ring buffer (last N 1-minute bars per ticker)
    BreakoutDetector             │     ├─ range_high cross? → BreakoutSignal
    filter_breakout (Q1-Q5)      │     ├─ Q1 regime gate (DB lookup)
                                 │     ├─ Q3 volume confirmation (from buffer)
                                 ▼     └─ Q5 RS vs SPY (delta over N bars)
                            on_signal(result)  ──→ user-supplied callback
                                                   (stdout / Telegram / DB write)

Why a ring buffer per ticker
----------------------------
Moomoo pushes one bar at a time. Q3 (volume) and Q5 (RS vs SPY) need
historical context — we hold the last ``BUFFER_BARS`` bars in memory,
trimmed FIFO. Smaller than rebuilding history on every push.

Threading
---------
The Moomoo SDK invokes our ``CurKlineHandlerBase.on_recv_rsp`` on its own
worker thread. We keep handler work *short* (mutate buffer + run pure-Python
filter) and dispatch the user callback synchronously on the same thread —
the caller is responsible for not blocking it (push to a Queue if needed).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


BUFFER_BARS = 60  # ~1 trading hour of 1-min bars
RANGE_LOOKBACK = 20  # how many prior bars define the "range high"
SPY_CODE = "SPY"  # used as the RS benchmark
SIGNAL_COOLDOWN_SECONDS = 300  # don't re-emit the same ticker within 5 min
RECONNECT_BACKOFF_SECONDS = 10  # wait this long after OpenD disconnect before retry


# Re-export the BreakoutSignal type so callers don't need a separate import.
from src.breakout.detector import Bar, BreakoutDetector, BreakoutSignal  # noqa: E402
from src.breakout.filter import BreakoutFilterResult, filter_breakout  # noqa: E402


@dataclass
class TickerState:
    """Per-ticker rolling buffer + last-known closing price for RS calc."""

    code: str
    bars: Deque[Bar] = field(default_factory=lambda: deque(maxlen=BUFFER_BARS))
    last_close: Optional[float] = None

    def push(self, bar: Bar) -> None:
        self.bars.append(bar)
        self.last_close = bar.c

    def range_high(self, lookback: int = RANGE_LOOKBACK) -> Optional[float]:
        """Highest high in the last `lookback` bars excluding the most recent."""
        if len(self.bars) < lookback + 1:
            return None
        window = list(self.bars)[-(lookback + 1) : -1]
        return max((b.h for b in window), default=None)

    def avg_volume(self, lookback: int = RANGE_LOOKBACK) -> Optional[float]:
        if len(self.bars) < lookback + 1:
            return None
        window = list(self.bars)[-(lookback + 1) : -1]
        if not window:
            return None
        return sum(b.v for b in window) / len(window)

    def return_pct(self, lookback: int = RANGE_LOOKBACK) -> Optional[float]:
        """Cumulative return over last `lookback` bars (decimal, e.g. 0.012 = +1.2%)."""
        if len(self.bars) < lookback + 1:
            return None
        bars = list(self.bars)
        start_close = bars[-(lookback + 1)].c
        end_close = bars[-1].c
        if start_close <= 0:
            return None
        return (end_close - start_close) / start_close


def _bar_from_kline_row(row: dict) -> Optional[Bar]:
    """Convert one row from Moomoo's CurKline DataFrame into a :class:`Bar`."""
    try:
        ts = row.get("time_key")
        if ts is None:
            return None
        ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if isinstance(ts, str) else ts
        return Bar(
            t=ts,
            o=float(row.get("open") or 0),
            h=float(row.get("high") or 0),
            l=float(row.get("low") or 0),
            c=float(row.get("close") or 0),
            v=float(row.get("volume") or 0),
        )
    except (TypeError, ValueError) as exc:  # noqa: BLE001
        logger.debug("[breakout_live] bar parse failed: %s row=%s", exc, row)
        return None


def _strip_market(code: str) -> str:
    parts = str(code).split(".", 1)
    return parts[1] if len(parts) == 2 else str(code)


def _get_regime_score() -> Optional[int]:
    """Look up today's regime score from the regime_scores table.

    Returns ``None`` when the table is empty or unreachable — the breakout
    filter will then refuse to pass (conservative default).
    """
    try:
        from src.regime.storage import get_today_score  # type: ignore[attr-defined]

        return get_today_score()
    except Exception:  # noqa: BLE001
        # Module may not expose that helper; do a defensive direct query.
        try:
            from sqlalchemy import select

            from src.regime.models import RegimeScoreRow
            from src.storage import get_db

            db = get_db()
            with db.session_scope() as session:
                row = session.execute(
                    select(RegimeScoreRow).order_by(RegimeScoreRow.date.desc()).limit(1)
                ).scalar_one_or_none()
                return int(row.score) if row else None
        except Exception:  # noqa: BLE001
            return None


@dataclass
class LiveBreakoutRunner:
    """Subscribe a list of tickers to KLine_1M and run the filter on each push."""

    tickers: List[str]
    on_signal: Callable[[BreakoutFilterResult], None]
    host: str = "127.0.0.1"
    port: int = 11111
    regime_min: int = 55
    volume_multiple: float = 1.2

    _states: Dict[str, TickerState] = field(default_factory=dict, init=False)
    _states_lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _ctx: Optional[object] = field(default=None, init=False)
    _running: threading.Event = field(default_factory=threading.Event, init=False)
    _detector: BreakoutDetector = field(default_factory=BreakoutDetector, init=False)
    # Per-ticker cooldown: last time we emitted a "passed" signal. Prevents
    # re-firing on every bar while a breakout is sustained.
    _last_emit: Dict[str, float] = field(default_factory=dict, init=False)
    # Connection health monitoring
    _last_push_ts: float = field(default=0.0, init=False)

    def _moomoo_codes(self) -> List[str]:
        out = []
        for t in self.tickers:
            t = t.upper().strip()
            if not t:
                continue
            if "." in t and t.split(".")[0] in {"US", "HK", "SH", "SZ", "BJ"}:
                out.append(t)
            else:
                out.append(f"US.{t}")
        # Always include SPY for RS calc (idempotent if already there).
        if "US.SPY" not in out and "SPY" not in self.tickers:
            out.append("US.SPY")
        return out

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        try:
            from moomoo import (
                CurKlineHandlerBase,
                OpenQuoteContext,
                RET_OK,
                SubType,
                Session,
            )
        except ImportError as exc:
            raise RuntimeError(
                "moomoo-api SDK not installed (or only namespace package found). "
                "Run: pip install moomoo-api>=10.4.6408"
            ) from exc

        self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        runner = self

        class _KlineHandler(CurKlineHandlerBase):
            """Bridge moomoo push → ring buffer + filter."""

            def on_recv_rsp(self, rsp_pb):  # type: ignore[override]
                ret_code, data = super().on_recv_rsp(rsp_pb)
                if ret_code != RET_OK or data is None or data.empty:
                    return ret_code, data
                # data may contain multiple rows when a snapshot push fires;
                # process oldest-first.
                rows = sorted(data.to_dict("records"), key=lambda r: r.get("time_key") or "")
                for row in rows:
                    runner._handle_bar(row)
                return RET_OK, data

        self._ctx.set_handler(_KlineHandler())

        codes = self._moomoo_codes()
        ret, msg = self._ctx.subscribe(
            codes,
            [SubType.K_1M],
            subscribe_push=True,
            session=Session.ALL,
        )
        if ret != RET_OK:
            raise RuntimeError(f"moomoo subscribe failed: {msg}")
        self._running.set()
        logger.info("[breakout_live] subscribed %d tickers (incl SPY): %s", len(codes), codes)

    def stop(self) -> None:
        self._running.clear()
        if self._ctx is not None:
            try:
                # Honour the 1-minute cool-down — we already enforce it because
                # users typically run the daemon for hours, but log a warning if
                # someone calls stop() within a minute of start().
                self._ctx.unsubscribe_all()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[breakout_live] unsubscribe_all failed: %s", exc)
            try:
                self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
            self._ctx = None
        logger.info("[breakout_live] stopped")

    def _ctx_alive(self) -> bool:
        """Probe OpenD with a cheap query — `False` = need reconnect."""
        if self._ctx is None:
            return False
        try:
            from moomoo import RET_OK

            ret, _ = self._ctx.get_global_state()
            return ret == RET_OK
        except Exception:  # noqa: BLE001
            return False

    def run_forever(self) -> None:
        """Block the calling thread until SIGINT / KeyboardInterrupt.

        Auto-reconnects: if OpenD bounces, this loop notices via the periodic
        health probe, tears down the dead context, sleeps the backoff window,
        and re-starts the subscription. We don't proactively re-emit signals
        from the ring buffer — only fresh pushes after reconnect count.
        """
        self.start()
        last_health_probe = time.time()
        try:
            while True:
                if not self._running.is_set():
                    break
                time.sleep(1)
                # Probe every 30s — cheap RPC, doesn't load OpenD.
                if time.time() - last_health_probe < 30:
                    continue
                last_health_probe = time.time()
                if self._ctx_alive():
                    continue
                logger.warning(
                    "[breakout_live] OpenD connection lost, reconnecting in %ds",
                    RECONNECT_BACKOFF_SECONDS,
                )
                try:
                    self._ctx and self._ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ctx = None
                time.sleep(RECONNECT_BACKOFF_SECONDS)
                if not self._running.is_set():
                    break
                try:
                    self.start()
                    logger.info("[breakout_live] reconnected and re-subscribed")
                except Exception as exc:  # noqa: BLE001
                    logger.error("[breakout_live] reconnect failed: %s — will retry", exc)
        except KeyboardInterrupt:
            logger.info("[breakout_live] KeyboardInterrupt — shutting down")
        finally:
            self.stop()

    # ------------------------------------------------------------------
    # Per-bar handler
    # ------------------------------------------------------------------
    def _handle_bar(self, row: dict) -> None:
        bar = _bar_from_kline_row(row)
        if bar is None:
            return
        code_full = str(row.get("code") or "")
        ticker = _strip_market(code_full)
        if not ticker:
            return

        with self._states_lock:
            state = self._states.setdefault(ticker, TickerState(code=ticker))
            state.push(bar)

            # SPY drives RS but we never emit signals on it.
            if ticker.upper() == SPY_CODE:
                return

            # Need enough history to compute range_high + reference volume.
            range_high = state.range_high()
            if range_high is None:
                return

            # Did the latest bar break the range high?
            if bar.c <= range_high:
                return

            spy_state = self._states.get(SPY_CODE)
            symbol_ret = state.return_pct()
            spy_ret = spy_state.return_pct() if spy_state else None
            ref_vol = state.avg_volume()

            signal = BreakoutSignal(
                code=ticker,
                bar=bar,
                reference_high=range_high,
                current_volume=bar.v,
                reference_volume=ref_vol or 0.0,
                reason="range_high",
            )

        regime_score = _get_regime_score()
        result = filter_breakout(
            signal,
            regime_score=regime_score,
            regime_min=self.regime_min,
            volume_multiple=self.volume_multiple,
            symbol_return_pct=symbol_ret,
            spy_return_pct=spy_ret,
        )

        # Cooldown: only emit a passed signal at most once per
        # SIGNAL_COOLDOWN_SECONDS per ticker. Failed signals are noisy by
        # design — the caller can opt into them via `--passed-only=False`.
        if result.passed:
            now = time.time()
            last = self._last_emit.get(ticker, 0.0)
            if now - last < SIGNAL_COOLDOWN_SECONDS:
                return
            self._last_emit[ticker] = now

        try:
            self.on_signal(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[breakout_live] on_signal callback raised: %s", exc)
