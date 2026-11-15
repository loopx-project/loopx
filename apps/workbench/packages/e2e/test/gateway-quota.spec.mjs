// A Claude instance on a model gateway shows the plans and balances the gateway reports for the
// models it can reach: GLM Coding Plan windows as meters, a DeepSeek key's balance as an amount.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';
import { modelGateway } from '../../daemon/test/fixtures/model-gateway.mjs';

test('a gateway account shows plan windows and key balances on the board, menu and picker', { timeout: 180000 }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'calli-gateway-quota-'));
  const gateway = await modelGateway();
  const reset = new Date(Date.now() + 3 * 3600_000).toISOString();
  gateway.state.models = [{ id: 'zcode/GLM-5.3' }, { id: 'deepseek/deepseek-flash' }];
  gateway.state.quotas = [
    { provider: 'zcode', name: 'ZCode', kind: 'subscription', plan: 'GLM Coding Pro', windows: [
      { name: '5 hours', used: 100, resetsAt: reset }, { name: 'Weekly', used: 37, resetsAt: new Date(Date.now() + 5 * 86400_000).toISOString() },
    ] },
    { provider: 'deepseek', name: 'DeepSeek', kind: 'balance', windows: [], balance: '¥60.52' },
  ];
  let daemon, browser;
  try {
    daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
      { id: 'claude-gw', adapter: 'claude', label: 'Claude Code · Gateway', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-acp.mjs')],
        env: { CLAUDE_CONFIG_DIR: home, ANTHROPIC_BASE_URL: gateway.url, CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1' } },
    ] }) });
    const quota = await (await api(daemon, '/api/agents/claude-gw/quota')).json();
    assert.deepEqual(quota.balances, [{ scope: 'DeepSeek', amount: '¥60.52', modelIds: ['deepseek/deepseek-flash'] }]);
    browser = await launchBrowser();
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude-gw', cwd: daemon.workdir, title: 'Gateway quota' }) })).json();
    const shots = process.env.CALLI_E2E_SHOTS_DIR;
    if (shots) mkdirSync(shots, { recursive: true });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
      try {
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', (error) => errors.push(String(error)));
        const name = (view) => join(shots ?? '', `gateway-${view}-${phone ? 'phone' : 'desktop'}-${theme}.png`);
        await openUi(page, daemon);
        if (phone) await page.locator('.menu-btn').click();
        await page.locator('.sidebar .thread-item', { hasText: thread.title }).click();

        await page.locator('.topbar-more').click();
        const quick = page.locator('.quota-menu-preview');
        await quick.locator('.quota-balance', { hasText: '¥60.52' }).waitFor();
        assert.deepEqual(await quick.getByRole('meter').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-valuenow'))), ['0', '63']);
        assert.equal(await quick.locator('.quota-menu-head.attention').count(), 1, 'a used-up window still asks for attention');
        if (shots) await page.screenshot({ path: name('menu'), animations: 'disabled' });

        await quick.getByRole('menuitem', { name: '查看全部额度', exact: true }).click();
        const board = page.getByRole('region', { name: '额度', exact: true });
        const row = board.locator('.quota-board-row', { hasText: 'Claude Code · Gateway' });
        await row.locator('.quota-balance', { hasText: '¥60.52' }).waitFor();
        assert.equal(await row.locator('.quota-pool-name').innerText(), 'GLM Coding Pro');
        assert.equal(await row.locator('.quota-balance.exhausted').count(), 0, 'a balance with money left stays neutral');
        if (shots) await page.screenshot({ path: name('board'), animations: 'disabled' });
        await row.locator('.quota-account-open').click();
        await page.locator('.quota-balance-row', { hasText: '¥60.52' }).waitFor();
        assert.equal(await page.locator('.quota-account-detail').getByRole('meter').count(), 2);
        if (shots) await page.screenshot({ path: name('detail'), animations: 'disabled', fullPage: true });
        // The account picker: the balance is one more chip on the summary line.
        await openUi(page, daemon);
        await page.locator('.agent-btn:visible').click();
        await page.locator('.agent-picker-panel').waitFor();
        const summary = page.locator('.agent-picker-panel .agent-quota-summary').first();
        await summary.waitFor();
        assert.match(await summary.getAttribute('aria-label'), /DeepSeek 余额 ¥60\.52/);
        await summary.click();
        await page.locator('.agent-quota-details .agent-quota-row', { hasText: '¥60.52' }).waitFor();
        if (shots) await page.screenshot({ path: name('picker'), animations: 'disabled' });
        assert.deepEqual(errors, []);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    await gateway.close();
    rmSync(home, { recursive: true, force: true });
  }
});
