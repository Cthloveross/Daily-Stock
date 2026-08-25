import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import JournalEpisodeReviewPage from '../JournalEpisodeReviewPage';
import {
  emptyEpisodeReviewDraft,
  episodeReviewDraftStorageKey,
  saveEpisodeReviewDraft,
} from '../../components/journal/review/episodeReviewDraft';
import type { PositionEpisodeDetailResponse } from '../../types/journal';
import type { StockHistory } from '../../types/stockHistory';

const apiMocks = vi.hoisted(() => ({
  fetchDetail: vi.fn(),
  createAiReview: vi.fn(),
  fetchLatestReview: vi.fn(),
  fetchReviewHistory: vi.fn(),
  saveReview: vi.fn(),
  getHistory: vi.fn(),
  fetchEpisodes: vi.fn(),
  fetchEpisodeExcursion: vi.fn(),
  fetchExcursionScatter: vi.fn(),
}));

vi.mock('../../api/journal', () => ({
  fetchPositionEpisodeDetail: apiMocks.fetchDetail,
  createPositionEpisodeAiReview: apiMocks.createAiReview,
  fetchLatestPositionEpisodeReviewAnnotation: apiMocks.fetchLatestReview,
  fetchPositionEpisodeReviewAnnotationHistory: apiMocks.fetchReviewHistory,
  savePositionEpisodeReviewAnnotation: apiMocks.saveReview,
  fetchPositionEpisodes: apiMocks.fetchEpisodes,
}));

vi.mock('../../api/journalReviewFlow', () => ({
  fetchEpisodeExcursion: apiMocks.fetchEpisodeExcursion,
  fetchExcursionScatter: apiMocks.fetchExcursionScatter,
}));

vi.mock('../../api/stocks', () => ({
  stocksApi: { getHistory: apiMocks.getHistory },
}));

vi.mock('../../components/charts/CandlestickChart', () => ({
  CandlestickChart: ({ data, markers, overlays, focusedTime, onTimeSelect, timeZone, timeZoneLabel }: {
    data: { time: number | string; close: number }[];
    markers: { id: string; time: number | string; text?: string }[];
    overlays: { label?: string; data: { time: number | string; value: number }[] }[];
    focusedTime?: number | string | null;
    onTimeSelect?: (time: number | string, markerId?: string) => void;
    timeZone?: string;
    timeZoneLabel?: string;
  }) => (
    <div
      data-testid="review-chart"
      data-candle-count={data.length}
      data-focused-time={focusedTime == null ? '' : String(focusedTime)}
      data-time-zone={timeZone}
      data-time-zone-label={timeZoneLabel}
    >
      {overlays.map((overlay) => (
        <span
          key={overlay.label}
          data-testid="chart-overlay"
          data-last-value={overlay.data.at(-1)?.value}
        >
          {overlay.label}
        </span>
      ))}
      {markers.map((marker) => (
        <button
          key={marker.id}
          type="button"
          onClick={() => onTimeSelect?.(marker.time, marker.id)}
        >
          图表事件 {marker.id} {marker.text}
        </button>
      ))}
    </div>
  ),
}));

const detail: PositionEpisodeDetailResponse = {
  dataState: 'ready',
  build: {
    id: 9,
    buildKey: 'build-9',
    builderName: 'position_episode_builder',
    builderVersion: '1.1.0',
    status: 'succeeded',
    sourceBatchIds: [2],
    sourceKind: 'canonical_set',
    sourceWindowStart: '2026-03-04T00:00:00Z',
    sourceCutoffAt: '2026-07-21T00:00:00Z',
    positionEpisodeCount: 1,
    unresolvedEvidenceCount: 0,
    completenessScore: '0.8000',
    openingBoundaryPolicy: 'assumed_flat_unverified',
    assumedFlatUnverified: true,
    executionGroupCount: 0,
    groupFeeAffectedEpisodeCount: 0,
    retainedExecutionGroupFeeTotal: '0.0000000000',
    feeConservationByCurrency: {},
    legFeeAttributionComplete: true,
    partialReasons: ['opening_boundary_unverified'],
    recordedAt: '2026-07-21T01:00:00Z',
  },
  reconciliation: {
    status: 'passed',
    scope: 'partial_window',
    partialWindow: true,
    matchedOrderCount: 1,
    totalOrderCount: 1,
  },
  item: {
    id: 41,
    episodeBuildId: 9,
    strategyEpisodeId: 31,
    episodeKey: 'episode-41',
    lineageKey: 'lineage-41',
    strategyType: 'single_position_unclassified',
    instrument: {
      rawSymbol: 'NVDA260731C00150000',
      assetType: 'option',
      underlying: 'NVDA',
      expiry: '2026-07-31',
      strike: '150.0000000000',
      optionRight: 'C',
      contractMultiplier: '100.0000000000',
      currency: 'USD',
    },
    direction: 'long',
    lifecycleStatus: 'closed',
    openedAt: '2026-07-20T14:32:00Z',
    closedAt: '2026-07-20T14:33:00Z',
    holdSeconds: 60,
    openedQuantity: '1.0000000000',
    closedQuantity: '1.0000000000',
    remainingQuantity: '0.0000000000',
    averageEntryPrice: '2.5000000000',
    averageExitPrice: '3.0000000000',
    realizedPnlGross: '50.0000000000',
    totalFee: '1.2500000000',
    realizedPnlNet: '48.7500000000',
    quality: {
      completenessStatus: 'partial',
      completenessScore: '0.8000',
      constructionBasis: 'mixed',
      contractMultiplierBasis: 'occ',
      openingBoundaryPolicy: 'assumed_flat_unverified',
      assumedFlatUnverified: true,
      leftBoundaryVerified: false,
      isLeftCensored: true,
      isRightCensored: false,
      pnlSummaryEligible: false,
      groupFeeUnallocated: false,
      pnlExclusionReasons: ['boundary_unverified'],
    },
  },
  matching: { method: 'signed_position_zero_crossing' },
  evidenceSummary: { allocationCount: 2 },
  completeness: { leftBoundaryVerified: false },
  provenance: { builderVersion: '1.1.0' },
  evidence: [
    {
      id: 11,
      evidenceKey: 'fill-11',
      evidenceKind: 'fill',
      eventRole: 'open',
      allocationSequence: 0,
      evidenceTime: '2026-07-20T14:32:00Z',
      allocatedQuantity: '1.0000000000',
      allocatedFee: '0.6500000000',
      allocatedCashFlow: '-250.0000000000',
      allocationRatio: '1.0000000000',
      brokerOrderObservationId: 10,
      brokerFillObservationId: 11,
      allocation: { timingPrecision: 'fill_time' },
      provenance: {},
    },
    {
      id: 12,
      evidenceKey: 'order-10',
      evidenceKind: 'order',
      eventRole: 'close',
      allocationSequence: 1,
      evidenceTime: '2026-07-20T14:33:00Z',
      allocatedQuantity: '1.0000000000',
      allocatedFee: '0.6000000000',
      allocatedCashFlow: '300.0000000000',
      allocationRatio: '1.0000000000',
      brokerOrderObservationId: 10,
      brokerFillObservationId: null,
      allocation: { timingPrecision: 'order_time_proxy' },
      provenance: {},
    },
  ],
};

const intraday: StockHistory = {
  stockCode: 'NVDA',
  period: '5m',
  source: 'YFinanceFetcher',
  coverageStart: '2026-07-20T14:30:00Z',
  coverageEnd: '2026-07-20T14:35:00Z',
  lastBarAt: '2026-07-20T14:35:00Z',
  data: [
    { date: '2026-07-20T14:30:00Z', open: 150, high: 152, low: 149, close: 151, volume: 1000 },
    { date: '2026-07-20T14:35:00Z', open: 151, high: 153, low: 150, close: 152, volume: 1200 },
  ],
};

const twoMinute: StockHistory = {
  ...intraday,
  period: '2m',
  source: 'TwoMinuteFetcher',
  coverageStart: '2026-07-20T14:32:00Z',
  coverageEnd: '2026-07-20T14:34:00Z',
  lastBarAt: '2026-07-20T14:34:00Z',
  data: [
    { date: '2026-07-20T14:32:00Z', open: 150, high: 152, low: 149, close: 151, volume: 1000 },
    { date: '2026-07-20T14:34:00Z', open: 151, high: 153, low: 150, close: 152, volume: 1200 },
  ],
};

const daily: StockHistory = {
  stockCode: 'NVDA',
  period: 'daily',
  source: 'DailyFetcher',
  coverageStart: '2026-07-18',
  coverageEnd: '2026-07-20',
  lastBarAt: '2026-07-20',
  data: [
    { date: '2026-07-20', open: 148, high: 153, low: 147, close: 151, volume: 5000 },
  ],
};

const aiReviewResponse = {
  dataState: 'ready',
  analysisMode: 'model_enhanced' as const,
  analysisSource: 'configured_llm' as const,
  episodeId: 41,
  buildId: 9,
  generatedAt: '2026-07-22T00:00:00Z',
  analysisMarkdown: '## 执行质量\n\n**事实：** 买入后卖出。',
  evidenceMarkdown: '## 一句话事实结论\n\n**事实：** 买入后卖出。',
  modelAnalysisMarkdown: '## 执行质量\n\n**推断：** 这次执行可能符合趋势延续逻辑。',
  marketContext: {
    benchmark: 'SPY',
    windowStart: '2026-07-20T13:30:00Z',
    windowEnd: '2026-07-20T20:00:00Z',
    underlyingReturnPct: 1.5,
    benchmarkReturnPct: 0.5,
    relativeReturnPct: 1,
    regimeLabel: '趋势日',
    provenance: ['NVDA 5m', 'SPY 5m'],
  },
  warnings: ['期初边界未验证'],
};

const evidenceReviewResponse = {
  ...aiReviewResponse,
  analysisMode: 'deterministic' as const,
  analysisSource: 'local_evidence_engine' as const,
  analysisMarkdown: '## 一句话事实结论\n\n**事实：** 买入后卖出。',
  evidenceMarkdown: '## 一句话事实结论\n\n**事实：** 买入后卖出。',
  modelAnalysisMarkdown: null,
};

const unavailableModelReviewResponse = {
  ...evidenceReviewResponse,
  dataState: 'llm_unavailable',
  warnings: ['模型增强暂不可用；已根据成交与行情事实生成本地证据复盘。'],
};

const savedReviewAnnotation = {
  id: 71,
  episodeBuildId: 9,
  positionEpisodeId: 41,
  revision: 2,
  reviewStatus: 'in_progress' as const,
  setupThesis: '服务器保存的趋势延续假设',
  entryTrigger: '收回前高',
  invalidationPlan: '跌破结构低点',
  positionRationale: '半仓',
  exitReason: '达到目标',
  postTradeReflection: '加仓需要新触发',
  tags: ['趋势延续', '早盘'],
  errorTypes: ['追高'],
  contentSha256: 'a'.repeat(64),
  previousAnnotationId: 70,
  createdAt: '2026-07-22T09:30:00Z',
};

const verdictBlock = {
  episodeId: 41,
  buildId: 9,
  lane: 'intraday_0dte',
  ruleId: 'V2-A',
  verdict: 'compliant' as const,
  quadrant: 'deserved_win',
  quadrantLabel: '应得的赢',
  counterfactualCompliantExcluded: false,
  counterfactualCompliantText: '本单为合规车道，在「仅合规车道」反事实中保留。',
  counterfactualGateStatus: 'missing',
  counterfactualGateReason: 'gate 逐日记录未回填（E-4 / Phase C）：标缺，不现算冒充',
};

const excursionMissingResponse = {
  dataState: 'missing' as const,
  episodeId: 41,
  buildId: 9,
  excursion: null,
  missingReason: '该回合尚无偏移记录：可能超出 5m 数据保留窗口（约 60 天，永久标缺）',
  verdict: verdictBlock,
  disclaimer: '本图不用于设置止损；不得由此推导出场/持有参数。'
    + '对尾部结构账户，按 MAE 分位收紧止损最大概率截掉的恰是右尾。'
    + '持仓分组（日内/隔夜）存在内生性。',
  limitations: [],
};

const scatterEmptyResponse = {
  dataState: 'ready' as const,
  accountKey: 'default_moomoo_us',
  buildId: 9,
  codeVersion: 'underlying-5m/1.0',
  items: [],
  disclaimer: excursionMissingResponse.disclaimer,
  limitations: [],
};

function LocationProbe() {
  const location = useLocation();
  // 用无隐式 ARIA role 的元素，避免与页面内 role="status" 的断言相互干扰。
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[
      '/journal/review/41?build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3',
    ]}>
      <Routes>
        <Route
          path="/journal/review/:episodeId"
          element={(
            <>
              <JournalEpisodeReviewPage />
              <LocationProbe />
            </>
          )}
        />
        <Route path="/journal" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function expandFullWorksheet() {
  fireEvent.click(screen.getByRole('button', { name: '展开完整工作单' }));
}

/** 区②折叠揭示：结果内容（盈亏/K 线/工作单/AI）在点击揭示后才渲染。 */
async function revealResults() {
  fireEvent.click(await screen.findByTestId('reveal-results'));
}

describe('JournalEpisodeReviewPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-21T12:00:00Z'));
    apiMocks.fetchDetail.mockResolvedValue(detail);
    apiMocks.getHistory.mockResolvedValue(intraday);
    apiMocks.createAiReview.mockResolvedValue(aiReviewResponse);
    apiMocks.fetchLatestReview.mockResolvedValue({ dataState: 'not_started', annotation: null });
    apiMocks.fetchReviewHistory.mockResolvedValue({ dataState: 'not_started', items: [] });
    apiMocks.saveReview.mockResolvedValue({
      dataState: 'ready',
      created: true,
      idempotentReplay: false,
      annotation: savedReviewAnnotation,
    });
    apiMocks.fetchEpisodes.mockResolvedValue({
      dataState: 'ready',
      total: 0,
      page: 1,
      perPage: 5,
      items: [],
    });
    apiMocks.fetchEpisodeExcursion.mockResolvedValue(excursionMissingResponse);
    apiMocks.fetchExcursionScatter.mockResolvedValue(scatterEmptyResponse);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('loads the selected build, distinguishes proxy evidence, links chart selection, and preserves return context', async () => {
    renderPage();
    await revealResults();

    expect(await screen.findByText('NVDA260731C00150000')).toBeInTheDocument();
    expect(apiMocks.fetchDetail).toHaveBeenCalledWith(41, 9);
    expect(screen.getByText('证据窗口内已归零')).toBeInTheDocument();
    expect(screen.getByText(/当前时点的券商持仓快照，也不能倒推这个历史证据窗口的期初持仓/)).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getHistory).toHaveBeenCalledWith('NVDA', '5m', expect.any(Number)));
    expect(await screen.findByText('5 分钟 · YFinanceFetcher')).toBeInTheDocument();
    expect(screen.getAllByText('真实逐笔成交')).not.toHaveLength(0);
    expect(screen.getByText('订单时间代理')).toBeInTheDocument();
    expect(screen.getByText('买入')).toBeInTheDocument();
    expect(screen.getByText('卖出')).toBeInTheDocument();
    expect(screen.getByText(/期权成交价绝不画入这个价轴/)).toBeInTheDocument();
    expect(screen.getByTestId('review-chart')).toHaveAttribute('data-time-zone', 'America/New_York');
    expect(screen.getByTestId('review-chart')).toHaveAttribute('data-time-zone-label', 'ET');
    expect(screen.getByText(/横轴与十字光标：纽约时间 ET/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /图表事件 order-10/ }));
    const timeline = screen.getByRole('list', { name: '成交证据时间线' });
    expect(within(timeline).getByRole('button', { name: /平仓/ })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '返回仓位复盘' }));
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/journal?tab=positions&build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3',
    );
  });

  it('requests the user-selected 2-minute period and does not relabel another timeframe', async () => {
    apiMocks.getHistory.mockImplementation((_code: string, period: string) => (
      Promise.resolve(period === '2m' ? twoMinute : intraday)
    ));
    renderPage();
    await revealResults();

    expect(await screen.findByText('5 分钟 · YFinanceFetcher')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换到2 分钟 K 线' }));

    expect(await screen.findByText('2 分钟 · TwoMinuteFetcher')).toBeInTheDocument();
    expect(apiMocks.getHistory).toHaveBeenCalledWith('NVDA', '2m', expect.any(Number));
    expect(screen.getByRole('button', { name: '切换到2 分钟 K 线' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('requests 15-minute and 30-minute periods exactly and keeps both EMA overlays visible', async () => {
    apiMocks.getHistory.mockImplementation((_code: string, period: string) => Promise.resolve({
      ...intraday,
      period,
      source: `${period}Fetcher`,
      data: Array.from({ length: 16 }, (_, index) => ({
        date: new Date(Date.parse('2026-07-20T14:30:00Z') + index * 30 * 60 * 1000).toISOString(),
        open: 150 + index,
        high: 152 + index,
        low: 149 + index,
        close: 151 + index,
        volume: 1000 + index,
      })),
    }));
    renderPage();
    await revealResults();

    expect(await screen.findByText('5 分钟 · 5mFetcher')).toBeInTheDocument();
    expect(screen.getAllByTestId('chart-overlay').map((item) => item.textContent)).toEqual(['EMA 8', 'EMA 13']);
    expect(screen.getByText('EMA 8（底层收盘价）')).toBeInTheDocument();
    expect(screen.getByText('EMA 13（底层收盘价）')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '切换到15 分钟 K 线' }));
    expect(await screen.findByText('15 分钟 · 15mFetcher')).toBeInTheDocument();
    expect(apiMocks.getHistory).toHaveBeenCalledWith('NVDA', '15m', expect.any(Number));
    expect(screen.getByRole('button', { name: '切换到15 分钟 K 线' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '切换到30 分钟 K 线' }));
    expect(await screen.findByText('30 分钟 · 30mFetcher')).toBeInTheDocument();
    expect(apiMocks.getHistory).toHaveBeenCalledWith('NVDA', '30m', expect.any(Number));
    expect(screen.getByRole('button', { name: '切换到30 分钟 K 线' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('defaults to regular US hours and recalculates EMA when extended hours are selected', async () => {
    const premarket = Array.from({ length: 13 }, (_, index) => ({
      date: new Date(Date.parse('2026-07-20T08:00:00Z') + index * 5 * 60 * 1000).toISOString(),
      open: 100 + index,
      high: 101 + index,
      low: 99 + index,
      close: 100 + index,
      volume: 1000,
    }));
    const regular = Array.from({ length: 13 }, (_, index) => ({
      date: new Date(Date.parse('2026-07-20T13:30:00Z') + index * 5 * 60 * 1000).toISOString(),
      open: 200 + index,
      high: 201 + index,
      low: 199 + index,
      close: 200 + index,
      volume: 2000,
    }));
    apiMocks.getHistory.mockResolvedValue({
      ...intraday,
      source: 'MoomooFetcher',
      coverageStart: premarket[0].date,
      coverageEnd: regular.at(-1)?.date,
      data: [...premarket, ...regular],
    });
    renderPage();
    await revealResults();

    expect(await screen.findByText('5 分钟 · MoomooFetcher')).toBeInTheDocument();
    const regularButton = screen.getByRole('button', { name: '使用常规时段 09:30–16:00 ET' });
    const extendedButton = screen.getByRole('button', { name: '使用含盘前盘后 04:00–20:00 ET' });
    expect(regularButton).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('review-chart')).toHaveAttribute('data-candle-count', '13');
    expect(screen.getByText(/EMA 口径：常规时段 09:30–16:00 ET · 13 根/)).toBeInTheDocument();
    const regularEma8 = screen.getAllByTestId('chart-overlay')[0].getAttribute('data-last-value');

    fireEvent.click(extendedButton);

    expect(extendedButton).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('review-chart')).toHaveAttribute('data-candle-count', '26');
    expect(screen.getByText(/EMA 口径：含盘前盘后 04:00–20:00 ET · 26 根/)).toBeInTheDocument();
    expect(screen.getAllByTestId('chart-overlay')[0]).not.toHaveAttribute('data-last-value', regularEma8);
    expect(screen.getByText(/切换交易时段会基于当前可见 K 线重新计算/)).toBeInTheDocument();
  });

  it('keeps an explicitly selected old-period gap visible instead of silently falling back', async () => {
    apiMocks.getHistory.mockImplementation((_code: string, period: string) => {
      if (period === '1m') {
        return Promise.resolve({
          ...intraday,
          period: '1m',
          source: 'RecentOnlyFetcher',
          data: [{ date: '2026-07-21T14:30:00Z', open: 152, high: 153, low: 151, close: 152 }],
        });
      }
      return Promise.resolve(intraday);
    });
    renderPage();
    await revealResults();

    expect(await screen.findByText('5 分钟 · YFinanceFetcher')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换到1 分钟 K 线' }));

    expect(await screen.findByText('1 分钟行情无法覆盖这次回合')).toBeInTheDocument();
    expect(screen.getByText(/没有自动换成其他周期/)).toBeInTheDocument();
    expect(screen.getByText(/不会用邻近日线伪装成精确入场点/)).toBeInTheDocument();
  });

  it('generates an evidence review without a model and labels its source', async () => {
    let resolveReview: (value: typeof evidenceReviewResponse) => void = () => undefined;
    apiMocks.createAiReview.mockReturnValueOnce(new Promise((resolve) => {
      resolveReview = resolve;
    }));
    renderPage();
    await revealResults();

    expect(await screen.findByText('尚未生成证据复盘')).toBeInTheDocument();
    expect(apiMocks.createAiReview).not.toHaveBeenCalled();
    expect(screen.getByText(/单笔交易不能证明策略存在 edge/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '生成证据复盘' }));
    expect(screen.getByRole('button', { name: '整理证据中…' })).toBeDisabled();
    expect(screen.getByText(/正在并行读取成交证据、底层与基准行情/)).toBeInTheDocument();
    expect(screen.getByText(/不调用外部模型/)).toBeInTheDocument();
    expect(apiMocks.createAiReview).toHaveBeenCalledWith(41, 9, false, undefined);

    resolveReview(evidenceReviewResponse);
    expect(await screen.findByText('一句话事实结论')).toBeInTheDocument();
    expect(screen.getByText('本地证据引擎')).toBeInTheDocument();
    expect(screen.getByText('趋势日')).toBeInTheDocument();
    expect(screen.getByText('+1.00%')).toBeInTheDocument();
    expect(screen.getByText('NVDA 5m')).toBeInTheDocument();
    expect(screen.getByText(/期初边界未验证/)).toBeInTheDocument();
  });

  it('keeps the evidence review visible while an optional model enhancement runs', async () => {
    let resolveModel: (value: typeof aiReviewResponse) => void = () => undefined;
    apiMocks.createAiReview
      .mockResolvedValueOnce(evidenceReviewResponse)
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveModel = resolve;
      }));
    renderPage();
    await revealResults();

    await screen.findByText('尚未生成证据复盘');
    fireEvent.click(screen.getByRole('button', { name: '生成证据复盘' }));
    expect(await screen.findByText('本地证据引擎')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '尝试模型增强' }));
    expect(screen.getByRole('button', { name: '模型增强中…' })).toBeDisabled();
    expect(screen.getByText(/证据复盘仍然保留/)).toBeInTheDocument();
    expect(screen.getByText('一句话事实结论')).toBeInTheDocument();
    expect(screen.getByText(/服务端硬截止 20s/)).toBeInTheDocument();
    expect(apiMocks.createAiReview).toHaveBeenLastCalledWith(41, 9, true, undefined);

    resolveModel(aiReviewResponse);
    expect(await screen.findByText('已使用模型增强')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '证据事实层' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '模型推断层' })).toBeInTheDocument();
    expect(screen.getByText('一句话事实结论')).toBeInTheDocument();
    expect(screen.getByText('执行质量')).toBeInTheDocument();
  });

  it('explains the API boundary when model enhancement degrades to local evidence', async () => {
    apiMocks.createAiReview.mockResolvedValueOnce(unavailableModelReviewResponse);
    renderPage();
    await revealResults();

    await screen.findByText('尚未生成证据复盘');
    fireEvent.click(screen.getByRole('button', { name: '尝试模型增强' }));

    expect(await screen.findByText('模型增强未成功，已自动保留证据复盘')).toBeInTheDocument();
    expect(screen.getByText(/网站内自动调用 GPT 需要服务端 API Key/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开模型设置' })).toBeInTheDocument();
  });

  it('keeps unsaved trade logic as a build-scoped local draft and attaches only non-empty context', async () => {
    apiMocks.createAiReview.mockResolvedValueOnce(evidenceReviewResponse);
    renderPage();
    await revealResults();

    expect(await screen.findByRole('heading', { name: '交易逻辑草稿' })).toBeInTheDocument();
    expandFullWorksheet();
    expect(screen.getByText('事后回忆的进场前计划 · 只写当时可知信息')).toBeInTheDocument();
    expect(screen.getByText('交易后记录 · 结果已知后的观察')).toBeInTheDocument();
    expect(screen.getByText(/服务器版本 \+ 本机未提交草稿/)).toBeInTheDocument();
    expect(screen.getByText(/不修改 Moomoo 或 execution evidence/)).toBeInTheDocument();
    expect(screen.getByText(/发送给本机服务，不会调用外部模型/)).toBeInTheDocument();
    expect(screen.getByText(/发送给你配置的第三方模型供应商/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('进场逻辑回忆 / Entry rationale'), {
      target: { value: '  回踩 8 EMA 后延续  ' },
    });
    fireEvent.change(screen.getByLabelText('实际出场原因 / Exit reason'), {
      target: { value: '跌破失效点' },
    });
    fireEvent.change(screen.getByLabelText('复盘标签'), {
      target: { value: '趋势延续,' },
    });
    expect(screen.getByLabelText('复盘标签')).toHaveValue('趋势延续,');
    fireEvent.change(screen.getByLabelText('复盘标签'), {
      target: { value: '趋势延续, 早盘' },
    });

    expect(screen.getByRole('status')).toHaveTextContent('未提交更改已保存在本机');
    expect(screen.getByText('用户自述 · 2/6 项')).toBeInTheDocument();
    const stored = window.localStorage.getItem(episodeReviewDraftStorageKey(9, 41));
    expect(stored).toContain('回踩 8 EMA 后延续');
    expect(stored).toContain('跌破失效点');
    expect(stored).toContain('早盘');

    fireEvent.click(screen.getByRole('button', { name: '生成证据复盘' }));
    expect(apiMocks.createAiReview).toHaveBeenCalledWith(41, 9, false, {
      setupThesis: '回踩 8 EMA 后延续',
      exitReason: '跌破失效点',
    });
    expect(await screen.findByText('一句话事实结论')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清空当前编辑' }));
    expect(screen.getByRole('status')).toHaveTextContent('服务器版本与历史未删除');
    expect(window.localStorage.getItem(episodeReviewDraftStorageKey(9, 41))).toBeNull();
    expect(screen.getByText(/草稿已更新，重新生成后才会进入复盘/)).toBeInTheDocument();
  });

  it('restores the latest server review when no unsaved local draft exists', async () => {
    apiMocks.fetchLatestReview.mockResolvedValueOnce({
      dataState: 'ready',
      annotation: savedReviewAnnotation,
    });
    renderPage();
    await revealResults();

    expect(await screen.findByDisplayValue('服务器保存的趋势延续假设')).toBeInTheDocument();
    expect(screen.getByText(/已折叠字段中有 4 项内容/)).toBeInTheDocument();
    expandFullWorksheet();
    expect(screen.getByLabelText('进场触发 / Entry trigger')).toHaveValue('收回前高');
    expect(screen.getByLabelText('复盘标签')).toHaveValue('趋势延续, 早盘');
    expect(screen.getByText('复盘 · 进行中')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('已从服务器恢复复盘版本 #2');
  });

  it('keeps a non-empty local draft ahead of the latest server review', async () => {
    saveEpisodeReviewDraft(9, 41, {
      ...emptyEpisodeReviewDraft(),
      setupThesis: '本机尚未提交的假设',
      tags: ['本机标签'],
    });
    apiMocks.fetchLatestReview.mockResolvedValueOnce({
      dataState: 'ready',
      annotation: savedReviewAnnotation,
    });
    renderPage();
    await revealResults();

    expect(await screen.findByDisplayValue('本机尚未提交的假设')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('服务器保存的趋势延续假设')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('本机未提交草稿，已优先保留');
    });
    expect(screen.getByLabelText('复盘标签')).toHaveValue('本机标签');
  });

  it('saves in-progress and completed revisions, clears only local persistence, and reports idempotent replay', async () => {
    const completedAnnotation = {
      ...savedReviewAnnotation,
      id: 72,
      revision: 3,
      reviewStatus: 'completed' as const,
      previousAnnotationId: 71,
      createdAt: '2026-07-22T10:00:00Z',
    };
    apiMocks.saveReview
      .mockResolvedValueOnce({
        dataState: 'ready',
        created: true,
        idempotentReplay: false,
        annotation: savedReviewAnnotation,
      })
      .mockResolvedValueOnce({
        dataState: 'ready',
        created: false,
        idempotentReplay: true,
        annotation: completedAnnotation,
      });
    renderPage();
    await revealResults();

    await screen.findByRole('heading', { name: '交易逻辑草稿' });
    expect(screen.getByRole('button', { name: '标记复盘完成' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('进场逻辑回忆 / Entry rationale'), {
      target: { value: '  回踩 8 EMA 后延续  ' },
    });
    fireEvent.change(screen.getByLabelText('复盘标签'), {
      target: { value: '趋势延续, 早盘' },
    });
    fireEvent.change(screen.getByLabelText('错误类型'), {
      target: { value: '追高, 仓位过大' },
    });

    fireEvent.click(screen.getByRole('button', { name: '保存进行中' }));
    await waitFor(() => expect(apiMocks.saveReview).toHaveBeenCalledWith(41, {
      buildId: 9,
      reviewStatus: 'in_progress',
      setupThesis: '  回踩 8 EMA 后延续  ',
      entryTrigger: '',
      invalidationPlan: '',
      positionRationale: '',
      exitReason: '',
      postTradeReflection: '',
      tags: ['趋势延续', '早盘'],
      errorTypes: ['追高', '仓位过大'],
    }));
    expect(await screen.findByText(/已保存服务器版本 #2/)).toBeInTheDocument();
    expect(window.localStorage.getItem(episodeReviewDraftStorageKey(9, 41))).toBeNull();
    expect(screen.getByLabelText('进场逻辑回忆 / Entry rationale')).toHaveValue('  回踩 8 EMA 后延续  ');

    fireEvent.click(screen.getByRole('button', { name: '标记复盘完成' }));
    await waitFor(() => expect(apiMocks.saveReview).toHaveBeenLastCalledWith(
      41,
      expect.objectContaining({ buildId: 9, reviewStatus: 'completed' }),
    ));
    expect(await screen.findByText(/内容未变化，沿用服务器版本 #3/)).toBeInTheDocument();
    expect(screen.getByText('复盘 · 已完成')).toBeInTheDocument();
  });

  it('does not allow tags alone to mark a review complete', async () => {
    renderPage();
    await revealResults();
    await screen.findByRole('heading', { name: '交易逻辑草稿' });
    fireEvent.change(screen.getByLabelText('复盘标签'), {
      target: { value: '待补充' },
    });

    expect(screen.getByRole('button', { name: '保存进行中' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '标记复盘完成' })).toBeDisabled();
    expect(screen.getByText(/标签或错误分类不能替代复盘内容/)).toBeInTheDocument();
  });

  it('enforces the 6000-character aggregate context limit across fields', async () => {
    renderPage();
    await revealResults();
    await screen.findByRole('heading', { name: '交易逻辑草稿' });
    expandFullWorksheet();

    fireEvent.change(screen.getByLabelText('进场逻辑回忆 / Entry rationale'), {
      target: { value: '甲'.repeat(2000) },
    });
    fireEvent.change(screen.getByLabelText('进场触发 / Entry trigger'), {
      target: { value: '乙'.repeat(2000) },
    });
    fireEvent.change(screen.getByLabelText('失效点与止损 / Invalidation'), {
      target: { value: '丙'.repeat(2000) },
    });
    fireEvent.change(screen.getByLabelText('仓位理由 / Position rationale'), {
      target: { value: '不应写入' },
    });

    expect(screen.getByText('总字符 6000/6000')).toBeInTheDocument();
    expect(screen.getByText(/超出部分未保存/)).toBeInTheDocument();
    expect(screen.getByLabelText('仓位理由 / Position rationale')).toHaveValue('');

    fireEvent.change(screen.getByLabelText('失效点与止损 / Invalidation'), {
      target: { value: '丙'.repeat(1990) },
    });
    fireEvent.change(screen.getByLabelText('仓位理由 / Position rationale'), {
      target: { value: '允许十个字' },
    });
    expect(screen.getByLabelText('仓位理由 / Position rationale')).toHaveValue('允许十个字');
    expect(screen.getByText('总字符 5995/6000')).toBeInTheDocument();
  });

  it('falls back to legacy analysisMarkdown when additive layer fields are absent', async () => {
    apiMocks.createAiReview.mockResolvedValueOnce({
      ...evidenceReviewResponse,
      evidenceMarkdown: undefined,
      modelAnalysisMarkdown: undefined,
      analysisMarkdown: '## 旧版兼容正文\n\n仍然可见。',
    });
    renderPage();
    await revealResults();

    await screen.findByText('尚未生成证据复盘');
    fireEvent.click(screen.getByRole('button', { name: '生成证据复盘' }));

    expect(await screen.findByText('旧版兼容正文')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '证据事实层' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '模型推断层' })).not.toBeInTheDocument();
    expect(screen.getByText(/旧版 analysisMarkdown 兼容显示/)).toBeInTheDocument();
  });

  it('keeps the workspace usable when the evidence request fails and supports retry', async () => {
    apiMocks.createAiReview
      .mockRejectedValueOnce({
        response: {
          status: 503,
          data: { detail: { error: 'llm_not_configured', message: 'LLM is not configured' } },
        },
      })
      .mockResolvedValueOnce(evidenceReviewResponse);
    renderPage();
    await revealResults();

    await screen.findByText('尚未生成证据复盘');
    fireEvent.click(screen.getByRole('button', { name: '生成证据复盘' }));
    expect(await screen.findByText(/网络请求失败不会影响/)).toBeInTheDocument();
    expect(screen.getAllByText(/真实逐笔成交|订单时间代理/)).not.toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: '重试证据复盘' }));
    expect(await screen.findByText('一句话事实结论')).toBeInTheDocument();
    expect(apiMocks.createAiReview).toHaveBeenCalledTimes(2);
  });

  it('keeps an existing evidence review visible when regeneration fails', async () => {
    apiMocks.createAiReview
      .mockResolvedValueOnce(evidenceReviewResponse)
      .mockRejectedValueOnce(new Error('temporary network failure'));
    renderPage();
    await revealResults();

    await screen.findByText('尚未生成证据复盘');
    fireEvent.click(screen.getByRole('button', { name: '生成证据复盘' }));
    expect(await screen.findByText('一句话事实结论')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新生成证据复盘' }));
    expect(await screen.findByText(/网络请求失败不会影响/)).toBeInTheDocument();
    expect(screen.getByText('一句话事实结论')).toBeInTheDocument();
  });

  it('automatically falls back to daily bars when minute data is empty and explains precision limits', async () => {
    apiMocks.getHistory.mockImplementation((_code: string, period: string) => (
      Promise.resolve(period === '5m' ? { ...intraday, data: [] } : daily)
    ));
    renderPage();
    await revealResults();

    expect(await screen.findByText('分钟行情无法覆盖这次回合，已自动降级为日线')).toBeInTheDocument();
    expect(screen.getByText(/不能用于精确判断入场、MFE 或 MAE/)).toBeInTheDocument();
    expect(screen.getByText('日线 · DailyFetcher')).toBeInTheDocument();
    expect(apiMocks.getHistory).toHaveBeenNthCalledWith(1, 'NVDA', '5m', expect.any(Number));
    expect(apiMocks.getHistory).toHaveBeenNthCalledWith(2, 'NVDA', 'daily', expect.any(Number));
  });

  it('keeps the evidence timeline usable when both minute and daily market data are empty', async () => {
    apiMocks.getHistory.mockImplementation((_code: string, period: string) => Promise.resolve({
      ...(period === '5m' ? intraday : daily),
      data: [],
    }));
    renderPage();
    await revealResults();

    expect(await screen.findByText('底层行情暂不可用')).toBeInTheDocument();
    expect(screen.getByText(/不会移动 evidence 时间/)).toBeInTheDocument();
    expect(screen.getAllByText('真实逐笔成交')).not.toHaveLength(0);
    expect(screen.getAllByText('行情缺口')).toHaveLength(2);
  });

  it('keeps the action bar blind before reveal and enables save actions only after', async () => {
    renderPage();

    expect(await screen.findByText('NVDA260731C00150000')).toBeInTheDocument();
    const actionBar = screen.getByTestId('review-action-bar');
    expect(actionBar.className).toContain('sticky');
    // 盲评：操作条不再显示 Net；跳过可用，但保存/完成要等揭示后。
    expect(within(actionBar).getByText('盲评中 · 结果已折叠')).toBeInTheDocument();
    expect(within(actionBar).queryByText('Net')).not.toBeInTheDocument();
    expect(within(actionBar).queryByText('$48.75')).not.toBeInTheDocument();
    expect(within(actionBar).getByRole('button', { name: /跳过，下一笔/ })).toBeEnabled();
    expect(within(actionBar).getByRole('button', { name: '保存草稿并下一笔' })).toBeDisabled();
    expect(within(actionBar).getByRole('button', { name: '完成复盘并下一笔' })).toBeDisabled();
    expect(within(actionBar).getByText(/下一笔优先级：进行中 → 亏损最大的未复盘 → 最近未开始/)).toBeInTheDocument();

    await revealResults();
    expect(within(actionBar).getByText('结果已揭示')).toBeInTheDocument();
    expect(within(actionBar).getByRole('button', { name: '保存草稿并下一笔' })).toBeEnabled();
    expect(within(actionBar).getByRole('button', { name: '完成复盘并下一笔' })).toBeEnabled();
  });

  it('enforces the blueprint zone order: snapshot first, folded results, quadrant and what-if after reveal', async () => {
    renderPage();

    // 区①决策快照默认展开；结果全部折叠：无盈亏、无 K 线请求、无工作单。
    expect(await screen.findByTestId('decision-snapshot')).toBeInTheDocument();
    expect(screen.getByText(/只列进场时刻可知的信息/)).toBeInTheDocument();
    expect(await screen.findByText(/V2-A · 合规/)).toBeInTheDocument();
    expect(screen.getByText(/regime_score_at_entry 回填未到/)).toBeInTheDocument();
    expect(screen.getByText(/push ledger 未建/)).toBeInTheDocument();
    expect(screen.getByTestId('reveal-gate')).toBeInTheDocument();
    expect(screen.queryByText('$48.75')).not.toBeInTheDocument();
    expect(screen.queryByTestId('review-columns')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '交易逻辑草稿' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('what-if-panel')).not.toBeInTheDocument();
    expect(apiMocks.getHistory).not.toHaveBeenCalled();

    await revealResults();

    // 区②：盈亏与偏移诊断（含永久免责）；区③：四象限（服务端判定）；区④：反事实。
    expect(await screen.findByText('$48.75')).toBeInTheDocument();
    expect(await screen.findByTestId('excursion-disclaimer')).toHaveTextContent(
      '本图不用于设置止损',
    );
    expect(screen.getByTestId('quadrant-strip')).toBeInTheDocument();
    expect(screen.getByTestId('quadrant-chip')).toHaveTextContent('应得的赢');
    const whatIf = screen.getByTestId('what-if-panel');
    expect(within(whatIf).getByText('反事实 A · 仅合规车道')).toBeInTheDocument();
    expect(within(whatIf).getByText(/gate 逐日记录未回填/)).toBeInTheDocument();
    expect(within(whatIf).getByText(/永不叠加/)).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getHistory).toHaveBeenCalled());
  });

  it('never renders banned metrics (SQN / Zella-style scores, hold-time edge, MAE stops)', async () => {
    const { container } = renderPage();
    await revealResults();
    await screen.findByText('$48.75');
    const text = container.textContent ?? '';
    for (const banned of ['SQN', 'Zella', '系统质量数', '综合评分', '最优持有时间', 'MAE 止损', '建议止损位']) {
      expect(text).not.toContain(banned);
    }
  });

  it('lays the evidence and worksheet columns side by side at wide viewports with the worksheet first on narrow ones', async () => {
    renderPage();
    await revealResults();
    await screen.findByText('NVDA260731C00150000');

    const columns = screen.getByTestId('review-columns');
    expect(columns.className).toContain('xl:grid-cols-[minmax(0,1fr)_minmax(400px,460px)]');
    const evidenceColumn = screen.getByTestId('review-evidence-column');
    expect(evidenceColumn.className).toContain('order-2');
    expect(evidenceColumn.className).toContain('xl:order-1');
    expect(within(evidenceColumn).getByText('成交证据时间线')).toBeInTheDocument();
    expect(within(evidenceColumn).getByText(/底层 K 线与事件/)).toBeInTheDocument();
    const worksheetColumn = screen.getByTestId('review-worksheet-column');
    expect(worksheetColumn.className).toContain('order-1');
    expect(worksheetColumn.className).toContain('xl:order-2');
    expect(worksheetColumn.querySelector('.xl\\:sticky')).not.toBeNull();
    expect(within(worksheetColumn).getByRole('heading', { name: '交易逻辑草稿' })).toBeInTheDocument();
  });

  it('starts the worksheet in quick mode and reveals the four folded fields on demand', async () => {
    renderPage();
    await revealResults();
    await screen.findByRole('heading', { name: '交易逻辑草稿' });

    expect(screen.getByLabelText('进场逻辑回忆 / Entry rationale')).toBeInTheDocument();
    expect(screen.getByLabelText('复盘反思 / Post-trade reflection')).toBeInTheDocument();
    expect(screen.queryByLabelText('进场触发 / Entry trigger')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('失效点与止损 / Invalidation')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('仓位理由 / Position rationale')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('实际出场原因 / Exit reason')).not.toBeInTheDocument();

    expandFullWorksheet();
    expect(screen.getByLabelText('进场触发 / Entry trigger')).toBeInTheDocument();
    expect(screen.getByLabelText('失效点与止损 / Invalidation')).toBeInTheDocument();
    expect(screen.getByLabelText('仓位理由 / Position rationale')).toBeInTheDocument();
    expect(screen.getByLabelText('实际出场原因 / Exit reason')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '收起完整工作单' }));
    expect(screen.queryByLabelText('进场触发 / Entry trigger')).not.toBeInTheDocument();
  });

  it('toggles preset chips into the shared tag and error-type fields while keeping free text', async () => {
    renderPage();
    await revealResults();
    await screen.findByRole('heading', { name: '交易逻辑草稿' });

    const errorChip = screen.getByRole('button', { name: '追高进场' });
    fireEvent.click(errorChip);
    expect(screen.getByRole('button', { name: '追高进场' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('错误类型')).toHaveValue('追高进场');

    fireEvent.click(screen.getByRole('button', { name: '跳空托举' }));
    expect(screen.getByRole('button', { name: '跳空托举' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('复盘标签')).toHaveValue('跳空托举');

    const stored = window.localStorage.getItem(episodeReviewDraftStorageKey(9, 41));
    expect(stored).toContain('追高进场');
    expect(stored).toContain('跳空托举');

    // 自由文本与 chip 共用同一字段：手输保留，chip 再点一次只移除自己。
    fireEvent.change(screen.getByLabelText('错误类型'), {
      target: { value: '追高进场, 自定义错误' },
    });
    fireEvent.click(screen.getByRole('button', { name: '追高进场' }));
    expect(screen.getByRole('button', { name: '追高进场' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByLabelText('错误类型')).toHaveValue('自定义错误');
  });

  it('saves a draft revision and then navigates to the next queued episode', async () => {
    apiMocks.fetchEpisodes.mockResolvedValue({
      dataState: 'ready',
      total: 1,
      page: 1,
      perPage: 5,
      items: [{ id: 52 }],
    });
    renderPage();
    await revealResults();
    await screen.findByRole('heading', { name: '交易逻辑草稿' });
    fireEvent.change(screen.getByLabelText('进场逻辑回忆 / Entry rationale'), {
      target: { value: '回踩 8 EMA 后延续' },
    });

    fireEvent.click(screen.getByRole('button', { name: '保存草稿并下一笔' }));

    await waitFor(() => expect(apiMocks.saveReview).toHaveBeenCalledWith(
      41,
      expect.objectContaining({ buildId: 9, reviewStatus: 'in_progress' }),
    ));
    await waitFor(() => expect(apiMocks.fetchEpisodes).toHaveBeenCalledWith(
      expect.objectContaining({ buildId: 9, reviewStatus: 'in_progress', page: 1, perPage: 5 }),
    ));
    await waitFor(() => expect(apiMocks.fetchDetail).toHaveBeenCalledWith(52, 9));
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/journal/review/52?build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3',
    );
  });

  it('completes the review and navigates next only after the completed save succeeds', async () => {
    apiMocks.fetchEpisodes.mockResolvedValue({
      dataState: 'ready',
      total: 1,
      page: 1,
      perPage: 5,
      items: [{ id: 61 }],
    });
    renderPage();
    await revealResults();
    await screen.findByRole('heading', { name: '交易逻辑草稿' });
    fireEvent.change(screen.getByLabelText('复盘反思 / Post-trade reflection'), {
      target: { value: '下次只在重新站稳后加仓' },
    });

    fireEvent.click(screen.getByRole('button', { name: '完成复盘并下一笔' }));

    await waitFor(() => expect(apiMocks.saveReview).toHaveBeenCalledWith(
      41,
      expect.objectContaining({ reviewStatus: 'completed' }),
    ));
    await waitFor(() => expect(apiMocks.fetchDetail).toHaveBeenCalledWith(61, 9));
  });

  it('skips to the next episode without saving anything', async () => {
    apiMocks.fetchEpisodes.mockResolvedValue({
      dataState: 'ready',
      total: 1,
      page: 1,
      perPage: 5,
      items: [{ id: 52 }],
    });
    renderPage();
    await revealResults();
    await screen.findByText('NVDA260731C00150000');

    fireEvent.click(screen.getByRole('button', { name: /跳过，下一笔/ }));

    await waitFor(() => expect(apiMocks.fetchDetail).toHaveBeenCalledWith(52, 9));
    expect(apiMocks.saveReview).not.toHaveBeenCalled();
    expect(screen.getByTestId('location')).toHaveTextContent('/journal/review/52');
  });

  it('stays on the page with a visible error when save-and-next fails validation or persistence', async () => {
    renderPage();
    await revealResults();
    await screen.findByRole('heading', { name: '交易逻辑草稿' });

    // 完成复盘校验：没有任何自述时不能完成，也不应去解析下一笔。
    fireEvent.click(screen.getByRole('button', { name: '完成复盘并下一笔' }));
    expect(await screen.findByText(/保存未成功：至少填写一项用户自述后才能标记复盘完成/)).toBeInTheDocument();
    expect(apiMocks.fetchEpisodes).not.toHaveBeenCalled();

    // 服务器保存失败：错误可见、停留当前回合、不导航。
    apiMocks.saveReview.mockRejectedValueOnce(new Error('server rejected'));
    fireEvent.change(screen.getByLabelText('进场逻辑回忆 / Entry rationale'), {
      target: { value: '回踩 8 EMA 后延续' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿并下一笔' }));
    expect(await screen.findByText(/保存未成功：server rejected/)).toBeInTheDocument();
    expect(apiMocks.fetchEpisodes).not.toHaveBeenCalled();
    expect(screen.getByTestId('location')).toHaveTextContent('/journal/review/41');
    expect(apiMocks.fetchDetail).toHaveBeenCalledTimes(1);
  });

  it('reports an exhausted review queue honestly and offers the way back to the list', async () => {
    renderPage();
    await revealResults();
    await screen.findByText('NVDA260731C00150000');

    fireEvent.click(screen.getByRole('button', { name: /跳过，下一笔/ }));

    expect(await screen.findByText(/全部回合已完成复盘/)).toBeInTheDocument();
    // in_progress → not_started(top_loss) → not_started 三档都查过且排除当前回合。
    expect(apiMocks.fetchEpisodes).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole('button', { name: '返回仓位列表' }));
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/journal?tab=positions&build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3',
    );
  });

  it('keeps the evidence window and technical provenance inside a collapsed evidence-details section', async () => {
    renderPage();
    await revealResults();
    await screen.findByText('NVDA260731C00150000');

    const details = screen.getByTestId('evidence-details');
    expect(details).not.toHaveAttribute('open');
    expect(within(details).getByText(/证据明细/)).toBeInTheDocument();
    expect(within(details).getByText('证据窗口与窗口末投影')).toBeInTheDocument();
    expect(within(details).getByText('证据窗口（ET）起—止')).toBeInTheDocument();
    expect(within(details).getByText('窗口末投影 as-of（ET）')).toBeInTheDocument();
    expect(within(details).getByText(/不等于券商当前持仓/)).toBeInTheDocument();
    expect(within(details).getByText('Matching')).toBeInTheDocument();
    expect(within(details).getByText('Completeness')).toBeInTheDocument();
    expect(within(details).getByText('Provenance')).toBeInTheDocument();
  });

  it('consolidates same-order partial fills into one auditable row with a single merged marker', async () => {
    apiMocks.fetchDetail.mockResolvedValue({
      ...detail,
      evidence: [
        {
          ...detail.evidence[0],
          id: 21,
          evidenceKey: 'fill-21',
          eventRole: 'open',
          evidenceTime: '2026-07-20T14:32:00Z',
          allocatedQuantity: '1.0000000000',
          allocatedFee: '0.6500000000',
          allocatedCashFlow: '-250.0000000000',
          brokerOrderObservationId: 10,
          brokerFillObservationId: 21,
        },
        {
          ...detail.evidence[0],
          id: 22,
          evidenceKey: 'fill-22',
          eventRole: 'add',
          allocationSequence: 1,
          evidenceTime: '2026-07-20T14:32:30Z',
          allocatedQuantity: '2.0000000000',
          allocatedFee: '1.3000000000',
          allocatedCashFlow: '-510.0000000000',
          brokerOrderObservationId: 10,
          brokerFillObservationId: 22,
        },
      ],
    });
    renderPage();
    await revealResults();

    expect(await screen.findByText('分 2 笔成交')).toBeInTheDocument();
    const timeline = screen.getByRole('list', { name: '成交证据时间线' });
    const orderRow = within(timeline).getByRole('button', { name: /一笔订单/ });
    expect(within(orderRow).getByText('买入 · 一笔订单')).toBeInTheDocument();
    expect(within(orderRow).getByText('回合角色：开仓 / 加仓')).toBeInTheDocument();
    expect(within(orderRow).getByText('总数量')).toBeInTheDocument();
    expect(within(orderRow).getByText('3')).toBeInTheDocument();
    expect(within(orderRow).getByText('现金流加权均价')).toBeInTheDocument();
    expect(within(orderRow).getByText('$2.5333')).toBeInTheDocument();
    expect(within(orderRow).getByText('合计费用')).toBeInTheDocument();
    expect(within(orderRow).getByText('$1.95')).toBeInTheDocument();
    expect(within(orderRow).getByText('真实逐笔成交')).toBeInTheDocument();

    // 展开后每一笔 fill 仍完整可审计。
    fireEvent.click(within(timeline).getByRole('button', { name: /展开 2 笔逐笔成交/ }));
    const fillList = within(timeline).getByRole('list', { name: '逐笔成交明细' });
    expect(within(fillList).getByText('第 1 笔')).toBeInTheDocument();
    expect(within(fillList).getByText('第 2 笔')).toBeInTheDocument();
    expect(within(fillList).getByText('fill observation 21')).toBeInTheDocument();
    expect(within(fillList).getByText('fill observation 22')).toBeInTheDocument();
    expect(within(fillList).getAllByText(/回合角色：/)).toHaveLength(2);

    // 同单同 bar 的分批成交只画一个合并 marker；点击选中合并行。
    const mergedMarker = screen.getByRole('button', { name: /图表事件 order:10 买×2/ });
    fireEvent.click(mergedMarker);
    expect(within(timeline).getByRole('button', { name: /一笔订单/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows an incomplete fee label instead of passing a partial fee sum off as the order total', async () => {
    apiMocks.fetchDetail.mockResolvedValue({
      ...detail,
      evidence: [
        {
          ...detail.evidence[0],
          id: 21,
          evidenceKey: 'fill-21',
          evidenceTime: '2026-07-20T14:32:00Z',
          allocatedFee: '0.6500000000',
          brokerOrderObservationId: 10,
          brokerFillObservationId: 21,
        },
        {
          ...detail.evidence[0],
          id: 22,
          evidenceKey: 'fill-22',
          allocationSequence: 1,
          evidenceTime: '2026-07-20T14:32:30Z',
          allocatedFee: null,
          brokerOrderObservationId: 10,
          brokerFillObservationId: 22,
        },
      ],
    });
    renderPage();
    await revealResults();

    expect(await screen.findByText('分 2 笔成交')).toBeInTheDocument();
    expect(screen.getByText('费用不完整')).toBeInTheDocument();
  });
});
