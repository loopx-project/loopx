import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('ruler knows unloaded and folded turns without fetching their bodies', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '轮次导航' }) })).json();
    const db = new DatabaseSync(join(daemon.dataDir, 'hub.db'));
    let seq = Number(db.prepare('SELECT MAX(seq) AS n FROM events WHERE thread_id=?').get(thread.id).n ?? 0);
    const insert = db.prepare('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)');
    for (let i = 1; i <= 45; i++) {
      for (const [type, data] of [['turn.started', { text: `问题 ${i}` }], ['message.delta', { text: `回答 ${i}` }], ['turn.done', {}]]) {
        insert.run(thread.id, ++seq, `turn-${i}`, type, new Date().toISOString(), JSON.stringify(data));
      }
    }
    db.close();
    const initial = await (await api(daemon, `/api/threads/${thread.id}/history`)).json();
    assert.equal(initial.turnIds.length, 45);
    assert.equal(initial.events.filter(e => e.type === 'turn.started').length, 8);
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        const requests = [];
        const errors = [];
        page.on('request', req => requests.push(req.url()));
        page.on('pageerror', error => errors.push(String(error)));
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: '轮次导航' }).click();
        await page.locator('[data-turn="turn-45"]').waitFor();
        await page.waitForFunction(() => document.querySelectorAll('.turn-ruler .tick').length === 45);
        // Wait for the mobile sidebar transition before measuring or capturing the layout.
        await page.waitForTimeout(400);
        assert.equal(await page.locator('[data-turn]').count(), 8);
        assert.equal(requests.filter(url => url.includes('before=')).length, 0);
        if (!mobile) {
          const first = page.getByRole('button', { name: '跳到第 1 轮', exact: true });
          await first.focus();
          await page.getByText('尚未加载，点击查看', { exact: true }).waitFor();
          assert.equal(requests.filter(url => url.includes('before=')).length, 0);
          if (process.env.CALLI_E2E_SHOTS_DIR) {
            mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
            await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `ruler-placeholder-${colorScheme}.png`) });
          }
          await first.click();
          await page.locator('[data-turn="turn-1"]').waitFor();
          await page.waitForFunction(() => {
            const row = document.querySelector('[data-turn="turn-1"]');
            const box = row.getBoundingClientRect();
            return box.top >= 0 && box.bottom < innerHeight;
          });
          assert.equal(await page.locator('.turn-ruler .tick').count(), 45);
          assert.equal(requests.filter(url => /\/history\/(work|detail)/.test(url)).length, 0);
        } else assert.equal(await page.locator('.turn-ruler').isVisible(), false);
        assert.deepEqual(errors, []);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `ruler-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
