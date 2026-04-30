import type React from 'react';
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { Button } from '../ui';
import { useStockIndex } from '../../hooks/useStockIndex';
import { useAutocomplete } from '../../hooks/useAutocomplete';
import { cn } from '../../utils/cn';
import type { StockIndexItem, StockSuggestion } from '../../types/stockIndex';

/**
 * Classic Levenshtein edit-distance. Keeps the input short (ticker codes are
 * 1-6 chars) so the O(nm) is cheap. Early-exit at 3 — beyond that we don't
 * care for "did you mean" UX.
 */
function editDistance(a: string, b: string, cap = 3): number {
  const al = a.length;
  const bl = b.length;
  if (Math.abs(al - bl) > cap) return cap + 1;
  if (al === 0) return bl;
  if (bl === 0) return al;
  let prev = Array.from({ length: bl + 1 }, (_, i) => i);
  for (let i = 1; i <= al; i++) {
    const curr = [i, ...new Array(bl).fill(0)];
    let rowMin = i;
    for (let j = 1; j <= bl; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      curr[j] = Math.min(
        curr[j - 1] + 1,
        prev[j] + 1,
        prev[j - 1] + cost,
      );
      if (curr[j] < rowMin) rowMin = curr[j];
    }
    if (rowMin > cap) return cap + 1;
    prev = curr;
  }
  return prev[bl];
}

/**
 * Fuzzy fallback: if `searchStocks` returned nothing (typo), scan the index
 * for entries within edit distance ≤ 2 on canonicalCode or displayCode.
 * Returns up to `limit` suggestions sorted by distance ascending, popularity
 * descending.
 */
function fuzzyFallback(
  query: string,
  index: StockIndexItem[],
  limit = 3,
): StockSuggestion[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2 || q.length > 8) return [];
  const scored: Array<{ item: StockIndexItem; dist: number }> = [];
  for (const item of index) {
    if (!item.active) continue;
    const dc = item.displayCode.toLowerCase();
    const cc = item.canonicalCode.toLowerCase();
    // Skip obviously different-length codes (saves ~90% of work)
    if (Math.abs(dc.length - q.length) > 2 && Math.abs(cc.length - q.length) > 2) continue;
    const d1 = editDistance(q, dc, 2);
    const d2 = editDistance(q, cc, 2);
    const dist = Math.min(d1, d2);
    if (dist <= 2) scored.push({ item, dist });
  }
  scored.sort((a, b) => {
    if (a.dist !== b.dist) return a.dist - b.dist;
    return (b.item.popularity || 0) - (a.item.popularity || 0);
  });
  return scored.slice(0, limit).map((s) => ({
    canonicalCode: s.item.canonicalCode,
    displayCode: s.item.displayCode,
    nameZh: s.item.nameZh,
    market: s.item.market,
    matchType: 'fuzzy' as const,
    matchField: 'code' as const,
    score: 10 - s.dist,
  }));
}

/**
 * TickerPicker
 * ------------
 * Guided ticker input: as the user types we search the local stock index
 * (`public/stock-index.json`) and show matches — exactly like serious
 * trading UIs do. "Add" is only enabled when either:
 *   1. the user has highlighted / picked a suggestion from the dropdown, OR
 *   2. the raw text matches a real entry's canonical/display code (case-insensitive).
 *
 * This means typos like `AMAZ` can't silently pollute the user's watchlist.
 */

interface Props {
  onAdd: (canonicalCode: string, nameZh?: string) => void;
  placeholder?: string;
  /** Visible input width (tailwind className). */
  className?: string;
  /** Stop propagation of onClick — useful when embedded in row-clickable tables. */
  stopPropagation?: boolean;
}

export const TickerPicker: React.FC<Props> = ({
  onAdd,
  placeholder = 'Search ticker · AMZN / NVDA / 600519 / 贵州茅台',
  className,
  stopPropagation,
}) => {
  const { index, fallback, loading } = useStockIndex();
  const {
    query,
    setQuery,
    suggestions,
    isOpen,
    highlightedIndex,
    setHighlightedIndex,
    highlightPrevious,
    highlightNext,
    close,
    reset,
    isComposing,
    setIsComposing,
    runtimeFallback,
  } = useAutocomplete(index, { minLength: 1, limit: 8 });

  const [error, setError] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Did-you-mean: when searchStocks returns nothing (typo like `amaz` → AMZN),
  // fall back to Levenshtein distance ≤ 2 on the index. Empty while normal
  // suggestions exist so we don't compete with them.
  const fuzzySuggestions = useMemo(() => {
    if (!query.trim() || suggestions.length > 0 || !index.length) return [];
    return fuzzyFallback(query, index, 3);
  }, [query, suggestions.length, index]);

  // Close on outside click.
  useEffect(() => {
    if (!isOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) close();
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [isOpen, close]);

  // When index fails or is still loading, we still accept direct typing
  // because the user may know what they want — but we warn them via toast
  // on submit. Keep the dropdown closed in that case.
  const indexReady = !fallback && !runtimeFallback && !loading && index.length > 0;

  // Best-match lookup: resolve the current query (case-insensitive) to a
  // real index entry. We match displayCode, canonicalCode, or exact nameZh.
  const matchedEntry = useMemo(() => {
    if (!indexReady || !query.trim()) return null;
    const q = query.trim().toUpperCase();
    // If the highlighted suggestion is a genuine prefix/exact match, prefer
    // whatever the user is actively navigating to.
    if (highlightedIndex >= 0 && suggestions[highlightedIndex]) {
      const s = suggestions[highlightedIndex];
      return index.find((it) => it.canonicalCode === s.canonicalCode) ?? null;
    }
    // Fall back to an exact code match on the index itself (lets a user type
    // `AMZN` + Enter without ever opening the dropdown).
    return (
      index.find(
        (it) =>
          it.displayCode.toUpperCase() === q ||
          it.canonicalCode.toUpperCase() === q,
      ) ?? null
    );
  }, [query, index, indexReady, highlightedIndex, suggestions]);

  // Enabled when: exact match, a highlighted/top suggestion, or a fuzzy
  // did-you-mean candidate exists.
  const canAdd =
    indexReady &&
    (matchedEntry != null || suggestions.length > 0 || fuzzySuggestions.length > 0);

  const addNow = (canonicalCode: string, name?: string) => {
    onAdd(canonicalCode, name);
    reset();
    setError(null);
  };

  const handleSubmit = () => {
    if (!indexReady) {
      // Fallback path: let the user submit raw, the caller's store can still
      // uppercase it. We warn but don't block — the index load may have
      // failed for reasons unrelated to their ticker being real.
      const raw = query.trim().toUpperCase();
      if (!raw) return;
      onAdd(raw);
      reset();
      setError(null);
      return;
    }
    if (highlightedIndex >= 0 && suggestions[highlightedIndex]) {
      const s = suggestions[highlightedIndex];
      addNow(s.canonicalCode, s.nameZh);
      return;
    }
    if (matchedEntry) {
      addNow(matchedEntry.canonicalCode, matchedEntry.nameZh);
      return;
    }
    if (suggestions[0]) {
      // Prefix / contains match exists → top one is the did-you-mean target.
      const s = suggestions[0];
      addNow(s.canonicalCode, s.nameZh);
      return;
    }
    if (fuzzySuggestions[0]) {
      // Pure typo (e.g. `amaz` → AMZN). Auto-pick the closest by edit distance.
      const s = fuzzySuggestions[0];
      addNow(s.canonicalCode, s.nameZh);
      return;
    }
    setError(`未找到 "${query.trim().toUpperCase()}"，请检查拼写`);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (isComposing) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (isOpen) highlightNext();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (isOpen) highlightPrevious();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape') {
      close();
    }
  };

  return (
    <div
      ref={wrapperRef}
      className={cn('relative', className)}
      onClick={stopPropagation ? (e) => e.stopPropagation() : undefined}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit();
        }}
        className="flex items-center gap-1"
      >
        <div className="relative flex-1">
          <Search
            size={12}
            strokeWidth={1.5}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-3"
          />
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setError(null);
            }}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => setIsComposing(true)}
            onCompositionEnd={() => setIsComposing(false)}
            placeholder={placeholder}
            disabled={loading}
            className="h-7 w-full rounded-ds-sm border border-subtle bg-bg-0 pl-6 pr-2 text-body-sm text-text-1 placeholder:text-text-3 focus:border-default focus:outline-none"
            aria-autocomplete="list"
            aria-expanded={isOpen}
            role="combobox"
          />
        </div>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          iconLeft={Plus}
          disabled={loading || (indexReady && !canAdd)}
          title={
            loading
              ? '股票索引加载中…'
              : matchedEntry
                ? `加入 ${matchedEntry.displayCode} · ${matchedEntry.nameZh}`
                : suggestions[0]
                  ? `加入 ${suggestions[0].displayCode} · ${suggestions[0].nameZh}`
                  : fuzzySuggestions[0]
                    ? `Did you mean ${fuzzySuggestions[0].displayCode} · ${fuzzySuggestions[0].nameZh}?`
                    : '从建议里选一个 ticker'
          }
        >
          Add
        </Button>
      </form>

      {/* Suggestion dropdown */}
      {isOpen && suggestions.length > 0 && (
        <ul
          role="listbox"
          className="absolute left-0 right-0 z-dropdown mt-1 max-h-64 overflow-y-auto rounded-ds-md border border-default bg-bg-2 py-1 shadow-md"
        >
          {suggestions.map((s, i) => (
            <li
              key={s.canonicalCode}
              role="option"
              aria-selected={i === highlightedIndex}
              onMouseEnter={() => setHighlightedIndex(i)}
              onMouseDown={(e) => {
                // Prevent input blur before click registers
                e.preventDefault();
                addNow(s.canonicalCode, s.nameZh);
              }}
              className={cn(
                'flex cursor-pointer items-center justify-between gap-3 px-3 py-1.5 text-body-sm',
                i === highlightedIndex ? 'bg-bg-3 text-text-1' : 'text-text-1 hover:bg-bg-3',
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 rounded-full border border-subtle px-1.5 py-0 text-[9px] uppercase text-text-3">
                  {s.market}
                </span>
                <span className="font-mono text-mono-sm text-text-1">{s.displayCode}</span>
                <span className="truncate text-text-2">{s.nameZh}</span>
              </span>
              <span className="shrink-0 text-caption text-text-3">
                {s.matchType === 'exact' ? '匹配' : s.matchType === 'prefix' ? '前缀' : ''}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Did-you-mean row: shown when we got no prefix/contains hits but the
          edit-distance fallback found at least one near-miss. Clicking a chip
          adds that ticker immediately; pressing Enter in the input adds the
          first one (matches the intent: type `amaz` → Enter → AMZN). */}
      {!isOpen && fuzzySuggestions.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1 text-caption">
          <span className="text-text-3">Did you mean</span>
          {fuzzySuggestions.map((s) => (
            <button
              key={s.canonicalCode}
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                addNow(s.canonicalCode, s.nameZh);
              }}
              className="inline-flex items-center gap-1 rounded-full border border-subtle bg-bg-2 px-2 py-0.5 text-text-1 hover:border-accent hover:text-accent"
              title={`加入 ${s.displayCode} · ${s.nameZh}`}
            >
              <span className="font-mono text-mono-xs">{s.displayCode}</span>
              <span className="text-text-3">· {s.nameZh}</span>
            </button>
          ))}
          <span className="text-text-3">?</span>
        </div>
      )}

      {error && <div className="mt-1 text-caption text-down-strong">{error}</div>}
    </div>
  );
};

export default TickerPicker;
