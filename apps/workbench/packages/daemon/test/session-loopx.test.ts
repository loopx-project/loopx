import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, realpathSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { LoopxService } from '../src/adapters/loopx/service.js';
import { LocalSessions } from '../src/local-sessions.js';
import { goalClues, lastGoal } from '../src/session-classify.js';
import { SessionEnricher } from '../src/session-enrich.js';

const command = (goal: string) => `loopx --format json --registry /r/registry.json heartbeat-prompt --thin --goal-id ${goal} --agent-id codex`;
const bootstrap = (goal: string) => `LoopX managed heartbeat bootstrap v2\n每次唤醒先执行：\n\`\`\`sh\n${command(goal)}\n\`\`\`\n读取完整结果`;

test('the heartbeat loader and its activation command name the Goal; talk about other loopx commands does not', () => {
  assert.deepEqual(goalClues(bootstrap('g-one')), [{ goalId: 'g-one', kind: 'bootstrap' }]);
  assert.deepEqual(goalClues(`ran ${command('g-two')}`), [{ goalId: 'g-two', kind: 'activation' }]);
  assert.deepEqual(goalClues('loopx status --goal-id g-three --limit 1'), []);
  assert.deepEqual(goalClues('see --goal-id g-four in the docs'), []);
});

test('a transcript line that escapes the text is read the same, and the last Goal activated wins', () => {
  const escaped = JSON.stringify({ text: bootstrap('g-old') + '\n' + bootstrap('g-new') });
  const found = lastGoal(goalClues(escaped));
  assert.equal(found?.goalId, 'g-new');
  assert.deepEqual(found?.all, ['g-old', 'g-new']);
  assert.equal(lastGoal(goalClues('nothing here')), null);
});

test('Codex tells its app from its terminal client by the rollout originator', () => {
  const adapter = new CodexAppServerAdapter(['codex']);
  const line = (originator: string, source = 'vscode') => JSON.stringify({ type: 'session_meta', payload: { originator, source } });
  assert.equal(adapter.sessionOrigin(line('Codex Desktop')), 'app');
  assert.equal(adapter.sessionOrigin(line('codex_cli_rs', 'cli')), 'cli');
  assert.equal(adapter.sessionOrigin(line('calli')), null);
  assert.equal(adapter.sessionOrigin('not json'), null);
});

// --- scan, recognise, import and bind, against a core and agent shaped like the real ones ---

const root = realpathSync(mkdtempSync(join(tmpdir(), 'loopx-sessions-')));
const goalRoot = join(root, 'goal-work');
mkdirSync(goalRoot);
let server: Server;
let core: URL;
const coreBindings: Array<Record<string, string>> = [];

before(async () => {
  server = createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(req.url === '/api/attached-sessions' ? { ok: true, bindings: coreBindings } : { ok: true }));
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  core = new URL(`http://127.0.0.1:${(server.address() as AddressInfo).port}`);
});
after(() => server.close());

interface Fixture { id: string; text: string; ageMs?: number; originator?: string }

/** Four kinds of conversation, as session files the fake agent reads back. */
function world(fixtures: Fixture[]) {
  const dir = mkdtempSync(join(tmpdir(), 'loopx-sessions-files-'));
  const files = new Map<string, string>();
  for (const f of fixtures) {
    const path = join(dir, `${f.id}.jsonl`);
    writeFileSync(path, `${JSON.stringify({ type: 'session_meta', payload: { originator: f.originator ?? 'Codex Desktop' } })}\n${JSON.stringify({ text: f.text })}\n`);
    files.set(f.id, path);
  }
  const imports: string[] = [];
  const adapter = {
    id: 'codex',
    capabilities: { history: true },
    listSessions: async () => fixtures.map((f) => ({ id: f.id, cwd: goalRoot, title: `title ${f.id}`, updatedAt: new Date(Date.now() - (f.ageMs ?? 3_600_000)).toISOString() })),
    sessionLocation: async (id: string | null) => ({ configDir: dir, sessionPath: files.get(id ?? '') ?? null }),
    sessionOrigin: (line: string) => new CodexAppServerAdapter(['codex']).sessionOrigin(line),
  };
  const adapters = new Map([['codex', adapter]]);
  const registry = join(mkdtempSync(join(tmpdir(), 'loopx-sessions-reg-')), 'registry.json');
  writeFileSync(registry, JSON.stringify({ goals: [{ id: 'g-core', repo: goalRoot }, { id: 'g-text', repo: goalRoot }] }));
  const fake = fileURLToPath(new URL('./fixtures/fake-loopx.mjs', import.meta.url));
  const service = new LoopxService({
    dataDir: mkdtempSync(join(tmpdir(), 'loopx-sessions-data-')), core, adapters: adapters as never,
    dirs: { all: () => [root] } as never, cli: { command: [process.execPath, fake], registry },
  });
  const threads = new Map<string, { id: string; agent: string; cwd: string }>();
  service.attach({
    threadOfSession: (agent: string, id: string) => threads.get(`${agent}/${id}`) ?? null,
    importSession: async ({ agent, sessionId }: { agent: string; sessionId: string }) => {
      imports.push(sessionId);
      const thread = { id: `thread-${sessionId}`, agent, cwd: goalRoot };
      threads.set(`${agent}/${sessionId}`, thread);
      return thread;
    },
    announceLoopx: () => {},
    autoHandover: { setOverride: () => {} },
  } as never);
  const local: LocalSessions = new LocalSessions({
    adapters: adapters as never, workdirs: () => [root], importedSessions: () => new Map([...threads.values()].map((t) => [t.id.replace('thread-', ''), t.agent])),
    instances: () => [{ id: 'codex', hidden: false, store: 'codex:' }],
    enricher: new SessionEnricher(service.lookup()),
    onScanned: () => service.syncSoon(() => local.all()),
  });
  return { local, service, imports, threads, dir, files };
}

const four: Fixture[] = [
  { id: 's-core', text: 'plain words only' },
  { id: 's-text', text: bootstrap('g-text') },
  { id: 's-gone', text: bootstrap('missing') },
  { id: 's-plain', text: 'fix the build', originator: 'codex_cli_rs' },
];

test('a scan recognises core-bound, heartbeat-text, deleted-Goal and ordinary sessions', async () => {
  coreBindings.length = 0;
  coreBindings.push({ host_surface: 'codex-app', session_id: 's-core', goal_id: 'g-core', goal_title: 'From the core', agent_id: 'codex' });
  const { local } = world(four);
  await local.scan();
  const by = (id: string) => local.find(id)!;
  assert.deepEqual([by('s-core').loopx?.basis, by('s-core').loopx?.source, by('s-core').loopx?.goalId], ['core', 'codex-app', 'g-core']);
  assert.deepEqual([by('s-text').loopx?.basis, by('s-text').loopx?.source], ['content', 'bootstrap']);
  assert.equal(by('s-gone').loopx?.missing, true);
  assert.equal(by('s-gone').loopx?.goalId, 'missing');
  assert.equal(by('s-plain').loopx, undefined);
  assert.equal(by('s-core').origin, 'app');
  assert.equal(by('s-plain').origin, 'cli');
});

test('Goals the core has are imported and bound under the Goal\'s root; a deleted Goal is listed but bound to nothing', async () => {
  const { local, service, imports, threads } = world(four);
  await local.scan();
  assert.deepEqual(imports.sort(), ['s-core', 's-text'].sort());
  for (const id of ['s-core', 's-text']) {
    const binding = service.bindings.get(threads.get(`codex/${id}`)!.id)!;
    assert.equal(binding.workspace, goalRoot);
    assert.equal(binding.permission, 'read_only', 'nothing earlier granted writing, so the session joins read-only');
    assert.equal(binding.basis, id === 's-core' ? 'core' : 'content');
  }
  assert.equal(threads.has('codex/s-gone'), false);
  assert.equal(threads.has('codex/s-plain'), false);
  // The deleted-Goal and ordinary sessions are still rows in the list; the bound ones left it.
  assert.deepEqual(local.page(100, null).sessions.map((s) => s.sessionId).sort(), ['s-gone', 's-plain']);
});

test('scanning again imports and binds nothing twice', async () => {
  const { local, imports, service } = world(four);
  await local.scan();
  const before = imports.length;
  const bindings = service.bindings.entries().length;
  await local.scan();
  await local.scan();
  assert.equal(imports.length, before);
  assert.equal(service.bindings.entries().length, bindings);
});

test('a session its host is still writing to waits until it has been quiet', async () => {
  const { local, imports } = world([{ id: 's-busy', text: bootstrap('g-text'), ageMs: 5_000 }]);
  await local.scan();
  assert.deepEqual(imports, []);
});

test('with the list limited to LoopX sessions, ordinary ones are not offered', async () => {
  const { local } = world(four);
  const limited = new LocalSessions({
    adapters: new Map([['codex', (local as any).deps.adapters.get('codex')]]) as never, workdirs: () => [root], importedSessions: () => new Map(),
    instances: () => [{ id: 'codex', hidden: false, store: 'codex:' }], enricher: (local as any).deps.enricher, listAll: () => false,
  });
  await limited.scan();
  assert.ok(limited.page(100, null).sessions.every((s) => s.loopx));
});

test('a binding follows the core: a Goal that disappears is marked, not unbound', async () => {
  const { local, service, threads } = world([{ id: 's-text', text: bootstrap('g-text') }]);
  await local.scan();
  const id = threads.get('codex/s-text')!.id;
  assert.equal(service.info(id)?.missing, undefined);
  // The Goal is gone from the core: the same thread, relabelled.
  service.bindings.update(id, { goalId: 'missing' });
  await local.scan();
  assert.equal(service.info(id)?.missing, true);
  assert.ok(service.bindings.get(id));
});
