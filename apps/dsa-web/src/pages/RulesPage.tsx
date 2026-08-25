import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchPlaybook, fetchRulesEvidence } from '../api/journal';
import { parseApiError, type ParsedApiError } from '../api/error';
import { InfoHint } from '../components/common/InfoHint';
import type {
  PlaybookListResponse,
  RulesEvidenceCell,
  RulesEvidenceResponse,
} from '../types/journal';

/**
 * 「交易纪律」页 —— 每一条纪律和它的证据放在同一个地方。
 *
 * 两个来源，一个页面：
 *  1. Playbook（GET /journal/v2/playbook）——规则原文与 候选/已采纳 状态，
 *     始终与 /journal 上的提升-退役流程同步，本页只读、不改写；
 *  2. 证据表（GET /journal/v2/rules-evidence）——支撑这些规则的数字。
 *
 * **本页不做任何统计**。分档边界、剔尾 N、严重度分位数、费用计算器的默认
 * 锚点全部来自后端响应；页面里唯一的算术是费用计算器，且它只是把后端给的
 * 费率乘以用户当场输入的两个数——输入只存在于组件 state，不落库、不上报，
 * 系统既不知道也不猜用户的账户规模。
 */

/** 后端横幅缺席时的回退文案（与后端默认同句，逐字挂 ⓘ tooltip）。 */
const RULES_BANNER_FALLBACK =
  '这一页是你自己的历史统计，不是建议；规则由这段样本推出，前向验证见 /journal 规则遵守度';

const NUMBER_FORMAT = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
});

function formatPct(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

function formatUsd(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return `$${NUMBER_FORMAT.format(Math.round(value))}`;
}

function toneFor(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'text-text-3';
  if (value > 0) return 'text-up-strong';
  if (value < 0) return 'text-down-strong';
  return 'text-text-2';
}

/** 每张表都必须显示样本量；缺席的格子给原因，绝不留空或补 0。 */
function Cell({ value, digits = 2 }: { value: number | null; digits?: number }) {
  return (
    <span className={`font-mono text-mono-xs tabular-nums ${toneFor(value)}`}>
      {formatPct(value, digits)}
    </span>
  );
}

function SampleNote({ row }: { row: RulesEvidenceCell }) {
  if (row.n > 0) {
    return <span className="font-mono text-mono-xs tabular-nums text-text-3">n={row.n}</span>;
  }
  return <span className="text-caption text-text-3">{row.reason ?? '无样本'}</span>;
}

function Section({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description?: string | null;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1" aria-label={title}>
      <div className="border-b border-subtle px-4 py-3">
        <div className="text-label uppercase tracking-label text-text-3">{eyebrow}</div>
        <h2 className="mt-0.5 text-h2 text-text-1">{title}</h2>
        {description && <p className="mt-1 text-body-sm text-text-2">{description}</p>}
      </div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function TableShell({
  head,
  children,
}: {
  head: string[];
  children: React.ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] border-collapse text-body-sm">
        <thead>
          <tr className="border-b border-subtle text-left text-caption text-text-3">
            {head.map((label, index) => (
              <th
                key={label}
                className={`py-1.5 pr-3 font-normal ${index === 0 ? '' : 'text-right'}`}
                scope="col"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** 手续费门槛计算器：两个输入，纯本地 state，永不持久化。 */
function FeeCalculator({ data }: { data: NonNullable<RulesEvidenceResponse['feeThreshold']> }) {
  const [ticketUsd, setTicketUsd] = useState(data.defaultTicketUsd);
  const [ticketsPerDay, setTicketsPerDay] = useState(data.defaultTicketsPerDay);

  const rate = data.feePctOfPremium;
  const perTrade = rate === null ? null : (ticketUsd * rate) / 100;
  const perMonth =
    perTrade === null ? null : perTrade * ticketsPerDay * data.tradingDaysPerMonth;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-2 text-body-sm">
        <span className="text-text-3">恒定费率</span>
        <span className="font-mono text-mono-lg tabular-nums text-text-1">
          {rate === null ? '—' : `${rate.toFixed(2)}%`}
        </span>
        <span className="text-caption text-text-3">
          {rate === null ? (data.reason ?? '无样本') : `占权利金 · n=${data.n}`}
        </span>
      </div>

      <div className="flex flex-wrap gap-4">
        <label className="text-body-sm text-text-2">
          <span className="block text-caption text-text-3">单笔金额（美元）</span>
          <input
            type="number"
            min={1}
            step={100}
            value={ticketUsd}
            onChange={(event) => setTicketUsd(Math.max(0, Number(event.target.value) || 0))}
            className="mt-1 w-36 rounded-ds-sm border border-subtle bg-bg-2 px-2 py-1 font-mono text-mono-sm tabular-nums text-text-1"
          />
        </label>
        <label className="text-body-sm text-text-2">
          <span className="block text-caption text-text-3">每天笔数</span>
          <input
            type="number"
            min={0}
            step={1}
            value={ticketsPerDay}
            onChange={(event) => setTicketsPerDay(Math.max(0, Number(event.target.value) || 0))}
            className="mt-1 w-28 rounded-ds-sm border border-subtle bg-bg-2 px-2 py-1 font-mono text-mono-sm tabular-nums text-text-1"
          />
        </label>
      </div>

      <div className="grid gap-x-4 gap-y-1 text-body-sm sm:grid-cols-2">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-text-3">每笔过路费</span>
          <span className="font-mono text-mono-sm tabular-nums text-text-1">
            {perTrade === null ? '—' : formatUsd(perTrade)}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-text-3">
            每月过路费（按 {data.tradingDaysPerMonth} 个交易日）
          </span>
          <span className="font-mono text-mono-sm tabular-nums text-down-strong">
            {perMonth === null ? '—' : formatUsd(perMonth)}
          </span>
        </div>
      </div>

      <p className="text-caption text-text-3">{data.note}</p>
    </div>
  );
}

export const RulesPage: React.FC = () => {
  const [evidence, setEvidence] = useState<RulesEvidenceResponse | null>(null);
  const [playbook, setPlaybook] = useState<PlaybookListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  // 最近一次成功读取的本机时刻：刷新失败仍显示旧表时，页面必须说明
  // 「显示的是上次成功读取的数据」——旧数据不加标注就是冒充新数据。
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [evidenceResult, playbookResult] = await Promise.all([
        fetchRulesEvidence(),
        fetchPlaybook().catch(() => null),
      ]);
      setEvidence(evidenceResult);
      setPlaybook(playbookResult);
      setLastLoadedAt(new Date());
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const activeRules = useMemo(
    () => (playbook?.rules ?? []).filter((rule) => rule.isLatestVersion),
    [playbook],
  );
  const openCandidates = useMemo(
    () => (playbook?.candidates ?? []).filter((candidate) => !candidate.promoted),
    [playbook],
  );

  const ready = evidence?.dataState === 'ready';

  return (
    <div className="mx-auto max-w-[1200px] space-y-4 p-4">
      <header className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-h2 text-text-1">交易纪律</h1>
          <button
            type="button"
            className="btn-ghost text-body-sm"
            onClick={() => void load()}
            disabled={loading}
          >
            刷新
          </button>
        </div>

        {/* 界面披露策略（2026-08-15）：横幅文案（后端下发，逐字）不再以整条
            警示横幅占可见 chrome——原文收进 ⓘ tooltip，可见只留跳转入口。 */}
        <div
          className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-body-sm text-text-2"
          role="note"
          aria-label="交易纪律页说明"
        >
          <InfoHint
            text={evidence?.banner ?? RULES_BANNER_FALLBACK}
            label="交易纪律页说明"
          />
          <Link className="underline hover:text-text-1" to="/journal?tab=positions">
            打开规则遵守度面板 →
          </Link>
        </div>

        {ready && (
          <p className="text-caption text-text-3">
            样本 n={evidence.sampleEpisodeCount} · {evidence.firstTradingDay} →{' '}
            {evidence.lastTradingDay} · Build #{evidence.buildId} · {evidence.sourceKind} ·
            干净口径起点 {evidence.cleanBasisStart} · 规则采纳日 {evidence.ruleSetAdoptedAt}
          </p>
        )}
      </header>

      {error && (
        <p className="text-body-sm text-down-strong" role="alert">
          {error.message}
        </p>
      )}

      {/* 刷新失败但仍有旧表：显式声明数据的 as-of，绝不让旧表冒充新读取。 */}
      {error && evidence && (
        <p className="text-caption text-text-3" role="status">
          显示的是上次成功读取（
          {lastLoadedAt
            ? lastLoadedAt.toLocaleTimeString('en-GB', {
              hour: '2-digit',
              minute: '2-digit',
            })
            : '时点未记录'}
          ）的数据，本次刷新未成功。
        </p>
      )}

      {loading && !evidence && (
        <p className="py-6 text-center text-body-sm text-text-3">重算纪律证据读数中…</p>
      )}

      {evidence?.dataState === 'not_built' && (
        <p className="py-6 text-center text-body-sm text-text-3">
          该账户还没有 episode build，暂无可用证据。
        </p>
      )}

      {ready && (
        <>
          {/* 1. Playbook —— 规则原文与状态，与 /journal 同一份数据 */}
          <Section
            eyebrow="Playbook"
            title="规则清单"
            description="规则原文与状态来自 Playbook，本页只读；提升与退役仍在 /journal 完成。"
          >
            {!playbook && (
              <p className="text-body-sm text-text-3">Playbook 暂不可读，证据表不受影响。</p>
            )}
            {playbook && activeRules.length === 0 && openCandidates.length === 0 && (
              <p className="text-body-sm text-text-3">还没有任何候选或已采纳的规则。</p>
            )}
            {playbook && (
              <ul className="space-y-2">
                {activeRules.map((rule) => (
                  <li key={rule.ruleKey} className="border-l-2 border-up-strong pl-3">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="text-body-sm text-text-1">{rule.title}</span>
                      <span className="rounded-ds-sm bg-bg-3 px-1.5 text-caption text-text-2">
                        已采纳 · v{rule.version} · {rule.status}
                      </span>
                    </div>
                    <p className="mt-0.5 text-body-sm text-text-2">{rule.ruleText}</p>
                  </li>
                ))}
                {openCandidates.map((candidate) => (
                  <li
                    key={candidate.candidateKey}
                    className="border-l-2 border-[color:var(--warn-muted)] pl-3"
                  >
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="text-body-sm text-text-1">{candidate.title}</span>
                      <span className="rounded-ds-sm bg-bg-3 px-1.5 text-caption text-text-2">
                        候选
                      </span>
                    </div>
                    <p className="mt-0.5 text-body-sm text-text-2">{candidate.ruleText}</p>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* 2. 合约价格甜蜜区 */}
          <Section
            eyebrow="Contract price"
            title="合约价格甜蜜区"
            description={evidence.priceBandHeadline}
          >
            <TableShell head={['进场价分档', '样本', '费用占权利金', '毛口径', '净口径']}>
              {evidence.priceBands.map((row) => (
                <tr key={row.label} className="border-b border-subtle/60">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-text-2">
                    {row.label}
                  </th>
                  <td className="py-1.5 pr-3 text-right">
                    <SampleNote row={row} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <span className="font-mono text-mono-xs tabular-nums text-text-2">
                      {row.feePct === null ? '—' : `${row.feePct.toFixed(2)}%`}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <Cell value={row.grossPct} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <Cell value={row.netPct} />
                  </td>
                </tr>
              ))}
            </TableShell>
            <p className="mt-2 text-caption text-text-3">
              分档边界为 [下限, 上限)（下含上不含）· {evidence.priceBandBoundaryPolicy}
            </p>
          </Section>

          {/* 3. DTE × 持有方式 */}
          <Section
            eyebrow="DTE × hold"
            title="DTE × 持有方式"
            description="同一个 DTE，当日平和过夜是两件完全不同的事；剔尾读数用来看是不是靠少数几笔撑住的。"
          >
            <TableShell
              head={['车道', '样本', '毛口径', '胜率', `剔除最好 ${evidence.dteHoldLanes[0]?.excludedTopN ?? 5} 笔`]}
            >
              {evidence.dteHoldLanes.map((row) => (
                <tr key={row.label} className="border-b border-subtle/60">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-text-2">
                    {row.label}
                  </th>
                  <td className="py-1.5 pr-3 text-right">
                    <SampleNote row={row} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <Cell value={row.grossPct} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <span className="font-mono text-mono-xs tabular-nums text-text-2">
                      {row.winRatePct === null ? '—' : `${row.winRatePct.toFixed(1)}%`}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {row.exTopNGrossPct === null ? (
                      <span className="text-caption text-text-3">{row.exTopNReason ?? '—'}</span>
                    ) : (
                      <Cell value={row.exTopNGrossPct} />
                    )}
                  </td>
                </tr>
              ))}
            </TableShell>
            <p className="mt-2 text-caption text-text-3">
              持有方式判定：{evidence.holdStyleBasis}
            </p>
          </Section>

          {/* 4. 时段 */}
          <Section
            eyebrow="Session hour"
            title="时段（毛口径 vs 过路费）"
            description="毛口径是行情给的，过路费是恒定支出——两条线要分开看。"
          >
            <TableShell head={['ET 小时', '样本', '毛口径', '过路费', '净口径']}>
              {evidence.etHours.map((row) => (
                <tr key={row.etHour} className="border-b border-subtle/60">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-text-2">
                    {row.label}
                  </th>
                  <td className="py-1.5 pr-3 text-right">
                    <SampleNote row={row} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <Cell value={row.grossPct} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <span className="font-mono text-mono-xs tabular-nums text-text-2">
                      {row.feePct === null ? '—' : `${row.feePct.toFixed(2)}%`}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <Cell value={row.netPct} />
                  </td>
                </tr>
              ))}
            </TableShell>
          </Section>

          {/* 5. 星期 × 0DTE 可用性 */}
          <Section
            eyebrow="Weekday"
            title="星期 × 0DTE 可用性"
            description={evidence.weekdayHeadline}
          >
            <TableShell head={['星期', '样本', '毛口径', '0DTE 笔数', '1-3DTE 笔数']}>
              {evidence.weekdays.map((row) => (
                <tr key={row.label} className="border-b border-subtle/60">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-text-2">
                    {row.label}
                  </th>
                  <td className="py-1.5 pr-3 text-right">
                    <SampleNote row={row} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <Cell value={row.grossPct} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <span className="font-mono text-mono-xs tabular-nums text-text-2">
                      {row.zeroDteN}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <span className="font-mono text-mono-xs tabular-nums text-text-2">
                      {row.dte13N}
                    </span>
                  </td>
                </tr>
              ))}
            </TableShell>
          </Section>

          {/* 6. 手续费门槛 */}
          {evidence.feeThreshold && (
            <Section
              eyebrow="Fee toll"
              title="手续费门槛"
              description="费率恒定，与行情无关；它只随「下注金额 × 笔数」放大。"
            >
              <FeeCalculator data={evidence.feeThreshold} />
            </Section>
          )}

          {/* 7. 仓位与回撤 */}
          {evidence.position && (
            <Section
              eyebrow="Position"
              title="仓位与回撤"
              description={evidence.position.framing}
            >
              <div className="space-y-4">
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-body-sm">
                  <span className="text-text-2">
                    单笔金额{' '}
                    <span className="font-mono text-mono-sm tabular-nums text-text-1">
                      {formatUsd(evidence.position.params.ticketUsd)}
                    </span>
                  </span>
                  <span className="text-text-2">
                    每日熔断{' '}
                    <span className="font-mono text-mono-sm tabular-nums text-text-1">
                      −{formatUsd(evidence.position.params.dailyBreakerUsd)}
                    </span>
                  </span>
                  <span className="text-text-2">
                    最大并发{' '}
                    <span className="font-mono text-mono-sm tabular-nums text-text-1">
                      {evidence.position.params.maxConcurrent}
                    </span>
                  </span>
                </div>

                <TableShell head={['亏损档', '定义', '单笔亏损', '触发熔断所需笔数']}>
                  {evidence.position.severityTiers.map((tier) => (
                    <tr key={tier.label} className="border-b border-subtle/60">
                      <th scope="row" className="py-1.5 pr-3 text-left font-normal text-text-2">
                        {tier.label}
                      </th>
                      <td className="py-1.5 pr-3 text-right text-caption text-text-3">
                        {tier.definition}
                      </td>
                      <td className="py-1.5 pr-3 text-right">
                        <Cell value={tier.lossPct} />
                      </td>
                      <td className="py-1.5 pr-3 text-right">
                        {tier.ticketsToBreaker === null ? (
                          <span className="text-caption text-text-3">{tier.reason ?? '—'}</span>
                        ) : (
                          <span className="font-mono text-mono-sm tabular-nums text-text-1">
                            {tier.ticketsToBreaker.toFixed(1)} 笔
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </TableShell>

                <div className="space-y-1 text-body-sm text-text-2">
                  <div>
                    样本内触发频率：{evidence.position.observedTradingDayCount} 个交易日中{' '}
                    {evidence.position.observedBreachDayCount} 天会触发
                    {evidence.position.observedBreachOnePerDays !== null && (
                      <> · 约每 {evidence.position.observedBreachOnePerDays.toFixed(1)} 个交易日 1 次</>
                    )}
                  </div>
                  <p className="text-caption text-text-3">
                    {evidence.position.observedCadenceCaveat}
                  </p>
                </div>

                <div className="space-y-1 text-body-sm text-text-2">
                  <div>
                    该样本单一真实路径、按{' '}
                    {evidence.position.historicalDrawdownSizingPct.toFixed(0)}% 比例仓位复利的累计最大回撤：{' '}
                    <span className="font-mono text-mono-sm tabular-nums text-down-strong">
                      {formatPct(evidence.position.historicalMaxDrawdownPct, 1)}
                    </span>
                  </div>
                  <p className="text-caption text-text-3">{evidence.position.drawdownCaveat}</p>
                </div>
              </div>
            </Section>
          )}

          {/* 8. 相关性提醒 */}
          {evidence.correlation && (
            <Section
              eyebrow="Correlation"
              title="相关性提醒"
              description="并发数限制只有在标的彼此独立时才成立。"
            >
              <div className="flex flex-wrap gap-2">
                {evidence.correlation.tickers.map((entry) => (
                  <span
                    key={entry.ticker}
                    className="rounded-ds-sm border border-subtle bg-bg-2 px-2 py-1 font-mono text-mono-xs tabular-nums text-text-1"
                  >
                    {entry.ticker} · {entry.n}
                  </span>
                ))}
              </div>
              <p className="mt-2 text-body-sm text-text-2">{evidence.correlation.note}</p>
              <p className="mt-1 text-caption text-text-3">
                合规车道样本 n={evidence.correlation.compliantEpisodeCount} · 当前并发上限{' '}
                {evidence.correlation.maxConcurrent}
              </p>
            </Section>
          )}

          {/* 9. 过夜跳空 */}
          <Section eyebrow="Overnight gap" title="过夜跳空">
            <p className="text-body-sm text-text-2">{evidence.overnightGapNote}</p>
          </Section>

          {/* 10. 口径与告警 */}
          <Section eyebrow="Caveats" title="口径与告警">
            <ul className="space-y-1.5">
              {evidence.limitations.map((line) => (
                <li key={line} className="text-caption leading-5 text-text-3">
                  · {line}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-caption text-text-3">
              排除计数：干净口径起点前 {evidence.excludedBeforeCleanBasis} · 汇总/不可判定口径{' '}
              {evidence.excludedAggregateOrUnknownBasis} · 缺开仓现金流{' '}
              {evidence.excludedMissingPremium} · 未平仓或缺净盈亏{' '}
              {evidence.excludedNotClosedOrMissingPnl} · 缺 total_fee（不入毛口径/费率）{' '}
              {evidence.feeUnknownCount ?? 0}
            </p>
          </Section>
        </>
      )}
    </div>
  );
};

export default RulesPage;
