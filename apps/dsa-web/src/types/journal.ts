// Journal types (Phase 0 Mirror). Mirrors api/v1/schemas/journal.py.

export interface TradeItem {
  id: number;
  portfolioLabel: string;
  isOption: boolean;
  rawSymbol?: string | null;
  underlying: string;
  expiry?: string | null;
  strike?: number | null;
  right?: string | null;
  direction: string;
  status: string;
  quantity: number;
  avgEntryPrice: number;
  avgExitPrice?: number | null;
  entryTime?: string | null;
  exitTime?: string | null;
  holdSeconds?: number | null;
  dteAtEntry?: number | null;
  dteBucket?: string | null;
  pnlGross?: number | null;
  pnlNet?: number | null;
  pnlPct?: number | null;
  totalFee?: number | null;
  tradeStyle?: string | null;
  regimeScoreAtEntry?: number | null;
  wasFakeBreakout?: boolean | null;
  userNotes?: string | null;
  emotionalState?: string | null;
  strategyTagAi?: string | null;
}

export interface TradeListResponse {
  total: number;
  page: number;
  perPage: number;
  items: TradeItem[];
}

export interface RealityTestResponse {
  totalTrades: number;
  totalPnlNet: number;
  topN: number;
  topNPnlNet: number;
  topNIds: number[];
  pnlWithoutTopN: number;
  topNPctOfTotal?: number | null;
  medianPnlNet?: number | null;
}

export interface HealthCheckItem {
  checkDate: string;
  totalOrders: number;
  orders0Dte: number;
  orders13Dte: number;
  ordersOpeningHour: number;
  topUnderlying?: string | null;
  topUnderlyingPct?: number | null;
  warningsJson: unknown[];
  pnlEstimate?: number | null;
  regimeScore?: number | null;
}

export interface JournalStatsResponse {
  windowDays: number;
  closedTradeCount: number;
  totalPnlNet: number;
  winRate?: number | null;
  dteDistribution: Record<string, number>;
  winRateByBucket: Record<
    string,
    { count: number; wins: number; winRate?: number | null; avgPnlNet?: number | null; sumPnlNet: number }
  >;
  realityTest: RealityTestResponse;
}

export interface ImportResponse {
  inserted: number;
  skipped: number;
  tradesRebuilt: number;
  message: string;
}

export interface TradeListFilters {
  symbol?: string;
  start?: string;
  end?: string;
  status?: string;
  style?: string;
  page?: number;
  perPage?: number;
}

export interface TradeUpdateRequest {
  userNotes?: string | null;
  emotionalState?: string | null;
  tradeStyle?: string | null;
}

// ---------- stats-by-style + journal QA ----------

export interface StyleBucketStat {
  style: string;
  count: number;
  winRate: number;
  avgPnlNet: number;
  sumPnlNet: number;
  medianHoldSeconds?: number | null;
  avgPnlPct?: number | null;
}

export interface DteBucketStat {
  bucket: string;
  count: number;
  winRate: number;
  avgPnlNet: number;
  sumPnlNet: number;
}

export interface CompactTradeItem {
  id?: number | null;
  underlying?: string | null;
  direction?: string | null;
  isOption?: boolean | null;
  dteBucket?: string | null;
  tradeStyle?: string | null;
  pnlNet?: number | null;
  pnlPct?: number | null;
  holdSeconds?: number | null;
  entryTime?: string | null;
  exitTime?: string | null;
}

export interface JournalStatsByStyleResponse {
  period: { start: string | null; end: string | null };
  totalCount: number;
  totalPnlNet: number;
  byStyle: StyleBucketStat[];
  byDte: DteBucketStat[];
  worstTrades: CompactTradeItem[];
  bestTrades: CompactTradeItem[];
}

export interface JournalQaRequest {
  framework: string;
  question: string;
  tradeWindowDays?: number;
  tradeLimit?: number;
}

export interface JournalQaResponse {
  answer: string;
  tradesConsidered: number;
  frameworkHash: string;
  generatedAt: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  ts: string; // ISO 8601
}
