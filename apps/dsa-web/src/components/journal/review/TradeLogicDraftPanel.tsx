import { useState } from 'react';
import { FilePenLine, HardDrive, ShieldCheck, Trash2 } from 'lucide-react';
import type { PositionEpisodeTradeLogicDraft } from '../../../types/journal';
import {
  clearEpisodeReviewDraft,
  emptyEpisodeReviewDraft,
  EPISODE_REVIEW_FIELD_MAX_CHARS,
  EPISODE_REVIEW_TOTAL_MAX_CHARS,
  episodeReviewContextFieldCount,
  episodeReviewDraftCharacterCount,
  episodeReviewTextCharacterCount,
  limitEpisodeReviewText,
  saveEpisodeReviewDraft,
} from './episodeReviewDraft';

type DraftField = keyof PositionEpisodeTradeLogicDraft;

interface FieldDefinition {
  field: DraftField;
  label: string;
  helper: string;
  placeholder: string;
}

const PRE_TRADE_FIELDS: FieldDefinition[] = [
  {
    field: 'setupThesis',
    label: '策略假设 / Setup thesis',
    helper: '当时看到的结构、方向假设与预期路径。',
    placeholder: '例：大盘维持上升趋势，底层回踩 8 EMA 后预期延续；若不是当时写下的，请明确标注为回忆。',
  },
  {
    field: 'entryTrigger',
    label: '进场触发 / Entry trigger',
    helper: '必须出现什么价格、K 线、成交量或时间条件才进场。',
    placeholder: '例：5m 收回前高且下一根不跌破；09:45 ET 后才允许进场。',
  },
  {
    field: 'invalidationPlan',
    label: '失效点与止损 / Invalidation',
    helper: '写下观点何时失效，以及计划如何退出。',
    placeholder: '例：5m 收盘跌破结构低点即失效；期权亏损达到计划风险上限时退出。',
  },
  {
    field: 'positionRationale',
    label: '仓位理由 / Position rationale',
    helper: '说明合约、数量、到期日和风险预算为什么匹配这次机会。',
    placeholder: '例：因波动较高只使用半仓；最大计划亏损不超过当日风险预算。',
  },
];

const POST_TRADE_FIELDS: FieldDefinition[] = [
  {
    field: 'exitReason',
    label: '实际出场原因 / Exit reason',
    helper: '记录真正触发退出的原因，不用盈亏结果替代执行理由。',
    placeholder: '例：达到第一目标后减仓，剩余仓位因跌破 8 EMA 平仓；或明确写“情绪性退出”。',
  },
  {
    field: 'postTradeReflection',
    label: '事后反思 / Post-trade reflection',
    helper: '只在这里写结果已知后的观察、偏差与下一次可验证的改进。',
    placeholder: '例：入场符合计划，但加仓没有新触发；下次只有重新站稳结构位才允许加仓。',
  },
];

function DraftTextarea({
  definition,
  value,
  inputId,
  onChange,
}: {
  definition: FieldDefinition;
  value: string;
  inputId: string;
  onChange: (value: string) => void;
}) {
  const helperId = `${inputId}-helper`;
  return (
    <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
      <div className="flex items-start justify-between gap-3">
        <label htmlFor={inputId} className="text-body-sm font-medium text-text-1">
          {definition.label}
        </label>
        <span className="shrink-0 font-mono text-[10px] text-text-4">
          {episodeReviewTextCharacterCount(value)}/{EPISODE_REVIEW_FIELD_MAX_CHARS}
        </span>
      </div>
      <p id={helperId} className="mt-1 text-caption text-text-3">{definition.helper}</p>
      <textarea
        id={inputId}
        value={value}
        rows={3}
        aria-describedby={helperId}
        placeholder={definition.placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="mt-3 w-full resize-y rounded-ds-sm border border-subtle bg-bg-0 p-2.5 text-body-sm leading-relaxed text-text-1 placeholder:text-text-4 focus:border-accent focus:outline-none"
      />
    </div>
  );
}

export function TradeLogicDraftPanel({
  episodeId,
  buildId,
  draft,
  onChange,
}: {
  episodeId: number;
  buildId: number;
  draft: PositionEpisodeTradeLogicDraft;
  onChange: (draft: PositionEpisodeTradeLogicDraft) => void;
}) {
  const restoredFieldCount = episodeReviewContextFieldCount(draft);
  const [saveMessage, setSaveMessage] = useState(
    restoredFieldCount > 0
      ? `已从本机恢复 ${restoredFieldCount} 项草稿`
      : '输入后会自动保存在本机',
  );
  const [limitMessage, setLimitMessage] = useState<string | null>(null);

  const updateField = (field: DraftField, value: string) => {
    const exceedsFieldLimit = episodeReviewTextCharacterCount(value)
      > EPISODE_REVIEW_FIELD_MAX_CHARS;
    const fieldCandidate = limitEpisodeReviewText(value, EPISODE_REVIEW_FIELD_MAX_CHARS);
    const otherFieldsLength = episodeReviewDraftCharacterCount(draft)
      - episodeReviewTextCharacterCount(draft[field]);
    const remainingForField = Math.max(0, EPISODE_REVIEW_TOTAL_MAX_CHARS - otherFieldsLength);
    const exceedsTotalLimit = episodeReviewTextCharacterCount(fieldCandidate) > remainingForField;
    const acceptedValue = exceedsTotalLimit
      ? limitEpisodeReviewText(fieldCandidate, remainingForField)
      : fieldCandidate;
    const next = { ...draft, [field]: acceptedValue };
    onChange(next);
    if (exceedsTotalLimit) {
      setLimitMessage(`交易逻辑最多发送 ${EPISODE_REVIEW_TOTAL_MAX_CHARS} 个字符；超出部分未保存。请先精简其他字段。`);
    } else if (exceedsFieldLimit) {
      setLimitMessage(`每个字段最多 ${EPISODE_REVIEW_FIELD_MAX_CHARS} 个字符；超出部分未保存。`);
    } else {
      setLimitMessage(null);
    }
    const saved = saveEpisodeReviewDraft(buildId, episodeId, next);
    if (!saved) {
      setSaveMessage('浏览器本机存储不可用；内容仅保留在当前页面');
    } else if (episodeReviewContextFieldCount(next) === 0) {
      setSaveMessage('空草稿已从本机移除');
    } else {
      setSaveMessage('已自动保存到本机');
    }
  };

  const clearDraft = () => {
    onChange(emptyEpisodeReviewDraft());
    setLimitMessage(null);
    setSaveMessage(
      clearEpisodeReviewDraft(buildId, episodeId)
        ? '已清空这个回合的本机草稿'
        : '草稿已从页面清空，但浏览器本机存储不可用',
    );
  };

  const renderFields = (fields: FieldDefinition[]) => fields.map((definition) => (
    <DraftTextarea
      key={definition.field}
      definition={definition}
      value={draft[definition.field]}
      inputId={`trade-logic-${buildId}-${episodeId}-${definition.field}`}
      onChange={(value) => updateField(definition.field, value)}
    />
  ));

  const fieldCount = episodeReviewContextFieldCount(draft);
  const totalCharacterCount = episodeReviewDraftCharacterCount(draft);
  const totalLimitReached = totalCharacterCount >= EPISODE_REVIEW_TOTAL_MAX_CHARS;

  return (
    <section className="card-base overflow-hidden" aria-labelledby="trade-logic-draft-title">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-subtle px-4 py-4">
        <div>
          <div className="flex items-center gap-2 text-label uppercase tracking-label text-text-3">
            <FilePenLine size={14} /> Review worksheet
          </div>
          <h2 id="trade-logic-draft-title" className="mt-1 text-h2 text-text-1">交易逻辑草稿</h2>
          <p className="mt-1 max-w-3xl text-caption text-text-3">
            补充成交单无法证明的“当时为什么”。复盘助手会把这些文字作为用户陈述，与 execution evidence 分开处理。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-accent-subtle-border bg-accent-subtle-bg px-2.5 py-1 text-caption text-accent">
            本机草稿 · {fieldCount}/6 项
          </span>
          <span className={`rounded-full border px-2.5 py-1 font-mono text-caption ${
            totalLimitReached
              ? 'border-warn-strong/30 bg-warn-subtle text-warn-strong'
              : 'border-subtle bg-bg-2 text-text-3'
          }`}>
            总字符 {totalCharacterCount}/{EPISODE_REVIEW_TOTAL_MAX_CHARS}
          </span>
          <span className="rounded-full border border-subtle bg-bg-2 px-2.5 py-1 text-caption text-text-3">
            不属于券商证据
          </span>
        </div>
      </div>

      <div className="space-y-4 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-ds-md border border-up-strong/20 bg-up-subtle p-3">
          <div className="flex items-start gap-2">
            <HardDrive size={15} className="mt-0.5 shrink-0 text-up-strong" />
            <div>
              <p className="text-body-sm font-medium text-text-1">只在本机持久化，不写 Moomoo/证据账本</p>
              <ul className="mt-1 space-y-1 text-caption text-text-3">
                <li>• 浏览器仅按构建 #{buildId} 与回合 #{episodeId} 保存这份草稿。</li>
                <li>• 点击“生成证据复盘”时，非空草稿会发送给本机服务，不会调用外部模型。</li>
                <li>• 点击“模型增强”时，非空草稿还会发送给你配置的第三方模型供应商。</li>
              </ul>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-caption text-text-3" role="status" aria-live="polite">{saveMessage}</span>
            <button
              type="button"
              className="btn-ghost inline-flex items-center gap-1.5"
              disabled={fieldCount === 0}
              onClick={clearDraft}
            >
              <Trash2 size={13} /> 清空草稿
            </button>
          </div>
        </div>

        {(limitMessage || totalLimitReached) && (
          <div
            className="rounded-ds-md border border-warn-strong/30 bg-warn-subtle px-3 py-2 text-body-sm text-warn-strong"
            role="alert"
          >
            {limitMessage ?? `已达到 ${EPISODE_REVIEW_TOTAL_MAX_CHARS} 个字符总上限；继续输入前请先精简其他字段。`}
          </div>
        )}

        <fieldset className="rounded-ds-md border border-accent-subtle-border bg-accent-subtle-bg/30 p-3">
          <legend className="px-1 text-body-sm font-semibold text-text-1">事后回忆的进场前计划 · 只写当时可知信息</legend>
          <div className="mb-3 mt-1 flex items-start gap-2 text-caption text-text-3">
            <ShieldCheck size={14} className="mt-0.5 shrink-0 text-accent" />
            <p>这是事后补录：若内容来自当时的笔记，请注明来源；否则系统只把它视为你的回忆。如果当时没有明确计划，请写“当时未定义”，不要根据盈亏倒推一个 setup。</p>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">{renderFields(PRE_TRADE_FIELDS)}</div>
        </fieldset>

        <fieldset className="rounded-ds-md border border-warn-strong/20 bg-warn-subtle/30 p-3">
          <legend className="px-1 text-body-sm font-semibold text-text-1">交易后记录 · 结果已知后的观察</legend>
          <p className="mb-3 mt-1 text-caption text-text-3">
            出场事实和事后反思放在这里，系统不会把它们伪装成进场前就已经知道的条件。
          </p>
          <div className="grid gap-3 lg:grid-cols-2">{renderFields(POST_TRADE_FIELDS)}</div>
        </fieldset>
      </div>
    </section>
  );
}

export default TradeLogicDraftPanel;
