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

## 阶段 C · 复盘 → Playbook（设计已冻结，实现未开始）

合同：`New-docs/phase1/13_PLAYBOOK_PROMOTION_CONTRACT.md`

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| C-1 | 模式观察聚合（零写） | `GET /journal/v2/review-insights`：按 tag/setup/方向/boundary policy 分桶；样本<10 笔或独立交易日<5 只显示计数不显示比率；条件性 P&L 单独分桶 | 门槛 fail-closed 测试 + 分桶不混轨测试 + 真实页面「模式观察」面板 | ⬜ 下一波 |
| C-2 | Playbook 表与晋升 | 2 张 append-only 表（candidate/rule 版本链）+ 晋升冻结证据快照（episode ids+build+revisions+聚合数字+as-of）+ CAS 晋升/退役端点 | 晋升快照可重放、deny triggers、不反写任何权重 | ⬜ |
| C-3 | 复盘页反向链接 | 单笔页显示命中的 candidate/rule | 零写、真实页面 | ⬜ |
| C-4 | 复盘工作流打磨 | Review Queue 逐项引导（未复盘案例队列化）、annotation 字段辅助文案 | 用户实际完成 ≥10 笔结构化复盘（用户行为，系统只降低摩擦） | ⬜ |

## 阶段 D · 可解释研究台（大部分完成，收尾三项）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| D-1 | 官方快照绑定 | 深链 snapshotKey + 冻结证据优先 + 回退标注 | 已验收 | ✅ 2026-07-31 |
| D-2 | 冻结 vs 当前差异 | 差异条 | 已验收 | ✅ 2026-07-31 |
| D-3 | 期权墙逐层合同 | level 级 expiry 分解 + 可观察报价/IV + 显式标缺（`option-wall/1.2`）；bid/ask/mark 需 adapter 扩展仍标缺 | 4 项 builder 回归（含无 dealer-sign 键递归断言）+ 真实页面逐层标注验证 | ✅ 2026-08-01 |
| D-4 | 摘要同证据束 | 详情页专业摘要只引用冻结 bundle 内数值，增强数据引用必须带 as-of 标注 | 文案审计测试（摘要不得出现无来源数值） | ⬜ |
| D-5 | 概率展示校准边界 | IV 终值区间明确标注模型假设；hit-rate 仅在 track 样本达门槛后出现 | 已有护栏，补自动化断言 | ⬜ |

## 阶段 E · 结果学习闭环（框架已在，等数据+两项工程）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| E-1 | events 域修复 | 官方 Fed/BLS 年度日程 provider | 已验收（周一实战确认） | ✅ 2026-08-01 |
| E-2 | premarket 域修复 | Alpaca key 配置 | key 已配置+认证 200；周一盘前实战确认 | ✅ 2026-08-01（待实战） |
| E-3 | 完整研究 track 积累 | E-1/E-2 后 quality=ready 的官方发布开始入样 | 首个 qualified full-research 样本出现 | ⏳ 自动积累 |
| E-4 | 20D 成熟与展示 | 20D outcome 达门槛后学习面板解锁 | 门槛逻辑已有，等时间 | ⏳ |
| E-5 | 年度日程续期机制 | 2027 BLS 日程发布后添加数据文件（现覆盖至 2026-11-27） | 提醒机制：HANDOFF ops + 健康层可加覆盖期预警 | ⬜ 小项 |

## 阶段 F · 生产稳态（剩余四项）

| # | 项 | 交付物 | 验收 | 状态 |
|---|---|---|---|---|
| F-1 | LaunchAgent 对齐 | sync/breakout 安装副本换新模板（含轮转 wrapper），或用户决定停用 breakout | 退出码/日志/OpenD 行为验证；**需用户确认 unload**（服务中断类操作） | ⛔ 等用户 |
| F-2 | artifact GC | 过期 refresh/snapshot artifact 的显式清理合同（短期表非 append-only 保护范围需先核实） | 设计评审 → 实现 + 零误删测试 | ⬜ |
| F-3 | 日志 retention 启用 | 用户设 `LOG_RETENTION_DAYS=30`（机制已在） | 首次清理日志输出核对 | ⛔ 等用户一行配置 |
| F-4 | clean-clone 演练 | 从 GitHub 干净克隆 → 按 README 启动成功 | 演练记录 + 修复发现的缺口 | ⬜ PR 合并后 |

## 横切 · 工程质量

| # | 项 | 状态 |
|---|---|---|
| Q-1 | 后端 2,566 / 前端 551 / E2E 5 全绿 | ✅ 持续维持 |
| Q-2 | Moomoo env 测试隔离（conftest 系统化） | ⬜ 胶囊待用户或纳入下波 |
| Q-3 | Desktop Electron 链路验证 | ⛔ 需下载 Electron（等用户点头） |
| Q-4 | PR 合并 + GitHub CI 恢复 | ⏳ 分支已推，等用户建 PR |

## 执行顺序（连续模式）

1. 收尾 B-2 + D-3（进行中）→ 验收 → commit+push；
2. C-1 → C-2 → C-3（Playbook 主线，每切片全量验证）；
3. D-4、F-2、Q-2、E-5（中型项穿插）；
4. 周一盘前：实战验证 E-1/E-2 与官方发布 quality=ready；
5. 用户依赖项（B-4/F-1/F-3/Q-3/Q-4）到位即插入。
