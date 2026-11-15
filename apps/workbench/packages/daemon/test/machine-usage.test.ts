import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, statSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import { DatabaseSync } from 'node:sqlite';
import { createHttpServer } from '../src/http.js';
import { MachineUsageMeter, type MachineUsageReport } from '../src/machine-usage.js';

const TOKEN = 'test-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}` };
const MIN = 60_000;

/** One meter over its own in-memory store and a fake home; every test starts clean. */
function makeMeter(launchers: Array<[string, string]> = [['11111111-1111-1111-1111-111111111111', 'claude']]) {
  const home = mkdtempSync(join(tmpdir(), 'calli-usage-home-'));
  const db = new DatabaseSync(':memory:');
  let nowMs = Date.parse('2026-11-09T10:30:00Z');
  const meter = new MachineUsageMeter({
    db,
    sources: () => ({ claudeDirs: [join(home, '.claude')], codexHomes: [join(home, '.codex')] }),
    launchers: () => new Map(launchers),
    now: () => nowMs,
  });
  return {
    home,
    db,
    meter,
    set now(value: number) { nowMs = value; },
    get now() { return nowMs; },
    done() { db.close(); rmSync(home, { recursive: true, force: true }); },
  };
}

function claudeTranscript(home: string, session: string, lines: unknown[]): string {
  const dir = join(home, '.claude', 'projects', '-home-user-project');
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${session}.jsonl`);
  writeFileSync(path, lines.map((l) => JSON.stringify(l)).join('\n') + '\n');
  return path;
}

function claudeReply(id: string, at: string, usage: Record<string, number>, model = 'claude-sonnet-5'): unknown {
  return { type: 'assistant', timestamp: at, message: { id, model, usage } };
}

function codexRollout(home: string, thread: string, lines: unknown[]): string {
  const dir = join(home, '.codex', 'sessions', '2026', '11', '09');
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `rollout-2026-11-09T09-00-00-${thread}.jsonl`);
  writeFileSync(path, lines.map((l) => JSON.stringify(l)).join('\n') + '\n');
  return path;
}

function tokenCount(at: string, last: { input_tokens: number; cached_input_tokens: number; output_tokens: number }, total: number): unknown {
  return { timestamp: at, type: 'event_msg', payload: { type: 'token_count', info: { last_token_usage: last, total_token_usage: { total_tokens: total } } } };
}

test('Claude replies are summed per minute, model and launcher; repeats and synthetic lines stay out', async () => {
  const m = makeMeter();
  try {
    claudeTranscript(m.home, '11111111-1111-1111-1111-111111111111', [
      { type: 'user', timestamp: '2026-11-09T10:00:10Z', message: { role: 'user', content: 'hi' } },
      claudeReply('msg_1', '2026-11-09T10:00:20Z', { input_tokens: 100, cache_creation_input_tokens: 40, cache_read_input_tokens: 500, output_tokens: 60 }),
      // The rest of the same reply (another content block) repeats its usage: not a second request.
      claudeReply('msg_1', '2026-11-09T10:00:21Z', { input_tokens: 100, cache_creation_input_tokens: 40, cache_read_input_tokens: 500, output_tokens: 60 }),
      claudeReply('msg_2', '2026-11-09T10:00:40Z', { input_tokens: 10, output_tokens: 5 }, '<synthetic>'),
      claudeReply('msg_3', '2026-11-09T10:01:30Z', { input_tokens: 20, output_tokens: 8 }, 'claude-opus-5'),
    ]);
    await m.meter.scan();
    const report = m.meter.report(1, undefined, undefined);
    assert.equal(report.step, 60);
    assert.deepEqual(report.total, { input: 120, cacheWrite: 40, cacheRead: 500, output: 68, replies: 2 });
    assert.deepEqual(report.models.map((x) => x.name), ['claude-sonnet-5', 'claude-opus-5']);
    assert.deepEqual(report.launchers.map((x) => x.name), ['claude']);
    const at = (iso: string) => report.points.find((p) => p.t === Math.floor(Date.parse(iso) / MIN) * 60);
    assert.equal(at('2026-11-09T10:00:20Z')?.input, 100);
    assert.equal(at('2026-11-09T10:01:30Z')?.output, 8);
  } finally { m.done(); }
});

test('a fork\'s copy of a reply in another file is counted once', async () => {
  const m = makeMeter();
  try {
    claudeTranscript(m.home, '11111111-1111-1111-1111-111111111111', [
      claudeReply('msg_fork', '2026-11-09T10:05:00Z', { input_tokens: 30, output_tokens: 7 }),
    ]);
    claudeTranscript(m.home, '22222222-2222-2222-2222-222222222222', [
      claudeReply('msg_fork', '2026-11-09T10:05:00Z', { input_tokens: 30, output_tokens: 7 }),
    ]);
    await m.meter.scan();
    const report = m.meter.report(1, undefined, undefined);
    assert.equal(report.total.replies, 1);
    assert.equal(report.total.input, 30);
  } finally { m.done(); }
});

test('subagent transcripts count under their session, which names the launcher', async () => {
  const m = makeMeter([['11111111-1111-1111-1111-111111111111', 'claude-2']]);
  try {
    const dir = join(m.home, '.claude', 'projects', '-home-user-project', '11111111-1111-1111-1111-111111111111', 'subagents');
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'agent-abc.jsonl'), JSON.stringify(
      claudeReply('msg_sub', '2026-11-09T10:03:00Z', { input_tokens: 12, output_tokens: 6 }),
    ) + '\n');
    await m.meter.scan();
    const report = m.meter.report(1, undefined, undefined);
    assert.equal(report.total.input, 12);
    assert.deepEqual(report.launchers.map((x) => x.name), ['claude-2']);
  } finally { m.done(); }
});

test('Codex rollouts: cached input is split out, the model comes from turn_context, repeats stay out', async () => {
  const m = makeMeter([]);
  try {
    const thread = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    codexRollout(m.home, thread, [
      { timestamp: '2026-11-09T10:10:00Z', type: 'event_msg', payload: { type: 'turn_context', model: 'gpt-6' } },
      tokenCount('2026-11-09T10:10:05Z', { input_tokens: 1000, cached_input_tokens: 800, output_tokens: 200 }, 2000),
      // A rate-limit update repeats the same total: not a new request.
      tokenCount('2026-11-09T10:10:20Z', { input_tokens: 1000, cached_input_tokens: 800, output_tokens: 200 }, 2000),
      tokenCount('2026-11-09T10:11:40Z', { input_tokens: 300, cached_input_tokens: 100, output_tokens: 90 }, 2390),
    ]);
    await m.meter.scan();
    const report = m.meter.report(1, undefined, undefined);
    assert.deepEqual(report.total, { input: 400, cacheWrite: 0, cacheRead: 900, output: 290, replies: 2 });
    assert.deepEqual(report.models.map((x) => x.name), ['gpt-6']);
    // The rollout's thread matches no thread of ours: the "other" launcher.
    assert.deepEqual(report.launchers.map((x) => x.name), ['']);
  } finally { m.done(); }
});

test('a transcript\'s new lines are counted on the next scan; the old ones are not again', async () => {
  const m = makeMeter();
  try {
    const path = claudeTranscript(m.home, '33333333-3333-3333-3333-333333333333', [
      claudeReply('msg_a', '2026-11-09T10:15:00Z', { input_tokens: 1, output_tokens: 1 }),
    ]);
    await m.meter.scan();
    assert.equal(m.meter.report(1, undefined, undefined).total.replies, 1);
    appendFileSync(path, JSON.stringify(claudeReply('msg_b', '2026-11-09T10:16:00Z', { input_tokens: 2, output_tokens: 2 })) + '\n');
    await m.meter.scan();
    const report = m.meter.report(1, undefined, undefined);
    assert.equal(report.total.replies, 2);
    assert.equal(report.total.input, 3);
  } finally { m.done(); }
});

test('minutes without usage are gaps only when the daemon was not running', async () => {
  const m = makeMeter();
  try {
    claudeTranscript(m.home, '44444444-4444-4444-4444-444444444444', [
      claudeReply('msg_c', '2026-11-09T10:00:00Z', { input_tokens: 5, output_tokens: 1 }),
    ]);
    // The daemon came up at 10:05 (its first uptime slot is 10:00's) and scanned once.
    m.now = Date.parse('2026-11-09T10:05:00Z');
    await m.meter.scan();
    const report = m.meter.report(1, undefined, undefined);
    const at = (iso: string) => report.points.find((p) => p.t === Math.floor(Date.parse(iso) / MIN) * 60);
    assert.equal(at('2026-11-09T10:00:00Z')?.gap, false, 'the minute that ran');
    assert.equal(at('2026-11-09T10:02:00Z')?.gap, false, 'idle but running: a zero, not a break');
    assert.equal(at('2026-11-09T09:35:00Z')?.gap, true, 'before the daemon came up');
  } finally { m.done(); }
});

test('model and launcher filters narrow the series but keep every option listed', async () => {
  const m = makeMeter();
  try {
    // A session the hub runs as a thread: its replies name that launcher, not "other".
    claudeTranscript(m.home, '11111111-1111-1111-1111-111111111111', [
      claudeReply('msg_m1', '2026-11-09T10:20:00Z', { input_tokens: 10, output_tokens: 1 }),
      claudeReply('msg_m2', '2026-11-09T10:20:30Z', { input_tokens: 20, output_tokens: 2 }, 'claude-opus-5'),
    ]);
    await m.meter.scan();
    const filtered = m.meter.report(1, 'claude-sonnet-5', undefined);
    assert.equal(filtered.total.input, 10);
    assert.deepEqual(filtered.models.map((x) => x.name), ['claude-opus-5', 'claude-sonnet-5'], 'the menu keeps both models, busiest first');
    const byOther = m.meter.report(1, undefined, '-');
    assert.equal(byOther.total.replies, 0, 'nothing of the "other" launcher in this window');
  } finally { m.done(); }
});

test('a restart replaces the window with what the files say now', async () => {
  const m = makeMeter();
  try {
    claudeTranscript(m.home, '66666666-6666-6666-6666-666666666666', [
      claudeReply('msg_r', '2026-11-09T10:25:00Z', { input_tokens: 7, output_tokens: 3 }),
    ]);
    await m.meter.scan();
    assert.equal(m.meter.report(1, undefined, undefined).total.replies, 1);
    // A second daemon (a fresh meter over the same store and files) re-reads the window and
    // replaces it; the reply is not counted twice.
    const restarted = new MachineUsageMeter({
      db: m.db,
      sources: () => ({ claudeDirs: [join(m.home, '.claude')], codexHomes: [] }),
      launchers: () => new Map(),
      now: () => m.now,
    });
    await restarted.scan();
    const report = restarted.report(1, undefined, undefined);
    assert.equal(report.total.replies, 1);
    assert.deepEqual(report.launchers.map((x) => x.name), [''], 'no thread map: the same reply, now "other"');
  } finally { m.done(); }
});

test('minute rows older than the window fold into 10-minute rows, and a week is the horizon', async () => {
  const m = makeMeter();
  try {
    // Rows no file re-reads anymore (older than the 26-hour window) are the meter's own to keep.
    const old = Math.floor((m.now - 30 * 3600_000) / MIN) * 60; // 30h ago
    const ancient = Math.floor((m.now - 8 * 24 * 3600_000) / MIN) * 60; // 8 days ago
    m.db.prepare(`INSERT INTO machine_usage(minute, step, model, launcher, input, cache_write, cache_read, output, replies)
      VALUES (?, 60, 'old-model', '', 10, 0, 0, 5, 1), (?, 600, 'old-model', '', 90, 0, 0, 9, 2)`).run(old, ancient);
    await m.meter.scan();
    const kept = (m.db.prepare(`SELECT minute, step, SUM(input) AS input FROM machine_usage WHERE model = 'old-model' GROUP BY minute, step`)
      .all() as Array<{ minute: number; step: number; input: number }>).map((row) => ({ ...row }));
    // The minute row folded into its 10-minute row; the 8-day-old row is gone.
    assert.deepEqual(kept, [{ minute: Math.floor(old / 600) * 600, step: 600, input: 10 }]);
    // And a 7-day report still reads it.
    assert.equal(m.meter.report(168, 'old-model', undefined).total.input, 10);
  } finally { m.done(); }
});

test('GET /api/machine-usage serves the report and rejects other ranges', async () => {
  const m = makeMeter();
  let server: Server | null = null;
  try {
    claudeTranscript(m.home, '77777777-7777-7777-7777-777777777777', [
      claudeReply('msg_api', '2026-11-09T10:28:00Z', { input_tokens: 4, output_tokens: 2 }),
    ]);
    await m.meter.scan();
    server = createHttpServer({ manager: stubManager(), token: TOKEN, webDir: null, version: 'test', machineUsage: m.meter });
    await new Promise<void>((resolve) => server!.listen(0, '127.0.0.1', () => resolve()));
    const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    assert.equal((await fetch(base + '/api/machine-usage?hours=24', {})).status, 401);
    assert.equal((await fetch(base + '/api/machine-usage?hours=5', { headers: auth })).status, 400);
    const res = await fetch(base + '/api/machine-usage?hours=1&model=claude-sonnet-5', { headers: auth });
    assert.equal(res.status, 200);
    const report = await res.json() as MachineUsageReport;
    assert.equal(report.hours, 1);
    assert.equal(report.total.input, 4);
    assert.ok(report.points.length >= 60);
  } finally {
    server?.close();
    m.done();
  }
});

function stubManager(): any {
  return {
    listThreads: () => [],
    subscribe: () => () => undefined,
    generalSettings: () => ({}),
    workdirsConfig: () => ({ workdirs: [] }),
    allowedWorkdirs: [],
  };
}

test('a big transcript is read in bounded rounds: 4 MiB per file per scan, the rest follows', async () => {
  const m = makeMeter();
  try {
    // Small reply lines past two rounds' cap: the first scan stops at the cap, the second finishes.
    const count = 40_000;
    const lines: unknown[] = Array.from({ length: count }, (_, i) =>
      claudeReply(`msg_bulk_${i}`, '2026-11-09T10:00:00Z', { input_tokens: 1, output_tokens: 1 }));
    const path = claudeTranscript(m.home, '11111111-1111-1111-1111-111111111111', lines);
    assert.ok(statSync(path).size > 4_194_304, 'the fixture must be bigger than one 4 MiB round');
    await m.meter.scan();
    const first = m.meter.report(1, undefined, undefined);
    assert.ok(first.total.replies > 0 && first.total.replies < count, `the first round is bounded (got ${first.total.replies})`);
    await m.meter.scan();
    const second = m.meter.report(1, undefined, undefined);
    assert.equal(second.total.replies, count);
    assert.equal(second.total.input, count);
  } finally { m.done(); }
});
