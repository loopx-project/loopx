import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

test('desktop sidebar drag resizes, persists, and stays off the phone layout', async () => {
  let daemon, browser;
  try {
    daemon = await startDaemon();
    browser = await launchBrowser();
    const context = await browser.newContext({ viewport: { width: 1200, height: 800 } });
    const page = await context.newPage();
    await page.addInitScript((token) => localStorage.setItem('calli.token', token), daemon.token);
    await openUi(page, daemon);

    const sidebar = page.locator('.sidebar');
    const handle = page.locator('.sidebar-resize-handle');
    assert.equal(await handle.getAttribute('aria-valuenow'), '300', 'undragged, the list takes a quarter of the window');
    const before = await handle.boundingBox();
    assert.ok(before);
    await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
    await page.mouse.down();
    await page.mouse.move(before.x + before.width / 2 + 140, before.y + before.height / 2);
    await page.mouse.up();

    assert.equal(Math.round((await sidebar.boundingBox()).width), 440);
    assert.equal(await handle.getAttribute('aria-valuenow'), '440');
    assert.equal(await page.evaluate(() => localStorage.getItem('calli.sidebar.width')), '440');

    await page.reload();
    assert.equal(Math.round((await sidebar.boundingBox()).width), 440, 'the dragged width survives a reload');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => window.matchMedia('(max-width: 899px)').matches && !document.querySelector('.sidebar-resize-handle'));
    assert.equal(await page.locator('.sidebar-resize-handle').count(), 0, 'the phone drawer has no resize handle');
    await context.close();
  } finally {
    await browser?.close();
    await daemon?.stop();
  }
});
