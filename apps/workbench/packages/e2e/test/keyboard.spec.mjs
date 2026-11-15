// A phone keyboard covers the bottom of the page, and the browser pans what is left of the screen
// (the visual viewport) down the page to the focused field. The app must be drawn where that is.
// A real keyboard cannot be raised here, so the visual viewport is given the numbers a phone
// reports, and the layout is checked against them.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, launchBrowser, api } from '../harness.mjs';

let daemon;
let browser;
let page;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

/** Makes the visual viewport report a keyboard: `height` of screen left, panned `top` down the page. */
const keyboard = (height, top) =>
  page.evaluate(
    ([h, t]) => {
      const vv = window.visualViewport;
      for (const [name, value] of Object.entries({ height: h, offsetTop: t, pageTop: t, scale: 1 })) {
        Object.defineProperty(vv, name, { get: () => value, configurable: true });
      }
      vv.dispatchEvent(new Event('resize'));
      vv.dispatchEvent(new Event('scroll'));
    },
    [height, top],
  );

const box = (selector) => page.locator(selector).first().evaluate((el) => {
  const r = el.getBoundingClientRect();
  return { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height) };
});

test('with the keyboard up, the app fills what is left of the screen, wherever the browser panned to', async () => {
  await openUi(page, daemon);
  assert.deepEqual(await box('.app'), { top: 0, bottom: 844, height: 844 });

  // Keyboard up: 500px of screen left, and Safari has panned 344px down to reach the field.
  await keyboard(500, 344);
  assert.deepEqual(await box('.app'), { top: 344, bottom: 844, height: 500 }, 'the app is the visible part, not the top of the page');
  const composer = await box('.composer-box');
  assert.ok(composer.bottom <= 844 && composer.bottom > 844 - 60, `the composer sits just above the keyboard (${composer.bottom})`);
  assert.ok((await box('.topbar')).top === 344, 'the top bar is at the top of what is visible');
  const safeBottom = () => page.evaluate(() => document.documentElement.style.getPropertyValue('--safe-bottom'));
  assert.equal(await safeBottom(), '0px', 'the home-indicator inset is under the keyboard, so no band is kept for it');

  // The plan page lives in the same visible part, and its line for a new plan can be typed on.
  await page.locator('.menu-btn').click();
  await page.locator('.sidebar').getByRole('button', { name: '计划' }).click();
  await page.waitForTimeout(500); // the list slides away
  const field = await box('.todos-page .todo-add');
  assert.ok(field.top >= 344 && field.bottom <= 844, `the new-plan line is inside the visible part (${field.top}–${field.bottom})`);

  // Keyboard down again: back to the whole screen.
  await keyboard(844, 0);
  assert.deepEqual(await box('.app'), { top: 0, bottom: 844, height: 844 });
  assert.equal(await safeBottom(), '', 'with the keyboard down the inset is kept clear again');
});

test('a comment on a passage is written just above the keyboard, with the passage shown', async () => {
  await openUi(page, daemon);
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').fill('notekeyboard');
  await page.locator('.composer .send-btn, .composer button[aria-label="发送"]').first().tap();
  await page.getByText('hi there notekeyboard').waitFor({ timeout: 15_000 });
  await page.locator('.composer textarea').blur();

  await page.evaluate(() => {
    const el = document.querySelector('.main .thread .markdown');
    const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode();
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, 8);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  });
  // The system's own menu (copy, look up, translate) sits beside the passage, above or below it;
  // this one is docked at the bottom in the composer's place, clear of it wherever it goes.
  await page.locator('.selection-menu').waitFor({ timeout: 5_000 });
  const menu = await box('.selection-menu');
  assert.ok(menu.bottom <= 844 && menu.bottom > 844 - 60, `the menu is at the bottom of the screen (${menu.top}–${menu.bottom})`);
  const passage = await page.evaluate(() => getSelection().getRangeAt(0).getBoundingClientRect().bottom);
  assert.ok(menu.top > passage + 40, 'the menu is well away from the passage');
  assert.equal(await page.locator('.composer-box').evaluate((el) => getComputedStyle(el).visibility), 'hidden', 'the composer makes way');
  await page.locator('.selection-menu button', { hasText: '注释' }).tap();
  await page.locator('.selection-comment').waitFor({ timeout: 5_000 });

  // The field takes the focus, the keyboard comes up and Safari pans down the page.
  await keyboard(500, 344);
  const field = await box('.selection-menu.commenting');
  assert.ok(field.top >= 344 && field.bottom <= 844 && field.bottom > 844 - 80, `the field sits just above the keyboard (${field.top}–${field.bottom})`);
  assert.equal(await page.locator('.selection-quote').count(), 0);

  await page.locator('.selection-comment').fill('为什么');

  // Dragging the thread to reread the passage keeps what is being written.
  const thread = await box('.main .thread');
  const x = 200;
  const y = Math.round((Math.max(thread.top, 344) + 844) / 2);
  const cdp = await page.context().newCDPSession(page);
  const touch = (type, points) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points });
  await touch('touchStart', [{ x, y }]);
  for (let i = 1; i <= 8; i++) await touch('touchMove', [{ x, y: y - i * 15 }]);
  await touch('touchEnd', []);
  await page.waitForTimeout(300);
  assert.equal(await page.locator('.selection-comment').inputValue(), '为什么', 'a drag does not drop the comment');

  await page.locator('.selection-add').tap();
  await page.locator('.composer .note-strip, .composer .notes').first().waitFor({ timeout: 5_000 });
  assert.equal(await page.locator('.selection-menu').count(), 0);

  // A tap elsewhere still drops a comment being written.
  await page.evaluate(() => {
    const el = document.querySelector('.main .thread .markdown');
    const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode();
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, 5);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  await page.locator('.selection-menu button', { hasText: '注释' }).tap();
  await page.locator('.selection-comment').waitFor({ timeout: 5_000 });
  await touch('touchStart', [{ x, y }]);
  await touch('touchEnd', []);
  await page.locator('.selection-menu').waitFor({ state: 'detached', timeout: 5_000 });
  await cdp.detach();
  await keyboard(844, 0);
});

test('with the keyboard up, the question sheet sits just above it, where the composer was', async () => {
  // A thread waiting on a question: the sheet takes the composer's place at the bottom.
  const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', title: '键盘提问', cwd: daemon.workdir, workMode: 'plan', permissionMode: 'ask' }) });
  assert.equal(created.status, 201);
  const thread = await created.json();
  await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'plan-question' }) });
  await openUi(page, daemon);
  await page.locator('.menu-btn').click();
  await page.locator('.thread-item', { hasText: '键盘提问' }).click();
  await page.getByRole('dialog', { name: '智能体提问' }).waitFor({ timeout: 10_000 });
  assert.equal(await page.locator('.composer-box').evaluate((el) => getComputedStyle(el).visibility), 'hidden', 'the composer makes way');

  // The keyboard comes up over the lower half and Safari pans down the page.
  await keyboard(500, 344);
  const sheet = await box('.action-sheet-card');
  assert.ok(sheet.top >= 344 && sheet.bottom <= 844, `the sheet is on the visible part (${sheet.top}–${sheet.bottom})`);
  assert.ok(sheet.bottom > 844 - 80, `the sheet sits just above the keyboard (${sheet.bottom})`);
  // What is there to pick is on screen too.
  const options = await box('.sheet-options');
  assert.ok(options.top >= 344 && options.bottom <= 844, `the options are on the visible part (${options.top}–${options.bottom})`);
  await keyboard(844, 0);
  await page.getByRole('button', { name: '跳过', exact: true }).click();
  await page.locator('.action-sheet').waitFor({ state: 'detached' });
});

test('with the keyboard up, the terminal and its input line stay on the visible part', async () => {
  await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '键盘终端' }) });
  await openUi(page, daemon);
  await page.locator('.menu-btn').click();
  await page.locator('.thread-item', { hasText: '键盘终端' }).click();
  await page.getByLabel('这个线程的更多操作').click();
  await page.getByRole('menuitem', { name: '打开终端' }).click();
  await page.locator('.term-panel').waitFor();

  // The field at the bottom takes the focus; Safari pans down to it.
  await keyboard(500, 344);
  assert.deepEqual(await box('.term-panel'), { top: 344, bottom: 844, height: 500 }, 'the terminal is the visible part, not pushed below it');
  const line = await box('.term-line');
  assert.ok(line.top >= 344 && line.bottom <= 844, `the input line is on screen (${line.top}–${line.bottom})`);
  await keyboard(844, 0);
  await page.getByLabel('关闭').click();
});

test('with the keyboard up, the side chat on a phone stays on the visible part', async () => {
  await page.locator('.composer textarea').fill('sidekeyboard');
  await page.locator('.composer .send-btn').first().tap();
  await page.getByText('hi there sidekeyboard').waitFor({ timeout: 15_000 });
  await page.locator('.composer textarea').blur();
  await page.evaluate(() => {
    const el = [...document.querySelectorAll('.main .thread .markdown')].pop();
    const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode();
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, 8);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  await page.locator('.selection-menu button', { hasText: '在侧边栏提问' }).tap();
  await page.locator('.side-chat').waitFor();

  await keyboard(500, 344);
  const panel = await box('.side-chat');
  assert.ok(panel.top >= 344 && panel.bottom <= 844, `the side chat is the visible part (${panel.top}–${panel.bottom})`);
  const field = await box('.side-chat textarea');
  assert.ok(field.top >= 344 && field.bottom <= 844, `its field is on screen (${field.top}–${field.bottom})`);
  await keyboard(844, 0);
});
