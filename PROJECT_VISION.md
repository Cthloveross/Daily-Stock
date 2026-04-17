# Daily-Stock: 个人化美股日内交易辅助系统

> **项目愿景文档 v1.0** · 维护者：[@Cthloveross](https://github.com/Cthloveross) · 最后更新：2026-04-16
>
> 本文档是 `Daily-Stock` 项目的**真源**。任何代码改造前，请先阅读本文档相关章节，确保改动方向与愿景一致。

---

## 目录

- [一、项目定位](#一项目定位)
- [二、目标用户与交易风格](#二目标用户与交易风格)
- [三、核心能力规划](#三核心能力规划)
- [四、技术架构](#四技术架构)
- [五、路线图](#五路线图)
- [六、技术决策记录（ADR）](#六技术决策记录adr)
- [七、实施细节：每个模块的具体改造点](#七实施细节每个模块的具体改造点)
- [八、验证与交付标准](#八验证与交付标准)
- [附录 A：从原仓库迁移到新仓库的操作步骤](#附录-a从原仓库迁移到新仓库的操作步骤)
- [附录 B：环境变量速查表](#附录-b环境变量速查表)

---

## 一、项目定位

### 1.1 从哪里来

本项目 fork 自 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) — 一个**通用**的股票 AI 分析系统，覆盖 A股/港股/美股，每日跑一次分析生成文本报告，通过企业微信/飞书/Telegram 等多渠道推送。

原项目是"**全市场 · 每日复盘**"定位，在自选股日终生成综合分析，适合持仓较多、以价值与趋势为主的长线/中线投资者。

### 1.2 要做成什么

`Daily-Stock` 的定位更聚焦：

> **个人化的美股日内交易辅助系统** — 围绕一个具体的交易者（我自己）重新设计数据视角、指标体系、分析时机、可视化呈现和 AI Prompt，让每一次分析都为"日内或短波段的交易决策"服务，而不是泛泛生成报告。

关键差异：

| 维度 | 原项目（通用） | Daily-Stock（个人化） |
|------|----------------|----------------------|
| 市场 | A股 + 港股 + 美股 | **只做美股**（七姐妹 + 半导体 + 科技热门） |
| 时间尺度 | 日线级（EOD） | **5min / 15min 日内** + 日线作背景 |
| 指标 | MA5 / MA10 / MA20（A 股习惯） | **MA3 / MA5 / MA13**（更贴合日内节奏） |
| 报告形态 | 纯文本 | **K 线图 + 文本**（图文并茂） |
| 运行时机 | 每日一次（18:00 北京时间） | **盘前 + 盘中每小时 + 盘后**（美东时间） |
| 交易风格 | 通用模板 | **我的个人风格注入到 Prompt** |
| 事件感知 | 新闻搜索被动获取 | **主动维护美股事件日历**（财报/FOMC/CPI） |

### 1.3 不做什么（明确边界）

为了避免范围失控，本项目**明确不做**以下事情：

- ❌ **不做自动下单 / 自动交易**。系统只生成决策建议，执行由人来做。
- ❌ **不做加密货币 / 期货 / 期权**。只做美股正股。
- ❌ **不做组合优化 / 量化回测**。项目原有的回测能力保留但不增强。
- ❌ **不做专业看盘软件**。K 线图作为"报告上下文参考"，TradingView 依然是主看盘工具。
- ❌ **不做多租户 / SaaS 化**。就是一个自用工具，不考虑对外服务。

---

## 二、目标用户与交易风格

### 2.1 用户画像

- **身份**：在校研究生，UCSD CSE / ECE 双方向，深度学习 + 量化交叉背景
- **盘龄**：美股实盘若干年，熟悉 TradingView
- **持仓风格**：日内 + 短波段为主（1 日到 5 个交易日）
- **标的偏好**：大市值科技股、半导体、成长型高 Beta（AAPL、MSFT、GOOGL、AMZN、META、TSLA、NVDA、MU、SNDK、AMD、AVGO、TSM、PLTR、COIN、NFLX）

### 2.2 交易风格（会写入 Prompt）

这套风格会通过 `TRADING_STYLE_PROMPT` 环境变量注入到每次 AI 分析的系统提示词中：

```
我的交易风格（美股日内 / 短波段）：

【仓位与风控】
- 严格 2-3% 止损，触发即出，不抱侥幸
- 单只股票仓位 ≤ 总资金 15%
- 日内最大亏损 ≤ 总资金 3%，触发即停手

【进场策略 — 混合】
1. 趋势追踪（momentum）：MA3 > MA5 > MA13 多头排列 + 放量突破近期高点，追入
2. 回调买入（pullback）：强势股回踩 MA5 或 MA13 企稳，低吸
3. 事件驱动：财报 / FOMC / CPI / 就业数据前后的波动机会（事前减仓 or 事后确认方向再入）

【指标体系】
- 主看 MA3 / MA5 / MA13 三线排列（不用 MA20 / MA50）
- 成交量：无量突破不追，放量滞涨要警惕
- 盘中看 5min / 15min 结构，背景看日线
- 关键位：当日 VWAP、昨日高低点、近期强阻力

【不做的事】
- 不接飞刀 — 跌破 MA13 破位不抄底
- 不摊平 — 止损就止损，不补仓
- 不过夜除非趋势极强且 MA3 / MA5 均未破位
```

### 2.3 为什么是 MA3 / MA5 / MA13

- **MA3**：超短周期，日内最敏感的趋势线，相当于 3 根日线的均线，在 5min 图上反应极快
- **MA5**：常规一周均线，兼具反应速度与稳定性
- **MA13**：斐波那契数列中的周期，在波动性标的（科技股）上比 MA10 / MA20 更贴合实际走势

三条合在一起足以判断短波段的"多头/空头/震荡"结构，不需要更多均线造成视觉噪音。

---

## 三、核心能力规划

按优先级分 P0 / P1 / P2 三档：

### 3.1 P0 — 本季度必做（M1 里程碑）

| 能力 | 说明 | 现状 | 要做什么 |
|------|------|------|---------|
| **K 线图可视化** | Web UI 报告页顶部嵌入日线 K 线图 + MA3/5/13 叠加 | 后端已有 OHLC API（`/api/v1/stocks/{code}/history`），前端未接入 | 新建 `KlineChart.tsx`，复用已捆绑的 Recharts |
| **MA 周期切换** | 把项目默认的 MA5/10/20 改为 MA3/5/13 | 硬编码在 6-8 处 | 全局替换 + 提取为 `MA_PERIODS` 常量 |
| **自定义交易风格注入** | AI 分析时携带我的交易风格提示词 | Prompt 硬编码在 `analyzer.py`，无注入点 | 新增 `TRADING_STYLE_PROMPT` env，拼入 SYSTEM_PROMPT |
| **美股聚焦** | STOCK_LIST 改为 15 只美股，MARKET_REVIEW_REGION=us | 已改（见 `.env`） | 无 |

### 3.2 P1 — 下季度做（M2–M3 里程碑）

| 能力 | 说明 | 现状 | 要做什么 |
|------|------|------|---------|
| **5m / 15m 日内分析** | 能拉 5 分钟 / 15 分钟 K 线并做 AI 分析 | Longbridge SDK 支持但未启用（硬编码 `Period.Day`） | 解硬编码；加 `ANALYSIS_INTERVAL` env |
| **美股事件日历** | 每周生成未来 5 日财报 / 经济数据事件摘要 | 无 | 新建 `src/core/event_calendar.py` + 报告模板 |
| **美股交易时段调度** | GitHub Actions 按美东时段跑（盘前/盘中/盘后） | 固定 UTC 10:00（北京 18:00） | 改 cron，加 matrix |
| **TradingView Lightweight Charts 升级** | 如果 Recharts 不够用，升级到专业图表库 | Recharts 足够 | 观望，视 M1 反馈再定 |

### 3.3 P2 — 长期观察

- 回测时段化（按 intraday 回测 5m 策略）
- 事件驱动策略 YAML（如"财报前 3 天减仓"专属 skill）
- 个人化桌面端（Electron 只保留美股 US 界面）
- 本地部署 Ollama 替代 Gemini，降低延迟
- 接入 TradingView 的 Webhook 做双向联动（盘中触发信号推送）

### 3.4 明确砍掉的能力

从原项目继承但**本项目不再维护**的功能（不删代码，但不加新特性）：

- A 股 / 港股分析链路
- 大盘复盘的 A 股部分（`MARKET_REVIEW_REGION=cn` / `both`）
- 智能补全 / 图片识别导入（日常用不到）
- 多语言文档（只维护中文 + 英文两份）
- 企业微信 / 飞书 / 钉钉 / PushPlus 推送（只保留 Telegram + 邮件）

---

## 四、技术架构

### 4.1 保留的基础设施（从原项目继承）

```
┌─────────────────────────────────────────────────────────────┐
│                  Daily-Stock Architecture                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  Data Layer  │───▶│  AI Analyzer │───▶│ Report / Push│  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                    │                    │         │
│         ▼                    ▼                    ▼         │
│   YFinance              Gemini / Claude    Telegram / Email │
│   Longbridge (P1)       LiteLLM 路由       Markdown 报告    │
│   AkShare 兜底          策略 YAML          K线图 (P0)       │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  FastAPI     │───▶│  React Web   │───▶│  SQLite DB   │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  GitHub Actions: daily_analysis.yml (cron 调度)      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 需要改造的组件

- **Data Layer**：`data_provider/longbridge_fetcher.py` 解除 `Period.Day` 硬编码，支持 5m/15m
- **AI Analyzer**：`src/analyzer.py` 的 `SYSTEM_PROMPT` 加入 `TRADING_STYLE_PROMPT` 注入点
- **Stock Analyzer**：`src/stock_analyzer.py` 的 MA 计算改为 `MA_PERIODS = (3, 5, 13)`
- **Web UI**：新增 `KlineChart.tsx`，在报告页顶部渲染
- **调度**：`daily_analysis.yml` 改美东时区 cron

### 4.3 新增的组件

- **`src/core/event_calendar.py`**：美股事件日历（P1）
- **`src/reports/weekly_events.py`**：周事件报告生成（P1）
- **`apps/dsa-web/src/components/report/KlineChart.tsx`**：K 线图组件（P0）
- **`apps/dsa-web/src/api/stocks.ts`** 扩展：新增 `getHistory()` 方法（P0）

---

## 五、路线图

### 5.1 里程碑总表

| 里程碑 | 时间窗口 | 核心交付 | 验收标准 |
|--------|---------|---------|---------|
| **M0** | 本周 | 项目愿景文档 + 迁移到新仓库 | PROJECT_VISION.md 可在 Cthloveross/Daily-Stock 看到 |
| **M1** | 下周 | K 线图 + MA3/5/13 + 自定义 Prompt | Web 报告页顶部有 K 线图；Gemini 报告引用 MA3/5/13；报告里体现"2-3% 止损"风格 |
| **M2** | 第三周 | 5m / 15m 日内分析能力 | `python main.py --interval 5m --stocks NVDA` 跑通 |
| **M3** | 第四周 | 美股事件日历 + 时段化调度 | 每周日晚收到未来 5 日财报事件推送；工作日美东时间自动跑盘前分析 |
| **M4** | 持续 | 事件驱动策略、回测时段化、TV 图表升级 | 视 M1–M3 使用反馈决定优先级 |

### 5.2 每个里程碑的具体任务

#### M0（当前）

- [x] 梳理项目现状，产出愿景文档（本文件）
- [x] 确认迁移到新仓库策略（替换 remote）
- [ ] 推送到 https://github.com/Cthloveross/Daily-Stock.git
- [ ] 在新仓库 README 顶部放 Daily-Stock 品牌与定位

#### M1：K 线图 + MA3/5/13 + 交易风格注入

前端：
- [ ] `apps/dsa-web/src/api/stocks.ts` 新增 `getHistory(stockCode, days)`
- [ ] `apps/dsa-web/src/components/report/KlineChart.tsx` 新建（Recharts `ComposedChart`，下方 LineChart 叠 MA3/5/13，上方自绘 K 线 Bar）
- [ ] `apps/dsa-web/src/components/report/ReportSummary.tsx` 在报告顶部插入 `<KlineChart stockCode={meta.code} />`
- [ ] 移动端响应式适配（高度 240 / 桌面 320）

后端：
- [ ] `src/stock_analyzer.py:267-273` 把 `MA5 / MA10 / MA20` 改为 `MA3 / MA5 / MA13`
- [ ] `src/analyzer.py:550-552, 700-702` Prompt schema 同步
- [ ] `src/analyzer.py:1513-1516, 1606-1610, 1634-1635, 1734` 报告模板同步
- [ ] 提取常量 `MA_PERIODS = (3, 5, 13)` 到 `src/config.py` 或类似位置（可选，长期建议）
- [ ] `src/analyzer.py:903` SYSTEM_PROMPT 拼装处加 `TRADING_STYLE_PROMPT` 注入
- [ ] `.env.example` 新增 `TRADING_STYLE_PROMPT=` 配置示例

验证：
- [ ] `python main.py --dry-run --stocks AAPL` 运行无错
- [ ] 生成的报告中 MA 引用为 3/5/13
- [ ] Web UI 报告页能看到 K 线图
- [ ] 报告里出现"2-3% 止损"、"MA3>MA5>MA13 多头"等表述

#### M2：5m / 15m 日内分析

前置：
- [ ] Longbridge 注册开户 + 获取 `LONGBRIDGE_APP_KEY / SECRET / ACCESS_TOKEN`
- [ ] 在 `.env` 配置好凭据

代码：
- [ ] `data_provider/longbridge_fetcher.py:464, 638` 把 `Period.Day` 改为参数化
- [ ] 新增 `fetch_intraday_candles(symbol, period='5m'|'15m', limit=200)`
- [ ] `src/analyzer.py` 加入 `ANALYSIS_INTERVAL` env 分支：
  - `1d` → 现有日线分析链路
  - `5m` / `15m` → 调用 Longbridge 日内 K 线，Prompt 切换到"日内模式"
- [ ] 日内模式 Prompt 模板（强调 VWAP、当日关键位、盘口方向）

CLI：
- [ ] `main.py` 支持 `--interval 5m|15m|1d` 参数
- [ ] 未配置 Longbridge 时选 intraday → 清晰错误提示

验证：
- [ ] `python main.py --interval 5m --stocks NVDA --dry-run` 拉到 5m K 线，生成日内视角分析
- [ ] 前端 K 线图支持周期切换（日 / 15m / 5m）

#### M3：事件日历 + 时段化调度

代码：
- [ ] 新建 `src/core/event_calendar.py`
  - `fetch_earnings_calendar(symbols, days=7)` — 用 YFinance `ticker.calendar`
  - `fetch_macro_calendar(days=7)` — Fed RSS + 可选 Finnhub
- [ ] 新建 `src/reports/weekly_events.py` — 周日晚生成未来 5 日事件清单
- [ ] `main.py --weekly-events` 命令入口
- [ ] 推送复用 `src/notification.py`

调度：
- [ ] `.github/workflows/daily_analysis.yml` cron 改美东时区：
  - `30 13 * * 1-5`（UTC 13:30 = EDT 09:30 盘前）
  - `0 14,15,16,17,18,19 * * 1-5`（盘中每小时）
  - `0 20 * * 1-5`（EDT 16:00 盘后）
  - `0 0 * * 1`（周一 UTC 0:00 周日晚美东 20:00 发事件清单）

验证：
- [ ] 周日晚 Telegram 收到未来 5 日财报清单（如 NVDA / MSFT 财报日）
- [ ] 工作日美东 9:30 AM 自动跑一次盘前分析

---

## 六、技术决策记录（ADR）

### ADR-001：为什么 fork 而不是从零写

**决策**：fork 原项目，替换 git remote 指向新仓库。

**原因**：
- 原项目已经完成数据源抽象（5 个供应商 fallback）、LLM 路由（LiteLLM 多渠道）、报告引擎、Web UI 骨架、CI、Docker 化 — 这些从零写需要 1-2 个月
- 我们的改动本质是"裁剪 + 注入个性化"，不是"推翻架构"
- 保留 MIT License，可以继续享受原项目的持续更新（需要时手工合并）

**代价**：需要维护一份"哪些文件是我们改的"清单（见[第七章](#七实施细节每个模块的具体改造点)）

---

### ADR-002：为什么 K 线图用 Recharts 而不是 TradingView Lightweight Charts

**决策**：M1 用 Recharts，观察一个月后视需要再升级到 TradingView Lightweight Charts。

**原因**：
- Recharts 已经捆绑（`apps/dsa-web/package.json` v3.3.0），`PortfolioPage.tsx` 已有饼图用法，团队熟悉
- K 线图在本项目中的定位是"报告上下文参考"，不是主看盘工具
- TradingView Lightweight Charts ~43KB gzip，更专业但需要新学 API
- Recharts 的 `ComposedChart` 可以模拟蜡烛图（K 线用 Bar + Scatter 组合），虽然不够漂亮但够用

**升级触发条件**：
- 如果 M1 之后发现想要画趋势线 / 复杂交互 / 多品种对比
- 如果想做盘中实时刷新

---

### ADR-003：为什么 MA3 / MA5 / MA13 而不是 MA5 / MA10 / MA20

**决策**：全项目改为 MA3 / MA5 / MA13，作为唯一三条均线。

**原因**：
- 日内 5m 图上，MA5 过慢（相当于 25 分钟），MA10 已经 50 分钟，失去敏感性
- MA3 在 5m 图 = 15 分钟 均线，刚好是美股典型的"短期趋势周期"
- MA13 是斐波那契数列项，在高波动标的上比 MA20 更稳定贴合
- 少一条均线（原项目 3 条，新项目依然 3 条）不改变代码结构，只改参数

**不兼容影响**：
- 原项目的策略 YAML（`strategies/*.yaml`）里提到 "MA5 > MA10 > MA20" 的表述需同步改写
- 历史回测数据按老 MA 算过，迁移后回测结果会有偏差（可接受）

---

### ADR-004：为什么日内分析优先 Longbridge 而不是 YFinance

**决策**：intraday 模式（5m/15m）**只**支持 Longbridge 数据源，未配置时报错而非降级。

**原因**：
- YFinance 的 intraday 数据（`interval='5m'`）只能拉最近 60 天，且美国时段外容易被限流
- YFinance 接口偶发返回空数据或结构异常，日内级别分析不能容忍这种不确定性
- Longbridge OpenAPI 免费（需开户），数据稳定，SDK 成熟（Rust 核心 + Python binding）
- "宁可不分析也不分析错误数据"的风控底线

**代价**：
- 用户必须开 Longbridge 证券账户（免费，但需要身份验证）
- 如果用户不想开户，M2 能力不可用（可以继续用 M1 的日线分析）

---

### ADR-005：为什么 Prompt 用 env var 而不是 YAML 文件

**决策**：`TRADING_STYLE_PROMPT` 用多行 env var 注入，不新建 YAML 文件。

**原因**：
- 交易风格是"每个用户一份"的配置，不是"可切换的多方案"
- env var 放在 `.env`，和其他个人配置同一处，符合"配置集中"原则
- 不新增文件维护成本
- 如果以后想支持多风格切换（如"日内风格"/"波段风格"），再引入 YAML

**用法**：
```bash
# .env
TRADING_STYLE_PROMPT="我的交易风格：
- 严格 2-3% 止损
- MA3>MA5>MA13 多头追入
- 回踩 MA5/13 低吸
- 财报前减仓
"
```

---

## 七、实施细节：每个模块的具体改造点

> 这一章是给未来的我（或其他开发者）看的**执行手册**。每个改造点都精确到文件和行号，可以直接照着改。

### 7.1 前端：K 线图集成

#### 7.1.1 新增文件：`apps/dsa-web/src/components/report/KlineChart.tsx`

```typescript
import { useEffect, useState } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Legend, CartesianGrid,
} from 'recharts'
import { stocksApi, type KLineData } from '../../api/stocks'

interface Props {
  stockCode: string
  days?: number  // 默认 60
}

// 简化的 K 线：Bar = high-low 跨度，填色区分涨跌
// MA3/5/13 用 Line 叠加
// 若需专业蜡烛图形态，M4 再升级 TradingView Lightweight Charts

export function KlineChart({ stockCode, days = 60 }: Props) {
  const [data, setData] = useState<KLineData[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    stocksApi.getHistory(stockCode, days)
      .then(r => setData(r.data))
      .catch(() => setData([]))
      .finally(() => setLoading(false))
  }, [stockCode, days])

  const withMA = computeMAs(data, [3, 5, 13])

  if (loading) return <div className="h-[320px] flex items-center justify-center">加载中...</div>
  if (!data.length) return <div className="h-[320px] flex items-center justify-center text-muted">无 K 线数据</div>

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={withMA}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="date" />
        <YAxis domain={['auto', 'auto']} />
        <Tooltip />
        <Legend />
        <Bar dataKey="range" fill="#8884d8" />  {/* 简化 K 线 */}
        <Line type="monotone" dataKey="ma3" stroke="#ef4444" dot={false} />
        <Line type="monotone" dataKey="ma5" stroke="#3b82f6" dot={false} />
        <Line type="monotone" dataKey="ma13" stroke="#10b981" dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

function computeMAs(data: KLineData[], periods: number[]) {
  // 滚动窗口均线计算
  // 实现略，按标准实现即可
}
```

#### 7.1.2 修改：`apps/dsa-web/src/api/stocks.ts`

新增方法：
```typescript
export const stocksApi = {
  // ... 已有方法
  getHistory: async (stockCode: string, days = 30) => {
    const res = await apiClient.get(`/api/v1/stocks/${stockCode}/history`, {
      params: { period: 'daily', days },
    })
    return res.data as StockHistoryResponse
  },
}

export interface KLineData {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}
```

#### 7.1.3 修改：`apps/dsa-web/src/components/report/ReportSummary.tsx`

在最顶部插入：
```tsx
import { KlineChart } from './KlineChart'

// 组件内，在 ReportOverview 之前
<KlineChart stockCode={meta.code} days={60} />
```

---

### 7.2 后端：MA 周期替换

#### 7.2.1 `src/stock_analyzer.py:267-273`

```python
# 原代码
df['MA5']  = df['close'].rolling(window=5).mean()
df['MA10'] = df['close'].rolling(window=10).mean()
df['MA20'] = df['close'].rolling(window=20).mean()

# 改为
df['MA3']  = df['close'].rolling(window=3).mean()
df['MA5']  = df['close'].rolling(window=5).mean()
df['MA13'] = df['close'].rolling(window=13).mean()
```

同时需全局 grep 替换所有 `MA10` / `MA20` 引用为 `MA3` / `MA13`（注意 MA5 保留）。

#### 7.2.2 长期改进（可选）：提取配置常量

```python
# src/config.py（或新建）
from os import getenv

MA_PERIODS = tuple(
    int(x) for x in getenv('MA_PERIODS', '3,5,13').split(',')
)  # -> (3, 5, 13)
```

然后在 `stock_analyzer.py` 改为循环生成：
```python
for p in MA_PERIODS:
    df[f'MA{p}'] = df['close'].rolling(window=p).mean()
```

---

### 7.3 后端：Prompt 注入

#### 7.3.1 `src/analyzer.py:903` SYSTEM_PROMPT 拼装处

找到类似代码：
```python
base_prompt = SYSTEM_PROMPT.format(
    market_placeholder=market_role,
    guidelines_placeholder=guidelines,
    skills_section=skills_section,
    default_skill_policy_section=default_policy,
)
```

在 `.format()` 之后追加：
```python
trading_style = os.getenv('TRADING_STYLE_PROMPT', '').strip()
if trading_style:
    base_prompt += f"\n\n## 用户个人交易风格（必须严格遵循）\n\n{trading_style}\n"
```

#### 7.3.2 `.env.example` 新增

```bash
# ===================================
# 个人交易风格（注入到 AI Prompt）
# ===================================
# 支持多行（用 \n 或 heredoc）
TRADING_STYLE_PROMPT="我的交易风格（美股日内/短波段）：

【仓位与风控】
- 严格 2-3% 止损，触发即出，不抱侥幸
- 单只股票仓位 ≤ 总资金 15%

【进场策略 — 混合】
1. 趋势追踪：MA3>MA5>MA13 多头排列 + 放量突破，追入
2. 回调买入：强势股回踩 MA5/MA13 企稳，低吸
3. 事件驱动：财报/FOMC/CPI 前后的波动机会

【指标体系】
- 主看 MA3/MA5/MA13 三线排列
- 放量确认，无量突破不追
- 盘中看 5min/15min，背景看日线
"
```

---

### 7.4 后端：日内数据源

#### 7.4.1 `data_provider/longbridge_fetcher.py:464, 638`

找到 `Period.Day` 的硬编码位置，改为参数化：

```python
from longbridge.openapi import Period

PERIOD_MAP = {
    '1d': Period.Day,
    '1h': Period.Hour,
    '15m': Period.Minute15,
    '5m':  Period.Minute5,
    '1m':  Period.Minute1,
}

def fetch_candlesticks(
    symbol: str,
    period: str = '1d',
    count: int = 200,
):
    lb_period = PERIOD_MAP.get(period, Period.Day)
    return ctx.history_candlesticks_by_offset(symbol, lb_period, count)
```

#### 7.4.2 `src/analyzer.py` 加 intraday 分支

```python
INTERVAL = os.getenv('ANALYSIS_INTERVAL', '1d')

if INTERVAL in ('5m', '15m'):
    if not _longbridge_configured():
        raise ConfigError(
            f'ANALYSIS_INTERVAL={INTERVAL} 需要 Longbridge 凭据。'
            f'请配置 LONGBRIDGE_APP_KEY/SECRET/ACCESS_TOKEN 或改回 1d。'
        )
    candles = longbridge_fetcher.fetch_candlesticks(
        symbol, period=INTERVAL, count=200,
    )
    # 切换到日内 Prompt 模板
else:
    # 原日线链路
```

#### 7.4.3 日内 Prompt 模板

在 `src/analyzer.py` 新增 `INTRADAY_SYSTEM_PROMPT`，重点强调：
- VWAP、当日开盘价、昨日收盘价作为关键参照
- MA3（5m 图 = 15 分钟均线）作为进出场触发
- 不谈基本面、不谈周线趋势，只关注盘中结构

---

### 7.5 后端：美股事件日历

#### 7.5.1 新建 `src/core/event_calendar.py`

```python
import yfinance as yf
from datetime import datetime, timedelta

def fetch_earnings_calendar(symbols: list[str], days: int = 7) -> list[dict]:
    """返回未来 N 天内的财报事件"""
    events = []
    cutoff = datetime.now() + timedelta(days=days)
    for sym in symbols:
        cal = yf.Ticker(sym).calendar
        if cal is None or 'Earnings Date' not in cal:
            continue
        dt = cal['Earnings Date']
        if isinstance(dt, list):
            dt = dt[0]
        if dt <= cutoff:
            events.append({'symbol': sym, 'date': dt, 'type': 'earnings'})
    return sorted(events, key=lambda x: x['date'])


def fetch_macro_calendar(days: int = 7) -> list[dict]:
    """FOMC / CPI / 就业数据等。首期用硬编码 + Fed RSS"""
    # 最简实现：硬编码一个 _known_events 表，每季度人工维护
    # 进阶：接入 Finnhub / Trading Economics API
    return []
```

#### 7.5.2 新建 `src/reports/weekly_events.py`

```python
def generate_weekly_events_report(symbols: list[str]) -> str:
    earnings = fetch_earnings_calendar(symbols, days=7)
    macro = fetch_macro_calendar(days=7)

    if not earnings and not macro:
        return "# 未来 7 日无重大事件"

    md = f"# 本周美股事件日历 ({datetime.now():%Y-%m-%d})\n\n"

    if earnings:
        md += "## 财报日历\n\n"
        for e in earnings:
            md += f"- **{e['date']:%m-%d} {e['symbol']}** 财报\n"

    if macro:
        md += "\n## 宏观事件\n\n"
        for e in macro:
            md += f"- **{e['date']:%m-%d} {e['name']}**\n"

    return md
```

#### 7.5.3 `main.py` 入口

```python
if args.weekly_events:
    from src.reports.weekly_events import generate_weekly_events_report
    from src.notification import send_notification

    report_md = generate_weekly_events_report(STOCK_LIST)
    send_notification(title='本周美股事件日历', content=report_md)
```

---

### 7.6 CI：时段化调度

#### 7.6.1 `.github/workflows/daily_analysis.yml`

```yaml
on:
  schedule:
    # UTC 时间，美东 EDT 为 UTC-4（夏令时），EST 为 UTC-5（冬令时）
    # 下列以 EDT 为准（3-11 月），冬季时间会提前 1 小时
    - cron: '30 13 * * 1-5'  # EDT 09:30 盘前
    - cron: '0 15,17,19 * * 1-5'  # EDT 11:00 / 13:00 / 15:00 盘中
    - cron: '0 20 * * 1-5'  # EDT 16:00 盘后
    - cron: '0 0 * * 1'  # 周一 UTC 0:00 = 美东周日 20:00 发周事件

  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [full, intraday-5m, intraday-15m, weekly-events]
        default: full
```

#### 7.6.2 job 分支逻辑

```yaml
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - name: Run analysis
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          TRADING_STYLE_PROMPT: ${{ vars.TRADING_STYLE_PROMPT }}
          # ...
        run: |
          case "${{ github.event.schedule }}" in
            "30 13 * * 1-5")   python main.py --pre-market ;;
            "0 20 * * 1-5")    python main.py --post-market ;;
            "0 0 * * 1")       python main.py --weekly-events ;;
            *)                 python main.py --interval 15m ;;
          esac
```

---

## 八、验证与交付标准

### 8.1 M0 验证（本次）

- [ ] `PROJECT_VISION.md` 存在于项目根目录
- [ ] 文档内容完整覆盖"项目定位 / 用户画像 / 核心能力 / 路线图 / 实施细节"
- [ ] 新仓库 https://github.com/Cthloveross/Daily-Stock 首页能看到此文件
- [ ] `git log` 显示 origin 已切换（或 remote 已替换）

### 8.2 M1 验证

- [ ] `python main.py --dry-run --stocks AAPL` 运行成功，报告中 MA 引用为 MA3/MA5/MA13
- [ ] `npm run dev`（在 `apps/dsa-web`）启动 Web UI，报告详情页顶部看到 K 线图
- [ ] K 线图上有 3 条均线（MA3 红、MA5 蓝、MA13 绿，或类似配色）
- [ ] 鼠标悬停 K 线能看到 tooltip（OHLC 数值）
- [ ] AI 生成的报告中出现"严格 2-3% 止损"、"MA3>MA5>MA13 多头"、"放量突破"等个性化表述

### 8.3 M2 验证

- [ ] `python main.py --interval 5m --stocks NVDA --dry-run` 成功拉到 5 分钟 K 线
- [ ] 生成的分析报告强调盘中结构（VWAP / 当日关键位）而非日线趋势
- [ ] 未配置 Longbridge 时 `--interval 5m` 返回清晰错误提示

### 8.4 M3 验证

- [ ] 周日晚（美东时间）Telegram/邮箱收到未来 5 日财报事件推送
- [ ] 工作日美东 9:30 AM 自动运行盘前分析（GitHub Actions 历史可查）
- [ ] 盘后 16:00 自动运行收盘总结

---

## 附录 A：从原仓库迁移到新仓库的操作步骤

### 方式 A（推荐）：保留完整历史，只换远端

```bash
cd /Users/cth/Desktop/daily_stock_analysis

# 1. 查看当前 remote
git remote -v
# 可能看到：origin  https://github.com/ZhuLinsen/daily_stock_analysis.git

# 2. 删除原 remote
git remote remove origin

# 3. 添加新 remote
git remote add origin https://github.com/Cthloveross/Daily-Stock.git

# 4. 首次推送（需要你有新仓库的推送权限；新仓库要是空的或允许强推）
git push -u origin main
```

**优点**：保留原项目所有 commit 历史，后续可以用 `git fetch upstream` 拉取原项目更新。

**若想保留 upstream 跟踪**（可选）：
```bash
git remote add upstream https://github.com/ZhuLinsen/daily_stock_analysis.git
git fetch upstream
# 以后想合并原项目更新：
# git merge upstream/main
```

### 方式 B：完全重置历史，从一个干净的 commit 开始

```bash
cd /Users/cth/Desktop/daily_stock_analysis

# 1. 删除 .git 目录
rm -rf .git

# 2. 重新 init
git init -b main

# 3. 首次 commit
git add .
git commit -m "Initial commit: fork of daily_stock_analysis as Daily-Stock"

# 4. 关联新 remote
git remote add origin https://github.com/Cthloveross/Daily-Stock.git

# 5. 首次推送
git push -u origin main
```

**代价**：丢失原项目所有历史 commit，失去 upstream 跟踪能力。

### 执行前检查

- [ ] 确认 https://github.com/Cthloveross/Daily-Stock 仓库已创建
- [ ] 确认本地 `.env` 已被 `.gitignore` 排除（应该已经是）
- [ ] 确认没有其他敏感文件会被推送（`git status` 检查）
- [ ] 选方式 A 还是方式 B（**强烈建议 A**）

> ⚠️ `git push --force` 之类的破坏性操作请等明确确认后再做。本项目的 `AGENTS.md` 要求任何 git 推送前必须征得用户同意，请遵循。

---

## 附录 B：环境变量速查表

本项目新增或重点使用的环境变量：

| 变量名 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `STOCK_LIST` | string | `AAPL,MSFT,...` | 自选股列表（已改为美股） |
| `MARKET_REVIEW_REGION` | enum | `us` | `us` / `cn` / `both`（已改为 us） |
| `GEMINI_API_KEY` | secret | — | 主 LLM，必填 |
| **`TRADING_STYLE_PROMPT`** | multiline | — | **M1 新增**：个人交易风格 |
| **`MA_PERIODS`** | csv int | `3,5,13` | **M1 新增（可选）**：均线周期 |
| **`ANALYSIS_INTERVAL`** | enum | `1d` | **M2 新增**：`1d` / `15m` / `5m` |
| `LONGBRIDGE_APP_KEY` | secret | — | M2 日内分析必填 |
| `LONGBRIDGE_APP_SECRET` | secret | — | M2 日内分析必填 |
| `LONGBRIDGE_ACCESS_TOKEN` | secret | — | M2 日内分析必填 |
| `TELEGRAM_BOT_TOKEN` | secret | — | 推送渠道（保留） |
| `TELEGRAM_CHAT_ID` | string | — | 推送渠道（保留） |
| `EMAIL_SENDER` / `EMAIL_PASSWORD` | secret | — | 推送渠道（保留） |

完整清单见 `.env.example`。

---

## 结语

这份文档是 `Daily-Stock` 项目的**方向舵**。未来任何改造都应该：

1. **先对齐愿景**（本文件第一章）
2. **再查实施细节**（本文件第七章）
3. **保持边界**（不做第 1.3 节明确砍掉的能力）

如果愿景本身需要调整（比如决定也做港股了，或者彻底放弃日内），请先改这份文档，再动代码。

> "先有文档，再有代码。代码会过时，愿景不会。"

— Maintainer: [@Cthloveross](https://github.com/Cthloveross) · 2026-04
