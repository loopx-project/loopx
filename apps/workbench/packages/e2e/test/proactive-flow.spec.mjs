// The proactive surfaces through the real backend: the scheduler's own tick starts each run (up to
// one 30s tick per test), the fake agent does the work, and git shows what the buttons did.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, api, openUi } from '../harness.mjs';

const json = async (daemon, path, method = 'GET', body) => {
  const response = await api(daemon, path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
  assert.ok(response.ok, `${method} ${path}: ${response.status}`);
  return response.status === 204 ? null : response.json();
};
/** A time zone where it is midday now, so the default quiet hours never hold a run back. */
const middayZone = () => {
  const offset = 12 - new Date().getUTCHours();
  return offset === 0 ? 'Etc/GMT' : `Etc/GMT${offset > 0 ? '-' : '+'}${Math.abs(offset)}`;
};
const startHub = (env = {}) => startDaemon({ TZ: middayZone(), ...env });
const settled = async daemon => {
  const deadline = Date.now() + 60_000;
  for (;;) {
    const { runs } = await json(daemon, '/api/personal/proactive');
    if (runs.length && runs[0].status !== 'running') return runs[0];
    assert.ok(Date.now() < deadline, 'no proactive run settled within a minute');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
};
const openPile = async (page, daemon, id) => {
  await openUi(page, daemon);
  // The desk is the drawer's own tab on the assistant's home; its address opens it directly.
  await page.evaluate(() => { location.hash = '#/desk/desk'; });
  await page.locator('.desk-matrix').waitFor();
  await page.getByRole('button', { name: '按进度', exact: true }).click();
  await page.locator(`#desk-pile-${id}`).click();
};
/** A repository and a matter already started in it: what sandbox work needs before it may begin. */
const arrangeSandbox = async daemon => {
  const repo = join(daemon.workdir, 'project');
  mkdirSync(repo, { recursive: true });
  const git = (...args) => execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c', 'commit.gpgsign=false', ...args], { cwd: repo, encoding: 'utf8' }).trim();
  git('init', '-qb', 'main');
  writeFileSync(join(repo, 'README.md'), 'project\n');
  git('add', '.'); git('commit', '-qm', 'init');
  const todo = await json(daemon, '/api/todos', 'POST', { text: '修导出边界', lane: 'you' });
  const thread = await json(daemon, '/api/threads', 'POST', { agent: 'pi', cwd: repo });
  await json(daemon, `/api/todos/${todo.id}/threads`, 'POST', { threadId: thread.id });
  await json(daemon, '/api/personal/proactive/settings', 'PUT', { mode: 'auto', repos: [repo] });
  return { repo, git, todo };
};

test('off by default; once on, materials appear quietly on a matter due soon', async () => {
  const daemon = await startHub();
  const browser = await launchBrowser();
  try {
    assert.deepEqual((await json(daemon, '/api/personal/proactive')).settings, { mode: 'off', repos: [], enabled: false });
    const todo = await json(daemon, '/api/todos', 'POST', { text: '周五的评审会', lane: 'you', importance: 'high', dueAt: new Date(Date.now() + 864e5).toISOString() });
    await json(daemon, '/api/personal/proactive/settings', 'PUT', { mode: 'auto' });
    const run = await settled(daemon);
    assert.equal(run.status, 'ready', run.summary);
    assert.equal(run.kind, 'prepare');
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await openPile(page, daemon, todo.id);
    const card = page.locator('.proactive-card.prepared');
    await card.getByText('outline.md').waitFor();
    assert.equal(await card.getByRole('button').count(), 0);
  } finally { await browser.close(); await daemon.stop(); }
});

test('sandbox work is looked at, merged locally, and reverted from the filter', async () => {
  const daemon = await startHub();
  const browser = await launchBrowser();
  try {
    const { repo, git, todo } = await arrangeSandbox(daemon);
    const run = await settled(daemon);
    assert.equal(run.status, 'ready', run.summary);
    assert.equal(run.kind, 'sandbox');
    assert.equal(existsSync(join(repo, 'proactive-fixture.txt')), false, 'nothing reaches the repository before the merge');
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await openPile(page, daemon, todo.id);
    const card = page.locator(`.proactive-card[data-run="${run.id}"]`);
    await card.getByText('fixture check: passed').waitFor();
    await card.getByRole('button', { name: '看改动', exact: true }).click();
    await page.locator('.proactive-diff').getByText('proactive-fixture.txt').waitFor();
    await page.locator('.proactive-diff').getByRole('button', { name: '关闭', exact: true }).click();
    await card.getByRole('button', { name: '合入', exact: true }).click();
    await card.getByText('已合入').waitFor();
    assert.equal(existsSync(join(repo, 'proactive-fixture.txt')), true);
    assert.match(git('log', '-1', '--format=%B'), new RegExp(`LooChat-Proactive: ${todo.id}`));
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });
    await page.locator('.desk-filter').click();
    const row = page.locator(`.proactive-list [data-matter="${todo.id}"]`);
    await row.getByRole('button', { name: '撤回', exact: true }).click();
    await row.getByText('已撤回').waitFor();
    assert.equal(existsSync(join(repo, 'proactive-fixture.txt')), false);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await daemon.stop(); }
});

test('discarding sandbox work removes its worktree and branch and leaves the repository alone', async () => {
  const daemon = await startHub();
  const browser = await launchBrowser();
  try {
    const { repo, git, todo } = await arrangeSandbox(daemon);
    const run = await settled(daemon);
    assert.equal(run.status, 'ready', run.summary);
    const head = git('rev-parse', 'HEAD');
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' });
    await openPile(page, daemon, todo.id);
    const card = page.locator(`.proactive-card[data-run="${run.id}"]`);
    await card.getByRole('button', { name: '丢弃', exact: true }).click();
    await page.getByRole('dialog', { name: '丢弃这次改动？' }).getByRole('button', { name: '丢弃', exact: true }).click();
    await card.waitFor({ state: 'detached' });
    assert.equal(git('rev-parse', 'HEAD'), head);
    assert.equal(git('branch', '--list', run.branch), '');
  } finally { await browser.close(); await daemon.stop(); }
});
