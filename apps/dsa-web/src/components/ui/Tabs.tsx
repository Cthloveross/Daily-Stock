import type React from 'react';
import { cn } from '../../utils/cn';

export interface TabItem {
  value: string;
  label: string;
  count?: number;
}

export interface TabsProps {
  value: string;
  onChange: (v: string) => void;
  items: TabItem[];
  variant?: 'underline' | 'pills';
  className?: string;
}

export const Tabs: React.FC<TabsProps> = ({
  value,
  onChange,
  items,
  variant = 'underline',
  className,
}) => {
  if (variant === 'pills') {
    return (
      <div className={cn('inline-flex gap-1 rounded-ds-sm bg-bg-1 p-1', className)}>
        {items.map((it) => {
          const active = it.value === value;
          return (
            <button
              key={it.value}
              type="button"
              onClick={() => onChange(it.value)}
              className={cn(
                'px-3 h-7 rounded-ds-sm text-body-sm transition-colors',
                active ? 'bg-bg-3 text-text-1' : 'text-text-2 hover:text-text-1',
              )}
            >
              {it.label}
              {typeof it.count === 'number' && (
                <span className="ml-2 font-mono text-mono-sm text-text-3">{it.count}</span>
              )}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className={cn('flex items-center gap-0 border-b border-subtle', className)}>
      {items.map((it) => {
        const active = it.value === value;
        return (
          <button
            key={it.value}
            type="button"
            onClick={() => onChange(it.value)}
            className={cn(
              'relative -mb-px px-3 py-2 text-body transition-colors',
              active
                ? 'text-text-1 border-b-2 border-accent'
                : 'text-text-2 hover:text-text-1 border-b-2 border-transparent',
            )}
          >
            {it.label}
            {typeof it.count === 'number' && (
              <span className="ml-2 font-mono text-mono-sm text-text-3">{it.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
};

export default Tabs;
