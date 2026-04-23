# 01 · Project Vision v4

> **基于真实 repo 的精准定位**  
> 前置阅读：`00_INDEX.md`、`HEALTH_CHECK_REPORT.md`、`BREAKOUT_FILTER_PLAYBOOK.md`  
> 取代：v1/v2/v3 的所有 PROJECT_VISION

---

## 0. 一句话定位

**Daily-Stock v4 是一个"交易者成熟度演进陪跑系统"。基于 ZhuLinsen/daily_stock_analysis 开源框架扩展，主攻美股期权，保留 A/H 股作为侧功能。使命是把用户从"幸运型短期期权投机者"陪跑到"LEAP + 趋势流 + 正股"的成熟交易者。**

---

## 1. 用户画像（定位的起点）

**唯一的主用户**：@Cthloveross，杜克学生，美股期权交易者。

**他的真实画像**（基于 CSV 体检）：
- 3 个月 735 笔订单，99% 期权，85% 是 0-3 DTE
- +$58,918 利润，但 96% 来自 5 笔交易
- 胜率 34.7%，去掉 Top 5 基本打平
- 有隐性方法论（Regime 判断 + 多周期确认 + EMA trail），但未系统化
- 最大痛点：追突破常踩假突破

**他的目标**：转型 LEAP / 趋势流 / 持股。项目使命就是陪他完成这个转型，不是做另一个通用股票分析工具。

**其他用户**：暂时不考虑。项目不做产品化。未来别人 fork 要用，自己看着改。

---

## 2. 保留 / 砍掉 / 新增（基于真实 repo 的三分类）

### 2.1 保留（原项目能力，不动或小改）

| 模块 | 保留理由 |
|--|--|
| `src/agent/` 多 Agent 编排（Technical→Intel→Risk→Specialist→Decision） | 已经做得很好，我们把期权 skill 挂进去就行 |
| `data_provider/` 多数据源抽象（Longbridge/YFinance/AkShare/Tushare 等 fallback 链） | 核心基础设施，不重写 |
| `bot/` Telegram/Discord/Slack/企微/飞书推送 | 现成，扩展用 |
| `strategies/` YAML skill 机制（11 种内置策略） | 新策略走 skill 路径 |
| `apps/dsa-web/` React 前端（双主题、Portfolio 页等） | 现成，扩展路由 |
| `templates/` Jinja2 模板 + `REPORT_RENDERER_ENABLED` | 加期权模板 |
| `src/core/config_registry.py` 集中配置 | 新字段走 registry |
| `api/app.py` FastAPI 路由 | 新增 router 并入 |
| Portfolio 模块（`get_portfolio_snapshot` + `/portfolio` 页面） | 作为 Journal 基础 |
| GitHub Actions `daily_analysis.yml` | 定时任务骨架，新增 workflow |
| A 股 / 港股数据链路 | 保留但不主动维护，README 说明"side 功能" |
| 各种内置指标（MA / 多头排列 / 筹码分布） | 保留，美股期权用不到但 A 股还用 |
| LiteLLM 模型路由 + Multi-key | 保留 |
| Docker 部署 / CI 门禁脚本 | 保留 |

### 2.2 砍掉（明确不要）

| 模块 | 砍的理由 |
|--|--|
| ~~每日固定 18:00 定时分析全 watchlist 推送~~ | 改为事件驱动 + 早盘 Regime 晨报 |
| ~~强调"LLM 决策仪表盘"作为产品核心~~ | 改为"Reality Test 作为核心" |
| ~~"一键分析 → 买入价 / 止损 / 目标"这种确定性口吻~~ | 改为"信号 + 条件 + 反对证据" |
| ~~MA5>MA10>MA20 作为硬规则~~ | 这是 A 股的纪律，对美股期权无效；改成策略可选项 |
| ~~Deep Research / EventMonitor（原项目已标记为未实现）~~ | 原项目已经承认能力漂移，我们不假装恢复 |
| ~~给别人提供 SaaS 入口 / 公开 demo~~ | 纯自用 |

### 2.3 新增（项目核心价值在这里）

| 模块 | 使命 |
|--|--|
| **Journal 模块**（扩展 Portfolio）| Moomoo CSV 自动入库 → FIFO 配对 → AI 复盘 |
| **Reality Test 引擎** | 每周/每月"去掉 Top N"测试，治疗幸运偏差 |
| **Market Regime Classifier** | 六维度评分，每天告诉你今天能不能交易 |
| **Breakout Filter** | 四层假突破过滤，把踩中率从 60% 砍到 30% |
| **三个新 Agent Skill** | `option_trader` / `leap_explorer` / `trend_follower`（走现有 skill 机制） |
| **Shadow Trades** | 虚拟交易，Phase 1 启用，练方向判断力 |
| **Backtest Replayer** | "3 个月前如果我买了 LEAP 会怎样"的反事实模拟 |
| **Phase 进度跟踪** | Journey 模块，记录从 Phase 0 到 Phase 3 的演进 |
| **期权数据支持** | OCC 代码解析、期权链抓取、Greek 计算 |
| **Monthly AI Retrospective** | 月度复盘，含 Reality Test + 行为模式 + 下月规则 |

---

## 3. 三层产品架构（Mirror / Lab / Journey）

```
┌─────────────────────────────────────────────────────┐
│           Daily-Stock v4                             │
│                                                      │
│  ┌─ Mirror（镜子）—— 永远开启 ───────────────┐     │
│  │   使命：让你看清现在是什么样子             │     │
│  │   · Journal（CSV → trades）                │     │
│  │   · Reality Test（首页卡片）              │     │
│  │   · Daily/Weekly/Monthly Health Check     │     │
│  │   · Regime Classifier（每日晨报）         │     │
│  └───────────────────────────────────────────┘     │
│                                                      │
│  ┌─ Lab（实验室）—— 分阶段开启 ───────────────┐   │
│  │   使命：为下一阶段策略提供零风险训练场     │   │
│  │   · LEAP Explorer（Phase 1 激活）         │   │
│  │   · Shadow Trades                          │   │
│  │   · Backtest Replayer                     │   │
│  │   · Trend Scanner（Phase 2 激活）         │   │
│  └────────────────────────────────────────────┘   │
│                                                     │
│  ┌─ Journey（旅程）—— 贯穿全程 ───────────────┐  │
│  │   使命：记录从投机者到投资者的演进过程     │  │
│  │   · Evolution Timeline（DTE 分布变化）    │  │
│  │   · Phase Tracker                          │  │
│  │   · Monthly AI Retrospective              │  │
│  └────────────────────────────────────────────┘  │
│                                                     │
│  ─── 横向支撑 ───                                  │
│  · Agent Skills（option_trader / leap_explorer/   │
│    trend_follower + 原有 11 种）                   │
│  · Bot（Telegram 主，其他保留）                   │
│  · Data Provider（Alpaca + Finnhub 接入现有链）   │
│  · A/H Stock Side（保留但不维护）                 │
└─────────────────────────────────────────────────────┘
```

**三层关系**：
- Mirror 是根基，永远跑
- Lab 按 Phase 激活/切换
- Journey 是时间线

---

## 4. 修订 ADR（架构决策记录）

### ADR-v4-01：主做美股期权，A/H 股作侧功能
**决策**：美股期权是 P0，A 股 / 港股保留但标记"自用不维护"。  
**影响**：README 分两章，`/analyze` 默认美股优先；但 A 股数据链路不删。  
**理由**：原项目以 A 股为主用户，我们真实需求反过来；但砍 A 股代码成本高收益低。

### ADR-v4-02：Portfolio 扩展为 Journal，不新建模块
**决策**：在 `src/agent/tools/` 和 `apps/dsa-web/src/pages/PortfolioPage.tsx` 基础上扩展。  
**影响**：`get_portfolio_snapshot` 工具增加期权字段；Portfolio 页面增加 Journal/Reality Test tabs。  
**理由**：Portfolio 已有完整基础设施（持仓记录、事件列表、券商解析器），重复造轮子没意义。

### ADR-v4-03：新策略走 Agent Skill 机制
**决策**：`option_trader` / `leap_explorer` / `trend_follower` 都是 `strategies/*.yaml` + `SKILL.md` bundle。  
**影响**：不新增独立代码路径，所有能力经 Agent orchestrator 触发。  
**理由**：原项目已支持 11 种 skill，架构成熟；期权分析是一种 skill，不该另起炉灶。

### ADR-v4-04：保留原有报告生成 Pipeline，新增期权专用 Jinja 模板
**决策**：利用 `REPORT_RENDERER_ENABLED` + `templates/` 增加期权报告模板。  
**影响**：`templates/option_decision.md.j2`、`templates/monthly_review.md.j2` 等。  
**理由**：原 Pipeline 已经支持多语言（zh/en）、完整性校验、图片转换，白用。

### ADR-v4-05：数据源优先级调整，接入 Alpaca
**决策**：美股优先级改为 Longbridge → Alpaca → YFinance 兜底。  
**影响**：`data_provider/__init__.py` 路由配置调整；新增 `data_provider/alpaca_fetcher.py`。  
**理由**：Alpaca 免费 Benzinga News + 实时 premarket K 线，Longbridge 不覆盖新闻。

### ADR-v4-06：Journal 走文件夹 watcher，不走 IMAP
**决策**：`watchdog` 监听本地 `~/Daily-Stock-Inbox/`，Moomoo 手动导出 CSV 到该目录。  
**影响**：新增 `src/journal/folder_watcher.py` 常驻服务。  
**理由**：用户是中国身份的 Moomoo US 账户，OpenAPI 不稳；邮件 IMAP 反而慢且易坏。

### ADR-v4-07：期权符号用 OCC 格式但 strike 变长
**决策**：正则 `^([A-Z]+)(\d{6})([CP])(\d+)$`，strike 除以 1000。  
**影响**：`src/journal/instruments.py` 实现。  
**理由**：Moomoo 实际 CSV 是这个格式，不是 OCC 标准 8 位 strike。

### ADR-v4-08：Reality Test 是首页卡片不是子页面
**决策**：`/` 首页顶部醒目显示"去掉 Top 5 的盈亏"。  
**影响**：`HomePage.tsx` 改造。  
**理由**：这是治疗"幸运偏差"的核心工具，不能藏起来。

### ADR-v4-09：Phase 分阶段激活 Lab 工具
**决策**：`src/core/config_registry.py` 加 `CURRENT_PHASE` 字段，前端按 Phase 显示/隐藏 Lab 模块。  
**影响**：Phase 0 只有 Mirror + Shadow Trades；Phase 1 加 LEAP Explorer；Phase 2 加 Trend Scanner；Phase 3 加 Fundamental Deep Dive。  
**理由**：一股脑上全功能会让用户迷失；渐进激活匹配学习曲线。

---

## 5. 不做什么（明确边界）

| 事项 | 态度 | 理由 |
|--|--|--|
| 自动下单 | 永不 | 风控底线 |
| 给用户账户连券商 API 直接交易 | 永不 | 同上 |
| 做 SaaS / 公开给陌生人用 | 不做 | 自用 |
| 期权复杂策略自动构建（spread / butterfly / iron condor） | 不做 | 超出用户当前能力 |
| 自己写 K 线渲染 | 不做 | TradingView Widget |
| 实时 tick 数据抓取 | 不做 | 预算 + 非必需 |
| 期货 / 加密货币 | 不做 | 范围 |
| 归因引擎（插针解释） | 延后到 P3 | 超预算（需 Unusual Whales） |

---

## 6. 预算

| 项 | 月成本 | 备注 |
|--|--|--|
| Alpaca Paper | $0 | Benzinga News + premarket |
| Finnhub Free | $0 | 宏观日历 |
| yfinance | $0 | 兜底 |
| SEC EDGAR / Fed RSS | $0 | 事件 |
| Telegram | $0 | 推送 |
| ScrapeCreators Truth Social | $20 | Trump 推文（Phase 1 再开） |
| LLM（Gemini 主 + Claude 备） | $0 | 用户自有 API Key |
| GitHub Actions | $0 | 免费额度够 |
| **Phase 0 总计** | **$0** | |
| **Phase 1+ 总计** | **$20** | |

---

## 7. 成功标准（12 个月）

基于 HEALTH_CHECK_REPORT 的基线，12 个月后应该达到：

| 指标 | 现状 | 12 月目标 |
|--|--|--|
| 日均订单 | 15 笔 | < 5 笔 |
| 胜率 | 34.7% | 50%+ |
| Profit Factor | 1.36 | 2.0+ |
| Top 5 盈利占比 | 96% | < 70% |
| 0-3 DTE 占比 | 85% | < 30% |
| LEAP + 正股占比 | 0% | 50%+ |
| 开盘 30 分钟下单占比 | 29% | < 10% |
| 心理状态 | 每天紧张 | 按规则不焦虑 |

**衡量方式**：月度 AI Retrospective 自动对比、Phase 进度评估。

**失败条件**：12 个月后以上 6 个以上指标没变 → 不是系统没用，是用户没执行。数据会告诉你在哪个环节脱轨。

---

## 8. 一句话结语

> **不是"Daily-Stock 是美股分析工具"。  
> 是"Daily-Stock 陪 @Cthloveross 用 12-24 个月从期权投机者蜕变成投资者，用数据作为无情但诚实的教练"。**

所有设计、优先级、UI 文案、AI Prompt 都必须回到这个使命。偏离这个，不做。
