// Quoting a selected passage into the composer, and asking about one in the side chat.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, api } from '../harness.mjs';

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

/** Picks out the first characters of the thread's first answer, the way a pointer drag would. */
async function selectStartOfAnswer(count) {
  await page.evaluate((n) => {
    const el = document.querySelector('.main .thread .markdown');
    const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode();
    const sel = window.getSelection();
    sel.removeAllRanges();
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, n);
    sel.addRange(range);
  }, count);
}

test('a selection in the answer becomes a quote in the composer and in the sent message', async () => {
  await openUi(page, daemon);
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('quoteselect');
  await page.keyboard.press('Enter');
  await page.getByText('hi there quoteselect').waitFor({ timeout: 15_000 });

  await selectStartOfAnswer(7);
  await page.locator('.selection-menu').waitFor({ timeout: 5_000 });
  assert.equal(await page.locator('.selection-menu').innerText(), '注释\n拷贝\n在侧边栏提问');

  // Quoting closes the menu and parks the passage above what is typed.
  await page.locator('.selection-menu button', { hasText: '注释' }).click();
  await page.locator('.selection-add').click();
  await page.locator('.composer .note-quote').waitFor({ timeout: 5_000 });
  assert.equal(await page.locator('.composer .note-quote').innerText(), 'hi ther');
  assert.equal(await page.locator('.selection-menu').count(), 0);

  await page.locator('.composer textarea').click();
  await page.keyboard.type('why so');
  await page.keyboard.press('Enter');
  await page.locator('.row.user .notes-chip').last().click();
  await page.locator('.row.user .note-quote').last().waitFor({ timeout: 5_000 });
  assert.equal(await page.locator('.row.user .note-quote').last().innerText(), 'hi ther');
  assert.equal(await page.locator('.row.user .bubble').last().innerText(), 'why so');
  // Wait for the turn to finish: the side chat in the next test forks this thread, and a fork
  // has to wait for a running turn.
  await page.waitForFunction(
    () => [...document.querySelectorAll('.main .thread .markdown')].some((m) => m.textContent?.includes('hi there [注释 1]')),
    null,
    { timeout: 15_000 },
  );
});

test('a note number stays out of the glass bars the conversation scrolls under', async () => {
  // Root navigation starts a new thread; explicitly reopen the source created above.
  await openUi(page, daemon);
  await page.locator('.sidebar .thread-title').first().click();
  await page.getByText('hi there quoteselect').first().waitFor({ timeout: 15_000 });
  await selectStartOfAnswer(7);
  await page.locator('.selection-menu button', { hasText: '注释' }).click();
  await page.locator('.selection-add').click();
  await page.locator('.note-mark').waitFor({ timeout: 5_000 });

  // Give the thread room to scroll, then slide the passage up under the top bar.
  await page.evaluate(() => {
    const scroller = document.querySelector('.main > .scroller');
    const spacer = document.createElement('div');
    spacer.style.height = '3000px';
    scroller.appendChild(spacer);
  });
  const topbar = await page.locator('.main > .topbar').boundingBox();
  await page.evaluate((barBottom) => {
    const scroller = document.querySelector('.main > .scroller');
    const mark = document.querySelector('.note-mark').getBoundingClientRect();
    scroller.scrollTop += mark.top + mark.height / 2 - (barBottom - 10);
  }, topbar.y + topbar.height);
  await page.waitForFunction(() => !document.querySelector('.note-mark'), null, { timeout: 5_000 });

  // And back in the readable area it returns.
  await page.evaluate(() => { document.querySelector('.main > .scroller').scrollTop = 0; });
  await page.locator('.note-mark').waitFor({ timeout: 5_000 });
});

test('asking in the side chat opens a thread of its own', async () => {
  const originalAnswer = await page.locator('.main .thread .markdown').first().innerText();
  await selectStartOfAnswer(7);
  await page.locator('.selection-menu').waitFor({ timeout: 5_000 });
  await page.locator('.selection-menu button', { hasText: '在侧边栏提问' }).click();
  await page.locator('.side-chat').waitFor({ timeout: 5_000 });

  // The passage is what the questions are about; nothing exists on the server yet.
  assert.equal(await page.locator('.side-chat-passage').innerText(), 'hi ther');
  assert.equal(await page.locator('.sidebar .thread-row').count(), 1);

  // The side chat starts on the source model but can choose its own before the first question.
  await api(daemon, '/api/draft-model-preferences', {
    method: 'PATCH',
    body: JSON.stringify({ agent: 'pi', model: 'fake/think', effort: 'low' }),
  });
  const sideModel = page.locator('.side-chat .model-control-trigger');
  await sideModel.filter({ hasText: 'Think' }).waitFor({ timeout: 10_000 });
  await sideModel.click();
  await page.locator('.model-control-panel .effort-model').click();
  await page.getByRole('menuitemradio', { name: /^Plain\b/ }).click();
  await page.keyboard.press('Escape');

  await page.locator('.side-chat textarea').click();
  await page.keyboard.type('go on');
  await page.keyboard.press('Enter');

  // The message carries the quote, and the side chat forks the conversation it came from: the
  // agent answers with the full history as context, while the panel shows only what is asked
  // here — one user turn, none of the inherited ones.
  await page.locator('.side-chat .row.user .notes-chip').click();
  await page.locator('.side-chat .row.user .note-quote').waitFor({ timeout: 5_000 });
  assert.equal(await page.locator('.side-chat .row.user .note-quote').innerText(), 'hi ther');
  assert.equal(await page.locator('.side-chat .row.user .bubble').last().innerText(), 'go on');
  assert.equal(await page.locator('.side-chat .row.user').count(), 1, 'inherited turns stay with the agent, not on the panel');
  // The side chat nests under its parent topic drawer instead of cluttering the root list.
  const drawerToggle = page.locator('.sidebar .handover-toggle').first();
  await drawerToggle.waitFor({ timeout: 10_000 });
  assert.equal(await page.locator('.sidebar .thread-row').count(), 1, 'root sidebar keeps only the parent topic');
  const sideThread = (await (await api(daemon, '/api/threads')).json()).find(thread => thread.title === '追问：hi ther');
  assert.equal(sideThread.forkKind, 'ask');
  assert.ok(sideThread.forkGroup, 'side chat is grouped under parent topic');
  assert.equal(sideThread.model, 'fake/plain');

  // After the thread exists, changing the model applies to the same thread rather than a new one,
  // and switching back to a model restores its remembered thinking effort.
  const patched = page.waitForResponse(response => response.request().method() === 'PATCH'
    && new URL(response.url()).pathname === `/api/threads/${sideThread.id}`);
  await sideModel.click();
  await page.getByRole('menuitemradio', { name: /^Think\b/ }).click();
  const patchedThread = await (await patched).json();
  assert.equal(patchedThread.model, 'fake/think');
  assert.equal(patchedThread.effort, 'low');
  assert.equal((await (await api(daemon, '/api/threads')).json()).filter(thread => thread.title === '追问：hi ther').length, 1);
  await page.locator('.side-chat-title').click();
  await page.locator('.model-control-panel').waitFor({ state: 'detached' });

  // The conversation it came from is untouched underneath.
  assert.equal(await page.locator('.main .thread .markdown').first().innerText(), originalAnswer);

  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.side-chat').count(), 0);

  // Expanding the parent topic drawer reveals the nested side chat with its 12px ask icon.
  if ((await drawerToggle.getAttribute('aria-expanded')) !== 'true') await drawerToggle.click();
  const nestedAsk = page.locator('.sidebar .handover-member', { hasText: 'hi ther' }).first();
  await nestedAsk.waitFor({ timeout: 5_000 });
  await nestedAsk.locator('.fork-kind-ico[aria-label="追问"]').waitFor({ timeout: 5_000 });
  assert.match(await nestedAsk.getAttribute('title'), /追问：hi ther/);
});

test('a picture attached to the composer can be looked at full size before sending', async () => {
  await openUi(page, daemon);
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  // A one-pixel PNG, the same the fake agent keeps in its canned history.
  const dot = join(daemon.dataDir, 'dot.png');
  writeFileSync(dot, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64'));
  await page.setInputFiles('.composer input[type=file]', dot);
  await page.locator('.attachment img').waitFor({ timeout: 5_000 });

  // Tapping the thumbnail opens the picture over everything; closing it changes nothing.
  await page.locator('.attachment-view').click();
  await page.locator('.lightbox').waitFor({ timeout: 3_000 });
  assert.match(await page.locator('.lightbox img').getAttribute('src'), /^blob:/);
  await page.keyboard.press('Escape');
  await page.locator('.lightbox').waitFor({ state: 'detached', timeout: 3_000 });
  assert.equal(await page.locator('.attachment img').count(), 1);

  // Removing the picture still works without opening the preview.
  await page.locator('.attachment-remove').click();
  await page.locator('.attachment img').waitFor({ state: 'detached', timeout: 3_000 });
});

for (const mobile of [false, true]) for (const theme of ['light', 'dark']) test(`quoted follow-up preserves model choices: ${mobile ? 'phone' : 'desktop'} ${theme}`, { timeout: 45000 }, async () => {
  const post = async (path, body, method = 'POST') => {
    const response = await api(daemon, path, { method, body: JSON.stringify(body) });
    assert.ok(response.ok, await response.clone().text()); return response.json();
  };
  const source = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, model: 'fake/think', effort: 'high', title: `Source ${mobile} ${theme}` });
  await post(`/api/threads/${source.id}/turns`, { text: 'quote model choices' });
  for (let i = 0; i < 100; i++) {
    if ((await (await api(daemon, `/api/threads/${source.id}`)).json()).status === 'idle') break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  await post('/api/draft-model-preferences', { agent: 'pi', model: 'fake/think', effort: 'low' }, 'PATCH');
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
  try {
    const p = await context.newPage();
    const errors = []; p.on('pageerror', error => errors.push(error.message));
    await openUi(p, daemon); await p.evaluate(id => { location.hash = `#/thread/${id}`; }, source.id);
    await p.locator('.main .thread .markdown').first().waitFor();
    await p.evaluate(() => {
      const node = document.createTreeWalker(document.querySelector('.main .thread .markdown'), NodeFilter.SHOW_TEXT).nextNode();
      const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, 7);
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    });
    await p.locator('.selection-menu button', { hasText: '在侧边栏提问' }).click();
    const side = p.locator('.side-chat'), picker = side.locator('.model-control-trigger');
    await picker.waitFor(); await picker.click();
    await p.locator('.model-control-panel .effort-model').click();
    await p.getByRole('menuitemradio', { name: /^Plain\b/ }).click(); await p.keyboard.press('Escape');
    const fork = p.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/threads/${source.id}/fork`);
    await side.locator('textarea').fill('Follow up with this model'); await side.locator('.send-btn:not(.stop)').click();
    const child = await (await fork).json();
    await side.locator('.row.user .bubble').last().waitFor();
    assert.equal((await (await api(daemon, `/api/threads/${child.id}`)).json()).model, 'fake/plain');
    const update = p.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname === `/api/threads/${child.id}`);
    await picker.click(); await p.getByRole('menuitemradio', { name: /^Think\b/ }).click();
    const changed = await (await update).json();
    assert.equal(changed.id, child.id); assert.equal(changed.model, 'fake/think'); assert.equal(changed.effort, 'low');
    const original = await (await api(daemon, `/api/threads/${source.id}`)).json();
    assert.equal(original.model, 'fake/think'); assert.equal(original.effort, 'high');
    await side.locator('.side-chat-title').click();
    if (process.env.LOOCHAT_E2E_SHOTS_DIR) {
      const { mkdirSync } = await import('node:fs'); mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
      await p.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `side-quote-${mobile ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled' });
    }
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});
