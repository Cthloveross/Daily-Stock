import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  HealthCheckItem,
  ImportResponse,
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
  // Leave Content-Type unset so axios/browser add the multipart boundary correctly.
  const { data } = await apiClient.post(`${BASE}/import`, form, {
    params: { broker },
  });
  return toCamelCase<ImportResponse>(data);
}
