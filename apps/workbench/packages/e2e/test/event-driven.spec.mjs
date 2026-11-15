// Lark settings, source status and two-level signal disclosure use fictional browser mocks.
// The daemon has an isolated home and no usable Lark CLI; the empty list uses its actual route.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, launchBrowser, api, openUi, closeSettings } from '../harness.mjs';

const meetingEvents = ['vc.meeting.participant_meeting_ended_v1', 'vc.note.generated_v1', 'minutes.minute.generated_v1'];
const lark = { enabled: true, people: [], chats: [], intervalMin: 30, events: ['im.message.receive_v1', ...meetingEvents] };
const status = { cli: true, loggedIn: true, lastRunAt: null, nextRunAt: null, lastError: null, lastSignals: 0 };
// An absolute nonexistent executable cannot resolve to a real CLI on the ambient PATH.
const isolatedDaemon = () => startDaemon({ LOOCHAT_LARK_CLI: '/fictional-e2e-no-lark-cli' });
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const settle = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));

async function openDesk(page, daemon) {
  await openUi(page, daemon);
  // The desk is the drawer's own tab on the assistant's home; its address opens it directly.
  await page.evaluate(() => { location.hash = '#/desk/desk'; });
  await page.locator('.desk-summary').waitFor();
}

async function mockSource(page, channel = lark, channelStatus = status) {
  await page.route('**/api/personal/channels/lark', route => route.fulfill({ json: channel }));
  await page.route('**/api/personal/channels/lark/status', route => route.fulfill({ json: channelStatus }));
}

test('the Lark card says when events are stuck elsewhere, and folds what failed to load', async () => {
  const daemon = await isolatedDaemon();
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    let realtime = 'busy-elsewhere';
    await page.route('**/api/personal/channels/lark', route => route.request().method() === 'GET' ? route.fulfill({ json: lark }) : route.continue());
    await page.route('**/api/personal/channels/lark/status', route => route.fulfill({ json: { ...status, realtime, topicErrors: realtime !== 'busy-elsewhere' ? [] : [{ ref: 'omt_1', error: 'lark_read_failed' }] } }));
    await openUi(page, daemon);
    const open = async () => {
      await page.evaluate(() => { location.hash = '#/todos'; });
      await page.locator('.personal-summary').click();
      await page.getByRole('region', { name: '私人设置', exact: true }).getByRole('button', { name: '飞书', exact: true }).click();
      await page.locator('.lark-body').waitFor();
    };
    await open();
    await page.locator('.lark-realtime .personal-warning', { hasText: '事件通道被其他机器占用，10 分钟自动重试' }).waitFor();
    const toggle = page.getByRole('button', { name: '1 个话题拉取失败' });
    assert.equal(await page.locator('.lark-realtime li').count(), 0, 'folded until asked');
    await toggle.click();
    await page.locator('.lark-realtime li', { hasText: 'omt_1' }).getByText('lark_read_failed').waitFor();
    await page.locator('.lark-body .personal-talk-hint', { hasText: '多关注 XX 的消息' }).waitFor();
    await closeSettings(page);

    realtime = 'live';
    await page.reload();
    await page.locator('.sidebar-inner').waitFor({ state: 'attached' });
    await open();
    await page.locator('.lark-live', { hasText: '实时' }).waitFor();
    assert.equal(await page.locator('.lark-realtime .personal-warning').count(), 0);

    realtime = 'connecting';
    await page.reload();
    await page.locator('.sidebar-inner').waitFor({ state: 'attached' });
    await open();
    assert.equal(await page.locator('.lark-realtime').count(), 0, 'connecting passes in silence');
  } finally { await browser.close(); await daemon.stop(); }
});

test('a matter reads its received messages only when asked; failure can be retried and empty says so', async () => {
  const daemon = await isolatedDaemon();
  const browser = await launchBrowser();
  try {
    const post = async text => (await api(daemon, '/api/todos', { method: 'POST', body: JSON.stringify({ text, lane: 'you', source: { type: 'lark-task', ref: 'task-1' } }) })).json();
    const full = await post('有消息的事'), empty = await post('没消息的事');
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    let reads = 0;
    await page.route(`**/api/personal/signals?matterId=${full.id}`, route => {
      if (++reads === 1) return route.fulfill({ status: 500, json: { error: '读取失败' } });
      return route.fulfill({ json: { signals: [
        { key: 'a', at: new Date().toISOString(), summary: '老板问周报什么时候给', source: null, updatedAt: new Date().toISOString(), disposition: { outcome: 'delivered' } },
        { key: 'b', at: new Date().toISOString(), summary: '群里闲聊', source: null, updatedAt: new Date().toISOString(), disposition: { outcome: 'filtered', reason: 'not-a-request' } },
        { key: 'c', at: new Date().toISOString(), summary: '有人提到这件事', source: null, updatedAt: new Date().toISOString(), disposition: { outcome: 'router' } },
      ] } });
    });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk/desk'; });
    await page.getByRole('button', { name: '按进度', exact: true }).click();
    await page.locator(`#desk-pile-${full.id}`).click();
    const toggle = page.locator('.desk-signals .desk-subscriptions-toggle');
    await toggle.getByText('收到过的消息').waitFor();
    assert.equal(reads, 0, 'nothing is read until it opens');
    await toggle.click();
    const signals = page.locator('.desk-signals');
    await signals.getByRole('alert').waitFor();
    await signals.getByRole('button', { name: '重试', exact: true }).click();
    await signals.locator('li').first().waitFor();
    await toggle.getByText('收到过的消息 3').waitFor();
    const rows = await signals.locator('li').allTextContents();
    assert.match(rows[0], /老板问周报什么时候给.*已直投给工作线程/);
    assert.match(rows[1], /群里闲聊.*被过滤：不是在找你/);
    assert.match(rows[2], /有人提到这件事.*送了助手/);
    await toggle.click();
    await toggle.click();
    assert.equal(reads, 2, 'reopening does not read again');
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });

    await page.locator(`#desk-pile-${empty.id}`).click();
    await page.locator('.desk-signals .desk-subscriptions-toggle').click();
    await page.locator('.desk-signals').getByText('还没有收到过消息').waitFor();
  } finally { await browser.close(); await daemon.stop(); }
});

test('post-meeting settings edit only the draft, preserve unrelated events and keep unknown lists unknown', async () => {
  const daemon = await isolatedDaemon();
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const unrelated = ['im.message.receive_v1', 'fictional.other_v1', 'fictional.other_v1'];
    const initial = { ...lark, enabled: false, events: [unrelated[0], meetingEvents[1], ...unrelated.slice(1)] };
    let channel = structuredClone(initial);
    const writes = [];
    let personalWrites = 0;
    const personal = { directory: '/fictional-workspace', directoryDisplay: 'Fictional workspace', agent: 'pi', model: null };
    await page.route('**/api/personal/settings', route => {
      if (route.request().method() === 'PUT') ++personalWrites;
      return route.fulfill({ json: personal });
    });
    await page.route('**/api/agents/*/models', route => route.fulfill({ json: [{ id: 'fictional/plain', label: 'Fictional plain' }] }));
    await page.route('**/api/personal/channels/lark', route => {
      if (route.request().method() === 'PUT') {
        channel = route.request().postDataJSON();
        writes.push(structuredClone(channel));
      }
      return route.fulfill({ json: channel });
    });
    await page.route('**/api/personal/channels/lark/status', route => route.fulfill({ json: { ...status, realtime: 'live' } }));
    await openDesk(page, daemon);
    await page.locator('.desk-source-state.off').waitFor();
    const open = async () => {
      await page.locator('.desk-summary').getByRole('button', { name: '配置', exact: true }).click();
      await page.getByRole('region', { name: '私人设置', exact: true }).getByRole('button', { name: '飞书', exact: true }).click();
      const dialog = page.getByRole('region', { name: '飞书', exact: true });
      await dialog.locator('.lark-meeting-events').waitFor();
      return dialog;
    };
    let dialog = await open();
    const meeting = () => dialog.getByRole('checkbox', { name: '会后跟进', exact: true });
    const global = () => dialog.getByRole('switch', { name: '飞书', exact: true });
    assert.equal(await meeting().getAttribute('aria-checked'), 'mixed');
    await dialog.locator('.lark-meeting-events small', { hasText: '只配置了部分会后事件' }).waitFor();
    await meeting().click();
    assert.equal(await meeting().getAttribute('aria-checked'), 'true');
    assert.equal(await global().getAttribute('aria-checked'), 'false');
    // The feature page rerenders on every draft update; later keyboard edits must still win.
    await meeting().focus();
    await page.keyboard.press('Space');
    assert.equal(await meeting().getAttribute('aria-checked'), 'false');
    assert.equal(await global().getAttribute('aria-checked'), 'false');
    assert.equal(writes.length, 0, 'toggling never writes immediately');
    assert.equal(personalWrites, 0);
    await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
    await page.getByRole('dialog', { name: '保留这次修改？', exact: true }).getByRole('button', { name: '放弃修改', exact: true }).click();
    await closeSettings(page);
    await dialog.waitFor({ state: 'detached' });
    assert.deepEqual(channel, initial, 'Cancel leaves the saved list untouched');

    dialog = await open();
    assert.equal(await meeting().getAttribute('aria-checked'), 'mixed');
    await meeting().click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.getByRole('status').filter({ hasText: '已保存' }).waitFor();
    await closeSettings(page);
    await dialog.waitFor({ state: 'detached' });
    assert.equal(personalWrites, 0, 'saving a feature does not rewrite the assistant configuration');
    assert.deepEqual(writes, [{ ...initial, events: [...initial.events, meetingEvents[0], meetingEvents[2]] }]);
    assert.equal(channel.enabled, false, 'configuring meeting events never enables the source');
    await page.locator('.desk-source-state.off').waitFor();

    dialog = await open();
    assert.equal(await meeting().getAttribute('aria-checked'), 'true');
    await meeting().click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.getByRole('status').filter({ hasText: '已保存' }).waitFor();
    await closeSettings(page);
    await dialog.waitFor({ state: 'detached' });
    assert.equal(writes.length, 2);
    assert.deepEqual(channel.events, unrelated, 'disabling removes only the three owned event keys');
    assert.equal(channel.enabled, false);

    channel = { ...lark, enabled: false };
    delete channel.events;
    await page.reload();
    await page.locator('.desk-source-state.off').waitFor();
    dialog = await open();
    assert.equal(await meeting().getAttribute('aria-checked'), 'mixed');
    assert.equal(await meeting().isDisabled(), true);
    await dialog.locator('.lark-meeting-events small', { hasText: '旧版 hub 未提供事件列表' }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '保存', exact: true }).isDisabled(), true);
    await closeSettings(page);
    assert.equal(writes.length, 2, 'an unknown event list cannot be overwritten');
    assert.equal(Object.hasOwn(channel, 'events'), false);
  } finally { await browser.close(); await daemon.stop(); }
});

test('desk source states distinguish loading, disabled, unknown and failed reads, including mobile disclosure', async () => {
  const daemon = await isolatedDaemon();
  const browser = await launchBrowser();
  const loading = deferred();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    let channel = { ...lark, enabled: false };
    let channelStatus = null;
    let failed = false;
    let hold = true;
    let settingsReads = 0;
    let writes = 0;
    let runs = 0;
    await page.route('**/api/personal/channels/lark', async route => {
      if (route.request().method() !== 'GET') ++writes;
      ++settingsReads;
      if (hold) await loading.promise;
      return route.fulfill({ json: channel });
    });
    await page.route('**/api/personal/channels/lark/status', route => route.fulfill(failed
      ? { status: 503, json: { error: 'fictional-source-read-failed' } }
      : { json: channelStatus }));
    await page.route('**/api/personal/channels/lark/run', route => {
      ++runs;
      return route.fulfill({ json: status });
    });
    await openDesk(page, daemon);
    await page.locator('.desk-source-state.loading', { hasText: '飞书：加载中…' }).waitFor();
    hold = false;
    loading.resolve();
    const off = page.locator('.desk-source-state.off');
    await off.getByText('飞书未启用', { exact: true }).waitFor();
    assert.equal(await off.evaluate(element => element.classList.contains('problem')), false);
    await off.getByRole('button').click();
    await page.getByRole('region', { name: '私人设置', exact: true }).getByRole('button', { name: '飞书', exact: true }).click();
    await page.locator('.lark-meeting-events').waitFor();
    await closeSettings(page);

    channel = lark;
    await page.reload();
    await page.locator('.desk-source-state.unknown', { hasText: '飞书：状态未知' }).waitFor();
    assert.equal(await page.locator('.desk-source-state.off').count(), 0, 'null status is not a known disabled source');

    failed = true;
    await page.reload();
    const error = page.locator('.desk-lark.problem[role="alert"]');
    await error.getByText('飞书：状态读取失败', { exact: true }).waitFor();
    assert.equal(await error.getAttribute('title'), 'fictional-source-read-failed');
    const beforeRetry = settingsReads;
    failed = false;
    channelStatus = status;
    channel = { ...lark, events: [] };
    await error.getByRole('button', { name: '重试', exact: true }).click();
    await page.locator('.desk-lark-meeting.problem', { hasText: '会后事件未配置' }).waitFor();
    assert.equal(settingsReads, beforeRetry + 1, 'status retry reloads configuration');
    assert.equal(runs, 0, 'retrying a failed status GET does not run Lark');

    channel = { ...lark, events: [meetingEvents[0]] };
    await page.reload();
    await page.locator('.desk-lark-meeting.problem', { hasText: '会后事件未配齐' }).waitFor();
    channel = { ...lark };
    delete channel.events;
    await page.reload();
    const unknownCoverage = page.locator('.desk-lark-meeting', { hasText: '会后配置未知' });
    await unknownCoverage.waitFor();
    assert.equal(await unknownCoverage.evaluate(element => element.classList.contains('problem')), false);

    channel = { ...lark, enabled: false };
    channelStatus = null;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.locator('.desk-summary .desk-meta-more').waitFor();
    assert.equal(await page.locator('.desk-summary .desk-source-state').count(), 0, 'quiet states fold on a phone');
    await page.getByRole('button', { name: '更多状态', exact: true }).click();
    const mobileOff = page.locator('.desk-meta-list .desk-source-state.off');
    await mobileOff.getByText('飞书未启用', { exact: true }).waitFor();
    await mobileOff.getByRole('button').click();
    await page.getByRole('region', { name: '私人设置', exact: true }).getByRole('button', { name: '飞书', exact: true }).click();
    await page.locator('.lark-meeting-events').waitFor();
    assert.equal(writes, 0);
    assert.equal(runs, 0);
  } finally { loading.resolve(); await browser.close(); await daemon.stop(); }
});

test('meeting materials are a second lazy read, with explicit retry, bounded text and distinct partial-empty semantics', async () => {
  const daemon = await isolatedDaemon();
  const browser = await launchBrowser();
  const listGate = deferred(), waitingGate = deferred();
  try {
    const response = await api(daemon, '/api/todos', { method: 'POST', body: JSON.stringify({ text: '虚构会后跟进', lane: 'you' }) });
    assert.ok(response.ok);
    const matter = await response.json();
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await mockSource(page);
    let untrustedRequests = 0;
    await page.route('https://untrusted.example.test/**', route => { ++untrustedRequests; return route.abort(); });
    const keys = {
      waiting: 'fictional:meeting/1?note=%23#fragment',
      http: 'fictional:http-failure', failed: 'fictional:failed-note',
      empty: 'fictional:empty-minutes', partial: 'fictional:partial-note',
      partialEmpty: 'fictional:partial-empty', chat: 'fictional:chat',
    };
    const meeting = (key, summary, material, event = 'note-generated') => ({
      key, summary, at: '2026-01-02T09:00:00.000Z', disposition: { outcome: 'router' },
      source: { type: 'lark-note', ref: key, from: 'Fictional meeting', url: 'https://meeting.example.test/fictional-note' },
      meeting: { event, title: summary }, material,
    });
    const signals = [
      meeting(keys.waiting, '虚构纪要等待生成', { state: 'not-ready', kind: 'note' }, 'ended'),
      meeting(keys.http, '虚构资料网络失败', { state: 'unread' }),
      meeting(keys.failed, '虚构资料读取失败', { state: 'failed', error: 'fictional-note-service-error' }),
      meeting(keys.empty, '虚构空白妙记', { state: 'unread' }, 'minutes-generated'),
      meeting(keys.partial, '虚构有限范围纪要', { state: 'ready', kind: 'note', partial: true }),
      meeting(keys.partialEmpty, '虚构本次无匹配内容', { state: 'unread' }),
      { key: keys.chat, summary: '虚构普通聊天', at: '2026-01-02T09:01:00.000Z', source: null, disposition: { outcome: 'delivered' } },
    ];
    const reply = (key, state, content = null, extra = {}) => ({ json: { key, material: { state, ...extra }, content } });
    const hostileBody = '# Fictional bounded note\n\n- Follow up with the fictional team\n\n'
      + '[Untrusted link](https://untrusted.example.test/link)\n\n![Fictional image](https://untrusted.example.test/image)\n\n'
      + '<script>window.__fictionalMaterialExecuted = true;</script>\n\n'
      + '<img src="https://untrusted.example.test/html-image" onerror="window.__fictionalMaterialExecuted = true">\n\n'
      + 'x'.repeat(32_000) + '\n\nFictional truncated tail';
    const replies = new Map([
      [keys.waiting, [reply(keys.waiting, 'not-ready', null, { kind: 'note' }), reply(keys.waiting, 'ready', '# Fictional generated note\n\n- Follow up tomorrow', { kind: 'note', partial: false })]],
      [keys.http, [{ status: 503, json: { error: 'fictional-http-read-failed' } }, reply(keys.http, 'ready', '# Fictional retry succeeded')]],
      [keys.failed, [reply(keys.failed, 'failed', null, { error: 'fictional-note-service-error' }), reply(keys.failed, 'ready', '# Fictional service recovered')]],
      [keys.empty, [reply(keys.empty, 'empty', null, { kind: 'minutes', partial: false })]],
      [keys.partial, [reply(keys.partial, 'ready', hostileBody, { kind: 'note', partial: true })]],
      [keys.partialEmpty, [reply(keys.partialEmpty, 'empty', null, { kind: 'note', partial: true })]],
    ]);
    let listReads = 0;
    const materialReads = new Map();
    const materialPaths = [];
    await page.route(`**/api/personal/signals?matterId=${matter.id}`, async route => {
      ++listReads;
      await listGate.promise;
      return route.fulfill({ json: { signals } });
    });
    await page.route('**/api/personal/signals/*/material', async route => {
      const path = new URL(route.request().url()).pathname;
      const key = decodeURIComponent(path.split('/').at(-2));
      const count = (materialReads.get(key) ?? 0) + 1;
      materialReads.set(key, count);
      materialPaths.push(path);
      assert.equal(route.request().method(), 'GET');
      if (key === keys.waiting && count === 1) await waitingGate.promise;
      const results = replies.get(key);
      assert.ok(results, 'only fictional meeting keys may be read');
      return route.fulfill(results[Math.min(count - 1, results.length - 1)]);
    });
    await openDesk(page, daemon);
    await page.evaluate(() => { window.__fictionalMaterialExecuted = false; });
    await page.getByRole('button', { name: '按进度', exact: true }).click();
    await page.locator(`#desk-pile-${matter.id}`).click();
    const list = page.locator('.desk-signals');
    const toggle = list.locator('.desk-subscriptions-toggle');
    await toggle.waitFor();
    assert.equal(listReads, 0, 'opening a source-less matter does not fetch its signal list');
    assert.equal(materialReads.size, 0);
    await toggle.click();
    await list.getByText('正在加载…', { exact: true }).waitFor();
    await toggle.click();
    await toggle.click();
    await settle(page);
    assert.equal(listReads, 1, 'list requests are single-flight while loading');
    listGate.resolve();
    await list.locator('.desk-signal').last().waitFor();
    assert.equal(await list.locator('.desk-signal').count(), 7);
    assert.equal(materialReads.size, 0, 'list metadata never starts material requests');
    const row = key => list.locator(`.desk-signal[data-signal-key="${key}"]`);
    const waiting = row(keys.waiting), failed = row(keys.failed);
    await waiting.locator('.desk-material-state.problem', { hasText: '资料尚未就绪' }).waitFor();
    await failed.locator('.desk-material-state.problem', { hasText: '资料读取失败' }).waitFor();
    assert.equal(await list.locator('.desk-signal-material').count(), 0);
    assert.equal(await row(keys.chat).locator('.desk-signal-material-toggle').count(), 0, 'non-meeting messages have no material action');
    await row(keys.partial).locator('.desk-material-state', { hasText: '资料可读取' }).waitFor();
    await row(keys.partial).getByText('部分读取', { exact: true }).waitFor();

    const waitingToggle = waiting.locator('.desk-signal-material-toggle');
    await waitingToggle.click();
    await waiting.locator('.desk-signal-material').getByText('资料读取中…', { exact: true }).waitFor();
    await waitingToggle.click();
    await waitingToggle.click();
    await waitingToggle.click();
    await settle(page);
    assert.equal(materialReads.get(keys.waiting), 1, 'reopening a pending read does not duplicate it');
    waitingGate.resolve();
    await waiting.locator('.desk-material-state', { hasText: '资料尚未就绪' }).waitFor();
    await settle(page);
    assert.equal(await waiting.locator('.desk-signal-material').count(), 0, 'a late result does not force a closed panel open');
    await waitingToggle.click();
    await waiting.getByText('资料还没生成好，稍后可重新读取。', { exact: true }).waitFor();
    assert.equal(materialReads.get(keys.waiting), 1);
    await waiting.getByRole('button', { name: '重新读取', exact: true }).click();
    await waiting.locator('.desk-material-text').getByRole('heading', { name: 'Fictional generated note', exact: true }).waitFor();
    assert.equal(materialReads.get(keys.waiting), 2, 'a waiting read retries only through the explicit action');
    assert.equal(materialPaths[0], `/api/personal/signals/${encodeURIComponent(keys.waiting)}/material`);
    assert.equal(await waiting.locator('.desk-material-retry').count(), 0, 'complete ready material is final');
    await waitingToggle.click();
    await waitingToggle.click();
    assert.equal(materialReads.get(keys.waiting), 2, 'a successful read survives collapse and reopen');

    for (const [key, message, heading] of [
      [keys.http, 'fictional-http-read-failed', 'Fictional retry succeeded'],
      [keys.failed, 'fictional-note-service-error', 'Fictional service recovered'],
    ]) {
      const item = row(key);
      await item.locator('.desk-signal-material-toggle').click();
      await item.getByRole('alert').getByText(message, { exact: true }).waitFor();
      await item.locator('.desk-material-state.problem', { hasText: '资料读取失败' }).waitFor();
      assert.equal(await item.getByText('已读取，没有内容', { exact: true }).count(), 0, 'failure must not become an empty read');
      await item.locator('.desk-signal-material-toggle').click();
      await item.locator('.desk-signal-material-toggle').click();
      assert.equal(materialReads.get(key), 1, 'reopening an error is not an implicit retry');
      await item.getByRole('button', { name: '重试', exact: true }).click();
      await item.getByRole('heading', { name: heading, exact: true }).waitFor();
      assert.equal(materialReads.get(key), 2);
    }

    const empty = row(keys.empty);
    await empty.locator('.desk-signal-material-toggle').click();
    await empty.locator('.desk-signal-material').getByText('已读取，没有内容', { exact: true }).waitFor();
    assert.equal(await empty.locator('.desk-material-partial-mark').count(), 0);
    await empty.locator('.desk-signal-material-toggle').click();
    await empty.locator('.desk-signal-material-toggle').click();
    assert.equal(materialReads.get(keys.empty), 1, 'completed empty reads have a component-memory result');
    await empty.getByRole('button', { name: '重新读取', exact: true }).click();
    await empty.locator('.desk-signal-material').getByText('已读取，没有内容', { exact: true }).waitFor();
    assert.equal(materialReads.get(keys.empty), 2, 'an empty result can explicitly be read again');

    const partial = row(keys.partial);
    await partial.locator('.desk-signal-material-toggle').click();
    const body = partial.locator('.desk-material-text');
    await body.getByRole('heading', { name: 'Fictional bounded note', exact: true }).waitFor();
    await body.getByText('Fictional image', { exact: true }).waitFor();
    assert.equal(await body.locator('script, img, a, input, iframe').count(), 0, 'untrusted material has no executable HTML, remote images or body links');
    assert.equal(await body.getByText('Fictional truncated tail', { exact: false }).count(), 0, 'only bounded text is constructed');
    assert.equal(await page.evaluate(() => window.__fictionalMaterialExecuted), false);
    assert.equal(untrustedRequests, 0);
    const source = partial.getByRole('link', { name: '打开原文', exact: true });
    assert.equal(await source.getAttribute('href'), 'https://meeting.example.test/fictional-note');
    assert.equal(await source.getAttribute('rel'), 'noopener noreferrer');
    await partial.getByText('仅展示本次有限范围的读取，不是整份文档。', { exact: true }).waitFor();
    await partial.locator('.desk-signal-material-toggle').click();
    await partial.locator('.desk-signal-material-toggle').click();
    assert.equal(materialReads.get(keys.partial), 1);
    await partial.getByRole('button', { name: '重新读取', exact: true }).click();
    await body.getByRole('heading', { name: 'Fictional bounded note', exact: true }).waitFor();
    assert.equal(materialReads.get(keys.partial), 2);

    const partialEmpty = row(keys.partialEmpty);
    await partialEmpty.locator('.desk-signal-material-toggle').click();
    await partialEmpty.locator('.desk-material-state', { hasText: '本次未找到匹配内容' }).waitFor();
    await partialEmpty.getByText('本次读取未找到匹配内容，不代表整份文档没有待办。', { exact: true }).waitFor();
    assert.equal(await partialEmpty.getByText('已读取，没有内容', { exact: true }).count(), 0);
    await partialEmpty.getByText('部分读取', { exact: true }).waitFor();
    await partialEmpty.getByRole('button', { name: '重新读取', exact: true }).click();
    await partialEmpty.getByText('本次读取未找到匹配内容，不代表整份文档没有待办。', { exact: true }).waitFor();
    assert.equal(materialReads.get(keys.partialEmpty), 2);
    await toggle.click();
    await toggle.click();
    await waiting.getByRole('heading', { name: 'Fictional generated note', exact: true }).waitFor();
    assert.equal(listReads, 1);
    assert.equal([...materialReads.values()].reduce((sum, count) => sum + count, 0), 12, 'list disclosure reuses every material result');
    assert.deepEqual(errors, []);
  } finally { listGate.resolve(); waitingGate.resolve(); await browser.close(); await daemon.stop(); }
});
