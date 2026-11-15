import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { AddressInfo } from 'node:net';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { AttachmentStore } from '../src/attachments.js';
import { PersonalExplore, type ExploreStatus } from '../src/personal-explore.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { createHttpServer } from '../src/http.js';

// The explore reads Lark through the readonly fake CLI and judges through the fake pi agent;
// the fixture reply is steered per test with FAKE_PI_EXPLORE_* environment variables.
const wait = async (fn: () => boolean, timeoutMs = 20_000): Promise<void> => {
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > timeoutMs) throw new Error('explore did not settle');
    await new Promise(resolve => setTimeout(resolve, 20));
  }
};
const ALL = ['lark-profile', 'lark-chats', 'lark-messages', 'lark-calendar', 'calli-threads'];

/** Three days of own messages: first at 09:10–09:50, last at 19:10–20:20, plus @-me group rows. */
const message = (id: string, chat: string, name: string, at: string, content: string) =>
  ({ message_id: id, chat_id: chat, chat_name: name, chat_type: chat.startsWith('oc_grp') ? 'group' : 'p2p', create_time: at, sender: { id: 'ou_self' }, content });
const atMe = (id: string, chat: string, name: string, at: string, content: string, sender: string) =>
  ({ message_id: id, chat_id: chat, chat_name: name, chat_type: 'group', create_time: at, sender: { id: sender }, content, mentions: [{ id: 'ou_self' }] });

const larkData = {
  // Where the directory exposes a leader, the self lookup carries it and the boss rule fires.
  self: { open_id: 'ou_self', name: '张三', leader_user_id: 'ou_boss' },
  contacts: { ou_boss: { open_id: 'ou_boss', name: '王磊', department: '内容 AI 平台' } },
  chatList: [
    { chat_id: 'oc_p2p_pm', chat_mode: 'p2p', name: '陈思', p2p_target_id: 'ou_pm', p2p_target_type: 'user' },
    { chat_id: 'oc_p2p_dev', chat_mode: 'p2p', name: '李想', p2p_target_id: 'ou_dev', p2p_target_type: 'user' },
    { chat_id: 'oc_p2p_bot', chat_mode: 'p2p', name: '报警机器人', p2p_target_id: 'ou_bot', p2p_target_type: 'bot' },
    { chat_id: 'oc_grp_duty', chat_mode: 'group', name: '客服 Agent 值班群' },
    { chat_id: 'oc_grp_big', chat_mode: 'group', name: '电商 AI 大群' },
  ],
  messages: [
    message('m1', 'oc_p2p_pm', '陈思', '2026-11-02 09:10', '转人工的需求今天能看吗'),
    message('m2', 'oc_p2p_pm', '陈思', '2026-11-02 19:10', 'PPE 回归又挂了，帮我看看'),
    message('m3', 'oc_p2p_dev', '李想', '2026-11-03 09:50', '周报的数字我核一下'),
    message('m4', 'oc_grp_duty', '客服 Agent 值班群', '2026-11-03 20:20', '值班表更新了'),
    message('m5', 'oc_p2p_pm', '陈思', '2026-11-04 09:20', '转人工率的目标定多少'),
    message('m6', 'oc_grp_big', '电商 AI 大群', '2026-11-04 19:50', '这个群的转人工讨论不错'),
  ],
  atMeMessages: [
    atMe('a1', 'oc_grp_duty', '客服 Agent 值班群', '2026-11-02 11:00', '@张三 线上告警看一下', 'ou_watch'),
    atMe('a2', 'oc_grp_duty', '客服 Agent 值班群', '2026-11-03 12:00', '@张三 修一下告警规则', 'ou_pm'),
    atMe('a3', 'oc_grp_duty', '客服 Agent 值班群', '2026-11-04 13:00', '@张三 转人工率掉了', 'ou_watch'),
    atMe('a4', 'oc_grp_big', '电商 AI 大群', '2026-11-04 14:00', '@张三 来看下', 'ou_other'),
  ],
  events: [
    { event_id: 'e1', summary: '周一组会', recurring_event_id: 'r1', start_time: { datetime: '2026-11-09T02:00:00Z' }, end_time: { datetime: '2026-11-09T03:00:00Z' } },
    { event_id: 'e2', summary: '临时评审', start_time: { datetime: '2026-11-12T06:00:00Z' }, end_time: { datetime: '2026-11-12T07:00:00Z' } },
  ],
};

/** The canned judgement tests inject; ids match larkData, ou_ghost does not exist in the facts. */
const modelReply = JSON.stringify({
  peopleRoles: [{ id: 'ou_dev', role: 'pm' }, { id: 'ou_ghost', role: 'pm' }],
  why: { people: { ou_pm: '常提需求和排期，像个 PM' }, chats: { oc_grp_duty: '值班群里总找你' }, brief: '按你开聊和收工的时间推的', profile: '从你的会话和日程归纳' },
  profileDraft: '# 我的说明\n\n## 我在意什么\n转人工率、PPE 回归。\n\n## 老板和 PM\n王磊（上级）；陈思（PM）。\n',
});

interface Handle {
  request: (path: string, body?: unknown, method?: string) => Promise<Response>;
  store: Store;
  manager: ThreadManager;
  cwd: string;
  seedThread: (title: string, texts: string[]) => string;
  close(): Promise<void>;
}

async function setup(lark: object | null, options: Record<string, unknown> = {}): Promise<Handle> {
  const dir = mkdtempSync(join(tmpdir(), 'calli-explore-'));
  const cwd = join(dir, 'personal'); mkdirSync(cwd);
  const data = lark === null ? null : join(dir, 'lark.json');
  if (data) writeFileSync(data, JSON.stringify({ ...lark, explore: true }));
  const store = new Store(dir);
  const adapters = new Map([['pi', new PiRpcAdapter([process.execPath, resolve('test/fixtures/fake-pi.mjs')])]]);
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'roots'), join(dir, 'scratch')), new AttachmentStore(dir));
  manager.personal.putSettings({ directory: cwd, agent: 'pi', model: null });
  const explore = new PersonalExplore(store, manager, manager.personal, {
    executable: data ? resolve('test/fixtures/fake-lark-channel.mjs') : join(dir, 'missing-cli'),
    env: { ...process.env, ...(data ? { FAKE_LARK_DATA: data, FAKE_LARK_LOG: join(dir, 'lark.log') } : {}) }, now: () => new Date('2026-11-06T12:00:00Z'), ...options,
  });
  Object.assign(manager.personal, { explore });
  const server = createHttpServer({ manager, token: 'explore-test', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const request = (path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST') =>
    fetch(base + path, { method, headers: { Authorization: 'Bearer explore-test', 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const seedThread = (title: string, texts: string[]) => {
    const id = `thread-${Math.random().toString(36).slice(2)}`;
    store.createThread({ id, agent: 'pi', cwd, title, permissionMode: 'full_access', model: null, effort: null });
    for (const [index, text] of texts.entries()) store.appendEvent(id, `turn-${index}`, 'turn.started', { text });
    return id;
  };
  return {
    request, store, manager, cwd, seedThread,
    async close() {
      await manager.shutdown();
      await new Promise<void>(resolve => server.close(() => resolve()));
      store.close();
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

const exploreStatus = async (h: Handle): Promise<ExploreStatus | null> => (await (await h.request('/api/personal/explore')).json());

async function runExplore(h: Handle, sources: string[] = ALL): Promise<ExploreStatus> {
  const started = await h.request('/api/personal/explore', { sources }, 'POST');
  assert.equal(started.status, 202);
  const { id } = await started.json() as { id: string };
  await wait(() => {
    const status = exploreSnapshot(h);
    return status?.state === 'ready' || status?.state === 'failed';
  });
  const status = (await exploreStatus(h))!;
  assert.equal(status.id, id);
  return status;
}

/** The latest status the service reported, read through the service directly (no HTTP polling). */
function exploreSnapshot(h: Handle): ExploreStatus | null {
  return (h.manager.personal as { explore: PersonalExplore }).explore.status();
}

test('sources endpoint reports availability and a human reason per source', async () => {
  const h = await setup(larkData); try {
    const sources = await (await h.request('/api/personal/explore/sources')).json() as Array<{ id: string; available: boolean; reason?: string }>;
    assert.deepEqual(sources.map(source => source.id), ALL);
    assert.ok(sources.every(source => source.available === true));
    const missing = await setup(null); try {
      const unavailable = await (await missing.request('/api/personal/explore/sources')).json() as Array<{ id: string; available: boolean; reason?: string }>;
      assert.deepEqual(unavailable.map(source => source.available), [false, false, false, false, true]);
      for (const source of unavailable.slice(0, 4)) assert.match(source.reason!, /lark-cli/);
    } finally { await missing.close(); }
  } finally { await h.close(); }
});

test('validation: unknown sources, empty lists and no-run queries', async () => {
  const h = await setup(larkData); try {
    assert.equal(await exploreStatus(h), null);
    assert.equal((await h.request('/api/personal/explore', { sources: ['nope'] }, 'POST')).status, 400);
    assert.equal((await h.request('/api/personal/explore', { sources: [] }, 'POST')).status, 400);
    assert.deepEqual(await (await h.request('/api/personal/explore', undefined, 'DELETE')).json(), { cancelled: false });
  } finally { await h.close(); }
});

test('all sources succeed: steps carry human lines, and the model supplements the baseline', async () => {
  process.env.FAKE_PI_EXPLORE_REPLY = modelReply;
  const h = await setup(larkData); try {
    h.seedThread('修登录页按钮', ['把登录页按钮改成圆角', '还是方的，再改一次']);
    h.seedThread('写周报草稿', ['按这周的事项写周报']);
    const status = await runExplore(h);
    assert.equal(status.state, 'ready'); assert.equal(status.model, 'ok'); assert.ok(status.finishedAt);
    const bySource = new Map(status.steps.map(step => [step.source, step]));
    assert.deepEqual(status.steps.map(step => step.state), ['done', 'done', 'done', 'done', 'done']);
    assert.match(bySource.get('lark-profile')!.line!, /王磊是你的上级/);
    assert.equal(bySource.get('lark-profile')!.read, '通讯录读了 2 次');
    assert.match(bySource.get('lark-chats')!.read!, /5 个会话/);
    assert.match(bySource.get('lark-messages')!.line!, /你发了 6 条/);
    assert.match(bySource.get('lark-messages')!.line!, /被 @ 4 次/);
    assert.match(bySource.get('lark-calendar')!.line!, /固定会议/);
    assert.match(bySource.get('calli-threads')!.line!, /2 个对话/);

    const rec = status.recommendation!;
    // Boss selected as boss; the two human p2p partners selected; the bot chat is not a person.
    const boss = rec.people.find(person => person.id === 'ou_boss')!;
    assert.equal(boss.role, 'boss'); assert.equal(boss.selected, true); assert.equal(boss.name, '王磊');
    const pm = rec.people.find(person => person.id === 'ou_pm')!;
    assert.equal(pm.selected, true); assert.equal(pm.why, '常提需求和排期，像个 PM');
    const dev = rec.people.find(person => person.id === 'ou_dev')!;
    assert.equal(dev.selected, true); assert.equal(dev.role, 'pm', 'the model may mark a partner as PM');
    assert.ok(!rec.people.some(person => person.id === 'ou_ghost'), 'ids outside the fact pack are dropped');
    assert.ok(!rec.people.some(person => person.id === 'ou_bot'), 'bot chats never become people');
    assert.ok(rec.people.every(person => person.selected === true), 'selected people come first');
    assert.ok(rec.people.length <= 10);
    const duty = rec.chats.find(chat => chat.id === 'oc_grp_duty')!;
    assert.equal(duty.selected, true); assert.equal(duty.why, '值班群里总找你'); assert.ok(duty.evidence.includes('@你 3 次'));
    const big = rec.chats.find(chat => chat.id === 'oc_grp_big')!;
    assert.equal(big.selected, false);
    assert.ok(rec.chats.every(chat => chat.selected || chat !== rec.chats[0]));
    assert.equal(rec.meetings.on, true); assert.deepEqual(rec.meetings.examples, ['周一组会']);
    assert.equal(rec.brief.enabled, true);
    assert.match(rec.brief.morning, /^\d{2}:(00|30)$/); assert.match(rec.brief.evening, /^\d{2}:(00|30)$/);
    assert.equal(rec.intervalMin.value, 120); assert.match(rec.intervalMin.why, /6 条/);
    // The person's own open id is a usable recipient (the same fallback push itself applies).
    assert.equal(rec.push.available, false); assert.equal(rec.push.enabled, false);
    assert.equal(rec.proactive.mode, 'observe');
    assert.equal(rec.agent.agent, 'pi'); assert.equal(rec.agent.options[0].recommended, true);
    assert.match(rec.profile.draft, /# 我的说明/); assert.match(rec.profile.draft, /转人工率/);
    assert.equal(rec.profile.why, '从你的会话和日程归纳');
    assert.equal(rec.profile.replace, true, 'the untouched template can be replaced outright');
  } finally {
    delete process.env.FAKE_PI_EXPLORE_REPLY;
    await h.close();
  }
});

test('the model text is validated: out-of-pack ids dropped, roles whitelisted, strings clipped', async () => {
  process.env.FAKE_PI_EXPLORE_REPLY = JSON.stringify({
    peopleRoles: [{ id: 'ou_pm', role: 'wizard' }, { id: 'ou_boss', role: 'pm' }, { id: 'ou_ghost', role: 'pm' }],
    why: { people: { ou_pm: 'x'.repeat(50) }, chats: { oc_ghost: '不存在的群' } },
    profileDraft: 'sk-abcdefghij0123456789abcdefghij',
  });
  const h = await setup(larkData); try {
    const status = await runExplore(h);
    assert.equal(status.model, 'ok');
    const rec = status.recommendation!;
    const pm = rec.people.find(person => person.id === 'ou_pm')!;
    assert.equal([...pm.why].length, 30, 'why strings are clipped to 30 characters');
    assert.equal(pm.role, 'other', 'unknown roles never apply');
    const boss = rec.people.find(person => person.id === 'ou_boss')!;
    assert.equal(boss.role, 'boss', 'the directory leader stays the boss');
    assert.ok(!rec.people.some(person => person.id === 'ou_ghost'));
    assert.ok(!rec.chats.some(chat => chat.id === 'oc_ghost'));
    assert.match(rec.profile.draft, /# 我的说明/, 'a credential-shaped draft falls back to the rule draft');
  } finally {
    delete process.env.FAKE_PI_EXPLORE_REPLY;
    await h.close();
  }
});

test('a tenant whose self lookup has no leader still explores, and says so plainly', async () => {
  const h = await setup({ ...larkData, self: { open_id: 'ou_self', name: '张三' } }); try {
    h.seedThread('修登录页按钮', ['把登录页按钮改成圆角']);
    const status = await runExplore(h, ['lark-profile']);
    assert.equal(status.state, 'ready');
    const step = status.steps[0];
    assert.equal(step.state, 'done');
    assert.equal(step.line, '你是 张三，通讯录没给出你的上级');
    assert.ok(!status.recommendation!.people.some(person => person.role === 'boss'));
  } finally { await h.close(); }
});

test('a missing lark-cli leaves calli-threads alone, and the run still ends ready', async () => {
  const h = await setup(null); try {
    h.seedThread('修登录页按钮', ['把登录页按钮改成圆角']);
    const status = await runExplore(h);
    assert.equal(status.state, 'ready');
    const bySource = new Map(status.steps.map(step => [step.source, step]));
    for (const source of ALL.slice(0, 4)) {
      assert.equal(bySource.get(source)!.state, 'skipped');
      assert.match(bySource.get(source)!.reason!, /lark-cli/);
    }
    assert.equal(bySource.get('calli-threads')!.state, 'done');
    const rec = status.recommendation!;
    assert.deepEqual(rec.people, []); assert.deepEqual(rec.chats, []);
    assert.equal(rec.meetings.on, false); assert.equal(rec.brief.enabled, false);
    assert.match(bySource.get('calli-threads')!.line!, /1 个对话/);
  } finally { await h.close(); }
});

test('a failing model falls back to the rule baseline and still reports ready', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'invalid';
  const h = await setup(larkData); try {
    const status = await runExplore(h);
    assert.equal(status.state, 'ready'); assert.equal(status.model, 'fallback');
    const rec = status.recommendation!;
    const boss = rec.people.find(person => person.id === 'ou_boss')!;
    assert.equal(boss.why, '通讯录里的上级', 'reasons come from the rules when the model fails');
    const pm = rec.people.find(person => person.id === 'ou_pm')!;
    assert.equal(pm.role, 'other'); assert.match(pm.why, /私聊 3 条/);
    assert.match(rec.profile.draft, /# 我的说明/); assert.match(rec.brief.why, /先不开启|按你/);
    assert.equal(rec.intervalMin.why.includes('fallback'), false);
  } finally {
    delete process.env.FAKE_PI_EXPLORE_MODE;
    await h.close();
  }
});

test('DELETE during the model question cancels the run and stops the askOnce thread', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'hold';
  const h = await setup(larkData); try {
    h.seedThread('修登录页按钮', ['把登录页按钮改成圆角']);
    assert.equal((await h.request('/api/personal/explore', { sources: ALL }, 'POST')).status, 202);
    await wait(() => exploreSnapshot(h)?.state === 'judging');
    const response = await h.request('/api/personal/explore', undefined, 'DELETE');
    assert.deepEqual(await response.json(), { cancelled: true });
    await wait(() => exploreSnapshot(h)?.state === 'cancelled');
    const status = (await exploreStatus(h))!;
    assert.equal(status.state, 'cancelled'); assert.ok(status.finishedAt);
    assert.equal(status.recommendation, undefined, 'a cancelled run recommends nothing');
    assert.ok(status.steps.every(step => step.state !== 'running'));
    await wait(() => [...h.store.listThreads(), ...h.store.listThreads({ archived: true })]
      .filter(thread => thread.title === '一键配置探索').every(thread => thread.status !== 'running'));
  } finally {
    delete process.env.FAKE_PI_EXPLORE_MODE;
    await h.close();
  }
});

test('DELETE during collection cancels the in-flight source read', async () => {
  const h = await setup({ ...larkData, delays: { 'im +chat-list': 5000 } }, { sourceTimeoutMs: 8000 }); try {
    assert.equal((await h.request('/api/personal/explore', { sources: ['lark-chats'] }, 'POST')).status, 202);
    await wait(() => exploreSnapshot(h)?.steps.some(step => step.state === 'running'));
    const response = await h.request('/api/personal/explore', undefined, 'DELETE');
    assert.deepEqual(await response.json(), { cancelled: true });
    await wait(() => exploreSnapshot(h)?.state === 'cancelled');
    const status = (await exploreStatus(h))!;
    assert.equal(status.state, 'cancelled');
  } finally { await h.close(); }
});

test('concurrent POSTs run one explore and the second caller joins it', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'hold';
  const h = await setup(larkData); try {
    const [first, second] = await Promise.all([
      h.request('/api/personal/explore', { sources: ALL }, 'POST'),
      h.request('/api/personal/explore', { sources: ALL }, 'POST'),
    ]);
    assert.deepEqual([first.status, second.status].sort(), [200, 202]);
    assert.equal((await first.json()).id, (await second.json()).id);
    await wait(() => exploreSnapshot(h)?.state === 'judging');
    const status = exploreSnapshot(h)!;
    assert.equal(status.steps.filter(step => step.state === 'done').length, 5);
    assert.equal(cliCalls(h).filter(args => args[0] === 'im' && args[1] === '+chat-list').length, 1, 'the source process really ran only once');
    await h.request('/api/personal/explore', undefined, 'DELETE');
    await wait(() => exploreSnapshot(h)?.state === 'cancelled');
  } finally {
    delete process.env.FAKE_PI_EXPLORE_MODE;
    await h.close();
  }
});

const cliCalls = (h: Handle): string[][] => {
  const file = join(h.cwd, '..', 'lark.log');
  return existsSync(file) ? readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
};

test('local-only collection reads human display text and never assistant/tool/background content', async () => {
  const h = await setup(larkData); try {
    const id = h.seedThread('Human project title', []);
    h.store.appendEvent(id, 'human', 'turn.started', { text: 'HIDDEN_PROTOCOL_SENTINEL', displayText: 'HUMAN_DISPLAY_SENTINEL' });
    h.store.appendEvent(id, 'human', 'message.delta', { text: 'ASSISTANT_SENTINEL' });
    h.store.appendEvent(id, 'human', 'tool.end', { text: 'TOOL_SENTINEL' });
    h.store.appendEvent(id, 'background', 'turn.started', { text: 'BACKGROUND_SENTINEL', background: true });
    h.store.appendEvent(id, 'scheduled', 'turn.started', { text: 'SCHEDULED_SENTINEL', scheduled: true });
    let prompt = '';
    const ask = h.manager.askOnce.bind(h.manager);
    h.manager.askOnce = async (agent, text, options) => { prompt = text; return ask(agent, text, options); };
    const status = await runExplore(h, ['calli-threads']);
    assert.equal(status.state, 'ready');
    assert.equal(status.steps[0].state, 'done');
    assert.match(prompt, /HUMAN_DISPLAY_SENTINEL/);
    assert.match(prompt, /Human project title/);
    assert.doesNotMatch(prompt, /HIDDEN_PROTOCOL_SENTINEL|ASSISTANT_SENTINEL|TOOL_SENTINEL|BACKGROUND_SENTINEL|SCHEDULED_SENTINEL/);
    assert.deepEqual(cliCalls(h), [], 'a deselected Lark source performs no probe');
    assert.deepEqual(h.store.userTurns(id, 3, '2099-01-01'), []);
    const question = h.store.listThreads({ archived: true }).find(t => t.title === '一键配置探索')!;
    assert.ok(question);
    const persisted = JSON.stringify(h.store.listEvents(question.id, 0, -1));
    assert.doesNotMatch(persisted, /HUMAN_DISPLAY_SENTINEL|Human project title|事实：/);
    assert.deepEqual(h.store.userTurns(question.id), []);
    assert.equal(existsSync(join(h.cwd, '.calli', 'explore.json')), false);
  } finally { await h.close(); }
});

test('logged-out Lark sources are skipped while local collection and judgement continue', async () => {
  const h = await setup({ ...larkData, loggedIn: false }); try {
    const status = await runExplore(h);
    assert.equal(status.state, 'ready');
    for (const step of status.steps.slice(0, 4)) { assert.equal(step.state, 'skipped'); assert.match(step.reason!, /登录/); }
    assert.equal(status.steps[4].state, 'done');
    assert.equal(cliCalls(h).length, 2, 'only help and auth are read');
  } finally { await h.close(); }
});

test('one failed source does not stop the remaining sources or leak CLI stderr', async () => {
  const h = await setup({ ...larkData, fail: 'im +chat-list' }); try {
    const status = await runExplore(h);
    assert.equal(status.state, 'ready');
    assert.deepEqual(status.steps.map(s => s.state), ['done', 'failed', 'done', 'done', 'done']);
    assert.doesNotMatch(JSON.stringify(status), /sensitive_cli_output/);
    assert.equal(status.recommendation!.people[0].role, 'boss');
  } finally { await h.close(); }
});

test('the source deadline covers both profile subprocesses together', async () => {
  const h = await setup({ ...larkData, delays: { 'contact +get-user': 900 } }, { sourceTimeoutMs: 1500 }); try {
    const status = await runExplore(h, ['lark-profile', 'calli-threads']);
    assert.equal(status.state, 'ready');
    assert.equal(status.steps[0].state, 'failed');
    assert.equal(status.steps[0].reason, '读取超时');
    assert.equal(status.steps[1].state, 'done');
  } finally { await h.close(); }
});

for (const mode of ['error', 'hold']) test(`model ${mode === 'hold' ? 'timeout' : 'runtime error'} produces a ready baseline`, async () => {
  process.env.FAKE_PI_EXPLORE_MODE = mode;
  const h = await setup(larkData, { modelTimeoutMs: 300 }); try {
    const status = await runExplore(h);
    assert.equal(status.state, 'ready'); assert.equal(status.model, 'fallback');
    assert.equal(status.recommendation!.people.find(p => p.id === 'ou_boss')!.role, 'boss');
    assert.ok(h.store.listThreads({ archived: true }).every(t => t.status !== 'running'));
  } finally { delete process.env.FAKE_PI_EXPLORE_MODE; await h.close(); }
});

test('cancellation while askOnce creates its thread cannot launch a late prompt, and a later run is independent', async () => {
  const h = await setup(larkData); try {
    const create = h.manager.createThread.bind(h.manager);
    let reached = false;
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    h.manager.createThread = async input => {
      const thread = await create(input);
      if (input.title === '一键配置探索') { reached = true; await gate; }
      return thread;
    };
    const first = await h.request('/api/personal/explore', { sources: ['calli-threads'] });
    const firstId = (await first.json()).id;
    await wait(() => reached);
    await h.request('/api/personal/explore', undefined, 'DELETE');
    const joining = await h.request('/api/personal/explore', { sources: ALL });
    assert.equal(joining.status, 200); assert.equal((await joining.json()).id, firstId);
    release();
    await wait(() => exploreSnapshot(h)?.state === 'cancelled');
    const question = h.store.listThreads({ archived: true }).find(t => t.title === '一键配置探索')!;
    assert.deepEqual(h.store.listEventsOfType(question.id, 'turn.started'), []);
    h.manager.createThread = create;
    const next = await runExplore(h, ['calli-threads']);
    assert.equal(next.state, 'ready'); assert.notEqual(next.id, firstId);
  } finally { await h.close(); }
});

test('baseline selects the top three peers including the boss, sorts by interaction, and requires a push recipient', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'invalid';
  const peers = ['boss', 'second', 'third', 'fourth'];
  const h = await setup({ ...larkData,
    chatList: peers.map(name => ({ chat_id: `oc_${name}`, chat_mode: 'p2p', name, p2p_target_id: `ou_${name}` })),
    messages: peers.flatMap((name, rank) => Array.from({ length: 10 - rank }, (_, n) => message(`${name}-${n}`, `oc_${name}`, name, '2026-11-04 10:00', 'hi'))),
  }); try {
    h.store.saveHubSetting('push', { recipient: '', baseUrl: 'https://example.invalid' });
    h.manager.personal.putSettings({ agent: null, model: null });
    await h.manager.personal.saveProfile({ text: '# 我的说明\n已由本人写好' });
    const status = await runExplore(h);
    const rec = status.recommendation!;
    assert.deepEqual(rec.people.filter(p => p.selected).map(p => p.id), ['ou_boss', 'ou_second', 'ou_third']);
    assert.equal(rec.push.available, false); assert.equal(rec.push.enabled, false);
    assert.equal(rec.agent.agent, 'pi', 'the first available agent is selected when none is configured');
    assert.equal(rec.profile.replace, false);
    h.store.saveHubSetting('push', { recipient: 'ou_receiver', baseUrl: '' });
    assert.equal((await runExplore(h, ['calli-threads'])).recommendation!.push.available, true);
  } finally { delete process.env.FAKE_PI_EXPLORE_MODE; await h.close(); }
});

test('message statistics enforce the cap, sender and time range, and ignore duplicate/deleted rows', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'invalid';
  const valid = message('valid', 'oc_p2p_pm', '陈思', '2026-11-04T10:00:00Z', 'hi');
  const h = await setup({ ...larkData, messages: [valid, valid,
    { ...valid, message_id: 'other', sender: { id: 'ou_other' } },
    { ...valid, message_id: 'old', create_time: '2026-01-01 10:00' },
    { ...valid, message_id: 'deleted', deleted: true },
    ...Array.from({ length: 220 }, (_, n) => ({ ...valid, message_id: `ok-${n}`, create_time: String(Date.parse('2026-11-04T10:00:00Z')) })),
  ] }); try {
    const rec = (await runExplore(h)).recommendation!;
    assert.equal(rec.intervalMin.why, '近 14 天你发了 196 条消息');
    assert.match(rec.people.find(p => p.id === 'ou_pm')!.why, /196/);
  } finally { delete process.env.FAKE_PI_EXPLORE_MODE; await h.close(); }
});

test('credential-shaped source names cannot enter the baseline profile draft', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'invalid';
  const h = await setup({ ...larkData, contacts: { ou_boss: { open_id: 'ou_boss', name: 'sk-abcdefghij0123456789abcdefghij' } } }); try {
    const rec = (await runExplore(h)).recommendation!;
    assert.doesNotMatch(rec.profile.draft, /sk-/);
    assert.match(rec.profile.draft, /## 不要打扰我的时间/);
  } finally { delete process.env.FAKE_PI_EXPLORE_MODE; await h.close(); }
});

test('keyword summaries exclude technical identifiers and paths without leaking token fragments', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'invalid';
  // Synthetic values only; no actual machine, account or network identifiers enter this fixture.
  const noise = [
    ['buildnode', 'invalid'].join('.'), ['buildnode', 'alpha'].join('-'),
    'https://' + ['buildnode', 'invalid'].join('.') + '/private?query=opaque&key=value#anchor',
    [192, 0, 2, 1].join('.'), ['2001', 'db8', '', '1'].join(':'),
    'abcdef'.repeat(4), 'aBcdEfGhIjKlMnOpQrStUvWx', 'ABCDEF'.repeat(5), 'abcdefghijklmnop',
    ...['om', 'oc', 'ou'].map(prefix => `${prefix}_privateidentifier`),
    ['', 'private', '部署目录', 'config'].join('/'), ['C:', 'private', 'config'].join('\\'),
    ['relative', 'folder', 'document'].join('/'),
    'x'.repeat(78) + ['buildnode', 'invalid'].join('.'),
  ];
  const h = await setup({ ...larkData, messages: noise.flatMap((token, index) => Array.from({ length: 3 }, (_, n) =>
    message(`noise-${index}-${n}`, 'oc_p2p_pm', '陈思', '2026-11-04 10:00', `周报 deployment ${token}`))) }); try {
    let prompt = '';
    const ask = h.manager.askOnce.bind(h.manager);
    h.manager.askOnce = async (agent, text, options) => { prompt = text; return ask(agent, text, options); };
    const status = await runExplore(h, ['lark-messages']);
    assert.equal(status.state, 'ready');
    const facts = JSON.parse(prompt.split('事实：\n')[1].split('\n\n基线推荐：')[0]);
    assert.deepEqual(facts.messages.keywords, ['deployment', '周报']);
    assert.equal(status.recommendation!.profile.draft.split('## 我在意什么\n')[1].split('\n\n')[0], 'deployment、周报');
    assert.match(status.steps[0].line!, /常提到 deployment、周报/);
  } finally { delete process.env.FAKE_PI_EXPLORE_MODE; await h.close(); }
});

test('brief times ignore overnight activity and daily morning/evening outliers', async () => {
  process.env.FAKE_PI_EXPLORE_MODE = 'invalid';
  const rows = [['09:00', '17:00'], ['09:30', '18:00'], ['10:00', '19:00'], ['17:00', '23:30']]
    .flatMap(([first, last], day) => ['03:00', first, last].map((time, n) =>
      message(`routine-${day}-${n}`, 'oc_p2p_pm', '陈思', `2026-11-0${day + 1} ${time}`, '周报')));
  rows.push(message('night-only', 'oc_p2p_pm', '陈思', '2026-11-05 03:00', '周报'));
  const h = await setup({ ...larkData, messages: rows }); try {
    const brief = (await runExplore(h, ['lark-messages'])).recommendation!.brief;
    assert.equal(brief.enabled, true);
    assert.equal(brief.morning, '09:30');
    assert.equal(brief.evening, '18:00');
    const night = await setup({ ...larkData, messages: rows.filter(row => row.create_time.endsWith('03:00')) }); try {
      assert.equal((await runExplore(night, ['lark-messages'])).recommendation!.brief.enabled, false, 'overnight-only days do not establish a routine');
    } finally { await night.close(); }
  } finally { delete process.env.FAKE_PI_EXPLORE_MODE; await h.close(); }
});
