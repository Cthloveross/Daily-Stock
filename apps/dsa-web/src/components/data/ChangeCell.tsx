import type React from 'react';
import { cn } from '../../utils/cn';

export interface ChangeCellProps {
  value: number | null | undefined;
  mode: 'absolute' | 'percent';
  decimals?: number;
  showArrow?: boolean;
  size?: 'sm' | 'md';
  className?: string;
}

const SIZE = { sm: 'text-mono-sm', md: 'text-mono-md' };
const MINUS = '\u2212'; // U+2212 TRUE MINUS

export const ChangeCell: React.FC<ChangeCellProps> = ({
  value,
  mode,
  decimals = 2,
  showArrow = false,
  size = 'md',
  className,
}) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={cn('font-mono text-text-3', SIZE[size], className)}>—</span>;
  }
  const abs = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const isPositive = value > 0;
  const isNegative = value < 0;
  const sign = isPositive ? '+' : isNegative ? MINUS : '';
  const color = isPositive
    ? 'text-up-strong'
    : isNegative
      ? 'text-down-strong'
      : 'text-text-3';
  const arrow = showArrow ? (isPositive ? '▲' : isNegative ? '▼' : '') : '';
  const suffix = mode === 'percent' ? '%' : '';
  return (
    <span className={cn('font-mono tabular-nums', SIZE[size], color, className)}>
      {arrow && <span className="mr-1 text-[8px]">{arrow}</span>}
      {sign}
      {abs}
      {suffix}
    </span>
  );
};

export default ChangeCell;
