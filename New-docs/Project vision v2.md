# Daily-Stock 项目愿景 v2（修订版）

> **v2.0** · 维护者：[@Cthloveross](https://github.com/Cthloveross) · 更新：2026-04-17  
> 本文件取代 v1。v1 的"K 线图 + MA3/5/13 + Prompt 注入"路线**基本作废**，替换为本文描述的方案。

---

## 0. v2 相对 v1 的关键变更

| 维度 | v1 方案 | v2 方案 | 原因 |
|--|--|--|--|
| **图表技术** | Recharts ComposedChart 模拟 K 线 | **TradingView Advanced Chart Widget** iframe 嵌入 | 用户有 TV Pro 会员；Recharts 模拟 K 线是下策 |
| **AI 触发逻辑** | 每小时 cron 扫 watchlist | **事件驱动**（Webhook/异动/新闻命中触发） | 降低 90% AI 调用成本，信噪比高一个量级 |
| **核心价值模块** | 日内 5m/15m 分析 | **Moomoo 交易日志 + AI 复盘** | 唯一真正能提高胜率的功能 |
| **数据预算** | 未提 | **$30/月上限** | 用户约束 |
| **页面结构** | 按内容类型（AI 基本面/新闻/K 线） | **按决策场景**（Today / Stock / Calendar / Journal） | 报表思维 vs 交易者思维 |
| **插针归因引擎** | 未提 | 降级到 **P2 长期目标**（非核心） | 用户不优先 |
| **Longbridge** | M2 必做 | **暂不开户**，用 yfinance + TV Webhook | 用户缓开 |
| **Pine Script 告警** | 未提 | **M2 核心**：Webhook → FastAPI → AI | TV Pro 会员的真正价值 |

---

## 1. 产品定位（微调）

**定位不变**：美股日内 / 短波段交易者的个人辅助系统。

**定位新增两条**：

1. **交易复盘引擎**：这是本项目和市面上所有工具的最大差异点。通过 Moomoo CSV 自动导入交易记录，AI 生成每笔交易的入场/出场逻辑推断、月度行为分析、与用户声明的交易风格的一致性评估。
2. **TradingView 信号中继**：利用用户 TV Pro 会员，通过 Pine Script 告警 + Webhook，把 TV 端的信号桥接到本系统，触发 AI 决策建议并推送到 Telegram。

---

## 2. 预算约束

**数据源月度上限：$30 USD**

这个约束强制以下决策：
- 不用 Polygon.io Starter（$29，吃光预算）
- 不用 Unusual Whales（$48，超预算）
- 不用 Benzinga Pro（$177，远超）
- **ScrapeCreators Trump Truth Social**（$20/月）是唯一付费
- **Alpaca Free Trading 账户自带的 Benzinga News Feed** 是核心免费替代品
- 其他全部免费层：SEC EDGAR RSS、Finnhub 免费、Fed RSS、yfinance news、金十试用

详见 `NEWS_AND_DATA_SOURCES.md`。

---

## 3. 修订后的 ADR 列表

### ADR-002（修订）：图表用 TradingView Widget

**决策**：废弃 Recharts 方案。在单票页 iframe 嵌入 TradingView Advanced Chart Widget。

**实现**：
```html
<!-- 免费，无需 API Key -->
<div class="tradingview-widget-container">
  <div id="tv_chart"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
    new TradingView.widget({
      container_id: "tv_chart",
      symbol: "NASDAQ:NVDA",
      interval: "5",
      studies: [
        { id: "MASimple@tv-basicstudies", inputs: { length: 3 } },
        { id: "MASimple@tv-basicstudies", inputs: { length: 5 } },
        { id: "MASimple@tv-basicstudies", inputs: { length: 13 } },
        { id: "VWAP@tv-basicstudies" },
      ],
      theme: "dark",
      autosize: true,
    });
  </script>
</div>
```

**优点**：视觉一致、指标完备、零维护成本、自动支持盘前盘后。

---

### ADR-006（新）：Pine Script Alert → Webhook → AI

**决策**：M2 核心能力。Pine Script 在 TV 端运行，命中用户定义的进场/止损条件时通过 Webhook POST 到 `POST /api/v1/webhooks/tv`，后端触发 AI 决策建议并推送。

**Pine Script 示例**（用户在 TV 侧保存为 indicator 并设置 alert）：
```pinescript
//@version=5
indicator("DS_Entry_Signal", overlay=true)
ma3  = ta.sma(close, 3)
ma5  = ta.sma(close, 5)
ma13 = ta.sma(close, 13)
vol20 = ta.sma(volume, 20)

bullStack = ma3 > ma5 and ma5 > ma13
volBurst  = volume > vol20 * 2.0
breakout  = close > ta.highest(high[1], 10)

if bullStack and volBurst and breakout
    alert('{"type":"BUY","sym":"' + syminfo.ticker + '","px":' + str.tostring(close) + ',"ma3":' + str.tostring(ma3) + '}', alert.freq_once_per_bar)
```

**后端端点**（FastAPI 骨架）：
```python
@app.post("/api/v1/webhooks/tv")
async def tv_webhook(req: Request, x_token: str = Header(...)):
    if x_token != os.getenv("TV_WEBHOOK_TOKEN"):
        raise HTTPException(401)
    payload = await req.json()
    # 1. 去重（10 分钟内同 symbol 同 type 只处理一次）
    if is_duplicate(payload): return {"skipped": True}
    # 2. 拉上下文（当日 K 线 / 最近新闻 / 当前 VWAP）
    ctx = build_context(payload["sym"])
    # 3. 调 Gemini，用户 TRADING_STYLE_PROMPT
    advice = call_llm_with_style(payload, ctx)
    # 4. 推 Telegram + 写信号表
    send_telegram(advice)
    db.signals.insert({**payload, "advice": advice})
    return {"ok": True}
```

---

### ADR-007（新）：Journal 作为 P0 功能

**决策**：Moomoo 交易日志 + AI 复盘上调到 M1 必做。

**路径**：Moomoo App 定期导出 CSV 到绑定邮箱 → Python IMAP 轮询 → 自动入库 → AI 分析每笔 + 月度复盘。

详见 `MOOMOO_JOURNAL_PIPELINE.md`。

---

### ADR-008（新）：Moomoo OpenAPI 不作为核心集成路径

**决策**：不走 OpenAPI。理由：

1. 用户是中国身份开的 Moomoo US 账户，Moomoo Financial Inc（US 实体）的 OpenAPI 对实盘交易 API 支持受限；
2. OpenAPI 需要本地常驻 OpenD 网关进程，部署复杂度不匹配一个"自用工具"的体量；
3. 用户交易频率低（日几笔到十几笔），半自动（CSV 邮件）与全自动（API 推送）体验差异可忽略。

**未来触发条件**：如果用户切到 IBKR / Alpaca 等 OpenAPI 成熟的券商，再考虑升级。

---

### ADR-009（新）：归因引擎降级到 P2

**决策**：插针归因引擎（整合新闻 + 期权异动 + Trump posts 解释盘中异动原因）是一个有趣的工程目标，但**不作为本阶段核心**。

理由：
- 需要 Unusual Whales（$48/月）才能有"聪明钱"维度，超预算；
- 没有期权维度的归因引擎价值打对折；
- 用户明确表示"不太能作为核心功能"。

**保留作为 M4+ 探索**，等预算或需求变化时再启动。

---

## 4. 修订路线图

### M0（本周）— 文档对齐

- [x] 本文件 v2（PROJECT_VISION_v2.md）
- [x] MOOMOO_JOURNAL_PIPELINE.md
- [x] NEWS_AND_DATA_SOURCES.md
- [ ] 推到新仓库
- [ ] README 顶部替换为 Daily-Stock 品牌

### M1（2-3 周）— 三大主力上线

**前端**：
- [ ] 单票页改造：Tab 结构（图表 / AI 分析 / 新闻 / 我的交易）
- [ ] 图表 Tab：TradingView Widget 嵌入，默认 MA3/5/13 + VWAP
- [ ] 首页改造：Today 面板（事件日历卡片 + Watchlist 状态一屏）
- [ ] Journal 页面骨架：上传 CSV / 手动录入 / 历史列表

**后端**：
- [ ] MA3/5/13 替换（沿用 v1 规划）
- [ ] `TRADING_STYLE_PROMPT` 注入（沿用 v1 规划）
- [ ] **Moomoo CSV 解析器** + **IMAP 轮询服务**（核心，详见 pipeline doc）
- [ ] Journal DB schema（trades / trade_legs / journal_entries / monthly_reviews）
- [ ] 单笔交易 AI 分析任务（异步 job）

**新闻 & 数据**：
- [ ] Alpaca 免费账户申请 + News API 接入
- [ ] SEC EDGAR RSS 订阅（watchlist 15 只）
- [ ] yfinance news 兜底
- [ ] Fed RSS 接入
- [ ] 新闻统一入库 + 去重 + 按 symbol 索引

### M2（2 周）— 信号与事件

- [ ] TV Webhook 端点 + 去重 + AI 决策建议生成
- [ ] ScrapeCreators Trump Truth Social 接入（$20/月付费开启）
- [ ] 结构化事件日历（Event dataclass + DB + 每周日生成周事件清单）
- [ ] 财报日历（yfinance `ticker.calendar`）
- [ ] 事件与 Watchlist 关联（按标的过滤）

### M3（2 周）— 复盘引擎

- [ ] 月度 AI 复盘任务（每月 1 号 00:00 ET 跑）
- [ ] 复盘报告前端页面（Journal/Reviews）
- [ ] 胜率、赢损比、时段分布、策略分类统计等可视化
- [ ] 与 `TRADING_STYLE_PROMPT` 的一致性评估

### M4+（长期观察）

- [ ] 插针归因引擎（预算扩展或 Unusual Whales 试用后启动）
- [ ] IBKR / Alpaca 切换评估（如果 Moomoo 体验撑不住）
- [ ] 财报历史反应库
- [ ] IV Rank 计算

---

## 5. 修订后的页面架构

```
Daily-Stock
├─ 1. Today （默认首页）
│   ├─ 今日事件卡片（财报/Fed/宏观，按时间排）
│   ├─ Watchlist 一屏状态（15 只，价/量/MA 三线状态/AI 色块）
│   ├─ 最新信号（TV Webhook 推来的）
│   └─ 今日新闻流（按重要度过滤）
│
├─ 2. Stock 单票深度 /stock/:symbol
│   ├─ Tab 图表（TradingView Widget，用户可在 TV 侧保存配置）
│   ├─ Tab AI 分析（技术/基本面/新闻综合，Gemini）
│   ├─ Tab 事件（该票财报日历、历史财报日 gap %）
│   ├─ Tab 新闻流（按该票过滤）
│   └─ Tab 我的交易（在此票的历史记录 + 胜率）
│
├─ 3. Calendar /calendar
│   ├─ 本周事件（替代原图文推送）
│   ├─ 财报日历（未来 2 周）
│   └─ Fed 讲话 / 宏观数据倒计时
│
├─ 4. Journal /journal  ★ M1 新增
│   ├─ 上传 CSV（手动） / 邮件自动同步状态
│   ├─ 交易列表（filter by symbol/date/策略/盈亏）
│   ├─ 单笔详情（系统字段 + AI 推断 + 人工备注）
│   ├─ 月度复盘（AI 生成，可订阅推送）
│   └─ 统计看板（胜率、赢损比、时段分布）
│
└─ 5. Settings
    ├─ Watchlist 管理
    ├─ TRADING_STYLE_PROMPT 编辑
    ├─ TV Webhook Token
    ├─ Moomoo 邮箱配置（用于 IMAP 轮询）
    └─ Telegram / 邮件推送
```

---

## 6. 明确砍掉或延后

| 项目 | 状态 | 理由 |
|--|--|--|
| A股 / 港股分析 | 砍 | 非目标市场 |
| 企业微信 / 飞书 / 钉钉推送 | 砍 | 只保留 Telegram + 邮件 |
| K 线图 Recharts 实现 | 砍 | 被 TV Widget 取代 |
| Longbridge 日内数据 | 延后 | 用户缓开户，用 yfinance 兜底 |
| 每小时 cron AI 分析 | 砍 | 被事件驱动（Webhook）取代 |
| 插针归因引擎 | 延后（M4+） | 预算和优先级 |
| Unusual Whales / Polygon 订阅 | 暂不启用 | 超预算 |
| Moomoo OpenAPI 直连 | 不做 | 账户类型 + 部署复杂度 |
| 自动下单 / 组合优化 / 回测增强 | 不做（v1 已明确） | 范围边界 |

---

## 7. 关键约束速查

- **预算**：$30/月 数据订阅 + $0 LLM（用户自有 Gemini key）
- **券商**：Moomoo US（中国身份），走 CSV 邮件
- **主看盘工具**：TradingView Pro（用户已有会员）
- **LLM**：Gemini 主，Claude 备选
- **推送**：Telegram + 邮件，不做其他渠道
- **部署**：GitHub Actions cron + Vercel/本地 FastAPI（维持 v1 架构）

---

> 任何后续改造，先改本文件，再动代码。