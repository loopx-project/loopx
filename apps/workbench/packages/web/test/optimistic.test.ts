import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  appendQueuedToThreads,
  createOptimisticThread,
  createOptimisticTurn,
  createQueuedPreview,
  findOptimisticImageBlob,
  isOptimisticThreadId,
  mergeOptimisticEvents,
  mergeServerThreads,
  optimisticTitle,
  reconcileOptimisticTurns,
  runOptimisticMainSend,
  type OptimisticTurn,
  type OptimisticTurnsController,
  type PendingThreadCreation,
} from '../src/optimistic';
import { createThreadFeed } from '../src/extensions/thread-feed';
import type { Thread } from '../src/api';
import type { StoredEvent } from '../src/timeline';

const event = (seq: number, type: string, turnId: string | null, data: Record<string, unknown> = {}): StoredEvent => ({
  threadId: 't1',
  turnId,
  seq,
  ts: '2026-11-24T00:00:00.000Z',
  type,
  data,
});

function makeController(): OptimisticTurnsController & { current: () => OptimisticTurn[] } {
  let turns: OptimisticTurn[] = [];
  return {
    get turns() { return turns; },
    current: () => turns,
    add(turn) { turns = [...turns, turn]; },
    bindTurnId(localId, turnId) { turns = turns.map(t => (t.localId === localId ? { ...t, turnId } : t)); },
    migrateThread(from, to) { turns = turns.map(t => (t.threadId === from ? { ...t, threadId: to } : t)); },
    remove(localId) { turns = turns.filter(t => t.localId !== localId); },
    reconcile(threadId, events) { turns = reconcileOptimisticTurns(turns, events, threadId); },
    imageBlob(id) { return findOptimisticImageBlob(turns, id); },
    hasForThread(threadId) { return Boolean(threadId && turns.some(t => t.threadId === threadId)); },
  };
}

test('optimisticTitle extracts first line and falls back to media labels', () => {
  assert.equal(optimisticTitle('  first line\nsecond line'), 'first line');
  assert.equal(optimisticTitle('', [{}]), '图片');
  assert.equal(optimisticTitle('', [], [{ name: 'clip.mp4', mimeType: 'video/mp4' }]), '视频');
  assert.equal(optimisticTitle('', [], [{ name: 'report.pdf', mimeType: 'application/pdf' }]), 'report.pdf');
});

test('mergeServerThreads preserves optimistic and pinned newly-created threads until server catches up', () => {
  const opt = createOptimisticThread({ agent: 'agy', cwd: '/proj', title: 'Draft ask', permissionMode: 'auto_edit' });
  const pinnedReal = createOptimisticThread({ id: 'real-1', agent: 'agy', cwd: '/proj', title: 'Real ask', permissionMode: 'auto_edit' });
  const older = createOptimisticThread({ id: 'old-1', agent: 'agy', cwd: '/proj', title: 'Old', permissionMode: 'auto_edit' });

  const mergedBefore = mergeServerThreads([older], [opt, pinnedReal, older], new Set(['real-1']));
  assert.deepEqual(mergedBefore.map(t => t.id), [opt.id, 'real-1', 'old-1']);

  const serverUpdated = { ...pinnedReal, title: 'Server title' };
  const mergedAfter = mergeServerThreads([serverUpdated, older], [opt, pinnedReal, older], new Set(['real-1']));
  assert.deepEqual(mergedAfter.map(t => t.id), [opt.id, 'real-1', 'old-1']);
  assert.equal(mergedAfter[1].title, 'Server title');
});

test('mergeOptimisticEvents renders immediately and deduplicates once server turn.started arrives', () => {
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
  const base = [event(1, 'turn.started', 'turn-0', { text: 'hello' }), event(2, 'turn.done', 'turn-0')];
  const opt = createOptimisticTurn({
    threadId: 't1',
    text: 'hello',
    images: [{ id: 'img-1', data: png, mimeType: 'image/png', name: 'dot.png' }],
    afterSeq: 2,
  });

  // Same text as an older turn (seq <= afterSeq) must NOT prematurely reconcile the new optimistic turn.
  const merged = mergeOptimisticEvents(base, [opt], 't1');
  assert.equal(merged.length, 3);
  assert.equal(merged[2].type, 'turn.started');
  assert.equal(merged[2].data.optimistic, true);
  const blob = findOptimisticImageBlob([opt], (merged[2].data.images as Array<{ id: string }>)[0].id);
  assert.ok(blob instanceof Blob);

  // Server delivers real turn.started with seq > afterSeq and matching text before turnId even binds.
  const withServer = [...base, event(3, 'turn.started', 'turn-1', { text: 'hello' })];
  const mergedAfter = mergeOptimisticEvents(withServer, [opt], 't1');
  assert.equal(mergedAfter.length, 3);
  assert.equal(mergedAfter[2].data.optimistic, undefined);
  assert.equal(reconcileOptimisticTurns([opt], withServer, 't1').length, 0);
});

test('runOptimisticMainSend transitions a new thread at 0ms and migrates seamlessly to the real id', async () => {
  let threads: Thread[] = [];
  let selectedId: string | null = null;
  const shownThread = { current: null as string | null };
  const setSelectedId = (id: string | null) => { selectedId = id; shownThread.current = id; };
  const pinnedThreadIds = { current: new Set<string>() };
  const pendingCreations = { current: new Map<string, PendingThreadCreation>() };
  const optimistic = makeController();

  let resolveCreate!: (t: Thread) => void;
  let resolveTurn!: (r: { turnId: string }) => void;
  const api = {
    demo: false,
    createThread: () => new Promise<Thread>(r => { resolveCreate = r; }),
    setWriting: async () => { throw new Error('unused'); },
    dispatchTeam: async () => { throw new Error('unused'); },
    startTurn: () => new Promise<{ turnId: string }>(r => { resolveTurn = r; }),
    uploadVideo: async () => { throw new Error('unused'); },
    deleteThread: async () => undefined,
  };

  const sendPromise = runOptimisticMainSend({
    api,
    selectedId: null,
    selected: null,
    running: false,
    events: [],
    draftTeam: null,
    draftAgent: 'agy',
    draftCwd: '/proj',
    draftScratch: false,
    draftPermission: 'auto_edit',
    draftWorkMode: 'execute',
    draftModel: null,
    draftEffort: null,
    draftSpeed: null,
    draftSectionId: null,
    draftWorktree: false,
    draftWriting: null,
    hasWorktreeOffer: false,
    draftCollaboration: false,
    collaborationDirectories: [],
    startingDefaults: {},
    memberPermission: 'full_access',
    text: 'Fix the composer lag',
    images: [],
    isNewThread: true,
    videos: [],
    references: [],
    teamAttachmentsError: 'no attachments',
    setThreads: updater => { threads = typeof updater === 'function' ? updater(threads) : updater; },
    setSelectedId,
    setView: () => undefined,
    setDraftWriting: () => undefined,
    setDraftSectionId: () => undefined,
    setDraftWorktree: () => undefined,
    setDraftTeam: () => undefined,
    pinnedThreadIds,
    shownThread,
    stickToBottom: { current: false },
    optimistic,
    pendingCreations,
    refreshThreads: () => undefined,
  });

  // Immediately after invocation (before createThread resolves), UI is already on the optimistic thread.
  assert.ok(isOptimisticThreadId(selectedId));
  assert.equal(threads.length, 1);
  assert.equal(threads[0].title, 'Fix the composer lag');
  assert.equal(optimistic.current().length, 1);
  assert.equal(optimistic.current()[0].threadId, selectedId);

  const realThread = createOptimisticThread({ id: 'thread-real-1', agent: 'agy', cwd: '/proj', title: 'New thread', permissionMode: 'auto_edit' });
  resolveCreate(realThread);
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(selectedId, 'thread-real-1');
  assert.equal(threads.length, 1);
  assert.equal(threads[0].id, 'thread-real-1');
  assert.equal(threads[0].title, 'Fix the composer lag', 'keeps optimistic title instead of flashing New thread');
  assert.equal(optimistic.current()[0].threadId, 'thread-real-1');

  resolveTurn({ turnId: 'turn-real-1' });
  await sendPromise;
  assert.equal(optimistic.current()[0].turnId, 'turn-real-1');
});

test('runOptimisticMainSend deletes 0-turn orphan thread and rolls back when first startTurn fails', async () => {
  let threads: Thread[] = [];
  let selectedId: string | null = null;
  const shownThread = { current: null as string | null };
  const setSelectedId = (id: string | null) => { selectedId = id; shownThread.current = id; };
  const deleted: string[] = [];
  const optimistic = makeController();

  const api = {
    demo: false,
    createThread: async () => createOptimisticThread({ id: 'orphan-1', agent: 'agy', cwd: '/proj', title: 'New thread', permissionMode: 'auto_edit' }),
    setWriting: async () => { throw new Error('unused'); },
    dispatchTeam: async () => { throw new Error('unused'); },
    startTurn: async () => { throw new Error('boom'); },
    uploadVideo: async () => { throw new Error('unused'); },
    deleteThread: async (id: string) => { deleted.push(id); },
  };

  await assert.rejects(() => runOptimisticMainSend({
    api,
    selectedId: null,
    selected: null,
    running: false,
    events: [],
    draftTeam: null,
    draftAgent: 'agy',
    draftCwd: '/proj',
    draftScratch: false,
    draftPermission: 'auto_edit',
    draftWorkMode: 'execute',
    draftModel: null,
    draftEffort: null,
    draftSpeed: null,
    draftSectionId: null,
    draftWorktree: false,
    draftWriting: null,
    hasWorktreeOffer: false,
    draftCollaboration: false,
    collaborationDirectories: [],
    startingDefaults: {},
    memberPermission: 'full_access',
    text: 'Will fail',
    images: [],
    isNewThread: true,
    videos: [],
    references: [],
    teamAttachmentsError: 'no attachments',
    setThreads: updater => { threads = typeof updater === 'function' ? updater(threads) : updater; },
    setSelectedId,
    setView: () => undefined,
    setDraftWriting: () => undefined,
    setDraftSectionId: () => undefined,
    setDraftWorktree: () => undefined,
    setDraftTeam: () => undefined,
    pinnedThreadIds: { current: new Set() },
    shownThread,
    stickToBottom: { current: false },
    optimistic,
    pendingCreations: { current: new Map() },
    refreshThreads: () => undefined,
  }), /boom/);

  assert.equal(selectedId, null);
  assert.deepEqual(threads, []);
  assert.deepEqual(optimistic.current(), []);
  assert.deepEqual(deleted, ['orphan-1']);
});

test('appendQueuedToThreads adds optimistic queued preview without duplicating', () => {
  const t = createOptimisticThread({ id: 't1', agent: 'agy', cwd: '/proj', title: 'T1', permissionMode: 'auto_edit' });
  const q = createQueuedPreview('q1', 'next ask', [], []);
  const once = appendQueuedToThreads([t], 't1', q);
  assert.equal(once[0].queued.length, 1);
  assert.equal(once[0].queued[0].text, 'next ask');
  const twice = appendQueuedToThreads(once, 't1', q);
  assert.equal(twice[0].queued.length, 1);
});

test('createThreadFeed applies earlier response that finishes first while still rejecting out-of-order stale responses', async () => {
  const resolvers: Array<(list: Thread[]) => void> = [];
  const received: string[][] = [];
  const feed = createThreadFeed(
    { demo: false, threads: () => new Promise<Thread[]>(r => { resolvers.push(r); }) },
    list => { received.push(list.map(t => t.id)); },
    () => undefined,
  );
  feed.start();

  const t1 = createOptimisticThread({ id: 'a', agent: 'agy', cwd: '/p', title: 'A', permissionMode: 'auto_edit' });
  const t2 = createOptimisticThread({ id: 'b', agent: 'agy', cwd: '/p', title: 'B', permissionMode: 'auto_edit' });
  const t3 = createOptimisticThread({ id: 'c', agent: 'agy', cwd: '/p', title: 'C', permissionMode: 'auto_edit' });

  // Request 1 and 2 overlap; 1 returns first -> MUST apply instead of being dropped!
  const p1 = feed.read();
  const p2 = feed.read();
  resolvers[0]([t1]);
  await p1;
  assert.deepEqual(received, [['a']]);

  resolvers[1]([t1, t2]);
  await p2;
  assert.deepEqual(received, [['a'], ['a', 'b']]);

  // Request 3 and 4 overlap; 4 returns BEFORE 3 -> 3 MUST be dropped as stale!
  const p3 = feed.read();
  const p4 = feed.read();
  resolvers[3]([t1, t2, t3]);
  await p4;
  resolvers[2]([t1]);
  await p3;
  assert.deepEqual(received, [['a'], ['a', 'b'], ['a', 'b', 'c']]);
  feed.dispose();
});
