import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, realpathSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { workspaceRef } from '../src/adapters/loopx/core.js';
import { LoopxService } from '../src/adapters/loopx/service.js';

// A core shaped like the real one: projects carry an opaque reference and a title, never a path,
// and goal.create only knows its own two permission words.
const root = realpathSync(mkdtempSync(join(tmpdir(), 'loopx-launch-')));
const granted = join(root, 'a'), other = join(root, 'b');
mkdirSync(granted); mkdirSync(other);
const proposals = new Map<string, any>();
let applies = 0;
let server: Server;
let core: URL;

before(async () => {
  server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null;
    const send = (status: number, value: unknown) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
    const url = req.url ?? '';
    if (url === '/api/chat/projects') return send(200, { ok: true, projects: [{ project_ref: 'p', title: 'a', grant: 'workspace_read', goal_workspace_ref: workspaceRef(granted) }] });
    if (url === '/api/actions/preview') {
      const p = body.normalized_parameters;
      if (!['read_only', 'workspace_write_on_confirmation'].includes(p.permission)) return send(400, { error: 'permission must select the existing Goal creation read or workspace-write review' });
      const id = `prop-${proposals.size + 1}`;
      proposals.set(id, { proposal_id: id, action_kind: 'goal.create', status: 'pending', normalized_parameters: p });
      return send(200, { proposal: proposals.get(id) });
    }
    const m = /^\/api\/actions\/([^/]+)(\/apply)?$/.exec(url);
    if (m && proposals.has(m[1]!)) {
      const p = proposals.get(m[1]!);
      if (m[2]) { applies++; Object.assign(p, { status: 'applied', receipt: { outcome: 'goal_created', resource_ids: { goal_id: p.normalized_parameters.goal_id } } }); }
      return send(200, { proposal: p });
    }
    send(404, { error: 'unknown path' });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  core = new URL(`http://127.0.0.1:${(server.address() as AddressInfo).port}`);
});
after(() => server.close());

type Manager = { threads: { id: string; cwd: string }[]; turns: string[]; events: Map<string, { type: string; turnId: string }[]>; fail: 'none' | 'before' | 'after' };

function fakeManager(fail: Manager['fail'] = 'none'): Manager & { api: unknown } {
  const m: Manager = { threads: [], turns: [], events: new Map(), fail };
  const api = {
    // Slow enough that two confirmations of one proposal overlap here.
    createThread: async (o: { cwd: string }) => { await new Promise((r) => setTimeout(r, 20)); const t = { id: `t${m.threads.length + 1}`, cwd: o.cwd }; m.threads.push(t); return t; },
    startTurn: async (id: string) => {
      if (m.fail === 'before') { m.fail = 'none'; throw new Error('agent offline'); }
      m.turns.push(id);
      const turnId = `turn-${m.turns.length}`;
      m.events.set(id, [...(m.events.get(id) ?? []), { type: 'turn.started', turnId }]);
      // The turn went out, then the daemon stopped before the launch could record it.
      if (m.fail === 'after') { m.fail = 'none'; throw new Error('daemon stopped'); }
      return { turnId };
    },
    listEvents: (id: string) => m.events.get(id) ?? [],
    announceLoopx: () => {},
    autoHandover: { setOverride: () => {} },
  };
  return Object.assign(m, { api });
}

function harness(fail: Manager['fail'] = 'none', opts: { dataDir?: string; manager?: ReturnType<typeof fakeManager> } = {}) {
  const registry = join(mkdtempSync(join(tmpdir(), 'loopx-reg-')), 'registry.json');
  writeFileSync(registry, JSON.stringify({ goals: [{ id: 'g-a', repo: granted }] }));
  const fake = fileURLToPath(new URL('./fixtures/fake-loopx.mjs', import.meta.url));
  const dataDir = opts.dataDir ?? mkdtempSync(join(tmpdir(), 'loopx-data-'));
  const service = new LoopxService({
    dataDir, core,
    adapters: new Map([['codex', { id: 'codex' }], ['claude', { id: 'claude' }]]) as never,
    dirs: { all: () => [root] } as never,
    cli: { command: [process.execPath, fake], registry },
  });
  const manager = opts.manager ?? fakeManager(fail);
  service.attach(manager.api as never);
  return { service, threads: manager.threads, turns: manager.turns, manager, dataDir };
}

const launch = { agent: 'codex', objective: 'Ship it' };
const rejects = (p: Promise<unknown>, status: number, code?: string) =>
  assert.rejects(p, (err: any) => err.status === status && (code === undefined || err.details?.code === code));

test('a directory the core granted is found by its reference alone, and the core gets its own permission words', async () => {
  const { service } = harness();
  const preview = await service.preview({ ...launch, cwd: granted, permission: 'workspace_auto' });
  assert.equal(preview.workspace, granted);
  assert.equal(proposals.get(preview.proposalId).normalized_parameters.permission, 'workspace_write_on_confirmation');
  await rejects(service.preview({ ...launch, cwd: other, permission: 'read_only' }), 409, 'workspace_not_granted');
});

test('a confirmation cannot raise the permission it was previewed with', async () => {
  const { service, threads } = harness();
  const preview = await service.preview({ ...launch, cwd: granted, permission: 'read_only' });
  const before = applies;
  await rejects(service.apply({ proposalId: preview.proposalId, agent: 'codex', permission: 'workspace_auto' }), 409, 'permission_mismatch');
  assert.equal(applies, before, 'nothing applied');
  assert.equal(threads.length, 0);
  const done = await service.apply({ proposalId: preview.proposalId, agent: 'codex' });
  assert.equal(service.binding(done.threadId)!.permission, 'read_only');
});

test('a launch whose first message failed sends it on retry instead of reporting success', async () => {
  const { service, threads, turns } = harness('before');
  const preview = await service.preview({ ...launch, cwd: granted, permission: 'workspace_auto' });
  const before = applies;
  await assert.rejects(service.apply({ proposalId: preview.proposalId, agent: 'codex', permission: 'workspace_auto' }));
  const retried = await service.apply({ proposalId: preview.proposalId, agent: 'codex', permission: 'workspace_auto' });
  assert.equal(applies, before + 1, 'the Goal is created once');
  assert.equal(threads.length, 1);
  assert.deepEqual(turns, [retried.threadId]);
  assert.equal(retried.turnId, 'turn-1');
  const again = await service.apply({ proposalId: preview.proposalId });
  assert.equal(again.reused, true);
  assert.equal(turns.length, 1, 'not sent twice');
});

test('joining a Goal works in its registered root, read-only unless the Goal was created writable here', async () => {
  const { service, threads } = harness();
  await rejects(service.join({ goalId: 'g-a', agent: 'codex', cwd: other, permission: 'read_only' }), 409, 'workspace_mismatch');
  await rejects(service.join({ goalId: 'g-a', agent: 'codex', permission: 'workspace_auto' }), 409, 'permission_mismatch');
  const joined = await service.join({ goalId: 'g-a', agent: 'codex' });
  assert.equal(joined.workspace, granted);
  assert.equal(joined.permission, 'read_only');
  assert.equal(threads.at(-1)!.cwd, granted);
  await rejects(service.join({ goalId: 'g-unknown', agent: 'codex' }), 409, 'goal_root_unknown');
});

test('two confirmations of one proposal at once open one thread and send one first message', async () => {
  const { service, threads, turns } = harness();
  const preview = await service.preview({ ...launch, cwd: granted, permission: 'read_only' });
  const before = applies;
  const [a, b] = await Promise.all([
    service.apply({ proposalId: preview.proposalId, agent: 'codex', permission: 'read_only' }),
    service.apply({ proposalId: preview.proposalId, agent: 'codex', permission: 'read_only' }),
  ]);
  assert.equal(a.threadId, b.threadId);
  assert.equal(threads.length, 1);
  assert.equal(turns.length, 1);
  assert.equal(applies, before + 1);
});

test('after a restart, a first message that already went out is not sent again', async () => {
  const first = harness('after');
  const preview = await first.service.preview({ ...launch, cwd: granted, permission: 'read_only' });
  await assert.rejects(first.service.apply({ proposalId: preview.proposalId, agent: 'codex', permission: 'read_only' }));
  const again = harness('none', { dataDir: first.dataDir, manager: first.manager });
  const done = await again.service.apply({ proposalId: preview.proposalId, agent: 'codex', permission: 'read_only' });
  assert.equal(done.turnId, 'turn-1');
  assert.equal(first.turns.length, 1);
  assert.equal(again.service.binding(done.threadId)!.firstMessage, undefined);
});
