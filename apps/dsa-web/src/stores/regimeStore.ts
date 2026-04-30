import { create } from 'zustand';
import {
  fetchBreakoutSignals,
  fetchRegimeHistory,
  fetchRegimeToday,
  recomputeRegime,
} from '../api/regime';
import { sessionCache } from '../utils/sessionCache';
import type {
  BreakoutSignalsResponse,
  RegimeHistoryResponse,
  RegimeScoreItem,
} from '../types/regime';

const CACHE_KEY_TODAY = 'regime:today';
const CACHE_KEY_HISTORY = (days: number) => `regime:history:${days}`;
const CACHE_KEY_SIGNALS = (limit: number, onlyFake?: boolean) =>
  `regime:signals:${limit}:${onlyFake ? 'fake' : 'all'}`;

interface RegimeState {
  today: RegimeScoreItem | null;
  history: RegimeHistoryResponse | null;
  signals: BreakoutSignalsResponse | null;
  todayLoading: boolean;
  historyLoading: boolean;
  signalsLoading: boolean;
  error: string | null;

  loadToday: () => Promise<void>;
  loadHistory: (days?: number) => Promise<void>;
  loadSignals: (limit?: number, onlyFake?: boolean) => Promise<void>;
  recompute: () => Promise<RegimeScoreItem | null>;
}

export const useRegimeStore = create<RegimeState>((set) => ({
  today: null,
  history: null,
  signals: null,
  todayLoading: false,
  historyLoading: false,
  signalsLoading: false,
  error: null,

  async loadToday() {
    // Serve from sessionCache if we hit within the 10-min TTL — this is the
    // big "don't re-fetch on every page open" win.
    const cached = sessionCache.get<RegimeScoreItem>(CACHE_KEY_TODAY);
    if (cached) {
      set({ today: cached });
      return;
    }
    set({ todayLoading: true, error: null });
    try {
      const t = await fetchRegimeToday();
      sessionCache.set(CACHE_KEY_TODAY, t);
      set({ today: t, todayLoading: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ todayLoading: false, error: msg });
    }
  },

  async loadHistory(days = 30) {
    const cached = sessionCache.get<RegimeHistoryResponse>(CACHE_KEY_HISTORY(days));
    if (cached) {
      set({ history: cached });
      return;
    }
    set({ historyLoading: true, error: null });
    try {
      const h = await fetchRegimeHistory(days);
      sessionCache.set(CACHE_KEY_HISTORY(days), h);
      set({ history: h, historyLoading: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ historyLoading: false, error: msg });
    }
  },

  async loadSignals(limit = 20, onlyFake?: boolean) {
    const cached = sessionCache.get<BreakoutSignalsResponse>(CACHE_KEY_SIGNALS(limit, onlyFake));
    if (cached) {
      set({ signals: cached });
      return;
    }
    set({ signalsLoading: true, error: null });
    try {
      const s = await fetchBreakoutSignals(limit, onlyFake);
      sessionCache.set(CACHE_KEY_SIGNALS(limit, onlyFake), s);
      set({ signals: s, signalsLoading: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ signalsLoading: false, error: msg });
    }
  },

  async recompute() {
    try {
      const t = await recomputeRegime();
      // Fresh compute invalidates any cached snapshot
      sessionCache.invalidate(CACHE_KEY_TODAY);
      sessionCache.set(CACHE_KEY_TODAY, t);
      set({ today: t });
      return t;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ error: msg });
      return null;
    }
  },
}));
