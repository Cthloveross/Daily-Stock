import type React from 'react';
import { cn } from '../../utils/cn';

export type BadgeVariant = 'neutral' | 'bullish' | 'bearish' | 'warn' | 'accent';

export interface BadgeProps {
  variant: BadgeVariant;
  size?: 'sm' | 'md';
  children: React.ReactNode;
  className?: string;
}

const SIZE = {
  sm: 'h-5 px-1.5 text-label',
  md: 'h-6 px-2 text-body-sm font-medium',
};

const VARIANT: Record<BadgeVariant, string> = {
  neutral: 'bg-bg-2 text-text-2 border border-subtle',
  bullish: 'bg-up-subtle text-up-strong',
  bearish: 'bg-down-subtle text-down-strong',
  warn: 'bg-warn-subtle text-warn-strong',
  accent:
    'bg-[color:var(--accent-subtle-bg)] text-accent border border-[color:var(--accent-subtle-border)]',
};

export const Badge: React.FC<BadgeProps> = ({ variant, size = 'sm', children, className }) => (
  <span
    className={cn(
      'inline-flex items-center gap-1 rounded-ds-sm font-sans whitespace-nowrap',
      SIZE[size],
      VARIANT[variant],
      className,
    )}
  >
    {children}
  </span>
);

export default Badge;
