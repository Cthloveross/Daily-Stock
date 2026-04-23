import type React from 'react';
import { useEffect, useState } from 'react';
import { useRegimeStore } from '../../stores/regimeStore';

export const BreakoutSignalsList: React.FC<{ limit?: number }> = ({ limit = 15 }) => {
  const { signals, signalsLoading, loadSignals } = useRegimeStore();
  const [onlyFake, setOnlyFake] = useState<boolean | undefined>(undefined);

  useEffect(() => {
    void loadSignals(limit, onlyFake);
  }, [limit, onlyFake, loadSignals]);

  return (
    <div className="card-base p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">Breakout Signals</h3>
        <div className="flex gap-1 text-xs">
          <button
            type="button"
            className={`rounded px-2 py-1 ${onlyFake === undefined ? 'bg-base-subtle' : ''}`}
            onClick={() => setOnlyFake(undefined)}
          >
            All
          </button>
          <button
            type="button"
            className={`rounded px-2 py-1 ${onlyFake === true ? 'bg-red-500/20 text-red-300' : ''}`}
            onClick={() => setOnlyFake(true)}
          >
            Fake only
          </button>
          <button
            type="button"
            className={`rounded px-2 py-1 ${onlyFake === false ? 'bg-emerald-500/20 text-emerald-300' : ''}`}
            onClick={() => setOnlyFake(false)}
          >
            Real only
          </button>
        </div>
      </div>

      {signalsLoading ? (
        <p className="mt-3 text-muted">Loading…</p>
      ) : !signals || signals.count === 0 ? (
        <p className="mt-3 text-muted">No breakout trades in scope.</p>
      ) : (
        <ul className="mt-3 space-y-1 text-sm">
          {signals.items.map((s) => (
            <li
              key={s.tradeId}
              className="flex items-center justify-between border-b border-base-subtle py-1 last:border-0"
            >
              <div>
                <span className="font-mono">{s.underlying}</span>
                <span className="ml-2 text-xs text-muted">{s.tradeStyle}</span>
                {s.wasFakeBreakout === true && (
                  <span className="ml-2 rounded bg-red-500/20 px-1 text-xs text-red-300">fake</span>
                )}
              </div>
              <div
                className={`font-mono ${
                  s.pnlNet !== null && s.pnlNet !== undefined && s.pnlNet >= 0
                    ? 'text-emerald-400'
                    : 'text-red-400'
                }`}
              >
                {s.pnlNet !== null && s.pnlNet !== undefined ? `$${s.pnlNet.toFixed(2)}` : '—'}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default BreakoutSignalsList;
