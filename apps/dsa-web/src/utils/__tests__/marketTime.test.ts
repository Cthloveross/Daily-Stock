import { describe, expect, it } from 'vitest';
import { formatEtClock, parseApiTimestamp } from '../marketTime';

describe('marketTime', () => {
  it('formats an offset-aware API timestamp in New York time', () => {
    expect(formatEtClock('2026-07-23T05:15:30+00:00')).toBe('01:15');
    expect(formatEtClock('2026-07-23T05:15:30+00:00', true)).toBe('01:15:30');
  });

  it('treats a legacy offset-free API timestamp as UTC', () => {
    expect(parseApiTimestamp('2026-07-23T05:15:30')?.toISOString()).toBe(
      '2026-07-23T05:15:30.000Z',
    );
  });
});
