import type React from 'react';
import { useEffect, useMemo } from 'react';
import { useRegimeStore } from '../../stores/regimeStore';
import type { RegimeScoreItem } from '../../types/regime';
import { Skeleton } from '../ui/Skeleton';
import { EmptyState } from '../ui/EmptyState';
import { cn } from '../../utils/cn';

interface RegimeHistoryChartProps {
  days?: number;
  height?: number;
  className?: string;
}

const STATE_COLOR: Record<string, string> = {
  aggressive: 'var(--up-strong)',
  standard: 'var(--accent)',
  cautious: 'var(--warn-strong)',
  no_trade: 'var(--down-strong)',
};

export const RegimeHistoryChart: React.FC<RegimeHistoryChartProps> = ({
  days = 30,
  height = 200,
  className,
}) => {
  const history = useRegimeStore((s) => s.history);
  const historyLoading = useRegimeStore((s) => s.historyLoading);
  const loadHistory = useRegimeStore((s) => s.loadHistory);

  useEffect(() => {
    void loadHistory(days);
  }, [loadHistory, days]);

  const items: RegimeScoreItem[] = history?.items ?? [];
  const width = 720;
  const pad = { left: 32, right: 40, top: 8, bottom: 20 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const { path, area, dotStripe, yOf } = useMemo(() => {
    if (items.length < 2) {
      return {
        path: '',
        area: '',
        dotStripe: [] as { x: number; color: string }[],
        yOf: (() => 0) as (v: number) => number,
      };
    }
    const minScore = Math.min(-100, ...items.map((i) => i.score));
    const maxScore = Math.max(100, ...items.map((i) => i.score));
    const range = maxScore - minScore || 1;
    const stepX = plotW / (items.length - 1);

    const yOfFn = (v: number) => pad.top + plotH - ((v - minScore) / range) * plotH;
    const xOf = (i: number) => pad.left + i * stepX;

    const pts = items.map((it, i) => `${xOf(i)},${yOfFn(it.score)}`);
    const zeroY = yOfFn(0);
    const areaPts = [
      `${xOf(0)},${zeroY}`,
      ...items.map((it, i) => `${xOf(i)},${yOfFn(it.score)}`),
      `${xOf(items.length - 1)},${zeroY}`,
    ];

    const stripe = items.map((it, i) => ({
      x: xOf(i),
      color: STATE_COLOR[it.label] ?? 'var(--text-3)',
    }));

    return {
      path: `M ${pts.join(' L ')}`,
      area: `M ${areaPts.join(' L ')} Z`,
      dotStripe: stripe,
      yOf: yOfFn,
    };
  }, [items, plotW, plotH, pad.left, pad.top]);

  if (historyLoading && items.length === 0) {
    return <Skeleton height={height} width="100%" className={className} />;
  }

  if (items.length < 2) {
    return (
      <div className={className}>
        <EmptyState
          title={items.length === 0 ? '还没有 regime 历史记录' : '历史只有 1 天，无法绘图'}
          description={
            '每天 14:00 UTC 的 GitHub Action 会自动算当日 regime，手动触发请点 RegimeGauge 卡上的 Recompute，' +
            '或在终端跑 `python -m src.regime.cli`。第 2 次 compute 后折线会出来。'
          }
          size="sm"
        />
      </div>
    );
  }

  const thresholds = [50, 20, -20, -50];

  return (
    <div className={cn('rounded-ds-md border border-subtle bg-bg-1 p-3', className)}>
      <svg viewBox={`0 0 ${width} ${height + 18}`} className="w-full" aria-label="Regime history">
        {thresholds.map((t) => (
          <g key={t}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={yOf(t)}
              y2={yOf(t)}
              stroke="var(--border-default)"
              strokeDasharray="2 4"
              strokeWidth={1}
              opacity={0.6}
            />
            <text
              x={width - pad.right + 4}
              y={yOf(t) + 3}
              className="text-[10px]"
              fill="var(--text-3)"
            >
              {t > 0 ? `+${t}` : t}
            </text>
          </g>
        ))}
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={yOf(0)}
          y2={yOf(0)}
          stroke="var(--border-default)"
          strokeWidth={1}
        />
        <path d={area} fill="var(--accent)" fillOpacity={0.04} />
        <path d={path} stroke="var(--accent)" strokeWidth={1.5} fill="none" />
        {dotStripe.map((d, i) => (
          <rect
            key={i}
            x={d.x - 2}
            y={height + 4}
            width={4}
            height={4}
            rx={1}
            fill={d.color}
          />
        ))}
      </svg>
    </div>
  );
};

export default RegimeHistoryChart;
