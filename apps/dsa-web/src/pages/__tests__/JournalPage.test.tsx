import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import JournalPage from '../JournalPage';

const storeMocks = vi.hoisted(() => ({
  loadStats: vi.fn(),
  loadTrades: vi.fn(),
  loadRealityTest: vi.fn(),
}));

const positionMocks = vi.hoisted(() => ({
  reload: vi.fn(),
  reloadCanonicalPreview: vi.fn(),
  loadDetail: vi.fn(),
  buildAssumedFlat: vi.fn(),
  buildCanonical: vi.fn(),
}));

vi.mock('../../stores/journalStore', () => ({
  useJournalStore: () => ({
    ...storeMocks,
    stats: {
      windowDays: 30,
      closedTradeCount: 20,
      totalPnlNet: 1200,
      winRate: 0.4,
      dteDistribution: { '0DTE': 8, '1-3DTE': 12 },
      winRateByBucket: {},
      realityTest: {
        totalTrades: 20,
        totalPnlNet: 1200,
        topN: 5,
        topNPnlNet: 800,
        topNIds: [],
        pnlWithoutTopN: 400,
      },
    },
    trades: { total: 0, page: 1, perPage: 100, items: [] },
    tradesLoading: false,
  }),
}));

vi.mock('../../hooks/usePositionEpisodes', () => ({
  usePositionEpisodes: () => ({
    list: {
      dataState: 'ready',
      total: 0,
      page: 1,
      perPage: 50,
      items: [],
    },
    loading: false,
    error: null,
    detailById: {},
    detailLoadingId: null,
    detailError: null,
    building: false,
    buildResult: null,
    canonicalPreview: null,
    canonicalPreviewLoading: false,
    canonicalPreviewError: null,
    canonicalBuilding: false,
    canonicalBuildResult: null,
    ...positionMocks,
  }),
}));

vi.mock('../../components/journal/JournalImport', () => ({ default: () => null }));
vi.mock('../../components/journal/DTEDistribution', () => ({ default: () => null }));
vi.mock('../../components/journal/MonthlyReviewPanel', () => ({ default: () => null }));
vi.mock('../../components/journal/RealityTestCard', () => ({ default: () => null }));
vi.mock('../../components/journal/TradeTable', () => ({ default: () => null }));
vi.mock('../../components/journal/StyleBreakdown', () => ({ default: () => null }));
vi.mock('../../components/journal/PnLByDte', () => ({ default: () => null }));
vi.mock('../../components/journal/FrameworkPanel', () => ({ default: () => null }));
vi.mock('../../components/journal/AskJournalChat', () => ({ default: () => null }));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}

describe('JournalPage active journal routing and legacy isolation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens position review by default without loading legacy endpoints', () => {
    render(
      <MemoryRouter initialEntries={['/journal']}>
        <JournalPage />
      </MemoryRouter>,
    );

    expect(screen.getByText('仓位复盘')).toHaveClass('text-text-1');
    expect(screen.getByText('没有符合当前筛选条件的仓位回合。')).toBeInTheDocument();
    expect(storeMocks.loadStats).not.toHaveBeenCalled();
    expect(storeMocks.loadTrades).not.toHaveBeenCalled();
    expect(storeMocks.loadRealityTest).not.toHaveBeenCalled();
  });

  it('presents win rate and 0DTE share as context-dependent metrics', () => {
    render(
      <MemoryRouter initialEntries={['/journal?tab=overview']}>
        <JournalPage />
      </MemoryRouter>,
    );

    expect(screen.getAllByText('40%')).toHaveLength(2);
    expect(screen.getByText('Descriptive only · combine with payoff ratio and fees')).toBeInTheDocument();
    expect(screen.getByText('Trade mix only · compare P&L, fees, and risk by DTE')).toBeInTheDocument();
    expect(screen.queryByText(/break-even/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/dangerous/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Legacy \/ 存档视图/)).toBeInTheDocument();
    expect(storeMocks.loadStats).toHaveBeenCalledTimes(1);
    expect(storeMocks.loadTrades).toHaveBeenCalledTimes(1);
  });

  it('writes Position filters and page reset into the URL', () => {
    render(
      <MemoryRouter initialEntries={['/journal?page=3']}>
        <JournalPage />
        <LocationProbe />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText('筛选标的'), { target: { value: 'nvda' } });
    fireEvent.change(screen.getByLabelText('筛选回合状态'), { target: { value: 'closed' } });
    fireEvent.change(screen.getByLabelText('筛选证据完整度'), { target: { value: 'partial' } });
    fireEvent.change(screen.getByLabelText('案例精选'), { target: { value: 'largest_fee' } });
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));

    expect(screen.getByTestId('location')).toHaveTextContent(
      '/journal?tab=positions&symbol=NVDA&status=closed&completeness=partial&case=largest_fee',
    );
    expect(screen.getByText(/当前为“费用最高”案例精选/)).toBeInTheDocument();
    expect(screen.getByText(/Net 仍可能是条件性数据/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/journal?tab=positions');
  });

  it('keeps an explicit comparison build in the URL until returning to the default build', () => {
    render(
      <MemoryRouter initialEntries={['/journal?tab=positions&build_id=9']}>
        <JournalPage />
        <LocationProbe />
      </MemoryRouter>,
    );

    expect(screen.getByText('正在查看构建 #9')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/journal?tab=positions&build_id=9');
    fireEvent.click(screen.getByRole('button', { name: '回到当前默认构建' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/journal?tab=positions');
  });
});
