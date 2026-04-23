import type React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Button } from './Button';

export interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  size?: 'sm' | 'md';
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon: Icon,
  title,
  description,
  action,
  size = 'md',
  className,
}) => {
  const iconSize = size === 'md' ? 32 : 20;
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        size === 'md' ? 'py-12' : 'py-6',
        className,
      )}
    >
      {Icon && <Icon size={iconSize} strokeWidth={1.5} className="text-text-4 mb-3" />}
      <p className={cn('text-text-2', size === 'md' ? 'text-h3' : 'text-body')}>{title}</p>
      {description && (
        <p className="mt-1 max-w-xs text-body-sm text-text-3">{description}</p>
      )}
      {action && (
        <div className="mt-4">
          <Button variant="secondary" size="sm" onClick={action.onClick}>
            {action.label}
          </Button>
        </div>
      )}
    </div>
  );
};

export default EmptyState;
