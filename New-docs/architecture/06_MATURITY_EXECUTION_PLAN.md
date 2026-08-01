# 成熟度执行计划：从当前状态到完整专业交易研究系统

> 状态：执行基线（2026-08-01）；每完成一项在本文更新状态与证据
>
> 原则：每项 = 明确交付物 + 可验证验收 + 依赖 + 风险；没有验收标准的不开工。
> 红线不变：Moomoo 永久只读；缺数据 fail closed；不伪造概率；未经确认不改变默认视图。

## 阶段 B · 未来仓位生命周期（收尾中）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| B-1 | 正式 future build 写路径 | `position_snapshot_episode_build.py` + fence link 表 + 端点 | T1-T9 + 对抗验证 4/4 HOLDS + 全量 gate | ✅ 2026-07-31 |
| B-2 | activation 支持 future build | 资格分支（target canonical 身份，零 schema 改动）+ `accept_left_censored_openings` + resolver/data-health 兼容 + 前端激活卡（含不可逆警告） | 5 项 fail-closed 回归 + 全量 gate 2,578 + web 555 全绿 | ✅ 2026-08-01 |
| B-3 | 前端 confirm UI | preview→确认块+acceptance 勾选+409 引导 | 组件回归 + 真实页面 | ✅ 2026-08-01 |
| B-4 | 真实小样本演练 | 用户完成一次 snapshot confirm → 次日 refresh → fence ready → 正式 build → 激活 | 正式库真实链路走通 + 回滚演练（激活回旧 build） | ⛔ 等用户交易日操作 |

## 阶段 C · 复盘 → Playbook（设计已冻结，C-1/C-2/C-3 已实现）

合同：`New-docs/phase1/13_PLAYBOOK_PROMOTION_CONTRACT.md`

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| C-1 | 模式观察聚合（零写） | `GET /journal/v2/review-insights`：按 tag/error_type × 方向 × boundary policy 分桶；verified 样本 <10 笔或独立交易日 <5 只显示计数不显示比率；条件性 P&L 单独计数永不入统计（`src/journal/ledger/review_insights.py` + `ReviewInsightsPanel.tsx`，锚点见合同 §3） | 门槛 fail-closed 测试 + 分桶不混轨测试 + 零写断言 + 「模式观察」面板渲染回归 | ✅ 2026-08-01 |
| C-2 | Playbook 表与晋升 | 2 张 append-only 表（candidate/rule 版本链）+ 晋升冻结证据快照（episode ids+build+revisions+聚合数字+as-of）+ CAS 晋升/退役端点（`src/journal/ledger/playbook_models.py` + `playbook_repository.py` + `/journal/v2/playbook*` + `PlaybookPanel.tsx`，锚点见合同 §3） | 晋升快照可重放、deny triggers、不反写任何权重：仓储/端点/组件回归全绿（桶缺失 fail-closed 409、幂等重放、退役快照逐字复制） | ✅ 2026-08-01 |
| C-3 | 复盘页反向链接 | 单笔页显示命中的 candidate/rule：`GET /journal/v2/position-episodes/{id}/playbook-links` 只匹配同构建冻结快照的 `episode_ids`；截断样本无法确认时以独立「可能相关」条目诚实标注（`list_playbook_links_for_episode` + `EpisodePlaybookLinksPanel.tsx`，锚点见合同 §3） | 零写断言 + 构建不匹配排除 + 截断 possible/省略 + 404 scope + 面板确认/截断/空态渲染回归 | ✅ 2026-08-01 |
| C-4 | 复盘工作流打磨 | Review Queue 逐项引导（未复盘案例队列化）、annotation 字段辅助文案 | 用户实际完成 ≥10 笔结构化复盘（用户行为，系统只降低摩擦） | ⬜ |

## 阶段 D · 可解释研究台（大部分完成，收尾三项）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| D-1 | 官方快照绑定 | 深链 snapshotKey + 冻结证据优先 + 回退标注 | 已验收 | ✅ 2026-07-31 |
| D-2 | 冻结 vs 当前差异 | 差异条 | 已验收 | ✅ 2026-07-31 |
| D-3 | 期权墙逐层合同 | level 级 expiry 分解 + 可观察报价/IV + 显式标缺（`option-wall/1.2`）；bid/ask/mark 需 adapter 扩展仍标缺 | 4 项 builder 回归（含无 dealer-sign 键递归断言）+ 真实页面逐层标注验证 | ✅ 2026-08-01 |
| D-4 | 摘要同证据束 | 官方绑定时摘要句中的增强数据数值（IV Rank / 模型区间 IV 输入）带「当前增强数据 as-of，非冻结榜单证据」内联标注；冻结 bundle 数值不加注；即时扫描不加注（`OpportunityDetailPage.tsx`） | 文案审计测试：增强数值必须带标注、冻结数值不得被标注、即时扫描无标注；审计按「指标名+数字」词面模式扫描结论段落（局限：不识别未命名裸数字） | ✅ 2026-08-01 |
| D-5 | 概率展示校准边界 | IV 终值区间明确标注模型假设；hit-rate 仅在 track 样本达门槛后出现 | 自动化断言已补（`OpportunityDetailPage.test.tsx`）：模型区间块必须携带「不是历史真实胜率/触及概率/方向预测」声明、「方向概率尚未校准」提示必须存在、全页（含各研究 tab 与期限切换）扫描禁止出现「上涨概率 / 胜率+数字」伪概率文案（校准提示中的否定引用为唯一豁免） | ✅ 2026-08-01 |

## 阶段 E · 结果学习闭环（框架已在，等数据+两项工程）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| E-1 | events 域修复 | 官方 Fed/BLS 年度日程 provider | 已验收（周一实战确认） | ✅ 2026-08-01 |
| E-2 | premarket 域修复 | Alpaca key 配置 | key 已配置+认证 200；周一盘前实战确认 | ✅ 2026-08-01（待实战） |
| E-3 | 完整研究 track 积累 | E-1/E-2 后 quality=ready 的官方发布开始入样 | 首个 qualified full-research 样本出现 | ⏳ 自动积累 |
| E-4 | 20D 成熟与展示 | 20D outcome 达门槛后学习面板解锁 | 门槛逻辑已有，等时间 | ⏳ |
| E-5 | 年度日程续期机制 | 2027 BLS 日程发布后添加数据文件（现数据文件 coverage_through 2026-12-04，7 天 agenda 窗口下 2026-11-27 后诚实降级） | 健康层预警已上线（2026-08-01）：`/api/v1/system/health-layers` 新增第 7 层 `economic_schedule_coverage`（余量 >30 天 ok / ≤30 天 degraded 提醒放入下一年度数据文件 / 超出覆盖 down），三态回归已测；剩余动作＝2027 官方日程发布后放入数据文件 | ⏳ 预警已上线，等 2027 日程发布 |

## 阶段 F · 生产稳态（剩余四项）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| F-1 | LaunchAgent 对齐 | sync/breakout 安装副本换新模板（含轮转 wrapper），或用户决定停用 breakout | 退出码/日志/OpenD 行为验证；**需用户确认 unload**（服务中断类操作） | ⛔ 等用户 |
| F-2 | artifact GC | 合同：`New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md`（核实结论：全部 artifact 表均在 deny-trigger 保护内，原“非保护范围”假设不成立；冻结删除谓词 + 单事务受控删除 + append-only 回执 + 备份前置） | 设计评审 → 实现 + 零误删测试（合同 T1-T10） | 🚧 F-2a 已实现 2026-08-01（`src/journal/ledger/artifact_gc.py` + `scripts/artifact_gc.py` CLI 默认只读 dry-run，T1-T10 全绿；正式库 apply 待用户确认 + 当日验证备份；F-2b 调度含 BLOCKED 决策，默认不做） |
| F-3 | 日志 retention 启用 | 用户设 `LOG_RETENTION_DAYS=30`（机制已在） | 首次清理日志输出核对 | ⛔ 等用户一行配置 |
| F-4 | clean-clone 演练 | 从 GitHub 干净克隆 → 按 README 启动成功 | 演练记录 + 修复发现的缺口 | ✅ 2026-08-01 已演练（clone bc74bc6：pip install（含 scipy）→ ci_gate.sh 2636 passed → npm ci+build → `--serve-only` 隔离空库 health/health-layers 双 200，全程零依赖原工作树/.env/数据库；README 小缺口已记录在 CHANGELOG 待后续修正） |

## 横切 · 工程质量

| # | 项 | 状态 |
|---|---|---|
| Q-1 | 后端 2,566 / 前端 551 / E2E 5 全绿 | ✅ 持续维持 |
| Q-2 | Moomoo env 测试隔离（conftest 系统化）：根 `conftest.py` autouse fixture 为非 network 测试强制关闭 5 个 live-integration 开关；network 标记与单测试 setenv 仍可 opt-in；`tests/test_env_isolation_conftest.py` 回归证明（HANDOFF §15 P1.9） | ✅ 2026-08-01 |
| Q-3 | Desktop Electron 链路验证 | ⛔ 需下载 Electron（等用户点头） |
| Q-4 | PR 合并 + GitHub CI 恢复 | ⏳ 分支已推，等用户建 PR |

## 执行顺序（连续模式）

1. 收尾 B-2 + D-3（进行中）→ 验收 → commit+push；
2. C-1 → C-2 → C-3（Playbook 主线，每切片全量验证）；
3. D-4、F-2、Q-2、E-5（中型项穿插）；
4. 周一盘前：实战验证 E-1/E-2 与官方发布 quality=ready；
5. 用户依赖项（B-4/F-1/F-3/Q-3/Q-4）到位即插入。

## 阶段 G · 专业化打磨（2026-08-01 用户反馈驱动）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| G-1 | 首页与详情页加宽 | max-w 1280→1720px | 构建+真实页面 | ✅ 2026-08-01 |
| G-2 | 盘中跟踪面板 | 冻结盘前计划 + 实时跟踪列（现价/VWAP/量能节奏/距确认与失效位的 ATR 标准化距离），有界刷新，全程 as-of | 后端+前端测试+盘中真实验证 | ⬜ 开工中 |
| G-3 | Journal 信息架构重构 | 仓位复盘页收敛为纯复盘工作区（待办条+列表+模式观察+Playbook），快照/构建/激活管理移入数据管理区 | 全部既有功能保留+测试更新+真实页面 | ⬜ 开工中 |
| G-4 | 指标专业化 | ATR 标准化、VWAP、相对强度 vs SPY（随 G-2 首批落地，RS 后续） | 指标定义文档化+确定性测试 | ⬜ 随 G-2 |
