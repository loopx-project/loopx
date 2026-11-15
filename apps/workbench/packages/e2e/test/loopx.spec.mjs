import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, repoRoot, api } from '../harness.mjs';
let daemon, browser, thread;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'pi', adapter: 'pi', command: ['fake-pi'] }] }) });
  browser = await launchBrowser();
  const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: 'Plugin validation' }) });
  assert.equal(created.status, 201); thread = await created.json();
});
after(async () => { await browser?.close(); await daemon?.stop(); });
async function shot(page, name) { if (shots) { mkdirSync(shots, { recursive: true }); await page.screenshot({ path: join(shots, name + '.png'), animations: 'disabled' }); } }

test('supported LoopX is visible before installation and remains discoverable after removal', async () => {
  for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, colorScheme: theme, isMobile: mobile, hasTouch: mobile });
    try {
      const page = await context.newPage();
      await openUi(page, daemon); await page.goto(daemon.base + '/#/extensions');
      const card = page.locator('.ext-card').filter({ has: page.getByRole('heading', { name: 'LoopX', exact: true }) });
      await card.getByText('未安装', { exact: true }).waitFor();
      await card.getByText('支持的插件', { exact: true }).waitFor();
      assert.equal(await card.getByRole('button', { name: '配置关联' }).count(), 0);
      await card.scrollIntoViewIfNeeded();
      await shot(page, `loopx-catalog-${mobile ? 'phone' : 'desktop'}-${theme}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (!mobile && theme === 'light') {
        await card.getByRole('button', { name: '安装', exact: true }).click();
        await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click();
        await card.getByText('未安装', { exact: true }).waitFor();
        await card.getByRole('button', { name: '安装', exact: true }).click();
        await page.getByRole('dialog').getByRole('button', { name: '安装', exact: true }).click();
        await card.getByRole('button', { name: '配置关联' }).waitFor();
        assert.equal(await card.count(), 1);
        await card.getByRole('button', { name: '卸载', exact: true }).click();
        await page.getByRole('dialog').getByRole('button', { name: '卸载', exact: true }).click();
        await card.getByText('未安装', { exact: true }).waitFor();
        await card.getByRole('button', { name: '安装', exact: true }).waitFor();
      }
    } finally { await context.close(); }
  }
});

test('install an independent LoopX package, configure and inspect shared work in all layouts', async () => {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await context.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message));
  await openUi(page, daemon); await page.goto(daemon.base + '/#/extensions');
  await page.getByRole('button', { name: '安装外部插件', exact: true }).click();
  await page.getByLabel('安装来源').fill(join(repoRoot, 'ext-loopx'));
  await page.getByRole('button', { name: '检查安装包', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '安装', exact: true }).click();
  const card = page.locator('.ext-card').filter({ has: page.getByRole('heading', { name: 'LoopX', exact: true }) });
  await card.getByRole('button', { name: '配置关联' }).click();
  await page.getByLabel('LoopX 可执行文件').fill(join(repoRoot, 'daemon/test/fixtures/fake-loopx.mjs'));
  await page.getByRole('button', { name: '保存并检测' }).click();
  await page.getByRole('status').getByText('可用 1.4.2', { exact: false }).waitFor();
  await page.getByRole('button', { name: '关联目标', exact: true }).click();
  await page.getByLabel('外部目标 ID', { exact: true }).fill('sample-goal');
  await page.getByLabel('Registry 文件路径').fill(join(daemon.workdir, 'registry.json'));
  await page.getByRole('button', { name: '已选 0 个线程' }).click();
  await page.getByRole('button', { name: 'Plugin validation', exact: true }).click();
  await page.keyboard.press('Escape');
  // The popover owns Escape; the settings dialog must remain usable.
  if (!await page.getByRole('dialog').count()) {
    await card.getByRole('button', { name: '配置关联' }).click();
    throw new Error('Escape dismissed settings while selecting threads');
  }
  await page.getByLabel('允许「看板」读取此目标内容').check();
  await shot(page, 'loopx-settings-desktop');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  const states = await (await api(daemon, '/api/extensions')).json();
  const board = states.find(s => s.manifest.id === 'board');
  assert.ok(board);
  await api(daemon, '/api/extensions/board/install', { method: 'POST', body: JSON.stringify({ granted: board.manifest.permissions }) });
  await context.close();
  for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, colorScheme: theme, isMobile: mobile, hasTouch: mobile });
    try {
      const p = await ctx.newPage(); p.on('pageerror', e => errors.push(e.message));
      await openUi(p, daemon); await p.goto(daemon.base + '/#/board');
      if (mobile) await p.getByRole('tab', { name: /待验收/ }).click();
      await p.locator('.work-summary-row').waitFor({ timeout: 8000 }).catch(async error => { await shot(p, 'loopx-failure'); throw error; });
      await shot(p, `loopx-board-${mobile ? 'phone' : 'desktop'}-${theme}`);
      assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await p.locator('.work-summary-row').click();
      await p.getByRole('dialog').getByText('部分内容：来源未提供完整待办列表。').waitFor();
      await shot(p, `loopx-detail-${mobile ? 'phone' : 'desktop'}-${theme}`);
      await p.getByRole('button', { name: '关闭', exact: true }).click();
      await p.locator('.ext-board-card').filter({ hasText: 'Plugin validation' }).click();
      await p.locator('.composer-slot .work-summary-row').waitFor();
      await shot(p, `loopx-thread-${mobile ? 'phone' : 'desktop'}-${theme}`);
      await p.goto(daemon.base + '/#/extensions');
      await p.locator('.ext-card').filter({ has: p.getByRole('heading', { name: 'LoopX', exact: true }) }).getByRole('button', { name: '配置关联' }).click();
      await p.getByLabel('外部目标 ID', { exact: true }).waitFor();
      await shot(p, `loopx-settings-${mobile ? 'phone' : 'desktop'}-${theme}`);
      assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    } finally { await ctx.close(); }
  }
  assert.deepEqual(errors, []);
});

test('disabling and uninstalling remove current content access without changing threads', async () => {
  await api(daemon, '/api/extensions/loopx', { method: 'PATCH', body: JSON.stringify({ enabled: false }) });
  const work = await (await api(daemon, '/api/work-sources?consumer=board')).json();
  assert.equal(work[0].freshness, 'unavailable');
  await api(daemon, '/api/extensions/loopx', { method: 'DELETE' });
  assert.deepEqual(await (await api(daemon, '/api/work-sources?consumer=board')).json(), []);
  assert.equal((await api(daemon, `/api/threads/${thread.id}`)).status, 200);
});
