import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openCollaboration, startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

test('compact works from a collaboration composer on desktop and phone in both themes', async () => {
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{
    id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')],
  }] }) });
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const title = `协作压缩 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
      const response = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title }) });
      assert.equal(response.status, 201);
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: title }).first().click();
        await openCollaboration(page);
        await page.getByRole('button', { name: '协作设置', exact: true }).click();
        await page.getByRole('button', { name: '启用协作', exact: true }).click();
        await page.locator('.composer-add.on').waitFor();
        await page.locator('.composer textarea').fill('/compact');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('上下文压缩完成。', { exact: true }).waitFor();
        await page.getByRole('button', { name: '发送', exact: true }).waitFor();
        assert.equal(await page.getByText('/compact 不接受参数或图片。', { exact: true }).count(), 0);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `coordination-compact-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
