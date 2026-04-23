import type React from 'react';
import { cn } from '../../utils/cn';

export interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  variant?: 'text' | 'rect' | 'circle';
  shimmer?: boolean;
  className?: string;
}

const VARIANT = {
  text: 'rounded-ds-sm',
  rect: 'rounded-ds-md',
  circle: 'rounded-full',
};

export const Skeleton: React.FC<SkeletonProps> = ({
  width,
  height,
  variant = 'text',
  shimmer = true,
  className,
}) => (
  <div
    className={cn(
      'relative overflow-hidden bg-bg-2',
      VARIANT[variant],
      shimmer && 'skeleton-shimmer',
      className,
    )}
    style={{ width, height }}
  >
    <style>{`
      @keyframes skeleton-shimmer {
        0%   { transform: translateX(-100%); }
        100% { transform: translateX(100%); }
      }
      .skeleton-shimmer::after {
        content: '';
        position: absolute;
        inset: 0;
        background: linear-gradient(
          90deg,
          transparent,
          var(--accent-subtle-bg),
          transparent
        );
        animation: skeleton-shimmer 2s linear infinite;
      }
    `}</style>
  </div>
);

export default Skeleton;
