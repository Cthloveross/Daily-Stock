# -*- coding: utf-8 -*-
"""Strict, session-aware derivation of 2-minute US equity bars.

The public history loader can only request native 1-minute bars from every
configured provider.  This module converts those rows to one canonical
contract before any signal code consumes them:

* input timestamps are timezone-aware and output timestamps are ET bar starts;
* Moomoo's end labels are shifted by one minute before bucketing, while
  start-labelled providers (including Yahoo) are left in place;
* buckets are anchored independently to premarket 04:00, the XNYS open, and
  the authoritative XNYS close (including early closes);
* a derived bar exists only when both unique, consecutive, legal and completed
  component minutes are present.

The implementation deliberately fails closed when the XNYS calendar or a
session schedule cannot be resolved.  It never invents a normal 16:00 close
for an unknown date.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any, NamedTuple, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from src.opportunities.intraday_bursts import BAR_LABEL_END_SOURCES

_ET = ZoneInfo("America/New_York")
_PREMARKET_OPEN = time(4, 0)
_EXTENDED_CLOSE = time(20, 0)

EXACT_2M_AGGREGATION_CONTRACT = "session_anchored_exact_2m_v2"


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "pct_chg",
        ]
    )


def _aware_et(
    value: Any,
    *,
    require_minute_alignment: bool = True,
) -> Optional[datetime]:
    # History frames contain tens of thousands of already-normalized ISO
    # values.  ``pd.to_datetime`` per scalar is much slower and retains the
    # GIL, so keep it only as a compatibility fallback for uncommon inputs.
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(
                f"{text[:-1]}+00:00" if text[-1:] in {"Z", "z"} else text
            )
        except ValueError:
            parsed = pd.to_datetime(value, errors="coerce")
    else:
        parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed) or parsed.tzinfo is None:
        return None
    if hasattr(parsed, "to_pydatetime"):
        parsed = parsed.to_pydatetime()
    result = parsed.astimezone(_ET)
    if require_minute_alignment and (result.second != 0 or result.microsecond != 0):
        return None
    return result


def _calendar_et(value: Any) -> Optional[datetime]:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(_ET)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class _NormalizedMinute(NamedTuple):
    timestamp: datetime
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]
    amount: Optional[float]
    source_duplicate: bool


def _legal_minute(row: _NormalizedMinute) -> bool:
    open_ = row.open
    high = row.high
    low = row.low
    close = row.close
    volume = row.volume
    if None in (open_, high, low, close, volume):
        return False
    assert open_ is not None
    assert high is not None
    assert low is not None
    assert close is not None
    assert volume is not None
    return (
        min(open_, high, low, close) > 0
        and high >= low
        and low <= open_ <= high
        and low <= close <= high
        and volume >= 0
    )


def _session_schedule(
    calendar: Any,
    market_date: date,
) -> Optional[tuple[datetime, datetime]]:
    try:
        if not bool(calendar.is_session(market_date)):
            return None
        open_at = _calendar_et(calendar.session_open(market_date))
        close_at = _calendar_et(calendar.session_close(market_date))
    except Exception:  # noqa: BLE001 - calendar uncertainty must fail closed
        return None
    if open_at is None or close_at is None:
        return None
    if open_at.date() != market_date or close_at.date() != market_date:
        return None
    if close_at <= open_at:
        return None
    return open_at, close_at


def _bucket_start(
    timestamp: datetime,
    *,
    session_open: datetime,
    session_close: datetime,
) -> Optional[tuple[str, datetime]]:
    premarket_open = datetime.combine(
        timestamp.date(), _PREMARKET_OPEN, tzinfo=_ET
    )
    extended_close = datetime.combine(
        timestamp.date(), _EXTENDED_CLOSE, tzinfo=_ET
    )
    if not (premarket_open <= timestamp < extended_close):
        return None

    if timestamp < session_open:
        scope = "premarket"
        anchor = premarket_open
        boundary = session_open
    elif timestamp < session_close:
        scope = "regular"
        anchor = session_open
        boundary = session_close
    else:
        scope = "afterhours"
        anchor = session_close
        boundary = extended_close

    offset_minutes = int((timestamp - anchor).total_seconds() // 60)
    start = anchor + timedelta(minutes=(offset_minutes // 2) * 2)
    if start < anchor or start + timedelta(minutes=2) > boundary:
        return None
    return scope, start


def aggregate_exact_2m_ohlcv(
    frame: pd.DataFrame,
    *,
    source: Optional[str],
    as_of: Optional[datetime] = None,
    calendar: Any = None,
) -> pd.DataFrame:
    """Return exact, start-labelled 2m OHLCV bars derived from native 1m.

    ``as_of`` is injectable for deterministic tests.  A component minute is
    completed only once ``minute_start + 1 minute <= as_of``.  With no
    explicit value, the current timezone-aware ET clock is used.
    """

    if frame is None or frame.empty or "date" not in frame.columns:
        return _empty_result()

    observed = (
        datetime.now(tz=_ET)
        if as_of is None
        else _aware_et(as_of, require_minute_alignment=False)
    )
    if observed is None:
        return _empty_result()

    if calendar is None:
        try:
            import exchange_calendars as xcals

            calendar = xcals.get_calendar("XNYS")
        except Exception:  # noqa: BLE001 - exact schedule is mandatory
            return _empty_result()

    normalized: list[_NormalizedMinute] = []
    shift_end_labels = source in BAR_LABEL_END_SOURCES
    # ``DataFrame.to_dict(orient="records")`` materializes one Python dict per
    # source minute and runs pandas object conversion over every cell.  A
    # 60-session extended-hours frame has about 57,600 rows, so that conversion
    # alone can starve unrelated FastAPI requests.  Positional tuples preserve
    # the same row multiplicity without the expensive record materialization.
    column_index = {str(name): index for index, name in enumerate(frame.columns)}
    date_index = column_index["date"]
    open_index = column_index.get("open")
    high_index = column_index.get("high")
    low_index = column_index.get("low")
    close_index = column_index.get("close")
    volume_index = column_index.get("volume")
    amount_index = column_index.get("amount")
    source_count_index = column_index.get("_source_time_key_count")

    def _value(values: tuple[Any, ...], index: Optional[int]) -> Any:
        return None if index is None else values[index]

    for values in frame.itertuples(index=False, name=None):
        timestamp = _aware_et(values[date_index])
        if timestamp is None:
            continue
        if shift_end_labels:
            timestamp -= timedelta(minutes=1)
        if timestamp + timedelta(minutes=1) > observed:
            continue
        source_count = _finite(_value(values, source_count_index))
        normalized.append(
            _NormalizedMinute(
                timestamp=timestamp,
                open=_finite(_value(values, open_index)),
                high=_finite(_value(values, high_index)),
                low=_finite(_value(values, low_index)),
                close=_finite(_value(values, close_index)),
                volume=_finite(_value(values, volume_index)),
                amount=_finite(_value(values, amount_index)),
                source_duplicate=(
                    source_count is not None and source_count != 1.0
                ),
            )
        )

    if not normalized:
        return _empty_result()

    schedules: dict[date, Optional[tuple[datetime, datetime]]] = {}
    bucket_rows: dict[
        tuple[date, str, datetime], list[_NormalizedMinute]
    ] = {}
    for row in normalized:
        timestamp = row.timestamp
        market_date = timestamp.date()
        if market_date not in schedules:
            schedules[market_date] = _session_schedule(calendar, market_date)
        schedule = schedules[market_date]
        if schedule is None:
            continue
        key = _bucket_start(
            timestamp,
            session_open=schedule[0],
            session_close=schedule[1],
        )
        if key is None:
            continue
        scope, start = key
        bucket_rows.setdefault((market_date, scope, start), []).append(row)

    bars: list[dict[str, Any]] = []
    for (_market_date, _scope, start), rows in sorted(
        bucket_rows.items(), key=lambda item: item[0][2]
    ):
        # Count the raw rows as well as unique timestamps: a duplicate minute
        # invalidates the bucket instead of being silently deduplicated.
        if len(rows) != 2:
            continue
        rows = sorted(rows, key=lambda item: item.timestamp)
        if rows[0].timestamp != start:
            continue
        if rows[1].timestamp != start + timedelta(minutes=1):
            continue
        if len({row.timestamp for row in rows}) != 2:
            continue
        if any(row.source_duplicate for row in rows):
            continue
        if not all(_legal_minute(row) for row in rows):
            continue

        amounts = [row.amount for row in rows]
        bars.append(
            {
                "date": start.isoformat(),
                "open": rows[0].open,
                "high": max(row.high for row in rows),
                "low": min(row.low for row in rows),
                "close": rows[1].close,
                "volume": sum(row.volume for row in rows),
                "amount": (
                    sum(value for value in amounts if value is not None)
                    if all(value is not None and value >= 0 for value in amounts)
                    else None
                ),
            }
        )

    if not bars:
        return _empty_result()

    result = pd.DataFrame(bars)
    result["pct_chg"] = (
        result["close"].pct_change() * 100
    ).fillna(0).round(2)
    return result


__all__ = [
    "EXACT_2M_AGGREGATION_CONTRACT",
    "aggregate_exact_2m_ohlcv",
]
