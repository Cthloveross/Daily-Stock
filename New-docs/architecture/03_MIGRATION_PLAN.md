# 03 · Migration Plan（改造路线图）

> **目的**：把 12 个文档的改造工作组织成 4 个 Phase，每个 Phase 有明确交付物和退出条件  
> **前置**：`01_PROJECT_VISION_v4.md`、`02_ARCHITECTURE_OVERVIEW.md`

---

## 0. 总览

```
Phase 0 ────────────── Phase 1 ────── Phase 2 ────── Phase 3
[0-6 周]              [6 周-3 月]    [3-6 月]       [6-12 月]

项目层面：             项目层面：       项目层面：     项目层面：
- 搭完 Mirror 层        - 搭 Lab 层     - 完善 Lab    - 维护模式
- 历史数据回补          - LEAP Explorer - Trend Scan  - 基本面深化
- Regime + Breakout     - Shadow Trades - 持仓跟踪    - 月度 review 精细化
  投入生产                               
                                                    
交易行为层面：          交易行为层面：  交易行为层面：  交易行为层面：
- 不改                  - 减频 +         - 40/40/20    - 20/50/30
  只看数据               开始 LEAP        实盘混合     核心持仓为主
                         shadow
```

---

## 1. Phase 0 — Mirror 建设期（0-6 周）

### 1.1 使命
**把"照镜子"工具搭完**。不要求改变交易行为，要求能从数据上看清自己。

### 1.2 交付物

#### Week 1：Journal 基础
- [ ] `src/journal/instruments.py` — OCC 解析
- [ ] `src/journal/csv_parser.py` — Moomoo CSV 解析
- [ ] `src/journal/storage.py` — orders 入库
- [ ] `src/journal/matcher.py` — FIFO 配对
- [ ] `src/journal/analytics.py` — Reality Test + Health Check
- [ ] 真实 CSV（735 行）端到端跑通
- [ ] 单元测试全部通过

#### Week 2：Regime Classifier
- [ ] `src/regime/data_fetcher.py` — 六维度数据拉取
- [ ] `src/regime/scorers.py` — 六个打分函数
- [ ] `src/regime/classifier.py` — 主函数
- [ ] `src/regime/morning_brief.py` — 接入 bot/telegram_bot.py
- [ ] `data_provider/alpaca_fetcher.py` — 新数据源
- [ ] `data_provider/finnhub_fetcher.py` — 新数据源
- [ ] `.github/workflows/regime_brief.yml` — cron
- [ ] 历史 3 个月回补 regime_scores 表

#### Week 3：Breakout Filter
- [ ] `src/breakout/filter.py` — 四层过滤决策树
- [ ] `src/breakout/volume_check.py`
- [ ] `src/breakout/timeframe_check.py`
- [ ] `src/breakout/rs_check.py`
- [ ] trades 表加字段（trade_style、pre_filter_pass、volume_multiple 等）
- [ ] 历史 trades 批量打标签（AI 辅助）

#### Week 4：前端 Mirror 层
- [ ] `apps/dsa-web/src/pages/JournalPage.tsx`
- [ ] `src/components/journal/RealityTestCard.tsx`（首页卡片）
- [ ] `src/components/journal/DTEDistribution.tsx`
- [ ] `src/components/regime/RegimeScoreCard.tsx`
- [ ] `HomePage.tsx` 集成 Reality Test 顶部卡片
- [ ] `PortfolioPage.tsx` 加 Journal / Reality Test tabs

#### Week 5：AI 复盘 + Templates
- [ ] `templates/daily_health_check.md.j2`
- [ ] `templates/weekly_reality_test.md.j2`
- [ ] `templates/monthly_retrospective.md.j2`
- [ ] `src/journal/monthly_review.py` — 月度 AI 复盘
- [ ] `src/agent/prompts/option_coach.py` — 期权专用 Prompt
- [ ] 生成 2026-01 / 02 / 03 三份月报并人工 review
- [ ] Prompt 迭代到质量稳定

#### Week 6：整合 + 文档
- [ ] README 改写
- [ ] `.env.example` 加所有新字段示例
- [ ] `CLAUDE.md` 更新
- [ ] `AGENTS.md` 更新
- [ ] CI 绿（flake8 + pytest 全过）
- [ ] `docs/phase0/` 目录放这 12 份文档

### 1.3 Phase 0 退出条件

**硬性指标**（全部满足才能进 Phase 1）：
- [ ] Daily Health Check 连续 2 周每天推送（不是"能推送"而是"真推送了"）
- [ ] 至少 1 份 Monthly AI Retrospective 已读完
- [ ] Regime Classifier 回溯过去 3 个月的数据已完整
- [ ] 用户能精确回答 3 个问题：
  - "去掉 Top 5 我 3 个月盈亏是多少？" → 约 +$2,257
  - "我 0DTE vs 1-3DTE 哪个胜率更高？" → 1-3DTE
  - "我 TSLA +$18K 那天的 Regime Score 是多少？" → （根据实际数据）
- [ ] 用户主动说出"我想试 LEAP 了"

**软性指标**（不强求但希望）：
- [ ] 0-3 DTE 占比从 85% 降到 75%（自然下降，不是强迫）
- [ ] 开盘 30 分钟下单从 29% 降到 20%

### 1.4 Phase 0 不做

- 不做 LEAP Explorer（放 Phase 1）
- 不做 Shadow Trades（放 Phase 1）
- 不做 Trend Scanner（放 Phase 2）
- 不做 Backtest Replayer（放 Phase 1）
- 不做强制减频工具（放 Phase 1）

Phase 0 专注"让数据把你拍醒"。

### 1.5 详细 Week 1 计划

见 `12_WEEK_1_SPRINT.md`（Day 1 到 Day 7 逐日拆解）。

---

## 2. Phase 1 — Lab 激活期（6 周-3 月）

### 2.1 使命
**引入 LEAP / 趋势流的学习工具，开始减频**。但不要求实盘仓位调整，用 Shadow Trades 练。

### 2.2 交付物

#### 新增模块
- [ ] `src/lab/leap_explorer.py`
- [ ] `src/lab/shadow_trades.py`
- [ ] `src/lab/backtest_replayer.py`
- [ ] `src/options/black_scholes.py`（Delta / Theta / Gamma 估算）
- [ ] `src/options/chain_analyzer.py`
- [ ] `src/agent/skills/leap_explorer.py` + `strategies/leap_explorer.yaml`
- [ ] `src/agent/skills/trend_follower.py` + `strategies/trend_follower.yaml`

#### 前端扩展
- [ ] `apps/dsa-web/src/pages/LabPage.tsx`
- [ ] `src/components/lab/LEAPExplorer.tsx`
- [ ] `src/components/lab/ShadowTradeForm.tsx`
- [ ] `src/components/lab/BacktestReplayer.tsx`
- [ ] `src/components/options/OptionChainTable.tsx`
- [ ] `src/components/options/GreeksPanel.tsx`

#### 行为层
- [ ] "强制减频配额"系统（Daily Health Check 显示今日剩余配额）
- [ ] Shadow Trade 强制每周至少 3 笔
- [ ] 每周 Shadow vs 实盘对比报告（自动生成）

#### 数据源扩展（可选）
- [ ] `data_provider/scrapecreators_trump.py`（Trump Truth Social）— 需开启 $20/月订阅

### 2.3 Phase 1 退出条件

- [ ] 连续 4 周每周至少 3 笔 shadow trades
- [ ] 至少 5 次 Backtest Replayer 使用
- [ ] 0-3DTE 占比从 85% 降到 70% 以下
- [ ] 用户开了第一笔实盘 LEAP 仓位
- [ ] Shadow Trades 数据显示用户的方向判断力（去掉 theta 后）优于实盘

---

## 3. Phase 2 — 策略混合期（3-6 月）

### 3.1 使命
**实盘三桶运行**。40% 短期期权 / 40% LEAP / 20% 现金或正股。

### 3.2 交付物

#### 新增模块
- [ ] `src/lab/trend_scanner.py`（周线 / 月线 breakout 扫描）
- [ ] `src/portfolio/bucket_allocator.py`（三桶管理）
- [ ] `src/options/atr_position_sizing.py`
- [ ] 财报跟踪（yfinance calendar + Finnhub 分析师）

#### 前端扩展
- [ ] `PortfolioPage` 扩展：按桶显示仓位
- [ ] `src/components/lab/TrendScanner.tsx`
- [ ] `src/components/portfolio/BucketAllocation.tsx`
- [ ] 长期持股跟踪页（财报日历、分析师变动）

### 3.3 Phase 2 退出条件

- [ ] 三桶运行 3 个月，无"挪仓越界"
- [ ] LEAP 桶累计回报 ≥ 短期期权桶
- [ ] 用户能说出每只 LEAP 持仓的 2-3 个持有理由

---

## 4. Phase 3 — 核心持仓期（6-12 月）

### 4.1 使命
**长期持仓为主**。80% LEAP / 正股，20% 短期期权"娱乐额度"。

### 4.2 交付物

#### 新增模块
- [ ] 深度基本面 AI 摘要（10-K / 10-Q 自动分析）
- [ ] 股东信分析器
- [ ] 年化回报 / Sharpe / Max DD 长期统计
- [ ] Quarterly Review 模板

#### 前端扩展
- [ ] Fundamental Deep Dive 页
- [ ] 长期回报 dashboard
- [ ] 年度复盘 report 生成器

### 4.3 Phase 3 使命评估

12 个月后对照 v1 里定义的成功标准表格。达标 → 项目价值兑现；不达标 → 调整 Phase 3+。

---

## 5. 时间轴汇总

| 时间 | Phase | 关键里程碑 |
|--|--|--|
| Week 1 | 0 | Journal 基础跑通（第一份月报可读） |
| Week 2-3 | 0 | Regime + Breakout 上线 |
| Week 4-5 | 0 | 前端 Mirror 层完成 |
| Week 6 | 0 | Phase 0 退出条件评估 |
| Month 2 | 1 | Lab 上线，Shadow Trades 开始 |
| Month 3 | 1 | 第一笔实盘 LEAP |
| Month 4-6 | 2 | 三桶运行，逐步调整 |
| Month 6-12 | 3 | 长期持仓为主 |

---

## 6. 风险与回退

### 6.1 风险识别

| 风险 | 概率 | 影响 | 缓解 |
|--|--|--|--|
| Moomoo CSV 格式变动 | 低 | Journal 失效 | 多字段别名 + 失败时告警 |
| Alpaca 账户审核不过 | 中 | Regime Classifier 质量下降 | 已有 yfinance fallback |
| Gemini API 限流 | 中 | Monthly Review 延迟 | LiteLLM 切 Claude 备用 |
| 用户 Phase 0 就放弃 | 高 | 项目废 | 日推 Telegram 不能让他逃避 |
| upstream 大改破坏兼容 | 低 | merge 冲突加剧 | 锁 fork 版本，不强求同步 |
| GitHub Actions 免费额度超限 | 低 | cron 不跑 | 本地 cron 备用 |
| 中国身份网络访问 Telegram | 高 | 推送失败 | 邮件 fallback |

### 6.2 回退方案

如果某个 Phase 延期严重：
- Phase 0 延 2 周是可接受的（工程性延期）
- Phase 0 延 4 周以上 → 砍某些功能（如前端先凑合，重点保后端）
- Phase 1 延期 → Shadow Trades 是底线，其他可延

如果核心假设被证伪：
- 假设"用户看到 Reality Test 会改变" — 如果 3 个月后用户行为没变，**不是系统没用，是用户没决心**。项目继续跑，但预期调整。
- 假设"LEAP 会比短期期权在震荡市更好" — 如果 Phase 2 数据显示相反，重新评估策略配比。

---

## 7. 不放进这份路线图的事

以下事情**不是**路线图的一部分，不做也不影响主线：

- 做 Demo 视频 / 录屏（浪费时间）
- 写中英文双语 README（优先度低）
- 做移动端 App（Web 够用）
- 做自动发推文 / 公众号（非自用）
- 积累用户做 SaaS（永不）
- 回测框架大升级（不是核心需求）
- TradingView Widget 深度定制（iframe 够用）

---

## 8. 本路线图的使用方式

1. **Phase 0 进行时**：每周结束对照交付物打勾，没完成的推到下周
2. **Phase 退出评估**：用"退出条件"的硬性指标打分，不是拍脑袋
3. **每月 review**：本路线图是否还合理？需不需要调整？
4. **Phase 1 启动前**：重新审视本文档，根据 Phase 0 的实际情况调整 Phase 1 细节
5. **v5 时机**：Phase 1 结束时可能会出 `03_MIGRATION_PLAN_v5.md`，因为到时候你自己对需求理解会更深

---

## 9. 结语

**本路线图的设计原则**：
- 工程进度可以延，**心态进化不能跳阶**
- Phase 0 不强迫改交易，但必须看数据
- Phase 1 开始学新东西，但账户会先变差（接受）
- Phase 2-3 是慢功夫，别急

**最大的误区**："我已经有想法了，直接跳到 Phase 2 吧"——**不行**。必须按阶段走。Phase 0 的"看清自己"是 Phase 1 动力的来源；Phase 1 的"shadow 对比"是 Phase 2 信心的来源。

跳过的代价是 Phase 4 不存在——你会回到原点。

---

## Batch 1 到此结束

读完 00 索引 + 01 愿景 + 02 架构 + 03 路线图这 4 份后，如果没根本性异议，告诉我"继续 Batch 2"——我会产出：
- `04_OPTION_SUPPORT_EXTENSION.md`（OCC 解析 + Greek + 期权链）
- `05_JOURNAL_MODULE.md`（Portfolio 扩展 + CSV 解析 + FIFO 配对 + AI 复盘）

如果有根本性异议（比如"我不想分 Phase，一次上"或"A 股还是要主做"），告诉我哪一条，我调整后再继续。
