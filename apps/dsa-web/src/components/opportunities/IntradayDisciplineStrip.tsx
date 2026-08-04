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

/** 最早一个该读数可得的月份＝用户自己的基线（不硬编码任何数字）。 */
function baseline(
  months: PersonalEdgeDisciplineMonth[],
  pick: (row: PersonalEdgeDisciplineMonth) => number | null,
  format: (value: number | null) => string,
): string | null {
  for (const row of months) {
    const value = pick(row);
    if (value !== null) return `${monthLabel(row.month)} ${format(value)}`;
  }
  return null;
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
 * 为什么是这四个读数：对用户自己 1,554 笔期权回合的全样本取证显示，4→7 月
 * 的衰减不是进场几何（追高比例 52.0%→56.6%，χ² p=0.105）、不是行情跟随
 * （前瞻 30 分钟 MFE 中位 0.370→0.341，Kruskal p=0.337）、也不是止损纪律
 * （亏损中位 −22.6%→−24.4% 权利金）变差——真正变的是每美元回报塌了约 24 倍
 * （5.83%→0.24%）、中位仓位 2.4 倍、日均笔数 +44%、盈亏集中到尾部赢家。
 * 这些恰恰是工作台此前完全不显示的量。
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
            <span className="text-caption text-text-3">成交明细含重建 · 时点仅供参考</span>
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
