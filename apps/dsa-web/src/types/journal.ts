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
  ordinaryOrderObservations: number;
  unclassifiedParentObservations: number;
  fillObservations: number;
  feeObservations: number;
  contractSpecObservations: number;
  executionGroupObservations: number;
  executionGroupLegObservations: number;
  executionGroupFillLinks: number;
  executionGroupFeeObservations: number;
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
    accountBound: boolean;
    windowStart: string;
    windowEnd: string;
    sourceTimezone: string;
    orderObservations: number;
    ordinaryOrderObservations: number;
    unclassifiedParentObservations: number;
    fillObservations: number;
    feeObservations: number;
    contractSpecObservations: number;
    executionGroupObservations: number;
    executionGroupLegObservations: number;
    executionGroupFillLinks: number;
    executionGroupFeeObservations: number;
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
    /** API-only orders inside the historical overlap; these require reconciliation. */
    overlapApiOnlyOrders: number;
    /** API-only orders after the prior evidence cutoff; these are expected new tail facts. */
    incrementalApiOnlyOrders: number;
    ambiguousIdentityKeys: number;
    coveredBaseOrders: number;
    baseOrders: number;
    coverageRatio: string;
    outsideUnverifiedOrders: number;
  };
  writePlan: {
    alreadyImported: boolean;
    orderObservations: number;
    ordinaryOrderObservations: number;
    unclassifiedParentObservations: number;
    fillObservations: number;
    feeObservations: number;
    executionGroupObservations: number;
    executionGroupLegObservations: number;
    executionGroupFillLinks: number;
    executionGroupFeeObservations: number;
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
    inputExecutionGroupObservations: number;
    canonicalExecutionGroups: number;
    canonicalExecutionGroupLegs: number;
    duplicateExecutionGroupObservations: number;
  };
  issues: OpenApiImportIssue[];
  scopeIsFullBatch: boolean;
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
    ordinaryOrders: number;
    fills: number;
    fees: number;
    executionGroups: number;
    executionGroupLegs: number;
    executionGroupFillLinks: number;
    executionGroupFees: number;
    orderLinks: number;
    dealLinks: number;
    fillSetAttestations: number;
    canonicalSets: number;
  };
  canonical: {
    orders: number;
    fills: number;
    executionGroups: number;
    executionGroupLegs: number;
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

export type JournalRefreshFreshnessState =
  | 'never_synced'
  | 'stale'
  | 'evidence_blocked'
  | 'evidence_current'
  | 'build_ready'
  | 'current';

export type JournalRefreshPendingStage =
  | 'refresh'
  | 'confirm'
  | 'build'
  | 'activate'
  | 'none';

export interface JournalRefreshStatus {
  refreshEnabled: boolean;
  refreshConfigured: boolean;
  freshnessState: JournalRefreshFreshnessState;
  pendingStage: JournalRefreshPendingStage;
  expectedCompleteThrough: string;
  brokerQueriedThrough?: string | null;
  latestFillAt?: string | null;
  evidencePublishedThrough?: string | null;
  publicationRecordedAt?: string | null;
  latestArtifactId?: number | null;
  latestArtifactConfirmAllowed?: boolean | null;
  latestArtifactExpiresAt?: string | null;
  latestCanonicalSetId?: number | null;
  latestCanonicalSetSha256?: string | null;
  latestCanonicalSourceThrough?: string | null;
  latestCanonicalBuildId?: number | null;
  latestCanonicalBuildKey?: string | null;
  latestCanonicalBuildSourceThrough?: string | null;
  activeSelectionSource: 'activation' | 'csv_fallback' | 'none';
  activeActivationId?: number | null;
  activeBuildId?: number | null;
  activeBuildKey?: string | null;
  activeCanonicalSetId?: number | null;
  activeSourceThrough?: string | null;
  accountBound: boolean;
}

export interface MoomooJournalRefreshSource {
  retrievalComplete: boolean;
  coverageComplete: boolean;
  hasActivity: boolean;
  brokerQueriedThrough: string;
  latestFillAt?: string | null;
  orderObservations: number;
  ordinaryOrderObservations: number;
  unclassifiedParentObservations: number;
  fillObservations: number;
  feeObservations: number;
  contractSpecObservations: number;
  executionGroupObservations: number;
  executionGroupLegObservations: number;
  executionGroupFillLinks: number;
  executionGroupFeeObservations: number;
}

export interface MoomooJournalRefreshPreview {
  artifactId: number;
  artifactKey: string;
  expiresAt: string;
  source: MoomooJournalRefreshSource;
  plan: MoomooOpenApiImportPlan;
  evidenceWritten: false;
  tradingActionPerformed: false;
}

export interface MoomooJournalRefreshPublication {
  publicationId: number;
  brokerQueriedThrough: string;
  latestFillAt?: string | null;
  evidencePublishedThrough: string;
  recordedAt: string;
}

export interface MoomooJournalRefreshConfirmResponse {
  artifactId: number;
  publication: MoomooJournalRefreshPublication;
  imported: MoomooOpenApiImportConfirmResponse;
  tradingActionPerformed: false;
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
  sourceWindowStart: string;
  sourceCutoffAt: string;
  positionEpisodeCount: number;
  unresolvedEvidenceCount: number;
  completenessScore: string;
  openingBoundaryPolicy: string;
  assumedFlatUnverified: boolean;
  executionGroupCount: number;
  groupFeeAffectedEpisodeCount: number;
  retainedExecutionGroupFeeTotal: string;
  feeConservationByCurrency: Record<string, Record<string, string>>;
  legFeeAttributionComplete: boolean;
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
  groupFeeAffectedEpisodeCount: number;
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
  groupFeeUnallocated: boolean;
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
  /** Latest user-authored review state for this immutable episode/build pair. */
  reviewStatus?: PositionEpisodeReviewStatus;
  reviewRevision?: number | null;
  reviewUpdatedAt?: string | null;
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
  /** 成交证据的父订单 id（后端联表补出；order XOR fill 约束下 fill 行自身不携带订单 id）。 */
  parentBrokerOrderObservationId?: number | null;
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
  reviewQueue?: PositionEpisodeReviewQueue;
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

export type PositionEpisodeReviewStatus = 'not_started' | 'in_progress' | 'completed';

export interface PositionEpisodeReviewQueue {
  pending: number;
  inProgress: number;
  completed: number;
  total: number;
}

export interface PositionEpisodeReviewWorkspaceDraft extends PositionEpisodeTradeLogicDraft {
  tags: string[];
  errorTypes: string[];
}

export interface PositionEpisodeReviewAnnotation {
  id: number;
  episodeBuildId: number;
  positionEpisodeId: number;
  revision: number;
  reviewStatus: PositionEpisodeReviewStatus;
  setupThesis: string;
  entryTrigger: string;
  invalidationPlan: string;
  positionRationale: string;
  exitReason: string;
  postTradeReflection: string;
  tags: string[];
  errorTypes: string[];
  contentSha256: string;
  previousAnnotationId?: number | null;
  createdAt: string;
}

export interface PositionEpisodeReviewAnnotationLatestResponse {
  dataState: 'not_started' | 'ready' | string;
  annotation?: PositionEpisodeReviewAnnotation | null;
}

export interface PositionEpisodeReviewAnnotationHistoryResponse {
  dataState: 'not_started' | 'ready' | string;
  total?: number;
  items: PositionEpisodeReviewAnnotation[];
}

export interface SavePositionEpisodeReviewAnnotationRequest
  extends PositionEpisodeReviewWorkspaceDraft {
  buildId: number;
  reviewStatus: Exclude<PositionEpisodeReviewStatus, 'not_started'>;
}

export interface SavePositionEpisodeReviewAnnotationResponse {
  dataState: 'ready' | string;
  created: boolean;
  idempotentReplay: boolean;
  annotation: PositionEpisodeReviewAnnotation;
}

/** Slice C-1: zero-write pattern observation over the default build. */
export interface ReviewInsightStats {
  winRate: string;
  avgPnl: string;
  sumPnl: string;
  winCount: number;
  lossCount: number;
  breakevenCount: number;
}

export interface ReviewInsightStatsGate {
  eligible: boolean;
  reason?: 'below_sample_threshold' | 'no_verified_pnl_episodes' | null;
}

export interface ReviewInsightBucket {
  groupKind: 'tag' | 'error_type';
  groupValue: string;
  direction: string;
  boundaryPolicy: 'verified' | 'assumed_or_censored';
  episodeCount: number;
  distinctTradingDayCount: number;
  reviewCompletedCount: number;
  verifiedEpisodeCount: number;
  verifiedDistinctTradingDayCount: number;
  conditionalEpisodeCount: number;
  stats?: ReviewInsightStats | null;
  statsGate: ReviewInsightStatsGate;
}

export interface ReviewInsightsResponse {
  schemaVersion: 'journal-review-insights/1.0' | string;
  dataState: 'not_built' | 'ready' | string;
  buildId?: number | null;
  buildKey?: string | null;
  sourceKind?: string | null;
  accountKey: string;
  generatedAt?: string | null;
  thresholds: {
    minEpisodeCount: number;
    minDistinctTradingDayCount: number;
  };
  totalEpisodeCount: number;
  annotatedEpisodeCount: number;
  unreviewed?: {
    episodeCount: number;
    distinctTradingDayCount: number;
  } | null;
  buckets: ReviewInsightBucket[];
}

/** Slice C-2: append-only playbook candidates (L2) and rule versions (L3). */
export interface PlaybookSourceBucket {
  groupKind: 'tag' | 'error_type';
  groupValue: string;
  direction: string;
  boundaryPolicy: 'verified' | 'assumed_or_censored';
}

export interface PlaybookCandidate {
  schemaVersion: 'playbook-candidate/1.0' | string;
  id: number;
  candidateKey: string;
  accountKey: string;
  title: string;
  ruleText: string;
  sourceBucket?: PlaybookSourceBucket | null;
  evidenceSnapshot: Record<string, unknown>;
  evidenceSnapshotSha256: string;
  promoted: boolean;
  createdAt: string;
}

export interface PlaybookRule {
  schemaVersion: 'playbook-rule/1.0' | string;
  id: number;
  ruleKey: string;
  lineageKey: string;
  accountKey: string;
  version: number;
  status: 'active' | 'retired';
  promotedFromCandidateId: number;
  promotedFromCandidateKey: string;
  previousRuleId?: number | null;
  title: string;
  ruleText: string;
  evidenceSnapshot: Record<string, unknown>;
  evidenceSnapshotSha256: string;
  isLatestVersion: boolean;
  createdAt: string;
}

export interface PlaybookListResponse {
  schemaVersion: 'journal-playbook/1.0' | string;
  accountKey: string;
  candidates: PlaybookCandidate[];
  rules: PlaybookRule[];
}

export interface CreatePlaybookCandidateRequest {
  title: string;
  ruleText: string;
  sourceBucket?: PlaybookSourceBucket | null;
}

export interface CreatePlaybookCandidateResponse {
  dataState: 'ready' | string;
  created: boolean;
  idempotentReplay: boolean;
  candidate: PlaybookCandidate;
}

export interface PromotePlaybookCandidateRequest {
  allowNewVersion?: boolean;
  expectedCurrentVersion?: number | null;
}

export interface PromotePlaybookCandidateResponse {
  dataState: 'ready' | string;
  created: boolean;
  idempotentReplay: boolean;
  rule: PlaybookRule;
}

export interface RetirePlaybookRuleResponse {
  dataState: 'ready' | string;
  retired: boolean;
  idempotentReplay: boolean;
  rule: PlaybookRule;
}

/** Slice C-3: zero-write reverse links from one episode to frozen snapshots. */
export interface EpisodePlaybookLink {
  schemaVersion: 'playbook-episode-link/1.0' | string;
  kind: 'rule' | 'candidate';
  linkState: 'confirmed' | 'possible_truncated';
  title: string;
  ruleText: string;
  bucket?: PlaybookSourceBucket | null;
  snapshotBuildId: number;
  snapshotGeneratedAt?: string | null;
  lineageKey?: string | null;
  version?: number | null;
  status?: 'active' | 'retired' | null;
  candidateKey?: string | null;
  promoted?: boolean | null;
  createdAt: string;
}

export interface EpisodePlaybookLinksResponse {
  schemaVersion: 'journal-playbook-episode-links/1.0' | string;
  accountKey: string;
  buildId: number;
  episodeId: number;
  links: EpisodePlaybookLink[];
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
  retainedExecutionGroupFeeTotal?: string | null;
  feeConservationByCurrency: Record<string, Record<string, string>>;
  feeConserved: boolean;
  executionGroupCount: number;
  groupFeeAffectedEpisodeCount: number;
  legFeeAttributionComplete: boolean;
  openingBoundaryPolicy?: string | null;
  requiresAssumedFlatAcceptance: boolean;
  requiresGroupFeeScopeAcceptance: boolean;
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
  acceptGroupFeeScope: boolean;
}

export type EpisodeBuildSelectionSource = 'activation' | 'csv_fallback' | 'none';

/** Append-only pointer used by default position-review reads. */
export interface EpisodeBuildActivationState {
  accountKey: string;
  selectionSource: EpisodeBuildSelectionSource;
  currentActivationId?: number | null;
  currentActivationSequence?: number | null;
  currentBuildId?: number | null;
  currentBuildKey?: string | null;
  canonicalSetId?: number | null;
  canonicalSetSha256?: string | null;
  previousActivationId?: number | null;
  previousBuildId?: number | null;
  activatedAt?: string | null;
}

export interface EpisodeBuildActivationRequest {
  expectedBuildKey: string;
  expectedCurrentActivationId?: number | null;
  expectedCurrentBuildId?: number | null;
  acceptAssumedFlat: boolean;
  acceptGroupFeeScope: boolean;
  /** Required whenever the target build has left-censored openings (snapshot-fence future builds). */
  acceptLeftCensoredOpenings: boolean;
}

export interface EpisodeBuildActivationResponse {
  activationId: number;
  activationKey: string;
  duplicate: boolean;
  state: EpisodeBuildActivationState;
  message: string;
  tradingActionPerformed: false;
}

export interface PositionEpisodeFilters {
  underlying?: string;
  lifecycleStatus?: 'open' | 'closed' | '';
  completenessStatus?: 'exact' | 'complete' | 'partial' | '';
  caseFocus?: 'top_profit' | 'top_loss' | 'largest_fee' | 'longest_hold' | 'weakest_evidence' | '';
  reviewStatus?: PositionEpisodeReviewStatus | '';
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

// --- personal edge (个人画像回灌): zero-write descriptive stats --------------

export interface PersonalEdgeUnderlyingStat {
  underlying: string;
  n: number;
  net: number;
  winRate: number;
  fees: number;
}

export interface PersonalEdgeHoldBucket {
  bucket: string;
  n: number;
  net: number;
  winRate: number | null;
  avgWin: number | null;
  avgLoss: number | null;
}

export interface PersonalEdgeDteBucket {
  bucket: string;
  n: number;
  net: number;
  winRate: number | null;
}

export interface PersonalEdgeMonthlyBucket {
  month: string;
  n: number;
  net: number;
  fees: number;
  winRate: number | null;
}

/**
 * 规模与频率纪律读数：每美元回报（真账）+ 仓位 + 频率 + 本体/尾部拆解 +
 * 口径来源。任何比率分母不足即为 null 并附 `*Reason`，绝不以 0 冒充。
 *
 * 口径可比性以 `fillDetailedShare` 为准（回合是否由明细成交构建），
 * `exactFillShare`（`has_exact_fill_times` 均值）仅作历史连续性保留，两者会不
 * 一致。`basisBreak === true` 的区间风险金额分母被低估，**不可**与全明细区间
 * 连成一条趋势线——消费端必须断开显示并说明原因。
 */
export interface PersonalEdgeDisciplineStats {
  n: number;
  tradingDayCount: number;
  tradesPerDay: number | null;
  tradesPerDayReason: string | null;
  premiumKnownCount: number;
  premiumMissingCount: number;
  medianPremiumAtRisk: number | null;
  totalPremiumAtRisk: number | null;
  premiumReason: string | null;
  netPnl: number;
  pnlPerDollarRisked: number | null;
  pnlPerDollarRiskedReason: string | null;
  medianEpisodePnl: number | null;
  bodyPnl: number | null;
  bodyEpisodeCount: number | null;
  bodyPnlReason: string | null;
  dteKnownCount: number;
  zeroDteShare: number | null;
  zeroDteReason: string | null;
  exactFillShare: number | null;
  hasReconstructedFills: boolean;
  /** additive（2026-08-04）：口径来源判据 —— 缺席时按「未知」处理，不得当作全明细。 */
  fillDetailedCount?: number;
  aggregateOnlyCount?: number;
  fillProvenanceUnknownCount?: number;
  fillDetailedShare?: number | null;
  basisBreak?: boolean;
  basisBreakReason?: string | null;
  /** additive（2026-08-04）：恒定过路费与毛口径（毛 = 净 + 费用）。 */
  feesTotal?: number | null;
  feesMissingCount?: number;
  feePctOfPremiumAtRisk?: number | null;
  feePctOfPremiumAtRiskReason?: string | null;
  grossPctOfPremiumAtRisk?: number | null;
  grossPctOfPremiumAtRiskReason?: string | null;
  /** additive（2026-08-04）：剔除最好 N 笔后的每美元回报（bodyPnl 的每美元版本）。 */
  pnlPerDollarExcludingTopN?: number | null;
  grossPctExcludingTopN?: number | null;
  excludingTopNCount?: number | null;
  excludingTopNReason?: string | null;
}

export interface PersonalEdgeDisciplineMonth extends PersonalEdgeDisciplineStats {
  month: string;
}

export interface PersonalEdgeDisciplineWindow extends PersonalEdgeDisciplineStats {
  requestedTradingDays: number;
  startDate: string | null;
  endDate: string | null;
}

export interface PersonalEdgeDiscipline {
  monthly: PersonalEdgeDisciplineMonth[];
  currentWindow: PersonalEdgeDisciplineWindow;
  bodyTrimCount: number;
  bodyMinEpisodeCount: number;
  /** additive（2026-08-04）：剔除最好 N 笔的 N，以及「哪个口径字段说了算」的原文。 */
  excludeTopN?: number;
  fillDetailedGoverns?: string | null;
}

// --- rule compliance (车道遵守度): 规则 v2 的前向证伪记账 ---------------------

export type RuleComplianceVerdict = 'compliant' | 'violation' | 'uncovered' | 'unknown';

export type RuleComplianceLane =
  | 'intraday_0dte'
  | 'overnight_4_7'
  | 'dte_1_3'
  | 'bought_time_unused'
  | 'late_0dte'
  | 'other'
  | 'unknown';

/**
 * 一个车道桶（或合规/违规汇总）的口径读数。
 * `gross` = Σ(净盈亏 + 费用)，`risk` = Σ|开仓现金流|——与规则文本引用的定义一致。
 * 任何分母不足的比率为 `null` 并附原因，绝不以 0 冒充。
 */
export interface RuleComplianceStat {
  key: string;
  n: number;
  risk: number | null;
  net: number | null;
  gross: number | null;
  grossPct: number | null;
  tollPct: number | null;
  winRate: number | null;
  grossPctExcludingTopN: number | null;
  excludingTopNCount: number | null;
  excludingTopNReason: string | null;
  ratioReason: string | null;
}

/**
 * 判定与规则编号随每一条车道行下发（后端不发以车道 id 为键的字典：本层的深层
 * camelCase 会改写字典键，规则 id 必须只以「值」的形式过网）。
 */
export interface RuleComplianceLaneStat extends RuleComplianceStat {
  verdict: RuleComplianceVerdict;
  ruleId: string;
}

/** `no_episodes_since_adoption` ＝采纳后尚无样本：必须显式说明，不得渲染成全 0 的表。 */
export interface RuleComplianceSlice {
  state: 'ready' | 'no_episodes' | 'no_episodes_since_adoption';
  stateReason: string | null;
  startDate: string | null;
  n: number;
  lanes: RuleComplianceLaneStat[];
  verdicts: RuleComplianceStat[];
}

export interface PersonalEdgeRuleCompliance {
  ruleSetId: string;
  adoptedAt: string;
  cleanBasisStart: string;
  cleanBasisReason: string;
  populationN: number;
  excludedBeforeCleanBasisCount: number;
  excludedAggregateOrUnknownBasisCount: number;
  excludedMissingPremiumCount: number;
  excludeTopN: number;
  excludeTopNMinEpisodeCount: number;
  intradayLaneDte: number;
  intradayLaneEtCutoffHour: number;
  overnightLaneMinDte: number;
  overnightLaneMaxDte: number;
  overnightLaneWeakEntryEtHours: number[];
  allHistory: RuleComplianceSlice;
  sinceAdoption: RuleComplianceSlice;
  // dailyBudget（V2-D 额度读数）已于 2026-08 移除：Journal 永远不含「今天」，
  // 该读数恒为过期；当日额度由 useIntradayManualBudgetStore 手动计数承载。
  limitations: string[];
}

/**
 * 个人画像回灌：当前默认 build 已平仓回合的描述统计（build_id + 日期范围 +
 * computed_at 全程可见）。描述不是因果——内生性 caveat 在 limitations 原文携带。
 */
export interface PersonalEdgeResponse {
  schemaVersion: string;
  dataState: 'ready' | 'not_built';
  accountKey: string;
  buildId: number | null;
  buildKey: string | null;
  sourceKind: string | null;
  computedAt: string | null;
  firstOpenedAt: string | null;
  lastClosedAt: string | null;
  closedEpisodeCount: number;
  excludedOpenCount: number;
  excludedMissingPnlCount: number;
  underlyingMinEpisodeCount: number;
  underlyings: PersonalEdgeUnderlyingStat[];
  smallSampleUnderlyingCount: number;
  holdTimeBuckets: PersonalEdgeHoldBucket[];
  holdUnknownCount: number;
  dteBuckets: PersonalEdgeDteBucket[];
  dteUnknown: PersonalEdgeDteBucket | null;
  monthly: PersonalEdgeMonthlyBucket[];
  /** additive（2026-08-04）：规模与频率纪律；not_built 或旧后端响应时缺席。 */
  discipline?: PersonalEdgeDiscipline | null;
  /** additive（2026-08-04）：车道遵守度；not_built 或旧后端响应时缺席。 */
  ruleCompliance?: PersonalEdgeRuleCompliance | null;
  monthBasis: string | null;
  limitations: string[];
}

/* ------------------------------------------------------------------ */
/* 交易纪律证据页（/rules）                                            */
/*                                                                     */
/* 所有数字都由后端算完下发：分档边界、剔尾 N、严重度分位数定义、费用   */
/* 计算器的默认锚点全部来自响应。**前端不得硬编码任何一个统计量**。     */
/* ------------------------------------------------------------------ */

/** 一行读数：样本量 + 三个口径 + 显式缺席原因（缺席绝不以 0 冒充）。 */
export interface RulesEvidenceCell {
  label: string;
  n: number;
  grossPct: number | null;
  netPct: number | null;
  feePct: number | null;
  winRatePct: number | null;
  reason: string | null;
}

export interface RulesEvidencePriceBand extends RulesEvidenceCell {
  lower: number | null;
  upper: number | null;
}

export interface RulesEvidenceDteHold extends RulesEvidenceCell {
  dteMin: number;
  dteMax: number;
  holdStyle: 'intraday' | 'overnight';
  excludedTopN: number;
  exTopNGrossPct: number | null;
  exTopNN: number;
  exTopNReason: string | null;
}

export interface RulesEvidenceHour extends RulesEvidenceCell {
  etHour: number;
}

export interface RulesEvidenceWeekday extends RulesEvidenceCell {
  weekday: number;
  zeroDteN: number;
  dte13N: number;
}

export interface RulesEvidenceFeeThreshold {
  feePctOfPremium: number | null;
  n: number;
  reason: string | null;
  defaultTicketUsd: number;
  defaultTicketsPerDay: number;
  tradingDaysPerMonth: number;
  note: string;
}

export interface RulesEvidenceSeverityTier {
  label: string;
  definition: string;
  lossPct: number | null;
  ticketsToBreaker: number | null;
  reason: string | null;
}

export interface RulesEvidenceChosenParams {
  ticketUsd: number;
  dailyBreakerUsd: number;
  maxConcurrent: number;
}

export interface RulesEvidencePosition {
  framing: string;
  params: RulesEvidenceChosenParams;
  severityTiers: RulesEvidenceSeverityTier[];
  observedBreachDayCount: number;
  observedTradingDayCount: number;
  observedBreachOnePerDays: number | null;
  observedMedianTicketsPerDay: number | null;
  observedCadenceCaveat: string;
  historicalMaxDrawdownPct: number | null;
  historicalDrawdownSizingPct: number;
  drawdownCaveat: string;
  reason: string | null;
}

export interface RulesEvidenceCorrelation {
  tickers: { ticker: string; n: number }[];
  compliantEpisodeCount: number;
  maxConcurrent: number;
  note: string;
}

export interface RulesEvidenceResponse {
  schemaVersion: string;
  dataState: 'ready' | 'not_built';
  accountKey: string;
  buildId: number | null;
  buildKey: string | null;
  sourceKind: string | null;
  computedAt: string | null;
  cleanBasisStart: string | null;
  cleanBasisReason: string | null;
  ruleSetAdoptedAt: string | null;
  sampleEpisodeCount: number;
  excludedBeforeCleanBasis: number;
  excludedAggregateOrUnknownBasis: number;
  excludedMissingPremium: number;
  excludedNotClosedOrMissingPnl: number;
  /**
   * additive：样本内缺 total_fee 的回合数——仍进净口径/胜率，但从毛口径与
   * 费率的分子分母中排除（0 回填会把毛口径冒充成净口径）。旧载荷可省略。
   */
  feeUnknownCount?: number;
  firstTradingDay: string | null;
  lastTradingDay: string | null;
  banner: string | null;
  priceBandHeadline: string | null;
  priceBandBoundaryPolicy: string | null;
  priceBands: RulesEvidencePriceBand[];
  holdStyleBasis: string | null;
  dteHoldLanes: RulesEvidenceDteHold[];
  etHours: RulesEvidenceHour[];
  weekdayHeadline: string | null;
  weekdays: RulesEvidenceWeekday[];
  feeThreshold: RulesEvidenceFeeThreshold | null;
  position: RulesEvidencePosition | null;
  correlation: RulesEvidenceCorrelation | null;
  overnightGapNote: string | null;
  limitations: string[];
}

// ---------------------------------------------------------------------------
// Edge 面板（Phase 1 E-5 · New-docs/phase1/16_EDGE_FORENSICS_AND_REGIME_GATE.md）
//
// 注意：该端点的形状是文档 16 固定契约，前后端都逐字对齐它——字段保持
// snake_case 原样，不过 toCamelCase（camelcase-keys 会把 "0-1"/"2-7" 这类
// 分桶键与 allow_0_1dte 改写成无法回读的键名）。
// ---------------------------------------------------------------------------

export type EdgePanelBlockedReason =
  | 'market_momentum'
  | 'sector_momentum'
  | 'volatility_scale'
  | 'market_data_unavailable';

export interface EdgePanelGateFeatures {
  mom_q_20d: number | null;
  mom_s_20d: number | null;
  mm_scale: number | null;
}

export interface EdgePanelGate {
  allow_0_1dte: boolean;
  default_bucket: '2-7';
  blocked_by: EdgePanelBlockedReason[];
  features: EdgePanelGateFeatures;
  data_status: 'ok' | 'unavailable';
}

export interface EdgePanelBucketStat {
  n: number;
  total: number;
  mean: number | null;
  win_rate: number | null;
}

export interface EdgePanelExpectancy {
  n: number;
  mean: number | null;
  ci95_low: number | null;
  ci95_high: number | null;
}

export interface EdgePanelEvidence {
  h1_t_stat: number | null;
  hlz_hurdle: number;
  status: 'insufficient' | 'significant';
  n_needed: number | null;
  n_remaining: number | null;
}

export interface EdgePanelEdge {
  buckets: Record<string, EdgePanelBucketStat>;
  expectancy: EdgePanelExpectancy;
  evidence: EdgePanelEvidence;
}

export interface EdgePanelDiscipline {
  trades_today: number;
  daily_cap: number;
  month_fees: number;
  month_gross: number;
  fee_ratio: number | null;
}

export interface EdgePanelResponse {
  as_of: string;
  gate: EdgePanelGate;
  edge: EdgePanelEdge;
  discipline: EdgePanelDiscipline;
}
