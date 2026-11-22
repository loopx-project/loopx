import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

// The native catalog observed before and after the account's model entitlement propagated.
const model = (id, label) => ({ id, label });
const flash = ['high', 'medium', 'low'].map(effort => model(`gemini-3.8-flash-${effort}`, `Gemini 3.8 Flash (${effort})`));
const oss = model('gpt-oss-120b-medium', 'GPT-OSS 120B (Medium)');
const initial = [...flash, model('claude-sonnet-4-6', 'Claude Sonnet 4.6 (Thinking)'), model('claude-opus-4-6-thinking', 'Claude Opus 4.6 (Thinking)'), oss];
const complete = [
  ...['medium', 'high', 'low'].map(e => model(`gemini-4-argon-${e}`, `Gemini 4 Argon (${e})`)),
  ...['high', 'xhigh', 'medium', 'low'].map(e => model(`gemini-4-hydrogen-${e}`, `Gemini 4 Hydrogen (${e})`)),
  model('gemini-pro-orchestrator-g1-pro', 'Orchestrator (Argon)'), ...flash,
  ...['opus', 'sonnet'].flatMap(f => ['low', 'medium', 'high'].map(e => model(`claude-${f}-5-5-${e}`, `Claude ${f} 5.5 (${e})`))), oss,
];

test('agy models group effort variants into an effort slider and refresh in an open picker without changing the chosen model', { timeout: 120_000 }, async () => {
  const root = mkdtempSync(join(tmpdir(), 'loochat-agy-models-e2e-'));
  const file = join(root, 'catalog.json');
  const catalog = models => writeFileSync(file, JSON.stringify({ models }));
  catalog(initial);
  const daemon = await startDaemon({}, { agents: [{ id: 'agy', adapter: 'agy', label: 'Antigravity', command: ['fake-agy'], env: { FAKE_AGY_MODELS_FILE: file } }] });
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      catalog(initial);
      assert.equal((await (await api(daemon, '/api/agents/agy/models?refresh=1')).json()).length, 4);
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        await context.addInitScript(() => localStorage.setItem('calli_onboard_dismissed', '1'));
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(String(error)));
        await openUi(page, daemon);
        await page.locator('.model-control-trigger').click();
        await page.locator('.effort-card').waitFor();
        await page.locator('.effort-model').click();
        await page.getByRole('menuitemradio', { name: 'Claude Sonnet 4.6 (Thinking)', exact: true }).waitFor();
        const preferences = await (await api(daemon, '/api/draft-model-preferences')).json();
        catalog(complete);
        const refresh = page.getByRole('button', { name: '刷新模型列表', exact: true });
        await refresh.click();
        await page.getByRole('menuitemradio', { name: 'Gemini 4 Argon', exact: true }).waitFor();
        await page.getByRole('menuitemradio', { name: 'Gemini 4 Hydrogen', exact: true }).waitFor();
        assert.equal(await page.locator('.model-control-panel [role="menuitemradio"]').count(), 8);
        const last = page.getByRole('menuitemradio', { name: 'Claude sonnet 5.5', exact: true });
        await last.scrollIntoViewIfNeeded();
        assert.ok(await last.isVisible());
        if (process.env.LOOCHAT_E2E_SHOTS_DIR) {
          mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `agy-models-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
        }
        // A refresh from another client updates this already-open picker through the shared stream.
        catalog([...complete, model('gemini-later', 'Newly available model')]);
        await api(daemon, '/api/agents/agy/models?refresh=1');
        await page.getByRole('menuitemradio', { name: 'Newly available model', exact: true }).waitFor();
        assert.deepEqual(await (await api(daemon, '/api/draft-model-preferences')).json(), preferences);
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); rmSync(root, { recursive: true, force: true }); }
});
