import { useState } from 'react';
import {
  CheckCircle2,
  FilePenLine,
  HardDrive,
  History,
  Save,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import type {
  PositionEpisodeReviewAnnotation,
  PositionEpisodeReviewStatus,
  PositionEpisodeReviewWorkspaceDraft,
  PositionEpisodeTradeLogicDraft,
} from '../../../types/journal';
import {
  clearEpisodeReviewDraft,
  emptyEpisodeReviewDraft,
  EPISODE_REVIEW_FIELD_MAX_CHARS,
  EPISODE_REVIEW_TOTAL_MAX_CHARS,
  episodeReviewContextFieldCount,
  episodeReviewDraftCharacterCount,
  episodeReviewTextCharacterCount,
  hasEpisodeReviewDraft,
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

const REVIEW_STATUS_LABELS: Record<PositionEpisodeReviewStatus, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
};

function formatSavedAt(value?: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
}

function parseLabels(value: string): string[] {
  return [...new Set(
    value
      .split(/[,，\n]/)
      .map((item) => item.trim())
      .filter(Boolean),
  )];
}

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

function LabelInput({
  inputLabel,
  labels,
  placeholder,
  onChange,
}: {
  inputLabel: string;
  labels: string[];
  placeholder: string;
  onChange: (raw: string) => void;
}) {
  const [rawValue, setRawValue] = useState(() => labels.join(', '));
  return (
    <label className="text-body-sm text-text-2">
      {inputLabel}
      <input
        className="input-base mt-1 w-full"
        aria-label={inputLabel}
        value={rawValue}
        placeholder={placeholder}
        onChange={(event) => {
          setRawValue(event.target.value);
          onChange(event.target.value);
        }}
      />
    </label>
  );
}

export function TradeLogicDraftPanel({
  episodeId,
  buildId,
  draft,
  onChange,
  annotation,
  annotationLoading,
  saving,
  saveError,
  saveNotice,
  restoreNotice,
  history,
  historyLoading,
  historyError,
  onSave,
  onLoadHistory,
}: {
  episodeId: number;
  buildId: number;
  draft: PositionEpisodeReviewWorkspaceDraft;
  onChange: (draft: PositionEpisodeReviewWorkspaceDraft) => void;
  annotation: PositionEpisodeReviewAnnotation | null;
  annotationLoading: boolean;
  saving: boolean;
  saveError: string | null;
  saveNotice: string | null;
  restoreNotice: string | null;
  history: PositionEpisodeReviewAnnotation[] | null;
  historyLoading: boolean;
  historyError: string | null;
  onSave: (status: 'in_progress' | 'completed') => void;
  onLoadHistory: () => void;
}) {
  const restoredFieldCount = episodeReviewContextFieldCount(draft);
  const [saveMessage, setSaveMessage] = useState(
    restoredFieldCount > 0
      ? `已从本机恢复 ${restoredFieldCount} 项草稿`
      : '未提交更改会保存在本机',
  );
  const [limitMessage, setLimitMessage] = useState<string | null>(null);
  const [labelsResetVersion, setLabelsResetVersion] = useState(0);

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
      setSaveMessage('未提交更改已保存在本机');
    }
  };

  const updateLabels = (field: 'tags' | 'errorTypes', value: string) => {
    const next = { ...draft, [field]: parseLabels(value) };
    onChange(next);
    const saved = saveEpisodeReviewDraft(buildId, episodeId, next);
    setSaveMessage(
      saved
        ? (hasEpisodeReviewDraft(next) ? '未提交更改已保存在本机' : '空草稿已从本机移除')
        : '浏览器本机存储不可用；内容仅保留在当前页面',
    );
  };

  const clearDraft = () => {
    onChange(emptyEpisodeReviewDraft());
    setLabelsResetVersion((value) => value + 1);
    setLimitMessage(null);
    setSaveMessage(
      clearEpisodeReviewDraft(buildId, episodeId)
        ? '已清空当前编辑；服务器版本与历史未删除'
        : '当前编辑已清空；服务器版本未删除，本机存储不可用',
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
  const hasDraft = hasEpisodeReviewDraft(draft);
  const completionDisabled = fieldCount === 0;
  const status = annotation?.reviewStatus ?? 'not_started';

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
          <span className={`rounded-full border px-2.5 py-1 text-caption ${
            status === 'completed'
              ? 'border-up-strong/25 bg-up-subtle text-up-strong'
              : status === 'in_progress'
                ? 'border-accent-subtle-border bg-accent-subtle-bg text-accent'
                : 'border-subtle bg-bg-2 text-text-3'
          }`}>
            复盘 · {annotationLoading ? '读取中…' : REVIEW_STATUS_LABELS[status]}
          </span>
          <span className="rounded-full border border-accent-subtle-border bg-accent-subtle-bg px-2.5 py-1 text-caption text-accent">
            用户自述 · {fieldCount}/6 项
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
              <p className="text-body-sm font-medium text-text-1">服务器版本 + 本机未提交草稿</p>
              <ul className="mt-1 space-y-1 text-caption text-text-3">
                <li>• 点击下方保存按钮才会写入复盘版本；未提交更改按构建 #{buildId} 与回合 #{episodeId} 留在本机。</li>
                <li>• 复盘版本只保存你的自述、标签与错误分类，不修改 Moomoo 或 execution evidence。</li>
                <li>• 点击“生成证据复盘”时，非空草稿会发送给本机服务，不会调用外部模型。</li>
                <li>• 点击“模型增强”时，非空草稿还会发送给你配置的第三方模型供应商。</li>
              </ul>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <span className="text-right text-caption text-text-3" role="status" aria-live="polite">
              {saveNotice ?? restoreNotice ?? saveMessage}
            </span>
            <button
              type="button"
              className="btn-ghost inline-flex items-center gap-1.5"
              disabled={!hasDraft || saving}
              onClick={clearDraft}
            >
              <Trash2 size={13} /> 清空当前编辑
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

        {saveError && (
          <div
            className="rounded-ds-md border border-down-strong/30 bg-down-subtle px-3 py-2 text-body-sm text-down-strong"
            role="alert"
          >
            保存失败：{saveError}。本机草稿仍然保留，可以稍后重试。
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

        <fieldset className="rounded-ds-md border border-subtle bg-bg-1 p-3">
          <legend className="px-1 text-body-sm font-semibold text-text-1">复盘分类 · 用户自述元数据</legend>
          <p className="mb-3 mt-1 text-caption text-text-3">
            使用逗号分隔；这些分类帮助之后筛选复盘，不会被当作券商证据。
          </p>
          <div className="grid gap-3 lg:grid-cols-2">
            <LabelInput
              key={`tags:${annotation?.id ?? 'new'}:${annotation?.revision ?? 0}:${labelsResetVersion}`}
              inputLabel="复盘标签"
              labels={draft.tags}
              placeholder="趋势延续, 早盘, 0DTE"
              onChange={(value) => updateLabels('tags', value)}
            />
            <LabelInput
              key={`errors:${annotation?.id ?? 'new'}:${annotation?.revision ?? 0}:${labelsResetVersion}`}
              inputLabel="错误类型"
              labels={draft.errorTypes}
              placeholder="追高, 失效后未退出, 仓位过大"
              onChange={(value) => updateLabels('errorTypes', value)}
            />
          </div>
        </fieldset>

        <section className="rounded-ds-md border border-accent-subtle-border bg-accent-subtle-bg/20 p-3" aria-label="保存复盘版本">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-body-sm font-semibold text-text-1">保存为独立复盘版本</h3>
              <p className="mt-1 text-caption text-text-3">
                {annotation
                  ? `当前服务器版本 #${annotation.revision} · ${REVIEW_STATUS_LABELS[annotation.reviewStatus]} · ${formatSavedAt(annotation.createdAt)}`
                  : '服务器尚无复盘版本。保存后可在任何设备重新打开。'}
              </p>
              {completionDisabled && (
                <p className="mt-1 text-caption text-warn-strong">
                  “标记复盘完成”需要六项用户自述中至少填写一项；标签或错误分类不能替代复盘内容。
                </p>
              )}
              {!hasDraft && (
                <p className="mt-1 text-caption text-text-3">至少填写一项内容后才能保存进行中。</p>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-ghost inline-flex items-center gap-1.5"
                disabled={!hasDraft || saving}
                onClick={() => onSave('in_progress')}
              >
                <Save size={14} /> {saving ? '保存中…' : '保存进行中'}
              </button>
              <button
                type="button"
                className="btn-primary inline-flex items-center gap-1.5"
                disabled={completionDisabled || saving}
                onClick={() => onSave('completed')}
              >
                <CheckCircle2 size={14} /> {saving ? '保存中…' : '标记复盘完成'}
              </button>
            </div>
          </div>
        </section>

        <details
          className="rounded-ds-md border border-subtle bg-bg-2 p-3"
          onToggle={(event) => {
            if (event.currentTarget.open) onLoadHistory();
          }}
        >
          <summary className="flex cursor-pointer list-none items-center gap-2 text-body-sm text-text-2">
            <History size={14} /> 查看用户自述版本历史
          </summary>
          <div className="mt-3">
            {historyLoading && <p className="text-caption text-text-3">读取版本历史…</p>}
            {!historyLoading && historyError && (
              <div className="flex flex-wrap items-center justify-between gap-2 text-caption text-down-strong" role="alert">
                <span>版本历史读取失败：{historyError}</span>
                <button type="button" className="btn-ghost" onClick={onLoadHistory}>重试</button>
              </div>
            )}
            {!historyLoading && history && history.length === 0 && (
              <p className="text-caption text-text-3">尚无已保存的用户自述版本。</p>
            )}
            {!historyLoading && history && history.length > 0 && (
              <ol className="space-y-3">
                {history.map((item) => (
                  <li key={item.id} className="rounded-ds-sm border border-subtle bg-bg-1 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono text-mono-xs text-text-2">
                        版本 #{item.revision} · {REVIEW_STATUS_LABELS[item.reviewStatus]}
                      </span>
                      <time className="text-caption text-text-3" dateTime={item.createdAt}>
                        {formatSavedAt(item.createdAt)}
                      </time>
                    </div>
                    <dl className="mt-2 grid gap-2 text-caption text-text-2 lg:grid-cols-2">
                      {[...PRE_TRADE_FIELDS, ...POST_TRADE_FIELDS]
                        .filter(({ field }) => item[field]?.trim())
                        .map(({ field, label }) => (
                          <div key={field}>
                            <dt className="text-text-3">{label}</dt>
                            <dd className="mt-0.5 whitespace-pre-wrap">{item[field]}</dd>
                          </div>
                        ))}
                    </dl>
                    {(item.tags.length > 0 || item.errorTypes.length > 0) && (
                      <div className="mt-2 flex flex-wrap gap-2 text-caption text-text-3">
                        {item.tags.map((tag) => <span key={`tag:${tag}`}>#{tag}</span>)}
                        {item.errorTypes.map((errorType) => (
                          <span key={`error:${errorType}`} className="text-warn-strong">错误：{errorType}</span>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </details>
      </div>
    </section>
  );
}

export default TradeLogicDraftPanel;
