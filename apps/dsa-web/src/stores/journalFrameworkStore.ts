import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ChatMessage } from '../types/journal';

const MAX_FRAMEWORK_CHARS = 10_000;
const MAX_HISTORY = 40; // cap persisted chat messages

interface JournalFrameworkState {
  framework: string;
  chatHistory: ChatMessage[];
  setFramework: (v: string) => string | null; // returns validation error or null
  appendMessage: (msg: ChatMessage) => void;
  clearChat: () => void;
}

export const useJournalFrameworkStore = create<JournalFrameworkState>()(
  persist(
    (set, get) => ({
      framework: '',
      chatHistory: [],
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
    { name: 'dsa-journal-framework', version: 1 },
  ),
);
