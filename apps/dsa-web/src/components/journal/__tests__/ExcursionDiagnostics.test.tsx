import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ExcursionDiagnostics } from '../review/ExcursionDiagnostics';
import { EXCURSION_DISCLAIMER } from '../review/reviewHardLines';

const apiMocks = vi.hoisted(() => ({
  fetchEpisodeExcursion: vi.fn(),
  fetchExcursionScatter: vi.fn(),
}));

vi.mock('../../../api/journalReviewFlow', () => ({
  fetchEpisodeExcursion: apiMocks.fetchEpisodeExcursion,
  fetchExcursionScatter: apiMocks.fetchExcursionScatter,
}));

const readyExcursion = {
  dataState: 'ready' as const,
  episodeId: 41,
  buildId: 9,
  excursion: {
    excursionId: 1,
    episodeBuildId: 9,
    positionEpisodeId: 41,
    codeVersion: 'underlying-5m/1.0',
    source: 'underlying_5m',
    status: 'ready' as const,
    statusReason: null,
    exposure: 1,
    u0: 100,
    u0At: '2026-07-20T13:35:00Z',
    u0Flag: null,
    mfeUnderlyingPct: 0.04,
    mfeAt: '2026-07-20T14:00:00Z',
    maeUnderlyingPct: -0.03,
    maeAt: '2026-07-20T13:45:00Z',
    maeBeforeMfe: true,
    atr14: 4,
    mfeAtr: 1,
    maeAtr: -0.75,
    barsUsed: 6,
    coverageStart: '2026-07-20T13:35:00Z',
    coverageEnd: '2026-07-20T14:05:00Z',
    missingSessions: [],
    computedAt: '2026-08-25T00:00:00Z',
  },
  missingReason: null,
  verdict: null,
  disclaimer: EXCURSION_DISCLAIMER,
  limitations: [],
};

const scatter = {
  dataState: 'ready' as const,
  accountKey: 'default_moomoo_us',
  buildId: 9,
  codeVersion: 'underlying-5m/1.0',
  items: [
    {
      episodeId: 41,
      rawSymbol: 'NVDA260731C00150000',
      holdStructure: 'intraday' as const,
      status: 'ready',
      maeUnderlyingPct: -0.03,
      mfeUnderlyingPct: 0.04,
      maeAtr: -0.75,
      mfeAtr: 1,
      rMultiple: 0.2,
      rMissingReason: null,
    },
    {
      episodeId: 55,
      rawSymbol: 'AMD260807C00160000',
      holdStructure: 'overnight' as const,
      status: 'ready',
      maeUnderlyingPct: -0.01,
      mfeUnderlyingPct: 0.06,
      maeAtr: null,
      mfeAtr: null,
      rMultiple: 1.4,
      rMissingReason: null,
    },
    {
      episodeId: 60,
      rawSymbol: 'MISSING',
      holdStructure: 'unknown' as const,
      status: 'missing_bars',
      maeUnderlyingPct: null,
      mfeUnderlyingPct: null,
      maeAtr: null,
      mfeAtr: null,
      rMultiple: null,
      rMissingReason: '净盈亏未知',
    },
  ],
  disclaimer: EXCURSION_DISCLAIMER,
  limitations: [],
};

describe('ExcursionDiagnostics', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchEpisodeExcursion.mockResolvedValue(readyExcursion);
    apiMocks.fetchExcursionScatter.mockResolvedValue(scatter);
  });

  it('locks the permanent disclaimer verbatim and renders readings + scatter', async () => {
    render(<ExcursionDiagnostics episodeId={41} buildId={9} />);

    // 免责逐字锁定（不可关闭）。
    expect(await screen.findByTestId('excursion-disclaimer')).toHaveTextContent(
      '本图不用于设置止损；不得由此推导出场/持有参数。'
      + '对尾部结构账户，按 MAE 分位收紧止损最大概率截掉的恰是右尾。'
      + '持仓分组（日内/隔夜）存在内生性。',
    );
    expect(await screen.findByText('-3.00%')).toBeInTheDocument();
    expect(screen.getByText('4.00%')).toBeInTheDocument();
    expect(screen.getByText('完整覆盖')).toBeInTheDocument();
    // 散点：只画 MAE 与 R 同时可得的点（标缺点绝不画成 0）。
    const plot = await screen.findByRole('img', { name: 'MAE 与最终 R 的只读诊断散点' });
    expect(plot.querySelectorAll('circle, rect').length).toBe(2);
  });

  it('shows the missing reason instead of zeros when no record exists', async () => {
    apiMocks.fetchEpisodeExcursion.mockResolvedValue({
      ...readyExcursion,
      dataState: 'missing',
      excursion: null,
      missingReason: '超出 5m 数据保留窗口（约 60 天），永久标缺',
    });
    apiMocks.fetchExcursionScatter.mockResolvedValue({ ...scatter, items: [] });
    render(<ExcursionDiagnostics episodeId={9} buildId={9} />);

    expect(await screen.findByText(/超出 5m 数据保留窗口/)).toBeInTheDocument();
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument();
    expect(await screen.findByText(/暂无可绘制的散点/)).toBeInTheDocument();
  });

  it('declares exit-bar spillover and distinguishes entry-day data gaps', async () => {
    apiMocks.fetchEpisodeExcursion.mockResolvedValue({
      ...readyExcursion,
      excursion: {
        ...readyExcursion.excursion,
        u0Flag: 'entry_day_bars_missing_next_session_bar',
      },
    });
    render(<ExcursionDiagnostics episodeId={41} buildId={9} />);
    await screen.findByTestId('excursion-disclaimer');
    // 复核修复 7：数据缺口不谎称盘外开仓。
    expect(
      screen.getByText(/入场日无 5m bar（数据缺口，非盘外开仓）/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/盘外开仓：U0/)).not.toBeInTheDocument();
    // 复核修复 8：出场 bar 溢出如实声明。
    expect(screen.getByText(/出场 bar/)).toBeInTheDocument();
    expect(screen.getByText(/至多约 5 分钟的行情也被计入/)).toBeInTheDocument();
  });

  it('never renders banned metric terms', async () => {
    const { container } = render(<ExcursionDiagnostics episodeId={41} buildId={9} />);
    await screen.findByTestId('excursion-disclaimer');
    const text = container.textContent ?? '';
    for (const banned of ['SQN', 'Zella', '综合评分', 'MAE 止损', '建议止损位', '最优持有时间']) {
      expect(text).not.toContain(banned);
    }
  });
});
