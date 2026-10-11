import { describe, expect, it } from 'vitest';
import {
  buildOrderAwareEvidenceMarkers,
  consolidateEvidenceLinks,
} from '../evidenceConsolidation';
import type { EvidenceChartLink } from '../episodeReviewMapping';
import type { PositionEpisodeEvidenceItem } from '../../../../types/journal';

interface FillOverrides {
  evidenceKey: string;
  evidenceTime: string;
  chartTime?: number | string | null;
  evidenceKind?: 'fill' | 'order';
  brokerOrderObservationId?: number | null;
  allocatedQuantity?: string | null;
  allocatedCashFlow?: string | null;
  allocatedFee?: string | null;
  timingPrecision?: string;
  eventRole?: string;
  cashFlowSide?: 'buy' | 'sell' | 'unknown';
}

function link(overrides: FillOverrides): EvidenceChartLink {
  const evidence: PositionEpisodeEvidenceItem = {
    id: 1,
    evidenceKey: overrides.evidenceKey,
    evidenceKind: overrides.evidenceKind ?? 'fill',
    eventRole: overrides.eventRole ?? 'open',
    allocationSequence: 0,
    evidenceTime: overrides.evidenceTime,
    allocatedQuantity: overrides.allocatedQuantity === undefined
      ? '1.0000000000'
      : overrides.allocatedQuantity,
    allocatedFee: overrides.allocatedFee === undefined
      ? '0.6500000000'
      : overrides.allocatedFee,
    allocatedCashFlow: overrides.allocatedCashFlow === undefined
      ? '-250.0000000000'
      : overrides.allocatedCashFlow,
    allocationRatio: '1.0000000000',
    brokerOrderObservationId: overrides.brokerOrderObservationId === undefined
      ? 10
      : overrides.brokerOrderObservationId,
    brokerFillObservationId: 99,
    allocation: { timingPrecision: overrides.timingPrecision ?? 'fill_time' },
    provenance: {},
  };
  return {
    evidence,
    chartTime: overrides.chartTime === undefined ? null : overrides.chartTime,
    orderTimeProxy: (overrides.evidenceKind ?? 'fill') === 'order'
      || (overrides.timingPrecision ?? 'fill_time') === 'order_time_proxy',
    cashFlowSide: overrides.cashFlowSide
      ?? ((overrides.allocatedCashFlow ?? '-250').startsWith('-') ? 'buy' : 'sell'),
  };
}

describe('consolidateEvidenceLinks', () => {
  it('merges same-order fills with exact weighted average price and fee sum', () => {
    const groups = consolidateEvidenceLinks([
      link({
        evidenceKey: 'fill-1',
        evidenceTime: '2026-07-20T14:32:00Z',
        allocatedQuantity: '1.0000000000',
        allocatedCashFlow: '-250.0000000000',
        allocatedFee: '0.6500000000',
      }),
      link({
        evidenceKey: 'fill-2',
        evidenceTime: '2026-07-20T14:32:30Z',
        allocatedQuantity: '2.0000000000',
        allocatedCashFlow: '-510.0000000000',
        allocatedFee: '1.3000000000',
      }),
    ], '5m', '100.0000000000');

    expect(groups).toHaveLength(1);
    const [group] = groups;
    expect(group.groupKey).toBe('order:10');
    expect(group.consolidated).toBe(true);
    expect(group.orderTimeProxy).toBe(false);
    expect(group.cashFlowSide).toBe('buy');
    // 760 / (3 × 100) = 2.533333… → half-up 4 位 = 2.5333
    expect(group.aggregate?.totalQuantity).toBe('3');
    expect(group.aggregate?.weightedAveragePrice).toBe('2.5333');
    expect(group.aggregate?.totalFee).toBe('1.95');
    expect(group.aggregate?.feeComplete).toBe(true);
    expect(group.aggregate?.firstEvidenceTime).toBe('2026-07-20T14:32:00Z');
    expect(group.aggregate?.lastEvidenceTime).toBe('2026-07-20T14:32:30Z');
  });

  it('rounds the weighted average half-up at the fourth decimal place', () => {
    const groups = consolidateEvidenceLinks([
      link({
        evidenceKey: 'fill-1',
        evidenceTime: '2026-07-20T14:32:00Z',
        allocatedQuantity: '3.0000000000',
        allocatedCashFlow: '-100.0000000000',
      }),
      link({
        evidenceKey: 'fill-2',
        evidenceTime: '2026-07-20T14:32:10Z',
        allocatedQuantity: '3.0000000000',
        allocatedCashFlow: '-100.0000000000',
      }),
    ], '5m', '100.0000000000');

    // 200 / (6 × 100) = 0.33333… → half-up 第 4 位 = 0.3333
    expect(groups[0].aggregate?.weightedAveragePrice).toBe('0.3333');
  });

  it('does not pass an incomplete fee sum off as a total', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', allocatedFee: '0.6500000000' }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:32:30Z', allocatedFee: null }),
    ], '5m', '100.0000000000');

    expect(groups[0].aggregate?.feeComplete).toBe(false);
    expect(groups[0].aggregate?.totalFee).toBeNull();
  });

  it('leaves the weighted average unknown when a member lacks quantity or cash flow', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z' }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:32:30Z', allocatedCashFlow: null }),
    ], '5m', '100.0000000000');

    expect(groups[0].aggregate?.weightedAveragePrice).toBeNull();
    expect(groups[0].aggregate?.totalQuantity).toBe('2');
  });

  it('keeps single-fill orders and order-proxy evidence as unconsolidated rows', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', brokerOrderObservationId: 10 }),
      link({
        evidenceKey: 'order-11',
        evidenceTime: '2026-07-20T14:33:00Z',
        evidenceKind: 'order',
        brokerOrderObservationId: 11,
      }),
      link({ evidenceKey: 'fill-3', evidenceTime: '2026-07-20T14:34:00Z', brokerOrderObservationId: null }),
    ], '5m', '100.0000000000');

    expect(groups).toHaveLength(3);
    expect(groups.every((group) => !group.consolidated)).toBe(true);
    expect(groups.map((group) => group.groupKey)).toEqual(['order:10', 'order-11', 'fill-3']);
    expect(groups[0].aggregate).toBeNull();
  });

  it('keeps proxy labeling when any member fill is order-time proxied', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z' }),
      link({
        evidenceKey: 'fill-2',
        evidenceTime: '2026-07-20T14:32:30Z',
        timingPrecision: 'order_time_proxy',
      }),
    ], '5m', '100.0000000000');

    expect(groups[0].consolidated).toBe(true);
    expect(groups[0].orderTimeProxy).toBe(true);
  });

  it('collects distinct event roles and downgrades mixed sides to unknown', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', eventRole: 'open', allocatedCashFlow: '-250.0000000000' }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:32:30Z', eventRole: 'add', allocatedCashFlow: '300.0000000000' }),
    ], '5m', '100.0000000000');

    expect(groups[0].eventRoles).toEqual(['open', 'add']);
    expect(groups[0].cashFlowSide).toBe('unknown');
  });
});

describe('buildOrderAwareEvidenceMarkers', () => {
  const bar = 1_753_021_800; // aligned 5m bar start seconds

  it('renders one marker per order anchored at the first mapped fill', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', chartTime: bar }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:32:30Z', chartTime: bar }),
    ], '5m', '100.0000000000');

    const markers = buildOrderAwareEvidenceMarkers(groups, null);

    expect(markers).toHaveLength(1);
    expect(markers[0].id).toBe('order:10');
    expect(markers[0].time).toBe(bar);
    expect(markers[0].text).toBe('买×2');
  });

  it('keeps adjacent-bar fills merged but splits fills more than one bar apart', () => {
    const adjacent = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', chartTime: bar }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:37:00Z', chartTime: bar + 300 }),
    ], '5m', '100.0000000000');
    expect(adjacent[0].chartDispersed).toBe(false);
    expect(buildOrderAwareEvidenceMarkers(adjacent, null)).toHaveLength(1);

    const dispersed = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', chartTime: bar }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:47:00Z', chartTime: bar + 900 }),
    ], '5m', '100.0000000000');
    expect(dispersed[0].chartDispersed).toBe(true);
    const markers = buildOrderAwareEvidenceMarkers(dispersed, null);
    expect(markers).toHaveLength(2);
    expect(markers.map((marker) => marker.id)).toEqual(['fill-1', 'fill-2']);
  });

  it('splits daily-bar groups whose fills land on different bars', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', chartTime: '2026-07-20' }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-21T14:32:00Z', chartTime: '2026-07-21' }),
    ], 'daily', '100.0000000000');

    expect(groups[0].chartDispersed).toBe(true);
    expect(buildOrderAwareEvidenceMarkers(groups, null)).toHaveLength(2);
  });

  it('keeps proxy labeling and highlights the merged marker when a member is selected', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', chartTime: bar, timingPrecision: 'order_time_proxy' }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:32:30Z', chartTime: bar }),
    ], '5m', '100.0000000000');

    const markers = buildOrderAwareEvidenceMarkers(groups, 'fill-2');

    expect(markers).toHaveLength(1);
    expect(markers[0].shape).toBe('square');
    expect(markers[0].text).toBe('代理·买×2');
    expect(markers[0].color).toBe('#f5d06f');
  });

  it('skips merged markers for fully unmapped groups instead of inventing a position', () => {
    const groups = consolidateEvidenceLinks([
      link({ evidenceKey: 'fill-1', evidenceTime: '2026-07-20T14:32:00Z', chartTime: null }),
      link({ evidenceKey: 'fill-2', evidenceTime: '2026-07-20T14:32:30Z', chartTime: null }),
    ], '5m', '100.0000000000');

    expect(groups[0].chartTime).toBeNull();
    expect(buildOrderAwareEvidenceMarkers(groups, null)).toHaveLength(0);
  });
});
