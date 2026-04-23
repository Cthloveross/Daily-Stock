import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  BreakoutSignalsResponse,
  RegimeHistoryResponse,
  RegimeScoreItem,
} from '../types/regime';

export async function fetchRegimeToday(): Promise<RegimeScoreItem | null> {
  const { data } = await apiClient.get('/api/v1/regime/today');
  if (data === null) return null;
  return toCamelCase<RegimeScoreItem>(data);
}

export async function fetchRegimeHistory(days = 30): Promise<RegimeHistoryResponse> {
  const { data } = await apiClient.get('/api/v1/regime/history', { params: { days } });
  return toCamelCase<RegimeHistoryResponse>(data);
}

export async function recomputeRegime(): Promise<RegimeScoreItem> {
  const { data } = await apiClient.post('/api/v1/regime/recompute');
  return toCamelCase<RegimeScoreItem>(data);
}

export async function fetchBreakoutSignals(
  limit = 20,
  onlyFake?: boolean,
): Promise<BreakoutSignalsResponse> {
  const params: Record<string, string | number | boolean | undefined> = { limit };
  if (onlyFake !== undefined) params.only_fake = onlyFake;
  const { data } = await apiClient.get('/api/v1/breakout/signals', { params });
  return toCamelCase<BreakoutSignalsResponse>(data);
}
