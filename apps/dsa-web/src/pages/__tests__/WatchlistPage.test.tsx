import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import WatchlistPage from '../WatchlistPage';
import {
  fetchPremarketUniverse,
  savePremarketUniverse,
} from '../../api/opportunities';
import type { PremarketUniverseResponse } from '../../types/opportunities';

const mocks = vi.hoisted(() => ({
  tickers: ['NVDA', 'HK.00700', 'AAPL'],
  loadToday: vi.fn(),
  add: vi.fn(),
  addMany: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('../../api/opportunities', () => ({
  fetchPremarketUniverse: vi.fn(),
  savePremarketUniverse: vi.fn(),
}));

vi.mock('../../stores/regimeStore', () => ({
  useRegimeStore: (
    selector: (state: {
      today: null;
      todayLoading: boolean;
      loadToday: typeof mocks.loadToday;
    }) => unknown,
  ) => selector({
    today: null,
    todayLoading: false,
    loadToday: mocks.loadToday,
  }),
}));

vi.mock('../../stores/userWatchlistStore', () => ({
  useUserWatchlistStore: (
    selector: (state: {
      tickers: string[];
      add: typeof mocks.add;
      addMany: typeof mocks.addMany;
      remove: typeof mocks.remove;
    }) => unknown,
  ) => selector({
    tickers: mocks.tickers,
    add: mocks.add,
    addMany: mocks.addMany,
    remove: mocks.remove,
  }),
}));

vi.mock('../../hooks/useTickerQuotes', () => ({
  useTickerQuotes: () => ({ quotes: {} }),
}));

function universe(
  overrides: Partial<PremarketUniverseResponse> = {},
): PremarketUniverseResponse {
  return {
    schemaVersion: 'premarket-research-universe/1.0',
    configured: false,
    universeVersionKey: null,
    source: 'stock_list_fallback',
    symbols: ['MSFT'],
    limit: 5,
    createdAt: null,
    duplicate: false,
    message: '这是未启用的 STOCK_LIST 建议。',
    ...overrides,
  };
}

describe('WatchlistPage official premarket universe', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.tickers = ['NVDA', 'HK.00700', 'AAPL'];
    mocks.loadToday.mockResolvedValue(undefined);
    mocks.add.mockReturnValue(true);
    mocks.addMany.mockReturnValue(0);
    vi.mocked(fetchPremarketUniverse).mockResolvedValue(universe());
    vi.mocked(savePremarketUniverse).mockResolvedValue(universe({
      configured: true,
      universeVersionKey: 'opru_saved_v1',
      source: 'persisted',
      symbols: ['NVDA', 'AAPL'],
      createdAt: '2026-07-23T13:00:00+00:00',
      message: '已保存新的正式研究池版本。',
    }));
  });

  it('shows an inactive suggestion without silently saving it', async () => {
    render(
      <MemoryRouter>
        <WatchlistPage />
      </MemoryRouter>,
    );

    const card = await screen.findByLabelText('官方盘前研究池');
    expect(card).toHaveTextContent('尚未保存正式版本');
    expect(card).toHaveTextContent('服务端仅建议 MSFT');
    expect(card).toHaveTextContent('STOCK_LIST 建议（未启用）');
    expect(savePremarketUniverse).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '保存为官方盘前研究池' })).toBeEnabled();
  });

  it('saves only the ordered, supported local symbols after an explicit click', async () => {
    render(
      <MemoryRouter>
        <WatchlistPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/忽略 1 个非美股期权标的/)).toHaveTextContent('HK.00700');
    fireEvent.click(screen.getByRole('button', { name: '保存为官方盘前研究池' }));

    await waitFor(() => {
      expect(savePremarketUniverse).toHaveBeenCalledWith(['NVDA', 'AAPL'], 5);
    });
    const card = screen.getByLabelText('官方盘前研究池');
    expect(card).toHaveTextContent('与本地可研究列表一致');
    expect(card).toHaveTextContent('opru_saved_v1');
    expect(card).toHaveTextContent('更新 2026/07/23 09:00:00 ET');
    expect(card).toHaveTextContent('来源 显式保存');
    expect(screen.getByRole('button', { name: '已与官方池一致' })).toBeDisabled();
  });

  it('treats the persisted order as part of the version comparison', async () => {
    mocks.tickers = ['AAPL', 'NVDA'];
    vi.mocked(fetchPremarketUniverse).mockResolvedValue(universe({
      configured: true,
      universeVersionKey: 'opru_existing',
      source: 'persisted',
      symbols: ['NVDA', 'AAPL'],
      createdAt: '2026-07-23T12:00:00+00:00',
    }));

    render(
      <MemoryRouter>
        <WatchlistPage />
      </MemoryRouter>,
    );

    const card = await screen.findByLabelText('官方盘前研究池');
    expect(card).toHaveTextContent('与本地列表不同');
    expect(screen.getByRole('button', { name: '保存为官方盘前研究池' })).toBeEnabled();
  });
});
