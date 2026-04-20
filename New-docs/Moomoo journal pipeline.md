# Moomoo 交易日志 Pipeline 详细实施文档

> **模块**：交易复盘引擎（Journal）  
> **状态**：M1 核心功能  
> **依赖**：无外部 API 订阅，完全基于 Moomoo CSV 导出 + Gmail IMAP  
> **维护者**：[@Cthloveross](https://github.com/Cthloveross)

---

## 目录

- [一、整体架构](#一整体架构)
- [二、Moomoo CSV 导出设置](#二moomoo-csv-导出设置)
- [三、Gmail IMAP 邮箱配置](#三gmail-imap-邮箱配置)
- [四、数据库 Schema](#四数据库-schema)
- [五、IMAP 轮询服务](#五imap-轮询服务)
- [六、CSV 解析器](#六csv-解析器)
- [七、交易配对算法](#七交易配对算法)
- [八、AI 单笔分析 Prompt](#八ai-单笔分析-prompt)
- [九、AI 月度复盘 Prompt](#九ai-月度复盘-prompt)
- [十、FastAPI 端点](#十fastapi-端点)
- [十一、React 前端页面](#十一react-前端页面)
- [十二、调度与 CI](#十二调度与-ci)
- [十三、幂等性与错误处理](#十三幂等性与错误处理)
- [十四、测试清单](#十四测试清单)
- [十五、部署 checklist](#十五部署-checklist)

---

## 一、整体架构

```
┌──────────────┐
│  Moomoo App  │  ① 用户手动触发导出（或自动每日）
│  (iOS/Mac)   │
└──────┬───────┘
       │ CSV 附件发到邮箱
       ▼
┌──────────────┐
│   Gmail      │  ② 邮件带 CSV 附件落在指定文件夹
│  (专用文件夹) │
└──────┬───────┘
       │ IMAP 连接
       ▼
┌──────────────────────┐
│ Python IMAP Poller   │  ③ GitHub Actions cron 每小时跑一次
│ (src/journal/imap_poll.py) │     或用户手动触发
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  CSV Parser +        │  ④ 解析 → 标准化 → 去重 → 入库
│  Trade Matcher       │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  SQLite / Postgres   │  ⑤ trades / trade_legs / reviews
└──────┬───────────────┘
       │
       ├─────────────────────┐
       ▼                     ▼
┌──────────────┐   ┌──────────────────┐
│ AI 单笔分析  │   │ AI 月度复盘     │
│ (每笔入库触发)│   │ (每月 1 号 cron)│
└──────┬───────┘   └──────┬───────────┘
       │                  │
       ▼                  ▼
┌──────────────────────────────┐
│  FastAPI /api/v1/journal/*  │
└──────┬───────────────────────┘
       │
       ▼
┌──────────────┐
│ React UI     │
│ /journal     │
└──────────────┘
```

**核心设计原则**：
- **幂等**：同一封邮件/同一张 CSV/同一条交易记录，重复处理不产生重复数据
- **最终一致**：IMAP 抓取失败不阻塞，下一轮重试
- **AI 分析异步**：入库和 AI 分析解耦，AI 失败不影响交易记录入库

---

## 二、Moomoo CSV 导出设置

### 2.1 Moomoo App 导出路径（US 账户）

> ⚠️ 用户需亲自在 App 中确认菜单路径，因为版本更新会变动。以下是当前（2026-04）版本的路径。

**iOS / Android**：
1. 打开 Moomoo App → 底栏 **Accounts**（账户）
2. 选择 US 账户 → **Statements**（对账单）或 **History**（历史）
3. 选择 **Trade History**（交易记录）
4. 右上角菜单 → **Export** 或 **Email**
5. 选日期范围（建议"本月"或"近 90 天"）
6. 填入**专用接收邮箱**（见下节）
7. 提交，CSV 会以邮件附件发到该邮箱

**Mac/Windows 桌面端**：
1. 顶部菜单 **Account** → **Statements**
2. 选 Trade History → Export → CSV
3. 保存到本地后手动转发到接收邮箱（或通过 Gmail 的 "Send mail as" 功能自动）

### 2.2 Moomoo CSV 字段预览（示例，用户确认为准）

Moomoo US 交易记录 CSV 常见字段（**用户需自己导出一份样本确认实际字段名**）：

```csv
Trade Date,Settled Date,Account,Type,Symbol,Name,Side,Quantity,Price,Commission,Fee,Amount,Currency,Status
2026-04-15,2026-04-17,U1234567,Stock,NVDA,NVIDIA Corp,Buy,100,182.34,0.00,0.12,-18234.12,USD,Filled
2026-04-15,2026-04-17,U1234567,Stock,NVDA,NVIDIA Corp,Sell,100,185.20,0.00,0.15,18519.85,USD,Filled
```

**字段映射规划**（等用户提供真实样本后微调）：

| CSV 字段 | 内部字段 | 说明 |
|--|--|--|
| Trade Date | `executed_at` | 解析为带时区的 datetime |
| Symbol | `symbol` | 标准化为大写 |
| Side | `side` | `Buy` → `long_open` / `short_close`，`Sell` → `long_close` / `short_open`（需配合持仓方向判断） |
| Quantity | `quantity` | int |
| Price | `price` | decimal(10,4) |
| Commission | `commission` | decimal |
| Fee | `fee` | decimal |
| Amount | `gross_amount` | decimal（含正负） |
| Status | — | 只处理 `Filled` 行 |

### 2.3 建议的导出频率

- **日内频繁交易时**：每日盘后（16:05 ET）手动导出 "今日"
- **轻度使用**：每周五收盘后导出 "本周"
- **月度复盘**：每月 1 号导出上月完整记录，覆盖前面可能漏的

**不建议"每次交易都导出"**—噪音太大且 Moomoo 对频繁导出可能限频。

---

## 三、Gmail IMAP 邮箱配置

### 3.1 建议的邮箱结构

**推荐方案**：在现有 Gmail 里建一个**专用标签/文件夹**，所有 Moomoo 对账单走这个文件夹。

1. 登录 Gmail → 设置 → **Filters and Blocked Addresses** → Create filter
2. 条件：`From: noreply@moomoo.com`（或 Moomoo 实际发件地址，首次导出后确认）
3. 操作：
   - **Skip Inbox**（跳过收件箱）
   - **Apply label**：`daily-stock/moomoo`
   - **Never mark as spam**

这样所有对账单自动进 `daily-stock/moomoo` 标签，不污染主收件箱。

### 3.2 启用 IMAP + 生成 App Password

**前提**：Gmail 账户已启用两步验证（2FA）。

1. Gmail → Settings → **Forwarding and POP/IMAP** → 启用 **IMAP Access**
2. Google 账户 → Security → **App passwords**
3. 生成一个新的 app password：
   - App: **Mail**
   - Device: **Other** → 命名 `daily-stock-imap`
4. 保存生成的 16 位密码（如 `abcd efgh ijkl mnop`，粘贴时去掉空格）

### 3.3 环境变量

```bash
# .env
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your_email@gmail.com
IMAP_APP_PASSWORD=abcdefghijklmnop   # 不要加空格
IMAP_LABEL=daily-stock/moomoo         # Gmail 标签名
IMAP_PROCESSED_LABEL=daily-stock/moomoo-done  # 处理完的邮件移动到这里
```

---

## 四、数据库 Schema

使用项目现有 SQLite（如果后续量大可切 Postgres，schema 不变）。

```sql
-- ========== Table: trade_imports ==========
-- 记录每次 CSV 导入事件，用于幂等与审计
CREATE TABLE trade_imports (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  email_message_id  TEXT UNIQUE NOT NULL,      -- IMAP message-id，去重用
  email_subject     TEXT,
  email_received_at TIMESTAMP NOT NULL,
  csv_filename      TEXT,
  csv_sha256        TEXT UNIQUE NOT NULL,      -- 文件指纹，二次去重
  rows_total        INTEGER,
  rows_imported     INTEGER,                   -- 去重后新增
  rows_skipped      INTEGER,                   -- 已存在
  status            TEXT NOT NULL,             -- 'success' / 'partial' / 'failed'
  error_message     TEXT,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ========== Table: trade_legs ==========
-- 单腿成交（一笔买入或一笔卖出）
CREATE TABLE trade_legs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id       INTEGER REFERENCES trade_imports(id),
  external_id     TEXT UNIQUE,                 -- Moomoo 订单号（如有）否则用 hash
  symbol          TEXT NOT NULL,
  side            TEXT NOT NULL,               -- 'buy' / 'sell'
  quantity        INTEGER NOT NULL,
  price           REAL NOT NULL,
  commission      REAL DEFAULT 0,
  fee             REAL DEFAULT 0,
  gross_amount    REAL NOT NULL,               -- quantity * price
  net_amount      REAL NOT NULL,               -- gross - commission - fee
  currency        TEXT DEFAULT 'USD',
  executed_at     TIMESTAMP NOT NULL,          -- 成交时间（带时区，存 UTC）
  raw_row         TEXT,                        -- 原始 CSV 行，便于审计
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trade_legs_symbol_time ON trade_legs(symbol, executed_at);

-- ========== Table: trades ==========
-- 配对后的完整交易（一个 open leg + 一个或多个 close legs）
CREATE TABLE trades (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol          TEXT NOT NULL,
  direction       TEXT NOT NULL,               -- 'long' / 'short'
  open_leg_ids    TEXT NOT NULL,               -- JSON array of trade_legs.id
  close_leg_ids   TEXT,                        -- JSON array；未平仓为 NULL
  status          TEXT NOT NULL,               -- 'open' / 'closed'
  quantity        INTEGER NOT NULL,
  avg_entry_price REAL NOT NULL,
  avg_exit_price  REAL,                        -- 未平仓为 NULL
  entry_time      TIMESTAMP NOT NULL,
  exit_time       TIMESTAMP,
  hold_seconds    INTEGER,                     -- 持仓时长（秒）
  pnl_gross       REAL,                        -- 毛利
  pnl_net         REAL,                        -- 扣费后净利
  pnl_pct         REAL,                        -- 相对入场成本的百分比
  commission_total REAL DEFAULT 0,
  fee_total       REAL DEFAULT 0,
  -- AI 推断字段
  strategy_tag    TEXT,                        -- momentum/pullback/event_driven，AI 填
  entry_reason_ai TEXT,                        -- AI 推断的入场逻辑
  exit_reason_ai  TEXT,                        -- AI 推断的出场逻辑
  mistakes_ai     TEXT,                        -- AI 识别的潜在失误
  -- 用户手动字段
  user_notes      TEXT,
  emotional_state TEXT,                        -- 'discipline' / 'fomo' / 'revenge'
  screenshot_url  TEXT,                        -- 入场/出场截图（可选）
  -- 元数据
  ai_analyzed_at  TIMESTAMP,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trades_symbol_entry ON trades(symbol, entry_time);
CREATE INDEX idx_trades_status ON trades(status);

-- ========== Table: monthly_reviews ==========
CREATE TABLE monthly_reviews (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  year_month      TEXT UNIQUE NOT NULL,        -- 'YYYY-MM'
  total_trades    INTEGER,
  win_rate        REAL,
  avg_win         REAL,
  avg_loss        REAL,
  profit_factor   REAL,                        -- total_wins / abs(total_losses)
  max_drawdown    REAL,
  best_strategy   TEXT,
  worst_strategy  TEXT,
  review_markdown TEXT NOT NULL,               -- Gemini 生成的长文复盘
  generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 五、IMAP 轮询服务

### 5.1 文件位置

```
src/journal/
  __init__.py
  imap_poll.py        # 邮件抓取
  csv_parser.py       # CSV 解析
  matcher.py          # 交易配对
  ai_analyzer.py      # AI 单笔 / 月度分析
  storage.py          # DB CRUD
```

### 5.2 `src/journal/imap_poll.py` 骨架

```python
"""Moomoo 交易记录 IMAP 轮询。"""
from __future__ import annotations

import hashlib
import imaplib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes
from email.message import Message
from typing import Iterator

from src.journal.csv_parser import parse_moomoo_csv
from src.journal.matcher import match_and_persist
from src.journal.storage import (
    is_message_processed,
    record_import,
)

logger = logging.getLogger(__name__)


@dataclass
class FetchedEmail:
    message_id: str
    subject: str
    received_at: datetime
    attachments: list[tuple[str, bytes]]  # [(filename, content_bytes), ...]


def connect() -> imaplib.IMAP4_SSL:
    host = os.environ["IMAP_HOST"]
    port = int(os.environ.get("IMAP_PORT", "993"))
    user = os.environ["IMAP_USER"]
    pw = os.environ["IMAP_APP_PASSWORD"]
    m = imaplib.IMAP4_SSL(host, port)
    m.login(user, pw)
    return m


def fetch_unprocessed(conn: imaplib.IMAP4_SSL) -> Iterator[FetchedEmail]:
    """从指定 Gmail 标签拉取所有未处理的邮件。"""
    label = os.environ["IMAP_LABEL"]
    conn.select(f'"{label}"')  # Gmail 标签名带斜杠时需加引号

    # 只取带附件 + 未被我们标记为 processed 的
    # Gmail 特有：X-GM-LABELS 可以拿到标签列表
    status, ids = conn.search(None, 'HAS', 'attachment')
    if status != "OK":
        return

    for msg_id_bytes in ids[0].split():
        status, data = conn.fetch(msg_id_bytes, "(RFC822)")
        if status != "OK":
            continue
        msg: Message = message_from_bytes(data[0][1])

        mid = msg.get("Message-ID", "").strip()
        if not mid or is_message_processed(mid):
            continue

        # 提取附件
        attachments = []
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                fn = part.get_filename() or "unknown.csv"
                if not fn.lower().endswith((".csv",)):
                    continue
                content = part.get_payload(decode=True)
                attachments.append((fn, content))

        if not attachments:
            continue

        yield FetchedEmail(
            message_id=mid,
            subject=msg.get("Subject", ""),
            received_at=_parse_email_date(msg.get("Date")),
            attachments=attachments,
        )


def process_once() -> dict:
    """单次轮询，返回统计摘要。"""
    stats = {"emails": 0, "trades_added": 0, "errors": 0}
    conn = connect()
    try:
        for email in fetch_unprocessed(conn):
            stats["emails"] += 1
            for fn, content in email.attachments:
                sha = hashlib.sha256(content).hexdigest()
                try:
                    legs = parse_moomoo_csv(content)
                    import_id = record_import(
                        email_message_id=email.message_id,
                        email_subject=email.subject,
                        email_received_at=email.received_at,
                        csv_filename=fn,
                        csv_sha256=sha,
                        rows_total=len(legs),
                    )
                    added = match_and_persist(import_id, legs)
                    stats["trades_added"] += added
                except Exception as exc:
                    logger.exception("CSV 处理失败 %s", fn)
                    stats["errors"] += 1
    finally:
        conn.logout()
    return stats


def _parse_email_date(s: str | None) -> datetime:
    from email.utils import parsedate_to_datetime
    return parsedate_to_datetime(s) if s else datetime.utcnow()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = process_once()
    print(result)
```

### 5.3 去重策略（三层防护）

1. **Message-ID**：Gmail 邮件的全局唯一 ID，存入 `trade_imports.email_message_id`，重复邮件直接跳过
2. **CSV SHA256**：即使用户重发邮件，内容一样 SHA 一致，直接跳过
3. **Trade Leg external_id**：CSV 里 Moomoo 订单号为主键（如无则用 `symbol+executed_at+side+qty+price` 的 hash），入库时 `INSERT OR IGNORE`

---

## 六、CSV 解析器

### 6.1 `src/journal/csv_parser.py` 骨架

```python
"""Moomoo CSV 解析。用户提供真实样本后按需调整列名映射。"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable


@dataclass
class RawLeg:
    external_id: str
    symbol: str
    side: str              # 'buy' | 'sell'
    quantity: int
    price: float
    commission: float
    fee: float
    gross_amount: float
    net_amount: float
    currency: str
    executed_at: datetime  # UTC
    raw_row: str


# 字段别名，兼容 Moomoo 不同版本导出的列名差异
FIELD_ALIASES = {
    "symbol":       ["Symbol", "Ticker", "Stock Code"],
    "side":         ["Side", "Action", "Type"],
    "quantity":     ["Quantity", "Qty", "Filled Qty"],
    "price":        ["Price", "Filled Price", "Executed Price"],
    "commission":   ["Commission", "Comm"],
    "fee":          ["Fee", "Total Fee", "Regulatory Fee"],
    "amount":       ["Amount", "Net Amount", "Total"],
    "trade_date":   ["Trade Date", "Date", "Executed Date", "Executed At"],
    "status":       ["Status", "State"],
    "order_id":     ["Order ID", "Order No", "OrderID"],
    "currency":     ["Currency", "Ccy"],
}


def _pick(row: dict, keys: list[str]) -> str | None:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def parse_moomoo_csv(content: bytes) -> list[RawLeg]:
    text = content.decode("utf-8-sig")  # 处理 BOM
    reader = csv.DictReader(io.StringIO(text))
    legs: list[RawLeg] = []

    for row in reader:
        status = _pick(row, FIELD_ALIASES["status"]) or ""
        if status.lower() not in ("filled", "executed", "complete", ""):
            continue

        side_raw = (_pick(row, FIELD_ALIASES["side"]) or "").lower()
        if "buy" in side_raw:
            side = "buy"
        elif "sell" in side_raw:
            side = "sell"
        else:
            continue  # 跳过无法识别的行

        try:
            symbol   = (_pick(row, FIELD_ALIASES["symbol"]) or "").strip().upper()
            qty      = int(float(_pick(row, FIELD_ALIASES["quantity"]) or 0))
            price    = float(_pick(row, FIELD_ALIASES["price"]) or 0)
            comm     = float(_pick(row, FIELD_ALIASES["commission"]) or 0)
            fee      = float(_pick(row, FIELD_ALIASES["fee"]) or 0)
            currency = _pick(row, FIELD_ALIASES["currency"]) or "USD"
            executed_at = _parse_datetime(_pick(row, FIELD_ALIASES["trade_date"]))
        except (TypeError, ValueError):
            continue

        if qty <= 0 or price <= 0 or not symbol:
            continue

        gross = qty * price * (1 if side == "buy" else -1) * -1  # 买入是现金流出
        # 约定：net_amount = 买入为负，卖出为正
        if side == "buy":
            net = -(qty * price) - comm - fee
        else:
            net = (qty * price) - comm - fee

        order_id = _pick(row, FIELD_ALIASES["order_id"])
        external_id = order_id or _hash_leg(symbol, side, qty, price, executed_at)

        legs.append(RawLeg(
            external_id=external_id,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            commission=comm,
            fee=fee,
            gross_amount=qty * price,
            net_amount=net,
            currency=currency,
            executed_at=executed_at,
            raw_row=str(row),
        ))

    return legs


def _parse_datetime(s: str | None) -> datetime:
    if not s:
        raise ValueError("missing trade date")
    # Moomoo 格式常见："2026-04-15" / "2026-04-15 09:32:15" / "04/15/2026 09:32 EDT"
    candidates = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    ]
    for fmt in candidates:
        try:
            dt = datetime.strptime(s.strip(), fmt)
            # 若无时区信息，假设是美东时间（Moomoo US 默认）
            from zoneinfo import ZoneInfo
            return dt.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {s}")


def _hash_leg(symbol: str, side: str, qty: int, price: float, dt: datetime) -> str:
    key = f"{symbol}|{side}|{qty}|{price}|{dt.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

---

## 七、交易配对算法

### 7.1 核心挑战

Moomoo CSV 导出的是**单腿成交**，不是"进出场配对好的完整交易"。一笔完整交易可能是：
- 1 Buy 100 NVDA → 1 Sell 100 NVDA（简单配对）
- 1 Buy 100 NVDA → 2 Sell 50 NVDA（分批出场）
- 3 Buy 50 NVDA → 1 Sell 150 NVDA（分批加仓后整体出场）
- 跨日持仓（隔夜）

需要一个**FIFO 配对引擎**把 legs 还原成 trades。

### 7.2 FIFO 配对算法

```python
"""src/journal/matcher.py"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class OpenPosition:
    """代表一个未平的持仓批次。"""
    symbol: str
    direction: str          # 'long' or 'short'
    qty_remaining: int
    avg_price: float
    open_leg_ids: list[int] = field(default_factory=list)
    first_open_time: datetime = None


def match_legs_fifo(legs: list["RawLeg"]) -> list[dict]:
    """
    输入：按时间升序排列的 legs
    输出：已配对的 trades 列表（每个是 dict，对应 trades 表一行）

    规则：
    - Buy 在无持仓或多头持仓时，加仓
    - Sell 在多头持仓时，按 FIFO 平仓（生成完整 trade）
    - Buy 在空头持仓时，按 FIFO 平仓
    - Sell 在无持仓或空头持仓时，开空头加仓
    """
    legs_sorted = sorted(legs, key=lambda x: x.executed_at)
    # 每个 symbol 维护一个开仓队列（FIFO）
    open_queues: dict[str, deque[OpenPosition]] = {}
    completed_trades: list[dict] = []

    for leg in legs_sorted:
        sym = leg.symbol
        q = open_queues.setdefault(sym, deque())

        # 判断当前持仓方向
        current_dir = q[0].direction if q else None

        if not q:
            # 新开仓
            direction = "long" if leg.side == "buy" else "short"
            q.append(OpenPosition(
                symbol=sym, direction=direction,
                qty_remaining=leg.quantity, avg_price=leg.price,
                open_leg_ids=[leg.external_id],
                first_open_time=leg.executed_at,
            ))
        elif (current_dir == "long" and leg.side == "buy") or \
             (current_dir == "short" and leg.side == "sell"):
            # 加仓
            q.append(OpenPosition(
                symbol=sym, direction=current_dir,
                qty_remaining=leg.quantity, avg_price=leg.price,
                open_leg_ids=[leg.external_id],
                first_open_time=leg.executed_at,
            ))
        else:
            # 平仓，按 FIFO 消费
            qty_to_close = leg.quantity
            close_leg_ids_used = [leg.external_id]
            consumed_opens = []

            while qty_to_close > 0 and q:
                head = q[0]
                if head.qty_remaining <= qty_to_close:
                    # 整个 head 被消费
                    consumed_opens.append({
                        "open_ids": head.open_leg_ids,
                        "qty": head.qty_remaining,
                        "avg_entry": head.avg_price,
                        "entry_time": head.first_open_time,
                    })
                    qty_to_close -= head.qty_remaining
                    q.popleft()
                else:
                    # 部分消费
                    consumed_opens.append({
                        "open_ids": head.open_leg_ids,
                        "qty": qty_to_close,
                        "avg_entry": head.avg_price,
                        "entry_time": head.first_open_time,
                    })
                    head.qty_remaining -= qty_to_close
                    qty_to_close = 0

            # 为每个被消费的 open 生成一条 trade
            for co in consumed_opens:
                pnl_gross = _compute_pnl(
                    direction=current_dir,
                    qty=co["qty"],
                    entry=co["avg_entry"],
                    exit_=leg.price,
                )
                completed_trades.append({
                    "symbol": sym,
                    "direction": current_dir,
                    "quantity": co["qty"],
                    "avg_entry_price": co["avg_entry"],
                    "avg_exit_price": leg.price,
                    "entry_time": co["entry_time"],
                    "exit_time": leg.executed_at,
                    "hold_seconds": int(
                        (leg.executed_at - co["entry_time"]).total_seconds()
                    ),
                    "pnl_gross": pnl_gross,
                    "pnl_net": pnl_gross - leg.commission - leg.fee,
                    "pnl_pct": (pnl_gross / (co["qty"] * co["avg_entry"])) * 100,
                    "open_leg_ids": co["open_ids"],
                    "close_leg_ids": close_leg_ids_used,
                    "status": "closed",
                })

    # 剩余在 open_queues 里的是未平仓持仓
    for sym, q in open_queues.items():
        for pos in q:
            completed_trades.append({
                "symbol": sym,
                "direction": pos.direction,
                "quantity": pos.qty_remaining,
                "avg_entry_price": pos.avg_price,
                "entry_time": pos.first_open_time,
                "status": "open",
                "open_leg_ids": pos.open_leg_ids,
                # pnl 等字段为 None
            })

    return completed_trades


def _compute_pnl(direction: str, qty: int, entry: float, exit_: float) -> float:
    if direction == "long":
        return (exit_ - entry) * qty
    else:
        return (entry - exit_) * qty
```

### 7.3 边界情况

- **跨 CSV 持仓**：用户周一买入 NVDA，周五卖出。如果两次导出分两次处理，配对算法要能跨导入批次工作 → 每次新 CSV 进来时，**重跑全量 legs 的配对**（不是增量）。对几千条以内完全够用。
- **账户多币种**：只处理 USD，其他币种过滤
- **股票拆股 / 分红**：这些不会出现在 Trade History 里（在 Corporate Action 里），所以忽略
- **订单部分成交**：Moomoo 已经按成交拆行，每行是一次成交，直接用即可

---

## 八、AI 单笔分析 Prompt

### 8.1 何时触发

每次 `match_and_persist` 新增一条 `closed` trade 时，异步触发 AI 分析（丢进任务队列或直接在 cron 批量处理）。

### 8.2 上下文拼装

AI 需要看到：
1. 交易基本信息（symbol, direction, entry/exit price/time, pnl）
2. 入场前后的 K 线形态（±30 根 5min 或日线）
3. 交易当日的重大事件（财报？宏观？）
4. 用户声明的 `TRADING_STYLE_PROMPT`

### 8.3 Prompt 模板

```python
SINGLE_TRADE_ANALYSIS_PROMPT = """
你是一位严谨的美股日内交易教练。基于以下数据，对用户的一笔**已完结**交易做客观分析。

# 用户声明的交易风格
{trading_style}

# 本笔交易基本信息
- 标的：{symbol}
- 方向：{direction}
- 数量：{quantity}
- 入场时间：{entry_time_et} (ET)
- 入场均价：${entry_price}
- 出场时间：{exit_time_et} (ET)
- 出场均价：${exit_price}
- 持仓时长：{hold_duration_human}
- 毛利：${pnl_gross}（{pnl_pct:.2f}%）
- 净利：${pnl_net}

# 入场前后 K 线（日线近 30 根 + 入场日 5m 图）
{kline_context}

# 交易当日事件
{events_of_day}

# 输出要求
用简洁中文回答，不要客套话。按以下 JSON 格式输出：

{{
  "strategy_tag": "momentum / pullback / event_driven / breakout / mean_reversion / unclear",
  "entry_reason_ai": "基于 K 线数据和用户风格，最可能的入场逻辑（1-2 句，直接引用数据）",
  "exit_reason_ai": "最可能的出场逻辑（1-2 句）",
  "alignment_score": "0-10，该笔交易与用户声明风格的契合度。0 = 完全背离，10 = 完全符合",
  "mistakes": "如果 pnl<0 或 alignment_score<6，指出 1-3 条具体问题（如'入场偏早，MA5 未金叉就追入'）。无问题输出空数组 []",
  "key_observations": "1-2 条中性客观的观察，不预设好坏"
}}

严格输出 JSON，不要 markdown 代码块。
""".strip()
```

### 8.4 典型输出示例

```json
{
  "strategy_tag": "pullback",
  "entry_reason_ai": "NVDA 回踩 MA13 日线支撑（$180.2），5m 图量能企稳，MA3 转平，符合用户'强势股回踩 MA13 低吸'策略",
  "exit_reason_ai": "盘中触及前高阻力 $185.5，MA3 向下拐头，主动止盈出场",
  "alignment_score": 8,
  "mistakes": [],
  "key_observations": [
    "持仓时长 3h42m，符合日内短波段预期",
    "收益率 1.57%，低于用户宣称的平均 2% 止盈目标，可能存在过早止盈"
  ]
}
```

---

## 九、AI 月度复盘 Prompt

### 9.1 触发时机

每月 1 号 00:00 ET（即 UTC 05:00）cron 触发，分析**上月**所有 closed trades。

### 9.2 预处理：生成统计数据

在喂给 AI 之前，后端先跑统计，减少 AI 计算负担（它不擅长也不稳定）：

```python
def compute_monthly_stats(year: int, month: int) -> dict:
    trades = query_closed_trades_in_month(year, month)
    wins = [t for t in trades if t.pnl_net > 0]
    losses = [t for t in trades if t.pnl_net < 0]

    return {
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0,
        "avg_win": mean([t.pnl_net for t in wins]) if wins else 0,
        "avg_loss": mean([t.pnl_net for t in losses]) if losses else 0,
        "profit_factor": sum(t.pnl_net for t in wins) / abs(sum(t.pnl_net for t in losses)) if losses else None,
        "total_pnl_net": sum(t.pnl_net for t in trades),
        "by_symbol": group_by_symbol(trades),
        "by_strategy": group_by_strategy(trades),
        "by_hour": group_by_entry_hour(trades),
        "by_day_of_week": group_by_weekday(trades),
        "avg_hold_seconds": mean([t.hold_seconds for t in trades]),
        "max_loss_pct": min(t.pnl_pct for t in trades) if trades else 0,
        "max_win_pct": max(t.pnl_pct for t in trades) if trades else 0,
        "worst_3_trades": sorted(trades, key=lambda x: x.pnl_net)[:3],
        "best_3_trades": sorted(trades, key=lambda x: -x.pnl_net)[:3],
        "alignment_scores": [t.alignment_score for t in trades if t.alignment_score is not None],
    }
```

### 9.3 复盘 Prompt

```python
MONTHLY_REVIEW_PROMPT = """
你是一位严谨的美股日内交易教练。基于以下**上月（{year}-{month:02d}）**的完整交易统计和用户声明的风格，产出一份行为复盘报告。

# 用户声明的交易风格
{trading_style}

# 月度统计
- 总交易数：{total_trades}
- 胜率：{win_rate:.1%}
- 平均盈利：${avg_win:.2f}
- 平均亏损：${avg_loss:.2f}
- 盈亏比：{profit_factor}
- 月度净利：${total_pnl_net:.2f}
- 平均持仓时长：{avg_hold_human}
- 单笔最大亏损：{max_loss_pct:.2f}%
- 风格一致性评分均值：{alignment_mean:.1f}/10

## 按标的分组
{by_symbol_markdown}

## 按策略分组
{by_strategy_markdown}

## 按时段分组（美东入场小时）
{by_hour_markdown}

## 表现最差的 3 笔
{worst_3_markdown}

## 表现最好的 3 笔
{best_3_markdown}

# 输出要求
用中文写一份 1000-1500 字的行为复盘报告，Markdown 格式。必须包含以下章节，不要加客套和鼓励的话：

## 一、数据快照（用户本月的硬事实）
直接列关键数字。

## 二、与声明风格的偏离度
对比用户声明的风格（2-3% 止损、MA3>MA5>MA13、不接飞刀等），指出**实际交易中偏离风格的次数和模式**。用具体交易 ID 引用。

## 三、重复犯的错
识别 2-3 个重复出现的错误模式（如"在 MA5 刚破就止损，3 次随后都反弹"），用数据支撑。

## 四、本月最优与最劣交易的共同特征
对比最好的 3 笔和最差的 3 笔，找出入场结构、时段、标的的系统性差异。

## 五、下月行动建议
3-5 条可执行建议（如"暂停在 14:00-15:00 入场，该时段胜率 25%"），不要鸡汤。

保持客观、直接、基于数据。不要"加油""继续努力"之类的话。
""".strip()
```

---

## 十、FastAPI 端点

```python
# src/api/routers/journal.py

from fastapi import APIRouter, HTTPException, UploadFile
from datetime import date

router = APIRouter(prefix="/api/v1/journal", tags=["journal"])

@router.post("/upload")
async def upload_csv(file: UploadFile):
    """手动上传 CSV（绕过邮箱）"""
    content = await file.read()
    # ... 同 IMAP 流程
    return {"imported": n}

@router.get("/trades")
async def list_trades(
    symbol: str | None = None,
    start: date | None = None,
    end: date | None = None,
    status: str | None = None,
    strategy: str | None = None,
    page: int = 1,
    per_page: int = 50,
):
    """交易列表，支持过滤"""
    ...

@router.get("/trades/{trade_id}")
async def get_trade(trade_id: int):
    """单笔详情 + AI 分析 + K 线上下文"""
    ...

@router.patch("/trades/{trade_id}")
async def update_trade_notes(trade_id: int, payload: dict):
    """用户补充备注、情绪状态"""
    ...

@router.post("/trades/{trade_id}/re-analyze")
async def reanalyze_trade(trade_id: int):
    """强制重新调用 AI 分析"""
    ...

@router.get("/reviews/{year}/{month}")
async def get_monthly_review(year: int, month: int):
    """月度复盘报告"""
    ...

@router.post("/reviews/{year}/{month}/generate")
async def generate_monthly_review(year: int, month: int):
    """手动触发月度复盘生成"""
    ...

@router.get("/stats")
async def global_stats(days: int = 90):
    """全局统计（用于仪表盘）"""
    ...

@router.post("/imap/poll")
async def trigger_imap_poll():
    """手动触发 IMAP 轮询"""
    from src.journal.imap_poll import process_once
    return process_once()
```

---

## 十一、React 前端页面

路径：`apps/dsa-web/src/pages/JournalPage.tsx`

### 11.1 页面结构

```
/journal
├─ Tab: Overview（默认）
│   ├─ 本月核心指标卡片（总交易/胜率/净利/风格一致性）
│   ├─ 盈亏曲线（Recharts LineChart）
│   ├─ 最近 10 笔交易简表
│   └─ 本月复盘预览（如已生成）
│
├─ Tab: Trades
│   ├─ 过滤栏（symbol / date range / strategy / 盈亏）
│   ├─ 交易表格（可排序）
│   └─ 行点击 → 右侧抽屉详情
│
├─ Tab: Reviews
│   ├─ 月份选择器
│   ├─ 当月 Markdown 复盘渲染
│   └─ 按钮：重新生成
│
├─ Tab: Import
│   ├─ 拖拽上传 CSV
│   ├─ IMAP 轮询状态（上次拉取时间 / 处理的邮件数 / 错误数）
│   └─ 手动触发 IMAP
```

### 11.2 关键组件

- `<TradeTable />` 复用 react-table 或手写
- `<TradeDetailDrawer />` 显示单笔 + K 线嵌入（TradingView Widget 聚焦入场时间点）+ AI 分析
- `<MonthlyReviewRenderer />` 用 react-markdown 渲染 AI 返回的 Markdown
- `<ImapStatusCard />` 轮询 `/api/v1/journal/stats` 显示最近同步情况

---

## 十二、调度与 CI

### 12.1 GitHub Actions：IMAP 轮询

```yaml
# .github/workflows/imap_poll.yml
name: IMAP Poll Moomoo CSV

on:
  schedule:
    # 每小时跑一次（盘中更勤快的话可以改成每 15 分钟）
    - cron: '15 * * * *'
  workflow_dispatch:

jobs:
  poll:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - env:
          IMAP_HOST: ${{ secrets.IMAP_HOST }}
          IMAP_PORT: ${{ secrets.IMAP_PORT }}
          IMAP_USER: ${{ secrets.IMAP_USER }}
          IMAP_APP_PASSWORD: ${{ secrets.IMAP_APP_PASSWORD }}
          IMAP_LABEL: ${{ vars.IMAP_LABEL }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          TRADING_STYLE_PROMPT: ${{ vars.TRADING_STYLE_PROMPT }}
        run: python -m src.journal.imap_poll
```

### 12.2 月度复盘 cron

```yaml
# .github/workflows/monthly_review.yml
name: Monthly Journal Review

on:
  schedule:
    - cron: '0 5 1 * *'   # 每月 1 号 UTC 05:00 = 美东 01:00
  workflow_dispatch:
    inputs:
      year_month:
        description: 'YYYY-MM (leave empty for last month)'
        required: false

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          TRADING_STYLE_PROMPT: ${{ vars.TRADING_STYLE_PROMPT }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python -m src.journal.monthly_review
```

---

## 十三、幂等性与错误处理

| 场景 | 处理 |
|--|--|
| 同一封邮件被 IMAP 二次读到 | `trade_imports.email_message_id` UNIQUE 约束，INSERT IGNORE |
| 用户重发同一份 CSV | `csv_sha256` UNIQUE，跳过 |
| 同一笔成交被两次导入 | `trade_legs.external_id` UNIQUE，INSERT IGNORE |
| CSV 格式变动导致解析失败 | 捕获异常，`trade_imports.status='failed'` + 发 Telegram 告警，不阻塞其他文件 |
| IMAP 连接失败 | 日志 + 重试（下次 cron）；连续失败 > 3 次发 Telegram |
| 配对算法遇到数据异常（如卖超持仓） | 记录警告日志，允许负持仓（可能是导出不完整），下次全量重配时自动修正 |
| AI 分析失败 | `trades.ai_analyzed_at` 保持 NULL，后续手动触发或下次 cron 补跑 |
| Gemini rate limit | 指数退避 + LiteLLM 路由切 Claude 备用 |

---

## 十四、测试清单

### 14.1 单元测试

- [ ] `parse_moomoo_csv` 能解析 3 种不同格式的 Moomoo CSV（用户提供样本后补充）
- [ ] `parse_moomoo_csv` 对缺失字段、空行、非 Filled 状态行能正确跳过
- [ ] `match_legs_fifo` 简单配对（1 开 1 平）
- [ ] `match_legs_fifo` 分批开仓 + 整单出场
- [ ] `match_legs_fifo` 整单开仓 + 分批出场
- [ ] `match_legs_fifo` 跨日持仓（开仓后次日平仓）
- [ ] `match_legs_fifo` 空头交易
- [ ] `match_legs_fifo` 未平仓列为 `status='open'`
- [ ] 重复 leg 不会被二次配对

### 14.2 集成测试

- [ ] IMAP mock 连接，从假邮件提取附件
- [ ] 端到端：模拟邮件到达 → 轮询 → 入库 → 配对 → 查询 API 返回正确数据
- [ ] AI 分析 mock 返回，trades 表字段正确更新

### 14.3 人工测试（用户）

- [ ] 在 Moomoo 导出本月 CSV，手动发到接收邮箱
- [ ] 等 1 小时 cron 跑完，或手动触发 `/api/v1/journal/imap/poll`
- [ ] 前端 `/journal` 看到新交易
- [ ] 点开单笔，AI 分析在 10 分钟内完成并显示
- [ ] 等到下月 1 号，Telegram 收到月度复盘推送

---

## 十五、部署 checklist

### 15.1 环境变量（`.env`）

```bash
# IMAP
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your_email@gmail.com
IMAP_APP_PASSWORD=xxxxxxxxxxxxxxxx
IMAP_LABEL=daily-stock/moomoo
IMAP_PROCESSED_LABEL=daily-stock/moomoo-done

# LLM
GEMINI_API_KEY=...
TRADING_STYLE_PROMPT="..."

# DB
DATABASE_URL=sqlite:///data/daily_stock.db

# 通知
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### 15.2 GitHub Secrets（Actions 用）

- `IMAP_HOST` / `IMAP_PORT` / `IMAP_USER` / `IMAP_APP_PASSWORD`
- `GEMINI_API_KEY`
- `DATABASE_URL`（如果用远程 DB；SQLite 文件需另外同步）
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`

### 15.3 GitHub Variables

- `IMAP_LABEL=daily-stock/moomoo`
- `TRADING_STYLE_PROMPT`（多行支持）

### 15.4 上线前必做

- [ ] 在 Moomoo App 导出一次 CSV，发到接收邮箱，确认格式
- [ ] 基于真实 CSV 微调 `FIELD_ALIASES`
- [ ] 用真实 CSV 跑一次 `parse_moomoo_csv` 本地验证
- [ ] 跑一次 `match_legs_fifo` 的单元测试覆盖上面 9 个场景
- [ ] 手动触发一次 `/api/v1/journal/imap/poll`，观察日志
- [ ] 前端 `/journal` 能正确渲染

---

## 十六、风险与已知限制

| 风险 | 影响 | 缓解 |
|--|--|--|
| Moomoo 改变 CSV 字段命名 | 解析失败 | `FIELD_ALIASES` 策略；失败时 Telegram 告警；用户重导 |
| Moomoo 停用"邮件导出"功能 | 整条链路断 | 降级到"手动网页上传 CSV"，UI 已支持 |
| Gmail 封 app password（如用户关 2FA） | IMAP 断 | 文档提醒；改用 IMAP OAuth2（复杂度高，不做） |
| 配对算法遇到未覆盖的边界（如分红转股） | 产生异常 trade | 捕获并标 `status='anomaly'`，人工检查 |
| 用户不及时导出 CSV | 数据滞后 | 默认"每小时轮询"；UI 显示"距上次同步 X 小时" |
| AI 误判策略标签 | 统计口径偏差 | 用户可在 UI 手动修正 `strategy_tag`，覆盖 AI 推断 |
| 隐私（CSV 含账户号） | 若 DB 泄露敏感 | 入库前把账户号字段 hash；或整张 DB 加密 |

---

> **实施顺序建议**：第 2 节（CSV 导出验证）→ 第 3 节（Gmail 配置）→ 第 4 节（建表）→ 第 6 节（解析器，拿真实 CSV 测）→ 第 7 节（配对算法）→ 第 5 节（IMAP 轮询）→ 第 8-9 节（AI）→ 第 10-11 节（API + UI）→ 第 12 节（CI）。