import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon;
let browser;
before(async () => {
  daemon = await startDaemon({
    FAKE_CODEX_WEEKLY_ONLY: '1', FAKE_CODEX_NO_EFFORT_MODEL: '1',
    CALLI_AGENTS_JSON: JSON.stringify({ agents: ['claude', 'codex', 'pi'].map((id) => ({
      id, adapter: id, command: [process.execPath, join(repoRoot, 'daemon/test/fixtures', `fake-${id === 'claude' ? 'acp' : id}.mjs`)],
    })) }),
  });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); });
const waitFor = async (fn) => {
  for (let i = 0; i < 160; i++) { if (await fn()) return; await new Promise((r) => setTimeout(r, 50)); }
  assert.fail('state did not settle');
};
const read = async (thread) => (await api(daemon, `/api/threads/${thread.id}`)).json();
const send = async (thread, text) => {
  const res = await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
  assert.ok(res.ok);
};
async function makeThread(title, agent = 'codex') {
  const response = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, cwd: daemon.workdir, title }) });
  assert.equal(response.status, 201);
  const thread = await response.json();
  await send(thread, 'usage');
  await waitFor(async () => (await read(thread)).status === 'idle');
  return thread;
}
async function select(page, thread, mobile = false) {
  await openUi(page, daemon);
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: thread.title }).first().click();
  // On a phone an empty box is one line until it is touched; the controls are on its second.
  if (mobile) await page.locator('.composer textarea').tap();
  await page.locator('.model-control-trigger:visible').waitFor();
}
async function shot(page, name) {
  await page.waitForTimeout(350);
  if (!process.env.CALLI_E2E_SHOTS_DIR) return;
  mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}.png`) });
}
async function fits(page, locator) {
  const bounds = await locator.boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1 && bounds.y + bounds.height <= page.viewportSize().height + 1);
}

test('effort slider, model subview, reset and compression work in both themes and viewport sizes', async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const tag = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
    const thread = await makeThread(`上下文操作 ${tag}`);
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await select(page, thread, mobile);
      await page.locator('.model-control-trigger').click();
      const slider = page.getByRole('slider', { name: '思考强度' });
      await slider.waitFor();
      const modelPanel = page.locator('.model-control-panel');
      assert.equal(await modelPanel.locator('.model-context, .context-percent').count(), 0);
      assert.equal(await modelPanel.getByText('上下文', { exact: true }).count(), 0);
      assert.equal(await modelPanel.getByRole('button', { name: /压缩/ }).count(), 0);
      await slider.press('Home');
      await waitFor(async () => (await read(thread)).effort === null);
      await page.waitForTimeout(350);
      assert.equal(await slider.getAttribute('aria-valuemax'), '3');
      const rail = await slider.locator('.effort-rail').boundingBox();
      if (mobile) await page.touchscreen.tap(rail.x + rail.width, rail.y + rail.height / 2);
      else {
        let patches = 0;
        page.on('request', (request) => { if (request.method() === 'PATCH' && request.url().endsWith(`/threads/${thread.id}`)) patches++; });
        await page.mouse.move(rail.x, rail.y + rail.height / 2);
        await page.mouse.down();
        await page.mouse.move(rail.x + rail.width, rail.y + rail.height / 2, { steps: 8 });
        assert.equal(patches, 0, 'dragging only previews the selection');
        await page.mouse.up();
        await waitFor(async () => (await read(thread)).effort === 'max');
        await waitFor(async () => patches === 1);
        assert.equal(patches, 1, 'releasing writes once');
      }
      await waitFor(async () => (await read(thread)).effort === 'max');
      await shot(page, `effort-${tag}`);
      await fits(page, page.locator('.model-control-panel'));
      assert.equal(await slider.getAttribute('aria-valuetext'), '最大');
      await slider.press('ArrowLeft');
      await waitFor(async () => (await read(thread)).effort === 'high');
      await slider.press('End');
      await waitFor(async () => (await read(thread)).effort === 'max');
      // The button shows the level as a dial; its name says it in words. A phone shows the dial alone.
      await waitFor(async () => (await page.locator('.model-control-trigger').getAttribute('aria-label')) === '模型与思考强度：GPT Big · 最大');
      assert.equal(await page.locator('.model-control-trigger').getAttribute('aria-label'), '模型与思考强度：GPT Big · 最大');
      assert.equal(await page.locator('.model-control-trigger .model-trigger-name').count(), mobile ? 0 : 1);
      if (mobile) {
        assert.equal(await page.locator('.model-control-trigger.dial-only .model-trigger-gauge svg').count(), 1);
        assert.equal(await page.locator('.model-control-trigger.usage, .model-control-trigger .usage-track').count(), 0, 'the effort dial is not the context ring');
        const dial = await page.locator('.model-control-trigger').boundingBox();
        assert.equal(Math.round(dial.width), Math.round(dial.height), `the dial button is a circle (${dial.width}×${dial.height})`);
      }
      await page.getByRole('button', { name: '恢复默认强度' }).click();
      await waitFor(async () => (await read(thread)).effort === null);
      await page.getByRole('button', { name: /切换模型/ }).click();
      assert.equal(await modelPanel.getByRole('button', { name: /压缩/ }).count(), 0);
      assert.equal(await modelPanel.getByText('上下文', { exact: true }).count(), 0);
      await page.getByRole('menuitemradio', { name: 'GPT Small', exact: true }).click();
      await waitFor(async () => (await read(thread)).model === 'gpt-small');
      await waitFor(async () => (await slider.getAttribute('aria-valuemax')) === '2');
      assert.equal(await slider.getAttribute('aria-valuemax'), '2');
      await slider.press('End');
      await waitFor(async () => (await read(thread)).effort === 'high');
      await page.getByRole('button', { name: /切换模型/ }).click();
      await page.getByRole('menuitemradio', { name: 'GPT Basic', exact: true }).click();
      await page.getByText('当前模型不提供思考强度设置。').waitFor();
      assert.equal(await slider.count(), 0);
      await page.keyboard.press('Escape');
      await page.locator('.model-control-trigger').click();
      await page.getByRole('menuitemradio', { name: /GPT Big/ }).last().click();
      await slider.waitFor();
      await page.keyboard.press('Escape');
      const input = page.locator('.composer textarea');
      await input.fill('这段草稿需要保留');
      const usage = page.locator('.composer .usage');
      await usage.click();
      const usagePanel = page.locator('.context-usage-panel');
      await usagePanel.waitFor();
      assert.equal(await modelPanel.count(), 0);
      assert.equal(await usagePanel.getByRole('slider', { name: '思考强度' }).count(), 0);
      await shot(page, `compact-${tag}`);
      await fits(page, usagePanel);
      await usagePanel.getByRole('button', { name: '压缩 精简较早的对话，释放上下文' }).click();
      await usagePanel.waitFor({ state: 'hidden' });
      assert.equal(await usage.getAttribute('aria-expanded'), 'false');
      await page.getByText('上下文压缩完成。', { exact: true }).waitFor();
      assert.equal(await input.inputValue(), '这段草稿需要保留');
      await input.fill('/usage');
      await input.press('Enter');
      const list = page.getByRole('region', { name: '额度', exact: true });
      await list.getByRole('button', { name: '查看 Codex 的额度详情', exact: true }).click();
      const quota = page.getByRole('region', { name: 'Codex', exact: true });
      await quota.getByText('本次仅返回每周额度，服务端未提供 5 小时窗口。').waitFor();
      assert.equal(await quota.getByRole('meter').count(), 1);
      assert.match(await quota.getByRole('meter').getAttribute('aria-label'), /^每周/);
      await shot(page, `quota-weekly-${tag}`);
    } finally { await context.close(); }
  }
});

test('the effort slider follows the finger and does not snap back while the change is saved', async () => {
  const thread = await makeThread('强度滑块不跳');
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  try {
    const page = await context.newPage();
    // The server answers slowly, then not at all: the slider must not wait for it either way.
    let held = null;
    let fail = false;
    await page.route(`**/api/threads/${thread.id}`, async (route) => {
      if (route.request().method() !== 'PATCH') return route.continue();
      await new Promise((resolve) => { held = resolve; });
      if (fail) await route.fulfill({ status: 500, json: { error: 'nope' } });
      else await route.continue();
    });
    await select(page, thread);
    await page.locator('.model-control-trigger').click();
    const slider = page.getByRole('slider', { name: '思考强度' });
    await slider.waitFor();
    const rail = await slider.locator('.effort-rail').boundingBox();
    const thumbX = async () => { const b = await slider.locator('.effort-thumb').boundingBox(); return b.x + b.width / 2; };
    const y = rail.y + rail.height / 2;

    // Between two levels the thumb is under the finger, not on a dot; the label is the nearest level.
    await page.mouse.move(rail.x, y);
    await page.mouse.down();
    await page.mouse.move(rail.x + rail.width * 0.6, y, { steps: 6 });
    assert.ok(Math.abs((await thumbX()) - (rail.x + rail.width * 0.6)) < 2, 'the thumb is under the finger');
    assert.equal(await page.locator('.effort-value').innerText(), '高');
    await page.mouse.move(rail.x + rail.width, y, { steps: 4 });
    await page.mouse.up();

    // Let go, the server has not answered yet: the slider stays where it was put.
    await waitFor(async () => held !== null);
    assert.equal(await slider.getAttribute('aria-valuetext'), '最大');
    assert.equal(await page.locator('.effort-value').innerText(), '最大');
    await page.waitForTimeout(300);
    assert.ok(Math.abs((await thumbX()) - (rail.x + rail.width)) < 2, 'the thumb stays at the end');
    held();
    await waitFor(async () => (await read(thread)).effort === 'max');

    // A change the server refuses goes back to what was there.
    fail = true;
    held = null;
    await slider.press('ArrowLeft');
    await waitFor(async () => held !== null);
    assert.equal(await slider.getAttribute('aria-valuetext'), '高');
    held();
    await waitFor(async () => (await slider.getAttribute('aria-valuetext')) === '最大');
    assert.equal((await read(thread)).effort, 'max');
  } finally { await context.close(); }
});

test('compression queues once during a turn and preserves a draft; pi uses its own compact command', async () => {
  const thread = await makeThread('排队压缩');
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.route('**/api/agents', async (route) => {
      const response = await route.fetch();
      const agents = await response.json();
      for (const agent of agents) delete agent.capabilities.compact;
      await route.fulfill({ response, json: agents });
    });
    await send(thread, 'delayed');
    await select(page, thread);
    await page.locator('.composer textarea').fill('下一轮草稿');
    await page.getByRole('button', { name: '停止', exact: true }).waitFor();
    const usage = page.locator('.composer .usage');
    const usagePanel = page.locator('.context-usage-panel');
    await usage.click();
    await usagePanel.getByRole('button', { name: /压缩 本轮结束后/ }).click();
    await usagePanel.waitFor({ state: 'hidden' });
    assert.equal(await usage.getAttribute('aria-expanded'), 'false');
    await waitFor(async () => (await read(thread)).queued.length === 1);
    // A successful submission closes the menu; reopen it to inspect the queued action.
    await usage.click();
    const queuedCompact = usagePanel.getByRole('button', { name: /已排队压缩/ });
    await queuedCompact.waitFor();
    assert.ok(await queuedCompact.isDisabled());
    assert.equal((await read(thread)).queued.length, 1);
    await page.keyboard.press('Escape');
    await page.getByText('上下文压缩完成。', { exact: true }).waitFor();
    assert.equal(await page.locator('.composer textarea').inputValue(), '下一轮草稿');
    const pi = await makeThread('pi 压缩操作', 'pi');
    await select(page, pi);
    await usage.click();
    await usagePanel.getByRole('button', { name: '压缩 精简较早的对话，释放上下文' }).click();
    await usagePanel.waitFor({ state: 'hidden' });
    assert.equal(await usage.getAttribute('aria-expanded'), 'false');
    await page.getByText('已压缩上下文：150.0k → 约 32.0k tokens。', { exact: true }).waitFor();
  } finally { await context.close(); }
});
