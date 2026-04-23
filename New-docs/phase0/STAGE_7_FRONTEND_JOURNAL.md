# Stage 7 · 前端 Mirror 核心（Journal UI + API）

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 2 / Stage 6（trades 数据稳定）
> 产出：7 个 FastAPI 端点 + React Journal 页 + HomePage 顶部 Reality Test 卡片

---

## 做了什么

### 1. 后端 API（`api/v1/endpoints/journal.py`）

7 个端点，全部走 Zustand store + `toCamelCase`（前端惯例）：

| Method | Path | 用途 |
|--|--|--|
| GET | `/api/v1/journal/reality-test` | Phase 0 灵魂指标 |
| GET | `/api/v1/journal/trades` | 分页 + 过滤（symbol / start-end / status / style） |
| GET | `/api/v1/journal/trades/{id}` | 单笔详情 |
| PATCH | `/api/v1/journal/trades/{id}` | 更新 user_notes / emotional_state / trade_style |
| GET | `/api/v1/journal/health-check` | 某天的 health_checks 行 |
| GET | `/api/v1/journal/stats` | 窗口统计（DTE 分布 + 桶胜率 + reality test） |
| POST | `/api/v1/journal/import` | multipart CSV 上传 → 入库 + 重配 |

- Pydantic schemas 在 [api/v1/schemas/journal.py](../../api/v1/schemas/journal.py)
- 注册在 [api/v1/router.py](../../api/v1/router.py) 的 `/api/v1` 下，tag `Journal`
- 7 个端点各有一个 FastAPI TestClient 单测（[api/v1/tests/test_journal_endpoints.py](../../api/v1/tests/test_journal_endpoints.py)）

### 2. 前端客户端与 store

- [apps/dsa-web/src/types/journal.ts](../../apps/dsa-web/src/types/journal.ts) — TradeItem / TradeListResponse / RealityTestResponse / HealthCheckItem / JournalStatsResponse / ImportResponse
- [apps/dsa-web/src/api/journal.ts](../../apps/dsa-web/src/api/journal.ts) — 7 个 axios 方法，全走 `apiClient`（cookie session 复用），返回 camelCase
- [apps/dsa-web/src/stores/journalStore.ts](../../apps/dsa-web/src/stores/journalStore.ts) — Zustand store：`loadRealityTest` / `loadStats` / `loadTrades` / `patchTrade` / `importCsv` + loading/error 标志

### 3. 前端组件（`apps/dsa-web/src/components/journal/`）

- `RealityTestCard` — Top-N 盈亏 + 中位数 + 警告（Top-N 占比 > 80% 自动红色提示）
- `DTEDistribution` — Recharts BarChart，左 Y 轴数量、右 Y 轴胜率%
- `TradeTable` — 12 列表格，按 entry_time desc，点击行触发 onRowClick
- `JournalImport` — `<input type="file" accept=".csv">` 直连 POST /import

### 4. `JournalPage`（[apps/dsa-web/src/pages/JournalPage.tsx](../../apps/dsa-web/src/pages/JournalPage.tsx)）

4 个 tab：
- **Overview** — RealityTestCard + DTEDistribution + 最近 10 笔 closed trades
- **Trades** — 过滤栏（symbol / style / status）+ 完整 TradeTable
- **Reality** — 两个 RealityTestCard（Top 5 / Top 10）+ 说明文字（"数据不骗你"基调）
- **Import** — JournalImport

单笔行点击打开右侧抽屉（JSON 原样展示；Phase 1 再美化成精美 detail 页）。

### 5. `HomePage` 顶部集成（per ADR-v4-08）

```tsx
<div className="px-3 pt-3 md:px-4 md:pt-4">
  <RealityTestCard />
</div>
```

不替换原有"股票查询 / 历史记录"区，照 v4 "扩展不替换" 原则直接加在顶部。

### 6. 路由

`apps/dsa-web/src/App.tsx` 新增 `<Route path="/journal" element={<JournalPage />} />`，登录后可访问。

---

## 怎么验证

### 后端
```bash
python -m pytest api/v1/tests/test_journal_endpoints.py -v
# 应 7 passed
```

### 前端
```bash
cd apps/dsa-web
npm run lint    # exit 0
npm run build   # vite build ok (有 Node 20.19+ 提示但不影响构建)
```

### 端到端
```bash
# 终端 1: 后端
uvicorn server:app --reload --host 0.0.0.0 --port 8000

# 终端 2: 前端
cd apps/dsa-web && npm run dev

# 浏览器 http://localhost:5173
# - 首页顶部应有 Reality Test 卡片
# - /journal 四 tab 可切，Trades 表有数据，Import tab 可上传
# - POST /api/v1/journal/reality-test 可 curl 测
```

---

## 留了什么坑 / 显式延后

- **TradeDetailDrawer** 目前只显示 JSON，Phase 1 再做精致版（K 线 + AI 分析 + 用户备注编辑表单）
- **UserNotes / EmotionalState PATCH** API 已完备，UI 目前还没表单入口 —— Phase 1 在 drawer 里加
- **前端单测**：dsa-web 用 vitest，但目前没为 Journal 组件写单测；build + lint 绿足够 Phase 0
- **Accessibility**：tab 按钮、抽屉等用 div/button 简单实现，没严格 ARIA attributes。Phase 1 再打磨
- **Recharts 数据空时**：DTEDistribution 有"无数据"兜底，但图表本身还渲染（小瑕疵）
- **UI 文案中英混用**：Phase 0 功能优先，文案 Phase 1 再统一

---

## 下一步

Stage 8：前端 Regime + Breakout + Today 首页改造。
