import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useJournalFrameworkStore } from '../../../stores/journalFrameworkStore';
import FrameworkPanel from '../FrameworkPanel';

describe('FrameworkPanel · INTRA-0-7 example', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useJournalFrameworkStore.setState({ framework: '', chatHistory: [], legacyArchive: null });
  });

  it('uses a same-ET-day 0–7DTE example without retired limits', () => {
    render(<FrameworkPanel />);

    fireEvent.click(screen.getByRole('button', { name: 'Use example' }));
    const value = (screen.getByRole('textbox') as HTMLTextAreaElement).value;
    expect(value).toContain('只做 INTRA-0-7');
    expect(value).toContain('0–7DTE');
    expect(value).toContain('同一 ET 交易日平仓');
    expect(value).not.toContain('每天 0DTE ≤ 2 笔');
    expect(value).not.toContain('1-3DTE ≤ 1 日');
    expect(value).not.toContain('09:45-10:30');
  });

  it('keeps migrated user text readable without activating it', () => {
    useJournalFrameworkStore.setState({ legacyArchive: {
      framework: '旧自定义框架', chatHistory: [{ role: 'user', content: '旧复盘记录', ts: '2026-08-01T00:00:00Z' }],
    } });
    render(<FrameworkPanel />);
    expect(screen.getByText('旧自定义框架')).toBeInTheDocument();
    expect(screen.getByText('user: 旧复盘记录')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(useJournalFrameworkStore.getState().framework).toBe('');
    expect(useJournalFrameworkStore.getState().chatHistory).toEqual([]);
  });
});
