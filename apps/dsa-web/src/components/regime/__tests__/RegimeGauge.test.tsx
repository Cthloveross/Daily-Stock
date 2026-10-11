import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RegimeGauge } from '../RegimeGauge';
import { RegimeScore } from '../RegimeScore';

describe('RegimeGauge quality states', () => {
  it('does not present the score sentinel as an authoritative no-trade reading', () => {
    render(
      <RegimeGauge
        score={0}
        state="no_trade"
        qualityState="unavailable"
        incompleteDomains={['SPY 趋势', 'VIX']}
      />,
    );

    expect(
      screen.getByLabelText('Regime unavailable because core market inputs are incomplete'),
    ).toBeInTheDocument();
    expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument();
    expect(screen.getByText(/不作为 Regime 分数或交易门禁/)).toBeInTheDocument();
    expect(screen.getByText('INSUFFICIENT CORE DATA')).toBeInTheDocument();
    expect(screen.queryByText('REGIME SCORE')).not.toBeInTheDocument();
  });

  it('marks a supporting-domain gap as provisional', () => {
    render(
      <RegimeGauge
        score={60}
        state="standard"
        qualityState="degraded"
        incompleteDomains={['盘前行情']}
      />,
    );

    expect(screen.getByText('+60')).toBeInTheDocument();
    expect(screen.getByText('CONTEXT ONLY · PROVISIONAL')).toBeInTheDocument();
    expect(screen.queryByText('STANDARD · PROVISIONAL')).not.toBeInTheDocument();
    expect(screen.getByText(/仅作临时背景/)).toBeInTheDocument();
    expect(screen.getByText('默认阈值 · 35 偏险 · 55 混合 · 75 有利')).toBeInTheDocument();
  });

  it('shows adverse risk context for the compatible no_trade enum', () => {
    render(<RegimeGauge score={20} state="no_trade" qualityState="ready" />);

    expect(screen.getAllByText('ADVERSE')).toHaveLength(2);
    expect(screen.queryByText('NO TRADE')).not.toBeInTheDocument();
    expect(screen.getByText(/仅作市场风险背景，不决定 DTE/)).toBeInTheDocument();
  });

  it('keeps the compact score descriptive without changing its state contract', () => {
    render(<RegimeScore score={20} state="no_trade" />);

    expect(screen.getByText('ADVERSE')).toBeInTheDocument();
    expect(screen.getByText('+20')).toBeInTheDocument();
    expect(screen.queryByText('NO TRADE')).not.toBeInTheDocument();
    expect(screen.getByText(/仅作市场风险背景，不决定 DTE/)).toBeInTheDocument();
  });
});
