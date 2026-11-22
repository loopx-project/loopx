import { test as nodeTest } from 'node:test';
import assert from 'node:assert/strict';
import { scriptedRuntime } from '@botiverse/oar/testing';
import type { Runtime, Event } from '@botiverse/oar/browser';
import { OAR_AGENTS, OarAdapter, oarEnvironment, projectOarEvent } from '../src/adapters/oar.js';
import { AgentRegistry, SUPPORTED_AGENTS } from '../src/agents.js';
import type { EmittedEvent } from '../src/events.js';
import type { TurnHooks } from '../src/adapters/types.js';
import { fileURLToPath } from 'node:url';

// The optional OAR path requires Node 24; native-adapter tests still run on Node 22.
const test = Number(process.versions.node.split('.')[0]) >= 24 ? nodeTest : nodeTest.skip;

const expected = ['cursor', 'codebuddy', 'devin', 'factory', 'grok', 'kimi', 'opencode', 'qoder', 'qwen'];
const hooks = (events: EmittedEvent[]): TurnHooks => ({ emit: event => events.push(event), requestApproval: async () => ({ cancelled: true }) });

test('all nine requested runtimes are registered without replacing existing native adapters', () => {
  assert.deepEqual(OAR_AGENTS.map(meta => meta.adapter), expected);
  for (const id of expected) assert.equal(SUPPORTED_AGENTS.filter(meta => meta.adapter === id).length, 1);
  const registry = new AgentRegistry(OAR_AGENTS.map(meta => ({ id: meta.adapter, adapter: meta.adapter })), null);
  assert.equal(registry.adapters.size, 9);
  for (const adapter of registry.adapters.values()) {
    assert.deepEqual(adapter.capabilities.permissionModes, ['full_access']);
    assert.equal(adapter.capabilities.history, false); // Hub replay does not imply native history import.
  }
  for (const id of ['claude', 'codex', 'pi', 'agy', 'traex', 'zcode', 'dsh']) assert.ok(SUPPORTED_AGENTS.some(meta => meta.adapter === id));
});

for (const meta of OAR_AGENTS) test(`${meta.label}: OAR session streams tools and preserves its native resume id`, async () => {
  const opened: unknown[] = [];
  const runtime = scriptedRuntime({ id: meta.adapter, async turn(turn) {
    turn.think('Inspecting');
    await turn.tool('read', '{"path":"README.md"}', () => 'file contents');
    turn.say('Answer: ' + turn.input);
  } });
  const wrapper: Runtime = { ...runtime, session: async (installation, options) => { opened.push(options); return runtime.session(installation, options); } };
  const adapter = new OarAdapter(meta.adapter, [], {}, async () => wrapper);
  const session = await adapter.start({ cwd: '/tmp', permissionMode: 'full_access' });
  const events: EmittedEvent[] = [];
  assert.equal((await session.prompt({ text: 'first', images: [] }, hooks(events))).stopReason, 'end_turn');
  assert.equal(events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), 'Answer: first');
  assert.deepEqual(events.filter(e => e.type.startsWith('tool.')).map(e => e.type), ['tool.start', 'tool.end']);
  assert.deepEqual(events.find(e => e.type === 'tool.start')?.data.input, { path: 'README.md' });
  assert.equal(events.find(e => e.type === 'reasoning.delta')?.data.text, 'Inspecting');
  const id = session.agentSessionId;
  await session.close();
  const resumed = await adapter.start({ cwd: '/tmp', resumeId: id, permissionMode: 'full_access' });
  assert.equal((opened[1] as { resume: string }).resume, id);
  await resumed.close();
});

test('restrictive permissions are refused before a permissive OAR session opens', async () => {
  let calls = 0;
  const adapter = new OarAdapter('grok', [], {}, async () => { calls++; return scriptedRuntime({ turn() {} }); });
  for (const permissionMode of [undefined, 'ask', 'auto_edit'] as const) {
    await assert.rejects(adapter.start({ cwd: '/tmp', permissionMode }), /full_access/);
  }
  assert.equal(calls, 0);
});

test('cancellation waits for the native OAR turn to end, then permits a new turn', async () => {
  let entered!: () => void;
  const began = new Promise<void>(resolve => { entered = resolve; });
  const adapter = new OarAdapter('grok', [], {}, async () => scriptedRuntime({ async turn(turn) {
    if (turn.input === 'wait') { entered(); await new Promise<void>(resolve => turn.signal.addEventListener('abort', () => resolve(), { once: true })); }
    else turn.say('next answer');
  } }));
  const session = await adapter.start({ cwd: '/tmp', permissionMode: 'full_access' });
  const first = session.prompt({ text: 'wait', images: [] }, hooks([]));
  await began;
  await session.cancel();
  assert.equal((await first).stopReason, 'cancelled');
  assert.equal((await session.prompt({ text: 'next', images: [] }, hooks([]))).stopReason, 'end_turn');
  await session.close();
});

test('runtime exit and failed turns never become successful replies', async () => {
  for (const exit of [true, false]) {
    const adapter = new OarAdapter('grok', [], {}, async () => scriptedRuntime({ turn(turn) {
      if (exit) turn.exit(1); else throw new Error('provider unavailable');
    } }));
    const session = await adapter.start({ cwd: '/tmp', permissionMode: 'full_access' });
    if (exit) await assert.rejects(session.prompt({ text: 'run', images: [] }, hooks([])));
    else assert.equal((await session.prompt({ text: 'run', images: [] }, hooks([]))).stopReason, 'error');
    await session.close();
  }
});

test('a process lost between turns releases the dead session instead of trapping every later prompt', async () => {
  let exit!: () => void;
  const adapter = new OarAdapter('grok', [], {}, async () => scriptedRuntime({ turn(turn) {
    exit = () => turn.exit(1); turn.say('first');
  } }));
  const session = await adapter.start({ cwd: '/tmp', permissionMode: 'full_access' });
  await session.prompt({ text: 'first', images: [] }, hooks([]));
  exit();
  await assert.rejects(session.prompt({ text: 'next', images: [] }, hooks([])), /exited/);
  await session.close();
});

test('child text cannot become the parent answer and same tool ids cannot collide', () => {
  const events: EmittedEvent[] = [];
  const base = { seq: 1, sessionId: 'root', agentPath: [] };
  for (const child of [{ sessionId: 'root', agentPath: ['child'] }, { sessionId: 'separate-child', agentPath: [] }]) {
    projectOarEvent({ ...base, ...child, kind: 'text_delta', text: 'child secret' } as Event, hooks(events), 'root');
    projectOarEvent({ ...base, ...child, kind: 'tool_call_started', callId: 'same', tool: 'read' } as Event, hooks(events), 'root');
  }
  projectOarEvent({ ...base, kind: 'text_delta', text: 'root answer' } as Event, hooks(events), 'root');
  assert.deepEqual(events.filter(e => e.type === 'message.delta').map(e => e.data.text), ['root answer']);
  assert.equal(new Set(events.filter(e => e.type === 'tool.start').map(e => e.data.id)).size, 2);
});

test('native models keep opaque ids, default and supported efforts; login refusal stays distinct', async () => {
  const runtime = scriptedRuntime({ turn() {} });
  const adapter = new OarAdapter('kimi', [], {}, async () => ({ ...runtime, listModels: async () => ({ kind: 'ok', models: [
    { id: 'provider/model', displayName: 'Model', default: true, effortLevels: ['high', 'unknown'] },
    { id: 'disabled', disabled: { reason: 'not available' } },
  ] }) }));
  assert.deepEqual(await adapter.models(), [{ id: 'provider/model', label: 'Model', efforts: ['high'], isDefault: true }]);
  const loggedOut = new OarAdapter('kimi', [], {}, async () => ({ ...runtime, listModels: async () => ({ kind: 'unauthenticated' }) }));
  await assert.rejects(loggedOut.models(), (error: any) => error.details?.code === 'agent_login_required' || error.message.includes('not signed in'));
});

test('OAR environment removes inherited hub authority and retains explicitly scoped tools', () => {
  const env = oarEnvironment({}, { LOOCHAT_TOOLS_KEY: 'scoped-test-key', LOOCHAT_TOOLS_SOCKET: '/tmp/test.sock' });
  assert.equal(env.LOOCHAT_TOKEN, null);
  assert.equal(env.LOOCHAT_TEAM_KEY, null);
  assert.equal(env.LOOCHAT_TOOLS_KEY, 'scoped-test-key');
});

test('tool argument and output fragments reach the existing timeline as complete values', () => {
  const events: EmittedEvent[] = [];
  const text = new Map<string, string>();
  const base = { seq: 1, sessionId: 'root', agentPath: [], callId: 'command' };
  for (const event of [
    { kind: 'tool_call_started', tool: 'shell' },
    { kind: 'tool_call_input_delta', delta: '{"command":' },
    { kind: 'tool_call_input_delta', delta: '"pwd"}' },
    { kind: 'tool_call_progress', outputDelta: 'one' },
    { kind: 'tool_call_progress', outputDelta: ' two' },
  ]) projectOarEvent({ ...base, ...event } as Event, hooks(events), 'root', text);
  assert.equal(events[1].data.input, undefined, 'partial JSON is not a complete invocation');
  assert.deepEqual(events[2].data.input, { command: 'pwd' });
  assert.equal(events.at(-1)?.data.output, 'one two');
});

test('resume failure is not silently replaced by a new conversation', async () => {
  const runtime = scriptedRuntime({ turn() {} });
  const adapter = new OarAdapter('grok', [], {}, async () => ({ ...runtime, session: async (_installation, options) => {
    assert.equal(options.resume, 'missing'); throw new Error('Session not found');
  } }));
  await assert.rejects(adapter.start({ cwd: '/tmp', resumeId: 'missing', permissionMode: 'full_access' }), /Session not found/);
});

test('packaged OAR Grok adapter drives a real ACP subprocess and observes native output', async () => {
  const command = fileURLToPath(new URL('./fixtures/fake-oar-grok.mjs', import.meta.url));
  const adapter = new OarAdapter('grok', [command]);
  assert.equal(await adapter.available(), true, adapter.unavailableReason);
  const session = await adapter.start({ cwd: '/tmp', permissionMode: 'full_access' });
  try {
    const events: EmittedEvent[] = [];
    const result = await session.prompt({ text: 'hello', images: [] }, hooks(events));
    assert.equal(result.stopReason, 'end_turn');
    assert.equal(session.agentSessionId, 'public-fixture-session');
    assert.equal(events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), 'Native ACP answer: hello');
    assert.ok(events.some(e => e.type === 'tool.end'));
    await assert.rejects(session.prompt({ text: 'crash', images: [] }, hooks([])));
  } finally { await session.close(); }
});

test('device authorization stays pending until the runtime confirms login; cancellation stays cancelled', async () => {
  let finish!: () => void;
  const runtime = scriptedRuntime({ turn() {} });
  const adapter = new OarAdapter('qwen', [], {}, async () => ({ ...runtime, login: async (_installation, interaction) => {
    interaction.onEvent({ kind: 'device_code', userCode: 'TEST-CODE', verificationUri: 'https://example.com/login' });
    await new Promise<void>(resolve => { finish = resolve; interaction.signal?.addEventListener('abort', () => resolve(), { once: true }); });
    return { kind: 'logged_in', account: { email: 'fixture@example.com' } };
  } }));
  let success = 0;
  const login = adapter.startLogin(async () => { success++; });
  for (let i = 0; i < 50 && login.state().status === 'starting'; i++) await new Promise(resolve => setTimeout(resolve, 1));
  assert.deepEqual(login.state(), { status: 'waiting', input: 'none', userCode: 'TEST-CODE', url: 'https://example.com/login' });
  assert.equal(success, 0);
  finish();
  for (let i = 0; i < 50 && login.state().status !== 'success'; i++) await new Promise(resolve => setTimeout(resolve, 1));
  assert.equal(success, 1);
  assert.equal(login.state().status, 'success');
  const cancelled = adapter.startLogin(async () => { success++; });
  cancelled.cancel();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(cancelled.state().status, 'cancelled');
  assert.equal(success, 1);
});
