# -*- coding: utf-8 -*-
"""Current-only Moomoo option-position snapshot API.

The routes in this module never place orders and never mutate historical
Journal executions or PositionEpisodes.  A confirmed snapshot is only a
future continuity candidate; it cannot prove a historical opening position.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from api.v1.endpoints.journal import (
    _episode_build_metadata,
    _episode_reconciliation,
    _episode_summary,
)
from api.v1.schemas.journal_positions import (
    ConfirmedPositionSnapshotResponse,
    FuturePositionEpisodeBuildConfirmRequest,
    FuturePositionEpisodeBuildConfirmResponse,
    FuturePositionEpisodeBuildCountsResponse,
    FuturePositionEpisodeBuildPreviewResponse,
    FuturePositionEpisodeBuildSourceResponse,
    PositionSnapshotConfirmRequest,
    PositionSnapshotConfirmResponse,
    PositionSnapshotContinuityCounts,
    PositionSnapshotContinuityReason,
    PositionSnapshotContinuityResponse,
    PositionSnapshotCostContext,
    PositionSnapshotFilterCounts,
    PositionSnapshotFreshness,
    PositionSnapshotLatestResponse,
    PositionSnapshotMember,
    PositionSnapshotObservation,
    PositionSnapshotPreviewResponse,
    PositionSnapshotStability,
)
from src.journal.brokers.moomoo_position_snapshot import (
    MoomooPositionSnapshotError,
    PositionSnapshotConfig,
    run_position_snapshot_probe,
)
from src.journal.brokers.moomoo_readonly import MoomooReadonlyError
from src.journal.ledger.position_snapshot_repository import (
    MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS,
    MAX_POSITION_SNAPSHOT_FUTURE_SKEW,
    POSITION_SNAPSHOT_TIME_SEMANTICS,
    PositionSnapshotError,
    confirm_position_snapshot_artifact,
    get_latest_position_snapshot_state,
    plan_position_snapshot,
    save_position_snapshot_artifact,
)
from src.journal.ledger.position_snapshot_continuity import (
    PositionSnapshotContinuityError,
    assess_latest_position_snapshot_continuity,
)
from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    SNAPSHOT_FENCE_SOURCE_KIND,
    get_episode_summary,
)
from src.journal.ledger.position_snapshot_episode_build import (
    append_future_position_episode_build,
)
from src.journal.ledger.position_snapshot_episode_preview import (
    FuturePositionEpisodePreviewError,
    preview_fenced_position_episodes,
)
from src.journal.ledger.refresh_repository import (
    JournalRefreshError,
    get_latest_refresh_publication,
)
from src.journal.ledger.repository import DEFAULT_LEDGER_ACCOUNT_KEY


router = APIRouter()

_ACCOUNT_KEY_QUERY = Query(
    DEFAULT_LEDGER_ACCOUNT_KEY,
    min_length=1,
    max_length=64,
    pattern=r"^[A-Za-z0-9_.:-]+$",
)
_NO_CONTINUITY_REASON = (
    "尚无已确认的 Journal 只读刷新记录；请先完成一次刷新确认，建立账户连续性后再确认当前持仓快照。"
)
_LATEST_BINDING_CHANGED_REASON = (
    "最新已存持仓快照与当前 Journal 账户绑定不同；旧快照仅作为历史记录展示，"
    "新的确认仍以当前 Journal 账户绑定为准。"
)
_LATEST_SNAPSHOT_STALE_REASON = (
    "最新已存持仓快照已超过 30 分钟，只能作为历史记录展示；请重新执行只读预览并确认。"
)
_LATEST_SNAPSHOT_FUTURE_SKEW_REASON = (
    "最新已存持仓快照的本地时钟偏差超过 2 分钟，不能视为当前证据；"
    "请校正服务器时钟后重新执行只读预览并确认。"
)


class PositionSnapshotApiError(ValueError):
    """Configuration or cross-subsystem continuity error."""


def _enabled_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _bounded_float_env(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = (os.environ.get(name) or str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise PositionSnapshotApiError(f"{name} must be a number") from exc
    if value < minimum or value > maximum:
        raise PositionSnapshotApiError(
            f"{name} must be between {minimum:g} and {maximum:g}"
        )
    return value


def _position_snapshot_enabled() -> bool:
    return _enabled_env("MOOMOO_JOURNAL_REFRESH_ENABLED")


def _position_snapshot_probe_config() -> PositionSnapshotConfig:
    if not _position_snapshot_enabled():
        raise PositionSnapshotApiError(
            "MOOMOO_JOURNAL_REFRESH_ENABLED is not enabled"
        )
    if not _enabled_env("MOOMOO_OPEND_ENABLED"):
        raise PositionSnapshotApiError("MOOMOO_OPEND_ENABLED is not enabled")
    environment = (os.environ.get("MOOMOO_JOURNAL_ENV") or "LIVE").strip().upper()
    if environment != "LIVE":
        raise PositionSnapshotApiError(
            "MOOMOO_JOURNAL_ENV must be LIVE for current position snapshots"
        )
    binding_secret = (
        os.environ.get("MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET") or ""
    ).strip()
    if len(binding_secret) < 32:
        raise PositionSnapshotApiError(
            "MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET must contain at least 32 characters"
        )
    account_id = (os.environ.get("MOOMOO_JOURNAL_ACCOUNT_ID") or "").strip() or None
    host = (os.environ.get("MOOMOO_OPEND_HOST") or "127.0.0.1").strip()
    if not host:
        raise PositionSnapshotApiError("MOOMOO_OPEND_HOST cannot be empty")
    try:
        port = int((os.environ.get("MOOMOO_OPEND_PORT") or "11111").strip())
    except ValueError as exc:
        raise PositionSnapshotApiError("MOOMOO_OPEND_PORT must be an integer") from exc
    try:
        return PositionSnapshotConfig(
            account_binding_secret=binding_secret,
            env="LIVE",
            market="US",
            host=host,
            port=port,
            acc_id=account_id,
            query_timeout=_bounded_float_env(
                "MOOMOO_JOURNAL_QUERY_TIMEOUT_SECONDS",
                15.0,
                minimum=1.0,
                maximum=60.0,
            ),
            overall_timeout=_bounded_float_env(
                "MOOMOO_JOURNAL_REFRESH_TIMEOUT_SECONDS",
                180.0,
                minimum=10.0,
                maximum=300.0,
            ),
        )
    except ValueError as exc:
        raise PositionSnapshotApiError(str(exc)) from exc


def _position_snapshot_configured() -> bool:
    try:
        _position_snapshot_probe_config()
    except PositionSnapshotApiError:
        return False
    return True


def _member_response(member: Any) -> PositionSnapshotMember:
    return PositionSnapshotMember(
        symbol=member.symbol,
        market="US",
        asset_type="option",
        underlying=member.underlying,
        expiry=member.expiry,
        strike=member.strike,
        option_right=member.option_right,
        position_side=member.position_side,
        quantity_contracts=member.quantity_contracts,
        signed_quantity_contracts=member.signed_quantity_contracts,
        can_sell_quantity_contracts=member.can_sell_quantity_contracts,
        currency=member.currency,
        contract_multiplier=member.contract_multiplier,
        basis=member.contract_multiplier_basis,
        context=PositionSnapshotCostContext(
            cost_price=member.cost_price,
            cost_price_valid=member.cost_price_valid,
            average_cost=member.average_cost,
            diluted_cost=member.diluted_cost,
            semantics=member.cost_context_semantics,
        ),
    )


def _count(counts: Mapping[str, int], name: str) -> int:
    value = counts.get(name, 0)
    return int(value)


def _stability_response(value: Mapping[str, Any]) -> PositionSnapshotStability:
    return PositionSnapshotStability(
        stable=bool(value.get("stable")),
        comparison_basis=str(value.get("comparison_basis") or ""),
        added_symbols=list(value.get("added_symbols") or ()),
        removed_symbols=list(value.get("removed_symbols") or ()),
        changed_symbols=list(value.get("changed_symbols") or ()),
    )


def _observation_response(value: Any) -> PositionSnapshotObservation:
    if bool(value.historical_opening_proven):
        raise PositionSnapshotApiError(
            "current position snapshot cannot prove a historical opening"
        )
    members = [_member_response(member) for member in value.members]
    counts = value.counts
    position_count = int(getattr(value, "position_count", len(members)))
    contract_spec_count = int(
        getattr(value, "contract_spec_count", _count(counts, "contract_specs"))
    )
    hashes = {
        name: str(getattr(value, name))
        for name in (
            "scope_sha256",
            "source_sha256",
            "evidence_sha256",
            "snapshot_sha256",
            "stability_evidence_sha256",
            "member_set_sha256",
        )
        if getattr(value, name, None)
    }
    stability = _stability_response(value.stability)
    return PositionSnapshotObservation(
        observed_from=value.query_started_at,
        observed_through=value.query_completed_at,
        operation_completed_at=value.operation_completed_at,
        broker_as_of=value.broker_as_of_at,
        stable=stability.stable,
        stability=stability,
        retrieval_complete=bool(value.retrieval_complete),
        position_snapshot_complete=bool(value.position_snapshot_complete),
        contract_spec_status=value.contract_spec_status,
        analysis_ready=bool(value.analysis_ready),
        content_state=value.content_state,
        position_count=position_count,
        total_contracts=value.total_contracts,
        future_anchor_candidate=bool(value.future_anchor_candidate),
        historical_opening_proven=False,
        filter_counts=PositionSnapshotFilterCounts(
            first_total_rows=_count(counts, "first_total_rows"),
            second_total_rows=_count(counts, "second_total_rows"),
            filtered_non_us_rows=_count(counts, "second_filtered_non_us_rows"),
            filtered_non_option_rows=_count(
                counts,
                "second_filtered_non_option_rows",
            ),
            filtered_zero_quantity_rows=_count(
                counts,
                "second_filtered_zero_quantity_rows",
            ),
            contract_spec_count=contract_spec_count,
            validation_issue_count=len(value.validation_issues),
        ),
        positions=members,
        hashes=hashes,
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _confirmed_response(
    snapshot: Any,
    *,
    publication: Any = None,
    evaluated_at: datetime | None = None,
) -> ConfirmedPositionSnapshotResponse:
    if not bool(snapshot.acknowledged_future_only):
        raise PositionSnapshotApiError(
            "stored current position snapshot lacks future-only acknowledgement"
        )
    checked_at = evaluated_at or _now_utc()
    if checked_at.tzinfo is None or snapshot.operation_completed_at.tzinfo is None:
        raise PositionSnapshotApiError(
            "position snapshot freshness timestamps must include a timezone"
        )
    checked_at = checked_at.astimezone(timezone.utc)
    completed_at = snapshot.operation_completed_at.astimezone(timezone.utc)
    raw_age = checked_at - completed_at
    raw_age_seconds = int(raw_age.total_seconds())
    threshold_seconds = int(
        MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS.total_seconds()
    )
    future_skew_exceeded = -raw_age > MAX_POSITION_SNAPSHOT_FUTURE_SKEW
    freshness_status = (
        "stale"
        if raw_age > MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS
        or future_skew_exceeded
        else "fresh"
    )
    publication_binding = (
        None if publication is None else publication.account_binding
    )
    binding_matches = (
        None
        if publication_binding is None
        else snapshot.account_binding_id == publication_binding
    )
    if binding_matches is None:
        binding_status = "publication_unavailable"
    elif binding_matches:
        binding_status = "matches_latest_publication"
    else:
        binding_status = "mismatch"
    publication_id = (
        None if publication is None else getattr(publication, "publication_id", None)
    )
    snapshot_publication_id = getattr(
        snapshot,
        "refresh_publication_id",
        None,
    )
    anchor_matches = (
        None
        if publication_id is None or snapshot_publication_id is None
        else int(snapshot_publication_id) == int(publication_id)
    )
    if publication is None:
        anchor_status = "publication_unavailable"
    elif anchor_matches is True:
        anchor_status = "latest"
    else:
        anchor_status = "superseded"
    is_current = bool(
        binding_matches is True
        and anchor_matches is True
        and freshness_status == "fresh"
    )
    return ConfirmedPositionSnapshotResponse(
        id=snapshot.snapshot_id,
        key=snapshot.snapshot_key,
        recorded_at=snapshot.recorded_at,
        acknowledged_future_only=True,
        freshness=PositionSnapshotFreshness(
            status=freshness_status,
            age_seconds=max(0, raw_age_seconds),
            future_skew_seconds=max(0, -raw_age_seconds),
            threshold_seconds=threshold_seconds,
            evaluated_at=checked_at,
            time_semantics=(
                "local_clock_age_since_operation_completed_at; "
                f"{POSITION_SNAPSHOT_TIME_SEMANTICS}"
            ),
        ),
        account_binding_status=binding_status,
        publication_anchor_status=anchor_status,
        is_current=is_current,
        observation=_observation_response(snapshot),
    )


def _not_found_or_conflict(exc: PositionSnapshotError) -> HTTPException:
    message = str(exc)
    status_code = 404 if "does not exist" in message or "not found" in message else 409
    return HTTPException(status_code=status_code, detail=message)


def _decimal_text(
    value: Any,
    *,
    field_name: str,
    nonnegative: bool = False,
) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PositionSnapshotApiError(
            f"future Episode preview {field_name} is not a decimal"
        ) from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise PositionSnapshotApiError(
            f"future Episode preview {field_name} is outside its safe range"
        )
    return str(value)


def _optional_decimal_text(value: Any, *, field_name: str) -> str | None:
    return None if value is None else _decimal_text(value, field_name=field_name)


def _future_episode_preview_response(
    preview: Any,
    *,
    expected_fence_key: str,
    expected_account_key: str,
) -> FuturePositionEpisodeBuildPreviewResponse:
    """Project only a compact summary and fail closed on unsafe flags."""
    if preview.account_key != expected_account_key:
        raise PositionSnapshotApiError(
            "future Episode preview returned a different account"
        )
    if preview.fence_key != expected_fence_key:
        raise PositionSnapshotApiError(
            "future Episode preview returned a different continuity fence"
        )
    if (
        preview.preview_only is not True
        or preview.default_will_change is not False
        or preview.evidence_written is not False
        or preview.trading_action_performed is not False
    ):
        raise PositionSnapshotApiError(
            "future Episode preview violated the zero-write contract"
        )
    counts = preview.counts
    if counts.planned_position_episode_count != (
        counts.planned_open_episode_count
        + counts.planned_closed_episode_count
    ):
        raise PositionSnapshotApiError(
            "future Episode preview returned inconsistent lifecycle counts"
        )
    if counts.headline_episode_count + counts.headline_excluded_episode_count != (
        counts.planned_position_episode_count
    ):
        raise PositionSnapshotApiError(
            "future Episode preview returned inconsistent headline counts"
        )
    if counts.source_event_count != (
        counts.post_boundary_order_event_count
        + counts.post_boundary_fill_event_count
    ):
        raise PositionSnapshotApiError(
            "future Episode preview returned inconsistent source-event counts"
        )
    if any(
        value > counts.planned_position_episode_count
        for value in (
            counts.left_censored_episode_count,
            counts.right_censored_episode_count,
            counts.group_fee_affected_episode_count,
        )
    ):
        raise PositionSnapshotApiError(
            "future Episode preview returned impossible censoring counts"
        )
    if preview.fee_conserved is not True:
        raise PositionSnapshotApiError(
            "future Episode preview did not conserve known fees"
        )
    if (
        preview.boundary_at.tzinfo is None
        or preview.source_cutoff_at.tzinfo is None
        or preview.source_cutoff_at < preview.boundary_at
    ):
        raise PositionSnapshotApiError(
            "future Episode preview returned an invalid source window"
        )
    fee_conservation = {
        str(currency): {
            str(name): _decimal_text(
                amount,
                field_name=f"fee_conservation_by_currency.{currency}.{name}",
                nonnegative=True,
            )
            for name, amount in values.items()
        }
        for currency, values in preview.fee_conservation_by_currency.items()
    }
    return FuturePositionEpisodeBuildPreviewResponse(
        projection_name=preview.projection_name,
        projection_version=preview.projection_version,
        continuity_policy_version=preview.continuity_policy_version,
        account_key=preview.account_key,
        scope=preview.scope,
        fence_key=preview.fence_key,
        snapshot_id=preview.snapshot_id,
        snapshot_key=preview.snapshot_key,
        target_publication_id=preview.target_publication_id,
        target_publication_key=preview.target_publication_key,
        target_canonical_set_id=preview.target_canonical_set_id,
        target_canonical_set_sha256=preview.target_canonical_set_sha256,
        canonical_source_batch_ids=list(preview.canonical_source_batch_ids),
        publication_source_batch_ids=list(
            preview.publication_source_batch_ids
        ),
        boundary_at=preview.boundary_at,
        source_cutoff_at=preview.source_cutoff_at,
        builder_name=preview.builder_name,
        builder_version=preview.builder_version,
        builder_config_sha256=preview.builder_config_sha256,
        evidence_set_sha256=preview.evidence_set_sha256,
        planned_build_key=preview.planned_build_key,
        opening_boundary_policy=preview.opening_boundary_policy,
        max_multiplier_proof_residual=_decimal_text(
            preview.max_multiplier_proof_residual,
            field_name="max_multiplier_proof_residual",
            nonnegative=True,
        ),
        source_known_fee_total=_decimal_text(
            preview.source_known_fee_total,
            field_name="source_known_fee_total",
            nonnegative=True,
        ),
        accounted_known_fee_total=_decimal_text(
            preview.accounted_known_fee_total,
            field_name="accounted_known_fee_total",
            nonnegative=True,
        ),
        retained_execution_group_fee_total=_decimal_text(
            preview.retained_execution_group_fee_total,
            field_name="retained_execution_group_fee_total",
            nonnegative=True,
        ),
        fee_conservation_by_currency=fee_conservation,
        fee_conserved=True,
        headline_realized_pnl_gross=_optional_decimal_text(
            preview.headline_realized_pnl_gross,
            field_name="headline_realized_pnl_gross",
        ),
        headline_total_fee=_optional_decimal_text(
            preview.headline_total_fee,
            field_name="headline_total_fee",
        ),
        headline_realized_pnl_net=_optional_decimal_text(
            preview.headline_realized_pnl_net,
            field_name="headline_realized_pnl_net",
        ),
        counts=FuturePositionEpisodeBuildCountsResponse(
            canonical_member_count=counts.canonical_member_count,
            opening_position_count=counts.opening_position_count,
            opening_contract_count=_decimal_text(
                counts.opening_contract_count,
                field_name="opening_contract_count",
                nonnegative=True,
            ),
            post_boundary_order_event_count=(
                counts.post_boundary_order_event_count
            ),
            post_boundary_fill_event_count=(
                counts.post_boundary_fill_event_count
            ),
            supporting_order_count=counts.supporting_order_count,
            excluded_non_option_event_count=(
                counts.excluded_non_option_event_count
            ),
            execution_group_count=counts.execution_group_count,
            source_event_count=counts.source_event_count,
            planned_position_episode_count=(
                counts.planned_position_episode_count
            ),
            planned_open_episode_count=counts.planned_open_episode_count,
            planned_closed_episode_count=counts.planned_closed_episode_count,
            left_censored_episode_count=counts.left_censored_episode_count,
            right_censored_episode_count=counts.right_censored_episode_count,
            headline_episode_count=counts.headline_episode_count,
            headline_excluded_episode_count=(
                counts.headline_excluded_episode_count
            ),
            group_fee_affected_episode_count=(
                counts.group_fee_affected_episode_count
            ),
        ),
        warnings=list(preview.warnings),
        preview_only=True,
        business_data_written=False,
        episode_build_written=False,
        activation_changed=False,
        evidence_written=False,
        trading_action_performed=False,
    )


@router.get(
    "/v2/position-snapshots/latest",
    response_model=PositionSnapshotLatestResponse,
)
def get_latest_current_position_snapshot(
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PositionSnapshotLatestResponse:
    """Return the newest confirmed current snapshot, if one exists."""
    try:
        state = get_latest_position_snapshot_state(account_key)
        publication = state.publication
        latest = state.snapshot
        latest_response = (
            None
            if latest is None
            else _confirmed_response(latest, publication=publication)
        )
    except (PositionSnapshotApiError, PositionSnapshotError, JournalRefreshError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    continuity_ready = publication is not None
    continuity_reason = None if continuity_ready else _NO_CONTINUITY_REASON
    if (
        publication is not None
        and latest is not None
        and latest.account_binding_id != publication.account_binding
    ):
        continuity_reason = _LATEST_BINDING_CHANGED_REASON
    elif latest_response is not None and latest_response.freshness.status == "stale":
        if (
            latest.operation_completed_at
            > latest_response.freshness.evaluated_at
            + MAX_POSITION_SNAPSHOT_FUTURE_SKEW
        ):
            continuity_reason = _LATEST_SNAPSHOT_FUTURE_SKEW_REASON
        else:
            continuity_reason = _LATEST_SNAPSHOT_STALE_REASON
    return PositionSnapshotLatestResponse(
        enabled=_position_snapshot_enabled(),
        configured=_position_snapshot_configured(),
        continuity_ready=continuity_ready,
        continuity_reason=continuity_reason,
        latest_is_current=(
            False if latest_response is None else latest_response.is_current
        ),
        latest_account_binding_matches=(
            None
            if latest_response is None or publication is None
            else latest_response.account_binding_status
            == "matches_latest_publication"
        ),
        latest_publication_anchor_matches=(
            None
            if latest_response is None or publication is None
            else latest_response.publication_anchor_status == "latest"
        ),
        latest=latest_response,
        trading_action_performed=False,
    )


@router.get(
    "/v2/position-snapshots/episode-boundary-readiness",
    response_model=PositionSnapshotContinuityResponse,
)
def get_position_snapshot_episode_boundary_readiness(
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PositionSnapshotContinuityResponse:
    """Evaluate the future Episode boundary without writing or building."""
    try:
        assessment = assess_latest_position_snapshot_continuity(account_key)
        if (assessment.status == "ready") != assessment.ready_for_episode_build:
            raise PositionSnapshotApiError(
                "continuity assessment status is internally inconsistent"
            )
        if assessment.ready_for_episode_build and (
            not assessment.fence_key
            or assessment.target_canonical_set_id is None
            or not assessment.target_canonical_set_sha256
        ):
            raise PositionSnapshotApiError(
                "ready continuity assessment is missing frozen evidence identity"
            )
        return PositionSnapshotContinuityResponse(
            policy_version=assessment.policy_version,
            status=assessment.status,
            ready_for_episode_build=assessment.ready_for_episode_build,
            fence_key=assessment.fence_key,
            account_key=assessment.account_key,
            snapshot_id=assessment.snapshot_id,
            snapshot_key=assessment.snapshot_key,
            anchor_publication_id=assessment.anchor_publication_id,
            anchor_publication_key=assessment.anchor_publication_key,
            target_publication_id=assessment.target_publication_id,
            target_publication_key=assessment.target_publication_key,
            target_canonical_set_id=assessment.target_canonical_set_id,
            target_canonical_set_sha256=(
                assessment.target_canonical_set_sha256
            ),
            boundary_at=assessment.boundary_at,
            guard_started_at=assessment.guard_started_at,
            guard_completed_at=assessment.guard_completed_at,
            publication_ids=list(assessment.publication_ids),
            source_batch_ids=list(assessment.source_batch_ids),
            counts=PositionSnapshotContinuityCounts(
                **assessment.counts.__dict__
            ),
            reasons=[
                PositionSnapshotContinuityReason(
                    code=reason.code,
                    message=reason.message,
                    entity_kind=reason.entity_kind,
                    entity_ids=list(reason.entity_ids),
                    count=reason.count,
                )
                for reason in assessment.reasons
            ],
            evidence_written=False,
            trading_action_performed=False,
        )
    except (
        PositionSnapshotApiError,
        PositionSnapshotContinuityError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/v2/episode-builds/position-snapshot/preview",
    response_model=FuturePositionEpisodeBuildPreviewResponse,
)
def get_future_position_episode_build_preview(
    expected_fence_key: str = Query(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> FuturePositionEpisodeBuildPreviewResponse:
    """Build an in-memory preview only after re-checking the exact fence."""
    try:
        preview = preview_fenced_position_episodes(
            account_key,
            expected_fence_key,
        )
        return _future_episode_preview_response(
            preview,
            expected_fence_key=expected_fence_key,
            expected_account_key=account_key,
        )
    except (
        FuturePositionEpisodePreviewError,
        PositionSnapshotApiError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/v2/episode-builds/position-snapshot",
    response_model=FuturePositionEpisodeBuildConfirmResponse,
)
def create_future_position_episode_build(
    request: FuturePositionEpisodeBuildConfirmRequest,
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> FuturePositionEpisodeBuildConfirmResponse:
    """Append one explicitly confirmed future build without activating it."""
    try:
        result = append_future_position_episode_build(
            account_key,
            expected_fence_key=request.expected_fence_key,
            expected_build_key=request.expected_build_key,
            expected_evidence_set_sha256=(
                request.expected_evidence_set_sha256
            ),
            accept_left_censored_openings=(
                request.accept_left_censored_openings
            ),
            accept_group_fee_scope=request.accept_group_fee_scope,
        )
    except (
        EpisodeRepositoryError,
        FuturePositionEpisodePreviewError,
        PositionSnapshotContinuityError,
        PositionSnapshotApiError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        summary = get_episode_summary(result.build_id, account_key)
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if (
        summary is None
        or summary.build_id != result.build_id
        or summary.build_key != result.build_key
        or summary.source_kind != SNAPSHOT_FENCE_SOURCE_KIND
        or summary.canonical_set_id != result.target_canonical_set_id
        or summary.canonical_set_sha256 != result.target_canonical_set_sha256
    ):
        raise HTTPException(
            status_code=500,
            detail="future episode build was appended but cannot be read back",
        )
    action = "already present" if result.duplicate else "appended"
    return FuturePositionEpisodeBuildConfirmResponse(
        data_state="ready",
        duplicate=result.duplicate,
        build=_episode_build_metadata(summary),
        reconciliation=_episode_reconciliation(summary),
        summary=_episode_summary(summary),
        snapshot_fence=FuturePositionEpisodeBuildSourceResponse(
            snapshot_id=result.snapshot_id,
            snapshot_key=result.snapshot_key,
            fence_key=result.fence_key,
            continuity_policy_version=result.continuity_policy_version,
            target_publication_id=result.target_publication_id,
            target_publication_key=result.target_publication_key,
            target_canonical_set_id=result.target_canonical_set_id,
            target_canonical_set_sha256=result.target_canonical_set_sha256,
            boundary_at=result.boundary_at,
            source_cutoff_at=result.source_cutoff_at,
            projection_name=result.projection_name,
            projection_version=result.projection_version,
            link_key=result.link_key,
        ),
        message=(
            f"future position episode build {action}: "
            f"{result.position_episode_count} episodes. "
            "The default position review remains unchanged; use the explicit "
            "build ID to inspect this result."
        ),
    )


@router.post(
    "/v2/position-snapshots/preview",
    response_model=PositionSnapshotPreviewResponse,
)
async def preview_current_position_snapshot(
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PositionSnapshotPreviewResponse:
    """Run a read-only double acquisition and freeze its server-side plan."""
    try:
        config = _position_snapshot_probe_config()
        publication = get_latest_refresh_publication(account_key)
        expected_binding = (
            None if publication is None else publication.account_binding
        )
        result = await run_in_threadpool(run_position_snapshot_probe, config)
        payload = result.export_payload
        plan = plan_position_snapshot(
            payload,
            account_key=account_key,
            expected_account_binding_id=expected_binding,
        )
        if publication is not None and plan.account_binding_id != expected_binding:
            raise PositionSnapshotApiError(
                "Moomoo account binding differs from the latest confirmed Journal refresh"
            )
        artifact = save_position_snapshot_artifact(plan, payload)
    except PositionSnapshotApiError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (PositionSnapshotError, JournalRefreshError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (MoomooPositionSnapshotError, MoomooReadonlyError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return PositionSnapshotPreviewResponse(
        artifact_id=artifact.artifact_id,
        artifact_key=artifact.artifact_key,
        preview_key=artifact.preview_key,
        expires_at=artifact.expires_at,
        confirm_allowed=bool(plan.confirm_allowed and publication is not None),
        blocking_reasons=list(plan.blocking_reasons),
        warnings=list(plan.warnings),
        observation=_observation_response(plan),
        evidence_written=False,
        trading_action_performed=False,
    )


@router.post(
    "/v2/position-snapshots/{artifact_id}/confirm",
    response_model=PositionSnapshotConfirmResponse,
)
def confirm_current_position_snapshot(
    artifact_id: int,
    request: PositionSnapshotConfirmRequest,
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PositionSnapshotConfirmResponse:
    """Confirm one frozen current snapshot as future-only evidence."""
    try:
        publication = get_latest_refresh_publication(account_key)
        if publication is None:
            raise PositionSnapshotApiError(_NO_CONTINUITY_REASON)
        confirmation = confirm_position_snapshot_artifact(
            artifact_id,
            preview_key=request.preview_key,
            acknowledge_future_only=request.acknowledge_future_only,
            expected_account_binding_id=publication.account_binding,
            account_key=account_key,
        )
        snapshot = _confirmed_response(
            confirmation.snapshot,
            publication=publication,
        )
    except PositionSnapshotApiError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PositionSnapshotError as exc:
        raise _not_found_or_conflict(exc) from exc
    except JournalRefreshError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PositionSnapshotConfirmResponse(
        snapshot=snapshot,
        duplicate=confirmation.duplicate,
        trading_action_performed=False,
    )
