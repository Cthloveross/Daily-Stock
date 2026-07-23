export interface TradingViewWatchlistParseResult {
  symbols: string[];
  ignored: string[];
}

const US_OPTION_UNDERLYING = /^[A-Z]{1,5}(?:[.-][A-Z])?$/;

/**
 * Parse TradingView's official TXT watchlist export.
 *
 * TradingView exports comma-separated exchange-qualified symbols and may
 * include `###` section dividers.  The local opportunity engine uses canonical
 * US underlying tickers, so `NASDAQ:AAPL` becomes `AAPL`; futures, crypto and
 * other symbols that cannot be represented safely are reported as ignored.
 */
export function parseTradingViewWatchlist(text: string): TradingViewWatchlistParseResult {
  const symbols: string[] = [];
  const ignored: string[] = [];
  const seen = new Set<string>();

  for (const rawToken of text.replace(/^\uFEFF/, '').split(/[,\r\n]+/)) {
    const token = rawToken.trim().replace(/^["']|["']$/g, '');
    if (!token || token.startsWith('###')) continue;

    const qualifiedParts = token.split(':');
    const ticker = qualifiedParts.at(-1)?.trim().toUpperCase().replace(/^\$/, '') ?? '';
    if (!US_OPTION_UNDERLYING.test(ticker)) {
      ignored.push(token);
      continue;
    }
    if (!seen.has(ticker)) {
      seen.add(ticker);
      symbols.push(ticker);
    }
  }

  return { symbols, ignored };
}
