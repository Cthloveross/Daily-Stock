import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { LaneChecklistPanel } from '../LaneChecklistPanel';
import { useIntradayManualBudgetStore } from '../../../stores/intradayPlanStore';
import type {
  IntradayLaneAvailability,
  IntradayPulseResponse,
  IntradayTopCandidate,
  IntradayTopResponse,
} from '../../../types/opportunities';

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

function top(
  candidates: IntradayTopCandidate[] = [],
  laneAvailability: IntradayLaneAvailability | null = null,
): IntradayTopResponse {
  return {
    marketDateEt: MARKET_DATE,
    candidates,
    laneAvailability,
  } as unknown as IntradayTopResponse;
}

/** 车道可用性区块：默认给一个「全部可读」的形状，逐用例覆写。 */
function availability(
  overrides: Partial<IntradayLaneAvailability> = {},
): IntradayLaneAvailability {
  const tickers = overrides.tickers ?? [];
  return {
    formulaVersion: 'lane-availability/v1',
    marketDateEt: MARKET_DATE,
    maxDte: 7,
    dayType: 'overnight_only',
    dayTypeReason: '深度层标的今日均无 0DTE 到期，日内车道关闭（V2-E）',
    basis: 'per_ticker_option_expiry_metadata_within_0_7_dte_v1',
    checkedScope: 'intraday_deep_lane_tickers',
    checkedCount: tickers.length,
    readableCount: tickers.filter((item) => item.state === 'ready').length,
    unavailableCount: tickers.filter((item) => item.state !== 'ready').length,
    zeroDteTickers: [],
    deferredTickers: [],
    limitations: [],
    ...overrides,
    // tickers 是必填字段：overrides 里可能没给（Partial），用上面归一化过的本地值兜底。
    tickers,
  };
}

function dayTypeLine() {
  return document.querySelector('[data-day-type]') as HTMLElement | null;
}

function hardBlock(id: string) {
  return document.querySelector(`[data-hard-block="${id}"]`) as HTMLElement | null;
}

function setDte(value: string) {
  fireEvent.change(screen.getByLabelText('进场 DTE'), { target: { value } });
}

function check(id: string) {
  return document.querySelector(`[data-check-id="${id}"]`) as HTMLElement | null;
}

function budgetCell(id: string) {
  return document.querySelector(`[data-budget-id="${id}"]`) as HTMLElement | null;
}

describe('LaneChecklistPanel', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useIntradayManualBudgetStore.getState().reset();
  });

  it('carries exactly one framing line and no read-only boilerplate', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    expect(await screen.findByText(/开仓前车道检查 · 对照你自己的规则/)).toBeInTheDocument();
    expect(screen.getByText(/按你自己的规则机械核对，不是买卖建议/)).toBeInTheDocument();
    // 用户明确要求删掉的样板话，一个字都不许留在正文。
    expect(screen.queryByText(/本系统只读/)).not.toBeInTheDocument();
    expect(screen.queryByText(/不会下单/)).not.toBeInTheDocument();
    // 长口径 caveat 不占正文，但内容仍在（挂 tooltip 的 aria-label）。
    expect(
      screen.getByLabelText(/样本窗口仅 2026-04→07 一个市场状态/),
    ).toBeInTheDocument();
    // 也不得出现任何「你应该 / 建议你」式的指令性措辞。
    expect(screen.queryByText(/你应该|建议你|推荐你/)).not.toBeInTheDocument();
  });

  // --- 「系统已经知道的事」不得显示成标缺 -----------------------------------

  it('states today requirement instead of 标缺 when no DTE is typed（日内可用日）', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({
          dayType: 'intraday_available',
          zeroDteTickers: ['NVDA'],
        }))}
      />,
    );
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('requirement'));
    expect(check('dte')?.getAttribute('aria-label')).toContain('今日只能 0DTE');
    expect(check('dte')?.getAttribute('aria-label')).not.toContain('标缺');
  });

  it('states 「今日只能 4-7DTE（无 0DTE）」 on an overnight-only day', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({ dayType: 'overnight_only' }))}
      />,
    );
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('requirement'));
    expect(check('dte')).toHaveTextContent('今日只能 4-7DTE（今日无可用 0DTE）');
    expect(check('dte')?.getAttribute('aria-label')).toContain('绝不退而买 1-3DTE');
  });

  it('flips the DTE row to pass/fail only once a DTE is actually typed', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({
          dayType: 'intraday_available',
          zeroDteTickers: ['NVDA'],
        }))}
      />,
    );
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('requirement'));
    setDte('0');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('pass'));
    setDte('5');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('fail'));
  });

  it('states the live ET clock and the binding window on the clock row', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({
          dayType: 'intraday_available',
          zeroDteTickers: ['NVDA'],
        }))}
      />,
    );
    await waitFor(() => expect(check('clock')?.dataset.checkStatus).toBe('pass'));
    expect(check('clock')?.getAttribute('aria-label')).toContain('09:30 ET');
    expect(check('clock')?.getAttribute('aria-label')).toContain('ET 09:30–12:00');
  });

  it('fails the clock row after the 12:00 ET cutoff citing V2-C③', async () => {
    // 2026-08-05 16:05Z = 12:05 ET。
    render(
      <LaneChecklistPanel
        pulse={pulse('2026-08-05T16:05:00Z')}
        top={top([], availability({
          dayType: 'intraday_available',
          zeroDteTickers: ['NVDA'],
        }))}
      />,
    );
    await waitFor(() => expect(check('clock')?.dataset.checkStatus).toBe('fail'));
    expect(check('clock')?.getAttribute('aria-label')).toContain('V2-C③');
  });

  it('日内 lane: 11:59 ET still passes and 12:00 ET flips to fail (boundary)', async () => {
    const dayAvailable = availability({
      dayType: 'intraday_available',
      zeroDteTickers: ['NVDA'],
    });
    // 15:59Z = 11:59 ET。
    const { unmount } = render(
      <LaneChecklistPanel pulse={pulse('2026-08-05T15:59:00Z')} top={top([], dayAvailable)} />,
    );
    await waitFor(() => expect(check('clock')?.dataset.checkStatus).toBe('pass'));
    unmount();

    // 16:00Z = 12:00 ET。
    render(
      <LaneChecklistPanel pulse={pulse('2026-08-05T16:00:00Z')} top={top([], dayAvailable)} />,
    );
    await waitFor(() => expect(check('clock')?.dataset.checkStatus).toBe('fail'));
  });

  it('drops the earnings row entirely（没有标的时它只能是噪音）', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    await waitFor(() => expect(check('dte')).not.toBeNull());
    expect(check('earnings')).toBeNull();
  });

  // --- 冷启动：读取中 ≠ 标缺 ------------------------------------------------

  it('cold start renders 读取中 rather than 标缺 while the first scan is in flight', async () => {
    render(<LaneChecklistPanel pulse={null} top={null} loading />);
    await waitFor(() => expect(dayTypeLine()?.dataset.dayType).toBe('loading'));
    expect(dayTypeLine()).toHaveTextContent('今日车道可用性读取中…');
    expect(dayTypeLine()).not.toHaveTextContent('标缺');
    expect(screen.getByText('ET 时钟读取中…')).toBeInTheDocument();
    expect(check('clock')?.dataset.checkStatus).toBe('requirement');
  });

  it('marks 标缺 once the scan came back without a lane availability block', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} loading={false} />);
    await waitFor(() => expect(dayTypeLine()?.dataset.dayType).toBe('unknown'));
    expect(dayTypeLine()).toHaveTextContent('车道可用性标缺');
    expect(dayTypeLine()?.getAttribute('aria-label')).toContain('未知≠「今天没有 0DTE」');
  });

  // --- 今日无 0DTE：全屏最重要的一件事 --------------------------------------

  it('renders a loud full-width warning block when today has no 0DTE', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({ dayType: 'overnight_only' }))}
      />,
    );
    await waitFor(() => expect(dayTypeLine()?.dataset.dayType).toBe('overnight_only'));
    const block = dayTypeLine() as HTMLElement;
    expect(block).toHaveTextContent('今日无 0DTE · 日内车道关闭');
    expect(block).toHaveTextContent('周二/周四历史 −2.61%/−3.74%，1DTE 当日 −4.97%');
    expect(block).toHaveTextContent('绝不退而买 1-3DTE');
    // 大字：h3 级标题，而不是清单里的一行小字。
    expect(block.querySelector('.text-h3')).not.toBeNull();
  });

  it('disables the 日内 lane button with a reason on an overnight-only day', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({ dayType: 'overnight_only' }))}
      />,
    );
    const intradayButton = await screen.findByRole('button', { name: /日内 · 今日关闭/ });
    expect(intradayButton).toBeDisabled();
    expect(intradayButton.getAttribute('aria-label')).toContain(
      '今日无可用 0DTE，日内车道关闭（V2-E）',
    );
    // 默认落在今天真正成立的车道上，而不是让用户先撞一堵失败墙。
    expect(screen.getByRole('button', { name: '过夜' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('day type: 日内车道可用 names the tickers that actually have a 0DTE', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({
          dayType: 'intraday_available',
          zeroDteTickers: ['NVDA', 'TSLA', 'MU'],
          tickers: [],
        }))}
      />,
    );

    await waitFor(() => expect(dayTypeLine()?.dataset.dayType).toBe('intraday_available'));
    expect(dayTypeLine()).toHaveTextContent('今日：日内车道可用（NVDA/TSLA/MU 有 0DTE）');
    expect(dayTypeLine()?.getAttribute('aria-label')).toContain(
      'V2-E · 按合约可用性决定今天做不做日内（周二/周四＝过夜日）',
    );
    expect(hardBlock('day_type_overnight_only')).toBeNull();
    expect(screen.getByRole('button', { name: '日内' })).not.toBeDisabled();
  });

  it('day type: 仅黑名单标的有 0DTE is said out loud and closes the 日内 lane', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({
          dayType: 'intraday_available',
          zeroDteTickers: ['QQQ'],
        }))}
      />,
    );

    await waitFor(() => expect(dayTypeLine()?.dataset.dayType).toBe('blacklist_only'));
    expect(dayTypeLine()).toHaveTextContent('今日仅黑名单标的有 0DTE（QQQ）· 日内车道关闭');
    expect(await screen.findByRole('button', { name: /日内 · 今日关闭/ })).toBeDisabled();
  });

  it('day type: a non-blacklist ticker with 0DTE stays available even alongside QQQ', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({
          dayType: 'intraday_available',
          zeroDteTickers: ['QQQ', 'NVDA'],
        }))}
      />,
    );

    await waitFor(() => expect(dayTypeLine()?.dataset.dayType).toBe('intraday_available'));
    expect(dayTypeLine()).toHaveTextContent('今日：日内车道可用（NVDA 有 0DTE）');
    expect(dayTypeLine()?.getAttribute('aria-label')).toContain('QQQ');
  });

  it('day type: unknown when the chain is unreadable — never implies 「今天没有 0DTE」', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({
          dayType: 'unknown',
          dayTypeReason: '1 个标的的期权到期日读不到（MU），未知≠「今天没有 0DTE」',
          tickers: [
            {
              ticker: 'MU',
              state: 'unavailable',
              hasZeroDte: null,
              availableDteList: [],
              expiries: [],
              unavailableReason: 'option_expiry_metadata_unavailable',
            },
          ],
        }))}
      />,
    );

    await waitFor(() => expect(dayTypeLine()?.dataset.dayType).toBe('unknown'));
    expect(dayTypeLine()).toHaveTextContent('标缺');
    expect(hardBlock('day_type_overnight_only')).toBeNull();
    expect(hardBlock('day_type_blacklist_only')).toBeNull();
  });

  // --- V2-C 硬禁止 ----------------------------------------------------------

  it('renders V2-C hard blocks regardless of the selected lane', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    setDte('2');
    await waitFor(() =>
      expect(document.querySelector('[data-hard-block="dte_1_3"]')).toBeInTheDocument(),
    );
    expect(screen.getByText(/硬禁止 V2-C①/)).toBeInTheDocument();

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

  it('過夜 lane: carries the no-speed-exit reminder verbatim', async () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top([], availability({ dayType: 'overnight_only' }))}
      />,
    );
    expect(
      await screen.findByText(/本车道不适用速度衰竭离场（V2-B）/),
    ).toBeInTheDocument();
  });

  // --- 手动额度计数（替换永远标缺的 Journal 读数）---------------------------

  it('starts the manual budget counters at 0 and increments/decrements them', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    await waitFor(() => expect(budgetCell('intraday_tickets')).toHaveTextContent('0/6'));
    expect(budgetCell('overnight_positions')).toHaveTextContent('0/3');
    // 不再出现「标缺」的额度读数——它以前恒为标缺，等于没有。
    expect(budgetCell('intraday_tickets')).not.toHaveTextContent('标缺');

    fireEvent.click(screen.getByRole('button', { name: '今日日内单 加 1' }));
    fireEvent.click(screen.getByRole('button', { name: '今日日内单 加 1' }));
    await waitFor(() => expect(budgetCell('intraday_tickets')).toHaveTextContent('2/6'));

    fireEvent.click(screen.getByRole('button', { name: '今日日内单 减 1' }));
    await waitFor(() => expect(budgetCell('intraday_tickets')).toHaveTextContent('1/6'));

    // 下界 0：不会数出负数。
    fireEvent.click(screen.getByRole('button', { name: '当前过夜持仓 减 1' }));
    await waitFor(() => expect(budgetCell('overnight_positions')).toHaveTextContent('0/3'));
  });

  it('labels the manual counters as manually maintained and warns at the cap', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    expect(await screen.findByText('手动维护 · 按 ET 交易日自动归零')).toBeInTheDocument();
    for (let i = 0; i < 6; i += 1) {
      fireEvent.click(screen.getByRole('button', { name: '今日日内单 加 1' }));
    }
    await waitFor(() => expect(budgetCell('intraday_tickets')).toHaveTextContent('6/6'));
    expect(
      budgetCell('intraday_tickets')?.querySelector('.text-warn-strong'),
    ).not.toBeNull();
  });
});
