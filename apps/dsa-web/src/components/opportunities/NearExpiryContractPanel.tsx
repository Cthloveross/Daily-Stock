import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { fetchNearExpiryContracts } from '../../api/opportunities';
import type {
  NearExpiryContractItem,
  NearExpiryContractRow,
  NearExpiryExpiryGroup,
} from '../../types/opportunities';

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
