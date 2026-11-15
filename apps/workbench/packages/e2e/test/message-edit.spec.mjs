// Editing an earlier message keeps one conversation in front of the user: the old turns leave the
// current history, the agent continues a copy of its conversation made just before the edited
// turn, and the thread keeps its id, title and native session trail. The hidden records stay in
// the database, but no second thread appears and nothing in the page can open them.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;
const scriptDir = mkdtempSync(join(tmpdir(), 'calli-edit-'));
const messagesFile = join(scriptDir, 'messages.json');

before(async () => {
  daemon = await startDaemon({ FAKE_PI_MESSAGES_FILE: messagesFile });
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
  rmSync(scriptDir, { recursive: true, force: true });
});

const threads = async () => (await api(daemon, '/api/threads')).json();
const events = async (id) => (await api(daemon, `/api/threads/${id}/events`)).json();
const asks = async (id) => (await events(id)).filter((e) => e.type === 'turn.started');
const editButton = () => page.locator('.row.user .message-edit-btn');
const pageText = () => page.locator('.thread').innerText();
/** Waits until the conversation on screen says `text` and no longer says `gone`. */
async function settles(text, gone = []) {
  const deadline = Date.now() + 15_000;
  for (;;) {
    const shown = await pageText();
    if (shown.includes(text) && gone.every((word) => !shown.includes(word))) return shown;
    if (Date.now() > deadline) throw new Error(`the conversation never showed ${text}:\n${shown}`);
    await page.waitForTimeout(100);
  }
}

/**
 * What the agent's own record of this session holds: the prompts exactly as they were sent (the
 * hub adds its own instructions to the visible words) and what it answered.
 */
function record(sessionId, turns) {
  writeFileSync(
    messagesFile,
    JSON.stringify({
      [sessionId]: turns.flatMap(([prompt, answer]) => [
        { role: 'user', content: prompt },
        { role: 'assistant', content: [{ type: 'text', text: answer }] },
      ]),
    }),
  );
}

test('the first message can be edited from where it stands, and only one thread remains', async () => {
  await openUi(page, daemon);
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('first wording');
  await page.keyboard.press('Enter');
  await page.getByText('hi there first wording').waitFor({ timeout: 10_000 });

  const source = (await threads())[0];
  const [first] = await asks(source.id);
  record(source.agentSessionId, [[first.data.text, 'hi there first wording']]);

  await editButton().click();
  await page.locator('.message-edit textarea').fill('better wording');
  await page.getByRole('button', { name: '发送修改' }).click();

  // The fake agent answers with the prompt as the hub sent it (its own instructions included), so
  // the check is that the new wording is answered and the old one is gone from the conversation.
  await settles('better wording', ['first wording']);
  assert.equal(await page.locator('.row.user').count(), 1);

  // One thread, same id, same place in the sidebar; the agent continues the copy made for the edit.
  const list = await threads();
  assert.equal(list.length, 1);
  assert.equal(list[0].id, source.id);
  assert.notEqual(list[0].agentSessionId, source.agentSessionId);

  // The current history starts at the edited message; the old turn is not in it.
  const started = await asks(source.id);
  assert.deepEqual(started.map((e) => e.data.displayText ?? e.data.text), ['better wording']);
  assert.equal((await api(daemon, `/api/threads/${source.id}/sync`, { method: 'POST' })).status, 200);
});

test('the middle message is edited: what came before stays, what came after leaves the view', async () => {
  await page.locator('.composer textarea').click();
  await page.keyboard.type('second wording');
  await page.keyboard.press('Enter');
  await settles('second wording');
  await page.locator('.composer textarea').click();
  await page.keyboard.type('third wording');
  await page.keyboard.press('Enter');
  await settles('third wording');

  const source = (await threads())[0];
  const started = await asks(source.id);
  assert.equal(started.length, 3);
  record(source.agentSessionId, [
    [started[0].data.text, 'hi there better wording'],
    [started[1].data.text, 'hi there second wording'],
    [started[2].data.text, 'hi there third wording'],
  ]);

  await editButton().nth(1).click();
  await page.locator('.message-edit textarea').fill('second, clearer');
  await page.getByRole('button', { name: '发送修改' }).click();
  // The turn before it stays; the old second turn and everything after it are gone from the view.
  const shown = await settles('second, clearer', ['second wording', 'third wording']);
  assert.ok(shown.includes('better wording'), 'the turn before the edited one stays');
  assert.equal(await page.locator('.row.user').count(), 2);
  assert.equal((await threads()).length, 1, 'still one conversation in the sidebar');

  const current = await asks(source.id);
  assert.deepEqual(current.map((e) => e.data.displayText ?? e.data.text), ['better wording', 'second, clearer']);

  // A reload and reopening the thread read the same current conversation, not the old turns.
  await page.reload();
  await page.locator('.sidebar .thread-title').first().click();
  await settles('second, clearer', ['third wording']);
});

test('a stale page cannot edit: the daemon refuses instead of mixing two histories', async () => {
  const source = (await threads())[0];
  const [first] = await asks(source.id);
  const res = await api(daemon, `/api/threads/${source.id}/turns/${first.turnId}/edit`, {
    method: 'POST',
    body: JSON.stringify({ text: 'too late', images: [], revision: 1, requestId: 'stale-request-1' }),
  });
  assert.equal(res.status, 409);
  assert.match((await res.json()).error, /changed|reload/);
});
