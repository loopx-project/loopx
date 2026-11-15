import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { linkDaemon, startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let root, remoteHome, remote, hub, browser;
before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-e2e-projects-'));
  remoteHome = join(root, 'remote-home');
  const localHome = join(root, 'local-home');
  mkdirSync(remoteHome);
  mkdirSync(localHome);
  mkdirSync(join(localHome, 'local-only'));
  const socket = join(remoteHome, '.local/state/calli/daemon.sock');
  remote = await startDaemon({
    HOME: remoteHome, CALLI_SOCKET: socket, FAKE_CODEX_RPC_LOG: join(root, 'codex-rpc.jsonl'),
    CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')] }] }),
  });
  hub = await startDaemon({ HOME: localHome });
  await linkDaemon(hub, remote);
  browser = await launchBrowser();
});
after(async () => {
  await browser?.close();
  await hub?.stop();
  await remote?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});

async function openPicker(page) {
  await page.locator('.composer').getByTitle(remote.workdir, { exact: true }).click();
  await page.getByRole('menuitem', { name: '打开其他目录…' }).click();
  return page.getByRole('dialog', { name: '在哪个目录里工作？' });
}
/** The one input: an address is submitted with Enter, a filter word just typed. */
async function gotoPath(dialog, value) {
  await dialog.locator('.project-browser-input').fill(value);
  await dialog.locator('.project-browser-input').press('Enter');
}
async function typeFilter(dialog, value) {
  await dialog.locator('.project-browser-input').fill(value);
}
async function newPage(mobile = false, colorScheme = 'light') {
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
  await context.addInitScript(() => localStorage.setItem('calli.host', 'devbox'));
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  await openUi(page, hub);
  await page.locator('.topbar-sub', { hasText: 'devbox' }).waitFor();
  return page;
}

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  const tag = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
  test(`browse remote home, cancel, add and run Codex in a project: ${tag}`, async () => {
    const name = `项目-${tag}`;
    const project = join(remoteHome, name);
    mkdirSync(project);
    const deep = join(project, 'a/b/c/d/e/f');
    mkdirSync(deep, { recursive: true });
    const localBefore = await (await api(hub, '/api/config')).json();
    const remoteBefore = await (await api(remote, '/api/config')).json();
    const page = await newPage(mobile, colorScheme);
    try {
      let dialog = await openPicker(page);
      await gotoPath(dialog, '~');
      await dialog.getByRole('button', { name, exact: true }).waitFor();
      assert.equal(await dialog.getByRole('button', { name: 'local-only', exact: true }).count(), 0);
      // The home folder itself is no project: it is shown to walk from, not to work in.
      assert.ok(await dialog.getByRole('button', { name: '添加并使用', exact: true }).isDisabled());
      await dialog.getByRole('button', { name: '取消', exact: true }).click();
      assert.deepEqual(await (await api(remote, '/api/config')).json(), remoteBefore, 'browsing and cancelling adds nothing');

      dialog = await openPicker(page);
      await gotoPath(dialog, '~');
      await typeFilter(dialog, '不存在的文件夹');
      await dialog.getByText('没有匹配的文件夹').waitFor();
      assert.equal(await dialog.locator('.project-browser-folder').count(), 0);
      await typeFilter(dialog, name);
      await dialog.getByRole('button', { name, exact: true }).waitFor();
      // Line the tree up with what was typed, then walk it: a folder's name selects and opens it.
      await typeFilter(dialog, '');
      await dialog.getByRole('button', { name, exact: true }).click();
      for (const folder of ['a', 'b', 'c', 'd', 'e', 'f']) {
        await dialog.getByRole('button', { name: folder, exact: true }).click();
      }
      await dialog.getByRole('button', { name: '添加并使用', exact: true }).waitFor();
      await dialog.locator('.project-browser-where').evaluate((el, deep) => el.textContent === deep || Promise.reject(new Error(`foot shows ${el.textContent}`)), deep);
      // The walk reached the deepest folder; choosing the project itself is one more tap on its name.
      await dialog.getByRole('button', { name, exact: true }).click();
      await dialog.locator('.project-browser-where').evaluate((el, project) => el.textContent === project || Promise.reject(new Error(`foot shows ${el.textContent}`)), project);
      await dialog.getByRole('button', { name: '添加并使用', exact: true }).waitFor();
      // The phone sheet slides in from below; measure once that entry animation has settled.
      await dialog.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
      const bounds = await dialog.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1 && bounds.y + bounds.height <= page.viewportSize().height + 1);
      if (process.env.CALLI_E2E_SHOTS_DIR) {
        mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `projects-${tag}.png`) });
      }
      await dialog.getByRole('button', { name: '添加并使用', exact: true }).click();
      await dialog.waitFor({ state: 'detached' });
      assert.ok((await (await api(remote, '/api/config')).json()).workdirs.includes(project));
      assert.ok(JSON.parse(readFileSync(join(remote.dataDir, 'workdirs.json'), 'utf8')).includes(project));
      assert.deepEqual(await (await api(hub, '/api/config')).json(), localBefore);
      await page.locator('.composer textarea').fill(`hello ${tag}`);
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.topbar-title')?.textContent !== '新线程');
      const threads = await (await api(remote, '/api/threads')).json();
      const created = threads.find((t) => t.cwd === project);
      assert.ok(created);
      assert.equal(created.agent, 'codex');
      for (let i = 0; i < 100; i++) {
        const current = await (await api(remote, `/api/threads/${created.id}`)).json();
        if (current.agentSessionId && current.status === 'idle') break;
        await new Promise((r) => setTimeout(r, 50));
      }
      const current = await (await api(remote, `/api/threads/${created.id}`)).json();
      assert.ok(current.agentSessionId, 'remote Codex session started');
      assert.equal(current.status, 'idle');
      const calls = readFileSync(join(root, 'codex-rpc.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line));
      assert.ok(calls.some((call) => call.method === 'thread/start' && call.params.cwd === project), 'project cwd reached the remote Codex process');
      assert.deepEqual(await (await api(hub, '/api/threads')).json(), []);
    } finally {
      await page.context().close();
    }
  });
}

test('a late home response cannot overwrite the configured roots view', async () => {
  const page = await newPage();
  try {
    let release;
    const held = new Promise((r) => { release = r; });
    // Every browse carries the path as a query, so the pattern must reach past '?' to hold them.
    await page.route('**/project-directories*', async (route) => { await held; await route.continue(); });
    const dialog = await openPicker(page);
    const jumped = page.waitForRequest((req) => req.url().includes('/project-directories') && req.url().includes('path=~'));
    await gotoPath(dialog, '~');
    await jumped;
    await dialog.getByRole('button', { name: '可用目录', exact: true }).click();
    release();
    await dialog.getByText('已添加的项目位置').waitFor();
    assert.ok(await dialog.getByRole('button', { name: '用这个目录', exact: true }).isDisabled());
    // The home branch answered late; it must not push itself back into the tree.
    await page.waitForTimeout(400);
    assert.equal(await dialog.getByRole('button', { name: 'home', exact: true }).count(), 0);
    assert.equal(await dialog.getByRole('button', { name: '添加并使用', exact: true }).count(), 0);
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
  } finally { await page.context().close(); }
});

test('older remote daemons keep configured browsing and explain the home upgrade', async () => {
  const page = await newPage();
  try {
    await page.route('**/project-directories*', (route) => route.fulfill({ status: 404, json: { error: 'not found' } }));
    const dialog = await openPicker(page);
    await page.waitForFunction(() => !document.querySelector('.project-browser .primary')?.disabled);
    await gotoPath(dialog, '~');
    await dialog.getByRole('alert').getByText(/尚不支持浏览家目录/).waitFor();
    await dialog.getByRole('button', { name: '手动添加目录…' }).click();
    await page.getByRole('dialog', { name: '可用目录', exact: true }).waitFor();
  } finally { await page.context().close(); }
});

test('manual add is the picker\'s own sub-dialog: closing it comes back to the picker', async () => {
  const page = await newPage();
  try {
    const dialog = await openPicker(page);
    await dialog.getByRole('button', { name: '手动添加目录…' }).click();
    const roots = page.getByRole('dialog', { name: '可用目录', exact: true });
    await roots.waitFor();
    await roots.getByRole('button', { name: '关闭', exact: true }).click();
    await roots.waitFor({ state: 'detached' });
    // The picker it was opened from is standing there again, not gone; its added locations remain
    // one step away, the way they were before the detour.
    await page.getByRole('dialog', { name: '在哪个目录里工作？' }).getByText('已添加的项目位置').waitFor();
  } finally { await page.context().close(); }
});

test('a failed add keeps the draft unchanged and can be retried; long lists stay inside the dialog', async () => {
  const project = join(remoteHome, '待添加项目');
  mkdirSync(project);
  for (let i = 0; i < 45; i++) mkdirSync(join(project, `folder-${String(i).padStart(2, '0')}`));
  const beforeConfig = await (await api(remote, '/api/config')).json();
  const page = await newPage(true);
  try {
    const dialog = await openPicker(page);
    await gotoPath(dialog, '~');
    await dialog.getByRole('button', { name: '待添加项目', exact: true }).click();
    await dialog.getByRole('button', { name: 'folder-44', exact: true }).scrollIntoViewIfNeeded();
    assert.ok(await dialog.getByRole('button', { name: '添加并使用', exact: true }).isVisible());
    assert.ok(await dialog.locator('.project-browser-list').evaluate((el) => el.scrollHeight > el.clientHeight));
    await page.route('**/proxy/workdirs', (route) => route.fulfill({ status: 400, json: { error: 'no such directory' } }));
    await dialog.getByRole('button', { name: '添加并使用', exact: true }).click();
    await dialog.getByRole('alert').getByText(/找不到这个目录/).waitFor();
    assert.deepEqual(await (await api(remote, '/api/config')).json(), beforeConfig);
    assert.ok(await dialog.getByRole('button', { name: '添加并使用', exact: true }).isEnabled());
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(await page.locator('.composer').getByTitle(remote.workdir, { exact: true }).count(), 1);
  } finally { await page.context().close(); }
});
