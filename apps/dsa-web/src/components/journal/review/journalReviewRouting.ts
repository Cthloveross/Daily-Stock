const JOURNAL_CONTEXT_KEYS = [
  'build_id',
  'symbol',
  'status',
  'completeness',
  'case',
  'page',
] as const;

function copyJournalContext(source: URLSearchParams): URLSearchParams {
  const target = new URLSearchParams();
  for (const key of JOURNAL_CONTEXT_KEYS) {
    const value = source.get(key);
    if (value) target.set(key, value);
  }
  return target;
}

export function positionReviewPath(episodeId: number, source: URLSearchParams): string {
  const query = copyJournalContext(source).toString();
  return `/journal/review/${episodeId}${query ? `?${query}` : ''}`;
}

export function journalReturnPath(source: URLSearchParams): string {
  const query = new URLSearchParams();
  query.set('tab', 'positions');
  for (const [key, value] of copyJournalContext(source)) query.set(key, value);
  return `/journal?${query.toString()}`;
}
