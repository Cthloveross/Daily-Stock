import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { fetchNearExpiryContracts } from '../../api/opportunities';
import { usePersonalEdge, type PersonalEdgeView } from '../../hooks/usePersonalEdge';
import type {
  IntradayEarningsProximity,
  NearExpiryContractItem,
  NearExpiryContractRow,
  NearExpiryExpiryGroup,
} from '../../types/opportunities';
import { Tooltip } from '../common/Tooltip';
import { formatSignedCompactUsd } from './intradayFormat';

/**
 * 点差 > 15% 标「流动性差」。这是 v1 启发式展示阈值（未经交易结果验证），
 * 只提示点差成本显著，不是合约质量评分；改动无需升级后端 schema。
 */
export const SPREAD_ILLIQUID_THRESHOLD_PERCENT = 15;

const GROUP_STATE_NOTES: Record<NearExpiryExpiryGroup['state'], string | null> = {
  ready: null,
  partial: '部分合约快照缺失，对应行显式标缺',
  unavailable: '本到期日动态快照缺失，各行显式标缺',
};

function formatPrice(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatCount(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '标缺';
  return value.toLocaleString('en-US');
}

function quoteTimeLabel(value: string | null): string {
  if (!value) return '标缺';
  const match = value.match(/\d{2}:\d{2}(?::\d{2})?/);
  return match ? `${match[0]} ET` : value;
}

/**
 * v3 财报临近头标（与扫描表财报列同一套语义与样式）：
 * - 回避窗内（≤blackoutDays 天）→ 醒目警示「财报 N 天内 · 期权贵 ·
 *   你的回避规则」（0 天 →「今日财报…」），在看合约的瞬间可见；
 * - ready 且在回避窗外 → 不渲染任何标注（不制造噪音）；
 * - 日历不可得（或旧缓存载荷缺字段）→ 小字「财报日历标缺 · 未知≠安全」，
 *   绝不以缺失冒充安全。
 */
function EarningsProximityBadge({
  proximity,
}: {
  proximity: IntradayEarningsProximity | undefined;
}) {
  if (!proximity || proximity.state !== 'ready') {
    return (
      <span className="text-caption text-text-3">财报日历标缺 · 未知≠安全</span>
    );
  }
  if (!proximity.withinBlackout || proximity.daysToEarnings === null) {
    return null;
  }
  return (
    <span className="inline-block rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning">
      {proximity.daysToEarnings === 0
        ? '今日财报 · 期权贵 · 你的回避规则'
        : `财报 ${proximity.daysToEarnings} 天内 · 期权贵 · 你的回避规则`}
    </span>
  );
}

/** DTE 提示只展示临期决策相关的前三档；数值全部来自 personal-edge 端点。 */
const PERSONAL_DTE_CAPTION_BUCKETS = ['0', '1-3', '4-7'] as const;

const PERSONAL_DTE_BUCKET_LABELS: Record<string, string> = {
  '0': '0DTE',
  '1-3': '1-3DTE',
  '4-7': '4-7DTE',
};

function personalDteSampleLabel(view: Extract<PersonalEdgeView, { state: 'ready' }>): string {
  const months = view.data.monthly;
  if (months.length === 0) return '';
  const first = months[0]?.month;
  const last = months[months.length - 1]?.month;
  if (!first || !last) return '';
  return first === last ? ` · 样本 ${first}` : ` · 样本 ${first}→${last}`;
}

/**
 * 个人 DTE 战绩提示行（个人画像回灌）：在看临期合约的瞬间给出你自己的
 * DTE 分层历史（净盈亏 + 胜率 + 样本期），数值全部来自 personal-edge 端点
 * 的实时重算，绝不硬编码；空档位显式「无样本」，端点缺席显式「标缺」。
 * tooltip 原文携带服务端 limitations（内生性 caveat），描述非因果、非建议。
 */
function PersonalDteCaption({ view }: { view: PersonalEdgeView }) {
  if (view.state === 'loading') return null;
  if (view.state === 'unavailable') {
    return (
      <div className="border-b border-subtle bg-bg-0 px-3 py-1.5 text-caption text-text-3">
        你的 DTE 战绩：标缺（个人战绩不可得 · Journal 未构建或读取失败）
      </div>
    );
  }
  const byBucket = new Map(view.data.dteBuckets.map((bucket) => [bucket.bucket, bucket]));
  const parts = PERSONAL_DTE_CAPTION_BUCKETS.map((key) => {
    const bucket = byBucket.get(key);
    const label = PERSONAL_DTE_BUCKET_LABELS[key] ?? key;
    if (!bucket || bucket.n === 0 || bucket.winRate === null) {
      return `${label} 无样本`;
    }
    return `${label} ${formatSignedCompactUsd(bucket.net)}(${Math.round(bucket.winRate * 100)}%)`;
  });
  const tooltip = `你的历史已平仓回合按进场 DTE 分层（build #${view.data.buildId ?? '—'}）。`
    + `${view.data.limitations.join('；')}`;
  return (
    <div className="border-b border-subtle bg-bg-0 px-3 py-1.5 text-caption text-text-3">
      <Tooltip focusable content={tooltip}>
        <span aria-label={tooltip}>
          你的 DTE 战绩：{parts.join(' · ')}
          {personalDteSampleLabel(view)}
          {' · 描述非因果'}
        </span>
      </Tooltip>
    </div>
  );
}

function bidAskLabel(row: NearExpiryContractRow): string {
  if (row.bid === null || row.ask === null) return '标缺';
  const spread = row.spreadPercent === null
    ? '点差标缺'
    : `${row.spreadPercent.toFixed(1)}%`;
  return `${formatPrice(row.bid)} × ${formatPrice(row.ask)}（${spread}）`;
}

function ContractTable({ group }: { group: NearExpiryExpiryGroup }) {
  return (
    <table className="w-full min-w-[760px] border-collapse" aria-label={`${group.expiry} 合约表`}>
      <thead>
        <tr className="border-b border-subtle text-left text-caption text-text-3">
          <th className="px-3 py-1.5 font-medium">行权价</th>
          <th className="px-3 py-1.5 font-medium">Call/Put</th>
          <th className="px-3 py-1.5 text-right font-medium">Bid × Ask（点差%）</th>
          <th className="px-3 py-1.5 text-right font-medium">最新价</th>
          <th className="px-3 py-1.5 text-right font-medium">当日量</th>
          <th className="px-3 py-1.5 text-right font-medium">OI（T-1）</th>
          <th className="px-3 py-1.5 text-right font-medium">IV</th>
          <th className="px-3 py-1.5 text-right font-medium">as-of</th>
        </tr>
      </thead>
      <tbody>
        {group.contracts.map((row) => {
          const illiquid = row.spreadPercent !== null
            && row.spreadPercent > SPREAD_ILLIQUID_THRESHOLD_PERCENT;
          return (
            <tr
              key={row.code}
              className={`border-b border-subtle text-body-sm last:border-b-0 ${
                row.isAtm ? 'bg-bg-2' : ''
              }`}
            >
              <td className="px-3 py-1.5 font-mono text-mono-xs text-text-1">
                {formatPrice(row.strike, row.strike % 1 === 0 ? 0 : 2)}
                {row.isAtm && (
                  <span className="ml-1.5 rounded-ds-sm border border-subtle px-1 text-caption text-text-2">
                    ATM
                  </span>
                )}
              </td>
              <td className="px-3 py-1.5 text-text-2">{row.right === 'C' ? 'Call' : 'Put'}</td>
              <td className="px-3 py-1.5 text-right font-mono text-mono-xs text-text-1">
                {bidAskLabel(row)}
                {illiquid && (
                  <span className="ml-1.5 text-caption text-warning">流动性差</span>
                )}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-mono-xs text-text-2">
                {row.lastPrice === null ? '标缺' : formatPrice(row.lastPrice)}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-mono-xs text-text-2">
                {formatCount(row.sessionVolume)}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-mono-xs text-text-2">
                {formatCount(row.openInterest)}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-mono-xs text-text-2">
                {row.ivPercent === null ? '标缺' : `${row.ivPercent.toFixed(1)}%`}
              </td>
              <td className="px-3 py-1.5 text-right text-caption text-text-3">
                {quoteTimeLabel(row.quoteAsOf)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/**
 * 临期合约面板（0–maxDte DTE）：合约选择支持，只读研究数据。
 *
 * 显示近价窗口内 Call/Put 合约的 bid/ask/点差/最新价/当日量/T-1 OI/
 * 供应商 IV 与 as-of，按到期日分组、按行权价中性排序。不打分、不排序
 * 偏好、不生成买卖建议；缺失字段显式「标缺」，绝不以 0 冒充。
 */
export function NearExpiryContractPanel({
  symbol,
  maxDte = 3,
}: {
  symbol: string;
  maxDte?: number;
}) {
  const [item, setItem] = useState<NearExpiryContractItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 个人画像回灌：DTE 提示行数据；失败/未构建显式标缺（10 分钟会话缓存）。
  const personalEdge = usePersonalEdge();

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    try {
      const response = await fetchNearExpiryContracts(symbol, maxDte, { refresh });
      setItem(response.item);
      setError(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : '临期合约读取失败');
    } finally {
      setLoading(false);
    }
  }, [symbol, maxDte]);

  useEffect(() => {
    void load(false);
  }, [load]);

  return (
    <section
      aria-label={`${symbol} 临期合约`}
      className="rounded-ds-md border border-subtle bg-bg-1"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-3 py-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="text-body-sm font-semibold text-text-1">
            临期合约 · {symbol}（0–{maxDte} DTE）
          </h3>
          {item && <EarningsProximityBadge proximity={item.earningsProximity} />}
          <span className="text-caption text-text-3">
            合约选择参考 · 不构成推荐 · 以券商实时盘口为准
          </span>
        </div>
        <div className="flex items-center gap-3">
          {item?.spot !== null && item?.spot !== undefined && (
            <span className="text-caption text-text-3">
              Spot {formatPrice(item.spot)}
              {' · '}
              {quoteTimeLabel(item.spotAsOf)}
            </span>
          )}
          <button
            type="button"
            disabled={loading}
            onClick={() => void load(true)}
            className="flex items-center gap-1 text-caption text-text-2 hover:text-text-1 disabled:opacity-50"
            aria-label={`刷新 ${symbol} 临期合约`}
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : undefined} />
            刷新
          </button>
        </div>
      </header>

      <PersonalDteCaption view={personalEdge} />

      {error && (
        <div className="border-b border-[color:var(--warn-muted)] bg-bg-0 px-3 py-2 text-caption text-warning" role="status">
          临期合约暂不可用：{error}
          {item ? '。仍显示上一次成功结果。' : ''}
        </div>
      )}

      {!item && loading ? (
        <div className="space-y-2 p-3">
          {[0, 1].map((row) => (
            <div key={row} className="h-8 animate-pulse rounded-ds-sm bg-bg-2" />
          ))}
        </div>
      ) : !item ? null : item.state === 'not_configured' || item.state === 'unavailable' || item.state === 'empty' ? (
        <div className="px-3 py-4 text-body-sm text-text-3">{item.message}</div>
      ) : (
        <div className="space-y-3 p-3">
          {item.expiries.map((group) => (
            <div key={group.expiry}>
              <div className="mb-1 flex flex-wrap items-baseline gap-x-2 text-caption">
                <span className="font-medium text-text-1">
                  {group.dte} DTE · {group.expiry}
                </span>
                <span className="text-text-3">
                  {group.observedQuoteCount}/{group.contractCount} 张有观测报价
                </span>
                {GROUP_STATE_NOTES[group.state] && (
                  <span className="text-warning">{GROUP_STATE_NOTES[group.state]}</span>
                )}
              </div>
              <div className="overflow-x-auto">
                <ContractTable group={group} />
              </div>
            </div>
          ))}
        </div>
      )}

      {item && item.state !== 'not_configured' && (
        <footer className="border-t border-subtle bg-bg-0 px-3 py-2 text-caption text-text-3">
          <span>
            OI 为 T-1 清算口径
            {item.openInterestAsOf ? `（截至 ${item.openInterestAsOf}）` : ''}
            {' '}
            · 点差% = (Ask−Bid)÷Mid，&gt;{SPREAD_ILLIQUID_THRESHOLD_PERCENT}% 标「流动性差」（v1 启发式展示阈值）
            · 行权价窗口：{item.strikeWindow.basis}
            · ATM 仅为距现价最近的位置标记
          </span>
          <ul className="mt-1 list-inside list-disc">
            {item.limitations.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </footer>
      )}
    </section>
  );
}
