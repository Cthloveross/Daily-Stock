import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, X } from 'lucide-react';
import { fetchNearExpiryContracts } from '../../api/opportunities';
import type {
  IntradayPulseResponse,
  IntradayTopResponse,
  NearExpiryContractItem,
  NearExpiryContractRow,
} from '../../types/opportunities';
import {
  INTRADAY_PLAN_MAX_TICKERS,
  selectPlanTickers,
  useIntradayPlanStore,
} from '../../stores/intradayPlanStore';
import { InfoHint } from '../common/InfoHint';
import { Tooltip } from '../common/Tooltip';
import { LANE_SAMPLE_CAVEAT } from './LaneChecklistPanel';
import { SPREAD_ILLIQUID_THRESHOLD_PERCENT } from './NearExpiryContractPanel';
import { quoteTimeLabel } from './intradayFormat';
import {
  DEFAULT_STANDARD_SIZE_USD,
  ROUND_TURN_FEE_PER_CONTRACT_USD,
  classifyContractPrice,
  computeContractSizing,
} from './intradayContractSizing';
import {
  evaluatePlanTicker,
  type PlanCheckRow,
  type PlanTickerView,
} from './intradayPlanChecks';
import {
  OVERNIGHT_MAX_DTE,
  OVERNIGHT_MIN_DTE,
  type CheckStatus,
} from './laneChecklist';

const STATUS_TEXT: Record<CheckStatus, string> = {
  pass: '符合',
  fail: '不符合',
  missing: '标缺',
  requirement: '要求',
  // 纯描述读数：达线/未达线不是判定（v8 循环性更正，见 laneChecklist）。
  neutral: '读数',
};

const STATUS_MARK: Record<CheckStatus, string> = {
  pass: '✓',
  fail: '✕',
  missing: '—',
  requirement: '▸',
  neutral: '·',
};

const STATUS_CLASS: Record<CheckStatus, string> = {
  pass: 'text-text-1',
  fail: 'text-warn-strong',
  missing: 'text-text-3',
  requirement: 'text-text-2',
  neutral: 'text-text-2',
};

/**
 * 面板定位句（用户明确要求：不要满屏「只读/不下单」样板话；2026-08-15
 * 界面披露策略后进一步收进标题行 ⓘ tooltip，不再占可见正文——原文一字不删）。
 */
export const PLAN_FRAMING_LINE = '按你自己的规则机械核对，不是买卖建议';

/** 标题行 ⓘ 的完整诚实边界：定位句 + 样本口径 caveat（逐字）。 */
export const PLAN_HONESTY_NOTE = [
  `${PLAN_FRAMING_LINE}。`,
  LANE_SAMPLE_CAVEAT,
].join('\n');

export const INVALIDATION_PROMPT =
  '进场前先写下失效位；说不清就不开（结构止损）';

function formatUsd(value: number): string {
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatPrice(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—';
  return value.toFixed(2);
}

/** 参考价：优先 mid（可成交口径），退到最新价；两者都缺＝标缺，不猜。 */
function referencePrice(row: NearExpiryContractRow): { price: number | null; basis: string } {
  if (row.mid !== null && Number.isFinite(row.mid) && row.mid > 0) {
    return { price: row.mid, basis: 'mid=(bid+ask)/2' };
  }
  if (row.lastPrice !== null && Number.isFinite(row.lastPrice) && row.lastPrice > 0) {
    return { price: row.lastPrice, basis: '最新价（无双边报价）' };
  }
  return { price: null, basis: '无可用报价' };
}

function CheckRow({ check }: { check: PlanCheckRow }) {
  const tag = check.ruleId ?? check.basis ?? '';
  return (
    <li
      data-plan-check-id={check.id}
      data-plan-check-status={check.status}
      className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5"
    >
      <span className="w-24 shrink-0 text-caption text-text-3">{check.label}</span>
      <span className={`w-16 shrink-0 text-caption font-medium ${STATUS_CLASS[check.status]}`}>
        <span aria-hidden="true">{STATUS_MARK[check.status]} </span>
        {STATUS_TEXT[check.status]}
      </span>
      <span className="min-w-0 flex-1 text-caption text-text-2">{check.reason}</span>
      {tag && (
        <span className="shrink-0 rounded-ds-sm border border-subtle px-1 text-caption text-text-3">
          {check.ruleId ?? '读数口径'}
        </span>
      )}
    </li>
  );
}

/**
 * 合约候选区：按今日车道推出的 DTE 区间读一次临期合约，按**用户自己的**
 * 价格分档标注，并把标准仓位换成张数与手续费。不排序偏好、不推荐任何一张。
 */
function PlanContracts({
  ticker,
  minDte,
  maxDte,
  windowMode,
  dteLabel,
  standardSizeUsd,
}: {
  ticker: string;
  minDte: number;
  maxDte: number;
  windowMode: PlanTickerView['contractWindowMode'];
  dteLabel: string;
  standardSizeUsd: number;
}) {
  const [item, setItem] = useState<NearExpiryContractItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    try {
      const response = await fetchNearExpiryContracts(ticker, maxDte, { refresh });
      setItem(response.item);
      setError(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : '临期合约读取失败');
    } finally {
      setLoading(false);
    }
  }, [ticker, maxDte]);

  useEffect(() => {
    void load(false);
  }, [load]);

  // 车道未知时同时显示两个合规窗口（0DTE 与 4-7DTE），1-3DTE 与车道无关地
  // 绝不显示（V2-C①）——「车道读不到」绝不静默降级成「默认日内」。
  const groups = (item?.expiries ?? []).filter((group) => (
    windowMode === 'unknown'
      ? group.dte === 0
        || (group.dte >= OVERNIGHT_MIN_DTE && group.dte <= OVERNIGHT_MAX_DTE)
      : group.dte >= minDte
  ));

  return (
    <div className="mt-2 rounded-ds-sm border border-subtle bg-bg-0 p-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-caption font-medium text-text-2">
          合约候选 · {dteLabel}
        </span>
        <div className="flex items-center gap-2">
          <Tooltip
            focusable
            contentClassName="whitespace-pre-line"
            content={
              '$2-8 甜蜜区（你自己的历史）：$2-4 净 +3.12%（n=544）、$4-8 净 +3.44%（n=208）。\n'
              + '<$1 净 −14.25%（n=72），费用吃掉权利金的 5.56%——同样金额买便宜合约＝张数多＝手续费按张收。\n'
              + '$1-2 净 −1.28%（n=365，毛 +0.93%，被 2.20% 费用翻负）；>$8 净 −0.96%（n=218）。\n'
              + LANE_SAMPLE_CAVEAT
            }
          >
            <span className="text-caption text-text-3 underline decoration-dotted underline-offset-2">
              价格分档依据 ⓘ
            </span>
          </Tooltip>
          <button
            type="button"
            disabled={loading}
            onClick={() => void load(true)}
            className="flex items-center gap-1 text-caption text-text-2 hover:text-text-1 disabled:opacity-50"
            aria-label={`刷新 ${ticker} 合约候选`}
          >
            <RefreshCw size={11} className={loading ? 'animate-spin' : undefined} />
            刷新
          </button>
        </div>
      </div>

      {error && (
        <p className="mt-1 text-caption text-warning" role="status">
          合约读取失败：{error}
        </p>
      )}

      {!item && loading ? (
        <div className="mt-2 h-8 animate-pulse rounded-ds-sm bg-bg-2" />
      ) : !item ? null : item.state !== 'ready' ? (
        <p className="mt-1 text-caption text-text-3">{item.message}</p>
      ) : groups.length === 0 ? (
        <p className="mt-1 text-caption text-text-3">
          {windowMode === 'unknown'
            ? `链内 0–${maxDte}DTE 中没有 0DTE 或 ${OVERNIGHT_MIN_DTE}-${OVERNIGHT_MAX_DTE}DTE 的到期日（今日车道未知，仅显示这两个合规窗口）。`
            : minDte > 0
              ? `链内 0–${maxDte}DTE 中没有 ${minDte}DTE 及以上的到期日（今日车道要求 ${dteLabel}）。`
              : '链内没有符合今日车道的到期日。'}
        </p>
      ) : (
        <div className="mt-1.5 overflow-x-auto">
          <table
            className="w-full min-w-[780px] border-collapse"
            aria-label={`${ticker} 合约候选表`}
          >
            <thead>
              <tr className="border-b border-subtle text-left text-caption text-text-3">
                <th className="px-2 py-1 font-medium">到期</th>
                <th className="px-2 py-1 font-medium">行权价 / CP</th>
                <th className="px-2 py-1 text-right font-medium">参考价</th>
                <th className="px-2 py-1 text-right font-medium">Bid × Ask（点差%）</th>
                <th className="px-2 py-1 font-medium">价格分档</th>
                <th className="px-2 py-1 text-right font-medium">可买张数</th>
                <th className="px-2 py-1 text-right font-medium">手续费估算</th>
                <th className="px-2 py-1 text-right font-medium">费/本金%</th>
                <th className="px-2 py-1 text-right font-medium">as-of</th>
              </tr>
            </thead>
            <tbody>
              {groups.flatMap((group) => group.contracts.map((row) => {
                const { price, basis } = referencePrice(row);
                const band = classifyContractPrice(price);
                const sizing = computeContractSizing(price, standardSizeUsd);
                const illiquid = row.spreadPercent !== null
                  && row.spreadPercent > SPREAD_ILLIQUID_THRESHOLD_PERCENT;
                return (
                  <tr
                    key={row.code}
                    data-contract-code={row.code}
                    data-price-band={band?.band ?? 'unknown'}
                    className={`border-b border-subtle text-body-sm last:border-b-0 ${
                      band?.highlighted ? 'bg-bg-2' : ''
                    } ${band?.warned ? 'opacity-60' : ''}`}
                  >
                    <td className="px-2 py-1 text-caption text-text-3">
                      {group.dte}DTE · {group.expiry}
                    </td>
                    <td className="px-2 py-1 font-mono text-mono-xs text-text-1">
                      {formatPrice(row.strike)} {row.right === 'C' ? 'Call' : 'Put'}
                      {row.isAtm && (
                        <span className="ml-1 rounded-ds-sm border border-subtle px-1 text-caption text-text-2">
                          ATM
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-mono-xs text-text-1">
                      <Tooltip focusable content={`参考价口径：${basis}`}>
                        <span>{price === null ? '标缺' : formatPrice(price)}</span>
                      </Tooltip>
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-mono-xs text-text-2">
                      {row.bid === null || row.ask === null
                        ? '标缺'
                        : `${formatPrice(row.bid)} × ${formatPrice(row.ask)}（${
                          row.spreadPercent === null ? '点差标缺' : `${row.spreadPercent.toFixed(1)}%`
                        }）`}
                      {illiquid && <span className="ml-1 text-caption text-warning">流动性差</span>}
                    </td>
                    <td className="px-2 py-1 text-caption">
                      {band === null ? (
                        <span className="text-text-3">标缺</span>
                      ) : (
                        <Tooltip focusable content={band.evidence}>
                          <span className={band.warned ? 'text-warn-strong' : band.highlighted ? 'text-text-1' : 'text-text-3'}>
                            {band.label}
                            {band.warned ? ' · 你的历史最差档' : ''}
                            {band.highlighted ? ' ★' : ''}
                          </span>
                        </Tooltip>
                      )}
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-mono-xs text-text-1">
                      {sizing === null ? '标缺' : `${sizing.contracts} 张`}
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-mono-xs text-text-2">
                      {sizing === null ? '标缺' : formatUsd(sizing.feeUsd)}
                    </td>
                    <td className={`px-2 py-1 text-right font-mono text-mono-xs ${
                      band?.warned ? 'text-warn-strong' : 'text-text-2'
                    }`}
                    >
                      {sizing === null || sizing.feePercentOfPremium === null
                        ? '标缺'
                        : `${sizing.feePercentOfPremium.toFixed(2)}%`}
                    </td>
                    {/* 报价时点：bid/ask/点差是这一刻的观测，不是「现在」。 */}
                    <td className="px-2 py-1 text-right text-caption text-text-3">
                      {quoteTimeLabel(row.quoteAsOf)}
                    </td>
                  </tr>
                );
              }))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-1 text-caption text-text-3">
        可买张数 = ⌊标准仓位 ÷ (参考价×100)⌋ · 手续费估算 = 张数 × {formatUsd(ROUND_TURN_FEE_PER_CONTRACT_USD)}
        （你实测的每张往返，区间 $3.29–3.31）· 费/本金% 的本金＝张数×参考价×100，与历史「费用占权利金」同口径
        · 点差 &gt;{SPREAD_ILLIQUID_THRESHOLD_PERCENT}% 标「流动性差」
        · 报价以逐行 as-of 时点为准（缺时点＝标缺），不代表此刻盘口
        {item?.fetchedAt ? ` · 本次读取 ${quoteTimeLabel(item.fetchedAt)}` : ''}
      </p>
    </div>
  );
}

/**
 * 盘中计划：用户从下方实时扫描手动提升上来的标的，逐个做**机械核对**。
 *
 * 每张卡片给四件事，全部是核对而非建议：
 * 1. 今日车道（V2-E，来自服务端 `laneAvailability`）；
 * 2. 逐条规则状态（复用 `laneChecklist.ts` 的判定 + 扫描已有读数）；
 * 3. 今日车道对应 DTE 区间的合约候选，按**你自己的**价格分档标注
 *    （$2-8 甜蜜区高亮、<$1 灰显警示）并把标准仓位换成张数与手续费；
 * 4. 失效位提示（中性一行，不给任何进出场价位）。
 *
 * 清单按 ET 交易日作用域保存在本地，换日自动为空；「清空」随时可用。
 * 标准仓位只是本地输入（默认 $3,000），**不落盘、不猜账户规模**。
 */
export function IntradayPlanPanel({
  pulse,
  top,
  loading = false,
}: {
  pulse: IntradayPulseResponse | null;
  top: IntradayTopResponse | null;
  loading?: boolean;
}) {
  const tickers = useIntradayPlanStore(selectPlanTickers);
  const demote = useIntradayPlanStore((state) => state.demote);
  const clear = useIntradayPlanStore((state) => state.clear);
  const [sizeText, setSizeText] = useState(String(DEFAULT_STANDARD_SIZE_USD));

  const standardSizeUsd = useMemo(() => {
    const parsed = Number(sizeText.trim());
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }, [sizeText]);

  const views = useMemo(() => tickers.map((ticker) => evaluatePlanTicker({
    ticker,
    candidates: top?.candidates ?? [],
    laneAvailability: top?.laneAvailability ?? null,
    pulseGeneratedAt: pulse?.generatedAt ?? null,
    loading: loading && !top,
  })), [tickers, top, pulse, loading]);

  return (
    <section
      aria-label="盘中计划"
      className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-h3 font-semibold text-text-1">盘中计划</h2>
          {/* 界面披露策略：定位句 + 样本口径边界原文收进这枚 ⓘ，不占正文。 */}
          <InfoHint text={PLAN_HONESTY_NOTE} label="盘中计划口径" />
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-caption text-text-3">
            标准仓位 $
            <input
              type="number"
              min={0}
              step={100}
              inputMode="numeric"
              value={sizeText}
              onChange={(event) => setSizeText(event.target.value)}
              aria-label="标准仓位（美元）"
              className="w-24 rounded-ds-sm border border-subtle bg-bg-0 px-1.5 py-0.5 font-mono text-mono-xs text-text-1"
            />
          </label>
          {tickers.length > 0 && (
            <button
              type="button"
              onClick={() => clear()}
              className="rounded-ds-sm border border-subtle px-2 py-0.5 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
              aria-label="清空盘中计划"
            >
              清空
            </button>
          )}
        </div>
      </header>

      {tickers.length === 0 ? (
        <p className="px-4 py-4 text-body-sm text-text-3">
          还没有加入盘中计划的标的。在下方「实时扫描」里点某一行的「加入盘中计划」，
          它会被提升到这里做逐条核对，并保证进入服务端深度扫描层（最多 {INTRADAY_PLAN_MAX_TICKERS} 个）。
          清单按 ET 交易日作用域保存在本机，换一个交易日自动为空。
        </p>
      ) : (
        <ul>
          {views.map((view) => (
            <li
              key={view.ticker}
              data-plan-ticker={view.ticker}
              className="border-b border-subtle px-4 py-3 last:border-b-0"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="font-mono text-mono-sm font-semibold text-text-1">
                    {view.ticker}
                  </span>
                  <Tooltip focusable contentClassName="whitespace-pre-line" content={view.dayType.tooltip}>
                    <span
                      data-plan-day-type={view.dayType.state}
                      className={`text-caption ${
                        view.lane === 'overnight' ? 'text-warn-strong' : 'text-text-2'
                      }`}
                    >
                      今日车道：
                      {view.lane === 'intraday'
                        ? '日内可用（V2-E）'
                        : view.lane === 'overnight'
                          ? '过夜日 · 日内关闭（V2-E）'
                          : view.dayType.state === 'loading'
                            ? '读取中…'
                            : '标缺（V2-E）'}
                    </span>
                  </Tooltip>
                  {!view.inDeepLane && (
                    <span className="rounded-ds-sm border border-dashed border-subtle px-1.5 py-0.5 text-caption text-text-3">
                      未在今日深度层 · 扫描读数逐条标缺
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => demote(view.ticker)}
                  className="flex items-center gap-1 rounded-ds-sm border border-subtle px-1.5 py-0.5 text-caption text-text-3 hover:bg-bg-2 hover:text-text-1"
                  aria-label={`从盘中计划移除 ${view.ticker}`}
                >
                  <X size={11} />
                  移除
                </button>
              </div>

              <ul className="mt-2 space-y-1" aria-label={`${view.ticker} 逐条核对`}>
                {view.checks.map((check) => (
                  <CheckRow key={check.id} check={check} />
                ))}
              </ul>

              {view.legs.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-baseline gap-1">
                  <span className="text-caption text-text-3">今日波段</span>
                  {view.legs.map((leg) => (
                    <span
                      key={`${leg.startEt}-${leg.endEt}`}
                      className="inline-block rounded-ds-sm border border-subtle px-1 text-caption text-text-2"
                    >
                      {leg.startEt}
                      {leg.direction === 'up' ? '↑' : leg.direction === 'down' ? '↓' : '→'}
                      {leg.grade === 'strong' ? ' 暴动' : leg.grade === 'medium' ? ' 推升' : ''}
                    </span>
                  ))}
                </div>
              )}

              <PlanContracts
                ticker={view.ticker}
                minDte={view.contractMinDte}
                maxDte={view.contractMaxDte}
                windowMode={view.contractWindowMode}
                dteLabel={view.contractDteLabel}
                standardSizeUsd={standardSizeUsd}
              />

              <p className="mt-2 text-caption text-text-2">
                {INVALIDATION_PROMPT}
                {view.invalidationReference && (
                  <span className="text-text-3">{` · 今日已有观测：${view.invalidationReference}`}</span>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default IntradayPlanPanel;
