import type {
  PositionEpisodeAiReviewUserContext,
  PositionEpisodeTradeLogicDraft,
} from '../../../types/journal';

const STORAGE_VERSION = 1;
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
  version: typeof STORAGE_VERSION;
  updatedAt: string;
  draft: PositionEpisodeTradeLogicDraft;
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

export function emptyEpisodeReviewDraft(): PositionEpisodeTradeLogicDraft {
  return {
    setupThesis: '',
    entryTrigger: '',
    invalidationPlan: '',
    positionRationale: '',
    exitReason: '',
    postTradeReflection: '',
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
): PositionEpisodeTradeLogicDraft {
  const storage = localStorageOrNull();
  if (!storage) return emptyEpisodeReviewDraft();

  const key = episodeReviewDraftStorageKey(buildId, episodeId);
  const raw = storage.getItem(key);
  if (!raw) return emptyEpisodeReviewDraft();

  try {
    const parsed = JSON.parse(raw) as Partial<StoredEpisodeReviewDraft>;
    if (parsed.version !== STORAGE_VERSION || !parsed.draft || typeof parsed.draft !== 'object') {
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
  draft: PositionEpisodeTradeLogicDraft,
): boolean {
  const storage = localStorageOrNull();
  if (!storage) return false;
  const key = episodeReviewDraftStorageKey(buildId, episodeId);

  try {
    if (episodeReviewContextFieldCount(draft) === 0) {
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
