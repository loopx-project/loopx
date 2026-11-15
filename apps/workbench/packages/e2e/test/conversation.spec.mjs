// How a turn is shown: thinking as its own collapsible section, tool rows with verbs and
// durations, the work group collapsing once the turn is done.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

test('a turn shows thinking, verb-first tool rows, and folds its work away when done', async () => {
  await openUi(page, daemon);
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('slowthink');
  await page.keyboard.press('Enter');

  // While it runs: the work group is open, thinking is expanded and says so.
  await page.locator('.thinking').waitFor({ timeout: 10_000 });
  assert.match(await page.locator('.thinking-toggle').innerText(), /思考中/);

  // Tool rows appear while the turn is still running, verb first; the one in flight says so.
  await page.locator('.step').first().waitFor({ timeout: 10_000 });
  assert.match(await page.locator('.step-title').first().innerText(), /^运行了\s+ls/);
  const inFlight = page.locator('.step', { hasText: 'npm test' });
  await inFlight.waitFor({ timeout: 10_000 });
  assert.equal((await inFlight.locator('.step-status').innerText()).trim(), '运行中');

  // Once the turn is done the work folds to one line and thinking collapses with a duration.
  await page.getByText('hi there slowthink').waitFor({ timeout: 15_000 });
  await page.locator('.work-toggle', { hasText: '用时' }).first().waitFor({ timeout: 10_000 });
  const toggle = await page.locator('.work-toggle').first().innerText();
  assert.match(toggle, /用时/);
  assert.match(toggle, /2 步/);

  // Expanding the work group again: what was said in between became the answer, so the two calls
  // are one run now, and a run of calls is one line that counts them. Opened, thinking is one
  // collapsed line and the steps are there.
  await page.locator('.work-toggle').first().click();
  const run = page.locator('.work-body .run-head');
  assert.equal((await run.innerText()).trim(), '已运行 2 条命令');
  assert.equal(await page.locator('.step').count(), 0);
  await run.click();
  await page.locator('.step').first().waitFor({ state: 'visible' });
  assert.equal(await page.locator('.run-body .step').count(), 2);
  assert.match(await page.locator('.thinking-toggle').innerText(), /^思考/);
  assert.equal(await page.locator('.thinking-para').count(), 0);

  // Thinking itself expands into its paragraphs.
  await page.locator('.thinking-toggle').first().click();
  await page.locator('.thinking-para').first().waitFor({ state: 'visible' });
  assert.match(await page.locator('.thinking-para').first().innerText(), /hmm/);
});

test('while a turn runs its steps stay the story: no changes card until it ends', async () => {
  await page.locator('.side-nav').getByRole('button', { name: '新线程', exact: true }).click();
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('forkflow');
  await page.keyboard.press('Enter');

  // Mid-run: the edit has landed and the long command is in flight. The steps stay visible and
  // nothing reads as an ending the turn has not had — in particular no card of changed files.
  const longRun = page.locator('.step', { hasText: 'npm test' });
  await longRun.waitFor({ timeout: 10_000 });
  await page.waitForTimeout(300);
  assert.equal(await page.getByText('工作中…', { exact: true }).count(), 0, 'no duplicate status above the composer');
  assert.equal(await page.locator('.changes').count(), 0, 'no changes card while the turn runs');
  assert.equal((await longRun.locator('.step-status').innerText()).trim(), '运行中');

  // Ended: the work folds to its duration, the answer stands after it, and the card appears.
  await page.locator('.work-toggle', { hasText: '用时' }).first().waitFor({ timeout: 15_000 });
  await page.getByText('now the long run').waitFor({ timeout: 5_000 });
  const card = page.locator('.changes');
  await card.waitFor({ timeout: 5_000 });
  assert.match(await card.locator('.changes-head').innerText(), /已更改 1 个文件/);
  assert.equal(await card.locator('.changes-head').getAttribute('aria-expanded'), 'false');
  assert.equal(await card.locator('.changes-file').count(), 0);
  await card.locator('.changes-head').click();
  assert.match(await card.innerText(), /demo\.ts/);
});

test('a run of calls folds into one line that counts them, and opens to the calls', async () => {
  await page.locator('.side-nav').getByRole('button', { name: '新线程', exact: true }).click();
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('manysteps');
  await page.keyboard.press('Enter');

  // Mid-run: the four calls before the remark are one line. After the remark one call is over and
  // one is in flight; that is not worth a count yet, so both show as themselves.
  const inFlight = page.locator('.step', { hasText: 'npm test' });
  await inFlight.waitFor({ timeout: 10_000 });
  const runs = page.locator('.work-body .run-head');
  assert.equal(await runs.count(), 1);
  assert.equal((await runs.first().innerText()).trim(), '已浏览 2 个文件，搜索 1 次，运行 1 条命令');
  assert.equal(await page.locator('.step', { hasText: 'src/a.ts' }).count(), 0, 'folded: its calls are not on screen');
  assert.equal((await inFlight.locator('.step-status').innerText()).trim(), '运行中');
  assert.equal(await page.locator('.work-body > .step', { hasText: 'npm run lint' }).count(), 1);

  // The line opens to its calls, in the order they were made, and closes again.
  await runs.first().click();
  const opened = await page.locator('.run-body .step-title').allInnerTexts();
  assert.equal(opened.length, 4);
  assert.match(opened[0], /^读取了\s+src\/a\.ts/);
  assert.match(opened[3], /^运行了\s+ls src/);
  await runs.first().click();
  assert.equal(await page.locator('.run-body').count(), 0);

  // Done: the turn's work folds to its one line as before. Opened, both runs are counts now.
  await page.locator('.row.assistant', { hasText: 'all good' }).waitFor({ timeout: 15_000 });
  const toggle = page.locator('.work-toggle', { hasText: '用时' });
  await toggle.waitFor({ timeout: 10_000 });
  assert.match(await toggle.innerText(), /6 步/);
  assert.equal(await page.locator('.work-body').count(), 0);
  await toggle.click();
  assert.deepEqual(
    (await runs.allInnerTexts()).map((t) => t.trim()),
    ['已浏览 2 个文件，搜索 1 次，运行 1 条命令', '已运行 2 条命令'],
  );
  assert.equal(await page.locator('.work-body .step').count(), 0, 'nothing is laid out until a line is opened');
});
