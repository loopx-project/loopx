import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

async function quotaRequests(page) {
  const gates = new Map();
  let used = 25;
  await page.route('**/api/agents/*/quota*', async route => {
    const agent = new URL(route.request().url()).pathname.split('/')[3];
    const gate = gates.get(agent);
    if (gate) { gates.delete(agent); gate.started(); await gate.wait; }
    await route.fulfill({ json: {
      plan: { id: 'pro', label: 'Pro' }, checkedAt: new Date().toISOString(),
      windows: [
        { label: '5H', usedPercent: used, resetsAt: '2026-11-08T09:00:00Z' },
        { label: '7D', usedPercent: 40, resetsAt: '2026-11-12T09:00:00Z' },
      ],
    } });
  });
  return {
    hold(agent) {
      let release, started;
      const wait = new Promise(resolve => { release = resolve; });
      const pending = new Promise(resolve => { started = resolve; });
      gates.set(agent, { wait, started });
      return { release, pending };
    },
    change() { used += 1; },
  };
}

async function settled(page, selector) {
  await page.waitForFunction(selector => document.querySelector(selector)?.getAttribute('aria-busy') === 'false', selector);
}
async function sameBox(locator, previous, reason) {
  const next = await locator.boundingBox();
  for (const dimension of ['x', 'y', 'width', 'height']) assert.ok(Math.abs(next[dimension] - previous[dimension]) < 1, reason + ': ' + dimension + ' changed from ' + previous[dimension] + ' to ' + next[dimension]);
}

test('A keeps the title and files compact, and quota refreshes do not add or remove a row', { timeout: 180000 }, async () => {
  const command = [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')];
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'primary', adapter: 'codex', label: 'Primary', command },
    { id: 'backup', adapter: 'codex', label: 'Backup', command },
  ] }) });
  const browser = await launchBrowser();
  const shots = process.env.CALLI_E2E_SHOTS_DIR;
  if (shots) mkdirSync(shots, { recursive: true });
  try {
    writeFileSync(join(daemon.workdir, 'topbar-notes.md'), 'A keeps the original file reader.\n');
    const otherDir = join(daemon.workdir, 'other'); mkdirSync(otherDir);
    writeFileSync(join(otherDir, 'other-thread.md'), 'This belongs to another thread.\n');
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'primary', cwd: daemon.workdir, title: '审查双端菜单样式与配置体验' }) })).json();
    const other = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'backup', cwd: otherDir, title: 'Other files' }) })).json();
    for (const width of [320, 390, 1440]) for (const theme of ['light', 'dark']) {
      const mobile = width < 900;
      const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      try {
        const page = await context.newPage(); page.setDefaultTimeout(12000);
        const errors = []; page.on('pageerror', error => errors.push(error.message));
        const requests = await quotaRequests(page), initial = requests.hold('primary');
        await openUi(page, daemon);
        await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' });
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.sidebar .thread-item', { hasText: thread.title }).click();
        await page.locator('.composer textarea').fill('Keep this draft');
        assert.equal(await page.locator('.topbar').evaluate(el => el.offsetHeight), mobile ? 60 : 58);
        assert.equal(await page.locator('.topbar-title').evaluate(el => getComputedStyle(el).fontWeight), '600');
        assert.equal(await page.locator('.topbar-files').count(), 0);
        const heading = await page.locator('.topbar-heading').boundingBox(), actions = await page.locator('.topbar-actions').boundingBox();
        assert.ok(Math.abs(heading.y + heading.height / 2 - actions.y - actions.height / 2) < 1);
        if (shots) await page.screenshot({ path: join(shots, 'a-header-' + width + '-' + theme + '.png'), animations: 'disabled' });
        await page.locator('.topbar-more').click(); await initial.pending;
        const quick = page.locator('.quota-menu-preview');
        await quick.locator('.quota-placeholder').first().waitFor();
        const files = page.getByRole('menuitem', { name: '文件', exact: true });
        const initialFiles = await files.boundingBox();
        assert.equal(await quick.getByRole('meter').count(), 0, 'unknown quota is never shown as zero');
        initial.release();
        await settled(page, '.quota-menu-preview .quota-preview');
        assert.equal(await quick.getByRole('meter').count(), 2);
        await sameBox(files, initialFiles, 'first readings retain the menu item position');
        const refresh = requests.hold('primary'), menuBox = await page.locator('.menu-float').boundingBox();
        const filesBox = await files.boundingBox();
        const refreshButton = quick.getByRole('menuitem', { name: '刷新 Primary 的额度', exact: true });
        await refreshButton.click(); await refresh.pending;
        assert.equal(await refreshButton.getAttribute('aria-busy'), 'true');
        assert.equal(await quick.getByText('更新中…', { exact: true }).count(), 0);
        assert.equal(await quick.getByRole('meter').first().getAttribute('aria-valuenow'), '75');
        await sameBox(files, filesBox, 'updating retains the menu item position');
        await sameBox(page.locator('.menu-float'), menuBox, 'updating retains menu geometry');
        requests.change(); refresh.release();
        await settled(page, '.quota-menu-preview .quota-preview');
        await sameBox(files, filesBox, 'finished refresh retains the menu item position');
        await sameBox(page.locator('.menu-float'), menuBox, 'finished refresh retains menu geometry');
        if (shots) await page.screenshot({ path: join(shots, 'a-quota-menu-' + width + '-' + theme + '.png'), animations: 'disabled' });
        await files.click();
        await page.locator('.files-panel').waitFor();
        await page.locator('.files-panel').getByText('topbar-notes.md', { exact: true }).waitFor();
        await page.locator('.files-panel').getByRole('button', { name: mobile ? '关闭' : '关闭文件', exact: true }).click();
        assert.equal(await page.locator('.composer textarea').inputValue(), 'Keep this draft');
        await page.locator('.topbar-more').click();
        const backup = requests.hold('backup');
        await quick.getByRole('menuitem', { name: '查看全部额度', exact: true }).click();
        const board = page.getByRole('region', { name: '额度', exact: true });
        const primaryRow = board.locator('.quota-account-row', { hasText: 'Primary' });
        const backupRow = board.locator('.quota-account-row', { hasText: 'Backup' });
        await backup.pending;
        const loadingBackup = await backupRow.boundingBox();
        backup.release(); await settled(page, '.quota-account-row:last-of-type .quota-preview');
        await sameBox(backupRow, loadingBackup, 'first list readings retain the account row footprint');
        const firstBox = await primaryRow.boundingBox(), secondBox = await backupRow.boundingBox();
        const primaryRefresh = requests.hold('primary'), backupRefresh = requests.hold('backup');
        await board.getByRole('button', { name: '全部刷新', exact: true }).click();
        await Promise.all([primaryRefresh.pending, backupRefresh.pending]);
        assert.equal(await board.getByText('更新中…', { exact: true }).count(), 0);
        await sameBox(primaryRow, firstBox, 'list refresh retains the first row');
        await sameBox(backupRow, secondBox, 'list refresh retains following rows');
        primaryRefresh.release(); backupRefresh.release();
        await settled(page, '.quota-page-toolbar button');
        await sameBox(primaryRow, firstBox, 'finished list refresh retains the first row');
        await sameBox(backupRow, secondBox, 'finished list refresh retains following rows');
        if (shots) await page.screenshot({ path: join(shots, 'a-quota-board-' + width + '-' + theme + '.png'), animations: 'disabled' });
        await primaryRow.getByRole('button', { name: '查看 Primary 的额度详情', exact: true }).click();
        const detail = page.getByRole('region', { name: 'Primary', exact: true });
        const meter = detail.getByRole('meter').first(); await meter.waitFor();
        const meterBox = await meter.boundingBox();
        const detailRefresh = requests.hold('primary');
        await detail.getByRole('button', { name: '刷新 Primary 的额度', exact: true }).click(); await detailRefresh.pending;
        await sameBox(meter, meterBox, 'detail refresh retains meter position');
        detailRefresh.release(); await settled(page, '.quota-windows');
        await sameBox(meter, meterBox, 'finished detail refresh retains meter position');
        await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
        await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('a cold account detail uses reading-sized placeholders until quota arrives', { timeout: 90000 }, async () => {
  const command = [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')];
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'primary', adapter: 'codex', label: 'Primary', command },
    { id: 'backup', adapter: 'codex', label: 'Backup', command },
  ] }) });
  const browser = await launchBrowser();
  try {
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width, height: 1000 }, isMobile: width < 900, hasTouch: width < 900, colorScheme: theme });
      try {
        const page = await context.newPage(), requests = await quotaRequests(page), initial = requests.hold('backup');
        await openUi(page, daemon);
        await page.goto(daemon.base + '/#/settings/quota/backup'); await initial.pending;
        const detail = page.getByRole('region', { name: 'Backup', exact: true });
        const first = detail.locator('.quota-placeholder').first(); await first.waitFor();
        const placeholderBox = await first.boundingBox();
        const refresh = detail.getByRole('button', { name: '刷新 Backup 的额度', exact: true }), buttonBox = await refresh.boundingBox();
        initial.release(); await settled(page, '.quota-windows');
        await sameBox(detail.getByRole('meter').first(), placeholderBox, 'detail reading retains placeholder dimensions');
        await sameBox(refresh, buttonBox, 'detail action retains its position');
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
