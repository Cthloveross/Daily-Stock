import { create } from 'zustand';
import {
  fetchJournalStats,
  fetchRealityTest,
  fetchTrades,
  importJournalCsv,
  updateTrade,
} from '../api/journal';
import type {
  ImportResponse,
  JournalStatsResponse,
  RealityTestResponse,
  TradeItem,
  TradeListFilters,
  TradeListResponse,
  TradeUpdateRequest,
} from '../types/journal';

interface JournalState {
  realityTest: RealityTestResponse | null;
  stats: JournalStatsResponse | null;
  trades: TradeListResponse | null;
  tradesLoading: boolean;
  realityLoading: boolean;
  statsLoading: boolean;
  importing: boolean;
  error: string | null;

  loadRealityTest: (topN?: number) => Promise<void>;
  loadStats: (days?: number) => Promise<void>;
  loadTrades: (filters?: TradeListFilters) => Promise<void>;
  patchTrade: (tradeId: number, payload: TradeUpdateRequest) => Promise<TradeItem>;
  importCsv: (file: File, broker?: string) => Promise<ImportResponse>;
  reset: () => void;
}

export const useJournalStore = create<JournalState>((set, get) => ({
  realityTest: null,
  stats: null,
  trades: null,
  tradesLoading: false,
  realityLoading: false,
  statsLoading: false,
  importing: false,
  error: null,

  async loadRealityTest(topN = 5) {
    set({ realityLoading: true, error: null });
    try {
      const r = await fetchRealityTest(topN);
      set({ realityTest: r, realityLoading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ realityLoading: false, error: message });
    }
  },

  async loadStats(days = 90) {
    set({ statsLoading: true, error: null });
    try {
      const s = await fetchJournalStats(days);
      set({ stats: s, statsLoading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ statsLoading: false, error: message });
    }
  },

  async loadTrades(filters: TradeListFilters = {}) {
    set({ tradesLoading: true, error: null });
    try {
      const t = await fetchTrades(filters);
      set({ trades: t, tradesLoading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ tradesLoading: false, error: message });
    }
  },

  async patchTrade(tradeId, payload) {
    const updated = await updateTrade(tradeId, payload);
    const current = get().trades;
    if (current) {
      set({
        trades: {
          ...current,
          items: current.items.map((i) => (i.id === tradeId ? updated : i)),
        },
      });
    }
    return updated;
  },

  async importCsv(file, broker = 'moomoo_us') {
    set({ importing: true, error: null });
    try {
      const resp = await importJournalCsv(file, broker);
      set({ importing: false });
      return resp;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ importing: false, error: message });
      throw err;
    }
  },

  reset() {
    set({
      realityTest: null,
      stats: null,
      trades: null,
      tradesLoading: false,
      realityLoading: false,
      statsLoading: false,
      importing: false,
      error: null,
    });
  },
}));
