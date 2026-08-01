import type {
  PositionEpisodeAiReviewUserContext,
  PositionEpisodeReviewAnnotation,
  PositionEpisodeReviewWorkspaceDraft,
  PositionEpisodeTradeLogicDraft,
} from '../../../types/journal';

const STORAGE_VERSION = 2;
const STORAGE_PREFIX = 'dsa:journal:episode-review-draft:v1';

export const EPISODE_REVIEW_FIELD_MAX_CHARS = 2000;
export const EPISODE_REVIEW_TOTAL_MAX_CHARS = 6000;

const DRAFT_FIELDS = [
  'setupThesis',
  'entryTrigger',
  'invalidationPlan',
  'positionRationale',
  'exitReason',
  'postTradeReflection',
] as const satisfies readonly (keyof PositionEpisodeTradeLogicDraft)[];

interface StoredEpisodeReviewDraft {
  version: 1 | typeof STORAGE_VERSION;
  updatedAt: string;
  draft: Partial<PositionEpisodeReviewWorkspaceDraft>;
}

/** Count Unicode code points after the same trim operation used by the API. */
export function episodeReviewTextCharacterCount(value: string): number {
  return Array.from(value.trim()).length;
}

/** Preserve harmless outer whitespace unless trimmed content exceeds a limit. */
export function limitEpisodeReviewText(value: string, maxCharacters: number): string {
  if (episodeReviewTextCharacterCount(value) <= maxCharacters) return value;
  return Array.from(value.trim()).slice(0, maxCharacters).join('');
}

function localStorageOrNull(): Storage | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

export function emptyEpisodeReviewDraft(): PositionEpisodeReviewWorkspaceDraft {
  return {
    setupThesis: '',
    entryTrigger: '',
    invalidationPlan: '',
    positionRationale: '',
    exitReason: '',
    postTradeReflection: '',
    tags: [],
    errorTypes: [],
  };
}

export function episodeReviewAnnotationToDraft(
  annotation: PositionEpisodeReviewAnnotation,
): PositionEpisodeReviewWorkspaceDraft {
  return {
    setupThesis: annotation.setupThesis ?? '',
    entryTrigger: annotation.entryTrigger ?? '',
    invalidationPlan: annotation.invalidationPlan ?? '',
    positionRationale: annotation.positionRationale ?? '',
    exitReason: annotation.exitReason ?? '',
    postTradeReflection: annotation.postTradeReflection ?? '',
    tags: Array.isArray(annotation.tags) ? annotation.tags : [],
    errorTypes: Array.isArray(annotation.errorTypes) ? annotation.errorTypes : [],
  };
}

export function episodeReviewDraftStorageKey(buildId: number, episodeId: number): string {
  return `${STORAGE_PREFIX}:build:${buildId}:episode:${episodeId}`;
}

export function compactEpisodeReviewUserContext(
  draft: PositionEpisodeTradeLogicDraft,
): PositionEpisodeAiReviewUserContext {
  const compact: PositionEpisodeAiReviewUserContext = {};
  for (const field of DRAFT_FIELDS) {
    const value = draft[field].trim();
    if (value) compact[field] = value;
  }
  return compact;
}

export function episodeReviewContextFieldCount(
  draft: PositionEpisodeTradeLogicDraft,
): number {
  return Object.keys(compactEpisodeReviewUserContext(draft)).length;
}

export function hasEpisodeReviewDraft(
  draft: PositionEpisodeReviewWorkspaceDraft,
): boolean {
  return episodeReviewContextFieldCount(draft) > 0
    || draft.tags.some((value) => value.trim())
    || draft.errorTypes.some((value) => value.trim());
}

export function episodeReviewDraftCharacterCount(
  draft: PositionEpisodeTradeLogicDraft,
): number {
  return DRAFT_FIELDS.reduce(
    (total, field) => total + episodeReviewTextCharacterCount(draft[field]),
    0,
  );
}

export function loadEpisodeReviewDraft(
  buildId: number,
  episodeId: number,
): PositionEpisodeReviewWorkspaceDraft {
  const storage = localStorageOrNull();
  if (!storage) return emptyEpisodeReviewDraft();

  const key = episodeReviewDraftStorageKey(buildId, episodeId);
  const raw = storage.getItem(key);
  if (!raw) return emptyEpisodeReviewDraft();

  try {
    const parsed = JSON.parse(raw) as Partial<StoredEpisodeReviewDraft>;
    if (
      (parsed.version !== 1 && parsed.version !== STORAGE_VERSION)
      || !parsed.draft
      || typeof parsed.draft !== 'object'
    ) {
      storage.removeItem(key);
      return emptyEpisodeReviewDraft();
    }

    const restored = emptyEpisodeReviewDraft();
    let remainingTotal = EPISODE_REVIEW_TOTAL_MAX_CHARS;
    for (const field of DRAFT_FIELDS) {
      const value = parsed.draft[field];
      if (typeof value === 'string') {
        const fieldValue = limitEpisodeReviewText(value, EPISODE_REVIEW_FIELD_MAX_CHARS);
        restored[field] = limitEpisodeReviewText(fieldValue, remainingTotal);
        remainingTotal -= episodeReviewTextCharacterCount(restored[field]);
      }
    }
    const cleanLabels = (value: unknown): string[] => (
      Array.isArray(value)
        ? [...new Set(value.filter((item): item is string => typeof item === 'string')
          .map((item) => item.trim())
          .filter(Boolean))]
        : []
    );
    restored.tags = cleanLabels(parsed.draft.tags);
    restored.errorTypes = cleanLabels(parsed.draft.errorTypes);
    return restored;
  } catch {
    storage.removeItem(key);
    return emptyEpisodeReviewDraft();
  }
}

/** Returns false when browser persistence is unavailable or rejected. */
export function saveEpisodeReviewDraft(
  buildId: number,
  episodeId: number,
  draft: PositionEpisodeReviewWorkspaceDraft,
): boolean {
  const storage = localStorageOrNull();
  if (!storage) return false;
  const key = episodeReviewDraftStorageKey(buildId, episodeId);

  try {
    if (!hasEpisodeReviewDraft(draft)) {
      storage.removeItem(key);
      return true;
    }
    const payload: StoredEpisodeReviewDraft = {
      version: STORAGE_VERSION,
      updatedAt: new Date().toISOString(),
      draft,
    };
    storage.setItem(key, JSON.stringify(payload));
    return true;
  } catch {
    return false;
  }
}

export function clearEpisodeReviewDraft(buildId: number, episodeId: number): boolean {
  const storage = localStorageOrNull();
  if (!storage) return false;
  try {
    storage.removeItem(episodeReviewDraftStorageKey(buildId, episodeId));
    return true;
  } catch {
    return false;
  }
}
