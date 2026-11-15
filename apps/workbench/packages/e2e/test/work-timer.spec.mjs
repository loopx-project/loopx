import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')] }] }) });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('running time ticks without events, survives reload and freezes when stopped or completed in four layouts', { timeout: 120000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 360, height: 780 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const title = `运行计时 ${mobile} ${colorScheme}`;
      const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', title, cwd: daemon.workdir }) });
      assert.equal(created.status, 201);
      const thread = await created.json();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      await page.getByRole('textbox', { name: '消息', exact: true }).fill('tool slow');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      const label = page.locator('.work-label').first();
      await page.locator('.step-title').first().waitFor();
      const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      const started = Date.parse(events.find(e => e.type === 'turn.started').ts);
      await page.clock.setFixedTime(started + 65000);
      await page.getByText('工作中 · 1 分 5 秒', { exact: true }).waitFor();
      // Folding the steps must not freeze the timer, even while the agent is silent.
      await page.locator('.work-toggle').first().click();
      await page.clock.setFixedTime(started + 66000);
      await page.getByText('工作中 · 1 分 6 秒', { exact: true }).waitFor();
      const later = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      assert.equal(later.length, events.length, 'the clock advances independently of streamed events');
      await page.reload();
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      await page.getByText('工作中 · 1 分 6 秒', { exact: true }).waitFor();
      await page.clock.setFixedTime(started + 3605000);
      await page.getByText('工作中 · 1 小时 0 分 5 秒', { exact: true }).waitFor();
      assert.match(await page.locator('.work-toggle').first().innerText(), /1 步/);
      assert.ok(await page.locator('.work-toggle').first().evaluate(el => el.getBoundingClientRect().right <= window.innerWidth));
      if (shots) await page.screenshot({ path: join(shots, `timer-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
      await page.getByRole('button', { name: '停止', exact: true }).click();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      const stopped = await label.textContent();
      assert.match(stopped, /停止/);
      await page.clock.setFixedTime(started + 7200000);
      await page.waitForTimeout(1100);
      assert.equal(await label.textContent(), stopped);

      await page.clock.setFixedTime(Date.now());
      await page.getByRole('textbox', { name: '消息', exact: true }).fill('tool');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      const completed = page.locator('.work-label').last();
      await page.getByText(/^用时 /).waitFor();
      const time = await completed.textContent();
      await page.clock.setFixedTime(Date.now() + 120000);
      await page.waitForTimeout(1100);
      assert.equal(await completed.textContent(), time);
    } finally { await context.close(); }
  }
});
