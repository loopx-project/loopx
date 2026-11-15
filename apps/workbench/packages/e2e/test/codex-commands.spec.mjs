import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon;
let browser;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: ['claude', 'codex'].map((id) => ({
    id, adapter: id, command: [process.execPath, join(repoRoot, 'daemon/test/fixtures', `fake-${id === 'claude' ? 'acp' : id}.mjs`)],
  })) }) });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('Codex quota, commands and skills work on desktop and phone in both themes', async () => {
  for (const mobile of [false, true]) {
    for (const colorScheme of ['light', 'dark']) {
      const title = `命令测试 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
      const response = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title }) });
      assert.equal(response.status, 201);
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: title }).first().click();
        const input = page.locator('.composer textarea');
        await input.fill('/');
        await page.getByRole('option', { name: /\/inspect/ }).waitFor();
        for (const command of ['compact', 'review', 'skills', 'mcp']) assert.equal(await page.locator('.command-name').filter({ hasText: `/${command}` }).count(), 1);
        await input.fill('/usage');
        assert.equal(await page.getByRole('option').count(), 1);
        await input.press('Enter');
        const list = page.getByRole('region', { name: '额度', exact: true });
        await list.getByRole('button', { name: '查看 Codex 的额度详情', exact: true }).click();
        const panel = page.getByRole('region', { name: 'Codex', exact: true });
        await panel.getByRole('meter').first().waitFor();
        assert.equal(await panel.getByRole('meter').count(), 3);
        assert.deepEqual(await panel.getByRole('meter').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-valuenow'))), ['75', '18', '90']);
        assert.deepEqual(await panel.locator('.quota-meter.low .quota-percent').allTextContents(), ['剩余 18%']);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `codex-quota-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
        await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
        await input.fill('/review 检查错误处理');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('审查完成：检查错误处理', { exact: true }).waitFor();
        await input.fill('/inspect');
        await page.getByRole('option', { name: /\/inspect/ }).waitFor();
        await page.waitForTimeout(400);
        if (process.env.CALLI_E2E_SHOTS_DIR) await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `codex-skill-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        await input.press('Enter');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText(/skills:inspect@/).waitFor();
      } finally { await context.close(); }
    }
  }
});
