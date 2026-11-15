import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, mkdirSync } from 'node:fs';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
before(async () => {
  daemon = await startDaemon(); browser = await launchBrowser();
  writeFileSync(join(daemon.workdir, 'design.html'), '<!doctype html><html data-variant="one-slot"><head><meta name="viewport" content="width=device-width"><style>body{font:16px system-ui;padding:24px;color:#222;background:#eee}button{padding:12px}@media(prefers-color-scheme:dark){body{color:#eee;background:#222}}</style></head><body><h1>离线状态设计</h1><button id="target" onclick="this.textContent=\'should not execute\'">devbox ○</button><p>右侧只显示一个点</p></body></html>');
});
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`annotations save, survive reload and send once: ${mobile ? 'phone' : 'desktop'} ${colorScheme}`, { timeout: 60000 }, async () => {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    try {
      const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '页面批注验收' }) })).json();
      await openUi(page, daemon);
      const openPreview = async () => {
        await page.goto(`${daemon.base}/#/thread/${thread.id}?preview=design.html`);
        await page.getByRole('button', { name: '加注释', exact: true }).waitFor();
        await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === '加注释')?.disabled);
      };
      await openPreview();
      // Messages from the app itself cannot impersonate a pick from the sandbox frame.
      await page.evaluate(() => window.postMessage({ type: 'calli-preview:picked', target: {}, context: {} }, location.origin));
      assert.equal(await page.locator('.preview-note-editor').count(), 0);
      await page.getByRole('button', { name: '加注释', exact: true }).click();
      await page.frameLocator('.preview-frame').locator('#target').click();
      await page.getByRole('textbox', { name: '这里想怎么改？' }).fill('空心点与黑点共用右侧位置');
      assert.equal(await page.frameLocator('.preview-frame').locator('#target').innerText(), 'devbox ○', 'annotation pick must not trigger the page action');
      const shots = process.env.CALLI_E2E_SHOTS_DIR;
      if (shots) { mkdirSync(shots, { recursive: true }); await page.screenshot({ animations: 'disabled', path: join(shots, `notes-editor-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) }); }
      await page.getByRole('button', { name: '保存注释', exact: true }).click();
      await page.getByRole('button', { name: '发送批注 1', exact: true }).waitFor();
      await page.reload(); await openPreview();
      await page.getByRole('button', { name: '批注 1', exact: true }).click();
      await page.locator('.preview-note').getByText('空心点与黑点共用右侧位置').waitFor();
      if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `notes-list-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: '发送批注 1', exact: true }).click();
      await page.locator('.preview-annotation-status').getByText('已发送', { exact: true }).waitFor();
      const notes = await (await api(daemon, `/api/threads/${thread.id}/preview-notes?page=design.html`)).json();
      assert.equal(notes.length, 1); assert.ok(notes[0].receipt.turnId); assert.equal(notes[0].target.selector, '#target'); assert.equal(notes[0].context.variant, 'one-slot');
      assert.equal(await page.locator('.preview-send-notes').isDisabled(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}

test('a standalone playground uses the same picker and Hub storage', { timeout: 60000 }, async () => {
  const server = createServer((_req, res) => res.end(`<!doctype html><html data-variant="external"><body><h1 id="external">External playground</h1><script src="${daemon.base}/api/preview-bridge.js"></script></body></html>`));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const context = await browser.newContext();
  try {
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
    await openUi(page, daemon);
    await page.goto(`${daemon.base}/#/thread/${thread.id}?preview=${encodeURIComponent(url)}`);
    await page.getByRole('button', { name: '加注释', exact: true }).click();
    await page.frameLocator('.preview-frame').locator('#external').click();
    await page.getByRole('textbox', { name: '这里想怎么改？' }).fill('通用批注');
    await page.getByRole('button', { name: '保存注释', exact: true }).click();
    await page.getByRole('button', { name: '发送批注 1', exact: true }).waitFor();
    const notes = await (await api(daemon, `/api/threads/${thread.id}/preview-notes?page=${encodeURIComponent(url)}`)).json();
    assert.equal(notes[0].context.variant, 'external');
    assert.equal(notes[0].target.selector, '#external');
    // Save failure keeps the user's words in the editor for retry.
    await page.getByRole('button', { name: '加注释', exact: true }).click();
    await page.frameLocator('.preview-frame').locator('#external').click();
    await page.getByRole('textbox', { name: '这里想怎么改？' }).fill('保留未保存的文字');
    await page.route(`**/api/threads/${thread.id}/preview-notes`, route => route.fulfill({ status: 503, body: '{}' }));
    await page.getByRole('button', { name: '保存注释', exact: true }).click();
    await page.locator('.preview-note-editor [role="alert"]').waitFor();
    assert.equal(await page.getByRole('textbox', { name: '这里想怎么改？' }).inputValue(), '保留未保存的文字');
    await page.unroute(`**/api/threads/${thread.id}/preview-notes`);
    await page.getByRole('button', { name: '保存注释', exact: true }).click();
    await page.getByRole('button', { name: '发送批注 2', exact: true }).waitFor();
  } finally { await context.close(); await new Promise(resolve => server.close(resolve)); }
});
