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
| G-2 | 盘中跟踪面板 | 冻结盘前计划 + 实时跟踪列（现价/VWAP/量能节奏/距确认与失效位的 ATR 标准化距离），有界刷新（30s TTL + single-flight + 仅可见页面盘前/盘中 60s 轮询），全程 as-of：`POST /opportunities/intraday-tracking` + `IntradayTrackingPanel` | 后端+前端测试已绿；盘中真实验证待服务重启后进行 | 🟡 代码落地 2026-08-01，待盘中实测 |
| G-3 | Journal 信息架构重构 | 仓位复盘页收敛为纯复盘工作区（「复盘工作台」头部条 = 默认构建标识 + Review Queue + 「继续复盘下一笔」，加列表+模式观察+Playbook）；快照/canonical 构建/激活管理迁入更名「数据与构建」tab（每日刷新 / 历史导入 / 当前持仓快照与未来构建 / 构建与默认视图管理），`?tab=import` 深链不变 | 全部既有功能保留+测试更新+真实页面 | ✅ 2026-08-01（vitest + Playwright smoke 全绿；CTA 优先级：进行中 → top_loss 未开始 → 最近未开始） |
| G-4 | 指标专业化 | ATR 标准化（Wilder 14，日线）、VWAP（session turnover/volume 近似）、量能节奏（vs 20 日全日中位）首批随 G-2 落地于 `src/opportunities/intraday.py`；相对强度 vs SPY 后续 | 指标定义已文档化（phase1/06 §2.7）+确定性测试（`test_intraday.py`） | 🟡 首批 2026-08-01，RS 待交付 |
| G-5 | 复盘流 v2 | 单笔复盘页（`/journal/review/:id`）队列化重构：sticky 快捷操作条（回合标识 + Net + 「跳过，下一笔 →」/「保存草稿并下一笔」/「完成复盘并下一笔」），「下一笔」与工作台 CTA 共用唯一实现 `review/nextReviewEpisode.ts`（进行中 → top_loss 未开始 → 最近未开始，排除当前回合，队列清空如实提示「全部回合已完成复盘」+返回列表）；≥1280px 双列（左 = K 线+成交时间线，右 = sticky 工作单；窄屏工作单先行）；快速复盘模式（错误类型/交易风格预设 chip 一键写入既有 tags/error_types + 默认仅进场逻辑回忆与复盘反思两栏，可展开完整六项）；证据窗口/窗口末投影与技术来源折叠入「证据明细」；同订单分批成交订单级合并展示（现金流加权均价 BigInt 精确、费用不完整显式标注、每单一 marker、跨 bar 分散例外保留逐笔），保存校验与 annotation 语义不变 | vitest 全量（含 `nextReviewEpisode` / `evidenceConsolidation` 单测与页面 31 测试）+ lint + build + Playwright smoke 全绿（详见 phase1/05 §1.1） | ✅ 2026-08-01 |
| G-6 | 日内/周内看板分离 + 日内工作台 | 新路由 `/intraday`（导航首位「日内」）：市场脉搏（SPY/QQQ/VIX，`GET /opportunities/intraday-pulse`，VIX 隔离请求缺失显式标缺）+ 今日计划（复用 `IntradayTrackingPanel` 对照冻结盘前 Top 5）+ 日内扫描表（`POST /opportunities/intraday-top`：G-2 会话快照/ATR14/量能中位复用 + 逐标的有界 Moomoo 异动聚合，v1 启发式证据计数排名 `intraday_session_evidence_v1`，60s TTL + single-flight，`statistics_track=none_intraday_v1_unscored` 不冻结不入统计，休市显式标注最近一个交易时段）+ 跨标的期权异动 feed（≤20 条，分类不证明开平仓）；`/regime` 周内榜副标题如实标注「基于上一完整交易日日线结构 · 数日至数周研究周期」并加日内入口 | 后端 `test_intraday_top.py`（27）+ `test_intraday_top_endpoint.py`（12）+ ci_gate 全绿；前端 lint + vitest 全量 + build + Playwright smoke（`/intraday` 诚实空态断言）全绿；周末休市 TestClient 连本机 OpenD 真实验收（closed + 最近一个交易时段标注、gap 分母切换为快照前收、VIX 逐代码显式 unavailable、AAPL 异动多空平票如实记 mixed） | ✅ 2026-08-02 |
| G-7 | 波段爆发（momentum burst）成为日内主排序信号 | `src/opportunities/intraday_bursts.py` 纯函数：5m K 线滚动 15 分钟窗口 `burst_score = 推力/波幅中位 × 量比`，当日不足 6 根回退上一时段中位；`intraday-top` 升 `intraday_session_evidence_v2`——盘中 `ranking_method=burst_score_first_then_evidence_count`（休市退回证据计数但附最近一个交易时段波段），候选新增 `session_bursts`（当前窗口 + ≤4 个独立波段）与「波段爆发」证据项（supports ≥6.0）；执行器逐标的有界 5m 读取（当前+上一时段、线程池、60s TTL、失败显式 unavailable 不阻塞聚合证据）；前端扫描表新增首列「当前爆发」（分数+方向+15 分钟推力%）与「今日波段」（如 2 波：09:40↓ · 15:15↑）。阈值按 2026-07-31 用户标注行情校准（MU 09:45 跳水 35.5 / AMZN 09:30 开盘波 18.7 / NVDA 09:40 波 9.3 与 15:15 波 8.6 全部 ≥ LEG_MIN_SCORE 8.0；GOOGL v1 误排第一样本最佳窗口仅 15.2 < MU 35.5），真实 K 线固化为永久校准回归 fixture | 后端 `test_intraday_bursts.py`（22，含校准回归）+ `test_intraday_top.py`（34）+ `test_intraday_top_endpoint.py`（14）+ ci_gate 全绿；前端 lint + vitest 全量 + build + Playwright smoke 全绿；TestClient 默认 universe refresh 真实验收（最近时段 NVDA/MU 波段与排序） | ✅ 2026-08-02 |
| G-8 | 临期合约面板（0–3 DTE 合约选择支持） | 补「选中标的 → 选中合约」缺口，显式非推荐引擎：`POST /opportunities/near-expiry-contracts`（单 underlying，`max_dte` 默认 3 上限 7 含 0DTE；1 次链日期窗口 + 同批 `get_market_snapshot` 直读期权 `bid_price/ask_price`，独占 wall lane、30s TTL + single-flight）；纯函数 `src/opportunities/near_expiry_contracts.py`（±5% ∪ 上下各 8 档近价窗口、`spread%=(ask−bid)/mid` 缺 bid/ask 显式 null + reason 绝不 0 回填、逐到期隔离、ATM 位置标记）；前端 `NearExpiryContractPanel`（按到期分组、ATM 高亮、点差 >15% 标「流动性差」v1 启发式、缺失「标缺」、页头「合约选择参考 · 不构成推荐 · 以券商实时盘口为准」+ limitations），`/intraday` 扫描表行尾「临期合约」按钮内联展开（行点击导航不变、单行展开），详情页期权墙 tab「查看临期合约」按需展开 | 后端 `test_near_expiry_contracts.py`（18：窗口/点差 null-not-zero/逐到期隔离）+ `test_near_expiry_contracts_endpoint.py`（17：TTL/single-flight/422/诚实空态）+ ci_gate 全绿；前端 lint + vitest 全量 + build + Playwright smoke 全绿；周日休市 TestClient 连本机 OpenD 真实验收：MU `max_dte=3` 返回 08-03/08-05 两个到期 64/64 张观测报价、NVDA `max_dte=7` 三个到期 96/96、bid/ask 确认来自批量 `get_market_snapshot`、spot/报价 as-of 如实标上一时段（周五 15:59/20:01 ET）、OI as-of=2026-07-31、0 失败批次 | ✅ 2026-08-02 |
| G-9 | 日内扫描噪音过滤 v3（用户纪律上下文信号） | 把用户自身纪律（Playbook 候选 R1/R3，1,653 笔已平仓交易统计核验）编码为四类诚实上下文标注，`intraday-top` 升 `intraday_session_evidence_v3`，设计规则「系统标注，用户过滤」（绝不自动过滤行/隐藏候选/参与排序）：① 时段上下文 `session_phase`（ET 时钟九段，pulse + top 双响应携带；硬编码 v1 纪律提示如 noise=「午间震荡 · 你的历史净亏损时段 · 默认观望」、power_hour=「尾盘趋势 · 你的历史最高单笔均值时段」，脉搏条首醒目展示）；② 财报临近 `earnings_proximity`（Finnhub 一次区间调用当日→+5 天覆盖全 universe，逐 ET 日缓存成功 1h/失败 10min；≤3 天醒目「财报 N 天内 · 期权贵」，日历不可得 `within_blackout=null` 显式标缺绝不冒充安全）；③ 大盘对齐 `market_alignment`（SPY 并入同批快照零新增请求，SPY 会话 VWAP 位置 vs 候选当前爆发方向 → 顺势/逆势/标缺）；④ 速度分级 `session_bursts.speed`（相邻两个 15 分钟窗口爆发分之差 → 加速/减速/持平/标缺，5m 诚实近似非 1m/2m；footer「减速=你的离场信号（R1）」）。前端：扫描表新增 速度/大盘/财报 三列（财报列可按天数排序、标缺恒排最后）+ footer 公式扩展；脉搏条首时段标签 + SPY/QQQ VWAP 位置 | 后端 `test_intraday.py`（+6 时段边界）+ `test_intraday_bursts.py`（+6 速度）+ `test_intraday_top.py`（49，含财报 0/3/4 天边界与 unavailable 诚实、对齐矩阵）+ `test_intraday_top_endpoint.py`（17，含日历单次区间调用跨 refresh/universe 缓存、SPY 同批断言）+ ci_gate 全绿（2826 passed）；前端 lint（0 error）+ vitest 全量 649 + build + Playwright smoke（时段标签断言）全绿；周日休市 TestClient 连本机 OpenD + 真实 Finnhub 验收：phase=closed「休市 · 复盘时段」、SPY 747.03 上一时段 VWAP 上方（as-of 周五 20:01 ET）、MU 末窗减速/NVDA·AMZN 加速（2026-07-31 时段）、MU dir=down → 逆势、四标的 5 天窗口内无财报＝诚实 `within_blackout=false`、VIX 照旧显式标缺 | ✅ 2026-08-02 |
| G-10 | 形态相似度 styleMatch v1（你的 setup 形状标注） | 回答「我的交易能根据你的推荐来操作吗」的诚实版本：不做推荐，标注当前时段几何形状与用户自己三个 Playbook setup 的相似度。纯函数 `src/opportunities/intraday_setups.py`，`intraday-top` 升 `intraday_session_evidence_v4`，候选新增 `setup_match`（每 setup 恰好一态 matched/partial/not_matched/unavailable + 中文理由 + 证据行，可多 setup 同时相似，`matched_setups`/`partial_setups` 顶层摘要）：S1 十五分钟低点抬高突破（5m 按 09:30 ET 栅格聚合 15m、尾部连续抬高 swing low ≥2、突破结构高点；<3 根 15m 显式 unavailable）；S2 跳空高开托举（向上跳空达缺口证据同阈值 0.75×ATR/1.5%——常量单一真源迁至 intraday_setups + 缺口未回补 + 现价 ≥ 会话 VWAP）；S3 高开遇阻回落（跳空 + 现价跌破开盘/VWAP + SPY 处于会话 VWAP 下方；SPY 强/标缺最多 partial「形态似 S3 但大盘未走弱」）。输入全复用（波段爆发通道 5m K 线随 60s 缓存同存原始 K 线、G-2 快照派生、v3 SPY 上下文，零新增请求）；Playbook 只读对应（journal_v2 候选表 S1/S2/S3 标题前缀，5 分钟缓存，读取失败仅缺标注）。前端「形态」列：matched 实底 / partial 描边加「· 似」徽标（共享 Tooltip + aria-label 展示理由/证据行/「对应 Playbook: S1（候选）」）、无相似「—」、输入不足「标缺」；footer「形态相似度为 v1 几何检测（5m近似），不含你的进场确认帧（2m/1m 回踩8/13EMA），不是信号」。设计规则同 v3：系统标注，用户过滤——不参与排序、不隐藏行、不改研究状态 | 后端 `test_intraday_setups.py`（25：三 setup 四态矩阵、阈值同源断言、多相似、休市 as-of、Playbook 透传、15m 栅格聚合）+ `test_intraday_top.py`（53，+4 接线/计数不变/refs 注入/休市 scope）+ `test_intraday_top_endpoint.py`（18，v4 合同 + setup 状态矩阵 + Playbook 只读 + 5m 失败仅 S1 标缺）+ ci_gate 全绿（2856 passed）；前端 lint（0 error）+ vitest 全量 651 + build + tsc + Playwright smoke 全绿；周日休市 TestClient 连本机 OpenD 真实验收（2026-07-31 时段，as-of 如实 latest_prior_session）：AMZN S1+S2 双 matched（低点序列 11:30 266.32→15:15 271.03、突破 16:00 收 272.46>271.88；+12.53% 缺口未回补且现价≥VWAP）、GOOGL S1+S2 matched（突破 15:45）、MU 跳水日 S3 partial——形态全中（+5.14% 高开、现价 823.03<开盘 919.65<VWAP）但 SPY 收盘时在会话 VWAP 上方 → 如实「形态似 S3 但大盘未走弱」、NVDA S2 partial（最低 194.95 ≤ 前收 195.04，9 美分回补如实不算托住）、四标的均带真实 Playbook 候选标注 | ✅ 2026-08-02 |
| G-8b | 临期合约面板财报警示联动 | 用户「财报临近不交易（期权贵）」规则在合约检视时刻可见：`near-expiry-contracts` 响应新增 additive `earnings_proximity`（与扫描表候选同 shape/同 `compute_earnings_proximity`/同一份逐 ET 日 Finnhub 日历缓存，零新增抓取路径、零新常量）；与面板自身 state 正交（Moomoo 禁用/空态照常返回，日历不可得 `within_blackout=null` 显式标缺绝不冒充安全）。前端 `NearExpiryContractPanel` 页头三态：回避窗内醒目徽标「财报 N 天内 · 期权贵 · 你的回避规则」（0 天「今日财报…」，样式 token 与扫描表财报列一致）/ ready 窗外零标注 / unavailable 小字「财报日历标缺 · 未知≠安全」；TS 侧 optional 兼容 30s sessionStorage 旧载荷 | 后端 `test_near_expiry_contracts_endpoint.py` +5（blackout 边界、窗外不标、unavailable 诚实、Moomoo 禁用仍携带、与扫描车道共享单次日历区间调用）+ ci_gate 全绿（2861 passed）；前端 +5（三渲染态 + 今日财报文案 + 旧载荷标缺）+ lint/vitest 656/build/tsc 全绿；周日 TestClient 真实 Finnhub 验收：SNDK `2026-08-05`、3 天、`within_blackout=true`（面板自身 empty，字段正交性实证）、AAPL 窗口内无财报零标注、两标的共享一次日历调用 | ✅ 2026-08-02 |
| G-11 | Watchlist 两层日内扫描（用户全自选 69 檔） | 用户提供完整 TradingView 自选（含 2026 新上市 SKHY/DRAM/SPCX/CBRS）后落地专业扫描器双层结构：`INTRADAY_WATCHLIST` 配置后 `intraday-top` 走两层——全清单单次批量快照宽层（`get_market_snapshot` 单请求 ≤400 檔，服务端 200 上限超出显式 `watchlist_truncated`）→ 异动闸门（|涨跌%| 主序→成交额次序 v1 启发式）晋升前 K 檔（`INTRADAY_DEEP_LANE_MAX` 默认 12，1..20 clamp）→ 既有 v4 深度管线只跑晋升标的；当日冻结盘前计划标的恒占深度位不占 K 名额。配额实证：5m 路径为 `request_history_kline`（30 天滚动去重标的额度，最低档 100 > 清单 69），另设每 ET 日新晋升去重上限 30（触顶显式 `day_promotion_cap_reached`）。诚实边界：additive `universe_scan` 块（mode/总数/深度数/闸门口径/仅快照数/未解析点名）、深度候选带 `deep_lane_reason`（计划钉选/异动 #n）、仅快照标的以次级列表可见（「仅快照 · 未做深度分析」）绝不静默丢弃、宽层行禁带爆发/形态字段；未配置或客户端显式 symbols → 与现状逐字节一致（回归测试锁定）。`.env.example` 中文块 + 69 檔真实清单示例 | 新 `test_intraday_top_two_tier.py`（8：unset 回归锁定/显式 symbols 旁路/单快照+闸门排序+宽层禁字段+财报单区间调用/计划钉选去重/日护栏诚实/Moomoo 禁用宽层保留/截断显式/K clamp）+ ci_gate 全绿（2869 passed）；前端 +4（两层页头/仅快照列表/单层回归/日护栏警示）+ lint/vitest 660/build/tsc 全绿；周日休市真实验收（本机 OpenD）：69/69 快照全解析（`snapshot_unresolved_symbols=[]`，SKHY −3.54%/DRAM −3.76%/SPCX −3.41% 现身仅快照区前列）、深度 lane=周五 top-12 movers（RBLX −26.9% #1、AMZN +15.3% #2、COIN −10.6% #3…）、正式服务 .env 启用后 POST 实测 mode=watchlist_two_tier deep 12/12 仅快照 57 | ✅ 2026-08-02 |
| G-12 | 盘前异动闸门（真·盘前数据） | 实战首日盘前实测发现：Moomoo 常规快照字段盘前仍指向上一常规时段（prev_close 未滚动、last=上收），两层闸门在盘前复现上一时段异动。修复：快照解析器 additive pre_price/pre_change_rate/pre_volume/pre_turnover（缺列 None 绝不 0 回填）；ET 盘前时段闸门与仅快照区改按 |pre_change_rate|→pre_turnover 排序（`premarket_pre_price_change_then_pre_turnover_v1`），缺盘前字段标的不可晋升恒排最后；整批无盘前字段显式回退+`gate_warnings` 警示绝不静默；常规/休市路径逐字节回归锁定。前端盘前口径 caption/警示 chip/「盘前 ±x%」行展示 | 定向+全量 ci_gate 2873 passed；两层测试 8→11、快照解析 +1（44 passed 定向）；前端 15/15+tsc+lint；开盘前 17 分钟真实部署验收：gate 切 premarket 口径、深度榜=真实盘前异动（CRCL#1/TEAM#2/SKHY#3、SNDK −4% 带财报徽标）、开盘后自动切回常规口径实测确认 | ✅ 2026-08-03 |
| G-13 | 波段分级记录（signal v5） | 用户实战中指认「NVDA 这一波应该有记录」（+2.5% 稳步推升，峰值窗口分 5.03 < 强波段阈值 8.0 → 全程漏记）。修复：波段两级记录——强 ≥8（07-31 暴动校准不变）/ 中 ≥2.5（08-03 NVDA 上午波实时校准：收录 09:55→10:25 全程、不触及同日无波时段 <1.1）；legs 携带 grade additive 字段，强波段贪心优先占 ≤4 名额；supports/当前爆发口径不变；限制文案同步两级校准出处 | test_intraday_bursts 分级断言重写（strong/medium/below-medium 三态+cap 强优先）+110 定向 passed+全量 ci_gate 2873 passed；部署后 NVDA 实测：波段「10:00→10:15 分 5.06 中级 up」如实入账 | ✅ 2026-08-03 |
| G-14 | 扫描表两级布局（默认 9 列） | 用户实战反馈「太乱不清晰」。默认网格收敛为交易关键 9 列：排名/标的（次行深度位+财报徽标）/涨跌%/当前爆发/今日波段（grade chips「10:00↑ 中」实底强·描边中）/速度/形态/波段vs大盘（改名+Tooltip 澄清波段方向 vs SPY 非个股涨跌）/详情；量比/缺口/波幅/VWAP/期权异动/研究状态收进展开行「研究读数」grid（临期合约面板上方）；仅快照列表默认折叠 top5+展开全部；footer 一行口径+「完整口径」toggle（原文逐字保留并同步 v5 分级表述）。数据零删除：全部读数一击可达 | 前端 vitest 20/20（默认列/分级 chips/折叠展开/研究读数/两级 footer）+tsc+lint 0 error；构建部署实测 | ✅ 2026-08-03 |
| G-15 | 扫描器 v2：钉选+动量闸门+今日账本+命名改清 | 首战次日用户三连反馈驱动：「NVDA 没扫到/ORCL 扫到过又不见/两个盘中板块分不清」。① `INTRADAY_PINNED_TICKERS`（默认 NVDA/TSLA/MU/AAPL）恒占深度位不占 K 名额、与计划钉选去重；② 闸门 v2 `momentum15m_then_day_change_v2`：宽层快照进程内喂养 12-18 分钟回看窗动量（零新增请求），常规时段深度名额 ceil(2K/3) 按 |mom15|（谁现在在动）+ 其余按 |当日涨跌|（谁今天最大）兜底——校准依据 2026-08-03 普涨日用户标注「真正能交易的只有 NVDA」而日涨幅闸门全天未晋升 NVDA；冷启动整批无动量显式回退 + warming-up 警示绝不静默，ET 日切清历史；③ 今日账本：当日进过深度层的标的轮出后保留在 `day_ledger`（最后一次深扫载荷 as-of 标注、波段分级 chips 保留、重启清空如实声明）；④ 前端命名「实时扫描 · 现在谁在动」/「今日计划跟踪 · 盘前冻结计划走到哪了」+ 页面重排主次 | ci_gate 全绿（2879 passed）×2（代理+编排方独立复跑）；backend +6（钉选去重/动量窗口数学/日切清空/双子配额+预热回退/账本累积）、两层套件 17/17；前端全量 vitest 674/76 files+tsc+lint 0 error；部署实测：NVDA/TSLA/AAPL user_pinned 深度位（MU 经计划位去重）、warming-up 警示如实、账本空+basis 标注 | ✅ 2026-08-04 |
| G-16 | 个人画像回灌（personal edge） | 用户请求「总结我的单子→我适合什么→反馈到盘面」。SQL 全量画像（build#3 1,653 笔）核心结论：<30m 持仓 −$100万（<10m 胜率 6.0%）vs 1-3h +$68.8万（57.1%，赔率 3.4:1）且逐小时交叉全部成立（唯 13 点例外）；0DTE +$13.1万 / 1-3DTE −$11.0万（自称主力区间实为唯一亏损带）/ 4-7DTE +$14.8万（43.8% 最佳）；提款机 TSLA/MSFT/HOOD/MU/META vs 漏斗 PLTR(21.7%)/AMD/QQQ/SMCI(9.1%)/AAPL、NVDA 191 笔仅 +$5,490；月度 4月+$10.2万→7月−$1.2万且费用 $2.5万→$7.7万（总费 $18万 vs 净利 $15万）；大亏后全面劣化。回灌实现：① `src/journal/personal_edge.py` 零写聚合（默认 build 解析复用 journal 现行路径、n≥5 门槛、DTE unknown 独立桶、ET≈UTC−4 标注、内生性 limitation 强制携带）+ `GET /v2/personal-edge`（600s 缓存）；② 扫描表第 10 列「你的战绩」（亏钱标的 n≥20 警示 tint、样本不足/标缺诚实）；③ 临期合约面板 DTE 战绩行（决策时刻显示 1-3DTE 是用户亏损带）；④ R4 候选 id7「持仓时间纪律」（数据描述非建议+内生性声明）。关键诚实点：现默认 build#1（1,437 笔至 7/20，无激活行）——强制解析 build#3 交叉核对与 SQL 完全一致，用户激活 build#3 后自动升全量 | ci_gate 全绿（2887 passed）×2；backend +8 测试；前端 vitest 触达 54/54+扩面 181/181+tsc+lint；部署实测端点 ready/build1/1437 笔、R4 id7 已建 | ✅ 2026-08-04 |
