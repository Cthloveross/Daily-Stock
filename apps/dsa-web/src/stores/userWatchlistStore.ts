import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface UserWatchlistState {
  tickers: string[];
  add: (t: string) => boolean;
  addMany: (tickers: string[]) => number;
  remove: (t: string) => void;
  clear: () => void;
}

function normalize(raw: string): string {
  return raw.trim().toUpperCase().replace(/^\$/, '');
}

export const useUserWatchlistStore = create<UserWatchlistState>()(
  persist(
    (set, get) => ({
      tickers: [],
      add: (raw) => {
        const t = normalize(raw);
        if (!t) return false;
        if (get().tickers.includes(t)) return false;
        set({ tickers: [...get().tickers, t] });
        return true;
      },
      addMany: (rawTickers) => {
        const existing = new Set(get().tickers);
        const next = [...get().tickers];
        let added = 0;
        for (const raw of rawTickers) {
          const ticker = normalize(raw);
          if (!ticker || existing.has(ticker)) continue;
          existing.add(ticker);
          next.push(ticker);
          added += 1;
        }
        if (added > 0) set({ tickers: next });
        return added;
      },
      remove: (raw) => {
        const t = normalize(raw);
        set({ tickers: get().tickers.filter((x) => x !== t) });
      },
      clear: () => set({ tickers: [] }),
    }),
    { name: 'dsa-user-watchlist', version: 1 },
  ),
);
