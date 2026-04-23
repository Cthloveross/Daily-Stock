import type React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../utils/cn';

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  icon: LucideIcon;
  variant?: 'ghost' | 'secondary';
  size?: 'sm' | 'md';
  'aria-label': string;
}

const SIZE = {
  sm: 'h-6 w-6',
  md: 'h-7 w-7',
};

const VARIANT = {
  ghost: 'bg-transparent text-text-2 hover:bg-bg-2 hover:text-text-1',
  secondary: 'bg-bg-2 text-text-1 border border-subtle hover:bg-bg-3',
};

export const IconButton: React.FC<IconButtonProps> = ({
  icon: Icon,
  variant = 'ghost',
  size = 'md',
  className,
  disabled,
  ...rest
}) => {
  const iconSize = size === 'sm' ? 14 : 16;
  return (
    <button
      type="button"
      disabled={disabled}
      className={cn(
        'inline-flex items-center justify-center rounded-ds-sm transition-colors duration-fast outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:text-text-3',
        SIZE[size],
        VARIANT[variant],
        className,
      )}
      {...rest}
    >
      <Icon size={iconSize} strokeWidth={1.5} />
    </button>
  );
};

export default IconButton;
