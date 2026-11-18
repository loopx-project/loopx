import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { api, launchBrowser, openUi, repoRoot, startDaemon } from '../harness.mjs';

test('interrupted work keeps prior thoughts visible without extra folding', async () => {
  const daemon = await startDaemon({
    RAH_AGENTS_JSON: JSON.stringify({ agents: [{
      id: 'codex',
      adapter: 'codex',
      command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')],
    }] }),
  });
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const title = `插队工作记录 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
      const thread = await (await api(daemon, '/api/threads', {
        method: 'POST',
        body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title }),
      })).json();
      await api(daemon, `/api/threads/${thread.id}/turns`, {
        method: 'POST',
        body: JSON.stringify({ text: 'reason tool fail slow' }),
      });

      const context = await browser.newContext({
        viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
        isMobile: mobile,
        hasTouch: mobile,
        colorScheme,
      });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: title }).click();

        // The running turn has enough work to be grouped. Interrupting it must keep all of that
        // work, and the stopped history stays open so earlier thinking does not disappear.
        await page.locator('.work-body .run-head').waitFor();
        await api(daemon, `/api/threads/${thread.id}/turns`, {
          method: 'POST',
          body: JSON.stringify({ text: 'urgent', queue: true }),
        });
        await page.getByRole('button', { name: '立即发送', exact: true }).click();

        const stopped = page.locator('.row.work', { has: page.locator('.work-toggle', { hasText: '停止了' }) });
        await stopped.locator('.work-toggle').waitFor();
        assert.match(await stopped.locator('.work-toggle').innerText(), /\d+ 步/);
        assert.equal(await stopped.locator('.work-toggle').getAttribute('aria-expanded'), 'true');

        assert.equal(await stopped.locator('.run-head').count(), 0, 'stopped work has no second run disclosure');
        assert.equal(await stopped.locator('.thinking-toggle').count(), 1, 'the prior thought remains visible');
        assert.equal(await stopped.locator('.thinking-toggle').getAttribute('aria-expanded'), 'true');
        assert.equal(await stopped.locator('.step').count(), 2, 'the prior tools are shown immediately');
        await stopped.locator('.thinking-para', { hasText: 'thinking hard' }).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

        await page.reload();
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: title }).waitFor();
        await page.locator('.thread-item', { hasText: title }).click();
        const reloaded = page.locator('.row.work', { has: page.locator('.work-toggle', { hasText: '停止了' }) });
        assert.equal(await reloaded.locator('.work-toggle').getAttribute('aria-expanded'), 'true');
        assert.equal(await reloaded.locator('.run-head').count(), 0, 'reloaded stopped work has no second run disclosure');
        assert.equal(await reloaded.locator('.thinking-toggle').getAttribute('aria-expanded'), 'true');
        await reloaded.locator('.thinking-para', { hasText: 'thinking hard' }).waitFor();

        if (process.env.RAH_E2E_SHOTS_DIR) {
          mkdirSync(process.env.RAH_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({
            animations: 'disabled',
            path: join(process.env.RAH_E2E_SHOTS_DIR, `interrupted-work-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`),
          });
        }
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser?.close();
    await daemon.stop();
  }
});
