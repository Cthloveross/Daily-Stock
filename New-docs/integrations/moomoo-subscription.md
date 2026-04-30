# Moomoo OpenAPI 实时订阅速查

> 来源：[官方 quote/sub.html](https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html) + 本地 skill `~/.claude/skills/moomooapi/scripts/subscribe/`。
> 目标：把 Moomoo 实时行情订阅的"怎么用"压成一份能直接抄的中文 cheatsheet。

---

## 一、核心模型（必须先理解）

订阅是 **client → OpenD daemon → Moomoo 后端** 三段：

1. 你的 Python 进程调 `quote_ctx.subscribe([codes], [subtypes])` → OpenD 转发到 Moomoo 后端
2. 后端把对应行情持续推回 OpenD，OpenD 推回你的进程
3. 你提前 `set_handler(handler)` 注册的 Handler 在另一线程里被调

**配额** 不是按"股票数"算，而是 **每只股票 × 每个 subtype = 一格配额**。`AAPL` 同时订 `QUOTE + KLINE_1M + ORDER_BOOK` = 占 3 格。配额上限 100 / 500 / 2000，看你 Moomoo 等级。

---

## 二、`subscribe()` 函数签名

```python
quote_ctx.subscribe(
    code_list,                       # list[str]，如 ['US.AAPL', 'HK.00700']
    subtype_list,                    # list[SubType]
    is_first_push=True,              # 订阅成功立即推一次缓存的最近数据
    subscribe_push=True,             # True = 走 Handler 推；False = 仅 get_*() 主动拉
    is_detailed_orderbook=False,     # 仅 HK SF 权限的详细盘口
    extended_time=False,             # 仅 US 股，是否包含盘前盘后
    session=Session.NONE,            # 仅 US，分时段：NONE / RTH / ETH / ALL（OVERNIGHT 不支持）
)
# 返回 (RET_OK, None) 或 (RET_ERROR, '错误描述')
```

`unsubscribe(code_list, subtype_list)` 同样形参；`unsubscribe_all()` 一把清空。

> ⚠️ 订阅后必须等 **≥ 1 分钟** 才能 `unsubscribe`，否则报错。

---

## 三、`SubType` 枚举与对应 Handler

| SubType | 推送内容 | 对应 Handler 基类 | Skill 脚本 |
|---|---|---|---|
| `QUOTE` | 当前价、OHLC、成交量、成交额（基础快照） | `StockQuoteHandlerBase` | `push_quote.py` |
| `ORDER_BOOK` | 买卖档位（价、量、笔数） | `OrderBookHandlerBase` | `push_orderbook.py` |
| `TICKER` | 逐笔成交（带时间戳、买卖方向） | `TickerHandlerBase` | `push_ticker.py` |
| `RT_DATA` | 分时数据（intraday 时分图） | `RTDataHandlerBase` | `push_rt_data.py` |
| `BROKER` | 经纪席位队列（HK 专用） | `BrokerHandlerBase` | `push_broker.py` |
| `K_1M` / `K_3M` / `K_5M` / `K_15M` / `K_30M` / `K_60M` / `K_DAY` / `K_WEEK` / `K_MON` / `K_QUARTER` / `K_YEAR` | K 线（指定周期 OHLC + 实时更新最新一根） | `CurKlineHandlerBase` | `push_kline.py` |

**权限差异**：
- HK 股 BMP 权限不支持任何订阅；要用得至少 LV1
- US 股盘前盘后 + 分时段（RTH/ETH/ALL）需要 LV1+
- HK 期权/期货 LV1 权限不支持 TICKER
- HK SF 权限的"详细盘口"（`is_detailed_orderbook=True`）只能同时订 50 只

---

## 四、配额管理

### 查剩余配额
```python
ret, data = quote_ctx.query_subscription()
# data = {
#   'total_used': 3,       # 全部已用格数
#   'remain': 97,          # 剩余可用
#   'own_used': 3,         # 当前进程占用
#   'sub_list': {
#       'US.AAPL': ['QUOTE', 'KLINE_1M'],
#       'HK.00700': ['QUOTE'],
#   }
# }
```

### 释放配额
- `unsubscribe(['US.AAPL'], [SubType.QUOTE])` —— 只释放该 ticker 的 QUOTE 一格
- `unsubscribe_all()` —— 全清，**1 分钟冷却仍生效**
- 进程退出 / OpenD 重启 → 配额自动归还
- **断线重连**：连接断掉再恢复时，OpenD 会重新订阅之前的列表；如果 `is_first_push=True`，缓存数据会再推一次

---

## 五、Handler 注册与回调

每种 subtype 对应一个 HandlerBase 子类，**必须 `set_handler` 才会收到推送**。一个 quote_ctx 可以挂多个 handler（每种基类一个）。

```python
from moomoo import StockQuoteHandlerBase, RET_OK, RET_ERROR

class QuoteHandler(StockQuoteHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        # 父类先做 protobuf 解析
        ret_code, data = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            return RET_ERROR, data
        # data 是 pandas.DataFrame，列名见每个 subtype 文档
        print(data)
        return RET_OK, data

quote_ctx.set_handler(QuoteHandler())
```

**回调线程独立**：handler 不要做长 IO / 阻塞，否则下一条推送会堆积。建议把 `data` 丢到一个 queue，主线程消费。

---

## 六、完整示例：AAPL QUOTE + KLINE_1M

```python
import time
from moomoo import (
    OpenQuoteContext, SubType, Session, RET_OK, RET_ERROR,
    StockQuoteHandlerBase, CurKlineHandlerBase,
)

class QuoteH(StockQuoteHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret == RET_OK:
            print('QUOTE', data[['code', 'last_price', 'volume']].to_string(index=False))
        return ret, data

class KlineH(CurKlineHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret == RET_OK:
            print('KLINE', data[['code', 'time_key', 'close', 'volume']].tail(1).to_string(index=False))
        return ret, data

ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ctx.set_handler(QuoteH())
ctx.set_handler(KlineH())

ret, msg = ctx.subscribe(
    ['US.AAPL'],
    [SubType.QUOTE, SubType.K_1M],
    subscribe_push=True,
    extended_time=True,
    session=Session.ALL,
)
if ret != RET_OK:
    raise RuntimeError(f'subscribe failed: {msg}')

try:
    time.sleep(70)  # ≥ 60s 才能合法 unsubscribe
finally:
    ctx.unsubscribe(['US.AAPL'], [SubType.QUOTE, SubType.K_1M])
    ctx.close()
```

---

## 七、常见错误一览

| 现象 | 原因 / 解决 |
|---|---|
| `Permission denied` | BMP 权限不让订阅；升 LV1+ 或换 ticker |
| `Out of subscription quota` | 配额用完；先 `query_subscription` 看 `own_used`，再 `unsubscribe` 不要的 |
| `Unsubscribe rejected: too soon after subscribe` | < 60s 就调 unsubscribe；睡 1 分钟再调 |
| `extended_time/session only valid for US` | 把这两个参数从 HK / CN 订阅里删掉 |
| `OVERNIGHT not supported` | session 用 `RTH` / `ETH` / `ALL`，不要 `OVERNIGHT` |
| Handler 没回调 | 忘了 `set_handler`，或 handler 类型不匹配（订 KLINE 用了 QuoteHandler） |
| `Connection lost` 后没数据 | OpenD 重连后会自动 re-subscribe，但你的 handler 状态需要自己处理首推重复 |

---

## 八、本地 skill 直跑

每个 push_*.py 都能命令行跑：

```bash
cd ~/.claude/skills/moomooapi/scripts/subscribe

# 订一只 60 秒 quote
python push_quote.py US.AAPL --duration 60

# 订 K 线 1m，5 分钟
python push_kline.py US.AAPL --ktype K_1M --duration 300

# 订盘口
python push_orderbook.py US.AAPL --duration 60

# 多类型一次订（subscribe.py，仅订不收推）
python subscribe.py US.AAPL --types QUOTE ORDER_BOOK KLINE_1M --session ALL

# 看当前订阅状态
python query_subscription.py

# 全清
python unsubscribe_all.py
```

---

## 九、要在 daily_stock_analysis 里用 → 怎么接

**最小集成路径**：在 `data_provider/moomoo_fetcher.py` 加一个 fetcher，模仿现有 `data_provider/yfinance_fetcher.py` 的形态：

- `fetch_intraday(code, interval, days)` —— 走 `OpenQuoteContext.get_cur_kline`（已订阅后才能拉）
- `fetch_quote(code)` —— `OpenQuoteContext.get_market_snapshot`
- daemon 启动 + 心跳 + 配额管理放在一个 module-level singleton，避免多次实例化撞配额

`.env` 加：
```bash
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111
MOOMOO_OPEND_ENABLED=false  # default off; turn on after OpenD logged in
```

**不要** 把 OpenD 启动放进 main.py 主流程；OpenD 必须用户手动登录 Moomoo 账号才能拉数据，自动启不可靠。

---

## 十、安全与合规

- 订阅 ≠ 下单。这一页只覆盖只读行情，无任何账户/资金风险。
- 但 OpenD daemon 一旦登录后，**所有挂在 11111 端口上的 Python 进程都能调下单接口** — 不要在不受信的代码里 import `moomoo` 后调 `place_order`。
- 真要做单走 `TrdEnv.SIMULATE`（paper trading）默认；production 模式必须显式 opt-in。

---

_本文档由 [moomooapi skill](file:///Users/cth/.claude/skills/moomooapi/SKILL.md) v0.1.1 + [官方文档](https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html) 整理。_
