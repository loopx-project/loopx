import { channelRows, hydrateHistoryFiles } from '../timeline';
import { ApiError, type Api } from '../api';
import { hydrateWork, HistoryClient, type HistoryDetail, type HistoryWork, type ResourceKind, type WorkFiles } from '../history';
import { Children, createContext, Fragment, isValidElement, memo, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { enlargeMermaidDiagram, renderMermaidDiagram } from '../mermaid';
import { copyText } from '../clipboard';
import { diffLines, numberLines } from '../diff';
import { pictureUrl, svgPicture } from '../images';
import { PlanList, PlanRing, planProgress } from './PlanPanel';
import { IMAGE_FILE, linkTarget, nameAttachments, relativePath, shortenPaths } from '../paths';
import {
  buildTimeline,
  diffsOf,
  type DetailRef,
  clockLabel,
  compactionLabel,
  deliveredPreview,
  durationLabel,
  groupByTurn,
  imagePathsIn,
  liveWorkFromEvents,
  runSummary,
  splitThinking,
  splitNotes,
  splitUserQuote,
  thinkingPreview,
  stepActive,
  stepCommand,
  stepTitle,
  stepStatusLabel,
  workSections,
  type ApprovalOption,
  type CoordinationNote,
  type FileChange,
  type FileDiff,
  type ImageRef,
  type Item,
  type Note,
  type Step,
  type Thought,
  type WorkSection,
} from '../timeline';
import { Lightbox } from './Lightbox';
import { PlanCopyButton, PlanDownloadButton, PlanReader } from './PlanReader';
import { IconBell, ActivityIcon, IconAgents, IconCheck, IconChevron, IconClose, IconCompact, IconCopy, IconEdit, IconFile, IconFork, IconHand, IconHandover, IconList, IconQuote, IconTray, IconWorktree } from './icons';
import { t } from '../i18n';

// The daemon's confirmation marker is replaced by the localized notice label.
const PERSONAL_CONFIRMATION_TEXT = '待你确认';

interface Props {
  /** Where a matter waiting for confirmation is looked at, when not the plans page: her desk's new finds. */
  onOpenFinds?: () => void;
  beforeItems?: ReactNode;
  chain?: boolean;
  observeOlder?: boolean;
  olderLabel?: string;
  olderError?: string;
  historyApi?: Api;
  historyThreadId?: string;
  items: Item[];
  /** Every spelling of the thread's directory (as stored, and resolved), so paths can be shown relative to it. */
  roots: string[];
  /** Fetches an attached image. Must keep the same identity while the thread stays the same. */
  loadImage: (id: string) => Promise<Blob>;
  /** Fetches an image file from the thread's directory by path; rejects when there is no such image. Same identity rule. */
  loadFile: (path: string) => Promise<Blob>;
  /** Opens a file an answer links to in the file viewer. Rejects when the thread cannot show it. */
  onOpenFile?: (path: string) => Promise<void>;
  /** Renders a page the agent wrote, from the thread's directory. HTML and SVG files. */
  onPreview?: (path: string) => void;
  /** The thread's log continues below what is in memory: one more history page can be fetched. */
  hasOlder?: boolean;
  revealTurnId?: string;
  loadingOlder?: boolean;
  onLoadOlder?: () => void;
  /** Copies the thread as it stands and opens the copy. Missing when the agent cannot fork, or there is no session yet. */
  onFork?: (turnId: string) => void;
  /**
   * When the last turn failed because the agent's plan ran out: hand the thread to another agent.
   * `label` names the agent one click goes to; `choose` opens the handover to pick another.
   */
  takeover?: { label: string; go: () => void; choose: () => void; busy?: boolean } | null;
  /**
   * When the last turn failed because the agent's plan ran out: wait for the quota to reset and
   * continue with the same agent. `armed` mirrors the daemon's watch; `onArm`/`onCancel` toggle it.
   */
  resume?: { armed: boolean; resetsAt?: string | null; onArm: () => void; onCancel: () => void; busy?: boolean; error?: string } | null;
  /** The agent can end a copy at any finished turn, so every answer gets the button, not only the last. */
  forkAnyTurn?: boolean;
  /**
   * Edits one of the user's own earlier messages and sends it again from there. `revision` is the
   * history the page read; the daemon refuses when it has moved since. Missing where the agent
   * cannot copy a conversation up to just before a message.
   */
  onEdit?: (turnId: string, edit: { text: string; images: string[]; revision: number; requestId: string }) => Promise<void>;
  /** The history this view was built from; an edit is sent against it. */
  revision?: number;
}

/** Lines of one diff shown before the rest is folded away. */
const MAX_DIFF_LINES = 400;

/** How many turns stay rendered before the rest folds into "N earlier messages". */
const RECENT_TURNS = 30;
/** Scrolling up to within this distance of the top unfolds or fetches the earlier turns. */
const NEAR_TOP_PX = 600;

export function ThreadView({ onOpenFinds, chain = false, observeOlder = true, beforeItems, olderLabel, olderError, revealTurnId, historyApi, historyThreadId, items: originalItems, roots, loadImage, loadFile, onOpenFile, onPreview, hasOlder, loadingOlder, onLoadOlder, onFork, forkAnyTurn, onEdit, revision = 0, takeover, resume }: Props) {
  const [loadedFiles, setLoadedFiles] = useState<{ client?: HistoryClient; files: Map<string, WorkFiles> }>({ files: new Map() });
  const historyClient = useMemo(() => {
    if (!historyApi || !historyThreadId) return undefined;
    const client = new HistoryClient(historyApi, historyThreadId, (ref, work) => {
      if (work.files) setLoadedFiles(previous => ({ client, files: new Map(previous.client === client ? previous.files : []).set(`${ref.turnId}:e${ref.id}`, work.files!) }));
    }, chain);
    return client;
  }, [historyApi, historyThreadId, chain]);
  const items = useMemo(() => loadedFiles.client === historyClient ? hydrateHistoryFiles(originalItems, loadedFiles.files) : originalItems, [originalItems, loadedFiles, historyClient]);
  const [lightbox, setLightbox] = useState<{ url: string; name?: string } | null>(null);
  const show = useCallback((url: string, name?: string) => setLightbox({ url, name }), []);
  // A picture opened from a link in an answer is fetched for the occasion, and let go of when it is closed.
  const fetched = useRef<string | null>(null);
  const release = () => {
    if (fetched.current) URL.revokeObjectURL(fetched.current);
    fetched.current = null;
  };
  const closeLightbox = useCallback(() => {
    setLightbox(null);
    release();
  }, []);
  const openPicture = useCallback(
    async (path: string) => {
      const url = await pictureUrl(await loadFile(path));
      release();
      fetched.current = url;
      setLightbox({ url, name: path });
    },
    [loadFile],
  );
  useEffect(() => release, []);
  const pictures = useMemo(() => ({ openFile: openPicture, show, openText: onOpenFile }), [openPicture, show, onOpenFile]);
  const groups = useMemo(() => groupByTurn(items), [items]);
  // "Show everything" is remembered per thread; switching threads starts folded again.
  const threadKey = items[0]?.key ?? '';
  const [expandedFor, setExpandedFor] = useState<{ key: string; value: boolean }>({ key: '', value: false });
  const expanded = expandedFor.key === threadKey && expandedFor.value;
  useLayoutEffect(() => {
    if (revealTurnId) setExpandedFor({ key: threadKey, value: true });
  }, [revealTurnId, threadKey]);
  const folded = !expanded && !revealTurnId && groups.length > RECENT_TURNS;
  const shown = folded ? groups.slice(groups.length - RECENT_TURNS) : groups;
  const hiddenCount = folded ? groups.length - shown.length : 0;
  const hiddenMessages = folded ? groups.slice(0, -RECENT_TURNS).reduce((n, g) => n + g.items.length, 0) : 0;

  // Scrolling up near the top brings in what is above, like Codex: first the folded turns, then
  // the pages the daemon has not sent yet. Only upward movement counts, so opening a thread (which
  // starts at the top and is pinned down) and the scroll restore after a load trigger nothing.
  const threadRef = useRef<HTMLDivElement | null>(null);
  const restoreFromBottom = useRef<number | null>(null);
  const expand = () => {
    const scroller = threadRef.current?.closest<HTMLElement>('.scroller');
    if (scroller) restoreFromBottom.current = scroller.scrollHeight - scroller.scrollTop;
    setExpandedFor({ key: threadKey, value: true });
  };
  const reachTop = useRef(() => {});
  reachTop.current = () => {
    if (!observeOlder || olderError) return;
    if (folded) expand();
    else if (hasOlder && !loadingOlder) onLoadOlder?.();
  };
  // Unfolded turns land above the viewport; keep the same content in view.
  useLayoutEffect(() => {
    const scroller = threadRef.current?.closest<HTMLElement>('.scroller');
    if (scroller && restoreFromBottom.current !== null) scroller.scrollTop = scroller.scrollHeight - restoreFromBottom.current;
    restoreFromBottom.current = null;
  }, [expanded]);
  useEffect(() => {
    const scroller = threadRef.current?.closest<HTMLElement>('.scroller');
    if (!scroller) return;
    let lastTop = scroller.scrollTop;
    let touchY = 0;
    let upward = false;
    // A prepend or collapsing a long handover can move the scrollbar too. Only user navigation
    // may cross another segment; consume each gesture before inserting the next page.
    const reach = () => {
      if (chain && !upward) return;
      upward = false;
      reachTop.current();
    };
    const onScroll = () => {
      const top = scroller.scrollTop;
      if (top < lastTop && top < NEAR_TOP_PX) reach();
      lastTop = top;
    };
    const nestedScrollConsumesUp = (target: EventTarget | null) => {
      if (!chain) return false;
      for (let el = target instanceof Element ? target : null; el && el !== scroller; el = el.parentElement) {
        if (el.scrollTop > 0 && el.scrollHeight > el.clientHeight) return true;
      }
      return false;
    };
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY >= 0 || nestedScrollConsumesUp(e.target)) return;
      upward = true;
      if (scroller.scrollTop < NEAR_TOP_PX) reach();
    };
    const onTouchStart = (e: TouchEvent) => void (touchY = e.touches[0]?.clientY ?? 0);
    const onTouchMove = (e: TouchEvent) => {
      if ((e.touches[0]?.clientY ?? 0) <= touchY + 10 || nestedScrollConsumesUp(e.target)) return;
      upward = true;
      if (scroller.scrollTop < NEAR_TOP_PX) reach();
    };
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (['ArrowUp', 'PageUp', 'Home'].includes(e.key)) {
        upward = true;
        if (scroller.scrollTop < NEAR_TOP_PX) reach();
      }
    };
    const onPointerDown = (e: PointerEvent) => { if (e.target === scroller) upward = true; };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    scroller.addEventListener('wheel', onWheel, { passive: true });
    scroller.addEventListener('touchstart', onTouchStart, { passive: true });
    scroller.addEventListener('touchmove', onTouchMove, { passive: true });
    scroller.addEventListener('keydown', onKeyDown);
    scroller.addEventListener('pointerdown', onPointerDown);
    return () => {
      scroller.removeEventListener('scroll', onScroll);
      scroller.removeEventListener('wheel', onWheel);
      scroller.removeEventListener('touchstart', onTouchStart);
      scroller.removeEventListener('touchmove', onTouchMove);
      scroller.removeEventListener('keydown', onKeyDown);
      scroller.removeEventListener('pointerdown', onPointerDown);
    };
  }, [chain]);
  // A fork from the last answer copies the whole thread; it is offered while that answer is the
  // last thing in it. Where the agent can end a copy at an earlier turn, every answer offers one.
  // The mark a fork starts with is not part of the conversation: the answer above it is still the last.
  const tail = [...items].reverse().find((it) => it.kind !== 'fork');
  const lastKey = tail?.kind === 'actions' ? tail.key : null;

  const renderItems = (list: Item[]) =>
    list.map((item) => {
      switch (item.kind) {
        case 'handover-summary': return <HandoverSummary key={item.key} item={item} />;
        case 'notice': {
          const title = item.confirmation ? item.text.replace(new RegExp(`^${PERSONAL_CONFIRMATION_TEXT}[：:]?`), '') : item.text;
          // In her own conversation a matter to confirm is looked at on the desk, among the new finds.
          const go = onOpenFinds ? <button type="button" className="agent-notice-link" onClick={onOpenFinds}>{t('personal.openFinds')}</button> : <a href="#/todos">{t('personal.openPlans')}</a>;
          return <div key={item.key} className="agent-notice"><IconBell size={14} /><span>{item.confirmation ? (title ? t('personal.confirmTitle', title) : t('personal.confirm')) : <>{t('personal.notice')} {title}</>}{item.confirmation && <> · {go}</>}</span></div>;
        }
        case 'model-switch': return <div key={item.key} className="context-compaction handover-divider">{t('auto.switched', item.model, item.previous, t(item.reason === 'exhausted' ? 'auto.exhausted' : 'auto.near'))}</div>;
        case 'quota-resume': return <div key={item.key} className="context-compaction handover-divider">{t('auto.resumed')}</div>;
        case 'user': {
          if (item.handoverCollapsed) return null;
          // Nobody wrote this turn: the agent picked up work on its own, e.g. a finished background task.
          if (item.background) return (
            <div key={item.key} className="row turn-note" data-turn={item.turnId}>
              <span className="work-toggle"><span className="work-label">{t('view.backgroundTurn')}</span></span>
            </div>
          );
          // Nobody said this either: a channel handed over what it read. A quiet line, not a bubble.
          if (item.channel) return channelRows(item.channel, item.text).map((row, index) => row.kind === 'person'
            ? <div key={`${item.key}:${index}`} className="row user" data-turn={item.turnId}><div className="bubble">{row.text}</div></div>
            : <div key={`${item.key}:${index}`} className="row channel-note" data-turn={item.turnId}><IconTray size={13} /><span>{row.text}</span></div>);
          if (item.coordination) return <DeliveredNote key={item.key} turnId={item.turnId} note={item.coordination} text={item.text} />;
          // Notes (and the older single quote) ride in the message text; drawn apart here.
          const noted = splitNotes(item.text);
          const { quote, body } = noted.notes.length ? { quote: null, body: noted.body } : splitUserQuote(item.text);
          return (
            <div key={item.key} className={`row user${item.voice ? ' voice' : ''}${item.streaming ? ' draft' : ''}`} data-turn={item.turnId}>
              {item.images.length > 0 && (
                <div className="sent-images">
                  {item.images.map((img) => (
                    <SentImage key={img.id} image={img} load={loadImage} onOpen={show} />
                  ))}
                </div>
              )}
              {noted.notes.length > 0 && <SentNotes notes={noted.notes} />}
              {quote !== null && (
                <div className="bubble-quote" title={quote}>
                  {quote}
                </div>
              )}
              {body && (
                <div className="bubble">
                  {item.fromTerminal && <span className="term-chip" title={t('view.fromTerminal')}>{t('term.title')}</span>}
                  {item.scheduled && <span className="term-chip" title={t('view.fromSchedule')}>{t('view.scheduled')}</span>}
                  {item.workMode === 'plan' && <span className="term-chip">{t('view.plan')}</span>}
                  {body}
                </div>
              )}
              {!body && (item.fromTerminal || item.scheduled) && (
                <div className="bubble">
                  {item.fromTerminal && <span className="term-chip" title={t('view.fromTerminal')}>{t('term.title')}</span>}
                  {item.scheduled && <span className="term-chip" title={t('view.fromSchedule')}>{t('view.scheduled')}</span>}
                </div>
              )}
              {onEdit && (body || item.images.length > 0) && !item.voice && !item.fromTerminal && !item.scheduled && !item.coordination && (
                <EditMessage
                  text={item.text}
                  imageIds={item.images.map((img) => img.id)}
                  revision={revision}
                  onSend={(edit) => onEdit(item.turnId, edit)}
                />
              )}
            </div>
          );
        }
        case 'input':
          // A native plan approval shows its plan as the card; the decision itself is asked in the
          // sheet, and answered or not, the card stays where it stood.
          if (item.inputKind === 'plan' && item.plan) {
            return (
              <Fragment key={item.key}>
                <ProposalCard text={item.plan} />
                <TraceLine mark={planTrace(item)} />
              </Fragment>
            );
          }
          // An ordinary question leaves just a trace once it has been answered; while it waits,
          // the sheet at the bottom is where it is asked.
          return <TraceLine key={item.key} mark={questionTrace(item)} />;
        case 'proposal':
          return <ProposalCard key={item.key} text={item.text} streaming={!item.done} />;
        case 'assistant':
          if (!item.text) return null;
          return <AssistantMessage key={item.key} text={item.text} done={item.done} roots={roots} loadFile={loadFile} onOpen={show} />;
        case 'plan':
          return <PlanCard key={item.key} item={item} />;
        case 'work':
          return <WorkGroup key={item.key} item={item} roots={roots} />;
        case 'waiting':
          return <TurnWaiting key={item.key} item={item} />;
        case 'approval':
          return <TraceLine key={item.key} mark={approvalTrace(item, roots)} />;
        case 'changes':
          return <ChangesCard key={item.key} item={item} roots={roots} onPreview={onPreview} />;
        case 'stopped': {
          const time = item.timed ? durationLabel(item.startedAt, item.endedAt) : '';
          return (
            <div key={item.key} className="row work">
              <div className="work-toggle stopped">{stoppedLabel(time)}</div>
            </div>
          );
        }
        case 'actions':
          return (
            <div key={item.key} className="row message-actions">
              <CopyButton text={item.text} label={t('view.copyAnswer')} />
              {onFork && (forkAnyTurn || item.key === lastKey) && (
                <button
                  type="button"
                  className="quiet-btn"
                  aria-label={t('sidebar.fork')}
                  title={item.key === lastKey ? t('view.forkLast') : t('view.forkHere')}
                  onClick={() => onFork(item.turnId)}
                >
                  <IconFork size={15} />
                </button>
              )}
              {item.at && (
                <time className="message-time" dateTime={item.at} title={new Date(item.at).toLocaleString('zh-CN')}>
                  {clockLabel(item.at)}
                </time>
              )}
            </div>
          );
        case 'error':
          return (
            <div key={item.key} className="row">
              <div className="error-box">
                {item.message}
                {item.planExhausted && takeover && item === items[items.length - 1] && (
                  <div className="error-actions">
                    <button className="primary" onClick={takeover.go} disabled={takeover.busy}>
                      <IconHand size={15} /> {takeover.busy ? t('view.handingOver') : t('view.handTo', takeover.label)}
                    </button>
                    <button className="ghost" onClick={takeover.choose} disabled={takeover.busy}>{t('view.chooseOther')}</button>
                  </div>
                )}
                {/* The alternative to handing over: stay on this agent and let the daemon continue
                    the thread once the quota resets. A quiet text link, not a third button. */}
                {item.planExhausted && resume && item === items[items.length - 1] && (
                  <div className="error-resume">
                    {resume.armed ? <>
                      <span role="status">{resume.resetsAt ? t('view.resumeArmedAt', resetClock(resume.resetsAt)) : t('view.resumeArmedAny')}
                        {resume.error && <small>{resume.error}</small>}</span>
                      <button className="text-btn" onClick={resume.onCancel} disabled={resume.busy}>{t('view.resumeCancel')}</button>
                    </> : (
                      <button className="text-btn" onClick={resume.onArm} disabled={resume.busy}>{t('view.resumeArm')}</button>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        case 'fork':
          return (
            <div key={item.key} className="row fork-mark" role="separator" aria-label={item.branch ? t('view.forkedInWorktree', item.branch) : t('view.forkedHere')}>
              <span className="fork-mark-label">
                {item.branch ? <IconWorktree size={15} /> : <IconFork size={15} />}
                {t('view.forkedHere')}
                {item.branch && <span className="fork-mark-branch">{item.branch}</span>}
              </span>
            </div>
          );
      }
    });

  return (
    <div className="thread" ref={threadRef}>
      <HistoryContext.Provider key={historyClient?.identity ?? historyThreadId} value={historyClient}>
      <Pictures.Provider value={pictures}>
        {/* Scrolling up loads these on its own; the buttons stay for keyboards and screen readers. */}
        {folded && (
          <div className="row">
            <button className="older-messages" onClick={expand}>
              {t('view.older', hiddenMessages)}
            </button>
          </div>
        )}
        {!folded && hasOlder && (
          <div className="row older-row">
            {olderError && <span className="history-status" role="alert">{olderError}</span>}
            {loadingOlder ? (
              <span className="spinner" role="status" aria-label={t('view.loadingOlder')} />
            ) : (
              <button className="older-messages" onClick={onLoadOlder}>
                {olderError ? t('common.retry') : olderLabel ?? t('view.loadOlder')}
              </button>
            )}
          </div>
        )}
        {beforeItems}
        {shown.map((g) => renderItems(g.items))}
      </Pictures.Provider>
      </HistoryContext.Provider>
      {lightbox && <Lightbox url={lightbox.url} name={lightbox.name} onClose={closeLightbox} />}
    </div>
  );
}

const HistoryContext = createContext<HistoryClient | undefined>(undefined);

/** A quota reset time as a short local note: "17:00" today, "10/12 17:00" later. Empty when unknown. */
function resetClock(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  const hm = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
  return at.toDateString() === now.toDateString() ? hm : `${at.getMonth() + 1}/${at.getDate()} ${hm}`;
}

function HandoverSummary({ item }: { item: Extract<Item, { kind: 'handover-summary' }> }) {
  const client = useContext(HistoryContext);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<Awaited<ReturnType<Api['historySummary']>> | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open || !client) return;
    let alive = true;
    setError('');
    const timer = setTimeout(() => {
      void client.api.historySummary(client.threadId, item.turnId).then(v => { if (alive) setValue(v); }).catch(e => { if (alive) setError(String(e)); });
    }, 100);
    return () => { alive = false; clearTimeout(timer); };
  }, [open, client, item.turnId, item.version, retry]);
  return <div className="handover-summary-turn" data-turn={item.turnId}>
    <button className="context-compaction handover-divider" aria-expanded={open} onClick={() => setOpen(!open)}>
      <IconHandover size={16} /><span>{t(item.done ? 'auto.summaryDone' : 'auto.summaryRunning', item.target)}</span><IconChevron size={14} />
    </button>
    {open && <div className="handover-summary-details">
      {error ? <p role="alert">{error} <button className="text-btn" onClick={() => setRetry(v => v + 1)}>{t('common.retry')}</button></p> : !value ? <p role="status">{t('common.loading')}</p> : <>
        <small>{t('auto.summaryPrompt')}</small><pre>{value.prompt}</pre>
        <small>{t('auto.summaryAnswer')}</small><pre>{value.text || t('auto.summaryEmpty')}</pre>
        {value.nextOffset !== null && <button className="text-btn" onClick={() => {
          void client?.api.historySummary(client.threadId, item.turnId, value.nextOffset!, value.snapshotSeq).then(v => setValue({ ...v, text: value.text + v.text })).catch(e => setError(String(e)));
        }}>{t('view.loadOlder')}</button>}
      </>}
    </div>}
  </div>;
}

/** Refresh open live details at most four times a second; closed details perform no I/O. */
function useHistoryResource<T extends HistoryDetail | HistoryWork>(kind: ResourceKind, ref: DetailRef | undefined, enabled: boolean, path?: string) {
  const client = useContext(HistoryContext);
  const latest = useRef(ref); latest.current = ref;
  const [state, setState] = useState<{ value?: T; loading: boolean; error: boolean }>({ loading: false, error: false });
  const [attempt, setAttempt] = useState(0);
  const continuation = useRef(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    if (!enabled || !ref || !client) return;
    let alive = true; mounted.current = true; generation.current++; continuation.current = false;
    let busy = false;
    let version = -1;
    setState({ loading: true, error: false });
    const update = () => {
      const target = latest.current;
      if (!alive || busy || continuation.current || !target || target.version === version) return;
      busy = true; version = target.version;
      setState(old => ({ ...old, loading: true, error: false }));
      client.load<T>(kind, target, path).then(value => {
        if (alive) setState({ value, loading: false, error: false });
      }).catch(() => {
        if (alive) setState(old => ({ ...old, loading: false, error: true }));
      }).finally(() => { busy = false; });
    };
    update();
    const timer = setInterval(update, 250);
    return () => { alive = false; mounted.current = false; generation.current++; continuation.current = false; clearInterval(timer); };
  }, [client, kind, ref?.turnId, ref?.id, enabled, attempt, path]);
  const more = async () => {
    if (!ref || !client || !state.value || state.value.nextCursor === null || state.loading || continuation.current) return;
    const previous = state.value;
    const target = ref;
    const epoch = generation.current;
    continuation.current = true;
    setState(old => ({ ...old, loading: true, error: false }));
    try {
      const next = await client.load<T>(kind, { ...target, version: previous.version }, path, previous.nextCursor!);
      // A work page the server answered after the turn advanced still merges: the entry list only
      // grows, so the window is unchanged, and `next` carries the server's current version. Only a
      // different block or an unmounted resource discards the fetch.
      if (epoch !== generation.current || !mounted.current || latest.current?.id !== target.id || latest.current?.turnId !== target.turnId) return;
      const value = ('events' in previous && 'events' in next
        ? { ...next, events: [...previous.events, ...next.events.filter(e => e.type !== 'turn.started')] }
        : { ...previous, ...next, input: (previous as HistoryDetail).input, content: (previous as HistoryDetail).content,
          text: ((previous as HistoryDetail).text ?? '') + ((next as HistoryDetail).text ?? ''),
          output: ((previous as HistoryDetail).output ?? '') + ((next as HistoryDetail).output ?? '') }) as T;
      client.remember(kind, target, value, path);
      setState({ value, loading: false, error: false });
    } catch { if (mounted.current && epoch === generation.current) setState(old => ({ ...old, loading: false, error: true })); }
    finally { if (epoch === generation.current) continuation.current = false; }
  };
  return { ...state, more, retry: () => { if (ref) client?.invalidate(kind, ref, path); setAttempt(n => n + 1); } };
}

function ResourceStatus({ resource }: { resource: { loading: boolean; error: boolean; retry: () => void } }) {
  if (resource.error) return <div className="history-status" role="status"><span>{t('view.detailFailed')}</span><button className="text-btn" onClick={resource.retry}>{t('common.retry')}</button></div>;
  if (resource.loading) return <div className="history-status" role="status">{t('common.loading')}</div>;
  return null;
}

function ResourceMore({ resource }: { resource: { value?: { nextCursor: number | null }; loading: boolean; more: () => void } }) {
  if (resource.value?.nextCursor == null) return null;
  return <button className="text-btn history-more" disabled={resource.loading} onClick={resource.more}>{t('view.detailMore')}</button>;
}

function DeferredFileDiff({ detail, path }: { detail: DetailRef; path: string }) {
  const resource = useHistoryResource<HistoryDetail>('file', detail, true, path);
  const diffs = useMemo(() => diffsOf(resource.value?.content).filter(d => d.path === path), [resource.value, path]);
  return <><ResourceStatus resource={resource} />{resource.value && <DiffView diffs={diffs} />}</>;
}

/** Copies on click and shows a tick for a moment, or says so when the browser refuses. */
/**
 * The pencil by a user message, and the box it opens in place of the bubble. Sending copies the
 * conversation up to just before this message and asks again from there; the page then reloads
 * the current conversation. Nothing changes until the user sends.
 */
function EditMessage({ text, imageIds, revision, onSend }: { text: string; imageIds: string[]; revision: number; onSend: (edit: { text: string; images: string[]; revision: number; requestId: string }) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(text);
  const [kept, setKept] = useState(imageIds);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const area = useRef<HTMLTextAreaElement | null>(null);
  const start = () => {
    setDraft(text);
    setKept(imageIds);
    setError('');
    setOpen(true);
    requestAnimationFrame(() => {
      const el = area.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      el.style.height = `${el.scrollHeight}px`;
    });
  };
  const send = async () => {
    if (busy || !draft.trim()) return;
    setBusy(true);
    setError('');
    try {
      await onSend({ text: draft, images: kept, revision, requestId: editRequestId() });
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  if (!open) return (
    <button type="button" className="quiet-btn message-edit-btn" aria-label={t('view.editMessage')} title={t('view.editMessage')} onClick={start}>
      <IconEdit size={14} />
    </button>
  );
  return (
    <div className="message-edit">
      <textarea
        ref={area}
        value={draft}
        aria-label={t('view.editMessage')}
        onChange={(e) => {
          setDraft(e.target.value);
          e.target.style.height = 'auto';
          e.target.style.height = `${e.target.scrollHeight}px`;
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); setOpen(false); }
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
        }}
      />
      {kept.length > 0 && (
        <div className="message-edit-images">
          {kept.map((id) => (
            <button key={id} type="button" className="message-edit-image" title={t('view.removeImage')} aria-label={t('view.removeImage')} onClick={() => setKept((list) => list.filter((it) => it !== id))}>
              <IconClose size={12} />
            </button>
          ))}
        </div>
      )}
      <div className="message-edit-hint">{t('view.editHint')}</div>
      {error && <div className="message-edit-error" role="alert">{error}</div>}
      <div className="message-edit-actions">
        <button type="button" className="ghost" onClick={() => setOpen(false)} disabled={busy}>{t('common.cancel')}</button>
        <button type="button" className="primary" onClick={() => void send()} disabled={busy || !draft.trim()}>
          {busy ? t('view.editing') : t('view.editSend')}
        </button>
      </div>
    </div>
  );
}

let editRequestSeq = 0;
/** One id per send, so a retry of the same edit cannot create a second conversation. */
function editRequestId(): string {
  return `edit-${Date.now().toString(36)}-${(++editRequestSeq).toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** The history the page last read; an edit sent against a stale one is refused by the daemon. */
function CopyButton({ text, label }: { text: string; label: string }) {  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle');
  useEffect(() => {
    if (state === 'idle') return;
    const timer = setTimeout(() => setState('idle'), 1600);
    return () => clearTimeout(timer);
  }, [state]);
  return (
    <button
      type="button"
      className="quiet-btn"
      aria-label={label}
      title={state === 'failed' ? t('view.copyFailed') : state === 'done' ? t('common.copied') : label}
      onClick={async () => setState((await copyText(text)) ? 'done' : 'failed')}
    >
      {state === 'done' ? <IconCheck size={15} /> : <IconCopy size={15} />}
    </button>
  );
}

function plainText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(plainText).join('');
  if (isValidElement<{ children?: ReactNode }>(node)) return plainText(node.props.children);
  return '';
}

/**
 * A fenced code block: a small header with the language and a copy button, then the code.
 * When the code is a whole SVG document it is shown as the picture it draws, with the source one
 * click away, as Claude and Open WebUI do. In an <img>, so that nothing in it runs or is fetched.
 */
function CodeBlock({ children }: { children?: ReactNode }) {
  const code = Children.toArray(children).find((c) => isValidElement(c));
  const className = isValidElement<{ className?: string }>(code) ? (code.props.className ?? '') : '';
  const language = /language-([\w+#.-]+)/.exec(className)?.[1] ?? '';
  const text = plainText(children).replace(/\n$/, '');
  const picture = useMemo(() => svgPicture(text), [text]);
  // A source the browser cannot draw (not well formed) stays code.
  const [failed, setFailed] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);
  // A mermaid block draws itself, once the library is in; until then and forever if it cannot
  // parse, the code stands in.
  const [diagram, setDiagram] = useState<string | null>(null);
  const [enlarged, setEnlarged] = useState(false);
  // Clone only on opening. Keep the full-size drawing in sync with theme changes.
  const largeDiagram = useMemo(() => enlarged && diagram ? enlargeMermaidDiagram(diagram) : null, [enlarged, diagram]);
  const closeDiagram = useCallback(() => setEnlarged(false), []);
  useEffect(() => {
    if (language !== 'mermaid') return;
    let cancelled = false;
    void renderMermaidDiagram(text)
      .then((svg) => !cancelled && setDiagram(svg))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [language, text]);
  // The look can flip while the answer stays on screen: redraw with it.
  useEffect(() => {
    if (language !== 'mermaid' || diagram === null) return;
    const root = document.documentElement;
    const observer = new MutationObserver(() => {
      void renderMermaidDiagram(text)
        .then((svg) => setDiagram(svg))
        .catch(() => undefined);
    });
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme', 'data-palette'] });
    return () => observer.disconnect();
  }, [language, text, diagram]);
  const pictures = useContext(Pictures);
  const drawn = (picture !== null && picture !== failed) || diagram !== null;
  return (
    <div className="code-block">
      <div className="code-head">
        <span>{language}</span>
        <span className="code-actions">
          {drawn && (
            <span className="code-views" role="group" aria-label={t('view.display')}>
              <button type="button" aria-pressed={!showCode} onClick={() => setShowCode(false)}>
                {t('files.preview')}
              </button>
              <button type="button" aria-pressed={showCode} onClick={() => setShowCode(true)}>
                {t('view.code')}
              </button>
            </span>
          )}
          <CopyButton text={text} label={t('view.copyCode')} />
        </span>
      </div>
      {drawn && !showCode ? (
        diagram !== null ? (
          <>
            <div className="code-diagram" role="button" tabIndex={0} aria-label={t('view.enlarge')} title={t('view.enlarge')}
              onClick={(event) => {
                // Drag selection and links inside a diagram keep their original meaning.
                if (window.getSelection()?.isCollapsed === false || (event.target as Element).closest('a')) return;
                event.currentTarget.focus({ preventScroll: true });
                setEnlarged(true);
              }}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return;
                event.preventDefault();
                setEnlarged(true);
              }}
              dangerouslySetInnerHTML={{ __html: diagram }} />
            {largeDiagram && <Lightbox svg={largeDiagram} onClose={closeDiagram} />}
          </>
        ) : (
          <button type="button" className="code-picture" aria-label={t('view.enlarge')} onClick={() => picture && pictures?.show(picture, 'drawing.svg')}>
            <img src={picture ?? undefined} alt={t('view.svg')} onError={() => picture && setFailed(picture)} />
          </button>
        )
      ) : (
        <pre>{children}</pre>
      )}
    </div>
  );
}

/**
 * An image in Markdown. One from the web is shown as it is. A local path cannot be loaded by the
 * browser (it is a file on the agent's host), so it is left out here and shown under the message.
 */
function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  if (!src || !/^(https?:|data:image\/)/i.test(src)) return null;
  return <img src={src} alt={alt ?? ''} loading="lazy" />;
}

/**
 * How something inside an answer puts a picture up at full size. `openFile` fetches one on the
 * agent's host, and rejects when the daemon has no such picture; `show` takes one already at hand.
 * Reaches the links and code blocks through context: the components Markdown is given stay the
 * same objects, so a message is not parsed again for it.
 */
const Pictures = createContext<{
  openFile: (path: string) => Promise<void>;
  show: (url: string, name?: string) => void;
  /** Opens any other file in the file viewer; rejects when the thread cannot show it. */
  openText?: (path: string) => Promise<void>;
} | null>(null);

/**
 * A link in an answer. One to the web opens in a tab of its own, so the hub stays where it is.
 * One to a file is on the agent's host, where the browser cannot follow: a picture opens at full
 * size, anything else in the file viewer. A file the thread cannot show gives its path to the
 * clipboard instead, for a terminal or an editor.
 */
function MarkdownLink({ href, title, children }: { href?: string; title?: string; children?: ReactNode }) {
  const pictures = useContext(Pictures);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => setNote(null), 3200);
    return () => clearTimeout(timer);
  }, [note]);

  const target = linkTarget(href ?? '');
  if (target.kind === 'web') {
    return (
      <a href={target.url} title={title} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  }
  if (target.kind === 'anchor') return <a href={href}>{children}</a>;
  if (target.kind === 'none') return <>{children}</>;

  const { path } = target;
  const picture = pictures !== null && IMAGE_FILE.test(path);
  const copy = async () => setNote((await copyText(path)) ? t('view.pathCopied') : t('view.copyFailedSelect'));
  const failed = async (error: unknown) => {
    const copied = await copyText(path);
    if (error instanceof ApiError && error.status === 404) setNote(copied ? t('view.missingCopied') : t('view.missing'));
    else setNote(copied ? t('view.noPreviewCopied') : t('view.noPreview'));
  };
  const act = () => {
    // The path is copied in the click itself where it can be: a browser may refuse once a fetch has come between.
    if (picture) pictures.openFile(path).catch(failed);
    else if (pictures?.openText) pictures.openText(path).catch(failed);
    else void copy();
  };
  return (
    <a
      className="file-link"
      role="button"
      tabIndex={0}
      title={title ?? (picture ? t('view.viewImage', path) : pictures?.openText ? t('view.openFile', path) : t('view.copyPath', path))}
      onClick={act}
      onKeyDown={(e: KeyboardEvent) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        act();
      }}
    >
      {children}
      {note && (
        <span className="file-link-note" role="status">
          {note}
        </span>
      )}
    </a>
  );
}

const MARKDOWN_COMPONENTS = { pre: CodeBlock, img: MarkdownImage, a: MarkdownLink };
const MARKDOWN_PLUGINS = [remarkGfm];
/**
 * Markdown drops addresses it does not know, `file://` among them, leaving a link that reloads
 * the page. Every address is kept instead: links and images are drawn by the components above,
 * which decide what each may do.
 */
const keepUrl = (url: string) => url;

interface AssistantProps {
  text: string;
  done: boolean;
  roots: string[];
  loadFile: (path: string) => Promise<Blob>;
  onOpen: (url: string, name?: string) => void;
}

/**
 * Parsing Markdown is the costly part of rendering a thread, and every streamed event re-renders
 * the list. Memoising on the props means only the message being written is parsed again.
 */
const AssistantMessage = memo(function AssistantMessage({ text, done, roots, loadFile, onOpen }: AssistantProps) {
  // Looked for once the message is complete: a path that is still being typed would not be found.
  const images = useMemo(() => (done ? imagePathsIn(text, roots) : []), [done, text, roots]);
  return (
    <div className="row assistant">
      <div className="markdown">
        <Markdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS} urlTransform={keepUrl}>
          {text}
        </Markdown>
      </div>
      {images.length > 0 && (
        <div className="file-images">
          {images.map((path) => (
            <FileImage key={path} path={path} roots={roots} load={loadFile} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  );
});

/** Text an agent wrote, read as Markdown, for places outside a thread's own timeline. */
export function Prose({ text, className = '' }: { text: string; className?: string }) {
  return (
    <div className={`markdown ${className}`.trim()}>
      <Markdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS} urlTransform={keepUrl}>
        {text}
      </Markdown>
    </div>
  );
}

/**
 * What another agent of the team sent into this thread. It is not the user's words, so it is not
 * a bubble: a card folded to who sent it and how it opens, and read as Markdown once opened.
 */
const DeliveredNote = memo(function DeliveredNote({ turnId, note, text }: { turnId: string; note: CoordinationNote; text: string }) {
  const [open, setOpen] = useState(false);
  const preview = useMemo(() => deliveredPreview(text), [text]);
  return (
    <div className={`row delivered${open ? ' open' : ''}`} data-turn={turnId}>
      <button type="button" className="delivered-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="delivered-title">
          <IconAgents size={15} />
          <span className="delivered-from">{note.from || t('team.member')}</span>
          <span className="muted">{note.kind === 'result' ? t('view.deliveredResult') : t('view.deliveredTask')}</span>
          {note.failed && <span className="delivered-failed">{t('team.failed')}</span>}
          <IconChevron size={14} open={open} />
        </span>
        {!open && preview && <span className="delivered-preview">{preview}</span>}
      </button>
      {open && <Prose className="delivered-body" text={text || t('team.noText')} />}
    </div>
  );
});

/** An image file the answer points at. Shown only if the daemon confirms it is one; otherwise nothing. */
function FileImage({ path, roots, load, onOpen }: { path: string; roots: string[]; load: (path: string) => Promise<Blob>; onOpen: (url: string, name?: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    load(path)
      .then(pictureUrl)
      .then((address) => {
        if (!alive) return void URL.revokeObjectURL(address);
        made = address;
        setUrl(made);
      })
      .catch(() => undefined); // not an image, not in this thread's directory, or gone
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [path, load]);

  if (!url) return null;
  return (
    <figure className="file-image">
      <button type="button" onClick={() => onOpen(url, path)} aria-label={t('view.enlargePath', path)}>
        <img src={url} alt={path} />
      </button>
      <figcaption title={path}>{relativePath(path, roots)}</figcaption>
    </figure>
  );
}

/** How deep a plan card goes before the rest fades away, and over what distance it fades. */
const PLAN_FADE_PX = 64;

/**
 * A plan in the thread: a small card holding the opening of the plan, fading out where it is cut,
 * and opening to the whole of it. While it is still being written it follows along, pinned to the
 * newest lines with the top fading instead.
 */
function ProposalCard({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const [open, setOpen] = useState(false);
  const [overflow, setOverflow] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (streaming) el.scrollTop = el.scrollHeight;
    setOverflow(el.scrollHeight - el.clientHeight > PLAN_FADE_PX / 2);
  }, [text, streaming]);
  return (
    <div className="row">
      <section className={`proposal${streaming ? ' streaming' : ''}${overflow ? ' overflow' : ''}`}>
        <div className="proposal-head">
          <span className="proposal-title">
            <IconList size={16} />
            {streaming ? t('plan.cardStreaming') : t('plan.cardTitle')}
          </span>
          <span className="proposal-tools">
            <PlanDownloadButton text={text} />
            <PlanCopyButton text={text} />
          </span>
        </div>
        <button type="button" className="proposal-body" aria-label={t('plan.openFull')} title={t('plan.openFull')} onClick={() => setOpen(true)}>
          <div className="proposal-scroll" ref={scrollRef}>
            <Prose text={text} />
          </div>
        </button>
      </section>
      {open && <PlanReader text={text} onClose={() => setOpen(false)} />}
    </div>
  );
}

/** What a resolved question, plan decision or approval says, and which mark leads it. */
type Trace = { icon: 'check' | 'hand'; text: string } | null;

/** An answered question: what was asked, in short, and what was picked. */
function questionTrace(item: Extract<Item, { kind: 'input' }>): Trace {
  const answer = item.answer;
  if (!answer) return null;
  if ('cancelled' in answer) {
    const asked = item.questions[0]?.question ?? item.title ?? '';
    return { icon: 'hand', text: 'ended' in answer ? t('trace.ended', asked) : t('trace.skipped', asked) };
  }
  const parts = item.questions.map((q) => {
    const said = (answer.answers[q.id] ?? []).map((value) => q.options.find((o) => o.value === value)?.label ?? value).join(' · ');
    return `${q.header || q.question}：${said}`;
  });
  return { icon: 'check', text: t('trace.answered', parts) };
}

/** A plan approval the agent asked for: carried out, sent back with changes, or left behind. */
function planTrace(item: Extract<Item, { kind: 'input' }>): Trace {
  const answer = item.answer;
  if (!answer) return null;
  if ('cancelled' in answer) return { icon: 'hand', text: t('trace.planSkipped') };
  const q = item.questions[0];
  const said = q ? (answer.answers[q.id] ?? [])[0] ?? '' : '';
  if (q && said === q.options[0]?.value) return { icon: 'check', text: t('trace.planConfirmed') };
  return { icon: 'hand', text: t('trace.planReturned', said) };
}

/** A tool approval: allowed, or refused (which a cancellation amounts to here). */
function approvalTrace(item: Extract<Item, { kind: 'approval' }>, roots: string[]): Trace {
  const decision = item.decision;
  if (!decision) return null;
  const title = shortenPaths(item.title, roots);
  const allowed = 'optionId' in decision && item.options.find((o) => o.optionId === decision.optionId)?.kind.startsWith('allow');
  return { icon: allowed ? 'check' : 'hand', text: allowed ? t('trace.allowed', title) : t('trace.rejected', title) };
}

/** What an answered question or approval leaves behind: one quiet line, there but not in the way. */
function TraceLine({ mark }: { mark: Trace }) {
  if (!mark) return null;
  return (
    <div className="trace-line">
      {mark.icon === 'check' ? <IconCheck size={13} /> : <IconHand size={13} />}
      <span className="trace-line-text">{mark.text}</span>
    </div>
  );
}

/**
 * The agent's own task list for an earlier turn, folded to one quiet line: how far along it got.
 * The whole list opens below it, in the same muted register as the work it steered. The newest
 * turn's plan is in the top bar instead (see `PlanButton`).
 */
function PlanCard({ item }: { item: Extract<Item, { kind: 'plan' }> }) {
  const [open, setOpen] = useState(false);
  const { total, completed, current } = planProgress(item);
  return (
    <div className="row plan">
      <button type="button" className="plan-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <PlanRing done={total ? completed / total : 0} />
        <span className="plan-count">{t('view.planProgress', completed, total)}</span>
        {current && <span className="plan-current">· {current.content}</span>}
        <IconChevron size={14} open={open} />
      </button>
      {open && <PlanList item={item} />}
    </div>
  );
}

function SentImage({ image, load, onOpen }: { image: ImageRef; load: (id: string) => Promise<Blob>; onOpen: (url: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    load(image.id)
      .then((blob) => {
        if (!alive) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [image.id, load]);

  if (failed) return <div className="sent-image missing">{t('view.imageFailed')}</div>;
  if (!url) return <div className="sent-image loading" aria-label={t('view.imageLoading')} />;
  return (
    <button type="button" className="sent-image" onClick={() => onOpen(url)} aria-label={t('view.enlarge')}>
      <img src={url} alt={t('view.sentImage')} />
    </button>
  );
}

function TurnWaiting({ item }: { item: Extract<Item, { kind: 'waiting' }> }) {
  const [now, setNow] = useState(() => new Date().toISOString());
  useEffect(() => {
    const tick = () => setNow(new Date().toISOString());
    tick();
    const timer = window.setInterval(tick, 1000);
    document.addEventListener('visibilitychange', tick);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
  }, [item.startedAt]);
  return <div className="row turn-waiting" role="status">
    <span className="work-toggle"><span className="work-label">{t(item.phase === 'preparing' ? 'view.preparing' : 'view.waitingResponse')} · {durationLabel(item.startedAt, now)}</span></span>
  </div>;
}

/** Only this small label ticks; the Markdown and tool output do not rerender every second. */
function WorkLabel({ item }: { item: Extract<Item, { kind: 'work' }> }) {
  const [now, setNow] = useState(() => new Date().toISOString());
  useEffect(() => {
    if (item.done || !item.timed) return;
    const tick = () => setNow(new Date().toISOString());
    tick();
    const timer = window.setInterval(tick, 1000);
    document.addEventListener('visibilitychange', tick);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
  }, [item.done, item.timed, item.startedAt]);
  const time = item.timed ? durationLabel(item.startedAt, item.done ? item.endedAt : now) : '';
  const label = !item.done ? time ? `${t('view.working')} · ${time}` : t('view.working') : item.stopped ? stoppedLabel(time) : time ? t('view.workedFor', time) : t('view.processed');
  return <span className="work-label">{label}</span>;
}

function WorkGroup({ item, roots }: { item: Extract<Item, { kind: 'work' }>; roots: string[] }) {
  // Open while the turn runs so progress is visible. Stopped turns stay open:
  // their interrupted thoughts are the user's history, not a completed work summary.
  const [open, setOpen] = useState(item.stopped || (!item.done && !item.deferred));
  // Collapse once a normal turn finishes; stopped turns keep their interrupted record visible.
  useEffect(() => {
    if (item.stopped) setOpen(true);
    else if (item.done) setOpen(false);
  }, [item.done, item.stopped]);
  // A turn still running when the thread is opened starts folded, its steps not yet fetched, so the
  // page draws first. Then it opens: what is going on now is what the thread was opened to see.
  useEffect(() => {
    if (item.done || !item.deferred) return;
    const frame = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(frame);
    // Once, on arrival: folding it again by hand is left alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const resource = useHistoryResource<HistoryWork>('work', item.deferred, open);
  // While the turn runs, continuation pages follow on their own — what is going on now is what
  // the thread was opened to see. Finished turns keep the manual button: old work loads on demand.
  const running = !item.done;
  useEffect(() => {
    if (!running || !open) return;
    if (resource.value?.nextCursor == null || resource.loading || resource.error) return;
    const frame = requestAnimationFrame(() => void resource.more());
    return () => cancelAnimationFrame(frame);
    // more() is re-created each render; the resource state is what paces the calls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, open, resource.value?.nextCursor, resource.loading, resource.error]);
  const local = useMemo(() => liveWorkFromEvents(item), [item]);
  const loaded = useMemo(() => resource.value ? buildTimeline(hydrateWork(resource.value.events, item.liveEvents ?? [], resource.value.version)).items.find((i): i is Extract<Item, { kind: 'work' }> => i.kind === 'work') : undefined, [resource.value, item.liveEvents]);
  const count = item.count ?? item.steps.length;
  const sections = workSections(loaded?.entries ?? local?.entries ?? item.entries);
  return (
    <div className="row work">
      <button className="work-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <WorkLabel item={item} />
        {count > 0 && <span className="muted">· {t('view.steps', count)}</span>}
        <IconChevron size={14} open={open} />
      </button>
      {open && (
        <div className="work-body">
          <ResourceStatus resource={resource} />
          {sections.map((section, i) =>
            section.kind === 'note' ? (
              <WorkNote key={section.key} text={section.text} />
            ) : section.kind === 'compaction' ? (
              <div className="context-compaction" key={section.key} role="status">
                <IconCompact size={18} />
                <span>{compactionLabel(section)}</span>
              </div>
            ) : (
              // Only the stretch at the end of a running turn is still going on.
              <StepRun key={section.key} run={section} live={!item.done && i === sections.length - 1} roots={roots} fold={!item.stopped} openThoughts={!!item.stopped} />
            ),
          )}
          {!running && <ResourceMore resource={resource} />}
        </div>
      )}
    </div>
  );
}

/** Something the agent said between two things it did. Memoised like an answer: Markdown is costly to parse. */
const WorkNote = memo(function WorkNote({ text }: { text: string }) {
  return (
    <div className="work-note markdown">
      <Markdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS} urlTransform={keepUrl}>
        {text}
      </Markdown>
    </div>
  );
});

/**
 * One stretch of the agent's thinking, kept small: while it is being thought, a line or two of
 * where it has got to; afterwards a single "Thought · Xs" line. Either opens up to the whole text.
 * A model can think for minutes before it acts, and that must not fill the screen.
 */
const ThinkingBlock = memo(function ThinkingBlock({ thought, live, defaultOpen = false }: { thought: Thought; live: boolean; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const resource = useHistoryResource<HistoryDetail>('detail', thought.detail, open);
  const text = resource.value?.text !== undefined ? resource.value.text + (thought.updates ?? []).filter(e => e.seq > resource.value!.version).map(e => e.text).join('') : thought.text;
  const paragraphs = useMemo(() => splitThinking(text), [text]);
  if (paragraphs.length === 0 && !thought.detail) return null;

  const secs = durationLabel(thought.startedAt, thought.endedAt);

  return (
    <div className="thinking">
      <button className="thinking-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>{live ? t('view.thinking') : t('view.thought', secs)}</span>
        <IconChevron size={13} open={open} />
      </button>
      {open ? (
        <div className="thinking-body">
          <ResourceStatus resource={resource} />
          {paragraphs.map((p, i) => (
            <p key={i} className="thinking-para">
              {p}
            </p>
          ))}
          <ResourceMore resource={resource} />
        </div>
      ) : (
        live && (
          <div className="thinking-body">
            <p className="thinking-para thinking-live">{thinkingPreview(paragraphs)}</p>
          </div>
        )
      )}
    </div>
  );
});

/** From this many finished calls on, a run is one line that counts them. Fewer are shown as they are. */
const RUN_FOLD_MIN = 2;

/**
 * A stretch of thinking and tool calls between two things the agent said. Several calls fold into
 * one line that counts them by kind ("explored 2 files, searched 1 time"), the way Codex does, and open to
 * the calls themselves. In a run that is still going on, what is in flight stays on screen as
 * itself; only what is over is folded.
 */
function StepRun({ run, live, roots, fold, openThoughts = false }: { run: Extract<WorkSection, { kind: 'run' }>; live: boolean; roots: string[]; fold: boolean; openThoughts?: boolean }) {
  const [open, setOpen] = useState(false);
  const last = run.entries.length - 1;
  const inFlight = (i: number) => {
    const entry = run.entries[i];
    return live && (entry.kind === 'step' ? stepActive(entry.step) : i === last);
  };
  const row = (i: number) => {
    const entry = run.entries[i];
    return entry.kind === 'thought' ? (
      <ThinkingBlock key={entry.thought.key} thought={entry.thought} live={live && i === last} defaultOpen={openThoughts} />
    ) : (
      <StepRow key={entry.step.id} step={entry.step} roots={roots} />
    );
  };
  const indexes = run.entries.map((_, i) => i);
  const over = indexes.filter((i) => !inFlight(i));
  const overSteps = over.map((i) => run.entries[i]).flatMap((e) => (e.kind === 'step' ? [e.step] : []));
  // A stopped turn already has the outer Codex-style disclosure. Opening it should reveal the
  // work directly instead of making interrupted thoughts and calls look as though they vanished.
  if (!fold || overSteps.length < RUN_FOLD_MIN) return <>{indexes.map(row)}</>;

  const summary = runSummary(overSteps);
  return (
    <>
      <div className="step-run">
        <button className="step-head run-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <ActivityIcon kind={summary.kind} />
          <span className="run-title">{summary.text}</span>
          <IconChevron size={13} open={open} />
        </button>
        {open && <div className="run-body">{over.map(row)}</div>}
      </div>
      {indexes.filter(inFlight).map(row)}
    </>
  );
}

function StepRow({ step: original, roots }: { step: Step; roots: string[] }) {
  const [open, setOpen] = useState(false);
  const client = useContext(HistoryContext);
  const resource = useHistoryResource<HistoryDetail>('detail', original.detail, open);
  const [full, setFull] = useState<{ state: 'idle' | 'loading' | 'loaded'; text?: string; error?: string }>({ state: 'idle' });
  const loadFullOutput = () => {
    if (!original.detail || full.state === 'loading') return;
    setFull({ state: 'loading' });
    void client?.api.historyToolOutput(client.threadId, original.detail).then(
      ({ output }) => setFull({ state: 'loaded', text: output }),
      (err: unknown) => setFull({ state: 'idle', error: err instanceof Error ? err.message : String(err) }),
    );
  };
  const step = useMemo(() => {
    if (!original.detail || !resource.value) return original;
    const newer = (original.liveVersion ?? 0) > resource.value.version;
    return { ...original, input: original.input ?? resource.value.input,
      output: newer ? original.output || resource.value.output : resource.value.output,
      diffs: newer && original.diffs.length ? original.diffs : diffsOf(resource.value.content) };
  }, [original, resource.value]);
  // The hub caps what it stores of a long tool output; the whole text is read on demand from the
  // agent's own session, or the page says so when that session cannot be read.
  const truncated = !full.text && resource.value?.outputTruncated === true;
  const shown = full.text ?? step.output;
  // An edit is best shown as its diff; the raw arguments would repeat the same text as JSON.
  const showInput = step.input !== undefined && step.diffs.length === 0 && JSON.stringify(step.input) !== '{}';
  // A command reads as the line that was typed, not as the JSON it was sent in.
  const command = stepCommand(step);
  const result = step.status === 'completed' ? t('view.succeeded') : step.status === 'failed' ? t('team.failed') : null;
  const hasContent = showInput || step.diffs.length > 0 || Boolean(step.output);
  const hasDetail = Boolean(original.detail) || hasContent || result !== null;
  return (
    <div className={`step ${step.status}`}>
      <button className="step-head" onClick={() => hasDetail && setOpen((v) => !v)} disabled={!hasDetail} aria-expanded={open}>
        <ActivityIcon kind={step.kind} />
        <span className="step-title">{nameAttachments(shortenPaths(stepTitle(step), roots)) || step.kind}</span>
        <span className="step-status">{stepStatusLabel(step)}</span>
      </button>
      {open && (
        <div className="step-detail">
          <div className="step-detail-content">
            <ResourceStatus resource={resource} />
            {showInput && (command ? <pre className="step-command">{command}</pre> : <pre>{JSON.stringify(step.input, null, 2)}</pre>)}
            {step.diffs.length > 0 && <DiffView diffs={step.diffs} />}
            {truncated && resource.value?.fullAvailable && full.state !== 'loading' && <button className="text-btn history-more" onClick={loadFullOutput}>{t('view.toolFullOutput')}</button>}
            {truncated && full.state === 'loading' && <div className="history-status" role="status">{t('common.loading')}</div>}
            {truncated && !resource.value?.fullAvailable && <div className="history-status" role="status">{t('view.toolFullOutputUnavailable')}</div>}
            {full.error && <div className="history-status" role="status"><span>{full.error}</span><button className="text-btn" onClick={loadFullOutput}>{t('common.retry')}</button></div>}
            {shown && <pre>{shown}</pre>}
            {!full.text && <ResourceMore resource={resource} />}
            {!hasContent && (!original.detail || resource.value) && !resource.loading && !resource.error && <div className="step-detail-empty">{t('view.noDetail')}</div>}
          </div>
          {result && <div className="step-result">{result}</div>}
        </div>
      )}
    </div>
  );
}

export function Stats({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="diff-stats">
      <span className="diff-added">+{added}</span>
      <span className="diff-removed">−{removed}</span>
    </span>
  );
}

/** One or more edited regions of a file, as numbered lines with added and removed lines tinted. */
export function DiffView({ diffs }: { diffs: FileDiff[] }) {
  const regions = useMemo(() => diffs.map((d) => numberLines(diffLines(d.oldText, d.newText))), [diffs]);
  let budget = MAX_DIFF_LINES;
  return (
    <div className="diff">
      {regions.map((lines, i) => {
        const shown = lines.slice(0, Math.max(0, budget));
        budget -= shown.length;
        const hidden = lines.length - shown.length;
        return (
          <div key={i} className="diff-region">
            {shown.map((line, n) => (
              <div key={n} className={`diff-line ${line.type}`}>
                <span className="diff-no">{line.oldNo ?? ''}</span>
                <span className="diff-no">{line.newNo ?? ''}</span>
                <span className="diff-sign">{line.type === 'add' ? '+' : line.type === 'del' ? '−' : ''}</span>
                <span className="diff-text">{line.text || ' '}</span>
              </div>
            ))}
            {hidden > 0 && <div className="diff-more">{t('view.moreLines', hidden)}</div>}
          </div>
        );
      })}
    </div>
  );
}

/** Files an answer can render: pages and drawings. */
const PREVIEW_FILE = /\.(html?|svg)$/i;

/** A collapsed change summary; files and their diffs are mounted only when opened. */
function ChangesCard({ item, roots, onPreview }: { item: Extract<Item, { kind: 'changes' }>; roots: string[]; onPreview?: (path: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [openPath, setOpenPath] = useState<string | null>(null);
  return (
    <div className="row">
      <div className="changes">
        <button type="button" className="changes-head" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
          <span className="changes-chevron"><IconChevron size={13} open={expanded} /></span>
          <span>{t('markdown.changed', item.files.length)}</span>
          <Stats added={item.added} removed={item.removed} />
        </button>
        {expanded && item.files.map((file: FileChange) => {
          const open = openPath === file.path;
          return (
            <div key={file.path} className="changes-file">
              <button className="changes-row" onClick={() => setOpenPath(open ? null : file.path)} aria-expanded={open}>
                <span className="changes-chevron">
                  <IconChevron size={13} open={open} />
                </span>
                <IconFile size={15} />
                <span className="changes-path" title={file.path}>
                  {relativePath(file.path, roots)}
                </span>
                <Stats added={file.added} removed={file.removed} />
              </button>
              {onPreview && PREVIEW_FILE.test(file.path) && (
                <button className="changes-preview" title={t('view.previewPage')} onClick={() => onPreview(file.path)}>
                  {t('files.preview')}
                </button>
              )}
              {open && (file.details?.length ? file.details.map(ref => <DeferredFileDiff key={ref.id} detail={ref} path={file.path} />) : <DiffView diffs={file.diffs} />)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** The button text for an approval option. The agent's own wording is kept where it carries the scope. */
export function optionLabel(option: ApprovalOption): string {
  switch (option.kind) {
    case 'allow_once':
      return t('view.allowOnce');
    case 'allow_always':
      return t('view.allowAlways');
    case 'reject_once':
      return t('view.reject');
    case 'reject_always':
      return t('view.rejectAlways');
    default:
      return option.name;
  }
}

/** The notes a message went out with: one quiet chip, opened to read them, like Codex. */
function SentNotes({ notes }: { notes: Note[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="notes-chip" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <IconQuote size={12} />
        {t('composer.notes', notes.length)}
        <IconChevron size={12} open={open} />
      </button>
      {open && (
        <ol className="sent-notes">
          {notes.map((n, i) => (
            <li key={i} className="note-strip">
              <span className="note-num">{i + 1}</span>
              <span className="note-body">
                {n.file && <span className="note-file" title={n.file}>{n.file}</span>}
                <span className="note-quote">{n.quote}</span>
                {n.comment && <span className="note-comment">{n.comment}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

/** A turn the user stopped, said the way Codex says it. */
function stoppedLabel(time: string): string {
  return time ? t('view.stoppedAfter', time) : t('team.stopped');
}
