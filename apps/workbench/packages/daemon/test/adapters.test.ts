import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { effortsFromConfig, mapAcpUpdate, modelsFromConfig } from '../src/adapters/acp.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { DshAcpAdapter } from '../src/adapters/dsh.js';
import { TraexAcpAdapter } from '../src/adapters/traex.js';
import { mapPiEvent, mapPiStats, piEfforts, PiRpcAdapter } from '../src/adapters/pi.js';
import type { EmittedEvent } from '../src/events.js';
import type { ApprovalDecision, ApprovalRequest, TurnHooks, TurnInput } from '../src/adapters/types.js';

const here = dirname(fileURLToPath(import.meta.url));
const fakeAcp = join(here, 'fixtures', 'fake-acp.mjs');
const fakePi = join(here, 'fixtures', 'fake-pi.mjs');

/** A 1x1 PNG. Real image bytes, since the daemon decides the type from the data. */
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

const turn = (text: string, images: TurnInput['images'] = []): TurnInput => ({ text, images });

function collectingHooks(decide: (id: string) => ApprovalDecision = () => ({ optionId: 'allow' })) {
  const events: EmittedEvent[] = [];
  const approvals: string[] = [];
  const hooks: TurnHooks = {
    emit: (e) => events.push(e),
    requestApproval: async (req) => {
      approvals.push(req.title);
      return decide(req.title);
    },
  };
  return { hooks, events, approvals };
}

test('mapAcpUpdate maps message, thought, tool and plan updates', () => {
  assert.deepEqual(mapAcpUpdate({ sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'hi' } }), {
    type: 'message.delta',
    data: { text: 'hi' },
  });
  assert.equal(mapAcpUpdate({ sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: 'x' } })?.type, 'reasoning.delta');
  assert.equal(mapAcpUpdate({ sessionUpdate: 'tool_call', toolCallId: 'a', title: 'ls', status: 'pending' })?.type, 'tool.start');
  // The raw tool name rides along when the bridge sends it, so the activity page can tell MCP calls.
  assert.equal(mapAcpUpdate({ sessionUpdate: 'tool_call', toolCallId: 'a', title: 'ls', status: 'pending', name: 'mcp__github__create_issue' })?.data.tool, 'mcp__github__create_issue');
  assert.equal(mapAcpUpdate({ sessionUpdate: 'tool_call', toolCallId: 'a', title: 'ls', status: 'pending', name: 'Read' })?.data.tool, 'Read');
  assert.equal('tool' in (mapAcpUpdate({ sessionUpdate: 'tool_call', toolCallId: 'a', title: 'ls', status: 'pending' })?.data ?? {}), false, 'no name, no guess');
  // Codex says the server and the tool apart, which is what an MCP count is made of.
  const mcp = mapCodexItemStart({ type: 'mcpToolCall', id: 'm1', server: 'github', tool: 'create_issue', arguments: {} });
  assert.equal(mcp?.data.tool, 'create_issue');
  assert.equal(mcp?.data.server, 'github');
  const shell = mapCodexItemStart({ type: 'commandExecution', id: 'c1', command: 'ls', cwd: '/tmp' });
  assert.equal('tool' in (shell?.data ?? {}), false);
  assert.equal(mapAcpUpdate({ sessionUpdate: 'tool_call_update', toolCallId: 'a', status: 'in_progress' })?.type, 'tool.update');
  assert.equal(mapAcpUpdate({ sessionUpdate: 'tool_call_update', toolCallId: 'a', status: 'failed' })?.type, 'tool.end');
  assert.equal(mapAcpUpdate({ sessionUpdate: 'plan', entries: [] })?.type, 'plan.updated');
  assert.equal(mapAcpUpdate({ sessionUpdate: 'usage_update' }), null);
  assert.equal(mapAcpUpdate({ sessionUpdate: 'user_message_chunk', content: { type: 'text', text: 'echo' } }), null);
});

test('mapPiEvent maps text, thinking and tool execution records', () => {
  assert.equal(mapPiEvent({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'a' } })?.type, 'message.delta');
  assert.equal(mapPiEvent({ type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', delta: 'b' } })?.type, 'reasoning.delta');
  assert.equal(mapPiEvent({ type: 'message_update', assistantMessageEvent: { type: 'toolcall_delta' } }), null);
  const start = mapPiEvent({ type: 'tool_execution_start', toolCallId: 'b', toolName: 'mcp__botmux__run', args: {} });
  assert.equal(start?.data.tool, 'mcp__botmux__run');
  const end = mapPiEvent({ type: 'tool_execution_end', toolCallId: 'c', result: { content: [{ type: 'text', text: 'out' }] }, isError: true });
  assert.deepEqual(end, { type: 'tool.end', data: { id: 'c', status: 'failed', output: 'out' } });
  assert.equal(mapPiEvent({ type: 'queue_update' }), null);
});

test('context mappings distinguish current occupancy, cumulative totals and unknown usage', () => {
  assert.deepEqual(mapPiStats({ tokens: { total: 1700000 }, contextUsage: { tokens: 1200, contextWindow: 200000 }, cost: 0.02 }), {
    type: 'usage.updated', data: { used: 1200, size: 200000, cost: { amount: 0.02, currency: 'USD' } },
  });
  assert.equal(mapPiStats({ tokens: { total: 1700000 } }), null);
  assert.deepEqual(mapPiStats({ contextUsage: { tokens: null, contextWindow: 200000 } }), {
    type: 'usage.updated', data: { used: null, size: 200000 },
  });
  assert.deepEqual(mapAcpUpdate({ sessionUpdate: 'usage_update', used: 52000, size: 200000, cost: { amount: 0.42, currency: 'USD' } }), {
    type: 'usage.updated', data: { used: 52000, size: 200000, cost: { amount: 0.42, currency: 'USD' } },
  });
  for (const used of [-1, NaN, Infinity]) {
    assert.equal(mapPiStats({ contextUsage: { tokens: used, contextWindow: 200000 } }), null);
    assert.equal(mapAcpUpdate({ sessionUpdate: 'usage_update', used, size: 200000 }), null);
  }
  for (const size of [0, -1, NaN, Infinity]) {
    assert.equal(mapPiStats({ contextUsage: { tokens: 1200, contextWindow: size } }), null);
    assert.equal(mapAcpUpdate({ sessionUpdate: 'usage_update', used: 52000, size }), null);
  }
});

test('ClaudeAcpAdapter: prompt streams events and returns end_turn', async () => {
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  assert.equal(await adapter.available(), true);
  const session = await adapter.start({ cwd: here });
  assert.equal(session.agentSessionId, 'acp-session-1');

  const { hooks, events } = collectingHooks();
  const result = await session.prompt(turn('hello'), hooks);
  assert.deepEqual(result, { stopReason: 'end_turn' });
  const types = events.map((e) => e.type);
  assert.deepEqual(types, ['reasoning.delta', 'tool.start', 'tool.end', 'message.delta']);
  assert.equal(events.at(-1)?.data.text, 'echo:hello');
  await session.close();
});

test('Claude and TRAE: context updates use each live protocol without inventing cost', async (t) => {
  for (const adapter of [new ClaudeAcpAdapter([process.execPath, fakeAcp]), new TraexAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'traex' })]) {
    const session = await adapter.start({ cwd: here });
    t.after(() => session.close());
    const { hooks, events } = collectingHooks();
    await session.prompt(turn('usage'), hooks);
    assert.deepEqual(events.find((e) => e.type === 'usage.updated')?.data, adapter.id === 'claude'
      ? { used: 52000, size: 200000, cost: { amount: 0.42, currency: 'USD' } }
      : { used: 183510, size: 258400 });
  }
});

test('ClaudeAcpAdapter: permission request is routed to the hook and answered', async () => {
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  const session = await adapter.start({ cwd: here });
  const { hooks, events, approvals } = collectingHooks(() => ({ optionId: 'allow' }));
  const result = await session.prompt(turn('please approve this'), hooks);
  assert.equal(result.stopReason, 'end_turn');
  assert.deepEqual(approvals, ['Edit file']);
  const last = events.at(-1);
  assert.equal(last?.type, 'message.delta');
  assert.equal(last?.data.text, 'approved:allow');
  await session.close();
});

test('ClaudeAcpAdapter: cancel resolves a slow prompt as cancelled', async () => {
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  const session = await adapter.start({ cwd: here });
  const { hooks } = collectingHooks();
  const pending = session.prompt(turn('slow task'), hooks);
  await new Promise((r) => setTimeout(r, 50));
  await session.cancel();
  const result = await pending;
  assert.equal(result.stopReason, 'cancelled');
  await session.close();
});

test('DshAcpAdapter: model list flattens provider groups and probes each model\'s efforts', async () => {
  const adapter = new DshAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'dsh' });
  const models = await adapter.models();
  assert.deepEqual(models.map((m) => m.label), ['DeepSeek V4 Pro', 'DeepSeek V4.1 Flash', 'Pi 5']);
  // The provider group rides along as a note; the default model is the route the agent reported.
  assert.equal(models[0].description, 'DeepSeek · 最强推理');
  assert.equal(models.find((m) => m.isDefault)?.id, '["deepseek-official","deepseek-v4-pro"]');
  // Probing asks each model for its own efforts; the empty provider-default value is not a level.
  assert.deepEqual(models[0].efforts, ['off', 'low', 'high', 'max']);
  // A model with no reasoning setting offers no levels, not the adapter-wide fallback.
  assert.deepEqual(models.find((m) => m.label === 'Pi 5')?.efforts, []);
});

test('DshAcpAdapter: a model that cannot be selected stays listed without effort details', async () => {
  const adapter = new DshAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'dsh', FAKE_DSH_UNSELECTABLE: '["pi-ai","pi-5"]' });
  const models = await adapter.models();
  const pi = models.find((m) => m.label === 'Pi 5');
  assert.ok(pi, 'the unselectable model is still there');
  assert.equal(pi.efforts, undefined, 'no probe result, so the agent-wide list applies');
});

test('DshAcpAdapter: streams a turn with usage but no cost, and images pass through', async () => {
  const adapter = new DshAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'dsh' });
  const session = await adapter.start({ cwd: here, permissionMode: 'auto_edit' });
  const { hooks, events } = collectingHooks();
  const result = await session.prompt(turn('usage and a picture', [{ data: PNG, mimeType: 'image/png' }]), hooks);
  assert.equal(result.stopReason, 'end_turn');
  // dsh reports context pressure without a price; the hub must not invent one.
  const usage = events.find((e) => e.type === 'usage.updated')?.data as { used: number; size: number; cost?: unknown };
  assert.deepEqual(usage, { used: 52000, size: 200000 });
  assert.equal('cost' in (usage ?? {}), false);
  assert.equal(events.find((e) => e.type === 'message.delta' && String(e.data.text).startsWith('images:'))?.data.text, 'images:1:image/png;');
  await session.close();
});

test('ClaudeAcpAdapter: resume uses session/load and keeps the same id', async () => {
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  const session = await adapter.start({ cwd: here, resumeId: 'previous-session' });
  assert.equal(session.agentSessionId, 'previous-session');
  const { hooks, events } = collectingHooks();
  await session.prompt(turn('hi'), hooks);
  // The replayed text sent during session/load must not leak into this turn.
  assert.ok(!events.some((e) => String(e.data.text ?? '').includes('replayed')));
  await session.close();
});

test('DshAcpAdapter: resume goes through session/resume, which never replays', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-dsh-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const log = join(dir, 'resume.log');
  const adapter = new DshAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'dsh', FAKE_ACP_RESUME_LOG: log });
  const session = await adapter.start({ cwd: here, resumeId: 'dsh-persisted-1' });
  assert.equal(session.agentSessionId, 'dsh-persisted-1');
  assert.equal(readFileSync(log, 'utf8'), 'dsh-persisted-1\n');
  const { hooks, events } = collectingHooks();
  const result = await session.prompt(turn('hi'), hooks);
  assert.equal(result.stopReason, 'end_turn');
  assert.equal(events.at(-1)?.data.text, 'echo:hi');
  await session.close();
  // An id the agent cannot resume is the agent's own error, surfaced as a failed start.
  await assert.rejects(adapter.start({ cwd: here, resumeId: 'not-resumable' }), /not resumable/);
});

test('DshAcpAdapter: no plan mode, and the approval names only the call id', async () => {
  const adapter = new DshAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'dsh' });
  assert.deepEqual(adapter.capabilities.permissionModes, []);
  await assert.rejects(adapter.start({ cwd: here, workMode: 'plan' }), /此 agent 不支持计划模式/);

  // The approval request itself carries only the call id and one-shot options; what the card
  // shows is borrowed from the tool's own update, and the agent gets the chosen option back.
  const session = await adapter.start({ cwd: here, permissionMode: 'full_access' });
  const seen: ApprovalRequest[] = [];
  const hooks: TurnHooks = {
    emit: () => undefined,
    requestApproval: async (req) => {
      seen.push(req);
      return { optionId: 'reject-once' };
    },
  };
  const result = await session.prompt(turn('please approve this'), hooks);
  assert.equal(result.stopReason, 'end_turn');
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].options.map((o) => o.optionId), ['allow-once', 'reject-once']);
  // The request itself named only the call id; the title and payload are the tool's own.
  assert.equal(seen[0].title, 'Run ls');
  assert.equal(seen[0].toolCall.title, 'Run ls');
  assert.deepEqual(seen[0].toolCall.rawInput, { command: 'ls' });
  await session.close();
});

test('DshAcpAdapter: cancel resolves a slow prompt as cancelled', async () => {
  const adapter = new DshAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'dsh' });
  const session = await adapter.start({ cwd: here });
  const { hooks } = collectingHooks();
  const pending = session.prompt(turn('slow task'), hooks);
  await new Promise((r) => setTimeout(r, 50));
  await session.cancel();
  assert.equal((await pending).stopReason, 'cancelled');
  await session.close();
});

test('PiRpcAdapter: prompt streams events and settles', async (t) => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  assert.equal(await adapter.available(), true);
  const session = await adapter.start({ cwd: here });
  t.after(() => session.close());
  assert.equal(session.agentSessionId, 'pi-session-1');

  const { hooks, events } = collectingHooks();
  const result = await session.prompt(turn('yo'), hooks);
  assert.deepEqual(result, { stopReason: 'end_turn', error: undefined });
  assert.deepEqual(
    events.map((e) => e.type),
    ['turn.phase', 'reasoning.delta', 'tool.start', 'tool.update', 'tool.end', 'message.delta', 'message.delta', 'usage.updated'],
  );
  // After the turn pi is asked how full its context is.
  assert.deepEqual(events.at(-1)!.data, { used: 1200, size: 200000, cost: { amount: 0.02, currency: 'USD' } });
  await session.close();
});

test('PiRpcAdapter: resume passes --session and reports the given id', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  const session = await adapter.start({ cwd: here, resumeId: 'old-pi' });
  assert.equal(session.agentSessionId, 'old-pi');
  await session.close();
});

test('PiRpcAdapter: model error ends the turn as error', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  const session = await adapter.start({ cwd: here });
  const { hooks } = collectingHooks();
  const result = await session.prompt(turn('please fail'), hooks);
  assert.equal(result.stopReason, 'error');
  assert.equal(result.error, 'provider exploded');
  await session.close();
});

test('PiRpcAdapter: a model error that pi retried successfully does not fail the turn', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  const session = await adapter.start({ cwd: here });
  const { hooks } = collectingHooks();
  const result = await session.prompt(turn('please retry'), hooks);
  assert.equal(result.stopReason, 'end_turn');
  assert.equal(result.error, undefined);
  await session.close();
});

test('PiRpcAdapter: abort ends a slow turn as cancelled', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  const session = await adapter.start({ cwd: here });
  const { hooks } = collectingHooks();
  const pending = session.prompt(turn('slow'), hooks);
  await new Promise((r) => setTimeout(r, 50));
  await session.cancel();
  assert.equal((await pending).stopReason, 'cancelled');
  await session.close();
});

test('modelsFromConfig reads the model option and keeps the description', () => {
  const models = modelsFromConfig([
    { id: 'mode', category: 'mode', options: [{ value: 'default', name: 'Manual' }] },
    { id: 'model', category: 'model', options: [{ value: 'opus', name: 'Opus 5.5', description: 'For complex work' }, { value: 'haiku', name: 'Haiku 5.5', description: '' }] },
  ]);
  assert.deepEqual(models, [
    { id: 'opus', label: 'Opus 5.5', description: 'For complex work' },
    { id: 'haiku', label: 'Haiku 5.5', description: undefined },
  ]);
  assert.deepEqual(modelsFromConfig(undefined), []);
});

test('ClaudeAcpAdapter: model list and slash commands come from the agent', async () => {
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  const models = await adapter.models(); // no session yet: the adapter probes one
  // Listing does not select models or hide one based on a preflight request.
  assert.deepEqual(models.map((m) => m.id), ['default', 'big', 'small', 'broken']);
  assert.equal(models[1].description, 'For hard work');
  assert.equal(await adapter.models(), models, 'the probe runs once and is cached');
  // What the agent announced, then the commands it answers without announcing them.
  assert.deepEqual((await adapter.commands()).map((c) => c.name), ['compact', 'review', 'goal', 'cost', 'stats', 'context']);
});

test('ClaudeAcpAdapter: permission mode, model and effort are applied when the native session starts', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-claude-start-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const started = join(dir, 'new.jsonl');
  const selected = join(dir, 'selected.log');
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_NEW_LOG: started, FAKE_ACP_MODEL_SELECTION_LOG: selected });
  const session = await adapter.start({ cwd: here, permissionMode: 'auto_edit', model: 'big', effort: 'high' });
  const { hooks, events } = collectingHooks();
  await session.prompt(turn('model?'), hooks);
  // The fake has no "auto" mode, so auto_edit falls back to acceptEdits.
  assert.equal(events.at(-1)?.data.text, 'model:big;mode:acceptEdits;effort:high');
  assert.deepEqual(JSON.parse(readFileSync(started, 'utf8'))._meta.claudeCode.options, { model: 'big', effort: 'high' });
  assert.equal(readFileSync(selected, 'utf8'), 'big\n', 'a fresh Claude session must select the model only at native startup');
  await session.close();
});

test('ClaudeAcpAdapter: full_access skips the approval prompt, and settings change on a live session', async () => {
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  const session = await adapter.start({ cwd: here, permissionMode: 'full_access' });
  const first = collectingHooks();
  await session.prompt(turn('please approve this'), first.hooks);
  assert.deepEqual(first.approvals, [], 'no approval should be requested in full_access');

  await session.setPermissionMode!('ask');
  assert.equal(await session.setModel!('big'), true);
  assert.equal(await session.setEffort!('low'), true);
  const second = collectingHooks();
  await session.prompt(turn('model?'), second.hooks);
  assert.equal(second.events.at(-1)?.data.text, 'model:big;mode:default;effort:low');
  await session.close();
});

test('effortsFromConfig lists real levels only, and is empty when the option is missing', () => {
  assert.deepEqual(
    effortsFromConfig([{ id: 'effort', category: 'thought_level', options: [{ value: 'default', name: 'Default' }, { value: 'low', name: 'Low' }, { value: 'max', name: 'Max' }] }]),
    ['low', 'max'],
  );
  assert.deepEqual(effortsFromConfig([{ id: 'model', category: 'model', options: [] }]), []);
  assert.deepEqual(effortsFromConfig(undefined), []);
});

test('piEfforts follows pi\'s own rule for which thinking levels a model takes', () => {
  assert.deepEqual(piEfforts({ reasoning: false }), []);
  // null is unsupported; xhigh and max need an explicit mapping; everything else is allowed.
  assert.deepEqual(
    piEfforts({ reasoning: true, thinkingLevelMap: { off: null, minimal: null, low: 'low', medium: null, high: 'high', xhigh: null, max: 'max' } }),
    ['low', 'high', 'max'],
  );
  assert.deepEqual(piEfforts({ reasoning: true }), ['off', 'minimal', 'low', 'medium', 'high']);
  assert.deepEqual(piEfforts({ reasoning: true, thinkingLevelMap: { xhigh: 'xhigh' } }), ['off', 'minimal', 'low', 'medium', 'high', 'xhigh']);
});

// Vision fallback is populated independently of the model catalog.
async function piModelsWithVision(adapter: PiRpcAdapter) {
  for (let i = 0; i < 300; i++) {
    const models = await adapter.models();
    if (models.find(model => model.id === 'fake/plain')?.images !== undefined) return models;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('MCP detection did not finish');
}

test('PiRpcAdapter: models report efforts, image support and which one is pi\'s default', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  const models = await piModelsWithVision(adapter); // fails in the fake if the probe forgets --no-session
  assert.deepEqual(models.map((m) => m.id), ['fake/think', 'fake/plain', 'fake/loose']);
  assert.deepEqual(models.map((m) => m.efforts), [['low', 'high', 'max'], [], ['off', 'minimal', 'low', 'medium', 'high']]);
  assert.deepEqual(models.map((m) => m.images), [true, false, false]);
  assert.deepEqual(models.map((m) => m.isDefault ?? false), [true, false, false]);
  assert.equal(models[0].label, 'Think');
  assert.deepEqual(await adapter.models(), models, 'a fresh catalog is reused');
});

test('PiRpcAdapter: commands carry their kind and have no leading slash', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  assert.deepEqual((await adapter.commands(here)).slice(PI_LOCAL_COMMANDS.length), [
    { name: 'fix-tests', description: 'Fix failing tests', kind: 'prompt' },
    { name: 'skill:pdf', description: 'Work with PDF files', kind: 'skill' },
    { name: 'deploy', description: '', kind: 'command' },
  ]);
});

test('mapPiEvent gives tools a readable title, a kind, and file changes for edit and write', () => {
  const bash = mapPiEvent({ type: 'tool_execution_start', toolCallId: 'a', toolName: 'bash', args: { command: 'npm test\necho done' } });
  assert.deepEqual([bash?.data.title, bash?.data.kind, bash?.data.content], ['npm test', 'execute', undefined]);

  const edit = mapPiEvent({
    type: 'tool_execution_start',
    toolCallId: 'b',
    toolName: 'edit',
    args: { path: 'src/a.ts', edits: [{ oldText: 'x', newText: 'y' }, { oldText: 'p', newText: 'q' }] },
  });
  assert.deepEqual([edit?.data.title, edit?.data.kind], ['src/a.ts', 'edit']);
  assert.deepEqual(edit?.data.content, [
    { type: 'diff', path: 'src/a.ts', oldText: 'x', newText: 'y' },
    { type: 'diff', path: 'src/a.ts', oldText: 'p', newText: 'q' },
  ]);

  const write = mapPiEvent({ type: 'tool_execution_start', toolCallId: 'c', toolName: 'write', args: { path: 'new.txt', content: 'hi' } });
  assert.deepEqual(write?.data.content, [{ type: 'diff', path: 'new.txt', oldText: null, newText: 'hi' }]);

  const grep = mapPiEvent({ type: 'tool_execution_start', toolCallId: 'd', toolName: 'grep', args: { pattern: 'TODO' } });
  assert.deepEqual([grep?.data.title, grep?.data.kind], ['grep TODO', 'search']);
});

test('images reach both agents as image blocks, before the text', async () => {
  const image = { data: PNG, mimeType: 'image/png' };
  const claude = await new ClaudeAcpAdapter([process.execPath, fakeAcp]).start({ cwd: here });
  const a = collectingHooks();
  await claude.prompt(turn('what is this', [image, image]), a.hooks);
  const claudeText = a.events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('');
  assert.ok(claudeText.startsWith('images:2:image/png,image/png;'), claudeText);
  assert.ok(claudeText.endsWith('echo:what is this'), claudeText);
  // An image with no text is a valid turn.
  const only = collectingHooks();
  assert.equal((await claude.prompt(turn('', [image]), only.hooks)).stopReason, 'end_turn');
  await claude.close();

  const pi = await new PiRpcAdapter([process.execPath, fakePi]).start({ cwd: here });
  const b = collectingHooks();
  await pi.prompt(turn('look', [image]), b.hooks);
  const piText = b.events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('');
  assert.ok(piText.startsWith('images:1:image/png;'), piText);
  await pi.close();
});

test('ClaudeAcpAdapter: commands keep the argument hint and are tracked per directory', async () => {
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  const session = await adapter.start({ cwd: here });
  const commands = await adapter.commands(here);
  assert.deepEqual(commands[0], { name: 'compact', description: 'Summarise the conversation', hint: '<instructions>' });
  assert.equal('hint' in commands[1], false);
  // A directory with no session yet falls back to a known list instead of nothing.
  assert.deepEqual((await adapter.commands('/somewhere/else')).map((c) => c.name), ['compact', 'review', 'goal', 'cost', 'stats', 'context']);
  await session.close();
});

import { turnsFromAcpReplay } from '../src/adapters/acp.js';
import { summarisePiSession, turnsFromPiMessages, withPiForkRefs } from '../src/adapters/pi.js';
import { mkdirSync as mkdir, mkdtempSync as mkdtemp, rmSync as rm, writeFileSync as writeFile } from 'node:fs';
import { tmpdir as osTmpdir } from 'node:os';

test('turnsFromAcpReplay groups a replayed session into turns', () => {
  const chunk = (kind: string, text: string, messageId?: string) => ({ sessionUpdate: kind, messageId, content: { type: 'text', text } });
  const turns = turnsFromAcpReplay([
    { sessionUpdate: 'available_commands_update', availableCommands: [] },
    chunk('agent_message_chunk', 'stray text before any user message'),
    chunk('user_message_chunk', 'first ', 'u1'),
    chunk('user_message_chunk', 'question', 'u1'),
    chunk('agent_thought_chunk', 'thinking'),
    { sessionUpdate: 'tool_call', toolCallId: 't', title: 'ls', kind: 'execute', status: 'pending' },
    { sessionUpdate: 'tool_call_update', toolCallId: 't', status: 'completed', rawOutput: 'a.txt' },
    chunk('agent_message_chunk', 'answer one'),
    chunk('user_message_chunk', 'second', 'u2'),
    { sessionUpdate: 'user_message_chunk', messageId: 'u2', content: { type: 'image', mimeType: 'image/png', data: PNG } },
    { sessionUpdate: 'usage_update' },
    chunk('agent_message_chunk', 'answer two'),
  ]);
  assert.equal(turns.length, 2);
  assert.equal(turns[0].text, 'first question', 'chunks of one message are joined');
  assert.deepEqual(turns[0].events.map((e) => e.type), ['reasoning.delta', 'tool.start', 'tool.end', 'message.delta']);
  assert.equal(turns[1].text, 'second');
  assert.deepEqual(turns[1].images, [{ data: PNG, mimeType: 'image/png' }]);
  assert.deepEqual(turns[1].events.map((e) => e.data.text), ['answer two']);
  assert.deepEqual(turnsFromAcpReplay([]), []);
});

test('turnsFromAcpReplay keeps two user messages in a row as one turn with both texts', () => {
  const turns = turnsFromAcpReplay([
    { sessionUpdate: 'user_message_chunk', messageId: 'a', content: { type: 'text', text: 'one' } },
    { sessionUpdate: 'user_message_chunk', messageId: 'b', content: { type: 'text', text: 'two' } },
    { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'ok' } },
  ]);
  assert.equal(turns.length, 1);
  assert.equal(turns[0].text, 'one\n\ntwo');
});

test('turnsFromAcpReplay leaves out notes the agent wrote as user messages, and starts a new turn after one', () => {
  const user = (text: string, messageId: string) => ({ sessionUpdate: 'user_message_chunk', messageId, content: { type: 'text', text } });
  const answer = (text: string) => ({ sessionUpdate: 'agent_message_chunk', messageId: `m-${text}`, content: { type: 'text', text } });
  const note = (text: string) => /^\[Request interrupted by user( for tool use)?\]$/.test(text);
  const turns = turnsFromAcpReplay([
    user('look at this', 'a'),
    answer('looking'),
    user('[Request interrupted by user for tool use]', 'n1'),
    user('go on', 'b'),
    answer('went on'),
    // Stopped before any answer: the message is kept, the next one is a turn of its own.
    user('never answered', 'c'),
    user('[Request interrupted by user]', 'n2'),
    user('ask again', 'd'),
    answer('done'),
  ], note);
  assert.deepEqual(turns.map((t) => t.text), ['look at this', 'go on', 'never answered', 'ask again']);
  assert.equal(turns[0].forkRef, 'm-looking');
  // Without the predicate the note is an ordinary user message.
  assert.deepEqual(turnsFromAcpReplay([user('[Request interrupted by user]', 'n'), user('go on', 'b')]).map((t) => t.text), ['[Request interrupted by user]\n\ngo on']);
});

test('turnsFromPiMessages maps user, assistant and tool result messages to turns', () => {
  const turns = turnsFromPiMessages([
    { role: 'system', content: '' },
    { role: 'assistant', content: [{ type: 'text', text: 'before any user message' }] },
    { role: 'user', content: 'run the tests' },
    { role: 'assistant', content: [{ type: 'thinking', thinking: 'hmm' }, { type: 'toolCall', id: 'c1', name: 'edit', arguments: { path: 'a.ts', edits: [{ oldText: 'x', newText: 'y' }] } }] },
    { role: 'toolResult', toolCallId: 'c1', content: [{ type: 'text', text: 'edited' }], isError: false },
    { role: 'assistant', content: [{ type: 'text', text: 'Done.' }] },
    { role: 'user', content: [{ type: 'text', text: 'look' }, { type: 'image', data: PNG, mimeType: 'image/png' }] },
    { role: 'toolResult', toolCallId: 'c2', content: [{ type: 'text', text: 'boom' }], isError: true },
  ]);
  assert.equal(turns.length, 2);
  assert.equal(turns[0].text, 'run the tests');
  assert.deepEqual(turns[0].events.map((e) => e.type), ['reasoning.delta', 'tool.start', 'tool.end', 'message.delta']);
  const start = turns[0].events[1];
  assert.equal(start.data.title, 'a.ts', 'the verb comes from the kind, the title is just the path');
  assert.deepEqual(start.data.content, [{ type: 'diff', path: 'a.ts', oldText: 'x', newText: 'y' }], 'edits come across as diffs');
  assert.equal(turns[0].events[2].data.output, 'edited');
  assert.equal(turns[1].text, 'look');
  assert.equal(turns[1].images.length, 1);
  assert.equal(turns[1].events[0].data.status, 'failed');
  assert.deepEqual(turnsFromPiMessages([]), []);
});

test('summarisePiSession reads id and directory from the header and picks a title', () => {
  const header = JSON.stringify({ type: 'session', version: 3, id: 'abc', timestamp: '2026-01-01T00:00:00Z', cwd: '/work/app' });
  const user = (text: string) => JSON.stringify({ type: 'message', id: 'm', message: { role: 'user', content: [{ type: 'text', text }] } });
  const system = JSON.stringify({ type: 'message', id: 's', message: { role: 'system', content: '' } });
  const named = (name: string) => JSON.stringify({ type: 'session_info', id: 'n', name });

  const plain = summarisePiSession([header, system, user('Fix the login bug\nmore detail'), user('later message')].join('\n'), '2026-02-02T00:00:00.000Z');
  assert.deepEqual(plain, { id: 'abc', cwd: '/work/app', title: 'Fix the login bug', updatedAt: '2026-02-02T00:00:00.000Z' });

  // A name set by the user wins over the first message, and the latest name wins.
  assert.equal(summarisePiSession([header, user('first words'), named('Old name'), named('Auth refactor')].join('\n'), null)?.title, 'Auth refactor');
  // No messages yet, and a last line cut off mid-way by the read limit.
  assert.equal(summarisePiSession([header, '{"type":"message","id":"x","message":{"role":"user","content":"cut of'].join('\n'), null)?.title, '');
  assert.equal(summarisePiSession('not json', null), null);
  assert.equal(summarisePiSession(JSON.stringify({ type: 'message' }), null), null);
});

test('PiRpcAdapter: sessions are discovered from pi\'s session files, newest first', async () => {
  const agentDir = mkdtemp(join(osTmpdir(), 'calli-pi-agent-'));
  const previous = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  try {
    const adapter = new PiRpcAdapter([process.execPath, fakePi]);
    assert.deepEqual(await adapter.listSessions(), [], 'no sessions directory yet');

    const write = (dir: string, file: string, lines: unknown[]) => {
      mkdir(join(agentDir, 'sessions', dir), { recursive: true });
      writeFile(join(agentDir, 'sessions', dir, file), lines.map((l) => JSON.stringify(l)).join('\n') + '\n');
    };
    write('--work-app--', 'a.jsonl', [
      { type: 'session', id: 'hist-old', cwd: '/work/app' },
      { type: 'message', message: { role: 'user', content: 'older one' } },
    ]);
    await new Promise((r) => setTimeout(r, 20));
    write('--work-lib--', 'b.jsonl', [
      { type: 'session', id: 'hist-new', cwd: '/work/lib' },
      { type: 'session_info', name: 'Named session' },
    ]);
    writeFile(join(agentDir, 'sessions', '--work-app--', 'notes.txt'), 'not a session');
    writeFile(join(agentDir, 'sessions', '--work-app--', 'broken.jsonl'), 'garbage');

    const list = await adapter.listSessions();
    assert.deepEqual(list.map((s) => [s.id, s.cwd, s.title]), [
      ['hist-new', '/work/lib', 'Named session'],
      ['hist-old', '/work/app', 'older one'],
    ]);
    assert.ok(list.every((s) => typeof s.updatedAt === 'string'));
  } finally {
    if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previous;
    rm(agentDir, { recursive: true, force: true });
  }
});

test('both agents list, replay and fork sessions through their own means', async () => {
  const claude = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
  const turns = await claude.loadHistory('hist-x', here);
  assert.deepEqual(turns.map((t) => t.text), ['first question', 'second question']);
  assert.equal(turns[1].images.length, 1);
  assert.equal(await claude.forkSession('hist-x', here), 'hist-x-fork');
  await assert.rejects(claude.forkSession('unforkable', here), /cannot fork/);
  // A copy can stop where a turn ended: at the last message the agent wrote in it.
  assert.deepEqual(turns.map((t) => t.forkRef), ['m1b', 'm2']);
  assert.equal(await claude.forkSession('hist-x', here, turns[0].forkRef), 'hist-x-fork-at-m1b');
  await assert.rejects(claude.forkSession('hist-x', here, 'no-such-message'), /was not found/);

  const pi = new PiRpcAdapter([process.execPath, fakePi]);
  const piTurns = await pi.loadHistory('hist-p', here);
  assert.deepEqual(piTurns.map((t) => t.text), ['run the tests', 'and this picture?']);
  assert.deepEqual(piTurns[0].events.map((e) => e.type), ['reasoning.delta', 'tool.start', 'tool.end', 'message.delta']);
  assert.equal(await pi.forkSession('hist-p', here), 'hist-p-clone');
  await assert.rejects(pi.forkSession('uncloneable', here), /cancelled/);
  // pi forks from a user message, so "after the first turn" is a fork from the second turn's
  // message. Nothing comes after the last turn; its copy is a clone.
  assert.deepEqual(piTurns.map((t) => t.forkRef), ['e2', undefined]);
  assert.equal(await pi.forkSession('hist-p', here, piTurns[0].forkRef), 'hist-p-fork-e2');
  // The copy holds the first turn and nothing of the second.
  assert.deepEqual((await pi.loadHistory('hist-p-fork-e2', here)).map((t) => t.text), ['run the tests']);
  await assert.rejects(pi.forkSession('hist-p', here, 'e9'), /no entry e9/);
});

test('a copy can be made for another directory, each agent in its own way', async () => {
  // Where the hub puts a worktree: `<repository>/.worktrees/<name>`.
  const tree = join(mkdtempSync(join(tmpdir(), 'calli-test-into-')), '.worktrees', 'abcd1234');
  mkdirSync(tree, { recursive: true });
  try {
    // Claude is asked for the copy from where it will run.
    const claude = new ClaudeAcpAdapter([process.execPath, fakeAcp]);
    assert.equal(await claude.forkSession('hist-x', here, undefined, tree), 'hist-x-fork-in-abcd1234');
    assert.equal(await claude.forkSession('hist-x', here, 'm1b', tree), 'hist-x-fork-at-m1b-in-abcd1234');

    // pi keeps sessions by directory: a pi started over there copies the session in with `--fork`.
    const pi = new PiRpcAdapter([process.execPath, fakePi]);
    assert.equal(await pi.forkSession('hist-p', here, undefined, tree), 'hist-p-in-abcd1234');
    assert.deepEqual((await pi.loadHistory('hist-p-in-abcd1234', tree)).map((t) => t.text), ['run the tests', 'and this picture?']);
    // It will not open that copy from anywhere else, which is why the copy has to be made there.
    await assert.rejects(pi.loadHistory('hist-p-in-abcd1234', here), /exited/);
    // Cut at a turn, the copy is cut over there too.
    assert.equal(await pi.forkSession('hist-p', here, 'e2', tree), 'hist-p-in-abcd1234-fork-e2');
    assert.deepEqual((await pi.loadHistory('hist-p-in-abcd1234-fork-e2', tree)).map((t) => t.text), ['run the tests']);

    // TRAE CLI uses its app-server turn ids for history and fork, matching the protocol that runs
    // live turns so an active writer does not block a history-boundary fork.
    const traex = new TraexAcpAdapter([process.execPath, fakeAcp], { FAKE_ACP_FLAVOR: 'traex' });
    assert.deepEqual((await traex.loadHistory('hist-x', here)).map((t) => t.forkRef), ['turn-1', 'turn-2']);
    assert.equal(await traex.forkSession('hist-x', here, undefined, tree), 'hist-x-fork-in-abcd1234');
    assert.equal(await traex.forkSession('hist-x', here, 'turn-1', tree), 'hist-x-fork-at-turn-1-in-abcd1234');
    assert.equal(await traex.forkSession('hist-x', here, 'turn-1'), 'hist-x-fork-at-turn-1');

    // Codex is told where the copy will work; resuming says it again.
    const codex = new CodexAppServerAdapter([process.execPath, fakeCodex]);
    assert.equal(await codex.forkSession('codex-thread-1', here, undefined, tree), 'codex-thread-1-fork-in-abcd1234');
    assert.equal(await codex.forkSession('hist-thread', here, 'turn-1', tree), 'hist-thread-fork-at-turn-1-in-abcd1234');

    assert.deepEqual(
      [claude, pi, codex, new TraexAcpAdapter(['traex']), new AgyAdapter(['agy'])].map((a) => [a.id, a.capabilities.forkInto]),
      [['claude', true], ['pi', true], ['codex', true], ['traex', true], ['agy', false]],
    );
  } finally {
    rmSync(join(tree, '..', '..'), { recursive: true, force: true });
  }
});

test('pi fork points are matched to turns by what was asked, not by position', () => {
  const turn = (text: string) => ({ text, images: [], events: [] });
  // pi lists a message that is not one of the turns here (say, from a branch summary): passed over.
  const refs = withPiForkRefs(
    [turn('one'), turn('two'), turn('three')],
    [{ entryId: 'a', text: 'one' }, { entryId: 'x', text: 'something else' }, { entryId: 'b', text: ' two ' }, { entryId: 'c', text: 'three' }],
  ).map((t) => t.forkRef);
  assert.deepEqual(refs, ['b', 'c', undefined]);
  // A turn pi has no fork point for: the turn before it cannot be forked at, the others still can.
  assert.deepEqual(withPiForkRefs([turn('one'), turn('two'), turn('three')], [{ entryId: 'a', text: 'one' }, { entryId: 'c', text: 'three' }]).map((t) => t.forkRef), [undefined, 'c', undefined]);
  assert.deepEqual(withPiForkRefs([turn('one')], []).map((t) => t.forkRef), [undefined]);
});

import { AgyAdapter, agyArgs, agyTurnResult, mapAgyStep, parseAgyModels } from '../src/adapters/agy.js';

const fakeAgy = join(here, 'fixtures', 'fake-agy.mjs');
const textFrom = (events: EmittedEvent[]) => events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('');

test('mapAgyStep maps reply text and tool steps, and ignores bookkeeping steps', () => {
  const started = new Set<number>();
  assert.deepEqual(mapAgyStep({ step_index: 0, state: 'DONE', step_type: 'user_input' }, started), []);
  assert.deepEqual(mapAgyStep({ step_index: 3, state: 'DONE', step_type: 'system_message' }, started), []);
  // A reply step with no text is the model deciding to call a tool.
  assert.deepEqual(mapAgyStep({ step_index: 1, state: 'DONE', step_type: 'agent_response', usage: {} }, started), []);
  assert.deepEqual(mapAgyStep({ step_index: 1, state: 'ACTIVE', step_type: 'agent_response', text_delta: 'Hel' }, started), [
    { type: 'message.delta', data: { text: 'Hel' } },
  ]);

  const info = { name: 'run_command', parameters: { CommandLine: 'npm test\necho done' } };
  assert.deepEqual(mapAgyStep({ step_index: 2, state: 'ACTIVE', step_type: 'tool', tool_name: 'run_command', tool_info: info }, started), [
    { type: 'tool.start', data: { id: 'agy-step-2', title: 'npm test', kind: 'execute', status: 'in_progress', tool: 'run_command', input: info.parameters } },
  ]);
  assert.deepEqual(
    mapAgyStep({ step_index: 2, state: 'DONE', step_type: 'tool', tool_name: 'run_command', tool_info: { ...info, output: '12 passed' } }, started),
    [{ type: 'tool.end', data: { id: 'agy-step-2', status: 'completed', output: '12 passed' } }],
  );

  // A refused command arrives as an error with the reason.
  const refused = mapAgyStep(
    { step_index: 5, state: 'ERROR', step_type: 'tool', tool_name: 'run_command', tool_info: { ...info, error: { type: 'TOOL_ERROR', message: 'user denied permission' } } },
    started,
  );
  assert.deepEqual(refused.map((e) => [e.type, e.data.status]), [['tool.start', 'in_progress'], ['tool.end', 'failed']], 'a step never seen active still gets its start');
  assert.equal(refused[1].data.output, 'user denied permission');
});

test('mapAgyStep names file tools by what they do, and shows a subagent by its role and task', () => {
  const start = (tool_name: string, parameters: Record<string, unknown>, index: number) =>
    mapAgyStep({ step_index: index, state: 'ACTIVE', step_type: 'tool', tool_name, tool_info: { name: tool_name, parameters } }, new Set())[0].data;
  assert.deepEqual([start('write_to_file', { TargetFile: '/w/a.txt' }, 1).title, start('write_to_file', { TargetFile: '/w/a.txt' }, 1).kind], ['Write /w/a.txt', 'edit']);
  assert.equal(start('replace_file_content', { TargetFile: '/w/a.txt' }, 2).title, 'Edit /w/a.txt');
  assert.deepEqual([start('view_file', { AbsolutePath: '/w/a.txt' }, 3).title, start('view_file', { AbsolutePath: '/w/a.txt' }, 3).kind], ['Read /w/a.txt', 'read']);
  assert.deepEqual([start('grep_search', { Query: 'TODO' }, 4).title, start('grep_search', { Query: 'TODO' }, 4).kind], ['grep_search TODO', 'search']);
  assert.deepEqual([start('manage_task', {}, 5).title, start('manage_task', {}, 5).kind], ['manage_task', 'other']);

  const [sub] = mapAgyStep(
    {
      step_index: 6,
      state: 'ACTIVE',
      step_type: 'subagent',
      tool_name: 'invoke_subagent',
      subagent_info: { subagents: [{ type_name: 'image-generator', role: 'Image Generator', initial_prompt: 'An orange circle.\nKeep it flat.' }] },
    },
    new Set(),
  );
  assert.equal(sub.data.title, 'Image Generator: An orange circle.');
});

test('agyTurnResult: success, interruption, errors, and actions refused for lack of a prompt', () => {
  assert.deepEqual(agyTurnResult({ status: 'SUCCESS', response: 'ok' }, false), { stopReason: 'end_turn' });
  assert.deepEqual(agyTurnResult({ status: 'ERROR', error: 'interrupted' }, true), { stopReason: 'cancelled' });
  assert.deepEqual(agyTurnResult({ status: 'ERROR', error: 'quota exceeded' }, false), { stopReason: 'error', error: 'quota exceeded' });
  const denied = agyTurnResult(
    { status: 'SUCCESS', response: '', denied_actions: [{ action: 'command', display_name: 'RunCommand' }, { action: 'command', display_name: 'RunCommand' }] },
    false,
  );
  assert.equal(denied.stopReason, 'error');
  assert.equal(denied.errorCode, 'permission_refused');
  assert.deepEqual(denied.refused, ['RunCommand'], 'each kind of action is named once');
  assert.match(denied.error!, /refused RunCommand:/);
  assert.match(denied.error!, /full access/);
});

test('parseAgyModels reads id and label pairs and skips everything else', () => {
  const models = parseAgyModels('Fetching available models...\nfast-low\tFast (Low)\n\nbroken line\nbig\tBig (Thinking)\n\tno id\n');
  assert.deepEqual(models.map((m) => [m.id, m.label]), [['fast-low', 'Fast (Low)'], ['big', 'Big (Thinking)']]);
  assert.ok(models.every((m) => m.efforts?.length === 0 && m.images === undefined), 'no effort setting; images go in as paths');
  assert.deepEqual(parseAgyModels(''), []);
});

test('agyArgs turns the permission level, model and conversation into flags', () => {
  const base = ['--input-format', 'stream-json', '--output-format', 'stream-json'];
  assert.deepEqual(agyArgs({}), [...base, '--mode', 'accept-edits', '-p=']);
  assert.deepEqual(agyArgs({ permissionMode: 'auto_edit', model: 'fast-low' }), [...base, '--mode', 'accept-edits', '--model', 'fast-low', '-p=']);
  assert.deepEqual(agyArgs({ permissionMode: 'full_access', conversationId: 'c1' }), [...base, '--dangerously-skip-permissions', '--conversation', 'c1', '-p=']);
  assert.equal(agyArgs({ permissionMode: 'full_access' }).includes('--mode'), false);
});

test('AgyAdapter: models, a turn with a tool, and several turns in one process', async () => {
  const adapter = new AgyAdapter([process.execPath, fakeAgy]);
  assert.equal(await adapter.available(), true);
  assert.equal(await new AgyAdapter(['definitely-not-installed-agy']).available(), false);
  assert.deepEqual((await adapter.models()).map((m) => m.id), ['fast-low', 'fast-high', 'big-thinking']);
  assert.ok((await adapter.commands()).some((c) => c.name === 'usage'));
  assert.equal(adapter.resumeCommand('abc'), 'agy --conversation abc');

  const session = await adapter.start({ cwd: here, model: 'fast-low' });
  try {
    assert.equal(session.agentSessionId, 'agy-conv-1', 'the conversation id is known before the first message');
    const first = collectingHooks();
    assert.deepEqual(await session.prompt(turn('use a tool'), first.hooks), { stopReason: 'end_turn' });
    assert.deepEqual(first.events.map((e) => e.type), ['tool.start', 'tool.end', 'message.delta', 'message.delta']);
    assert.equal(first.events[1].data.output, 'hi\r\n');
    assert.equal(textFrom(first.events).trim(), 'echo:use a tool;model:fast-low;mode:accept-edits;conv:agy-conv-1');

    const second = collectingHooks();
    await session.prompt(turn('again'), second.hooks);
    assert.match(textFrom(second.events), /^echo:again;/);
    assert.deepEqual(second.events.map((e) => e.type), ['message.delta', 'message.delta'], 'steps of the first turn are not repeated');
  } finally {
    await session.close();
  }
});

test('AgyAdapter: a permission change restarts the process on the same conversation', async () => {
  const adapter = new AgyAdapter([process.execPath, fakeAgy]);
  const session = await adapter.start({ cwd: here, resumeId: 'conv-kept', permissionMode: 'auto_edit' });
  try {
    assert.equal(session.agentSessionId, 'conv-kept');
    const refused = collectingHooks();
    const result = await session.prompt(turn('run a command'), refused.hooks);
    assert.equal(result.stopReason, 'error');
    assert.match(result.error!, /refused RunCommand/);
    assert.deepEqual(refused.events.map((e) => [e.type, e.data.status]), [['tool.start', 'in_progress'], ['tool.end', 'failed']]);

    await session.setPermissionMode!('full_access');
    const allowed = collectingHooks();
    assert.deepEqual(await session.prompt(turn('run a command'), allowed.hooks), { stopReason: 'end_turn' });
    assert.match(textFrom(allowed.events), /mode:always-proceed;conv:conv-kept/);
    assert.equal(session.agentSessionId, 'conv-kept');
  } finally {
    await session.close();
  }
});

test('AgyAdapter: cancelling stops the turn, and the next message carries on in the same conversation', async () => {
  const adapter = new AgyAdapter([process.execPath, fakeAgy]);
  const session = await adapter.start({ cwd: here });
  try {
    const slow = collectingHooks();
    const running = session.prompt(turn('slow'), slow.hooks);
    await new Promise((r) => setTimeout(r, 150));
    await session.cancel();
    assert.deepEqual(await running, { stopReason: 'cancelled' });
    assert.equal(textFrom(slow.events), 'working', 'what was said before the cancel is kept');

    const next = collectingHooks();
    assert.deepEqual(await session.prompt(turn('hello'), next.hooks), { stopReason: 'end_turn' });
    assert.match(textFrom(next.events), /^echo:hello;.*conv:agy-conv-1/);
    await session.cancel(); // nothing running: a no-op
  } finally {
    await session.close();
  }
});

test('AgyAdapter: a process that dies mid-turn is reported with what it said, and the session recovers', async () => {
  const adapter = new AgyAdapter([process.execPath, fakeAgy]);
  const session = await adapter.start({ cwd: here });
  try {
    const result = await session.prompt(turn('crash'), collectingHooks().hooks);
    assert.equal(result.stopReason, 'error');
    assert.match(result.error!, /code=7.*something broke/);
    const after = collectingHooks();
    assert.deepEqual(await session.prompt(turn('still there?'), after.hooks), { stopReason: 'end_turn' });
  } finally {
    await session.close();
  }
  await assert.rejects(new AgyAdapter(['definitely-not-installed-agy']).start({ cwd: here }), /could not be started/);
  // Something that is not agy exits without announcing a conversation; its complaint is passed on.
  await assert.rejects(new AgyAdapter([process.execPath, '--no-such-flag']).start({ cwd: here }), /agy exited \(code=\d+.*bad option/);
});

import { formatAgyOutput, parseAgyHelp, parseAgySkills } from '../src/adapters/agy.js';
import { formatPiCompact, formatPiStats, PI_LOCAL_COMMANDS } from '../src/adapters/pi.js';
import { withUnlistedCommands } from '../src/adapters/claude.js';
test('parseAgyHelp reads names, aliases and descriptions; parseAgySkills reads skills', () => {
  const help = '/config (settings)\tOpen settings panel\n/usage (quota)\tView model quota usage\n/help\tShow available commands\nnot a command\n\tno name\n';
  assert.deepEqual(parseAgyHelp(help), [
    { name: 'config', aliases: ['settings'], description: 'Open settings panel' },
    { name: 'usage', aliases: ['quota'], description: 'View model quota usage' },
    { name: 'help', aliases: [], description: 'Show available commands' },
  ]);
  assert.deepEqual(parseAgySkills('make-video\tTurn a brief into a video\nbad name here\tx\n\n'), [
    { name: 'make-video', description: 'Turn a brief into a video', kind: 'skill' },
  ]);
});

test('formatAgyOutput turns tab-separated listings into a table and passes other text through', () => {
  assert.equal(
    formatAgyOutput('Gemini\tWeekly\t97%\nClaude | GPT\tFive Hour\t100%\n'),
    ['| | | |', '|---|---|---|', '|Gemini|Weekly|97%|', '|Claude \\| GPT|Five Hour|100%|'].join('\n'),
  );
  assert.equal(formatAgyOutput('a\tb\nc\n'), 'a\tb\nc', 'a mix is not a table');
  assert.equal(formatAgyOutput('# Changelog\n\n## 1.3.3\n'), '# Changelog\n\n## 1.3.3');
  assert.equal(formatAgyOutput('\n'), '（没有输出）');
  assert.equal(formatAgyOutput('one\ttwo\tthree\nfour\n'.replace('four', 'four\tfive')), ['| | | |', '|---|---|---|', '|one|two|three|', '|four|five| |'].join('\n'));
});

test('AgyAdapter: lists what agy answers itself plus its skills, and answers those commands without the model', async () => {
  const adapter = new AgyAdapter([process.execPath, fakeAgy]);
  const commands = await adapter.commands(here);
  assert.deepEqual(
    commands.map((c) => [c.name, c.kind]),
    [['config', 'command'], ['skills', 'command'], ['usage', 'command'], ['broken', 'command'], ['make-video', 'skill'], ['review', 'skill']],
    'model and effort are left out: they are set by the hub, per session',
  );

  const session = await adapter.start({ cwd: here });
  try {
    const usage = collectingHooks();
    assert.deepEqual(await session.prompt(turn('/usage'), usage.hooks), { stopReason: 'end_turn' });
    assert.match(textFrom(usage.events), /^\| \| \| \| \|\n\|---\|---\|---\|---\|\n\|Fast models\|Weekly Limit Remaining\|97%\|/);
    const alias = collectingHooks();
    await session.prompt(turn('/quota'), alias.hooks);
    assert.match(textFrom(alias.events), /Weekly Limit Remaining/, 'aliases work too');

    const broken = await session.prompt(turn('/broken'), collectingHooks().hooks);
    assert.deepEqual(broken, { stopReason: 'error', error: 'agy /broken failed: /broken failed: not available here' });

    // Skills and unknown commands go to the model as text, through the session's process.
    const skill = collectingHooks();
    await session.prompt(turn('/make-video a cat'), skill.hooks);
    assert.match(textFrom(skill.events), /^echo:\/make-video a cat;/);
    const plain = collectingHooks();
    await session.prompt(turn('/unknown-thing'), plain.hooks);
    assert.match(textFrom(plain.events), /^echo:\/unknown-thing;/);
  } finally {
    await session.close();
  }
  assert.deepEqual(await new AgyAdapter(['definitely-not-installed-agy']).commands(here), []);
});

test('pi: /compact and /session are answered through its requests, and listed first', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  const commands = await adapter.commands(here);
  assert.deepEqual(commands.slice(0, 2).map((c) => c.name), PI_LOCAL_COMMANDS.map((c) => c.name));
  assert.ok(commands.some((c) => c.name === 'fix-tests'), "pi's own list follows");

  const session = await adapter.start({ cwd: here });
  try {
    const compact = collectingHooks();
    assert.deepEqual(await session.prompt(turn('/compact keep the API notes'), compact.hooks), { stopReason: 'end_turn' });
    assert.deepEqual(compact.events.map((e) => e.type), ['tool.start', 'message.delta', 'usage.updated', 'tool.end']);
    assert.equal(compact.events[1].data.text, '已压缩上下文：150.0k → 约 32.0k tokens。');
    assert.deepEqual(compact.events[2].data, { used: null, size: 200000, cost: { amount: 0.02, currency: 'USD' } });

    const stats = collectingHooks();
    await session.prompt(turn('/session'), stats.hooks);
    assert.match(String(stats.events[0].data.text), /\| 上下文 \| 待下一次响应更新 \/ 200\.0k \|/);

    const failed = await session.prompt(turn('/compact explode'), collectingHooks().hooks);
    assert.deepEqual(failed, { stopReason: 'error', error: 'nothing to compact' });

    // Anything else is a normal prompt.
    const normal = collectingHooks();
    await session.prompt(turn('/compacted is not a command'), normal.hooks);
    assert.ok(normal.events.some((e) => e.type === 'tool.start'));
    assert.deepEqual(normal.events.at(-1)?.data, { used: 1200, size: 200000, cost: { amount: 0.02, currency: 'USD' } });
    const measured = collectingHooks();
    await session.prompt(turn('/session'), measured.hooks);
    assert.match(String(measured.events[0].data.text), /\| 上下文 \| 1\.2k \/ 200\.0k（0\.6%） \|/);
  } finally {
    await session.close();
  }
});

test('formatPiCompact and formatPiStats cope with partial answers', () => {
  assert.equal(formatPiCompact({ tokensBefore: 1500 }), '已压缩上下文（压缩前 1.5k tokens）。');
  assert.equal(formatPiCompact({ tokensBefore: 2_500_000, estimatedTokensAfter: 900 }), '已压缩上下文：2.5M → 约 900 tokens。');
  const table = formatPiStats({});
  assert.match(table, /\| 消息 \| 你 0 · 助手 0 · 工具调用 0 \|/);
  assert.doesNotMatch(table, /上下文|费用|会话 ID/);
});

test('Claude: commands it answers but does not announce are added, once', () => {
  assert.deepEqual(withUnlistedCommands([]), [], 'nothing known yet');
  const listed = [{ name: 'compact', description: '' }, { name: 'stats', description: 'already there' }];
  assert.deepEqual(withUnlistedCommands(listed).map((c) => c.name), ['compact', 'stats', 'goal', 'cost', 'context']);
});

// TRAE CLI speaks ACP too (see acp.ts); these tests cover what is its own. The fake agent takes
// a flavour that answers the way TRAE CLI does: no modes announced, the effort option named
// reasoning_effort, and deletion happening through its CLI rather than the protocol.
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';

const traexEnv = { FAKE_ACP_FLAVOR: 'traex' };

test('TraexAcpAdapter: pictures and follow-ups work when the backend rejects uploaded references', async (t) => {
  const adapter = new TraexAcpAdapter([process.execPath, fakeAcp], { ...traexEnv, FAKE_CODEX_REJECT_LOCAL_IMAGES: '1' });
  for (const resumeId of [undefined, 'existing-image-thread']) {
    const session = await adapter.start({ cwd: here, resumeId });
    t.after(() => session.close());
    const { hooks, events } = collectingHooks();
    const result = await session.prompt(turn('what is this', [{ data: PNG, mimeType: 'image/png', path: '/data/attachments/t/1.png' }]), hooks);
    assert.equal(result.stopReason, 'end_turn');
    assert.equal(result.error, undefined);
    assert.match(textFrom(events), /inline-images:image\/png;/);
    const followUp = collectingHooks();
    assert.equal((await session.prompt(turn('hello again'), followUp.hooks)).stopReason, 'end_turn');
    assert.match(textFrom(followUp.events), /hello again/);
    await session.close();
  }
});


test('TraexAcpAdapter: live settings use the native app-server sandbox, approval and effort fields', async (t) => {
  const adapter = new TraexAcpAdapter([process.execPath, fakeAcp], traexEnv);
  assert.deepEqual(adapter.capabilities.permissionModes, ['auto_edit', 'full_access']);
  assert.deepEqual(adapter.capabilities.effortLevels, ['low', 'medium', 'high', 'xhigh']);
  assert.equal(adapter.resumeCommand('s1'), 'traex resume s1');

  const session = await adapter.start({ cwd: here, permissionMode: 'auto_edit', model: 'big', effort: 'high' });
  t.after(() => session.close());
  const { hooks, events, approvals } = collectingHooks();
  await session.prompt(turn('model?'), hooks);
  assert.equal(events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), 'model:big;effort:high;sandbox:workspace-write;approval:on-request;reviewer:auto_review');
  await session.prompt(turn('approve this'), hooks);
  assert.deepEqual(approvals, [], 'native auto-review handles eligible approvals');
  assert.match(events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), /auto-review:approved/);
  await session.setPermissionMode!('full_access');
  await session.setEffort!('low');
  const second = collectingHooks();
  await session.prompt(turn('model?'), second.hooks);
  assert.equal(second.events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), 'model:big;effort:low;sandbox:danger-full-access;approval:never;reviewer:user');
  await session.close();
});

test('TraexAcpAdapter: the model list is taken as reported, with the current pick marked', async () => {
  const adapter = new TraexAcpAdapter([process.execPath, fakeAcp], traexEnv);
  const models = await adapter.models();
  // Unlike Claude, nothing is probed: every reported model is offered as is, including "broken".
  assert.deepEqual(models.map((m) => m.id), ['default', 'big', 'small', 'broken']);
  assert.deepEqual(
    models.filter((m) => m.isDefault).map((m) => m.id),
    ['default'],
    'the model the agent is on is marked',
  );
  const commands = (await adapter.commands()).map((c) => c.name);
  assert.ok(['goal', 'compact', 'review', 'skills'].every(c => commands.includes(c)), 'commands use the live app-server transport');
});

test('TraexAcpAdapter: sessions are deleted through the CLI, not the protocol', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-traex-del-'));
  const log = join(dir, 'deleted.log');
  // The full form of the command, as the daemon configures it; the adapter strips "acp serve".
  const adapter = new TraexAcpAdapter([process.execPath, fakeAcp, 'acp', 'serve'], { ...traexEnv, FAKE_ACP_DELETE_LOG: log });
  await adapter.deleteSession('sid-123', here);
  assert.equal(readFileSync(log, 'utf8'), 'sid-123\n');
  rmSync(dir, { recursive: true, force: true });
});

test('TraexAcpAdapter: artifactDirs finds the session folder under .trae/cli/sessions', () => {
  const home = mkdtempSync(join(tmpdir(), 'calli-traex-home-'));
  const id = '01a1255b-b666-7820-8ae3-0842196c13b4';
  const artifacts = join(home, 'sessions', '2026', '10', '10', `rollout-2026-10-10T10-28-49-${id}.artifacts`);
  mkdirSync(artifacts, { recursive: true });
  const adapter = new TraexAcpAdapter([process.execPath, fakeAcp], { CALLI_TRAEX_HOME: home });
  assert.deepEqual(adapter.artifactDirs!(id), [artifacts]);
  assert.deepEqual(adapter.artifactDirs!('not an id'), [], 'junk ids are not looked up');
  rmSync(home, { recursive: true, force: true });
});

import { imagePathNote } from '../src/attachments.js';

test('imagePathNote lists the stored files of a message, and nothing for images without a file', () => {
  assert.equal(imagePathNote([]), '');
  assert.equal(imagePathNote([{ data: PNG, mimeType: 'image/png' }]), '', 'an image read from history has no file');
  assert.equal(imagePathNote([{ data: PNG, mimeType: 'image/png', path: '/d/a.png' }]), '\n\nThe user attached an image. Look at it before answering:\n- /d/a.png');
  assert.match(imagePathNote([{ data: PNG, mimeType: 'image/png', path: '/d/a.png' }, { data: PNG, mimeType: 'image/jpeg', path: '/d/b.jpg' }]), /attached 2 images\. Look at them[\s\S]*- \/d\/a\.png\n- \/d\/b\.jpg$/);
});

test('agy gets the pictures of a message as file paths', async () => {
  const adapter = new AgyAdapter([process.execPath, fakeAgy]);
  assert.equal(adapter.capabilities.images, true);
  const session = await adapter.start({ cwd: here });
  try {
    const { hooks, events } = collectingHooks();
    await session.prompt(turn('what is this', [{ data: PNG, mimeType: 'image/png', path: '/data/attachments/t/1.png' }]), hooks);
    assert.match(textFrom(events), /^echo:what is this\n\nThe user attached an image\. Look at it before answering:\n- \/data\/attachments\/t\/1\.png;/);
  } finally {
    await session.close();
  }
});

test('pi: a model that cannot see gets paths for describe_image, when that extension is installed', async () => {
  const plain = new PiRpcAdapter([process.execPath, fakePi]);
  assert.equal((await plain.models()).find((m) => m.id === 'fake/plain')?.imagesVia, undefined, 'no extension, no way in');

  process.env.FAKE_PI_VISION = '1';
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  let models;
  try {
    models = await piModelsWithVision(adapter);
  } finally {
    delete process.env.FAKE_PI_VISION;
  }
  assert.deepEqual(models.map((m) => [m.id, m.images, m.imagesVia]), [
    ['fake/think', true, undefined],
    ['fake/plain', false, 'describe_image'],
    ['fake/loose', false, 'describe_image'],
  ]);

  const image = { data: PNG, mimeType: 'image/png', path: '/data/attachments/t/1.png' };
  const blind = await adapter.start({ cwd: here, model: 'fake/plain' });
  try {
    const { hooks, events } = collectingHooks();
    await blind.prompt(turn('what is this', [image]), hooks);
    const text = textFrom(events);
    assert.doesNotMatch(text, /images:/, 'no image blocks: pi would drop them');
    assert.match(text, /there what is this\n\nThe user attached an image\. Look at it before answering:\n- \/data\/attachments\/t\/1\.png/);
  } finally {
    await blind.close();
  }

  // A model that sees gets the picture itself, as before.
  const seeing = await adapter.start({ cwd: here, model: 'fake/think' });
  try {
    const { hooks, events } = collectingHooks();
    await seeing.prompt(turn('what is this', [image]), hooks);
    assert.match(textFrom(events), /^images:1:image\/png;hi there what is this$/);
  } finally {
    await seeing.close();
  }
});

test('pi: the zai vision MCP server also gives a text-only model sight, when it is connected', async () => {
  const plain = new PiRpcAdapter([process.execPath, fakePi]);
  assert.equal((await plain.models()).find((m) => m.id === 'fake/plain')?.imagesVia, undefined, 'no MCP, no way in');

  process.env.FAKE_PI_MCP = '1';
  const adapter = new PiRpcAdapter([process.execPath, fakePi]);
  let models;
  try {
    models = await piModelsWithVision(adapter);
  } finally {
    delete process.env.FAKE_PI_MCP;
  }
  assert.deepEqual(models.map((m) => [m.id, m.images, m.imagesVia]), [
    ['fake/think', true, undefined],
    ['fake/plain', false, 'analyze_image'],
    ['fake/loose', false, 'analyze_image'],
  ]);

  // The extension, when present too, still wins: it needs no extra key.
  process.env.FAKE_PI_MCP = '1';
  process.env.FAKE_PI_VISION = '1';
  const both = new PiRpcAdapter([process.execPath, fakePi]);
  try {
    models = await piModelsWithVision(both);
  } finally {
    delete process.env.FAKE_PI_MCP;
    delete process.env.FAKE_PI_VISION;
  }
  assert.equal(models.find((m) => m.id === 'fake/plain')?.imagesVia, 'describe_image');

  // And the pictures travel as paths, like with the extension.
  const image = { data: PNG, mimeType: 'image/png', path: '/data/attachments/t/1.png' };
  const blind = await adapter.start({ cwd: here, model: 'fake/plain' });
  try {
    const { hooks, events } = collectingHooks();
    await blind.prompt(turn('what is this', [image]), hooks);
    const text = textFrom(events);
    assert.doesNotMatch(text, /images:/, 'no image blocks: pi would drop them');
    assert.match(text, /The user attached an image\. Look at it before answering:\n- \/data\/attachments\/t\/1\.png/);
  } finally {
    await blind.close();
  }
});

import {
  CODEX_LOCAL_COMMANDS,
  CodexAppServerAdapter,
  codexApprovalDecision,
  codexApprovalOptions,
  codexModelOptions,
  mapCodexItemEnd,
  mapCodexItemStart,
  parseUnifiedDiff,
  turnsFromCodexHistory,
} from '../src/adapters/codex.js';

const fakeCodex = join(here, 'fixtures', 'fake-codex.mjs');

test('parseUnifiedDiff turns a Codex patch into old and new region text', () => {
  // Added file: no old lines at all reads as a new file (oldText null).
  assert.deepEqual(parseUnifiedDiff('@@ -0,0 +1 @@\n+hello\n'), { oldText: null, newText: 'hello' });
  // Deleted file: nothing new remains.
  assert.deepEqual(parseUnifiedDiff('@@ -1 +0,0 @@\n-hello\n\\ No newline at end of file\n'), { oldText: 'hello', newText: '' });
  // Context lines belong to both sides; headers and markers are dropped.
  assert.deepEqual(parseUnifiedDiff('--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n const a = 1;\n-const b = 2;\n+const b = 3;\n'), {
    oldText: 'const a = 1;\nconst b = 2;',
    newText: 'const a = 1;\nconst b = 3;',
  });
  // An added or deleted file is not a patch: it is the file's plain content, told apart by kind.
  assert.deepEqual(parseUnifiedDiff('patched\n', 'add'), { oldText: null, newText: 'patched\n' });
  assert.deepEqual(parseUnifiedDiff('gone\n', 'delete'), { oldText: 'gone\n', newText: '' });
  // Content that happens to look like a patch is still parsed as one.
  assert.deepEqual(parseUnifiedDiff('@@ -1 +1 @@\n-a\n+b\n', 'add'), { oldText: 'a', newText: 'b' });
});

test('mapCodexItemStart/End map commands, file changes and the rest of the step items', () => {
  const start = mapCodexItemStart({ type: 'commandExecution', id: 'e1', command: 'npm test\nci', cwd: '/tmp/x' });
  assert.deepEqual(start, { type: 'tool.start', data: { id: 'e1', title: 'npm test', kind: 'execute', status: 'in_progress', input: { command: 'npm test\nci', cwd: '/tmp/x' } } });

  const failed = mapCodexItemEnd({ type: 'commandExecution', id: 'e1', status: 'completed', exitCode: 1, aggregatedOutput: 'boom' });
  assert.equal(failed?.type, 'tool.end');
  assert.equal(failed?.data.status, 'failed');
  assert.equal(failed?.data.output, 'boom\n(exit 1)');
  // Stdout can look normal before a later command fails; words in the output are not status.
  assert.equal(mapCodexItemEnd({ type: 'commandExecution', id: 'e2', status: 'completed', exitCode: 2, aggregatedOutput: 'file contents\nmatched lines' })?.data.status, 'failed');
  assert.equal(mapCodexItemEnd({ type: 'commandExecution', id: 'e3', status: 'completed', exitCode: 0, aggregatedOutput: '0 errors; no failed checks' })?.data.status, 'completed');

  const edit = mapCodexItemStart({ type: 'fileChange', id: 'f1', changes: [{ path: '/tmp/x/a.ts', diff: '-a\n+b\n', kind: { type: 'update' } }] });
  assert.equal(edit?.data.kind, 'edit');
  assert.deepEqual(edit?.data.content, [{ type: 'diff', path: '/tmp/x/a.ts', oldText: 'a', newText: 'b' }]);
  assert.deepEqual(mapCodexItemEnd({ type: 'fileChange', id: 'f1', status: 'completed', changes: [{ path: '/tmp/x/a.ts', diff: '+x\n' }] })?.data.content, [
    { type: 'diff', path: '/tmp/x/a.ts', oldText: null, newText: 'x' },
  ]);

  assert.equal(mapCodexItemStart({ type: 'userMessage', id: 'u' }), null);
  assert.equal(mapCodexItemStart({ type: 'agentMessage', id: 'm' }), null);
  assert.equal(mapCodexItemEnd({ type: 'agentMessage', id: 'm' }), null);
  assert.deepEqual(mapCodexItemStart({ type: 'contextCompaction', id: 'c' }), { type: 'context.compaction', data: { id: 'c', status: 'in_progress' } });
  assert.deepEqual(mapCodexItemStart({ type: 'webSearch', id: 'w1', query: 'rust async' })?.data, {
    id: 'w1',
    tool: 'web_search',
    title: 'rust async',
    kind: 'search',
    status: 'in_progress',
  });
});

test('codexModelOptions keeps hidden models out and efforts to the known levels', () => {
  const models = codexModelOptions([
    {
      id: 'gpt-big',
      displayName: 'GPT Big',
      description: 'For hard work',
      isDefault: true,
      inputModalities: ['text', 'image'],
      supportedReasoningEfforts: [{ reasoningEffort: 'low' }, { reasoningEffort: 'high' }, { reasoningEffort: 'max' }, { reasoningEffort: 'ultra' }],
    },
    { id: 'gpt-small', displayName: 'GPT Small', inputModalities: ['text'], supportedReasoningEfforts: [{ reasoningEffort: 'low' }] },
    { id: 'gpt-hidden', displayName: 'Hidden', hidden: true },
  ]);
  assert.deepEqual(models, [
    { id: 'gpt-big', label: 'GPT Big', description: 'For hard work', efforts: ['low', 'high', 'max'], images: true, isDefault: true },
    { id: 'gpt-small', label: 'GPT Small', efforts: ['low'], images: false },
  ]);
});

test('codex approval options come from the decisions Codex offers, and map back to one', () => {
  const amendment = { acceptWithExecpolicyAmendment: { execpolicy_amendment: ['rm'] } };
  assert.deepEqual(codexApprovalOptions(['accept', amendment, 'cancel']), [
    { optionId: 'accept', name: '允许这一次', kind: 'allow_once' },
    { optionId: 'acceptWithExecpolicyAmendment', name: '允许，同类命令不再询问', kind: 'allow_always' },
  ]);
  assert.deepEqual(codexApprovalOptions(['accept', 'acceptForSession', 'decline', 'cancel']), [
    { optionId: 'accept', name: '允许这一次', kind: 'allow_once' },
    { optionId: 'acceptForSession', name: '本会话内不再询问', kind: 'allow_always' },
    { optionId: 'decline', name: '拒绝', kind: 'reject_once' },
  ]);
  // No list (an older server): the two basic answers.
  assert.deepEqual(codexApprovalOptions(undefined).map((o) => o.optionId), ['accept']);

  const params = { proposedExecpolicyAmendment: ['rm'] };
  assert.deepEqual(codexApprovalDecision({ optionId: 'accept' }, params), { decision: 'accept' });
  assert.deepEqual(codexApprovalDecision({ optionId: 'acceptWithExecpolicyAmendment' }, params), { decision: { acceptWithExecpolicyAmendment: { execpolicy_amendment: ['rm'] } } });
  assert.deepEqual(codexApprovalDecision({ cancelled: true }, params), { decision: 'cancel' });
  assert.deepEqual(codexApprovalDecision({ optionId: 'decline' }, params), { decision: 'decline' });
});

test('turnsFromCodexHistory reads a conversation back as turns', () => {
  const turns = turnsFromCodexHistory([
    {
      id: 't1',
      status: 'completed',
      items: [
        { type: 'userMessage', id: 'u1', content: [{ type: 'text', text: 'first question' }] },
        { type: 'reasoning', id: 'r1', summary: [{ type: 'summaryText', text: 'let me look' }] },
        { type: 'commandExecution', id: 'e1', command: 'ls', status: 'completed', exitCode: 0, aggregatedOutput: 'a.txt' },
        { type: 'agentMessage', id: 'm1', text: 'first answer' },
      ],
    },
    {
      id: 't2',
      status: 'completed',
      items: [
        { type: 'userMessage', id: 'u2', content: [{ type: 'text', text: 'second' }] },
        { type: 'plan', id: 'p1', text: '- [x] Read\n- [ ] Write\n' },
        { type: 'agentMessage', id: 'm2', text: 'second answer' },
      ],
    },
  ]);
  assert.deepEqual(turns.map((t) => t.text), ['first question', 'second']);
  assert.deepEqual(turns[0].events.map((e) => e.type), ['reasoning.delta', 'tool.start', 'tool.end', 'message.delta']);
  assert.equal(turns[1].events.find((e) => e.type === 'plan.completed')?.data.text, '- [x] Read\n- [ ] Write\n');
});

test('CodexAppServerAdapter: prompt streams steps and text, then ends the turn', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  assert.equal(await adapter.available(), true);
  const session = await adapter.start({ cwd: here });
  assert.equal(session.agentSessionId, 'codex-thread-1');

  const { hooks, events } = collectingHooks();
  const result = await session.prompt(turn('tool and reason please'), hooks);
  assert.deepEqual(result, { stopReason: 'end_turn' });
  assert.deepEqual(
    events.map((e) => e.type),
    ['tool.start', 'tool.update', 'tool.update', 'tool.end', 'reasoning.delta', 'reasoning.delta', ...Array(7).fill('message.delta'), 'usage.sample', 'usage.updated'],
  );
  // Streamed command output accumulates, so an update carries the whole text so far.
  assert.deepEqual(events.find((e) => e.type === 'tool.update')?.data, { id: 'exec-1', status: 'in_progress', output: 'par' });
  assert.equal(textFrom(events), 'echo:tool and reason please');
  assert.deepEqual(events.at(-1)?.data, { used: 500, size: 100000 });
  await session.close();
});

test('CodexAppServerAdapter: a turn cut off by the server dying reports whose server it was', async () => {
  // TRAE CLI runs the same protocol through this adapter; its threads must not blame codex.
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex], { FAKE_CODEX_TURN_DELAY: '500' }, 'TRAE CLI');
  const session = await adapter.start({ cwd: here });
  const { hooks } = collectingHooks();
  const result = session.prompt(turn('slow please'), hooks);
  await new Promise((r) => setTimeout(r, 50)); // the turn is in flight before the server dies
  await session.close();
  assert.deepEqual(await result, { stopReason: 'error', error: 'TRAE CLI app-server exited' });
});

test('CodexAppServerAdapter: context uses the latest request and shrinks after compaction', async (t) => {
  const session = await new CodexAppServerAdapter([process.execPath, fakeCodex]).start({ cwd: here });
  t.after(() => session.close());
  for (const [text, expected] of [
    ['usage', { used: 183510, size: 258400 }],
    ['usage compacted', { used: 12000, size: 258400 }],
    ['usage missing', undefined],
  ] as const) {
    const { hooks, events } = collectingHooks();
    await session.prompt(turn(text), hooks);
    assert.deepEqual(events.find((e) => e.type === 'usage.updated')?.data, expected);
  }
});

test('CodexAppServerAdapter: file changes stream their patch and carry diffs', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here });
  const { hooks, events } = collectingHooks();
  await session.prompt(turn('edit it'), hooks);
  const types = events.map((e) => e.type);
  assert.deepEqual(types, ['tool.start', 'tool.update', 'tool.end', ...Array(3).fill('message.delta'), 'usage.sample', 'usage.updated']);
  // The patch grew while it ran: the update carries both files, the end keeps them.
  const update = events.find((e) => e.type === 'tool.update');
  assert.equal((update?.data.content as any[]).length, 2);
  assert.deepEqual((update?.data.content as any[])[1], { type: 'diff', path: '/tmp/one/src/new.ts', oldText: null, newText: 'export const x = 1;\n' });
  const end = events.find((e) => e.type === 'tool.end');
  assert.equal(end?.data.status, 'completed');
  assert.equal((end?.data.content as any[]).length, 2);
  await session.close();
});

test('CodexAppServerAdapter: an approval goes to the hook and its answer is sent back', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here });
  const { hooks, events, approvals } = collectingHooks(() => ({ optionId: 'accept' }));
  const result = await session.prompt(turn('approve this'), hooks);
  assert.equal(result.stopReason, 'end_turn');
  assert.deepEqual(approvals, ['运行命令']);
  assert.match(textFrom(events), /approved:accept/);
  await session.close();
});

test('CodexAppServerAdapter: declining keeps the turn going, cancelling interrupts it', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here });
  const denied = collectingHooks(() => ({ optionId: 'decline' }));
  const deniedResult = await session.prompt(turn('deny this'), denied.hooks);
  assert.equal(deniedResult.stopReason, 'end_turn');
  assert.match(textFrom(denied.events), /denied/);

  const cancelled = collectingHooks(() => ({ cancelled: true }));
  const cancelledResult = await session.prompt(turn('approve again'), cancelled.hooks);
  assert.equal(cancelledResult.stopReason, 'cancelled');
  await session.close();
});

test('CodexAppServerAdapter: a failed command ends as failed, an interrupted one as cancelled', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here });
  const failing = collectingHooks();
  await session.prompt(turn('fail it'), failing.hooks);
  const failed = failing.events.find((e) => e.type === 'tool.end');
  assert.equal(failed?.data.status, 'failed');
  assert.equal(failed?.data.output, 'boom\n(exit 1)');

  const { hooks } = collectingHooks();
  const pending = session.prompt(turn('slow one'), hooks);
  await new Promise((r) => setTimeout(r, 60));
  await session.cancel();
  assert.equal((await pending).stopReason, 'cancelled');
  await session.close();
});

test('CodexAppServerAdapter: a server that dies mid-turn ends it as an error', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here });
  const { hooks } = collectingHooks();
  const result = await session.prompt(turn('crash now'), hooks);
  assert.equal(result.stopReason, 'error');
  assert.match(result.error ?? '', /exited|codex/);
});

test('CodexAppServerAdapter: permission level, model and effort ride with the turns', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here, permissionMode: 'auto_edit', model: 'gpt-big', effort: 'low' });
  const { hooks, events, approvals } = collectingHooks();
  await session.prompt(turn('model?'), hooks);
  assert.equal(textFrom(events), 'model:gpt-big;effort:low;sandbox:workspace-write;approval:on-request;reviewer:auto_review');
  await session.prompt(turn('approve this'), hooks);
  assert.deepEqual(approvals, [], 'native auto-review handles eligible approvals');
  assert.match(textFrom(events), /auto-review:approved/);

  // Changing them on the live session applies from the next turn, without a restart.
  await session.setPermissionMode!('full_access');
  assert.equal(await session.setModel!('gpt-small'), true);
  assert.equal(await session.setEffort!('high'), true);
  const second = collectingHooks();
  await session.prompt(turn('model?'), second.hooks);
  assert.equal(textFrom(second.events), 'model:gpt-small;effort:high;sandbox:danger-full-access;approval:never;reviewer:user');
  // full_access answers nothing: the approval that would have been asked is skipped.
  const third = collectingHooks();
  await session.prompt(turn('approve this'), third.hooks);
  assert.deepEqual(third.approvals, []);
  await session.close();
});

test('CodexAppServerAdapter: native auto-review is selected when threads start or resume', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-codex-auto-review-'));
  const log = join(dir, 'rpc.jsonl');
  try {
    const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex], { FAKE_CODEX_RPC_LOG: log });
    const created = await adapter.start({ cwd: here, permissionMode: 'auto_edit' });
    await created.close();
    const resumed = await adapter.start({ cwd: here, resumeId: 'previous-thread', permissionMode: 'auto_edit' });
    await resumed.close();

    const calls = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    const started = calls.find((call) => call.method === 'thread/start');
    const resumedCall = calls.find((call) => call.method === 'thread/resume');
    assert.equal(started?.params.approvalsReviewer, 'auto_review');
    assert.equal(resumedCall?.params.approvalsReviewer, 'auto_review');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('CodexAppServerAdapter: changing permission during a turn updates its native reviewer', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-codex-live-auto-review-'));
  const log = join(dir, 'rpc.jsonl');
  try {
    const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex], {
      FAKE_CODEX_RPC_LOG: log,
      FAKE_CODEX_TURN_DELAY: '80',
    });
    const session = await adapter.start({ cwd: here, permissionMode: 'ask' });
    const live = collectingHooks();
    const pending = session.prompt(turn('approve after switching'), live.hooks);
    await new Promise((resolve) => setTimeout(resolve, 30));
    await session.setPermissionMode!('auto_edit');

    assert.equal((await pending).stopReason, 'end_turn');
    assert.deepEqual(live.approvals, [], 'the active turn switches to the native reviewer');
    assert.match(textFrom(live.events), /auto-review:approved/);

    const calls = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    const update = calls.find((call) => call.method === 'turn/settings/update');
    assert.deepEqual(update?.params, {
      threadId: 'codex-thread-1',
      turnId: 'turn-1',
      approvalsReviewer: 'auto_review',
    });

    const next = collectingHooks();
    await session.prompt(turn('model?'), next.hooks);
    assert.equal(textFrom(next.events), 'model:gpt-big;effort:high;sandbox:workspace-write;approval:on-request;reviewer:auto_review');
    await session.close();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('CodexAppServerAdapter: resume keeps the thread id, /compact is answered locally', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here, resumeId: 'previous-thread' });
  assert.equal(session.agentSessionId, 'previous-thread');

  const { hooks, events } = collectingHooks();
  const result = await session.prompt(turn('/compact'), hooks);
  assert.deepEqual(result, { stopReason: 'end_turn' });
  assert.equal(textFrom(events), '上下文压缩完成。');
  await session.close();
});

test('CodexAppServerAdapter: models come from the agent, commands are the local ones', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const models = await adapter.models();
  assert.deepEqual(models.map((m) => [m.id, m.label, m.images]), [
    ['gpt-big', 'GPT Big', true],
    ['gpt-small', 'GPT Small', false],
  ]);
  assert.deepEqual(models[0].efforts, ['low', 'high', 'max']);
  assert.equal(await adapter.models(), models, 'the probe runs once and is cached');
  assert.deepEqual(await adapter.commands(), CODEX_LOCAL_COMMANDS);
  assert.deepEqual(CODEX_LOCAL_COMMANDS.map((c) => c.name), ['goal', 'compact', 'review', 'skills', 'mcp', 'usage']);
});

test('CodexAppServerAdapter: listing, history, forking and deleting go through one-off servers', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  // Three sessions, paged two at a time.
  const sessions = await adapter.listSessions();
  assert.deepEqual(sessions.map((s) => [s.id, s.title]), [
    ['codex-thread-1', 'Named chat'],
    ['codex-thread-2', 'How do I…'],
    ['codex-thread-3', 'Third'],
  ]);
  assert.equal(sessions[0].updatedAt, new Date(1791648439 * 1000).toISOString());

  const history = await adapter.loadHistory('hist-thread', here);
  assert.deepEqual(history.map((t) => t.text), ['first question', 'second question']);
  assert.deepEqual(history[0].events.map((e) => e.type), ['reasoning.delta', 'tool.start', 'tool.end', 'tool.start', 'tool.end', 'message.delta']);
  assert.equal(textFrom(history[0].events), 'first answer');
  const historyDiff = history[0].events.flatMap((e) => (e.type === 'tool.end' ? (e.data.content as any[]) ?? [] : []));
  assert.deepEqual(historyDiff, [{ type: 'diff', path: '/tmp/one/notes.md', oldText: 'a', newText: 'b' }]);

  assert.equal(await adapter.forkSession('codex-thread-1'), 'codex-thread-1-fork');
  // Codex forks through a turn it is given the id of.
  assert.deepEqual(history.map((t) => t.forkRef), ['turn-1', 'turn-2']);
  assert.equal(await adapter.forkSession('hist-thread', here, history[0].forkRef), 'hist-thread-fork-at-turn-1');
  await assert.rejects(adapter.forkSession('hist-thread', here, 'turn-9'), /unknown turn/);

  const log = join(tmpdir(), `fake-codex-delete-${process.pid}.log`);
  process.env.FAKE_CODEX_DELETE_LOG = log;
  try {
    await adapter.deleteSession('codex-thread-1');
    assert.equal(readFileSync(log, 'utf8').trim(), 'codex-thread-1');
  } finally {
    delete process.env.FAKE_CODEX_DELETE_LOG;
    rmSync(log, { force: true });
  }
});

test('CodexAppServerAdapter: index-only listing shares requests and keeps sessions beyond page 50', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-codex-index-'));
  const file = join(dir, 'sessions.json');
  const log = join(dir, 'rpc.jsonl');
  try {
    writeFileSync(file, JSON.stringify(Array.from({ length: 5101 }, (_, i) => ({
      id: `indexed-${i}`, cwd: '/tmp/project', name: `Session ${i}`, updatedAt: 1791648439,
    }))));
    const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex], {
      FAKE_CODEX_SESSIONS_FILE: file, FAKE_CODEX_RPC_LOG: log,
    });
    const [first, concurrent] = await Promise.all([adapter.listSessions(), adapter.listSessions()]);
    assert.equal(first, concurrent, 'concurrent callers share the probe');
    assert.equal(first.length, 5101);
    assert.equal(first.at(-1)?.title, 'Session 5100');
    const calls = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    assert.equal(calls.filter((c) => c.method === 'initialize').length, 1);
    assert.equal(calls.filter((c) => c.method === 'thread/list').length, 52);
    assert.ok(calls.filter((c) => c.method === 'thread/list').every((c) => c.params.useStateDbOnly && c.params.sortKey === 'updated_at'));
    assert.ok(!calls.some((c) => c.method === 'thread/read' || c.method === 'thread/turns/list'));
    writeFileSync(file, '[]');
    assert.deepEqual(await adapter.listSessions(), [], 'a later request sees native changes');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('CodexAppServerAdapter: a broken cursor fails instead of silently truncating the list', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex], { FAKE_CODEX_REPEAT_CURSOR: '1' });
  await assert.rejects(adapter.listSessions(), /repeated session cursor/);
  await assert.rejects(adapter.listSessions(), /repeated session cursor/, 'failed probes can be retried');
});

test('CodexAppServerAdapter: one session can be read without listing or hydrating history', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  assert.equal((await adapter.getSession('codex-thread-1'))?.title, 'Named chat');
  assert.equal(await adapter.getSession('missing-thread'), null);
});

test('CodexAppServerAdapter: pictures go as file paths', async () => {
  const adapter = new CodexAppServerAdapter([process.execPath, fakeCodex]);
  const session = await adapter.start({ cwd: here });
  const image = { data: PNG, mimeType: 'image/png', path: '/data/attachments/t/1.png' };
  const { hooks, events } = collectingHooks();
  await session.prompt(turn('what is this', [image]), hooks);
  assert.match(textFrom(events), /images:\/data\/attachments\/t\/1\.png/);
  await session.close();
});
