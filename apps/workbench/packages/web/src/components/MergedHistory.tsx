import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentProps, type ReactNode } from 'react';
import { ApiError, type Api, type Thread } from '../api';
import { historyPage, prependHistory, type HistoryPage } from '../history';
import { buildTimeline } from '../timeline';
import { t } from '../i18n';
import { IconChevron, IconHandover } from './icons';
import { Prose, ThreadView } from './ThreadView';

type ViewProps = ComponentProps<typeof ThreadView>;
type Segment = { thread: Thread; page: HistoryPage };
type Props = ViewProps & {
  api: Api; thread: Thread; merged: boolean; ready: boolean;
  agentLabel: (id: string) => string;
  onBrowseOlder: () => void;
  onSegmentFork: (thread: Thread, turn: string) => void;
  canFork: (thread: Thread) => { enabled: boolean; any: boolean };
  onSegmentFile: (thread: Thread, path: string) => Promise<void>;
  onSegmentPreview: (thread: Thread, path: string) => void;
  /** What follows a segment: the next of an automatic chain, or a conversation handed over to by hand; none at the end. Reading goes on into it. */
  laterOf?: (thread: Thread) => Thread | undefined;
};

/** Every relay, automatic or by hand, leaves a fold at its start with what the successor was given. */
const handedOver = (thread: Thread): boolean => Boolean(thread.handover && !thread.handover.detached);

/** Each earlier segment owns its client, cursor and action targets. No ancestor reads on mount. */
export function MergedHistory({ api, thread, merged, ready, agentLabel, laterOf, onBrowseOlder, onSegmentFork, canFork, onSegmentFile, onSegmentPreview, ...current }: Props) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<'missing' | 'failed' | null>(null);
  const [later, setLater] = useState<Segment[]>([]);
  const [loadingLater, setLoadingLater] = useState(false);
  const [laterFailed, setLaterFailed] = useState(false);
  const pendingLater = useRef(false);
  const laterGeneration = useRef(0);
  const sentinel = useRef<HTMLDivElement>(null);
  const pending = useRef(false);
  const generation = useRef(0);
  const root = useRef<HTMLDivElement>(null);
  const restore = useRef<number | null>(null);
  useEffect(() => {
    generation.current++; pending.current = false;
    setSegments([]); setLoading(false); setError(null);
  }, [api, thread.id, merged]);
  useLayoutEffect(() => {
    const scroller = root.current?.closest<HTMLElement>('.scroller');
    if (scroller && restore.current !== null) scroller.scrollTop = scroller.scrollHeight - restore.current;
    restore.current = null;
  }, [segments]);
  const load = async () => {
    if (pending.current || !ready) return;
    if (!segments.length && current.hasOlder) { onBrowseOlder(); current.onLoadOlder?.(); return; }
    const oldest = segments[0];
    const target = oldest?.thread ?? thread;
    const same = Boolean(oldest?.page.hasOlder);
    const id = same ? target.id : (target.handover?.mode === 'continue' ? target.handover.sourceId : undefined);
    if (!id) return;
    // Broken/cyclic metadata must not repeatedly insert the same conversation.
    if (!same && [thread.id, ...segments.map(s => s.thread.id)].includes(id)) { setError('missing'); return; }
    pending.current = true; setLoading(true); setError(null); onBrowseOlder();
    const version = generation.current;
    try {
      const [source, page] = await Promise.all([
        same ? Promise.resolve(target) : api.thread(id),
        historyPage(api, id, same ? oldest.page.nextCursor ?? undefined : undefined, undefined, true),
      ]);
      if (generation.current !== version) return;
      const scroller = root.current?.closest<HTMLElement>('.scroller');
      if (scroller) restore.current = scroller.scrollHeight - scroller.scrollTop;
      setSegments(previous => same
        ? [{ thread: source, page: { ...page, events: prependHistory(page.events, previous[0].page.events) } }, ...previous.slice(1)]
        : [{ thread: source, page }, ...previous]);
    } catch (e) {
      if (generation.current === version) setError(e instanceof ApiError && e.status === 404 ? 'missing' : 'failed');
    } finally {
      if (generation.current === version) { pending.current = false; setLoading(false); }
    }
  };
  // Reading on from a segment: the next one is read from its beginning, a page at a time, as the end
  // of this one comes into view, so the chain scrolls like one conversation. What follows is known
  // from the list of threads, so when it changes (the source went on after a handover by hand) what
  // was loaded below is dropped.
  const first = laterOf?.(thread);
  const firstId = first?.id ?? null;
  useEffect(() => {
    laterGeneration.current++; pendingLater.current = false;
    setLater([]); setLoadingLater(false); setLaterFailed(false);
  }, [api, thread.id, firstId]);
  const tail = later.at(-1);
  const shown = new Set([thread.id, ...segments.map(segment => segment.thread.id), ...later.map(segment => segment.thread.id)]);
  const candidate = tail ? laterOf?.(tail.thread) : first;
  // A broken or cyclic relation must not insert the same conversation twice.
  const nextThread = candidate && !shown.has(candidate.id) ? candidate : undefined;
  const hasLater = Boolean(tail?.page.hasNewer || nextThread);
  const loadLater = async () => {
    if (pendingLater.current || !ready) return;
    const same = Boolean(tail?.page.hasNewer);
    const target = same ? tail!.thread : nextThread;
    if (!target) return;
    pendingLater.current = true; setLoadingLater(true); setLaterFailed(false); onBrowseOlder();
    const version = laterGeneration.current;
    try {
      const page = await api.historyPage(target.id, undefined, same ? tail!.page.nextCursor ?? undefined : undefined, true, undefined, true);
      if (laterGeneration.current !== version) return;
      setLater(previous => same
        ? [...previous.slice(0, -1), { thread: target, page: { ...page, events: prependHistory(tail!.page.events, page.events) } }]
        : [...previous, { thread: target, page }]);
    } catch { if (laterGeneration.current === version) setLaterFailed(true); }
    finally { if (laterGeneration.current === version) { pendingLater.current = false; setLoadingLater(false); } }
  };
  const loader = useRef(loadLater);
  loader.current = loadLater;
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasLater || !ready || loadingLater || laterFailed) return;
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) void loader.current(); },
      { root: root.current?.closest<HTMLElement>('.scroller') ?? null, rootMargin: '0px 0px 600px 0px' });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasLater, ready, loadingLater, laterFailed, later]);
  const laterBlock = <>
    {later.map(segment => <EarlierSegment key={segment.thread.id} segment={segment} api={api} agentLabel={agentLabel}
      onQuote={current.onQuote} onMessageContext={current.onMessageContext} onHandToAssistant={current.onHandToAssistant} fork={canFork(segment.thread)} onFork={turn => onSegmentFork(segment.thread, turn)}
      onOpenFile={path => onSegmentFile(segment.thread, path)} onPreview={path => onSegmentPreview(segment.thread, path)}
      older={{ observeOlder: false, chain: true }} prefix={null} />)}
    {laterFailed
      ? <div className="history-status" role="alert">{t('handover.chain.laterFailed')} <button className="text-btn" onClick={() => setLaterFailed(false)}>{t('common.retry')}</button></div>
      : hasLater
        ? <div ref={sentinel} className="chain-later" role={loadingLater ? 'status' : undefined}>{loadingLater ? t('common.loading') : null}</div>
        : null}
  </>;
  if (!merged) return later.length || hasLater ? <div className="merged-history" ref={root}><ThreadView {...current} />{laterBlock}</div> : <ThreadView {...current} />;
  const localOlder = segments.length ? segments[0].page.hasOlder : current.hasOlder;
  const oldest = segments[0]?.thread ?? thread;
  const more = Boolean(localOlder || oldest.handover?.mode === 'continue');
  const olderProps = { hasOlder: ready && more, loadingOlder: loading || Boolean(current.loadingOlder), onLoadOlder: load,
    olderLabel: localOlder ? undefined : t('handover.chain.loadPrevious'),
    chain: true, observeOlder: true, olderError: error ? t(error === 'missing' ? 'handover.chain.missing' : 'handover.chain.failed') : undefined };
  const beginning = ready && !more ? <div className="row chain-beginning" role="status">{t('handover.chain.beginning')}</div> : null;
  return <div className="merged-history" ref={root}>
    {segments.map((segment, i) => <EarlierSegment key={segment.thread.id} segment={segment} api={api} agentLabel={agentLabel}
      onQuote={current.onQuote} onMessageContext={current.onMessageContext} onHandToAssistant={current.onHandToAssistant} fork={canFork(segment.thread)} onFork={turn => onSegmentFork(segment.thread, turn)}
      onOpenFile={path => onSegmentFile(segment.thread, path)} onPreview={path => onSegmentPreview(segment.thread, path)}
      older={i === 0 ? olderProps : { observeOlder: false, chain: true }} prefix={i === 0 ? beginning : null} />)}
    <div data-history-thread={thread.id} className="history-segment current-segment">
      <ThreadView {...current} {...(segments.length ? { hasOlder: false, observeOlder: false, chain: true } : olderProps)}
        beforeItems={<>{!segments.length && beginning}{ready && !current.hasOlder && handedOver(thread) && <HandoverBoundary api={api} thread={thread} agentLabel={agentLabel} turnId={current.items.find(item => item.kind === 'user' && item.handoverCollapsed)?.turnId ?? undefined} />}</>}
      />
    </div>
    {laterBlock}
  </div>;
}

function EarlierSegment({ segment, api, agentLabel, fork, onFork, onQuote, onMessageContext, onHandToAssistant, onOpenFile, onPreview, older, prefix }: {
  segment: Segment; api: Api; agentLabel: Props['agentLabel']; fork: ReturnType<Props['canFork']>;
  onFork: (id: string) => void; onOpenFile: (path: string) => Promise<void>; onPreview: (path: string) => void;
  older: Partial<ViewProps>; prefix: ReactNode; onQuote?: ViewProps['onQuote']; onMessageContext?: ViewProps['onMessageContext']; onHandToAssistant?: ViewProps['onHandToAssistant'];
}) {
  const { thread, page } = segment;
  const timeline = useMemo(() => buildTimeline(page.events), [page.events]);
  const roots = useMemo(() => [thread.cwd, thread.cwdReal], [thread.cwd, thread.cwdReal]);
  const loadImage = useMemo(() => (id: string) => api.attachment(thread.id, id), [api, thread.id]);
  const loadFile = useMemo(() => (path: string) => api.threadFile(thread.id, path), [api, thread.id]);
  return <div data-history-thread={thread.id} className="history-segment">
    <ThreadView onQuote={onQuote} onMessageContext={onMessageContext} onHandToAssistant={onHandToAssistant} historyApi={api} historyThreadId={thread.id} items={timeline.items} roots={roots} loadImage={loadImage} loadFile={loadFile}
      onOpenFile={onOpenFile} onPreview={onPreview} onFork={fork.enabled ? onFork : undefined} forkAnyTurn={fork.any} {...older}
      beforeItems={<>{prefix}{!page.hasOlder && handedOver(thread) && <HandoverBoundary api={api} thread={thread} agentLabel={agentLabel} turnId={page.events.find(event => event.type === 'turn.started')?.turnId ?? undefined} />}
        {!timeline.items.length && <div className="row chain-beginning">{t('handover.chain.empty')}</div>}</>} />
  </div>;
}

function HandoverBoundary({ api, thread, agentLabel, turnId }: { api: Api; thread: Thread; agentLabel: Props['agentLabel']; turnId?: string }) {
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<Awaited<ReturnType<Api['historyHandover']>> | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true; setLoading(true); setFailed(false);
    void api.historyHandover(thread.id, open).then(value => { if (alive) setInfo(value); })
      .catch(() => { if (alive) setFailed(true); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [api, thread.id, turnId, open, attempt]);
  // By hand it is a conversation of its own that was started from the source; the reason an automatic relay happened does not apply.
  const manual = !thread.handover!.automatic;
  const reason = manual ? '' : info?.reason === 'plan_exhausted' ? t('handover.chain.exhausted') : info?.reason === 'interrupted' ? t('handover.chain.interrupted') : info?.reason ? t('handover.chain.error') : '';
  const kind = !info?.size ? '' : info.method === 'resume' ? t('handover.card.resume') : t(info.method === 'summary' ? 'handover.card.summary' : 'handover.card.text', info.size);
  // Like the plan in the conversation: one quiet line that folds out into the whole text, hung under a hairline.
  return <div className="row handover-boundary" data-handover-thread={thread.id} data-turn={turnId}>
    <div className="fork-mark">
      <span className="fork-mark-label"><IconHandover size={15} />{manual ? t('handover.trail.manual', agentLabel(thread.handover!.agent), agentLabel(thread.agent)) : t('handover.trail.from', agentLabel(thread.handover!.agent))}{reason ? ` · ${reason}` : ''}</span>
    </div>
    {failed && <div className="history-status" role="alert">{t('handover.chain.detailFailed')} <button className="text-btn" onClick={() => setAttempt(n => n + 1)}>{t('common.retry')}</button></div>}
    {!failed && !info && <div className="handover-card-head placeholder" aria-hidden="true" />}
    {!failed && info && !info.size && <div className="handover-card-empty">{t('handover.chain.noSummary')}</div>}
    {!failed && info && info.size > 0 && <div className={`handover-card${open ? ' open' : ''}`}>
      <button type="button" className="handover-card-head" aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <span className="handover-card-kind">{kind}</span>
        {!open && info.preview && <span className="handover-card-preview">{info.preview}</span>}
        <IconChevron size={12} open={open} />
      </button>
      {open && <div className="handover-card-body" role="region" aria-label={t('handover.chain.summary')}>
        {loading || info.text === undefined ? <span role="status">{t('common.loading')}</span> : <Prose text={info.text} />}
      </div>}
    </div>}
  </div>;
}
