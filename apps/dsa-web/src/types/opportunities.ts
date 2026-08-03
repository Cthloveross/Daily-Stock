export type OpportunityResearchState =
  | 'research_ready'
  | 'watch_only'
  | 'context_only'
  | 'blocked';

export type OpportunityEvidenceStatus = 'supports' | 'neutral' | 'contradicts' | 'unknown';

export type OpportunityReadinessState =
  | 'ready'
  | 'partial'
  | 'stale'
  | 'background_only'
  | 'not_configured'
  | 'unavailable';

export interface OpportunityReadiness {
  domain: string;
  state: OpportunityReadinessState;
  source?: string | null;
  asOf?: string | null;
  actionability: string;
  message: string;
}

export interface OpportunityEvidence {
  evidenceId: string;
  domain: string;
  metric: string;
  value?: unknown;
  unit?: string | null;
  status: OpportunityEvidenceStatus;
  source: string;
  observedAt?: string | null;
  publishedAt?: string | null;
  fetchedAt: string;
  observationWindow: string;
  qualityState: string;
  actionability: string;
  limitations: string[];
}

export interface OpportunityHardGate {
  gateId: string;
  status: string;
  reason: string;
  evidenceRefs: string[];
}

export interface OpportunityStyleMatch {
  status: string;
  source: string;
  matchedRules: string[];
  conflictingRules: string[];
  unknownFields: string[];
}

export interface OpportunityCandidate {
  candidateId: string;
  ticker: string;
  researchState: OpportunityResearchState;
  directionalContext: string;
  setupTags: string[];
  lastCompletedBarAt?: string | null;
  referenceSessionDate?: string | null;
  referenceClose?: number | null;
  referencePriceBasis: 'prior_completed_close';
  source?: string | null;
  styleMatch: OpportunityStyleMatch;
  hardGates: OpportunityHardGate[];
  evidence: OpportunityEvidence[];
  readiness: OpportunityReadiness[];
  supportingEvidenceCount: number;
  dataCompleteness: {
    state: string;
    availableCount: number;
    expectedCount: number;
  };
  unknowns: string[];
}

export interface DailyOpportunityRun {
  schemaVersion: string;
  runId: string;
  runType: string;
  marketDateEt: string;
  asOf: string;
  generatedAt: string;
  signalVersion: string;
  rankingMethod: 'rule_based_evidence_count';
  strategyValidationState: 'not_validated';
  strategyValidationMessage: string;
  universe: string[];
  requestedLimit: number;
  candidateCount: number;
  runReadiness: OpportunityReadiness[];
  candidates: OpportunityCandidate[];
}

export type PremarketCycleState =
  | 'non_session'
  | 'waiting_window'
  | 'ready_to_run'
  | 'research_pool_missing'
  | 'running'
  | 'published'
  | 'blocked'
  | 'window_closed'
  | 'failed';

export type PremarketCycleQuality = 'unknown' | 'ready' | 'degraded' | 'blocked';

export type PremarketUniverseSource =
  | 'persisted'
  | 'stock_list_fallback'
  | 'request_fallback'
  | 'unavailable';

export interface PremarketUniverseResponse {
  schemaVersion: 'premarket-research-universe/1.0' | string;
  configured: boolean;
  universeVersionKey: string | null;
  source: PremarketUniverseSource;
  symbols: string[];
  limit: number;
  createdAt: string | null;
  duplicate: boolean;
  message: string;
}

export type PremarketCycleStageState =
  | 'pending'
  | 'running'
  | 'completed'
  | 'degraded'
  | 'blocked'
  | 'failed'
  | 'skipped';

export interface PremarketCycleStage {
  name: string;
  state: PremarketCycleStageState;
  startedAt: string | null;
  completedAt: string | null;
  errorCode: string | null;
}

export interface PremarketCycleResponse {
  schemaVersion: 'canonical-premarket-cycle/1.0' | string;
  cycleVersion: string;
  freezePolicyVersion: string;
  scopeKey: string;
  cycleKey: string;
  state: PremarketCycleState;
  quality: PremarketCycleQuality;
  marketDateEt: string;
  previousSession: string | null;
  regularOpenAt: string | null;
  windowStartAt: string | null;
  windowEndAt: string | null;
  latestStartAt: string | null;
  cycleAsOf: string | null;
  startedAt: string | null;
  completedAt: string | null;
  retryAfterSeconds: number | null;
  universe: string[];
  requestedLimit: number;
  universeSource: PremarketUniverseSource;
  universeVersionKey: string | null;
  attemptKey: string | null;
  attemptTrigger: 'manual' | 'scheduler' | null;
  attemptStartedAt: string | null;
  leaseExpiresAt: string | null;
  recoveredFromAttemptKey: string | null;
  recoverable: boolean;
  nextScheduledAt: string | null;
  schedulerEnabled: boolean;
  primaryScheduledAt: string | null;
  retryScheduledAt: string | null;
  stages: PremarketCycleStage[];
  regimeQuality: Record<string, string>;
  qualityReasons: string[];
  run: DailyOpportunityRun | null;
  snapshot: OpportunitySnapshot | null;
  idempotentReplay: boolean;
  errorCode: string | null;
  message: string;
}

export type OpportunityOptionContextState = 'ready' | 'not_configured' | 'unavailable';

export interface OpportunityOptionContextItem {
  ticker: string;
  state: OpportunityOptionContextState;
  source: string;
  fetchedAt: string;
  expiry: string | null;
  atmCallIvPercent: number | null;
  message: string;
  limitations: string[];
}

export interface OpportunityOptionContextResponse {
  schemaVersion: string;
  marketDateEt: string;
  generatedAt: string;
  items: OpportunityOptionContextItem[];
}

export type OpportunityOptionOverviewState = 'ready' | 'not_configured' | 'unavailable';

export interface OpportunityOptionOverviewItem {
  ticker: string;
  name: string | null;
  state: OpportunityOptionOverviewState;
  source: string;
  fetchedAt: string;
  sessionVolumeDate: string;
  openInterestAsOf: string | null;
  volumeBasis: 'current_session_cumulative';
  openInterestBasis: 'prior_clearing_session';
  volatilityBasis: 'provider_snapshot';
  callVolume: number | null;
  putVolume: number | null;
  putCallVolumeRatio: number | null;
  callOpenInterest: number | null;
  putOpenInterest: number | null;
  putCallOpenInterestRatio: number | null;
  ivPercent: number | null;
  ivRankPercent: number | null;
  ivPercentilePercent: number | null;
  previousIvPercent: number | null;
  ivChangePoints: number | null;
  hv30dPercent: number | null;
  hv30dPercentile: number | null;
  hv60dPercent: number | null;
  hv60dPercentile: number | null;
  hv90dPercent: number | null;
  hv90dPercentile: number | null;
  hv120dPercent: number | null;
  hv120dPercentile: number | null;
  hv365dPercent: number | null;
  hv365dPercentile: number | null;
  ivHv30SpreadPoints: number | null;
  message: string;
  limitations: string[];
}

export interface OpportunityOptionOverviewResponse {
  schemaVersion: string;
  marketDateEt: string;
  generatedAt: string;
  items: OpportunityOptionOverviewItem[];
}

export type OpportunityOptionWallState =
  | 'ready'
  | 'partial'
  | 'not_configured'
  | 'unavailable';

export type OpportunityOptionWallQuoteEvidence = 'observed' | 'partial' | 'unavailable';

export type OpportunityOptionWallMetricBasis =
  | 'settled_open_interest_prior_session'
  | 'current_session_cumulative_volume'
  | 'model_from_settled_oi_and_snapshot_greeks';

/** Quote fields traced to the single snapshot row backing one expiry cell.
 * 快照未携带的字段保持 null（当前 Moomoo 墙快照行没有 bid/ask/mark），
 * 缺失通过 quoteEvidence 显式标缺，不做零值回填。 */
export interface OpportunityOptionWallLevelExpiryQuote {
  ivPercent: number | null;
  bid: number | null;
  ask: number | null;
  mark: number | null;
  quoteAsOf: string | null;
}

export interface OpportunityOptionWallLevelExpiry {
  expiry: string;
  dte: number | null;
  metricValue: number;
  shareOfLevelPercent: number;
  contractCount: number;
  quote: OpportunityOptionWallLevelExpiryQuote;
  quoteEvidence: OpportunityOptionWallQuoteEvidence;
}

export interface OpportunityOptionWallLevelExpiryBreakdown {
  topExpiries: OpportunityOptionWallLevelExpiry[];
  other: {
    expiryCount: number;
    metricValue: number;
    shareOfLevelPercent: number;
  } | null;
}

export interface OpportunityOptionWallLevel {
  rank: number;
  strike: number;
  distanceFromSpotPercent: number;
  metricValue: number;
  shareOfBucketPercent: number;
  unit: 'contracts' | 'usd_delta_change_per_1pct_move';
  method: 'sum_open_interest' | 'sum_session_volume' | 'gross_gamma_concentration_1pct';
  // Additive per-level fields (option-wall/1.2); optional so pre-1.2 payloads
  // remain valid without them.
  side?: 'call' | 'put' | 'call_put_aggregate' | null;
  metricBasis?: OpportunityOptionWallMetricBasis | null;
  quoteEvidence?: OpportunityOptionWallQuoteEvidence | null;
  expiryBreakdown?: OpportunityOptionWallLevelExpiryBreakdown | null;
}

export interface OpportunityOptionWallItem {
  ticker: string;
  state: OpportunityOptionWallState;
  source: string;
  fetchedAt: string;
  quoteAsOf: string | null;
  formulaVersion: string;
  spot: number | null;
  atmCallIv: {
    state: OpportunityOptionContextState;
    expiry: string | null;
    strike: number | null;
    atmCallIvPercent: number | null;
    selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot';
  };
  scope: {
    dteMin: number;
    dteMax: number;
    expiries: string[];
    standardContractsOnly: boolean;
  };
  coverage: {
    requestedContracts: number;
    snapshotReceivedContracts: number;
    validContracts: number;
    coveragePercent: number;
    failedBatches: number;
    excludedNonstandardContracts: number;
    excludedUnknownStandardTypeContracts: number;
    gammaContracts: number;
  };
  walls: {
    callOi: OpportunityOptionWallLevel[];
    putOi: OpportunityOptionWallLevel[];
    callVolume: OpportunityOptionWallLevel[];
    putVolume: OpportunityOptionWallLevel[];
    callGammaConcentration: OpportunityOptionWallLevel[];
    putGammaConcentration: OpportunityOptionWallLevel[];
    grossGammaConcentration: OpportunityOptionWallLevel[];
  };
  message: string;
  assumptions: string[];
  limitations: string[];
}

export interface OpportunityOptionWallResponse {
  schemaVersion: string;
  marketDateEt: string;
  generatedAt: string;
  items: OpportunityOptionWallItem[];
}

export type NearExpiryContractState =
  | 'ready'
  | 'partial'
  | 'empty'
  | 'not_configured'
  | 'unavailable';

/** 单张临期合约的只读读数：逐字段可空、缺失显式标缺；不含打分或推荐。 */
export interface NearExpiryContractRow {
  code: string;
  right: 'C' | 'P';
  strike: number;
  expiry: string;
  dte: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  spreadPercent: number | null;
  spreadUnavailableReason: string | null;
  lastPrice: number | null;
  sessionVolume: number | null;
  openInterest: number | null;
  ivPercent: number | null;
  delta: number | null;
  quoteAsOf: string | null;
  quoteState: 'observed' | 'unavailable';
  unavailableReason: string | null;
  isAtm: boolean;
}

export interface NearExpiryExpiryGroup {
  expiry: string;
  dte: number;
  state: 'ready' | 'partial' | 'unavailable';
  contractCount: number;
  observedQuoteCount: number;
  contracts: NearExpiryContractRow[];
}

/** 临期合约面板（0–max_dte DTE）：合约选择参考，不构成推荐。 */
export interface NearExpiryContractItem {
  ticker: string;
  state: NearExpiryContractState;
  source: string;
  fetchedAt: string;
  formulaVersion: string;
  maxDte: number;
  spot: number | null;
  spotAsOf: string | null;
  openInterestAsOf: string | null;
  openInterestBasis: 'prior_clearing_session';
  strikeWindow: {
    percentBand: number;
    minStrikesPerSide: number;
    basis: string;
  };
  coverage: {
    requestedContracts: number;
    snapshotReceivedContracts: number;
    observedContracts: number;
    missingContracts: number;
    failedBatches: number;
    excludedNonstandardContracts: number;
    excludedUnknownStandardTypeContracts: number;
  };
  expiries: NearExpiryExpiryGroup[];
  /**
   * v3 财报临近（additive 字段，与扫描表候选同形状、同一份日历缓存）。
   * 旧缓存载荷可能缺省 → 按「标缺 · 未知≠安全」处理，绝不冒充安全。
   */
  earningsProximity?: IntradayEarningsProximity;
  message: string;
  limitations: string[];
}

export interface NearExpiryContractResponse {
  schemaVersion: string;
  generatedAt: string;
  marketDateEt: string;
  item: NearExpiryContractItem;
}

export type OpportunityOptionEventState =
  | 'ready'
  | 'empty'
  | 'not_configured'
  | 'unavailable';

export interface OpportunityOptionEvent {
  eventId: string;
  optionCode: string;
  ownerCode: string | null;
  symbol: string | null;
  fillTime: string | null;
  tickerType: string | null;
  price: number | null;
  volume: number | null;
  turnover: number | null;
  optionType: string | null;
  strikePrice: number | null;
  expiry: string | null;
  dte: number | null;
  underlyingPrice: number | null;
  bidPrice: number | null;
  askPrice: number | null;
  ivPercent: number | null;
  totalVolume: number | null;
  totalOpenInterest: number | null;
  voRatioPercent: number | null;
  delta: number | null;
  sentiment: string | null;
  orderTypes: string[];
  strategyType: string | null;
}

export interface OpportunityOptionEventItem {
  ticker: string;
  state: OpportunityOptionEventState;
  source: string;
  fetchedAt: string;
  eventAsOf: string | null;
  allCount: number | null;
  events: OpportunityOptionEvent[];
  message: string;
  limitations: string[];
}

export interface OpportunityOptionEventResponse {
  schemaVersion: string;
  marketDateEt: string;
  generatedAt: string;
  items: OpportunityOptionEventItem[];
}

export interface OpportunityOutcomeProgress {
  horizonSessions: 5 | 20;
  eligibleCount: number;
  matureCount: number;
  pendingCount: number;
  partialCount: number;
  dataGapCount?: number;
}

export type OpportunityQualificationTrackKey =
  | 'raw_underlying_path_v1'
  | 'underlying_daily_selection_v1'
  | 'canonical_full_research_v1';

export interface OpportunityQualificationTrackSummary {
  trackKey: OpportunityQualificationTrackKey;
  qualifiedCount: number;
  excludedCount: number;
  unverifiedCount: number;
  prospectiveCount: number;
  retrospectiveCount: number;
  observationReadyCount: number;
}

export interface OpportunityQualificationSummary {
  assessmentKey: string;
  policyVersion: string;
  publicationState: 'canonical_published' | 'audit_frozen' | 'legacy_unverified';
  analysisQualityState: 'ready' | 'degraded' | 'blocked' | 'unassessed';
  assessedAt: string;
  reasonCodes: string[];
  tracks: OpportunityQualificationTrackSummary[];
}

export interface OpportunitySnapshot {
  schemaVersion: string;
  snapshotKey: string;
  marketDateEt: string;
  sourceRunId: string;
  frozenAt: string;
  signalVersion: string;
  candidateCount: number;
  eligibleCandidateCount: number;
  validationEligible: boolean;
  eligibilityReasons: string[];
  analysisQualityEligible?: boolean | null;
  analysisQualityReasons?: string[];
  qualification?: OpportunityQualificationSummary | null;
  outcomeProgress: OpportunityOutcomeProgress[];
  underlyingPathCandidateCount?: number;
  underlyingPathProgress?: OpportunityOutcomeProgress[];
  fullResearchCandidateCount?: number;
  fullResearchProgress?: OpportunityOutcomeProgress[];
  idempotentReplay: boolean;
}

export interface OpportunitySnapshotListResponse {
  schemaVersion: string;
  items: OpportunitySnapshot[];
}

export interface OpportunitySnapshotDetailResponse {
  schemaVersion: string;
  snapshot: OpportunitySnapshot;
  /** The frozen board run payload, verbatim as published (same shape as DailyOpportunityRun). */
  run: DailyOpportunityRun;
}

export interface OpportunitySnapshotEnsureResponse {
  schemaVersion: string;
  state: 'saved' | 'existing' | 'outside_window' | 'unavailable';
  marketDateEt: string;
  snapshot: OpportunitySnapshot | null;
  message: string;
}

export interface OpportunitySnapshotEvaluationResponse {
  schemaVersion: string;
  snapshotKey: string;
  evaluatedAt: string;
  candidateCount: number;
  trackingCandidateCount?: number;
  insertedOutcomes: number;
  alreadyRecorded: number;
  pendingHorizons: number;
  dataGapHorizons: number;
  outcomeProgress: OpportunityOutcomeProgress[];
  underlyingPathProgress?: OpportunityOutcomeProgress[];
  fullResearchProgress?: OpportunityOutcomeProgress[];
  message: string;
}

export interface OpportunityLearningHorizon {
  horizonSessions: 5 | 20;
  matureCount: number;
  distinctSignalSessions: number;
  directionalSampleCount: number;
  contextHitCount: number | null;
  contextMissCount: number | null;
  neutralCount: number | null;
  nonDirectionalCount: number | null;
  contextHitRatePercent: number | null;
  summaryVisible: boolean;
  investigationReady: boolean;
  cohortKey?: string | null;
  cohortLabel?: string | null;
  excludedQualityCount?: number;
}

export interface OpportunityOutcomeMaintenanceStatus {
  sessionDateEt: string;
  policyVersion: string;
  state: 'pending' | 'running' | 'completed' | 'degraded' | 'failed';
  attemptCount: number;
  completedAt: string | null;
  nextRetryAt: string | null;
  dueSnapshotCount: number;
  insertedOutcomes: number;
  dataGapHorizons: number;
  lastErrorCode: string | null;
}

export interface OpportunityLearningSummaryResponse {
  schemaVersion: string;
  generatedAt: string;
  strategyState: 'collecting' | 'descriptive_summary_available' | 'investigation_ready';
  autoAdjustment: false;
  minimumSummarySamples: number;
  minimumInvestigationSamples: number;
  automaticMaintenanceEnabled: boolean;
  maintenancePolicyVersion: string | null;
  latestMaintenance: OpportunityOutcomeMaintenanceStatus | null;
  horizons: OpportunityLearningHorizon[];
  limitations: string[];
}

export type IntradaySessionState = 'premarket' | 'regular' | 'afterhours' | 'closed';

/** v3 时段上下文：常规时段按用户自身历史纪律再切分（提示文案为硬编码 v1）。 */
export type IntradaySessionPhase =
  | 'premarket'
  | 'opening_probe'
  | 'prime'
  | 'midday'
  | 'noise'
  | 'afternoon'
  | 'power_hour'
  | 'afterhours'
  | 'closed';

export type IntradayTrackingItemState =
  | 'ready'
  | 'partial'
  | 'not_configured'
  | 'unavailable';

/** 盘中跟踪单标的行：只对照冻结盘前计划，缺失字段显式标缺，不含买卖信号。 */
export interface IntradayTrackingItem {
  ticker: string;
  state: IntradayTrackingItemState;
  source: string;
  fetchedAt: string;
  quoteAsOf: string | null;
  lastPrice: number | null;
  sessionOpen: number | null;
  sessionHigh: number | null;
  sessionLow: number | null;
  prevClose: number | null;
  sessionVolume: number | null;
  sessionTurnover: number | null;
  vwap: number | null;
  vwapBasis: 'session_turnover_over_volume';
  vwapUnavailableReason: string | null;
  atr14: number | null;
  atr14Method: 'wilder_smoothing_14_daily_completed_bars';
  atr14BarCount: number;
  atr14LastBarDate: string | null;
  atr14Source: string | null;
  atr14UnavailableReason: string | null;
  volumePaceRatio: number | null;
  volumePaceBasis: 'session_cumulative_vs_prior_20_session_full_day_median';
  prior20dMedianVolume: number | null;
  volumePaceUnavailableReason: string | null;
  message: string;
  limitations: string[];
}

export interface IntradayTrackingResponse {
  schemaVersion: string;
  generatedAt: string;
  marketDateEt: string;
  sessionState: IntradaySessionState;
  sessionStateBasis: 'america_new_york_clock_v1';
  trackingBasis: 'frozen_premarket_plan_readonly';
  items: IntradayTrackingItem[];
  limitations: string[];
}

/** 日内滚动研究状态：盘中活跃 / 观察 / 数据不足（缺核心快照时 fail-closed）。 */
export type IntradayTopResearchState = 'active' | 'watch' | 'insufficient';

export type IntradayTopDominantSentiment =
  | 'bullish'
  | 'bearish'
  | 'neutral'
  | 'mixed'
  | 'unknown';

/** 一页有界 Moomoo 异动的诚实聚合：只有计数与供应商分类，不推断开平仓。 */
export interface IntradayTopOptionActivity {
  state: 'ready' | 'empty' | 'not_configured' | 'unavailable';
  count: number;
  allCount: number | null;
  bullishCount: number;
  bearishCount: number;
  neutralCount: number;
  unclassifiedCount: number;
  dominantSentiment: IntradayTopDominantSentiment;
  maxSingleTurnover: number | null;
  eventAsOf: string | null;
  fetchedAt: string | null;
  source: string;
  limitations: string[];
}

/** 上一完整交易日结构背景；仅作 research_context，不参与盘中排序。 */
export interface IntradayTopPriorDayContext {
  priorClose: number | null;
  priorCloseDate: string | null;
  priorHigh20d: number | null;
  priorLow20d: number | null;
  rangePosition:
    | 'above_prior_20d_high'
    | 'below_prior_20d_low'
    | 'inside_prior_20d_range'
    | null;
  emaAlignment: 'bullish' | 'bearish' | 'mixed' | null;
}

/** 一个 15 分钟滚动窗口的爆发读数：推力、量比与两者乘积的爆发分。 */
export interface IntradayBurstWindow {
  startEt: string;
  endEt: string;
  thrustPercent: number | null;
  thrustNorm: number | null;
  volNorm: number | null;
  score: number | null;
  direction: 'up' | 'down' | 'flat';
}

/** v3 速度分级：相邻两个滚动 15 分钟窗口爆发分之差（5m K 线近似）。 */
export interface IntradayBurstSpeed {
  state: 'accelerating' | 'decelerating' | 'flat' | 'unknown';
  currentScore: number | null;
  previousScore: number | null;
  delta: number | null;
  basis: 'consecutive_rolling_15m_window_burst_score_delta_5m_bars';
  unavailableReason: string | null;
}

/** v3 财报临近标记：前向 5 天窗口内最近财报日；within_blackout=null 表示未知。 */
export interface IntradayEarningsProximity {
  state: 'ready' | 'unavailable';
  daysToEarnings: number | null;
  earningsDate: string | null;
  withinBlackout: boolean | null;
  blackoutDays: number;
  windowDays: number;
  basis: 'finnhub_earnings_calendar_forward_window';
  source: string;
  fetchedAt: string | null;
  unavailableReason: string | null;
}

/** v3 大盘对齐：候选当前爆发方向 vs SPY 会话 VWAP 位置，仅作标注。 */
export interface IntradayMarketAlignment {
  state: 'aligned' | 'against' | 'unknown';
  burstDirection: 'up' | 'down' | 'flat' | null;
  spyVwapPosition: 'above' | 'below' | 'flat' | null;
  basis: 'candidate_current_burst_direction_vs_spy_session_vwap_position';
  unavailableReason: string | null;
}

/** v3 SPY 大盘上下文：会话 VWAP 位置（累计额/量近似）+ 明确 provenance。 */
export interface IntradayMarketContext {
  ticker: string;
  state: 'ready' | 'not_configured' | 'unavailable';
  lastPrice: number | null;
  vwap: number | null;
  vwapPosition: 'above' | 'below' | 'flat' | 'unknown';
  vwapBasis: 'session_turnover_over_volume';
  quoteAsOf: string | null;
  fetchedAt: string | null;
  source: string;
  unavailableReason: string | null;
}

/** v4 styleMatch：S1/S2/S3 的 setup key。 */
export type IntradaySetupKey = 'S1' | 'S2' | 'S3';

/** S1/S2/S3 形态对应的 Playbook 条目：只读展示，规则不反哺评分或排序。 */
export interface IntradaySetupPlaybookRef {
  setupKey: IntradaySetupKey;
  candidateKey: string | null;
  status: 'candidate' | 'promoted';
  title: string;
}

/** 单个 setup 的 v1 几何相似度：matched/partial/not_matched/unavailable。 */
export interface IntradaySetupMatch {
  setupKey: IntradaySetupKey;
  label: string;
  title: string;
  state: 'matched' | 'partial' | 'not_matched' | 'unavailable';
  reason: string;
  evidenceLines: string[];
  basis: string;
  playbook: IntradaySetupPlaybookRef | null;
}

/**
 * v4 styleMatch v1：当前时段几何形状 vs 用户三个 Playbook setup。
 * 纯标注：不参与排序、不隐藏行、不是信号；5m 聚合到 15m 近似，非 2m/1m 确认帧。
 */
export interface IntradaySetupMatchProfile {
  state: 'ready' | 'unavailable';
  styleMatchVersion: 'style_match_v1';
  quoteSessionScope: 'current_session' | 'latest_prior_session';
  sessionDateEt: string | null;
  barCount5M: number;
  barCount15M: number;
  matchedSetups: IntradaySetupKey[];
  partialSetups: IntradaySetupKey[];
  setups: IntradaySetupMatch[];
  basis: string;
  unavailableReason: string | null;
  limitations: string[];
}

/** 波段爆发（v2 主信号）：当前窗口 + 当日（或最近一个交易时段）波段列表。 */
export interface IntradaySessionBursts {
  state: 'ready' | 'insufficient_bars' | 'unavailable';
  sessionDateEt: string | null;
  barCount: number;
  medianBarRange: number | null;
  medianBarVolume: number | null;
  medianBasis: 'current_session_bars_so_far' | 'prior_session_fallback' | null;
  windowMinutes: number;
  current: IntradayBurstWindow | null;
  legs: IntradayBurstWindow[];
  speed: IntradayBurstSpeed;
  unavailableReason: string | null;
  source: string | null;
  fetchedAt: string | null;
  basis: string;
  limitations: string[];
}

/** 两层模式下该候选进入深度层的原因：计划钉选或异动排名（v1 闸门，非信号）。 */
export interface IntradayDeepLaneReason {
  promotedBy: 'plan_always_include' | 'mover_rank';
  moverRank: number | null;
  basis: 'abs_change_percent_then_turnover_v1';
}

/** 宽层（仅快照）单行：只有快照可得字段，绝不虚构深度层读数。 */
export interface IntradaySnapshotOnlyRow {
  ticker: string;
  state: 'ready' | 'partial' | 'unavailable';
  lastPrice: number | null;
  changePercent: number | null;
  changeBasis: 'moomoo_snapshot_prev_close';
  sessionHigh: number | null;
  sessionLow: number | null;
  volume: number | null;
  turnover: number | null;
  quoteAsOf: string | null;
  unavailableReason: string | null;
}

/** 深度层名单单行（含未上榜候选，名单本身绝不无声截断）。 */
export interface IntradayDeepLaneEntry {
  ticker: string;
  promotedBy: 'plan_always_include' | 'mover_rank';
  moverRank: number | null;
}

/** watchlist 两层模式的诚实 universe 概览；单层（现状）模式恒为 null。 */
export interface IntradayUniverseScan {
  mode: 'watchlist_two_tier';
  gateBasis: 'abs_change_percent_then_turnover_v1';
  watchlistTotal: number;
  watchlistTruncated: boolean;
  scannedTotal: number;
  deepLaneCount: number;
  deepLaneMax: number;
  deepLane: IntradayDeepLaneEntry[];
  planAlwaysInclude: string[];
  gatedOutCount: number;
  snapshotUnresolvedSymbols: string[];
  dayPromotionCap: number;
  dayPromotionCapReached: boolean;
  snapshotOnly: IntradaySnapshotOnlyRow[];
  limitations: string[];
}

/** 日内 Top 候选行：每个指标要么有值+口径，要么显式标缺原因。 */
export interface IntradayTopCandidate {
  ticker: string;
  researchState: IntradayTopResearchState;
  stateReason: string;
  supportingEvidenceCount: number;
  source: string;
  fetchedAt: string | null;
  quoteAsOf: string | null;
  lastPrice: number | null;
  sessionOpen: number | null;
  sessionHigh: number | null;
  sessionLow: number | null;
  sessionChangePercent: number | null;
  sessionChangeBasis: 'moomoo_snapshot_prev_close';
  gapPercent: number | null;
  gapAtrMultiple: number | null;
  gapBasis:
    | 'session_open_vs_prior_completed_close_daily_loader'
    | 'session_open_vs_moomoo_snapshot_prev_close';
  gapUnavailableReason: string | null;
  volumePaceRatio: number | null;
  volumePaceBasis: 'session_cumulative_vs_prior_20_session_full_day_median';
  volumePaceUnavailableReason: string | null;
  vwap: number | null;
  vwapPosition: 'above' | 'below' | 'flat' | 'unknown';
  vwapBasis: 'session_turnover_over_volume';
  vwapUnavailableReason: string | null;
  atr14: number | null;
  atr14LastBarDate: string | null;
  atrRangeExpansion: number | null;
  rangeExpansionUnavailableReason: string | null;
  sessionBursts: IntradaySessionBursts;
  earningsProximity: IntradayEarningsProximity;
  marketAlignment: IntradayMarketAlignment;
  setupMatch: IntradaySetupMatchProfile;
  optionActivity: IntradayTopOptionActivity;
  priorDayContext: IntradayTopPriorDayContext;
  evidence: OpportunityEvidence[];
  message: string;
  limitations: string[];
  /** watchlist 两层模式（additive）：单层模式下缺席/为 null。 */
  scanTier?: 'deep' | null;
  deepLaneReason?: IntradayDeepLaneReason | null;
}

/** 跨标的异动 feed 单行；供应商分类原样透传，不改写为方向结论。 */
export interface IntradayTopRecentOptionEvent {
  ticker: string;
  eventId: string;
  optionCode: string;
  fillTime: string | null;
  tickerType: string | null;
  price: number | null;
  volume: number | null;
  turnover: number | null;
  optionType: string | null;
  strikePrice: number | null;
  expiry: string | null;
  dte: number | null;
  sentiment: string | null;
  orderTypes: string[];
  strategyType: string | null;
}

/** 盘中滚动 Top N：不冻结、不入统计，与盘前冻结榜互不替代。 */
export interface IntradayTopResponse {
  schemaVersion: string;
  runId: string;
  generatedAt: string;
  asOf: string;
  marketDateEt: string;
  sessionState: IntradaySessionState;
  sessionStateBasis: 'america_new_york_clock_v1';
  sessionPhase: IntradaySessionPhase;
  sessionPhaseLabel: string;
  sessionPhaseHintBasis: 'user_trading_history_hardcoded_v1';
  quoteSessionScope: 'current_session' | 'latest_prior_session';
  quoteSessionLabel: string;
  marketContext: IntradayMarketContext;
  signalVersion: 'intraday_session_evidence_v4';
  rankingMethod: 'burst_score_first_then_evidence_count' | 'rule_based_evidence_count';
  statisticsTrack: 'none_intraday_v1_unscored';
  moomooEnabled: boolean;
  universe: string[];
  /** watchlist 两层模式（additive）：单层（现状）模式恒为 null/缺席。 */
  universeScan?: IntradayUniverseScan | null;
  unsupportedSymbols: string[];
  requestedLimit: number;
  candidateCount: number;
  candidates: IntradayTopCandidate[];
  recentOptionEvents: IntradayTopRecentOptionEvent[];
  limitations: string[];
}

/** 市场脉搏单行（SPY/QQQ/VIX）：缺失显式标缺，不以 0 冒充。 */
export interface IntradayPulseItem {
  ticker: string;
  state: 'ready' | 'partial' | 'not_configured' | 'unavailable';
  lastPrice: number | null;
  prevClose: number | null;
  changePercent: number | null;
  changeBasis: 'moomoo_snapshot_prev_close';
  vwap: number | null;
  vwapPosition: 'above' | 'below' | 'flat' | 'unknown';
  vwapBasis: 'session_turnover_over_volume';
  vwapUnavailableReason: string | null;
  quoteAsOf: string | null;
  fetchedAt: string;
  source: string;
  message: string;
  limitations: string[];
}

export interface IntradayPulseResponse {
  schemaVersion: string;
  generatedAt: string;
  marketDateEt: string;
  sessionState: IntradaySessionState;
  sessionStateBasis: 'america_new_york_clock_v1';
  sessionPhase: IntradaySessionPhase;
  sessionPhaseLabel: string;
  sessionPhaseHintBasis: 'user_trading_history_hardcoded_v1';
  items: IntradayPulseItem[];
  limitations: string[];
}
