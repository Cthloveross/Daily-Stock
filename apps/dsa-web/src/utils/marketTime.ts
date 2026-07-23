const EXPLICIT_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;

export function parseApiTimestamp(value: Date | string | null | undefined): Date | null {
  if (!value) return null;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  const trimmed = value.trim();
  if (!trimmed) return null;
  // Backend timestamps without an offset are UTC-by-contract. Explicit
  // offsets from Moomoo and other APIs must remain untouched.
  const parsed = new Date(EXPLICIT_OFFSET.test(trimmed) ? trimmed : `${trimmed}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatEtClock(
  value: Date | string | null | undefined,
  includeSeconds = false,
): string {
  const parsed = parseApiTimestamp(value);
  if (!parsed) return '';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    ...(includeSeconds ? { second: '2-digit' } : {}),
    hourCycle: 'h23',
  }).format(parsed);
}
