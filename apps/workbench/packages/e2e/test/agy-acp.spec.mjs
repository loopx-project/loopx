import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('Antigravity effort picker, reload and compact preserve the native ACP conversation on four layouts', { timeout: 120000 }, async () => {
  const root = mkdtempSync(join(tmpdir(), 'agy-acp-browser-'));
  const log = join(root, 'rpc.jsonl');
  const daemon = await startDaemon({}, { agents: [{ id: 'agy-secondary', adapter: 'agy', label: 'Antigravity', command: ['fake-agy'], env: { FAKE_AGY_RPC_LOG: log } }] });
  const browser = await launchBrowser();
  const get = async path => (await api(daemon, path)).json();
  const waitFor = async check => {
    for (let i = 0; i < 150; i++) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
    assert.fail('native state did not settle');
  };
  try {
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      writeFileSync(log, '');
      const response = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'agy-secondary', cwd: daemon.workdir, title: 'Antigravity reasoning', model: 'fast', effort: 'low' }) });
      assert.equal(response.status, 201);
      const thread = await response.json();
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage(), errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await openUi(page, daemon);
        await page.evaluate(id => { location.hash = `#/thread/${id}`; }, thread.id);
        if (mobile) await page.locator('.composer textarea').last().tap();
        const picker = page.locator('.composer .model-control-trigger');
        await waitFor(async () => (await picker.getAttribute('aria-label'))?.includes('Fast · 低'));
        const send = async text => {
          await page.locator('.composer textarea').last().fill(text);
          await page.keyboard.press('Escape');
          const receipt = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === `/api/threads/${thread.id}/turns`);
          await page.locator('.composer .send-btn:not(.stop)').click();
          assert.equal((await receipt).status(), 202);
          await waitFor(async () => (await get(`/api/threads/${thread.id}`)).status === 'idle');
        };
        await send('First question');
        const nativeId = (await get(`/api/threads/${thread.id}`)).agentSessionId;
        if (mobile) await page.locator('.composer textarea').last().tap();
        await picker.click();
        await page.getByRole('slider', { name: '思考强度' }).press('End');
        await waitFor(async () => (await get(`/api/threads/${thread.id}`)).effort === 'high');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (process.env.LOOCHAT_E2E_SHOTS_DIR) {
          mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `agy-effort-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
        }
        await page.keyboard.press('Escape');
        await send('Think more deeply');
        await page.locator('.markdown').filter({ hasText: 'model:fast;effort:high;' }).last().waitFor();
        await page.reload();
        await page.locator('.sidebar-inner').waitFor({ state: 'attached' });
        await page.evaluate(id => { location.hash = `#/thread/${id}`; }, thread.id);
        if (mobile) await page.locator('.composer textarea').last().tap();
        await waitFor(async () => (await picker.getAttribute('aria-label'))?.includes('Fast · 高'));
        await send('/compact');
        const events = await get(`/api/threads/${thread.id}/events`);
        assert.ok(events.some(e => e.type === 'tool.end' && e.data.id === 'context-compaction' && e.data.status === 'completed'));
        assert.ok(events.some(e => e.type === 'usage.updated' && e.data.used === 250));
        assert.equal((await get(`/api/threads/${thread.id}`)).agentSessionId, nativeId);
        const calls = readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line));
        assert.equal(new Set(calls.map(c => c.pid)).size, 1, 'effort changes and compaction keep one native process');
        assert.ok(calls.some(c => c.method === 'session/set_config_option' && c.params.configId === 'thought_level' && c.params.value === 'high'));
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); rmSync(root, { recursive: true, force: true }); }
});
