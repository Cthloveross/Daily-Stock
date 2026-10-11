import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { OptionWallRatioPanel } from '../OptionWallRatioPanel';
import type { OpportunityOptionWallItem } from '../../../types/opportunities';

const CAVEAT =
  'call/put 比例与墙位是当前持仓与成交的事实描述；本仓库尚无历史 OI 序列，' +
  '因此「高 call 比例＝会涨」「价格会向最大痛点靠拢」这两个说法在你的数据上都' +
  '**尚未被检验**。系统正在逐日记录，样本足够后会给出回溯频率。';

function item(overrides: Partial<OpportunityOptionWallItem> = {}): OpportunityOptionWallItem {
  return {
    ticker: 'NVDA',
    state: 'ready',
    source: 'moomoo_openapi',
    fetchedAt: '2026-07-22T14:00:00Z',
    quoteAsOf: '2026-07-22 10:00:01',
    formulaVersion: 'gross-gamma-concentration-1pct/v1',
    spot: 100,
    atmCallIv: {
      state: 'ready',
      expiry: '2026-07-24',
      strike: 105,
      atmCallIvPercent: 42.5,
      selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot',
    },
    scope: { dteMin: 0, dteMax: 45, expiries: ['2026-07-24'], standardContractsOnly: true },
    coverage: {
      requestedContracts: 2,
      snapshotReceivedContracts: 2,
      validContracts: 2,
      coveragePercent: 100,
      failedBatches: 0,
      excludedNonstandardContracts: 0,
      excludedUnknownStandardTypeContracts: 0,
      gammaContracts: 2,
    },
    walls: {
      callOi: [],
      putOi: [],
      callVolume: [],
      putVolume: [],
      callGammaConcentration: [],
      putGammaConcentration: [],
      grossGammaConcentration: [],
    },
    totals: { callOi: 1000, putOi: 500, callVolume: 250, putVolume: 300 },
    ratios: {
      callPutOiRatio: {
        value: 2,
        numeratorTotal: 1000,
        denominatorTotal: 500,
        numeratorSide: 'call',
        denominatorSide: 'put',
        metricBasis: 'settled_open_interest_prior_session',
        reason: null,
      },
      callPutVolumeRatio: {
        value: 0.83,
        numeratorTotal: 250,
        denominatorTotal: 300,
        numeratorSide: 'call',
        denominatorSide: 'put',
        metricBasis: 'current_session_cumulative_volume',
        reason: null,
      },
      caveat: CAVEAT,
    },
    oiWeightedCenter: {
      strike: 99,
      totalOpenInterest: 1500,
      label: '未平仓分布的加权中心（描述，未验证是否有引力）',
      method: 'open_interest_weighted_mean_strike',
      metricBasis: 'settled_open_interest_prior_session',
      validatedAsPriceMagnet: false,
      reason: null,
    },
    message: '',
    assumptions: [],
    limitations: [],
    ...overrides,
  } as OpportunityOptionWallItem;
}

describe('OptionWallRatioPanel', () => {
  it('renders both aggregate ratios with their metric basis', () => {
    render(<OptionWallRatioPanel item={item()} />);

    const panel = screen.getByLabelText('call/put 比例与未平仓加权中心');
    expect(within(panel).getByText('Call / Put OI')).toBeInTheDocument();
    expect(panel).toHaveTextContent('2.00');
    expect(panel).toHaveTextContent('0.83');
    // OI 与 Volume 的口径必须分别标明，不可混读。
    expect(panel).toHaveTextContent('OI＝上一交易日结算后的未平仓量');
    expect(panel).toHaveTextContent('Volume＝当日累计成交量');
  });

  it('renders the untested-hypothesis caveat verbatim', () => {
    render(<OptionWallRatioPanel item={item()} />);

    const panel = screen.getByLabelText('call/put 比例与未平仓加权中心');
    expect(panel).toHaveTextContent('尚未被检验');
    expect(panel).toHaveTextContent('高 call 比例＝会涨');
    expect(panel).toHaveTextContent('价格会向最大痛点靠拢');
    expect(panel).toHaveTextContent('系统正在逐日记录');
  });

  it('never labels the weighted centre as max pain or a target', () => {
    render(<OptionWallRatioPanel item={item()} />);

    const panel = screen.getByLabelText('call/put 比例与未平仓加权中心');
    expect(panel).toHaveTextContent('未平仓分布的加权中心（描述，未验证是否有引力）');
    expect(panel).toHaveTextContent('99');
    expect(panel.textContent).not.toMatch(/最大痛点位|目标位|max ?pain/i);
  });

  it('shows an undefined ratio as null plus its reason, never as 0 or 1', () => {
    const withZeroDenominator = item({
      ratios: {
        callPutOiRatio: {
          value: null,
          numeratorTotal: 1000,
          denominatorTotal: 0,
          numeratorSide: 'call',
          denominatorSide: 'put',
          metricBasis: 'settled_open_interest_prior_session',
          reason: '分母为 0（该窗口内无对应 put 持仓/成交），比例无定义',
        },
        callPutVolumeRatio: {
          value: 0.83,
          numeratorTotal: 250,
          denominatorTotal: 300,
          numeratorSide: 'call',
          denominatorSide: 'put',
          metricBasis: 'current_session_cumulative_volume',
          reason: null,
        },
        caveat: CAVEAT,
      },
    });

    render(<OptionWallRatioPanel item={withZeroDenominator} />);

    const panel = screen.getByLabelText('call/put 比例与未平仓加权中心');
    expect(panel).toHaveTextContent('比例无定义');
    expect(panel).toHaveTextContent('分母为 0');
    expect(panel).toHaveTextContent('—');
  });

  it('renders nothing for a pre-1.3 payload without ratios', () => {
    const { container } = render(
      <OptionWallRatioPanel item={item({ ratios: null })} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
