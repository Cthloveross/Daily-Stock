# 17. Pro Practice Blueprint：推送分级与深度复盘设计蓝图

> as-of: 2026-08-25 ｜ 输入: 五份研究简报（A 日内动量文献 / B 期权流文献 / C 机构扫描实践 / D 复盘方法论 / E 工具基准）＋ `New-docs/phase1/16_EDGE_FORENSICS_AND_REGIME_GATE.md`（取证真源）＋ 现有代码（`src/journal/`、`src/regime/`、`api/v1/`、`apps/dsa-web/`）
> 性质: 设计蓝图（docs only）。不改变任何现有行为；实现按 §三(f) 分期交付。
> 真源关系: 取证结论（edge 是 regime 条件化的、gate 只防不攻、R1–R6）以文档 16 为准；**推送分级设计与深度复盘界面设计以本文为准**。

---

## 0. 约束声明（读任何一节前先读这里）

**用户画像**：美股 0-7DTE 期权交易者；风格＝被动等待推送、机会不来就休息、日内为主＋小仓 4-7DTE 过夜。

**已证事实（来自本人 1,400+ 笔已平仓交易的取证，文档 15/16）。凡研究文献与下列事实冲突，以下列事实为准，冲突处本文逐条明示**：

| # | 已证事实 | 证据 |
|---|---|---|
| F1 | 进场择时无方向边际 | 两个时间框架、2 万+事件 |
| F2 | 「好日子」不存在 | 132 个预测因子全灭，样本外符号一致率 45% |
| F3 | 盈亏尾部结构 | top 5 笔 = 80% 毛利（v1 口径 top-10 = 106%） |
| F4 | 费用 ≈ 1.25% 权利金/笔 | 券商账单 |
| F5 | 4-7DTE 隔夜持有是唯一尾部稳健的正边际桶 | 2-7DTE 连续 5 个月为正（含灾难性的 8 月），文档 16 §1.7 |
| F6 | 持有时长分层是构造性循环论证 | 本人取证已识别（「持有久因为在走」） |

**硬红线（任何建议不得逾越）**：系统只读、永不下单；不展示概率、不做预测；缺失数据一律「标缺」、绝不冒充。本文中所有「反事实」「统计门槛」均为对本人历史的描述统计与规则记账，非市场预测。

---

## 一、贴合度评审：现有系统 vs「等待-提醒-行动」风格

判定分四档：**核心**（真正服务该风格）／**需改造**（部件对、语义错位）／**装饰**（低价值但无害）／**冲突**（与已证事实或强文献相悖）。

### 1.1 盘中/推送侧

| 部件 | 判定 | 依据（简报条目＋证据强度） |
|---|---|---|
| 两级扫描（69 快照 / 8 深度） | **核心** | 与 SMB「筛选先于交易」结构同构（C§1，中）。但 Barber-Odean 2008（C§2，**强**）警告：扫描器本质是注意力制造机，注意力驱动进场平均负边际——扫描输出必须经过短名单与分级闸门才许触达用户，不得直推。 |
| 分级波动台账（strong≥8 / medium≥2.5） | **需改造** | 作为「今天存在机会」的存在性证据合法（A§2/§3 ORB/RVOL 均仅够格做存在性过滤器，中）；作为进场触发与 F1 冲突（override）。正确角色：喂给短名单与 4-7DTE 隔夜候选池、以及 regime 存在性判断，剥离一切「现在进场」语义。 |
| 30 分钟位移（$ + ATR） | **需改造** | ATR 归一化作为度量工具成立（Wilder 1978，C§2 中）；位移本身同上只做存在性/确认窗口输入（见 §2.2 双窗确认的长窗），不做进场信号。 |
| 速度分级 | **装饰→需改造** | 无文献支持速度的方向含义；个股小时内默认均值回复（Heston-Korajczyk-Sadka 2010，**强**）。保留为「紧急度」元数据（影响推送合并/去重），删除方向暗示；V2-B 已明文「隔夜车道不适用速度衰竭离场」。 |
| Setup 匹配 S1/S2/S3 | **冲突** | 进场 setup 分类学在一个已证明进场无边际的账户上（F1）没有学习信号：Kahneman-Klein 2009（D§1.1，**强**）——低效度维度上复盘只会产生迷信。处置：降级为归档元数据；单独的 setup 匹配不得构成推送理由；Playbook 归档键改为「推送类型 × regime × 持有结构」（D§2）。 |
| 期权墙列（C/P wall、γ、OI 加权中心） | **需改造** | 必须拆两种语义（B§4）：到期日尾盘「墙位吸附」有证据（Ni-Pearson-Poteshman 2005，**强**，~16.5bp 量级，别夸大）；非到期日「价格走向墙位」是**传闻**，只许展示为 gamma regime（正 γ 压波动/负 γ 放大动量与尾部，NPPW 2021 强 + Barbon-Buraschi 中），并注明 GEX 符号基于未经验证的持仓假设（B§6）。现有 info-hint 披露机制扩展即可。 |
| 盘前期权异常面板（skew + $1M 大单） | **需改造** | 信号重排（B§7）：① C−P 隐波价差（Cremers-Weinbaum 2010，**强**，唯一可用免费链快照完整复现、时间尺度精确匹配 4-7DTE 隔夜）② O/S 比率（Johnson-So 2012，**强**——注意高 O/S 历史上**偏空**，文案别写反）③ 到期日墙距 ④ 大单/sweep 降权为「有人急」（期权 sweep 无直接学术检验，**弱/传闻**；Muravyev 2016 强：先怀疑做市商库存流）。总量 P/C 只作情绪展示（Pan-Poteshman 强信号用的是拿不到的 open-buy 数据）。 |
| 盘前涨跌显示 | **核心** | 低成本情境信息，无信号语义，无冲突。 |
| 日型（0DTE 可得性 → 车道闸门） | **核心** | 这是结构事实不是预测（与 F2 无冲突）；V2-E 的机器化，正确。 |
| 车道检查单（V2/V3 规则） | **核心** | 全系统与研究对齐度最高的部件之一：Bellafiore playbook 化（C§1，中）＋ TradeZella Rule Adherence 同构（E§1.3，强-官方文档）＋ 本人数据推导。 |
| 盘中计划面板（自选标的、$2-8 合约甜区、股数/费用计算） | **核心** | 摩擦感知直接服务 F4（费 1.25% + 散户偏好合约平均点差 12.6%，Bryzgalova et al. 2023，**强**）。约束：所计划标的应能映射到车道/Playbook 条目，映射不上的在面板上标注。 |
| Telegram 推送 ×6 | **冲突→需改造** | 六条通道无分级＝告警疲劳的教科书前置条件：医疗告警 85-99% 无需干预→脱敏漏真（Joint Commission 2013，**强**）；CDS 弹窗 override 率 49-96%，重复暴露有剂量效应（Ancker 2017 / JAMIA 2019，**强**）。整改方案见 §二（本文核心交付之一）。 |
| 通道 watchdog | **核心** | 系统健康通知与机会通知分离，保留；归入 §2.2 的「系统通道」，不占机会预算。 |
| 热缓存（ms 级页面） | **核心** | 基础设施。 |

### 1.2 复盘侧（现状偏弱，是本文重心）

| 部件 | 判定 | 依据 |
|---|---|---|
| Episode 台账（FIFO，closed 1,600+，以激活 build 为准） | **核心** | 一切复盘的脊柱；自动导入是复盘工具的生死线（E§4，中——Edgewonk 手工录入被弃用是行业头号死因）。 |
| 单笔复盘页（K 线 + 成交 + AI 分析 + 手工标签） | **需改造** | 当前布局先见结果后写评价＝制度化 outcome bias（Baron-Hershey 1988，**强**，多次注册复现）与后见之明偏差（Fischhoff 1975，**强**）。必须改为「决策时快照 → 折叠揭示结果 → 双轨标签」的强制顺序（§三(b)）。AI 分析文本须拆过程/结果两段且在揭示后出现。 |
| 形态观察（标签聚合） | **需改造** | 标签聚合本身合法；但聚合必须过程/结果分离（D§6），且受 R5 统计门槛（n≥100、bootstrap CI 下界>0）约束，否则是在噪声里找形态（F1/F2）。 |
| 个人边际（分 ticker / hold / DTE / 月度） | **部分冲突** | DTE 分桶＝**核心**（F5 所在维度；TradesViz 把 DTE 做一等公民是对的，E§1.5 强）。**hold-time 分桶与 F6 循环论证正面冲突（override）**：`personal_edge.py::HOLD_TIME_BUCKETS` 的展示必须挂永久免责（「持有时长是结果不是决策变量，本表不用于推导持有规则」）或从主视图降级。分 ticker 受 R5 小样本门槛约束。月度作描述统计合法。 |
| 规模-频率纪律面板（每美元边际、费用门槛、剔尾） | **核心** | 与 R2/R3 精确对应；剔尾后每美元边际正是「利润集中度」自研指标族（E§5「无人做全，自研补位」）。 |
| V3 规则遵守前向表 | **核心（全系统最对）** | 前向验证（采纳日 2026-08-05 之后才算数）＝预注册；决策前过程问责是复盘文献里最硬的一条（Lerner-Tetlock 1999，**强**）；商业对应物是 TradeZella Rule Adherence（E 值得抄第 2 条）。它是 §三 一切「过程指标」的判定内核。 |
| Playbook（候选/晋升） | **需改造** | 保留晋升合同（文档 13）；归档键从 setup 改为「推送类型 × regime × 持有结构」（D§2——被数据证明有边际的维度）；月度复盘固定动作「把 ≥1 笔最佳 4-7DTE 隔夜单写成完整条目」（Bellafiore PlayBook + Steenbarger「伟大的交易员在赚钱时复盘」）。 |

### 1.3 研究结论与已证事实的冲突裁定（逐条明示，事实优先）

1. **Zarattini/Aziz 系 ORB 盈利主张**（A§2，弱-中，未同行评审，与 MNQ 证伪研究矛盾未解决）vs F1 → **override**：ORB/波动台账仅作存在性过滤器，永不作进场推送理由。
2. **Gao et al. 2018 日内动量**（强）：效应真实但是指数层面 bp 量级、regime 依赖、成本上不可收割（Baltussen 2021 自己承认）。vs F1/F4 → **降级使用**：仅作为「已持仓位的尾盘持有/平仓辅助」（§2.3-7），永不作进场信号。
3. **Sweeney MAE 止损优化**（D§3，概念中/跨期稳定性弱）vs F3+F6 → **override**：对 top-5=80% 毛利的账户，MAE 分位止损最大概率截掉的恰是右尾。MAE/MFE 一律只读诊断，禁接任何参数（§三(b)）。
4. **Tharp SQN / TradeZella Zella Score 类合成分**（E§3，中/传闻）vs F3 → **禁用**：std(R) 在分母，+30R 的单会拉低分数；胜率与一致性权重对尾部型选手是系统性负激励。禁用清单进代码常量（§三(c)）。
5. **time-of-day 进场优化报表**（全行业标配）vs F1/F2 → **禁用**为优化器；时段维度仅保留费用/流动性监控与 V2-B 弱时段记账。
6. **「聪明钱跟单」UOA 叙事**（B§3，期权 sweep 无直接学术检验）vs F1 → **文案禁用**「聪明钱买入」；替换语义见 §2.3-5。
7. **简报 D §5 假设「推送日志（已有）」与代码现状不符**：`src/services/intraday_opportunity_alerter.py` 仅内存去重状态，**无持久化推送台账** → 更正为「必须新建」（§三(e)，Phase B）。
8. 研究**确认**（非冲突，列出以固化信心）：0DTE 散户负和（Beckmeyer 2023，中-强）；费用数学（Bryzgalova 2023，强）；技能尾部分布（Barber et al. 2014，强）；4-7DTE 隔夜边际的收益时钟（Lou-Polk-Skouras 2019 + Hendershott 2020，强）——附三条操作性警告：隔夜持仓须保持有意义净 delta（Muravyev-Ni 2020：delta 中性隔夜是纯 vol 逆风）；尽量避免跨周末持长权利金（Jones-Shemesh 2018，强）；隔夜溢价会退潮（NY Fed 2026），「隔夜边际是否仍在」应做滚动监控量而非信仰。

---

## 二、盘中/推送改进清单

### 2.1 分级设计要回答的唯一问题：什么配打断休息？

设计原理（C§3/§6，医疗+SRE 证据移植）：每条打断型推送必须同时满足 **urgent / important / actionable / real** 四问，其中 **actionable 的定义收窄为「能映射到本人车道规则的 4-7DTE 隔夜结构或在册 Playbook 条目」**；漏掉机会的代价（下一个尾部事件还会来，F3）远小于脱敏的代价（真 P0 被无视，剂量效应有强证据）→ **precision 压倒 recall，不确定时降级**（与 PagerDuty「升级处理」相反，因为本用户的边际不依赖抢时效）。

### 2.2 四级推送协议

| 级别 | 语义 | 触发条件（全部满足） | 通道 | 硬预算 |
|---|---|---|---|---|
| **P0 行动级** | 「值得从休息中站起来看一眼」 | ① 标的在当日盘前短名单（催化剂在册）② 映射到活跃车道/Playbook 条目（默认 V2-B 4-7DTE 隔夜结构）③ **双窗确认**：短窗异常（5m 波动台账 strong 级）**且**长窗结构确认（30-60min 位移，SRE multiwindow multi-burn-rate 移植）④ regime gate 对该车道非红（gate 只许关、不许作为进场理由——文档 16 §5.3 裁定）⑤ 摩擦核算通过：报价点差% + 2×1.25% 费用 < 位移/ATR 给出的波动预算 ⑥ 预算未耗尽 ⑦ ET 时段合规（V2-B 弱时段 11/13 点排除；周四/五进场自动附「跨周末」红字，Jones-Shemesh） | Telegram 正常通知（响铃） | **≤1 条/日、≤3 条/周**；P0 的 passed 率滚动 20 条 >70% → 阈值自动收紧（机械规则，非预测） |
| **P1 预备级** | 「等下可能需要你」 | (a) P0 七条件差且仅差一条（缺哪条写明）；或 (b) **尾盘持仓助手**：持有隔夜/日内仓位 + ET 15:30-16:00 + 高波动/高\|γ\|日（指数级条件）→ 推「ROD 同向可考虑持有到收盘 / 逆向提示查看」（Gao/Baltussen/Rosa，仅对已持仓、永不建仓）；或 (c) R6 保险丝触线通告（§2.3-9） | Telegram 静默消息 | ≤3 条/日 |
| **P2 观察级** | 「回到屏幕前才需要知道」 | 全部分级波动、UOA/大单打印、墙位事件、halt、速度分级——一切无 Playbook 映射的异常 | 仅 Web 面板（按短名单聚合去重） | 不限 |
| **P3 日志级** | 复盘原料 | 其余一切 + 所有被抑制/降级的候选 | 仅入库（推送台账） | — |
| 系统通道 | watchdog / 数据源健康 | 现状保留 | Telegram 系统通知 | 不占机会预算 |

去重与合并：同 ticker 同方向 60 分钟内不重复升级；同催化剂事件族合并为线程式更新；级别不确定一律降级。

**闭环（本设计的生命线）**：每条 P0/P1 在日终复盘（§三(a) S1 步）强制标注 `acted / passed(+一词理由) / missed`；面板常驻「疲劳仪表」展示各级 acted 率。这是把 CDS 文献 49-96% override 率的教训做成自动护栏，也是 Perold 机会成本分量（§三(c)）的数据源。

### 2.3 改进项清单（按 研究支持度 × 对该用户价值 排序）

每项四栏：改什么 / 简报依据 / 与已证负结论的冲突自检 / 规模（S/M/L）。

| # | 改什么 | 依据 | 冲突自检 | 规模 |
|---|---|---|---|---|
| 1 | **落地 §2.2 四级推送协议**：现有 6 条 Telegram 通道重构为 P0/P1/系统 三个出口 + P2/P3 面板/入库；硬预算与降级规则写入 `intraday_opportunity_alerter` | C§3 医疗告警（强）+ SRE MWMBR（中-强）+ 降频生存（Barber/Chague/Beckmeyer，强） | 无进场语义：P0 推的是「隔夜结构候选＋车道映射」，不是「现在买」；不预测，只报条件成立 | **L** |
| 2 | **推送事件持久化**（push ledger）：每条 P0-P3 落库（含当时期权 mid/spread 快照）——见 §三(e) 表结构 | D§5 Perold IS 全靠它；E 值得抄第 1 条 Missed Trades | 纯记账，无冲突；修正简报 D 的「已有」误判 | **M** |
| 3 | **财报前长权利金默认拦截**：短名单/计划面板/推送均检查 5 日内财报（日历缓存已在 `opportunities.py` v3），命中则推送降级 + 红字「财报前买权利金是文献最确定亏损模式之一（IV 买贵+点差+拖延平仓）」 | de Silva-Smith-So 2026（**强**：均亏 5-9%，高预期波动事件 10-14%） | 拦截提示≠预测；只读系统只降级推送、不禁止任何行为 | **S** |
| 4 | **跨周末持仓提示**：周四/五开的 4-7DTE 候选与持仓，自动附「长权利金跨周末系统性负（Jones-Shemesh）」标注 | A§4.3（强） | 提示结构性成本，非方向预测 | **S** |
| 5 | **UOA/大单语义改写**：文案禁用「聪明钱」；替换为「异常量（高 O/S 历史上偏空；来源可能是对冲/库存流）」；sweep 标签语义＝「有人愿付跨所成本＝紧急」，紧急≠方向 | B§2/§3（Pan-Poteshman 强但公开量版本弱；Muravyev 2016 强；sweep 传闻） | 与 F1 一致：盘中刺激跟单已被本人数据证伪 | **S** |
| 6 | **盘前面板信号重排**：新增 C−P 隐波价差列（同 strike 同到期，链快照可算）与 O/S 列（注意偏空方向）置顶；总量 P/C 降为情绪展示；大单打印降权 | B§7 落地清单第 1 条（C-W 强、O/S 强、时间尺度与 4-7DTE 精确重合——B 全篇最重要对齐） | 横截面弱信号（周度 30-50bp）只用于提高候选池基础比率，不提高单笔仓位、不诱导日内进场 | **M** |
| 7 | **期权墙两语义拆分**：到期日尾盘＝吸附语义可推送（P2）；非到期日＝仅显示 gamma regime（正 γ 压波动/负 γ 放大动量尾部）+ GEX 符号假设免责 | B§4（NPP 2005 强，仅到期日；非到期日吸附传闻；NPPW 2021/Barbon-Buraschi 波动率通道） | 无方向预测；免责文案走现有 info-hint 机制 | **S/M** |
| 8 | **尾盘持仓助手**（P1-b，见 §2.2）：仅指数级条件（QQQ/SPY ROD 方向 + 高波动日）、仅对已持仓位、输出「持有到收盘/查看」两态 | A§1（Gao 2018 强 / Baltussen 2021 强 / Rosa 2022 中：唯一有顶刊背书的日内时段结构，正确用法就是收盘前持平仓决策） | 与 F1 无冲突（不是进场）；效应 bp 量级故绝不据此开新仓；个股不适用（Heston 反向） | **M** |
| 9 | **R6 灾备保险丝接入推送**：当日已实现亏损 ≥ 宽线（暂定 $12k，文档 16 §5.2：与 gate 不叠加、只作灾备）→ 当日 P0/P1 全部降级为 P2 + 页面横幅「当日保险丝已触发，明日复位」；当日不可上调（Topstep 式自锁精神） | C§4（Topstep 官方文档 中；SEC 15c3-5 强：风控闸门不在交易员手里是市场结构法定设计）+ 文档 16 R6 模拟 | 只读红线：只降级推送与界面提示，**不锁任何交易功能**（系统本来就不能下单）；非预测 | **S/M** |
| 10 | **regime gate 接入推送抑制**：`tsmom_mm_gate` 红灯日抑制一切 0-1DTE 车道相关推送（R1）；绿灯**不得**出现在推送理由里（防御性部署裁定，PBO 62.9% 未过） | 文档 16 §5.1/§5.3；Harvey et al. 2018（左尾变薄稳健） | gate 只关不开；抑制≠预测 | **S** |
| 11 | **短名单化**：盘前产出当日 ≤10 只短名单（催化剂在册 + 时段调整 RVOL），P0/P1 只从短名单产生；无催化剂日显式标记「预期空转日＝合法休息」 | C§1（Bellafiore 中）；RVOL 阈值本身传闻级故只做准入不做信号 | 「合法休息」正面制度化了用户风格；F2 无冲突（短名单是注意力管理不是好日子预测） | **M** |
| 12 | **推送载荷带摩擦核算**：每条 P0/P1 附「往返摩擦 =点差% + 2×1.25%」vs ATR 波动预算，并存 mid 快照（服务 #2 与 IS 的 Cd/Ci 分量） | A§5（摩擦与信号差 2-3 个数量级的数学，强）+ D§5 | 纯算术展示 | **S/M** |
| 13 | **个股追涨型推送默认标注 fade 先验**：散户注意力个股高开放量的文献预期是日内衰竭不是延续；指数/ETF 才有末盘延续 | A§3（Berkman 2012 强 / Heston 2010 强：对两类标的用相反方向先验） | 标注先验≠预测方向；仅影响推送措辞与分级 | **S** |

---

## 三、深度复盘界面设计规格（本文核心交付）

设计总纲（简报 D+E 的合成）：复盘火力从「这笔进场时机对不对」（F1：无学习信号的维度）整体转向**执行与纪律**（高效度、即时反馈、Kahneman-Klein 意义上真正能练出技能的维度）；一切界面顺序服从「先决策快照、后结果」（Fischhoff/Baron-Hershey）；过程与结果两本账，永不合成一个分数（Duke anti-resulting）；每日 5 分钟、引导式步骤序列，而不是一堆面板（Steenbarger 日志五大失败模式的针对性解药）。

### (a) 引导式日终复盘流（Guided Daily Close Review）

新路由 `/journal/review/daily`（`JournalDailyReviewPage.tsx`），**步进器（stepper），非面板堆**。目标时长 ≤5 分钟。全程**默认不显示当日 P&L**（盲评模式）；每步可跳过但记「标缺」，绝不静默补默认值。

```
S0 检票（自动）
    读当日 ET 自然日的激活 build episodes（新开/平仓）。
    ├─ 无任何活动 → 「休息日确认」一键完成（写 session_kind=rest_day）
    │   → 休息纪律连续计数 +1（过程指标 7 正向记账；「不交易也是仓位」制度化）
    └─ 有活动 → 进入 S1
S1 推送清账 + 违规扫描（自动表格，双栏）
    左栏（Phase B 起）：当日 P0/P1 推送逐条标注 acted / passed(+一词理由) / missed
    右栏（Phase A 即有）：当日新开 episodes × V-规则机械分类器判定
      （classify_rule_lane：compliant / violation / uncovered / unknown）
      每条 violation 强制一句话确认（append-only），不写不能进下一步——
      Lerner-Tetlock：过程问责在暴露处生效
S2 过程打分卡（七项，见下表；自动优先，手动兜底，缺席标缺）
S3 持仓出场预登记
    当前未平的隔夜仓位，若无在册出场计划 → 现在登记
    （invalidation_plan / exit_reason 计划，写入 review annotation 修订链，
     锁定时间戳＝此后「规则出场 vs 情绪出场」判定的唯一依据）
S4 封卷
    可选一句话日志 → 会话密封（append-only revision，含 sha256）
    密封后出现「揭示当日结果」按钮（自愿点击）；
    reveal_after_seal 与时间戳入库——顺序本身是数据
```

七项过程指标（简报 D§6）与 Phase A 可得性：

| # | 指标 | 判定来源 | Phase A |
|---|---|---|---|
| 1 | 推送依从率（只打被推的 / freelance 笔数） | push ledger × episode 关联 | **标缺**（Phase B 起自动） |
| 2 | 结构依从率（合规车道风险占比） | `classify_rule_lane` 按 \|opening_cash_flow\| 加权 | 自动 |
| 3 | 出场规则依从率（规则出场 vs 情绪出场） | S3 预登记 vs 实际平仓对照 | 自登记首日起可判，此前标缺 |
| 4 | 有效点差支付（\|成交−下单时 mid\|/报价点差） | 需下单时 mid 快照 | **标缺**（Phase B/C） |
| 5 | 费用占比（total_fee / 权利金，对照 1.25% 基线） | episode 台账 | 自动 |
| 6 | 决策日志完整率（当日 episodes 有当日进场侧 annotation 的比例） | review annotation 时间戳 | 自动 |
| 7 | 休息纪律（无机会日零交易达成 / 连续计数） | S0 + episodes | 自动 |

UI 契约：每个自动值带来源徽章（自动/手填/标缺）；打分卡上**没有任何 P&L 字段、没有任何按盈亏着色**；表单只有 是/否/标缺 与一句话文本。

### (b) 单笔深潜（per-trade deep dive）——改造 `JournalEpisodeReviewPage.tsx`

**布局强制顺序（代码层约束，不是排版建议）**：

1. **区①决策时快照**（默认展开）：进场时刻可知的信息——`regime_score_at_entry`（E-4 回填后；缺则标缺）、当日 gate 状态、触发推送（Phase B 起关联 push_id；无推送＝freelance 标记）、车道判定与规则编号（`RULE_COMPLIANCE_LANE_RULE_IDS`）、在册出场计划（S3 登记的）、进场时 IV/墙位上下文（若当时快照存在，否则标缺）。
2. **区②结果揭示**（默认折叠，点击展开）：K 线 + 成交标记 + 盈亏 + 偏移诊断（下）。AI 分析文本也在此区，且必须分「过程段/结果段」两节生成。
3. **区③双轨标签 + 四象限**（揭示后可编辑）。
4. **区④单笔反事实**（只读）。

**MAE/MFE 的正确用法（避开 F6 循环陷阱）**：

- 定义（标的近似口径，诚实标注）：期权盘中逐笔路径不可得（Tradervue 官方文档公开放弃期权 MFE/MAE——行业证词，E§1.1 强；ONE 的解需要盘中期权链库，个人不可行）。用**持有窗口内标的 5m bar 路径**近似：
  - `exposure = +1`（long call / short put / long stock）或 `−1`（long put / short call / short stock）；组合腿与 underlying 不明者 `not_applicable`。
  - `U0` = opened_at 之后第一根 5m bar 的 open（盘前开仓且无 bar → 用当日首根 RTH bar，记 flag）。
  - 逐 bar：favorable 用 `exposure=+1 ? high : low`，adverse 反之；
    `mfe_underlying_pct = max(0, max_t exposure×(fav_t/U0 − 1))`，
    `mae_underlying_pct = min(0, min_t exposure×(adv_t/U0 − 1))`，并记 `mfe_at / mae_at / mae_before_mfe`。
  - 跨日持仓的隔夜跳空以次日首根 bar 体现（RTH bars only，标注「盘后路径不可见」）。
  - **不做** BS/delta 折算成期权盈亏——那是假精度；展示原始标的偏移并挂「标的近似，非期权价格路径」。
- **展示纪律（三条陷阱的对策，D§3）**：
  - 只读诊断散点：MAE% vs 最终 R，按持有结构（日内/隔夜）分层，只看形态不定参数；
  - 图面永久免责（不可关闭）：「**本图不用于设置止损；不得由此推导出场/持有参数。对尾部结构账户，按 MAE 分位收紧止损最大概率截掉的恰是右尾。**」
  - 全系统**禁止**任何字段/接口把 MAE/MFE 接到参数或建议上；
  - 被许可回答的唯一问题：「我的规则性出场是否系统性发生在后来恢复的 MAE 深处？」——季度重估（§(c)），不做单笔优化；
  - **尾部捕获率**（唯一真正重要的结果指标，D§6-9 + E 值得抄末条）：仅对赢单尾部（当期 R 最高十分位）计算 `MFE 捕获 = 实现盈亏相对 MFE 近似的比例`，回答「我是否截断了本可成为 top-5 的单」。只在季度视图出现。

**决策质量标注（过程/结果分离，anti-resulting）**：

- 两条独立轴，**永不合成**：
  - 过程轴（机械优先）：车道判定（分类器）∧ 推送依从（Phase B）∧ 出场计划依从（S3 对照）→ 合规/违规/不可判定（uncovered/unknown 不硬归类，标缺）；
  - 结果轴（自动）：`realized_pnl_net` 符号与 R 值。
- **四象限自动落格**（Duke 操作化，D§1.2）：应得的赢 / 坏运气（记录后放行，不改规则）/ **侥幸（违规∧盈利，标红，月度首看其趋势）** / 应得的输（学习价值最高）。
- 手工标签走**双轨词表**（TraderSync setup/mistake 双轨，E 值得抄）：收益归因轨＝推送类型×regime×持有结构；损耗归因轨＝固定 mistake 词表（建议初版：`freelance_no_push / chase_after_expiry / late_0dte / early_exit_fear / size_overrun / revenge_add / plan_absent`），沿用现有 labels 机制（`review_repository`，append-only 修订链已具备——这正是 Kahneman-Mauboussin「决策日志必须只追加」的实现）。
- UI 反 resulting 三禁：无单笔「评分」；过程面板不按盈亏着色；标签修订永不覆盖（链上留痕）。

**What-if 回放「如果遵守规则」（机械，非预测）**：

- 内核＝V-规则机械分类器（`src/journal/personal_edge.py::classify_rule_lane`；`rule_set_id` 参数化——当前激活集 v2，用户口径的 V3 修订落地后同接口切换）。
- 单笔视图（区④）：本单车道判定 + 若 violation：「在『仅合规车道』反事实中本单被移除（贡献 X）」。
- 期间视图（日/月/季）：
  - 反事实 A「仅合规车道」：移除全部 violation 单后的净盈亏 vs 实际；
  - 反事实 B「gate 执行」：`tsmom_mm_gate` 红灯日移除 0-1DTE 单（文档 16 §5.1 同口径重放）；
  - 两者**不叠加展示为最优组合**（文档 16 §5.2(c)：叠加反而差 $32k——如实呈现该结论）。
- 固定诚实边界（随图携带）：移除式反事实假设无替代行为；gate 只防不攻（样本外放行日仍净亏，gate 减损不点金）；n 与 bootstrap CI 按 R5 展示，n<100 只报「距离显著还差多少笔」。此为对本人历史的记账，非预测。

### (c) 周期聚合：过程指标与结果指标物理分离

两个入口，不同页面、不同节奏（D§7 骨架）：

**过程仪表盘**（日/周，随日终流）：七项过程指标的周汇总与趋势；本周唯一目标（上周目标核查 → 立新目标，一次只立一个）；疲劳仪表（各级推送 acted 率，Phase B）。**此页无任何 P&L。**

**结果室**（月/季，需主动进入，入口带提示「结果含大量噪声，仅看分布不看均值」）：

- 月度（约 1 小时，AAR 四问向导：计划遵守什么 → 实际发生什么（过程汇总+四象限计数，先对齐事实）→ 差距为何 → 保持/改变什么；时间配比 25/25/50，TC 25-20）+ 月度 IS 分解（下）+ 固定动作：≥1 笔最佳 4-7DTE 隔夜单写成 Playbook 条目（走 `playbook_repository` 晋升合同）。
- 季度（半天，**唯一允许解读结果分布的场合**）：R 分布直方图 + 右尾贡献占比（top-5/毛利）＋尾部捕获率＋bootstrap 期望值 CI＋regime 条件化边际重验证（对接文档 16 §5.3 预注册判据：实盘 t>3 → H1 升格；gate 被关日盈亏持续为正 → gate 降级废除——判据已写死，界面只做自动核对与展示，避免事后合理化）＋ MAE/MFE 诊断散点重估。
- **月度 IS 分解**（Perold 1988 降维，D§5——简报 D 认定的全篇杠杆最高项）：`IS = Cd 延迟（推送 mid→下单 mid）+ Ce 显性（费用，已有）+ Ci 隐性（½×有效点差）+ Co 机会（passed/missed 推送的纸面结果）`。Phase A 仅 Ce 可算，Cd/Ci/Co 标缺；Phase B 起随 push ledger 与 mid 快照逐列点亮。在 12.6% 点差 + 1.25% 费用的环境里，执行成本极可能是仅次于隔夜边际的第二大可控项，且是高效度反馈维度。
- **禁用指标清单（进代码常量，UI 永不渲染）**：单笔/单日/单周 P&L 评分；胜率作为标题指标（仅允许出现在分布上下文内；`personal_edge` 现有 win_rate 保留为描述统计）；SQN/Sharpe 类一致性合成分；MAE 分位止损参数；hold-time 优化回归（F6）。

### (d) 与现有资产的集成

| 现有资产 | 集成方式 |
|---|---|
| Episode 台账（`episode_repository`，激活 build） | 脊柱：一切新表以 `(build_id, episode_id)` 外键挂接；build 切换时偏移与会话记录按新 build 重算/重挂 |
| Review annotations（append-only 修订链） | S3 出场预登记与 violation 确认直接写该链（新增用途，不新增机制）；`review_status` 与日终流互认 |
| V 规则遵守前向表（`RuleCompliancePanel`） | 判定内核共享 `classify_rule_lane`；前向表新增「日终复盘完成率」列（会话记录聚合）；反事实 A 与该表同口径（clean basis、风险加权定义原样引用，不复制数值进前端） |
| Playbook（文档 13 晋升合同） | 月度固定动作产出候选条目；归档键迁移为 推送类型×regime×持有结构 |
| Regime（`regime_scores`、`tsmom_mm_gate`、E-4 回填） | 区①快照引用 `regime_score_at_entry`（缺→标缺）；反事实 B 调 gate 纯函数；季度重验证读 gate 逐日记录 |
| Reality Test / edge_stats | 季度结果室复用 `edge_stats` 的 bootstrap CI 与分桶函数；不另起平行实现 |
| 推送链路（`intraday_opportunity_alerter`） | Phase B：dispatch 前落库 push ledger；S1 左栏消费之 |

### (e) 数据需求：已有 vs 需新采

**已有（直接可用）**：episodes 全字段（含 `opened_at/closed_at/hold_seconds/realized_pnl_net/total_fee/opening_cash_flow/dte`、censoring 与完整度标记）；费用归因（episode 级 + `journal_v2_broker_fee_observations`）；决策标签部分存在（annotation 六字段 + labels，append-only）；机械分类器与 clean-basis 定义；`regime_scores`（85 天，5-6 月缺——缺即标缺）；gate 纯函数；月度复盘模块；5m bar **可抓**（yfinance，`src/regime/` 已用）。

**5m bar 的硬约束（必须写进实现）**：yfinance 5m 仅回溯 ~60 天 → (1) 偏移回填只覆盖近 ~60 天内的 episodes，更早的**永久标缺**（不冒充）；(2) Phase A 起建立**每日持久化**任务（当日持有过的 underlying 的 5m bars 落库），让覆盖率随时间趋近 100%。

**需新采（按依赖排序）**：

1. `journal_v2_episode_excursions`（Phase A）：`(id, build_id, episode_id, code_version, source='underlying_5m', status: ready|partial|missing_bars|not_applicable, u0, u0_at, mfe_underlying_pct, mfe_at, mae_underlying_pct, mae_at, mae_before_mfe, bars_used, coverage_start, coverage_end, computed_at)`，`(build_id, episode_id, code_version)` 唯一，幂等重算。
2. `journal_v2_daily_review_sessions`（Phase A）：`(id, account_key, et_date, session_kind: trading_day|rest_day, revision, previous_id, started_at, sealed_at, revealed_after_seal, steps_json, process_scores_json[7×{value,basis:auto|manual|标缺}], violation_acks_json, note, content_sha256)`，append-only 同 annotation 约定。
3. 5m bar 存储（Phase A）：`market_5m_bars (symbol, bar_ts, o,h,l,c,v, fetched_at)`，或复用 data_provider 现有缓存层（如已有等价物则不新建——遵循不平行实现原则）。
4. Push ledger（Phase B）：`journal_v2_push_events (push_id, tier P0..P3, ts, ticker, lane_rule_id, playbook_ref, payload_json, option_mid, option_spread_pct, gate_state, suppressed_reason)` + `journal_v2_push_outcomes (push_id, verdict: acted|passed|missed, reason_word, episode_id?, tagged_at)`。
5. 出场预登记的字段化（Phase A 用现有 annotation 文本字段起步；Phase B 若需机判「规则出场 vs 情绪出场」，增加结构化 `exit_plan_json`）。
6. mistake 词表常量（Phase A，纯常量 + labels 校验）。
7. `regime_score_at_entry` 回填（既有 E-4 计划，Phase C 对接）。

### (f) 分期交付（每期独立可交付、独立可回滚）

**Phase A —— 引导式日终复盘 + 既有数据上的 MAE/MFE（无新采集依赖，5m 抓取除外）**

后端：
- 新增 `src/journal/excursions.py`：纯函数 `compute_excursion(episode, bars) -> ExcursionResult`（(b) 节公式；exposure 判定表；status fail-closed）＋ `src/journal/tests/test_excursions.py`（用例：long call 先深 MAE 后大 MFE；short put；跨日跳空；无 bar → missing_bars；组合腿 → not_applicable）。
- 新增 `src/journal/ledger/daily_review_repository.py`：会话 append/list（复制 `review_repository` 的修订链模式）＋ 表 1/2 建表迁移＋测试。
- 回填脚本 `scripts/backfill_excursions.py`（近 60 天窗口；逐 underlying 抓 5m → 落库 → 逐 episode 计算；超窗 episodes 写 status=missing_bars）＋ 每日增量任务挂现有调度。
- 新增 `api/v1/endpoints/journal_review_flow.py` + `api/v1/schemas/journal_review_flow.py`：
  - `GET /api/v1/journal/review-flow/daily?date=` → `{session?, episodes_today:[{episode, lane_verdict, rule_id, quadrant, excursion?}], open_positions_needing_exit_plan, process_metrics_auto[7]}`（判定服务端算，前端不复算）；
  - `POST /api/v1/journal/review-flow/daily` → 追加会话修订；
  - `GET /api/v1/journal/episodes/{id}/excursion` → 记录或标缺原因。
- 四象限判定放服务端（分类器 verdict × pnl 符号；uncovered/unknown → 不判定）。

前端（`apps/dsa-web`）：
- 新路由 `/journal/review/daily` + `JournalDailyReviewPage.tsx`（S0-S4 步进器；盲评默认；来源徽章；密封→揭示顺序落库）。
- `JournalEpisodeReviewPage.tsx` 改造为区①→④强制顺序（区②默认折叠）。
- `components/journal/review/ExcursionDiagnostics.tsx`：单笔偏移条 + 聚合散点（MAE% vs R，按持有结构分层），永久免责文案组件化。
- Journal 首页入口卡：「今日复盘 未开始/已密封/休息日」。

验收（Phase A 完成定义）：`./scripts/ci_gate.sh` 过；`npm run lint && npm run build` 过；日终流全程 ≤5 分钟可走完；无 bar 覆盖的 episode 在 UI 显示「标缺（超出 5m 数据窗口）」而非空白或 0；免责文案与禁用指标常量有测试锁定。
回滚：全部为新增文件/新表 + 单页改造，删除即回滚，不触碰扫描与推送链路。

**Phase B —— 推送闭环**：push ledger（改 `intraday_opportunity_alerter` dispatch 落库）＋四级协议与硬预算＋S1 左栏推送清账＋疲劳仪表＋过程指标 1/4 点亮＋IS 的 Cd/Ci 分量＋改进清单 #3/#4/#5/#9/#10/#12。独立可交付：Phase A 的流在无 push ledger 时左栏标缺，Phase B 落地后自动点亮。

**Phase C —— 周期聚合与集成收尾**：月度 AAR 向导＋季度结果室（预注册判据自动核对）＋完整 Perold IS（Co 用 passed/missed 纸面结果）＋Playbook 归档键迁移与月度晋升动作＋`regime_score_at_entry` 回填对接＋盘前面板信号重排（#6）与墙位语义拆分（#7）＋尾盘持仓助手（#8）＋短名单化（#11）。

---

## 引用清单（按主题合并自五份简报，作者/年份）

**日内动量与时段结构**：Gao, Han, Li & Zhou (2018, JFE)；Baltussen, Da, Lammers & Martens (2021, JFE)；Rosa (2022, JFM)；Li, Sakkas & Urquhart (2022, JFM)；Heston, Korajczyk & Sadka (2010, JF)；Bogousslavsky (2021, JFE)；Mesfin (2026, arXiv 预印本)；Holmberg, Lönnbark & Lundström (2013, FRL)；Zarattini & Aziz (2023, SSRN，未刊)；Zarattini, Aziz & Barbon (2024, SSRN，未刊)；Crabel (1990)。

**隔夜/日内分解与期权结构**：Lou, Polk & Skouras (2019, JFE)；Hendershott, Livdan & Rösch (2020, JFE)；Boyarchenko, Larsen & Whelan (2023, RFS；及 NY Fed Liberty Street 2026 后续)；Berkman, Koch, Tuttle & Zhang (2012, JFQA)；Aboody, Even-Tov, Lehavy & Trueman (2018, JFQA)；Muravyev & Ni (2020, JFE)；Jones & Shemesh (2018, JF)。

**期权流信息含量**：Pan & Poteshman (2006, RFS)；Cremers & Weinbaum (2010, JFQA)；Johnson & So (2012, JFE)；Ge, Lin & Pearson (2016, JFE)；Hu (2014, JFE)；Chan, Chung & Fong (2002, RFS)；Muravyev (2016, JF)；Easley, O'Hara & Srinivas (1998, JF)；Augustin, Brenner & Subrahmanyam (2019, Mgmt Sci)；Chakravarty, Jain, Upson & Wood (2012, JFQA)。

**Pinning / gamma / 0DTE**：Ni, Pearson & Poteshman (2005, JFE)；Golez & Jackwerth (2012, JFE)；Ni, Pearson, Poteshman & White (2021, RFS)；Barbon & Buraschi (2021, 工作论文)；Dim, Eraker & Vilkov (2024, 工作论文)；Amaya, Garcia-Ares, Pearson & Vasquez (2025, 工作论文)。

**散户绩效与成本**：Bryzgalova, Pavlova & Sikorskaya (2023, JF)；Beckmeyer, Branger & Gayda (2023, SSRN)；Bogousslavsky & Muravyev (2024, SSRN)；de Silva, Smith & So (2026, Rev. Finance)；Barber, Lee, Liu & Odean (2014, JFM)；Barber & Odean (2008, RFS)；Chague, De-Losso & Giovannetti (2020, SSRN)；Gervais, Kaniel & Mingelgrin (2001, JF)；Nagel (2012, RFS)。

**机构实践与告警工程**：Bellafiore (2010, *One Good Trade*; 2013, *The PlayBook*)；Berkowitz, Logue & Noser (1988, JF) / Madhavan (2002)；Wilder (1978)；Joint Commission Sentinel Event Alert #50 (2013)；Cvach (2012)；van der Sijs et al. (2006)；Ancker et al. (2017)；JAMIA 系统综述 (2019)；Ewaschuk（Google SRE）；Google SRE Workbook ch.5；PagerDuty severity levels；SEC Rule 15c3-5 (2010)；Topstep 官方风控文档；SMB Training。

**复盘方法论与决策科学**：Baron & Hershey (1988, JPSP)；Fischhoff (1975)；Duke (2018, *Thinking in Bets*)；Kahneman & Klein (2009, Am. Psych.)；Lerner & Tetlock (1999, Psych. Bulletin)；Steenbarger (2009, *The Daily Trading Coach*; 2015, *Trading Psychology 2.0*)；Sweeney (1996, Wiley)；Tharp (1998/2007)；Kinlay（SQN 批评）；Perold (1988, JPM)；SEC Rule 605；美陆军 TC 25-20 (1993, AAR)；Mauboussin (2012, *The Success Equation*)；Odean (1998, JF)；Locke & Mann (2005, JFE)；Coval & Shumway (2005, JF；2001, JF)。

**工具基准（官方文档为准）**：Tradervue（含期权 MFE/MAE 排除声明）；Edgewonk（Tiltmeter / Missed Trades / Alternative Strategies / Simulator）；TradeZella（Rule Adherence / Zella Score）；TraderSync（What-If / setup-mistake 双轨）；TradesViz（DTE 一等公民 / 希腊值统计化）；Wingman Tracker；OptionNet Explorer。

**本仓取证**：`New-docs/phase1/15_TRADING_DISCIPLINE_EVIDENCE.md`；`New-docs/phase1/16_EDGE_FORENSICS_AND_REGIME_GATE.md`（Goyal & Saretto 2009；Carr & Wu 2009；Lo & MacKinlay 1988；Moreira & Muir 2017；Daniel & Moskowitz 2016；Moskowitz, Ooi & Pedersen 2012；Coates & Herbert 2008；Thaler & Johnson 1990；Bailey & López de Prado 2014；Bailey, Borwein, López de Prado & Zhu 2015；Harvey, Liu & Zhu 2016；Harvey et al. 2018 亦引自该文档）。
