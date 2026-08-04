/**
 * 合约价格甜蜜区与张数/手续费换算（纯函数层）。
 *
 * 全部数字来自**用户自己的**已平仓回合（build #3 干净口径，n=1,407，
 * 2026-04-21→07-31，`New-docs/phase1/15_TRADING_DISCIPLINE_EVIDENCE.md` §3.1）：
 *
 * | 分档 | n | 费用占权利金 | 净口径 |
 * |---|---|---|---|
 * | <$1 | 72 | 5.56% | −14.25% |
 * | $1-2 | 365 | 2.20% | −1.28% |
 * | $2-4 | 544 | 1.23% | +3.12% |
 * | $4-8 | 208 | 0.61% | +3.44% |
 * | >$8 | 218 | 0.18% | −0.96% |
 *
 * 机制不是玄学：**同样金额买便宜合约＝张数多＝手续费按张收**。每张合约往返
 * 实测 $3.29（区间 $3.29–3.31）。$1-2 档毛口径本来是正的（+0.93%），是费用把
 * 它翻成负的。
 *
 * 边界：样本仅 2026-04→07 一个市场状态（SPY 上行），分档由同一份样本内推出
 * （in-sample），前向验证见 /journal 的规则遵守度。描述统计，不是买卖建议。
 */

export type ContractPriceBand = 'below_1' | 'band_1_2' | 'sweet_2_8' | 'above_8';

export interface ContractBandReading {
  band: ContractPriceBand;
  label: string;
  /** 一行历史读数，必须自带样本量。 */
  evidence: string;
  /** 甜蜜区（$2-8）：面板高亮。 */
  highlighted: boolean;
  /** <$1：面板灰显并警示。 */
  warned: boolean;
}

/** 每张合约往返手续费（用户实测口径 $3.29–3.31，取下界 $3.29）。 */
export const ROUND_TURN_FEE_PER_CONTRACT_USD = 3.29;

/** 标准仓位默认值：只是输入框的初始值，纯 UI 状态——不落盘、不猜账户规模。 */
export const DEFAULT_STANDARD_SIZE_USD = 3000;

export const SWEET_SPOT_MIN_PRICE = 2;
export const SWEET_SPOT_MAX_PRICE = 8;

const BANDS: Record<ContractPriceBand, ContractBandReading> = {
  below_1: {
    band: 'below_1',
    label: '<$1',
    evidence: '你的历史 <$1 档净 −14.25%（n=72），费用吃掉权利金的 5.56%',
    highlighted: false,
    warned: true,
  },
  band_1_2: {
    band: 'band_1_2',
    label: '$1-2',
    evidence: '你的历史 $1-2 档净 −1.28%（n=365，毛 +0.93%，费用 2.20% 把它翻负）',
    highlighted: false,
    warned: false,
  },
  sweet_2_8: {
    band: 'sweet_2_8',
    label: '$2-8 甜蜜区',
    evidence: '你的历史 $2-4 档净 +3.12%（n=544）、$4-8 档净 +3.44%（n=208）',
    highlighted: true,
    warned: false,
  },
  above_8: {
    band: 'above_8',
    label: '>$8',
    evidence: '你的历史 >$8 档净 −0.96%（n=218，费用占比最低 0.18%，但边际为负）',
    highlighted: false,
    warned: false,
  },
};

/** 按合约价分档；价格标缺时返回 null（不猜、不按 0 处理）。 */
export function classifyContractPrice(
  price: number | null | undefined,
): ContractBandReading | null {
  if (price === null || price === undefined || !Number.isFinite(price) || price <= 0) {
    return null;
  }
  if (price < 1) return BANDS.below_1;
  if (price < SWEET_SPOT_MIN_PRICE) return BANDS.band_1_2;
  if (price < SWEET_SPOT_MAX_PRICE) return BANDS.sweet_2_8;
  return BANDS.above_8;
}

export interface ContractSizing {
  /** 可买张数 = floor(标准仓位 ÷ (合约价 × 100))。 */
  contracts: number;
  /** 实际投入权利金 = 张数 × 合约价 × 100。 */
  premiumUsd: number;
  /** 本次手续费估算 = 张数 × $3.29（往返）。 */
  feeUsd: number;
  /** 手续费占本金%（本金＝实际投入权利金），与历史「费用占权利金」同口径。 */
  feePercentOfPremium: number | null;
}

/**
 * 张数与手续费换算：价格或仓位不可用时返回 null（绝不以 0 冒充可买 0 张）。
 * 只做算术，不给任何进出场建议。
 */
export function computeContractSizing(
  price: number | null | undefined,
  standardSizeUsd: number,
): ContractSizing | null {
  if (price === null || price === undefined || !Number.isFinite(price) || price <= 0) {
    return null;
  }
  if (!Number.isFinite(standardSizeUsd) || standardSizeUsd <= 0) return null;
  const perContract = price * 100;
  const contracts = Math.floor(standardSizeUsd / perContract);
  const premiumUsd = contracts * perContract;
  const feeUsd = contracts * ROUND_TURN_FEE_PER_CONTRACT_USD;
  return {
    contracts,
    premiumUsd,
    feeUsd,
    feePercentOfPremium: premiumUsd > 0 ? (feeUsd / premiumUsd) * 100 : null,
  };
}
