import type React from 'react';
import { useState } from 'react';
import { Button, toast } from '../ui';
import { useJournalFrameworkStore } from '../../stores/journalFrameworkStore';

const DEFAULT_PLACEHOLDER = `# 我的交易框架（示例，请根据你的实际体系改写）

1. Regime Gate: 只在 Regime Score ≥ 55 的日子开新仓；< 55 一律空仓观察
2. 时段: 打单仅限 09:45-10:30 和 15:30-15:55 两个时段
3. 日内期权: 每天 0DTE ≤ 2 笔；连亏 2 笔立即停盘
4. 风险: 单笔最大亏损 = 当日净值的 1%；超出直接平仓
5. 持仓时长: 0DTE ≤ 30 分钟；1-3DTE ≤ 1 日
6. 禁忌: 不做 GOOG/META 之外的 low-float、不做 earnings 前一天
`;

export const FrameworkPanel: React.FC = () => {
  const framework = useJournalFrameworkStore((s) => s.framework);
  const setFramework = useJournalFrameworkStore((s) => s.setFramework);
  const [draft, setDraft] = useState(framework);

  const dirty = draft !== framework;
  const len = draft.length;

  const save = () => {
    const err = setFramework(draft);
    toast[err ? 'warning' : 'success'](err ?? '框架已保存（本地 localStorage，刷新不丢）');
  };

  const reset = () => {
    setDraft(framework);
  };

  const clear = () => {
    setDraft('');
  };

  const useExample = () => {
    setDraft(DEFAULT_PLACEHOLDER);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <div>
        <div className="text-label uppercase text-text-3">Trading framework / 交易框架</div>
        <p className="mt-2 text-body-sm text-text-2">
          你输入的这段文本会作为 AI 分析你交割单的「大前提」。建议用 Markdown 列出可执行的规则（时段、仓位、止损、禁忌）。
          越具体，AI 越能点到哪笔偏离了哪条。存在 localStorage 里，刷新不会丢。
        </p>
      </div>

      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={DEFAULT_PLACEHOLDER}
        spellCheck={false}
        className="h-[360px] w-full rounded-ds-md border border-subtle bg-bg-1 p-3 font-mono text-body-sm text-text-1 placeholder:text-text-3 focus:border-default focus:outline-none"
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" onClick={save} disabled={!dirty}>
          Save
        </Button>
        <Button variant="ghost" size="sm" onClick={reset} disabled={!dirty}>
          Reset
        </Button>
        <Button variant="ghost" size="sm" onClick={useExample}>
          Use example
        </Button>
        <Button variant="ghost" size="sm" onClick={clear} disabled={!draft}>
          Clear
        </Button>
        <div className="ml-auto font-mono text-mono-xs text-text-3">{len} / 10000</div>
      </div>
    </div>
  );
};

export default FrameworkPanel;
