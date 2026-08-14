import { Fragment, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUp } from 'lucide-react';
import type {
  IntradaySetupMatch,
  IntradayTopCandidate,
  IntradayTopResponse,
} from '../../types/opportunities';
import type { PersonalEdgeUnderlyingStat } from '../../types/journal';
import { usePersonalEdge, type PersonalEdgeView } from '../../hooks/usePersonalEdge';
import {
  INTRADAY_PLAN_MAX_TICKERS,
  selectPlanTickers,
  useIntradayPlanStore,
} from '../../stores/intradayPlanStore';
import { parseApiTimestamp } from '../../utils/marketTime';
import { Tooltip } from '../common/Tooltip';
import {
  formatCompactUsd,
  formatRatio,
  formatSignedAtr,
  formatSignedCompactUsd,
  formatSignedPercent,
} from './intradayFormat';
import { NearExpiryContractPanel } from './NearExpiryContractPanel';

const STATE_LABELS: Record<IntradayTopCandidate['researchState'], string> = {
  active: '盘中活跃',
  watch: '观察',
  insufficient: '数据不足',
};

const STATE_STYLES: Record<IntradayTopCandidate['researchState'], string> = {
  active: 'text-text-1',
  watch: 'text-text-2',
  insufficient: 'text-warning',
};

const SENTIMENT_LABELS: Record<string, string> = {
  bullish: '偏多',
  bearish: '偏空',
  neutral: '中性',
  mixed: '多空分歧',
  unknown: '未知',
};

const VWAP_LABELS: Record<IntradayTopCandidate['vwapPosition'], string> = {
  above: 'VWAP 上方',
  below: 'VWAP 下方',
  flat: 'VWAP 持平',
  unknown: '标缺',
};

/** v3 大盘对齐：顺势/逆势/标缺——仅作标注，不隐藏行、不改变排序。 */
const ALIGNMENT_LABELS: Record<IntradayTopCandidate['marketAlignment']['state'], string> = {
  aligned: '顺势',
  against: '逆势',
  unknown: '标缺',
};

/** v3 速度分级：加速↑/减速↓/持平/标缺（5m 窗口近似，减速＝用户 R1 离场提示）。 */
const SPEED_LABELS: Record<IntradayTopCandidate['sessionBursts']['speed']['state'], string> = {
  accelerating: '加速↑',
  decelerating: '减速↓',
  flat: '持平',
  unknown: '标缺',
};

const SPEED_STYLES: Record<IntradayTopCandidate['sessionBursts']['speed']['state'], string> = {
  accelerating: 'text-up-strong',
  decelerating: 'text-down-strong',
  flat: 'text-text-2',
  unknown: 'text-text-3',
};

/** v5 波段分级 chip 文案：强＝爆发分 ≥8 暴动、中＝≥2.5 持续推升。 */
const GRADE_LABELS: Record<'strong' | 'medium', string> = {
  strong: '强',
  medium: '中',
};

const GRADE_DETAIL_LABELS: Record<'strong' | 'medium', string> = {
  strong: '强波段',
  medium: '中波段',
};

/** 强波段＝实底警示底色 chip；中波段/未分级＝描边 chip。 */
/**
 * v7 哑火形态标记：只在命中时渲染一枚 warning-muted 小徽标（挂在既有
 * 「当前爆发」单元格里，不新增列）。not_flagged / unavailable 一律**不渲染**——
 * 缺席不是结论，而表格已经足够密。
 */
const FIZZLE_CHIP_CLASS =
  'mt-0.5 inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning';

const FIZZLE_CAVEAT =
  '这是形态描述与历史频率，不是卖出信号；方向本身在样本中约 53%，与掷硬币无实质差别。';

function formatPercentRate(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : '标缺';
}

/** 哑火形态 tooltip：口径 + 样本外/样本内频率 + 基准 + 诚实边界。 */
function fizzleTooltip(flag: NonNullable<IntradayTopCandidate['fizzleFlag']>): string {
  const reference = flag.reference;
  const efficiency =
    typeof flag.efficiency === 'number' ? flag.efficiency.toFixed(2) : '标缺';
  const volNorm = typeof flag.volNorm === 'number' ? flag.volNorm.toFixed(2) : '标缺';
  return [
    `哑火形态：当前 15 分钟窗口效率 ${efficiency}（≥0.9，|收−开| ÷ 窗口高低差，`
    + `几乎没有回撤）+ 量比 ${volNorm}（<2.0，量能平平）+ 盘中段（非开盘回退基准段）。`,
    `这类窗口在 30 分钟内达到 ≥0.5 ATR 有利位移的比例：样本内（2026-02→05）`
    + `${formatPercentRate(reference.inSampleRate)}（n=${reference.nIn}）、`
    + `样本外（2026-06→08）${formatPercentRate(reference.outOfSampleRate)}（n=${reference.nOut}），`
    + `同期基准 ${formatPercentRate(reference.baseRateIn)} / ${formatPercentRate(reference.baseRateOut)}。`,
    `样本：${reference.sample}（21 个标的 × 123 个交易时段，你自己的 5m K 线逐根重放、无未来函数）；`
    + '方向一致性 20/20 个标的、6/6 个月、三把 ATR 标尺同号。',
    '读法：一段几乎不回撤、量能却平平的干净推升——最像「真速度」的形态，恰恰是这份样本里最常哑火的。',
    FIZZLE_CAVEAT,
  ].join('\n');
}

/**
 * 命中才渲染的哑火徽标；未命中 / 标缺一律返回 null（不占位、不解释）。
 * 落在既有「当前爆发」单元格内——本版**不新增列**。
 */
function fizzleMarker(item: IntradayTopCandidate) {
  const flag = item.fizzleFlag;
  if (!flag || flag.state !== 'flagged') return null;
  const tooltip = fizzleTooltip(flag);
  return (
    <Tooltip focusable content={<span className="whitespace-pre-line">{tooltip}</span>}>
      <span aria-label={tooltip} className={FIZZLE_CHIP_CLASS}>
        哑火形态
      </span>
    </Tooltip>
  );
}

const LEG_CHIP_STRONG_CLASS =
  'inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning';
const LEG_CHIP_DEFAULT_CLASS =
  'inline-block whitespace-nowrap rounded-ds-sm border border-subtle px-1.5 py-0.5 text-caption text-text-2';

export type IntradaySortKey = 'burst' | 'change';

interface SortState {
  key: IntradaySortKey;
  direction: 'desc' | 'asc';
}

function formatPrice(value: number | null): string {
  if (value === null) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function quoteAsOfLabel(item: IntradayTopCandidate): string {
  const fromQuote = item.quoteAsOf?.match(/\d{2}:\d{2}(?::\d{2})?/)?.[0];
  if (fromQuote) return `${fromQuote} ET`;
  const parsed = parseApiTimestamp(item.fetchedAt);
  if (!parsed) return '时点未报告';
  return `${new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(parsed)} ET`;
}

function sortValue(
  item: IntradayTopCandidate,
  key: IntradaySortKey,
  premarketPhase: boolean,
): number | null {
  switch (key) {
    case 'burst':
      return item.sessionBursts.state === 'ready'
        ? item.sessionBursts.current?.score ?? null
        : null;
    case 'change':
      // 盘前时段列头显示的是盘前涨跌（G-12：sessionChangePercent 此刻仍是
      // 上一常规时段读数），排序必须跟着显示口径走；盘前标缺行按 null 语义
      // 恒排最后，绝不用昨日涨跌冒充盘前排序键。
      return premarketPhase
        ? item.preChangePercent ?? null
        : item.sessionChangePercent;
    default:
      return null;
  }
}

const DIRECTION_ARROWS: Record<'up' | 'down' | 'flat', string> = {
  up: '↑',
  down: '↓',
  flat: '·',
};

/** 盘前闸门口径标注：常规快照字段在盘前仍指向上一常规时段，须如实声明。 */
const PREMARKET_GATE_BASIS = 'premarket_pre_price_change_then_pre_turnover_v1';
const PREMARKET_GATE_CAPTION = '盘前异动排序（盘前价 vs 前收 · 盘前成交额次序）';
const PREMARKET_FIELDS_UNAVAILABLE_WARNING =
  'premarket_fields_unavailable_ranking_reflects_prior_session';
const PREMARKET_FALLBACK_CHIP = '盘前字段不可用 · 当前排序反映上一常规时段';

/**
 * 闸门 v2（2026-08-03 普涨跳空日校准）：常规时段深度名额拆两档——约 2/3 按
 * 最近 15 分钟动量（谁现在在动）、其余按当日涨跌兜底（谁今天最大）。动量
 * 历史冷启动（服务重启/开盘）时服务端显式回退当日涨跌口径并携带警示——
 * 前端必须原样透出，绝不静默假装在按动量排序。
 */
const MOMENTUM_GATE_BASIS = 'momentum15m_then_day_change_v2';
const MOMENTUM_GATE_CAPTION = '15分动量优先（谁现在在动）· 当日涨跌兜底';
const MOMENTUM_WARMING_UP_WARNING =
  'momentum_history_warming_up_ranking_by_day_change';
const MOMENTUM_WARMING_UP_CHIP = '动量样本预热中 · 暂按当日涨跌排序';

/** 仅快照次级列表默认只展示前 N 檔，其余一键展开（重排可见性，不删除数据）。 */
const SNAPSHOT_ONLY_COLLAPSED_COUNT = 5;

/** 「你的战绩」亏损警示门槛：净亏损且样本 ≥20 笔才加警示（小样本不警示）。 */
const PERSONAL_LOSS_WARN_MIN_N = 20;

const PERSONAL_HEADER_TOOLTIP =
  '你的战绩＝Journal 当前默认 build 已平仓回合按标的聚合（净盈亏 · 胜率 · 笔数）——'
  + '系统标注，用户过滤：不隐藏行、不改排序、不是信号。样本 <5 笔显示「样本不足」，'
  + '净亏损且 ≥20 笔加警示。持仓时长与结果存在内生性（止损单天然短），'
  + '描述统计不是因果结论，不构成建议。';

/** 「你的战绩」单元格判定结果：显式区分加载中/标缺/样本不足/有读数。 */
type PersonalCellState =
  | { kind: 'loading' }
  | { kind: 'missing' }
  | { kind: 'small_sample'; minN: number }
  | { kind: 'ready'; stat: PersonalEdgeUnderlyingStat; warn: boolean };

function personalCellState(
  view: PersonalEdgeView,
  ticker: string,
): PersonalCellState {
  if (view.state === 'loading') return { kind: 'loading' };
  if (view.state === 'unavailable') return { kind: 'missing' };
  const stat = view.data.underlyings.find(
    (item) => item.underlying === ticker.toUpperCase(),
  );
  if (!stat) {
    return { kind: 'small_sample', minN: view.data.underlyingMinEpisodeCount };
  }
  return {
    kind: 'ready',
    stat,
    warn: stat.net < 0 && stat.n >= PERSONAL_LOSS_WARN_MIN_N,
  };
}

function personalWinRateLabel(stat: PersonalEdgeUnderlyingStat): string {
  return `${(stat.winRate * 100).toFixed(1)}%`;
}

function personalWarnTooltip(stat: PersonalEdgeUnderlyingStat): string {
  return `你的历史亏钱标的 · ${stat.n} 笔 · 净 ${formatSignedCompactUsd(stat.net)}`
    + ` · 胜率 ${personalWinRateLabel(stat)}`;
}

/** 「你的战绩」列内容：标缺/样本不足绝不冒充读数，亏损 ≥20 笔加警示 tooltip。 */
function personalStatCell(view: PersonalEdgeView, ticker: string) {
  const cell = personalCellState(view, ticker);
  if (cell.kind === 'loading') {
    return <div className="text-body-sm text-text-3">…</div>;
  }
  if (cell.kind === 'missing') {
    return (
      <>
        <div className="text-body-sm text-text-3">标缺</div>
        <div className="mt-0.5 text-caption text-text-3">个人战绩不可得</div>
      </>
    );
  }
  if (cell.kind === 'small_sample') {
    return (
      <>
        <div className="text-body-sm text-text-3">样本不足</div>
        <div className="mt-0.5 text-caption text-text-3">{`<${cell.minN} 笔`}</div>
      </>
    );
  }
  const { stat, warn } = cell;
  const summary = `${formatSignedCompactUsd(stat.net)} · ${personalWinRateLabel(stat)}`;
  if (warn) {
    return (
      <Tooltip focusable content={personalWarnTooltip(stat)}>
        <span aria-label={personalWarnTooltip(stat)} className="inline-block">
          <span className="inline-block rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 font-mono text-mono-xs font-medium text-warning">
            {summary}
          </span>
          <span className="mt-0.5 block text-caption text-text-3">{stat.n} 笔</span>
        </span>
      </Tooltip>
    );
  }
  return (
    <>
      <div className="font-mono text-mono-xs text-text-1">{summary}</div>
      <div className="mt-0.5 text-caption text-text-3">{stat.n} 笔</div>
    </>
  );
}

/** 今日曾深扫账本行的紧凑「你的战绩」标注（同一判定，行内 chip 形态）。 */
function personalLedgerChip(view: PersonalEdgeView, ticker: string) {
  const cell = personalCellState(view, ticker);
  if (cell.kind === 'loading') return null;
  if (cell.kind === 'missing') {
    return <span className="text-caption text-text-3">你的战绩标缺</span>;
  }
  if (cell.kind === 'small_sample') {
    return <span className="text-caption text-text-3">你的战绩样本不足</span>;
  }
  const { stat, warn } = cell;
  const label = `你的战绩 ${formatSignedCompactUsd(stat.net)}`
    + ` · ${personalWinRateLabel(stat)} · ${stat.n} 笔`;
  if (warn) {
    return (
      <Tooltip focusable content={personalWarnTooltip(stat)}>
        <span
          aria-label={personalWarnTooltip(stat)}
          className="inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning"
        >
          {label}
        </span>
      </Tooltip>
    );
  }
  return <span className="text-caption text-text-2">{label}</span>;
}

const RANK_HEADER_TOOLTIP =
  '服务端排名（盘中爆发分优先，休市按最近交易时段证据计数）；点击其他列头做客户端排序时，此排名不变。';

const DISPLACEMENT_HEADER_TOOLTIP =
  '近 30 分钟（6×5m）净位移 ÷ ATR。【更正】0.5 这条线原本来自按持仓时长分组的分层，'
  + '而持仓时长由结果决定——那个分层按构造就是循环的；改成只从第 15 分钟检查点向前计分后，'
  + '「速度未死 vs 已死」的差距从 37.6 个百分点塌到 0.1 个百分点（49.9% vs 49.8%，n=3,492）。'
  + '所以这一列只描述过去 30 分钟已经发生的事，不含任何前向信息，0.5 只是参考刻度、'
  + '不是「越过就能活下来」；基准：ATR14 日线 / 盘中代理，逐行 tooltip 标注实际使用的一种。'
  + '不隐藏行、不参与排序、不是信号。';

const ALIGNMENT_HEADER_TOOLTIP =
  '顺势/逆势＝候选当前波段（爆发）方向 vs SPY 会话 VWAP 位置，不是个股自身涨跌方向：'
  + '上涨股在 SPY 处于 VWAP 下方时同样标「逆势」。任一侧标缺时显示标缺。';

/**
 * 完整口径原文（逐字保留）：默认收进「完整口径」切换——隐藏 ≠ 删除，
 * 文本本身必须可一键展开查看。
 */
const FULL_METHODOLOGY_TEXT =
  '当前爆发＝最近 15 分钟（3 根 5m K 线）|收−开| ÷ 当日 5m 波幅中位 × 窗口量比（阈值按 2026-07-31 标注样本校准，盘中排序优先，不是信号）；速度＝相邻两个 15 分钟窗口爆发分之差（5m 近似，非 1m/2m 秒级；减速=你的离场信号，R1）；今日波段＝爆发分分级记录的独立窗口（强 ≥8 按 2026-07-31 暴动样本校准、中 ≥2.5 按 2026-08-03 NVDA 上午持续推升校准；起点相隔 ≥30 分钟，≤4 个，休市显示最近一个交易时段）；缺口＝开盘价对参考前收（休市时段改用快照前收并标注）；量能节奏＝当日累计 vs 20 日全日中位（未按时点折算）；大盘＝候选爆发方向 vs SPY 会话 VWAP 位置（累计额/量近似）；波幅扩张＝当日高低价差 ÷ ATR14；财报＝Finnhub 前向 5 天窗口，≤3 天标「期权贵」（你的回避规则）；期权异动＝最近一页 Moomoo 分类计数，不推断开平仓。你的战绩＝Journal 当前默认 build 已平仓回合按标的聚合（净盈亏/胜率/笔数，<5 笔样本不足，净亏损且 ≥20 笔警示；持仓时长与结果存在内生性，描述非因果、不构成建议）。时段/财报/大盘/速度/你的战绩均为上下文标注——系统标注，用户过滤：不隐藏行、不阻断操作、不参与排序。形态＝styleMatch v1（S1 低点抬高 / S2 跳空托举 / S3 高开遇阻，与你的 Playbook setup 的形状对比；「· 似」=部分相似，缺 K 线或快照输入时标缺）：形态相似度为 v1 几何检测（5m近似），不含你的进场确认帧（2m/1m 回踩8/13EMA），不是信号。近30分位移＝最近 30 分钟（6 根 5m K 线）相对「30 分钟前价格」（窗口首根开盘）的净位移 ÷ ATR 标尺，同时给出区间最高/最低偏移；ATR 标尺优先日线 ATR14（与取证分析同源），缺失时先回退「上一批交易时段真实波幅均值」（日线量级、当日内恒定），连一个上一时段都没有时才落回旧盘中代理（最近 20 根 5m 波幅均值 ×3）；每行标注实际基准与可比性——旧代理与真实日线 ATR14 之比在盘中 0.28→0.42→0.20 漂移，因此标了「不可比」的行不要与走 ATR14 的行横向比较，全都不可得时显式标缺。0.5 ATR 这条线来自你自己 766 笔期权回合（2026-06-08→07-31，Journal build #3）的取证分析：进场几何（追高 vs 回调）对结果没有预测力，而按持仓时长分组后 30 分钟位移把结果分得很开——速死亏损单（持仓<30分钟）前向 MFE 中位 0.18 ATR / MAE −0.49 ATR，仅 18.3% 达到 ≥0.5 ATR；走出来的赢家（30分钟-3小时）MFE 0.69 / MAE −0.13，71.2% 达到 ≥0.5 ATR；该样本 84% 的合约 ≤1DTE，对「不动」零容忍。【v8 更正——这一段必须连着上一段读】上面那个 18.3% vs 71.2% 的分层**按构造就是循环的**：分组变量是持仓时长，而持仓时长本身由结果决定，等于拿结果去解释结果。2026-08 的 2m 回放研究（3,492 次 EMA8/13 回踩持稳进场，21 标的 × 123 个交易时段）用同一套机器量化了这份循环性：按同样的循环方式记分（用整段 30 分钟窗口内速度是否还活着，去判这同一段 30 分钟的结果），速度未死均值 +0.071 / P(>0) 54.4%、速度已死 −0.476 / 16.8%，鸿沟 37.6 个百分点；改成只从第 15 分钟检查点**向前**计分（即进场当时真能知道的信息），只剩 +0.005 / 49.9% vs −0.013 / 49.8%——**0.1 个百分点**；而且样本内那点微弱的检查点效应**样本外直接反号**（样本内「第 5 分钟已走 ≥0.3 ATR」→ 其后 +0.122；样本外同一条件 → −0.128）。结论：肉眼可见的那道鸿沟几乎全部是循环性，**这一列不含任何前向信息**，0.5 只是参考刻度，不要读成「越过它就能活下来」。位移读数本身仍然保留，因为它是对「它现在到底有没有在动」的诚实描述。诚实边界：这是对已经发生的事的描述统计，不是预测、不是买卖信号；样本窗口恰是你最差的两个月，K 线为非官方 5m 聚合。缺失字段显式标缺，不以 0 冒充。「哑火形态」（当前爆发格内的小徽标，命中才出现）＝当前 15 分钟窗口效率 ≥0.9（几乎无回撤）+ 量比 <2.0（量能平平）+ 盘中段：2026-08 起速回放研究（9,173 次爆发起点、21 标的 × 123 个交易时段）里这类窗口 30 分钟内达到 ≥0.5 ATR 有利位移的比例只有 26.8%（样本内 n=291）/ 25.4%（样本外 n=177），基准 47.8%/50.5%。同一份研究的首要结论是否定的：起速那一刻**方向不可预测**（P(方向)=50.6%，19 个候选因子方向 AUC 0.48–0.52，MFE/|MAE| 中位 1.026），所以本表**没有**延续概率或真假速度评分；哑火形态是形态描述与历史频率，不是卖出信号。';

function formatBurstThrust(value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

function legsDetailLabel(item: IntradayTopCandidate): string | undefined {
  const bursts = item.sessionBursts;
  if (bursts.state !== 'ready' || bursts.legs.length === 0) return undefined;
  return `波段明细：${bursts.legs
    .map(
      (leg) =>
        `${leg.startEt}-${leg.endEt} ${DIRECTION_ARROWS[leg.direction]} `
        + `${formatBurstThrust(leg.thrustPercent)} · 爆发分 ${leg.score?.toFixed(1) ?? '—'}`
        + `${leg.grade ? `（${GRADE_DETAIL_LABELS[leg.grade]}）` : ''}`,
    )
    .join('；')}`;
}

function legsBasisCaption(item: IntradayTopCandidate): string {
  return item.sessionBursts.medianBasis === 'prior_session_fallback'
    ? '基准回退上一时段'
    : '≥30 分钟独立波段';
}

/**
 * 两层模式深度位徽标：计划钉选 / 用户钉选 / 盘中计划 / 异动排名
 * （闸门标注，非信号）。
 */
function deepLaneBadge(item: IntradayTopCandidate): string | null {
  const reason = item.deepLaneReason;
  if (!reason) return null;
  if (reason.promotedBy === 'plan_always_include') return '计划钉选';
  if (reason.promotedBy === 'user_pinned') return '钉选';
  if (reason.promotedBy === 'user_focus') return '盘中计划';
  return reason.moverRank !== null ? `异动 #${reason.moverRank}` : '异动晋升';
}

/** 账本 as-of 时点：ISO → ET HH:MM（与主表 quote as-of 同一时区口径）。 */
function ledgerAsOfLabel(iso: string): string {
  const parsed = parseApiTimestamp(iso);
  if (!parsed) return '时点未报告';
  return `${new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(parsed)} ET`;
}

function optionActivityLabel(item: IntradayTopCandidate): string {
  const activity = item.optionActivity;
  if (activity.state === 'not_configured') return '未配置';
  if (activity.state === 'unavailable') return '标缺';
  if (activity.count === 0) return '0 笔';
  const sentiment = SENTIMENT_LABELS[activity.dominantSentiment] ?? activity.dominantSentiment;
  return `${activity.count} 笔 · ${sentiment} · 最大单 ${formatCompactUsd(activity.maxSingleTurnover)}`;
}

/** v3 财报读数：回避窗内醒目标注「财报 N 天内 · 期权贵」；标缺绝不冒充安全。 */
function earningsLabel(item: IntradayTopCandidate): {
  text: string;
  emphasized: boolean;
  caption: string;
} {
  const proximity = item.earningsProximity;
  if (proximity.state !== 'ready') {
    return { text: '标缺', emphasized: false, caption: '财报日历不可得 · 未知≠安全' };
  }
  if (proximity.daysToEarnings === null) {
    return {
      text: `${proximity.windowDays} 天内无`,
      emphasized: false,
      caption: 'Finnhub 前向窗口',
    };
  }
  if (proximity.withinBlackout) {
    return {
      text: proximity.daysToEarnings === 0
        ? '今日财报 · 期权贵'
        : `财报 ${proximity.daysToEarnings} 天内 · 期权贵`,
      emphasized: true,
      caption: `${proximity.earningsDate ?? ''} · 你的回避规则（≤${proximity.blackoutDays} 天）`,
    };
  }
  return {
    text: `${proximity.daysToEarnings} 天后财报`,
    emphasized: false,
    caption: proximity.earningsDate ?? '',
  };
}

/**
 * 标的格次行的财报警示徽标：回避窗内＝实底警示 chip；日历标缺＝显式
 * 「财报标缺」（未知≠安全，绝不以无徽标冒充安全）；已知且窗外不占默认格，
 * 全文在展开行「研究读数」内一键可达。
 */
function earningsTickerBadge(item: IntradayTopCandidate) {
  const proximity = item.earningsProximity;
  if (proximity.state !== 'ready') {
    const caption = '财报日历不可得 · 未知≠安全';
    return (
      <Tooltip focusable content={caption}>
        <span
          aria-label={caption}
          className="inline-block whitespace-nowrap rounded-ds-sm border border-dashed border-subtle px-1.5 py-0.5 text-caption text-text-3"
        >
          财报标缺
        </span>
      </Tooltip>
    );
  }
  if (!proximity.withinBlackout) return null;
  const earnings = earningsLabel(item);
  return (
    <Tooltip focusable content={earnings.caption}>
      <span
        aria-label={`${earnings.text}：${earnings.caption}`}
        className="inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning"
      >
        {earnings.text}
      </span>
    </Tooltip>
  );
}

const ALIGNMENT_DIRECTION_LABELS: Record<string, string> = {
  up: '爆发↑',
  down: '爆发↓',
  flat: '爆发·',
};

const ALIGNMENT_SPY_LABELS: Record<string, string> = {
  above: 'SPY VWAP上',
  below: 'SPY VWAP下',
  flat: 'SPY VWAP平',
};

function alignmentCaption(item: IntradayTopCandidate): string {
  const alignment = item.marketAlignment;
  if (alignment.state === 'unknown') return 'SPY 或爆发方向标缺';
  return `${ALIGNMENT_DIRECTION_LABELS[alignment.burstDirection ?? ''] ?? ''} · ${
    ALIGNMENT_SPY_LABELS[alignment.spyVwapPosition ?? ''] ?? ''
  }`;
}

function speedCaption(item: IntradayTopCandidate): string {
  const speed = item.sessionBursts.speed;
  if (speed.state === 'unknown' || speed.delta === null) return '相邻 15 分钟窗口';
  const sign = speed.delta > 0 ? '+' : speed.delta < 0 ? '−' : '';
  return `Δ ${sign}${Math.abs(speed.delta).toFixed(1)}`;
}

/**
 * v6 近 30 分钟位移：ATR 标尺文案。缺席时如实写「标缺」，不假装知道用了哪把尺。
 */
const DISPLACEMENT_BASIS_LABELS: Record<string, string> = {
  atr14_daily: 'ATR14 日线',
  prior_sessions_true_range_mean: '上一批交易时段真实波幅均值（日线量级，当日内恒定）',
  intraday_20bar_proxy_x3: '盘中代理（最近 20 根 5m 波幅均值 ×3）',
};

/** v7：这一行的 ATR 读数能不能和走 ATR14 的行放一起比——直说。 */
const DISPLACEMENT_COMPARABILITY_LABELS: Record<string, string> = {
  daily_atr14: '可与其它 ATR14 行横向比较',
  daily_scale_prior_sessions_approximate:
    '日线量级、可近似比较（仅 ≤3 个时段，比 ATR14 噪声大）',
  intraday_scale_not_comparable:
    '盘中量级，不可与 ATR14 行或跨时点比较（该代理与真实日线 ATR14 之比在盘中 0.28→0.42→0.20 漂移）',
};

/**
 * 位移列 tooltip：**先说更正**，再说口径与实际使用的 ATR 基准。
 *
 * v8：0.5 这条线原本来自按持仓时长分组的分层，而持仓时长由结果决定——那个
 * 分层按构造就是循环的。2m 回放研究（n=3,492）把循环性量化了：循环记分下
 * 「速度未死 vs 已死」差 37.6pp，只从第 15 分钟检查点向前计分后只剩 0.1pp。
 * 因此这一列只是**对过去 30 分钟的描述**，不含前向信息，文案里不得再出现
 * 「活下来 / 存活」这类框架。
 */
function displacementTooltip(item: IntradayTopCandidate): string {
  const displacement = item.recentDisplacement;
  const minutes = displacement?.windowMinutes ?? 30;
  const line = displacement?.survivalLineAtr ?? 0.5;
  const basis = displacement?.atrBasis
    ? DISPLACEMENT_BASIS_LABELS[displacement.atrBasis] ?? displacement.atrBasis
    : '标缺（ATR14 日线 / 上一时段波幅 / 盘中代理均不可得）';
  const parts = [
    `近 ${minutes} 分钟（6×5m）净位移 ÷ ATR。【更正】${line} 这条线原本来自按持仓时长`
    + '分组的分层，而持仓时长由结果决定——那个分层按构造就是循环的；只从第 15 分钟'
    + '检查点向前计分后，「速度未死 vs 已死」的差距从 37.6 个百分点塌到 0.1 个百分点'
    + '（49.9% vs 49.8%，n=3,492），样本内的微弱效应还样本外反号。'
    + `所以这一列只描述过去 ${minutes} 分钟已经发生的事，不含任何前向信息；`
    + `${line} 只是参考刻度，不是「越过就能活下来」。基准：`
    + basis,
  ];
  // v7：不同标尺的读数不能混着比——把这行读数的可比性直接说出来。
  if (displacement?.atrScaleComparability) {
    const comparability =
      DISPLACEMENT_COMPARABILITY_LABELS[displacement.atrScaleComparability]
      ?? displacement.atrScaleComparability;
    const sessions = displacement.atrPriorSessionCount
      ? `（取 ${displacement.atrPriorSessionCount} 个已结束时段）`
      : '';
    parts.push(`标尺可比性：${comparability}${sessions}。`);
  }
  if (displacement?.state === 'ready') {
    parts.push(
      `区间：最高 ${formatSignedAtr(displacement.highExcursionAtr)}`
      + ` · 最低 ${formatSignedAtr(displacement.lowExcursionAtr)}`
      + ` · 幅度 ${formatSignedAtr(displacement.absRangeAtr)}`
      + `（${displacement.barCount} 根 5m K 线）`,
    );
    // 断档诚实化：窗口不连续时如实标注实际跨度，不冒充「近 30 分钟」。
    if (
      typeof displacement.windowSpanMinutes === 'number'
      && displacement.windowSpanMinutes > minutes
    ) {
      parts.push(
        `断档提示：最近 6 根 5m K 线不连续（停牌/缺 K 线），实际跨 `
        + `${displacement.windowSpanMinutes} 分钟——「近 ${minutes} 分钟」按此口径读。`,
      );
    }
  } else if (displacement?.state === 'insufficient_bars') {
    parts.push(`当前时段仅 ${displacement.barCount} 根 5m K 线，不足 6 根（30 分钟）。`);
  } else {
    parts.push(
      `读数不可得：${displacement?.unavailableReason ?? '载荷未提供 recent_displacement'}。`,
    );
  }
  return parts.join('\n');
}

/**
 * 位移单元格：|净位移| ≥ 0.5 ATR 用涨跌色强调，之间用弱化色 +「不足 0.5 ATR」
 * 说明，K 线不足或标尺不可得显式「标缺」。三态都不隐藏行、不参与排序。
 *
 * v8：弱化态文案从「未达 0.5」改成纯描述的「不足 0.5 ATR」——「未达」隐含
 * 「本该达到 / 达到才算数」的存活框架，而 0.5 这条线的循环性已被量化推翻
 * （见 displacementTooltip）。数字本身保留：它仍是对过去 30 分钟的诚实描述。
 */
function displacementCell(item: IntradayTopCandidate) {
  const displacement = item.recentDisplacement;
  const tooltip = displacementTooltip(item);
  if (!displacement || displacement.state !== 'ready' || displacement.netMoveAtr === null) {
    const caption = displacement?.state === 'insufficient_bars'
      ? 'K线不足 30 分钟'
      : 'ATR 标尺/K线不可得';
    return (
      <Tooltip focusable content={<span className="whitespace-pre-line">{tooltip}</span>}>
        <span aria-label={tooltip} className="inline-block">
          <span className="block font-mono text-mono-sm text-text-3">标缺</span>
          <span className="mt-0.5 block text-caption text-text-3">{caption}</span>
        </span>
      </Tooltip>
    );
  }
  const net = displacement.netMoveAtr;
  const line = displacement.survivalLineAtr;
  const reached = Math.abs(net) >= line;
  const valueClass = !reached
    ? 'text-text-3'
    : net > 0
      ? 'text-up-strong font-medium'
      : 'text-down-strong font-medium';
  // ATR 是抽象刻度，盘面上看不见——同时给出可直接对照的美元与百分比。
  const atr = displacement.atrBasis === 'atr14_daily' ? item.atr14 : null;
  const price = item.lastPrice;
  const moneyMove = atr !== null && atr > 0 ? net * atr : null;
  const moneyNeeded = atr !== null && atr > 0 ? line * atr : null;
  const pctNeeded =
    moneyNeeded !== null && price !== null && price > 0
      ? (moneyNeeded / price) * 100
      : null;
  const money = (value: number) =>
    `${value < 0 ? '−' : '+'}$${Math.abs(value).toFixed(2)}`;
  // 断档窗口：次行优先提示实际跨度（比高低偏移更重要的口径事实）。
  const gapSpan =
    typeof displacement.windowSpanMinutes === 'number'
    && displacement.windowSpanMinutes > displacement.windowMinutes
      ? displacement.windowSpanMinutes
      : null;
  const caption = gapSpan !== null
    ? `含断档 · 跨 ${gapSpan} 分钟`
    : reached
      ? `高 ${formatSignedAtr(displacement.highExcursionAtr)} · 低 ${formatSignedAtr(
        displacement.lowExcursionAtr,
      )}`
      : moneyNeeded !== null
        ? `需 $${moneyNeeded.toFixed(2)}${pctNeeded !== null ? `（${pctNeeded.toFixed(1)}%）` : ''}`
        : `不足 ${line} ATR`;
  return (
    <Tooltip focusable content={<span className="whitespace-pre-line">{tooltip}</span>}>
      <span aria-label={tooltip} className="inline-block">
        <span className={`block font-mono text-mono-sm ${valueClass}`}>
          {moneyMove !== null ? money(moneyMove) : formatSignedAtr(net)}
        </span>
        <span className="mt-0.5 block text-caption text-text-3">
          {moneyMove !== null ? `${formatSignedAtr(net)} · ${caption}` : caption}
        </span>
      </span>
    </Tooltip>
  );
}

function gapCaption(item: IntradayTopCandidate): string {
  if (item.gapPercent === null) return '标缺';
  if (item.gapAtrMultiple !== null) {
    return `${item.gapAtrMultiple.toFixed(2)}×ATR${
      item.gapBasis === 'session_open_vs_moomoo_snapshot_prev_close' ? ' · 快照前收' : ''
    }`;
  }
  return item.gapBasis === 'session_open_vs_moomoo_snapshot_prev_close'
    ? '对快照前收'
    : '对上一完整日收盘';
}

/**
 * v4 styleMatch：形态徽标 tooltip——matched 给证据行，partial 给未满足的原因，
 * 有 Playbook 对应时附一行只读标注（候选/已晋升）。
 */
function setupTooltip(setup: IntradaySetupMatch): string {
  const parts: string[] = [`${setup.label}（${setup.title}）：${setup.reason}`];
  if (setup.evidenceLines.length > 0) parts.push(setup.evidenceLines.join('；'));
  if (setup.playbook) {
    parts.push(
      `对应 Playbook: ${setup.playbook.setupKey}（${
        setup.playbook.status === 'promoted' ? '已晋升' : '候选'
      }）`,
    );
  }
  return parts.join('\n');
}

/** 形态列内容：matched=实底徽标、partial=描边徽标、无相似=—、不可评估=标缺。 */
function setupMatchCell(item: IntradayTopCandidate) {
  const profile = item.setupMatch;
  if (!profile || profile.state === 'unavailable') {
    return (
      <>
        <div className="text-body-sm text-text-3">标缺</div>
        <div className="mt-0.5 text-caption text-text-3">K线/快照输入不足</div>
      </>
    );
  }
  const visible = profile.setups.filter(
    (setup) => setup.state === 'matched' || setup.state === 'partial',
  );
  if (visible.length === 0) {
    return (
      <>
        <div className="text-body-sm text-text-3">—</div>
        <div className="mt-0.5 text-caption text-text-3">无相似形态 · 非信号</div>
      </>
    );
  }
  return (
    <>
      <div className="flex flex-wrap gap-1">
        {visible.map((setup) => (
          <Tooltip
            key={setup.setupKey}
            focusable
            content={
              <span className="whitespace-pre-line">{setupTooltip(setup)}</span>
            }
          >
            <span
              aria-label={setupTooltip(setup)}
              className={
                setup.state === 'matched'
                  ? 'inline-block whitespace-nowrap rounded-ds-sm border border-subtle bg-bg-2 px-1.5 py-0.5 text-caption font-medium text-text-1'
                  : 'inline-block whitespace-nowrap rounded-ds-sm border border-dashed border-subtle px-1.5 py-0.5 text-caption text-text-2'
              }
            >
              {setup.label}
              {setup.state === 'partial' ? ' · 似' : ''}
            </span>
          </Tooltip>
        ))}
      </div>
      <div className="mt-0.5 text-caption text-text-3">v1 几何 · 非信号</div>
    </>
  );
}

/**
 * 展开行「研究读数」网格：从默认网格移出的次要指标（波段vs大盘/量能节奏/缺口/
 * 波幅/VWAP/期权异动/财报全文/研究状态），一键可达——重排可见性，不删除任何
 * 读数；缺失字段照旧显式标缺，不以 0 冒充。
 *
 * v6 起「波段vs大盘」也在这里：默认网格让位给「近30分位移」（你自己 766 笔
 * 样本里唯一把结果分开的读数），大盘对齐仍是上下文标注，一键可达。
 */
function ResearchReadoutsGrid({ item }: { item: IntradayTopCandidate }) {
  const earnings = earningsLabel(item);
  return (
    <div aria-label={`${item.ticker} 研究读数`} className="mb-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-body-sm font-medium text-text-2">研究读数</span>
        <span className="text-caption text-text-3">
          次要指标 · 缺失显式标缺，不以 0 冒充
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4 lg:grid-cols-8">
        <div>
          <dt className="text-caption text-text-3">
            <Tooltip focusable content={ALIGNMENT_HEADER_TOOLTIP}>
              <span aria-label={ALIGNMENT_HEADER_TOOLTIP}>波段vs大盘</span>
            </Tooltip>
          </dt>
          <dd
            className={`mt-0.5 text-body-sm ${
              item.marketAlignment.state === 'unknown' ? 'text-text-3' : 'text-text-1'
            }`}
          >
            {ALIGNMENT_LABELS[item.marketAlignment.state]}
          </dd>
          <dd className="text-caption text-text-3">{alignmentCaption(item)}</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">量能节奏</dt>
          <dd className="mt-0.5 font-mono text-mono-xs text-text-1">
            {formatRatio(item.volumePaceRatio)}
          </dd>
          <dd className="text-caption text-text-3">vs 20日全日中位</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">缺口</dt>
          <dd className="mt-0.5 font-mono text-mono-xs text-text-1">
            {formatSignedPercent(item.gapPercent)}
          </dd>
          <dd className="text-caption text-text-3">{gapCaption(item)}</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">波幅扩张(ATR)</dt>
          <dd className="mt-0.5 font-mono text-mono-xs text-text-1">
            {formatRatio(item.atrRangeExpansion)}
          </dd>
          <dd className="text-caption text-text-3">当日高低 ÷ ATR14</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">VWAP</dt>
          <dd className="mt-0.5 text-body-sm text-text-1">{VWAP_LABELS[item.vwapPosition]}</dd>
          <dd className="text-caption text-text-3">
            {item.vwap !== null ? `≈ ${formatPrice(item.vwap)} · 额/量近似` : '额/量近似'}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">期权异动</dt>
          <dd
            className="mt-0.5 text-body-sm text-text-1"
            aria-label={
              item.optionActivity.limitations.length > 0
                ? `期权异动限制：${item.optionActivity.limitations.join('；')}`
                : undefined
            }
          >
            {optionActivityLabel(item)}
          </dd>
          <dd className="text-caption text-text-3">Moomoo 分类 · 不证明方向</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">财报</dt>
          <dd
            className={
              earnings.emphasized
                ? 'mt-0.5 inline-block rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning'
                : 'mt-0.5 text-body-sm text-text-2'
            }
          >
            {earnings.text}
          </dd>
          <dd className="text-caption text-text-3">{earnings.caption}</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">研究状态</dt>
          <dd className={`mt-0.5 text-body-sm font-medium ${STATE_STYLES[item.researchState]}`}>
            {STATE_LABELS[item.researchState]}
          </dd>
          <dd className="text-caption text-text-3">
            {item.supportingEvidenceCount} 项证据支持
          </dd>
        </div>
      </dl>
    </div>
  );
}

/**
 * 实时扫描表（现在谁在动）：确定性证据计数排名的盘中滚动 Top N。
 * 两层模式下深度层为实时排名主表，表下依次为「今日曾深扫 · 波段保留」账本
 * （轮换出深度层的标的以最后一次深扫摘要 as-of 保留）与「仅快照」次级列表。
 *
 * 两级布局（2026-08-03 声效整理，2026-08-04 加「你的战绩」与「近30分位移」）：
 * 默认网格保留 10 列交易关键读数（排名/标的/涨跌%/当前爆发/今日波段/速度/
 * 近30分位移/形态/详情/你的战绩），次要指标移入行内展开区「研究读数」网格
 * （临期合约面板上方）——重排可见性 ≠ 删除，所有读数一键可达，标缺语义不变。
 * v6 为给「近30分位移」腾出默认位，「波段vs大盘」下沉到研究读数区。
 * 2026-08-14 按用户要求把「你的战绩」移到最后一列（它是上下文标注，不是
 * 盘中交易关键读数），语义与列头 tooltip 不变。
 *
 * 盘前时段（sessionPhase=premarket）「涨跌%」列改示真实盘前变动（G-12：
 * sessionChangePercent 此刻仍指向上一常规时段，副行如实标「昨日」），
 * 盘前字段标缺显式「盘前标缺」；常规时段渲染不变。
 *
 * 「近30分位移」＝该标的最近 30 分钟（6×5m）的 ATR 归一化净位移。证据：用户
 * 自己 766 笔回合（2026-06-08→07-31）的取证分析显示进场几何无预测力，而进场后
 * 30 分钟位移把结果分得很开（速死单 18.3% 达到 ≥0.5 ATR，走出来的赢家 71.2%）。
 * 0.5 ATR 因此是**他自己样本里的经验线**：描述统计，非预测、非信号。
 *
 * 「你的战绩」＝个人画像回灌（personal-edge）：该标的在你 Journal 默认 build
 * 里的净盈亏/胜率/笔数。系统标注，用户过滤——不隐藏行、不改排序、不是信号；
 * 端点缺席显式标缺，样本 <5 笔显式「样本不足」。
 *
 * 行点击仍是既有的详情页导航（不改变肌肉记忆），行尾「详情」按钮在行
 * 下方展开研究读数 + 只读临期合约面板；同一时刻只展开一行。
 */
export function IntradayScanTable({
  data,
  loading,
  error,
}: {
  data: IntradayTopResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const navigate = useNavigate();
  // 个人画像回灌：每会话/2 分钟最多取一次；失败或未构建 → 显式标缺。
  const personalEdge = usePersonalEdge();
  // 盘中计划（按 ET 交易日作用域的本地清单）：行内一键提升到页面顶部的
  // 「盘中计划」区块。它走的是 additive 的 focus_symbols，**不进 symbols**，
  // 因此不会关掉服务端两层扫描。
  const planTickers = useIntradayPlanStore(selectPlanTickers);
  const promote = useIntradayPlanStore((state) => state.promote);
  const planFull = planTickers.length >= INTRADAY_PLAN_MAX_TICKERS;
  const [sort, setSort] = useState<SortState | null>(null);
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const [snapshotListExpanded, setSnapshotListExpanded] = useState(false);
  const [methodologyExpanded, setMethodologyExpanded] = useState(false);

  // 盘前口径：闸门/宽层排序按盘前字段；回退时携带显式警示，绝不静默。
  /**
   * 今日概览：达 0.5 ATR 的檔数与量比中位（描述统计）。分母＝**有位移读数**
   * 的檔数，不是整个深度层——缺读数的檔以 `missing` 计数如实标出，绝不把
   * 「未读」并进「未达」。中位数为标准中位（偶数个取中间两值平均）。
   */
  const liveSummary = useMemo(() => {
    const rows = data?.candidates ?? [];
    if (rows.length === 0) return null;
    const displacements = rows
      .map((row) => row.recentDisplacement)
      .filter((d) => d && d.state === 'ready' && d.netMoveAtr !== null);
    if (displacements.length === 0) return null;
    const reached = displacements.filter(
      (d) => Math.abs(d!.netMoveAtr as number) >= (d!.survivalLineAtr ?? 0.5),
    ).length;
    const vols = rows
      .map((row) => row.sessionBursts?.current?.volNorm)
      .filter((v): v is number => typeof v === 'number')
      .sort((a, b) => a - b);
    let medianVolNorm: number | null = null;
    if (vols.length > 0) {
      const mid = Math.floor(vols.length / 2);
      medianVolNorm = vols.length % 2 === 1
        ? vols[mid]
        : (vols[mid - 1] + vols[mid]) / 2;
    }
    return {
      total: displacements.length,
      missing: rows.length - displacements.length,
      reached,
      medianVolNorm,
    };
  }, [data]);

  const premarketBasis = data?.universeScan?.gateBasis === PREMARKET_GATE_BASIS;
  // 盘前时段（按盘段而非闸门口径判定）：深度行「涨跌%」主行改示真实盘前
  // 变动（G-12：sessionChangePercent 此刻仍指向上一常规时段，副行如实标
  // 「昨日」）；盘前字段标缺显式「盘前标缺」，绝不以昨日涨跌冒充。
  const premarketPhase = data?.sessionPhase === 'premarket';
  const premarketFieldsUnavailable = (data?.universeScan?.gateWarnings ?? []).includes(
    PREMARKET_FIELDS_UNAVAILABLE_WARNING,
  );
  // v2 动量口径与冷启动预热警示：服务端如实声明，前端原样透出。
  const momentumBasis = data?.universeScan?.gateBasis === MOMENTUM_GATE_BASIS;
  const momentumWarmingUp = (data?.universeScan?.gateWarnings ?? []).includes(
    MOMENTUM_WARMING_UP_WARNING,
  );
  // 今日曾深扫账本：轮换出深度层的标的以最后一次深扫摘要保留（as-of）。
  const dayLedgerRows = data?.universeScan?.dayLedger ?? [];

  // 排名列＝服务端排名（响应内顺序）；客户端排序只重排行，不改写此排名。
  const serverRankByTicker = useMemo(() => {
    const map = new Map<string, number>();
    (data?.candidates ?? []).forEach((item, index) => {
      map.set(item.ticker, index + 1);
    });
    return map;
  }, [data]);

  const rows = useMemo(() => {
    const candidates = data?.candidates ?? [];
    if (!sort) return candidates;
    const sorted = [...candidates].sort((left, right) => {
      const a = sortValue(left, sort.key, premarketPhase);
      const b = sortValue(right, sort.key, premarketPhase);
      if (a === null && b === null) return 0;
      if (a === null) return 1; // 标缺永远排最后，不参与方向。
      if (b === null) return -1;
      return sort.direction === 'desc' ? b - a : a - b;
    });
    return sorted;
  }, [data, sort, premarketPhase]);

  const toggleSort = (key: IntradaySortKey) => {
    setSort((current) => {
      if (!current || current.key !== key) return { key, direction: 'desc' };
      if (current.direction === 'desc') return { key, direction: 'asc' };
      return null; // 第三次点击回到服务端证据排名。
    });
  };

  const sortMark = (key: IntradaySortKey): string => {
    if (!sort || sort.key !== key) return '';
    return sort.direction === 'desc' ? ' ↓' : ' ↑';
  };

  const sortableHeader = (key: IntradaySortKey, label: string) => (
    <button
      type="button"
      onClick={() => toggleSort(key)}
      className="font-medium text-text-3 hover:text-text-1"
      aria-label={`按${label}排序`}
    >
      {label}
      {sortMark(key)}
    </button>
  );

  const snapshotRows = data?.universeScan?.snapshotOnly ?? [];
  const visibleSnapshotRows = snapshotListExpanded
    ? snapshotRows
    : snapshotRows.slice(0, SNAPSHOT_ONLY_COLLAPSED_COUNT);

  return (
    <section aria-label="实时扫描" className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-h3 font-semibold text-text-1">实时扫描 · 现在谁在动</h2>
          <span className="text-caption text-text-3">
            {data?.quoteSessionScope === 'latest_prior_session'
              ? '休市 · 按最近交易时段最强波段排序'
              : '波段爆发优先排名'}
            {/* 载荷没带版本就如实写「版本未声明」，绝不冒充某个具体版本。 */}
            （{data?.signalVersion ?? '版本未声明'}）· 不冻结 · 不入统计
          </span>
          {data?.universeScan && (
            <span className="text-caption text-text-3">
              深度层实时排名，下方为今日曾深扫账本
            </span>
          )}
        </div>
        {/* 一行看完「今天到底有没有货」：达标数 + 量比中位。全是描述统计，
            不预测方向——0.5 ATR 只是参考刻度（见位移列口径）。 */}
        {liveSummary && (
          <div
            data-testid="scan-live-summary"
            className="mt-1.5 text-body-sm text-text-2"
          >
            <span className={liveSummary.reached > 0 ? 'font-medium text-text-1' : ''}>
              {liveSummary.missing > 0
                ? `有位移读数的 ${liveSummary.total} 檔中 ${liveSummary.reached} 檔近30分位移达 0.5 ATR（另 ${liveSummary.missing} 檔标缺）`
                : `深度层 ${liveSummary.total} 檔中 ${liveSummary.reached} 檔近30分位移达 0.5 ATR`}
            </span>
            {liveSummary.medianVolNorm !== null && (
              <span className="text-text-3">
                {` · 量比中位 ${liveSummary.medianVolNorm.toFixed(2)}×`}
                {liveSummary.medianVolNorm < 1 ? '（比平时清淡）' : ''}
              </span>
            )}
          </div>
        )}
        {data && (
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-caption text-text-3">
            <span>
              {data.quoteSessionLabel}
              {data.universeScan
                ? ` · 全清单 ${data.universeScan.scannedTotal} 檔快照 · 深度分析 ${data.universeScan.deepLaneCount} 檔`
                : ` · 扫描 ${data.universe.length} 个标的`}
              {premarketBasis ? ` · ${PREMARKET_GATE_CAPTION}` : ''}
              {momentumBasis ? ` · ${MOMENTUM_GATE_CAPTION}` : ''}
              {data.unsupportedSymbols.length > 0
                ? ` · ${data.unsupportedSymbols.length} 个非美股期权标的未纳入`
                : ''}
            </span>
            {premarketFieldsUnavailable && (
              <span className="inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning">
                {PREMARKET_FALLBACK_CHIP}
              </span>
            )}
            {momentumWarmingUp && (
              <span className="inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning">
                {MOMENTUM_WARMING_UP_CHIP}
              </span>
            )}
          </span>
        )}
      </header>

      {error && (
        <div className="border-b border-[color:var(--warn-muted)] bg-bg-0 px-4 py-2 text-caption text-warning" role="status">
          实时扫描暂不可用：{error}
          {data ? '。仍显示上一次成功结果。' : ''}
        </div>
      )}

      {!data && loading ? (
        <div className="space-y-2 p-4">
          {[0, 1, 2].map((item) => (
            <div key={item} className="h-10 animate-pulse rounded-ds-sm bg-bg-2" />
          ))}
        </div>
      ) : !data || rows.length === 0 ? (
        <div className="px-4 py-6 text-body-sm text-text-3">
          当前没有可展示的日内候选。请先在 Watchlist 加入美股期权标的，或检查服务端 STOCK_LIST 与 Moomoo 行情配置。
        </div>
      ) : (
        <div className="overflow-auto">
          <table className="w-full min-w-[1180px] border-collapse" aria-label="实时扫描表">
            <thead>
              <tr className="border-b border-subtle text-left text-caption text-text-3">
                <th className="px-3 py-2 text-right font-medium">
                  <Tooltip focusable content={RANK_HEADER_TOOLTIP}>
                    <span aria-label={RANK_HEADER_TOOLTIP}>排名</span>
                  </Tooltip>
                </th>
                <th className="px-3 py-2 font-medium">标的</th>
                <th className="px-3 py-2 text-right">{sortableHeader('change', '涨跌%')}</th>
                <th className="px-3 py-2 text-right">{sortableHeader('burst', '当前爆发')}</th>
                <th className="px-3 py-2 font-medium">今日波段</th>
                <th className="px-3 py-2 font-medium">速度</th>
                <th className="px-3 py-2 font-medium">
                  <Tooltip focusable content={DISPLACEMENT_HEADER_TOOLTIP}>
                    <span aria-label={DISPLACEMENT_HEADER_TOOLTIP}>近30分位移</span>
                  </Tooltip>
                </th>
                <th className="px-3 py-2 font-medium">形态</th>
                <th className="px-3 py-2 font-medium">详情</th>
                {/* 「你的战绩」按用户要求收尾：它是上下文标注，不是盘中
                    交易关键读数——放最后一列，读表动线先看行情后看战绩。 */}
                <th className="px-3 py-2 font-medium">
                  <Tooltip focusable content={PERSONAL_HEADER_TOOLTIP}>
                    <span aria-label={PERSONAL_HEADER_TOOLTIP}>你的战绩</span>
                  </Tooltip>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <Fragment key={item.ticker}>
                <tr
                  onClick={() => navigate(`/regime/opportunity/${item.ticker}`)}
                  className="cursor-pointer border-b border-subtle last:border-b-0 hover:bg-bg-2"
                  aria-label={`打开 ${item.ticker} 即时扫描详情`}
                >
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-sm text-text-2">
                      #{serverRankByTicker.get(item.ticker) ?? '—'}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="font-mono text-mono-sm font-semibold text-text-1">{item.ticker}</div>
                    {(() => {
                      const badge = deepLaneBadge(item);
                      const earningsBadge = earningsTickerBadge(item);
                      if (!badge && !earningsBadge) return null;
                      return (
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {badge && (
                            <span
                              className="inline-block whitespace-nowrap rounded-ds-sm border border-subtle px-1.5 py-0.5 text-caption text-text-2"
                              aria-label={`深度位原因：${badge}（闸门标注，非信号）`}
                            >
                              {badge}
                            </span>
                          )}
                          {earningsBadge}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {premarketPhase ? (
                      // 盘前口径（G-12）：主行＝真实盘前变动，标缺显式
                      // 「盘前标缺」；sessionChangePercent 此刻仍是上一常规
                      // 时段读数——降级到副行并如实标「昨日」。
                      <>
                        {item.preChangePercent !== null
                        && item.preChangePercent !== undefined ? (
                          <div className={`font-mono text-mono-sm ${
                            item.preChangePercent > 0
                              ? 'text-up-strong'
                              : item.preChangePercent < 0
                                ? 'text-down-strong'
                                : 'text-text-3'
                          }`}
                          >
                            盘前 {formatSignedPercent(item.preChangePercent)}
                          </div>
                        ) : (
                          <div className="font-mono text-mono-sm text-text-3">盘前标缺</div>
                        )}
                        <div className="mt-0.5 text-caption text-text-3">
                          昨日 {formatSignedPercent(item.sessionChangePercent)}
                          {' · '}
                          {formatPrice(item.lastPrice)} · {quoteAsOfLabel(item)}
                        </div>
                      </>
                    ) : (
                      <>
                        <div className={`font-mono text-mono-sm ${
                          item.sessionChangePercent === null
                            ? 'text-text-3'
                            : item.sessionChangePercent > 0
                              ? 'text-up-strong'
                              : item.sessionChangePercent < 0
                                ? 'text-down-strong'
                                : 'text-text-3'
                        }`}
                        >
                          {formatSignedPercent(item.sessionChangePercent)}
                        </div>
                        <div className="mt-0.5 text-caption text-text-3">
                          {formatPrice(item.lastPrice)} · {quoteAsOfLabel(item)}
                        </div>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {item.sessionBursts.state === 'ready' && item.sessionBursts.current ? (
                      <>
                        <div className="font-mono text-mono-sm text-text-1">
                          {item.sessionBursts.current.score === null
                            ? '—'
                            : item.sessionBursts.current.score.toFixed(1)}
                          <span className={`ml-1 ${
                            item.sessionBursts.current.direction === 'up'
                              ? 'text-up-strong'
                              : item.sessionBursts.current.direction === 'down'
                                ? 'text-down-strong'
                                : 'text-text-3'
                          }`}
                          >
                            {DIRECTION_ARROWS[item.sessionBursts.current.direction]}
                          </span>
                        </div>
                        {/* 量比是「暴不暴量」的直读：<1 比平时还清淡，≥2 才算放量。 */}
                        <div className="mt-0.5 text-caption text-text-3">
                          15分 {formatBurstThrust(item.sessionBursts.current.thrustPercent)}
                          {item.sessionBursts.current.volNorm !== null && (
                            <>
                              {' · 量比 '}
                              <span
                                className={
                                  item.sessionBursts.current.volNorm >= 2
                                    ? 'text-warning font-medium'
                                    : item.sessionBursts.current.volNorm < 1
                                      ? 'text-text-3'
                                      : 'text-text-2'
                                }
                              >
                                {item.sessionBursts.current.volNorm.toFixed(2)}×
                              </span>
                            </>
                          )}
                        </div>
                        {fizzleMarker(item)}
                      </>
                    ) : (
                      <>
                        <div className="font-mono text-mono-sm text-text-3">—</div>
                        <div className="mt-0.5 text-caption text-text-3">
                          {item.sessionBursts.state === 'insufficient_bars' ? 'K线不足' : '标缺'}
                        </div>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    {item.sessionBursts.state === 'ready' ? (
                      item.sessionBursts.legs.length > 0 ? (
                        <>
                          <div className="flex flex-wrap gap-1" aria-label={legsDetailLabel(item)}>
                            {item.sessionBursts.legs.map((leg) => (
                              <span
                                key={`${leg.startEt}-${leg.endEt}`}
                                className={
                                  leg.grade === 'strong'
                                    ? LEG_CHIP_STRONG_CLASS
                                    : LEG_CHIP_DEFAULT_CLASS
                                }
                              >
                                {leg.startEt}
                                {DIRECTION_ARROWS[leg.direction]}
                                {leg.grade ? ` ${GRADE_LABELS[leg.grade]}` : ''}
                              </span>
                            ))}
                          </div>
                          <div className="mt-0.5 text-caption text-text-3">{legsBasisCaption(item)}</div>
                        </>
                      ) : (
                        <>
                          <div className="text-body-sm text-text-3">—</div>
                          <div className="mt-0.5 text-caption text-text-3">本时段无记录波段</div>
                        </>
                      )
                    ) : (
                      <>
                        <div className="text-body-sm text-text-3">标缺</div>
                        <div className="mt-0.5 text-caption text-text-3">
                          {item.sessionBursts.state === 'insufficient_bars' ? 'K线不足' : '波段读数不可得'}
                        </div>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className={`text-body-sm font-medium ${SPEED_STYLES[item.sessionBursts.speed.state]}`}>
                      {SPEED_LABELS[item.sessionBursts.speed.state]}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">{speedCaption(item)}</div>
                  </td>
                  <td className="px-3 py-2.5">{displacementCell(item)}</td>
                  <td className="px-3 py-2.5">{setupMatchCell(item)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={(event) => {
                          // 阻断整行导航：按钮只负责展开/收起研究读数 + 合约面板。
                          event.stopPropagation();
                          setExpandedTicker((current) => (
                            current === item.ticker ? null : item.ticker
                          ));
                        }}
                        className="whitespace-nowrap rounded-ds-sm border border-subtle px-2 py-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
                        aria-expanded={expandedTicker === item.ticker}
                        aria-label={`展开 ${item.ticker} 研究读数与临期合约`}
                      >
                        详情
                        {expandedTicker === item.ticker ? ' ▴' : ' ▾'}
                      </button>
                      {planTickers.includes(item.ticker) ? (
                        <span
                          data-plan-state="promoted"
                          className="whitespace-nowrap rounded-ds-sm border border-strong bg-bg-2 px-2 py-1 text-caption text-text-1"
                          aria-label={`${item.ticker} 已在盘中计划`}
                        >
                          已在计划
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={planFull}
                          onClick={(event) => {
                            event.stopPropagation();
                            promote(item.ticker);
                          }}
                          className="flex items-center gap-1 whitespace-nowrap rounded-ds-sm border border-subtle px-2 py-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1 disabled:cursor-not-allowed disabled:opacity-50"
                          // 满额原因走 aria-label（仓库禁用原生 title 属性）。
                          aria-label={planFull
                            ? `盘中计划已满（最多 ${INTRADAY_PLAN_MAX_TICKERS} 个标的）`
                            : `把 ${item.ticker} 加入盘中计划`}
                        >
                          <ArrowUp size={11} />
                          加入盘中计划
                        </button>
                      )}
                    </div>
                  </td>
                  {/* 「你的战绩」按用户要求放最后：上下文标注收尾，不挤占
                      行情读数的视线（列头 tooltip 口径不变）。 */}
                  <td className="px-3 py-2.5">{personalStatCell(personalEdge, item.ticker)}</td>
                </tr>
                {expandedTicker === item.ticker && (
                  <tr className="border-b border-subtle last:border-b-0">
                    <td colSpan={10} className="bg-bg-0 px-3 py-3">
                      <ResearchReadoutsGrid item={item} />
                      <NearExpiryContractPanel symbol={item.ticker} />
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data?.universeScan && dayLedgerRows.length > 0 && (
        <div
          aria-label="今日曾深扫账本"
          className="border-t border-subtle bg-bg-0 px-4 py-3"
        >
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-body-sm font-medium text-text-2">
              今日曾深扫 · 波段保留（{dayLedgerRows.length} 檔）
            </span>
            <span className="text-caption text-text-3">
              已被轮换出深度层 · 显示最后一次深扫读数（as-of，不实时刷新）· 重启后从当前时刻累计
            </span>
          </div>
          <ul className="mt-2 space-y-1.5">
            {dayLedgerRows.map((row) => (
              <li
                key={row.ticker}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-ds-sm border border-subtle bg-bg-1 px-2 py-1.5"
              >
                <span className="font-mono text-mono-sm font-semibold text-text-1">
                  {row.ticker}
                </span>
                <span className="font-mono text-mono-xs text-text-3">
                  {ledgerAsOfLabel(row.lastSeenAt)} 深扫
                </span>
                <span
                  className={`font-mono text-mono-xs ${
                    row.lastChangePercent === null
                      ? 'text-text-3'
                      : row.lastChangePercent > 0
                        ? 'text-up-strong'
                        : row.lastChangePercent < 0
                          ? 'text-down-strong'
                          : 'text-text-3'
                  }`}
                >
                  {formatSignedPercent(row.lastChangePercent)}
                </span>
                {row.sessionBurstsLegs.length > 0 ? (
                  <span className="flex flex-wrap gap-1">
                    {row.sessionBurstsLegs.map((leg) => (
                      <span
                        key={`${leg.startEt}-${leg.endEt}`}
                        className={
                          leg.grade === 'strong'
                            ? LEG_CHIP_STRONG_CLASS
                            : LEG_CHIP_DEFAULT_CLASS
                        }
                      >
                        {leg.startEt}
                        {DIRECTION_ARROWS[leg.direction]}
                        {leg.grade ? ` ${GRADE_LABELS[leg.grade]}` : ''}
                      </span>
                    ))}
                  </span>
                ) : (
                  <span className="text-caption text-text-3">无记录波段</span>
                )}
                <span className="text-caption text-text-3">
                  形态 {row.setupMatchedSetups.length > 0 ? row.setupMatchedSetups.join('/') : '—'}
                </span>
                {personalLedgerChip(personalEdge, row.ticker)}
                <span className="inline-block whitespace-nowrap rounded-ds-sm border border-dashed border-subtle px-1.5 py-0.5 text-caption text-text-3">
                  已轮换出
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 因 8 行硬顶被挤出深度层的标的：必须可见，否则「披露」形同虚设。 */}
      {(data?.universeScan?.trimmedByTotalCap?.length ?? 0) > 0 && (
        <div
          data-testid="trimmed-by-total-cap"
          aria-label="因总行数上限未进扫描表的标的"
          className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3"
        >
          {`因 ${data?.universeScan?.deepLaneTotalMax ?? 8} 行上限未进扫描表：`}
          {(data?.universeScan?.trimmedByTotalCap ?? [])
            .map((row) => row.ticker)
            .join('、')}
          {'（'}
          {(data?.universeScan?.trimmedByTotalCap ?? []).some(
            (row) => row.wouldBePromotedBy === 'plan_always_include',
          )
            ? '盘前计划标的见下方「今日计划跟踪」；'
            : ''}
          {'仍在全清单快照覆盖内，未被丢弃）'}
        </div>
      )}

      {data?.universeScan && (
        <div
          aria-label="仅快照标的"
          className="border-t border-subtle bg-bg-0 px-4 py-3"
        >
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-body-sm font-medium text-text-2">
              仅快照 · 未做深度分析（{data.universeScan.snapshotOnly.length} 檔）
            </span>
            <span className="text-caption text-text-3">
              {premarketBasis ? '按 |盘前涨跌幅| 排序（盘前价 vs 前收）' : '按 |涨跌幅| 排序'}
              {' '}· 闸门之外无爆发/形态/速度读数——缺席即缺席，不以 0 冒充
            </span>
          </div>
          {visibleSnapshotRows.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {visibleSnapshotRows.map((row) => (
                <li
                  key={row.ticker}
                  className="inline-flex items-baseline gap-1.5 rounded-ds-sm border border-subtle bg-bg-1 px-2 py-1"
                >
                  <span className="font-mono text-mono-xs font-medium text-text-1">{row.ticker}</span>
                  {premarketBasis ? (
                    // 盘前口径：只展示真实盘前读数；缺盘前字段显式「盘前标缺」，
                    // 绝不把上一常规时段涨跌冒充成盘前变动。
                    row.preChangePercent !== null && row.preChangePercent !== undefined ? (
                      <>
                        <span
                          className={`font-mono text-mono-xs ${
                            row.preChangePercent > 0
                              ? 'text-up-strong'
                              : row.preChangePercent < 0
                                ? 'text-down-strong'
                                : 'text-text-3'
                          }`}
                        >
                          盘前 {formatSignedPercent(row.preChangePercent)}
                        </span>
                        <span className="font-mono text-mono-xs text-text-3">
                          {row.preTurnover !== null && row.preTurnover !== undefined
                            ? formatCompactUsd(row.preTurnover)
                            : '标缺'}
                        </span>
                      </>
                    ) : (
                      <span className="font-mono text-mono-xs text-text-3">盘前标缺</span>
                    )
                  ) : (
                    <>
                      <span
                        className={`font-mono text-mono-xs ${
                          row.changePercent === null
                            ? 'text-text-3'
                            : row.changePercent > 0
                              ? 'text-up-strong'
                              : row.changePercent < 0
                                ? 'text-down-strong'
                                : 'text-text-3'
                        }`}
                      >
                        {formatSignedPercent(row.changePercent)}
                      </span>
                      <span className="font-mono text-mono-xs text-text-3">
                        {row.turnover !== null ? formatCompactUsd(row.turnover) : '标缺'}
                      </span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
          {snapshotRows.length > SNAPSHOT_ONLY_COLLAPSED_COUNT && (
            <button
              type="button"
              onClick={() => setSnapshotListExpanded((current) => !current)}
              aria-expanded={snapshotListExpanded}
              className="mt-2 rounded-ds-sm border border-subtle px-2 py-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
            >
              {snapshotListExpanded
                ? `收起 · 只显示前 ${SNAPSHOT_ONLY_COLLAPSED_COUNT} 檔`
                : `展开全部 ${snapshotRows.length} 檔`}
            </button>
          )}
          {data.universeScan.snapshotUnresolvedSymbols.length > 0 && (
            <div className="mt-2 text-caption text-warning">
              快照未解析 {data.universeScan.snapshotUnresolvedSymbols.length} 檔（供应商无返回行，显式标缺）：
              {data.universeScan.snapshotUnresolvedSymbols.join('、')}
            </div>
          )}
          {data.universeScan.dayPromotionCapReached && (
            <div className="mt-1 text-caption text-warning">
              今日新晋升深度位已达上限（{data.universeScan.dayPromotionCap} 檔 · K 线额度护栏）：
              新异动标的今日仅保留快照行。
            </div>
          )}
          {data.universeScan.watchlistTruncated && (
            <div className="mt-1 text-caption text-warning">
              清单超出服务端上限，超出部分未纳入扫描（配置 {data.universeScan.watchlistTotal} 檔）。
            </div>
          )}
        </div>
      )}

      {data?.universeScan && (
        <div className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
          全清单 {data.universeScan.scannedTotal} 檔快照 · 深度分析前 {data.universeScan.deepLaneMax} 檔
          {premarketBasis
            ? ` · ${PREMARKET_GATE_CAPTION} · 其余仅快照；`
            : momentumBasis
              ? `（${MOMENTUM_GATE_CAPTION}）· 其余仅快照；`
              : '（|涨跌|→成交额）· 其余仅快照；'}
          {`计划钉选 ${data.universeScan.planAlwaysInclude.length} 檔${
            (data.universeScan.userPinned?.length ?? 0) > 0
              ? ` + 用户钉选 ${data.universeScan.userPinned?.length} 檔`
              : ''
          }始终占深度位（不占 K 名额）。`}
          {premarketFieldsUnavailable ? `${PREMARKET_FALLBACK_CHIP}。` : ''}
          {momentumWarmingUp ? `${MOMENTUM_WARMING_UP_CHIP}。` : ''}
          闸门为启发式，晋升不代表方向或质量结论。
        </div>
      )}

      <div className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
        {/* 延迟诊断（additive）：让「页面变慢」可以从截图直接定位。命中缓存
            时耗时是该结果的原始生成耗时，不是本次请求耗时。旧载荷缺席即不渲染。 */}
        {data?.generatedInSeconds !== null && data?.generatedInSeconds !== undefined && (
          <div
            data-testid="scan-latency-footer"
            className="mb-1 font-mono text-mono-xs text-text-3"
          >
            {`本轮扫描耗时 ${data.generatedInSeconds.toFixed(1)}s`}
            {data.servedFrom === 'cache'
              ? ' · 缓存命中'
              : data.servedFrom === 'warm_cache'
                ? ' · 预热缓存命中'
                : data.servedFrom === 'fresh'
                  ? ' · 实时生成'
                  : ''}
          </div>
        )}
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span>
            排序与口径说明：盘中排序＝爆发分优先（休市按最近交易时段）；今日波段分级
            强＝爆发分 ≥8 暴动 / 中＝≥2.5 持续推升；近30分位移＝最近 6 根 5m K 线净位移 ÷ ATR，
            0.5 ATR 只是参考刻度（原分层按持仓时长定义、循环性已被量化推翻：向前计分后差距仅
            0.1pp）——该列只描述过去 30 分钟，不含前向信息、非预测、非信号；
            速度/位移/波段vs大盘/财报/形态/你的战绩均为上下文标注——
            不隐藏行、不参与排序、不是信号；缺失字段显式标缺，不以 0 冒充。
          </span>
          <button
            type="button"
            onClick={() => setMethodologyExpanded((current) => !current)}
            aria-expanded={methodologyExpanded}
            aria-label="展开完整口径说明"
            className="whitespace-nowrap rounded-ds-sm border border-subtle px-2 py-0.5 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
          >
            完整口径
            {methodologyExpanded ? ' ▴' : ' ▾'}
          </button>
        </div>
        {methodologyExpanded && <div className="mt-2">{FULL_METHODOLOGY_TEXT}</div>}
      </div>
    </section>
  );
}
