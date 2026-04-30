import type React from 'react';
import { RefreshCw } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { RegimeState } from './RegimeScore';

export interface RegimeGaugeProps {
  score: number;             // expected range: -100 .. +100
  state: RegimeState;
  note?: string | null;
  updatedAt?: Date | string | null;
  version?: string | null;
  onRecompute?: () => void;
  recomputing?: boolean;
  className?: string;
}

// Four-band speedometer. Each band spans a range on the -100..+100 scale.
// Bands are drawn as arcs in the SVG, matching the design-system semantic
// colors (muted green/red/amber, accent violet for Standard).
const BANDS: Array<{ from: number; to: number; fill: string; label: string; state: RegimeState }> = [
  { from: -100, to: -50, fill: 'var(--down-strong)', label: 'NO TRADE', state: 'no_trade' },
  { from: -50,  to: 20,  fill: 'var(--warn-strong)', label: 'CAUTIOUS', state: 'cautious' },
  { from: 20,   to: 55,  fill: 'var(--accent)',      label: 'STANDARD', state: 'standard' },
  { from: 55,   to: 100, fill: 'var(--up-strong)',   label: 'AGGRESSIVE', state: 'aggressive' },
];

const STATE_COLOR: Record<RegimeState, string> = {
  aggressive: 'text-up-strong',
  standard: 'text-accent',
  cautious: 'text-warn-strong',
  no_trade: 'text-down-strong',
};
const STATE_LABEL: Record<RegimeState, string> = {
  aggressive: 'AGGRESSIVE',
  standard: 'STANDARD',
  cautious: 'CAUTIOUS',
  no_trade: 'NO TRADE',
};

const W = 300;
const H = 196;
const CX = W / 2;
const CY = 160;   // gauge "axle" near the bottom of the card
const R_OUTER = 122;
const R_INNER = 96;

// Map score in [-100, +100] to angle in [180°, 360°] (i.e. west -> east through
// north). 180° = left end, 270° = top, 360° = right end.
function angleForScore(score: number): number {
  const clamped = Math.max(-100, Math.min(100, score));
  return 180 + ((clamped + 100) / 200) * 180;
}

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, rOuter: number, rInner: number, startDeg: number, endDeg: number): string {
  const pOuterStart = polar(cx, cy, rOuter, startDeg);
  const pOuterEnd = polar(cx, cy, rOuter, endDeg);
  const pInnerEnd = polar(cx, cy, rInner, endDeg);
  const pInnerStart = polar(cx, cy, rInner, startDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  // Sweep flags: outer arc goes CW, inner arc CCW
  return [
    `M ${pOuterStart.x.toFixed(2)} ${pOuterStart.y.toFixed(2)}`,
    `A ${rOuter} ${rOuter} 0 ${largeArc} 1 ${pOuterEnd.x.toFixed(2)} ${pOuterEnd.y.toFixed(2)}`,
    `L ${pInnerEnd.x.toFixed(2)} ${pInnerEnd.y.toFixed(2)}`,
    `A ${rInner} ${rInner} 0 ${largeArc} 0 ${pInnerStart.x.toFixed(2)} ${pInnerStart.y.toFixed(2)}`,
    'Z',
  ].join(' ');
}

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

export const RegimeGauge: React.FC<RegimeGaugeProps> = ({
  score,
  state,
  note,
  updatedAt,
  version,
  onRecompute,
  recomputing,
  className,
}) => {
  const needleAngle = angleForScore(score);
  const needleTip = polar(CX, CY, R_OUTER - 6, needleAngle);

  return (
    <div className={cn('flex flex-col gap-2 rounded-ds-md border border-subtle bg-bg-1 p-4', className)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="text-label uppercase text-text-3">Regime · speedometer</div>
          <span
            className="rounded-full border border-subtle px-1.5 py-0.5 text-[9px] font-medium text-text-3"
            title="设计参考 CNN Fear & Greed Index（把多组市场信号折叠成单一刻度）"
          >
            F&amp;G style
          </span>
        </div>
        {onRecompute && (
          <button
            type="button"
            onClick={onRecompute}
            disabled={recomputing}
            className="inline-flex items-center gap-1 text-caption text-text-3 hover:text-text-1 disabled:opacity-60"
            title="Recompute today (60 s cooldown)"
          >
            <RefreshCw size={11} strokeWidth={1.5} className={recomputing ? 'animate-spin' : undefined} />
            {recomputing ? 'Computing…' : 'Recompute'}
          </button>
        )}
      </div>

      <svg viewBox={`0 0 ${W} ${H + 8}`} className="w-full" aria-label={`Regime score ${score}`}>
        {/* Band arcs */}
        {BANDS.map((b) => {
          const a1 = angleForScore(b.from);
          const a2 = angleForScore(b.to);
          const active = b.state === state;
          return (
            <path
              key={b.label}
              d={arcPath(CX, CY, R_OUTER, R_INNER, a1, a2)}
              fill={b.fill}
              fillOpacity={active ? 0.95 : 0.22}
              stroke="var(--bg-0)"
              strokeWidth={1.2}
            />
          );
        })}

        {/* Band labels — like Fear & Greed's "Fear / Neutral / Greed" arc labels */}
        {BANDS.map((b) => {
          const midScore = (b.from + b.to) / 2;
          const a = angleForScore(midScore);
          const labelP = polar(CX, CY, (R_OUTER + R_INNER) / 2, a);
          const active = b.state === state;
          return (
            <text
              key={`${b.label}-label`}
              x={labelP.x}
              y={labelP.y}
              textAnchor="middle"
              dominantBaseline="middle"
              className="text-[8.5px] font-medium"
              fill={active ? 'var(--bg-0)' : 'var(--text-3)'}
              style={{ letterSpacing: '0.04em' }}
            >
              {b.label}
            </text>
          );
        })}

        {/* Tick marks every 25 units */}
        {[-100, -50, 0, 50, 100].map((t) => {
          const a = angleForScore(t);
          const outer = polar(CX, CY, R_OUTER + 3, a);
          const inner = polar(CX, CY, R_OUTER - 3, a);
          const labelP = polar(CX, CY, R_OUTER + 14, a);
          return (
            <g key={t}>
              <line
                x1={outer.x}
                y1={outer.y}
                x2={inner.x}
                y2={inner.y}
                stroke="var(--text-3)"
                strokeWidth={1}
              />
              <text
                x={labelP.x}
                y={labelP.y}
                textAnchor="middle"
                dominantBaseline="middle"
                className="text-[9px]"
                fill="var(--text-3)"
              >
                {t > 0 ? `+${t}` : t}
              </text>
            </g>
          );
        })}

        {/* Needle */}
        <line
          x1={CX}
          y1={CY}
          x2={needleTip.x}
          y2={needleTip.y}
          stroke="var(--text-1)"
          strokeWidth={3}
          strokeLinecap="round"
        />
        <circle cx={CX} cy={CY} r={7} fill="var(--bg-3)" stroke="var(--text-1)" strokeWidth={1.5} />

        {/* Score number — big, F&G style */}
        <text
          x={CX}
          y={CY - 46}
          textAnchor="middle"
          className="font-mono"
          style={{ fontSize: 38, fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}
          fill="var(--text-1)"
        >
          {formatScore(score)}
        </text>

        {/* Small caption under the number */}
        <text
          x={CX}
          y={CY - 26}
          textAnchor="middle"
          className="text-[9px]"
          fill="var(--text-3)"
          style={{ letterSpacing: '0.08em' }}
        >
          REGIME SCORE
        </text>
      </svg>

      <div className="flex items-center justify-between">
        <span className={cn('font-mono text-mono-sm uppercase', STATE_COLOR[state])}>
          {STATE_LABEL[state]}
        </span>
        <div className="text-caption text-text-4">
          {version && <span className="font-mono">{version}</span>}
          {version && updatedAt && <span> · </span>}
          {updatedAt && <span className="font-mono">{formatUpdatedAt(updatedAt)} ET</span>}
        </div>
      </div>

      {note && <div className="text-caption text-text-3">{note}</div>}
    </div>
  );
};

export default RegimeGauge;
