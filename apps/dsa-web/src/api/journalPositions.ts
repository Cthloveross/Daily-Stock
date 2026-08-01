import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  ConfirmedPositionSnapshot,
  CurrentOptionPosition,
  FuturePositionEpisodeBuildConfirmInput,
  FuturePositionEpisodeBuildConfirmResponse,
  FuturePositionEpisodeBuildSnapshotFence,
  FuturePositionEpisodePreviewCounts,
  FuturePositionEpisodePreviewResponse,
  LatestPositionSnapshotResponse,
  PositionSnapshotBrokerCostContext,
  PositionSnapshotConfirmResponse,
  PositionSnapshotContinuityAssessment,
  PositionSnapshotContinuityCounts,
  PositionSnapshotContinuityReason,
  PositionSnapshotFilterCounts,
  PositionSnapshotFreshness,
  PositionSnapshotObservation,
  PositionSnapshotPreviewResponse,
  PositionSnapshotStability,
} from '../types/journalPositions';
import type {
  EpisodeBuildMetadata,
  EpisodeReconciliationSummary,
  PositionEpisodeSummary,
} from '../types/journal';

const BASE = '/api/v1/journal/v2/position-snapshots';
const POSITION_CHECK_TIMEOUT_MS = 180_000;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as UnknownRecord
    : {};
}

function firstValue(record: UnknownRecord, keys: string[]): unknown {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key];
  }
  return undefined;
}

function textValue(record: UnknownRecord, keys: string[], fallback = ''): string {
  const value = firstValue(record, keys);
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

function nullableText(record: UnknownRecord, keys: string[]): string | null {
  const value = firstValue(record, keys);
  if (value === undefined || value === null || value === '') return null;
  return typeof value === 'string' || typeof value === 'number' ? String(value) : null;
}

function numberValue(record: UnknownRecord, keys: string[], fallback = 0): number {
  const value = firstValue(record, keys);
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function nullableNumber(record: UnknownRecord, keys: string[]): number | null {
  const value = firstValue(record, keys);
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function isPositiveInteger(value: number | null): value is number {
  return value != null && Number.isSafeInteger(value) && value > 0;
}

function isLowercaseSha256(value: string | null): value is string {
  return value != null && SHA256_PATTERN.test(value);
}

function isTimestampWithTimezone(value: string | null): value is string {
  return value != null
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && !Number.isNaN(Date.parse(value));
}

function requiredText(record: UnknownRecord, key: string): string {
  const value = record[key];
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${key} 缺失或无效，页面已停止处理该响应。`);
  }
  return value;
}

function requiredLowercaseSha256(record: UnknownRecord, key: string): string {
  const value = requiredText(record, key);
  if (!SHA256_PATTERN.test(value)) {
    throw new Error(`${key} 不是 64 位小写 SHA-256，页面已停止处理该响应。`);
  }
  return value;
}

function requiredPositiveInteger(record: UnknownRecord, key: string): number {
  const value = record[key];
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${key} 不是正安全整数，页面已停止处理该响应。`);
  }
  return value;
}

function requiredNonNegativeInteger(record: UnknownRecord, key: string): number {
  const value = record[key];
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${key} 不是非负安全整数，页面已停止处理该响应。`);
  }
  return value;
}

function requiredTimestamp(record: UnknownRecord, key: string): string {
  const value = requiredText(record, key);
  if (!isTimestampWithTimezone(value)) {
    throw new Error(`${key} 不是带时区的有效时间，页面已停止处理该响应。`);
  }
  return value;
}

function isFiniteDecimalText(value: unknown, allowNegative = true): value is string {
  const pattern = allowNegative
    ? /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/
    : /^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
  return typeof value === 'string'
    && pattern.test(value)
    && Number.isFinite(Number(value));
}

function requiredFiniteDecimalText(
  record: UnknownRecord,
  key: string,
  allowNegative = true,
): string {
  const value = record[key];
  if (!isFiniteDecimalText(value, allowNegative) || (!allowNegative && Number(value) < 0)) {
    throw new Error(
      `${key} 不是${allowNegative ? '' : '非负'}有限十进数字符串，页面已停止处理该响应。`,
    );
  }
  return value;
}

function nullableFiniteDecimalText(record: UnknownRecord, key: string): string | null {
  const value = record[key];
  if (value == null) return null;
  if (!isFiniteDecimalText(value)) {
    throw new Error(`${key} 不是有限十进数字符串或 null，页面已停止处理该响应。`);
  }
  return value;
}

function requiredPositiveIntegerArray(value: unknown, key: string): number[] {
  if (!Array.isArray(value) || value.some(
    (item) => typeof item !== 'number' || !Number.isSafeInteger(item) || item <= 0,
  )) {
    throw new Error(`${key} 包含无效身份，页面已停止处理该响应。`);
  }
  return [...value] as number[];
}

function stringRecordMap(value: unknown): Record<string, Record<string, string>> {
  const outer = asRecord(value);
  const result: Record<string, Record<string, string>> = {};
  for (const [currency, rawValues] of Object.entries(outer)) {
    const values = asRecord(rawValues);
    const normalized: Record<string, string> = {};
    for (const [key, rawValue] of Object.entries(values)) {
      if (typeof rawValue !== 'string') {
        throw new Error('fee_conservation_by_currency 含非字符串金额，页面已停止处理该响应。');
      }
      if (!isFiniteDecimalText(rawValue)) {
        throw new Error('fee_conservation_by_currency 含非有限十进制金额，页面已停止处理该响应。');
      }
      normalized[key] = rawValue;
    }
    result[currency] = normalized;
  }
  return result;
}

function booleanValue(record: UnknownRecord, keys: string[], fallback = false): boolean {
  const value = firstValue(record, keys);
  return typeof value === 'boolean' ? value : fallback;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter(Boolean);
}

function integerArray(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === 'number' && Number.isSafeInteger(item) && item >= 0) {
      return [item];
    }
    if (typeof item === 'string' && /^\d+$/.test(item.trim())) {
      const parsed = Number(item);
      return Number.isSafeInteger(parsed) ? [parsed] : [];
    }
    return [];
  });
}

function assertFalse(value: unknown, label: string): false {
  if (value !== false) {
    throw new Error(`${label} 未满足只读安全契约，页面已停止处理该响应。`);
  }
  return false;
}

function assertTrue(value: unknown, label: string): true {
  if (value !== true) {
    throw new Error(`${label} 未满足面向未来的证据契约，页面已停止处理该响应。`);
  }
  return true;
}

function normalizeCostContext(value: unknown): PositionSnapshotBrokerCostContext {
  const context = asRecord(value);
  return {
    costPrice: nullableText(context, ['costPrice']),
    costPriceValid: typeof context.costPriceValid === 'boolean'
      ? context.costPriceValid
      : null,
    averageCost: nullableText(context, ['averageCost']),
    dilutedCost: nullableText(context, ['dilutedCost']),
    semantics: nullableText(context, ['semantics']),
  };
}

function normalizePosition(value: unknown): CurrentOptionPosition {
  const item = asRecord(value);
  return {
    symbol: textValue(item, ['symbol'], '—'),
    underlying: textValue(item, ['underlying'], '—'),
    expiry: textValue(item, ['expiry'], '—'),
    strike: textValue(item, ['strike'], '—'),
    optionRight: textValue(item, ['optionRight'], '—'),
    positionSide: textValue(item, ['positionSide'], '—'),
    quantityContracts: textValue(item, ['quantityContracts'], '0'),
    signedQuantityContracts: textValue(item, ['signedQuantityContracts'], '0'),
    canSellQuantityContracts: nullableText(item, ['canSellQuantityContracts']),
    currency: textValue(item, ['currency'], '—'),
    contractMultiplier: nullableText(item, ['contractMultiplier']),
    basis: textValue(item, ['basis', 'contractMultiplierBasis'], '—'),
    brokerCostContext: normalizeCostContext(
      firstValue(item, ['brokerCostContext', 'context']),
    ),
  };
}

function normalizeStability(value: unknown, fallbackStable: boolean): PositionSnapshotStability {
  const stability = asRecord(value);
  return {
    stable: booleanValue(stability, ['stable'], fallbackStable),
    comparisonBasis: textValue(stability, ['comparisonBasis']),
    addedSymbols: stringArray(stability.addedSymbols),
    removedSymbols: stringArray(stability.removedSymbols),
    changedSymbols: stringArray(stability.changedSymbols),
  };
}

function normalizeContentState(value: unknown): PositionSnapshotObservation['contentState'] {
  if (value === 'positions' || value === 'empty' || value === 'incomplete' || value === 'failed') {
    return value;
  }
  return 'failed';
}

function normalizePositions(value: unknown): CurrentOptionPosition[] {
  if (!Array.isArray(value)) return [];
  return value.map(normalizePosition);
}

function fallbackTotalContracts(positions: CurrentOptionPosition[]): string {
  const total = positions.reduce((sum, position) => {
    const quantity = Number(position.quantityContracts);
    return Number.isFinite(quantity) ? sum + Math.abs(quantity) : sum;
  }, 0);
  return String(total);
}

function normalizeFilterCounts(value: unknown): PositionSnapshotFilterCounts {
  const counts = asRecord(value);
  return {
    firstTotalRows: numberValue(counts, ['firstTotalRows']),
    secondTotalRows: numberValue(counts, ['secondTotalRows']),
    filteredNonUsRows: numberValue(
      counts,
      ['filteredNonUsRows', 'secondFilteredNonUsRows'],
    ),
    filteredNonOptionRows: numberValue(
      counts,
      ['filteredNonOptionRows', 'secondFilteredNonOptionRows'],
    ),
    filteredZeroQuantityRows: numberValue(
      counts,
      ['filteredZeroQuantityRows', 'secondFilteredZeroQuantityRows'],
    ),
    contractSpecCount: numberValue(counts, ['contractSpecCount', 'contractSpecs']),
    validationIssueCount: numberValue(
      counts,
      ['validationIssueCount', 'validationIssues'],
    ),
  };
}

function normalizeHashes(record: UnknownRecord): Record<string, string> {
  const hashes: Record<string, string> = {};
  const nested = asRecord(record.hashes);
  for (const [key, value] of Object.entries(nested)) {
    if (typeof value === 'string' && value) hashes[key] = value;
  }
  for (const key of [
    'positionsSha256',
    'snapshotSha256',
    'scopeSha256',
    'sourceSha256',
    'evidenceSha256',
    'stabilityEvidenceSha256',
    'memberSetSha256',
  ]) {
    const value = record[key];
    if (typeof value === 'string' && value) hashes[key] = value;
  }
  return hashes;
}

function normalizeObservation(
  value: unknown,
  fallbackPositions: unknown = [],
  fallbackHistoricalOpeningProven?: unknown,
): PositionSnapshotObservation {
  const observation = asRecord(value);
  const interval = asRecord(firstValue(observation, ['observedInterval']));
  const stability = asRecord(observation.stability);
  const positions = normalizePositions(
    firstValue(observation, ['positions']) ?? fallbackPositions,
  );
  const filterCounts = normalizeFilterCounts(
    firstValue(observation, ['filterCounts', 'counts']),
  );
  const positionCount = numberValue(
    observation,
    ['positionCount'],
    numberValue(asRecord(observation.counts), ['positions'], positions.length),
  );
  const retrievalComplete = booleanValue(observation, ['retrievalComplete']);
  const stable = booleanValue(observation, ['stable'], booleanValue(stability, ['stable']));
  const stabilityDetails = normalizeStability(stability, stable);
  if (stable !== stabilityDetails.stable) {
    throw new Error('stable 与 stability.stable 不一致，页面已停止处理该响应。');
  }
  const contentState = normalizeContentState(
    firstValue(observation, ['contentState'])
      ?? (retrievalComplete ? (positionCount > 0 ? 'positions' : 'empty') : 'failed'),
  );
  const historicalOpeningProven = firstValue(
    observation,
    ['historicalOpeningProven'],
  ) ?? fallbackHistoricalOpeningProven;

  return {
    observedFrom: textValue(
      observation,
      ['observedFrom', 'queryStartedAt'],
      textValue(interval, ['startedAt']),
    ),
    observedThrough: textValue(
      observation,
      ['observedThrough', 'queryCompletedAt'],
      textValue(interval, ['completedAt']),
    ),
    operationCompletedAt: textValue(
      observation,
      ['operationCompletedAt'],
      textValue(interval, ['completedAt']),
    ),
    brokerAsOf: nullableText(observation, ['brokerAsOf'])
      ?? nullableText(interval, ['brokerAsOf']),
    stable,
    stability: stabilityDetails,
    retrievalComplete,
    positionSnapshotComplete: booleanValue(observation, ['positionSnapshotComplete']),
    contractSpecStatus: textValue(observation, ['contractSpecStatus'], 'unknown'),
    analysisReady: booleanValue(observation, ['analysisReady']),
    contentState,
    positionCount,
    totalContracts: textValue(
      observation,
      ['totalContracts'],
      fallbackTotalContracts(positions),
    ),
    futureAnchorCandidate: booleanValue(observation, ['futureAnchorCandidate']),
    historicalOpeningProven: assertFalse(
      historicalOpeningProven,
      'historical_opening_proven',
    ),
    filterCounts,
    positions,
    hashes: normalizeHashes(observation),
  };
}

function normalizeFreshness(value: unknown): PositionSnapshotFreshness {
  const freshness = asRecord(value);
  const rawStatus = textValue(freshness, ['status']);
  const status = rawStatus === 'fresh' || rawStatus === 'stale'
    ? rawStatus
    : 'unknown';
  return {
    status,
    ageSeconds: nullableNumber(freshness, ['ageSeconds']),
    futureSkewSeconds: nullableNumber(freshness, ['futureSkewSeconds']),
    thresholdSeconds: numberValue(freshness, ['thresholdSeconds']),
    evaluatedAt: textValue(freshness, ['evaluatedAt']),
    ageBasis: textValue(freshness, ['ageBasis']),
    timeSemantics: textValue(freshness, ['timeSemantics']),
  };
}

function normalizeAccountBindingStatus(
  value: unknown,
): ConfirmedPositionSnapshot['accountBindingStatus'] {
  if (
    value === 'matches_latest_publication'
    || value === 'mismatch'
    || value === 'publication_unavailable'
  ) {
    return value;
  }
  return 'publication_unavailable';
}

function normalizePublicationAnchorStatus(
  value: unknown,
): ConfirmedPositionSnapshot['publicationAnchorStatus'] {
  if (value === 'latest' || value === 'superseded' || value === 'publication_unavailable') {
    return value;
  }
  return 'publication_unavailable';
}

function normalizeSnapshot(
  value: unknown,
  fallbackPositions: unknown = [],
  fallbackHistoricalOpeningProven?: unknown,
): ConfirmedPositionSnapshot {
  const snapshot = asRecord(value);
  const freshness = normalizeFreshness(snapshot.freshness);
  const accountBindingStatus = normalizeAccountBindingStatus(snapshot.accountBindingStatus);
  const publicationAnchorStatus = normalizePublicationAnchorStatus(
    snapshot.publicationAnchorStatus,
  );
  const isCurrent = booleanValue(snapshot, ['isCurrent']);
  if (
    isCurrent
    && (
      freshness.status !== 'fresh'
      || accountBindingStatus !== 'matches_latest_publication'
      || publicationAnchorStatus !== 'latest'
    )
  ) {
    throw new Error('当前仓位快照状态自相矛盾，页面已停止把它视为当前。');
  }
  return {
    id: numberValue(snapshot, ['id', 'snapshotId']),
    key: textValue(snapshot, ['key', 'snapshotKey']),
    recordedAt: textValue(snapshot, ['recordedAt', 'confirmedAt']),
    acknowledgedFutureOnly: assertTrue(
      snapshot.acknowledgedFutureOnly,
      'acknowledged_future_only',
    ),
    freshness,
    accountBindingStatus,
    publicationAnchorStatus,
    isCurrent,
    observation: normalizeObservation(
      firstValue(snapshot, ['observation']) ?? snapshot,
      fallbackPositions,
      firstValue(snapshot, ['historicalOpeningProven'])
        ?? fallbackHistoricalOpeningProven,
    ),
  };
}

export async function fetchLatestPositionSnapshot(): Promise<LatestPositionSnapshotResponse> {
  const { data } = await apiClient.get(`${BASE}/latest`);
  const response = toCamelCase<UnknownRecord>(data);
  const latestValue = response.latest;
  const latest = latestValue === null || latestValue === undefined
    ? null
    : normalizeSnapshot(
      latestValue,
      response.members,
      response.historicalOpeningProven,
    );
  const latestIsCurrent = booleanValue(response, ['latestIsCurrent']);
  const latestAccountBindingMatches = typeof response.latestAccountBindingMatches === 'boolean'
    ? response.latestAccountBindingMatches
    : null;
  const latestPublicationAnchorMatches = typeof response.latestPublicationAnchorMatches === 'boolean'
    ? response.latestPublicationAnchorMatches
    : null;
  if (latest && latest.isCurrent !== latestIsCurrent) {
    throw new Error('最新仓位快照的 current 状态不一致，页面已停止处理该响应。');
  }
  if (
    latestIsCurrent
    && (latestAccountBindingMatches !== true || latestPublicationAnchorMatches !== true)
  ) {
    throw new Error('最新仓位快照的账户连续性状态不一致，页面已停止处理该响应。');
  }
  return {
    enabled: booleanValue(response, ['enabled']),
    configured: booleanValue(response, ['configured']),
    continuityReady: booleanValue(response, ['continuityReady']),
    continuityReason: nullableText(
      response,
      ['continuityReason', 'continuityBlockingReason'],
    ),
    latestIsCurrent,
    latestAccountBindingMatches,
    latestPublicationAnchorMatches,
    latest,
    tradingActionPerformed: assertFalse(
      response.tradingActionPerformed,
      'trading_action_performed',
    ),
  };
}

export async function previewPositionSnapshot(): Promise<PositionSnapshotPreviewResponse> {
  const { data } = await apiClient.post(`${BASE}/preview`, {}, {
    timeout: POSITION_CHECK_TIMEOUT_MS,
  });
  const response = toCamelCase<UnknownRecord>(data);
  const blockingReasons = stringArray(response.blockingReasons);
  const blockingReason = nullableText(response, ['blockingReason']);
  if (blockingReason && !blockingReasons.includes(blockingReason)) {
    blockingReasons.push(blockingReason);
  }
  return {
    artifactId: numberValue(response, ['artifactId']),
    artifactKey: textValue(response, ['artifactKey']),
    previewKey: textValue(response, ['previewKey']),
    expiresAt: textValue(response, ['expiresAt']),
    confirmAllowed: booleanValue(response, ['confirmAllowed']),
    blockingReasons,
    warnings: stringArray(response.warnings),
    observation: normalizeObservation(
      firstValue(response, ['observation']) ?? response,
      response.positions,
      response.historicalOpeningProven,
    ),
    evidenceWritten: assertFalse(response.evidenceWritten, 'evidence_written'),
    tradingActionPerformed: assertFalse(
      response.tradingActionPerformed,
      'trading_action_performed',
    ),
  };
}

export async function confirmPositionSnapshot(
  artifactId: number,
  previewKey: string,
  acknowledgeFutureOnly: true,
): Promise<PositionSnapshotConfirmResponse> {
  const { data } = await apiClient.post(`${BASE}/${artifactId}/confirm`, {
    preview_key: previewKey,
    acknowledge_future_only: acknowledgeFutureOnly,
  });
  const response = toCamelCase<UnknownRecord>(data);
  return {
    snapshot: normalizeSnapshot(
      response.snapshot,
      response.members,
      response.historicalOpeningProven,
    ),
    duplicate: booleanValue(response, ['duplicate']),
    tradingActionPerformed: assertFalse(
      response.tradingActionPerformed,
      'trading_action_performed',
    ),
  };
}

function normalizeContinuityCounts(value: unknown): PositionSnapshotContinuityCounts {
  const counts = asRecord(value);
  return {
    snapshotMemberCount: numberValue(counts, ['snapshotMemberCount']),
    chainPublicationCount: numberValue(counts, ['chainPublicationCount']),
    chainBatchCount: numberValue(counts, ['chainBatchCount']),
    targetOrderCount: numberValue(counts, ['targetOrderCount']),
    targetFillCount: numberValue(counts, ['targetFillCount']),
    guardFillCount: numberValue(counts, ['guardFillCount']),
    preBoundaryFillCount: numberValue(counts, ['preBoundaryFillCount']),
    postBoundaryFillCount: numberValue(counts, ['postBoundaryFillCount']),
    aggregateChangedPreBoundaryCount: numberValue(
      counts,
      ['aggregateChangedPreBoundaryCount'],
    ),
    straddlingOrderCount: numberValue(counts, ['straddlingOrderCount']),
    straddlingGroupCount: numberValue(counts, ['straddlingGroupCount']),
    unbackedPostBoundaryCount: numberValue(counts, ['unbackedPostBoundaryCount']),
    optionIdentityMismatchCount: numberValue(counts, ['optionIdentityMismatchCount']),
  };
}

function normalizeContinuityReasons(value: unknown): PositionSnapshotContinuityReason[] {
  if (!Array.isArray(value)) return [];
  return value.map((raw) => {
    const reason = asRecord(raw);
    return {
      code: textValue(reason, ['code'], 'unknown'),
      message: textValue(reason, ['message']),
      entityKind: nullableText(reason, ['entityKind']),
      entityIds: integerArray(reason.entityIds),
      count: numberValue(reason, ['count']),
    };
  });
}

export async function fetchPositionSnapshotContinuity(): Promise<PositionSnapshotContinuityAssessment> {
  const { data } = await apiClient.get(`${BASE}/episode-boundary-readiness`);
  const response = toCamelCase<UnknownRecord>(data);
  const rawStatus = textValue(response, ['status']);
  if (!(
    rawStatus === 'no_snapshot'
    || rawStatus === 'awaiting_refresh'
    || rawStatus === 'blocked'
    || rawStatus === 'ready'
  )) {
    throw new Error('Episode 连续性门禁状态无效，页面已停止处理该响应。');
  }
  const status = rawStatus;
  const readyForEpisodeBuild = booleanValue(response, ['readyForEpisodeBuild']);
  if ((status === 'ready') !== readyForEpisodeBuild) {
    throw new Error('Episode 连续性门禁状态自相矛盾，页面已停止处理该响应。');
  }
  const assessment: PositionSnapshotContinuityAssessment = {
    policyVersion: textValue(response, ['policyVersion']),
    status,
    readyForEpisodeBuild,
    fenceKey: nullableText(response, ['fenceKey']),
    accountKey: textValue(response, ['accountKey']),
    snapshotId: nullableNumber(response, ['snapshotId']),
    snapshotKey: nullableText(response, ['snapshotKey']),
    anchorPublicationId: nullableNumber(response, ['anchorPublicationId']),
    anchorPublicationKey: nullableText(response, ['anchorPublicationKey']),
    targetPublicationId: nullableNumber(response, ['targetPublicationId']),
    targetPublicationKey: nullableText(response, ['targetPublicationKey']),
    targetCanonicalSetId: nullableNumber(response, ['targetCanonicalSetId']),
    targetCanonicalSetSha256: nullableText(response, ['targetCanonicalSetSha256']),
    boundaryAt: nullableText(response, ['boundaryAt']),
    guardStartedAt: nullableText(response, ['guardStartedAt']),
    guardCompletedAt: nullableText(response, ['guardCompletedAt']),
    publicationIds: integerArray(response.publicationIds),
    sourceBatchIds: integerArray(response.sourceBatchIds),
    counts: normalizeContinuityCounts(response.counts),
    reasons: normalizeContinuityReasons(response.reasons),
    evidenceWritten: assertFalse(response.evidenceWritten, 'evidence_written'),
    tradingActionPerformed: assertFalse(
      response.tradingActionPerformed,
      'trading_action_performed',
    ),
  };

  if (status !== 'ready' && assessment.fenceKey != null) {
    throw new Error('Episode 连续性门禁尚未 ready 却返回 fence，页面已停止处理该响应。');
  }
  if (status === 'ready') {
    const invalidFields: string[] = [];
    if (!isLowercaseSha256(assessment.fenceKey)) invalidFields.push('fence_key');
    if (!isPositiveInteger(assessment.snapshotId)) invalidFields.push('snapshot_id');
    if (!isLowercaseSha256(assessment.snapshotKey)) invalidFields.push('snapshot_key');
    if (!isPositiveInteger(assessment.targetPublicationId)) {
      invalidFields.push('target_publication_id');
    }
    if (!isLowercaseSha256(assessment.targetPublicationKey)) {
      invalidFields.push('target_publication_key');
    }
    if (!isPositiveInteger(assessment.targetCanonicalSetId)) {
      invalidFields.push('target_canonical_set_id');
    }
    if (!isLowercaseSha256(assessment.targetCanonicalSetSha256)) {
      invalidFields.push('target_canonical_set_sha256');
    }
    if (!isTimestampWithTimezone(assessment.boundaryAt)) invalidFields.push('boundary_at');
    if (invalidFields.length > 0) {
      throw new Error(
        `Episode 连续性门禁 ready 响应缺少或包含无效的冻结身份：${invalidFields.join('、')}。页面已停止处理该响应。`,
      );
    }
  }
  return assessment;
}

function normalizeFutureEpisodeCounts(value: unknown): FuturePositionEpisodePreviewCounts {
  const counts = asRecord(value);
  const normalized: FuturePositionEpisodePreviewCounts = {
    canonicalMemberCount: requiredNonNegativeInteger(counts, 'canonicalMemberCount'),
    openingPositionCount: requiredNonNegativeInteger(counts, 'openingPositionCount'),
    openingContractCount: requiredFiniteDecimalText(counts, 'openingContractCount', false),
    postBoundaryOrderEventCount: requiredNonNegativeInteger(
      counts,
      'postBoundaryOrderEventCount',
    ),
    postBoundaryFillEventCount: requiredNonNegativeInteger(
      counts,
      'postBoundaryFillEventCount',
    ),
    supportingOrderCount: requiredNonNegativeInteger(counts, 'supportingOrderCount'),
    excludedNonOptionEventCount: requiredNonNegativeInteger(
      counts,
      'excludedNonOptionEventCount',
    ),
    executionGroupCount: requiredNonNegativeInteger(counts, 'executionGroupCount'),
    sourceEventCount: requiredNonNegativeInteger(counts, 'sourceEventCount'),
    plannedPositionEpisodeCount: requiredNonNegativeInteger(
      counts,
      'plannedPositionEpisodeCount',
    ),
    plannedOpenEpisodeCount: requiredNonNegativeInteger(counts, 'plannedOpenEpisodeCount'),
    plannedClosedEpisodeCount: requiredNonNegativeInteger(
      counts,
      'plannedClosedEpisodeCount',
    ),
    leftCensoredEpisodeCount: requiredNonNegativeInteger(counts, 'leftCensoredEpisodeCount'),
    rightCensoredEpisodeCount: requiredNonNegativeInteger(
      counts,
      'rightCensoredEpisodeCount',
    ),
    headlineEpisodeCount: requiredNonNegativeInteger(counts, 'headlineEpisodeCount'),
    headlineExcludedEpisodeCount: requiredNonNegativeInteger(
      counts,
      'headlineExcludedEpisodeCount',
    ),
    groupFeeAffectedEpisodeCount: requiredNonNegativeInteger(
      counts,
      'groupFeeAffectedEpisodeCount',
    ),
  };
  if (normalized.plannedPositionEpisodeCount !== (
    normalized.plannedOpenEpisodeCount + normalized.plannedClosedEpisodeCount
  )) {
    throw new Error('未来回合预览的总回合数与 open/closed 不一致，页面已停止处理该响应。');
  }
  if (normalized.plannedPositionEpisodeCount !== (
    normalized.headlineEpisodeCount + normalized.headlineExcludedEpisodeCount
  )) {
    throw new Error('未来回合预览的 Headline 纳入/排除计数不一致，页面已停止处理该响应。');
  }
  if (normalized.sourceEventCount !== (
    normalized.postBoundaryOrderEventCount + normalized.postBoundaryFillEventCount
  )) {
    throw new Error('未来回合预览的来源事件计数不一致，页面已停止处理该响应。');
  }
  for (const [label, value] of [
    ['leftCensoredEpisodeCount', normalized.leftCensoredEpisodeCount],
    ['rightCensoredEpisodeCount', normalized.rightCensoredEpisodeCount],
    ['groupFeeAffectedEpisodeCount', normalized.groupFeeAffectedEpisodeCount],
  ] as const) {
    if (value > normalized.plannedPositionEpisodeCount) {
      throw new Error(`${label} 超过计划总回合数，页面已停止处理该响应。`);
    }
  }
  return normalized;
}

export async function fetchFuturePositionEpisodePreview(
  expectedFenceKey: string,
): Promise<FuturePositionEpisodePreviewResponse> {
  if (!SHA256_PATTERN.test(expectedFenceKey)) {
    throw new Error('expected_fence_key 不是 64 位小写 SHA-256，未发起预览请求。');
  }
  const { data } = await apiClient.get(
    '/api/v1/journal/v2/episode-builds/position-snapshot/preview',
    { params: { expected_fence_key: expectedFenceKey } },
  );
  const response = toCamelCase<UnknownRecord>(data);
  const scope = requiredText(response, 'scope');
  const openingBoundaryPolicy = requiredText(response, 'openingBoundaryPolicy');
  if (scope !== 'moomoo_live_us_options' || openingBoundaryPolicy !== 'complete_snapshot') {
    throw new Error('未来回合预览的 scope 或边界策略无效，页面已停止处理该响应。');
  }
  const feeConserved = assertTrue(response.feeConserved, 'fee_conserved');
  const previewOnly = assertTrue(response.previewOnly, 'preview_only');
  const boundaryAt = requiredTimestamp(response, 'boundaryAt');
  const sourceCutoffAt = requiredTimestamp(response, 'sourceCutoffAt');
  if (Date.parse(sourceCutoffAt) < Date.parse(boundaryAt)) {
    throw new Error('sourceCutoffAt 早于 boundaryAt，页面已停止处理该响应。');
  }
  const normalized: FuturePositionEpisodePreviewResponse = {
    projectionName: requiredText(response, 'projectionName'),
    projectionVersion: requiredText(response, 'projectionVersion'),
    continuityPolicyVersion: requiredText(response, 'continuityPolicyVersion'),
    accountKey: requiredText(response, 'accountKey'),
    scope,
    fenceKey: requiredLowercaseSha256(response, 'fenceKey'),
    snapshotId: requiredPositiveInteger(response, 'snapshotId'),
    snapshotKey: requiredLowercaseSha256(response, 'snapshotKey'),
    targetPublicationId: requiredPositiveInteger(response, 'targetPublicationId'),
    targetPublicationKey: requiredLowercaseSha256(response, 'targetPublicationKey'),
    targetCanonicalSetId: requiredPositiveInteger(response, 'targetCanonicalSetId'),
    targetCanonicalSetSha256: requiredLowercaseSha256(
      response,
      'targetCanonicalSetSha256',
    ),
    canonicalSourceBatchIds: requiredPositiveIntegerArray(
      response.canonicalSourceBatchIds,
      'canonicalSourceBatchIds',
    ),
    publicationSourceBatchIds: requiredPositiveIntegerArray(
      response.publicationSourceBatchIds,
      'publicationSourceBatchIds',
    ),
    boundaryAt,
    sourceCutoffAt,
    builderName: requiredText(response, 'builderName'),
    builderVersion: requiredText(response, 'builderVersion'),
    builderConfigSha256: requiredLowercaseSha256(response, 'builderConfigSha256'),
    evidenceSetSha256: requiredLowercaseSha256(response, 'evidenceSetSha256'),
    plannedBuildKey: requiredLowercaseSha256(response, 'plannedBuildKey'),
    openingBoundaryPolicy,
    maxMultiplierProofResidual: requiredFiniteDecimalText(
      response,
      'maxMultiplierProofResidual',
      false,
    ),
    sourceKnownFeeTotal: requiredFiniteDecimalText(response, 'sourceKnownFeeTotal', false),
    accountedKnownFeeTotal: requiredFiniteDecimalText(
      response,
      'accountedKnownFeeTotal',
      false,
    ),
    retainedExecutionGroupFeeTotal: requiredFiniteDecimalText(
      response,
      'retainedExecutionGroupFeeTotal',
      false,
    ),
    feeConservationByCurrency: stringRecordMap(response.feeConservationByCurrency),
    feeConserved,
    headlineRealizedPnlGross: nullableFiniteDecimalText(response, 'headlineRealizedPnlGross'),
    headlineTotalFee: nullableFiniteDecimalText(response, 'headlineTotalFee'),
    headlineRealizedPnlNet: nullableFiniteDecimalText(response, 'headlineRealizedPnlNet'),
    counts: normalizeFutureEpisodeCounts(response.counts),
    warnings: stringArray(response.warnings),
    previewOnly,
    businessDataWritten: assertFalse(response.businessDataWritten, 'business_data_written'),
    episodeBuildWritten: assertFalse(response.episodeBuildWritten, 'episode_build_written'),
    activationChanged: assertFalse(response.activationChanged, 'activation_changed'),
    evidenceWritten: assertFalse(response.evidenceWritten, 'evidence_written'),
    tradingActionPerformed: assertFalse(
      response.tradingActionPerformed,
      'trading_action_performed',
    ),
  };
  return normalized;
}

const SNAPSHOT_FENCE_SOURCE_KIND = 'position_snapshot_fenced_canonical';

function normalizeConfirmSnapshotFence(
  value: unknown,
): FuturePositionEpisodeBuildSnapshotFence {
  const fence = asRecord(value);
  if (fence.sourceKind !== SNAPSHOT_FENCE_SOURCE_KIND) {
    throw new Error('source_kind 不是 snapshot-fence 构建来源，页面已停止处理该响应。');
  }
  return {
    sourceKind: SNAPSHOT_FENCE_SOURCE_KIND,
    snapshotId: requiredPositiveInteger(fence, 'snapshotId'),
    snapshotKey: requiredLowercaseSha256(fence, 'snapshotKey'),
    fenceKey: requiredLowercaseSha256(fence, 'fenceKey'),
    continuityPolicyVersion: requiredText(fence, 'continuityPolicyVersion'),
    targetPublicationId: requiredPositiveInteger(fence, 'targetPublicationId'),
    targetPublicationKey: requiredLowercaseSha256(fence, 'targetPublicationKey'),
    targetCanonicalSetId: requiredPositiveInteger(fence, 'targetCanonicalSetId'),
    targetCanonicalSetSha256: requiredLowercaseSha256(
      fence,
      'targetCanonicalSetSha256',
    ),
    boundaryAt: requiredTimestamp(fence, 'boundaryAt'),
    sourceCutoffAt: requiredTimestamp(fence, 'sourceCutoffAt'),
    projectionName: requiredText(fence, 'projectionName'),
    projectionVersion: requiredText(fence, 'projectionVersion'),
    linkKey: requiredLowercaseSha256(fence, 'linkKey'),
  };
}

export async function confirmFuturePositionEpisodeBuild(
  input: FuturePositionEpisodeBuildConfirmInput,
): Promise<FuturePositionEpisodeBuildConfirmResponse> {
  const echoedKeys = [
    ['expected_fence_key', input.expectedFenceKey],
    ['expected_build_key', input.expectedBuildKey],
    ['expected_evidence_set_sha256', input.expectedEvidenceSetSha256],
  ] as const;
  for (const [label, value] of echoedKeys) {
    if (!SHA256_PATTERN.test(value)) {
      throw new Error(`${label} 不是 64 位小写 SHA-256，未发起正式构建请求。`);
    }
  }
  const { data } = await apiClient.post(
    '/api/v1/journal/v2/episode-builds/position-snapshot',
    {
      expected_fence_key: input.expectedFenceKey,
      expected_build_key: input.expectedBuildKey,
      expected_evidence_set_sha256: input.expectedEvidenceSetSha256,
      accept_left_censored_openings: input.acceptLeftCensoredOpenings,
      accept_group_fee_scope: input.acceptGroupFeeScope,
    },
  );
  const response = toCamelCase<UnknownRecord>(data);
  if (typeof response.duplicate !== 'boolean') {
    throw new Error('duplicate 缺失或无效，页面已停止处理该响应。');
  }
  const build = asRecord(response.build);
  const buildId = requiredPositiveInteger(build, 'id');
  const buildKey = requiredLowercaseSha256(build, 'buildKey');
  const positionEpisodeCount = requiredNonNegativeInteger(
    build,
    'positionEpisodeCount',
  );
  if (build.sourceKind !== SNAPSHOT_FENCE_SOURCE_KIND) {
    throw new Error('source_kind 不是 snapshot-fence 构建来源，页面已停止处理该响应。');
  }
  const snapshotFence = normalizeConfirmSnapshotFence(response.snapshotFence);
  if (
    buildKey !== input.expectedBuildKey
    || snapshotFence.fenceKey !== input.expectedFenceKey
  ) {
    throw new Error('服务器回写的 build/fence 身份与本次确认不一致，页面已停止处理该响应。');
  }
  return {
    dataState: requiredText(response, 'dataState'),
    duplicate: response.duplicate,
    build: {
      ...(build as unknown as EpisodeBuildMetadata),
      id: buildId,
      buildKey,
      positionEpisodeCount,
    },
    reconciliation: asRecord(
      response.reconciliation,
    ) as unknown as EpisodeReconciliationSummary,
    summary: asRecord(response.summary) as unknown as PositionEpisodeSummary,
    snapshotFence,
    message: requiredText(response, 'message'),
    activationChanged: assertFalse(response.activationChanged, 'activation_changed'),
    tradingActionPerformed: assertFalse(
      response.tradingActionPerformed,
      'trading_action_performed',
    ),
  };
}
