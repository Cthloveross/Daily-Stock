# Daily-Stock 完整交接手册

> 文档状态：当前接手总入口
>
> 快照时间：2026-07-31（Asia/Shanghai）
>
> 适用仓库：本文所在 `New-docs/` 的仓库根目录
>
> GitHub：`Cthloveross/Daily-Stock`
>
> 安全等级：Moomoo 永久只读；不得解锁、下单、改单或撤单

这份文档回答四个问题：

1. 产品最终要做成什么；
2. 现在真实完成到了哪里；
3. 本地、运行中服务与 GitHub 分别有哪些内容；
4. 下一位开发者怎样在不破坏交易证据的前提下继续。

它是“当前接手入口”，不是所有专题合同的替代品。遇到冲突时，按下面的真源顺序判断。

## 目录

1. [真源顺序与阅读方法](#0-真源顺序与阅读方法)
2. [一屏结论](#1-一屏结论)
3. [产品目标、判断标准与红线](#2-产品目标判断标准与红线)
4. [术语](#3-术语)
5. [系统架构](#4-系统架构)
6. [Journal 数据生命周期](#5-journal-数据生命周期)
7. [正式库快照](#6-正式库快照)
8. [Web 路由与用户日常流程](#7-web-路由与用户日常流程)
9. [AI 复盘边界与性能](#8-ai-复盘边界与性能)
10. [今日机会研究的专业边界](#9-今日机会研究的专业边界)
11. [API 快速地图](#10-api-快速地图)
12. [配置与权限](#11-配置与权限)
13. [本机运行与运维](#12-本机运行与运维)
14. [Git 与 GitHub 交接](#13-git-与-github-交接)
15. [验证矩阵](#14-验证矩阵)
16. [已知问题与技术债](#15-已知问题与技术债)
17. [后续路线图与验收](#16-后续路线图与验收)
18. [故障排查](#17-故障排查)
19. [数据保护、备份与回滚](#18-数据保护备份与回滚)
20. [执行协议](#19-给下一位-codex开发者的执行协议)
21. [接手检查清单](#20-接手检查清单)
22. [本次交接结论](#21-本次交接结论)

---

## 0. 真源顺序与阅读方法

### 0.1 真源优先级

1. 可执行代码、数据库约束、测试和当前运行结果；
2. [Phase 1 当前持仓快照专题](./phase1/11_CURRENT_POSITION_SNAPSHOTS.md)及其他与改动直接对应的 `phase1/*` 专题；
3. 本手册记录的时间点快照；
4. [产品章程与路线图](./architecture/05_PRODUCT_CHARTER_AND_ROADMAP.md)；
5. 较早的总览、Phase 0 和归档文档。

特别注意：

- [当前状态旧快照](./architecture/04_CURRENT_STATE.md)、[产品章程与路线图](./architecture/05_PRODUCT_CHARTER_AND_ROADMAP.md)和 [Journal 每日刷新专题](./phase1/10_JOURNAL_READONLY_REFRESH.md)中有少量早于最新实现的“尚未接入”描述。
- 当前持仓、continuity fence 和 future Episode preview 的最新事实，以可执行代码及 [`phase1/11`](./phase1/11_CURRENT_POSITION_SNAPSHOTS.md)为准。
- 仓库协作与交付规则只以根目录 `AGENTS.md` 为准。

### 0.2 推荐阅读顺序

新接手者不要先遍历整个仓库。按以下顺序阅读即可：

1. 本文第 1、2、3 节；
2. 根目录 `AGENTS.md`；
3. [产品章程与路线图](./architecture/05_PRODUCT_CHARTER_AND_ROADMAP.md)；
4. [可信证据账本](./phase1/01_MOOMOO_EVIDENCE_LEDGER.md)；
5. [PositionEpisode](./phase1/02_POSITION_EPISODES.md)；
6. [OpenAPI canonical](./phase1/03_OPENAPI_CANONICAL.md)与[显式构建](./phase1/04_CANONICAL_EPISODE_BUILD.md)；
7. [单笔复盘工作台](./phase1/05_SINGLE_POSITION_REVIEW_WORKSPACE.md)；
8. [每日机会榜](./phase1/06_DAILY_OPPORTUNITY_BOARD.md)；
9. [当前持仓快照与未来边界](./phase1/11_CURRENT_POSITION_SNAPSHOTS.md)；
10. 只读查看代码与测试后，再决定改动。

---

## 1. 一屏结论

| 维度 | 2026-08-02 真实状态 | 接手判断 |
|---|---|---|
| 产品 | 「交易证据 → canonical 事实 → PositionEpisode → 单笔复盘 → Playbook」与「官方研究池 → 周内 Top 5 → 期权增强 → 5D/20D outcome 维护」双闭环完成；`/intraday` 日内工作台上线（波段爆发 v2 按用户标注样本校准、噪音过滤 v3 时段/财报/大盘/速度、styleMatch v1 形态标注、临期合约面板含财报警示） | 5D/20D 完整研究样本仍在积累期，不得展示策略命中率；日内榜 statistics_track=none 属有意设计 |
| 交易权限 | Moomoo 只读，代码禁止 unlock/place/modify/cancel | 必须永久保持 |
| Web | `http://127.0.0.1:8000` 运行中（LaunchAgent com.dailystock.uvicorn），`static/` 从磁盘服务：前端改动只需重建、后端改动需 kickstart 重启 | 交易日主屏是 `/intraday`；复盘主屏是 `/journal` |
| OpenD | 本机 `127.0.0.1:11111` 可达，只读；期权 bid/ask 经批量 get_market_snapshot 直读 | TCP 可达不等于历史证据、订阅与权限全部健康 |
| Journal 历史事实 | canonical set #2；build #3（1,663 Episode，成交按母单合并）已构建**未激活**（激活不可逆，用户门控）；用户交易原则已入库为 6 条 Playbook 候选（S1-S3/R1-R3，待用户晋升）；statement 解析器 v3 支持组合单（审计级父单，不伪装单腿） | 用户最新 CSV（06-04→07-31）经核对完全被现有账本覆盖（03-04→07-31），无需导入 |
| 当前持仓 | 只读双采样、确认账本、continuity readiness、future preview 已实现；fence build 经冻结目标 canonical 身份激活 | 正式库尚无已确认 snapshot（B-4 实弹演练用户门控） |
| 复盘 | 复盘工作流 v2 + 快捷标签已对齐用户打法分类（S1/S2/S3 风格、噪音时段/速度不足等错误标签）；「模式观察」只读聚合标注 | 用户逐笔打标后统计才有积累价值 |
| 今日机会 | 周内榜 21:12 自动发布（premarket-scheduler 已挂载）+ outcome 调度器运行中；2026-07-31 首次 events+premarket 域恢复的健康发布 | 2026-08-03 晚为三重实战验证首日（发布→计划条→日内工作台全链路） |
| GitHub | 分支 `codex/options-research-workbench` 已推送，PR #3 打开，远端 `main` 仍为基线 `4dc04fd` | 用户合并 PR #3 后 main 才含全部功能 |
| 本地 Git | 工作树干净，HEAD 与远端分支同步（见 `git log --oneline -5`） | 每轮功能经双 gate + 真实页面验收后单独 commit+push（用户已授权本分支） |
| CI | 本地 `./scripts/ci_gate.sh` 在 HEAD 全绿（2,861 passed）+ Node 22 web gate 全绿；PR #3 远端 CI 逐提交重跑 | 合并前以 PR 页最新提交的远端 CI 为准 |
| 最大风险 | build #3 未激活（用户决策）、旧 moomoo-sync LaunchAgent 每 15 分钟空转报「已暂停」（F-1 清理待确认） | 2026-08-03 首个实战日全链路验证通过（62 波账本、G-12/13/14 实战驱动修复当日落地）；后续以用户复盘打标积累与用户门控项为主 |

### 1.1 “本地和 GitHub 都有了吗”的准确答案

分支已同步，`main` 待合并（截至 2026-08-02 晚）。

- 分支 `codex/options-research-workbench` 已推送到 GitHub 并打开 PR #3，本地工作树干净、HEAD 与远端分支一致。
- GitHub `main` 仍指向基线 `4dc04fdd0a98682aba4e9555adb607d7ab4ae857`（`feat: build evidence-first trading research workbench`）；PR #3 合并前 main 不含本分支功能。
- 本地 `main` 停在 `db94455`，比 GitHub `main` 落后一个提交；不要误把本地 `main` 当发布真源。
- `data/stock_analysis.db`、`.env`、`logs/` 和构建后的 `static/` 属于忽略或本地运行资产，不进 Git。
- 每轮功能验收后单独 commit+push（commit message 英文、无 Co-Authored-By；用户已对本分支授权），推送后核对远端 CI。

每次交接仍应重跑第 13 节的只读命令，以当时输出为准。

### 1.2 接手后的前十分钟

先做只读核对：

~~~bash
# 在仓库根目录执行
git branch --show-current
git rev-parse HEAD
git status --short --branch -uall
git diff --check
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/v1/system/moomoo-status
curl -fsS http://127.0.0.1:8000/api/v1/system/health-layers
~~~

然后在浏览器依次打开：

1. `http://127.0.0.1:8000/intraday`（交易日主屏）；
2. `http://127.0.0.1:8000/journal?tab=positions`；
3. `http://127.0.0.1:8000/journal?tab=import`；
4. `http://127.0.0.1:8000/regime`。

只观察，不先点击任何 confirm/activate/run。确认页面与本文状态一致后，再选择一个最小目标。若本地服务不可达，按第 12 节启动；若 Git 工作树数字和本文不同，先解释差异，不要 reset、checkout 或清理 untracked。

---

## 2. 产品目标、判断标准与红线

### 2.1 北极星目标

把项目建设成个人专属的美股期权 Trading OS：

1. 可信接收 Moomoo 交割与成交事实；
2. 用可重放、可审计的数据构建每个真实仓位生命周期；
3. 找出盈利、亏损、入场、出场、持仓和策略类型中的可重复模式；
4. 将这些模式逐步沉淀为个人 Playbook；
5. 每个交易日前生成与个人风格匹配的少量高价值研究候选；
6. 所有结论都标明证据、时间点、缺口和置信边界；
7. 系统只做研究与复盘，永不代替用户下单。

### 2.2 成功不等于“页面上有很多数字”

一个功能只有同时满足以下条件才算完成：

- 数据来源明确；
- `as_of`、交易日、市场时区明确；
- 缺数据时 fail closed，不用 0 或猜测代替；
- 同一结论使用同一冻结证据版本；
- 可重放并得到相同 hash/结果；
- 用户能看懂结论如何得出；
- 有相应测试；
- 不改变券商状态、不产生交易动作；
- 文档、API 与 UI 对同一概念用词一致。

### 2.3 永久红线

- 不调用或新增 Moomoo 解锁交易、下单、改单、撤单能力。
- 不把 TCP 可达写成“账户数据已同步”。
- 不把日线方向、IV 终值区间或单笔盈亏写成胜率。
- 不把缺失数据编码为 0。
- 不把 aggregate order 猜成 fill。
- 不默认期权 multiplier 为 100；必须有合同或金额证据。
- 不把组合单父单伪装成普通单腿订单。
- 不把组级费用猜测分摊到腿。
- 不把事后用户回忆写成进场前已记录的计划。
- 不让 AI 生成内容反写券商事实、canonical、Episode 经济字段或策略权重。
- 不因“继续”而自动扩大到 commit、push、生产写入或外部发布。

### 2.4 当前非目标

- 自动交易；
- 复制 TradingView 私有 Watchlist；
- 用少量 5D/20D 样本宣称 edge；
- 用“综合概率”掩盖不同数据时点；
- 同时支持所有市场与所有期权策略；
- 先做炫酷 UI，再补证据合同。

---

## 3. 术语

| 术语 | 准确定义 |
|---|---|
| Evidence ledger | append-only 原始证据账本，保存 CSV/OpenAPI observations、费用、合约规格与来源 |
| Canonical set | 对重叠来源去重、比对并冻结后的交易事实集 |
| PositionEpisode | 单一合约有符号仓位从建立、变化到平仓或当前开放的生命周期 |
| StrategyEpisode | 当前实质是与 PositionEpisode 1:1 的占位；roll、exercise/assignment 和多执行组合聚合尚未落地 |
| Build | 对冻结证据的确定性 Episode 重放；分为 CSV-backed fallback build 与 canonical-linked build |
| Activation | canonical-linked build 的独立 append-only 默认视图选择；build 成功不等于自动生效，CSV fallback build 当前不可被该端点选中 |
| Snapshot | Moomoo 当前活动美股期权持仓的只读双采样证据 |
| Publication | 一次用户确认过的 Journal OpenD 只读刷新发布 |
| Continuity fence | 证明 snapshot boundary 之后的成交证据连续且可安全重放的冻结门禁 |
| Future preview | 绑定特定 fence 的内存 Episode 投影；不写 build，不激活 |
| Research universe | 用户显式保存到服务端的官方盘前研究池，和浏览器本地 Watchlist 不相同 |
| Official Top 5 | 由服务端一次盘前 cycle 发布的主要候选 |
| Readiness | 某个数据域是否具备可用于当前结论的时点、覆盖和权限证据 |
| Degraded | 有结果但部分证据缺失；不是失败，也不是完整研究 |
| Qualified | 满足某条明确统计 track 的入样合同；不同 track 不能混算 |

---

## 4. 系统架构

~~~mermaid
flowchart LR
    CSV["Moomoo History CSV"] --> Parser["Loss-aware parser"]
    OpenD["Moomoo OpenD · read-only"] --> Refresh["Frozen refresh artifact"]
    Parser --> Ledger["Append-only evidence ledger"]
    Ledger --> CSVBuilder["CSV-backed builder"]
    CSVBuilder --> CSVBuild["Fallback build · no activation row"]
    CSVBuild --> Journal["Journal / Review Queue"]

    Refresh --> Confirm["Explicit refresh confirm"]
    Confirm --> Bundle["Atomic evidence + canonical bundle"]
    Bundle --> Ledger
    Bundle --> Canonical["Frozen canonical set"]
    Bundle --> Receipt["Append publication receipt"]
    Receipt --> AnchorA["Confirmed publication A"]

    Canonical --> Builder["Deterministic Episode builder"]
    Builder --> Build["Canonical-linked append-only build"]
    Build --> Activate["Separate CAS activation"]
    Activate --> Journal
    Journal --> Annotation["Append-only user annotations"]
    Journal --> AI["Deterministic review + optional model layer"]

    AnchorA --> SnapshotProbe
    OpenD --> SnapshotProbe["Current positions · double sample"]
    SnapshotProbe --> SnapshotConfirm["Future-only explicit confirm"]
    SnapshotConfirm --> Snapshot["Confirmed snapshot"]
    Snapshot --> LaterPublication["Strictly later publication chain B"]
    OpenD -. "later read-only refresh" .-> LaterPublication
    LaterPublication --> TargetCanonical["Target canonical + source cutoff"]
    TargetCanonical --> Fence["Continuity fence"]
    Fence --> FuturePreview["Zero-write future Episode preview"]

    Universe["Server-owned research universe"] --> Cycle["09:12 / 09:17 ET cycle"]
    Cycle --> Opportunity["Official Top 5 snapshot"]
    Opportunity --> Walls["Option walls / events / IV evidence"]
    Opportunity --> Outcomes["5D / 20D outcome maintenance"]
    Outcomes --> Learning["Qualified learning tracks"]
~~~

### 4.1 目录边界

| 层 | 主要目录 | 职责 |
|---|---|---|
| API | `api/v1/endpoints/`、`api/v1/schemas/` | HTTP 合同、输入确认、错误边界 |
| Journal | `src/journal/` | CSV/OpenAPI、账本、canonical、Episode、snapshot、continuity |
| Regime/机会 | `src/regime/`、`api/v1/endpoints/opportunities.py` | 市场背景、研究池、盘前发布、结果维护 |
| 期权 | `src/options/` | OCC、Black-Scholes、IV、chain/墙位语义 |
| 服务层 | `src/services/` | AI 复盘、数据加载与跨模块编排 |
| 数据源 | `data_provider/` | Moomoo、Alpaca、Finnhub、yfinance 等适配/fallback |
| Web | `apps/dsa-web/` | React 页面、状态机、图表和 API client |
| Desktop | `apps/dsa-desktop/` | Electron 包装 |
| 运维 | `scripts/`、`.github/workflows/`、`docker/` | 启动、轮转、CI、部署 |

### 4.2 关键实现入口

Journal：

- `src/journal/brokers/moomoo_statement.py`：History CSV loss-aware parser（v3 起识别组合单父单：`Nunit(s)` 组合 unit 数量、`MU260731P745/760` 型价差符号与腿展示行；父单为 audit-only 证据，组级费用不分摊、不推导乘数或腿数量，CSV 组合父单被 canonical 选择显式排除，腿级真相仍以 OpenAPI execution group 为准；详见 `New-docs/phase1/01_MOOMOO_EVIDENCE_LEDGER.md` §4.3）。
- `src/journal/brokers/moomoo_readonly.py`：OpenD 历史数据只读探测。
- `src/journal/brokers/moomoo_openapi_export.py`：去标识化 OpenAPI export 严格解析。
- `src/journal/ledger/models.py`：核心 evidence/canonical/Episode 模型；不是全部 Journal 模型的单一文件。
- `src/journal/ledger/identity.py`：CSV/OpenAPI identity projection。
- `src/journal/ledger/repository.py`：证据读写与不可变约束。
- `src/journal/ledger/canonical.py`：canonical 选择、去重和 provenance。
- `src/journal/ledger/openapi_repository.py`：数据库感知的 OpenAPI plan/replan/confirm。
- `src/journal/ledger/refresh_models.py`、`refresh_repository.py`：server-owned refresh artifact、publication 与恢复。
- `src/journal/ledger/episodes.py`：Episode 构建。
- `src/journal/ledger/episode_repository.py`：build 与 Episode 持久化读取。
- `src/journal/ledger/activation_repository.py`：默认 build 的 CAS activation。
- `src/journal/ledger/review_repository.py`：人工复盘 revision。
- `src/journal/brokers/moomoo_position_snapshot.py`：当前持仓双采样。
- `src/journal/ledger/position_snapshot_models.py`：snapshot 表与数据合同。
- `src/journal/ledger/position_snapshot_repository.py`：snapshot append-only 存储。
- `src/journal/ledger/position_snapshot_continuity.py`：跨 boundary 连续性证明。
- `src/journal/ledger/position_snapshot_episode_preview.py`：future preview。
- `src/services/episode_ai_review_service.py`：证据化复盘与可选模型增强。

Web：

- `apps/dsa-web/src/pages/JournalPage.tsx`
- `apps/dsa-web/src/components/journal/JournalImport.tsx`
- `apps/dsa-web/src/components/journal/PositionEpisodesPanel.tsx`
- `apps/dsa-web/src/components/journal/CurrentPositionsSnapshotCard.tsx`
- `apps/dsa-web/src/pages/JournalEpisodeReviewPage.tsx`
- `apps/dsa-web/src/pages/RegimePage.tsx`
- `apps/dsa-web/src/pages/OpportunityDetailPage.tsx`
- `apps/dsa-web/src/pages/WatchlistPage.tsx`
- `apps/dsa-web/src/api/journal.ts`
- `apps/dsa-web/src/api/journalPositions.ts`
- `apps/dsa-web/src/api/opportunities.ts`

---

## 5. Journal 数据生命周期

### 5.1 首次历史导入

首先共同执行：

1. 用户上传 Moomoo History CSV；
2. `/journal?tab=import` 只做 preview；
3. 检查日期覆盖、订单、逐笔成交、费用和阻断原因；
4. 用户显式确认；
5. 系统 append evidence ledger；

此后分成两条不能混淆的 build 路径。

#### A. CSV-only fallback

1. 没有 accepted OpenAPI 证据时，不会生成 canonical set；
2. `POST /journal/v2/episode-builds?accept_assumed_flat=true` 在服务端内部先计算，再直接 append CSV-backed build；当前没有独立公开的 CSV build preview endpoint；
3. 没有 activation row 时，默认读取可回退到最新 CSV-backed build；
4. 当前 activation endpoint 不能选择 CSV-backed build。

#### B. OpenAPI + canonical

1. OpenAPI 文件先做纯解析 preview；
2. 再做 database-aware plan；
3. confirm 时重新 plan，并绑定 expected preview key；
4. stale plan、blocking issue 或未显式接受 partial window 时拒绝；
5. accepted OpenAPI evidence 与 canonical set 原子追加；
6. `/journal?tab=positions` 请求 canonical build preview；
7. confirm 必须绑定 expected canonical SHA 与 expected build key，并分别显式接受 `assumed_flat_unverified` 和 group-fee scope；
8. canonical-linked build append 后仍不改变默认视图；
9. 用户单独执行 CAS activation，默认复盘版本才改变。

不能跳过的理由：

- preview 与 confirm 分开，避免看一眼就写入；
- canonical 与 build 分开，避免事实变化暗中改写分析；
- build 与 activation 分开，避免一次重算覆盖当前工作视图；
- activation 使用 compare-and-swap，避免并发页面互相覆盖。
- `analysis_ready=false` 的 canonical 只能用于诊断，绝不能 build。

### 5.2 每日 OpenD 只读刷新

运行条件：

- `MOOMOO_OPEND_ENABLED=true`；
- `MOOMOO_JOURNAL_REFRESH_ENABLED=true`；
- `MOOMOO_JOURNAL_ENV=LIVE`；
- 设置至少 32 字符的 `MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET`；
- 多个合资格账户时设置 `MOOMOO_JOURNAL_ACCOUNT_ID`；
- OpenD 已登录，且历史订单、成交、费用权限可读。

语义：

- `LIVE` 只表示读取真实账户历史范围，不表示允许交易；
- 刷新截止到最近已完成的 XNYS session close；
- 不把当日盘中、盘后、夜盘未结算事实混入上一完整交易日；
- OpenD 查询按最多 7 天分块、总窗口最多 366 天，fee/spec 按最多 400 项一批；默认总截止 180 秒，SDK 读取在可终止 spawn child 中执行；
- 大于支持范围的历史缺口应重新建立 CSV baseline，不能无限扩大 OpenD 查询；
- preview 冻结为短期 server-owned artifact，不把可篡改 payload 交给浏览器保管；
- confirm 重新验证 artifact、payload hash、账户 binding、查询窗口和当下数据库 baseline，再执行与手工 OpenAPI confirm 相同的 strict replan；
- accepted evidence 与 canonical bundle 在一个事务中原子 commit，publication receipt 随后 append；
- 若进程在 bundle commit 后、receipt 前崩溃，accepted batch/canonical 不能冒充已发布 publication；重试只补合法 receipt，continuity 仍 fail closed；
- overlap 不一致、账户 binding 不一致、窗口缺口、费用/规格证据不完整或 stale plan 时 fail closed；
- blocked canonical 可供诊断，但 `analysis_ready=false` 时不能生成 Episode build；
- 当前正式 broker/canonical execution-group 相关 rows 均为 0；组合增量 tail 的主要保证来自测试，pre-baseline/overlap 组合 merge 仍保持阻断；
- 短期 refresh/snapshot artifact 的过期清理已实现（F-2a：`scripts/artifact_gc.py` 显式 CLI，默认只读 dry-run，apply 需当日验证备份，合同见 `New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md`）；可恢复异步 job 和持久化 last failure 仍是运维技术债。

### 5.3 当前持仓与未来 Episode

这是不同于历史导入的独立流程：

1. 用户可以先点击当前持仓“只读检查”；即使没有 publication，preview 也允许采集并保存短期 artifact，但 `confirm_allowed=false`；
2. 服务端对 LIVE/US 活动期权持仓进行两次完整采样；
3. 稳定性只比较 instrument identity、LONG/SHORT 与 signed quantity；cost、average cost 和 diluted cost 不参与稳定性证明，只作为第二次读取的展示上下文；
4. Moomoo position 没有可靠 `broker_as_of`，该字段固定为 null；系统只证明本地 `[query_started_at, operation_completed_at]` bracket，不伪造券商历史时点；
5. 硬门禁为未来时钟偏差不超过 2 分钟、双采样区间不超过 5 分钟、采集完成到保存/首次确认不超过 30 分钟；
6. 只有两次完整成功且 eligible 活动期权成员都为空、过滤/分类计数守恒且无 validation/spec 问题，才能确认可信 empty；查询失败、部分读取、边界变化或过滤统计不一致绝不能伪装为空仓；
7. 每个期权 multiplier 必须由同次 quote contract spec 证明；
8. confirm 前必须存在 latest same-account Journal publication A，artifact 必须绑定它；
9. 若 artifact 生成后、confirm 前出现了新的 latest publication，旧 artifact 立即失配并拒绝确认；
10. 用户接受 future-only 用途后，snapshot 才 append-only 保存；
11. snapshot 之后必须再完成严格更晚的 confirmed refresh publication chain B，并生成 target canonical；
12. continuity assessment 在同一只读 SQLite 事务中重验全部证据；
13. 只有 `ready` 才能带 exact `fence_key` 请求 future Episode preview；
14. preview 再次重算 fence，stale fence 返回 409；
15. 当前只生成内存结果，零 build 写入、零 activation、零交易。

currentness 与 continuity 是两个概念：

- confirmed snapshot 超过 30 分钟或被新 publication supersede 后，不再是“当前仓位”；
- 只要不可变证据与后续覆盖仍能严格重验，它仍可作为历史 future boundary；
- continuity 不得因 `is_current=false` 自动阻断；
- boundary 固定为 `operation_completed_at`，guard 固定为 `[query_started_at, operation_completed_at]`。

Continuity 的关键 fail-closed 合同：

- 只支持已有文件的 SQLite；`:memory:`、缺表、非 SQLite 均拒绝；
- 使用 `mode=ro + query_only + authorizer deny writes + explicit BEGIN`，整个 Session 绑定同一 SQLAlchemy connection/WAL snapshot；
- strict replay snapshot artifact/member、每个 refresh artifact/publication、anchor/target canonical root/member、selected source、batch、group/leg/fill-link 和 multiplier provenance；
- publication chain 必须 bracket guard，且 chain batches 全部属于 target canonical；
- guard 内精确 fill 阻断；边界前新增/变化 aggregate-only 阻断；
- 普通 order 的 fill set 或 execution group 跨 boundary 阻断；
- boundary 后 facts 必须有 publication chain backing；
- OCC semantic identity 与 multiplier 分别验证；
- fence 同时冻结 target canonical source cutoff；即使其他 hash 合法，cutoff 变化也必须生成新 fence。

Future preview 的关键边界：

- 只消费 `(operation_completed_at, source_cutoff]` 内的 LIVE/US/options facts；
- 必填 64 位小写 `expected_fence_key`，并在同一只读事务重算；
- snapshot quantity 是 complete opening boundary，但 current cost 不传给 builder；
- 从 boundary 继承的仓位仍标记 left-censored；
- group fee 不下沉到腿；
- full canonical member guard 当前上限 25,000；
- “零写”是零业务行、零 schema、零交易；SQLite 读取仍可能维护 WAL/SHM sidecar。

状态解释：

| 状态 | 含义 | 下一步 |
|---|---|---|
| `no_snapshot` | 没有已确认未来边界 | 做只读双采样并确认 |
| `awaiting_refresh` | 有 snapshot，缺 boundary 后 publication | 检查上一完整交易日并确认 |
| `blocked` | 证据连续性存在具体冲突 | 查看 reason，补证据，不能绕过 |
| `ready` | 可以生成零写 future preview | 使用当前 fence 请求 preview |

当前缺口：切片 1（formal future-build confirm 写路径 + `journal_v2_episode_build_snapshot_fence_sources` link 表 + `POST /api/v1/journal/v2/episode-builds/position-snapshot`）已于 2026-07-31 实现——confirm 在 `BEGIN IMMEDIATE` 写事务内重跑 preview 核心、CAS 对比 fence/build/evidence hash、要求 left-censored 与 group-fee 显式接受后 append-only 追加 build 与 link，build 追加后默认视图不变（CSV fallback 显式排除 fence build）。切片 3（Web UI 显式确认）已于 2026-08-01 实现；切片 2（activation 资格扩展到 future build）也已于 2026-08-01 实现：无 schema 变更，fence build 激活时以 link 冻结的目标事实集身份写入 activation 的 NOT NULL canonical 列，并新增 `accept_left_censored_openings` 显式门禁（决策记录见写合同 §6）。剩余缺口：真实库演练仍待用户完成一次 snapshot confirm + 后续 refresh publication 后走 confirm → 显式激活全链路。写合同见 [`phase1/12_FORMAL_FUTURE_BUILD_CONTRACT.md`](./phase1/12_FORMAL_FUTURE_BUILD_CONTRACT.md)。

### 5.4 Append-only 与 legacy 的边界

- SQLite deny triggers 保护的是 `journal_v2_*` 业务行的 UPDATE/DELETE；schema 仍可能 CREATE/ALTER，strict read 仍会重算 hash/provenance。
- Phase 0 `journal_*` 是另一套可变 legacy 存储，不属于 v2 evidence truth。
- `PATCH /api/v1/journal/trades/{trade_id}` 仍会更新 legacy trade；`POST /reviews/{year}/{month}/generate` 会写 legacy review；部分 legacy GET 还会触发 schema init。
- 旧 `POST /journal/import` 与 `POST /journal/sync-live` 的危险 FIFO/writer 旁路已关闭或暂停，但这不等于全部 legacy API 都是 append-only 或 zero-write。
- 新分析、对账、P&L 和未来边界不得重新混用 legacy FIFO 事实。

---

## 6. 正式库快照

当前活动数据库唯一按 `data/stock_analysis.db` 解释。它是本地用户数据，不进入 GitHub。`data/daily_stock.db` 与仓库根 `stock_analysis.db` 当前都是 0-byte 干扰文件，不能被脚本误选。

2026-07-31 20:02 已通过 SQLite backup API（只读源连接，捕获 WAL 内当日写入）创建 `data/backups/stock_analysis-handoff-baseline-20260731.db`：`integrity_check=ok`，49 张 `journal_*` / `journal_v2_*` / `opportunity_*` 业务表 live/copy 行数全部一致，sha256 `1e707b8741f664d62666e456583fc6f7ef0787b036bfa8960ceb89972f3d1500`。这是覆盖 07-30 业务事实的当前可恢复备份；备份时服务保持运行，一致性边界即备份窗口 `20:02:08`。更早的 `stock_analysis-pre-opportunity-outcomes-20260722.db` 仅作历史留档。任何新写入或 migration 前，仍必须按 18.2 重新创建并验证新备份。

### 6.1 Journal

截至 2026-07-31 的只读核对：

| 对象 | 当前值 |
|---|---|
| CSV import batch #1 | accepted；3,542 orders / 5,400 fills；2026-03-04 至 2026-07-20 |
| CSV import batch #2 | accepted；与 #1 相同覆盖，用于 parser 表达升级/审计 |
| OpenAPI batch #3 | accepted；1,089 orders / 2,107 fills；2026-06-20 至 2026-07-20 |
| Canonical set #1 | stored reader `stable_broker_identity_reader 1.0.0`；source batches `[2,3]`；3,542 orders / 5,400 fills；0 blocker |
| Episode build #1 | `signed_position_episode_builder 1.1.0`；source batches `[2]`；CSV-backed fallback；1,441 episodes；partial |
| Episode build #2 | 同 builder；source batches `[2,3]`；绑定 canonical #1；1,441 episodes；partial |
| Activation rows | 0 |
| Refresh publications | 0 |
| Current snapshots | 0 |
| Review annotations | 0 |

重要解释：

- build #2 存在，不代表默认页面已激活它。
- activation 为 0 时，默认读取逻辑仍回退到 v2 CSV-backed fallback build #1；它不是 Phase 0 legacy FIFO build。
- 当前代码 canonical reader 是 `1.1.0`；stored root `1.0.0` 只在“root 没有 execution-group metadata，且数据库 canonical/broker group rows 确实为 0”时通过窄兼容重放，不能把 1.0.0 当作当前 writer 版本。
- 1,441 个 Episode 因缺乏已验证 opening position snapshot，属于 `assumed_flat_unverified` 边界；条件性 closed P&L 不能写成已经验证的账户总收益。
- 今天采集的 current snapshot 只能服务 forward Episode，不能 retroactively 修复 2026-03 起的历史 opening boundary。
- 正式 snapshot 为 0，因此当前 future readiness 显示 `no_snapshot` 是正常且诚实的状态。

### 6.2 今日机会

| 对象 | 当前值 |
|---|---|
| 研究池版本 | 1 |
| 研究池 | 15 个 symbol；页面主要展示 Top 5 |
| Premarket cycle slots | 4 |
| Attempts | 5 |
| Opportunity snapshots | 6 |
| Outcomes | 15 个已完成 5D；尚无成熟 20D |
| 完整研究 track | 全部 excluded，当前没有可统计样本 |

最近运行：

- 2026-07-24 首次 attempt 失败后恢复并发布；
- 2026-07-27、07-28、07-30 已发布；
- 最新 07-30 是 degraded：Regime 宏观事件域降级，盘前域不可用；这里的 events 指 FOMC/CPI/NFP/watchlist earnings 等辅助事件，不是异常期权成交；
- 这不是“卡在扫描中”，也不能把 degraded 解释为完整信号。

统计边界：

- raw underlying path 的结果只验证标的路径，不验证期权合约、执行、IV 或策略；
- 5D/20D 必须按相同 setup / Regime / 方向 / policy version 分层；
- 少于门槛时不显示命中率；
- 当前完整研究 track 为 0，任何“策略成功率”都是不合规文案。

---

## 7. Web 路由与用户日常流程

### 7.1 路由

| 路由 | 用途 |
|---|---|
| `/` | 重定向到 `/regime` |
| `/login` | 登录入口；受保护路由会保留原 path/query 后跳转 |
| `/journal?tab=positions` | 复盘工作台（G-3）：「复盘工作台」头部条（默认构建标识 + Review Queue + 继续复盘下一笔）+ 回合列表/筛选 + 模式观察 + Playbook |
| `/journal?tab=import` | 「数据与构建」（原「交易证据」，深链不变）：每日刷新、历史导入（CSV/OpenAPI）、当前持仓快照与 future preview、canonical 构建与默认视图（激活）管理 |
| `/journal/review/:episodeId` | 单一 PositionEpisode 专业复盘 |
| `/intraday` | 日内工作台（G-6）：市场脉搏 + 冻结盘前计划对照 + 盘中滚动扫描 + 期权异动 feed；盘中滚动研究，不冻结不入统计 |
| `/regime` | 官方盘前研究、周内 Top 5（基于上一完整交易日日线结构）、状态与学习面板 |
| `/regime/opportunity/:ticker` | 单票机会详情 |
| `/watchlist` | 浏览器本地自选与官方研究池核对/保存 |
| `/stocks/:ticker` | 股票分析详情 |
| `/backtest` | 回测 |
| `/settings` | 数据源、模型与系统设置 |
| `/design-lab` | 隐藏设计实验页，不是生产入口 |

### 7.2 每日建议操作

盘前：

1. 打开 `/watchlist`；
2. 核对本地 Watchlist 与“官方盘前研究池”的差异；
3. 如有变化，显式保存官方池；
4. 打开 `/regime`；
5. 先看官方版本、目标交易日、生成时间与 analysis quality；
6. 只把 Top 5 当研究优先级；
7. 点入单票详情，核对方向、确认条件、失效条件、期权墙和数据时点；
8. 若核心 research input/gate 失败，候选应被阻断；若只是 supporting Regime 或 option enhancement 降级，基础 Top 5 仍可研究，但会被排除出严格完整研究统计；
9. 所有候选始终只是研究优先级，不是自动交易信号。

盘后：

1. 打开 `/journal?tab=import`（「数据与构建」，G-3 起集中全部数据管线）；
2. 在「每日刷新」点击“检查上一完整交易日”；
3. 查看 overlap、incremental tail、费用与 blocker；
4. 显式确认发布；
5. 仍在「数据与构建」的「构建与默认视图管理」查看 canonical preview；
6. 需要新版本时先 build，再单独 activate（激活管理已随 G-3 移到本 tab；当前持仓快照/future preview 在「当前持仓快照与未来构建」分组）；
7. 打开 `/journal?tab=positions`（复盘工作台），头部条显示默认构建标识与 Review Queue（未开始/进行中/已完成）；
8. 点「继续复盘下一笔」直达最优先回合（优先续上进行中；否则 top_loss 案例精选取亏损最大的未复盘已平仓回合；无命中退回最近未开始），或用列表案例精选选择大盈、大亏、长持仓、高费用或证据最不完整案例（`weakest_evidence` 按 completeness_score 升序，2026-08-01 新增）；
9. 在详情页填写事后复盘上下文、对进场逻辑的回忆、出场原因与反思；这些是用户自述，不是系统留存的事前计划证据；
10. 先看确定性证据分析，再按需调用模型增强；
11. 不因单笔结果改变系统权重。

每周：

- 对同一 setup 分组复盘；
- 查盈利和亏损是否来自相同市场环境；
- 查进场、持有、出场、费用和合约选择；
- 把“观察到的模式”与“已经验证的规则”分开；
- 只有满足样本门槛和独立交易日门槛，才手工把候选规律提升为 Playbook 规则；当前 Web 尚无从 Journal 自动晋升或编辑 Playbook 的产品链路。

### 7.3 K 线和 EMA

Journal 单笔复盘支持：

- `1m / 2m / 5m / 15m / 30m / 1h / 1D`；
- 常规时段与扩展时段切换；
- 标的 underlying K 线；不是期权 premium K 线；
- 可映射到当前交易日/盘段/bar 的 broker fill marker；部分证据只能明确标为 order-time proxy，无法安全映射时不画 marker；
- EMA8 / EMA13。

一致性状态（2026-07-31 已修复 seed 分裂）：

- 前端所有图表 EMA overlay 已统一到共享实现 `apps/dsa-web/src/utils/ema.ts`（SMA seed 落在第 period 根 bar，样本不足 fail closed）；`buildEmaOverlay` 与 `OpportunityDetailPage` 均委托它，旧的首 close 递推实现已删除；
- 该实现与后端 `src/opportunities/engine.py` 的 `_ema_last`（fmean seed + 同 alpha）语义一致，`utils/__tests__/ema.test.ts` 含手算小样本与后端同输入 parity fixture；
- 常规/扩展时段切换后只基于当前可见 bars 重算（`OpportunityDetailPage.test.tsx` 有回归）；
- 即使 seed 统一，不同 timeframe、session 和 lookback window 仍可产生合理差异；
- 仍未解决：不把尚未结束 bar 的最终 close 当成成交时已知信息（盘中最后一根 bar 的 EMA 值仍随 bar 演进变化）。

---

## 8. AI 复盘边界与性能

### 8.1 两层输出

`POST /api/v1/journal/v2/position-episodes/{episode_id}/ai-review` 支持：

- `enhance=false`：确定性证据复盘，不调用外部模型；
- `enhance=true`：保留确定性证据，并尝试模型增强。

模型增强不可用时，页面必须继续显示 evidence review，不能整块失败。

隐私边界：非空用户复盘字段会发送给本地 API；`enhance=true` 时还会进入用户配置的第三方模型 provider，空字段会省略。页面必须在调用前让用户理解这一点。

### 8.2 为什么不能“直接接当前 ChatGPT”

浏览器里的本地网站与当前 Codex/ChatGPT 对话是两个安全边界。网站不能继承本对话的登录态、额度或隐藏凭据。要让网站主动调用 GPT，必须配置：

- OpenAI API key；或
- 项目已经支持的其他模型 provider；或
- 明确接入的本地模型服务。

任何 key 只能放本地 `.env` 或安全配置，不写入仓库。

### 8.3 当前性能护栏

`episode_ai_review_service.py` 当前包含：

- 行情读取硬截止 8 秒；
- 行情成功结果进程内缓存 180 秒；
- 模型总 timeout 16 秒；
- 单模型 timeout 10 秒；
- 模型硬截止 20 秒；
- Web 请求 timeout 45 秒；
- provider 失败回退到确定性分析。

后续优化优先级：

1. 页面立即渲染确定性复盘；
2. 模型层独立 loading，不遮挡证据；
3. 用 episode/build/evidence hash 做短期结果缓存；
4. 记录阶段耗时，不记录 prompt、key 或账户数据；
5. 把模型不可用、超时、格式不合格分开显示；
6. 不用无上限重试。

### 8.4 输出质量合同

AI 必须区分：

- 已知事实；
- 合理推断及置信度；
- 未知；
- 用户事后补录；
- 条件性 P&L；
- 单笔不能证明 edge。

没有 Greeks、IV、bid/ask、当时的计划止损或 opening snapshot 时，必须明确未知，不能补写。

---

## 9. 今日机会研究的专业边界

### 9.1 候选榜能回答什么

当前官方 immutable snapshot 冻结的是 deterministic candidate 与 qualification，不包含详情页异步重取的 option overview、walls、events 或 ATM IV。这些期权增强当前不是 official snapshot-bound，也不能进入完整研究 outcome 的因果证据。

当前榜单适合回答：

- 今天哪些标的最值得优先研究；
- 标的昨日结构和相对量能是否支持某一方向；
- 市场环境是支持、反对还是仅作背景；
- 哪些执行价附近有可观察的 OI/成交集中；
- 哪些数据域完整、哪些降级；
- 进入盘中后应等待什么确认，什么情况失效。

它暂时不能可靠回答：

- 某标的今天上涨的真实概率；
- 某期权策略的胜率；
- 某执行价一定是支撑/阻力；
- 暗池或异常流“导致”方向；
- 用昨天 OI 与当前 IV 拼出的单一综合概率。

时间口径也必须分开：

- 基础排名的 underlying volume 与 dollar-volume ratio 使用 latest completed session（通常 T-1）相对之前 20 个 session；
- option overview 的 call/put volume 是当前交易日累计；
- 2026-07-31 已修复：榜单数据时点提示现区分“相对量能＝上一完整交易日（相对之前 20 个 session）”与“期权 Call/Put Volume＝当前交易日累计”，不再用总括 Volume 文案误导基础相对量列。

### 9.2 期权墙

页面应直接展示已有关键执行价墙，不要求用户先点击才知道是否有数据。但必须区分当前 schema 与目标合同。

当前实现（`option-wall/1.2`，2026-08-01 起）：

- 一个 wall item 仍是指定 DTE scope 内跨 expiry 的聚合；item 级字段（expiries、coverage、spot、同一 snapshot 的最近到期 ATM Call IV）不变；
- 每个 level 在原 rank、strike、距 spot 距离、metric value/share/unit/method 之外，additive 补齐了逐层字段（`src/opportunities/option_walls.py`）：
  - `side`：call / put / call_put_aggregate（gross gamma 桶显式标为双边聚合）；
  - `metric_basis` 结算口径：OI＝`settled_open_interest_prior_session`（T-1 清算），Volume＝`current_session_cumulative_volume`（当日累计），Gamma＝`model_from_settled_oi_and_snapshot_greeks`（模型值，非观测）；
  - `expiry_breakdown`：按贡献排序的 top 3 到期日（每条含 expiry、dte、metric_value、share_of_level_percent、contract_count）+ `other` 汇总桶，level 不再是不可拆的跨 expiry 黑盒；
  - 逐到期 quote 上下文：仅当该 strike×expiry×right 单元恰好由一行快照支撑（`contract_count == 1`）时，附该行的 `iv_percent` 与 `quote_as_of`；多行聚合时不归属报价；
  - `quote_evidence` 显式标缺（observed / partial / unavailable）：缺失字段保持 null，不做零值或估算回填；
- 前端 `/regime/opportunity/:ticker` 期权墙 tab 与今日机会候选详情逐层可展开到期分布，缺失报价按「标缺」文案显示（`WallLevelExpiryBreakdown.tsx`）。

因此当前墙位仍只能称为“集中度区域”。它不是 dealer net GEX、gamma flip 或已经证明的对冲支撑/阻力；OI 仍是结算后数据，不能伪装成实时。

对照目标合同（逐层 underlying、expiration/DTE、call/put、OI、volume、bid/ask/mark、IV、snapshot/as-of、source、coverage、settlement 语义）仍缺：

- 逐层 bid/ask/mark：Moomoo 墙快照 adapter（`data_provider/moomoo_options.py` 的 `MoomooOptionWallContract`）当前只携带 volume/OI/gamma/contract_size/IV/update_time，不携带盘口字段；schema 与前端已预留字段并显式标缺，补齐需扩展 adapter 采集（另一轮次）；
- 逐层独立 OI 与 volume 双值：breakdown 每条只带该 metric 自己的贡献值，同一到期的 OI 与 volume 需分别看两组墙；
- dealer sign 假设：无持仓方向证据，继续禁止输出 net GEX / gamma flip（红线不变，builder 测试断言 payload 无任何 dealer-sign 字段）。

### 9.3 单票详情必须绑定同一研究版本

2026-07-31 已落地第一层绑定：

1. 新增只读端点 `GET /api/v1/opportunities/snapshots/{snapshot_key}`（`opportunity-snapshot-detail/1.0`）：返回 snapshot summary + 冻结 run payload 原文；key 格式不合法或不存在时 404 fail closed，存储异常 503。
2. 官方 canonical 榜单（`baselineSource === 'canonical'` 且有 `premarketCycle.snapshot`）跳详情时 URL 携带 `?snapshotKey=ops_…`；preview/即时扫描不携带，不伪装官方绑定。
3. 详情页带合法 snapshotKey 时优先读取冻结候选：证据、信号日、signalVersion 全部来自冻结 run，不再触发即时单票扫描；数据时点栏显示「官方快照 ops_xxx… · 冻结于 …」。
4. 快照读取失败或该标的不在冻结候选中时，显式黄条提示并回退为即时扫描；无 snapshotKey 时显示「即时扫描 · 未绑定官方快照」。
5. 期权 overview/context/walls/events 与 K 线仍为实时增强，各自携带 as-of 标签，与冻结榜单证据在页面上明确分离。

仍未完成（后续轮次）：

- ~~增强数据与冻结证据的差异仅靠 as-of 标签区分，尚无 diff 展示~~ 第一项 diff 已做（2026-07-31）：官方绑定时数据时点栏下方显示「冻结基准 close（信号日收盘）→ 当前 spot（Moomoo 时点）：±x.xx%」差异条，双值任一缺失时整条隐藏 fail closed；~~摘要文案强制同一 evidence bundle 仍未做~~ 摘要同证据束已做（2026-08-01，D-4）：官方绑定时「交易研究结论」摘要句中织入的增强数据数值（option-overview 的 IV Rank）强制带「（当前增强数据 as-of ET，非冻结榜单证据）」内联标注，模型终值区间的 IV 输入同步加注（价格基准 as-of 已由 modelBasisLabel 携带）；冻结 bundle 数值（20 日区间、EMA、量能比率）不加注，页头已声明冻结绑定；即时扫描视图无标注；由文案审计测试守护——结论侧栏任何「增强指标名 + 数字」句必须含非冻结声明（词面模式审计，无法捕捉未命名裸数字，新增增强数值须同步扩审计词表）；
- cycle id / candidate id 未随 URL 传递（当前以 snapshot_key + ticker 定位候选已足够唯一）；
- 不把 T-1 structure/T-1 underlying volume、current-session option volume、settled OI 和 current IV 合并成无来源概率（该红线继续有效）。

### 9.4 概率文案

当前页面的预期波动实现是：

- 使用 `option-overview` 的 `ivPercent`，不是某张 ATM 合约的逐合约 IV；
- 使用用户选择的 1/5/20 trading-session horizon 和 `sqrt(N/252)`，不是合约剩余到期时间；
- 价格基准优先使用当前 wall spot，否则使用 T-1 close；
- 输出模型终值区间，不是方向胜率。

这只能标为现有启发式波动区间。专业目标可以分开增加：

- 由明确 ATM contract IV 与实际到期剩余时间推导的 one-standard-deviation 终值区间；
- 历史 realized move 分位数；
- 事件条件下的历史路径分布；
- 明确回测策略的 empirical hit rate。

必须分开显示，不能统称“上涨概率”。68%/95% 正态区间是模型终值区间，不是策略胜率或路径不触碰概率。

---

## 10. API 快速地图

以下均挂在 `/api/v1` 下；内部 `/v2` 是兼容路径，不应继续作为用户可见产品名。

### 10.1 Journal

| 方法与路径 | 行为 | 是否写业务数据 |
|---|---|---|
| `GET /journal/v2/data-health` | 最新证据健康 | 否 |
| `GET /journal/v2/refresh-status` | broker/evidence/build/active 水位 | 否 |
| `GET /journal/v2/episode-builds/activation` | 当前默认 build 选择 | 否 |
| `POST /journal/v2/refreshes/preview` | OpenD 冻结刷新计划 | 只写短期 server artifact，不发布事实 |
| `POST /journal/v2/refreshes/{artifact_id}/confirm` | 显式发布刷新 | 是，append-only |
| `POST /journal/v2/imports/preview` | CSV 预览 | 否 |
| `POST /journal/v2/imports` | CSV 确认导入 | 是，append-only |
| `POST /journal/v2/openapi-imports/preview` | OpenAPI JSON 纯解析预览 | 否 |
| `POST /journal/v2/openapi-imports/plan` | OpenAPI JSON 计划 | 否 |
| `POST /journal/v2/openapi-imports/confirm` | OpenAPI 确认导入 | 是，append-only |
| `POST /journal/v2/episode-builds` | CSV-backed fallback build；无独立公开 preview | 是，append-only |
| `GET /journal/v2/episode-builds/canonical/preview` | canonical build 预览 | 否 |
| `POST /journal/v2/episode-builds/canonical` | 确认 build | 是，append-only |
| `POST /journal/v2/episode-builds/{build_id}/activate` | CAS 激活 | 是，append-only |
| `GET /journal/v2/position-episodes` | Episode 列表 | 否 |
| `GET /journal/v2/position-episodes/{id}` | Episode 详情 | 否 |
| `POST /journal/v2/position-episodes/{id}/ai-review` | 非持久化复盘 | 否 |
| `GET /journal/v2/position-episodes/{id}/review-annotations/latest` | 最新人工复盘 revision | 否 |
| `GET /journal/v2/position-episodes/{id}/review-annotations` | 人工复盘历史 | 否 |
| `POST /journal/v2/position-episodes/{id}/review-annotations` | 用户显式复盘 revision | 是，append-only |
| `GET /journal/v2/position-snapshots/latest` | 最新已确认 current snapshot 状态 | 否 |
| `POST /journal/v2/position-snapshots/preview` | 当前持仓双采样 | 只写短期 artifact |
| `POST /journal/v2/position-snapshots/{artifact_id}/confirm` | 确认 future-only snapshot | 是，append-only |
| `GET /journal/v2/position-snapshots/episode-boundary-readiness` | continuity fence | 否 |
| `GET /journal/v2/episode-builds/position-snapshot/preview?expected_fence_key=<64位小写SHA>` | exact fence-bound future preview | 否 |

表中“否”只表示不写业务数据。部分普通 GET/repository 仍可能做 schema init；只有 continuity 与 future-preview 两条路径明确从 `mode=ro` 连接读取且不初始化 schema。

旧 `POST /journal/import` 和 `POST /journal/sync-live` 是被关闭/暂停的旁路，不能恢复成旧 FIFO writer；其他 legacy API 的可变边界见 5.4。

### 10.2 今日机会

| 方法与路径 | 用途 |
|---|---|
| `POST /opportunities/daily` | 确定性基础榜 |
| `GET /opportunities/premarket/universe` | 官方研究池 |
| `PUT /opportunities/premarket/universe` | 显式保存新版本 |
| `POST /opportunities/premarket/status` | cycle 状态 |
| `POST /opportunities/premarket/run` | 受门禁的手动运行 |
| `GET /opportunities/snapshots` | 已冻结结果列表（summary，不含 candidates） |
| `GET /opportunities/snapshots/{snapshot_key}` | 单个冻结快照 + 冻结 run payload 原文（详情页官方绑定用） |
| `POST /opportunities/option-overview` | 期权概览 |
| `POST /opportunities/option-walls` | 执行价墙 |
| `POST /opportunities/option-events` | 期权事件 |

修改 API/Schema 时必须同时检查后端、Web、Desktop 兼容，并优先追加字段而不是破坏旧字段。

---

## 11. 配置与权限

### 11.1 最小 Moomoo 配置

只在本地 `.env` 中配置：

~~~dotenv
MOOMOO_OPEND_ENABLED=true
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111

MOOMOO_JOURNAL_REFRESH_ENABLED=true
MOOMOO_JOURNAL_ENV=LIVE
MOOMOO_JOURNAL_ACCOUNT_ID=
MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET=
MOOMOO_JOURNAL_QUERY_TIMEOUT_SECONDS=15
MOOMOO_JOURNAL_REFRESH_TIMEOUT_SECONDS=180
~~~

仓库 `.env.example` 的自动化默认值是关闭：

~~~dotenv
MOOMOO_PREMARKET_PREFETCH_ENABLED=false
PREMARKET_RESEARCH_SCHEDULER_ENABLED=false
OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED=false
~~~

当前本机 `.env` 与默认值不同：

- `PREMARKET_RESEARCH_SCHEDULER_ENABLED=true`；
- `OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED=true`；
- `MOOMOO_PREMARKET_PREFETCH_ENABLED` 未设置，使用默认 false；
- API lifespan 已实际启动盘前研究与 outcome maintenance scheduler。

因此当前 Web 服务不是“零业务写”环境：即使使用 `--serve-only`，FastAPI lifespan 仍可能按门禁更新协调状态、发布盘前结果或回填到期 outcome。做零写验收必须使用隔离的临时数据库/配置并关闭 scheduler，不能只依赖命令名。

当前本机 `ADMIN_AUTH_ENABLED=false`、`WEBUI_HOST=127.0.0.1`、`WEBUI_PORT=8000`。在没有 admin auth 时必须显式只监听 localhost；严禁把服务绑定到 LAN 或 `0.0.0.0`。若未来开放远程访问，先完成认证、TLS、反向代理和权限审查。

### 11.2 数据源能力分层

| 能力 | 首选/已接入 | 降级原则 |
|---|---|---|
| Moomoo 历史账户事实 | OpenD + History CSV | 失败时不写账本 |
| 当前持仓 | OpenD LIVE/US 双采样 | 失败不可伪装为空仓 |
| 美股 K 线 | Moomoo/Alpaca/yfinance 等现有 provider chain | 明确 source、as-of、session |
| Regime | SPY、VIX、sector、structure、premarket 等分域 | 子域失败只降级相应域 |
| 期权墙 | Moomoo option chain/snapshot | 保留 coverage；不猜缺失 IV/OI |
| 异常期权流/暗池 | 当前仅部分/候选来源 | 缺权限时不能假装“无事件” |
| AI | 项目配置的 provider | 失败回退 deterministic |

具体申请、权限和冒烟命令见[期权研究数据配置指南](./integrations/options-research-data-setup.md)和[Moomoo 订阅说明](./integrations/moomoo-subscription.md)。

### 11.3 健康状态必须分层

2026-07-31 已落地第一层：`GET /api/v1/system/health-layers`（`system-health-layers/1.0`）返回 6 个分层只读探测——api_process、opend_tcp（TCP 可达≠已登录）、moomoo_sdk、journal_refresh_config（env 就绪度，不回显 secret）、premarket_publication（最新官方发布水位）、outcome_maintenance（最新维护 session）——每层独立 try/except，单层失败只降级该层，端点永不因探测失败 500；overall 聚合 down > degraded/unknown > ok。前端 TopBar「健康」按钮（`SystemHealthPopover`）点击按需拉取并分层展示，未知层按原名展示不崩溃。quote entitlement、option chain、breakout subscription 三层未接。完整目标仍是至少区分：

1. Web/API process health；
2. OpenD TCP liveness；
3. SDK query health；
4. account binding；
5. Journal refresh readiness；
6. quote entitlement/coverage；
7. option chain health；
8. breakout subscription health；
9. scheduler last successful publication。

---

## 12. 本机运行与运维

### 12.1 当前进程快照

2026-07-31 19:41（Asia/Shanghai）实机核对：

- `com.dailystock.uvicorn` 正在运行；
- wrapper PID 2423，Uvicorn child PID 2428；
- `GET http://127.0.0.1:8000/api/health` 返回 200 / `status=ok`；
- `GET /api/v1/system/moomoo-status` 返回：
  - `enabled=true`；
  - `sdk_installed=true`；
  - `connected=true`；
  - `read_only=true`；
  - `probe_level=tcp`；
  - host `127.0.0.1` / port `11111`；
- transport `trd_env=SIMULATE`；
- SDK `10.09.6908`。

注意：状态栏里的 SIMULATE 是通用 transport 状态；Journal snapshot/refresh 使用独立 `MOOMOO_JOURNAL_ENV=LIVE` 读取真实账户，仍然只读。

当前网站由未提交源码和被 Git 忽略的 `static/` bundle 共同运行，GitHub clean clone 无法复现这份页面。当前 Uvicorn 也没有 `--reload`；本轮后续修改源码不会自动进入运行进程，必须重新构建前端并重启服务后才能做最终 UI 验收。

### 12.2 手动启动

优先：

~~~bash
python main.py --serve-only --host 127.0.0.1 --port 8000
~~~

`--serve-only` 只表示不自动执行主分析任务，不会关闭 FastAPI lifespan 内已启用的盘前研究和 outcome scheduler。

需要重新构建 Web：

~~~bash
cd apps/dsa-web
npm ci
npm run build

cd ../..
python -m uvicorn server:app --host 127.0.0.1 --port 8000
~~~

检查：

~~~bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/v1/system/moomoo-status
~~~

`scripts/launchagent_status.sh` 在 Codex filesystem/network sandbox 中可能错误报告“未加载/不可达”。需要判断真实机器状态时，以机器侧 curl 和下列明确 label 为准：

~~~bash
launchctl print "gui/$UID/com.dailystock.uvicorn"
launchctl print "gui/$UID/com.dailystock.moomoo-sync"
launchctl print "gui/$UID/com.dailystock.breakout-live"
~~~

### 12.3 LaunchAgent 现状

| Agent | 当前状态 | 风险 |
|---|---|---|
| `com.dailystock.uvicorn` | 已加载并运行；使用当前 rotation wrapper | 当前相对健康 |
| `com.dailystock.moomoo-sync` | 已加载，每 900 秒触发；last exit=3 | 旧 plist 无 wrapper；脚本虽安全暂停，仍会产生日志 |
| `com.dailystock.breakout-live` | 已加载并运行，PID 4285 | 安装的是 4 月旧 plist；持续 OpenD reconnect/subscribe 错误，不能算健康 |

仓库内新模板已接日志轮转，但系统安装的 sync/breakout plist 仍是旧副本。不要直接覆盖系统 plist；先：

1. 比较安装副本与仓库模板；
2. 明确是否保留该任务；
3. 在用户确认下 unload；
4. 备份旧 plist；
5. 安装新模板；
6. 验证启动、退出码、日志和 OpenD 行为；
7. 再决定是否长期启用。

### 12.4 日志

当前 `logs/` 约 218 MB，其中约 200 MB 是 2026-07-20 runaway 事故的压缩归档。

已有护栏：

- Uvicorn stdout/stderr：活动文件 10 MiB，3 个备份；
- 应用日志：按文件大小轮转；
- SDK console/log 尽量压低。

剩余问题：

- ~~应用日志按日期创建，缺少跨日期 retention~~ 机制已实现（2026-07-31）：`src/logging_config.py` 的 `cleanup_old_logs` 按 `LOG_RETENTION_DAYS` / `LOG_RETENTION_MAX_TOTAL_MB` 在 `setup_logging` 时清理同前缀按日期文件；**默认 0=关闭**，本机 `.env` 未启用（删除属破坏性动作，须用户显式配置开启）；只匹配本前缀 `*_{YYYYMMDD}.log(.N)`，当天文件与 `logs/archive/` 永不受影响，有 7 个安全边界测试；
- sync/breakout 安装副本仍绕过 wrapper；
- 历史大归档（logs/archive/ 约 200 MB）不会自动清理，删除仍需用户明确同意。

处理原则：

- 先列文件、日期、大小和打开句柄；
- 确认不再被进程使用；
- 用户明确同意后再删除或归档；
- 不执行宽泛 `rm -rf`；
- 增加“按天数 + 总体积”的 retention，并为删除写测试；
- 日志不得包含 API key、完整 query string、账户 ID 或原始交割单内容。

---

## 13. Git 与 GitHub 交接

### 13.1 只读核对命令

~~~bash
git branch --show-current
git rev-parse HEAD
git status --short --branch -uall
git diff --stat
git diff --numstat
git diff --check
git remote -v
git ls-remote origin refs/heads/main refs/heads/codex/options-research-workbench
git rev-parse main
~~~

`git ls-remote` 只读取远端 SHA，不更新本地 `origin/main`。应把其输出的 `refs/heads/main` SHA 与 `git rev-parse HEAD` 直接比较；不能在未 fetch 时用缓存的 `HEAD...origin/main` 代替远端核对。

### 13.2 当前已核实事实

~~~text
branch: codex/options-research-workbench
HEAD:   4dc04fdd0a98682aba4e9555adb607d7ab4ae857
origin: https://github.com/Cthloveross/Daily-Stock.git
GitHub main: 4dc04fdd0a98682aba4e9555adb607d7ab4ae857
upstream: none
remote work branch: absent
staged: 0
~~~

GitHub connector 还确认：

- repository default branch 是 `main`；
- 当前用户具有 push/admin 权限；
- commit `4dc04fd` 的 combined status 没有可用 status entries。

权限存在不等于应立即推送。

### 13.3 当前 GitHub CI 真相

- 当前 SHA 没有完整 backend/web/docker gate 证据；
- 观察到的 Auto Tag run 为 skipped；
- `.github/workflows/ci.yml` 文件存在，但 GitHub workflow API 当前返回 `state=deleted`；
- 该文件只监听发往 `main` 的 `pull_request`，没有 `push` 或 `workflow_dispatch`；
- Network Smoke 等定时 workflow 因 inactivity 停用；
- 最近旧 Network Smoke 成功验证的是 `db94455`，不是当前工作树。

因此不能说“GitHub CI 已验证当前改动”。

### 13.4 发布前的安全顺序

未经用户明确确认，不执行 commit 或 push。获得确认后也应按顺序：

1. 记录当前分支、HEAD、工作树清单和数据库 hash；
2. 检查没有 key、账户标识、CSV、DB、日志或构建垃圾进入 diff；
3. 审阅所有 untracked 文件，区分产品代码、测试、文档与本地 artifact；其中包含被 tracked router/import 依赖的运行必需代码，禁止只用 `git add -u` 或 `commit -am`；
4. 按 dependency closure 暂存必需 endpoint/schema/ledger/service/tests/docs；`.env.example` 当前也是 untracked，确认只有占位值且无秘密后才能纳入；
5. 冻结改动范围，不夹带无关重构；
6. 跑第 14 节全量 gate，并从暂存 index 或 clean clone 验证 imports 与启动，避免漏掉 untracked dependency；
7. 根据文件职责拆分可审阅 commit，英文 commit message，不加 Co-Authored-By；
8. 先恢复并确认 GitHub workflow 为 active；
9. 优先推 `codex/*` 分支并开往 `main` 的 PR，观察该 SHA 的真实 backend/web/docker run；仅有 workflow 文件不算证据；
10. 若用户再次明确要求直推 main，也必须先 dry-run、确认远端 main 未移动；当前配置下直推不会触发完整 gate，不能声称 Actions 已验证；
11. 推送后核对远端 SHA；只有真实 workflow run 通过，才记录远端 CI 证据；
12. 绝不把本地 DB、`.env`、日志上传。

回滚不能依赖“GitHub 上应该有”。当前未提交工作树的第一保护目标是创建可审计 Git 记录；在此之前，机器损坏会丢失最新源码。

---

## 14. 验证矩阵

### 14.1 最近本地证据

截至本手册快照，本地最近一次完整验证记录为：

- `./scripts/ci_gate.sh`：
  - 2,520 passed；
  - 2 skipped；
  - 6 deselected；
  - 106 subtests passed；
  - gate 全通过。
- Web：
  - 63 test files；
  - 526 tests passed；
  - lint 0 error；
  - 1 个已知 TanStack `useReactTable` warning；
  - production build 与 TypeScript 通过。
- Future snapshot/continuity targeted suite：
  - 关键连续性与 CAS 测试复核通过。
- 浏览器：
  - `/journal?tab=positions` 正常；
  - canonical preview 正常；
  - 当前 snapshot 为 `no_snapshot`；
  - future preview 按条件隐藏；
  - console 0 error。
- Live API：
  - canonical preview 200；
  - 5,974 events；
  - 1,441 episodes；
  - fees 151,750.75；
  - fake/stale fence 返回 409。
- 零写验证：
  - 正式 DB 主文件 size/mtime/SHA 在探测前后未变化；
  - 无交易动作。

验证时序必须如实解释：完整 backend/Web gate 之后又落过最后一组 compatibility patch；该补丁只复跑了对应 targeted tests，前端未再变化。上述结果是本地终端记录，不是当前最终工作树的完整发布证据，也没有持久化 JUnit/coverage artifact；发布前必须重跑全量 gate。

SQLite 处于 WAL 模式，当前存在约 3.9 MB `-wal` 和 `-shm` sidecar。主 DB 文件 hash 不变不能单独证明“文件系统零写”；只读流程也可能维护 WAL/SHM。可信验收应检查业务表 count/content hash、连接 `query_only`、交易动作边界，并把“零业务写”与“目录零字节变化”分开。

### 14.2 发布前必跑

后端：

~~~bash
./scripts/ci_gate.sh
python -m pytest -m "not network"
~~~

Web：

~~~bash
cd apps/dsa-web
npm ci
npm run lint
npm run test
npm run build
npx playwright test e2e/smoke.spec.ts
~~~

Playwright 命令会自行启动隔离后端（端口 8765 + 独立 `ENV_FILE` + 一次性空白
SQLite，位于 `apps/dsa-web/e2e/.artifacts/`，已 gitignore），不读取真实
`.env`、不触碰 `data/stock_analysis.db`，也不复用本机常驻正式后端；运行前无需
停掉 LaunchAgent。浏览器使用本机 Chrome（`channel: 'chrome'`），无需
`npx playwright install`。

按改动追加：

- API/Schema：后端测试 + Web build + 真实 endpoint smoke；
- Journal ledger：append-only trigger、幂等、篡改、费用守恒、零交易 AST；
- snapshot/fence：stale fence、账户 mismatch、guard window、同一只读事务、零业务写入；
- opportunity：provider timeout、partial coverage、same-snapshot contract、统计 track 隔离；
- UI：桌面与移动关键流程的真实浏览器 E2E；
- LaunchAgent：退出码、信号转发、rotation、实际安装副本。

### 14.3 E2E 套件的可信范围

`apps/dsa-web/e2e/smoke.spec.ts` 已按当前 UI 语义重写，并跑在隔离空库后端上
（见 §14.2 的 Playwright 说明）。可以信任的范围：

- `/` 与 `/login` 在认证关闭时重定向到 `/regime`；
- `/regime` 官方盘前研究卡的诚实未发布空态（发布状态收敛、后台自动研究未启用、
  “数据时点”口径行、尚无可统计样本）；
- TopBar 分层健康弹层端到端打通 `GET /api/v1/system/health-layers`，
  OpenD / SDK / Journal 刷新 / 盘前发布 / 结果回填在关闭 env 下全为“未启用”；
- `/journal?tab=positions` 与 `?tab=import` 的空态渲染，无未预期
  console/page error（空库上 `episode-builds/canonical/preview` 的诚实 404
  是唯一放行的资源加载失败）；
- `/regime/opportunity/AAPL` 深链 shell 与“即时扫描 · 未绑定官方快照”标注。

它仍然不能替代：登录开启（`ADMIN_AUTH_ENABLED=true`）流程、有数据状态下的
Journal/机会页面行为，以及任何写路径验收。`e2e/report-markdown.spec.ts` 依赖
已下线的旧首页 UI，已显式 `test.describe.skip`（文件内有 TODO），在
ReportMarkdown 于当前 UI 有稳定入口前不得作为验收证据。

---

## 15. 已知问题与技术债

### P0：接手前必须处理

1. 大量新代码只在本地，未 commit、未 push；
2. 78 个 untracked 中包含 tracked router 已依赖的运行必需产品代码；漏提任一 dependency 会让远端不可导入；
3. ~~当前正式 DB 没有发现覆盖 07-30 事实的已验证 SQLite-safe repo-local backup~~ 已解决（2026-07-31）：`data/backups/stock_analysis-handoff-baseline-20260731.db`，integrity ok、49 表 count 一致、sha256 已记录于第 6 节；
4. GitHub 无当前改动的完整 CI；
5. sync/breakout 的系统 plist 仍是旧副本；
6. breakout runner 日志显示重连/订阅异常；
7. 日志无跨日期 retention；
8. 文档存在少量“未连接”旧描述。

### P1：可信产品闭环

1. future preview 缺正式 append-only build/activation；
2. canonical build #2 已存在但尚未激活；这是用户选择，不是应自动“修复”的技术债；
3. review annotations 为 0，说明尚无用户复盘输入；AI/开发者不得为了填数伪造；
4. 既有历史 opening snapshot 缺失无法被今天的 snapshot 追溯修复；旧 build 会继续保持 `assumed_flat_unverified`，新 snapshot 只服务 forward episodes；
5. 组合单组级费用不能合法下沉到腿；
6. ~~详情页不绑定同一 official opportunity snapshot~~ 第一层已解决（2026-07-31）：官方榜单深链携带 snapshotKey，详情页优先读冻结证据并显式标注绑定/回退/即时扫描，见 9.3；差异 diff 展示仍待做；
7. ~~EMA seed 不统一~~ 已解决（2026-07-31）：共享 `utils/ema.ts` + 手算/后端 parity 测试，见 7.3；
8. 本地 Watchlist 与官方研究池容易被用户混淆。

### P1.9（2026-07-31 新发现）：`-m "not network"` 套件不完全封闭 —— 已系统性修复（2026-08-01）

原问题：套件中任何一个测试触发 `get_config()` 都会 `load_dotenv` 把本机 `.env` 的 `MOOMOO_OPEND_ENABLED=true` 注入 `os.environ`，此后所有「Moomoo 优先、fallback 兜底」的代码路径在测试里都可能打到真实 OpenD。此前该失败只在盘中 OpenD 有实时报价时出现，离线运行会假性通过。

系统性修复（2026-08-01）：仓库根新增 `conftest.py`，autouse fixture 在每个**非 network** 测试开始前把 `MOOMOO_OPEND_ENABLED` / `MOOMOO_JOURNAL_REFRESH_ENABLED` / `MOOMOO_PREMARKET_PREFETCH_ENABLED` / `PREMARKET_RESEARCH_SCHEDULER_ENABLED` / `OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED` 强制为 `"false"`（写 false 而非删除：`load_dotenv` 默认 `override=False`，已存在的值不会被 `.env` 中途覆盖）。带 `@pytest.mark.network` 的测试保持进程环境原样可继续 opt-in；单测试自身的 `monkeypatch.setenv(..., "true")` 在该 fixture 之后执行，仍可覆盖（`tests/test_moomoo_runtime.py` 等 opt-in 用法不受影响）。`test_iv_rank.py` 的局部 autouse fixture 保留作为纵深防御。回归证明：`tests/test_env_isolation_conftest.py` 在导出 `MOOMOO_OPEND_ENABLED=true` 的 shell 下断言未打补丁的测试看到的开关全为 false、`moomoo_options._enabled()` 为 False；验证命令 `MOOMOO_OPEND_ENABLED=true python -m pytest src/options/tests/test_iv_rank.py tests/test_env_isolation_conftest.py -q`。

### P2：研究质量

1. 完整研究 track 还没有 qualified 样本；
2. 20D outcome 尚未成熟；
3. OI、IV、成交量和日线结构混合时间基准；
4. 暗池/异常期权流权限不足时，只能显示 unavailable；
5. 概率摘要需要经校准的独立模型，不能由文案拼接；
6. 移动端表格和关键流程没有可信 E2E。

### P3：体验与性能

1. 页面信息密度仍需按“结论 → 条件 → 证据 → 细节”收敛；
2. AI 模型增强需要更明确的阶段耗时和缓存；
3. 多标签页仍可能竞争某些 provider 限额；
4. 健康状态还没有统一分层面板；
5. 旧 Phase 0/overview 文档需要标 stale 或更新。
6. ~~基础相对量是 T-1，但当前页面总括提示写成“Volume＝本交易日累计”~~ 已解决（2026-07-31）：提示已区分 T-1 相对量能与当日期权成交量，并有测试断言。
7. ~~过期 refresh/snapshot artifact 清理尚未实现~~ 已实现（2026-08-01，F-2a：显式 CLI dry-run/apply + append-only 回执，见 `New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md`）；refresh preview 的可恢复异步 job 与持久化 last failure 尚未实现。
8. strict anchor replay 会重复构造 full builder objects；non-empty persisted canonical → future preview 仍需真实 DB E2E。

---

## 16. 后续路线图与验收

### 阶段 A：保护与恢复可交付状态

目标：先保护最新源码和数据库；只有得到用户明确授权后，才 commit/push/发布。

验收：

- 精确审阅全部 modified/untracked；
- 无 secrets/DB/logs/CSV；
- 用 SQLite backup API 创建覆盖当前事实的备份，并通过 integrity/count/content-hash 验证；
- 全量 backend + Web gate 通过；
- commit 结构可审阅；
- GitHub 分支或 main 与批准的本地 SHA 一致；
- workflow 已恢复 active，且目标 SHA 的真实 PR run 提供完整 Actions 证据；否则明确记录远端验证缺口；
- 可从干净 clone 启动。

### 阶段 B：完成未来仓位生命周期

目标：从已确认当前持仓锚点安全生成下一段真实 Episode。

验收：

- snapshot → refresh B → ready fence → preview；（已实现）
- 新增独立、显式的 formal future-build confirm/write contract；不得把现有 GET preview 或 canonical POST 偷改成写；（切片 1 已实现，2026-07-31）
- confirm 冻结 snapshot id/key、fence policy/key、anchor + publication chain、target canonical id/hash/source cutoff、boundary/guard、source batches、builder config 与 evidence hash；（切片 1 已实现）
- append-only build；（切片 1 已实现）
- 有意扩展 activation eligibility 以支持 future build，同时保留 CAS 与所有 acceptance；（切片 2 已实现，2026-08-01：无 schema 变更，激活 fence build 时以 fence link 冻结的目标事实集身份写入 NOT NULL canonical 列；新增 additive `accept_left_censored_openings` 门禁，activation key 仅在置真时参与派生；CAS、assumed-flat、组费 acceptance 全部保留；CSV build 仍不可激活；决策记录见 `New-docs/phase1/12_FORMAL_FUTURE_BUILD_CONTRACT.md` §6）
- stale fence/账户变化/边界成交全部阻断；（切片 1 已实现）
- left-censored/opening cost 限制完整显示；（切片 2/3 已实现：confirm 与 activation 双门禁 + Web 勾选文案）
- 正式库零交易动作；（全链路保持）
- 有真实小样本演练和 rollback。（未完成：正式库当前 0 confirmed snapshot，真实 fence build confirm + activation 演练要等用户完成一次 snapshot confirm + 后续 refresh publication；rollback＝再激活其他 source-linked build，无法回到「零激活」CSV 默认态，UI 已明示）

### 阶段 C：把复盘变成个人 Playbook 输入器

目标：每个高价值 Episode 有结构化计划、执行与反思。

验收：

- Review Queue 默认突出大盈、大亏、高费、异常持有和证据不完整案例；
- annotation 与真实 schema 对齐：`setup_thesis`、`entry_trigger`、`invalidation_plan`、`position_rationale`、`exit_reason`、`post_trade_reflection`，以及 tags/error_types；
- AI 只比较“用户自述”与“可观察证据”；
- EMA 统一；
- K 线和成交 marker 经时区/盘段测试；
- 同类样本 dashboard 不混 policy/regime/direction；
- 未达门槛不显示 edge。

### 阶段 D：让今日机会成为可解释研究台

目标：每天只给少量、证据明确、与个人风格相关的候选。

验收：

- 官方 Top 5 与单票详情绑定同一 snapshot；
- 页面首屏只显示摘要、确认条件、失效条件和关键墙位；
- 每个指标有 source/as-of/readiness；
- OI/IV/flow/暗池缺失逐域降级；
- 期权墙默认可见；
- probability 模型命名、回测和校准明确；
- 35 秒预算内返回基础榜，增强独立加载；
- last-good 与当前刷新状态区分。

### 阶段 E：建立结果学习闭环

目标：从历史与前瞻结果中验证规则，而不是追逐单笔。

验收：

- 5D/20D outcome 均按 XNYS 精确目标日；
- raw path、日线选股、完整研究三条 track 隔离；
- 至少满足约定样本数和独立交易日数；
- walk-forward，不把回填数据当 prospective；
- 版本化权重；
- false positive、missed opportunity、regime mismatch 可追溯；
- 任何权重变化需要人工审批。

### 阶段 F：生产稳态

验收：

- LaunchAgent 安装副本与仓库模板一致；
- 日志有单文件、备份数、天数和总体积上限；
- health 分层；
- scheduler lease/recovery 有监控；
- GitHub PR/main CI 恢复；
- clean clone runbook 实测；
- 数据备份、schema migration 和恢复演练完成。

---

## 17. 故障排查

### 页面一直“扫描中”

1. 打开浏览器 Network，看是 `/daily`、`/premarket/status` 还是增强请求；
2. 确认页面是否保留 last-good；
3. 查基础榜 35 秒预算；
4. 查服务端 cycle 是否已有 owner/lease；
5. 区分 provider timeout 与“无数据”；
6. 不要用无限重试；
7. 不要让 Regime 或单一 option enhancement 阻塞 Top 5。

### 官方发布每天都是 degraded（events 已解决 + premarket 不可用）

2026-08-01 已定位根因并解决 events 侧；premarket 侧仍是配置缺口：

1. `regime_supporting_events_degraded`（**已解决，2026-08-01**）：原因是 Finnhub `/calendar/economic` 对免费档 key 返回 403 Forbidden（付费端点）。现在 FOMC/CPI/NFP 经济序列改由零成本官方年度日程提供（`src/regime/official_schedule.py` + `src/regime/data/official_economic_schedule_2026.json`，来源 federalreserve.gov FOMC 日历与 bls.gov CPI / Employment Situation 发布日程页，文件内记录 per-series source_url + retrieved_at + coverage），`get_macro_events` 不再调用 Finnhub 经济日历端点，403 日志噪音随之消失；Finnhub 只保留 earnings 日历。官方日程覆盖窗口内 economic_calendar readiness=ready，earnings 同时可用时 events 域整体 ready。
   - **年度运维步骤**：日程 fail closed——`target_date + 7 天`窗口超出 coverage 即回到 `unavailable`，不会伪装“今天没有事件”。当前 coverage_through 由 BLS 序列决定（NFP 到 2026-12-04，即 2026-11-27 之后 events 会诚实地重新降级）；BLS/美联储发布来年日程后，需从官方页面刷新 `src/regime/data/official_economic_schedule_*.json`（新增年度文件或扩展现有文件均可，loader 会合并）。
2. `regime_supporting_premarket_unavailable`（**仍未解决**）：盘前活动域唯一接线的数据源是 Alpaca，本机 `.env` 未配置 ALPACA key。`MoomooFetcher.get_premarket` 适配器已存在但未接线（`src/regime/fetchers.py` 注释：同步 SDK 冷历史调用可能超预算且无法安全取消）。解决选项：配置 Alpaca key（有免费档）、或评审 Moomoo 接线的预算隔离方案。

在 premarket 解决前，quality=degraded（premarket 单域）仍是诚实且预期的状态；基础 Top 5 不受影响，但候选被排除出严格完整研究统计（full_research track 持续为 0 的原因之一）。

### 所有候选都“证据不足”

1. 查看 analysis quality 和逐域 readiness；
2. 查日线是否足够新鲜完整；
3. 查 Regime 核心域；
4. 查 option wall coverage；
5. 查 provider 是否 401/403/429/timeout；
6. 查 official snapshot 是否 degraded；
7. 不要为消除黄色提示而放松门槛。

### AI 复盘不可用或很慢

1. 先用 `enhance=false` 验证确定性层；
2. 查看本地是否配置有效 model provider；
3. 检查 provider timeout；
4. 确认错误是否降级而不是 500；
5. 检查行情加载阶段；
6. 不在日志输出 key/prompt/账户事实；
7. 当前 ChatGPT 登录不能替代 API key。

### EMA 看起来不对

1. 记录 interval、盘段和当前 visible bars；
2. 检查是否 SMA-seeded；
3. 检查数据是否从首 close seed；
4. 检查扩展时段切换后是否重算；
5. 检查不完整 bar；
6. 用固定 fixture 对比；
7. 修共享算法，不能只调 CSS。

### Moomoo 显示可达但数据缺失

1. `/system/moomoo-status` 的 tcp 只证明端口；
2. 检查 OpenD 登录；
3. 检查账户和市场权限；
4. 检查 Journal binding；
5. 检查 quote entitlement；
6. 检查请求频率；
7. 查看去敏后的具体 reason；
8. 不把失败当空仓或零事件。

### 当前持仓一直等待

按顺序检查：

1. 是否有已确认 Journal publication；
2. snapshot 是否两轮稳定；
3. snapshot 是否已经 confirm；
4. boundary 后是否又有一个 confirmed refresh；
5. target canonical 是否发布；
6. guard window 是否有成交；
7. account binding 是否一致；
8. fence 是否已经 stale。

### 网页可看，但修改没生效

1. 查看 `static/` build 时间；
2. 对比源码修改时间；
3. 重新跑 `npm run build`；
4. 重启后端；
5. 强制刷新页面；
6. 检查浏览器打开的端口；
7. 不把旧 bundle 误当新源码。

---

## 18. 数据保护、备份与回滚

### 18.1 不进入 GitHub

- `.env`；
- Moomoo CSV/JSON 原始文件；
- `data/*.db`、`-wal`、`-shm`；
- `logs/`；
- 本地缓存；
- 包含账户标识的截图或导出；
- build artifact，除非仓库已有明确发布合同。

### 18.2 数据库改动前

1. 阻止新写请求并 quiesce/停止所有正式 DB writer：至少包括 Uvicorn/API-hosted schedulers、确认/legacy 写端点、手动分析或维护任务，并核对相关 LaunchAgent；若只做 SQLite live backup 可保持服务，但必须记录一致性边界；
2. 记录 DB、WAL/SHM 状态及关键业务表 count/content hash；
3. 使用 SQLite backup API 或等价的一致性快照，不复制一个可能未 checkpoint 的主文件冒充完整备份；
4. 在副本上跑 migration；
5. 验证 append-only triggers；
6. 对账 count/hash/fees；
7. 再在用户确认下操作正式库。

### 18.3 回滚层级

| 改动 | 回滚 |
|---|---|
| Web UI | 回退对应源码 commit，重建 static |
| 后端纯逻辑 | 回退 commit，重跑 gate，重启 |
| 新配置 | 恢复 `.env.example`/文档和本地值 |
| LaunchAgent | unload 新模板，恢复备份 plist |
| Append-only build | 不删除；通过新的 activation 切回旧 build |
| 错误证据 batch | 按 incident 处理：立即冻结消费/写入，先做当前 forensic backup 并计算影响范围；只有确认没有后续事实且用户批准时才可整库恢复，否则应在新库重建或先实现 append-only supersession policy；当前没有通用“追加废弃”能力 |
| Schema | 从已验证备份恢复，不手工删表 |

---

## 19. 给下一位 Codex/开发者的执行协议

每次“继续”都按以下循环，不凭感觉扩功能：

1. 读 `AGENTS.md` 和本手册；
2. 只读核对 Git、服务、DB 和最新测试；
3. 选路线图中一个最小可验收目标；
4. 写清：
   - 用户问题；
   - 证据；
   - 失败边界；
   - 数据/时间合同；
   - UI 结果；
   - 测试；
   - 回滚；
5. 先复用现有模块；
6. 最小改动；
7. 本地验证；
8. 真实浏览器验证；
9. 更新专题文档和 `docs/CHANGELOG.md`；
10. 未获明确确认不 commit/push；
11. 交付时说明已验证、未验证、风险和下一步。

推荐 Goal prompt：

> 持续把 Daily-Stock 建设为一个证据优先、永久只读的个人美股期权 Trading OS。以 Moomoo 真实成交与当前持仓为事实源，完成可重放的 canonical 账本、PositionEpisode 生命周期、结构化单笔复盘、个人 Playbook 和每日 Top 5 机会研究闭环。每轮先核对当前 Git/运行/数据库真相，只选择一个最小且可验证的瓶颈；所有结论必须携带 source、as-of、readiness 和缺口，缺证据时 fail closed；严禁下单、解锁交易、伪造概率、混合不同时间点或用单笔宣称 edge。实现后运行与改动面匹配的后端、前端和真实浏览器验证，更新专题文档与 changelog；未经我明确确认不得 commit 或 push。只在满足验收标准后进入下一阶段，并始终报告改了什么、为什么、验证、未验证、风险、回滚和下一步。

---

## 20. 接手检查清单

开始前：

- [ ] 已读 `AGENTS.md`；
- [ ] 已读本手册；
- [ ] 已记录 branch/HEAD/status；
- [ ] 已确认 GitHub main SHA；
- [ ] 已确认没有把本地 main 当当前分支；
- [ ] 已确认数据库和 secrets 不进入 Git；
- [ ] 已确认当前服务/LaunchAgent 真实状态；
- [ ] 已确认 Moomoo 只读边界；
- [ ] 已选一个最小目标。

完成前：

- [ ] 数据 source/as-of/readiness 明确；
- [ ] 缺失值 fail closed；
- [ ] append-only/幂等/并发边界有测试；
- [ ] 无 unlock/order/modify/cancel；
- [ ] backend gate 按风险执行；
- [ ] Web lint/test/build 按风险执行；
- [ ] 真实浏览器关键流程已看；
- [ ] 文档和 changelog 已更新；
- [ ] 未验证项写明；
- [ ] 回滚方式写明；
- [ ] commit/push 得到明确授权；
- [ ] 推送后远端 SHA/CI 再核对。

---

## 21. 本次交接结论

项目的核心方向是正确的：已经从“展示很多行情指标”转向“以真实交易证据构建可审计的个人交易系统”。目前最需要的不是再增加一个卡片，而是把现有链路做成同一时间点、同一证据版本、同一统计口径的完整闭环。

接手后的第一优先级应当是先保护当前本地源码与正式数据库，在用户明确授权后再 commit/push，并恢复 GitHub CI；第二优先级是完成 snapshot → continuity fence → formal future build → activation；第三优先级是让复盘 annotation 和 official Top 5 snapshot 真正进入可积累的个人 Playbook。只有在这三件事稳定后，新增暗池、异常期权流或概率模型才会产生可靠价值。
