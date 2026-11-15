import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { CoordinationManager } from '../src/coordination.js';
import { parseTeamMentions } from '../src/teams.js';
import { createHttpServer } from '../src/http.js';
import { HttpError } from '../src/errors.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-teams-test-'));
  const cwd = join(dir, 'project'); mkdirSync(cwd);
  const store = new Store(dir);
  const manager = new ThreadManager(store, new Map([['pi', new PiRpcAdapter([process.execPath, join(fixtures, 'fake-pi.mjs')])], ['second', new PiRpcAdapter([process.execPath, join(fixtures, 'fake-pi.mjs')])]]), [cwd], new AttachmentStore(dir));
  const coordination = new CoordinationManager(store, manager, join(dir, 'unused.sock'));
  const teams = coordination.definitions;
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path = '', method = 'GET', body?: unknown) => fetch(base + '/api/teams' + path, { method, headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const template = await teams.template();
  const definition = teams.save(template);
  const dispatch = (text = 'TEAM_FLOW') => teams.dispatch({ teamId: definition.id, text, cwd, source: { kind: 'http' } });
  const settled = async (id: string, status = 'awaiting_confirm') => wait(() => coordination.view(id)?.team?.status === status);
  return { dir, cwd, store, manager, coordination, teams, base, api, definition, dispatch, settled,
    async close() { coordination.dispose(); await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); } };
}
async function wait(check: () => boolean) { const deadline = Date.now() + 12000; while (!check()) { if (Date.now() > deadline) throw new Error('timed out'); await new Promise(r => setTimeout(r, 15)); } }

test('team definitions validate every boundary and retain unavailable instances', async () => {
  const h = await setup();
  try {
    const draft = { ...h.definition, name: 'Other' };
    const bad = (patch: object, match: RegExp) => assert.throws(() => h.teams.save({ ...draft, ...patch }), match);
    bad({ name: ' ' }, /名称/); bad({ name: h.definition.name }, /已存在/);
    for (const name of ['user', 'bad name', '', 'a'.repeat(41)]) bad({ members: [{ name, agent: 'pi' }] }, /成员名称/);
    bad({ members: [{ name: 'a', agent: 'pi' }, { name: 'a', agent: 'pi' }] }, /成员名称/);
    bad({ members: [] }, /1～12/); bad({ members: Array.from({ length: 13 }, (_, i) => ({ name: `m${i}`, agent: 'pi' })) }, /1～12/);
    bad({ entry: 'missing' }, /入口/);
    for (const relations of [[{ kind: 'transfer', from: ['lead'], to: 'dev' }], [{ kind: 'delegate', from: ['lead'], to: 'lead' }], [{ kind: 'delegate', from: ['missing'], to: 'dev' }], [{ kind: 'delegate', from: [], to: 'dev' }], [{ kind: 'delegate', from: ['dev'], to: 'missing' }]]) bad({ relations }, /关系/);
    bad({ members: [{ name: 'lead', agent: 'pi', instruction: 'x'.repeat(4001) }] }, /4000/);
    for (const maxDeliveries of [0, 201, 1.5, '30']) bad({ budget: { maxDeliveries } }, /预算/);
    bad({ members: [{ name: 'lead', agent: 'pi', cwd: h.dir }] }, /目录/);
    symlinkSync(h.dir, join(h.cwd, 'outside')); bad({ members: [{ name: 'lead', agent: 'pi', cwd: join(h.cwd, 'outside') }] }, /目录/);
    const saved = h.teams.save({ ...draft, members: [{ name: 'lead', agent: 'missing', instruction: 'x'.repeat(4000) }], relations: [], budget: { maxDeliveries: 200 } });
    assert.equal(saved.members[0].name, 'lead');
    await assert.rejects(h.teams.dispatch({ teamId: saved.id, text: 'task', cwd: h.cwd, source: { kind: 'http' } }), /lead.*missing/);
    await assert.rejects(h.teams.dispatch({ teamId: h.definition.id, text: 'task', cwd: h.dir, source: { kind: 'http' } }), /目录/);
  } finally { await h.close(); }
});

test('authenticated CRUD, template, JSON import/export and dispatch APIs', async () => {
  const h = await setup();
  try {
    assert.equal((await fetch(h.base + '/api/teams')).status, 401);
    assert.equal((await h.api()).status, 200);
    const template = await (await h.api('/template')).json() as any;
    assert.deepEqual(template.members.map((m: any) => m.agent), ['pi', 'pi', 'pi']);
    const created = await (await h.api('', 'POST', { ...template, name: 'API' })).json() as any;
    assert.equal((await h.api('/' + created.id)).status, 200);
    assert.equal((await h.api('/' + created.id, 'PUT', { ...created, name: 'Changed' })).status, 200);
    const exported = await (await h.api('/' + created.id + '/export')).json() as any;
    assert.equal(exported.name, 'Changed');
    assert.equal((await h.api('/import', 'POST', exported)).status, 409);
    assert.equal((await h.api('/' + created.id, 'DELETE')).status, 204);
    const imported = await (await h.api('/import', 'POST', exported)).json() as any;
    assert.notEqual(imported.id, created.id);
    assert.equal((await h.api('/' + created.id)).status, 404);
    const response = await h.api('/' + imported.id + '/tasks', 'POST', { text: 'TEAM_FLOW', cwd: h.cwd });
    assert.equal(response.status, 202); const { threadId } = await response.json() as any;
    await h.settled(threadId);
    const confirm = () => fetch(`${h.base}/api/threads/${threadId}/coordination`, { method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'confirm' }) });
    assert.equal((await confirm()).status, 200); assert.equal((await confirm()).status, 409);
    const invalidTurn = await fetch(`${h.base}/api/threads/${threadId}/turns`, { method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ text: '' }) });
    assert.equal(invalidTurn.status, 400); assert.equal(h.coordination.view(threadId)!.team!.status, 'done');
  } finally { await h.close(); }
});

test('line-start mentions, punctuation, background, multiple peers and unauthorized plain text', () => {
  assert.deepEqual(parseTeamMentions('Context\n@dev: build\ninline @reviewer ignored\n@stranger ignored\n@reviewer：review', ['dev', 'reviewer']), [
    { to: 'dev', text: 'Context\n\nbuild\ninline @reviewer ignored\n@stranger ignored' }, { to: 'reviewer', text: 'Context\n\nreview' },
  ]);
  assert.deepEqual(parseTeamMentions('@dev\nTask', ['dev']), [{ to: 'dev', text: 'Task' }]);
  assert.deepEqual(parseTeamMentions(' @dev task\n@developer task\nemail @dev', ['dev']), []);
});

for (const scenario of ['TEAM_FLOW', 'TEAM_REWORK']) test(`scripted ${scenario}: delegation, automatic return, completion and snapshot isolation`, async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch(scenario);
    h.teams.save({ ...h.definition, description: 'changed later' }, h.definition.id);
    await h.settled(threadId);
    const view = h.coordination.view(threadId)!;
    assert.equal(view.team!.snapshot.description, h.definition.description);
    assert.equal(view.team!.delegations.every(d => d.closed), true);
    assert.equal(view.team!.deliveries, scenario === 'TEAM_FLOW' ? 4 : 8);
    assert.deepEqual(view.members.map(m => m.name), ['dev', 'reviewer']);
    for (const id of [threadId, ...view.members.map(m => m.id)]) {
      assert.equal(h.coordination.environment(id), undefined);
      assert.match(h.coordination.instructions(id, 'x'), /<loochat-team>/);
      await assert.rejects(h.coordination.command(id, { op: 'spawn' }), /@/);
    }
    const rootId = view.team!.taskId;
    await h.coordination.userSpoke(threadId); await h.manager.startTurn(threadId, { text: 'Additional requirements' });
    await h.settled(threadId); assert.equal(h.coordination.view(threadId)!.team!.taskId, rootId);
    await h.coordination.confirm(threadId);
    await h.coordination.userSpoke(threadId); await h.manager.startTurn(threadId, { text: scenario });
    await h.settled(threadId);
    const next = h.coordination.view(threadId)!;
    assert.equal(next.team!.deliveries, scenario === 'TEAM_FLOW' ? 4 : 8);
    assert.notEqual(next.team!.taskId, rootId); assert.equal(next.team!.snapshot.description, 'changed later');
    assert.deepEqual(next.members.map(m => m.id), view.members.map(m => m.id));
    assert.equal(next.team!.history[0].taskId, rootId);
    assert.deepEqual(next.messages.slice(0, view.messages.length), view.messages);
    assert.equal(next.messages.length, view.messages.length * 2);
    h.teams.remove(h.definition.id); await h.coordination.confirm(threadId);
    await h.coordination.userSpoke(threadId); await h.manager.startTurn(threadId, { text: 'after deletion' }); await h.settled(threadId);
  } finally { await h.close(); }
});

for (const action of ['userSpoke', 'resume'] as const) test(`budget exhaustion grants a fresh budget on ${action} and the same task can finish`, async () => {
  const h = await setup();
  try {
    h.teams.save({ ...h.definition, budget: { maxDeliveries: 2 } }, h.definition.id);
    const { threadId } = await h.dispatch(action === 'userSpoke' ? 'TEAM_FLOW' : 'TEAM_BUDGET'); await h.settled(threadId, 'needs_attention');
    assert.equal(h.coordination.view(threadId)!.paused, true);
    assert.equal(h.coordination.view(threadId)!.team!.deliveries, 2);
    await assert.rejects(h.coordination.confirm(threadId), /待确认/);
    const taskId = h.coordination.view(threadId)!.team!.taskId;
    await h.coordination[action](threadId);
    assert.equal(h.coordination.view(threadId)!.team!.deliveries, 0);
    await h.manager.startTurn(threadId, { text: 'TEAM_SHORT' });
    await h.settled(threadId);
    assert.equal(h.coordination.view(threadId)!.team!.taskId, taskId);
    assert.equal(h.coordination.view(threadId)!.team!.deliveries, 2);
  } finally { await h.close(); }
});

test('interrupted team root recovers into needs_attention without injecting CLI environment', async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch('slow');
    h.coordination.dispose();
    const manager = new ThreadManager(h.store, new Map(), [h.cwd], new AttachmentStore(h.dir));
    manager.recoverInterrupted();
    const restored = new CoordinationManager(h.store, manager, join(h.dir, 'other.sock'));
    try { await wait(() => restored.view(threadId)?.team?.status === 'needs_attention'); assert.equal(restored.view(threadId)!.paused, true); }
    finally { restored.dispose(); }
  } finally { await h.close(); }
});

test('CLI team ls and run use authenticated HTTP without socket credentials', async () => {
  const h = await setup();
  const cli = (args: string[], input = '') => new Promise<any>((resolve, reject) => {
    const child = spawn(process.execPath, [join(fixtures, '../../bin/loochat'), 'team', ...args], { env: { ...process.env, LOOCHAT_URL: h.base, LOOCHAT_TOKEN: 'test-token', LOOCHAT_TEAM_SOCKET: '', LOOCHAT_TEAM_KEY: '' }, stdio: 'pipe' });
    let out = '', err = ''; child.stdout.on('data', d => out += d); child.stderr.on('data', d => err += d); child.on('error', reject);
    child.on('exit', code => code ? reject(new Error(err)) : resolve(JSON.parse(out))); child.stdin.end(input);
  });
  try {
    assert.equal((await cli(['ls']))[0].id, h.definition.id);
    const task = await cli(['run', h.definition.name, '--cwd', h.cwd, '--task-file', '-'], 'TEAM_FLOW');
    await h.settled(task.threadId); assert.equal(h.coordination.view(task.threadId)!.team!.source.kind, 'cli');
  } finally { await h.close(); }
});

test('parallel delegations stay open until every peer returns; results link to the oldest outstanding request', async () => {
  const h = await setup();
  try {
    h.teams.save({ ...h.definition, relations: [{ kind: 'delegate', from: ['lead'], to: 'dev' }, { kind: 'delegate', from: ['lead'], to: 'reviewer' }] }, h.definition.id);
    const { threadId } = await h.dispatch('TEAM_MULTI'); await h.settled(threadId);
    const view = h.coordination.view(threadId)!;
    assert.equal(view.team!.delegations.length, 2);
    assert.ok(view.team!.delegations.every(d => d.closed));
    for (const d of view.messages.filter(d => d.kind === 'result')) {
      assert.ok(d.replyTo); const asked = view.messages.find(v => v.id === d.replyTo)!;
      assert.equal(asked.from, d.to); assert.equal(asked.to, d.from);
    }
  } finally { await h.close(); }
});

for (const text of ['fail', 'TEAM_MEMBER_ERROR']) test(`errors in ${text} stop routing and keep the failure visible`, async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch(text); await h.settled(threadId, 'needs_attention');
    assert.equal(h.coordination.view(threadId)!.paused, true);
    assert.ok(h.coordination.view(threadId)!.team!.error);
    await h.coordination.userSpoke(threadId);
    assert.equal(h.coordination.view(threadId)!.team!.status, 'running');
    await h.manager.startTurn(threadId, { text: 'TEAM_FINISH' });
    await h.settled(threadId);
    if (text === 'TEAM_MEMBER_ERROR') {
      const prompt = h.store.listEventsOfType(threadId, 'turn.started').at(-1)!.data.text;
      assert.match(String(prompt), /以下委派没有交回，已关闭：dev（失败：provider exploded/);
      assert.ok(h.coordination.view(threadId)!.team!.delegations.every(d => d.closed));
      assert.doesNotMatch(h.coordination.instructions(threadId, 'next'), /以下委派没有交回/);
    }
  } finally { await h.close(); }
});

test('team runtime has no CLI credentials and members honor fixed directories', async () => {
  const h = await setup();
  try {
    const cwd = join(h.cwd, 'fixed'); mkdirSync(cwd);
    h.teams.save({ ...h.definition, members: h.definition.members.map(m => m.name === 'dev' ? { ...m, cwd } : m) }, h.definition.id);
    const first = await h.dispatch('TEAM_ENV'); await h.settled(first.threadId);
    const answer = h.store.listEvents(first.threadId, 0, -1).filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
    assert.match(answer, /loochat-ready:false/);
    const flow = await h.dispatch(); await h.settled(flow.threadId);
    assert.equal(h.coordination.view(flow.threadId)!.members.find(m => m.name === 'dev')!.thread!.cwd, cwd);
  } finally { await h.close(); }
});

test('recovery replays a committed finish exactly once before delivering new work', async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch('TEAM_FLOW'); await h.settled(threadId);
    h.coordination.dispose();
    const persisted = JSON.parse(h.store.coordinationRecords().find(r => r.id === threadId)!.data);
    const final = persisted.team.resultTurnId;
    persisted.team.status = 'running'; persisted.team.resultTurnId = null;
    persisted.team.processed = persisted.team.processed.filter((id: string) => id !== final);
    const last = persisted.deliveries.find((d: any) => d.turnId === final); last.state = 'running';
    h.store.saveCoordination(threadId, JSON.stringify(persisted));
    for (let n = 0; n < 2; n++) {
      const restored = new CoordinationManager(h.store, h.manager, join(h.dir, 'recovered.sock'));
      try {
        await wait(() => restored.view(threadId)?.team?.status === 'awaiting_confirm');
        assert.equal(restored.view(threadId)!.team!.deliveries, 4);
        assert.equal(restored.view(threadId)!.paused, false);
      } finally { restored.dispose(); }
    }
  } finally { await h.close(); }
});

test('new tasks replace changed member instances while retaining previous thread links', async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch(); await h.settled(threadId);
    const previous = h.coordination.view(threadId)!.members;
    await h.coordination.confirm(threadId);
    h.teams.save({ ...h.definition, members: h.definition.members.map(m => m.name === 'dev' ? { ...m, agent: 'second' } : m) }, h.definition.id);
    await h.coordination.userSpoke(threadId); await h.manager.startTurn(threadId, { text: 'TEAM_FLOW' }); await h.settled(threadId);
    const current = h.coordination.view(threadId)!.members;

    assert.notEqual(current.find(m => m.name === 'dev')!.id, previous.find(m => m.name === 'dev')!.id);
    assert.equal(current.find(m => m.name === 'dev')!.thread!.agent, 'second');
    assert.equal(current.find(m => m.name === 'reviewer')!.id, previous.find(m => m.name === 'reviewer')!.id);
    assert.equal(h.coordination.metadata(previous[0].id)!.rootId, threadId);
    await h.coordination.confirm(threadId);
    h.teams.save({ ...h.definition, members: h.definition.members.map(m => m.name === 'lead' ? { ...m, agent: 'second' } : m) }, h.definition.id);
    await assert.rejects(h.coordination.userSpoke(threadId), /入口实例/);
    assert.equal(h.coordination.view(threadId)!.team!.status, 'done');
  } finally { await h.close(); }
});

test('entry and member settings use snapshot models and lead is an ordinary non-entry role', async () => {
  const h = await setup();
  try {
    const models = await h.manager.listModels('pi');
    const model = models.find(m => m.efforts?.includes('high')) ?? models[0];
    const effort = model.efforts?.includes('high') ? 'high' : undefined;
    h.teams.save({ ...h.definition, entry: 'chief', members: [{ name: 'chief', agent: 'pi', model: model.id, effort }, { name: 'lead', agent: 'pi', model: model.id, effort }], relations: [{ kind: 'delegate', from: ['chief'], to: 'lead' }] }, h.definition.id);
    const { threadId } = await h.dispatch('TEAM_CUSTOM'); await h.settled(threadId);
    const view = h.coordination.view(threadId)!;
    assert.equal(view.members[0].name, 'lead'); assert.equal(view.members[0].thread!.model, model.id);
    assert.equal(h.manager.threadView(threadId).model, model.id);
    if (effort) assert.equal(view.members[0].thread!.effort, effort);
    assert.equal(view.team!.delegations[0].from, 'chief'); assert.ok(view.team!.delegations[0].closed);
  } finally { await h.close(); }
});

test('a crash after task creation but before its first turn is an interrupted task', async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch('TEAM_ENV'); await h.settled(threadId);
    h.coordination.dispose();
    const fresh = await h.manager.createThread({ agent: 'pi', cwd: h.cwd });
    const record = JSON.parse(h.store.coordinationRecords().find(r => r.id === threadId)!.data);
    record.rootId = fresh.id; record.members = []; record.deliveries = [];
    record.team.status = 'running'; record.team.processed = []; record.team.resultTurnId = null;
    h.store.saveCoordination(fresh.id, JSON.stringify(record));
    const restored = new CoordinationManager(h.store, h.manager, join(h.dir, 'restart.sock'));
    try { assert.equal(restored.view(fresh.id)!.team!.status, 'needs_attention'); assert.equal(restored.view(fresh.id)!.paused, true); }
    finally { restored.dispose(); }
  } finally { await h.close(); }
});

for (const action of ['userSpoke', 'resume'] as const) test(`stopping the root closes running and queued delegations on ${action}`, async context => {
  const h = await setup();
  try {
    h.teams.save({ ...h.definition, relations: [{ kind: 'delegate', from: ['lead'], to: 'dev' }, { kind: 'delegate', from: ['lead'], to: 'reviewer' }] }, h.definition.id);
    const start = h.manager.startTurn.bind(h.manager);
    const mocked = context.mock.method(h.manager, 'startTurn', async (...args: Parameters<typeof start>) => {
      if (args[2] && h.manager.threadView(args[0]).title === 'reviewer') throw new HttpError(409, 'busy');
      return start(...args);
    });
    const { threadId } = await h.dispatch('TEAM_STOP');
    await wait(() => {
      const view = h.coordination.view(threadId)!;
      return view.messages.some(d => d.state === 'queued') && view.members.some(m => m.name === 'dev' && m.thread?.status === 'running');
    });
    await h.manager.cancelTurn(threadId);
    await wait(() => h.coordination.view(threadId)!.messages.every(d => d.state === 'stopped'));
    assert.equal(h.coordination.view(threadId)!.paused, true);
    mocked.mock.restore();
    await h.coordination[action](threadId);
    const notice = h.coordination.instructions(threadId, 'preview');
    assert.match(notice, /dev（已停止/); assert.match(notice, /reviewer（已停止/);
    assert.equal(h.coordination.instructions(threadId, 'preview'), notice);
    await h.manager.startTurn(threadId, { text: 'TEAM_FINISH' });
    await h.settled(threadId);
    assert.match(String(h.store.listEventsOfType(threadId, 'turn.started').at(-1)!.data.text), /以下委派没有交回/);
    assert.doesNotMatch(h.coordination.instructions(threadId, 'next'), /以下委派没有交回/);
  } finally { await h.close(); }
});

test('delivery startup failure is closed on resume and the notice survives a refused turn', async context => {
  const h = await setup();
  try {
    const start = h.manager.startTurn.bind(h.manager);
    const mocked = context.mock.method(h.manager, 'startTurn', async (...args: Parameters<typeof start>) => {
      if (args[2]) throw new Error('Delivery launch rejected');
      return start(...args);
    });
    const { threadId } = await h.dispatch('TEAM_FLOW'); await h.settled(threadId, 'needs_attention');
    mocked.mock.restore();
    await h.coordination.resume(threadId);
    await assert.rejects(h.manager.startTurn(threadId, { text: 'TEAM_FINISH', videos: ['missing-video'] }));
    assert.match(h.coordination.instructions(threadId, 'preview'), /dev（失败：Delivery launch rejected）/);
    await h.manager.startTurn(threadId, { text: 'TEAM_FINISH' }); await h.settled(threadId);
    const prompt = String(h.store.listEventsOfType(threadId, 'turn.started').at(-1)!.data.text);
    assert.match(prompt, /dev（失败：Delivery launch rejected）/);
    assert.doesNotMatch(h.coordination.instructions(threadId, 'next'), /以下委派没有交回/);
  } finally { await h.close(); }
});

test('resume preserves an unpaused awaiting_confirm task and its confirmable result', async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch(); await h.settled(threadId);
    const before = h.coordination.view(threadId);
    const response = await fetch(`${h.base}/api/threads/${threadId}/coordination`, {
      method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'resume' }),
    });
    assert.equal(response.status, 200);
    assert.deepEqual(h.coordination.view(threadId), before);
    await h.coordination.confirm(threadId);
    assert.equal(h.coordination.view(threadId)!.team!.status, 'done');
  } finally { await h.close(); }
});

test('mixed interrupted and committed unprocessed results survive recovery as a durable one-time notice', async () => {
  const h = await setup();
  let restored: CoordinationManager | undefined;
  try {
    h.teams.save({ ...h.definition, relations: [{ kind: 'delegate', from: ['lead'], to: 'dev' }, { kind: 'delegate', from: ['lead'], to: 'reviewer' }] }, h.definition.id);
    const { threadId } = await h.dispatch('TEAM_MULTI'); await h.settled(threadId);
    h.coordination.dispose();
    // Restore the durable mailbox boundary before either member's finish was processed.
    const record = JSON.parse(h.store.coordinationRecords().find(r => r.id === threadId)!.data);
    record.deliveries = record.deliveries.filter((d: any) => d.kind === 'message');
    const dev = record.members.find((m: any) => m.name === 'dev');
    const reviewer = record.members.find((m: any) => m.name === 'reviewer');
    const devJob = record.deliveries.find((d: any) => d.to === dev.id);
    const reviewJob = record.deliveries.find((d: any) => d.to === reviewer.id);
    devJob.turnId = 'interrupted-member-turn';
    h.store.appendEvent(dev.id, devJob.turnId, 'turn.started', { text: 'Pending implementation' });
    h.store.updateThread(dev.id, { status: 'running' });
    h.manager.recoverInterrupted();
    for (const d of record.deliveries) { d.state = 'running'; d.result = ''; }
    for (const d of record.team.delegations) d.closed = false;
    record.team.processed = record.team.processed.filter((id: string) => id !== reviewJob.turnId);
    record.team.status = 'running'; record.team.resultTurnId = null;
    h.store.saveCoordination(threadId, JSON.stringify(record));
    restored = new CoordinationManager(h.store, h.manager, join(h.dir, 'restored.sock'));
    await restored.userSpoke(threadId); // Serializes after the recovery replay.
    assert.ok(restored.view(threadId)!.team!.delegations.every(d => d.closed));
    assert.match(restored.instructions(threadId, 'preview'), /reviewer（已返回但未送达：Reviewer finished）/);
    assert.match(restored.instructions(threadId, 'preview'), /dev（失败：The hub restarted/);
    restored.dispose();
    restored = new CoordinationManager(h.store, h.manager, join(h.dir, 'restored-again.sock'));
    await h.manager.startTurn(threadId, { text: 'TEAM_FINISH' });
    await wait(() => restored!.view(threadId)!.team!.status === 'awaiting_confirm');
    const prompt = String(h.store.listEventsOfType(threadId, 'turn.started').at(-1)!.data.text);
    assert.match(prompt, /reviewer（已返回但未送达：Reviewer finished）/);
    assert.doesNotMatch(restored.instructions(threadId, 'next'), /以下委派没有交回/);
    assert.equal(restored.view(threadId)!.messages.length, 2);
  } finally { restored?.dispose(); await h.close(); }
});

test('continuation preserves a queued delegation while closing a stopped member', async context => {
  const h = await setup();
  try {
    h.teams.save({ ...h.definition, relations: [{ kind: 'delegate', from: ['lead'], to: 'dev' }, { kind: 'delegate', from: ['lead'], to: 'reviewer' }] }, h.definition.id);
    const start = h.manager.startTurn.bind(h.manager);
    const mocked = context.mock.method(h.manager, 'startTurn', async (...args: Parameters<typeof start>) => {
      if (args[2] && h.manager.threadView(args[0]).title === 'reviewer') throw new HttpError(409, 'busy');
      return start(...args);
    });
    const { threadId } = await h.dispatch('TEAM_STOP');
    await wait(() => h.coordination.view(threadId)!.messages.some(d => d.state === 'queued') && h.coordination.view(threadId)!.members.some(m => m.name === 'dev' && m.thread?.status === 'running'));
    const dev = h.coordination.view(threadId)!.members.find(m => m.name === 'dev')!;
    await h.manager.cancelTurn(dev.id); await h.settled(threadId, 'needs_attention');
    await h.coordination.resume(threadId);
    const delegates = h.coordination.view(threadId)!.team!.delegations;
    assert.equal(delegates.find(d => d.to === 'dev')!.closed, true);
    assert.equal(delegates.find(d => d.to === 'reviewer')!.closed, false);
    mocked.mock.restore();
    await h.settled(threadId);
    assert.match(String(h.store.listEventsOfType(threadId, 'turn.started').at(-1)!.data.text), /dev（已停止/);
  } finally { await h.close(); }
});

for (const action of ['userSpoke', 'resume'] as const) test(`${action} preserves an idle member waiting on a live downstream after budget exhaustion`, async () => {
  const h = await setup();
  try {
    h.teams.save({ ...h.definition, budget: { maxDeliveries: 2 } }, h.definition.id);
    const { threadId } = await h.dispatch('TEAM_WAIT_FLOW');
    await wait(() => {
      const view = h.coordination.view(threadId)!;
      return view.members.some(m => m.name === 'reviewer' && m.thread?.status === 'running')
        && view.messages.length === 2 && view.messages[0].state === 'returned';
    });
    // The lead attempts one more dispatch while the reviewer is still working.
    await h.coordination.userSpoke(threadId);
    await h.manager.startTurn(threadId, { text: 'TEAM_FLOW' });
    await h.settled(threadId, 'needs_attention');
    const paused = h.coordination.view(threadId)!;
    assert.match(paused.team!.error!, /预算/);
    assert.equal(paused.members.find(m => m.name === 'dev')!.thread!.status, 'idle');
    assert.equal(paused.members.find(m => m.name === 'reviewer')!.thread!.status, 'running');
    assert.equal(paused.team!.delegations.length, 2);
    await h.coordination[action](threadId);
    const resumed = h.coordination.view(threadId)!;
    assert.ok(resumed.team!.delegations.every(d => !d.closed));
    assert.doesNotMatch(h.coordination.instructions(threadId, 'preview'), /以下委派没有交回/);
    assert.equal(resumed.team!.continuationNotices, undefined);
    writeFileSync(join(h.cwd, 'team-review-release'), 'ok');
    await h.settled(threadId);
    const complete = h.coordination.view(threadId)!;
    assert.ok(complete.team!.delegations.every(d => d.closed));
    assert.equal(complete.messages.length, 4);
    const returned = complete.messages.find(d => d.to === threadId)!;
    // An unauthorized @lead would survive as literal text in an automatic return with no replyTo.
    assert.equal(returned.text, 'Completed TEAM_DELIVERED');
    assert.equal(returned.replyTo, paused.team!.delegations[0].deliveryId);
    assert.doesNotMatch(String(h.store.listEventsOfType(threadId, 'turn.started').at(-1)!.data.text), /以下委派没有交回/);
  } finally { await h.close(); }
});

for (const action of ['userSpoke', 'resume'] as const) test(`${action} closes the entire idle chain when its downstream reviewer fails`, async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch('TEAM_WAIT_FLOW');
    await wait(() => h.coordination.view(threadId)!.members.some(m => m.name === 'reviewer' && m.thread?.status === 'running'));
    writeFileSync(join(h.cwd, 'team-review-release'), 'error');
    await h.settled(threadId, 'needs_attention');
    await h.coordination[action](threadId);
    const resumed = h.coordination.view(threadId)!;
    assert.equal(resumed.team!.delegations.length, 2);
    assert.ok(resumed.team!.delegations.every(d => d.closed));
    assert.equal(resumed.team!.continuationNotices!.length, 2);
    const notice = h.coordination.instructions(threadId, 'preview');
    assert.match(notice, /dev（已返回但未送达：@reviewer TEAM_REVIEW_WAIT）/);
    assert.match(notice, /reviewer（失败：reviewer exploded）/);
    await h.manager.startTurn(threadId, { text: 'TEAM_FINISH' });
    await h.settled(threadId);
  } finally { await h.close(); }
});

test('an idle delegation cycle does not keep itself alive on continuation', async () => {
  const h = await setup();
  try {
    h.teams.save({ ...h.definition, relations: [...h.definition.relations, { kind: 'delegate', from: ['reviewer'], to: 'lead' }] }, h.definition.id);
    const { threadId } = await h.dispatch('TEAM_CYCLE');
    await wait(() => {
      const view = h.coordination.view(threadId)!;
      return view.messages.length === 3 && view.messages.every(d => d.state === 'returned') && !!view.team!.resultTurnId;
    });
    const before = h.coordination.view(threadId)!;
    assert.deepEqual(before.team!.delegations.map(d => [d.from, d.to]), [['lead', 'dev'], ['dev', 'reviewer'], ['reviewer', 'lead']]);
    assert.ok(before.team!.delegations.every(d => !d.closed));
    await h.manager.cancelTurn(threadId);
    await h.coordination.resume(threadId);
    const resumed = h.coordination.view(threadId)!;
    assert.ok(resumed.team!.delegations.every(d => d.closed));
    assert.equal(resumed.team!.continuationNotices!.length, 3);
    await h.manager.startTurn(threadId, { text: 'TEAM_FINISH' });
    await h.settled(threadId);
  } finally { await h.close(); }
});

test('a member spoken to directly answers the person and routes nothing in the team', async () => {
  const h = await setup();
  try {
    const { threadId } = await h.dispatch(); await h.settled(threadId);
    const before = h.coordination.view(threadId)!;
    const dev = before.members.find(m => m.name === 'dev')!;
    const sent = await fetch(`${h.base}/api/threads/${dev.id}/turns`, { method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ text: 'rename the helper' }) });
    assert.equal(sent.status, 202);
    await wait(() => h.coordination.view(threadId)!.messages.some(d => d.from === 'user' && d.state === 'returned'));
    const prompt = h.store.listEvents(dev.id, 0, -1).filter(e => e.type === 'turn.started').at(-1)!;
    assert.match(String(prompt.data.text), /用户直接发给你的：直接回复用户，不要 @ 任何人/);
    await new Promise(r => setTimeout(r, 200));
    const after = h.coordination.view(threadId)!;
    assert.equal(after.team!.status, 'awaiting_confirm');
    assert.equal(after.team!.deliveries, before.team!.deliveries);
    assert.equal(after.messages.filter(d => d.from !== 'user').length, before.messages.length);
    assert.equal(h.manager.threadView(threadId).status, 'idle');
  } finally { await h.close(); }
});

const TEAM_PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9c8AAAAASUVORK5CYII=', 'base64');

test('team tasks accept images, retain attachments and allow image-only continuations', async () => {
  const h = await setup();
  try {
    const summary = (await (await h.api()).json() as any[]).find(team => team.id === h.definition.id);
    assert.equal(summary.entryAgent, 'pi');
    assert.equal(summary.entryModel, null);
    assert.equal(summary.members, undefined);
    // Larger than the ordinary JSON limit, still within the normal image contract.
    const data = Buffer.concat([TEAM_PNG, Buffer.alloc(1024 * 1024)]).toString('base64');
    const response = await h.api(`/${h.definition.id}/tasks`, 'POST', { text: 'inspect-work-mode', images: [{ data, mimeType: 'image/jpeg' }], cwd: h.cwd });
    assert.equal(response.status, 202);
    const { threadId } = await response.json() as any;
    await h.settled(threadId);
    const start = h.store.listEventsOfType(threadId, 'turn.started')[0];
    const attached = (start.data.images as Array<{ id: string; mimeType: string; size: number }>)[0];
    assert.equal(attached.mimeType, 'image/png');
    const answer = h.store.listEventsOfType(threadId, 'message.delta').map(e => e.data.text).join('');
    assert.equal(JSON.parse(answer).images[0].data, data);
    const downloaded = await fetch(`${h.base}/api/threads/${threadId}/attachments/${attached.id}`, { headers: { Authorization: 'Bearer test-token' } });
    assert.equal(downloaded.status, 200);
    assert.equal(Buffer.from(await downloaded.arrayBuffer()).toString('base64'), data);
    await h.coordination.confirm(threadId);
    const taskId = h.coordination.view(threadId)!.team!.taskId;
    const followup = await fetch(`${h.base}/api/threads/${threadId}/turns`, { method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ images: [{ data: TEAM_PNG.toString('base64') }] }) });
    assert.equal(followup.status, 202);
    await h.settled(threadId);
    assert.notEqual(h.coordination.view(threadId)!.team!.taskId, taskId);
    assert.equal((h.store.listEventsOfType(threadId, 'turn.started').at(-1)!.data.images as unknown[]).length, 1);
    const imageOnly = await h.api(`/${h.definition.id}/tasks`, 'POST', { images: [{ data: TEAM_PNG.toString('base64') }], cwd: h.cwd });
    assert.equal(imageOnly.status, 202);
    const next = await imageOnly.json() as any;
    await h.settled(next.threadId);
    assert.equal(h.manager.threadView(next.threadId).title, h.definition.name);
  } finally { await h.close(); }
});

test('invalid team images and unsupported models are rejected before creating or reopening a task', async () => {
  const h = await setup();
  try {
    const count = () => (h.store.db.prepare('SELECT COUNT(*) AS n FROM threads').get() as { n: number }).n;
    const before = count();
    for (const images of ['invalid', [{ data: 'invalid' }], Array.from({ length: 7 }, () => ({ data: TEAM_PNG.toString('base64') }))]) {
      assert.equal((await h.api(`/${h.definition.id}/tasks`, 'POST', { text: 'task', images, cwd: h.cwd })).status, 400);
      assert.equal(count(), before);
    }
    h.teams.save({ ...h.definition, members: h.definition.members.map(m => m.name === h.definition.entry ? { ...m, model: 'fake/plain' } : m) }, h.definition.id);
    const unsupported = await h.api(`/${h.definition.id}/tasks`, 'POST', { text: 'task', images: [{ data: TEAM_PNG.toString('base64') }], cwd: h.cwd });
    assert.equal(unsupported.status, 400);
    assert.match((await unsupported.json() as any).error, /cannot read images/);
    assert.equal(count(), before);
    h.teams.save(h.definition, h.definition.id);
    const { threadId } = await h.dispatch();
    await h.settled(threadId); await h.coordination.confirm(threadId);
    const taskId = h.coordination.view(threadId)!.team!.taskId;
    const invalid = await fetch(`${h.base}/api/threads/${threadId}/turns`, { method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, body: JSON.stringify({ text: 'task', images: [{ data: 'invalid' }] }) });
    assert.equal(invalid.status, 400);
    assert.equal(h.coordination.view(threadId)!.team!.status, 'done');
    assert.equal(h.coordination.view(threadId)!.team!.taskId, taskId);
  } finally { await h.close(); }
});
