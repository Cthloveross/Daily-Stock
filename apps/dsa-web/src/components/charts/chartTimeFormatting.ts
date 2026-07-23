import { TickMarkType, type Time } from 'lightweight-charts';

interface ChartDateParts {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
  second: string;
}

type ChartTimeValue = Time | number | string;

const chartTimeFormatters = new Map<string, Intl.DateTimeFormat>();

function dateOnlyTime(value: ChartTimeValue): string | null {
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (typeof value === 'object' && value != null && 'year' in value && 'month' in value && 'day' in value) {
    const year = String(value.year).padStart(4, '0');
    const month = String(value.month).padStart(2, '0');
    const day = String(value.day).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }
  return null;
}

function chartTimeDate(value: ChartTimeValue): Date | null {
  if (typeof value === 'number') {
    const date = new Date(value * 1000);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  if (typeof value === 'string') {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  return null;
}

function chartDateParts(value: ChartTimeValue, timeZone: string): ChartDateParts | null {
  const date = chartTimeDate(value);
  if (!date) return null;
  let formatter = chartTimeFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    });
    chartTimeFormatters.set(timeZone, formatter);
  }
  const parts = formatter.formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? '';
  return {
    year: part('year'),
    month: part('month'),
    day: part('day'),
    hour: part('hour'),
    minute: part('minute'),
    second: part('second'),
  };
}

/** Format a crosshair timestamp in an explicit IANA timezone. */
export function formatChartTimeInTimeZone(
  value: ChartTimeValue,
  timeZone: string,
  timeZoneLabel = timeZone,
): string {
  const dateOnly = dateOnlyTime(value);
  if (dateOnly) return dateOnly;
  const parts = chartDateParts(value, timeZone);
  if (!parts) return String(value);
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} ${timeZoneLabel}`;
}

export function formatChartTickInTimeZone(
  value: Time,
  tickMarkType: TickMarkType,
  timeZone: string,
): string | null {
  const dateOnly = dateOnlyTime(value);
  if (dateOnly) {
    const [year, month, day] = dateOnly.split('-');
    if (tickMarkType === TickMarkType.Year) return year;
    if (tickMarkType === TickMarkType.Month) return `${year}-${month}`;
    return `${month}/${day}`;
  }
  const parts = chartDateParts(value, timeZone);
  if (!parts) return null;
  if (tickMarkType === TickMarkType.Year) return parts.year;
  if (tickMarkType === TickMarkType.Month) return `${parts.year}-${parts.month}`;
  if (tickMarkType === TickMarkType.DayOfMonth) return `${parts.month}/${parts.day}`;
  if (tickMarkType === TickMarkType.TimeWithSeconds) return `${parts.hour}:${parts.minute}:${parts.second}`;
  return `${parts.hour}:${parts.minute}`;
}
