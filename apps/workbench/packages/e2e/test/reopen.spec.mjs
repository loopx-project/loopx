// Reopening a long thread: the newest turn shows whole, from its question, however many events
// it took; older turns come a page at a time from the top, without moving what is on screen.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, repoRoot } from '../harness.mjs';

let daemon;
let browser;
let page;
let dataDir;
let workdir;
const shots = process.env.CALLI_E2E_SHOTS_DIR;

/** Long thoughts must not change the number of answer summaries in a page. */
const LONG_TURN = 21_000;
const recent = [...Array.from({ length: 7 }, (_, i) => `middle-${i}`), 'latest'];

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-e2e-long-'));
  workdir = mkdtempSync(join(tmpdir(), 'calli-e2e-long-work-'));
  // The log is written directly: streaming this much through a fake agent would take minutes.
  const { Store } = await import(join(repoRoot, 'daemon', 'dist', 'store.js'));
  const store = new Store(dataDir);
  store.createThread({ id: 'long', agent: 'pi', cwd: workdir, title: 'long thread', permissionMode: 'auto_edit', model: null, effort: null });
  store.transaction(() => {
    for (const name of ['first', 'second', ...recent]) {
      const add = (type, data) => store.appendEvent('long', name, type, data);
      add('turn.started', { text: `${name} question` });
      for (let i = 0; i < (name.startsWith('middle-') ? 1 : LONG_TURN); i++) add('reasoning.delta', { text: 'thinking. ' });
      add('tool.start', { id: `${name}-ls`, title: 'ls', kind: 'execute', status: 'in_progress' });
      add('tool.end', { id: `${name}-ls`, status: 'completed', output: 'a.txt' });
      for (let i = 0; i < 40; i++) add('message.delta', { text: `${name} answer line ${i}\n\n` });
      add('turn.done', { stopReason: 'end_turn' });
    }
  });
  store.createThread({ id: 'snapshots', agent: 'pi', cwd: workdir, title: '按需加载对话', permissionMode: 'auto_edit', model: null, effort: null });
  store.transaction(() => {
    for (let i = 1; i <= 12; i++) {
      const add = (type, data) => store.appendEvent('snapshots', `turn-${i}`, type, data);
      add('turn.started', { text: `第 ${i} 轮问题` });
      add('tool.start', { id: `check-${i}`, kind: 'execute', title: '检查结果', status: 'in_progress' });
      for (let n = 0; n < 30; n++) add('tool.update', { id: `check-${i}`, output: `snapshot ${n}\n${'output\n'.repeat(1000)}` });
      add('tool.end', { id: `check-${i}`, status: 'completed' });
      add('message.delta', { text: `${'已检查加载流程，历史记录完整保留。\n\n'.repeat(8)}第 ${i} 轮回答结束` });
      add('turn.done', { stopReason: 'end_turn' });
    }
  });
  store.close();

  daemon = await startDaemon({ CALLI_DATA_DIR: dataDir, CALLI_WORKDIRS: workdir });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, permissions: ['clipboard-read', 'clipboard-write'] });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
});

const questions = () => page.locator('.thread .bubble').allTextContents();
const view = () =>
  page.evaluate(() => {
    const el = document.querySelector('.scroller');
    const latest = [...document.querySelectorAll('.thread .bubble')].find((b) => b.textContent?.includes('latest question'));
    return {
      dist: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight),
      latestTop: latest ? Math.round(latest.getBoundingClientRect().top) : null,
    };
  });

test('the newest turn opens whole and at the bottom; older turns page in from the top', async () => {
  await openUi(page, daemon);
  const requests = [];
  page.on('request', (r) => {
    if (r.url().includes('/threads/long/history')) requests.push(r.url().split('/history')[1]);
  });
  await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: 'long thread' }).first().click();

  // Even an exceptionally long thought leaves all eight questions and answers in the page.
  await page.getByText('latest answer line 39').waitFor({ timeout: 15_000 });
  assert.deepEqual(await questions(), recent.map(name => `${name} question`));
  assert.equal((await view()).dist, 0, 'a reopened thread sits at its newest line');
  assert.deepEqual(requests, ['']);

  // Scrolling near the top loads a page automatically. Hold its response to measure the
  // viewport before prepending, rather than clicking again and inadvertently loading two pages.
  let releaseOlder;
  const holdOlder = new Promise((resolve) => { releaseOlder = resolve; });
  await page.route('**/history?before=*', async (route) => {
    await holdOlder;
    await route.continue();
  });
  const toTop = async () => {
    await page.mouse.move(195, 400);
    await page.mouse.wheel(0, -200_000);
    await page.waitForFunction(() => document.querySelector('.scroller').scrollTop === 0, undefined, { timeout: 5000 });
  };
  await toTop();
  const older = page.getByRole('button', { name: '加载更早的消息' });
  const before = await view();
  releaseOlder();
  await page.getByText('second question').waitFor({ timeout: 15_000 });
  await page.waitForTimeout(300);
  assert.deepEqual(await questions(), ['first question', 'second question', ...recent.map(name => `${name} question`)]);
  const afterLoad = await view();
  assert.ok(Math.abs(afterLoad.latestTop - before.latestTop) <= 4, `what was on screen stays put (${before.latestTop} → ${afterLoad.latestTop})`);

  // The last page reaches the start of the log, and the button goes away.
  await toTop();
  await page.getByText('first question').waitFor({ timeout: 15_000 });
  assert.deepEqual(await questions(), ['first question', 'second question', ...recent.map(name => `${name} question`)]);
  await page.waitForTimeout(300);
  assert.equal(await older.count(), 0, 'nothing older is left to load');
});

test('copying the conversation takes all of it, not only the turns on screen', async () => {
  // A fresh visit: eight recent turns are loaded; the first two are only in the export.
  await openUi(page, daemon);
  await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: 'long thread' }).first().click();
  await page.getByText('latest answer line 39').waitFor({ timeout: 15_000 });
  assert.deepEqual(await questions(), recent.map(name => `${name} question`));

  await page.locator('.composer textarea').click();
  await page.keyboard.type('/export');
  await page.keyboard.press('Enter');
  await page.getByText('已复制对话').waitFor({ timeout: 15_000 });
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  for (const name of ['first', 'second', 'latest']) {
    assert.ok(copied.includes(`${name} question`) && copied.includes(`${name} answer line 39`), `the ${name} turn is in the copy`);
  }
});

test('opening a snapshot-heavy conversation loads only recent turns in desktop and phone themes', { timeout: 60000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      const response = page.waitForResponse(r => r.url().endsWith('/api/threads/snapshots/history'));
      await page.locator('.thread-title', { hasText: '按需加载对话' }).first().click();
      const data = await (await response).json();
      assert.equal(data.events.filter(e => e.type === 'turn.started').length, 8);
      assert.ok(JSON.stringify(data).length < 40_000, 'the first response excludes old turns and superseded tool snapshots');
      await page.getByText('第 12 轮回答结束', { exact: true }).waitFor();
      assert.deepEqual(await page.locator('.thread .bubble').allTextContents(), Array.from({ length: 8 }, (_, i) => `第 ${i + 5} 轮问题`));
      await page.waitForFunction(() => { const el = document.querySelector('.scroller'); return el.scrollHeight - el.scrollTop - el.clientHeight < 2; });
      if (shots) await page.screenshot({ path: join(shots, `history-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
      for (let n = 0; n < 1; n++) {
        const count = await page.locator('.thread .bubble').count();
        const response = page.waitForResponse(r => r.url().includes('/api/threads/snapshots/history?before='));
        await page.mouse.move(page.viewportSize().width / 2, 200);
        await page.mouse.wheel(0, -100_000);
        await response;
        await page.waitForFunction(count => document.querySelectorAll('.thread .bubble').length > count, count);
      }
      assert.deepEqual(await page.locator('.thread .bubble').allTextContents(), Array.from({ length: 12 }, (_, i) => `第 ${i + 1} 轮问题`));
      assert.equal(await page.getByRole('button', { name: '加载更早的消息' }).count(), 0);
    } finally { await context.close(); }
  }
});
