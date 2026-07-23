const TRADING_DAYS_PER_YEAR = 252;

export const IMPLIED_MOVE_PROBABILITIES = {
  oneSigma: {
    insidePercent: 68.27,
    lowerTailPercent: 15.865,
    upperTailPercent: 15.865,
  },
  twoSigma: {
    insidePercent: 95.45,
    lowerTailPercent: 2.275,
    upperTailPercent: 2.275,
  },
} as const;

export interface ImpliedMoveRange {
  sigmaMultiple: 1 | 2;
  lowerPrice: number;
  upperPrice: number;
  /** Conventional quoted move: spot * sigmaMultiple * horizon volatility. */
  movePoints: number;
  movePercent: number;
  /** Exact distances from spot to the lognormal quantile bounds. */
  downsideMovePoints: number;
  upsideMovePoints: number;
  downsideMovePercent: number;
  upsideMovePercent: number;
  insideProbabilityPercent: number;
  lowerTailProbabilityPercent: number;
  upperTailProbabilityPercent: number;
}

export interface ImpliedMoveResult {
  spot: number;
  annualizedIvPercent: number;
  horizonTradingDays: number;
  tradingDaysPerYear: typeof TRADING_DAYS_PER_YEAR;
  horizonVolatility: number;
  oneSigma: ImpliedMoveRange;
  twoSigma: ImpliedMoveRange;
}

const isPositiveFinite = (value: number): boolean => Number.isFinite(value) && value > 0;

/**
 * Converts annualized IV into model-implied price intervals.
 *
 * Assumption: under a zero-drift forward approximation, log returns follow
 * N(-0.5 * variance, variance). The mean adjustment keeps E[S_T] equal to spot.
 * These are risk-neutral/model-distribution intervals derived from IV, not
 * forecasts, directional signals, or empirical probabilities of winning.
 */
export const calculateImpliedMove = (
  spot: number,
  annualizedIvPercent: number,
  horizonTradingDays: number,
): ImpliedMoveResult | null => {
  if (
    !isPositiveFinite(spot) ||
    !isPositiveFinite(annualizedIvPercent) ||
    !isPositiveFinite(horizonTradingDays)
  ) {
    return null;
  }

  const horizonVolatility =
    (annualizedIvPercent / 100) * Math.sqrt(horizonTradingDays / TRADING_DAYS_PER_YEAR);
  const variance = horizonVolatility ** 2;
  const logReturnMean = -0.5 * variance;

  const buildRange = (sigmaMultiple: 1 | 2): ImpliedMoveRange => {
    const lowerPrice = spot * Math.exp(logReturnMean - sigmaMultiple * horizonVolatility);
    const upperPrice = spot * Math.exp(logReturnMean + sigmaMultiple * horizonVolatility);
    const movePoints = spot * sigmaMultiple * horizonVolatility;
    const movePercent = sigmaMultiple * horizonVolatility * 100;
    const probabilities =
      sigmaMultiple === 1
        ? IMPLIED_MOVE_PROBABILITIES.oneSigma
        : IMPLIED_MOVE_PROBABILITIES.twoSigma;

    return {
      sigmaMultiple,
      lowerPrice,
      upperPrice,
      movePoints,
      movePercent,
      downsideMovePoints: spot - lowerPrice,
      upsideMovePoints: upperPrice - spot,
      downsideMovePercent: ((spot - lowerPrice) / spot) * 100,
      upsideMovePercent: ((upperPrice - spot) / spot) * 100,
      insideProbabilityPercent: probabilities.insidePercent,
      lowerTailProbabilityPercent: probabilities.lowerTailPercent,
      upperTailProbabilityPercent: probabilities.upperTailPercent,
    };
  };

  const result: ImpliedMoveResult = {
    spot,
    annualizedIvPercent,
    horizonTradingDays,
    tradingDaysPerYear: TRADING_DAYS_PER_YEAR,
    horizonVolatility,
    oneSigma: buildRange(1),
    twoSigma: buildRange(2),
  };

  const numericValues = [
    result.horizonVolatility,
    ...Object.values(result.oneSigma).filter((value): value is number => typeof value === 'number'),
    ...Object.values(result.twoSigma).filter((value): value is number => typeof value === 'number'),
  ];
  return numericValues.every(Number.isFinite) ? result : null;
};
