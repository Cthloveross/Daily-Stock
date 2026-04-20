# Phase 0 Week 1 Sprint Plan

> **文档定位**：Phase 0 的第一周，Day 1 到 Day 7 的逐日任务分解。  
> **前置**：已完成 SETUP_CHECKLIST.md 的所有准备工作  
> **目标**：Week 1 结束时，Journal 能吃 CSV + 生成第一份 Monthly Review。Regime Classifier 原型能跑但不上线

---

## 总览

```
Day 1 (周一) — 基础设施
Day 2 (周二) — CSV 解析 + OCC 期权代码
Day 3 (周三) — FIFO 配对引擎
Day 4 (周四) — Reality Test + Daily Health Check
Day 5 (周五) — Regime Classifier 原型（只跑不上线）
Day 6 (周六) — 前端 Journal 页面 + Monthly Review
Day 7 (周日) — 第一份 Monthly Review 生成 + 周复盘
```

**每日工作量预估**：3-5 小时（按你在校的节奏，周末可以 6-8 小时）

**如果某天做不完，顺延。不要赶进度**。Phase 0 的核心是"做得对"而不是"做得快"。

---

## Day 1（周一）— 基础设施

### 目标
项目跑起来，DB schema 建好，CSV 能导入（最基础版）。

### 任务清单

#### T1.1 — 项目分支（30 分钟）

```bash
cd ~/Desktop/daily_stock_analysis
git checkout -b phase-0-journal
```

创建新分支，所有 Phase 0 代码在这个分支做。不直接动 main。

#### T1.2 — 目录结构（30 分钟）

```bash
mkdir -p src/journal src/regime src/api/routers
mkdir -p data migrations
touch src/journal/__init__.py
touch src/regime/__init__.py
touch src/api/__init__.py
touch src/api/routers/__init__.py
```

#### T1.3 — 依赖安装（30 分钟）

```bash
source .venv/bin/activate
pip install -U yfinance fastapi uvicorn pydantic pandas \
    sqlalchemy python-dotenv requests watchdog feedparser \
    python-telegram-bot litellm google-generativeai \
    scipy numpy
pip freeze > requirements.txt
git add requirements.txt && git commit -m "chore: install phase-0 deps"
```

#### T1.4 — DB Schema 初始化（1.5 小时）

创建 `migrations/001_phase0_init.sql`：

```sql
-- 从 JOURNAL_AND_TOOLS_SPEC.md 第四章拷贝所有 CREATE TABLE
-- 然后加 regime_scores 表（从 REGIME_CLASSIFIER_IMPL.md）
```

创建 `scripts/init_db.py`：
```python
import sqlite3
import os

def init():
    db = os.environ.get('DATABASE_URL', 'data/daily_stock.db').replace('sqlite:///', '')
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db)
    with open('migrations/001_phase0_init.sql') as f:
        conn.executescript(f.read())
    conn.close()
    print(f'DB initialized at {db}')

if __name__ == '__main__':
    init()
```

```bash
python scripts/init_db.py
# 确认 data/daily_stock.db 生成
```

#### T1.5 — .env 加载与 config 模块（30 分钟）

创建 `src/config.py`：
```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
DB_URL = os.environ.get('DATABASE_URL', f'sqlite:///{DATA_DIR}/daily_stock.db')

WATCHLIST = os.environ.get('WATCHLIST', '').split(',') if os.environ.get('WATCHLIST') else []
CURRENT_PHASE = int(os.environ.get('CURRENT_PHASE', '0'))
TRADING_STYLE_PROMPT = os.environ.get('TRADING_STYLE_PROMPT', '')

INBOX_DIR  = Path(os.environ.get('INBOX_DIR', '~/Daily-Stock-Inbox')).expanduser()
PROCESSED_DIR = Path(os.environ.get('PROCESSED_DIR', '~/Daily-Stock-Processed')).expanduser()

# API keys
APCA_KEY = os.environ.get('APCA_API_KEY_ID')
APCA_SECRET = os.environ.get('APCA_API_SECRET_KEY')
FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY')
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT = os.environ.get('TELEGRAM_CHAT_ID')
```

```bash
python -c "from src.config import *; print(WATCHLIST, CURRENT_PHASE)"
# 验证输出
```

#### T1.6 — Commit

```bash
git add src/config.py scripts/init_db.py migrations/
git commit -m "feat(phase-0): init db schema and config module"
```

### Day 1 验收标准

- [ ] `data/daily_stock.db` 存在，能 `sqlite3 data/daily_stock.db ".schema"` 看到所有表
- [ ] `python -c "from src.config import *"` 不报错
- [ ] `~/Daily-Stock-Inbox/` 和 `~/Daily-Stock-Processed/` 都存在
- [ ] `.env` 里所有必填项都填了

---

## Day 2（周二）— CSV 解析 + OCC 期权代码

### 目标
能把用户的 Moomoo CSV（真实文件）解析入库。期权 symbol 正确识别。

### 任务清单

#### T2.1 — OCC Parser（1 小时）

创建 `src/journal/instruments.py`：
```python
# 从 JOURNAL_AND_TOOLS_SPEC.md 第二章拷贝 parse_symbol() 完整代码
```

创建 `src/journal/tests/test_instruments.py`：
```python
from datetime import date
from src.journal.instruments import parse_symbol, Instrument

def test_option_call():
    r = parse_symbol('AMD260417C275000')
    assert r.is_option
    assert r.underlying == 'AMD'
    assert r.expiry == date(2026, 4, 17)
    assert r.right == 'C'
    assert r.strike == 275.0

def test_option_put():
    r = parse_symbol('TSLA260417P382500')
    assert r.right == 'P'
    assert r.strike == 382.5

def test_option_high_strike():
    r = parse_symbol('SNDK260417C920000')
    assert r.strike == 920.0

def test_stock():
    r = parse_symbol('AAPL')
    assert not r.is_option
    assert r.underlying == 'AAPL'

def test_empty():
    r = parse_symbol('')
    assert not r.is_option
```

```bash
pytest src/journal/tests/test_instruments.py -v
```

#### T2.2 — CSV Row Merger（1.5 小时）

创建 `src/journal/csv_parser.py`：
```python
# 从 JOURNAL_AND_TOOLS_SPEC.md 第三章拷贝 merge_fill_rows() 完整代码
# 加 parse_moomoo_csv() 作为上层入口

import csv
import io
from datetime import datetime
from typing import Any

def parse_moomoo_csv(content: bytes) -> list[dict]:
    """解析 Moomoo CSV 字节流，返回 orders 列表。"""
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    
    orders = merge_fill_rows(rows)
    # 补充衍生字段
    for o in orders:
        total_qty = sum(f['qty'] for f in o.get('fills', []))
        total_value = sum(f['qty'] * f['price'] for f in o.get('fills', []))
        o['filled_qty'] = total_qty
        o['avg_fill_price'] = total_value / total_qty if total_qty else 0
        o['first_fill_time'] = min((f['time'] for f in o['fills'] if f['time']), default=None)
        o['last_fill_time'] = max((f['time'] for f in o['fills'] if f['time']), default=None)
    
    return orders
```

测试用真实 CSV：
```bash
python -c "
from src.journal.csv_parser import parse_moomoo_csv
with open('/path/to/History-xxx.csv', 'rb') as f:
    orders = parse_moomoo_csv(f.read())
print(f'Parsed {len(orders)} orders')
print(orders[0])  # 看第一个订单结构
"
# 期望：Parsed 735 orders (基于你真实 CSV)
```

#### T2.3 — 入库逻辑（1.5 小时）

创建 `src/journal/storage.py`：
```python
import hashlib
import json
import sqlite3
from datetime import datetime
from src.config import DB_URL
from src.journal.instruments import parse_symbol


def _db():
    path = DB_URL.replace('sqlite:///', '')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_instrument(conn, raw_symbol: str) -> int:
    inst = parse_symbol(raw_symbol)
    cur = conn.cursor()
    cur.execute('SELECT id FROM instruments WHERE raw_symbol = ?', (raw_symbol,))
    row = cur.fetchone()
    if row:
        return row['id']
    cur.execute('''
        INSERT INTO instruments (raw_symbol, is_option, underlying, expiry, right, strike)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        raw_symbol, inst.is_option, inst.underlying,
        inst.expiry.isoformat() if inst.expiry else None,
        inst.right, inst.strike,
    ))
    conn.commit()
    return cur.lastrowid


def compute_order_external_id(order: dict) -> str:
    """用 symbol + side + order_time + qty + price 作 hash。"""
    key = (
        f'{order["symbol"]}|{order["side"]}|'
        f'{order["order_time"].isoformat()}|{order["order_qty"]}|{order["order_price"]}'
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def import_csv(file_path: str) -> dict:
    """CSV 文件 → orders 入库。"""
    with open(file_path, 'rb') as f:
        content = f.read()
    sha = hashlib.sha256(content).hexdigest()
    
    conn = _db()
    cur = conn.cursor()
    
    # 幂等检查
    cur.execute('SELECT id FROM trade_imports WHERE csv_sha256 = ?', (sha,))
    if cur.fetchone():
        conn.close()
        return {'status': 'skipped', 'reason': 'duplicate_csv'}
    
    from src.journal.csv_parser import parse_moomoo_csv
    orders = parse_moomoo_csv(content)
    
    # 创建 import 记录
    cur.execute('''
        INSERT INTO trade_imports (source_path, csv_sha256, rows_total, status)
        VALUES (?, ?, ?, ?)
    ''', (file_path, sha, len(orders), 'processing'))
    import_id = cur.lastrowid
    conn.commit()
    
    rows_imported = 0
    rows_skipped = 0
    
    for o in orders:
        if o.get('status') != 'Filled':
            continue
        inst_id = upsert_instrument(conn, o['symbol'])
        ext_id = compute_order_external_id(o)
        
        try:
            cur.execute('''
                INSERT INTO orders (
                    import_id, external_id, instrument_id, side,
                    order_qty, order_price, order_amount, status,
                    filled_qty, avg_fill_price, order_time,
                    first_fill_time, last_fill_time,
                    order_type, session,
                    commission, platform_fees, trading_activity_fees,
                    options_regulatory_fees, occ_fees, contract_fees,
                    sec_fees, settlement_fees, total_fee,
                    currency, raw_row
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                import_id, ext_id, inst_id, o['side'],
                o['order_qty'], o['order_price'], o['order_amount'], o['status'],
                o['filled_qty'], o['avg_fill_price'],
                o['order_time'].isoformat() if o['order_time'] else None,
                o['first_fill_time'].isoformat() if o['first_fill_time'] else None,
                o['last_fill_time'].isoformat() if o['last_fill_time'] else None,
                o.get('order_type'), o.get('session'),
                o.get('commission', 0), o.get('platform_fee', 0),
                o.get('trading_activity_fee', 0), o.get('options_regulatory_fee', 0),
                o.get('occ_fee', 0), o.get('contract_fee', 0),
                o.get('sec_fee', 0), o.get('settlement_fee', 0),
                o.get('total_fee', 0),
                'USD', json.dumps(o, default=str),
            ))
            rows_imported += 1
        except sqlite3.IntegrityError:
            rows_skipped += 1
    
    cur.execute('''
        UPDATE trade_imports SET rows_imported=?, rows_skipped=?, status=?
        WHERE id=?
    ''', (rows_imported, rows_skipped, 'success', import_id))
    conn.commit()
    conn.close()
    
    return {
        'status': 'success',
        'import_id': import_id,
        'rows_imported': rows_imported,
        'rows_skipped': rows_skipped,
    }
```

#### T2.4 — 跑一次真实 CSV（30 分钟）

```bash
# 把你的真实 CSV 放 ~/Daily-Stock-Inbox
cp ~/Downloads/History-Margin_Account_xxx.csv ~/Daily-Stock-Inbox/

# 手动导入
python -c "
from src.journal.storage import import_csv
result = import_csv('~/Daily-Stock-Inbox/History-Margin_Account_xxx.csv')
print(result)
"
# 期望：rows_imported 约 735
```

验证：
```bash
sqlite3 data/daily_stock.db <<SQL
SELECT COUNT(*) FROM orders;
SELECT COUNT(*) FROM instruments WHERE is_option=1;
SELECT underlying, COUNT(*) FROM instruments WHERE is_option=1 GROUP BY underlying ORDER BY COUNT(*) DESC LIMIT 10;
SQL
```

#### T2.5 — Commit

```bash
git add src/journal/ src/api/
git commit -m "feat(phase-0/journal): csv parser + storage with 735 orders verified"
```

### Day 2 验收标准

- [ ] 单元测试全过 (`pytest src/journal/tests/`)
- [ ] 真实 CSV 导入成功，`orders` 表有约 735 行
- [ ] `instruments` 表能看到 140+ 个不同 contract
- [ ] Top underlying 是 MU / TSLA / PLTR（和体检报告一致）

---

## Day 3（周三）— FIFO 配对引擎

### 目标
orders → trades。能区分 open / closed trade，能算 pnl。

### 任务清单

#### T3.1 — FIFO 算法实现（2 小时）

创建 `src/journal/matcher.py`，从 `JOURNAL_AND_TOOLS_SPEC.md` 第五章拷贝 `match_orders_fifo` 完整代码。

#### T3.2 — 单元测试（1.5 小时）

创建 `src/journal/tests/test_matcher.py`，覆盖 7 个场景：

```python
from datetime import datetime, timedelta
from src.journal.matcher import match_orders_fifo

def make_order(inst_id, side, qty, price, t):
    return {
        'id': hash((inst_id, side, t.isoformat())),
        'instrument_id': inst_id,
        'side': side,
        'filled_qty': qty,
        'avg_fill_price': price,
        'order_time': t,
        'total_fee': 1.0,
    }

def test_simple_pair():
    t0 = datetime(2026, 4, 1, 10, 0)
    orders = [
        make_order(1, 'Buy', 10, 5.0, t0),
        make_order(1, 'Sell', 10, 6.0, t0 + timedelta(hours=1)),
    ]
    trades = match_orders_fifo(orders)
    assert len(trades) == 1
    assert trades[0]['status'] == 'closed'
    assert trades[0]['quantity'] == 10
    assert trades[0]['direction'] == 'long'

def test_scale_in_scale_out():
    t0 = datetime(2026, 4, 1, 10, 0)
    orders = [
        make_order(1, 'Buy', 5, 5.0, t0),
        make_order(1, 'Buy', 5, 5.5, t0 + timedelta(minutes=10)),
        make_order(1, 'Sell', 10, 6.0, t0 + timedelta(hours=1)),
    ]
    trades = match_orders_fifo(orders)
    closed = [t for t in trades if t['status'] == 'closed']
    assert len(closed) == 2  # 按 FIFO 拆两笔 trade

def test_partial_exits():
    t0 = datetime(2026, 4, 1, 10, 0)
    orders = [
        make_order(1, 'Buy', 10, 5.0, t0),
        make_order(1, 'Sell', 3, 6.0, t0 + timedelta(minutes=30)),
        make_order(1, 'Sell', 7, 7.0, t0 + timedelta(hours=1)),
    ]
    trades = match_orders_fifo(orders)
    closed = [t for t in trades if t['status'] == 'closed']
    assert len(closed) == 2
    assert closed[0]['quantity'] == 3
    assert closed[1]['quantity'] == 7

def test_open_trade():
    t0 = datetime(2026, 4, 1, 10, 0)
    orders = [make_order(1, 'Buy', 10, 5.0, t0)]
    trades = match_orders_fifo(orders)
    assert len(trades) == 1
    assert trades[0]['status'] == 'open'

def test_short_trade():
    t0 = datetime(2026, 4, 1, 10, 0)
    orders = [
        make_order(1, 'Sell', 10, 6.0, t0),
        make_order(1, 'Buy', 10, 5.0, t0 + timedelta(hours=1)),
    ]
    trades = match_orders_fifo(orders)
    closed = [t for t in trades if t['status'] == 'closed']
    assert len(closed) == 1
    assert closed[0]['direction'] == 'short'

def test_flip_long_to_short():
    t0 = datetime(2026, 4, 1, 10, 0)
    orders = [
        make_order(1, 'Buy', 10, 5.0, t0),
        make_order(1, 'Sell', 20, 6.0, t0 + timedelta(hours=1)),
    ]
    trades = match_orders_fifo(orders)
    closed = [t for t in trades if t['status'] == 'closed']
    opens = [t for t in trades if t['status'] == 'open']
    assert len(closed) == 1 and len(opens) == 1
    assert closed[0]['quantity'] == 10
    assert opens[0]['direction'] == 'short' and opens[0]['quantity'] == 10
```

```bash
pytest src/journal/tests/test_matcher.py -v
```

#### T3.3 — 入库 trades（1 小时）

在 `src/journal/storage.py` 加 `persist_trades`：
```python
def persist_trades(conn, trades: list[dict]):
    cur = conn.cursor()
    for t in trades:
        cur.execute('''
            INSERT INTO trades (
                instrument_id, direction, status, quantity,
                avg_entry_price, avg_exit_price,
                entry_time, exit_time, hold_seconds,
                is_option, days_to_expiry_at_entry,
                pnl_gross, total_fee, pnl_net, pnl_pct,
                dte_bucket, open_order_ids, close_order_ids
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            t['instrument_id'], t['direction'], t['status'], t['quantity'],
            t['avg_entry_price'], t.get('avg_exit_price'),
            t['entry_time'].isoformat() if hasattr(t['entry_time'], 'isoformat') else t['entry_time'],
            t.get('exit_time').isoformat() if t.get('exit_time') else None,
            t.get('hold_seconds'),
            is_option_instrument(conn, t['instrument_id']),
            compute_dte_at_entry(conn, t),
            t.get('pnl_gross'), t.get('total_fee', 0), t.get('pnl_net'),
            compute_pnl_pct(t),
            compute_dte_bucket_from_trade(conn, t),
            json.dumps(t.get('open_order_ids', [])),
            json.dumps(t.get('close_order_ids', [])),
        ))
    conn.commit()


def rebuild_trades_table():
    """从 orders 重建整个 trades 表。幂等。"""
    conn = _db()
    cur = conn.cursor()
    cur.execute('DELETE FROM trades')
    
    # 拉所有 orders
    cur.execute('SELECT * FROM orders ORDER BY order_time')
    orders = [dict(row) for row in cur.fetchall()]
    
    # 字段转换
    for o in orders:
        o['order_time'] = datetime.fromisoformat(o['order_time'])
    
    from src.journal.matcher import match_orders_fifo
    trades = match_orders_fifo(orders)
    persist_trades(conn, trades)
    conn.close()
    return len(trades)
```

#### T3.4 — 重建并验证（30 分钟）

```bash
python -c "
from src.journal.storage import rebuild_trades_table
n = rebuild_trades_table()
print(f'Built {n} trades')
"

sqlite3 data/daily_stock.db <<SQL
SELECT status, COUNT(*) FROM trades GROUP BY status;
SELECT dte_bucket, COUNT(*), ROUND(SUM(pnl_net), 2) 
FROM trades WHERE status='closed' GROUP BY dte_bucket;
SELECT underlying, COUNT(*), ROUND(SUM(pnl_net), 2) 
FROM trades t JOIN instruments i ON t.instrument_id=i.id
WHERE status='closed'
GROUP BY underlying ORDER BY SUM(pnl_net) DESC LIMIT 5;
SQL
```

期望输出：
- closed trades ~400-500 笔（比 277 symbol-level 配对多，因为分批出场拆成多笔）
- 0DTE / 1-3DTE / LEAP 分布符合体检报告
- Top 5 underlying 的 pnl 应该能看到之前体检报告里 TSLA / AMD / MSFT 的大赢家

#### T3.5 — Commit

```bash
git add src/journal/matcher.py src/journal/tests/
git commit -m "feat(phase-0/journal): fifo matcher + trades persistence"
```

### Day 3 验收标准

- [ ] 7 个 matcher 测试全过
- [ ] `rebuild_trades_table` 运行成功
- [ ] trades 表有 400+ 行
- [ ] DTE 分布统计符合你的实际情况

---

## Day 4（周四）— Reality Test + Daily Health Check

### 目标
能算 Reality Test 指标。Daily Health Check 能推 Telegram。

### 任务清单

#### T4.1 — Reality Test 函数（2 小时）

创建 `src/journal/analytics.py`，从 `JOURNAL_AND_TOOLS_SPEC.md` 第六章拷贝 `reality_test` 代码。

手动跑一次验证：
```bash
python -c "
from src.journal.analytics import reality_test
r = reality_test(period_days=90)
import json
print(json.dumps(r, indent=2, default=str))
"
```

**对照体检报告**：`remove_top_5` 应该接近 +$2,257（几乎打平）。

#### T4.2 — Daily Health Check 函数（1.5 小时）

从 `JOURNAL_AND_TOOLS_SPEC.md` 第七章拷贝 `daily_health_check` 到 `src/journal/analytics.py`。

测试：
```bash
python -c "
from datetime import date
from src.journal.analytics import daily_health_check
r = daily_health_check(date(2026, 4, 16))  # 你 CSV 最后一天
import json
print(json.dumps(r, indent=2, default=str))
"
```

#### T4.3 — Telegram 推送（1 小时）

创建 `src/common/telegram.py`：
```python
import os
import requests

def send_telegram(text: str, parse_mode: str = 'HTML'):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not token:
        print(text)
        return
    requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat, 'text': text, 'parse_mode': parse_mode},
        timeout=10,
    )


def format_daily_health(check: dict) -> str:
    # 见 EVOLUTION_ROADMAP.md 的 Daily Health Check 示例格式
    ...
```

#### T4.4 — CLI 入口（30 分钟）

创建 `src/journal/cli.py`：
```python
import argparse
from datetime import date

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd')
    
    # import
    imp = sub.add_parser('import')
    imp.add_argument('file')
    
    # health
    hc = sub.add_parser('health-check')
    hc.add_argument('--date', default=None)
    hc.add_argument('--send', action='store_true')
    
    # rebuild
    sub.add_parser('rebuild-trades')
    
    # reality-test
    rt = sub.add_parser('reality-test')
    rt.add_argument('--days', type=int, default=30)
    
    args = p.parse_args()
    
    if args.cmd == 'import':
        from src.journal.storage import import_csv, rebuild_trades_table
        r = import_csv(args.file)
        print(r)
        print(f'Rebuilding trades... got {rebuild_trades_table()} trades')
    
    elif args.cmd == 'health-check':
        from src.journal.analytics import daily_health_check
        from src.common.telegram import send_telegram, format_daily_health
        d = date.fromisoformat(args.date) if args.date else date.today()
        result = daily_health_check(d)
        msg = format_daily_health(result)
        if args.send:
            send_telegram(msg)
        else:
            print(msg)
    
    elif args.cmd == 'rebuild-trades':
        from src.journal.storage import rebuild_trades_table
        print(f'Built {rebuild_trades_table()} trades')
    
    elif args.cmd == 'reality-test':
        from src.journal.analytics import reality_test
        import json
        r = reality_test(period_days=args.days)
        print(json.dumps(r, indent=2, default=str))

if __name__ == '__main__':
    main()
```

```bash
# 测试
python -m src.journal.cli health-check --date 2026-04-16
python -m src.journal.cli health-check --date 2026-04-16 --send
# 检查 Telegram 收到消息
```

#### T4.5 — Commit

```bash
git add src/journal/analytics.py src/common/ src/journal/cli.py
git commit -m "feat(phase-0/journal): reality test + daily health check + telegram"
```

### Day 4 验收标准

- [ ] `reality-test --days 90` 返回数字，`remove_top_5` 接近 $2,257
- [ ] `health-check --date 2026-04-16` 能正确统计当日 61 笔交易
- [ ] Telegram 收到格式化的消息
- [ ] warnings 列表能正确识别 overtrading 等问题

---

## Day 5（周五）— Regime Classifier 原型

### 目标
跑起来，能给历史日期打分。**不上线推送**。

### 任务清单

#### T5.1 — 全部拷贝代码（2 小时）

从 `REGIME_CLASSIFIER_IMPL.md` 拷贝：
- `src/regime/classifier.py`
- `src/regime/data_fetcher.py`
- `src/regime/scorers.py`
- `src/regime/storage.py`
- `src/regime/cli.py`

#### T5.2 — 单元测试（1 小时）

从 IMPL 文档第七章拷贝 `test_scorers.py`。跑：
```bash
pytest src/regime/tests/
```

#### T5.3 — 历史回溯（1 小时）

```bash
# 对你 CSV 覆盖的整个期间跑 Regime
python -m src.regime.cli --backfill 2026-01-15 2026-04-16
# 期望：每天打印分数
```

这会在 `regime_scores` 表填 50+ 天数据。

#### T5.4 — 看看分数和实际盈亏的相关性（1 小时）

```bash
sqlite3 data/daily_stock.db <<SQL
.headers on
.mode column
SELECT 
    r.date,
    r.score,
    r.label,
    COUNT(t.id) AS n_trades,
    ROUND(SUM(t.pnl_net), 2) AS daily_pnl
FROM regime_scores r
LEFT JOIN trades t ON DATE(t.entry_time) = r.date AND t.status = 'closed'
GROUP BY r.date
ORDER BY r.date;
SQL
```

观察：
- 最赚钱的几天是否 Regime Score 高？（如果 TSLA +$18K 那天 score >= 70 → 系统有效的初步证据）
- 最赔钱的几天是否 Regime Score 低？（或者是高分日但你执行走样了）

**Phase 0 Week 1 不强求系统完美，只要能跑起来、能打出分数就成功**。

#### T5.5 — Commit

```bash
git add src/regime/
git commit -m "feat(phase-0/regime): classifier prototype with historical backfill"
```

### Day 5 验收标准

- [ ] 至少 40 个交易日有 regime_score 记录
- [ ] TSLA +$18K 那天的 Regime Score 能查到（看看是多少）
- [ ] 测试全过
- [ ] 你看了分数和 pnl 的对照表，有初步感觉

---

## Day 6（周六）— 前端 Journal 页面

### 目标
能在浏览器看到 Journal 数据。基础的几个组件跑起来。

### 任务清单

#### T6.1 — FastAPI 骨架（1 小时）

创建 `src/api/main.py`：
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routers import journal

app = FastAPI(title='Daily-Stock API')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)
app.include_router(journal.router)

@app.get('/health')
def health():
    return {'status': 'ok'}
```

#### T6.2 — Journal API Router（2 小时）

创建 `src/api/routers/journal.py`：
```python
from fastapi import APIRouter, HTTPException
from datetime import date
import sqlite3
import json
from src.config import DB_URL

router = APIRouter(prefix='/api/v1/journal', tags=['journal'])


def _db():
    path = DB_URL.replace('sqlite:///', '')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@router.get('/trades')
def list_trades(
    underlying: str | None = None,
    dte_bucket: str | None = None,
    status: str = 'closed',
    limit: int = 100,
):
    conn = _db()
    cur = conn.cursor()
    sql = '''
        SELECT t.*, i.raw_symbol, i.underlying, i.expiry, i.right, i.strike
        FROM trades t JOIN instruments i ON t.instrument_id = i.id
        WHERE t.status = ?
    '''
    params = [status]
    if underlying:
        sql += ' AND i.underlying = ?'
        params.append(underlying)
    if dte_bucket:
        sql += ' AND t.dte_bucket = ?'
        params.append(dte_bucket)
    sql += ' ORDER BY t.entry_time DESC LIMIT ?'
    params.append(limit)
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {'trades': rows}


@router.get('/reality-test')
def reality_test_endpoint(days: int = 30):
    from src.journal.analytics import reality_test
    return reality_test(period_days=days)


@router.get('/health-check/{target_date}')
def health_check_endpoint(target_date: date):
    from src.journal.analytics import daily_health_check
    return daily_health_check(target_date)


@router.get('/stats/overview')
def stats_overview(days: int = 90):
    """基本统计，前端 Overview 用。"""
    conn = _db()
    cur = conn.cursor()
    # 按 DTE bucket 分组
    cur.execute('''
        SELECT dte_bucket, COUNT(*) AS n, 
               SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(pnl_net), 2) AS pnl
        FROM trades
        WHERE status = 'closed' AND entry_time >= date('now', ?) 
        GROUP BY dte_bucket
    ''', (f'-{days} days',))
    by_dte = [dict(r) for r in cur.fetchall()]
    
    cur.execute('''
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(pnl_net), 2) AS pnl
        FROM trades
        WHERE status = 'closed' AND entry_time >= date('now', ?)
    ''', (f'-{days} days',))
    overall = dict(cur.fetchone())
    overall['win_rate'] = overall['wins'] / overall['total'] if overall['total'] else 0
    
    conn.close()
    return {'overall': overall, 'by_dte_bucket': by_dte}
```

跑起来：
```bash
uvicorn src.api.main:app --reload --port 8000
# 浏览器访问 http://localhost:8000/docs 看 Swagger
# 测试 http://localhost:8000/api/v1/journal/stats/overview?days=90
```

#### T6.3 — 前端 Journal 页（2 小时）

如果项目已有 React 前端（v1 文档提到过 `apps/dsa-web`），新建：
```
apps/dsa-web/src/pages/JournalPage.tsx
apps/dsa-web/src/components/journal/OverviewCards.tsx
apps/dsa-web/src/components/journal/RealityTestPanel.tsx
apps/dsa-web/src/components/journal/DTEDistribution.tsx
```

最小可用版本：
```tsx
// JournalPage.tsx
import { useEffect, useState } from 'react'

export function JournalPage() {
  const [stats, setStats] = useState<any>(null)
  const [reality, setReality] = useState<any>(null)
  
  useEffect(() => {
    fetch('/api/v1/journal/stats/overview?days=90').then(r => r.json()).then(setStats)
    fetch('/api/v1/journal/reality-test?days=90').then(r => r.json()).then(setReality)
  }, [])
  
  if (!stats) return <div>Loading...</div>
  
  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-bold">Journal</h1>
      
      <div className="grid grid-cols-3 gap-4">
        <Card title="总交易" value={stats.overall.total} />
        <Card title="胜率" value={`${(stats.overall.win_rate * 100).toFixed(1)}%`} />
        <Card title="盈亏" value={`$${stats.overall.pnl}`} />
      </div>
      
      <section>
        <h2 className="text-xl">DTE 分布</h2>
        <table className="w-full">
          <thead><tr><th>Bucket</th><th>数量</th><th>胜率</th><th>盈亏</th></tr></thead>
          <tbody>
            {stats.by_dte_bucket.map((b: any) => (
              <tr key={b.dte_bucket}>
                <td>{b.dte_bucket}</td>
                <td>{b.n}</td>
                <td>{((b.wins / b.n) * 100).toFixed(1)}%</td>
                <td>${b.pnl}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      
      {reality && (
        <section className="bg-yellow-100 p-4 rounded">
          <h2 className="text-xl">Reality Test</h2>
          <p>全量盈亏：${reality.full_pnl.toFixed(2)}</p>
          <p>去掉 Top 5：${reality.remove_top_5.toFixed(2)}</p>
          <p>去掉 Top 10：${reality.remove_top_10.toFixed(2)}</p>
        </section>
      )}
    </div>
  )
}

function Card({ title, value }: any) {
  return (
    <div className="border rounded p-4">
      <div className="text-sm text-gray-500">{title}</div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  )
}
```

#### T6.4 — Commit

```bash
git add src/api/ apps/dsa-web/
git commit -m "feat(phase-0/api): fastapi journal endpoints + react overview page"
```

### Day 6 验收标准

- [ ] `uvicorn src.api.main:app` 启动成功
- [ ] Swagger 能看到所有端点
- [ ] 浏览器 `/journal` 页面能看到你 3 个月的数据
- [ ] Reality Test 数字醒目显示

---

## Day 7（周日）— 第一份 Monthly Review

### 目标
生成 2026-03 的 AI 月度复盘（用 3 月完整数据）。读完，看 AI 质量。

### 任务清单

#### T7.1 — Monthly Review 生成器（2 小时）

创建 `src/journal/monthly_review.py`：
```python
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from src.config import DB_URL, GEMINI_KEY, TRADING_STYLE_PROMPT
import google.generativeai as genai


def collect_monthly_stats(year: int, month: int) -> dict:
    """聚合上月所有统计。"""
    conn = sqlite3.connect(DB_URL.replace('sqlite:///', ''))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    start = date(year, month, 1).isoformat()
    if month == 12:
        end = date(year + 1, 1, 1).isoformat()
    else:
        end = date(year, month + 1, 1).isoformat()
    
    # 基本统计
    cur.execute('''
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN pnl_net > 0 THEN pnl_net ELSE 0 END) AS gross_wins,
               SUM(CASE WHEN pnl_net <= 0 THEN -pnl_net ELSE 0 END) AS gross_losses,
               SUM(pnl_net) AS total_pnl,
               AVG(pnl_net) AS avg_pnl
        FROM trades WHERE status='closed' AND entry_time >= ? AND entry_time < ?
    ''', (start, end))
    basic = dict(cur.fetchone())
    basic['win_rate'] = basic['wins'] / basic['total'] if basic['total'] else 0
    basic['profit_factor'] = basic['gross_wins'] / basic['gross_losses'] if basic['gross_losses'] else None
    
    # Reality Test
    cur.execute('''
        SELECT pnl_net FROM trades WHERE status='closed' 
        AND entry_time >= ? AND entry_time < ? ORDER BY pnl_net DESC
    ''', (start, end))
    pnls = [r['pnl_net'] for r in cur.fetchall()]
    reality = {
        'full_pnl': sum(pnls),
        'remove_top_5': sum(pnls[5:]),
        'remove_top_10': sum(pnls[10:]),
    }
    
    # DTE 分布
    cur.execute('''
        SELECT dte_bucket, COUNT(*) AS n, 
               AVG(CASE WHEN pnl_net > 0 THEN 1.0 ELSE 0 END) AS win_rate,
               SUM(pnl_net) AS pnl
        FROM trades WHERE status='closed' AND entry_time >= ? AND entry_time < ?
        GROUP BY dte_bucket
    ''', (start, end))
    by_dte = [dict(r) for r in cur.fetchall()]
    
    # Top 5 / Worst 5
    cur.execute('''
        SELECT t.*, i.raw_symbol, i.underlying
        FROM trades t JOIN instruments i ON t.instrument_id=i.id
        WHERE status='closed' AND entry_time >= ? AND entry_time < ?
        ORDER BY pnl_net DESC LIMIT 5
    ''', (start, end))
    best_5 = [dict(r) for r in cur.fetchall()]
    
    cur.execute('''
        SELECT t.*, i.raw_symbol, i.underlying
        FROM trades t JOIN instruments i ON t.instrument_id=i.id
        WHERE status='closed' AND entry_time >= ? AND entry_time < ?
        ORDER BY pnl_net ASC LIMIT 5
    ''', (start, end))
    worst_5 = [dict(r) for r in cur.fetchall()]
    
    conn.close()
    
    return {
        'year_month': f'{year}-{month:02d}',
        'basic': basic,
        'reality_test': reality,
        'by_dte_bucket': by_dte,
        'best_5': best_5,
        'worst_5': worst_5,
    }


def generate_review(year: int, month: int) -> str:
    stats = collect_monthly_stats(year, month)
    
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash-exp')
    
    prompt = f"""
你是严苛的美股期权交易教练。基于下面数据，对用户 **{year}-{month:02d}** 做行为复盘。

# 用户风格声明
{TRADING_STYLE_PROMPT}

# 月度数据（JSON）
{json.dumps(stats, indent=2, default=str)}

# 输出要求（中文 Markdown，1500-2500 字）

必须包含：
## 一、数据快照
## 二、Reality Test 铁证
  - 去掉 Top 5 盈亏
  - 去掉 Top 10 盈亏
  - 按 DTE bucket 分组的胜率
  - 结论：用户的 edge 来自哪里？
## 三、最好 5 笔 vs 最差 5 笔的共同特征
## 四、重复错误模式（识别 2-3 条）
## 五、Phase 0 目标评估
  - 本月是否在"用数据看清自己"？
  - 有哪些觉醒？
## 六、下月 3 条可执行规则
  - 不是"加油"，是具体规则

严格要求：
- 不客套不鼓励
- 数字精确引用
- 指出问题不美化
"""
    
    response = model.generate_content(prompt)
    markdown = response.text
    
    # 保存到 DB
    conn = sqlite3.connect(DB_URL.replace('sqlite:///', ''))
    conn.execute('''
        INSERT OR REPLACE INTO monthly_reviews 
          (year_month, current_phase, stats_json, review_markdown)
        VALUES (?, ?, ?, ?)
    ''', (stats['year_month'], 0, json.dumps(stats, default=str), markdown))
    conn.commit()
    conn.close()
    
    return markdown


if __name__ == '__main__':
    import sys
    y, m = int(sys.argv[1]), int(sys.argv[2])
    print(generate_review(y, m))
```

#### T7.2 — 生成 3 份 Review（1.5 小时）

```bash
python -m src.journal.monthly_review 2026 1
python -m src.journal.monthly_review 2026 2
python -m src.journal.monthly_review 2026 3
```

每份生成后：
1. 读完
2. 判断 AI 输出质量：
   - 有没有说客套话（应该没有）
   - Reality Test 数字是否引用准确
   - 建议是否具体可执行

如果 AI 输出质量不行，**在 Prompt 里加更严格的约束**，重新生成。

#### T7.3 — 保存到前端可访问（1 小时）

在 `src/api/routers/journal.py` 加：
```python
@router.get('/reviews/{year}/{month}')
def get_review(year: int, month: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute('''
        SELECT review_markdown, stats_json, generated_at
        FROM monthly_reviews WHERE year_month = ?
    ''', (f'{year}-{month:02d}',))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    return dict(row)
```

#### T7.4 — Week 1 复盘（30 分钟）

在 Journal 里写 Week 1 的复盘笔记（`Journey > Weekly Notes`）：
- Week 1 做完了什么？
- 哪些超预期？
- 哪些低于预期？
- Week 2 的重点？

#### T7.5 — Commit 和 Merge

```bash
git add src/journal/monthly_review.py src/api/
git commit -m "feat(phase-0/journal): monthly review generator with gemini"

# 如果 Week 1 一切正常，合入 main
git checkout main
git merge phase-0-journal --no-ff
```

### Day 7 验收标准

- [ ] 3 份 Monthly Review 生成成功
- [ ] AI 输出质量及格（至少不说废话）
- [ ] Reality Test 数字能对上之前的体检报告
- [ ] 代码合入 main（如果你采用 feature branch 流程）

---

## Week 1 总验收

**功能上**：
- [x] Moomoo CSV 能自动入库
- [x] FIFO 配对生成 trades
- [x] Daily Health Check 能推 Telegram
- [x] Reality Test 能跑
- [x] Monthly Review 能生成
- [x] Regime Classifier 原型能回溯历史
- [x] 前端能看基本数据

**工程上**：
- [ ] 所有单元测试通过
- [ ] 代码 commit 在 git 历史里清晰
- [ ] `.env` 不在仓库里
- [ ] README 更新了 Phase 0 使用说明

**心态上**：
- [ ] 你看完了 2026-01、2026-02、2026-03 三份 Monthly Review
- [ ] 你接受"去掉 Top 5 基本打平"这个事实
- [ ] 你**还没有改变交易方式**（Phase 0 目标就是这样）

---

## Week 2 预览

Week 2 要做的事：
- Regime Classifier 上线（每天早上推送）
- Breakout Filter 信号追踪字段加入 trades 表
- Weekly Reality Test 自动化（每周日晚推送）
- 前端加 Regime Score 历史图

Week 2 的开工文档会在 Week 1 结束后根据你的实际进度调整。

---

## 常见困境

### "Day X 没做完怎么办"
顺延。Phase 0 的核心是"质量"不是"进度"。如果 Day 3 FIFO 测试没全过就不要进 Day 4。

### "我工作/学校忙，只能周末做"
按周划分：Weekend 1 = Day 1-4，Weekend 2 = Day 5-7。工作日只读文档不写代码。

### "某个测试一直不过"
- 发给 Claude 让帮 debug
- 或者直接跳过那个 case，标记 `@pytest.mark.xfail`，继续前进，周末再回来弄

### "Gemini 生成的 Review 质量差"
- 第一次出来确实不会很好
- 在 Prompt 里加"禁止使用 XXX 词"
- 或者换 Claude Sonnet 4 备用（LiteLLM 切换）
- Prompt 迭代 3-5 次质量会稳定

### "发现之前设计有问题"
- Phase 0 就是用来发现问题的
- 改 DB schema 时用 migrations，不要直接改表
- 如果是大改，标记为 Phase 0.5，推到 Week 3

---

## 最后一件事

Week 1 结束时，**发一条 Telegram 给自己**：

```
🎉 Phase 0 Week 1 Done.

系统能吃我的 CSV。我看到了 3 个月的真相：
- 735 笔交易，99% 是期权
- Top 5 赚钱笔贡献 96% 利润
- 去掉 Top 5 几乎打平

下周我要做的：
1. Regime Classifier 上线
2. Breakout Filter 字段跟踪
3. 用数据继续看清楚自己

Phase 0 不是要赚更多钱。是要看懂自己。
```

把这条消息截图保存。3 个月后 Phase 0 结束时，再发一条同样格式的对比。**差别会是项目的最大回报**。