import type { Candle, CandleMarker, MAOverlay } from '../../charts/CandlestickChart';
import type { PositionEpisodeEvidenceItem } from '../../../types/journal';
import type { Timeframe } from '../../../types/stockHistory';
import {
  filterByUsTradingSession,
  type UsTradingSession,
} from '../../../utils/usTradingSession';

export interface EvidenceChartLink {
  evidence: PositionEpisodeEvidenceItem;
  chartTime: number | string | null;
  orderTimeProxy: boolean;
  cashFlowSide: 'buy' | 'sell' | 'unknown';
}

export {
  isTimeInUsTradingSession,
} from '../../../utils/usTradingSession';
export type {
  UsTradingSession,
} from '../../../utils/usTradingSession';

const PERIOD_SECONDS: Partial<Record<Timeframe, number>> = {
  '1m': 60,
  '2m': 2 * 60,
  '5m': 5 * 60,
  '15m': 15 * 60,
  '30m': 30 * 60,
  '60m': 60 * 60,
  '1h': 60 * 60,
  '90m': 90 * 60,
};

function toUnixSeconds(value: number | string): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
}

export function filterCandlesByUsTradingSession(
  candles: Candle[],
  session: UsTradingSession,
): Candle[] {
  return filterByUsTradingSession(candles, session);
}

function etCalendarDate(value: string): string | null {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(parsed);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value;
  const year = part('year');
  const month = part('month');
  const day = part('day');
  return year && month && day ? `${year}-${month}-${day}` : null;
}

function candleCalendarDate(candle: Candle): string | null {
  if (typeof candle.time === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(candle.time)) {
    return candle.time;
  }
  const seconds = toUnixSeconds(candle.time);
  return seconds == null ? null : new Date(seconds * 1000).toISOString().slice(0, 10);
}

export function isOrderTimeProxy(evidence: PositionEpisodeEvidenceItem): boolean {
  return evidence.evidenceKind === 'order'
    || evidence.allocation.timingPrecision === 'order_time_proxy';
}

export function cashFlowSide(evidence: PositionEpisodeEvidenceItem): 'buy' | 'sell' | 'unknown' {
  const value = evidence.allocatedCashFlow?.trim();
  if (!value || /^[-+]?0(?:\.0+)?$/.test(value)) return 'unknown';
  return value.startsWith('-') ? 'buy' : 'sell';
}

export function mapEvidenceToCandles(
  evidence: PositionEpisodeEvidenceItem[],
  candles: Candle[],
  timeframe: Timeframe,
): EvidenceChartLink[] {
  if (!candles.length) {
    return evidence.map((item) => ({
      evidence: item,
      chartTime: null,
      orderTimeProxy: isOrderTimeProxy(item),
      cashFlowSide: cashFlowSide(item),
    }));
  }

  if (timeframe === 'daily' || timeframe === 'weekly' || timeframe === 'monthly') {
    const candleByDate = new Map<string, Candle>();
    for (const candle of candles) {
      const date = candleCalendarDate(candle);
      if (date && !candleByDate.has(date)) candleByDate.set(date, candle);
    }
    return evidence.map((item) => {
      const date = etCalendarDate(item.evidenceTime);
      return {
        evidence: item,
        chartTime: (date ? candleByDate.get(date)?.time : null) ?? null,
        orderTimeProxy: isOrderTimeProxy(item),
        cashFlowSide: cashFlowSide(item),
      };
    });
  }

  const interval = PERIOD_SECONDS[timeframe] ?? 5 * 60;
  const sortedCandles = candles
    .map((candle) => ({ candle, seconds: toUnixSeconds(candle.time) }))
    .filter((item): item is { candle: Candle; seconds: number } => item.seconds != null)
    .sort((a, b) => a.seconds - b.seconds);

  return evidence.map((item) => {
    const eventSeconds = toUnixSeconds(item.evidenceTime);
    let matched: Candle | null = null;
    if (eventSeconds != null) {
      let low = 0;
      let high = sortedCandles.length - 1;
      let candidate = -1;
      while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        if (sortedCandles[middle].seconds <= eventSeconds) {
          candidate = middle;
          low = middle + 1;
        } else {
          high = middle - 1;
        }
      }
      if (candidate >= 0 && eventSeconds - sortedCandles[candidate].seconds < interval) {
        matched = sortedCandles[candidate].candle;
      } else if (
        candidate < 0
        && sortedCandles[0]
        && sortedCandles[0].seconds - eventSeconds < interval
      ) {
        matched = sortedCandles[0].candle;
      }
    }
    return {
      evidence: item,
      chartTime: matched?.time ?? null,
      orderTimeProxy: isOrderTimeProxy(item),
      cashFlowSide: cashFlowSide(item),
    };
  });
}

export function buildEvidenceMarkers(
  links: EvidenceChartLink[],
  selectedEvidenceKey?: string | null,
): CandleMarker[] {
  return links
    .filter((link): link is EvidenceChartLink & { chartTime: number | string } => link.chartTime != null)
    .map((link) => {
      const selected = link.evidence.evidenceKey === selectedEvidenceKey;
      const buy = link.cashFlowSide === 'buy';
      const sell = link.cashFlowSide === 'sell';
      const label = buy ? '买' : sell ? '卖' : '方向未知';
      return {
        id: link.evidence.evidenceKey,
        time: link.chartTime,
        position: sell ? 'aboveBar' : buy ? 'belowBar' : 'inBar',
        shape: link.orderTimeProxy ? 'square' : sell ? 'arrowDown' : buy ? 'arrowUp' : 'circle',
        color: selected ? '#f5d06f' : sell ? '#f85149' : buy ? '#22d3ee' : '#8a8a94',
        text: link.orderTimeProxy ? `代理·${label}` : label,
        size: selected ? 2 : link.orderTimeProxy ? 1.2 : 1.4,
      } satisfies CandleMarker;
    })
    .sort((a, b) => {
      const left = toUnixSeconds(a.time) ?? 0;
      const right = toUnixSeconds(b.time) ?? 0;
      return left - right;
    });
}

export function findEvidenceAtChartTime(
  links: EvidenceChartLink[],
  chartTime: number | string,
): PositionEpisodeEvidenceItem | undefined {
  return links.find((link) => String(link.chartTime) === String(chartTime))?.evidence;
}

/**
 * Builds a conventional EMA series seeded by the first full-period SMA.
 * Candle closes are always the underlying's prices on the review workspace;
 * option fills remain evidence markers and never enter this calculation.
 */
export function buildEmaOverlay(
  candles: Candle[],
  period: number,
  color: string,
  label = `EMA ${period}`,
): MAOverlay {
  if (!Number.isInteger(period) || period <= 0 || candles.length < period) {
    return { period, color, label, data: [] };
  }

  const seed = candles
    .slice(0, period)
    .reduce((total, candle) => total + candle.close, 0) / period;
  const multiplier = 2 / (period + 1);
  let value = seed;
  const data: MAOverlay['data'] = [{
    time: candles[period - 1].time,
    value,
  }];

  for (let index = period; index < candles.length; index += 1) {
    value += (candles[index].close - value) * multiplier;
    data.push({ time: candles[index].time, value });
  }

  return { period, color, label, data };
}
