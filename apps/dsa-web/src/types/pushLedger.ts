/**
 * 推送台账日 feed（蓝图 17 §2.2 Phase B）：今日提示流的唯一数据源。
 * 服务端 snake_case 经 toCamelCase 转换后的形状。
 */

export type PushTier = 'P0X' | 'P0' | 'P1' | 'P2' | 'P3' | 'system';

export type PushSessionScope = 'premarket' | 'regular' | 'afterhours';

export type PushDeliveryChannel =
  | 'telegram'
  | 'telegram_silent'
  | 'web_only'
  | 'ledger_only'
  | 'none';

export type PushDeliveryStatus =
  | 'not_requested'
  | 'pending'
  | 'succeeded'
  | 'failed'
  | 'indeterminate'
  | 'expired_unconfirmed';

export interface P02mSignalEvidence {
  state: string;
  alertQualified: boolean;
  alertTier: 'P0' | 'P0X' | null;
  signalVersion: string;
  symbol: string | null;
  universeHash: string | null;
  sessionScope: PushSessionScope | null;
  windowStart: string | null;
  impulseWindowStart: string | null;
  displacementWindowStart: string | null;
  windowEnd: string | null;
  direction: 'up' | 'down' | 'flat' | null;
  triggerPrice: number | null;
  impulse8mLog: number | null;
  impulse8mPercent: number | null;
  pricePercentile: number | null;
  rvol8m: number | null;
  referenceMedianVolume8m: number | null;
  displacement18mPercent: number | null;
  displacementAtr: number | null;
  priorDailyAtr14: number | null;
  baselineCount: number;
  baselineFirstDate: string | null;
  baselineLastDate: string | null;
  source: string | null;
  dataQuality: { status: 'complete' | 'incomplete'; issues: string[] };
  optionCandidates: Array<Record<string, unknown>>;
  researchFields: Record<string, unknown>;
  failedChecks: string[];
  unavailableReason: string | null;
  thresholds: Record<string, unknown>;
  basis: string;
  limitations: string[];
}

export interface PushLedgerEvent {
  pushId: string;
  recordKind: 'decision' | 'delivery';
  etDate: string;
  ts: string;
  tier: PushTier;
  sessionScope: PushSessionScope | null;
  rule: string;
  ticker: string | null;
  text: string;
  deliveryChannel: PushDeliveryChannel;
  suppressed: boolean;
  suppressedReason: string | null;
  gateState: string | null;
  laneRuleId: string | null;
  optionMid?: number | null;
  optionSpreadPct?: number | null;
  messageKey: string | null;
  telegramOk: boolean | null;
  deliveryStatus?: PushDeliveryStatus | null;
  conditions: Array<Record<string, unknown>>;
  p0SignalEvidenceV2?: P02mSignalEvidence | null;
}

export interface PushBudget {
  openingUsedToday: number;
  openingDailyBudget: number;
  p0UsedToday: number;
  p0DailyBudget: number;
  p0UsedWeek: number;
  p0WeeklyBudget: number;
  p0xUsedTodayBySession: Record<PushSessionScope, number>;
  p0xDailyBudgetBySession: Partial<Record<PushSessionScope, number>>;
  p0xUsedWeek: number;
  p0xWeeklyBudget: number;
  p1UsedToday: number;
  p1DailyBudget: number;
  telegramLinesUsed: number;
  telegramLinesCap: number;
  researchRthUsedToday?: number;
  researchRthDailyBudget?: number;
  researchRthUsedWeek?: number;
  researchRthWeeklyBudget?: number;
  researchExtendedUsedTodayBySession?: Partial<Record<PushSessionScope, number>>;
  researchExtendedDailyBudgetBySession?: Partial<Record<PushSessionScope, number>>;
  researchExtendedUsedWeek?: number;
  researchExtendedWeeklyBudget?: number;
}

export interface P0PassRate {
  status: 'ready' | 'missing';
  reason: string | null;
  window: number;
  delivered: number;
  passed: number;
  rate: number | null;
  threshold: number;
  autoTightenEnabled: boolean;
  limitations: string[];
}

export interface PushLedgerDailyResponse {
  etDate: string;
  events: PushLedgerEvent[];
  budgets: PushBudget;
  p0PassRate: P0PassRate;
  limitations: string[];
}
