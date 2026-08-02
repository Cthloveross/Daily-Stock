/** 日内工作台共享格式化工具（组件文件外置，保证 fast-refresh 纯组件导出）。 */

export function formatCompactUsd(value: number | null): string {
  if (value === null) return '—';
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

export function formatSignedPercent(value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

export function formatRatio(value: number | null): string {
  if (value === null) return '—';
  return `${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}×`;
}
