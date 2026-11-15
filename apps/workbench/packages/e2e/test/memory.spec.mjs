import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, repoRoot, api } from '../harness.mjs';

let daemon, browser, directory;
const shots = join(tmpdir(), 'calli-memory-shots');
before(async () => {
  directory = mkdtempSync(join(tmpdir(), 'calli-memory-browser-'));
  const home = join(directory, 'primary');
  cpSync(join(repoRoot, 'daemon/test/fixtures/codex-memory'), join(home, 'memories'), { recursive: true });
  const command = [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')];
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'codex', adapter: 'codex', label: 'Codex', command, env: { CODEX_HOME: home } },
    { id: 'codex-shared', adapter: 'codex', label: 'Codex Work', command, env: { CODEX_HOME: home } },
    { id: 'codex-empty', adapter: 'codex', label: 'Codex Personal', command, env: { CODEX_HOME: join(directory, 'empty') } },
  ] }) });
  browser = await launchBrowser();
  mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); if (directory) rmSync(directory, { recursive: true, force: true }); });

const changeExtension = (enabled) => api(daemon, '/api/extensions/memory', { method: 'PATCH', body: JSON.stringify({ enabled }) });

const memoryCard = page => page.locator('.ext-card').filter({ has: page.getByRole('heading', { name: '记忆', exact: true }) });

async function openSettings(page) {
  if (page.viewportSize().width < 600) await page.locator('.menu-btn').click();
  await page.getByRole('button', { name: '配置', exact: true }).click();
}

async function openMemory(page, screenshot) {
  await openUi(page, daemon);
  assert.equal(await page.locator('.sidebar-inner button', { hasText: /^记忆$/ }).count(), 0);
  await openSettings(page);
  const open = page.getByRole('menuitem', { name: '记忆', exact: true });
  await open.waitFor();
  await open.scrollIntoViewIfNeeded();
  if (screenshot) await page.screenshot({ path: screenshot });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'settings actions fit the viewport');
  await open.click();
  await page.locator('.memory-page').waitFor();
  await page.locator('.memory-card').first().waitFor();
}

test('memory is inactive until installed, then renders read-only in both themes and sizes', async () => {
  const before = readFileSync(join(directory, 'primary/memories/MEMORY.md'), 'utf8');
  const page = await browser.newPage();
  const requests = [];
  page.on('request', request => { if (request.url().includes('/memory/')) requests.push(request.url()); });
  await openUi(page, daemon);
  assert.equal(await page.locator('.sidebar-inner button', { hasText: /^记忆$/ }).count(), 0);
  assert.equal(requests.length, 0, 'uninstalled extension does not read memory');
  await openSettings(page);
  assert.equal(await page.getByRole('menuitem', { name: '记忆', exact: true }).count(), 0);
  await page.keyboard.press('Escape');
  await page.evaluate(() => { location.hash = '#/extensions'; });
  const card = page.locator('.ext-card').filter({ has: page.getByRole('heading', { name: '记忆', exact: true }) });
  assert.equal(await card.getByRole('button', { name: '打开', exact: true }).count(), 0);
  await card.getByRole('button', { name: '安装', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText('已有记忆', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: /安装/ }).click();
  await dialog.waitFor({ state: 'detached' });
  await card.getByRole('button', { name: '打开', exact: true }).click();
  await page.locator('.memory-card').first().waitFor();
  assert.equal(await page.locator('.memory-card').count(), 3);
  assert.ok(await page.getByText('共用记忆', { exact: true }).isVisible());
  await page.close();

  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const external = [], errors = [];
      page.on('request', request => { if (request.url().includes('example.invalid')) external.push(request.url()); });
      page.on('pageerror', error => errors.push(error.message));
      const prefix = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
      await openMemory(page, join(shots, `${prefix}-entry.png`));
      await page.screenshot({ path: join(shots, `${prefix}-list.png`) });
      await page.locator('.memory-card').filter({ hasText: '工作方式与项目线索' }).click();
      await page.locator('.memory-document .md').waitFor();
      await page.screenshot({ path: join(shots, `${prefix}-detail.png`) });
      assert.ok(await page.locator('.memory-document').getByText('协作偏好', { exact: true }).isVisible());
      await page.getByRole('button', { name: '原文', exact: true }).click();
      assert.ok((await page.locator('.memory-original').textContent()).includes('# 工作方式'));
      if (mobile) await page.getByRole('button', { name: '返回列表', exact: true }).click();
      await page.getByRole('searchbox').fill('no-such-document');
      await page.getByText('没有匹配的文档，试试其他关键词。').waitFor();
      await page.getByRole('searchbox').fill('');
      await page.locator('.memory-card').filter({ has: page.locator('.memory-file-path', { hasText: /^rollout_summaries\/workbench\.md$/ }) }).click();
      await page.locator('.memory-document .md').waitFor();
      assert.equal(await page.locator('.memory-document img, .memory-document script, .memory-document a').count(), 0);
      assert.equal(external.length, 0);
      assert.equal(await page.evaluate(() => window.memoryUnsafe), undefined);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no page overflow');
      if (mobile) await page.getByRole('button', { name: '返回列表', exact: true }).click();
      await page.locator('.memory-source-picker').click();
      await page.getByRole('menuitemradio', { name: 'Codex Personal', exact: true }).click();
      await page.getByText('尚未发现记忆目录。此来源可能未启用记忆，或还没有生成记录。').waitFor();
      assert.equal(await page.locator('.memory-card').count(), 0);
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  }
  assert.equal(readFileSync(join(directory, 'primary/memories/MEMORY.md'), 'utf8'), before);
});

test('refresh observes outside changes; disabling and uninstalling remove the view and deny reads', async () => {
  const page = await browser.newPage();
  try {
    await openMemory(page);
    const root = join(directory, 'primary/memories');
    writeFileSync(join(root, 'memory_summary.md'), '# 外部更新的新摘要\n\n已经刷新。');
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await page.locator('.memory-card').filter({ hasText: '外部更新的新摘要' }).waitFor();
    await page.locator('.memory-document').getByText('已经刷新。', { exact: true }).waitFor();
    await changeExtension(false);
    await page.locator('.memory-page').waitFor({ state: 'detached' });
    await openSettings(page);
    assert.equal(await page.getByRole('menuitem', { name: '记忆', exact: true }).count(), 0);
    await page.keyboard.press('Escape');
    assert.equal((await api(daemon, '/api/extensions/memory/memory/sources')).status, 403);
    await page.evaluate(() => { location.hash = '#/extensions'; });
    await memoryCard(page).getByRole('button', { name: '启用', exact: true }).waitFor();
    assert.equal(await memoryCard(page).getByRole('button', { name: '打开', exact: true }).count(), 0);
    await changeExtension(true);
    await openMemory(page);
    await api(daemon, '/api/extensions/memory', { method: 'DELETE' });
    await page.locator('.memory-page').waitFor({ state: 'detached' });
    await openSettings(page);
    assert.equal(await page.getByRole('menuitem', { name: '记忆', exact: true }).count(), 0);
    assert.equal((await api(daemon, '/api/extensions/memory/memory/sources')).status, 403);
  } finally { await page.close(); }
});
