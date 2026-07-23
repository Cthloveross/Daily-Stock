// Journal types (Phase 0 Mirror). Mirrors api/v1/schemas/journal.py.

export interface TradeItem {
  id: number;
  portfolioLabel: string;
  isOption: boolean;
  rawSymbol?: string | null;
  underlying: string;
  expiry?: string | null;
  strike?: number | null;
  right?: string | null;
  direction: string;
  status: string;
  quantity: number;
  avgEntryPrice: number;
  avgExitPrice?: number | null;
  entryTime?: string | null;
  exitTime?: string | null;
  holdSeconds?: number | null;
  dteAtEntry?: number | null;
  dteBucket?: string | null;
  pnlGross?: number | null;
  pnlNet?: number | null;
  pnlPct?: number | null;
  totalFee?: number | null;
  tradeStyle?: string | null;
  regimeScoreAtEntry?: number | null;
  wasFakeBreakout?: boolean | null;
  userNotes?: string | null;
  emotionalState?: string | null;
  strategyTagAi?: string | null;
}

export interface TradeListResponse {
  total: number;
  page: number;
  perPage: number;
  items: TradeItem[];
}

export interface RealityTestResponse {
  totalTrades: number;
  totalPnlNet: number;
  topN: number;
  topNPnlNet: number;
  topNIds: number[];
  pnlWithoutTopN: number;
  topNPctOfTotal?: number | null;
  medianPnlNet?: number | null;
}

export interface HealthCheckItem {
  checkDate: string;
  totalOrders: number;
  orders0Dte: number;
  orders13Dte: number;
  ordersOpeningHour: number;
  topUnderlying?: string | null;
  topUnderlyingPct?: number | null;
  warningsJson: unknown[];
  pnlEstimate?: number | null;
  regimeScore?: number | null;
}

export interface JournalStatsResponse {
  windowDays: number;
  closedTradeCount: number;
  totalPnlNet: number;
  winRate?: number | null;
  dteDistribution: Record<string, number>;
  winRateByBucket: Record<
    string,
    { count: number; wins: number; winRate?: number | null; avgPnlNet?: number | null; sumPnlNet: number }
  >;
  realityTest: RealityTestResponse;
}

export interface ImportResponse {
  inserted: number;
  skipped: number;
  tradesRebuilt: number;
  message: string;
}

export interface StatementTimeRange {
  first?: string | null;
  last?: string | null;
}

export interface MoomooStatementPreview {
  parserVersion: string;
  analysisLevel: 'exact' | 'partial' | 'blocked';
  rowsTotal: number;
  ordersTotal: number;
  statusCounts: Record<string, number>;
  filledOrders: number;
  detailBackedFilledOrders: number;
  aggregateOnlyFilledOrders: number;
  inconsistentFilledOrders: number;
  fillRecords: number;
  orphanFillRows: number;
  filledFeeTotal: string;
  detailBackedFeeTotal: string;
  aggregateOnlyFeeTotal: string;
  orderTimeRange: StatementTimeRange;
  fillDetailTimeRange: StatementTimeRange;
  warnings: string[];
  journalDatabaseWritten: boolean;
}

export interface MoomooOpenApiPreview {
  sourceSchema: string;
  parserName: string;
  parserVersion: string;
  sourceSha256: string;
  evidenceSha256: string;
  batchKey: string;
  environment: string;
  market: string;
  analysisLevel: 'exact' | 'blocked';
  analysisReady: boolean;
  reconciliationStatus: string;
  reconciliation: Record<string, number | null>;
  warnings: string[];
  windowStart: string;
  windowEnd: string;
  sourceTimezone: string;
  orderObservations: number;
  fillObservations: number;
  feeObservations: number;
  feeTotalsByCurrency: Record<string, string>;
  journalDatabaseWritten: boolean;
}

export interface OpenApiImportIssue {
  code: string;
  severity: string;
  entityKind: string;
  entityKey: string;
  fieldName?: string | null;
  message: string;
}

export interface MoomooOpenApiImportPlan {
  previewKey: string;
  confirmAllowed: boolean;
  journalDatabaseWritten: false;
  warnings: string[];
  sourceScope: {
    environment: string;
    market: string;
    accountSelection: string;
    windowStart: string;
    windowEnd: string;
    sourceTimezone: string;
    orderObservations: number;
    fillObservations: number;
    feeObservations: number;
    feeTotalsByCurrency: Record<string, string>;
  };
  baseScope?: {
    batchId: number;
    batchKey: string;
    windowStart: string;
    windowEnd: string;
    orderObservations: number;
    fillObservations: number;
    windowOrderObservations: number;
  } | null;
  coverage: {
    scope: string;
    matchedOrders: number;
    csvOnlyInWindow: number;
    apiOnlyOrders: number;
    ambiguousIdentityKeys: number;
    coveredBaseOrders: number;
    baseOrders: number;
    coverageRatio: string;
    outsideUnverifiedOrders: number;
  };
  writePlan: {
    alreadyImported: boolean;
    orderObservations: number;
    fillObservations: number;
    feeObservations: number;
    orderIdentityLinks: number;
    dealIdentityLinks: number;
    fillSetAttestations: number;
    canonicalSets: number;
  };
  canonicalImpact: {
    inputOrderObservations: number;
    inputFillObservations: number;
    canonicalOrders: number;
    canonicalFills: number;
    duplicateOrderObservations: number;
    duplicateFillObservations: number;
    shadowedCsvFills: number;
    shadowedAggregateOrders: number;
    blockingIssues: number;
    analysisReady: boolean;
    canonicalSetSha256: string;
  };
  issues: OpenApiImportIssue[];
}

export interface MoomooOpenApiImportConfirmResponse {
  status: string;
  duplicate: boolean;
  importBatchId: number;
  canonicalSetId: number;
  canonicalSetSha256: string;
  scope: string;
  appended: {
    orders: number;
    fills: number;
    fees: number;
    orderLinks: number;
    dealLinks: number;
    fillSetAttestations: number;
    canonicalSets: number;
  };
  canonical: {
    orders: number;
    fills: number;
    shadowedCsvFills: number;
    shadowedAggregateOrders: number;
    blockingIssues: number;
    analysisReady: boolean;
  };
  legacyJournalWritten: false;
  episodeBuildTriggered: false;
  tradingActionPerformed: false;
  message: string;
}

export interface LedgerImportResponse {
  batchId: number;
  duplicate: boolean;
  analysisLevel: 'exact' | 'partial';
  orderObservations: number;
  fillObservations: number;
  legacyJournalWritten: boolean;
  message: string;
}

export interface LedgerDataHealth {
  hasData: boolean;
  batchId?: number | null;
  analysisLevel?: 'exact' | 'partial' | 'blocked' | null;
  reconciliationStatus?: 'passed' | 'failed' | 'not_run' | string | null;
  reconciliationScope?: 'full_batch' | 'partial_window' | 'not_run' | 'unknown' | string | null;
  reconciliationWindowStart?: string | null;
  reconciliationWindowEnd?: string | null;
  reconciledOrderObservations: number;
  completenessScore?: string | null;
  orderObservations: number;
  fillObservations: number;
  aggregateOnlyFilledOrders: number;
  windowStart?: string | null;
  windowEnd?: string | null;
  recordedAt?: string | null;
  legacyJournalWritten: boolean;
}

// ---------- Journal v2 PositionEpisode ----------

export interface EpisodeBuildMetadata {
  id: number;
  buildKey: string;
  builderName: string;
  builderVersion: string;
  status: string;
  sourceBatchIds: number[];
  sourceKind: string;
  canonicalSetId?: number | null;
  canonicalSetSha256?: string | null;
  sourceCutoffAt: string;
  positionEpisodeCount: number;
  unresolvedEvidenceCount: number;
  completenessScore: string;
  openingBoundaryPolicy: string;
  assumedFlatUnverified: boolean;
  partialReasons: string[];
  recordedAt: string;
}

export interface EpisodeReconciliationSummary {
  status: string;
  scope: string;
  partialWindow: boolean;
  windowStart?: string | null;
  windowEnd?: string | null;
  matchedOrderCount: number;
  totalOrderCount: number;
}

export interface EpisodeHeadlinePnl {
  eligibleClosedCount: number;
  excludedEpisodeCount: number;
  eligibilityRule: string;
  exclusionCounts: Record<string, number>;
  realizedPnlGross?: string | null;
  totalFee?: string | null;
  realizedPnlNet?: string | null;
}

export interface EpisodeConditionalPnl {
  basis: string;
  openingBoundaryPolicy: string;
  count: number;
  realizedPnlGross?: string | null;
  totalFee?: string | null;
  realizedPnlNet?: string | null;
  includedInHeadline: false;
}

export interface PositionEpisodeSummary {
  totalEpisodeCount: number;
  openEpisodeCount: number;
  closedEpisodeCount: number;
  boundaryUnverifiedEpisodeCount: number;
  leftCensoredEpisodeCount: number;
  incompleteEpisodeCount: number;
  aggregateOnlyEpisodeCount: number;
  headlinePnl: EpisodeHeadlinePnl;
  conditionalPnl?: EpisodeConditionalPnl;
}

export interface PositionEpisodeInstrument {
  rawSymbol: string;
  assetType: string;
  underlying: string;
  expiry?: string | null;
  strike?: string | null;
  optionRight?: string | null;
  contractMultiplier?: string | null;
  currency: string;
}

export interface PositionEpisodeQuality {
  completenessStatus: string;
  completenessScore: string;
  constructionBasis: string;
  contractMultiplierBasis: string;
  openingBoundaryPolicy: string;
  assumedFlatUnverified: boolean;
  leftBoundaryVerified: boolean;
  isLeftCensored: boolean;
  isRightCensored: boolean;
  pnlSummaryEligible: boolean;
  pnlExclusionReasons: string[];
}

export interface PositionEpisodeItem {
  id: number;
  episodeBuildId: number;
  strategyEpisodeId: number;
  episodeKey: string;
  lineageKey: string;
  strategyType: string;
  instrument: PositionEpisodeInstrument;
  direction: string;
  lifecycleStatus: 'open' | 'closed' | string;
  openedAt: string;
  closedAt?: string | null;
  holdSeconds?: number | null;
  openedQuantity: string;
  closedQuantity: string;
  remainingQuantity: string;
  averageEntryPrice?: string | null;
  averageExitPrice?: string | null;
  realizedPnlGross?: string | null;
  totalFee?: string | null;
  realizedPnlNet?: string | null;
  quality: PositionEpisodeQuality;
}

export interface PositionEpisodeEvidenceItem {
  id: number;
  evidenceKey: string;
  evidenceKind: 'fill' | 'order' | string;
  eventRole: string;
  allocationSequence: number;
  evidenceTime: string;
  allocatedQuantity?: string | null;
  allocatedFee?: string | null;
  allocatedCashFlow?: string | null;
  allocationRatio?: string | null;
  brokerOrderObservationId?: number | null;
  brokerFillObservationId?: number | null;
  allocation: Record<string, unknown>;
  provenance: Record<string, unknown>;
}

export interface PositionEpisodeListResponse {
  dataState: 'not_built' | 'ready' | string;
  build?: EpisodeBuildMetadata | null;
  reconciliation?: EpisodeReconciliationSummary | null;
  summary?: PositionEpisodeSummary | null;
  total: number;
  page: number;
  perPage: number;
  items: PositionEpisodeItem[];
}

export interface PositionEpisodeDetailResponse {
  dataState: 'ready' | string;
  build: EpisodeBuildMetadata;
  reconciliation: EpisodeReconciliationSummary;
  item: PositionEpisodeItem;
  matching: Record<string, unknown>;
  evidenceSummary: Record<string, unknown>;
  completeness: Record<string, unknown>;
  provenance: Record<string, unknown>;
  evidence: PositionEpisodeEvidenceItem[];
}

export interface PositionEpisodeAiReviewMarketContext {
  benchmark?: string | null;
  windowStart?: string | null;
  windowEnd?: string | null;
  underlyingReturnPct?: number | null;
  benchmarkReturnPct?: number | null;
  relativeReturnPct?: number | null;
  regimeLabel?: string | null;
  provenance: string[];
}

/**
 * User-authored context for one immutable position episode.
 *
 * This is intentionally separate from execution evidence. The web client keeps
 * the draft in localStorage and only attaches non-empty fields to a review
 * request; it never writes these notes to Moomoo or the evidence ledger.
 */
export interface PositionEpisodeAiReviewUserContext {
  setupThesis?: string;
  entryTrigger?: string;
  invalidationPlan?: string;
  positionRationale?: string;
  exitReason?: string;
  postTradeReflection?: string;
}

export interface PositionEpisodeTradeLogicDraft {
  setupThesis: string;
  entryTrigger: string;
  invalidationPlan: string;
  positionRationale: string;
  exitReason: string;
  postTradeReflection: string;
}

export interface PositionEpisodeAiReviewResponse {
  dataState: 'ready' | string;
  analysisMode: 'model_enhanced' | 'deterministic';
  analysisSource: 'configured_llm' | 'local_evidence_engine';
  episodeId: number;
  buildId: number;
  generatedAt: string;
  /** Legacy compatibility output. Prefer the two additive layer fields below. */
  analysisMarkdown: string;
  /** Deterministic facts and explicit unknowns; model output must never replace it. */
  evidenceMarkdown?: string | null;
  /** Optional provider-generated inference layer, present only after successful enhancement. */
  modelAnalysisMarkdown?: string | null;
  marketContext: PositionEpisodeAiReviewMarketContext;
  warnings: string[];
}

export interface EpisodeBuildResponse {
  dataState: 'ready' | string;
  duplicate: boolean;
  build: EpisodeBuildMetadata;
  reconciliation: EpisodeReconciliationSummary;
  summary: PositionEpisodeSummary;
  message: string;
}

export interface CanonicalEpisodeBuildPlanResponse {
  dataState: string;
  canonicalSetId?: number | null;
  canonicalSetSha256?: string | null;
  buildKey?: string | null;
  sourceBatchIds: number[];
  sourceWindowStart?: string | null;
  sourceWindowEnd?: string | null;
  sourceEventCount: number;
  aggregateOrderEventCount: number;
  detailedFillEventCount: number;
  plannedPositionEpisodeCount: number;
  plannedOpenEpisodeCount: number;
  plannedClosedEpisodeCount: number;
  sourceKnownFeeTotal?: string | null;
  allocatedKnownFeeTotal?: string | null;
  feeConserved: boolean;
  openingBoundaryPolicy?: string | null;
  requiresAssumedFlatAcceptance: boolean;
  defaultBuildId?: number | null;
  defaultPositionEpisodeCount: number;
  episodeCountDelta: number;
  defaultWillChange: boolean;
  confirmAllowed: boolean;
  warnings: string[];
}

export interface CanonicalEpisodeBuildConfirmRequest {
  canonicalSetId: number;
  canonicalSetSha256: string;
  buildKey: string;
  acceptAssumedFlat: boolean;
}

export interface PositionEpisodeFilters {
  underlying?: string;
  lifecycleStatus?: 'open' | 'closed' | '';
  completenessStatus?: 'exact' | 'complete' | 'partial' | '';
  caseFocus?: 'top_profit' | 'top_loss' | 'largest_fee' | 'longest_hold' | '';
  buildId?: number;
  page?: number;
  perPage?: number;
}

export interface TradeListFilters {
  symbol?: string;
  start?: string;
  end?: string;
  status?: string;
  style?: string;
  page?: number;
  perPage?: number;
}

export interface TradeUpdateRequest {
  userNotes?: string | null;
  emotionalState?: string | null;
  tradeStyle?: string | null;
}

// ---------- stats-by-style + journal QA ----------

export interface StyleBucketStat {
  style: string;
  count: number;
  winRate: number;
  avgPnlNet: number;
  sumPnlNet: number;
  medianHoldSeconds?: number | null;
  avgPnlPct?: number | null;
}

export interface DteBucketStat {
  bucket: string;
  count: number;
  winRate: number;
  avgPnlNet: number;
  sumPnlNet: number;
}

export interface CompactTradeItem {
  id?: number | null;
  underlying?: string | null;
  direction?: string | null;
  isOption?: boolean | null;
  dteBucket?: string | null;
  tradeStyle?: string | null;
  pnlNet?: number | null;
  pnlPct?: number | null;
  holdSeconds?: number | null;
  entryTime?: string | null;
  exitTime?: string | null;
}

export interface JournalStatsByStyleResponse {
  period: { start: string | null; end: string | null };
  totalCount: number;
  totalPnlNet: number;
  byStyle: StyleBucketStat[];
  byDte: DteBucketStat[];
  worstTrades: CompactTradeItem[];
  bestTrades: CompactTradeItem[];
}

export interface JournalQaRequest {
  framework: string;
  question: string;
  tradeWindowDays?: number;
  tradeLimit?: number;
}

export interface JournalQaResponse {
  answer: string;
  tradesConsidered: number;
  frameworkHash: string;
  generatedAt: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  ts: string; // ISO 8601
}
