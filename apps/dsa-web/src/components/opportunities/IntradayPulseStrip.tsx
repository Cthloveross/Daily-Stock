import type { IntradayPulseResponse, IntradaySessionState } from '../../types/opportunities';
import { ChangeCell } from '../data/ChangeCell';
import { formatEtClock } from '../../utils/marketTime';

const SESSION_STATE_LABELS: Record<IntradaySessionState, string> = {
  premarket: '盘前',
  regular: '盘中',
  afterhours: '盘后',
  closed: '休市',
};

const PULSE_STATE_LABELS: Record<string, string> = {
  not_configured: '未配置',
  unavailable: '标缺',
  partial: '缺前收',
};

function formatPrice(value: number | null): string {
  if (value === null) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/**
 * 市场脉搏（SPY / QQQ / VIX 快照读数 + 盘段 + ET 时钟 + 刷新指示）。
 * 缺失代码显式标缺（如 VIX 快照不可得），绝不以 0 或旧值冒充。
 */
export function IntradayPulseStrip({
  data,
  loading,
  error,
  pollingActive,
}: {
  data: IntradayPulseResponse | null;
  loading: boolean;
  error: string | null;
  pollingActive: boolean;
}) {
  const sessionState = data?.sessionState ?? null;
  return (
    <section
      aria-label="市场脉搏"
      className="sticky top-0 z-10 border-b border-subtle bg-bg-1/95 backdrop-blur"
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
        {(data?.items ?? []).map((item) => (
          <div key={item.ticker} className="flex items-baseline gap-2" aria-label={`${item.ticker} 脉搏`}>
            <span className="font-mono text-mono-sm font-semibold text-text-1">{item.ticker}</span>
            {item.lastPrice !== null ? (
              <>
                <span className="font-mono text-mono-sm text-text-2">{formatPrice(item.lastPrice)}</span>
                {item.changePercent !== null ? (
                  <ChangeCell value={item.changePercent} mode="percent" size="sm" />
                ) : (
                  <span className="text-caption text-text-3">{PULSE_STATE_LABELS[item.state] ?? '标缺'}</span>
                )}
              </>
            ) : (
              <span className="text-caption text-text-3">
                {PULSE_STATE_LABELS[item.state] ?? '标缺'}
              </span>
            )}
          </div>
        ))}
        {!data && (
          <span className="text-caption text-text-3">{loading ? '脉搏读取中…' : '脉搏暂无数据'}</span>
        )}

        <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-text-3">
          <span>
            盘段 <strong className="font-medium text-text-2">
              {sessionState ? SESSION_STATE_LABELS[sessionState] : '读取中'}
            </strong>
          </span>
          <span>
            数据时点 <strong className="font-mono font-medium text-text-2">
              {data ? `${formatEtClock(data.generatedAt) || '—'} ET` : '—'}
            </strong>
          </span>
          <span aria-label="自动刷新状态">
            {pollingActive ? '自动刷新 60 秒' : '自动刷新暂停（仅页面可见且盘前/盘中）'}
          </span>
        </div>
      </div>
      {error && (
        <div className="border-t border-[color:var(--warn-muted)] bg-bg-0 px-4 py-1.5 text-caption text-warning" role="status">
          市场脉搏暂不可用：{error}
        </div>
      )}
    </section>
  );
}
