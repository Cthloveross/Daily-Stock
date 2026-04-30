import type React from 'react';
import { cn } from '../../utils/cn';

export interface ContributionItem {
  label: string;
  value: number;
  description?: string;
  /**
   * `no_data`: the upstream data feed (Finnhub / yfinance / etc.) didn't provide
   *   anything computable — render `—` instead of `0` and surface an
   *   informative caption. `computed` (default): value is real, render as-is.
   */
  status?: 'no_data' | 'computed';
  /** Optional caption shown in place of description when `status === 'no_data'`. */
  noDataHint?: string;
}

export interface ContributionListProps {
  items: ContributionItem[];
  maxAbsValue?: number;
  sortByAbs?: boolean;
  className?: string;
}

const formatSignedInt = (n: number) => {
  if (n > 0) return `+${n.toFixed(0)}`;
  if (n < 0) return `\u2212${Math.abs(n).toFixed(0)}`;
  return '0';
};

export const ContributionList: React.FC<ContributionListProps> = ({
  items,
  maxAbsValue,
  sortByAbs = true,
  className,
}) => {
  const effectiveMax = Math.max(
    1,
    maxAbsValue ?? Math.max(...items.map((it) => Math.abs(it.value)), 1),
  );

  const ordered = sortByAbs
    ? [...items].sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    : items;

  return (
    <div className={cn('space-y-1', className)}>
      {ordered.map((it) => {
        const noData = it.status === 'no_data';
        const pct = Math.min(100, (Math.abs(it.value) / effectiveMax) * 50);
        const zero = it.value === 0;
        const isPositive = it.value > 0;
        const color = zero
          ? ''
          : isPositive
            ? 'bg-up-strong'
            : 'bg-down-strong';
        const displayedValue = noData ? '—' : formatSignedInt(it.value);
        const caption = noData ? (it.noDataHint ?? '数据源未配置 / 暂无数据') : it.description;
        return (
          <div key={it.label} className="flex h-7 items-center gap-3 py-1.5">
            <div className="w-24 shrink-0 text-body text-text-1">{it.label}</div>
            <div className="relative h-1.5 flex-1 rounded-ds-sm bg-bg-2">
              <span className="absolute left-1/2 top-0 h-full w-px bg-[color:var(--border-subtle)]" />
              {!zero && !noData && (
                <span
                  className={cn('absolute top-0 h-full rounded-ds-sm', color)}
                  style={{
                    left: isPositive ? '50%' : `calc(50% - ${pct}%)`,
                    width: `${pct}%`,
                  }}
                />
              )}
            </div>
            <div
              className={cn(
                'w-10 shrink-0 text-right font-mono text-mono-sm tabular-nums',
                zero || noData ? 'text-text-3' : 'text-text-1',
              )}
              title={noData ? '数据源未返回数据' : undefined}
            >
              {displayedValue}
            </div>
            {caption && (
              <div
                className={cn(
                  'hidden lg:block w-56 shrink-0 text-caption',
                  noData ? 'italic text-text-3' : 'text-text-3',
                )}
              >
                {caption}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default ContributionList;
