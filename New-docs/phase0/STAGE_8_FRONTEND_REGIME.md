# Stage 8 · 前端 Regime + Breakout + Today 首页

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 3/4/6/7
> 产出：5 个 FastAPI 端点 + Regime/Breakout 前端卡片 + TradingView Widget + `/regime` 页 + HomePage 双卡并排

---

## 做了什么

### 1. 后端 API（2 个 router）

| 端点 | 用途 |
|--|--|
| GET `/api/v1/regime/today` | 今日 Regime Score（返回 null 若未计算） |
| GET `/api/v1/regime/history?days=30` | 近 N 日时间序列 |
| POST `/api/v1/regime/recompute` | 立即重新计算今日（便于手动触发） |
| GET `/api/v1/breakout/signals?limit=15&only_fake=true` | 最近 breakout_chase / retest trades |

- Schemas [api/v1/schemas/regime.py](../../api/v1/schemas/regime.py)
- Endpoints [api/v1/endpoints/regime.py](../../api/v1/endpoints/regime.py) + [api/v1/endpoints/breakout.py](../../api/v1/endpoints/breakout.py)
- 注册到 `api/v1/router.py`：`/regime` tag + `/breakout` tag
- 7 个新契约测试（含 empty / seeded / fake-only 过滤）

Regime API 追加兼容字段 `quality_state`、`authoritative`、`domain_quality`、
`missing_domains`、`incomplete_domains` 与 `quality_message`；`today` / `recompute`
统一使用纽约市场日期。页面对 `unavailable` 隐藏占位 0 与指针，对 `degraded` 标记
`CONTEXT ONLY · PROVISIONAL`，顶栏不再把临时分档显示成权威 `NO TRADE`；仪表盘色带
与后端默认阈值 35/55/75 对齐。`generated_at` 由后端返回 UTC 证据时间，Web 统一按
`America/New_York` 显示，避免浏览器本地时区被误标为 ET。六项贡献按域质量显示数值
或 `—`，历史图排除核心行情缺失记录。
数据源状态栏读取快照 provenance，实际展示 `MoomooFetcher / Cboe` 等来源，不再把全部
市场日线固定标成 yfinance。

### 2. 前端 store + 组件

新增 [apps/dsa-web/src/types/regime.ts](../../apps/dsa-web/src/types/regime.ts) + [api/regime.ts](../../apps/dsa-web/src/api/regime.ts) + [stores/regimeStore.ts](../../apps/dsa-web/src/stores/regimeStore.ts)。

组件：
- `components/regime/RegimeScoreCard.tsx` — 醒目大分数 + 六维度小卡（标色：aggressive 绿 / standard 蓝 / cautious 琥珀 / no_trade 红）
- `components/regime/RegimeHistoryChart.tsx` — Recharts line，30 天分数序列
- `components/breakout/BreakoutSignalsList.tsx` — 最近信号列表 + All/Fake only/Real only 过滤
- `components/charts/TradingViewWidget.tsx` — 动态加载 `https://s3.tradingview.com/tv.js`，用 `useId` 生成 container id（pure render），autosize 嵌入

### 3. HomePage Today 仪表盘

原有股票查询 / 历史记录 / 任务流都保留。新增顶部"Reality Test + Regime Score"两列并排卡片（md 以下叠加）。

```tsx
<div className="grid gap-3 md:grid-cols-2">
  <RealityTestCard />
  <RegimeScoreCard />
</div>
```

### 4. `/regime` 页（[apps/dsa-web/src/pages/RegimePage.tsx](../../apps/dsa-web/src/pages/RegimePage.tsx)）

Regime 卡片 + 30 天历史图 + 最近 Breakout 信号列表。

### 5. 验收

| 检查 | 结果 |
|--|--|
| `cd apps/dsa-web && npm run lint` | exit 0 |
| `npm run build` | vite build ok（1.32 MB bundle，有 >500KB 警告，Stage 12 决定是否 code-split） |
| `python -m pytest src/... api/v1/tests` | 167 passed |

---

## 留了什么坑 / 显式延后

- **TradingView Widget 未接入股票详情页**：组件已就位，只在 `/stock/:symbol` 页的 Tab 里调用还没做。Stage 10 Agent 对话里也会引用它；先做通用组件。
- **Recompute 按钮在未配置 Alpaca 时会成功但数据降级**：d6 的 0 表示未计分，UI 会列出盘前域，不再把它显示成真实平盘贡献。
- **Breakout 实时扫描** 依然是 Phase 1（需要长驻进程 + Alpaca）；Stage 8 只展示历史。
- **Bundle size**：`index-*.js` ~1.3MB。Phase 1 做 `React.lazy` 拆 JournalPage / RegimePage / TVWidget 可以大幅瘦身。

---

## 下一步

Stage 9：AI 月度复盘 + Jinja templates。
