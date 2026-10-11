# 盘中手机提示：Linux VPS 常驻部署

这份手册只覆盖一个目标：本地 Mac 关机后，服务仍能在美股交易时段扫描，并通过
Telegram 向手机发送提示。它不会创建云主机、代替用户登录 Moomoo，也不会启用
任何交易动作。

## 1. 推荐边界

```text
Linux VPS（单机）
├── OpenD 官方 Linux 命令行程序（宿主机，127.0.0.1:11111）
└── stock-server 容器（host network，单 Uvicorn worker）
    ├── FastAPI lifespan：独立快信号时钟 + 慢页面预热 + 提示器
    ├── /app/data：SQLite + 已验收 activation artifact 持久化
    └── Telegram Bot API：HTTPS 出站
```

必须使用**单台、单副本、单 Uvicorn worker**。当前盘中 scheduler、single-flight、
动量历史和 09:31–09:35 逐分钟确认都含进程内状态；不要开启多副本、autoscaling 或
`--workers > 1`。SQLite 也只适合这里的单主机写入，不要把数据库放到缺少可靠文件锁
语义的共享网络盘。

OpenD 官方目前不提供 Docker image，所以本方案让官方 Linux 二进制运行在宿主机。
应用容器使用 Linux host networking；这样 `MOOMOO_OPEND_HOST=127.0.0.1` 仍指向宿主机
OpenD，同时 11111 不需要监听公网或 Docker bridge。

## 2. 需要用户准备

- 一台持续在线的 Linux x86_64 VPS；容量应同时覆盖 OpenD、Python/pandas/scipy 和
  扫描缓存，建议从 2 vCPU、4 GiB RAM 起步并按实测调整。
- Docker Engine 与 Docker Compose plugin。
- 从 Moomoo 官方渠道下载的 Linux OpenD 包，以及具备所需美股/期权实时行情权限的
  Moomoo 账户。
- Telegram Bot Token 和目标 Chat ID。
- 将已验收的代码放到 VPS；本地未提交文件不会自动出现在 GitHub 部署中。

不要把 Moomoo 密码、Telegram Token 或其他 API Key 发到聊天、提交到 Git，或写入
Dockerfile。VPS 上的项目 `.env` 应设置为仅部署用户可读：

```bash
chmod 600 .env
```

## 3. 安装并托管 OpenD

按 Moomoo 官方 Linux Command Line OpenD 文档安装。发行包中的 `OpenD.xml` 和
`Appdata.dat` 都是启动所需文件；先在目标 VPS 上执行该版本自带的 `OpenD -help`，再
根据实际输出编写 systemd unit。不同 OpenD 版本的参数可能变化，本仓库不猜测或复制
未经目标机验证的 CLI flags。

必须满足以下运行合同：

1. OpenD API 只监听 `127.0.0.1:11111`；云安全组和主机防火墙都不要开放 11111。
2. 用专门的非 root 用户运行，`OpenD.xml` 权限为 `0600`。
3. 用 systemd 或等价 supervisor 设置开机启动、异常重启和日志轮转。
4. 持久保存 OpenD 配置、日志和非 Windows 数据目录
   `~/.com.moomoo.OpenD/F3CNN/`，特别是 `Device.dat`。丢失设备状态可能重新触发设备锁。
5. 用户亲自在首次登录时完成账户协议、设备锁和手机验证码；后续若 Moomoo 要求重新
   验证，仍需用户介入，不能承诺永久无人值守登录。
6. 明确选择 `auto_hold_quote_right` 行为。让 OpenD 优先持有最高行情权限有利于扫描，
   但可能影响同账户手机或桌面客户端的最高行情权限。

仅端口可连不代表 OpenD 已登录，也不代表账户拥有期权行情权限。上线验收必须读取真实
美股快照和所需期权链，而不能只做 TCP 探测。

## 4. 应用配置

从 `.env.example` 复制一份仅存在于 VPS 的 `.env`，至少核对：

```dotenv
DATABASE_PATH=/app/data/stock_analysis.db
LOG_DIR=/app/logs

MOOMOO_OPEND_ENABLED=true
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111

INTRADAY_REFRESH_SCHEDULER_ENABLED=true
INTRADAY_REFRESH_INTERVAL_SECONDS=60

# 严格 2m 全自选研究；保持开启也不会自动发手机
INTRADAY_P0_2M_ENABLED=true
# 当前没有通过真实 69 只 + 20 日前瞻 shadow 的 artifact 时必须保持 false
INTRADAY_P0_2M_ACTION_ENABLED=false
# 通过门禁后才填写容器内持久路径，并把上一行改为 true
# INTRADAY_P0_2M_ACTIVATION_ARTIFACT=/app/data/p0_2m_activation.json
# 可选：设 true 接收真实四条件研究提醒；仍为 P2，未验证收益，不是正式 P0
INTRADAY_P0_2M_SHADOW_MOBILE_ENABLED=false

# P1-O 与旧四级提示流开关；不是 strict-2m P0 的 action 开关
INTRADAY_ALERTS_ENABLED=true

TELEGRAM_BOT_TOKEN=<在服务器填写>
TELEGRAM_CHAT_ID=<在服务器填写>
```

再按实际策略填写 `STOCK_LIST`、`INTRADAY_WATCHLIST`、
`INTRADAY_PINNED_TICKERS` 和 `INTRADAY_DEEP_LANE_MAX`。修改上述启动期配置后必须重启
server。只需要手机提示时，不必开放 Web 端口。

三类出口不要混为一个开关：`INTRADAY_ALERTS_ENABLED=true` 启用 P1-O 和旧提示流；
strict-2m 研究扫描还要求 scheduler、自选和有效数据。正式 P0/P0-X 手机出口必须同时具备
`INTRADAY_P0_2M_ACTION_ENABLED=true`、与当前完整自选 `universe_hash` 匹配且
`activation_ready=true` 的 artifact，以及 Telegram 凭据。单独改 action 开关不会
绕过门禁。校准前如需手机研究观察，另显式开启
`INTRADAY_P0_2M_SHADOW_MOBILE_ENABLED` 并配置 Telegram；这类真实命中记为 P2，
不代表已验证收益，不占正式 P0/P0-X 指标。研究 RTH 独立限 2/日、7/周，盘外每盘段
1/日、合计 5/周，页面分别显示两套预算。

当前仓库没有附带一份已通过的生产 artifact，也没有完成未来 20 个交易日 shadow；
部署初始状态必须保持 action 为 `false`。校准输入、生成的 artifact 和人工审阅记录应
放在加密备份覆盖的持久位置；运行时 artifact 必须位于容器可见的 `/app/data`，不要只
放在容器临时层或本地 Mac。生成与验收步骤见
[`22_P0_2M_SIGNAL_AND_CALIBRATION.md`](../phase1/22_P0_2M_SIGNAL_AND_CALIBRATION.md)。
20 日 shadow 必须有完整现场观测并全部达标；仅等满 20 天、得到测试成功回执或
补历史回放都不能生成通过资格。此手册和 Compose 文件不代表云主机已经部署在线。

快信号实例每分钟结束后约 5 秒运行 strict-2m/P1-O；慢页面和期权预热使用独立实例及
`INTRADAY_REFRESH_INTERVAL_SECONDS`，不会阻塞快时钟。启用开盘提醒时，后台慢预热在
09:30–09:36 ET 暂停，供应商定稿滞后只在快实例内做有界重试。

校准 CLI 与运行时 validator 必须连接同一个持久 `DATABASE_PATH`。live-shadow exporter
先把导出 SHA-256 写入 append-only
`journal_v2_p0_2m_shadow_export_attestations`；全部门禁通过时，校准 CLI 再把最终
`artifact_sha256` 及其 export/shadow binding 写入
`journal_v2_p0_2m_activation_artifact_attestations`。运行时会按 artifact 的 exact SHA
查询这张表；只复制 JSON、换数据库或丢失 `/app/data/stock_analysis.db` 都会以
`activation_artifact_attestation_not_found` fail closed。因此应在生产持久卷对应的
SQLite 上完成最终校准，artifact 文件和数据库必须一起保留。这个 DB receipt 防止单独
改写 JSON 后重算自哈希，但不是独立密码学签名；部署用户和 SQLite 文件的写权限仍是信任
边界。exporter 和成功校准都不是只读诊断：前者追加 export digest receipt，后者即使未
指定 artifact 输出路径也会追加 activation receipt。

strict-2m 前瞻 shadow 不依赖 Telegram 凭据或 action 开关。server 首次启动会在
`journal_v2_p0_2m_shadow_enrollments` 冻结 69 只和连续 20 个 XNYS 日。若还没有 enrollment
且首次尝试发生在 RTH，仓库会拒绝写入，monitor 会继续重试；收盘后成功建立时，首日是下
一个 XNYS 交易日，不能回填当天。若持久库已有身份匹配的冻结 enrollment，盘中重启会恢复
原窗口，不会另建或顺延。交易日 20:00 ET 起及周末/休市日，历史基线预热目标会切到下一
个 XNYS 交易日；这不改变冻结窗口。随后每个 RTH endpoint 只允许在结束后 0–75 秒（含）
写入 `DATABASE_PATH` 所指 SQLite，超过 +75 秒不补写：首次 endpoint、完整评估、每个
message-id 队列 attempt 是独立 append-only rows。因此 `/app/data` 必须使用宿主持久卷并
纳入备份。删除数据库、换临时容器层、运行多副本或轮换 universe hash 都会留下可见缺口，
不能靠历史重放补回。遥测失败不会阻断页面/提醒主链，但会令上线门禁失败。

这些表不是今日提示 feed。数据正常时，每个 endpoint 至少有 endpoint 与 evaluation
两行：完整交易日约 `69 × 195 × 2 = 26,910` 行，半日市约
`69 × 105 × 2 = 14,490` 行，qualified 队列 attempt 另加少量行。请据此监控磁盘与
SQLite 写入错误。action=false 且未开启研究响铃时，qualified 首次 crossing 会使用真实 P0 装配路径并以
`counterfactual_no_send_same_queue` 放入同一个有界 worker queue；probe 出队后绝不调用
Telegram、绝不写 delivery、绝不占真实预算。实际 action 回执只能标成
`actual_telegram_queue_receipt`；显式研究响铃使用真实消息回执，但仍不计正式 P0 指标。
通用网页队列、布尔 observer 或缺逐票回执都不是 SLA
证据。20 日结束后用 phase1/22 的导出函数生成 `shadow` rows 并追加 export digest receipt；导出日期和标的必须与
冻结 enrollment 完全一致。在 artifact 全部通过并经人工审阅前继续保持
`INTRADAY_P0_2M_ACTION_ENABLED=false`。

## 5. 构建与启动

下面的 Compose 文件面向 Linux VPS，并使用 host networking：

```bash
docker compose -f docker/docker-compose.yml build server
docker compose -f docker/docker-compose.yml up -d server
docker compose -f docker/docker-compose.yml ps
```

默认只启动 `server`。传统每日分析器是可选 profile，且不是盘中提示器的宿主：

```bash
docker compose -f docker/docker-compose.yml --profile analyzer up -d analyzer
```

不要用 `python main.py --schedule` 代替 server；盘中预热与 Telegram 提示器由 FastAPI
lifespan 托管。镜像和 Compose 都固定 Uvicorn 单 worker，并让 Uvicorn 作为容器前台
进程，崩溃后 `restart: unless-stopped` 才能生效。

## 6. 上线验收

### 6.1 API liveness

```bash
curl -fsS http://127.0.0.1:8000/api/health
docker inspect --format '{{.State.Health.Status}}' stock-server
```

`/api/health` 只证明 FastAPI 活着。Docker healthcheck 必须在该请求失败时变成 unhealthy，
但不会因短暂行情或 Telegram 故障重启健康 API。

### 6.2 SDK 与 OpenD 状态

```bash
docker compose -f docker/docker-compose.yml exec server \
  python -c "from moomoo import OpenQuoteContext; print('moomoo-sdk-ok')"

curl -fsS http://127.0.0.1:8000/api/v1/system/moomoo-status
curl -fsS http://127.0.0.1:8000/api/v1/system/health-layers
```

随后在 Web/API 现有只读路径中抽取一只实际关注标的，确认：

- OpenD 不只是 TCP 可达，而是已登录；
- 美股报价时间戳和价格有效；
- 0–7DTE 期权链、Bid/Ask/Delta 所需字段可得；
- 日志出现 `intraday-warm` started，且没有 SDK 缺失或凭据缺失警告。

开盘 v2 还必须单独确认健康层 `opening_v2_1m_provider=ok`。这表示当日目标清单
已经从 Moomoo 得到精确前 20 个 XNYS 交易日的同进度 1m 基准；TCP、SDK 或实时快照
单独变绿都不够。09:00 预热前或刚重启且尚无缓存时，该层显示 `unknown` 是预期状态，
但 09:31 前仍不是 `ok` 就应当视为当日早期提示未就绪。当前基准只保存在单进程内存
12 小时，09:00 后重启会重新拉取；不要在 09:31–09:35 关键窗做常规发布或重启。

> **当前持久化缺口**：opening v2 与 strict-2m 的分钟基线都不是 durable precompute。
> 系统会在缺基线时 fail closed，但不会补发停机期间错过的首个窗口；Docker
> `/api/health` 也只证明 API 活着，不证明基线已恢复。若验收目标只是“服务提前上线、
> 关键窗不重启”的 best-effort，这个限制可以通过运维约束管理；若目标是“异常重启后
> 仍保证最早一分钟/2 分钟提示”，持久化基线或等价的增量预计算是尚未完成的上线
> blocker，当前部署不能声称满足。

strict-2m 的 60 日同钟历史同样是启动后从 Moomoo 重建的进程内状态，不在 SQLite
持久化。`history_loaded_count` 只表示下载完成；`ready_count` 按当前盘段/同钟点核验
固定前 60 个交易日的完整样本。半日市缺少下午 RTH 时槽时不能借第 61 日补足。
周末、休市或 04:00 前/20:00 后没有当前扫描槽，`ready_count=0` 不等于同槽基线故障；
每个盘段开始后的前 18 分钟属于形成期，应从首个合法 18m endpoint 判断就绪。
可修复的数据缺口会有限重取，不能让一个非空下载结果永久标 ready。服务重启后，缺基线的标的保持
`baseline_warming/unavailable`，不会借用其他股票基线，也不会补发重启期间已经错过的
窗口。因此即使 activation artifact 已通过，也不能把“进程已启动”当作“69 只全部
可提醒”。`/api/v1/system/health-layers` 的 `intraday_p0_2m` 层会以只读 readiness
给出 `running`、`stream_ready`、`ready_count`、`baseline_warming_count`、
`unavailable_count`、`universe_hash`、action/artifact 状态和
`mobile_eligible_symbol_count`：

- 研究开启但 action 保持 `false` 时，该层为 `degraded` 是预期 shadow 状态；
- action 请求为 `true`，但当前标的 stream、同槽基线、artifact 或投递门禁未通过时
  不可发送，不能把 API liveness 当成可发送；
- 只有 action 已启用且上述门禁通过时才应为 `ok`。

发布应避开交易窗口，并在该层确认目标 `universe_hash` 与就绪/手机 eligible 数量后
才算恢复完成。健康读取不会反向抓行情或重连，因此不会为了显示绿色而改变系统状态。

### 6.3 Telegram 最后一公里

部署时主动发送一条无静默标志的测试消息，并在手机上确认机器人会响铃。还应检查
Telegram 会话未静音、未归档，系统通知和后台刷新已允许。strict-2m 在确认 accepted
前原子提交 decision 与可恢复 outbox，首次网络发送也必须晚于持久化；数据库不可读或
写失败则拒绝本次受理并重检，不退回内存去重。普通消息有跨重启的最多 6 分钟重试窗口，
过期终止并标注延迟；未知送达不自动重发。opening 不跨 checkpoint 补发，09:35 最终
提醒截止 09:36。页面按 `message_key` 关联回执与 outbox，显示等待、失败或结果未确认；
accepted 或“Telegram 已接收”不是手机响铃/已读证明。频道测试不等于真实行情到手机的验收。

## 7. 持久化、备份与恢复

Compose 将宿主机项目目录下的 `data/`、`logs/`、`reports/` 分别挂载到 `/app` 下；
其中 `/app/data/stock_analysis.db` 保存推送台账、当日去重和预算证据。容器重建不应删除
这些目录。生产 activation artifact 也应保存在 `/app/data`；只写入容器文件系统的
artifact 会在重建时丢失并使手机出口 fail closed。

持久卷不等于备份。至少每天使用 SQLite online backup 或云盘快照生成一致性备份，保存
到另一故障域，并定期实际恢复验证。不要在应用写入期间直接复制 WAL 数据库单文件。

OpenD 的设备状态也必须独立备份，但不要把包含账户配置的文件放进应用仓库或 Docker
build context。

## 8. 网络与远程访问

本 Compose 把 API 绑定在 VPS loopback，不开放 8000。临时查看 WebUI 可用 SSH tunnel：

```bash
ssh -L 8000:127.0.0.1:8000 <user>@<vps>
```

然后本机访问 `http://127.0.0.1:8000`。如需长期多人访问，应另行部署带 TLS 和认证的
反向代理或 VPN；不要直接把未保护的 8000 暴露给 `0.0.0.0/0`。

容器需要的公网能力只有 HTTPS 出站（Telegram 及已配置的数据源）。不要在工单、CI
日志或聊天中粘贴 `docker compose config` 的完整输出，因为它会展开 `env_file` 中的
密钥。

## 9. 更新与回滚

更新前先备份 SQLite 与 OpenD 状态，再构建并只替换 server：

```bash
docker compose -f docker/docker-compose.yml build server
docker compose -f docker/docker-compose.yml up -d --no-deps server
curl -fsS http://127.0.0.1:8000/api/health
```

回滚时恢复上一份已验收代码/镜像和兼容数据库备份，再重复 liveness、OpenD 行情、期权
链和 Telegram 四项验收。仓库不会自动执行 commit、push、购买 VPS 或写入用户登录信息；
这些仍需用户明确提供和操作。
