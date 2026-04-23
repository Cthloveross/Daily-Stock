import type React from 'react';
import { cn } from '../../utils/cn';

export interface PriceCellProps {
  value: number | null | undefined;
  currency?: string;
  decimals?: number;
  colorize?: boolean;
  size?: 'sm' | 'md';
  className?: string;
}

const SIZE = { sm: 'text-mono-sm', md: 'text-mono-md' };

export const PriceCell: React.FC<PriceCellProps> = ({
  value,
  currency,
  decimals = 2,
  colorize = false,
  size = 'md',
  className,
}) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={cn('font-mono text-text-3', SIZE[size], className)}>—</span>;
  }
  const fmt = value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const color = colorize
    ? value > 0
      ? 'text-up-strong'
      : value < 0
        ? 'text-down-strong'
        : 'text-text-3'
    : 'text-text-1';
  return (
    <span className={cn('font-mono tabular-nums', SIZE[size], color, className)}>
      {currency && <span className="text-text-3">{currency} </span>}
      {fmt}
    </span>
  );
};

export default PriceCell;
