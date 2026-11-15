import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let hub, browser, dir;
const shots = join(repoRoot, 'e2e', 'shots', 'handover-mode2');
const request = async (path, method = 'GET', body) => {
  const response = await api(hub, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await response.json(); assert.ok(response.ok, JSON.stringify(value)); return value;
};
const wait = async check => {
  for (let i = 0; i < 200; i++) { const result = await check(); if (result) return result; await new Promise(r => setTimeout(r, 50)); }
  throw new Error('condition timed out');
};
const idle = id => wait(async () => (await request(`/api/threads/${id}`)).status === 'idle');
const send = (id, text) => request(`/api/threads/${id}/turns`, 'POST', { text });
const create = title => request('/api/threads', 'POST', { agent: 'first', cwd: hub.workdir, title });
const sidebar = async (page, mobile) => {
  if (mobile && !await page.locator('.app.drawer-open').count()) await page.locator('.menu-btn').click();
};
const select = async (page, title, mobile) => {
  await sidebar(page, mobile);
  await page.locator('.sidebar .thread-item').filter({ has: page.locator('.thread-title', { hasText: new RegExp(`^${title}$`) }) }).click();
  if (mobile) await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden', null, { timeout: 5000 });
};
const dialog = async page => {
  await page.getByLabel('这个线程的更多操作').click();
  await page.getByRole('menuitem', { name: '交接', exact: true }).click();
  const value = page.getByRole('dialog', { name: '交接', exact: true });
  await value.locator('.handover-summary').waitFor(); return value;
};
const handover = async (page, modal) => {
  const [response] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/handover-thread') && r.request().method() === 'POST'), modal.getByRole('button', { name: '新建接手对话', exact: true }).click()]);
  assert.equal(response.status(), 201);
  const next = await response.json(); await modal.waitFor({ state: 'detached' });
  await page.locator('.topbar-title').filter({ hasText: next.title }).waitFor(); return next;
};
const uiSend = async (page, id, text) => {
  await page.locator('.composer textarea').fill(text);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await wait(async () => (await request(`/api/threads/${id}/events`)).some(e => e.type === 'turn.started' && e.data.text === text));
  await idle(id);
};
const chainDialog = async page => {
  await page.getByLabel('这个线程的更多操作').click();
  await page.getByRole('menuitem', { name: '交接记录', exact: true }).click();
  await page.getByRole('dialog', { name: '交接记录', exact: true }).waitFor();
};
const origin = async page => {
  await chainDialog(page);
  await page.getByRole('menuitem').filter({ hasText: '原始会话' }).click();
};
before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'calli-mode-e2e-')); mkdirSync(shots, { recursive: true });
  hub = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: ['first', 'next'].map((id, i) => ({ id, adapter: 'claude', label: `Agent ${i + 1}`, command: ['fake-claude'], env: { CLAUDE_CONFIG_DIR: join(dir, id), FAKE_SESSION_ROOT: join(dir, id) } })) }) });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await hub?.stop(); rmSync(dir, { recursive: true, force: true }); });

for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
  test(`manual conversations remain independent in grouped cards, automatic handovers merge: ${mobile ? 'phone' : 'desktop'} ${theme}`, async () => {
    const name = `${mobile ? 'phone' : 'desktop'}-${theme}`;
    const source = await create(`并行任务 ${name}`); await send(source.id, 'slow original task');
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    try {
      await openUi(page, hub); await select(page, source.title, mobile);
      const modal = await dialog(page);
      assert.equal(await modal.locator('.handover-mode-choice').count(), 0);
      await page.screenshot({ path: join(shots, `running-dialog-${name}.png`), animations: 'disabled' });
      const branch = await handover(page, modal);
      assert.equal(branch.handover.mode, 'branch');
      assert.equal((await request(`/api/threads/${source.id}`)).status, 'running');
      assert.equal(await page.locator('.handover-readonly, .merged-history').count(), 0);
      await sidebar(page, mobile);
      const card = page.locator('.handover-group').filter({ has: page.locator('.thread-title', { hasText: source.title }) });
      assert.equal(await card.count(), 1);
      assert.equal(await card.locator('.thread-item').count(), 1);
      assert.equal(await card.locator('.agent-stack svg').count(), 2);
      assert.equal(await page.locator('.sidebar .thread-fork').count(), 0);
      await card.locator('.handover-toggle').click();
      assert.equal(await card.locator('.handover-member').count(), 2);
      await page.screenshot({ path: join(shots, `manual-sidebar-${name}.png`), animations: 'disabled' });
      await select(page, source.title, mobile);
      await uiSend(page, branch.id, '独立处理新任务');
      await request(`/api/threads/${source.id}/cancel`, 'POST'); await idle(source.id);
      await origin(page);
      assert.equal(await page.locator('.handover-readonly, .merged-history').count(), 0);
      await uiSend(page, source.id, '原会话继续工作');
      assert.ok(!(await request(`/api/threads/${branch.id}/events`)).some(e => e.type === 'turn.started' && e.data.text === '原会话继续工作'));
      await chainDialog(page);
      await page.getByRole('menuitem').filter({ hasText: '接手会话' }).click();
      await uiSend(page, branch.id, '新会话再次独立工作');
      await chainDialog(page);
      await page.getByRole('menuitem').filter({ hasText: '手动交接' }).waitFor();
      assert.equal(await page.locator('.handover-mode').count(), 0);
      await page.screenshot({ path: join(shots, `manual-history-${name}.png`), animations: 'disabled' });
      await page.keyboard.press('Escape');

      const stoppedSource = await create(`已停手动任务 ${name}`); await send(stoppedSource.id, '已结束的任务'); await idle(stoppedSource.id);
      await select(page, stoppedSource.title, mobile);
      const stopped = await dialog(page);
      assert.equal(await stopped.locator('.handover-mode-choice').count(), 0);
      const stoppedBranch = await handover(page, stopped);
      assert.equal(stoppedBranch.handover.mode, 'branch');
      assert.equal(await page.locator('.handover-readonly, .merged-history').count(), 0);
      await uiSend(page, stoppedBranch.id, '手动新会话独立处理');
      await origin(page);
      await uiSend(page, stoppedSource.id, '已停原会话仍可独立发送');
      assert.ok(!(await request(`/api/threads/${stoppedBranch.id}/events`)).some(e => e.type === 'turn.started' && e.data.text === '已停原会话仍可独立发送'));
      await page.screenshot({ path: join(shots, `idle-manual-${name}.png`), animations: 'disabled' });

      const relay = await create(`自动接力任务 ${name}`); await send(relay.id, '准备自动接力的任务'); await idle(relay.id);
      // The automatic scheduler and exhausted-card entry point both explicitly request continue.
      const continued = await request(`/api/threads/${relay.id}/handover-thread`, 'POST', { agent: 'next', mode: 'continue' });
      await select(page, relay.title, mobile);
      await page.locator('.merged-history').waitFor();
      await uiSend(page, continued.id, '接手继续处理');
      await origin(page);
      await page.locator('.handover-readonly').waitFor();
      await uiSend(page, continued.id, '从前段输入也发给接手方');
      await origin(page);
      await page.locator('.handover-readonly').waitFor();
      await chainDialog(page);
      await page.locator('.handover-entry small').filter({ hasText: /^自动接力 ·/ }).waitFor();
      await page.keyboard.press('Escape');
      await page.screenshot({ path: join(shots, `continue-source-${name}.png`), animations: 'disabled' });
      const detachButton = page.locator('.handover-readonly').getByRole('button', { name: '取消这次接力', exact: true });
      await detachButton.waitFor();
      await detachButton.click();
      const detachDialog = page.getByRole('dialog', { name: '取消自动接力？', exact: true });
      await detachDialog.waitFor();
      await detachDialog.getByRole('button', { name: '取消接力', exact: true }).click();
      await detachDialog.waitFor({ state: 'detached' });
      await page.locator('.handover-readonly').waitFor({ state: 'detached' });
      await page.locator('.merged-history').waitFor({ state: 'detached' });
      assert.equal((await request(`/api/threads/${relay.id}`)).autoHandover.override, false);
      assert.equal((await request(`/api/threads/${continued.id}`)).autoHandover.override, null);
      await uiSend(page, relay.id, '取消接力后发回原会话');
      assert.ok(!(await request(`/api/threads/${continued.id}/events`)).some(e => e.type === 'turn.started' && e.data.text === '取消接力后发回原会话'));
      await chainDialog(page);
      await page.getByRole('menuitem').filter({ hasText: '接手会话' }).click();
      await uiSend(page, continued.id, '取消接力后接手会话也能发送');
      assert.ok(!(await request(`/api/threads/${relay.id}/events`)).some(e => e.type === 'turn.started' && e.data.text === '取消接力后接手会话也能发送'));
      await chainDialog(page);
      await page.locator('.handover-entry small').filter({ hasText: /^已取消接力 ·/ }).waitFor();
      await page.keyboard.press('Escape');
      await sidebar(page, mobile);
      const detachedRows = page.locator('.sidebar .thread-title').filter({ hasText: relay.title });
      assert.equal(await detachedRows.count(), 2, 'the source and successor become separate sidebar cards');
      await page.screenshot({ path: join(shots, `detached-${name}.png`), animations: 'disabled' });

      // External/source-side activity still separates a continuation when the user did not detach it.
      const resumedRelay = await create(`自动接力后复用原会话 ${name}`); await send(resumedRelay.id, '准备接力'); await idle(resumedRelay.id);
      const resumedNext = await request(`/api/threads/${resumedRelay.id}/handover-thread`, 'POST', { agent: 'next', mode: 'continue' });
      await select(page, resumedRelay.title, mobile);
      await send(resumedRelay.id, '原会话重新开启一轮'); await idle(resumedRelay.id);
      await wait(async () => (await request(`/api/threads/${resumedNext.id}`)).handover.mode === 'branch');
      await page.locator('.handover-readonly').waitFor({ state: 'detached' });
      await page.locator('.merged-history').waitFor({ state: 'detached' });
      await origin(page);
      await uiSend(page, resumedRelay.id, '解除接力后发回当前会话');
      assert.ok(!(await request(`/api/threads/${resumedNext.id}/events`)).some(e => e.type === 'turn.started' && e.data.text === '解除接力后发回当前会话'));
      await page.screenshot({ path: join(shots, `resumed-source-${name}.png`), animations: 'disabled' });
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}
