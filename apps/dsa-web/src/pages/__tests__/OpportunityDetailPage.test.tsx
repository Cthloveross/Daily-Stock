import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  DailyOpportunityRun,
  OpportunityEvidence,
  OpportunityOptionContextResponse,
  OpportunityOptionEventResponse,
  OpportunityOptionOverviewResponse,
  OpportunityOptionWallLevel,
  OpportunityOptionWallResponse,
} from '../../types/opportunities';
import type { StockHistory } from '../../types/stockHistory';
import OpportunityDetailPage from '../OpportunityDetailPage';

const apiMocks = vi.hoisted(() => ({
  fetchDailyOpportunities: vi.fn(),
  fetchOptionContext: vi.fn(),
  fetchOptionEvents: vi.fn(),
  fetchOptionOverview: vi.fn(),
  fetchOptionWalls: vi.fn(),
  fetchSnapshotDetail: vi.fn(),
  getHistory: vi.fn(),
}));

vi.mock('../../api/opportunities', () => ({
  fetchDailyOpportunities: apiMocks.fetchDailyOpportunities,
  fetchOpportunityOptionContext: apiMocks.fetchOptionContext,
  fetchOpportunityOptionEvents: apiMocks.fetchOptionEvents,
  fetchOpportunityOptionOverview: apiMocks.fetchOptionOverview,
  fetchOpportunityOptionWalls: apiMocks.fetchOptionWalls,
  fetchOpportunitySnapshotDetail: apiMocks.fetchSnapshotDetail,
}));

vi.mock('../../api/stocks', () => ({
  stocksApi: { getHistory: apiMocks.getHistory },
}));

vi.mock('../../components/charts/CandlestickChart', () => ({
  CandlestickChart: ({
    data,
    overlays,
  }: {
    data: unknown[];
    overlays?: Array<{ data: Array<{ value: number }> }>;
  }) => (
    <div
      data-testid="opportunity-chart"
      data-candle-count={data.length}
      data-ema8-last={overlays?.[0]?.data.at(-1)?.value}
      data-ema13-last={overlays?.[1]?.data.at(-1)?.value}
    />
  ),
}));

function evidence(metric: string, value: unknown): OpportunityEvidence {
  return {
    evidenceId: `evidence-${metric}`,
    domain: 'daily_history',
    metric,
    value,
    unit: null,
    status: 'supports',
    source: 'fixture',
    observedAt: '2026-07-22T16:00:00-04:00',
    publishedAt: null,
    fetchedAt: '2026-07-23T08:00:00Z',
    observationWindow: 'prior_completed_session',
    qualityState: 'ready',
    actionability: 'research_input',
    limitations: [],
  };
}

const opportunityRun: DailyOpportunityRun = {
  schemaVersion: '1.0',
  runId: 'opportunity-detail-test',
  runType: 'morning_prior_close',
  marketDateEt: '2026-07-23',
  asOf: '2026-07-23T08:00:00-04:00',
  generatedAt: '2026-07-23T08:00:00-04:00',
  signalVersion: 'fixture-v1',
  rankingMethod: 'rule_based_evidence_count',
  strategyValidationState: 'not_validated',
  strategyValidationMessage: 'Fixture is not a validated trading model.',
  universe: ['COIN'],
  requestedLimit: 1,
  candidateCount: 1,
  runReadiness: [],
  candidates: [{
    candidateId: 'candidate-coin',
    ticker: 'COIN',
    researchState: 'watch_only',
    directionalContext: 'bullish',
    setupTags: ['daily_close_above_prior_20d_high'],
    lastCompletedBarAt: '2026-07-22T16:00:00-04:00',
    referenceSessionDate: '2026-07-22',
    referenceClose: 100,
    referencePriceBasis: 'prior_completed_close',
    source: 'fixture',
    styleMatch: {
      status: 'unknown',
      source: 'fixture',
      matchedRules: [],
      conflictingRules: [],
      unknownFields: [],
    },
    hardGates: [],
    evidence: [
      evidence('last_completed_close', 100),
      evidence('prior_20d_range_position', { priorHigh: 105, priorLow: 90, context: 'inside_range' }),
      evidence('ema8_ema13_alignment', { ema8: 99, ema13: 97, context: 'bullish_alignment' }),
      evidence('volume_vs_prior_20d_median', 1.5),
      evidence('dollar_volume_vs_prior_20d_median', 1.4),
    ],
    readiness: [],
    supportingEvidenceCount: 4,
    dataCompleteness: { state: 'partial', availableCount: 5, expectedCount: 7 },
    unknowns: ['具体合约盘口尚未进入模型'],
  }],
};

const optionOverview: OpportunityOptionOverviewResponse = {
  schemaVersion: '1.0',
  marketDateEt: '2026-07-23',
  generatedAt: '2026-07-23T10:00:00-04:00',
  items: [{
    ticker: 'COIN',
    name: 'Coinbase Global',
    state: 'ready',
    source: 'Moomoo',
    fetchedAt: '2026-07-23T10:00:00-04:00',
    sessionVolumeDate: '2026-07-23',
    openInterestAsOf: '2026-07-22',
    volumeBasis: 'current_session_cumulative',
    openInterestBasis: 'prior_clearing_session',
    volatilityBasis: 'provider_snapshot',
    callVolume: 12_000,
    putVolume: 9_000,
    putCallVolumeRatio: 0.75,
    callOpenInterest: 50_000,
    putOpenInterest: 40_000,
    putCallOpenInterestRatio: 0.8,
    ivPercent: 50,
    ivRankPercent: 80,
    ivPercentilePercent: 85,
    previousIvPercent: 48,
    ivChangePoints: 2,
    hv30dPercent: 42,
    hv30dPercentile: 70,
    hv60dPercent: 40,
    hv60dPercentile: 68,
    hv90dPercent: 38,
    hv90dPercentile: 65,
    hv120dPercent: 36,
    hv120dPercentile: 60,
    hv365dPercent: 34,
    hv365dPercentile: 55,
    ivHv30SpreadPoints: 8,
    message: 'ready',
    limitations: ['IV is a provider snapshot.'],
  }],
};

const optionContext: OpportunityOptionContextResponse = {
  schemaVersion: '1.0',
  marketDateEt: '2026-07-23',
  generatedAt: '2026-07-23T10:00:00-04:00',
  items: [{
    ticker: 'COIN',
    state: 'ready',
    source: 'Moomoo',
    fetchedAt: '2026-07-23T10:00:00-04:00',
    expiry: '2026-08-21',
    atmCallIvPercent: 51,
    message: 'ready',
    limitations: [],
  }],
};

const callOiLevel: OpportunityOptionWallLevel = {
  rank: 1,
  strike: 110,
  distanceFromSpotPercent: 10,
  metricValue: 20_000,
  shareOfBucketPercent: 12,
  unit: 'contracts',
  method: 'sum_open_interest',
  side: 'call',
  metricBasis: 'settled_open_interest_prior_session',
  quoteEvidence: 'partial',
  expiryBreakdown: {
    topExpiries: [
      {
        expiry: '2026-08-21',
        dte: 29,
        metricValue: 12_000,
        shareOfLevelPercent: 60,
        contractCount: 1,
        quote: {
          ivPercent: 42.1,
          bid: null,
          ask: null,
          mark: null,
          quoteAsOf: '2026-07-23 09:59:00',
        },
        quoteEvidence: 'partial',
      },
      {
        expiry: '2026-09-18',
        dte: 57,
        metricValue: 6_000,
        shareOfLevelPercent: 30,
        contractCount: 1,
        quote: { ivPercent: null, bid: null, ask: null, mark: null, quoteAsOf: null },
        quoteEvidence: 'unavailable',
      },
    ],
    other: { expiryCount: 2, metricValue: 2_000, shareOfLevelPercent: 10 },
  },
};

const putOiLevel: OpportunityOptionWallLevel = {
  ...callOiLevel,
  strike: 90,
  distanceFromSpotPercent: -10,
  metricValue: 18_000,
  side: 'put',
  quoteEvidence: null,
  expiryBreakdown: null,
};

const callVolumeLevel: OpportunityOptionWallLevel = {
  ...callOiLevel,
  metricValue: 4_000,
  method: 'sum_session_volume',
  metricBasis: 'current_session_cumulative_volume',
  quoteEvidence: null,
  expiryBreakdown: null,
};

const gammaLevel: OpportunityOptionWallLevel = {
  ...callOiLevel,
  strike: 100,
  distanceFromSpotPercent: 0,
  metricValue: 250_000,
  unit: 'usd_delta_change_per_1pct_move',
  method: 'gross_gamma_concentration_1pct',
  side: 'call_put_aggregate',
  metricBasis: 'model_from_settled_oi_and_snapshot_greeks',
  quoteEvidence: null,
  expiryBreakdown: null,
};

const optionWalls: OpportunityOptionWallResponse = {
  schemaVersion: 'option-wall/1.2',
  marketDateEt: '2026-07-23',
  generatedAt: '2026-07-23T10:00:00-04:00',
  items: [{
    ticker: 'COIN',
    state: 'ready',
    source: 'Moomoo',
    fetchedAt: '2026-07-23T10:00:00-04:00',
    quoteAsOf: '2026-07-23 10:00:00',
    formulaVersion: 'fixture-v1',
    spot: 100,
    atmCallIv: {
      state: 'ready',
      expiry: '2026-08-21',
      strike: 100,
      atmCallIvPercent: 42.5,
      selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot',
    },
    scope: {
      dteMin: 0,
      dteMax: 45,
      expiries: ['2026-08-21'],
      standardContractsOnly: true,
    },
    coverage: {
      requestedContracts: 100,
      snapshotReceivedContracts: 100,
      validContracts: 100,
      coveragePercent: 100,
      failedBatches: 0,
      excludedNonstandardContracts: 0,
      excludedUnknownStandardTypeContracts: 0,
      gammaContracts: 100,
    },
    walls: {
      callOi: [callOiLevel],
      putOi: [putOiLevel],
      callVolume: [callVolumeLevel],
      putVolume: [{ ...callVolumeLevel, strike: 90, distanceFromSpotPercent: -10 }],
      callGammaConcentration: [gammaLevel],
      putGammaConcentration: [gammaLevel],
      grossGammaConcentration: [gammaLevel],
    },
    message: 'ready',
    assumptions: [],
    limitations: ['OI is prior clearing session data.'],
  }],
};

const optionEvents: OpportunityOptionEventResponse = {
  schemaVersion: '1.0',
  marketDateEt: '2026-07-23',
  generatedAt: '2026-07-23T10:00:00-04:00',
  items: [{
    ticker: 'COIN',
    state: 'ready',
    source: 'Moomoo',
    fetchedAt: '2026-07-23T10:00:00-04:00',
    eventAsOf: '2026-07-23 10:00:00',
    allCount: 1,
    events: [{
      eventId: 'event-1',
      optionCode: 'COIN260821C00110000',
      ownerCode: 'US.COIN',
      symbol: 'COIN 260821 110C',
      fillTime: '2026-07-23 09:45:00',
      tickerType: 'option',
      price: 4.25,
      volume: 500,
      turnover: 212_500,
      optionType: 'CALL',
      strikePrice: 110,
      expiry: '2026-08-21',
      dte: 29,
      underlyingPrice: 100,
      bidPrice: 4.2,
      askPrice: 4.3,
      ivPercent: 52,
      totalVolume: 2_000,
      totalOpenInterest: 1_000,
      voRatioPercent: 200,
      delta: 0.45,
      sentiment: 'BULLISH',
      orderTypes: ['SWEEP'],
      strategyType: 'BUY',
    }],
    message: 'ready',
    limitations: ['Provider labels are not trader intent.'],
  }],
};

const history: StockHistory = {
  stockCode: 'COIN',
  stockName: 'Coinbase Global',
  period: '5m',
  source: 'fixture',
  coverageStart: '2026-07-23T09:30:00-04:00',
  coverageEnd: '2026-07-23T09:35:00-04:00',
  lastBarAt: '2026-07-23T09:35:00-04:00',
  data: [
    { date: '2026-07-23T09:30:00-04:00', open: 99, high: 101, low: 98, close: 100, volume: 1_000 },
    { date: '2026-07-23T09:35:00-04:00', open: 100, high: 102, low: 99, close: 101, volume: 1_200 },
  ],
};

const sessionHistory: StockHistory = {
  ...history,
  coverageStart: '2026-07-23T08:00:00-04:00',
  coverageEnd: '2026-07-23T21:00:00-04:00',
  lastBarAt: '2026-07-23T21:00:00-04:00',
  data: [
    { date: '2026-07-23T08:00:00-04:00', open: 49, high: 51, low: 48, close: 50, volume: 500 },
    ...Array.from({ length: 13 }, (_, index) => {
      const close = 100 + index;
      return {
        date: new Date(Date.parse('2026-07-23T09:30:00-04:00') + (index * 5 * 60 * 1000)).toISOString(),
        open: close - 1,
        high: close + 1,
        low: close - 2,
        close,
        volume: 1_000 + index,
      };
    }),
    { date: '2026-07-23T16:05:00-04:00', open: 198, high: 202, low: 197, close: 200, volume: 800 },
    { date: '2026-07-23T21:00:00-04:00', open: 299, high: 302, low: 298, close: 300, volume: 100 },
  ],
};

const dailyHistory: StockHistory = {
  ...history,
  period: 'daily',
  coverageStart: '2026-07-21',
  coverageEnd: '2026-07-23',
  lastBarAt: '2026-07-23',
  data: [
    { date: '2026-07-21', open: 95, high: 101, low: 94, close: 100, volume: 100_000 },
    { date: '2026-07-22', open: 100, high: 103, low: 99, close: 102, volume: 120_000 },
    { date: '2026-07-23', open: 102, high: 105, low: 101, close: 104, volume: 130_000 },
  ],
};

function renderPage(initialEntry = '/regime/opportunity/COIN') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/regime/opportunity/:ticker" element={<OpportunityDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const officialSnapshotKey = `ops_${'b'.repeat(64)}`;

function snapshotDetailResponse() {
  return {
    schemaVersion: 'opportunity-snapshot-detail/1.0',
    snapshot: {
      schemaVersion: 'opportunity-snapshot/1.0',
      snapshotKey: officialSnapshotKey,
      marketDateEt: '2026-07-22',
      sourceRunId: 'opr_official',
      frozenAt: '2026-07-22T12:05:00+00:00',
      signalVersion: 'fixture-v1',
      candidateCount: 1,
      eligibleCandidateCount: 1,
      validationEligible: true,
      eligibilityReasons: [],
      outcomeProgress: [],
      idempotentReplay: false,
    },
    run: {
      ...opportunityRun,
      runId: 'opr_official',
      marketDateEt: '2026-07-22',
    },
  };
}

describe('OpportunityDetailPage', () => {
  beforeEach(() => {
    apiMocks.fetchDailyOpportunities.mockReset().mockResolvedValue(opportunityRun);
    apiMocks.fetchOptionContext.mockReset().mockResolvedValue(optionContext);
    apiMocks.fetchOptionEvents.mockReset().mockResolvedValue(optionEvents);
    apiMocks.fetchOptionOverview.mockReset().mockResolvedValue(optionOverview);
    apiMocks.fetchOptionWalls.mockReset().mockResolvedValue(optionWalls);
    apiMocks.fetchSnapshotDetail.mockReset().mockResolvedValue(snapshotDetailResponse());
    apiMocks.getHistory.mockReset().mockResolvedValue(history);
  });

  it('binds to the official frozen snapshot instead of a fresh scan when snapshotKey is present', async () => {
    renderPage(`/regime/opportunity/COIN?snapshotKey=${officialSnapshotKey}`);

    expect(await screen.findByText(/官方快照 ops_bbbbbbbb…/)).toBeInTheDocument();
    expect(screen.getByText(/冻结于 2026-07-22T12:05:00\+00:00/)).toBeInTheDocument();
    expect(apiMocks.fetchSnapshotDetail).toHaveBeenCalledWith(officialSnapshotKey);
    // 冻结候选存在时不得再用即时扫描覆盖榜单证据。
    expect(apiMocks.fetchDailyOpportunities).not.toHaveBeenCalled();
    expect(screen.queryByText('即时扫描 · 未绑定官方快照')).not.toBeInTheDocument();
    // 信号日来自冻结 run，而不是今天的即时扫描。
    expect(screen.getAllByText(/信号日 2026-07-22/).length).toBeGreaterThan(0);
  });

  it('shows the frozen-vs-current drift strip only under official binding', async () => {
    apiMocks.fetchOptionWalls.mockResolvedValue({
      ...optionWalls,
      items: [{ ...optionWalls.items[0], spot: 103, quoteAsOf: '2026-07-23 10:00:00' }],
    });
    renderPage(`/regime/opportunity/COIN?snapshotKey=${officialSnapshotKey}`);

    expect(await screen.findByLabelText('冻结与当前差异')).toHaveTextContent('+3.00%');
    expect(screen.getByLabelText('冻结与当前差异')).toHaveTextContent('冻结基准 close 100');
    expect(screen.getByLabelText('冻结与当前差异')).toHaveTextContent('不改变冻结榜单结论');
  });

  it('hides the drift strip on live scans and when spot evidence is missing', async () => {
    renderPage();
    expect(await screen.findByText('即时扫描 · 未绑定官方快照')).toBeInTheDocument();
    expect(screen.queryByLabelText('冻结与当前差异')).not.toBeInTheDocument();
  });

  it('falls back to a labelled live scan when the official snapshot is unavailable', async () => {
    apiMocks.fetchSnapshotDetail.mockRejectedValue(new Error('404'));
    renderPage(`/regime/opportunity/COIN?snapshotKey=${officialSnapshotKey}`);

    expect(await screen.findByText(/官方快照读取失败，以下为即时扫描结果。/)).toBeInTheDocument();
    expect(apiMocks.fetchDailyOpportunities).toHaveBeenCalledWith(['COIN'], 1, { refresh: false });
    expect(screen.getByText('即时扫描 · 未绑定官方快照')).toBeInTheDocument();
  });

  it('labels the page as a live scan when no snapshotKey is provided', async () => {
    renderPage();

    expect(await screen.findByText('即时扫描 · 未绑定官方快照')).toBeInTheDocument();
    expect(apiMocks.fetchSnapshotDetail).not.toHaveBeenCalled();
    expect(apiMocks.fetchDailyOpportunities).toHaveBeenCalledWith(['COIN'], 1, { refresh: false });
  });

  it('updates the model interval when switching from 1D to 5D', async () => {
    renderPage();

    const summaryHeading = screen.getByRole('heading', { name: '专业结论与波动情景' });
    const summary = summaryHeading.closest('section');
    expect(summary).not.toBeNull();

    await within(summary as HTMLElement).findByText('约 68% 模型终值区间 · 1 个交易日');
    expect(within(summary as HTMLElement).getByText('当前定位 · 等待确认')).toBeInTheDocument();
    expect(within(summary as HTMLElement).getByText('执行前门禁')).toBeInTheDocument();
    expect(
      within(summary as HTMLElement).getByText(/价格结构与量能尚未同时确认/),
    ).toBeInTheDocument();
    const range = summary?.querySelector('.text-2xl');
    expect(range).not.toBeNull();
    const oneDayRange = range?.textContent;
    expect(oneDayRange).toMatch(/^\$[\d,.]+ – \$[\d,.]+$/);

    fireEvent.click(screen.getByRole('button', { name: '5D' }));

    expect(await within(summary as HTMLElement).findByText('约 68% 模型终值区间 · 5 个交易日')).toBeInTheDocument();
    await waitFor(() => expect(range?.textContent).not.toBe(oneDayRange));
    expect(screen.getByRole('button', { name: '5D' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders only the active research tab content', async () => {
    renderPage();

    expect(await screen.findByText('查看各期限历史波动率')).toBeInTheDocument();
    expect(screen.queryByText('查看当日成交量集中位')).not.toBeInTheDocument();
    expect(screen.queryByText('概率区间怎么算')).not.toBeInTheDocument();
    expect(screen.queryByText(/BUY \/ SELL、情绪与订单类型/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '期权墙' }));
    expect(screen.getByText('查看当日成交量集中位')).toBeInTheDocument();
    expect(screen.queryByText('查看各期限历史波动率')).not.toBeInTheDocument();
    expect(screen.queryByText('概率区间怎么算')).not.toBeInTheDocument();
    expect(screen.queryByText(/BUY \/ SELL、情绪与订单类型/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /异常成交/ }));
    expect(screen.getByText(/BUY \/ SELL、情绪与订单类型/)).toBeInTheDocument();
    expect(screen.queryByText('查看各期限历史波动率')).not.toBeInTheDocument();
    expect(screen.queryByText('查看当日成交量集中位')).not.toBeInTheDocument();
    expect(screen.queryByText('概率区间怎么算')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '数据说明' }));
    expect(screen.getByText('概率区间怎么算')).toBeInTheDocument();
    expect(screen.getByText('使用限制与执行门禁')).toBeInTheDocument();
    expect(screen.queryByText('查看各期限历史波动率')).not.toBeInTheDocument();
    expect(screen.queryByText('查看当日成交量集中位')).not.toBeInTheDocument();
    expect(screen.queryByText(/BUY \/ SELL、情绪与订单类型/)).not.toBeInTheDocument();
  });

  it('renders per-level expiry breakdown with explicit missing-quote markers in the walls tab', async () => {
    renderPage();

    await screen.findByText('查看各期限历史波动率');
    fireEvent.click(screen.getByRole('button', { name: '期权墙' }));

    expect(
      screen.getByText('到期分布 · OI＝T-1 清算 · 报价证据部分缺失'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '2026-08-21 · DTE 29 · 占该位 60% · IV 42.1% · Bid/Ask/Mark 标缺（快照未含盘口报价） · 2026-07-23 09:59:00',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '2026-09-18 · DTE 57 · 占该位 30% · IV 标缺 · Bid/Ask/Mark 标缺（快照未含盘口报价）',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('其余 2 个到期日 · 占该位 10%')).toBeInTheDocument();
    expect(
      screen.getByText('标缺字段为快照未提供的数据，未用估算或旧值回填。'),
    ).toBeInTheDocument();
    // Honesty framing stays: concentration context, not dealer GEX.
    expect(
      screen.getByText(/总 Gamma 为绝对值集中度，不是 dealer GEX 或 gamma flip/),
    ).toBeInTheDocument();
  });

  it('defaults intraday charts to regular hours and recomputes EMA from the selected visible bars', async () => {
    apiMocks.getHistory.mockResolvedValue(sessionHistory);
    renderPage();

    const chart = await screen.findByTestId('opportunity-chart');
    expect(chart).toHaveAttribute('data-candle-count', '13');
    expect(screen.getByRole('button', { name: '使用常规时段 09:30–16:00 ET' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('基于当前可见 13 根 K 线重算')).toBeInTheDocument();
    expect(screen.getByText(/K线 5m · 07\/23 10:30 ET/)).toBeInTheDocument();
    expect(screen.queryByText(/K线 5m · 07\/23 21:00 ET/)).not.toBeInTheDocument();
    const regularEma8 = chart.getAttribute('data-ema8-last');
    const regularEma13 = chart.getAttribute('data-ema13-last');

    fireEvent.click(screen.getByRole('button', { name: '使用含盘前盘后 04:00–20:00 ET' }));

    expect(chart).toHaveAttribute('data-candle-count', '15');
    expect(screen.getByText('基于当前可见 15 根 K 线重算')).toBeInTheDocument();
    expect(chart.getAttribute('data-ema8-last')).not.toBe(regularEma8);
    expect(chart.getAttribute('data-ema13-last')).not.toBe(regularEma13);
    expect(screen.getByRole('button', { name: '使用含盘前盘后 04:00–20:00 ET' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/K线 5m · 07\/23 16:05 ET/)).toBeInTheDocument();
  });

  it('hides the session selector on daily candles', async () => {
    apiMocks.getHistory.mockImplementation((
      _ticker: string,
      period: string,
    ) => Promise.resolve(period === 'daily' ? dailyHistory : sessionHistory));
    renderPage();

    const chartSection = screen.getByRole('heading', { name: '价格结构' }).closest('section');
    expect(chartSection).not.toBeNull();
    await within(chartSection as HTMLElement).findByTestId('opportunity-chart');

    fireEvent.click(within(chartSection as HTMLElement).getByRole('button', { name: '1D' }));

    await waitFor(() => expect(apiMocks.getHistory).toHaveBeenLastCalledWith(
      'COIN',
      'daily',
      400,
      { refresh: false },
    ));
    expect(await within(chartSection as HTMLElement).findByText(/日线（无盘前盘后切换）/)).toBeInTheDocument();
    expect(within(chartSection as HTMLElement).queryByRole('group', { name: '美股交易时段' })).not.toBeInTheDocument();
    expect(within(chartSection as HTMLElement).getByTestId('opportunity-chart')).toHaveAttribute('data-candle-count', '3');
    expect(screen.getByText('K线 daily · 2026-07-23')).toBeInTheDocument();
  });
});
