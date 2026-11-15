// The quota board names the plan each account is on, quietly beside the agent's name, and says
// nothing for an agent that does not report one.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

test('the quota board shows each account plan beside its name', { timeout: 180000 }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'calli-quota-plan-'));
  let daemon, browser;
  try {
    const fixtures = fileURLToPath(new URL('../../daemon/test/fixtures/', import.meta.url));
    const fakeCodex = join(home, 'fake-codex');
    writeFileSync(fakeCodex, `#!/bin/sh\nexec '${process.execPath}' '${join(fixtures, 'fake-codex.mjs')}' "$@"\n`, { mode: 0o755 });
    // A Max 20x login; its tokens must never reach the page.
    mkdirSync(join(home, '.claude'));
    writeFileSync(join(home, '.claude', '.credentials.json'), JSON.stringify({ claudeAiOauth: { accessToken: 'sk-secret-access', subscriptionType: 'max', rateLimitTier: 'default_claude_max_20x' } }));
    daemon = await startDaemon({ CALLI_CODEX_BIN: fakeCodex, CALLI_CLAUDE_QUOTA_BIN: join(fixtures, 'fake-claude-usage.sh'), HOME: home, CLAUDE_CONFIG_DIR: join(home, '.claude'), CODEX_HOME: join(home, '.codex') });
    browser = await launchBrowser();
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: daemon.workdir, title: 'Claude quota periods' }) })).json();
    const shots = process.env.CALLI_E2E_SHOTS_DIR;
    if (shots) mkdirSync(shots, { recursive: true });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
      try {
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', (error) => errors.push(String(error)));
        await openUi(page, daemon);
        if (phone) await page.locator('.menu-btn').click();
        await page.locator('.sidebar .thread-item', { hasText: thread.title }).click();
        assert.equal(await page.locator('.topbar [role=meter], .chat-quota-preview').count(), 0, 'quotas do not occupy the chat header');
        await page.locator('.topbar-more').click();
        const quick = page.locator('.quota-menu-preview');
        await quick.getByRole('meter', { name: '每周', exact: true }).waitFor();
        assert.deepEqual(await quick.getByRole('meter').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-valuenow'))), ['5', '60']);
        assert.equal(await quick.locator('.quota-pools').count(), 0, 'Claude session and all-model week share one visible pair');
        assert.equal(await quick.locator('.quota-missing').count(), 0, 'neither returned period is called missing');
        if (shots) await page.screenshot({ path: join(shots, `claude-menu-${phone ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled' });
        await quick.getByRole('menuitem', { name: '查看全部额度', exact: true }).click();
        const board = page.getByRole('region', { name: '额度', exact: true });
        const plan = (agent) => board.locator('.quota-board-row', { has: page.locator('.quota-board-name', { hasText: new RegExp(`^${agent}$`) }) }).locator('.quota-account-plan');
        await plan('Claude Code').filter({ hasText: 'Max 20x' }).waitFor({ timeout: 30_000 });
        assert.equal(await plan('Claude Code').count(), 1, 'the plan is shown once');
        const claude = board.locator('.quota-board-row', { has: page.locator('.quota-board-name', { hasText: /^Claude Code$/ }) });
        assert.deepEqual(await claude.getByRole('meter').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-valuenow'))), ['5', '60']);
        assert.equal(await claude.locator('.quota-pools, .quota-missing').count(), 0, 'the full overview keeps the same account-wide pair');
        await plan('Codex').filter({ hasText: 'Pro 5x' }).waitFor({ timeout: 30_000 });
        assert.equal(await plan('Codex').getAttribute('title'), 'prolite', "the agent's own word for it, on hover");
        assert.equal(await plan('pi').count(), 0, 'an agent without a plan shows none');
        assert.equal(await plan('TRAE CLI').count(), 0, 'nor does one that has no plan at all');
        assert.doesNotMatch(await board.innerText(), /secret/);
        // Quiet: the plan is not bolder or louder than the name it sits beside.
        const [name, label] = await Promise.all([
          board.locator('.quota-board-name', { hasText: /^Codex$/ }).evaluate((el) => getComputedStyle(el).fontWeight),
          plan('Codex').evaluate((el) => getComputedStyle(el).fontWeight),
        ]);
        assert.ok(Number(label) < Number(name), `plan weight ${label} vs name ${name}`);
        if (shots) await page.screenshot({ path: join(shots, `quota-plan-${phone ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled' });
        assert.deepEqual(errors, []);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    rmSync(home, { recursive: true, force: true });
  }
});

test('live Claude rate limits keep both account-wide periods in the menu and overview', { timeout: 90000 }, async () => {
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'claude', adapter: 'claude', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-acp.mjs')] },
  ] }) });
  const browser = await launchBrowser();
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: daemon.workdir, title: 'Live quota periods' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'rate-limit-tell' }) });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (phone) await page.locator('.menu-btn').click();
        await page.locator('.sidebar .thread-item', { hasText: thread.title }).click();
        await page.getByText('told', { exact: true }).waitFor();
        assert.equal(await page.locator('.topbar [role=meter], .chat-quota-preview').count(), 0);
        await page.locator('.topbar-more').click();
        const quick = page.locator('.quota-menu-preview');
        await quick.getByRole('meter', { name: '每周', exact: true }).waitFor();
        assert.deepEqual(await quick.getByRole('meter').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-valuenow'))), ['10', '58']);
        assert.equal(await quick.locator('.quota-pools, .quota-missing').count(), 0, 'live reports produce one complete pair without a usage probe');
        const shots = process.env.CALLI_E2E_SHOTS_DIR;
        if (shots) await page.screenshot({ path: join(shots, `live-menu-${phone ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled' });
        await quick.getByRole('menuitem', { name: '查看全部额度', exact: true }).click();
        const board = page.getByRole('region', { name: '额度', exact: true });
        await board.getByRole('meter', { name: '每周', exact: true }).waitFor();
        assert.deepEqual(await board.getByRole('meter').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-valuenow'))), ['10', '58']);
        assert.equal(await board.locator('.quota-pools, .quota-missing').count(), 0);
        if (shots) await page.screenshot({ path: join(shots, `live-board-${phone ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled' });
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
