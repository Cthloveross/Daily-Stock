import type React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../utils/cn';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md';

export interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  iconLeft?: LucideIcon;
  iconRight?: LucideIcon;
  fullWidth?: boolean;
  type?: 'button' | 'submit';
}

const BASE =
  'inline-flex items-center justify-center font-sans font-medium rounded-ds-sm transition-colors duration-fast outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:cursor-not-allowed';

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 px-3 text-body-sm gap-1.5',
  md: 'h-8 px-4 text-body gap-2',
};

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-hover active:bg-accent-active disabled:bg-text-4 disabled:text-text-3',
  secondary:
    'bg-bg-2 text-text-1 border border-subtle hover:bg-bg-3 disabled:bg-bg-1 disabled:text-text-3',
  ghost:
    'bg-transparent text-text-2 hover:bg-bg-2 hover:text-text-1 disabled:text-text-3',
  danger:
    'bg-down-muted text-white hover:bg-down-strong disabled:bg-text-4 disabled:text-text-3',
};

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  loading = false,
  iconLeft: IconLeft,
  iconRight: IconRight,
  children,
  disabled,
  fullWidth,
  type = 'button',
  className,
  ...rest
}) => {
  const iconSize = size === 'sm' ? 14 : 16;
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cn(BASE, SIZE[size], VARIANT[variant], fullWidth && 'w-full', className)}
      {...rest}
    >
      {loading ? (
        <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border border-current border-t-transparent" />
      ) : (
        IconLeft && <IconLeft size={iconSize} strokeWidth={1.5} />
      )}
      {children && <span>{children}</span>}
      {!loading && IconRight && <IconRight size={iconSize} strokeWidth={1.5} />}
    </button>
  );
};

export default Button;
