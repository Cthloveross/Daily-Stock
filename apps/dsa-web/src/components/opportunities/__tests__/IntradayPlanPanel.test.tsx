import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntradayPlanPanel } from '../IntradayPlanPanel';
import { fetchNearExpiryContracts } from '../../../api/opportunities';
import { useIntradayPlanStore } from '../../../stores/intradayPlanStore';
import type {
  IntradayLaneAvailability,
  IntradayPulseResponse,
  IntradayTopCandidate,
  IntradayTopResponse,
  NearExpiryContractResponse,
  NearExpiryContractRow,
} from '../../../types/opportunities';

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return { ...actual, fetchNearExpiryContracts: vi.fn() };
});

const MARKET_DATE = '2026-08-05';
const PULSE_AT = '2026-08-05T13:30:00Z';

function pulse(): IntradayPulseResponse {
  return { generatedAt: PULSE_AT, marketDateEt: MARKET_DATE } as unknown as IntradayPulseResponse;
}

function laneAvailability(
  overrides: Partial<IntradayLaneAvailability> = {},
): IntradayLaneAvailability {
  return {
    formulaVersion: 'lane-availability/v1',
    marketDateEt: MARKET_DATE,
    maxDte: 7,
    dayType: 'intraday_available',
    dayTypeReason: '',
    basis: 'per_ticker_option_expiry_metadata_within_0_7_dte_v1',
    checkedScope: 'intraday_deep_lane_tickers',
    checkedCount: 1,
    readableCount: 1,
    unavailableCount: 0,
    zeroDteTickers: ['NVDA'],
    deferredTickers: [],
    tickers: [],
    limitations: [],
    ...overrides,
  };
}

function top(overrides: Partial<IntradayTopResponse> = {}): IntradayTopResponse {
  return {
    marketDateEt: MARKET_DATE,
    candidates: [] as IntradayTopCandidate[],
    laneAvailability: laneAvailability(),
    ...overrides,
  } as unknown as IntradayTopResponse;
}

function contractRow(overrides: Partial<NearExpiryContractRow>): NearExpiryContractRow {
  return {
    code: 'US.NVDA260805C100000',
    right: 'C',
    strike: 100,
    expiry: '2026-08-05',
    dte: 0,
    bid: null,
    ask: null,
    mid: null,
    spreadPercent: null,
    spreadUnavailableReason: null,
    lastPrice: null,
    sessionVolume: 10,
    openInterest: 20,
    ivPercent: 30,
    delta: 0.5,
    quoteAsOf: '2026-08-05 09:30:00',
    quoteState: 'observed',
    unavailableReason: null,
    isAtm: false,
    ...overrides,
  };
}

function contractsResponse(rows: NearExpiryContractRow[], dte = 0): NearExpiryContractResponse {
  return {
    schemaVersion: 'near-expiry-contracts/1.0',
    generatedAt: PULSE_AT,
    marketDateEt: MARKET_DATE,
    item: {
      ticker: 'NVDA',
      state: 'ready',
      source: 'moomoo_openapi',
      fetchedAt: PULSE_AT,
      formulaVersion: 'v1',
      maxDte: dte,
      spot: 100,
      spotAsOf: '2026-08-05 09:30:00',
      openInterestAsOf: '2026-08-04',
      openInterestBasis: 'prior_clearing_session',
      strikeWindow: { percentBand: 5, minStrikesPerSide: 3, basis: 'x' },
      coverage: {
        requestedContracts: rows.length,
        snapshotReceivedContracts: rows.length,
        observedContracts: rows.length,
        missingContracts: 0,
        failedBatches: 0,
        excludedNonstandardContracts: 0,
        excludedUnknownStandardTypeContracts: 0,
      },
      expiries: [
        {
          expiry: '2026-08-05',
          dte,
          state: 'ready',
          contractCount: rows.length,
          observedQuoteCount: rows.length,
          contracts: rows,
        },
      ],
      message: '',
      limitations: [],
    },
  } as unknown as NearExpiryContractResponse;
}

/** 等到指定合约行渲染出来（querySelector 返回 null 不会让 waitFor 重试）。 */
async function findContractRow(code: string): Promise<HTMLElement> {
  return waitFor(() => {
    const row = document.querySelector(`[data-contract-code="${code}"]`);
    if (!row) throw new Error(`contract row ${code} not rendered yet`);
    return row as HTMLElement;
  });
}

function renderPanel(props: Partial<Parameters<typeof IntradayPlanPanel>[0]> = {}) {
  return render(
    <IntradayPlanPanel pulse={pulse()} top={top()} loading={false} {...props} />,
  );
}

describe('IntradayPlanPanel', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useIntradayPlanStore.getState().clear();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(contractsResponse([]));
  });

  it('carries exactly one framing line and no advice wording', () => {
    renderPanel();
    expect(screen.getByText(/按你自己的规则机械核对，不是买卖建议/)).toBeInTheDocument();
    expect(screen.queryByText(/本系统只读|不会下单|建议买入|可以进场|目标价|胜率/)).toBeNull();
  });

  it('shows an honest empty state pointing at the scan table below', () => {
    renderPanel();
    expect(screen.getByText(/还没有加入盘中计划的标的/)).toBeInTheDocument();
    expect(screen.getByText(/换一个交易日自动为空/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '清空盘中计划' })).toBeNull();
  });

  it('renders a per-ticker card with the day lane, checks and 清空 action', async () => {
    useIntradayPlanStore.getState().promote('NVDA');
    renderPanel();

    const card = await screen.findByLabelText('NVDA 逐条核对');
    expect(within(card).getByText('合约期限')).toBeInTheDocument();
    expect(within(card).getByText('开仓时点')).toBeInTheDocument();
    expect(within(card).getByText('财报回避')).toBeInTheDocument();
    expect(within(card).getByText('近30分位移')).toBeInTheDocument();
    expect(screen.getByText(/今日车道：日内可用（V2-E）/)).toBeInTheDocument();
    // 不在深度层 → 逐条标缺，绝不静默放行。
    expect(screen.getByText(/未在今日深度层 · 扫描读数逐条标缺/)).toBeInTheDocument();
    // 中性失效位提示，不含任何价位目标。
    expect(
      screen.getByText(/进场前先写下失效位；说不清就不开（结构止损）/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清空盘中计划' }));
    await waitFor(() =>
      expect(screen.getByText(/还没有加入盘中计划的标的/)).toBeInTheDocument());
  });

  it('requests 0DTE contracts on a 日内 day and 0–7DTE on an overnight day', async () => {
    useIntradayPlanStore.getState().promote('NVDA');
    const { unmount } = renderPanel();
    await waitFor(() =>
      expect(fetchNearExpiryContracts).toHaveBeenCalledWith('NVDA', 0, { refresh: false }));
    unmount();

    vi.mocked(fetchNearExpiryContracts).mockClear();
    renderPanel({
      top: top({ laneAvailability: laneAvailability({ dayType: 'overnight_only', zeroDteTickers: [] }) }),
    });
    await waitFor(() =>
      expect(fetchNearExpiryContracts).toHaveBeenCalledWith('NVDA', 7, { refresh: false }));
    expect(await screen.findByText(/合约候选 · 4-7DTE（V2-B 过夜车道）/)).toBeInTheDocument();
  });

  it('highlights the $2-8 sweet spot, greys and warns <$1, and shows 张数/手续费', async () => {
    useIntradayPlanStore.getState().promote('NVDA');
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(contractsResponse([
      contractRow({ code: 'CHEAP', bid: 0.45, ask: 0.55, mid: 0.5, spreadPercent: 20 }),
      contractRow({ code: 'SWEET', strike: 105, bid: 3.9, ask: 4.1, mid: 4, spreadPercent: 5 }),
    ]));
    renderPanel();

    const cheap = await findContractRow('CHEAP');
    const sweet = await findContractRow('SWEET');

    expect(cheap.dataset.priceBand).toBe('below_1');
    expect(cheap.className).toContain('opacity-60');
    expect(within(cheap).getByText(/<\$1 · 你的历史最差档/)).toBeInTheDocument();
    // $3,000 / ($0.50×100) = 60 张 × $3.29 = $197.40 → 费/本金 6.58%。
    expect(within(cheap).getByText('60 张')).toBeInTheDocument();
    expect(within(cheap).getByText('$197.40')).toBeInTheDocument();
    expect(within(cheap).getByText('6.58%')).toBeInTheDocument();
    // 流动性差沿用既有 15% 阈值。
    expect(within(cheap).getByText('流动性差')).toBeInTheDocument();

    expect(sweet.dataset.priceBand).toBe('sweet_2_8');
    expect(sweet.className).toContain('bg-bg-2');
    expect(within(sweet).getByText(/\$2-8 甜蜜区/)).toBeInTheDocument();
    expect(within(sweet).getByText('7 张')).toBeInTheDocument();
    expect(within(sweet).getByText('$23.03')).toBeInTheDocument();
    expect(within(sweet).getByText('0.82%')).toBeInTheDocument();
  });

  it('recomputes 张数/手续费 from the local standard size and never persists it', async () => {
    useIntradayPlanStore.getState().promote('NVDA');
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(contractsResponse([
      contractRow({ code: 'SWEET', bid: 1.9, ask: 2.1, mid: 2, spreadPercent: 10 }),
    ]));
    renderPanel();

    const row = await findContractRow('SWEET');
    expect(within(row).getByText('15 张')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('标准仓位（美元）'), { target: { value: '1000' } });
    await waitFor(() => expect(within(row).getByText('5 张')).toBeInTheDocument());
    // 仓位只是本地 UI 状态：不落盘，也不写进任何 store。
    expect(window.localStorage.getItem('dsa-intraday-standard-size')).toBeNull();
    expect(JSON.stringify(window.localStorage)).not.toContain('1000');
  });

  it('marks a missing contract price 标缺 instead of pretending it is 0', async () => {
    useIntradayPlanStore.getState().promote('NVDA');
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(contractsResponse([
      contractRow({ code: 'NOQUOTE', quoteState: 'unavailable' }),
    ]));
    renderPanel();

    const row = await findContractRow('NOQUOTE');
    expect(row.dataset.priceBand).toBe('unknown');
    expect(within(row).getAllByText('标缺').length).toBeGreaterThanOrEqual(3);
  });
});
