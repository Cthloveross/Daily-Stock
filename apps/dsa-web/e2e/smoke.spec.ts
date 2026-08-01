import { expect, test, type Page } from '@playwright/test';

/**
 * Web smoke（当前 UI 语义，HANDOFF §14.3 重写版）。
 *
 * 运行环境由 playwright.config.ts 提供：隔离后端（独立端口 + 独立 ENV_FILE +
 * 空白一次性 SQLite DB），ADMIN_AUTH_ENABLED=false，所有 Moomoo / 盘前 / 回填
 * provider 与调度器关闭。因此这里的断言全部针对“诚实的空态 / fail-closed 态”：
 * 页面必须明确说明数据为何缺失，而不是崩溃、无限转圈或伪造数据。
 */

interface CapturedErrors {
  consoleErrors: string[];
  pageErrors: string[];
}

/**
 * 空库上后端会对 episode-builds canonical preview 诚实返回 404
 * （"canonical evidence set not found"），前端渲染“请求失败 + 重新加载预览”。
 * 浏览器会为该 404 记录一条资源加载 console error，属于预期空态，不算失败。
 */
const EXPECTED_EMPTY_DB_RESOURCE_FAILURES = [
  /\/api\/v1\/journal\/v2\/episode-builds\/canonical\/preview/,
];

function captureErrors(page: Page): CapturedErrors {
  const captured: CapturedErrors = { consoleErrors: [], pageErrors: [] };
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    captured.consoleErrors.push(`${message.text()} @ ${message.location().url}`);
  });
  page.on('pageerror', (error) => {
    captured.pageErrors.push(String(error));
  });
  return captured;
}

function unexpectedConsoleErrors(captured: CapturedErrors): string[] {
  return captured.consoleErrors.filter((entry) => {
    const isResourceFailure = entry.includes('Failed to load resource');
    const isExpected = EXPECTED_EMPTY_DB_RESOURCE_FAILURES.some((pattern) => pattern.test(entry));
    return !(isResourceFailure && isExpected);
  });
}

test.describe('web smoke (isolated empty-DB backend)', () => {
  test('root and /login land on the regime workspace when auth is disabled', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/regime$/, { timeout: 15_000 });

    // 认证关闭时 /login 不渲染登录表单，而是明确重定向回工作台。
    await page.goto('/login');
    await expect(page).toHaveURL(/\/regime$/, { timeout: 15_000 });
    await expect(page.getByText('今日机会研究')).toBeVisible({ timeout: 15_000 });
  });

  test('regime board renders the honest unpublished premarket research state', async ({ page }) => {
    await page.goto('/regime');

    const premarket = page.locator('section[aria-label="官方盘前研究状态"]');
    await expect(premarket).toBeVisible({ timeout: 15_000 });
    await expect(premarket.getByText('官方盘前研究', { exact: true })).toBeVisible();

    // 空库 + 调度器关闭：发布状态必须收敛到明确的“未发布类”文案，不能停在“读取中”。
    await expect(
      premarket.getByLabel(/^发布状态：(研究池未配置|今日不发布|待发布|未发布)$/),
    ).toBeVisible({ timeout: 20_000 });

    // PREMARKET_RESEARCH_SCHEDULER_ENABLED=false 必须如实呈现为“未启用”。
    await expect(premarket.getByText(/后台自动研究\s*未启用/)).toBeVisible();

    // 空库上没有任何冻结快照可统计。
    await expect(page.getByText('尚无可统计样本').first()).toBeVisible({ timeout: 20_000 });

    // 只读预览用服务端默认研究池给出确定性候选行，数据时点口径必须始终可见。
    await expect(
      page.getByText(/^数据时点：价格、技术结构与相对量能＝上一完整交易日/),
    ).toBeVisible({ timeout: 30_000 });
  });

  test('health popover reports layered states with Moomoo layers disabled', async ({ page }) => {
    await page.goto('/regime');

    await page.getByRole('button', { name: '系统健康分层' }).click();
    const dialog = page.getByRole('dialog', { name: '系统健康分层详情' });
    await expect(dialog).toBeVisible();

    // GET /api/v1/system/health-layers 端到端：逐层状态而非单一布尔。
    const apiRow = dialog.locator('li', { hasText: 'API 进程' });
    await expect(apiRow).toContainText('正常', { timeout: 15_000 });

    const opendRow = dialog.locator('li', { hasText: 'OpenD 端口' });
    await expect(opendRow).toContainText('未启用');
    await expect(opendRow).toContainText('MOOMOO_OPEND_ENABLED=false');

    const journalRow = dialog.locator('li', { hasText: 'Journal 刷新配置' });
    await expect(journalRow).toContainText('未启用');
    await expect(journalRow).toContainText('MOOMOO_JOURNAL_REFRESH_ENABLED=false');

    await expect(dialog.locator('li', { hasText: '盘前官方发布' })).toContainText('未启用');
    await expect(dialog.locator('li', { hasText: '结果回填维护' })).toContainText('未启用');
  });

  test('journal positions and import tabs render empty states without unexpected errors', async ({ page }) => {
    const captured = captureErrors(page);

    await page.goto('/journal?tab=positions');
    await expect(page.getByText('期权交易复盘')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: '仓位复盘' })).toBeVisible();
    await expect(page.getByRole('button', { name: '交易证据' })).toBeVisible();
    // 当前仓位快照能力未启用时必须 fail-closed，而不是伪装成空仓。
    await expect(page.getByText('当前仓位快照功能未启用')).toBeVisible({ timeout: 15_000 });

    await page.goto('/journal?tab=import');
    await expect(page.getByText('OpenD 只读刷新', { exact: true }).first()).toBeVisible({ timeout: 15_000 });
    // MOOMOO_JOURNAL_REFRESH_ENABLED=false 的诚实降级文案 + 手动导入兜底仍在。
    await expect(
      page.getByText('服务器尚未启用 Journal 只读刷新；仍可在下方使用手动文件导入。'),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('高级 / 首次导入：CSV 账单或只读 JSON')).toBeVisible();

    expect(captured.pageErrors).toEqual([]);
    expect(unexpectedConsoleErrors(captured)).toEqual([]);
  });

  test('opportunity deep link renders the live-scan shell without an official snapshot', async ({ page }) => {
    await page.goto('/regime/opportunity/AAPL');

    await expect(page.getByRole('heading', { name: 'AAPL' })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('专业研究视图')).toBeVisible();
    await expect(page.getByText('只读 · 不下单')).toBeVisible();

    // 无 snapshotKey 的深链必须如实标注为即时扫描，而不是冒充官方冻结快照。
    await expect(page.getByText('即时扫描 · 未绑定官方快照')).toBeVisible();
    await expect(page.locator('section[aria-label="数据时点"]')).toBeVisible();
  });
});
