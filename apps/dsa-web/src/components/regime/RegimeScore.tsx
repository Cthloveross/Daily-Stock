import type React from 'react';
import { RefreshCw } from 'lucide-react';
import { Sparkline } from '../data/Sparkline';
import { cn } from '../../utils/cn';

export type RegimeState = 'aggressive' | 'standard' | 'cautious' | 'no_trade';

export interface RegimeScoreProps {
  score: number;
  state: RegimeState;
  note?: string | null;
  history?: number[];
  updatedAt?: Date | string | null;
  version?: string | null;
  onRecompute?: () => void;
  recomputing?: boolean;
  className?: string;
}

const STATE_COLOR: Record<RegimeState, string> = {
  aggressive: 'text-up-strong',
  standard: 'text-text-1',
  cautious: 'text-warn-strong',
  no_trade: 'text-down-strong',
};
const STATE_LABEL: Record<RegimeState, string> = {
  aggressive: 'AGGRESSIVE',
  standard: 'STANDARD',
  cautious: 'CAUTIOUS',
  no_trade: 'NO TRADE',
};

function formatScore(n: number): string {
  if (n > 0) return `+${n.toFixed(0)}`;
  if (n < 0) return `\u2212${Math.abs(n).toFixed(0)}`;
  return '0';
}

function formatUpdatedAt(ts?: Date | string | null): string {
  if (!ts) return '';
  const d = typeof ts === 'string' ? new Date(ts.endsWith('Z') ? ts : `${ts}Z`) : ts;
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export const RegimeScore: React.FC<RegimeScoreProps> = ({
  score,
  state,
  note,
  history,
  updatedAt,
  version,
  onRecompute,
  recomputing,
  className,
}) => (
  <div
    className={cn(
      'flex flex-col gap-2 rounded-ds-md border border-subtle bg-bg-1 p-4',
      className,
    )}
  >
    <div className="flex items-center justify-between">
      <div className="text-label uppercase text-text-3">Regime</div>
      {onRecompute && (
        <button
          type="button"
          onClick={onRecompute}
          disabled={recomputing}
          className="inline-flex items-center gap-1 text-caption text-text-3 hover:text-text-1 disabled:opacity-60"
          title="Recompute today (60 s cooldown)"
        >
          <RefreshCw
            size={11}
            strokeWidth={1.5}
            className={recomputing ? 'animate-spin' : undefined}
          />
          {recomputing ? 'Computing…' : 'Recompute'}
        </button>
      )}
    </div>

    <div className="flex items-center gap-4">
      <span className={cn('font-mono text-mono-lg tabular-nums', STATE_COLOR[state])}>
        {formatScore(score)}
      </span>
      <span className={cn('font-mono text-mono-sm uppercase', STATE_COLOR[state])}>
        {STATE_LABEL[state]}
      </span>
      {history && history.length > 1 && (
        <div className="ml-auto">
          <Sparkline data={history} width={120} height={28} />
        </div>
      )}
    </div>

    {note && <div className="text-caption text-text-3">{note}</div>}
    <div className="text-caption text-text-4">
      {version && <span className="font-mono">{version}</span>}
      {version && updatedAt && <span> · </span>}
      {updatedAt && <span className="font-mono">{formatUpdatedAt(updatedAt)} ET</span>}
    </div>
  </div>
);

export default RegimeScore;
