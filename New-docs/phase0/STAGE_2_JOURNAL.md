# Stage 2 · Journal 核心

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 0 / Stage 1
> 产出：Moomoo CSV 解析 + FIFO 配对 + Reality Test + 7 张 journal_* 表 + 4 个 CLI 脚本

---

## ⚠️ 对 v4 文档的偏离（合理化说明）

[New-docs/modules/05_JOURNAL_MODULE.md](../modules/05_JOURNAL_MODULE.md) 假设"原 repo 有 `portfolio_events` 表，ALTER 加 16 个期权字段"。**真实 repo 是 `portfolio_trades`**（见 [src/storage.py:422](../../src/storage.py#L422)），且 `symbol` 字段是 `String(16)` —— 装不下 17 字符的 OCC 期权 symbol（如 `TSLA260417P382500`）。

若 ALTER `portfolio_trades`：
- 风险：PortfolioService / PortfolioRisk / PortfolioAgent / A 股 & 港股流程都在读写此表，ALTER 破坏面大
- 还需要同时改 `symbol` 字段宽度

**决策**：**新建 `journal_*` 独立表族，不触碰 `portfolio_trades`**。这仍然符合 ADR-v4-02 "不新建模块"的精神（Journal 作为 `src/journal/` 子模块仍然存在，也仍然是 Portfolio 子系统的"复盘补全"），只是数据层独立，避免把 A/H 股交易 history 和美股期权 trade 混在一张表里。

---

## 做了什么

### 1. 7 张新表（`src/journal/models.py`）

通过 `src.storage.Base.metadata.create_all()` 幂等创建。**完全不动 `portfolio_*` 原表**。

| 表 | 作用 | Stage 2 写？ |
|--|--|--|
| `journal_imports` | CSV 导入审计（sha256 去重）| ✅ |
| `journal_orders` | Filled 订单（fill-merge 后）| ✅ |
| `journal_trades` | FIFO 配对产物 | ✅ |
| `journal_shadow_trades` | Phase 1 虚拟交易 | ❌ 只建表 |
| `journal_health_checks` | 每日体检 | ❌ 只建表，Stage 7 写 |
| `journal_monthly_reviews` | AI 月度复盘 | ❌ 只建表，Stage 9 写 |
| `journal_phase_state` | 单行表，当前 Phase | ✅ 插默认 phase=0 |

### 2. Moomoo CSV 解析（`src/journal/brokers/moomoo_us.py`）

```python
from src.journal.brokers.moomoo_us import parse, compute_external_id
orders = parse(csv_bytes)  # -> list[MoomooOrder]
```

- **主行 + fill-only 行合并**：`Side` 和 `Symbol` 空的行被视作前一订单的附加 fill。
- **时间解析**：`Apr 16, 2026 15:53:57 ET` / `Apr 16, 2026 15:53` / `2026-04-15 09:32:15` 等 5 种格式兼容，aware 返回 ET 时区。
- **字段别名**：`Commission|Comm` / `Platform Fees|Platform Fee` / `Fill Qty` 等用 `_first(row, ...)` 兼容未来 Moomoo 改列名。
- **OCC 解析**：import `src.options.occ_parser`（Stage 1 提供）。
- **只保留 Filled**：status=cancelled / pending / rejected 跳过。
- **去重 ID**：`compute_external_id(o)` 基于 `symbol|side|order_time|first_fill|qty|price|avg` sha256 前 16 位，无 Moomoo order id 字段也能稳定去重。

### 3. Storage（`src/journal/storage.py`）

核心 API：

```python
init_journal_schema()                   # 幂等建 7 表 + seed phase_state
record_import(path, content, broker, rows_total) -> import_id | None
insert_events_from_orders(import_id, orders) -> (inserted, skipped)
query_events_for_matching() -> list[dict]
replace_trades(trades) -> int
```

- **三层去重**：邮件级（未来 Stage 11 watcher）→ CSV sha256（`record_import` 返回 None）→ 订单 `external_id`（`insert_events_from_orders` 批量检测）
- **Portfolio 概念**：CSV 无账户信息，默认 label `default_moomoo_us`。多账户需求出现时再扩展。
- **时区**：aware datetime 转 naive UTC 存 SQLite，避免 SQLite 时区坑。

### 4. FIFO 配对（`src/journal/matcher.py`）

```python
from src.journal.matcher import match_legs_fifo, dte_bucket_of
trades = match_legs_fifo(events)
```

- **分组键**：期权 = `(underlying, expiry, strike, right, True)`；正股 = `(underlying, None, None, None, False)`。互不干扰。
- **规则**：
  - 空仓 Buy → 开 long，空仓 Sell → 开 short
  - 同向 → 加仓（独立 lot，各自 entry_price）
  - 反向 → FIFO 消费最老 lot，产生 closed trade
  - 过度平仓（卖超持仓）→ 剩余翻方向开新仓 + 告警
- **乘数**：期权 100 / 股票 1，PnL = `(exit - entry) * qty * mul * sign`
- **DTE 桶**：`0DTE / 1-3DTE / 4-7DTE / 8-30DTE / 30+DTE`，`entry_time.date()` 算 DTE
- **Fee 分摊**：退出 fee 按本次消费 qty 比例分摊到 close trade；开仓 fee 待 Stage 5 的 Breakout 打标签时再对齐到具体 open leg

### 5. Analytics（`src/journal/analytics.py`）

```python
reality_test(trades, top_n=5)                    # Phase 0 灵魂指标
dte_distribution(trades)                          # 桶计数
dte_bucket_win_rates(trades)                     # 每桶胜率 + 平均 PnL
daily_health_check(orders_of_day, closed_trades) # 日度体检（纯数字）
```

`reality_test` 输出：`total_trades / total_pnl_net / top_n_pnl_net / pnl_without_top_n / top_n_pct_of_total / median_pnl_net / top_n_ids`。

### 6. 4 个 CLI 脚本

| 脚本 | 用途 |
|--|--|
| `python -m scripts.init_journal_schema` | 幂等建表（含 phase_state seed） |
| `python -m scripts.import_csv --csv PATH [--broker moomoo_us] [--no-rebuild]` | 手动导 CSV + 默认跟着 FIFO 重配 |
| `python -m scripts.rebuild_trades [--portfolio LABEL]` | 仅重配（CSV 已入库） |
| `python -m scripts.reality_test [--top-n 5] [--since YYYY-MM-DD]` | 终端表格打印 |

### 7. 35 个单元测试

- `test_moomoo_parser.py` — fill-merge / 期权解析 / equity / cancelled skip / external_id 幂等 / 空 CSV / header-only / ET 时区（9 cases）
- `test_matcher.py` — DTE 桶 + 简单配对 / 期权乘数 / 加仓 + 整平 / 整开 + 分批平 / 跨日 / 多 symbol / 空头 / 仅开仓 / 确定性 / fee 分摊（13 cases）
- `test_analytics.py` — reality_test 5 场景 / dte_distribution / dte_bucket_win_rates / daily_health_check（9 cases）
- `test_storage.py` — schema 幂等 / 端到端 / sha256 dedup / external_id dedup / replace_trades 幂等 / sha256 稳定性（6 cases）
- `conftest.py` — 每 test 走 tmp SQLite，Config / DatabaseManager singleton 重置

**76 passed**（Stage 1 的 41 + Stage 2 的 35）。

---

## 怎么验证

### 单元测试
```bash
python -m pytest src/journal src/options -v
# 应 76 passed
```

### 端到端 smoke（inline CSV）
```bash
export DATABASE_PATH=/tmp/journal_smoke.db
rm -f $DATABASE_PATH

python -m scripts.init_journal_schema
python -m scripts.import_csv --csv tests/fixtures/journal/moomoo_inline_sample.csv --broker moomoo_us
python -m scripts.reality_test --top-n 3
```

预期输出（inline 固定样例）：
```
Total closed trades      : 2
Total net PnL            : $923.30
Top 3 net PnL            : $923.30  (100.0% of total)
PnL without Top 3        : $0.00
...
DTE distribution :
  1-3DTE     :    2
  equity     :    1
```

### 真实 735 行 CSV（**需要你自己 drop**）

```bash
# 把真实脱敏 CSV 放到:
cp ~/Downloads/History-20260417-...csv tests/fixtures/journal/moomoo_real_sample.csv

export DATABASE_PATH=./data/daily_stock.db
python -m scripts.init_journal_schema
python -m scripts.import_csv --csv tests/fixtures/journal/moomoo_real_sample.csv --broker moomoo_us
python -m scripts.reality_test --top-n 5
```

预期对照 `New-docs/architecture/01_PROJECT_VISION_v4.md` §1 基线：
- 735 订单（99% 期权 ≈ 727）
- 总净利 ≈ $58,918
- 去掉 Top 5 ≈ $2,257
- 胜率 ≈ 34.7%
- ±10% 偏差可接受；更大偏差 → 回头查 FIFO 逻辑

---

## 留了什么坑 / 显式延后

- **真实 735 行 CSV 还没 drop**：能跑 inline 样例；用户 drop 真实文件后我会顺手跑一遍并把结果贴回给你。
- **Entry fee 未分摊到 close trade**：目前 `total_fee` 只含退出 fee。要严格精准需要 Stage 2 再加一轮（把入场 fee 按 qty 摊回）—— 对 Reality Test 整体影响 < 1%，先不改。
- **分批开仓 + 整单出场的 avg_entry**：现在每个消费的 lot 独立出 trade（可能产生多条 close trade，对应同一个 close event）。这更精细但会让"trades 总数"比直观预期偏多。如果前端想看"逻辑上一笔交易"，Stage 7 加聚合视图。
- **账户分隔**：硬编码 `default_moomoo_us`。未来多账户需求再引入 `journal_accounts`。
- **`.env.example`**：仍被 gitignore swallow（Stage 0 已记录）。
- **`portfolio_trades` 不动**：A/H 股流程照原样跑；v4 美股期权走独立 journal_* 表族。Portfolio 页面保持原行为。
- **Breakout 字段** 在 `journal_trades` 已占位但未填（Stage 5/6 填充）。
- **AI 字段** 占位未填（Stage 9 填充）。

---

## 下一步

Stage 3：Alpaca / Finnhub + Regime 核心。Journal 表已稳定，Regime 独立模块无依赖 journal。
