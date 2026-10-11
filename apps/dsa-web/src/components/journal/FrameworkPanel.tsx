import type React from 'react';
import { useState } from 'react';
import { Button, toast } from '../ui';
import { useJournalFrameworkStore } from '../../stores/journalFrameworkStore';

const DEFAULT_PLACEHOLDER = `# 我的交易框架（示例，请根据你的实际体系改写）

1. 策略范围: 只做 INTRA-0-7；0–7DTE 只是合约期限，不代表允许持有多日
2. 持仓边界: 所有期权仓位必须在同一 ET 交易日平仓；绝不留隔夜仓位
3. 入场前检查: 标的、方向、具体合约、Bid/Ask、Delta 与报价时点均可核验；缺失就不做
4. 执行: 只使用限价单，不追市价
5. 风险: 开仓前写明单笔最大亏损与结构失效条件
6. 退出: 命中止损、结构失效或日内退出截止即平仓
`;

export const FrameworkPanel: React.FC = () => {
  const framework = useJournalFrameworkStore((s) => s.framework);
  const setFramework = useJournalFrameworkStore((s) => s.setFramework);
  const legacyArchive = useJournalFrameworkStore((s) => s.legacyArchive);
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

      {legacyArchive && (legacyArchive.framework || legacyArchive.chatHistory.length > 0) && (
        <details className="rounded-ds-sm border border-subtle p-3 text-body-sm text-text-2">
          <summary>旧框架与聊天已保留（只读，不再作为 AI 分析前提）</summary>
          <p className="my-2">请按当前日内规则复核后，再将需要的内容复制到下方保存。</p>
          <pre className="whitespace-pre-wrap break-words">{legacyArchive.framework}</pre>
          {legacyArchive.chatHistory.map((message, index) => (
            <p key={index} className="mt-2 whitespace-pre-wrap break-words">
              {message.role}: {message.content}
            </p>
          ))}
        </details>
      )}

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
