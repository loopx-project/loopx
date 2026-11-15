import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let dir;

before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'calli-import-browser-'));
  const sessions = join(dir, 'sessions.json');
  writeFileSync(sessions, '[]');
  daemon = await startDaemon({
    CALLI_AGENTS_JSON: JSON.stringify({ agents: [{
      id: 'codex', adapter: 'codex',
      command: [process.execPath, fileURLToPath(new URL('../../daemon/test/fixtures/fake-codex.mjs', import.meta.url))],
      env: { FAKE_CODEX_SESSIONS_FILE: sessions },
    }] }),
  });
  writeFileSync(sessions, JSON.stringify(Array.from({ length: 5101 }, (_, i) => ({
    id: `hist-browser-${i}`, cwd: daemon.workdir, name: `历史会话 ${i}`, updatedAt: 1791648439 - i,
  }))));
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
  if (dir) rmSync(dir, { recursive: true, force: true });
});

for (const mobile of [false, true]) {
  for (const colorScheme of ['light', 'dark']) {
    test(`index history remains searchable beyond 5000 sessions (${mobile ? 'phone' : 'desktop'}, ${colorScheme})`, async () => {
      const context = await browser.newContext({
        viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 },
        isMobile: mobile, hasTouch: mobile, colorScheme,
      });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
        await page.locator('.settings-nav').getByRole('button', { name: '导入历史', exact: true }).click();
        const panel = page.getByRole('region', { name: '导入历史', exact: true });
        await panel.getByText('历史会话 0', { exact: true }).waitFor();
        assert.equal(await panel.locator('.panel-row').count(), 100);
        await panel.getByLabel('搜索会话').fill('历史会话 5100');
        await panel.getByText('历史会话 5100', { exact: true }).waitFor();
        assert.equal(await panel.locator('.panel-row').count(), 1);
        const box = await panel.boundingBox();
        const viewport = page.viewportSize();
        assert.ok(box.x >= 0 && box.x + box.width <= viewport.width + 1);
        assert.ok(box.y >= 0);
        assert.equal(await page.getByRole('dialog').count(), 0);
        if (process.env.CALLI_HISTORY_SHOTS) {
          await page.screenshot({ path: join(process.env.CALLI_HISTORY_SHOTS, `history-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
      } finally {
        await context.close();
      }
    });
  }
}
