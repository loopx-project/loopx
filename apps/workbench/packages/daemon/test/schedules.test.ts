import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { AgentAdapter, AgentSession, TurnInput, TurnHooks } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { Scheduler } from '../src/scheduler.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

// Scheduled tasks over HTTP, with a stub agent whose turns can be held and released so runs
// overlap and skip the way they do in production.

let gate: Promise<void> | null = null;
let openGate: (() => void) | null = null;
const hold = () => {
  gate = new Promise((r) => {
    openGate = r;
  });
};
const release = () => {
  openGate?.();
  gate = null;
};

const stubSession = (): AgentSession => ({
  agentSessionId: 'stub-session',
  async prompt(input: TurnInput, hooks: TurnHooks) {
    hooks.emit({ type: 'message.delta', data: { text: `echo: ${input.text}` } });
    if (gate) await gate;
    return { stopReason: 'end_turn' };
  },
  async cancel() {},
  async close() {},
});

const stubAdapter: AgentAdapter = {
  label: 'Stub',
  available: async () => true,
  capabilities: {
    resume: false,
    approvals: false,
    permissionModes: ['ask', 'auto_edit', 'full_access'],
    defaultPermissionMode: 'auto_edit',
    effortLevels: [],
    images: false,
    deleteSession: false,
    history: false,
    fork: false,
  },
  async commands() {
    return [];
  },
  resumeCommand: () => 'stub --resume',
  async start() {
    return stubSession();
  },
} as unknown as AgentAdapter;

const TOKEN = 'test-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

let server: Server;
let base: string;
let dataDir: string;
let workroot: string;
let manager: ThreadManager;
let store: Store;
let scheduler: Scheduler;

const api = async (path: string, init: RequestInit = {}): Promise<Response> =>
  fetch(base + path, { ...init, headers: { ...auth, ...(init.headers ?? {}) } });

interface RunJson {
  id: string;
  status: 'running' | 'done' | 'error' | 'skipped' | 'missed';
  threadId: string | null;
  message: string | null;
}

interface ScheduleJson {
  id: string;
  name: string;
  agent: string;
  cwd: string;
  prompt: string;
  cron: string;
  mode: 'new' | 'same';
  threadId: string | null;
  permissionMode: string;
  enabled: boolean;
  timezone: string | null;
  catchUp: boolean;
  description: string;
  nextRunAt: string | null;
  lastRun: RunJson | null;
  problem: string | null;
}

const createSchedule = async (body: Record<string, unknown>): Promise<ScheduleJson> => {
  const res = await api('/api/schedules', { method: 'POST', body: JSON.stringify(body) });
  assert.equal(res.status, 201);
  return (await res.json()) as ScheduleJson;
};

const runsOf = async (id: string): Promise<RunJson[]> => ((await (await api(`/api/schedules/${id}/runs`)).json()) as RunJson[]);

/** Polls a schedule's runs until nothing is running, then hands back how the latest real run ended. */
const settle = async (id: string): Promise<RunJson | null> => {
  for (let i = 0; i < 100; i++) {
    const runs = await runsOf(id);
    if (runs.length && !runs.some((r) => r.status === 'running')) {
      return runs.find((r) => r.status === 'done' || r.status === 'error') ?? runs[0];
    }
    await new Promise((r) => setTimeout(r, 20));
  }
  return null;
};

const scheduleBody = {
  name: '构建检查',
  agent: 'stub',
  prompt: '跑一遍 npm test，报告结果（{{date}}）',
  cron: '* * * * *',
  mode: 'new',
  cwd: null as string | null,
};

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-sched-test-'));
  workroot = mkdtempSync(join(tmpdir(), 'calli-sched-work-'));
  store = new Store(dataDir);
  manager = new ThreadManager(store, new Map([['stub', stubAdapter]]), [workroot], new AttachmentStore(dataDir));
  scheduler = new Scheduler(store, manager, new Map([['stub', stubAdapter]]));
  scheduler.start();
  server = createHttpServer({ manager, scheduler, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const { port } = server.address() as AddressInfo;
  base = `http://127.0.0.1:${port}`;
  scheduleBody.cwd = workroot;
});

after(async () => {
  server.close();
  scheduler.dispose();
  await manager.shutdown();
  store.close();
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(workroot, { recursive: true, force: true });
});

test('schedules: create, list, preview; the page shows words and next runs', async () => {
  const created = await createSchedule(scheduleBody);
  assert.equal(created.mode, 'new');
  assert.equal(created.enabled, true);
  assert.equal(created.permissionMode, 'auto_edit');
  assert.equal(created.description, '每分钟');
  assert.ok(created.nextRunAt);
  assert.equal(created.problem, null);

  const listed = ((await (await api('/api/schedules')).json()) as ScheduleJson[]).find((s) => s.id === created.id);
  assert.ok(listed);

  const preview = (await (await api('/api/schedules/preview', { method: 'POST', body: JSON.stringify({ cron: '30 9 * * mon-fri' }) })).json()) as {
    description: string;
    fires: string[];
  };
  assert.equal(preview.description, '每工作日 09:30');
  assert.equal(preview.fires.length, 3);
  assert.equal((await api('/api/schedules/preview', { method: 'POST', body: JSON.stringify({ cron: 'nope' }) })).status, 400);

  const previewTz = (await (await api('/api/schedules/preview', { method: 'POST', body: JSON.stringify({ cron: '0 9 * * *', timezone: 'Asia/Shanghai' }) })).json()) as {
    description: string;
    fires: string[];
  };
  assert.equal(previewTz.description, '每天 09:00');
  // In Asia/Shanghai (UTC+8), 09:00 is 01:00 UTC.
  assert.ok(previewTz.fires[0].endsWith('T01:00:00.000Z'));
});

test('schedules: refuse what could never run', async () => {
  const cases: Array<Record<string, unknown>> = [
    { ...scheduleBody, cron: '99 * * * *' },
    { ...scheduleBody, prompt: '   ' },
    { ...scheduleBody, agent: 'nobody' },
    { ...scheduleBody, cwd: '/definitely/not/allowed' },
    { ...scheduleBody, cwd: join(workroot, 'missing') },
    { ...scheduleBody, mode: 'side-ways' },
    { ...scheduleBody, permissionMode: 'yolo' },
    { ...scheduleBody, timezone: 'Not/A_Real_Timezone' },
  ];
  for (const body of cases) {
    const res = await api('/api/schedules', { method: 'POST', body: JSON.stringify(body) });
    assert.equal(res.status, 400, JSON.stringify(body));
  }
});

test('schedules: a tick fires, the run lands in a fresh thread, and ends done', async () => {
  const s = await createSchedule({ ...scheduleBody, name: '巡检' });
  await scheduler.tick();
  const run = await settle(s.id);
  assert.ok(run);
  assert.equal(run.status, 'done');
  assert.ok(run.threadId);
  // The thread says what started it, and the turn's text has the date filled in.
  const thread = (await (await api(`/api/threads/${run.threadId}`)).json()) as { schedule: { id: string; name: string } | null; title: string };
  assert.deepEqual(thread.schedule, { id: s.id, name: '巡检' });
  assert.match(thread.title, /^巡检 · \d{4}-\d{2}-\d{2}$/);
  const events = (await (await api(`/api/threads/${run.threadId}/events?tail=5`)).json()) as Array<{ type: string; data: Record<string, unknown> }>;
  const started = events.find((e) => e.type === 'turn.started');
  assert.equal(started?.data.scheduled, true);
  assert.ok(!String(started?.data.text ?? '').includes('{{date}}'));
  assert.ok(String(started?.data.text ?? '').includes(new Date().getFullYear().toString()));
});

test('schedules: same mode keeps one thread; rebinding adopts the new thread', async () => {
  const s = await createSchedule({ ...scheduleBody, mode: 'same', name: '周报' });
  await scheduler.tick();
  const first = await settle(s.id);
  assert.ok(first?.threadId);
  const after = ((await (await api('/api/schedules')).json()) as ScheduleJson[]).find((x) => x.id === s.id);
  assert.equal(after?.threadId, first.threadId);
  // A manual run goes into the same thread, so the context builds up.
  await api(`/api/schedules/${s.id}/run`, { method: 'POST' });
  const second = await settle(s.id);
  assert.equal(second?.threadId, first.threadId);
  const runs = await runsOf(s.id);
  assert.equal(runs.length, 2);

  // Rebind to another thread: the schedule follows it, agent and directory included.
  const other = (await (await api('/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'stub', cwd: workroot }) })).json()) as { id: string };
  const patched = (await (await api(`/api/schedules/${s.id}`, { method: 'PATCH', body: JSON.stringify({ threadId: other.id }) })).json()) as ScheduleJson;
  assert.equal(patched.threadId, other.id);
});

test('schedules: a previous run still going means the next one is skipped', async () => {
  const s = await createSchedule({ ...scheduleBody, name: '慢任务' });
  hold();
  try {
    await api(`/api/schedules/${s.id}/run`, { method: 'POST' });
    const skipped = (await (await api(`/api/schedules/${s.id}/run`, { method: 'POST' })).json()) as { status: string; message?: string };
    assert.equal(skipped.status, 'skipped');
  } finally {
    release();
  }
  const run = await settle(s.id);
  assert.equal(run?.status, 'done');
});

test('schedules: deleting a schedule keeps its threads', async () => {
  const s = await createSchedule({ ...scheduleBody, name: '将被删除' });
  await scheduler.tick();
  const run = await settle(s.id);
  assert.ok(run?.threadId);
  assert.equal((await api(`/api/schedules/${s.id}`, { method: 'DELETE' })).status, 204);
  assert.equal((await api(`/api/schedules/${s.id}/runs`)).status, 404);
  const thread = await api(`/api/threads/${run.threadId}`);
  assert.equal(thread.status, 200);
});

test('schedules: a bound thread that is gone says so, in the list and in the run', async () => {
  const s = await createSchedule({ ...scheduleBody, mode: 'same', name: '绑定消失' });
  await scheduler.tick();
  const run = await settle(s.id);
  assert.ok(run?.threadId);
  await api(`/api/threads/${run.threadId}`, { method: 'DELETE' });
  const listed = ((await (await api('/api/schedules')).json()) as ScheduleJson[]).find((x) => x.id === s.id);
  assert.equal(listed?.problem, '绑定的线程已删除');
  const fired = (await (await api(`/api/schedules/${s.id}/run`, { method: 'POST' })).json()) as { status: string };
  assert.equal(fired.status, 'error');
});

test('schedules: a restart closes interrupted runs and records what downtime missed', async () => {
  const s = await createSchedule({ ...scheduleBody, cron: '0 * * * *', name: '每小时' });
  scheduler.dispose();
  // A run that was going when the daemon stopped, and two hourly fires nobody saw.
  store.insertScheduleRun({
    id: 'run-interrupted',
    scheduleId: s.id,
    status: 'running',
    threadId: null,
    turnId: null,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    message: null,
  });
  const twoHoursAgo = new Date(Date.now() - 2 * 3_600_000);
  store.updateSchedule(s.id, { lastFiredAt: twoHoursAgo.toISOString() });

  const reborn = new Scheduler(store, manager, new Map([['stub', stubAdapter]]));
  reborn.start();
  try {
    const runs = await runsOf(s.id);
    const interrupted = runs.find((r) => r.id === 'run-interrupted');
    assert.equal(interrupted?.status, 'error');
    assert.match(interrupted?.message ?? '', /重启/);
    const missed = runs.find((r) => r.status === 'missed');
    assert.ok(missed, 'a missed entry is recorded');
    assert.match(missed?.message ?? '', /2 次/);
  } finally {
    reborn.dispose();
  }
});

test('schedules: restart recovery fires once when catchUp is enabled', async () => {
  const s = await createSchedule({ ...scheduleBody, cron: '0 * * * *', name: '补跑任务', catchUp: true });
  scheduler.dispose();
  const twoHoursAgo = new Date(Date.now() - 2 * 3_600_000);
  store.updateSchedule(s.id, { lastFiredAt: twoHoursAgo.toISOString() });

  const reborn = new Scheduler(store, manager, new Map([['stub', stubAdapter]]));
  reborn.start();
  try {
    const run = await settle(s.id);
    assert.ok(run);
    assert.equal(run.status, 'done');
    const runs = await runsOf(s.id);
    assert.ok(runs.some((r) => r.status === 'missed'), 'recorded missed entry');
    assert.ok(runs.some((r) => r.status === 'done'), 'also fired once');
  } finally {
    reborn.dispose();
  }
});

test('schedules: auto archive hides only old idle threads that need no attention', async () => {
  await api('/api/general-settings', { method: 'PUT', body: JSON.stringify({ startupPage: 'new-thread', autoArchiveDays: 7 }) });
  const make = async (id: string, updatedAt: string) => {
    const thread = await manager.createThread({ agent: 'stub', cwd: workroot, title: id });
    store.db.prepare('UPDATE threads SET updated_at = ? WHERE id = ?').run(updatedAt, thread.id);
    return thread.id;
  };
  const now = new Date('2026-11-02T12:00:00.000Z');
  const old = await make('old', '2026-10-20T00:00:00.000Z');
  const pinned = await make('pinned', '2026-10-20T00:00:00.000Z'); store.updateThread(pinned, { pinned: true });
  const unread = await make('unread', '2026-10-20T00:00:00.000Z'); store.updateThread(unread, { unread: true });
  const running = await make('running', '2026-10-20T00:00:00.000Z'); store.updateThread(running, { status: 'running' });
  const waiting = await make('waiting', '2026-10-20T00:00:00.000Z');
  const recent = await make('recent', '2026-10-31T00:00:00.000Z');
  const approvals = (manager as unknown as { approvals: Map<string, { threadId: string; optionIds: string[]; resolve: () => void }> }).approvals;
  approvals.set('auto-archive-waiting', { threadId: waiting, optionIds: ['allow'], resolve: () => {} });

  try {
    await scheduler.tick(now);

    assert.equal(store.getThread(old)?.archivedAt !== null, true);
    assert.equal(store.getThread(pinned)?.archivedAt, null);
    assert.equal(store.getThread(unread)?.archivedAt, null);
    assert.equal(store.getThread(running)?.archivedAt, null);
    assert.equal(store.getThread(waiting)?.archivedAt, null);
    assert.equal(store.getThread(recent)?.archivedAt, null);
  } finally {
    approvals.delete('auto-archive-waiting');
    await api('/api/general-settings', { method: 'PUT', body: JSON.stringify({ startupPage: 'new-thread', autoArchiveDays: null }) });
    store.updateThread(running, { status: 'idle' });
  }
});

test('schedules: enable and disable', async () => {
  const s = await createSchedule({ ...scheduleBody, name: '开关' });
  const off = (await (await api(`/api/schedules/${s.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: false }) })).json()) as ScheduleJson;
  assert.equal(off.enabled, false);
  assert.equal(off.nextRunAt, null, 'a disabled schedule has no next run');
  const on = (await (await api(`/api/schedules/${s.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: true }) })).json()) as ScheduleJson;
  assert.equal(on.enabled, true);
  assert.ok(on.nextRunAt);
  await api(`/api/schedules/${s.id}`, { method: 'DELETE' });
});
