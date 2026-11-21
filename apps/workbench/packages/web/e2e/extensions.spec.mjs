import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { launchBrowser } from '../../e2e/harness.mjs';

let vite, browser, base;
before(async () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  vite = await createServer({ root, server: { port: 0, host: '127.0.0.1' } });
  await vite.listen();
  base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await vite?.close(); });

// Mount the real App with an in-memory API. Each pending promise can be released in any order.
async function setup(t, options = {}) {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.addInitScript(options => { window.testOptions = options; }, options);
  await page.route('**/src/main.tsx', route => route.fulfill({ contentType: 'text/javascript', body: `
    import '/e2e/extensions-fixture.js';
  ` }));
  await page.goto(`${base}/${options.settings ? '#/extensions' : '#/board'}`);
  await page.waitForFunction(() => Boolean(window.probe));
  return page;
}

test('demo board has no stale snapshot warning and dropdown chevrons point down', async t => {
  const page = await setup(t, { demo: true });
  await page.locator('.ext-board-page').waitFor();
  assert.equal(await page.locator('.ext-board-stale').count(), 0, 'demo snapshots have no observedAt');
  const paths = await page.locator('.ext-board-filter svg path').evaluateAll(nodes => nodes.map(n => n.getAttribute('d')));
  assert.ok(paths.includes('M6 9l6 6 6-6'), 'filter chevrons point down');
});

test('dropdown chevron uses the downward host icon', async t => {
  const page = await setup(t);
  await page.locator('.ext-board-page').waitFor();
  const paths = await page.locator('.ext-board-page button svg path').evaluateAll(nodes => nodes.map(n => n.getAttribute('d')));
  assert.ok(paths.includes('M6 9l6 6 6-6'));
  assert.ok(!paths.includes('M9 6l6 6-6 6'));
});

test('permission shrink requires confirmation in the management page', async t => {
  const page = await setup(t, { settings: true });
  const result = await page.evaluate(async () => {
    const { needsConfirm } = await import('/src/components/ExtensionsPage.tsx');
    return [
      { manifest: { permissions: ['events.summary'] }, granted: ['events.summary', 'notify'] },
      { manifest: { permissions: ['notify'] }, granted: ['events.summary'] },
      { manifest: { permissions: [] }, granted: ['events.summary'] },
    ].map(s => needsConfirm({ ...s, installed: true }));
  });
  assert.deepEqual(result, [true, true, true]);
});

test('malformed view modules fail within the extension while core stays mounted', async t => {
  const page = await setup(t, { badModule: true });
  await page.locator('.ext-failed').waitFor({ timeout: 5000 });
  assert.equal(await page.locator('.sidebar-inner').count(), 1);
});

test('subscription exceptions show an extension failure while core stays mounted', async t => {
  const page = await setup(t, { throwing: true });
  await page.locator('#probe-view').waitFor();
  await page.evaluate(() => window.probe.changeThreads());
  await page.locator('.ext-failed').waitFor({ timeout: 5000 });
  assert.equal(await page.locator('.sidebar-inner').count(), 1);
});

test('live stream plus failed snapshot degrades and retries before restoring live', async t => {
  const page = await setup(t);
  await page.locator('.ext-board-page').waitFor();
  await page.evaluate(() => { window.probe.fail(true); window.probe.emit(); });
  await page.locator('.ext-board-stale').waitFor({ timeout: 5000 });
  const reads = await page.evaluate(() => window.probe.reads);
  await page.waitForFunction(n => window.probe.reads > n, reads, { timeout: 5000 });
  await page.evaluate(() => window.probe.fail(false));
  await page.locator('.ext-board-stale').waitFor({ state: 'hidden', timeout: 5000 });
});

test('extension refreshes accept only the newest response', async t => {
  const page = await setup(t, { settings: true });
  await page.getByRole('button', { name: '停用', exact: true }).waitFor();
  await page.evaluate(() => { window.probe.defer(); window.probe.emit('extensions.changed'); window.probe.emit('extensions.changed'); });
  await page.waitForFunction(() => window.probe.pending === 2);
  await page.evaluate(() => window.probe.release(1, false));
  await page.getByRole('button', { name: '启用', exact: true }).waitFor();
  await page.evaluate(() => window.probe.release(0, true));
  await page.waitForTimeout(100);
  assert.equal(await page.getByRole('button', { name: '启用', exact: true }).count(), 1);
});

test('a local toggle invalidates an extension refresh already in flight', async t => {
  const page = await setup(t, { settings: true });
  await page.getByRole('button', { name: '停用', exact: true }).waitFor();
  await page.evaluate(() => { window.probe.defer(); window.probe.emit('extensions.changed'); });
  await page.waitForFunction(() => window.probe.pending === 1);
  await page.getByRole('button', { name: '停用', exact: true }).click();
  await page.getByRole('button', { name: '启用', exact: true }).waitFor();
  await page.evaluate(() => window.probe.release(0, true));
  await page.waitForTimeout(100);
  assert.equal(await page.getByRole('button', { name: '启用', exact: true }).count(), 1);
});
