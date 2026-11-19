import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, other, browser;
const prefsPath = '/api/draft-model-preferences';
const env = { LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: ['codex', 'second'].map(id => ({
  id, label: id, adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')],
})) }) };
before(async () => { daemon = await startDaemon(env); other = await startDaemon(env); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); await other?.stop(); });
const read = async (hub = daemon) => (await api(hub, prefsPath)).json();
const edit = async (agent, patch, hub = daemon) => {
  const response = await api(hub, prefsPath, { method: 'PATCH', body: JSON.stringify({ agent, ...patch }) });
  assert.equal(response.status, 200); return response.json();
};
async function waitFor(check) {
  for (let i = 0; i < 200; i++) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
  assert.fail('preference did not settle');
}
async function screenshot(page, name) {
  if (!process.env.LOOCHAT_E2E_SHOTS_DIR) return;
  mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `${name}.png`) });
}
async function newThread(page, mobile) {
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.sidebar').getByRole('button', { name: '新对话', exact: true }).click();
}
async function agent(page, name) {
  await page.locator('.composer .context-btn').first().click();
  await page.locator('.agent-picker-panel > button.pop-item').first().click();
  await page.getByRole('menuitemradio', { name: new RegExp(`^${name}`, 'i') }).click();
}
async function chooseModel(page, name) {
  await page.getByRole('button', { name: /切换模型/ }).click();
  await page.getByRole('menuitemradio', { name: new RegExp(`^${name}`) }).last().click();
}
const summary = page => page.locator('.model-control-trigger').getAttribute('aria-label');

for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
  test(`choices survive reload and model/agent switches, ${mobile ? 'phone' : 'desktop'} ${theme}`, async () => {
    await edit('codex', { model: 'gpt-small', effort: 'low' });
    await edit('codex', { model: 'gpt-big', effort: 'high' });
    await edit('second', { model: 'gpt-small', effort: 'low' });
    await edit('codex', { model: 'gpt-small', effort: 'low' }, other);
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
    try {
      await context.addInitScript(({ token }) => {
        localStorage.setItem('calli.token', token);
        localStorage.setItem('calli_onboard_dismissed', '1');
        // An old browser-only choice must not override the daemon.
        localStorage.setItem('calli.agent.choice', JSON.stringify({ codex: { model: 'gpt-small', effort: 'low' } }));
      }, { token: daemon.token });
      const page = await context.newPage(); page.setDefaultTimeout(8000);
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      let release; const gate = new Promise(resolve => { release = resolve; });
      let held = true, writes = 0;
      await page.route('**/api/draft-model-preferences', async route => {
        if (route.request().method() !== 'GET') { writes++; return route.continue(); }
        const response = await route.fetch();
        if (held) await gate;
        await route.fulfill({ response });
      });
      await page.goto(daemon.base);
      await page.locator('.model-control-trigger').waitFor();
      assert.equal(await summary(page), '正在加载…');
      const loading = await page.locator('.composer').boundingBox();
      await screenshot(page, `preferences-loading-${mobile}-${theme}`);
      held = false; release();
      await waitFor(async () => (await summary(page))?.includes('GPT Big · 高'));
      assert.equal(writes, 0, 'loading and initial selection must not save anything');
      const loaded = await page.locator('.composer').boundingBox();
      assert.ok(Math.abs(loading.y - loaded.y) <= 2, 'the composer stays in place');
      await page.locator('.model-control-trigger').click();
      await chooseModel(page, 'GPT Small');
      const slider = page.getByRole('slider', { name: '思考强度' });
      await waitFor(async () => (await summary(page))?.includes('GPT Small · 低'));
      await slider.press('End');
      await waitFor(async () => (await read()).codex.effort === 'high');
      await chooseModel(page, 'GPT Big');
      await waitFor(async () => (await read()).codex.model === 'gpt-big');
      assert.match(await summary(page), /GPT Big · 高/);
      await screenshot(page, `preferences-panel-${mobile}-${theme}`);
      await page.keyboard.press('Escape');
      await agent(page, 'second');
      await waitFor(async () => (await summary(page))?.includes('GPT Small · 低'));
      await agent(page, 'codex');
      await waitFor(async () => (await summary(page))?.includes('GPT Big · 高'));
      const beforeReload = await read();
      const beforeWrites = writes;
      await page.reload();
      await waitFor(async () => (await summary(page))?.includes('GPT Big · 高'));
      assert.deepEqual(await read(), beforeReload);
      assert.equal(writes, beforeWrites, 'reload and agent switches are read-only');
      assert.equal((await read(other)).codex.model, 'gpt-small', 'another machine keeps its own choice');
      // An existing thread retains its settings; merely viewing it does not change defaults.
      const response = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title: `old-${mobile}-${theme}`, model: 'gpt-small', effort: 'low' }) });
      const thread = await response.json();
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: thread.title }).click();
      if (mobile) await page.locator('.composer textarea').tap();
      await waitFor(async () => (await summary(page))?.includes('GPT Small · 低'));
      assert.deepEqual(await read(), beforeReload);
      await page.locator('.model-control-trigger').click();
      await page.getByRole('slider', { name: '思考强度' }).press('End');
      await waitFor(async () => (await read()).codex.model === 'gpt-small' && (await read()).codex.effort === 'high');
      await page.keyboard.press('Escape');
      await newThread(page, mobile);
      await waitFor(async () => (await summary(page))?.includes('GPT Small · 高'));
      assert.equal((await (await api(daemon, `/api/threads/${thread.id}`)).json()).effort, 'high');
      await screenshot(page, `preferences-new-${mobile}-${theme}`);
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}

test('another browser reads server choices and failed reads/writes never clear saved preferences', async () => {
  await edit('codex', { model: 'gpt-big', effort: 'high' });
  const contexts = [];
  try {
    for (let i = 0; i < 2; i++) {
      const ctx = await browser.newContext(); contexts.push(ctx);
      await ctx.addInitScript(token => { localStorage.setItem('calli.token', token); localStorage.setItem('calli_onboard_dismissed', '1'); }, daemon.token);
    }
    const a = await contexts[0].newPage(), b = await contexts[1].newPage();
    await a.goto(daemon.base);
    await waitFor(async () => (await summary(a))?.includes('GPT Big · 高'));
    await a.locator('.model-control-trigger').click();
    await chooseModel(a, 'GPT Small');
    await waitFor(async () => (await read()).codex.model === 'gpt-small');
    await b.goto(daemon.base);
    await waitFor(async () => (await summary(b))?.includes('GPT Small'));
    const before = await read();
    await b.route('**/api/draft-model-preferences', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'test preference failure' }) }));
    await b.reload();
    await waitFor(async () => (await summary(b)) === '模型偏好读取失败');
    assert.deepEqual(await read(), before);
    await b.locator('.model-control-trigger').click();
    await b.unroute('**/api/draft-model-preferences');
    await b.getByRole('button', { name: '重试', exact: true }).click();
    await waitFor(async () => (await summary(b))?.includes('GPT Small'));
    await b.keyboard.press('Escape');
    await b.route('**/api/draft-model-preferences', route => route.request().method() === 'PATCH'
      ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'test save failure' }) }) : route.continue());
    await b.locator('.model-control-trigger').click();
    await chooseModel(b, 'GPT Big');
    await b.getByText('test save failure', { exact: true }).waitFor();
    await waitFor(async () => (await summary(b))?.includes('GPT Small'));
    assert.deepEqual(await read(), before);
  } finally { for (const ctx of contexts) await ctx.close(); }
});

test('a late refresh cannot overwrite a newer explicit model choice', async () => {
  await edit('codex', { model: 'gpt-small', effort: 'low' });
  const context = await browser.newContext();
  let release;
  try {
    await context.addInitScript(token => { localStorage.setItem('calli.token', token); localStorage.setItem('calli_onboard_dismissed', '1'); }, daemon.token);
    const page = await context.newPage();
    await page.goto(daemon.base);
    await waitFor(async () => (await summary(page))?.includes('GPT Small · 低'));
    let hold = true, captured = false;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/api/draft-model-preferences', async route => {
      if (route.request().method() !== 'GET' || !hold) return route.continue();
      hold = false;
      const response = await route.fetch(); captured = true;
      await gate;
      await route.fulfill({ response });
    });
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => captured);
    await page.locator('.model-control-trigger').click();
    await chooseModel(page, 'GPT Big');
    await waitFor(async () => (await read()).codex.model === 'gpt-big');
    const staleResponse = page.waitForResponse(response => response.url().endsWith(prefsPath) && response.request().method() === 'GET');
    release(); await staleResponse;
    await waitFor(async () => (await summary(page))?.includes('GPT Big'));
    assert.equal((await read()).codex.model, 'gpt-big');
  } finally { release?.(); await context.close(); }
});
