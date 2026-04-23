# Stage 1 · Options 基础层

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 0
> 产出：OCC parser / Black-Scholes / IV Rank / yfinance 期权链抓取 / 2 张缓存表

---

## 做了什么

### 1. OCC 代码解析（`src/options/occ_parser.py`）

两个公开 API：

```python
from src.options.occ_parser import parse_symbol, format_occ
parse_symbol("TSLA260417P382500")
# InstrumentInfo(is_option=True, underlying='TSLA',
#     option=OptionInfo(underlying='TSLA', expiry=date(2026,4,17), right='P', strike=382.5))

parse_symbol("NVDA")
# InstrumentInfo(is_option=False, underlying='NVDA', option=None)

format_occ("TSLA", date(2026,4,17), "P", 382.5)  # → "TSLA260417P382500"
format_occ(..., padded=True)                      # → "TSLA260417P00382500"
```

规则采 ADR-v4-07：`^([A-Z]+)(\d{6})([CP])(\d+)$`，strike 除以 1000（Moomoo 变长）。同时兼容 OCC 8 位零填充形式（parser 自动识别，format 可选 `padded=True`）。畸形输入一律 `ValueError`。

### 2. Black-Scholes（`src/options/black_scholes.py`）

```python
call_price(S, K, T, iv, r=0.045, q=0.0)
put_price(S, K, T, iv, r=0.045, q=0.0)
compute_greeks(S, K, T, iv, right, r=0.045, q=0.0) -> Greeks
price_and_greeks(S, expiry_date, K, iv, right, ...) -> PricingResult
implied_volatility_from_price(market_price, S, K, T, right, ...) -> float
time_to_expiry_years(expiry_date, from_date=None) -> float
```

- Greeks 标尺：delta 对 1 单位 S / gamma 对 1 单位 S / vega 对 1% IV / theta 对 1 日历日 / rho 对 1% 利率。
- 过期 (`T<=0`) / 零 IV 自动退化到内在价值；`compute_greeks` 返回全 0 而非抛错，便于上层批处理。
- IV 反推：Newton-Raphson 最多 100 次，失败后 bisection 兜底，实在不行返回 `math.nan`。
- 默认 `_TRADING_YEAR_DAYS=365`（日历日）；如未来切换到 252 日历日需同步 theta scaling。

### 3. IV Rank（`src/options/iv_rank.py`）

```python
compute_atm_iv(symbol, ref_date=None) -> (iv, expiry_str)
compute_iv_rank(symbol, days_window=252) -> IVRankResult
```

- ATM IV：从 yfinance `option_chain` 拉最近一期 call，用"距离 spot 最近的 strike"作为 ATM。
- IV Rank：真实 IV 历史需要数月积累（`iv_snapshots` 表 Phase 1 才开始写），Stage 1 先用 HV（20 日滚动已实现波动率，年化）作为 fallback 并用百分位排名。`source='hv_fallback'` 标明出处。
- yfinance 未装 / 数据缺 → `rank_pct=None`，不 crash。

### 4. 期权链抓取（`data_provider/options_chain.py`）

```python
from data_provider.options_chain import OptionsChainFetcher
fetcher = OptionsChainFetcher(cache_ttl_seconds=600)
fetcher.get_expirations("NVDA")              # 字符串列表
fetcher.get_chain("NVDA", "2027-01-15", right="C")  # List[OptionQuote]
fetcher.find_leap_candidates("NVDA", delta_min=0.70, delta_max=0.85, min_dte=270)
```

- 内存 TTL 缓存（key: underlying + expiry + right）。
- `persist_cache=True` 时把本次 snapshot 写入 `option_chains_cache`（Stage 1 只有表结构；默认 False，避免 test 副作用）。
- LEAP 候选：yfinance 不暴露 delta，用 moneyness 代理（ITM=0.70, ATM=0.50, OTM=0.30）。精确 delta 请调 `compute_greeks`。

### 5. Schema（`src/options/storage.py`）

幂等建两张表：

- `option_chains_cache(underlying, expiry, right, fetched_at, spot_at_fetch, chain_json, UNIQUE(u,e,r,ft))`
- `iv_snapshots(underlying, snapshot_date, atm_iv, ref_expiry, ref_dte, spot, UNIQUE(u,sd))`

`init_options_schema(conn=None)`：传 conn 就用传的（测试友好），不传就解析 `src.storage.get_db_path` 失败兜底 `data/daily_stock.db`。

### 6. 测试

41 cases 全绿。位置：`src/options/tests/`。

- `test_occ_parser.py` — 期权/正股/变长/零填充/畸形/边界（10 cases）
- `test_black_scholes.py` — Hull 教材值 + 平价 + 内在 + Greek 单调性/符号 + IV 反推 round-trip（17 cases）
- `test_iv_rank.py` — yfinance mock / 过去 expiry 跳过 / HV fallback rank（5 cases）
- `test_options_chain_fetcher.py` — yfinance mock / right 过滤 / 缓存命中 / LEAP 候选（5 cases）
- `test_storage.py` — schema 幂等（2 cases）

---

## 怎么验证

```bash
python -m pytest src/options -v
# 应 41 passed

python -c "
from datetime import date
from src.options.occ_parser import parse_symbol
from src.options.black_scholes import call_price, price_and_greeks

assert parse_symbol('TSLA260417P382500').option.strike == 382.5
assert abs(call_price(42, 40, 0.5, 0.2, r=0.1) - 4.7594) < 0.01
r = price_and_greeks(100, date(2027, 1, 15), 100, 0.25, 'C')
print(f'sanity check: call px={r.price:.2f}, delta={r.greeks.delta:.3f}, theta/day={r.greeks.theta:.4f}')
"
```

依赖：`scipy` / `pandas` / `yfinance`（期权链抓取；本地未装时 fetcher graceful 返回 []）、`fake_useragent`（`data_provider/__init__.py` 的 efinance fetcher 传染性依赖，pre-existing）。

---

## 留了什么坑 / 显式延后

- **真实 IV 历史**：`iv_snapshots` 表建好但未写入。Phase 1 的"IV 每日 snapshot cron"启用后开始积累。Stage 1 的 IV rank 走 HV fallback。
- **精确 delta**：LEAP 候选用 moneyness 代理，不是真实 delta。用户用 `compute_greeks` 自己算更精准；Agent tool 层（Stage 10）会同时提供。
- **期权链数据源**：yfinance 免费但 volume / open_interest 精度不如 Alpaca / Polygon。Stage 3 接入 Alpaca 时可加一个 `OptionsChainFetcher` 的 Alpaca backend。
- **期权流动性打分**：FEATURES.md 说 "spread % / bid-ask ratio" 是 LEAP 流动性指标，未实现。Phase 1 补。
- **no dividend yield**：`q` 默认 0，多数股适用。股息率敏感的标的调用时传 `q`。

---

## 下一步

Stage 2：Journal 核心。依赖 `src.options.occ_parser.parse_symbol`（已就位）。
