import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Command } from 'cmdk';
import { useNavigate } from 'react-router-dom';
import {
  Gauge,
  CandlestickChart,
  NotebookPen,
  Rewind,
  Settings,
  RefreshCw,
  TrendingUp,
  ArrowRight,
} from 'lucide-react';
import { useRegimeStore } from '../../stores/regimeStore';

interface CommandMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const RECENT_KEY = 'dsa:recent-tickers';

function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as string[]).slice(0, 5) : [];
  } catch {
    return [];
  }
}

function pushRecent(ticker: string) {
  const t = ticker.toUpperCase();
  try {
    const cur = loadRecent().filter((x) => x !== t);
    cur.unshift(t);
    localStorage.setItem(RECENT_KEY, JSON.stringify(cur.slice(0, 5)));
  } catch {
    /* ignore */
  }
}

export const CommandMenu: React.FC<CommandMenuProps> = ({ open, onOpenChange }) => {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const recompute = useRegimeStore((s) => s.recompute);
  const recent = useMemo(() => (open ? loadRecent() : []), [open]);

  const close = (reset = true) => {
    if (reset) setSearch('');
    onOpenChange(false);
  };

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const goToStock = (ticker: string) => {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    pushRecent(t);
    navigate(`/stocks/${t}`);
    close();
  };

  const goToPage = (to: string) => {
    navigate(to);
    close();
  };

  const typed = search.trim().toUpperCase();
  const looksLikeTicker = /^[A-Z0-9.]{1,8}$/.test(typed);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-modal flex items-start justify-center bg-black/50 px-4 pt-[15vh]"
      onClick={() => close()}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg overflow-hidden rounded-ds-md border border-default bg-bg-2 shadow-md"
      >
        <Command shouldFilter label="Command Menu">
          <div className="flex items-center gap-2 border-b border-subtle px-4">
            <Command.Input
              value={search}
              onValueChange={setSearch}
              placeholder="Type a ticker (NFLX), page (regime), or action…"
              className="h-12 flex-1 bg-transparent text-body text-text-1 placeholder:text-text-3 outline-none"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter' && looksLikeTicker) {
                  e.preventDefault();
                  goToStock(typed);
                }
              }}
            />
          </div>

          <Command.List className="max-h-80 overflow-y-auto py-1">
            <Command.Empty className="py-6 text-center text-body-sm text-text-3">
              No matches. Press Enter to navigate to ticker “{typed || '—'}”.
            </Command.Empty>

            {looksLikeTicker && (
              <Command.Group heading="Navigate to ticker">
                <Item
                  icon={<TrendingUp size={14} strokeWidth={1.5} />}
                  label={`Go to ${typed}`}
                  meta="Enter"
                  onSelect={() => goToStock(typed)}
                />
              </Command.Group>
            )}

            <Command.Group heading="Pages">
              <Item
                icon={<Gauge size={14} strokeWidth={1.5} />}
                label="Regime"
                meta="g r"
                onSelect={() => goToPage('/regime')}
              />
              <Item
                icon={<CandlestickChart size={14} strokeWidth={1.5} />}
                label="Watchlist"
                meta="g w"
                onSelect={() => goToPage('/watchlist')}
              />
              <Item
                icon={<NotebookPen size={14} strokeWidth={1.5} />}
                label="Journal"
                meta="g j"
                onSelect={() => goToPage('/journal')}
              />
              <Item
                icon={<Rewind size={14} strokeWidth={1.5} />}
                label="Backtest"
                meta="g b"
                onSelect={() => goToPage('/backtest')}
              />
              <Item
                icon={<Settings size={14} strokeWidth={1.5} />}
                label="Settings"
                onSelect={() => goToPage('/settings')}
              />
            </Command.Group>

            <Command.Group heading="Actions">
              <Item
                icon={<RefreshCw size={14} strokeWidth={1.5} />}
                label="Recompute regime"
                onSelect={() => {
                  void recompute();
                  close();
                }}
              />
            </Command.Group>

            {recent.length > 0 && (
              <Command.Group heading="Recent">
                {recent.map((t) => (
                  <Item
                    key={t}
                    icon={<ArrowRight size={14} strokeWidth={1.5} />}
                    label={t}
                    onSelect={() => goToStock(t)}
                  />
                ))}
              </Command.Group>
            )}
          </Command.List>
        </Command>

        <style>{`
          [cmdk-group-heading] {
            padding: 8px 16px 4px;
            font-size: var(--text-label);
            text-transform: uppercase;
            letter-spacing: var(--tracking-label);
            color: var(--text-3);
          }
          [cmdk-item] {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            font-size: var(--text-body);
            color: var(--text-1);
            cursor: pointer;
          }
          [cmdk-item][data-selected="true"] {
            background: var(--bg-3);
          }
        `}</style>
      </div>
    </div>
  );
};

const Item: React.FC<{
  icon: React.ReactNode;
  label: string;
  meta?: string;
  onSelect: () => void;
}> = ({ icon, label, meta, onSelect }) => (
  <Command.Item value={label} onSelect={onSelect}>
    <span className="text-text-2">{icon}</span>
    <span>{label}</span>
    {meta && <span className="ml-auto font-mono text-caption text-text-3">{meta}</span>}
  </Command.Item>
);

export default CommandMenu;
