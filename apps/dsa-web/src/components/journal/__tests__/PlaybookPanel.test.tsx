import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PlaybookPanel from '../PlaybookPanel';
import { fetchPlaybook, promotePlaybookCandidate, retirePlaybookRule } from '../../../api/journal';
import type { PlaybookCandidate, PlaybookListResponse, PlaybookRule } from '../../../types/journal';

vi.mock('../../../api/journal', () => ({
  fetchPlaybook: vi.fn(),
  promotePlaybookCandidate: vi.fn(),
  retirePlaybookRule: vi.fn(),
}));

const fetchPlaybookMock = vi.mocked(fetchPlaybook);
const promoteMock = vi.mocked(promotePlaybookCandidate);
const retireMock = vi.mocked(retirePlaybookRule);

const candidate = (overrides: Partial<PlaybookCandidate> = {}): PlaybookCandidate => ({
  schemaVersion: 'playbook-candidate/1.0',
  id: 1,
  candidateKey: 'c'.repeat(64),
  accountKey: 'default_moomoo_us',
  title: '动量候选',
  ruleText: '只做计划内触发。',
  sourceBucket: {
    groupKind: 'tag',
    groupValue: 'momentum',
    direction: 'LONG',
    boundaryPolicy: 'assumed_or_censored',
  },
  evidenceSnapshot: {
    snapshotKind: 'insights_bucket',
    buildId: 7,
    counts: { episodeCount: 2, conditionalEpisodeCount: 2 },
  },
  evidenceSnapshotSha256: 'e'.repeat(64),
  promoted: false,
  createdAt: '2026-08-01T12:00:00Z',
  ...overrides,
});

const rule = (overrides: Partial<PlaybookRule> = {}): PlaybookRule => ({
  schemaVersion: 'playbook-rule/1.0',
  id: 11,
  ruleKey: 'r'.repeat(64),
  lineageKey: 'l'.repeat(64),
  accountKey: 'default_moomoo_us',
  version: 1,
  status: 'active',
  promotedFromCandidateId: 1,
  promotedFromCandidateKey: 'c'.repeat(64),
  previousRuleId: null,
  title: '动量规则',
  ruleText: '只做计划内触发。',
  evidenceSnapshot: {
    snapshotKind: 'insights_bucket',
    buildId: 7,
    counts: { episodeCount: 2, conditionalEpisodeCount: 2 },
  },
  evidenceSnapshotSha256: 'e'.repeat(64),
  isLatestVersion: true,
  createdAt: '2026-08-01T12:00:00Z',
  ...overrides,
});

const response = (overrides: Partial<PlaybookListResponse> = {}): PlaybookListResponse => ({
  schemaVersion: 'journal-playbook/1.0',
  accountKey: 'default_moomoo_us',
  candidates: [],
  rules: [],
  ...overrides,
});

describe('PlaybookPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders empty states with the honest scope copy', async () => {
    fetchPlaybookMock.mockResolvedValue(response());
    render(<PlaybookPanel />);

    expect(
      await screen.findByText(/规则不会影响系统评分或榜单，仅是你的决策清单/),
    ).toBeInTheDocument();
    expect(screen.getByText(/还没有候选/)).toBeInTheDocument();
    expect(screen.getByText(/还没有晋升过的规则/)).toBeInTheDocument();
  });

  it('promotes a candidate only after an explicit confirmation', async () => {
    fetchPlaybookMock.mockResolvedValue(response({ candidates: [candidate()] }));
    promoteMock.mockResolvedValue({
      dataState: 'ready',
      created: true,
      idempotentReplay: false,
      rule: rule(),
    });
    render(<PlaybookPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '晋升为规则' }));
    expect(promoteMock).not.toHaveBeenCalled();
    expect(screen.getByText(/确认晋升？将重新冻结当前证据快照/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认晋升' }));
    await waitFor(() => {
      expect(promoteMock).toHaveBeenCalledWith('c'.repeat(64));
    });
    // The list is reloaded after the explicit promotion.
    expect(fetchPlaybookMock).toHaveBeenCalledTimes(2);
  });

  it('marks promoted candidates instead of offering another promotion', async () => {
    fetchPlaybookMock.mockResolvedValue(
      response({ candidates: [candidate({ promoted: true })], rules: [rule()] }),
    );
    render(<PlaybookPanel />);

    expect(await screen.findByText('已晋升')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '晋升为规则' })).not.toBeInTheDocument();
    expect(screen.getByText('生效中')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
    // Candidate row and rule row each surface their frozen snapshot summary.
    expect(screen.getAllByText(/证据快照 · Build #7 · 2 笔（条件性 2 笔）/)).toHaveLength(2);
  });

  it('retires the latest active rule with CAS on its version after confirm', async () => {
    fetchPlaybookMock.mockResolvedValue(response({ rules: [rule({ version: 3 })] }));
    retireMock.mockResolvedValue({
      dataState: 'ready',
      retired: true,
      idempotentReplay: false,
      rule: rule({ version: 4, status: 'retired' }),
    });
    render(<PlaybookPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '退役' }));
    expect(retireMock).not.toHaveBeenCalled();
    expect(screen.getByText(/确认退役？将追加一条退役版本记录，不删除历史/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认退役' }));
    await waitFor(() => {
      expect(retireMock).toHaveBeenCalledWith('l'.repeat(64), 3);
    });
    expect(fetchPlaybookMock).toHaveBeenCalledTimes(2);
  });

  it('never offers retirement on retired or superseded versions', async () => {
    fetchPlaybookMock.mockResolvedValue(
      response({
        rules: [
          rule({ id: 12, ruleKey: 's'.repeat(64), version: 2, status: 'retired' }),
          rule({ version: 1, isLatestVersion: false }),
        ],
      }),
    );
    render(<PlaybookPanel />);

    expect(await screen.findByText('已退役')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '退役' })).not.toBeInTheDocument();
  });

  it('surfaces a promotion conflict as an alert', async () => {
    fetchPlaybookMock.mockResolvedValue(response({ candidates: [candidate()] }));
    promoteMock.mockRejectedValue(new Error('conflict'));
    render(<PlaybookPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '晋升为规则' }));
    fireEvent.click(screen.getByRole('button', { name: '确认晋升' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });
});
