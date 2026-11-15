import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

// The board lays out conversations by what they are doing and what their agents report; the person marks them done.
let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
const threads = {};

async function json(path, init) {
  const response = await api(daemon, path, init);
  assert.ok(response.ok, `${path}: ${response.status} ${response.ok ? '' : await response.clone().text()}`);
  return response.status === 204 ? null : response.json();
}
const post = (path, body) => json(path, { method: 'POST', body: JSON.stringify(body) });
const view = (id) => json(`/api/threads/${id}`);
async function until(check, message) {
  for (let i = 0; i < 100; i++) { if (await check()) return; await new Promise((r) => setTimeout(r, 100)); }
  throw new Error(message);
}

before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'claude', adapter: 'claude', label: 'Claude Code', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-acp.mjs')] },
  ] }) });
  // Automatic handover checks hold a thread for a moment after each turn; they have nothing to do with the board.
  await json('/api/handover-settings', { method: 'PUT', body: JSON.stringify({ enabled: false, threshold: 90, orders: {} }) });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
  const create = (title, extra = {}) => post('/api/threads', { agent: 'claude', title, cwd: daemon.workdir, ...extra });
  threads.before = await create('装看板之前');
  await post(`/api/threads/${threads.before.id}/turns`, { text: 'report 待验收 不该被读到' });
  await until(async () => (await view(threads.before.id)).status === 'idle', 'the first turn did not end');

  const board = (await json('/api/extensions')).find((state) => state.manifest.id === 'board');
  assert.ok(board.manifest.permissions.includes('progress.report'));
  await post('/api/extensions/board/install', { granted: board.manifest.permissions });
  threads.review = await create('重构支付流程');
  threads.waiting = await create('发布前检查', { workMode: 'execute', permissionMode: 'ask' });
  await post(`/api/threads/${threads.review.id}/turns`, { text: 'report 待验收 diff 已就绪' });
  await post(`/api/threads/${threads.waiting.id}/turns`, { text: 'please approve' });
  await until(async () => (await view(threads.review.id)).report?.stage === 'review', 'the progress line was not recorded');
  assert.equal((await view(threads.before.id)).report ?? null, null, 'nobody was asked before consent');
});
after(async () => { await browser?.close(); await daemon?.stop(); });

const column = (page, name) => page.locator('.ext-board-column').filter({ has: page.getByRole('heading', { name: new RegExp(`^${name}`) }) });
const card = (scope, title) => scope.locator('.ext-board-card-wrap', { hasText: title });

test('conversations sit where their state and agents put them, in all layouts', { timeout: 90000 }, async () => {
  const errors = [];
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(error.message));
      await openUi(page, daemon);
      await page.goto(daemon.base + '/#/board');
      if (mobile) {
        // Phones open on what needs the person.
        await page.getByRole('tab', { name: /等你/, selected: true }).waitFor();
        await card(page, '发布前检查').getByText('等你批准').waitFor();
        await page.getByRole('tab', { name: /待验收/ }).click();
        await card(page, '重构支付流程').getByText('diff 已就绪').waitFor();
      } else {
        await card(column(page, '等你'), '发布前检查').getByText('等你批准').waitFor();
        const review = card(column(page, '待验收'), '重构支付流程');
        await review.getByText('diff 已就绪').waitFor();
        await review.getByText('agent 请你验收').waitFor();
        await card(column(page, '待验收'), '装看板之前').getByText('有新结果').waitFor();
      }
      if (shots) await page.screenshot({ path: join(shots, `board-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'the board fits the viewport');
    } finally { await context.close(); }
  }
  assert.deepEqual(errors, []);
});

test('the person marks a conversation done, and new work reopens it', async () => {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  try {
    await openUi(page, daemon);
    await page.goto(daemon.base + '/#/board');
    await page.getByRole('checkbox', { name: '标记完成: 重构支付流程' }).click();
    await card(column(page, '已完成'), '重构支付流程').waitFor();
    const done = await view(threads.review.id);
    assert.ok(done.doneAt);
    assert.equal(done.report, null);

    await until(async () => (await view(threads.review.id)).status === 'idle', 'the earlier turn did not end');
    // The host leaves the progress instructions out once the agent has them in view; the fake agent writes its line only when it sees them.
    await post(`/api/threads/${threads.review.id}/turns`, { text: 'report 进行中 等 CI\n<calli-progress>' });
    await card(column(page, '进行中'), '重构支付流程').getByText('等 CI').waitFor();
    assert.equal((await view(threads.review.id)).doneAt, null);

    // On wide screens a card peeks its conversation beside the board; the full page leaves the board.
    await card(column(page, '等你'), '发布前检查').locator('.ext-board-card').click();
    await page.locator('.thread-peek').waitFor();
    assert.equal(await page.locator('.ext-board-page').count(), 1);
    await page.getByRole('button', { name: '打开完整页面' }).click();
    await page.locator('.composer-slot').waitFor();
    assert.equal(await page.locator('.ext-board-page').count(), 0);
  } finally { await page.close(); }
});

async function openBoard(width = 1400, height = 900, options = {}) {
  const page = await browser.newPage({ viewport: { width, height }, ...options });
  await openUi(page, daemon);
  await page.goto(daemon.base + '/#/board');
  return page;
}

test('a card can be dragged to another column; running and waiting ones are refused except for done', { timeout: 60000 }, async () => {
  const mover = await post('/api/threads', { agent: 'claude', title: '拖动的卡片', cwd: daemon.workdir });
  await post(`/api/threads/${mover.id}/turns`, { text: 'report 待验收 拖着玩' });
  await until(async () => (await view(mover.id)).report?.stage === 'review', 'the progress line was not recorded');
  const page = await openBoard();
  try {
    const dragging = card(column(page, '待验收'), '拖动的卡片');
    await dragging.waitFor();
    // Out of review into in progress: it moves at once and the host remembers the person's placement.
    await dragging.dragTo(column(page, '进行中'));
    await card(column(page, '进行中'), '拖动的卡片').getByText('你标为进行中').waitFor();
    await until(async () => (await view(mover.id)).report?.stage === 'doing', 'the placement was not saved');
    assert.equal((await view(mover.id)).report.by, 'user');

    // A thread that waits on the person cannot be placed elsewhere, and the refusal is said in a fixed pill.
    const waiting = card(column(page, '等你'), '发布前检查');
    const before = await waiting.boundingBox();
    await waiting.dragTo(column(page, '待验收'));
    const pill = page.locator('.ext-board-notice [role="status"], .ext-board-notice').filter({ hasText: '只能标为已完成' }).first();
    await pill.waitFor();
    await card(column(page, '等你'), '发布前检查').waitFor();
    assert.deepEqual(await card(column(page, '等你'), '发布前检查').boundingBox(), before, 'the refused card stays put');
    if (shots) await page.screenshot({ path: join(shots, 'board-drag-refused.png'), animations: 'disabled' });

    // Out of done is a placement, which clears done.
    await page.getByRole('checkbox', { name: '标记完成: 拖动的卡片' }).click();
    await card(column(page, '已完成'), '拖动的卡片').waitFor();
    await card(column(page, '已完成'), '拖动的卡片').dragTo(column(page, '待验收'));
    await card(column(page, '待验收'), '拖动的卡片').waitFor();
    await until(async () => (await view(mover.id)).doneAt === null, 'done was not cleared');
  } finally { await page.close(); }
});

test('a card can be put off until later and leaves that column when it is worked on again', { timeout: 60000 }, async () => {
  const later = await post('/api/threads', { agent: 'claude', title: '以后再说的卡片', cwd: daemon.workdir });
  await post(`/api/threads/${later.id}/turns`, { text: 'report 待验收 先放着' });
  await until(async () => (await view(later.id)).report?.stage === 'review', 'the progress line was not recorded');
  const page = await openBoard(1360, 860);
  try {
    // Wide screens keep it as a narrow rail until opened; a card can be dropped on the rail.
    const rail = page.getByRole('button', { name: /^展开以后再做/ });
    assert.equal(await rail.getAttribute('aria-expanded'), 'false');
    const width = (await page.locator('.ext-board-columns').evaluate((node) => [node.scrollWidth, node.clientWidth]));
    assert.ok(width[0] <= width[1], `four columns and the rail fit without scrolling: ${width}`);
    if (shots) await page.screenshot({ path: join(shots, 'board-backlog-rail-desktop.png'), animations: 'disabled' });
    await card(column(page, '待验收'), '以后再说的卡片').dragTo(rail);
    await until(async () => (await view(later.id)).report?.stage === 'backlog', 'the drop on the rail was not saved');
    await page.getByRole('button', { name: '展开以后再做（1 个）' }).click();
    await card(column(page, '以后再做'), '以后再说的卡片').getByText('你放到以后再做').waitFor();
    await page.reload();
    await page.getByRole('button', { name: '收起以后再做' }).waitFor();
    await card(column(page, '以后再做'), '以后再说的卡片').waitFor();
    await page.getByRole('button', { name: '收起以后再做' }).click();
    await page.getByRole('button', { name: /^展开以后再做/ }).waitFor();
    await page.reload();
    await page.getByRole('button', { name: /^展开以后再做/ }).waitFor();
    await page.getByRole('button', { name: /^展开以后再做/ }).click();
    await card(column(page, '以后再做'), '以后再说的卡片').waitFor();
    await until(async () => (await view(later.id)).report?.stage === 'backlog', 'the placement was not saved');
    assert.equal((await view(later.id)).report.by, 'user');
    if (shots) await page.screenshot({ path: join(shots, 'board-backlog-desktop.png'), animations: 'disabled' });

    // Running or waiting cards cannot be put off.
    await card(column(page, '等你'), '发布前检查').dragTo(column(page, '以后再做'));
    await page.locator('.ext-board-notice').filter({ hasText: '只能标为已完成' }).first().waitFor();

    // Working on it again takes it out; the turn's end, with no progress line, leaves it to be checked.
    await post(`/api/threads/${later.id}/turns`, { text: '再做一点' });
    await until(async () => (await view(later.id)).status === 'idle' && (await view(later.id)).report === null, 'the turn did not end');
    await card(column(page, '待验收'), '以后再说的卡片').waitFor();
    assert.equal(await card(column(page, '以后再做'), '以后再说的卡片').count(), 0);
  } finally { await page.close(); }
});

test('cards are not draggable on phones', async () => {
  const page = await openBoard(390, 844, { isMobile: true, hasTouch: true });
  try {
    await page.getByRole('tab', { name: /等你/ }).waitFor();
    assert.equal(await page.locator('.ext-board-card-wrap[draggable="true"]').count(), 0);
  } finally { await page.close(); }
});

test('right click opens the host menu instead of the browser menu; a click peeks the thread beside the board', async () => {
  const page = await openBoard();
  try {
    const target = card(column(page, '等你'), '发布前检查');
    const prevented = await target.evaluate((node) => {
      const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 200, clientY: 200 });
      node.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(prevented, true);
    await page.getByRole('menu').first().waitFor();
    await page.keyboard.press('Escape');
    await target.locator('.ext-board-open').click();
    await page.locator('.thread-peek').waitFor();
    // The board stays beside the thread.
    assert.equal(await page.locator('.ext-board-page').count(), 1);
  } finally { await page.close(); }
});

test('new chat opens a draft in the filtered project; the assistant button hands the board over', { timeout: 60000 }, async () => {
  const page = await openBoard();
  try {
    await page.getByRole('button', { name: '新对话' }).click();
    await page.locator('.composer-slot').waitFor();
    await page.goto(daemon.base + '/#/board');
    const ask = page.getByRole('button', { name: '让助手看看' });
    await ask.waitFor();
    await ask.click();
    // The host asks for consent once, then opens the assistant with the summary as the person's message.
    const allow = page.getByRole('button', { name: /允许|授权/ });
    if (await allow.count()) await allow.first().click();
    await page.getByText('帮我看看看板上这').first().waitFor();
  } finally { await page.close(); }
});

test('a pinned project becomes a pill that narrows the board, and the pin survives a reload', { timeout: 60000 }, async () => {
  mkdirSync(join(daemon.workdir, 'elsewhere'), { recursive: true });
  execFileSync('git', ['init', '-q'], { cwd: join(daemon.workdir, 'elsewhere') });
  const other = await post('/api/threads', { agent: 'claude', title: '别的目录里的线程', cwd: join(daemon.workdir, 'elsewhere') });
  const page = await openBoard();
  try {
    const bar = page.getByRole('toolbar', { name: '项目快捷切换' });
    await bar.waitFor();
    assert.equal(await bar.getByRole('button').count(), 2, 'only 全部 and 固定项目 before pinning');
    await bar.getByRole('button', { name: '固定项目' }).click();
    const name = daemon.workdir.split('/').filter(Boolean).at(-1);
    await page.getByRole('menuitemcheckbox', { name: new RegExp(name) }).first().click();
    await page.keyboard.press('Escape');
    const pill = bar.getByRole('button', { name: new RegExp(name) }).first();
    await pill.waitFor();
    await pill.click();
    assert.equal(await pill.getAttribute('aria-pressed'), 'true');
    await page.locator('.ext-board-page').getByText('别的目录里的线程').first().waitFor({ state: 'detached', timeout: 3000 });
    await pill.click();
    await page.locator('.ext-board-page').getByText('别的目录里的线程').first().waitFor();
    assert.equal(await bar.getByRole('button', { name: '全部' }).getAttribute('aria-pressed'), 'true');
    await page.reload();
    await bar.getByRole('button', { name: new RegExp(name) }).first().waitFor();
    if (shots) await page.screenshot({ path: join(shots, 'pins-desktop.png'), animations: 'disabled' });
  } finally { await page.close(); }
  void other;
});
