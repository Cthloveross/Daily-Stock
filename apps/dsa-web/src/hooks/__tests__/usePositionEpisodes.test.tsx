import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { usePositionEpisodes } from '../usePositionEpisodes';

const apiMocks = vi.hoisted(() => ({
  fetchPositionEpisodes: vi.fn(),
  fetchPositionEpisodeDetail: vi.fn(),
  createPositionEpisodeBuild: vi.fn(),
  fetchCanonicalEpisodeBuildPreview: vi.fn(),
  createCanonicalPositionEpisodeBuild: vi.fn(),
}));

vi.mock('../../api/journal', () => apiMocks);

const emptyList = {
  dataState: 'ready',
  total: 0,
  page: 1,
  perPage: 50,
  items: [],
};

const canonicalPreview = {
  dataState: 'ready',
  canonicalSetId: 1,
  canonicalSetSha256: 'a'.repeat(64),
  buildKey: 'b'.repeat(64),
  sourceBatchIds: [2, 3],
  sourceEventCount: 12,
  aggregateOrderEventCount: 2,
  detailedFillEventCount: 10,
  plannedPositionEpisodeCount: 4,
  plannedOpenEpisodeCount: 1,
  plannedClosedEpisodeCount: 3,
  feeConserved: true,
  requiresAssumedFlatAcceptance: true,
  defaultPositionEpisodeCount: 3,
  episodeCountDelta: 1,
  defaultWillChange: false,
  confirmAllowed: true,
  warnings: [],
};

describe('usePositionEpisodes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchPositionEpisodes.mockResolvedValue(emptyList);
    apiMocks.fetchPositionEpisodeDetail.mockResolvedValue({ dataState: 'ready', evidence: [] });
    apiMocks.fetchCanonicalEpisodeBuildPreview.mockResolvedValue(canonicalPreview);
    apiMocks.createCanonicalPositionEpisodeBuild.mockResolvedValue({
      dataState: 'ready',
      build: { id: 9 },
    });
  });

  it('does not load before the Positions tab is active and fetches each list key once', async () => {
    const { result, rerender } = renderHook(
      ({ active, page }) => usePositionEpisodes({
        active,
        filters: { page, perPage: 50 },
      }),
      { initialProps: { active: false, page: 1 } },
    );

    expect(apiMocks.fetchPositionEpisodes).not.toHaveBeenCalled();
    rerender({ active: true, page: 1 });
    await waitFor(() => expect(result.current.list).toEqual(emptyList));
    expect(apiMocks.fetchPositionEpisodes).toHaveBeenCalledTimes(1);

    rerender({ active: false, page: 1 });
    rerender({ active: true, page: 1 });
    expect(apiMocks.fetchPositionEpisodes).toHaveBeenCalledTimes(1);

    rerender({ active: true, page: 2 });
    await waitFor(() => expect(apiMocks.fetchPositionEpisodes).toHaveBeenCalledTimes(2));
  });

  it('caches detail evidence after the first on-demand request', async () => {
    const { result } = renderHook(() => usePositionEpisodes({
      active: false,
      filters: { page: 1, perPage: 50 },
    }));

    await act(async () => {
      await result.current.loadDetail(41);
    });
    expect(apiMocks.fetchPositionEpisodeDetail).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.loadDetail(41);
    });
    expect(apiMocks.fetchPositionEpisodeDetail).toHaveBeenCalledTimes(1);
  });

  it('loads an explicitly selected build and keeps detail reads on the same build', async () => {
    const { result } = renderHook(() => usePositionEpisodes({
      active: true,
      filters: { buildId: 9, page: 1, perPage: 50 },
    }));

    await waitFor(() => expect(result.current.list).toEqual(emptyList));
    expect(apiMocks.fetchPositionEpisodes).toHaveBeenCalledWith(
      expect.objectContaining({ buildId: 9 }),
    );

    await act(async () => {
      await result.current.loadDetail(41);
    });
    expect(apiMocks.fetchPositionEpisodeDetail).toHaveBeenCalledWith(41, 9);
  });

  it('previews and appends a canonical build without reloading the default list', async () => {
    const { result } = renderHook(() => usePositionEpisodes({
      active: true,
      filters: { page: 1, perPage: 50 },
    }));

    await waitFor(() => expect(result.current.canonicalPreview).toEqual(canonicalPreview));
    await waitFor(() => expect(result.current.list).toEqual(emptyList));
    const listCallsBeforeBuild = apiMocks.fetchPositionEpisodes.mock.calls.length;

    await act(async () => {
      await result.current.buildCanonical(true, false);
    });

    expect(apiMocks.createCanonicalPositionEpisodeBuild).toHaveBeenCalledWith({
      canonicalSetId: 1,
      canonicalSetSha256: 'a'.repeat(64),
      buildKey: 'b'.repeat(64),
      acceptAssumedFlat: true,
      acceptGroupFeeScope: false,
    });
    expect(apiMocks.fetchPositionEpisodes).toHaveBeenCalledTimes(listCallsBeforeBuild);
    expect(result.current.canonicalBuildResult?.build.id).toBe(9);
  });
});
