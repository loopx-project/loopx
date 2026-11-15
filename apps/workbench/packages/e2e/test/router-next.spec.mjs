// Observe mode through the real backend. The batch and the stages are scripted onto real responses:
// Lark and worker arrivals have no HTTP way in, and the daemon's own tests cover how they merge.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, launchBrowser, api, openUi } from '../harness.mjs';

const json = async (daemon, path, method = 'GET', body) => {
  const response = await api(daemon, path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
  assert.ok(response.ok, `${method} ${path}: ${response.status}`);
  return response.status === 204 ? null : response.json();
};
const middayZone = () => {
  const offset = 12 - new Date().getUTCHours();
  return offset === 0 ? 'Etc/GMT' : `Etc/GMT${offset > 0 ? '-' : '+'}${Math.abs(offset)}`;
};
const waitFor = async (check, what, ms = 60_000) => {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await check();
    if (value) return value;
    assert.ok(Date.now() < deadline, `timed out: ${what}`);
    await new Promise(resolve => setTimeout(resolve, 300));
  }
};

test('observe mode only records; the record can be approved once, in the spread and the filter', async () => {
  const daemon = await startDaemon({ TZ: middayZone() });
  const browser = await launchBrowser();
  try {
    const todo = await json(daemon, '/api/todos', 'POST', { text: '周五的评审会', lane: 'you', importance: 'high', dueAt: new Date(Date.now() + 864e5).toISOString() });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/todos'; });
    await page.locator('.personal-summary').click();
    const mode = name => page.locator('.proactive-mode').getByRole('button', { name, exact: true });
    await mode('观察').click();
    await page.getByText('只记下她本来会做什么').waitFor();
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.locator('.personal-settings').waitFor({ state: 'detached' });
    assert.equal((await json(daemon, '/api/personal/proactive')).settings.mode, 'observe');

    const run = await waitFor(async () => (await json(daemon, '/api/personal/proactive')).runs[0], 'an observation', 60_000);
    assert.equal(run.status, 'observed');
    assert.equal(run.threadId, '', 'nothing really ran');
    await page.evaluate(() => { location.hash = '#/desk/desk'; });
    await page.getByRole('button', { name: '按进度', exact: true }).click();
    await page.locator(`#desk-pile-${todo.id}`).click();
    const card = page.locator(`.proactive-card[data-run="${run.id}"]`);
    await card.getByText(run.summary).waitFor();
    await card.getByRole('button', { name: '这条可以', exact: true }).click();
    await card.getByText('已认可').waitFor();
    assert.equal(await card.getByRole('button').count(), 0);
    assert.equal((await json(daemon, '/api/personal/proactive')).runs[0].status, 'approved');
    assert.equal((await api(daemon, `/api/personal/proactive/runs/${run.id}/approve`, { method: 'POST' })).status, 409);
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });
    await page.locator('.desk-filter').click();
    await page.locator(`.proactive-list [data-matter="${todo.id}"]`).getByText('已认可').waitFor();
  } finally { await browser.close(); await daemon.stop(); }
});

test('a merged batch reads part by part, while queued and in the conversation', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const { threadId } = await json(daemon, '/api/personal/say', 'POST', { text: '先记下周三的会' });
    await waitFor(async () => (await json(daemon, `/api/threads/${threadId}`)).status === 'idle', 'the Router turn');
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const parts = [{ kind: 'lark', count: 2 }, { kind: 'person', text: '先记下周三的会' }, { kind: 'worker', count: 1 }, { kind: 'person', text: '还有一件：买票' }];
    const channel = { kind: 'mixed', count: 5, parts };
    const enrich = value => {
      if (Array.isArray(value)) value.forEach(enrich);
      else if (value && typeof value === 'object') {
        if (value.type === 'turn.started' && value.data?.displayText === '先记下周三的会') value.data.channel = channel;
        if (value.id === threadId && Array.isArray(value.queued)) value.queued = [{ id: 'q1', text: parts.map(part => part.text ?? '').join(' '), images: 0, videos: 0, channel }];
        Object.values(value).forEach(enrich);
      }
      return value;
    };
    await page.route(/\/api\/threads(\/|\?|$)/, async route => {
      if (route.request().method() !== 'GET' || route.request().url().includes('/stream')) return route.continue();
      const response = await route.fetch();
      const body = await response.text();
      let parsed; try { parsed = JSON.parse(body); } catch { return route.fulfill({ response }); }
      return route.fulfill({ response, json: enrich(parsed) });
    });
    await openUi(page, daemon);
    // Her home is the conversation itself now; no button between the desk and the queue.
    await page.evaluate(() => { location.hash = '#/desk'; });
    const queue = page.locator('.queue-text').first();
    await queue.waitFor();
    const shown = await queue.evaluate(node => ({ text: node.textContent, quiet: [...node.querySelectorAll('.muted')].map(part => part.textContent.replace(/^ · /, '')) }));
    assert.match(shown.text, /飞书 · 2 条新信号 · 先记下周三的会 · 工作 · 1 条进展 · 还有一件：买票/);
    assert.deepEqual(shown.quiet, ['飞书 · 2 条新信号', '工作 · 1 条进展'], 'a person\'s words are not among the quiet parts');
    await page.locator('.row.channel-note', { hasText: '工作 · 1 条进展' }).waitFor();
    const rows = await page.locator('.row.channel-note, .row.user').evaluateAll(list => list.map(row => [row.classList.contains('user') ? 'bubble' : 'note', row.textContent.trim()]));
    const batch = rows.slice(rows.findIndex(([, text]) => text.includes('飞书 · 2')));
    assert.deepEqual(batch.slice(0, 4), [['note', '飞书 · 2 条新信号'], ['bubble', '先记下周三的会'], ['note', '工作 · 1 条进展'], ['bubble', '还有一件：买票']]);
  } finally { await browser.close(); await daemon.stop(); }
});

test('the desk says where matters stand, in the head and on each pile', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const stages = { answer: await json(daemon, '/api/todos', 'POST', { text: '要回答的事', lane: 'you' }), working: await json(daemon, '/api/todos', 'POST', { text: '在做的事', lane: 'you' }), quiet: await json(daemon, '/api/todos', 'POST', { text: '没动静的事', lane: 'you' }) };
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.route(/\/api\/todos$/, async route => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      const named = { [stages.answer.id]: 'answer', [stages.working.id]: 'working', [stages.quiet.id]: null };
      return route.fulfill({ response, json: (await response.json()).map(todo => ({ ...todo, stage: named[todo.id] ?? null, ...(todo.id === stages.quiet.id ? { subscriptions: [{ kind: 'lark-task', ref: 'task-1', at: new Date().toISOString() }] } : {}) })) });
    });
    const sent = [];
    await page.route(`**/api/todos/${stages.quiet.id}`, async route => { if (route.request().method() !== 'GET') sent.push(route.request().postDataJSON()); return route.continue(); });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk/desk'; });
    await page.getByRole('button', { name: '按进度', exact: true }).click();
    await page.locator('.desk-summary, .desk-header').getByText('1 件需要你').waitFor();
    const answer = page.locator(`#desk-pile-${stages.answer.id} .desk-cover-meta .personal-warning`, { hasText: '等你回答' });
    await answer.waitFor();
    await page.locator(`#desk-pile-${stages.working.id} .desk-cover-meta`, { hasText: '进行中' }).waitFor();
    assert.equal(await page.locator(`#desk-pile-${stages.working.id} .personal-warning`).count(), 0, 'in progress asks nothing of anyone');
    assert.doesNotMatch(await page.locator(`#desk-pile-${stages.quiet.id}`).textContent(), /等|进行中|受阻/);
    await page.locator(`#desk-pile-${stages.quiet.id}`).click();
    const toggle = page.locator('.desk-subscriptions:not(.desk-signals) .desk-subscriptions-toggle');
    await toggle.getByText('在跟的来源 1').waitFor();
    assert.equal(await page.locator('.desk-subscriptions:not(.desk-signals) li').count(), 0, 'folded until asked');
    await toggle.click();
    await page.locator('.desk-subscriptions li', { hasText: '飞书任务' }).getByRole('button', { name: '退订', exact: true }).click();
    await waitFor(() => sent.length > 0, 'the unsubscribe', 5000);
    assert.deepEqual(sent[0], { unsubscribe: [{ kind: 'lark-task', ref: 'task-1' }] });
  } finally { await browser.close(); await daemon.stop(); }
});
