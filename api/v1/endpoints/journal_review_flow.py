# -*- coding: utf-8 -*-
"""引导式日终复盘流 + MAE/MFE 端点（蓝图 17 §三(f) Phase A）。

Exposes:
    GET  /api/v1/journal/review-flow/daily
    POST /api/v1/journal/review-flow/daily
    GET  /api/v1/journal/review-flow/excursions
    GET  /api/v1/journal/episodes/{episode_id}/excursion

判定全部在服务端算（车道、四象限、过程指标），前端只收集人的输入。
盲评契约：GET/POST 的载荷在会话密封并主动揭示之前不含任何盈亏字段。
只读红线：除 append-only 的会话修订与出场预登记注解外，绝不改写任何行。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.journal_review_flow import (
    DailyEpisodeVerdictModel,
    DailyReviewFlowResponse,
    DailyReviewPostRequest,
    DailyReviewPostResponse,
    DailyReviewSessionModel,
    EpisodeExcursionModel,
    EpisodeExcursionResponse,
    EpisodeVerdictModel,
    ExcursionScatterItemModel,
    ExcursionScatterResponse,
    OpenPositionExitPlanModel,
    ProcessMetricModel,
    RevealEpisodeModel,
    RevealSummaryModel,
)
from src.journal.excursions import (
    EXCURSION_CODE_VERSION,
    EXCURSION_DISCLAIMER,
    EXCURSION_LIMITATIONS,
)
from src.journal.ledger.daily_review_repository import (
    DailyReviewRepositoryError,
    DailyReviewSessionInput,
    StoredDailyReviewSession,
    append_daily_review_session,
    normalize_daily_review_session_input,
)
from src.journal.ledger.episode_repository import EpisodeRepositoryError
from src.journal.ledger.excursion_repository import get_episode_excursion
from src.journal.ledger.repository import DEFAULT_LEDGER_ACCOUNT_KEY
from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    ReviewAnnotationRepositoryError,
    append_review_annotation,
    get_latest_review_annotation,
)
from src.journal.review_flow import (
    DailyReviewFlowData,
    EXIT_PLAN_REGISTRATION_TAG,
    MISTAKE_VOCABULARY,
    REVIEW_FLOW_LIMITATIONS,
    RevealSummary,
    build_reveal_summary,
    get_daily_review_flow,
    get_episode_review_verdict,
    list_excursion_scatter,
    today_et_date,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_ACCOUNT_KEY_QUERY = Query(
    DEFAULT_LEDGER_ACCOUNT_KEY,
    min_length=1,
    max_length=64,
    pattern=r"^[A-Za-z0-9_.:-]+$",
)


def _session_model(value: StoredDailyReviewSession) -> DailyReviewSessionModel:
    return DailyReviewSessionModel(
        session_id=value.session_id,
        account_key=value.account_key,
        et_date=value.et_date,
        session_kind=value.session_kind,  # type: ignore[arg-type]
        revision=value.revision,
        previous_session_id=value.previous_session_id,
        episode_build_id=value.episode_build_id,
        started_at=value.started_at,
        sealed_at=value.sealed_at,
        revealed_at=value.revealed_at,
        revealed_after_seal=value.revealed_after_seal,
        steps=dict(value.steps),
        process_scores=[dict(item) for item in value.process_scores],
        violation_acks=[dict(item) for item in value.violation_acks],
        note=value.note,
        content_sha256=value.content_sha256,
        created_at=value.created_at,
    )


def _reveal_model(value: RevealSummary) -> RevealSummaryModel:
    return RevealSummaryModel(
        et_date=value.et_date,
        closed_episode_count=value.closed_episode_count,
        pnl_known_count=value.pnl_known_count,
        total_net=value.total_net,
        quadrant_counts=dict(value.quadrant_counts),
        episodes=[
            RevealEpisodeModel(
                episode_id=item.episode_id,
                raw_symbol=item.raw_symbol,
                verdict=item.verdict,
                quadrant=item.quadrant,
                quadrant_label=item.quadrant_label,
                pnl_net=item.pnl_net,
            )
            for item in value.episodes
        ],
    )


def _flow_response(flow: DailyReviewFlowData) -> DailyReviewFlowResponse:
    session_model = (
        _session_model(flow.session) if flow.session is not None else None
    )
    reveal: Optional[RevealSummaryModel] = None
    if (
        flow.session is not None
        and flow.session.sealed_at is not None
        and flow.session.revealed_after_seal
    ):
        reveal = _reveal_model(
            build_reveal_summary(
                flow.account_key,
                build_id=flow.build_id,
                et_date=flow.et_date,
            )
        )
    return DailyReviewFlowResponse(
        data_state="ready",
        account_key=flow.account_key,
        et_date=flow.et_date,
        build_id=flow.build_id,
        build_key=flow.build_key,
        session=session_model,
        rest_day_candidate=flow.rest_day_candidate,
        rest_streak=flow.rest_streak,
        episodes_today=[
            DailyEpisodeVerdictModel(
                episode_id=item.episode_id,
                raw_symbol=item.raw_symbol,
                underlying=item.underlying,
                direction=item.direction,
                lifecycle_status=item.lifecycle_status,
                opened_at=item.opened_at,
                closed_at=item.closed_at,
                dte_at_entry=item.dte_at_entry,
                lane=item.lane,
                rule_id=item.rule_id,
                verdict=item.verdict,
                opened_today=item.opened_today,
                closed_today=item.closed_today,
                needs_ack=item.needs_ack,
                acked=item.acked,
            )
            for item in flow.episodes_today
        ],
        open_positions=[
            OpenPositionExitPlanModel(
                episode_id=item.episode_id,
                raw_symbol=item.raw_symbol,
                underlying=item.underlying,
                opened_at=item.opened_at,
                dte_at_entry=item.dte_at_entry,
                has_exit_plan=item.has_exit_plan,
                exit_plan_text=item.exit_plan_text,
                exit_plan_registered_at=item.exit_plan_registered_at,
            )
            for item in flow.open_positions
        ],
        process_metrics=[
            ProcessMetricModel(
                metric_id=item.metric_id,
                name=item.name,
                basis=item.basis,  # type: ignore[arg-type]
                value_ratio=item.value_ratio,
                value_text=item.value_text,
                numerator=item.numerator,
                denominator=item.denominator,
                reason=item.reason,
                manual_allowed=item.manual_allowed,
                manual_value=item.manual_value,
            )
            for item in flow.process_metrics
        ],
        mistake_vocabulary=list(MISTAKE_VOCABULARY),
        reveal=reveal,
        limitations=list(REVIEW_FLOW_LIMITATIONS),
    )


@router.get("/review-flow/daily", response_model=DailyReviewFlowResponse)
def get_daily_review(
    date: Optional[str] = Query(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="ET 自然日（YYYY-MM-DD），缺省＝今天（America/New_York，DST 感知）",
    ),
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> DailyReviewFlowResponse:
    """日终复盘流装配：车道判定、过程指标、出场预登记状态、会话状态。

    盲评契约：``reveal`` 仅在最新会话已密封且已记录揭示时非空；此前整个
    载荷不含任何盈亏字段。零写。
    """
    try:
        flow = get_daily_review_flow(account_key, et_date=date)
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if flow is None:
        return DailyReviewFlowResponse(
            data_state="not_built",
            account_key=account_key,
            et_date=date or today_et_date(),
            limitations=list(REVIEW_FLOW_LIMITATIONS),
        )
    return _flow_response(flow)


def _append_exit_plan(
    *,
    account_key: str,
    build_id: int,
    episode_id: int,
    plan_text: str,
) -> None:
    """出场预登记：写现有 review annotation 修订链（新增用途，不新增机制）。

    合并策略：保留最新修订的其它字段，仅替换 ``invalidation_plan``；
    锁定时间戳＝此后「规则出场 vs 情绪出场」判定的唯一依据。修订打
    ``EXIT_PLAN_REGISTRATION_TAG``（复核修复 9）：指标 6 据此不把出场
    预登记计作进场决策日志。
    """
    latest = get_latest_review_annotation(
        account_key=account_key,
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )
    tags = tuple(latest.tags) if latest is not None else ()
    if EXIT_PLAN_REGISTRATION_TAG not in tags:
        tags = tags + (EXIT_PLAN_REGISTRATION_TAG,)
    append_review_annotation(
        ReviewAnnotationInput(
            review_status=(
                latest.review_status if latest is not None else "in_progress"
            ),
            setup_thesis=latest.setup_thesis if latest else "",
            entry_trigger=latest.entry_trigger if latest else "",
            invalidation_plan=plan_text,
            position_rationale=latest.position_rationale if latest else "",
            exit_reason=latest.exit_reason if latest else "",
            post_trade_reflection=(
                latest.post_trade_reflection if latest else ""
            ),
            tags=tags,
            error_types=latest.error_types if latest else (),
        ),
        account_key=account_key,
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )


def _post_reveal_revision(
    flow: DailyReviewFlowData,
    request: DailyReviewPostRequest,
    et_date: str,
) -> DailyReviewPostResponse:
    """揭示修订：服务端逐字重放已密封修订的内容（复核修复 1）。

    密封后客户端状态可能漂移（页面重载丢手填分、封卷后新写注解、指标 3
    因会话行出现而翻面）——揭示绝不能因此被卡死。因此揭示请求是**最小**
    的（date + 可选 expected_revision），内容字段一律忽略、零写出场预登记；
    服务端把已密封修订的内容原样复制成新修订，只翻揭示位。保持不变的
    规则：揭示要求此前已有密封修订（否则 422）、密封与揭示不能同一修订、
    揭示不可撤销（仓库层继续强制）。
    """
    latest = flow.session
    if latest is None or latest.sealed_at is None:
        raise HTTPException(
            status_code=422,
            detail="reveal must be appended after an already-sealed revision",
        )
    if (
        request.expected_revision is not None
        and request.expected_revision != latest.revision
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"reveal expected revision {request.expected_revision} but "
                f"the latest revision is {latest.revision}; reload and retry"
            ),
        )
    try:
        result = append_daily_review_session(
            DailyReviewSessionInput(
                et_date=latest.et_date,
                session_kind=latest.session_kind,
                steps=dict(latest.steps),
                process_scores=tuple(latest.process_scores),
                violation_acks=tuple(latest.violation_acks),
                note=latest.note,
                sealed=True,
                revealed_after_seal=True,
            ),
            account_key=flow.account_key,
            episode_build_id=(
                latest.episode_build_id
                if latest.episode_build_id is not None
                else flow.build_id
            ),
        )
    except DailyReviewRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    reveal_model = _reveal_model(
        build_reveal_summary(
            flow.account_key, build_id=flow.build_id, et_date=et_date
        )
    )
    return DailyReviewPostResponse(
        created=not result.duplicate,
        idempotent_replay=result.duplicate,
        session=_session_model(result.session),
        reveal=reveal_model,
    )


@router.post("/review-flow/daily", response_model=DailyReviewPostResponse)
def post_daily_review(
    request: DailyReviewPostRequest,
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> DailyReviewPostResponse:
    """追加一条日终复盘会话修订（append-only，幂等）。

    服务端强制的流程规则：休息日确认只在当日无活动时合法；密封要求当日
    每条 violation 都已有一句话确认；揭示要求此前修订已密封，且由服务端
    逐字重放已密封内容（请求内容字段被忽略）。出场预登记写入既有
    annotation 修订链，且只在**全部校验通过后**才落笔——被拒绝的请求
    零写（复核修复 8）。除此之外零写。
    """
    et_date = request.et_date or today_et_date()
    try:
        flow = get_daily_review_flow(account_key, et_date=et_date)
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if flow is None:
        raise HTTPException(
            status_code=422,
            detail="account has no episode build; import evidence first",
        )

    # --- 揭示：独立最小路径，重放密封内容，绝不重建 ------------------------
    if request.reveal:
        return _post_reveal_revision(flow, request, et_date)

    # --- session_kind：服务端裁定，客户端不能把交易日谎报成休息日 --------
    derived_kind = "rest_day" if flow.rest_day_candidate else "trading_day"
    session_kind = request.session_kind or derived_kind
    if session_kind != derived_kind:
        raise HTTPException(
            status_code=422,
            detail=(
                f"session_kind must be {derived_kind} for {et_date}: "
                f"当日{'无' if flow.rest_day_candidate else '有'}交易活动"
            ),
        )

    # --- 先校验，后落笔（复核修复 8：被拒绝的请求不留任何写入） ------------
    if (
        flow.session is not None
        and flow.session.sealed_at is not None
        and request.exit_plans
    ):
        # 密封后内容冻结，本次追加注定被仓库层拒绝——在写出场预登记之前拒。
        raise HTTPException(
            status_code=422,
            detail=(
                "sealed session content is frozen; only a reveal revision "
                "may be appended (exit plans were not written)"
            ),
        )
    open_ids = {item.episode_id for item in flow.open_positions}
    for plan in request.exit_plans:
        if plan.position_episode_id not in open_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"episode {plan.position_episode_id} is not an open "
                    "position in the current build"
                ),
            )

    # --- violation 确认：合并既有 + 本次；密封时必须全覆盖 -----------------
    verdicts_by_id = {item.episode_id: item for item in flow.episodes_today}
    merged_acks: dict[int, dict[str, str]] = {}
    if flow.session is not None:
        for item in flow.session.violation_acks:
            episode_id = int(item.get("position_episode_id", 0))
            if episode_id:
                merged_acks[episode_id] = dict(item)
    for ack in request.violation_acks:
        verdict = verdicts_by_id.get(ack.position_episode_id)
        if verdict is None or not verdict.needs_ack:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"episode {ack.position_episode_id} is not a violation "
                    "requiring acknowledgement today"
                ),
            )
        merged_acks[ack.position_episode_id] = {
            "position_episode_id": ack.position_episode_id,
            "lane": verdict.lane,
            "rule_id": verdict.rule_id,
            "ack_text": ack.ack_text.strip(),
        }
    if request.seal:
        missing = [
            item.episode_id
            for item in flow.episodes_today
            if item.needs_ack and item.episode_id not in merged_acks
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    "sealing requires a one-line acknowledgement for every "
                    f"violation; missing episode ids: {missing}"
                ),
            )

    # --- 过程打分卡：自动值服务端冻结，手填只允许 manual_allowed 指标 -------
    manual_allowed_ids = {
        item.metric_id for item in flow.process_metrics if item.manual_allowed
    }
    manual_by_id = {}
    for score in request.manual_scores:
        if score.metric_id not in manual_allowed_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"metric {score.metric_id} is computed automatically; "
                    "manual values are only accepted for missing Phase B "
                    "metrics"
                ),
            )
        manual_by_id[score.metric_id] = score

    # --- 内容级预校验（note/steps 尺寸等），仍零写 --------------------------
    try:
        normalize_daily_review_session_input(
            DailyReviewSessionInput(
                et_date=et_date,
                session_kind=session_kind,
                steps=dict(request.steps),
                violation_acks=tuple(
                    merged_acks[key] for key in sorted(merged_acks)
                ),
                note=request.note,
                sealed=bool(request.seal),
            )
        )
    except DailyReviewRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # --- 校验全部通过：写出场预登记，再重算流以冻结自动指标 ----------------
    for plan in request.exit_plans:
        try:
            _append_exit_plan(
                account_key=flow.account_key,
                build_id=flow.build_id,
                episode_id=plan.position_episode_id,
                plan_text=plan.plan_text,
            )
        except ReviewAnnotationRepositoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.exit_plans:
        flow = get_daily_review_flow(account_key, et_date=et_date)
        assert flow is not None

    process_scores = []
    for metric in flow.process_metrics:
        manual = manual_by_id.get(metric.metric_id)
        if manual is not None and manual.value != "missing":
            process_scores.append(
                {
                    "metric_id": metric.metric_id,
                    "basis": "manual",
                    "value": manual.value,
                    "note": manual.note,
                    "reason": metric.reason,
                }
            )
        elif metric.basis == "auto":
            entry = {
                "metric_id": metric.metric_id,
                "basis": "auto",
                "value": (
                    metric.value_ratio
                    if metric.value_ratio is not None
                    else metric.value_text
                ),
            }
            if metric.reason:
                entry["reason"] = metric.reason
            process_scores.append(entry)
        else:
            entry = {
                "metric_id": metric.metric_id,
                "basis": "missing",
            }
            if metric.reason:
                entry["reason"] = metric.reason
            process_scores.append(entry)

    try:
        result = append_daily_review_session(
            DailyReviewSessionInput(
                et_date=et_date,
                session_kind=session_kind,
                steps=dict(request.steps),
                process_scores=tuple(process_scores),
                violation_acks=tuple(
                    merged_acks[key] for key in sorted(merged_acks)
                ),
                note=request.note,
                sealed=bool(request.seal),
                revealed_after_seal=False,
            ),
            account_key=flow.account_key,
            episode_build_id=flow.build_id,
        )
    except DailyReviewRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 非揭示路径：盲评契约下永不携带 reveal（揭示走 _post_reveal_revision）。
    reveal_model: Optional[RevealSummaryModel] = None
    if result.session.sealed_at is not None and result.session.revealed_after_seal:
        reveal_model = _reveal_model(
            build_reveal_summary(
                flow.account_key,
                build_id=flow.build_id,
                et_date=et_date,
            )
        )
    return DailyReviewPostResponse(
        created=not result.duplicate,
        idempotent_replay=result.duplicate,
        session=_session_model(result.session),
        reveal=reveal_model,
    )


def _excursion_model(stored) -> EpisodeExcursionModel:
    return EpisodeExcursionModel(
        excursion_id=stored.excursion_id,
        episode_build_id=stored.episode_build_id,
        position_episode_id=stored.position_episode_id,
        code_version=stored.code_version,
        attempt=stored.attempt,
        source=stored.source,
        status=stored.status,
        status_reason=stored.status_reason,
        exposure=stored.exposure,
        u0=stored.u0,
        u0_at=stored.u0_at,
        u0_flag=stored.u0_flag,
        mfe_underlying_pct=stored.mfe_underlying_pct,
        mfe_at=stored.mfe_at,
        mae_underlying_pct=stored.mae_underlying_pct,
        mae_at=stored.mae_at,
        mae_before_mfe=stored.mae_before_mfe,
        atr14=stored.atr14,
        mfe_atr=stored.mfe_atr,
        mae_atr=stored.mae_atr,
        bars_used=stored.bars_used,
        coverage_start=stored.coverage_start,
        coverage_end=stored.coverage_end,
        missing_sessions=list(stored.missing_sessions),
        computed_at=stored.computed_at,
    )


@router.get(
    "/episodes/{episode_id}/excursion",
    response_model=EpisodeExcursionResponse,
)
def get_episode_excursion_endpoint(
    episode_id: int,
    build_id: Optional[int] = Query(None, ge=1),
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> EpisodeExcursionResponse:
    """单回合 MAE/MFE 记录（或标缺原因）＋机械判定与两个反事实。

    只读。永久免责随载荷携带；无记录时给出标缺原因而非空白或 0。
    """
    try:
        verdict = get_episode_review_verdict(
            episode_id, account_key=account_key, build_id=build_id
        )
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if verdict is None:
        return EpisodeExcursionResponse(
            data_state="not_built",
            episode_id=episode_id,
            build_id=build_id,
            missing_reason="episode not found in the selected build",
            disclaimer=EXCURSION_DISCLAIMER,
            limitations=list(EXCURSION_LIMITATIONS),
        )
    verdict_model = EpisodeVerdictModel(
        episode_id=verdict.episode_id,
        build_id=verdict.build_id,
        lane=verdict.lane,
        rule_id=verdict.rule_id,
        verdict=verdict.verdict,
        quadrant=verdict.quadrant,
        quadrant_label=verdict.quadrant_label,
        counterfactual_compliant_excluded=(
            verdict.counterfactual_compliant_excluded
        ),
        counterfactual_compliant_text=verdict.counterfactual_compliant_text,
        counterfactual_gate_status=verdict.counterfactual_gate_status,
        counterfactual_gate_reason=verdict.counterfactual_gate_reason,
    )
    stored = get_episode_excursion(
        episode_build_id=verdict.build_id,
        position_episode_id=episode_id,
    )
    if stored is None:
        return EpisodeExcursionResponse(
            data_state="missing",
            episode_id=episode_id,
            build_id=verdict.build_id,
            missing_reason=(
                "该回合尚无偏移记录：可能超出 5m 数据保留窗口（约 60 天，"
                "永久标缺）或回填脚本尚未运行"
            ),
            verdict=verdict_model,
            disclaimer=EXCURSION_DISCLAIMER,
            limitations=list(EXCURSION_LIMITATIONS),
        )
    return EpisodeExcursionResponse(
        data_state="ready",
        episode_id=episode_id,
        build_id=verdict.build_id,
        excursion=_excursion_model(stored),
        verdict=verdict_model,
        disclaimer=EXCURSION_DISCLAIMER,
        limitations=list(EXCURSION_LIMITATIONS),
    )


@router.get(
    "/review-flow/excursions",
    response_model=ExcursionScatterResponse,
)
def get_excursion_scatter(
    account_key: str = _ACCOUNT_KEY_QUERY,
) -> ExcursionScatterResponse:
    """只读诊断散点数据：MAE% × 最终 R，按持有结构分层。

    只看形态不定参数；免责文案随载荷携带且前端组件永久渲染。
    """
    try:
        result = list_excursion_scatter(account_key)
    except EpisodeRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        return ExcursionScatterResponse(
            data_state="not_built",
            account_key=account_key,
            code_version=EXCURSION_CODE_VERSION,
            disclaimer=EXCURSION_DISCLAIMER,
            limitations=list(EXCURSION_LIMITATIONS),
        )
    build_id, items = result
    return ExcursionScatterResponse(
        data_state="ready",
        account_key=account_key,
        build_id=build_id,
        code_version=EXCURSION_CODE_VERSION,
        items=[
            ExcursionScatterItemModel(
                episode_id=item.episode_id,
                raw_symbol=item.raw_symbol,
                hold_structure=item.hold_structure,  # type: ignore[arg-type]
                status=item.status,
                mae_underlying_pct=item.mae_underlying_pct,
                mfe_underlying_pct=item.mfe_underlying_pct,
                mae_atr=item.mae_atr,
                mfe_atr=item.mfe_atr,
                r_multiple=item.r_multiple,
                r_missing_reason=item.r_missing_reason,
            )
            for item in items
        ],
        disclaimer=EXCURSION_DISCLAIMER,
        limitations=list(EXCURSION_LIMITATIONS),
    )
