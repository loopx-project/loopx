import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, repoRoot } from '../harness.mjs';
import { fakeZcodeQuota } from '../../daemon/test/fixtures/fake-zcode-quota.mjs';

test('zcode quota appears in the shared board on desktop and phone, light and dark', async () => {
  const provider = await fakeZcodeQuota();
  let daemon, browser;
  try {
    daemon = await startDaemon({ ZCODE_BIGMODEL_USAGE_API_KEY: 'fixture-key', ZCODE_BIGMODEL_USAGE_QUOTA_URL: provider.url, CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'zcode', adapter: 'zcode', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-zcode.mjs')] }] }) });
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.getByRole('button', { name: '配置', exact: true }).click();
        await page.locator('.settings-nav').getByRole('button', { name: '额度', exact: true }).click();
        const row = page.locator('.quota-board-row').filter({ has: page.locator('.quota-board-name', { hasText: /zcode/i }) });
        await row.getByRole('meter').filter({ hasText: '2%' }).waitFor();
        assert.equal(await row.getByRole('meter').count(), 2, 'the list shows short and weekly quota together');
        await row.getByRole('button').click();
        const detail = page.locator('.quota-account-detail');
        await detail.getByRole('meter').first().waitFor();
        assert.deepEqual(await detail.getByRole('meter').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-valuenow'))), ['88', '2']);
        assert.deepEqual(await detail.locator('.quota-meter.low .quota-percent').allTextContents(), ['剩余 2%']);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `quota-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon?.stop(); await provider.close(); }
});

test('quota reopens immediately, survives page reload and retains the last reading after a failed refresh', { timeout: 90_000 }, async () => {
  const provider = await fakeZcodeQuota();
  let daemon, browser;
  try {
    daemon = await startDaemon({ ZCODE_BIGMODEL_USAGE_API_KEY: 'fixture-key', ZCODE_BIGMODEL_USAGE_QUOTA_URL: provider.url, CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'zcode', adapter: 'zcode', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-zcode.mjs')] }] }) });
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      let pending;
      try {
        const page = await context.newPage();
        const openBoard = async () => { await page.evaluate(() => { location.hash = '#/settings/quota'; }); };
        const panel = page.getByRole('region', { name: '额度', exact: true });
        const row = panel.locator('.quota-board-row');
        const close = () => page.evaluate(() => { location.hash = ''; });
        const gate = () => {
          let respond, requested;
          const response = new Promise(resolve => { respond = resolve; });
          const seen = new Promise(resolve => { requested = resolve; });
          return { respond, requested, response, seen };
        };
        await openUi(page, daemon);
        await openBoard();
        await row.getByRole('meter').filter({ hasText: '2%' }).waitFor();
        await close();
        let calls = 0;
        pending = gate();
        await page.route('**/api/agents/zcode/quota*', async route => {
          calls++;
          const current = pending;
          current.requested(route.request().url());
          await route.fulfill(await current.response);
        });
        await openBoard();
        await panel.getByRole('button', { name: '全部刷新', exact: true }).click();
        await pending.seen;
        await row.getByRole('meter').filter({ hasText: '2%' }).waitFor();
        await row.getByRole('status').filter({ hasText: '更新中' }).waitFor();
        assert.equal(await row.getByText('正在查询额度…', { exact: true }).count(), 0);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `quota-cached-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        // Reopening while a read is pending must share it, and the remounted row must receive it.
        await close();
        await openBoard();
        await row.getByRole('meter').filter({ hasText: '2%' }).waitFor();
        const updated = { windows: [{ label: '5H', usedPercent: 64 }], checkedAt: new Date().toISOString() };
        pending.respond({ json: updated });
        await row.getByRole('meter').filter({ hasText: '36%' }).waitFor();
        assert.equal(calls, 1);
        pending = gate();
        await openUi(page, daemon);
        await openBoard();
        await panel.getByRole('button', { name: '全部刷新', exact: true }).click();
        await pending.seen;
        await row.getByRole('meter').filter({ hasText: '36%' }).waitFor();
        await row.getByRole('status').filter({ hasText: '更新中' }).waitFor();
        pending.respond({ status: 503, json: { error: '暂时无法查询额度' } });
        await row.getByRole('status').filter({ hasText: '刷新失败' }).waitFor();
        assert.ok(await row.getByRole('meter').filter({ hasText: '36%' }).isVisible());
        assert.equal(calls, 2, 'reporting an error must not start another request');
        if (process.env.CALLI_E2E_SHOTS_DIR) await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `quota-stale-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        pending = gate();
        await panel.getByRole('button', { name: '全部刷新', exact: true }).click();
        assert.match(await pending.seen, /refresh=1/);
        assert.ok(await row.getByRole('meter').filter({ hasText: '36%' }).isVisible());
        pending.respond({ json: { ...updated, windows: [{ label: '5H', usedPercent: 35 }] } });
        await row.getByRole('meter').filter({ hasText: '65%' }).waitFor();
        assert.equal(await row.getByText(/上次刷新失败/).count(), 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      } finally {
        pending?.respond({ status: 503, json: { error: 'test ended' } });
        await context.close();
      }
    }
  } finally { await browser?.close(); await daemon?.stop(); await provider.close(); }
});
