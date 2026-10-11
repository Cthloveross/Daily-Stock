import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  DailyReviewFlowResponse,
  DailyReviewPostBody,
  DailyReviewPostResponse,
  EpisodeExcursionResponse,
  ExcursionScatterResponse,
} from '../types/journalReviewFlow';

const BASE = '/api/v1/journal';

export async function fetchDailyReviewFlow(
  date?: string,
): Promise<DailyReviewFlowResponse> {
  const { data } = await apiClient.get(`${BASE}/review-flow/daily`, {
    params: { date },
  });
  return toCamelCase<DailyReviewFlowResponse>(data);
}

export async function postDailyReviewFlow(
  body: DailyReviewPostBody,
): Promise<DailyReviewPostResponse> {
  const { data } = await apiClient.post(`${BASE}/review-flow/daily`, {
    et_date: body.etDate,
    session_kind: body.sessionKind,
    steps: body.steps ?? {},
    manual_scores: (body.manualScores ?? []).map((item) => ({
      metric_id: item.metricId,
      value: item.value,
      note: item.note ?? '',
    })),
    violation_acks: (body.violationAcks ?? []).map((item) => ({
      position_episode_id: item.positionEpisodeId,
      ack_text: item.ackText,
    })),
    exit_plans: (body.exitPlans ?? []).map((item) => ({
      position_episode_id: item.positionEpisodeId,
      plan_text: item.planText,
    })),
    note: body.note ?? '',
    seal: body.seal ?? false,
    reveal: body.reveal ?? false,
  });
  return toCamelCase<DailyReviewPostResponse>(data);
}

/**
 * 揭示请求是最小请求（复核修复 1）：服务端逐字重放已密封修订的内容，
 * 客户端不重发任何内容字段——页面重载丢手填分、封卷后注解漂移都不会
 * 卡死揭示。expectedRevision 用于链位校验（不符则 422，提示刷新）。
 */
export async function postDailyReviewReveal(
  etDate: string,
  expectedRevision?: number,
): Promise<DailyReviewPostResponse> {
  const { data } = await apiClient.post(`${BASE}/review-flow/daily`, {
    et_date: etDate,
    reveal: true,
    ...(expectedRevision != null
      ? { expected_revision: expectedRevision }
      : {}),
  });
  return toCamelCase<DailyReviewPostResponse>(data);
}

export async function fetchEpisodeExcursion(
  episodeId: number,
  buildId?: number,
): Promise<EpisodeExcursionResponse> {
  const { data } = await apiClient.get(
    `${BASE}/episodes/${episodeId}/excursion`,
    { params: { build_id: buildId } },
  );
  return toCamelCase<EpisodeExcursionResponse>(data);
}

export async function fetchExcursionScatter(): Promise<ExcursionScatterResponse> {
  const { data } = await apiClient.get(`${BASE}/review-flow/excursions`);
  return toCamelCase<ExcursionScatterResponse>(data);
}
