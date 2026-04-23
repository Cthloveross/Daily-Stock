# 📚 Daily-Stock 文档中心

> **定位**：这是整个仓库的文档入口。所有 `.md`（除 `docs/CHANGELOG.md` 和仓库根的 `README.md` / `AGENTS.md` / `SKILL.md`）都在这里。
> **维护者**：[@Cthloveross](https://github.com/Cthloveross)
> **最后更新**：2026-04-21

---

## 🗺️ 快速导航

想做什么 → 看哪一类：

| 你要… | 目录 |
|---|---|
| 第一次跑通这个项目 | [`user-guide/`](./user-guide/) — 从安装到每日流程 |
| 看系统怎么组织、为什么这么设计 | [`architecture/`](./architecture/) — 愿景 + 架构总览 + 改造路线 |
| 改/扩展某个模块（Journal / Regime / Breakout / Options / Agent Skill） | [`modules/`](./modules/) |
| 改 Web 前端样式 | [`design/Design_system.md`](./design/Design_system.md) — UI 的唯一真源 |
| 部署（本地 / Docker / Zeabur / 桌面端） | [`deployment/`](./deployment/) |
| 配置（LLM / 图像提示词） | [`configuration/`](./configuration/) |
| 给这个项目贡献代码 | [`contributing/`](./contributing/) |
| 接入机器人（Telegram / 钉钉 / Discord / 飞书）或外部服务 | [`integrations/`](./integrations/) |
| 看 Phase 0（美股期权 + Journal + Regime + Breakout）落地细节 | [`phase0/`](./phase0/) |
| 查看旧版本文档 | [`archive/`](./archive/) |

> **变更日志** 不在这里 —— 在 [`docs/CHANGELOG.md`](../docs/CHANGELOG.md)（自动化与 AGENTS.md 硬规则依赖该路径）。

---

## 📂 目录结构

```
New-docs/
├── README.md                       ← 本文件（总索引）
│
├── architecture/                   战略 · 架构 · 路线图
│   ├── 01_PROJECT_VISION_v4.md     项目愿景 v4（基于真实 repo 的精准定位）
│   ├── 02_ARCHITECTURE_OVERVIEW.md 架构总览 + 目录树 + 改造点标注
│   └── 03_MIGRATION_PLAN.md        Phase 0-3 改造路线 + 里程碑
│
├── modules/                        模块设计（具体怎么实现）
│   ├── 04_OPTION_SUPPORT_EXTENSION.md   OCC 解析 / Greek / IV rank / chain 抓取
│   ├── 05_JOURNAL_MODULE.md        Moomoo CSV 导入 + FIFO 配对 + Reality Test + 月度复盘
│   ├── 06_REGIME_CLASSIFIER.md     六维度 Regime Score + 四档分类 + 晨报推送
│   ├── 07_BREAKOUT_FILTER.md       Q1-Q5 四层过滤 + 历史 trade_style 回填
│   └── 08_AGENT_SKILL_REGISTRY.md  option_trader / leap_explorer / trend_follower 三个 skill
│
├── design/                         UI 设计系统
│   └── Design_system.md            Linear-Dark 终端风 · 所有视觉与组件契约的源头
│
├── user-guide/                     使用手册（怎么跑起来、每天怎么用）
│   ├── Daily_workflow_guide.md     ⭐ 每日使用手册（从早到晚 + 应急情境）
│   ├── full-guide.md               完整环境变量 / CLI / 部署 / 排障（中文）
│   ├── full-guide_EN.md            同上（英文）
│   ├── README_CHT.md               繁体中文快速上手
│   ├── README_EN.md                英文快速上手
│   ├── FAQ.md                      常见问题（中文）
│   ├── FAQ_EN.md                   常见问题（英文）
│   └── TUSHARE_STOCK_LIST_GUIDE.md Tushare 股票列表获取工具说明
│
├── configuration/                  可选配置
│   ├── LLM_CONFIG_GUIDE.md         LLM 三档配置：极简 / 渠道模式 / YAML 路由（中文）
│   ├── LLM_CONFIG_GUIDE_EN.md      同上（英文）
│   └── image-extract-prompt.md     图片持仓识别的 prompt 说明
│
├── deployment/                     部署
│   ├── DEPLOY.md                   本地 / Docker 部署（中文）
│   ├── DEPLOY_EN.md                同上（英文）
│   ├── deploy-webui-cloud.md       云服务器 Web 界面访问指南
│   ├── desktop-package.md          Electron 桌面端打包
│   └── zeabur-deployment.md        Zeabur 一键部署
│
├── contributing/                   贡献指南
│   ├── CONTRIBUTING.md             怎么提 PR / 分支策略 / 双语文档同步（中文）
│   ├── CONTRIBUTING_EN.md          同上（英文）
│   └── INDEX_EN.md                 英文版文档索引
│
├── integrations/                   外部集成
│   ├── bot-command.md              机器人命令大全（中文）
│   ├── bot-command_EN.md           同上（英文）
│   ├── openclaw-skill-integration.md  通过 Openclaw Skill 调用 DSA API
│   └── bots/                       每个平台的配置图文指南
│       ├── dingding-bot-config.md  + 8 张截图
│       ├── discord-bot-config.md
│       └── feishu-bot-config.md    + 6 张截图
│
├── phase0/                         Phase 0 Mirror 层（美股期权 + Journal + Regime + Breakout）
│   ├── README.md                   Phase 0 分 stage 索引
│   ├── HOW_TO_USE.md               ⭐ TL;DR + 每日/每周/每月流程 + CLI/API/bot 速查
│   ├── API_KEYS_SETUP.md           Alpaca / Finnhub / yfinance API Key 申请
│   ├── PHASE_0_EXIT_REVIEW.md      Phase 0 退出评估
│   ├── STAGE_0_INFRA.md            模块骨架 + config_registry 扩展
│   ├── STAGE_1_OPTIONS.md          OCC 解析 + Black-Scholes + IV rank + options_chain
│   ├── STAGE_2_JOURNAL.md          journal_* 7 张表 + Moomoo broker + FIFO matcher
│   ├── STAGE_3_REGIME_CORE.md      六维度 scorer + classifier + fetcher + storage
│   ├── STAGE_4_REGIME_BRIEF.md     晨报模板 + Telegram 推送 + GitHub workflow cron
│   ├── STAGE_5_BREAKOUT.md         range_high/low 检测 + Q1-Q5 过滤
│   ├── STAGE_6_BREAKOUT_BACKFILL.md 历史 trade 回填 trade_style / fake_breakout
│   ├── STAGE_7_FRONTEND_JOURNAL.md /journal 路由 + 7 API 端点
│   ├── STAGE_8_FRONTEND_REGIME.md  /regime 路由 + TV widget
│   ├── STAGE_9_AI_REVIEW.md        月度复盘 AI prompt + 模板
│   ├── STAGE_10_AGENT_SKILLS.md    三个 Claude skill bundle + 新增 Agent tools
│   ├── STAGE_11_AUTOMATION.md      folder watcher + Telegram 命令
│   └── STAGE_12_INTEGRATION.md     Phase 0 整合 + banner + CI 扩展
│
└── archive/                        归档（旧版本 / 一次性盘点）
    ├── PROJECT_VISION_v1-3.md      旧愿景（已被 architecture/01 取代）
    └── review_2026-03-19.md        2026-03 仓库问题梳理
```

---

## 🔑 三条常走路径

### 1️⃣ 第一次接触这个项目

```
README.md (仓库根) → New-docs/user-guide/full-guide.md → New-docs/user-guide/Daily_workflow_guide.md
```

大约 1 小时能知道：它是干嘛的、怎么跑起来、每天怎么用。

### 2️⃣ 要改代码

```
New-docs/architecture/02_ARCHITECTURE_OVERVIEW.md → 找到你要改的模块 → 读对应 modules/*.md
```

`architecture/02` 的目录树标注了"哪个文件改哪几行"，精确到文件路径。

### 3️⃣ 要改前端样式

```
New-docs/design/Design_system.md
```

这是所有视觉/组件/token 的 **合同**。违反它的 PR 应该被驳回。

---

## 📝 文档维护规则

- **入门、运行、部署、核心能力总览** → 仓库根 `README.md`
- **更细的模块行为、页面交互、专题配置、排障** → `New-docs/` 对应子目录
- **变更日志** → `docs/CHANGELOG.md`（路径不动，AGENTS.md §1 硬规则）
- **仓库协作规则** → 仓库根 `AGENTS.md`（真源） + `CLAUDE.md`（软链）
- **GitHub Copilot / Coding Agent 镜像** → `.github/copilot-instructions.md` + `.github/instructions/*.instructions.md`
- **Agent skill bundles** → `.claude/skills/**/SKILL.md`（代码协作）+ `strategies/*.yaml`（交易策略）+ `New-docs/integrations/openclaw-skill-integration.md`（外部产品契约）
- **双语文档** → 改其中一份时评估另一份是否要同步；未同步就在 PR 里说明原因

---

## 🗃️ 已废弃（不要参考）

- `New-docs/00_INDEX.md` → 被本文件（`New-docs/README.md`）取代
- `PROJECT_VISION.md`（仓库根）→ 已移到 `archive/PROJECT_VISION_v1-3.md`，最新版是 `architecture/01_PROJECT_VISION_v4.md`
- `review.md`（仓库根）→ 已移到 `archive/review_2026-03-19.md`
- `docs/bot/` / `docs/phase0/` / `docs/architecture/*.md` / `docs/*.md` → 全部迁移到 `New-docs/`（只剩 `docs/CHANGELOG.md` 和 `docs/architecture/api_spec.json`）
