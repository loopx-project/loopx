import test from 'node:test';
import assert from 'node:assert/strict';
import { collectTags, parseTitleTags } from '../src/tags';
import { emptyFilters, matchesFilters, parseFilters } from '../src/columns';
import type { ThreadSummary } from '@calli/extension-api';

test('extracts only consecutive leading prefixes and preserves a tag-only title', () => {
  assert.deepEqual(parseTitleTags('[周报]看看最新的看板'), { title: '看看最新的看板', tags: ['周报'] });
  assert.deepEqual(parseTitleTags('  [ Flux ] 【发布】 [FLUX] 推动发布'), { title: '推动发布', tags: ['Flux', '发布'] });
  assert.deepEqual(parseTitleTags('标题 [object Object]'), { title: '标题 [object Object]', tags: [] });
  assert.deepEqual(parseTitleTags('[周报] '), { title: '[周报] ', tags: ['周报'] });
  assert.deepEqual(parseTitleTags('[周报] 内容 [发布]'), { title: '内容 [发布]', tags: ['周报'] });
});

test('rejects empty, overlong, nested or mismatched prefixes without stripping text', () => {
  for (const title of ['[] title', '[  ] [ok] title', '[a[b]] title', '【a】】', '[a】 title', `[${'a'.repeat(21)}] title`]) {
    if (title === '【a】】') assert.deepEqual(parseTitleTags(title), { title: '】', tags: ['a'] });
    else assert.deepEqual(parseTitleTags(title), { title, tags: [] });
  }
  assert.deepEqual(parseTitleTags(`[${'文'.repeat(20)}] x`).tags, ['文'.repeat(20)]);
  assert.deepEqual(parseTitleTags('[ok] [] [later] x'), { title: '[] [later] x', tags: ['ok'] });
  assert.deepEqual(parseTitleTags(`[${'𠮷'.repeat(20)}] x`).tags, ['𠮷'.repeat(20)]);
});

test('counts each thread once, keeps first spelling, excludes archived and sorts count then name', () => {
  const titles = ['[Flux][flux][B] one', '[FLUX][a] two', '[b] three', '[a] four'];
  assert.deepEqual(collectTags([...titles.map(title => ({ title, archived: false })), { title: '[hidden]', archived: true }]), [
    { key: 'a', label: 'a', count: 2 }, { key: 'b', label: 'B', count: 2 }, { key: 'flux', label: 'Flux', count: 2 },
  ]);
});

test('old and malformed persisted preferences remain usable and tag values are normalized', () => {
  assert.deepEqual(parseFilters('{"project":[],"agent":[],"scheduled":[]}'), emptyFilters());
  for (const tags of [null, false, {}, 12]) assert.deepEqual(parseFilters(JSON.stringify({ tags })), emptyFilters());
  assert.deepEqual(parseFilters(JSON.stringify({ tags: [' Flux ', 'FLUX', '', '[bad]', null, 1, 'x'.repeat(21)] })).tags, ['flux']);
});

test('tag filtering is case insensitive, OR within tags and AND across filter groups', () => {
  const thread = { title: '[Flux][周报] Work', project: '/p', agent: 'a', scheduled: true } as ThreadSummary;
  assert.ok(matchesFilters(thread, { ...emptyFilters(), tags: ['other', 'FLUX'], project: ['/p'], scheduled: [true] }));
  assert.equal(matchesFilters(thread, { ...emptyFilters(), tags: ['other'] }), false);
  assert.equal(matchesFilters(thread, { ...emptyFilters(), tags: ['flux'], agent: ['b'] }), false);
  assert.equal(matchesFilters({ ...thread, title: 'Work [Flux]' }, { ...emptyFilters(), tags: ['flux'] }), false);
});

test('first spelling follows creation order rather than changing with recent activity', () => {
  assert.deepEqual(collectTags([
    { title: '[flux] new', archived: false, createdAt: '2026-02-01' },
    { title: '[Flux] old', archived: false, createdAt: '2026-01-01' },
  ]), [{ key: 'flux', label: 'Flux', count: 2 }]);
});
