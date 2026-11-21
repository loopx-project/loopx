import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const svg = (fill, label) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 300"><rect width="480" height="300" rx="16" fill="${fill}"/><text x="28" y="52" fill="#888" font-size="28">${label}</text></svg>`;

test('an answer\'s pictures sit right after the passage that names them, desktop and phone, light and dark', async () => {
  const artifacts = mkdtempSync(join(tmpdir(), 'loochat-answer-images-'));
  let daemon, browser;
  try {
    daemon = await startDaemon();
    browser = await launchBrowser();
    const light = join(artifacts, 'light.svg');
    const dark = join(artifacts, 'dark.svg');
    writeFileSync(light, svg('#f4f1ea', 'light'));
    writeFileSync(dark, svg('#24221f', 'dark'));
    const post = (path, body) => api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    const thread = await (await post('/api/threads', { agent: 'pi', cwd: daemon.workdir })).json();
    // The fake agent answers with what it was sent, so the answer names both pictures in a list.
    await post(`/api/threads/${thread.id}/turns`, { text: `草图画好了：\n\n- 亮色：${light}\n- 深色：${dark}\n\n看完告诉我哪里要改。` });
    const deadline = Date.now() + 10_000;
    while ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'running') {
      assert.ok(Date.now() < deadline, 'fake agent finished');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: '草图画好了' }).click();
        const answer = page.locator('.row.assistant');
        await answer.locator('.file-image img').nth(1).waitFor();
        // Each picture is in the list item that names it, and the closing line comes after both.
        for (const name of ['亮色', '深色']) {
          const item = answer.locator('li', { hasText: name });
          assert.equal(await item.locator('.file-image').count(), 1);
          assert.equal(await item.locator('.file-image figcaption').count(), 0, 'no path below the picture');
          assert.ok(await item.locator('.file-image img').evaluate(img => img.complete && img.naturalWidth > 0));
        }
        assert.equal(await answer.locator('.markdown > .file-images').count(), 0, 'nothing is left over at the bottom');
        const order = await answer.evaluate(el => [...el.querySelectorAll('.file-image, p')].map(n => n.matches('.file-image') ? 'image' : n.textContent.includes('看完') ? 'end' : 'text'));
        const end = order.indexOf('end');
        assert.deepEqual(order.slice(end - 2, end + 1), ['image', 'image', 'end']);
        if (process.env.LOOCHAT_E2E_SHOTS_DIR) {
          mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
          await answer.scrollIntoViewIfNeeded();
          await page.screenshot({ animations: 'disabled', path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `answer-images-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    rmSync(artifacts, { recursive: true, force: true });
  }
});
