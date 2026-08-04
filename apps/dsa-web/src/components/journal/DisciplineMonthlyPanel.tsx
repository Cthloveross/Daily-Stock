import type React from 'react';
import { Fragment, useCallback, useEffect, useState } from 'react';
import { fetchPersonalEdge } from '../../api/journal';
import { parseApiError, type ParsedApiError } from '../../api/error';
import type { PersonalEdgeDisciplineMonth, PersonalEdgeResponse } from '../../types/journal';
import { Tooltip } from '../common/Tooltip';

/**
 * 「规模与频率」：模式观察的同层兄弟区块，把每美元回报（真账）与仓位、频率、
 * 费用门槛、剔除尾部赢家后的读数按月摊开——数据来自
 * `GET /journal/v2/personal-edge` 的 `discipline` 块（当前默认 build 实时重算，
 * 前端不硬编码任何数字）。
 *
 * 口径订正（2026-08-04 晚）：此前把月度每美元回报画成一条连续趋势线，暗示
 * 「边际在衰减」——**其中约 75% 是口径假象**。build #3 有 245 笔回合由汇总
 * ORDER 行构建（`evidence_summary_json.fill_allocations == 0`，无明细成交），
 * 恰好是 3 月 + 4/1–4/20；这个 4/20–21 边界≈券商明细成交保留窗口（CSV 导出日前
 * 约 90 天），不是行情或行为边界。汇总口径的风险金额分母被低估——管线自身把该
 * 金额标记为 `audit_only_not_execution_cash_flow`——**同在 4 月内**对照即可看清：
 * 汇总口径毛每美元 9.53%、明细口径 2.42%（同一人、同一月、同一行情）。
 *
 * 因此本表按 `basisBreak` **断开显示**：断裂月份单独成组、显式说明不可比，绝不
 * 与后续月份连成一条线。在可比窗口（4/20–7/31）上毛每美元 2.42 / 2.58 / 2.00 /
 * 1.22%，两两置换检验全部 p ≥ 0.756、Kruskal p=0.887——**没有可检出的衰减**。
 *
 * 真正变了的是：单笔风险 +68%（$6,760 → $11,340）而单笔美元盈亏基本持平
 * （$163 → $138）；以及恒定的费用门槛（约 119–138 bp 权利金），在约 1.2% 毛边际
 * 下让 7 月净边际变成 −0.19%。所以本表把「费用门槛」与「毛每美元」并排显示：
 * 毛口径低于门槛，净口径必为负。
 *
 * 诚实边界：任何分母不足的比率显示「标缺」并给出原因（走共享 Tooltip +
 * aria-label，仓库 UI 治理禁用原生 title）。描述统计，不构成建议。
 */

const MISSING = '标缺';

const fmtUsd = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return MISSING;
  const sign = value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
};

const fmtSignedUsd = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
};

const fmtPerDollar = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value * 100).toFixed(2)}%`;
};

/** 费用门槛永远是成本，不带正负号——它只是一道必须跨过去的线。 */
const fmtToll = (value: number | null | undefined): string =>
  value === null || value === undefined ? MISSING : `${(value * 100).toFixed(2)}%`;

const fmtShare = (value: number | null | undefined): string =>
  value === null || value === undefined ? MISSING : `${(value * 100).toFixed(0)}%`;

const fmtTrades = (value: number | null | undefined): string =>
  value === null || value === undefined
    ? MISSING
    : value.toLocaleString('en-US', { maximumFractionDigits: 1 });

/** 分母不足的读数用共享 Tooltip 挂原因：屏幕上是「标缺」，悬停能读到为什么。
 * （原生 title 被仓库 UI 治理测试禁用，必须走 Tooltip 组件。） */
const Cell: React.FC<{ text: string; reason?: string | null }> = ({ text, reason }) => (
  <td className="px-3 py-1.5 text-right font-mono text-mono-xs tabular-nums text-text-1">
    {text === MISSING && reason ? (
      <Tooltip focusable content={reason}>
        <span aria-label={reason}>{text}</span>
      </Tooltip>
    ) : (
      <span>{text}</span>
    )}
  </td>
);

/** 口径断裂行内标记与断口文案（测试与文档共用同一份常量）。 */
export const BASIS_BREAK_BADGE = '口径断裂 · 不可与后续月份比较';
export const SERIES_BREAK_TEXT =
  '── 口径断口：以上为汇总 ORDER 口径（风险金额分母被低估），以下为明细成交口径 —— 两组每美元读数不可比、不可连成一条趋势线 ──';
/** 毛口径不及过路费＝净口径必为负，一眼可见。 */
export const BELOW_TOLL_BADGE = '不及门槛';

const COLUMN_COUNT = 14;

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
  const excludeTopN = discipline?.excludeTopN ?? discipline?.bodyTrimCount ?? 5;
  const brokenMonths = months.filter((row) => row.basisBreak === true);
  const aggregateOnlyTotal = brokenMonths.reduce(
    (sum, row) => sum + (row.aggregateOnlyCount ?? 0),
    0,
  );
  const hasCleanMonth = months.some((row) => row.basisBreak !== true);
  /** 断裂组的最后一行之后插入显式断口——绝不让两组连成一条趋势线。 */
  const breakAfterMonth =
    brokenMonths.length > 0 && hasCleanMonth
      ? brokenMonths[brokenMonths.length - 1].month
      : null;

  const renderRow = (row: PersonalEdgeDisciplineMonth) => {
    const broken = row.basisBreak === true;
    const gross = row.grossPctOfPremiumAtRisk ?? null;
    const toll = row.feePctOfPremiumAtRisk ?? null;
    const belowToll = gross !== null && toll !== null && gross <= toll;
    return (
      <tr
        key={row.month}
        data-basis-break={broken ? 'true' : 'false'}
        className={broken ? 'bg-bg-2/50' : undefined}
      >
        <td className="px-3 py-1.5 text-left text-text-1">
          <span className="font-mono text-mono-xs">{row.month}</span>
          {broken && (
            <Tooltip focusable content={row.basisBreakReason ?? BASIS_BREAK_BADGE}>
              <span
                className="ml-2 text-caption text-warn-strong"
                aria-label={`${row.month} ${BASIS_BREAK_BADGE}：${row.basisBreakReason ?? ''}`}
              >
                {BASIS_BREAK_BADGE}
              </span>
            </Tooltip>
          )}
          {!broken && row.hasReconstructedFills && (
            <span
              className="ml-2 text-caption text-text-3"
              aria-label={`${row.month} 含 has_exact_fill_times=0 的回合，但口径仍为明细成交构建`}
            >
              含重建时点（口径仍为明细成交）
            </span>
          )}
        </td>
        <Cell text={row.n.toLocaleString()} />
        <Cell text={fmtTrades(row.tradesPerDay)} reason={row.tradesPerDayReason} />
        <Cell text={fmtUsd(row.medianPremiumAtRisk)} reason={row.premiumReason} />
        <Cell text={fmtUsd(row.totalPremiumAtRisk)} reason={row.premiumReason} />
        <Cell text={fmtPerDollar(row.pnlPerDollarRisked)} reason={row.pnlPerDollarRiskedReason} />
        <td className="px-3 py-1.5 text-right font-mono text-mono-xs tabular-nums text-text-1">
          {gross === null ? (
            <Tooltip focusable content={row.grossPctOfPremiumAtRiskReason ?? MISSING}>
              <span aria-label={row.grossPctOfPremiumAtRiskReason ?? MISSING}>{MISSING}</span>
            </Tooltip>
          ) : (
            <>
              <span>{fmtPerDollar(gross)}</span>
              {belowToll && (
                <span
                  className="ml-1 text-caption text-warn-strong"
                  aria-label={`毛每美元 ${fmtPerDollar(gross)} 不及费用门槛 ${fmtToll(toll)}，净口径必为负`}
                >
                  {BELOW_TOLL_BADGE}
                </span>
              )}
            </>
          )}
        </td>
        <Cell text={fmtToll(toll)} reason={row.feePctOfPremiumAtRiskReason} />
        <Cell text={fmtPerDollar(row.grossPctExcludingTopN)} reason={row.excludingTopNReason} />
        <Cell text={fmtSignedUsd(row.medianEpisodePnl)} />
        <Cell text={fmtSignedUsd(row.bodyPnl)} reason={row.bodyPnlReason} />
        <Cell text={fmtShare(row.zeroDteShare)} reason={row.zeroDteReason} />
        <Cell text={fmtShare(row.fillDetailedShare)} />
        <Cell text={fmtShare(row.exactFillShare)} />
      </tr>
    );
  };

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
              每美元回报（净）＝ 当月净盈亏 ÷ 当月开仓风险金额合计（|opening_cash_flow|）；毛每美元
              ＝ 净 ＋ 费用；费用门槛 ＝ 当月费用 ÷ 同一分母——
              <strong className="text-text-2">毛口径低于门槛，净口径必为负</strong>。 「剔除最好{' '}
              {excludeTopN} 笔」为毛口径，与本体盈亏（去掉最好/最差各 {discipline.bodyTrimCount}{' '}
              笔的美元合计）同一个 N，样本 &lt;{discipline.bodyMinEpisodeCount} 笔时不成立。
              月度口径与上方一致（ET≈UTC−4 近似）。
            </p>

            {brokenMonths.length > 0 && (
              <p
                className="rounded-ds-sm border border-subtle px-3 py-2 text-caption text-text-2"
                role="note"
                aria-label="口径断裂说明"
              >
                <strong className="text-warn-strong">口径断裂：</strong>
                {brokenMonths.map((row) => row.month).join('、')} 含由<strong>汇总 ORDER 行</strong>
                构建的回合（{aggregateOnlyTotal.toLocaleString()} 笔，
                <code className="mx-1 font-mono text-mono-xs">
                  evidence_summary_json.fill_allocations = 0
                </code>
                ，无明细成交）。这些回合的风险金额分母被低估——管线自身把该金额标记为
                <code className="mx-1 font-mono text-mono-xs">
                  audit_only_not_execution_cash_flow
                </code>
                （仅供审计，不是执行现金流）——所以
                <strong className="text-text-1">
                  这些月份的每美元口径与后续月份不可比，本表按组断开显示，不构成一条趋势线
                </strong>
                。同在 2026-04 内做对照即可看清这是分母假象而非边际衰减：汇总口径毛每美元{' '}
                <strong>9.53%</strong>，明细口径 <strong>2.42%</strong>
                （同一人、同一月、同一行情）。断点 4/20–21 ≈ 券商明细成交保留窗口（CSV
                导出日前约 90 天），不是行情或行为边界。
              </p>
            )}

            <div className="overflow-x-auto">
              <table className="w-full min-w-[1180px] text-body-sm">
                <thead>
                  <tr className="border-b border-subtle text-caption text-text-3">
                    <th className="px-3 py-1.5 text-left font-normal">月份</th>
                    <th className="px-3 py-1.5 text-right font-normal">笔数</th>
                    <th className="px-3 py-1.5 text-right font-normal">日均笔数</th>
                    <th className="px-3 py-1.5 text-right font-normal">中位仓位</th>
                    <th className="px-3 py-1.5 text-right font-normal">风险金额合计</th>
                    <th className="px-3 py-1.5 text-right font-normal">每美元回报</th>
                    <th className="px-3 py-1.5 text-right font-normal">毛每美元</th>
                    <th className="px-3 py-1.5 text-right font-normal">费用门槛</th>
                    <th className="px-3 py-1.5 text-right font-normal">
                      剔除最好 {excludeTopN} 笔
                    </th>
                    <th className="px-3 py-1.5 text-right font-normal">中位盈亏</th>
                    <th className="px-3 py-1.5 text-right font-normal">本体盈亏</th>
                    <th className="px-3 py-1.5 text-right font-normal">0DTE 占比</th>
                    <th className="px-3 py-1.5 text-right font-normal">明细成交占比</th>
                    <th className="px-3 py-1.5 text-right font-normal">精确成交占比</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[color:var(--border-subtle)]">
                  {months.map((row) => (
                    <Fragment key={row.month}>
                      {renderRow(row)}
                      {row.month === breakAfterMonth && (
                        <tr data-series-break="true">
                          <td
                            colSpan={COLUMN_COUNT}
                            className="border-y-2 border-dashed border-strong px-3 py-1.5 text-caption text-text-3"
                          >
                            {SERIES_BREAK_TEXT}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>

            <p className="text-caption text-text-3">
              「剔除最好 {excludeTopN} 笔」＝<strong className="text-text-2">
                剔除最好的 {excludeTopN} 笔后的每美元回报
              </strong>
              （毛口径）：撤走当月最好的 {excludeTopN}{' '}
              笔之后还剩什么。分子分母取同一子集（风险金额已知的回合），按净盈亏排序去尾。
            </p>

            {currentWindow && (
              <p className="text-caption text-text-3">
                当前窗口（近 {currentWindow.requestedTradingDays} 个交易日 ·{' '}
                {currentWindow.startDate ?? '—'} → {currentWindow.endDate ?? '—'}）：
                {currentWindow.n.toLocaleString()} 笔 · 日均 {fmtTrades(currentWindow.tradesPerDay)}{' '}
                笔 · 中位仓位 {fmtUsd(currentWindow.medianPremiumAtRisk)} · 每美元回报{' '}
                {fmtPerDollar(currentWindow.pnlPerDollarRisked)} · 毛每美元{' '}
                {fmtPerDollar(currentWindow.grossPctOfPremiumAtRisk)} · 费用门槛{' '}
                {fmtToll(currentWindow.feePctOfPremiumAtRisk)} · 剔除最好 {excludeTopN} 笔{' '}
                {fmtPerDollar(currentWindow.grossPctExcludingTopN)} · 本体盈亏{' '}
                {fmtSignedUsd(currentWindow.bodyPnl)}
                {currentWindow.basisBreak === true && (
                  <span className="ml-1 text-warn-strong">· {BASIS_BREAK_BADGE}</span>
                )}
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
