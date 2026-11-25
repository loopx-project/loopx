import { mergeHistory, watchHistory } from '../history';
import { createOptimisticThread, createOptimisticThreadId, createOptimisticTurn, isOptimisticThreadId, maxEventSeq, mergeOptimisticEvents, useOptimisticTurns } from '../optimistic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Api, type AgentInfo, type AgentMention, type ModelOption, type Thread } from '../api';
import type { PreparedImage } from '../images';
import { restoreVideos, type PreparedVideo } from '../videos';
import { folderName } from '../paths';
import { attachFallbackReference, splitReferences, withReferences } from '../context-references';
import { buildTimeline, splitNotes, withNotes, type StoredEvent } from '../timeline';
import { Composer } from './Composer';
import { ThreadView } from './ThreadView';
import { IconClose } from './icons';
import { t } from '../i18n';
import { effortsForModel, imagesBlocked, ModelPicker } from './Pickers';
import { applyModelPreference, type DraftModelPreference } from '../model-preferences';

interface Props {
  api: Api;
  /** Where the question goes: the same setup as the thread the quoted text came from. */
  agent: string;
  /** The source agent's model and effort capabilities. */
  agentInfo?: AgentInfo;
  /** Models already known for that agent; the picker refreshes them when opened. */
  models: ModelOption[];
  /** Saved per-model preferences for this agent, so switching models restores each model's effort. */
  savedPreference?: DraftModelPreference;
  onModelsLoaded?: (models: ModelOption[]) => void;
  onPreferenceChanged?: () => void;
  agentLabel: string;
  cwd: string;
  permissionMode: string;
  model: string | null;
  effort: string | null;
  /** The passage the questions are about; sent along with the first message. */
  quoteText: string;
  /** The thread the passage was selected in, so the new agent knows where it came from. */
  sourceTitle: string | null;
  /** The source thread id, attached as a read-only reference when native forking is unavailable. */
  sourceThreadId?: string | null;
  /**
   * Set while that thread can be forked: the side chat is then an ephemeral fork of it, the way
   * Codex's side conversation works — the questions land with the whole conversation as context,
   * while the panel itself starts empty. Null (agent cannot fork, no session yet, or a turn is
   * running) falls back to a fresh thread with a read-only reference to the source conversation.
   */
  forkSourceId: string | null;
  /** Why pictures cannot be attached, or null when they can. */
  attachBlocked: string | null;
  onClose: () => void;
  onError: (e: unknown) => void;
  /** The side chat became a thread of its own: keep the sidebar list in step. */
  onThreadCreated: (thread: Thread) => void;
  /** Threads an answer here may name in passing, turned into jump links. */
  threads?: Array<{ id: string; title: string }>;
  /** Opens the thread such a mention names; the panel closes so the thread is in full view. */
  onOpenThread?: (threadId: string) => void;
}

/**
 * Asking about a selected passage without disturbing the conversation it came from: a panel over
 * the thread with its own composer. Nothing exists on the server until the first question is sent,
 * which opens a new thread carrying the quoted text; it then lives in the sidebar like any other.
 */
export function SideChat({
  api,
  agent,
  agentLabel,
  cwd,
  permissionMode,
  agentInfo,
  models: cachedModels,
  savedPreference,
  onModelsLoaded,
  onPreferenceChanged,
  model,
  effort,
  quoteText,
  sourceTitle,
  sourceThreadId,
  forkSourceId,
  attachBlocked,
  onClose,
  onError,
  onThreadCreated,
  threads,
  onOpenThread,
}: Props) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [draftModel, setDraftModel] = useState(model);
  const [draftEffort, setDraftEffort] = useState(effort);
  const [draftEdited, setDraftEdited] = useState<{ model: boolean; effort: boolean }>({ model: false, effort: false });
  const draftPref = useRef<DraftModelPreference>(applyModelPreference(savedPreference, { model, effort }));
  const [models, setModels] = useState(cachedModels);
  const [modelsStatus, setModelsStatus] = useState<'loading' | 'ready' | 'error'>(cachedModels.length || agentInfo?.capabilities.modelCatalog === false ? 'ready' : 'loading');
  const [events, setEvents] = useState<StoredEvent[]>([]);
  const optimistic = useOptimisticTurns();
  const [restore, setRestore] = useState<{ text: string; nonce: number } | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // Where a fork's inherited history ends: the panel shows only what is asked here. Written before
  // the first question is sent, so no event of ours can land below it and be hidden as inherited.
  const baseSeq = useRef(0);
  const threadId = thread?.id ?? null;
  const displayEvents = useMemo(() => mergeOptimisticEvents(events, optimistic.turns, threadId), [events, optimistic.turns, threadId]);
  const timeline = useMemo(() => buildTimeline(displayEvents), [displayEvents]);
  const running = timeline.running || thread?.status === 'running';
  const caps = agentInfo?.capabilities;
  const shownModel = thread ? thread.model : draftModel;
  const shownEffort = thread ? thread.effort : draftEffort;
  const effectiveAttachBlocked = (caps?.images === false ? t('app.noImages', agentLabel) : attachBlocked) ?? imagesBlocked(models, shownModel);

  useEffect(() => {
    if (!thread && !draftEdited.model && !draftEdited.effort) {
      draftPref.current = applyModelPreference(savedPreference, { model: draftModel, effort: draftEffort });
    }
  }, [savedPreference, thread, draftEdited.model, draftEdited.effort, draftModel, draftEffort]);

  const errorRef = useRef(onError);
  errorRef.current = onError;
  const modelsLoadedRef = useRef(onModelsLoaded);
  modelsLoadedRef.current = onModelsLoaded;
  const modelRequest = useRef(0);
  const loadModels = useCallback((refresh = false) => {
    if (agentInfo?.capabilities.modelCatalog === false) return;
    const ticket = ++modelRequest.current;
    setModelsStatus(current => current === 'ready' ? 'ready' : 'loading');
    void api.models(agent, refresh).then(list => {
      if (modelRequest.current !== ticket) return;
      setModels(list);
      setModelsStatus('ready');
      modelsLoadedRef.current?.(list);
    }).catch(error => {
      if (modelRequest.current !== ticket) return;
      setModelsStatus(current => current === 'ready' ? 'ready' : 'error');
      errorRef.current(error);
    });
  }, [api, agent, agentInfo?.capabilities.modelCatalog]);

  useEffect(() => {
    if (cachedModels.length > 0) {
      setModels(cachedModels);
      setModelsStatus('ready');
    }
  }, [cachedModels]);

  useEffect(() => {
    setModels(cachedModels);
    if (agentInfo?.capabilities.modelCatalog === false) {
      setModelsStatus('ready');
      return;
    }
    setModelsStatus(cachedModels.length ? 'ready' : 'loading');
    // A cached catalog is enough until the person opens the picker; without one, fetch it now.
    if (cachedModels.length === 0) loadModels();
    return () => { modelRequest.current++; };
    // The catalog belongs to the source agent, not to whichever thread later opens here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent, loadModels]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    setEvents([]);
    if (!threadId || isOptimisticThreadId(threadId)) return;
    const ctrl = new AbortController();
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const visible = (events: StoredEvent[]) => thread?.forkedFrom ? events.filter(e => e.seq > baseSeq.current) : events;
    void watchHistory(api, threadId, ctrl.signal, {
      minimumSeq: thread?.forkedFrom ? baseSeq.current : undefined,
      page: page => { const next = visible(page.events); setEvents(next); optimistic.reconcile(threadId, next); },
      events: batch => {
        setEvents(previous => {
          const next = visible(mergeHistory(previous, batch));
          optimistic.reconcile(threadId, next);
          return next;
        });
        if (batch.some(e => e.type.startsWith('turn.') || e.type === 'history.reset')) {
          clearTimeout(refreshTimer);
          refreshTimer = setTimeout(() => {
            api.thread(threadId).then(t => {
              if (!ctrl.signal.aborted) setThread(cur => cur?.id === threadId ? t : cur);
            }).catch(() => undefined);
          }, 300);
        }
      },
      error: onError,
    });
    return () => { ctrl.abort(); clearTimeout(refreshTimer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, threadId]);

  // Small panel, simple rule: stay at the bottom unless the reader has moved up.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [displayEvents]);

  const onScroll = () => {
    const el = scrollerRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  const send = async (text: string, images: PreparedImage[], _mentions?: AgentMention[], videos: PreparedVideo[] = [], _onCreated?: (draftKey: string) => void, references: string[] = []) => {
    const outgoing = images.map(({ data, mimeType }) => ({ data, mimeType }));
    if (threadId && !isOptimisticThreadId(threadId)) {
      const optTurn = !running ? createOptimisticTurn({ threadId, text, workMode: thread?.workMode, images, videos, afterSeq: maxEventSeq(events) }) : null;
      if (optTurn) { optimistic.add(optTurn); stick.current = true; }
      try {
        const result = await api.startTurn(threadId, text, outgoing, { queue: true, references, videos: await Promise.all(videos.map(async video => (await api.uploadVideo(threadId, video.file)).id)) });
        if ('turnId' in result) {
          if (optTurn) optimistic.bindTurnId(optTurn.localId, result.turnId);
          else {
            const late = createOptimisticTurn({ threadId, text, workMode: thread?.workMode, images, videos, afterSeq: maxEventSeq(events) });
            late.turnId = result.turnId;
            optimistic.add(late);
          }
        } else if (optTurn) optimistic.remove(optTurn.localId);
        return;
      } catch (error) {
        if (optTurn) optimistic.remove(optTurn.localId);
        throw error;
      }
    }
    const title = t('sideChat.threadTitle', quoteText.replace(/\s+/g, ' ').slice(0, 24));
    const tempId = createOptimisticThreadId();
    const optTurn = createOptimisticTurn({ threadId: tempId, text, images, videos, afterSeq: 0 });
    if (!api.demo) {
      setThread(createOptimisticThread({ id: tempId, agent, cwd, title, permissionMode, model: draftModel, effort: draftEffort }));
      optimistic.add(optTurn);
      stick.current = true;
    }
    let created: Thread | null = null;
    let firstText = text;
    let firstReferences = [...references];
    const choicePatch = {
      ...(draftEdited.model || draftModel !== model ? { model: draftModel } : {}),
      ...(draftEdited.effort || (!draftEdited.model && draftEffort !== effort) ? { effort: draftEffort } : {}),
    };
    const choiceChanged = choicePatch.model !== undefined || choicePatch.effort !== undefined;
    try {
      if (forkSourceId) {
        // The fork carries the conversation the passage was selected from, so the first question
        // already lands with all its context. The message itself stays just the quote and the
        // question: the history around it is the attribution. The boundary between inherited
        // history and this panel's turns is read before the question is sent, or the question
        // could be counted as inherited.
        const forked = await api.forkThread(forkSourceId);
        created = await api.updateThread(forked.id, {
          title,
          ...(choiceChanged ? { ...choicePatch, rememberModelChoice: true } : {}),
        });
        baseSeq.current = (await api.historyPosition(created.id)).snapshotSeq;
      } else {
        // A fresh agent seeing a bare snippet has nothing to go on but the words, so the message
        // names the source thread and attaches a read-only snapshot of it when available.
        created = await api.createThread({ agent, cwd, permissionMode, model: draftModel, effort: draftEffort, title });
        if (choiceChanged) created = await api.updateThread(created.id, { ...choicePatch, rememberModelChoice: true });
        const { notes, body } = splitNotes(text);
        const referenced = splitReferences(body);
        if (sourceTitle && notes.length) {
          firstText = withNotes(notes, withReferences(referenced.references, `${t('sideChat.notesFrom', sourceTitle)}\n\n${referenced.body}`));
        }
        if (!api.demo && sourceThreadId
          && referenced.references.length < 12
          && firstReferences.length < 12
          && !referenced.references.some(item => item.kind === 'thread' && item.sourceId === sourceThreadId)) {
          const sourceReference = await api.createReference({ kind: 'thread', sourceId: sourceThreadId }).catch(() => null);
          if (sourceReference) {
            firstText = attachFallbackReference(firstText, sourceReference);
            if (!firstReferences.includes(sourceReference.id)) firstReferences.push(sourceReference.id);
          }
        }
      }
      if (choiceChanged) onPreferenceChanged?.();
      draftPref.current = applyModelPreference(draftPref.current, { model: created.model, effort: created.effort });
      if (!api.demo) optimistic.migrateThread(tempId, created.id);
      setThread({ ...created, status: 'running' });
      onThreadCreated(created);
      const result = await api.startTurn(created.id, firstText, outgoing, { queue: true, references: firstReferences, videos: await Promise.all(videos.map(async video => (await api.uploadVideo(created!.id, video.file)).id)) });
      if (!api.demo) {
        if ('turnId' in result) optimistic.bindTurnId(optTurn.localId, result.turnId);
        else optimistic.remove(optTurn.localId);
      }
    } catch (error) {
      if (!api.demo) {
        optimistic.remove(optTurn.localId);
        if (created) void api.deleteThread(created.id).catch(() => undefined);
        setThread(null);
      }
      throw error;
    }
  };

  const loadImage = (attachmentId: string) =>
    optimistic.imageBlob(attachmentId) ? Promise.resolve(optimistic.imageBlob(attachmentId)!) : threadId && !isOptimisticThreadId(threadId) ? api.attachment(threadId, attachmentId) : Promise.reject(new Error('no thread'));
  const loadFile = (path: string) => (threadId ? api.threadFile(threadId, path) : Promise.reject(new Error('no thread')));

  const roots = useMemo(() => (thread ? [thread.cwd, thread.cwdReal] : [cwd]), [thread, cwd]);

  const updateChoice = (patch: { model?: string | null; effort?: string | null }) => {
    const nextPref = applyModelPreference(draftPref.current, patch);
    draftPref.current = nextPref;
    const nextModel = patch.model !== undefined ? patch.model : shownModel;
    const levels = effortsForModel(models, nextModel, caps?.effortLevels ?? []);
    const nextEffort = patch.effort !== undefined ? patch.effort : nextPref.effort && levels.includes(nextPref.effort) ? nextPref.effort : null;
    if (!thread) {
      if (patch.model !== undefined) {
        setDraftModel(patch.model);
        setDraftEffort(nextEffort);
        setDraftEdited({ model: true, effort: patch.effort !== undefined });
      } else if (patch.effort !== undefined) {
        setDraftEffort(patch.effort);
        setDraftEdited(prev => ({ ...prev, effort: true }));
      }
      return;
    }
    const before = thread;
    const id = thread.id;
    setThread({ ...thread, ...patch, effort: nextEffort });
    void api.updateThread(id, { ...patch, rememberModelChoice: true }).then(updated => {
      draftPref.current = applyModelPreference(draftPref.current, { model: updated.model, effort: updated.effort });
      setThread(current => current?.id === id ? updated : current);
      onThreadCreated(updated);
      onPreferenceChanged?.();
    }).catch(error => {
      setThread(current => current?.id === id ? before : current);
      onError(error);
    });
  };

  const chooseModel = (next: string | null) => {
    updateChoice({ model: next });
  };

  const modelPicker = caps && <ModelPicker
    catalog={caps.modelCatalog}
    catalogStatus={modelsStatus}
    onRetryCatalog={() => loadModels(true)}
    models={models}
    model={shownModel}
    efforts={caps.effortLevels ?? []}
    effort={shownEffort}
    onOpen={() => loadModels(true)}
    onModel={chooseModel}
    onEffort={effort => updateChoice({ effort })}
  />;

  return (
    <div className="dialog-scrim side-chat-scrim" onClick={onClose}>
      <aside className="side-chat" role="dialog" aria-modal="true" aria-label={t('sideChat.title')} onClick={(e) => e.stopPropagation()}>
        <header className="side-chat-head">
          <div className="side-chat-heading">
            <div className="side-chat-title">{t('sideChat.title')}</div>
            <div className="side-chat-sub">
              {agentLabel} · {folderName(cwd)}
            </div>
          </div>
          <button className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
            <IconClose />
          </button>
        </header>

        <div className="side-chat-body" ref={scrollerRef} onScroll={onScroll}>
          {thread ? (
            <ThreadView
              historyApi={api}
              historyThreadId={thread.id}
              historyTitle={thread.title}
              items={timeline.items}
              roots={roots}
              loadImage={loadImage}
              loadFile={loadFile}
              threads={threads}
              onOpenThread={onOpenThread}
            />
          ) : (
            <div className="side-chat-intro">
              <div className="side-chat-passage" title={quoteText}>
                {quoteText}
              </div>
            </div>
          )}
        </div>

        <Composer
          running={running}
          placeholder={running ? t('sideChat.queuePlaceholder') : t('sideChat.placeholder')}
          onSend={send}
          onStop={() => threadId && api.cancel(threadId).then(result => { if (result.dropped.length) setRestore({ text: result.dropped.join('\n\n'), nonce: Date.now() }); }).catch(onError)}
          header={null}
          left={null}
          right={modelPicker}
          commands={[]}
          actions={[]}
          hiddenCommands={[]}
          onAction={() => undefined}
          attachBlocked={effectiveAttachBlocked}
          onError={(message) => onError(new Error(message))}
          queued={thread?.queued ?? []}
          onRemoveQueued={(id) => threadId && api.removeQueued(threadId, id).catch(onError)}
          onEditQueued={async (id) => {
            if (!threadId) throw new Error(t('composer.noThread'));
            const taken = await api.removeQueued(threadId, id);
            return { ...taken, videos: await restoreVideos(api, threadId, taken.videos) };
          }}
          onSendQueuedNow={(id) => threadId && api.sendQueuedNow(threadId, id).catch(onError)}
          restore={restore}
          referenceSource={{ api, cwd: thread?.cwd ?? cwd, threadId: threadId ?? undefined }}
          note={{ id: 'side-chat-passage', quote: quoteText, comment: '', nonce: 1 }}
        />
      </aside>
    </div>
  );
}
