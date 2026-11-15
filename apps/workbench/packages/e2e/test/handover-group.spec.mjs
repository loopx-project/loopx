import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('handover groups and bidirectional navigation on desktop and phone in both themes', async () => {
  const hub = await startDaemon();
  let browser;
  try {
    const post = async (path, body) => {
      const res = await api(hub, path, { method: 'POST', body: JSON.stringify(body) });
      assert.equal(res.status, 201);
      return res.json();
    };
    const original = await post('/api/threads', { agent: 'pi', cwd: hub.workdir, title: '增加技能管理功能' });
    const next = await post(`/api/threads/${original.id}/handover-thread`, { agent: 'claude' });
    const last = await post(`/api/threads/${next.id}/handover-thread`, { agent: 'agy' });
    const agents = await (await api(hub, '/api/agents')).json();
    const label = (id) => agents.find((a) => a.id === id)?.label ?? id;
    const passed = ['pi', 'claude', 'agy'].map(label).join(' → ');
    browser = await launchBrowser();
    mkdirSync('/tmp/calli-handover-shots', { recursive: true });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.addInitScript((token) => localStorage.setItem('calli.token', token), hub.token);
      await openUi(page, hub);
      if (phone) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.handover-group').waitFor();
      assert.equal(await page.locator('.handover-group').count(), 1);
      assert.equal(await page.locator('.handover-group .thread-item').count(), 1);
      // Only the stacked agent marks remain; relationship details live in the hover hint.
      assert.equal(await page.locator('.sidebar .thread-fork').count(), 0);
      assert.match(await page.locator('.handover-group .thread-item').getAttribute('title'), /交接：/);
      const stack = page.locator('.handover-group .thread-item .agent-stack');
      assert.equal(await stack.getAttribute('title'), passed);
      assert.equal(await stack.getAttribute('aria-label'), label('agy'));
      assert.equal(await stack.locator('svg').count(), 2);
      await page.locator('.handover-toggle').click();
      assert.equal(await page.locator('.handover-chain .handover-member').count(), 3);
      const offsets = await page.locator('.handover-chain .handover-member').evaluateAll(rows => rows.map(row => {
        const dot = row.querySelector('.dot').getBoundingClientRect();
        const line = getComputedStyle(row, '::before');
        return Math.abs(row.getBoundingClientRect().left + parseFloat(line.left) + parseFloat(line.width) / 2 - (dot.left + dot.width / 2));
      }));
      assert.ok(offsets.every(offset => offset <= 0.5), `chain line crosses dot centers: ${offsets}`);
      await page.locator('.handover-member .handover-member-time').first().waitFor();
      await page.waitForTimeout(250);
      await page.screenshot({ path: `/tmp/calli-handover-shots/${phone ? 'phone' : 'desktop'}-${theme}-sidebar.png` });
      await page.locator('.handover-group > .thread-row .thread-item').click();
      await page.getByLabel('这个线程的更多操作').click();
      await page.getByRole('menuitem', { name: '分叉与交接', exact: true }).click();
      await page.getByRole('menuitem', { name: '交接记录', exact: true }).click();
      await page.getByRole('dialog', { name: '交接记录', exact: true }).waitFor();
      await page.waitForTimeout(250);
      await page.screenshot({ path: `/tmp/calli-handover-shots/${phone ? 'phone' : 'desktop'}-${theme}-trail.png` });
      await page.locator('.handover-entry').filter({ hasText: '原始会话' }).click();
      await page.locator('.topbar-title').filter({ hasText: original.title }).waitFor();
      await page.getByLabel('这个线程的更多操作').click();
      await page.getByRole('menuitem', { name: '分叉与交接', exact: true }).click();
      await page.getByRole('menuitem', { name: '交接记录', exact: true }).click();
      await page.locator('.handover-entry').filter({ hasText: '接手会话' }).first().click();
      await page.locator('.topbar-title').filter({ hasText: next.title }).waitFor();
      assert.deepEqual(errors, []);
      assert.ok(next.handover.sourceId === original.id);
      assert.ok(last.handover.sourceId === next.id);
      await context.close();
    }
  } finally {
    await browser?.close();
    await hub.stop();
  }
});

test('a turn stopped by a plan that ran out offers another account of the same type, which takes over in one click', async () => {
  // One product with two accounts: the one-click suggestion stays inside the same adapter.
  const hub = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'claude', adapter: 'claude', command: ['fake-claude'] },
    { id: 'claude2', adapter: 'claude', label: 'Claude 备用', command: ['fake-claude'] },
  ] }) });
  let browser;
  try {
    const res = await api(hub, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: hub.workdir, title: '额度用完的任务' }) });
    const source = await res.json();
    await api(hub, `/api/threads/${source.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'out of plan' }) });
    browser = await launchBrowser();
    mkdirSync('/tmp/calli-handover-shots', { recursive: true });
    // The takeover goes last: after it, the sidebar's top row for this task is the successor.
    for (const phone of [true, false]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.addInitScript((token) => localStorage.setItem('calli.token', token), hub.token);
      await openUi(page, hub);
      if (phone) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.thread-item').filter({ hasText: '额度用完的任务' }).first().click();
      const go = page.locator('.exhausted-note .exhausted-hand');
      await go.waitFor();
      assert.match(await page.locator('.exhausted-note').innerText(), /额度用尽/);
      assert.match(await go.innerText(), /交给 .+ 接手/);
      await page.waitForTimeout(400);
      await page.screenshot({ path: `/tmp/calli-handover-shots/${phone ? 'phone' : 'desktop'}-${theme}-exhausted.png` });
      if (phone || theme === 'light') { await context.close(); continue; }
      const [takeover] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/takeover') && r.request().method() === 'POST'), go.click()]);
      assert.equal(takeover.request().postDataJSON().agent, 'claude2');
      assert.equal((await takeover.json()).agent, 'claude2');
      assert.equal((await takeover.json()).handover.mode, 'continue');
      await page.locator('.topbar-title').filter({ hasText: '（接手）' }).waitFor();
      // A same-type relay leaves no seam: the merged conversation reads as one.
      await page.locator('.merged-history').waitFor();
      assert.equal(await page.locator('.handover-boundary').count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
  } finally {
    await browser?.close();
    await hub.stop();
  }
});
