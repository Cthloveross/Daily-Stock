# Phase 0 · Mirror 层改造文档

本目录收录 Daily-Stock v4 改造 Phase 0 各 stage 的 how-to 与执行记录。战略文档（愿景 / 架构 / 路线图）在仓库根的 `New-docs/` 下，不重复。

> 🚀 **第一次用？先看这里 → [HOW_TO_USE.md](HOW_TO_USE.md)**
> 一张页回答"我怎么跑起来 / 每天做什么 / API 在哪 / 出问题查哪"。
>
> 🔑 **要配 API key → [API_KEYS_SETUP.md](API_KEYS_SETUP.md)**
> 7 个服务的完整申请步骤（Alpaca / Finnhub / Telegram / Gemini / Claude / 等），免费/付费清单，填到哪，怎么验证。

## Stage 索引

Stage 0-12 依次对应 `.claude/plans/new-docs-wiggly-yeti.md` 里的分阶段实施计划。

| Stage | 说明 | 文档 |
|--|--|--|
| 0 | 基础设施铺路（模块骨架 + config 字段 + 文档目录）| [STAGE_0_INFRA.md](./STAGE_0_INFRA.md) |
| 1 | Options 基础层（OCC / BS / IV） | [STAGE_1_OPTIONS.md](./STAGE_1_OPTIONS.md) |
| 2 | Journal 核心（CSV / FIFO / Reality Test）| [STAGE_2_JOURNAL.md](./STAGE_2_JOURNAL.md) |
| 3 | Alpaca / Finnhub + Regime 核心 | [STAGE_3_REGIME_CORE.md](./STAGE_3_REGIME_CORE.md) |
| 4 | Regime 晨报 + GitHub Actions cron | [STAGE_4_REGIME_BRIEF.md](./STAGE_4_REGIME_BRIEF.md) |
| 5 | Breakout Detector + 四层过滤 | [STAGE_5_BREAKOUT.md](./STAGE_5_BREAKOUT.md) |
| 6 | Breakout 历史回填 + AI 打标签 | [STAGE_6_BREAKOUT_BACKFILL.md](./STAGE_6_BREAKOUT_BACKFILL.md) |
| 7 | 前端 Mirror 核心（Journal + Reality Test）| [STAGE_7_FRONTEND_JOURNAL.md](./STAGE_7_FRONTEND_JOURNAL.md) |
| 8 | 前端 Regime + Breakout + Today 首页 | [STAGE_8_FRONTEND_REGIME.md](./STAGE_8_FRONTEND_REGIME.md) |
| 9 | AI 月度复盘 + templates | [STAGE_9_AI_REVIEW.md](./STAGE_9_AI_REVIEW.md) |
| 10 | Agent Skills（option / leap / trend） | [STAGE_10_AGENT_SKILLS.md](./STAGE_10_AGENT_SKILLS.md) |
| 11 | Folder Watcher + bot 命令 | [STAGE_11_AUTOMATION.md](./STAGE_11_AUTOMATION.md) |
| 12 | README / docs / CI 整合 + Phase 0 退出评估 | [STAGE_12_INTEGRATION.md](./STAGE_12_INTEGRATION.md) + [PHASE_0_EXIT_REVIEW.md](./PHASE_0_EXIT_REVIEW.md) |

## 约定

- 每个 stage 落地时在此目录新建对应的 `STAGE_N_*.md`，描述本 stage 的"装了什么 / 怎么验证 / 留了什么坑"
- stage 之间可读，但不强依赖其他 stage 的文档（用仓库根 `New-docs/` 做真源）
- 完成 stage 12 后，本目录加 `PHASE_0_EXIT_REVIEW.md` 做最终评估，Phase 0 打 `v0.phase0` tag

## 当前状态

| Stage | 状态 | 日期 |
|--|--|--|
| 0 | ✅ 完成 | 2026-04-20 |
| 1 | ✅ 完成 | 2026-04-20 |
| 2 | ✅ 完成 | 2026-04-20 |
| 3 | ✅ 完成 | 2026-04-20 |
| 4 | ✅ 完成 | 2026-04-20 |
| 5 | ✅ 完成 | 2026-04-20 |
| 6 | ✅ 完成 | 2026-04-20 |
| 7 | ✅ 完成 | 2026-04-20 |
| 8 | ✅ 完成 | 2026-04-20 |
| 9 | ✅ 完成 | 2026-04-20 |
| 10 | ✅ 完成 | 2026-04-20 |
| 11 | ✅ 完成 | 2026-04-20 |
| 12 | ✅ 完成 | 2026-04-20 |

**Phase 0 全部 stage 完成 · 195 backend tests passed · frontend lint+build 全绿。**
**待用户行为验收**（4 项签字见 [PHASE_0_EXIT_REVIEW.md](./PHASE_0_EXIT_REVIEW.md) §八）**后打 `v0.phase0` tag。**
