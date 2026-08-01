import type {
  OpportunityOptionWallLevel,
  OpportunityOptionWallLevelExpiry,
} from '../../types/opportunities';

// 逐层口径标签：OI 为 T-1 清算存量，Volume 为当前交易日累计，
// Gamma 为模型集中度（清算 OI × 当前快照 Greeks），与榜单数据时点文案一致。
const METRIC_BASIS_LABELS: Record<string, string> = {
  settled_open_interest_prior_session: 'OI＝T-1 清算',
  current_session_cumulative_volume: 'Volume＝当前交易日累计',
  model_from_settled_oi_and_snapshot_greeks: 'Gamma＝模型值（T-1 清算 OI × 当前快照 Greeks）',
};

const QUOTE_EVIDENCE_LABELS: Record<string, string> = {
  observed: '报价证据完整',
  partial: '报价证据部分缺失',
  unavailable: '报价证据缺失',
};

function formatShare(value: number): string {
  return `${value.toLocaleString('en-US', { maximumFractionDigits: 1 })}%`;
}

function formatQuotePrice(value: number | null): string {
  return value === null
    ? '标缺'
    : value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function expiryQuoteText(entry: OpportunityOptionWallLevelExpiry): string {
  if (entry.contractCount > 1) {
    return `聚合 ${entry.contractCount} 张合约 · 逐合约报价不归属 · 标缺`;
  }
  const iv = entry.quote.ivPercent !== null
    ? `IV ${entry.quote.ivPercent.toLocaleString('en-US', { maximumFractionDigits: 1 })}%`
    : 'IV 标缺';
  const hasAnyPrice = entry.quote.bid !== null || entry.quote.ask !== null || entry.quote.mark !== null;
  const price = hasAnyPrice
    ? `Bid ${formatQuotePrice(entry.quote.bid)} / Ask ${formatQuotePrice(entry.quote.ask)} / Mark ${formatQuotePrice(entry.quote.mark)}`
    : 'Bid/Ask/Mark 标缺（快照未含盘口报价）';
  return `${iv} · ${price}`;
}

/**
 * 单个墙位（strike × 方向）的到期分布与逐层报价证据。
 * 仅展示快照真实观察到的字段；缺失字段显式标缺，不回填。
 * 墙位仍是集中度区域，不是 dealer GEX / gamma flip。
 */
export function WallLevelExpiryBreakdown({ level }: { level: OpportunityOptionWallLevel }) {
  const breakdown = level.expiryBreakdown;
  if (!breakdown || breakdown.topExpiries.length === 0) return null;

  const basisLabel = level.metricBasis ? METRIC_BASIS_LABELS[level.metricBasis] : null;
  const evidenceLabel = level.quoteEvidence ? QUOTE_EVIDENCE_LABELS[level.quoteEvidence] : null;
  const summaryParts = ['到期分布', basisLabel, evidenceLabel].filter(Boolean);

  return (
    <details className="mt-0.5">
      <summary className="cursor-pointer select-none text-[11px] text-text-3 hover:text-text-2">
        {summaryParts.join(' · ')}
      </summary>
      <ul className="mt-1 space-y-0.5 text-[11px] leading-4 text-text-3">
        {breakdown.topExpiries.map((entry) => (
          <li key={entry.expiry} className="font-mono">
            {entry.expiry}
            {entry.dte !== null ? ` · DTE ${entry.dte}` : ' · DTE 标缺'}
            {' · '}占该位 {formatShare(entry.shareOfLevelPercent)}
            {' · '}{expiryQuoteText(entry)}
            {entry.quote.quoteAsOf ? ` · ${entry.quote.quoteAsOf}` : ''}
          </li>
        ))}
        {breakdown.other && (
          <li className="font-mono">
            其余 {breakdown.other.expiryCount} 个到期日 · 占该位 {formatShare(breakdown.other.shareOfLevelPercent)}
          </li>
        )}
        {level.quoteEvidence !== 'observed' && (
          <li>标缺字段为快照未提供的数据，未用估算或旧值回填。</li>
        )}
      </ul>
    </details>
  );
}

export default WallLevelExpiryBreakdown;
