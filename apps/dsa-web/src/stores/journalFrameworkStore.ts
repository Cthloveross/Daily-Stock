import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ChatMessage } from '../types/journal';

const MAX_FRAMEWORK_CHARS = 10_000;
const MAX_HISTORY = 40; // cap persisted chat messages
export const JOURNAL_FRAMEWORK_STORAGE_KEY = 'dsa-journal-framework';
export const JOURNAL_FRAMEWORK_STORAGE_VERSION = 2;

interface LegacyFrameworkArchive {
  framework: string;
  chatHistory: ChatMessage[];
}

interface JournalFrameworkState {
  framework: string;
  chatHistory: ChatMessage[];
  legacyArchive: LegacyFrameworkArchive | null;
  setFramework: (v: string) => string | null; // returns validation error or null
  appendMessage: (msg: ChatMessage) => void;
  clearChat: () => void;
}

export const useJournalFrameworkStore = create<JournalFrameworkState>()(
  persist(
    (set, get) => ({
      framework: '',
      chatHistory: [],
      legacyArchive: null,
      setFramework: (v) => {
        const trimmed = (v ?? '').slice(0, MAX_FRAMEWORK_CHARS);
        set({ framework: trimmed });
        return v && v.length > MAX_FRAMEWORK_CHARS
          ? `框架文本被截断到 ${MAX_FRAMEWORK_CHARS} 字`
          : null;
      },
      appendMessage: (msg) => {
        const next = [...get().chatHistory, msg];
        if (next.length > MAX_HISTORY) next.splice(0, next.length - MAX_HISTORY);
        set({ chatHistory: next });
      },
      clearChat: () => set({ chatHistory: [] }),
    }),
    {
      name: JOURNAL_FRAMEWORK_STORAGE_KEY,
      version: JOURNAL_FRAMEWORK_STORAGE_VERSION,
      migrate: (persistedState, version) => {
        // v0/v1 may contain the retired overnight/DTE/time-budget framework.
        // Stop using the retired premise, but preserve user text/history in the
        // same persisted object. The archive is never sent to Journal AI.
        if (version < JOURNAL_FRAMEWORK_STORAGE_VERSION) {
          const old = persistedState as Partial<JournalFrameworkState> | null;
          return {
            framework: '',
            chatHistory: [],
            legacyArchive: old?.legacyArchive ?? {
              framework: typeof old?.framework === 'string' ? old.framework : '',
              chatHistory: Array.isArray(old?.chatHistory) ? old.chatHistory : [],
            },
          } as unknown as JournalFrameworkState;
        }
        return persistedState as JournalFrameworkState;
      },
    },
  ),
);
