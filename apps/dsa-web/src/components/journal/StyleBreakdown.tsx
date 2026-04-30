import type React from 'react';
import { useNavigate } from 'react-router-dom';
import type { StyleBucketStat } from '../../types/journal';

const STYLE_COLOR: Record<string, string> = {
  breakout_chase: '#7170ff',
  retest: '#06b6d4',
  pullback_buy: '#22c55e',
  mean_reversion: '#d29922',
  gap_fade: '#a855f7',
  equity_swing: '#ef4444',
  other: '#8a8a94',
  unknown: '#8a8a94',
};

const STYLE_LABEL: Record<string, string> = {
  breakout_chase: '破位追涨',
  retest: '回踩确认',
  pullback_buy: '回调买入',
  mean_reversion: '均值回归',
  gap_fade: 'Gap 反向',
  equity_swing: '股票波段',
  other: '其他',
  unknown: '未分类',
};

const colorFor = (style: string) => STYLE_COLOR[style] ?? '#8a8a94';
const labelFor = (style: string) => STYLE_LABEL[style] ?? style;

const fmtMoney = (n: number) => {
  const sign = n > 0 ? '+' : n < 0 ? '\u2212' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
};
const fmtPct = (n: number) => `${(n * 100).toFixed(0)}%`;

interface Props {
  items: StyleBucketStat[];
  className?: string;
}

export const StyleBreakdown: React.FC<Props> = ({ items, className }) => {
  const navigate = useNavigate();

  if (!items.length) {
    return (
      <div className={`rounded-ds-md border border-subtle bg-bg-1 p-4 ${className ?? ''}`}>
        <div className="text-label uppercase text-text-3">交易分类</div>
        <p className="mt-3 text-body-sm text-text-3">
          还没有已平仓交易可分类。导入 CSV 后运行 <code>scripts/backfill_trade_style.py</code> 回填 trade_style。
        </p>
      </div>
    );
  }

  // PnL 堆叠条：把 sum_pnl_net 正负分别按绝对值算占比
  const totalAbs = items.reduce((acc, it) => acc + Math.abs(it.sumPnlNet), 0) || 1;

  return (
    <div className={`rounded-ds-md border border-subtle bg-bg-1 p-4 ${className ?? ''}`}>
      <div className="flex items-center justify-between">
        <div className="text-label uppercase text-text-3">交易分类 / PnL</div>
        <div className="font-mono text-mono-xs text-text-3">{items.length} 类</div>
      </div>

      {/* Stacked proportional bar */}
      <div className="mt-3 flex h-3 w-full overflow-hidden rounded-ds-sm">
        {items.map((it) => {
          const width = (Math.abs(it.sumPnlNet) / totalAbs) * 100;
          const color = colorFor(it.style);
          const opacity = it.sumPnlNet >= 0 ? 1 : 0.45;
          return (
            <div
              key={it.style}
              title={`${labelFor(it.style)} · ${fmtMoney(it.sumPnlNet)}`}
              style={{ width: `${width}%`, background: color, opacity }}
            />
          );
        })}
      </div>

      {/* Detail table */}
      <table className="mt-4 w-full text-body-sm">
        <thead>
          <tr className="border-b border-subtle text-left text-label uppercase text-text-3">
            <th className="py-2 pr-3 font-normal">Style</th>
            <th className="py-2 pr-3 text-right font-normal">N</th>
            <th className="py-2 pr-3 text-right font-normal">Win%</th>
            <th className="py-2 pr-3 text-right font-normal">Avg net</th>
            <th className="py-2 pr-3 text-right font-normal">Sum net</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <tr
              key={it.style}
              className="cursor-pointer border-b border-subtle hover:bg-bg-2"
              onClick={() =>
                navigate(`/journal?tab=trades&style=${encodeURIComponent(it.style)}`)
              }
            >
              <td className="py-2 pr-3 text-text-1">
                <span className="inline-flex items-center gap-2">
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ background: colorFor(it.style) }}
                    aria-hidden
                  />
                  {labelFor(it.style)}
                </span>
              </td>
              <td className="py-2 pr-3 text-right font-mono tabular-nums text-text-1">{it.count}</td>
              <td className="py-2 pr-3 text-right font-mono tabular-nums text-text-2">
                {fmtPct(it.winRate)}
              </td>
              <td
                className={`py-2 pr-3 text-right font-mono tabular-nums ${
                  it.avgPnlNet >= 0 ? 'text-up-strong' : 'text-down-strong'
                }`}
              >
                {fmtMoney(it.avgPnlNet)}
              </td>
              <td
                className={`py-2 pr-3 text-right font-mono tabular-nums ${
                  it.sumPnlNet >= 0 ? 'text-up-strong' : 'text-down-strong'
                }`}
              >
                {fmtMoney(it.sumPnlNet)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default StyleBreakdown;
