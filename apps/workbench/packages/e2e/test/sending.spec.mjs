// A message that is on its way when the page is reloaded. The daemon may already have it; the box,
// restored from the device, must not offer it again as if it had never been sent.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

/** A thread with one answer, open in a fresh page, with a note and a picture waiting in the box. */
async function boxWithNoteAndPicture(question) {
  const post = (path, body) => api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
  const thread = await (await post('/api/threads', { agent: 'pi', cwd: daemon.workdir })).json();
  await post(`/api/threads/${thread.id}/turns`, { text: question });
  for (let i = 0; i < 100; i++) {
    if ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status !== 'running') break;
    await new Promise((r) => setTimeout(r, 100));
  }
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  await openUi(page, daemon);
  await page.locator('.thread-item', { hasText: question }).click();
  await page.locator('.main .thread .row.assistant .markdown').waitFor();

  await page.evaluate(() => {
    const node = document.createTreeWalker(document.querySelector('.main .thread .row.assistant .markdown'), NodeFilter.SHOW_TEXT).nextNode();
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, 8);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  });
  await page.locator('.selection-menu button', { hasText: '注释' }).click();
  await page.locator('.selection-comment').fill('为什么');
  await page.locator('.selection-add').click();
  const dot = join(daemon.dataDir, 'dot.png');
  writeFileSync(dot, Buffer.from(PNG, 'base64'));
  await page.setInputFiles('.composer input[type=file]', dot);
  await page.locator('.composer .attachment img').waitFor();
  await page.locator('.composer .note-strip').waitFor();
  await page.waitForTimeout(600); // the draft is written down once typing pauses
  return { page, thread, reopen: () => page.locator('.thread-item', { hasText: question }).click() };
}

const box = (page) =>
  page.evaluate(() => ({
    notes: document.querySelectorAll('.composer .note-strip').length,
    pictures: document.querySelectorAll('.composer .attachment img').length,
    stored: Object.keys(JSON.parse(localStorage.getItem('calli.drafts') ?? '{}')).length,
  }));

test('reloaded while the answer to a send was still on its way back: the message is not a draft again', async () => {
  const { page, reopen } = await boxWithNoteAndPicture('taken at once');
  // The daemon takes the message at once and its answer is slow to arrive, as with a picture on a slow line.
  await page.route('**/turns', async (route) => {
    const response = await route.fetch();
    await new Promise((r) => setTimeout(r, 4000));
    await route.fulfill({ response }).catch(() => undefined);
  });
  await page.locator('.composer .send-btn:not(.stop)').click();
  // A send that takes a while says so.
  await page.getByRole('button', { name: '正在发送' }).waitFor({ timeout: 3000 });
  await page.locator('.row.user .sent-image, .row.user .sent-images > *').first().waitFor({ timeout: 5000 });

  await page.reload();
  await reopen();
  await page.locator('.row.user').nth(1).waitFor({ timeout: 10_000 });
  await page.waitForTimeout(800);
  assert.deepEqual(await box(page), { notes: 0, pictures: 0, stored: 0 }, 'the thread shows the message, so the box lets go of it');
  assert.equal(await page.locator('.row.user').count(), 2, 'sent once');
  await page.close();
});

test('reloaded before the daemon got the message: the words and notes are still in the box', async () => {
  const { page, reopen } = await boxWithNoteAndPicture('never arrives');
  await page.route('**/turns', async (route) => {
    await new Promise((r) => setTimeout(r, 4000));
    await route.abort().catch(() => undefined);
  });
  await page.locator('.composer .send-btn:not(.stop)').click();
  await page.getByRole('button', { name: '正在发送' }).waitFor({ timeout: 3000 });

  await page.reload();
  await reopen();
  await page.locator('.composer .note-strip').waitFor({ timeout: 10_000 });
  await page.waitForTimeout(800);
  // Pictures only last as long as the page; the rest is there to send again.
  assert.deepEqual(await box(page), { notes: 1, pictures: 0, stored: 1 });
  assert.equal(await page.locator('.row.user').count(), 1);
  await page.close();
});

test('a send that comes back is forgotten on the device at once', async () => {
  const { page } = await boxWithNoteAndPicture('plain send');
  await page.locator('.composer .send-btn:not(.stop)').click();
  await page.locator('.row.user').nth(1).waitFor({ timeout: 10_000 });
  // Not after the next pause in typing: a reload right now must find nothing.
  assert.deepEqual(await box(page), { notes: 0, pictures: 0, stored: 0 });
  await page.close();
});
