import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { linkDaemon, startDaemon, openUi, api, launchBrowser, closeSettings } from '../harness.mjs';

let root, hub, remote, browser, page;
const envFor = (home) => ({ HOME: home, CLAUDE_CONFIG_DIR: join(home, '.claude'), CODEX_HOME: join(home, '.codex'), PI_CODING_AGENT_DIR: join(home, '.pi/agent'), CALLI_AGY_HOME: join(home, '.agy') });
function seed(home, body) {
  const dir = join(home, '.claude/skills/review-code');
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'SKILL.md'), '---\nname: review-code\ndescription: 检查代码质量与测试覆盖\n---\n# 代码审查\n检查边界条件与错误处理。');
  writeFileSync(join(dir, 'review.sh'), body, { mode: 0o755 });
}

before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-skills-e2e-'));
  const localHome = join(root, 'local');
  const remoteHome = join(root, 'remote');
  seed(localHome, 'echo a');
  seed(remoteHome, 'echo b');
  remote = await startDaemon({ ...envFor(remoteHome), CALLI_SOCKET: join(remoteHome, '.local/state/calli/daemon.sock') });
  hub = await startDaemon({ ...envFor(localHome) });
  await linkDaemon(hub, remote);
  browser = await launchBrowser();
  page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on('pageerror', (err) => console.error('browser error:', err));
  await page.addInitScript((token) => localStorage.setItem('calli.token', token), hub.token);
  await openUi(page, hub);
  await openSkills(page);
});
after(async () => {
  await browser?.close();
  await hub?.stop();
  await remote?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});

async function openSkills(p, phone = false, shot) {
  if (phone) await p.locator('.menu-btn').click();
  assert.equal(await p.locator('.side-nav').getByRole('button', { name: '技能', exact: true }).count(), 0);
  await p.getByRole('button', { name: '配置', exact: true }).click();
  await p.locator('.settings-nav').getByRole('button', { name: '技能', exact: true }).waitFor();
  if (shot) { await p.waitForTimeout(300); await p.screenshot({ path: shot }); }
  await p.locator('.settings-nav').getByRole('button', { name: '技能', exact: true }).click();
}

async function openSync(p = page) {
  await p.getByRole('button', { name: 'review-code 的操作' }).click();
  await p.getByRole('menuitem', { name: '同步到…' }).click();
  return p.getByRole('dialog', { name: '同步「review-code」' });
}

test('machine matrix, search, instructions, and remote selection retain the hub', async () => {
  await page.locator('.skills-differs').waitFor();
  assert.deepEqual((await page.locator('.skills-grid-head .skills-cell-machine').allInnerTexts()).sort(), ['devbox', '本机'].sort());
  await page.getByLabel('搜索技能').fill('absent');
  await page.getByText('没有匹配的技能。').waitFor();
  await page.getByLabel('清除搜索').click();
  await page.getByTitle('查看说明', { exact: true }).click();
  await page.getByRole('heading', { name: '代码审查', exact: true }).waitFor();
  await page.getByRole('dialog').getByLabel('关闭').click();
  await closeSettings(page);
  await page.getByRole('tab', { name: 'devbox' }).click();
  await openSkills(page);
  await page.locator('.skills-differs').waitFor();
  assert.deepEqual((await page.locator('.skills-grid-head .skills-cell-machine').allInnerTexts()).sort(), ['devbox', '本机'].sort());
});

test('sync through the proxy preserves scripts and asks before replacing a different version', async () => {
  const dialog = await openSync();
  await dialog.locator('.skill-source', { hasText: '本机' }).click();
  // Choose only the remote Claude instance; existing copies are not preselected.
  while (await dialog.locator('.skill-target[aria-pressed=true]').count()) await dialog.locator('.skill-target[aria-pressed=true]').first().click();
  await dialog.locator('.skill-target-row', { hasText: 'devbox' }).getByRole('button', { name: 'Claude Code', exact: true }).click();
  await dialog.getByRole('button', { name: '同步到 1 个 agent' }).click();
  await dialog.getByRole('button', { name: '覆盖 1 处' }).waitFor();
  assert.equal(readFileSync(join(root, 'remote/.claude/skills/review-code/review.sh'), 'utf8'), 'echo b');
  await dialog.getByRole('button', { name: '覆盖 1 处' }).click();
  await dialog.getByRole('button', { name: '完成', exact: true }).waitFor();
  assert.equal(readFileSync(join(root, 'remote/.claude/skills/review-code/review.sh'), 'utf8'), 'echo a');
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
});

test('desktop and phone, light and dark fit the viewport with readable sync dialogs', async () => {
  const shots = process.env.CALLI_SKILLS_SHOTS;
  if (shots) mkdirSync(shots, { recursive: true });
  for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
    const p = await ctx.newPage();
    await p.addInitScript((token) => localStorage.setItem('calli.token', token), hub.token);
    await openUi(p, hub);
    await openSkills(p, phone, shots ? join(shots, `${phone ? 'phone' : 'desktop'}-${theme}-settings.png`) : undefined);
    await p.locator('.skills-row').waitFor();
    await p.locator('.skills-grid-head .skills-cell-machine').last().waitFor({ state: 'attached' });
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.ok((await p.locator('.skills-cell-name').last().boundingBox()).width > 200);
    await p.waitForTimeout(300);
    if (shots) await p.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${theme}.png`) });
    const dialog = await openSync(p);
    await p.waitForTimeout(300);
    const box = await dialog.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= (phone ? 390 : 1440));
    await p.waitForTimeout(300);
    if (shots) await p.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${theme}-sync.png`) });
    await ctx.close();
  }
});
