/**
 * 深度复盘的硬红线常量（蓝图 17 §三(b)/(c)，与后端
 * `src/journal/review_flow.py` / `src/journal/excursions.py` 同源锁定）。
 *
 * - `EXCURSION_DISCLAIMER`：MAE/MFE 图面永久免责，不可关闭，测试锁定逐字。
 * - `BANNED_METRIC_TERMS`：禁用指标清单——这些词**永不**出现在复盘界面
 *   （SQN/Zella 类合成分、hold-time 分桶 edge 主张、MAE 推导止损）。
 *   新页面测试对渲染输出做负断言。
 * - `MISTAKE_VOCABULARY`：损耗归因轨固定词表（写入 error_types labels）。
 */

/** 与后端 EXCURSION_DISCLAIMER 逐字一致；测试锁定，勿改写。 */
export const EXCURSION_DISCLAIMER = '本图不用于设置止损；不得由此推导出场/持有参数。'
  + '对尾部结构账户，按 MAE 分位收紧止损最大概率截掉的恰是右尾。'
  + '持仓分组（日内/隔夜）存在内生性。';

/**
 * 渲染输出中被禁止出现的指标词（大小写不敏感做匹配）。
 * 依据：F3（尾部结构，合成分是负激励）、F6（hold-time 循环论证）、
 * 蓝图 §一.1.3-3（MAE 止损截右尾）。
 */
export const BANNED_METRIC_TERMS: readonly string[] = [
  'SQN',
  'Zella',
  '系统质量数',
  '综合评分',
  '持有时长最优',
  '最优持有时间',
  'MAE 止损',
  'MAE止损',
  '建议止损位',
];

/** 损耗归因轨 mistake 词表（蓝图初版；与后端 MISTAKE_VOCABULARY 同源）。 */
export const MISTAKE_VOCABULARY: readonly string[] = [
  'freelance_no_push',
  'chase_after_expiry',
  'late_0dte',
  'early_exit_fear',
  'size_overrun',
  'revenge_add',
  'plan_absent',
];

/** mistake 词表的中文注释（仅展示辅助，写入 labels 的仍是英文词条）。 */
export const MISTAKE_LABELS: Record<string, string> = {
  freelance_no_push: '无推送自由单',
  chase_after_expiry: '追到期追涨',
  late_0dte: '午后 0DTE',
  early_exit_fear: '恐惧提前离场',
  size_overrun: '仓位超限',
  revenge_add: '报复性加仓',
  plan_absent: '无计划入场',
};

/** 四象限展示：侥幸标红（违规∧盈利），其余按语义着色。 */
export const QUADRANT_TONES: Record<string, string> = {
  deserved_win: 'border-up-strong/30 bg-up-subtle text-up-strong',
  bad_luck: 'border-subtle bg-bg-2 text-text-2',
  undeserved_win: 'border-down-strong/40 bg-down-subtle text-down-strong',
  deserved_loss: 'border-warn-strong/30 bg-warn-subtle text-warn-strong',
  not_judged: 'border-subtle bg-bg-2 text-text-3',
};
