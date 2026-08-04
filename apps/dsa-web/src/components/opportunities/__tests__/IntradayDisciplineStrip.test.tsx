import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntradayDisciplineStrip } from '../IntradayDisciplineStrip';
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

const LIMITATION = '持仓时长与结果存在内生性（止损单天然短），描述统计不是因果结论，不构成建议';

function stats(overrides: Partial<PersonalEdgeDisciplineStats> = {}): PersonalEdgeDisciplineStats {
  return {
    n: 470,
    tradingDayCount: 20,
    tradesPerDay: 23.5,
    tradesPerDayReason: null,
    premiumKnownCount: 470,
    premiumMissingCount: 0,
    medianPremiumAtRisk: 8580,
    totalPremiumAtRisk: 4512986,
    premiumReason: null,
    netPnl: 146350.16,
    pnlPerDollarRisked: 0.032429,
    pnlPerDollarRiskedReason: null,
    medianEpisodePnl: -1133.39,
    bodyPnl: 56677.35,
    bodyEpisodeCount: 460,
    bodyPnlReason: null,
    dteKnownCount: 468,
    zeroDteShare: 0.4252,
    zeroDteReason: null,
    exactFillShare: 1,
    hasReconstructedFills: false,
    ...overrides,
  };
}

function ready(overrides: Partial<PersonalEdgeDisciplineStats> = {}): PersonalEdgeResponse {
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
        { month: '2026-04', ...stats({ tradesPerDay: 17.19, medianPremiumAtRisk: 4209, pnlPerDollarRisked: 0.048476, bodyPnl: 48376.74, exactFillShare: 0.4155, hasReconstructedFills: true }) },
        { month: '2026-07', ...stats({ tradesPerDay: 24.68, medianPremiumAtRisk: 9690 }) },
      ],
      currentWindow: {
        requestedTradingDays: 20,
        startDate: '2026-06-22',
        endDate: '2026-07-20',
        ...stats(overrides),
      },
      bodyTrimCount: 5,
      bodyMinEpisodeCount: 15,
    },
    monthBasis: 'opened_at_utc_minus_4_approximation',
    limitations: [LIMITATION],
  };
}

describe('IntradayDisciplineStrip', () => {
  beforeEach(() => {
    vi.mocked(fetchPersonalEdge).mockReset();
  });

  it('renders the trailing-window readings with the user own earliest-month baseline', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<IntradayDisciplineStrip />);

    const perDollar = await screen.findByLabelText('每美元回报');
    expect(perDollar).toHaveTextContent('+3.24%');
    // 基线来自端点最早一个可得月份，前端不硬编码任何数字。
    expect(perDollar).toHaveTextContent('4月 +4.85%');

    expect(screen.getByLabelText('日均')).toHaveTextContent('23.5 笔');
    expect(screen.getByLabelText('日均')).toHaveTextContent('4月 17.2 笔');
    expect(screen.getByLabelText('中位仓位')).toHaveTextContent('$8,580');
    expect(screen.getByLabelText('中位仓位')).toHaveTextContent('4月 $4,209');
    expect(screen.getByLabelText('本体盈亏')).toHaveTextContent('+$57K');
    expect(screen.getByLabelText('本体盈亏')).toHaveTextContent('4月 +$48K');

    // as-of：证据 build 与截止日期始终可见。
    expect(screen.getByText(/基于已发布证据 build #1 · 截至 2026-07-20/)).toBeInTheDocument();
    // 镜子不是警报：没有任何建议式措辞。
    expect(screen.queryByText(/你应该/)).not.toBeInTheDocument();
  });

  it('marks每一个缺分母的读数为标缺，never zero-filled', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(
      ready({
        pnlPerDollarRisked: null,
        pnlPerDollarRiskedReason: '无可用开仓现金流（opening_cash_flow 缺失）',
        medianPremiumAtRisk: null,
        premiumReason: '无可用开仓现金流（opening_cash_flow 缺失）',
        bodyPnl: null,
        bodyPnlReason: '样本 8 笔 < 15 笔，去掉最好/最差各 5 笔后不成立',
        tradesPerDay: null,
        tradesPerDayReason: '窗口内无入场交易日',
      }),
    );
    render(<IntradayDisciplineStrip />);

    await waitFor(() => {
      expect(screen.getByLabelText('每美元回报')).toHaveTextContent('标缺');
    });
    expect(screen.getByLabelText('日均')).toHaveTextContent('标缺');
    expect(screen.getByLabelText('中位仓位')).toHaveTextContent('标缺');
    expect(screen.getByLabelText('本体盈亏')).toHaveTextContent('标缺');
  });

  it('carries the endpoint limitations verbatim in the tooltip', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<IntradayDisciplineStrip />);

    const label = await screen.findByText(/规模与频率 · 近 20 个交易日/);
    expect(label.getAttribute('aria-label')).toContain(LIMITATION);

    fireEvent.mouseEnter(label.parentElement as HTMLElement);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(LIMITATION);
  });

  it('flags reconstructed fills inside the current window', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(
      ready({ exactFillShare: 0.42, hasReconstructedFills: true }),
    );
    render(<IntradayDisciplineStrip />);
    expect(await screen.findByText(/成交明细含重建 · 时点仅供参考/)).toBeInTheDocument();
  });

  it('says标缺 when Journal has no build or the endpoint is down', async () => {
    vi.mocked(fetchPersonalEdge).mockRejectedValue(new Error('endpoint down'));
    render(<IntradayDisciplineStrip />);
    expect(
      await screen.findByText(/规模与频率 标缺（Journal 未构建或端点不可得）/),
    ).toBeInTheDocument();
  });
});
