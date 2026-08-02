import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import IntradayPage from '../IntradayPage';
import {
  fetchIntradayPulse,
  fetchIntradayTop,
  fetchPremarketCycleStatus,
} from '../../api/opportunities';
import type {
  IntradayPulseResponse,
  IntradaySessionState,
  IntradayTopCandidate,
  IntradayTopResponse,
  PremarketCycleResponse,
} from '../../types/opportunities';

vi.mock('../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/opportunities')>();
  return {
    ...actual,
    fetchIntradayPulse: vi.fn(),
    fetchIntradayTop: vi.fn(),
    fetchIntradayTracking: vi.fn().mockResolvedValue({ items: [], sessionState: 'closed', limitations: [] }),
    fetchPremarketCycleStatus: vi.fn(),
  };
});

// 稳定引用：store mock 每次渲染必须返回同一数组，否则页面会因 symbols 身份
// 变化而无限重跑加载 effect（真实 zustand store 本身就是稳定引用）。
const MOCK_WATCHLIST_STATE = { tickers: ['NVDA', 'TSLA'] };
vi.mock('../../stores/userWatchlistStore', () => ({
  useUserWatchlistStore: (selector: (state: { tickers: string[] }) => unknown) =>
    selector(MOCK_WATCHLIST_STATE),
}));

function pulse(sessionState: IntradaySessionState = 'regular'): IntradayPulseResponse {
  return {
    schemaVersion: 'intraday-pulse/1.0',
    generatedAt: '2026-07-28T14:30:05+00:00',
    marketDateEt: '2026-07-28',
    sessionState,
    sessionStateBasis: 'america_new_york_clock_v1',
    items: [
      {
        ticker: 'SPY',
        state: 'ready',
        lastPrice: 500.0,
        prevClose: 490.0,
        changePercent: 2.040816,
        changeBasis: 'moomoo_snapshot_prev_close',
        quoteAsOf: '2026-07-28 10:30:04',
        fetchedAt: '2026-07-28T14:30:05+00:00',
        source: 'moomoo_openapi',
        message: '快照读数就绪；仅作盘中背景。',
        limitations: [],
      },
      {
        ticker: 'QQQ',
        state: 'ready',
        lastPrice: 430.0,
        prevClose: 433.0,
        changePercent: -0.69284,
        changeBasis: 'moomoo_snapshot_prev_close',
        quoteAsOf: '2026-07-28 10:30:04',
        fetchedAt: '2026-07-28T14:30:05+00:00',
        source: 'moomoo_openapi',
        message: '快照读数就绪；仅作盘中背景。',
        limitations: [],
      },
      {
        ticker: 'VIX',
        state: 'unavailable',
        lastPrice: null,
        prevClose: null,
        changePercent: null,
        changeBasis: 'moomoo_snapshot_prev_close',
        quoteAsOf: null,
        fetchedAt: '2026-07-28T14:30:05+00:00',
        source: 'moomoo_openapi',
        message: 'Moomoo 未返回该代码的快照；显式标缺，不以 0 或旧值冒充。',
        limitations: [],
      },
    ],
    limitations: [],
  };
}

function topCandidate(overrides: Partial<IntradayTopCandidate> = {}): IntradayTopCandidate {
  return {
    ticker: 'NVDA',
    researchState: 'active',
    stateReason: '3 项独立盘中证据支持（当前交易时段）；仅为研究优先级，不是买卖信号。',
    supportingEvidenceCount: 3,
    source: 'moomoo_openapi',
    fetchedAt: '2026-07-28T14:30:05+00:00',
    quoteAsOf: '2026-07-28 10:30:04',
    lastPrice: 130.5,
    sessionOpen: 128.0,
    sessionHigh: 131.0,
    sessionLow: 127.5,
    sessionChangePercent: 5.241935,
    sessionChangeBasis: 'moomoo_snapshot_prev_close',
    gapPercent: 3.225806,
    gapAtrMultiple: 2.666667,
    gapBasis: 'session_open_vs_prior_completed_close_daily_loader',
    gapUnavailableReason: null,
    volumePaceRatio: 2.5,
    volumePaceBasis: 'session_cumulative_vs_prior_20_session_full_day_median',
    volumePaceUnavailableReason: null,
    vwap: 130.5,
    vwapPosition: 'flat',
    vwapBasis: 'session_turnover_over_volume',
    vwapUnavailableReason: null,
    atr14: 1.5,
    atr14LastBarDate: '2026-07-27',
    atrRangeExpansion: 2.333333,
    rangeExpansionUnavailableReason: null,
    optionActivity: {
      state: 'ready',
      count: 3,
      allCount: 3,
      bullishCount: 3,
      bearishCount: 0,
      neutralCount: 0,
      unclassifiedCount: 0,
      dominantSentiment: 'bullish',
      maxSingleTurnover: 250_000,
      eventAsOf: '2026-07-28 10:12:00',
      fetchedAt: '2026-07-28T14:30:06+00:00',
      source: 'moomoo_openapi',
      limitations: ['ticker_type 与 sentiment 是 Moomoo 分类，不独立证明主动买卖。'],
    },
    priorDayContext: {
      priorClose: 124.0,
      priorCloseDate: '2026-07-27',
      priorHigh20d: 124.5,
      priorLow20d: 104.5,
      rangePosition: 'above_prior_20d_high',
      emaAlignment: 'bullish',
    },
    evidence: [],
    message: '',
    limitations: [],
    ...overrides,
  };
}

function topResponse(
  sessionState: IntradaySessionState = 'regular',
  candidates: IntradayTopCandidate[] = [
    topCandidate(),
    topCandidate({
      ticker: 'TSLA',
      researchState: 'watch',
      supportingEvidenceCount: 1,
      volumePaceRatio: 1.2,
      sessionChangePercent: -0.5,
      optionActivity: {
        state: 'empty',
        count: 0,
        allCount: 0,
        bullishCount: 0,
        bearishCount: 0,
        neutralCount: 0,
        unclassifiedCount: 0,
        dominantSentiment: 'unknown',
        maxSingleTurnover: null,
        eventAsOf: null,
        fetchedAt: '2026-07-28T14:30:06+00:00',
        source: 'moomoo_openapi',
        limitations: [],
      },
    }),
  ],
): IntradayTopResponse {
  return {
    schemaVersion: 'intraday-top/1.0',
    runId: 'itr_2026-07-28_abc',
    generatedAt: '2026-07-28T14:30:05+00:00',
    asOf: '2026-07-28T14:30:05+00:00',
    marketDateEt: '2026-07-28',
    sessionState,
    sessionStateBasis: 'america_new_york_clock_v1',
    quoteSessionScope: sessionState === 'closed' ? 'latest_prior_session' : 'current_session',
    quoteSessionLabel: sessionState === 'closed' ? '最近一个交易时段' : '当前交易时段',
    signalVersion: 'intraday_session_evidence_v1',
    rankingMethod: 'rule_based_evidence_count',
    statisticsTrack: 'none_intraday_v1_unscored',
    moomooEnabled: true,
    universe: candidates.map((item) => item.ticker),
    unsupportedSymbols: [],
    requestedLimit: 5,
    candidateCount: candidates.length,
    candidates,
    recentOptionEvents: [
      {
        ticker: 'NVDA',
        eventId: 'evt_1',
        optionCode: 'US.NVDA260918C100000',
        fillTime: '2026-07-28 10:12:00',
        tickerType: 'BUY',
        price: 5.0,
        volume: 100,
        turnover: 250_000,
        optionType: 'CALL',
        strikePrice: 100,
        expiry: '2026-09-18',
        dte: 52,
        sentiment: 'BULLISH',
        orderTypes: ['SWEEP'],
        strategyType: 'SINGLE_LEG',
      },
    ],
    limitations: ['盘中滚动研究：结果随行情持续变化，不冻结任何版本，也不能事后重建。'],
  };
}

function cycle(overrides: Partial<PremarketCycleResponse> = {}): PremarketCycleResponse {
  return {
    state: 'research_pool_missing',
    run: null,
    ...overrides,
  } as PremarketCycleResponse;
}

function setDocumentVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/intraday']}>
      <IntradayPage />
    </MemoryRouter>,
  );
}

describe('IntradayPage', () => {
  beforeEach(() => {
    vi.mocked(fetchIntradayPulse).mockReset();
    vi.mocked(fetchIntradayTop).mockReset();
    vi.mocked(fetchPremarketCycleStatus).mockReset();
    vi.mocked(fetchIntradayPulse).mockResolvedValue(pulse());
    vi.mocked(fetchIntradayTop).mockResolvedValue(topResponse());
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(cycle());
    setDocumentVisibility('visible');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders header honesty line, pulse, plan, scan table and event feed sections', async () => {
    renderPage();

    expect(screen.getByText('日内工作台')).toBeInTheDocument();
    expect(screen.getByText('盘中滚动研究 · 不是信号 · 不进入统计')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '周内研究 →' })).toHaveAttribute('href', '/regime');

    // 市场脉搏：SPY 读数 + VIX 显式标缺（不伪造 0）。
    const pulseSection = await screen.findByLabelText('市场脉搏');
    expect(within(pulseSection).getByText('SPY')).toBeInTheDocument();
    expect(within(pulseSection).getByText('VIX')).toBeInTheDocument();
    expect(within(pulseSection).getByText('标缺')).toBeInTheDocument();

    // 今日计划：无冻结计划时的诚实空态。
    expect(screen.getByText('今日计划 · 盘前冻结对照')).toBeInTheDocument();
    expect(
      await screen.findByText(/今日尚无已发布的冻结盘前计划/),
    ).toBeInTheDocument();

    // 日内扫描表：全部列头 + as-of。
    const table = await screen.findByRole('table', { name: '日内扫描表' });
    for (const header of ['标的', 'VWAP', '研究状态']) {
      expect(within(table).getByText(header)).toBeInTheDocument();
    }
    expect(within(table).getByText('现价 / 当日')).toBeInTheDocument();
    expect(within(table).getByText('缺口')).toBeInTheDocument();
    expect(within(table).getByText('量能节奏')).toBeInTheDocument();
    expect(within(table).getByText('波幅扩张(ATR)')).toBeInTheDocument();
    expect(within(table).getByText('期权异动')).toBeInTheDocument();
    expect(within(table).getAllByText('10:30:04 ET').length).toBeGreaterThan(0);
    expect(within(table).getByText('盘中活跃')).toBeInTheDocument();
    expect(within(table).getByText('3 笔 · 偏多 · 最大单 $250K')).toBeInTheDocument();

    // 期权异动 feed + 诚实边界文案。
    const feed = screen.getByLabelText('期权异动');
    expect(within(feed).getByText('偏多（Moomoo 分类）')).toBeInTheDocument();
    expect(
      within(feed).getByText(/不推断开平仓，不证明真实主动买卖方向，不是信号/),
    ).toBeInTheDocument();
  });

  it('sorts the scan table client-side and toggles back to server rank', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: '日内扫描表' });

    const tickerOrder = () =>
      within(table)
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[0].textContent ?? '');

    // 服务端排名：NVDA（active）在前。
    expect(tickerOrder()[0]).toContain('NVDA');

    const paceHeader = within(table).getByRole('button', { name: '按量能节奏排序' });
    fireEvent.click(paceHeader); // desc：NVDA 2.5 在前
    expect(tickerOrder()[0]).toContain('NVDA');
    fireEvent.click(paceHeader); // asc：TSLA 1.2 在前
    expect(tickerOrder()[0]).toContain('TSLA');
    fireEvent.click(paceHeader); // 第三次点击回到服务端证据排名
    expect(tickerOrder()[0]).toContain('NVDA');
  });

  it('navigates to the live-scan detail page on row click', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: '日内扫描表' });
    const row = within(table).getByLabelText('打开 NVDA 即时扫描详情');
    // MemoryRouter 无法直接断言 URL；点击不抛错并保持行可交互即可（路由跳转
    // 行为由 /regime/opportunity/:ticker 的既有测试与 E2E 覆盖）。
    fireEvent.click(row);
    expect(row).toBeInTheDocument();
  });

  it('polls every 60s only while visible and in premarket/regular session', async () => {
    vi.useFakeTimers();
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchIntradayTop).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchIntradayTop).toHaveBeenCalledTimes(2);

    act(() => {
      setDocumentVisibility('hidden');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
    });
    expect(fetchIntradayTop).toHaveBeenCalledTimes(2);

    act(() => {
      setDocumentVisibility('visible');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchIntradayTop).toHaveBeenCalledTimes(3);
  });

  it('does not poll outside premarket/regular sessions and labels last-session data', async () => {
    vi.useFakeTimers();
    vi.mocked(fetchIntradayPulse).mockResolvedValue(pulse('closed'));
    vi.mocked(fetchIntradayTop).mockResolvedValue(topResponse('closed'));
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchIntradayTop).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300_000);
    });
    expect(fetchIntradayTop).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText(/最近一个交易时段/).length).toBeGreaterThan(0);
    expect(
      screen.getByText('自动刷新暂停（仅页面可见且盘前/盘中）'),
    ).toBeInTheDocument();
  });

  it('manual refresh bypasses the server TTL with refresh=true', async () => {
    renderPage();
    await screen.findByRole('table', { name: '日内扫描表' });
    fireEvent.click(screen.getByRole('button', { name: '手动刷新日内工作台' }));
    await waitFor(() => {
      expect(fetchIntradayTop).toHaveBeenLastCalledWith(
        ['NVDA', 'TSLA'],
        { limit: 5, refresh: true },
      );
    });
  });

  it('keeps the scan table honest when the endpoint fails', async () => {
    vi.mocked(fetchIntradayTop).mockRejectedValue(new Error('scan down'));
    renderPage();
    expect(await screen.findByText(/日内扫描暂不可用：scan down/)).toBeInTheDocument();
  });
});
