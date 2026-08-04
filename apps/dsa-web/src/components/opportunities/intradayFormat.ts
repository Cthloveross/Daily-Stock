/** 日内工作台共享格式化工具（组件文件外置，保证 fast-refresh 纯组件导出）。 */

export function formatCompactUsd(value: number | null): string {
  if (value === null) return '—';
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

/** 带符号的紧凑美元：+$88K / −$53K / $0；null 显式 —（不以 0 冒充）。 */
export function formatSignedCompactUsd(value: number | null): string {
  if (value === null) return '—';
  const compact = formatCompactUsd(Math.abs(value));
  if (value > 0) return `+${compact}`;
  if (value < 0) return `−${compact}`;
  return compact;
}

export function formatSignedPercent(value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

/** 带符号的 ATR 归一化位移：+0.72 ATR / −0.31 ATR / 0.00 ATR；null 显式 —。 */
export function formatSignedAtr(value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)} ATR`;
}

export function formatRatio(value: number | null): string {
  if (value === null) return '—';
  return `${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}×`;
}
