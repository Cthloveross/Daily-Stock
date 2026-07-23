import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PositionEpisodesPanel from '../PositionEpisodesPanel';
import type {
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildResponse,
  PositionEpisodeDetailResponse,
  PositionEpisodeItem,
  PositionEpisodeListResponse,
} from '../../../types/journal';

const openAggregateEpisode: PositionEpisodeItem = {
  id: 41,
  episodeBuildId: 7,
  strategyEpisodeId: 17,
  episodeKey: 'position-41',
  lineageKey: 'lineage-41',
  strategyType: 'single_contract',
  instrument: {
    rawSymbol: 'NVDA260619C00150000',
    assetType: 'option',
    underlying: 'NVDA',
    expiry: '2026-06-19',
    strike: '150.0000000000',
    optionRight: 'C',
    contractMultiplier: '100.0000000000',
    currency: 'USD',
  },
  direction: 'long',
  lifecycleStatus: 'open',
  openedAt: '2026-06-20T14:31:00Z',
  closedAt: null,
  holdSeconds: null,
  openedQuantity: '3.0000000000',
  closedQuantity: '1.0000000000',
  remainingQuantity: '2.0000000000',
  averageEntryPrice: '2.5000000000',
  averageExitPrice: null,
  realizedPnlGross: null,
  totalFee: '0.2500000000',
  realizedPnlNet: null,
  quality: {
    completenessStatus: 'partial',
    completenessScore: '0.8000',
    constructionBasis: 'aggregate_orders',
    contractMultiplierBasis: 'occ',
    openingBoundaryPolicy: 'assumed_flat_unverified',
    assumedFlatUnverified: true,
    leftBoundaryVerified: false,
    isLeftCensored: true,
    isRightCensored: true,
    pnlSummaryEligible: false,
    pnlExclusionReasons: ['boundary_unverified', 'open'],
  },
};

const closedEpisode: PositionEpisodeItem = {
  ...openAggregateEpisode,
  id: 42,
  episodeKey: 'position-42',
  instrument: { ...openAggregateEpisode.instrument, rawSymbol: 'TSLA260619P00200000', underlying: 'TSLA' },
  lifecycleStatus: 'closed',
  closedAt: '2026-06-21T15:31:00Z',
  holdSeconds: 90000,
  openedQuantity: '1.0000000000',
  closedQuantity: '1.0000000000',
  remainingQuantity: '0.0000000000',
  realizedPnlGross: '125.0000000000',
  totalFee: '1.2500000000',
  realizedPnlNet: '123.7500000000',
  quality: {
    ...openAggregateEpisode.quality,
    constructionBasis: 'fills',
    isRightCensored: false,
    pnlExclusionReasons: ['boundary_unverified', 'assumed_flat_unverified'],
  },
};

const readyList: PositionEpisodeListResponse = {
  dataState: 'ready',
  build: {
    id: 7,
    buildKey: 'build-7',
    builderName: 'position_episode_builder',
    builderVersion: '1.1.0',
    status: 'succeeded',
    sourceBatchIds: [2],
    sourceKind: 'csv_batch',
    sourceCutoffAt: '2026-07-19T23:59:59Z',
    positionEpisodeCount: 2,
    unresolvedEvidenceCount: 0,
    completenessScore: '0.8000',
    openingBoundaryPolicy: 'assumed_flat_unverified',
    assumedFlatUnverified: true,
    partialReasons: ['opening_boundary_unverified'],
    recordedAt: '2026-07-21T10:00:00Z',
  },
  reconciliation: {
    status: 'passed',
    scope: 'partial_window',
    partialWindow: true,
    windowStart: '2026-06-19T23:59:59Z',
    windowEnd: '2026-07-19T23:59:59Z',
    matchedOrderCount: 1089,
    totalOrderCount: 3542,
  },
  summary: {
    totalEpisodeCount: 2,
    openEpisodeCount: 1,
    closedEpisodeCount: 1,
    boundaryUnverifiedEpisodeCount: 2,
    leftCensoredEpisodeCount: 1,
    incompleteEpisodeCount: 1,
    aggregateOnlyEpisodeCount: 1,
    headlinePnl: {
      eligibleClosedCount: 0,
      excludedEpisodeCount: 2,
      eligibilityRule: 'strict',
      exclusionCounts: { boundary_unverified: 2 },
      realizedPnlGross: null,
      totalFee: null,
      realizedPnlNet: null,
    },
    conditionalPnl: {
      basis: 'closed_known_cash_flows_under_opening_boundary_policy',
      openingBoundaryPolicy: 'assumed_flat_unverified',
      count: 1,
      realizedPnlGross: '125.0000000000',
      totalFee: '1.2500000000',
      realizedPnlNet: '123.7500000000',
      includedInHeadline: false,
    },
  },
  total: 2,
  page: 1,
  perPage: 50,
  items: [openAggregateEpisode, closedEpisode],
};

const canonicalPreview: CanonicalEpisodeBuildPlanResponse = {
  dataState: 'ready',
  canonicalSetId: 1,
  canonicalSetSha256: 'a'.repeat(64),
  buildKey: 'b'.repeat(64),
  sourceBatchIds: [2, 3],
  sourceWindowStart: '2026-01-01T00:00:00Z',
  sourceWindowEnd: '2026-07-19T23:59:59Z',
  sourceEventCount: 5974,
  aggregateOrderEventCount: 574,
  detailedFillEventCount: 5400,
  plannedPositionEpisodeCount: 1441,
  plannedOpenEpisodeCount: 4,
  plannedClosedEpisodeCount: 1437,
  sourceKnownFeeTotal: '151750.7500000000',
  allocatedKnownFeeTotal: '151750.7500000000',
  feeConserved: true,
  openingBoundaryPolicy: 'assumed_flat_unverified',
  requiresAssumedFlatAcceptance: true,
  defaultBuildId: 7,
  defaultPositionEpisodeCount: 1439,
  episodeCountDelta: 2,
  defaultWillChange: false,
  confirmAllowed: true,
  warnings: [],
};

const canonicalBuildResult: EpisodeBuildResponse = {
  dataState: 'ready',
  duplicate: false,
  build: {
    ...readyList.build!,
    id: 9,
    buildKey: canonicalPreview.buildKey!,
    sourceBatchIds: [2, 3],
    sourceKind: 'canonical_set',
    canonicalSetId: 1,
    canonicalSetSha256: canonicalPreview.canonicalSetSha256,
    positionEpisodeCount: 1441,
  },
  reconciliation: readyList.reconciliation!,
  summary: readyList.summary!,
  message: 'canonical episode build appended',
};

const detail: PositionEpisodeDetailResponse = {
  dataState: 'ready',
  build: readyList.build!,
  reconciliation: readyList.reconciliation!,
  item: openAggregateEpisode,
  matching: { method: 'signed_position_zero_crossing' },
  evidenceSummary: { allocationCount: 1 },
  completeness: { leftBoundaryVerified: false },
  provenance: { builderVersion: '1.1.0' },
  evidence: [{
    id: 61,
    evidenceKey: 'order-observation-61',
    evidenceKind: 'order',
    eventRole: 'open',
    allocationSequence: 0,
    evidenceTime: '2026-06-20T14:31:00Z',
    allocatedQuantity: '1.0000000000',
    allocatedFee: '0.2500000000',
    allocatedCashFlow: '-250.0000000000',
    allocationRatio: '1.0000000000',
    brokerOrderObservationId: 101,
    brokerFillObservationId: null,
    allocation: { timingPrecision: 'order_time_proxy' },
    provenance: { syntheticFill: false },
  }],
};

function controller(overrides: Record<string, unknown> = {}) {
  return {
    list: readyList,
    loading: false,
    error: null,
    reload: vi.fn(),
    detailById: {},
    detailLoadingId: null,
    detailError: null,
    loadDetail: vi.fn().mockResolvedValue(undefined),
    building: false,
    buildResult: null,
    buildAssumedFlat: vi.fn().mockResolvedValue(undefined),
    canonicalPreview,
    canonicalPreviewLoading: false,
    canonicalPreviewError: null,
    reloadCanonicalPreview: vi.fn(),
    canonicalBuilding: false,
    canonicalBuildResult: null,
    buildCanonical: vi.fn().mockResolvedValue(canonicalBuildResult),
    ...overrides,
  };
}

describe('PositionEpisodesPanel', () => {
  it('separates partial coverage, conditional PnL, and strict headline PnL', () => {
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={controller()}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    expect(screen.getByText('1,089 / 3,542')).toBeInTheDocument();
    expect(screen.getByText(/这不代表整批通过/)).toBeInTheDocument();
    expect(screen.getByText('按期初空仓假设的条件性结果')).toBeInTheDocument();
    expect(screen.getByText('不进入 Headline')).toBeInTheDocument();
    const conditionalCard = screen.getByLabelText('按期初空仓假设的条件性结果');
    expect(within(conditionalCard).getByText('$123.75')).toBeInTheDocument();

    const verifiedNetCard = screen.getByText('Verified Net').closest('div.rounded-ds-md');
    expect(verifiedNetCard).not.toBeNull();
    expect(within(verifiedNetCard as HTMLElement).getByText('—')).toBeInTheDocument();

    const openRow = screen.getByText('NVDA260619C00150000').closest('tr');
    expect(openRow).not.toBeNull();
    expect(within(openRow as HTMLElement).getByText('2')).toBeInTheDocument();
    expect(within(openRow as HTMLElement).getByText('—')).toBeInTheDocument();
  });

  it('loads structured evidence on row selection and closes the drawer with Escape', () => {
    const loadDetail = vi.fn().mockResolvedValue(undefined);
    const onOpenReview = vi.fn();
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={controller({ detailById: { 41: detail }, loadDetail })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
        onOpenReview={onOpenReview}
      />,
    );

    fireEvent.click(screen.getByText('NVDA260619C00150000'));
    expect(loadDetail).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Evidence chain')).toBeInTheDocument();
    expect(screen.getByText('订单时间代理 · 非逐笔成交时间')).toBeInTheDocument();
    expect(screen.getByText('order observation 101')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开完整复盘 · K 线与成交证据' }));
    expect(onOpenReview).toHaveBeenCalledWith(openAggregateEpisode);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('requires a second explicit confirmation before creating an assumed-flat build', () => {
    const buildAssumedFlat = vi.fn().mockResolvedValue(undefined);
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={controller({
          list: { dataState: 'not_built', total: 0, page: 1, perPage: 50, items: [] },
          buildAssumedFlat,
        })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '按未验证期初空仓构建' }));
    expect(buildAssumedFlat).not.toHaveBeenCalled();
    expect(screen.getByText(/把观察窗口开始前的持仓假设为 0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '接受假设并构建' }));
    expect(buildAssumedFlat).toHaveBeenCalledTimes(1);
  });

  it('requires explicit boundary acceptance before generating from the trusted fact set', () => {
    const buildCanonical = vi.fn().mockResolvedValue(canonicalBuildResult);
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={controller({ buildCanonical })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    expect(screen.getByText('5,974 条事件')).toBeInTheDocument();
    expect(screen.getByText('已通过')).toBeInTheDocument();
    expect(screen.getByText('+2 个回合')).toBeInTheDocument();
    expect(screen.getByText(/不会替换当前默认视图/)).toBeInTheDocument();

    const generate = screen.getByRole('button', { name: '生成仓位复盘构建' });
    expect(generate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /我接受未验证的期初空仓假设/ }));
    expect(generate).toBeEnabled();
    fireEvent.click(generate);
    expect(buildCanonical).toHaveBeenCalledWith(true);
  });

  it('opens a generated build explicitly and offers a return to the default view', () => {
    const onSelectBuild = vi.fn();
    const { rerender } = render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={controller({ canonicalBuildResult })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={onSelectBuild}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '查看这个构建' }));
    expect(onSelectBuild).toHaveBeenCalledWith(9);

    rerender(
      <PositionEpisodesPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controller({ canonicalBuildResult })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={onSelectBuild}
      />,
    );
    expect(screen.getByText('正在查看构建 #9')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '回到当前默认构建' }));
    expect(onSelectBuild).toHaveBeenLastCalledWith();
  });
});
