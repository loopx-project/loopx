import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

const waitTurnDone = async (id, count = 1) => {
  for (let i = 0; i < 200; i++) {
    const [t, evs] = await Promise.all([
      (await api(daemon, `/api/threads/${id}`)).json(),
      (await api(daemon, `/api/threads/${id}/events`)).json(),
    ]);
    if (t.status === 'idle' && evs.filter((e) => e.type === 'turn.done').length >= count) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error(`thread ${id} did not finish turn ${count}`);
};

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

test('Scheme A: flat Fork & Handoff menu, reply-footer (...) handoff, 12px topic drawer icons, promote-to-topic, and phone layout', async () => {
  const root = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '主线重构' }) })).json();
  await api(daemon, `/api/threads/${root.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '主线第一轮讨论方案 A 与 C' }) });
  await waitTurnDone(root.id, 1);

  await openUi(page, daemon);
  await page.locator('.sidebar-inner').getByText('主线重构').first().click();
  await page.getByText('hi there 主线第一轮讨论方案 A 与 C').waitFor({ timeout: 10_000 });

  // Page menu has flat top-level "分叉" and "接力继续" (no "分叉与交接" or legacy "交接"), and "复制交接信息" inside "复制".
  await page.locator('.topbar-more').click();
  assert.equal(await page.getByRole('menuitem', { name: '分叉与交接' }).count(), 0);
  assert.equal(await page.getByRole('menuitem', { name: '交接', exact: true }).count(), 0);
  await page.getByRole('menuitem', { name: '分叉', exact: true }).waitFor();
  await page.getByRole('menuitem', { name: '接力继续', exact: true }).waitFor();
  await page.getByRole('menuitem', { name: '复制' }).click();
  await page.locator('.menu-branch').getByRole('menuitem', { name: '复制交接信息' }).waitFor();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');

  // Reply-footer (...) menu contains "接力继续" right beside the thumb.
  await page.locator('.message-actions button[aria-label="更多消息操作"]').last().click();
  await page.getByRole('menuitem', { name: '接力继续', exact: true }).click();
  const card = page.locator('.fork-subchat-dialog');
  await card.waitFor();

  // Codex composer sheet: clean "接力继续" heading, no jargon, 2 buttons.
  assert.equal((await card.locator('#fork-card-title').innerText()).trim(), '接力继续');
  assert.equal(await card.getByText(/轻量|省 Token/).count(), 0);
  await card.getByRole('button', { name: '接力继续' }).waitFor();
  await card.getByRole('button', { name: '取消' }).waitFor();

  const shotDir = process.env.LOOCHAT_FORK_SHOTS;
  if (shotDir) {
    mkdirSync(shotDir, { recursive: true });
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${shotDir}/desktop-card-light.png`, animations: 'disabled' });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await page.waitForTimeout(200);
    await page.screenshot({ path: `${shotDir}/desktop-card-dark.png`, animations: 'disabled' });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  }

  // Typing a note inside the composer switches the primary button to "接力并发送".
  await card.locator('.fork-composer textarea').fill('子话题讨论');
  await card.getByRole('button', { name: '接力并发送' }).click();
  await card.waitFor({ state: 'detached', timeout: 10_000 });

  // Opens new handoff thread nested under parent topic with 12px handoff icon; anchor never leaks.
  await page.locator('.markdown').filter({ hasText: '子话题讨论' }).last().waitFor({ timeout: 10_000 });
  assert.equal(await page.locator('.row.user').count(), 1, 'no root turns replayed');
  assert.equal(await page.locator('.row.user').filter({ hasText: '<loochat-fork-anchor>' }).count(), 0);
  await page.locator('.sidebar-inner .handover-member .fork-kind-ico[aria-label="接力继续"]').first().waitFor({ timeout: 10_000 });

  // Reopen from root via topbar ... -> 接力继续, switch Agent to agy, and click "接力继续".
  await page.locator('.sidebar-inner').getByText('主线重构').first().click();
  await page.locator('.topbar-more').click();
  await page.getByRole('menuitem', { name: '接力继续', exact: true }).click();
  await card.waitFor();
  await card.locator('.fork-composer-bar .agent-btn').click();
  await page.locator('.pop-panel .pop-item', { hasText: /Antigravity|agy/i }).first().click();
  await card.getByRole('button', { name: '接力继续' }).click();
  await card.waitFor({ state: 'detached', timeout: 10_000 });

  // Continues immediately on the switched agent (agy) with 0-token anchor + paired loochat history.
  await page.locator('.topbar-title', { hasText: '主线重构（继续）' }).waitFor({ timeout: 10_000 });
  await page.locator('.markdown').filter({ hasText: 'echo:接着上面的事情继续做' }).last().waitFor({ timeout: 10_000 });
  assert.equal(await page.locator('.row.user').filter({ hasText: '<loochat-fork-anchor>' }).count(), 0);

  const allThreads = await (await api(daemon, '/api/threads')).json();
  const continued = allThreads.find((t) => t.title === '主线重构（继续）');
  assert.ok(continued, 'continued handoff thread exists');
  assert.equal(continued.agent, 'agy');
  assert.equal(continued.forkGroup, root.id);
  assert.equal(continued.forkKind, 'handoff');
  const contEvents = await (await api(daemon, `/api/threads/${continued.id}/events`)).json();
  const firstStart = contEvents.find((e) => e.type === 'turn.started');
  assert.match(String(firstStart.data.text), /<loochat-fork-anchor>/);
  assert.match(String(firstStart.data.text), /主线第一轮讨论方案 A 与 C/);
  assert.match(String(firstStart.data.text), new RegExp(`loochat history ${root.id} --turns 6`));

  // Promote a derived child out to a standalone top-level topic via right-click menu.
  const childRow = page.locator('.sidebar-inner .handover-member', { hasText: '子话题讨论' }).first();
  await childRow.click({ button: 'right' });
  await page.locator('.menu-float').getByRole('menuitem', { name: '移出为独立话题', exact: true }).click();
  await page.locator('.sidebar-inner .thread-row', { hasText: '子话题讨论' }).first().waitFor({ timeout: 10_000 });

  // Phone viewport check: open card on 390px phone in light & dark, verify zero horizontal overflow.
  const phoneCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    const pPage = await phoneCtx.newPage();
    await openUi(pPage, daemon);
    await pPage.locator('.topbar button').first().click();
    await pPage.locator('.sidebar-inner').getByText('主线重构').first().click();
    await pPage.locator('.topbar-more').click();
    await pPage.getByRole('menuitem', { name: '接力继续', exact: true }).click();
    await pPage.locator('.fork-subchat-dialog').waitFor();
    assert.equal(await pPage.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, 'no horizontal overflow on phone card');
    if (shotDir) {
      await pPage.waitForTimeout(300);
      await pPage.screenshot({ path: `${shotDir}/phone-card-light.png`, animations: 'disabled' });
      await pPage.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
      await pPage.waitForTimeout(200);
      await pPage.screenshot({ path: `${shotDir}/phone-card-dark.png`, animations: 'disabled' });
    }
  } finally {
    await phoneCtx.close();
  }
});

test('reply-footer fork icon does Codex 1-click native fork and nests under parent topic with 12px fork icon', async () => {
  const { execFileSync } = await import('node:child_process');
  const { writeFileSync } = await import('node:fs');
  const { join } = await import('node:path');
  execFileSync('git', ['init', '-b', 'main'], { cwd: daemon.workdir, stdio: 'ignore' });
  execFileSync('git', ['config', 'user.email', 'test@example.com'], { cwd: daemon.workdir, stdio: 'ignore' });
  execFileSync('git', ['config', 'user.name', 'Test'], { cwd: daemon.workdir, stdio: 'ignore' });
  writeFileSync(join(daemon.workdir, 'README.md'), 'hello\n');
  execFileSync('git', ['add', 'README.md'], { cwd: daemon.workdir, stdio: 'ignore' });
  execFileSync('git', ['commit', '-m', 'init'], { cwd: daemon.workdir, stdio: 'ignore' });

  const topic = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '两轮对话' }) })).json();
  await api(daemon, `/api/threads/${topic.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '第一轮问题' }) });
  await waitTurnDone(topic.id, 1);

  await openUi(page, daemon);
  await page.locator('.sidebar-inner').getByText('两轮对话').first().click();
  await page.getByText('hi there 第一轮问题').waitFor({ timeout: 10_000 });

  // Clicking reply-footer fork icon performs 1-click native fork with zero modal, grouped under parent topic.
  await page.locator('.message-actions button[aria-label="分叉"]').last().click();
  assert.equal(await page.locator('.fork-subchat-dialog').count(), 0);
  await page.locator('.topbar-title', { hasText: '两轮对话（分叉）' }).waitFor({ timeout: 10_000 });
  await page.getByText('hi there 第一轮问题').waitFor({ timeout: 5_000 });
  await page.locator('.sidebar-inner .handover-member .fork-kind-ico[aria-label="原生分叉"]').first().waitFor({ timeout: 5_000 });

  const shotDir = process.env.LOOCHAT_FORK_SHOTS;
  if (shotDir) {
    mkdirSync(shotDir, { recursive: true });
    await page.waitForTimeout(200);
    await page.screenshot({ path: `${shotDir}/desktop-drawer-light.png`, animations: 'disabled' });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await page.waitForTimeout(200);
    await page.screenshot({ path: `${shotDir}/desktop-drawer-dark.png`, animations: 'disabled' });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  }

  const forked = (await (await api(daemon, '/api/threads')).json()).find((t) => t.title === '两轮对话（分叉）');
  assert.equal(forked.forkGroup, topic.id);
  assert.equal(forked.forkKind, 'fork');
});

test('handoff works while running and fork is disabled in menu', { timeout: 120_000 }, async () => {
  const runningThread = await (await api(daemon, '/api/threads', {
    method: 'POST',
    body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '长任务主线' }),
  })).json();
  await api(daemon, `/api/threads/${runningThread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '第一轮结果' }) });
  await waitTurnDone(runningThread.id, 1);
  await api(daemon, `/api/threads/${runningThread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow 持续运行中' }) });
  for (let i = 0; i < 100; i++) {
    const t = await (await api(daemon, `/api/threads/${runningThread.id}`)).json();
    if (t.status === 'running') break;
    await new Promise((r) => setTimeout(r, 20));
  }

  try {
    await openUi(page, daemon);
    await page.locator('.sidebar-inner').getByText('长任务主线').first().click();
    await page.getByText('hi there 第一轮结果').waitFor({ timeout: 10_000 });

    // While running: top-level "分叉" is disabled, while "接力继续" is enabled.
    await page.locator('.topbar-more').click();
    assert.equal(await page.getByRole('menuitem', { name: '分叉', exact: true }).isDisabled(), true);
    const handoffMenu = page.getByRole('menuitem', { name: '接力继续', exact: true });
    assert.equal(await handoffMenu.isDisabled(), false);
    await handoffMenu.click();

    const card = page.locator('.fork-subchat-dialog');
    await card.waitFor();
    await card.locator('.fork-composer textarea').fill('运行中子话题');
    await card.getByRole('button', { name: '接力并发送' }).click();
    await card.waitFor({ state: 'detached', timeout: 10_000 });
    await page.locator('.markdown').filter({ hasText: '运行中子话题' }).last().waitFor({ timeout: 10_000 });

    // Sidebar context menu opens "接力继续" while source is running.
    const rootRow = page.locator('.sidebar-inner .thread-row', { hasText: '长任务主线' }).first();
    await rootRow.click({ button: 'right' });
    await page.locator('.menu-float').getByRole('menuitem', { name: '接力继续', exact: true }).click();
    await card.waitFor({ timeout: 5_000 });
    await card.getByRole('button', { name: '取消' }).click();
    await card.waitFor({ state: 'detached', timeout: 5_000 });
  } finally {
    await api(daemon, `/api/threads/${runningThread.id}/cancel`, { method: 'POST' }).catch(() => undefined);
  }
});
