import { describe, expect, it } from 'vitest';
import { formatChartTimeInTimeZone } from '../chartTimeFormatting';

describe('CandlestickChart timezone formatting', () => {
  it('renders UTC timestamps as New York ET with daylight-saving handled by IANA data', () => {
    expect(formatChartTimeInTimeZone(
      Date.parse('2026-07-01T13:54:00Z') / 1000,
      'America/New_York',
      'ET',
    )).toBe('2026-07-01 09:54 ET');

    expect(formatChartTimeInTimeZone(
      Date.parse('2026-01-05T14:54:00Z') / 1000,
      'America/New_York',
      'ET',
    )).toBe('2026-01-05 09:54 ET');
  });

  it('keeps date-only candles on their calendar date', () => {
    expect(formatChartTimeInTimeZone(
      '2026-07-01',
      'America/New_York',
      'ET',
    )).toBe('2026-07-01');
  });
});
