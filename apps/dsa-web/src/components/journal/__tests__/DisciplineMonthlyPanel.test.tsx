import { render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DisciplineMonthlyPanel from '../DisciplineMonthlyPanel';
import { fetchPersonalEdge } from '../../../api/journal';
import type {
  PersonalEdgeDisciplineStats,
  PersonalEdgeResponse,
} from '../../../types/journal';

vi.mock('../../../api/journal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/journal')>();
  return {
    ...actual,
    fetchPersonalEdge: vi.fn(),
  };
});

const LIMITATION =
  '部分回合由汇总 ORDER 行构建（evidence_summary_json.fill_allocations=0，无明细成交）';

function stats(overrides: Partial<PersonalEdgeDisciplineStats> = {}): PersonalEdgeDisciplineStats {
  return {
    n: 361,
    tradingDayCount: 21,
    tradesPerDay: 17.19,
    tradesPerDayReason: null,
    premiumKnownCount: 361,
    premiumMissingCount: 0,
    medianPremiumAtRisk: 4209,
    totalPremiumAtRisk: 2105199.84,
    premiumReason: null,
    netPnl: 102051.47,
    pnlPerDollarRisked: 0.048476,
    pnlPerDollarRiskedReason: null,
    medianEpisodePnl: -418.58,
    bodyPnl: 48376.74,
    bodyEpisodeCount: 351,
    bodyPnlReason: null,
    dteKnownCount: 351,
    zeroDteShare: 0.2678,
    zeroDteReason: null,
    exactFillShare: 0.4155,
    hasReconstructedFills: true,
    // 口径来源：默认＝ 4 月的真实形状（208/361 由汇总 ORDER 行构建 → 断裂）。
    fillDetailedCount: 153,
    aggregateOnlyCount: 208,
    fillProvenanceUnknownCount: 0,
    fillDetailedShare: 0.4238,
    basisBreak: true,
    basisBreakReason:
      '208/361 笔由汇总 ORDER 行构建（fill_allocations=0，无明细成交）：'
      + '风险金额分母被低估（管线自身标记 audit_only_not_execution_cash_flow），'
      + '本区间每美元口径不可与全明细区间比较，须断开显示',
    feesTotal: 24989.62,
    feesMissingCount: 0,
    feePctOfPremiumAtRisk: 0.011871,
    feePctOfPremiumAtRiskReason: null,
    grossPctOfPremiumAtRisk: 0.060347,
    grossPctOfPremiumAtRiskReason: null,
    pnlPerDollarExcludingTopN: 0.00946,
    grossPctExcludingTopN: 0.021311,
    excludingTopNCount: 356,
    excludingTopNReason: null,
    ...overrides,
  };
}

/** 全明细成交的干净月份（口径未断裂）。 */
function cleanStats(
  overrides: Partial<PersonalEdgeDisciplineStats> = {},
): PersonalEdgeDisciplineStats {
  return stats({
    exactFillShare: 1,
    hasReconstructedFills: false,
    fillDetailedCount: 327,
    aggregateOnlyCount: 0,
    fillDetailedShare: 1,
    basisBreak: false,
    basisBreakReason: null,
    ...overrides,
  });
}

function ready(): PersonalEdgeResponse {
  return {
    schemaVersion: 'journal-personal-edge/1.0',
    dataState: 'ready',
    accountKey: 'default_moomoo_us',
    buildId: 1,
    buildKey: 'abc',
    sourceKind: 'csv_batch',
    computedAt: '2026-08-04T12:00:00Z',
    firstOpenedAt: '2026-03-04T14:00:00Z',
    lastClosedAt: '2026-07-20T20:00:00Z',
    closedEpisodeCount: 1437,
    excludedOpenCount: 0,
    excludedMissingPnlCount: 0,
    underlyingMinEpisodeCount: 5,
    underlyings: [],
    smallSampleUnderlyingCount: 0,
    holdTimeBuckets: [],
    holdUnknownCount: 0,
    dteBuckets: [],
    dteUnknown: null,
    monthly: [],
    discipline: {
      monthly: [
        { month: '2026-04', ...stats() },
        {
          month: '2026-07',
          ...cleanStats({
            n: 327,
            tradesPerDay: 25.15,
            medianPremiumAtRisk: 8946,
            totalPremiumAtRisk: 3284642,
            pnlPerDollarRisked: 0.017973,
            medianEpisodePnl: -1259.11,
            bodyPnl: -30637.94,
            zeroDteShare: 0.4755,
            // 毛 1.22% ≤ 门槛 1.25% → 净为负：这一格必须显式标「不及门槛」。
            grossPctOfPremiumAtRisk: 0.012234,
            feePctOfPremiumAtRisk: 0.012543,
            pnlPerDollarExcludingTopN: -0.031988,
            grossPctExcludingTopN: -0.019409,
            excludingTopNCount: 322,
          }),
        },
        {
          month: '2026-08',
          ...cleanStats({
            n: 6,
            tradesPerDay: 3,
            medianPremiumAtRisk: null,
            totalPremiumAtRisk: null,
            premiumReason: '无可用开仓现金流（opening_cash_flow 缺失）',
            pnlPerDollarRisked: null,
            pnlPerDollarRiskedReason: '无可用开仓现金流（opening_cash_flow 缺失）',
            bodyPnl: null,
            bodyEpisodeCount: null,
            bodyPnlReason: '样本 6 笔 < 15 笔，去掉最好/最差各 5 笔后不成立',
            feesTotal: null,
            feePctOfPremiumAtRisk: null,
            feePctOfPremiumAtRiskReason: '无可用开仓现金流（opening_cash_flow 缺失）',
            grossPctOfPremiumAtRisk: null,
            grossPctOfPremiumAtRiskReason: '无可用开仓现金流（opening_cash_flow 缺失）',
            pnlPerDollarExcludingTopN: null,
            grossPctExcludingTopN: null,
            excludingTopNCount: null,
            excludingTopNReason: '风险金额已知样本 6 笔 < 15 笔，剔除最好 5 笔后不成立',
          }),
        },
      ],
      currentWindow: {
        requestedTradingDays: 20,
        startDate: '2026-06-22',
        endDate: '2026-07-20',
        ...cleanStats({
          n: 470,
          tradesPerDay: 23.5,
          medianPremiumAtRisk: 8580,
          pnlPerDollarRisked: 0.032429,
          bodyPnl: 56677.35,
          grossPctOfPremiumAtRisk: 0.046642,
          feePctOfPremiumAtRisk: 0.014214,
          pnlPerDollarExcludingTopN: 0.000247,
          grossPctExcludingTopN: 0.014448,
          excludingTopNCount: 465,
        }),
      },
      bodyTrimCount: 5,
      bodyMinEpisodeCount: 15,
      excludeTopN: 5,
      fillDetailedGoverns: '口径可比性由 fill_detailed_share 判定',
    },
    monthBasis: 'opened_at_utc_minus_4_approximation',
    limitations: [LIMITATION],
  };
}

describe('DisciplineMonthlyPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchPersonalEdge).mockReset();
  });

  it('renders one row per month with per-dollar edge, size and frequency', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    const april = (await screen.findByText('2026-04')).closest('tr') as HTMLElement;
    expect(within(april).getByText('17.2')).toBeInTheDocument();
    expect(within(april).getByText('$4,209')).toBeInTheDocument();
    expect(within(april).getByText('$2,105,200')).toBeInTheDocument();
    expect(within(april).getByText('+4.85%')).toBeInTheDocument();
    expect(within(april).getByText('−$419')).toBeInTheDocument();
    expect(within(april).getByText('+$48,377')).toBeInTheDocument();
    expect(within(april).getByText('27%')).toBeInTheDocument();

    const july = (await screen.findByText('2026-07')).closest('tr') as HTMLElement;
    expect(within(july).getByText('25.2')).toBeInTheDocument();
    expect(within(july).getByText('+1.80%')).toBeInTheDocument();
    expect(within(july).getByText('−$30,638')).toBeInTheDocument();

    // 当前窗口脚注：用户能看到「现在」相对自己历史的位置。
    expect(
      screen.getByText(/当前窗口（近 20 个交易日 · 2026-06-22 → 2026-07-20）/),
    ).toBeInTheDocument();
  });

  it('renders aggregate-only months as a BROKEN series, never blended', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    // 断裂月份行内显式标注，并带上成因（笔数 + fill_allocations=0）。
    const april = (await screen.findByText('2026-04')).closest('tr') as HTMLElement;
    expect(april.getAttribute('data-basis-break')).toBe('true');
    const badge = within(april).getByText('口径断裂 · 不可与后续月份比较');
    expect(badge.getAttribute('aria-label')).toContain('208/361');
    expect(badge.getAttribute('aria-label')).toContain('fill_allocations=0');
    // 明细成交占比与精确成交占比是两列，且此处不一致（42% vs 42%→数值同为 42%）。
    expect(within(april).getAllByText('42%').length).toBeGreaterThanOrEqual(1);

    // 干净月份不带断裂标记。
    const july = (await screen.findByText('2026-07')).closest('tr') as HTMLElement;
    expect(july.getAttribute('data-basis-break')).toBe('false');
    expect(
      within(july).queryByText('口径断裂 · 不可与后续月份比较'),
    ).not.toBeInTheDocument();

    // 两组之间必须有一条显式断口，防止读成一条连续趋势线。
    const separator = document.querySelector('[data-series-break="true"]');
    expect(separator).not.toBeNull();
    expect(separator?.textContent).toContain('不可比、不可连成一条趋势线');
  });

  it('explains why the broken months are not comparable, with the within-April control', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    const note = await screen.findByLabelText('口径断裂说明');
    expect(note).toHaveTextContent('汇总 ORDER 行');
    expect(note).toHaveTextContent('evidence_summary_json.fill_allocations = 0');
    // 管线自身的标记名必须原文出现——这是「分母不可比」的证据来源。
    expect(note).toHaveTextContent('audit_only_not_execution_cash_flow');
    expect(note).toHaveTextContent('这些月份的每美元口径与后续月份不可比');
    // 同月对照：9.53%（汇总）vs 2.42%（明细），证明是分母假象而非边际衰减。
    expect(note).toHaveTextContent('9.53%');
    expect(note).toHaveTextContent('2.42%');
  });

  it('shows the constant fee toll next to the gross figure and flags below-toll months', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    // 7 月：毛 1.22% ≤ 门槛 1.25% → 净口径必为负，一眼可见。
    const july = (await screen.findByText('2026-07')).closest('tr') as HTMLElement;
    expect(within(july).getByText('+1.22%')).toBeInTheDocument();
    expect(within(july).getByText('1.25%')).toBeInTheDocument();
    const belowToll = within(july).getByText('不及门槛');
    expect(belowToll.getAttribute('aria-label')).toContain('净口径必为负');

    // 4 月：毛 6.03% 远高于门槛 1.19% → 不加标记。
    const april = (await screen.findByText('2026-04')).closest('tr') as HTMLElement;
    expect(within(april).getByText('+6.03%')).toBeInTheDocument();
    expect(within(april).getByText('1.19%')).toBeInTheDocument();
    expect(within(april).queryByText('不及门槛')).not.toBeInTheDocument();
  });

  it('reports the excluding-top-N per-dollar edge per month and for the window', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    // 列头带上 N，口径与 bodyTrimCount 同一个 N。
    expect(await screen.findByText('剔除最好 5 笔')).toBeInTheDocument();
    // 7 月剔除最好 5 笔后为负：尾部赢家撤走就没有边际了。
    const july = (await screen.findByText('2026-07')).closest('tr') as HTMLElement;
    expect(within(july).getByText('−1.94%')).toBeInTheDocument();
    // 说明文案用大白话讲清楚这一列是什么。
    expect(screen.getByText(/剔除最好的 5 笔后的每美元回报/)).toBeInTheDocument();
    // 当前窗口脚注同样带上这一读数。
    expect(screen.getByText(/剔除最好 5 笔 \+1\.44%/)).toBeInTheDocument();
  });

  it('shows 标缺 with the reason instead of zero-filling short samples', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    const august = (await screen.findByText('2026-08')).closest('tr') as HTMLElement;
    const missing = within(august).getAllByText('标缺');
    // 中位仓位 / 风险金额合计 / 每美元回报 / 毛每美元 / 费用门槛 /
    // 剔除最好 5 笔 / 本体盈亏 七个读数全部显式缺席。
    expect(missing).toHaveLength(7);
    expect(
      missing.some((node) => node.getAttribute('aria-label')?.includes('剔除最好 5 笔')),
    ).toBe(true);
    // 原因走共享 Tooltip + aria-label（仓库 UI 治理禁用原生 title）。
    expect(
      missing.some((node) => node.getAttribute('aria-label')?.includes('15 笔')),
    ).toBe(true);
    expect(
      missing.some((node) => node.getAttribute('aria-label')?.includes('opening_cash_flow')),
    ).toBe(true);
  });

  it('carries the endpoint limitations verbatim', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);
    expect(await screen.findByText(`· ${LIMITATION}`)).toBeInTheDocument();
  });

  it('stays honest when Journal has no build', async () => {
    const notBuilt = { ...ready(), dataState: 'not_built' as const, discipline: null };
    vi.mocked(fetchPersonalEdge).mockResolvedValue(notBuilt);
    render(<DisciplineMonthlyPanel />);

    await waitFor(() => {
      expect(screen.getByText(/还没有 Episode 构建/)).toBeInTheDocument();
    });
  });
});
