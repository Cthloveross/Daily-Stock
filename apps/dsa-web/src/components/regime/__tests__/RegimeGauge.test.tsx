import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RegimeGauge } from '../RegimeGauge';

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
    expect(screen.getByText('默认阈值 · 35 谨慎 · 55 标准 · 75 激进')).toBeInTheDocument();
  });
});
