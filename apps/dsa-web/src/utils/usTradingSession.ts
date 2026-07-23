export type UsTradingSession = 'regular' | 'extended';

type Timestamped = {
  time: number | string;
};

const NEW_YORK_CLOCK = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

const US_SESSION_MINUTES: Record<UsTradingSession, { start: number; end: number }> = {
  regular: { start: 9 * 60 + 30, end: 16 * 60 },
  extended: { start: 4 * 60, end: 20 * 60 },
};

function toUnixSeconds(value: number | string): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
}

function newYorkMinute(value: number | string): number | null {
  const seconds = toUnixSeconds(value);
  if (seconds == null) return null;
  const parts = NEW_YORK_CLOCK.formatToParts(new Date(seconds * 1000));
  const hour = Number(parts.find((item) => item.type === 'hour')?.value);
  const minute = Number(parts.find((item) => item.type === 'minute')?.value);
  return Number.isInteger(hour) && Number.isInteger(minute) ? hour * 60 + minute : null;
}

/**
 * Tests an intraday timestamp against the US equity session in New York time.
 * `Intl` owns the UTC offset so daylight-saving transitions are not hard-coded.
 * Session end is exclusive because candle timestamps identify the bar start.
 */
export function isTimeInUsTradingSession(
  value: number | string,
  session: UsTradingSession,
): boolean {
  const minute = newYorkMinute(value);
  if (minute == null) return false;
  const boundary = US_SESSION_MINUTES[session];
  return minute >= boundary.start && minute < boundary.end;
}

export function filterByUsTradingSession<T extends Timestamped>(
  items: T[],
  session: UsTradingSession,
): T[] {
  return items.filter((item) => isTimeInUsTradingSession(item.time, session));
}
