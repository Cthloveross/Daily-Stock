import type React from 'react';
import type {
  OpportunityOptionWallItem,
  OpportunityOptionWallRatio,
} from '../../types/opportunities';

/**
 * call/put 比例与未平仓加权中心的**诚实呈现**。
 *
 * 这个面板刻意只做一件事：把「现在盘口上摆着什么」如实说出来。它**不**把
 * 比例翻译成方向，也**不**把加权中心叫成 max pain 或目标位——因为本仓库
 * 没有任何历史 OI 序列，「高 call 比例＝会涨」「价格会向最大痛点靠拢」这
 * 两个说法在用户自己的数据上一次都没有被检验过。caveat 文案由后端下发并
 * 逐字渲染，前端不改写、不省略。
 */

const BASIS_LABELS: Record<string, string> = {
  settled_open_interest_prior_session: 'OI＝上一交易日结算后的未平仓量',
  current_session_cumulative_volume: 'Volume＝当日累计成交量',
};

function formatRatio(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—';
  return value.toFixed(2);
}

function formatCount(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function RatioRow({
  label,
  ratio,
}: {
  label: string;
  ratio: OpportunityOptionWallRatio;
}) {
  const undefinedRatio = ratio.value === null;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-text-3">{label}</span>
        <span
          className={
            undefinedRatio
              ? 'font-mono text-mono-xs text-text-3'
              : 'font-mono text-mono-sm tabular-nums text-text-1'
          }
        >
          {formatRatio(ratio.value)}
        </span>
      </div>
      <div className="flex items-baseline justify-between gap-3 text-caption text-text-3">
        <span>{BASIS_LABELS[ratio.metricBasis] ?? ratio.metricBasis}</span>
        <span className="font-mono text-mono-xs tabular-nums">
          {formatCount(ratio.numeratorTotal)} / {formatCount(ratio.denominatorTotal)}
        </span>
      </div>
      {undefinedRatio && ratio.reason && (
        <div className="text-caption text-warning">比例无定义 · {ratio.reason}</div>
      )}
    </div>
  );
}

export const OptionWallRatioPanel: React.FC<{
  item: OpportunityOptionWallItem;
}> = ({ item }) => {
  const ratios = item.ratios;
  if (!ratios) return null;
  const center = item.oiWeightedCenter ?? null;

  return (
    <section
      className="space-y-2 rounded-ds-sm border border-subtle bg-bg-2 p-3"
      aria-label="call/put 比例与未平仓加权中心"
    >
      <div className="text-label uppercase tracking-label text-text-3">
        Call / Put 比例（事实描述）
      </div>
      <div className="space-y-2 text-body-sm">
        <RatioRow label="Call / Put OI" ratio={ratios.callPutOiRatio} />
        <RatioRow label="Call / Put Volume" ratio={ratios.callPutVolumeRatio} />
      </div>

      {center && (
        <div className="border-t border-subtle pt-2">
          <div className="flex items-baseline justify-between gap-3 text-body-sm">
            <span className="text-text-3">{center.label}</span>
            <span className="font-mono text-mono-sm tabular-nums text-text-2">
              {center.strike === null
                ? '—'
                : center.strike.toLocaleString('en-US', { maximumFractionDigits: 2 })}
            </span>
          </div>
          {center.strike === null && center.reason && (
            <div className="text-caption text-text-3">{center.reason}</div>
          )}
        </div>
      )}

      {/* 后端下发的标准 caveat，逐字渲染——这是本页最重要的一句话。 */}
      <p className="border-l-2 border-[color:var(--warn-muted)] pl-3 text-caption text-text-2">
        {ratios.caveat}
      </p>
    </section>
  );
};

export default OptionWallRatioPanel;
