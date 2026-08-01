# 期权研究数据接入与权限配置

> 适用项目：`daily_stock_analysis`
>
> 核对日期：2026-07-24。行情权限、套餐名称和价格可能变化，购买前必须重新打开本文链接确认。
>
> 本文只讨论只读研究数据。项目不得解锁交易、下单、改单或撤单。

## 先看结论

当前最合理的组合不是“把所有 Key 都买一遍”，而是：

1. **继续使用已经实测可用的 Moomoo OpenD**，作为期权概览、IV、OI、执行价墙和 Moomoo 异动事件的主要来源。目前没有证据表明需要额外购买另一套 OPRA 数据。
2. 保留项目现有的 **yfinance 无 Key 降级链**，用于已完成的股票日线等基础行情。它是降级源，不应冒充实时全市场报价。
3. 如需补强 Regime：
   - 可申请 **Alpaca Basic 免费 Key**，当前代码只用它读取股票 bars、盘前活动和新闻，不用它读取期权流。
   - 可申请 **Finnhub 免费 Key**，优先用于近期财报日历。Finnhub 的全球经济日历现在是单独付费产品，免费 Key 不保证能读。
4. 如需网站里的 **GPT 复盘**，必须单独申请 OpenAI API Key 并配置复盘模型。ChatGPT Plus/Pro、Codex 登录或订阅都不能直接供本网站调用。
5. **Fed、BLS、FRED、FINRA、Massive 目前都只是候选数据源，尚未接入项目运行链路。** 现在即使把它们的 Key 写进 `.env`，页面也不会自动出现新数据。
6. 暂不建议购买 Finnhub Economic 或 Massive Options。应先明确 Moomoo 缺失的字段和历史范围、完成 connector 与验收用例，再决定是否付费。

### 当前能力与凭据类型

| 数据源 | 凭据/权限类型 | 当前是否接入 | 当前用途 | 现在是否建议办理 |
| --- | --- | --- | --- | --- |
| Moomoo OpenD / OPRA | Moomoo 账号、OpenD、本机登录及美股期权行情权限 | 是 | 期权概览、IV/OI、墙位、异动事件、行情 | 已可用则保持，不额外购买 |
| yfinance | 无 Key | 是 | 股票历史行情降级 | 已内置 |
| Alpaca | 免费账户 Key pair；完整 SIP/OPRA 为付费权益 | 是 | 股票 bars、盘前活动、新闻 | 可选免费增强 |
| Finnhub | 免费 Key；Economic Calendar 为付费数据集 | 是 | 财报日历、经济日历、评级趋势 | 免费 Key 可办，不建议现在购买 Economic |
| Federal Reserve | 公开网页，无 Key | 否 | 候选 FOMC 官方日历 | 不用申请 |
| BLS | v1 无 Key；注册后的 v2 Key 免费 | 否 | 候选 CPI/NFP/就业官方数据 | 先不申请也可以 |
| FRED | 免费账户 API Key | 否 | 候选利率、收益率、历史宏观序列 | connector 落地时再申请 |
| FINRA OTC Transparency | 公共网页免费；自动化 API 使用开发者中心/OAuth2 | 否 | 候选延迟 ATS/非 ATS 背景 | 先不申请 |
| Massive（原 Polygon.io） | 免费 Key；期权行情能力按套餐付费 | 否 | 候选 OPRA 历史/实时期权数据 | 暂不购买 |
| OpenAI API | 项目 API Key，API 单独计费 | 是 | GPT 模型增强复盘 | 需要 GPT 时办理 |

## 配置文件与安全规则

本地配置文件是仓库根目录下的 `.env`：

```text
<repo-root>/.env
```

遵守以下规则：

- 只把真实 Key 写入 `.env` 或 Web 设置页，不要写入本文、源码、截图、Issue 或日志。
- `.env` 已被 Git 忽略，但仍应在提交前运行 `git status --short`，确认没有生成其他含密钥文件。
- 不要把整个 `.env` 直接 `source` 到 shell；文件里可能包含 shell 不兼容的值。项目会通过 `src.config.setup_env()` 加载。
- 修改 `.env` 后重启服务；正在运行的 Python 进程不会自动获得所有新环境变量。
- 冒烟测试只打印状态、数量和脱敏错误，不打印 Key。
- 如果使用 Web 设置页保存配置，不要同时在多处维护互相冲突的同名配置。
- “项目只读”是本仓库代码的行为保证，不代表供应商凭据天然只读。Moomoo 登录态和 Alpaca 账户 Key 可能被其他程序用于交易接口，因此只能保存在受控本机，不能交给不可信代码。

## 1. Moomoo OpenD / OPRA：当前主数据源

### 权限属于哪一类

Moomoo 不使用普通 REST API Key。它需要：

- Moomoo 账号；
- 本机运行并登录的 OpenD；
- 首次登录后的 API 问卷与协议确认；
- 对应市场和品种的行情权限；
- 本项目安装 `moomoo-api` Python SDK。

官方入口：

- [Moomoo OpenD 介绍](https://openapi.moomoo.com/moomoo-api-doc/en/opend/opend-intro.html)
- [可视化 OpenD 安装与配置](https://openapi.moomoo.com/moomoo-api-doc/quick/opend-base.html)
- [权限与额度](https://openapi.moomoo.com/moomoo-api-doc/intro/authority.html)
- [行情接口总览](https://openapi.moomoo.com/moomoo-api-doc/en/quote/overview.html)
- [期权链接口说明](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html)
- [行情权限常见问题](https://openapi.moomoo.com/moomoo-api-doc/en/qa/quote.html)

官方权限页当前说明：美股期权 LV1/OPRA 权限可能由账户资产门槛获得，也可能需要购买行情卡；平台可调整推广、门槛和价格。**以 OpenD 实际返回和 Moomoo 产品页为准，不要根据旧教程直接购买。**

本项目已经实测期权 overview/events 可用，因此当前默认结论是：现有账号权限足够继续开发和日常使用。

### 配置步骤

1. 安装并打开 OpenD。
2. 在 OpenD 中登录 Moomoo，完成问卷和协议。
3. 确认监听地址为本机地址，默认端口为 `11111`。
4. 在项目 `.env` 写入：

```dotenv
MOOMOO_OPEND_ENABLED=true
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111
MOOMOO_PRIORITY=2

# SIMULATE 只读模拟账户历史；LIVE 只读真实账户历史。
# 这个变量不会给项目增加下单能力。
MOOMOO_TRADE_ENV=LIVE
```

如果只使用行情，不读取真实账户历史，`MOOMOO_TRADE_ENV` 可以保持 `SIMULATE`。无论设为何值，本仓库都不应调用交易解锁和订单接口。

OpenD 监听地址应保持 `127.0.0.1`。除非另有经过审计的网络隔离与鉴权，不要改成对局域网或公网开放的地址。

5. 重启本地服务。

### 三层冒烟测试

第一层只验证开关、SDK 和 TCP 连接，不消耗期权链查询额度：

```bash
curl -fsS http://127.0.0.1:8000/api/v1/system/moomoo-status \
  | python -m json.tool
```

期望重点字段：

```json
{
  "enabled": true,
  "sdk_installed": true,
  "connected": true,
  "read_only": true,
  "probe_level": "tcp"
}
```

`connected=true` 只证明 OpenD 端口可达，**不证明 OPRA 权限可用**。

第二层读取一只标的的期权概览，验证实际权限：

```bash
curl -fsS \
  -X POST http://127.0.0.1:8000/api/v1/opportunities/option-overview \
  -H 'Content-Type: application/json' \
  -d '{"symbols":["AAPL"]}' \
  | python -m json.tool
```

第三层读取 Moomoo 最近异动事件：

```bash
curl -fsS \
  -X POST http://127.0.0.1:8000/api/v1/opportunities/option-events \
  -H 'Content-Type: application/json' \
  -d '{"symbols":["AAPL"],"limit_per_symbol":5}' \
  | python -m json.tool
```

Regime 盘前 fallback 可用下面的只读调用单独验收。它只请求股票历史分钟线，不订阅、
不解锁交易，也不调用订单接口：

```bash
python - <<'PY'
from datetime import datetime, timezone
from src.config import setup_env
setup_env()

from data_provider.moomoo_fetcher import MoomooFetcher

result = MoomooFetcher().get_premarket("SPY", as_of=datetime.now(timezone.utc))
print({
    "status": result.get("_status"),
    "reason": result.get("_reason"),
    "source": result.get("_source"),
    "market_date": result.get("market_date"),
    "previous_close_date": result.get("previous_close_date"),
    "as_of": result.get("as_of"),
})
PY
```

该读取固定使用纽约 `04:00–09:30` 的已完成 1 分钟 bar、
`extended_time=True` 与 `AuType.NONE`。上一收盘必须来自 Moomoo bar 的
`last_close`，并绑定精确上一 XNYS session；超过 5 分钟、缺 `last_close` 或
非交易日都不会生成盘前涨跌。

该命令当前是适配器验收，不代表正式 Regime 已启用 Moomoo fallback。2026-07-24
本机真实 SPY 冷调用返回了正确 `2026-07-23 last_close` 与最后已完成盘前分钟，
但耗时约 32 秒；同步 SDK 调用无法由现有 Regime 工作预算安全取消。因此正式链路仍
保持 Alpaca-only。后续必须先落地盘前预取/last-good 缓存，或可安全终止且不会关闭
在途共享 QuoteContext 的 worker，才允许启用整篮子 fallback。

结果解释：

- `state=ready`：本次成功返回有效数据。
- `state=empty`：请求成功，但本次没有异动事件；这不是权限失败。
- `state=not_configured`：`MOOMOO_OPEND_ENABLED` 没有生效。
- `state=unavailable`：OpenD、权限、限频或标的数据不可用，需要结合 `message` 排查。
- `fetched_at`：本项目真正取得该响应的时间，不是所有字段的经济含义时间。

执行价墙请求更重，只在前两层成功后测试一次，不要连续刷新：

```bash
curl -fsS \
  -X POST http://127.0.0.1:8000/api/v1/opportunities/option-walls \
  -H 'Content-Type: application/json' \
  -d '{"symbols":["AAPL"],"dte_min":0,"dte_max":45}' \
  | python -m json.tool
```

机会页的 Top 5 会在一个请求内并发读取，但每个 underlying 都独占一条可复用的只读 QuoteContext lane，返回顺序与请求顺序一致；同一标的、同一 DTE 的重叠请求仍由服务端 30 秒 TTL/single-flight 合并。默认 0–45 DTE 每标最多拆成两个链日期窗口，Top 5 最多 10 次 `get_option_chain`；`option-wall/1.1` 同时从这批动态快照返回 `atm_call_iv`，因此 `/regime` 主机会榜不会再为选中标的额外调用 `/option-context`。独立 `option-context` endpoint 仍为其他消费者兼容保留。

并发只缩短独立标的的等待时间，不减少合约数，也不改变 Moomoo 的官方 [`get_market_snapshot`](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html) 额度：单次最多 400 个代码、30 秒最多 60 次。不要在多个页面用不同标的集合连续强制刷新；若任一批次触发限频，接口会保留真实 coverage 并将该标的标为部分覆盖，而不是以 0 或旧数据补齐。多个标签页同时冷刷新仍可能竞争 `get_option_chain` 的 10 次/30 秒额度；本次复用只消除正常单页自身的第 11 次请求，不是跨进程全局限流。

### Moomoo 常见问题

| 现象 | 更可能的原因 | 处理 |
| --- | --- | --- |
| TCP 不通 | OpenD 未启动、端口不一致、登录未完成 | 先看 OpenD 界面，再检查 HOST/PORT |
| SDK 未安装 | Python 环境里没有正确的 `moomoo-api` | 在运行服务所用的同一 Python 环境安装依赖 |
| 权限不足 | 没有美股期权 LV1/OPRA，或行情权限被其他终端抢占 | 在 OpenD/客户端核对权限；不要先猜测需要升级 |
| 返回空链 | 到期范围无合约、代码格式错误、非期权标的或供应商暂无结果 | 先用高流动性标的和 0–45 DTE 测试 |
| 间歇性失败 | Moomoo 接口限频或过多并发链查询 | 等待后重试；不要开多个页面反复点墙位 |
| App 有数据但 OpenD 没有 | App 权限和 API 权限不完全相同，或发生 market kicking | 以 OpenD 返回为准，关闭冲突终端或调整 OpenD 权限保持设置 |

## 2. Alpaca：免费股票盘前增强，不是当前期权流来源

### 权限与套餐

官方入口：

- [Market Data API 与当前套餐对比](https://docs.alpaca.markets/us/docs/about-market-data-api)
- [Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- [历史期权数据及 Indicative/OPRA 区别](https://docs.alpaca.markets/us/docs/historical-option-data)
- [Market Data 快速开始](https://docs.alpaca.markets/us/docs/getting-started-with-alpaca-market-data)

Alpaca Trading API 的 Basic 数据计划当前免费，但有明确边界：

- 股票实时数据只覆盖 IEX 单一交易所，不是全市场 SIP。
- 免费期权源是 Indicative Pricing Feed，不是真实 OPRA BBO；其交易数据为衍生值且延迟。
- 完整股票 SIP 和期权 OPRA 属于 Algo Trader Plus 权益。
- Basic 和付费套餐的价格、速率及符号限制以官方套餐页为准。

当前项目的 `data_provider/alpaca_fetcher.py` **只使用股票 bars、盘前 bars 和新闻**，没有调用 Alpaca 期权接口。因此购买 Alpaca OPRA 不会自动增强当前期权墙或异动流。

### 申请与配置

1. 在 [Alpaca](https://alpaca.markets/) 创建个人开发/交易账户。
2. 在 Dashboard 的 API Keys 区域生成 Key ID 和 Secret。
3. Secret 通常只完整显示一次，立即保存到密码管理器。
4. 在 `.env` 写入当前代码实际读取的准确变量名：

```dotenv
APCA_API_KEY_ID=<your-key-id>
APCA_API_SECRET_KEY=<your-secret>
```

不要使用 Alpaca MCP 文档中的 `ALPACA_API_KEY` 名称；本项目当前读取的是上面两个 `APCA_*` 变量。

这组账户 Key 本身不一定是供应商侧的“只读 Key”；当前项目之所以只读，是因为适配器只访问 `data.alpaca.markets`，不调用订单端点。不要把 Key 复用给其他脚本。

### 项目内冒烟测试

```bash
python - <<'PY'
from src.config import setup_env
setup_env()

from data_provider.alpaca_fetcher import AlpacaFetcher

client = AlpacaFetcher()
bars = client.get_bars("SPY", timeframe="1Day", limit=2)
print("configured:", client.configured)
print("bar_count:", len(bars))
if bars:
    print("latest_timestamp:", bars[-1].get("t"))
PY
```

判断：

- `configured=False`：至少一个变量未加载，或变量名错误。
- HTTP `401`：Key pair 缺失、无效、撤销，或 Key ID/Secret 不配对。
- HTTP `403`/`422` 且请求 `feed=sip` 或 `feed=opra`：通常是套餐不允许该 feed，不代表 Basic 的 IEX 数据也不可用。
- `configured=True` 但 `bar_count=0`：检查日期、市场时段、symbol 和网络。公开
  `get_bars()` 仍兼容地返回空列表，但适配器内部会把 HTTP 401/403、429、timeout
  与“请求成功但确实没有 bar”分开记录，Regime 不再把权限失败写成
  `no_completed_premarket_bar`。

### 对结果的限制

IEX 的成交量、价格和盘前活跃度只代表该数据覆盖，不得标注为“全美市场成交量”或“全市场资金流”。本项目也不得把 Alpaca Indicative 期权数据标成 OPRA 实时报价。

Regime 当前仍只调用 Alpaca，并对同轮 SPY/watchlist 请求冻结同一个 `as_of`。
Alpaca 失败时会保留准确的 permission/rate-limit/timeout/empty reason，并令 d6
保持未计分；不会以 Moomoo 数据静默补齐。Moomoo 整篮子 fallback 是下一阶段工作，
在延迟可控之前不得接入，也不得把尚未接线的 adapter 写成生产能力。

## 3. Finnhub：免费财报日历可用，经济日历可能需要付费

### 权限与套餐

官方入口：

- [免费 Key 注册](https://finnhub.io/register)
- [API 文档](https://finnhub.io/docs/api)
- [Earnings Calendar 文档](https://api2.finnhub.io/docs/api/stock-basic-dividends)
- [Global Economic Data / Economic Calendar 套餐](https://finnhub.io/pricing-economic-data-api)

当前代码读取一个变量：

```dotenv
FINNHUB_API_KEY=<your-finnhub-key>
```

`data_provider/finnhub_fetcher.py` 当前调用：

- `/calendar/earnings`
- `/calendar/economic`
- `/stock/recommendation`

官方 Earnings Calendar 页面当前标注免费层可取得近期历史和新更新；但 Global Economic Calendar 当前列在单独的 Economic-1 付费数据中。也就是说：

- 免费 Key 成功读取财报，不代表经济日历也有权限。
- 经济日历返回 `403` 不能直接诊断为 Key 错误。
- 不建议仅为当前网站购买 Economic-1；优先完成 Fed/BLS 官方数据 connector。

### 项目内冒烟测试

```bash
python - <<'PY'
from datetime import date, timedelta
from src.config import setup_env
setup_env()

from data_provider.finnhub_fetcher import FinnhubFetcher

client = FinnhubFetcher()
start = date.today()
end = start + timedelta(days=7)

earnings = client.get_earnings_calendar(start, end, symbol="AAPL")
print("configured:", client.configured)
print("earnings_request_ok:", client.request_succeeded("earnings_calendar"))
print("earnings_count:", len(earnings))
print("earnings_error:", client.last_request_error("earnings_calendar"))

economic = client.get_economic_calendar(start, end)
print("economic_request_ok:", client.request_succeeded("economic_calendar"))
print("economic_count:", len(economic))
print("economic_error:", client.last_request_error("economic_calendar"))
PY
```

解释：

- `request_ok=True, count=0`：请求成功，只是该窗口没有结果。
- `request_ok=False`：查看脱敏后的 `*_error`，区分认证、权限、限频和网络。
- 财报日期和 `bmo/amc/dmh` 状态可能被公司调整；冻结研究快照必须保存本次 `fetched_at`，不能用后来更新的日期改写旧信号。

## 4. Federal Reserve：无 Key 的官方 FOMC 日历候选

[Federal Reserve FOMC 官方页面](https://www.federalreserve.gov/monetarypolicy/fomc.htm) 公开列出会议日历、声明、纪要和发布材料，不需要 API Key。

当前项目没有 Fed calendar connector，也没有对应环境变量。**现在无需在 `.env` 添加任何 FED 变量，添加也不会生效。**

在 connector 完成前，可以人工以该页面核对：

- 会议日期；
- 声明发布时间；
- 记者会安排；
- 已发布的声明和纪要。

不可从“今天是 FOMC 日”直接推出涨跌方向、波动幅度或某只期权必然获利。它只能作为事件风险标记。

## 5. BLS：官方 CPI/NFP/就业数据候选

官方入口：

- [BLS API Getting Started](https://www.bls.gov/developers/home.htm)
- [BLS v2 请求格式](https://www.bls.gov/developers/api_signature_v2.htm)
- [BLS API FAQ](https://www.bls.gov/developers/api_faqs.htm)
- [BLS Release Calendar](https://www.bls.gov/schedule/)

权限分层：

- v1：无注册 Key，查询范围和频率较低。
- v2：免费注册 Key，可提高查询范围、频率并使用更多参数。
- BLS 数据是官方已发布时间序列，不是实时市场行情。

当前项目没有 BLS connector，也没有任何会被读取的 `BLS_*` 环境变量。未来可能采用 `BLS_REGISTRATION_KEY`，但在代码正式加入前，这只是建议命名，**现在写入 `.env` 不会生效**。

### 无 Key 直连冒烟

下面只验证 BLS 公共服务可达，不会改变项目：

```bash
curl -fsS \
  https://api.bls.gov/publicAPI/v1/timeseries/data/CUSR0000SA0 \
  | python -m json.tool
```

可作为未来 connector 验收样本的系列包括：

- `CUSR0000SA0`：CPI-U All items，季调；
- `CES0000000001`：Total nonfarm employees，季调；
- `LNS14000000`：失业率，季调。

使用前仍需在 BLS 官方 Series Finder 核对定义、单位、季调口径和发布日期。

### 不可忽略的 as-of 问题

BLS 数据可能有初值、修订值和基准修订。回测时不能拿今天看到的修订后数据，假装交易当日已经知道。未来 connector 必须保存：

- release timestamp；
- series ID；
- period；
- value；
- preliminary/revised 标记；
- 本次抓取时间。

## 6. FRED：利率与宏观历史序列候选

官方入口：

- [FRED API Overview](https://fred.stlouisfed.org/docs/api/fred/overview.html)
- [FRED API Key 说明](https://fred.stlouisfed.org/docs/api/fred/v2/api_key.html)
- [Series Observations 接口](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [FRED API Terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html)

FRED API Key 可免费申请，但官方 API 请求需要 Key。当前项目没有 FRED connector，也没有会被读取的 `FRED_API_KEY`。因此：

- 现在申请 Key 可以用于人工验证；
- 把 `FRED_API_KEY` 写进 `.env` 目前不会改变网站；
- 等 connector 落地时再将它加入 `.env.example` 和设置页。

### 不落盘的凭据冒烟

这段脚本用隐藏输入读取 Key，只打印状态和最新观测日期，不把 Key 写进仓库：

```bash
python - <<'PY'
from getpass import getpass
import requests

key = getpass("FRED API key: ")
response = requests.get(
    "https://api.stlouisfed.org/fred/series/observations",
    params={
        "series_id": "DGS10",
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1,
    },
    timeout=10,
)
print("status_code:", response.status_code)
if response.ok:
    rows = response.json().get("observations", [])
    print("observation_count:", len(rows))
    if rows:
        print("latest_date:", rows[0].get("date"))
else:
    print("error_type:", response.reason)
PY
```

未来可能使用的序列包括 `DGS2`、`DGS10` 等，但必须逐项核对更新频率、缺失值和版权说明。

普通 FRED observations 可能包含后来修订的数据。需要点时回测时应使用 ALFRED/vintage 信息或保存当时响应，不能把最新修订值作为历史时点事实。

## 7. FINRA：免费但延迟的 OTC/ATS 背景，不是“今日暗池”

官方入口：

- [FINRA OTC Transparency](https://www.finra.org/filing-reporting/otc-transparency)
- [FINRA 数据目录](https://www.finra.org/finra-data/browse-catalog)
- [FINRA OTC Transparency Data API](https://www.finra.org/filing-reporting/otc-transparency/otc-transparency-data-api)
- [FINRA Developer Center Query API](https://developer.finra.org/products/query-api)
- [FINRA 发布延迟说明](https://www.finra.org/filing-reporting/otc-transparency/technical-notices/technical-notices/finra-ats-data-changes-effective-monday-april-25-2016)

权限与费用：

- FINRA 数据目录将 OTC Transparency 标为 Public / No Fee。
- 网页可人工查看公开汇总。
- 自动化 Query API 走 Developer Center，并采用 OAuth2；可能需要创建个人账号或由机构管理员授权 API Console。
- 当前项目没有 FINRA connector，也没有 `FINRA_CLIENT_ID`、`FINRA_CLIENT_SECRET` 等运行时变量。现在填入任何 FINRA 凭据都不会生效。

### 数据时间边界

FINRA 官方发布机制是延迟汇总：

- Tier 1 NMS 股票的周度信息约延迟两周；
- 其他 NMS 股票和 OTC Equity Securities 的周度信息约延迟四周；
- 部分月度汇总约延迟一个月。

因此 FINRA 数据只能回答类似“过去某周，某标的在 ATS/非 ATS 的汇总成交占比如何”，不能证明：

- 今天刚出现暗池买入；
- 某笔成交是机构开仓；
- 买方或卖方方向；
- 期权 dealer 的仓位或 Gamma 方向；
- 明天一定上涨或下跌。

它是慢速背景特征，不应进入当日 09:12 ET 信号的实时证据时钟。

## 8. Massive（原 Polygon.io）：高质量候选，但尚未接入

官方入口：

- [Massive REST 快速开始与认证](https://massive.com/docs/rest)
- [Options API 套餐](https://massive.com/options)
- [Options REST 接口总览](https://massive.com/docs/rest/options/overview)
- [Option Chain Snapshot](https://massive.com/docs/rest/options/snapshots/option-contract-snapshot)
- [Options 数据来源与专业/非专业分类](https://massive.com/knowledge-base/categories/options)
- [官方 Python Client](https://github.com/massive-com/client-python)

Massive 是 Polygon.io 在 2025 年后的品牌名；官方客户端说明旧 Key 和旧域名仍有兼容期，但新集成应使用 Massive 名称和 `api.massive.com`。

官方个人期权套餐在核对日列出：

| 套餐 | 当前页面价格 | 主要边界 |
| --- | ---: | --- |
| Options Basic | 免费 | 以套餐页列出的参考能力为准 |
| Options Starter | 29 美元/月 | 15 分钟延迟，聚合、IV/Greeks、日 OI、snapshot 等 |
| Options Developer | 79 美元/月 | 15 分钟延迟，增加更长历史与 trades |
| Options Advanced | 199 美元/月 | 实时数据，trades 与 quotes |

价格和字段会变化；购买前重新核对官方页面及个人/专业用户分类。

### 当前项目状态

项目目前：

- 没有 Massive client；
- 没有调用 Massive REST/WebSocket；
- 没有读取 `MASSIVE_API_KEY`；
- 也没有读取旧名 `POLYGON_API_KEY`。

因此现在购买套餐或把 Key 加入 `.env`，页面不会自动增强。未来 connector 建议采用官方当前名 `MASSIVE_API_KEY`，但必须在代码、`.env.example`、设置页和测试一起落地后才算有效配置。

### 不落盘的免费 Key 冒烟

此测试只验证 Key 和免费参考端点，不证明你拥有链快照、实时 OPRA、trades 或 quotes 权限：

```bash
python - <<'PY'
from getpass import getpass
import requests

key = getpass("Massive API key: ")
response = requests.get(
    "https://api.massive.com/v3/reference/options/contracts",
    headers={"Authorization": f"Bearer {key}"},
    params={"underlying_ticker": "AAPL", "limit": 1},
    timeout=10,
)
print("status_code:", response.status_code)
if response.ok:
    payload = response.json()
    print("status:", payload.get("status"))
    print("result_count:", len(payload.get("results", [])))
else:
    print("error_type:", response.reason)
PY
```

`401` 通常是 Key 无效；`403` 更常表示端点或 feed 不在当前套餐；成功但缺少 `last_quote`、`last_trade` 或 Greeks，也可能是套餐字段裁剪，不应填默认值冒充。

### 什么时候才值得购买

只有至少满足一项，才进入付费评估：

- Moomoo 的历史深度无法支持已定义的回测；
- 需要可审计的 OPRA 历史 quotes/trades 和 NBBO 对齐；
- 需要覆盖大量合约的稳定批量接口或 WebSocket；
- 需要明确 SLA、长期历史或大规模 flat files。

购买前先做一个 connector spike，用免费 Key 验证认证、schema、分页、限频、时间戳和降级，再用一个月套餐验收 20 个真实案例。未通过验收不要续费。

即使拿到完整 OPRA trades/quotes，也不能只凭“成交在 ask 附近”就断言开仓、平仓、买方身份、dealer 仓位或最终交易意图。

## 9. OpenAI：网站使用 GPT 必须单独配置 API

官方入口：

- [ChatGPT 与 API 分开计费](https://help.openai.com/en/articles/8156019)
- [API 与 ChatGPT Billing 分离](https://help.openai.com/en/articles/9039756)
- [创建和管理 API Key](https://help.openai.com/en/articles/4936850)
- [API Key 安全建议](https://help.openai.com/en/articles/5008148)
- [OpenAI API 平台](https://platform.openai.com/)

### 最重要的区别

- ChatGPT Plus/Pro/Business 订阅不是 API 额度。
- Codex 当前登录状态不是可供本地网站调用的 API 凭据。
- 本地网站不能“直接接这个聊天里的 GPT”。
- OpenAI API 在 Platform 独立创建项目、Key 和 Billing，按 API 使用量计费。

### 申请步骤

1. 登录 [OpenAI API 平台](https://platform.openai.com/)。
2. 创建一个只用于此项目的 Project。
3. 在 API Billing 中添加支付方式或预付额度，并设置合理的项目预算/告警。
4. 在该 Project 创建独立 API Key；完整 Secret 只保存到密码管理器和 `.env`。
5. 在项目可用模型页面选择该 Project 确实有权调用的模型 ID，不要从旧教程复制一个名称。

### 推荐的简单模式

如果没有启用 `LLM_CHANNELS`，在 `.env` 写：

```dotenv
OPENAI_API_KEY=<your-project-api-key>

# 把占位符替换成该 Project 实际可用的模型 ID。
JOURNAL_AI_MODEL=openai/<model-id-from-your-openai-project>

# 可选：显式留空表示复盘主模型失败后不调用其他供应商。
JOURNAL_AI_FALLBACK_MODELS=
```

官方 OpenAI API 不需要设置 `OPENAI_BASE_URL`；留空即可。

如果项目已经使用多渠道模式，顶层 `OPENAI_API_KEY` 会被渠道配置覆盖。此时保留现有渠道列表，并配置对应 OpenAI 渠道：

```dotenv
# 例：不要覆盖已有渠道；把 openai 加入现有逗号列表。
LLM_CHANNELS=openai
LLM_OPENAI_PROTOCOL=openai
LLM_OPENAI_BASE_URL=https://api.openai.com/v1
LLM_OPENAI_API_KEY=<your-project-api-key>
LLM_OPENAI_MODELS=<model-id-from-your-openai-project>
LLM_OPENAI_ENABLED=true

JOURNAL_AI_MODEL=openai/<model-id-from-your-openai-project>
```

简单模式和渠道模式选一种，不要留下两套互相矛盾的值。若已有 Gemini 等渠道，`LLM_CHANNELS` 应保留它们，只追加 `openai`。

### 认证与项目配置冒烟

先确认项目实际解析到了复盘模型：

```bash
python - <<'PY'
from src.config import (
    get_config,
    get_effective_journal_ai_models_to_try,
    setup_env,
)

setup_env()
config = get_config()
models = get_effective_journal_ai_models_to_try(config)
print("journal_model_count:", len(models))
print("journal_models:", models)
PY
```

再做只读认证测试，不发送交易数据：

```bash
python - <<'PY'
import os
import requests
from src.config import setup_env

setup_env()
key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_OPENAI_API_KEY")
if not key:
    raise SystemExit("OpenAI API key is not loaded")

response = requests.get(
    "https://api.openai.com/v1/models",
    headers={"Authorization": f"Bearer {key}"},
    timeout=15,
)
print("status_code:", response.status_code)
if response.ok:
    print("authentication: ok")
else:
    print("error_type:", response.reason)
PY
```

最后在网站设置页使用“测试渠道”，再打开一个真实 PositionEpisode 点击模型增强。只有真实复盘返回明确的模型成功状态，才算全链可用。

常见问题：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `401` | Key 错误、已撤销、读到了另一套配置 | 轮换 Project Key，核对简单/渠道模式 |
| `404 model not found` | 模型 ID 错误或该 Project 无权限 | 从当前 Project 的可用模型中选择 |
| `429` | 请求速率或账户额度/预算耗尽 | 查看 API Usage/Billing；不要误认为 ChatGPT 订阅会补额度 |
| 网站仍用 Gemini | `JOURNAL_AI_MODEL` 未显式指定，或配置没有重启 | 设置复盘模型并重启服务 |
| 直接认证成功但复盘失败 | 模型不支持请求、超时、上下文过长或 LiteLLM 路由错误 | 用设置页渠道测试和服务端脱敏日志定位 |

模型输出只能解释提供给它的证据，不能补造缺失的行情、成本、开平仓、胜率或概率。

## 10. 统一排障：401、403、空结果和限频

### 先分清四类失败

| 表现 | 含义 | 首要检查 |
| --- | --- | --- |
| HTTP `401` | 身份认证失败 | Key 是否加载、是否撤销、Key pair 是否匹配 |
| HTTP `403` | 身份可能有效，但无资源/套餐权限 | OPRA/SIP/Economic/endpoint entitlement |
| HTTP `200` + 空数组 | 请求可能完全成功，只是该窗口无数据 | 日期、时区、市场时段、symbol、DTE、流动性 |
| HTTP `429` | 速率、并发或计费额度限制 | 降低并发、缓存、重试退避、套餐额度 |

Moomoo 不是 HTTP API，权限和限频通常在 SDK 的 `ret/msg` 中体现；不要强行套用 HTTP 状态码。

### 推荐排障顺序

1. 查看本地状态接口，确认开关和连接。
2. 用本文最小单标的 smoke test，避免整池扫描掩盖根因。
3. 查看响应里的 `state`、`message`、`coverage`、`fetched_at` 和 provider error。
4. 到供应商 Dashboard 核对 Key 状态、计划和当前用量。
5. 确认请求的 feed、端点和时间范围属于该计划。
6. 修改 `.env` 后重启服务，再测试一次。
7. 只有单标的成功后，才恢复 Top 5 或批量扫描。

### 空结果不等于零

以下值必须区分：

- `null` / 字段缺失：没有证据；
- `[]` 且请求成功：该范围没有观测；
- `0`：供应商明确返回数值零；
- `unavailable`：请求或权限失败；
- `partial`：有部分观测，但覆盖不足；
- `stale`：数据存在，但不满足当前时效。

页面和 AI 都不得把前四种情况互相替换。

## 11. as-of、来源与不可推断边界

每条研究证据至少要保存：

- `source`：供应商或官方机构；
- `fetched_at`：系统获取时间，使用带时区的 ISO 时间；
- `market_date` / `session`：数据所属交易日和时段；
- `data_as_of`：供应商声明的观测时间；
- `scope`：标的、到期范围、合约集合和过滤条件；
- `coverage`：请求数、成功数、缺失数；
- `limitations`：延迟、套餐、模型和推断限制。

### 各类数据能证明什么

| 数据 | 可以描述 | 不能单独证明 |
| --- | --- | --- |
| Moomoo session volume | 抓取时当前交易时段的累计活动 | 开仓、平仓、看多、看空 |
| Open Interest | 上一清算周期后的未平仓存量 | 当日新开仓、dealer 方向 |
| IV / IV Rank / Greeks | 供应商在该时点的模型/统计值 | 标的涨跌方向、必然波动幅度 |
| OI/Volume 墙 | 所选 DTE/合约中的集中执行价 | 保证支撑阻力、真实 dealer GEX 或 gamma flip |
| Moomoo option events | Moomoo 分类的最近异动事件页 | 完整 OPRA 流、主动买卖方、开平仓 |
| Alpaca IEX bars | IEX 覆盖下的股票价格和成交 | 全市场 SIP 成交量 |
| Finnhub earnings | 本次抓取时的预定/历史财报信息 | 公司之后不会改期 |
| Fed/BLS/FRED | 官方日历或已发布宏观观测 | 未来结果、市场反应方向 |
| FINRA OTC/ATS | 延迟的场外汇总背景 | 今日暗池成交、买卖方向 |
| Massive OPRA 数据 | 套餐范围内的行情、历史或衍生指标 | 参与者身份、开平仓和交易意图 |
| LLM 复盘 | 对已提供证据的结构化解释 | 补造缺失事实、可靠胜率或无样本概率 |

历史研究必须使用当时冻结的证据。后来修订的宏观数据、重新计算的 IV、补全的行情或新模型结论，都应作为新版本附加，不能覆盖旧快照。

## 12. 给当前项目的办理清单

### 现在做

- [ ] 保持 OpenD 登录，运行 Moomoo 三层 smoke test。
- [ ] 确认期权 overview/events 为 `ready` 或成功的 `empty`，再决定是否测试墙位。
- [ ] 如 Regime 需要免费增强，申请 Alpaca Basic Key pair。
- [ ] 申请 Finnhub 免费 Key，只把近期财报作为确定可期待的免费能力。
- [ ] 如必须使用 GPT，创建 OpenAI API Project、Billing 和独立 Key，配置 `JOURNAL_AI_MODEL`。
- [ ] 重启服务，用真实页面和单标的 API 验证。

### 现在不要做

- [ ] 不要因为 Finnhub economic 返回 `403` 就立即购买 Economic-1。
- [ ] 不要购买 Massive Options 来“试试看”；当前代码不会读取它。
- [ ] 不要把 FINRA 延迟汇总命名为“今日暗池”。
- [ ] 不要把 ChatGPT/Codex 订阅当成 OpenAI API Key。
- [ ] 不要把 `empty`、`unavailable` 或 `null` 显示成数值零。

### 等 connector 落地后再做

- [ ] Fed FOMC 官方日历接入与发布日期冻结。
- [ ] BLS CPI/NFP/就业 release 与 revision 账本。
- [ ] FRED/ALFRED 利率序列和 point-in-time vintage。
- [ ] FINRA 延迟 ATS/非 ATS 背景特征。
- [ ] Massive 与 Moomoo 的字段、覆盖、成本和历史深度对照试验。

任何新数据源进入正式 Top 5 排名之前，都必须先完成：schema、as-of、覆盖率、超时、重试、缓存、401/403/429、空结果、降级、冻结快照和至少 20 个真实案例的人工核对。
