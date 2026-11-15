// Context occupancy must survive reloads, shrink independently of billed totals, and clear when unknown.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon;
let browser;

before(async () => {
  daemon = await startDaemon({
    CALLI_AGENTS_JSON: JSON.stringify({ agents: ['claude', 'codex', 'pi'].map((id) => ({
      id, adapter: id, command: [process.execPath, join(repoRoot, 'daemon', 'test', 'fixtures', `fake-${id === 'claude' ? 'acp' : id}.mjs`)],
    })) }),
  });
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

async function makeThread(hub, agent, title) {
  const response = await api(hub, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, cwd: hub.workdir, title }) });
  assert.equal(response.status, 201);
  return response.json();
}

async function send(hub, thread, text) {
  const response = await api(hub, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
  assert.ok(response.ok);
  for (let i = 0; i < 100; i++) {
    const current = await (await api(hub, `/api/threads/${thread.id}`)).json();
    if (current.status !== 'running') {
      assert.equal(current.status, 'idle');
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail('turn did not settle');
}

async function selectThread(page, thread, mobile) {
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: thread.title }).first().click();
  // On a phone an empty box is one line until it is touched; the context ring is on its second.
  if (mobile) await page.locator('.composer textarea').tap();
}

async function expectUsage(page, percent) {
  const usage = page.locator('.composer .usage');
  const label = percent === null ? '上下文用量未知 · 查看详情' : `上下文已用 ${percent}% · 查看详情`;
  await page.waitForFunction(label => document.querySelector('.composer .usage')?.getAttribute('aria-label') === label, label);
  assert.equal(await usage.getAttribute('aria-label'), label);
  assert.equal(await usage.innerText(), '', 'the ring has no visible percentage text');
  assert.equal(await usage.locator('svg').count(), 1);
  return usage;
}

test('Codex shows current context on desktop and phone in both themes, including after reload', async () => {
  const thread = await makeThread(daemon, 'codex', 'context regression');
  await send(daemon, thread, 'usage');
  for (const mobile of [false, true]) {
    for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({
        viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 },
        isMobile: mobile, hasTouch: mobile, colorScheme,
      });
      try {
        const page = await context.newPage();
        let modelReads = 0;
        page.on('request', request => { if (/^\/api\/agents\/[^/]+\/models$/.test(new URL(request.url()).pathname)) modelReads++; });
        const modelsLoaded = page.waitForResponse(response => new URL(response.url()).pathname === '/api/agents/codex/models' && response.ok());
        await openUi(page, daemon);
        await selectThread(page, thread, mobile);
        await modelsLoaded;
        const usage = await expectUsage(page, 71);
        const model = page.locator('.model-control-trigger');
        await model.waitFor();
        // Read both controls in one frame while the desktop sidebar is still settling.
        assert.ok(await page.evaluate(() => {
          const ring = document.querySelector('.composer .usage')?.getBoundingClientRect();
          const model = document.querySelector('.composer .model-control-trigger')?.getBoundingClientRect();
          return ring && model && ring.width > 0 && model.width > 0 && ring.right <= model.left + 1;
        }), 'the context ring is left of the model button');
        const readsBefore = modelReads;
        await usage.click();
        const panel = page.locator('.context-usage-panel');
        const meter = panel.locator('.context-percent');
        await meter.waitFor();
        assert.equal(await meter.innerText(), '71%');
        assert.equal(await page.locator('.context-usage-panel.high').count(), 0);
        assert.equal(await page.locator('.model-control-panel').count(), 0, 'the ring opens only context details');
        assert.equal(await panel.locator('.usage-head .usage-used').innerText(), '184K / 258K');
        // Let the phone drawer and anchored menu finish moving before inspecting their bounds.
        await page.waitForTimeout(450);
        assert.equal(modelReads, readsBefore, 'opening context details does not request the model catalog');
        const bounds = await panel.boundingBox();
        assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1 && bounds.y + bounds.height <= page.viewportSize().height + 1);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `context-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        await openUi(page, daemon);
        await selectThread(page, thread, mobile);
        await expectUsage(page, 71);
        await usage.click();
        await meter.waitFor();
        assert.equal(await meter.innerText(), '71%');
      } finally {
        await context.close();
      }
    }
  }
  await send(daemon, thread, 'usage compacted');
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await openUi(page, daemon);
    await selectThread(page, thread, false);
    const usage = await expectUsage(page, 5);
    await usage.click();
    const meter = page.locator('.context-usage-panel .context-percent');
    await meter.waitFor();
    assert.equal(await meter.innerText(), '5%');
  } finally {
    await context.close();
  }
});

test('Pi clears its previous context after compaction and measures it again after a response', async () => {
  const thread = await makeThread(daemon, 'pi', 'pi context regression');
  await send(daemon, thread, 'hello');
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await openUi(page, daemon);
    await selectThread(page, thread, false);
    const usage = await expectUsage(page, 1);
    await usage.click();
    const panel = page.locator('.context-usage-panel');
    const meter = panel.locator('.context-percent');
    await meter.waitFor();
    assert.equal(await meter.innerText(), '1%');
    await panel.getByRole('button', { name: '压缩', exact: true }).click();
    await panel.waitFor({ state: 'hidden' });
    assert.equal(await usage.getAttribute('aria-expanded'), 'false');
    await expectUsage(page, null);
    await openUi(page, daemon);
    await selectThread(page, thread, false);
    await page.getByText('已压缩上下文：150.0k → 约 32.0k tokens。', { exact: true }).waitFor();
    await expectUsage(page, null);
    await usage.click();
    await panel.getByText('暂未获取上下文用量，下一次响应后更新。', { exact: true }).waitFor();
    assert.equal(await meter.innerText(), '?', 'unknown context is not shown as a percentage');
    assert.equal(await panel.locator('.usage-head.unknown').count(), 1, 'unknown is not drawn as zero');
    // A manual compact from the hub shows as the compaction tool row, not a native event, so the
    // history line stays away here; the automatic path below covers it.
    await send(daemon, thread, 'hello again');
    await expectUsage(page, 1);
    await meter.waitFor();
    assert.equal(await meter.innerText(), '1%');
  } finally {
    await context.close();
  }
});

test('the context panel shows the request composition, session totals and the /context entry', async () => {
  // A daemon of its own: the telemetry fixtures change the numbers the shared daemon asserts above.
  // Claude's live usage needs a config dir the fixture can write and the adapter tails; both get
  // the same scratch directory through the instance env.
  const claudeConfig = mkdtempSync(join(tmpdir(), 'calli-claude-config-'));
  const hub = await startDaemon({
    CALLI_AGENTS_JSON: JSON.stringify({ agents: [
      { id: 'pi', adapter: 'pi', command: [process.execPath, join(repoRoot, 'daemon', 'test', 'fixtures', 'fake-pi.mjs')] },
      { id: 'claude', adapter: 'claude', command: [process.execPath, join(repoRoot, 'daemon', 'test', 'fixtures', 'fake-acp.mjs')],
        env: { FAKE_LIVE_USAGE: '1', CLAUDE_CONFIG_DIR: claudeConfig } },
    ] }),
    FAKE_TELEMETRY: '1',
  });
  try {
    const pi = await makeThread(hub, 'pi', 'panel composition');
    await send(hub, pi, 'hello');
    // A native automatic compaction: this is the path the history line counts.
    await send(hub, pi, 'auto-compact');
    const claude = await makeThread(hub, 'claude', 'panel entry');
    await send(hub, claude, 'usage');
    await send(hub, claude, 'live usage');

    // Desktop: the composition of the latest request, what the daemon stored for the session,
    // and the compact action — but no /context entry, because pi has no such command.
    for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, hub);
        await selectThread(page, pi, false);
        const usage = await expectUsage(page, 1);
        await usage.click();
        const panel = page.locator('.context-usage-panel');
        await panel.locator('.usage-bar-fill').waitFor();
        const cells = panel.locator('.usage-cell');
        await cells.nth(0).waitFor();
        // input is normalized to include the cache, so the parts add up to the request's 170 tokens.
        assert.deepEqual((await cells.allInnerTexts()).map((text) => text.replace(/\s+/g, '')), [
          '4023.5%缓存读取',
          '105.8%缓存写入',
          '10058.8%未缓存输入',
          '2011.7%输出',
        ]);
        assert.match(await panel.locator('.usage-request-label').innerText(), /最近一次请求 · fixture-model/);
        // The summary fits without scrolling; the totals and the compaction history wait behind Details.
        assert.equal(await panel.locator('.usage-more').count(), 0, 'details stay closed until asked for');
        assert.equal(await panel.getByText(/本会话已压缩/).count(), 0);
        await panel.getByRole('button', { name: '明细' }).click();
        await panel.locator('.usage-more-head', { hasText: '会话累计' }).waitFor();
        await panel.locator('.usage-rows div', { hasText: '缓存命中' }).first().waitFor();
        await panel.getByText(/本会话已压缩 1 次 · 上次 \d{2}:\d{2}（自动）/).waitFor();
        await panel.getByRole('button', { name: '压缩', exact: true }).waitFor();
        assert.equal(await panel.getByRole('button', { name: /\/context/ }).count(), 0, 'pi has no context command');
        const bounds = await panel.boundingBox();
        assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `context-panel-desktop-${colorScheme}.png`) });
        }
      } finally {
        await context.close();
      }
    }

    // Phones in both themes: the same panel, clamped into the viewport.
    for (const colorScheme of ['light', 'dark']) {
      const phone = await browser.newContext({
        viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme,
      });
      try {
        const page = await phone.newPage();
        await openUi(page, hub);
        await selectThread(page, pi, true);
        const usage = await expectUsage(page, 1);
        await usage.click();
        const panel = page.locator('.context-usage-panel');
        await panel.locator('.usage-cell').nth(0).waitFor();
        await page.waitForTimeout(450);
        const bounds = await panel.boundingBox();
        assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0
          && bounds.x + bounds.width <= page.viewportSize().width + 1
          && bounds.y + bounds.height <= page.viewportSize().height + 1);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `context-panel-phone-${colorScheme}.png`) });
        }
      } finally {
        await phone.close();
      }
    }

    // Claude lists /context through the adapter's unlisted commands; the entry sends it as a turn.
    const entry = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    try {
      const page = await entry.newPage();
      await openUi(page, hub);
      await selectThread(page, claude, false);
      const usage = await expectUsage(page, 26);
      await usage.click();
      const panel = page.locator('.context-usage-panel');
      // The live session file gives request-level samples the panel composes.
      await panel.locator('.usage-request-label', { hasText: '最近一次请求' }).waitFor();
      await panel.getByRole('button', { name: '明细' }).click();
      const command = panel.getByRole('button', { name: /\/context/ });
      await command.waitFor();
      const sent = page.waitForRequest((request) => {
        if (!new URL(request.url()).pathname.endsWith(`/api/threads/${claude.id}/turns`)) return false;
        const body = JSON.parse(request.postData() ?? '{}');
        return body.text === '/context';
      });
      await command.click();
      await sent;
      await panel.waitFor({ state: 'hidden' });
    } finally {
      await entry.close();
    }
  } finally {
    await hub.stop();
  }
});
