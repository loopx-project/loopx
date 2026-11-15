import { ZcodeWire } from '../src/adapters/zcode-wire.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ZcodeAdapter, ZCODE_BASIC_TOOLS, zcodeContextUsage, zcodeModels } from '../src/adapters/zcode.js';
import type { EmittedEvent } from '../src/events.js';
import type { TurnHooks } from '../src/adapters/types.js';
const here = dirname(fileURLToPath(import.meta.url));
const command = [process.execPath, join(here, 'fixtures/fake-zcode.mjs')];
function setup(env: Record<string, string> = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-zcode-'));
  const log = join(dir, 'wire.jsonl');
  // The empty data dir has no plugin store, keeping these baseline tests free of any
  // machine-local MCP plugins.
  return { dir, adapter: new ZcodeAdapter(command, { ZCODE_DATA_BASE_DIR: dir, ...env, FAKE_ZCODE_LOG: log }), log: () => readFileSync(log, 'utf8').trim().split('\n').map((s) => JSON.parse(s)), clean: () => rmSync(dir, { recursive: true, force: true }) };
}
const hooks = (events: EmittedEvent[]): TurnHooks => ({ emit: (event) => events.push(event), requestApproval: async () => ({ cancelled: true }) });
const input = (text: string) => ({ text, images: [] });

test('zcode reports native model image support without guessing missing capabilities', () => {
  const available = [true, false, undefined].map((supportsImage, i) => ({ ref: { providerId: 'fixture', modelId: String(i) }, properties: { inputFormat: { supportsImage } } }));
  assert.deepEqual(zcodeModels({ settings: { model: { available } } }).map(m => m.images), [true, false, undefined]);
});

test('zcode sends local and inline image attachments, including image-only turns; commands never discard images', async () => {
  const f = setup();
  const s = await f.adapter.start({ cwd: f.dir });
  try {
    assert.equal(f.adapter.capabilities.images, true);
    const models = await f.adapter.models();
    assert.deepEqual(models.map(m => m.images), [true, false]);
    const data = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
    const path = join(f.dir, 'picture.png');
    writeFileSync(path, Buffer.from(data, 'base64'));
    const images = [{ data, mimeType: 'image/png', path }, { data, mimeType: 'image/png' }];
    for (const text of ['Describe these images.', '']) {
      const events: EmittedEvent[] = [];
      assert.equal((await s.prompt({ text, images }, hooks(events))).stopReason, 'end_turn');
      assert.ok(events.some(e => e.type === 'message.delta' && e.data.text === '已读取 2 张图片'));
      const sent = f.log().filter(r => r.method === 'session/send').at(-1).params;
      assert.equal(sent.content, text);
      assert.deepEqual(sent.attachments, [
        { kind: 'image', filename: 'picture.png', mimeType: 'image/png', localPath: path },
        { kind: 'image', filename: 'image-2.png', mimeType: 'image/png', dataBase64: data },
      ]);
    }
    for (const text of ['/compact', '/goal investigate', '/help', '/session', '/usage']) {
      await assert.rejects(s.prompt({ text, images }, hooks([])), /不接受图片/);
    }
    assert.equal(f.log().filter(r => ['session/send', 'session/goal', 'session/compact'].includes(r.method)).length, 2);
  } finally { await s.close(); f.clean(); }
});

test('zcode context maps only native current usage and validates optional cost', () => {
  const snapshot = (used: unknown, size: unknown, cost: unknown = null) => ({ runtime: { contextUsage: { used, size, cost } }, projection: { totalTokenCount: 900000 } });
  assert.deepEqual(zcodeContextUsage(snapshot(12000, 128000)), { type: 'usage.updated', data: { used: 12000, size: 128000 } });
  assert.equal(zcodeContextUsage({ totalTokens: 900000, projection: { totalTokenCount: 900000 } }), null);
  for (const [used, size] of [[-1, 128000], [12, 0], [12, Infinity], [NaN, 128000], ['12', 128000], [null, 128000]]) assert.equal(zcodeContextUsage(snapshot(used, size)), null);
  assert.equal(zcodeContextUsage(snapshot(0, 128000))?.data.used, 0);
  assert.deepEqual(zcodeContextUsage(snapshot(12, 128000, { amount: 0.2, currency: 'USD' }))?.data.cost, { amount: 0.2, currency: 'USD' });
  assert.equal(zcodeContextUsage(snapshot(12, 128000, { amount: NaN, currency: 'USD' }))?.data.cost, undefined);
});

test('zcode updates context after replies and compaction and restores usage on resume', async () => {
  const f = setup({ FAKE_ZCODE_CONTEXT: '1' });
  let s = await f.adapter.start({ cwd: f.dir });
  try {
    const events: EmittedEvent[] = [];
    await s.prompt(input('hello'), hooks(events));
    assert.deepEqual(events.at(-1), { type: 'usage.updated', data: { used: 48000, size: 128000 } });
    await s.prompt(input('/compact'), hooks(events));
    assert.deepEqual(events.filter(e => e.type === 'usage.updated').at(-1)?.data, { used: 8000, size: 128000 });
    assert.equal(events.at(-1)?.type, 'tool.end');
    assert.equal(events.at(-1)?.data.status, 'completed');
    const id = s.agentSessionId;
    await s.close();
    s = await f.adapter.start({ cwd: f.dir, resumeId: id });
    const resumed: EmittedEvent[] = [];
    await s.prompt(input('/usage'), hooks(resumed));
    assert.deepEqual(resumed[0]?.data, { used: 12000, size: 128000 });
    await s.setModel!(JSON.stringify(['api', 'large']));
    const changed: EmittedEvent[] = [];
    await s.prompt(input('hello'), hooks(changed));
    assert.equal(changed[0]?.data.size, 256000);
    assert.equal(changed.at(-1)?.data.size, 256000);
  } finally { await s.close(); f.clean(); }
});

test('zcode clears stale usage when compaction has no new measurement; optional statistics failure does not fail replies', async () => {
  for (const env of [{ FAKE_ZCODE_COMPACT_USAGE_UNKNOWN: '1' }, { FAKE_ZCODE_USAGE_READ_FAIL: '1' }]) {
    const f = setup({ FAKE_ZCODE_CONTEXT: '1', FAKE_ZCODE_INITIAL_CONTEXT: '48000', ...env });
    const s = await f.adapter.start({ cwd: f.dir });
    try {
      const events: EmittedEvent[] = [];
      assert.equal((await s.prompt(input('hello'), hooks(events))).stopReason, 'end_turn');
      assert.equal((await s.prompt(input('/compact'), hooks(events))).stopReason, 'end_turn');
      assert.deepEqual(events.filter(e => e.type === 'usage.updated').at(-1)?.data, { used: null, size: 128000 });
    } finally { await s.close(); f.clean(); }
  }
});

test('zcode without registered models hides fallback efforts and explains account support before setting effort', async () => {
  const f = setup({ FAKE_ZCODE_NO_MODELS: '1' });
  try {
    assert.deepEqual(await f.adapter.models(), []);
    assert.deepEqual(f.adapter.capabilities.effortLevels, []);
    for (const resumeId of [undefined, 'existing-account-session']) {
      await assert.rejects(f.adapter.start({ cwd: f.dir, resumeId, effort: 'high' }), /没有可用模型.*Coding Plan/);
    }
    assert.equal(f.log().some(r => r.method === 'session/setThoughtLevel' || r.method === 'session/send'), false);
    assert.equal(f.log().filter(r => r.method === 'session/close').length, 3);
  } finally { f.clean(); }
});

test('zcode creation and resume send restrictive protocol preferences without changing config', async () => {
  const f = setup();
  try {
    for (const resumeId of [undefined, 'sess-resumed']) {
      const s = await f.adapter.start({ cwd: f.dir, resumeId, permissionMode: 'ask' });
      assert.equal(s.agentSessionId, resumeId ?? 'sess-zcode-fixture');
      await s.close();
    }
    const logs = f.log();
    const creates = logs.filter((r) => ['session/create', 'session/resume'].includes(r.method));
    assert.equal(creates.length, 2);
    for (const r of creates) {
      assert.deepEqual(r.params.toolAllowlist, ZCODE_BASIC_TOOLS);
      assert.deepEqual(r.params.mcpServers, []);
      assert.equal(r.params.dynamicWorkflowEnabled, false);
      assert.equal(r.params.offPeakToolEnabled, false);
    }
    assert.equal(creates[0].params.titleGenerationEnabled, false);
    const prefs = logs.filter((r) => r.preferences);
    assert.equal(prefs.length, 2);
    for (const p of prefs) assert.deepEqual(p.preferences, { memoryEnabled: false, nativeSearchEnhancementsEnabled: false, askUserQuestionAutoResolutionEnabled: false });
  } finally { f.clean(); }
});

test('zcode refuses older servers that do not request memory preferences', async () => {
  for (const env of [{ FAKE_ZCODE_NO_PREFERENCES: '1' }]) {
    const f = setup(env);
    try { await assert.rejects(f.adapter.start({ cwd: f.dir }), /记忆|目标/); } finally { f.clean(); }
  }
});

test('zcode can reopen a session with a native goal without starting another loop', async () => {
  const f = setup({ FAKE_ZCODE_GOAL: '1' });
  try {
    const s = await f.adapter.start({ cwd: f.dir, resumeId: 'sess-goal' });
    assert.equal((await s.prompt(input('/compact'), hooks([]))).stopReason, 'end_turn');
    await s.close();
    assert.equal(f.log().some(r => r.method === 'session/send' || r.method === 'session/goal'), false);
  } finally { f.clean(); }
});

test('zcode probes models with deferred persistence and closes the probe', async () => {
  const f = setup();
  try {
    const models = await f.adapter.models();
    assert.equal(models.length, 2);
    assert.equal(models[0].isDefault, true);
    assert.deepEqual(models[0].efforts, ['low', 'high']);
    assert.equal(f.log().find((r) => r.method === 'session/create').params.persistence, 'deferred');
    assert.equal(f.log().find((r) => r.method === 'session/close').params.expectedPersistence, 'deferred');
  } finally { f.clean(); }
});

test('zcode streams text, thinking and tools once, with early completion and repeated turns', async () => {
  const f = setup(); const s = await f.adapter.start({ cwd: f.dir });
  try {
    for (const text of ['tool', 'early', 'normal']) {
      const events: EmittedEvent[] = [];
      const result = await s.prompt(input(text), hooks(events));
      assert.equal(result.stopReason, 'end_turn');
      assert.equal(events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join(''), text === 'early' ? '提前完成' : '你好');
      if (text === 'tool') {
        assert.equal(events.find((e) => e.type === 'tool.start')?.data.title, 'README.md');
        assert.equal(events.find((e) => e.type === 'tool.end')?.data.output, 'hello');
      }
    }
  } finally { await s.close(); f.clean(); }
});

test('zcode approvals use only offered responses and reject unknown option ids', async () => {
  const f = setup(); const s = await f.adapter.start({ cwd: f.dir });
  try {
    for (const optionId of ['once', 'invented']) {
      const events: EmittedEvent[] = [];
      await s.prompt(input('approve'), { ...hooks(events), requestApproval: async (request) => { assert.equal(request.options[0].optionId, 'once'); return { optionId }; } });
      assert.equal(events.filter(e => e.type === 'message.delta').at(-1)?.data.text, optionId === 'once' ? '已批准' : '已拒绝');
    }
  } finally { await s.close(); f.clean(); }
});

test('zcode supports cancellation, compact and rejects custom commands before sending', async () => {
  const f = setup(); const s = await f.adapter.start({ cwd: f.dir });
  try {
    const events: EmittedEvent[] = [];
    const running = s.prompt(input('slow'), hooks(events));
    await s.cancel();
    assert.equal((await running).stopReason, 'cancelled');
    assert.equal((await s.prompt(input('/compact keep details'), hooks(events))).stopReason, 'end_turn');
    assert.ok(events.some((e) => e.data.text === '已压缩'));
    assert.equal((await s.prompt(input('/compact already-running'), hooks(events))).stopReason, 'error');
    await assert.rejects(s.prompt(input('/expert solve'), hooks(events)), /不执行/);
    assert.ok(!f.log().some((r) => r.params?.content === '/expert solve'));
  } finally { await s.close(); f.clean(); }
});

test('zcode reports model failures, rejected submissions and process exit', async () => {
  const f = setup(); const s = await f.adapter.start({ cwd: f.dir });
  try {
    const result = await s.prompt(input('fail'), hooks([]));
    assert.equal(result.error, '模型请求失败');
    await assert.rejects(s.prompt(input('reject'), hooks([])), /submission rejected/);
    await assert.rejects(s.prompt(input('crash'), hooks([])), /已退出/);
  } finally { await s.close(); f.clean(); }
});

test('zcode model and effort changes stay session scoped', async () => {
  const f = setup(); const s = await f.adapter.start({ cwd: f.dir });
  try {
    await s.setModel!(JSON.stringify(['api', 'large']));
    await s.setEffort!('high');
    for (const r of f.log().filter((r) => ['session/setModel', 'session/setThoughtLevel'].includes(r.method))) assert.equal(r.params.persistAsWorkspaceLastUsed, false);
    await assert.rejects(s.setModel!('invalid'), /无效/);
  } finally { await s.close(); f.clean(); }
});


test('zcode sends the selected model default reasoning level at creation', async () => {
  const f = setup();
  try {
    const [model] = await f.adapter.models();
    const s = await f.adapter.start({ cwd: f.dir, model: model.id });
    await s.close();
    const created = f.log().filter((r) => r.method === 'session/create').at(-1);
    assert.equal(created.params.thoughtLevel, 'low');
    assert.equal(created.params.model.options.reasoningLevel, 'low');
    assert.deepEqual(f.adapter.capabilities.permissionModes, []);
  } finally { f.clean(); }
});


test('zcode exposes and handles local query commands without sending prompts', async () => {
  const f = setup(); const s = await f.adapter.start({ cwd: f.dir });
  try {
    assert.deepEqual((await f.adapter.commands()).map((c) => c.name), ['goal', 'compact', 'help', 'session', 'usage']);
    for (const [command, expected] of [['/help', '/compact'], ['/session', 'api / small'], ['  /usage  ', '1,500 tokens']]) {
      const events: EmittedEvent[] = [];
      assert.equal((await s.prompt(input(command), hooks(events))).stopReason, 'end_turn');
      assert.ok(events.some((e) => e.type === 'message.delta' && String(e.data.text).includes(expected)));
    }
    await assert.rejects(s.prompt(input('/usage extra'), hooks([])), /不接受参数/);
    await assert.rejects(s.prompt(input('/workflow'), hooks([])), /不执行/);
    assert.equal(f.log().some((r) => r.method === 'session/send'), false);
    assert.equal(f.log().find((r) => r.method === 'session/usage').params.sessionId, s.agentSessionId);
    assert.equal(f.adapter.capabilities.quota, true);
  } finally { await s.close(); f.clean(); }
});


test('an optional statistics timeout leaves the native connection usable', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-zcode-optional-'));
  const wire = new ZcodeWire(command, dir, { ...process.env, FAKE_ZCODE_USAGE_HANG: '1' });
  try {
    await assert.rejects(wire.request('session/usage', {}, 30, false), /超时/);
    const snapshot = await wire.request('session/read', { sessionId: 'sess-zcode-fixture' });
    assert.equal(snapshot.session.sessionId, 'sess-zcode-fixture');
  } finally { wire.close(); rmSync(dir, { recursive: true, force: true }); }
});
