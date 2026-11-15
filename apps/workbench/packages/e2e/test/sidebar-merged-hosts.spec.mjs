// Every machine's threads in one list: named by machine, opened where they are, kept across
// reloads, and a machine that drops out says so instead of its threads silently going away.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { linkDaemon, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const SOCKET_IN_HOME = '.local/state/loochat/daemon.sock';

test('the merged list shows every machine, opens a thread on its own machine, and says when one is gone', { timeout: 240000 }, async () => {
  const root = mkdtempSync(join(tmpdir(), 'loochat-e2e-merged-'));
  const nodeHome = join(root, 'devbox');
  mkdirSync(nodeHome);
  const node = await startDaemon({ LOOCHAT_SOCKET: join(nodeHome, SOCKET_IN_HOME) });
  const hub = await startDaemon();
  const shots = process.env.LOOCHAT_E2E_SHOTS_DIR;
  if (shots) mkdirSync(shots, { recursive: true });
  let browser;
  let nodeUp = true;
  try {
    const remoteThread = await (await api(node, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: node.workdir, title: 'devbox上的线程' }) })).json();
    await api(node, `/api/threads/${remoteThread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'unread answer' }) });
    await new Promise(resolve => setTimeout(resolve, 1500));
    await api(hub, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: hub.workdir, title: '本机的线程' }) });
    await linkDaemon(hub, node);
    const devboxId = (await (await api(hub, '/api/hosts')).json())[0].id;
    browser = await launchBrowser();
    let savedState;
    const open = async (mobile, colorScheme, storageState) => {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme, storageState });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      await openUi(page, hub);
      if (mobile) await page.locator('.menu-btn').click();
      return { context, page, errors };
    };
    const row = (page, title) => page.locator('.sidebar .thread-row', { has: page.locator('.thread-title', { hasText: new RegExp(`^${title}$`) }) });

    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const { context, page, errors } = await open(mobile, colorScheme);
      try {
        await row(page, '本机的线程').waitFor({ timeout: 10_000 });
        const all = page.getByRole('tab', { name: '全部' });
        await all.click();
        assert.equal(await all.getAttribute('aria-selected'), 'true');
        await row(page, 'devbox上的线程').waitFor({ timeout: 10_000 });
        await row(page, '本机的线程').waitFor();
        // Threads elsewhere are named by their machine; the hub's own are not.
        const host = row(page, 'devbox上的线程').locator('.thread-host');
        assert.equal(await host.innerText(), 'devbox');
        assert.equal(await host.evaluate((el) => el.scrollWidth <= el.clientWidth), true, 'the machine name is never cut short');
        assert.equal(await row(page, '本机的线程').locator('.thread-host').count(), 0, 'the hub\'s own machine carries no mark');
        assert.equal(await row(page, 'devbox上的线程').locator('.row-more').count(), 1, 'remote actions are available through their own machine proxy');
        if (shots) await page.screenshot({ path: join(shots, `merged-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });

        if (!savedState) {
          assert.equal(await row(page, 'devbox上的线程').locator('.dot.unread').count(), 1);
          savedState = await context.storageState();
        }

        // Opening a thread elsewhere goes to that machine, the list still merged.
        await row(page, 'devbox上的线程').locator('.thread-item').click();
        await page.waitForFunction((id) => localStorage.getItem('calli.host') === id, devboxId, { timeout: 15_000 });
        assert.equal(await page.evaluate(() => localStorage.getItem('calli.host')), devboxId, 'the page is on devbox now');
        if (mobile) await page.locator('.menu-btn').click();
        await page.getByRole('tab', { name: '全部', selected: true }).waitFor();
        assert.equal(await row(page, 'devbox上的线程').locator('.row-more').count(), 1, 'on its own machine the thread has its menu again');
        await row(page, '本机的线程').waitFor();

        // A reload keeps the merged list.
        await page.reload();
        if (mobile) await page.locator('.menu-btn').click();
        await page.getByRole('tab', { name: '全部', selected: true }).waitFor({ timeout: 10_000 });
        await row(page, '本机的线程').waitFor({ timeout: 10_000 });

        // Picking one machine leaves the merged list.
        await page.getByRole('tab', { name: '本机' }).click();
        if (mobile && !(await page.locator('.sidebar .thread-row').first().isVisible())) await page.locator('.menu-btn').click();
        await row(page, '本机的线程').waitFor({ timeout: 10_000 });
        assert.equal(await row(page, 'devbox上的线程').count(), 0);
        assert.deepEqual(errors, []);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
      } finally { await context.close(); }
    }

    // devbox goes away: its threads stay listed from the last read, and the list says so.
    await node.stop();
    nodeUp = false;
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const { context, page } = await open(mobile, colorScheme, savedState);
      try {
        await page.getByRole('tab', { name: '全部' }).click();
        const remote = row(page, 'devbox上的线程');
        await remote.locator('.dot.offline').waitFor({ timeout: 60_000 });
        assert.equal(await remote.locator('.dot').count(), 1, 'offline and unread share exactly one right-side slot');
        assert.equal(await remote.locator('.dot.unread').count(), 0);
        assert.equal(await page.locator('.merged-note.offline').count(), 0);
        assert.ok((await remote.locator('.dot.offline').getAttribute('aria-label')).includes('devbox'));
        assert.ok((await remote.getAttribute('class')).includes('attention'), 'offline does not erase unread state');
        await row(page, '本机的线程').waitFor();
        if (shots) await page.screenshot({ path: join(shots, `merged-down-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await hub.stop();
    if (nodeUp) await node.stop();
    rmSync(root, { recursive: true, force: true });
  }
});
