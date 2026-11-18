import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { AttachmentStore } from '../src/attachments.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { PersonalPlanService, planWindow, horizonDeadline } from '../src/personal-plan.js';
import { PersonalPrep } from '../src/personal-prep.js';
import { personalContext } from '../src/personal-context.js';

async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-plan-')), store = new Store(dir);
  const manager = new ThreadManager(store, new Map([['pi', new PiRpcAdapter([process.execPath, resolve('test/fixtures/fake-pi.mjs')])]]), new Workdirs([dir]), new AttachmentStore(dir));
  manager.personal.putSettings({ directory: dir, agent: 'pi' });
  return { manager, store, async close() { await manager.shutdown(); await manager.personal.flushMirror(); store.close(); rmSync(dir, { recursive: true, force: true }); } };
}
const now = new Date('2026-11-13T12:00:00Z');
const event = (id = 'meeting', start = '2026-11-13T12:20:00Z') => ({ event_id: id, summary: id, start_time: { timestamp: Date.parse(start) / 1000 }, end_time: { timestamp: Date.parse(start) / 1000 + 3600 }, recurrence: 'weekly', attendees: [{ display_name: 'Teammate' }], description: 'https://example.com/agenda' });

test('plan boundaries use local Monday, month end and DST; horizon defaults are 23:59', () => {
  const old = process.env.TZ;
  try {
    process.env.TZ = 'Asia/Shanghai';
    assert.deepEqual(planWindow('day', new Date('2026-11-01T16:30Z')), { range: 'day', from: '2026-11-01T16:00:00.000Z', to: '2026-11-02T16:00:00.000Z' });
    assert.equal(planWindow('week', new Date('2026-11-01T16:30Z')).from, '2026-11-01T16:00:00.000Z');
    assert.equal(planWindow('week', new Date('2026-11-01T15:59Z')).to, '2026-11-01T16:00:00.000Z');
    assert.equal(horizonDeadline('month', new Date('2024-02-28T16:00Z')), '2024-02-29T15:59:00.000Z');
    assert.equal(horizonDeadline('week', new Date('2026-11-02T00:00Z')), '2026-11-08T15:59:00.000Z');
    process.env.TZ = 'America/New_York';
    const day = planWindow('day', new Date('2026-03-08T12:00Z'));
    assert.equal(Date.parse(day.to) - Date.parse(day.from), 23 * 3600_000);
    const hour = planWindow('hour', new Date('2026-03-08T06:00Z'));
    assert.equal(Date.parse(hour.to) - Date.parse(hour.from), 4 * 3600_000);
    assert.throws(() => planWindow('year'), /range/);
  } finally { if (old === undefined) delete process.env.TZ; else process.env.TZ = old; }
});

test('calendar distinguishes off, unavailable and empty, caches, deduplicates and bounds each view', async () => {
  const h = await setup(); try {
    let calls = 0;
    const plan = new PersonalPlanService(h.manager, async () => { calls++; return JSON.stringify({ data: { events: [event(), event(), ...Array.from({ length: 25 }, (_, i) => event(`meeting${i}`)), event('ended', '2026-11-13T10:00Z')] } }); });
    assert.equal((await plan.get('hour', now)).meetings.state, 'off'); assert.equal(calls, 0);
    h.manager.lark.save({ enabled: true });
    const value = await plan.get('hour', now);
    assert.equal(value.meetings.state, 'ok'); assert.equal(value.meetings.items.length, 20);
    assert.equal(value.meetings.items.filter(item => item.id === 'meeting').length, 1);
    assert.ok(!value.meetings.items.some(item => item.id === 'ended'));
    assert.ok(!('description' in value.meetings.items[0]));
    await plan.get('hour', new Date(now.getTime() + 60_000)); assert.equal(calls, 1);
    await plan.get('hour', new Date(now.getTime() + 300_000)); assert.equal(calls, 2);
    const failed = new PersonalPlanService(h.manager, async () => { throw new Error('cli_missing'); });
    assert.deepEqual((await failed.get('day', now)).meetings, { state: 'unavailable', error: 'cli_missing', items: [] });
    assert.equal((await new PersonalPlanService(h.manager, async () => '{}').get('day', now)).meetings.state, 'unavailable');
    assert.deepEqual((await new PersonalPlanService(h.manager, async () => '{"data":{"items":[]}}').get('day', now)).meetings, { state: 'ok', items: [] });
  } finally { await h.close(); }
});

test('calendar reads Lark RSVP and links and tolerates individual malformed events', async () => {
  const h = await setup(); try {
    h.manager.lark.save({ enabled: true });
    const meeting = { ...event(), start_time: { datetime: '2026-11-13T20:20:00+08:00', timezone: 'Asia/Shanghai' },
      end_time: { datetime: '2026-11-13T21:20:00+08:00', timezone: 'Asia/Shanghai' }, self_rsvp_status: 'accept',
      app_link: 'https://applink.larkoffice.com/calendar/event', vchat: { meeting_url: 'https://example.com/meeting' } };
    const read = async (events: unknown[]) => new PersonalPlanService(h.manager, async () => JSON.stringify({ data: { events } })).calendar('hour', now);
    const result = await read([meeting, { ...meeting, event_id: 'declined', self_rsvp_status: 'decline' }, { event_id: 'bad' }]);
    assert.equal(result.state, 'ok'); assert.equal(result.items.length, 1);
    assert.equal(result.items[0].url, meeting.app_link);
    assert.equal(result.items[0].start, '2026-11-13T12:20:00.000Z');
    assert.equal((await read([{ ...meeting, app_link: undefined }])).items[0].url, meeting.vchat.meeting_url);
    assert.equal((await read([{ ...meeting, app_link: 'http://example.com' }])).items[0].url, undefined);
    assert.deepEqual(await read([{ event_id: 'bad' }]), { state: 'unavailable', error: 'cli_unexpected_event', items: [] });
    assert.deepEqual(await read([{ ...meeting, self_rsvp_status: 'decline' }]), { state: 'ok', items: [] });
  } finally { await h.close(); }
});

test('plan assigns urgent, due and goal rows, excludes done, caps after deduplication; horizon persists', async () => {
  const h = await setup(); try {
    const add = (id: string, fields = {}) => h.store.createTodo(id, id, null, fields);
    add('overdue', { dueAt: '2026-11-12T00:00:00.000Z' });
    add('pinned', { urgentUntil: '2026-11-14T00:00:00.000Z' });
    add('high', { importance: 'high', dueAt: '2026-11-13T15:00:00.000Z' });
    add('follow', { followUpAt: null }); h.store.updateTodo('follow', { followUpAt: '2026-11-13T14:00:00.000Z' });
    add('normal', { dueAt: '2026-11-13T16:00:00.000Z' });
    add('done', { dueAt: '2026-11-12T00:00:00.000Z' }); h.store.updateTodo('done', { done: true });
    const month = h.manager.personal.create({ text: 'month', horizon: 'month' });
    const week = h.manager.personal.create({ text: 'week', horizon: 'week', parentId: month.id });
    assert.equal(month.dueAt, horizonDeadline('month')); assert.equal(week.dueAt, horizonDeadline('week'));
    assert.equal(h.store.getTodo(week.id)?.horizon, 'week');
    assert.throws(() => h.manager.personal.create({ text: 'bad', horizon: 'year' }), /horizon/);
    const changed = h.manager.personal.update(week.id, { rev: week.rev, horizon: 'month', dueAt: '2026-11-28T00:00:00Z' });
    assert.equal(changed.horizon, 'month'); assert.equal(changed.dueAt, '2026-11-28T00:00:00.000Z');
    const plan = await h.manager.personal.plan.get('day', now);
    assert.deepEqual(plan.urgent.map(item => item.id), ['overdue', 'high', 'pinned']);
    assert.deepEqual(plan.due.map(item => item.id), ['follow', 'normal']);
    for (let i = 0; i < 25; i++) add(`due${i}`, { dueAt: '2026-11-13T17:00:00.000Z' });
    assert.equal((await h.manager.personal.plan.get('day', now)).due.length, 20);
    h.store.updateTodo(week.id, { horizon: 'week', dueAt: '2026-11-15T23:59:00.000Z' });
    assert.ok((await h.manager.personal.plan.get('month', now)).goals.some(row => row.id === week.id));
    assert.ok((await h.manager.personal.plan.get('hour', now)).goals.some(row => row.id === week.id));
    assert.ok(!(await h.manager.personal.plan.get('week', now)).due.some(row => row.id === week.id), 'a goal is listed once, under goals, not again as due');
  } finally { await h.close(); }
});

test('upcoming deltas retain calendar failure, bound meetings and deadlines independently', async () => {
  const h = await setup(); try {
    h.manager.lark.save({ enabled: true });
    let failure = false;
    const plan = new PersonalPlanService(h.manager, async () => { if (failure) throw new Error('cli_failed'); return JSON.stringify({ data: { items: Array.from({ length: 8 }, (_, i) => event(`m${i}`)) } }); });
    for (let i = 0; i < 8; i++) h.store.createTodo(`d${i}`, `deadline${i}`, null, { dueAt: '2026-11-13T13:00:00.000Z' });
    const upcoming = await plan.upcoming(now);
    assert.equal(upcoming.length, 10);
    const first = personalContext(h.store, h.manager, 'assistant', now, upcoming); first.commit();
    assert.match(personalContext(h.store, h.manager, 'assistant', now, upcoming).text, /没有变化/);
    failure = true;
    const later = new Date(now.getTime() + 300_000);
    const next = personalContext(h.store, h.manager, 'assistant', later, await plan.upcoming(later));
    assert.match(next.text, /日程读取失败/); assert.match(next.text, /cli_failed/); assert.doesNotMatch(next.text, /已不再接下来/);
  } finally { await h.close(); }
});

test('prep only triggers recurring or named meetings once across restarts and retries failed delivery', async () => {
  const h = await setup(); try {
    h.manager.lark.save({ enabled: true });
    const named = { ...event('named'), recurrence: undefined }, ignored = { ...event('ignored'), recurrence: undefined };
    Object.assign(h.manager.personal, { plan: new PersonalPlanService(h.manager, async () => JSON.stringify({ data: { events: [event(), named, ignored] } })) });
    const received: string[] = [];
    h.manager.personal.router.receiveMeetingPrep = async meeting => { received.push(meeting.id); return { threadId: 'assistant' }; };
    assert.deepEqual(h.manager.personal.prep.settings(), { enabled: true, leadMin: 30 });
    h.manager.personal.prep.add('named');
    await Promise.all([h.manager.personal.prep.tick(now), h.manager.personal.prep.tick(now)]);
    assert.deepEqual(received, ['meeting', 'named']);
    await new PersonalPrep(h.manager.personal).tick(new Date(now.getTime() + 300_000));
    assert.equal(received.length, 2);
    h.manager.personal.prep.add('ignored');
    let fail = true;
    h.manager.personal.router.receiveMeetingPrep = async meeting => { if (fail) throw new Error('unavailable'); received.push(meeting.id); return { threadId: 'assistant' }; };
    await assert.rejects(new PersonalPrep(h.manager.personal).tick(now), /unavailable/);
    fail = false; await new PersonalPrep(h.manager.personal).tick(now); assert.equal(received.at(-1), 'ignored');
    assert.throws(() => h.manager.personal.prep.save({ leadMin: 0 }), /leadMin/);
    h.manager.personal.prep.save({ enabled: false });
    await new PersonalPrep(h.manager.personal).tick(now); assert.equal(received.length, 3);
  } finally { await h.close(); }
});

 test('an existing todo database migrates horizon to null without changing the item', () => {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-horizon-migration-'));
  let store = new Store(dir);
  try {
    store.createTodo('old', 'Existing item', null, { dueAt: '2026-11-13T12:00:00.000Z' });
    store.db.exec('ALTER TABLE todos DROP COLUMN horizon'); store.close();
    store = new Store(dir);
    assert.equal(store.getTodo('old')?.horizon, null);
    assert.equal(store.getTodo('old')?.dueAt, '2026-11-13T12:00:00.000Z');
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});
