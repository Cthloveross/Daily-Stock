import { useMemo, useState } from 'react';
import type {
  IntradayPulseResponse,
  IntradayTopResponse,
} from '../../types/opportunities';
import {
  selectManualIntradayTickets,
  selectManualOvernightPositions,
  useIntradayManualBudgetStore,
} from '../../stores/intradayPlanStore';
import { Tooltip } from '../common/Tooltip';
import {
  DAY_TYPE_EVIDENCE_LINE,
  LANE_LABELS,
  V2D_INTRADAY_TICKET_LIMIT,
  V2D_OVERNIGHT_CONCURRENT_LIMIT,
  evaluateLaneChecklist,
  isIntradayLaneClosed,
  laneForDayType,
  type CheckStatus,
  type LaneDayTypeView,
  type LaneId,
} from './laneChecklist';

const MISSING = '标缺';

/** 车道日类型的文字标记：语义靠文字承载，不靠颜色（无障碍 + 无涨跌色）。 */
const DAY_TYPE_MARK: Record<LaneDayTypeView, string> = {
  intraday_available: '✓',
  overnight_only: '✕',
  blacklist_only: '✕',
  unknown: '—',
  loading: '…',
};

const DAY_TYPE_CLASS: Record<LaneDayTypeView, string> = {
  intraday_available: 'text-text-1',
  overnight_only: 'text-warn-strong',
  blacklist_only: 'text-warn-strong',
  unknown: 'text-text-3',
  loading: 'text-text-3',
};

const STATUS_TEXT: Record<CheckStatus, string> = {
  pass: '符合',
  fail: '不符合',
  missing: MISSING,
  requirement: '要求',
  // 纯描述读数（本面板当前不产出，为状态全集补齐渲染）。
  neutral: '读数',
};

const STATUS_CLASS: Record<CheckStatus, string> = {
  pass: 'text-text-1',
  fail: 'text-warn-strong',
  missing: 'text-text-3',
  requirement: 'text-text-2',
  neutral: 'text-text-2',
};

/** 状态标记用文字而非颜色承载语义（无障碍 + 无涨跌色）。 */
const STATUS_MARK: Record<CheckStatus, string> = {
  pass: '✓',
  fail: '✕',
  missing: '—',
  requirement: '▸',
  neutral: '·',
};

/** 长口径 caveat：不再占正文，改挂 tooltip（内容一字不删）。 */
export const LANE_SAMPLE_CAVEAT = [
  '样本窗口仅 2026-04→07 一个市场状态（SPY 上行，隔夜多头天然占优）。',
  '过夜车道 n=65 偏小，隔夜跳空风险在该窗口内未被充分体现。',
  '规则由同一份样本内推出（in-sample）；前向验证见 /journal 的规则遵守度。',
  '数字口径：build #3 干净口径，n=1,407，2026-04-21→07-31。',
].join('\n');

/**
 * 开仓前车道检查清单：把「今天这条车道到底开不开、当下是否符合你自己那套
 * 规则」摊在盘面上的一小块面板。
 *
 * 三条设计约束（2026-08-05 依用户反馈重排）：
 * 1. **系统已经知道的事，绝不显示成「标缺」**。今天只能 0DTE / 只能 4-7DTE、
 *    ET 12:00 截止线，都是**要求**（`requirement`）——直接写出要求本身；只有
 *    「查过了拿不到」才是标缺。
 * 2. **今日无 0DTE 是全屏最重要的一件事**：整块警示、大字、一行证据，并把
 *    日内车道按钮置为「不可选 + 原因」，而不是让用户点进一堵失败墙。
 * 3. **额度是用户自己数的**。Journal 只有导入的历史成交，永远不含今天，那两个
 *    计数恒为标缺——改成按 ET 日作用域的手动 +1/−1，如实标注「手动维护」。
 *
 * ET 时钟取市场脉搏端点的 `generatedAt`（与脉搏条「数据时点」同一口径）；
 * 车道可用性取日内扫描的 `laneAvailability`（服务端按当日真实期权到期日元
 * 数据判定，不含星期规则）。首轮请求仍在飞时显示「读取中…」——**读取中 ≠
 * 标缺**。财报回避需要标的才有意义，已下沉到「盘中计划」的逐标的卡片。
 */
export function LaneChecklistPanel({
  pulse,
  top,
  loading = false,
  nowMs,
}: {
  pulse: IntradayPulseResponse | null;
  top: IntradayTopResponse | null;
  loading?: boolean;
  /** 「现在」的毫秒时戳（测试注入用）；缺省取 `Date.now()`。 */
  nowMs?: number;
}) {
  const [laneOverride, setLaneOverride] = useState<LaneId | null>(null);
  const [dteText, setDteText] = useState('');
  const [intendsToCloseToday, setIntendsToCloseToday] = useState(false);

  const intradayTickets = useIntradayManualBudgetStore(selectManualIntradayTickets);
  const overnightPositions = useIntradayManualBudgetStore(selectManualOvernightPositions);
  const bump = useIntradayManualBudgetStore((state) => state.bump);

  // 先按「今天成立的车道」预判一次日型，用来决定默认选中的车道与禁用状态。
  const preview = useMemo(() => evaluateLaneChecklist({
    lane: 'intraday',
    dte: null,
    intendsToCloseToday: false,
    pulseGeneratedAt: pulse?.generatedAt ?? null,
    laneAvailability: top?.laneAvailability ?? null,
    loading: loading && !top,
    nowMs,
  }), [pulse, top, loading, nowMs]);

  const intradayClosed = isIntradayLaneClosed(preview.dayType);
  // 今日只剩过夜车道时默认就落在过夜（不让用户先撞一堵失败墙）；用户仍可
  // 在日型标缺/可用时自由切换。
  const lane: LaneId = laneOverride ?? laneForDayType(preview.dayType) ?? 'intraday';

  const result = useMemo(() => {
    const trimmed = dteText.trim();
    const parsed = Number(trimmed);
    const dte = trimmed !== '' && Number.isFinite(parsed) && Number.isInteger(parsed) && parsed >= 0
      ? parsed
      : null;
    return evaluateLaneChecklist({
      lane,
      dte,
      intendsToCloseToday,
      pulseGeneratedAt: pulse?.generatedAt ?? null,
      laneAvailability: top?.laneAvailability ?? null,
      loading: loading && !top,
      nowMs,
    });
  }, [lane, dteText, intendsToCloseToday, pulse, top, loading, nowMs]);

  const budgetRows = [
    {
      id: 'intraday_tickets' as const,
      label: '今日日内单',
      count: intradayTickets,
      limit: V2D_INTRADAY_TICKET_LIMIT,
      reason: `V2-D③：日内单每日最多 ${V2D_INTRADAY_TICKET_LIMIT} 笔。`
        + '本计数由你自己维护（按 ET 交易日自动归零）——Journal 只有导入的历史成交，'
        + '永远不含今天，因此不用它冒充今日读数。',
    },
    {
      id: 'overnight_positions' as const,
      label: '当前过夜持仓',
      count: overnightPositions,
      limit: V2D_OVERNIGHT_CONCURRENT_LIMIT,
      reason: `V2-D③：同时持有的过夜单不超过 ${V2D_OVERNIGHT_CONCURRENT_LIMIT} 个。`
        + '本计数由你自己维护（按 ET 交易日自动归零）。',
    },
  ];

  return (
    <section
      aria-label="开仓前车道检查"
      className="rounded-ds-sm border border-subtle bg-bg-1 px-4 py-2.5"
    >
      {/*
        今日无可用 0DTE 是这块面板上最重要的一件事：整块警示 + 大字 + 一行证据。
        它决定「今天这条车道到底开不开」，必须在用户做任何选择之前先看到。
      */}
      {intradayClosed ? (
        <div
          data-day-type={preview.dayType.state}
          role="status"
          aria-label={`今日车道可用性：${preview.dayType.text} · ${preview.dayType.tooltip}`}
          className="mb-3 rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-3 py-2.5"
        >
          <p className="text-h3 font-semibold text-warn-strong">
            <span aria-hidden="true">{DAY_TYPE_MARK[preview.dayType.state]} </span>
            {preview.dayType.state === 'blacklist_only'
              ? `今日仅黑名单标的有 0DTE（${preview.dayType.blacklistedZeroDteTickers.join('/')}）· 日内车道关闭`
              : '今日无 0DTE · 日内车道关闭'}
          </p>
          <p className="mt-1 text-body-sm text-text-2">
            只剩过夜车道（V2-B，4-7DTE）或不做；绝不退而买 1-3DTE。
            {' '}
            <span className="text-text-3">{DAY_TYPE_EVIDENCE_LINE}</span>
          </p>
          <Tooltip content={preview.dayType.tooltip} focusable contentClassName="whitespace-pre-line">
            <span className="mt-1 inline-block text-caption text-text-3 underline decoration-dotted underline-offset-2">
              判定依据（V2-E）
            </span>
          </Tooltip>
        </div>
      ) : (
        <Tooltip content={preview.dayType.tooltip} focusable contentClassName="whitespace-pre-line">
          <p
            data-day-type={preview.dayType.state}
            aria-label={`今日车道可用性：${preview.dayType.text} · ${preview.dayType.tooltip}`}
            className={`mb-2 border-b border-subtle pb-2 text-body font-medium ${
              DAY_TYPE_CLASS[preview.dayType.state]
            }`}
          >
            <span aria-hidden="true">{DAY_TYPE_MARK[preview.dayType.state]} </span>
            {preview.dayType.text}
          </p>
        </Tooltip>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-caption font-medium text-text-2">
          开仓前车道检查 · 对照你自己的规则
        </span>

        <div className="flex items-center gap-1" role="group" aria-label="车道">
          {(['intraday', 'overnight'] as LaneId[]).map((option) => {
            const disabled = option === 'intraday' && intradayClosed;
            const disabledReason = '今日无可用 0DTE，日内车道关闭（V2-E）';
            const button = (
              <button
                key={option}
                type="button"
                aria-pressed={lane === option}
                disabled={disabled}
                // 禁用原因走 aria-label + Tooltip（仓库禁用原生 title 属性）。
                aria-label={disabled
                  ? `${LANE_LABELS[option]} · 今日关闭 · ${disabledReason}`
                  : undefined}
                onClick={() => setLaneOverride(option)}
                className={`rounded-ds-sm border px-2 py-0.5 text-caption ${
                  disabled
                    ? 'cursor-not-allowed border-dashed border-subtle text-text-3 opacity-60'
                    : lane === option
                      ? 'border-strong bg-bg-2 text-text-1'
                      : 'border-subtle text-text-3'
                }`}
              >
                {LANE_LABELS[option]}
                {disabled ? ' · 今日关闭' : ''}
              </button>
            );
            return disabled ? (
              <Tooltip key={option} content={disabledReason} focusable>
                {button}
              </Tooltip>
            ) : button;
          })}
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
                {/* 要求态直接把要求写在盘面上：不用悬停才知道今天能开什么。 */}
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

      <ul className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1" aria-label="今日额度（手动计数）">
        {budgetRows.map((row) => {
          const atLimit = row.count >= row.limit;
          return (
            <li key={row.id}>
              <Tooltip content={row.reason} focusable contentClassName="whitespace-pre-line">
                <span
                  className="flex items-baseline gap-1.5"
                  data-budget-id={row.id}
                  aria-label={`${row.label}：${row.count}/${row.limit} · ${row.reason}`}
                >
                  <span className="text-caption text-text-3">{row.label}</span>
                  <span
                    className={`font-mono text-mono-xs tabular-nums ${
                      atLimit ? 'text-warn-strong' : 'text-text-1'
                    }`}
                  >
                    {row.count}/{row.limit}
                  </span>
                  <button
                    type="button"
                    onClick={() => bump(row.id, 1)}
                    aria-label={`${row.label} 加 1`}
                    className="rounded-ds-sm border border-subtle px-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
                  >
                    +1
                  </button>
                  <button
                    type="button"
                    onClick={() => bump(row.id, -1)}
                    aria-label={`${row.label} 减 1`}
                    className="rounded-ds-sm border border-subtle px-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
                  >
                    −1
                  </button>
                </span>
              </Tooltip>
            </li>
          );
        })}
        <li className="text-caption text-text-3">手动维护 · 按 ET 交易日自动归零</li>
      </ul>

      <div className="mt-2 border-t border-subtle pt-1.5 text-caption text-text-3">
        {result.reminders.map((line) => (
          <p key={line}>· {line}</p>
        ))}
        <Tooltip content={LANE_SAMPLE_CAVEAT} focusable contentClassName="whitespace-pre-line">
          <p className="mt-1 underline decoration-dotted underline-offset-2" aria-label={LANE_SAMPLE_CAVEAT}>
            按你自己的规则机械核对，不是买卖建议（样本与口径边界 ⓘ）
          </p>
        </Tooltip>
      </div>
    </section>
  );
}

export default LaneChecklistPanel;
