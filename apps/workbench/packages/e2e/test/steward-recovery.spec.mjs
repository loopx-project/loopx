import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

// The steward talks to the LoopX core through the hub's proxy; the page's own requests to it are
// answered here, so each failure can be placed exactly where it happens in a real outage.
let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });

const json = (route, status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const sse = (route, frames) => route.fulfill({ status: 200, contentType: 'text/event-stream',
  body: frames.map(([id, kind, payload]) => `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify({ payload })}\n\n`).join('') });

test('after a dropped stream or a lost history read, the steward still sends the next message', async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  try {
    const page = await context.newPage();
    const asked = [];
    let historyReads = 0;
    await page.route('**/api/loopx/status.json*', (route) => json(route, 200, { run_history: { goals: [] }, served_at: new Date().toISOString() }));
    await page.route('**/api/loopx/api/chat/sessions', (route) => json(route, 200, { session_id: 's1' }));
    await page.route('**/api/loopx/api/chat/sessions/s1', (route) => {
      historyReads++;
      // The first read opens the conversation; the one after the second turn fails; then the core is back.
      if (historyReads === 2) return json(route, 500, { error: 'history_unavailable' });
      return json(route, 200, { messages: historyReads === 1 ? [] : asked.flatMap((text, i) => [
        { message_id: `u${i}`, role: 'user', text }, ...(i === 2 ? [{ message_id: 'a2', role: 'agent', text: 'third answer' }] : [])]) });
    });
    await page.route('**/api/loopx/api/chat/sessions/s1/turns', async (route) => {
      asked.push(JSON.parse(route.request().postData()).message);
      const turn = `t${asked.length}`;
      await json(route, 202, { turn_id: turn, events_url: `/api/chat/sessions/s1/turns/${turn}/events` });
    });
    await page.route('**/api/loopx/api/chat/sessions/s1/turns/*/events*', (route) => {
      const turn = /turns\/(t\d)\/events/.exec(route.request().url())[1];
      if (turn === 't1') return json(route, 502, { error: 'loopx_core_unavailable' });
      // t2 streams part of an answer and closes without an ending; t3 ends normally.
      if (turn === 't2') return sse(route, [['1', 'answer.delta', { text: 'partial answer' }]]);
      return sse(route, [['1', 'answer.delta', { text: 'third answer' }], ['2', 'turn.completed', {}]]);
    });

    await openUi(page, daemon);
    await page.goto(daemon.base + '/#/steward');
    const box = page.locator('.steward-composer textarea');
    const send = async (text) => { await box.fill(text); await page.locator('.steward-composer .send-btn').click(); };

    await send('first');
    await page.locator('.steward-chat [role="alert"]').waitFor();
    await send('second');
    await page.locator('.steward-chat .row.assistant').filter({ hasText: 'partial answer' }).waitFor();
    assert.deepEqual(asked, ['first', 'second'], 'the message after a dropped stream is really sent');

    await send('third');
    await page.locator('.steward-chat .row.assistant').filter({ hasText: 'third answer' }).waitFor();
    assert.deepEqual(asked, ['first', 'second', 'third'], 'the message after a lost history read is really sent');
    assert.equal(await box.inputValue(), '');
  } finally { await context.close(); }
});

test('a steward opened while the core was down opens its conversation once the core is back, and only once', async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  try {
    const page = await context.newPage();
    let statusReads = 0, opened = 0;
    await page.route('**/api/loopx/status.json*', (route) => (++statusReads === 1
      ? json(route, 502, { error: 'loopx_core_unavailable' })
      : json(route, 200, { run_history: { goals: [] }, served_at: new Date().toISOString() })));
    await page.route('**/api/loopx/api/chat/sessions', (route) => { opened++; return json(route, 200, { session_id: 's2' }); });
    await page.route('**/api/loopx/api/chat/sessions/s2', (route) => json(route, 200, { messages: [] }));
    await openUi(page, daemon);
    await page.goto(daemon.base + '/#/steward');
    await page.locator('.steward-chat-failure').waitFor();
    assert.equal(opened, 0);
    // The status refresh finds the core again; the conversation opens without leaving the page.
    await page.locator('.steward-composer textarea:not([disabled])').waitFor({ timeout: 40_000 });
    assert.equal(opened, 1);
    await page.waitForTimeout(16_000); // one more status refresh
    assert.ok(statusReads >= 3);
    assert.equal(opened, 1, 'a refresh does not open another session');
  } finally { await context.close(); }
});
