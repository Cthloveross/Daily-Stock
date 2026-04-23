import type React from 'react';
import type { TradeItem } from '../../types/journal';

const fmtUSD = (n: number | null | undefined): string =>
  n === null || n === undefined
    ? '—'
    : `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const fmtPct = (n: number | null | undefined): string =>
  n === null || n === undefined ? '—' : `${n.toFixed(1)}%`;

const fmtDate = (s: string | null | undefined): string => {
  if (!s) return '—';
  // Backend serializes naive UTC datetimes without a tz suffix; append "Z" so
  // the browser doesn't misinterpret them as local time.
  const normalised = s.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(s) ? s : `${s}Z`;
  const d = new Date(normalised);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
};

interface Props {
  items: TradeItem[];
  onRowClick?: (t: TradeItem) => void;
  loading?: boolean;
}

export const TradeTable: React.FC<Props> = ({ items, onRowClick, loading }) => {
  if (loading) {
    return (
      <div className="card-base p-4">
        <p className="text-muted">Loading trades…</p>
      </div>
    );
  }
  if (!items.length) {
    return (
      <div className="card-base p-4">
        <p className="text-muted">No trades match your filters.</p>
      </div>
    );
  }
  return (
    <div className="card-base overflow-hidden">
      <div className="max-h-[60vh] overflow-y-auto">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="sticky top-0 bg-base-subtle text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-3 py-2 text-left">Symbol</th>
              <th className="px-3 py-2 text-left">Dir</th>
              <th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Entry</th>
              <th className="px-3 py-2 text-right">Exit</th>
              <th className="px-3 py-2 text-left">Entry time</th>
              <th className="px-3 py-2 text-right">Hold</th>
              <th className="px-3 py-2 text-left">DTE</th>
              <th className="px-3 py-2 text-right">PnL net</th>
              <th className="px-3 py-2 text-right">PnL %</th>
              <th className="px-3 py-2 text-left">Style</th>
              <th className="px-3 py-2 text-left">Fake</th>
            </tr>
          </thead>
          <tbody>
            {items.map((t) => {
              const pnlColor =
                t.pnlNet === null || t.pnlNet === undefined
                  ? ''
                  : t.pnlNet >= 0
                    ? 'text-emerald-400'
                    : 'text-red-400';
              return (
                <tr
                  key={t.id}
                  onClick={() => onRowClick?.(t)}
                  className={`border-t border-base-subtle ${onRowClick ? 'cursor-pointer hover:bg-base-subtle/40' : ''}`}
                >
                  <td className="px-3 py-2 font-mono">{t.rawSymbol ?? t.underlying}</td>
                  <td className="px-3 py-2">{t.direction}</td>
                  <td className="px-3 py-2 text-right">{t.quantity}</td>
                  <td className="px-3 py-2 text-right">${t.avgEntryPrice.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right">
                    {t.avgExitPrice !== null && t.avgExitPrice !== undefined
                      ? `$${t.avgExitPrice.toFixed(2)}`
                      : '—'}
                  </td>
                  <td className="px-3 py-2">{fmtDate(t.entryTime)}</td>
                  <td className="px-3 py-2 text-right">
                    {t.holdSeconds !== null && t.holdSeconds !== undefined
                      ? `${Math.round(t.holdSeconds / 60)}m`
                      : '—'}
                  </td>
                  <td className="px-3 py-2">{t.dteBucket ?? '—'}</td>
                  <td className={`px-3 py-2 text-right ${pnlColor}`}>{fmtUSD(t.pnlNet)}</td>
                  <td className={`px-3 py-2 text-right ${pnlColor}`}>{fmtPct(t.pnlPct)}</td>
                  <td className="px-3 py-2">{t.tradeStyle ?? '—'}</td>
                  <td className="px-3 py-2">
                    {t.wasFakeBreakout === true
                      ? '✓'
                      : t.wasFakeBreakout === false
                        ? '·'
                        : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default TradeTable;
