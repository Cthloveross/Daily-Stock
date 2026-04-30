import apiClient from './index';
import { toCamelCase } from './utils';
import { sessionCache } from '../utils/sessionCache';
import type {
  HealthCheckItem,
  ImportResponse,
  JournalQaRequest,
  JournalQaResponse,
  JournalStatsByStyleResponse,
  JournalStatsResponse,
  RealityTestResponse,
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
