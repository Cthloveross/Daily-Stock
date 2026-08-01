import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RegimePage from '../RegimePage';

const mocks = vi.hoisted(() => ({
  loadToday: vi.fn(),
  recompute: vi.fn(),
  add: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('../../stores/regimeStore', () => ({
  useRegimeStore: () => ({
    today: null,
    todayLoading: true,
    loadToday: mocks.loadToday,
    recompute: mocks.recompute,
  }),
}));

vi.mock('../../stores/userWatchlistStore', () => ({
  useUserWatchlistStore: (
    selector: (state: {
      tickers: string[];
      add: typeof mocks.add;
      remove: typeof mocks.remove;
    }) => unknown,
  ) => selector({
    tickers: ['NVDA'],
    add: mocks.add,
    remove: mocks.remove,
  }),
}));

vi.mock('../../hooks/useTickerQuotes', () => ({
  useTickerQuotes: () => ({ quotes: {} }),
}));

vi.mock('../../components/opportunities/DailyOpportunityList', () => ({
  DailyOpportunityList: ({ symbols }: { symbols: string[] }) => (
    <div data-testid="daily-opportunity-list">{symbols.join(',')}</div>
  ),
}));

describe('RegimePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loadToday.mockResolvedValue(undefined);
  });

  it('mounts the opportunity list while the Regime snapshot is still loading', () => {
    render(
      <MemoryRouter>
        <RegimePage />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('daily-opportunity-list')).toHaveTextContent('NVDA');
    expect(screen.getByLabelText('Regime 数据读取中')).toBeInTheDocument();
    expect(mocks.loadToday).toHaveBeenCalledTimes(1);
  });
});
