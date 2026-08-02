import type { IntradayTopRecentOptionEvent } from '../../types/opportunities';
import { formatCompactUsd } from './intradayFormat';

const SENTIMENT_LABELS: Record<string, string> = {
  BULLISH: '偏多',
  BEARISH: '偏空',
  NEUTRAL: '中性',
};

const OPTION_TYPE_LABELS: Record<string, string> = {
  CALL: 'Call',
  PUT: 'Put',
};

function fillClock(fillTime: string | null): string {
  if (!fillTime) return '—';
  const match = fillTime.match(/\d{2}:\d{2}(?::\d{2})?/);
  return match ? match[0] : fillTime;
}

function contractLabel(event: IntradayTopRecentOptionEvent): string {
  const type = event.optionType ? (OPTION_TYPE_LABELS[event.optionType.toUpperCase()] ?? event.optionType) : '—';
  const strike = event.strikePrice !== null
    ? event.strikePrice.toLocaleString('en-US', { maximumFractionDigits: 2 })
    : '—';
  const dte = event.dte !== null ? ` · ${event.dte}DTE` : '';
  return `${type} ${strike}${event.expiry ? ` · ${event.expiry}` : ''}${dte}`;
}

/**
 * 期权异动 feed：按时间倒序展示自选池最近的 Moomoo 异动成交。
 * 分类（偏多/偏空/中性）为供应商标签，不证明开平仓方向，不是信号。
 */
export function IntradayOptionEventFeed({
  events,
  quoteSessionLabel,
}: {
  events: IntradayTopRecentOptionEvent[];
  quoteSessionLabel: string | null;
}) {
  return (
    <section aria-label="期权异动" className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
        <h2 className="text-h3 font-semibold text-text-1">期权异动</h2>
        <span className="text-caption text-text-3">
          {quoteSessionLabel ? `${quoteSessionLabel} · ` : ''}最新在前 · 最多 20 条
        </span>
      </header>

      {events.length === 0 ? (
        <div className="px-4 py-6 text-body-sm text-text-3">
          当前自选池暂无可展示的异动成交（未配置、无事件或供应商未返回时均如实为空）。
        </div>
      ) : (
        <ul className="divide-y divide-[color:var(--border-subtle)]">
          {events.map((event) => (
            <li key={`${event.ticker}:${event.eventId}`} className="px-4 py-2.5">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <span className="font-mono text-mono-xs text-text-3">{fillClock(event.fillTime)}</span>
                <span className="font-mono text-mono-sm font-semibold text-text-1">{event.ticker}</span>
                <span className="text-body-sm text-text-2">{contractLabel(event)}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3 text-caption text-text-3">
                <span className="font-mono text-text-2">{formatCompactUsd(event.turnover)}</span>
                {event.volume !== null && <span>{event.volume} 张</span>}
                <span>
                  {event.sentiment
                    ? `${SENTIMENT_LABELS[event.sentiment.toUpperCase()] ?? event.sentiment}（Moomoo 分类）`
                    : '未分类'}
                </span>
                {event.orderTypes.length > 0 && <span>{event.orderTypes.join(' / ')}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
        偏多 / 偏空 / 中性为 Moomoo 供应商分类：不推断开平仓，不证明真实主动买卖方向，不是信号。
      </div>
    </section>
  );
}
