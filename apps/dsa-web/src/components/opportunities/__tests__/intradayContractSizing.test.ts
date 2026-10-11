import { describe, expect, it } from 'vitest';
import {
  DEFAULT_STANDARD_SIZE_USD,
  ROUND_TURN_FEE_PER_CONTRACT_USD,
  classifyContractPrice,
  computeContractSizing,
} from '../intradayContractSizing';

describe('classifyContractPrice', () => {
  it('marks the $2-8 sweet spot as highlighted with its sample sizes', () => {
    for (const price of [2, 3.5, 4, 7.99]) {
      const band = classifyContractPrice(price);
      expect(band?.band).toBe('sweet_2_8');
      expect(band?.highlighted).toBe(true);
      expect(band?.warned).toBe(false);
      expect(band?.evidence).toContain('n=544');
      expect(band?.evidence).toContain('n=208');
    }
  });

  it('warns on <$1 with the fee-drag evidence and its sample size', () => {
    const band = classifyContractPrice(0.85);
    expect(band?.band).toBe('below_1');
    expect(band?.warned).toBe(true);
    expect(band?.highlighted).toBe(false);
    expect(band?.evidence).toContain('−14.25%');
    expect(band?.evidence).toContain('n=72');
    expect(band?.evidence).toContain('5.56%');
  });

  it('keeps $1-2 and >$8 neither highlighted nor greyed, each with n', () => {
    const low = classifyContractPrice(1.5);
    expect(low?.band).toBe('band_1_2');
    expect(low?.highlighted).toBe(false);
    expect(low?.warned).toBe(false);
    expect(low?.evidence).toContain('n=365');

    const high = classifyContractPrice(12);
    expect(high?.band).toBe('above_8');
    expect(high?.evidence).toContain('n=218');
  });

  it('returns null for a missing or non-positive price（不以 0 冒充）', () => {
    expect(classifyContractPrice(null)).toBeNull();
    expect(classifyContractPrice(undefined)).toBeNull();
    expect(classifyContractPrice(0)).toBeNull();
    expect(classifyContractPrice(Number.NaN)).toBeNull();
  });
});

describe('computeContractSizing', () => {
  it('computes 张数 = floor(size / (price×100)) and 手续费 = 张数 × $3.29', () => {
    // $3,000 / ($0.50 × 100) = 60 张 → 便宜合约的张数陷阱。
    const cheap = computeContractSizing(0.5, DEFAULT_STANDARD_SIZE_USD);
    expect(cheap?.contracts).toBe(60);
    expect(cheap?.premiumUsd).toBeCloseTo(3000, 6);
    expect(cheap?.feeUsd).toBeCloseTo(60 * ROUND_TURN_FEE_PER_CONTRACT_USD, 6);
    expect(cheap?.feePercentOfPremium).toBeCloseTo(6.58, 2);

    // $3,000 / ($4.00 × 100) = 7 张（余数留着，不凑整）。
    const sweet = computeContractSizing(4, DEFAULT_STANDARD_SIZE_USD);
    expect(sweet?.contracts).toBe(7);
    expect(sweet?.premiumUsd).toBeCloseTo(2800, 6);
    expect(sweet?.feeUsd).toBeCloseTo(23.03, 2);
    expect(sweet?.feePercentOfPremium).toBeCloseTo(0.8225, 3);
  });

  it('returns 0 张 rather than a fraction when one contract is unaffordable', () => {
    const sizing = computeContractSizing(50, 3000);
    expect(sizing?.contracts).toBe(0);
    expect(sizing?.feeUsd).toBe(0);
    expect(sizing?.feePercentOfPremium).toBeNull();
  });

  it('returns null on a missing price or a non-positive size', () => {
    expect(computeContractSizing(null, 3000)).toBeNull();
    expect(computeContractSizing(3, 0)).toBeNull();
    expect(computeContractSizing(3, Number.NaN)).toBeNull();
  });

  it('shows the cheap-contract trap: same $ buys ~8.6x the fee drag', () => {
    const cheap = computeContractSizing(0.5, 3000);
    const sweet = computeContractSizing(4, 3000);
    expect(cheap!.feePercentOfPremium!).toBeGreaterThan(sweet!.feePercentOfPremium! * 5);
  });
});
