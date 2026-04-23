import type React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../utils/cn';

export interface StatBarItem {
  label: string;
  value: string;
  delta?: string;
  deltaPositive?: boolean;
  sub?: string;
  icon?: LucideIcon;
}

export interface StatBarProps {
  items: StatBarItem[];
  separator?: 'line' | 'space';
  className?: string;
}

export const StatBar: React.FC<StatBarProps> = ({
  items,
  separator = 'line',
  className,
}) => (
  <div
    className={cn(
      'flex items-stretch rounded-ds-md border border-subtle bg-bg-1 px-4',
      'h-12',
      className,
    )}
  >
    {items.map((it, idx) => {
      const first = idx === 0;
      const Icon = it.icon;
      return (
        <div
          key={`${it.label}-${idx}`}
          className={cn(
            'flex flex-col justify-center flex-1 pr-6',
            !first && 'pl-6',
            !first && separator === 'line' && 'border-l border-subtle',
          )}
        >
          <div className="flex items-center gap-1 text-label uppercase text-text-3">
            {Icon && <Icon size={11} strokeWidth={1.5} />}
            <span>{it.label}</span>
          </div>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className="font-mono text-mono-md text-text-1 tabular-nums">{it.value}</span>
            {it.delta && (
              <span
                className={cn(
                  'font-mono text-mono-sm tabular-nums',
                  it.deltaPositive === true && 'text-up-strong',
                  it.deltaPositive === false && 'text-down-strong',
                  it.deltaPositive === undefined && 'text-text-3',
                )}
              >
                {it.delta}
              </span>
            )}
          </div>
          {it.sub && <div className="mt-0.5 text-caption text-text-3">{it.sub}</div>}
        </div>
      );
    })}
  </div>
);

export default StatBar;
