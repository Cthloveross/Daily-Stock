import { fetchPositionEpisodes } from '../../../api/journal';
import type { PositionEpisodeItem } from '../../../types/journal';

export interface FindNextReviewEpisodeOptions {
  /** 显式构建视图；不传时沿用后端默认构建。 */
  buildId?: number;
  /** 正在复盘的回合；解析“下一笔”时排除它，避免原地打转。 */
  excludeEpisodeId?: number;
  /** 每档查询的候选数量；只需容纳“排除当前回合”后仍有命中。 */
  perPage?: number;
}

/**
 * 「继续复盘下一笔」的唯一实现（复盘工作台头部 CTA 与单笔复盘页共用）。
 *
 * 确定性优先级（只读，不改变任何后端排序口径）：
 * 1. 先续上「进行中」的回合（最近开仓优先），避免半途而废；
 * 2. 否则从「未开始」里按 top_loss 案例精选取第一笔——亏损最大的已平仓回合
 *    优先复盘（top_loss 只覆盖已平仓且净额已知的回合）；
 * 3. top_loss 无命中时退回「未开始」的最近开仓回合。
 *
 * 三档都无命中时返回 null，由调用方如实提示“全部回合已完成复盘”，
 * 绝不伪造下一笔。
 */
export async function findNextReviewEpisode(
  options: FindNextReviewEpisodeOptions = {},
): Promise<PositionEpisodeItem | null> {
  const { buildId, excludeEpisodeId, perPage = 5 } = options;
  const base = { buildId, page: 1, perPage } as const;
  const attempts = [
    { ...base, reviewStatus: 'in_progress' as const },
    { ...base, reviewStatus: 'not_started' as const, caseFocus: 'top_loss' as const },
    { ...base, reviewStatus: 'not_started' as const },
  ];
  for (const filters of attempts) {
    const response = await fetchPositionEpisodes(filters);
    const target = response.items.find((item) => item.id !== excludeEpisodeId);
    if (target) return target;
  }
  return null;
}

export default findNextReviewEpisode;
