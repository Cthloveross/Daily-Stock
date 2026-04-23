import { create } from 'zustand';
import {
  fetchBreakoutSignals,
  fetchRegimeHistory,
  fetchRegimeToday,
  recomputeRegime,
} from '../api/regime';
import type {
  BreakoutSignalsResponse,
  RegimeHistoryResponse,
  RegimeScoreItem,
} from '../types/regime';

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
    set({ todayLoading: true, error: null });
    try {
      const t = await fetchRegimeToday();
      set({ today: t, todayLoading: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ todayLoading: false, error: msg });
    }
  },

  async loadHistory(days = 30) {
    set({ historyLoading: true, error: null });
    try {
      const h = await fetchRegimeHistory(days);
      set({ history: h, historyLoading: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ historyLoading: false, error: msg });
    }
  },

  async loadSignals(limit = 20, onlyFake?: boolean) {
    set({ signalsLoading: true, error: null });
    try {
      const s = await fetchBreakoutSignals(limit, onlyFake);
      set({ signals: s, signalsLoading: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ signalsLoading: false, error: msg });
    }
  },

  async recompute() {
    try {
      const t = await recomputeRegime();
      set({ today: t });
      return t;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ error: msg });
      return null;
    }
  },
}));
