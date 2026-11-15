import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  const agents = ['codex', 'traex', 'claude', 'pi', 'zcode'].map(id => ({ id, adapter: id,
    command: [process.execPath, join(repoRoot, `daemon/test/fixtures/fake-${['claude', 'traex'].includes(id) ? 'acp' : id}.mjs`)],
    ...(id === 'traex' ? { env: { FAKE_ACP_FLAVOR: 'traex' } } : {}),
  }));
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents }), FAKE_AUTO_COMPACT_DELAY: '1500' });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('native compaction renders in order and survives reload for five agents in four layouts', { timeout: 180000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      await openUi(page, daemon);
      for (const agent of ['codex', 'traex', 'claude', 'pi', 'zcode']) {
        const title = `自动压缩 ${agent} ${mobile} ${colorScheme}`;
        const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, title, cwd: daemon.workdir }) });
        assert.equal(created.status, 201);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: title }).first().click();
        await page.getByRole('textbox', { name: '消息', exact: true }).fill('auto-compact');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        if (['codex', 'traex'].includes(agent)) await page.getByText('正在压缩上下文…', { exact: true }).waitFor();
        await page.getByText(/^用时 /).waitFor();
        await page.locator('.work-toggle').click();
        await page.getByText('上下文已自动压缩', { exact: true }).waitFor();
        assert.equal(await page.locator('.context-compaction').count(), 1);
        assert.ok(await page.locator('.context-compaction').evaluate(el => el.getBoundingClientRect().right <= innerWidth));
        if (agent === 'codex') {
          assert.deepEqual(await page.locator('.work-body').evaluate(el => [...el.children].map(c => c.className)), ['step-run', 'context-compaction', 'step-run']);
          assert.match(await page.locator('.work-toggle').innerText(), /4 步/);
          if (shots) await page.screenshot({ path: join(shots, `compaction-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
        }
        await page.reload();
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: title }).first().click();
        await page.locator('.work-toggle').click();
        await page.getByText('上下文已自动压缩', { exact: true }).waitFor();
        assert.equal(await page.locator('.context-compaction').count(), 1);
      }
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  }
});

test('stopping an unfinished compaction never reports success', async () => {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    const title = '压缩中停止';
    await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', title, cwd: daemon.workdir }) });
    await openUi(page, daemon);
    await page.locator('.thread-title', { hasText: title }).click();
    await page.getByRole('textbox', { name: '消息', exact: true }).fill('auto-compact pending');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.getByText('正在压缩上下文…', { exact: true }).waitFor();
    await page.getByRole('button', { name: '停止', exact: true }).click();
    await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
    await page.locator('.work-toggle').click();
    await page.getByText('上下文压缩未完成', { exact: true }).waitFor();
    assert.equal(await page.getByText('上下文已自动压缩', { exact: true }).count(), 0);
  } finally { await context.close(); }
});
