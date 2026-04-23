import type React from 'react';
import { cn } from '../../utils/cn';

export type MATrend = 'up' | 'flat' | 'down';

export interface MASlopeCellProps {
  ma3: MATrend;
  ma5: MATrend;
  ma13: MATrend;
  className?: string;
}

const GLYPH: Record<MATrend, string> = {
  up: '↑',
  flat: '→',
  down: '↓',
};

const COLOR: Record<MATrend, string> = {
  up: 'text-up-strong',
  flat: 'text-text-3',
  down: 'text-down-strong',
};

export const MASlopeCell: React.FC<MASlopeCellProps> = ({ ma3, ma5, ma13, className }) => (
  <span
    className={cn('font-mono text-mono-md tabular-nums inline-flex gap-0.5', className)}
    aria-label={`MA3 ${ma3}, MA5 ${ma5}, MA13 ${ma13}`}
  >
    <span className={COLOR[ma3]}>{GLYPH[ma3]}</span>
    <span className={COLOR[ma5]}>{GLYPH[ma5]}</span>
    <span className={COLOR[ma13]}>{GLYPH[ma13]}</span>
  </span>
);

export default MASlopeCell;
