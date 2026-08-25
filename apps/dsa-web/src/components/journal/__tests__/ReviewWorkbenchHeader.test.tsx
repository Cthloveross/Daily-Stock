import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ReviewWorkbenchHeader from '../ReviewWorkbenchHeader';
import { fetchPositionEpisodes } from '../../../api/journal';
import {
  closedEpisode,
  openAggregateEpisode,
  readyList,
} from './positionEpisodeFixtures';
import type { PositionEpisodeListResponse } from '../../../types/journal';

vi.mock('../../../api/journal', () => ({
  fetchPositionEpisodes: vi.fn(),
}));

const fetchEpisodesMock = vi.mocked(fetchPositionEpisodes);

function listWith(items: PositionEpisodeListResponse['items']): PositionEpisodeListResponse {
  return { ...readyList, items, total: items.length };
}

describe('ReviewWorkbenchHeader', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the default build identity and the review queue counters', () => {
    render(
      <ReviewWorkbenchHeader
        build={readyList.build}
        reviewQueue={readyList.reviewQueue}
        onOpenReview={vi.fn()}
      />,
    );

    expect(screen.getByText('复盘工作台')).toBeInTheDocument();
    expect(screen.getByText(/默认构建 #7 · 历史 CSV 基线/)).toBeInTheDocument();
    const queue = screen.getByLabelText('复盘队列');
    expect(within(queue).getByText('未开始')).toBeInTheDocument();
    expect(within(queue).getByText('4')).toBeInTheDocument();
    expect(within(queue).getByText('进行中')).toBeInTheDocument();
    expect(within(queue).getByText('1')).toBeInTheDocument();
    expect(within(queue).getByText('已完成')).toBeInTheDocument();
    expect(within(queue).getByText('5')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '继续复盘下一笔' })).toBeEnabled();
  });

  it('resumes the in-progress episode first', async () => {
    const onOpenReview = vi.fn();
    fetchEpisodesMock.mockResolvedValueOnce(listWith([openAggregateEpisode]));
    render(
      <ReviewWorkbenchHeader
        build={readyList.build}
        reviewQueue={readyList.reviewQueue}
        onOpenReview={onOpenReview}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '继续复盘下一笔' }));
    await waitFor(() => expect(onOpenReview).toHaveBeenCalledWith(openAggregateEpisode));
    expect(fetchEpisodesMock).toHaveBeenCalledTimes(1);
    // 共享 helper（nextReviewEpisode）用小页拉候选，仍是同一优先级实现。
    expect(fetchEpisodesMock).toHaveBeenCalledWith(expect.objectContaining({
      reviewStatus: 'in_progress',
      page: 1,
      perPage: 5,
    }));
  });

  it('falls back to the top-loss unreviewed episode and keeps the viewed build', async () => {
    const onOpenReview = vi.fn();
    fetchEpisodesMock
      .mockResolvedValueOnce(listWith([]))
      .mockResolvedValueOnce(listWith([closedEpisode]));
    render(
      <ReviewWorkbenchHeader
        build={readyList.build}
        reviewQueue={readyList.reviewQueue}
        viewingBuildId={9}
        onOpenReview={onOpenReview}
      />,
    );

    expect(screen.getByText(/对比视图 · 构建 #9/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '继续复盘下一笔' }));
    await waitFor(() => expect(onOpenReview).toHaveBeenCalledWith(closedEpisode));
    expect(fetchEpisodesMock).toHaveBeenNthCalledWith(2, expect.objectContaining({
      reviewStatus: 'not_started',
      caseFocus: 'top_loss',
      buildId: 9,
    }));
  });

  it('reports an all-done state instead of pretending there is a next episode', async () => {
    fetchEpisodesMock.mockResolvedValue(listWith([]));
    render(
      <ReviewWorkbenchHeader
        build={readyList.build}
        reviewQueue={{ pending: 1, inProgress: 0, completed: 9, total: 10 }}
        onOpenReview={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '继续复盘下一笔' }));
    expect(await screen.findByText(/全部回合已完成复盘/)).toBeInTheDocument();
    // in_progress -> not_started(top_loss) -> not_started(recent)
    expect(fetchEpisodesMock).toHaveBeenCalledTimes(3);
  });

  it('disables the CTA when nothing is left to review and offers the data tab link', () => {
    const onOpenBuildTools = vi.fn();
    render(
      <ReviewWorkbenchHeader
        build={readyList.build}
        reviewQueue={{ pending: 0, inProgress: 0, completed: 10, total: 10 }}
        onOpenReview={vi.fn()}
        onOpenBuildTools={onOpenBuildTools}
      />,
    );

    expect(screen.getByRole('button', { name: '继续复盘下一笔' })).toBeDisabled();
    expect(screen.getByText('所有回合都已完成复盘')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '管理数据与构建' }));
    expect(onOpenBuildTools).toHaveBeenCalledTimes(1);
  });

  it('stays honest when episodes are not built yet', () => {
    render(
      <ReviewWorkbenchHeader
        build={null}
        reviewQueue={null}
        notBuilt
        onOpenReview={vi.fn()}
      />,
    );

    expect(screen.getByText(/尚未构建仓位回合/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '继续复盘下一笔' })).toBeDisabled();
    expect(screen.getByText('构建就绪后可从这里开始逐笔复盘')).toBeInTheDocument();
  });
});
