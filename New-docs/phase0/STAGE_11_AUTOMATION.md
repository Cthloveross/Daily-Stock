# Stage 11 · Folder Watcher + bot 命令

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 2 / 4 / 9
> 产出：`~/Daily-Stock-Inbox/` watchdog 监听 + 3 个 bot 命令

---

## 做了什么

### 1. Folder Watcher (`src/journal/folder_watcher.py`)

- `MoomooCsvHandler` — 核心入口（测试友好，不强依赖 watchdog）
  - 只接受前缀 `History` / `Trade` / `Orders` / `moomoo` 的 `.csv`
  - `DS_WATCHER_SETTLE_SECONDS` 环境变量控制落盘延迟（默认 1s）
  - 走完 `record_import → insert_events → match → replace_trades`
  - 处理完移动到 `PROCESSED_DIR`，命名冲突 append timestamp
  - Telegram best-effort 通知；未配置时降级到日志
- `start_watching()` — 阻塞运行，延迟导入 watchdog（包未装也不 break `handle_path`）
- 启动时 sweep 一次 inbox，把离线期间落地的文件补上

### 2. CLI `scripts/run_journal_watcher.py`

```bash
pip install watchdog>=3.0
python -m scripts.run_journal_watcher
```

### 3. 三个 bot 命令（自动注册）

在 [bot/commands/__init__.py](../../bot/commands/__init__.py) 的 `ALL_COMMANDS` 里加了：

| Command | 别名 | 用途 |
|--|--|--|
| `/journal [today\|reality]` | `日志` / `今日体检` | 今日 Health Check 或 Reality Test 摘要 |
| `/regime [YYYY-MM-DD]` | `市场环境` | 某天 Regime Score + 六维度 breakdown |
| `/phase` | `阶段` | 当前 Journey Phase + 在此 phase 天数 |

三个命令都**容错**：DB 无数据时返回"尚未计算/生成"而不是崩溃。

### 4. requirements.txt 新增

```
watchdog>=3.0   # Journal folder watcher (Stage 11)
```

### 5. 测试

- `src/journal/tests/test_folder_watcher.py` — 4 cases：非 CSV skip / 坏前缀 skip / 正常 ingest+move / 重复 sha256 = duplicate
- `bot/commands/tests/test_phase0_commands.py` — 7 cases：每个命令空数据 + 有数据 + 参数校验

**累计 190 backend passed**。

---

## 怎么验证

### 本地 watcher
```bash
pip install watchdog
python -m scripts.run_journal_watcher &

# 另一终端
cp tests/fixtures/journal/moomoo_inline_sample.csv ~/Daily-Stock-Inbox/History-smoke.csv

# 期望:
#   watcher log: "✅ Journal 导入 History-smoke.csv..."
#   Telegram (如配置): 收到同一条消息
#   ls ~/Daily-Stock-Processed/ 看到文件
```

### Bot 命令
部署时走原 `bot/dispatcher.py` 的 `register_class`。Telegram 私聊里发：
```
/journal
/journal reality
/regime
/regime 2026-04-17
/phase
```

---

## 留了什么坑 / 显式延后

- **watcher 无 systemd / launchd 单元文件**：文档建议放到用户 launchd plist；样板 plist 留到 Phase 1 真上线时写。
- **watcher 不自动触发 Regime / Breakout backfill**：职责分离，只跑 journal 那一链。Stage 12 CI / cron 统一调度时再决定。
- **bot 命令里的 JournalHealthCheck 行为**：Stage 7 Daily Health Check 目前不是自动生成——Phase 1 的日终 cron 才会落库。现在 `/journal today` 多数情况会显示"尚未生成"（正确行为）。

---

## 下一步

Stage 12：README / AGENTS / CI gate / Phase 0 退出评估。
