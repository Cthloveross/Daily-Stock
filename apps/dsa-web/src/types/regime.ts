export interface RegimeScoreItem {
  date: string;
  score: number;
  label: string;
  actionHint?: string | null;
  d1Direction: number;
  d2Volatility: number;
  d3MacroPenalty: number;
  d4Sector: number;
  d5PrevDay: number;
  d6Premarket: number;
  snapshot: Record<string, unknown>;
  version: string;
  generatedAt?: string | null;
}

export interface RegimeHistoryResponse {
  count: number;
  items: RegimeScoreItem[];
}

export interface BreakoutSignalItem {
  tradeId: number;
  underlying: string;
  entryTime?: string | null;
  tradeStyle?: string | null;
  wasFakeBreakout?: boolean | null;
  pnlNet?: number | null;
  regimeScoreAtEntry?: number | null;
  breakoutVolumeMult?: number | null;
  rsVsSpy?: number | null;
}

export interface BreakoutSignalsResponse {
  count: number;
  items: BreakoutSignalItem[];
}
