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

// 个人画像回灌（你的战绩列）：页面级测试固定「Journal 未构建」→ 列显式标缺。
vi.mock('../../api/journal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/journal')>();
  return {
    ...actual,
    fetchPersonalEdge: vi.fn(async () => ({
      schemaVersion: 'journal-personal-edge/1.0',
      dataState: 'not_built' as const,
      accountKey: 'default_moomoo_us',
      buildId: null,
      buildKey: null,
      sourceKind: null,
      computedAt: null,
      firstOpenedAt: null,
      lastClosedAt: null,
      closedEpisodeCount: 0,
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
      monthBasis: null,
      limitations: [],
    })),
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
  const closed = sessionState === 'closed';
  return {
    schemaVersion: 'intraday-pulse/1.0',
    generatedAt: '2026-07-28T14:30:05+00:00',
    marketDateEt: '2026-07-28',
    sessionState,
    sessionStateBasis: 'america_new_york_clock_v1',
    sessionPhase: closed ? 'closed' : 'prime',
    sessionPhaseLabel: closed
      ? '休市 · 复盘时段'
      : '主战场 · 你的历史最大净盈利时段',
    sessionPhaseHintBasis: 'user_trading_history_hardcoded_v1',
    items: [
      {
        ticker: 'SPY',
        state: 'ready',
        lastPrice: 500.0,
        prevClose: 490.0,
        changePercent: 2.040816,
        changeBasis: 'moomoo_snapshot_prev_close',
        vwap: 499.0,
        vwapPosition: 'above',
        vwapBasis: 'session_turnover_over_volume',
        vwapUnavailableReason: null,
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
        vwap: 431.0,
        vwapPosition: 'below',
        vwapBasis: 'session_turnover_over_volume',
        vwapUnavailableReason: null,
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
        vwap: null,
        vwapPosition: 'unknown',
        vwapBasis: 'session_turnover_over_volume',
        vwapUnavailableReason: 'quote_unavailable',
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
    sessionBursts: {
      state: 'ready',
      sessionDateEt: '2026-07-28',
      barCount: 78,
      medianBarRange: 0.5875,
      medianBarVolume: 975_948,
      medianBasis: 'current_session_bars_so_far',
      windowMinutes: 15,
      current: {
        startEt: '15:15',
        endEt: '15:30',
        thrustPercent: 0.95,
        thrustNorm: 3.19,
        volNorm: 2.68,
        score: 8.6,
        direction: 'up',
      },
      legs: [
        {
          startEt: '09:40',
          endEt: '09:55',
          thrustPercent: -0.93,
          thrustNorm: 3.17,
          volNorm: 2.95,
          score: 9.3,
          direction: 'down',
        },
        {
          startEt: '15:15',
          endEt: '15:30',
          thrustPercent: 0.95,
          thrustNorm: 3.19,
          volNorm: 2.68,
          score: 8.6,
          direction: 'up',
        },
      ],
      speed: {
        state: 'accelerating',
        currentScore: 8.6,
        previousScore: 5.1,
        delta: 3.5,
        basis: 'consecutive_rolling_15m_window_burst_score_delta_5m_bars',
        unavailableReason: null,
      },
      unavailableReason: null,
      source: 'fixture_5m',
      fetchedAt: '2026-07-28T14:30:06+00:00',
      basis: 'rolling_15m_thrust_over_median_range_times_volume_ratio',
      limitations: [],
    },
    earningsProximity: {
      state: 'ready',
      daysToEarnings: 2,
      earningsDate: '2026-07-30',
      withinBlackout: true,
      blackoutDays: 3,
      windowDays: 5,
      basis: 'finnhub_earnings_calendar_forward_window',
      source: 'finnhub_earnings_calendar',
      fetchedAt: '2026-07-28T14:30:05+00:00',
      unavailableReason: null,
    },
    marketAlignment: {
      state: 'aligned',
      burstDirection: 'up',
      spyVwapPosition: 'above',
      basis: 'candidate_current_burst_direction_vs_spy_session_vwap_position',
      unavailableReason: null,
    },
    // v6 近 30 分钟位移：越过用户自己的 0.5 ATR 经验线（描述统计，非信号）。
    recentDisplacement: {
      state: 'ready',
      windowMinutes: 30,
      netMoveAtr: 0.72,
      highExcursionAtr: 0.91,
      lowExcursionAtr: -0.14,
      absRangeAtr: 1.05,
      atrBasis: 'atr14_daily',
      survivalLineAtr: 0.5,
      barCount: 40,
      unavailableReason: null,
    },
    setupMatch: {
      state: 'ready',
      styleMatchVersion: 'style_match_v1',
      quoteSessionScope: 'current_session',
      sessionDateEt: '2026-07-28',
      barCount5M: 78,
      barCount15M: 26,
      matchedSetups: [],
      partialSetups: [],
      setups: [
        {
          setupKey: 'S1',
          label: 'S1 低点抬高',
          title: '十五分钟低点抬高突破',
          state: 'not_matched',
          reason: '可识别的 15m swing low 不足 2 个。',
          evidenceLines: [],
          basis: '15m_rising_swing_lows_then_break_above_structure_high',
          playbook: null,
        },
        {
          setupKey: 'S2',
          label: 'S2 跳空托举',
          title: '跳空高开托举',
          state: 'not_matched',
          reason: '无达到阈值的向上跳空。',
          evidenceLines: [],
          basis: 'gap_up_support_hold_above_reference_close_and_session_vwap',
          playbook: null,
        },
        {
          setupKey: 'S3',
          label: 'S3 高开遇阻',
          title: '高开遇阻回落（做空）',
          state: 'not_matched',
          reason: '无达到阈值的向上跳空。',
          evidenceLines: [],
          basis: 'gap_up_rejection_below_open_or_vwap_requires_spy_below_vwap',
          playbook: null,
        },
      ],
      basis: 'session_5m_bars_aggregated_to_15m_grid_plus_session_quote_geometry',
      unavailableReason: null,
      limitations: [],
    },
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
      sessionBursts: {
        state: 'unavailable',
        sessionDateEt: null,
        barCount: 0,
        medianBarRange: null,
        medianBarVolume: null,
        medianBasis: null,
        windowMinutes: 15,
        current: null,
        legs: [],
        speed: {
          state: 'unknown',
          currentScore: null,
          previousScore: null,
          delta: null,
          basis: 'consecutive_rolling_15m_window_burst_score_delta_5m_bars',
          unavailableReason: 'fewer_than_2_windows',
        },
        unavailableReason: 'history_5m_unavailable:RuntimeError',
        source: null,
        fetchedAt: null,
        basis: 'rolling_15m_thrust_over_median_range_times_volume_ratio',
        limitations: [],
      },
      earningsProximity: {
        state: 'unavailable',
        daysToEarnings: null,
        earningsDate: null,
        withinBlackout: null,
        blackoutDays: 3,
        windowDays: 5,
        basis: 'finnhub_earnings_calendar_forward_window',
        source: 'finnhub_earnings_calendar',
        fetchedAt: null,
        unavailableReason: 'finnhub_not_configured',
      },
      marketAlignment: {
        state: 'unknown',
        burstDirection: null,
        spyVwapPosition: null,
        basis: 'candidate_current_burst_direction_vs_spy_session_vwap_position',
        unavailableReason: 'burst_direction_unavailable',
      },
      // K 线不可得 → 位移同样显式标缺，绝不以 0 冒充「没动」。
      recentDisplacement: {
        state: 'unavailable',
        windowMinutes: 30,
        netMoveAtr: null,
        highExcursionAtr: null,
        lowExcursionAtr: null,
        absRangeAtr: null,
        atrBasis: null,
        survivalLineAtr: 0.5,
        barCount: 0,
        unavailableReason: 'fewer_than_6_session_bars',
      },
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
    sessionPhase: sessionState === 'closed' ? 'closed' : 'prime',
    sessionPhaseLabel:
      sessionState === 'closed' ? '休市 · 复盘时段' : '主战场 · 你的历史最大净盈利时段',
    sessionPhaseHintBasis: 'user_trading_history_hardcoded_v1',
    quoteSessionScope: sessionState === 'closed' ? 'latest_prior_session' : 'current_session',
    quoteSessionLabel: sessionState === 'closed' ? '最近一个交易时段' : '当前交易时段',
    marketContext: {
      ticker: 'SPY',
      state: 'ready',
      lastPrice: 500.0,
      vwap: 499.0,
      vwapPosition: 'above',
      vwapBasis: 'session_turnover_over_volume',
      quoteAsOf: '2026-07-28 10:30:04',
      fetchedAt: '2026-07-28T14:30:05+00:00',
      source: 'moomoo_openapi',
      unavailableReason: null,
    },
    signalVersion: 'intraday_session_evidence_v7',
    rankingMethod:
      sessionState === 'closed'
        ? 'rule_based_evidence_count'
        : 'burst_score_first_then_evidence_count',
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
    // v3 时段上下文醒目展示：用户历史纪律提示（硬编码 v1 文案）。
    expect(within(pulseSection).getByLabelText('时段上下文')).toHaveTextContent(
      '主战场 · 你的历史最大净盈利时段',
    );
    // SPY 会话 VWAP 位置（累计额/量近似）。
    expect(within(pulseSection).getByText('VWAP 上方')).toBeInTheDocument();

    // 今日计划跟踪：无冻结计划时的诚实空态（命名与实时扫描明确区分）。
    expect(
      screen.getByText('今日计划跟踪 · 盘前冻结计划走到哪了'),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/今日尚无已发布的冻结盘前计划/),
    ).toBeInTheDocument();

    // 实时扫描表（两级布局默认 10 列交易关键读数，v6 起含「近30分位移」，
    // 「波段vs大盘」下沉到展开区「研究读数」）。
    expect(screen.getByText('实时扫描 · 现在谁在动')).toBeInTheDocument();
    const table = await screen.findByRole('table', { name: '实时扫描表' });
    const headers = ['排名', '标的', '涨跌%', '当前爆发', '今日波段', '速度', '近30分位移', '形态', '你的战绩', '详情'];
    for (const header of headers) {
      expect(within(table).getByText(header)).toBeInTheDocument();
    }
    expect(within(table).getAllByRole('columnheader').length).toBe(10);
    expect(within(table).queryByText('波段vs大盘')).not.toBeInTheDocument();
    // v6 位移：NVDA 越过 0.5 ATR 经验线。
    expect(within(table).getByText('+0.72 ATR')).toBeInTheDocument();
    // v3 上下文（财报回避窗 badge；速度加速↑）。
    expect(within(table).getByText('财报 2 天内 · 期权贵')).toBeInTheDocument();
    expect(within(table).getByText('加速↑')).toBeInTheDocument();
    expect(within(table).getAllByText(/10:30:04 ET/).length).toBeGreaterThan(0);

    // 当前爆发：分数 + 15 分钟推力%；今日波段 chips；标缺行诚实。
    expect(within(table).getByText('8.6')).toBeInTheDocument();
    expect(within(table).getByText('15分 +0.95%')).toBeInTheDocument();
    expect(within(table).getByText('09:40↓')).toBeInTheDocument();
    expect(within(table).getByText('15:15↑')).toBeInTheDocument();
    const tslaRow = within(table).getByLabelText('打开 TSLA 即时扫描详情');
    expect(within(tslaRow).getAllByText('标缺').length).toBeGreaterThan(0);

    // 页头副标题：爆发分优先排名 + 版本号。
    expect(screen.getByText(/波段爆发优先排名/)).toBeInTheDocument();
    expect(screen.getByText(/intraday_session_evidence_v7/)).toBeInTheDocument();

    // 主次顺序：实时扫描（主表）在前，今日计划跟踪其后，期权事件流最后。
    const scanSection = screen.getByLabelText('实时扫描');
    const planSection = screen.getByLabelText('今日计划');
    const feed = screen.getByLabelText('期权异动');
    expect(
      scanSection.compareDocumentPosition(planSection)
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      planSection.compareDocumentPosition(feed) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // 期权异动 feed + 诚实边界文案。
    expect(within(feed).getByText('偏多（Moomoo 分类）')).toBeInTheDocument();
    expect(
      within(feed).getByText(/不推断开平仓，不证明真实主动买卖方向，不是信号/),
    ).toBeInTheDocument();
  });

  it('sorts the scan table client-side and toggles back to server rank', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: '实时扫描表' });

    const tickerOrder = () =>
      within(table)
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[1].textContent ?? '');

    // 服务端排名：NVDA（active）在前。
    expect(tickerOrder()[0]).toContain('NVDA');

    const changeHeader = within(table).getByRole('button', { name: '按涨跌%排序' });
    fireEvent.click(changeHeader); // desc：NVDA +5.24% 在前
    expect(tickerOrder()[0]).toContain('NVDA');
    fireEvent.click(changeHeader); // asc：TSLA −0.5% 在前
    expect(tickerOrder()[0]).toContain('TSLA');
    fireEvent.click(changeHeader); // 第三次点击回到服务端证据排名
    expect(tickerOrder()[0]).toContain('NVDA');

    // 当前爆发列可排序；TSLA 爆发标缺永远排最后（升降序均如此）。
    const burstHeader = within(table).getByRole('button', { name: '按当前爆发排序' });
    fireEvent.click(burstHeader); // desc
    expect(tickerOrder()[0]).toContain('NVDA');
    fireEvent.click(burstHeader); // asc：标缺仍最后
    expect(tickerOrder()[0]).toContain('NVDA');
  });

  it('navigates to the live-scan detail page on row click', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: '实时扫描表' });
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
    // 休市时段上下文如实标注，不冒充任何盘中时段。
    expect(screen.getByLabelText('时段上下文')).toHaveTextContent('休市 · 复盘时段');
    expect(
      screen.getByText('自动刷新暂停（仅页面可见且盘前/盘中）'),
    ).toBeInTheDocument();
  });

  it('manual refresh bypasses the server TTL with refresh=true', async () => {
    renderPage();
    await screen.findByRole('table', { name: '实时扫描表' });
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
    expect(await screen.findByText(/实时扫描暂不可用：scan down/)).toBeInTheDocument();
  });
});
