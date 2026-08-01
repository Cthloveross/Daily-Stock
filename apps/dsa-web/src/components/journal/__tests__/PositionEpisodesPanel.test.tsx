import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PositionEpisodesPanel from '../PositionEpisodesPanel';
import {
  activateEpisodeBuild,
  fetchEpisodeBuildActivation,
} from '../../../api/journal';
import type {
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildActivationResponse,
  EpisodeBuildActivationState,
  EpisodeBuildResponse,
  PositionEpisodeDetailResponse,
  PositionEpisodeItem,
  PositionEpisodeListResponse,
} from '../../../types/journal';

vi.mock('../../../api/journal', () => ({
  activateEpisodeBuild: vi.fn(),
  fetchEpisodeBuildActivation: vi.fn(),
}));

const fetchActivationMock = vi.mocked(fetchEpisodeBuildActivation);
const activateBuildMock = vi.mocked(activateEpisodeBuild);

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
  reviewStatus: 'in_progress',
  reviewRevision: 2,
  reviewUpdatedAt: '2026-07-22T09:30:00Z',
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
    groupFeeUnallocated: false,
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
  reviewStatus: 'completed',
  reviewRevision: 3,
  reviewUpdatedAt: '2026-07-22T10:00:00Z',
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
    sourceWindowStart: '2026-03-04T00:00:00Z',
    sourceCutoffAt: '2026-07-19T23:59:59Z',
    positionEpisodeCount: 2,
    unresolvedEvidenceCount: 0,
    completenessScore: '0.8000',
    openingBoundaryPolicy: 'assumed_flat_unverified',
    assumedFlatUnverified: true,
    executionGroupCount: 0,
    groupFeeAffectedEpisodeCount: 0,
    retainedExecutionGroupFeeTotal: '0.0000000000',
    feeConservationByCurrency: {},
    legFeeAttributionComplete: true,
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
    groupFeeAffectedEpisodeCount: 0,
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
  reviewQueue: {
    pending: 4,
    inProgress: 1,
    completed: 5,
    total: 10,
  },
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
  retainedExecutionGroupFeeTotal: '0.0000000000',
  feeConservationByCurrency: {
    USD: {
      ordinarySource: '151750.7500000000',
      ordinaryAllocated: '151750.7500000000',
      retainedExecutionGroup: '0.0000000000',
      sourceKnown: '151750.7500000000',
      accounted: '151750.7500000000',
    },
  },
  feeConserved: true,
  executionGroupCount: 0,
  groupFeeAffectedEpisodeCount: 0,
  legFeeAttributionComplete: true,
  openingBoundaryPolicy: 'assumed_flat_unverified',
  requiresAssumedFlatAcceptance: true,
  requiresGroupFeeScopeAcceptance: false,
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
  // A canonical build has no snapshot-inherited openings, so its report
  // never counts left-censored episodes.
  summary: { ...readyList.summary!, leftCensoredEpisodeCount: 0 },
  message: 'canonical episode build appended',
};

const activationState: EpisodeBuildActivationState = {
  accountKey: 'default_moomoo_us',
  selectionSource: 'csv_fallback',
  currentActivationId: null,
  currentActivationSequence: null,
  currentBuildId: 7,
  currentBuildKey: 'c'.repeat(64),
  canonicalSetId: null,
  canonicalSetSha256: null,
  previousActivationId: null,
  previousBuildId: null,
  activatedAt: null,
};

const activationResponse: EpisodeBuildActivationResponse = {
  activationId: 12,
  activationKey: 'd'.repeat(64),
  duplicate: false,
  state: {
    ...activationState,
    selectionSource: 'activation',
    currentActivationId: 12,
    currentActivationSequence: 1,
    currentBuildId: 9,
    currentBuildKey: canonicalBuildResult.build.buildKey,
    canonicalSetId: 1,
    canonicalSetSha256: canonicalPreview.canonicalSetSha256,
    previousBuildId: 7,
    activatedAt: '2026-07-30T14:00:00Z',
  },
  message: 'canonical Episode build 9 activated',
  tradingActionPerformed: false,
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
  beforeEach(() => {
    vi.clearAllMocks();
    fetchActivationMock.mockResolvedValue(activationState);
    activateBuildMock.mockResolvedValue(activationResponse);
  });

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

    const replayBasis = screen.getByRole('region', { name: '复盘口径' });
    expect(within(replayBasis).getByText('证据窗口（ET）起—止')).toBeInTheDocument();
    expect(within(replayBasis).getByText('窗口末投影 as-of（ET）')).toBeInTheDocument();
    expect(within(replayBasis).getByText(/不等于券商当前持仓/)).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '证据窗口末数量' })).toBeInTheDocument();
    expect(screen.queryByText('Remaining')).not.toBeInTheDocument();

    const verifiedNetCard = screen.getByText('Verified Net').closest('div.rounded-ds-md');
    expect(verifiedNetCard).not.toBeNull();
    expect(within(verifiedNetCard as HTMLElement).getByText('—')).toBeInTheDocument();

    const openRow = screen.getByText('NVDA260619C00150000').closest('tr');
    expect(openRow).not.toBeNull();
    expect(within(openRow as HTMLElement).getByText('证据窗口末未归零')).toBeInTheDocument();
    expect(within(openRow as HTMLElement).getByText('2')).toBeInTheDocument();
    expect(within(openRow as HTMLElement).getByText('—')).toBeInTheDocument();
  });

  it('shows the review queue and applies the persisted review-status filter', () => {
    const onApplyFilters = vi.fn();
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={controller()}
        onApplyFilters={onApplyFilters}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    const queue = screen.getByLabelText('复盘队列');
    expect(within(queue).getByText('4')).toBeInTheDocument();
    expect(within(queue).getByText('1')).toBeInTheDocument();
    expect(within(queue).getByText('5')).toBeInTheDocument();
    const openRow = screen.getByText('NVDA260619C00150000').closest('tr');
    const closedRow = screen.getByText('TSLA260619P00200000').closest('tr');
    expect(within(openRow as HTMLElement).getByText('进行中 · #2')).toBeInTheDocument();
    expect(within(closedRow as HTMLElement).getByText('已完成 · #3')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('筛选复盘状态'), {
      target: { value: 'completed' },
    });
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
    expect(onApplyFilters).toHaveBeenCalledWith(expect.objectContaining({
      reviewStatus: 'completed',
      page: 1,
    }));
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
    expect(screen.getByText(/把证据窗口开始时的持仓假设为 0/)).toBeInTheDocument();
    expect(screen.getByText(/当前时点的券商持仓快照，也不能倒推这个历史期初/)).toBeInTheDocument();
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
    expect(screen.getByText(/不会替换默认复盘构建/)).toBeInTheDocument();
    const evidenceWindow = screen.getByRole('region', { name: '构建证据窗口' });
    expect(within(evidenceWindow).getByText('证据窗口（ET）起—止')).toBeInTheDocument();
    expect(within(evidenceWindow).getByText('窗口末投影 as-of（ET）')).toBeInTheDocument();

    const generate = screen.getByRole('button', { name: '生成仓位复盘构建' });
    expect(generate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', {
      name: /当前时点的券商持仓快照，也不能倒推这个历史证据窗口的期初持仓/,
    }));
    expect(generate).toBeEnabled();
    fireEvent.click(generate);
    expect(buildCanonical).toHaveBeenCalledWith(true, false);
  });

  it('keeps combo fees at execution-group scope and requires a separate acknowledgement', () => {
    const buildCanonical = vi.fn().mockResolvedValue(canonicalBuildResult);
    const groupPreview: CanonicalEpisodeBuildPlanResponse = {
      ...canonicalPreview,
      retainedExecutionGroupFeeTotal: '8.0800000000',
      feeConservationByCurrency: {
        USD: {
          ordinarySource: '151750.7500000000',
          ordinaryAllocated: '151750.7500000000',
          retainedExecutionGroup: '8.0800000000',
          sourceKnown: '151758.8300000000',
          accounted: '151758.8300000000',
        },
      },
      executionGroupCount: 1,
      groupFeeAffectedEpisodeCount: 2,
      legFeeAttributionComplete: false,
      requiresGroupFeeScopeAcceptance: true,
      warnings: [
        'execution_group_fee_retained_unallocated',
        'execution-group fee remains exact only at group scope; affected leg episodes have no fee/net P&L and require explicit acceptance',
      ],
    };
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={controller({ canonicalPreview: groupPreview, buildCanonical })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    const accounting = screen.getByLabelText('组合执行组费用口径');
    expect(within(accounting).getByText(/1 个组合执行组 · 2 个腿回合受影响/)).toBeInTheDocument();
    expect(within(accounting).getByText(/组费用 USD 8.08/)).toBeInTheDocument();
    expect(screen.getAllByText('组合执行组费用已完整保留在组级；受影响腿不显示费用或净收益，也不进入严格 Headline。')).toHaveLength(1);
    expect(screen.queryByText('execution_group_fee_retained_unallocated')).not.toBeInTheDocument();
    expect(screen.queryByText(/execution-group fee remains exact only/)).not.toBeInTheDocument();
    const generate = screen.getByRole('button', { name: '生成仓位复盘构建' });
    fireEvent.click(screen.getByRole('checkbox', { name: /我接受未验证的期初空仓假设/ }));
    expect(generate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /我理解组合费用只在执行组层精确保留/ }));
    expect(generate).toBeEnabled();
    fireEvent.click(generate);
    expect(buildCanonical).toHaveBeenCalledWith(true, true);
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

    expect(onSelectBuild).not.toHaveBeenCalled();
    expect(activateBuildMock).not.toHaveBeenCalled();
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
    fireEvent.click(screen.getByRole('button', { name: '回到默认复盘构建' }));
    expect(onSelectBuild).toHaveBeenLastCalledWith();
  });

  it('requires a separate assumed-flat acknowledgement before activation', async () => {
    render(
      <PositionEpisodesPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controller({ canonicalBuildResult })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalledTimes(1));
    const activate = screen.getByRole('button', { name: '设为默认复盘构建' });
    expect(activate).toBeDisabled();
    expect(screen.getByText(/这是独立于“生成构建”的第二次确认/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    expect(activate).toBeEnabled();
  });

  it('requires execution-group fee acknowledgement again before activation', async () => {
    const groupBuildResult: EpisodeBuildResponse = {
      ...canonicalBuildResult,
      build: {
        ...canonicalBuildResult.build,
        executionGroupCount: 1,
        groupFeeAffectedEpisodeCount: 2,
        retainedExecutionGroupFeeTotal: '8.0800000000',
        legFeeAttributionComplete: false,
      },
    };
    render(
      <PositionEpisodesPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controller({ canonicalBuildResult: groupBuildResult })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalledTimes(1));
    const activate = screen.getByRole('button', { name: '设为默认复盘构建' });
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    expect(activate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次确认组合费用仅保留在执行组层/,
    }));
    expect(activate).toBeEnabled();
    fireEvent.click(activate);

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(9, expect.objectContaining({
      acceptAssumedFlat: true,
      acceptGroupFeeScope: true,
    })));
  });

  it('activates with the GET state as CAS, clears explicit build, and reloads defaults', async () => {
    const onSelectBuild = vi.fn();
    const onImported = vi.fn();
    const controlled = controller({ canonicalBuildResult });
    render(
      <PositionEpisodesPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controlled}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={onSelectBuild}
        onImported={onImported}
      />,
    );

    await screen.findByText('历史 CSV 默认构建');
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    fireEvent.click(screen.getByRole('button', { name: '设为默认复盘构建' }));

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(9, {
      expectedBuildKey: canonicalBuildResult.build.buildKey,
      expectedCurrentActivationId: null,
      expectedCurrentBuildId: 7,
      acceptAssumedFlat: true,
      acceptGroupFeeScope: false,
      acceptLeftCensoredOpenings: false,
    }));
    expect(onSelectBuild).toHaveBeenCalledWith();
    expect(controlled.reload).toHaveBeenCalledTimes(1);
    expect(controlled.reloadCanonicalPreview).toHaveBeenCalledTimes(1);
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('构建 #9 已设为默认复盘构建')).toBeInTheDocument();
    expect(screen.getByText('零交易动作')).toBeInTheDocument();
  });

  it('reloads stale CAS state and never switches the default view after failure', async () => {
    const onSelectBuild = vi.fn();
    const onImported = vi.fn();
    const controlled = controller({ canonicalBuildResult });
    fetchActivationMock
      .mockResolvedValueOnce(activationState)
      .mockResolvedValueOnce({
        ...activationState,
        selectionSource: 'activation',
        currentActivationId: 13,
        currentActivationSequence: 2,
        currentBuildId: 8,
      });
    activateBuildMock.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'activation state changed; refresh and retry with the current IDs' },
      },
    });

    render(
      <PositionEpisodesPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controlled}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={onSelectBuild}
        onImported={onImported}
      />,
    );

    await screen.findByText('历史 CSV 默认构建');
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    fireEvent.click(screen.getByRole('button', { name: '设为默认复盘构建' }));

    expect(await screen.findByText('默认复盘构建已变化')).toBeInTheDocument();
    expect(screen.getByText(/当前状态已重新读取/)).toBeInTheDocument();
    expect(fetchActivationMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText('构建 #8')).toBeInTheDocument();
    expect(onSelectBuild).not.toHaveBeenCalled();
    expect(controlled.reload).not.toHaveBeenCalled();
    expect(controlled.reloadCanonicalPreview).not.toHaveBeenCalled();
    expect(onImported).not.toHaveBeenCalled();
  });

  it('requires left-censored acknowledgement before activating a build with left-censored episodes', async () => {
    const leftCensoredResult: EpisodeBuildResponse = {
      ...canonicalBuildResult,
      summary: { ...canonicalBuildResult.summary, leftCensoredEpisodeCount: 2 },
    };
    render(
      <PositionEpisodesPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controller({ canonicalBuildResult: leftCensoredResult })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalledTimes(1));
    const activate = screen.getByRole('button', { name: '设为默认复盘构建' });
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    expect(activate).toBeDisabled();
    expect(screen.getByText(/无法回到「零激活」的 CSV 默认状态/)).toBeInTheDocument();
    expect(screen.getByText(/确认 left-censored 回合口径后才能设为默认/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', {
      name: /我接受 left-censored 回合无券商成本/,
    }));
    expect(activate).toBeEnabled();
    fireEvent.click(activate);

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(9, expect.objectContaining({
      acceptAssumedFlat: true,
      acceptLeftCensoredOpenings: true,
    })));
  });

  it('activates a viewed snapshot-fence build only after the left-censored acknowledgement', async () => {
    const fenceList: PositionEpisodeListResponse = {
      ...readyList,
      build: {
        ...readyList.build!,
        id: 31,
        buildKey: 'e'.repeat(64),
        sourceKind: 'position_snapshot_fenced_canonical',
        canonicalSetId: 5,
        canonicalSetSha256: 'f'.repeat(64),
        openingBoundaryPolicy: 'complete_snapshot',
        assumedFlatUnverified: false,
      },
      summary: { ...readyList.summary!, leftCensoredEpisodeCount: 1 },
    };
    const onSelectBuild = vi.fn();
    const onImported = vi.fn();
    const controlled = controller({ list: fenceList });
    activateBuildMock.mockResolvedValue({
      ...activationResponse,
      state: {
        ...activationResponse.state,
        currentBuildId: 31,
        currentBuildKey: 'e'.repeat(64),
        canonicalSetId: 5,
      },
    });
    render(
      <PositionEpisodesPanel
        filters={{ buildId: 31, page: 1, perPage: 50 }}
        controller={controlled}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={onSelectBuild}
        onImported={onImported}
      />,
    );

    const card = await screen.findByRole('region', {
      name: '将当前查看的构建 #31 设为默认',
    });
    await within(card).findByText(/当前 #7/);
    expect(within(card).getByText(/快照围栏 future build · 目标事实集 #5/)).toBeInTheDocument();
    expect(within(card).getByText(/激活身份绑定其冻结的目标事实集/)).toBeInTheDocument();
    expect(within(card).getByText(/无法回到「零激活」的 CSV 默认状态/)).toBeInTheDocument();
    const activate = within(card).getByRole('button', { name: '设为默认复盘构建' });
    expect(activate).toBeDisabled();
    expect(within(card).queryByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    })).not.toBeInTheDocument();

    fireEvent.click(within(card).getByRole('checkbox', {
      name: /left-censored 回合无券商成本/,
    }));
    expect(activate).toBeEnabled();
    fireEvent.click(activate);

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(31, {
      expectedBuildKey: 'e'.repeat(64),
      expectedCurrentActivationId: null,
      expectedCurrentBuildId: 7,
      acceptAssumedFlat: false,
      acceptGroupFeeScope: false,
      acceptLeftCensoredOpenings: true,
    }));
    expect(onSelectBuild).toHaveBeenCalledWith();
    expect(controlled.reload).toHaveBeenCalledTimes(1);
    expect(controlled.reloadCanonicalPreview).toHaveBeenCalledTimes(1);
    expect(onImported).toHaveBeenCalledTimes(1);
  });

  it('offers no activation affordance for a viewed CSV fallback build', async () => {
    render(
      <PositionEpisodesPanel
        filters={{ buildId: 7, page: 1, perPage: 50 }}
        controller={controller()}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    expect(screen.getByText('正在查看构建 #7')).toBeInTheDocument();
    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: '设为默认复盘构建' })).not.toBeInTheDocument();
    expect(activateBuildMock).not.toHaveBeenCalled();
  });
});
