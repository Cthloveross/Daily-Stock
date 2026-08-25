import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { fetchPersonalEdge } from '../../api/journal';
import { parseApiError, type ParsedApiError } from '../../api/error';
import type {
  PersonalEdgeResponse,
  RuleComplianceLaneStat,
  RuleComplianceSlice,
  RuleComplianceStat,
} from '../../types/journal';
import { InfoHint } from '../common/InfoHint';
import { Tooltip } from '../common/Tooltip';

/**
 * 「规则遵守度」：规模与频率的同层兄弟区块，把用户自己那套两车道规则
 * （Playbook 候选「V2-0」…「V2-D」）变成**可前向证伪的记账**。
 *
 * 数据来自 `GET /journal/v2/personal-edge` 的 `rule_compliance` 块（当前默认
 * build 实时重算，前端不硬编码任何数字）。车道判定是纯机械的：只读
 * `dte_at_entry`、`opened_at` 的 ET 小时、`opened_at`/`closed_at` 的 ET 自然日与
 * `ABS(opening_cash_flow)`——它无从知道进场当时的意图，因此也不声称知道。
 *
 * 为什么这张表值得看：规则本身就是从这张表的「全历史」一列推出来的，所以那一列
 * **必然**好看；真正能证伪或确认规则的是「采纳后」一列。采纳后尚无样本时，本区块
 * 显示一行「尚无采纳后样本」，**绝不渲染成一张全 0 的表**——0 会被读成「合规单
 * 一分没赚」，而事实是「还没有样本」。
 *
 * 最重要的一个数字是**合规单 vs 违规单的毛每美元**：它就是这套规则的成败判据。
 *
 * 依据（用户自身 build #3 干净口径样本 n=1,407，2026-04-21 起、仅明细成交构建）：
 * 同为 4-7DTE 合约，隔夜持有毛 +34.13%（n=65，胜率 61.5%，过路费 1.55%，剔除最好
 * 3 笔仍 +22.27%、最好 5 笔仍 +19.13%——全样本唯一非尾部驱动的组合），当日平掉
 * −4.04%（n=57，胜率 22.8%）；0DTE 日内 +3.44%（n=556）但剔除最好 5 笔仅 +0.41%，
 * 低于 1.76% 过路费；ET 12:00 后的 0DTE −8.19%（剔除最好 5 笔 −15.54%）；1-3DTE
 * 两头都不占（当日平 −2.94%，n=469；隔夜 +6.98% 但剔尾后 −4.03%）。
 *
 * 诚实边界：样本仅 2026-04→07 一个市场状态（SPY 上行，隔夜多头天然占优），过夜
 * 车道 n=65 偏小、隔夜跳空风险未被充分体现。描述统计，不是因果结论，不构成建议；
 * 本系统只读，不下单。
 */

const MISSING = '标缺';

const LANE_LABELS: Record<string, string> = {
  intraday_0dte: '日内 0DTE',
  overnight_4_7: '过夜 4-7DTE',
  dte_1_3: '1-3DTE',
  bought_time_unused: '买了时间却不用',
  late_0dte: '午后 0DTE',
  other: '≥8DTE 隔夜',
  unknown: '不可判定',
};

const VERDICT_LABELS: Record<string, string> = {
  compliant: '合规单',
  violation: '违规单',
  uncovered: '规则未覆盖',
  unknown: '不可判定',
};

export const EMPTY_SINCE_ADOPTION_TEXT = '尚无采纳后样本';

const fmtPct = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return MISSING;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value * 100).toFixed(2)}%`;
};

/** 过路费是成本，不带正负号——它只是一道必须跨过去的线。 */
const fmtToll = (value: number | null | undefined): string =>
  value === null || value === undefined ? MISSING : `${(value * 100).toFixed(2)}%`;

const fmtRate = (value: number | null | undefined): string =>
  value === null || value === undefined ? MISSING : `${(value * 100).toFixed(1)}%`;

const fmtUsd = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return MISSING;
  const sign = value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
};

/** 缺分母的读数把原因挂在 Tooltip 上（仓库 UI 治理禁用原生 title）。 */
const Cell: React.FC<{ text: string; reason?: string | null }> = ({ text, reason }) => (
  <td className="px-3 py-1.5 text-right font-mono text-mono-xs tabular-nums text-text-1">
    {text === MISSING && reason ? (
      <Tooltip focusable content={reason}>
        <span aria-label={reason}>{text}</span>
      </Tooltip>
    ) : (
      <span>{text}</span>
    )}
  </td>
);

function LaneRow({ lane }: { lane: RuleComplianceLaneStat }) {
  return (
    <tr data-lane={lane.key} data-verdict={lane.verdict}>
      <td className="px-3 py-1.5 text-left text-text-1">
        <span>{LANE_LABELS[lane.key] ?? lane.key}</span>
        <span className="ml-2 font-mono text-mono-xs text-text-3">{lane.ruleId}</span>
      </td>
      <td className="px-3 py-1.5 text-left text-caption text-text-3">
        {VERDICT_LABELS[lane.verdict] ?? lane.verdict}
      </td>
      <Cell text={lane.n.toLocaleString()} />
      <Cell text={fmtUsd(lane.risk)} reason={lane.ratioReason} />
      <Cell text={fmtUsd(lane.gross)} reason={lane.ratioReason} />
      <Cell text={fmtPct(lane.grossPct)} reason={lane.ratioReason} />
      <Cell text={fmtToll(lane.tollPct)} reason={lane.ratioReason} />
      <Cell text={fmtRate(lane.winRate)} reason={lane.ratioReason} />
      <Cell
        text={fmtPct(lane.grossPctExcludingTopN)}
        reason={lane.excludingTopNReason}
      />
    </tr>
  );
}

/** 合规单 vs 违规单：这就是会证伪或确认这套规则的那个数字，必须最醒目。 */
function Headline({ slice }: { slice: RuleComplianceSlice }) {
  const byVerdict = new Map<string, RuleComplianceStat>(
    slice.verdicts.map((item) => [item.key, item]),
  );
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-2" aria-label="合规与违规对比">
      {['compliant', 'violation'].map((verdict) => {
        const stat = byVerdict.get(verdict);
        return (
          <div key={verdict} data-headline={verdict} className="min-w-[9rem]">
            <div className="text-caption text-text-3">
              {VERDICT_LABELS[verdict]} · {stat ? stat.n.toLocaleString() : 0} 笔
            </div>
            <div className="font-mono text-h3 tabular-nums text-text-1">
              {stat && stat.grossPct !== null ? (
                fmtPct(stat.grossPct)
              ) : (
                <Tooltip focusable content={stat?.ratioReason ?? MISSING}>
                  <span aria-label={stat?.ratioReason ?? MISSING}>{MISSING}</span>
                </Tooltip>
              )}
            </div>
            <div className="text-caption text-text-3">
              剔尾 {fmtPct(stat?.grossPctExcludingTopN)} · 过路费{' '}
              {fmtToll(stat?.tollPct)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Slice({
  slice,
  title,
  caption,
  excludeTopN,
}: {
  slice: RuleComplianceSlice;
  title: string;
  caption: string;
  excludeTopN: number;
}) {
  const empty = slice.state !== 'ready';
  return (
    <div className="space-y-2" data-slice={title}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-h3 text-text-1">{title}</h3>
        <span className="text-caption text-text-3">{caption}</span>
      </div>

      {empty ? (
        <p
          className="rounded-ds-sm border border-dashed border-subtle px-3 py-2 text-body-sm text-text-3"
          role="note"
          data-empty-slice={slice.state}
        >
          <strong className="text-text-2">
            {slice.state === 'no_episodes_since_adoption'
              ? EMPTY_SINCE_ADOPTION_TEXT
              : '本区间无样本'}
          </strong>
          {slice.stateReason ? ` · ${slice.stateReason}` : null}
        </p>
      ) : (
        <>
          <Headline slice={slice} />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-body-sm">
              <thead>
                <tr className="border-b border-subtle text-caption text-text-3">
                  <th className="px-3 py-1.5 text-left font-normal">车道</th>
                  <th className="px-3 py-1.5 text-left font-normal">判定</th>
                  <th className="px-3 py-1.5 text-right font-normal">笔数</th>
                  <th className="px-3 py-1.5 text-right font-normal">风险金额</th>
                  <th className="px-3 py-1.5 text-right font-normal">毛盈亏</th>
                  <th className="px-3 py-1.5 text-right font-normal">毛每美元</th>
                  <th className="px-3 py-1.5 text-right font-normal">过路费</th>
                  <th className="px-3 py-1.5 text-right font-normal">胜率</th>
                  <th className="px-3 py-1.5 text-right font-normal">
                    剔除最好 {excludeTopN} 笔
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[color:var(--border-subtle)]">
                {slice.lanes.map((lane) => (
                  <LaneRow key={lane.key} lane={lane} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export const RuleCompliancePanel: React.FC = () => {
  const [edge, setEdge] = useState<PersonalEdgeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    setError(null);
    try {
      setEdge(await fetchPersonalEdge(refresh));
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const compliance = edge?.ruleCompliance ?? null;
  const excludeTopN = compliance?.excludeTopN ?? 5;

  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1" aria-label="规则遵守度">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">
            Rule compliance
          </div>
          <h2 className="mt-0.5 flex items-baseline gap-2 text-h2 text-text-1">
            规则遵守度
            {/* 界面披露策略（2026-08-15）：端点 limitations 原文一字不删，
                从底部常驻列表收进这枚 ⓘ；正文只保留车道判定与样本口径
                （它们是表内数字的基准标注，不是免责声明）。 */}
            {compliance && compliance.limitations.length > 0 && (
              <InfoHint
                text={compliance.limitations.join('\n')}
                label="规则遵守度诚实边界"
              />
            )}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {edge?.dataState === 'ready' && (
            <span className="font-mono text-mono-xs text-text-3">
              Build #{edge.buildId} · {edge.sourceKind}
            </span>
          )}
          <button
            type="button"
            className="btn-ghost text-body-sm"
            onClick={() => void load(true)}
            disabled={loading}
          >
            刷新
          </button>
        </div>
      </div>

      <div className="space-y-4 px-4 py-3">
        {loading && !edge && (
          <p className="py-4 text-center text-body-sm text-text-3">重算规则遵守度读数中…</p>
        )}

        {error && (
          <p className="text-body-sm text-down-strong" role="alert">
            {error.message}
          </p>
        )}

        {!error && edge?.dataState === 'not_built' && (
          <p className="text-body-sm text-text-3">
            还没有 Episode 构建。导入证据并生成构建后，这里会按车道摊开合规单与违规单的每美元回报。
          </p>
        )}

        {!error && edge?.dataState === 'ready' && !compliance && (
          <p className="text-body-sm text-text-3">规则遵守度读数{MISSING}（端点未返回该区块）。</p>
        )}

        {!error && compliance && (
          <>
            <p className="text-caption text-text-3">
              车道判定<strong className="text-text-2">纯机械</strong>：只读 <code className="font-mono text-mono-xs">dte_at_entry</code>、
              <code className="mx-1 font-mono text-mono-xs">opened_at</code> 的 ET 小时、
              <code className="mx-1 font-mono text-mono-xs">opened_at</code>/
              <code className="mx-1 font-mono text-mono-xs">closed_at</code> 的 ET 自然日与{' '}
              <code className="font-mono text-mono-xs">ABS(opening_cash_flow)</code>
              ——它无从知道进场当时的意图，因此也不声称知道。日内 0DTE ＝ 0DTE 且 ET{' '}
              {compliance.intradayLaneEtCutoffHour}:00 前开仓（V2-A）；过夜 ＝{' '}
              {compliance.overnightLaneMinDte}-{compliance.overnightLaneMaxDte}DTE 且跨自然日平仓（V2-B）；
              违规三条为 1-3DTE（V2-C①）、≥4DTE 当日平（V2-C②）、ET{' '}
              {compliance.intradayLaneEtCutoffHour}:00 后开 0DTE（V2-C③）。毛每美元 ＝
              （净盈亏 ＋ 费用）÷ Σ|开仓现金流|；「剔除最好 {excludeTopN} 笔」与规模与频率同一实现、
              同一 n≥{compliance.excludeTopNMinEpisodeCount} 门槛，样本不足时缺席并给出原因。
            </p>

            <p className="text-caption text-text-3" data-basis="clean">
              样本口径：{compliance.cleanBasisReason}。本 build 内共{' '}
              <strong className="text-text-2">{compliance.populationN.toLocaleString()}</strong> 笔进入统计；
              另有 {compliance.excludedBeforeCleanBasisCount.toLocaleString()} 笔早于{' '}
              {compliance.cleanBasisStart}、
              {compliance.excludedAggregateOrUnknownBasisCount.toLocaleString()} 笔口径不可比、
              {compliance.excludedMissingPremiumCount.toLocaleString()} 笔缺开仓现金流被排除。
            </p>

            <Slice
              slice={compliance.sinceAdoption}
              title="采纳后（前向验证）"
              caption={`自 ${compliance.adoptedAt} 起 · 这一列才能证伪或确认这套规则`}
              excludeTopN={excludeTopN}
            />

            <Slice
              slice={compliance.allHistory}
              title="全历史（规则从这里推出来）"
              caption={`自 ${compliance.cleanBasisStart} 起 · 规则由这段样本推出，因此它必然好看`}
              excludeTopN={excludeTopN}
            />
          </>
        )}
      </div>
    </section>
  );
};

export default RuleCompliancePanel;
