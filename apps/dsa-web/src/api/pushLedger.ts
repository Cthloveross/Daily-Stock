import apiClient from './index';
import { toCamelCase } from './utils';
import type { PushLedgerDailyResponse } from '../types/pushLedger';

const PUSH_LEDGER_TIMEOUT_MS = 30_000;

let inFlight: Promise<PushLedgerDailyResponse> | null = null;

/** 今日推送台账 feed（今日提示流）；同参并发合并为单请求。 */
export async function fetchPushLedgerDaily(): Promise<PushLedgerDailyResponse> {
  if (inFlight) return inFlight;
  const request = apiClient
    .get<Record<string, unknown>>('/api/v1/journal/push-ledger/daily', {
      timeout: PUSH_LEDGER_TIMEOUT_MS,
    })
    .then((response) => toCamelCase<PushLedgerDailyResponse>(response.data))
    .finally(() => {
      inFlight = null;
    });
  inFlight = request;
  return request;
}
