// Switching machines keeps each one's last list on screen until the live one arrives, and a list
// not read yet says so instead of claiming there are no threads.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { linkDaemon, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const SOCKET_IN_HOME = '.local/state/calli/daemon.sock';

test('switching machines shows the remembered list at once and never an empty one', { timeout: 150000 }, async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-e2e-host-cache-'));
  const nodeHome = join(root, 'devbox');
  mkdirSync(nodeHome);
  const node = await startDaemon({ CALLI_SOCKET: join(nodeHome, SOCKET_IN_HOME) });
  const hub = await startDaemon();
  let browser;
  try {
    await api(node, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: node.workdir, title: 'devbox上的线程' }) });
    await api(hub, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: hub.workdir, title: '本机的线程' }) });
    await linkDaemon(hub, node);
    browser = await launchBrowser();
    for (const mobile of [false, true]) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile });
      try {
        const page = await context.newPage();
        // Every empty-list line the sidebar ever draws, however briefly.
        await page.addInitScript(() => {
          window.__emptyLines = [];
          window.__agentNames = [];
          new MutationObserver(() => {
            for (const el of document.querySelectorAll('.sidebar .group-empty.top')) window.__emptyLines.push(el.textContent);
            // An agent's name in place of its mark: what rows showed while the agents were not read yet.
            for (const el of document.querySelectorAll('.sidebar .agent-tag')) window.__agentNames.push(el.textContent);
          }).observe(document, { subtree: true, childList: true, characterData: true });
        });
        const seen = async () => { const lines = await page.evaluate(() => window.__emptyLines.splice(0)); return lines; };
        const names = () => page.evaluate(() => window.__agentNames.splice(0));
        await openUi(page, hub);
        if (mobile) await page.locator('.menu-btn').click();
        const title = text => page.locator('.sidebar .thread-title', { hasText: new RegExp(`^${text}$`) });
        const tab = name => page.getByRole('tab', { name });
        await title('本机的线程').waitFor({ timeout: 10_000 });
        await seen();
        await names();

        // The first visit to devbox: nothing remembered, so it reads, and says so rather than "no threads".
        await tab('devbox').click();
        await title('devbox上的线程').waitFor({ timeout: 10_000 });
        const first = await seen();
        assert.ok(!first.some(l => l.includes('还没有线程')), `first visit claimed an empty list: ${first}`);

        // Back and forth: each machine's list is there at once.
        for (const [name, thread] of [['本机', '本机的线程'], ['devbox', 'devbox上的线程'], ['本机', '本机的线程']]) {
          await tab(name).click();
          await title(thread).waitFor({ timeout: 10_000 });
          assert.deepEqual(await seen(), [], `switching to ${name} drew an empty list first`);
          await page.locator('.sidebar .thread-row .agent-mark').first().waitFor({ timeout: 10_000 });
          assert.deepEqual(await names(), [], `switching to ${name} showed agent names before their marks`);
        }

        // A reload remembers too.
        await page.reload();
        if (mobile) await page.locator('.menu-btn').click();
        await title('本机的线程').waitFor({ timeout: 10_000 });
        assert.deepEqual(await seen(), [], 'a reload drew an empty list first');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await hub.stop();
    await node.stop();
    rmSync(root, { recursive: true, force: true });
  }
});
