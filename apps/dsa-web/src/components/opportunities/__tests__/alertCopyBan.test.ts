import { describe, expect, it } from 'vitest';

/**
 * B3-③ 措辞禁令（蓝图 17 §1.3-6 / 改进 #5）：提示与面板的系统文案禁用
 * 「聪明钱 / 主力 / 大资金」式资金身份归因——期权异动的来源可能是对冲或
 * 库存流（Muravyev 2016），身份归因叙事没有文献支撑。负向断言锁定源码，
 * 防止措辞回潜。（用户自建 Playbook 标题等用户数据不在此列。）
 *
 * 通过 Vite 的 `?raw` 静态导入读取源码文本（浏览器 tsconfig 下无 node:fs）。
 */

const GUARDED_SOURCES: Record<string, string> = import.meta.glob(
  [
    '../IntradayScanTable.tsx',
    '../IntradayAlertFeedCard.tsx',
    '../IntradayPlanPanel.tsx',
    '../intradayPlanChecks.ts',
    '../laneChecklist.ts',
    '../PremarketOptionAnomalyPanel.tsx',
    '../IntradayOptionEventFeed.tsx',
  ],
  { query: '?raw', import: 'default', eager: true },
) as Record<string, string>;

const BANNED = ['聪明钱', '主力', '大资金'];

describe('alert/panel copy ban (聪明钱/主力/大资金)', () => {
  it('guards the intended source files', () => {
    expect(Object.keys(GUARDED_SOURCES)).toHaveLength(7);
  });

  it.each(Object.keys(GUARDED_SOURCES))(
    '%s contains no smart-money attribution wording',
    (file) => {
      const source = GUARDED_SOURCES[file];
      for (const word of BANNED) {
        for (const line of source.split('\n')) {
          if (line.includes(word) && !line.includes('禁用') && !line.includes('禁令')) {
            throw new Error(`${file} 含违禁措辞 ${word}: ${line.trim()}`);
          }
        }
      }
      expect(source.length).toBeGreaterThan(0);
    },
  );
});
