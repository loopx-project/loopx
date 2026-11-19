import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Api, Thread } from '../api';
import { historyPage, refreshHistoryPage, mergeHistory, mergeHistoryPage, watchHistory } from '../history';
import { buildTimeline, type StoredEvent } from '../timeline';
import { restoreVideos } from '../videos';
import { ActionSheet, type SheetTarget } from './ActionSheet';
import { Composer } from './Composer';
import { ThreadView } from './ThreadView';
import { IconClose, IconExternal } from './icons';
import { t } from '../i18n';

/** A conversation reader with its own stream and draft; opening it never changes the board's layout. */
export function ThreadPeek({ api, initial, draftKey, returnFocus, onClose, onOpen, onChange }: {
  api: Api; initial: Thread; draftKey: string; returnFocus: HTMLElement | null;
  onClose: () => void; onOpen: () => void; onChange: (thread: Thread) => void;
}) {
  const [thread, setThread] = useState(initial);
  const [events, setEvents] = useState<StoredEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState<number | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const stick = useRef(true);
  const callbacks = useRef({ onClose, onChange });
  callbacks.current = { onClose, onChange };
  const timeline = useMemo(() => buildTimeline(events), [events]);
  const waiting = useMemo(() => timeline.items.flatMap((item): SheetTarget[] =>
    item.kind === 'approval' && item.decision === null ? [{ kind: 'approval', item }]
      : item.kind === 'input' && item.answer === null ? [{ kind: item.inputKind === 'plan' ? 'plan-approval' : 'question', item }] : []), [timeline]);
  const reportError = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const refresh = useCallback(async () => {
    const next = await api.thread(initial.id);
    setThread(next); callbacks.current.onChange(next);
  }, [api, initial.id]);
  useEffect(() => {
    if (!thread.unread || document.hidden) return;
    let live = true;
    void api.updateThread(thread.id, { unread: false }).then(updated => {
      if (live) { setThread(updated); callbacks.current.onChange(updated); }
    }).catch(reportError);
    return () => { live = false; };
  }, [api, thread.id, thread.unread]);
  useEffect(() => {
    const visible = () => { if (!document.hidden) void refresh().catch(reportError); };
    document.addEventListener('visibilitychange', visible);
    return () => document.removeEventListener('visibilitychange', visible);
  }, [refresh]);
  useEffect(() => {
    const previous = returnFocus;
    close.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.isComposing && !(event.target instanceof Element && event.target.closest('[role=menu], [role=dialog]'))) { callbacks.current.onClose(); }
    };
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('keydown', escape); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    void api.thread(initial.id).then(value => { if (!ctrl.signal.aborted) { setThread(value); callbacks.current.onChange(value); } }).catch(reportError);
    void watchHistory(api, initial.id, ctrl.signal, {
      page: page => { setEvents(page.events); setCursor(page.hasOlder ? page.nextCursor : null); setLoaded(true); setError(''); },
      events: batch => {
        setEvents(previous => mergeHistory(previous, batch));
        if (batch.some(e => e.type.startsWith('turn.') || e.type === 'history.reset')) {
          clearTimeout(timer);
          timer = setTimeout(() => { if (!ctrl.signal.aborted) void refresh().catch(reportError); }, 200);
        }
      },
      error: reportError,
    });
    return () => { ctrl.abort(); clearTimeout(timer); };
  }, [api, initial.id, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (stick.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [events]);
  const loadImage = useCallback((id: string) => api.attachment(initial.id, id), [api, initial.id]);
  const loadFile = useCallback((path: string) => api.threadFile(initial.id, path), [api, initial.id]);
  const loadOlder = async () => {
    if (cursor === null || loadingOlder) return;
    setLoadingOlder(true);
    const element = scroll.current;
    const height = element?.scrollHeight ?? 0;
    const top = element?.scrollTop ?? 0;
    try {
      const page = await historyPage(api, initial.id, cursor);
      stick.current = false;
      setEvents(previous => mergeHistoryPage(previous, page));
      setCursor(page.hasOlder ? page.nextCursor : null);
      requestAnimationFrame(() => { if (element) element.scrollTop = top + element.scrollHeight - height; });
    } catch (error) { reportError(error); }
    finally { setLoadingOlder(false); }
  };
  return <aside className="thread-peek" role="complementary" aria-label={thread.title}>
    <header className="thread-peek-head">
      <strong title={thread.title}>{thread.title}</strong>
      {(timeline.running || thread.status === 'running' || thread.status === 'error') && <span className="thread-peek-status" role="status">{t(thread.status === 'error' ? 'status.error' : 'status.running')}</span>}
      <button className="icon-btn" onClick={onOpen} title={t('threadPeek.open')} aria-label={t('threadPeek.open')}><IconExternal /></button>
      <button ref={close} className="icon-btn" onClick={onClose} title={t('common.close')} aria-label={t('common.close')}><IconClose /></button>
    </header>
    {error && <p className="thread-peek-error" role="alert">{error}</p>}
    {thread.waiting && waiting.length === 0 && <button className="thread-peek-attention ghost" onClick={onOpen}>{t('threadPeek.needsAction')}</button>}
    <div className="thread-peek-body" ref={scroll} onScroll={() => { const el = scroll.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120; }}>
      {!loaded ? <div className="loading-note">{t('common.loading')}</div> : <ThreadView historyApi={api} historyThreadId={thread.id}
        items={timeline.items} roots={[thread.cwd, thread.cwdReal]} loadImage={loadImage} loadFile={loadFile}
        observeOlder={false} hasOlder={cursor !== null} loadingOlder={loadingOlder} onLoadOlder={() => void loadOlder()} />}
    </div>
    <Composer attachBlocked={null} restore={null} note={null} draftKey={draftKey} running={timeline.running || thread.status === 'running'}
      placeholder={timeline.running ? t('sideChat.queuePlaceholder') : t('app.placeholder.continue')}
      onSend={async (text, images, _mentions, videos = []) => {
        await api.startTurn(thread.id, text, images.map(({ data, mimeType }) => ({ data, mimeType })),
          { queue: true, videos: await Promise.all(videos.map(async video => (await api.uploadVideo(thread.id, video.file)).id)) });
        await refresh();
      }}
      onStop={() => void api.cancel(thread.id).catch(reportError)} onError={setError}
      header={null} left={null} right={null} commands={[]} actions={[]} hiddenCommands={[]} onAction={() => undefined}
      queued={thread.queued} onRemoveQueued={id => void api.removeQueued(thread.id, id).then(refresh).catch(reportError)}
      onEditQueued={async id => { const taken = await api.removeQueued(thread.id, id); return { ...taken, videos: await restoreVideos(api, thread.id, taken.videos) }; }}
      onSendQueuedNow={id => void api.sendQueuedNow(thread.id, id).then(refresh).catch(reportError)} />
    {waiting[0] && <ActionSheet key={waiting[0].item.key} target={waiting[0]} index={1} total={waiting.length}
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
  </aside>;
}
