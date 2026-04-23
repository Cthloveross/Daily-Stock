# Phase 0 · 退出评估报告

> 日期：2026-04-20
> 范围：v4 改造 Stage 0-12 全部落地
> 决策：**是否进入 Phase 1**？

---

## 一、交付清单（对照 `New-docs/architecture/03_MIGRATION_PLAN.md` §1.2）

### 代码

| 目标 | 完成状态 | 位置 |
|--|--|--|
| `src/options/` — OCC parser / BS / IV rank / chain fetcher | ✅ | [src/options/](../../src/options/) |
| `src/journal/` — Moomoo CSV / FIFO / Reality Test / 7 张新表 | ✅ | [src/journal/](../../src/journal/) |
| `src/regime/` — 6 维度评分 / Alpaca+Finnhub / Backfill | ✅ | [src/regime/](../../src/regime/) |
| `src/breakout/` — Detector + Q1-Q5 filter + 历史回填 | ✅ | [src/breakout/](../../src/breakout/) |
| `src/lab/` 骨架（Phase 1 启用） | ✅（占位） | [src/lab/](../../src/lab/) |
| Agent tools × 4（regime / option_chain / breakout / journal_snapshot） | ✅ | [src/agent/tools/](../../src/agent/tools/) |
| Agent skills × 3（option_trader / leap / trend） | ✅ | [strategies/](../../strategies/) + [.claude/skills/](../../.claude/skills/) |
| Folder watcher + 3 bot 命令 | ✅ | [src/journal/folder_watcher.py](../../src/journal/folder_watcher.py) + [bot/commands/](../../bot/commands/) |
| AI 月度复盘 | ✅ | [src/journal/monthly_review.py](../../src/journal/monthly_review.py) |

### API 端点

| 端点 | 状态 |
|--|--|
| `/api/v1/journal/*`（reality-test / trades / stats / import / reviews） | ✅ 10 个 |
| `/api/v1/regime/*`（today / history / recompute） | ✅ 3 个 |
| `/api/v1/breakout/signals` | ✅ |

### 前端

| 目标 | 完成 |
|--|--|
| Journal 页（Overview / Trades / Reality / Reviews / Import） | ✅ |
| Regime 页（score / 30 日历史图 / breakout signals） | ✅ |
| 首页 Reality Test + Regime Score 双卡 | ✅ |
| TradingView Widget 组件 | ✅ |
| 侧边导航加 `/journal` + `/regime` 入口 | ✅ |

### CI / 自动化

| 目标 | 完成 |
|--|--|
| `.github/workflows/regime_brief.yml`（EDT/EST 双 cron） | ✅ |
| `.github/workflows/monthly_review.yml`（月初 cron） | ✅ |
| `scripts/ci_gate.sh` 扩展 py_compile 新模块 | ✅ |

### 文档

| 目标 | 完成 |
|--|--|
| `New-docs/phase0/STAGE_0_INFRA.md` - `STAGE_11_AUTOMATION.md` | ✅ 12 份 |
| `New-docs/phase0/PHASE_0_EXIT_REVIEW.md`（本文件） | ✅ |
| README 顶部 Phase 0 banner | ✅ |
| `AGENTS.md` 加 Phase 0 模块索引 | ✅ |
| `docs/CHANGELOG.md` 每 stage 一条 `[Unreleased]` 条目 | ✅ 12 条 |

---

## 二、硬性指标对照（`03_MIGRATION_PLAN.md` §1.3）

| 指标 | 目标 | 实际 | 状态 |
|--|--|--|--|
| Daily Health Check 连续 2 周每日推送 | ≥ 10 天 | **未达成** — watcher + health_check 代码就位，但需要用户先 drop 真实 CSV + 配 Telegram | ⚠ 待验收 |
| 至少 1 份 Monthly AI Retrospective 已读完 | ≥ 1 | **未达成** — 代码路径就位（含 dry-run），等用户跑 `generate_monthly_review --month 2026-03` | ⚠ 待验收 |
| Regime Classifier 回补过去 3 个月完整 | ≥ 60 交易日 | **未达成** — `python -m src.regime.backfill --days 90` 可一键跑，但需要 Alpaca/Finnhub key | ⚠ 待验收 |
| 用户能回答 "去掉 Top 5 我 3 个月盈亏" | 要 | **未达成** — 等用户 drop 真实 735 行 CSV 跑 `reality_test` | ⚠ 待验收 |
| 用户主动说出 "我想试 LEAP 了" | 用户主观 | — | 用户决定 |

**结论**：Phase 0 的**代码层面**目标全部达成，**行为层面**需要用户本人跑一轮真实数据才能签字。

---

## 三、测试与质量

| 维度 | 数字 |
|--|--|
| backend pytest（含 5 个 regression 测试） | **195 passed** |
| frontend `npm run lint` | ✅ |
| frontend `npm run build` | ✅（bundle 1.3MB，Phase 1 可考虑 code-split） |
| `./scripts/ci_gate.sh syntax` | ✅（扩展覆盖新模块） |
| flake8 critical checks | ✅ 0 errors |

---

## 四、审计阶段发现的问题与修复

对 Stage 1-11 做了跨层 audit（代码 / API 契约 / 前端），共识别 **27 个问题**，处理情况：

**Critical（4 个，已修）**：
1. `apps/dsa-web/src/api/journal.ts::importJournalCsv` 显式 Content-Type 会破坏 multipart boundary → 删除 header
2. `src/journal/brokers/moomoo_us.py::compute_external_id` DST 切换会产生不同 id → 新增 `_canonical_ts` 统一转 UTC
3. `/api/v1/journal/import` 无 size limit（OOM 风险） + 文件名注入 → 加 50 MB 限制 + `Path.name` sanitize
4. `/api/v1/regime/recompute` 无速率限制 → 进程内 60s 冷却 + 429 返回

**Major（5 个，已修）**：
5. `TradeTable::fmtDate` 前端对 naive UTC 时间戳按本地时区解析（偏移 4-8 小时）→ 追加 `Z` 后缀
6. `TradingViewWidget` `useEffect` 依赖 `studies` 数组每次 render 都变 → 用 `studies.join('|')` 作为稳定 key
7. 侧边导航没加 `/journal` + `/regime` 入口 → `SidebarNav.tsx` 补 2 项
8. `regime/fetchers.py::get_prev_day_structure` 6 天 lookback 跨 3-day holiday 会漏 → 改 14 天
9. Stage 0 的 `.env.example` 被 `.gitignore` swallow 导致不入版本控制 → 已在 [STAGE_0_INFRA.md](STAGE_0_INFRA.md) 记录，保留本地参考

**Minor（剩 18 个）**：保留到 Phase 1（详情见各 `STAGE_N_*.md` 的"留了什么坑"段）。

**新增 5 个 regression tests**：
- `test_moomoo_parser.py::test_external_id_stable_across_dst_boundary`
- `test_journal_endpoints.py::test_import_rejects_oversized_upload`
- `test_journal_endpoints.py::test_import_rejects_empty_upload`
- `test_journal_endpoints.py::test_import_sanitises_path_traversal_filename`
- `test_regime_breakout_endpoints.py::TestRegimeRecompute::test_cooldown_triggers_429`

---

## 五、与 v4 文档的偏离

| 偏离项 | 原因 | 文档 |
|--|--|--|
| Stage 2 用独立 `journal_*` 表族，不 ALTER `portfolio_trades` | 实际表的 `symbol` 是 `String(16)`，装不下 17 字符 OCC + 已深度耦合 A 股流程 | [STAGE_2_JOURNAL.md](STAGE_2_JOURNAL.md) |
| `src/options/occ_parser.py` 作为 OCC 真源，Journal / LEAP 都从这里 import | `05` 文档把 OCC parser 的 import 路径写成 `src.journal.instruments`，但 `04` 文档定位在 `src/options/` | [STAGE_1_OPTIONS.md](STAGE_1_OPTIONS.md) |
| Stage 6 没上 AI 打标签（只走规则） | 控 CI 确定性 + LLM 费用 | [STAGE_6_BREAKOUT_BACKFILL.md](STAGE_6_BREAKOUT_BACKFILL.md) |
| 没写 `.github/workflows/breakout_backfill.yml` | Phase 0 CSV 盘后日频，hourly cron 过度；watcher 触发 rebuild 更自然 | [STAGE_6_BREAKOUT_BACKFILL.md](STAGE_6_BREAKOUT_BACKFILL.md) |

所有偏离都符合 v4 "扩展优于重写" 原则，未破坏原有功能。

---

## 六、用户待办（Phase 0 → Phase 1 过渡）

把这几件事跑一遍就能把"代码完成"升级为"Phase 0 真正完成"：

1. **Drop 真实 Moomoo History CSV** 到 `tests/fixtures/journal/moomoo_real_sample.csv`（或 `~/Daily-Stock-Inbox/`）
   ```bash
   python -m scripts.init_journal_schema
   python -m scripts.import_csv --csv ~/Downloads/History-xxx.csv
   python -m scripts.reality_test --top-n 5
   # 预期：约 735 订单、去掉 Top 5 约 +$2,257（对照 HEALTH_CHECK_REPORT.md）
   ```

2. **配 API keys**（`.env` 里加）：
   ```
   APCA_API_KEY_ID=...
   APCA_API_SECRET_KEY=...
   FINNHUB_API_KEY=...
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   GEMINI_API_KEY=...  # 月度复盘用
   ```

3. **回补 Regime + 跑一次晨报 smoke**：
   ```bash
   python -m src.regime.backfill --days 90
   python -m src.regime.morning_brief --format-only
   ```

4. **生成一份月度复盘试读**：
   ```bash
   python -m scripts.generate_monthly_review --month 2026-03
   # 前端 /journal → Reviews tab 看渲染效果
   ```

5. **GitHub Actions 配置**：
   - Secrets: `APCA_*` / `FINNHUB_API_KEY` / `TELEGRAM_*` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`（任一 LLM 即可）
   - 手动 dispatch `regime_brief.yml` 验证 Telegram 收到

6. **Phase 1 启动前**：
   - 阅读 `New-docs/architecture/01_PROJECT_VISION_v4.md` §3 里 Lab 模块激活条件
   - 给 `.env` 里设 `CURRENT_PHASE=1` + `SHADOW_TRADES_ENABLED=true`
   - 根据 Phase 1 计划开 `src/lab/` 下的模块（LEAP Explorer / Shadow Trades / Backtest Replayer）

---

## 七、打 tag 建议

Phase 0 代码落地完成，可以打 tag `v0.phase0`（annotated）：

```bash
git tag -a v0.phase0 -m "Phase 0 Mirror layer complete — Journal + Regime + Breakout + Agent skills"
# NOT pushing until user confirms
```

（`AGENTS.md` §1 硬规则：`git push` 需用户确认）

---

## 八、签字

- [ ] **@Cthloveross**：我已跑过真实 CSV，看到 Reality Test 数字
- [ ] **@Cthloveross**：我已收到至少一次 Regime 晨报 Telegram
- [ ] **@Cthloveross**：我已读完至少一份 AI 月度复盘
- [ ] **@Cthloveross**：我想试 LEAP 了 → **Phase 1 启动**

所有框打勾之前，代码保持在 `v0.phase0` tag，行为侧不开新 feature。
