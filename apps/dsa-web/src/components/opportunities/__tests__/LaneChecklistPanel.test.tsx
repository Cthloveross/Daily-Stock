import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LaneChecklistPanel } from '../LaneChecklistPanel';
import { fetchPersonalEdge } from '../../../api/journal';
import type {
  PersonalEdgeResponse,
  RuleComplianceDailyBudget,
} from '../../../types/journal';
import type {
  IntradayPulseResponse,
  IntradayTopCandidate,
  IntradayTopResponse,
} from '../../../types/opportunities';

vi.mock('../../../api/journal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/journal')>();
  return { ...actual, fetchPersonalEdge: vi.fn() };
});

const MARKET_DATE = '2026-08-05';

/** 2026-08-05 13:30Z = 09:30 ET（EDT）。 */
function pulse(generatedAt = '2026-08-05T13:30:00Z'): IntradayPulseResponse {
  return {
    schemaVersion: 'intraday-pulse/1.0',
    generatedAt,
    marketDateEt: MARKET_DATE,
    sessionState: 'regular',
    sessionStateBasis: 'america_new_york_clock_v1',
    sessionPhase: 'opening_probe',
    sessionPhaseLabel: '开盘试错',
    sessionPhaseHintBasis: 'user_trading_history_hardcoded_v1',
    items: [],
    limitations: [],
  };
}

function candidate(
  ticker: string,
  proximity: Partial<IntradayTopCandidate['earningsProximity']> = {},
): IntradayTopCandidate {
  return {
    ticker,
    earningsProximity: {
      state: 'ready',
      daysToEarnings: 30,
      earningsDate: '2026-09-04',
      withinBlackout: false,
      blackoutDays: 3,
      windowDays: 60,
      basis: 'finnhub_earnings_calendar_forward_window',
      source: 'finnhub',
      fetchedAt: '2026-08-05T13:00:00Z',
      unavailableReason: null,
      ...proximity,
    },
  } as unknown as IntradayTopCandidate;
}

function top(candidates: IntradayTopCandidate[] = []): IntradayTopResponse {
  return {
    marketDateEt: MARKET_DATE,
    candidates,
  } as unknown as IntradayTopResponse;
}

function budget(
  overrides: Partial<RuleComplianceDailyBudget> = {},
): RuleComplianceDailyBudget {
  return {
    asOfTradingDay: MARKET_DATE,
    intradayTicketCount: 2,
    intradayTicketLimit: 6,
    intradayReason: null,
    overnightOpenCount: 1,
    overnightConcurrentLimit: 3,
    overnightReason: null,
    overnightUnknownDteOpenCount: 0,
    ...overrides,
  };
}

function edge(dailyBudget: RuleComplianceDailyBudget | null): PersonalEdgeResponse {
  return {
    schemaVersion: 'journal-personal-edge/1.0',
    dataState: 'ready',
    accountKey: 'default_moomoo_us',
    buildId: 3,
    buildKey: 'abc',
    sourceKind: 'csv_batch',
    computedAt: '2026-08-05T13:30:00Z',
    firstOpenedAt: null,
    lastClosedAt: null,
    closedEpisodeCount: 1407,
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
    monthBasis: 'opened_at_utc_minus_4_approximation',
    limitations: [],
    ruleCompliance: dailyBudget
      ? ({
        adoptedAt: '2026-08-05',
        excludeTopN: 5,
        dailyBudget,
        limitations: ['样本窗口仅 2026-04→07 一个市场状态（SPY 上行）'],
      } as unknown as PersonalEdgeResponse['ruleCompliance'])
      : null,
  };
}

function setDte(value: string) {
  fireEvent.change(screen.getByLabelText('进场 DTE'), { target: { value } });
}

function setTicker(value: string) {
  fireEvent.change(screen.getByLabelText('标的'), { target: { value } });
}

function check(id: string) {
  return document.querySelector(`[data-check-id="${id}"]`) as HTMLElement | null;
}

function budgetCell(id: string) {
  return document.querySelector(`[data-budget-id="${id}"]`) as HTMLElement | null;
}

describe('LaneChecklistPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchPersonalEdge).mockReset();
    vi.mocked(fetchPersonalEdge).mockResolvedValue(edge(budget()));
  });

  it('says plainly it checks the user own rules and never places an order', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    expect(await screen.findByText(/开仓前车道检查 · 对照你自己的规则/)).toBeInTheDocument();
    expect(screen.getByText(/不会下单/)).toBeInTheDocument();
    // 面板自述其定位：不是推荐、不含概率。
    expect(screen.getByText(/不是推荐、不含概率/)).toBeInTheDocument();
    // 也不得出现任何「你应该 / 建议你」式的指令性措辞。
    expect(screen.queryByText(/你应该|建议你|推荐你/)).not.toBeInTheDocument();
  });

  it('日内 lane: passes 0DTE before 12:00 ET and a ticker outside the blackout', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top([candidate('AAPL')])} />);
    setDte('0');
    setTicker('AAPL');

    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('pass'));
    expect(check('dte')?.getAttribute('aria-label')).toContain('V2-A');
    expect(check('clock')?.dataset.checkStatus).toBe('pass');
    expect(check('clock')?.getAttribute('aria-label')).toContain('09:30 ET');
    expect(check('earnings')?.dataset.checkStatus).toBe('pass');
  });

  it('日内 lane: fails a non-zero DTE and fails after the 12:00 ET cutoff', async () => {
    // 2026-08-05 16:05Z = 12:05 ET。
    render(
      <LaneChecklistPanel pulse={pulse('2026-08-05T16:05:00Z')} top={top()} />,
    );
    setDte('5');

    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('fail'));
    expect(check('dte')?.getAttribute('aria-label')).toContain('V2-A');
    expect(check('clock')?.dataset.checkStatus).toBe('fail');
    expect(check('clock')?.getAttribute('aria-label')).toContain('V2-C③');
  });

  it('日内 lane: 11:59 ET still passes and 12:00 ET flips to fail (boundary)', async () => {
    // 15:59Z = 11:59 ET。
    const { unmount } = render(
      <LaneChecklistPanel pulse={pulse('2026-08-05T15:59:00Z')} top={top()} />,
    );
    setDte('0');
    await waitFor(() => expect(check('clock')?.dataset.checkStatus).toBe('pass'));
    unmount();

    // 16:00Z = 12:00 ET。
    render(<LaneChecklistPanel pulse={pulse('2026-08-05T16:00:00Z')} top={top()} />);
    setDte('0');
    await waitFor(() => expect(check('clock')?.dataset.checkStatus).toBe('fail'));
  });

  it('日内 lane: 标缺 for missing DTE, missing clock, and unknown earnings', async () => {
    render(<LaneChecklistPanel pulse={null} top={top()} />);

    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('missing'));
    expect(check('clock')?.dataset.checkStatus).toBe('missing');
    // 未填标的 → 标缺（未知≠安全）。
    expect(check('earnings')?.dataset.checkStatus).toBe('missing');
    expect(check('earnings')?.getAttribute('aria-label')).toContain('未知≠安全');
  });

  it('日内 lane: a ticker outside the deep lane is 标缺, never a silent pass', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top([candidate('AAPL')])} />);
    setTicker('TSLA');
    await waitFor(() => expect(check('earnings')?.dataset.checkStatus).toBe('missing'));
    expect(check('earnings')?.getAttribute('aria-label')).toContain('不在今日深度扫描层');
  });

  it('日内 lane: fails a ticker inside the earnings blackout', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([candidate('NVDA', { daysToEarnings: 1, withinBlackout: true })])}
      />,
    );
    setTicker('nvda');
    await waitFor(() => expect(check('earnings')?.dataset.checkStatus).toBe('fail'));
    expect(check('earnings')?.getAttribute('aria-label')).toContain('回避窗');
  });

  it('日内 lane: an unavailable earnings calendar is 标缺 with its reason', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([
          candidate('AAPL', {
            state: 'unavailable',
            withinBlackout: null,
            daysToEarnings: null,
            unavailableReason: 'Finnhub 超时',
          }),
        ])}
      />,
    );
    setTicker('AAPL');
    await waitFor(() => expect(check('earnings')?.dataset.checkStatus).toBe('missing'));
    expect(check('earnings')?.getAttribute('aria-label')).toContain('Finnhub 超时');
  });

  it('过夜 lane: 4 and 7 DTE pass, 8 fails, and the weak ET hours fail', async () => {
    // 15:30Z = 11:30 ET → V2-B 偏弱时段。
    render(
      <LaneChecklistPanel pulse={pulse('2026-08-05T15:30:00Z')} top={top()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '过夜' }));

    setDte('4');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('pass'));
    setDte('7');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('pass'));
    setDte('8');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('fail'));
    expect(check('dte')?.getAttribute('aria-label')).toContain('V2-B');

    expect(check('clock')?.dataset.checkStatus).toBe('fail');
    expect(check('clock')?.getAttribute('aria-label')).toContain('13:00–14:00');
    // 过夜车道没有财报检查（V2-B 不含该条款），不得凭空发明一条。
    expect(check('earnings')).toBeNull();
  });

  it('过夜 lane: carries the no-speed-exit reminder verbatim', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    fireEvent.click(screen.getByRole('button', { name: '过夜' }));
    expect(
      await screen.findByText(/本车道不适用速度衰竭离场（V2-B）/),
    ).toBeInTheDocument();
  });

  it('renders V2-C hard blocks regardless of the selected lane', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    // 1-3DTE：即便选的是日内车道也必须列出。
    setDte('2');
    await waitFor(() =>
      expect(document.querySelector('[data-hard-block="dte_1_3"]')).toBeInTheDocument(),
    );
    expect(screen.getByText(/硬禁止 V2-C①/)).toBeInTheDocument();

    // 4-7DTE + 打算今天平掉 → V2-C②。
    setDte('5');
    fireEvent.click(screen.getByLabelText('打算今天就平掉'));
    await waitFor(() =>
      expect(
        document.querySelector('[data-hard-block="bought_time_unused"]'),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/硬禁止 V2-C②/)).toBeInTheDocument();
  });

  it('renders the late-0DTE hard block after 12:00 ET', async () => {
    render(
      <LaneChecklistPanel pulse={pulse('2026-08-05T18:00:00Z')} top={top()} />,
    );
    setDte('0');
    await waitFor(() =>
      expect(document.querySelector('[data-hard-block="late_0dte"]')).toBeInTheDocument(),
    );
    expect(screen.getByText(/硬禁止 V2-C③/)).toBeInTheDocument();
  });

  it('reads the daily budget when the build is as of today', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    await waitFor(() => expect(budgetCell('intraday_tickets')).toHaveTextContent('2/6'));
    expect(budgetCell('overnight_positions')).toHaveTextContent('1/3');
  });

  it('marks the budget 标缺 with a reason when the build predates today, never 0', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(
      edge(budget({ asOfTradingDay: '2026-07-31', intradayTicketCount: 0 })),
    );
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);

    await waitFor(() => expect(budgetCell('intraday_tickets')).toHaveTextContent('标缺'));
    expect(budgetCell('intraday_tickets')).not.toHaveTextContent('0/6');
    expect(budgetCell('intraday_tickets')?.getAttribute('aria-label')).toContain(
      '证据 build 截至 2026-07-31',
    );
    expect(budgetCell('overnight_positions')).toHaveTextContent('标缺');
  });

  it('marks the budget 标缺 when the personal-edge endpoint is unavailable', async () => {
    vi.mocked(fetchPersonalEdge).mockRejectedValue(new Error('endpoint down'));
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);

    await waitFor(() => expect(budgetCell('intraday_tickets')).toHaveTextContent('标缺'));
    expect(budgetCell('intraday_tickets')?.getAttribute('aria-label')).toContain(
      '不以 0 冒充额度',
    );
  });
});
