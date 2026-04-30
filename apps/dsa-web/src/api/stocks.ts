import apiClient from './index';
import { toCamelCase } from './utils';
import { sessionCache } from '../utils/sessionCache';
import type {
  StockHistory,
  StockNewsDigestResponse,
  StockNewsResponse,
  Timeframe,
} from '../types/stockHistory';

export interface StockQuote {
  stockCode: string;
  stockName?: string | null;
  currentPrice: number;
  change?: number | null;
  changePercent?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  prevClose?: number | null;
  volume?: number | null;
  amount?: number | null;
  updateTime?: string | null;
}

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  codes: string[];
  items?: ExtractItem[];
  rawText?: string;
};

export const stocksApi = {
  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { codes?: string[]; items?: ExtractItem[]; raw_text?: string };
    return {
      codes: data.codes ?? [],
      items: data.items,
      rawText: data.raw_text,
    };
  },

  async getQuote(code: string, opts: { refresh?: boolean } = {}): Promise<StockQuote | null> {
    const key = `stock:quote:${code.toUpperCase()}`;
    if (!opts.refresh) {
      const cached = sessionCache.get<StockQuote>(key);
      if (cached) return cached;
    }
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/stocks/${encodeURIComponent(code)}/quote`,
      );
      const quote = toCamelCase<StockQuote>(response.data);
      // Skip caching a placeholder response (0 price etc.) so the next page
      // load gives the user a fresh chance rather than a stuck blank.
      if (quote && quote.currentPrice > 0) {
        sessionCache.set(key, quote, 2 * 60 * 1000);
      }
      return quote;
    } catch {
      // 404 for invalid ticker → just return null; callers render fallback.
      return null;
    }
  },

  async getHistory(
    code: string,
    period: Timeframe = 'daily',
    days = 180,
    opts: { refresh?: boolean } = {},
  ): Promise<StockHistory> {
    const key = `stock:history:${code.toUpperCase()}:${period}:${days}`;
    if (!opts.refresh) {
      const cached = sessionCache.get<StockHistory>(key);
      if (cached) return cached;
    }
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(code)}/history`,
      { params: { period, days } },
    );
    const data = toCamelCase<StockHistory>(response.data);
    // Only cache when we actually got bars. Empty arrays get re-fetched next
    // time — otherwise a transient backend hiccup poisons the 10-min cache.
    if (data.data && data.data.length > 0) {
      sessionCache.set(key, data);
    }
    return data;
  },

  async getNews(
    code: string,
    limit = 15,
    opts: { refresh?: boolean } = {},
  ): Promise<StockNewsResponse> {
    const key = `stock:news:${code.toUpperCase()}:${limit}`;
    if (!opts.refresh) {
      const cached = sessionCache.get<StockNewsResponse>(key);
      if (cached) return cached;
    }
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(code)}/news`,
      { params: { limit } },
    );
    const data = toCamelCase<StockNewsResponse>(response.data);
    if (data.items && data.items.length > 0) {
      sessionCache.set(key, data);
    }
    return data;
  },

  async getNewsDigest(
    code: string,
    opts: { limit?: number; refresh?: boolean } = {},
  ): Promise<StockNewsDigestResponse> {
    const { limit = 10, refresh = false } = opts;
    const key = `stock:digest:${code.toUpperCase()}:${limit}`;
    if (!refresh) {
      const cached = sessionCache.get<StockNewsDigestResponse>(key);
      if (cached) return cached;
    }
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(code)}/news/digest`,
      { params: { limit, refresh }, timeout: 60000 },
    );
    const data = toCamelCase<StockNewsDigestResponse>(response.data);
    // Only cache real digests — the LLM fallback path returns newsCount=0
    // with a "LLM failed" summary, which we never want to persist.
    if (data.newsCount > 0) {
      sessionCache.set(key, data);
    }
    return data;
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    throw new Error('请提供文件或粘贴文本');
  },
};
