import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

// Opt-in: discover an installed CLI's signed-in account without sending a paid model request.
test('real zcode account models are selectable on desktop and phone in both themes', { skip: !process.env.CALLI_ZCODE_REAL_BIN, timeout: 60000 }, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'calli-zcode-account-ui-'));
  let daemon;
  let browser;
  try {
    daemon = await startDaemon({
      ZCODE_DATA_BASE_DIR: process.env.ZCODE_DATA_BASE_DIR || homedir(),
      ZCODE_STORAGE_DIR: dir, ZCODE_SESSION_DB_PATH: join(dir, 'sessions.db'),
      CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'zcode', adapter: 'zcode', command: [process.env.CALLI_ZCODE_REAL_BIN] }] }),
    });
    const models = await (await api(daemon, '/api/agents/zcode/models')).json();
    assert.ok(Array.isArray(models) && models.length > 0, 'The real account must expose native model options');
    assert.ok(models.some(m => JSON.parse(m.id)[0].startsWith('account:')));
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        await page.getByRole('button', { name: /^模型与思考强度：/ }).click();
        const switcher = page.getByRole('button', { name: /^切换模型，当前/ });
        if (await switcher.count()) await switcher.click();
        for (const model of models) await page.getByRole('menuitemradio', { name: model.label, exact: true }).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          await mkdir(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.waitForTimeout(300);
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `zcode-account-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        const chosen = models.at(-1);
        await page.getByRole('menuitemradio', { name: chosen.label, exact: true }).click();
        await page.getByRole('button', { name: `切换模型，当前 ${chosen.label}`, exact: true }).waitFor();
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    await rm(dir, { recursive: true, force: true });
  }
});
