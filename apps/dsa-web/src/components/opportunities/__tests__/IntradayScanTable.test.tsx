import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IntradayScanTable } from '../IntradayScanTable';
import { fetchNearExpiryContracts } from '../../../api/opportunities';
import { fetchPersonalEdge } from '../../../api/journal';
import type { PersonalEdgeResponse } from '../../../types/journal';
import type {
  IntradayRecentDisplacement,
  IntradaySetupKey,
  IntradaySetupMatch,
  IntradaySetupMatchProfile,
  IntradaySnapshotOnlyRow,
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

/**
 * v6 近 30 分钟位移 fixture：默认 K 线不足（标缺），各用例按需覆盖。
 * 0.5 ATR 是用户自己 766 笔样本的经验线——描述统计，非预测、非信号。
 */
function displacementProfile(
  overrides: Partial<IntradayRecentDisplacement> = {},
): IntradayRecentDisplacement {
  return {
    state: 'insufficient_bars',
    windowMinutes: 30,
    netMoveAtr: null,
    highExcursionAtr: null,
    lowExcursionAtr: null,
    absRangeAtr: null,
    atrBasis: null,
    survivalLineAtr: 0.5,
    barCount: 3,
    unavailableReason: 'fewer_than_6_session_bars',
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

// 个人画像回灌（你的战绩列）：默认「Journal 未构建」→ 单元格显式标缺。
const { personalEdgeNotBuilt } = vi.hoisted(() => ({
  personalEdgeNotBuilt: () => ({
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
  }),
}));

vi.mock('../../../api/journal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/journal')>();
  return {
    ...actual,
    fetchPersonalEdge: vi.fn(async () => personalEdgeNotBuilt()),
  };
});

function personalEdgeReady(
  underlyings: PersonalEdgeResponse['underlyings'],
): PersonalEdgeResponse {
  return {
    ...personalEdgeNotBuilt(),
    dataState: 'ready',
    buildId: 3,
    buildKey: 'fixture-build-key',
    sourceKind: 'canonical_evidence_set',
    computedAt: '2026-08-04T14:00:00+00:00',
    firstOpenedAt: '2026-03-04T14:31:57+00:00',
    lastClosedAt: '2026-07-31T17:39:18+00:00',
    closedEpisodeCount: 1653,
    underlyings,
    monthly: [
      { month: '2026-03', n: 37, net: 4934, fees: 654, winRate: 0.3514 },
      { month: '2026-07', n: 543, net: -11914, fees: 77223, winRate: 0.3043 },
    ],
    monthBasis: 'opened_at_utc_minus_4_approximation',
    limitations: [
      '持仓时长与结果存在内生性（止损单天然短），描述统计不是因果结论，不构成建议',
    ],
  };
}

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
    recentDisplacement: displacementProfile(),
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
    signalVersion: 'intraday_session_evidence_v7',
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

describe('IntradayScanTable 默认 10 列网格（v3 上下文并入）', () => {
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
      recentDisplacement: displacementProfile({
        state: 'ready',
        netMoveAtr: 0.72,
        highExcursionAtr: 0.91,
        lowExcursionAtr: -0.14,
        absRangeAtr: 1.05,
        atrBasis: 'atr14_daily',
        barCount: 40,
        unavailableReason: null,
      }),
    });
  }

  it('renders the trading-critical default grid and keeps secondary metrics out of it', () => {
    const response = { ...topResponse(), candidates: [v3Candidate()], universe: ['NVDA'] };
    render(<IntradayScanTable data={response} loading={false} error={null} />);

    const table = screen.getByRole('table', { name: '实时扫描表' });
    // 默认网格恰好 10 列交易关键读数（v6 起含「近30分位移」，「波段vs大盘」下沉）。
    const headers = ['排名', '标的', '涨跌%', '当前爆发', '今日波段', '速度', '近30分位移', '形态', '你的战绩', '详情'];
    for (const header of headers) {
      expect(within(table).getByText(header)).toBeInTheDocument();
    }
    expect(within(table).getAllByRole('columnheader').length).toBe(10);
    // 次要指标移出默认网格（展开行「研究读数」一键可达，不是删除）。
    for (const moved of ['缺口', '量能节奏', 'VWAP', '波幅扩张(ATR)', '财报', '期权异动', '研究状态', '波段vs大盘']) {
      expect(within(table).queryByText(moved)).not.toBeInTheDocument();
    }
    // 排名列显示服务端排名。
    expect(within(table).getByText('#1')).toBeInTheDocument();
    // 财报回避窗内：警示徽标并入标的格次行，回避规则口径随 aria-label 附带。
    const blackoutBadge = within(table).getByText('财报 2 天内 · 期权贵');
    expect(blackoutBadge.getAttribute('aria-label')).toContain('你的回避规则（≤3 天）');
    // 速度：加速↑ + Δ 值。
    expect(within(table).getByText('加速↑')).toBeInTheDocument();
    expect(within(table).getByText('Δ +4.3')).toBeInTheDocument();
  });

  it('moves 波段vs大盘 into the expanded 研究读数 grid without losing its 口径', async () => {
    const response = { ...topResponse(), candidates: [v3Candidate()], universe: ['NVDA'] };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '实时扫描表' });
    fireEvent.click(
      within(table).getByRole('button', { name: '展开 NVDA 研究读数与临期合约' }),
    );
    const readouts = await screen.findByLabelText('NVDA 研究读数');
    expect(within(readouts).getByText('波段vs大盘')).toBeInTheDocument();
    expect(within(readouts).getByText('顺势')).toBeInTheDocument();
    expect(within(readouts).getByText('爆发↑ · SPY VWAP上')).toBeInTheDocument();
    expect(
      within(readouts).getByLabelText(/不是个股自身涨跌方向/),
    ).toBeInTheDocument();
  });

  it('keeps unavailable context honest: 标缺 never pretends safe or aligned', () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '实时扫描表' });
    // 默认 fixture：财报日历标缺 → 标的格次行显式「财报标缺」（未知≠安全），
    // 绝不以无徽标冒充安全；大盘/速度同为标缺。
    const missingBadges = within(table).getAllByText('财报标缺');
    expect(missingBadges.length).toBe(2);
    expect(missingBadges[0].getAttribute('aria-label')).toContain('未知≠安全');
    // 位移同为标缺（K 线不足），绝不以 0 冒充「没动」。
    expect(within(table).getAllByText('K线不足 30 分钟').length).toBe(2);
    // 全部行仍在表中：上下文标注绝不隐藏行。
    expect(within(table).getByLabelText('打开 NVDA 即时扫描详情')).toBeInTheDocument();
    expect(within(table).getByLabelText('打开 MU 即时扫描详情')).toBeInTheDocument();
  });

  it('sorts by current burst with unavailable rows always last and server rank unchanged', () => {
    const ready = v3Candidate();
    const response = {
      ...topResponse(),
      candidates: [candidate({ ticker: 'MU' }), ready],
      universe: ['MU', 'NVDA'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '实时扫描表' });
    fireEvent.click(within(table).getByRole('button', { name: '按当前爆发排序' }));
    const bodyRows = () => within(table).getAllByRole('row').slice(1);
    const tickerOrder = () =>
      bodyRows().map((row) => within(row).getAllByRole('cell')[1]?.textContent ?? '');
    // desc：有爆发分的 NVDA 在前，标缺的 MU 永远最后。
    expect(tickerOrder()[0]).toContain('NVDA');
    // 客户端排序不改写服务端排名：NVDA 仍是 #2。
    expect(within(bodyRows()[0]).getAllByRole('cell')[0]?.textContent).toBe('#2');
    fireEvent.click(within(table).getByRole('button', { name: '按当前爆发排序' }));
    expect(tickerOrder()[0]).toContain('NVDA');
  });
});

describe('IntradayScanTable 今日波段分级 chips（signal v5 grade）', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  function legsCandidate(
    legs: IntradayTopCandidate['sessionBursts']['legs'],
  ): IntradayTopCandidate {
    return candidate({
      sessionBursts: {
        ...candidate().sessionBursts,
        state: 'ready',
        sessionDateEt: '2026-08-04',
        barCount: 40,
        medianBasis: 'current_session_bars_so_far',
        current: {
          startEt: '10:15',
          endEt: '10:30',
          thrustPercent: 1.2,
          thrustNorm: 3.0,
          volNorm: 3.0,
          score: 9.0,
          direction: 'up',
        },
        legs,
        unavailableReason: null,
      },
    });
  }

  it('renders strong legs as solid warning chips and medium legs as outline chips', () => {
    const response = {
      ...topResponse(),
      candidates: [
        legsCandidate([
          {
            startEt: '09:45',
            endEt: '10:00',
            thrustPercent: -5.2,
            thrustNorm: 5.0,
            volNorm: 4.6,
            score: 35.5,
            direction: 'down',
            grade: 'strong',
          },
          {
            startEt: '10:30',
            endEt: '10:45',
            thrustPercent: 1.1,
            thrustNorm: 2.0,
            volNorm: 1.5,
            score: 3.1,
            direction: 'up',
            grade: 'medium',
          },
        ]),
      ],
      universe: ['NVDA'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '实时扫描表' });
    // 强波段＝实底警示 chip。
    const strongChip = within(table).getByText('09:45↓ 强');
    expect(strongChip).toHaveClass('text-warning');
    expect(strongChip).toHaveClass('font-medium');
    // 中波段＝描边 chip。
    const mediumChip = within(table).getByText('10:30↑ 中');
    expect(mediumChip).toHaveClass('border-subtle');
    expect(mediumChip).not.toHaveClass('text-warning');
    // 每波明细（含分级）以可访问 aria-label 附带。
    const detail = within(table).getByLabelText(/波段明细：/);
    expect(detail.getAttribute('aria-label')).toContain('爆发分 35.5（强波段）');
    expect(detail.getAttribute('aria-label')).toContain('爆发分 3.1（中波段）');
  });

  it('renders ungraded legacy legs without inventing a grade', () => {
    const response = {
      ...topResponse(),
      candidates: [
        legsCandidate([
          {
            startEt: '09:40',
            endEt: '09:55',
            thrustPercent: 2.0,
            thrustNorm: 3.0,
            volNorm: 3.1,
            score: 9.3,
            direction: 'up',
          },
        ]),
      ],
      universe: ['NVDA'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '实时扫描表' });
    const chip = within(table).getByText('09:40↑');
    expect(chip).toHaveClass('border-subtle');
    expect(chip.textContent).not.toContain('强');
    expect(chip.textContent).not.toContain('中');
  });

  it('renders — for zero legs and 标缺 when bursts are unavailable', () => {
    const response = {
      ...topResponse(),
      candidates: [legsCandidate([]), candidate({ ticker: 'MU' })],
      universe: ['NVDA', 'MU'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '实时扫描表' });
    expect(within(table).getByText('本时段无记录波段')).toBeInTheDocument();
    expect(within(table).getByText('波段读数不可得')).toBeInTheDocument();
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
    const table = screen.getByRole('table', { name: '实时扫描表' });
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
    // 诚实边界原文收进「完整口径」，一键展开可见（隐藏 ≠ 删除）。
    fireEvent.click(screen.getByRole('button', { name: '展开完整口径说明' }));
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
    const table = screen.getByRole('table', { name: '实时扫描表' });
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
    const table = screen.getByRole('table', { name: '实时扫描表' });
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
    // ≤5 檔时无折叠切换（无可隐藏行）。
    expect(within(section).queryByText(/展开全部/)).not.toBeInTheDocument();
    // 快照未解析标的显式列出，绝不静默消失。
    expect(
      within(section).getByText(/快照未解析 1 檔（供应商无返回行，显式标缺）：GLW/),
    ).toBeInTheDocument();
  });

  it('collapses the snapshot-only list to top 5 with an explicit expand toggle', () => {
    const response = twoTierResponse();
    const extraRow = (ticker: string, changePercent: number): IntradaySnapshotOnlyRow => ({
      ticker,
      state: 'ready',
      lastPrice: 50.0,
      changePercent,
      changeBasis: 'moomoo_snapshot_prev_close',
      sessionHigh: 51.0,
      sessionLow: 49.0,
      volume: 100_000,
      turnover: 5_000_000,
      quoteAsOf: '2026-08-04 10:30:04',
      unavailableReason: null,
    });
    response.universeScan = {
      ...response.universeScan!,
      snapshotUnresolvedSymbols: [],
      snapshotOnly: [
        extraRow('AAPL', 5.1),
        extraRow('TSLA', -4.2),
        extraRow('AMD', 3.3),
        extraRow('META', -2.4),
        extraRow('AMZN', 1.5),
        extraRow('GOOG', 0.6),
        extraRow('NFLX', -0.3),
      ],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const section = screen.getByLabelText('仅快照标的');
    // 收起态只显示前 5 檔；计数仍如实报告全量。
    expect(within(section).getByText('仅快照 · 未做深度分析（7 檔）')).toBeInTheDocument();
    expect(within(section).getByText('AMZN')).toBeInTheDocument();
    expect(within(section).queryByText('GOOG')).not.toBeInTheDocument();
    expect(within(section).queryByText('NFLX')).not.toBeInTheDocument();
    // 一键展开全部（隐藏 ≠ 删除）。
    const toggle = within(section).getByRole('button', { name: '展开全部 7 檔' });
    fireEvent.click(toggle);
    expect(within(section).getByText('GOOG')).toBeInTheDocument();
    expect(within(section).getByText('NFLX')).toBeInTheDocument();
    // 再次点击收起。
    fireEvent.click(within(section).getByRole('button', { name: '收起 · 只显示前 5 檔' }));
    expect(within(section).queryByText('GOOG')).not.toBeInTheDocument();
  });

  it('keeps the legacy single-tier rendering unchanged without universeScan', () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);
    expect(screen.getByText(/扫描 2 个标的/)).toBeInTheDocument();
    expect(screen.queryByLabelText('仅快照标的')).not.toBeInTheDocument();
    expect(screen.queryByText(/其余仅快照/)).not.toBeInTheDocument();
    const table = screen.getByRole('table', { name: '实时扫描表' });
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

  it('renders the premarket basis caption and pre-change chips during the premarket gate', () => {
    const response = twoTierResponse();
    response.universeScan = {
      ...response.universeScan!,
      gateBasis: 'premarket_pre_price_change_then_pre_turnover_v1',
      gateWarnings: [],
      snapshotOnly: response.universeScan!.snapshotOnly.map((row) => {
        if (row.ticker === 'AAPL') {
          return { ...row, preChangePercent: 1.85, preTurnover: 12_000_000 };
        }
        if (row.ticker === 'TSLA') {
          return { ...row, preChangePercent: -0.42, preTurnover: 3_000_000 };
        }
        return { ...row, preChangePercent: null, preTurnover: null };
      }),
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);

    // 页头 + footer 均如实声明盘前口径（恰好两处）。
    expect(
      screen.getAllByText(/盘前异动排序（盘前价 vs 前收 · 盘前成交额次序）/).length,
    ).toBe(2);
    // 仅快照区改按盘前口径描述，行内展示真实盘前涨跌与盘前成交额。
    const section = screen.getByLabelText('仅快照标的');
    expect(within(section).getByText(/按 \|盘前涨跌幅\| 排序（盘前价 vs 前收）/)).toBeInTheDocument();
    expect(within(section).getByText('盘前 +1.85%')).toBeInTheDocument();
    expect(within(section).getByText('$12.0M')).toBeInTheDocument();
    expect(within(section).getByText('盘前 −0.42%')).toBeInTheDocument();
    // 缺盘前字段的行显式「盘前标缺」，绝不以上一常规时段涨跌冒充盘前变动。
    expect(within(section).getByText('盘前标缺')).toBeInTheDocument();
    expect(within(section).queryByText('+2.31%')).not.toBeInTheDocument();
    // 盘前字段可得：无回退警示。
    expect(
      screen.queryByText(/盘前字段不可用 · 当前排序反映上一常规时段/),
    ).not.toBeInTheDocument();
  });

  it('shows the explicit fallback warning chip when premarket fields are unavailable', () => {
    const response = twoTierResponse();
    response.universeScan = {
      ...response.universeScan!,
      gateWarnings: ['premarket_fields_unavailable_ranking_reflects_prior_session'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);

    // 页头警示 chip + footer 各一处，明说当前排序反映上一常规时段。
    expect(
      screen.getAllByText(/盘前字段不可用 · 当前排序反映上一常规时段/).length,
    ).toBe(2);
    // 回退口径下仍按常规涨跌渲染，绝不伪装盘前读数。
    expect(screen.queryByText(/盘前异动排序/)).not.toBeInTheDocument();
    const section = screen.getByLabelText('仅快照标的');
    expect(within(section).getByText('+2.31%')).toBeInTheDocument();
  });

  it('keeps the legacy caption without premarket markers during the regular session', () => {
    render(<IntradayScanTable data={twoTierResponse()} loading={false} error={null} />);

    expect(screen.queryByText(/盘前异动排序/)).not.toBeInTheDocument();
    expect(screen.queryByText(/盘前字段不可用/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/（\|涨跌\|→成交额）· 其余仅快照/),
    ).toBeInTheDocument();
    const section = screen.getByLabelText('仅快照标的');
    expect(within(section).getByText('按 |涨跌幅| 排序 · 闸门之外无爆发/形态/速度读数——缺席即缺席，不以 0 冒充')).toBeInTheDocument();
    expect(within(section).getByText('+2.31%')).toBeInTheDocument();
    expect(within(section).queryByText(/盘前/)).not.toBeInTheDocument();
  });
});

describe('IntradayScanTable 闸门 v2 + 用户钉选 + 今日曾深扫账本', () => {
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
      universe: ['NVDA', 'MU', 'AAPL'],
      candidates: [
        candidate({
          scanTier: 'deep',
          deepLaneReason: {
            promotedBy: 'user_pinned',
            moverRank: null,
            basis: 'momentum15m_then_day_change_v2',
          },
        }),
        candidate({
          ticker: 'MU',
          lastPrice: 100.5,
          scanTier: 'deep',
          deepLaneReason: {
            promotedBy: 'mover_rank',
            moverRank: 1,
            basis: 'momentum15m_then_day_change_v2',
          },
        }),
      ],
      universeScan: {
        mode: 'watchlist_two_tier',
        gateBasis: 'momentum15m_then_day_change_v2',
        gateWarnings: [],
        watchlistTotal: 3,
        watchlistTruncated: false,
        scannedTotal: 3,
        deepLaneCount: 2,
        deepLaneMax: 12,
        deepLane: [
          { ticker: 'NVDA', promotedBy: 'user_pinned', moverRank: null },
          { ticker: 'MU', promotedBy: 'mover_rank', moverRank: 1 },
        ],
        planAlwaysInclude: [],
        userPinned: ['NVDA'],
        gatedOutCount: 1,
        snapshotUnresolvedSymbols: [],
        dayPromotionCap: 30,
        dayPromotionCapReached: false,
        dayLedger: [],
        dayLedgerBasis: 'in_process_since_service_start_resets_on_restart',
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
        ],
        limitations: ['两层扫描：仅晋升标的做深度分析。'],
      },
    };
  }

  it('renders the renamed heading with the deep-lane sub caption in two-tier mode', () => {
    render(<IntradayScanTable data={twoTierResponse()} loading={false} error={null} />);
    expect(screen.getByText('实时扫描 · 现在谁在动')).toBeInTheDocument();
    expect(
      screen.getByText('深度层实时排名，下方为今日曾深扫账本'),
    ).toBeInTheDocument();
  });

  it('renders the 钉选 badge for user-pinned candidates and honest pin counts', () => {
    render(<IntradayScanTable data={twoTierResponse()} loading={false} error={null} />);
    const table = screen.getByRole('table', { name: '实时扫描表' });
    // 用户钉选徽标（与「计划钉选」区分）；异动位徽标不受影响。
    expect(within(table).getByText('钉选')).toBeInTheDocument();
    expect(within(table).queryByText('计划钉选')).not.toBeInTheDocument();
    expect(within(table).getByText('异动 #1')).toBeInTheDocument();
    // footer 如实报告钉选占位不占 K 名额。
    expect(
      screen.getByText(/计划钉选 0 檔 \+ 用户钉选 1 檔始终占深度位（不占 K 名额）/),
    ).toBeInTheDocument();
  });

  it('renders the momentum v2 gate caption in header and footer', () => {
    render(<IntradayScanTable data={twoTierResponse()} loading={false} error={null} />);
    // 页头 + footer 均如实声明 v2 口径（谁现在在动优先、当日涨跌兜底）。
    expect(
      screen.getAllByText(/15分动量优先（谁现在在动）· 当日涨跌兜底/).length,
    ).toBe(2);
  });

  it('shows the explicit warming-up warning when momentum history is cold', () => {
    const response = twoTierResponse();
    response.universeScan = {
      ...response.universeScan!,
      gateBasis: 'abs_change_percent_then_turnover_v1',
      gateWarnings: ['momentum_history_warming_up_ranking_by_day_change'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    // 页头警示 chip + footer 各一处：预热期按当日涨跌排序，绝不静默冒充动量。
    expect(
      screen.getAllByText(/动量样本预热中 · 暂按当日涨跌排序/).length,
    ).toBe(2);
    expect(screen.queryByText(/15分动量优先/)).not.toBeInTheDocument();
  });

  it('renders day-ledger rows with waves, setups and honest as-of below the live table', () => {
    const response = twoTierResponse();
    response.universeScan = {
      ...response.universeScan!,
      dayLedger: [
        {
          ticker: 'ORCL',
          lastSeenAt: '2026-08-04T13:40:00+00:00',
          sessionBurstsLegs: [
            {
              startEt: '09:40',
              endEt: '09:55',
              thrustPercent: 2.4,
              thrustNorm: 4.0,
              volNorm: 3.0,
              score: 12.0,
              direction: 'up',
              grade: 'strong',
            },
          ],
          setupMatchedSetups: ['S1'],
          lastChangePercent: 4.1,
          state: 'rotated_out',
        },
      ],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    const section = screen.getByLabelText('今日曾深扫账本');
    expect(
      within(section).getByText('今日曾深扫 · 波段保留（1 檔）'),
    ).toBeInTheDocument();
    // 行内容：ticker + as-of（ET）+ 最后涨跌 + 分级波段 chip + 形态 + 状态。
    expect(within(section).getByText('ORCL')).toBeInTheDocument();
    expect(within(section).getByText('09:40 ET 深扫')).toBeInTheDocument();
    expect(within(section).getByText('+4.10%')).toBeInTheDocument();
    const chip = within(section).getByText('09:40↑ 强');
    expect(chip).toHaveClass('text-warning');
    expect(within(section).getByText('形态 S1')).toBeInTheDocument();
    expect(within(section).getByText('已轮换出')).toBeInTheDocument();
    // 诚实标注：as-of 不刷新 + 重启后从当前时刻累计。
    expect(
      within(section).getByText(/显示最后一次深扫读数（as-of，不实时刷新）· 重启后从当前时刻累计/),
    ).toBeInTheDocument();
    // 当前深度层标的不出现在账本（数据合同由服务端保证，这里锁定渲染面）。
    expect(within(section).queryByText('NVDA')).not.toBeInTheDocument();
  });

  it('renders no ledger section when the day ledger is empty', () => {
    render(<IntradayScanTable data={twoTierResponse()} loading={false} error={null} />);
    expect(screen.queryByLabelText('今日曾深扫账本')).not.toBeInTheDocument();
  });
});

describe('IntradayScanTable 展开行（研究读数 + 临期合约）', () => {
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

  it('expands secondary metrics and the near-expiry panel via the explicit button', async () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('展开 NVDA 研究读数与临期合约'));

    // 研究读数网格：从默认网格移出的次要指标一键可达。
    const readouts = screen.getByLabelText('NVDA 研究读数');
    expect(within(readouts).getByText('量能节奏')).toBeInTheDocument();
    expect(within(readouts).getByText('2.5×')).toBeInTheDocument();
    expect(within(readouts).getByText('vs 20日全日中位')).toBeInTheDocument();
    expect(within(readouts).getByText('缺口')).toBeInTheDocument();
    expect(within(readouts).getByText('+3.23%')).toBeInTheDocument();
    expect(within(readouts).getByText('波幅扩张(ATR)')).toBeInTheDocument();
    expect(within(readouts).getByText('VWAP 持平')).toBeInTheDocument();
    expect(within(readouts).getByText('0 笔')).toBeInTheDocument();
    // 财报标缺全文与研究状态也在读数网格中，标缺语义不变。
    expect(within(readouts).getByText('财报日历不可得 · 未知≠安全')).toBeInTheDocument();
    expect(within(readouts).getByText('盘中活跃')).toBeInTheDocument();
    expect(within(readouts).getByText('3 项证据支持')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByLabelText('NVDA 临期合约')).toBeInTheDocument();
    });
    expect(navigateMock).not.toHaveBeenCalled();
    expect(fetchNearExpiryContracts).toHaveBeenCalledWith('NVDA', 3, { refresh: false });

    // 再次点击收起。
    fireEvent.click(screen.getByLabelText('展开 NVDA 研究读数与临期合约'));
    expect(screen.queryByLabelText('NVDA 临期合约')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('NVDA 研究读数')).not.toBeInTheDocument();
  });

  it('keeps a single expanded row so the table stays usable', async () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('展开 NVDA 研究读数与临期合约'));
    await waitFor(() => {
      expect(screen.getByLabelText('NVDA 临期合约')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('展开 MU 研究读数与临期合约'));
    await waitFor(() => {
      expect(screen.getByLabelText('MU 临期合约')).toBeInTheDocument();
    });
    expect(screen.queryByLabelText('NVDA 临期合约')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('NVDA 研究读数')).not.toBeInTheDocument();
  });
});

describe('IntradayScanTable 近30分位移列（v6 recent displacement）', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  function displacementResponse(
    displacement: IntradayRecentDisplacement | undefined,
  ): IntradayTopResponse {
    return {
      ...topResponse(),
      candidates: [candidate({ recentDisplacement: displacement })],
      universe: ['NVDA'],
      candidateCount: 1,
    };
  }

  /** 位移单元格＝默认网格第 7 列（排名/标的/涨跌/爆发/波段/速度/位移/…）。 */
  function displacementCellOf(table: HTMLElement): HTMLElement {
    const row = within(table).getByLabelText('打开 NVDA 即时扫描详情');
    return within(row).getAllByRole('cell')[6] as HTMLElement;
  }

  it('emphasises an up move at or above the user 0.5 ATR survival line', () => {
    render(
      <IntradayScanTable
        data={displacementResponse(
          displacementProfile({
            state: 'ready',
            netMoveAtr: 0.72,
            highExcursionAtr: 0.91,
            lowExcursionAtr: -0.14,
            absRangeAtr: 1.05,
            atrBasis: 'atr14_daily',
            barCount: 40,
            unavailableReason: null,
          }),
        )}
        loading={false}
        error={null}
      />,
    );
    const table = screen.getByRole('table', { name: '实时扫描表' });
    const value = within(table).getByText('+0.72 ATR');
    expect(value).toHaveClass('text-up-strong');
    expect(value).toHaveClass('font-medium');
    // 越线时副标题给出区间（本次 MFE/MAE 类比），不显示「未达」。
    expect(within(table).getByText('高 +0.91 ATR · 低 −0.14 ATR')).toBeInTheDocument();
    expect(within(table).queryByText('未达 0.5')).not.toBeInTheDocument();
    // tooltip 原文：口径 + 用户自己的经验线来源 + 实际 ATR 基准。
    const tooltip = within(displacementCellOf(table))
      .getByLabelText(/区间：最高/)
      .getAttribute('aria-label') ?? '';
    expect(tooltip).toContain('近 30 分钟（6×5m）净位移 ÷ ATR');
    expect(tooltip).toContain('0.5 ATR 是你自己 766 笔样本里「活下来」的经验线');
    expect(tooltip).toContain('描述统计，非预测、非信号');
    expect(tooltip).toContain('基准：ATR14 日线');
  });

  it('emphasises a down move beyond the survival line with the 跌 color', () => {
    render(
      <IntradayScanTable
        data={displacementResponse(
          displacementProfile({
            state: 'ready',
            netMoveAtr: -0.83,
            highExcursionAtr: 0.05,
            lowExcursionAtr: -0.95,
            absRangeAtr: 1.0,
            atrBasis: 'intraday_20bar_proxy_x3',
            atrScaleComparability: 'intraday_scale_not_comparable',
            barCount: 18,
            unavailableReason: null,
          }),
        )}
        loading={false}
        error={null}
      />,
    );
    const table = screen.getByRole('table', { name: '实时扫描表' });
    const value = within(table).getByText('−0.83 ATR');
    expect(value).toHaveClass('text-down-strong');
    // 回退基准如实标注为盘中代理，不冒充 ATR14。
    const label = within(displacementCellOf(table))
      .getByLabelText(/区间：最高/)
      .getAttribute('aria-label') ?? '';
    expect(label).toContain('基准：盘中代理（最近 20 根 5m 波幅均值 ×3）');
    // v7：这把尺子随时点伸缩，tooltip 必须直说它不可与 ATR14 行横向比较。
    expect(label).toContain('标尺可比性：');
    expect(label).toContain('不可');
    expect(label).toContain('0.28→0.42→0.20');
  });

  it('says the prior-session fallback is daily-scale and reports how many sessions it used', () => {
    render(
      <IntradayScanTable
        data={displacementResponse(
          displacementProfile({
            state: 'ready',
            netMoveAtr: 0.61,
            highExcursionAtr: 0.7,
            lowExcursionAtr: -0.05,
            absRangeAtr: 0.75,
            atrBasis: 'prior_sessions_true_range_mean',
            atrScaleComparability: 'daily_scale_prior_sessions_approximate',
            atrPriorSessionCount: 2,
            barCount: 30,
            unavailableReason: null,
          }),
        )}
        loading={false}
        error={null}
      />,
    );
    const table = screen.getByRole('table', { name: '实时扫描表' });
    const label = within(displacementCellOf(table))
      .getByLabelText(/区间：最高/)
      .getAttribute('aria-label') ?? '';
    expect(label).toContain('上一批交易时段真实波幅均值（日线量级，当日内恒定）');
    expect(label).toContain('日线量级、可近似比较');
    expect(label).toContain('（取 2 个已结束时段）');
  });

  it('mutes a move inside ±0.5 ATR and says 未达 0.5 instead of pretending', () => {
    render(
      <IntradayScanTable
        data={displacementResponse(
          displacementProfile({
            state: 'ready',
            netMoveAtr: 0.18,
            highExcursionAtr: 0.24,
            lowExcursionAtr: -0.31,
            absRangeAtr: 0.55,
            atrBasis: 'atr14_daily',
            barCount: 26,
            unavailableReason: null,
          }),
        )}
        loading={false}
        error={null}
      />,
    );
    const table = screen.getByRole('table', { name: '实时扫描表' });
    const value = within(table).getByText('+0.18 ATR');
    expect(value).toHaveClass('text-text-3');
    expect(value).not.toHaveClass('text-up-strong');
    expect(within(table).getByText('未达 0.5')).toBeInTheDocument();
  });

  it('marks 标缺 for insufficient bars, unavailable ATR unit and missing payload', () => {
    const table = () => screen.getByRole('table', { name: '实时扫描表' });

    const { unmount } = render(
      <IntradayScanTable
        data={displacementResponse(displacementProfile({ barCount: 4 }))}
        loading={false}
        error={null}
      />,
    );
    const insufficient = displacementCellOf(table());
    expect(within(insufficient).getByText('标缺')).toBeInTheDocument();
    expect(within(insufficient).getByText('K线不足 30 分钟')).toBeInTheDocument();
    expect(
      within(insufficient).getByLabelText(/仅 4 根 5m K 线，不足 6 根/),
    ).toBeInTheDocument();
    unmount();

    const { unmount: unmountUnavailable } = render(
      <IntradayScanTable
        data={displacementResponse(
          displacementProfile({
            state: 'unavailable',
            barCount: 30,
            unavailableReason: 'no_usable_atr_unit_atr14_missing_and_intraday_proxy_zero',
          }),
        )}
        loading={false}
        error={null}
      />,
    );
    const unavailable = displacementCellOf(table());
    expect(within(unavailable).getByText('标缺')).toBeInTheDocument();
    expect(within(unavailable).getByText('ATR 标尺/K线不可得')).toBeInTheDocument();
    expect(
      within(unavailable)
        .getByLabelText(/读数不可得/)
        .getAttribute('aria-label'),
    ).toContain('no_usable_atr_unit_atr14_missing_and_intraday_proxy_zero');
    unmountUnavailable();

    // 旧载荷（无 recent_displacement 字段）同样显式标缺，不发明读数。
    render(
      <IntradayScanTable data={displacementResponse(undefined)} loading={false} error={null} />,
    );
    const missing = displacementCellOf(table());
    expect(within(missing).getByText('标缺')).toBeInTheDocument();
    expect(within(missing).getByText('ATR 标尺/K线不可得')).toBeInTheDocument();
  });
});

describe('IntradayScanTable footer 口径两级展示', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  it('shows one short methodology line by default and the full text behind 完整口径', () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    // 默认只有一行简短口径说明（含 v5 波段分级图例），公式墙收起。
    expect(screen.getByText(/排序与口径说明：盘中排序＝爆发分优先/)).toBeInTheDocument();
    expect(screen.getByText(/强＝爆发分 ≥8 暴动 \/ 中＝≥2.5 持续推升/)).toBeInTheDocument();
    // v6：位移一行也在默认简短口径里（含 0.5 ATR 经验线与「非预测」声明）。
    expect(
      screen.getByText(/0.5 ATR 是你自己 766 笔样本的经验「活下来」线/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/当前爆发＝最近 15 分钟（3 根 5m K 线）/)).not.toBeInTheDocument();

    // 一键展开完整口径：原文逐字可见（隐藏 ≠ 删除）。
    const toggle = screen.getByRole('button', { name: '展开完整口径说明' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/当前爆发＝最近 15 分钟（3 根 5m K 线）/)).toBeInTheDocument();
    expect(screen.getByText(/减速=你的离场信号，R1/)).toBeInTheDocument();
    expect(screen.getByText(/系统标注，用户过滤：不隐藏行、不阻断操作、不参与排序/)).toBeInTheDocument();
    expect(screen.getByText(/期权异动＝最近一页 Moomoo 分类计数，不推断开平仓/)).toBeInTheDocument();
    // 完整口径逐字携带取证数字与全部 caveat。
    expect(screen.getByText(/仅 18.3% 达到 ≥0.5 ATR/)).toBeInTheDocument();
    expect(screen.getByText(/71.2% 达到 ≥0.5 ATR/)).toBeInTheDocument();
    expect(screen.getByText(/84% 的合约 ≤1DTE/)).toBeInTheDocument();
    expect(screen.getByText(/样本窗口恰是你最差的两个月/)).toBeInTheDocument();

    // 再次点击收起。
    fireEvent.click(toggle);
    expect(screen.queryByText(/当前爆发＝最近 15 分钟（3 根 5m K 线）/)).not.toBeInTheDocument();
  });
});

describe('IntradayScanTable 「你的战绩」列（个人画像回灌）', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
    vi.mocked(fetchPersonalEdge).mockReset();
  });

  afterEach(() => {
    // 恢复默认「未构建」实现，避免污染其他 describe。
    vi.mocked(fetchPersonalEdge).mockImplementation(
      async () => personalEdgeNotBuilt(),
    );
  });

  it('renders profitable stats plainly and losing n>=20 with the warning tooltip', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(personalEdgeReady([
      { underlying: 'NVDA', n: 191, net: 5490, winRate: 0.293, fees: 33189 },
      { underlying: 'MU', n: 92, net: -52550, winRate: 0.217, fees: 13882 },
    ]));
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    const table = screen.getByRole('table', { name: '实时扫描表' });
    await waitFor(() => {
      expect(within(table).getByText('+$5K · 29.3%')).toBeInTheDocument();
    });
    expect(within(table).getByText('191 笔')).toBeInTheDocument();
    // 盈利标的：普通展示，无警示语义。
    expect(
      within(table).getByText('+$5K · 29.3%').getAttribute('aria-label'),
    ).toBeNull();

    // 亏损且 n≥20：警示 tint + tooltip（标注不过滤——行仍在、排序不变）。
    const losing = within(table).getByText('−$53K · 21.7%');
    expect(losing).toHaveClass('text-warning');
    expect(
      within(table).getByLabelText('你的历史亏钱标的 · 92 笔 · 净 −$53K · 胜率 21.7%'),
    ).toBeInTheDocument();
    expect(within(table).getByLabelText('打开 MU 即时扫描详情')).toBeInTheDocument();
  });

  it('renders losing stats without warning below the n>=20 threshold', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(personalEdgeReady([
      { underlying: 'NVDA', n: 19, net: -5449, winRate: 0.476, fees: 2083 },
    ]));
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    const table = screen.getByRole('table', { name: '实时扫描表' });
    await waitFor(() => {
      expect(within(table).getByText('−$5K · 47.6%')).toBeInTheDocument();
    });
    expect(within(table).getByText('−$5K · 47.6%')).not.toHaveClass('text-warning');
    expect(within(table).queryByLabelText(/你的历史亏钱标的/)).not.toBeInTheDocument();
  });

  it('renders 样本不足 for tickers below the n>=5 gate', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(personalEdgeReady([
      { underlying: 'NVDA', n: 191, net: 5490, winRate: 0.293, fees: 33189 },
    ]));
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    const table = screen.getByRole('table', { name: '实时扫描表' });
    // MU 不在 n≥5 名单：显式「样本不足」，绝不以 0 冒充战绩。
    await waitFor(() => {
      expect(within(table).getByText('样本不足')).toBeInTheDocument();
    });
    expect(within(table).getByText('<5 笔')).toBeInTheDocument();
  });

  it('renders 标缺 when the personal-edge endpoint is absent', async () => {
    vi.mocked(fetchPersonalEdge).mockRejectedValue(new Error('endpoint down'));
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    const table = screen.getByRole('table', { name: '实时扫描表' });
    await waitFor(() => {
      expect(within(table).getAllByText('标缺').length).toBeGreaterThanOrEqual(2);
    });
    expect(within(table).getAllByText('个人战绩不可得').length).toBe(2);
    // 端点缺席绝不隐藏行。
    expect(within(table).getByLabelText('打开 NVDA 即时扫描详情')).toBeInTheDocument();
    expect(within(table).getByLabelText('打开 MU 即时扫描详情')).toBeInTheDocument();
  });

  it('renders 标缺 when the journal has no episode build (not_built)', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(personalEdgeNotBuilt() as PersonalEdgeResponse);
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    const table = screen.getByRole('table', { name: '实时扫描表' });
    await waitFor(() => {
      expect(within(table).getAllByText('个人战绩不可得').length).toBe(2);
    });
  });

  it('annotates day-ledger rows with the same personal stat semantics', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(personalEdgeReady([
      { underlying: 'ORCL', n: 92, net: -52550, winRate: 0.217, fees: 13882 },
    ]));
    const response = {
      ...topResponse(),
      universeScan: {
        mode: 'watchlist_two_tier' as const,
        gateBasis: 'momentum15m_then_day_change_v2' as const,
        gateWarnings: [],
        watchlistTotal: 3,
        watchlistTruncated: false,
        scannedTotal: 3,
        deepLaneCount: 2,
        deepLaneMax: 12,
        deepLane: [],
        planAlwaysInclude: [],
        userPinned: [],
        gatedOutCount: 1,
        snapshotUnresolvedSymbols: [],
        dayPromotionCap: 30,
        dayPromotionCapReached: false,
        dayLedger: [
          {
            ticker: 'ORCL',
            lastSeenAt: '2026-08-04T13:40:00+00:00',
            sessionBurstsLegs: [],
            setupMatchedSetups: [],
            lastChangePercent: 4.1,
            state: 'rotated_out' as const,
          },
        ],
        dayLedgerBasis: 'in_process_since_service_start_resets_on_restart' as const,
        snapshotOnly: [],
        limitations: [],
      },
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);

    const section = screen.getByLabelText('今日曾深扫账本');
    await waitFor(() => {
      expect(
        within(section).getByText('你的战绩 −$53K · 21.7% · 92 笔'),
      ).toBeInTheDocument();
    });
    expect(
      within(section).getByLabelText('你的历史亏钱标的 · 92 笔 · 净 −$53K · 胜率 21.7%'),
    ).toBeInTheDocument();
    expect(within(section).getByText('已轮换出')).toBeInTheDocument();
  });
});

describe('IntradayScanTable 哑火形态标记（v7，命中才渲染）', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  function fizzleFlag(
    overrides: Partial<NonNullable<IntradayTopCandidate['fizzleFlag']>> = {},
  ): NonNullable<IntradayTopCandidate['fizzleFlag']> {
    return {
      state: 'flagged',
      efficiency: 0.94,
      volNorm: 1.2,
      stratum: 'intraday',
      reason: 'intraday_clean_thrust_with_unremarkable_volume',
      basis:
        'current_15m_window_intraday_stratum_and_efficiency_ge_0.9_and_vol_norm_lt_2.0',
      reference: {
        sample:
          '9173_burst_onsets_21_underlyings_123_sessions_2026-02-05_to_2026-08-03_fresh_onsets_time_ordered_split',
        inSampleRate: 0.268,
        outOfSampleRate: 0.254,
        baseRateIn: 0.478,
        baseRateOut: 0.505,
        nIn: 291,
        nOut: 177,
      },
      ...overrides,
    };
  }

  function burstReadyCandidate(
    flag: IntradayTopCandidate['fizzleFlag'],
  ): IntradayTopCandidate {
    return candidate({
      fizzleFlag: flag,
      sessionBursts: {
        ...candidate().sessionBursts,
        state: 'ready',
        sessionDateEt: '2026-08-04',
        barCount: 12,
        medianBasis: 'current_session_bars_so_far',
        current: {
          startEt: '10:15',
          endEt: '10:30',
          thrustPercent: 1.2,
          thrustNorm: 3.0,
          volNorm: 1.2,
          score: 3.6,
          direction: 'up',
        },
        legs: [],
        unavailableReason: null,
      },
    });
  }

  function renderWith(flag: IntradayTopCandidate['fizzleFlag']) {
    const response = {
      ...topResponse(),
      candidates: [burstReadyCandidate(flag)],
      universe: ['NVDA'],
    };
    render(<IntradayScanTable data={response} loading={false} error={null} />);
    return screen.getByRole('table', { name: '实时扫描表' });
  }

  it('renders a warning-muted chip inside the existing 当前爆发 cell, adding no column', () => {
    const table = renderWith(fizzleFlag());
    // 不新增列：默认网格仍是 10 列。
    expect(within(table).getAllByRole('columnheader').length).toBe(10);
    const chip = within(table).getByText('哑火形态');
    expect(chip).toBeInTheDocument();
    expect(chip.className).toContain('text-warning');
    // 徽标落在「当前爆发」格里（与该窗口的分数同一单元格）。
    const cell = chip.closest('td');
    expect(cell).not.toBeNull();
    expect(within(cell as HTMLElement).getByText('3.6')).toBeInTheDocument();
  });

  it('carries the study rates, the sample and the honest caveat in its tooltip', () => {
    const table = renderWith(fizzleFlag());
    const label = within(table).getByText('哑火形态').getAttribute('aria-label') ?? '';
    expect(label).toContain('26.8%');
    expect(label).toContain('n=291');
    expect(label).toContain('25.4%');
    expect(label).toContain('n=177');
    expect(label).toContain('47.8%');
    expect(label).toContain('50.5%');
    expect(label).toContain('9173_burst_onsets_21_underlyings_123_sessions');
    expect(label).toContain(
      '这是形态描述与历史频率，不是卖出信号；方向本身在样本中约 53%，与掷硬币无实质差别。',
    );
    // 当前窗口的实际读数一并给出，便于本人复核。
    expect(label).toContain('0.94');
    expect(label).toContain('1.20');
  });

  it('renders nothing when the window is not flagged (no clutter)', () => {
    const table = renderWith(
      fizzleFlag({ state: 'not_flagged', reason: 'efficiency_below_0.9' }),
    );
    expect(within(table).queryByText('哑火形态')).not.toBeInTheDocument();
  });

  it('renders nothing when the flag is unavailable (absence is not a claim)', () => {
    const table = renderWith(
      fizzleFlag({
        state: 'unavailable',
        efficiency: null,
        reason: 'window_high_equals_low_efficiency_undefined',
      }),
    );
    expect(within(table).queryByText('哑火形态')).not.toBeInTheDocument();
  });

  it('renders nothing when the payload omits the field entirely (v6 载荷)', () => {
    const table = renderWith(undefined);
    expect(within(table).queryByText('哑火形态')).not.toBeInTheDocument();
  });
});
