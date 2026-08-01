# -*- coding: utf-8 -*-
"""Journal REST endpoints for the dsa-web frontend.

Stage 7 exposes:
    GET  /api/v1/journal/reality-test
    GET  /api/v1/journal/trades
    GET  /api/v1/journal/trades/{trade_id}
    PATCH /api/v1/journal/trades/{trade_id}
    GET  /api/v1/journal/health-check
    GET  /api/v1/journal/stats
    POST /api/v1/journal/import           (multipart CSV upload)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import and_, func, select
from starlette.concurrency import run_in_threadpool

from api.v1.schemas.journal import (
    CanonicalEpisodeBuildConfirmRequest,
    CanonicalEpisodeBuildPlanResponse,
    EpisodeBuildMetadata,
    EpisodeBuildActivationRequest,
    EpisodeBuildActivationResponse,
    EpisodeBuildActivationStateResponse,
    EpisodeBuildResponse,
    EpisodeConditionalPnl,
    EpisodeHeadlinePnl,
    EpisodeReconciliationSummary,
    HealthCheckItem,
    ImportResponse,
    JournalQaRequest,
    JournalQaResponse,
    JournalStatsByStyleResponse,
    JournalStatsResponse,
    JournalRefreshStatusResponse,
    LedgerDataHealthResponse,
    LedgerImportResponse,
    MonthlyReviewGenerateRequest,
    MonthlyReviewItem,
    MonthlyReviewListResponse,
    MoomooOpenApiConfirmResponse,
    MoomooOpenApiPlanResponse,
    MoomooStatementPreviewResponse,
    MoomooOpenApiPreviewResponse,
    MoomooJournalRefreshConfirmRequest,
    MoomooJournalRefreshConfirmResponse,
    MoomooJournalRefreshPreviewResponse,
    MoomooJournalRefreshPublication,
    MoomooJournalRefreshRequest,
    MoomooJournalRefreshSource,
    MoomooSyncRequest,
    MoomooSyncResponse,
    PositionEpisodeDetailResponse,
    PositionEpisodeCaseFocus,
    PositionEpisodeEvidenceItem,
    PositionEpisodeInstrument,
    PositionEpisodeItem,
    PositionEpisodeListResponse,
    PositionEpisodeQuality,
    PositionEpisodeReviewQueue,
    PositionEpisodeSummaryResponse,
    RealityTestResponse,
    TradeItem,
    TradeListResponse,
    TradeUpdateRequest,
)
from src.journal.analytics import (
    dte_bucket_win_rates,
    dte_distribution,
    reality_test,
    stats_by_style,
)
from src.journal.brokers.moomoo_statement import (
    MoomooStatementError,
    parse_statement,
)
from src.journal.brokers.moomoo_openapi_export import (
    MoomooOpenApiExportError,
    parse_openapi_export,
)
from src.journal.brokers.moomoo_readonly import (
    MoomooReadonlyError,
    ProbeConfig,
    run_readonly_probe,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    LedgerImportError,
    get_latest_data_health,
    import_statement_batch,
)
from src.journal.ledger.openapi_repository import (
    OpenApiPlanError,
    confirm_openapi_import_plan,
    plan_openapi_import,
)
from src.journal.ledger.refresh_repository import (
    JournalRefreshError,
    confirm_refresh_artifact,
    get_journal_refresh_status,
    save_refresh_artifact,
    suggest_refresh_window,
)
from src.journal.ledger.episode_repository import (
    EpisodeBuildSummary,
    EpisodeRepositoryError,
    PositionEpisodeListItem,
    append_canonical_position_episode_build,
    append_latest_position_episode_build,
    get_episode_summary,
    get_latest_episode_summary,
    get_latest_position_episode_detail,
    get_latest_position_episode_page,
    get_position_episode_detail,
    get_position_episode_page,
    position_episode_pnl_exclusion_reasons,
    preview_canonical_position_episodes,
    preview_latest_position_episodes,
)
from src.journal.ledger.activation_repository import (
    SNAPSHOT_FENCE_SOURCE_KIND,
    EpisodeBuildActivationError,
    activate_episode_build,
    get_episode_build_activation_state,
)
from src.journal.models import (
    JournalHealthCheck,
    JournalMonthlyReview,
    JournalTrade,
)
from src.journal.storage import (
    DEFAULT_PORTFOLIO_LABEL,
    init_journal_schema,
)
from src.storage import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

_LEGACY_BROKERS = {"moomoo_us"}
_MAX_CSV_UPLOAD_BYTES = 50 * 1024 * 1024
_MAX_OPENAPI_EXPORT_BYTES = 20 * 1024 * 1024


# --- helpers -----------------------------------------------------------------


async def _read_upload_limited(file: UploadFile, limit: int) -> bytes:
    """Read at most ``limit`` bytes and close the temporary upload handle."""
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await file.read(min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"file too large ({total} bytes read, max {limit})",
                )
        return b"".join(chunks)
    finally:
        await file.close()


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
        raise JournalRefreshError(f"{name} must be a number") from exc
    if value < minimum or value > maximum:
        raise JournalRefreshError(
            f"{name} must be between {minimum:g} and {maximum:g}"
        )
    return value


def _journal_refresh_probe_config(
    *,
    account_key: str,
    overlap_days: int,
) -> ProbeConfig:
    if not _enabled_env("MOOMOO_OPEND_ENABLED"):
        raise JournalRefreshError("MOOMOO_OPEND_ENABLED is not enabled")
    if not _enabled_env("MOOMOO_JOURNAL_REFRESH_ENABLED"):
        raise JournalRefreshError("MOOMOO_JOURNAL_REFRESH_ENABLED is not enabled")
    environment = (os.environ.get("MOOMOO_JOURNAL_ENV") or "LIVE").strip().upper()
    if environment != "LIVE":
        raise JournalRefreshError("MOOMOO_JOURNAL_ENV must be LIVE for Journal refresh")
    binding_secret = (
        os.environ.get("MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET") or ""
    ).strip()
    if len(binding_secret) < 32:
        raise JournalRefreshError(
            "MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET must contain at least 32 characters"
        )
    account_id = (os.environ.get("MOOMOO_JOURNAL_ACCOUNT_ID") or "").strip() or None
    try:
        port = int((os.environ.get("MOOMOO_OPEND_PORT") or "11111").strip())
    except ValueError as exc:
        raise JournalRefreshError("MOOMOO_OPEND_PORT must be an integer") from exc
    start, end = suggest_refresh_window(
        account_key,
        overlap_days=overlap_days,
    )
    try:
        return ProbeConfig(
            start=start,
            end=end,
            env="LIVE",
            market="US",
            host=(os.environ.get("MOOMOO_OPEND_HOST") or "127.0.0.1").strip(),
            port=port,
            acc_id=account_id,
            account_binding_secret=binding_secret,
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
        raise JournalRefreshError(str(exc)) from exc


def _parse_openapi_content(content: bytes) -> tuple[dict, object]:
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        payload = json.loads(content.decode("utf-8-sig"), parse_float=Decimal)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="read-only export must be valid UTF-8 JSON",
        ) from exc
    try:
        preview = parse_openapi_export(payload)
    except MoomooOpenApiExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload, preview


def _openapi_observation_counts(preview: object) -> dict[str, int]:
    """Describe broker rows without collapsing multi-leg parent orders."""
    orders = tuple(getattr(preview, "orders", ()) or ())
    fills = tuple(getattr(preview, "fills", ()) or ())
    fees = tuple(getattr(preview, "fees", ()) or ())
    contract_specs = tuple(getattr(preview, "contract_specs", ()) or ())
    group_orders = tuple(
        item for item in orders if bool(getattr(item, "combo_legs", ()))
    )
    group_ids = {
        getattr(item, "source_order_id", None) for item in group_orders
    }
    ordinary_orders = sum(
        bool(getattr(item, "combo_definition_available", False))
        and not bool(getattr(item, "combo_legs", ()))
        for item in orders
    )
    return {
        "order_observations": len(orders),
        "ordinary_order_observations": ordinary_orders,
        "unclassified_parent_observations": (
            len(orders) - ordinary_orders - len(group_orders)
        ),
        "fill_observations": len(fills),
        "fee_observations": len(fees),
        "contract_spec_observations": len(contract_specs),
        "execution_group_observations": len(group_orders),
        "execution_group_leg_observations": sum(
            len(getattr(item, "combo_legs", ()) or ())
            for item in group_orders
        ),
        "execution_group_fill_links": sum(
            getattr(item, "source_order_id", None) in group_ids
            for item in fills
        ),
        "execution_group_fee_observations": sum(
            getattr(item, "source_order_id", None) in group_ids
            for item in fees
        ),
    }


def _trade_row_to_dict(row: JournalTrade) -> dict:
    return {
        "id": row.id,
        "portfolio_label": row.portfolio_label,
        "is_option": bool(row.is_option),
        "raw_symbol": row.raw_symbol,
        "underlying": row.underlying,
        "expiry": row.expiry,
        "strike": row.strike,
        "right": row.right,
        "direction": row.direction,
        "status": row.status,
        "quantity": int(row.quantity),
        "avg_entry_price": float(row.avg_entry_price),
        "avg_exit_price": row.avg_exit_price,
        "entry_time": row.entry_time,
        "exit_time": row.exit_time,
        "hold_seconds": row.hold_seconds,
        "dte_at_entry": row.dte_at_entry,
        "dte_bucket": row.dte_bucket,
        "pnl_gross": row.pnl_gross,
        "pnl_net": row.pnl_net,
        "pnl_pct": row.pnl_pct,
        "total_fee": row.total_fee,
        "trade_style": row.trade_style,
        "regime_score_at_entry": row.regime_score_at_entry,
        "was_fake_breakout": row.was_fake_breakout,
        "user_notes": row.user_notes,
        "emotional_state": row.emotional_state,
        "strategy_tag_ai": row.strategy_tag_ai,
    }


def _load_trades(portfolio: str, since: Optional[date] = None) -> list[dict]:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        stmt = select(JournalTrade).where(JournalTrade.portfolio_label == portfolio)
        rows = session.execute(stmt).scalars().all()
        out = []
        for r in rows:
            if since and r.entry_time and r.entry_time.date() < since:
                continue
            out.append(_trade_row_to_dict(r))
        return out


def _decimal_text(value: object) -> Optional[str]:
    """Serialize ledger Decimals without a float round-trip."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")


def _stringify_nested_decimals(value: object) -> object:
    """Keep Decimal-safe JSON semantics inside parsed evidence metadata too."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {
            str(key): _stringify_nested_decimals(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_stringify_nested_decimals(item) for item in value]
    return value


def _episode_build_metadata(
    summary: EpisodeBuildSummary,
) -> EpisodeBuildMetadata:
    policy = summary.opening_boundary_policy
    source_batch_ids = getattr(summary, "source_batch_ids", None)
    if source_batch_ids is None:
        source_batch_ids = (summary.source_batch_id,)
    return EpisodeBuildMetadata(
        id=summary.build_id,
        build_key=summary.build_key,
        builder_name=summary.builder_name,
        builder_version=summary.builder_version,
        status=summary.status,
        source_batch_ids=list(source_batch_ids),
        source_kind=getattr(summary, "source_kind", "csv_batch"),
        canonical_set_id=getattr(summary, "canonical_set_id", None),
        canonical_set_sha256=getattr(
            summary,
            "canonical_set_sha256",
            None,
        ),
        source_window_start=summary.source_window_start,
        source_cutoff_at=summary.source_cutoff_at,
        position_episode_count=summary.position_episode_count,
        unresolved_evidence_count=summary.unresolved_evidence_count,
        completeness_score=_decimal_text(summary.completeness_score) or "0",
        opening_boundary_policy=policy,
        assumed_flat_unverified=policy in {
            "assumed_flat_unverified",
            "mixed_explicit_and_assumed",
        },
        execution_group_count=getattr(
            summary,
            "execution_group_count",
            0,
        ),
        group_fee_affected_episode_count=getattr(
            summary,
            "group_fee_affected_episode_count",
            0,
        ),
        retained_execution_group_fee_total=(
            _decimal_text(
                getattr(
                    summary,
                    "retained_execution_group_fee_total",
                    Decimal("0"),
                )
            )
            or "0"
        ),
        fee_conservation_by_currency={
            str(currency): {
                str(key): _decimal_text(value) or "0"
                for key, value in values.items()
            }
            for currency, values in getattr(
                summary,
                "fee_conservation_by_currency",
                {},
            ).items()
        },
        leg_fee_attribution_complete=getattr(
            summary,
            "leg_fee_attribution_complete",
            True,
        ),
        partial_reasons=list(summary.partial_reasons),
        recorded_at=summary.recorded_at,
    )


def _episode_reconciliation(
    summary: EpisodeBuildSummary,
) -> EpisodeReconciliationSummary:
    """Prefer immutable build coverage, with a fallback for older callers."""
    frozen_fields = (
        "reconciliation_window_start",
        "reconciliation_window_end",
        "reconciled_order_count",
        "total_order_count",
    )
    has_frozen_fields = all(
        hasattr(summary, field) for field in frozen_fields
    )
    has_frozen_coverage = (
        has_frozen_fields
        and summary.total_order_count > 0
    )
    if has_frozen_coverage:
        scope = summary.reconciliation_scope
        return EpisodeReconciliationSummary(
            status=summary.reconciliation_status,
            scope=scope,
            partial_window=scope == "partial_window",
            window_start=summary.reconciliation_window_start,
            window_end=summary.reconciliation_window_end,
            matched_order_count=summary.reconciled_order_count,
            total_order_count=summary.total_order_count,
        )

    health = get_latest_data_health(summary.account_key)
    same_batch = health is not None and health.batch_id == summary.source_batch_id
    scope = summary.reconciliation_scope
    return EpisodeReconciliationSummary(
        status=summary.reconciliation_status,
        scope=scope,
        partial_window=scope == "partial_window",
        window_start=(
            health.reconciliation_window_start if same_batch else None
        ),
        window_end=health.reconciliation_window_end if same_batch else None,
        matched_order_count=(
            health.reconciled_order_observations if same_batch else 0
        ),
        total_order_count=health.order_observations if same_batch else 0,
    )


def _episode_summary(
    summary: EpisodeBuildSummary,
) -> PositionEpisodeSummaryResponse:
    exclusion_counts = {
        str(key): int(value)
        for key, value in summary.headline_exclusion_counts.items()
    }
    total = summary.position_episode_count
    return PositionEpisodeSummaryResponse(
        total_episode_count=total,
        open_episode_count=summary.open_episode_count,
        closed_episode_count=summary.closed_episode_count,
        boundary_unverified_episode_count=(
            summary.boundary_unverified_episode_count
        ),
        left_censored_episode_count=summary.left_censored_episode_count,
        incomplete_episode_count=summary.incomplete_episode_count,
        aggregate_only_episode_count=summary.aggregate_only_episode_count,
        group_fee_affected_episode_count=getattr(
            summary,
            "group_fee_affected_episode_count",
            0,
        ),
        headline_pnl=EpisodeHeadlinePnl(
            eligible_closed_count=summary.headline_episode_count,
            excluded_episode_count=summary.headline_excluded_episode_count,
            exclusion_counts=exclusion_counts,
            realized_pnl_gross=_decimal_text(
                summary.headline_realized_pnl_gross
            ),
            total_fee=_decimal_text(summary.headline_total_fee),
            realized_pnl_net=_decimal_text(
                summary.headline_realized_pnl_net
            ),
        ),
        conditional_pnl=EpisodeConditionalPnl(
            opening_boundary_policy=summary.opening_boundary_policy,
            count=summary.conditional_closed_episode_count,
            realized_pnl_gross=_decimal_text(
                summary.conditional_realized_pnl_gross
            ),
            total_fee=_decimal_text(summary.conditional_total_fee),
            realized_pnl_net=_decimal_text(
                summary.conditional_realized_pnl_net
            ),
            included_in_headline=False,
        ),
    )


def _episode_exclusion_reasons(
    item: PositionEpisodeListItem,
    opening_boundary_policy: str,
) -> list[str]:
    # Single source of truth in the repository so the review-insights
    # aggregation and this projection can never disagree on eligibility.
    return list(
        position_episode_pnl_exclusion_reasons(item, opening_boundary_policy)
    )


def _position_episode_item(
    item: PositionEpisodeListItem,
    *,
    opening_boundary_policy: str,
) -> PositionEpisodeItem:
    item_policy = getattr(
        item,
        "opening_boundary_policy",
        opening_boundary_policy,
    )
    exclusion_reasons = _episode_exclusion_reasons(item, item_policy)
    return PositionEpisodeItem(
        id=item.episode_id,
        episode_build_id=item.build_id,
        strategy_episode_id=item.strategy_episode_id,
        episode_key=item.episode_key,
        lineage_key=item.lineage_key,
        strategy_type=item.strategy_type,
        instrument=PositionEpisodeInstrument(
            raw_symbol=item.raw_symbol,
            asset_type=item.asset_type,
            underlying=item.underlying,
            expiry=item.expiry,
            strike=_decimal_text(item.strike),
            option_right=item.option_right,
            contract_multiplier=_decimal_text(item.contract_multiplier),
            currency=item.currency,
        ),
        direction=item.direction,
        lifecycle_status=item.lifecycle_status,
        opened_at=item.opened_at,
        closed_at=item.closed_at,
        hold_seconds=item.hold_seconds,
        opened_quantity=_decimal_text(item.opened_quantity) or "0",
        closed_quantity=_decimal_text(item.closed_quantity) or "0",
        remaining_quantity=_decimal_text(item.remaining_quantity) or "0",
        average_entry_price=_decimal_text(item.average_entry_price),
        average_exit_price=_decimal_text(item.average_exit_price),
        realized_pnl_gross=_decimal_text(item.realized_pnl_gross),
        total_fee=_decimal_text(item.total_fee),
        realized_pnl_net=_decimal_text(item.realized_pnl_net),
        quality=PositionEpisodeQuality(
            completeness_status=item.completeness_status,
            completeness_score=(
                _decimal_text(item.completeness_score) or "0"
            ),
            construction_basis=item.construction_basis,
            contract_multiplier_basis=item.contract_multiplier_basis,
            opening_boundary_policy=item_policy,
            assumed_flat_unverified=(
                item_policy
                in {
                    "assumed_flat_unverified",
                    "mixed_explicit_and_assumed",
                }
                and not item.left_boundary_verified
            ),
            left_boundary_verified=item.left_boundary_verified,
            is_left_censored=item.is_left_censored,
            is_right_censored=item.is_right_censored,
            pnl_summary_eligible=not exclusion_reasons,
            group_fee_unallocated=getattr(
                item,
                "group_fee_unallocated",
                False,
            ),
            pnl_exclusion_reasons=exclusion_reasons,
        ),
        review_status=getattr(item, "review_status", "not_started"),
        review_revision=getattr(item, "review_revision", None),
        review_updated_at=getattr(item, "review_updated_at", None),
    )


# --- endpoints ---------------------------------------------------------------


@router.get("/reality-test", response_model=RealityTestResponse)
def get_reality_test(
    top_n: int = Query(5, ge=0, le=100),
    since: Optional[date] = Query(None),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> RealityTestResponse:
    trades = _load_trades(portfolio, since=since)
    rt = reality_test(trades, top_n=top_n)
    return RealityTestResponse(**rt)


@router.get("/trades", response_model=TradeListResponse)
def list_trades(
    symbol: Optional[str] = Query(None),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    status: Optional[str] = Query(None),
    style: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> TradeListResponse:
    init_journal_schema()
    db = get_db()
    filters = [JournalTrade.portfolio_label == portfolio]
    if symbol:
        filters.append(JournalTrade.underlying == symbol.upper())
    if start:
        filters.append(JournalTrade.entry_time >= datetime.combine(start, datetime.min.time()))
    if end:
        filters.append(JournalTrade.entry_time <= datetime.combine(end, datetime.max.time()))
    if status:
        filters.append(JournalTrade.status == status)
    if style:
        filters.append(JournalTrade.trade_style == style)

    with db.session_scope() as session:
        total = session.execute(
            select(func.count(JournalTrade.id)).where(and_(*filters))
        ).scalar_one()
        stmt = (
            select(JournalTrade)
            .where(and_(*filters))
            .order_by(JournalTrade.entry_time.desc().nulls_last(), JournalTrade.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        rows = session.execute(stmt).scalars().all()
        items = [TradeItem(**_trade_row_to_dict(r)) for r in rows]

    return TradeListResponse(total=int(total), page=page, per_page=per_page, items=items)


@router.get("/trades/{trade_id}", response_model=TradeItem)
def get_trade(trade_id: int) -> TradeItem:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.get(JournalTrade, trade_id)
        if row is None:
            raise HTTPException(status_code=404, detail="trade not found")
        return TradeItem(**_trade_row_to_dict(row))


@router.patch("/trades/{trade_id}", response_model=TradeItem)
def update_trade(trade_id: int, payload: TradeUpdateRequest) -> TradeItem:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.get(JournalTrade, trade_id)
        if row is None:
            raise HTTPException(status_code=404, detail="trade not found")
        if payload.user_notes is not None:
            row.user_notes = payload.user_notes
        if payload.emotional_state is not None:
            row.emotional_state = payload.emotional_state
        if payload.trade_style is not None:
            row.trade_style = payload.trade_style
        session.flush()
        return TradeItem(**_trade_row_to_dict(row))


@router.get("/health-check", response_model=Optional[HealthCheckItem])
def get_health_check(
    date_: date = Query(..., alias="date"),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
):
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        row = (
            session.execute(
                select(JournalHealthCheck).where(
                    JournalHealthCheck.portfolio_label == portfolio,
                    JournalHealthCheck.check_date == date_,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        import json as _json

        warnings = []
        if row.warnings_json:
            try:
                warnings = _json.loads(row.warnings_json)
            except Exception:  # noqa: BLE001
                warnings = []
        return HealthCheckItem(
            check_date=row.check_date,
            total_orders=int(row.total_orders or 0),
            orders_0dte=int(row.orders_0dte or 0),
            orders_1_3dte=int(row.orders_1_3dte or 0),
            orders_opening_hour=int(row.orders_opening_hour or 0),
            top_underlying=row.top_underlying,
            top_underlying_pct=row.top_underlying_pct,
            warnings_json=warnings,
            pnl_estimate=row.pnl_estimate,
            regime_score=row.regime_score,
        )


@router.get("/stats", response_model=JournalStatsResponse)
def get_stats(
    days: int = Query(90, ge=1, le=3650),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> JournalStatsResponse:
    since = date.today() - timedelta(days=days)
    trades = _load_trades(portfolio, since=since)
    dist = dte_distribution(trades)
    by_bucket = dte_bucket_win_rates(trades)
    rt = reality_test(trades, top_n=5)
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None]
    win_rate = None
    if closed:
        wins = sum(1 for t in closed if t["pnl_net"] > 0)
        win_rate = wins / len(closed)
    return JournalStatsResponse(
        window_days=days,
        closed_trade_count=len(closed),
        total_pnl_net=rt["total_pnl_net"],
        win_rate=win_rate,
        dte_distribution=dist,
        win_rate_by_bucket=by_bucket,
        reality_test=RealityTestResponse(**rt),
    )


@router.get("/v2/data-health", response_model=LedgerDataHealthResponse)
def get_ledger_data_health(
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> LedgerDataHealthResponse:
    health = get_latest_data_health(account_key)
    if health is None:
        return LedgerDataHealthResponse(has_data=False)
    return LedgerDataHealthResponse(
        has_data=True,
        batch_id=health.batch_id,
        analysis_level=health.analysis_level,
        reconciliation_status=health.reconciliation_status,
        reconciliation_scope=health.reconciliation_scope,
        reconciliation_window_start=health.reconciliation_window_start,
        reconciliation_window_end=health.reconciliation_window_end,
        reconciled_order_observations=health.reconciled_order_observations,
        completeness_score=format(health.completeness_score, "f"),
        order_observations=health.order_observations,
        fill_observations=health.fill_observations,
        aggregate_only_filled_orders=health.aggregate_only_filled_orders,
        window_start=health.window_start,
        window_end=health.window_end,
        recorded_at=health.recorded_at,
        legacy_journal_written=False,
    )


@router.get("/v2/refresh-status", response_model=JournalRefreshStatusResponse)
def get_moomoo_journal_refresh_status(
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> JournalRefreshStatusResponse:
    """Return separate broker, evidence, build, and active-view watermarks."""
    try:
        status = get_journal_refresh_status(account_key)
    except JournalRefreshError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    refresh_enabled = _enabled_env("MOOMOO_JOURNAL_REFRESH_ENABLED")
    refresh_configured = (
        refresh_enabled
        and _enabled_env("MOOMOO_OPEND_ENABLED")
        and (os.environ.get("MOOMOO_JOURNAL_ENV") or "LIVE").strip().upper()
        == "LIVE"
        and len(
            (
                os.environ.get("MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET") or ""
            ).strip()
        )
        >= 32
    )
    return JournalRefreshStatusResponse(
        refresh_enabled=refresh_enabled,
        refresh_configured=refresh_configured,
        **status.__dict__,
    )


@router.get(
    "/v2/position-episodes",
    response_model=PositionEpisodeListResponse,
)
def list_position_episodes_v2(
    underlying: Optional[str] = Query(None, min_length=1, max_length=32),
    lifecycle_status: Optional[str] = Query(
        None,
        pattern=r"^(open|closed)$",
    ),
    completeness_status: Optional[str] = Query(
        None,
        pattern=r"^(exact|complete|partial)$",
    ),
    review_status: Optional[str] = Query(
        None,
        pattern=r"^(not_started|in_progress|completed)$",
    ),
    case_focus: Optional[PositionEpisodeCaseFocus] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    build_id: Optional[int] = Query(None, ge=1),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> PositionEpisodeListResponse:
    """List the default build, or one explicitly selected immutable build."""
    if build_id is None:
        summary = get_latest_episode_summary(account_key)
    else:
        summary = get_episode_summary(build_id, account_key)
    if summary is None:
        if build_id is not None:
            raise HTTPException(status_code=404, detail="episode build not found")
        return PositionEpisodeListResponse(
            data_state="not_built",
            total=0,
            page=page,
            per_page=per_page,
            items=[],
        )

    page_kwargs = {
        "underlying": underlying,
        "lifecycle_status": lifecycle_status,
        "completeness_status": completeness_status,
        "case_focus": case_focus,
        "page": page,
        "per_page": per_page,
    }
    if review_status is not None:
        page_kwargs["review_status"] = review_status
    if build_id is None:
        result_page = get_latest_position_episode_page(
            account_key,
            **page_kwargs,
        )
        # A concurrent CSV append can move the default between repository reads.
        # Retry once so build metadata and rows never cross versions.
        if result_page.build_id != summary.build_id:
            summary = get_latest_episode_summary(account_key)
            result_page = get_latest_position_episode_page(
                account_key,
                **page_kwargs,
            )
    else:
        result_page = get_position_episode_page(
            build_id,
            account_key,
            **page_kwargs,
        )
    if summary is None or result_page.build_id != summary.build_id:
        raise HTTPException(
            status_code=503,
            detail="episode build changed during read; retry",
        )

    review_queue = getattr(result_page, "review_queue", None)
    return PositionEpisodeListResponse(
        data_state="ready",
        build=_episode_build_metadata(summary),
        reconciliation=_episode_reconciliation(summary),
        summary=_episode_summary(summary),
        total=result_page.total,
        page=result_page.page,
        per_page=result_page.per_page,
        review_queue=PositionEpisodeReviewQueue(
            pending=getattr(review_queue, "pending", result_page.total),
            in_progress=getattr(review_queue, "in_progress", 0),
            completed=getattr(review_queue, "completed", 0),
            total=getattr(review_queue, "total", result_page.total),
        ),
        items=[
            _position_episode_item(
                item,
                opening_boundary_policy=summary.opening_boundary_policy,
            )
            for item in result_page.items
        ],
    )


@router.get(
    "/v2/position-episodes/{episode_id}",
    response_model=PositionEpisodeDetailResponse,
)
def get_position_episode_v2(
    episode_id: int,
    build_id: Optional[int] = Query(None, ge=1),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> PositionEpisodeDetailResponse:
    """Return default or explicitly selected immutable episode evidence."""
    if build_id is None:
        summary = get_latest_episode_summary(account_key)
    else:
        summary = get_episode_summary(build_id, account_key)
    if summary is None:
        raise HTTPException(status_code=404, detail="episode build not found")
    if build_id is None:
        detail = get_latest_position_episode_detail(
            episode_id=episode_id,
            account_key=account_key,
        )
    else:
        detail = get_position_episode_detail(
            episode_id=episode_id,
            build_id=build_id,
            account_key=account_key,
        )
    if detail is None or detail.episode.build_id != summary.build_id:
        scope = "selected build" if build_id is not None else "latest build"
        raise HTTPException(
            status_code=404,
            detail=f"position episode not found in {scope}",
        )
    return PositionEpisodeDetailResponse(
        data_state="ready",
        build=_episode_build_metadata(summary),
        reconciliation=_episode_reconciliation(summary),
        item=_position_episode_item(
            detail.episode,
            opening_boundary_policy=summary.opening_boundary_policy,
        ),
        matching=_stringify_nested_decimals(dict(detail.matching_evidence)),
        evidence_summary=_stringify_nested_decimals(
            dict(detail.evidence_summary)
        ),
        completeness=_stringify_nested_decimals(dict(detail.completeness)),
        provenance=_stringify_nested_decimals(dict(detail.provenance)),
        evidence=[
            PositionEpisodeEvidenceItem(
                id=item.evidence_id,
                evidence_key=item.evidence_key,
                evidence_kind=item.evidence_kind,
                event_role=item.event_role,
                allocation_sequence=item.allocation_sequence,
                evidence_time=item.evidence_time,
                allocated_quantity=_decimal_text(item.allocated_quantity),
                allocated_fee=_decimal_text(item.allocated_fee),
                allocated_cash_flow=_decimal_text(item.allocated_cash_flow),
                allocation_ratio=_decimal_text(item.allocation_ratio),
                broker_order_observation_id=(
                    item.broker_order_observation_id
                ),
                broker_fill_observation_id=(
                    item.broker_fill_observation_id
                ),
                allocation=_stringify_nested_decimals(
                    dict(item.allocation_evidence)
                ),
                provenance=_stringify_nested_decimals(dict(item.provenance)),
            )
            for item in detail.evidence
        ],
    )


@router.post(
    "/v2/episode-builds/canonical",
    response_model=EpisodeBuildResponse,
)
def create_canonical_position_episode_build(
    request: CanonicalEpisodeBuildConfirmRequest,
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> EpisodeBuildResponse:
    """Append an explicitly confirmed canonical build without activating it."""
    try:
        result = append_canonical_position_episode_build(
            canonical_set_id=request.canonical_set_id,
            expected_canonical_set_sha256=request.canonical_set_sha256,
            expected_build_key=request.build_key,
            account_key=account_key,
            accept_assumed_flat=request.accept_assumed_flat,
            accept_group_fee_scope=request.accept_group_fee_scope,
        )
    except EpisodeRepositoryError as exc:
        status_code = (
            404
            if str(exc) == "canonical evidence set does not exist"
            else 409
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    summary = get_episode_summary(result.build_id, account_key)
    if summary is None or summary.build_id != result.build_id:
        raise HTTPException(
            status_code=500,
            detail="episode build was appended but cannot be read back",
        )
    action = "already present" if result.duplicate else "appended"
    return EpisodeBuildResponse(
        data_state="ready",
        duplicate=result.duplicate,
        build=_episode_build_metadata(summary),
        reconciliation=_episode_reconciliation(summary),
        summary=_episode_summary(summary),
        message=(
            f"canonical position episode build {action}: "
            f"{result.position_episode_count} episodes. "
            "The default position review remains unchanged; use the explicit "
            "build ID to inspect this result."
        ),
    )


@router.get(
    "/v2/episode-builds/activation",
    response_model=EpisodeBuildActivationStateResponse,
)
def get_position_episode_build_activation(
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> EpisodeBuildActivationStateResponse:
    """Return the append-only selection used by default Episode reads."""
    try:
        state = get_episode_build_activation_state(account_key)
    except EpisodeBuildActivationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EpisodeBuildActivationStateResponse(**state.__dict__)


@router.post(
    "/v2/episode-builds/{build_id}/activate",
    response_model=EpisodeBuildActivationResponse,
)
def activate_position_episode_build(
    build_id: int,
    request: EpisodeBuildActivationRequest,
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> EpisodeBuildActivationResponse:
    """Explicitly select one immutable source-linked build as the default view."""
    try:
        result = activate_episode_build(
            build_id,
            request.expected_build_key,
            expected_current_activation_id=(
                request.expected_current_activation_id
            ),
            expected_current_build_id=request.expected_current_build_id,
            accept_assumed_flat=request.accept_assumed_flat,
            accept_group_fee_scope=request.accept_group_fee_scope,
            accept_left_censored_openings=(
                request.accept_left_censored_openings
            ),
            account_key=account_key,
        )
    except EpisodeBuildActivationError as exc:
        status_code = 404 if "does not exist" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    action = "already active" if result.duplicate else "activated"
    kind_label = (
        "snapshot-fence future"
        if result.target_source_kind == SNAPSHOT_FENCE_SOURCE_KIND
        else "canonical"
    )
    return EpisodeBuildActivationResponse(
        activation_id=result.activation_id,
        activation_key=result.activation_key,
        duplicate=result.duplicate,
        state=EpisodeBuildActivationStateResponse(**result.state.__dict__),
        message=(
            f"{kind_label} Episode build {result.state.current_build_id} "
            f"{action}; default review reads now resolve through the "
            "append-only activation."
        ),
        trading_action_performed=False,
    )


@router.get(
    "/v2/episode-builds/canonical/preview",
    response_model=CanonicalEpisodeBuildPlanResponse,
)
def preview_canonical_position_episode_build(
    canonical_set_id: Optional[int] = Query(None, ge=1),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> CanonicalEpisodeBuildPlanResponse:
    """Plan one canonical build without appending any episode rows."""
    try:
        preview = preview_canonical_position_episodes(
            canonical_set_id=canonical_set_id,
            account_key=account_key,
            assume_flat_if_missing=True,
        )
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if preview is None:
        raise HTTPException(status_code=404, detail="canonical evidence set not found")

    default_summary = get_latest_episode_summary(account_key)
    default_count = (
        default_summary.position_episode_count
        if default_summary is not None
        else 0
    )
    planned_count = len(preview.episodes)
    assumption_required = preview.opening_boundary_policy in {
        "assumed_flat_unverified",
        "mixed_explicit_and_assumed",
    }
    warnings = list(preview.partial_reasons)
    if assumption_required:
        warnings.append("explicit assumed-flat acceptance is required")
    execution_group_count = int(
        getattr(preview, "execution_group_count", 0)
    )
    group_fee_affected_episode_count = int(
        getattr(preview, "group_fee_affected_episode_count", 0)
    )
    leg_fee_attribution_complete = bool(
        getattr(preview, "leg_fee_attribution_complete", True)
    )
    retained_execution_group_fee_total = getattr(
        preview,
        "retained_execution_group_fee_total",
        Decimal("0"),
    )
    fee_conservation_by_currency = getattr(
        preview,
        "fee_conservation_by_currency",
        {},
    )
    group_fee_acceptance_required = (
        group_fee_affected_episode_count > 0
        and not leg_fee_attribution_complete
    )
    if group_fee_acceptance_required:
        warnings.append(
            "execution-group fee remains exact only at group scope; affected "
            "leg episodes have no fee/net P&L and require explicit acceptance"
        )
    warnings.append(
        "confirming this plan will not replace the default position review"
    )
    confirm_allowed = (
        preview.fee_conserved
        and preview.unresolved_evidence_count == 0
        and preview.canonical_set_id is not None
        and preview.canonical_set_sha256 is not None
    )
    return CanonicalEpisodeBuildPlanResponse(
        data_state="ready",
        canonical_set_id=preview.canonical_set_id,
        canonical_set_sha256=preview.canonical_set_sha256,
        build_key=preview.build_key,
        source_batch_ids=list(preview.source_batch_ids),
        source_window_start=preview.source_window_start,
        source_window_end=preview.source_cutoff_at,
        source_event_count=preview.source_event_count,
        aggregate_order_event_count=preview.aggregate_order_event_count,
        detailed_fill_event_count=preview.detailed_fill_event_count,
        planned_position_episode_count=planned_count,
        planned_open_episode_count=preview.open_episode_count,
        planned_closed_episode_count=preview.closed_episode_count,
        source_known_fee_total=(
            _decimal_text(preview.source_known_fee_total) or "0"
        ),
        allocated_known_fee_total=(
            _decimal_text(preview.allocated_known_fee_total) or "0"
        ),
        retained_execution_group_fee_total=(
            _decimal_text(retained_execution_group_fee_total) or "0"
        ),
        fee_conservation_by_currency={
            str(currency): {
                str(key): _decimal_text(value) or "0"
                for key, value in values.items()
            }
            for currency, values in (
                fee_conservation_by_currency.items()
            )
        },
        fee_conserved=preview.fee_conserved,
        execution_group_count=execution_group_count,
        group_fee_affected_episode_count=(
            group_fee_affected_episode_count
        ),
        leg_fee_attribution_complete=(
            leg_fee_attribution_complete
        ),
        opening_boundary_policy=preview.opening_boundary_policy,
        requires_assumed_flat_acceptance=assumption_required,
        requires_group_fee_scope_acceptance=(
            group_fee_acceptance_required
        ),
        default_build_id=(
            default_summary.build_id if default_summary is not None else None
        ),
        default_position_episode_count=default_count,
        episode_count_delta=planned_count - default_count,
        default_will_change=False,
        confirm_allowed=confirm_allowed,
        warnings=warnings,
    )


@router.post(
    "/v2/episode-builds",
    response_model=EpisodeBuildResponse,
)
def create_position_episode_build_v2(
    accept_assumed_flat: bool = Query(False),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> EpisodeBuildResponse:
    """Append a build only after explicit acceptance of an assumed boundary."""
    try:
        preview = preview_latest_position_episodes(
            account_key,
            assume_flat_if_missing=True,
        )
        if preview is None:
            raise HTTPException(
                status_code=409,
                detail="no accepted evidence batch exists",
            )
        assumption_used = preview.opening_boundary_policy in {
            "assumed_flat_unverified",
            "mixed_explicit_and_assumed",
        }
        if assumption_used and not accept_assumed_flat:
            raise HTTPException(
                status_code=409,
                detail=(
                    "accept_assumed_flat=true is required because no verified "
                    "opening-position snapshot exists"
                ),
            )
        result = append_latest_position_episode_build(
            account_key,
            accept_assumed_flat=accept_assumed_flat,
        )
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    summary = get_latest_episode_summary(account_key)
    if summary is None or summary.build_id != result.build_id:
        raise HTTPException(
            status_code=500,
            detail="episode build was appended but cannot be read back",
        )
    action = "already present" if result.duplicate else "appended"
    return EpisodeBuildResponse(
        data_state="ready",
        duplicate=result.duplicate,
        build=_episode_build_metadata(summary),
        reconciliation=_episode_reconciliation(summary),
        summary=_episode_summary(summary),
        message=(
            f"position episode build {action}: "
            f"{result.position_episode_count} episodes; "
            f"opening boundary={result.opening_boundary_policy}."
        ),
    )


@router.post(
    "/v2/imports/preview",
    response_model=MoomooStatementPreviewResponse,
)
async def preview_moomoo_statement(
    file: UploadFile = File(...),
) -> MoomooStatementPreviewResponse:
    """Inspect Moomoo CSV coverage without opening or writing the database."""
    content = await _read_upload_limited(file, _MAX_CSV_UPLOAD_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        statement = parse_statement(content)
    except MoomooStatementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    summary = statement.summary()
    warnings = list(summary["warnings"])
    if summary["orders_total"] == 0:
        analysis_level = "blocked"
        warnings.append("no_order_rows")
    elif summary["inconsistent_filled_orders"] or summary["orphan_fill_rows"]:
        analysis_level = "blocked"
    elif summary["aggregate_only_filled_orders"]:
        analysis_level = "partial"
        warnings.append("older_filled_orders_have_aggregate_evidence_only")
    else:
        analysis_level = "exact"

    response_data = dict(summary)
    response_data.update(
        {
            "analysis_level": analysis_level,
            "warnings": warnings,
            "journal_database_written": False,
        }
    )
    return MoomooStatementPreviewResponse(**response_data)


@router.post(
    "/v2/refreshes/preview",
    response_model=MoomooJournalRefreshPreviewResponse,
)
async def preview_moomoo_journal_refresh(
    request: MoomooJournalRefreshRequest,
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> MoomooJournalRefreshPreviewResponse:
    """Query OpenD read-only and freeze the exact server-owned import plan."""
    try:
        config = _journal_refresh_probe_config(
            account_key=account_key,
            overlap_days=request.overlap_days,
        )
        result = await run_in_threadpool(run_readonly_probe, config)
        payload = result.export_payload
        preview = parse_openapi_export(payload)
        expected_selection = "explicit" if config.acc_id is not None else "unique_auto"
        if (
            preview.metadata.window_start.astimezone(timezone.utc)
            != config.start.astimezone(timezone.utc)
            or preview.metadata.window_end.astimezone(timezone.utc)
            != config.end.astimezone(timezone.utc)
            or preview.metadata.account_selection != expected_selection
        ):
            raise JournalRefreshError(
                "OpenD refresh result does not match the server-owned query scope"
            )
        plan = plan_openapi_import(preview, payload, account_key=account_key)
        artifact = save_refresh_artifact(
            preview,
            payload,
            plan,
            account_key=account_key,
        )
    except JournalRefreshError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MoomooReadonlyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (MoomooOpenApiExportError, OpenApiPlanError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    latest_fill_at = max(
        (item.filled_at for item in preview.fills),
        default=None,
    )
    observation_counts = _openapi_observation_counts(preview)
    return MoomooJournalRefreshPreviewResponse(
        artifact_id=artifact.artifact_id,
        artifact_key=artifact.artifact_key,
        expires_at=artifact.expires_at,
        source=MoomooJournalRefreshSource(
            retrieval_complete=preview.metadata.retrieval_complete,
            coverage_complete=preview.metadata.coverage_complete,
            has_activity=preview.metadata.has_activity,
            broker_queried_through=preview.metadata.window_end,
            latest_fill_at=latest_fill_at,
            **observation_counts,
        ),
        plan=MoomooOpenApiPlanResponse(**plan.as_dict()),
        evidence_written=False,
        trading_action_performed=False,
    )


@router.post(
    "/v2/refreshes/{artifact_id}/confirm",
    response_model=MoomooJournalRefreshConfirmResponse,
)
def confirm_moomoo_journal_refresh(
    artifact_id: int,
    request: MoomooJournalRefreshConfirmRequest,
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> MoomooJournalRefreshConfirmResponse:
    """Publish the exact frozen artifact after an explicit user confirmation."""
    try:
        confirmation = confirm_refresh_artifact(
            artifact_id,
            preview_key=request.preview_key,
            acknowledge_partial_window=request.acknowledge_partial_window,
            account_key=account_key,
        )
    except JournalRefreshError as exc:
        status_code = 404 if "does not exist" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    publication = confirmation.publication
    return MoomooJournalRefreshConfirmResponse(
        artifact_id=confirmation.artifact.artifact_id,
        publication=MoomooJournalRefreshPublication(
            publication_id=publication.publication_id,
            broker_queried_through=publication.broker_queried_through,
            latest_fill_at=publication.latest_fill_at,
            evidence_published_through=publication.evidence_published_through,
            recorded_at=publication.recorded_at,
        ),
        imported=MoomooOpenApiConfirmResponse(
            **confirmation.import_result.__dict__
        ),
        trading_action_performed=False,
    )


@router.post(
    "/v2/openapi-imports/preview",
    response_model=MoomooOpenApiPreviewResponse,
)
async def preview_moomoo_openapi_export(
    file: UploadFile = File(...),
) -> MoomooOpenApiPreviewResponse:
    """Validate a de-identified read-only export without opening the DB."""
    content = await _read_upload_limited(file, _MAX_OPENAPI_EXPORT_BYTES)
    _, preview = _parse_openapi_content(content)

    metadata = preview.metadata
    observation_counts = _openapi_observation_counts(preview)
    order_currency = {
        order.source_order_id: order.currency for order in preview.orders
    }
    fee_totals: dict[str, Decimal] = {}
    for fee in preview.fees:
        currency = order_currency[fee.source_order_id]
        fee_totals[currency] = (
            fee_totals.get(currency, Decimal("0")) + fee.total_fee
        )
    return MoomooOpenApiPreviewResponse(
        source_schema=metadata.source_schema,
        parser_name=metadata.parser_name,
        parser_version=metadata.parser_version,
        source_sha256=metadata.source_sha256,
        evidence_sha256=metadata.evidence_sha256,
        batch_key=metadata.batch_key,
        environment=metadata.environment,
        market=metadata.market,
        analysis_level=metadata.analysis_level,
        analysis_ready=metadata.analysis_ready,
        reconciliation_status=metadata.reconciliation_status,
        reconciliation=metadata.reconciliation.as_dict(),
        warnings=list(metadata.warnings),
        window_start=metadata.window_start,
        window_end=metadata.window_end,
        source_timezone=metadata.source_timezone,
        **observation_counts,
        fee_totals_by_currency={
            currency: format(amount, "f")
            for currency, amount in sorted(fee_totals.items())
        },
        journal_database_written=False,
    )


@router.post(
    "/v2/openapi-imports/plan",
    response_model=MoomooOpenApiPlanResponse,
)
async def plan_moomoo_openapi_export(
    file: UploadFile = File(...),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> MoomooOpenApiPlanResponse:
    """Plan cross-source links and canonical impact without writing rows."""
    content = await _read_upload_limited(file, _MAX_OPENAPI_EXPORT_BYTES)
    payload, preview = _parse_openapi_content(content)
    try:
        plan = plan_openapi_import(preview, payload, account_key=account_key)
    except OpenApiPlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MoomooOpenApiPlanResponse(**plan.as_dict())


@router.post(
    "/v2/openapi-imports/confirm",
    response_model=MoomooOpenApiConfirmResponse,
)
async def confirm_moomoo_openapi_export(
    file: UploadFile = File(...),
    preview_key: str = Query(..., min_length=64, max_length=64),
    acknowledge_partial_window: bool = Query(False),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> MoomooOpenApiConfirmResponse:
    """Append the exact acknowledged plan in one immutable transaction."""
    content = await _read_upload_limited(file, _MAX_OPENAPI_EXPORT_BYTES)
    payload, preview = _parse_openapi_content(content)
    try:
        result = confirm_openapi_import_plan(
            preview,
            payload,
            preview_key=preview_key,
            acknowledge_partial_window=acknowledge_partial_window,
            account_key=account_key,
        )
    except OpenApiPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MoomooOpenApiConfirmResponse(**result.__dict__)


@router.post("/v2/imports", response_model=LedgerImportResponse)
async def import_moomoo_statement_v2(
    file: UploadFile = File(...),
    allow_partial: bool = Query(False),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> LedgerImportResponse:
    """Append one confirmed CSV snapshot to the isolated evidence ledger."""
    content = await _read_upload_limited(file, _MAX_CSV_UPLOAD_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        statement = parse_statement(content)
        result = import_statement_batch(
            statement,
            account_key=account_key,
            allow_partial=allow_partial,
        )
    except MoomooStatementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LedgerImportError as exc:
        status_code = 409 if "explicitly allow" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    action = "already present" if result.duplicate else "appended"
    return LedgerImportResponse(
        batch_id=result.batch_id,
        duplicate=result.duplicate,
        analysis_level=result.analysis_level,
        order_observations=result.order_observations,
        fill_observations=result.fill_observations,
        legacy_journal_written=False,
        message=(
            f"evidence batch {action}: {result.order_observations} orders, "
            f"{result.fill_observations} fills; legacy Journal unchanged."
        ),
    )


@router.post("/import", response_model=ImportResponse)
async def import_csv(
    file: UploadFile = File(...),
    broker: str = Query("moomoo_us"),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> ImportResponse:
    if broker not in _LEGACY_BROKERS:
        raise HTTPException(status_code=400, detail=f"unknown broker: {broker}")

    content = await _read_upload_limited(file, _MAX_CSV_UPLOAD_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    raise HTTPException(
        status_code=410,
        detail=(
            "legacy Moomoo CSV import is disabled because it can discard "
            "aggregate-only executions and rebuild FIFO from incomplete "
            "evidence; use /v2/imports/preview then /v2/imports"
        ),
    )


# --- monthly review ----------------------------------------------------------


@router.get("/reviews", response_model=MonthlyReviewListResponse)
def list_reviews(portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL)) -> MonthlyReviewListResponse:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = (
            session.execute(
                select(JournalMonthlyReview)
                .where(JournalMonthlyReview.portfolio_label == portfolio)
                .order_by(JournalMonthlyReview.year_month.desc())
            )
            .scalars()
            .all()
        )
        items = [
            MonthlyReviewItem(
                year_month=r.year_month,
                current_phase=int(r.current_phase or 0),
                review_markdown=r.review_markdown,
                generated_at=r.generated_at,
            )
            for r in rows
        ]
        return MonthlyReviewListResponse(count=len(items), items=items)


@router.get("/reviews/{year}/{month}", response_model=Optional[MonthlyReviewItem])
def get_review(year: int, month: int, portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL)):
    init_journal_schema()
    db = get_db()
    ym = f"{year:04d}-{month:02d}"
    with db.session_scope() as session:
        row = (
            session.execute(
                select(JournalMonthlyReview).where(
                    JournalMonthlyReview.portfolio_label == portfolio,
                    JournalMonthlyReview.year_month == ym,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        return MonthlyReviewItem(
            year_month=row.year_month,
            current_phase=int(row.current_phase or 0),
            review_markdown=row.review_markdown,
            generated_at=row.generated_at,
        )


@router.post("/reviews/{year}/{month}/generate", response_model=MonthlyReviewItem)
def generate_review_endpoint(
    year: int,
    month: int,
    payload: MonthlyReviewGenerateRequest,
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
):
    from src.journal.monthly_review import run as run_review

    ym = f"{year:04d}-{month:02d}"
    try:
        run_review(ym, portfolio=portfolio, dry_run=payload.dry_run)
    except Exception as exc:  # noqa: BLE001
        logger.exception("monthly review generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result = get_review(year, month, portfolio)  # type: ignore[arg-type]
    if result is None:
        raise HTTPException(status_code=500, detail="review missing after generation")
    return result


# --- stats-by-style + QA -----------------------------------------------------


_DTE_BUCKET_ORDER = ["0DTE", "1-3DTE", "4-7DTE", "8-30DTE", "30+DTE", "equity", "unknown"]


def _filter_trades_by_window(
    trades: list[dict],
    start_date: Optional[date],
    end_date: Optional[date],
) -> list[dict]:
    out = []
    for t in trades:
        ref = t.get("exit_time") or t.get("entry_time")
        if ref is None:
            continue
        ref_date = ref.date() if isinstance(ref, datetime) else ref
        if start_date and ref_date < start_date:
            continue
        if end_date and ref_date > end_date:
            continue
        out.append(t)
    return out


def _compact_trade_for_api(t: dict) -> dict:
    """Minimal trade row for `worst_trades` / `best_trades` transport."""
    return {
        "id": t.get("id"),
        "underlying": t.get("underlying"),
        "direction": t.get("direction"),
        "is_option": t.get("is_option"),
        "dte_bucket": t.get("dte_bucket"),
        "trade_style": t.get("trade_style"),
        "pnl_net": t.get("pnl_net"),
        "pnl_pct": t.get("pnl_pct"),
        "hold_seconds": t.get("hold_seconds"),
        "entry_time": t.get("entry_time").isoformat() if t.get("entry_time") else None,
        "exit_time": t.get("exit_time").isoformat() if t.get("exit_time") else None,
    }


@router.get("/stats-by-style", response_model=JournalStatsByStyleResponse)
def get_stats_by_style(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    top_n: int = Query(5, ge=1, le=20),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> JournalStatsByStyleResponse:
    """P&L breakdown by trade_style + DTE bucket across an optional date window."""
    trades = _load_trades(portfolio)
    trades = _filter_trades_by_window(trades, start_date, end_date)

    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None]

    by_style_rows = stats_by_style(closed)
    dte_win = dte_bucket_win_rates(closed)
    by_dte_rows = []
    for bucket in _DTE_BUCKET_ORDER:
        info = dte_win.get(bucket)
        if not info or not info.get("count"):
            continue
        by_dte_rows.append(
            {
                "bucket": bucket,
                "count": int(info["count"]),
                "win_rate": float(info.get("win_rate") or 0.0),
                "avg_pnl_net": float(info.get("avg_pnl_net") or 0.0),
                "sum_pnl_net": float(info.get("sum_pnl_net") or 0.0),
            }
        )

    worst = sorted(closed, key=lambda t: t["pnl_net"])[: top_n]
    best = sorted(closed, key=lambda t: t["pnl_net"], reverse=True)[: top_n]

    return JournalStatsByStyleResponse(
        period={
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
        },
        total_count=len(closed),
        total_pnl_net=float(sum(t["pnl_net"] for t in closed)) if closed else 0.0,
        by_style=by_style_rows,
        by_dte=by_dte_rows,
        worst_trades=[_compact_trade_for_api(t) for t in worst],
        best_trades=[_compact_trade_for_api(t) for t in best],
    )


@router.post("/qa", response_model=JournalQaResponse)
def journal_qa(payload: JournalQaRequest) -> JournalQaResponse:
    """Single-turn LLM Q&A over the user's trade journal, anchored on their framework."""
    from src.services.journal_qa_service import generate_answer

    portfolio = DEFAULT_PORTFOLIO_LABEL
    since = date.today() - timedelta(days=payload.trade_window_days)
    trades = _load_trades(portfolio, since=since)
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None]
    closed.sort(key=lambda t: t.get("exit_time") or t.get("entry_time") or datetime.min, reverse=True)
    closed = closed[: payload.trade_limit]

    try:
        answer, fw_hash = generate_answer(
            framework=payload.framework,
            question=payload.question,
            trades=closed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("journal qa failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JournalQaResponse(
        answer=answer,
        trades_considered=len(closed),
        framework_hash=fw_hash,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


# --- moomoo live sync (Phase B) ---------------------------------------------


@router.post("/sync-live", response_model=MoomooSyncResponse)
def sync_live(payload: MoomooSyncRequest) -> MoomooSyncResponse:
    """Refuse the legacy journal writer until its facts are fully reconciled.

    The old implementation selected an account by position, omitted fees and
    wrote directly into the FIFO journal.  It remains intentionally unavailable
    while the isolated read-only export is promoted into a provenance-aware
    importer.  This endpoint never opens an SDK context or writes the database.
    """
    del payload
    raise HTTPException(
        status_code=409,
        detail=(
            "Legacy Moomoo journal sync is paused. Use the bounded read-only "
            "probe; import remains disabled until account, fill, price and fee "
            "reconciliation passes."
        ),
    )
