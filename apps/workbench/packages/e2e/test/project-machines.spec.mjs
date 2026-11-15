import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { linkDaemon, startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let root, hub, remote, browser, localCode, remoteCode;
before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-project-machines-'));
  const remoteHome = join(root, 'remote-home');
  mkdirSync(remoteHome);
  localCode = join(root, 'local', 'code');
  remoteCode = join(remoteHome, 'code');
  mkdirSync(join(root, 'local'));
  for (const [alias, name] of [[localCode, 'local-real'], [remoteCode, 'remote-real']]) {
    const real = join(root, name, 'code');
    mkdirSync(real, { recursive: true });
    symlinkSync(real, alias);
  }
  const agents = JSON.stringify({ agents: [{ id: 'pi', adapter: 'pi', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-pi.mjs')] }] });
  remote = await startDaemon({ HOME: remoteHome, CALLI_WORKDIRS: remoteCode, CALLI_SOCKET: join(remoteHome, '.local/state/calli/daemon.sock'), CALLI_AGENTS_JSON: agents });
  hub = await startDaemon({ CALLI_WORKDIRS: localCode, CALLI_AGENTS_JSON: agents });
  await linkDaemon(hub, remote);
  for (const [daemon, code, name] of [[hub, localCode, '本机项目'], [remote, remoteCode, '远端项目']]) {
    mkdirSync(join(code, name));
    for (const cwd of [code, join(code, name)]) assert.equal((await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd, title: name }) })).status, 201);
  }
  browser = await launchBrowser();
});
after(async () => {
  await browser?.close(); await hub?.stop(); await remote?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});
async function shot(page, name) {
  if (!process.env.CALLI_E2E_SHOTS_DIR) return;
  mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}.png`) });
}
async function projects(page, cwd) {
  await page.locator('.composer').getByTitle(cwd, { exact: true }).click();
  return page.getByRole('menu', { name: '在哪个项目里工作' });
}
async function machines(page) {
  await page.getByRole('button', { name: '选择机器', exact: true }).click();
  return page.getByRole('menu', { name: '工作位置' });
}
async function fits(page, locator) {
  await locator.evaluate((el) => Promise.all(el.getAnimations().map((animation) => animation.finished.catch(() => {}))));
  const box = await locator.boundingBox();
  assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= page.viewportSize().width + 1 && box.y + box.height <= page.viewportSize().height + 1);
}

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  const tag = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
  test(`machine picker and project badges isolate identical project names: ${tag}`, async () => {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage(); page.setDefaultTimeout(10_000);
      await openUi(page, hub);
      const localBefore = await (await api(hub, '/api/threads')).json();
      await page.locator('.machine-trigger', { hasText: '本机' }).waitFor();
      let menu = await projects(page, localCode);
      await menu.getByText('本机项目', { exact: true }).waitFor();
      assert.equal(await menu.locator('.project-name-text', { hasText: /^code$/ }).count(), 1, 'symlink and configured root do not duplicate');
      assert.equal(await menu.getByRole('menuitemradio', { checked: true }).count(), 1);
      assert.ok((await menu.locator('.project-machine').allInnerTexts()).every((name) => name === '本机'));
      await fits(page, menu);
      await shot(page, `projects-local-${tag}`);
      await page.keyboard.press('Escape');
      await page.locator('.composer textarea').fill('只在本机的草稿');
      await page.waitForFunction(() => localStorage.getItem('calli.drafts')?.includes('只在本机的草稿'));
      menu = await machines(page);
      assert.equal(await menu.getByRole('menuitemradio', { checked: true }).count(), 1);
      await fits(page, menu);
      await shot(page, `machines-local-${tag}`);
      await menu.getByRole('menuitemradio', { name: /devbox/ }).click();
      await page.locator('.machine-trigger', { hasText: '远程 · devbox' }).waitFor();
      assert.equal(await page.locator('.composer textarea').inputValue(), '');
      await fits(page, page.locator('.composer-context'));
      menu = await projects(page, remoteCode);
      await menu.getByText('远端项目', { exact: true }).waitFor();
      assert.equal(await menu.getByText('本机项目', { exact: true }).count(), 0);
      assert.equal(await menu.locator('.project-name-text', { hasText: /^code$/ }).count(), 1);
      assert.equal(await menu.getByRole('menuitemradio', { checked: true }).count(), 1);
      assert.ok((await menu.locator('.project-machine.remote').allInnerTexts()).every((name) => name === 'devbox'));
      await fits(page, menu);
      await shot(page, `projects-remote-${tag}`);
      await page.keyboard.press('Escape');
      menu = await machines(page);
      await fits(page, menu);
      await shot(page, `machines-remote-${tag}`);
      await menu.getByRole('menuitemradio', { name: /本机/ }).click();
      await page.locator('.machine-trigger', { hasText: '本机' }).waitFor();
      assert.equal(await page.locator('.composer textarea').inputValue(), '只在本机的草稿');
      menu = await machines(page);
      await menu.getByRole('menuitemradio', { name: /devbox/ }).click();
      await page.locator('.machine-trigger', { hasText: '远程 · devbox' }).waitFor();
      await page.locator('.composer textarea').fill(`machine selection ${tag}`);
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText(`hi there machine selection ${tag}`, { exact: true }).waitFor();
      const created = (await (await api(remote, '/api/threads')).json()).find((t) => t.preview === `machine selection ${tag}`);
      assert.equal(created?.cwd, remoteCode);
      assert.deepEqual(await (await api(hub, '/api/threads')).json(), localBefore, 'switching and sending never affects local threads');
    } finally { await context.close(); }
  });
}

test('the location picker offers machine management even before a remote is added', async () => {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.route('**/api/hosts', (route) => route.fulfill({ json: [] }));
    await openUi(page, hub);
    const menu = await machines(page);
    assert.equal(await menu.getByRole('menuitemradio').count(), 1);
    await menu.getByRole('menuitem', { name: '管理机器…' }).click();
    await page.getByRole('region', { name: '机器', exact: true }).waitFor();
  } finally { await context.close(); }
});
