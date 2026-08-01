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
