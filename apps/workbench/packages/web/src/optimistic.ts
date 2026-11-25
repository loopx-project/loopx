import { useCallback, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { ApiError, type AgentMention, type Api, type MemberDefault, type QueuedMessage, type Thread, type WorkMode, type WritingStage } from './api';
import { referencePreview, sameReferencedMessage } from './context-references';
import { t } from './i18n';
import type { PreparedImage } from './images';
import type { StoredEvent } from './timeline';
import { isPlayable, type PreparedVideo } from './videos';

export const OPTIMISTIC_THREAD_PREFIX = 'optimistic:';

let optimisticSeq = 0;

export function isOptimisticThreadId(id: string | null | undefined): boolean {
  return typeof id === 'string' && id.startsWith(OPTIMISTIC_THREAD_PREFIX);
}

export function createOptimisticThreadId(): string {
  return `${OPTIMISTIC_THREAD_PREFIX}${Date.now()}-${++optimisticSeq}`;
}

export interface OptimisticImage {
  id: string;
  mimeType: string;
  name: string;
  blob: Blob;
}

export interface OptimisticTurn {
  localId: string;
  turnId: string | null;
  threadId: string;
  text: string;
  workMode?: WorkMode;
  ts: string;
  images: OptimisticImage[];
  videos: Array<{ id: string; name: string; mimeType: string; size: number; blob: Blob }>;
  afterSeq: number;
}

export function optimisticTitle(
  text: string,
  images: ReadonlyArray<unknown> = [],
  videos: ReadonlyArray<{ name: string; file?: { type?: string }; mimeType?: string }> = [],
): string {
  const firstLine = referencePreview(text)
    .split('\n')
    .map(line => line.trim())
    .find(Boolean);
  if (firstLine) return firstLine.slice(0, 60);
  if (videos.length > 0) {
    const allVideos = videos.every(v => (v.mimeType ?? v.file?.type ?? '').startsWith('video/'));
    return allVideos ? t('videos.video') : (videos[0]?.name || t('uploads.file'));
  }
  if (images.length > 0) return t('images.image');
  return 'New thread';
}

export function createOptimisticThread(input: {
  id?: string;
  agent: string;
  cwd: string;
  scratch?: boolean;
  title: string;
  permissionMode: string;
  workMode?: WorkMode;
  model?: string | null;
  effort?: string | null;
  speed?: string | null;
  sectionId?: string | null;
  worktree?: { root: string; branch: string; of: string; ofReal: string } | null;
}): Thread {
  const now = new Date().toISOString();
  return {
    id: input.id ?? createOptimisticThreadId(),
    agent: input.agent,
    cwd: input.cwd,
    cwdReal: input.cwd,
    title: input.title,
    agentSessionId: null,
    status: 'running',
    pinned: false,
    workMode: input.workMode ?? 'execute',
    speed: input.speed ?? null,
    permissionMode: input.permissionMode,
    model: input.model ?? null,
    effort: input.effort ?? null,
    archivedAt: null,
    sectionId: input.sectionId ?? null,
    forkedFrom: null,
    worktree: input.worktree ?? null,
    scratch: input.scratch ?? false,
    unread: false,
    preview: input.title,
    terminalActivity: false,
    waiting: false,
    queued: [],
    runningSince: now,
    resumeCommand: null,
    createdAt: now,
    updatedAt: now,
  };
}

/**
 * Merges a fresh server thread list while preserving local optimistic placeholders and recently
 * created real threads that have not yet appeared in the server snapshot.
 */
export function mergeServerThreads(
  server: Thread[],
  current: Thread[],
  pinnedIds: ReadonlySet<string>,
): Thread[] {
  const serverIds = new Set(server.map(thread => thread.id));
  const kept = current.filter(
    thread => !serverIds.has(thread.id) && (isOptimisticThreadId(thread.id) || pinnedIds.has(thread.id)),
  );
  return kept.length ? [...kept, ...server] : server;
}

export function base64ToBlob(data: string, mimeType: string): Blob {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mimeType });
}

export function createOptimisticTurn(input: {
  threadId: string;
  text: string;
  workMode?: WorkMode;
  images?: ReadonlyArray<Pick<PreparedImage, 'id' | 'data' | 'mimeType'> & { name?: string }>;
  videos?: ReadonlyArray<Pick<PreparedVideo, 'id' | 'name' | 'file'>>;
  afterSeq: number;
}): OptimisticTurn {
  const localId = `optimistic-turn-${Date.now()}-${++optimisticSeq}`;
  const images: OptimisticImage[] = (input.images ?? []).map((img, index) => ({
    id: `opt-img-${localId}-${index}`,
    mimeType: img.mimeType,
    name: img.name ?? 'image',
    blob: base64ToBlob(img.data, img.mimeType),
  }));
  const videos = (input.videos ?? []).map((video, index) => ({
    id: `opt-vid-${localId}-${index}`,
    name: video.name,
    mimeType: video.file.type || 'application/octet-stream',
    size: video.file.size,
    blob: video.file,
  }));
  return {
    localId,
    turnId: null,
    threadId: input.threadId,
    text: input.text,
    workMode: input.workMode,
    ts: new Date().toISOString(),
    images,
    videos,
    afterSeq: input.afterSeq,
  };
}

function turnMatchedInEvents(turn: OptimisticTurn, events: StoredEvent[]): boolean {
  for (const ev of events) {
    if (ev.type !== 'turn.started' || ev.data.optimistic) continue;
    if (turn.turnId && ev.turnId === turn.turnId) return true;
    if (ev.seq > turn.afterSeq) {
      const shown = String(ev.data.displayText ?? ev.data.text ?? '');
      if (sameReferencedMessage(shown, turn.text)) return true;
    }
  }
  return false;
}

/** Keeps only optimistic turns that have not yet been confirmed by server events. */
export function reconcileOptimisticTurns(
  turns: OptimisticTurn[],
  events: StoredEvent[],
  threadId: string,
): OptimisticTurn[] {
  let changed = false;
  const next = turns.filter(turn => {
    if (turn.threadId !== threadId) return true;
    const matched = turnMatchedInEvents(turn, events);
    if (matched) changed = true;
    return !matched;
  });
  return changed ? next : turns;
}

/** Appends synthetic `turn.started` events for unconfirmed optimistic turns of `threadId`. */
export function mergeOptimisticEvents(
  events: StoredEvent[],
  turns: OptimisticTurn[],
  threadId: string | null | undefined,
): StoredEvent[] {
  if (!threadId || !turns.length) return events;
  const pending = turns.filter(turn => turn.threadId === threadId && !turnMatchedInEvents(turn, events));
  if (!pending.length) return events;
  const maxSeq = events.reduce((highest, ev) => Math.max(highest, ev.seq), 0);
  const synthetic: StoredEvent[] = pending.map((turn, index) => ({
    threadId,
    turnId: turn.turnId ?? turn.localId,
    seq: maxSeq + 1 + index,
    ts: turn.ts,
    type: 'turn.started',
    data: {
      text: turn.text,
      workMode: turn.workMode ?? 'execute',
      optimistic: true,
      ...(turn.images.length ? { images: turn.images.map(({ id, mimeType, name }) => ({ id, mimeType, name })) } : {}),
      ...(turn.videos.length ? { videos: turn.videos.map(({ id, name, mimeType, size }) => ({ id, name, mimeType, size })) } : {}),
    },
  }));
  return [...events, ...synthetic];
}

export function findOptimisticImageBlob(turns: OptimisticTurn[], attachmentId: string): Blob | null {
  for (const turn of turns) {
    const img = turn.images.find(item => item.id === attachmentId);
    if (img) return img.blob;
    const video = turn.videos.find(item => item.id === attachmentId);
    if (video) return video.blob;
  }
  return null;
}

export function maxEventSeq(events: ReadonlyArray<StoredEvent>): number {
  let max = 0;
  for (const ev of events) if (ev.seq > max) max = ev.seq;
  return max;
}

export function createQueuedPreview(
  id: string,
  text: string,
  images: ReadonlyArray<unknown> = [],
  videos: ReadonlyArray<PreparedVideo> = [],
  mentions?: ReadonlyArray<AgentMention>,
): QueuedMessage {
  const videoCount = videos.filter(isPlayable).length;
  const fileCount = videos.length - videoCount;
  const targets = (mentions ?? [])
    .filter(m => typeof m.task === 'string' && m.task.trim().length > 0)
    .map(m => m.label || m.id);
  return {
    id,
    text,
    images: images.length,
    ...(videoCount ? { videos: videoCount } : {}),
    ...(fileCount ? { files: fileCount } : {}),
    ...(targets.length ? { targets } : {}),
  };
}

export function appendQueuedToThreads(threads: Thread[], threadId: string, queued: QueuedMessage): Thread[] {
  return threads.map(thread => {
    if (thread.id !== threadId) return thread;
    if (thread.queued.some(item => item.id === queued.id)) return thread;
    return { ...thread, queued: [...thread.queued, queued] };
  });
}

/** Shared optimistic-turn state used by every conversation surface (main thread and panels). */
export function useOptimisticTurns() {
  const [turns, setTurns] = useState<OptimisticTurn[]>([]);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;

  const add = useCallback((turn: OptimisticTurn) => {
    setTurns(prev => [...prev, turn]);
  }, []);

  const bindTurnId = useCallback((localId: string, turnId: string) => {
    setTurns(prev => prev.map(turn => (turn.localId === localId ? { ...turn, turnId } : turn)));
  }, []);

  const migrateThread = useCallback((fromThreadId: string, toThreadId: string) => {
    setTurns(prev => prev.map(turn => (turn.threadId === fromThreadId ? { ...turn, threadId: toThreadId } : turn)));
  }, []);

  const remove = useCallback((localId: string) => {
    setTurns(prev => prev.filter(turn => turn.localId !== localId));
  }, []);

  const reconcile = useCallback((threadId: string, events: StoredEvent[]) => {
    setTurns(prev => reconcileOptimisticTurns(prev, events, threadId));
  }, []);

  const imageBlob = useCallback((attachmentId: string) => findOptimisticImageBlob(turnsRef.current, attachmentId), []);
  const hasForThread = useCallback((threadId: string | null | undefined) => Boolean(threadId && turnsRef.current.some(turn => turn.threadId === threadId)), []);

  return { turns, add, bindTurnId, migrateThread, remove, reconcile, imageBlob, hasForThread };
}

export type OptimisticTurnsController = ReturnType<typeof useOptimisticTurns>;

export interface PendingThreadCreation {
  tempId: string;
  promise: Promise<string>;
  cancelled: boolean;
}

export type FailedFirstTurnOutcome =
  | { kind: 'accepted'; thread: Thread; events: StoredEvent[] }
  | { kind: 'deleted' }
  | { kind: 'kept' };

/**
 * True when an error cannot rule out that the daemon already accepted or completed the turn
 * (network drop, aborted response, timeout, or upstream gateway error).
 */
export function isUnknownDeliveryError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 502;
}

/**
 * Safely settles a failed first turn on a newly created thread without deleting durable work:
 * - On unknown delivery, reads back the thread and its history first; if any turn/event/queue
 *   exists, keeps the thread and returns `{ kind: 'accepted' }`.
 * - Only deletes through the daemon's atomic `ifEmpty: true` guard. If the daemon refuses with
 *   `thread_not_empty` or readback/cleanup cannot complete, keeps the thread and draft binding.
 */
const hasTurnWork = (events: ReadonlyArray<StoredEvent>): boolean =>
  events.some(ev => ev.turnId !== null || ev.type.startsWith('turn.'));

export async function reconcileOrCleanupEmptyThread(
  api: Pick<Api, 'deleteThread' | 'thread' | 'historyPage'>,
  created: Thread,
  error: unknown,
): Promise<FailedFirstTurnOutcome> {
  if (isUnknownDeliveryError(error)) {
    let liveThread: Thread;
    let page: { events: StoredEvent[] };
    try {
      [liveThread, page] = await Promise.all([api.thread(created.id), api.historyPage(created.id)]);
    } catch {
      return { kind: 'kept' };
    }
    if (liveThread.status === 'running' || liveThread.queued.length > 0 || hasTurnWork(page.events) || Boolean(liveThread.agentSessionId)) {
      return { kind: 'accepted', thread: liveThread, events: page.events };
    }
  }
  try {
    await api.deleteThread(created.id, { worktree: Boolean(created.worktree), ifEmpty: true });
    return { kind: 'deleted' };
  } catch (cleanupError) {
    if (cleanupError instanceof ApiError && cleanupError.status === 409 && cleanupError.details?.code === 'thread_not_empty') {
      try {
        const [liveThread, page] = await Promise.all([api.thread(created.id), api.historyPage(created.id)]);
        return { kind: 'accepted', thread: liveThread, events: page.events };
      } catch {
        return { kind: 'kept' };
      }
    }
    return { kind: 'kept' };
  }
}

export interface OptimisticMainSendArgs {
  api: Pick<Api, 'demo' | 'createThread' | 'setWriting' | 'dispatchTeam' | 'startTurn' | 'uploadVideo' | 'deleteThread' | 'thread' | 'historyPage'>;
  selectedId: string | null;
  selected: Thread | null;
  running: boolean;
  events: StoredEvent[];
  draftTeam: string | null;
  draftAgent: string;
  draftCwd: string;
  draftScratch: boolean;
  draftPermission: string;
  draftWorkMode: WorkMode;
  draftModel: string | null;
  draftEffort: string | null;
  draftSpeed: string | null;
  draftSectionId: string | null;
  draftWorktree: boolean;
  draftWriting: WritingStage | null;
  hasWorktreeOffer: boolean;
  draftCollaboration: boolean;
  collaborationDirectories: string[];
  startingDefaults: Record<string, MemberDefault>;
  memberPermission: string;
  text: string;
  images: PreparedImage[];
  isNewThread: boolean;
  mentions?: AgentMention[];
  videos: PreparedVideo[];
  references: string[];
  teamAttachmentsError: string;
  setThreads: Dispatch<SetStateAction<Thread[]>>;
  setSelectedId: (id: string | null) => void;
  setView: (view: 'thread') => void;
  setDraftWriting: (stage: WritingStage | null) => void;
  setDraftSectionId: (id: string | null) => void;
  setDraftWorktree: (v: boolean) => void;
  setDraftTeam: (id: string | null) => void;
  pinnedThreadIds: MutableRefObject<Set<string>>;
  shownThread: MutableRefObject<string | null>;
  stickToBottom: MutableRefObject<boolean>;
  optimistic: OptimisticTurnsController;
  pendingCreations: MutableRefObject<Map<string, PendingThreadCreation>>;
  refreshThreads: () => void;
  onCreated?: (threadId: string) => void;
  onRollback?: () => void;
}

/**
 * Optimistic send orchestration for the main conversation view:
 * - New threads & team tasks transition immediately (0ms) with a local placeholder thread + user message.
 * - Existing threads show the user message immediately while the turn request is in flight.
 * - Failed first turns clean up any 0-turn orphan thread and roll back to the draft cleanly.
 */
export async function runOptimisticMainSend(args: OptimisticMainSendArgs): Promise<void> {
  const {
    api, selected, events, draftTeam, draftAgent, draftCwd, draftScratch, draftPermission, draftWorkMode,
    draftModel, draftEffort, draftSpeed, draftSectionId, draftWorktree, draftWriting, hasWorktreeOffer,
    draftCollaboration, collaborationDirectories, startingDefaults, memberPermission,
    text, images, isNewThread, mentions, videos, references, teamAttachmentsError,
    setThreads, setSelectedId, setView, setDraftWriting, setDraftSectionId, setDraftWorktree, setDraftTeam,
    pinnedThreadIds, shownThread, stickToBottom, optimistic, pendingCreations, refreshThreads, onCreated, onRollback,
  } = args;

  const outgoingImages = images.map(({ data, mimeType }) => ({ data, mimeType }));
  let id = args.selectedId;

  // Demo mode throws DemoError from API write calls; avoid flashing an optimistic thread first.
  if (api.demo) {
    if (!id && draftTeam) {
      if (videos.length) throw new Error(teamAttachmentsError);
      const task = await api.dispatchTeam(draftTeam, text, draftCwd, outgoingImages, references);
      onCreated?.(task.threadId);
      setSelectedId(task.threadId); setDraftTeam(null); setDraftSectionId(null); setView('thread'); refreshThreads();
      return;
    }
    if (!id) {
      const created = await api.createThread({
        agent: draftAgent, cwd: draftCwd, permissionMode: draftPermission,
        workMode: draftWorkMode, model: draftModel, effort: draftEffort, speed: draftSpeed,
        ...(draftSectionId && isNewThread ? { sectionId: draftSectionId } : {}),
        ...(draftScratch ? { scratch: true } : draftWorktree && hasWorktreeOffer ? { worktree: true } : {}),
      });
      id = created.id;
      onCreated?.(id);
      setThreads(prev => [created, ...prev.filter(v => v.id !== created.id)]);
      setSelectedId(id); setDraftSectionId(null); setDraftWorktree(false);
    }
    stickToBottom.current = true;
    await api.startTurn(id, text, outgoingImages, {
      queue: true, mentions, references,
      videos: await Promise.all(videos.map(async video => (await api.uploadVideo(id!, video.file)).id)),
    });
    refreshThreads();
    return;
  }

  // Case 1: New team task — navigate immediately with optimistic thread + message.
  if (!id && draftTeam) {
    if (videos.length) throw new Error(teamAttachmentsError);
    const tempId = createOptimisticThreadId();
    const title = optimisticTitle(text, images, videos);
    const optThread = createOptimisticThread({
      id: tempId, agent: draftAgent, cwd: draftCwd, title, permissionMode: draftPermission,
      workMode: draftWorkMode, model: draftModel, effort: draftEffort, speed: draftSpeed, sectionId: draftSectionId,
    });
    const optTurn = createOptimisticTurn({ threadId: tempId, text, workMode: draftWorkMode, images, videos, afterSeq: 0 });
    setThreads(prev => [optThread, ...prev]);
    optimistic.add(optTurn);
    stickToBottom.current = true;
    setSelectedId(tempId);
    setView('thread');
    try {
      const task = await api.dispatchTeam(draftTeam, text, draftCwd, outgoingImages, references);
      pinnedThreadIds.current.add(task.threadId);
      setThreads(prev => prev.map(item => (item.id === tempId ? { ...item, id: task.threadId } : item)));
      optimistic.migrateThread(tempId, task.threadId);
      onCreated?.(task.threadId);
      if (shownThread.current === tempId) setSelectedId(task.threadId);
      setDraftTeam(null);
      setDraftSectionId(null);
      refreshThreads();
      return;
    } catch (error) {
      optimistic.remove(optTurn.localId);
      setThreads(prev => prev.filter(item => item.id !== tempId));
      if (shownThread.current === tempId) setSelectedId(null);
      throw error;
    }
  }

  // Case 2: Brand-new thread — navigate at 0ms, create on server, migrate seamlessly, clean up on failure.
  if (!id) {
    const tempId = createOptimisticThreadId();
    const title = optimisticTitle(text, images, videos);
    const optThread = createOptimisticThread({
      id: tempId, agent: draftAgent, cwd: draftScratch ? '' : draftCwd, scratch: draftScratch, title,
      permissionMode: draftPermission, workMode: draftWorkMode, model: draftModel, effort: draftEffort,
      speed: draftSpeed, sectionId: draftSectionId && isNewThread ? draftSectionId : null,
    });
    const optTurn = createOptimisticTurn({ threadId: tempId, text, workMode: draftWorkMode, images, videos, afterSeq: 0 });
    let resolveCreated!: (realId: string) => void;
    let rejectCreated!: (err: unknown) => void;
    const creationPromise = new Promise<string>((resolve, reject) => { resolveCreated = resolve; rejectCreated = reject; });
    creationPromise.catch(() => undefined);
    const entry: PendingThreadCreation = { tempId, promise: creationPromise, cancelled: false };
    pendingCreations.current.set(tempId, entry);

    setThreads(prev => [optThread, ...prev]);
    optimistic.add(optTurn);
    stickToBottom.current = true;
    setSelectedId(tempId);
    setView('thread');

    let created: Thread | null = null;
    try {
      created = await api.createThread({
        agent: draftAgent,
        cwd: draftCwd,
        permissionMode: draftPermission,
        workMode: draftWorkMode,
        model: draftModel,
        effort: draftEffort,
        speed: draftSpeed,
        ...(draftSectionId && isNewThread ? { sectionId: draftSectionId } : {}),
        ...(draftScratch ? { scratch: true } : draftWorktree && hasWorktreeOffer ? { worktree: true } : {}),
      });
      if (entry.cancelled) {
        optimistic.remove(optTurn.localId);
        setThreads(prev => prev.filter(item => item.id !== tempId && item.id !== created!.id));
        if (shownThread.current === tempId) setSelectedId(null);
        void api.deleteThread(created.id, { worktree: Boolean(created.worktree), ifEmpty: true }).catch(() => undefined);
        rejectCreated(new Error('cancelled'));
        return;
      }
      pinnedThreadIds.current.add(created.id);
      const shownCreated: Thread = {
        ...created,
        status: 'running',
        title: created.title === 'New thread' ? title : created.title,
        preview: created.preview ?? title,
      };
      setThreads(prev => [shownCreated, ...prev.filter(item => item.id !== tempId && item.id !== created!.id)]);
      optimistic.migrateThread(tempId, created.id);
      onCreated?.(created.id);
      if (shownThread.current === tempId) setSelectedId(created.id);
      setDraftSectionId(null);
      setDraftWorktree(false);
      resolveCreated(created.id);

      if (draftWriting) {
        const updated = await api.setWriting(created.id, draftWriting);
        setThreads(prev => [updated, ...prev.filter(item => item.id !== updated.id)]);
        setDraftWriting(null);
      }
      const collaboration = (draftCollaboration || mentions?.length)
        ? { directories: collaborationDirectories.length ? collaborationDirectories : undefined, defaults: startingDefaults, permission: memberPermission }
        : undefined;
      const uploadedVideos = videos.length
        ? await Promise.all(videos.map(async video => (await api.uploadVideo(created!.id, video.file)).id))
        : [];
      const result = await api.startTurn(created.id, text, outgoingImages, {
        queue: true, mentions, collaboration, references, videos: uploadedVideos,
      });
      if ('turnId' in result) optimistic.bindTurnId(optTurn.localId, result.turnId);
      else optimistic.remove(optTurn.localId);
      refreshThreads();
      return;
    } catch (error) {
      if (!created) {
        optimistic.remove(optTurn.localId);
        rejectCreated(error);
        setThreads(prev => prev.filter(item => item.id !== tempId));
        if (shownThread.current === tempId) setSelectedId(null);
        throw error;
      }
      const outcome = await reconcileOrCleanupEmptyThread(api, created, error);
      if (outcome.kind === 'accepted') {
        optimistic.reconcile(created.id, outcome.events);
        if (outcome.events.length > 0) optimistic.remove(optTurn.localId);
        setThreads(prev => [outcome.thread, ...prev.filter(item => item.id !== tempId && item.id !== created!.id)]);
        refreshThreads();
        return;
      }
      optimistic.remove(optTurn.localId);
      if (outcome.kind === 'deleted') {
        pinnedThreadIds.current.delete(created.id);
        onRollback?.();
        setThreads(prev => prev.filter(item => item.id !== tempId && item.id !== created!.id));
        if (shownThread.current === tempId || shownThread.current === created.id) setSelectedId(null);
      } else {
        refreshThreads();
      }
      throw error;
    } finally {
      pendingCreations.current.delete(tempId);
    }
  }

  // Case 3: Existing thread (including a follow-up typed while an optimistic new thread is still creating).
  let effectiveRunning = args.running;
  if (isOptimisticThreadId(id)) {
    const pending = pendingCreations.current.get(id);
    if (!pending) throw new Error('Thread is still starting');
    id = await pending.promise;
    effectiveRunning = true;
  }
  const collaboration = (draftCollaboration || mentions?.length) && !selected?.coordination
    ? { directories: collaborationDirectories.length ? collaborationDirectories : undefined, defaults: startingDefaults, permission: memberPermission }
    : undefined;
  const optTurn = !effectiveRunning
    ? createOptimisticTurn({ threadId: id, text, workMode: selected?.workMode, images, videos, afterSeq: maxEventSeq(events) })
    : null;
  if (optTurn) optimistic.add(optTurn);
  stickToBottom.current = true;
  try {
    const uploadedVideos = videos.length
      ? await Promise.all(videos.map(async video => (await api.uploadVideo(id!, video.file)).id))
      : [];
    const result = await api.startTurn(id, text, outgoingImages, {
      queue: true, mentions, collaboration, references, videos: uploadedVideos,
    });
    if ('turnId' in result) {
      if (optTurn) optimistic.bindTurnId(optTurn.localId, result.turnId);
      else {
        const late = createOptimisticTurn({ threadId: id, text, workMode: selected?.workMode, images, videos, afterSeq: maxEventSeq(events) });
        late.turnId = result.turnId;
        optimistic.add(late);
      }
    } else if ('queued' in result) {
      if (optTurn) optimistic.remove(optTurn.localId);
      setThreads(prev => appendQueuedToThreads(prev, id!, createQueuedPreview(result.id, text, images, videos, mentions)));
    }
    refreshThreads();
  } catch (error) {
    if (optTurn) optimistic.remove(optTurn.localId);
    throw error;
  }
}
