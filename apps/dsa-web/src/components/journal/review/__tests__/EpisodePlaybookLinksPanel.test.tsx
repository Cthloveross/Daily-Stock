import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import EpisodePlaybookLinksPanel from '../EpisodePlaybookLinksPanel';
import { fetchEpisodePlaybookLinks } from '../../../../api/journal';
import type { EpisodePlaybookLink, EpisodePlaybookLinksResponse } from '../../../../types/journal';

vi.mock('../../../../api/journal', () => ({
  fetchEpisodePlaybookLinks: vi.fn(),
}));

const fetchLinksMock = vi.mocked(fetchEpisodePlaybookLinks);

const link = (overrides: Partial<EpisodePlaybookLink>): EpisodePlaybookLink => ({
  schemaVersion: 'playbook-episode-link/1.0',
  kind: 'candidate',
  linkState: 'confirmed',
  title: '动量候选',
  ruleText: '只做计划内触发。',
  bucket: {
    groupKind: 'tag',
    groupValue: 'momentum',
    direction: 'LONG',
    boundaryPolicy: 'assumed_or_censored',
  },
  snapshotBuildId: 7,
  snapshotGeneratedAt: '2026-08-01T12:00:00Z',
  lineageKey: null,
  version: null,
  status: null,
  candidateKey: 'c'.repeat(64),
  promoted: false,
  createdAt: '2026-08-01T12:00:00Z',
  ...overrides,
});

const response = (links: EpisodePlaybookLink[]): EpisodePlaybookLinksResponse => ({
  schemaVersion: 'journal-playbook-episode-links/1.0',
  accountKey: 'default_moomoo_us',
  buildId: 7,
  episodeId: 42,
  links,
});

describe('EpisodePlaybookLinksPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders confirmed rule and candidate links with kind labels and the frozen bucket echo', async () => {
    fetchLinksMock.mockResolvedValue(response([
      link({
        kind: 'rule',
        title: '动量规则',
        lineageKey: 'a'.repeat(64),
        version: 2,
        status: 'active',
        candidateKey: null,
        promoted: null,
      }),
      link({ promoted: true }),
    ]));
    render(<EpisodePlaybookLinksPanel episodeId={42} buildId={7} />);

    expect(await screen.findByText('规则 v2 · 生效中')).toBeInTheDocument();
    expect(screen.getByText('动量规则')).toBeInTheDocument();
    expect(screen.getByText('候选 · 已晋升')).toBeInTheDocument();
    expect(screen.getByText('动量候选')).toBeInTheDocument();
    expect(
      screen.getAllByText('标签 · momentum · 做多 · 边界假设/截断').length,
    ).toBe(2);
    expect(fetchLinksMock).toHaveBeenCalledWith(42, 7);
    expect(
      screen.queryByText(/证据快照抽样截断/),
    ).not.toBeInTheDocument();
  });

  it('labels a retired rule version honestly', async () => {
    fetchLinksMock.mockResolvedValue(response([
      link({
        kind: 'rule',
        lineageKey: 'a'.repeat(64),
        version: 3,
        status: 'retired',
        candidateKey: null,
        promoted: null,
      }),
    ]));
    render(<EpisodePlaybookLinksPanel episodeId={42} buildId={7} />);

    expect(await screen.findByText('规则 v3 · 已退役')).toBeInTheDocument();
  });

  it('separates possible-truncated entries and shows the uncertainty notice', async () => {
    fetchLinksMock.mockResolvedValue(response([
      link({}),
      link({
        title: '截断候选',
        linkState: 'possible_truncated',
        candidateKey: 'd'.repeat(64),
      }),
    ]));
    render(<EpisodePlaybookLinksPanel episodeId={42} buildId={7} />);

    expect(await screen.findByText('截断候选')).toBeInTheDocument();
    expect(screen.getByText('可能相关（无法确认）')).toBeInTheDocument();
    expect(
      screen.getByText('证据快照抽样截断，无法确认该回合是否在样本内'),
    ).toBeInTheDocument();
    const uncertainList = screen.getByRole('list', { name: '无法确认的 Playbook 关联' });
    expect(uncertainList).toHaveTextContent('截断候选');
    expect(uncertainList).not.toHaveTextContent('动量候选');
  });

  it('shows the empty state when nothing references the episode', async () => {
    fetchLinksMock.mockResolvedValue(response([]));
    render(<EpisodePlaybookLinksPanel episodeId={42} buildId={7} />);

    expect(
      await screen.findByText('该回合未被任何 Playbook 候选或规则引用。'),
    ).toBeInTheDocument();
  });

  it('surfaces the request error without an empty-state claim', async () => {
    fetchLinksMock.mockRejectedValue(new Error('network down'));
    render(<EpisodePlaybookLinksPanel episodeId={42} buildId={7} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/未被任何 Playbook 候选或规则引用/),
    ).not.toBeInTheDocument();
  });
});
