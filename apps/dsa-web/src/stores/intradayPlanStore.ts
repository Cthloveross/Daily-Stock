import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * 盘中计划按 ET 市场日作用域保存用户从实时扫描提升的标的清单。
 *
 * 状态持久化在 localStorage，并携带 `marketDate`（America/New_York 日期）：
 * 换一个交易日读到的就是空清单，不需要用户记得清理。旧 V2-D 手动交易计数
 * 已随 INTRA-0-7/v1 退役；本 store 不承载交易次数限制。
 *
 * ⚠️ 与 `useUserWatchlistStore` 的关键区别：那个 store 一旦非空，日内页面就会
 * 向 `/opportunities/intraday-top` 显式传 `symbols`，从而**关掉服务端两层扫描**
 * （服务端只在「空 symbols + 已配置 INTRADAY_WATCHLIST」时走两层）。本 store
 * 的标的走的是 additive 的 `focus_symbols` 字段，不进 `symbols`，因此两层扫描
 * 保持不变——见 `fetchIntradayTop` 与 `IntradayPage` 的用法。
 */

/** 盘中计划最多提升的标的数：与服务端 `focus_symbols` 上界（≤8）一致。 */
export const INTRADAY_PLAN_MAX_TICKERS = 8;

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
