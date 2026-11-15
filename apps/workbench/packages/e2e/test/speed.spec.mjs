import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';
let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')], env: { FAKE_CODEX_SPEED: '1' } }, { id: 'claude', adapter: 'claude', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-acp.mjs')], env: { FAKE_ACP_SPEED: '1' } }, { id: 'pi', adapter: 'pi', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-pi.mjs')] }] }) });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('lightning toggles speed, restores selection, sends in Plan and clears on model change in all four layouts', { timeout: 120000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const title = `速度设置 ${mobile} ${colorScheme}`;
      const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', title, cwd: daemon.workdir, effort: 'high', workMode: 'plan', permissionMode: 'full_access' }) });
      const thread = await res.json(); assert.equal(res.status, 201);
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      // On a phone an empty box is one line until it is touched; the controls are on its second.
      if (mobile) await page.locator('.composer textarea').tap();
      const picker = () => page.getByRole('button', { name: /^模型与思考强度：/ });
      const toggle = async (name, keyboard = false) => {
        const button = page.getByRole('button', { name, exact: true });
        if (keyboard) await button.focus();
        await Promise.all([
          page.waitForResponse(r => r.url().endsWith(`/api/threads/${thread.id}`) && r.request().method() === 'PATCH' && r.ok()),
          keyboard ? button.press('Enter') : button.click(),
        ]);
      };
      await picker().click();
      assert.equal(await page.getByRole('button', { name: '开启加速', exact: true }).getAttribute('aria-pressed'), 'false');
      if (shots) await page.screenshot({ path: join(shots, `standard-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await toggle('开启加速');
      assert.equal(await page.getByRole('button', { name: '关闭加速', exact: true }).getAttribute('aria-pressed'), 'true');
      if (shots) await page.screenshot({ path: join(shots, `speed-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await page.getByRole('button', { name: /切换模型，当前/ }).click();
      await page.getByText('For hard work · 可加速', { exact: true }).waitFor();
      if (shots) await page.screenshot({ path: join(shots, `models-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await page.keyboard.press('Escape');
      await page.reload();
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      // On a phone an empty box is one line until it is touched; the controls are on its second.
      if (mobile) await page.locator('.composer textarea').tap();
      await picker().click();
      assert.equal(await page.getByRole('button', { name: '关闭加速', exact: true }).getAttribute('aria-pressed'), 'true');
      await toggle('关闭加速');
      assert.equal(await page.getByRole('button', { name: '开启加速', exact: true }).getAttribute('aria-pressed'), 'false');
      const standard = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      assert.equal(standard.speed, 'default'); assert.equal(standard.effort, 'high'); assert.equal(standard.workMode, 'plan');
      await toggle('开启加速', !mobile);
      await page.keyboard.press('Escape');
      await page.locator('.composer textarea').fill('speed?');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText('model:gpt-big;effort:high;speed:priority;mode:plan;approval:never', { exact: true }).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      await picker().click();
      await page.getByRole('button', { name: /切换模型，当前/ }).click();
      await page.getByRole('menuitemradio', { name: 'GPT Small', exact: true }).click();
      await page.locator('.model-speed-toggle').waitFor({ state: 'hidden' });
      const updated = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      assert.equal(updated.model, 'gpt-small'); assert.equal(updated.speed, 'default');
      assert.equal(updated.effort, null, 'a model without a saved effort starts at its own default');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    } finally { await context.close(); }
  }
});


test('Claude speed uses its own protocol and unsupported agents hide the control', { timeout: 30000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  try {
    const page = await context.newPage();
    for (const agent of ['claude', 'pi']) {
      const title = `速度能力 ${agent}`;
      const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, title, cwd: daemon.workdir }) });
      assert.equal(res.status, 201); const thread = await res.json();
      await openUi(page, daemon);
      await page.locator('.thread-title', { hasText: title }).first().click();
      await page.getByRole('button', { name: /^模型与思考强度：/ }).click();
      if (agent === 'pi') { assert.equal(await page.locator('.model-speed-toggle').count(), 0); continue; }
      await page.getByRole('button', { name: '开启加速', exact: true }).click();
      await page.keyboard.press('Escape');
      await page.locator('.composer textarea').fill('speed?');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText('speed:on', { exact: true }).waitFor();
      const updated = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      assert.equal(updated.speed, 'fast');
    }
  } finally { await context.close(); }
});

test('a newer UI keeps the lightning toggle with older Hub model responses in all four layouts', { timeout: 60000 }, async () => {
  for (const agent of ['codex', 'claude']) for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await page.route('**/api/agents/*/models', async route => {
        const response = await route.fetch();
        const models = await response.json();
        await route.fulfill({ response, json: models.map(model => ({ ...model, speeds: model.speeds?.map(({ isFast, ...speed }) => speed) })) });
      });
      const title = `旧接口 ${agent} ${mobile} ${colorScheme}`;
      const speed = agent === 'codex' ? 'priority' : 'fast';
      const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, title, cwd: daemon.workdir, speed, effort: 'high' }) });
      assert.equal(res.status, 201); const thread = await res.json();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      // On a phone an empty box is one line until it is touched; the controls are on its second.
      if (mobile) await page.locator('.composer textarea').tap();
      await page.getByRole('button', { name: /^模型与思考强度：/ }).click();
      assert.equal(await page.getByRole('button', { name: '关闭加速', exact: true }).getAttribute('aria-pressed'), 'true');
      if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `legacy-${agent}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      for (const [name, expected] of [['关闭加速', 'default'], ['开启加速', speed]]) {
        await Promise.all([
          page.waitForResponse(r => r.url().endsWith(`/api/threads/${thread.id}`) && r.request().method() === 'PATCH' && r.ok()),
          page.getByRole('button', { name, exact: true }).click(),
        ]);
        const saved = await (await api(daemon, `/api/threads/${thread.id}`)).json();
        assert.equal(saved.speed, expected); assert.equal(saved.effort, 'high');
      }
    } finally { await context.close(); }
  }
});
