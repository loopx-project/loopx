import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { request } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AgentRegistry } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { CoordinationManager, coordinationServer } from '../src/coordination.js';
import { listenPrivate } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { sessionEnvironment } from '../src/adapters/types.js';

const here = dirname(fileURLToPath(import.meta.url));
const cli = join(here, '../bin/calli');
async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-handover-reader-'));
  const registry = new AgentRegistry(['pi', 'claude', 'codex', 'agy', 'traex', 'zcode', 'doubao'].map(adapter => ({
    id: adapter, adapter, label: adapter,
    command: [process.execPath, join(here, 'fixtures', `fake-${['claude', 'traex'].includes(adapter) ? 'acp' : adapter}.mjs`)],
    env: { CLAUDE_CONFIG_DIR: join(dir, 'claude'), ZCODE_DATA_BASE_DIR: join(dir, 'zcode') },
  })), null);
  const store = new Store(join(dir, 'data'));
  const manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir));
  const socket = join(dir, 'ipc', 'calli.sock');
  const coordination = new CoordinationManager(store, manager, socket);
  const server = coordinationServer(coordination);
  await listenPrivate(server, socket);
  return { dir, store, manager, coordination, socket, async close() {
    coordination.dispose(); await manager.shutdown();
    await new Promise<void>(r => server.close(() => r()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}
const runCli = (args: string[], env: Record<string, string>) => new Promise<{ code: number; value: any }>((resolve, reject) => {
  execFile(process.execPath, [cli, ...args], { env: sessionEnvironment({}, env) }, (err, stdout, stderr) => {
    if (!stdout) { reject(new Error(stderr || String(err))); return; }
    resolve({ code: err ? 1 : 0, value: JSON.parse(stdout) });
  });
});
const runCommand = (command: string, env: Record<string, string>) => new Promise<any>((resolve, reject) => {
  execFile('/bin/sh', ['-c', command], { env: sessionEnvironment({}, env) }, (err, stdout, stderr) => {
    if (err) { reject(new Error(stderr || String(err))); return; }
    resolve(JSON.parse(stdout));
  });
});
const post = (socket: string, path: string, key: string, input: unknown) => new Promise<number>((resolve, reject) => {
  const req = request({ socketPath: socket, path, method: 'POST', headers: { Authorization: `Bearer ${key}` } }, res => {
    res.resume(); res.on('end', () => resolve(res.statusCode!));
  });
  req.on('error', reject); req.end(JSON.stringify(input));
});
const waitIdle = async (manager: ThreadManager, id: string) => {
  for (let n = 0; n < 300; n++) {
    if (manager.getThread(id).status !== 'running') return;
    await new Promise(r => setTimeout(r, 20));
  }
  throw new Error('fixture did not finish');
};

test('handover CLI reads ancestors without collaboration, pages safely and rejects unrelated threads and writes', async (t) => {
  const h = await setup();
  try {
    const source = await h.manager.createThread({ agent: 'pi', cwd: h.dir, title: 'source task' });
    const middle = await h.manager.createHandoverThread(source.id, 'claude');
    const last = await h.manager.createHandoverThread(middle.id, 'codex');
    const sibling = await h.manager.createHandoverThread(source.id, 'agy');
    const unrelated = await h.manager.createThread({ agent: 'pi', cwd: h.dir });
    h.store.updateThread(source.id, { agentSessionId: 'source-session', archivedAt: new Date().toISOString() });
    for (let i = 0; i < 4; i++) {
      const turn = `turn-${i}`;
      h.store.appendEvent(source.id, turn, 'turn.started', { text: `internal question ${i}`, displayText: `question ${i}`, ...(i === 1 ? { handover: 'codex' } : {}) });
      h.store.appendEvent(source.id, turn, 'tool.start', { id: `tool-${i}`, tool: 'Read', title: 'Read source', kind: 'read', input: 'private-large-input'.repeat(10000) });
      h.store.appendEvent(source.id, turn, 'tool.end', { id: `tool-${i}`, status: 'completed', output: 'private-large-output'.repeat(10000) });
      h.store.appendEvent(source.id, turn, 'message.delta', { text: `answer ${i}` });
      h.store.appendEvent(source.id, turn, 'message.delta', { text: ' continued' });
      h.store.appendEvent(source.id, turn, 'message.replace', { previous: ' continued', text: ' revised' });
      h.store.appendEvent(source.id, turn, i === 3 ? 'turn.error' : 'turn.done', i === 3 ? { message: 'usage exhausted', code: 'plan_exhausted' } : {});
    }
    const before = h.store.listEvents(source.id, 0, -1);
    const env = h.coordination.environment(last.id)!;
    assert.equal(env.CALLI_TEAM_SOCKET, undefined);
    assert.equal(env.CALLI_TEAM_KEY, undefined);
    assert.equal(h.coordination.metadata(last.id), undefined);
    assert.equal(h.coordination.environment(unrelated.id), undefined);
    const overview = await runCli(['handover'], env);
    assert.equal(overview.code, 0);
    assert.deepEqual(overview.value.segments.map((s: any) => s.threadId), [source.id, middle.id, last.id]);
    assert.equal(overview.value.segments[0].sessionId, 'source-session');
    assert.equal(overview.value.segments[0].cwd, h.dir);
    assert.equal(overview.value.segments[0].title, 'source task');
    assert.equal(overview.value.segments[0].sessionPath, null);
    assert.equal(overview.value.missingSource, null);
    const all: any[] = [];
    let cursor: number | undefined;
    do {
      const page = await runCli(['history', source.id, '--turns', '2', ...(cursor ? ['--before', String(cursor)] : [])], env);
      assert.equal(page.code, 0);
      assert.equal(page.value.turns.length, 2);
      assert.doesNotMatch(JSON.stringify(page.value), /private-large|input|output/);
      all.unshift(...page.value.turns);
      assert.ok(!cursor || page.value.turns.every((turn: any) => turn.startSeq < cursor!));
      assert.equal(page.value.hasOlder, page.value.nextCursor !== null);
      cursor = page.value.nextCursor ?? undefined;
    } while (cursor);
    assert.deepEqual(all.map(turn => turn.turnId), ['turn-0', 'turn-1', 'turn-2', 'turn-3']);
    for (let i = 0; i < all.length; i++) {
      assert.equal(all[i].question, `question ${i}`);
      assert.equal(all[i].answer.text, `answer ${i} revised`);
      assert.equal(all[i].answer.truncated, false);
      assert.equal(all[i].answer.fullText, null);
      assert.deepEqual(all[i].tools, [{ id: `tool-${i}`, name: 'Read', kind: 'read', title: 'Read source', status: 'completed' }]);
      assert.ok(all[i].startedAt && all[i].endedAt);
    }
    assert.equal(all[0].status, 'ok');
    assert.equal(all[1].handover, 'codex');
    assert.deepEqual(all[3].error, { message: 'usage exhausted', code: 'plan_exhausted' });
    assert.equal(all[3].status, 'error');
    const example = await runCli(['history', source.id, '--turns', '1'], env);
    t.diagnostic(`CLI history example: ${JSON.stringify(example.value)}`);
    const empty = await runCli(['history', source.id, '--before', '1'], env);
    assert.deepEqual(empty.value.turns, []);
    assert.equal(empty.value.nextCursor, null);
    assert.equal(empty.value.hasOlder, false);
    for (const target of [unrelated.id, sibling.id, 'missing-thread']) {
      for (const flags of [[], ['--turn', 'turn-0']]) {
        const denied = await runCli(['history', target, ...flags], env);
        assert.equal(denied.code, 1); assert.match(denied.value.error, /outside handover chain/);
      }
    }
    for (const turns of ['0', '-1', '51', 'NaN', '1.5']) assert.equal((await runCli(['history', source.id, '--turns', turns], env)).code, 1);
    for (const flags of [['--turn', 'missing'], ['--turn', 'turn-0', '--turns', '1'], ['--offset', '0'], ['--turn', 'turn-0', '--offset', '-1'], ['--turn', 'turn-0', '--offset', '1']]) {
      assert.equal((await runCli(['history', source.id, ...flags], env)).code, 1);
    }
    assert.equal(await post(h.socket, '/handover', 'wrong', { op: 'handover' }), 401);
    for (const op of ['send', 'spawn', 'stop', 'team', 'agents']) {
      assert.equal(await post(h.socket, '/handover', env.CALLI_HANDOVER_KEY, { op }), 403);
      assert.equal(await post(h.socket, '/command', env.CALLI_HANDOVER_KEY, { op }), 401);
    }
    assert.deepEqual(h.store.listEvents(source.id, 0, -1), before, 'reads do not append events');
    const summary = await h.manager.handoverSummary(source.id, null, { carryFailed: true });
    assert.ok('summary' in summary);
    // The same clean brief the auto handover sends: where to look, a few lines of progress.
    assert.match(summary.summary, /# 接手/);
    assert.match(summary.summary, /calli history /);
    assert.match(summary.summary, /看不到原对话/);
    assert.match(summary.summary, /git status、git diff 对齐现状再继续/);
    assert.doesNotMatch(summary.summary, new RegExp(env.CALLI_HANDOVER_KEY));
    h.store.deleteThread(source.id);
    assert.equal((await runCli(['handover'], env)).value.missingSource.sourceId, source.id);
    assert.equal((await runCli(['history', source.id], env)).code, 1);
    h.store.deleteThread(last.id);
    assert.equal(await post(h.socket, '/handover', env.CALLI_HANDOVER_KEY, { op: 'handover' }), 403);
  } finally { await h.close(); }
});

test('hundreds of deltas stay in one complete round and turn cursors reach the beginning without duplicates', async () => {
  const h = await setup();
  try {
    const source = await h.manager.createThread({ agent: 'pi', cwd: h.dir });
    const next = await h.manager.createHandoverThread(source.id, 'pi');
    const env = h.coordination.environment(next.id)!;
    const fragments = Array.from({ length: 600 }, (_, i) => `${i}🙂`);
    for (let i = 0; i < 12; i++) {
      const turn = `round-${i}`;
      h.store.appendEvent(source.id, turn, 'turn.started', { text: `question ${i}` });
      h.store.appendEvent(source.id, turn, 'tool.start', { id: 'read', tool: 'Read', kind: 'read', title: 'Read source', input: 'not returned' });
      for (const text of i === 11 ? fragments : [`answer ${i}`]) h.store.appendEvent(source.id, turn, 'message.delta', { text });
      h.store.appendEvent(source.id, turn, 'tool.end', { id: 'read', status: 'completed', output: 'not returned' });
      h.store.appendEvent(source.id, turn, 'turn.done', { stopReason: i === 10 ? 'cancelled' : 'end_turn' });
    }
    const recent = await runCli(['history', source.id], env);
    assert.equal(recent.value.turns.length, 10);
    assert.equal(recent.value.turns[0].turnId, 'round-2');
    assert.equal(recent.value.turns[8].status, 'cancelled');
    const long = recent.value.turns.at(-1);
    assert.equal(long.turnId, 'round-11');
    assert.equal(long.question, 'question 11');
    assert.equal(long.answer.text, fragments.join(''));
    assert.equal(long.answer.length, Array.from(fragments.join('')).length);
    assert.equal(long.answer.truncated, false);
    assert.equal(long.tools.length, 1);
    assert.equal(long.tools[0].status, 'completed');
    assert.equal(recent.value.hasOlder, true);
    const earlier = await runCli(['history', source.id, '--before', String(recent.value.nextCursor)], env);
    assert.deepEqual(earlier.value.turns.map((turn: any) => turn.turnId), ['round-0', 'round-1']);
    assert.equal(earlier.value.hasOlder, false);
    assert.equal(earlier.value.nextCursor, null);
    assert.equal((await runCli(['history', source.id, '--turns', '50'], env)).value.turns.length, 12);
  } finally { await h.close(); }
});

test('truncated answers advertise their length and full text can be reconstructed by Unicode character offsets at a fixed snapshot', async () => {
  const h = await setup();
  try {
    const source = await h.manager.createThread({ agent: 'pi', cwd: h.dir });
    const next = await h.manager.createHandoverThread(source.id, 'pi');
    const env = h.coordination.environment(next.id)!;
    const turnId = "long ' $(printf wrong) `printf wrong` ; turn";
    const fragments = Array.from({ length: 400 }, (_, i) => `${'段落🙂'.repeat(30)}${i}\n`);
    h.store.appendEvent(source.id, turnId, 'turn.started', { text: 'Explain the implementation' });
    for (const text of fragments) h.store.appendEvent(source.id, turnId, 'message.delta', { text });
    h.store.appendEvent(source.id, turnId, 'message.replace', { previous: fragments.at(-1), text: '修订结尾🙂' });
    const expected = fragments.slice(0, -1).join('') + '修订结尾🙂';
    const page = await runCli(['history', source.id], env);
    const turn = page.value.turns[0];
    assert.equal(turn.status, 'unfinished');
    assert.equal(turn.answer.truncated, true);
    assert.equal(turn.answer.length, Array.from(expected).length);
    assert.equal(turn.answer.text, Array.from(expected).slice(0, 4000).join(''));
    assert.equal(turn.answer.fullText.turnId, turnId);
    assert.equal(turn.answer.fullText.offset, 0);
    assert.equal(turn.answer.fullText.snapshotSeq, page.value.snapshotSeq);
    assert.match(turn.answer.fullText.command, /--snapshot \d+ --offset 0$/);
    // A running answer may change between requests; the page's snapshot stays reconstructible.
    h.store.appendEvent(source.id, turnId, 'message.replace', { previous: '修订结尾🙂', text: 'a newer ending' });
    h.store.appendEvent(source.id, turnId, 'message.delta', { text: 'additional live text' });
    let offset = 0, reconstructed = '', pages = 0, command = turn.answer.fullText.command;
    do {
      const full = { value: await runCommand(command, env) };
      assert.equal(full.value.turnId, turnId, 'shell metacharacters in imported IDs stay literal');
      assert.equal(full.value.offset, offset);
      assert.equal(full.value.length, turn.answer.length);
      assert.equal(full.value.snapshotSeq, page.value.snapshotSeq);
      assert.ok(Array.from(full.value.text).length <= 16000);
      reconstructed += full.value.text;
      pages++;
      if (full.value.nextOffset === null) { assert.equal(full.value.command, null); break; }
      assert.match(full.value.command, /--snapshot \d+ --offset \d+$/);
      command = full.value.command;
      assert.ok(full.value.nextOffset > offset);
      offset = full.value.nextOffset;
    } while (pages < 10);
    assert.ok(pages > 1 && pages < 10);
    assert.equal(reconstructed, expected);
    const first = await runCli(['history', source.id, '--turn', turnId], env);
    assert.equal(first.code, 0);
    assert.ok(first.value.snapshotSeq > page.value.snapshotSeq);
    assert.equal(first.value.nextOffset, 16000);
    for (const flags of [
      ['--offset', String(turn.answer.length + 1), '--snapshot', String(page.value.snapshotSeq)],
      ['--snapshot', String(first.value.snapshotSeq + 1)], ['--snapshot', '1'],
    ]) assert.equal((await runCli(['history', source.id, '--turn', turnId, ...flags], env)).code, 1);
  } finally { await h.close(); }
});

test('round previews cap answers at 4000 characters and list only the latest 30 tools with exact omission counts', async () => {
  const h = await setup();
  try {
    const source = await h.manager.createThread({ agent: 'pi', cwd: h.dir });
    const next = await h.manager.createHandoverThread(source.id, 'pi');
    const env = h.coordination.environment(next.id)!;
    for (const [turnId, length, toolCount] of [['at-limit', 4000, 30], ['over-limit', 4001, 130]] as const) {
      h.store.appendEvent(source.id, turnId, 'turn.started', { text: 'Inspect the changes' });
      h.store.appendEvent(source.id, turnId, 'message.delta', { text: '🙂'.repeat(length) });
      for (let i = 0; i < toolCount; i++) {
        h.store.appendEvent(source.id, turnId, 'tool.start', { id: `tool-${i}`, tool: 'Read', title: `File ${i}`, kind: 'read', input: 'private-input' });
        h.store.appendEvent(source.id, turnId, 'tool.end', { id: `tool-${i}`, status: 'completed', output: 'private-output' });
      }
      h.store.appendEvent(source.id, turnId, 'turn.done', {});
    }
    const page = await runCli(['history', source.id], env);
    const [exact, over] = page.value.turns;
    assert.equal(Array.from(exact.answer.text).length, 4000);
    assert.equal(exact.answer.truncated, false);
    assert.equal(exact.answer.fullText, null);
    assert.equal(exact.toolCount, 30);
    assert.equal(exact.toolsOmitted, 0);
    assert.equal(exact.tools.length, 30);
    assert.equal(Array.from(over.answer.text).length, 4000);
    assert.equal(over.answer.length, 4001);
    assert.equal(over.answer.truncated, true);
    assert.equal(over.toolCount, 130);
    assert.equal(over.toolsOmitted, 100);
    assert.deepEqual(over.tools.map((tool: any) => tool.id), Array.from({ length: 30 }, (_, i) => `tool-${100 + i}`));
    assert.ok(over.tools.every((tool: any) => tool.status === 'completed'));
    assert.doesNotMatch(JSON.stringify(page.value), /private-input|private-output/);
    const full = await runCommand(over.answer.fullText.command, env);
    assert.equal(full.text, '🙂'.repeat(4001));
    assert.equal(full.nextOffset, null);
    assert.equal(full.command, null);
  } finally { await h.close(); }
});

test('all adapters inject handover credentials and run calli in their fake agent without enabling collaboration', async () => {
  const h = await setup();
  try {
    const source = await h.manager.createThread({ agent: 'pi', cwd: h.dir });
    for (const agent of ['pi', 'claude', 'codex', 'agy', 'traex', 'zcode', 'doubao']) {
      const next = await h.manager.createHandoverThread(source.id, agent);
      await h.manager.startTurn(next.id, { text: 'CALLI_HANDOVER_ENV' });
      await waitIdle(h.manager, next.id);
      const events = h.store.listEvents(next.id, 0, -1);
      assert.equal(events.some(e => e.type === 'turn.error'), false, `${agent}: ${JSON.stringify(events.filter(e => e.type === 'turn.error'))}`);
      const answer = events.filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
      assert.match(answer, /handover-ready:true;collaboration:false/, agent);
      assert.ok(answer.includes(source.id), agent);
      assert.ok(answer.includes(next.id), agent);
      assert.equal(h.coordination.metadata(next.id), undefined, agent);
    }
    const combined = await h.manager.createHandoverThread(source.id, 'pi');
    await h.coordination.enable(combined.id);
    const env = h.coordination.environment(combined.id)!;
    assert.ok(env.CALLI_TEAM_KEY && env.CALLI_HANDOVER_KEY);
    assert.notEqual(env.CALLI_TEAM_KEY, env.CALLI_HANDOVER_KEY);
    assert.equal(await post(h.socket, '/handover', env.CALLI_TEAM_KEY, { op: 'handover' }), 401);
    assert.equal(await post(h.socket, '/command', env.CALLI_HANDOVER_KEY, { op: 'status' }), 401);
    assert.equal((await runCli(['handover'], env)).code, 0);
    const normal = sessionEnvironment({ CALLI_HANDOVER_KEY: 'inherited-key', CALLI_HANDOVER_SOCKET: 'inherited-socket' });
    assert.equal(normal.CALLI_HANDOVER_KEY, undefined);
    assert.equal(normal.CALLI_HANDOVER_SOCKET, undefined);
  } finally { await h.close(); }
});
