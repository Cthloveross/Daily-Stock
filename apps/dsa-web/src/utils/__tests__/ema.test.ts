import { describe, expect, it } from 'vitest';
import { computeSmaSeededEmaSeries } from '../ema';

function bars(closes: number[]): Array<{ time: number; close: number }> {
  return closes.map((close, index) => ({ time: index + 1, close }));
}

describe('computeSmaSeededEmaSeries', () => {
  it('matches a hand-computed small sample (SMA seed, then recursive EMA)', () => {
    // period 3 over closes [1..5]: seed = (1+2+3)/3 = 2 at bar 3, alpha = 0.5
    // bar 4: 2 + (4-2)*0.5 = 3; bar 5: 3 + (5-3)*0.5 = 4
    expect(computeSmaSeededEmaSeries(bars([1, 2, 3, 4, 5]), 3)).toEqual([
      { time: 3, value: 2 },
      { time: 4, value: 3 },
      { time: 5, value: 4 },
    ]);
  });

  it('fails closed with no values until a full seed window exists', () => {
    expect(computeSmaSeededEmaSeries(bars([1, 2, 3, 4, 5, 6, 7]), 8)).toEqual([]);
    expect(computeSmaSeededEmaSeries([], 8)).toEqual([]);
  });

  it('rejects non-positive or fractional periods', () => {
    const sample = bars([1, 2, 3]);
    expect(computeSmaSeededEmaSeries(sample, 0)).toEqual([]);
    expect(computeSmaSeededEmaSeries(sample, -3)).toEqual([]);
    expect(computeSmaSeededEmaSeries(sample, 2.5)).toEqual([]);
  });

  it('agrees with the backend _ema_last reference on identical input', () => {
    // Fixture generated from src/opportunities/engine.py:_ema_last semantics
    // (statistics.fmean seed + alpha recursion) over these 12 closes.
    const closes = [
      412.31, 415.08, 413.66, 417.92, 421.45, 419.03,
      423.5, 425.11, 424.06, 428.73, 427.18, 431.4,
    ];
    const series = computeSmaSeededEmaSeries(bars(closes), 8);
    expect(series).toHaveLength(5);
    expect(series[0].time).toBe(8);
    expect(series[0].value).toBeCloseTo(418.5075, 10);
    expect(series[series.length - 1].value).toBeCloseTo(424.82622275567746, 10);
    // 13-period seed needs 13 bars; 12 closes must yield nothing.
    expect(computeSmaSeededEmaSeries(bars(closes), 13)).toEqual([]);
  });

  it('emits the seed on the period-th bar, not the first bar', () => {
    const series = computeSmaSeededEmaSeries(bars([10, 20, 30, 40]), 3);
    expect(series[0]).toEqual({ time: 3, value: 20 });
  });
});
