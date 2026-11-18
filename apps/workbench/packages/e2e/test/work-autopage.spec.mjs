import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

// A turn with more work entries than one page holds (100): while it runs, the work panel pages
// by itself — no Load more button, content past the first page arrives on its own. Once the
// turn is done, a fresh open goes back to lazy pages with the manual button.
test('running work panels page by themselves; finished ones keep the manual Load more', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    browser = await launchBrowser();
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '长跑自动翻页' }) })).json();
    const started = await (await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'longrun' }) })).json();

    // Wait until every step is recorded while the turn itself keeps running.
    const deadline = Date.now() + 10000;
    while (true) {
      const events = await (await api(daemon, `/api/threads/${thread.id}/events?all=1`)).json();
      if (events.some(e => e.turnId === started.turnId && e.type === 'tool.end' && e.data?.id === 'L140')) break;
      assert.ok(Date.now() < deadline, 'all 140 steps recorded while the turn ran');
      await new Promise(r => setTimeout(r, 200));
    }

    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const requests = [];
    let page;
    try {
      page = await context.newPage();
      page.on('request', req => requests.push(req.url()));
      await openUi(page, daemon);
      await page.locator('.thread-item').filter({ hasText: '长跑自动翻页' }).first().click();
      await page.locator('.work-body').waitFor();
      // Content from the last page arrived without a click: the panel followed the cursor itself.
      await page.locator('.step').filter({ hasText: 'scan-140' }).waitFor();
      assert.equal(await page.locator('.work-body .history-more').count(), 0, 'no Load more while the turn runs');
      assert.ok(requests.filter(u => u.includes('/history/work') && u.includes('cursor=')).length >= 1, 'continuation pages were followed automatically');
    } finally { await context.close(); }

    // The turn settles; a fresh open keeps finished work lazy, with the manual button.
    const settled = Date.now() + 15000;
    while (true) {
      const events = await (await api(daemon, `/api/threads/${thread.id}/events?all=1`)).json();
      if (events.some(e => e.turnId === started.turnId && e.type === 'turn.done')) break;
      assert.ok(Date.now() < settled, 'the turn finished');
      await new Promise(r => setTimeout(r, 300));
    }
    const done = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
      const view = await done.newPage();
      await openUi(view, daemon);
      await view.locator('.thread-item').filter({ hasText: '长跑自动翻页' }).first().click();
      const work = view.locator('.work').last();
      await work.locator('.work-toggle').click();
      await work.locator('.work-body .history-more').waitFor();
      // Two clicks carry the two remaining pages; then the button is gone and the end is there.
      await work.locator('.work-body .history-more').click();
      await work.locator('.step').filter({ hasText: 'scan-51' }).waitFor();
      await work.locator('.work-body .history-more').click();
      await work.locator('.step').filter({ hasText: 'scan-140' }).waitFor();
      await work.locator('.work-body .history-more').waitFor({ state: 'detached' });
    } finally { await done.close(); }
  } finally { await browser?.close(); await daemon.stop(); }
});
