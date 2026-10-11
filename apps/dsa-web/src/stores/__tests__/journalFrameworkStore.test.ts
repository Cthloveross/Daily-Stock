import { beforeEach, describe, expect, it } from 'vitest';
import {
  JOURNAL_FRAMEWORK_STORAGE_KEY,
  JOURNAL_FRAMEWORK_STORAGE_VERSION,
  useJournalFrameworkStore,
} from '../journalFrameworkStore';

describe('journalFrameworkStore persistence migration', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useJournalFrameworkStore.setState({ framework: '', chatHistory: [], legacyArchive: null });
  });

  it('archives v1 framework and chat without feeding retired rules to AI', async () => {
    window.localStorage.setItem(JOURNAL_FRAMEWORK_STORAGE_KEY, JSON.stringify({
      version: 1,
      state: {
        framework: '1-3DTE 可持有 1 日；每天最多两笔 0DTE',
        chatHistory: [{ role: 'user', content: '按旧框架复盘' }],
      },
    }));

    await useJournalFrameworkStore.persist.rehydrate();

    expect(useJournalFrameworkStore.getState().framework).toBe('');
    expect(useJournalFrameworkStore.getState().chatHistory).toEqual([]);
    const archive = {
      framework: '1-3DTE 可持有 1 日；每天最多两笔 0DTE',
      chatHistory: [{ role: 'user', content: '按旧框架复盘' }],
    };
    expect(useJournalFrameworkStore.getState().legacyArchive).toEqual(archive);
    const stored = JSON.parse(
      window.localStorage.getItem(JOURNAL_FRAMEWORK_STORAGE_KEY) ?? '{}',
    ) as { version?: number; state?: { framework?: string; chatHistory?: unknown[]; legacyArchive?: unknown } };
    expect(stored.version).toBe(JOURNAL_FRAMEWORK_STORAGE_VERSION);
    expect(stored.state?.framework).toBe('');
    expect(stored.state?.chatHistory).toEqual([]);
    expect(stored.state?.legacyArchive).toEqual(archive);
  });

  it('preserves data already written by the current storage version', async () => {
    window.localStorage.setItem(JOURNAL_FRAMEWORK_STORAGE_KEY, JSON.stringify({
      version: JOURNAL_FRAMEWORK_STORAGE_VERSION,
      state: { framework: 'INTRA-0-7 · 同一 ET 日平仓', chatHistory: [] },
    }));

    await useJournalFrameworkStore.persist.rehydrate();

    expect(useJournalFrameworkStore.getState().framework).toBe(
      'INTRA-0-7 · 同一 ET 日平仓',
    );
  });
});
