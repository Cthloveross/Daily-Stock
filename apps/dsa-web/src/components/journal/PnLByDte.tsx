import type React from 'react';
import type { DteBucketStat } from '../../types/journal';

const fmtMoney = (n: number) => {
  const sign = n > 0 ? '+' : n < 0 ? '\u2212' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
};
const fmtPct = (n: number) => `${(n * 100).toFixed(0)}%`;

interface Props {
  items: DteBucketStat[];
  className?: string;
}

export const PnLByDte: React.FC<Props> = ({ items, className }) => {
  if (!items.length) {
    return (
      <div className={`rounded-ds-md border border-subtle bg-bg-1 p-4 ${className ?? ''}`}>
        <div className="text-label uppercase text-text-3">DTE 分布</div>
        <p className="mt-3 text-body-sm text-text-3">无期权成交。</p>
      </div>
    );
  }
  const maxAbs = Math.max(1, ...items.map((it) => Math.abs(it.sumPnlNet)));
  return (
    <div className={`rounded-ds-md border border-subtle bg-bg-1 p-4 ${className ?? ''}`}>
      <div className="flex items-center justify-between">
        <div className="text-label uppercase text-text-3">DTE 分布 / PnL</div>
        <div className="font-mono text-mono-xs text-text-3">{items.length} 桶</div>
      </div>
      <ul className="mt-3 space-y-2">
        {items.map((it) => {
          const pct = Math.min(100, (Math.abs(it.sumPnlNet) / maxAbs) * 100);
          const up = it.sumPnlNet >= 0;
          return (
            <li key={it.bucket} className="flex items-center gap-3">
              <div className="w-16 shrink-0 font-mono text-mono-sm text-text-1">{it.bucket}</div>
              <div className="flex-1">
                <div className="relative h-2 rounded-ds-sm bg-bg-2">
                  <span
                    className={`absolute top-0 h-full rounded-ds-sm ${up ? 'bg-up-strong' : 'bg-down-strong'}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <div className="mt-1 flex justify-between text-caption text-text-3">
                  <span>{it.count} 笔 · 胜率 {fmtPct(it.winRate)}</span>
                  <span
                    className={`font-mono tabular-nums ${
                      up ? 'text-up-strong' : 'text-down-strong'
                    }`}
                  >
                    {fmtMoney(it.sumPnlNet)}
                  </span>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default PnLByDte;
