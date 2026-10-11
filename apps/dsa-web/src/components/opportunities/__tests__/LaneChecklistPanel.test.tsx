import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LaneChecklistPanel } from '../LaneChecklistPanel';
import type {
  IntradayLaneAvailability,
  IntradayExecutionPolicy,
  IntradayPulseResponse,
  IntradayTopResponse,
} from '../../../types/opportunities';

const MARKET_DATE = '2026-08-05';

function pulse(generatedAt = '2026-08-05T13:30:00Z'): IntradayPulseResponse {
  return {
    generatedAt,
    marketDateEt: MARKET_DATE,
  } as unknown as IntradayPulseResponse;
}

function availability(
  overrides: Partial<IntradayLaneAvailability> = {},
): IntradayLaneAvailability {
  const tickers = overrides.tickers ?? [{
    ticker: 'NVDA',
    state: 'ready' as const,
    hasZeroDte: false,
    hasEligible0_7: true,
    availableDteList: [1, 3, 6],
    expiries: [{ expiry: '2026-08-06', dte: 1 }],
    unavailableReason: null,
  }];
  return {
    formulaVersion: 'lane-availability/v1',
    marketDateEt: MARKET_DATE,
    maxDte: 7,
    policyVersion: 'INTRA-0-7/v1',
    holdingPolicy: 'same_et_day_only',
    eligibleDteMin: 0,
    eligibleDteMax: 7,
    policyState: 'available',
    policyStateReason: '至少一个标的有 0..7DTE 到期',
    eligibleTickers: ['NVDA'],
    // 故意保留冲突的 legacy 值，锁定新 policy 优先。
    dayType: 'overnight_only',
    dayTypeReason: '旧规则：没有 0DTE',
    basis: 'per_ticker_option_expiry_metadata_within_0_7_dte_v1',
    checkedScope: 'intraday_deep_lane_tickers',
    checkedCount: tickers.length,
    readableCount: tickers.filter((item) => item.state === 'ready').length,
    unavailableCount: tickers.filter((item) => item.state !== 'ready').length,
    zeroDteTickers: [],
    deferredTickers: [],
    limitations: [],
    ...overrides,
    tickers,
  };
}

function top(
  laneAvailability: IntradayLaneAvailability | null = availability(),
  executionPolicy?: IntradayExecutionPolicy,
): IntradayTopResponse {
  return {
    marketDateEt: MARKET_DATE,
    candidates: [],
    laneAvailability,
    executionPolicy,
  } as unknown as IntradayTopResponse;
}

function executionPolicy(asOf: string, overrides: Partial<IntradayExecutionPolicy> = {}): IntradayExecutionPolicy {
  return {
    policyVersion: 'intraday_execution_policy_v1', state: 'ready', asOf,
    expiresAt: new Date(Date.parse(asOf) + 120_000).toISOString(),
    marketDateEt: MARKET_DATE, reason: 'regular', entryStartEt: '09:45',
    ordinaryEntryCutoffEt: '15:15', zeroDteEntryCutoffEt: '14:30',
    exitDeadlineEt: '15:45', closeEt: '16:00',
    ordinaryEntryAllowed: true, zeroDteEntryAllowed: true,
    ...overrides,
  };
}

function check(id: string): HTMLElement | null {
  return document.querySelector(`[data-check-id="${id}"]`);
}

function hardBlock(id: string): HTMLElement | null {
  return document.querySelector(`[data-hard-block="${id}"]`);
}

function setDte(value: string) {
  fireEvent.change(screen.getByLabelText('进场 DTE'), { target: { value } });
}

describe('LaneChecklistPanel · INTRA-0-7', () => {
  it('renders one fixed active lane and removes overnight controls', () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    expect(
      screen.getByLabelText(/手动合约范围：INTRA-0-7，不参与 P0 判定/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '日内' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '过夜' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('打算今天就平掉')).not.toBeInTheDocument();
    expect(document.querySelector('[data-budget-id="overnight_positions"]')).toBeNull();
  });

  it('shows only the current INTRA-0-7 caveat in the header hint', () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    const hint = screen.getByLabelText(/手动合约参考，不参与 underlying P0\/P0-X/);
    expect(hint.getAttribute('aria-label')).toContain('活动策略为 INTRA-0-7/v1');
    expect(hint.getAttribute('aria-label')).toContain('全部必须同一 ET 交易日平仓');
    expect(hint.getAttribute('aria-label')).toContain('旧 4–7DTE 隔夜结论已冻结');
  });

  it('shows 0–7DTE and same-day close as fixed requirements before input', () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    expect(check('dte')?.dataset.checkStatus).toBe('requirement');
    expect(check('dte')).toHaveTextContent('0–7DTE');
    expect(check('holding')?.dataset.checkStatus).toBe('requirement');
    expect(check('holding')).toHaveTextContent('必须在同一 ET 交易日平仓');
  });

  it.each(['0', '1', '3', '7'])('accepts %sDTE', async (dte) => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} nowMs={Date.parse('2026-08-05T13:31:00Z')} />);
    setDte(dte);
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('pass'));
  });

  it('rejects 8DTE', async () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    setDte('8');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('fail'));
    expect(check('dte')?.getAttribute('aria-label')).toContain('超出 0–7DTE');
  });

  it('prefers v2 policy fields over a conflicting legacy overnight_only day type', () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top(availability())} />);
    const line = document.querySelector('[data-day-type]') as HTMLElement;
    expect(line.dataset.dayType).toBe('available');
    expect(line).toHaveTextContent('INTRA-0-7 可用');
    expect(line.getAttribute('aria-label')).toContain('服务端 policy：INTRA-0-7/v1');
    expect(hardBlock('policy_unavailable')).toBeNull();
  });

  it('legacy payload with no 0DTE but a 1–7DTE expiry stays available', () => {
    const legacy = availability({
      policyVersion: undefined,
      holdingPolicy: undefined,
      eligibleDteMin: undefined,
      eligibleDteMax: undefined,
      policyState: undefined,
      policyStateReason: undefined,
      eligibleTickers: undefined,
      dayType: 'overnight_only',
      zeroDteTickers: [],
    });
    render(<LaneChecklistPanel pulse={pulse()} top={top(legacy)} />);
    const line = document.querySelector('[data-day-type]') as HTMLElement;
    expect(line.dataset.dayType).toBe('available');
    expect(line).toHaveTextContent('NVDA 有 0–7DTE');
    expect(line.getAttribute('aria-label')).toContain('旧 day_type=overnight_only');
  });

  it('does not close from legacy overnight_only alone when per-ticker evidence is absent', () => {
    const legacy = availability({
      policyState: undefined,
      eligibleTickers: undefined,
      tickers: [],
      checkedCount: 0,
      readableCount: 0,
      dayType: 'overnight_only',
      zeroDteTickers: [],
    });
    render(<LaneChecklistPanel pulse={pulse()} top={top(legacy)} />);
    const line = document.querySelector('[data-day-type]') as HTMLElement;
    expect(line.dataset.dayType).toBe('unknown');
    expect(line).toHaveTextContent('可用性标缺');
    expect(hardBlock('policy_unavailable')).toBeNull();
  });

  it('closes only when v2 says the whole 0–7DTE policy is unavailable', () => {
    render(
      <LaneChecklistPanel
        pulse={pulse()}
        top={top(availability({
          policyState: 'unavailable',
          policyStateReason: '全部标的均无 0..7DTE 到期',
          eligibleTickers: [],
        }))}
      />,
    );
    const line = document.querySelector('[data-day-type]') as HTMLElement;
    expect(line.dataset.dayType).toBe('unavailable');
    expect(line.querySelector('.text-h3')).toHaveTextContent('今日 0–7DTE 均不可用');
    expect(hardBlock('policy_unavailable')).toBeInTheDocument();
  });

  it('distinguishes cold-start loading from an absent result', () => {
    const { unmount } = render(<LaneChecklistPanel pulse={null} top={null} loading />);
    expect(document.querySelector('[data-day-type]')).toHaveTextContent('读取中');
    unmount();
    render(<LaneChecklistPanel pulse={pulse()} top={top(null)} loading={false} />);
    expect(document.querySelector('[data-day-type]')).toHaveTextContent('标缺');
  });

  it.each([
    ['2026-08-05T13:44:00Z', '1', 'fail', 'outside_entry_window', false, false],
    ['2026-08-05T13:45:00Z', '1', 'pass', null, true, true],
    ['2026-08-05T19:14:00Z', '7', 'pass', null, true, false],
    ['2026-08-05T19:15:00Z', '7', 'fail', 'outside_entry_window', false, false],
    ['2026-08-05T18:29:00Z', '0', 'pass', null, true, true],
    ['2026-08-05T18:30:00Z', '0', 'fail', 'late_0dte', true, false],
  ] as const)('renders the server entry decision at %s for %sDTE', async (
    generatedAt,
    dte,
    status,
    blockId,
    ordinaryEntryAllowed,
    zeroDteEntryAllowed,
  ) => {
    render(
      <LaneChecklistPanel
        pulse={pulse(generatedAt)}
        top={top(availability(), executionPolicy(generatedAt, { ordinaryEntryAllowed, zeroDteEntryAllowed }))}
        nowMs={Date.parse(generatedAt) + 15_000}
      />,
    );
    setDte(dte);
    await waitFor(() => expect(check('clock')?.dataset.checkStatus).toBe(status));
    if (blockId) expect(hardBlock(blockId)).toBeInTheDocument();
  });

  it('does not enforce the removed 11/13 weak-hour or old 1–3/4–7 rules', async () => {
    // 17:30Z = 13:30 ET.
    render(
      <LaneChecklistPanel
        pulse={pulse('2026-08-05T17:30:00Z')}
        top={top(availability(), executionPolicy('2026-08-05T17:30:00Z'))}
        nowMs={Date.parse('2026-08-05T17:31:00Z')}
      />,
    );
    setDte('2');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('pass'));
    expect(check('clock')?.dataset.checkStatus).toBe('pass');
    expect(hardBlock('dte_1_3')).toBeNull();

    setDte('5');
    await waitFor(() => expect(check('dte')?.dataset.checkStatus).toBe('pass'));
    expect(hardBlock('bought_time_unused')).toBeNull();
  });

  it('does not expose the retired V2-D trade-count budget', () => {
    render(<LaneChecklistPanel pulse={pulse()} top={top()} />);
    expect(screen.queryByLabelText('今日额度（手动计数）')).not.toBeInTheDocument();
    expect(document.querySelector('[data-budget-id="intraday_tickets"]')).toBeNull();
    expect(document.querySelector('[data-budget-id="overnight_positions"]')).toBeNull();
    expect(screen.queryByText(/每日最多 6 笔/)).not.toBeInTheDocument();
  });

  it('fails closed without server policy even when the pulse is fresh', () => {
    const stamp = '2026-08-05T14:00:00Z';
    render(<LaneChecklistPanel pulse={pulse(stamp)} top={top()} nowMs={Date.parse(stamp)} />);
    setDte('1');
    expect(check('clock')?.dataset.checkStatus).toBe('missing');
  });

  it.each([
    { state: 'closed', reason: 'non_trading_day', ordinaryEntryAllowed: false, zeroDteEntryAllowed: false },
    { ordinaryEntryCutoffEt: '12:15', zeroDteEntryCutoffEt: '11:30', closeEt: '13:00', ordinaryEntryAllowed: false, zeroDteEntryAllowed: false },
  ] as Partial<IntradayExecutionPolicy>[])('does not override a closed server calendar/window with a midday pulse: %j', (overrides) => {
    const stamp = '2026-11-27T17:50:00Z';
    render(<LaneChecklistPanel pulse={pulse(stamp)} top={top(availability(), executionPolicy(stamp, overrides))} nowMs={Date.parse(stamp)} />);
    setDte('1');
    expect(check('clock')?.dataset.checkStatus).toBe('fail');
  });

  it('expires permission at the exact server deadline before the normal stale interval', () => {
    const stamp = '2026-08-05T19:14:30Z';
    const expiresAt = '2026-08-05T19:15:00Z';
    const snapshot = top(availability(), executionPolicy(stamp, { expiresAt }));
    const { rerender } = render(<LaneChecklistPanel pulse={pulse(stamp)} top={snapshot} nowMs={Date.parse(expiresAt) - 1} />);
    setDte('1');
    expect(check('clock')?.dataset.checkStatus).toBe('pass');
    rerender(<LaneChecklistPanel pulse={pulse(stamp)} top={snapshot} nowMs={Date.parse(expiresAt)} />);
    expect(check('clock')?.dataset.checkStatus).toBe('missing');
  });

  it.each([
    { state: 'unavailable', reason: 'calendar_missing', ordinaryEntryAllowed: null },
    { asOf: '2026-08-06T14:00:00Z' },
    { expiresAt: null },
  ] as Partial<IntradayExecutionPolicy>[])('rejects unknown or invalid policy %j', (overrides) => {
    const stamp = '2026-08-05T14:00:00Z';
    render(<LaneChecklistPanel pulse={pulse(stamp)} top={top(availability(), executionPolicy(stamp, overrides))} nowMs={Date.parse(stamp)} />);
    setDte('1');
    expect(check('clock')?.dataset.checkStatus).toBe('missing');
  });
});
