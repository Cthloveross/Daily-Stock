import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  INTRADAY_PLAN_MAX_TICKERS,
  etMarketDate,
  selectManualIntradayTickets,
  selectManualOvernightPositions,
  selectPlanTickers,
  useIntradayManualBudgetStore,
  useIntradayPlanStore,
} from '../intradayPlanStore';

describe('intradayPlanStore', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useIntradayPlanStore.getState().clear();
    useIntradayManualBudgetStore.getState().reset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('normalizes and dedupes promoted tickers', () => {
    const { promote } = useIntradayPlanStore.getState();
    expect(promote(' nvda ')).toBe(true);
    expect(promote('$TSLA')).toBe(true);
    expect(promote('NVDA')).toBe(false);
    expect(selectPlanTickers(useIntradayPlanStore.getState())).toEqual(['NVDA', 'TSLA']);
  });

  it('bounds the list at the focus_symbols server limit', () => {
    const { promote } = useIntradayPlanStore.getState();
    for (let i = 0; i < INTRADAY_PLAN_MAX_TICKERS; i += 1) {
      expect(promote(`SYM${i}`)).toBe(true);
    }
    expect(promote('OVERFLOW')).toBe(false);
    expect(selectPlanTickers(useIntradayPlanStore.getState())).toHaveLength(
      INTRADAY_PLAN_MAX_TICKERS,
    );
  });

  it('demotes and clears', () => {
    const { promote, demote, clear } = useIntradayPlanStore.getState();
    promote('NVDA');
    promote('MU');
    demote('nvda');
    expect(selectPlanTickers(useIntradayPlanStore.getState())).toEqual(['MU']);
    clear();
    expect(selectPlanTickers(useIntradayPlanStore.getState())).toEqual([]);
  });

  it('etMarketDate reports the America/New_York calendar day', () => {
    // 2026-08-06 01:30Z 在纽约仍是 08-05（EDT = UTC−4）。
    expect(etMarketDate(new Date('2026-08-06T01:30:00Z'))).toBe('2026-08-05');
    expect(etMarketDate(new Date('2026-08-06T14:30:00Z'))).toBe('2026-08-06');
  });

  it('auto-clears the plan on the next ET trading day without touching storage', () => {
    useIntradayPlanStore.getState().promote('NVDA');
    // 模拟「昨天写下的清单」：状态里的市场日不是今天。
    useIntradayPlanStore.setState({ marketDate: '1999-01-04' });
    expect(selectPlanTickers(useIntradayPlanStore.getState())).toEqual([]);
    // 稳定空引用：连续两次读取必须是同一个数组（否则 React 会无限重渲染）。
    expect(selectPlanTickers(useIntradayPlanStore.getState()))
      .toBe(selectPlanTickers(useIntradayPlanStore.getState()));
    // 换日后第一次提升会把清单重置到今天，不会把旧标的带进来。
    useIntradayPlanStore.getState().promote('MU');
    expect(selectPlanTickers(useIntradayPlanStore.getState())).toEqual(['MU']);
    expect(useIntradayPlanStore.getState().marketDate).toBe(etMarketDate());
  });

  it('manual budget counters increment, floor at zero, and are day-scoped', () => {
    const { bump } = useIntradayManualBudgetStore.getState();
    bump('intraday_tickets', 1);
    bump('intraday_tickets', 1);
    bump('overnight_positions', 1);
    expect(selectManualIntradayTickets(useIntradayManualBudgetStore.getState())).toBe(2);
    expect(selectManualOvernightPositions(useIntradayManualBudgetStore.getState())).toBe(1);

    bump('intraday_tickets', -1);
    expect(selectManualIntradayTickets(useIntradayManualBudgetStore.getState())).toBe(1);
    bump('overnight_positions', -5);
    expect(selectManualOvernightPositions(useIntradayManualBudgetStore.getState())).toBe(0);

    // 超过 V2-D 上限是事实，如实计数（面板转警示样式，不夹回上限）。
    for (let i = 0; i < 8; i += 1) bump('intraday_tickets', 1);
    expect(selectManualIntradayTickets(useIntradayManualBudgetStore.getState())).toBe(9);

    useIntradayManualBudgetStore.setState({ marketDate: '1999-01-04' });
    expect(selectManualIntradayTickets(useIntradayManualBudgetStore.getState())).toBe(0);
    expect(selectManualOvernightPositions(useIntradayManualBudgetStore.getState())).toBe(0);
    // 换日后第一次 +1 从 0 起算，不接着昨天的数。
    useIntradayManualBudgetStore.getState().bump('intraday_tickets', 1);
    expect(selectManualIntradayTickets(useIntradayManualBudgetStore.getState())).toBe(1);
  });
});
