import type React from 'react';
import { useState } from 'react';
import { fetchPositionEpisodes } from '../../api/journal';
import { parseApiError } from '../../api/error';
import type {
  EpisodeBuildMetadata,
  PositionEpisodeItem,
  PositionEpisodeReviewQueue,
} from '../../types/journal';

interface ReviewWorkbenchHeaderProps {
  build?: EpisodeBuildMetadata | null;
  reviewQueue?: PositionEpisodeReviewQueue | null;
  notBuilt?: boolean;
  loading?: boolean;
  viewingBuildId?: number;
  onOpenReview: (item: PositionEpisodeItem) => void;
  onOpenBuildTools?: () => void;
}

function buildSourceLabel(sourceKind?: string): string {
  if (sourceKind === 'csv_batch') return '历史 CSV 基线';
  if (sourceKind === 'canonical_set') return '可信事实集构建';
  if (sourceKind === 'position_snapshot_fenced_canonical') return '快照围栏构建';
  return sourceKind ?? '';
}

/**
 * 「复盘工作台」头部条：默认构建标识 + Review Queue 计数 + 「继续复盘下一笔」。
 *
 * 「继续复盘下一笔」的确定性优先级（只读，不改变任何后端排序口径）：
 * 1. 先续上「进行中」的回合（最近开仓优先），避免半途而废；
 * 2. 否则从「未开始」里按 top_loss 案例精选取第一笔——亏损最大的已平仓回合
 *    优先复盘，这是职业复盘的默认优先级（top_loss 只覆盖已平仓且净额已知
 *    的回合）；
 * 3. top_loss 无命中时退回「未开始」的最近开仓回合。
 */
export const ReviewWorkbenchHeader: React.FC<ReviewWorkbenchHeaderProps> = ({
  build,
  reviewQueue,
  notBuilt = false,
  loading = false,
  viewingBuildId,
  onOpenReview,
  onOpenBuildTools,
}) => {
  const [finding, setFinding] = useState(false);
  const [findMessage, setFindMessage] = useState<string | null>(null);

  const remaining = (reviewQueue?.pending ?? 0) + (reviewQueue?.inProgress ?? 0);
  const ctaDisabled = finding || loading || notBuilt || reviewQueue == null || remaining === 0;

  const handleContinue = async () => {
    if (finding) return;
    setFinding(true);
    setFindMessage(null);
    try {
      const base = { buildId: viewingBuildId, page: 1, perPage: 1 } as const;
      const inProgress = await fetchPositionEpisodes({ ...base, reviewStatus: 'in_progress' });
      let target = inProgress.items[0] ?? null;
      if (!target) {
        const topLoss = await fetchPositionEpisodes({
          ...base,
          reviewStatus: 'not_started',
          caseFocus: 'top_loss',
        });
        target = topLoss.items[0] ?? null;
      }
      if (!target) {
        const recent = await fetchPositionEpisodes({ ...base, reviewStatus: 'not_started' });
        target = recent.items[0] ?? null;
      }
      if (target) {
        onOpenReview(target);
      } else {
        setFindMessage('当前构建没有待复盘的回合；全部回合已完成复盘。');
      }
    } catch (reason) {
      setFindMessage(parseApiError(reason).message);
    } finally {
      setFinding(false);
    }
  };

  return (
    <section className="card-base p-4" aria-label="复盘工作台">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-48">
          <div className="text-label uppercase tracking-label text-text-3">Review workspace</div>
          <h2 className="mt-0.5 text-h2 text-text-1">复盘工作台</h2>
          <p className="mt-1 text-caption text-text-3">
            {notBuilt
              ? '尚未构建仓位回合'
              : viewingBuildId != null
                ? `对比视图 · 构建 #${viewingBuildId}（默认复盘构建未改变）`
                : build
                  ? `默认构建 #${build.id} · ${buildSourceLabel(build.sourceKind)}`
                  : loading
                    ? '正在读取默认构建…'
                    : '构建信息暂不可用'}
            {onOpenBuildTools && (
              <>
                {' '}·{' '}
                <button
                  type="button"
                  className="text-accent underline-offset-2 hover:underline"
                  onClick={onOpenBuildTools}
                >
                  管理数据与构建
                </button>
              </>
            )}
          </p>
        </div>

        {reviewQueue && (
          <dl className="flex items-center gap-2" aria-label="复盘队列">
            {[
              ['未开始', reviewQueue.pending, 'text-text-1'],
              ['进行中', reviewQueue.inProgress, 'text-accent'],
              ['已完成', reviewQueue.completed, 'text-up-strong'],
            ].map(([label, value, tone]) => (
              <div key={label as string} className="rounded-ds-sm border border-subtle bg-bg-2 px-3 py-2 text-center">
                <dt className="text-caption text-text-3">{label}</dt>
                <dd className={`mt-0.5 font-mono text-mono-md tabular-nums ${tone}`}>
                  {Number(value).toLocaleString()}
                </dd>
              </div>
            ))}
          </dl>
        )}

        <div className="flex flex-col items-end gap-1">
          <button
            type="button"
            className="btn-primary"
            disabled={ctaDisabled}
            onClick={() => void handleContinue()}
          >
            {finding ? '正在定位下一笔…' : '继续复盘下一笔'}
          </button>
          <span className="text-caption text-text-3">
            {notBuilt || reviewQueue == null
              ? '构建就绪后可从这里开始逐笔复盘'
              : remaining === 0
                ? '所有回合都已完成复盘'
                : '优先续上进行中，其次亏损最大的未复盘回合'}
          </span>
        </div>
      </div>
      {findMessage && (
        <p className="mt-2 text-caption text-warn-strong" role="status">{findMessage}</p>
      )}
    </section>
  );
};

export default ReviewWorkbenchHeader;
