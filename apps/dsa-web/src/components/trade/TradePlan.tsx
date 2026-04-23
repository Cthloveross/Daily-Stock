import type React from 'react';
import { cn } from '../../utils/cn';

export interface TradePlanLevel {
  price: number;
  rationale?: string;
}

export interface TradePlanProps {
  side: 'long' | 'short';
  entry: TradePlanLevel;
  entryAlt?: TradePlanLevel;
  stop: TradePlanLevel;
  target: TradePlanLevel;
  currentPrice?: number;
  className?: string;
}

function fmtPrice(v: number) {
  return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function calcRR(entry: number, stop: number, target: number, side: 'long' | 'short'): string {
  const risk = side === 'long' ? entry - stop : stop - entry;
  const reward = side === 'long' ? target - entry : entry - target;
  if (risk <= 0 || reward <= 0) return '—';
  const ratio = reward / risk;
  return `1:${ratio.toFixed(2)}`;
}

export const TradePlan: React.FC<TradePlanProps> = ({
  side,
  entry,
  entryAlt,
  stop,
  target,
  className,
}) => {
  const stopColor = side === 'long' ? 'text-down-strong' : 'text-up-strong';
  const targetColor = side === 'long' ? 'text-up-strong' : 'text-down-strong';
  const rr = calcRR(entry.price, stop.price, target.price, side);
  const rrNum = (() => {
    const m = /^1:(\d+\.?\d*)$/.exec(rr);
    return m ? Number(m[1]) : 0;
  })();
  const rrColor = rrNum > 2 ? 'text-up-strong' : rrNum < 1 ? 'text-warn-strong' : 'text-text-1';

  return (
    <div className={cn('rounded-ds-md border border-subtle bg-bg-1', className)}>
      <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
        <div className="text-label uppercase text-text-3">Trade plan</div>
        <div className={cn('font-mono text-mono-sm uppercase', side === 'long' ? 'text-up-strong' : 'text-down-strong')}>
          {side}
        </div>
      </div>
      <table className="w-full">
        <tbody>
          <Row label="Entry" sub="primary" price={entry.price} rationale={entry.rationale} />
          {entryAlt && <Row label="" sub="alt" price={entryAlt.price} rationale={entryAlt.rationale} />}
          <Row label="Stop" price={stop.price} rationale={stop.rationale} priceClass={stopColor} />
          <Row label="Target" price={target.price} rationale={target.rationale} priceClass={targetColor} />
          <tr className="border-t border-subtle">
            <td className="py-2 pl-4 text-label uppercase text-text-3">R:R</td>
            <td />
            <td className={cn('py-2 text-right font-mono text-mono-md tabular-nums', rrColor)}>{rr}</td>
            <td />
          </tr>
        </tbody>
      </table>
    </div>
  );
};

const Row: React.FC<{
  label: string;
  sub?: string;
  price: number;
  rationale?: string;
  priceClass?: string;
}> = ({ label, sub, price, rationale, priceClass }) => (
  <tr className="border-b border-subtle last:border-b-0">
    <td className="py-2 pl-4 align-top text-label uppercase text-text-3">{label}</td>
    <td className="py-2 align-top text-caption text-text-3">{sub ?? ''}</td>
    <td
      className={cn(
        'py-2 text-right align-top font-mono text-mono-md tabular-nums',
        priceClass ?? 'text-text-1',
      )}
    >
      {fmtPrice(price)}
    </td>
    <td className="py-2 pr-4 pl-3 align-top text-body-sm text-text-2">{rationale ?? ''}</td>
  </tr>
);

export default TradePlan;
