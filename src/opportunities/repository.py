# -*- coding: utf-8 -*-
"""Append-only repository for frozen daily opportunity research.

The repository shares the project's SQLAlchemy engine but has no dependency
on the Journal repositories or models.  A daily slot is first-write-wins:
an exact retry is idempotent, while a different payload for the same slot is
reported as an immutable conflict.  Run and candidate rows are committed in a
single transaction.

Outcome rows follow the same rule per
``(candidate, horizon, evaluator_version, result_state)``.  A ``partial`` row
may only be appended after the target session close exists; it represents
missing entry-path/SPY attachments and is not revised repeatedly.  A later
fully sourced ``complete`` row is a separate append-only slot and readers
prefer it over ``partial``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError

from src.opportunities.models import (
    OpportunityCandidateOutcome,
    OpportunitySnapshotCandidate,
    OpportunitySnapshotRun,
)
from src.storage import Base, DatabaseManager, get_db

__all__ = [
    "SnapshotRunInput",
    "SnapshotCandidateInput",
    "CandidateOutcomeInput",
    "SnapshotAppendResult",
    "OutcomeAppendResult",
    "StoredSnapshot",
    "StoredSnapshotCandidate",
    "StoredOutcome",
    "OpportunityRepositoryError",
    "SnapshotConflictError",
    "OutcomeConflictError",
    "canonical_json",
    "canonical_sha256",
    "build_snapshot_key",
    "build_candidate_key",
    "init_opportunity_schema",
    "append_snapshot",
    "append_candidate_outcome",
    "get_snapshot",
    "get_preferred_candidate_outcome",
    "list_snapshots",
    "list_snapshot_outcomes",
    "list_candidate_outcomes",
]


_APPEND_ONLY_TABLE_NAMES = (
    "opportunity_snapshot_runs",
    "opportunity_snapshot_candidates",
    "opportunity_candidate_outcomes",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTEXT_LABELS = {
    "CONTEXT_HIT",
    "CONTEXT_MISS",
    "NEUTRAL",
    "NON_DIRECTIONAL",
    "DIRECTION_UNKNOWN",
}
_NON_DIRECTIONAL_CONTEXTS = {"mixed", "unknown"}


class OpportunityRepositoryError(ValueError):
    """Base class for deterministic persistence-contract failures."""


class SnapshotConflictError(OpportunityRepositoryError):
    """A daily slot already contains a different immutable snapshot."""


class OutcomeConflictError(OpportunityRepositoryError):
    """An outcome slot already contains a different immutable result."""


@dataclass(frozen=True)
class SnapshotRunInput:
    market_date_et: date
    run_type: str
    signal_version: str
    schema_version: str
    freeze_policy_version: str
    playbook_version: str
    scope_key: str
    universe: Sequence[str]
    requested_limit: int
    source_run_id: str
    ranking_method: str
    strategy_validation_state: str
    as_of: datetime
    payload: Mapping[str, Any]
    validation_eligible: bool = False
    eligibility_reasons: Sequence[str] = ()
    frozen_at: Optional[datetime] = None


@dataclass(frozen=True)
class SnapshotCandidateInput:
    ticker: str
    rank: int
    research_state: str
    directional_context: str
    supporting_evidence_count: int
    data_completeness_state: str
    payload: Mapping[str, Any]
    source_candidate_id: Optional[str] = None
    reference_session_date: Optional[date] = None
    reference_close: Optional[Decimal] = None
    reference_price_basis: Optional[str] = "prior_completed_close"
    reference_source: Optional[str] = None
    validation_eligible: bool = False
    eligibility_reasons: Sequence[str] = ()


@dataclass(frozen=True)
class CandidateOutcomeInput:
    horizon_sessions: int
    evaluator_version: str
    result_state: str
    context_label: str
    reference_session_date: Optional[date] = None
    reference_close: Optional[Decimal] = None
    refetched_reference_close: Optional[Decimal] = None
    entry_session_date: Optional[date] = None
    entry_open: Optional[Decimal] = None
    target_session_date: Optional[date] = None
    target_close_at: Optional[datetime] = None
    window_start_date: Optional[date] = None
    window_end_date: Optional[date] = None
    end_close: Optional[Decimal] = None
    max_high: Optional[Decimal] = None
    min_low: Optional[Decimal] = None
    close_return_pct: Optional[Decimal] = None
    entry_proxy_return_pct: Optional[Decimal] = None
    max_high_return_pct: Optional[Decimal] = None
    min_low_return_pct: Optional[Decimal] = None
    signed_close_return_pct: Optional[Decimal] = None
    signed_entry_return_pct: Optional[Decimal] = None
    mfe_pct: Optional[Decimal] = None
    mae_pct: Optional[Decimal] = None
    benchmark_ticker: str = "SPY"
    spy_reference_close: Optional[Decimal] = None
    spy_refetched_reference_close: Optional[Decimal] = None
    spy_entry_open: Optional[Decimal] = None
    spy_end_close: Optional[Decimal] = None
    spy_close_return_pct: Optional[Decimal] = None
    spy_entry_proxy_return_pct: Optional[Decimal] = None
    excess_close_return_pct: Optional[Decimal] = None
    excess_entry_proxy_return_pct: Optional[Decimal] = None
    signed_excess_spy_pct: Optional[Decimal] = None
    input_bars_sha256: Optional[str] = None
    benchmark_bars_sha256: Optional[str] = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    evaluated_at: Optional[datetime] = None


@dataclass(frozen=True)
class SnapshotAppendResult:
    snapshot_run_id: int
    snapshot_key: str
    payload_sha256: str
    candidate_count: int
    duplicate: bool


@dataclass(frozen=True)
class OutcomeAppendResult:
    outcome_id: int
    candidate_key: str
    horizon_sessions: int
    evaluator_version: str
    result_state: str
    result_sha256: str
    duplicate: bool


@dataclass(frozen=True)
class StoredSnapshotCandidate:
    id: int
    candidate_key: str
    ticker: str
    rank: int
    research_state: str
    directional_context: str
    supporting_evidence_count: int
    data_completeness_state: str
    reference_session_date: Optional[date]
    reference_close: Optional[Decimal]
    reference_price_basis: Optional[str]
    reference_source: Optional[str]
    validation_eligible: bool
    eligibility_reasons: tuple[str, ...]
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class StoredSnapshot:
    id: int
    snapshot_key: str
    market_date_et: date
    run_type: str
    scope_key: str
    signal_version: str
    schema_version: str
    freeze_policy_version: str
    playbook_version: str
    source_run_id: str
    requested_limit: int
    ranking_method: str
    strategy_validation_state: str
    as_of: datetime
    frozen_at: datetime
    validation_eligible: bool
    eligibility_reasons: tuple[str, ...]
    payload_sha256: str
    payload: Mapping[str, Any]
    candidates: tuple[StoredSnapshotCandidate, ...]


@dataclass(frozen=True)
class StoredOutcome:
    id: int
    snapshot_key: str
    market_date_et: date
    candidate_key: str
    ticker: str
    rank: int
    directional_context: str
    horizon_sessions: int
    evaluator_version: str
    result_state: str
    context_label: str
    result_sha256: str
    evaluated_at: datetime
    result: Mapping[str, Any]


@dataclass(frozen=True)
class _PreparedCandidate:
    candidate_key: str
    source_candidate_id: Optional[str]
    ticker: str
    rank: int
    research_state: str
    directional_context: str
    supporting_evidence_count: int
    data_completeness_state: str
    reference_session_date: Optional[date]
    reference_close: Optional[Decimal]
    reference_price_basis: Optional[str]
    reference_source: Optional[str]
    validation_eligible: bool
    eligibility_reasons_json: str
    payload_sha256: str
    payload_json: str


@dataclass(frozen=True)
class _PreparedOutcome:
    values: Mapping[str, Any]
    result_json: str
    result_sha256: str


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("canonical datetime must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize canonical, finite JSON suitable for immutable content hashes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OpportunityRepositoryError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise OpportunityRepositoryError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        raise OpportunityRepositoryError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _database_utc(value: datetime) -> datetime:
    """Restore UTC tzinfo that SQLite's DateTime adapter does not retain."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decimal_or_none(
    value: Any,
    field_name: str,
    *,
    positive: bool = False,
    scale: Optional[int] = None,
) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OpportunityRepositoryError(f"{field_name} must be numeric") from exc
    if not result.is_finite():
        raise OpportunityRepositoryError(f"{field_name} must be finite")
    if positive and result <= 0:
        raise OpportunityRepositoryError(f"{field_name} must be positive")
    if scale is not None:
        try:
            result = result.quantize(Decimal(1).scaleb(-scale))
        except InvalidOperation as exc:
            raise OpportunityRepositoryError(
                f"{field_name} exceeds supported numeric precision"
            ) from exc
    return result


def _optional_sha256(value: Any, field_name: str) -> Optional[str]:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text.lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise OpportunityRepositoryError(
            f"{field_name} must be a 64-character SHA256"
        )
    return normalized


def _normalize_symbol(value: Any, field_name: str = "ticker") -> str:
    symbol = _required_text(value, field_name).upper()
    if symbol.startswith("US."):
        symbol = symbol[3:]
    if len(symbol) > 32:
        raise OpportunityRepositoryError(f"{field_name} is too long")
    return symbol


def _normalize_universe(values: Sequence[str]) -> tuple[str, ...]:
    normalized = sorted({_normalize_symbol(value, "universe symbol") for value in values})
    if not normalized:
        raise OpportunityRepositoryError("universe must not be empty")
    return tuple(normalized)


def build_snapshot_key(run: SnapshotRunInput) -> str:
    """Return the stable daily-slot key, excluding volatile fetch timestamps."""

    universe = _normalize_universe(run.universe)
    slot = {
        "market_date_et": run.market_date_et,
        "run_type": _required_text(run.run_type, "run_type"),
        "signal_version": _required_text(run.signal_version, "signal_version"),
        "freeze_policy_version": _required_text(
            run.freeze_policy_version, "freeze_policy_version"
        ),
        "playbook_version": _required_text(run.playbook_version, "playbook_version"),
        "scope_key": _required_text(run.scope_key, "scope_key"),
        "universe": universe,
        "requested_limit": int(run.requested_limit),
    }
    return f"ops_{canonical_sha256(slot)}"


def build_candidate_key(snapshot_key: str, ticker: str) -> str:
    normalized = _normalize_symbol(ticker)
    digest = hashlib.sha256(
        f"{_required_text(snapshot_key, 'snapshot_key')}|{normalized}".encode("utf-8")
    ).hexdigest()
    return f"opc_{digest}"


def init_opportunity_schema(
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    """Create only the three opportunity tables and install immutability triggers."""

    db = db_manager or get_db()
    tables = (
        OpportunitySnapshotRun.__table__,
        OpportunitySnapshotCandidate.__table__,
        OpportunityCandidateOutcome.__table__,
    )
    Base.metadata.create_all(db._engine, tables=tables)
    if db._engine.dialect.name == "sqlite":
        with db._engine.begin() as connection:
            for table_name in _APPEND_ONLY_TABLE_NAMES:
                for operation in ("UPDATE", "DELETE"):
                    trigger_name = (
                        f"trg_{table_name}_{operation.lower()}_immutable"
                    )
                    connection.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                        f"BEFORE {operation} ON {table_name} "
                        "BEGIN SELECT RAISE(ABORT, "
                        "'opportunity rows are append-only'); END"
                    )


def _prepare_candidates(
    snapshot_key: str,
    run: SnapshotRunInput,
    candidates: Sequence[SnapshotCandidateInput],
) -> tuple[_PreparedCandidate, ...]:
    if len(candidates) > int(run.requested_limit):
        raise OpportunityRepositoryError(
            "candidate count exceeds requested_limit"
        )
    expected_ranks = list(range(1, len(candidates) + 1))
    actual_ranks = sorted(int(candidate.rank) for candidate in candidates)
    if actual_ranks != expected_ranks:
        raise OpportunityRepositoryError(
            "candidate ranks must be unique and contiguous from 1"
        )

    universe = set(_normalize_universe(run.universe))
    prepared: list[_PreparedCandidate] = []
    seen_tickers: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: int(item.rank)):
        ticker = _normalize_symbol(candidate.ticker)
        if ticker not in universe:
            raise OpportunityRepositoryError(
                f"candidate {ticker} is not present in the frozen universe"
            )
        if ticker in seen_tickers:
            raise OpportunityRepositoryError(f"duplicate candidate ticker: {ticker}")
        seen_tickers.add(ticker)

        evidence_count = int(candidate.supporting_evidence_count)
        if evidence_count < 0:
            raise OpportunityRepositoryError(
                "supporting_evidence_count must be non-negative"
            )
        reference_close = _decimal_or_none(
            candidate.reference_close,
            "reference_close",
            positive=True,
            scale=10,
        )
        payload_json = canonical_json(candidate.payload)
        prepared.append(
            _PreparedCandidate(
                candidate_key=build_candidate_key(snapshot_key, ticker),
                source_candidate_id=_optional_text(candidate.source_candidate_id),
                ticker=ticker,
                rank=int(candidate.rank),
                research_state=_required_text(
                    candidate.research_state, "research_state"
                ),
                directional_context=_required_text(
                    candidate.directional_context, "directional_context"
                ).lower(),
                supporting_evidence_count=evidence_count,
                data_completeness_state=_required_text(
                    candidate.data_completeness_state,
                    "data_completeness_state",
                ),
                reference_session_date=candidate.reference_session_date,
                reference_close=reference_close,
                reference_price_basis=_optional_text(
                    candidate.reference_price_basis
                ),
                reference_source=_optional_text(candidate.reference_source),
                validation_eligible=bool(candidate.validation_eligible),
                eligibility_reasons_json=canonical_json(
                    list(candidate.eligibility_reasons)
                ),
                payload_sha256=hashlib.sha256(
                    payload_json.encode("utf-8")
                ).hexdigest(),
                payload_json=payload_json,
            )
        )
    return tuple(prepared)


def _candidate_signature_from_prepared(
    candidate: _PreparedCandidate,
) -> Mapping[str, Any]:
    return {
        "candidate_key": candidate.candidate_key,
        "source_candidate_id": candidate.source_candidate_id,
        "ticker": candidate.ticker,
        "rank": candidate.rank,
        "research_state": candidate.research_state,
        "directional_context": candidate.directional_context,
        "supporting_evidence_count": candidate.supporting_evidence_count,
        "data_completeness_state": candidate.data_completeness_state,
        "reference_session_date": candidate.reference_session_date,
        "reference_close": candidate.reference_close,
        "reference_price_basis": candidate.reference_price_basis,
        "reference_source": candidate.reference_source,
        "validation_eligible": candidate.validation_eligible,
        "eligibility_reasons_json": candidate.eligibility_reasons_json,
        "payload_sha256": candidate.payload_sha256,
    }


def _candidate_signature_from_row(
    candidate: OpportunitySnapshotCandidate,
) -> Mapping[str, Any]:
    return {
        "candidate_key": candidate.candidate_key,
        "source_candidate_id": candidate.source_candidate_id,
        "ticker": candidate.ticker,
        "rank": int(candidate.rank),
        "research_state": candidate.research_state,
        "directional_context": candidate.directional_context,
        "supporting_evidence_count": int(candidate.supporting_evidence_count),
        "data_completeness_state": candidate.data_completeness_state,
        "reference_session_date": candidate.reference_session_date,
        "reference_close": (
            Decimal(candidate.reference_close)
            if candidate.reference_close is not None
            else None
        ),
        "reference_price_basis": candidate.reference_price_basis,
        "reference_source": candidate.reference_source,
        "validation_eligible": bool(candidate.validation_eligible),
        "eligibility_reasons_json": candidate.eligibility_reasons_json,
        "payload_sha256": candidate.payload_sha256,
    }


def _resolve_existing_snapshot(
    session,
    existing: OpportunitySnapshotRun,
    *,
    payload_sha256: str,
    candidates: Sequence[_PreparedCandidate],
) -> SnapshotAppendResult:
    if existing.payload_sha256 != payload_sha256:
        raise SnapshotConflictError(
            "daily snapshot slot already contains a different immutable payload "
            f"(snapshot_key={existing.snapshot_key}, "
            f"existing={existing.payload_sha256}, requested={payload_sha256})"
        )
    stored_candidates = (
        session.execute(
            select(OpportunitySnapshotCandidate)
            .where(OpportunitySnapshotCandidate.snapshot_run_id == existing.id)
            .order_by(OpportunitySnapshotCandidate.rank)
        )
        .scalars()
        .all()
    )
    stored_signature = canonical_json(
        [_candidate_signature_from_row(item) for item in stored_candidates]
    )
    requested_signature = canonical_json(
        [_candidate_signature_from_prepared(item) for item in candidates]
    )
    if stored_signature != requested_signature:
        raise SnapshotConflictError(
            "daily snapshot payload matches but its immutable candidate bundle differs "
            f"(snapshot_key={existing.snapshot_key})"
        )
    return SnapshotAppendResult(
        snapshot_run_id=int(existing.id),
        snapshot_key=str(existing.snapshot_key),
        payload_sha256=str(existing.payload_sha256),
        candidate_count=len(stored_candidates),
        duplicate=True,
    )


def append_snapshot(
    run: SnapshotRunInput,
    candidates: Sequence[SnapshotCandidateInput],
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> SnapshotAppendResult:
    """Atomically append one run and all candidates, or return an exact retry."""

    db = db_manager or get_db()
    init_opportunity_schema(db)
    if not isinstance(run.market_date_et, date):
        raise OpportunityRepositoryError("market_date_et must be a date")
    requested_limit = int(run.requested_limit)
    if requested_limit <= 0:
        raise OpportunityRepositoryError("requested_limit must be positive")

    universe = _normalize_universe(run.universe)
    universe_json = canonical_json(list(universe))
    universe_sha256 = hashlib.sha256(universe_json.encode("utf-8")).hexdigest()
    snapshot_key = build_snapshot_key(run)
    payload_json = canonical_json(run.payload)
    payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    as_of = _aware_utc(run.as_of, "as_of")
    frozen_at = _aware_utc(
        run.frozen_at or datetime.now(timezone.utc), "frozen_at"
    )
    prepared_candidates = _prepare_candidates(
        snapshot_key,
        run,
        tuple(candidates),
    )

    session = db.get_session()
    try:
        existing = session.execute(
            select(OpportunitySnapshotRun).where(
                OpportunitySnapshotRun.snapshot_key == snapshot_key
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _resolve_existing_snapshot(
                session,
                existing,
                payload_sha256=payload_sha256,
                candidates=prepared_candidates,
            )

        row = OpportunitySnapshotRun(
            snapshot_key=snapshot_key,
            market_date_et=run.market_date_et,
            run_type=_required_text(run.run_type, "run_type"),
            signal_version=_required_text(run.signal_version, "signal_version"),
            schema_version=_required_text(run.schema_version, "schema_version"),
            freeze_policy_version=_required_text(
                run.freeze_policy_version, "freeze_policy_version"
            ),
            playbook_version=_required_text(
                run.playbook_version, "playbook_version"
            ),
            scope_key=_required_text(run.scope_key, "scope_key"),
            universe_sha256=universe_sha256,
            universe_json=universe_json,
            requested_limit=requested_limit,
            source_run_id=_required_text(run.source_run_id, "source_run_id"),
            ranking_method=_required_text(run.ranking_method, "ranking_method"),
            strategy_validation_state=_required_text(
                run.strategy_validation_state,
                "strategy_validation_state",
            ),
            as_of=as_of,
            frozen_at=frozen_at,
            validation_eligible=bool(run.validation_eligible),
            eligibility_reasons_json=canonical_json(
                list(run.eligibility_reasons)
            ),
            payload_sha256=payload_sha256,
            payload_json=payload_json,
        )
        session.add(row)
        session.flush()
        session.add_all(
            [
                OpportunitySnapshotCandidate(
                    snapshot_run_id=row.id,
                    **candidate.__dict__,
                )
                for candidate in prepared_candidates
            ]
        )
        session.commit()
        return SnapshotAppendResult(
            snapshot_run_id=int(row.id),
            snapshot_key=snapshot_key,
            payload_sha256=payload_sha256,
            candidate_count=len(prepared_candidates),
            duplicate=False,
        )
    except IntegrityError:
        # A concurrent first writer may have won the unique daily slot.
        session.rollback()
        existing = session.execute(
            select(OpportunitySnapshotRun).where(
                OpportunitySnapshotRun.snapshot_key == snapshot_key
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return _resolve_existing_snapshot(
            session,
            existing,
            payload_sha256=payload_sha256,
            candidates=prepared_candidates,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _prepare_outcome(
    candidate: OpportunitySnapshotCandidate,
    candidate_key: str,
    outcome: CandidateOutcomeInput,
) -> _PreparedOutcome:
    horizon = int(outcome.horizon_sessions)
    if horizon not in {5, 20}:
        raise OpportunityRepositoryError("horizon_sessions must be 5 or 20")
    evaluator_version = _required_text(
        outcome.evaluator_version, "evaluator_version"
    )
    result_state = _required_text(outcome.result_state, "result_state").lower()
    if result_state not in {"complete", "partial"}:
        raise OpportunityRepositoryError(
            "result_state must be complete or partial"
        )
    context_label = _required_text(outcome.context_label, "context_label").upper()
    if context_label not in _CONTEXT_LABELS:
        raise OpportunityRepositoryError(f"unsupported context_label: {context_label}")
    if outcome.window_end_date is None or outcome.end_close is None:
        raise OpportunityRepositoryError(
            "target session window_end_date and end_close are required; "
            "do not persist a pending outcome"
        )
    if (
        outcome.window_start_date is not None
        and outcome.window_end_date < outcome.window_start_date
    ):
        raise OpportunityRepositoryError(
            "window_end_date must not precede window_start_date"
        )
    if (
        outcome.target_session_date is not None
        and outcome.target_session_date != outcome.window_end_date
    ):
        raise OpportunityRepositoryError(
            "target_session_date must equal window_end_date"
        )

    price_fields = {
        "reference_close": outcome.reference_close,
        "refetched_reference_close": outcome.refetched_reference_close,
        "entry_open": outcome.entry_open,
        "end_close": outcome.end_close,
        "max_high": outcome.max_high,
        "min_low": outcome.min_low,
        "spy_reference_close": outcome.spy_reference_close,
        "spy_refetched_reference_close": outcome.spy_refetched_reference_close,
        "spy_entry_open": outcome.spy_entry_open,
        "spy_end_close": outcome.spy_end_close,
    }
    numeric: dict[str, Optional[Decimal]] = {
        name: _decimal_or_none(value, name, positive=True, scale=10)
        for name, value in price_fields.items()
    }
    percent_fields = {
        "close_return_pct": outcome.close_return_pct,
        "entry_proxy_return_pct": outcome.entry_proxy_return_pct,
        "max_high_return_pct": outcome.max_high_return_pct,
        "min_low_return_pct": outcome.min_low_return_pct,
        "signed_close_return_pct": outcome.signed_close_return_pct,
        "signed_entry_return_pct": outcome.signed_entry_return_pct,
        "mfe_pct": outcome.mfe_pct,
        "mae_pct": outcome.mae_pct,
        "spy_close_return_pct": outcome.spy_close_return_pct,
        "spy_entry_proxy_return_pct": outcome.spy_entry_proxy_return_pct,
        "excess_close_return_pct": outcome.excess_close_return_pct,
        "excess_entry_proxy_return_pct": outcome.excess_entry_proxy_return_pct,
        "signed_excess_spy_pct": outcome.signed_excess_spy_pct,
    }
    numeric.update(
        {
            name: _decimal_or_none(value, name, scale=8)
            for name, value in percent_fields.items()
        }
    )

    direction = str(candidate.directional_context or "").strip().lower()
    directional_fields = (
        "signed_close_return_pct",
        "signed_entry_return_pct",
        "mfe_pct",
        "mae_pct",
        "signed_excess_spy_pct",
    )
    if direction in _NON_DIRECTIONAL_CONTEXTS and any(
        numeric[name] is not None for name in directional_fields
    ):
        raise OpportunityRepositoryError(
            "mixed/unknown candidates must keep signed returns, MFE, and MAE null"
        )

    candidate_reference_close = (
        Decimal(candidate.reference_close)
        if candidate.reference_close is not None
        else None
    )
    if (
        outcome.reference_session_date is not None
        and candidate.reference_session_date is not None
        and outcome.reference_session_date != candidate.reference_session_date
    ):
        raise OpportunityRepositoryError(
            "outcome reference_session_date differs from frozen candidate"
        )
    if (
        numeric["reference_close"] is not None
        and candidate_reference_close is not None
        and numeric["reference_close"] != candidate_reference_close
    ):
        raise OpportunityRepositoryError(
            "outcome reference_close differs from frozen candidate"
        )

    benchmark_ticker = _normalize_symbol(
        outcome.benchmark_ticker, "benchmark_ticker"
    )
    if benchmark_ticker != "SPY":
        raise OpportunityRepositoryError(
            "the v1 outcome contract requires benchmark_ticker=SPY"
        )
    input_bars_sha256 = _optional_sha256(
        outcome.input_bars_sha256, "input_bars_sha256"
    )
    benchmark_bars_sha256 = _optional_sha256(
        outcome.benchmark_bars_sha256, "benchmark_bars_sha256"
    )
    evaluated_at = _aware_utc(
        outcome.evaluated_at or datetime.now(timezone.utc),
        "evaluated_at",
    )
    provenance_json = canonical_json(outcome.provenance)
    target_close_at = (
        _aware_utc(outcome.target_close_at, "target_close_at")
        if outcome.target_close_at is not None
        else None
    )
    values: dict[str, Any] = {
        "horizon_sessions": horizon,
        "evaluator_version": evaluator_version,
        "result_state": result_state,
        "context_label": context_label,
        "reference_session_date": outcome.reference_session_date,
        "entry_session_date": outcome.entry_session_date,
        "target_session_date": outcome.target_session_date,
        "target_close_at": target_close_at,
        "window_start_date": outcome.window_start_date,
        "window_end_date": outcome.window_end_date,
        "benchmark_ticker": benchmark_ticker,
        "input_bars_sha256": input_bars_sha256,
        "benchmark_bars_sha256": benchmark_bars_sha256,
        "provenance_json": provenance_json,
        "evaluated_at": evaluated_at,
        **numeric,
    }
    result_payload = {
        "candidate_key": candidate_key,
        **values,
        "provenance": outcome.provenance,
    }
    # ``provenance_json`` is an implementation column; avoid duplicating its
    # serialized representation inside the canonical semantic result.
    result_payload.pop("provenance_json")
    # ``evaluated_at`` records when the immutable insert completed; it is not
    # part of the economic result and must not turn an otherwise exact retry
    # into a conflict when the caller lets the repository supply the clock.
    result_payload.pop("evaluated_at")
    result_json = canonical_json(result_payload)
    return _PreparedOutcome(
        values=values,
        result_json=result_json,
        result_sha256=hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
    )


def _resolve_existing_outcome(
    existing: OpportunityCandidateOutcome,
    *,
    candidate_key: str,
    prepared: _PreparedOutcome,
) -> OutcomeAppendResult:
    if existing.result_sha256 != prepared.result_sha256:
        raise OutcomeConflictError(
            "outcome slot already contains a different immutable result "
            f"(candidate_key={candidate_key}, horizon={existing.horizon_sessions}, "
            f"state={existing.result_state})"
        )
    return OutcomeAppendResult(
        outcome_id=int(existing.id),
        candidate_key=candidate_key,
        horizon_sessions=int(existing.horizon_sessions),
        evaluator_version=str(existing.evaluator_version),
        result_state=str(existing.result_state),
        result_sha256=str(existing.result_sha256),
        duplicate=True,
    )


def append_candidate_outcome(
    candidate_key: str,
    outcome: CandidateOutcomeInput,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> OutcomeAppendResult:
    """Append one final/partial target-session result without calculating it."""

    db = db_manager or get_db()
    init_opportunity_schema(db)
    normalized_key = _required_text(candidate_key, "candidate_key")
    session = db.get_session()
    try:
        candidate = session.execute(
            select(OpportunitySnapshotCandidate).where(
                OpportunitySnapshotCandidate.candidate_key == normalized_key
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise OpportunityRepositoryError(
                f"unknown frozen candidate: {normalized_key}"
            )
        prepared = _prepare_outcome(candidate, normalized_key, outcome)
        values = prepared.values
        existing = session.execute(
            select(OpportunityCandidateOutcome).where(
                OpportunityCandidateOutcome.snapshot_candidate_id == candidate.id,
                OpportunityCandidateOutcome.horizon_sessions
                == values["horizon_sessions"],
                OpportunityCandidateOutcome.evaluator_version
                == values["evaluator_version"],
                OpportunityCandidateOutcome.result_state == values["result_state"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _resolve_existing_outcome(
                existing,
                candidate_key=normalized_key,
                prepared=prepared,
            )

        row = OpportunityCandidateOutcome(
            snapshot_candidate_id=candidate.id,
            **values,
            result_sha256=prepared.result_sha256,
            result_json=prepared.result_json,
        )
        session.add(row)
        session.commit()
        return OutcomeAppendResult(
            outcome_id=int(row.id),
            candidate_key=normalized_key,
            horizon_sessions=int(row.horizon_sessions),
            evaluator_version=str(row.evaluator_version),
            result_state=str(row.result_state),
            result_sha256=str(row.result_sha256),
            duplicate=False,
        )
    except IntegrityError:
        session.rollback()
        candidate = session.execute(
            select(OpportunitySnapshotCandidate).where(
                OpportunitySnapshotCandidate.candidate_key == normalized_key
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise
        prepared = _prepare_outcome(candidate, normalized_key, outcome)
        values = prepared.values
        existing = session.execute(
            select(OpportunityCandidateOutcome).where(
                OpportunityCandidateOutcome.snapshot_candidate_id == candidate.id,
                OpportunityCandidateOutcome.horizon_sessions
                == values["horizon_sessions"],
                OpportunityCandidateOutcome.evaluator_version
                == values["evaluator_version"],
                OpportunityCandidateOutcome.result_state == values["result_state"],
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return _resolve_existing_outcome(
            existing,
            candidate_key=normalized_key,
            prepared=prepared,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _stored_snapshot(
    run: OpportunitySnapshotRun,
    candidates: Sequence[OpportunitySnapshotCandidate],
) -> StoredSnapshot:
    return StoredSnapshot(
        id=int(run.id),
        snapshot_key=str(run.snapshot_key),
        market_date_et=run.market_date_et,
        run_type=str(run.run_type),
        scope_key=str(run.scope_key),
        signal_version=str(run.signal_version),
        schema_version=str(run.schema_version),
        freeze_policy_version=str(run.freeze_policy_version),
        playbook_version=str(run.playbook_version),
        source_run_id=str(run.source_run_id),
        requested_limit=int(run.requested_limit),
        ranking_method=str(run.ranking_method),
        strategy_validation_state=str(run.strategy_validation_state),
        as_of=_database_utc(run.as_of),
        frozen_at=_database_utc(run.frozen_at),
        validation_eligible=bool(run.validation_eligible),
        eligibility_reasons=tuple(json.loads(run.eligibility_reasons_json)),
        payload_sha256=str(run.payload_sha256),
        payload=json.loads(run.payload_json),
        candidates=tuple(
            StoredSnapshotCandidate(
                id=int(candidate.id),
                candidate_key=str(candidate.candidate_key),
                ticker=str(candidate.ticker),
                rank=int(candidate.rank),
                research_state=str(candidate.research_state),
                directional_context=str(candidate.directional_context),
                supporting_evidence_count=int(
                    candidate.supporting_evidence_count
                ),
                data_completeness_state=str(
                    candidate.data_completeness_state
                ),
                reference_session_date=candidate.reference_session_date,
                reference_close=(
                    Decimal(candidate.reference_close)
                    if candidate.reference_close is not None
                    else None
                ),
                reference_price_basis=candidate.reference_price_basis,
                reference_source=candidate.reference_source,
                validation_eligible=bool(candidate.validation_eligible),
                eligibility_reasons=tuple(
                    json.loads(candidate.eligibility_reasons_json)
                ),
                payload=json.loads(candidate.payload_json),
            )
            for candidate in candidates
        ),
    )


def get_snapshot(
    snapshot_key: str,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[StoredSnapshot]:
    db = db_manager or get_db()
    init_opportunity_schema(db)
    session = db.get_session()
    try:
        run = session.execute(
            select(OpportunitySnapshotRun).where(
                OpportunitySnapshotRun.snapshot_key == snapshot_key
            )
        ).scalar_one_or_none()
        if run is None:
            return None
        candidates = (
            session.execute(
                select(OpportunitySnapshotCandidate)
                .where(OpportunitySnapshotCandidate.snapshot_run_id == run.id)
                .order_by(OpportunitySnapshotCandidate.rank)
            )
            .scalars()
            .all()
        )
        return _stored_snapshot(run, candidates)
    finally:
        session.close()


def list_snapshots(
    *,
    limit: int = 30,
    db_manager: Optional[DatabaseManager] = None,
) -> tuple[StoredSnapshot, ...]:
    """Return recent frozen snapshots with their candidate bundles."""

    normalized_limit = int(limit)
    if not 1 <= normalized_limit <= 500:
        raise OpportunityRepositoryError("limit must be between 1 and 500")
    db = db_manager or get_db()
    init_opportunity_schema(db)
    session = db.get_session()
    try:
        runs = (
            session.execute(
                select(OpportunitySnapshotRun)
                .order_by(
                    OpportunitySnapshotRun.frozen_at.desc(),
                    OpportunitySnapshotRun.id.desc(),
                )
                .limit(normalized_limit)
            )
            .scalars()
            .all()
        )
        if not runs:
            return ()
        run_ids = [int(run.id) for run in runs]
        candidate_rows = (
            session.execute(
                select(OpportunitySnapshotCandidate)
                .where(OpportunitySnapshotCandidate.snapshot_run_id.in_(run_ids))
                .order_by(
                    OpportunitySnapshotCandidate.snapshot_run_id,
                    OpportunitySnapshotCandidate.rank,
                )
            )
            .scalars()
            .all()
        )
        by_run: dict[int, list[OpportunitySnapshotCandidate]] = {
            run_id: [] for run_id in run_ids
        }
        for candidate in candidate_rows:
            by_run[int(candidate.snapshot_run_id)].append(candidate)
        return tuple(
            _stored_snapshot(run, by_run[int(run.id)])
            for run in runs
        )
    finally:
        session.close()


def _stored_outcome_from_joined(
    outcome: OpportunityCandidateOutcome,
    candidate: OpportunitySnapshotCandidate,
    run: OpportunitySnapshotRun,
) -> StoredOutcome:
    return StoredOutcome(
        id=int(outcome.id),
        snapshot_key=str(run.snapshot_key),
        market_date_et=run.market_date_et,
        candidate_key=str(candidate.candidate_key),
        ticker=str(candidate.ticker),
        rank=int(candidate.rank),
        directional_context=str(candidate.directional_context),
        horizon_sessions=int(outcome.horizon_sessions),
        evaluator_version=str(outcome.evaluator_version),
        result_state=str(outcome.result_state),
        context_label=str(outcome.context_label),
        result_sha256=str(outcome.result_sha256),
        evaluated_at=_database_utc(outcome.evaluated_at),
        result=json.loads(outcome.result_json),
    )


def _preferred_joined_outcomes(rows) -> tuple[StoredOutcome, ...]:
    """Collapse partial/complete pairs, retaining the ordered complete row."""

    results: list[StoredOutcome] = []
    seen: set[tuple[int, int, str]] = set()
    for outcome, candidate, run in rows:
        slot = (
            int(candidate.id),
            int(outcome.horizon_sessions),
            str(outcome.evaluator_version),
        )
        if slot in seen:
            continue
        seen.add(slot)
        results.append(_stored_outcome_from_joined(outcome, candidate, run))
    return tuple(results)


def _outcome_join_query(*, evaluator_version: Optional[str] = None):
    query = (
        select(
            OpportunityCandidateOutcome,
            OpportunitySnapshotCandidate,
            OpportunitySnapshotRun,
        )
        .join(
            OpportunitySnapshotCandidate,
            OpportunitySnapshotCandidate.id
            == OpportunityCandidateOutcome.snapshot_candidate_id,
        )
        .join(
            OpportunitySnapshotRun,
            OpportunitySnapshotRun.id
            == OpportunitySnapshotCandidate.snapshot_run_id,
        )
    )
    if evaluator_version is not None:
        query = query.where(
            OpportunityCandidateOutcome.evaluator_version
            == _required_text(evaluator_version, "evaluator_version")
        )
    return query


def list_snapshot_outcomes(
    snapshot_key: str,
    evaluator_version: Optional[str] = None,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> tuple[StoredOutcome, ...]:
    """List preferred outcomes for every candidate in one frozen snapshot."""

    db = db_manager or get_db()
    init_opportunity_schema(db)
    session = db.get_session()
    try:
        query = _outcome_join_query(evaluator_version=evaluator_version).where(
            OpportunitySnapshotRun.snapshot_key
            == _required_text(snapshot_key, "snapshot_key")
        )
        rows = session.execute(
            query.order_by(
                OpportunitySnapshotCandidate.rank,
                OpportunityCandidateOutcome.horizon_sessions,
                OpportunityCandidateOutcome.evaluator_version,
                case(
                    (OpportunityCandidateOutcome.result_state == "complete", 0),
                    else_=1,
                ),
                OpportunityCandidateOutcome.id.desc(),
            )
        ).all()
        return _preferred_joined_outcomes(rows)
    finally:
        session.close()


def list_candidate_outcomes(
    evaluator_version: Optional[str] = None,
    *,
    db_manager: Optional[DatabaseManager] = None,
) -> tuple[StoredOutcome, ...]:
    """List preferred outcomes across snapshots for learning summaries."""

    db = db_manager or get_db()
    init_opportunity_schema(db)
    session = db.get_session()
    try:
        rows = session.execute(
            _outcome_join_query(evaluator_version=evaluator_version).order_by(
                OpportunitySnapshotRun.market_date_et.desc(),
                OpportunitySnapshotRun.frozen_at.desc(),
                OpportunitySnapshotCandidate.rank,
                OpportunityCandidateOutcome.horizon_sessions,
                OpportunityCandidateOutcome.evaluator_version,
                case(
                    (OpportunityCandidateOutcome.result_state == "complete", 0),
                    else_=1,
                ),
                OpportunityCandidateOutcome.id.desc(),
            )
        ).all()
        return _preferred_joined_outcomes(rows)
    finally:
        session.close()


def get_preferred_candidate_outcome(
    candidate_key: str,
    *,
    horizon_sessions: int,
    evaluator_version: str,
    db_manager: Optional[DatabaseManager] = None,
) -> Optional[StoredOutcome]:
    """Return complete before partial for one immutable evaluation slot."""

    db = db_manager or get_db()
    init_opportunity_schema(db)
    session = db.get_session()
    try:
        row = session.execute(
            _outcome_join_query(evaluator_version=evaluator_version)
            .where(
                OpportunitySnapshotCandidate.candidate_key == candidate_key,
                OpportunityCandidateOutcome.horizon_sessions
                == int(horizon_sessions),
            )
            .order_by(
                case(
                    (OpportunityCandidateOutcome.result_state == "complete", 0),
                    else_=1,
                ),
                OpportunityCandidateOutcome.id.desc(),
            )
            .limit(1)
        ).first()
        if row is None:
            return None
        outcome, candidate, run = row
        return _stored_outcome_from_joined(outcome, candidate, run)
    finally:
        session.close()
