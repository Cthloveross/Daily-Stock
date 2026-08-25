import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import EdgePanelCard from '../EdgePanelCard';
import {
  BLOCKED_REASON_TEXT,
  FAIL_CLOSED_BANNER,
  GATE_CLOSED_TEXT,
  GATE_OPEN_TEXT,
} from '../edgePanelText';
import { fetchEdgePanel } from '../../../api/journal';
import type { EdgePanelResponse } from '../../../types/journal';

vi.mock('../../../api/journal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/journal')>();
  return { ...actual, fetchEdgePanel: vi.fn() };
});

/** 文档 16 固定契约（snake_case 原样）的一份「绿灯」基准载荷。 */
function payload(over: Partial<EdgePanelResponse> = {}): EdgePanelResponse {
  return {
    as_of: '2026-08-20T09:31:00-04:00',
    gate: {
      allow_0_1dte: true,
      default_bucket: '2-7',
      blocked_by: [],
      features: { mom_q_20d: 0.0512, mom_s_20d: 0.0234, mm_scale: 1.0 },
      data_status: 'ok',
    },
    edge: {
      buckets: {
        '0-1': { n: 467, total: -45138.0, mean: -96.65, win_rate: 0.34 },
        '2-7': { n: 88, total: 195272.0, mean: 2219.0, win_rate: 0.52 },
        '8-30': { n: 12, total: 1200.0, mean: 100.0, win_rate: 0.5 },
        '31+': { n: 0, total: 0.0, mean: null, win_rate: null },
        stock: { n: 3, total: 300.0, mean: 100.0, win_rate: 0.67 },
      },
      expectancy: { n: 570, mean: 138.0, ci95_low: -12.0, ci95_high: 288.0 },
      evidence: {
        h1_t_stat: 1.79,
        hlz_hurdle: 3.0,
        status: 'insufficient',
        n_needed: 247,
        n_remaining: 159,
      },
    },
    discipline: {
      trades_today: 3,
      daily_cap: 8,
      month_fees: 1200.0,
      month_gross: 8000.0,
      fee_ratio: 0.15,
    },
    ...over,
  };
}

describe('EdgePanelCard', () => {
  beforeEach(() => {
    vi.mocked(fetchEdgePanel).mockReset();
  });

  it('renders the green 0-1DTE state with features, buckets and evidence line', async () => {
    vi.mocked(fetchEdgePanel).mockResolvedValue(payload());
    render(<EdgePanelCard />);

    expect(await screen.findByText(GATE_OPEN_TEXT)).toBeInTheDocument();
    const card = screen.getByRole('region', { name: '今日武器档位' });
    // 特征行：动量百分比 + MM 缩放档。
    expect(card).toHaveTextContent('+5.12%');
    expect(card).toHaveTextContent('+2.34%');
    expect(card).toHaveTextContent('×1.00');
    // 分桶表只列 0-1 / 2-7 两个证据桶。
    expect(card).toHaveTextContent('0-1DTE');
    expect(card).toHaveTextContent('2-7DTE');
    expect(card).toHaveTextContent('−$97');
    expect(card).toHaveTextContent('+$2,219');
    expect(card).toHaveTextContent('52%');
    // H1 证据行：t / 门槛 / 缺口笔数 + 期望 CI。
    expect(card).toHaveTextContent('t=1.79');
    expect(card).toHaveTextContent('证据不足');
    expect(card).toHaveTextContent('距统计显著还差 159 笔');
    expect(card).toHaveTextContent('CI95 [−$12, +$288]');
    // 纪律行：额度内不标红。
    expect(card).toHaveTextContent('今日 3/8 笔');
    expect(card).toHaveTextContent('本月费率 15%');
    expect(card).not.toHaveTextContent('超出 R2 频率硬顶');
    expect(card).not.toHaveTextContent('超过 30% 红线');
  });

  it('renders the red 2-7DTE state with Chinese blocked_by reasons', async () => {
    vi.mocked(fetchEdgePanel).mockResolvedValue(
      payload({
        gate: {
          allow_0_1dte: false,
          default_bucket: '2-7',
          blocked_by: ['sector_momentum', 'volatility_scale'],
          features: { mom_q_20d: 0.012, mom_s_20d: -0.1085, mm_scale: 0.25 },
          data_status: 'ok',
        },
      }),
    );
    render(<EdgePanelCard />);

    expect(await screen.findByText(GATE_CLOSED_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(GATE_OPEN_TEXT)).not.toBeInTheDocument();
    const card = screen.getByRole('region', { name: '今日武器档位' });
    expect(card).toHaveTextContent(BLOCKED_REASON_TEXT.sector_momentum);
    expect(card).toHaveTextContent(BLOCKED_REASON_TEXT.volatility_scale);
    expect(card).toHaveTextContent('−10.85%');
    expect(card).toHaveTextContent('×0.25');
  });

  it('fails closed on market-data unavailability: red state, no blank card, no fabricated features', async () => {
    vi.mocked(fetchEdgePanel).mockResolvedValue(
      payload({
        gate: {
          allow_0_1dte: false,
          default_bucket: '2-7',
          blocked_by: ['market_data_unavailable'],
          features: { mom_q_20d: null, mom_s_20d: null, mm_scale: null },
          data_status: 'unavailable',
        },
      }),
    );
    render(<EdgePanelCard />);

    expect(await screen.findByText(GATE_CLOSED_TEXT)).toBeInTheDocument();
    const card = screen.getByRole('region', { name: '今日武器档位' });
    expect(card).toHaveTextContent(FAIL_CLOSED_BANNER);
    expect(card).toHaveTextContent(BLOCKED_REASON_TEXT.market_data_unavailable);
    // 特征缺失显示占位，不编造数字：三个 dd 全部是「—」。
    const features = Array.from(card.querySelectorAll('dd')).map((el) => el.textContent);
    expect(features).toEqual(['—', '—', '—']);
    // Journal 侧读数照常渲染——整卡红色降档但绝不留白。
    expect(card).toHaveTextContent('0-1DTE');
    expect(card).toHaveTextContent('今日 3/8 笔');
  });

  it('turns the R2 cap and R3 fee ratio red when over their lines', async () => {
    vi.mocked(fetchEdgePanel).mockResolvedValue(
      payload({
        discipline: {
          trades_today: 11,
          daily_cap: 8,
          month_fees: 5100.0,
          month_gross: 5000.0,
          fee_ratio: 1.02,
        },
      }),
    );
    render(<EdgePanelCard />);

    const card = await screen.findByRole('region', { name: '今日武器档位' });
    await waitFor(() => expect(card).toHaveTextContent('今日 11/8 笔'));
    expect(card).toHaveTextContent('超出 R2 频率硬顶');
    expect(card).toHaveTextContent('本月费率 102%');
    expect(card).toHaveTextContent('超过 30% 红线 (R3)');
  });

  it('shows the API error state instead of a blank card', async () => {
    vi.mocked(fetchEdgePanel).mockRejectedValue(new Error('boom'));
    render(<EdgePanelCard />);

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });
});
