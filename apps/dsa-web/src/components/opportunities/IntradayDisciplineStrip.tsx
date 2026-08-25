import { usePersonalEdge } from '../../hooks/usePersonalEdge';
import type { PersonalEdgeDisciplineMonth } from '../../types/journal';
import { Tooltip } from '../common/Tooltip';
import { formatSignedCompactUsd, formatSignedPercent } from './intradayFormat';

/** 缺席即缺席：任何分母不足或端点不可得的读数一律显式标缺，绝不以 0 冒充。 */
const MISSING = '标缺';

function formatUsd(value: number | null): string {
  if (value === null) return MISSING;
  const sign = value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function formatPerDollar(value: number | null): string {
  if (value === null) return MISSING;
  return formatSignedPercent(value * 100);
}

/** 费用门槛是成本，不带正负号——它只是一道必须跨过去的线。 */
function formatToll(value: number | null): string {
  if (value === null) return MISSING;
  return `${(value * 100).toFixed(2)}%`;
}

function formatTrades(value: number | null): string {
  if (value === null) return MISSING;
  return `${value.toLocaleString('en-US', { maximumFractionDigits: 1 })} 笔`;
}

/** 「2026-04」→「4月」：基线注脚只写月份，行内已有 as-of 完整日期。 */
function monthLabel(month: string): string {
  const parts = month.split('-');
  const value = Number(parts[1]);
  return Number.isFinite(value) ? `${value}月` : month;
}

/**
 * 最早一个该读数可得、且**口径未断裂**的月份＝用户自己的基线。
 *
 * 为什么要跳过 `basisBreak` 的月份：3 月与 4/1–4/20 的回合由汇总 ORDER 行构建
 * （`evidence_summary_json.fill_allocations = 0`），风险金额分母被低估——管线自身
 * 标记 `audit_only_not_execution_cash_flow`。拿它当基线等于让「现在」去和一个被
 * 污染的数字比：同在 4 月内，汇总口径毛每美元 9.53%、明细口径 2.42%。因此这里
 * 只取最早一个**全明细成交**的月份，并在文案里写明取的是哪个月；一个干净月份
 * 都没有时**宁可不显示基线**，绝不退回污染基线。
 */
function baseline(
  months: PersonalEdgeDisciplineMonth[],
  pick: (row: PersonalEdgeDisciplineMonth) => number | null,
  format: (value: number | null) => string,
): string | null {
  for (const row of months) {
    if (row.basisBreak === true) continue;
    const value = pick(row);
    if (value !== null && value !== undefined) {
      return `${monthLabel(row.month)} ${format(value)}`;
    }
  }
  return null;
}

/** 基线是否因为口径断裂而被跳过（用于脚注说明取的是最早的全明细月份）。 */
function skippedContaminatedBaseline(
  months: PersonalEdgeDisciplineMonth[],
): boolean {
  return months.some((row) => row.basisBreak === true);
}

function Chip({
  label,
  value,
  caption,
  reason,
}: {
  label: string;
  value: string;
  caption: string | null;
  reason: string | null;
}) {
  const body = (
    <span className="flex items-baseline gap-1.5" aria-label={label}>
      <span className="text-caption text-text-3">{label}</span>
      <span className="font-mono text-mono-sm tabular-nums text-text-1">{value}</span>
      {caption && <span className="text-caption text-text-3">（{caption}）</span>}
    </span>
  );
  return reason ? <Tooltip content={reason}>{body}</Tooltip> : body;
}

/**
 * 规模与频率纪律条：把「每美元回报」这一真账与仓位、频率、本体盈亏并排放在
 * 盘面上，读数取当前默认 build 的**近 20 个交易日**窗口，注脚是用户自己最早
 * 一个月的同口径基线——所有数字都来自端点实时重算，前端不硬编码任何基准。
 *
 * 为什么是这些读数：对用户自己期权回合的全样本取证显示，4→7 月的「衰减」不是
 * 进场几何（追高比例 52.0%→56.6%，χ² p=0.105）、不是行情跟随（前瞻 30 分钟 MFE
 * 中位 0.370→0.341，Kruskal p=0.337）、也不是止损纪律（亏损中位 −22.6%→−24.4%
 * 权利金）变差。
 *
 * 口径订正（2026-08-04 晚）：**这条「衰减」本身约 75% 是口径假象**。3 月与
 * 4/1–4/20 的回合由汇总 ORDER 行构建（`fill_allocations = 0`），风险金额分母被
 * 低估——管线自身标记 `audit_only_not_execution_cash_flow`。同在 4 月内对照：
 * 汇总口径毛每美元 9.53% vs 明细口径 2.42%。在可比窗口（4/20–7/31）上两两置换
 * 检验 p ≥ 0.756、Kruskal p=0.887，**没有可检出的边际衰减**。
 *
 * 因此基线**绝不与被污染的月份比较**：`baseline()` 跳过所有 `basisBreak` 月份，
 * 只取最早一个全明细成交月份并在脚注写明取的是哪个月；一个干净月份都没有时宁可
 * 不显示基线。真正变了的是单笔风险 +68% 而单笔美元盈亏基本持平，以及恒定的
 * 费用门槛（约 119–138 bp）——所以「毛每美元」与「费用门槛」并排显示：毛口径
 * 低于门槛，净口径必为负。
 *
 * 定位：镜子，不是警报——中性排版、无涨跌色、无「你应该」，不隐藏任何数据，
 * 不进入任何排序或信号。limitations 由端点原文透传到 tooltip。
 */
export function IntradayDisciplineStrip() {
  const view = usePersonalEdge();
  const data = view.state === 'ready' ? view.data : null;
  const discipline = data?.discipline ?? null;
  const currentWindow = discipline?.currentWindow ?? null;
  const months = discipline?.monthly ?? [];

  // 基线只取最早一个「全明细成交」月份；一个都没有时宁可不显示基线。
  const cleanBaselineMonth = months.find((row) => row.basisBreak !== true) ?? null;
  const contaminatedSkipped = skippedContaminatedBaseline(months);
  const windowGross = currentWindow?.grossPctOfPremiumAtRisk ?? null;
  const windowToll = currentWindow?.feePctOfPremiumAtRisk ?? null;
  const belowToll = windowGross !== null && windowToll !== null && windowGross <= windowToll;

  const limitations = data?.limitations ?? [];
  // limitations 原文透传：tooltip 与 aria-label 同一份文本，读屏与悬停一致。
  const tooltip = [
    `近 ${currentWindow?.requestedTradingDays ?? 20} 个交易日窗口（build 内实际存在的交易日）· 描述统计，非建议`,
    ...limitations.map((line) => `· ${line}`),
  ].join('\n');

  const asOf = currentWindow?.endDate ?? data?.lastClosedAt?.slice(0, 10) ?? null;

  return (
    <section
      aria-label="规模与频率"
      className="flex flex-wrap items-center gap-x-5 gap-y-1.5 rounded-ds-sm border border-subtle bg-bg-1 px-4 py-2"
    >
      <Tooltip content={tooltip} focusable contentClassName="whitespace-pre-line">
        <span className="text-caption font-medium text-text-2" aria-label={tooltip}>
          规模与频率 · 近 {currentWindow?.requestedTradingDays ?? 20} 个交易日
        </span>
      </Tooltip>

      {view.state === 'loading' && !currentWindow && (
        <span className="text-caption text-text-3">读取中…</span>
      )}

      {view.state !== 'loading' && !currentWindow && (
        <span className="text-caption text-text-3">
          规模与频率 {MISSING}（Journal 未构建或端点不可得）
        </span>
      )}

      {currentWindow && (
        <>
          <Chip
            label="每美元回报"
            value={formatPerDollar(currentWindow.pnlPerDollarRisked)}
            caption={baseline(months, (row) => row.pnlPerDollarRisked, formatPerDollar)}
            reason={currentWindow.pnlPerDollarRiskedReason}
          />
          <Chip
            label="毛每美元"
            value={formatPerDollar(currentWindow.grossPctOfPremiumAtRisk ?? null)}
            caption={baseline(
              months,
              (row) => row.grossPctOfPremiumAtRisk ?? null,
              formatPerDollar,
            )}
            reason={currentWindow.grossPctOfPremiumAtRiskReason ?? null}
          />
          <Chip
            label="费用门槛"
            value={formatToll(currentWindow.feePctOfPremiumAtRisk ?? null)}
            caption={
              belowToll
                ? '毛口径不及门槛 · 净口径为负'
                : baseline(months, (row) => row.feePctOfPremiumAtRisk ?? null, formatToll)
            }
            reason={
              currentWindow.feePctOfPremiumAtRiskReason
              ?? '恒定过路费＝窗口费用 ÷ 同一风险金额分母；毛口径低于门槛，净口径必为负'
            }
          />
          <Chip
            label="日均"
            value={formatTrades(currentWindow.tradesPerDay)}
            caption={baseline(months, (row) => row.tradesPerDay, formatTrades)}
            reason={currentWindow.tradesPerDayReason}
          />
          <Chip
            label="中位仓位"
            value={formatUsd(currentWindow.medianPremiumAtRisk)}
            caption={baseline(months, (row) => row.medianPremiumAtRisk, formatUsd)}
            reason={currentWindow.premiumReason}
          />
          <Chip
            label="本体盈亏"
            value={
              currentWindow.bodyPnl === null ? MISSING : formatSignedCompactUsd(currentWindow.bodyPnl)
            }
            caption={baseline(
              months,
              (row) => row.bodyPnl,
              (value) => (value === null ? MISSING : formatSignedCompactUsd(value)),
            )}
            reason={
              currentWindow.bodyPnlReason
              ?? `去掉窗口内最好/最差各 ${discipline?.bodyTrimCount ?? 5} 笔后的合计`
            }
          />
          {currentWindow.hasReconstructedFills && (
            // 时点重建 ≠ 口径断裂：这句只讲 has_exact_fill_times，可比性看下一句。
            <span className="text-caption text-text-3">成交明细含重建 · 时点仅供参考</span>
          )}
          {contaminatedSkipped && (
            <span className="text-caption text-text-3">
              {cleanBaselineMonth
                ? `基线取最早的全明细成交月份（${monthLabel(cleanBaselineMonth.month)}）· 更早月份由汇总 ORDER 行构建、分母被低估，不可比`
                : '基线不显示 · 早期月份均由汇总 ORDER 行构建（分母被低估），无可比基线'}
            </span>
          )}
          {currentWindow.basisBreak === true && (
            <span className="text-caption text-warn-strong">
              本窗口口径断裂 · 含汇总 ORDER 行构建的回合，每美元读数不可与全明细区间比较
            </span>
          )}
        </>
      )}

      <span className="ml-auto text-caption text-text-3">
        基于已发布证据 build #{data?.buildId ?? '—'} · 截至 {asOf ?? '—'}
      </span>
    </section>
  );
}

export default IntradayDisciplineStrip;
