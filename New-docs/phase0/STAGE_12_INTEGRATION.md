# Stage 12 · README / AGENTS / CI 整合 + Phase 0 退出评估

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 0-11 全部合入
> 产出：README Phase 0 banner / AGENTS.md 模块索引 / ci_gate.sh 扩展 / Phase 0 Exit Review

---

## 做了什么

### 1. 审计 + 修复（在 Stage 12 开头完成）

对 Stage 1-11 跑了一轮跨层 audit（backend 代码 / API 契约 / 前端集成），共识别 27 个问题。修复：

- **4 critical**：axios multipart Content-Type、compute_external_id DST 漂移、import 无 size limit、regime recompute 无速率限制
- **5 major**：fmtDate 时区偏移、TradingView studies 依赖、导航菜单缺入口、prev_day lookback 6 天跨假期不够、.env.example gitignore
- **5 regression tests** 写进单测矩阵：external_id DST、import size 413、empty 400、filename sanitise、recompute 429 cooldown

**backend 195 passed**，frontend `npm run lint && npm run build` 全绿。

### 2. 文档与项目元信息

- [README.md](../../README.md) 顶部加 **v4 Phase 0 Mirror 层 banner**（不替换原有 A 股 / 港股说明，按 ADR-v4-01 "side feature 保留" 原则）
- [AGENTS.md](../../AGENTS.md) §3 仓库速览加 Phase 0 新模块索引
- [New-docs/phase0/PHASE_0_EXIT_REVIEW.md](PHASE_0_EXIT_REVIEW.md) 新增——正式退出评估报告，含：
  - 交付清单对照 `03_MIGRATION_PLAN.md` §1.2
  - 硬性指标对照（`§1.3`）现况
  - 27 个问题的严重度分布 + 已修 fix 表
  - 与 v4 文档的合理偏离清单
  - 用户待办六步（drop CSV / 配 key / 回补 regime / 跑月报 / 配 Secrets / 准备 Phase 1）
  - 打 tag `v0.phase0` 建议 + 四项用户签字

### 3. CI gate 扩展

[scripts/ci_gate.sh](../../scripts/ci_gate.sh) 的 `syntax_check` 追加了：

```bash
python -m py_compile src/options/*.py src/journal/*.py src/journal/brokers/*.py
python -m py_compile src/regime/*.py src/breakout/*.py
python -m py_compile src/agent/tools/get_regime_score_tool.py \
    src/agent/tools/get_option_chain_tool.py \
    src/agent/tools/check_breakout_tool.py \
    src/agent/tools/get_journal_snapshot_tool.py
python -m py_compile api/v1/endpoints/journal.py \
    api/v1/endpoints/regime.py api/v1/endpoints/breakout.py
```

`offline_test_suite` 已经用 `pytest -m "not network"`，新目录自动被收进。

### 4. CHANGELOG 最终状态

`[Unreleased]` 现有 **13 条** Phase 0 条目：stage 0-12（stage 12 待补）。按 AGENTS.md §1 规则保持扁平格式，由 maintainer 发版时再整理。

---

## 怎么验证

```bash
# 一遍跑
./scripts/ci_gate.sh syntax
./scripts/ci_gate.sh flake8
python -m pytest src/options src/journal src/regime src/breakout \
                 src/agent/tools/tests api/v1/tests bot/commands/tests
# 期望 195 passed

# 前端
cd apps/dsa-web && npm run lint && npm run build

# 查看 README banner
head -40 README.md
```

---

## 留了什么坑 / Phase 1 入口

- **用户行为验收**（4 项）签字未完成，见 [PHASE_0_EXIT_REVIEW.md §八](PHASE_0_EXIT_REVIEW.md#八签字)
- **DB 同步策略** 待定：regime_brief workflow 把 `regime_scores` 留在 runner tmpfs。Phase 1 要么 commit 回 repo、要么切 Turso / Supabase
- **Bundle size** 1.3 MB，Phase 1 用 `React.lazy` 拆 JournalPage / RegimePage / TradingViewWidget
- **deterministic_checks**（`./test.sh code` / `yfinance`）没加 Phase 0 模块覆盖——这些是原 repo 的 A 股测试，Phase 0 的测试由 `offline_test_suite` 覆盖，分工合理
- **Lab 模块** 空目录待 Phase 1 填（LEAP Explorer / Shadow Trades / Backtest Replayer）

---

## Phase 0 正式结论

代码、测试、文档三个维度全部就位。**待用户本人用真实数据跑一轮即可关闭 Phase 0**，之后进入 Phase 1（Lab 激活期）。
