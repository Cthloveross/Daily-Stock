import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CurrentPositionsSnapshotCard from '../CurrentPositionsSnapshotCard';
import {
  confirmFuturePositionEpisodeBuild,
  confirmPositionSnapshot,
  fetchFuturePositionEpisodePreview,
  fetchLatestPositionSnapshot,
  fetchPositionSnapshotContinuity,
  previewPositionSnapshot,
} from '../../../api/journalPositions';
import type {
  ConfirmedPositionSnapshot,
  FuturePositionEpisodeBuildConfirmResponse,
  FuturePositionEpisodePreviewResponse,
  LatestPositionSnapshotResponse,
  PositionSnapshotContinuityAssessment,
  PositionSnapshotObservation,
  PositionSnapshotPreviewResponse,
} from '../../../types/journalPositions';

vi.mock('../../../api/journalPositions', () => ({
  confirmFuturePositionEpisodeBuild: vi.fn(),
  confirmPositionSnapshot: vi.fn(),
  fetchFuturePositionEpisodePreview: vi.fn(),
  fetchLatestPositionSnapshot: vi.fn(),
  fetchPositionSnapshotContinuity: vi.fn(),
  previewPositionSnapshot: vi.fn(),
}));

const fetchLatestMock = vi.mocked(fetchLatestPositionSnapshot);
const fetchContinuityMock = vi.mocked(fetchPositionSnapshotContinuity);
const fetchFuturePreviewMock = vi.mocked(fetchFuturePositionEpisodePreview);
const previewMock = vi.mocked(previewPositionSnapshot);
const confirmMock = vi.mocked(confirmPositionSnapshot);
const confirmFutureBuildMock = vi.mocked(confirmFuturePositionEpisodeBuild);

const observation: PositionSnapshotObservation = {
  observedFrom: '2026-07-30T13:00:00Z',
  observedThrough: '2026-07-30T13:00:02Z',
  operationCompletedAt: '2026-07-30T13:00:03Z',
  brokerAsOf: null,
  stable: true,
  stability: {
    stable: true,
    comparisonBasis: 'instrument_position_side_signed_quantity_contracts',
    addedSymbols: [],
    removedSymbols: [],
    changedSymbols: [],
  },
  retrievalComplete: true,
  positionSnapshotComplete: true,
  contractSpecStatus: 'complete',
  analysisReady: true,
  contentState: 'positions',
  positionCount: 1,
  totalContracts: '2',
  futureAnchorCandidate: true,
  historicalOpeningProven: false,
  filterCounts: {
    firstTotalRows: 2,
    secondTotalRows: 3,
    filteredNonUsRows: 0,
    filteredNonOptionRows: 0,
    filteredZeroQuantityRows: 2,
    contractSpecCount: 1,
    validationIssueCount: 0,
  },
  positions: [{
    symbol: 'US.NVDA260918C00150000',
    underlying: 'NVDA',
    expiry: '2026-09-18',
    strike: '150',
    optionRight: 'C',
    positionSide: 'LONG',
    quantityContracts: '2',
    signedQuantityContracts: '2',
    canSellQuantityContracts: '2',
    currency: 'USD',
    contractMultiplier: '100',
    basis: 'moomoo_market_snapshot_three_field_consensus',
    brokerCostContext: {
      costPrice: '2.5',
      costPriceValid: true,
      averageCost: '2.4',
      dilutedCost: '2.3',
      semantics: 'broker_display_context_only',
    },
  }],
  hashes: {
    positionsSha256: 'a'.repeat(64),
    snapshotSha256: 'b'.repeat(64),
  },
};

const snapshot: ConfirmedPositionSnapshot = {
  id: 17,
  key: 'c'.repeat(64),
  recordedAt: '2026-07-30T13:01:00Z',
  acknowledgedFutureOnly: true,
  freshness: {
    status: 'fresh',
    ageSeconds: 60,
    futureSkewSeconds: 0,
    thresholdSeconds: 1800,
    evaluatedAt: '2026-07-30T13:01:03Z',
    ageBasis: 'operation_completed_at',
    timeSemantics: 'locally_bracketed_observation_not_broker_as_of',
  },
  accountBindingStatus: 'matches_latest_publication',
  publicationAnchorStatus: 'latest',
  isCurrent: true,
  observation,
};

const latestReady: LatestPositionSnapshotResponse = {
  enabled: true,
  configured: true,
  continuityReady: true,
  continuityReason: null,
  latestIsCurrent: true,
  latestAccountBindingMatches: true,
  latestPublicationAnchorMatches: true,
  latest: snapshot,
  tradingActionPerformed: false,
};

const latestWithoutSnapshot: LatestPositionSnapshotResponse = {
  ...latestReady,
  latestIsCurrent: false,
  latestAccountBindingMatches: null,
  latestPublicationAnchorMatches: null,
  latest: null,
};

const preview: PositionSnapshotPreviewResponse = {
  artifactId: 31,
  artifactKey: 'artifact-31',
  previewKey: 'p'.repeat(64),
  expiresAt: '2026-07-30T13:10:00Z',
  confirmAllowed: true,
  blockingReasons: [],
  warnings: ['filtered_zero_quantity_rows:2'],
  observation,
  evidenceWritten: false,
  tradingActionPerformed: false,
};

const continuityNoSnapshot: PositionSnapshotContinuityAssessment = {
  policyVersion: 'position-snapshot-continuity/1.0',
  status: 'no_snapshot',
  readyForEpisodeBuild: false,
  fenceKey: null,
  accountKey: 'moomoo-margin-3705',
  snapshotId: null,
  snapshotKey: null,
  anchorPublicationId: null,
  anchorPublicationKey: null,
  targetPublicationId: null,
  targetPublicationKey: null,
  targetCanonicalSetId: null,
  targetCanonicalSetSha256: null,
  boundaryAt: null,
  guardStartedAt: null,
  guardCompletedAt: null,
  publicationIds: [],
  sourceBatchIds: [],
  counts: {
    snapshotMemberCount: 0,
    chainPublicationCount: 0,
    chainBatchCount: 0,
    targetOrderCount: 0,
    targetFillCount: 0,
    guardFillCount: 0,
    preBoundaryFillCount: 0,
    postBoundaryFillCount: 0,
    aggregateChangedPreBoundaryCount: 0,
    straddlingOrderCount: 0,
    straddlingGroupCount: 0,
    unbackedPostBoundaryCount: 0,
    optionIdentityMismatchCount: 0,
  },
  reasons: [{
    code: 'no_confirmed_snapshot',
    message: 'no confirmed position snapshot',
    entityKind: null,
    entityIds: [],
    count: 1,
  }],
  evidenceWritten: false,
  tradingActionPerformed: false,
};

const continuityAwaiting: PositionSnapshotContinuityAssessment = {
  ...continuityNoSnapshot,
  status: 'awaiting_refresh',
  snapshotId: 17,
  snapshotKey: snapshot.key,
  anchorPublicationId: 31,
  anchorPublicationKey: 'a'.repeat(64),
  boundaryAt: observation.operationCompletedAt,
  guardStartedAt: observation.observedFrom,
  guardCompletedAt: observation.operationCompletedAt,
  counts: { ...continuityNoSnapshot.counts, snapshotMemberCount: 1 },
  reasons: [{
    code: 'no_later_publication',
    message: 'a later publication is required',
    entityKind: null,
    entityIds: [],
    count: 1,
  }],
};

const continuityReady: PositionSnapshotContinuityAssessment = {
  ...continuityAwaiting,
  status: 'ready',
  readyForEpisodeBuild: true,
  fenceKey: 'f'.repeat(64),
  targetPublicationId: 32,
  targetPublicationKey: 'e'.repeat(64),
  targetCanonicalSetId: 9,
  targetCanonicalSetSha256: 'd'.repeat(64),
  publicationIds: [32],
  sourceBatchIds: [41],
  counts: {
    ...continuityAwaiting.counts,
    chainPublicationCount: 1,
    chainBatchCount: 1,
    targetOrderCount: 2,
    targetFillCount: 2,
    postBoundaryFillCount: 2,
  },
  reasons: [],
};

const futureEpisodePreview: FuturePositionEpisodePreviewResponse = {
  projectionName: 'position_snapshot_fenced_canonical_projection',
  projectionVersion: '1.0.0',
  continuityPolicyVersion: continuityReady.policyVersion,
  accountKey: continuityReady.accountKey,
  scope: 'moomoo_live_us_options',
  fenceKey: continuityReady.fenceKey as string,
  snapshotId: continuityReady.snapshotId as number,
  snapshotKey: continuityReady.snapshotKey as string,
  targetPublicationId: continuityReady.targetPublicationId as number,
  targetPublicationKey: continuityReady.targetPublicationKey as string,
  targetCanonicalSetId: continuityReady.targetCanonicalSetId as number,
  targetCanonicalSetSha256: continuityReady.targetCanonicalSetSha256 as string,
  canonicalSourceBatchIds: [41],
  publicationSourceBatchIds: [41],
  boundaryAt: continuityReady.boundaryAt as string,
  sourceCutoffAt: '2026-07-31T20:00:00Z',
  builderName: 'canonical_position_episode_builder',
  builderVersion: '1.0.0',
  builderConfigSha256: '1'.repeat(64),
  evidenceSetSha256: '2'.repeat(64),
  plannedBuildKey: '3'.repeat(64),
  openingBoundaryPolicy: 'complete_snapshot',
  maxMultiplierProofResidual: '0',
  sourceKnownFeeTotal: '1.20',
  accountedKnownFeeTotal: '1.20',
  retainedExecutionGroupFeeTotal: '0',
  feeConservationByCurrency: { USD: { sourceKnown: '1.20', accounted: '1.20' } },
  feeConserved: true,
  headlineRealizedPnlGross: '50',
  headlineTotalFee: '1.20',
  headlineRealizedPnlNet: '48.80',
  counts: {
    canonicalMemberCount: 5,
    openingPositionCount: 1,
    openingContractCount: '2.0000000000',
    postBoundaryOrderEventCount: 1,
    postBoundaryFillEventCount: 2,
    supportingOrderCount: 1,
    excludedNonOptionEventCount: 0,
    executionGroupCount: 0,
    sourceEventCount: 3,
    plannedPositionEpisodeCount: 2,
    plannedOpenEpisodeCount: 1,
    plannedClosedEpisodeCount: 1,
    leftCensoredEpisodeCount: 1,
    rightCensoredEpisodeCount: 1,
    headlineEpisodeCount: 1,
    headlineExcludedEpisodeCount: 1,
    groupFeeAffectedEpisodeCount: 0,
  },
  warnings: ['opening_positions_remain_left_censored'],
  previewOnly: true,
  businessDataWritten: false,
  episodeBuildWritten: false,
  activationChanged: false,
  evidenceWritten: false,
  tradingActionPerformed: false,
};

const futureBuildConfirmed: FuturePositionEpisodeBuildConfirmResponse = {
  dataState: 'ready',
  duplicate: false,
  build: {
    id: 77,
    buildKey: futureEpisodePreview.plannedBuildKey,
    builderName: 'canonical_position_episode_builder',
    builderVersion: '1.0.0',
    status: 'partial',
    sourceBatchIds: [41],
    sourceKind: 'position_snapshot_fenced_canonical',
    canonicalSetId: 9,
    canonicalSetSha256: 'd'.repeat(64),
    sourceWindowStart: '2026-07-30T13:00:03Z',
    sourceCutoffAt: '2026-07-31T20:00:00Z',
    positionEpisodeCount: 2,
    unresolvedEvidenceCount: 0,
    completenessScore: '0.95',
    openingBoundaryPolicy: 'complete_snapshot',
    assumedFlatUnverified: false,
    executionGroupCount: 0,
    groupFeeAffectedEpisodeCount: 0,
    retainedExecutionGroupFeeTotal: '0',
    feeConservationByCurrency: {},
    legFeeAttributionComplete: true,
    partialReasons: ['left_censored_episodes'],
    recordedAt: '2026-07-31T20:05:00Z',
  },
  reconciliation: {
    status: 'passed',
    scope: 'window',
    partialWindow: true,
    windowStart: '2026-07-30T13:00:03Z',
    windowEnd: '2026-07-31T20:00:00Z',
    matchedOrderCount: 1,
    totalOrderCount: 1,
  },
  summary: {
    totalEpisodeCount: 2,
    openEpisodeCount: 1,
    closedEpisodeCount: 1,
    boundaryUnverifiedEpisodeCount: 0,
    leftCensoredEpisodeCount: 1,
    incompleteEpisodeCount: 0,
    aggregateOnlyEpisodeCount: 0,
    groupFeeAffectedEpisodeCount: 0,
    headlinePnl: {
      eligibleClosedCount: 1,
      excludedEpisodeCount: 1,
      eligibilityRule: 'closed_net_known_boundary_verified_not_left_censored_complete',
      exclusionCounts: { leftCensored: 1 },
      realizedPnlGross: '50',
      totalFee: '1.20',
      realizedPnlNet: '48.80',
    },
  },
  snapshotFence: {
    sourceKind: 'position_snapshot_fenced_canonical',
    snapshotId: 17,
    snapshotKey: 'c'.repeat(64),
    fenceKey: 'f'.repeat(64),
    continuityPolicyVersion: 'position-snapshot-continuity/1.0',
    targetPublicationId: 32,
    targetPublicationKey: 'e'.repeat(64),
    targetCanonicalSetId: 9,
    targetCanonicalSetSha256: 'd'.repeat(64),
    boundaryAt: '2026-07-30T13:00:03Z',
    sourceCutoffAt: '2026-07-31T20:00:00Z',
    projectionName: 'position_snapshot_fenced_canonical_projection',
    projectionVersion: '1.0.0',
    linkKey: '4'.repeat(64),
  },
  message: 'future position episode build appended: 2 episodes. The default position review remains unchanged; use the explicit build ID to inspect this result.',
  activationChanged: false,
  tradingActionPerformed: false,
};

describe('CurrentPositionsSnapshotCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchLatestMock.mockResolvedValue(latestReady);
    fetchContinuityMock.mockResolvedValue(continuityAwaiting);
    fetchFuturePreviewMock.mockResolvedValue(futureEpisodePreview);
    previewMock.mockResolvedValue(preview);
    confirmMock.mockResolvedValue({
      snapshot,
      duplicate: false,
      tradingActionPerformed: false,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads a compact latest snapshot and keeps current evidence separate from history', async () => {
    render(<CurrentPositionsSnapshotCard />);

    expect(await screen.findByText('当前快照 · 已确认')).toBeInTheDocument();
    expect(screen.getByText('当前时点 ≠ 历史期初。')).toBeInTheDocument();
    const summary = screen.getByLabelText('最近已确认仓位快照摘要');
    expect(within(summary).getByText('1 行有效期权持仓')).toBeInTheDocument();
    expect(within(summary).getByText('2')).toBeInTheDocument();
    expect(within(summary).getByText('采集完成距评估 1 分钟')).toBeInTheDocument();
    expect(within(summary).getByText(/账户与证据锚点一致/)).toBeInTheDocument();
    expect(screen.getByText(/券商未提供 as-of 时/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('查看采集与仓位明细'));
    expect(screen.getByRole('table', { name: '当前仓位明细' })).toBeInTheDocument();
    expect(screen.getByText('US.NVDA260918C00150000')).toBeInTheDocument();
    expect(screen.getAllByText(/券商成本上下文/).length).toBeGreaterThan(0);
  });

  it('explains that a confirmed snapshot still needs a later evidence refresh', async () => {
    const onOpenEvidence = vi.fn();
    fetchContinuityMock.mockResolvedValue(continuityAwaiting);

    render(<CurrentPositionsSnapshotCard onOpenEvidence={onOpenEvidence} />);

    const fence = await screen.findByRole('region', { name: '未来 Episode 连续性门禁' });
    expect(within(fence).getByText('等待后续刷新')).toBeInTheDocument();
    expect(within(fence).getByText(/即使已经过 30 分钟/)).toBeInTheDocument();
    expect(within(fence).getByText(/证据写入 0 · 交易动作 0/)).toBeInTheDocument();
    expect(fetchFuturePreviewMock).not.toHaveBeenCalled();
    fireEvent.click(within(fence).getByRole('button', { name: '前往交易证据' }));
    expect(onOpenEvidence).toHaveBeenCalledTimes(1);
  });

  it('labels a proved continuity fence as zero-write and not yet built', async () => {
    fetchContinuityMock.mockResolvedValue(continuityReady);

    render(<CurrentPositionsSnapshotCard />);

    const fence = await screen.findByRole('region', { name: '未来 Episode 连续性门禁' });
    expect(within(fence).getByText('连续性已证明')).toBeInTheDocument();
    expect(within(fence).getByText(/尚未生成、激活或改写任何 Episode/)).toBeInTheDocument();
    expect(within(fence).getByText(/Canonical #9/)).toBeInTheDocument();
    await waitFor(() => expect(fetchFuturePreviewMock).toHaveBeenCalledTimes(1));
    expect(fetchFuturePreviewMock).toHaveBeenCalledWith('f'.repeat(64));
    const futurePreview = await screen.findByRole('region', { name: '未来回合预览' });
    expect(within(futurePreview).getByText(/预计 2 个回合：1 已归零 \/ 1 未归零/)).toBeInTheDocument();
    expect(within(futurePreview).getByText(/边界前开仓成本与费用未知/)).toBeInTheDocument();
    expect(within(futurePreview).getByText(/业务写入 0 · Episode 构建 0/)).toBeInTheDocument();
  });

  it('keeps a zero-event future preview valid and explicit', async () => {
    fetchContinuityMock.mockResolvedValue(continuityReady);
    fetchFuturePreviewMock.mockResolvedValue({
      ...futureEpisodePreview,
      counts: {
        ...futureEpisodePreview.counts,
        postBoundaryOrderEventCount: 0,
        postBoundaryFillEventCount: 0,
        sourceEventCount: 0,
      },
      warnings: ['no_post_boundary_execution_events'],
    });

    render(<CurrentPositionsSnapshotCard />);

    const futurePreview = await screen.findByRole('region', { name: '未来回合预览' });
    expect(await within(futurePreview).findByText(/边界后暂无执行事件；预览仍有效/)).toBeInTheDocument();
    expect(within(futurePreview).getByText(/0 orders · 0 fills/)).toBeInTheDocument();
  });

  it('removes a future preview as soon as readiness becomes non-ready', async () => {
    fetchContinuityMock
      .mockResolvedValueOnce(continuityReady)
      .mockResolvedValueOnce(continuityAwaiting);

    render(<CurrentPositionsSnapshotCard />);
    await screen.findByText(/预计 2 个回合：1 已归零 \/ 1 未归零/);

    fireEvent.focus(window);

    expect(await screen.findByText('等待后续刷新')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '未来回合预览' })).not.toBeInTheDocument();
  });

  it('auto-rechecks and hides a preview whose echoed identity changed', async () => {
    fetchContinuityMock
      .mockResolvedValueOnce(continuityReady)
      .mockResolvedValueOnce(continuityAwaiting);
    fetchFuturePreviewMock.mockResolvedValue({
      ...futureEpisodePreview,
      targetCanonicalSetId: 10,
    });

    render(<CurrentPositionsSnapshotCard />);

    await waitFor(() => expect(fetchContinuityMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('等待后续刷新')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '未来回合预览' })).not.toBeInTheDocument();
  });

  it('ignores an old preview response after the continuity fence changes', async () => {
    let resolveOldPreview!: (value: FuturePositionEpisodePreviewResponse) => void;
    const oldPreviewRequest = new Promise<FuturePositionEpisodePreviewResponse>((resolve) => {
      resolveOldPreview = resolve;
    });
    const nextReadiness: PositionSnapshotContinuityAssessment = {
      ...continuityReady,
      fenceKey: '8'.repeat(64),
      targetPublicationId: 33,
      targetPublicationKey: '7'.repeat(64),
      targetCanonicalSetId: 10,
      targetCanonicalSetSha256: '6'.repeat(64),
    };
    const nextPreview: FuturePositionEpisodePreviewResponse = {
      ...futureEpisodePreview,
      fenceKey: nextReadiness.fenceKey as string,
      targetPublicationId: nextReadiness.targetPublicationId as number,
      targetPublicationKey: nextReadiness.targetPublicationKey as string,
      targetCanonicalSetId: nextReadiness.targetCanonicalSetId as number,
      targetCanonicalSetSha256: nextReadiness.targetCanonicalSetSha256 as string,
    };
    fetchContinuityMock
      .mockResolvedValueOnce(continuityReady)
      .mockResolvedValueOnce(nextReadiness);
    fetchFuturePreviewMock
      .mockImplementationOnce(() => oldPreviewRequest)
      .mockResolvedValueOnce(nextPreview);

    render(<CurrentPositionsSnapshotCard />);
    await waitFor(() => expect(fetchFuturePreviewMock).toHaveBeenCalledTimes(1));

    fireEvent.focus(window);

    await waitFor(() => expect(fetchFuturePreviewMock).toHaveBeenCalledTimes(2));
    const currentPreview = await screen.findByRole('region', { name: '未来回合预览' });
    expect(within(currentPreview).getByText(/快照 #17 → Canonical #10/)).toBeInTheDocument();

    await act(async () => {
      resolveOldPreview(futureEpisodePreview);
      await Promise.resolve();
    });

    expect(within(currentPreview).getByText(/快照 #17 → Canonical #10/)).toBeInTheDocument();
    expect(within(currentPreview).queryByText(/快照 #17 → Canonical #9/)).not.toBeInTheDocument();
  });

  it('fails closed when latest and assessed snapshot ids differ, then recovers after reloading both', async () => {
    const newerSnapshot: ConfirmedPositionSnapshot = {
      ...snapshot,
      id: 18,
      key: 'e'.repeat(64),
    };
    fetchLatestMock.mockResolvedValue({
      ...latestReady,
      latest: newerSnapshot,
    });
    fetchContinuityMock
      .mockResolvedValueOnce(continuityReady)
      .mockResolvedValueOnce({
        ...continuityReady,
        snapshotId: newerSnapshot.id,
        snapshotKey: newerSnapshot.key,
      });
    fetchFuturePreviewMock.mockResolvedValue({
      ...futureEpisodePreview,
      snapshotId: newerSnapshot.id,
      snapshotKey: newerSnapshot.key,
    });

    render(<CurrentPositionsSnapshotCard />);

    const fence = await screen.findByRole('region', { name: '未来 Episode 连续性门禁' });
    expect(within(fence).getByText('状态已变化，请重新核对')).toBeInTheDocument();
    expect(within(fence).getByText(/当前最新快照为 #18，连续性评估仍针对 #17/)).toBeInTheDocument();
    expect(within(fence).queryByText('连续性已证明')).not.toBeInTheDocument();
    expect(within(fence).queryByText(/Canonical #9/)).not.toBeInTheDocument();
    expect(within(fence).queryByText(/^Fence /)).not.toBeInTheDocument();
    expect(fetchFuturePreviewMock).not.toHaveBeenCalled();

    fireEvent.click(within(fence).getByRole('button', { name: '重新核对状态' }));

    await waitFor(() => expect(fetchLatestMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(fetchContinuityMock).toHaveBeenCalledTimes(2));
    expect(await within(fence).findByText('连续性已证明')).toBeInTheDocument();
    expect(within(fence).queryByText('状态已变化，请重新核对')).not.toBeInTheDocument();
    expect(within(fence).getByText(/Canonical #9/)).toBeInTheDocument();
  });

  it('fails closed when the snapshot id matches but its immutable key differs', async () => {
    fetchLatestMock.mockResolvedValue({
      ...latestReady,
      latest: {
        ...snapshot,
        key: '9'.repeat(64),
      },
    });
    fetchContinuityMock.mockResolvedValue(continuityReady);

    render(<CurrentPositionsSnapshotCard />);

    const fence = await screen.findByRole('region', { name: '未来 Episode 连续性门禁' });
    expect(within(fence).getByText('状态已变化，请重新核对')).toBeInTheDocument();
    expect(within(fence).getByText(/不可变指纹与连续性评估不一致/)).toBeInTheDocument();
    expect(within(fence).queryByText('连续性已证明')).not.toBeInTheDocument();
    expect(within(fence).queryByText(/Canonical #9/)).not.toBeInTheDocument();
    expect(within(fence).queryByText(/^Fence /)).not.toBeInTheDocument();
    expect(fetchFuturePreviewMock).not.toHaveBeenCalled();
  });

  it('blocks ready when the latest response has no snapshot to cross-check', async () => {
    fetchLatestMock.mockResolvedValue(latestWithoutSnapshot);
    fetchContinuityMock.mockResolvedValue(continuityReady);

    render(<CurrentPositionsSnapshotCard />);

    const fence = await screen.findByRole('region', { name: '未来 Episode 连续性门禁' });
    expect(within(fence).getByText('状态无法交叉核对')).toBeInTheDocument();
    expect(within(fence).getByText(/最新快照缺失或读取失败/)).toBeInTheDocument();
    expect(within(fence).queryByText('连续性已证明')).not.toBeInTheDocument();
    expect(within(fence).queryByText(/Canonical #9/)).not.toBeInTheDocument();
    expect(within(fence).queryByText(/^Fence /)).not.toBeInTheDocument();
    expect(fetchFuturePreviewMock).not.toHaveBeenCalled();
  });

  it('blocks ready when loading the latest snapshot fails', async () => {
    fetchLatestMock.mockRejectedValue(new Error('latest snapshot unavailable'));
    fetchContinuityMock.mockResolvedValue(continuityReady);

    render(<CurrentPositionsSnapshotCard />);

    const fence = await screen.findByRole('region', { name: '未来 Episode 连续性门禁' });
    expect(within(fence).getByText('状态无法交叉核对')).toBeInTheDocument();
    expect(within(fence).queryByText('连续性已证明')).not.toBeInTheDocument();
    expect(within(fence).queryByText(/Canonical #9/)).not.toBeInTheDocument();
    expect(within(fence).queryByText(/^Fence /)).not.toBeInTheDocument();
    expect(within(fence).getByRole('button', { name: '重新核对状态' })).toBeInTheDocument();
    expect(fetchFuturePreviewMock).not.toHaveBeenCalled();
  });

  it('fails closed when a mounted current snapshot crosses its freshness deadline', async () => {
    vi.useFakeTimers();
    fetchLatestMock.mockResolvedValueOnce({
      ...latestReady,
      latest: {
        ...snapshot,
        freshness: {
          ...snapshot.freshness,
          ageSeconds: 1799,
        },
      },
    }).mockImplementationOnce(() => new Promise(() => {}));

    render(<CurrentPositionsSnapshotCard />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText('当前快照 · 已确认')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1_200);
    });

    expect(screen.getAllByText('历史快照 · 已过期').length).toBeGreaterThan(0);
    expect(screen.queryByText('当前快照 · 已确认')).not.toBeInTheDocument();
  });

  it('does not keep an old readiness result visible when a resume recheck fails', async () => {
    fetchContinuityMock
      .mockResolvedValueOnce(continuityAwaiting)
      .mockRejectedValueOnce(new Error('continuity read failed'));

    render(<CurrentPositionsSnapshotCard />);
    expect(await screen.findByText('等待后续刷新')).toBeInTheDocument();

    fireEvent.focus(window);

    expect(await screen.findByText(/continuity read failed/)).toBeInTheDocument();
    expect(screen.queryByText('等待后续刷新')).not.toBeInTheDocument();
  });

  it('distinguishes a complete empty snapshot from a failed read', async () => {
    fetchLatestMock.mockResolvedValue({
      ...latestReady,
      latest: {
        ...snapshot,
        observation: {
          ...observation,
          contractSpecStatus: 'not_applicable',
          contentState: 'empty',
          positionCount: 0,
          totalContracts: '0',
          positions: [],
        },
      },
    });
    render(<CurrentPositionsSnapshotCard />);

    expect(await screen.findByText('当前快照 · 完整空仓')).toBeInTheDocument();
    expect(screen.getByText('完整空仓')).toBeInTheDocument();
    expect(screen.queryByText(/读取失败或不完整/)).not.toBeInTheDocument();
  });

  it('does not query positions when the feature is unconfigured', async () => {
    fetchLatestMock.mockResolvedValue({
      enabled: true,
      configured: false,
      continuityReady: false,
      continuityReason: 'missing account binding',
      latestIsCurrent: false,
      latestAccountBindingMatches: null,
      latestPublicationAnchorMatches: null,
      latest: null,
      tradingActionPerformed: false,
    });
    render(<CurrentPositionsSnapshotCard />);

    expect(await screen.findByText('当前仓位只读检查尚未配置')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '只读检查当前持仓' })).toBeDisabled();
    expect(previewMock).not.toHaveBeenCalled();
  });

  it('shows a stable preview with filtering, warnings, positions, and hashes', async () => {
    fetchLatestMock.mockResolvedValue(latestWithoutSnapshot);
    previewMock.mockResolvedValue({
      ...preview,
      observation,
    });
    render(<CurrentPositionsSnapshotCard />);
    await screen.findByText('尚无已确认快照');

    fireEvent.click(screen.getByRole('button', { name: '只读检查当前持仓' }));
    const previewRegion = await screen.findByRole('region', { name: '当前仓位检查预览' });
    expect(within(previewRegion).getByText('两次读取稳定')).toBeInTheDocument();
    expect(within(previewRegion).getByText('读取到 1 行有效期权持仓')).toBeInTheDocument();
    expect(within(previewRegion).getByText('过滤零数量缓存行')).toBeInTheDocument();
    expect(within(previewRegion).getByText(/已过滤 2 行零数量缓存记录/)).toBeInTheDocument();
    expect(within(previewRegion).getByText('US.NVDA260918C00150000')).toBeInTheDocument();
    expect(within(previewRegion).getByText('证据指纹')).toBeInTheDocument();
  });

  it('labels a stable and complete zero-row preview as an empty snapshot candidate', async () => {
    fetchLatestMock.mockResolvedValue(latestWithoutSnapshot);
    previewMock.mockResolvedValue({
      ...preview,
      observation: {
        ...observation,
        contractSpecStatus: 'not_applicable',
        contentState: 'empty',
        positionCount: 0,
        totalContracts: '0',
        positions: [],
      },
    });
    render(<CurrentPositionsSnapshotCard />);
    await screen.findByText('尚无已确认快照');
    fireEvent.click(screen.getByRole('button', { name: '只读检查当前持仓' }));

    expect(await screen.findByText('完整空仓快照候选')).toBeInTheDocument();
    expect(screen.getByText(/两次读取均完成且稳定/)).toBeInTheDocument();
    expect(screen.queryByText(/读取失败或不完整，不是空仓/)).not.toBeInTheDocument();
  });

  it('requires the future-only acknowledgement, confirms, and refreshes latest', async () => {
    fetchLatestMock
      .mockResolvedValueOnce(latestWithoutSnapshot)
      .mockResolvedValueOnce(latestReady);
    render(<CurrentPositionsSnapshotCard />);
    await screen.findByText('尚无已确认快照');
    fireEvent.click(screen.getByRole('button', { name: '只读检查当前持仓' }));
    await screen.findByRole('region', { name: '当前仓位检查预览' });

    const confirm = screen.getByRole('button', { name: '确认并保存当前仓位证据' });
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', {
      name: /仅作为面向未来的当前仓位证据，不能倒推历史期初/,
    }));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(31, 'p'.repeat(64), true));
    await waitFor(() => expect(fetchLatestMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(fetchContinuityMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('当前仓位证据已保存，可作为后续证据连续性的起点。')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '当前仓位检查预览' })).not.toBeInTheDocument();
  });

  it('allows preview without continuity but blocks confirmation', async () => {
    const onOpenEvidence = vi.fn();
    fetchLatestMock.mockResolvedValue({
      ...latestWithoutSnapshot,
      continuityReady: false,
      continuityReason: '尚未确认 OpenD 刷新',
    });
    render(<CurrentPositionsSnapshotCard onOpenEvidence={onOpenEvidence} />);
    await screen.findByText('尚无账户连续性锚点');

    fireEvent.click(screen.getByRole('button', { name: '只读检查当前持仓' }));
    const previewRegion = await screen.findByRole('region', { name: '当前仓位检查预览' });
    expect(within(previewRegion).getByText('可以检查，但暂不能确认')).toBeInTheDocument();
    expect(within(previewRegion).getByRole('checkbox', {
      name: /仅作为面向未来的当前仓位证据/,
    })).toBeDisabled();
    expect(within(previewRegion).getByRole('button', {
      name: '确认并保存当前仓位证据',
    })).toBeDisabled();
    fireEvent.click(within(previewRegion).getByRole('button', { name: '前往交易证据' }));
    expect(onOpenEvidence).toHaveBeenCalledTimes(1);
  });

  it('marks a stale confirmed snapshot as history instead of current holdings', async () => {
    fetchLatestMock.mockResolvedValue({
      ...latestReady,
      latestIsCurrent: false,
      latest: {
        ...snapshot,
        isCurrent: false,
        freshness: {
          ...snapshot.freshness,
          status: 'stale',
          ageSeconds: 7200,
        },
      },
    });
    render(<CurrentPositionsSnapshotCard />);

    expect((await screen.findAllByText('历史快照 · 已过期')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/已超过“当前”门槛（30 分钟）/)).toBeInTheDocument();
    expect(screen.getByText(/当前性未通过，原因见上方提示/)).toBeInTheDocument();
    expect(screen.queryByText('当前快照 · 已确认')).not.toBeInTheDocument();
  });

  it('makes an old-account snapshot fail-visible while allowing a new preview', async () => {
    fetchLatestMock.mockResolvedValue({
      ...latestReady,
      continuityReason: 'account_binding_mismatch',
      latestIsCurrent: false,
      latestAccountBindingMatches: false,
      latest: {
        ...snapshot,
        isCurrent: false,
        accountBindingStatus: 'mismatch',
      },
    });
    render(<CurrentPositionsSnapshotCard />);

    expect(await screen.findByText('历史快照 · 账户不匹配')).toBeInTheDocument();
    expect(screen.getByText(/属于另一账户绑定/)).toBeInTheDocument();
    const previewButton = screen.getByRole('button', { name: '只读检查当前持仓' });
    expect(previewButton).toBeEnabled();
    fireEvent.click(previewButton);
    expect(await screen.findByRole('region', { name: '当前仓位检查预览' })).toBeInTheDocument();
  });

  it('labels a snapshot tied to a superseded refresh publication as history', async () => {
    fetchLatestMock.mockResolvedValue({
      ...latestReady,
      latestIsCurrent: false,
      latestPublicationAnchorMatches: false,
      latest: {
        ...snapshot,
        isCurrent: false,
        publicationAnchorStatus: 'superseded',
      },
    });
    render(<CurrentPositionsSnapshotCard />);

    expect(await screen.findByText('历史快照 · 账户证据锚点已更新')).toBeInTheDocument();
    expect(screen.getByText(/已不是当前最新锚点/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '只读检查当前持仓' })).toBeEnabled();
  });

  it('shows excessive future clock skew as a time error, not an old snapshot', async () => {
    fetchLatestMock.mockResolvedValue({
      ...latestReady,
      latestIsCurrent: false,
      latest: {
        ...snapshot,
        isCurrent: false,
        freshness: {
          ...snapshot.freshness,
          status: 'stale',
          ageSeconds: 0,
          futureSkewSeconds: 360,
        },
      },
    });
    render(<CurrentPositionsSnapshotCard />);

    expect(await screen.findByText('历史快照 · 本地时间异常')).toBeInTheDocument();
    expect(screen.getByText(/比本次服务器评估时间晚 6 分钟/)).toBeInTheDocument();
    expect(screen.queryByText(/超过“当前”门槛/)).not.toBeInTheDocument();
  });

  it('never presents an incomplete retrieval as an empty account', async () => {
    fetchLatestMock.mockResolvedValue(latestWithoutSnapshot);
    previewMock.mockResolvedValue({
      ...preview,
      confirmAllowed: false,
      blockingReasons: ['position_retrieval_incomplete'],
      observation: {
        ...observation,
        stable: false,
        stability: {
          stable: false,
          comparisonBasis: 'instrument_position_side_signed_quantity_contracts',
          addedSymbols: ['US.NVDA260918C00150000'],
          removedSymbols: [],
          changedSymbols: [],
        },
        retrievalComplete: false,
        positionSnapshotComplete: false,
        analysisReady: false,
        contentState: 'failed',
        positionCount: 0,
        totalContracts: '0',
        futureAnchorCandidate: false,
        positions: [],
      },
    });
    render(<CurrentPositionsSnapshotCard />);
    await screen.findByText('尚无已确认快照');
    fireEvent.click(screen.getByRole('button', { name: '只读检查当前持仓' }));

    expect(await screen.findByText('读取失败或不完整，不是空仓')).toBeInTheDocument();
    expect(screen.queryByText('完整空仓快照候选')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认并保存当前仓位证据' })).toBeDisabled();
    expect(screen.getByText(/当前仓位读取未完整/)).toBeInTheDocument();
    expect(screen.getByText(/新增 1 · 减少 0/)).toBeInTheDocument();
  });

  it('renders every confirmation blocker as actionable Chinese guidance', async () => {
    fetchLatestMock.mockResolvedValue(latestWithoutSnapshot);
    previewMock.mockResolvedValue({
      ...preview,
      confirmAllowed: false,
      blockingReasons: [
        'account_continuity_not_established',
        'account_binding_mismatch',
        'position_retrieval_incomplete',
        'position_boundary_unstable',
        'position_snapshot_incomplete',
        'position_validation_issues',
      ],
      observation: {
        ...observation,
        stable: false,
        stability: {
          ...observation.stability,
          stable: false,
          changedSymbols: ['US.NVDA260918C00150000'],
        },
        retrievalComplete: false,
        positionSnapshotComplete: false,
        analysisReady: false,
        contentState: 'incomplete',
        futureAnchorCandidate: false,
      },
    });
    render(<CurrentPositionsSnapshotCard />);
    await screen.findByText('尚无已确认快照');
    fireEvent.click(screen.getByRole('button', { name: '只读检查当前持仓' }));

    const blockers = await screen.findByRole('list', { name: '当前仓位确认阻断原因' });
    expect(within(blockers).getByText(/尚未建立同一账户/)).toBeInTheDocument();
    expect(within(blockers).getByText(/当前 OpenD 账户与最近确认/)).toBeInTheDocument();
    expect(within(blockers).getByText(/仓位读取未完整/)).toBeInTheDocument();
    expect(within(blockers).getByText(/两次读取的仓位不一致/)).toBeInTheDocument();
    expect(within(blockers).getByText(/快照未通过完整性检查/)).toBeInTheDocument();
    expect(within(blockers).getByText(/字段或合约规格验证问题/)).toBeInTheDocument();
    expect(within(blockers).queryByText('account_binding_mismatch')).not.toBeInTheDocument();
  });

  it('keeps request failures visible and does not fabricate an empty snapshot', async () => {
    fetchLatestMock.mockResolvedValue(latestWithoutSnapshot);
    previewMock.mockRejectedValue(new Error('OpenD position query failed'));
    render(<CurrentPositionsSnapshotCard />);
    await screen.findByText('尚无已确认快照');
    fireEvent.click(screen.getByRole('button', { name: '只读检查当前持仓' }));

    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('完整空仓快照候选')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '当前仓位检查预览' })).not.toBeInTheDocument();
    expect(screen.getByText(/OpenD position query failed/)).toBeInTheDocument();
  });

  it('hides the formal future-build confirm block while no future preview is displayed', async () => {
    render(<CurrentPositionsSnapshotCard />);

    expect(await screen.findByText('等待后续刷新')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '正式 Future Build 确认' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '写入正式 Future Build' })).not.toBeInTheDocument();
    expect(confirmFutureBuildMock).not.toHaveBeenCalled();
  });

  it('gates the formal build on the left-censored acceptance, stays disabled in flight, and reports 已构建≠已生效', async () => {
    fetchContinuityMock.mockResolvedValue(continuityReady);
    let resolveConfirm!: (value: FuturePositionEpisodeBuildConfirmResponse) => void;
    confirmFutureBuildMock.mockImplementation(() => new Promise((resolve) => {
      resolveConfirm = resolve;
    }));

    render(<CurrentPositionsSnapshotCard />);

    const confirmBlock = await screen.findByRole('region', { name: '正式 Future Build 确认' });
    const writeButton = within(confirmBlock).getByRole('button', { name: '写入正式 Future Build' });
    expect(writeButton).toBeDisabled();
    expect(within(confirmBlock).getByText('接受上方全部前提后才能写入。')).toBeInTheDocument();
    expect(within(confirmBlock).queryByRole('checkbox', {
      name: /接受组合单费用仅组级精确/,
    })).not.toBeInTheDocument();

    fireEvent.click(within(confirmBlock).getByRole('checkbox', {
      name: /接受 snapshot 继承仓位为 left-censored（无券商成本）/,
    }));
    expect(writeButton).toBeEnabled();
    fireEvent.click(writeButton);

    expect(await within(confirmBlock).findByRole('button', { name: '正在写入…' })).toBeDisabled();
    expect(confirmFutureBuildMock).toHaveBeenCalledTimes(1);
    expect(confirmFutureBuildMock).toHaveBeenCalledWith({
      expectedFenceKey: 'f'.repeat(64),
      expectedBuildKey: '3'.repeat(64),
      expectedEvidenceSetSha256: '2'.repeat(64),
      acceptLeftCensoredOpenings: true,
      acceptGroupFeeScope: false,
    });

    await act(async () => {
      resolveConfirm(futureBuildConfirmed);
      await Promise.resolve();
    });

    expect(await within(confirmBlock).findByText('正式 Future Build 已写入')).toBeInTheDocument();
    expect(within(confirmBlock).getByText(/Build #77/)).toBeInTheDocument();
    expect(within(confirmBlock).getByText(
      /已构建 ≠ 已生效：默认复盘视图不变（activation 尚未支持 future build）/,
    )).toBeInTheDocument();
    expect(within(confirmBlock).getByRole('button', { name: '写入正式 Future Build' })).toBeDisabled();
  });

  it('requires the group-fee acceptance whenever the plan has group-fee-affected episodes', async () => {
    fetchContinuityMock.mockResolvedValue(continuityReady);
    fetchFuturePreviewMock.mockResolvedValue({
      ...futureEpisodePreview,
      counts: { ...futureEpisodePreview.counts, groupFeeAffectedEpisodeCount: 1 },
      warnings: [
        'opening_positions_remain_left_censored',
        'execution_group_fee_retained_unallocated',
      ],
    });
    confirmFutureBuildMock.mockResolvedValue({ ...futureBuildConfirmed, duplicate: false });

    render(<CurrentPositionsSnapshotCard />);

    const confirmBlock = await screen.findByRole('region', { name: '正式 Future Build 确认' });
    const writeButton = within(confirmBlock).getByRole('button', { name: '写入正式 Future Build' });
    fireEvent.click(within(confirmBlock).getByRole('checkbox', {
      name: /接受 snapshot 继承仓位为 left-censored（无券商成本）/,
    }));
    expect(writeButton).toBeDisabled();
    fireEvent.click(within(confirmBlock).getByRole('checkbox', {
      name: /接受组合单费用仅组级精确/,
    }));
    expect(writeButton).toBeEnabled();
    fireEvent.click(writeButton);

    await waitFor(() => expect(confirmFutureBuildMock).toHaveBeenCalledWith({
      expectedFenceKey: 'f'.repeat(64),
      expectedBuildKey: '3'.repeat(64),
      expectedEvidenceSetSha256: '2'.repeat(64),
      acceptLeftCensoredOpenings: true,
      acceptGroupFeeScope: true,
    }));
  });

  it('labels an idempotent confirm replay as 已存在 without claiming activation', async () => {
    fetchContinuityMock.mockResolvedValue(continuityReady);
    confirmFutureBuildMock.mockResolvedValue({ ...futureBuildConfirmed, duplicate: true });

    render(<CurrentPositionsSnapshotCard />);

    const confirmBlock = await screen.findByRole('region', { name: '正式 Future Build 确认' });
    fireEvent.click(within(confirmBlock).getByRole('checkbox', {
      name: /接受 snapshot 继承仓位为 left-censored（无券商成本）/,
    }));
    fireEvent.click(within(confirmBlock).getByRole('button', { name: '写入正式 Future Build' }));

    expect(await within(confirmBlock).findByText('该 build 已存在（幂等重放）')).toBeInTheDocument();
    expect(within(confirmBlock).getByText(
      /已构建 ≠ 已生效：默认复盘视图不变（activation 尚未支持 future build）/,
    )).toBeInTheDocument();
    expect(within(confirmBlock).queryByText('正式 Future Build 已写入')).not.toBeInTheDocument();
  });

  it('clears the stale preview on a 409 confirm conflict and requires a fresh zero-write preview', async () => {
    fetchContinuityMock.mockResolvedValue(continuityReady);
    confirmFutureBuildMock.mockRejectedValue(Object.assign(new Error('conflict'), {
      response: {
        status: 409,
        data: { detail: 'future build plan changed; request a new preview' },
      },
    }));

    render(<CurrentPositionsSnapshotCard />);

    const confirmBlock = await screen.findByRole('region', { name: '正式 Future Build 确认' });
    fireEvent.click(within(confirmBlock).getByRole('checkbox', {
      name: /接受 snapshot 继承仓位为 left-censored（无券商成本）/,
    }));
    fireEvent.click(within(confirmBlock).getByRole('button', { name: '写入正式 Future Build' }));

    expect(await screen.findByText('正式 Future Build 未写入（409）')).toBeInTheDocument();
    expect(screen.getByText(/future build plan changed; request a new preview/)).toBeInTheDocument();
    expect(screen.getByText(/计划已变化或 fence 过期——请重新运行零写预览后再确认/)).toBeInTheDocument();
    expect(screen.queryByText(/预计 2 个回合：1 已归零 \/ 1 未归零/)).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '正式 Future Build 确认' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '写入正式 Future Build' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新运行零写预览' }));

    await waitFor(() => expect(fetchContinuityMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(fetchFuturePreviewMock).toHaveBeenCalledTimes(2));
    const refreshedBlock = await screen.findByRole('region', { name: '正式 Future Build 确认' });
    expect(within(refreshedBlock).getByRole('checkbox', {
      name: /接受 snapshot 继承仓位为 left-censored（无券商成本）/,
    })).not.toBeChecked();
    expect(within(refreshedBlock).getByRole('button', { name: '写入正式 Future Build' })).toBeDisabled();
  });
});
