import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

test('TRAE model load and weekly quota appear in existing controls in both themes and sizes', async () => {
  const home = mkdtempSync(join(tmpdir(), 'calli-traex-ui-'));
  let daemon, browser;
  try {
    const fixtures = fileURLToPath(new URL('../../daemon/test/fixtures/', import.meta.url));
    const fakeCodex = join(home, 'fake-codex');
    writeFileSync(fakeCodex, `#!/bin/sh\nexec '${process.execPath}' '${join(fixtures, 'fake-codex.mjs')}' "$@"\n`, { mode: 0o755 });
    daemon = await startDaemon({ CALLI_CODEX_BIN: fakeCodex, CALLI_CLAUDE_QUOTA_BIN: join(fixtures, 'fake-claude-usage.sh'), HOME: home, CLAUDE_CONFIG_DIR: join(home, '.claude'), CODEX_HOME: join(home, '.codex'), CALLI_TRAEX_BIN: 'fake-claude', FAKE_ACP_FLAVOR: 'traex' });
    browser = await launchBrowser();
    const shots = process.env.CALLI_TRAEX_SHOTS;
    if (shots) mkdirSync(shots, { recursive: true });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
      const page = await context.newPage();
      await page.addInitScript((token) => localStorage.setItem('calli.token', token), daemon.token);
      await openUi(page, daemon);
      await page.locator('.composer .context-btn').first().click();
      await page.getByRole('menuitemradio', { name: /^TRAE CLI/ }).click();
      await page.locator('.model-control-trigger').click();
      const inlineLoad = page.locator('.effort-model .model-inline-load');
      await inlineLoad.waitFor();
      assert.match(await inlineLoad.innerText(), /184%$/);
      assert.match(await inlineLoad.getAttribute('title'), /负载\s*184%/);
      assert.equal(await page.locator('.model-control-trigger .model-inline-load').count(), 0, 'load stays in the model details');
      await page.getByRole('button', { name: /^切换模型，当前/ }).click();
      await page.getByText(/模型负载 184%.*每周额度剩余 99%/).waitFor();
      assert.equal(await page.getByText(/模型负载 0%/).count(), 1);
      await page.waitForTimeout(300);
      if (shots) await page.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${theme}-models.png`) });
      await page.keyboard.press('Escape');
      if (phone) await page.locator('.menu-btn').click();
      await page.getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-nav').getByRole('button', { name: '额度', exact: true }).click();
      const list = page.getByRole('region', { name: '额度', exact: true });
      const row = list.getByRole('button', { name: '查看 TRAE CLI 的额度详情', exact: true });
      await row.getByRole('meter').waitFor();
      assert.match(await page.locator('.quota-board-row').first().innerText(), /TRAE CLI/);
      assert.equal(await row.locator('.quota-percent').innerText(), '剩余 99%');
      await row.locator('.quota-load.high').waitFor();
      await row.click();
      const detail = page.getByRole('region', { name: 'TRAE CLI', exact: true });
      assert.match(await detail.locator('.quota-load').innerText(), /负载\s*184%/);
      assert.match(await detail.getByRole('meter').getAttribute('aria-label'), /Big 1\.0/);
      await detail.getByRole('button', { name: '刷新 TRAE CLI 的额度' }).click();
      await detail.getByText(/按模型显示每周额度/).waitFor();
      await detail.getByRole('button', { name: '刷新 TRAE CLI 的额度' }).filter({ visible: true }).waitFor();
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      await Promise.all([
        page.waitForRequest((r) => r.url().includes('/api/agents/traex/quota?refresh=1')),
        list.getByRole('button', { name: '全部刷新' }).click(),
      ]);
      await page.waitForTimeout(300);
      if (shots) await page.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${theme}-quota.png`) });
      await context.close();
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    rmSync(home, { recursive: true, force: true });
  }
});
