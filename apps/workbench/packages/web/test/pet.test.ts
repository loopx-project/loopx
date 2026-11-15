import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyPetStarter, clearPetHaze, petBustBox, nextPetFrame, petCacheKey, petCacheName, petClampPosition, petErrorDetail, petLicenseUrl, petAdvice, petBriefAdvice, petBriefWanted, petFailureKind, petTriage, petReplyParts, petRing, petSpeaksOf, parsePetPending, petDay, petGreetingLine, petReplyPreview, petThreadState, PET_GIVE_UP_MS, PET_QUIET_POLLS, readPetRouter, yawnDelay } from '../src/pet.js';

test('a looping clip wraps and a one-shot clip ends', () => {
  assert.equal(nextPetFrame(0, 121, true), 1);
  assert.equal(nextPetFrame(120, 121, true), 0);
  assert.equal(nextPetFrame(119, 121, false), 120);
  assert.equal(nextPetFrame(120, 121, false), null);
});

test('only the frames just ahead stay decoded', () => {
  assert.deepEqual(petRing(0, 121, true, 4), [0, 1, 2, 3]);
  assert.deepEqual(petRing(119, 121, true, 4), [119, 120, 0, 1]);
  assert.deepEqual(petRing(119, 121, false, 4), [119, 120]);
  assert.deepEqual(petRing(0, 1, true, 8), [0]);
});

test('cleaned frames are filed under the artwork they came from', () => {
  assert.notEqual(petCacheName('dafeiyu', 'aaa'), petCacheName('dafeiyu', 'bbb'));
  assert.match(petLicenseUrl('aaa'), /\/blob\/aaa\//);
  assert.equal(petCacheKey('idle', 7), '/calli-pet-frames/idle/7.webp');
});

test('she yawns between 40 and 80 seconds', () => {
  assert.equal(yawnDelay(() => 0), 40_000);
  assert.equal(yawnDelay(() => 1), 80_000);
});

test('the day turns over at local midnight', () => {
  assert.equal(petDay(new Date(2026, 9, 31, 23, 59)), '2026-10-31');
  assert.equal(petDay(new Date(2026, 10, 1, 0, 1)), '2026-11-01');
});

test('the greeting names what is most urgent, and is light when nothing is', () => {
  const now = new Date(2026, 9, 31);
  const busy = petGreetingLine({ you: 3, first: 'OKR 自评' }, now);
  assert.match(busy, /3/);
  assert.match(busy, /OKR 自评/);
  const calm = petGreetingLine({ you: 0, first: null }, now);
  assert.ok(calm.length > 0);
  assert.doesNotMatch(calm, /0/);
  assert.doesNotMatch(petGreetingLine({ you: 2, first: null }, now), /null/);
});

test('an opening goes in front of the words and replaces another opening', () => {
  const starts = ['记一下：', '这件最急：'];
  assert.equal(applyPetStarter('', '记一下：', starts), '记一下：');
  assert.equal(applyPetStarter('买票', '记一下：', starts), '记一下：买票');
  assert.equal(applyPetStarter('记一下：买票', '这件最急：', starts), '这件最急：买票');
});

test('a message in flight survives a reload until it is too old to wait for', () => {
  const sentAt = 1_000;
  const raw = JSON.stringify({ since: 'a', threadId: 't', sentAt, text: '买票' });
  assert.deepEqual(parsePetPending(raw, sentAt + 1), { since: 'a', threadId: 't', sentAt, text: '买票' });
  assert.equal(parsePetPending(raw, sentAt + PET_GIVE_UP_MS + 1), null);
  assert.equal(parsePetPending('not json', 0), null);
  assert.equal(parsePetPending('{"since":null}', 0), null);
  assert.equal(parsePetPending(null, 0), null);
});

test('only a reply newer than the message answers it', () => {
  const router = { threadId: 't', status: 'idle', lastReply: '记下了', lastReplyAt: '2026-10-31T10:00:00.000Z' };
  assert.deepEqual(readPetRouter(router, null, 1), { reply: '记下了' });
  assert.deepEqual(readPetRouter(router, '2026-10-31T09:59:59.000Z', 1), { reply: '记下了' });
  assert.equal(readPetRouter(router, '2026-10-31T10:00:00.000Z', 1), 'waiting');
  assert.equal(readPetRouter({ ...router, status: 'running' }, router.lastReplyAt, 99), 'waiting');
  assert.equal(readPetRouter({ ...router, status: 'error' }, router.lastReplyAt, 1), 'failed');
  assert.equal(readPetRouter(router, router.lastReplyAt, PET_QUIET_POLLS), 'quiet');
  assert.equal(readPetRouter({ ...router, lastReply: null, lastReplyAt: null }, null, 1), 'waiting');
});

test('the sheet of near-invisible pixels goes and everything else stays', () => {
  const pixels = new Uint8ClampedArray([0, 0, 0, 1, 0, 0, 0, 3, 9, 9, 9, 4, 9, 9, 9, 255]);
  clearPetHaze(pixels);
  assert.deepEqual([...pixels], [0, 0, 0, 0, 0, 0, 0, 0, 9, 9, 9, 4, 9, 9, 9, 255]);
});

test('details leave addresses out', () => {
  assert.equal(petErrorDetail(new Error('pet download failed: 502 https://raw.example.test/a/b.webp')), 'pet download failed: 502');
  assert.equal(petErrorDetail(new Error('https://raw.example.test/a')), null);
  assert.equal(petErrorDetail('hub down'), 'hub down');
  assert.equal(petErrorDetail(null), null);
});

test('a reply is split around the matters it names', () => {
  const todos = [{ id: 'a', text: 'OKR' }, { id: 'b', text: 'OKR 自评' }, { id: 'c', text: '买' }];
  assert.deepEqual(petReplyParts('记下了「OKR 自评」，还有买票', todos), [
    { text: '记下了「' }, { text: 'OKR 自评', todoId: 'b' }, { text: '」，还有买票' },
  ]);
  assert.deepEqual(petReplyParts('没提到', todos), [{ text: '没提到' }]);
  assert.deepEqual(petReplyParts('OKR', todos), [{ text: 'OKR', todoId: 'a' }]);
});

test('reply previews are at most 100 characters and preserve Unicode', () => {
  assert.equal(petReplyPreview('  已记下  '), '已记下');
  const reply = petReplyPreview('🐟'.repeat(101));
  assert.equal(Array.from(reply).length, 100);
  assert.equal(reply, '🐟'.repeat(99) + '…');
  assert.equal(petReplyPreview('a'.repeat(100)), 'a'.repeat(100));
});

test('a small portrait keeps her head and upper body, trimmed to her outline', () => {
  const width = 10, height = 10, pixels = new Uint8ClampedArray(width * height * 4);
  const paint = (x: number, y: number, alpha = 255) => { pixels[(y * width + x) * 4 + 3] = alpha; };
  for (let y = 2; y < 6; y++) for (let x = 3; x < 6; x++) paint(x, y); // head and body
  for (let y = 6; y < 10; y++) paint(1, y); // a tail low down, outside the bust
  paint(9, 0, 3); // haze does not count
  assert.deepEqual(petBustBox(pixels, width, height, 0.5), { x: 3, y: 2, width: 3, height: 4 });
  assert.equal(petBustBox(new Uint8ClampedArray(16), 2, 2), null);
});

test('a thread is read by what it needs from the person, and a team member outranks the thread', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  const base = { id: 'a', status: 'idle', waiting: false, unread: false, updatedAt: '2026-11-06T10:00:00Z' } as const;
  const failure = { code: 'plan_exhausted', message: 'session limit', at: '2026-11-06T10:00:00Z' };
  assert.equal(petThreadState({ ...base }, now), 'idle');
  assert.equal(petThreadState({ ...base, status: 'running' }, now), 'running');
  assert.equal(petThreadState({ ...base, unread: true }, now), 'review');
  assert.equal(petThreadState({ ...base, status: 'error' }, now), 'failed');
  assert.equal(petThreadState({ ...base, status: 'error', failure }, now), 'failed');
  assert.equal(petThreadState({ ...base, waiting: true }, now), 'waiting');
  assert.equal(petThreadState({ ...base, coordination: { team: { status: 'awaiting_confirm' } } }, now), 'waiting');
  assert.equal(petThreadState({ ...base, coordination: { team: { status: 'needs_attention' } } }, now), 'failed');
  assert.equal(petThreadState({ ...base, status: 'running', coordination: { team: { status: 'done' } } }, now), 'running');
  // A stop days ago is history; one with a quota watch armed takes care of itself; done is done.
  assert.equal(petThreadState({ ...base, status: 'error', failure: { ...failure, at: '2026-11-02T10:00:00Z' } }, now), 'stale');
  assert.equal(petThreadState({ ...base, status: 'error', failure, autoHandover: { resume: { resetsAt: null } } }, now), 'parked');
  assert.equal(petThreadState({ ...base, status: 'error', failure, doneAt: '2026-11-06T11:00:00Z' }, now), 'idle');
  assert.equal(petThreadState({ ...base, waiting: true, doneAt: '2026-11-06T11:00:00Z' }, now), 'waiting');
});

test('the tray holds what needs the person, most needing first, and folds away what is history', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  const at = (minutes: number) => `2026-11-06T10:${String(minutes).padStart(2, '0')}:00Z`;
  const threads = [
    { id: 'run', status: 'running', waiting: false, unread: false, updatedAt: at(3) },
    { id: 'wait', status: 'idle', waiting: true, unread: false, updatedAt: at(1) },
    { id: 'done', status: 'idle', waiting: false, unread: true, updatedAt: at(5) },
    { id: 'stuck', status: 'error', waiting: false, unread: false, updatedAt: at(2) },
    { id: 'quiet', status: 'idle', waiting: false, unread: false, updatedAt: at(4) },
    { id: 'wait2', status: 'idle', waiting: true, unread: false, updatedAt: at(6) },
    { id: 'old', status: 'error', waiting: false, unread: false, updatedAt: '2026-10-20T10:00:00Z' },
    // Its work went on in 'next': the stop is history, whatever its age.
    { id: 'handed', status: 'error', waiting: false, unread: false, updatedAt: at(7) },
    { id: 'next', status: 'error', waiting: false, unread: false, updatedAt: at(8), handover: { sourceId: 'handed' } },
  ] as const;
  const triage = petTriage(threads, now);
  assert.deepEqual(triage.active.map(t => t.id), ['wait2', 'wait', 'next', 'stuck', 'done', 'run']);
  assert.deepEqual(triage.earlier.map(t => t.id), ['old']);
  // Set aside stays aside until the thread changes.
  assert.deepEqual(petTriage(threads, now, { wait2: at(6), stuck: at(0) }).active.map(t => t.id), ['wait', 'next', 'stuck', 'done', 'run']);
  assert.deepEqual(petTriage([], now), { active: [], earlier: [] });
});

test('she reads why a thread stopped and offers the step that moves it on', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  const base = { id: 'a', status: 'error', waiting: false, unread: false, updatedAt: '2026-11-06T10:00:00Z' } as const;
  const failed = (code: string | null, message: string) => ({ ...base, failure: { code, message, at: base.updatedAt } });
  assert.equal(petFailureKind({ code: null, message: '429: {"code":"1308","message":"已达到 5 小时的使用上限"}' }), 'quota');
  assert.equal(petFailureKind({ code: null, message: 'Authentication required' }), 'auth');
  assert.equal(petFailureKind({ code: null, message: 'Not logged in · Please run /login' }), 'auth');
  assert.equal(petFailureKind({ code: null, message: 'Connection error.' }), 'other');
  for (const message of ['529 overloaded_error: Overloaded', 'Internal error: upstream connection reset (request 3)', 'read ECONNRESET', '503 Service Unavailable', 'request timed out', 'Gateway Timeout']) {
    assert.equal(petFailureKind({ code: null, message }), 'transient', message);
  }
  // Quota and login trouble win over the words they share with a flaky server.
  assert.equal(petFailureKind({ code: null, message: '429 rate limit, request timed out' }), 'quota');
  assert.equal(petFailureKind({ code: null, message: 'Unauthorized: token timeout' }), 'auth');
  assert.equal(petAdvice(failed('plan_exhausted', "Internal error: You've hit your session limit"), now).step, 'retry');
  assert.equal(petAdvice(failed('interrupted', 'The hub restarted'), now).step, 'retry');
  assert.equal(petAdvice(failed('permission_refused', 'refused'), now).step, 'open');
  assert.equal(petAdvice(failed(null, 'Authentication required'), now).step, 'open');
  const other = petAdvice(failed(null, 'Internal error: Connection error.\nat line 3'), now);
  assert.deepEqual([other.why, other.step, other.since], ['Connection error.', 'retry', base.updatedAt]);
  // An older daemon says nothing about the failure: she still takes the person there.
  assert.equal(petAdvice(base, now).step, 'open');
  assert.equal(petAdvice({ ...base, status: 'idle', waiting: true, waitingReason: 'approval' }, now).step, 'open');
  assert.equal(petAdvice({ ...base, status: 'running' }, now).step, null);
  assert.equal(petAdvice({ ...base, status: 'idle', unread: true, report: { note: '改完了，测试全过' } }, now).why, '改完了，测试全过');
});

test('with the thread\'s brief she says what is asked of the person, not just the kind of matter', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  const at = '2026-11-06T10:00:00Z';
  const waiting = { id: 'w', status: 'idle', waiting: true, waitingReason: 'approval', unread: false, updatedAt: at } as const;
  const approval = (patch: object) => ({ updatedAt: at, kind: 'approval' as const, approval: { action: 'run' as const, command: null, paths: [], moreFiles: 0, title: null, pending: 1, ...patch } });

  // The command is handed over apart from the words around it, to be set in monospace.
  const run = petBriefAdvice(waiting, approval({ command: 'npm install lodash' }), now);
  assert.deepEqual([run.why, run.code], ['要运行', 'npm install lodash']);
  assert.equal(petBriefAdvice(waiting, approval({ action: 'edit', paths: ['a.ts'] }), now).code, undefined);
  assert.equal(petBriefAdvice(waiting, approval({ action: 'edit', paths: ['src/a.ts', 'src/b.ts'] }), now).why, '要改 a.ts、b.ts。');
  assert.equal(petBriefAdvice(waiting, approval({ action: 'edit', paths: ['a.ts', 'b.ts', 'c.ts'], moreFiles: 4 }), now).why, '要改 a.ts、b.ts、c.ts 等 7 个文件。');
  assert.equal(petBriefAdvice(waiting, approval({ action: 'edit', paths: ['package-lock.json', 'web/yarn.lock'] }), now).why, '要改 package-lock.json、yarn.lock，都是 lock 文件。');
  assert.equal(petBriefAdvice(waiting, approval({ action: 'edit', paths: ['package-lock.json', 'src/a.ts'] }), now).why, '要改 package-lock.json、a.ts。');
  assert.equal(petBriefAdvice(waiting, approval({ action: 'delete', paths: ['old.ts'] }), now).why, '要删 old.ts。');
  assert.equal(petBriefAdvice(waiting, approval({ action: 'other', title: 'Edit file' }), now).why, '等你批准的是「Edit file」。');
  // The button stays the one that goes to approve it.
  assert.equal(petBriefAdvice(waiting, approval({ command: 'ls' }), now).label, petAdvice(waiting, now).label);
  // Too little said to tell, no brief, or a brief for an older version of the thread: the plain line stands.
  assert.deepEqual(petBriefAdvice(waiting, approval({}), now), petAdvice(waiting, now));
  assert.deepEqual(petBriefAdvice(waiting, null, now), petAdvice(waiting, now));
  assert.deepEqual(petBriefAdvice(waiting, { ...approval({ command: 'ls' }), updatedAt: '2026-11-06T09:00:00Z' }, now), petAdvice(waiting, now));
  // A brief of another kind of matter says nothing about this one.
  assert.deepEqual(petBriefAdvice(waiting, { updatedAt: at, kind: 'result', result: { first: 'ok', edits: 0, files: 0 } }, now), petAdvice(waiting, now));
});

test('with the brief she says whether trying again is safe, and stops offering it when it keeps failing', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  const at = '2026-11-06T10:00:00Z';
  const stopped = (code: string | null, message: string) => ({ id: 'f', status: 'error', waiting: false, unread: false, updatedAt: at, failure: { code, message, at } }) as const;
  const brief = (patch: object) => ({ updatedAt: at, kind: 'failure' as const, failure: { code: null, edits: 0, files: 0, repeats: 1, resume: false, ...patch } });
  const connection = stopped(null, 'Internal error: Connection error.');

  const clean = petBriefAdvice(connection, brief({}), now);
  assert.equal(clean.why, 'Connection error，这一轮还没改文件，重试没有副作用。');
  assert.deepEqual([clean.step, clean.label, clean.since], ['retry', '再试一次', at]);
  assert.equal(petBriefAdvice(stopped('plan_exhausted', 'quota'), brief({}), now).why, '额度用完停下了。这一轮还没改文件，重试没有副作用。');
  // A temporary server error is named for what it is and can be tried again; an English line is joined with a comma, not a Chinese full stop.
  const overloaded = stopped(null, '529 overloaded_error: Overloaded');
  assert.deepEqual([petAdvice(overloaded, now).why, petAdvice(overloaded, now).step], ['服务端临时出错，和你的代码无关', 'retry']);
  assert.equal(petBriefAdvice(overloaded, brief({}), now).why, '服务端临时出错，和你的代码无关。这一轮还没改文件，重试没有副作用。');
  assert.equal(petBriefAdvice(overloaded, brief({ repeats: 3 }), now).step, 'open');
  assert.equal(petBriefAdvice(stopped(null, 'Something broke in 42'), brief({ edits: 1, files: 1 }), now).why, 'Something broke in 42，这一轮已经改了 1 个文件，重试前最好打开看看。');
  assert.equal(petBriefAdvice(connection, brief({ edits: 4, files: 3 }), now).why, 'Connection error，这一轮已经改了 3 个文件，重试前最好打开看看。');
  // Still offered to retry at the second time; at the third the button becomes a way in.
  assert.equal(petBriefAdvice(connection, brief({ repeats: 2 }), now).step, 'retry');
  const stuck = petBriefAdvice(connection, brief({ repeats: 3 }), now);
  assert.deepEqual([stuck.why, stuck.step, stuck.label, stuck.since], ['同样的错已经连续 3 次，先别重试，打开看看。', 'open', '去看', at]);
  assert.equal(petBriefAdvice(stopped('plan_exhausted', 'quota'), brief({ repeats: 5 }), now).step, 'open');
  // A quota watch already resends it: the plain line says so.
  assert.deepEqual(petBriefAdvice(connection, brief({ resume: true, repeats: 4 }), now), petAdvice(connection, now));
  // A login or an approval she cannot ask for needs a person whatever the brief says.
  const login = stopped(null, 'Authentication required');
  assert.deepEqual(petBriefAdvice(login, brief({}), now), petAdvice(login, now));
  assert.equal(petBriefWanted(login, now), false);
  assert.equal(petBriefWanted(connection, now), true);
});

test('with the brief she says what a finished thread did', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  const at = '2026-11-06T10:00:00Z';
  const done = { id: 'd', status: 'idle', waiting: false, unread: true, updatedAt: at } as const;
  const result = (first: string | null, files: number) => ({ updatedAt: at, kind: 'result' as const, result: { first, edits: files, files } });

  assert.equal(petBriefAdvice(done, result('登录页修好了。', 2), now).why, '做完了：登录页修好了，改了 2 个文件。');
  assert.equal(petBriefAdvice(done, result('登录页修好了', 0), now).why, '做完了：登录页修好了。');
  assert.equal(petBriefAdvice(done, result(null, 3), now).why, '做完了，改了 3 个文件。');
  // Nothing said and nothing changed: nothing to add to the plain line.
  assert.deepEqual(petBriefAdvice(done, result(null, 0), now), petAdvice(done, now));
  assert.equal(petBriefAdvice(done, result('ok', 1), now).step, 'open');
  // The agent's own report of progress is better than a guess from its log.
  const reported = { ...done, report: { note: '改完了，测试全过' } };
  assert.equal(petBriefWanted(reported, now), false);
  assert.equal(petBriefAdvice(reported, result('登录页修好了。', 2), now).why, '改完了，测试全过');
  // Running, parked and idle matters are not asked about.
  assert.equal(petBriefWanted({ ...done, status: 'running', unread: false }, now), false);
  assert.equal(petBriefWanted({ ...done, unread: false }, now), false);
});

test('wherever she is left, she stays whole inside the viewport', () => {
  assert.deepEqual(petClampPosition(-50, 900, 62, 80, 1280, 800), { x: 8, y: 712 });
  assert.deepEqual(petClampPosition(30, 100, 62, 80, 1280, 800), { x: 30, y: 100 });
  // A viewport smaller than she is: she keeps to the top-left corner, not to negative numbers.
  assert.deepEqual(petClampPosition(10, 10, 200, 300, 100, 100), { x: 8, y: 8 });
});

test('she brings up what wants the person or is done unseen, not what is running or parked', () => {
  assert.equal(petSpeaksOf('waiting'), true);
  assert.equal(petSpeaksOf('failed'), true);
  assert.equal(petSpeaksOf('review'), true);
  assert.equal(petSpeaksOf('running'), false);
  assert.equal(petSpeaksOf('parked'), false);
  assert.equal(petSpeaksOf('stale'), false);
  assert.equal(petSpeaksOf('idle'), false);
});
