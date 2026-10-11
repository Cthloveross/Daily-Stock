# -*- coding: utf-8 -*-
"""Full-watchlist runtime host for the strict two-minute P0 research lane.

The host deliberately separates three concerns:

* a background, per-symbol history warm-up builds that symbol's own baseline;
* one Moomoo ``K_1M`` subscription supplies live rows for the whole universe;
* the scheduler calls :meth:`scan_once` every minute, while this class only
  evaluates a symbol when a new exact 2-minute ``window_end`` is available.

No option-chain request occurs on this path.  The returned payload reaches the
alert ledger first; a later option supplement may be attached independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from src.opportunities.exact_2m import aggregate_exact_2m_ohlcv
from src.opportunities.intraday import compute_atr14
from src.opportunities.p0_2m_signal import (
    P0_2M_REFERENCE_SESSIONS,
    P0_2M_SIGNAL_VERSION,
    _normalise_bar,
    classify_p0_session,
    compute_p0_2m_signal,
    unavailable_p0_2m_signal,
)
from src.services.moomoo_runtime import (
    MOOMOO_RPC_BREAKER,
    MoomooRuntimeError,
    create_ready_quote_context,
    probe_opend_tcp,
    quote_context_is_ready,
)

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_MOOMOO_SOURCE = "MoomooFetcher"
_DAILY_CALENDAR_DAYS = 140
_STREAM_RECONNECT_SECONDS = 10.0
_STREAM_HEALTH_SECONDS = 30.0
_STREAM_DOWN_NOTICE_SECONDS = 60.0
_STREAM_RECOVERY_NOTICE_SECONDS = 30.0
_STREAM_ROWS_PER_SYMBOL = 180
_HISTORY_RETRY_SECONDS = 60.0
_BASELINE_LOAD_ATTEMPTS_PER_SESSION = 3

HistoryLoader = Callable[[str, str], Mapping[str, Any]]
PayloadObserver = Callable[[Mapping[str, Any]], Optional[bool]]
Clock = Callable[[], datetime]
ShadowRecorder = Callable[[Sequence[Any]], Any]
ShadowEnroller = Callable[..., Any]


def _normalise_symbols(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return tuple(result)


def _history_bar_start_et(value: Any) -> datetime:
    """Parse the normal ISO exact-bar label without pandas scalar overhead."""

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty history timestamp")
        try:
            moment = datetime.fromisoformat(
                f"{text[:-1]}+00:00" if text[-1:] in {"Z", "z"} else text
            )
        except ValueError:
            moment = pd.Timestamp(value).to_pydatetime()
    else:
        moment = pd.Timestamp(value).to_pydatetime()
    if pd.isna(moment):
        raise ValueError("invalid history timestamp")
    if moment.tzinfo is None or moment.utcoffset() is None:
        # Preserve the previous contract for injected/legacy naive values,
        # including pandas' fail-closed handling of ambiguous DST wall time.
        return pd.Timestamp(moment).tz_localize(_ET).to_pydatetime()
    return moment.astimezone(_ET)


def _intraday_history_calendar_days(
    market_date_et: str,
    *,
    as_of_date_et: Optional[date] = None,
) -> int:
    """Return the minimal calendar span containing the prior 60 XNYS days.

    Live P0 evaluates against exactly the preceding 60 exchange sessions; its
    180-session development/holdout corpus is an offline calibration input and
    must not be downloaded for every live restart.  ``StockService`` expresses
    the provider range as calendar days ending today, so resolve the earliest
    required session from XNYS and convert that boundary without a guessed
    150-day overfetch.
    """

    import exchange_calendars as xcals

    target_day = date.fromisoformat(market_date_et)
    calendar = xcals.get_calendar("XNYS")
    target_session = calendar.date_to_session(target_day, direction="none")
    earliest_session = target_session
    for _ in range(P0_2M_REFERENCE_SESSIONS):
        earliest_session = calendar.previous_session(earliest_session)
    earliest_day = earliest_session.date()
    provider_end = as_of_date_et or datetime.now(_ET).date()
    if provider_end < earliest_day:
        raise ValueError("history_provider_end_precedes_required_baseline")
    return max(1, (provider_end - earliest_day).days)


def _default_history_loader(symbol: str, market_date_et: str) -> Mapping[str, Any]:
    """Load exact 2m history plus a prior-session Wilder ATR14."""

    from src.services.stock_service import StockService

    service = StockService()
    intraday = service.get_history_data(
        symbol,
        period="2m",
        days=_intraday_history_calendar_days(market_date_et),
        include_stock_name=False,
    )
    # StockService already returns detached response dictionaries.  Keep that
    # list by reference while the warm worker compacts it; copying ~28,800
    # rows here materially increases the per-symbol peak RSS.
    bars = intraday.get("data") or []
    source = intraday.get("source")
    aggregation_contract = intraday.get("aggregation_contract")
    if (
        source != _MOOMOO_SOURCE
        or aggregation_contract != "session_anchored_exact_2m_v2"
        or not bars
    ):
        # A remote fallback may return rows, but it can never satisfy this
        # Moomoo-only live baseline.  Do not issue a second provider/fallback
        # read after the decisive intraday provenance failure.
        return {
            "bars": bars,
            "source": source,
            "aggregation_contract": aggregation_contract,
            "prior_daily_atr14": None,
            "atr14_last_bar_date": None,
            "atr14_unavailable_reason": "intraday_reference_unavailable",
        }
    daily = service.get_history_data(
        symbol,
        period="daily",
        days=_DAILY_CALENDAR_DAYS,
        include_stock_name=False,
    )
    daily_rows = [
        dict(row)
        for row in (daily.get("data") or ())
        if str(row.get("date") or "") < market_date_et
    ]
    daily_rows.sort(key=lambda row: str(row.get("date") or ""))
    atr = compute_atr14(daily_rows)
    return {
        "bars": bars,
        "source": source,
        "aggregation_contract": aggregation_contract,
        "prior_daily_atr14": atr.value,
        "atr14_last_bar_date": atr.last_bar_date,
        "atr14_unavailable_reason": atr.unavailable_reason,
    }


@dataclass(frozen=True, slots=True)
class _CompactReferenceBar:
    """Seven signal-consumed fields without one hash table per history row."""

    date: Any
    open: Any
    high: Any
    low: Any
    close: Any
    volume: Any
    amount: Any

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "_CompactReferenceBar":
        return cls(
            date=row.get("date"),
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            amount=row.get("amount"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
        }


_IndexedReferenceBar = Mapping[str, Any] | _CompactReferenceBar


@dataclass(frozen=True)
class _HistoryState:
    bars: tuple[dict[str, Any], ...]
    source: Optional[str]
    prior_daily_atr14: Optional[float]
    market_date_et: str
    state: str
    unavailable_reason: Optional[str] = None
    # Production warm-up builds these once.  ``None`` preserves the legacy
    # full-list fallback for focused tests and manually constructed fixtures.
    reference_by_start_minute: Optional[
        Mapping[int, tuple[_IndexedReferenceBar, ...]]
    ] = None
    current_session_bars: Optional[tuple[dict[str, Any], ...]] = None
    # Counts refer to exact clock slots inside the declared prior 60 sessions,
    # not the number of downloaded dates.  None is only for legacy fixtures.
    baseline_count_by_end_minute: Optional[Mapping[int, int]] = None


class IntradayP02mMonitor:
    """Own the live 1m subscription and produce versioned 2m evidence."""

    def __init__(
        self,
        symbols: Sequence[str],
        observer: PayloadObserver,
        *,
        action_enabled: bool = False,
        delivery_ready: bool = True,
        delivery_unavailable_reason: str = "mobile_delivery_unavailable",
        activation_artifact: Optional[str] = None,
        history_loader: Optional[HistoryLoader] = None,
        clock: Optional[Clock] = None,
        host: str = "127.0.0.1",
        port: int = 11111,
        system_notifier: Optional[Callable[[str], None]] = None,
        shadow_recorder: Optional[ShadowRecorder] = None,
        shadow_enroller: Optional[ShadowEnroller] = None,
    ) -> None:
        self._symbols = _normalise_symbols(symbols)
        self._observer = observer
        self._action_enabled = bool(action_enabled)
        self._delivery_ready = bool(delivery_ready)
        self._delivery_unavailable_reason = str(
            delivery_unavailable_reason or "mobile_delivery_unavailable"
        )
        self._activation_artifact = (
            Path(activation_artifact).expanduser()
            if str(activation_artifact or "").strip()
            else None
        )
        self._history_loader = history_loader or _default_history_loader
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._host = str(host)
        self._port = int(port)
        self._system_notifier = system_notifier
        self._shadow_recorder = shadow_recorder
        self._shadow_enroller = shadow_enroller
        self._shadow_enrollment_ready = False
        self._shadow_enrollment_key: Optional[str] = None
        self._shadow_enrollment_sessions: tuple[str, ...] = ()
        self._shadow_enrollment_error: Optional[str] = None
        self._shadow_last_write_at: Optional[str] = None
        self._shadow_last_write_failed_at: Optional[str] = None
        self._shadow_write_error: Optional[str] = None
        self._uses_default_history_loader = history_loader is None

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._stream_thread: Optional[threading.Thread] = None
        self._history_thread: Optional[threading.Thread] = None
        self._history_next_retry_monotonic = 0.0
        self._ctx: Optional[Any] = None
        self._subscribed = False
        self._stream_down_notified = False
        self._stream_unavailable_since_monotonic: Optional[float] = None
        self._stream_healthy_since_monotonic: Optional[float] = None
        self._histories: dict[str, _HistoryState] = {}
        self._history_generation = 0
        self._history_market_date: Optional[str] = None
        self._baseline_load_attempts: dict[str, int] = {}
        self._stream_rows: dict[str, dict[str, dict[str, Any]]] = {
            symbol: {} for symbol in self._symbols
        }
        self._last_complete_window: dict[str, str] = {}
        self._last_scan_at: Optional[str] = None
        self._started_at: Optional[datetime] = None
        self._scan_completed_at: Optional[datetime] = None
        self._last_scan_health: dict[str, Any] = {
            "ready_count": 0,
            "baseline_warming_count": len(self._symbols),
            "unavailable_count": 0,
            "stream_ready": False,
        }
        self._last_mobile_enabled = False
        self._last_mobile_reason = "not_scanned"
        self._last_mobile_eligible_count = 0

        from src.opportunities.p0_2m_calibration import compute_universe_hash

        self.universe_hash = compute_universe_hash(self._symbols)

    @staticmethod
    def _history_indexes(
        bars: Sequence[Mapping[str, Any]], market_date_et: str
    ) -> tuple[
        dict[int, tuple[_CompactReferenceBar, ...]],
        tuple[dict[str, Any], ...],
    ]:
        """Index immutable history once by ET bar-start minute.

        Signal evaluation only needs the four historical start-clock slots in
        its current 8m window.  Retaining every date at those four slots keeps
        half-day/session-scope filtering exact while avoiding a full-history
        scan and index rebuild for every symbol on every tick.
        """

        reference: dict[int, list[_CompactReferenceBar]] = {}
        current: list[dict[str, Any]] = []
        for raw in bars:
            try:
                moment = _history_bar_start_et(raw.get("date"))
            except Exception:  # noqa: BLE001 - malformed rows remain unusable
                continue
            if moment.date().isoformat() == market_date_et:
                # The current session has at most 480 exact bars and is merged
                # with live stream mappings, so retaining dictionaries here
                # keeps that path simple without meaningful memory cost.
                current.append(dict(raw))
                continue
            minute = moment.hour * 60 + moment.minute
            reference.setdefault(minute, []).append(
                _CompactReferenceBar.from_mapping(raw)
            )
        return (
            {
                minute: tuple(
                    sorted(rows, key=lambda row: str(row.date or ""))
                )
                for minute, rows in reference.items()
            },
            tuple(sorted(current, key=lambda row: str(row.get("date") or ""))),
        )

    @staticmethod
    def _reference_slice(
        history: _HistoryState, *, as_of: datetime
    ) -> list[dict[str, Any]]:
        if history.reference_by_start_minute is None:
            return [
                dict(row)
                for row in history.bars
                if not str(row.get("date") or "").startswith(
                    history.market_date_et
                )
            ]
        observed = as_of.astimezone(_ET)
        # All exact-2m strata are anchored on an even ET minute (04:00,
        # XNYS 09:30, and the real 13:00/16:00 close).
        end_minute = observed.hour * 60 + observed.minute
        end_minute -= end_minute % 2
        needed = tuple(end_minute - offset for offset in (8, 6, 4, 2))
        return [
            (
                row.to_mapping()
                if isinstance(row, _CompactReferenceBar)
                else dict(row)
            )
            for minute in needed
            for row in history.reference_by_start_minute.get(minute, ())
        ]

    @staticmethod
    def _history_baseline_coverage(
        reference: Mapping[int, tuple[_IndexedReferenceBar, ...]],
        market_date_et: str,
    ) -> tuple[dict[int, int], bool]:
        """Audit same-slot 8m history once; calendar mismatches cannot be refetched.

        A half-day inside the fixed 60-day lookback may make an afternoon slot
        structurally ineligible.  Count it honestly, but only missing/invalid
        provider bars require retry; never substitute an older session.
        """

        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS")
        target = calendar.date_to_session(date.fromisoformat(market_date_et))
        prior: dict[date, tuple[int, int]] = {}
        cursor = target
        for _ in range(P0_2M_REFERENCE_SESSIONS):
            cursor = calendar.previous_session(cursor)
            opening = calendar.session_open(cursor).tz_convert(_ET)
            closing = calendar.session_close(cursor).tz_convert(_ET)
            prior[cursor.date()] = (
                opening.hour * 60 + opening.minute,
                closing.hour * 60 + closing.minute,
            )

        valid: dict[date, set[int]] = {day: set() for day in prior}
        seen: set[tuple[date, int]] = set()
        invalid: set[tuple[date, int]] = set()
        for minute, rows in reference.items():
            for compact in rows:
                row = (
                    compact.to_mapping()
                    if isinstance(compact, _CompactReferenceBar)
                    else compact
                )
                try:
                    start = _history_bar_start_et(row.get("date"))
                except Exception:  # noqa: BLE001 - unusable history stays closed
                    continue
                if start.date() not in prior:
                    continue
                key = (start.date(), minute)
                if key in seen or _normalise_bar(row) is None:
                    invalid.add(key)
                seen.add(key)
                valid[start.date()].add(minute)
        for day, minute in invalid:
            valid[day].discard(minute)

        opening = calendar.session_open(target).tz_convert(_ET)
        closing = calendar.session_close(target).tz_convert(_ET)
        open_minute = opening.hour * 60 + opening.minute
        close_minute = closing.hour * 60 + closing.minute
        counts: dict[int, int] = {}
        missing_bars = False
        for anchor, boundary, scope in (
            (240, open_minute, "premarket"),
            (open_minute, close_minute, "regular"),
            (close_minute, 1200, "afterhours"),
        ):
            for end_minute in range(anchor + 18, boundary + 1, 2):
                if end_minute == 1200:  # the live contract closes at 20:00 ET
                    continue
                starts = set(range(end_minute - 8, end_minute, 2))
                matched = 0
                complete = 0
                for day, (prior_open, prior_close) in prior.items():
                    end = datetime.combine(day, datetime.min.time(), _ET) + timedelta(
                        minutes=end_minute
                    )
                    if classify_p0_session(
                        end - timedelta(minutes=8),
                        end,
                        regular_open_minute_et=prior_open,
                        regular_close_minute_et=prior_close,
                    ) != scope:
                        continue
                    matched += 1
                    complete += starts.issubset(valid[day])
                counts[end_minute] = complete
                missing_bars |= complete < matched
        return counts, missing_bars

    @staticmethod
    def _history_ready_at(history: _HistoryState, now: datetime) -> bool:
        if history.state not in {"ready", "partial"}:
            return False
        if history.baseline_count_by_end_minute is None:
            return history.state == "ready"
        from src.services.xnys_session_clock import resolve_xnys_session_clock

        observed = now.astimezone(_ET)
        session = resolve_xnys_session_clock(observed)
        if (
            session.open_at_et is None
            or session.close_at_et is None
            or observed.date().isoformat() != history.market_date_et
        ):
            return False
        minute = observed.hour * 60 + observed.minute
        opening = session.open_at_et.hour * 60 + session.open_at_et.minute
        closing = session.close_at_et.hour * 60 + session.close_at_et.minute
        if not 240 <= minute < 1200:
            return False
        anchor = 240 if minute < opening else opening if minute < closing else closing
        # During normal <18m segment formation, readiness means the first
        # legal slot is warm.  Formation itself is not a historical failure.
        endpoint = (
            anchor
            if anchor != 240 and minute < anchor + 2
            else max(anchor + 18, minute - minute % 2)
        )
        return history.baseline_count_by_end_minute.get(endpoint, 0) == (
            P0_2M_REFERENCE_SESSIONS
        )

    @staticmethod
    def _latest_completed_rth_endpoint(now: datetime) -> Optional[datetime]:
        """Return the exact completed XNYS RTH 2m endpoint, or ``None``."""

        from src.services.xnys_session_clock import resolve_xnys_session_clock

        session = resolve_xnys_session_clock(now)
        if (
            session.state != "session"
            or session.open_at_et is None
            or session.close_at_et is None
        ):
            return None
        observed = now.astimezone(_ET)
        if observed < session.open_at_et + timedelta(minutes=2):
            return None
        capped = min(observed, session.close_at_et)
        completed = int((capped - session.open_at_et).total_seconds() // 120)
        if completed <= 0:
            return None
        endpoint = session.open_at_et + timedelta(minutes=2 * completed)
        endpoint = min(endpoint, session.close_at_et)
        # Normal minute scheduling observes an endpoint at about +5 seconds
        # and may retry it once at about +65 seconds.  Outside that prospective
        # window it is missing evidence, not a historical endpoint to backfill.
        age_seconds = (observed - endpoint).total_seconds()
        if not 0 <= age_seconds <= 75:
            return None
        return endpoint

    @staticmethod
    def _component_finality(
        rows: Sequence[Mapping[str, Any]],
        *,
        expected_end: datetime,
    ) -> tuple[bool, bool, Optional[datetime], dict[str, Any]]:
        """Independently audit completed exact-2m component timestamps."""

        from src.services.xnys_session_clock import resolve_xnys_session_clock

        expected = expected_end.astimezone(_ET)
        session = resolve_xnys_session_clock(expected)
        if (
            session.state != "session"
            or session.open_at_et is None
            or session.close_at_et is None
            or not (session.open_at_et < expected <= session.close_at_et)
        ):
            return False, False, None, {
                "status": "invalid_session",
                "expected_window_end": expected.isoformat(),
            }

        seen: set[datetime] = set()
        valid_starts: set[datetime] = set()
        duplicate_starts: set[str] = set()
        invalid_starts: set[str] = set()
        parsed_starts: list[datetime] = []
        for raw in rows:
            try:
                stamp = pd.Timestamp(raw.get("date"))
                if pd.isna(stamp):
                    raise ValueError("missing timestamp")
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize(_ET)
                else:
                    stamp = stamp.tz_convert(_ET)
                start = stamp.to_pydatetime()
            except Exception:  # noqa: BLE001 - audit must fail closed
                continue
            if not (session.open_at_et <= start < expected):
                continue
            parsed_starts.append(start)
            if start in seen:
                duplicate_starts.add(start.isoformat())
                continue
            seen.add(start)
            try:
                opened = float(raw.get("open"))
                high = float(raw.get("high"))
                low = float(raw.get("low"))
                closed = float(raw.get("close"))
                volume = float(raw.get("volume"))
                valid = bool(
                    start.second == 0
                    and start.microsecond == 0
                    and all(
                        math.isfinite(value)
                        for value in (opened, high, low, closed, volume)
                    )
                    and min(opened, high, low, closed) > 0
                    and high >= max(opened, closed)
                    and low <= min(opened, closed)
                    and volume >= 0
                )
            except (TypeError, ValueError):
                valid = False
            if valid:
                valid_starts.add(start)
            else:
                invalid_starts.add(start.isoformat())

        max_end = (
            max(parsed_starts) + timedelta(minutes=2)
            if parsed_starts
            else None
        )
        expected_starts: list[datetime] = []
        cursor = session.open_at_et
        while cursor < expected:
            expected_starts.append(cursor)
            cursor += timedelta(minutes=2)
        expected_set = set(expected_starts)
        missing_starts = sorted(
            value.isoformat() for value in expected_set - valid_starts
        )
        unexpected_starts = sorted(
            value.isoformat() for value in valid_starts - expected_set
        )
        endpoint_start = expected - timedelta(minutes=2)
        endpoint_complete = bool(
            endpoint_start in valid_starts
            and endpoint_start.isoformat() not in duplicate_starts
            and max_end == expected
        )
        complete_from_open = bool(
            endpoint_complete
            and not missing_starts
            and not unexpected_starts
            and not duplicate_starts
            and len(valid_starts) == len(expected_starts)
        )
        return endpoint_complete, complete_from_open, max_end, {
            "status": "complete" if endpoint_complete else "incomplete",
            "expected_window_end": expected.isoformat(),
            "max_component_end": max_end.isoformat() if max_end else None,
            "expected_component_count_from_open": len(expected_starts),
            "observed_component_count_from_open": len(valid_starts),
            "complete_from_open": complete_from_open,
            "missing_component_starts": missing_starts,
            "duplicate_component_starts": sorted(duplicate_starts),
            "invalid_component_starts": sorted(invalid_starts),
            "unexpected_component_starts": unexpected_starts,
        }

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def running(self) -> bool:
        return bool(self._stream_thread and self._stream_thread.is_alive())

    def start(self) -> None:
        if self.running or not self._symbols:
            return
        self._stop_event.clear()
        now = self._now()
        self._started_at = now
        self._ensure_shadow_enrollment(now)
        self._ensure_history_worker(self._history_target_market_date(now))
        self._stream_thread = threading.Thread(
            target=self._stream_supervisor,
            name="dsa-p0-2m-moomoo-stream",
            daemon=True,
        )
        self._stream_thread.start()

    def stop(self, *, join_timeout_seconds: float = 10.0) -> None:
        self._stop_event.set()
        self._close_stream()
        for thread in (self._stream_thread, self._history_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=max(0.0, float(join_timeout_seconds)))
        self._stream_thread = None
        self._history_thread = None

    def _now(self) -> datetime:
        moment = self._clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise ValueError("P0 2m monitor clock must be timezone-aware")
        return moment.astimezone(timezone.utc)

    @staticmethod
    def _history_target_market_date(now: datetime) -> str:
        """Choose the session whose baseline should be warm after a restart.

        During the legal 04:00--20:00 extended session, current-day bars are
        still needed.  After 20:00, on holidays, and on weekends, prewarming
        the next XNYS session avoids throwing away a full 69-symbol download
        when the calendar date changes before the next actionable scan.
        Calendar uncertainty falls back to the observed ET date; downstream
        signal computation still resolves XNYS independently and fails closed.
        """

        observed = now.astimezone(_ET)
        day = observed.date()
        try:
            import exchange_calendars as xcals

            calendar = xcals.get_calendar("XNYS")
            if bool(calendar.is_session(day)):
                minute = observed.hour * 60 + observed.minute
                if minute < 20 * 60:
                    return day.isoformat()
                target = calendar.next_session(day)
            else:
                target = calendar.date_to_session(day, direction="next")
            return target.date().isoformat()
        except Exception:  # noqa: BLE001 - warm-up is advisory, signal fails closed
            return day.isoformat()

    def _notify_system(self, text: str) -> None:
        if self._system_notifier is None:
            return
        try:
            self._system_notifier(text)
        except Exception:  # noqa: BLE001 - health notice must not stop stream
            logger.warning("[p0-2m] system notifier failed", exc_info=True)

    def _ensure_shadow_enrollment(self, now: datetime) -> None:
        if self._shadow_enroller is None:
            return
        try:
            enrollment = self._shadow_enroller(
                symbols=self._symbols,
                universe_hash=self.universe_hash,
                signal_version=P0_2M_SIGNAL_VERSION,
                enrollment_started_at=now,
            )

            def field(name: str) -> Any:
                return (
                    enrollment.get(name)
                    if isinstance(enrollment, Mapping)
                    else getattr(enrollment, name, None)
                )

            key = str(field("enrollment_key") or "").strip()
            sessions = tuple(str(day) for day in (field("sessions") or ()))
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                raise ValueError("enrollment_key_invalid")
            if (
                len(sessions) != 20
                or tuple(sorted(set(sessions))) != sessions
                or any(date.fromisoformat(day).isoformat() != day for day in sessions)
            ):
                raise ValueError("enrollment_sessions_invalid")
        except Exception as exc:  # noqa: BLE001 - telemetry is non-blocking
            with self._lock:
                self._shadow_enrollment_ready = False
                self._shadow_enrollment_key = None
                self._shadow_enrollment_sessions = ()
                self._shadow_enrollment_error = type(exc).__name__
            logger.warning(
                "[p0-2m] live-forward enrollment unavailable error_type=%s; "
                "signal path continues",
                type(exc).__name__,
            )
            return
        with self._lock:
            if key != self._shadow_enrollment_key:
                self._shadow_last_write_at = None
                self._shadow_last_write_failed_at = None
                self._shadow_write_error = None
            self._shadow_enrollment_ready = True
            self._shadow_enrollment_key = key
            self._shadow_enrollment_sessions = sessions
            self._shadow_enrollment_error = None

    def _shadow_run_state(self, now: datetime) -> str:
        """Describe the frozen run without moving its dates or writing data."""

        if not self._shadow_enrollment_ready or not self._shadow_enrollment_sessions:
            return "unknown"
        from src.services.xnys_session_clock import resolve_xnys_session_clock

        first_day = self._shadow_enrollment_sessions[0]
        last_day = self._shadow_enrollment_sessions[-1]
        try:
            first = resolve_xnys_session_clock(datetime.fromisoformat(first_day).replace(tzinfo=_ET))
            last = resolve_xnys_session_clock(datetime.fromisoformat(last_day).replace(tzinfo=_ET))
            if first.open_at_et is None or last.close_at_et is None:
                return "unknown"
            if now < first.open_at_et:
                return "waiting"
            # Keep the final completed 2m endpoint's existing live-write SLA.
            if now > last.close_at_et + timedelta(seconds=75):
                return "completed"
            return "collecting"
        except Exception:  # noqa: BLE001 - calendar uncertainty is visible
            return "unknown"

    def get_completed_1m_bars(
        self, symbol: str, *, as_of: datetime
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        """Return detached, completed Moomoo end-labelled rows; never fetch."""

        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        observed = as_of.astimezone(_ET)
        with self._lock:
            if not self._subscribed:
                return [], None
            rows = [dict(row) for row in self._stream_rows.get(symbol.upper(), {}).values()]
        completed = []
        for row in rows:
            try:
                end = _history_bar_start_et(row.get("date"))
            except (TypeError, ValueError):
                continue
            if end.date() == observed.date() and end <= observed:
                completed.append(row)
        completed.sort(key=lambda row: str(row.get("date") or ""))
        return completed, _MOOMOO_SOURCE

    # -- baseline warm-up -------------------------------------------------
    def _history_provider_unavailable_reason(
        self,
        *,
        probe_tcp: bool,
    ) -> Optional[str]:
        """Return a batch-level provider failure for the production loader.

        Injected loaders used by replay/tests are independent of OpenD and
        must not inherit its process-wide health.  The production loader,
        however, requires Moomoo provenance, so continuing through the full
        universe after the shared breaker trips cannot produce a usable
        baseline and only amplifies load.
        """

        if not self._uses_default_history_loader:
            return None
        # ``is_tripped`` intentionally remains true after cooldown until a
        # successful probe reports recovery.  Using it here would prevent this
        # very worker from ever issuing that half-open probe.  ``is_open`` is
        # the current fast-fail view: it becomes false after cooldown so the
        # next history page can pass through ``check()`` as the one probe.
        if MOOMOO_RPC_BREAKER.is_open():
            return "moomoo_circuit_open"
        if probe_tcp and not probe_opend_tcp(self._host, self._port):
            return "opend_tcp_unreachable"
        return None

    @staticmethod
    def _unavailable_history_state(
        market_date_et: str,
        reason: str,
    ) -> _HistoryState:
        return _HistoryState(
            bars=(),
            source=None,
            prior_daily_atr14=None,
            market_date_et=market_date_et,
            state="unavailable",
            unavailable_reason=reason,
            reference_by_start_minute={},
            current_session_bars=(),
        )

    def _stop_history_batch(
        self,
        symbols: Sequence[str],
        *,
        market_date_et: str,
        generation: int,
        reason: str,
    ) -> None:
        """Fail the remaining batch closed while retaining ready histories."""

        with self._lock:
            if generation != self._history_generation:
                return
            stopped_count = 0
            for symbol in symbols:
                existing = self._histories.get(symbol)
                if existing is not None and existing.state in {"ready", "partial"}:
                    continue
                self._histories[symbol] = self._unavailable_history_state(
                    market_date_et,
                    f"history_batch_stopped:{reason}",
                )
                stopped_count += 1
        if stopped_count:
            logger.warning(
                "[p0-2m] history warm batch stopped reason=%s "
                "remaining_symbols=%d; ready histories retained for retry",
                reason,
                stopped_count,
            )

    def _ensure_history_worker(self, market_date_et: str) -> None:
        with self._lock:
            same_day = self._history_market_date == market_date_et
            if same_day and self._history_thread is not None:
                if self._history_thread.is_alive():
                    return
                self._history_thread = None
            if same_day and len(self._histories) == len(self._symbols) and all(
                item.state == "ready"
                or (
                    item.state == "partial"
                    and self._baseline_load_attempts.get(symbol, 0)
                    >= _BASELINE_LOAD_ATTEMPTS_PER_SESSION
                )
                for symbol, item in self._histories.items()
            ):
                return
            now_monotonic = time.monotonic()
            if same_day and now_monotonic < self._history_next_retry_monotonic:
                return
            if not same_day:
                self._history_generation += 1
                self._history_market_date = market_date_et
                self._histories = {}
                self._baseline_load_attempts = {}
                self._last_complete_window = {}
            generation = self._history_generation
            self._history_next_retry_monotonic = (
                now_monotonic + _HISTORY_RETRY_SECONDS
            )
            worker = threading.Thread(
                target=self._warm_histories,
                args=(market_date_et, generation),
                name="dsa-p0-2m-history-warm",
                daemon=True,
            )
            self._history_thread = worker
        worker.start()

    def _warm_histories(self, market_date_et: str, generation: int) -> None:
        try:
            provider_reason = self._history_provider_unavailable_reason(
                probe_tcp=True
            )
            if provider_reason is not None:
                self._stop_history_batch(
                    self._symbols,
                    market_date_et=market_date_et,
                    generation=generation,
                    reason=provider_reason,
                )
                return

            for position, symbol in enumerate(self._symbols):
                if self._stop_event.is_set():
                    return
                provider_reason = self._history_provider_unavailable_reason(
                    probe_tcp=False
                )
                if provider_reason is not None:
                    self._stop_history_batch(
                        self._symbols[position:],
                        market_date_et=market_date_et,
                        generation=generation,
                        reason=provider_reason,
                    )
                    return
                with self._lock:
                    if generation != self._history_generation:
                        return
                    existing = self._histories.get(symbol)
                    baseline_attempts = self._baseline_load_attempts.get(symbol, 0)
                if existing is not None and (
                    existing.state == "ready"
                    or (
                        existing.state == "partial"
                        and self._baseline_load_attempts.get(symbol, 0)
                        >= _BASELINE_LOAD_ATTEMPTS_PER_SESSION
                    )
                ):
                    continue
                if existing is not None and existing.state == "partial":
                    baseline_attempts += 1
                load_failed = False
                try:
                    loaded = dict(self._history_loader(symbol, market_date_et))
                    source = str(loaded.get("source") or "").strip() or None
                    contract = str(
                        loaded.get("aggregation_contract") or ""
                    ).strip()
                    loaded_bars = loaded.get("bars") or ()
                    # The production loader returns a list and injected test
                    # loaders commonly return a tuple.  Reuse either sequence
                    # during one-pass compaction instead of duplicating every
                    # row before building the immutable index.
                    bars = (
                        loaded_bars
                        if isinstance(loaded_bars, (list, tuple))
                        else tuple(loaded_bars)
                    )
                    atr_raw = loaded.get("prior_daily_atr14")
                    atr = float(atr_raw) if atr_raw is not None else None
                    if source != _MOOMOO_SOURCE:
                        state = "unavailable"
                        reason = "live_reference_source_mismatch"
                    elif contract != "session_anchored_exact_2m_v2":
                        state = "unavailable"
                        reason = "exact_2m_aggregation_contract_missing"
                    elif not bars:
                        state = "unavailable"
                        reason = "exact_2m_history_empty"
                    elif atr is None or not math.isfinite(atr) or atr <= 0:
                        state = "unavailable"
                        reason = str(
                            loaded.get("atr14_unavailable_reason")
                            or "prior_daily_atr14_unavailable"
                        )
                    else:
                        state = "ready"
                        reason = None
                    reference_index, current_session_bars = self._history_indexes(
                        bars, market_date_et
                    )
                    baseline_counts = None
                    if state == "ready":
                        baseline_counts, missing_bars = self._history_baseline_coverage(
                            reference_index, market_date_et
                        )
                        if missing_bars or not any(
                            count == P0_2M_REFERENCE_SESSIONS
                            for count in baseline_counts.values()
                        ):
                            state = "partial"
                            reason = "historical_same_slot_baseline_incomplete"
                            if existing is None or existing.state != "partial":
                                baseline_attempts += 1
                    history = _HistoryState(
                        # The immutable clock index and current-session slice
                        # are the production cache.  Retaining ``bars`` as
                        # well would duplicate tens of thousands of row dicts
                        # per symbol; it is only the compatibility fallback
                        # when callers construct a state without indexes.
                        bars=(),
                        source=source,
                        prior_daily_atr14=atr,
                        market_date_et=market_date_et,
                        state=state,
                        unavailable_reason=reason,
                        reference_by_start_minute=reference_index,
                        current_session_bars=current_session_bars,
                        baseline_count_by_end_minute=baseline_counts,
                    )
                except Exception as exc:  # noqa: BLE001 - one symbol fails closed
                    load_failed = True
                    logger.warning(
                        "[p0-2m] history warm failed symbol=%s error_type=%s",
                        symbol,
                        type(exc).__name__,
                    )
                    history = (
                        existing
                        if existing is not None and existing.state == "partial"
                        else self._unavailable_history_state(
                            market_date_et,
                            f"history_load_failed:{type(exc).__name__}",
                        )
                    )
                with self._lock:
                    if generation != self._history_generation:
                        return
                    self._histories[symbol] = history
                    if history.state == "partial":
                        self._baseline_load_attempts[symbol] = baseline_attempts
                if load_failed:
                    provider_reason = self._history_provider_unavailable_reason(
                        probe_tcp=True
                    )
                    if provider_reason is not None:
                        self._stop_history_batch(
                            self._symbols[position + 1 :],
                            market_date_et=market_date_et,
                            generation=generation,
                            reason=provider_reason,
                        )
                        return
        finally:
            with self._lock:
                if (
                    generation == self._history_generation
                    and self._history_thread is threading.current_thread()
                ):
                    self._history_thread = None
                    self._history_next_retry_monotonic = max(
                        self._history_next_retry_monotonic,
                        time.monotonic() + _HISTORY_RETRY_SECONDS,
                    )

    # -- live subscription ------------------------------------------------
    def _moomoo_codes(self) -> list[str]:
        return [
            symbol if symbol.startswith("US.") else f"US.{symbol}"
            for symbol in self._symbols
        ]

    def _connect_stream(self) -> None:
        from moomoo import CurKlineHandlerBase, RET_OK, Session, SubType

        monitor = self

        class _KlineHandler(CurKlineHandlerBase):
            def on_recv_rsp(self, rsp_pb):  # type: ignore[override]
                ret_code, data = super().on_recv_rsp(rsp_pb)
                if ret_code == RET_OK and data is not None and not data.empty:
                    monitor._handle_stream_rows(data.to_dict("records"))
                return ret_code, data

        try:
            ctx = create_ready_quote_context(host=self._host, port=self._port)
        except MoomooRuntimeError as exc:
            raise RuntimeError(str(exc)) from exc
        try:
            ctx.set_handler(_KlineHandler())
            ret, message = ctx.subscribe(
                self._moomoo_codes(),
                [SubType.K_1M],
                subscribe_push=True,
                session=Session.ALL,
            )
            if ret != RET_OK:
                raise RuntimeError(f"moomoo subscribe failed: {message}")
        except Exception:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        with self._lock:
            self._ctx = ctx
            self._subscribed = True

    def _close_stream(self) -> None:
        with self._lock:
            ctx = self._ctx
            self._ctx = None
            self._subscribed = False
        if ctx is None:
            return
        try:
            ctx.unsubscribe_all()
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass

    def _stream_healthy(self) -> bool:
        with self._lock:
            ctx = self._ctx
            subscribed = self._subscribed
        return (
            subscribed
            and probe_opend_tcp(self._host, self._port)
            and quote_context_is_ready(ctx)
        )

    def _update_stream_notice(self, healthy: bool) -> None:
        """Debounce phone notices only; subscription safety changes immediately."""

        now = time.monotonic()
        if healthy:
            self._stream_unavailable_since_monotonic = None
            if not self._stream_down_notified:
                self._stream_healthy_since_monotonic = None
                return
            if self._stream_healthy_since_monotonic is None:
                self._stream_healthy_since_monotonic = now
            if now - self._stream_healthy_since_monotonic >= _STREAM_RECOVERY_NOTICE_SECONDS:
                self._notify_system(
                    "P0 2m 行情订阅已连续确认可用至少30秒；"
                    "后续仅评估新到达的完整分钟，不补发中断期间的旧信号。"
                )
                self._stream_down_notified = False
                self._stream_healthy_since_monotonic = None
            return
        self._stream_healthy_since_monotonic = None
        if self._stream_unavailable_since_monotonic is None:
            self._stream_unavailable_since_monotonic = now
        if (
            not self._stream_down_notified
            and now - self._stream_unavailable_since_monotonic >= _STREAM_DOWN_NOTICE_SECONDS
        ):
            self._notify_system(
                "P0 2m 行情订阅已连续不可用或无法确认至少60秒；"
                "新 P0/P0-X 已 fail closed，不会使用旧 K 线补发。"
            )
            self._stream_down_notified = True

    def _stream_supervisor(self) -> None:
        while not self._stop_event.is_set():
            try:
                healthy = self._stream_healthy()
            except Exception as exc:  # noqa: BLE001 - uncertain is not healthy
                healthy = False
                logger.info(
                    "[p0-2m] stream health unconfirmed error_type=%s",
                    type(exc).__name__,
                )
            if not healthy:
                self._close_stream()
            self._update_stream_notice(healthy)
            if not healthy:
                try:
                    self._connect_stream()
                except Exception as exc:  # noqa: BLE001 - bounded reconnect
                    logger.info(
                        "[p0-2m] stream connect failed error_type=%s",
                        type(exc).__name__,
                    )
                    self._stop_event.wait(_STREAM_RECONNECT_SECONDS)
                    continue
            self._stop_event.wait(_STREAM_HEALTH_SECONDS)

    @staticmethod
    def _ticker_from_code(value: Any) -> str:
        code = str(value or "").strip().upper()
        return code.split(".", 1)[-1]

    @staticmethod
    def _stream_timestamp(value: Any) -> Optional[str]:
        try:
            timestamp = pd.Timestamp(value)
        except Exception:  # noqa: BLE001
            return None
        if pd.isna(timestamp):
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(_ET)
        else:
            timestamp = timestamp.tz_convert(_ET)
        if timestamp.second or timestamp.microsecond:
            return None
        return timestamp.isoformat()

    def _handle_stream_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        with self._lock:
            for raw in rows:
                symbol = self._ticker_from_code(raw.get("code"))
                if symbol not in self._stream_rows:
                    continue
                label = self._stream_timestamp(
                    raw.get("time_key") if "time_key" in raw else raw.get("date")
                )
                if label is None:
                    continue
                row = {
                    "date": label,
                    "open": raw.get("open"),
                    "high": raw.get("high"),
                    "low": raw.get("low"),
                    "close": raw.get("close"),
                    "volume": raw.get("volume"),
                    "amount": raw.get("turnover", raw.get("amount")),
                }
                bucket = self._stream_rows[symbol]
                # Revisions of the same still-forming provider minute replace
                # the earlier observation; they are not two component bars.
                bucket[label] = row
                if len(bucket) > _STREAM_ROWS_PER_SYMBOL:
                    for old_label in sorted(bucket)[: -_STREAM_ROWS_PER_SYMBOL]:
                        bucket.pop(old_label, None)

    # -- scan -------------------------------------------------------------
    def _activation_context(self) -> tuple[bool, str, frozenset[str]]:
        if not self._action_enabled:
            return False, "action_switch_disabled", frozenset()
        if not self._delivery_ready:
            return False, self._delivery_unavailable_reason, frozenset()
        if self._activation_artifact is None:
            return False, "activation_artifact_not_configured", frozenset()
        try:
            from src.opportunities.p0_2m_calibration import (
                validated_mobile_eligible_symbols,
            )

            result = validated_mobile_eligible_symbols(
                self._activation_artifact,
                expected_universe_hash=self.universe_hash,
                expected_signal_version=P0_2M_SIGNAL_VERSION,
            )
        except Exception as exc:  # noqa: BLE001 - action gate fails closed
            return (
                False,
                f"activation_artifact_invalid:{type(exc).__name__}",
                frozenset(),
            )
        if not isinstance(result, tuple) or len(result) != 3:
            return False, "activation_artifact_rejected", frozenset()
        valid, reason, eligible = result
        if not valid:
            return False, str(reason), frozenset()
        eligible_symbols = frozenset(
            str(symbol or "").strip().upper() for symbol in eligible
        )
        if not eligible_symbols.issubset(set(self._symbols)):
            return False, "mobile_eligible_symbols_outside_runtime", frozenset()
        return True, str(reason), eligible_symbols

    def _activation_status(self) -> tuple[bool, str]:
        """Compatibility view used by diagnostics and existing callers."""

        enabled, reason, _eligible = self._activation_context()
        return enabled, reason

    def health_snapshot(self) -> dict[str, Any]:
        """Return cached, read-only readiness; never fetch bars or reconnect."""

        from src.services.xnys_session_clock import resolve_xnys_session_clock

        now = self._now()
        scan_window_active: Optional[bool] = None
        scan_window_age_seconds: Optional[float] = None
        try:
            session = resolve_xnys_session_clock(now)
            if session.state == "holiday":
                scan_window_active = False
            elif session.state == "session":
                observed = now.astimezone(_ET)
                scan_window_active = 240 <= observed.hour * 60 + observed.minute < 1200
                if scan_window_active:
                    opened = observed.replace(hour=4, minute=0, second=0, microsecond=0)
                    scan_window_age_seconds = (observed - opened).total_seconds()
        except Exception:  # noqa: BLE001 - unknown calendar is not a holiday
            pass
        with self._lock:
            history_worker_running = bool(
                self._history_thread and self._history_thread.is_alive()
            )
            warming_count = sum(
                1 for symbol in self._symbols if symbol not in self._histories
            )
            history_ready_count = sum(
                1
                for item in self._histories.values()
                if scan_window_active is True and self._history_ready_at(item, now)
            )
            history_loaded_count = sum(
                1
                for item in self._histories.values()
                if item.state in {"ready", "partial"}
            )
            history_unavailable_count = len(self._histories) - history_ready_count
            # A ready baseline still cannot be scanned without the live 1m
            # subscription.  Derive these counts from current cached state so
            # health shows warm-up progress before the first legal scan rather
            # than leaving the startup 0/N snapshot frozen until 04:00 ET.
            ready_count = history_ready_count if self._subscribed else 0
            unavailable_count = history_unavailable_count + (
                0 if self._subscribed else history_ready_count
            )
            shadow_run_state = self._shadow_run_state(now)
            snapshot = {
                "schema_version": "intraday_p0_2m_health_v1",
                "running": self.running,
                "stream_ready": self._subscribed,
                "symbol_count": len(self._symbols),
                "universe_hash": self.universe_hash,
                "history_market_date_et": self._history_market_date,
                "scan_window_active": scan_window_active,
                "scan_window_age_seconds": scan_window_age_seconds,
                "history_worker_running": history_worker_running,
                "history_loaded_count": history_loaded_count,
                "baseline_retry_exhausted_count": sum(
                    item.state == "partial"
                    and self._baseline_load_attempts.get(symbol, 0)
                    >= _BASELINE_LOAD_ATTEMPTS_PER_SESSION
                    for symbol, item in self._histories.items()
                ),
                "ready_count": ready_count,
                "baseline_warming_count": warming_count,
                "unavailable_count": unavailable_count,
                "last_scan_at": self._last_scan_at,
                "scan_completed_at": (
                    self._scan_completed_at.isoformat() if self._scan_completed_at else None
                ),
                "scan_age_seconds": (
                    max(0.0, (now - self._scan_completed_at).total_seconds())
                    if self._scan_completed_at else None
                ),
                "startup_age_seconds": (
                    max(0.0, (now - self._started_at).total_seconds())
                    if self._started_at else None
                ),
                "last_complete_window_count": len(self._last_complete_window),
                "action_switch_enabled": self._action_enabled,
                "activation_artifact_configured": (
                    self._activation_artifact is not None
                ),
                "mobile_enabled": self._last_mobile_enabled,
                "mobile_reason": self._last_mobile_reason,
                "mobile_eligible_symbol_count": (
                    self._last_mobile_eligible_count
                ),
                "shadow_enrollment_ready": (
                    self._shadow_enrollment_ready
                    and shadow_run_state in {"waiting", "collecting"}
                ),
                "shadow_enrollment_key": self._shadow_enrollment_key,
                "shadow_run_state": shadow_run_state,
                "shadow_start_session": (
                    self._shadow_enrollment_sessions[0] if self._shadow_enrollment_sessions else None
                ),
                "shadow_end_session": (
                    self._shadow_enrollment_sessions[-1] if self._shadow_enrollment_sessions else None
                ),
                "shadow_enrollment_error": self._shadow_enrollment_error,
                "shadow_last_write_at": self._shadow_last_write_at,
                "shadow_last_write_failed_at": self._shadow_last_write_failed_at,
                "shadow_write_error": self._shadow_write_error,
            }
        return snapshot

    def _merged_current_bars(
        self,
        symbol: str,
        history: _HistoryState,
        *,
        as_of: datetime,
        market_date_et: str,
    ) -> list[dict[str, Any]]:
        current_source = (
            history.current_session_bars
            if history.current_session_bars is not None
            else tuple(
                row
                for row in history.bars
                if str(row.get("date") or "").startswith(market_date_et)
            )
        )
        historical_today = {
            str(row.get("date")): dict(row)
            for row in current_source
        }
        with self._lock:
            raw_rows = list(self._stream_rows.get(symbol, {}).values())
        if raw_rows:
            streamed = aggregate_exact_2m_ohlcv(
                pd.DataFrame(raw_rows),
                source=_MOOMOO_SOURCE,
                as_of=as_of,
            )
            for row in streamed.to_dict("records"):
                historical_today[str(row.get("date"))] = dict(row)
        lower_bound = as_of.astimezone(_ET) - timedelta(minutes=20)
        sliced: list[dict[str, Any]] = []
        for key in sorted(historical_today):
            try:
                stamp = pd.Timestamp(key)
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize(_ET)
                else:
                    stamp = stamp.tz_convert(_ET)
                start = stamp.to_pydatetime()
            except Exception:  # noqa: BLE001 - signal code will fail closed
                continue
            if lower_bound <= start < as_of.astimezone(_ET):
                sliced.append(historical_today[key])
        return sliced

    def scan_once(self) -> dict[str, Any]:
        """Evaluate every warmed symbol and pass one payload to the observer."""

        now = self._now()
        self._ensure_shadow_enrollment(now)
        market_date_et = now.astimezone(_ET).date().isoformat()
        # Keep the same calendar target used at startup.  A manual/initial
        # scan after 20:00 ET or on a closed date must not discard the next
        # session's 69-symbol warm-up and re-download an unactionable day.
        self._ensure_history_worker(self._history_target_market_date(now))
        mobile_enabled, mobile_reason, mobile_eligible_symbols = (
            self._activation_context()
        )
        with self._lock:
            histories = dict(self._histories)
            stream_ready = self._subscribed

        expected_rth_end = self._latest_completed_rth_endpoint(now)

        candidates: list[dict[str, Any]] = []
        telemetry_facts: dict[
            str, tuple[Mapping[str, Any], list[dict[str, Any]]]
        ] = {}
        pending_complete_windows: dict[str, str] = {}
        ready_count = 0
        warming_count = 0
        unavailable_count = 0
        for symbol in self._symbols:
            current: list[dict[str, Any]] = []
            history = histories.get(symbol)
            if history is None:
                warming_count += 1
                evidence = unavailable_p0_2m_signal(
                    "baseline_warming",
                    state="baseline_warming",
                    symbol=symbol,
                    universe_hash=self.universe_hash,
                    source=None,
                )
            elif history.state not in {"ready", "partial"}:
                unavailable_count += 1
                evidence = unavailable_p0_2m_signal(
                    history.unavailable_reason or "baseline_unavailable",
                    state="shadow",
                    symbol=symbol,
                    universe_hash=self.universe_hash,
                    source=history.source,
                )
            elif not stream_ready:
                unavailable_count += 1
                evidence = unavailable_p0_2m_signal(
                    "live_1m_subscription_unavailable",
                    state="shadow",
                    symbol=symbol,
                    universe_hash=self.universe_hash,
                    source=history.source,
                )
            else:
                if self._history_ready_at(history, now):
                    ready_count += 1
                else:
                    unavailable_count += 1
                current = self._merged_current_bars(
                    symbol,
                    history,
                    as_of=now,
                    market_date_et=market_date_et,
                )
                reference = self._reference_slice(history, as_of=now)
                evidence = compute_p0_2m_signal(
                    current,
                    reference_bars=reference,
                    market_date_et=market_date_et,
                    as_of=now,
                    prior_daily_atr14=history.prior_daily_atr14,
                    symbol=symbol,
                    source=history.source or _MOOMOO_SOURCE,
                    universe_hash=self.universe_hash,
                )

            telemetry_facts[symbol] = (evidence, current)

            window_end = str(evidence.get("window_end") or "")
            data_complete = (
                isinstance(evidence.get("data_quality"), Mapping)
                and evidence["data_quality"].get("status") == "complete"
            )
            with self._lock:
                already_evaluated = bool(
                    window_end
                    and self._last_complete_window.get(symbol) == window_end
                )
            # Complete evidence is emitted only at its first crossing.  State
            # transitions while warming remain visible but are deduped later by
            # the ledger, so a reconnect can be diagnosed without fake signals.
            if already_evaluated:
                continue
            if window_end and data_complete:
                pending_complete_windows[symbol] = window_end
            candidates.append(
                {
                    "ticker": symbol,
                    "p0_mobile_eligible": bool(
                        mobile_enabled and symbol in mobile_eligible_symbols
                        and evidence.get("session_scope") == "regular"
                    ),
                    "p0_signal_evidence_v2": evidence,
                }
            )

        payload = {
            "schema_version": "intraday_p0_2m_scan_v1",
            "as_of": now.isoformat(),
            "alert_observed_at": now.isoformat(),
            "market_date_et": market_date_et,
            "session_state": (
                str(candidates[0]["p0_signal_evidence_v2"].get("session_scope"))
                if candidates
                and candidates[0]["p0_signal_evidence_v2"].get("session_scope")
                else "closed"
            ),
            "quote_session_scope": "current_session",
            "candidates": candidates,
            "universe_scan": {
                "mode": "p0_2m_full_watchlist",
                "watchlist_total": len(self._symbols),
                "watchlist_scanned": len(self._symbols),
                "watchlist_truncated": False,
                "universe_hash": self.universe_hash,
            },
            "p0_mobile_delivery": {
                "enabled": mobile_enabled,
                "reason": mobile_reason,
                "action_switch_enabled": self._action_enabled,
                "eligible_symbol_count": len(mobile_eligible_symbols),
                "activation_artifact": (
                    str(self._activation_artifact)
                    if self._activation_artifact is not None
                    else None
                ),
            },
            "p0_scan_health": {
                "ready_count": ready_count,
                "baseline_warming_count": warming_count,
                "unavailable_count": unavailable_count,
                "stream_ready": stream_ready,
            },
        }
        observer_accepted = True
        observer_receipt: Optional[Mapping[str, Any]] = None
        if candidates:
            try:
                observer_result = self._observer(payload)
                if isinstance(observer_result, Mapping):
                    observer_receipt = observer_result
                    observer_accepted = observer_result.get("accepted") is True
                else:
                    observer_accepted = observer_result is not False
            except Exception as exc:  # noqa: BLE001 - retry same window later
                observer_accepted = False
                logger.warning(
                    "[p0-2m] observer rejected payload error_type=%s; "
                    "completed window will retry",
                    type(exc).__name__,
                )
        # The production observer returns a receipt timestamp captured
        # immediately after its bounded work-queue put.  A boolean-only test or
        # compatibility observer lacks per-candidate dedupe evidence and is
        # retained as explicitly unavailable rather than guessed.
        receipt_enqueued_at: Optional[datetime] = None
        receipt_outcomes: dict[tuple[str, str], Mapping[str, Any]] = {}
        if observer_receipt is not None:
            try:
                raw_enqueued_at = observer_receipt.get(
                    "observer_queue_enqueued_at"
                )
                if raw_enqueued_at:
                    receipt_enqueued_at = datetime.fromisoformat(
                        str(raw_enqueued_at).replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
            except (TypeError, ValueError):
                receipt_enqueued_at = None
            for raw_outcome in observer_receipt.get("candidate_outcomes") or ():
                if not isinstance(raw_outcome, Mapping):
                    continue
                receipt_outcomes[
                    (
                        str(raw_outcome.get("symbol") or "").strip().upper(),
                        str(raw_outcome.get("window_end") or ""),
                    )
                ] = raw_outcome
        if (
            self._shadow_recorder is not None
            and expected_rth_end is not None
            and (
                self._shadow_enroller is None
                or (
                    self._shadow_enrollment_ready
                    and market_date_et in self._shadow_enrollment_sessions
                )
            )
        ):
            try:
                from src.journal.ledger.p0_2m_shadow_repository import (
                    P02mShadowObservationInput,
                )

                telemetry_rows: list[P02mShadowObservationInput] = []
                for symbol, (evidence, current_rows) in telemetry_facts.items():
                    raw_window_end = evidence.get("window_end")
                    try:
                        evidence_end = datetime.fromisoformat(
                            str(raw_window_end).replace("Z", "+00:00")
                        ).astimezone(_ET)
                    except (TypeError, ValueError):
                        evidence_end = None
                    endpoint_matches = evidence_end == expected_rth_end
                    state = str(evidence.get("state") or "unavailable")
                    structural_pending = (
                        state == "pending"
                        and evidence.get("unavailable_reason")
                        == "fewer_than_18_completed_minutes_in_session_stratum"
                    )
                    quality = evidence.get("data_quality")
                    evidence_data_complete = bool(
                        isinstance(quality, Mapping)
                        and quality.get("status") == "complete"
                    )
                    (
                        endpoint_component_complete,
                        complete_from_open,
                        max_component_end,
                        component_audit,
                    ) = self._component_finality(
                        current_rows,
                        expected_end=expected_rth_end,
                    )
                    component_is_final = bool(
                        endpoint_component_complete
                        and max_component_end == expected_rth_end
                        and max_component_end <= now.astimezone(_ET)
                    )
                    data_available = bool(
                        endpoint_matches
                        and component_is_final
                        and (
                            evidence_data_complete
                            or (structural_pending and complete_from_open)
                        )
                    )
                    evaluation_available = data_available
                    qualified = bool(
                        evaluation_available
                        and evidence.get("alert_qualified") is True
                        and evidence.get("session_scope") == "regular"
                    )
                    outcome = receipt_outcomes.get((symbol, str(raw_window_end or "")))
                    notification_decision_available = bool(
                        not qualified
                        or (
                            isinstance(outcome, Mapping)
                            and outcome.get("notification_decision_available")
                            is True
                        )
                    )
                    would_notify = bool(
                        qualified
                        and isinstance(outcome, Mapping)
                        and outcome.get("would_notify") is True
                    )
                    duplicate_suppressed = bool(
                        isinstance(outcome, Mapping)
                        and outcome.get("duplicate_episode_suppressed") is True
                    )
                    direction = (
                        str(evidence.get("direction"))
                        if evidence.get("direction") is not None
                        else None
                    )
                    source = (
                        str(evidence.get("source"))
                        if evidence.get("source") is not None
                        else None
                    )
                    unavailable_reason = (
                        str(evidence.get("unavailable_reason"))
                        if evidence.get("unavailable_reason") is not None
                        else None
                    )
                    quality_payload = (
                        dict(quality)
                        if isinstance(quality, Mapping)
                        else {
                            "status": "incomplete",
                            "issues": ["data_quality_missing"],
                        }
                    )
                    quality_payload["telemetry_component_finality"] = component_audit
                    window_close = None
                    if evaluation_available and source:
                        # Capture the exact completed endpoint even when it
                        # does not qualify.  These later rows supply t+20m
                        # diagnostics without hindsight reads or backfilling.
                        endpoint_prices = []
                        for row in current_rows:
                            try:
                                start = _history_bar_start_et(row.get("date"))
                                price = float(row.get("close"))
                            except (TypeError, ValueError):
                                continue
                            if start + timedelta(minutes=2) == expected_rth_end:
                                if math.isfinite(price) and price > 0:
                                    endpoint_prices.append(price)
                        if len(endpoint_prices) == 1:
                            window_close = endpoint_prices[0]
                            quality_payload["window_close_basis"] = {
                                "source": source,
                                "price": "completed_exact_2m_close",
                                "adjustment": "same_session_provider_basis",
                                "session_scope": "regular",
                                "basis_verified": False,
                                "limitation": "provider_adjustment_mode_not_explicit",
                            }

                    common = dict(
                        symbol=symbol,
                        et_date=market_date_et,
                        window_end=expected_rth_end,
                        universe_hash=self.universe_hash,
                        signal_version=str(
                            evidence.get("signal_version")
                            or P0_2M_SIGNAL_VERSION
                        ),
                        observed_at=now,
                        max_component_end=max_component_end,
                        direction=direction,
                        source=source,
                        unavailable_reason=unavailable_reason,
                        data_quality=quality_payload,
                        enrollment_key=self._shadow_enrollment_key,
                    )
                    # First endpoint sighting is immutable.  A later +65s
                    # completed evaluation is a separate row, so an initial
                    # +5s data miss cannot permanently erase a valid SLA event.
                    telemetry_rows.append(
                        P02mShadowObservationInput(
                            **common,
                            record_kind="endpoint_observation",
                            data_available=data_available,
                            evaluation_available=False,
                            evaluation_state="endpoint_observed",
                            qualified=False,
                            would_notify=False,
                            notification_decision_available=False,
                        )
                    )
                    if not evaluation_available:
                        continue
                    telemetry_rows.append(
                        P02mShadowObservationInput(
                            **common,
                            record_kind="completed_evaluation",
                            data_available=True,
                            evaluation_available=True,
                            evaluation_state=state,
                            window_close=window_close,
                            qualified=qualified,
                            would_notify=would_notify,
                            notification_decision_available=(
                                notification_decision_available
                            ),
                            duplicate_episode_suppressed=duplicate_suppressed,
                        )
                    )

                    attempt_id = (
                        str(outcome.get("notification_attempt_id") or "")
                        if isinstance(outcome, Mapping)
                        else ""
                    )
                    enqueue_mode = (
                        str(outcome.get("telegram_enqueue_evidence_mode") or "")
                        if isinstance(outcome, Mapping)
                        else ""
                    )
                    if not (would_notify and attempt_id and enqueue_mode):
                        continue
                    attempt_accepted = bool(
                        observer_accepted
                        and outcome.get("telegram_queue_accepted") is True
                        and receipt_enqueued_at is not None
                    )
                    telemetry_rows.append(
                        P02mShadowObservationInput(
                            **common,
                            record_kind="notification_attempt",
                            notification_attempt_id=attempt_id,
                            data_available=True,
                            evaluation_available=True,
                            evaluation_state=state,
                            qualified=True,
                            would_notify=True,
                            notification_decision_available=True,
                            observer_queue_accepted=attempt_accepted,
                            observer_queue_enqueued_at=(
                                receipt_enqueued_at if attempt_accepted else None
                            ),
                            telegram_enqueue_evidence_mode=enqueue_mode,
                            telegram_enqueued_at=(
                                receipt_enqueued_at if attempt_accepted else None
                            ),
                        )
                    )
                if telemetry_rows:
                    self._shadow_recorder(telemetry_rows)
                    with self._lock:
                        self._shadow_last_write_at = self._now().isoformat()
                        self._shadow_write_error = None
            except Exception as exc:  # noqa: BLE001 - telemetry is non-blocking
                with self._lock:
                    self._shadow_last_write_failed_at = self._now().isoformat()
                    self._shadow_write_error = type(exc).__name__
                logger.warning(
                    "[p0-2m] live-forward telemetry append failed "
                    "error_type=%s; signal path continues",
                    type(exc).__name__,
                )
        if observer_accepted and pending_complete_windows:
            with self._lock:
                self._last_complete_window.update(pending_complete_windows)
        result = {
            "candidate_count": len(candidates),
            "ready_count": ready_count,
            "baseline_warming_count": warming_count,
            "unavailable_count": unavailable_count,
            "mobile_enabled": mobile_enabled,
            "mobile_reason": mobile_reason,
            "observer_accepted": observer_accepted,
        }
        with self._lock:
            self._last_scan_at = now.isoformat()
            self._scan_completed_at = self._now()
            self._last_scan_health = dict(payload["p0_scan_health"])
            self._last_mobile_enabled = mobile_enabled
            self._last_mobile_reason = mobile_reason
            self._last_mobile_eligible_count = len(mobile_eligible_symbols)
        return result


__all__ = ["IntradayP02mMonitor"]
