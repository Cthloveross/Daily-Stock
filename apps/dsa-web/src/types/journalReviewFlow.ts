/**
 * 引导式日终复盘流 + MAE/MFE（蓝图 17 Phase A）的前端类型。
 *
 * 全部判定（车道、四象限、过程指标）由服务端计算，前端只收集人的输入。
 * 盲评契约：`reveal` 仅在会话密封且已记录揭示后非空；此前载荷无任何盈亏。
 */

export type SessionKind = 'trading_day' | 'rest_day';
export type MetricBasis = 'auto' | 'manual' | 'missing';
export type LaneVerdict = 'compliant' | 'violation' | 'uncovered' | 'unknown';

export interface DailyEpisodeVerdict {
  episodeId: number;
  rawSymbol: string;
  underlying: string;
  direction: string;
  lifecycleStatus: string;
  openedAt: string;
  closedAt: string | null;
  dteAtEntry: number | null;
  lane: string;
  ruleId: string;
  verdict: LaneVerdict;
  openedToday: boolean;
  closedToday: boolean;
  needsAck: boolean;
  acked: boolean;
}

export interface OpenPositionExitPlan {
  episodeId: number;
  rawSymbol: string;
  underlying: string;
  openedAt: string;
  dteAtEntry: number | null;
  hasExitPlan: boolean;
  exitPlanText: string;
  exitPlanRegisteredAt: string | null;
}

export interface ProcessMetric {
  metricId: number;
  name: string;
  basis: MetricBasis;
  valueRatio: number | null;
  valueText: string | null;
  numerator: number | null;
  denominator: number | null;
  reason: string | null;
  manualAllowed: boolean;
  manualValue: string | null;
}

export interface DailyReviewSession {
  sessionId: number;
  accountKey: string;
  etDate: string;
  sessionKind: SessionKind;
  revision: number;
  previousSessionId: number | null;
  episodeBuildId: number | null;
  startedAt: string;
  sealedAt: string | null;
  revealedAt: string | null;
  revealedAfterSeal: boolean;
  steps: Record<string, unknown>;
  processScores: Array<Record<string, unknown>>;
  violationAcks: Array<{
    positionEpisodeId: number;
    lane: string;
    ruleId: string;
    ackText: string;
  }>;
  note: string;
  contentSha256: string;
  createdAt: string;
}

export interface RevealEpisode {
  episodeId: number;
  rawSymbol: string;
  verdict: LaneVerdict;
  quadrant: string;
  quadrantLabel: string;
  pnlNet: number | null;
}

export interface RevealSummary {
  etDate: string;
  closedEpisodeCount: number;
  pnlKnownCount: number;
  totalNet: number | null;
  /** 键已被 camelcase 转换：deservedWin / badLuck / undeservedWin / deservedLoss / notJudged */
  quadrantCounts: Record<string, number>;
  episodes: RevealEpisode[];
}

export interface DailyReviewFlowResponse {
  dataState: 'ready' | 'not_built';
  accountKey: string;
  etDate: string;
  buildId: number | null;
  buildKey: string | null;
  session: DailyReviewSession | null;
  restDayCandidate: boolean;
  restStreak: number;
  episodesToday: DailyEpisodeVerdict[];
  openPositions: OpenPositionExitPlan[];
  processMetrics: ProcessMetric[];
  mistakeVocabulary: string[];
  reveal: RevealSummary | null;
  limitations: string[];
}

export interface DailyReviewPostBody {
  etDate?: string;
  sessionKind?: SessionKind;
  steps?: Record<string, unknown>;
  manualScores?: Array<{ metricId: number; value: 'yes' | 'no' | 'missing'; note?: string }>;
  violationAcks?: Array<{ positionEpisodeId: number; ackText: string }>;
  exitPlans?: Array<{ positionEpisodeId: number; planText: string }>;
  note?: string;
  seal?: boolean;
  reveal?: boolean;
}

export interface DailyReviewPostResponse {
  created: boolean;
  idempotentReplay: boolean;
  session: DailyReviewSession;
  reveal: RevealSummary | null;
}

export interface EpisodeVerdictBlock {
  episodeId: number;
  buildId: number;
  lane: string;
  ruleId: string;
  verdict: LaneVerdict;
  quadrant: string;
  quadrantLabel: string;
  counterfactualCompliantExcluded: boolean | null;
  counterfactualCompliantText: string;
  counterfactualGateStatus: string;
  counterfactualGateReason: string;
}

export interface EpisodeExcursion {
  excursionId: number;
  episodeBuildId: number;
  positionEpisodeId: number;
  codeVersion: string;
  /** 同 code_version 下的重试序号（服务端读取已取最优/最新 attempt）。 */
  attempt?: number;
  source: string;
  status: 'ready' | 'partial' | 'missing_bars' | 'not_applicable';
  statusReason: string | null;
  exposure: number | null;
  u0: number | null;
  u0At: string | null;
  u0Flag: string | null;
  mfeUnderlyingPct: number | null;
  mfeAt: string | null;
  maeUnderlyingPct: number | null;
  maeAt: string | null;
  maeBeforeMfe: boolean | null;
  atr14: number | null;
  mfeAtr: number | null;
  maeAtr: number | null;
  barsUsed: number;
  coverageStart: string | null;
  coverageEnd: string | null;
  missingSessions: string[];
  computedAt: string;
}

export interface EpisodeExcursionResponse {
  dataState: 'ready' | 'missing' | 'not_built';
  episodeId: number;
  buildId: number | null;
  excursion: EpisodeExcursion | null;
  missingReason: string | null;
  verdict: EpisodeVerdictBlock | null;
  disclaimer: string;
  limitations: string[];
}

export interface ExcursionScatterItem {
  episodeId: number;
  rawSymbol: string;
  holdStructure: 'intraday' | 'overnight' | 'unknown';
  status: string;
  maeUnderlyingPct: number | null;
  mfeUnderlyingPct: number | null;
  maeAtr: number | null;
  mfeAtr: number | null;
  rMultiple: number | null;
  rMissingReason: string | null;
}

export interface ExcursionScatterResponse {
  dataState: 'ready' | 'not_built';
  accountKey: string;
  buildId: number | null;
  codeVersion: string;
  items: ExcursionScatterItem[];
  disclaimer: string;
  limitations: string[];
}
