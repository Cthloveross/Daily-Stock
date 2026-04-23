# Stage 4 · Regime 晨报 + GitHub Actions cron

> 状态：✅ 完成于 2026-04-20（workflow 入库，首次真实 Telegram 推送需你配 Secrets + 手动 dispatch 一次）
> 前置：Stage 3
> 产出：Jinja 模板 + morning_brief 格式化/推送 + GitHub Actions cron

---

## 做了什么

### 1. 模板 `templates/regime_morning_brief.md.j2`

Telegram-friendly Markdown：
- 一行概括：date / score / label
- action_hint 引号高亮
- 六维度 breakdown（用反引号 formatter 好看）
- 今日宏观事件列表（仅有事件才出现）
- SPY 快照 + VIX
- Warnings（no_trade 时、macro >= -30 时、VIX crisis 时自动加）

### 2. `src/regime/morning_brief.py`

```python
format_brief(result) -> str           # 渲染 Jinja
send_brief(result) -> bool            # 调 TelegramSender；未配置时 fallback 打印 stdout
python -m src.regime.morning_brief --send [--date YYYY-MM-DD] [--format-only] [--no-save]
```

- 复用原项目 `src/notification_sender/telegram_sender.py`（已有签名 `send_to_telegram(content)`）
- Jinja 环境 loader 指向 `templates/`（和其他报告共用）
- Telegram 未配置 → warn + stdout（workflow 日志可见）不 crash

### 3. GitHub Actions `.github/workflows/regime_brief.yml`

双 cron（13:00 UTC for EDT, 14:00 UTC for EST）+ ET 小时 guard，避免双推：
```bash
et_hour=$(TZ="America/New_York" date +%H)
# skip=true unless et_hour == "09" or workflow_dispatch
```

`workflow_dispatch` 输入：
- `date` (YYYY-MM-DD, 空 = 今天)
- `dry_run` (true = format-only，不推 Telegram)

需要的 Secrets：
- `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`（可选）
- `FINNHUB_API_KEY`（可选）
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（必需才能真推）

Vars：
- `WATCHLIST`（逗号分隔），Regime fetcher 从 `STOCK_LIST` env 读

### 4. 4 个单测

- `test_morning_brief.py`：basic render / macro events rendered / no_trade warning / missing VIX handled / Telegram unconfigured stdout / Telegram configured sends

总测试数 **117 passed**（Stage 1-4 累计）。

---

## 怎么验证

### 本地
```bash
# 不推送，只打印
python -m src.regime.morning_brief --format-only
# 完整跑一遍（需要 API key 才出真实数字；否则 scorer 用空 snapshot 得基础分数）
python -m src.regime.morning_brief --send
```

### GitHub Actions 手动 dispatch
1. 在 repo Settings → Secrets 配好 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
2. `Actions` tab → `Regime Morning Brief` → `Run workflow` → 选 main → 可选 `date` / `dry_run`
3. 检查日志 + Telegram 是否收到

---

## 留了什么坑 / 显式延后

- **DB 同步**：workflow 每天跑完产生的 `regime_scores` 行只活在临时 runner 里，不自动 push 回 repo。Stage 12 再决定 DB 持久化方案（commit 回、artifact、或切 Turso）。
- **真实 Telegram 联调**：需要你有 TG bot + chat id。没有 workflow 会走 stdout fallback，日志能看。
- **邮件兜底**：`send_brief` 未配 TG 时只打 stdout，没发 email。Phase 1 如果需要再加。
- **时区 guard 对 workflow_dispatch 手动触发放行**：本意如此（方便随时测）；否则需要手动指定 date 才能绕开时区。
- **ET dates 跨午夜**：当 UTC 是 01:00-04:00 而 ET 是 21:00-0:00 之间（夏令时尾），脚本取的 `date.today()` 是 UTC 日。对每日 09:00 ET 触发无影响；但 `--no-save` 情况下日期显示可能比 ET 晚一天，留坑。

---

## 下一步

Stage 5：Breakout Detector + 四层过滤。依赖 Stage 3 的 `regime_scores` 做 Q1 gate。
