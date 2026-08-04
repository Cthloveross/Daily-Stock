import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { fetchPersonalEdge } from '../../api/journal';
import { parseApiError, type ParsedApiError } from '../../api/error';
import type { PersonalEdgeResponse } from '../../types/journal';
import { Tooltip } from '../common/Tooltip';

/**
 * 「规模与频率」：模式观察的同层兄弟区块，把每美元回报（真账）与仓位、频率、
 * 本体/尾部拆解按月摊开——数据来自 `GET /journal/v2/personal-edge` 的
 * `discipline` 块（当前默认 build 实时重算，前端不硬编码任何数字）。
 *
 * 取证背景：用户自己 1,554 笔期权回合的全样本研究显示，4→7 月的衰减不是
 * 进场几何变差（追高 52.0%→56.6%，χ² p=0.105）、不是行情跟随变差（前瞻
 * 30 分钟 MFE 中位 0.370→0.341，Kruskal p=0.337）、也不是止损纪律变差
 * （亏损中位 −22.6%→−24.4% 权利金），而是每美元回报塌了约 24 倍、中位仓位
 * 2.4 倍、日均笔数 +44%、盈亏集中到尾部赢家。
 *
 * 诚实边界：任何分母不足的比率显示「标缺」并给出原因；`exactFillShare < 1`
 * 的月份显式标注「成交明细为重建，时点仅供参考」。描述统计，不构成建议。
 */

const MISSING = '标缺';

const fmtUsd = (value: number | null): string => {
  if (value === null) return MISSING;
  const sign = value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
};

const fmtSignedUsd = (value: number | null): string => {
  if (value === null) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
};

const fmtPerDollar = (value: number | null): string => {
  if (value === null) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value * 100).toFixed(2)}%`;
};

const fmtShare = (value: number | null): string =>
  value === null ? MISSING : `${(value * 100).toFixed(0)}%`;

const fmtTrades = (value: number | null): string =>
  value === null ? MISSING : value.toLocaleString('en-US', { maximumFractionDigits: 1 });

/** 分母不足的读数用共享 Tooltip 挂原因：屏幕上是「标缺」，悬停能读到为什么。
 * （原生 title 被仓库 UI 治理测试禁用，必须走 Tooltip 组件。） */
const Cell: React.FC<{ text: string; reason?: string | null }> = ({ text, reason }) => (
  <td className="px-3 py-1.5 text-right font-mono text-mono-xs tabular-nums text-text-1">
    {text === MISSING && reason ? (
      <Tooltip content={reason}>
        <span aria-label={reason}>{text}</span>
      </Tooltip>
    ) : (
      <span>{text}</span>
    )}
  </td>
);

export const DisciplineMonthlyPanel: React.FC = () => {
  const [edge, setEdge] = useState<PersonalEdgeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    setError(null);
    try {
      setEdge(await fetchPersonalEdge(refresh));
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const discipline = edge?.discipline ?? null;
  const months = discipline?.monthly ?? [];
  const currentWindow = discipline?.currentWindow ?? null;

  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1" aria-label="规模与频率">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">
            Size &amp; frequency
          </div>
          <h2 className="mt-0.5 text-h2 text-text-1">规模与频率</h2>
        </div>
        <div className="flex items-center gap-2">
          {edge?.dataState === 'ready' && (
            <span className="font-mono text-mono-xs text-text-3">
              Build #{edge.buildId} · {edge.sourceKind}
            </span>
          )}
          <button
            type="button"
            className="btn-ghost text-body-sm"
            onClick={() => void load(true)}
            disabled={loading}
          >
            刷新
          </button>
        </div>
      </div>

      <div className="px-4 py-3">
        {loading && !edge && (
          <p className="py-4 text-center text-body-sm text-text-3">重算规模与频率读数中…</p>
        )}

        {error && (
          <p className="text-body-sm text-down-strong" role="alert">
            {error.message}
          </p>
        )}

        {!error && edge?.dataState === 'not_built' && (
          <p className="text-body-sm text-text-3">
            还没有 Episode 构建。导入证据并生成构建后，这里会按月摊开每美元回报、仓位与频率。
          </p>
        )}

        {!error && edge?.dataState === 'ready' && !discipline && (
          <p className="text-body-sm text-text-3">规模与频率读数{MISSING}（端点未返回该区块）。</p>
        )}

        {!error && discipline && (
          <div className="space-y-3">
            <p className="text-caption text-text-3">
              每美元回报 ＝ 当月净盈亏 ÷ 当月开仓风险金额合计（|opening_cash_flow|）；本体盈亏 ＝
              去掉当月最好/最差各 {discipline.bodyTrimCount} 笔后的合计，样本 &lt;
              {discipline.bodyMinEpisodeCount} 笔时不成立。月度口径与上方一致（ET≈UTC−4 近似）。
            </p>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-body-sm">
                <thead>
                  <tr className="border-b border-subtle text-caption text-text-3">
                    <th className="px-3 py-1.5 text-left font-normal">月份</th>
                    <th className="px-3 py-1.5 text-right font-normal">笔数</th>
                    <th className="px-3 py-1.5 text-right font-normal">日均笔数</th>
                    <th className="px-3 py-1.5 text-right font-normal">中位仓位</th>
                    <th className="px-3 py-1.5 text-right font-normal">风险金额合计</th>
                    <th className="px-3 py-1.5 text-right font-normal">每美元回报</th>
                    <th className="px-3 py-1.5 text-right font-normal">中位盈亏</th>
                    <th className="px-3 py-1.5 text-right font-normal">本体盈亏</th>
                    <th className="px-3 py-1.5 text-right font-normal">0DTE 占比</th>
                    <th className="px-3 py-1.5 text-right font-normal">精确成交占比</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[color:var(--border-subtle)]">
                  {months.map((row) => (
                    <tr key={row.month}>
                      <td className="px-3 py-1.5 text-left text-text-1">
                        <span className="font-mono text-mono-xs">{row.month}</span>
                        {row.hasReconstructedFills && (
                          <span
                            className="ml-2 text-caption text-warn-strong"
                            aria-label={`${row.month} 成交明细为重建`}
                          >
                            成交明细为重建，时点仅供参考
                          </span>
                        )}
                      </td>
                      <Cell text={row.n.toLocaleString()} />
                      <Cell text={fmtTrades(row.tradesPerDay)} reason={row.tradesPerDayReason} />
                      <Cell text={fmtUsd(row.medianPremiumAtRisk)} reason={row.premiumReason} />
                      <Cell text={fmtUsd(row.totalPremiumAtRisk)} reason={row.premiumReason} />
                      <Cell
                        text={fmtPerDollar(row.pnlPerDollarRisked)}
                        reason={row.pnlPerDollarRiskedReason}
                      />
                      <Cell text={fmtSignedUsd(row.medianEpisodePnl)} />
                      <Cell text={fmtSignedUsd(row.bodyPnl)} reason={row.bodyPnlReason} />
                      <Cell text={fmtShare(row.zeroDteShare)} reason={row.zeroDteReason} />
                      <Cell text={fmtShare(row.exactFillShare)} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {currentWindow && (
              <p className="text-caption text-text-3">
                当前窗口（近 {currentWindow.requestedTradingDays} 个交易日 ·{' '}
                {currentWindow.startDate ?? '—'} → {currentWindow.endDate ?? '—'}）：
                {currentWindow.n.toLocaleString()} 笔 · 日均 {fmtTrades(currentWindow.tradesPerDay)} 笔 · 中位仓位{' '}
                {fmtUsd(currentWindow.medianPremiumAtRisk)} · 每美元回报{' '}
                {fmtPerDollar(currentWindow.pnlPerDollarRisked)} · 本体盈亏 {fmtSignedUsd(currentWindow.bodyPnl)}
              </p>
            )}

            <ul className="border-t border-subtle pt-2 text-caption text-text-3">
              {(edge?.limitations ?? []).map((line) => (
                <li key={line}>· {line}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
};

export default DisciplineMonthlyPanel;
