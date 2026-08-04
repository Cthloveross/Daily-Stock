import { useMemo, useState } from 'react';
import { usePersonalEdge } from '../../hooks/usePersonalEdge';
import type {
  IntradayPulseResponse,
  IntradayTopResponse,
} from '../../types/opportunities';
import { Tooltip } from '../common/Tooltip';
import {
  LANE_LABELS,
  evaluateLaneChecklist,
  type CheckStatus,
  type LaneId,
} from './laneChecklist';

const MISSING = '标缺';

const STATUS_TEXT: Record<CheckStatus, string> = {
  pass: '符合',
  fail: '不符合',
  missing: MISSING,
};

const STATUS_CLASS: Record<CheckStatus, string> = {
  pass: 'text-text-1',
  fail: 'text-warn-strong',
  missing: 'text-text-3',
};

/** 三态标记用文字而非颜色承载语义（无障碍 + 无涨跌色）。 */
const STATUS_MARK: Record<CheckStatus, string> = {
  pass: '✓',
  fail: '✕',
  missing: '—',
};

/**
 * 开仓前车道检查清单：把「这一单属于哪条车道、当下是否符合你自己那套规则」
 * 摊在盘面上的一小块面板。
 *
 * 它检查的是**用户自己的规则**（Playbook 候选「V2-0」…「V2-D」，由用户本人的
 * 干净口径历史推出并采纳），不是推荐、不是信号、不含概率；本系统只读，
 * **永远不会下单**。
 *
 * 数据来源全部复用盘面已有的读数，不新起任何时钟或数据源：
 * - ET 时钟取市场脉搏端点的 `generatedAt`（与脉搏条「数据时点」同一口径）；
 * - 财报回避窗取日内扫描深度层候选上已有的 `earningsProximity`——标的不在深度层
 *   或日历不可得时显式标缺，**未知≠安全**；
 * - 额度读数取 `GET /journal/v2/personal-edge` 的 `rule_compliance.daily_budget`，
 *   它带自己的 `asOfTradingDay`：build 不含今日时显式标缺并说明，绝不显示 0。
 *
 * V2-C 的三条硬禁止与所选车道无关：命中即列出，不会因为「选了另一条车道」而消失。
 */
export function LaneChecklistPanel({
  pulse,
  top,
}: {
  pulse: IntradayPulseResponse | null;
  top: IntradayTopResponse | null;
}) {
  const [lane, setLane] = useState<LaneId>('intraday');
  const [dteText, setDteText] = useState('');
  const [ticker, setTicker] = useState('');
  const [intendsToCloseToday, setIntendsToCloseToday] = useState(false);

  const view = usePersonalEdge();
  const edge = view.state === 'ready' ? view.data : null;
  const compliance = edge?.ruleCompliance ?? null;

  const result = useMemo(() => {
    const trimmed = dteText.trim();
    const parsed = Number(trimmed);
    const dte = trimmed !== '' && Number.isFinite(parsed) && Number.isInteger(parsed) && parsed >= 0
      ? parsed
      : null;
    return evaluateLaneChecklist({
      lane,
      dte,
      ticker: ticker.trim() || null,
      intendsToCloseToday,
      pulseGeneratedAt: pulse?.generatedAt ?? null,
      candidates: top?.candidates ?? [],
      marketDateEt: top?.marketDateEt ?? pulse?.marketDateEt ?? null,
      budget: compliance?.dailyBudget ?? null,
      budgetUnavailableReason:
        view.state === 'unavailable'
          ? '车道遵守度读数不可得（Journal 未构建或端点不可得）——不以 0 冒充额度'
          : view.state === 'loading'
            ? '车道遵守度读数读取中'
            : compliance
              ? null
              : '端点未返回车道遵守度区块——不以 0 冒充额度',
    });
  }, [lane, dteText, ticker, intendsToCloseToday, pulse, top, compliance, view.state]);

  const limitations = compliance?.limitations ?? [];
  const limitationTooltip = [
    '对照的是你自己那套规则（V2-0…V2-D），不是推荐；本系统只读，不会下单。',
    ...limitations.map((line) => `· ${line}`),
  ].join('\n');

  return (
    <section
      aria-label="开仓前车道检查"
      className="rounded-ds-sm border border-subtle bg-bg-1 px-4 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Tooltip content={limitationTooltip} focusable contentClassName="whitespace-pre-line">
          <span className="text-caption font-medium text-text-2" aria-label={limitationTooltip}>
            开仓前车道检查 · 对照你自己的规则
          </span>
        </Tooltip>

        <div className="flex items-center gap-1" role="group" aria-label="车道">
          {(['intraday', 'overnight'] as LaneId[]).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={lane === option}
              onClick={() => setLane(option)}
              className={`rounded-ds-sm border px-2 py-0.5 text-caption ${
                lane === option
                  ? 'border-strong bg-bg-2 text-text-1'
                  : 'border-subtle text-text-3'
              }`}
            >
              {LANE_LABELS[option]}
            </button>
          ))}
        </div>

        <label className="flex items-center gap-1.5 text-caption text-text-3">
          DTE
          <input
            type="number"
            min={0}
            inputMode="numeric"
            value={dteText}
            onChange={(event) => setDteText(event.target.value)}
            aria-label="进场 DTE"
            placeholder="选填"
            className="w-16 rounded-ds-sm border border-subtle bg-bg-0 px-1.5 py-0.5 font-mono text-mono-xs text-text-1"
          />
        </label>

        <label className="flex items-center gap-1.5 text-caption text-text-3">
          标的
          <input
            type="text"
            value={ticker}
            onChange={(event) => setTicker(event.target.value)}
            aria-label="标的"
            placeholder="选填"
            className="w-20 rounded-ds-sm border border-subtle bg-bg-0 px-1.5 py-0.5 font-mono text-mono-xs uppercase text-text-1"
          />
        </label>

        <label className="flex cursor-pointer items-center gap-1.5 text-caption text-text-3">
          <input
            type="checkbox"
            checked={intendsToCloseToday}
            onChange={(event) => setIntendsToCloseToday(event.target.checked)}
            aria-label="打算今天就平掉"
          />
          打算今天就平掉
        </label>

        <span className="ml-auto text-caption text-text-3">
          {result.etClock ? `${result.etClock} ET` : `ET 时钟 ${MISSING}`}
        </span>
      </div>

      <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
        {result.checks.map((check) => (
          <li key={check.id}>
            <Tooltip content={check.reason} focusable>
              <span
                className="flex items-baseline gap-1.5"
                data-check-id={check.id}
                data-check-status={check.status}
                aria-label={`${check.label}：${STATUS_TEXT[check.status]} · ${check.reason}`}
              >
                <span className="text-caption text-text-3">{check.label}</span>
                <span className={`text-caption font-medium ${STATUS_CLASS[check.status]}`}>
                  {STATUS_MARK[check.status]} {STATUS_TEXT[check.status]}
                </span>
              </span>
            </Tooltip>
          </li>
        ))}
      </ul>

      {result.hardBlocks.length > 0 && (
        <ul aria-label="硬禁止" className="mt-2 space-y-1">
          {result.hardBlocks.map((block) => (
            <li
              key={block.id}
              data-hard-block={block.id}
              className="rounded-ds-sm border border-[color:var(--warn-muted)] px-2 py-1 text-caption text-warn-strong"
            >
              硬禁止 {block.ruleId}：{block.reason}
            </li>
          ))}
        </ul>
      )}

      <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
        {result.budget.map((reading) => (
          <li key={reading.id}>
            <Tooltip content={reading.reason ?? ''} focusable>
              <span
                className="flex items-baseline gap-1.5"
                data-budget-id={reading.id}
                aria-label={`${reading.label}：${reading.text ?? MISSING}${
                  reading.reason ? ` · ${reading.reason}` : ''
                }`}
              >
                <span className="text-caption text-text-3">{reading.label}</span>
                <span
                  className={`font-mono text-mono-xs tabular-nums ${
                    reading.text === null
                      ? 'text-text-3'
                      : reading.atLimit
                        ? 'text-warn-strong'
                        : 'text-text-1'
                  }`}
                >
                  {reading.text ?? MISSING}
                </span>
                {reading.text === null && (
                  <span className="text-caption text-text-3">（{reading.limit} 上限）</span>
                )}
              </span>
            </Tooltip>
          </li>
        ))}
      </ul>

      <div className="mt-2 border-t border-subtle pt-1.5 text-caption text-text-3">
        {result.reminders.map((line) => (
          <p key={line}>· {line}</p>
        ))}
        <p className="mt-1">
          这是对照<strong className="text-text-2">你自己那套规则</strong>的检查清单，不是推荐、不含概率；
          本系统只读，<strong className="text-text-2">不会下单</strong>。
          样本仅 2026-04→07 一个市场状态，过夜车道 n=65 偏小、跳空风险未充分体现。
        </p>
      </div>
    </section>
  );
}

export default LaneChecklistPanel;
