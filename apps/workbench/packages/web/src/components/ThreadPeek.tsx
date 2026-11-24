import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MAX_QUEUED, type AgentInfo, type Api, type ModelOption, type SlashCommand, type Thread, type WorkMode } from '../api';
import { historyPage, peekCachedPage, prependHistory, refreshHistoryPage, mergeHistory, mergeHistoryPage, watchHistory } from '../history';
import { useMinWidth } from '../hooks';
import { useReadingPosition } from '../use-reading-position';
import { buildTimeline, type StoredEvent } from '../timeline';
import { restoreVideos, type PreparedVideo } from '../videos';
import { ActionSheet, type SheetTarget } from './ActionSheet';
import { Composer, type DraftNote } from './Composer';
import { ConversationSkeleton } from './ConversationSkeleton';
import { effortsForModel, imagesBlocked, imagesNote, ModelPicker, PermissionPicker, speedsForModel, UsageMeter } from './Pickers';
import { ThreadView } from './ThreadView';
import { IconChevron, IconClose, IconExternal } from './icons';
import { t } from '../i18n';
import { AgentPanelFrame } from './AgentPanelFrame';

/** A conversation reader: overlay for the board, shared team panel beside the assistant. */
export function ThreadPeek({ api, initial, agents, draftKey, returnFocus, onClose, onOpen, onChange, side = false, noteSignal, onNoteConsumed, onNotesChange }: {
  api: Api; initial: Thread | string; agents?: AgentInfo[]; side?: boolean; draftKey: string; returnFocus: HTMLElement | null;
  onClose: () => void; onOpen: () => void; onChange: (thread: Thread) => void;
  noteSignal?: { draftKey: string; note: DraftNote & { nonce: number } } | null;
  onNoteConsumed?: (id: string) => void;
  onNotesChange?: (target: string, notes: DraftNote[]) => void;
}) {
  const fullOverlay = side && !useMinWidth(901);
  const id = typeof initial === 'string' ? initial : initial.id;
  const [thread, setThread] = useState<Thread | null>(typeof initial === 'string' ? null : initial);
  const [fetchedAgents, setFetchedAgents] = useState<AgentInfo[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelsStatus, setModelsStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [modeBusy, setModeBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const panel = useRef<HTMLElement>(null);
  const modelsReq = useRef(0);
  const [events, setEvents] = useState<StoredEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [restore, setRestore] = useState<{ text: string; nonce: number } | null>(null);
  const [olderError, setOlderError] = useState('');
  const [cursor, setCursor] = useState<number | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const stick = useRef(true);
  const activeThread = useRef(id);
  activeThread.current = id;
  const olderRequest = useRef<object | null>(null);
  const readingPosition = useReadingPosition(scroll, stick, id, events, String(retry), false);
  const callbacks = useRef({ onClose, onChange, onNotesChange });
  callbacks.current = { onClose, onChange, onNotesChange };
  const handlePeekNotes = useCallback((notes: DraftNote[]) => { callbacks.current.onNotesChange?.(draftKey, notes); }, [draftKey]);
  useEffect(() => () => { callbacks.current.onNotesChange?.(draftKey, []); }, [draftKey]);
  const timeline = useMemo(() => buildTimeline(events), [events]);
  const waiting = useMemo(() => timeline.items.flatMap((item): SheetTarget[] =>
    item.kind === 'approval' && item.decision === null ? [{ kind: 'approval', item }]
      : item.kind === 'input' && item.answer === null ? [{ kind: item.inputKind === 'plan' ? 'plan-approval' : 'question', item }] : []), [timeline]);
  const reportError = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const refresh = useCallback(async () => {
    const next = await api.thread(id);
    setThread(next); callbacks.current.onChange(next);
  }, [api, id]);
  useEffect(() => {
    if (agents !== undefined) return;
    let live = true;
    void api.agents().then(list => { if (live) setFetchedAgents(list); }).catch(() => undefined);
    return () => { live = false; };
  }, [api, agents]);
  const resolvedAgents = agents ?? fetchedAgents;
  const agentInfo = thread ? resolvedAgents.find(agent => agent.id === thread.agent) : undefined;
  const caps = agentInfo?.capabilities;
  const loadModels = useCallback((refreshCatalog = false) => {
    const req = ++modelsReq.current;
    if (!thread?.agent || caps?.modelCatalog === false) {
      setModels([]);
      setModelsStatus('ready');
      return;
    }
    setModelsStatus(current => current === 'ready' ? 'ready' : 'loading');
    void api.models(thread.agent, refreshCatalog)
      .then(list => {
        if (modelsReq.current !== req) return;
        setModels(list);
        setModelsStatus('ready');
      })
      .catch(() => {
        if (modelsReq.current !== req) return;
        setModelsStatus(current => current === 'ready' ? 'ready' : 'error');
      });
  }, [api, thread?.agent, caps?.modelCatalog]);
  useEffect(() => {
    setModels([]);
    setModelsStatus('loading');
    loadModels();
  }, [loadModels]);
  useEffect(() => {
    if (!thread?.agent) { setCommands([]); return; }
    let live = true;
    void api.commands(thread.agent, thread.cwd)
      .then(list => { if (live) setCommands(list); })
      .catch(() => { if (live) setCommands([]); });
    return () => { live = false; };
  }, [api, thread?.agent, thread?.cwd]);
  useEffect(() => {
    if (!thread?.unread || document.hidden) return;
    let live = true;
    void api.updateThread(thread.id, { unread: false }).then(updated => {
      if (live) { setThread(updated); callbacks.current.onChange(updated); }
    }).catch(reportError);
    return () => { live = false; };
  }, [api, thread?.id, thread?.unread]);
  useEffect(() => {
    const visible = () => { if (!document.hidden) void refresh().catch(reportError); };
    document.addEventListener('visibilitychange', visible);
    return () => document.removeEventListener('visibilitychange', visible);
  }, [refresh]);
  useEffect(() => {
    const previous = returnFocus;
    panel.current?.focus({ preventScroll: true });
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.isComposing && !document.querySelector('.pop-panel, [role=menu], [role=dialog][aria-modal=true]')) { event.preventDefault(); callbacks.current.onClose(); }
      if (side && event.key === 'Tab' && window.matchMedia('(max-width: 900px)').matches) {
        const items = [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], textarea, [tabindex="0"]') ?? [])].filter(el => el.getClientRects().length);
        const first = items[0], last = items.at(-1);
        if (first && last && (event.shiftKey ? document.activeElement === first : document.activeElement === last)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
      }
    };
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('keydown', escape); if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cached = peekCachedPage(api, id)?.page ?? null;
    olderRequest.current = null;
    setLoadingOlder(false);
    setOlderError('');
    stick.current = true;
    setError('');
    if (cached) {
      setEvents(cached.events);
      setCursor(cached.hasOlder ? cached.nextCursor : null);
      setLoaded(true);
    }
    void api.thread(id).then(value => {
      if (ctrl.signal.aborted) return;
      setThread(value); callbacks.current.onChange(value);
      return watchHistory(api, id, ctrl.signal, {
        page: page => { setEvents(page.events); setCursor(page.hasOlder ? page.nextCursor : null); setLoaded(true); setError(''); },
        events: batch => {
          setEvents(previous => mergeHistory(previous, batch));
          if (batch.some(e => e.type.startsWith('turn.') || e.type === 'history.reset' || e.type === 'thread.updated')) {
            clearTimeout(timer);
            timer = setTimeout(() => { if (!ctrl.signal.aborted) void refresh().catch(reportError); }, 200);
          }
        },
        error: reportError,
      });
    }).catch(error => { if (!ctrl.signal.aborted) reportError(error); });
    return () => { ctrl.abort(); clearTimeout(timer); };
  }, [api, id, refresh, retry]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (stick.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [events]);
  const loadImage = useCallback((attachmentId: string) => api.attachment(id, attachmentId), [api, id]);
  const loadFile = useCallback((path: string) => api.threadFile(id, path), [api, id]);
  const loadOlder = async () => {
    const targetId = id;
    if (cursor === null || loadingOlder || olderRequest.current) return;
    const request = {};
    olderRequest.current = request;
    stick.current = false;
    setOlderError('');
    setLoadingOlder(true);
    try {
      const page = await historyPage(api, targetId, cursor);
      if (activeThread.current !== targetId || olderRequest.current !== request) return;
      stick.current = false;
      const firstTurn = page.events.find(event => event.type === 'turn.started') ?? page.events[0];
      readingPosition.preparePrepend(firstTurn?.seq);
      setEvents(previous => prependHistory(page.events, previous));
      setCursor(page.hasOlder ? page.nextCursor : null);
    } catch (error) {
      if (olderRequest.current === request) setOlderError(error instanceof Error ? error.message : String(error));
    } finally {
      if (olderRequest.current === request) {
        olderRequest.current = null;
        setLoadingOlder(false);
      }
    }
  };
  const running = timeline.running || thread?.status === 'running';
  const effortLevels = thread ? effortsForModel(models, thread.model ?? null, caps?.effortLevels ?? []) : [];
  const canCompact = caps?.compact ?? commands.some(command => command.name === 'compact' && command.kind !== 'skill');
  const hasContextCommand = commands.some(command => command.name === 'context' && command.kind !== 'skill');
  const hiddenCommands = [...(models.length ? ['model'] : []), ...(effortLevels.length ? ['effort'] : []), ...(caps?.quota ? ['usage'] : []), ...(caps?.goal ? ['goal'] : [])];
  const attachBlocked = thread && caps ? (!caps.images ? t('app.noImages', agentInfo?.label ?? thread.agent) : imagesBlocked(models, thread.model ?? null)) : null;
  const updateSettings = (patch: { speed?: string | null; permissionMode?: string; model?: string | null; effort?: string | null }) => {
    if (!thread) return;
    const before = thread;
    const nextPatch = { ...patch };
    if (patch.model !== undefined) {
      const nextEfforts = effortsForModel(models, patch.model, caps?.effortLevels ?? []);
      if (thread.effort && !nextEfforts.includes(thread.effort)) nextPatch.effort = null;
      const nextSpeeds = speedsForModel(models, patch.model);
      if (thread.speed && nextSpeeds.length > 0 && !nextSpeeds.some(item => item.id === thread.speed)) nextPatch.speed = null;
    }
    setThread({ ...thread, ...nextPatch });
    void api.updateThread(thread.id, { ...nextPatch, rememberModelChoice: true }).then(updated => {
      setThread(current => current?.id === updated.id ? updated : current);
      callbacks.current.onChange(updated);
    }).catch(e => {
      setThread(current => current?.id === before.id ? before : current);
      reportError(e);
    });
  };
  const changeWorkMode = async (mode: WorkMode) => {
    if (!thread || (thread.workMode ?? 'execute') === mode) return;
    setModeBusy(true);
    try {
      const updated = await api.updateThread(thread.id, { workMode: mode });
      setThread(updated);
      callbacks.current.onChange(updated);
    } catch (e) { reportError(e); }
    finally { setModeBusy(false); }
  };
  const sendTurn = async (text: string, images: { data: string; mimeType: string }[] = [], videos: PreparedVideo[] = [], references?: string[]) => {
    if (!thread) return;
    const uploadedVideos = videos.length ? await Promise.all(videos.map(async video => (await api.uploadVideo(thread.id, video.file)).id)) : [];
    await api.startTurn(thread.id, text, images, {
      queue: true,
      ...(uploadedVideos.length ? { videos: uploadedVideos } : {}),
      ...(references !== undefined ? { references } : {}),
    });
    await refresh();
  };
  const composerLeft = thread && caps ? <PermissionPicker
    modes={caps.permissionModes}
    value={thread.permissionMode}
    canAsk={caps.approvals ?? true}
    onChange={permissionMode => updateSettings({ permissionMode })}
  /> : null;
  const composerRight = thread ? <>
    {(timeline.usage || canCompact) && <UsageMeter
      key={thread.id}
      usage={timeline.usage}
      sample={timeline.lastSample}
      api={api}
      threadId={thread.id}
      onCompact={canCompact ? () => sendTurn('/compact') : undefined}
      onContext={hasContextCommand ? () => sendTurn('/context') : undefined}
      running={running}
      compactQueued={thread.queued?.some(message => message.text.trim() === '/compact')}
      compactDisabled={(thread.queued?.length ?? 0) >= MAX_QUEUED}
    />}
    {caps && <ModelPicker
      catalog={caps.modelCatalog}
      catalogStatus={modelsStatus}
      onRetryCatalog={() => loadModels(true)}
      models={models}
      model={thread.model ?? null}
      efforts={caps.effortLevels ?? []}
      effort={thread.effort ?? null}
      speed={thread.speed ?? null}
      onSpeed={caps.speed ? speed => updateSettings({ speed }) : undefined}
      onOpen={() => loadModels(true)}
      onModel={model => updateSettings({ model })}
      onEffort={effort => updateSettings({ effort })}
    />}
  </> : null;
  const title = thread?.title || t('view.openThread');
  const content = <>
    <header className={side ? "agent-panel-head" : "thread-peek-head"}>
      {fullOverlay && <button ref={close} className="icon-btn" onClick={onClose} title={t('threadPeek.back')} aria-label={t('threadPeek.back')}><span className="coord-back"><IconChevron /></span></button>}
      <div className="agent-panel-heading"><strong title={title}>{title}</strong></div>
      {(running || thread?.status === 'error') && <span className="thread-peek-status" role="status">{t(thread?.status === 'error' ? 'status.error' : 'status.running')}</span>}
      <button disabled={!thread} className="icon-btn" onClick={onOpen} title={t('threadPeek.open')} aria-label={t('threadPeek.open')}><IconExternal /></button>
      {!fullOverlay && <button ref={close} className="icon-btn" onClick={onClose} title={t(side ? 'threadPeek.back' : 'common.close')} aria-label={t(side ? 'threadPeek.back' : 'common.close')}><IconClose /></button>}
    </header>
    {error && <p className="thread-peek-error" role="alert">{error} <button className="ghost" onClick={() => setRetry(value => value + 1)}>{t('common.retry')}</button></p>}
    {thread?.waiting && waiting.length === 0 && <button className="thread-peek-attention ghost" onClick={onOpen}>{t('threadPeek.needsAction')}</button>}
    <div className={side ? "agent-panel-body" : "thread-peek-body"} ref={scroll} tabIndex={0} data-note-target={draftKey} data-note-thread={id} onScroll={() => {
      const el = scroll.current;
      if (!el || readingPosition.didRestore()) return;
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      readingPosition.capture();
    }}>
      {!loaded || !thread ? (!error && <ConversationSkeleton compact />) : <ThreadView historyApi={api} historyThreadId={thread.id} historyTitle={thread.title}
        items={timeline.items} roots={[thread.cwd, thread.cwdReal]} loadImage={loadImage} loadFile={loadFile}
        hasOlder={cursor !== null} loadingOlder={loadingOlder} olderError={olderError || undefined} onLoadOlder={() => void loadOlder()} />}
    </div>
    {thread && <Composer attachBlocked={attachBlocked} attachNote={imagesNote(models, thread.model ?? null)} restore={restore} note={noteSignal?.draftKey === draftKey ? noteSignal.note : null} onNoteConsumed={onNoteConsumed} onNotesChange={onNotesChange ? handlePeekNotes : undefined} draftKey={draftKey}
      running={running} disabled={modeBusy}
      placeholder={thread.workMode === 'plan' && !running ? t('composerMenu.planOn') : running ? t('sideChat.queuePlaceholder') : t('app.placeholder.continue')}
      referenceSource={!api.demo ? { api, cwd: thread.cwd, threadId: thread.id, sourceId: thread.handover?.sourceId } : undefined}
      planMode={caps?.workModes?.includes('plan') ? { active: thread.workMode === 'plan', disabled: running || modeBusy, onChange: active => void changeWorkMode(active ? 'plan' : 'execute') } : undefined}
      onSend={async (text, images, _mentions, videos = [], _onCreated, references = []) => {
        await sendTurn(text, images.map(({ data, mimeType }) => ({ data, mimeType })), videos, references);
      }}
      onStop={() => void api.cancel(thread.id).then(result => { if (result.dropped.length) setRestore({ text: result.dropped.join('\n\n'), nonce: Date.now() }); void refresh(); }).catch(reportError)} onError={setError}
      header={null} left={composerLeft} right={composerRight} commands={commands} actions={[]} hiddenCommands={hiddenCommands} onAction={() => undefined}
      queued={thread.queued} modelQueue={thread.modelQueue ?? null}
      onRemoveQueued={id => void api.removeQueued(thread.id, id).then(refresh).catch(reportError)}
      onEditQueued={async id => { const taken = await api.removeQueued(thread.id, id); return { ...taken, videos: await restoreVideos(api, thread.id, taken.videos) }; }}
      onSendQueuedNow={id => void api.sendQueuedNow(thread.id, id).then(refresh).catch(reportError)} />}
    {thread && waiting[0] && <ActionSheet key={waiting[0].item.key} target={waiting[0]} index={1} total={waiting.length}
      roots={[thread.cwd, thread.cwdReal]}
      onAnswer={async (id, answer) => { await api.answerInput(id, answer); await refresh(); }}
      onDecide={async (id, option) => {
        let failure: unknown;
        try { await api.decide(id, option === null ? { cancel: true } : { optionId: option }); } catch (error) { failure = error; }
        const page = await refreshHistoryPage(api, thread.id);
        setEvents(previous => mergeHistoryPage(previous, page));
        if (!page.events.some(event => event.type === 'approval.resolved' && event.data.approvalId === id)) throw failure ?? new Error(t('sheet.approval.syncError'));
        await refresh();
      }}
      onRejectPlan={onOpen} onSkipPlan={onOpen} />}
  </>;
  return side ? <AgentPanelFrame detail panelRef={panel} label={title}>{content}</AgentPanelFrame>
    : <aside ref={panel} tabIndex={-1} className="thread-peek" role="complementary" aria-label={title}>{content}</aside>;
}
