# -*- coding: utf-8 -*-
"""Explicit, append-only user reviews for immutable PositionEpisodes."""
from __future__ import annotations

import time
from dataclasses import asdict
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.journal_reviews import (
    PersonalEdgeDisciplineModel,
    PersonalEdgeDisciplineMonthModel,
    PersonalEdgeDisciplineWindowModel,
    PersonalEdgeDteBucketModel,
    PersonalEdgeHoldBucketModel,
    PersonalEdgeMonthlyBucketModel,
    PersonalEdgeResponse,
    PersonalEdgeRuleComplianceModel,
    PersonalEdgeUnderlyingModel,
    PlaybookCandidateCreateRequest,
    PlaybookCandidateCreateResponse,
    PlaybookCandidateItem,
    PlaybookEpisodeLinkItem,
    PlaybookEpisodeLinksResponse,
    PlaybookListResponse,
    PlaybookRuleItem,
    PlaybookRulePromoteRequest,
    PlaybookRulePromoteResponse,
    PlaybookRuleRetireRequest,
    PlaybookRuleRetireResponse,
    PlaybookSourceBucketModel,
    ReviewAnnotationCreateRequest,
    ReviewAnnotationCreateResponse,
    ReviewAnnotationHistoryResponse,
    ReviewAnnotationItem,
    ReviewAnnotationLatestResponse,
    ReviewInsightBucketModel,
    ReviewInsightStatsGate,
    ReviewInsightStatsModel,
    ReviewInsightThresholds,
    ReviewInsightUnreviewed,
    ReviewInsightsResponse,
    RuleComplianceDailyBudgetModel,
    RuleComplianceLaneStatModel,
    RuleComplianceSliceModel,
    RuleComplianceStatModel,
    RulesEvidenceChosenParamsModel,
    RulesEvidenceCorrelationModel,
    RulesEvidenceDteHoldModel,
    RulesEvidenceFeeThresholdModel,
    RulesEvidenceHourModel,
    RulesEvidencePositionModel,
    RulesEvidencePriceBandModel,
    RulesEvidenceResponse,
    RulesEvidenceWeekdayModel,
)
from src.journal.ledger.episode_repository import EpisodeRepositoryError
from src.journal.ledger.playbook_repository import (
    PlaybookConflictError,
    PlaybookEpisodeLink,
    PlaybookRepositoryError,
    PlaybookSourceBucket,
    StoredPlaybookCandidate,
    StoredPlaybookRule,
    create_playbook_candidate,
    list_playbook_candidates,
    list_playbook_links_for_episode,
    list_playbook_rules,
    promote_candidate_to_rule,
    retire_playbook_rule,
)
from src.journal.ledger.repository import DEFAULT_LEDGER_ACCOUNT_KEY
from src.journal.personal_edge import (
    PersonalEdgeResult,
    RULE_SET_V2_ADOPTED_AT,
    RuleComplianceSlice,
    get_personal_edge_stats,
)
from src.journal.rules_evidence import (
    RulesEvidenceResult,
    get_rules_evidence,
)
from src.journal.ledger.review_insights import (
    DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT,
    DEFAULT_MIN_EPISODE_COUNT,
    ReviewInsightBucket,
    get_latest_review_insights,
)
from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    ReviewAnnotationRepositoryError,
    ReviewAnnotationScopeNotFoundError,
    StoredReviewAnnotation,
    append_review_annotation,
    get_latest_review_annotation,
    list_review_annotation_history,
)


router = APIRouter()


def _item(value: StoredReviewAnnotation) -> ReviewAnnotationItem:
    return ReviewAnnotationItem(
        id=value.annotation_id,
        account_key=value.account_key,
        episode_build_id=value.episode_build_id,
        position_episode_id=value.position_episode_id,
        revision=value.revision,
        review_status=value.review_status,
        setup_thesis=value.setup_thesis,
        entry_trigger=value.entry_trigger,
        invalidation_plan=value.invalidation_plan,
        position_rationale=value.position_rationale,
        exit_reason=value.exit_reason,
        post_trade_reflection=value.post_trade_reflection,
        tags=list(value.tags),
        error_types=list(value.error_types),
        content_sha256=value.content_sha256,
        previous_annotation_id=value.previous_annotation_id,
        created_at=value.created_at,
    )


def _scope_error(exc: ReviewAnnotationRepositoryError) -> HTTPException:
    if isinstance(exc, ReviewAnnotationScopeNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _insight_bucket(bucket: ReviewInsightBucket) -> ReviewInsightBucketModel:
    stats = None
    if bucket.stats is not None:
        stats = ReviewInsightStatsModel(
            win_rate=format(bucket.stats.win_rate, "f"),
            avg_pnl=format(bucket.stats.avg_pnl, "f"),
            sum_pnl=format(bucket.stats.sum_pnl, "f"),
            win_count=bucket.stats.win_count,
            loss_count=bucket.stats.loss_count,
            breakeven_count=bucket.stats.breakeven_count,
        )
    return ReviewInsightBucketModel(
        group_kind=bucket.group_kind,
        group_value=bucket.group_value,
        direction=bucket.direction,
        boundary_policy=bucket.boundary_policy,
        episode_count=bucket.episode_count,
        distinct_trading_day_count=bucket.distinct_trading_day_count,
        review_completed_count=bucket.review_completed_count,
        verified_episode_count=bucket.verified_episode_count,
        verified_distinct_trading_day_count=(
            bucket.verified_distinct_trading_day_count
        ),
        conditional_episode_count=bucket.conditional_episode_count,
        stats=stats,
        stats_gate=ReviewInsightStatsGate(
            eligible=bucket.stats_gate_eligible,
            reason=bucket.stats_gate_reason,
        ),
    )


@router.get("/v2/review-insights", response_model=ReviewInsightsResponse)
def get_review_insights(
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewInsightsResponse:
    """Zero-write L1 pattern observation over the current default build.

    Buckets join the default build's episodes with each episode's latest
    review annotation.  Win-rate style ratios fail closed below the sample
    threshold; conditional-P&L members never enter the stats.
    """
    thresholds = ReviewInsightThresholds(
        min_episode_count=DEFAULT_MIN_EPISODE_COUNT,
        min_distinct_trading_day_count=(
            DEFAULT_MIN_DISTINCT_TRADING_DAY_COUNT
        ),
    )
    try:
        result = get_latest_review_insights(account_key)
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        return ReviewInsightsResponse(
            data_state="not_built",
            account_key=account_key,
            thresholds=thresholds,
            total_episode_count=0,
            annotated_episode_count=0,
        )
    return ReviewInsightsResponse(
        data_state="ready",
        build_id=result.build_id,
        build_key=result.build_key,
        source_kind=result.source_kind,
        account_key=result.account_key,
        generated_at=result.generated_at,
        thresholds=ReviewInsightThresholds(
            min_episode_count=result.min_episode_count,
            min_distinct_trading_day_count=(
                result.min_distinct_trading_day_count
            ),
        ),
        total_episode_count=result.total_episode_count,
        annotated_episode_count=result.annotated_episode_count,
        unreviewed=ReviewInsightUnreviewed(
            episode_count=result.unreviewed.episode_count,
            distinct_trading_day_count=(
                result.unreviewed.distinct_trading_day_count
            ),
        ),
        buckets=[_insight_bucket(bucket) for bucket in result.buckets],
    )


# --- personal edge (个人画像回灌): zero-write descriptive stats --------------


# In-process TTL cache: episodes are append-only per build, so a short cache
# is safe; the response keeps computed_at so the as-of moment stays honest.
_PERSONAL_EDGE_CACHE_TTL_SECONDS = 600.0
# Key is (account_key, rule-compliance `since`): the forward slice is the only
# request-controlled input, so it must not share a cache entry with another one.
_personal_edge_cache: dict[tuple[str, str], tuple[float, PersonalEdgeResponse]] = {}
_personal_edge_cache_lock = Lock()


def _reset_personal_edge_cache() -> None:
    """Test hook: drop every cached personal-edge response."""
    with _personal_edge_cache_lock:
        _personal_edge_cache.clear()


# --- 交易纪律证据页（/rules）: same clean basis, same zero-write contract ----
_RULES_EVIDENCE_CACHE_TTL_SECONDS = 600.0
# Key is (account_key, build_id, ticket_usd, daily_breaker_usd, max_concurrent):
# every request-controlled input changes the arithmetic, so none of them may
# share a cache entry with another value.
_rules_evidence_cache: dict[
    tuple[str, Optional[int], int, int, int],
    tuple[float, RulesEvidenceResponse],
] = {}
_rules_evidence_cache_lock = Lock()


def _reset_rules_evidence_cache() -> None:
    """Test hook: drop every cached rules-evidence response."""
    with _rules_evidence_cache_lock:
        _rules_evidence_cache.clear()


def _compliance_slice(slice_: RuleComplianceSlice) -> RuleComplianceSliceModel:
    """Project one compliance slice; ``asdict`` keeps the dataclass the truth."""
    return RuleComplianceSliceModel(
        state=slice_.state,
        state_reason=slice_.state_reason,
        start_date=slice_.start_date,
        n=slice_.n,
        lanes=[RuleComplianceLaneStatModel(**asdict(lane)) for lane in slice_.lanes],
        verdicts=[
            RuleComplianceStatModel(**asdict(verdict))
            for verdict in slice_.verdicts
        ],
    )


def _personal_edge_response(
    account_key: str,
    result: PersonalEdgeResult,
) -> PersonalEdgeResponse:
    return PersonalEdgeResponse(
        data_state="ready",
        account_key=result.account_key or account_key,
        build_id=result.build_id,
        build_key=result.build_key,
        source_kind=result.source_kind,
        computed_at=result.computed_at,
        first_opened_at=result.first_opened_at,
        last_closed_at=result.last_closed_at,
        closed_episode_count=result.closed_episode_count,
        excluded_open_count=result.excluded_open_count,
        excluded_missing_pnl_count=result.excluded_missing_pnl_count,
        underlying_min_episode_count=result.underlying_min_episode_count,
        underlyings=[
            PersonalEdgeUnderlyingModel(
                underlying=item.underlying,
                n=item.n,
                net=item.net,
                win_rate=item.win_rate,
                fees=item.fees,
            )
            for item in result.underlyings
        ],
        small_sample_underlying_count=result.small_sample_underlying_count,
        hold_time_buckets=[
            PersonalEdgeHoldBucketModel(
                bucket=item.bucket,
                n=item.n,
                net=item.net,
                win_rate=item.win_rate,
                avg_win=item.avg_win,
                avg_loss=item.avg_loss,
            )
            for item in result.hold_time_buckets
        ],
        hold_unknown_count=result.hold_unknown_count,
        dte_buckets=[
            PersonalEdgeDteBucketModel(
                bucket=item.bucket,
                n=item.n,
                net=item.net,
                win_rate=item.win_rate,
            )
            for item in result.dte_buckets
        ],
        dte_unknown=PersonalEdgeDteBucketModel(
            bucket=result.dte_unknown.bucket,
            n=result.dte_unknown.n,
            net=result.dte_unknown.net,
            win_rate=result.dte_unknown.win_rate,
        ),
        monthly=[
            PersonalEdgeMonthlyBucketModel(
                month=item.month,
                n=item.n,
                net=item.net,
                fees=item.fees,
                win_rate=item.win_rate,
            )
            for item in result.monthly
        ],
        discipline=PersonalEdgeDisciplineModel(
            monthly=[
                PersonalEdgeDisciplineMonthModel(
                    month=item.month,
                    **asdict(item.stats),
                )
                for item in result.discipline.monthly
            ],
            current_window=PersonalEdgeDisciplineWindowModel(
                requested_trading_days=(
                    result.discipline.current_window.requested_trading_days
                ),
                start_date=result.discipline.current_window.start_date,
                end_date=result.discipline.current_window.end_date,
                **asdict(result.discipline.current_window.stats),
            ),
            body_trim_count=result.discipline.body_trim_count,
            body_min_episode_count=result.discipline.body_min_episode_count,
            exclude_top_n=result.discipline.exclude_top_n,
            fill_detailed_governs=result.discipline.fill_detailed_governs,
        ),
        rule_compliance=PersonalEdgeRuleComplianceModel(
            rule_set_id=result.rule_compliance.rule_set_id,
            adopted_at=result.rule_compliance.adopted_at,
            clean_basis_start=result.rule_compliance.clean_basis_start,
            clean_basis_reason=result.rule_compliance.clean_basis_reason,
            population_n=result.rule_compliance.population_n,
            excluded_before_clean_basis_count=(
                result.rule_compliance.excluded_before_clean_basis_count
            ),
            excluded_aggregate_or_unknown_basis_count=(
                result.rule_compliance.excluded_aggregate_or_unknown_basis_count
            ),
            excluded_missing_premium_count=(
                result.rule_compliance.excluded_missing_premium_count
            ),
            exclude_top_n=result.rule_compliance.exclude_top_n,
            exclude_top_n_min_episode_count=(
                result.rule_compliance.exclude_top_n_min_episode_count
            ),
            intraday_lane_dte=result.rule_compliance.intraday_lane_dte,
            intraday_lane_et_cutoff_hour=(
                result.rule_compliance.intraday_lane_et_cutoff_hour
            ),
            overnight_lane_min_dte=result.rule_compliance.overnight_lane_min_dte,
            overnight_lane_max_dte=result.rule_compliance.overnight_lane_max_dte,
            overnight_lane_weak_entry_et_hours=list(
                result.rule_compliance.overnight_lane_weak_entry_et_hours
            ),
            all_history=_compliance_slice(result.rule_compliance.all_history),
            since_adoption=_compliance_slice(
                result.rule_compliance.since_adoption
            ),
            daily_budget=RuleComplianceDailyBudgetModel(
                **asdict(result.rule_compliance.daily_budget)
            ),
            limitations=list(result.rule_compliance.limitations),
        ),
        month_basis=result.month_basis,
        limitations=list(result.limitations),
    )


@router.get("/v2/personal-edge", response_model=PersonalEdgeResponse)
def get_personal_edge(
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
    since: str = Query(
        RULE_SET_V2_ADOPTED_AT,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=(
            "车道遵守度前向切片的起点（ET 自然日）；默认＝规则 v2 采纳日。"
            "只影响 rule_compliance.since_adoption，不影响任何既有字段。"
        ),
    ),
) -> PersonalEdgeResponse:
    """个人画像回灌：当前默认 build 已平仓回合的零写描述统计。

    Per-underlying / hold-time / DTE / monthly buckets plus the additive
    ``discipline`` block (规模与频率：每美元回报 + 仓位 + 频率 + 本体/尾部 +
    成交明细来源，按月与近 20 个交易日窗口) and the additive ``rule_compliance``
    block (车道遵守度：把本人规则 v2 的车道判定与合规/违规每美元读数按干净口径
    摊开，全历史 + 采纳后两个切片) recomputed from the same effective default
    build the other journal reads use, cached in-process for ~10 minutes per
    ``(account_key, since)``.  Descriptive only — never a signal, never a
    filter, and no order is ever placed; the endogeneity, reconstructed-fill,
    single-regime and fail-closed caveats ship verbatim in ``limitations``.
    """
    now = time.monotonic()
    cache_key = (account_key, since)
    with _personal_edge_cache_lock:
        cached = _personal_edge_cache.get(cache_key)
        if cached is not None and now - cached[0] < _PERSONAL_EDGE_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        result = get_personal_edge_stats(account_key, rule_compliance_since=since)
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        # 未构建不进缓存：导入后第一次构建完成即可立刻看到数据。
        return PersonalEdgeResponse(
            data_state="not_built",
            account_key=account_key,
        )
    response = _personal_edge_response(account_key, result)
    with _personal_edge_cache_lock:
        _personal_edge_cache[cache_key] = (now, response)
    return response


def _rules_evidence_response(
    account_key: str,
    result: RulesEvidenceResult,
) -> RulesEvidenceResponse:
    """Transport shell only — every number is already computed in the reader."""

    return RulesEvidenceResponse(
        data_state="ready",
        account_key=result.account_key or account_key,
        build_id=result.build_id,
        build_key=result.build_key,
        source_kind=result.source_kind,
        computed_at=result.computed_at,
        clean_basis_start=result.clean_basis_start,
        clean_basis_reason=result.clean_basis_reason,
        rule_set_adopted_at=result.rule_set_adopted_at,
        sample_episode_count=result.sample_episode_count,
        excluded_before_clean_basis=result.excluded_before_clean_basis,
        excluded_aggregate_or_unknown_basis=(
            result.excluded_aggregate_or_unknown_basis
        ),
        excluded_missing_premium=result.excluded_missing_premium,
        excluded_not_closed_or_missing_pnl=(
            result.excluded_not_closed_or_missing_pnl
        ),
        first_trading_day=result.first_trading_day,
        last_trading_day=result.last_trading_day,
        banner=result.banner,
        price_band_headline=result.price_band_headline,
        price_band_boundary_policy=result.price_band_boundary_policy,
        price_bands=[
            RulesEvidencePriceBandModel(**asdict(row)) for row in result.price_bands
        ],
        hold_style_basis=result.hold_style_basis,
        dte_hold_lanes=[
            RulesEvidenceDteHoldModel(**asdict(row)) for row in result.dte_hold_lanes
        ],
        et_hours=[RulesEvidenceHourModel(**asdict(row)) for row in result.et_hours],
        weekday_headline=result.weekday_headline,
        weekdays=[
            RulesEvidenceWeekdayModel(**asdict(row)) for row in result.weekdays
        ],
        fee_threshold=RulesEvidenceFeeThresholdModel(
            **asdict(result.fee_threshold)
        ),
        position=RulesEvidencePositionModel(**asdict(result.position)),
        correlation=RulesEvidenceCorrelationModel(**asdict(result.correlation)),
        overnight_gap_note=result.overnight_gap_note,
        limitations=list(result.limitations),
    )


@router.get("/v2/rules-evidence", response_model=RulesEvidenceResponse)
def get_rules_evidence_endpoint(
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
    build_id: Optional[int] = Query(
        None,
        ge=1,
        description=(
            "显式指定要读的 episode build；缺省走与其余 journal 读数相同的默认解析"
            "（已激活 build，无激活记录时回落到最新 CSV build）。响应始终回显 "
            "build_id/build_key，页面据此显示「这页在读哪个 build」。"
        ),
    ),
    ticket_usd: int = Query(
        3000,
        ge=1,
        le=1_000_000,
        description="你选择的单笔金额（用于熔断触发算术）；不落库、不改变样本。",
    ),
    daily_breaker_usd: int = Query(
        6000,
        ge=1,
        le=10_000_000,
        description="你选择的每日熔断额度；不落库、不改变样本。",
    ),
    max_concurrent: int = Query(
        2,
        ge=1,
        le=100,
        description="你选择的最大并发持仓数；仅用于相关性提醒的文案。",
    ),
) -> RulesEvidenceResponse:
    """「交易纪律」页的证据读数：与车道遵守度同一干净口径的零写聚合。

    合约价格甜蜜区 / DTE × 持有方式 / 时段 / 星期 × 0DTE 可用性 / 手续费门槛 /
    仓位与回撤算术 / 相关性簇，全部在后端算完并按 ``(account_key, build_id,
    参数)`` 缓存 ~10 分钟。纯描述统计——不是建议、不是信号、不参与任何排序或
    下单；单一 regime、样本内拟合与 fail-closed 的告警随 ``limitations`` 原样下发。
    """
    now = time.monotonic()
    cache_key = (account_key, build_id, ticket_usd, daily_breaker_usd, max_concurrent)
    with _rules_evidence_cache_lock:
        cached = _rules_evidence_cache.get(cache_key)
        if cached is not None and now - cached[0] < _RULES_EVIDENCE_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        result = get_rules_evidence(
            account_key,
            build_id=build_id,
            ticket_usd=ticket_usd,
            daily_breaker_usd=daily_breaker_usd,
            max_concurrent=max_concurrent,
        )
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        # 未构建不进缓存：导入后第一次构建完成即可立刻看到数据。
        return RulesEvidenceResponse(
            data_state="not_built",
            account_key=account_key,
        )
    response = _rules_evidence_response(account_key, result)
    with _rules_evidence_cache_lock:
        _rules_evidence_cache[cache_key] = (now, response)
    return response


@router.get(
    "/v2/position-episodes/{episode_id}/review-annotations/latest",
    response_model=ReviewAnnotationLatestResponse,
)
def get_latest_position_episode_review(
    episode_id: int,
    build_id: int = Query(..., ge=1),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewAnnotationLatestResponse:
    """Return the latest revision without treating no review as an error."""

    try:
        annotation = get_latest_review_annotation(
            account_key=account_key,
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )
    except ReviewAnnotationRepositoryError as exc:
        raise _scope_error(exc) from exc
    if annotation is None:
        return ReviewAnnotationLatestResponse(
            data_state="not_started",
            annotation=None,
        )
    return ReviewAnnotationLatestResponse(
        data_state="ready",
        annotation=_item(annotation),
    )


@router.get(
    "/v2/position-episodes/{episode_id}/review-annotations",
    response_model=ReviewAnnotationHistoryResponse,
)
def get_position_episode_review_history(
    episode_id: int,
    build_id: int = Query(..., ge=1),
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewAnnotationHistoryResponse:
    """Return immutable revisions newest first."""

    try:
        items = list_review_annotation_history(
            account_key=account_key,
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )
    except ReviewAnnotationRepositoryError as exc:
        raise _scope_error(exc) from exc
    return ReviewAnnotationHistoryResponse(
        data_state="ready" if items else "not_started",
        total=len(items),
        items=[_item(item) for item in items],
    )


@router.post(
    "/v2/position-episodes/{episode_id}/review-annotations",
    response_model=ReviewAnnotationCreateResponse,
)
def append_position_episode_review(
    episode_id: int,
    request: ReviewAnnotationCreateRequest,
    account_key: str = Query(
        DEFAULT_LEDGER_ACCOUNT_KEY,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
) -> ReviewAnnotationCreateResponse:
    """Append explicit user content; identical latest content is idempotent."""

    try:
        result = append_review_annotation(
            ReviewAnnotationInput(
                review_status=request.review_status,
                setup_thesis=request.setup_thesis,
                entry_trigger=request.entry_trigger,
                invalidation_plan=request.invalidation_plan,
                position_rationale=request.position_rationale,
                exit_reason=request.exit_reason,
                post_trade_reflection=request.post_trade_reflection,
                tags=tuple(request.tags),
                error_types=tuple(request.error_types),
            ),
            account_key=account_key,
            episode_build_id=request.build_id,
            position_episode_id=episode_id,
        )
    except ReviewAnnotationRepositoryError as exc:
        raise _scope_error(exc) from exc
    return ReviewAnnotationCreateResponse(
        created=not result.duplicate,
        idempotent_replay=result.duplicate,
        annotation=_item(result.annotation),
    )


# --- playbook (slice C-2): explicit user promotion only, append-only ---------


_ACCOUNT_KEY_QUERY = Query(
    DEFAULT_LEDGER_ACCOUNT_KEY,
    min_length=1,
    max_length=64,
    pattern=r"^[A-Za-z0-9_.:-]+$",
)


def _playbook_error(exc: PlaybookRepositoryError) -> HTTPException:
    if isinstance(exc, PlaybookConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _bucket_model(
    value: PlaybookSourceBucket | None,
) -> PlaybookSourceBucketModel | None:
    if value is None:
        return None
    return PlaybookSourceBucketModel(
        group_kind=value.group_kind,  # type: ignore[arg-type]
        group_value=value.group_value,
        direction=value.direction,
        boundary_policy=value.boundary_policy,  # type: ignore[arg-type]
    )


def _candidate_item(value: StoredPlaybookCandidate) -> PlaybookCandidateItem:
    return PlaybookCandidateItem(
        id=value.candidate_id,
        candidate_key=value.candidate_key,
        account_key=value.account_key,
        title=value.title,
        rule_text=value.rule_text,
        source_bucket=_bucket_model(value.source_bucket),
        evidence_snapshot=dict(value.evidence_snapshot),
        evidence_snapshot_sha256=value.evidence_snapshot_sha256,
        promoted=value.promoted,
        created_at=value.created_at,
    )


def _rule_item(value: StoredPlaybookRule) -> PlaybookRuleItem:
    return PlaybookRuleItem(
        id=value.rule_id,
        rule_key=value.rule_key,
        lineage_key=value.lineage_key,
        account_key=value.account_key,
        version=value.version,
        status=value.status,  # type: ignore[arg-type]
        promoted_from_candidate_id=value.promoted_from_candidate_id,
        promoted_from_candidate_key=value.promoted_from_candidate_key,
        previous_rule_id=value.previous_rule_id,
        title=value.title,
        rule_text=value.rule_text,
        evidence_snapshot=dict(value.evidence_snapshot),
        evidence_snapshot_sha256=value.evidence_snapshot_sha256,
        is_latest_version=value.is_latest_version,
        created_at=value.created_at,
    )


@router.get("/v2/playbook", response_model=PlaybookListResponse)
def get_playbook(
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PlaybookListResponse:
    """List append-only candidates and rule versions, newest first.

    Rules are the user's own decision checklist: they never feed back into
    any scoring, ranking, or AI prompt.
    """
    try:
        candidates = list_playbook_candidates(account_key)
        rules = list_playbook_rules(account_key)
    except PlaybookRepositoryError as exc:
        raise _playbook_error(exc) from exc
    return PlaybookListResponse(
        account_key=account_key,
        candidates=[_candidate_item(item) for item in candidates],
        rules=[_rule_item(item) for item in rules],
    )


@router.post(
    "/v2/playbook/candidates",
    response_model=PlaybookCandidateCreateResponse,
)
def create_candidate(
    request: PlaybookCandidateCreateRequest,
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PlaybookCandidateCreateResponse:
    """Explicitly save one candidate, freezing its evidence snapshot now."""
    bucket = None
    if request.source_bucket is not None:
        bucket = PlaybookSourceBucket(
            group_kind=request.source_bucket.group_kind,
            group_value=request.source_bucket.group_value,
            direction=request.source_bucket.direction,
            boundary_policy=request.source_bucket.boundary_policy,
        )
    try:
        result = create_playbook_candidate(
            title=request.title,
            rule_text=request.rule_text,
            source_bucket=bucket,
            account_key=account_key,
        )
    except PlaybookRepositoryError as exc:
        raise _playbook_error(exc) from exc
    return PlaybookCandidateCreateResponse(
        created=not result.duplicate,
        idempotent_replay=result.duplicate,
        candidate=_candidate_item(result.candidate),
    )


@router.post(
    "/v2/playbook/candidates/{candidate_key}/promote",
    response_model=PlaybookRulePromoteResponse,
)
def promote_candidate(
    candidate_key: str,
    request: PlaybookRulePromoteRequest,
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PlaybookRulePromoteResponse:
    """Explicit user promotion; evidence is re-frozen at promotion time."""
    try:
        result = promote_candidate_to_rule(
            candidate_key=candidate_key,
            account_key=account_key,
            allow_new_version=request.allow_new_version,
            expected_current_version=request.expected_current_version,
        )
    except PlaybookRepositoryError as exc:
        raise _playbook_error(exc) from exc
    return PlaybookRulePromoteResponse(
        created=not result.duplicate,
        idempotent_replay=result.duplicate,
        rule=_rule_item(result.rule),
    )


@router.post(
    "/v2/playbook/rules/{lineage_key}/retire",
    response_model=PlaybookRuleRetireResponse,
)
def retire_rule(
    lineage_key: str,
    request: PlaybookRuleRetireRequest,
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PlaybookRuleRetireResponse:
    """Append a retired version row; nothing is deleted or updated."""
    try:
        result = retire_playbook_rule(
            lineage_key=lineage_key,
            expected_current_version=request.expected_current_version,
            account_key=account_key,
        )
    except PlaybookRepositoryError as exc:
        raise _playbook_error(exc) from exc
    return PlaybookRuleRetireResponse(
        retired=not result.duplicate,
        idempotent_replay=result.duplicate,
        rule=_rule_item(result.rule),
    )


# --- playbook episode links (slice C-3): zero-write reverse lookup -----------


def _episode_link_item(value: PlaybookEpisodeLink) -> PlaybookEpisodeLinkItem:
    return PlaybookEpisodeLinkItem(
        kind=value.kind,  # type: ignore[arg-type]
        link_state=value.link_state,  # type: ignore[arg-type]
        title=value.title,
        rule_text=value.rule_text,
        bucket=_bucket_model(value.bucket),
        snapshot_build_id=value.snapshot_build_id,
        snapshot_generated_at=value.snapshot_generated_at,
        lineage_key=value.lineage_key,
        version=value.version,
        status=value.status,  # type: ignore[arg-type]
        candidate_key=value.candidate_key,
        promoted=value.promoted,
        created_at=value.created_at,
    )


@router.get(
    "/v2/position-episodes/{episode_id}/playbook-links",
    response_model=PlaybookEpisodeLinksResponse,
)
def get_position_episode_playbook_links(
    episode_id: int,
    build_id: int = Query(..., ge=1),
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> PlaybookEpisodeLinksResponse:
    """Which candidates/rules reference this episode in their frozen snapshot.

    Zero writes.  Only snapshots frozen against the same build identity are
    considered; truncated-sample snapshots that cannot prove membership are
    returned as separate ``possible_truncated`` entries (bucket echo must
    match the episode) instead of being passed off as confirmed links.
    """
    try:
        result = list_playbook_links_for_episode(
            episode_id=episode_id,
            build_id=build_id,
            account_key=account_key,
        )
    except ReviewAnnotationRepositoryError as exc:
        raise _scope_error(exc) from exc
    except PlaybookRepositoryError as exc:
        raise _playbook_error(exc) from exc
    return PlaybookEpisodeLinksResponse(
        account_key=result.account_key,
        build_id=result.build_id,
        episode_id=result.episode_id,
        links=[_episode_link_item(link) for link in result.links],
    )
