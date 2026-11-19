// Stream order and pinning: a turn's work reads top-down in the order it happened (like Codex),
// the newest content stays pinned to the bottom while a turn runs, and scrolling up hands the
// position to the user until they jump back.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;
let threadId;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  // Phone-sized, like the screens this behaviour matters on.
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const scrollState = () =>
  page.evaluate(() => {
    const el = document.querySelector('.scroller');
    return {
      dist: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight),
      jump: !!document.querySelector('.jump-btn'),
    };
  });

const openThreadByTitle = async (title) => {
  await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: title }).first().click();
  await page.waitForTimeout(800);
};

test('a slow turn keeps stream order and stays pinned to the bottom', async () => {
  await openUi(page, daemon);
  const made = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
  threadId = made.id;
  await openThreadByTitle('New thread');

  // Start the slow turn with the thread open, so the interleaved events can be watched live.
  await api(daemon, `/api/threads/${threadId}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slowthink one' }) });

  // The turn goes tool → text → tool. Its work block must keep that order, newest at the end:
  // what the agent said on the way sits between the two calls, not hoisted out or left behind.
  await page.locator('.thread .work-body .step', { hasText: 'npm test' }).waitFor({ timeout: 20_000 });
  const order = await page.evaluate(() => {
    const kids = Array.from(document.querySelector('.thread .work-body').children);
    const at = (pick) => kids.findIndex(pick);
    return {
      blocks: document.querySelectorAll('.thread .work').length,
      first: at((k) => k.classList.contains('step') && k.textContent.includes('ls')),
      text: at((k) => k.classList.contains('work-note') && k.textContent.includes('hi there slowthink one')),
      last: at((k) => k.classList.contains('step') && k.textContent.includes('npm test')),
    };
  });
  assert.equal(order.blocks, 1, 'one work block for the turn');
  assert.ok(order.first >= 0 && order.first < order.text && order.text < order.last, `text must sit between the two calls: ${JSON.stringify(order)}`);

  // Through the rest of the run: the view never leaves the bottom, the jump button stays hidden.
  for (let i = 0; i < 6; i++) {
    await page.waitForTimeout(250);
    const state = await scrollState();
    assert.equal(state.dist, 0, `sample ${i}: the view must stay pinned to the bottom`);
    assert.equal(state.jump, false, `sample ${i}: no jump button while pinned`);
  }

  // Done: the block folds to one line with the turn's duration, and the last thing said stands
  // below it as the answer.
  await page.locator('.thread .work-toggle', { hasText: '用时' }).waitFor({ timeout: 20_000 });
  assert.match(await page.locator('.thread .work-toggle').first().innerText(), /2 步/);
  assert.equal(await page.locator('.thread .work-body').count(), 0, 'a finished block is folded');
  await page.locator('.thread .row.assistant', { hasText: 'hi there slowthink one' }).waitFor({ timeout: 5000 });
});

test('scrolling up hands the position to the user; jumping back re-pins for the next turn', async () => {
  // Filler turns so the thread is taller than the screen and can actually be scrolled.
  for (let i = 0; i < 6; i++) {
    await api(daemon, `/api/threads/${threadId}/turns`, { method: 'POST', body: JSON.stringify({ text: `filler ${i}` }) });
    await page.waitForTimeout(350);
  }
  await page.waitForTimeout(600);

  // Wheel over the conversation area, not over the top bar.
  await page.mouse.move(195, 400);
  await page.mouse.wheel(0, -900);
  await page.waitForTimeout(400);
  const up = await scrollState();
  assert.ok(up.dist > 300, `the view stays where the user scrolled (dist=${up.dist})`);
  assert.equal(up.jump, true, 'the jump button appears');

  await page.locator('.jump-btn').click();
  await page.waitForTimeout(900);
  const back = await scrollState();
  assert.ok(back.dist < 60, 'jump returns to the bottom');
  assert.equal(back.jump, false);

  // A second slow turn while pinned: still glued to the bottom as it streams. A gesture that
  // scrolls nothing here (a sideways swipe, as over a code block) must not let go of the bottom.
  await api(daemon, `/api/threads/${threadId}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slowthink two' }) });
  for (let i = 0; i < 9; i++) {
    await page.mouse.wheel(120, 0);
    await page.waitForTimeout(300);
    const state = await scrollState();
    assert.equal(state.dist, 0, `sample ${i}: pinned through the second turn`);
  }
  await page.getByText('hi there slowthink two').waitFor({ timeout: 15_000 });
});
