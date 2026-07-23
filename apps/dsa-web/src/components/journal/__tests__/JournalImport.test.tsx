import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  confirmJournalOpenApiImport,
  fetchLedgerDataHealth,
  importJournalLedgerCsv,
  planJournalOpenApiImport,
  previewJournalCsv,
} from '../../../api/journal';
import JournalImport from '../JournalImport';

vi.mock('../../../api/journal', () => ({
  confirmJournalOpenApiImport: vi.fn(),
  fetchLedgerDataHealth: vi.fn(),
  importJournalLedgerCsv: vi.fn(),
  planJournalOpenApiImport: vi.fn(),
  previewJournalCsv: vi.fn(),
}));

const previewMock = vi.mocked(previewJournalCsv);
const openApiPlanMock = vi.mocked(planJournalOpenApiImport);
const openApiConfirmMock = vi.mocked(confirmJournalOpenApiImport);
const importMock = vi.mocked(importJournalLedgerCsv);
const healthMock = vi.mocked(fetchLedgerDataHealth);

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
        windowStart: '2026-06-19T23:59:59-04:00',
        windowEnd: '2026-07-19T23:59:59-04:00',
        sourceTimezone: 'America/New_York',
        orderObservations: 1089,
        fillObservations: 2107,
        feeObservations: 1058,
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
        ambiguousIdentityKeys: 0,
        coveredBaseOrders: 1089,
        baseOrders: 3542,
        coverageRatio: '0.3075',
        outsideUnverifiedOrders: 2453,
      },
      writePlan: {
        alreadyImported: false,
        orderObservations: 1089,
        fillObservations: 2107,
        feeObservations: 1058,
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
      },
      issues: [],
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
        fills: 2107,
        fees: 1058,
        orderLinks: 1089,
        dealLinks: 0,
        fillSetAttestations: 1089,
        canonicalSets: 1,
      },
      canonical: {
        orders: 3542,
        fills: 5400,
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
    fireEvent.click(screen.getByRole('button', { name: 'OpenAPI 只读 JSON' }));
    const input = container.querySelector('input[accept=".json,application/json"]');
    expect(input).not.toBeNull();
    const file = new File(['{}'], 'readonly.json', { type: 'application/json' });
    fireEvent.change(input!, { target: { files: [file] } });

    expect(await screen.findByText('OpenAPI 只读证据计划可确认')).toBeInTheDocument();
    expect(screen.getByText(/局部覆盖：窗口内匹配 1,089 \/ 3,542/)).toBeInTheDocument();
    expect(screen.getByText(/窗口外 2,453 笔订单尚未经过 API 核验/)).toBeInTheDocument();
    expect(screen.getByText(/Canonical 结果：3,542 笔订单/)).toBeInTheDocument();
    expect(screen.getByText(/2,107 条 CSV fill 标记为 shadow/)).toBeInTheDocument();
    const confirmButton = screen.getByRole('button', { name: '确认追加只读证据' });
    expect(confirmButton).toBeDisabled();
    expect(openApiPlanMock).toHaveBeenCalledWith(file);

    fireEvent.click(screen.getByRole('checkbox'));
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);

    expect(await screen.findByText(/已向本地可信账本追加 1,089 条订单/)).toBeInTheDocument();
    expect(openApiConfirmMock).toHaveBeenCalledWith(file, 'preview-key', true);
    expect(onImported).not.toHaveBeenCalled();
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
        windowStart: '2026-06-19T23:59:59-04:00',
        windowEnd: '2026-07-19T23:59:59-04:00',
        sourceTimezone: 'America/New_York',
        orderObservations: 1089,
        fillObservations: 2107,
        feeObservations: 1058,
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
        ambiguousIdentityKeys: 0,
        coveredBaseOrders: 1089,
        baseOrders: 3542,
        coverageRatio: '0.3075',
        outsideUnverifiedOrders: 2453,
      },
      writePlan: {
        alreadyImported: true,
        orderObservations: 0,
        fillObservations: 0,
        feeObservations: 0,
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
      },
      issues: [],
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
        fills: 0,
        fees: 0,
        orderLinks: 0,
        dealLinks: 0,
        fillSetAttestations: 0,
        canonicalSets: 0,
      },
      canonical: {
        orders: 3542,
        fills: 5400,
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
});
