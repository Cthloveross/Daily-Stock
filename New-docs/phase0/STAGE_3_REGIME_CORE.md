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

每个 getter 都 defensive：缺 API key / 缺包 / 网络错 → 返回空 dict（scorer 会读成 0 贡献，总分不 crash）。

### 5. 主入口 + 存储

`compute_regime_score(target_date, watchlist=None, save_to_db=True, thresholds=None) -> RegimeResult`

- 默认 `target_date=date.today()`、watchlist 读 `config.stock_list` fallback 到 `['SPY','QQQ','NVDA','AAPL','TSLA']`
- DB 表 `regime_scores`（date PK，d1-d6 + snapshot_json + version）
- 幂等 upsert：同 date 再跑一次覆盖旧值
- `get_regime_score(date)` / `get_recent_scores(days=30)` 读取

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
- **Premarket 数据源**：Alpaca 之外没备选。未配置 Alpaca 时 d6 恒为 0（scorer 下限）—— 不 crash，但信息量低。
- **Watchlist 来源**：优先读 config.stock_list；如果用户只有 A 股 watchlist（v1 默认），`compute_regime_score` 会跑到美股 symbol 也不奇怪——但 scorer 逻辑本身是市场宽度 + 大盘走势，对具体 watchlist 不敏感。
- **反身性列** (`user_perceived_quality`、`user_did_trade`) 已在 schema 占位，Phase 1 激活。
- **thresholds 自定义** 已支持但 CLI 没暴露；够用场景下留 argparse 未加。

---

## 下一步

Stage 4：Regime 晨报 + GitHub Actions cron。依赖 Stage 3 的 `compute_regime_score` + `bot/telegram_bot.py`（原项目已有）。
