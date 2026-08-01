import { describe, expect, it } from 'vitest';
import {
  hasSameOrderedPremarketUniverse,
  MAX_PREMARKET_UNIVERSE_SIZE,
  normalizePremarketUniverse,
  preparePremarketUniverse,
} from '../premarketUniverse';

describe('premarket universe helpers', () => {
  it('normalizes, preserves order, de-duplicates, and caps the official pool at 20', () => {
    const symbols = [
      ' nvda ',
      '$AAPL',
      'US.MSFT',
      'NVDA',
      ...Array.from({ length: 26 }, (_, index) => String.fromCharCode(65 + index)),
    ];

    const prepared = preparePremarketUniverse(symbols);
    const normalized = normalizePremarketUniverse(symbols);

    expect(normalized).toHaveLength(MAX_PREMARKET_UNIVERSE_SIZE);
    expect(normalized.slice(0, 4)).toEqual(['NVDA', 'AAPL', 'MSFT', 'A']);
    expect(normalized.at(-1)).toBe('Q');
    expect(prepared.overflowCount).toBe(9);
  });

  it('filters unsupported and non-US symbols before saving the official pool', () => {
    expect(preparePremarketUniverse([
      'NVDA',
      'HK.00700',
      '600519',
      'TOOLONG',
      'BRK.B',
      'BRK-B',
    ])).toEqual({
      symbols: ['NVDA', 'BRK.B', 'BRK-B'],
      unsupportedSymbols: ['HK.00700', '600519', 'TOOLONG'],
      overflowCount: 0,
    });
  });

  it('treats ordering as part of the official research-pool version', () => {
    expect(hasSameOrderedPremarketUniverse(
      [' nvda', '$AAPL', 'NVDA'],
      ['NVDA', 'AAPL'],
    )).toBe(true);
    expect(hasSameOrderedPremarketUniverse(
      ['AAPL', 'NVDA'],
      ['NVDA', 'AAPL'],
    )).toBe(false);
  });
});
