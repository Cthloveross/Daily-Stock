import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LARGE_PRINT_MIN_TURNOVER_USD,
  OPTION_SKEW_RATIO_HIGH,
  OPTION_SKEW_RATIO_LOW,
  PremarketOptionAnomalyPanel,
} from '../PremarketOptionAnomalyPanel';
import {
  fetchOpportunityOptionEvents,
  fetchOpportunityOptionWalls,
} from '../../../api/opportunities';
import type {
  IntradaySessionPhase,
  IntradayTopResponse,
  OpportunityOptionEvent,
  OpportunityOptionEventItem,
  OpportunityOptionEventResponse,
  OpportunityOptionWallItem,
  OpportunityOptionWallRatio,
  OpportunityOptionWallResponse,
} from '../../../types/opportunities';

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return {
    ...actual,
    fetchOpportunityOptionWalls: vi.fn(),
    fetchOpportunityOptionEvents: vi.fn(),
  };
});

function ratio(
  value: number | null,
  metricBasis: OpportunityOptionWallRatio['metricBasis'],
  reason: string | null = null,
): OpportunityOptionWallRatio {
  return {
    value,
    numeratorTotal: 120_000,
    denominatorTotal: 40_000,
    numeratorSide: 'call',
    denominatorSide: 'put',
    metricBasis,
    reason,
  };
}

function wallItem(
  ticker: string,
  volumeRatio: number | null,
  oiRatio: number | null,
  overrides: Partial<OpportunityOptionWallItem> = {},
): OpportunityOptionWallItem {
  return {
    ticker,
    state: 'ready',
    source: 'moomoo_openapi',
    fetchedAt: '2026-08-14T12:35:00+00:00',
    quoteAsOf: '2026-08-13 16:00:00',
    formulaVersion: 'option-wall/1.3',
    spot: 100,
    atmCallIv: {
      state: 'ready',
      expiry: '2026-08-15',
      strike: 100,
      atmCallIvPercent: 40,
      selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot',
    },
    scope: { dteMin: 0, dteMax: 45, expiries: ['2026-08-15'], standardContractsOnly: true },
    coverage: {
      requestedContracts: 10,
      snapshotReceivedContracts: 10,
      validContracts: 10,
      coveragePercent: 100,
      failedBatches: 0,
      excludedNonstandardContracts: 0,
      excludedUnknownStandardTypeContracts: 0,
      gammaContracts: 10,
    },
    walls: {
      callOi: [],
      putOi: [],
      callVolume: [],
      putVolume: [],
      callGammaConcentration: [],
      putGammaConcentration: [],
      grossGammaConcentration: [],
    },
    totals: { callOi: 120_000, putOi: 40_000, callVolume: 90_000, putVolume: 30_000 },
    ratios: {
      callPutOiRatio: ratio(oiRatio, 'settled_open_interest_prior_session'),
      callPutVolumeRatio: ratio(volumeRatio, 'current_session_cumulative_volume'),
      caveat: '这两个比例在本账户数据上尚未被检验过。',
    },
    oiWeightedCenter: null,
    message: '',
    assumptions: [],
    limitations: [],
    ...overrides,
  };
}

function wallResponse(items: OpportunityOptionWallItem[]): OpportunityOptionWallResponse {
  return {
    schemaVersion: 'option-wall/1.3',
    marketDateEt: '2026-08-14',
    generatedAt: '2026-08-14T12:35:00+00:00',
    items,
  };
}

function optionEvent(
  overrides: Partial<OpportunityOptionEvent> = {},
): OpportunityOptionEvent {
  return {
    eventId: 'evt_1',
    optionCode: 'US.NVDA260821C250000',
    ownerCode: 'US.NVDA',
    symbol: 'NVDA260821C250000',
    fillTime: '2026-08-13 15:42:00',
    tickerType: 'BUY',
    price: 5.0,
    volume: 2_400,
    turnover: 1_200_000,
    optionType: 'CALL',
    strikePrice: 250,
    expiry: '2026-08-21',
    dte: 7,
    underlyingPrice: 245.0,
    bidPrice: 4.9,
    askPrice: 5.1,
    ivPercent: 55.0,
    totalVolume: 10_000,
    totalOpenInterest: 50_000,
    voRatioPercent: 24.0,
    delta: 0.4,
    sentiment: 'BULLISH',
    orderTypes: ['SWEEP'],
    strategyType: 'SINGLE_LEG',
    ...overrides,
  };
}

function eventItem(
  ticker: string,
  events: OpportunityOptionEvent[],
): OpportunityOptionEventItem {
  return {
    ticker,
    state: events.length > 0 ? 'ready' : 'empty',
    source: 'moomoo_openapi',
    fetchedAt: '2026-08-14T12:35:00+00:00',
    eventAsOf: '2026-08-13 16:00:00',
    allCount: events.length,
    events,
    message: '',
    limitations: [],
  };
}

function eventResponse(items: OpportunityOptionEventItem[]): OpportunityOptionEventResponse {
  return {
    schemaVersion: 'option-events/1.0',
    marketDateEt: '2026-08-14',
    generatedAt: '2026-08-14T12:35:00+00:00',
    items,
  };
}

function topStub(
  sessionPhase: IntradaySessionPhase,
  tickers: string[],
): IntradayTopResponse {
  return {
    schemaVersion: 'intraday-top/1.0',
    runId: 'itr_fixture',
    generatedAt: '2026-08-14T12:35:00+00:00',
    asOf: '2026-08-14T12:35:00+00:00',
    marketDateEt: '2026-08-14',
    sessionState: sessionPhase === 'premarket' ? 'premarket' : 'regular',
    sessionStateBasis: 'america_new_york_clock_v1',
    sessionPhase,
    sessionPhaseLabel: '盘段标签',
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
    signalVersion: 'intraday_session_evidence_v8',
    rankingMethod: 'burst_score_first_then_evidence_count',
    statisticsTrack: 'none_intraday_v1_unscored',
    moomooEnabled: true,
    universe: tickers,
    universeScan: {
      mode: 'watchlist_two_tier',
      gateBasis: 'premarket_pre_price_change_then_pre_turnover_v1',
      gateWarnings: [],
      watchlistTotal: tickers.length,
      watchlistTruncated: false,
      scannedTotal: tickers.length,
      deepLaneCount: tickers.length,
      deepLaneMax: 12,
      deepLane: tickers.map((ticker, index) => ({
        ticker,
        promotedBy: 'mover_rank' as const,
        moverRank: index + 1,
      })),
      planAlwaysInclude: [],
      gatedOutCount: 0,
      snapshotUnresolvedSymbols: [],
      dayPromotionCap: 30,
      dayPromotionCapReached: false,
      snapshotOnly: [],
      limitations: [],
    },
    unsupportedSymbols: [],
    requestedLimit: 5,
    candidateCount: 0,
    candidates: [],
    recentOptionEvents: [],
    limitations: [],
  };
}

describe('PremarketOptionAnomalyPanel（盘前期权异常 · 昨日事实）', () => {
  beforeEach(() => {
    vi.mocked(fetchOpportunityOptionWalls).mockReset();
    vi.mocked(fetchOpportunityOptionEvents).mockReset();
    vi.mocked(fetchOpportunityOptionWalls).mockResolvedValue(wallResponse([]));
    vi.mocked(fetchOpportunityOptionEvents).mockResolvedValue(eventResponse([]));
  });

  it('renders facts, fires skew/large-print flags and carries the basis lines in premarket', async () => {
    vi.mocked(fetchOpportunityOptionWalls).mockResolvedValue(wallResponse([
      // NVDA：量比 5.2 ≥3 → 偏斜；OI 比 1.1 正常。
      wallItem('NVDA', 5.2, 1.1),
      // TSLA：量比 1.0 正常；OI 比 0.33 ≤0.33 → 偏斜（put 侧对称阈值）。
      wallItem('TSLA', 1.0, OPTION_SKEW_RATIO_LOW),
    ]));
    vi.mocked(fetchOpportunityOptionEvents).mockResolvedValue(eventResponse([
      eventItem('NVDA', [
        optionEvent({ eventId: 'small', turnover: 400_000, volume: 800 }),
        optionEvent({ eventId: 'big', turnover: LARGE_PRINT_MIN_TURNOVER_USD + 200_000 }),
      ]),
      eventItem('TSLA', [
        optionEvent({
          eventId: 'tsla_small',
          turnover: 999_999,
          optionType: 'PUT',
          sentiment: 'BEARISH',
          strikePrice: 300,
        }),
      ]),
    ]));
    render(
      <PremarketOptionAnomalyPanel top={topStub('premarket', ['NVDA', 'TSLA'])} />,
    );

    const panel = screen.getByLabelText('盘前期权异常');
    // 盘前自动展开并取数。
    expect(
      within(panel).getByRole('button', { name: '展开或收起盘前期权异常面板' }),
    ).toHaveAttribute('aria-expanded', 'true');

    // NVDA：量比偏斜 + ≥$1M 大单章；大单明细逐字段带供应商分类。
    expect(await within(panel).findByText('偏斜 · 量比 5.20')).toBeInTheDocument();
    expect(within(panel).getByText('大单 $1.2M')).toBeInTheDocument();
    expect(
      within(panel).getByText(
        '大单明细：Call 250 · 2026-08-21 · 7DTE · 2400 张 · $1.2M · 偏多（Moomoo 分类）',
      ),
    ).toBeInTheDocument();

    // TSLA：OI 比 0.33 命中下沿阈值 → 偏斜；<$1M 的最大单不盖「大单」章，
    // 但金额仍如实陈列。
    expect(within(panel).getByText('偏斜 · OI比 0.33')).toBeInTheDocument();
    expect(within(panel).queryByText('大单 $1.0M')).not.toBeInTheDocument();
    expect(within(panel).getByText('最大单 $1000K')).toBeInTheDocument();

    // 事实读数行（未偏斜的比例照常陈列，不因未命中阈值而隐藏）。
    expect(within(panel).getByText('量比 C/P 5.20')).toBeInTheDocument();
    expect(within(panel).getByText('OI比 C/P 1.10')).toBeInTheDocument();
    expect(within(panel).getByText('量比 C/P 1.00')).toBeInTheDocument();

    // 诚实口径三连：上一时段基准、启发式阈值声明、供应商分类边界。
    expect(
      within(panel).getByText(/OI＝上一清算时段结算；成交量与大单＝供应商快照\/异动页的会话累计/),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText(
        new RegExp(
          `偏斜＝量比或 OI比 ≥${OPTION_SKEW_RATIO_HIGH} 或 ≤${OPTION_SKEW_RATIO_LOW}；大单＝单笔 ≥\\$1M——v1 启发式，未经验证`,
        ),
      ),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText(
        /分类来自供应商，不构成方向证明；比例异常≠会涨会跌——该假说在你的数据上尚未检验（期权墙逐日快照正在积累样本）/,
      ),
    ).toBeInTheDocument();

    // 请求有界：2 檔 → 墙 1 次（≤5 檔/批）、异动 1 次（≤3 檔/批，每檔 10 条）。
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1);
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledWith(['NVDA', 'TSLA']);
    expect(fetchOpportunityOptionEvents).toHaveBeenCalledTimes(1);
    expect(fetchOpportunityOptionEvents).toHaveBeenCalledWith(['NVDA', 'TSLA'], 10);
  });

  it('batches wall reads by 5 and event reads by 3 across the 8-ticker deep lane', async () => {
    const tickers = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF', 'GGG', 'HHH'];
    render(<PremarketOptionAnomalyPanel top={topStub('premarket', tickers)} />);

    await waitFor(() => {
      expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(2);
    });
    expect(fetchOpportunityOptionWalls).toHaveBeenNthCalledWith(1, ['AAA', 'BBB', 'CCC', 'DDD', 'EEE']);
    expect(fetchOpportunityOptionWalls).toHaveBeenNthCalledWith(2, ['FFF', 'GGG', 'HHH']);
    expect(fetchOpportunityOptionEvents).toHaveBeenCalledTimes(3);
    expect(fetchOpportunityOptionEvents).toHaveBeenNthCalledWith(1, ['AAA', 'BBB', 'CCC'], 10);
    expect(fetchOpportunityOptionEvents).toHaveBeenNthCalledWith(2, ['DDD', 'EEE', 'FFF'], 10);
    expect(fetchOpportunityOptionEvents).toHaveBeenNthCalledWith(3, ['GGG', 'HHH'], 10);
  });

  it('marks per-ticker 标缺 when a lane fails instead of inventing readings', async () => {
    // 墙请求整体失败 + 异动只返回 NVDA：TSLA 两路全标缺，NVDA 比例标缺。
    vi.mocked(fetchOpportunityOptionWalls).mockRejectedValue(new Error('wall down'));
    vi.mocked(fetchOpportunityOptionEvents).mockResolvedValue(eventResponse([
      eventItem('NVDA', [optionEvent()]),
    ]));
    render(
      <PremarketOptionAnomalyPanel top={topStub('premarket', ['NVDA', 'TSLA'])} />,
    );

    const panel = screen.getByLabelText('盘前期权异常');
    expect(await within(panel).findAllByText('量比标缺')).toHaveLength(2);
    expect(within(panel).getAllByText('OI比标缺')).toHaveLength(2);
    // NVDA 的异动仍可用（大单照常）；TSLA 异动无返回行 → 显式标缺。
    expect(within(panel).getByText('大单 $1.2M')).toBeInTheDocument();
    expect(within(panel).getByText('大单标缺')).toBeInTheDocument();
    // 行仍全部在列：fail-closed 标缺，不隐藏任何深度层标的。
    expect(within(panel).getByText('NVDA')).toBeInTheDocument();
    expect(within(panel).getByText('TSLA')).toBeInTheDocument();
  });

  it('renders a null ratio as 标缺 with the server reason instead of 0 or infinity', async () => {
    vi.mocked(fetchOpportunityOptionWalls).mockResolvedValue(wallResponse([
      {
        ...wallItem('NVDA', 2.0, 1.0),
        ratios: {
          callPutOiRatio: ratio(1.0, 'settled_open_interest_prior_session'),
          callPutVolumeRatio: ratio(
            null,
            'current_session_cumulative_volume',
            'put_volume_total_zero',
          ),
          caveat: '这两个比例在本账户数据上尚未被检验过。',
        },
      },
    ]));
    vi.mocked(fetchOpportunityOptionEvents).mockResolvedValue(eventResponse([
      eventItem('NVDA', []),
    ]));
    render(<PremarketOptionAnomalyPanel top={topStub('premarket', ['NVDA'])} />);

    const panel = screen.getByLabelText('盘前期权异常');
    expect(
      await within(panel).findByText('量比 C/P 标缺（put_volume_total_zero）'),
    ).toBeInTheDocument();
    // 无定义比例绝不参与偏斜判定。
    expect(within(panel).queryByText(/偏斜 · 量比/)).not.toBeInTheDocument();
    // 异动页可读但最近一页没有带金额成交：如实陈述，不等同「无大单」章。
    expect(within(panel).getByText('最近一页无带金额成交')).toBeInTheDocument();
  });

  it('stays collapsed outside premarket/opening_probe and only fetches after manual expand', async () => {
    render(<PremarketOptionAnomalyPanel top={topStub('prime', ['NVDA'])} />);

    const panel = screen.getByLabelText('盘前期权异常');
    const toggle = within(panel).getByRole('button', {
      name: '展开或收起盘前期权异常面板',
    });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(
      within(panel).getByText(/当前为次要参考（昨日事实）/),
    ).toBeInTheDocument();
    // 收起态零请求：面板承载的是昨日事实，不该在盘中偷跑取数。
    expect(fetchOpportunityOptionWalls).not.toHaveBeenCalled();
    expect(fetchOpportunityOptionEvents).not.toHaveBeenCalled();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => {
      expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1);
    });
    expect(fetchOpportunityOptionEvents).toHaveBeenCalledTimes(1);
  });

  it('auto-expands during opening_probe (first 30 minutes) as part of the briefing window', () => {
    render(<PremarketOptionAnomalyPanel top={topStub('opening_probe', ['NVDA'])} />);
    expect(
      screen.getByRole('button', { name: '展开或收起盘前期权异常面板' }),
    ).toHaveAttribute('aria-expanded', 'true');
  });

  it('renders an honest empty state when no deep-lane tickers are available', () => {
    render(<PremarketOptionAnomalyPanel top={null} />);
    // 无数据时（top=null）prominent 不成立 → 收起态提示；展开后显式空态。
    fireEvent.click(
      screen.getByRole('button', { name: '展开或收起盘前期权异常面板' }),
    );
    expect(
      screen.getByText(/暂无深度层名单可查/),
    ).toBeInTheDocument();
    expect(fetchOpportunityOptionWalls).not.toHaveBeenCalled();
  });
});
