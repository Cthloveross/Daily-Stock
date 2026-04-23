import type React from 'react';
import { cn } from '../../utils/cn';

export type DataSourceStatusKind = 'ok' | 'partial' | 'missing' | 'error';

export interface DataSource {
  name: string;
  status: DataSourceStatusKind;
  detail?: string;
}

export interface DataSourceStatusProps {
  sources: DataSource[];
  variant?: 'bar' | 'list';
  className?: string;
}

const DOT: Record<DataSourceStatusKind, string> = {
  ok: 'bg-up-strong',
  partial: 'bg-warn-strong',
  missing: 'bg-down-strong',
  error: 'bg-down-strong animate-pulse',
};
const TEXT: Record<DataSourceStatusKind, string> = {
  ok: 'text-text-2',
  partial: 'text-warn-strong',
  missing: 'text-down-strong',
  error: 'text-down-strong',
};

export const DataSourceStatus: React.FC<DataSourceStatusProps> = ({
  sources,
  variant = 'bar',
  className,
}) => {
  if (variant === 'bar') {
    return (
      <div className={cn('flex flex-wrap items-center gap-4 text-caption', className)}>
        {sources.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1.5">
            <span className={cn('h-1.5 w-1.5 rounded-full', DOT[s.status])} aria-hidden />
            <span className={TEXT[s.status]}>{s.name}</span>
            {s.detail && <span className="text-text-3">{s.detail}</span>}
          </span>
        ))}
      </div>
    );
  }

  return (
    <ul className={cn('space-y-1.5', className)}>
      {sources.map((s) => (
        <li key={s.name} className="flex items-start gap-2">
          <span className={cn('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', DOT[s.status])} />
          <div className="flex-1 text-body-sm">
            <span className={TEXT[s.status]}>{s.name}</span>
            {s.detail && <span className="ml-2 text-text-3">{s.detail}</span>}
          </div>
        </li>
      ))}
    </ul>
  );
};

export default DataSourceStatus;
