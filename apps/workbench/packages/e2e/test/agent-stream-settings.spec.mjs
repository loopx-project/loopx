import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';
import { writeFakeDoubaoManifest } from '../../daemon/test/fixtures/doubao-runtime.mjs';

const switchName = '启用实验流式桥';
const view = async (daemon, id) => (await (await api(daemon, `/api/threads/${id}`)).json());
const send = async (page, text) => {
  await page.locator('.composer textarea').fill(text);
  await page.getByRole('button', { name: '发送', exact: true }).click();
};
const chooseThread = async (page, title, mobile) => {
  await page.evaluate(() => { location.hash = ''; });
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: title }).first().click();
};
const openSettings = async (page, name) => {
  await page.evaluate(() => { location.hash = '#/agents'; });
  const row = page.locator('.agents-row').filter({ hasText: name });
  await row.getByRole('button', { name: '操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '接入设置', exact: true }).click();
  await page.locator('.agent-settings-dialog').waitFor();
  return page.locator('.agent-settings-dialog');
};

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  const mode = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
  test(`add local CLI, persist stream consent and change the next turn in ${mode}`, { timeout: 90_000 }, async () => {
    const root = mkdtempSync(join(tmpdir(), 'calli-stream-setting-e2e-'));
    const log = join(root, 'calls.jsonl');
    const daemon = await startDaemon({ CALLI_E2E_DOUBAO_RUNTIME: '1', FAKE_DOUBAO_LOG: log, FAKE_DOUBAO_STREAM_DELAY: '300' }, { agents: [] });
    let browser;
    try {
      browser = await launchBrowser();
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      await context.addInitScript(() => localStorage.setItem('calli_onboard_dismissed', '1'));
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      const shot = async name => {
        if (!process.env.CALLI_E2E_SHOTS_DIR) return;
        mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `stream-settings-${mode}-${name}.png`), animations: 'disabled' });
      };
      await openUi(page, daemon);
      await page.evaluate(() => { location.hash = '#/agents'; });
      await page.getByRole('button', { name: '添加 agent', exact: true }).click();
      const add = page.locator('.agent-account-dialog');
      await add.locator('.choice-row').filter({ hasText: 'Doubao CLI' }).click();
      const toggle = add.getByRole('switch', { name: switchName, exact: true });
      assert.equal(await toggle.getAttribute('aria-checked'), 'false', 'new opt-in is not preselected');
      await add.getByText(/仅 Linux x64/).waitFor();
      const name = `豆包 ${mode}`;
      await add.getByLabel('名称（选填）').fill(name);
      await toggle.click();
      await shot('add');
      await add.getByRole('button', { name: '接入 CLI', exact: true }).click();
      await add.waitFor({ state: 'detached' });
      const agent = (await (await api(daemon, '/api/agents')).json()).find(a => a.id === 'doubao');
      assert.equal(agent.label, name); assert.equal(agent.streamBridgeEnabled, true);
      assert.equal(existsSync(log), false, 'enabling only validates local code and starts no native task');
      const stored = JSON.parse(readFileSync(join(daemon.dataDir, 'agents.json'), 'utf8'));
      assert.equal(stored.agents[0].env.CALLI_DOUBAO_STREAM, '1');
      await page.reload();
      const settings = await openSettings(page, name);
      assert.equal(await settings.getByRole('switch', { name: switchName }).getAttribute('aria-checked'), 'true');
      await settings.getByRole('switch', { name: switchName }).click();
      await settings.getByRole('button', { name: '保存设置', exact: true }).click();
      await settings.waitFor({ state: 'detached' });
      assert.equal((await (await api(daemon, '/api/agents')).json()).find(a => a.id === 'doubao').streamBridgeEnabled, false);
      const retry = await openSettings(page, name);
      // Changed output seams report a neutral fallback note; saving consent still succeeds.
      const runtimeRoot = join(daemon.dataDir, 'doubao-runtime');
      const bundlePath = join(runtimeRoot, 'resources/js_app/entry-cli-router/main.js');
      const bundleText = readFileSync(bundlePath, 'utf8');
      writeFileSync(bundlePath, bundleText.replace('case"stream-json":', 'case"new":'));
      writeFakeDoubaoManifest(runtimeRoot);
      await retry.getByRole('switch', { name: switchName }).click();
      await retry.getByRole('status').waitFor();
      assert.match(await retry.getByRole('status').textContent(), /普通文本输出/);
      assert.equal(await retry.getByRole('alert').count(), 0, 'an incompatible install is not an error');
      await shot('incompatible');
      await retry.getByRole('button', { name: '保存设置', exact: true }).click();
      await retry.waitFor({ state: 'detached' });
      assert.equal((await (await api(daemon, '/api/agents')).json()).find(a => a.id === 'doubao').streamBridgeEnabled, true);
      const bridge = await (await api(daemon, '/api/agents/doubao/stream-bridge')).json();
      assert.equal(bridge.compatible, false); assert.match(bridge.reason, /输出结构/);
      writeFileSync(bundlePath, bundleText);
      writeFakeDoubaoManifest(runtimeRoot);
      const made = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'doubao', cwd: daemon.workdir, title: `测试下一轮 ${mode}` }) });
      const thread = await made.json();
      await chooseThread(page, thread.title, mobile);
      await send(page, 'stream-tool');
      await page.getByText('Tool task complete.', { exact: true }).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      const sessionId = (await view(daemon, thread.id)).agentSessionId;
      assert.ok(sessionId);
      await send(page, 'slow');
      await page.getByText('working', { exact: true }).waitFor();
      const during = await openSettings(page, name);
      await during.getByText('从下一轮开始生效，不打断当前任务。', { exact: true }).waitFor();
      await shot('settings');
      await during.getByRole('switch', { name: switchName }).click();
      await during.getByRole('button', { name: '保存设置', exact: true }).click();
      await during.waitFor({ state: 'detached' });
      assert.equal((await view(daemon, thread.id)).status, 'running', 'changing the bridge preserves the in-flight task');
      await chooseThread(page, thread.title, mobile);
      await page.getByRole('button', { name: '停止', exact: true }).click();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      await send(page, 'after switch');
      await page.getByText('doubao:after switch', { exact: true }).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      assert.equal((await view(daemon, thread.id)).agentSessionId, sessionId);
      const calls = readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line));
      assert.equal(calls.at(-1).runtime, undefined, 'next turn uses the original CLI shim, not a patched copy');
      await shot('next-turn');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      assert.deepEqual(errors, []);
      await context.close();
    } finally { await browser?.close(); await daemon.stop(); rmSync(root, { recursive: true, force: true }); }
  });
}

test('discovery offers bridge consent separately from selecting a CLI, and incompatible installs still connect', { timeout: 60_000 }, async () => {
  const daemon = await startDaemon({ CALLI_E2E_DOUBAO_RUNTIME: '1', CALLI_E2E_DISCOVER_DOUBAO2: '1' }, { agents: [{ id: 'pi', adapter: 'pi', command: ['fake-pi'] }] });
  let browser;
  try {
    browser = await launchBrowser();
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' });
    await context.addInitScript(() => localStorage.setItem('calli_onboard_dismissed', '1'));
    const page = await context.newPage();
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/agents'; });
    await page.getByRole('button', { name: '添加 agent', exact: true }).click();
    await page.getByRole('button', { name: '接入已安装的 agent 或已有账号', exact: true }).click();
    const discovery = page.locator('.onboard-dialog-results');
    const entry = discovery.locator('.onboard-entry').filter({ has: page.getByText('doubao2', { exact: true }) });
    await entry.waitFor();
    for (const row of await discovery.locator('.onboard-row-installed').all()) {
      if (!(await row.textContent()).includes('doubao2') && await row.getAttribute('aria-checked') === 'true') await row.click();
    }
    const row = entry.locator('.onboard-row-installed'), toggle = entry.getByRole('switch', { name: switchName });
    assert.equal(await toggle.getAttribute('aria-checked'), 'false');
    await row.click(); assert.equal(await toggle.isDisabled(), true);
    await row.click(); await toggle.click();
    assert.equal(await row.getAttribute('aria-checked'), 'true', 'checking bridge consent never deselects its CLI');
    const runtimeRoot = join(daemon.dataDir, 'doubao-runtime');
    const bundlePath = join(runtimeRoot, 'resources/js_app/entry-cli-router/main.js'), bundleText = readFileSync(bundlePath, 'utf8');
    writeFileSync(bundlePath, bundleText.replace('case"stream-json":', 'case"new":'));
    writeFakeDoubaoManifest(runtimeRoot);
    await discovery.getByRole('button', { name: '连接 1 个', exact: true }).click();
    await discovery.waitFor({ state: 'detached' }, { timeout: 10_000 });
    const agent = (await (await api(daemon, '/api/agents')).json()).find(a => a.id === 'doubao2');
    assert.ok(agent.available); assert.equal(agent.streamBridgeEnabled, true, 'consent saves even when incompatible');
    assert.equal((await (await api(daemon, '/api/agents/doubao2/stream-bridge')).json()).compatible, false);
    const saved = JSON.parse(readFileSync(join(daemon.dataDir, 'agents.json'), 'utf8')).agents.find(a => a.id === 'doubao2');
    assert.equal(saved.env.CALLI_DOUBAO_STREAM, '1');
    await context.close();
  } finally { await browser?.close(); await daemon.stop(); }
});
