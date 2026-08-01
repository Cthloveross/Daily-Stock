# -*- coding: utf-8 -*-
"""Freeze daily opportunity research and observe no-lookahead outcomes.

This service is deliberately separate from the Journal ledger.  It persists
research candidates and subsequent underlying-price paths only; it never
infers an option fill, trade decision, broker P&L, or ranking-weight update.
"""
from __future__ import annotations

import copy
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Sequence

from src.opportunities.outcomes import (
    FORMULA_VERSION,
    STATE_COMPLETE,
    STATE_DATA_GAP,
    STATE_INELIGIBLE,
    STATE_PARTIAL,
    STATE_PENDING,
    evaluate_opportunity_outcomes,
)
from src.opportunities.repository import (
    CandidateOutcomeInput,
    SnapshotAppendResult,
    SnapshotCandidateInput,
    SnapshotRunInput,
    StoredOutcome,
    StoredSnapshot,
    append_candidate_outcome,
    append_snapshot,
    build_snapshot_key,
    canonical_sha256,
    get_snapshot,
    list_candidate_outcomes,
    list_snapshot_outcomes,
    list_snapshots,
)

logger = logging.getLogger(__name__)

FREEZE_POLICY_VERSION = "premarket_prior_close_xnys_v1"
PLAYBOOK_VERSION = "unverified"
SCOPE_KEY = "web_daily_opportunity"
SNAPSHOT_SCHEMA_VERSION = "opportunity-snapshot/1.0"
MINIMUM_SUMMARY_SAMPLES = 20
MINIMUM_INVESTIGATION_SAMPLES = 20
_HORIZONS = (5, 20)
_MAX_FETCH_WORKERS = 4

HistoryLoader = Callable[[str], tuple[Sequence[Mapping[str, Any]], Optional[str]]]
SnapshotAppender = Callable[
    [SnapshotRunInput, Sequence[SnapshotCandidateInput]],
    SnapshotAppendResult,
]


class OpportunitySnapshotNotFoundError(LookupError):
    """Raised when an explicit immutable snapshot key does not exist."""


class SnapshotPublishWindowClosedError(RuntimeError):
    """Raised when canonical publication reaches its hard deadline."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _aware_utc(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime") from exc
    return _aware_utc(parsed)


def _parse_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc


def _finite_positive(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return Decimal(str(result))


def _candidate_reference(candidate: Mapping[str, Any]) -> tuple[Optional[date], Optional[float]]:
    raw_date = candidate.get("reference_session_date")
    if raw_date is None:
        raw_date = candidate.get("last_completed_bar_at")
    reference_date: Optional[date]
    try:
        reference_date = _parse_date(raw_date, "reference_session_date")
    except ValueError:
        reference_date = None

    reference_close = _finite_positive(candidate.get("reference_close"))
    if reference_close is None:
        for evidence in candidate.get("evidence") or ():
            if evidence.get("metric") == "last_completed_close":
                reference_close = _finite_positive(evidence.get("value"))
                break
    return reference_date, reference_close


def _direction(value: Any) -> str:
    return {
        "bullish": "LONG",
        "bearish": "SHORT",
        "mixed": "MIXED",
        "unknown": "UNKNOWN",
    }.get(str(value or "").strip().lower(), "UNKNOWN")


def _base_run_input(
    run_payload: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    frozen_at: datetime,
    validation_eligible: bool,
    eligibility_reasons: Sequence[str],
    freeze_policy_version: str = FREEZE_POLICY_VERSION,
    scope_key: str = SCOPE_KEY,
) -> SnapshotRunInput:
    return SnapshotRunInput(
        market_date_et=_parse_date(run_payload.get("market_date_et"), "market_date_et"),
        run_type=str(run_payload.get("run_type") or ""),
        signal_version=str(run_payload.get("signal_version") or ""),
        schema_version=str(run_payload.get("schema_version") or ""),
        freeze_policy_version=freeze_policy_version,
        playbook_version=PLAYBOOK_VERSION,
        scope_key=scope_key,
        universe=tuple(str(item) for item in (run_payload.get("universe") or ())),
        requested_limit=int(run_payload.get("requested_limit") or 0),
        source_run_id=str(run_payload.get("run_id") or ""),
        ranking_method=str(run_payload.get("ranking_method") or ""),
        strategy_validation_state=str(
            run_payload.get("strategy_validation_state") or ""
        ),
        as_of=_parse_datetime(run_payload.get("as_of"), "as_of"),
        frozen_at=frozen_at,
        validation_eligible=validation_eligible,
        eligibility_reasons=tuple(eligibility_reasons),
        payload=payload,
    )


def _default_history_loader(
    symbol: str,
    *,
    manager,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    days: int = 180,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    from src.services.stock_service import StockService

    frame, source = manager.get_daily_data(
        symbol,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
        days=days,
    )
    rows = (
        [StockService._row_to_kline(row, intraday=False) for _, row in frame.iterrows()]
        if frame is not None and not frame.empty
        else []
    )
    return rows, source


def _load_benchmark_anchors(
    reference_dates: Sequence[date],
    *,
    frozen_at: datetime,
    history_loader: Optional[HistoryLoader],
) -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    if not reference_dates:
        return {}, None
    manager = None
    try:
        if history_loader is None:
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager()
            rows, source = _default_history_loader("SPY", manager=manager, days=120)
        else:
            rows, source = history_loader("SPY")
        indexed = {
            str(row.get("date"))[:10]: row
            for row in rows
            if isinstance(row, Mapping) and row.get("date") is not None
        }
        anchors: dict[str, dict[str, Any]] = {}
        for session in sorted(set(reference_dates)):
            close = _finite_positive((indexed.get(session.isoformat()) or {}).get("close"))
            if close is not None:
                anchors[session.isoformat()] = {
                    "ticker": "SPY",
                    "reference_session_date": session.isoformat(),
                    "reference_close": close,
                    "source": source,
                    "fetched_at": frozen_at.isoformat(),
                }
        return anchors, None if anchors else "SPY reference close unavailable"
    except Exception as exc:  # noqa: BLE001 - benchmark absence is explicit partial context
        logger.debug(
            "[opportunity-snapshot] SPY anchor unavailable error_type=%s",
            type(exc).__name__,
        )
        return {}, f"provider_error:{type(exc).__name__}"
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as exc:  # noqa: BLE001 - best-effort resource cleanup
                logger.debug(
                    "[opportunity-snapshot] manager close failed error_type=%s",
                    type(exc).__name__,
                )


def prepare_snapshot_benchmark_context(
    run_payload: Mapping[str, Any],
    *,
    fetched_at: datetime,
    history_loader: Optional[HistoryLoader] = None,
) -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    """Prefetch benchmark anchors before the final local persistence section."""

    reference_dates: list[date] = []
    for raw in run_payload.get("candidates") or ():
        reference_date, _reference_close = _candidate_reference(dict(raw))
        if reference_date is not None:
            reference_dates.append(reference_date)
    return _load_benchmark_anchors(
        reference_dates,
        frozen_at=_aware_utc(fetched_at),
        history_loader=history_loader,
    )


def _candidate_eligibility(
    candidate: Mapping[str, Any],
    *,
    frozen_at: datetime,
    reference_date: Optional[date],
    reference_close: Optional[float],
) -> tuple[bool, list[str], Optional[dict[str, Any]]]:
    reasons: list[str] = []
    if reference_date is None:
        reasons.append("missing_reference_session")
    if reference_close is None:
        reasons.append("missing_reference_close")
    if not candidate.get("source"):
        reasons.append("missing_reference_source")
    if candidate.get("research_state") == "blocked":
        reasons.append("candidate_blocked")
    if reasons or reference_date is None or reference_close is None:
        return False, reasons, None

    try:
        contract = evaluate_opportunity_outcomes(
            signal_session=reference_date,
            generated_at=frozen_at,
            reference_close=reference_close,
            direction=_direction(candidate.get("directional_context")),
            stock_bars=(),
            spy_bars=(),
            now=frozen_at,
        )
    except Exception as exc:  # noqa: BLE001 - calendar must fail closed
        return False, [f"calendar_contract_unavailable:{type(exc).__name__}"], None
    if not contract["eligible"]:
        reasons.append(str(contract.get("ineligibility_reason") or "ineligible"))
    return bool(contract["eligible"]), reasons, contract.get("schedule")


def freeze_daily_snapshot(
    run_payload: Mapping[str, Any],
    *,
    db_manager=None,
    frozen_at: Optional[datetime] = None,
    history_loader: Optional[HistoryLoader] = None,
    freeze_policy_version: str = FREEZE_POLICY_VERSION,
    scope_key: str = SCOPE_KEY,
    analysis_quality_eligible: bool = True,
    analysis_quality_reasons: Sequence[str] = (),
    benchmark_context: Optional[
        tuple[Mapping[str, Mapping[str, Any]], Optional[str]]
    ] = None,
    publish_deadline: Optional[datetime] = None,
    publish_guard_clock: Optional[Callable[[], datetime]] = None,
    snapshot_appender: Optional[SnapshotAppender] = None,
    snapshot_session=None,
) -> dict[str, Any]:
    """Append the first immutable daily slot; later calls reuse that slot."""

    frozen_utc = _aware_utc(frozen_at or datetime.now(timezone.utc))
    provisional = _base_run_input(
        run_payload,
        payload=run_payload,
        frozen_at=frozen_utc,
        validation_eligible=False,
        eligibility_reasons=(),
        freeze_policy_version=freeze_policy_version,
        scope_key=scope_key,
    )
    snapshot_key = build_snapshot_key(provisional)
    existing = get_snapshot(
        snapshot_key,
        db_manager=db_manager,
        session=snapshot_session,
    )
    if existing is not None:
        return snapshot_item(
            existing,
            db_manager=db_manager,
            idempotent_replay=True,
            snapshot_session=snapshot_session,
        )

    prepared: list[dict[str, Any]] = []
    reference_dates: list[date] = []
    for rank, raw in enumerate(run_payload.get("candidates") or (), start=1):
        candidate = copy.deepcopy(dict(raw))
        reference_date, reference_close = _candidate_reference(candidate)
        eligible, reasons, schedule = _candidate_eligibility(
            candidate,
            frozen_at=frozen_utc,
            reference_date=reference_date,
            reference_close=reference_close,
        )
        if reference_date is not None:
            reference_dates.append(reference_date)
        prepared.append(
            {
                "rank": rank,
                "candidate": candidate,
                "reference_date": reference_date,
                "reference_close": reference_close,
                "eligible": eligible,
                "reasons": reasons,
                "schedule": schedule,
            }
        )

    if benchmark_context is None:
        benchmark_anchors, benchmark_error = _load_benchmark_anchors(
            reference_dates,
            frozen_at=frozen_utc,
            history_loader=history_loader,
        )
    else:
        supplied_anchors, benchmark_error = benchmark_context
        benchmark_anchors = copy.deepcopy(dict(supplied_anchors))
    candidate_inputs: list[SnapshotCandidateInput] = []
    normalized_quality_reasons = sorted(
        {
            str(reason).strip()
            for reason in analysis_quality_reasons
            if str(reason).strip()
        }
    )
    if not analysis_quality_eligible and not normalized_quality_reasons:
        normalized_quality_reasons = ["analysis_quality_not_eligible"]
    for item in prepared:
        candidate = item["candidate"]
        reference_date = item["reference_date"]
        anchor = benchmark_anchors.get(reference_date.isoformat()) if reference_date else None
        effective_reasons = sorted(
            set(item["reasons"]).union(
                () if analysis_quality_eligible else normalized_quality_reasons
            )
        )
        effective_eligible = bool(
            item["eligible"] and analysis_quality_eligible
        )
        candidate["outcome_validation"] = {
            "freeze_policy_version": freeze_policy_version,
            "frozen_at": frozen_utc.isoformat(),
            "eligible": effective_eligible,
            "learning_eligible": effective_eligible,
            "eligibility_reasons": effective_reasons,
            "analysis_quality_eligible": bool(analysis_quality_eligible),
            "analysis_quality_reasons": normalized_quality_reasons,
            "schedule": item["schedule"],
            "benchmark_anchor": anchor,
            "benchmark_error": benchmark_error if anchor is None else None,
            "underlying_only": True,
        }
        completeness = candidate.get("data_completeness") or {}
        candidate_inputs.append(
            SnapshotCandidateInput(
                ticker=str(candidate.get("ticker") or ""),
                rank=int(item["rank"]),
                research_state=str(candidate.get("research_state") or "blocked"),
                directional_context=str(
                    candidate.get("directional_context") or "unknown"
                ),
                supporting_evidence_count=int(
                    candidate.get("supporting_evidence_count") or 0
                ),
                data_completeness_state=str(
                    completeness.get("state") or "insufficient"
                ),
                payload=candidate,
                source_candidate_id=candidate.get("candidate_id"),
                reference_session_date=reference_date,
                reference_close=_decimal(item["reference_close"]),
                reference_price_basis=str(
                    candidate.get("reference_price_basis")
                    or "prior_completed_close"
                ),
                reference_source=candidate.get("source"),
                validation_eligible=effective_eligible,
                eligibility_reasons=tuple(effective_reasons),
            )
        )

    eligible_count = sum(
        1
        for item in prepared
        if item["eligible"] and analysis_quality_eligible
    )
    run_reasons = [] if eligible_count else sorted(
        {
            reason
            for item in prepared
            for reason in (
                list(item["reasons"])
                + (
                    []
                    if analysis_quality_eligible
                    else normalized_quality_reasons
                )
            )
        }
    )
    if not prepared:
        run_reasons.append("no_candidates")
    payload = copy.deepcopy(dict(run_payload))
    payload["snapshot_meta"] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "freeze_policy_version": freeze_policy_version,
        "playbook_version": PLAYBOOK_VERSION,
        "scope_key": scope_key,
        "frozen_at": frozen_utc.isoformat(),
        "validation_eligible": eligible_count > 0,
        "learning_eligible": eligible_count > 0,
        "eligible_candidate_count": eligible_count,
        "eligibility_reasons": run_reasons,
        "analysis_quality_eligible": bool(analysis_quality_eligible),
        "analysis_quality_reasons": normalized_quality_reasons,
        "benchmark_anchors": benchmark_anchors,
        "benchmark_error": benchmark_error,
        "underlying_only": True,
    }
    run_input = _base_run_input(
        run_payload,
        payload=payload,
        frozen_at=frozen_utc,
        validation_eligible=eligible_count > 0,
        eligibility_reasons=run_reasons,
        freeze_policy_version=freeze_policy_version,
        scope_key=scope_key,
    )
    if publish_deadline is not None:
        deadline_utc = _aware_utc(publish_deadline)
        guard_clock = publish_guard_clock or (
            lambda: datetime.now(timezone.utc)
        )
        if _aware_utc(guard_clock()) >= deadline_utc:
            raise SnapshotPublishWindowClosedError(
                "snapshot publication reached its hard deadline"
            )
    appended = (
        snapshot_appender(run_input, tuple(candidate_inputs))
        if snapshot_appender is not None
        else append_snapshot(
            run_input,
            tuple(candidate_inputs),
            db_manager=db_manager,
        )
    )
    stored = get_snapshot(
        appended.snapshot_key,
        db_manager=db_manager,
        session=snapshot_session,
    )
    if stored is None:  # pragma: no cover - repository transaction invariant
        raise RuntimeError("snapshot append completed without a readable row")
    logger.info(
        "[opportunity-snapshot] frozen key=%s candidates=%s eligible=%s duplicate=%s",
        stored.snapshot_key,
        len(stored.candidates),
        eligible_count,
        appended.duplicate,
    )
    return snapshot_item(
        stored,
        db_manager=db_manager,
        idempotent_replay=appended.duplicate,
        snapshot_session=snapshot_session,
    )


def ensure_daily_snapshot(
    run_payload: Mapping[str, Any],
    *,
    db_manager=None,
    frozen_at: Optional[datetime] = None,
    history_loader: Optional[HistoryLoader] = None,
) -> dict[str, Any]:
    """Save one official research snapshot only in its XNYS pre-open window.

    The visible opportunity list may be refreshed at any time.  Persistence is
    different: it is allowed only when at least one candidate's next XNYS entry
    session equals the run's market date and the request is strictly before
    that session open.  This prevents weekend duplicates and after-the-open
    hindsight samples while making normal page loads safely idempotent.
    """

    frozen_utc = _aware_utc(frozen_at or datetime.now(timezone.utc))
    market_date = _parse_date(run_payload.get("market_date_et"), "market_date_et")
    provisional = _base_run_input(
        run_payload,
        payload=run_payload,
        frozen_at=frozen_utc,
        validation_eligible=False,
        eligibility_reasons=(),
    )
    existing = get_snapshot(build_snapshot_key(provisional), db_manager=db_manager)
    if existing is not None:
        item = snapshot_item(existing, db_manager=db_manager, idempotent_replay=True)
        return {
            "schema_version": "opportunity-snapshot-ensure/1.0",
            "state": "existing",
            "market_date_et": market_date.isoformat(),
            "snapshot": item,
            "message": "今日研究版本已经保存；页面继续刷新不会重复写入。",
        }

    official_window = False
    for raw in run_payload.get("candidates") or ():
        candidate = dict(raw)
        reference_date, reference_close = _candidate_reference(candidate)
        eligible, _reasons, schedule = _candidate_eligibility(
            candidate,
            frozen_at=frozen_utc,
            reference_date=reference_date,
            reference_close=reference_close,
        )
        entry_session = (schedule or {}).get("entry_session")
        if eligible and entry_session == market_date.isoformat():
            official_window = True
            break

    if not official_window:
        return {
            "schema_version": "opportunity-snapshot-ensure/1.0",
            "state": "outside_window",
            "market_date_et": market_date.isoformat(),
            "snapshot": None,
            "message": (
                "今日清单会正常更新；当前不在对应 XNYS 交易日的盘前保存窗口，"
                "因此不会生成事后样本。"
            ),
        }

    item = freeze_daily_snapshot(
        run_payload,
        db_manager=db_manager,
        frozen_at=frozen_utc,
        history_loader=history_loader,
    )
    return {
        "schema_version": "opportunity-snapshot-ensure/1.0",
        "state": "existing" if item.get("idempotent_replay") else "saved",
        "market_date_et": market_date.isoformat(),
        "snapshot": item,
        "message": (
            "今日盘前研究版本已自动保存，后续将按 5 / 20 个交易日跟踪结果。"
        ),
    }


def snapshot_item(
    snapshot: StoredSnapshot,
    *,
    db_manager=None,
    idempotent_replay: bool = False,
    observed_at: Optional[datetime] = None,
    snapshot_session=None,
) -> dict[str, Any]:
    outcomes = list_snapshot_outcomes(
        snapshot.snapshot_key,
        evaluator_version=FORMULA_VERSION,
        db_manager=db_manager,
        session=snapshot_session,
    )
    eligible_keys = {
        candidate.candidate_key
        for candidate in snapshot.candidates
        if candidate.validation_eligible
    }
    underlying_path_keys = _qualified_track_candidate_keys(
        snapshot,
        track_key="raw_underlying_path_v1",
        db_manager=db_manager,
        require_prospective=True,
    )
    full_research_keys = _qualified_track_candidate_keys(
        snapshot,
        track_key="canonical_full_research_v1",
        db_manager=db_manager,
        require_prospective=True,
    )
    candidate_by_key = {
        candidate.candidate_key: candidate for candidate in snapshot.candidates
    }
    now = _aware_utc(observed_at or datetime.now(timezone.utc))
    progress = _outcome_progress_for_keys(
        snapshot,
        outcomes=outcomes,
        eligible_keys=eligible_keys,
        candidate_by_key=candidate_by_key,
        observed_at=now,
    )
    underlying_path_progress = _outcome_progress_for_keys(
        snapshot,
        outcomes=outcomes,
        eligible_keys=underlying_path_keys,
        candidate_by_key=candidate_by_key,
        observed_at=now,
    )
    full_research_progress = _outcome_progress_for_keys(
        snapshot,
        outcomes=outcomes,
        eligible_keys=full_research_keys,
        candidate_by_key=candidate_by_key,
        observed_at=now,
    )
    meta = snapshot.payload.get("snapshot_meta") or {}
    qualification = _snapshot_qualification_summary(
        snapshot.snapshot_key,
        db_manager=db_manager,
    )
    analysis_quality_eligible = (
        bool(meta.get("analysis_quality_eligible"))
        if "analysis_quality_eligible" in meta
        else None
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_key": snapshot.snapshot_key,
        "market_date_et": snapshot.market_date_et.isoformat(),
        "source_run_id": str(snapshot.payload.get("run_id") or ""),
        "frozen_at": _aware_utc(snapshot.frozen_at).isoformat(),
        "signal_version": snapshot.signal_version,
        "candidate_count": len(snapshot.candidates),
        "eligible_candidate_count": len(eligible_keys),
        "validation_eligible": snapshot.validation_eligible,
        "eligibility_reasons": list(meta.get("eligibility_reasons") or ()),
        "analysis_quality_eligible": analysis_quality_eligible,
        "analysis_quality_reasons": list(
            meta.get("analysis_quality_reasons") or ()
        ),
        "qualification": qualification,
        "outcome_progress": progress,
        "underlying_path_candidate_count": len(underlying_path_keys),
        "underlying_path_progress": underlying_path_progress,
        "full_research_candidate_count": len(full_research_keys),
        "full_research_progress": full_research_progress,
        "idempotent_replay": idempotent_replay,
    }


def _outcome_progress_for_keys(
    snapshot: StoredSnapshot,
    *,
    outcomes: Sequence[StoredOutcome],
    eligible_keys: set[str],
    candidate_by_key: Mapping[str, Any],
    observed_at: datetime,
) -> list[dict[str, Any]]:
    progress = []
    for horizon in _HORIZONS:
        mature = [
            outcome
            for outcome in outcomes
            if outcome.horizon_sessions == horizon
            and outcome.candidate_key in eligible_keys
        ]
        mature_keys = {outcome.candidate_key for outcome in mature}
        pending_count = 0
        data_gap_count = 0
        for candidate_key in sorted(eligible_keys - mature_keys):
            candidate = candidate_by_key[candidate_key]
            validation = candidate.payload.get("outcome_validation") or {}
            anchor = validation.get("benchmark_anchor") or {}
            try:
                schedule_state = evaluate_opportunity_outcomes(
                    signal_session=candidate.reference_session_date,
                    generated_at=_aware_utc(snapshot.frozen_at),
                    reference_close=float(candidate.reference_close),
                    direction=_direction(candidate.directional_context),
                    stock_bars=(),
                    spy_bars=(),
                    spy_reference_close=_finite_positive(anchor.get("reference_close")),
                    now=observed_at,
                )["horizons"][f"{horizon}d"]["state"]
            except Exception:  # noqa: BLE001 - calendar failure is a visible gap
                schedule_state = STATE_DATA_GAP
            if schedule_state == STATE_PENDING:
                pending_count += 1
            else:
                data_gap_count += 1
        progress.append(
            {
                "horizon_sessions": horizon,
                "eligible_count": len(eligible_keys),
                "mature_count": len(mature),
                "pending_count": pending_count,
                "partial_count": sum(
                    1 for outcome in mature if outcome.result_state == "partial"
                ),
                "data_gap_count": data_gap_count,
            }
        )
    return progress


def _qualified_track_candidate_keys(
    snapshot: StoredSnapshot,
    *,
    track_key: str,
    db_manager=None,
    require_prospective: bool = False,
) -> set[str]:
    """Select one persisted track with track-specific legacy behavior.

    Strict selection and learning tracks never fall back to the historical
    aggregate boolean.  The raw path keeps a narrow compatibility fallback
    only for known pre-qualification v1 snapshots; canonical publications and
    repository read failures fail closed.
    """

    try:
        from src.opportunities.qualification import (
            TRACK_RAW_UNDERLYING_PATH,
        )
        from src.opportunities.qualification_repository import (
            get_snapshot_qualification,
        )

        qualification = get_snapshot_qualification(
            snapshot.snapshot_key,
            db_manager=db_manager,
            initialize=False,
        )
    except Exception as exc:  # noqa: BLE001 - qualification is an integrity gate
        logger.debug(
            "[opportunity-qualification] track lookup unavailable "
            "error_type=%s",
            type(exc).__name__,
        )
        return set()
    if qualification is None:
        canonical_cycle = snapshot.payload.get("canonical_cycle")
        known_legacy = (
            not isinstance(canonical_cycle, Mapping)
            and (
                snapshot.scope_key == SCOPE_KEY
                or snapshot.freeze_policy_version == FREEZE_POLICY_VERSION
            )
        )
        if track_key == TRACK_RAW_UNDERLYING_PATH and known_legacy:
            return {
                candidate.candidate_key
                for candidate in snapshot.candidates
                if candidate.validation_eligible
            }
        return set()
    return {
        item.candidate_key
        for item in qualification.candidate_tracks
        if (
            item.track_key == track_key
            and item.qualification_state == "qualified"
            and (
                not require_prospective
                or item.causal_window_state == "prospective"
            )
        )
    }


def _snapshot_qualification_summary(
    snapshot_key: str,
    *,
    db_manager=None,
) -> Optional[dict[str, Any]]:
    """Project persisted policy facts without making a read endpoint write."""

    try:
        from src.opportunities.qualification import TRACK_KEYS
        from src.opportunities.qualification_repository import (
            get_snapshot_qualification,
        )

        qualification = get_snapshot_qualification(
            snapshot_key,
            db_manager=db_manager,
            initialize=False,
        )
    except Exception as exc:  # noqa: BLE001 - optional additive projection
        logger.debug(
            "[opportunity-qualification] read unavailable error_type=%s",
            type(exc).__name__,
        )
        return None
    if qualification is None:
        return None

    tracks = []
    for track_key in TRACK_KEYS:
        rows = [
            item
            for item in qualification.candidate_tracks
            if item.track_key == track_key
        ]
        tracks.append(
            {
                "track_key": track_key,
                "qualified_count": sum(
                    1 for item in rows
                    if item.qualification_state == "qualified"
                ),
                "excluded_count": sum(
                    1 for item in rows
                    if item.qualification_state == "excluded"
                ),
                "unverified_count": sum(
                    1 for item in rows
                    if item.qualification_state == "unverified"
                ),
                "prospective_count": sum(
                    1 for item in rows
                    if item.causal_window_state == "prospective"
                ),
                "retrospective_count": sum(
                    1 for item in rows
                    if item.causal_window_state == "retrospective"
                ),
                "observation_ready_count": sum(
                    1 for item in rows
                    if item.observation_state == "ready"
                ),
            }
        )
    return {
        "assessment_key": qualification.assessment_key,
        "policy_version": qualification.policy_version,
        "publication_state": qualification.publication_state,
        "analysis_quality_state": qualification.analysis_quality_state,
        "assessed_at": qualification.assessed_at.isoformat(),
        "reason_codes": list(qualification.reason_codes),
        "tracks": tracks,
    }


def list_snapshot_items(*, limit: int = 10, db_manager=None) -> dict[str, Any]:
    items = [
        snapshot_item(snapshot, db_manager=db_manager)
        for snapshot in list_snapshots(limit=limit, db_manager=db_manager)
    ]
    return {"schema_version": "opportunity-snapshot-list/1.0", "items": items}


def get_snapshot_detail(
    snapshot_key: str,
    *,
    db_manager=None,
) -> Optional[dict[str, Any]]:
    """Return one immutable snapshot summary plus its frozen run payload.

    Read-only: the frozen run payload is returned verbatim (deep copy) so the
    caller can rebuild the exact board evidence a candidate was published with.
    """

    snapshot = get_snapshot(snapshot_key, db_manager=db_manager)
    if snapshot is None:
        return None
    return {
        "schema_version": "opportunity-snapshot-detail/1.0",
        "snapshot": snapshot_item(snapshot, db_manager=db_manager),
        "run": copy.deepcopy(dict(snapshot.payload)),
    }


def _load_evaluation_histories(
    symbols: Sequence[str],
    *,
    earliest_reference: date,
    evaluated_at: datetime,
    history_loader: Optional[HistoryLoader],
) -> dict[str, tuple[list[Mapping[str, Any]], Optional[str], Optional[str]]]:
    normalized = sorted({str(symbol).strip().upper() for symbol in symbols if symbol})
    if history_loader is not None:
        result = {}
        for symbol in normalized:
            try:
                rows, source = history_loader(symbol)
                result[symbol] = (list(rows), source, None)
            except Exception as exc:  # noqa: BLE001 - per-symbol explicit gap
                result[symbol] = ([], None, f"{type(exc).__name__}: {exc}")
        return result

    from data_provider.base import DataFetcherManager

    try:
        manager = DataFetcherManager()
    except Exception as exc:  # noqa: BLE001 - provider setup is a recoverable batch gap
        message = f"{type(exc).__name__}: {exc}"
        return {symbol: ([], None, message) for symbol in normalized}
    start = earliest_reference - timedelta(days=7)
    # Most historical APIs (including yfinance) treat ``end`` as exclusive.
    # Ask through the following calendar day so a just-completed target
    # session can be returned; the pure evaluator still rejects future dates.
    end = evaluated_at.date() + timedelta(days=1)
    days = max(180, (end - start).days + 10)
    result: dict[str, tuple[list[Mapping[str, Any]], Optional[str], Optional[str]]] = {}

    def load(symbol: str):
        rows, source = _default_history_loader(
            symbol,
            manager=manager,
            start_date=start,
            end_date=end,
            days=days,
        )
        return list(rows), source, None

    try:
        with ThreadPoolExecutor(
            max_workers=min(_MAX_FETCH_WORKERS, len(normalized)),
            thread_name_prefix="opportunity-outcomes",
        ) as executor:
            futures = {executor.submit(load, symbol): symbol for symbol in normalized}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result[symbol] = future.result()
                except Exception as exc:  # noqa: BLE001 - one symbol must not fail batch
                    result[symbol] = ([], None, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            manager.close()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.debug("[opportunity-outcomes] manager close failed: %s", exc)
    return result


def _percent_change(end: Optional[float], start: Optional[float]) -> Optional[float]:
    if end is None or start is None or start <= 0:
        return None
    return ((end / start) - 1.0) * 100.0


def _bar_date(value: Any) -> Optional[date]:
    try:
        return _parse_date(value, "bar date")
    except ValueError:
        return None


def _slice_audit_bars(
    bars: Sequence[Mapping[str, Any]],
    *,
    start: date,
    end: date,
) -> list[Mapping[str, Any]]:
    """Hash only the economic window, never bars observed after the target."""

    return [
        row
        for row in bars
        if isinstance(row, Mapping)
        and (session := _bar_date(row.get("date", row.get("session")))) is not None
        and start <= session <= end
    ]


def _outcome_input(
    candidate,
    result: Mapping[str, Any],
    horizon: Mapping[str, Any],
    *,
    stock_bars: Sequence[Mapping[str, Any]],
    stock_source: Optional[str],
    spy_bars: Sequence[Mapping[str, Any]],
    spy_source: Optional[str],
    evaluated_at: datetime,
) -> CandidateOutcomeInput:
    entry_open = _finite_positive(horizon.get("entry_open"))
    end_close = _finite_positive(horizon.get("end_close"))
    reference_close = _finite_positive(horizon.get("reference_close"))
    max_high = _finite_positive(horizon.get("max_high"))
    min_low = _finite_positive(horizon.get("min_low"))
    spy_reference = _finite_positive(horizon.get("spy_reference_close"))
    spy_entry = _finite_positive(horizon.get("spy_entry_open"))
    spy_end = _finite_positive(horizon.get("spy_end_close"))
    raw_entry = horizon.get("raw_entry_return_pct")
    spy_entry_return = _percent_change(spy_end, spy_entry)
    entry_session = _parse_date(horizon.get("entry_session"), "entry_session")
    target_session = _parse_date(horizon.get("target_session"), "target_session")
    reference_session = candidate.reference_session_date
    stock_audit_bars = _slice_audit_bars(
        stock_bars,
        start=reference_session,
        end=target_session,
    )
    spy_audit_bars = _slice_audit_bars(
        spy_bars,
        start=reference_session,
        end=target_session,
    )
    return CandidateOutcomeInput(
        horizon_sessions=int(horizon["horizon_sessions"]),
        evaluator_version=FORMULA_VERSION,
        result_state=str(horizon["state"]).lower(),
        context_label=str(horizon["label"]),
        reference_session_date=candidate.reference_session_date,
        reference_close=_decimal(reference_close),
        refetched_reference_close=_decimal(
            horizon.get("refetched_reference_close")
        ),
        entry_session_date=entry_session,
        entry_open=_decimal(entry_open),
        target_session_date=target_session,
        target_close_at=_parse_datetime(
            horizon.get("target_close_at"), "target_close_at"
        ),
        window_start_date=entry_session,
        window_end_date=target_session,
        end_close=_decimal(end_close),
        max_high=_decimal(max_high),
        min_low=_decimal(min_low),
        close_return_pct=_decimal(horizon.get("raw_close_return_pct")),
        entry_proxy_return_pct=_decimal(raw_entry),
        max_high_return_pct=_decimal(_percent_change(max_high, reference_close)),
        min_low_return_pct=_decimal(_percent_change(min_low, reference_close)),
        signed_close_return_pct=_decimal(horizon.get("signed_close_return_pct")),
        signed_entry_return_pct=_decimal(horizon.get("signed_entry_return_pct")),
        mfe_pct=_decimal(horizon.get("mfe_pct")),
        mae_pct=_decimal(horizon.get("mae_pct")),
        spy_reference_close=_decimal(spy_reference),
        spy_refetched_reference_close=_decimal(
            horizon.get("spy_refetched_reference_close")
        ),
        spy_entry_open=_decimal(spy_entry),
        spy_end_close=_decimal(spy_end),
        spy_close_return_pct=_decimal(horizon.get("spy_close_return_pct")),
        spy_entry_proxy_return_pct=_decimal(spy_entry_return),
        excess_close_return_pct=_decimal(horizon.get("raw_excess_spy_pct")),
        excess_entry_proxy_return_pct=_decimal(
            (float(raw_entry) - spy_entry_return)
            if raw_entry is not None and spy_entry_return is not None
            else None
        ),
        signed_excess_spy_pct=_decimal(horizon.get("signed_excess_spy_pct")),
        input_bars_sha256=canonical_sha256(stock_audit_bars),
        benchmark_bars_sha256=(
            canonical_sha256(spy_audit_bars) if spy_audit_bars else None
        ),
        provenance={
            "formula_version": FORMULA_VERSION,
            "schedule": result.get("schedule"),
            "horizon": dict(horizon),
            "stock_source": stock_source,
            "spy_source": spy_source,
            "underlying_only": True,
            "not_option_pnl": True,
            "not_trade_fill": True,
        },
        evaluated_at=evaluated_at,
    )


def evaluate_snapshot(
    snapshot_key: str,
    *,
    db_manager=None,
    evaluated_at: Optional[datetime] = None,
    history_loader: Optional[HistoryLoader] = None,
) -> dict[str, Any]:
    """Evaluate due candidate horizons; pending/data-gap states are never stored."""

    snapshot = get_snapshot(snapshot_key, db_manager=db_manager)
    if snapshot is None:
        raise OpportunitySnapshotNotFoundError(snapshot_key)
    now = _aware_utc(evaluated_at or datetime.now(timezone.utc))
    tracking_keys = _qualified_track_candidate_keys(
        snapshot,
        track_key="raw_underlying_path_v1",
        db_manager=db_manager,
        require_prospective=True,
    )
    eligible = [
        candidate
        for candidate in snapshot.candidates
        if candidate.candidate_key in tracking_keys
    ]
    existing = list_snapshot_outcomes(
        snapshot_key,
        evaluator_version=FORMULA_VERSION,
        db_manager=db_manager,
    )
    existing_map = {
        (outcome.candidate_key, outcome.horizon_sessions): outcome
        for outcome in existing
    }
    needing_data = [
        candidate
        for candidate in eligible
        if any(
            existing_map.get((candidate.candidate_key, horizon)) is None
            or existing_map[(candidate.candidate_key, horizon)].result_state != "complete"
            for horizon in _HORIZONS
        )
    ]
    if needing_data:
        earliest = min(
            candidate.reference_session_date
            for candidate in needing_data
            if candidate.reference_session_date is not None
        )
        histories = _load_evaluation_histories(
            [*(candidate.ticker for candidate in needing_data), "SPY"],
            earliest_reference=earliest,
            evaluated_at=now,
            history_loader=history_loader,
        )
    else:
        histories = {}

    inserted = 0
    already_recorded = 0
    pending = 0
    data_gaps = 0
    spy_bars, spy_source, _ = histories.get("SPY", ([], None, None))
    for candidate in eligible:
        missing_slots = [
            horizon
            for horizon in _HORIZONS
            if existing_map.get((candidate.candidate_key, horizon)) is None
            or existing_map[(candidate.candidate_key, horizon)].result_state != "complete"
        ]
        if not missing_slots:
            already_recorded += len(_HORIZONS)
            continue
        stock_bars, stock_source, stock_error = histories.get(
            candidate.ticker,
            ([], None, "history not requested"),
        )
        stored_source = str(candidate.payload.get("source") or "")
        source_mismatch = bool(
            stored_source
            and stock_source
            and stored_source.strip().lower() != str(stock_source).strip().lower()
        )
        stock_continuity_error = bool(
            stock_error or not stored_source or not stock_source or source_mismatch
        )
        validation = candidate.payload.get("outcome_validation") or {}
        benchmark_anchor = validation.get("benchmark_anchor") or {}
        spy_reference = _finite_positive(benchmark_anchor.get("reference_close"))
        frozen_spy_source = str(benchmark_anchor.get("source") or "")
        spy_continuity_error = bool(
            spy_reference is not None
            and (
                not frozen_spy_source
                or not spy_source
                or frozen_spy_source.strip().lower()
                != str(spy_source).strip().lower()
            )
        )
        usable_stock_bars = [] if stock_continuity_error else stock_bars
        usable_spy_bars = [] if spy_continuity_error else spy_bars
        try:
            result = evaluate_opportunity_outcomes(
                signal_session=candidate.reference_session_date,
                generated_at=_aware_utc(snapshot.frozen_at),
                reference_close=float(candidate.reference_close),
                direction=_direction(candidate.directional_context),
                stock_bars=usable_stock_bars,
                spy_bars=usable_spy_bars,
                spy_reference_close=spy_reference,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one malformed candidate
            logger.debug(
                "[opportunity-outcomes] candidate=%s evaluation unavailable: %s",
                candidate.candidate_key,
                exc,
            )
            data_gaps += len(missing_slots)
            continue
        for horizon_sessions in _HORIZONS:
            current = existing_map.get((candidate.candidate_key, horizon_sessions))
            horizon = result["horizons"][f"{horizon_sessions}d"]
            state = horizon["state"]
            if current is not None and current.result_state == "complete":
                already_recorded += 1
                continue
            if state == STATE_PENDING:
                pending += 1
                continue
            if state in {STATE_DATA_GAP, STATE_INELIGIBLE}:
                data_gaps += 1
                continue
            if state not in {STATE_COMPLETE, STATE_PARTIAL} or horizon.get("end_close") is None:
                data_gaps += 1
                continue
            if current is not None and current.result_state == "partial" and state == STATE_PARTIAL:
                already_recorded += 1
                continue
            try:
                appended = append_candidate_outcome(
                    candidate.candidate_key,
                    _outcome_input(
                        candidate,
                        result,
                        horizon,
                        stock_bars=usable_stock_bars,
                        stock_source=stock_source,
                        spy_bars=usable_spy_bars,
                        spy_source=spy_source,
                        evaluated_at=now,
                    ),
                    db_manager=db_manager,
                )
            except Exception as exc:  # noqa: BLE001 - preserve other candidates
                logger.debug(
                    "[opportunity-outcomes] candidate=%s horizon=%s append unavailable: %s",
                    candidate.candidate_key,
                    horizon_sessions,
                    exc,
                )
                data_gaps += 1
                continue
            if appended.duplicate:
                already_recorded += 1
            else:
                inserted += 1

    item = snapshot_item(snapshot, db_manager=db_manager, observed_at=now)
    message = (
        f"新增 {inserted} 个成熟结果；{pending} 个仍待目标交易日，"
        f"{data_gaps} 个存在数据缺口。"
    )
    logger.info(
        "[opportunity-outcomes] snapshot=%s inserted=%s pending=%s gaps=%s",
        snapshot.snapshot_key,
        inserted,
        pending,
        data_gaps,
    )
    return {
        "schema_version": "opportunity-outcome-evaluation/1.0",
        "snapshot_key": snapshot.snapshot_key,
        "evaluated_at": now.isoformat(),
        "candidate_count": len(snapshot.candidates),
        "tracking_candidate_count": len(eligible),
        "inserted_outcomes": inserted,
        "already_recorded": already_recorded,
        "pending_horizons": pending,
        "data_gap_horizons": data_gaps,
        "outcome_progress": item["outcome_progress"],
        "underlying_path_progress": item["underlying_path_progress"],
        "full_research_progress": item["full_research_progress"],
        "message": message,
    }


def _snapshot_has_due_outcome_work(item: Mapping[str, Any]) -> bool:
    """Use the local XNYS schedule projection before touching a provider."""

    return any(
        int(progress.get("data_gap_count") or 0) > 0
        or int(progress.get("partial_count") or 0) > 0
        for progress in (
            item.get("underlying_path_progress")
            or item.get("outcome_progress")
            or ()
        )
        if isinstance(progress, Mapping)
    )


def evaluate_due_snapshots(
    *,
    db_manager=None,
    evaluated_at: Optional[datetime] = None,
    history_loader: Optional[HistoryLoader] = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Evaluate only snapshots with a locally proven due/partial horizon.

    The preflight calls ``snapshot_item`` with no market-data fetch.  This is
    important after a 5D result matures: the still-pending 20D horizon must not
    trigger another full history download on every daily scheduler tick.
    A request-scoped loader cache also prevents the same ticker or SPY history
    from being fetched once per snapshot when several horizons mature together.
    """

    now = _aware_utc(evaluated_at or datetime.now(timezone.utc))
    snapshots = list_snapshots(
        limit=max(1, min(500, int(limit))),
        db_manager=db_manager,
    )
    due: list[StoredSnapshot] = []
    for snapshot in snapshots:
        item = snapshot_item(
            snapshot,
            db_manager=db_manager,
            observed_at=now,
        )
        if _snapshot_has_due_outcome_work(item):
            due.append(snapshot)

    if not due:
        return {
            "schema_version": "opportunity-outcome-maintenance/1.0",
            "evaluated_at": now.isoformat(),
            "scanned_snapshot_count": len(snapshots),
            "due_snapshot_count": 0,
            "evaluated_snapshot_count": 0,
            "failed_snapshot_count": 0,
            "inserted_outcomes": 0,
            "already_recorded": 0,
            "pending_horizons": 0,
            "data_gap_horizons": 0,
            "evaluated_snapshot_keys": [],
            "failed_snapshot_keys": [],
            "message": "没有已到期且尚未完成的 5D/20D 结果。",
        }

    cache: dict[str, tuple[list[Mapping[str, Any]], Optional[str]]] = {}
    cache_errors: dict[str, Exception] = {}
    manager = None
    source_loader = history_loader
    if source_loader is None:
        from data_provider.base import DataFetcherManager

        tracking_keys_by_snapshot = {
            snapshot.snapshot_key: _qualified_track_candidate_keys(
                snapshot,
                track_key="raw_underlying_path_v1",
                db_manager=db_manager,
                require_prospective=True,
            )
            for snapshot in due
        }
        earliest_reference = min(
            candidate.reference_session_date
            for snapshot in due
            for candidate in snapshot.candidates
            if candidate.candidate_key
            in tracking_keys_by_snapshot[snapshot.snapshot_key]
            and candidate.reference_session_date is not None
        )
        start = earliest_reference - timedelta(days=7)
        end = now.date() + timedelta(days=1)
        days = max(180, (end - start).days + 10)
        manager = DataFetcherManager()

        def source_loader(symbol: str):
            return _default_history_loader(
                symbol,
                manager=manager,
                start_date=start,
                end_date=end,
                days=days,
            )

    def shared_loader(symbol: str):
        normalized = str(symbol).strip().upper()
        if normalized in cache_errors:
            raise cache_errors[normalized]
        if normalized not in cache:
            try:
                rows, source = source_loader(normalized)
                cache[normalized] = (list(rows), source)
            except Exception as exc:  # noqa: BLE001 - cache one provider failure
                cache_errors[normalized] = exc
                raise
        return cache[normalized]

    aggregate = {
        "inserted_outcomes": 0,
        "already_recorded": 0,
        "pending_horizons": 0,
        "data_gap_horizons": 0,
    }
    evaluated_keys: list[str] = []
    failed_keys: list[str] = []
    try:
        for snapshot in due:
            try:
                result = evaluate_snapshot(
                    snapshot.snapshot_key,
                    db_manager=db_manager,
                    evaluated_at=now,
                    history_loader=shared_loader,
                )
            except Exception as exc:  # noqa: BLE001 - isolate one snapshot
                logger.warning(
                    "[opportunity-outcomes] maintenance snapshot=%s failed "
                    "error_type=%s",
                    snapshot.snapshot_key,
                    type(exc).__name__,
                )
                failed_keys.append(snapshot.snapshot_key)
                continue
            evaluated_keys.append(snapshot.snapshot_key)
            for field_name in aggregate:
                aggregate[field_name] += int(result.get(field_name) or 0)
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                logger.debug(
                    "[opportunity-outcomes] maintenance manager close failed: %s",
                    exc,
                )

    message = (
        f"检查 {len(due)} 个到期快照，新增 "
        f"{aggregate['inserted_outcomes']} 条成熟结果；"
        f"{aggregate['data_gap_horizons']} 个到期窗口仍缺数据。"
    )
    return {
        "schema_version": "opportunity-outcome-maintenance/1.0",
        "evaluated_at": now.isoformat(),
        "scanned_snapshot_count": len(snapshots),
        "due_snapshot_count": len(due),
        "evaluated_snapshot_count": len(evaluated_keys),
        "failed_snapshot_count": len(failed_keys),
        **aggregate,
        "evaluated_snapshot_keys": evaluated_keys,
        "failed_snapshot_keys": failed_keys,
        "message": message,
    }


def _strategy_base_key(snapshot: StoredSnapshot) -> str:
    from src.opportunities.qualification import QUALIFICATION_POLICY_VERSION

    return canonical_sha256(
        {
            "outcome_formula_version": FORMULA_VERSION,
            "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
            "signal_version": snapshot.signal_version,
            "freeze_policy_version": snapshot.freeze_policy_version,
            "playbook_version": snapshot.playbook_version,
            "scope_key": snapshot.scope_key,
            "ranking_method": snapshot.ranking_method,
            "universe": sorted(str(item) for item in snapshot.payload.get("universe") or ()),
            "requested_limit": snapshot.requested_limit,
        }
    )


def _candidate_setup(candidate) -> tuple[str, ...]:
    structural = tuple(
        sorted(
            str(tag)
            for tag in candidate.payload.get("setup_tags") or ()
            if str(tag).startswith(
                (
                    "close_above_",
                    "close_below_",
                    "daily_close_above_",
                    "daily_close_below_",
                )
            )
        )
    )
    return structural or ("unclassified",)


def _candidate_regime(candidate) -> str:
    for evidence in candidate.payload.get("evidence") or ():
        if evidence.get("metric") != "stored_regime":
            continue
        value = evidence.get("value")
        if isinstance(value, Mapping):
            return str(value.get("label") or "unknown").strip().lower()
    return "unknown"


def _critical_quality_gap(outcome: StoredOutcome) -> bool:
    provenance = outcome.result.get("provenance") or {}
    horizon = provenance.get("horizon") or {}
    gaps = {str(item) for item in horizon.get("data_gaps") or ()}
    return bool(
        gaps
        & {
            "reference_revision_mismatch",
            "reference_close_refetch_missing",
            "stock_target_close",
        }
    )


def _cohort_records(
    outcomes: Sequence[StoredOutcome],
    snapshots: Sequence[StoredSnapshot],
    *,
    db_manager=None,
) -> tuple[
    Optional[str],
    dict[tuple[int, str], list[StoredOutcome]],
    dict[tuple[int, str], int],
    dict[str, str],
]:
    if not snapshots:
        return None, {}, {}, {}
    by_snapshot = {snapshot.snapshot_key: snapshot for snapshot in snapshots}
    full_research_keys = {
        snapshot.snapshot_key: _qualified_track_candidate_keys(
            snapshot,
            track_key="canonical_full_research_v1",
            db_manager=db_manager,
            require_prospective=True,
        )
        for snapshot in snapshots
    }
    latest = next(
        (
            snapshot
            for snapshot in snapshots
            if full_research_keys[snapshot.snapshot_key]
        ),
        snapshots[0],
    )
    active_base = _strategy_base_key(latest)
    candidates = {
        (snapshot.snapshot_key, candidate.candidate_key): candidate
        for snapshot in snapshots
        for candidate in snapshot.candidates
    }
    grouped: dict[tuple[int, str], list[StoredOutcome]] = {}
    excluded: dict[tuple[int, str], int] = {}
    labels: dict[str, str] = {}
    seen: set[tuple[str, str, int, str]] = set()
    for outcome in outcomes:
        snapshot = by_snapshot.get(outcome.snapshot_key)
        candidate = candidates.get((outcome.snapshot_key, outcome.candidate_key))
        if snapshot is None or candidate is None:
            continue
        if (
            outcome.candidate_key
            not in full_research_keys.get(snapshot.snapshot_key, set())
        ):
            continue
        if _strategy_base_key(snapshot) != active_base:
            continue
        setup = _candidate_setup(candidate)
        regime = _candidate_regime(candidate)
        direction = str(candidate.directional_context or "unknown").lower()
        cohort_key = canonical_sha256(
            {
                "strategy_base": active_base,
                "setup": setup,
                "regime": regime,
                "direction": direction,
            }
        )
        signal_session = str(outcome.result.get("reference_session_date") or "")
        identity = (outcome.ticker, signal_session, outcome.horizon_sessions, cohort_key)
        if identity in seen:
            continue
        seen.add(identity)
        group = (outcome.horizon_sessions, cohort_key)
        labels[cohort_key] = (
            f"{'+'.join(setup)} · Regime {regime} · {direction}"
        )
        if _critical_quality_gap(outcome):
            excluded[group] = excluded.get(group, 0) + 1
            continue
        grouped.setdefault(group, []).append(outcome)
    return active_base, grouped, excluded, labels


def learning_summary(*, db_manager=None, generated_at: Optional[datetime] = None) -> dict[str, Any]:
    snapshots = list_snapshots(limit=500, db_manager=db_manager)
    outcomes = list_candidate_outcomes(
        evaluator_version=FORMULA_VERSION,
        db_manager=db_manager,
    )
    _active_base, grouped, excluded, cohort_labels = _cohort_records(
        outcomes,
        snapshots,
        db_manager=db_manager,
    )
    horizons = []
    for horizon_sessions in _HORIZONS:
        cohort_candidates = [
            (cohort_key, records)
            for (horizon, cohort_key), records in grouped.items()
            if horizon == horizon_sessions
        ]
        cohort_candidates.sort(
            key=lambda item: (
                any(
                    outcome.context_label
                    in {"CONTEXT_HIT", "CONTEXT_MISS", "NEUTRAL"}
                    for outcome in item[1]
                ),
                len(item[1]),
                item[0],
            ),
            reverse=True,
        )
        cohort_key, current = cohort_candidates[0] if cohort_candidates else (None, [])
        labels = [outcome.context_label for outcome in current]
        hit = labels.count("CONTEXT_HIT")
        miss = labels.count("CONTEXT_MISS")
        neutral = labels.count("NEUTRAL")
        non_directional = labels.count("NON_DIRECTIONAL") + labels.count(
            "DIRECTION_UNKNOWN"
        )
        directional_count = hit + miss + neutral
        sessions = {
            str(outcome.result.get("reference_session_date") or "")
            for outcome in current
            if outcome.result.get("reference_session_date")
        }
        summary_visible = (
            directional_count >= MINIMUM_SUMMARY_SAMPLES
            and len(sessions) >= MINIMUM_SUMMARY_SAMPLES
        )
        investigation_ready = (
            directional_count >= MINIMUM_INVESTIGATION_SAMPLES
            and len(sessions) >= MINIMUM_INVESTIGATION_SAMPLES
        )
        horizons.append(
            {
                "horizon_sessions": horizon_sessions,
                "mature_count": len(current),
                "distinct_signal_sessions": len(sessions),
                "directional_sample_count": directional_count,
                "context_hit_count": hit if summary_visible else None,
                "context_miss_count": miss if summary_visible else None,
                "neutral_count": neutral if summary_visible else None,
                "non_directional_count": (
                    non_directional if summary_visible else None
                ),
                "context_hit_rate_percent": (
                    round(hit / directional_count * 100.0, 2)
                    if summary_visible and directional_count
                    else None
                ),
                "summary_visible": summary_visible,
                "investigation_ready": investigation_ready,
                "cohort_key": cohort_key,
                "cohort_label": cohort_labels.get(cohort_key) if cohort_key else None,
                "excluded_quality_count": (
                    excluded.get((horizon_sessions, cohort_key), 0)
                    if cohort_key
                    else sum(
                        count
                        for (horizon, _cohort), count in excluded.items()
                        if horizon == horizon_sessions
                    )
                ),
            }
        )
    if any(item["investigation_ready"] for item in horizons):
        strategy_state = "investigation_ready"
    elif any(item["summary_visible"] for item in horizons):
        strategy_state = "descriptive_summary_available"
    else:
        strategy_state = "collecting"
    return {
        "schema_version": "opportunity-learning/1.0",
        "generated_at": _aware_utc(generated_at or datetime.now(timezone.utc)).isoformat(),
        "strategy_state": strategy_state,
        "auto_adjustment": False,
        "minimum_summary_samples": MINIMUM_SUMMARY_SAMPLES,
        "minimum_investigation_samples": MINIMUM_INVESTIGATION_SAMPLES,
        "horizons": horizons,
        "limitations": [
            "这里只验证冻结候选后的标的价格路径，不是期权收益或真实成交 P&L。",
            "同一 cohort 不足 20 个方向样本或 20 个独立信号交易日时不显示命中率；5 日与 20 日绝不混算。",
            "至少 20 个方向样本且来自 20 个独立信号交易日才进入人工调查。",
            "任何样本量都不会自动修改排名权重；新版本必须另做 walk-forward 验证并由用户确认。",
            "摘要只展示当前 signal/playbook/universe 下同一 setup、Regime、方向的 cohort，不混算版本。",
            "参考价格复权或来源连续性无法核对的结果会单列并排除命中率。",
            "未记录 trade_taken=false 和预定义触发条件，因此不生成‘错过机会’标签。",
        ],
    }


__all__ = [
    "FREEZE_POLICY_VERSION",
    "MINIMUM_INVESTIGATION_SAMPLES",
    "MINIMUM_SUMMARY_SAMPLES",
    "OpportunitySnapshotNotFoundError",
    "ensure_daily_snapshot",
    "evaluate_due_snapshots",
    "evaluate_snapshot",
    "freeze_daily_snapshot",
    "learning_summary",
    "list_snapshot_items",
    "snapshot_item",
]
