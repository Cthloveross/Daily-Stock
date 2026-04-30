import { useEffect, useState } from 'react';
import { stocksApi, type StockQuote } from '../api/stocks';

/**
 * Fetch live quotes for a list of tickers in parallel. Returns a map keyed
 * by uppercase ticker. Cached via sessionCache (see `stocksApi.getQuote`),
 * so re-mounts within 2 min are network-free.
 *
 * Failures per-ticker become `null` entries rather than throwing — the UI
 * just shows `—` for those rows.
 */
export function useTickerQuotes(tickers: string[]): {
  quotes: Record<string, StockQuote | null>;
  loading: boolean;
} {
  const key = tickers.map((t) => t.toUpperCase()).sort().join(',');
  const [quotes, setQuotes] = useState<Record<string, StockQuote | null>>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!tickers.length) {
      setQuotes({});
      return;
    }
    let cancelled = false;
    setLoading(true);
    Promise.all(
      tickers.map((t) =>
        stocksApi.getQuote(t).then((q) => [t.toUpperCase(), q] as const).catch(() => [t.toUpperCase(), null] as const),
      ),
    ).then((entries) => {
      if (cancelled) return;
      setQuotes(Object.fromEntries(entries));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { quotes, loading };
}
