import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearEpisodeReviewDraft,
  compactEpisodeReviewUserContext,
  emptyEpisodeReviewDraft,
  EPISODE_REVIEW_TOTAL_MAX_CHARS,
  episodeReviewDraftCharacterCount,
  episodeReviewDraftStorageKey,
  episodeReviewTextCharacterCount,
  limitEpisodeReviewText,
  loadEpisodeReviewDraft,
  saveEpisodeReviewDraft,
} from '../episodeReviewDraft';

describe('position episode review local draft', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('scopes persisted text to both the immutable build and episode', () => {
    const draft = {
      ...emptyEpisodeReviewDraft(),
      setupThesis: '趋势延续',
      exitReason: '跌破失效点',
    };

    expect(saveEpisodeReviewDraft(9, 41, draft)).toBe(true);
    expect(episodeReviewDraftStorageKey(9, 41)).toContain('build:9:episode:41');
    expect(loadEpisodeReviewDraft(9, 41)).toEqual(draft);
    expect(loadEpisodeReviewDraft(10, 41)).toEqual(emptyEpisodeReviewDraft());
    expect(loadEpisodeReviewDraft(9, 42)).toEqual(emptyEpisodeReviewDraft());
  });

  it('trims and compacts user context without mixing pre-trade and post-trade fields', () => {
    const draft = {
      ...emptyEpisodeReviewDraft(),
      setupThesis: '  回踩后延续  ',
      invalidationPlan: '   ',
      postTradeReflection: '加仓缺少新触发',
    };
    expect(compactEpisodeReviewUserContext(draft)).toEqual({
      setupThesis: '回踩后延续',
      postTradeReflection: '加仓缺少新触发',
    });
    expect(episodeReviewDraftCharacterCount(draft)).toBe(12);
    expect(episodeReviewTextCharacterCount('  🚀  ')).toBe(1);
    expect(limitEpisodeReviewText('🚀🚀🚀', 2)).toBe('🚀🚀');
  });

  it('never restores more than the backend aggregate character budget', () => {
    saveEpisodeReviewDraft(9, 44, {
      ...emptyEpisodeReviewDraft(),
      setupThesis: '甲'.repeat(2000),
      entryTrigger: '乙'.repeat(2000),
      invalidationPlan: '丙'.repeat(2000),
      positionRationale: '丁'.repeat(2000),
    });

    const restored = loadEpisodeReviewDraft(9, 44);
    expect(episodeReviewDraftCharacterCount(restored)).toBe(EPISODE_REVIEW_TOTAL_MAX_CHARS);
    expect(restored.positionRationale).toBe('');
  });

  it('clears only the selected episode draft and rejects malformed storage', () => {
    const draft = { ...emptyEpisodeReviewDraft(), entryTrigger: '收回前高' };
    saveEpisodeReviewDraft(9, 41, draft);
    saveEpisodeReviewDraft(9, 42, draft);

    expect(clearEpisodeReviewDraft(9, 41)).toBe(true);
    expect(loadEpisodeReviewDraft(9, 41)).toEqual(emptyEpisodeReviewDraft());
    expect(loadEpisodeReviewDraft(9, 42)).toEqual(draft);

    window.localStorage.setItem(episodeReviewDraftStorageKey(9, 43), '{not-json');
    expect(loadEpisodeReviewDraft(9, 43)).toEqual(emptyEpisodeReviewDraft());
    expect(window.localStorage.getItem(episodeReviewDraftStorageKey(9, 43))).toBeNull();
  });
});
