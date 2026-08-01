import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  confirmJournalRefresh,
  confirmJournalOpenApiImport,
  fetchJournalRefreshStatus,
  fetchLedgerDataHealth,
  importJournalLedgerCsv,
  planJournalOpenApiImport,
  previewJournalRefresh,
  previewJournalCsv,
} from '../../../api/journal';
import JournalImport from '../JournalImport';
import type {
  JournalRefreshStatus,
  MoomooJournalRefreshPreview,
  MoomooOpenApiImportPlan,
} from '../../../types/journal';

vi.mock('../../../api/journal', () => ({
  confirmJournalRefresh: vi.fn(),
  confirmJournalOpenApiImport: vi.fn(),
  fetchJournalRefreshStatus: vi.fn(),
  fetchLedgerDataHealth: vi.fn(),
  importJournalLedgerCsv: vi.fn(),
  planJournalOpenApiImport: vi.fn(),
  previewJournalRefresh: vi.fn(),
  previewJournalCsv: vi.fn(),
}));

const refreshStatusMock = vi.mocked(fetchJournalRefreshStatus);
const refreshPreviewMock = vi.mocked(previewJournalRefresh);
const refreshConfirmMock = vi.mocked(confirmJournalRefresh);
const previewMock = vi.mocked(previewJournalCsv);
const openApiPlanMock = vi.mocked(planJournalOpenApiImport);
const openApiConfirmMock = vi.mocked(confirmJournalOpenApiImport);
const importMock = vi.mocked(importJournalLedgerCsv);
const healthMock = vi.mocked(fetchLedgerDataHealth);

const refreshStatus: JournalRefreshStatus = {
  refreshEnabled: true,
  refreshConfigured: true,
  freshnessState: 'stale',
  pendingStage: 'refresh',
  expectedCompleteThrough: '2026-07-29T20:00:00Z',
  brokerQueriedThrough: '2026-07-28T20:00:00Z',
  latestFillAt: '2026-07-28T14:35:00Z',
  evidencePublishedThrough: '2026-07-28T20:00:00Z',
  publicationRecordedAt: '2026-07-29T01:00:00Z',
  latestArtifactId: null,
  latestArtifactConfirmAllowed: null,
  latestArtifactExpiresAt: null,
  latestCanonicalSetId: 4,
  latestCanonicalSetSha256: 'a'.repeat(64),
  latestCanonicalSourceThrough: '2026-07-28T20:00:00Z',
  latestCanonicalBuildId: 12,
  latestCanonicalBuildKey: 'b'.repeat(64),
  latestCanonicalBuildSourceThrough: '2026-07-28T20:00:00Z',
  activeSelectionSource: 'activation',
  activeActivationId: 3,
  activeBuildId: 12,
  activeBuildKey: 'b'.repeat(64),
  activeCanonicalSetId: 4,
  activeSourceThrough: '2026-07-28T20:00:00Z',
  accountBound: true,
};

const refreshPlan: MoomooOpenApiImportPlan = {
  previewKey: 'p'.repeat(64),
  confirmAllowed: true,
  journalDatabaseWritten: false,
  warnings: [],
  sourceScope: {
    environment: 'LIVE',
    market: 'US',
    accountSelection: 'bound',
    accountBound: true,
    windowStart: '2026-07-21T00:00:00-04:00',
    windowEnd: '2026-07-29T16:00:00-04:00',
    sourceTimezone: 'America/New_York',
    orderObservations: 14,
    ordinaryOrderObservations: 13,
    unclassifiedParentObservations: 0,
    fillObservations: 18,
    feeObservations: 12,
    contractSpecObservations: 2,
    executionGroupObservations: 1,
    executionGroupLegObservations: 2,
    executionGroupFillLinks: 4,
    executionGroupFeeObservations: 1,
    feeTotalsByCurrency: { USD: '28.50' },
  },
  baseScope: {
    batchId: 4,
    batchKey: 'baseline',
    windowStart: '2026-03-01T00:00:00-05:00',
    windowEnd: '2026-07-28T16:00:00-04:00',
    orderObservations: 100,
    fillObservations: 140,
    windowOrderObservations: 10,
  },
  coverage: {
    scope: 'partial_window',
    matchedOrders: 10,
    csvOnlyInWindow: 0,
    apiOnlyOrders: 4,
    overlapApiOnlyOrders: 0,
    incrementalApiOnlyOrders: 4,
    ambiguousIdentityKeys: 0,
    coveredBaseOrders: 10,
    baseOrders: 100,
    coverageRatio: '0.1000',
    outsideUnverifiedOrders: 90,
  },
  writePlan: {
    alreadyImported: false,
    orderObservations: 4,
    ordinaryOrderObservations: 3,
    unclassifiedParentObservations: 0,
    fillObservations: 6,
    feeObservations: 4,
    executionGroupObservations: 1,
    executionGroupLegObservations: 2,
    executionGroupFillLinks: 4,
    executionGroupFeeObservations: 1,
    orderIdentityLinks: 10,
    dealIdentityLinks: 8,
    fillSetAttestations: 2,
    canonicalSets: 1,
  },
  canonicalImpact: {
    inputOrderObservations: 114,
    inputFillObservations: 158,
    canonicalOrders: 104,
    canonicalFills: 146,
    duplicateOrderObservations: 10,
    duplicateFillObservations: 12,
    shadowedCsvFills: 12,
    shadowedAggregateOrders: 0,
    blockingIssues: 0,
    analysisReady: true,
    canonicalSetSha256: 'c'.repeat(64),
    inputExecutionGroupObservations: 1,
    canonicalExecutionGroups: 1,
    canonicalExecutionGroupLegs: 2,
    duplicateExecutionGroupObservations: 0,
  },
  issues: [],
  scopeIsFullBatch: false,
};

const refreshPreview: MoomooJournalRefreshPreview = {
  artifactId: 31,
  artifactKey: 'artifact-31',
  expiresAt: '2026-07-30T03:00:00Z',
  source: {
    retrievalComplete: true,
    coverageComplete: true,
    hasActivity: true,
    brokerQueriedThrough: '2026-07-29T20:00:00Z',
    latestFillAt: '2026-07-29T15:12:00Z',
    orderObservations: 14,
    ordinaryOrderObservations: 13,
    unclassifiedParentObservations: 0,
    fillObservations: 18,
    feeObservations: 12,
    contractSpecObservations: 2,
    executionGroupObservations: 1,
    executionGroupLegObservations: 2,
    executionGroupFillLinks: 4,
    executionGroupFeeObservations: 1,
  },
  plan: refreshPlan,
  evidenceWritten: false,
  tradingActionPerformed: false,
};

function openAdvancedImport(): void {
  fireEvent.click(screen.getByText('高级 / 首次导入：CSV 账单或只读 JSON'));
}

beforeEach(() => {
  refreshStatusMock.mockResolvedValue(refreshStatus);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('JournalImport', () => {
  it('previews evidence coverage without claiming a database write', async () => {
    healthMock.mockResolvedValue({
      hasData: false,
      orderObservations: 0,
      fillObservations: 0,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 0,
      legacyJournalWritten: false,
    });
    previewMock.mockResolvedValue({
      parserVersion: 'moomoo-statement-v2',
      analysisLevel: 'partial',
      rowsTotal: 6094,
      ordersTotal: 3542,
      statusCounts: { FILLED: 3422, CANCELLED: 50, FAILED: 70 },
      filledOrders: 3422,
      detailBackedFilledOrders: 2848,
      aggregateOnlyFilledOrders: 574,
      inconsistentFilledOrders: 0,
      fillRecords: 5400,
      orphanFillRows: 0,
      filledFeeTotal: '151750.75',
      detailBackedFeeTotal: '137922.76',
      aggregateOnlyFeeTotal: '13827.99',
      orderTimeRange: {
        first: '2026-03-04T09:31:57-05:00',
        last: '2026-07-20T14:46:16-04:00',
      },
      fillDetailTimeRange: {
        first: '2026-04-21T11:04:44-04:00',
        last: '2026-07-20T14:46:25-04:00',
      },
      warnings: ['older_filled_orders_have_aggregate_evidence_only'],
      journalDatabaseWritten: false,
    });
    importMock.mockResolvedValue({
      batchId: 1,
      duplicate: false,
      analysisLevel: 'partial',
      orderObservations: 3542,
      fillObservations: 5400,
      legacyJournalWritten: false,
      message: 'appended',
    });

    const { container } = render(<JournalImport />);
    openAdvancedImport();
    const input = container.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, {
      target: { files: [new File(['test'], 'history.csv', { type: 'text/csv' })] },
    });

    expect(await screen.findByText('部分历史仅有汇总成交')).toBeInTheDocument();
    expect(screen.getByText('证据行写入：0（仅计划）')).toBeInTheDocument();
    expect(screen.getByText('3,542')).toBeInTheDocument();
    expect(screen.getByText('5,400')).toBeInTheDocument();
    expect(screen.getByText('574')).toBeInTheDocument();
    expect(screen.getByText(/不会用于精确进场时刻/)).toBeInTheDocument();
    expect(previewMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '保存到可信证据账本' }));
    expect(await screen.findByText(/已保存 3,542 条订单与 5,400 条逐笔成交/)).toBeInTheDocument();
    expect(importMock).toHaveBeenCalledWith(expect.any(File), true);
  });

  it('labels a passed reconciliation as a partial window, not the whole batch', async () => {
    healthMock.mockResolvedValue({
      hasData: true,
      analysisLevel: 'partial',
      reconciliationStatus: 'passed',
      reconciliationScope: 'partial_window',
      reconciliationWindowStart: '2026-06-19T23:59:59-04:00',
      reconciliationWindowEnd: '2026-07-19T23:59:59-04:00',
      reconciledOrderObservations: 1089,
      completenessScore: '0.8323',
      orderObservations: 3542,
      fillObservations: 5400,
      aggregateOnlyFilledOrders: 574,
      legacyJournalWritten: false,
    });

    render(<JournalImport />);
    openAdvancedImport();

    expect(await screen.findByText(/部分窗口已通过（1,089 \/ 3,542 笔订单）/)).toBeInTheDocument();
    expect(screen.getByText(/窗口外证据尚未经过 API 逐单核对/)).toBeInTheDocument();
    expect(screen.queryByText('对账：已通过')).not.toBeInTheDocument();
  });

  it('shows the partial-window OpenAPI plan and requires acknowledgement before confirm', async () => {
    healthMock.mockResolvedValue({
      hasData: false,
      orderObservations: 0,
      fillObservations: 0,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 0,
      legacyJournalWritten: false,
    });
    openApiPlanMock.mockResolvedValue({
      previewKey: 'preview-key',
      confirmAllowed: true,
      journalDatabaseWritten: false,
      warnings: [],
      sourceScope: {
        environment: 'LIVE',
        market: 'US',
        accountSelection: 'unique_auto',
        accountBound: false,
        windowStart: '2026-06-19T23:59:59-04:00',
        windowEnd: '2026-07-19T23:59:59-04:00',
        sourceTimezone: 'America/New_York',
        orderObservations: 1089,
        ordinaryOrderObservations: 1089,
        unclassifiedParentObservations: 0,
        fillObservations: 2107,
        feeObservations: 1058,
        contractSpecObservations: 0,
        executionGroupObservations: 0,
        executionGroupLegObservations: 0,
        executionGroupFillLinks: 0,
        executionGroupFeeObservations: 0,
        feeTotalsByCurrency: { USD: '56081.9' },
      },
      baseScope: {
        batchId: 1,
        batchKey: 'csv-batch',
        windowStart: '2026-03-04T09:31:57-05:00',
        windowEnd: '2026-07-20T14:46:25-04:00',
        orderObservations: 3542,
        fillObservations: 5400,
        windowOrderObservations: 1089,
      },
      coverage: {
        scope: 'partial_window',
        matchedOrders: 1089,
        csvOnlyInWindow: 0,
        apiOnlyOrders: 0,
        overlapApiOnlyOrders: 0,
        incrementalApiOnlyOrders: 0,
        ambiguousIdentityKeys: 0,
        coveredBaseOrders: 1089,
        baseOrders: 3542,
        coverageRatio: '0.3075',
        outsideUnverifiedOrders: 2453,
      },
      writePlan: {
        alreadyImported: false,
        orderObservations: 1089,
        ordinaryOrderObservations: 1089,
        unclassifiedParentObservations: 0,
        fillObservations: 2107,
        feeObservations: 1058,
        executionGroupObservations: 0,
        executionGroupLegObservations: 0,
        executionGroupFillLinks: 0,
        executionGroupFeeObservations: 0,
        orderIdentityLinks: 1089,
        dealIdentityLinks: 0,
        fillSetAttestations: 1089,
        canonicalSets: 1,
      },
      canonicalImpact: {
        inputOrderObservations: 4631,
        inputFillObservations: 7507,
        canonicalOrders: 3542,
        canonicalFills: 5400,
        duplicateOrderObservations: 1089,
        duplicateFillObservations: 0,
        shadowedCsvFills: 2107,
        shadowedAggregateOrders: 574,
        blockingIssues: 0,
        analysisReady: true,
        canonicalSetSha256: 'c'.repeat(64),
        inputExecutionGroupObservations: 0,
        canonicalExecutionGroups: 0,
        canonicalExecutionGroupLegs: 0,
        duplicateExecutionGroupObservations: 0,
      },
      issues: [],
      scopeIsFullBatch: false,
    });
    openApiConfirmMock.mockResolvedValue({
      status: 'appended',
      duplicate: false,
      importBatchId: 2,
      canonicalSetId: 1,
      canonicalSetSha256: 'c'.repeat(64),
      scope: 'partial_window',
      appended: {
        orders: 1089,
        ordinaryOrders: 1089,
        fills: 2107,
        fees: 1058,
        executionGroups: 0,
        executionGroupLegs: 0,
        executionGroupFillLinks: 0,
        executionGroupFees: 0,
        orderLinks: 1089,
        dealLinks: 0,
        fillSetAttestations: 1089,
        canonicalSets: 1,
      },
      canonical: {
        orders: 3542,
        fills: 5400,
        executionGroups: 0,
        executionGroupLegs: 0,
        shadowedCsvFills: 2107,
        shadowedAggregateOrders: 574,
        blockingIssues: 0,
        analysisReady: true,
      },
      legacyJournalWritten: false,
      episodeBuildTriggered: false,
      tradingActionPerformed: false,
      message: 'appended',
    });

    const onImported = vi.fn();
    const { container } = render(<JournalImport onImported={onImported} />);
    openAdvancedImport();
    fireEvent.click(screen.getByRole('button', { name: 'OpenAPI 只读 JSON' }));
    const input = container.querySelector('input[accept=".json,application/json"]');
    expect(input).not.toBeNull();
    const file = new File(['{}'], 'readonly.json', { type: 'application/json' });
    fireEvent.change(input!, { target: { files: [file] } });

    expect(await screen.findByText('OpenAPI 只读证据计划可确认')).toBeInTheDocument();
    expect(screen.getByText(/局部覆盖：窗口内匹配 1,089 \/ 3,542/)).toBeInTheDocument();
    expect(screen.getByText(/窗口外 2,453 笔订单尚未经过 API 核验/)).toBeInTheDocument();
    expect(screen.getByText(/Canonical 结果：3,542 笔普通单/)).toBeInTheDocument();
    expect(screen.getByText(/2,107 条 CSV fill 标记为 shadow/)).toBeInTheDocument();
    const confirmButton = screen.getByRole('button', { name: '确认追加只读证据' });
    expect(confirmButton).toBeDisabled();
    expect(openApiPlanMock).toHaveBeenCalledWith(file);

    fireEvent.click(screen.getByRole('checkbox'));
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);

    expect(await screen.findByText(/已向本地可信账本追加 1,089 条父记录/)).toBeInTheDocument();
    expect(openApiConfirmMock).toHaveBeenCalledWith(file, 'preview-key', true);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
  });

  it('reports an idempotent OpenAPI confirm as zero new rows with its hash', async () => {
    healthMock.mockResolvedValue({
      hasData: false,
      orderObservations: 0,
      fillObservations: 0,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 0,
      legacyJournalWritten: false,
    });
    openApiPlanMock.mockResolvedValue({
      previewKey: 'duplicate-preview',
      confirmAllowed: true,
      journalDatabaseWritten: false,
      warnings: [],
      sourceScope: {
        environment: 'LIVE',
        market: 'US',
        accountSelection: 'unique_auto',
        accountBound: false,
        windowStart: '2026-06-19T23:59:59-04:00',
        windowEnd: '2026-07-19T23:59:59-04:00',
        sourceTimezone: 'America/New_York',
        orderObservations: 1089,
        ordinaryOrderObservations: 1089,
        unclassifiedParentObservations: 0,
        fillObservations: 2107,
        feeObservations: 1058,
        contractSpecObservations: 0,
        executionGroupObservations: 0,
        executionGroupLegObservations: 0,
        executionGroupFillLinks: 0,
        executionGroupFeeObservations: 0,
        feeTotalsByCurrency: { USD: '56081.9' },
      },
      baseScope: {
        batchId: 1,
        batchKey: 'csv-batch',
        windowStart: '2026-03-04T09:31:57-05:00',
        windowEnd: '2026-07-20T14:46:25-04:00',
        orderObservations: 3542,
        fillObservations: 5400,
        windowOrderObservations: 1089,
      },
      coverage: {
        scope: 'partial_window',
        matchedOrders: 1089,
        csvOnlyInWindow: 0,
        apiOnlyOrders: 0,
        overlapApiOnlyOrders: 0,
        incrementalApiOnlyOrders: 0,
        ambiguousIdentityKeys: 0,
        coveredBaseOrders: 1089,
        baseOrders: 3542,
        coverageRatio: '0.3075',
        outsideUnverifiedOrders: 2453,
      },
      writePlan: {
        alreadyImported: true,
        orderObservations: 0,
        ordinaryOrderObservations: 0,
        unclassifiedParentObservations: 0,
        fillObservations: 0,
        feeObservations: 0,
        executionGroupObservations: 0,
        executionGroupLegObservations: 0,
        executionGroupFillLinks: 0,
        executionGroupFeeObservations: 0,
        orderIdentityLinks: 0,
        dealIdentityLinks: 0,
        fillSetAttestations: 0,
        canonicalSets: 0,
      },
      canonicalImpact: {
        inputOrderObservations: 4631,
        inputFillObservations: 7507,
        canonicalOrders: 3542,
        canonicalFills: 5400,
        duplicateOrderObservations: 1089,
        duplicateFillObservations: 0,
        shadowedCsvFills: 2107,
        shadowedAggregateOrders: 574,
        blockingIssues: 0,
        analysisReady: true,
        canonicalSetSha256: 'd'.repeat(64),
        inputExecutionGroupObservations: 0,
        canonicalExecutionGroups: 0,
        canonicalExecutionGroupLegs: 0,
        duplicateExecutionGroupObservations: 0,
      },
      issues: [],
      scopeIsFullBatch: false,
    });
    openApiConfirmMock.mockResolvedValue({
      status: 'duplicate',
      duplicate: true,
      importBatchId: 2,
      canonicalSetId: 1,
      canonicalSetSha256: 'd'.repeat(64),
      scope: 'partial_window',
      appended: {
        orders: 0,
        ordinaryOrders: 0,
        fills: 0,
        fees: 0,
        executionGroups: 0,
        executionGroupLegs: 0,
        executionGroupFillLinks: 0,
        executionGroupFees: 0,
        orderLinks: 0,
        dealLinks: 0,
        fillSetAttestations: 0,
        canonicalSets: 0,
      },
      canonical: {
        orders: 3542,
        fills: 5400,
        executionGroups: 0,
        executionGroupLegs: 0,
        shadowedCsvFills: 2107,
        shadowedAggregateOrders: 574,
        blockingIssues: 0,
        analysisReady: true,
      },
      legacyJournalWritten: false,
      episodeBuildTriggered: false,
      tradingActionPerformed: false,
      message: 'duplicate',
    });

    const { container } = render(<JournalImport />);
    openAdvancedImport();
    fireEvent.click(screen.getByRole('button', { name: 'OpenAPI 只读 JSON' }));
    const input = container.querySelector('input[accept=".json,application/json"]');
    const file = new File(['{}'], 'readonly.json', { type: 'application/json' });
    fireEvent.change(input!, { target: { files: [file] } });

    await screen.findByText('OpenAPI 只读证据计划可确认');
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '确认追加只读证据' }));

    expect(await screen.findByText(new RegExp(`本次新增 0 条；canonical hash：${'d'.repeat(64)}`))).toBeInTheDocument();
    await waitFor(() => expect(openApiConfirmMock).toHaveBeenCalledTimes(1));
  });

  it('keeps combo execution-group evidence separate through manual plan and confirm', async () => {
    healthMock.mockResolvedValue({
      hasData: false,
      orderObservations: 0,
      fillObservations: 0,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 0,
      legacyJournalWritten: false,
    });
    openApiPlanMock.mockResolvedValue(refreshPlan);
    openApiConfirmMock.mockResolvedValue({
      status: 'appended',
      duplicate: false,
      importBatchId: 5,
      canonicalSetId: 5,
      canonicalSetSha256: 'c'.repeat(64),
      scope: 'incremental_tail',
      appended: {
        orders: 4,
        ordinaryOrders: 3,
        fills: 6,
        fees: 4,
        executionGroups: 1,
        executionGroupLegs: 2,
        executionGroupFillLinks: 4,
        executionGroupFees: 1,
        orderLinks: 10,
        dealLinks: 8,
        fillSetAttestations: 2,
        canonicalSets: 1,
      },
      canonical: {
        orders: 104,
        fills: 146,
        executionGroups: 1,
        executionGroupLegs: 2,
        shadowedCsvFills: 12,
        shadowedAggregateOrders: 0,
        blockingIssues: 0,
        analysisReady: true,
      },
      legacyJournalWritten: false,
      episodeBuildTriggered: false,
      tradingActionPerformed: false,
      message: 'appended',
    });

    const { container } = render(<JournalImport />);
    openAdvancedImport();
    fireEvent.click(screen.getByRole('button', { name: 'OpenAPI 只读 JSON' }));
    const input = container.querySelector('input[accept=".json,application/json"]');
    const file = new File(['{}'], 'combo-readonly.json', { type: 'application/json' });
    fireEvent.change(input!, { target: { files: [file] } });

    const evidenceStructure = await screen.findByRole('region', {
      name: 'API 执行证据结构',
    });
    expect(within(evidenceStructure).getByLabelText('普通单父记录：13')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('组合执行组：1')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('组合腿：2')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('成交 → 腿链接：4')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('组合组级费用：1')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('合约规格快照：2')).toBeInTheDocument();
    expect(within(evidenceStructure).getByText(/组合费用只按券商原值保留在 execution group 层/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '确认追加只读证据' }));

    expect(await screen.findByText(/普通单 3 \/ 组合执行组 1/)).toBeInTheDocument();
    expect(screen.getByText(/组合部分含 2 条腿、4 条成交链接和 1 条组级费用/)).toBeInTheDocument();
    expect(screen.getByText(/组合费用未分摊到腿/)).toBeInTheDocument();
    expect(openApiConfirmMock).toHaveBeenCalledWith(file, 'p'.repeat(64), true);
  });

  it('runs the server-owned OpenD preview without publishing evidence', async () => {
    healthMock.mockResolvedValue({
      hasData: true,
      orderObservations: 100,
      fillObservations: 140,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 10,
      legacyJournalWritten: false,
    });
    refreshPreviewMock.mockResolvedValue(refreshPreview);

    render(<JournalImport />);

    expect(await screen.findByText('券商查询水位已过期')).toBeInTheDocument();
    expect(screen.getByText('券商查询完整至')).toBeInTheDocument();
    expect(screen.getByText('最新成交')).toBeInTheDocument();
    expect(screen.getByText('仅表示交易活动，不代表查询完整度')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '检查上一完整交易日' }));

    expect(await screen.findByRole('region', { name: 'Moomoo 刷新预览' })).toBeInTheDocument();
    expect(refreshPreviewMock).toHaveBeenCalledWith(7);
    expect(screen.getByText('零证据写入 · 零交易动作')).toBeInTheDocument();
    expect(screen.getByText('新增尾部订单')).toBeInTheDocument();
    const evidenceStructure = screen.getByRole('region', {
      name: '本次读取的执行证据结构',
    });
    expect(within(evidenceStructure).getByLabelText('普通单父记录：13')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('组合执行组：1')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('组合腿：2')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('成交 → 腿链接：4')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('组合组级费用：1')).toBeInTheDocument();
    expect(within(evidenceStructure).getByLabelText('合约规格快照：2')).toBeInTheDocument();
    expect(within(evidenceStructure).getByText(/组合费用只按券商原值保留在 execution group 层/)).toBeInTheDocument();
    expect(refreshConfirmMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '确认发布到可信账本' })).toBeDisabled();
  });

  it('explains combo-order blockers without presenting a publish acknowledgement', async () => {
    healthMock.mockResolvedValue({
      hasData: true,
      orderObservations: 100,
      fillObservations: 140,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 10,
      legacyJournalWritten: false,
    });
    refreshPreviewMock.mockResolvedValue({
      ...refreshPreview,
      plan: {
        ...refreshPlan,
        confirmAllowed: false,
        warnings: [
          'filled_orders_without_fees=9',
          'combo_order_requires_group_projection=1',
        ],
        coverage: {
          ...refreshPlan.coverage,
          scope: 'not_evaluated_combo_blocked',
          matchedOrders: 0,
          incrementalApiOnlyOrders: 0,
        },
        writePlan: {
          ...refreshPlan.writePlan,
          orderObservations: 0,
          ordinaryOrderObservations: 0,
          fillObservations: 0,
          feeObservations: 0,
          executionGroupObservations: 0,
          executionGroupLegObservations: 0,
          executionGroupFillLinks: 0,
          executionGroupFeeObservations: 0,
          canonicalSets: 0,
        },
        canonicalImpact: {
          ...refreshPlan.canonicalImpact,
          canonicalOrders: 0,
          canonicalFills: 0,
          canonicalExecutionGroups: 0,
          canonicalExecutionGroupLegs: 0,
          blockingIssues: 1,
          analysisReady: false,
          canonicalSetSha256: '',
        },
        issues: [{
          code: 'combo_order_requires_group_projection',
          severity: 'blocking',
          entityKind: 'execution_group',
          entityKey: 'combo_incremental_tail',
          message: 'raw backend message',
        }],
        scopeIsFullBatch: false,
      },
    });

    render(<JournalImport />);
    await screen.findByText('券商查询水位已过期');
    fireEvent.click(screen.getByRole('button', { name: '检查上一完整交易日' }));

    expect(await screen.findByText(/旧版预览检测到 1 笔组合期权父单/)).toBeInTheDocument();
    expect(screen.getByText(/9 笔已成交订单尚未取得券商费用明细/)).toBeInTheDocument();
    expect(screen.getByText(/旧版计划留下的兼容阻断/)).toBeInTheDocument();
    expect(screen.getByText('组合单阻断前未执行跨源核对')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /我确认本次 OpenD 查询只核对/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认发布到可信账本' })).toBeDisabled();
    expect(refreshConfirmMock).not.toHaveBeenCalled();
  });

  it('confirms the frozen refresh then reloads status and notifies the position workflow', async () => {
    healthMock.mockResolvedValue({
      hasData: true,
      batchId: 5,
      analysisLevel: 'exact',
      orderObservations: 104,
      fillObservations: 146,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 10,
      legacyJournalWritten: false,
    });
    refreshPreviewMock.mockResolvedValue(refreshPreview);
    refreshConfirmMock.mockResolvedValue({
      artifactId: 31,
      publication: {
        publicationId: 8,
        brokerQueriedThrough: '2026-07-29T20:00:00Z',
        latestFillAt: '2026-07-29T15:12:00Z',
        evidencePublishedThrough: '2026-07-29T20:00:00Z',
        recordedAt: '2026-07-30T01:30:00Z',
      },
      imported: {
        status: 'appended',
        duplicate: false,
        importBatchId: 5,
        canonicalSetId: 5,
        canonicalSetSha256: 'c'.repeat(64),
        scope: 'partial_window',
        appended: {
          orders: 4,
          ordinaryOrders: 3,
          fills: 6,
          fees: 4,
          executionGroups: 1,
          executionGroupLegs: 2,
          executionGroupFillLinks: 4,
          executionGroupFees: 1,
          orderLinks: 10,
          dealLinks: 8,
          fillSetAttestations: 2,
          canonicalSets: 1,
        },
        canonical: {
          orders: 104,
          fills: 146,
          executionGroups: 1,
          executionGroupLegs: 2,
          shadowedCsvFills: 12,
          shadowedAggregateOrders: 0,
          blockingIssues: 0,
          analysisReady: true,
        },
        legacyJournalWritten: false,
        episodeBuildTriggered: false,
        tradingActionPerformed: false,
        message: 'appended',
      },
      tradingActionPerformed: false,
    });
    const onImported = vi.fn();

    render(<JournalImport onImported={onImported} />);
    await screen.findByText('券商查询水位已过期');
    fireEvent.click(screen.getByRole('button', { name: '检查上一完整交易日' }));
    await screen.findByRole('region', { name: 'Moomoo 刷新预览' });
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认本次 OpenD 查询只核对/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认发布到可信账本' }));

    expect(await screen.findByText('可信证据已发布')).toBeInTheDocument();
    expect(screen.getByText(/新增普通单 3 \/ 组合执行组 1 \/ 2 条组合腿 \/ 4 条成交链接 \/ 1 条组级费用/)).toBeInTheDocument();
    expect(screen.getByText(/组合费用只保留在执行组层，不会分摊到腿/)).toBeInTheDocument();
    expect(refreshConfirmMock).toHaveBeenCalledWith(31, 'p'.repeat(64), true);
    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(refreshStatusMock.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(healthMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('fails closed when the server-owned refresh is not configured', async () => {
    refreshStatusMock.mockResolvedValue({
      ...refreshStatus,
      refreshConfigured: false,
      freshnessState: 'never_synced',
      pendingStage: 'refresh',
      brokerQueriedThrough: null,
      latestFillAt: null,
      evidencePublishedThrough: null,
      publicationRecordedAt: null,
      activeSelectionSource: 'none',
      activeBuildId: null,
      activeSourceThrough: null,
      accountBound: false,
    });
    healthMock.mockResolvedValue({
      hasData: false,
      orderObservations: 0,
      fillObservations: 0,
      aggregateOnlyFilledOrders: 0,
      reconciledOrderObservations: 0,
      legacyJournalWritten: false,
    });

    render(<JournalImport />);

    expect(await screen.findByText('OpenD 只读刷新尚未就绪')).toBeInTheDocument();
    expect(screen.getByText(/尚未完成 LIVE 只读账户绑定或 OpenD 配置/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '检查上一完整交易日' })).toBeDisabled();
    expect(refreshPreviewMock).not.toHaveBeenCalled();
  });
});
