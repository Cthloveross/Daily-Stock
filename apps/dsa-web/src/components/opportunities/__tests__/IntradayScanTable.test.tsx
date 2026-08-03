import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntradayScanTable } from '../IntradayScanTable';
import { fetchNearExpiryContracts } from '../../../api/opportunities';
import type {
  IntradaySetupKey,
  IntradaySetupMatch,
  IntradaySetupMatchProfile,
  IntradayTopCandidate,
  IntradayTopResponse,
  NearExpiryContractResponse,
} from '../../../types/opportunities';

const SETUP_LABELS: Record<IntradaySetupKey, string> = {
  S1: 'S1 低点抬高',
  S2: 'S2 跳空托举',
  S3: 'S3 高开遇阻',
};

const SETUP_TITLES: Record<IntradaySetupKey, string> = {
  S1: '十五分钟低点抬高突破',
  S2: '跳空高开托举',
  S3: '高开遇阻回落（做空）',
};

function setupRow(
  key: IntradaySetupKey,
  state: IntradaySetupMatch['state'],
  overrides: Partial<IntradaySetupMatch> = {},
): IntradaySetupMatch {
  return {
    setupKey: key,
    label: SETUP_LABELS[key],
    title: SETUP_TITLES[key],
    state,
    reason: '无达到阈值的向上跳空。',
    evidenceLines: [],
    basis: 'fixture_basis',
    playbook: null,
    ...overrides,
  };
}

function setupMatchProfile(
  overrides: Partial<IntradaySetupMatchProfile> = {},
): IntradaySetupMatchProfile {
  return {
    state: 'ready',
    styleMatchVersion: 'style_match_v1',
    quoteSessionScope: 'current_session',
    sessionDateEt: '2026-08-04',
    barCount5M: 12,
    barCount15M: 4,
    matchedSetups: [],
    partialSetups: [],
    setups: [
      setupRow('S1', 'not_matched'),
      setupRow('S2', 'not_matched'),
      setupRow('S3', 'not_matched'),
    ],
    basis: 'session_5m_bars_aggregated_to_15m_grid_plus_session_quote_geometry',
    unavailableReason: null,
    limitations: [],
    ...overrides,
  };
}

const navigateMock = vi.fn();

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return {
    ...actual,
    fetchNearExpiryContracts: vi.fn(),
  };
});

function candidate(overrides: Partial<IntradayTopCandidate> = {}): IntradayTopCandidate {
  return {
    ticker: 'NVDA',
    researchState: 'active',
    stateReason: '',
    supportingEvidenceCount: 3,
    source: 'moomoo_openapi',
    fetchedAt: '2026-08-04T14:30:05+00:00',
    quoteAsOf: '2026-08-04 10:30:04',
    lastPrice: 130.5,
    sessionOpen: 128.0,
    sessionHigh: 131.0,
    sessionLow: 127.5,
    sessionChangePercent: 5.24,
    sessionChangeBasis: 'moomoo_snapshot_prev_close',
    gapPercent: 3.23,
    gapAtrMultiple: 2.67,
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
    atr14LastBarDate: '2026-08-01',
    atrRangeExpansion: 2.33,
    rangeExpansionUnavailableReason: null,
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
    setupMatch: setupMatchProfile(),
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
      fetchedAt: '2026-08-04T14:30:06+00:00',
      source: 'moomoo_openapi',
      limitations: [],
    },
    priorDayContext: {
      priorClose: 124.0,
      priorCloseDate: '2026-08-01',
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

function topResponse(): IntradayTopResponse {
  return {
    schemaVersion: 'intraday-top/1.0',
    runId: 'itr_fixture',
    generatedAt: '2026-08-04T14:30:06+00:00',
    asOf: '2026-08-04T14:30:05+00:00',
    marketDateEt: '2026-08-04',
    sessionState: 'regular',
    sessionStateBasis: 'america_new_york_clock_v1',
    sessionPhase: 'prime',
    sessionPhaseLabel: '主战场 · 你的历史最大净盈利时段',
    sessionPhaseHintBasis: 'user_trading_history_hardcoded_v1',
    quoteSessionScope: 'current_session',
    quoteSessionLabel: '当前交易时段',
    marketContext: {
      ticker: 'SPY',
      state: 'unavailable',
      lastPrice: null,
      vwap: null,
      vwapPosition: 'unknown',
      vwapBasis: 'session_turnover_over_volume',
      quoteAsOf: null,
      fetchedAt: null,
      source: 'moomoo_openapi',
      unavailableReason: 'spy_quote_unavailable',
    },
    signalVersion: 'intraday_session_evidence_v4',
    rankingMethod: 'burst_score_first_then_evidence_count',
    statisticsTrack: 'none_intraday_v1_unscored',
    moomooEnabled: true,
    universe: ['NVDA', 'MU'],
    unsupportedSymbols: [],
    requestedLimit: 5,
    candidateCount: 2,
    candidates: [candidate(), candidate({ ticker: 'MU', lastPrice: 100.5 })],
    recentOptionEvents: [],
    limitations: [],
  };
}

function nearExpiryEmpty(ticker: string): NearExpiryContractResponse {
  return {
    schemaVersion: 'near-expiry-contracts/1.0',
    generatedAt: '2026-08-04T14:30:06+00:00',
    marketDateEt: '2026-08-04',
    item: {
      ticker,
      state: 'empty',
      source: 'moomoo_openapi',
      fetchedAt: '2026-08-04T14:30:06+00:00',
      formulaVersion: 'near-expiry-contracts/v1',
      maxDte: 3,
      spot: 100,
      spotAsOf: '2026-08-04 10:30:04',
      openInterestAsOf: '2026-08-01',
      openInterestBasis: 'prior_clearing_session',
      strikeWindow: { percentBand: 5, minStrikesPerSide: 8, basis: 'fixture' },
      coverage: {
        requestedContracts: 0,
        snapshotReceivedContracts: 0,
        observedContracts: 0,
        missingContracts: 0,
        failedBatches: 0,
        excludedNonstandardContracts: 0,
        excludedUnknownStandardTypeContracts: 0,
      },
      expiries: [],
      message: '3 天内没有该标的的期权到期日；这是诚实空态，不是数据失败。',
      limitations: [],
    },
  };
}

describe('IntradayScanTable v3 上下文列（财报/大盘/速度）', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  function v3Candidate(): IntradayTopCandidate {
    return candidate({
      sessionBursts: {
        ...candidate().sessionBursts,
        state: 'ready',
        sessionDateEt: '2026-08-04',
        barCount: 12,
        current: {
          startEt: '10:15',
          endEt: '10:30',
          thrustPercent: 1.2,
          thrustNorm: 3.0,
          volNorm: 3.0,
          score: 9.0,
          direction: 'up',
        },
        legs: [],
        speed: {
          state: 'accelerating',
          currentScore: 9.0,
          previousScore: 4.7,
          delta: 4.3,
          basis: 'consecutive_rolling_15m_window_burst_score_delta_5m_bars',
          unavailableReason: null,
        },
        unavailableReason: null,
      },
      earningsProximity: {
        state: 'ready',
        daysToEarnings: 2,
        earningsDate: '2026-08-06',
        withinBlackout: true,
        blackoutDays: 3,
        windowDays: 5,
        basis: 'finnhub_earnings_calendar_forward_window',
        source: 'finnhub_earnings_calendar',
        fetchedAt: '2026-08-04T14:30:05+00:00',
        unavailableReason: null,
      },
      marketAlignment: {
        state: 'aligned',
        burstDirection: 'up',
        spyVwapPosition: 'above',
        basis: 'candidate_current_burst_direction_vs_spy_session_vwap_position',
        unavailableReason: null,
      },
    });
  }

  it('renders columns, blackout badge, alignment and speed labels', () => {
    const response = { ...topResponse(), candidates: [v3Candidate()], universe: ['NVDA'] };
    render(<IntradayScanTable data={response} loading={false} error={null} />);

    const table = screen.getByRole('table', { name: '日内扫描表' });
    for (const header of ['速度', '大盘', '财报']) {
      expect(within(table).getByText(header)).toBeInTheDocument();
    }
    // 财报回避窗内：醒目「财报 N 天内 · 期权贵」badge + 用户回避规则口径。
    expect(within(table).getByText('财报 2 天内 · 期权贵')).toBeInTheDocument();
    expect(within(table).getByText(/你的回避规则（≤3 天）/)).toBeInTheDocument();
    // 大盘对齐：顺势 + 明确的两侧口径。
    expect(within(table).getByText('顺势')).toBeInTheDocument();
    expect(within(table).getByText('爆发↑ · SPY VWAP上')).toBeInTheDocument();
    // 速度：加速 + Δ 值；footer 携带 R1 离场提示与设计规则。
    expect(within(table).getByText('加速')).toBeInTheDocument();
    expect(within(table).getByText('Δ +4.3')).toBeInTheDocument();
    expect(screen.getByText(/减速=你的离场信号/)).toBeInTheDocument();
    expect(screen.getByText(/系统标注，用户过滤/)).toBeInTheDocument();
  });

  it('keeps unavailable context honest: 标缺 never pretends safe or aligned', () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '日内扫描表' });
    // 默认 fixture：财报日历标缺 → 「未知≠安全」；大盘/速度同为标缺。
    expect(within(table).getAllByText('财报日历不可得 · 未知≠安全').length).toBe(2);
    expect(within(table).getAllByText('SPY 或爆发方向标缺').length).toBe(2);
    // 全部行仍在表中：v3 上下文绝不隐藏行。
    expect(within(table).getByLabelText('打开 NVDA 即时扫描详情')).toBeInTheDocument();
    expect(within(table).getByLabelText('打开 MU 即时扫描详情')).toBeInTheDocument();
  });

  it('sorts by days-to-earnings with unavailable rows always last', () => {
    const ready = v3Candidate();
    const response = {
      ...topResponse(),
      candidates: [candidate({ ticker: 'MU' }), ready],
      universe: ['MU', 'NVDA'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '日内扫描表' });
    fireEvent.click(within(table).getByRole('button', { name: '按财报排序' }));
    const tickerOrder = () =>
      within(table)
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[0]?.textContent ?? '');
    // desc：有数值的 NVDA 在前，标缺的 MU 永远最后。
    expect(tickerOrder()[0]).toContain('NVDA');
    fireEvent.click(within(table).getByRole('button', { name: '按财报排序' }));
    expect(tickerOrder()[0]).toContain('NVDA');
  });
});

describe('IntradayScanTable v4 形态列（styleMatch v1）', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  it('renders the 形态 column with matched solid and partial outline badges', () => {
    const matched = candidate({
      setupMatch: setupMatchProfile({
        matchedSetups: ['S1'],
        partialSetups: ['S3'],
        setups: [
          setupRow('S1', 'matched', {
            reason: '15m 低点连续抬高且已突破结构高点（几何相似，非进场确认）。',
            evidenceLines: [
              '低点序列 10:15 745.20 → 10:45 747.80',
              '结构高点 749.60（10:15–11:00）',
              '突破 11:00 收 750.10 > 749.60',
            ],
            playbook: {
              setupKey: 'S1',
              candidateKey: 'a'.repeat(64),
              status: 'candidate',
              title: 'S1 · 15分钟低点抬高突破（主力打法）',
            },
          }),
          setupRow('S2', 'not_matched'),
          setupRow('S3', 'partial', {
            reason: '形态似 S3 但大盘未走弱（SPY 未处于 VWAP 下方）。',
          }),
        ],
      }),
    });
    render(
      <IntradayScanTable
        data={{ ...topResponse(), candidates: [matched], universe: ['NVDA'] }}
        loading={false}
        error={null}
      />,
    );
    const table = screen.getByRole('table', { name: '日内扫描表' });
    expect(within(table).getByText('形态')).toBeInTheDocument();
    // matched=实底徽标（aria-label 与共享 Tooltip 携带证据行 + 只读 Playbook 标注）。
    const matchedBadge = within(table).getByText('S1 低点抬高');
    expect(matchedBadge).toHaveClass('bg-bg-2');
    expect(matchedBadge.getAttribute('aria-label')).toContain(
      '低点序列 10:15 745.20 → 10:45 747.80',
    );
    expect(matchedBadge.getAttribute('aria-label')).toContain('对应 Playbook: S1（候选）');
    // partial=描边徽标 + 「· 似」后缀 + 原因说明。
    const partialBadge = within(table).getByText(/S3 高开遇阻 · 似/);
    expect(partialBadge).toHaveClass('border-dashed');
    expect(partialBadge.getAttribute('aria-label')).toContain('形态似 S3 但大盘未走弱');
    // not_matched 的 S2 不渲染徽标。
    expect(within(table).queryByText('S2 跳空托举')).not.toBeInTheDocument();
    // footer 诚实边界。
    expect(
      screen.getByText(/形态相似度为 v1 几何检测（5m近似），不含你的进场确认帧（2m\/1m 回踩8\/13EMA），不是信号/),
    ).toBeInTheDocument();
  });

  it('renders — when no setup is similar and 标缺 when inputs are unavailable', () => {
    const none = candidate(); // 默认全 not_matched。
    const unavailable = candidate({
      ticker: 'MU',
      setupMatch: setupMatchProfile({
        state: 'unavailable',
        unavailableReason: 'no_usable_bars_and_missing_quote_inputs',
        setups: [
          setupRow('S1', 'unavailable'),
          setupRow('S2', 'unavailable'),
          setupRow('S3', 'unavailable'),
        ],
      }),
    });
    render(
      <IntradayScanTable
        data={{ ...topResponse(), candidates: [none, unavailable], universe: ['NVDA', 'MU'] }}
        loading={false}
        error={null}
      />,
    );
    const table = screen.getByRole('table', { name: '日内扫描表' });
    expect(within(table).getByText('无相似形态 · 非信号')).toBeInTheDocument();
    expect(within(table).getByText('K线/快照输入不足')).toBeInTheDocument();
    // 标缺行仍完整在表：形态列绝不隐藏候选。
    expect(within(table).getByLabelText('打开 MU 即时扫描详情')).toBeInTheDocument();
  });
});

describe('IntradayScanTable watchlist 两层模式（universeScan）', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  function twoTierResponse(): IntradayTopResponse {
    return {
      ...topResponse(),
      universe: ['NVDA', 'MU', 'AAPL', 'TSLA', 'GLW'],
      candidates: [
        candidate({
          scanTier: 'deep',
          deepLaneReason: {
            promotedBy: 'plan_always_include',
            moverRank: null,
            basis: 'abs_change_percent_then_turnover_v1',
          },
        }),
        candidate({
          ticker: 'MU',
          lastPrice: 100.5,
          scanTier: 'deep',
          deepLaneReason: {
            promotedBy: 'mover_rank',
            moverRank: 1,
            basis: 'abs_change_percent_then_turnover_v1',
          },
        }),
      ],
      universeScan: {
        mode: 'watchlist_two_tier',
        gateBasis: 'abs_change_percent_then_turnover_v1',
        watchlistTotal: 5,
        watchlistTruncated: false,
        scannedTotal: 5,
        deepLaneCount: 2,
        deepLaneMax: 12,
        deepLane: [
          { ticker: 'NVDA', promotedBy: 'plan_always_include', moverRank: null },
          { ticker: 'MU', promotedBy: 'mover_rank', moverRank: 1 },
        ],
        planAlwaysInclude: ['NVDA'],
        gatedOutCount: 3,
        snapshotUnresolvedSymbols: ['GLW'],
        dayPromotionCap: 30,
        dayPromotionCapReached: false,
        snapshotOnly: [
          {
            ticker: 'AAPL',
            state: 'ready',
            lastPrice: 210.1,
            changePercent: 2.31,
            changeBasis: 'moomoo_snapshot_prev_close',
            sessionHigh: 211.0,
            sessionLow: 205.2,
            volume: 1_000_000,
            turnover: 210_000_000,
            quoteAsOf: '2026-08-04 10:30:04',
            unavailableReason: null,
          },
          {
            ticker: 'TSLA',
            state: 'ready',
            lastPrice: 300.0,
            changePercent: -1.05,
            changeBasis: 'moomoo_snapshot_prev_close',
            sessionHigh: 305.0,
            sessionLow: 298.0,
            volume: 500_000,
            turnover: 150_000_000,
            quoteAsOf: '2026-08-04 10:30:04',
            unavailableReason: null,
          },
          {
            ticker: 'GLW',
            state: 'unavailable',
            lastPrice: null,
            changePercent: null,
            changeBasis: 'moomoo_snapshot_prev_close',
            sessionHigh: null,
            sessionLow: null,
            volume: null,
            turnover: null,
            quoteAsOf: null,
            unavailableReason: 'snapshot_missing',
          },
        ],
        limitations: ['两层扫描：仅晋升标的做深度分析。'],
      },
    };
  }

  it('renders deep-lane reason badges, two-tier header and honest footer', () => {
    render(<IntradayScanTable data={twoTierResponse()} loading={false} error={null} />);
    // 页头与 footer 均如实描述两层口径。
    expect(screen.getByText(/全清单 5 檔快照 · 深度分析 2 檔/)).toBeInTheDocument();
    expect(
      screen.getByText(/全清单 5 檔快照 · 深度分析前 12 檔（\|涨跌\|→成交额）· 其余仅快照/),
    ).toBeInTheDocument();
    // 深度位徽标：计划钉选 / 异动排名。
    const table = screen.getByRole('table', { name: '日内扫描表' });
    expect(within(table).getByText('计划钉选')).toBeInTheDocument();
    expect(within(table).getByText('异动 #1')).toBeInTheDocument();
  });

  it('renders the snapshot-only secondary list with 仅快照 caption', () => {
    render(<IntradayScanTable data={twoTierResponse()} loading={false} error={null} />);
    const section = screen.getByLabelText('仅快照标的');
    expect(
      within(section).getByText('仅快照 · 未做深度分析（3 檔）'),
    ).toBeInTheDocument();
    // 行内容：ticker + 涨跌% + 成交额；标缺行显式「标缺」。
    expect(within(section).getByText('AAPL')).toBeInTheDocument();
    expect(within(section).getByText('+2.31%')).toBeInTheDocument();
    expect(within(section).getByText('TSLA')).toBeInTheDocument();
    expect(within(section).getByText('−1.05%')).toBeInTheDocument();
    expect(within(section).getByText('GLW')).toBeInTheDocument();
    // 快照未解析标的显式列出，绝不静默消失。
    expect(
      within(section).getByText(/快照未解析 1 檔（供应商无返回行，显式标缺）：GLW/),
    ).toBeInTheDocument();
  });

  it('keeps the legacy single-tier rendering unchanged without universeScan', () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);
    expect(screen.getByText(/扫描 2 个标的/)).toBeInTheDocument();
    expect(screen.queryByLabelText('仅快照标的')).not.toBeInTheDocument();
    expect(screen.queryByText(/其余仅快照/)).not.toBeInTheDocument();
    const table = screen.getByRole('table', { name: '日内扫描表' });
    expect(within(table).queryByText('计划钉选')).not.toBeInTheDocument();
  });

  it('surfaces the day promotion cap warning when the quota guard trips', () => {
    const response = twoTierResponse();
    response.universeScan = {
      ...response.universeScan!,
      dayPromotionCapReached: true,
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    expect(
      screen.getByText(/今日新晋升深度位已达上限（30 檔 · K 线额度护栏）/),
    ).toBeInTheDocument();
  });
});

describe('IntradayScanTable 临期合约 row interaction', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  it('keeps row click navigating to the detail page', () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('打开 NVDA 即时扫描详情'));

    expect(navigateMock).toHaveBeenCalledWith('/regime/opportunity/NVDA');
  });

  it('expands the near-expiry panel via the explicit button without navigating', async () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('展开 NVDA 临期合约'));

    await waitFor(() => {
      expect(screen.getByLabelText('NVDA 临期合约')).toBeInTheDocument();
    });
    expect(navigateMock).not.toHaveBeenCalled();
    expect(fetchNearExpiryContracts).toHaveBeenCalledWith('NVDA', 3, { refresh: false });

    // 再次点击收起。
    fireEvent.click(screen.getByLabelText('展开 NVDA 临期合约'));
    expect(screen.queryByLabelText('NVDA 临期合约')).not.toBeInTheDocument();
  });

  it('keeps a single expanded row so the table stays usable', async () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('展开 NVDA 临期合约'));
    await waitFor(() => {
      expect(screen.getByLabelText('NVDA 临期合约')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('展开 MU 临期合约'));
    await waitFor(() => {
      expect(screen.getByLabelText('MU 临期合约')).toBeInTheDocument();
    });
    expect(screen.queryByLabelText('NVDA 临期合约')).not.toBeInTheDocument();
  });
});
