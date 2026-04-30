# Moomoo OpenAPI 接入路线图

> 目标：用 Moomoo OpenD daemon 替换 / 增强当前以 yfinance + 手动 CSV 导入为主的数据链路，逐步走向"实时行情 + 实时交割单"的闭环。
>
> 操作风险按 phase 递增；**默认全部关闭**（`MOOMOO_OPEND_ENABLED=false`）。每个 phase 都能独立验收、独立回滚。

---

## 现状（无 moomoo 时）

| 数据 | 来源 | 缺陷 |
|---|---|---|
| 美股 K 线 | yfinance | 日线 OK；intraday 配额紧（1m=7d / 5m=60d）；偶尔抽风 |
| 美股实时报价 | yfinance + 长桥兜底 | 延迟 15 分钟；非交易时段空 |
| 期权链 / IV | `data_provider/options_chain.py`（yfinance） | 行权价偶有缺失；greeks 自己算 |
| 交割单 | 手动 export Moomoo CSV → `/api/v1/journal/import` | 每次手动；FIFO 可能滞后 |
| 仓位 | 不存在 | UI 上看不到当前持仓 |
| 板块 / 经纪席位 | 无 | regime 第 4 维只能用 ETF 代理 |

---

## Phase 配额表

| Phase | 范围 | 风险 | 状态 |
|---|---|---|---|
| **A · 行情读取** | K 线 + 报价走 OpenD | 低（只读） | ✅ 完成 |
| **B · 实时交割单** | 后端定时拉持仓/订单/成交 | 中（read-only 账户） | ✅ 完成 |
| **C · 期权链 + IV** | 期权链替换 yfinance | 中 | ✅ 完成 |
| **D · 实时订阅 + 突破检测** | KLine_1M push 喂 breakout detector | 中 | ✅ 完成 |
| **E · 模拟下单 / 真实下单** | TrdEnv.SIMULATE → 后续解锁 LIVE | **高**，必须用户显式开 | ❌ 不规划 |

---

## ✅ Phase A · 行情读取（已实现）

### 落地

- [data_provider/moomoo_fetcher.py](data_provider/moomoo_fetcher.py) — 新建 `MoomooFetcher(BaseFetcher)`
  - `_fetch_raw_data` 走 `request_history_kline(K_DAY)` + 标准化为 `STANDARD_COLUMNS`
  - `fetch_intraday(stock_code, interval, days)` 同 `YfinanceFetcher` 签名，覆盖 `1m / 5m / 15m / 30m / 60m / 1h`
  - `get_realtime_quote(stock_code)` 走 `get_market_snapshot([code])`，返回 `UnifiedRealtimeQuote`（`source=RealtimeSource.MOOMOO`）
  - SDK 懒加载：未装 `moomoo-api` 时静默 shelve（priority=99），不影响其他 fetcher
  - OpenQuoteContext 用 RLock + 单例，多线程安全
- [data_provider/base.py](data_provider/base.py)
  - `DataFetcherManager.__init__` 把 `MoomooFetcher()` 加入 `_fetchers`
  - `get_intraday_data` 路由现在是 **Moomoo 优先 → yfinance 兜底**（仅当 `MoomooFetcher.priority < 99` 即 SDK + ENABLED 同时满足）
- [data_provider/realtime_types.py](data_provider/realtime_types.py) — `RealtimeSource` 枚举加 `MOOMOO = "moomoo"`
- [.env.example](.env.example) + [requirements.txt](requirements.txt) — 新增配置入口（默认全注释，opt-in）

### 启用步骤

```bash
# 1) 装 OpenD（macOS）— 让 install-moomoo-opend skill 跑，或手动从 https://openapi.moomoo.com/ 下
# 2) 装 Python SDK
pip install moomoo-api>=10.4.6408

# 3) 启动 OpenD app，登录你的 Moomoo 账号

# 4) 改 .env
echo 'MOOMOO_OPEND_ENABLED=true'   >> .env
echo 'MOOMOO_OPEND_HOST=127.0.0.1' >> .env
echo 'MOOMOO_OPEND_PORT=11111'     >> .env

# 5) 重启 uvicorn / 重启分析器
# 日志里应看到：[MoomooFetcher] enabled OpenD=127.0.0.1:11111 priority=2
# 这之后 /api/v1/stocks/AMZN/history?period=5m 会自动走 Moomoo（实时），
# 失败再 fallback 到 yfinance。
```

### 验收

```bash
# 后端：
curl 'http://localhost:8000/api/v1/stocks/AMZN/history?period=daily&days=10'   # 看 stock_name 是否 "Amazon.com Inc"
curl 'http://localhost:8000/api/v1/stocks/AMZN/history?period=5m&days=2'       # intraday，检查 date 是否带时区

# 服务日志应看到 [Moomoo] history_kline daily/intraday code=US.AMZN ...
# 关掉 OpenD，重启后端 → fetcher shelve，自动用 yfinance；功能不掉
```

### 回滚

```bash
# .env 改 MOOMOO_OPEND_ENABLED=false 或删行；下次重启自动回到 yfinance-only
```

---

## ⏳ Phase B · 实时交割单同步

**目标**：用 `OpenSecTradeContext.history_order_list_query` + `history_deal_list_query` 拉真实交易记录，喂给现有 [src/journal/](../src/journal/) FIFO 管线，**不再要手动导出 CSV**。

### 设计

```
src/journal/brokers/moomoo_live.py      ← 新建
    fetch_orders_since(start_date, env=PaperOrLive) → list[OrderEvent]
    fetch_deals_since(start_date, env)              → list[DealEvent]
    convert_to_journal_orders(events)               → list[dict]（与 moomoo_us.parse 同 shape）

scripts/sync_moomoo_live.py             ← 新建 cron 入口
    每 15 分钟 / 每小时跑一次 → fetch_deals_since(last_synced) → 走 record_import + match_legs_fifo

api/v1/endpoints/journal.py             ← 增量
    POST /api/v1/journal/sync-live      ← 手动触发同步（前端按钮）
```

**幂等保障**：moomoo 给每条 order 一个稳定 `order_id`；用它当 `external_id`，重复同步不会插重复记录（沿用 `record_import` 现有 sha256 + external_id 双重去重）。

### 风险与缓解

| 风险 | 缓解 |
|---|---|
| 误用 `LIVE` 账户读真实账户 | 默认 `TrdEnv.SIMULATE`；live 模式必须 `MOOMOO_TRADE_ENV=LIVE` 显式开 |
| OpenD 断线丢同步 | `last_synced_ts` 持久化到 `journal_phase_state` 表；恢复后从断点继续 |
| Moomoo trade context 需要登录密码 | `unlock_trade(password)` 由用户**首次手动**在 OpenD GUI 登录，不持久化密码 |

### 验收

- 跑 `python scripts/sync_moomoo_live.py --simulate` → 看到 N 条订单 import；`/journal` 看到对应 trades
- 重跑 → `inserted=0, skipped=N`（幂等）

---

## ⏳ Phase C · 期权链 + IV 替换

**目标**：当前 [data_provider/options_chain.py](../data_provider/options_chain.py) 走 yfinance 拉期权链，IV 自己算（Black-Scholes 反推）。Moomoo 给的链更全 + 自带 IV/Delta/Gamma/Theta/Vega。

### 设计

- 给 `OptionsChainFetcher` 加一个 `_source: 'yfinance' | 'moomoo'` 属性
- `MOOMOO_OPEND_ENABLED=true` 时优先 moomoo，回落 yfinance
- moomoo: `OpenQuoteContext.get_option_chain(code, start, end)` + `get_option_expiration_date(code)`
- moomoo greeks 直接用，避免我们的 BS 反推数值漂移

### 影响面

- [src/options/iv_rank.py](../src/options/iv_rank.py) 减少对 BS 反推的依赖
- [src/lab/](../src/lab/) LEAP explorer 拿到更稳定的 IV 历史

---

## ⏳ Phase D · 实时订阅 + 突破检测

**目标**：用 KLine_1M / Ticker 实时推送，喂 [src/breakout/detector.py](../src/breakout/detector.py)，把"事后回填的 trade_style"升级为"盘中实时打标"。

### 设计

```
src/breakout/live_runner.py             ← 新建
    init_subscriptions(watchlist_tickers)
    on_kline_push(handler) → run_filter(Q1..Q5) → 命中 → push 到 bot/Telegram

scripts/run_breakout_live.py            ← 常驻进程
```

**配额管理**：用户自选 50 只 ticker × KLine_1M = 50 格；够用，但要在 `unsubscribe` 旧的 ticker 时小心 1 分钟冷却。

### 影响面

- bot/commands/regime_cmd.py 加新指令 `/breakout-live` 查看当前订阅状态
- 前端 BreakoutSignalsList 增加 "live" 标签，区分历史回填 vs 实时

---

## ❌ Phase E · 下单（不规划）

明确**不在路线图内**：

- 项目定位是 review & analysis，不是执行系统
- 一旦后端能调 `place_order`，CI / 测试 / 误调用都是真金白银风险
- 如果未来要做，必须：
  - 单独的 `EXECUTION_ENABLED=true` 顶级开关
  - 强制 `TrdEnv.SIMULATE` 默认；切 LIVE 要 `MOOMOO_TRADE_ENV_LIVE_CONFIRMED=I_UNDERSTAND_THE_RISK` 这种笨拙 flag
  - 全部 order endpoint 走独立 router + middleware audit log
  - 不在主 main.py 路径上

---

## 整体环境变量速查

```bash
# Phase A
MOOMOO_OPEND_ENABLED=false           # 总开关
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111
MOOMOO_PRIORITY=2                    # fetcher 优先级；默认 2，排在 yfinance(4) 前

# Phase B (待办)
MOOMOO_TRADE_ENABLED=false           # 拉真实账户开关
MOOMOO_TRADE_ENV=SIMULATE            # SIMULATE | LIVE
MOOMOO_SYNC_INTERVAL_MINUTES=15

# Phase E (永不打开 unless explicit)
EXECUTION_ENABLED=false
```

---

## 与已有 skill 的关系

- 装在 `~/.claude/skills/install-moomoo-opend` 和 `~/.claude/skills/moomooapi`：**Claude Code 交互**用，不是 daily_stock_analysis 后端运行时依赖
- 后端依赖只有 `pip install moomoo-api`（OpenD 二进制是用户自己装在系统层级）
- `New-docs/integrations/moomoo-subscription.md` 是写给人看的速查；本文是路线图

---

_Phase A 完成于 [当前 commit]，下一步看 Phase B 的同步脚本与 endpoint。_
