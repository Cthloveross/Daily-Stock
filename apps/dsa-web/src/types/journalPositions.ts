/**
 * Current option-position evidence is intentionally isolated from the
 * historical execution ledger. A confirmed snapshot is useful only as a
 * forward continuity anchor; it never proves a historical opening balance.
 */

import type {
  EpisodeBuildMetadata,
  EpisodeReconciliationSummary,
  PositionEpisodeSummary,
} from './journal';

export type PositionSnapshotContentState =
  | 'positions'
  | 'empty'
  | 'incomplete'
  | 'failed';

export interface PositionSnapshotBrokerCostContext {
  costPrice?: string | null;
  costPriceValid?: boolean | null;
  averageCost?: string | null;
  dilutedCost?: string | null;
  semantics?: string | null;
}

export interface CurrentOptionPosition {
  symbol: string;
  underlying: string;
  expiry: string;
  strike: string;
  optionRight: string;
  positionSide: string;
  quantityContracts: string;
  signedQuantityContracts: string;
  canSellQuantityContracts?: string | null;
  currency: string;
  contractMultiplier: string | null;
  basis: string;
  brokerCostContext: PositionSnapshotBrokerCostContext;
}

export interface PositionSnapshotFilterCounts {
  firstTotalRows: number;
  secondTotalRows: number;
  filteredNonUsRows: number;
  filteredNonOptionRows: number;
  filteredZeroQuantityRows: number;
  contractSpecCount: number;
  validationIssueCount: number;
}

export interface PositionSnapshotStability {
  stable: boolean;
  comparisonBasis: string;
  addedSymbols: string[];
  removedSymbols: string[];
  changedSymbols: string[];
}

export interface PositionSnapshotObservation {
  observedFrom: string;
  observedThrough: string;
  operationCompletedAt: string;
  brokerAsOf: string | null;
  stable: boolean;
  stability: PositionSnapshotStability;
  retrievalComplete: boolean;
  positionSnapshotComplete: boolean;
  contractSpecStatus: string;
  analysisReady: boolean;
  contentState: PositionSnapshotContentState;
  positionCount: number;
  totalContracts: string;
  futureAnchorCandidate: boolean;
  historicalOpeningProven: false;
  filterCounts: PositionSnapshotFilterCounts;
  positions: CurrentOptionPosition[];
  hashes: Record<string, string>;
}

export type PositionSnapshotFreshnessStatus = 'fresh' | 'stale' | 'unknown';

export interface PositionSnapshotFreshness {
  status: PositionSnapshotFreshnessStatus;
  ageSeconds: number | null;
  futureSkewSeconds: number | null;
  thresholdSeconds: number;
  evaluatedAt: string;
  ageBasis: string;
  timeSemantics: string;
}

export type PositionSnapshotAccountBindingStatus =
  | 'matches_latest_publication'
  | 'mismatch'
  | 'publication_unavailable';

export type PositionSnapshotPublicationAnchorStatus =
  | 'latest'
  | 'superseded'
  | 'publication_unavailable';

export interface ConfirmedPositionSnapshot {
  id: number;
  key: string;
  recordedAt: string;
  acknowledgedFutureOnly: true;
  freshness: PositionSnapshotFreshness;
  accountBindingStatus: PositionSnapshotAccountBindingStatus;
  publicationAnchorStatus: PositionSnapshotPublicationAnchorStatus;
  isCurrent: boolean;
  observation: PositionSnapshotObservation;
}

export interface LatestPositionSnapshotResponse {
  enabled: boolean;
  configured: boolean;
  continuityReady: boolean;
  continuityReason?: string | null;
  latestIsCurrent: boolean;
  latestAccountBindingMatches: boolean | null;
  latestPublicationAnchorMatches: boolean | null;
  latest: ConfirmedPositionSnapshot | null;
  tradingActionPerformed: false;
}

export interface PositionSnapshotPreviewResponse {
  artifactId: number;
  artifactKey: string;
  previewKey: string;
  expiresAt: string;
  confirmAllowed: boolean;
  blockingReasons: string[];
  warnings: string[];
  observation: PositionSnapshotObservation;
  evidenceWritten: false;
  tradingActionPerformed: false;
}

export interface PositionSnapshotConfirmResponse {
  snapshot: ConfirmedPositionSnapshot;
  duplicate: boolean;
  tradingActionPerformed: false;
}

/**
 * A read-only proof that a confirmed current-position observation can be used
 * as the left edge of a future Episode build.  `ready` does not mean a build
 * was generated or activated; it only means the continuity gate was proved.
 */
export type PositionSnapshotContinuityStatus =
  | 'no_snapshot'
  | 'awaiting_refresh'
  | 'blocked'
  | 'ready';

export interface PositionSnapshotContinuityCounts {
  snapshotMemberCount: number;
  chainPublicationCount: number;
  chainBatchCount: number;
  targetOrderCount: number;
  targetFillCount: number;
  guardFillCount: number;
  preBoundaryFillCount: number;
  postBoundaryFillCount: number;
  aggregateChangedPreBoundaryCount: number;
  straddlingOrderCount: number;
  straddlingGroupCount: number;
  unbackedPostBoundaryCount: number;
  optionIdentityMismatchCount: number;
}

export interface PositionSnapshotContinuityReason {
  code: string;
  message: string;
  entityKind: string | null;
  entityIds: number[];
  count: number;
}

export interface PositionSnapshotContinuityAssessment {
  policyVersion: string;
  status: PositionSnapshotContinuityStatus;
  readyForEpisodeBuild: boolean;
  fenceKey: string | null;
  accountKey: string;
  snapshotId: number | null;
  snapshotKey: string | null;
  anchorPublicationId: number | null;
  anchorPublicationKey: string | null;
  targetPublicationId: number | null;
  targetPublicationKey: string | null;
  targetCanonicalSetId: number | null;
  targetCanonicalSetSha256: string | null;
  boundaryAt: string | null;
  guardStartedAt: string | null;
  guardCompletedAt: string | null;
  publicationIds: number[];
  sourceBatchIds: number[];
  counts: PositionSnapshotContinuityCounts;
  reasons: PositionSnapshotContinuityReason[];
  evidenceWritten: false;
  tradingActionPerformed: false;
}

export interface FuturePositionEpisodePreviewCounts {
  canonicalMemberCount: number;
  openingPositionCount: number;
  openingContractCount: string;
  postBoundaryOrderEventCount: number;
  postBoundaryFillEventCount: number;
  supportingOrderCount: number;
  excludedNonOptionEventCount: number;
  executionGroupCount: number;
  sourceEventCount: number;
  plannedPositionEpisodeCount: number;
  plannedOpenEpisodeCount: number;
  plannedClosedEpisodeCount: number;
  leftCensoredEpisodeCount: number;
  rightCensoredEpisodeCount: number;
  headlineEpisodeCount: number;
  headlineExcludedEpisodeCount: number;
  groupFeeAffectedEpisodeCount: number;
}

/** A fence-bound, in-memory build plan. It never stores or activates Episodes. */
export interface FuturePositionEpisodePreviewResponse {
  projectionName: string;
  projectionVersion: string;
  continuityPolicyVersion: string;
  accountKey: string;
  scope: 'moomoo_live_us_options';
  fenceKey: string;
  snapshotId: number;
  snapshotKey: string;
  targetPublicationId: number;
  targetPublicationKey: string;
  targetCanonicalSetId: number;
  targetCanonicalSetSha256: string;
  canonicalSourceBatchIds: number[];
  publicationSourceBatchIds: number[];
  boundaryAt: string;
  sourceCutoffAt: string;
  builderName: string;
  builderVersion: string;
  builderConfigSha256: string;
  evidenceSetSha256: string;
  plannedBuildKey: string;
  openingBoundaryPolicy: 'complete_snapshot';
  maxMultiplierProofResidual: string;
  sourceKnownFeeTotal: string;
  accountedKnownFeeTotal: string;
  retainedExecutionGroupFeeTotal: string;
  feeConservationByCurrency: Record<string, Record<string, string>>;
  feeConserved: true;
  headlineRealizedPnlGross: string | null;
  headlineTotalFee: string | null;
  headlineRealizedPnlNet: string | null;
  counts: FuturePositionEpisodePreviewCounts;
  warnings: string[];
  previewOnly: true;
  businessDataWritten: false;
  episodeBuildWritten: false;
  activationChanged: false;
  evidenceWritten: false;
  tradingActionPerformed: false;
}

/** Explicit confirm input; every hash must be echoed from the fenced preview. */
export interface FuturePositionEpisodeBuildConfirmInput {
  expectedFenceKey: string;
  expectedBuildKey: string;
  expectedEvidenceSetSha256: string;
  acceptLeftCensoredOpenings: boolean;
  acceptGroupFeeScope: boolean;
}

/** Immutable snapshot-fence identity recorded beside the appended build. */
export interface FuturePositionEpisodeBuildSnapshotFence {
  sourceKind: 'position_snapshot_fenced_canonical';
  snapshotId: number;
  snapshotKey: string;
  fenceKey: string;
  continuityPolicyVersion: string;
  targetPublicationId: number;
  targetPublicationKey: string;
  targetCanonicalSetId: number;
  targetCanonicalSetSha256: string;
  boundaryAt: string;
  sourceCutoffAt: string;
  projectionName: string;
  projectionVersion: string;
  linkKey: string;
}

/**
 * EpisodeBuild append result. Built is not activated: the default review
 * view stays unchanged until activation supports future builds.
 */
export interface FuturePositionEpisodeBuildConfirmResponse {
  dataState: string;
  duplicate: boolean;
  build: EpisodeBuildMetadata;
  reconciliation: EpisodeReconciliationSummary;
  summary: PositionEpisodeSummary;
  snapshotFence: FuturePositionEpisodeBuildSnapshotFence;
  message: string;
  activationChanged: false;
  tradingActionPerformed: false;
}
