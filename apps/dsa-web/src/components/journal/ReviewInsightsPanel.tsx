import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { createPlaybookCandidate, fetchReviewInsights } from '../../api/journal';
import { parseApiError, type ParsedApiError } from '../../api/error';
import type { ReviewInsightBucket, ReviewInsightsResponse } from '../../types/journal';

/**
 * 「模式观察」（Playbook 合同切片 C-1）：只读聚合当前默认构建的最新复盘标注。
 * 统计口径 fail-closed——样本不足只显示计数；条件性 P&L 永不进入比率。
 * 切片 C-2：每个分桶提供「保存为候选」——显式提交后冻结当时的证据快照，
 * 候选/规则永不反写任何系统评分或榜单。
 */

const DIRECTION_LABEL: Record<string, string> = {
  LONG: '做多',
  SHORT: '做空',
};

const fmtMoney = (raw?: string | null): string => {
  if (raw == null || raw === '') return '—';
  const value = Number(raw);
  if (!Number.isFinite(value)) return raw;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toFixed(2)}`;
};

const fmtRate = (raw?: string | null): string => {
  if (raw == null || raw === '') return '—';
  const value = Number(raw);
  if (!Number.isFinite(value)) return raw;
  return `${(value * 100).toFixed(0)}%`;
};

const BucketRow: React.FC<{
  bucket: ReviewInsightBucket;
  minEpisodeCount: number;
  minDistinctTradingDayCount: number;
  onCandidateSaved?: () => void;
}> = ({ bucket, minEpisodeCount, minDistinctTradingDayCount, onCandidateSaved }) => {
  const kindLabel = bucket.groupKind === 'tag' ? '标签' : '错误类型';
  const directionLabel = DIRECTION_LABEL[bucket.direction] ?? bucket.direction;
  const boundaryVerified = bucket.boundaryPolicy === 'verified';
  const stats = bucket.stats ?? null;
  const [formOpen, setFormOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [ruleText, setRuleText] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<ParsedApiError | null>(null);
  const [savedReplay, setSavedReplay] = useState<boolean | null>(null);

  const canSubmit = title.trim().length > 0 && ruleText.trim().length > 0 && !saving;

  const submit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    setSaveError(null);
    try {
      const response = await createPlaybookCandidate({
        title,
        ruleText,
        sourceBucket: {
          groupKind: bucket.groupKind,
          groupValue: bucket.groupValue,
          direction: bucket.direction,
          boundaryPolicy: bucket.boundaryPolicy,
        },
      });
      setSavedReplay(response.idempotentReplay);
      setFormOpen(false);
      setTitle('');
      setRuleText('');
      onCandidateSaved?.();
    } catch (e) {
      setSaveError(parseApiError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <li className="flex flex-col gap-1 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2 py-0.5 text-caption ${
            bucket.groupKind === 'tag'
              ? 'bg-accent/10 text-accent'
              : 'bg-warn-subtle text-warn-strong'
          }`}
        >
          {kindLabel} · {bucket.groupValue}
        </span>
        <span className="text-body-sm text-text-2">{directionLabel}</span>
        <span
          className={`text-caption ${
            boundaryVerified ? 'text-up-strong' : 'text-warn-strong'
          }`}
        >
          {boundaryVerified ? '边界已验证' : '边界假设/截断'}
        </span>
        <span className="font-mono text-mono-xs tabular-nums text-text-2">
          {bucket.episodeCount} 笔 · {bucket.distinctTradingDayCount} 个交易日 · 已复盘{' '}
          {bucket.reviewCompletedCount}
        </span>
        <button
          type="button"
          className="btn-ghost text-caption"
          onClick={() => {
            setFormOpen((open) => !open);
            setSaveError(null);
            setSavedReplay(null);
          }}
        >
          保存为候选
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-caption">
        {stats ? (
          <span className="font-mono tabular-nums text-text-1">
            胜率 {fmtRate(stats.winRate)}（{stats.winCount} 胜 / {stats.lossCount} 负）· 合计{' '}
            {fmtMoney(stats.sumPnl)} · 单笔均值 {fmtMoney(stats.avgPnl)}
          </span>
        ) : bucket.statsGate.reason === 'no_verified_pnl_episodes' ? (
          <span className="text-text-3">无已验证 P&amp;L 样本，仅显示计数</span>
        ) : (
          <span className="text-text-3">
            样本不足（&lt;{minEpisodeCount} 笔或 &lt;{minDistinctTradingDayCount} 个交易日），仅显示计数
          </span>
        )}
        {bucket.conditionalEpisodeCount > 0 && (
          <span className="text-warn-strong">
            条件性 P&amp;L {bucket.conditionalEpisodeCount} 笔 · 不参与统计
          </span>
        )}
      </div>
      {savedReplay !== null && !formOpen && (
        <p className="text-caption text-up-strong" role="status">
          {savedReplay ? '相同内容的候选已存在（幂等回放）。' : '已保存为候选，可在下方 Playbook 面板显式晋升。'}
        </p>
      )}
      {formOpen && (
        <form
          className="mt-1 flex flex-col gap-2 rounded-ds-sm border border-subtle bg-bg-0 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="flex flex-col gap-1 text-caption text-text-2">
            候选标题
            <input
              type="text"
              className="input-base text-body-sm"
              maxLength={120}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="一句话概括这个模式"
            />
          </label>
          <label className="flex flex-col gap-1 text-caption text-text-2">
            规则描述
            <textarea
              className="input-base min-h-[72px] text-body-sm"
              maxLength={2000}
              value={ruleText}
              onChange={(event) => setRuleText(event.target.value)}
              placeholder="用自己的话写下可执行的规则"
            />
          </label>
          <p className="text-caption text-text-3">
            保存会冻结当前分桶的证据快照（构建、回合、标注修订与计数）；候选与规则不会影响系统评分或榜单。
          </p>
          {saveError && (
            <p className="text-caption text-down-strong" role="alert">
              {saveError.message}
            </p>
          )}
          <div className="flex items-center gap-2">
            <button type="submit" className="btn-primary text-body-sm" disabled={!canSubmit}>
              保存候选
            </button>
            <button
              type="button"
              className="btn-ghost text-body-sm"
              onClick={() => setFormOpen(false)}
              disabled={saving}
            >
              取消
            </button>
          </div>
        </form>
      )}
    </li>
  );
};

export const ReviewInsightsPanel: React.FC<{ onCandidateSaved?: () => void }> = ({
  onCandidateSaved,
}) => {
  const [insights, setInsights] = useState<ReviewInsightsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setInsights(await fetchReviewInsights());
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const thresholds = insights?.thresholds;
  const minEpisodeCount = thresholds?.minEpisodeCount ?? 10;
  const minDistinctTradingDayCount = thresholds?.minDistinctTradingDayCount ?? 5;

  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1" aria-label="模式观察">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Pattern observation</div>
          <h2 className="mt-0.5 text-h2 text-text-1">模式观察</h2>
        </div>
        <div className="flex items-center gap-2">
          {insights?.dataState === 'ready' && (
            <span className="font-mono text-mono-xs text-text-3">
              Build #{insights.buildId} · {insights.sourceKind}
            </span>
          )}
          <button type="button" className="btn-ghost text-body-sm" onClick={() => void load()} disabled={loading}>
            刷新
          </button>
        </div>
      </div>

      <div className="px-4 py-3">
        {loading && !insights && (
          <p className="py-4 text-center text-body-sm text-text-3">聚合复盘标注中…</p>
        )}

        {error && (
          <p className="text-body-sm text-down-strong" role="alert">
            {error.message}
          </p>
        )}

        {!error && insights?.dataState === 'not_built' && (
          <p className="text-body-sm text-text-3">
            还没有 Episode 构建。导入交易证据并生成构建后，这里会按复盘标签聚合可观察的模式。
          </p>
        )}

        {!error && insights?.dataState === 'ready' && (
          <div className="space-y-3">
            <p className="text-caption text-text-3">
              共 {insights.totalEpisodeCount.toLocaleString()} 个回合 · 已标注{' '}
              {insights.annotatedEpisodeCount.toLocaleString()} · 按最新复盘标注的标签/错误类型 ×
              方向 × 边界口径分桶；统计仅在 ≥{minEpisodeCount} 笔且 ≥{minDistinctTradingDayCount}{' '}
              个独立交易日的已验证 P&amp;L 样本上显示。
            </p>

            {insights.unreviewed && insights.unreviewed.episodeCount > 0 && (
              <div className="rounded-ds-sm border border-subtle bg-bg-0 px-3 py-2 text-body-sm text-text-2">
                未复盘 ·{' '}
                <span className="font-mono tabular-nums">
                  {insights.unreviewed.episodeCount.toLocaleString()} 笔 ·{' '}
                  {insights.unreviewed.distinctTradingDayCount.toLocaleString()} 个交易日
                </span>
                <span className="ml-2 text-caption text-text-3">仅计数，永不显示盈亏统计</span>
              </div>
            )}

            {insights.buckets.length === 0 ? (
              <p className="text-body-sm text-text-3">
                尚无带标签的复盘标注。完成单笔复盘并添加标签或错误类型后，这里会出现可观察的模式分组。
              </p>
            ) : (
              <ul className="divide-y divide-[color:var(--border-subtle)]">
                {insights.buckets.map((bucket) => (
                  <BucketRow
                    key={`${bucket.groupKind}:${bucket.groupValue}:${bucket.direction}:${bucket.boundaryPolicy}`}
                    bucket={bucket}
                    minEpisodeCount={minEpisodeCount}
                    minDistinctTradingDayCount={minDistinctTradingDayCount}
                    onCandidateSaved={onCandidateSaved}
                  />
                ))}
              </ul>
            )}

            <p className="border-t border-subtle pt-2 text-caption text-text-3">
              观察到的模式 ≠ 已验证规则；保存候选与晋升规则都是你的显式操作，系统永不自动晋升。
            </p>
          </div>
        )}
      </div>
    </section>
  );
};

export default ReviewInsightsPanel;
