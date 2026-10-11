import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PositionEpisodesPanel from '../PositionEpisodesPanel';
import {
  episodeDetail,
  makeController,
  openAggregateEpisode,
} from './positionEpisodeFixtures';

describe('PositionEpisodesPanel (review workspace)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('separates partial coverage, conditional PnL, and strict headline PnL', () => {
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController()}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    expect(screen.getByText('1,089 / 3,542')).toBeInTheDocument();
    expect(screen.getByText(/这不代表整批通过/)).toBeInTheDocument();
    expect(screen.getByText('按期初空仓假设的条件性结果')).toBeInTheDocument();
    expect(screen.getByText('不进入 Headline')).toBeInTheDocument();
    const conditionalCard = screen.getByLabelText('按期初空仓假设的条件性结果');
    expect(within(conditionalCard).getByText('$123.75')).toBeInTheDocument();

    const replayBasis = screen.getByRole('region', { name: '复盘口径' });
    expect(within(replayBasis).getByText('证据窗口（ET）起—止')).toBeInTheDocument();
    expect(within(replayBasis).getByText('窗口末投影 as-of（ET）')).toBeInTheDocument();
    expect(within(replayBasis).getByText(/不等于券商当前持仓/)).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '证据窗口末数量' })).toBeInTheDocument();
    expect(screen.queryByText('Remaining')).not.toBeInTheDocument();

    const verifiedNetCard = screen.getByText('Verified Net').closest('div.rounded-ds-md');
    expect(verifiedNetCard).not.toBeNull();
    expect(within(verifiedNetCard as HTMLElement).getByText('—')).toBeInTheDocument();

    const openRow = screen.getByText('NVDA260619C00150000').closest('tr');
    expect(openRow).not.toBeNull();
    expect(within(openRow as HTMLElement).getByText('证据窗口末未归零')).toBeInTheDocument();
    expect(within(openRow as HTMLElement).getByText('2')).toBeInTheDocument();
    expect(within(openRow as HTMLElement).getByText('—')).toBeInTheDocument();
  });

  it('keeps the data plumbing off the review workspace', () => {
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController()}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    // Canonical build preview, activation management, and the snapshot card
    // now live on the 数据与构建 tab, not in the review workspace.
    expect(screen.queryByLabelText('可信事实集构建预览')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '生成仓位复盘构建' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '设为默认复盘构建' })).not.toBeInTheDocument();
  });

  it('shows review-status badges and applies the persisted review-status filter', () => {
    const onApplyFilters = vi.fn();
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController()}
        onApplyFilters={onApplyFilters}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
      />,
    );

    const openRow = screen.getByText('NVDA260619C00150000').closest('tr');
    const closedRow = screen.getByText('TSLA260619P00200000').closest('tr');
    expect(within(openRow as HTMLElement).getByText('进行中 · #2')).toBeInTheDocument();
    expect(within(closedRow as HTMLElement).getByText('已完成 · #3')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('筛选复盘状态'), {
      target: { value: 'completed' },
    });
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
    expect(onApplyFilters).toHaveBeenCalledWith(expect.objectContaining({
      reviewStatus: 'completed',
      page: 1,
    }));
  });

  it('loads structured evidence on row selection and closes the drawer with Escape', () => {
    const loadDetail = vi.fn().mockResolvedValue(undefined);
    const onOpenReview = vi.fn();
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController({ detailById: { 41: episodeDetail }, loadDetail })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
        onOpenReview={onOpenReview}
      />,
    );

    fireEvent.click(screen.getByText('NVDA260619C00150000'));
    expect(loadDetail).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Evidence chain')).toBeInTheDocument();
    expect(screen.getByText('订单时间代理 · 非逐笔成交时间')).toBeInTheDocument();
    expect(screen.getByText('order observation 101')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开完整复盘 · K 线与成交证据' }));
    expect(onOpenReview).toHaveBeenCalledWith(openAggregateEpisode);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('points to the data tab when episodes are not built yet', () => {
    const onOpenBuildTools = vi.fn();
    render(
      <PositionEpisodesPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController({
          list: { dataState: 'not_built', total: 0, page: 1, perPage: 50, items: [] },
        })}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={vi.fn()}
        onOpenBuildTools={onOpenBuildTools}
      />,
    );

    expect(screen.getByText('尚未构建仓位回合')).toBeInTheDocument();
    // The assumed-flat build flow itself lives on the 数据与构建 tab now.
    expect(screen.queryByRole('button', { name: '按未验证期初空仓构建' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '前往数据与构建' }));
    expect(onOpenBuildTools).toHaveBeenCalledTimes(1);
  });

  it('keeps an explicit comparison view with a return-to-default action', () => {
    const onSelectBuild = vi.fn();
    render(
      <PositionEpisodesPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={makeController()}
        onApplyFilters={vi.fn()}
        onPageChange={vi.fn()}
        onSelectBuild={onSelectBuild}
      />,
    );

    expect(screen.getByText('正在查看构建 #9')).toBeInTheDocument();
    expect(screen.getByText(/请到「数据与构建」的构建管理区操作/)).toBeInTheDocument();
    // Activation stays out of the review workspace even for a viewed build.
    expect(screen.queryByRole('button', { name: '设为默认复盘构建' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '回到默认复盘构建' }));
    expect(onSelectBuild).toHaveBeenCalledWith();
  });
});
