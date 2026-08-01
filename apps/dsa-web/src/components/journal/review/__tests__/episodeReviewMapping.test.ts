import { describe, expect, it } from 'vitest';
import type { Candle } from '../../../charts/CandlestickChart';
import type { PositionEpisodeEvidenceItem } from '../../../../types/journal';
import {
  buildEmaOverlay,
  buildEvidenceMarkers,
  filterCandlesByUsTradingSession,
  findEvidenceAtChartTime,
  isTimeInUsTradingSession,
  mapEvidenceToCandles,
} from '../episodeReviewMapping';

function evidence(
  evidenceKey: string,
  evidenceTime: string,
  kind: 'fill' | 'order',
  eventRole: string,
  allocatedCashFlow = '-250.0000000000',
): PositionEpisodeEvidenceItem {
  return {
    id: Number(evidenceKey.replace(/\D/g, '')) || 1,
    evidenceKey,
    evidenceKind: kind,
    eventRole,
    allocationSequence: 0,
    evidenceTime,
    allocatedQuantity: '1.0000000000',
    allocatedFee: '0.6500000000',
    allocatedCashFlow,
    allocationRatio: '1.0000000000',
    brokerOrderObservationId: kind === 'order' ? 10 : null,
    brokerFillObservationId: kind === 'fill' ? 11 : null,
    allocation: kind === 'order' ? { timingPrecision: 'order_time_proxy' } : {},
    provenance: {},
  };
}

describe('episode review evidence marker mapping', () => {
  it('calculates an EMA from underlying candle closes with an SMA seed', () => {
    const candles = Array.from({ length: 14 }, (_, index) => ({
      time: index + 1,
      open: index + 1,
      high: index + 1,
      low: index + 1,
      close: index + 1,
    }));

    const ema8 = buildEmaOverlay(candles, 8, '#22d3ee');
    const ema13 = buildEmaOverlay(candles, 13, '#f59e0b');

    expect(ema8).toMatchObject({ period: 8, color: '#22d3ee', label: 'EMA 8' });
    expect(ema8.data[0]).toEqual({ time: 8, value: 4.5 });
    expect(ema8.data[1]).toEqual({ time: 9, value: 5.5 });
    expect(ema13.data).toEqual([
      { time: 13, value: 7 },
      { time: 14, value: 8 },
    ]);
    expect(buildEmaOverlay(candles.slice(0, 7), 8, '#22d3ee').data).toEqual([]);
  });

  it('filters US sessions in New York time across daylight-saving offsets', () => {
    const candles: Candle[] = [
      // Summer EDT (UTC-4).
      { time: Date.parse('2026-07-01T08:00:00Z') / 1000, open: 1, high: 1, low: 1, close: 1 },
      { time: Date.parse('2026-07-01T13:29:00Z') / 1000, open: 2, high: 2, low: 2, close: 2 },
      { time: Date.parse('2026-07-01T13:30:00Z') / 1000, open: 3, high: 3, low: 3, close: 3 },
      { time: Date.parse('2026-07-01T19:59:00Z') / 1000, open: 4, high: 4, low: 4, close: 4 },
      { time: Date.parse('2026-07-01T20:00:00Z') / 1000, open: 5, high: 5, low: 5, close: 5 },
      { time: Date.parse('2026-07-02T00:00:00Z') / 1000, open: 6, high: 6, low: 6, close: 6 },
      // Winter EST (UTC-5): 14:30 UTC is still the 09:30 NY open.
      { time: Date.parse('2026-01-05T14:30:00Z') / 1000, open: 7, high: 7, low: 7, close: 7 },
    ];

    expect(filterCandlesByUsTradingSession(candles, 'regular').map((item) => item.close)).toEqual([3, 4, 7]);
    expect(filterCandlesByUsTradingSession(candles, 'extended').map((item) => item.close)).toEqual([1, 2, 3, 4, 5, 7]);
    expect(isTimeInUsTradingSession('2026-07-01T09:30:00-04:00', 'regular')).toBe(true);
    expect(isTimeInUsTradingSession('2026-07-01T16:00:00-04:00', 'regular')).toBe(false);
    expect(isTimeInUsTradingSession('not-a-time', 'extended')).toBe(false);
  });

  it('anchors evidence to its 5-minute bar and keeps fill/proxy semantics distinct', () => {
    const barTime = Date.parse('2026-07-20T14:30:00Z') / 1000;
    const candles: Candle[] = [
      { time: barTime, open: 150, high: 152, low: 149, close: 151 },
      { time: barTime + 300, open: 151, high: 153, low: 150, close: 152 },
    ];
    const links = mapEvidenceToCandles([
      evidence('fill-11', '2026-07-20T14:32:00Z', 'fill', 'open'),
      evidence('order-10', '2026-07-20T14:33:00Z', 'order', 'close', '300.0000000000'),
    ], candles, '5m');

    expect(links.map((link) => link.chartTime)).toEqual([barTime, barTime]);
    expect(links.map((link) => link.orderTimeProxy)).toEqual([false, true]);
    expect(links.map((link) => link.cashFlowSide)).toEqual(['buy', 'sell']);

    const markers = buildEvidenceMarkers(links, 'fill-11');
    expect(markers[0]).toMatchObject({
      id: 'fill-11',
      shape: 'arrowUp',
      text: '买',
      color: '#f5d06f',
    });
    expect(markers[1]).toMatchObject({
      id: 'order-10',
      shape: 'square',
      text: '代理·卖',
    });
    expect(findEvidenceAtChartTime(links, barTime)?.evidenceKey).toBe('fill-11');
  });

  it('leaves evidence outside market coverage unanchored instead of shifting its time', () => {
    const candles: Candle[] = [{
      time: Date.parse('2026-07-20T14:30:00Z') / 1000,
      open: 150,
      high: 152,
      low: 149,
      close: 151,
    }];
    const links = mapEvidenceToCandles([
      evidence('fill-99', '2026-07-19T14:32:00Z', 'fill', 'open'),
    ], candles, '5m');

    expect(links[0].chartTime).toBeNull();
    expect(buildEvidenceMarkers(links)).toEqual([]);
  });

  it('does not forward-map evidence just before the first visible candle', () => {
    const firstRegularBar = Date.parse('2026-07-20T13:30:00Z') / 1000;
    const links = mapEvidenceToCandles([
      evidence('fill-100', '2026-07-20T13:29:59Z', 'fill', 'open'),
    ], [{
      time: firstRegularBar,
      open: 150,
      high: 152,
      low: 149,
      close: 151,
    }], '1m');

    expect(links[0].chartTime).toBeNull();
    expect(buildEvidenceMarkers(links)).toEqual([]);
  });

  it('does not carry evidence backward across a New York session boundary', () => {
    const lastRegularBar = Date.parse('2026-07-20T19:59:00Z') / 1000;
    const links = mapEvidenceToCandles([
      evidence('fill-101', '2026-07-20T20:00:30Z', 'fill', 'close'),
    ], [{
      time: lastRegularBar,
      open: 150,
      high: 152,
      low: 149,
      close: 151,
    }], '5m');

    expect(links[0].chartTime).toBeNull();
    expect(buildEvidenceMarkers(links)).toEqual([]);
  });

  it('maps daily fallback by the evidence New York calendar date', () => {
    const links = mapEvidenceToCandles([
      evidence('fill-12', '2026-07-21T00:30:00Z', 'fill', 'open'),
    ], [
      { time: '2026-07-20', open: 150, high: 152, low: 149, close: 151 },
      { time: '2026-07-21', open: 151, high: 153, low: 150, close: 152 },
    ], 'daily');

    // 00:30 UTC is still July 20 in New York.
    expect(links[0].chartTime).toBe('2026-07-20');
  });
});
