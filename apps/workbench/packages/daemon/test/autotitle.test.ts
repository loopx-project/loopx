import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { Workdirs } from '../src/workdirs.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { ExtensionHost } from '../src/extension-host.js';
import { loadCatalog } from '../src/extensions.js';
import { AUTOTITLE_ID, AutoTitle, cleanTitle, namingPrompt } from '../src/autotitle.js';
import { createHttpServer } from '../src/http.js';

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'test-token-autotitle';
const PERMISSIONS = ['events.summary', 'transcript.read', 'threads.rename', 'agents.ask'];
const wait = async (check: () => boolean, timeout = 15000) => {
  const until = Date.now() + timeout;
  while (!check()) { if (Date.now() > until) throw new Error('timed out'); await new Promise(r => setTimeout(r, 20)); }
};

async function setup(opts: { install?: boolean } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-autotitle-'));
  const cwd = join(dir, 'project'); mkdirSync(cwd);
  const adapters = new Map<string, AgentAdapter>([['claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures/fake-acp.mjs')])]]);
  const store = new Store(dir);
  const manager = new ThreadManager(store, adapters, new Workdirs([cwd], join(dir, 'dirs.json'), join(dir, 'scratch')), new AttachmentStore(dir));
  const extensions = new ExtensionHost({ store: store.extensions, source: manager, catalog: loadCatalog() });
  const autotitle = new AutoTitle(extensions.builtin(AUTOTITLE_ID)!, manager, store, extensions);
  extensions.register(autotitle);
  extensions.start();
  if (opts.install !== false) extensions.install(AUTOTITLE_ID, PERMISSIONS);
  const server = createHttpServer({ manager, extensions, autotitle, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) =>
    fetch(base + path, { method, headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const idle = (id: string) => wait(() => manager.threadView(id).status !== 'running');
  const send = async (id: string, text: string) => { await manager.startTurn(id, { text }); await idle(id); };
  return { dir, cwd, store, manager, extensions, autotitle, api, send, idle, async close() {
    extensions.dispose(); await manager.shutdown();
    await new Promise<void>(r => server.close(() => r()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}

test('cleanTitle keeps the first line, without labels, quotes and marks, emoji included, at most 40 characters', () => {
  assert.equal(cleanTitle('标题：「🐛 修复登录超时」\n解释'), '🐛 修复登录超时');
  assert.equal(cleanTitle('\n\n"Fix the login timeout"'), 'Fix the login timeout');
  assert.equal(cleanTitle('**整理周报**'), '整理周报');
  assert.equal(cleanTitle('**「标题」**'), '标题', 'bold is stripped before the quotes it wraps');
  assert.equal(cleanTitle('# 标题'), '标题', 'a heading marker goes');
  assert.equal(cleanTitle('`标题`'), '标题', 'inline code marks go');
  assert.equal(cleanTitle('标题。'), '标题', 'a sentence-ending mark goes');
  assert.equal(cleanTitle('为什么登录超时？'), '为什么登录超时？', 'a question keeps its mark');
  assert.equal(cleanTitle('Fix **A** and **B**'), 'Fix **A** and **B**', 'bold inside the title is left alone');
  assert.equal([...cleanTitle('长'.repeat(80))].length, 40);
  const joined = cleanTitle('👨‍👩‍👧‍👦'.repeat(41));
  assert.ok(!joined.endsWith('\u200D'), 'the cut leaves no emoji half');
  assert.equal(joined.length, 40 * '👨‍👩‍👧‍👦'.length, 'whole joined emoji only');
  assert.equal(cleanTitle('   '), '');
});

test('the naming request carries the rules as written, the conversation start, and the hub\'s own last line', () => {
  const prompt = namingPrompt('- 开头放一个 emoji', '为什么登录会超时？'.repeat(200), '因为 token 过期了');
  assert.match(prompt, /- 开头放一个 emoji/);
  assert.match(prompt, /用户：为什么登录会超时？/);
  assert.match(prompt, /回复：因为 token 过期了/);
  assert.ok(prompt.length < 2200, 'long messages are cut');
  assert.match(prompt.trim().split('\n').at(-1)!, /只输出标题本身/);
  const emoji = namingPrompt('', '🐛'.repeat(1600), null);
  assert.equal((emoji.match(/🐛/g) ?? []).length, 1500, 'the cut does not take an emoji apart');
  // Empty rules fall back to the shipped defaults, which ask for one emoji that fits the work.
  assert.match(namingPrompt('', '帮我修登录超时', null), /开头放一个贴切/);
  assert.match(namingPrompt('', '帮我修登录超时', null), /注意区分度/);
});

test('the settings endpoint serves the shipped defaults, and saving keeps the person\'s own rules', async (t) => {
  const h = await setup();
  t.after(() => h.close());
  const served = (await (await h.api('/api/extensions/autotitle/settings')).json()) as { settings: { rules: string }; defaultRules: string };
  assert.equal(served.settings.rules, served.defaultRules, 'a fresh install starts from the rules in git');
  assert.match(served.defaultRules, /emoji/);
  await h.api('/api/extensions/autotitle/settings', 'PUT', { rules: '- 我自己的规则' });
  const after = (await (await h.api('/api/extensions/autotitle/settings')).json()) as { settings: { rules: string }; defaultRules: string };
  assert.equal(after.settings.rules, '- 我自己的规则', 'what the person saved stays theirs');
  assert.match(after.defaultRules, /emoji/, 'the defaults stay reachable for 恢复默认规则');
});

test('after the first turn the chosen agent names the thread by the rules; the question leaves no thread in the list', async () => {
  const h = await setup();
  try {
    assert.equal((await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude', rules: '- 开头放一个 emoji，用 🤖' })).status, 200);
    const thread = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(thread.id, '登录接口总是超时，帮我查一下');
    assert.equal(h.manager.titleBy(thread.id), 'default');
    await wait(() => h.manager.threadView(thread.id).title === '🤖 修复登录超时');
    assert.equal(h.manager.titleBy(thread.id), 'auto');
    const listed = h.manager.listThreads().map(t => t.id);
    assert.deepEqual(listed, [thread.id], 'the naming question ran in an archived thread');
    const asked = h.manager.listThreads({ archived: true });
    assert.equal(asked.length, 1);
    const question = String(h.store.listEventsOfType(asked[0].id, 'turn.started')[0].data.text);
    assert.match(question, /登录接口总是超时/);
    assert.doesNotMatch(question, /calli-tools|calli-progress/, 'nothing is added around the question');
    const status = await (await h.api('/api/extensions/autotitle/settings')).json() as { status: { last: { title: string } } };
    assert.equal(status.status.last.title, '🤖 修复登录超时');
    // Later turns do not rename again unless asked for.
    await h.send(thread.id, '再看看 2'); await h.send(thread.id, '再看看 3');
    await new Promise(r => setTimeout(r, 200));
    assert.equal(h.manager.listThreads({ archived: true }).length, 1);
  } finally { await h.close(); }
});

test('a title the person set is never renamed, and the second pass only revises automatic titles', async () => {
  const h = await setup();
  try {
    await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude', again: true });
    const mine = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.manager.startTurn(mine.id, { text: 'slow 一件事' });
    await h.manager.updateThread(mine.id, { title: '我自己起的名字' });
    await h.manager.cancelTurn(mine.id); await h.idle(mine.id);
    await h.send(mine.id, '继续'); await h.send(mine.id, '再继续');
    await new Promise(r => setTimeout(r, 300));
    assert.equal(h.manager.threadView(mine.id).title, '我自己起的名字');
    assert.equal(h.manager.titleBy(mine.id), 'user');
    assert.equal(h.manager.listThreads({ archived: true }).length, 0, 'nothing was asked');

    const auto = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(auto.id, '登录总是超时');
    await wait(() => h.manager.titleBy(auto.id) === 'auto');
    await h.send(auto.id, '第二轮'); await h.send(auto.id, '第三轮');
    await wait(() => h.manager.listThreads({ archived: true }).length === 2);
  } finally { await h.close(); }
});

test('a first turn that was stopped leaves the default title, and the next turn that ends well names the thread', async () => {
  const h = await setup();
  try {
    await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude' });
    const thread = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.manager.startTurn(thread.id, { text: 'slow 登录接口总是超时' });
    await h.manager.cancelTurn(thread.id); await h.idle(thread.id);
    await new Promise(r => setTimeout(r, 200));
    assert.equal(h.manager.titleBy(thread.id), 'default', 'a stopped turn is not named');
    await h.send(thread.id, '继续');
    await wait(() => h.manager.titleBy(thread.id) === 'auto');
    const question = String(h.store.listEventsOfType(h.manager.listThreads({ archived: true })[0].id, 'turn.started')[0].data.text);
    assert.match(question, /登录接口总是超时/, 'named from the first message, not the one that ended well');
    await h.send(thread.id, '再看看');
    await new Promise(r => setTimeout(r, 200));
    assert.equal(h.manager.listThreads({ archived: true }).length, 1, 'once named, later turns ask nothing');
  } finally { await h.close(); }
});

test('naming on send uses only the message; nothing happens without a chosen agent or the extension', async () => {
  const off = await setup({ install: false });
  try {
    const thread = await off.manager.createThread({ agent: 'claude', cwd: off.cwd });
    await off.send(thread.id, '登录总是超时');
    await new Promise(r => setTimeout(r, 300));
    assert.equal(off.manager.threadView(thread.id).title, '登录总是超时', 'not installed: left alone');
    assert.equal((await off.api('/api/extensions/autotitle/settings')).status, 403, 'GET settings needs the grant');
    assert.equal((await off.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude' })).status, 403);
    assert.equal((await off.api('/api/extensions/autotitle/preview', 'POST', { threadId: thread.id })).status, 403);
    assert.equal((await off.api(`/api/threads/${thread.id}/autotitle`, 'POST')).status, 403);
  } finally { await off.close(); }

  const h = await setup();
  try {
    const thread = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(thread.id, '登录总是超时');
    await new Promise(r => setTimeout(r, 300));
    assert.equal(h.manager.threadView(thread.id).title, '登录总是超时', 'no agent chosen: left alone');
    assert.equal((await h.api(`/api/threads/${thread.id}/autotitle`, 'POST')).status, 409);

    await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude', trigger: 'sent' });
    const early = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.manager.startTurn(early.id, { text: 'slow 登录总是超时' });
    await wait(() => h.manager.threadView(early.id).title === '🐛 修复登录超时');
    assert.equal(h.manager.threadView(early.id).status, 'running', 'named while its first turn still runs');
    const asked = h.manager.listThreads({ archived: true })[0];
    assert.doesNotMatch(String(h.store.listEventsOfType(asked.id, 'turn.started')[0].data.text), /回复：/, 'on send, the answer is not sent');
    await h.manager.cancelTurn(early.id);
  } finally { await h.close(); }
});

test('「重新取名」 renames any thread at once, the preview renames nothing, and bad settings are refused', async () => {
  const h = await setup();
  try {
    for (const bad of [{ agent: 'missing' }, { trigger: 'later' }, { again: 'yes' }, { rules: 7 }, { colour: 'red' }]) {
      assert.equal((await h.api('/api/extensions/autotitle/settings', 'PUT', bad)).status, 400, JSON.stringify(bad));
    }
    const thread = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(thread.id, '登录总是超时');
    await h.manager.updateThread(thread.id, { title: '旧名字' });
    await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude' });
    const preview = await (await h.api('/api/extensions/autotitle/preview', 'POST', { threadId: thread.id })).json() as { title: string };
    assert.equal(preview.title, '🐛 修复登录超时');
    assert.equal(h.manager.threadView(thread.id).title, '旧名字', 'a preview renames nothing');
    const named = await (await h.api(`/api/threads/${thread.id}/autotitle`, 'POST')).json() as { title: string };
    assert.equal(named.title, '🐛 修复登录超时');
    assert.equal(h.manager.threadView(thread.id).title, '🐛 修复登录超时');
  } finally { await h.close(); }
});

test('a naming agent that does not answer in time is stopped, and the title stays', async () => {
  const h = await setup();
  try {
    await assert.rejects(h.manager.askOnce('claude', '给下面这段对话起一个线程标题。slow name', { timeoutMs: 300 }), /too long/);
    await wait(() => h.manager.listThreads({ archived: true }).every(t => t.status !== 'running'));
    assert.equal(h.manager.listThreads().length, 0, 'the question never showed in the list');
    assert.equal((h.manager as unknown as { sessions: Map<string, unknown> }).sessions.size, 0, 'a stopped question closes its agent process');
    assert.equal(readdirSync(join(h.dir, 'scratch')).length, 0, 'and takes its scratch folder with it');
    const asked = h.manager.listThreads({ archived: true })[0];
    assert.equal(h.store.listEventsOfType(asked.id, 'turn.error').length, 0, 'a timeout is a cancelled turn, not a failed one');
  } finally { await h.close(); }
});

test('a background question leaves no live session and no scratch folder behind', async () => {
  const h = await setup();
  try {
    const answer = await h.manager.askOnce('claude', '给下面这段对话起一个线程标题。', { title: '一问' });
    assert.match(answer, /修复登录超时/);
    assert.equal((h.manager as unknown as { sessions: Map<string, unknown> }).sessions.size, 0, 'the agent process is closed after the answer');
    assert.equal(readdirSync(join(h.dir, 'scratch')).length, 0, 'its scratch folder is removed');
  } finally { await h.close(); }
});

test('a naming that waited behind another does not send the conversation once the grant is gone', async () => {
  const h = await setup();
  try {
    await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude', trigger: 'sent' });
    // The hung thread's naming blocks the chain; the second thread's naming queues behind it.
    const hung = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.manager.startTurn(hung.id, { text: 'slow name 登录总是超时' });
    const asking = () => h.manager.listThreads({ archived: true }).find(t => t.status === 'running');
    await wait(() => Boolean(asking()));
    const queued = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(queued.id, '登录总是超时');
    // Precondition: the queued naming really is waiting in the chain, so what follows tests the
    // recheck inside run(), not the checks notify() itself makes.
    assert.ok((h.autotitle as unknown as { waiting: Set<string> }).waiting.has(queued.id));
    h.extensions.setEnabled(AUTOTITLE_ID, false);
    await h.manager.cancelTurn(asking()!.id);
    await wait(() => h.manager.listThreads({ archived: true }).every(t => t.status !== 'running'));
    await new Promise(r => setTimeout(r, 300));
    assert.equal(h.manager.threadView(queued.id).title, '登录总是超时', 'the queued naming asked nothing');
    assert.equal(h.manager.listThreads({ archived: true }).length, 1, 'only the hung naming opened a thread');
    assert.equal((await h.api(`/api/threads/${queued.id}/autotitle`, 'POST')).status, 403, 'manual renaming refuses without the grant');
    await h.manager.cancelTurn(hung.id).catch(() => undefined);
  } finally { await h.close(); }
});

test('「重新取名」 does not overwrite a title the person set while the agent was thinking', async () => {
  const h = await setup();
  try {
    await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude', rules: '- later name' });
    const thread = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(thread.id, '登录总是超时');
    const naming = h.api(`/api/threads/${thread.id}/autotitle`, 'POST');
    // The automatic naming from the send runs first; the manual one runs behind it. Wait for the
    // manual one to be underway (a second background thread) before the person renames.
    await wait(() => h.manager.listThreads({ archived: true }).length === 2 && h.manager.listThreads({ archived: true }).some(t => t.status === 'running'));
    await h.manager.updateThread(thread.id, { title: '我想叫这个' });
    assert.equal((await naming).status, 200);
    assert.equal(h.manager.threadView(thread.id).title, '我想叫这个', 'the person\u2019s title stands');
    assert.equal(h.manager.titleBy(thread.id), 'user');
  } finally { await h.close(); }
});

test('「整理现有线程」 names the backlog, one title the person set stays, a second click while running is refused', async () => {
  const h = await setup();
  try {
    await h.api('/api/extensions/autotitle/settings', 'PUT', { agent: 'claude' });
    const old1 = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(old1.id, '登录总是超时');
    const old2 = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(old2.id, '注册收不到验证码');
    const mine = await h.manager.createThread({ agent: 'claude', cwd: h.cwd });
    await h.send(mine.id, '第三件事');
    await wait(() => h.manager.titleBy(old1.id) === 'auto');
    await h.manager.updateThread(mine.id, { title: '我的名字' });
    // Threads from before title_by was kept: the backlog the pass is for. The person's own title keeps its mark.
    (h.store as unknown as { db: { prepare: (q: string) => { run: (v: string) => unknown } } }).db.prepare('UPDATE threads SET title_by = NULL WHERE id != ?').run(mine.id);

    const first = await (await h.api('/api/extensions/autotitle/organize', 'POST')).json() as { queued: number };
    assert.equal(first.queued, 2, 'the default-titled pair, not the person\u2019s own');
    assert.equal((await h.api('/api/extensions/autotitle/organize', 'POST')).status, 409, 'a pass already runs');
    await wait(() => h.autotitle.passState() === null, 20000);
    assert.equal(h.manager.threadView(old1.id).title, '🐛 修复登录超时');
    assert.equal(h.manager.titleBy(old1.id), 'auto');
    assert.equal(h.manager.threadView(old2.id).title, '🐛 修复登录超时');
    assert.equal(h.manager.threadView(mine.id).title, '我的名字', 'the person\u2019s title is never touched');
    const again = await (await h.api('/api/extensions/autotitle/organize', 'POST')).json() as { queued: number };
    assert.equal(again.queued, 0, 'a finished pass leaves nothing to do');
  } finally { await h.close(); }
});
