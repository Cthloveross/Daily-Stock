import type React from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Command, User } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useRegimeStore } from '../../stores/regimeStore';
import { MoomooBadge } from '../system/MoomooBadge';

interface TopBarProps {
  onSearchOpen: () => void;
}

const STATE_COLOR: Record<string, string> = {
  aggressive: 'text-up-strong',
  standard: 'text-text-1',
  cautious: 'text-warn-strong',
  no_trade: 'text-down-strong',
};

function deriveState(label?: string | null): string {
  if (!label) return 'standard';
  const k = label.toLowerCase();
  if (k.includes('aggressive')) return 'aggressive';
  if (k.includes('caution')) return 'cautious';
  if (k.includes('no trade') || k.includes('no_trade')) return 'no_trade';
  return 'standard';
}

export const TopBar: React.FC<TopBarProps> = ({ onSearchOpen }) => {
  const navigate = useNavigate();
  const today = useRegimeStore((s) => s.today);
  const state = deriveState(today?.label);
  const stateColor = STATE_COLOR[state] ?? 'text-text-1';

  const signStr = (() => {
    if (!today) return '—';
    const n = today.score;
    return (n > 0 ? '+' : n < 0 ? '\u2212' : '') + Math.abs(n).toFixed(0);
  })();

  const labelDisplay = today?.label
    ? today.label.replace(/_/g, ' ').toUpperCase()
    : '';

  return (
    <header className="flex h-14 w-full items-center gap-3 border-b border-subtle bg-bg-0 px-4">
      <button
        type="button"
        onClick={onSearchOpen}
        className={cn(
          'inline-flex h-[34px] w-80 max-w-full items-center gap-2 rounded-ds-sm border border-subtle bg-bg-1 px-3 text-body-sm text-text-3 transition-colors hover:border-default hover:text-text-2 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2',
        )}
      >
        <Search size={16} strokeWidth={1.5} />
        <span className="flex-1 text-left">Search tickers, actions…</span>
        <span className="inline-flex items-center gap-0.5 text-text-3">
          <Command size={12} strokeWidth={1.5} />
          <span className="font-mono text-mono-xs">K</span>
        </span>
      </button>

      <div className="flex-1" />

      {/* Moomoo OpenD live status — green=live, amber=offline, grey=disabled */}
      <MoomooBadge />

      <button
        type="button"
        onClick={() => navigate('/regime')}
        className="inline-flex items-center gap-3 rounded-ds-sm px-2 py-1 text-body-sm transition-colors hover:bg-bg-2"
        title="Go to Regime"
      >
        <span className="text-label uppercase text-text-3">Regime</span>
        <span className="font-mono text-mono-md tabular-nums text-text-1">{signStr}</span>
        {labelDisplay && (
          <span className={cn('font-mono text-mono-sm uppercase', stateColor)}>
            {labelDisplay}
          </span>
        )}
      </button>

      <button
        type="button"
        aria-label="Account"
        className="inline-flex h-9 w-9 items-center justify-center rounded-ds-sm text-text-2 hover:bg-bg-2 hover:text-text-1"
      >
        <User size={18} strokeWidth={1.5} />
      </button>
    </header>
  );
};

export default TopBar;
