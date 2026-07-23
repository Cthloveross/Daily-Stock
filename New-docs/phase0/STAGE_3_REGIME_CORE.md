# Stage 3 · Alpaca/Finnhub + Regime 核心

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 0
> 产出：2 个新数据源 + 6 维度 Regime 评分 + DB 落地 + 回补 CLI

---

## 做了什么

### 1. Alpaca 数据源（`data_provider/alpaca_fetcher.py`）

REST 直连（不上 SDK，`requests` 够用）：

```python
from data_provider.alpaca_fetcher import AlpacaFetcher
a = AlpacaFetcher()  # 读 APCA_API_KEY_ID / APCA_API_SECRET_KEY
a.configured                           # bool
a.get_bars('SPY', '5Min', start, end) # List[dict]
a.get_news(['NVDA','TSLA'], limit=50)  # Benzinga feed
a.get_premarket('SPY')                  # latest 1Min bar
```

**未配置时**：每个方法返回空列表/空 dict + WARN 日志。不 crash。

### 2. Finnhub 数据源（`data_provider/finnhub_fetcher.py`）

```python
from data_provider.finnhub_fetcher import FinnhubFetcher
f = FinnhubFetcher()   # reads FINNHUB_API_KEY
f.get_economic_calendar(from_, to)  # CPI/NFP/FOMC 等宏观事件
f.get_earnings_calendar(from_, to, symbol=None)
f.get_recommendation_trends('NVDA')
```

同样 graceful fallback。

### 3. Regime 六维度打分（`src/regime/scorers.py`）

纯函数，范围固定：

| 维度 | 函数 | 范围 | 输入 keys |
|--|--|--|--|
| d1 市场方向 | `score_market_direction(spy)` | 0–30 | `close`, `ma20`, `ma50`, `pct_change_5d` |
| d2 波动率 | `score_volatility(vix)` | -15 – 20 | `level`, `pct_change_5d` |
| d3 宏观惩罚 | `score_macro_penalty(events)` | -50 – 0 | `fomc_today`, `cpi_today`, `nfp_today`, `earnings_count_watchlist`, `tariff_headline_today` |
| d4 板块轮动 | `score_sector_rotation(sectors)` | -5 – 15 | `sectors_above_ma20`, `defensive_leaders` |
| d5 前日结构 | `score_prev_day_structure(prev_day)` | -2 – 13 | `close_vs_high_pct`, `prev_day_range_pct` |
| d6 盘前 | `score_premarket_activity(premarket)` | 0 – 20 | `spy_pre_pct`, `watchlist_up_5pct`, `watchlist_down_5pct` |

总分理论 [-72, 98]；classifier 映射到 4 档：
- score ≥ 75 → **aggressive**
- score ≥ 55 → **standard**
- score ≥ 35 → **cautious**
- 其余 → **no_trade**

### 4. 数据聚合（`src/regime/fetchers.py`）

`RegimeDataFetcher` 把 Alpaca / yfinance / Finnhub 编排成 scorer 需要的 dict：

- SPY 60 日 close 系列 → MA20/MA50 + 5d % change
- VIX 20 日 close
- Finnhub 经济日历 + 财报日历
- 11 个 S&P sector ETF 的 close vs MA20（定义 `sectors_above_ma20` + `defensive_leaders`）
- SPY 前日 Low/High/Close → `close_vs_high_pct`
- Alpaca premarket SPY + 每只 watchlist symbol（前 20 只）

每个 getter 都 defensive：缺 API key / 缺包 / 网络错不会 crash。`regime-quality-v1`
通过 `_status=ready|degraded|unavailable` 声明输入质量；空或未就绪输入的 0 只表示
“未计分”，不再被解释成看空/看多证据。

当前只读取数顺序与预算：

- SPY、11 个板块 ETF 与昨日结构优先复用项目 `DataFetcherManager` 中已启用的
  `MoomooFetcher`；昨日结构复用同一份 SPY 日线，不重复请求
- VIX 先尝试 Moomoo；本机 OpenD 若返回 `Unknown stock. VIX`，回退到
  [Cboe 官方每日 VIX 历史 CSV](https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv)，
  单次 timeout 3 秒，不用 VIX ETF 代理冒充现货指数
- Moomoo 不可用时，仅 SPY 允许一次 3 秒 yfinance fallback；板块域直接标为
  unavailable，不再串行发起 11 次远端 fallback
- Finnhub 经济日历与财报由逐日十几次请求合并为两个 7 日区间请求，自动实例单次 timeout
  2 秒；403/timeout 会显式标为 degraded/unavailable，不能被当成“今日无事件”
- Alpaca 先读 SPY；SPY 失败便停止 watchlist fan-out，成功时也最多检查 5 个标的并按覆盖
  完整度降级。整次 Regime fetcher 使用 18 秒工作预算，后续可选域不会挤占核心行情。

### 5. 主入口 + 存储

`compute_regime_score(target_date, watchlist=None, save_to_db=True, thresholds=None) -> RegimeResult`

- 默认 `target_date` 使用 `America/New_York` 市场日期、watchlist 读 `config.stock_list` fallback 到 `['SPY','QQQ','NVDA','AAPL','TSLA']`
- DB 表 `regime_scores`（date PK，d1-d6 + snapshot_json + version）
- 幂等 upsert：同 date 再跑一次覆盖旧值并刷新 UTC `generated_at`
- `get_regime_score(date)` / `get_recent_scores(days=30)` 读取

`snapshot_json.quality` 保存六域质量。仅 `ready` 是权威分数；核心 SPY/VIX 不完整时
为 `unavailable`，用 `score=0` + `label=unavailable` fail closed，但不得把该占位值
解释为真实 `no_trade`；核心完整而支持域缺失时为 `degraded`。下游应先检查
`state` / `authoritative` / `domain_states` / `incomplete_domains`，再读取分数。
`degraded` 的数字分档仅作市场背景，不得产生允许或阻止交易的 action hint。

### 6. CLI

```bash
python -m src.regime.cli                          # 今天
python -m src.regime.cli --date 2026-04-17        # 某天
python -m src.regime.cli --no-save --verbose      # 只打分不入库 + 打印 snapshot

python -m src.regime.backfill --days 90            # 过去 90 天（跳 weekend；节假日待 Phase 1 补）
```

### 7. 35 个单测

- `test_scorers.py` — 6 维度各 2-4 case：极端边界、空 dict 安全、sum 落在期望带（17 cases）
- `test_classifier.py` — 阈值边界 + 好/差日端到端 + upsert（7 cases）
- `test_fetchers.py` — yfinance/Finnhub mock，缺 key graceful、MA 计算、板块计数、前日结构（7 cases）
- `test_storage.py` — schema 幂等 / save+fetch / upsert / recent / missing（5 cases）

---

## 怎么验证

### 单测
```bash
python -m pytest src/regime src/options src/journal -v
# 应 111 passed (41 + 35 + 35)
```

### 手动打分（需要 API key）
```bash
export APCA_API_KEY_ID=...        # 可选
export APCA_API_SECRET_KEY=...    # 可选
export FINNHUB_API_KEY=...        # 可选
python -m src.regime.cli --verbose
```

没 key 也能跑（scorer 用空 snapshot 会得到基础分数，上层会 WARN）。

### 回补 90 天
```bash
python -m src.regime.backfill --days 90
sqlite3 data/daily_stock.db "SELECT date, score, label FROM regime_scores ORDER BY date DESC LIMIT 10"
```

---

## 留了什么坑 / 显式延后

- **晨报推送 + GitHub Actions cron** → Stage 4
- **交易日历精度**：`backfill` 只跳 weekend，不查美国联邦假日。用 `src.core.trading_calendar` 精化留到 Phase 1。
- **Premarket 数据源**：Alpaca 之外没备选。未配置、SPY 请求失败或 watchlist 覆盖不完整时
  d6 的 0 表示未计分，整体质量标为 `degraded`，不是“盘前平盘”证据。
- **Watchlist 来源**：优先读 config.stock_list；如果用户只有 A 股 watchlist（v1 默认），`compute_regime_score` 会跑到美股 symbol 也不奇怪——但 scorer 逻辑本身是市场宽度 + 大盘走势，对具体 watchlist 不敏感。
- **反身性列** (`user_perceived_quality`、`user_did_trade`) 已在 schema 占位，Phase 1 激活。
- **thresholds 自定义** 已支持但 CLI 没暴露；够用场景下留 argparse 未加。

---

## 下一步

Stage 4：Regime 晨报 + GitHub Actions cron。依赖 Stage 3 的 `compute_regime_score` + `bot/telegram_bot.py`（原项目已有）。
