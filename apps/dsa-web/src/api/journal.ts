import apiClient from './index';
import { toCamelCase } from './utils';
import { sessionCache } from '../utils/sessionCache';
import type {
  HealthCheckItem,
  ImportResponse,
  MoomooStatementPreview,
  MoomooOpenApiPreview,
  MoomooOpenApiImportConfirmResponse,
  MoomooOpenApiImportPlan,
  JournalQaRequest,
  JournalQaResponse,
  JournalStatsByStyleResponse,
  JournalStatsResponse,
  LedgerDataHealth,
  LedgerImportResponse,
  JournalRefreshStatus,
  MoomooJournalRefreshConfirmResponse,
  MoomooJournalRefreshPreview,
  EpisodeBuildResponse,
  CanonicalEpisodeBuildConfirmRequest,
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildActivationRequest,
  EpisodeBuildActivationResponse,
  EpisodeBuildActivationState,
  PositionEpisodeDetailResponse,
  PositionEpisodeAiReviewResponse,
  PositionEpisodeAiReviewUserContext,
  PositionEpisodeFilters,
  PositionEpisodeListResponse,
  PersonalEdgeResponse,
  PositionEpisodeReviewAnnotationHistoryResponse,
  PositionEpisodeReviewAnnotationLatestResponse,
  EpisodePlaybookLinksResponse,
  PlaybookListResponse,
  CreatePlaybookCandidateRequest,
  CreatePlaybookCandidateResponse,
  PromotePlaybookCandidateRequest,
  PromotePlaybookCandidateResponse,
  RetirePlaybookRuleResponse,
  RealityTestResponse,
  ReviewInsightsResponse,
  RulesEvidenceResponse,
  SavePositionEpisodeReviewAnnotationRequest,
  SavePositionEpisodeReviewAnnotationResponse,
  TradeItem,
  TradeListFilters,
  TradeListResponse,
  TradeUpdateRequest,
} from '../types/journal';

const BASE = '/api/v1/journal';

export async function fetchRealityTest(topN = 5, since?: string): Promise<RealityTestResponse> {
  const { data } = await apiClient.get(`${BASE}/reality-test`, {
    params: { top_n: topN, since },
  });
  return toCamelCase<RealityTestResponse>(data);
}

export async function fetchTrades(filters: TradeListFilters = {}): Promise<TradeListResponse> {
  const params: Record<string, string | number | undefined> = {
    symbol: filters.symbol,
    start: filters.start,
    end: filters.end,
    status: filters.status,
    style: filters.style,
    page: filters.page ?? 1,
    per_page: filters.perPage ?? 50,
  };
  const { data } = await apiClient.get(`${BASE}/trades`, { params });
  return toCamelCase<TradeListResponse>(data);
}

export async function fetchTrade(tradeId: number): Promise<TradeItem> {
  const { data } = await apiClient.get(`${BASE}/trades/${tradeId}`);
  return toCamelCase<TradeItem>(data);
}

export async function updateTrade(
  tradeId: number,
  payload: TradeUpdateRequest,
): Promise<TradeItem> {
  const snakePayload = {
    user_notes: payload.userNotes,
    emotional_state: payload.emotionalState,
    trade_style: payload.tradeStyle,
  };
  const { data } = await apiClient.patch(`${BASE}/trades/${tradeId}`, snakePayload);
  return toCamelCase<TradeItem>(data);
}

export async function fetchHealthCheck(date: string): Promise<HealthCheckItem | null> {
  const { data } = await apiClient.get(`${BASE}/health-check`, { params: { date } });
  if (data === null) return null;
  return toCamelCase<HealthCheckItem>(data);
}

export async function fetchJournalStats(days = 90): Promise<JournalStatsResponse> {
  const { data } = await apiClient.get(`${BASE}/stats`, { params: { days } });
  return toCamelCase<JournalStatsResponse>(data);
}

export async function importJournalCsv(file: File, broker = 'moomoo_us'): Promise<ImportResponse> {
  const form = new FormData();
  form.append('file', file);
  // apiClient defaults to `Content-Type: application/json`. For multipart
  // uploads we MUST unset it so the browser writes the correct
  // `multipart/form-data; boundary=...` header — otherwise the backend
  // sees a JSON body and FastAPI raises `File field required`.
  const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
  const { data } = await apiClient.post(`${BASE}/import`, form, {
    params: { broker },
    headers,
    timeout: 60000,
  });
  return toCamelCase<ImportResponse>(data);
}

export async function previewJournalCsv(file: File): Promise<MoomooStatementPreview> {
  const form = new FormData();
  form.append('file', file);
  const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
  const { data } = await apiClient.post(`${BASE}/v2/imports/preview`, form, {
    headers,
    timeout: 60000,
  });
  return toCamelCase<MoomooStatementPreview>(data);
}

export async function previewJournalOpenApiExport(
  file: File,
): Promise<MoomooOpenApiPreview> {
  const form = new FormData();
  form.append('file', file);
  const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
  const { data } = await apiClient.post(`${BASE}/v2/openapi-imports/preview`, form, {
    headers,
    timeout: 60000,
  });
  return toCamelCase<MoomooOpenApiPreview>(data);
}

export async function planJournalOpenApiImport(
  file: File,
): Promise<MoomooOpenApiImportPlan> {
  const form = new FormData();
  form.append('file', file);
  const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
  const { data } = await apiClient.post(`${BASE}/v2/openapi-imports/plan`, form, {
    headers,
    timeout: 60000,
  });
  return toCamelCase<MoomooOpenApiImportPlan>(data);
}

export async function confirmJournalOpenApiImport(
  file: File,
  previewKey: string,
  acknowledgePartialWindow: boolean,
): Promise<MoomooOpenApiImportConfirmResponse> {
  const form = new FormData();
  form.append('file', file);
  const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
  const { data } = await apiClient.post(`${BASE}/v2/openapi-imports/confirm`, form, {
    params: {
      preview_key: previewKey,
      acknowledge_partial_window: acknowledgePartialWindow,
    },
    headers,
    timeout: 60000,
  });
  return toCamelCase<MoomooOpenApiImportConfirmResponse>(data);
}

export async function importJournalLedgerCsv(
  file: File,
  allowPartial: boolean,
): Promise<LedgerImportResponse> {
  const form = new FormData();
  form.append('file', file);
  const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
  const { data } = await apiClient.post(`${BASE}/v2/imports`, form, {
    params: { allow_partial: allowPartial },
    headers,
    timeout: 60000,
  });
  return toCamelCase<LedgerImportResponse>(data);
}

export async function fetchLedgerDataHealth(): Promise<LedgerDataHealth> {
  const { data } = await apiClient.get(`${BASE}/v2/data-health`);
  return toCamelCase<LedgerDataHealth>(data);
}

export async function fetchJournalRefreshStatus(): Promise<JournalRefreshStatus> {
  const { data } = await apiClient.get(`${BASE}/v2/refresh-status`);
  return toCamelCase<JournalRefreshStatus>(data);
}

export async function previewJournalRefresh(
  overlapDays = 7,
): Promise<MoomooJournalRefreshPreview> {
  const { data } = await apiClient.post(
    `${BASE}/v2/refreshes/preview`,
    { overlap_days: overlapDays },
    { timeout: 210000 },
  );
  return toCamelCase<MoomooJournalRefreshPreview>(data);
}

export async function confirmJournalRefresh(
  artifactId: number,
  previewKey: string,
  acknowledgePartialWindow: boolean,
): Promise<MoomooJournalRefreshConfirmResponse> {
  const { data } = await apiClient.post(
    `${BASE}/v2/refreshes/${artifactId}/confirm`,
    {
      preview_key: previewKey,
      acknowledge_partial_window: acknowledgePartialWindow,
    },
    { timeout: 60000 },
  );
  return toCamelCase<MoomooJournalRefreshConfirmResponse>(data);
}

export async function fetchPositionEpisodes(
  filters: PositionEpisodeFilters = {},
): Promise<PositionEpisodeListResponse> {
  const { data } = await apiClient.get(`${BASE}/v2/position-episodes`, {
    params: {
      underlying: filters.underlying,
      lifecycle_status: filters.lifecycleStatus || undefined,
      completeness_status: filters.completenessStatus || undefined,
      case_focus: filters.caseFocus || undefined,
      review_status: filters.reviewStatus || undefined,
      build_id: filters.buildId,
      page: filters.page ?? 1,
      per_page: filters.perPage ?? 50,
    },
  });
  return toCamelCase<PositionEpisodeListResponse>(data);
}

export async function fetchPositionEpisodeDetail(
  episodeId: number,
  buildId?: number,
): Promise<PositionEpisodeDetailResponse> {
  const { data } = await apiClient.get(`${BASE}/v2/position-episodes/${episodeId}`, {
    params: { build_id: buildId },
  });
  return toCamelCase<PositionEpisodeDetailResponse>(data);
}

export async function fetchLatestPositionEpisodeReviewAnnotation(
  episodeId: number,
  buildId: number,
): Promise<PositionEpisodeReviewAnnotationLatestResponse> {
  const { data } = await apiClient.get(
    `${BASE}/v2/position-episodes/${episodeId}/review-annotations/latest`,
    { params: { build_id: buildId } },
  );
  return toCamelCase<PositionEpisodeReviewAnnotationLatestResponse>(data);
}

export async function fetchPositionEpisodeReviewAnnotationHistory(
  episodeId: number,
  buildId: number,
): Promise<PositionEpisodeReviewAnnotationHistoryResponse> {
  const { data } = await apiClient.get(
    `${BASE}/v2/position-episodes/${episodeId}/review-annotations`,
    { params: { build_id: buildId } },
  );
  const response = toCamelCase<
    PositionEpisodeReviewAnnotationHistoryResponse & {
      annotations?: PositionEpisodeReviewAnnotationHistoryResponse['items'];
    }
  >(data);
  return {
    ...response,
    items: response.items ?? response.annotations ?? [],
  };
}

export async function fetchReviewInsights(): Promise<ReviewInsightsResponse> {
  const { data } = await apiClient.get(`${BASE}/v2/review-insights`);
  return toCamelCase<ReviewInsightsResponse>(data);
}

/**
 * 个人画像回灌（你的战绩）：默认 build 已平仓回合的描述统计。
 * sessionCache 默认 10 分钟 TTL，与服务端进程内缓存同一口径——
 * 每会话/10 分钟最多一次网络请求；not_built 不缓存（构建完成立即可见）。
 */
export async function fetchPersonalEdge(refresh = false): Promise<PersonalEdgeResponse> {
  const key = 'journal:personal-edge';
  if (!refresh) {
    const cached = sessionCache.get<PersonalEdgeResponse>(key);
    if (cached) return cached;
  }
  const { data } = await apiClient.get(`${BASE}/v2/personal-edge`);
  const camel = toCamelCase<PersonalEdgeResponse>(data);
  if (camel.dataState === 'ready') {
    sessionCache.set(key, camel);
  }
  return camel;
}

/**
 * 交易纪律证据表（/rules 页）：与车道遵守度同一干净口径的后端读数。
 *
 * 所有统计量都在后端算完，前端只渲染。可选参数只改变「熔断触发算术」，
 * 不改变样本；它们不落库，也不代表系统知道你的账户规模。
 */
export async function fetchRulesEvidence(
  params: {
    buildId?: number;
    ticketUsd?: number;
    dailyBreakerUsd?: number;
    maxConcurrent?: number;
  } = {},
): Promise<RulesEvidenceResponse> {
  const query: Record<string, number> = {};
  if (params.buildId !== undefined) query.build_id = params.buildId;
  if (params.ticketUsd !== undefined) query.ticket_usd = params.ticketUsd;
  if (params.dailyBreakerUsd !== undefined) query.daily_breaker_usd = params.dailyBreakerUsd;
  if (params.maxConcurrent !== undefined) query.max_concurrent = params.maxConcurrent;
  const { data } = await apiClient.get(`${BASE}/v2/rules-evidence`, { params: query });
  return toCamelCase<RulesEvidenceResponse>(data);
}

export async function fetchPlaybook(): Promise<PlaybookListResponse> {
  const { data } = await apiClient.get(`${BASE}/v2/playbook`);
  return toCamelCase<PlaybookListResponse>(data);
}

export async function createPlaybookCandidate(
  request: CreatePlaybookCandidateRequest,
): Promise<CreatePlaybookCandidateResponse> {
  const { data } = await apiClient.post(`${BASE}/v2/playbook/candidates`, {
    title: request.title.trim(),
    rule_text: request.ruleText.trim(),
    source_bucket: request.sourceBucket
      ? {
          group_kind: request.sourceBucket.groupKind,
          group_value: request.sourceBucket.groupValue,
          direction: request.sourceBucket.direction,
          boundary_policy: request.sourceBucket.boundaryPolicy,
        }
      : null,
  });
  return toCamelCase<CreatePlaybookCandidateResponse>(data);
}

export async function promotePlaybookCandidate(
  candidateKey: string,
  request: PromotePlaybookCandidateRequest = {},
): Promise<PromotePlaybookCandidateResponse> {
  const { data } = await apiClient.post(
    `${BASE}/v2/playbook/candidates/${candidateKey}/promote`,
    {
      allow_new_version: request.allowNewVersion ?? false,
      expected_current_version: request.expectedCurrentVersion ?? null,
    },
  );
  return toCamelCase<PromotePlaybookCandidateResponse>(data);
}

export async function fetchEpisodePlaybookLinks(
  episodeId: number,
  buildId: number,
): Promise<EpisodePlaybookLinksResponse> {
  const { data } = await apiClient.get(
    `${BASE}/v2/position-episodes/${episodeId}/playbook-links`,
    { params: { build_id: buildId } },
  );
  return toCamelCase<EpisodePlaybookLinksResponse>(data);
}

export async function retirePlaybookRule(
  lineageKey: string,
  expectedCurrentVersion: number,
): Promise<RetirePlaybookRuleResponse> {
  const { data } = await apiClient.post(
    `${BASE}/v2/playbook/rules/${lineageKey}/retire`,
    { expected_current_version: expectedCurrentVersion },
  );
  return toCamelCase<RetirePlaybookRuleResponse>(data);
}

export async function savePositionEpisodeReviewAnnotation(
  episodeId: number,
  request: SavePositionEpisodeReviewAnnotationRequest,
): Promise<SavePositionEpisodeReviewAnnotationResponse> {
  const cleanLabels = (values: string[]) => (
    [...new Set(values.map((value) => value.trim()).filter(Boolean))]
  );
  const { data } = await apiClient.post(
    `${BASE}/v2/position-episodes/${episodeId}/review-annotations`,
    {
      build_id: request.buildId,
      review_status: request.reviewStatus,
      setup_thesis: request.setupThesis.trim(),
      entry_trigger: request.entryTrigger.trim(),
      invalidation_plan: request.invalidationPlan.trim(),
      position_rationale: request.positionRationale.trim(),
      exit_reason: request.exitReason.trim(),
      post_trade_reflection: request.postTradeReflection.trim(),
      tags: cleanLabels(request.tags),
      error_types: cleanLabels(request.errorTypes),
    },
  );
  return toCamelCase<SavePositionEpisodeReviewAnnotationResponse>(data);
}

export async function createPositionEpisodeAiReview(
  episodeId: number,
  buildId: number,
  enhanceWithModel = true,
  userContext?: PositionEpisodeAiReviewUserContext,
): Promise<PositionEpisodeAiReviewResponse> {
  const snakeContext: Record<string, string> = {};
  const addContextField = (key: string, value?: string) => {
    const trimmed = value?.trim();
    if (trimmed) snakeContext[key] = trimmed;
  };
  addContextField('setup_thesis', userContext?.setupThesis);
  addContextField('entry_trigger', userContext?.entryTrigger);
  addContextField('invalidation_plan', userContext?.invalidationPlan);
  addContextField('position_rationale', userContext?.positionRationale);
  addContextField('exit_reason', userContext?.exitReason);
  addContextField('post_trade_reflection', userContext?.postTradeReflection);
  const requestBody = Object.keys(snakeContext).length > 0
    ? { user_context: snakeContext }
    : undefined;
  const { data } = await apiClient.post(
    `${BASE}/v2/position-episodes/${episodeId}/ai-review`,
    requestBody,
    // Evidence-only reviews return without a provider call. Model enhancement
    // has a 20-second server wall-clock deadline plus market-context loading.
    { params: { build_id: buildId, enhance: enhanceWithModel }, timeout: 45000 },
  );
  return toCamelCase<PositionEpisodeAiReviewResponse>(data);
}

export async function createPositionEpisodeBuild(): Promise<EpisodeBuildResponse> {
  const { data } = await apiClient.post(`${BASE}/v2/episode-builds`, undefined, {
    params: { accept_assumed_flat: true },
  });
  return toCamelCase<EpisodeBuildResponse>(data);
}

export async function fetchCanonicalEpisodeBuildPreview(
  canonicalSetId?: number,
  accountKey?: string,
): Promise<CanonicalEpisodeBuildPlanResponse> {
  const { data } = await apiClient.get(`${BASE}/v2/episode-builds/canonical/preview`, {
    params: {
      canonical_set_id: canonicalSetId,
      account_key: accountKey,
    },
  });
  return toCamelCase<CanonicalEpisodeBuildPlanResponse>(data);
}

export async function createCanonicalPositionEpisodeBuild(
  request: CanonicalEpisodeBuildConfirmRequest,
  accountKey?: string,
): Promise<EpisodeBuildResponse> {
  const { data } = await apiClient.post(
    `${BASE}/v2/episode-builds/canonical`,
    {
      canonical_set_id: request.canonicalSetId,
      canonical_set_sha256: request.canonicalSetSha256,
      build_key: request.buildKey,
      accept_assumed_flat: request.acceptAssumedFlat,
      accept_group_fee_scope: request.acceptGroupFeeScope,
    },
    { params: { account_key: accountKey } },
  );
  return toCamelCase<EpisodeBuildResponse>(data);
}

export async function fetchEpisodeBuildActivation(): Promise<EpisodeBuildActivationState> {
  const { data } = await apiClient.get(`${BASE}/v2/episode-builds/activation`);
  return toCamelCase<EpisodeBuildActivationState>(data);
}

export async function activateEpisodeBuild(
  buildId: number,
  request: EpisodeBuildActivationRequest,
): Promise<EpisodeBuildActivationResponse> {
  const { data } = await apiClient.post(
    `${BASE}/v2/episode-builds/${buildId}/activate`,
    {
      expected_build_key: request.expectedBuildKey,
      expected_current_activation_id: request.expectedCurrentActivationId ?? null,
      expected_current_build_id: request.expectedCurrentBuildId ?? null,
      accept_assumed_flat: request.acceptAssumedFlat,
      accept_group_fee_scope: request.acceptGroupFeeScope,
      accept_left_censored_openings: request.acceptLeftCensoredOpenings,
    },
  );
  return toCamelCase<EpisodeBuildActivationResponse>(data);
}

export async function fetchStatsByStyle(params: {
  startDate?: string;
  endDate?: string;
  topN?: number;
  refresh?: boolean;
} = {}): Promise<JournalStatsByStyleResponse> {
  const topN = params.topN ?? 5;
  // Cache-key includes inputs; "no inputs" is the typical case (whole-history
  // breakdown) so it consistently hits the same key across page-mounts.
  const key = `journal:stats-by-style:${params.startDate ?? ''}:${params.endDate ?? ''}:${topN}`;
  if (!params.refresh) {
    const cached = sessionCache.get<JournalStatsByStyleResponse>(key);
    if (cached) return cached;
  }
  const { data } = await apiClient.get(`${BASE}/stats-by-style`, {
    params: {
      start_date: params.startDate,
      end_date: params.endDate,
      top_n: topN,
    },
  });
  const camel = toCamelCase<JournalStatsByStyleResponse>(data);
  // Only cache when there's real content — empty buckets get re-fetched.
  if (camel.totalCount > 0 || camel.byStyle.length > 0) {
    sessionCache.set(key, camel);
  }
  return camel;
}

export async function askJournalQa(req: JournalQaRequest): Promise<JournalQaResponse> {
  const { data } = await apiClient.post(
    `${BASE}/qa`,
    {
      framework: req.framework,
      question: req.question,
      trade_window_days: req.tradeWindowDays ?? 30,
      trade_limit: req.tradeLimit ?? 50,
    },
    { timeout: 60000 },
  );
  return toCamelCase<JournalQaResponse>(data);
}
