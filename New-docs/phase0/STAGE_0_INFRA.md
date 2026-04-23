# Stage 0 · 基础设施铺路

> 状态：✅ 完成于 2026-04-20
> 前置：v4 文档集 (`New-docs/00-08`)
> 产出对应计划：`~/.claude/plans/new-docs-wiggly-yeti.md` Stage 0 节（Claude Code 本地 plan，非仓库文件）

---

## 做了什么

Stage 0 只铺路，不写业务逻辑。目标是让后续 Stage 1-11 有地方落代码。

### 1. 新增模块骨架

5 个新 Python 包 + 一个子包，每个仅含说明性 `__init__.py`（3 行 docstring）：

- [src/journal/](../../src/journal/) — Stage 2 落 CSV 解析、FIFO、Reality Test
- [src/journal/brokers/](../../src/journal/brokers/) — broker-specific 解析器（`moomoo_us.py` 首先落户）
- [src/regime/](../../src/regime/) — Stage 3/4 六维度评分 + 晨报
- [src/breakout/](../../src/breakout/) — Stage 5/6 四层过滤 + 历史回填
- [src/options/](../../src/options/) — Stage 1 OCC/BS/IV
- [src/lab/](../../src/lab/) — Phase 1 激活（Stage 0 只占位）

同时给 journal / regime / breakout / options 加了 `tests/__init__.py`（空文件），方便 pytest 按模块收集。

### 2. config_registry 扩展

[src/core/config_registry.py](../../src/core/config_registry.py) 新增：

- 一个分类 `phase0` (title "Phase 0 (v4)")，display_order=70，位于 backtest 和 uncategorized 之间
- **25 个字段**，按子域分组：

| 子域 | 字段 | display_order |
|--|--|--|
| Journey | `CURRENT_PHASE` / `PHASE_CONTEXT_INJECTION` / `PERSONAL_TRADING_STYLE` | 10-12 |
| Journal | `INBOX_DIR` / `PROCESSED_DIR` / `CSV_FORMAT` | 20-22 |
| Regime | `REGIME_ENABLED` / `REGIME_MIN_SCORE` / `REGIME_THRESHOLD_AGGRESSIVE/STANDARD/CAUTIOUS` / `REGIME_BRIEF_ENABLED` / `REGIME_BRIEF_TIME_ET` / `REGIME_DB_VERSION` | 30-37 |
| Breakout | `BREAKOUT_VOLUME_MIN` / `BREAKOUT_VOLUME_MIN_MULTIPLE` / `BREAKOUT_TIMEFRAMES` / `BREAKOUT_RS_MIN` / `BREAKOUT_RETEST_WINDOW_MIN` / `RETEST_WINDOW_MINUTES` | 40-45 |
| Lab | `LAB_LEAP_DELTA_MIN` / `LAB_LEAP_DELTA_MAX` / `SHADOW_TRADES_ENABLED` | 50-52 |
| Options | `DEFAULT_RISK_FREE_RATE` / `IV_SNAPSHOT_ENABLED` | 60-61 |

所有"启用类开关"（`REGIME_ENABLED` / `REGIME_BRIEF_ENABLED` / `SHADOW_TRADES_ENABLED` / `IV_SNAPSHOT_ENABLED`）默认 **false**，避免 Stage 0 合入就触发业务变化。相应的启用由后续 stage 明确开启。

### 3. `.env.example` 同步

[.env.example](../../.env.example) 在尾部加了一段 Phase 0 注释块，与 `config_registry.py` 的字段一一对应，供本地参考。

> ⚠️ 本仓库 `.gitignore` 里的 `.env.*` 规则把 `.env.example` 也 swallow 了，**此文件不入版本控制**。`config_registry.py` 才是 WebUI 的真源（用户在配置页能看到 Phase 0 分类）。如需让 `.env.example` 进 git，要另开 PR 调 `.gitignore`。

### 4. 新文档目录

[New-docs/phase0/](.) 新建，放 Phase 0 各 stage 的 how-to。本 Stage 0 只落了两份文档：索引 README + 本 how-to。

### 5. CHANGELOG

[docs/CHANGELOG.md](../../docs/CHANGELOG.md) `[Unreleased]` 段追加 1 条：

```
- [chore] phase0 stage 0: 新增 src/journal/、src/regime/、src/breakout/、src/options/、src/lab/ 五个模块骨架与对应 tests 目录；src/core/config_registry.py 注册 phase0 分类与 Journey/Regime/Breakout/Lab/Options 默认字段；.env.example 同步 Phase 0 注释；新增 docs/phase0/README.md 作为分 stage 索引。为 v4 改造后续 stage 1-12 铺路，不触发业务行为变化。
```

---

## 怎么验证

全部命令在 repo 根执行：

```bash
# 1. 5 个新模块能 import
python -c "import src.journal, src.journal.brokers, src.regime, src.breakout, src.options, src.lab; print('OK')"

# 2. phase0 分类 + 25 字段注册成功，原有字段未受影响
python -c "
from src.core.config_registry import get_field_definition, build_schema_response, get_registered_field_keys
keys = get_registered_field_keys()
phase0 = [k for k in keys if get_field_definition(k)['category'] == 'phase0']
assert len(phase0) == 25
assert get_field_definition('STOCK_LIST')['category'] == 'base'
schema = build_schema_response()
assert any(c['category'] == 'phase0' for c in schema['categories'])
print(f'total fields: {len(keys)}; phase0: {len(phase0)}')
"

# 3. 新测试目录可被 pytest 收集（0 tests 不报错）
python -m pytest src/journal src/regime src/breakout src/options -v

# 4. 语法 + 关键 lint
python -m py_compile main.py server.py src/core/config_registry.py
python -m flake8 src/journal src/regime src/breakout src/options src/lab \
    src/core/config_registry.py --select=E9,F63,F7,F82
```

---

## 留了什么坑 / 显式延后

- **没跑** `scripts/ci_gate.sh` 全链路。本机环境缺 `pandas`，deterministic 测试步失败（pre-existing，和 Stage 0 无关）。Stage 12 CI 整合时会一并处理。
- **没改** `requirements.txt`。新依赖（`watchdog`、`scipy` 已有）留到对应 stage 引入。
- **没动** `portfolio_events` schema —— Stage 2 才 ALTER。
- **没注册** prefix inference 到 `_infer_category()`。未显式注册的新 `REGIME_*` 或 `BREAKOUT_*` key 会落到 `uncategorized`。等 Stage 3/5 实现时若发现有必要再补。
- **AGENTS.md / CLAUDE.md 不动** —— 等 Phase 0 整体落地（Stage 12）再一次性改，避免 rebase 冲突。

---

## 下一步

Stage 1：Options 基础层。依赖：无。入口 [src/options/__init__.py](../../src/options/__init__.py) 已就位，开工即可。
