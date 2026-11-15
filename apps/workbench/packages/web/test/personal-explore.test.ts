import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ExploreRecommendation } from '../src/api.js';
import { exploreChanges, exploreCounts, exploreDraft, explorePayloads, exploreSummary, needsExplore, type ExploreSnapshot } from '../src/personal-explore.js';
import { POST_MEETING_EVENTS } from '../src/lark.js';

const current = (): ExploreSnapshot => ({
  personal: { agent: 'agent-a', model: null, directory: '/workspace', directoryIsDefault: true },
  lark: { enabled: false, people: [{ id: 'old', name: 'Already followed', role: 'boss' }], chats: [{ id: 'old-chat', name: 'Existing group' }], intervalMin: 30, events: ['unrelated.event'], workHours: { start: 9, end: 18, timeZone: 'UTC' } },
  brief: { enabled: false, morning: '09:00', evening: '19:00' },
  push: { enabled: false, recipient: 'recipient', baseUrl: 'https://example.test', waiting: true, failure: true, notice: false, scheduled: false },
  proactive: { mode: 'off', enabled: false, repos: ['/workspace'] },
  profile: { text: 'My own words', rev: 7, isTemplate: false, path: 'profile.md', updatedAt: null },
});
const recommendation = (): ExploreRecommendation => ({
  agent: { agent: 'agent-b', model: 'small', options: [{ agent: 'agent-b', model: 'small', label: 'Agent B', why: 'Available', recommended: true }] },
  profile: { draft: 'Suggested notes', replace: false, why: 'From conversations' },
  people: [{ id: 'new', name: 'New person', selected: true, role: 'pm', why: 'Frequent contact', evidence: ['Three conversations'] }],
  chats: [{ id: 'new-chat', name: 'New group', selected: false, why: 'Active', evidence: [] }],
  meetings: { on: true, why: 'Recurring', examples: ['Weekly meeting'] },
  intervalMin: { value: 60, why: 'Normal pace' }, brief: { enabled: true, morning: '08:30', evening: '18:30', why: 'Active hours' },
  push: { available: true, enabled: true, why: 'Recipient set' }, proactive: { mode: 'observe', why: 'Start by observing' },
});

test('setup detection distinguishes unknown readings, an unset agent and an untouched empty setup', () => {
  const s = current();
  assert.equal(needsExplore(null, null, null), null);
  assert.equal(needsExplore({ ...s.personal, agent: null }, null, null), true);
  assert.equal(needsExplore(s.personal, null, s.lark), null);
  assert.equal(needsExplore(s.personal, false, null), false);
  assert.equal(needsExplore(s.personal, true, null), null);
  assert.equal(needsExplore(s.personal, true, { ...s.lark, people: [], chats: [] }), true);
  assert.equal(needsExplore(s.personal, true, s.lark), false);
});

test('re-exploring preserves existing subscriptions and never silently replaces personal notes', () => {
  const s = current(), rec = recommendation();
  rec.profile.replace = true; // Even a stale recommendation cannot overwrite a newly edited profile.
  const draft = exploreDraft(rec, s);
  assert.equal(draft.profile.replace, false);
  assert.equal(draft.people.find(p => p.id === 'old')?.selected, true);
  assert.equal(draft.people.find(p => p.id === 'old')?.role, 'boss');
  assert.equal(draft.chats.find(p => p.id === 'old-chat')?.selected, true);
  assert.equal(rec.people.length, 1, 'the recommendation is not mutated');
  assert.deepEqual(exploreCounts(draft), { people: 2, chats: 1 });
  assert.match(exploreSummary(draft), /2/);
  assert.ok(!exploreChanges(draft, s).includes('profile'));
});

test('payloads include selected items and preserve unrelated settings and profile revision', () => {
  const s = current(), draft = exploreDraft(recommendation(), s);
  draft.people.find(p => p.id === 'old')!.selected = false;
  draft.profile.replace = true;
  const next = explorePayloads(draft, s);
  assert.equal(next.personal.directory, null, 'the default directory stays the default');
  assert.deepEqual(next.lark.people, [{ id: 'new', name: 'New person', role: 'pm' }]);
  assert.deepEqual(next.lark.chats, [{ id: 'old-chat', name: 'Existing group' }]);
  assert.equal(next.lark.enabled, true);
  assert.equal(next.lark.intervalMin, 60);
  assert.deepEqual(next.lark.workHours, s.lark.workHours);
  assert.deepEqual(next.lark.events, ['unrelated.event', ...POST_MEETING_EVENTS]);
  assert.deepEqual(next.push, { ...s.push, enabled: true });
  assert.deepEqual(next.proactive, { mode: 'observe', repos: ['/workspace'] });
  assert.deepEqual(next.profile, { text: 'Suggested notes', rev: 7 });
  assert.ok(exploreChanges(draft, s).includes('profile'));
  draft.meetings.on = false;
  assert.deepEqual(explorePayloads(draft, { ...s, lark: next.lark }).lark.events, ['unrelated.event']);
});

test('retry skips unchanged successful items and includes edits made after a failure', () => {
  const s = current(), draft = exploreDraft(recommendation(), s);
  const next = explorePayloads(draft, s);
  const afterPartial: ExploreSnapshot = { ...s, personal: next.personal, lark: next.lark, brief: next.brief, proactive: { ...next.proactive, enabled: true } };
  assert.deepEqual(exploreChanges(draft, afterPartial), ['push']);
  draft.intervalMin.value = 120;
  assert.deepEqual(exploreChanges(draft, afterPartial), ['lark', 'push']);
  const afterAll = { ...afterPartial, lark: explorePayloads(draft, afterPartial).lark, push: next.push };
  assert.deepEqual(exploreChanges(draft, afterAll), []);
});

test('unavailable push is never enabled, and an untouched template may receive its draft', () => {
  const s = current(); s.profile.isTemplate = true;
  const rec = recommendation(); rec.push.available = false; rec.profile.replace = true;
  const draft = exploreDraft(rec, s);
  assert.equal(draft.push.enabled, false);
  assert.equal(explorePayloads({ ...draft, push: { ...draft.push, enabled: true } }, s).push.enabled, false);
  assert.ok(exploreChanges(draft, s).includes('profile'));
});

test('the four explore API methods use the documented verbs, paths and source payload', async t => {
  const { Api } = await import('../src/api.js');
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method!, body: init.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(null), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  const api = new Api('test-token');
  await api.exploreSources(); await api.startExplore(['calli-threads']); await api.exploreStatus(); await api.cancelExplore();
  assert.deepEqual(calls, [
    { url: '/api/personal/explore/sources', method: 'GET', body: null },
    { url: '/api/personal/explore', method: 'POST', body: { sources: ['calli-threads'] } },
    { url: '/api/personal/explore', method: 'GET', body: null },
    { url: '/api/personal/explore', method: 'DELETE', body: null },
  ]);
});
