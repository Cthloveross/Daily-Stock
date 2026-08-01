import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createCanonicalPositionEpisodeBuild,
  createPositionEpisodeBuild,
  fetchCanonicalEpisodeBuildPreview,
  fetchPositionEpisodeDetail,
  fetchPositionEpisodes,
} from '../api/journal';
import { parseApiError, type ParsedApiError } from '../api/error';
import type {
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildResponse,
  PositionEpisodeDetailResponse,
  PositionEpisodeFilters,
  PositionEpisodeListResponse,
} from '../types/journal';

interface UsePositionEpisodesOptions {
  active: boolean;
  filters: PositionEpisodeFilters;
}

const pendingListRequests = new Map<string, Promise<PositionEpisodeListResponse>>();
const pendingCanonicalPreviewRequests = new Map<
  string,
  Promise<CanonicalEpisodeBuildPlanResponse>
>();

function fetchPositionEpisodesOnce(
  key: string,
  filters: PositionEpisodeFilters,
): Promise<PositionEpisodeListResponse> {
  const existing = pendingListRequests.get(key);
  if (existing) return existing;
  const request = fetchPositionEpisodes(filters).finally(() => {
    if (pendingListRequests.get(key) === request) pendingListRequests.delete(key);
  });
  pendingListRequests.set(key, request);
  return request;
}

function fetchCanonicalPreviewOnce(
  key: string,
): Promise<CanonicalEpisodeBuildPlanResponse> {
  const existing = pendingCanonicalPreviewRequests.get(key);
  if (existing) return existing;
  const request = fetchCanonicalEpisodeBuildPreview().finally(() => {
    if (pendingCanonicalPreviewRequests.get(key) === request) {
      pendingCanonicalPreviewRequests.delete(key);
    }
  });
  pendingCanonicalPreviewRequests.set(key, request);
  return request;
}

export function usePositionEpisodes({ active, filters }: UsePositionEpisodesOptions) {
  const [list, setList] = useState<PositionEpisodeListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [detailById, setDetailById] = useState<Record<number, PositionEpisodeDetailResponse>>({});
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null);
  const [detailError, setDetailError] = useState<ParsedApiError | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildResult, setBuildResult] = useState<EpisodeBuildResponse | null>(null);
  const [canonicalPreview, setCanonicalPreview] = useState<CanonicalEpisodeBuildPlanResponse | null>(null);
  const [canonicalPreviewLoading, setCanonicalPreviewLoading] = useState(false);
  const [canonicalPreviewError, setCanonicalPreviewError] = useState<ParsedApiError | null>(null);
  const [canonicalBuilding, setCanonicalBuilding] = useState(false);
  const [canonicalBuildResult, setCanonicalBuildResult] = useState<EpisodeBuildResponse | null>(null);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [canonicalPreviewReloadVersion, setCanonicalPreviewReloadVersion] = useState(0);
  const loadedListKeyRef = useRef<string | null>(null);
  const loadedCanonicalPreviewKeyRef = useRef<string | null>(null);
  const pendingDetailIdsRef = useRef(new Set<number>());

  const listKey = JSON.stringify({
    underlying: filters.underlying || '',
    lifecycleStatus: filters.lifecycleStatus || '',
    completenessStatus: filters.completenessStatus || '',
    caseFocus: filters.caseFocus || '',
    reviewStatus: filters.reviewStatus || '',
    buildId: filters.buildId ?? null,
    page: filters.page ?? 1,
    perPage: filters.perPage ?? 50,
    reloadVersion,
  });
  const canonicalPreviewKey = `latest:${canonicalPreviewReloadVersion}`;

  useEffect(() => {
    loadedListKeyRef.current = null;
    setList(null);
    setDetailById({});
    pendingDetailIdsRef.current.clear();
  }, [filters.buildId]);

  useEffect(() => {
    if (!active || loadedListKeyRef.current === listKey) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchPositionEpisodesOnce(listKey, filters)
      .then((response) => {
        if (!cancelled) {
          loadedListKeyRef.current = listKey;
          setList(response);
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(parseApiError(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active, filters, listKey]);

  useEffect(() => {
    if (!active || loadedCanonicalPreviewKeyRef.current === canonicalPreviewKey) return;
    let cancelled = false;
    setCanonicalPreviewLoading(true);
    setCanonicalPreviewError(null);
    void fetchCanonicalPreviewOnce(canonicalPreviewKey)
      .then((response) => {
        if (!cancelled) {
          loadedCanonicalPreviewKeyRef.current = canonicalPreviewKey;
          setCanonicalPreview(response);
        }
      })
      .catch((reason) => {
        if (!cancelled) setCanonicalPreviewError(parseApiError(reason));
      })
      .finally(() => {
        if (!cancelled) setCanonicalPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active, canonicalPreviewKey]);

  const reload = useCallback(() => {
    loadedListKeyRef.current = null;
    setReloadVersion((value) => value + 1);
  }, []);

  const reloadCanonicalPreview = useCallback(() => {
    loadedCanonicalPreviewKeyRef.current = null;
    setCanonicalPreview(null);
    setCanonicalBuildResult(null);
    setCanonicalPreviewReloadVersion((value) => value + 1);
  }, []);

  const loadDetail = useCallback(async (episodeId: number) => {
    if (detailById[episodeId] || pendingDetailIdsRef.current.has(episodeId)) return;
    pendingDetailIdsRef.current.add(episodeId);
    setDetailLoadingId(episodeId);
    setDetailError(null);
    try {
      const detail = await fetchPositionEpisodeDetail(episodeId, filters.buildId);
      setDetailById((current) => ({ ...current, [episodeId]: detail }));
    } catch (reason) {
      setDetailError(parseApiError(reason));
    } finally {
      pendingDetailIdsRef.current.delete(episodeId);
      setDetailLoadingId((current) => (current === episodeId ? null : current));
    }
  }, [detailById, filters.buildId]);

  const buildAssumedFlat = useCallback(async () => {
    setBuilding(true);
    setError(null);
    try {
      const response = await createPositionEpisodeBuild();
      setBuildResult(response);
      setDetailById({});
      reload();
      return response;
    } catch (reason) {
      const parsed = parseApiError(reason);
      setError(parsed);
      throw reason;
    } finally {
      setBuilding(false);
    }
  }, [reload]);

  const buildCanonical = useCallback(async (
    acceptAssumedFlat: boolean,
    acceptGroupFeeScope: boolean,
  ) => {
    if (
      canonicalPreview?.canonicalSetId == null
      || !canonicalPreview.canonicalSetSha256
      || !canonicalPreview.buildKey
    ) {
      throw new Error('可信事实集预览缺少构建标识，请重新加载预览。');
    }
    setCanonicalBuilding(true);
    setCanonicalPreviewError(null);
    try {
      const response = await createCanonicalPositionEpisodeBuild({
        canonicalSetId: canonicalPreview.canonicalSetId,
        canonicalSetSha256: canonicalPreview.canonicalSetSha256,
        buildKey: canonicalPreview.buildKey,
        acceptAssumedFlat,
        acceptGroupFeeScope,
      });
      setCanonicalBuildResult(response);
      return response;
    } catch (reason) {
      const parsed = parseApiError(reason);
      setCanonicalPreviewError(parsed);
      throw reason;
    } finally {
      setCanonicalBuilding(false);
    }
  }, [canonicalPreview]);

  return {
    list,
    loading,
    error,
    reload,
    detailById,
    detailLoadingId,
    detailError,
    loadDetail,
    building,
    buildResult,
    buildAssumedFlat,
    canonicalPreview,
    canonicalPreviewLoading,
    canonicalPreviewError,
    reloadCanonicalPreview,
    canonicalBuilding,
    canonicalBuildResult,
    buildCanonical,
  };
}

export default usePositionEpisodes;
