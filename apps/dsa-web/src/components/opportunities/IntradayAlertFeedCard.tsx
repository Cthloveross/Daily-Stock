import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { fetchPushLedgerDaily } from '../../api/pushLedger';
import type {
  PushLedgerDailyResponse,
  PushLedgerEvent,
  PushDeliveryStatus,
  PushTier,
} from '../../types/pushLedger';
import { InfoHint } from '../common/InfoHint';

/**
 * 今日提示流：推送台账的持久化读端。
 *
 * P0 是 RTH 异动，P0-X 是盘外正股信息提醒；预算只决定是否发送，
 * 不改写信号事实。普通 P2 只在页面展示；P0 的异步期权补充可静默发送；
 * 未校准 strict-2m 研究提醒单独标注并使用独立预算，不冒充正式 P0。
 * P3 只记台账且不进入用户主 feed。
 */

export const ALERT_FEED_POLL_INTERVAL_MS = 60_000;

const TIER_LABELS: Record<PushTier, string> = {
  P0X: 'P0-X 盘外异动',
  P0: 'P0 RTH 异动',
  P1: 'P1 预备',
  P2: 'P2 页面',
  P3: 'P3 台账',
  system: '系统',
};

const OPENING_RULES = new Set([
  'opening_impulse_v1',
  'opening_impulse_v2',
  'opening_impulse_sequential_v1',
  'opening_option_update',
]);

function tierLabel(event: PushLedgerEvent): string {
  if (event.rule === 'p0_2m_shadow_research') {
    return '2m 研究提醒（未验证收益）';
  }
  if (event.rule === 'p0_2m_option_update') {
    return 'P0 期权补充';
  }
  if (event.tier === 'P1' && OPENING_RULES.has(event.rule)) {
    return 'P1-O 开盘';
  }
  return TIER_LABELS[event.tier];
}

const CHANNEL_LABELS: Record<PushLedgerEvent['deliveryChannel'], string> = {
  telegram: '响铃',
  telegram_silent: '静默',
  web_only: '仅页面',
  ledger_only: '仅台账',
  none: '无通道',
};

const DELIVERY_LABELS: Record<PushDeliveryStatus, string> = {
  not_requested: '',
  pending: '等待发送回执',
  succeeded: 'Telegram 已接收',
  failed: '发送失败',
  indeterminate: '结果未确认',
  expired_unconfirmed: '已过期 · 未确认',
};

function feedEvents(events: PushLedgerEvent[]): PushLedgerEvent[] {
  const outcomes = new Map<string, boolean>();
  const decisionKeys = new Set<string>();
  const unmatchedDeliveryKeys = new Set<string>();
  for (const event of events) {
    if (!event.messageKey) continue;
    if (event.recordKind === 'decision' && event.tier !== 'P3') {
      decisionKeys.add(event.messageKey);
    }
    if (event.recordKind === 'delivery' && event.telegramOk !== null) {
      outcomes.set(event.messageKey, outcomes.get(event.messageKey) === true || event.telegramOk);
    }
  }
  return events
    .slice()
    .reverse()
    .filter((event) => {
      if (event.recordKind === 'decision') return event.tier !== 'P3';
      if (event.telegramOk !== false) return false;
      if (!event.messageKey) return true;
      if (decisionKeys.has(event.messageKey)
        || outcomes.get(event.messageKey) === true
        || unmatchedDeliveryKeys.has(event.messageKey)) return false;
      unmatchedDeliveryKeys.add(event.messageKey);
      return true;
    })
    .map((event) => {
      const receipt = event.messageKey ? outcomes.get(event.messageKey) : undefined;
      const requested = !event.suppressed
        && ['telegram', 'telegram_silent'].includes(event.deliveryChannel)
        && Boolean(event.messageKey);
      const deliveryStatus: PushDeliveryStatus = !requested
        ? 'not_requested'
        : receipt === true
          ? 'succeeded'
          : event.deliveryStatus ?? (
            receipt === false || event.telegramOk === false
              ? 'failed'
              : event.telegramOk === true ? 'succeeded' : 'indeterminate'
          );
      return { ...event, deliveryStatus };
    });
}

const TIER_CLASS: Record<PushTier, string> = {
  P0X: 'border-warn-strong text-warn-strong',
  P0: 'border-warn-strong text-warn-strong',
  P1: 'border-subtle text-text-1',
  P2: 'border-subtle text-text-2',
  P3: 'border-dashed border-subtle text-text-3',
  system: 'border-subtle text-text-3',
};

function etTime(ts: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(ts));
  } catch {
    return '--:--';
  }
}

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function p0Evidence(event: PushLedgerEvent): UnknownRecord | null {
  if (!event.rule.startsWith('p0_2m')) return null;
  if (isRecord(event.p0SignalEvidenceV2)) return event.p0SignalEvidenceV2;
  for (const condition of event.conditions) {
    const conditionId = condition.conditionId ?? condition.condition_id;
    if (conditionId !== 'p0_2m_evidence') continue;
    if (isRecord(condition.evidence)) return condition.evidence;
  }
  return null;
}

function evidenceClock(value: unknown): string {
  const raw = nonEmptyString(value);
  if (!raw) return '标缺';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return `${etTime(raw)} ET`;
}

function directionLabel(value: unknown): string {
  if (value === 'up') return '向上';
  if (value === 'down') return '向下';
  return '标缺';
}

function sessionLabel(value: unknown): string {
  if (value === 'premarket') return '盘前';
  if (value === 'regular') return 'RTH';
  if (value === 'afterhours') return '盘后';
  return '标缺';
}

function fixed(value: unknown, digits: number, suffix = ''): string {
  const number = finiteNumber(value);
  return number === null ? '标缺' : `${number.toFixed(digits)}${suffix}`;
}

function P0EvidenceDetails({ event }: { event: PushLedgerEvent }) {
  const evidence = p0Evidence(event);
  if (!evidence) return null;

  const percentile = finiteNumber(evidence.pricePercentile);
  const quality = isRecord(evidence.dataQuality) ? evidence.dataQuality : null;
  const qualityStatus = nonEmptyString(quality?.status);
  const issues = Array.isArray(quality?.issues)
    ? quality.issues.filter((item): item is string => typeof item === 'string')
    : [];
  const signalVersion = nonEmptyString(evidence.signalVersion);
  const universeHash = nonEmptyString(evidence.universeHash);
  const source = nonEmptyString(evidence.source);

  return (
    <div className="w-full pl-14">
      <div
        className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-ds-sm bg-bg-2 px-3 py-2 text-caption text-text-3 md:grid-cols-4"
        data-testid="p0-2m-evidence"
      >
        <span>方向 <strong className="font-medium text-text-2">{directionLabel(evidence.direction)}</strong></span>
        <span>盘段 <strong className="font-medium text-text-2">{sessionLabel(evidence.sessionScope ?? event.sessionScope)}</strong></span>
        <span>窗口截止 <strong className="font-medium text-text-2">{evidenceClock(evidence.windowEnd)}</strong></span>
        <span>同钟价格分位 <strong className="font-medium text-text-2">{percentile === null ? '标缺' : `${(percentile * 100).toFixed(1)}%`}</strong></span>
        <span>8m RVOL <strong className="font-medium text-text-2">{fixed(evidence.rvol8m, 2, 'x')}</strong></span>
        <span>18m 位移 <strong className="font-medium text-text-2">{fixed(evidence.displacementAtr, 2, ' ATR')}</strong></span>
        <span>8m 变化 <strong className="font-medium text-text-2">{fixed(evidence.impulse8mPercent, 2, '%')}</strong></span>
        <span>18m 变化 <strong className="font-medium text-text-2">{fixed(evidence.displacement18mPercent, 2, '%')}</strong></span>
        <span>基线 <strong className="font-medium text-text-2">{fixed(evidence.baselineCount, 0, '日')}</strong></span>
        <span>数据质量 <strong className="font-medium text-text-2">{qualityStatus === 'complete' ? '完整' : qualityStatus ?? '标缺'}{issues.length ? ` · ${issues.join(' / ')}` : ''}</strong></span>
        <span>数据源 <strong className="break-all font-medium text-text-2">{source ?? '标缺'}</strong></span>
        <span>版本 <strong className="break-all font-medium text-text-2">{signalVersion ?? '标缺'}</strong></span>
        {universeHash && (
          <span className="col-span-2 md:col-span-4">自选快照 <strong className="break-all font-mono font-medium text-text-2">{universeHash}</strong></span>
        )}
      </div>
    </div>
  );
}

function FeedRow({ event }: { event: PushLedgerEvent }) {
  return (
    <li
      data-push-id={event.pushId}
      data-push-tier={event.tier}
      className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-subtle py-1.5 last:border-b-0"
    >
      <span className="w-12 shrink-0 font-mono text-mono-xs text-text-3">
        {etTime(event.ts)}
      </span>
      <span
        className={`shrink-0 rounded-ds-sm border px-1 text-caption ${TIER_CLASS[event.tier]}`}
      >
        {tierLabel(event)}
      </span>
      <span className="min-w-0 flex-1 text-caption text-text-2">
        {event.text}
      </span>
      <span className="shrink-0 text-caption text-text-3">
        <span>{CHANNEL_LABELS[event.deliveryChannel]}</span>
        {event.deliveryStatus && event.deliveryStatus !== 'not_requested' && (
          <span>
            {' · '}{DELIVERY_LABELS[event.deliveryStatus]}
          </span>
        )}
      </span>
      {event.suppressed && event.suppressedReason && (
        <span className="w-full pl-14 text-caption text-text-3">
          被抑制：{event.suppressedReason}
        </span>
      )}
      <P0EvidenceDetails event={event} />
    </li>
  );
}

export function IntradayAlertFeedCard({
  pollMs = ALERT_FEED_POLL_INTERVAL_MS,
}: {
  pollMs?: number;
}) {
  const [data, setData] = useState<PushLedgerDailyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setData(await fetchPushLedgerDaily());
      setError(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : '推送台账读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    if (pollMs <= 0) return undefined;
    const timer = window.setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') {
        void load();
      }
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [load, pollMs]);

  // Join existing receipts by message key, including older API responses.
  // A failed receipt is shown separately only when its decision is unavailable.
  const decisions = feedEvents(data?.events ?? []);

  const passRate = data?.p0PassRate ?? null;

  return (
    <section
      aria-label="今日提示流"
      className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h2 className="text-h3 font-semibold text-text-1">今日提示流</h2>
          <span className="text-caption text-text-3">推送台账 · 持久化记账</span>
          {data && (
            <InfoHint
              text={[
                ...data.limitations,
                'Telegram 接收结果不等于手机响铃或已读',
              ].join('\n')}
              label="今日提示流口径"
            />
          )}
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={() => {
            setLoading(true);
            void load();
          }}
          className="flex items-center gap-1 text-caption text-text-2 hover:text-text-1 disabled:opacity-50"
          aria-label="刷新今日提示流"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : undefined} />
          刷新
        </button>
      </header>

      {error && (
        <p className="px-4 py-2 text-caption text-warning" role="status">
          推送台账读取失败：{error}
        </p>
      )}

      {data && (
        <div
          className="flex flex-wrap gap-x-4 gap-y-1 border-b border-subtle px-4 py-2 text-caption text-text-3"
          data-testid="push-budget-strip"
        >
          <span>
            开盘响铃 {data.budgets.openingUsedToday}/
            {data.budgets.openingDailyBudget} 日
          </span>
          <span>
            P0 {data.budgets.p0UsedToday}/{data.budgets.p0DailyBudget} 日 ·{' '}
            {data.budgets.p0UsedWeek}/{data.budgets.p0WeeklyBudget} 周
          </span>
          <span>
            P0-X 盘前 {data.budgets.p0xUsedTodayBySession.premarket ?? 0}/
            {data.budgets.p0xDailyBudgetBySession.premarket ?? 0} 日 · 盘后{' '}
            {data.budgets.p0xUsedTodayBySession.afterhours ?? 0}/
            {data.budgets.p0xDailyBudgetBySession.afterhours ?? 0} 日 ·{' '}
            {data.budgets.p0xUsedWeek}/{data.budgets.p0xWeeklyBudget} 周
          </span>
          <span>
            P1 {data.budgets.p1UsedToday}/{data.budgets.p1DailyBudget} 日
          </span>
          {data.budgets.researchRthDailyBudget !== undefined && (
            <>
              <span>
                2m 研究 RTH {data.budgets.researchRthUsedToday ?? 0}/
                {data.budgets.researchRthDailyBudget} 日 ·{' '}
                {data.budgets.researchRthUsedWeek ?? 0}/
                {data.budgets.researchRthWeeklyBudget ?? 0} 周
              </span>
              <span>
                2m 研究盘外 盘前 {data.budgets.researchExtendedUsedTodayBySession?.premarket ?? 0}/
                {data.budgets.researchExtendedDailyBudgetBySession?.premarket ?? 0} 日 · 盘后{' '}
                {data.budgets.researchExtendedUsedTodayBySession?.afterhours ?? 0}/
                {data.budgets.researchExtendedDailyBudgetBySession?.afterhours ?? 0} 日 ·{' '}
                {data.budgets.researchExtendedUsedWeek ?? 0}/
                {data.budgets.researchExtendedWeeklyBudget ?? 0} 周
              </span>
            </>
          )}
          {passRate && (
            <span data-testid="p0-pass-rate">
              P0 passed 率（滚动 {passRate.window} 条，机械代理）：
              {passRate.status === 'ready' && passRate.rate !== null
                ? `${(passRate.rate * 100).toFixed(0)}%（${passRate.passed}/${passRate.delivered}）`
                : `标缺（${passRate.reason ?? '尚无已投递 P0'}）`}
              {' · '}
              {'仅作复盘代理，不自动改变 P95 / 1.5x / 0.50 ATR 冻结阈值'}
            </span>
          )}
        </div>
      )}

      {loading && !data ? (
        <div className="m-4 h-16 animate-pulse rounded-ds-sm bg-bg-2" />
      ) : decisions.length === 0 ? (
        <p className="px-4 py-4 text-body-sm text-text-3">
          今日暂无可展示提示。普通 P2 观察级事实只出现在这里（不推 Telegram），
          P3 仅写入台账、不在主 feed 展示；无催化剂、无异常的日子为空——
          预期空转日＝合法休息。
        </p>
      ) : (
        <ul className="px-4 py-1" data-testid="push-feed-list">
          {decisions.map((event) => (
            <FeedRow key={event.pushId} event={event} />
          ))}
        </ul>
      )}
    </section>
  );
}

export default IntradayAlertFeedCard;
