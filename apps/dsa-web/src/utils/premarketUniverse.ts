export const MAX_PREMARKET_UNIVERSE_SIZE = 20;
const US_OPTION_UNDERLYING_PATTERN = /^[A-Z]{1,5}(?:[.-][A-Z])?$/;

export interface PreparedPremarketUniverse {
  symbols: string[];
  unsupportedSymbols: string[];
  overflowCount: number;
}

function normalizedUsOptionUnderlying(raw: string): string | null {
  const normalized = raw.trim().toUpperCase().replace(/^\$/, '');
  const symbol = normalized.startsWith('US.') ? normalized.slice(3) : normalized;
  return US_OPTION_UNDERLYING_PATTERN.test(symbol) ? symbol : null;
}

export function preparePremarketUniverse(symbols: string[]): PreparedPremarketUniverse {
  const normalized: string[] = [];
  const unsupportedSymbols: string[] = [];
  const seen = new Set<string>();
  for (const raw of symbols) {
    const symbol = normalizedUsOptionUnderlying(raw);
    if (!symbol) {
      const unsupported = raw.trim().toUpperCase();
      if (unsupported) unsupportedSymbols.push(unsupported);
      continue;
    }
    if (seen.has(symbol)) continue;
    seen.add(symbol);
    normalized.push(symbol);
  }
  return {
    symbols: normalized.slice(0, MAX_PREMARKET_UNIVERSE_SIZE),
    unsupportedSymbols,
    overflowCount: Math.max(0, normalized.length - MAX_PREMARKET_UNIVERSE_SIZE),
  };
}

export function normalizePremarketUniverse(symbols: string[]): string[] {
  return preparePremarketUniverse(symbols).symbols;
}

export function hasSameOrderedPremarketUniverse(
  localSymbols: string[],
  officialSymbols: string[],
): boolean {
  const local = normalizePremarketUniverse(localSymbols);
  const official = normalizePremarketUniverse(officialSymbols);
  return local.length === official.length
    && local.every((symbol, index) => symbol === official[index]);
}
