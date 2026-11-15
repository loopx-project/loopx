import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

test('general startup preference is shared, respects recent activity and explicit navigation', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const save = (startupPage, autoArchiveDays = null) => api(daemon, '/api/general-settings', { method: 'PUT', body: JSON.stringify({ startupPage, autoArchiveDays }) });
  const shots = '/tmp/calli-general-settings-shots'; mkdirSync(shots, { recursive: true });
  try {
    const create = async title => (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) })).json();
    const older = await create('Older pinned conversation');
    const latest = await create('Latest conversation');
    await api(daemon, `/api/threads/${older.id}`, { method: 'PATCH', body: JSON.stringify({ pinned: true }) });
    const archived = await create('Archived latest conversation');
    await api(daemon, `/api/threads/${archived.id}`, { method: 'PATCH', body: JSON.stringify({ archived: true }) });
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      await save('new-thread');
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, colorScheme: theme });
      const page = await context.newPage();
      await openUi(page, daemon);
      await page.locator('.topbar-title', { hasText: '新线程' }).waitFor();
      if (mobile) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
      const menu = page.locator('.settings-nav');
      assert.equal(await menu.getByRole('button').first().innerText(), '常规');
      await menu.getByRole('button', { name: '常规', exact: true }).click();
      const dialog = page.getByRole('region', { name: '常规', exact: true });
      const select = dialog.getByRole('button', { name: '默认打开页面', exact: true });
      await select.waitFor();
      assert.match(await select.innerText(), /新会话页面/);
      assert.equal(await dialog.getByRole('button', { name: '保存', exact: true }).count(), 0);
      assert.equal(await dialog.getByRole('button', { name: '取消', exact: true }).count(), 0);
      const labelBox = await dialog.getByText('默认打开页面', { exact: true }).boundingBox();
      const selectBox = await select.boundingBox();
      assert.ok(selectBox.x > labelBox.x + labelBox.width, 'dropdown is on the right of the label');
      await select.click();
      assert.equal(await page.getByRole('menuitemradio', { name: '新会话页面', exact: true }).getAttribute('aria-checked'), 'true');
      const saved = page.waitForResponse(response => response.url().endsWith('/api/general-settings') && response.request().method() === 'PUT');
      await page.getByRole('menuitemradio', { name: '最近会话', exact: true }).click();
      assert.equal((await saved).status(), 200);
      await page.waitForFunction(() => !document.querySelector('.general-select:disabled'));
      const archiveSelect = dialog.getByRole('button', { name: '自动归档', exact: true });
      assert.match(await archiveSelect.innerText(), /不归档/);
      await archiveSelect.click();
      const archiveSaved = page.waitForResponse(response => response.url().endsWith('/api/general-settings') && response.request().method() === 'PUT');
      await page.getByRole('menuitemradio', { name: '7 天前', exact: true }).click();
      assert.equal((await archiveSaved).status(), 200);
      await page.waitForFunction(() => !document.querySelector('.general-select:disabled'));
      await page.screenshot({ path: join(shots, `general-${mobile ? 'mobile' : 'desktop'}-${theme}.png`), fullPage: true, animations: 'disabled' });
      await select.click();
      await page.screenshot({ path: join(shots, `general-menu-${mobile ? 'mobile' : 'desktop'}-${theme}.png`), fullPage: true, animations: 'disabled' });
      await page.keyboard.press('Escape');
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      if (await page.locator('.settings-page').count()) await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      await dialog.waitFor({ state: 'detached' });
      assert.deepEqual(await (await api(daemon, '/api/general-settings')).json(), { startupPage: 'recent-thread', autoArchiveDays: 7 });
      await page.reload();
      await page.locator('.topbar-title', { hasText: 'Latest conversation' }).waitFor();
      // An explicit new-thread action is not undone by subsequent list updates.
      if (mobile) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.sidebar').getByRole('button', { name: '新线程', exact: true }).click();
      await page.locator('.topbar-title', { hasText: '新线程' }).waitFor();
      await page.goto(`${daemon.base}/#/thread/${older.id}`);
      await page.locator('.topbar-title', { hasText: 'Older pinned conversation' }).waitFor();
      await page.goto(`${daemon.base}/#/todos`);
      await page.waitForFunction(() => location.hash === '#/todos' && !document.querySelector('.thread-row.active'));
      await context.close();
    }
    // A different device reads the preference from the daemon, with no browser storage to copy.
    const other = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await other.newPage();
    await openUi(page, daemon);
    await page.locator('.topbar-title', { hasText: 'Latest conversation' }).waitFor();
    // A failed automatic save keeps the previous server value and lets the user retry.
    await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
    await page.locator('.settings-nav').getByRole('button', { name: '常规', exact: true }).click();
    const dialog = page.getByRole('region', { name: '常规', exact: true });
    await page.route('**/api/general-settings', route => route.request().method() === 'PUT'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Could not save settings' }) }) : route.continue());
    await dialog.getByRole('button', { name: '默认打开页面', exact: true }).click();
    await page.getByRole('menuitemradio', { name: '新会话页面', exact: true }).click();
    await dialog.getByRole('alert').waitFor();
    assert.match(await dialog.getByRole('button', { name: '默认打开页面', exact: true }).innerText(), /最近会话/);
    assert.deepEqual(await (await api(daemon, '/api/general-settings')).json(), { startupPage: 'recent-thread', autoArchiveDays: 7 });
    await page.unroute('**/api/general-settings');
    await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      if (await page.locator('.settings-page').count()) await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
    // A slow preference response must not replace a thread the user chose in the meantime.
    let release;
    const ready = new Promise(resolve => { release = resolve; });
    await page.route('**/api/general-settings', async route => { await ready; await route.continue(); });
    await page.reload();
    await page.locator('.sidebar .thread-item', { hasText: 'Older pinned conversation' }).click();
    const settingsResponse = page.waitForResponse(response => response.url().endsWith('/api/general-settings') && response.request().method() === 'GET');
    release();
    await settingsResponse;
    await page.locator('.topbar-title', { hasText: 'Older pinned conversation' }).waitFor();
    await page.unroute('**/api/general-settings');
    await save('new-thread');
    await page.reload();
    await page.locator('.topbar-title', { hasText: '新线程' }).waitFor();
    await save('recent-thread');
    for (const thread of [older, latest]) await api(daemon, `/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ archived: true }) });
    await page.reload();
    await page.locator('.topbar-title', { hasText: '新线程' }).waitFor();
    await other.close();
  } finally { await browser.close(); await daemon.stop(); }
});
