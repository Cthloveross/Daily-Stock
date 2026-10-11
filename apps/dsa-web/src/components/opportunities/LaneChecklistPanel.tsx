import { useMemo, useState } from 'react';
import type {
  IntradayPulseResponse,
  IntradayTopResponse,
} from '../../types/opportunities';
import { InfoHint } from '../common/InfoHint';
import { Tooltip } from '../common/Tooltip';
import {
  LANE_LABELS,
  evaluateLaneChecklist,
  type CheckStatus,
  type LaneDayTypeView,
} from './laneChecklist';

const MISSING = '标缺';

const DAY_TYPE_MARK: Record<LaneDayTypeView, string> = {
  available: '✓',
  unavailable: '✕',
  unknown: '—',
  loading: '…',
};

const DAY_TYPE_CLASS: Record<LaneDayTypeView, string> = {
  available: 'text-text-1',
  unavailable: 'text-warn-strong',
  unknown: 'text-text-3',
  loading: 'text-text-3',
};

const STATUS_TEXT: Record<CheckStatus, string> = {
  pass: '符合',
  fail: '不符合',
  missing: MISSING,
  requirement: '要求',
  neutral: '读数',
};

const STATUS_CLASS: Record<CheckStatus, string> = {
  pass: 'text-text-1',
  fail: 'text-warn-strong',
  missing: 'text-text-3',
  requirement: 'text-text-2',
  neutral: 'text-text-2',
};

const STATUS_MARK: Record<CheckStatus, string> = {
  pass: '✓',
  fail: '✕',
  missing: '—',
  requirement: '▸',
  neutral: '·',
};

/** 当前策略的诚实边界；旧隔夜样本只在 Journal 历史证据中展示。 */
export const INTRADAY_POLICY_CAVEAT = [
  '活动策略为 INTRA-0-7/v1：0–7DTE 只是候选期限，全部必须同一 ET 交易日平仓。',
  '点差、报价新鲜度与 Delta 阈值是保守工程起点，不是论文证明的最优参数；需用真实成交继续校准。',
  '旧 4–7DTE 隔夜结论已冻结为 Journal 历史取证，不参与本面板判定。',
].join('\n');

export const LANE_CHECKLIST_HONESTY_NOTE = [
  '这是收到标的提示后的手动合约参考，不参与 underlying P0/P0-X 是否成立。',
  '按你自己的执行规则机械核对，不是买卖建议。',
  INTRADAY_POLICY_CAVEAT,
].join('\n');

/** 收到 underlying 提示后的 INTRA-0-7 手动合约机械核对。 */
export function LaneChecklistPanel({
  pulse,
  top,
  loading = false,
  nowMs,
}: {
  pulse: IntradayPulseResponse | null;
  top: IntradayTopResponse | null;
  loading?: boolean;
  nowMs?: number;
}) {
  const [dteText, setDteText] = useState('');

  const result = useMemo(() => {
    const trimmed = dteText.trim();
    const parsed = Number(trimmed);
    const dte = trimmed !== '' && Number.isFinite(parsed) && Number.isInteger(parsed)
      ? parsed
      : null;
    return evaluateLaneChecklist({
      lane: 'intra_0_7',
      dte,
      pulseGeneratedAt: pulse?.generatedAt ?? null,
      executionPolicy: top?.executionPolicy,
      laneAvailability: top?.laneAvailability ?? null,
      loading: loading && !top,
      nowMs,
    });
  }, [dteText, pulse, top, loading, nowMs]);

  const policyUnavailable = result.dayType.state === 'unavailable';

  return (
    <section
      aria-label="开仓前车道检查"
      className="rounded-ds-sm border border-subtle bg-bg-1 px-4 py-2.5"
    >
      {policyUnavailable ? (
        <div
          data-day-type={result.dayType.state}
          role="status"
          aria-label={`今日车道可用性：${result.dayType.text} · ${result.dayType.tooltip}`}
          className="mb-3 rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-3 py-2.5"
        >
          <p className="text-h3 font-semibold text-warn-strong">
            <span aria-hidden="true">{DAY_TYPE_MARK[result.dayType.state]} </span>
            今日 0–7DTE 均不可用 · INTRA-0-7 暂停
          </p>
          <p className="mt-1 text-body-sm text-text-2">
            这是整个 0–7DTE 窗口不可用；仅仅没有 0DTE 不会触发关闭。
          </p>
        </div>
      ) : (
        <Tooltip content={result.dayType.tooltip} focusable contentClassName="whitespace-pre-line">
          <p
            data-day-type={result.dayType.state}
            aria-label={`今日车道可用性：${result.dayType.text} · ${result.dayType.tooltip}`}
            className={`mb-2 border-b border-subtle pb-2 text-body font-medium ${
              DAY_TYPE_CLASS[result.dayType.state]
            }`}
          >
            <span aria-hidden="true">{DAY_TYPE_MARK[result.dayType.state]} </span>
            {result.dayType.text}
          </p>
        </Tooltip>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-caption font-medium text-text-2">
          手动合约参考 · 不参与 P0/P0-X 判定
        </span>
        <InfoHint text={LANE_CHECKLIST_HONESTY_NOTE} label="车道检查口径" />

        <span
          aria-label="手动合约范围：INTRA-0-7，不参与 P0 判定"
          className="rounded-ds-sm border border-strong bg-bg-2 px-2 py-0.5 text-caption text-text-1"
        >
          {LANE_LABELS.intra_0_7} · 合约候选范围
        </span>

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

        <span className="ml-auto text-caption text-text-3">
          {result.etClock
            ? `${result.etClock} ET${
              result.pulseStaleMinutes !== null
                ? `（约 ${result.pulseStaleMinutes} 分钟前的服务端时点，已过时）`
                : ''
            }`
            : loading && !pulse
              ? 'ET 时钟读取中…'
              : `ET 时钟 ${MISSING}`}
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
                {check.status === 'requirement' && (
                  <span className="text-caption text-text-2">{check.reason}</span>
                )}
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

      <div className="mt-2 border-t border-subtle pt-1.5 text-caption text-text-3">
        {result.reminders.map((line) => <p key={line}>· {line}</p>)}
      </div>
    </section>
  );
}

export default LaneChecklistPanel;
