import { describe, expect, it } from 'vitest';

import { calculateImpliedMove, IMPLIED_MOVE_PROBABILITIES } from '../impliedMove';

describe('calculateImpliedMove', () => {
  it('uses the zero-forward lognormal quantiles and conventional quoted move', () => {
    const result = calculateImpliedMove(100, 25, 1);

    expect(result).not.toBeNull();
    if (!result) return;

    const sigma = 0.25 * Math.sqrt(1 / 252);
    const logMean = -0.5 * sigma ** 2;

    expect(result.horizonVolatility).toBeCloseTo(sigma, 12);
    expect(result.oneSigma.lowerPrice).toBeCloseTo(100 * Math.exp(logMean - sigma), 12);
    expect(result.oneSigma.upperPrice).toBeCloseTo(100 * Math.exp(logMean + sigma), 12);
    expect(result.oneSigma.movePoints).toBeCloseTo(100 * sigma, 12);
    expect(result.oneSigma.movePercent).toBeCloseTo(sigma * 100, 12);
    expect(result.twoSigma.lowerPrice).toBeCloseTo(100 * Math.exp(logMean - 2 * sigma), 12);
    expect(result.twoSigma.upperPrice).toBeCloseTo(100 * Math.exp(logMean + 2 * sigma), 12);
    expect(result.twoSigma.movePoints).toBeCloseTo(200 * sigma, 12);
  });

  it('returns the requested normal-distribution probability constants', () => {
    const result = calculateImpliedMove(100, 30, 5);

    expect(result?.oneSigma).toMatchObject({
      insideProbabilityPercent: 68.27,
      lowerTailProbabilityPercent: 15.865,
      upperTailProbabilityPercent: 15.865,
    });
    expect(result?.twoSigma).toMatchObject({
      insideProbabilityPercent: 95.45,
      lowerTailProbabilityPercent: 2.275,
      upperTailProbabilityPercent: 2.275,
    });
    expect(IMPLIED_MOVE_PROBABILITIES.oneSigma.insidePercent).toBe(68.27);
    expect(IMPLIED_MOVE_PROBABILITIES.twoSigma.upperTailPercent).toBe(2.275);
  });

  it.each([
    [0, 30, 1],
    [-100, 30, 1],
    [Number.NaN, 30, 1],
    [Number.POSITIVE_INFINITY, 30, 1],
    [100, 0, 1],
    [100, -30, 1],
    [100, Number.NaN, 1],
    [100, Number.POSITIVE_INFINITY, 1],
    [100, 30, 0],
    [100, 30, -1],
    [100, 30, Number.NaN],
    [100, 30, Number.POSITIVE_INFINITY],
  ])('returns null for invalid inputs (%s, %s, %s)', (spot, iv, horizon) => {
    expect(calculateImpliedMove(spot, iv, horizon)).toBeNull();
  });

  it('accepts a positive fractional trading-day horizon', () => {
    const result = calculateImpliedMove(100, 40, 0.5);

    expect(result?.horizonVolatility).toBeCloseTo(0.4 * Math.sqrt(0.5 / 252), 12);
  });

  it('widens the ranges monotonically for a higher practical IV or longer horizon', () => {
    const lowIv = calculateImpliedMove(100, 20, 1);
    const highIv = calculateImpliedMove(100, 40, 1);
    const longHorizon = calculateImpliedMove(100, 20, 5);

    expect(lowIv).not.toBeNull();
    expect(highIv).not.toBeNull();
    expect(longHorizon).not.toBeNull();
    if (!lowIv || !highIv || !longHorizon) return;

    const width = (result: NonNullable<typeof lowIv>) =>
      result.oneSigma.upperPrice - result.oneSigma.lowerPrice;

    expect(width(highIv)).toBeGreaterThan(width(lowIv));
    expect(width(longHorizon)).toBeGreaterThan(width(lowIv));
    expect(highIv.oneSigma.movePoints).toBeGreaterThan(lowIv.oneSigma.movePoints);
    expect(longHorizon.oneSigma.movePoints).toBeGreaterThan(lowIv.oneSigma.movePoints);
  });

  it('scales prices and point moves linearly with spot while preserving percentages', () => {
    const base = calculateImpliedMove(100, 35, 2);
    const doubled = calculateImpliedMove(200, 35, 2);

    expect(base).not.toBeNull();
    expect(doubled).not.toBeNull();
    if (!base || !doubled) return;

    expect(doubled.oneSigma.lowerPrice).toBeCloseTo(base.oneSigma.lowerPrice * 2, 12);
    expect(doubled.oneSigma.upperPrice).toBeCloseTo(base.oneSigma.upperPrice * 2, 12);
    expect(doubled.oneSigma.movePoints).toBeCloseTo(base.oneSigma.movePoints * 2, 12);
    expect(doubled.oneSigma.movePercent).toBeCloseTo(base.oneSigma.movePercent, 12);
  });
});
