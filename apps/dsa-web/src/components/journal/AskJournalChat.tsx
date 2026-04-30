import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Send, Trash2 } from 'lucide-react';
import { Button, toast } from '../ui';
import { InlineAlert } from '../common/InlineAlert';
import { useJournalFrameworkStore } from '../../stores/journalFrameworkStore';
import { askJournalQa } from '../../api/journal';
import { parseApiError } from '../../api/error';
import type { ChatMessage } from '../../types/journal';

const SUGGESTED_QUESTIONS = [
  '我本月最明显的风格偏离是什么？',
  '分析一下最近 3 笔亏损最大的交易，到底是哪条框架规则没执行？',
  '按我的框架，下周应该如何调整仓位？',
  '我的 0DTE 交易是否系统性在违反 risk 规则？',
];

export const AskJournalChat: React.FC = () => {
  const framework = useJournalFrameworkStore((s) => s.framework);
  const chatHistory = useJournalFrameworkStore((s) => s.chatHistory);
  const appendMessage = useJournalFrameworkStore((s) => s.appendMessage);
  const clearChat = useJournalFrameworkStore((s) => s.clearChat);

  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const frameworkPreview = useMemo(() => {
    const lines = framework.split(/\r?\n/).filter((l) => l.trim()).slice(0, 5);
    return lines.join('\n');
  }, [framework]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [chatHistory.length, loading]);

  const send = async (qOverride?: string) => {
    const q = (qOverride ?? question).trim();
    if (!q) return;
    if (!framework.trim()) {
      setErr('请先到 Framework tab 设置交易框架，AI 才能用这段大前提分析你的交割单');
      return;
    }
    setErr(null);
    setLoading(true);
    const userMsg: ChatMessage = { role: 'user', content: q, ts: new Date().toISOString() };
    appendMessage(userMsg);
    setQuestion('');
    try {
      const resp = await askJournalQa({ framework, question: q });
      appendMessage({
        role: 'assistant',
        content: resp.answer,
        ts: resp.generatedAt || new Date().toISOString(),
      });
    } catch (e) {
      const parsed = parseApiError(e);
      appendMessage({
        role: 'assistant',
        content: `⚠️ **调用失败**\n\n${parsed.message}\n\n请稍后重试，或在终端查看 uvicorn 日志。`,
        ts: new Date().toISOString(),
      });
      setErr(parsed.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
      {/* Main chat column */}
      <div className="flex min-h-[600px] flex-col rounded-ds-md border border-subtle bg-bg-1">
        <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
          <div className="text-label uppercase text-text-3">Ask AI · journal coach</div>
          <button
            type="button"
            onClick={() => {
              clearChat();
              toast.info('已清空会话（框架保留）');
            }}
            className="inline-flex items-center gap-1 rounded-ds-sm px-2 py-1 text-body-sm text-text-3 hover:bg-bg-2 hover:text-text-1"
            disabled={!chatHistory.length}
          >
            <Trash2 size={14} strokeWidth={1.5} />
            Clear
          </button>
        </div>

        <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto p-4">
          {chatHistory.length === 0 && (
            <div className="py-10 text-center text-body-sm text-text-3">
              把你的交易框架贴到 Framework tab，然后在这里问 AI「我最近的交割单哪里偏离了框架」。
              <div className="mt-3 flex flex-wrap justify-center gap-1.5">
                {SUGGESTED_QUESTIONS.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => void send(q)}
                    className="rounded-full border border-subtle px-3 py-1 text-caption text-text-2 hover:border-default hover:text-text-1"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {chatHistory.map((m, i) => (
            <div
              key={`${m.ts}-${i}`}
              className={
                m.role === 'user'
                  ? 'ml-auto max-w-[80%] rounded-ds-md bg-accent-subtle-bg px-3 py-2 text-body text-text-1'
                  : 'mr-auto max-w-[85%] rounded-ds-md border border-subtle bg-bg-2 px-3 py-2 text-body text-text-1'
              }
            >
              {m.role === 'user' ? (
                <div className="whitespace-pre-wrap">{m.content}</div>
              ) : (
                <div className="prose prose-invert prose-sm max-w-none text-body-sm leading-relaxed [&_*]:text-text-1 [&_code]:text-accent">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                </div>
              )}
              <div className="mt-1 font-mono text-mono-xs text-text-3">
                {new Date(m.ts).toLocaleTimeString()}
              </div>
            </div>
          ))}

          {loading && (
            <div className="mr-auto max-w-[85%] rounded-ds-md border border-subtle bg-bg-2 px-3 py-2 text-body-sm text-text-3">
              思考中…（通常 3-10 秒）
            </div>
          )}
        </div>

        {err && !loading && (
          <div className="px-4 py-2">
            <InlineAlert variant="danger" message={err} />
          </div>
        )}

        <div className="flex items-center gap-2 border-t border-subtle p-3">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="问一下你的 journal coach（⌘/Ctrl + Enter 发送）"
            rows={2}
            className="flex-1 resize-none rounded-ds-sm border border-subtle bg-bg-0 p-2 text-body text-text-1 placeholder:text-text-3 focus:border-default focus:outline-none"
          />
          <Button
            variant="primary"
            size="sm"
            iconLeft={Send}
            onClick={() => void send()}
            disabled={loading || !question.trim()}
          >
            Send
          </Button>
        </div>
      </div>

      {/* Side panel — framework preview */}
      <aside className="rounded-ds-md border border-subtle bg-bg-1 p-3">
        <div className="text-label uppercase text-text-3">Framework (active)</div>
        {framework.trim() ? (
          <>
            <pre className="mt-2 whitespace-pre-wrap font-mono text-mono-xs text-text-2">
              {frameworkPreview}
              {framework.split(/\r?\n/).filter((l) => l.trim()).length > 5 && '\n…'}
            </pre>
            <div className="mt-2 font-mono text-mono-xs text-text-3">
              {framework.length} chars
            </div>
          </>
        ) : (
          <p className="mt-2 text-body-sm text-text-3">
            没有设置框架。去 Framework tab 填一段再回来问问题。
          </p>
        )}
      </aside>
    </div>
  );
};

export default AskJournalChat;
