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

const LIMITATION = '部分回合的成交明细为重建（has_exact_fill_times=0，集中在 2026-04 前半段）';

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
    ...overrides,
  };
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
          ...stats({
            n: 327,
            tradesPerDay: 25.15,
            medianPremiumAtRisk: 8946,
            totalPremiumAtRisk: 3284642,
            pnlPerDollarRisked: 0.017973,
            medianEpisodePnl: -1259.11,
            bodyPnl: -30637.94,
            zeroDteShare: 0.4755,
            exactFillShare: 1,
            hasReconstructedFills: false,
          }),
        },
        {
          month: '2026-08',
          ...stats({
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
            exactFillShare: 1,
            hasReconstructedFills: false,
          }),
        },
      ],
      currentWindow: {
        requestedTradingDays: 20,
        startDate: '2026-06-22',
        endDate: '2026-07-20',
        ...stats({ n: 470, tradesPerDay: 23.5, medianPremiumAtRisk: 8580, pnlPerDollarRisked: 0.032429, bodyPnl: 56677.35, exactFillShare: 1, hasReconstructedFills: false }),
      },
      bodyTrimCount: 5,
      bodyMinEpisodeCount: 15,
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

  it('marks months whose fills are reconstructed', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    const april = (await screen.findByText('2026-04')).closest('tr') as HTMLElement;
    expect(
      within(april).getByText('成交明细为重建，时点仅供参考'),
    ).toBeInTheDocument();
    expect(within(april).getByText('42%')).toBeInTheDocument();

    const july = (await screen.findByText('2026-07')).closest('tr') as HTMLElement;
    expect(
      within(july).queryByText('成交明细为重建，时点仅供参考'),
    ).not.toBeInTheDocument();
  });

  it('shows 标缺 with the reason instead of zero-filling short samples', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<DisciplineMonthlyPanel />);

    const august = (await screen.findByText('2026-08')).closest('tr') as HTMLElement;
    const missing = within(august).getAllByText('标缺');
    // 中位仓位 / 风险金额合计 / 每美元回报 / 本体盈亏 四个读数全部显式缺席。
    expect(missing).toHaveLength(4);
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
