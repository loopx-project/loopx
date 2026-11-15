import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

const git = (cwd, ...args) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
const identity = cwd => { git(cwd, 'config', 'user.name', 'Test'); git(cwd, 'config', 'user.email', 'test@example.com'); };
const commit = (cwd, message) => { git(cwd, 'add', '.'); git(cwd, 'commit', '-m', message); };

for (const width of [1440, 390]) for (const colorScheme of ['light', 'dark']) {
test(`Git sidebar reads lazily, preserves layout and pulls a real remote: ${width} ${colorScheme}`, { timeout: 120_000 }, async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-git-e2e-')), repo = join(root, 'project');
  mkdirSync(join(repo, 'src'), { recursive: true }); git(repo, 'init', '-b', 'main'); identity(repo);
  writeFileSync(join(repo, 'src/demo.ts'), 'export const value = 1;\n'); commit(repo, '建立项目');
  const remote = join(root, 'remote.git'), peer = join(root, 'peer');
  git(root, 'clone', '--bare', repo, remote); git(repo, 'remote', 'add', 'origin', remote); git(repo, 'push', '-u', 'origin', 'main');
  git(root, 'clone', remote, peer); identity(peer);
  writeFileSync(join(peer, 'remote.txt'), 'from remote\n'); commit(peer, '补上远端说明'); git(peer, 'push');
  writeFileSync(join(repo, 'src/demo.ts'), 'export const value = 2;\n'); git(repo, 'add', '.');
  writeFileSync(join(repo, 'src/demo.ts'), 'export const value = 3;\n');
  const daemon = await startDaemon({}, { workdir: repo }), browser = await launchBrowser();
  try {
    const made = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: repo, title: 'Git 管理验收' }) })).json();
    const context = await browser.newContext({ viewport: { width, height: 960 }, colorScheme, isMobile: width === 390, hasTouch: width === 390 });
    const page = await context.newPage(), requests = [], errors = [];
    page.setDefaultTimeout(12_000);
    page.on('pageerror', e => errors.push(String(e)));
    page.on('request', r => { if (r.url().includes('/git/')) requests.push(new URL(r.url()).pathname.split('/git/')[1]); });
    await openUi(page, daemon);
    if (width === 390) await page.locator('.menu-btn').click();
    await page.locator('.thread-row', { hasText: 'Git 管理验收' }).click();
    await page.locator('.topbar-more').waitFor();
    const composer = page.locator('.composer textarea').first();
    await composer.fill('保留这段草稿');
    assert.deepEqual(requests, [], 'opening a conversation must not read Git');
    const open = async () => { await page.locator('.topbar-more').click(); await page.getByRole('menuitem', { name: 'Git', exact: true }).click(); };
    await open();
    const panel = page.locator('.git-panel');
    await panel.locator('.git-file').first().waitFor();
    assert.ok(requests.includes('status')); assert.ok(!requests.includes('log') && !requests.includes('diff') && !requests.includes('commit'));
    assert.equal(await panel.getByRole('button', { name: '拉取更新', exact: true }).isDisabled(), true);
    assert.match(await panel.locator('.git-block').innerText(), /未提交/);
    assert.equal(await panel.locator('.git-file').count(), 2, 'the same file has staged and unstaged views');
    await panel.locator('.git-files').filter({ hasText: '未暂存' }).locator('.git-file').click();
    await panel.locator('.git-diff').waitFor();
    assert.match(await panel.locator('.git-diff').innerText(), /value = 3/);
    await panel.getByRole('button', { name: '返回', exact: true }).click();
    await panel.getByRole('tab', { name: '提交记录', exact: true }).click();
    await panel.locator('.git-commit').first().waitFor();
    assert.ok(requests.includes('log')); assert.ok(!requests.includes('commit'));
    await panel.locator('.git-commit').first().click();
    await panel.locator('.git-detail .git-file').waitFor();
    assert.ok(requests.includes('commit'));
    await panel.locator('.git-detail .git-file').click();
    await panel.locator('.git-diff').waitFor();
    assert.match(await panel.locator('.git-diff').innerText(), /value = 1/);
    await panel.getByRole('button', { name: '返回', exact: true }).click();
    await panel.getByRole('button', { name: '返回', exact: true }).click();
    await panel.getByRole('tab', { name: /变更/ }).click();

    // A delayed refresh keeps the branch, file list, header, and footer in their original slots.
    await page.route('**/git/status', async route => { await new Promise(r => setTimeout(r, 700)); await route.continue(); });
    const before = await panel.locator('.git-tabs').boundingBox();
    await panel.getByRole('button', { name: '刷新本地状态', exact: true }).click();
    assert.equal(await panel.locator('.git-file').count(), 2);
    assert.deepEqual(await panel.locator('.git-tabs').boundingBox(), before);
    await panel.getByRole('button', { name: '刷新本地状态', exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('.git-head button[title]')?.disabled);
    await page.unroute('**/git/status');

    const head = git(repo, 'rev-parse', 'HEAD');
    await panel.getByRole('button', { name: '获取远端', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.git-foot')?.textContent.includes('已获取远端'));
    assert.equal(git(repo, 'rev-parse', 'HEAD'), head);
    assert.match(await panel.locator('.git-tracking').innerText(), /↓ 1/);
    if (process.env.CALLI_E2E_SHOTS_DIR) {
      mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `git-${width}-${colorScheme}.png`) });
    }
    git(repo, 'reset', '--hard', 'HEAD');
    await panel.getByRole('button', { name: '刷新本地状态', exact: true }).click();
    await panel.locator('.git-empty', { hasText: '工作区干净' }).waitFor();
    await panel.getByRole('button', { name: '拉取更新', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.git-foot')?.textContent.includes('已快进拉取更新'));
    assert.equal(readFileSync(join(repo, 'remote.txt'), 'utf8'), 'from remote\n');
    assert.match(await panel.locator('.git-tracking').innerText(), /↓ 0/);

    // Failure is visible and retains the last useful snapshot.
    git(repo, 'remote', 'set-url', 'origin', join(root, 'missing'));
    await panel.getByRole('button', { name: '获取远端', exact: true }).click();
    await panel.getByRole('alert').waitFor();
    assert.equal(await panel.locator('.git-branch').innerText(), 'main');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (width === 390) await page.goBack(); else await panel.getByRole('button', { name: '关闭', exact: true }).click();
    await panel.waitFor({ state: 'detached' });
    assert.equal(await composer.inputValue(), '保留这段草稿');
    assert.match(await page.locator('.topbar-title').innerText().catch(() => page.locator('.topbar-heading').innerText()), /Git 管理验收/);
    assert.deepEqual(errors, []);
    await context.close();
  } finally { await browser.close(); await daemon.stop(); rmSync(root, { recursive: true, force: true }); }
});
}
