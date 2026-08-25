import { beforeEach, describe, expect, it, vi } from 'vitest';
import { findNextReviewEpisode } from '../nextReviewEpisode';
import { fetchPositionEpisodes } from '../../../../api/journal';
import type {
  PositionEpisodeItem,
  PositionEpisodeListResponse,
} from '../../../../types/journal';

vi.mock('../../../../api/journal', () => ({
  fetchPositionEpisodes: vi.fn(),
}));

const fetchEpisodesMock = vi.mocked(fetchPositionEpisodes);

function episode(id: number): PositionEpisodeItem {
  return { id } as PositionEpisodeItem;
}

function listWith(items: PositionEpisodeItem[]): PositionEpisodeListResponse {
  return {
    dataState: 'ready',
    total: items.length,
    page: 1,
    perPage: 5,
    items,
  };
}

describe('findNextReviewEpisode', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('resumes the in-progress episode first without consulting later tiers', async () => {
    fetchEpisodesMock.mockResolvedValueOnce(listWith([episode(11), episode(12)]));

    const target = await findNextReviewEpisode({ buildId: 9 });

    expect(target?.id).toBe(11);
    expect(fetchEpisodesMock).toHaveBeenCalledTimes(1);
    expect(fetchEpisodesMock).toHaveBeenCalledWith({
      buildId: 9,
      page: 1,
      perPage: 5,
      reviewStatus: 'in_progress',
    });
  });

  it('excludes the current episode inside a tier instead of returning it again', async () => {
    fetchEpisodesMock.mockResolvedValueOnce(listWith([episode(41), episode(42)]));

    const target = await findNextReviewEpisode({ excludeEpisodeId: 41 });

    expect(target?.id).toBe(42);
    expect(fetchEpisodesMock).toHaveBeenCalledTimes(1);
  });

  it('falls through to the top-loss tier when in-progress only contains the current episode', async () => {
    fetchEpisodesMock
      .mockResolvedValueOnce(listWith([episode(41)]))
      .mockResolvedValueOnce(listWith([episode(77)]));

    const target = await findNextReviewEpisode({ buildId: 9, excludeEpisodeId: 41 });

    expect(target?.id).toBe(77);
    expect(fetchEpisodesMock).toHaveBeenNthCalledWith(2, {
      buildId: 9,
      page: 1,
      perPage: 5,
      reviewStatus: 'not_started',
      caseFocus: 'top_loss',
    });
  });

  it('falls back to the most recent not-started episode when top_loss has no hit', async () => {
    fetchEpisodesMock
      .mockResolvedValueOnce(listWith([]))
      .mockResolvedValueOnce(listWith([]))
      .mockResolvedValueOnce(listWith([episode(88)]));

    const target = await findNextReviewEpisode();

    expect(target?.id).toBe(88);
    expect(fetchEpisodesMock).toHaveBeenNthCalledWith(3, {
      buildId: undefined,
      page: 1,
      perPage: 5,
      reviewStatus: 'not_started',
    });
  });

  it('returns null after all three tiers are exhausted', async () => {
    fetchEpisodesMock.mockResolvedValue(listWith([episode(41)]));

    const target = await findNextReviewEpisode({ excludeEpisodeId: 41 });

    expect(target).toBeNull();
    expect(fetchEpisodesMock).toHaveBeenCalledTimes(3);
  });
});
