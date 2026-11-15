import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('preparation and response waits survive reload and clear on output, stop and failure', { timeout: 90000 }, async () => {
  const daemon = await startDaemon({ FAKE_PI_START_DELAY_MS: '1200', FAKE_PI_FIRST_OUTPUT_DELAY_MS: '6000' });
  let browser;
  const post = async (path, body) => {
    const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    assert.ok(response.ok, await response.clone().text());
    return response.json();
  };
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const title = `等待状态 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
      const thread = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(String(error)));
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item').filter({ hasText: title }).click();
        await page.locator('.composer textarea').fill('wait-first');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        const wait = page.locator('.turn-waiting');
        await wait.filter({ hasText: '正在准备' }).waitFor();
        await wait.filter({ hasText: '等待响应' }).waitFor();
        const initial = await wait.innerText();
        await page.waitForTimeout(1100);
        assert.notEqual(await wait.innerText(), initial, 'the clock advances before any output');
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `startup-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        await page.reload();
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item').filter({ hasText: title }).click();
        await wait.filter({ hasText: '等待响应' }).waitFor();
        await page.getByText('Response received.', { exact: true }).waitFor();
        await wait.waitFor({ state: 'detached' });
        const events = await (await api(daemon, `/api/threads/${thread.id}/events?all=1`)).json();
        const phases = events.filter(e => e.type === 'turn.phase');
        assert.deepEqual(phases.map(e => e.data.phase), ['session_start', 'session_ready', 'prompt_sent', 'model_request']);
        assert.equal(phases[0].data.reused, false);
        assert.ok(phases[1].data.sincePreviousPhaseMs >= 1000, 'startup is timed separately');
        for (let i = 1; i < phases.length; i++) assert.ok(phases[i].data.elapsedMs >= phases[i - 1].data.elapsedMs);

        const next = await post(`/api/threads/${thread.id}/turns`, { text: 'wait-first' });
        await wait.waitFor();
        await page.getByRole('button', { name: '停止', exact: true }).click();
        await wait.waitFor({ state: 'detached' });
        const stopped = await (await api(daemon, `/api/threads/${thread.id}/events?all=1`)).json();
        assert.equal(stopped.find(e => e.turnId === next.turnId && e.data.phase === 'session_start')?.data.reused, true);
        assert.ok(stopped.some(e => e.turnId === next.turnId && e.type === 'turn.done' && e.data.stopReason === 'cancelled'));
        await post(`/api/threads/${thread.id}/turns`, { text: 'wait-first-fail' });
        await wait.waitFor();
        await page.getByText('provider unavailable', { exact: true }).waitFor();
        await wait.waitFor({ state: 'detached' });
        assert.deepEqual(errors, []);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
