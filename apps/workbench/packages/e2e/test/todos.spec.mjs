// The plan page: a list of the user's own plans with sub-items, ticked off when done and linked
// to the threads that work on them.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  page = await ctx.newPage();
  const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
  await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'write the release notes' }) });
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const plan = (text) => page.locator('.todos-page .todo:not(.child)', { hasText: text });
const newPlan = () => page.getByLabel('新计划');

test('plans and sub-items are added, each on its own line, and ticked off', async () => {
  await openUi(page, daemon);
  await page.locator('.side-link', { hasText: '计划' }).click();
  await page.locator('.todos-page').waitFor();
  assert.equal(await newPlan().getAttribute('placeholder'), '写下第一条计划，回车添加');
  assert.equal(await page.locator('.composer').count(), 0, 'the page has no composer');
  assert.equal(await page.locator('.side-link[aria-current="page"]', { hasText: '计划' }).count(), 1);
  assert.equal((await page.locator('.topbar-title').innerText()).trim(), '计划', 'the top bar names the page');

  await newPlan().fill('发布 0.2');
  // Enter while an input method is still composing belongs to it: nothing is added yet.
  await newPlan().evaluate((el) => el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true })));
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.todos-page .todo').count(), 0);
  await newPlan().press('Enter');
  await plan('发布 0.2').waitFor();
  assert.equal(await newPlan().inputValue(), '', 'the line is ready for the next plan');

  // Sub-items come several at a time: the field stays open, and typing in it is its own text.
  await plan('发布 0.2').locator('.row-more').first().click();
  await page.getByRole('menuitem', { name: '加子项' }).click();
  const sub = page.getByLabel('新子项');
  await sub.fill('写更新说明');
  assert.equal(await newPlan().inputValue(), '', 'a sub-item being typed does not show up in the new-plan line');
  await sub.press('Enter');
  await sub.fill('打 tag');
  await sub.press('Enter');
  await sub.press('Escape');
  const children = plan('发布 0.2').locator('.todo.child');
  await children.nth(1).waitFor();
  assert.deepEqual(await children.locator('.todo-text').allTextContents(), ['写更新说明', '打 tag']);
  const [a, b] = await Promise.all([children.nth(0).boundingBox(), children.nth(1).boundingBox()]);
  assert.ok(Math.abs(a.x - b.x) < 1 && b.y >= a.y + a.height - 1, 'sub-items stack, one under the other');
  assert.equal(await page.locator('.todos-page').count(), 1, 'Escape closed the field, not the page');

  // A sub-item is struck through where it is; a finished plan moves down to the done group.
  await children.nth(0).locator('.todo-check').click();
  await plan('发布 0.2').locator('.todo.child .todo-text.done', { hasText: '写更新说明' }).waitFor();
  await newPlan().fill('清理重复轮次');
  await newPlan().press('Enter');
  await plan('清理重复轮次').locator('.todo-check').first().click();
  await page.locator('.todos-done-head', { hasText: '已完成 1' }).waitFor();
  assert.equal(await plan('清理重复轮次').count(), 0, 'done plans are folded away');
  await page.locator('.todos-done-head').click();
  await plan('清理重复轮次').locator('.todo-text.done').waitFor();
});

test('a plan links to a thread; the chip opens it', async () => {
  await plan('发布 0.2').locator('.row-more').first().click();
  await page.getByRole('menuitem', { name: '关联线程' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'write the release notes' }).click();
  const chip = plan('发布 0.2').locator('.todo-chip', { hasText: 'write the release notes' });
  await chip.waitFor();

  // It survives a reload: plans live on the daemon, not in the page.
  await page.reload();
  await page.locator('.side-link', { hasText: '计划' }).click();
  await chip.locator('.todo-chip-name').click();
  await page.locator('.todos-page').waitFor({ state: 'detached' });
  assert.match(await page.locator('.topbar-title').innerText(), /write the release notes/);
});

test('deleting a plan asks first, and leaves the page open', async () => {
  await page.locator('.side-link', { hasText: '计划' }).click();
  await newPlan().fill('写错的一条');
  await newPlan().press('Enter');
  await plan('写错的一条').locator('.row-more').first().click();
  await page.getByRole('menuitem', { name: '删除' }).click();
  await page.getByRole('button', { name: '删除', exact: true }).click();
  await plan('写错的一条').waitFor({ state: 'detached' });
  assert.equal(await page.locator('.todos-page').count(), 1);
  await plan('发布 0.2').waitFor();
});

const topLevel = () => page.locator('.todos-list > .todo > .todo-line .todo-text').allTextContents();

test('the page has an address of its own, and the done group stays as it was left', async () => {
  assert.equal(new URL(page.url()).hash, '#/todos');
  await page.reload();
  await page.locator('.todos-page').waitFor();
  // Opened in the first test, and still open after a reload.
  assert.equal(await page.locator('.todos-done-head').getAttribute('aria-expanded'), 'true');
  await page.locator('.todos-done-head').click();
  await page.reload();
  await page.locator('.todos-page').waitFor();
  assert.equal(await page.locator('.todos-done-head').getAttribute('aria-expanded'), 'false');

  // A thread is the plain address again.
  await page.locator('.thread-row', { hasText: 'write the release notes' }).click();
  await page.locator('.todos-page').waitFor({ state: 'detached' });
  assert.equal(new URL(page.url()).hash, '');
  await page.locator('.side-link', { hasText: '计划' }).click();
  await page.locator('.todos-page').waitFor();
});

test('like a checklist in a document: Tab nests, Shift+Tab takes back out, Backspace removes an empty line', async () => {
  for (const text of ['甲', '乙', '丙']) {
    await newPlan().fill(text);
    await newPlan().press('Enter');
    await plan(text).waitFor();
  }
  const editor = page.getByLabel('编辑计划');

  await plan('乙').locator('.todo-text').first().click();
  await editor.press('End');
  await editor.type('二');
  await editor.press('Tab');
  // The row being edited: a textarea has no text content, so it is found from the editor up.
  const editedRow = editor.locator('xpath=ancestor::div[contains(concat(" ", @class, " "), " todo ")][1]');
  await page.waitForFunction(() => document.querySelector('.todo-edit')?.closest('.todo')?.classList.contains('child'));
  assert.equal(await editedRow.locator('xpath=ancestor::div[contains(concat(" ", @class, " "), " todo ")][1]').getAttribute('data-id'),
    await plan('甲').getAttribute('data-id'), 'now a sub-item of the plan above');
  assert.equal(await editor.inputValue(), '乙二', 'the edit carries on in the new place');
  assert.ok(await editor.evaluate((el) => el === document.activeElement), 'the caret stays in it');

  await editor.press('Shift+Tab');
  await page.waitForFunction(() => !document.querySelector('.todo-edit')?.closest('.todo')?.classList.contains('child'));
  assert.ok(await editor.evaluate((el) => el === document.activeElement));
  await editor.press('Escape');
  const order = await topLevel();
  assert.deepEqual(order.slice(order.indexOf('甲')), ['甲', '乙二', '丙'], 'back out, right after the plan it was under');

  await plan('乙二').locator('.todo-text').first().click();
  await editor.fill('');
  await editor.press('Backspace');
  await plan('乙二').waitFor({ state: 'detached' });
  assert.equal(await editor.inputValue(), '甲', 'the caret moved up to the line above');
  await editor.press('Escape');
  const server = await (await api(daemon, '/api/todos')).json();
  assert.equal(server.some((t) => t.text === '乙二'), false);
});

test('a plan is dragged by its handle to a new place among its siblings', async () => {
  const from = plan('丙').locator('.todo-line').first();
  await from.hover();
  const grip = await from.locator('.todo-grip').boundingBox();
  const to = await plan('甲').locator('.todo-line').first().boundingBox();
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + 40, to.y + 3, { steps: 8 });
  await page.locator('.todo-drop').waitFor();
  await page.mouse.up();
  await page.locator('.todo-drop').waitFor({ state: 'detached' });
  const order = await topLevel();
  assert.deepEqual(order.slice(order.indexOf('丙')), ['丙', '甲']);
  // The server has it too.
  await page.reload();
  await page.locator('.todos-page').waitFor();
  const reloaded = await topLevel();
  assert.deepEqual(reloaded.slice(reloaded.indexOf('丙')), ['丙', '甲']);
});

test('on a touch screen there is no handle; the row menu moves a plan up and down', async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const phone = await ctx.newPage();
  await openUi(phone, daemon);
  await phone.goto(phone.url().split('#')[0] + '#/todos');
  await phone.locator('.todos-page').waitFor();
  assert.equal(await phone.locator('.todo-grip').count(), 0);
  const rows = () => phone.locator('.todos-list > .todo > .todo-line .todo-text').allTextContents();
  const before = await rows();
  const last = before.at(-1);
  await phone.locator('.todos-list > .todo', { hasText: last }).locator('.row-more').first().tap();
  assert.equal(await phone.getByRole('menuitem', { name: '下移' }).isDisabled(), true, 'the last one cannot go further down');
  await phone.getByRole('menuitem', { name: '上移' }).tap();
  await phone.waitForFunction((t) => [...document.querySelectorAll('.todos-list > .todo > .todo-line .todo-text')].at(-2)?.textContent === t, last);
  assert.deepEqual(await rows(), [...before.slice(0, -2), last, before.at(-2)]);
  await ctx.close();
});
