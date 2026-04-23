import type React from 'react';
import { useMemo } from 'react';
import { cn } from '../../utils/cn';

export interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  colorize?: boolean;
  showDot?: boolean;
  className?: string;
}

export const Sparkline: React.FC<SparklineProps> = ({
  data,
  width = 80,
  height = 20,
  colorize = false,
  showDot = false,
  className,
}) => {
  const { points, dotX, dotY, stroke } = useMemo(() => {
    if (data.length < 2) {
      return { points: '', dotX: 0, dotY: 0, stroke: 'var(--text-2)' };
    }
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const stepX = width / (data.length - 1);
    const mapped = data.map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return [x, y] as const;
    });
    const ptStr = mapped.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
    const last = mapped[mapped.length - 1];
    const first = mapped[0];
    const up = last[1] < first[1];
    const color = colorize
      ? up
        ? 'var(--up-strong)'
        : 'var(--down-strong)'
      : 'var(--text-2)';
    return { points: ptStr, dotX: last[0], dotY: last[1], stroke: color };
  }, [data, width, height, colorize]);

  if (data.length < 2) {
    return <span className={cn('text-text-3 text-mono-xs', className)}>—</span>;
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn('block', className)}
      aria-hidden
    >
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={1}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {showDot && <circle cx={dotX} cy={dotY} r={1.5} fill={stroke} />}
    </svg>
  );
};

export default Sparkline;
