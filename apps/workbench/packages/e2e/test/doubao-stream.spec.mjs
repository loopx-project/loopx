import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  daemon = await startDaemon({ CALLI_DOUBAO_STREAM: '1', FAKE_DOUBAO_STREAM_DELAY: '900', CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'doubao', adapter: 'doubao', command: ['fake-doubao'] }] }) });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });
const view = async id => (await (await api(daemon, `/api/threads/${id}`)).json());
const idle = async id => {
  for (let index = 0; index < 180; index++) {
    const thread = await view(id);
    if (thread.status !== 'running') return thread;
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  assert.fail('The stream did not finish');
};
const send = async (page, text) => {
  await page.locator('.composer textarea').fill(text);
  await page.getByRole('button', { name: '发送', exact: true }).click();
};

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  const mode = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
  test(`native JSON bridge streams and resumes in ${mode}`, { timeout: 60_000 }, async () => {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      await context.addInitScript(() => localStorage.setItem('calli_onboard_dismissed', '1'));
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      const shot = async name => { if (shots) await page.screenshot({ path: join(shots, `doubao-stream-${mode}-${name}.png`), animations: 'disabled' }); };
      const made = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'doubao', cwd: daemon.workdir, title: `流式桥 ${mode}` }) });
      assert.equal(made.status, 201);
      const thread = await made.json();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: thread.title }).first().click();
      await send(page, 'stream-tool');
      await page.getByText('Inspecting a file.', { exact: true }).waitFor();
      assert.equal((await view(thread.id)).status, 'running', 'the browser receives native output before the task finishes');
      await shot('streaming');
      await page.getByText('Tool task complete.', { exact: true }).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      const completed = await idle(thread.id), id = completed.agentSessionId;
      assert.ok(id); assert.equal(completed.status, 'idle');
      const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      assert.equal(events.filter(event => event.type === 'tool.start').length, 1);
      assert.equal(events.find(event => event.type === 'tool.end').data.status, 'completed');
      const text = events.filter(event => event.type === 'message.delta').map(event => event.data.text).join('');
      assert.equal(text, 'Inspecting a file.Tool task complete.', 'assistant and result snapshots do not repeat streamed text');
      assert.equal(await page.getByText('Tool task complete.', { exact: true }).count(), 1);
      assert.equal((await page.locator('.thread').textContent()).includes('calli_bridge_tool'), false);
      await shot('completed');
      await page.locator('.work-toggle[aria-expanded="false"]').first().click();
      await page.getByRole('button', { name: /读取了 Read fixture/ }).click();
      await page.getByText('fixture contents', { exact: true }).waitFor();
      await shot('tool-details');
      await page.reload();
      await page.locator('.sidebar-inner').waitFor({ state: 'attached' });
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: thread.title }).first().click();
      await page.getByText('Tool task complete.', { exact: true }).waitFor();
      await send(page, 'slow');
      await page.getByText('working', { exact: true }).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).click();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      assert.equal((await idle(thread.id)).agentSessionId, id);
      const stopped = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      assert.equal(stopped.filter(event => event.type === 'tool.end').at(-1).data.status, 'interrupted');
      await send(page, 'stream-unknown-tool');
      await page.getByText('Tool task complete.', { exact: true }).nth(1).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      assert.equal((await idle(thread.id)).agentSessionId, id);
      const unknown = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      assert.equal(unknown.filter(event => event.type === 'tool.end').at(-1).data.status, 'unknown', 'a missing result is not success');
      await shot('resumed');
      await page.locator('.work-toggle[aria-expanded="false"]').last().click();
      await page.getByRole('button', { name: /^Read fixture/ }).last().click();
      await page.getByText('结果未知', { exact: true }).waitFor();
      await shot('unknown-result');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}
