import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'zcode', adapter: 'zcode', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-zcode.mjs')] }] }) });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('zcode uploads text with images and image-only messages in desktop/phone and light/dark', async () => {
  const models = await (await api(daemon, '/api/agents/zcode/models')).json();
  const vision = models.find(m => m.images === true);
  const textOnly = models.find(m => m.images === false);
  assert.ok(vision && textOnly);
  const blocked = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'zcode', cwd: daemon.workdir, model: textOnly.id }) })).json();
  const refusal = await api(daemon, `/api/threads/${blocked.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'Describe', images: [{ data: PNG, mimeType: 'image/png' }] }) });
  assert.equal(refusal.status, 400, 'Native text-only models still reject images');
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const title = `图片验收 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'zcode', cwd: daemon.workdir, title, model: vision.id }) })).json();
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      for (const [index, text] of ['看看这张图片', ''].entries()) {
        await page.setInputFiles('.composer input[type=file]', { name: 'picture.png', mimeType: 'image/png', buffer: Buffer.from(PNG, 'base64') });
        await page.locator('.composer .attachment img').waitFor();
        await page.locator('.composer textarea').fill(text);
        await page.locator('.composer .send-btn:not(.stop)').click();
        await page.getByText('已读取 1 张图片', { exact: true }).nth(index).waitFor();
        assert.equal(await page.getByText('zcode 不支持发图片', { exact: true }).count(), 0);
        await page.locator('.composer .attachment img').waitFor({ state: 'hidden' });
        await page.locator('.composer .send-btn.stop').waitFor({ state: 'hidden' });
      }
      assert.equal((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status, 'idle');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (process.env.CALLI_E2E_SHOTS_DIR) {
        mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `zcode-images-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      }
    } finally { await context.close(); }
  }
});
