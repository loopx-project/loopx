import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { ZcodeAdapter } from '../src/adapters/zcode.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import type { EmittedEvent } from '../src/events.js';

const fixture = (name: string) => [process.execPath, fileURLToPath(new URL(`./fixtures/fake-${name}.mjs`, import.meta.url))];

test('real protocol fixtures emit normalized usage without lifetime or context totals', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-observe-adapters-'));
  const env = { FAKE_TELEMETRY: '1', ZCODE_DATA_BASE_DIR: dir };
  const adapters: AgentAdapter[] = [new PiRpcAdapter(fixture('pi'), env), new CodexAppServerAdapter(fixture('codex'), env), new ClaudeAcpAdapter(fixture('acp'), env), new ZcodeAdapter(fixture('zcode'), env)];
  try {
    for (const adapter of adapters) {
      const session = await adapter.start({ cwd: dir });
      try {
        for (let i = 0; i < 2; i++) {
          const events: EmittedEvent[] = [];
          const result = await session.prompt({ text: 'hello', images: [] }, { emit: e => events.push(e), requestApproval: async () => ({ cancelled: true }) });
          assert.equal(result.stopReason, 'end_turn', adapter.id);
          const samples = events.filter(e => e.type === 'usage.sample');
          assert.equal(samples.length, 1, adapter.id);
          assert.equal(samples[0].data.total, adapter.id === 'pi' || adapter.id === 'claude' ? 170 : 150, adapter.id);
          if (adapter.id === 'pi') { assert.ok(Number(samples[0].data.ttftMs) >= 250); assert.equal((samples[0].data.cost as any).amount, .0123); }
        }
      } finally { await session.close(); }
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('authenticated diagnostics explain unavailable, empty, login and version errors, refresh and deduplicate', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-diagnostics-'));
  const adapter = new CodexAppServerAdapter(fixture('codex'), {});
  const empty = new ZcodeAdapter(fixture('zcode'), { FAKE_ZCODE_NO_MODELS: '1', ZCODE_DATA_BASE_DIR: dir });
  const missing = new CodexAppServerAdapter(['calli-does-not-exist-test'], {});
  const login = new CodexAppServerAdapter(fixture('codex'), { FAKE_DIAGNOSTIC_ERROR: '401 authentication required secret-must-not-leak' });
  const old = new CodexAppServerAdapter(fixture('codex'), { FAKE_DIAGNOSTIC_ERROR: 'method not found' });
  let reads = 0;
  const dynamic = new CodexAppServerAdapter(fixture('codex'), {});
  dynamic.models = async () => { reads++; await new Promise(r => setTimeout(r, 20)); return [{ id: `model-${reads}`, label: `model-${reads}` }]; };
  const store = new Store(dir);
  const manager = new ThreadManager(store, new Map<string, AgentAdapter>([['codex', adapter], ['empty', empty], ['missing', missing], ['login', login], ['old', old], ['dynamic', dynamic]]), [dir], new AttachmentStore(dir));
  const token = 'test-observability-token';
  const server = createHttpServer({ manager, token, webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const get = (path: string) => fetch(base + path, { headers: { Authorization: `Bearer ${token}` } });
  try {
    assert.equal((await fetch(base + '/api/usage')).status, 401);
    assert.equal((await fetch(base + '/api/usage/costs')).status, 401);
    assert.equal((await fetch(base + '/api/usage/daily')).status, 401);
    assert.equal((await get('/api/usage?hours=2')).status, 400);
    assert.equal((await get('/api/usage/costs?hours=2')).status, 400);
    assert.equal((await get('/api/agents/nope/diagnostics')).status, 404);
    for (const [id, state] of [['codex', 'ready'], ['empty', 'empty'], ['missing', 'missing'], ['login', 'authentication'], ['old', 'unsupported']]) {
      const response = await get(`/api/agents/${id}/diagnostics`);
      assert.equal(response.status, 200);
      const diagnostic = await response.json() as any;
      assert.equal(diagnostic.state, state);
      assert.doesNotMatch(JSON.stringify(diagnostic), /secret-must-not-leak/);
      if (id === 'codex') assert.equal(diagnostic.version, '1.2.3');
    }
    await Promise.all([get('/api/agents/dynamic/diagnostics'), get('/api/agents/dynamic/diagnostics?refresh=1')]);
    assert.equal(reads, 1);
    await get('/api/agents/dynamic/diagnostics'); assert.equal(reads, 1);
    await get('/api/agents/dynamic/diagnostics?refresh=1'); assert.equal(reads, 2);
    const thread = await manager.createThread({ agent: 'codex', cwd: dir, title: 'ledger' });
    // API reads the same persisted store used by live turns.
    store.appendEvent(thread.id, 't', 'turn.started', { text: 'hello' });
    store.appendEvent(thread.id, 't', 'usage.sample', { id: 'a', scope: 'request', total: 170 });
    store.appendEvent(thread.id, 't', 'turn.done', {});
    const report = await (await get('/api/usage?agent=codex')).json() as any;
    assert.equal(report.totals.total, 170);
    assert.equal(report.tpm, 170);
    assert.equal(report.rows[0].title, 'ledger');
    assert.equal(report.costSummary.unpricedSamples, 1);
    assert.equal(report.costSummary.groups, undefined);
    const costs = await (await get('/api/usage/costs?agent=codex')).json() as any;
    assert.equal(costs.groups[0].reason, 'missing_model');
    assert.equal((await (await get('/api/usage/costs?agent=not-selected')).json() as any).groups.length, 0);
    const daily = await (await get('/api/usage/daily?agent=codex')).json() as any;
    assert.equal(daily.days.length, 1);
    assert.equal(daily.days[0].total, 170);
    assert.equal(daily.since, daily.days[0].date);
    assert.deepEqual((await (await get('/api/usage/daily?agent=not-selected')).json() as any).days, []);
    assert.equal((await get('/api/usage/daily?tz=Mars%2FOlympus')).status, 400);
    assert.equal((await (await get('/api/usage/daily?agent=codex&tz=UTC')).json() as any).days[0].total, 170);
  } finally { server.close(); await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});
