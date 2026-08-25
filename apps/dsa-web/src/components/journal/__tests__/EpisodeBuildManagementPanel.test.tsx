import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import EpisodeBuildManagementPanel from '../EpisodeBuildManagementPanel';
import {
  activateEpisodeBuild,
  fetchEpisodeBuildActivation,
} from '../../../api/journal';
import {
  activationResponse,
  activationState,
  canonicalBuildResult,
  canonicalPreview,
  makeController,
  readyList,
} from './positionEpisodeFixtures';
import type {
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildResponse,
  PositionEpisodeListResponse,
} from '../../../types/journal';

vi.mock('../../../api/journal', () => ({
  activateEpisodeBuild: vi.fn(),
  fetchEpisodeBuildActivation: vi.fn(),
}));

const fetchActivationMock = vi.mocked(fetchEpisodeBuildActivation);
const activateBuildMock = vi.mocked(activateEpisodeBuild);

describe('EpisodeBuildManagementPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchActivationMock.mockResolvedValue(activationState);
    activateBuildMock.mockResolvedValue(activationResponse);
  });

  it('requires a second explicit confirmation before creating an assumed-flat build', () => {
    const buildAssumedFlat = vi.fn().mockResolvedValue(undefined);
    render(
      <EpisodeBuildManagementPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController({
          list: { dataState: 'not_built', total: 0, page: 1, perPage: 50, items: [] },
          buildAssumedFlat,
        })}
        onSelectBuild={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '按未验证期初空仓构建' }));
    expect(buildAssumedFlat).not.toHaveBeenCalled();
    expect(screen.getByText(/把证据窗口开始时的持仓假设为 0/)).toBeInTheDocument();
    expect(screen.getByText(/当前时点的券商持仓快照，也不能倒推这个历史期初/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '接受假设并构建' }));
    expect(buildAssumedFlat).toHaveBeenCalledTimes(1);
  });

  it('requires explicit boundary acceptance before generating from the trusted fact set', () => {
    const buildCanonical = vi.fn().mockResolvedValue(canonicalBuildResult);
    render(
      <EpisodeBuildManagementPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController({ buildCanonical })}
        onSelectBuild={vi.fn()}
      />,
    );

    expect(screen.getByText('5,974 条事件')).toBeInTheDocument();
    expect(screen.getByText('已通过')).toBeInTheDocument();
    expect(screen.getByText('+2 个回合')).toBeInTheDocument();
    expect(screen.getByText(/不会替换默认复盘构建/)).toBeInTheDocument();
    const evidenceWindow = screen.getByRole('region', { name: '构建证据窗口' });
    expect(within(evidenceWindow).getByText('证据窗口（ET）起—止')).toBeInTheDocument();
    expect(within(evidenceWindow).getByText('窗口末投影 as-of（ET）')).toBeInTheDocument();

    const generate = screen.getByRole('button', { name: '生成仓位复盘构建' });
    expect(generate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', {
      name: /当前时点的券商持仓快照，也不能倒推这个历史证据窗口的期初持仓/,
    }));
    expect(generate).toBeEnabled();
    fireEvent.click(generate);
    expect(buildCanonical).toHaveBeenCalledWith(true, false);
  });

  it('keeps combo fees at execution-group scope and requires a separate acknowledgement', () => {
    const buildCanonical = vi.fn().mockResolvedValue(canonicalBuildResult);
    const groupPreview: CanonicalEpisodeBuildPlanResponse = {
      ...canonicalPreview,
      retainedExecutionGroupFeeTotal: '8.0800000000',
      feeConservationByCurrency: {
        USD: {
          ordinarySource: '151750.7500000000',
          ordinaryAllocated: '151750.7500000000',
          retainedExecutionGroup: '8.0800000000',
          sourceKnown: '151758.8300000000',
          accounted: '151758.8300000000',
        },
      },
      executionGroupCount: 1,
      groupFeeAffectedEpisodeCount: 2,
      legFeeAttributionComplete: false,
      requiresGroupFeeScopeAcceptance: true,
      warnings: [
        'execution_group_fee_retained_unallocated',
        'execution-group fee remains exact only at group scope; affected leg episodes have no fee/net P&L and require explicit acceptance',
      ],
    };
    render(
      <EpisodeBuildManagementPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController({ canonicalPreview: groupPreview, buildCanonical })}
        onSelectBuild={vi.fn()}
      />,
    );

    const accounting = screen.getByLabelText('组合执行组费用口径');
    expect(within(accounting).getByText(/1 个组合执行组 · 2 个腿回合受影响/)).toBeInTheDocument();
    expect(within(accounting).getByText(/组费用 USD 8.08/)).toBeInTheDocument();
    expect(screen.getAllByText('组合执行组费用已完整保留在组级；受影响腿不显示费用或净收益，也不进入严格 Headline。')).toHaveLength(1);
    expect(screen.queryByText('execution_group_fee_retained_unallocated')).not.toBeInTheDocument();
    expect(screen.queryByText(/execution-group fee remains exact only/)).not.toBeInTheDocument();
    const generate = screen.getByRole('button', { name: '生成仓位复盘构建' });
    fireEvent.click(screen.getByRole('checkbox', { name: /我接受未验证的期初空仓假设/ }));
    expect(generate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /我理解组合费用只在执行组层精确保留/ }));
    expect(generate).toBeEnabled();
    fireEvent.click(generate);
    expect(buildCanonical).toHaveBeenCalledWith(true, true);
  });

  it('opens a generated build explicitly through the review workspace callback', () => {
    const onSelectBuild = vi.fn();
    render(
      <EpisodeBuildManagementPanel
        filters={{ page: 1, perPage: 50 }}
        controller={makeController({ canonicalBuildResult })}
        onSelectBuild={onSelectBuild}
      />,
    );

    expect(onSelectBuild).not.toHaveBeenCalled();
    expect(activateBuildMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '查看这个构建' }));
    expect(onSelectBuild).toHaveBeenCalledWith(9);
  });

  it('requires a separate assumed-flat acknowledgement before activation', async () => {
    render(
      <EpisodeBuildManagementPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={makeController({ canonicalBuildResult })}
        onSelectBuild={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalledTimes(1));
    const activate = screen.getByRole('button', { name: '设为默认复盘构建' });
    expect(activate).toBeDisabled();
    expect(screen.getByText(/这是独立于“生成构建”的第二次确认/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    expect(activate).toBeEnabled();
  });

  it('requires execution-group fee acknowledgement again before activation', async () => {
    const groupBuildResult: EpisodeBuildResponse = {
      ...canonicalBuildResult,
      build: {
        ...canonicalBuildResult.build,
        executionGroupCount: 1,
        groupFeeAffectedEpisodeCount: 2,
        retainedExecutionGroupFeeTotal: '8.0800000000',
        legFeeAttributionComplete: false,
      },
    };
    render(
      <EpisodeBuildManagementPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={makeController({ canonicalBuildResult: groupBuildResult })}
        onSelectBuild={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalledTimes(1));
    const activate = screen.getByRole('button', { name: '设为默认复盘构建' });
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    expect(activate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次确认组合费用仅保留在执行组层/,
    }));
    expect(activate).toBeEnabled();
    fireEvent.click(activate);

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(9, expect.objectContaining({
      acceptAssumedFlat: true,
      acceptGroupFeeScope: true,
    })));
  });

  it('activates with the GET state as CAS, clears explicit build, and reloads defaults', async () => {
    const onSelectBuild = vi.fn();
    const onImported = vi.fn();
    const controlled = makeController({ canonicalBuildResult });
    render(
      <EpisodeBuildManagementPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controlled}
        onSelectBuild={onSelectBuild}
        onImported={onImported}
      />,
    );

    await screen.findByText('历史 CSV 默认构建');
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    fireEvent.click(screen.getByRole('button', { name: '设为默认复盘构建' }));

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(9, {
      expectedBuildKey: canonicalBuildResult.build.buildKey,
      expectedCurrentActivationId: null,
      expectedCurrentBuildId: 7,
      acceptAssumedFlat: true,
      acceptGroupFeeScope: false,
      acceptLeftCensoredOpenings: false,
    }));
    expect(onSelectBuild).toHaveBeenCalledWith();
    expect(controlled.reload).toHaveBeenCalledTimes(1);
    expect(controlled.reloadCanonicalPreview).toHaveBeenCalledTimes(1);
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('构建 #9 已设为默认复盘构建')).toBeInTheDocument();
    expect(screen.getByText('零交易动作')).toBeInTheDocument();
  });

  it('reloads stale CAS state and never switches the default view after failure', async () => {
    const onSelectBuild = vi.fn();
    const onImported = vi.fn();
    const controlled = makeController({ canonicalBuildResult });
    fetchActivationMock
      .mockResolvedValueOnce(activationState)
      .mockResolvedValueOnce({
        ...activationState,
        selectionSource: 'activation',
        currentActivationId: 13,
        currentActivationSequence: 2,
        currentBuildId: 8,
      });
    activateBuildMock.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'activation state changed; refresh and retry with the current IDs' },
      },
    });

    render(
      <EpisodeBuildManagementPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={controlled}
        onSelectBuild={onSelectBuild}
        onImported={onImported}
      />,
    );

    await screen.findByText('历史 CSV 默认构建');
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    fireEvent.click(screen.getByRole('button', { name: '设为默认复盘构建' }));

    expect(await screen.findByText('默认复盘构建已变化')).toBeInTheDocument();
    expect(screen.getByText(/当前状态已重新读取/)).toBeInTheDocument();
    expect(fetchActivationMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText('构建 #8')).toBeInTheDocument();
    expect(onSelectBuild).not.toHaveBeenCalled();
    expect(controlled.reload).not.toHaveBeenCalled();
    expect(controlled.reloadCanonicalPreview).not.toHaveBeenCalled();
    expect(onImported).not.toHaveBeenCalled();
  });

  it('requires left-censored acknowledgement before activating a build with left-censored episodes', async () => {
    const leftCensoredResult: EpisodeBuildResponse = {
      ...canonicalBuildResult,
      summary: { ...canonicalBuildResult.summary, leftCensoredEpisodeCount: 2 },
    };
    render(
      <EpisodeBuildManagementPanel
        filters={{ buildId: 9, page: 1, perPage: 50 }}
        controller={makeController({ canonicalBuildResult: leftCensoredResult })}
        onSelectBuild={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalledTimes(1));
    const activate = screen.getByRole('button', { name: '设为默认复盘构建' });
    fireEvent.click(screen.getByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    }));
    expect(activate).toBeDisabled();
    expect(screen.getByText(/无法回到「零激活」的 CSV 默认状态/)).toBeInTheDocument();
    expect(screen.getByText(/确认 left-censored 回合口径后才能设为默认/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', {
      name: /我接受 left-censored 回合无券商成本/,
    }));
    expect(activate).toBeEnabled();
    fireEvent.click(activate);

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(9, expect.objectContaining({
      acceptAssumedFlat: true,
      acceptLeftCensoredOpenings: true,
    })));
  });

  it('activates a viewed snapshot-fence build only after the left-censored acknowledgement', async () => {
    const fenceList: PositionEpisodeListResponse = {
      ...readyList,
      build: {
        ...readyList.build!,
        id: 31,
        buildKey: 'e'.repeat(64),
        sourceKind: 'position_snapshot_fenced_canonical',
        canonicalSetId: 5,
        canonicalSetSha256: 'f'.repeat(64),
        openingBoundaryPolicy: 'complete_snapshot',
        assumedFlatUnverified: false,
      },
      summary: { ...readyList.summary!, leftCensoredEpisodeCount: 1 },
    };
    const onSelectBuild = vi.fn();
    const onImported = vi.fn();
    const controlled = makeController({ list: fenceList });
    activateBuildMock.mockResolvedValue({
      ...activationResponse,
      state: {
        ...activationResponse.state,
        currentBuildId: 31,
        currentBuildKey: 'e'.repeat(64),
        canonicalSetId: 5,
      },
    });
    render(
      <EpisodeBuildManagementPanel
        filters={{ buildId: 31, page: 1, perPage: 50 }}
        controller={controlled}
        onSelectBuild={onSelectBuild}
        onImported={onImported}
      />,
    );

    const card = await screen.findByRole('region', {
      name: '将当前查看的构建 #31 设为默认',
    });
    await within(card).findByText(/当前 #7/);
    expect(within(card).getByText(/快照围栏 future build · 目标事实集 #5/)).toBeInTheDocument();
    expect(within(card).getByText(/激活身份绑定其冻结的目标事实集/)).toBeInTheDocument();
    expect(within(card).getByText(/无法回到「零激活」的 CSV 默认状态/)).toBeInTheDocument();
    const activate = within(card).getByRole('button', { name: '设为默认复盘构建' });
    expect(activate).toBeDisabled();
    expect(within(card).queryByRole('checkbox', {
      name: /设为默认时，我再次接受未验证的期初空仓假设/,
    })).not.toBeInTheDocument();

    fireEvent.click(within(card).getByRole('checkbox', {
      name: /left-censored 回合无券商成本/,
    }));
    expect(activate).toBeEnabled();
    fireEvent.click(activate);

    await waitFor(() => expect(activateBuildMock).toHaveBeenCalledWith(31, {
      expectedBuildKey: 'e'.repeat(64),
      expectedCurrentActivationId: null,
      expectedCurrentBuildId: 7,
      acceptAssumedFlat: false,
      acceptGroupFeeScope: false,
      acceptLeftCensoredOpenings: true,
    }));
    expect(onSelectBuild).toHaveBeenCalledWith();
    expect(controlled.reload).toHaveBeenCalledTimes(1);
    expect(controlled.reloadCanonicalPreview).toHaveBeenCalledTimes(1);
    expect(onImported).toHaveBeenCalledTimes(1);
  });

  it('offers no activation affordance for a viewed CSV fallback build', async () => {
    render(
      <EpisodeBuildManagementPanel
        filters={{ buildId: 7, page: 1, perPage: 50 }}
        controller={makeController()}
        onSelectBuild={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchActivationMock).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: '设为默认复盘构建' })).not.toBeInTheDocument();
    expect(activateBuildMock).not.toHaveBeenCalled();
  });
});
