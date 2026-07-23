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

export interface OpportunityOptionWallLevel {
  rank: number;
  strike: number;
  distanceFromSpotPercent: number;
  metricValue: number;
  shareOfBucketPercent: number;
  unit: 'contracts' | 'usd_delta_change_per_1pct_move';
  method: 'sum_open_interest' | 'sum_session_volume' | 'gross_gamma_concentration_1pct';
}

export interface OpportunityOptionWallItem {
  ticker: string;
  state: OpportunityOptionWallState;
  source: string;
  fetchedAt: string;
  quoteAsOf: string | null;
  formulaVersion: string;
  spot: number | null;
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
  outcomeProgress: OpportunityOutcomeProgress[];
  idempotentReplay: boolean;
}

export interface OpportunitySnapshotListResponse {
  schemaVersion: string;
  items: OpportunitySnapshot[];
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
  insertedOutcomes: number;
  alreadyRecorded: number;
  pendingHorizons: number;
  dataGapHorizons: number;
  outcomeProgress: OpportunityOutcomeProgress[];
  message: string;
}

export interface OpportunityLearningHorizon {
  horizonSessions: 5 | 20;
  matureCount: number;
  distinctSignalSessions: number;
  contextHitCount: number;
  contextMissCount: number;
  neutralCount: number;
  nonDirectionalCount: number;
  contextHitRatePercent: number | null;
  summaryVisible: boolean;
  investigationReady: boolean;
  cohortKey?: string | null;
  cohortLabel?: string | null;
  excludedQualityCount?: number;
}

export interface OpportunityLearningSummaryResponse {
  schemaVersion: string;
  generatedAt: string;
  strategyState: 'collecting' | 'descriptive_summary_available' | 'investigation_ready';
  autoAdjustment: false;
  minimumSummarySamples: number;
  minimumInvestigationSamples: number;
  horizons: OpportunityLearningHorizon[];
  limitations: string[];
}
