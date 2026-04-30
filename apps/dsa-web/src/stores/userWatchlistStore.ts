import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface UserWatchlistState {
  tickers: string[];
  add: (t: string) => boolean;
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
      remove: (raw) => {
        const t = normalize(raw);
        set({ tickers: get().tickers.filter((x) => x !== t) });
      },
      clear: () => set({ tickers: [] }),
    }),
    { name: 'dsa-user-watchlist', version: 1 },
  ),
);
