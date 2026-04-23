import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  StockHistory,
  StockNewsDigestResponse,
  StockNewsResponse,
  Timeframe,
} from '../types/stockHistory';

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

  async getHistory(
    code: string,
    period: Timeframe = 'daily',
    days = 180,
  ): Promise<StockHistory> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(code)}/history`,
      { params: { period, days } },
    );
    return toCamelCase<StockHistory>(response.data);
  },

  async getNews(code: string, limit = 15): Promise<StockNewsResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(code)}/news`,
      { params: { limit } },
    );
    return toCamelCase<StockNewsResponse>(response.data);
  },

  async getNewsDigest(
    code: string,
    opts: { limit?: number; refresh?: boolean } = {},
  ): Promise<StockNewsDigestResponse> {
    const { limit = 10, refresh = false } = opts;
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(code)}/news/digest`,
      { params: { limit, refresh }, timeout: 60000 },
    );
    return toCamelCase<StockNewsDigestResponse>(response.data);
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
