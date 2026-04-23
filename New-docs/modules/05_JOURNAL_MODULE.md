# 05 · Journal Module

> **模块**：交易日志 / 复盘引擎（Moomoo CSV 自动入库 + AI 复盘）  
> **位置**：`src/journal/` + 扩展 `src/agent/tools/get_portfolio_snapshot.py`  
> **前置**：`02_ARCHITECTURE_OVERVIEW.md`、`04_OPTION_SUPPORT_EXTENSION.md`  
> **设计原则**：**扩展原项目 Portfolio，不另起炉灶**

---

## 0. 为什么要扩展 Portfolio 而不是新建

原项目已经有完整的 Portfolio 系统：

```
原项目已有：
├─ 持仓主表 portfolios
├─ 交易事件 portfolio_events（开仓/平仓/转账等）
├─ 每日快照 portfolio_snapshots
├─ 券商 CSV 解析器 registry（huatai/citic/cmb + 可扩展）
├─ 风控计算（drawdown、止损预警）
├─ 汇率处理
├─ /portfolio Web 页面
├─ get_portfolio_snapshot Agent 工具
```

**如果新建 `journal` 独立模块**：
- 重复造轮子（持仓/事件/快照表都有）
- 数据不互通（Portfolio 一份 / Journal 一份）
- Agent 需要同时调两套工具

**扩展 Portfolio 的路径**：
- 保留原有所有能力
- 加一个新的 CSV 解析器（Moomoo 美股期权）
- 扩展 `portfolio_events` 表字段支持期权
- 新增"复盘"专用表（Reality Test / Health Check / Monthly Review 的结果）
- 前端 `/portfolio` 页面加 tabs（Journal / Reality Test）

**这是 ADR-v4-02 的具体落地**。

---

## 1. 数据模型

### 1.1 对现有 `portfolio_events` 的扩展

原 repo 的 `portfolio_events` 表（根据 `review.md` 和 `/portfolio` 页面推断）大致结构：

```sql
-- 原表（不修改字段，只加新字段）
CREATE TABLE portfolio_events (
    id              INTEGER PRIMARY KEY,
    portfolio_id    INTEGER,
    event_type      TEXT,         -- 'buy' / 'sell' / 'deposit' / 'withdrawal' / 'dividend' / ...
    symbol          TEXT,
    quantity        REAL,
    price           REAL,
    amount          REAL,
    currency        TEXT,
    executed_at     TIMESTAMP,
    -- ... 其他字段
);
```

**我们加的字段**（通过 migration 增加，兼容老数据）：

```sql
-- migration 001_journal_option_extension.sql
ALTER TABLE portfolio_events ADD COLUMN is_option BOOLEAN DEFAULT 0;
ALTER TABLE portfolio_events ADD COLUMN occ_symbol TEXT;            -- 原始 OCC 字符串
ALTER TABLE portfolio_events ADD COLUMN underlying TEXT;
ALTER TABLE portfolio_events ADD COLUMN expiry DATE;
ALTER TABLE portfolio_events ADD COLUMN strike REAL;
ALTER TABLE portfolio_events ADD COLUMN right TEXT;                 -- 'C' / 'P'
ALTER TABLE portfolio_events ADD COLUMN dte_at_entry INTEGER;
ALTER TABLE portfolio_events ADD COLUMN commission REAL DEFAULT 0;
ALTER TABLE portfolio_events ADD COLUMN occ_fee REAL DEFAULT 0;     -- 期权特有
ALTER TABLE portfolio_events ADD COLUMN contract_fee REAL DEFAULT 0;
ALTER TABLE portfolio_events ADD COLUMN reg_fees REAL DEFAULT 0;
ALTER TABLE portfolio_events ADD COLUMN sec_fee REAL DEFAULT 0;
ALTER TABLE portfolio_events ADD COLUMN total_fee REAL DEFAULT 0;
ALTER TABLE portfolio_events ADD COLUMN order_time TIMESTAMP;       -- 下单时间（vs executed = 成交时间）
ALTER TABLE portfolio_events ADD COLUMN session TEXT;               -- 'regular' / 'pre' / 'post'
ALTER TABLE portfolio_events ADD COLUMN external_order_id TEXT;    -- 去重用
ALTER TABLE portfolio_events ADD COLUMN raw_row TEXT;              -- 原始 CSV 行 JSON

CREATE INDEX idx_events_underlying ON portfolio_events(underlying);
CREATE INDEX idx_events_expiry ON portfolio_events(expiry);
CREATE UNIQUE INDEX idx_events_external_id ON portfolio_events(external_order_id)
    WHERE external_order_id IS NOT NULL;
```

### 1.2 新增专用表

```sql
-- trade_imports: CSV 导入审计（原 Portfolio 没有）
CREATE TABLE trade_imports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id        INTEGER REFERENCES portfolios(id),
    source_path         TEXT NOT NULL,
    csv_sha256          TEXT UNIQUE NOT NULL,
    broker              TEXT NOT NULL,       -- 'moomoo_us' / 'ibkr' / ...
    imported_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rows_total          INTEGER,
    rows_imported       INTEGER,
    rows_skipped        INTEGER,
    status              TEXT NOT NULL,       -- 'success' / 'partial' / 'failed'
    error               TEXT
);

-- trades: FIFO 配对后的完整交易（高层视图）
-- 原 Portfolio 可能没有"交易配对"概念，只看事件流
-- 我们需要这一层来做胜率/盈亏统计
CREATE TABLE trades (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id            INTEGER REFERENCES portfolios(id),
    -- 标的标识
    is_option               BOOLEAN NOT NULL,
    occ_symbol              TEXT,
    underlying              TEXT NOT NULL,
    expiry                  DATE,
    strike                  REAL,
    right                   TEXT,
    -- 交易信息
    direction               TEXT NOT NULL,   -- 'long' / 'short'
    status                  TEXT NOT NULL,   -- 'open' / 'closed'
    quantity                INTEGER NOT NULL,
    avg_entry_price         REAL NOT NULL,
    avg_exit_price          REAL,
    entry_time              TIMESTAMP NOT NULL,
    exit_time               TIMESTAMP,
    hold_seconds            INTEGER,
    dte_at_entry            INTEGER,
    dte_bucket              TEXT,            -- '0DTE' / '1-3DTE' / '4-7DTE' / ...
    -- 盈亏
    pnl_gross               REAL,
    total_fee               REAL DEFAULT 0,
    pnl_net                 REAL,
    pnl_pct                 REAL,
    -- 关联事件
    open_event_ids          TEXT,            -- JSON array of portfolio_events.id
    close_event_ids         TEXT,
    -- 策略维度（Breakout Filter 给）
    trade_style             TEXT,            -- 'breakout_chase' / 'breakout_retest' / 'pullback' / ...
    regime_score_at_entry   INTEGER,         -- JOIN regime_scores 填充
    pre_filter_pass         BOOLEAN,
    breakout_volume_mult    REAL,
    timeframe_alignment     INTEGER,
    rs_vs_spy               REAL,
    entry_was_retest        BOOLEAN,
    was_fake_breakout       BOOLEAN,
    -- AI 分析
    strategy_tag_ai         TEXT,
    entry_reason_ai         TEXT,
    exit_reason_ai          TEXT,
    alignment_score         INTEGER,         -- 0-10 与用户风格契合度
    mistakes_ai             TEXT,            -- JSON array
    lesson_ai               TEXT,
    ai_analyzed_at          TIMESTAMP,
    -- 用户输入
    user_notes              TEXT,
    emotional_state         TEXT,            -- 'discipline' / 'fomo' / 'revenge' / 'calm'
    screenshot_url          TEXT,
    -- 元数据
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trades_underlying ON trades(underlying);
CREATE INDEX idx_trades_entry ON trades(entry_time);
CREATE INDEX idx_trades_dte ON trades(dte_bucket);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_style ON trades(trade_style);

-- shadow_trades: Phase 1 虚拟交易
CREATE TABLE shadow_trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id        INTEGER REFERENCES portfolios(id),
    instrument_raw      TEXT NOT NULL,
    is_option           BOOLEAN,
    underlying          TEXT,
    expiry              DATE,
    strike              REAL,
    right               TEXT,
    direction           TEXT NOT NULL,
    quantity            INTEGER NOT NULL,
    entry_price         REAL NOT NULL,
    entry_time          TIMESTAMP NOT NULL,
    intended_hold       TEXT,              -- 'short' / 'swing' / 'long'
    rationale           TEXT,
    exit_price          REAL,
    exit_time           TIMESTAMP,
    current_price       REAL,              -- 每日刷新
    current_pnl         REAL,
    status              TEXT DEFAULT 'open',
    closed_reason       TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- health_checks: 每日体检结果（原有 portfolio_snapshots 不够细）
CREATE TABLE health_checks (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    check_date              DATE UNIQUE NOT NULL,
    total_orders            INTEGER DEFAULT 0,
    orders_0dte             INTEGER DEFAULT 0,
    orders_1_3dte           INTEGER DEFAULT 0,
    orders_opening_hour     INTEGER DEFAULT 0,
    top_underlying          TEXT,
    top_underlying_pct      REAL,
    warnings_json           TEXT,          -- JSON array of warning strings
    pnl_estimate            REAL,
    regime_score            INTEGER,       -- 当日 regime
    generated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- monthly_reviews: AI 月度复盘结果
CREATE TABLE monthly_reviews (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    year_month          TEXT UNIQUE NOT NULL,   -- 'YYYY-MM'
    current_phase       INTEGER NOT NULL,
    stats_json          TEXT NOT NULL,          -- 计算好的统计
    review_markdown     TEXT NOT NULL,          -- Gemini/Claude 生成
    generated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- phase_state: 单行表，当前阶段
CREATE TABLE phase_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    phase           INTEGER NOT NULL,
    phase_started   DATE NOT NULL,
    notes           TEXT
);

INSERT INTO phase_state (id, phase, phase_started) VALUES (1, 0, DATE('now'));
```

### 1.3 数据关系

```
portfolios (原有，1 行 = 一个账户)
    │
    ├─ (*) portfolio_events (原有扩展字段)
    │        │
    │        └─ 被 FIFO 配对 → (*) trades (新增)
    │
    ├─ (*) portfolio_snapshots (原有，每日快照)
    │
    └─ (*) trade_imports (新增，审计)

trades (新增)
    │
    ├─ entry_time 关联 regime_scores.date → regime_score_at_entry
    └─ ai_analyzed_at 触发 Agent 复盘

shadow_trades (新增，Phase 1 启用，独立于 trades)

health_checks (新增，每日日终生成)
monthly_reviews (新增，每月 1 号 cron 生成)
phase_state (新增，单行表)
```

---

## 2. CSV 解析器（Moomoo US）

### 2.1 注册到原项目 broker registry

原项目已经有 `huatai/citic/cmb` 的 broker 解析器注册机制。我们加一个 `moomoo_us`。

**文件**：`src/journal/brokers/moomoo_us.py`

```python
"""Moomoo US 账户 CSV 解析器。接入原项目的 broker registry。

Moomoo 导出 CSV 特征：
- 35 列（含两处重复 Markets / Currency）
- 主行 + Fill-only 行混合（同一订单多次成交各占一行）
- 期权 symbol 用 OCC 变长 strike 格式
- 时间格式 'Apr 16, 2026 15:53:57 ET'
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from src.options.occ_parser import parse_symbol, InstrumentInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')


@dataclass
class MoomooOrder:
    """一个 Moomoo 订单（主行 + 所有 fill-only 行合并后）。"""
    # 必填
    side: str                   # 'Buy' / 'Sell'
    symbol: str
    name: str
    order_qty: int
    order_price: float
    order_amount: float
    status: str                 # 'Filled' 等
    order_time: Optional[datetime]
    order_type: str
    session: str
    fills: list = field(default_factory=list)
    # 费用
    commission: float = 0
    platform_fee: float = 0
    trading_activity_fee: float = 0
    options_regulatory_fee: float = 0
    occ_fee: float = 0
    contract_fee: float = 0
    sec_fee: float = 0
    settlement_fee: float = 0
    total_fee: float = 0
    currency: str = 'USD'
    # 派生
    filled_qty: int = 0
    avg_fill_price: float = 0
    first_fill_time: Optional[datetime] = None
    last_fill_time: Optional[datetime] = None
    instrument: Optional[InstrumentInfo] = None
    raw_rows: list = field(default_factory=list)


def _parse_trade_time(s: str) -> Optional[datetime]:
    """'Apr 16, 2026 15:53:57 ET' → aware UTC datetime"""
    if not s:
        return None
    s = s.replace(' ET', '').strip()
    for fmt in ['%b %d, %Y %H:%M:%S', '%b %d, %Y %H:%M', '%Y-%m-%d %H:%M:%S']:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=ET)
        except ValueError:
            continue
    return None


def _float_or_zero(v):
    if v is None or v == '':
        return 0.0
    try:
        return float(str(v).replace(',', ''))
    except (ValueError, TypeError):
        return 0.0


def _int_or_zero(v):
    try:
        return int(float(str(v).replace(',', '')))
    except (ValueError, TypeError):
        return 0


def parse(content: bytes) -> list[MoomooOrder]:
    """
    解析 Moomoo CSV 字节内容。
    返回 MoomooOrder 列表（只包含 Filled 状态）。
    """
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    
    orders: list[MoomooOrder] = []
    current: Optional[MoomooOrder] = None
    
    for row in rows:
        side = (row.get('Side') or '').strip()
        symbol = (row.get('Symbol') or '').strip()
        
        is_main_row = bool(side and symbol)
        
        if is_main_row:
            # 推入上一单
            if current:
                orders.append(current)
            # 开新单
            current = MoomooOrder(
                side=side,
                symbol=symbol,
                name=(row.get('Name') or '').strip(),
                order_qty=_int_or_zero(row.get('Order Qty')),
                order_price=_float_or_zero(row.get('Order Price')),
                order_amount=_float_or_zero(row.get('Order Amount')),
                status=(row.get('Status') or '').strip(),
                order_time=_parse_trade_time(row.get('Order Time')),
                order_type=(row.get('Order Type') or '').strip(),
                session=(row.get('Session') or '').strip(),
                commission=_float_or_zero(row.get('Commission')),
                platform_fee=_float_or_zero(row.get('Platform Fees')),
                trading_activity_fee=_float_or_zero(row.get('Trading Activity Fees')),
                options_regulatory_fee=_float_or_zero(row.get('Options Regulatory Fees')),
                occ_fee=_float_or_zero(row.get('OCC Fees')),
                contract_fee=_float_or_zero(row.get('Contract Fees')),
                sec_fee=_float_or_zero(row.get('SEC Fees')),
                settlement_fee=_float_or_zero(row.get('Settlement Fees')),
                total_fee=_float_or_zero(row.get('Total')),
                currency=(row.get('Currency') or 'USD').strip(),
                raw_rows=[dict(row)],
            )
            current.instrument = parse_symbol(symbol)
            
            # 主行可能本身带一个 fill
            fill_qty = row.get('Fill Qty')
            if fill_qty:
                current.fills.append({
                    'qty': _int_or_zero(fill_qty),
                    'price': _float_or_zero(row.get('Fill Price')),
                    'time': _parse_trade_time(row.get('Fill Time')),
                })
        else:
            # Fill-only 行
            if current and row.get('Fill Qty'):
                current.fills.append({
                    'qty': _int_or_zero(row.get('Fill Qty')),
                    'price': _float_or_zero(row.get('Fill Price')),
                    'time': _parse_trade_time(row.get('Fill Time')),
                })
                current.raw_rows.append(dict(row))
    
    if current:
        orders.append(current)
    
    # 过滤 Filled + 补派生字段
    result = []
    for o in orders:
        if o.status != 'Filled':
            continue
        total_qty = sum(f['qty'] for f in o.fills)
        total_value = sum(f['qty'] * f['price'] for f in o.fills)
        o.filled_qty = total_qty
        o.avg_fill_price = total_value / total_qty if total_qty else 0
        fill_times = [f['time'] for f in o.fills if f['time']]
        if fill_times:
            o.first_fill_time = min(fill_times)
            o.last_fill_time = max(fill_times)
        result.append(o)
    
    return result


def compute_external_id(o: MoomooOrder) -> str:
    """订单唯一 ID（无 Moomoo order_id 字段时用 hash 兜底）。"""
    key = (
        f'{o.symbol}|{o.side}|{o.order_time.isoformat() if o.order_time else ""}|'
        f'{o.order_qty}|{o.order_price}|{o.avg_fill_price}'
    )
    return 'moomoo_' + hashlib.sha256(key.encode()).hexdigest()[:16]
```

### 2.2 Broker Registry 接入

假设原项目的 broker registry 接口类似：

```python
# src/journal/brokers/__init__.py

from . import moomoo_us

BROKERS = {
    # 原有
    'huatai': ...,
    'citic': ...,
    'cmb': ...,
    # 新增
    'moomoo_us': moomoo_us,
}
```

**具体接入方式取决于原 repo 实际的 broker registry API**（需开工时读代码确认）。如果接口不同，写一层 adapter 包起来。

### 2.3 测试

**文件**：`src/journal/tests/test_moomoo_parser.py`

```python
from pathlib import Path
from src.journal.brokers.moomoo_us import parse, compute_external_id

def test_real_csv_735_orders():
    """用真实的用户 CSV 验证总数。"""
    # 指向 fixtures 目录的一份脱敏 CSV
    csv_path = Path(__file__).parent / 'fixtures' / 'moomoo_sample.csv'
    with open(csv_path, 'rb') as f:
        orders = parse(f.read())
    
    assert len(orders) == 735  # 基于用户真实数据
    
    # 选几个 known case 验证
    option_orders = [o for o in orders if o.instrument.is_option]
    assert len(option_orders) == 727  # 99%

def test_fill_merge():
    """主行 + fill-only 行合并。"""
    csv_content = b"""Side,Symbol,Name,Order Price,Order Qty,Order Amount,Status,Filled@Avg Price,Order Time,Order Type,Time-in-Force,Allow Pre-Market,Session,Trigger price,Position Opening,Markets,Currency,Order Source,Fill Qty,Fill Price,Fill Amount,Fill Time,Markets,Currency,Counterparty,Remarks,Commission,Platform Fees,Trading Activity Fees,Options Regulatory Fees,OCC Fees,Contract Fees,SEC Fees,Settlement Fees,Total
Buy,NVDA260417C200000,NVDA 04/17 $200 C,2.50,10,2500.00,Filled,2.55,"Apr 16, 2026 10:00:00 ET",Limit,Day,No,Regular,,Open,US,USD,API,5,2.50,1250.00,"Apr 16, 2026 10:00:15 ET",US,USD,,,0.00,0.30,0.03,0.06,0.05,0.00,0.02,0.00,0.46
,,,,,,,,,,,,,,,,,,5,2.60,1300.00,"Apr 16, 2026 10:00:20 ET",US,USD,,,,,,,,,,,
"""
    orders = parse(csv_content)
    assert len(orders) == 1
    assert orders[0].filled_qty == 10
    assert len(orders[0].fills) == 2
    # 均价 = (5*2.50 + 5*2.60) / 10 = 2.55
    assert abs(orders[0].avg_fill_price - 2.55) < 0.001

def test_option_symbol_parsed():
    csv_content = b"""Side,Symbol,Name,Order Price,Order Qty,Order Amount,Status,Filled@Avg Price,Order Time,Order Type,Time-in-Force,Allow Pre-Market,Session,Trigger price,Position Opening,Markets,Currency,Order Source,Fill Qty,Fill Price,Fill Amount,Fill Time,Markets,Currency,Counterparty,Remarks,Commission,Platform Fees,Trading Activity Fees,Options Regulatory Fees,OCC Fees,Contract Fees,SEC Fees,Settlement Fees,Total
Buy,TSLA260417P382500,TSLA 04/17 $382.5 P,1.00,1,100.00,Filled,1.00,"Apr 16, 2026 10:00:00 ET",Limit,Day,No,Regular,,Open,US,USD,API,1,1.00,100.00,"Apr 16, 2026 10:00:15 ET",US,USD,,,0.00,0.06,0.01,0.01,0.01,0.00,0.00,0.00,0.09
"""
    orders = parse(csv_content)
    assert orders[0].instrument.is_option is True
    assert orders[0].instrument.underlying == 'TSLA'
    assert orders[0].instrument.option.strike == 382.5
    assert orders[0].instrument.option.right == 'P'

def test_skips_cancelled():
    # 同样格式但 Status=Cancelled 不入
    ...

def test_external_id_deterministic():
    # 同一 order 多次调用 compute_external_id 产生相同 ID
    ...
```

### 2.4 文件夹 Watcher

**文件**：`src/journal/folder_watcher.py`

```python
"""监听 ~/Daily-Stock-Inbox/，新 CSV 落地自动入库。"""
import logging
import shutil
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from src.journal.storage import import_csv_to_events
from src.config import INBOX_DIR, PROCESSED_DIR

logger = logging.getLogger(__name__)


class MoomooCsvHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        
        # 只处理 .csv，且文件名以 "History" 开头（Moomoo 导出模式）
        if path.suffix.lower() != '.csv':
            return
        if not path.name.startswith(('History', 'Trade')):
            logger.info(f'跳过非 Moomoo 格式文件: {path.name}')
            return
        
        logger.info(f'检测到新 CSV: {path.name}')
        
        # 等几秒让文件写完整（macOS 写入有延迟）
        time.sleep(2)
        
        try:
            result = import_csv_to_events(path, broker='moomoo_us')
            logger.info(f'导入成功: {result}')
            
            # 移动到 processed
            dest = PROCESSED_DIR / path.name
            if dest.exists():
                dest = PROCESSED_DIR / f'{path.stem}_{int(time.time())}{path.suffix}'
            shutil.move(str(path), str(dest))
            
            # 触发 Telegram 通知
            from bot.telegram_bot import send_message
            send_message(f'✅ 处理 {path.name}：{result["rows_imported"]} 笔入库')
        except Exception as e:
            logger.exception(f'处理 {path.name} 失败')
            from bot.telegram_bot import send_message
            send_message(f'❌ 处理 {path.name} 失败: {e}')


def start_watching():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    
    observer = Observer()
    observer.schedule(MoomooCsvHandler(), str(INBOX_DIR), recursive=False)
    observer.start()
    
    logger.info(f'Watching {INBOX_DIR} for CSVs...')
    
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    start_watching()
```

### 2.5 CLI 手动导入

**文件**：`src/journal/cli.py`

```python
"""CLI 入口。

使用：
  python -m src.journal.cli import /path/to/history.csv
  python -m src.journal.cli rebuild-trades
  python -m src.journal.cli health-check [--date YYYY-MM-DD] [--send]
  python -m src.journal.cli reality-test [--days 30]
  python -m src.journal.cli monthly-review 2026 3
  python -m src.journal.cli watch   # 启动 folder watcher
"""
import argparse
import json
import logging
from datetime import date

logger = logging.getLogger(__name__)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    
    # import
    imp = sub.add_parser('import')
    imp.add_argument('file')
    imp.add_argument('--broker', default='moomoo_us')
    
    # rebuild-trades
    sub.add_parser('rebuild-trades')
    
    # health-check
    hc = sub.add_parser('health-check')
    hc.add_argument('--date', default=None)
    hc.add_argument('--send', action='store_true', help='推送 Telegram')
    
    # reality-test
    rt = sub.add_parser('reality-test')
    rt.add_argument('--days', type=int, default=30)
    
    # monthly-review
    mr = sub.add_parser('monthly-review')
    mr.add_argument('year', type=int)
    mr.add_argument('month', type=int)
    mr.add_argument('--send', action='store_true')
    
    # watch
    sub.add_parser('watch')
    
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    
    if args.cmd == 'import':
        from src.journal.storage import import_csv_to_events, rebuild_trades
        r = import_csv_to_events(args.file, broker=args.broker)
        print(json.dumps(r, indent=2))
        n = rebuild_trades()
        print(f'Rebuilt {n} trades')
    
    elif args.cmd == 'rebuild-trades':
        from src.journal.storage import rebuild_trades
        n = rebuild_trades()
        print(f'Rebuilt {n} trades')
    
    elif args.cmd == 'health-check':
        from src.journal.analytics import daily_health_check
        d = date.fromisoformat(args.date) if args.date else date.today()
        r = daily_health_check(d)
        print(json.dumps(r, indent=2, default=str))
        if args.send:
            from src.journal.formatters import format_health_check
            from bot.telegram_bot import send_message
            send_message(format_health_check(r))
    
    elif args.cmd == 'reality-test':
        from src.journal.analytics import reality_test
        r = reality_test(period_days=args.days)
        print(json.dumps(r, indent=2, default=str))
    
    elif args.cmd == 'monthly-review':
        from src.journal.monthly_review import generate_review
        md = generate_review(args.year, args.month, send=args.send)
        print(md)
    
    elif args.cmd == 'watch':
        from src.journal.folder_watcher import start_watching
        start_watching()


if __name__ == '__main__':
    main()
```

---

## 3. FIFO 配对算法

### 3.1 核心规则

期权配对需要特别注意：
- 每个 `(underlying, expiry, right, strike)` 是独立的 contract，不跨合约配对
- 跨日持仓正常（LEAP 持几个月）
- 分批开仓 / 分批平仓 / 翻仓都要处理

### 3.2 实现

**文件**：`src/journal/matcher.py`

```python
"""FIFO 交易配对算法。portfolio_events → trades。"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OpenLot:
    """一批未平的持仓。"""
    direction: str              # 'long' / 'short'
    qty_remaining: int
    avg_price: float
    open_time: datetime
    open_event_id: int
    is_option: bool
    instrument_key: tuple       # (underlying, expiry, right, strike) or (underlying,)


@dataclass
class TradeRecord:
    """配对后的 trades 表一行。"""
    is_option: bool
    occ_symbol: Optional[str]
    underlying: str
    expiry: Optional[datetime]
    strike: Optional[float]
    right: Optional[str]
    direction: str
    status: str
    quantity: int
    avg_entry_price: float
    avg_exit_price: Optional[float]
    entry_time: datetime
    exit_time: Optional[datetime]
    hold_seconds: Optional[int]
    dte_at_entry: Optional[int]
    dte_bucket: Optional[str]
    pnl_gross: Optional[float]
    total_fee: float
    pnl_net: Optional[float]
    pnl_pct: Optional[float]
    open_event_ids: list
    close_event_ids: list


def _instrument_key(event: dict) -> tuple:
    """产生独立 contract 的标识。"""
    if event.get('is_option'):
        return (event['underlying'], event['expiry'], event['right'], event['strike'])
    else:
        return (event['underlying'],)


def _compute_dte_bucket(dte: int) -> str:
    if dte is None:
        return None
    if dte == 0:
        return '0DTE'
    if dte <= 3:
        return '1-3DTE'
    if dte <= 7:
        return '4-7DTE'
    if dte <= 30:
        return '8-30DTE'
    if dte <= 90:
        return '31-90DTE'
    return 'LEAP'


def match_events_fifo(events: list[dict]) -> list[TradeRecord]:
    """
    FIFO 配对所有 events → trades。
    
    events 每个 dict 要求字段：
      id, is_option, underlying, expiry, right, strike,
      occ_symbol, side ('Buy'/'Sell'), quantity, price,
      executed_at (datetime), total_fee
    """
    # 按 instrument_key 分组
    by_instrument: dict[tuple, list] = defaultdict(list)
    for e in events:
        k = _instrument_key(e)
        by_instrument[k].append(e)
    
    trades: list[TradeRecord] = []
    
    for inst_key, inst_events in by_instrument.items():
        inst_events.sort(key=lambda e: e['executed_at'])
        open_queue: deque[OpenLot] = deque()
        
        for event in inst_events:
            side = event['side']
            qty = event['quantity']
            price = event['price']
            event_id = event['id']
            is_opt = event.get('is_option', False)
            
            current_dir = open_queue[0].direction if open_queue else None
            
            # 判断开/平
            is_open_action = (
                not open_queue or
                (current_dir == 'long' and side == 'Buy') or
                (current_dir == 'short' and side == 'Sell')
            )
            
            if is_open_action:
                new_dir = 'long' if side == 'Buy' else 'short'
                open_queue.append(OpenLot(
                    direction=new_dir,
                    qty_remaining=qty,
                    avg_price=price,
                    open_time=event['executed_at'],
                    open_event_id=event_id,
                    is_option=is_opt,
                    instrument_key=inst_key,
                ))
            else:
                # 平仓：FIFO 消费 open_queue
                qty_to_close = qty
                close_event_fee = event.get('total_fee', 0) or 0
                
                while qty_to_close > 0 and open_queue:
                    head = open_queue[0]
                    
                    closed_qty = min(head.qty_remaining, qty_to_close)
                    # pro-rata 分配当前 close event 的费用
                    pro_rata_fee = close_event_fee * (closed_qty / qty) if qty else 0
                    
                    # 计算 pnl
                    if head.direction == 'long':
                        pnl_gross = (price - head.avg_price) * closed_qty
                    else:
                        pnl_gross = (head.avg_price - price) * closed_qty
                    
                    if is_opt:
                        pnl_gross *= 100  # 每张合约 100 股
                    
                    pnl_net = pnl_gross - pro_rata_fee
                    cost_basis = head.avg_price * closed_qty * (100 if is_opt else 1)
                    pnl_pct = (pnl_gross / cost_basis * 100) if cost_basis else 0
                    
                    # 构造 TradeRecord
                    dte_entry = None
                    if is_opt and event.get('expiry'):
                        dte_entry = (event['expiry'] - head.open_time.date()).days
                    
                    trades.append(TradeRecord(
                        is_option=is_opt,
                        occ_symbol=event.get('occ_symbol'),
                        underlying=event['underlying'],
                        expiry=event.get('expiry'),
                        strike=event.get('strike'),
                        right=event.get('right'),
                        direction=head.direction,
                        status='closed',
                        quantity=closed_qty,
                        avg_entry_price=head.avg_price,
                        avg_exit_price=price,
                        entry_time=head.open_time,
                        exit_time=event['executed_at'],
                        hold_seconds=int((event['executed_at'] - head.open_time).total_seconds()),
                        dte_at_entry=dte_entry,
                        dte_bucket=_compute_dte_bucket(dte_entry) if is_opt else None,
                        pnl_gross=pnl_gross,
                        total_fee=pro_rata_fee,
                        pnl_net=pnl_net,
                        pnl_pct=pnl_pct,
                        open_event_ids=[head.open_event_id],
                        close_event_ids=[event_id],
                    ))
                    
                    head.qty_remaining -= closed_qty
                    qty_to_close -= closed_qty
                    if head.qty_remaining == 0:
                        open_queue.popleft()
                
                # 翻仓：平光了还有 qty_to_close → 反向开仓
                if qty_to_close > 0:
                    new_dir = 'long' if side == 'Buy' else 'short'
                    open_queue.append(OpenLot(
                        direction=new_dir,
                        qty_remaining=qty_to_close,
                        avg_price=price,
                        open_time=event['executed_at'],
                        open_event_id=event_id,
                        is_option=is_opt,
                        instrument_key=inst_key,
                    ))
        
        # 未平仓转为 open trades
        for lot in open_queue:
            dte_entry = None
            if lot.is_option and len(inst_key) > 1 and inst_key[1]:
                dte_entry = (inst_key[1] - lot.open_time.date()).days
            trades.append(TradeRecord(
                is_option=lot.is_option,
                occ_symbol=None,
                underlying=inst_key[0],
                expiry=inst_key[1] if len(inst_key) > 1 else None,
                strike=inst_key[3] if len(inst_key) > 3 else None,
                right=inst_key[2] if len(inst_key) > 2 else None,
                direction=lot.direction,
                status='open',
                quantity=lot.qty_remaining,
                avg_entry_price=lot.avg_price,
                avg_exit_price=None,
                entry_time=lot.open_time,
                exit_time=None,
                hold_seconds=None,
                dte_at_entry=dte_entry,
                dte_bucket=_compute_dte_bucket(dte_entry) if lot.is_option else None,
                pnl_gross=None,
                total_fee=0,
                pnl_net=None,
                pnl_pct=None,
                open_event_ids=[lot.open_event_id],
                close_event_ids=[],
            ))
    
    return trades
```

### 3.3 配对测试（7 核心场景）

**文件**：`src/journal/tests/test_matcher.py`

```python
from datetime import datetime, timedelta, date
from src.journal.matcher import match_events_fifo

def _event(eid, side, qty, price, t, is_option=False, underlying='NVDA', expiry=None, right=None, strike=None, fee=0.5):
    return {
        'id': eid, 'side': side, 'quantity': qty, 'price': price,
        'executed_at': t, 'is_option': is_option, 'underlying': underlying,
        'expiry': expiry, 'right': right, 'strike': strike,
        'occ_symbol': None, 'total_fee': fee,
    }

def test_simple_close():
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [
        _event(1, 'Buy', 10, 5.0, t0),
        _event(2, 'Sell', 10, 6.0, t0 + timedelta(hours=1)),
    ]
    trades = match_events_fifo(events)
    assert len(trades) == 1
    assert trades[0].status == 'closed'
    assert trades[0].direction == 'long'
    assert trades[0].quantity == 10
    assert trades[0].pnl_gross == 10.0  # (6-5)*10

def test_option_close_x100():
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [
        _event(1, 'Buy', 2, 2.5, t0, is_option=True, expiry=date(2026, 4, 17), right='C', strike=200),
        _event(2, 'Sell', 2, 3.0, t0 + timedelta(hours=1), is_option=True, expiry=date(2026, 4, 17), right='C', strike=200),
    ]
    trades = match_events_fifo(events)
    # (3 - 2.5) * 2 * 100 = 100
    assert trades[0].pnl_gross == 100

def test_scale_in_scale_out():
    """Buy 5 + Buy 5 → Sell 10，生成 2 笔 trade（FIFO 拆分）。"""
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [
        _event(1, 'Buy', 5, 5.0, t0),
        _event(2, 'Buy', 5, 5.5, t0 + timedelta(minutes=10)),
        _event(3, 'Sell', 10, 6.0, t0 + timedelta(hours=1)),
    ]
    trades = match_events_fifo(events)
    closed = [t for t in trades if t.status == 'closed']
    assert len(closed) == 2
    # FIFO: 先 5@5.0，后 5@5.5
    assert closed[0].avg_entry_price == 5.0
    assert closed[1].avg_entry_price == 5.5

def test_partial_exits():
    """Buy 10 → Sell 3 → Sell 7。"""
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [
        _event(1, 'Buy', 10, 5.0, t0),
        _event(2, 'Sell', 3, 6.0, t0 + timedelta(minutes=30)),
        _event(3, 'Sell', 7, 7.0, t0 + timedelta(hours=1)),
    ]
    trades = match_events_fifo(events)
    closed = [t for t in trades if t.status == 'closed']
    assert len(closed) == 2
    assert closed[0].quantity == 3
    assert closed[1].quantity == 7

def test_unclosed_open_trade():
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [_event(1, 'Buy', 10, 5.0, t0)]
    trades = match_events_fifo(events)
    assert len(trades) == 1
    assert trades[0].status == 'open'

def test_short_trade():
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [
        _event(1, 'Sell', 10, 6.0, t0),
        _event(2, 'Buy', 10, 5.0, t0 + timedelta(hours=1)),
    ]
    trades = match_events_fifo(events)
    closed = [t for t in trades if t.status == 'closed']
    assert len(closed) == 1
    assert closed[0].direction == 'short'
    assert closed[0].pnl_gross == 10  # (6-5)*10

def test_flip_long_to_short():
    """Buy 10 → Sell 20 → 产生 1 closed + 1 open short 10。"""
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [
        _event(1, 'Buy', 10, 5.0, t0),
        _event(2, 'Sell', 20, 6.0, t0 + timedelta(hours=1)),
    ]
    trades = match_events_fifo(events)
    closed = [t for t in trades if t.status == 'closed']
    opens = [t for t in trades if t.status == 'open']
    assert len(closed) == 1 and len(opens) == 1
    assert closed[0].quantity == 10
    assert opens[0].direction == 'short'
    assert opens[0].quantity == 10

def test_different_contracts_dont_cross_pair():
    """NVDA $200 C 和 NVDA $205 C 是不同 contract，不配对。"""
    t0 = datetime(2026, 4, 1, 10, 0)
    events = [
        _event(1, 'Buy', 1, 2.0, t0, is_option=True, expiry=date(2026,4,17), right='C', strike=200),
        _event(2, 'Sell', 1, 2.5, t0 + timedelta(hours=1), is_option=True, expiry=date(2026,4,17), right='C', strike=205),
    ]
    trades = match_events_fifo(events)
    # 各自是独立的 open trade
    assert len([t for t in trades if t.status == 'open']) == 2
    assert len([t for t in trades if t.status == 'closed']) == 0
```

---

## 4. Analytics：Reality Test / Health Check / Stats

**文件**：`src/journal/analytics.py`

核心 3 个函数。全量实现放代码块里太长，这里给接口签名 + 关键逻辑说明：

### 4.1 `reality_test(period_days)`

```python
def reality_test(period_days: int = 30, portfolio_id: int = None) -> dict:
    """
    返回多维度 Reality Test 结果。
    
    关键检验：
    1. 全量 pnl
    2. 去掉 Top N 后的 pnl（N=5, 10, 10%）
    3. 按 DTE bucket 分组胜率
    4. 按入场小时分组胜率
    5. 按 underlying 分组（top 5）
    """
    # 拉 closed trades in window
    # 按 pnl_net 降序
    # remove_top_n = full - sum(top_n)
    # group by dte_bucket / hour / underlying
    return {
        'period_days': period_days,
        'total_trades': ...,
        'full_pnl': ...,
        'remove_top_5': ...,
        'remove_top_10': ...,
        'remove_top_10pct': ...,
        'by_dte_bucket': {...},
        'by_hour_et': {...},
        'by_underlying_top5': {...},
        'by_trade_style': {...},    # 新增：breakout_chase vs retest 对比
    }
```

### 4.2 `daily_health_check(date)`

```python
def daily_health_check(check_date: date, portfolio_id: int = None) -> dict:
    """
    当日体检。生成 warnings。
    
    指标：
    - 订单数
    - DTE 分布
    - 开盘第一小时订单数
    - 标的集中度
    - 单日 pnl 估算
    - Regime Score（JOIN regime_scores 表）
    - LEAP 新增笔数（Phase 1+ 关注）
    
    warnings 触发：
    - 订单数 > 20
    - 0-3DTE > 70%
    - 开盘 1 小时 > 30%
    - 单票占比 > 40%
    - 连续同标的 > 5 次
    """
    # 实现略，回参考 analytics 章节测试用例
    return {
        'date': check_date,
        'total_orders': ...,
        'dte_distribution': {...},
        'warnings': [...],
        'regime_score': ...,
    }
```

### 4.3 `compute_portfolio_stats(days)`

为原项目 `get_portfolio_snapshot` 工具**补充期权维度**：

```python
def compute_portfolio_stats(portfolio_id: int, days: int = 90) -> dict:
    """
    供 Agent 工具 get_portfolio_snapshot 扩展使用。
    
    原有字段保留（positions / events / risks），新增：
    - option_positions（期权持仓细分）
    - dte_exposure（按 DTE 分组的金额暴露）
    - greek_summary（总 delta / total theta per day）
    - journal_stats_90d（90 天胜率 / pnl 等）
    """
```

---

## 5. AI 月度复盘

**文件**：`src/journal/monthly_review.py`

### 5.1 数据聚合

```python
def collect_monthly_stats(year: int, month: int) -> dict:
    """组织月度统计 JSON。"""
    # 从 trades + health_checks + regime_scores 聚合
    return {
        'year_month': f'{year}-{month:02d}',
        'basic': {
            'total_trades': ...,
            'win_rate': ...,
            'profit_factor': ...,
            'total_pnl': ...,
        },
        'reality_test': {...},
        'by_dte_bucket': [...],
        'by_trade_style': [...],
        'best_5': [...],
        'worst_5': [...],
        'regime_correlation': {...},   # 当月 Regime Score vs 日 pnl 的相关性
        'phase_context': {...},
        'prev_month_comparison': {...},
    }
```

### 5.2 Prompt

用原项目的 LiteLLM 路由（沿用 `LITELLM_MODEL`）：

```python
def generate_review(year: int, month: int, send: bool = False) -> str:
    import litellm
    
    stats = collect_monthly_stats(year, month)
    phase = get_current_phase()  # 从 phase_state 表
    
    prompt = render_template(
        'monthly_retrospective.md.j2',
        stats=stats,
        phase=phase,
        trading_style=os.getenv('PERSONAL_TRADING_STYLE', ''),
    )
    
    response = litellm.completion(
        model=os.getenv('LITELLM_MODEL'),
        messages=[
            {'role': 'system', 'content': MONTHLY_REVIEW_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.3,
    )
    
    markdown = response.choices[0].message.content
    
    # 存 DB
    save_monthly_review(year, month, phase, stats, markdown)
    
    if send:
        from bot.telegram_bot import send_message
        send_message(markdown[:4000])  # Telegram 单条限 4096
    
    return markdown
```

### 5.3 Prompt 内容（`templates/monthly_retrospective.md.j2`）

```jinja
# 月度复盘：{{ stats.year_month }}

你是严苛的美股期权交易教练。基于下面数据，对用户 {{ stats.year_month }} 做行为复盘。

## 用户当前 Phase
Phase {{ phase.phase }}（从 {{ phase.started }} 开始）

Phase 目标：
{% if phase.phase == 0 %}
- Phase 0 「现状清醒期」：不改变交易方式，只用数据看清自己
- 关键 KPI：Reality Test 结果、DTE 分布、开盘时段占比
{% elif phase.phase == 1 %}
- Phase 1 「认知升级期」：开始 Shadow Trades 和 LEAP Explorer，减频
- 关键 KPI：Shadow vs 实盘对比、0-3DTE 占比下降
{% elif phase.phase == 2 %}
- Phase 2 「策略混合期」：40/40/20 桶分配
- 关键 KPI：各桶回报、挪仓越界次数
{% else %}
- Phase 3 「核心持仓期」：长期持仓为主
- 关键 KPI：年化回报、基本面理解深度
{% endif %}

## 用户风格声明
{{ trading_style }}

## 月度数据
{{ stats | tojson(indent=2) }}

## 输出要求

中文 Markdown，1500-2500 字。必须包含：

### 一、数据快照（用户本月硬事实）
### 二、Reality Test 铁证
  - 去掉 Top 5 盈亏：${{ stats.reality_test.remove_top_5 }}
  - 去掉 Top 10 盈亏：${{ stats.reality_test.remove_top_10 }}
  - 按 DTE bucket 胜率：哪个真正在赚钱？
  - 按 trade_style 胜率：chase vs retest 哪个更赢？
  - 结论：用户的 edge 来自哪里？（具体数据回答）
### 三、最好 5 笔 vs 最差 5 笔共同特征
### 四、重复错误模式（识别 2-3 条，具体交易 ID 引用）
### 五、与上月对比
  - 0-3DTE 占比变化
  - 开盘 30 分钟下单占比变化
  - LEAP / 正股仓位变化
### 六、Phase 目标进度
  - 按 Phase 退出条件逐条评估
  - 距离下一 Phase 还差什么
### 七、下月 3 条可执行规则
  - 不是"继续努力"，是"禁止 X" / "必须做 Y" 具体规则
  - 每条规则带"怎么衡量下月是否做到"

严格要求：
- 不客套不鼓励
- 数字精确引用
- 指出问题不美化
- 禁止"加油"、"恭喜"、"很棒" 之类的词
- 允许使用严厉但尊重的语气
```

---

## 6. API 端点

**文件**：`api/routers/journal.py`

```python
from fastapi import APIRouter, HTTPException, UploadFile, File
from datetime import date

router = APIRouter(prefix='/api/v1/journal', tags=['journal'])


@router.post('/upload')
async def upload_csv(file: UploadFile = File(...), broker: str = 'moomoo_us'):
    """手动上传 CSV 入库（绕过 folder watcher）。"""
    content = await file.read()
    from src.journal.storage import import_csv_content, rebuild_trades
    r = import_csv_content(content, broker=broker, source=file.filename)
    rebuild_trades()
    return r


@router.get('/trades')
async def list_trades(
    underlying: str | None = None,
    dte_bucket: str | None = None,
    trade_style: str | None = None,
    status: str = 'closed',
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
    offset: int = 0,
):
    from src.journal.storage import query_trades
    return query_trades(**locals())


@router.get('/trades/{trade_id}')
async def get_trade(trade_id: int):
    from src.journal.storage import get_trade_detail
    r = get_trade_detail(trade_id)
    if not r:
        raise HTTPException(404)
    return r


@router.patch('/trades/{trade_id}')
async def update_trade(trade_id: int, payload: dict):
    from src.journal.storage import update_trade_user_fields
    return update_trade_user_fields(trade_id, payload)


@router.post('/trades/{trade_id}/reanalyze')
async def reanalyze(trade_id: int):
    from src.journal.ai_analyzer import analyze_trade
    return analyze_trade(trade_id, force=True)


@router.get('/reality-test')
async def reality_test_endpoint(days: int = 30):
    from src.journal.analytics import reality_test
    return reality_test(period_days=days)


@router.get('/health-check')
async def health_check_endpoint(target_date: date | None = None):
    from src.journal.analytics import daily_health_check
    return daily_health_check(target_date or date.today())


@router.get('/reviews/{year}/{month}')
async def get_review(year: int, month: int):
    from src.journal.storage import get_monthly_review
    r = get_monthly_review(year, month)
    if not r:
        raise HTTPException(404)
    return r


@router.post('/reviews/{year}/{month}/generate')
async def generate_review(year: int, month: int, send: bool = False):
    from src.journal.monthly_review import generate_review as _gen
    md = _gen(year, month, send=send)
    return {'markdown': md}


@router.get('/stats/overview')
async def stats_overview(days: int = 90):
    from src.journal.analytics import compute_portfolio_stats
    return compute_portfolio_stats(portfolio_id=None, days=days)
```

---

## 7. Agent Tool 扩展

**修改**：`src/agent/tools/get_portfolio_snapshot.py`（原项目已有）

在原有返回字段基础上**追加**：

```python
def execute(portfolio_id: int = None, include_journal: bool = True) -> dict:
    # 原有逻辑保留
    base = original_get_portfolio_snapshot(portfolio_id)
    
    if include_journal:
        from src.journal.analytics import compute_portfolio_stats, reality_test, daily_health_check
        base['journal'] = {
            'stats_90d': compute_portfolio_stats(portfolio_id, days=90),
            'reality_test_30d': reality_test(period_days=30, portfolio_id=portfolio_id),
            'today_health': daily_health_check(date.today(), portfolio_id=portfolio_id),
        }
    
    return base
```

**新增工具**：`src/agent/tools/get_journal_snapshot.py`

```python
TOOL_METADATA = {
    'name': 'get_journal_snapshot',
    'description': '获取用户交易 Journal 快照（胜率、DTE 分布、Reality Test 等）',
    'parameters': {...},
}

def execute(days: int = 30, portfolio_id: int = None) -> dict:
    from src.journal.analytics import reality_test, compute_portfolio_stats
    return {
        'reality_test': reality_test(period_days=days, portfolio_id=portfolio_id),
        'stats': compute_portfolio_stats(portfolio_id, days=days),
    }
```

---

## 8. 与原 Portfolio 的兼容性

**关键**：原有 Portfolio 功能（`/portfolio` 页面、`get_portfolio_snapshot` 工具、持仓快照、风控）**全部保留**。

数据层面：
- 原 `portfolio_events` 表加的字段都有默认值，老数据兼容
- 我们的 `trades` 表是**派生视图**，不影响 events 真实数据
- 同一个 `portfolios` 可以既有原来的人工录入事件，也有我们的 CSV 导入事件，互不冲突

UI 层面：
- `/portfolio` 主页不变
- 新增 tabs：`/portfolio?tab=journal` / `/portfolio?tab=reality-test` / `/portfolio?tab=monthly-review`

---

## 9. Week 1 Sprint 要做的事（Journal 部分）

按 `03_MIGRATION_PLAN.md` Week 1 要求，Journal 模块必须完成：

- [ ] `src/options/occ_parser.py`（见 04 文档）
- [ ] `src/journal/brokers/moomoo_us.py`
- [ ] `src/journal/matcher.py`
- [ ] `src/journal/analytics.py`（reality_test + health_check）
- [ ] `src/journal/storage.py`（DB CRUD）
- [ ] 7 个 FIFO 配对测试全过
- [ ] CLI `import` + `rebuild-trades` + `health-check` 能跑
- [ ] 用户真实 CSV（735 行）端到端跑通
- [ ] `/api/v1/journal/*` 端点通

详细 Day 1-7 拆解在 `12_WEEK_1_SPRINT.md`。

---

## 10. 我确实不做的事

- ❌ 不做"多账户合并" — 只一个 `portfolios.id` 就够了
- ❌ 不做"跨券商统一账户" — 只做 Moomoo US，将来要接 IBKR 再加 broker
- ❌ 不做"历史分红复权" — 美股期权用不到
- ❌ 不做"税务计算" — 超出范围（wash sale 等）
- ❌ 不做"实时持仓刷新" — CSV 是准 T+0，够用

---

## Batch 2 交付结束

本批产出了 04 + 05，期权支持 + Journal 模块的完整设计。下一批 Batch 3 会产出：

- `06_REGIME_CLASSIFIER.md` — 市场环境评分（六维度 + 晨报 + 作为 skill 接入 Agent）
- `07_BREAKOUT_FILTER.md` — 突破过滤（四层架构 + Journal 字段联动）
- `08_AGENT_SKILL_REGISTRY.md` — 把 option_trader / leap_explorer / trend_follower 三个新 skill 做成 `strategies/*.yaml` + `SKILL.md` bundle

告诉我"继续 Batch 3"即可。

或者如果你想先看看这一批有没有根本性问题，打开 Portfolio 页面现有代码对照一下第 1 节的表结构扩展是否合理，有异议告诉我。
