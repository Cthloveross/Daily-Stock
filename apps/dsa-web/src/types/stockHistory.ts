export type Timeframe =
  | '1m'
  | '5m'
  | '15m'
  | '30m'
  | '60m'
  | '90m'
  | '1h'
  | 'daily'
  | 'weekly'
  | 'monthly';

export interface StockKLine {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  amount?: number | null;
  changePercent?: number | null;
}

export interface StockHistory {
  stockCode: string;
  stockName?: string | null;
  period: Timeframe;
  data: StockKLine[];
}

export interface StockNewsItem {
  title: string;
  snippet: string;
  url: string;
  source?: string | null;
  publishedAt?: string | null;
}

export interface StockNewsResponse {
  stockCode: string;
  total: number;
  items: StockNewsItem[];
  provider?: string | null;
}

export type SentimentScore = -2 | -1 | 0 | 1 | 2;

export interface NewsSentimentItem {
  url: string;
  score: SentimentScore;
  reason?: string | null;
}

export interface StockNewsDigestResponse {
  stockCode: string;
  newsCount: number;
  overallScore: SentimentScore;
  overallLabel: string;
  summary: string;
  bullets: string[];
  items: NewsSentimentItem[];
  cached: boolean;
  generatedAt?: string | null;
}
