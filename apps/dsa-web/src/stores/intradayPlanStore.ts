import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * 盘中计划的两个**按 ET 市场日作用域**的本地状态：
 *
 * 1. `useIntradayPlanStore`：用户从实时扫描手动提升上来的标的清单；
 * 2. `useIntradayManualBudgetStore`：V2-D 两个额度的**手动计数**。
 *
 * 两者都持久化在 localStorage，但都带 `marketDate`（America/New_York 日期）：
 * 换一个交易日读到的就是空清单 / 归零计数，**不需要用户记得清理**。清空动作
 * 仍然显式提供（当日想重来）。
 *
 * 为什么额度改成手动计数：Journal 里只有导入的历史成交（截至 2026-07-20），
 * 它**永远不可能**反映今天开了几笔，于是那两行恒为「标缺」——一个永远标缺的
 * 读数等于没有。改成用户自己 +1/−1 的当日计数：数值是他自己的，来源如实标注
 * 「手动维护」，不冒充系统读数。
 *
 * ⚠️ 与 `useUserWatchlistStore` 的关键区别：那个 store 一旦非空，日内页面就会
 * 向 `/opportunities/intraday-top` 显式传 `symbols`，从而**关掉服务端两层扫描**
 * （服务端只在「空 symbols + 已配置 INTRADAY_WATCHLIST」时走两层）。本 store
 * 的标的走的是 additive 的 `focus_symbols` 字段，不进 `symbols`，因此两层扫描
 * 保持不变——见 `fetchIntradayTop` 与 `IntradayPage` 的用法。
 */

/** 盘中计划最多提升的标的数：与服务端 `focus_symbols` 上界（≤8）一致。 */
export const INTRADAY_PLAN_MAX_TICKERS = 8;

/** V2-D③ 的两个上限（用户自己的规则，不是系统阈值）。 */
export const INTRADAY_MANUAL_INTRADAY_TICKET_LIMIT = 6;
export const INTRADAY_MANUAL_OVERNIGHT_LIMIT = 3;

/** ET 市场日（America/New_York 的日历日，YYYY-MM-DD）。 */
export function etMarketDate(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
}

function normalize(raw: string): string {
  return raw.trim().toUpperCase().replace(/^\$/, '');
}

/** 稳定空引用：选择器返回新数组会让 useSyncExternalStore 每次快照都不相等。 */
const EMPTY_TICKERS: string[] = [];

interface IntradayPlanState {
  /** 该清单所属的 ET 市场日；与今天不一致时读到的一律是空清单。 */
  marketDate: string;
  tickers: string[];
  promote: (ticker: string) => boolean;
  demote: (ticker: string) => void;
  clear: () => void;
}

export const useIntradayPlanStore = create<IntradayPlanState>()(
  persist(
    (set, get) => ({
      marketDate: etMarketDate(),
      tickers: [],
      promote: (raw) => {
        const ticker = normalize(raw);
        if (!ticker) return false;
        const today = etMarketDate();
        const current = get().marketDate === today ? get().tickers : [];
        if (current.includes(ticker)) return false;
        if (current.length >= INTRADAY_PLAN_MAX_TICKERS) return false;
        set({ marketDate: today, tickers: [...current, ticker] });
        return true;
      },
      demote: (raw) => {
        const ticker = normalize(raw);
        const today = etMarketDate();
        const current = get().marketDate === today ? get().tickers : [];
        set({ marketDate: today, tickers: current.filter((item) => item !== ticker) });
      },
      clear: () => set({ marketDate: etMarketDate(), tickers: [] }),
    }),
    { name: 'dsa-intraday-plan', version: 1 },
  ),
);

/**
 * 当日有效的盘中计划清单：跨交易日自动为空（不写回 storage，读侧判定即可，
 * 避免为了「清理」而在渲染期写状态）。
 */
export function selectPlanTickers(state: IntradayPlanState): string[] {
  return state.marketDate === etMarketDate() ? state.tickers : EMPTY_TICKERS;
}

interface IntradayManualBudgetState {
  marketDate: string;
  intradayTickets: number;
  overnightPositions: number;
  bump: (id: 'intraday_tickets' | 'overnight_positions', delta: number) => void;
  reset: () => void;
}

export const useIntradayManualBudgetStore = create<IntradayManualBudgetState>()(
  persist(
    (set, get) => ({
      marketDate: etMarketDate(),
      intradayTickets: 0,
      overnightPositions: 0,
      bump: (id, delta) => {
        const today = etMarketDate();
        const fresh = get().marketDate !== today;
        const currentIntraday = fresh ? 0 : get().intradayTickets;
        const currentOvernight = fresh ? 0 : get().overnightPositions;
        // 计数不设上限：超过 V2-D 的额度是**事实**，面板照实显示并转警示样式，
        // 绝不把它夹回上限装作没超。下界 0（负数没有意义）。
        set({
          marketDate: today,
          intradayTickets:
            id === 'intraday_tickets'
              ? Math.max(0, currentIntraday + delta)
              : currentIntraday,
          overnightPositions:
            id === 'overnight_positions'
              ? Math.max(0, currentOvernight + delta)
              : currentOvernight,
        });
      },
      reset: () => set({
        marketDate: etMarketDate(),
        intradayTickets: 0,
        overnightPositions: 0,
      }),
    }),
    { name: 'dsa-intraday-manual-budget', version: 1 },
  ),
);

/**
 * 逐字段选择器（zustand v5 + React 19：选择器不得返回新对象，否则
 * `useSyncExternalStore` 每次快照都不相等）。跨交易日一律读到 0。
 */
export function selectManualIntradayTickets(state: IntradayManualBudgetState): number {
  return state.marketDate === etMarketDate() ? state.intradayTickets : 0;
}

export function selectManualOvernightPositions(
  state: IntradayManualBudgetState,
): number {
  return state.marketDate === etMarketDate() ? state.overnightPositions : 0;
}
