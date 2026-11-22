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
import { AgentPanelFrame } from './AgentPanelFrame';

/** A conversation reader: overlay for the board, shared team panel beside the assistant. */
export function ThreadPeek({ api, initial, draftKey, returnFocus, onClose, onOpen, onChange, side = false }: {
  api: Api; initial: Thread | string; side?: boolean; draftKey: string; returnFocus: HTMLElement | null;
  onClose: () => void; onOpen: () => void; onChange: (thread: Thread) => void;
}) {
  const id = typeof initial === 'string' ? initial : initial.id;
  const [thread, setThread] = useState<Thread | null>(typeof initial === 'string' ? null : initial);
  const [retry, setRetry] = useState(0);
  const panel = useRef<HTMLElement>(null);
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
    const next = await api.thread(id);
    setThread(next); callbacks.current.onChange(next);
  }, [api, id]);
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
    close.current?.focus({ preventScroll: true });
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
    setError('');
    void api.thread(id).then(value => {
      if (ctrl.signal.aborted) return;
      setThread(value); callbacks.current.onChange(value);
      return watchHistory(api, id, ctrl.signal, {
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
    }).catch(error => { if (!ctrl.signal.aborted) reportError(error); });
    return () => { ctrl.abort(); clearTimeout(timer); };
  }, [api, id, refresh, retry]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (stick.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [events]);
  const loadImage = useCallback((attachmentId: string) => api.attachment(id, attachmentId), [api, id]);
  const loadFile = useCallback((path: string) => api.threadFile(id, path), [api, id]);
  const loadOlder = async () => {
    if (cursor === null || loadingOlder) return;
    setLoadingOlder(true);
    const element = scroll.current;
    const height = element?.scrollHeight ?? 0;
    const top = element?.scrollTop ?? 0;
    try {
      const page = await historyPage(api, id, cursor);
      stick.current = false;
      setEvents(previous => mergeHistoryPage(previous, page));
      setCursor(page.hasOlder ? page.nextCursor : null);
      requestAnimationFrame(() => { if (element) element.scrollTop = top + element.scrollHeight - height; });
    } catch (error) { reportError(error); }
    finally { setLoadingOlder(false); }
  };
  const title = thread?.title || t('view.openThread');
  const content = <>
    <header className={side ? "agent-panel-head" : "thread-peek-head"}>
      <div className="agent-panel-heading"><strong title={title}>{title}</strong></div>
      {(timeline.running || thread?.status === 'running' || thread?.status === 'error') && <span className="thread-peek-status" role="status">{t(thread?.status === 'error' ? 'status.error' : 'status.running')}</span>}
      <button disabled={!thread} className="icon-btn" onClick={onOpen} title={t('threadPeek.open')} aria-label={t('threadPeek.open')}><IconExternal /></button>
      <button ref={close} className="icon-btn" onClick={onClose} title={t(side ? 'threadPeek.back' : 'common.close')} aria-label={t(side ? 'threadPeek.back' : 'common.close')}><IconClose /></button>
    </header>
    {error && <p className="thread-peek-error" role="alert">{error} <button className="ghost" onClick={() => setRetry(value => value + 1)}>{t('common.retry')}</button></p>}
    {thread?.waiting && waiting.length === 0 && <button className="thread-peek-attention ghost" onClick={onOpen}>{t('threadPeek.needsAction')}</button>}
    <div className={side ? "agent-panel-body" : "thread-peek-body"} ref={scroll} tabIndex={0} onScroll={() => { const el = scroll.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120; }}>
      {!loaded || !thread ? (!error && <div className="loading-note">{t('common.loading')}</div>) : <ThreadView historyApi={api} historyThreadId={thread.id}
        items={timeline.items} roots={[thread.cwd, thread.cwdReal]} loadImage={loadImage} loadFile={loadFile}
        observeOlder={false} hasOlder={cursor !== null} loadingOlder={loadingOlder} onLoadOlder={() => void loadOlder()} />}
    </div>
    {thread && <Composer attachBlocked={null} restore={null} note={null} draftKey={draftKey} running={timeline.running || thread?.status === 'running'}
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
    : <aside className="thread-peek" role="complementary" aria-label={title}>{content}</aside>;
}
