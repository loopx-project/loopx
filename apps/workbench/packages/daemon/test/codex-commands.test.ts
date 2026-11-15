import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CODEX_LOCAL_COMMANDS, CodexAppServerAdapter, codexQuota, turnsFromCodexHistory } from '../src/adapters/codex.js';
import type { EmittedEvent } from '../src/events.js';

const here = dirname(fileURLToPath(import.meta.url));
const command = [process.execPath, join(here, 'fixtures/fake-codex.mjs')];

test('quota maps old and multi-bucket responses without double counting or inventing missing windows', () => {
  const primary = { usedPercent: 25, windowDurationMins: 300, resetsAt: 1791800000 };
  const legacy = { primary, secondary: { usedPercent: 80, windowDurationMins: 10080 } };
  assert.deepEqual(codexQuota({ rateLimits: legacy }).windows, [
    { label: '5H', usedPercent: 25, resetsAt: new Date(primary.resetsAt * 1000).toISOString() },
    { label: '7D', usedPercent: 80 },
  ]);
  const multi = codexQuota({ rateLimits: legacy, rateLimitsByLimitId: {
    codex: legacy,
    review: { limitName: '审查', primary: { usedPercent: 125, windowDurationMins: 15, resetsAt: Infinity } },
  } });
  assert.equal(multi.windows.length, 3);
  assert.deepEqual(multi.windows[2], { label: '15M', scope: '审查', usedPercent: 100 });
  assert.deepEqual(codexQuota({ rateLimits: { primary: { usedPercent: NaN }, secondary: null } }).windows, []);
  assert.deepEqual(codexQuota({ rateLimitsByLimitId: {}, rateLimits: legacy }).windows, codexQuota({ rateLimits: legacy }).windows);
  assert.deepEqual(codexQuota({ rateLimits: { primary: { usedPercent: 0, windowDurationMins: null, resetsAt: null } } }).windows, [{ label: '主要', usedPercent: 0 }]);
  assert.deepEqual(codexQuota({}).windows, []);
});

test('quota and project command probes use official RPCs without opening a thread', async () => {
  const cwd = mkdtempSync(join(tmpdir(), 'calli-codex-probe-'));
  const log = join(cwd, 'rpc.jsonl');
  try {
    const adapter = new CodexAppServerAdapter(command, { FAKE_CODEX_RPC_LOG: log });
    assert.equal(adapter.capabilities.quota, true);
    assert.equal((await adapter.fetchQuota()).windows.length, 3);
    const commands = await adapter.commands(cwd);
    assert.deepEqual(commands.filter((c) => c.kind === 'skill').map((c) => c.name), ['inspect']);
    assert.equal(commands.filter((c) => c.name === 'review').length, 1);
    const requests = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    assert.equal(requests.some((r) => r.method.startsWith('thread/') || r.method === 'turn/start'), false);
    assert.deepEqual(requests.find((r) => r.method === 'skills/list').params.cwds, [cwd]);
    const older = new CodexAppServerAdapter(command, { FAKE_CODEX_NO_SKILLS: '1' });
    assert.deepEqual(await older.commands(cwd), CODEX_LOCAL_COMMANDS);
    const signedOut = new CodexAppServerAdapter(command, { FAKE_CODEX_QUOTA_ERROR: '1' });
    await assert.rejects(signedOut.fetchQuota(), /authentication required/);
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
});

test('local queries, paginated MCP, reviews and explicit skills route correctly and leave the session usable', async () => {
  const session = await new CodexAppServerAdapter(command).start({ cwd: here });
  const send = async (text: string) => {
    const events: EmittedEvent[] = [];
    const result = await session.prompt({ text, images: [] }, {
      emit: (event) => events.push(event), requestApproval: async () => ({ cancelled: true }),
    });
    return { result, text: events.filter((e) => e.type === 'message.delta').map((e) => (e.data as { text: string }).text).join('') };
  };
  try {
    assert.match((await send('/usage')).text, /5H：已用 25%/);
    const mcp = await send('/mcp');
    assert.equal(mcp.text, '- repo：1 个工具 · 已登录\n- docs：0 个工具 · 不支持认证');
    assert.match((await send('/skills')).text, /\/inspect：检查项目/);
    const review = await send('/review');
    assert.equal(review.result.stopReason, 'end_turn');
    assert.equal(review.text, '审查完成：uncommittedChanges');
    assert.equal((await send('/review 检查错误处理')).text, '审查完成：检查错误处理');
    const skill = await send('/inspect 检查测试');
    assert.ok(skill.text.includes(`skills:inspect@${here}/.agents/skills/inspect/SKILL.md;`));
    assert.match(skill.text, /\$inspect 检查测试/);
    assert.equal((await send('/disabled')).result.stopReason, 'error');
    assert.equal((await send('/compact extra')).result.stopReason, 'error');
    assert.equal((await send('/compact-other')).result.stopReason, 'error');
    assert.equal((await send('/compact')).text, '上下文压缩完成。');
    assert.equal((await send('hello')).result.stopReason, 'end_turn');
  } finally {
    await session.close();
  }
});

test('review results survive importing history', () => {
  const history = turnsFromCodexHistory([{ items: [{ type: 'exitedReviewMode', review: '审查结论' }] }]);
  assert.deepEqual(history[0].events, [{ type: 'message.delta', data: { text: '审查结论' } }]);
});

test('weekly-only quota reports the missing upstream window without fabricating 5-hour usage', async () => {
  const weekly = await new CodexAppServerAdapter(command, { FAKE_CODEX_WEEKLY_ONLY: '1' }).fetchQuota();
  assert.deepEqual(weekly.windows.map((window) => window.label), ['7D']);
  assert.match(weekly.note ?? '', /服务端未提供 5 小时窗口/);
  const full = await new CodexAppServerAdapter(command).fetchQuota();
  assert.ok(full.windows.some((window) => window.label === '5H'));
  assert.equal(full.note, undefined);
});

test('a model with no reasoning levels does not inherit the agent-wide slider choices', async () => {
  const models = await new CodexAppServerAdapter(command, { FAKE_CODEX_NO_EFFORT_MODEL: '1' }).models();
  assert.deepEqual(models.find((model) => model.id === 'gpt-basic')?.efforts, []);
});


test('compact waits beyond acknowledgement and item completion, then releases the session', async () => {
  for (const early of ['', '1']) {
    const session = await new CodexAppServerAdapter(command, { FAKE_CODEX_COMPACT_DELAY: '100', FAKE_CODEX_COMPACT_EARLY: early }).start({ cwd: here });
    const events: EmittedEvent[] = [];
    const hooks = { emit: (event: EmittedEvent) => events.push(event), requestApproval: async () => ({ cancelled: true as const }) };
    try {
      let settled = false;
      const pending = session.prompt({ text: '/compact', images: [] }, hooks).then((result) => { settled = true; return result; });
      await new Promise((resolve) => setTimeout(resolve, early ? 150 : 250));
      assert.equal(settled, false, 'acknowledgement, unrelated turns and item completion do not finish compaction');
      assert.ok(events.some((event) => event.type === 'tool.start'));
      assert.equal(events.some((event) => event.type === 'tool.end'), false);
      await assert.rejects(session.prompt({ text: 'next', images: [] }, hooks), /already running/);
      assert.equal((await pending).stopReason, 'end_turn');
      assert.ok(events.some((event) => event.type === 'tool.end' && event.data.status === 'completed'));
      assert.equal((await session.prompt({ text: 'next', images: [] }, hooks)).stopReason, 'end_turn');
    } finally { await session.close(); }
  }
});

test('compact settles on failure, rejection, disconnect and cancellation before its turn id arrives', async () => {
  for (const mode of ['FAIL', 'REJECT', 'CRASH', 'CANCEL']) {
    const session = await new CodexAppServerAdapter(command, { [`FAKE_CODEX_COMPACT_${mode}`]: '1' }).start({ cwd: here });
    const events: EmittedEvent[] = [];
    try {
      const pending = session.prompt({ text: '/compact', images: [] }, { emit: (event) => events.push(event), requestApproval: async () => ({ cancelled: true }) });
      if (mode === 'CANCEL') await session.cancel();
      const result = await pending;
      assert.equal(result.stopReason, mode === 'CANCEL' ? 'cancelled' : 'error');
      assert.ok(events.some((event) => event.type === 'tool.end' && event.data.status === 'failed'));
      assert.equal(events.some((event) => event.type === 'message.delta'), false);
    } finally { await session.close(); }
  }
});
