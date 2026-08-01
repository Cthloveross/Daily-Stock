# -*- coding: utf-8 -*-
"""Zero-cost official annual schedule for FOMC / CPI / NFP.

Finnhub's free tier returns 403 on ``/calendar/economic``, which used to
degrade the Regime events domain every single day.  The three series that
scoring actually consumes (FOMC decision, CPI release, Employment Situation
release) are published a full year ahead by their official sources, so this
module reads a locally versioned data file instead of a paid calendar API:

* FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
  (flag days are the DECISION days — the second day of two-day meetings)
* CPI: https://www.bls.gov/schedule/news_release/cpi.htm
* Employment Situation (NFP): https://www.bls.gov/schedule/news_release/empsit.htm

Data files live in ``src/regime/data/official_economic_schedule_*.json`` and
record ``source_url`` + ``retrieved_at`` per series.  Multiple files (for
example one per schedule year) are merged.

Fail-closed contract: a stale schedule must never silently report "no events".
``get_official_macro_snapshot`` only reports ``readiness == "ready"`` when the
whole agenda window (``target_date`` .. ``target_date + window_days``) sits
inside every series' coverage.  Outside coverage the flags stay ``False`` and
readiness is ``unavailable`` so scorers ignore them.

Annual ops step: when the Federal Reserve / BLS publish the next year's
schedules, refresh the data file (add ``official_economic_schedule_<year>.json``
or extend the existing one) from the official pages above; nothing else needs
to change.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "OfficialEconomicSchedule",
    "get_official_macro_snapshot",
    "load_official_schedule",
]

_DATA_DIR = Path(__file__).resolve().parent / "data"
_DATA_GLOB = "official_economic_schedule_*.json"
# The three series the Regime macro scorer consumes.  A file that cannot prove
# all three must not claim a clean calendar.
_REQUIRED_SERIES = ("fomc", "cpi", "nfp")
_AGENDA_WINDOW_DAYS = 7

_cache_lock = threading.Lock()
_cached_schedule: Optional["OfficialEconomicSchedule"] = None
_cached_dir: Optional[Path] = None


@dataclass(frozen=True)
class _Series:
    name: str
    event: str
    source_url: str
    retrieved_at: str
    release_time_et: Optional[str]
    dates: frozenset
    coverage_start: date
    coverage_through: date


@dataclass(frozen=True)
class OfficialEconomicSchedule:
    """Merged, immutable view over the versioned schedule data files."""

    schedule_version: str
    retrieved_at: str
    series: dict

    @property
    def coverage_start(self) -> date:
        return max(item.coverage_start for item in self.series.values())

    @property
    def coverage_through(self) -> date:
        return min(item.coverage_through for item in self.series.values())

    @property
    def source_urls(self) -> dict:
        return {name: item.source_url for name, item in self.series.items()}

    def covers_window(self, target_date: date, window_days: int) -> bool:
        window_end = target_date + timedelta(days=window_days)
        return (
            self.coverage_start <= target_date
            and window_end <= self.coverage_through
        )

    def snapshot(
        self,
        target_date: date,
        *,
        window_days: int = _AGENDA_WINDOW_DAYS,
    ) -> dict:
        """Today's flags + the ``window_days`` US agenda, failing closed."""
        result = _empty_snapshot()
        result.update(
            {
                "schedule_version": self.schedule_version,
                "retrieved_at": self.retrieved_at,
                "source_urls": self.source_urls,
                "coverage_start": self.coverage_start.isoformat(),
                "coverage_through": self.coverage_through.isoformat(),
            }
        )
        if not self.covers_window(target_date, window_days):
            result["reason"] = "target_window_outside_coverage"
            return result

        window_end = target_date + timedelta(days=window_days)
        agenda: list[dict] = []
        for name in _REQUIRED_SERIES:
            item = self.series[name]
            result[f"{name}_today"] = target_date in item.dates
            for event_date in sorted(item.dates):
                if target_date <= event_date <= window_end:
                    agenda.append(
                        {
                            "date": event_date.isoformat(),
                            "time": item.release_time_et,
                            "event": item.event,
                            "impact": "high",
                            "estimate": None,
                            "prev": None,
                        }
                    )
        agenda.sort(key=lambda row: (row["date"], row["event"]))
        result["us_agenda"] = agenda
        result["readiness"] = "ready"
        result["reason"] = None
        return result


def _empty_snapshot() -> dict:
    return {
        "source": "official_schedule",
        "readiness": "unavailable",
        "reason": "schedule_unavailable",
        "fomc_today": False,
        "cpi_today": False,
        "nfp_today": False,
        "us_agenda": [],
        "schedule_version": None,
        "retrieved_at": None,
        "source_urls": {},
        "coverage_start": None,
        "coverage_through": None,
    }


def _parse_date(value: Any, *, context: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid date {value!r} in {context}") from exc


def _load_series(name: str, payload: dict, *, context: str) -> _Series:
    dates = frozenset(
        _parse_date(raw, context=f"{context}:{name}.dates")
        for raw in payload.get("dates") or []
    )
    if not dates:
        raise ValueError(f"series {name!r} in {context} has no dates")
    coverage_start = _parse_date(
        payload.get("coverage_start"),
        context=f"{context}:{name}.coverage_start",
    )
    coverage_through = _parse_date(
        payload.get("coverage_through"),
        context=f"{context}:{name}.coverage_through",
    )
    if coverage_through < coverage_start:
        raise ValueError(f"series {name!r} in {context} has inverted coverage")
    source_url = str(payload.get("source_url") or "").strip()
    if not source_url:
        raise ValueError(f"series {name!r} in {context} is missing source_url")
    return _Series(
        name=name,
        event=str(payload.get("event") or name.upper()),
        source_url=source_url,
        retrieved_at=str(payload.get("retrieved_at") or ""),
        release_time_et=payload.get("release_time_et"),
        dates=dates,
        coverage_start=coverage_start,
        coverage_through=coverage_through,
    )


def _merge_series(existing: _Series, incoming: _Series) -> _Series:
    """Union of dates across data files; newest retrieval metadata wins."""
    newer = incoming if incoming.retrieved_at >= existing.retrieved_at else existing
    return _Series(
        name=existing.name,
        event=newer.event,
        source_url=newer.source_url,
        retrieved_at=newer.retrieved_at,
        release_time_et=newer.release_time_et,
        dates=existing.dates | incoming.dates,
        coverage_start=min(existing.coverage_start, incoming.coverage_start),
        coverage_through=max(
            existing.coverage_through, incoming.coverage_through
        ),
    )


def load_official_schedule(
    data_dir: Optional[Path] = None,
) -> Optional[OfficialEconomicSchedule]:
    """Load and merge every schedule data file; ``None`` when unusable.

    Any missing required series or malformed file makes the whole schedule
    unusable (fail closed) rather than silently narrowing coverage.
    """
    directory = Path(data_dir) if data_dir is not None else _DATA_DIR
    merged: dict[str, _Series] = {}
    versions: list[str] = []
    retrieved: list[str] = []
    try:
        files = sorted(directory.glob(_DATA_GLOB))
        for file_path in files:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            series_map = payload.get("series")
            if not isinstance(series_map, dict):
                raise ValueError(f"{file_path.name} has no series map")
            version = str(
                payload.get("schedule_version")
                or payload.get("schema_version")
                or file_path.stem
            )
            versions.append(version)
            retrieved.append(str(payload.get("retrieved_at") or ""))
            for name, item in series_map.items():
                series = _load_series(
                    str(name).lower(), item, context=file_path.name
                )
                if series.name in merged:
                    merged[series.name] = _merge_series(
                        merged[series.name], series
                    )
                else:
                    merged[series.name] = series
    except Exception as exc:  # noqa: BLE001
        logger.warning("official economic schedule unusable: %s", exc)
        return None
    missing = [name for name in _REQUIRED_SERIES if name not in merged]
    if missing:
        logger.warning(
            "official economic schedule missing required series: %s",
            ", ".join(missing),
        )
        return None
    return OfficialEconomicSchedule(
        schedule_version="+".join(versions),
        retrieved_at=max(retrieved) if retrieved else "",
        series=merged,
    )


def get_official_macro_snapshot(
    target_date: date,
    *,
    window_days: int = _AGENDA_WINDOW_DAYS,
    data_dir: Optional[Path] = None,
) -> dict:
    """Module-level entry point with a process-wide schedule cache.

    Passing ``data_dir`` bypasses the cache (test seam).  Never raises: any
    load problem is reported as ``readiness == "unavailable"``.
    """
    if data_dir is not None:
        schedule = load_official_schedule(data_dir)
    else:
        global _cached_schedule, _cached_dir
        with _cache_lock:
            if _cached_schedule is None or _cached_dir != _DATA_DIR:
                _cached_schedule = load_official_schedule(_DATA_DIR)
                _cached_dir = _DATA_DIR
            schedule = _cached_schedule
    if schedule is None:
        return _empty_snapshot()
    return schedule.snapshot(target_date, window_days=window_days)
