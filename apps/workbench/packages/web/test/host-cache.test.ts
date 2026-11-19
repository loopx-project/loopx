import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentInfo, ModelOption, PetStatus } from '../src/api.js';
import { cachedAgents, cachedDraft, cachedModels, cachedPet, clearHostCache, rememberAgents, rememberDraft, rememberModels, rememberPetBust, rememberPetStatus } from '../src/host-cache.js';

function storage(t: TestContext) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const data = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    removeItem: (key: string) => { data.delete(key); },
  } });
  t.after(() => {
    clearHostCache();
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  });
  return data;
}

const agent = (id: string): AgentInfo => ({ id, label: id.toUpperCase(), available: true, capabilities: { effortLevels: [], images: true, deleteSession: true, history: true, quota: false, resume: true, approvals: true, permissionModes: ['ask', 'auto_edit', 'full_access'] } }) as unknown as AgentInfo;
const model = (id: string, live: Partial<ModelOption> = {}): ModelOption => ({ id, label: id, ...live });

const pet = (over: Partial<PetStatus> = {}): PetStatus => ({ id: 'dsh', commit: 'abc123', installed: true, installing: false, error: null, clips: { idle: 24 }, frameMs: 80, ...over });

test('each machine keeps its own agents, draft and models, in memory whole', t => {
  storage(t);
  assert.equal(cachedAgents(null), null, 'a machine never read has no list, not an empty one');
  rememberAgents('devbox', [agent('pi'), agent('codex')]);
  rememberAgents(null, [agent('claude')]);
  assert.deepEqual(cachedAgents('devbox')?.map(a => a.id), ['pi', 'codex']);
  assert.deepEqual(cachedAgents(null)?.map(a => a.id), ['claude']);

  rememberDraft('devbox', { agent: 'pi', cwd: '/work/code', scratch: false });
  rememberDraft(null, { agent: 'claude', cwd: '', scratch: true });
  assert.deepEqual(cachedDraft('devbox'), { agent: 'pi', cwd: '/work/code', scratch: false });
  assert.deepEqual(cachedDraft(null), { agent: 'claude', cwd: '', scratch: true });

  rememberModels('devbox', { pi: [model('default'), model('sonnet')] });
  assert.deepEqual(Object.keys(cachedModels('devbox') ?? {}), ['pi']);
  assert.equal(cachedModels(null), null);
});

test('a draft without an agent is not remembered, and a models answer without models is not either', t => {
  storage(t);
  rememberDraft(null, { agent: '', cwd: '/x', scratch: false });
  assert.equal(cachedDraft(null), null);
  rememberModels(null, { pi: [] });
  assert.equal(cachedModels(null), null);
});

test('models keep their catalog but lose their live load and queue readings', t => {
  const data = storage(t);
  rememberModels(null, { pi: [model('sonnet', { loadPercent: 140, loadError: true, queueSize: 3, queuePosition: 2, queueObservedAt: 123, description: 'fast', efforts: ['low', 'high'] })] });
  const kept = cachedModels(null)?.pi[0];
  assert.deepEqual(kept, { id: 'sonnet', label: 'sonnet', description: 'fast', efforts: ['low', 'high'] } as ModelOption);
  // The same catalog with fresh readings does not count as a change worth writing.
  const before = data.get('calli.host.v1');
  rememberModels(null, { pi: [model('sonnet', { loadPercent: 5, queuePosition: 0, queueObservedAt: 999, description: 'fast', efforts: ['low', 'high'] })] });
  assert.equal(data.get('calli.host.v1'), before);
});

test('the assistant keeps her shape and portrait per machine, and an uninstall drops them', t => {
  storage(t);
  assert.equal(cachedPet(null), null);
  rememberPetStatus(null, pet({ installing: true, error: 'mid-install' }));
  const kept = cachedPet(null);
  assert.ok(kept, 'installed is kept even mid-install, without the passing parts');
  assert.deepEqual(kept!.status, { id: 'dsh', commit: 'abc123', installed: true, clips: { idle: 24 }, frameMs: 80 });
  assert.equal(kept!.bust, null, 'no portrait yet');

  rememberPetBust(null, { src: 'data:image/png;base64,AAA', aspect: 0.9 });
  assert.deepEqual(cachedPet(null)?.bust, { src: 'data:image/png;base64,AAA', aspect: 0.9 });
  rememberPetStatus('devbox', pet({ id: 'dsh2', commit: 'def' }));
  assert.equal(cachedPet('devbox')?.status.id, 'dsh2', 'another machine keeps its own');

  rememberPetStatus(null, pet({ installed: false }));
  assert.equal(cachedPet(null), null, 'uninstalled is not frozen as installed');
  assert.equal(cachedPet('devbox')?.status.id, 'dsh2', 'the other machine is untouched');
});

test('what does not parse is nothing, not a crash', t => {
  const data = storage(t);
  data.set('calli.host.v1', 'not json');
  data.set('calli.pet.v1', '{"devbox": {"status": {"id": 5}}}');
  assert.equal(cachedAgents(null), null);
  assert.equal(cachedDraft('devbox'), null);
  assert.equal(cachedModels(null), null);
  assert.equal(cachedPet('devbox'), null);
});

test('another hub, or signing out, clears every machine', t => {
  storage(t);
  rememberAgents(null, [agent('pi')]);
  rememberDraft(null, { agent: 'pi', cwd: '/x', scratch: false });
  rememberModels(null, { pi: [model('default')] });
  rememberPetStatus(null, pet());
  rememberPetBust(null, { src: 'data:,bust', aspect: 1 });
  clearHostCache();
  assert.equal(cachedAgents(null), null);
  assert.equal(cachedDraft(null), null);
  assert.equal(cachedModels(null), null);
  assert.equal(cachedPet(null), null);
});
