// The phone layout: the thread list is a page of its own (Codex's way). The arrow in the top bar
// slides the conversation off to show it; picking a thread or a swipe brings it back. Rows have
// no "more" button: a long press opens their menu.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const WIDTH = 390;

let daemon;
let browser;
let page;
let cdp;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: WIDTH, height: 844 }, hasTouch: true, isMobile: true });
  page = await ctx.newPage();
  cdp = await ctx.newCDPSession(page);

  // Two threads with an answer each, made over the API.
  for (const text of ['first thread', 'second thread']) {
    const made = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
    await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
    for (let i = 0; i < 100; i++) {
      const cur = await (await api(daemon, `/api/threads/${made.id}`)).json();
      if (cur.status !== 'running') break;
      await new Promise((r) => setTimeout(r, 100));
    }
  }
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const listOpen = async () => (await page.locator('.app.drawer-open').count()) === 1;
const settle = () => page.waitForTimeout(450); // the slide
const left = (selector) => page.locator(selector).first().evaluate((el) => Math.round(el.getBoundingClientRect().left));

async function touch(from, to, holdMs = 0) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [from] });
  if (holdMs) await page.waitForTimeout(holdMs);
  if (to) {
    for (let i = 1; i <= 6; i++) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x + ((to.x - from.x) * i) / 6, y: from.y + ((to.y - from.y) * i) / 6 }] });
    }
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await settle();
}

test('the list is a page of its own; the arrow in the top bar goes to it, a swipe comes back', async () => {
  await openUi(page, daemon);
  assert.equal(await listOpen(), false);
  assert.equal(await left('.main'), 0);
  assert.equal(await page.locator('.sidebar').isVisible(), false, 'the list is not on screen until it is opened');

  await page.locator('.menu-btn').click();
  await settle();
  assert.equal(await listOpen(), true);
  // The whole screen is the list: nothing of the conversation stays in view.
  assert.ok((await left('.main')) >= WIDTH, 'the conversation has slid off');
  assert.equal(await page.locator('.main').isVisible(), false);
  await page.locator('.sidebar .brand').waitFor();
  await page.locator('.sidebar .thread-title', { hasText: 'first thread' }).waitFor();
  const box = await page.locator('.sidebar-inner').boundingBox();
  assert.ok(box.x === 0 && Math.abs(box.width - WIDTH) < 2, `the list fills the width (${box.width})`);

  await touch({ x: 250, y: 400 }, { x: 110, y: 400 });
  assert.equal(await listOpen(), false);
  assert.equal(await left('.main'), 0);
});

test('the list has its pages as rows, and search behind a button', async () => {
  await page.locator('.menu-btn').click();
  await settle();
  const list = page.locator('.sidebar');
  for (const name of ['计划', '新线程', '搜索线程', '配置']) {
    assert.equal(await list.getByRole('button', { name, exact: true }).isVisible(), true, name);
  }
  // What is not an everyday thing sits behind the settings button: the archive, importing, signing out.
  assert.equal(await list.getByRole('button', { name: '已归档', exact: true }).count(), 0);
  await list.getByRole('button', { name: '配置', exact: true }).tap();
  for (const name of ['已归档', '导入历史', '退出']) {
    await page.locator('.settings-nav').getByRole('button', { name, exact: true }).waitFor({ timeout: 5_000 });
  }
  // Signing out costs the saved token, so it asks first; backing out leaves everything as it was.
  await page.locator('.settings-nav').getByRole('button', { name: '退出', exact: true }).tap();
  await page.getByText('退出登录？').waitFor({ timeout: 5_000 });
  await page.getByRole('button', { name: '取消' }).tap();
  await page.getByText('退出登录？').waitFor({ state: 'detached', timeout: 5_000 });
  // Settings were opened from the list, so going back lands on the list again.
  await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).tap();
  await settle();
  assert.equal(await listOpen(), true);
  assert.equal(await list.locator('.thread-row .row-more').first().isVisible(), false, 'rows carry no button on a touch screen');

  await list.getByRole('button', { name: '搜索线程' }).click();
  await page.keyboard.type('second');
  await list.locator('.thread-title', { hasText: 'second thread' }).waitFor();
  assert.equal(await list.locator('.thread-title', { hasText: 'first thread' }).count(), 0);
  await list.getByRole('button', { name: '关闭搜索' }).click();
  await list.locator('.thread-title', { hasText: 'first thread' }).waitFor();
});

test('a long press on a row opens its menu, and is not a tap', async () => {
  const row = page.locator('.sidebar .thread-row', { hasText: 'first thread' });
  const box = await row.boundingBox();
  await touch({ x: box.x + 60, y: box.y + box.height / 2 }, null, 700);
  await page.getByRole('menuitem', { name: '置顶' }).waitFor({ timeout: 5_000 });
  assert.equal(await listOpen(), true, 'the list is still open: the press did not open the thread');
  assert.equal((await page.locator('.topbar-title').textContent()).trim(), '新线程');
  await page.keyboard.press('Escape');
  await page.getByRole('menuitem', { name: '置顶' }).waitFor({ state: 'detached', timeout: 5_000 });
});

test('a tap on a row opens the thread; the top bar says which agent and directory', async () => {
  await page.locator('.sidebar .thread-title', { hasText: 'first thread' }).tap();
  await page.getByText('hi there first thread').waitFor({ timeout: 10_000 });
  await settle();
  assert.equal(await listOpen(), false);
  assert.match(await page.locator('.topbar-title').innerText(), /first thread/);
  await page.getByRole('button', { name: 'first thread', exact: true }).click();
  assert.match(await page.locator('.place-panel').innerText(), /pi/);
  await page.keyboard.press('Escape');
  // So the composer does not say it again.
  assert.equal(await page.locator('.composer-context').count(), 0);
  // Under the answer: copy, fork, and when the turn ended.
  const actions = page.locator('.message-actions');
  assert.equal(await actions.getByRole('button', { name: '复制回答' }).isVisible(), true);
  assert.equal(await actions.getByRole('button', { name: '分叉' }).isVisible(), true);
  assert.match(await actions.locator('.message-time').innerText(), /^\d\d:\d\d$/);
});

test('swipes move the list: in from the left edge opens it, leftwards closes it', async () => {
  // A sideways swipe in the middle of the conversation is not for the list (it scrolls code and tables).
  await touch({ x: 200, y: 400 }, { x: 330, y: 400 });
  assert.equal(await listOpen(), false);

  await touch({ x: 6, y: 400 }, { x: 140, y: 400 });
  assert.equal(await listOpen(), true);

  // Scrolling the list up and down does not close it.
  await touch({ x: 150, y: 500 }, { x: 140, y: 300 });
  assert.equal(await listOpen(), true);

  await touch({ x: 250, y: 400 }, { x: 110, y: 400 });
  assert.equal(await listOpen(), false);
});

test('the new-thread button in the top bar starts a draft', async () => {
  await page.locator('.topbar-new').tap();
  assert.equal((await page.locator('.topbar-title').innerText()).trim(), '新线程');
  assert.equal(await page.locator('.topbar-sub').count(), 0);
  // A draft still picks its agent and directory in the composer.
  assert.equal(await page.locator('.composer-context').count(), 1);
});
