import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';
let daemon, browser;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: ['claude', 'codex', 'traex', 'zcode'].map(id => ({
    id, adapter: id, command: [process.execPath, join(repoRoot, 'daemon/test/fixtures', `fake-${['claude', 'traex'].includes(id) ? 'acp' : id}.mjs`)],
  })) }) });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('native goals, immediate controls and persisted results on desktop/phone in both themes', { timeout: 120_000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      for (const agent of ['codex', 'claude', 'traex', 'zcode']) {
        const title = `目标 ${agent} ${mobile} ${colorScheme}`;
        const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, cwd: daemon.workdir, title }) });
        assert.equal(res.status, 201);
        const thread = await res.json();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: title }).first().click();
        const input = page.locator('.composer textarea');
        await input.fill('/goal');
        await page.getByRole('option', { name: /\/goal/ }).waitFor();
        await page.waitForTimeout(350); // Let the command menu and mobile navigation settle.
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `goal-${agent}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        await input.fill('/goal slow');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByRole('button', { name: '停止', exact: true }).waitFor();
        // Let admission complete before testing the out-of-band control lane.
        await page.waitForTimeout(200);
        await input.fill(agent === 'claude' ? '/goal clear' : '/goal pause');
        const send = page.getByRole('button', { name: '发送', exact: true });
        await send.waitFor();
        await send.click();
        await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
        assert.equal(await page.locator('.queue-item').count(), 0);
        await input.fill('/goal 完成测试');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText(agent === 'claude' ? '原生目标完成。' : /原生 agent 报告已完成/).last().waitFor();
        const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
        const serialized = events.map(e => e.type === 'message.delta' ? e.data.text : '').join('');
        if (agent !== 'claude') { assert.match(serialized, /目标第 1 轮/); assert.match(serialized, /目标第 2 轮/); }
        await page.reload();
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: title }).first().click();
        await page.getByText(agent === 'claude' ? '原生目标完成。' : /原生 agent 报告已完成/).last().waitFor();
        await page.waitForTimeout(350);
        if (process.env.CALLI_E2E_SHOTS_DIR) await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `goal-result-${agent}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
      }
    } finally { await context.close(); }
  }
});
