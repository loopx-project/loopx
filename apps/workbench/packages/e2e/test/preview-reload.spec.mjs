// The preview of a page an agent wrote keeps its iframe while the parent renders anew: events
// streaming into the thread re-render the app every few seconds, and the page the person is
// reading must not start over each time, losing its state. Only the refresh button re-signs.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });

const post = async (path, body) => {
  const res = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
  const text = await res.text(); // a turn is accepted with 202 and nothing to read back
  assert.ok(res.ok, `${path}: ${text}`);
  return text ? JSON.parse(text) : null;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const idle = async (id) => {
  for (let i = 0; i < 200; i++) {
    const t = await (await api(daemon, `/api/threads/${id}`)).json();
    if (t.status !== 'running' && !['checking', 'preparing', 'countdown'].includes(t.autoHandover?.state) && !t.queued?.length) return t;
    await sleep(100);
  }
  throw new Error(`thread ${id} is still running`);
};

test('the preview keeps its frame while the thread streams events; refresh alone reloads it', async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  try {
    const thread = await post('/api/threads', { agent: 'claude', cwd: daemon.workdir, title: '预览刷新' });
    await post(`/api/threads/${thread.id}/turns`, { text: 'make outputs' });
    await idle(thread.id);

    await openUi(page, daemon);
    await page.locator('.thread-row', { hasText: '预览刷新' }).click();
    // Every signing of the page's URL is counted; opening the preview is the first and only one.
    let signed = 0;
    page.on('request', req => { if (req.url().includes('/preview-link')) signed++; });
    await page.locator('.changes-head').click(); // the written files sit inside the changes card
    await page.locator('.changes-preview').first().click();
    await page.locator('.preview-frame').waitFor();
    const firstSrc = await page.evaluate(() => {
      const frame = document.querySelector('.preview-frame');
      frame.__probe = 'first'; // a property only this DOM node carries; a remount loses it
      return frame.getAttribute('src');
    });
    await page.waitForTimeout(500);
    assert.equal(signed, 1);

    // A thread that keeps talking to the page: every turn re-renders the app under the dialog.
    for (let i = 1; i <= 5; i++) {
      await post(`/api/threads/${thread.id}/turns`, { text: `第 ${i} 轮`, queue: true });
      await idle(thread.id);
    }
    await page.waitForFunction(() => document.body.innerText.includes('echo:第 5 轮'));
    await page.waitForTimeout(500);
    // The same frame node and the same signed URL: the page never started over on its own.
    const kept = await page.evaluate(() => {
      const frame = document.querySelector('.preview-frame');
      return { probe: frame?.__probe ?? null, src: frame?.getAttribute('src') ?? null };
    });
    assert.equal(kept.probe, 'first', 'the frame was rebuilt while the thread streamed events');
    assert.equal(kept.src, firstSrc);
    assert.equal(signed, 1, 'events streaming into the thread re-signed the preview');

    // The refresh button alone re-signs and reloads.
    await page.locator('.preview-actions button', { hasText: '刷新' }).click();
    await page.waitForFunction(() => {
      const frame = document.querySelector('.preview-frame');
      return Boolean(frame) && frame.__probe === undefined;
    });
    assert.equal(signed, 2, 'the refresh button did not re-sign the page');
    await page.locator('.preview-frame').waitFor();
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});
