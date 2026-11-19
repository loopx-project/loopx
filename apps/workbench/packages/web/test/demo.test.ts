import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DemoApi, DemoError } from '../src/demo/DemoApi';
import { demoData } from '../src/demo/data';
import { buildTimeline } from '../src/timeline';

const NOW = Date.parse('2026-10-16T08:00:00Z');
const demoApi = () => new DemoApi(async () => demoData(NOW));

test('the demo answers reads from its own data and never touches the network', async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = () => Promise.reject(new Error('the demo must not fetch'));
  try {
    const api = demoApi();
    assert.equal(api.demo, true);
    const [agents, config, threads, sections, todos, schedules] = await Promise.all([api.agents(), api.workdirs(), api.threads(), api.sections(), api.todos(), api.schedules()]);
    assert.ok(agents.length >= 2 && agents.every((a) => a.available));
    assert.ok(config.workdirs.length > 0);
    assert.ok(threads.length >= 4);
    assert.ok(sections.length > 0);
    assert.ok(todos.some((t) => t.threads.length > 0), 'plan items link to demo threads');
    assert.equal(schedules[0].lastRun?.threadId && threads.some((t) => t.id === schedules[0].lastRun!.threadId), true);
    assert.deepEqual(await api.threads({ archived: true }), []);
    assert.ok((await api.models('codex')).length > 0);
    assert.deepEqual(await api.discoverAgents(), []);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('every demo thread has events the timeline can draw', async () => {
  const api = demoApi();
  for (const t of await api.threads()) {
    const timeline = buildTimeline(await api.eventsTail(t.id));
    assert.ok(timeline.items.some((it) => it.kind === 'user'), `${t.id} has a question`);
    // A thread listed as running is one that waits on the user; the others are finished.
    assert.equal(timeline.running, t.status === 'running', t.id);
    if (t.waiting) assert.equal(timeline.awaitingApproval, true, t.id);
  }
  const events = await api.eventsTail('demo-dark-mode');
  assert.deepEqual(await api.events('demo-dark-mode', events.length - 2), events.slice(-2), 'after= gives only what is newer');
});

test('anything that would change something asks to connect; a read with no data is quiet', async () => {
  const api = demoApi();
  const write = await api.startTurn('demo-dark-mode', 'hi').catch((e) => e);
  assert.ok(write instanceof DemoError && write.write);
  const created = await api.createThread({ agent: 'codex', cwd: '~/projects/storefront' }).catch((e) => e);
  assert.ok(created instanceof DemoError && created.write);
  const rename = await api.updateThread('demo-dark-mode', { title: 'x' }).catch((e) => e);
  assert.ok(rename instanceof DemoError && rename.write);
  const read = await api.usageReport().catch((e) => e);
  assert.ok(read instanceof DemoError && !read.write);
  const video = await api.uploadVideo('demo-dark-mode', {} as File).catch((e) => e);
  assert.ok(video instanceof DemoError && video.write);
});

test('opening an unread thread marks it read, and only that is kept', async () => {
  const api = demoApi();
  const updated = await api.updateThread('demo-tests', { unread: false });
  assert.equal(updated.unread, false);
  assert.equal((await api.thread('demo-tests')).unread, false);
  // What the page was handed is a copy: changing it does not change the demo.
  updated.title = 'changed';
  assert.notEqual((await api.thread('demo-tests')).title, 'changed');
});

test('a demo stream stays open until the page lets go of it', async () => {
  const api = demoApi();
  const ctrl = new AbortController();
  let settled = false;
  const stream = api.stream('/api/stream', () => assert.fail('nothing happens in the demo'), ctrl.signal).then(() => (settled = true));
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(settled, false);
  ctrl.abort();
  await stream;
  assert.equal(settled, true);
});

test('files and search work inside the demo threads', async () => {
  const api = demoApi();
  const top = await api.threadTree('demo-dark-mode');
  assert.ok(top.entries.some((e) => e.name === 'src' && e.type === 'dir'));
  const file = await api.threadText('demo-dark-mode', 'src/theme.ts');
  assert.match(file.text ?? '', /applyTheme/);
  const hits = await api.search('深色模式');
  assert.ok(hits.some((h) => h.threadId === 'demo-dark-mode'));
});

test('the machines tab has demo data to read, whatever it filters', async () => {
  const api = demoApi();
  const day = await api.machineUsage(24);
  assert.equal(day.step, 600);
  assert.equal(day.points.length, 144);
  assert.ok(day.models.length >= 2, 'models are listed');
  assert.ok(day.launchers.some((l) => l.name === ''), 'the "other" launcher is listed');
  const all = day.total.input + day.total.cacheWrite + day.total.cacheRead + day.total.output;
  assert.ok(all > 0);
  assert.ok(day.points.some((p) => p.gap), 'the offline night shows as gaps, not zeros');
  assert.ok(day.points.some((p) => !p.gap && p.replies >= 3), 'busy steps have enough replies for the hit rate');
  // A model filter narrows the series but never the share lists it is picked from.
  const one = await api.machineUsage(24, day.models[0].name);
  const oneAll = one.total.input + one.total.cacheWrite + one.total.cacheRead + one.total.output;
  assert.ok(oneAll < all);
  assert.deepEqual(one.models.map((m) => m.name), day.models.map((m) => m.name));
  assert.equal(one.points.length, 144);
  // The other ranges keep their own steps.
  assert.equal((await api.machineUsage(1)).points.length, 60);
  assert.equal((await api.machineUsage(168)).step, 3600);
});
