import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, connect, api } from '../harness.mjs';

test('thread deep links survive connection, open on desktop/mobile in both themes and clear the address', async () => {
  const daemon = await startDaemon(); const browser = await launchBrowser();
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: 'Deep link thread' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'Deep link content' }) });
    while ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'running') await new Promise(resolve => setTimeout(resolve, 20));
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'personal-notify' }) });
    while ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'running') await new Promise(resolve => setTimeout(resolve, 20));
    const shots = join(import.meta.dirname, '../shots/push'); mkdirSync(shots, { recursive: true });
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      assert.equal((await api(daemon, `/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ archived: true }) })).status, 200);
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 }, colorScheme: theme, isMobile: mobile });
      const page = await context.newPage();
      await page.goto(`${daemon.base}/#/thread/${thread.id}`);
      await connect(page, daemon.token);
      await page.waitForFunction(() => !location.hash);
      await page.locator('.topbar-title', { hasText: 'Deep link thread' }).waitFor();
      assert.equal((await (await api(daemon, `/api/threads/${thread.id}`)).json()).archivedAt, null);
      await page.getByText('Deep link content', { exact: true }).first().waitFor();
      await page.getByText('正在加载…', { exact: true }).waitFor({ state: 'hidden' });
      await page.locator('.agent-notice', { hasText: 'Invisible notice fixture' }).waitFor();
      assert.equal(await page.locator('.agent-notice', { hasText: 'Invisible notice fixture' }).count(), 1);
      await page.getByText('Notice recorded.', { exact: true }).first().waitFor();
      await page.screenshot({ path: join(shots, `thread-${mobile ? 'mobile' : 'desktop'}-${theme}.png`), fullPage: true });
      await page.evaluate(() => { location.hash = '#/thread/missing-thread'; });
      await page.getByText('找不到这个线程，它可能已被删除。', { exact: true }).waitFor();
      assert.ok((await page.locator('.topbar-title').innerText()).includes('Deep link thread'));
      await context.close();
    }
  } finally { await browser.close(); await daemon.stop(); }
});
