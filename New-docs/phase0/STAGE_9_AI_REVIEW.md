# Stage 9 · AI 月度复盘 + Templates

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 2（trades）+ Stage 6（trade_style / was_fake_breakout）+ Stage 7（API 骨架）
> 产出：3 个 Jinja 模板 + Prompt + compute_monthly_stats + LLM wrapper + workflow + 前端 Reviews tab

---

## 做了什么

### 1. Prompt（`src/agent/prompts/monthly_retrospective.py`）

- `SYSTEM_MESSAGE` — 严禁 emoji / 加油话，必须中文，800-1400 字，精确引用数据
- `MONTHLY_REVIEW_PROMPT` — 5 个固定中文章节：数据快照 / 与声明风格偏离 / 重复错误 / 最优-最劣共同特征 / 下月可执行建议
- 注入 Phase 上下文（Mirror / Lab / 混合 / 核心）

### 2. Jinja 模板

- `templates/daily_health_check.md.j2` — Telegram 友好，Stage 11 会由 watcher 调用
- `templates/weekly_reality_test.md.j2` — 本周核心数字 + Reality Test 简版（Phase 1 启用周报时用）
- `templates/monthly_retrospective.md.j2` — 外壳，wraps AI body + 附原始 stats JSON 便于核对

### 3. `src/journal/monthly_review.py`

```python
compute_monthly_stats(year, month, portfolio) -> dict        # 纯统计
generate_review(year, month, trading_style, current_phase, dry_run=False) -> (markdown, stats)
run(year_month: str, portfolio, dry_run=False) -> dict      # 端到端 + 入库
```

- `compute_monthly_stats`：总交易数/胜率/profit factor/ DTE 分布/桶胜率/按 trade_style 分组/best 3 & worst 3/ reality test
- LLM 通过原项目 `LLMToolAdapter` 走（Gemini 主，Claude 备；LiteLLM 路由）
- `run` 幂等 upsert 到 `journal_monthly_reviews` 表

### 4. CLI + Workflow

- `python -m scripts.generate_monthly_review --month 2026-03 [--dry-run]`
- `.github/workflows/monthly_review.yml`：每月 1 号 UTC 05:00 cron，默认跑上个月；workflow_dispatch 支持手动指定 `year_month` + `dry_run`。依赖 secrets `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`（任一即可）+ var `PERSONAL_TRADING_STYLE`（可空）

### 5. API

3 个端点加到 `/api/v1/journal/`：

| Method | Path |
|--|--|
| GET | `/reviews` — 按年月倒序列表 |
| GET | `/reviews/{year}/{month}` — 单条详情（null 若未生成）|
| POST | `/reviews/{year}/{month}/generate` — 触发生成（body 支持 `dry_run`） |

### 6. 前端 Reviews tab

`apps/dsa-web/src/components/journal/MonthlyReviewPanel.tsx`：
- 左侧月份列表 + 触发生成（输入 YYYY-MM）
- 右侧 `react-markdown + remark-gfm` 渲染
- JournalPage 加入 `reviews` tab

### 7. 测试

4 个新测试 `src/journal/tests/test_monthly_review.py`：空月 / 混月 / dry_run / upsert 幂等。

**171 backend passed** + frontend lint + build 全绿。

---

## 怎么验证

```bash
# 后端
python -m pytest src/journal/tests/test_monthly_review.py -v

# 完整 dry-run（不需要 LLM key）
python -m scripts.generate_monthly_review --month 2026-03 --dry-run

# 真实 LLM 调用（需要 .env 里 GEMINI_API_KEY / ANTHROPIC_API_KEY 至少一个）
python -m scripts.generate_monthly_review --month 2026-03
sqlite3 data/daily_stock.db "SELECT year_month, length(review_markdown) FROM journal_monthly_reviews"

# 前端
cd apps/dsa-web && npm run build
# /journal → Reviews tab → 看列表 + 渲染
```

---

## 留了什么坑 / 显式延后

- **LLM 未本地联调**：dry-run 走通了；实 LLM 需要 API key。Stage 12 的验收环节会让你跑一次真实生成。
- **Weekly Reality Test workflow cron** 没单独 workflow：周报本身需要的数据都在 `/api/v1/journal/reality-test` 里，前端 Stage 7 已经能渲染；硬要 Telegram 推送再 Phase 1 加 cron。
- **Prompt 回归测试**：只有 dry-run 验证，没做 snapshot comparison。Phase 1 再加。
- **emotional_state / user_notes** 没进 prompt 上下文：目前 Prompt 只引用 AI 计算的数字。Phase 1 在 prompt 里加一段 "最近 3 个月用户自己标的 emotional_state 分布"，会更准确。

---

## 下一步

Stage 10：Agent Skills（option_trader / leap_explorer / trend_follower）。
