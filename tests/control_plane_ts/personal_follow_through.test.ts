import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp, writeFile, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createServer} from 'node:http';
import {FileAuthorityStore} from '../../loopx/control_plane/coordination/file_authority_store.ts';
import {projection} from './shadow_file_fixture.ts';
import {buildReview, config, digest, instant, type FollowThroughConfig, type SourceWindow} from '../../loopx/control_plane/work_items/personal_follow_through/contract.ts';
import {applyReview, readTodos} from '../../loopx/control_plane/work_items/personal_follow_through/service.ts';
import {captureLark} from '../../loopx/extensions/lark/personal_follow_through.ts';

const execute = promisify(execFile);
const source: SourceWindow = {binding: 'selected-group', chat: 'oc_test', start: '2026-10-01T00:00:00Z', end: '2026-10-02T00:00:00Z',
  capturedAt: '2026-10-02T00:00:00Z', complete: true,
  messages: [{id: 'om_promise', revision: 'r1', sender: 'ou_owner', text: 'I will prepare the draft by 2026-10-03T10:00:00Z.'}]};
for (const m of source.messages) m.revision = digest({content: m.text, updated: '100'});
const readerFor = (window: SourceWindow) => async () => ({ok: true, identity: 'bot', data: {
  has_more: false, messages: window.messages.map(m => ({message_id: m.id, msg_type: 'text',
    chat_id: window.chat, sender: {id: m.sender}, content: m.text, create_time: '100'}))}});
const proposal = {kind: 'create', title: 'Prepare draft', responsible_id: 'ou_owner', source_ids: ['om_promise'],
  target_todo_id: null, due_at: '2026-10-03T10:00:00Z', due_basis: 'by 2026-10-03T10:00:00Z'};
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'loopx-personal-'));
  const c: FollowThroughConfig = {schema_version: 'personal_follow_through_config_v0', enabled: true, runtime_root: root,
    goal_id: 'goal-a', goal_instance_id: 'ginst_00000000000000000000000000000001', registry_path: join(root, 'registry.json'), owner_id: 'ou_owner', binding: 'selected-group', chat_id: 'oc_test', lark_profile: 'test',
    model: {endpoint: 'http://127.0.0.1:1/chat/completions', name: 'fixture', key_env: 'FOLLOW_THROUGH_TEST_KEY'}};
  await writeFile(c.registry_path, JSON.stringify({schema_version: '0.1', registry_role: 'project-local', common_runtime_root: root, goals: [{id: c.goal_id, goal_instance_id: c.goal_instance_id, activation_state: 'active', registered_agents: []}]}), {mode: 0o600});
  const configPath = join(root, 'profile.json');
  await writeFile(configPath, JSON.stringify(c), {mode: 0o600});
  const store = new FileAuthorityStore(join(root, 'authority', 'file-v0'), 'goal-a');
  const seeded = await store.commitAuthority({expected_provider_revision: null, operation_id: 'fixture-seed', events: [], receipts: [], next_projection: projection([], [], 'soft_claim')});
  assert.equal(seeded.status, 'applied');
  return {root, c, configPath};
}

test('reviewed commitment survives retry and a deadline correction in the canonical store', async t => {
  const f = await fixture(); t.after(() => rm(f.root, {recursive: true, force: true}));
  const first = buildReview(f.c, source, proposal, await readTodos(f.c));
  const applied = await applyReview(f.configPath, first, digest(first), readerFor(source));
  assert.notEqual(applied.status, 'failed', JSON.stringify(applied));
  assert.notEqual((await applyReview(f.configPath, first, digest(first), readerFor(source))).status, 'failed');
  const created = await readTodos(f.c);
  assert.equal(created.todos.length, 1);
  assert.equal(created.todos[0].text, 'Prepare draft');
  assert.equal(created.todos[0].role, 'user');
  const changed = {...source, messages: [...source.messages,
    {id: 'om_update', revision: 'r1', sender: 'ou_owner', text: 'Change that deadline to 2026-10-05T10:00:00Z.'}]};
  for (const m of changed.messages) m.revision = digest({content: m.text, updated: '100'});
  const amendment = buildReview(f.c, changed, {...proposal, kind: 'amend', target_todo_id: first.todo_id,
    source_ids: ['om_promise', 'om_update'], due_at: '2026-10-05T10:00:00Z', due_basis: '2026-10-05T10:00:00Z'}, created);
  assert.notEqual((await applyReview(f.configPath, amendment, digest(amendment), readerFor(changed))).status, 'failed');
  assert.notEqual((await applyReview(f.configPath, amendment, digest(amendment), readerFor(changed))).status, 'failed');
  const reopened = await readTodos(f.c);
  assert.equal(reopened.todos.length, 1);
  assert.equal(reopened.todos[0].todo_id, first.todo_id);
  assert.equal(reopened.todos[0].status, 'open');
  assert.match(String(reopened.todos[0].note), /2026-10-05T10:00:00Z/);
});

test('unapproved, wrong-owner, incomplete, stale and revoked inputs cannot write', async t => {
  const f = await fixture(); t.after(() => rm(f.root, {recursive: true, force: true}));
  const before = await readTodos(f.c);
  for (const bad of [{...proposal, responsible_id: 'ou_other'}, {...proposal, source_ids: ['invented']},
    {...proposal, kind: 'complete'}, {...proposal, due_at: 'tomorrow'}]) {
    assert.throws(() => buildReview(f.c, source, bad, before));
  }
  assert.throws(() => buildReview(f.c, {...source, complete: false}, proposal, before), /incomplete/);
  const packet = buildReview(f.c, source, proposal, before);
  await assert.rejects(applyReview(f.configPath, packet, 'wrong', readerFor(source)), /identity/);
  assert.equal((await readTodos(f.c)).todos.length, 0);
  await applyReview(f.configPath, packet, digest(packet), readerFor(source));
  const other = buildReview(f.c, {...source, messages: [{...source.messages[0], id: 'om_second'}]},
    {...proposal, source_ids: ['om_second'], title: 'A separate promise'}, before);
  await assert.rejects(applyReview(f.configPath, other, digest(other), readerFor(other.source)), /stale/);
  await writeFile(f.configPath, JSON.stringify({...f.c, enabled: false}));
  await assert.rejects(applyReview(f.configPath, packet, digest(packet), readerFor(source)), /disabled/);
  assert.equal((await readTodos(f.c)).todos.length, 1);
});

test('Lark capture stops on pagination gaps, changed identities and disabled scope', async () => {
  const c = config({schema_version: 'personal_follow_through_config_v0', enabled: true, runtime_root: '/unused',
    goal_id: 'a', goal_instance_id: 'ginst_00000000000000000000000000000001', registry_path: '/unused/registry.json', owner_id: 'ou_owner', binding: 'b', chat_id: 'oc_test', lark_profile: 'test',
    model: {endpoint: 'https://example.com/chat/completions', name: 'test', key_env: 'TEST_KEY'}});
  let calls = 0;
  const reader = async () => {calls++; return {ok: true, identity: 'bot', data: {messages: [], has_more: true, page_token: 'same'}};};
  await assert.rejects(captureLark({...c, enabled: false}, source.start, source.end, async () => true, reader), /disabled/);
  assert.equal(calls, 0);
  await assert.rejects(captureLark(c, source.start, source.end, async () => true, reader), /cursor/);
  assert.equal(calls, 2);
  await assert.rejects(captureLark(c, source.start, source.end, async () => true, async () => ({ok: true, identity: 'user', data: {messages: [], has_more: false}})), /identity/);
});

test('CLI end-to-end uses Node processes, HTTP model transport and real durable authority', async t => {
  const f = await fixture(); t.after(() => rm(f.root, {recursive: true, force: true}));
  let modelCalls = 0;
  const server = createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    const request = JSON.parse(body); assert.equal(request.model, 'fixture');
    assert.equal(req.headers.authorization, 'Bearer synthetic-test-key');
    assert.equal(request.tools, undefined);
    modelCalls++;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({choices: [{finish_reason: 'stop', message: {content: JSON.stringify({candidates: [proposal, {...proposal, source_ids: ['om_second'], title: 'Prepare appendix'}]})}}]}));
  });
  await new Promise<void>(ok => server.listen(0, '127.0.0.1', ok));
  t.after(() => {server.closeAllConnections(); server.close();});
  const address = server.address(); assert.ok(address && typeof address === 'object');
  f.c.model.endpoint = `http://127.0.0.1:${address.port}/chat/completions`;
  await writeFile(f.configPath, JSON.stringify(f.c));
  const larkData = join(f.root, 'lark.json');
  await writeFile(larkData, JSON.stringify({ok: true, identity: 'bot', data: {has_more: false, messages: [
    {message_id: 'om_promise', chat_id: 'oc_test', msg_type: 'text', sender: {id: 'ou_owner'}, content: source.messages[0].text, create_time: '100'}, {message_id: 'om_second', chat_id: 'oc_test', msg_type: 'text', sender: {id: 'ou_owner'}, content: 'I will also prepare an appendix by 2026-10-03T10:00:00Z.', create_time: '101'}]}}));
  const binary = join(f.root, 'lark-cli');
  await writeFile(binary, `#!${process.execPath}\nconst fs = require('node:fs'); const a=process.argv.slice(2); if(a.includes('--execute') || !a.includes('+chat-messages-list') || !a.includes('oc_test')) process.exit(9); process.stdout.write(fs.readFileSync(process.env.TEST_LARK_DATA,'utf8'));\n`, {mode: 0o700});
  const cli = resolve('loopx/control_plane/work_items/personal_follow_through/cli.ts');
  const run = async (...args: string[]) => {
    try {
      const {stdout} = await execute(process.execPath, ['--no-warnings', '--experimental-sqlite', '--experimental-strip-types', cli, ...args, '--config', f.configPath],
        {env: {PATH: f.root, FOLLOW_THROUGH_TEST_KEY: 'synthetic-test-key', TEST_LARK_DATA: larkData}, timeout: 30_000});
      return JSON.parse(stdout);
    } catch (e) {
      const stdout = (e as {stdout?: string}).stdout;
      if (stdout) return JSON.parse(stdout);
      throw e;
    }
  };
  const batch = join(f.root, 'review.json');
  const prepared = await run('prepare', '--start', source.start, '--end', source.end, '--output', batch, '--allow-model');
  assert.equal(prepared.status, 'prepared', JSON.stringify(prepared));
  assert.equal(prepared.count, 2);
  assert.equal(modelCalls, 1);
  const inspected = await run('inspect', '--packet', batch, '--index', '0');
  assert.equal(inspected.packet.candidate.title, 'Prepare draft');
  assert.equal((await run('brief')).todos.length, 0);
  const apply = await run('apply', '--packet', batch, '--index', '0', '--approve-digest', inspected.digest);
  assert.notEqual(apply.status, 'failed', JSON.stringify(apply));
  const replay = await run('apply', '--packet', batch, '--index', '0', '--approve-digest', inspected.digest);
  assert.notEqual(replay.status, 'failed', JSON.stringify(replay));
  assert.equal((await run('brief')).todos.length, 1);
  const refreshedPath = join(f.root, 'second-review.json');
  const refreshed = await run('refresh', '--packet', batch, '--index', '1', '--output', refreshedPath);
  assert.equal(refreshed.status, 'review_ready', JSON.stringify(refreshed));
  assert.equal(modelCalls, 1);
  assert.notEqual((await run('apply', '--packet', refreshedPath, '--approve-digest', refreshed.digest)).status, 'failed');
  assert.equal((await run('brief')).todos.length, 2);
  // A changed provider source cannot use an old reviewed packet.
  const external = JSON.parse(await readFile(larkData, 'utf8'));
  external.data.messages[0].content = 'This promise was withdrawn.';
  await writeFile(larkData, JSON.stringify(external));
  assert.equal((await run('apply', '--packet', batch, '--index', '0', '--approve-digest', inspected.digest)).error, 'source_changed_review_again');
  await writeFile(f.configPath, JSON.stringify({...f.c, enabled: false}));
  assert.equal((await run('prepare', '--allow-model')).status, 'disabled');
  assert.equal(modelCalls, 1);
});

 test('dates reject calendar overflow and amendments preserve omission versus explicit clear', async t => {
  assert.throws(() => instant('2026-02-30T10:00:00Z', 'due_at'), /invalid/);
  assert.equal(instant('2024-02-29T10:00:00Z', 'due_at'), '2024-02-29T10:00:00Z');
  const f = await fixture(); t.after(() => rm(f.root, {recursive: true, force: true}));
  const first = buildReview(f.c, source, proposal, await readTodos(f.c));
  await applyReview(f.configPath, first, digest(first), readerFor(source));
  const amended = {...proposal, kind: 'amend', target_todo_id: first.todo_id, title: 'Prepare reviewed draft'};
  const {due_at, due_basis, ...withoutDate} = amended;
  const review = buildReview(f.c, source, withoutDate, await readTodos(f.c));
  assert.equal(Object.hasOwn(review.candidate, 'due_at'), false);
  assert.match(review.note, /2026-10-03T10:00:00Z/);
  const cleared = buildReview(f.c, source, {...amended, due_at: null, due_basis: null}, await readTodos(f.c));
  assert.equal(cleared.candidate.due_at, null);
});

test('edited source cannot create another task and service rechecks withdrawal', async t => {
  const f = await fixture(); t.after(() => rm(f.root, {recursive: true, force: true}));
  const packet = buildReview(f.c, source, proposal, await readTodos(f.c));
  const edited = {...source, messages: [{...source.messages[0], text: 'Withdrawn', revision: 'edited'}]};
  await assert.rejects(applyReview(f.configPath, packet, digest(packet), readerFor(edited)), /source_changed/);
  assert.equal((await readTodos(f.c)).todos.length, 0);
  await applyReview(f.configPath, packet, digest(packet), readerFor(source));
  assert.throws(() => buildReview(f.c, edited, {...proposal, title: 'Changed draft'}, {registry_digest: 'unused', revision: 'new', todos: [{todo_id: packet.todo_id}]}), /already_tracked/);
});

test('revocation after a source page prevents the next page from being read', async () => {
  const f = await fixture();
  try {
    let granted = true, calls = 0;
    await assert.rejects(captureLark(f.c, source.start, source.end, async () => granted,
      async () => {calls++; granted = false; return {ok: true, identity: 'bot', data: {messages: [], has_more: true, page_token: 'next'}};}), /revoked/);
    assert.equal(calls, 1);
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test('real registry admission rejects agents, stop, removal and incarnation replacement before source access', async t => {
  const f = await fixture(); t.after(() => rm(f.root, {recursive: true, force: true}));
  const registry = JSON.parse(await readFile(f.c.registry_path, 'utf8'));
  const before = await readTodos(f.c), packet = buildReview(f.c, source, proposal, before);
  const cases = [
    {...registry, goals: []},
    {...registry, goals: [{...registry.goals[0], goal_instance_id: 'ginst_00000000000000000000000000000002'}]},
    {...registry, goals: [{...registry.goals[0], activation_state: 'stopped'}]},
    ...['registered_agents', 'coordination', 'spawn_policy'].map(field => ({...registry, goals: [{...registry.goals[0],
      [field]: field === 'registered_agents' ? ['agent-a', 'agent-b'] : {registered_agents: ['agent-a', 'agent-b']}}]})),
  ];
  let reads = 0;
  for (const changed of cases) {
    await writeFile(f.c.registry_path, JSON.stringify(changed));
    await assert.rejects(readTodos(f.c));
    await assert.rejects(applyReview(f.configPath, packet, digest(packet), async () => {reads++; return readerFor(source)();}));
    const store = new FileAuthorityStore(join(f.root, 'authority', 'file-v0'), f.c.goal_id);
    const head = await store.loadAuthority(); assert.equal(head.status, 'loaded');
    if (head.status === 'loaded') assert.equal(head.provider_revision, before.revision);
  }
  assert.equal(reads, 0);
  await writeFile(f.c.registry_path, JSON.stringify(registry));
  // An unrelated registration edit invalidates the old witness too.
  await writeFile(f.c.registry_path, JSON.stringify({...registry, description: 'changed'}));
  await assert.rejects(applyReview(f.configPath, packet, digest(packet), readerFor(source)), /registry_changed/);
  assert.equal((await readTodos(f.c)).revision, before.revision);
});

test('registry revocation during source pagination prevents a second page and any mutation', async t => {
  const f = await fixture(); t.after(() => rm(f.root, {recursive: true, force: true}));
  const registry = JSON.parse(await readFile(f.c.registry_path, 'utf8'));
  const before = await readTodos(f.c), packet = buildReview(f.c, source, proposal, before);
  let calls = 0;
  await assert.rejects(applyReview(f.configPath, packet, digest(packet), async () => {
    calls++;
    await writeFile(f.c.registry_path, JSON.stringify({...registry, goals: [{...registry.goals[0], activation_state: 'stopped'}]}));
    return {ok: true, identity: 'bot', data: {messages: [], has_more: true, page_token: 'next'}};
  }), /revoked/);
  assert.equal(calls, 1);
  const head = await new FileAuthorityStore(join(f.root, 'authority', 'file-v0'), f.c.goal_id).loadAuthority();
  assert.equal(head.status, 'loaded');
  if (head.status === 'loaded') assert.equal(head.provider_revision, before.revision);
});
