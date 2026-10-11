/**
 * Shared read-only display helpers for PositionEpisode surfaces.
 *
 * These render backend-persisted Decimal strings verbatim (grouping and sign
 * only); the browser never re-aggregates accounting results.
 */

function normaliseDecimal(value: string): { sign: string; whole: string; fraction: string } {
  const raw = value.trim();
  const sign = raw.startsWith('-') ? '−' : raw.startsWith('+') ? '+' : '';
  const unsigned = raw.replace(/^[+-]/, '');
  const [whole = '0', fraction = ''] = unsigned.split('.', 2);
  return { sign, whole: whole.replace(/\B(?=(\d{3})+(?!\d))/g, ','), fraction };
}

export function formatDecimalDisplay(value?: string | null, money = false): string {
  if (value == null || !value.trim()) return '—';
  const { sign, whole, fraction } = normaliseDecimal(value);
  const significantFraction = fraction.replace(/0+$/, '');
  const displayFraction = money
    ? significantFraction.padEnd(2, '0')
    : significantFraction;
  const body = `${whole}${displayFraction ? `.${displayFraction}` : ''}`;
  return money ? `${sign}$${body}` : `${sign}${body}`;
}

export function formatCurrencyAmount(currency: string, value?: string | null): string {
  const amount = formatDecimalDisplay(value, false);
  return amount === '—' ? '—' : `${currency} ${amount}`;
}

export function decimalTone(value?: string | null): string {
  if (!value) return 'text-text-3';
  if (value.trim().startsWith('-')) return 'text-down-strong';
  if (/^\+?0(?:\.0+)?$/.test(value.trim())) return 'text-text-2';
  return 'text-up-strong';
}

export function formatEt(value?: string | null, includeSeconds = false): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...(includeSeconds ? { second: '2-digit' as const } : {}),
    hour12: false,
  }).format(parsed);
}

export function formatHold(seconds?: number | null): string {
  if (seconds == null) return '—';
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}
