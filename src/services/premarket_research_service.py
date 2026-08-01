# -*- coding: utf-8 -*-
"""Server-owned canonical XNYS premarket research orchestration.

The service deliberately performs no background-thread cancellation.  One
request owns the provider work while matching callers wait for a bounded
period.  Publication remains append-only and is allowed only after the exact
window, Regime causality, and completed-session freshness gates pass.
"""
from __future__ import annotations

import copy
import logging
import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from src.opportunities.engine import SIGNAL_VERSION
from src.opportunities.engine import (
    is_supported_us_option_underlying,
    normalize_symbols,
)
from src.opportunities.cycle_repository import (
    CycleAttemptStatus,
    PremarketAttemptLeaseLostError,
    PremarketPublicationDeadlineError,
    append_cycle_event,
    claim_cycle_attempt,
    count_cycle_attempts,
    get_latest_cycle_attempt_status,
    get_latest_research_universe,
    publish_cycle_snapshot_atomically,
)
from src.opportunities.premarket import (
    CANONICAL_CYCLE_VERSION,
    CANONICAL_FREEZE_POLICY_VERSION,
    CANONICAL_SCOPE_KEY,
    CanonicalPremarketWindow,
    assess_canonical_inputs,
    assess_regime_for_cycle,
    resolve_canonical_premarket_window,
)
from src.opportunities.repository import (
    SnapshotAppendResult,
    append_snapshot,
    canonical_sha256,
    get_snapshot,
    list_snapshots,
)
from src.services.opportunity_snapshot_service import (
    SnapshotPublishWindowClosedError,
    freeze_daily_snapshot,
    prepare_snapshot_benchmark_context,
    snapshot_item,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "canonical-premarket-cycle/1.0"
_FOLLOWER_WAIT_SECONDS = 30.0
_MAX_CYCLE_STATUSES = 64
_LATEST_START_BUFFER = timedelta(minutes=2)
_SCHEDULE_PRIMARY_FROM_OPEN = timedelta(minutes=18)
_SCHEDULE_RETRY_FROM_OPEN = timedelta(minutes=13)
_ATTEMPT_LEASE_SECONDS = 120

ScanRunner = Callable[..., dict[str, Any]]
RegimeLoader = Callable[[date, Sequence[str], datetime], Mapping[str, Any]]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ResolvedPremarketUniverse:
    symbols: tuple[str, ...]
    requested_limit: int
    source: str
    universe_version_key: Optional[str] = None
    created_at: Optional[datetime] = None


def resolve_premarket_research_universe(
    *,
    requested_symbols: Sequence[str] = (),
    requested_limit: int = 5,
    configured_symbols: Sequence[str] = (),
    include_fallback_suggestion: bool = False,
    db_manager=None,
) -> Optional[ResolvedPremarketUniverse]:
    """Resolve the persisted pool; optional fallbacks are display suggestions."""

    persisted = get_latest_research_universe(
        scope_key=CANONICAL_SCOPE_KEY,
        db_manager=db_manager,
    )
    if persisted is not None:
        return ResolvedPremarketUniverse(
            symbols=persisted.symbols,
            requested_limit=persisted.requested_limit,
            source="persisted",
            universe_version_key=persisted.universe_version_key,
            created_at=persisted.created_at,
        )

    if not include_fallback_suggestion:
        return None

    def supported(values: Sequence[str]) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol in normalize_symbols(values)[:20]
            if is_supported_us_option_underlying(symbol)
        )

    configured = supported(configured_symbols)
    if configured:
        return ResolvedPremarketUniverse(
            symbols=configured,
            requested_limit=max(1, min(15, int(requested_limit))),
            source="stock_list_fallback",
        )
    requested = supported(requested_symbols)
    if requested:
        return ResolvedPremarketUniverse(
            symbols=requested,
            requested_limit=max(1, min(15, int(requested_limit))),
            source="request_fallback",
        )
    return None


@dataclass
class _CycleFlight:
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[dict[str, Any]] = None


_cycle_lock = threading.RLock()
_cycle_flights: dict[str, _CycleFlight] = {}
_cycle_statuses: dict[str, dict[str, Any]] = {}


def _remember_status(key: str, status: Mapping[str, Any]) -> None:
    _cycle_statuses.pop(key, None)
    _cycle_statuses[key] = copy.deepcopy(dict(status))
    while len(_cycle_statuses) > _MAX_CYCLE_STATUSES:
        oldest_key = next(iter(_cycle_statuses))
        _cycle_statuses.pop(oldest_key, None)


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime | date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _cycle_key(market_date_et: date) -> str:
    return "pmr_" + canonical_sha256(
        {
            "cycle_version": CANONICAL_CYCLE_VERSION,
            "freeze_policy_version": CANONICAL_FREEZE_POLICY_VERSION,
            "scope_key": CANONICAL_SCOPE_KEY,
            "market_date_et": market_date_et,
        }
    )


def _window_fields(window: CanonicalPremarketWindow) -> dict[str, Any]:
    latest_start = (
        window.window_end_at - _LATEST_START_BUFFER
        if window.window_end_at is not None
        else None
    )
    return {
        "market_date_et": window.market_date_et.isoformat(),
        "previous_session": _iso(window.previous_session),
        "regular_open_at": _iso(window.regular_open_at),
        "window_start_at": _iso(window.window_start_at),
        "window_end_at": _iso(window.window_end_at),
        "latest_start_at": _iso(latest_start),
        "primary_scheduled_at": _iso(
            window.regular_open_at - _SCHEDULE_PRIMARY_FROM_OPEN
            if window.regular_open_at is not None
            else None
        ),
        "retry_scheduled_at": _iso(
            window.regular_open_at - _SCHEDULE_RETRY_FROM_OPEN
            if window.regular_open_at is not None
            else None
        ),
    }


def _base_response(
    window: CanonicalPremarketWindow,
    symbols: Sequence[str],
    limit: int,
    *,
    state: str,
    quality: str = "unknown",
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "schema_version": SCHEMA_VERSION,
        "cycle_version": CANONICAL_CYCLE_VERSION,
        "freeze_policy_version": CANONICAL_FREEZE_POLICY_VERSION,
        "scope_key": CANONICAL_SCOPE_KEY,
        "cycle_key": _cycle_key(window.market_date_et),
        "state": state,
        "quality": quality,
        **_window_fields(window),
        "universe": list(symbols),
        "requested_limit": int(limit),
        "universe_source": "unavailable",
        "universe_version_key": None,
        "attempt_key": None,
        "attempt_trigger": None,
        "attempt_started_at": None,
        "lease_expires_at": None,
        "recovered_from_attempt_key": None,
        "recoverable": False,
        "next_scheduled_at": None,
        "scheduler_enabled": False,
        "stages": [],
        "regime_quality": {},
        "quality_reasons": [],
        "run": None,
        "snapshot": None,
        "idempotent_replay": False,
        "message": message,
    }
    result.update(extra)
    return result


def _stored_response(
    window: CanonicalPremarketWindow,
    symbols: Sequence[str],
    limit: int,
    *,
    stored,
    db_manager,
    observed_at: datetime,
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(stored.payload))
    cycle_meta = payload.get("canonical_cycle") or {}
    stored_symbols = tuple(
        str(symbol) for symbol in (payload.get("universe") or symbols)
    )
    stored_limit = int(payload.get("requested_limit") or stored.requested_limit)
    snapshot = snapshot_item(
        stored,
        db_manager=db_manager,
        idempotent_replay=True,
        observed_at=observed_at,
    )
    return _base_response(
        window,
        stored_symbols,
        stored_limit,
        state="published",
        quality=str(cycle_meta.get("quality") or "unknown"),
        message="今日官方盘前研究已发布；返回同一份不可变快照。",
        cycle_key=str(
            cycle_meta.get("cycle_key")
            or _cycle_key(window.market_date_et)
        ),
        cycle_as_of=cycle_meta.get("cycle_as_of"),
        started_at=cycle_meta.get("started_at"),
        completed_at=cycle_meta.get("published_at") or stored.frozen_at.isoformat(),
        stages=copy.deepcopy(list(cycle_meta.get("stages") or ())),
        regime_quality=copy.deepcopy(dict(cycle_meta.get("regime_quality") or {})),
        quality_reasons=list(cycle_meta.get("quality_reasons") or ()),
        universe_source=str(
            cycle_meta.get("universe_source") or "persisted"
        ),
        universe_version_key=cycle_meta.get("universe_version_key"),
        attempt_key=cycle_meta.get("attempt_key"),
        attempt_trigger=cycle_meta.get("attempt_trigger"),
        attempt_started_at=cycle_meta.get("attempt_started_at"),
        recovered_from_attempt_key=cycle_meta.get(
            "recovered_from_attempt_key"
        ),
        run=payload,
        snapshot=snapshot,
        idempotent_replay=True,
    )


def _official_snapshot(market_date_et: date, *, db_manager):
    matches = [
        snapshot
        for snapshot in list_snapshots(limit=500, db_manager=db_manager)
        if snapshot.market_date_et == market_date_et
        and snapshot.freeze_policy_version == CANONICAL_FREEZE_POLICY_VERSION
        and snapshot.scope_key == CANONICAL_SCOPE_KEY
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: (item.frozen_at, item.id))


_SENSITIVE_MANIFEST_KEYS = (
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "token",
    "url",
)


def _sanitize_manifest_value(value: Any, *, key: str = "") -> Any:
    lowered_key = key.strip().lower()
    if any(marker in lowered_key for marker in _SENSITIVE_MANIFEST_KEYS):
        return "[redacted]"
    if "error" in lowered_key and value not in (None, "", False):
        return "[redacted_provider_error]"
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize_manifest_value(
                child_value,
                key=str(child_key),
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_manifest_value(item, key=key)
            for item in value
        ]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        if "://" in value:
            return "[redacted_url]"
        return value[:512]
    if isinstance(value, (str, int, float, bool, date, datetime)) or value is None:
        return value
    return str(value)[:512]


def _regime_manifest(regime: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = regime.get("snapshot")
    snapshot_mapping = snapshot if isinstance(snapshot, Mapping) else {}
    domains = ("spy", "vix", "events", "sectors", "prev_day", "premarket")
    return {
        "date": _sanitize_manifest_value(regime.get("date"), key="date"),
        "score": _sanitize_manifest_value(regime.get("score"), key="score"),
        "label": _sanitize_manifest_value(regime.get("label"), key="label"),
        "dimensions": {
            name: _sanitize_manifest_value(regime.get(name), key=name)
            for name in (
                "d1_direction",
                "d2_volatility",
                "d3_macro_penalty",
                "d4_sector",
                "d5_prev_day",
                "d6_premarket",
            )
        },
        "version": _sanitize_manifest_value(regime.get("version"), key="version"),
        "generated_at": _sanitize_manifest_value(
            regime.get("generated_at"),
            key="generated_at",
        ),
        "snapshot": {
            "quality": _sanitize_manifest_value(
                snapshot_mapping.get("quality"),
                key="quality",
            ),
            "domains": {
                domain: _sanitize_manifest_value(
                    snapshot_mapping.get(domain),
                    key=domain,
                )
                for domain in domains
            },
        },
    }


def _default_regime_loader(
    market_date_et: date,
    symbols: Sequence[str],
    as_of: datetime,
) -> Mapping[str, Any]:
    from src.regime.classifier import compute_regime_score

    result = compute_regime_score(
        target_date=market_date_et,
        watchlist=list(symbols),
        save_to_db=True,
        as_of=as_of,
    )
    return {
        "date": result.date,
        "score": result.score,
        "label": result.label,
        "action_hint": result.action_hint,
        "d1_direction": result.d1_direction,
        "d2_volatility": result.d2_volatility,
        "d3_macro_penalty": result.d3_macro_penalty,
        "d4_sector": result.d4_sector,
        "d5_prev_day": result.d5_prev_day,
        "d6_premarket": result.d6_premarket,
        "snapshot": result.snapshot,
        "version": result.version,
        "generated_at": result.generated_at,
    }


def _stage(
    name: str,
    state: str,
    *,
    started_at: datetime,
    completed_at: Optional[datetime] = None,
    error_code: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "state": state,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat() if completed_at else None,
        "error_code": error_code,
    }


def _attempt_response(
    window: CanonicalPremarketWindow,
    status: CycleAttemptStatus,
    *,
    now: datetime,
    scheduler_enabled: bool,
    db_manager,
) -> dict[str, Any]:
    attempt = status.attempt
    terminal_payload = (
        dict(status.events[-1].payload)
        if status.events and status.events[-1].event_type == "attempt_finished"
        else {}
    )
    attempt_count = count_cycle_attempts(
        attempt.cycle_key,
        db_manager=db_manager,
    )
    retry_at = (
        window.regular_open_at - _SCHEDULE_RETRY_FROM_OPEN
        if window.regular_open_at is not None
        else None
    )
    latest_start = (
        window.window_end_at - _LATEST_START_BUFFER
        if window.window_end_at is not None
        else None
    )
    next_scheduled_at = None
    if (
        status.recoverable
        and attempt_count < 2
        and retry_at is not None
        and latest_start is not None
        and now < latest_start
    ):
        next_scheduled_at = max(now, retry_at).isoformat()
    return _base_response(
        window,
        attempt.universe,
        attempt.requested_limit,
        state=status.state,
        quality=str(
            terminal_payload.get("quality")
            or ("blocked" if status.state == "failed" else "unknown")
        ),
        message={
            "running": "官方盘前研究正在由服务端执行。",
            "blocked": "最近一次研究被证据门禁阻断。",
            "failed": "最近一次研究失败或租约已过期。",
            "window_closed": "最近一次研究越过截止时间。",
            "published": "官方盘前研究已发布。",
        }.get(status.state, "已读取持久化研究状态。"),
        universe_source=attempt.universe_source,
        universe_version_key=attempt.universe_version_key,
        attempt_key=attempt.attempt_key,
        attempt_trigger=attempt.trigger,
        attempt_started_at=attempt.started_at.isoformat(),
        started_at=attempt.started_at.isoformat(),
        completed_at=(
            status.completed_at.isoformat()
            if status.completed_at is not None
            else None
        ),
        lease_expires_at=(
            status.lease_expires_at.isoformat()
            if status.lease_expires_at is not None
            else None
        ),
        recovered_from_attempt_key=attempt.recovered_from_attempt_key,
        recoverable=(
            status.recoverable
            and attempt_count < 2
            and latest_start is not None
            and now < latest_start
        ),
        next_scheduled_at=next_scheduled_at,
        scheduler_enabled=scheduler_enabled,
        stages=[dict(stage) for stage in status.stages],
        regime_quality=dict(terminal_payload.get("regime_quality") or {}),
        quality_reasons=list(terminal_payload.get("quality_reasons") or ()),
        error_code=status.error_code,
    )


def _current_status(
    symbols: Sequence[str],
    limit: int,
    *,
    now: datetime,
    calendar: Any,
    db_manager,
    universe_source: str,
    universe_version_key: Optional[str],
    scheduler_enabled: bool,
) -> tuple[CanonicalPremarketWindow, dict[str, Any]]:
    window = resolve_canonical_premarket_window(now, calendar=calendar)
    key = _cycle_key(window.market_date_et)
    stored = _official_snapshot(window.market_date_et, db_manager=db_manager)
    if stored is not None:
        response = _stored_response(
            window,
            symbols,
            limit,
            stored=stored,
            db_manager=db_manager,
            observed_at=now,
        )
        response["scheduler_enabled"] = scheduler_enabled
        return window, response

    durable_status = get_latest_cycle_attempt_status(
        key,
        now=now,
        db_manager=db_manager,
    )
    if durable_status is not None:
        return window, _attempt_response(
            window,
            durable_status,
            now=now,
            scheduler_enabled=scheduler_enabled,
            db_manager=db_manager,
        )
    if not symbols:
        return window, _base_response(
            window,
            (),
            limit,
            state="research_pool_missing",
            message="尚未配置服务端研究池；不会启动行情或 Regime provider。",
            universe_source="unavailable",
            scheduler_enabled=scheduler_enabled,
            error_code="research_pool_missing",
        )

    latest_start = (
        window.window_end_at - _LATEST_START_BUFFER
        if window.window_end_at is not None
        else None
    )
    if (
        window.state == "in_window"
        and latest_start is not None
        and now >= latest_start
    ):
        return window, _base_response(
            window,
            symbols,
            limit,
            state="window_closed",
            message=(
                "已超过 09:18 ET 最晚启动时间；09:20 ET 发布硬截止前"
                "不再接受新研究周期。"
            ),
            universe_source=universe_source,
            universe_version_key=universe_version_key,
            scheduler_enabled=scheduler_enabled,
            error_code="latest_start_passed",
        )

    if window.state == "non_session":
        state = "non_session"
        message = "今天不是 XNYS 交易日，不生成官方盘前快照。"
    elif window.state == "before_window":
        state = "waiting_window"
        message = "等待官方盘前研究窗口开始。"
    elif window.state == "in_window":
        primary_at = window.regular_open_at - _SCHEDULE_PRIMARY_FROM_OPEN
        if now < primary_at:
            state = "waiting_window"
            message = "等待 09:12 ET 官方研究时点。"
        else:
            state = "ready_to_run"
            message = "已到 09:12 ET 官方研究时点，可以启动研究周期。"
    else:
        state = "window_closed"
        message = "今日官方盘前研究窗口已关闭，不允许事后补写。"
    return window, _base_response(
        window,
        symbols,
        limit,
        state=state,
        message=message,
        universe_source=universe_source,
        universe_version_key=universe_version_key,
        scheduler_enabled=scheduler_enabled,
        next_scheduled_at=(
            (
                window.regular_open_at - _SCHEDULE_PRIMARY_FROM_OPEN
            ).isoformat()
            if window.regular_open_at is not None
            and now < window.regular_open_at - _SCHEDULE_PRIMARY_FROM_OPEN
            else None
        ),
    )


def status_canonical_premarket_research(
    symbols: Sequence[str],
    limit: int,
    *,
    now: Optional[datetime] = None,
    calendar: Any = None,
    db_manager=None,
    universe_source: str = "persisted",
    universe_version_key: Optional[str] = None,
    scheduler_enabled: bool = False,
) -> dict[str, Any]:
    """Return the one official cycle state for the current market date."""

    persisted_universe = get_latest_research_universe(
        scope_key=CANONICAL_SCOPE_KEY,
        db_manager=db_manager,
    )
    if persisted_universe is None:
        symbols = ()
        universe_source = "unavailable"
        universe_version_key = None
    else:
        symbols = persisted_universe.symbols
        limit = persisted_universe.requested_limit
        universe_source = "persisted"
        universe_version_key = persisted_universe.universe_version_key
    observed_at = _aware_utc(
        now or datetime.now(timezone.utc),
        field_name="now",
    )
    _window, status = _current_status(
        tuple(symbols),
        int(limit),
        now=observed_at,
        calendar=calendar,
        db_manager=db_manager,
        universe_source=universe_source,
        universe_version_key=universe_version_key,
        scheduler_enabled=scheduler_enabled,
    )
    return status


def run_canonical_premarket_research(
    symbols: Sequence[str],
    limit: int,
    *,
    scan_runner: ScanRunner,
    now: Optional[datetime] = None,
    calendar: Any = None,
    db_manager=None,
    regime_loader: Optional[RegimeLoader] = None,
    clock: Optional[Clock] = None,
    history_loader=None,
    universe_source: str = "persisted",
    universe_version_key: Optional[str] = None,
    trigger: str = "manual",
    scheduler_enabled: bool = False,
) -> dict[str, Any]:
    """Run or join the one official canonical cycle for the market date."""

    persisted_universe = get_latest_research_universe(
        scope_key=CANONICAL_SCOPE_KEY,
        db_manager=db_manager,
    )
    if persisted_universe is None:
        normalized_symbols = ()
        normalized_limit = int(limit)
        universe_source = "unavailable"
        universe_version_key = None
    else:
        normalized_symbols = persisted_universe.symbols
        normalized_limit = persisted_universe.requested_limit
        universe_source = "persisted"
        universe_version_key = persisted_universe.universe_version_key
    clock_fn = clock or (lambda: datetime.now(timezone.utc))
    started_at = _aware_utc(
        now or clock_fn(),
        field_name="now",
    )
    window, initial = _current_status(
        normalized_symbols,
        normalized_limit,
        now=started_at,
        calendar=calendar,
        db_manager=db_manager,
        universe_source=universe_source,
        universe_version_key=universe_version_key,
        scheduler_enabled=scheduler_enabled,
    )
    if initial["state"] == "published":
        return initial
    if initial["state"] == "research_pool_missing":
        return initial
    if initial.get("attempt_key"):
        normalized_symbols = tuple(initial["universe"])
        normalized_limit = int(initial["requested_limit"])
        universe_source = str(initial["universe_source"])
        universe_version_key = initial.get("universe_version_key")
    latest_start = (
        window.window_end_at - _LATEST_START_BUFFER
        if window.window_end_at is not None
        else None
    )
    if latest_start is not None and started_at >= latest_start:
        return _base_response(
            window,
            normalized_symbols,
            normalized_limit,
            state="window_closed",
            message=(
                "已超过 09:18 ET 最晚启动时间；未启动新的 provider 工作。"
            ),
            universe_source=universe_source,
            universe_version_key=universe_version_key,
            scheduler_enabled=scheduler_enabled,
            error_code="latest_start_passed",
        )
    if window.state != "in_window":
        return initial
    primary_at = window.regular_open_at - _SCHEDULE_PRIMARY_FROM_OPEN
    retry_at = window.regular_open_at - _SCHEDULE_RETRY_FROM_OPEN
    key = str(initial["cycle_key"])
    attempt_count = count_cycle_attempts(key, db_manager=db_manager)
    if attempt_count == 0 and started_at < primary_at:
        return initial
    if attempt_count:
        if initial["state"] == "running":
            return initial
        if attempt_count >= 2:
            exhausted = copy.deepcopy(initial)
            exhausted["recoverable"] = False
            exhausted["next_scheduled_at"] = None
            exhausted["error_code"] = (
                exhausted.get("error_code") or "attempt_budget_exhausted"
            )
            return exhausted
        if started_at < retry_at:
            waiting = copy.deepcopy(initial)
            waiting["recoverable"] = True
            waiting["next_scheduled_at"] = retry_at.isoformat()
            waiting["message"] = "等待 09:17 ET 最后一次恢复重试。"
            return waiting

    owner_key = "pmw_" + uuid.uuid4().hex
    claim = claim_cycle_attempt(
        cycle_key=key,
        market_date_et=window.market_date_et,
        scope_key=CANONICAL_SCOPE_KEY,
        freeze_policy_version=CANONICAL_FREEZE_POLICY_VERSION,
        cycle_version=CANONICAL_CYCLE_VERSION,
        universe=normalized_symbols,
        requested_limit=normalized_limit,
        universe_source=universe_source,
        universe_version_key=universe_version_key,
        trigger=trigger,
        owner_key=owner_key,
        hard_deadline_at=window.window_end_at,
        now=started_at,
        lease_seconds=_ATTEMPT_LEASE_SECONDS,
        max_attempts=2,
        db_manager=db_manager,
    )
    if not claim.claimed:
        return _attempt_response(
            window,
            claim.status,
            now=started_at,
            scheduler_enabled=scheduler_enabled,
            db_manager=db_manager,
        )
    attempt_key = claim.attempt.attempt_key
    durable = append_cycle_event(
        attempt_key,
        owner_key=owner_key,
        event_type="stage_finished",
        stage="resolve_window",
        state="completed",
        now=started_at,
        lease_seconds=_ATTEMPT_LEASE_SECONDS,
        db_manager=db_manager,
    )
    with _cycle_lock:
        flight = _CycleFlight()
        _cycle_flights[key] = flight
    stages = [dict(stage) for stage in durable.stages]

    def persist_stage(
        name: str,
        state: str,
        observed_at: datetime,
        *,
        event_type: str,
        error_code: Optional[str] = None,
    ) -> None:
        nonlocal stages
        status = append_cycle_event(
            attempt_key,
            owner_key=owner_key,
            event_type=event_type,
            stage=name,
            state=state,
            error_code=error_code,
            now=observed_at,
            lease_seconds=_ATTEMPT_LEASE_SECONDS,
            db_manager=db_manager,
        )
        stages = [dict(stage) for stage in status.stages]

    current_stage = "compute_regime"
    terminal_persisted = False
    try:
        regime_started = _aware_utc(clock_fn(), field_name="clock")
        persist_stage(
            "compute_regime",
            "running",
            regime_started,
            event_type="stage_started",
        )
        regime = dict(
            (regime_loader or _default_regime_loader)(
                window.market_date_et,
                normalized_symbols,
                regime_started,
            )
        )
        cycle_as_of = _aware_utc(clock_fn(), field_name="clock")
        regime_state, regime_reasons, regime_domains = assess_regime_for_cycle(
            regime,
            market_date_et=window.market_date_et,
            cycle_as_of=cycle_as_of,
        )
        regime_stage_state = (
            "blocked"
            if regime_state == "blocked"
            else "degraded"
            if regime_state == "degraded"
            else "completed"
        )
        persist_stage(
            "compute_regime",
            regime_stage_state,
            cycle_as_of,
            event_type="stage_finished",
        )
        if regime_state == "blocked":
            result = _base_response(
                window,
                normalized_symbols,
                normalized_limit,
                state="blocked",
                quality="blocked",
                message="Regime 核心证据或因果时间门禁未通过，不发布快照。",
                started_at=started_at.isoformat(),
                completed_at=cycle_as_of.isoformat(),
                cycle_as_of=cycle_as_of.isoformat(),
                stages=stages,
                regime_quality=dict(regime_domains),
                quality_reasons=list(regime_reasons),
                error_code="regime_gate_blocked",
            )
        else:
            current_stage = "scan_completed_bars"
            scan_started = _aware_utc(clock_fn(), field_name="clock")
            persist_stage(
                "scan_completed_bars",
                "running",
                scan_started,
                event_type="stage_started",
            )
            run_payload = scan_runner(
                list(normalized_symbols),
                normalized_limit,
                as_of=cycle_as_of,
                regime=regime,
            )
            scan_completed = _aware_utc(clock_fn(), field_name="clock")
            persist_stage(
                "scan_completed_bars",
                "completed",
                scan_completed,
                event_type="stage_finished",
            )

            current_stage = "quality_gate"
            persist_stage(
                "quality_gate",
                "running",
                scan_completed,
                event_type="stage_started",
            )
            quality = assess_canonical_inputs(
                run_payload,
                regime,
                window=window,
                cycle_as_of=cycle_as_of,
            )
            gate_completed = _aware_utc(clock_fn(), field_name="clock")
            quality_stage_state = (
                "blocked"
                if quality.state == "blocked"
                else "degraded"
                if quality.state == "degraded"
                else "completed"
            )
            persist_stage(
                "quality_gate",
                quality_stage_state,
                gate_completed,
                event_type="stage_finished",
            )
            if quality.state == "blocked":
                result = _base_response(
                    window,
                    normalized_symbols,
                    normalized_limit,
                    state="blocked",
                    quality="blocked",
                    message="上一完整 XNYS 交易日或候选数据门禁未通过，不发布快照。",
                    started_at=started_at.isoformat(),
                    completed_at=gate_completed.isoformat(),
                    cycle_as_of=cycle_as_of.isoformat(),
                    stages=stages,
                    regime_quality=dict(quality.regime_domain_states),
                    quality_reasons=list(quality.reasons),
                    run=run_payload,
                    error_code="input_quality_gate_blocked",
                )
            else:
                benchmark_started = _aware_utc(clock_fn(), field_name="clock")
                if (
                    window.window_start_at is None
                    or window.window_end_at is None
                    or benchmark_started < window.window_start_at
                    or benchmark_started >= window.window_end_at
                ):
                    result = _base_response(
                        window,
                        normalized_symbols,
                        normalized_limit,
                        state="window_closed",
                        quality=quality.state,
                        message="研究完成时已越过官方盘前窗口，未写入事后快照。",
                        started_at=started_at.isoformat(),
                        completed_at=benchmark_started.isoformat(),
                        cycle_as_of=cycle_as_of.isoformat(),
                        stages=stages,
                        regime_quality=dict(quality.regime_domain_states),
                        quality_reasons=list(quality.reasons),
                        run=run_payload,
                        error_code="publish_window_closed",
                    )
                else:
                    current_stage = "prepare_benchmark"
                    persist_stage(
                        "prepare_benchmark",
                        "running",
                        benchmark_started,
                        event_type="stage_started",
                    )
                    benchmark_context = prepare_snapshot_benchmark_context(
                        run_payload,
                        fetched_at=benchmark_started,
                        history_loader=history_loader,
                    )
                    publish_at = _aware_utc(clock_fn(), field_name="clock")
                    persist_stage(
                        "prepare_benchmark",
                        "completed",
                        publish_at,
                        event_type="stage_finished",
                    )
                    if publish_at >= window.window_end_at:
                        result = _base_response(
                            window,
                            normalized_symbols,
                            normalized_limit,
                            state="window_closed",
                            quality=quality.state,
                            message=(
                                "基准证据预取完成时已达到 09:20 ET 发布硬截止，"
                                "未写入快照。"
                            ),
                            started_at=started_at.isoformat(),
                            completed_at=publish_at.isoformat(),
                            cycle_as_of=cycle_as_of.isoformat(),
                            stages=stages,
                            regime_quality=dict(
                                quality.regime_domain_states
                            ),
                            quality_reasons=list(quality.reasons),
                            run=run_payload,
                            error_code="publish_window_closed",
                        )
                    else:
                        current_stage = "persist_snapshot"
                        persist_stage(
                            "persist_snapshot",
                            "running",
                            publish_at,
                            event_type="stage_started",
                        )
                        supporting_reasons = [
                            f"regime_supporting_{domain}_{state}"
                            for domain, state in quality.regime_domain_states.items()
                            if domain not in {"spy", "vix"} and state != "ready"
                        ]
                        quality_reasons = sorted(
                            set(quality.reasons).union(supporting_reasons)
                        )
                        regime_manifest = _regime_manifest(regime)
                        publication_bundle: dict[str, Any] = {}

                        def snapshot_factory(
                            session,
                            authoritative_published_at: datetime,
                        ) -> SnapshotAppendResult:
                            frozen_stages = copy.deepcopy(stages)
                            for stage in reversed(frozen_stages):
                                if stage.get("name") == "persist_snapshot":
                                    stage["state"] = "completed"
                                    stage["completed_at"] = (
                                        authoritative_published_at.isoformat()
                                    )
                                    stage["error_code"] = None
                                    break
                            authoritative_payload = copy.deepcopy(
                                dict(run_payload)
                            )
                            authoritative_payload["canonical_cycle"] = {
                                "schema_version": SCHEMA_VERSION,
                                "cycle_version": CANONICAL_CYCLE_VERSION,
                                "cycle_key": key,
                                "freeze_policy_version": (
                                    CANONICAL_FREEZE_POLICY_VERSION
                                ),
                                "scope_key": CANONICAL_SCOPE_KEY,
                                "universe_source": universe_source,
                                "universe_version_key": universe_version_key,
                                "attempt_key": attempt_key,
                                "attempt_trigger": trigger,
                                "attempt_started_at": started_at.isoformat(),
                                "recovered_from_attempt_key": (
                                    claim.attempt.recovered_from_attempt_key
                                ),
                                "market_date_et": (
                                    window.market_date_et.isoformat()
                                ),
                                "previous_session": _iso(
                                    window.previous_session
                                ),
                                "regular_open_at": _iso(
                                    window.regular_open_at
                                ),
                                "window_start_at": _iso(
                                    window.window_start_at
                                ),
                                "window_end_at": _iso(
                                    window.window_end_at
                                ),
                                "latest_start_at": _iso(latest_start),
                                "started_at": started_at.isoformat(),
                                "cycle_as_of": cycle_as_of.isoformat(),
                                "published_at": (
                                    authoritative_published_at.isoformat()
                                ),
                                "quality": quality.state,
                                "validation_eligible": (
                                    quality.state == "ready"
                                ),
                                "learning_eligible": quality.state == "ready",
                                "quality_reasons": quality_reasons,
                                "regime_quality": dict(
                                    quality.regime_domain_states
                                ),
                                "regime_generated_at": _iso(
                                    regime.get("generated_at")
                                ),
                                "regime_manifest": regime_manifest,
                                "regime_manifest_sha256": canonical_sha256(
                                    regime_manifest
                                ),
                                "fresh_candidate_count": (
                                    quality.fresh_candidate_count
                                ),
                                "unavailable_candidate_count": (
                                    quality.unavailable_candidate_count
                                ),
                                "universe_sha256": canonical_sha256(
                                    list(normalized_symbols)
                                ),
                                "signal_version": SIGNAL_VERSION,
                                "stages": frozen_stages,
                            }

                            def append_prepared_snapshot(
                                run_input,
                                candidate_inputs,
                            ) -> SnapshotAppendResult:
                                appended = append_snapshot(
                                    run_input,
                                    candidate_inputs,
                                    db_manager=db_manager,
                                    session=session,
                                )
                                publication_bundle["append_result"] = appended
                                return appended

                            snapshot_value = freeze_daily_snapshot(
                                authoritative_payload,
                                db_manager=db_manager,
                                frozen_at=authoritative_published_at,
                                history_loader=history_loader,
                                freeze_policy_version=(
                                    CANONICAL_FREEZE_POLICY_VERSION
                                ),
                                scope_key=CANONICAL_SCOPE_KEY,
                                analysis_quality_eligible=(
                                    quality.state == "ready"
                                ),
                                analysis_quality_reasons=(
                                    ()
                                    if quality.state == "ready"
                                    else quality_reasons
                                ),
                                benchmark_context=benchmark_context,
                                snapshot_appender=append_prepared_snapshot,
                                snapshot_session=session,
                            )
                            appended = publication_bundle.get("append_result")
                            if not isinstance(appended, SnapshotAppendResult):
                                stored = get_snapshot(
                                    snapshot_value["snapshot_key"],
                                    db_manager=db_manager,
                                    session=session,
                                )
                                if stored is None:
                                    raise RuntimeError(
                                        "snapshot factory returned no row"
                                    )
                                appended = SnapshotAppendResult(
                                    snapshot_run_id=stored.id,
                                    snapshot_key=stored.snapshot_key,
                                    payload_sha256=stored.payload_sha256,
                                    candidate_count=len(stored.candidates),
                                    duplicate=True,
                                )
                            publication_bundle["payload"] = (
                                authoritative_payload
                            )
                            publication_bundle["snapshot"] = snapshot_value
                            return appended

                        from src.opportunities.qualification import (
                            CanonicalPublicationProof,
                            PublicationVerification,
                            classify_snapshot_qualification,
                        )
                        from src.opportunities.qualification_repository import (
                            append_qualification_bundle,
                            init_qualification_schema,
                        )

                        # Schema creation is deliberately outside the canonical
                        # writer transaction. The callback below performs only
                        # deterministic local classification and append-only
                        # writes, so a qualification failure rolls the snapshot
                        # and terminal published event back together.
                        init_qualification_schema(db_manager)

                        def append_publication_qualification(
                            session,
                            stored_snapshot,
                            publication_status,
                        ) -> None:
                            terminal_event = publication_status.events[-1]
                            proof = CanonicalPublicationProof(
                                snapshot_key=stored_snapshot.snapshot_key,
                                cycle_key=key,
                                attempt_key=attempt_key,
                                event_sequence=terminal_event.sequence,
                                published_at=_aware_utc(
                                    stored_snapshot.frozen_at,
                                    field_name="snapshot.frozen_at",
                                ),
                            )
                            bundle = classify_snapshot_qualification(
                                stored_snapshot,
                                publication_verification=(
                                    PublicationVerification(proof=proof)
                                ),
                            )
                            publication_bundle["qualification"] = (
                                append_qualification_bundle(
                                    bundle,
                                    db_manager=db_manager,
                                    session=session,
                                )
                            )

                        publication = publish_cycle_snapshot_atomically(
                            key,
                            attempt_key,
                            owner_key=owner_key,
                            snapshot_factory=snapshot_factory,
                            guard_clock=clock_fn,
                            stage_payload={"quality": quality.state},
                            terminal_payload={
                                "quality": quality.state,
                                "quality_reasons": quality_reasons,
                                "regime_quality": dict(
                                    quality.regime_domain_states
                                ),
                            },
                            publication_audit_factory=(
                                append_publication_qualification
                            ),
                            db_manager=db_manager,
                        )
                        terminal_persisted = True
                        persisted_at = (
                            publication.status.completed_at
                            or publication.published_at
                        )
                        stages = [
                            dict(stage)
                            for stage in publication.status.stages
                        ]
                        publish_payload = publication_bundle["payload"]
                        published_snapshot = get_snapshot(
                            publication.snapshot.snapshot_key,
                            db_manager=db_manager,
                        )
                        if published_snapshot is None:
                            raise RuntimeError(
                                "published snapshot is not readable after commit"
                            )
                        snapshot = snapshot_item(
                            published_snapshot,
                            db_manager=db_manager,
                            idempotent_replay=bool(
                                publication.snapshot.duplicate
                            ),
                            observed_at=persisted_at,
                        )
                        result = _base_response(
                            window,
                            normalized_symbols,
                            normalized_limit,
                            state="published",
                            quality=quality.state,
                            message="今日官方盘前研究已发布并保存为不可变快照。",
                            started_at=started_at.isoformat(),
                            completed_at=persisted_at.isoformat(),
                            cycle_as_of=cycle_as_of.isoformat(),
                            stages=stages,
                            regime_quality=dict(quality.regime_domain_states),
                            quality_reasons=quality_reasons,
                            run=publish_payload,
                            snapshot=snapshot,
                            idempotent_replay=bool(
                                publication.snapshot.duplicate
                            ),
                            lease_expires_at=None,
                        )
    except (
        SnapshotPublishWindowClosedError,
        PremarketPublicationDeadlineError,
    ):
        failed_at = _aware_utc(clock_fn(), field_name="clock")
        result = _base_response(
            window,
            normalized_symbols,
            normalized_limit,
            state="window_closed",
            quality="blocked",
            message="快照写入前已达到 09:20 ET 发布硬截止；未写入快照。",
            started_at=started_at.isoformat(),
            completed_at=failed_at.isoformat(),
            stages=stages,
            error_code="publish_window_closed",
        )
    except Exception as exc:  # noqa: BLE001 - return a redacted operational state
        failed_at = _aware_utc(clock_fn(), field_name="clock")
        hard_window_closed = bool(
            window.window_end_at is not None
            and failed_at >= window.window_end_at
        )
        error_code = (
            "publish_window_closed"
            if hard_window_closed
            else f"{current_stage}_failed:{type(exc).__name__}"
        )
        logger.error(
            "[premarket-cycle] failed cycle_key=%s stage=%s error_type=%s",
            key,
            current_stage,
            type(exc).__name__,
        )
        if not hard_window_closed:
            try:
                persist_stage(
                    current_stage,
                    "failed",
                    failed_at,
                    event_type="stage_finished",
                    error_code=error_code,
                )
            except PremarketAttemptLeaseLostError:
                stages.append(
                    _stage(
                        current_stage,
                        "failed",
                        started_at=failed_at,
                        completed_at=failed_at,
                        error_code=error_code,
                    )
                )
        result = _base_response(
            window,
            normalized_symbols,
            normalized_limit,
            state="window_closed" if hard_window_closed else "failed",
            quality="blocked",
            message=(
                "研究执行跨过 09:20 ET 发布硬截止；未发布快照。"
                if hard_window_closed
                else "官方盘前研究暂时失败；未发布快照。"
            ),
            started_at=started_at.isoformat(),
            completed_at=failed_at.isoformat(),
            stages=stages,
            error_code=error_code,
        )

    result.update(
        {
            "universe_source": universe_source,
            "universe_version_key": universe_version_key,
            "attempt_key": attempt_key,
            "attempt_trigger": trigger,
            "attempt_started_at": started_at.isoformat(),
            "recovered_from_attempt_key": (
                claim.attempt.recovered_from_attempt_key
            ),
            "scheduler_enabled": scheduler_enabled,
        }
    )
    if (
        not terminal_persisted
        and result.get("state")
        in {
            "published",
            "blocked",
            "failed",
            "window_closed",
        }
    ):
        try:
            terminal_at = _aware_utc(clock_fn(), field_name="clock")
            terminal_status = append_cycle_event(
                attempt_key,
                owner_key=owner_key,
                event_type="attempt_finished",
                stage=None,
                state=str(result["state"]),
                error_code=result.get("error_code"),
                payload={
                    "quality": result.get("quality"),
                    "quality_reasons": result.get("quality_reasons") or [],
                    "regime_quality": result.get("regime_quality") or {},
                    "snapshot_key": (
                        (result.get("snapshot") or {}).get("snapshot_key")
                    ),
                },
                now=terminal_at,
                db_manager=db_manager,
            )
            result["stages"] = [
                dict(stage) for stage in terminal_status.stages
            ]
            result["completed_at"] = terminal_at.isoformat()
            result["lease_expires_at"] = None
            attempt_count = count_cycle_attempts(
                key,
                db_manager=db_manager,
            )
            can_recover = (
                terminal_status.recoverable
                and attempt_count < 2
                and latest_start is not None
                and terminal_at < latest_start
            )
            result["recoverable"] = can_recover
            result["next_scheduled_at"] = (
                max(terminal_at, retry_at).isoformat()
                if can_recover
                else None
            )
        except PremarketAttemptLeaseLostError:
            result["state"] = "failed"
            result["quality"] = "blocked"
            result["error_code"] = "attempt_lease_lost"
            result["message"] = "研究租约已失效；迟到结果未获准发布。"

    with _cycle_lock:
        _remember_status(key, result)
        flight.result = copy.deepcopy(result)
        _cycle_flights.pop(key, None)
        flight.event.set()
    return copy.deepcopy(result)


def _reset_premarket_cycle_state_for_tests() -> None:
    """Clear process-local orchestration state between deterministic tests."""

    with _cycle_lock:
        for flight in _cycle_flights.values():
            flight.event.set()
        _cycle_flights.clear()
        _cycle_statuses.clear()
