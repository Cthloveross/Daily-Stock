import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  DailyOpportunityRun,
  OpportunityLearningSummaryResponse,
  OpportunityOptionContextResponse,
  OpportunityOptionEventResponse,
  OpportunityOptionOverviewResponse,
  OpportunityOptionWallResponse,
  PremarketCycleResponse,
  PremarketUniverseResponse,
  OpportunitySnapshot,
  OpportunitySnapshotEnsureResponse,
  OpportunitySnapshotDetailResponse,
  OpportunitySnapshotEvaluationResponse,
  OpportunitySnapshotListResponse,
} from '../types/opportunities';
import { sessionCache } from '../utils/sessionCache';

const CACHE_TTL_MS = 5 * 60 * 1000;
const DAILY_OPPORTUNITY_TIMEOUT_MS = 35_000;
const OPTION_CONTEXT_TIMEOUT_MS = 30_000;
const OPTION_EVENT_TIMEOUT_MS = 30_000;
const OPTION_EVENT_CACHE_TTL_MS = 30_000;
const OPTION_WALL_TIMEOUT_MS = 45_000;
const OPPORTUNITY_LEARNING_TIMEOUT_MS = 120_000;
const PREMARKET_STATUS_TIMEOUT_MS = 30_000;
const PREMARKET_RUN_TIMEOUT_MS = 35_000;
const PREMARKET_UNIVERSE_TIMEOUT_MS = 30_000;
const US_OPTION_UNDERLYING_PATTERN = /^[A-Z]{1,5}(?:[.-][A-Z])?$/;
const dailyInFlight = new Map<string, Promise<DailyOpportunityRun>>();
const optionContextInFlight = new Map<string, Promise<OpportunityOptionContextResponse>>();
const optionEventInFlight = new Map<string, Promise<OpportunityOptionEventResponse>>();
const optionOverviewInFlight = new Map<string, Promise<OpportunityOptionOverviewResponse>>();
const optionWallInFlight = new Map<string, Promise<OpportunityOptionWallResponse>>();
const premarketStatusInFlight = new Map<string, Promise<PremarketCycleResponse>>();
const premarketRunInFlight = new Map<string, Promise<PremarketCycleResponse>>();

function normalizedSymbols(symbols: string[]): string[] {
  return [...new Set(symbols.map((item) => item.trim().toUpperCase()).filter(Boolean))];
}

function cacheKey(symbols: string[], limit: number): string {
  const universe = normalizedSymbols(symbols).sort();
  return `opportunities:daily:${universe.join(',')}:${limit}`;
}

function premarketCycleRequestKey(limit: number): string {
  return `official:${limit}`;
}

function hasUsableDailyCandidate(run: DailyOpportunityRun): boolean {
  return run.candidates.some((candidate) => {
    const history = candidate.readiness.find((item) => item.domain === 'daily_history');
    return candidate.researchState !== 'blocked'
      && (history?.state === 'ready' || history?.state === 'stale');
  });
}

export function normalizeSupportedUsOptionUnderlying(symbol: string): string | null {
  const normalized = symbol.trim().toUpperCase();
  const withoutMarketPrefix = normalized.startsWith('US.') ? normalized.slice(3) : normalized;
  return US_OPTION_UNDERLYING_PATTERN.test(withoutMarketPrefix) ? withoutMarketPrefix : null;
}

function normalizedOptionContextSymbols(symbols: string[]): string[] {
  return [...new Set(symbols
    .map(normalizeSupportedUsOptionUnderlying)
    .filter((symbol): symbol is string => symbol !== null))];
}

function optionContextCacheKey(symbols: string[]): string {
  return `opportunities:option-context:${normalizedOptionContextSymbols(symbols).sort().join(',')}`;
}

function optionOverviewCacheKey(symbols: string[]): string {
  return `opportunities:option-overview:${normalizedOptionContextSymbols(symbols).sort().join(',')}`;
}

function optionWallCacheKey(symbols: string[], dteMin: number, dteMax: number): string {
  return `opportunities:option-walls-v1.1:${normalizedOptionContextSymbols(symbols).sort().join(',')}:${dteMin}:${dteMax}`;
}

function optionEventCacheKey(symbols: string[], limitPerSymbol: number): string {
  return `opportunities:option-events:${normalizedOptionContextSymbols(symbols).sort().join(',')}:${limitPerSymbol}`;
}

export async function fetchDailyOpportunities(
  symbols: string[],
  limit = 10,
  options: { refresh?: boolean } = {},
): Promise<DailyOpportunityRun> {
  const key = cacheKey(symbols, limit);
  if (!options.refresh) {
    const cached = sessionCache.get<DailyOpportunityRun>(key);
    if (cached) return cached;
  }

  const pending = dailyInFlight.get(key);
  if (pending) return pending;

  const request = apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/daily',
    { symbols, limit, refresh: Boolean(options.refresh) },
    // The list is the page's base layer.  It must fail closed in bounded time;
    // slower Moomoo option overview/wall/event requests are separate calls and
    // never extend this loading state.
    { timeout: DAILY_OPPORTUNITY_TIMEOUT_MS },
  ).then((response) => {
    const result = toCamelCase<DailyOpportunityRun>(response.data);
    const previous = sessionCache.get<DailyOpportunityRun>(key);
    if (!previous || hasUsableDailyCandidate(result) || !hasUsableDailyCandidate(previous)) {
      sessionCache.set(key, result, CACHE_TTL_MS);
    }
    return result;
  }).finally(() => {
    dailyInFlight.delete(key);
  });

  dailyInFlight.set(key, request);
  return request;
}

export async function fetchPremarketCycleStatus(
  limit = 5,
): Promise<PremarketCycleResponse> {
  const key = premarketCycleRequestKey(limit);
  const pending = premarketStatusInFlight.get(key);
  if (pending) return pending;

  const request = apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/premarket/status',
    { symbols: [], limit, manual: false },
    { timeout: PREMARKET_STATUS_TIMEOUT_MS },
  ).then((response) => toCamelCase<PremarketCycleResponse>(response.data))
    .finally(() => {
      premarketStatusInFlight.delete(key);
    });
  premarketStatusInFlight.set(key, request);
  return request;
}

export async function runPremarketCycle(
  limit = 5,
): Promise<PremarketCycleResponse> {
  const key = premarketCycleRequestKey(limit);
  const pending = premarketRunInFlight.get(key);
  if (pending) return pending;

  const request = apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/premarket/run',
    { symbols: [], limit, manual: true },
    { timeout: PREMARKET_RUN_TIMEOUT_MS },
  ).then((response) => toCamelCase<PremarketCycleResponse>(response.data))
    .finally(() => {
      premarketRunInFlight.delete(key);
    });
  premarketRunInFlight.set(key, request);
  return request;
}

export async function fetchPremarketUniverse(): Promise<PremarketUniverseResponse> {
  const response = await apiClient.get<Record<string, unknown>>(
    '/api/v1/opportunities/premarket/universe',
    { timeout: PREMARKET_UNIVERSE_TIMEOUT_MS },
  );
  return toCamelCase<PremarketUniverseResponse>(response.data);
}

export async function savePremarketUniverse(
  symbols: string[],
  limit = 5,
): Promise<PremarketUniverseResponse> {
  const response = await apiClient.put<Record<string, unknown>>(
    '/api/v1/opportunities/premarket/universe',
    { symbols: normalizedOptionContextSymbols(symbols).slice(0, 20), limit },
    { timeout: PREMARKET_UNIVERSE_TIMEOUT_MS },
  );
  return toCamelCase<PremarketUniverseResponse>(response.data);
}

export async function fetchOpportunityOptionContext(
  symbols: string[],
  options: { refresh?: boolean } = {},
): Promise<OpportunityOptionContextResponse> {
  const requestedSymbols = normalizedOptionContextSymbols(symbols).slice(0, 3);
  const key = optionContextCacheKey(requestedSymbols);
  if (!options.refresh) {
    const cached = sessionCache.get<OpportunityOptionContextResponse>(key);
    if (cached) return cached;
  }

  const pending = optionContextInFlight.get(key);
  if (pending) return pending;

  const request = apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/option-context',
    { symbols: requestedSymbols },
    { timeout: OPTION_CONTEXT_TIMEOUT_MS },
  ).then((response) => {
    const result = toCamelCase<OpportunityOptionContextResponse>(response.data);
    sessionCache.set(key, result, CACHE_TTL_MS);
    return result;
  }).finally(() => {
    optionContextInFlight.delete(key);
  });

  optionContextInFlight.set(key, request);
  return request;
}

export async function fetchOpportunityOptionOverview(
  symbols: string[],
  options: { refresh?: boolean } = {},
): Promise<OpportunityOptionOverviewResponse> {
  const requestedSymbols = normalizedOptionContextSymbols(symbols).slice(0, 15);
  const key = optionOverviewCacheKey(requestedSymbols);
  if (!options.refresh) {
    const cached = sessionCache.get<OpportunityOptionOverviewResponse>(key);
    if (cached) return cached;
  }

  const pending = optionOverviewInFlight.get(key);
  if (pending) return pending;

  const request = apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/option-overview',
    { symbols: requestedSymbols },
    { timeout: OPTION_CONTEXT_TIMEOUT_MS },
  ).then((response) => {
    const result = toCamelCase<OpportunityOptionOverviewResponse>(response.data);
    sessionCache.set(key, result, CACHE_TTL_MS);
    return result;
  }).finally(() => {
    optionOverviewInFlight.delete(key);
  });

  optionOverviewInFlight.set(key, request);
  return request;
}

export async function fetchOpportunityOptionWalls(
  symbols: string[],
  dteMin = 0,
  dteMax = 45,
  options: { refresh?: boolean } = {},
): Promise<OpportunityOptionWallResponse> {
  const requestedSymbols = normalizedOptionContextSymbols(symbols).slice(0, 5);
  const key = optionWallCacheKey(requestedSymbols, dteMin, dteMax);
  if (!options.refresh) {
    const cached = sessionCache.get<OpportunityOptionWallResponse>(key);
    if (cached) return cached;
  }

  const pending = optionWallInFlight.get(key);
  if (pending) return pending;

  const request = apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/option-walls',
    { symbols: requestedSymbols, dte_min: dteMin, dte_max: dteMax },
    { timeout: OPTION_WALL_TIMEOUT_MS },
  ).then((response) => {
    const result = toCamelCase<OpportunityOptionWallResponse>(response.data);
    sessionCache.set(key, result, CACHE_TTL_MS);
    return result;
  }).finally(() => {
    optionWallInFlight.delete(key);
  });

  optionWallInFlight.set(key, request);
  return request;
}

export async function fetchOpportunityOptionEvents(
  symbols: string[],
  limitPerSymbol = 5,
  options: { refresh?: boolean } = {},
): Promise<OpportunityOptionEventResponse> {
  const requestedSymbols = normalizedOptionContextSymbols(symbols).slice(0, 3);
  const key = optionEventCacheKey(requestedSymbols, limitPerSymbol);
  if (!options.refresh) {
    const cached = sessionCache.get<OpportunityOptionEventResponse>(key);
    if (cached) return cached;
  }

  const pending = optionEventInFlight.get(key);
  if (pending) return pending;

  const request = apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/option-events',
    { symbols: requestedSymbols, limit_per_symbol: limitPerSymbol },
    { timeout: OPTION_EVENT_TIMEOUT_MS },
  ).then((response) => {
    const result = toCamelCase<OpportunityOptionEventResponse>(response.data);
    sessionCache.set(key, result, OPTION_EVENT_CACHE_TTL_MS);
    return result;
  }).finally(() => {
    optionEventInFlight.delete(key);
  });

  optionEventInFlight.set(key, request);
  return request;
}

export async function freezeOpportunitySnapshot(
  symbols: string[],
  limit = 10,
): Promise<OpportunitySnapshot> {
  const response = await apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/snapshots/freeze',
    { symbols: normalizedSymbols(symbols), limit },
    { timeout: OPPORTUNITY_LEARNING_TIMEOUT_MS },
  );
  return toCamelCase<OpportunitySnapshot>(response.data);
}

export async function ensureOpportunitySnapshot(
  symbols: string[],
  limit = 10,
): Promise<OpportunitySnapshotEnsureResponse> {
  const response = await apiClient.post<Record<string, unknown>>(
    '/api/v1/opportunities/snapshots/ensure',
    { symbols: normalizedSymbols(symbols), limit },
    { timeout: OPPORTUNITY_LEARNING_TIMEOUT_MS },
  );
  return toCamelCase<OpportunitySnapshotEnsureResponse>(response.data);
}

export async function fetchOpportunitySnapshots(
  limit = 10,
): Promise<OpportunitySnapshotListResponse> {
  const response = await apiClient.get<Record<string, unknown>>(
    '/api/v1/opportunities/snapshots',
    { params: { limit }, timeout: 30_000 },
  );
  return toCamelCase<OpportunitySnapshotListResponse>(response.data);
}

export async function fetchOpportunitySnapshotDetail(
  snapshotKey: string,
): Promise<OpportunitySnapshotDetailResponse> {
  const response = await apiClient.get<Record<string, unknown>>(
    `/api/v1/opportunities/snapshots/${encodeURIComponent(snapshotKey)}`,
    { timeout: 30_000 },
  );
  return toCamelCase<OpportunitySnapshotDetailResponse>(response.data);
}

export async function evaluateOpportunitySnapshot(
  snapshotKey: string,
): Promise<OpportunitySnapshotEvaluationResponse> {
  const response = await apiClient.post<Record<string, unknown>>(
    `/api/v1/opportunities/snapshots/${encodeURIComponent(snapshotKey)}/evaluate`,
    undefined,
    { timeout: OPPORTUNITY_LEARNING_TIMEOUT_MS },
  );
  return toCamelCase<OpportunitySnapshotEvaluationResponse>(response.data);
}

export async function fetchOpportunityLearningSummary(): Promise<OpportunityLearningSummaryResponse> {
  const response = await apiClient.get<Record<string, unknown>>(
    '/api/v1/opportunities/learning-summary',
    { timeout: 30_000 },
  );
  return toCamelCase<OpportunityLearningSummaryResponse>(response.data);
}
