import type { EdgePanelBlockedReason } from '../../types/journal';

/**
 * Edge 面板（doc 16 E-5）的共享文案：EdgePanelCard 与其测试共用同一份，
 * 单独成文件以满足 react-refresh 只导出组件的约束（同 episodeFormat.ts）。
 */

/** blocked_by 枚举 → 中文说明（契约固定）。 */
export const BLOCKED_REASON_TEXT: Record<EdgePanelBlockedReason, string> = {
  market_momentum: 'QQQ 20日动量 ≤ 0',
  sector_momentum: 'SOXX 20日动量 ≤ 0',
  volatility_scale: '波动率过高 (MM<0.5)',
  market_data_unavailable: '行情数据不可用（fail closed）',
};

export const GATE_OPEN_TEXT = '0-1DTE 允许';
export const GATE_CLOSED_TEXT = '仅 2-7DTE';
export const FAIL_CLOSED_BANNER =
  '行情数据不可用——按 fail closed 降到默认档 2-7DTE，特征不编造、读数不留白。';
