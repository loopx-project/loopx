import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { changedFiles, mentionedFiles, pathsIn, checkMissingFiles, type DiffEvent, type ThreadOutputs } from '../src/outputs.js';
import { diffStats } from '../src/history-diff.js';
import { DatabaseSync } from 'node:sqlite';
import { Store } from '../src/store.js';

const diff = (path: string, oldText: string | null, newText: string) => ({ type: 'diff', path, oldText, newText });
const summary = (path: string, oldText: string | null, newText: string) => ({ path, created: oldText === null, ...diffStats(oldText, newText) });

test('changed files sum every tool call over all turns, once per call, newest first', () => {
  const changes = changedFiles([
    // The same call reported twice: only its last report counts.
    { turnId: 't1', seq: 1, ts: '2026-01-01T00:00:01Z', data: { id: 'a', files: [summary('src/a.ts', 'x\n', 'y\n')] } },
    { turnId: 't1', seq: 2, ts: '2026-01-01T00:00:02Z', data: { id: 'a', files: [summary('src/a.ts', 'x\n', 'y\nz\n')] } },
    { turnId: 't1', seq: 3, ts: '2026-01-01T00:00:03Z', data: { id: 'b', files: [summary('/w/new.md', null, 'one\ntwo\n')] } },
    { turnId: 't2', seq: 4, ts: '2026-01-01T00:01:00Z', data: { id: 'a', files: [summary('/w/src/a.ts', 'z\n', '')] } },
    { turnId: null, seq: 5, ts: '2026-01-01T00:02:00Z', data: { id: 'c', files: [summary('/w/stray.ts', 'a', 'b')] } },
    { turnId: 't2', seq: 6, ts: '2026-01-01T00:02:00Z', data: { id: 'd' } },
  ], '/w');
  assert.equal(changes.total, 2);
  assert.deepEqual(changes.files.map(f => f.path), ['/w/src/a.ts', '/w/new.md']);
  const a = changes.files[0];
  assert.deepEqual({ added: a.added, removed: a.removed, turns: a.turns, turnId: a.turnId, created: a.created }, { added: 2, removed: 2, turns: 2, turnId: 't2', created: false });
  assert.equal(changes.files[1].created, true);
  assert.equal(changes.added, 4);
  assert.equal(changes.removed, 2);
});

test('diff references retain three calls across two turns in final log order', () => {
  const edit = (turnId: string, id: number, seq: number): DiffEvent => ({
    turnId, seq, ts: String(100 - seq), data: { id: String(id), files: [{ ...summary('a.ts', 'old', 'new'), detail: { turnId, id, version: seq } }] },
  });
  const last = edit('t1', 1, 3);
  last.data.files!.push({ ...last.data.files![0], path: '/w/a.ts' });
  const [file] = changedFiles([edit('t1', 1, 1), edit('t1', 2, 2), last, edit('t2', 1, 4)], '/w').files;
  assert.deepEqual(file.details, [
    { turnId: 't1', id: 2, version: 2 },
    { turnId: 't1', id: 1, version: 3 },
    { turnId: 't2', id: 1, version: 4 },
  ]);
  assert.equal(file.turns, 2);
  assert.equal(Object.hasOwn(file, 'detailsTotal'), false);
});

test('diff references retain the latest 20 and count omitted calls, not repeated reports', () => {
  const events: DiffEvent[] = Array.from({ length: 25 }, (_, i) => ({
    turnId: 't', seq: i + 1, ts: 'same', data: { id: String(i), files: [{ ...summary('a.ts', 'old', 'new'), detail: { turnId: 't', id: i + 1, version: i + 1 } }] },
  }));
  const exact = changedFiles(events.slice(0, 20), '/w').files[0];
  assert.equal(exact.details.length, 20);
  assert.equal(Object.hasOwn(exact, 'detailsTotal'), false);
  events.push({ turnId: 't', seq: 26, ts: 'same', data: { id: '0', files: [{ ...summary('a.ts', 'old', 'final'), detail: { turnId: 't', id: 1, version: 26 } }] } });
  const [file] = changedFiles(events, '/w').files;
  assert.equal(file.detailsTotal, 25);
  assert.deepEqual(file.details.map(d => d.version), Array.from({ length: 20 }, (_, i) => i + 7));
  assert.equal(file.details.at(-1)!.id, 1);
});

test('answers yield pictures by any mention and files only when handed over', () => {
  assert.deepEqual(pathsIn('see [a](<my file.md>), `/w/b.ts:12` and https://x.io/c.png').map(p => p.path), ['my file.md', '/w/b.ts']);
  assert.deepEqual(pathsIn('`/tmp/中文 图 (1).png` and /tmp/README; <file:///tmp/my%20report.md#L2> [remote](file://host/a.png)').map(p => p.path), ['/tmp/中文 图 (1).png', '/tmp/my report.md', '/tmp/README']);
  const { images, files } = mentionedFiles([
    { turnId: 't1', seq: 7, ts: '1', text: '![shot](shot.png) and [report](/w/out/report.html) and src/talk.ts in passing' },
    { turnId: 't2', seq: 8, ts: '2', text: 'saved /tmp/chart.svg, wrote `/w/data.csv`; your picture /d/attachments/0b6c1c1e-6a8e-4b8b-9c1d-111111111111/0b6c1c1e-6a8e-4b8b-9c1d-222222222222.png' },
    { turnId: 't3', seq: 9, ts: '3', text: 'again: [report](file:///w/out/report.html#L3)' },
  ], '/w');
  assert.deepEqual(images.map(i => i.path), ['/tmp/chart.svg', '/w/shot.png']);
  assert.deepEqual(files.map(f => [f.path, f.turnId]), [['/w/out/report.html', 't3'], ['/w/data.csv', 't2']]);
});

test('closing markup tags are not file references; explicit paths remain supported', () => {
  const text = '<calli-tools>Use calli tools search.</calli-tools>\n<calli-progress>Report progress.</calli-progress>\n<div>answer<br/></div >';
  assert.deepEqual(pathsIn(text), []);
  for (const tag of ['</calli-tools>', '</calli-progress>', '<br/>']) assert.deepEqual(pathsIn(tag), [], tag);
  assert.deepEqual(mentionedFiles([{ turnId: 't1', seq: 1, ts: '1', text }], '/w'), { images: [], files: [] });
  assert.deepEqual(pathsIn('[root](</README>) <file:///tmp/report.md> </tmp/report.md> `/README`').map(f => f.path), ['/README', '/tmp/report.md', '/tmp/report.md', '/README']);
  assert.deepEqual(pathsIn('</srv/out/report.html> <file:///tmp/a b.png> </srv/README> </report.html> <file:///README>').map(f => f.path), ['/srv/out/report.html', '/tmp/a b.png', '/srv/README', '/report.html', '/README']);
});

test('directory and abbreviated paths are not output files', () => {
  const omitted = [
    '.', '..', '…', '...', './', '../', '/', '/a/b/', '/a/b/.', '/a/b/..', '/data/…', '/a/b/...',
    '/a/…/b.py', '/a/truncated…name/b.py', '/a/.../b', '/a/.../b.py', 'out/.../report.html',
    '/a/---', '/a/___', '/a/📄', '/a/.#L2', '/a/...:12',
  ];
  for (const path of omitted) {
    for (const text of [path, '`' + path + '`', `[x](<${path}>)`]) {
      assert.deepEqual(pathsIn(text), [], text);
      assert.deepEqual(mentionedFiles([{ turnId: 't1', seq: 1, ts: '1', text }], '/w'), { images: [], files: [] }, text);
    }
    if (path.startsWith('/')) assert.deepEqual(pathsIn(`[x](file://${encodeURI(path)})`), [], path);
  }
});

test('ordinary paths retain letters, numbers, dotfiles and real parent-directory segments', () => {
  assert.deepEqual(pathsIn('/a/b.py [x](out/report.html)').map(f => f.path), ['out/report.html', '/a/b.py']);
  for (const path of ['/a/b.py', 'out/report.html', './out/report.html', '../report.html', '/a/../b.py', '/a/.env', '/a/报告', '/a/123', '/a/report...html']) {
    assert.deepEqual(pathsIn(`[x](<${path}>)`), [{ path, linked: true }], path);
  }
});

test('wildcard expressions are never output paths, including encoded and unquoted patterns', () => {
  for (const path of ['/tmp/codex-preview-*.png', '/tmp/chart?.png', '/tmp/chart.png?', '/tmp/chart[12].png', '/tmp/{first,second}.png', 'out/chart*.png', 'out/chart.png?', 'out/chart[12].png']) {
    for (const text of [path, '`' + path + '`', `[x](${path})`, `[x](${encodeURI(path).replaceAll('?', '%3F')})`]) {
      assert.deepEqual(pathsIn(text), [], text);
    }
  }
});

test('home-relative paths expand before resolving against the thread directory', () => {
  for (const text of ['~/out/chart.png', '`~/out/chart.png`', '[chart](~/out/chart.png)', '[chart](%7E/out/chart.png)']) {
    assert.equal(pathsIn(text)[0].path, join(homedir(), 'out/chart.png'));
    assert.equal(mentionedFiles([{ turnId: 't', seq: 1, ts: '1', text }], '/w').images[0].path, join(homedir(), 'out/chart.png'));
  }
  assert.deepEqual(pathsIn('~/README'), [{ path: join(homedir(), 'README'), linked: false }]);
  assert.deepEqual(pathsIn('`~/my report.md`'), [{ path: join(homedir(), 'my report.md'), linked: false }]);
  assert.deepEqual(pathsIn('[home](~) ~ `~`'), []);
  assert.equal(mentionedFiles([{ turnId: 't', seq: 1, ts: '1', text: '~/README' }], '/w').files[0].path, join(homedir(), 'README'));
  const changes = changedFiles([{ turnId: 't', seq: 1, ts: '1', data: { id: 'e', files: [summary('~/out/report.html', null, 'new')] } }], '/w');
  assert.equal(changes.files[0].path, join(homedir(), 'out/report.html'));
});

test('disk checks mark missing files and directories, then clear stale missing flags', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-outputs-disk-'));
  const present = join(dir, 'present.txt');
  const absent = join(dir, 'absent.txt');
  const folder = join(dir, 'folder');
  writeFileSync(present, 'present');
  mkdirSync(folder);
  const paths = [present, absent, folder, join(present, 'child.txt')];
  const outputs: ThreadOutputs = {
    changes: { total: paths.length, added: 4, removed: 0, files: paths.map(path => ({ path, added: 1, removed: 0, created: true, turns: 1, turnId: 't', at: '1', details: [] })) },
    images: paths.map(path => ({ path, turnId: 't', at: '1' })),
    files: paths.map(path => ({ path, turnId: 't', at: '1' })), seq: 1,
  };
  try {
    const checked = await checkMissingFiles(outputs);
    for (const list of [checked.changes.files, checked.images, checked.files]) {
      assert.equal(Object.hasOwn(list[0], 'missing'), false);
      assert.deepEqual(list.slice(1).map(f => f.missing), [true, true, true]);
    }
    assert.equal(Object.hasOwn(outputs.files[1], 'missing'), false, 'checks do not mutate a previously returned snapshot');
    writeFileSync(absent, 'restored');
    rmSync(present);
    const refreshed = await checkMissingFiles(checked);
    for (const list of [refreshed.changes.files, refreshed.images, refreshed.files]) {
      assert.equal(list[0].missing, true);
      assert.equal(Object.hasOwn(list[1], 'missing'), false);
    }
    assert.equal(checked.files[1].missing, true);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('files the thread created count as output even when no answer names them', () => {
  const created = [
    { seq: 1, path: '/w/gen/logo.png', added: 0, removed: 0, created: true, turns: 1, turnId: 't1', at: '2026-01-01T00:00:00Z', details: [] },
    { seq: 2, path: '/w/notes.md', added: 3, removed: 0, created: true, turns: 1, turnId: 't2', at: '2026-01-02T00:00:00Z', details: [] },
    { seq: 2, path: '/w/src/a.ts', added: 1, removed: 1, created: false, turns: 1, turnId: 't2', at: '2026-01-02T00:00:00Z', details: [] },
  ];
  const { images, files } = mentionedFiles([{ turnId: 't3', seq: 10, ts: '2026-01-03T00:00:00Z', text: 'see [notes](/w/notes.md)' }], '/w', created);
  assert.deepEqual(images.map(i => i.path), ['/w/gen/logo.png']);
  assert.deepEqual(files.map(f => [f.path, f.turnId]), [['/w/notes.md', 't3']]);
});

test('the store reads diffs from the log and answers from the finished turns', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-outputs-'));
  const store = new Store(dir);
  try {
    store.createThread({ id: 'th', agent: 'pi', cwd: dir, title: 'th', agentSessionId: null, permissionMode: 'default', model: null, effort: null });
    store.appendEvent('th', 't1', 'turn.started', { text: 'go' });
    store.appendEvent('th', 't1', 'tool.update', { id: 'e', content: [diff('a.ts', 'one\n', 'two\n')] });
    store.appendEvent('th', 't1', 'tool.update', { id: 'r', output: 'plain' });
    store.appendEvent('th', 't1', 'message.delta', { text: 'done, see ![x](x.png)' });
    store.appendEvent('th', 't1', 'turn.done', {});
    const sources = await store.outputSources('th');
    assert.equal(sources.diffs.filter(e => e.data.files?.length).length, 1);
    assert.ok(!JSON.stringify(sources.diffs).includes('oldText'));
    assert.ok(!JSON.stringify(sources.diffs).includes('plain'));
    assert.equal(sources.answers.length, 1);
    assert.match(sources.answers[0].text, /x\.png/);
    assert.ok(sources.seq >= 5);
  } finally {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('log order wins over timestamps, including answers that begin before an edit', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-outputs-'));
  const store = new Store(dir);
  const db = new DatabaseSync(join(dir, 'hub.db'));
  try {
    store.createThread({ id: 'th', agent: 'pi', cwd: dir, title: 'th', agentSessionId: null, permissionMode: 'default', model: null, effort: null });
    store.appendEvent('th', 't1', 'turn.started', { text: 'go' });
    const before = store.appendEvent('th', 't1', 'message.delta', { text: 'starting ' });
    const edit = store.appendEvent('th', 't1', 'tool.update', { id: 'e', content: [diff('a.md', null, 'one\n')] });
    const answer = store.appendEvent('th', 't1', 'message.delta', { text: '[a](a.md) [b](b.md)' });
    store.appendEvent('th', 't1', 'turn.done', {});
    // Imported/copy histories can have coarse timestamps; wall clocks can also move backwards.
    for (const [seq, ts] of [[before.seq, '2026-01-03'], [edit.seq, '2026-01-02'], [answer.seq, '2026-01-01']]) {
      db.prepare('UPDATE events SET ts=? WHERE thread_id=? AND seq=?').run(ts, 'th', seq);
      db.prepare('UPDATE history_rows SET ts=? WHERE thread_id=? AND seq=?').run(ts, 'th', seq);
    }
    store.indexText('th');
    const sources = await store.outputSources('th');
    assert.equal(sources.answers[0].seq, answer.seq);
    assert.equal(sources.answers[0].ts, '2026-01-01');
    const changes = changedFiles(sources.diffs, dir);
    const outputs = mentionedFiles(sources.answers, dir, changes.files);
    assert.deepEqual(outputs.files.map(f => [f.path, f.at]), [[join(dir, 'b.md'), '2026-01-01'], [join(dir, 'a.md'), '2026-01-01']]);
    const newer: DiffEvent = { turnId: 't2', seq: answer.seq + 1, ts: '2025-01-01', data: { id: 'e', files: [summary('a.md', 'one\n', 'two\n')] } };
    const updated = changedFiles([...sources.diffs, newer], dir).files[0];
    assert.equal(updated.turnId, 't2');
    assert.equal(updated.created, true);
  } finally { db.close(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('relative paths and cwd symlink spellings name the same file', () => {
  const changes = changedFiles([
    { turnId: 't1', seq: 1, ts: '1', data: { id: 'a', files: [summary('out/a.md', null, 'a')] } },
    { turnId: 't2', seq: 2, ts: '2', data: { id: 'a', files: [summary('/alias/out/a.md', 'a', 'b')] } },
    { turnId: 't2', seq: 3, ts: '3', data: { id: 'b', files: [summary('/real/work/out/a.md', 'b', '')] } },
  ], '/alias', '/real/work');
  assert.equal(changes.total, 1);
  assert.equal(changes.files[0].path, '/real/work/out/a.md');
  assert.equal(changes.files[0].turns, 2);
  const outputs = mentionedFiles([{ turnId: 't3', seq: 4, ts: '4', text: '[a](/alias/out/a.md) [a](out/a.md) [sibling](../b.md) [other](/alias-other/c.md)' }], '/alias', changes.files, '/real/work');
  assert.deepEqual(outputs.files.map(f => f.path), ['/alias-other/c.md', '/real/b.md', '/real/work/out/a.md']);
});

test('forks and cold histories build only tools and keep all copied turns in order', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-outputs-'));
  const store = new Store(dir);
  const db = new DatabaseSync(join(dir, 'hub.db'));
  try {
    for (const id of ['source', 'fork', 'cut']) store.createThread({ id, agent: 'pi', cwd: dir, title: id, agentSessionId: null, permissionMode: 'default', model: null, effort: null });
    let cut = 0;
    for (let i = 0; i < 12; i++) {
      const turn = `t${i}`;
      store.appendEvent('source', turn, 'turn.started', { text: `turn ${i}` });
      store.appendEvent('source', turn, 'tool.update', { id: 'same-id', content: [diff(`out/${i}.md`, null, 'one\n')] });
      store.appendEvent('source', turn, 'message.delta', { text: `[file](out/${i}.md)` });
      const done = store.appendEvent('source', turn, 'turn.done', {});
      if (i === 1) cut = done.seq;
    }
    store.copyEvents('source', 'fork');
    store.copyEvents('source', 'cut', cut);
    const fork = await store.outputSources('fork');
    const source = await store.outputSources('source');
    assert.deepEqual(changedFiles(fork.diffs, dir), changedFiles(source.diffs, dir));
    assert.ok(changedFiles(fork.diffs, dir).files.every(f => f.details.length === 1));
    assert.equal(fork.answers.length, 12);
    assert.equal((await store.outputSources('cut')).answers.length, 2);
    assert.equal(changedFiles((await store.outputSources('cut')).diffs, dir).total, 2);
    assert.equal((db.prepare("SELECT COUNT(*) AS n FROM history_rows WHERE thread_id='fork' AND type NOT LIKE 'history.tool.%'").get() as { n: number }).n, 0, 'the overview does not build unrelated history details');
    // Raw payloads are unavailable: warm reads must still use the small projections alone.
    db.prepare("UPDATE events SET data='unreadable raw tool payload' WHERE thread_id='fork' AND type='tool.update'").run();
    assert.deepEqual(changedFiles((await store.outputSources('fork')).diffs, dir), changedFiles(fork.diffs, dir));
  } finally { db.close(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('older projections upgrade only creation flags and keep large diff bodies out of sources', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-outputs-'));
  const store = new Store(dir);
  const db = new DatabaseSync(join(dir, 'hub.db'));
  try {
    store.createThread({ id: 'th', agent: 'pi', cwd: dir, title: 'th', agentSessionId: null, permissionMode: 'default', model: null, effort: null });
    store.appendEvent('th', 't1', 'turn.started', { text: 'go' });
    const ev = store.appendEvent('th', 't1', 'tool.update', { id: 'e', content: [diff('new.md', null, 'x'.repeat(2_000_000)), diff('empty.md', '', 'one')] });
    db.prepare("UPDATE history_rows SET data=json_remove(data, '$.files[0].created', '$.files[1].created') WHERE thread_id=? AND seq=?").run('th', ev.seq);
    const sources = await store.outputSources('th');
    assert.ok(JSON.stringify(sources).length < 1000);
    assert.deepEqual(sources.diffs[0].data.files?.map(f => f.created), [true, false]);
    db.prepare("UPDATE events SET data='unreadable raw tool payload' WHERE thread_id=? AND seq=?").run('th', ev.seq);
    assert.deepEqual((await store.outputSources('th')).diffs, sources.diffs, 'upgraded flags are saved');
  } finally { db.close(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});
