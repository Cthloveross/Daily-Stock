import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { fetchEdgePanel } from '../../api/journal';
import { parseApiError, type ParsedApiError } from '../../api/error';
import type { EdgePanelResponse } from '../../types/journal';
import {
  BLOCKED_REASON_TEXT,
  FAIL_CLOSED_BANNER,
  GATE_CLOSED_TEXT,
  GATE_OPEN_TEXT,
} from './edgePanelText';

/**
 * 「今日武器档位」：文档 16（EDGE_FORENSICS_AND_REGIME_GATE）E-5 的 Edge 面板卡。
 * 数据来自 `GET /api/v1/journal/edge-panel` 固定契约：0-1DTE regime gate 档位、
 * DTE 分桶期望、H1 证据状态与 R2/R3 纪律仪表。
 *
 * 三条铁律（§5.3 投产裁定，前端逐字执行）：
 * 1. gate 仅作防御——只用于**关闭** 0-1DTE，绿灯不构成任何加仓依据；
 * 2. fail closed——行情数据取不到时整卡进入红色降档状态，绝不留白、绝不编造特征；
 * 3. H1（2-7DTE）当前证据不足（t<3.0），展示「距统计显著还差多少笔」而不是结论。
 */

const MISSING = '—';

const fmtSignedPct = (value: number | null | undefined, digits = 2): string => {
  if (value === null || value === undefined) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value * 100).toFixed(digits)}%`;
};

const fmtScale = (value: number | null | undefined): string =>
  value === null || value === undefined ? MISSING : `×${value.toFixed(2)}`;

const fmtSignedUsd = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
};

const fmtWinRate = (value: number | null | undefined): string =>
  value === null || value === undefined ? MISSING : `${(value * 100).toFixed(0)}%`;

const fmtT = (value: number | null | undefined): string =>
  value === null || value === undefined ? MISSING : value.toFixed(2);

/** as_of 是 ET isoformat；只展示到分钟，不做时区换算。 */
const fmtAsOf = (iso: string): string => `${iso.slice(0, 16).replace('T', ' ')} ET`;

/** 面板只展示证据链上的两个桶：0-1（gate 管的）与 2-7（默认档 / H1）。 */
const EDGE_BUCKET_KEYS = ['0-1', '2-7'] as const;

export const EdgePanelCard: React.FC = () => {
  const [panel, setPanel] = useState<EdgePanelResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPanel(await fetchEdgePanel());
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const gate = panel?.gate ?? null;
  const failClosed = gate?.data_status === 'unavailable';
  const allow01 = gate?.allow_0_1dte === true && !failClosed;
  const discipline = panel?.discipline ?? null;
  const evidence = panel?.edge?.evidence ?? null;
  const expectancy = panel?.edge?.expectancy ?? null;
  const overCap = discipline !== null && discipline.trades_today > discipline.daily_cap;
  const feeRed = discipline?.fee_ratio != null && discipline.fee_ratio > 0.3;

  return (
    <section
      className={`rounded-ds-md border bg-bg-1 ${
        failClosed ? 'border-down-strong/40' : 'border-subtle'
      }`}
      aria-label="今日武器档位"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Edge · Regime gate</div>
          <h2 className="mt-0.5 text-h2 text-text-1">今日武器档位</h2>
        </div>
        <div className="flex items-center gap-2">
          {panel && <span className="font-mono text-mono-xs text-text-3">{fmtAsOf(panel.as_of)}</span>}
          <button
            type="button"
            className="btn-ghost text-body-sm"
            onClick={() => void load()}
            disabled={loading}
          >
            刷新
          </button>
        </div>
      </div>

      <div className="space-y-4 px-4 py-3">
        {loading && !panel && (
          <p className="py-4 text-center text-body-sm text-text-3">加载 Edge 面板中…</p>
        )}

        {error && (
          <p className="text-body-sm text-down-strong" role="alert">
            {error.message}
          </p>
        )}

        {!error && panel && gate && (
          <>
            {/* 档位状态灯：绿=0-1DTE 允许；红=仅 2-7DTE（含 fail closed）。 */}
            <div className="flex flex-wrap items-center gap-3" role="status">
              <span
                className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-body-sm font-medium ${
                  allow01
                    ? 'border-up-strong/25 bg-up-subtle text-up-strong'
                    : 'border-down-strong/25 bg-down-subtle text-down-strong'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`h-2.5 w-2.5 rounded-full ${allow01 ? 'bg-up-strong' : 'bg-down-strong'}`}
                />
                {allow01 ? GATE_OPEN_TEXT : GATE_CLOSED_TEXT}
              </span>
              <span className="text-caption text-text-3">
                默认档 {gate.default_bucket}DTE · gate 仅作防御，绿灯不构成加仓依据
              </span>
            </div>

            {failClosed && (
              <p
                className="rounded-ds-sm border border-down-strong/25 bg-down-subtle px-3 py-2 text-body-sm text-down-strong"
                role="alert"
              >
                {FAIL_CLOSED_BANNER}
              </p>
            )}

            {!allow01 && gate.blocked_by.length > 0 && (
              <ul className="space-y-0.5 text-body-sm text-down-strong" aria-label="降档原因">
                {gate.blocked_by.map((reason) => (
                  <li key={reason}>· {BLOCKED_REASON_TEXT[reason] ?? reason}</li>
                ))}
              </ul>
            )}

            {/* 特征行：全部为开盘前可得信息；取不到时显示缺失，不编造。 */}
            <dl className="flex flex-wrap gap-x-6 gap-y-1 text-body-sm">
              <div className="flex items-baseline gap-2">
                <dt className="text-text-3">QQQ 20日动量</dt>
                <dd className="font-mono text-mono-sm tabular-nums text-text-1">
                  {fmtSignedPct(gate.features.mom_q_20d)}
                </dd>
              </div>
              <div className="flex items-baseline gap-2">
                <dt className="text-text-3">SOXX 20日动量</dt>
                <dd className="font-mono text-mono-sm tabular-nums text-text-1">
                  {fmtSignedPct(gate.features.mom_s_20d)}
                </dd>
              </div>
              <div className="flex items-baseline gap-2">
                <dt className="text-text-3">仓位缩放 (MM ×0.25–×1.0)</dt>
                <dd className="font-mono text-mono-sm tabular-nums text-text-1">
                  {fmtScale(gate.features.mm_scale)}
                </dd>
              </div>
            </dl>

            {/* Edge 分桶：只列 0-1 / 2-7 两个证据桶。 */}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[360px] text-body-sm">
                <thead>
                  <tr className="border-b border-subtle text-caption text-text-3">
                    <th className="px-3 py-1.5 text-left font-normal">DTE 桶</th>
                    <th className="px-3 py-1.5 text-right font-normal">笔数</th>
                    <th className="px-3 py-1.5 text-right font-normal">单笔均值</th>
                    <th className="px-3 py-1.5 text-right font-normal">胜率</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[color:var(--border-subtle)]">
                  {EDGE_BUCKET_KEYS.map((key) => {
                    const bucket = panel.edge.buckets[key];
                    return (
                      <tr key={key}>
                        <td className="px-3 py-1.5 text-left font-mono text-mono-xs text-text-1">
                          {key}DTE
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-mono-xs tabular-nums text-text-1">
                          {bucket ? bucket.n.toLocaleString() : MISSING}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-mono-xs tabular-nums text-text-1">
                          {fmtSignedUsd(bucket?.mean)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-mono-xs tabular-nums text-text-1">
                          {fmtWinRate(bucket?.win_rate)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {evidence && (
              <p className="text-body-sm text-text-2">
                H1 证据状态: t={fmtT(evidence.h1_t_stat)} / 门槛 {evidence.hlz_hurdle.toFixed(1)} —{' '}
                <span
                  className={
                    evidence.status === 'significant' ? 'text-up-strong' : 'text-warn-strong'
                  }
                >
                  {evidence.status === 'significant' ? '显著' : '证据不足'}
                </span>
                {expectancy && (
                  <span className="ml-2 text-text-3">
                    · 全部已平仓期望 {fmtSignedUsd(expectancy.mean)} · CI95 [
                    {fmtSignedUsd(expectancy.ci95_low)}, {fmtSignedUsd(expectancy.ci95_high)}]
                    （n={expectancy.n.toLocaleString()}）
                  </span>
                )}
                {evidence.status !== 'significant' && evidence.n_remaining != null && (
                  <span className="ml-2 text-text-3">
                    · 距统计显著还差 {evidence.n_remaining.toLocaleString()} 笔
                  </span>
                )}
              </p>
            )}

            {/* 纪律行：R2 频率硬顶（超限标红，不禁止）与 R3 费率红灯。 */}
            {discipline && (
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-subtle pt-3 text-body-sm">
                <span className={overCap ? 'text-down-strong' : 'text-text-1'}>
                  今日{' '}
                  <span className="font-mono tabular-nums">
                    {discipline.trades_today}/{discipline.daily_cap}
                  </span>{' '}
                  笔
                  {overCap && <span className="ml-1 text-caption">超出 R2 频率硬顶</span>}
                </span>
                <span className={feeRed ? 'text-down-strong' : 'text-text-1'}>
                  本月费率{' '}
                  <span className="font-mono tabular-nums">
                    {discipline.fee_ratio == null
                      ? MISSING
                      : `${(discipline.fee_ratio * 100).toFixed(0)}%`}
                  </span>
                  {feeRed && <span className="ml-1 text-caption">超过 30% 红线 (R3)</span>}
                </span>
                <span className="text-caption text-text-3">
                  本月费用 {fmtSignedUsd(discipline.month_fees)} / 毛利{' '}
                  {fmtSignedUsd(discipline.month_gross)}
                </span>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
};

export default EdgePanelCard;
