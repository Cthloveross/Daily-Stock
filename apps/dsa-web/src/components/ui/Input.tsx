import type React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../utils/cn';

export interface InputProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  size?: 'sm' | 'md';
  iconLeft?: LucideIcon;
  iconRight?: LucideIcon;
  disabled?: boolean;
  autoFocus?: boolean;
  type?: 'text' | 'number' | 'search';
  className?: string;
  'aria-label'?: string;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}

const HEIGHT = { sm: 'h-7', md: 'h-8' };
const TEXT = { sm: 'text-body-sm', md: 'text-body' };

export const Input: React.FC<InputProps> = ({
  value,
  onChange,
  placeholder,
  size = 'md',
  iconLeft: IconLeft,
  iconRight: IconRight,
  disabled,
  autoFocus,
  type = 'text',
  className,
  onKeyDown,
  ...rest
}) => {
  const iconSize = size === 'sm' ? 12 : 14;
  return (
    <div
      className={cn(
        'inline-flex items-center gap-2 rounded-ds-sm border border-subtle bg-bg-1 px-2 transition-colors focus-within:border-accent focus-within:ring-1 focus-within:ring-accent',
        HEIGHT[size],
        disabled && 'opacity-60',
        className,
      )}
    >
      {IconLeft && <IconLeft size={iconSize} strokeWidth={1.5} className="text-text-3" />}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        onKeyDown={onKeyDown}
        className={cn(
          'flex-1 bg-transparent text-text-1 placeholder:text-text-3 outline-none',
          TEXT[size],
        )}
        {...rest}
      />
      {IconRight && <IconRight size={iconSize} strokeWidth={1.5} className="text-text-3" />}
    </div>
  );
};

export default Input;
