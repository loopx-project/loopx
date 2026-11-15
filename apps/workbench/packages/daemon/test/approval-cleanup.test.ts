import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import type { AgentAdapter, AgentSession, ApprovalRequest, SessionHooks, TurnHooks, TurnResult } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const permission: ApprovalRequest = {
  title: 'Run test command', toolCall: { rawInput: { command: 'true' } },
  options: [{ optionId: 'allow', name: 'Allow', kind: 'allow_once' }],
};

async function fixture(t: TestContext) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-approval-cleanup-'));
  const store = new Store(dir);
  const result = deferred<TurnResult>();
  const started = deferred<TurnHooks>();
  let sessionHooks!: SessionHooks;
  const nativeIds = new Set<string>();
  const session: AgentSession = {
    agentSessionId: 'controlled-session',
    setSessionHooks: hooks => { sessionHooks = hooks; },
    prompt: async (_input, hooks) => { started.resolve(hooks); return result.promise; },
    cancel: async () => { result.resolve({ stopReason: 'cancelled' }); },
    close: async () => { result.resolve({ stopReason: 'cancelled' }); },
  };
  const adapter: AgentAdapter = {
    id: 'controlled', label: 'Controlled',
    capabilities: {
      resume: false, approvals: true, permissionModes: ['ask'], effortLevels: [], images: false,
      deleteSession: false, history: false, fork: false, forkAt: false, multiAccount: false, forkInto: false,
    },
    available: async () => true, models: async () => [], commands: async () => [],
    resumeCommand: id => `controlled resume ${id}`, start: async () => session,
  };
  const manager = new ThreadManager(store, new Map([[adapter.id, adapter]]), [dir], new AttachmentStore(dir));
  const thread = store.createThread({ id: randomUUID(), agent: adapter.id, cwd: dir, title: 'Approval cleanup', permissionMode: 'ask', model: null, effort: null });
  const beginNative = (id: string) => { nativeIds.add(id); return sessionHooks.turnStarted(id, { background: true }); };
  const finishNative = (id: string) => { nativeIds.delete(id); sessionHooks.turnFinished(id, { stopReason: 'end_turn' }); };
  t.after(async () => {
    result.resolve({ stopReason: 'cancelled' });
    for (const id of nativeIds) finishNative(id);
    await manager.shutdown();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });
  await manager.startTurn(thread.id, { text: 'work' });
  const hooks = await started.promise;
  const requests = () => store.listEventsOfType(thread.id, 'approval.request');
  const waitForPromptEnd = async () => {
    const deadline = Date.now() + 2000;
    const turnId = store.listEventsOfType(thread.id, 'turn.started')[0].turnId;
    while (!store.listEvents(thread.id).some(e => e.turnId === turnId && (e.type === 'turn.done' || e.type === 'turn.error'))) {
      if (Date.now() > deadline) throw new Error('prompt did not finish');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    await new Promise(resolve => setImmediate(resolve));
  };
  return { manager, store, thread, result, hooks, beginNative, finishNative, requests, waitForPromptEnd };
}

test('approval cleanup: prompted completion cancels only its own approval', { timeout: 5000 }, async t => {
  const f = await fixture(t);
  const prompted = f.hooks.requestApproval(permission);
  const promptedId = String(f.requests().at(-1)!.data.approvalId);
  const native = f.beginNative('background');
  const background = native.requestApproval(permission);
  const backgroundId = String(f.requests().at(-1)!.data.approvalId);
  f.result.resolve({ stopReason: 'end_turn' });
  await f.waitForPromptEnd();
  assert.deepEqual(await prompted, { cancelled: true });
  assert.equal(f.manager.resolveApproval(promptedId, { optionId: 'allow' }), false);
  assert.equal(f.manager.threadView(f.thread.id).waitingReason, 'approval');
  assert.equal(f.manager.resolveApproval(backgroundId, { optionId: 'allow' }), true, 'the live native turn retains its decision');
  assert.deepEqual(await background, { optionId: 'allow' });
  assert.deepEqual(await f.hooks.requestApproval(permission), { cancelled: true }, 'finished prompted hooks cannot borrow the native lock');
  assert.equal(f.requests().length, 2);
  f.finishNative('background');
});

test('approval cleanup: native completion cancels its own approval while a prompt owns the lock', { timeout: 5000 }, async t => {
  const f = await fixture(t);
  const prompted = f.hooks.requestApproval(permission);
  const promptedId = String(f.requests().at(-1)!.data.approvalId);
  const native = f.beginNative('background');
  const background = native.requestApproval(permission);
  const backgroundId = String(f.requests().at(-1)!.data.approvalId);
  f.finishNative('background');
  assert.deepEqual(await background, { cancelled: true });
  assert.equal(f.manager.resolveApproval(backgroundId, { optionId: 'allow' }), false);
  assert.equal(f.manager.resolveApproval(promptedId, { optionId: 'allow' }), true, 'the live prompted turn retains its decision');
  assert.deepEqual(await prompted, { optionId: 'allow' });
  assert.deepEqual(await native.requestApproval(permission), { cancelled: true });
  assert.equal(f.requests().length, 2);
});

test('approval cleanup: native completion preserves another native turn\'s approval', { timeout: 5000 }, async t => {
  const f = await fixture(t);
  f.result.resolve({ stopReason: 'end_turn' });
  await f.waitForPromptEnd();
  const first = f.beginNative('first');
  const firstDecision = first.requestApproval(permission);
  const second = f.beginNative('second');
  const secondDecision = second.requestApproval(permission);
  const secondId = String(f.requests().at(-1)!.data.approvalId);
  f.finishNative('first');
  assert.deepEqual(await firstDecision, { cancelled: true });
  assert.equal(f.manager.resolveApproval(secondId, { optionId: 'allow' }), true);
  assert.deepEqual(await secondDecision, { optionId: 'allow' });
  f.finishNative('second');
});

for (const failure of ['returned', 'thrown'] as const) {
  test(`approval cleanup: ${failure} prompt error cancels its outstanding approval`, { timeout: 5000 }, async t => {
    const f = await fixture(t);
    const pending = f.hooks.requestApproval(permission);
    const id = String(f.requests().at(-1)!.data.approvalId);
    if (failure === 'returned') f.result.resolve({ stopReason: 'error', error: 'fixture failure' });
    else f.result.reject(new Error('fixture failure'));
    await f.waitForPromptEnd();
    assert.deepEqual(await pending, { cancelled: true });
    assert.equal(f.manager.resolveApproval(id, { optionId: 'allow' }), false);
    assert.equal(f.manager.threadView(f.thread.id).waiting, false);
    assert.deepEqual(await f.hooks.requestApproval(permission), { cancelled: true });
    assert.equal(f.requests().length, 1);
  });
}

test('approval cleanup: synchronous request listeners can resolve an already registered approval', { timeout: 5000 }, async t => {
  const f = await fixture(t);
  let accepted = false;
  const unsubscribe = f.manager.subscribe(event => {
    if (event.type === 'approval.request') accepted = f.manager.resolveApproval(String(event.data.approvalId), { optionId: 'allow' });
  });
  try {
    const decision = f.hooks.requestApproval(permission);
    assert.equal(accepted, true);
    assert.deepEqual(await decision, { optionId: 'allow' });
  } finally { unsubscribe(); }
});

test('approval cleanup: terminal event listeners cannot reopen a finished prompted turn', { timeout: 5000 }, async t => {
  const f = await fixture(t);
  const late = deferred<ReturnType<TurnHooks['requestApproval']>>();
  const unsubscribe = f.manager.subscribe(event => {
    if (event.type === 'turn.done') late.resolve(f.hooks.requestApproval(permission));
  });
  try {
    f.result.resolve({ stopReason: 'end_turn' });
    assert.deepEqual(await late.promise, { cancelled: true });
    await f.waitForPromptEnd();
    assert.equal(f.requests().length, 0, 'finished hooks must not publish an immediately obsolete card');
  } finally { unsubscribe(); }
});

test('approval cleanup: native resolution listeners cannot recreate a decision during final cleanup', { timeout: 5000 }, async t => {
  const f = await fixture(t);
  f.result.resolve({ stopReason: 'end_turn' });
  await f.waitForPromptEnd();
  const native = f.beginNative('background');
  const pending = native.requestApproval(permission);
  const approvalId = String(f.requests().at(-1)!.data.approvalId);
  const late = deferred<ReturnType<TurnHooks['requestApproval']>>();
  const unsubscribe = f.manager.subscribe(event => {
    if (event.type === 'approval.resolved' && event.data.approvalId === approvalId) late.resolve(native.requestApproval(permission));
  });
  try {
    f.finishNative('background');
    assert.deepEqual(await pending, { cancelled: true });
    assert.deepEqual(await late.promise, { cancelled: true });
    assert.equal(f.requests().length, 1);
    assert.equal(f.manager.threadView(f.thread.id).waiting, false);
  } finally { unsubscribe(); }
});
