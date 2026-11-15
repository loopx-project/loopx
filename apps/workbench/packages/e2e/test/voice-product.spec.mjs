import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{
    id: 'voice-provider', adapter: 'codex',
    command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')],
  }, { id: 'text-provider', adapter: 'claude', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-acp.mjs')] }] }), FAKE_CODEX_VOICE_TASK: 'approve' });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); });

// This tests the built product and real daemon/adapter with synthetic browser media/signaling.
// The separate voice-media test negotiates actual Chromium peers; neither uses a real microphone.
async function mediaFixture(context) {
  await context.addInitScript(() => {
    window.voiceTracks = [];
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async () => {
      if (window.voiceDeny) throw new DOMException('denied by test', 'NotAllowedError');
      const audio = new AudioContext();
      const stream = audio.createMediaStreamDestination().stream;
      window.voiceTracks.push(...stream.getTracks());
      return stream;
    } });
    class Peer extends EventTarget {
      connectionState = 'new';
      iceConnectionState = 'new';
      iceGatheringState = 'complete';
      localDescription = null;
      addTrack() {}
      createDataChannel() { return { readyState: 'open', close() {}, send() {} }; }
      async createOffer() { return { type: 'offer', sdp: 'fixture-offer' }; }
      async setLocalDescription(value) { this.localDescription = value; }
      async setRemoteDescription(value) {
        if (value.sdp !== 'fixture-answer') throw new Error('Unexpected test answer');
        this.connectionState = 'connected';
        this.dispatchEvent(new Event('connectionstatechange'));
      }
      close() { this.connectionState = 'closed'; }
    }
    window.RTCPeerConnection = Peer;
  });
}

async function threadPage(context, title, agent = 'voice-provider', server = daemon) {
  const response = await api(server, '/api/threads', { method: 'POST', body: JSON.stringify({
    agent, cwd: server.workdir, title,
  }) });
  assert.equal(response.status, 201);
  const thread = await response.json();
  const page = await context.newPage();
  await openUi(page, server);
  if (page.viewportSize().width < 600) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: title }).first().click();
  if (page.viewportSize().width < 600) await page.waitForFunction(() => document.querySelector('.main').getBoundingClientRect().left < 1);
  return { page, thread };
}

test('voice is opt-in; stopping voice preserves native approval and work on desktop/phone in both themes', { timeout: 120_000 }, async () => {
  const plain = await browser.newContext();
  try {
    const { page } = await threadPage(plain, '尚未安装语音');
    assert.equal(await page.getByRole('button', { name: '开启实时语音', exact: true }).count(), 0);
  } finally { await plain.close(); }
  const installed = await api(daemon, '/api/extensions/voice/install', {
    method: 'POST', body: JSON.stringify({ granted: ['voice.control'] }),
  });
  assert.equal(installed.status, 200, await installed.text());
  const textOnly = await browser.newContext();
  try {
    const { page } = await threadPage(textOnly, '仅文字提供方', 'text-provider');
    await page.getByRole('button', { name: '开启实时语音', exact: true }).waitFor();
  } finally { await textOnly.close(); }
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 },
      isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      await mediaFixture(context);
      const { page, thread } = await threadPage(context, `语音 ${mobile ? '手机' : '桌面'} ${colorScheme}`);
      if (process.env.CALLI_E2E_SHOTS_DIR) {
        mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `voice-idle-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      }
      await page.getByRole('button', { name: '开启实时语音', exact: true }).click();
      await page.locator('.voice-orb[data-phase="listening"], .voice-orb[data-phase="thinking"], .voice-orb[data-phase="speaking"]').waitFor();
      await page.getByRole('button', { name: '静音', exact: true }).click();
      assert.ok(await page.evaluate(() => window.voiceTracks.every(t => !t.enabled)));
      await page.getByRole('button', { name: '取消静音', exact: true }).click();
      assert.equal(await page.locator('.voice-composer textarea').first().getAttribute('placeholder'), '继续这个线程');
      const bar = page.locator('.voice-controls');
      await page.locator('.action-sheet').waitFor();
      const sheet = await page.locator('.action-sheet').boundingBox();
      const bounds = await bar.boundingBox();
      assert.ok(sheet && bounds && (sheet.y + sheet.height <= bounds.y + 1 || bounds.y + bounds.height <= sheet.y + 1),
        'voice controls and approval must not overlap');
      assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1);
      if (process.env.CALLI_E2E_SHOTS_DIR) {
        mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `voice-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      }
      await bar.getByRole('button', { name: '结束语音', exact: true }).click();
      await page.waitForFunction(() => window.voiceTracks.length > 0 && window.voiceTracks.every(t => t.readyState === 'ended'));
      const detail = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      assert.equal(detail.status, 'running', 'ending media must preserve pending native work');
      // Answer the actual native approval through the UI, then observe its result.
      await page.getByRole('button', { name: '允许这一次', exact: true }).click();
      await page.getByText(/approved:accept/, { exact: false }).first().waitFor();
    } finally { await context.close(); }
  }
});

test('Pi keeps its conversation while a visible Codex member provides voice in all four layouts', { timeout: 120_000 }, async () => {
  const server = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'speaker', adapter: 'codex', label: 'Codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')],
      env: { FAKE_CODEX_VOICE_TASK: 'voice-delegate', FAKE_CODEX_VOICE_RELAY: '1' } },
    { id: 'worker', adapter: 'pi', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-pi.mjs')] },
  ] }) });
  try {
    assert.equal((await api(server, '/api/extensions/voice/install', { method: 'POST', body: JSON.stringify({ granted: ['voice.control'] }) })).status, 200);
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 },
        isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        await mediaFixture(context);
        const title = `Pi 语音协作 ${mobile} ${colorScheme}`;
        const { page, thread } = await threadPage(context, title, 'worker', server);
        await page.getByRole('button', { name: '开启实时语音', exact: true }).click();
        await page.getByRole('button', { name: '语音详情', exact: true }).waitFor();
        await page.getByText('已收到成员结果，将继续检查并汇总。', { exact: true }).waitFor();
        assert.equal((await (await api(server, `/api/threads/${thread.id}`)).json()).agent, 'worker');
        const team = await (await api(server, `/api/threads/${thread.id}/coordination`)).json();
        assert.equal(team.members.length, 1); assert.equal(team.members[0].voiceTarget, thread.id);
        assert.equal(team.messages.length, 2);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        const bar = await page.locator('.voice-controls').boundingBox();
        assert.ok(bar.x >= 0 && bar.x + bar.width <= page.viewportSize().width + 1);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `voice-pi-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        await page.locator('.voice-controls').getByRole('button', { name: '结束语音', exact: true }).click();
        await page.waitForFunction(() => window.voiceTracks.every(t => t.readyState === 'ended'));
        assert.equal((await (await api(server, `/api/threads/${thread.id}/coordination`)).json()).messages.filter(m => m.kind === 'task').length, team.messages.filter(m => m.kind === 'task').length);
      } finally { await context.close(); }
    }
  } finally { await server.stop(); }
});

test('speech streams in the conversation and final Markdown survives ending and reloading', { timeout: 120_000 }, async () => {
  const server = await startDaemon({
    CALLI_AGENTS_JSON: JSON.stringify({ agents: [{
      id: 'voice-provider', adapter: 'codex',
      command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')],
    }] }),
    FAKE_CODEX_VOICE_TASK: 'voice-final-news', FAKE_CODEX_VOICE_CONVERSATION: '1',
    FAKE_CODEX_VOICE_TRANSCRIPT_DELAY: '1200',
  });
  try {
    assert.equal((await api(server, '/api/extensions/voice/install', {
      method: 'POST', body: JSON.stringify({ granted: ['voice.control'] }),
    })).status, 200);
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 },
        isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        await mediaFixture(context);
        const { page, thread } = await threadPage(context, `语音正文 ${mobile} ${colorScheme}`, 'voice-provider', server);
        await page.getByRole('button', { name: '开启实时语音', exact: true }).click();
        await page.locator('.row.user .bubble').filter({ hasText: '帮我整理最新的新闻。' }).waitFor();
        await page.getByText('能，我在听，你说。', { exact: true }).waitFor();
        await page.locator('.row.user .bubble').filter({ hasText: '帮我整理最新的科技新闻。' }).waitFor();
        assert.equal(await page.getByText('帮我整理最新的新闻。', { exact: true }).count(), 0);
        await page.getByRole('heading', { name: '科技新闻', exact: true }).waitFor();
        assert.equal(await page.locator('strong').filter({ hasText: '测试新闻标题' }).count(), 1);
        assert.equal(await page.getByRole('link', { name: '来源', exact: true }).getAttribute('href'), 'https://example.com/news');
        assert.equal(await page.getByText('Here are the newz', { exact: true }).count(), 0);
        assert.equal(await page.getByText('你能听到我说话吗？', { exact: true }).count(), 1);
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `voice-conversation-live-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        await page.locator('.voice-controls').getByRole('button', { name: '结束语音', exact: true }).click();
        await page.waitForFunction(() => window.voiceTracks.every(t => t.readyState === 'ended'));
        await page.getByRole('heading', { name: '科技新闻', exact: true }).waitFor();
        await page.reload();
        // Reload intentionally opens the new-thread view; reopen the original conversation.
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: `语音正文 ${mobile} ${colorScheme}` }).first().click();
        if (mobile) await page.waitForFunction(() => {
          const main = document.querySelector('.main');
          return main && Math.abs(main.getBoundingClientRect().left) < 1;
        });
        try { await page.getByRole('heading', { name: '科技新闻', exact: true }).waitFor({ timeout: 10_000 }); }
        catch (error) {
          if (process.env.CALLI_E2E_SHOTS_DIR) {
            await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, 'voice-reload-failure.png') });
            writeFileSync(join(process.env.CALLI_E2E_SHOTS_DIR, 'voice-reload-failure.json'), JSON.stringify({
              history: await (await api(server, `/api/threads/${thread.id}/history`)).json(),
              text: await page.locator('body').innerText(), url: page.url(),
            }, null, 2));
          }
          throw error;
        }
        assert.equal(await page.getByText('你能听到我说话吗？', { exact: true }).count(), 0);
        await page.locator('.voice-recap-toggle').click();
        await page.getByText('你能听到我说话吗？', { exact: true }).waitFor();
        assert.equal(await page.getByText('能，我在听，你说。', { exact: true }).count(), 1);
        assert.equal(await page.getByText('帮我整理最新的科技新闻。', { exact: true }).count(), 1);
        assert.equal(await page.locator('.voice-controls').count(), 0);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `voice-conversation-ended-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
      } finally { await context.close(); }
    }
  } finally { await server.stop(); }
});


test('microphone refusal is visible and disabling an extension releases media without approving work', { timeout: 30_000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    await mediaFixture(context);
    const { page, thread } = await threadPage(context, '语音权限与停用');
    await page.evaluate(() => { window.voiceDeny = true; });
    await page.getByRole('button', { name: '开启实时语音', exact: true }).click();
    await page.getByText('麦克风权限被拒绝，请在浏览器地址栏允许麦克风访问。', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.voiceTracks.length), 0);
    await page.evaluate(() => { window.voiceDeny = false; });
    await page.getByRole('button', { name: '重新连接', exact: true }).click();
    await page.locator('.voice-orb[data-phase="listening"], .voice-orb[data-phase="thinking"], .voice-orb[data-phase="speaking"]').waitFor();
    await page.locator('.action-sheet').waitFor();
    assert.equal((await api(daemon, '/api/extensions/voice', { method: 'PATCH', body: JSON.stringify({ enabled: false }) })).status, 200);
    await page.waitForFunction(() => window.voiceTracks.length === 1 && window.voiceTracks[0].readyState === 'ended');
    assert.equal((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status, 'running');
    assert.equal((await api(daemon, '/api/extensions/voice', { method: 'PATCH', body: JSON.stringify({ enabled: true }) })).status, 200);
    // Re-enabling permission does not reacquire the microphone or answer an approval.
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(() => window.voiceTracks.length), 1);
    assert.equal((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status, 'running');
  } finally { await context.close(); }
});

test('voice can start from a new thread: the thread is created first, then voice opens on it', { timeout: 30_000 }, async () => {
  await api(daemon, '/api/extensions/voice/install', { method: 'POST', body: JSON.stringify({ granted: ['voice.control'] }) });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    await mediaFixture(context);
    const page = await context.newPage();
    await openUi(page, daemon);
    const before = (await (await api(daemon, '/api/threads')).json()).length;
    // The draft's agent is the first one, the native voice provider.
    const shot = async name => { if (process.env.CALLI_E2E_SHOTS_DIR) {
      mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `voice-draft-${name}.png`) });
    } };
    await page.getByRole('button', { name: '开启实时语音', exact: true }).waitFor();
    await shot('before');
    await page.getByRole('button', { name: '开启实时语音', exact: true }).click();
    await page.locator('.voice-orb[data-phase="listening"], .voice-orb[data-phase="thinking"], .voice-orb[data-phase="speaking"]').waitFor();
    await shot('live');
    const threads = await (await api(daemon, '/api/threads')).json();
    assert.equal(threads.length, before + 1);
    assert.ok(threads.some(t => t.agent === 'voice-provider'));
    await page.locator('.voice-controls').getByRole('button', { name: '结束语音', exact: true }).click();
    await page.waitForFunction(() => window.voiceTracks.length > 0 && window.voiceTracks.every(t => t.readyState === 'ended'));
  } finally { await context.close(); }
});
