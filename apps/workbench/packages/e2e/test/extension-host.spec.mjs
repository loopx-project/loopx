import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { linkDaemon, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let root, hub, remote, browser, machine, localThread, remoteThread;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
const json = async (daemon, path, method = 'GET', body) => {
  const response = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  assert.ok(response.ok, `${path}: ${response.status} ${response.ok ? '' : await response.text()}`);
  return response.status === 204 ? null : response.json();
};
const card = (page, title) => page.locator('.ext-board-card-wrap').filter({ has: page.locator('.ext-board-card-title', { hasText: title }) });
async function boardPage(options = {}) {
  const page = await browser.newPage({ viewport: { width: 1360, height: 860 }, ...options });
  await page.addInitScript(() => localStorage.setItem('calli.sidebar.allHosts', '1'));
  await openUi(page, hub);
  await page.goto(`${hub.base}/#/board`);
  await card(page, '远程预览线程').waitFor({ state: 'attached' });
  if (options.isMobile) {
    for (const column of ['进行中', '待验收', '等你', '已完成']) {
      await page.getByRole('tab', { name: new RegExp(`^${column}`) }).click();
      if (await card(page, '远程预览线程').isVisible()) break;
    }
  }
  await card(page, '远程预览线程').waitFor();
  return page;
}
before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-extension-host-'));
  const remoteHome = join(root, 'remote-home'); mkdirSync(remoteHome);
  remote = await startDaemon({ CALLI_SOCKET: join(remoteHome, '.local/state/calli/daemon.sock') });
  hub = await startDaemon({ FAKE_PERSONAL_BOARD_LINKS: '1' });
  console.log(`Temporary hubs: ${hub.pid}, ${remote.pid}`);
  machine = await linkDaemon(hub, remote);
  const board = (await json(hub, '/api/extensions')).find(e => e.manifest.id === 'board');
  await json(hub, '/api/extensions/board/install', 'POST', { granted: board.manifest.permissions });
  localThread = await json(hub, '/api/threads', 'POST', { agent: 'pi', cwd: hub.workdir, title: '本机预览线程' });
  remoteThread = await json(remote, '/api/threads', 'POST', { agent: 'pi', cwd: remote.workdir, title: '远程预览线程' });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await hub?.stop(); await remote?.stop(); rmSync(root, { recursive: true, force: true }); });

test('all-machine board mutations and the shared sidebar menu reach the remote machine', { timeout: 60000 }, async () => {
  const page = await boardPage();
  try {
    await card(page, '本机预览线程').waitFor();
    assert.equal(await card(page, '远程预览线程').locator('.ext-board-machine').textContent(), 'devbox');
    assert.equal((await json(remote, `/api/threads/${remoteThread.id}`)).report, null);
    await card(page, '远程预览线程').getByRole('checkbox').click();
    await page.waitForFunction(() => document.querySelector('.ext-board-card-wrap.is-done') !== null);
    assert.ok((await json(remote, `/api/threads/${remoteThread.id}`)).doneAt);
    assert.equal((await json(hub, `/api/threads/${localThread.id}`)).doneAt, null);
    await card(page, '远程预览线程').click({ button: 'right' });
    await page.getByRole('menuitem', { name: '看板状态' }).click();
    await page.getByRole('menuitemradio', { name: '进行中', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.ext-board-card-wrap.is-done'));
    assert.equal((await json(remote, `/api/threads/${remoteThread.id}`)).report.stage, 'doing');
    await page.locator('.sidebar .thread-row').filter({ hasText: '远程预览线程' }).click({ button: 'right' });
    await page.getByRole('menuitem', { name: '置顶', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.sidebar .thread-row')?.textContent?.includes('远程预览线程'));
    assert.equal((await json(remote, `/api/threads/${remoteThread.id}`)).pinned, true);
  } finally { await page.close(); }
});

test('preview reads and replies on its machine while board and topbar retain their position', { timeout: 60000 }, async () => {
  const page = await boardPage();
  try {
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const before = await page.locator('.ext-board-page').boundingBox();
    const top = await page.locator('.topbar').boundingBox();
    await page.locator('.ext-board-page').evaluate(el => { el.dataset.retained = 'yes'; });
    await card(page, '远程预览线程').locator('.ext-board-open').click();
    const panel = page.locator('.thread-peek'); await panel.waitFor();
    assert.deepEqual(await page.locator('.ext-board-page').boundingBox(), before);
    assert.deepEqual(await page.locator('.topbar').boundingBox(), top);
    await panel.locator('textarea').fill('hello from board preview');
    await panel.locator('textarea').press('Enter');
    await panel.getByText('hi there hello from board preview', { exact: true }).waitFor({ timeout: 15000 });
    assert.match(JSON.stringify(await json(remote, `/api/threads/${remoteThread.id}/events`)), /hello from board preview/);
    if (shots) await page.waitForTimeout(250);
    if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, 'preview-reply-desktop-light.png') });
    await page.keyboard.press('Escape'); await panel.waitFor({ state: 'detached' });
    assert.equal(await page.locator('.ext-board-page').getAttribute('data-retained'), 'yes');
    const after = await page.locator('.ext-board-page').boundingBox();
    assert.deepEqual({ x: after.x, y: after.y, width: after.width }, { x: before.x, y: before.y, width: before.width });
    assert.deepEqual(await page.locator('.topbar').boundingBox(), top);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('desktop and phone, light and dark: menus, previews, and project drafts', { timeout: 120000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const page = await boardPage({ viewport: mobile ? { width: 390, height: 844 } : { width: 1360, height: 860 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    const suffix = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
    try {
      const target = card(page, '远程预览线程');
      await target.click({ button: 'right' });
      await page.getByRole('menuitem', { name: '看板状态' }).waitFor();
      if (shots) await page.waitForTimeout(250);
    if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `menu-${suffix}.png`) });
      await page.keyboard.press('Escape');
      await target.locator('.ext-board-open').click();
      if (mobile) { await page.locator('.composer-slot textarea').waitFor(); assert.equal(await page.locator('.thread-peek').count(), 0); }
      else await page.locator('.thread-peek textarea').waitFor();
      if (shots) await page.waitForTimeout(250);
    if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `preview-${suffix}.png`) });
      if (!mobile) await page.locator('.thread-peek').getByRole('button', { name: '关闭', exact: true }).click();
      else {
        await page.goto(`${hub.base}/#/thread/${localThread.id}?machine=`);
        await page.locator('.topbar-title').getByText('本机预览线程', { exact: true }).waitFor();
      }
      await page.goto(`${hub.base}/#/board`);
      await page.locator('.ext-board-page').waitFor();
      await page.locator('.ext-board-page').getByRole('button', { name: '新对话', exact: true }).click();
      await page.locator('.composer-slot textarea').waitFor();
      if (shots) await page.waitForTimeout(250);
    if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `draft-${suffix}.png`) });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    } finally { await page.close(); }
  }
});

test('project and machine filters prefill a new draft on the target machine', { timeout: 60000 }, async () => {
  const page = await boardPage();
  try {
    await card(page, '远程预览线程').locator('.ext-board-project').click();
    await page.locator('.ext-board-filters').getByRole('button', { name: '机器', exact: true }).click();
    await page.getByRole('menuitemcheckbox', { name: /^devbox/ }).click();
    await page.keyboard.press('Escape');
    await page.locator('.ext-board-page').getByRole('button', { name: '新对话', exact: true }).click();
    await page.locator('.composer-slot textarea').waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('calli.host')), machine.id);
    const creation = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/threads'));
    await page.locator('.composer-slot textarea').fill('project draft on remote');
    await page.locator('.composer-slot textarea').press('Enter');
    const createdResponse = await creation;
    assert.ok(createdResponse.ok());
    assert.equal(createdResponse.request().postDataJSON().cwd, remoteThread.cwdReal);
    const created = await createdResponse.json();
    const there = await json(remote, `/api/threads/${created.id}`);
    assert.equal(there.cwdReal, remoteThread.cwdReal);
    await page.locator('.bubble').getByText('project draft on remote', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal((await json(hub, '/api/threads')).some(t => t.preview?.includes('project draft on remote')), false);
  } finally { await page.close(); }
});

test('assistant consent can be declined, is saved when accepted, and reply links open the remote thread', { timeout: 60000 }, async () => {
  await json(hub, '/api/personal/settings', 'PUT', { agent: 'pi' });
  const page = await browser.newPage({ viewport: { width: 1360, height: 860 } });
  let granted = false, sends = 0;
  try {
    await page.addInitScript(() => localStorage.setItem('calli.sidebar.allHosts', '1'));
    // The daemon upgrade is covered by the HTTP unit test. Here reproduce its old-grant catalog.
    await page.route('**/api/extensions', async route => {
      const response = await route.fetch();
      const states = await response.json();
      await route.fulfill({ response, json: states.map(state => state.manifest.id === 'board' && !granted
        ? { ...state, granted: state.granted.filter(p => p !== 'assistant.ask') } : state) });
    });
    await page.route('**/api/extensions/board/install', async route => { granted = true; await route.continue(); });
    await page.route('**/api/personal/say', async route => { sends++; await route.continue(); });
    await openUi(page, hub); await page.goto(`${hub.base}/#/board`);
    await card(page, '远程预览线程').waitFor();
    await page.getByRole('button', { name: '让助手看看', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '交给个人助手' }); await dialog.waitFor();
    await dialog.getByText('查看将发送的摘要').click();
    assert.match(await dialog.innerText(), /远程预览线程/);
    if (shots) await page.waitForTimeout(250);
    if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, 'assistant-consent-desktop-light.png') });
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(sends, 0); assert.equal(granted, false);
    await page.getByRole('button', { name: '让助手看看', exact: true }).click();
    await dialog.getByRole('button', { name: '交给助手', exact: true }).click();
    await page.locator('.composer-slot textarea').waitFor();
    assert.equal(sends, 1); assert.equal(granted, true);
    const link = page.getByRole('link', { name: '远程预览线程', exact: true }).first();
    await link.waitFor({ timeout: 15000 });
    assert.match(await link.getAttribute('href'), new RegExp(`^#/thread/${remoteThread.id}\\?machine=`));
    await link.click();
    await page.locator('.topbar-title').getByText('远程预览线程', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('calli.host')), machine.id);
  } finally { await page.close(); }
});

test('the preview shows pending approvals and submits the decision on the remote machine', { timeout: 60000 }, async () => {
  const approval = await json(remote, '/api/threads', 'POST', { agent: 'claude', cwd: remote.workdir, title: '远程审批线程', permissionMode: 'ask' });
  await json(remote, `/api/threads/${approval.id}/turns`, 'POST', { text: 'please approve' });
  const page = await boardPage();
  try {
    await card(page, '远程审批线程').locator('.ext-board-open').click();
    const panel = page.locator('.thread-peek');
    await panel.getByRole('button', { name: '允许这一次', exact: true }).waitFor();
    for (const colorScheme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme });
      await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, colorScheme);
      if (shots) await page.waitForTimeout(250);
    if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `approval-desktop-${colorScheme}.png`) });
    }
    await panel.getByRole('button', { name: '允许这一次', exact: true }).click();
    await panel.getByText('approved:allow', { exact: true }).waitFor({ timeout: 15000 });
    await panel.locator('.action-sheet').waitFor({ state: 'detached' });
    const events = await json(remote, `/api/threads/${approval.id}/events`);
    assert.match(JSON.stringify(events), /approval.resolved/);
    await panel.locator('textarea').fill('shared preview draft');
    await panel.getByRole('button', { name: '打开完整页面', exact: true }).click();
    assert.equal(await page.locator('.composer-slot textarea').inputValue(), 'shared preview draft');
  } finally { await page.close(); }
});

test('a thread link naming a removed machine reports it without opening the local thread', { timeout: 30000 }, async () => {
  const page = await browser.newPage();
  try {
    await openUi(page, hub);
    await page.goto(`${hub.base}/#/thread/${localThread.id}?machine=removed-machine`);
    await page.getByText('找不到这台机器', { exact: true }).waitFor();
    assert.equal(await page.locator('.topbar-title').getByText('本机预览线程', { exact: true }).count(), 0);
  } finally { await page.close(); }
});
