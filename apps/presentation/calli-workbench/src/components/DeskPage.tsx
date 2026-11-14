import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { type Api, type Todo, type TodoNote, type TodoSubscription } from '../api';
import { buildDesk, byPeriod, dueLabel, LANES, needsPerson, NEXT_STEP, noteSections, pinUntil, QUADRANTS, safeSourceUrl, sourceLabel, tilt, whyHere, type DeskTodo, type Lane, type Pile } from '../desk';
import { formatDate, formatDateTime, t } from '../i18n';
import { DialogSurface } from './Dialog';
import { IconAgents, IconPin, IconSparkle, IconExternal, IconCheck, IconChevron, IconClose, IconFile, IconSideChat } from './icons';
import { Popover } from './Popover';
import { TriagePanel } from './TriagePanel';
import type { TriageView } from '../loopx/types';

interface Props {
  api: Api;
  todos: Todo[];
  triage?: TriageView | null;
  loading: boolean;
  error: string;
  refreshKey: number;
  onReload: () => void;
  onOpenThread: (id: string) => void;
}

const ORDER_KEY = 'calli.desk.order';
const ORDERS = ['matrix', 'progress', 'time'] as const;
type Order = typeof ORDERS[number];
function readOrder(): Order { try { const saved = localStorage.getItem(ORDER_KEY); return ORDERS.find(order => order === saved) ?? 'matrix'; } catch { return 'matrix'; } }
type OpenPile = { id: string; source: HTMLElement; from: DOMRect };
const rotation = (id: string) => ({ '--desk-tilt': `${tilt(id)}deg` } as CSSProperties);

/** The same plans, laid out as a personal desk. Only an open pile fetches its continuation note. */
export function DeskPage(props: Props) {
  const { api, todos, triage = null, loading, error, refreshKey, onReload, onOpenThread } = props;
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const timer = setInterval(() => setNow(new Date()), 60_000); return () => clearInterval(timer); }, []);
  const desk = useMemo(() => buildDesk(todos, now), [todos, now]);
  const [order, setOrder] = useState(readOrder);
  const [open, setOpen] = useState<OpenPile | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const piles = LANES.flatMap(lane => desk.lanes[lane]);
  const selected = piles.find(pile => pile.todo.id === open?.id);
  const periods = byPeriod(piles, now);
  useEffect(() => { if (refreshKey > 0) onReload(); }, [refreshKey, onReload]);
  const show = (id: string, source: HTMLElement) => { setActionError(''); setOpen({ id, source, from: source.getBoundingClientRect() }); };
  const closeOpen = () => {
    const source = open?.source; setOpen(null);
    requestAnimationFrame(() => (source?.isConnected ? source : document.getElementById('desk-stack-later') ?? document.querySelector<HTMLElement>('.desk-segment button'))?.focus({ preventScroll: true }));
  };
  const showMatter = (pile: Pile, fallback: HTMLElement) => {
    const source = document.getElementById(`desk-pile-${pile.todo.id}`) ?? fallback;
    show(pile.todo.id, source);
  };
  const act = async (fn: () => Promise<unknown>, close = false) => {
    if (busy) return;
    setBusy(true); setActionError('');
    try { await fn(); onReload(); if (close) setOpen(null); }
    catch (e) { setActionError(String(e)); }
    finally { setBusy(false); }
  };
  const resolve = (todo: DeskTodo, proposal: NonNullable<Todo['proposals']>[number], action: 'accept' | 'dismiss') =>
    act(() => api.resolveTodoProposal(todo.id, proposal.id, action, todo.rev), action === 'accept' && proposal.kind === 'done');

  const matrix = order === 'matrix';
  const pileCard = (pile: Pile) => <PileCover key={pile.todo.id} pile={pile} now={now} weighed={matrix} onOpen={source => showMatter(pile, source)} />;
  const laterPiles = matrix ? desk.quadrants.later : desk.lanes.later;
  const stack = (list: Pile[]) => <StackButton id="later" count={list.length} label={t('desk.laterCount', list.length)} onOpen={source => show('later', source)}><strong>{t('desk.laterCount', list.length)}</strong><span className="desk-stack-preview">{list.map(pile => pile.todo.text).join(' · ')}</span></StackButton>;
  const title = open?.id === 'inbox' ? t('desk.inbox') : open?.id === 'later' ? t('desk.later') : selected?.todo.text ?? '';
  return <div className="desk-page">
    <header className="desk-header">
      <div><h1>{formatDate(now, { month: 'long', day: 'numeric', weekday: 'long' })}</h1>
        {!loading && !error && <p className="desk-summary"><span className="desk-count-now">{t('desk.needsYou', triage ? triage.needsYou.length : desk.needsYou)}</span>{desk.stale > 0 && <span className="desk-meta-item"><span aria-hidden="true">·</span><span className="personal-warning">{t('desk.stale', desk.stale)}</span></span>}</p>}
      </div>
      <div className="desk-views">
        <div className="desk-segment" role="group" aria-label={t('desk.title')}>{ORDERS.map(value => <button key={value} aria-pressed={order === value} onClick={() => { setOrder(value); try { localStorage.setItem(ORDER_KEY, value); } catch { /* Keep the choice for this visit. */ } }}>{t(`desk.${value}`)}</button>)}</div></div>
    </header>
    <TriagePanel triage={triage} onOpenThread={onOpenThread} />
    {loading ? <p className="desk-state" role="status">{t('common.loading')}</p> : error ? <p className="desk-state" role="alert">{t('desk.loadFailed')} <button className="ghost" onClick={onReload}>{t('common.retry')}</button></p> : <div className="desk-board">
      <><div className={`desk-top${desk.inbox.length ? ' has-inbox' : ''}`}>
        <WeekCard week={desk.week} done={desk.doneThisWeek} />
        <section className="desk-first desk-paper"><h2>{t('desk.first')}</h2>
          {desk.first.length ? <ol>{desk.first.map(pile => <li key={pile.todo.id}><button onClick={e => showMatter(pile, e.currentTarget)}>{pile.todo.text}</button></li>)}</ol> : <p className="desk-empty">{t('desk.firstEmpty')}</p>}
        </section>
        {desk.inbox.length > 0 && <StackButton id="inbox" count={desk.inbox.length} label={t('desk.findCount', desk.inbox.length)} onOpen={source => show('inbox', source)}>
          <strong>{t('desk.inbox')}<span className="desk-inbox-count">{desk.inbox.length}</span></strong><span className="desk-stack-preview">{desk.inbox.map(todo => todo.text).join(' · ')}</span>
        </StackButton>}
      </div>
      {matrix ? <div className="desk-matrix">{QUADRANTS.map(key => {
        const list = desk.quadrants[key];
        return <section className={`desk-quad ${key}`} key={key}><h2>{t(`desk.quad.${key}`)}<small>{t(`desk.quadHint.${key}`)}</small><span>{list.length}</span></h2>
          <div className="desk-quad-body">{!list.length ? <p className="desk-empty">{t(key === 'now' ? 'desk.firstEmpty' : 'desk.emptyLane')}</p> : key === 'later' ? stack(list) : list.map(pileCard)}</div>
        </section>;
      })}</div> : <div className={`desk-lanes ${order}`}>
        {(order === 'progress' ? LANES : ['today', 'yesterday', 'earlier'] as const).map(key => {
          const list = key in desk.lanes ? desk.lanes[key as Lane] : periods[key as keyof typeof periods];
          return <section className="desk-lane" key={key}><h2>{t(`desk.${key}`)}<span>{list.length}</span></h2>
            <div className="desk-lane-body">{!list.length ? <p className="desk-empty">{t('desk.emptyLane')}</p> : key === 'later' ? stack(list) : list.map(pileCard)}</div>
          </section>;
        })}
      </div>}</>
    </div>}
    {open && <Spread key={open.id} origin={open} title={title || t('desk.solved')} onClose={closeOpen}
      actions={selected && <>
        <button className="primary" disabled={busy} onClick={() => void act(() => api.updateTodo(selected.todo.id, { done: true }), true)}><IconCheck size={14} />{t('desk.solved')}</button>
        <button className="ghost" disabled={busy} onClick={() => void act(() => api.updateTodo(selected.todo.id, { touch: true } as Parameters<Api['updateTodo']>[1] & { touch: boolean }))}>{t('desk.touch')}</button>
        <button className={`desk-quiet desk-important${selected.todo.importance === 'high' ? ' on' : ''}`} aria-pressed={selected.todo.importance === 'high'} disabled={busy} onClick={() => void act(() => api.updateTodo(selected.todo.id, { importance: selected.todo.importance === 'high' ? 'normal' : 'high' }))}><IconSparkle size={14} />{t('desk.important')}</button>
        <Popover panelClassName="desk-menu" label={t('desk.pin')} placement="below" triggerClassName={`desk-quiet${selected.todo.urgency?.reason === 'pinned' ? ' on' : ''}`} disabled={busy} trigger={<><IconPin size={14} />{t('desk.pin')}<IconChevron size={13} open /></>}>
          {close => <>{(['today', 'tomorrow', 'week'] as const).map(span => <button key={span} className="pop-item compact" onClick={() => { close(); void act(() => api.updateTodo(selected.todo.id, { urgentUntil: pinUntil(span, new Date()) })); }}>{t(`desk.pin.${span}`)}</button>)}
            {selected.todo.urgentUntil && <button className="pop-item compact" onClick={() => { close(); void act(() => api.updateTodo(selected.todo.id, { urgentUntil: null })); }}>{t('desk.pin.clear')}</button>}</>}
        </Popover>
        <Popover panelClassName="desk-menu" label={t('desk.group')} placement="below" triggerClassName="desk-quiet desk-lane-picker" disabled={busy} trigger={<>{t(`desk.${selected.lane}`)}<IconChevron size={13} open /></>}>
          {close => LANES.map(lane => <button key={lane} role="menuitemradio" aria-checked={selected.lane === lane} className="pop-item compact" onClick={() => { close(); void act(() => api.updateTodo(selected.todo.id, { lane })); }}>{t(`desk.${lane}`)}</button>)}
        </Popover>
      </>}>
      {actionError && <p className="desk-action-error" role="alert">{actionError}</p>}
      {open.id === 'inbox' ? <div className="desk-fan">{desk.inbox.length ? desk.inbox.map(todo => <article className="desk-card" key={todo.id} style={rotation(todo.id)}><span className="desk-card-kind">{t('desk.addProposal')}</span><h3>{todo.text}</h3><Suggestion todo={todo} now={now} />{todo.proposals?.filter(proposal => proposal.kind === 'add').map(proposal => <Proposal key={proposal.id} todo={todo} proposal={proposal} busy={busy} onResolve={resolve} />)}</article>) : <p className="desk-empty">{t('desk.noFinds')}</p>}</div>
        : open.id === 'later' ? <div className="desk-fan desk-later-fan">{laterPiles.map(pileCard)}</div>
        : selected ? <MatterCards api={api} pile={selected} busy={busy} onChange={(todo, done) => void act(() => api.updateTodo(todo.id, { done }))} onUnsubscribe={(todo, { kind, ref }) => void act(() => api.updateTodo(todo.id, { unsubscribe: [{ kind, ref }] }))} onResolve={resolve} onOpenThread={onOpenThread} /> : <p className="desk-empty">{t('desk.solved')}</p>}
    </Spread>}
  </div>;
}

function StackButton({ id, count, label, onOpen, children }: { id: string; count: number; label: string; onOpen: (source: HTMLElement) => void; children: ReactNode }) {
  return <button id={`desk-stack-${id}`} className="desk-pile desk-stack" style={rotation(id)} aria-label={label} onClick={e => onOpen(e.currentTarget)}>
    {count > 1 && <span className="desk-back one" />}{count > 2 && <span className="desk-back two" />}
    <span className="desk-cover">{children}</span>
  </button>;
}

/** What Router suggests for a new matter. Taking the matter keeps it; its reasons are in the proposal below. */
function Suggestion({ todo, now }: { todo: DeskTodo; now: Date }) {
  const why = whyHere(todo, now);
  const important = todo.importance === 'high';
  return <>
    {(important || why.urgent) && <p className="desk-suggest"><IconSparkle size={13} />{[important && t('desk.suggestImportant'), why.urgent && (todo.urgency?.reason === 'pinned' ? t('desk.suggestPinned') : t('desk.suggestUrgent', why.urgent))].filter(Boolean).join(' · ')}</p>}
    {why.important && <SourceLine todo={todo} label={why.important} />}
  </>;
}

function SourceLine({ todo, label }: { todo: DeskTodo; label: string }) {
  const url = safeSourceUrl(todo.source?.url);
  const at = todo.source?.at && Number.isFinite(Date.parse(todo.source.at)) ? formatDateTime(todo.source.at, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : null;
  return <p className="desk-source">{url ? <a href={url} target="_blank" rel="noreferrer">{label}<IconExternal size={13} /></a> : <span>{label}</span>}{at && <time>{at}</time>}</p>;
}

function PileCover({ pile, now, weighed, onOpen }: { pile: Pile; now: Date; weighed: boolean; onOpen: (source: HTMLElement) => void }) {
  const { todo } = pile;
  const why = weighed ? whyHere(todo, now) : null;
  const preview = todo.notePreview || todo.threads[0]?.title;
  // Where the reason already says when it is due, the date is not said twice.
  const due = todo.dueAt && !why?.urgent?.length ? dueLabel(todo.dueAt, now) : null;
  const stage = todo.stage ? t(`desk.stage.${todo.stage}`) : null;
  return <button id={`desk-pile-${todo.id}`} className="desk-pile" style={rotation(todo.id)} onClick={e => onOpen(e.currentTarget)} aria-label={[todo.text, stage, t('desk.cards', pile.cards)].filter(Boolean).join(' · ')}>
    {pile.cards > 1 && <span className="desk-back one" />}{pile.cards > 2 && <span className="desk-back two" />}
    <span className="desk-cover">{preview && <span className="desk-cover-preview"><span>{preview}</span></span>}
      <strong>{todo.text}</strong><span className="desk-cover-meta">{stage && <span className={needsPerson(todo.stage) ? 'personal-warning' : ''}>{stage}</span>}{why?.urgent && <span className={why.overdue ? 'personal-warning' : ''}>{why.urgent}</span>}{why?.important && <span>{why.important}</span>}{todo.owner === 'agent' && <span className="desk-cover-owner"><IconAgents size={11} />{t('desk.agent')}</span>}{weighed && (pile.lane === 'wait' || pile.lane === 'later') && <span>{t(`desk.${pile.lane}`)}</span>}{due && <span className={due.overdue ? 'personal-warning' : ''}>{due.text}</span>}{pile.idleDays > 0 && <span>{t('desk.idleDays', pile.idleDays)}</span>}</span>
      {todo.proposals?.some(proposal => proposal.kind === 'done') && <span className="desk-cover-alert">{t('desk.doneProposal')}</span>}
    </span>{pile.cards >= 2 && <span className="desk-count">{t('desk.cards', pile.cards)}</span>}
  </button>;
}

function WeekCard({ week, done }: { week: ReturnType<typeof buildDesk>['week']; done: number }) {
  const max = Math.max(1, ...week.map(day => day.done ?? 0));
  const [active, setActive] = useState<number | null>(null);
  return <section className={`desk-week desk-paper${done === 0 ? ' quiet' : ''}`}><header><h2>{t('desk.week')}</h2><span>{done === 0 ? t('desk.weekEmpty') : t('desk.finished', done)}</span></header>
    <div className="desk-chart">{week.map((day, index) => {
      const weekday = formatDate(`${day.date}T12:00:00`, { weekday: 'short' });
      const label = day.done === null ? t('desk.futureDay', weekday) : t('desk.dayCount', weekday, day.done);
      return <button key={day.date} className={`desk-chart-day${day.done === null ? ' future' : index === week.filter(day => day.done !== null).length - 1 ? ' today' : ''}`} aria-label={label} aria-expanded={active === index} onMouseEnter={() => setActive(index)} onMouseLeave={e => { if (e.currentTarget !== document.activeElement) setActive(null); }} onFocus={() => setActive(index)} onBlur={() => setActive(null)} onClick={() => setActive(index)}>
        <span className="desk-chart-track"><span className="desk-chart-bar" style={{ height: `${day.done === null ? 0 : day.done / max * 100}%` }} /></span><span className="desk-chart-label">{weekday}</span>
        {active === index && <span className="desk-chart-tip" role="tooltip">{label}</span>}
      </button>;
    })}</div>
  </section>;
}

/** A full-screen dialog with each sheet flying from, and back to, the original stack. */
function Spread({ origin, title, tool, actions, children, onClose }: { origin: OpenPile; title: string; tool?: ReactNode; actions?: ReactNode; children: ReactNode; onClose: () => void }) {
  const root = useRef<HTMLDivElement>(null);
  const closing = useRef(false);
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const animate = (back: boolean) => {
    const from = origin.source.isConnected ? origin.source.getBoundingClientRect() : origin.from;
    return [...(root.current?.querySelectorAll<HTMLElement>('.desk-card, .desk-later-fan > .desk-pile') ?? [])].map((card, i) => {
      const to = card.getBoundingClientRect();
      const folded = { transform: `translate(${from.left + from.width / 2 - to.left - to.width / 2}px, ${from.top + from.height / 2 - to.top - to.height / 2}px) scale(${Math.min(1, from.width / to.width) * .7}) rotate(0deg)`, opacity: 0 };
      const unfolded = { transform: getComputedStyle(card).transform, opacity: 1 };
      return card.animate(back ? [unfolded, folded] : [folded, unfolded], { duration: back ? 220 : 380, delay: back ? 0 : Math.min(i * 35, 140), easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' }).finished;
    });
  };
  useLayoutEffect(() => { if (!reduce) void Promise.allSettled(animate(false)); }, []);
  const close = () => {
    if (closing.current) return; closing.current = true;
    if (reduce) { onClose(); return; }
    void Promise.allSettled(animate(true)).then(onClose);
  };
  return createPortal(<div ref={root} className="desk-spread-layer"><DialogSurface className="desk-spread" labelledBy="desk-spread-title" onClose={close}>
    <header className="desk-spread-header"><h2 id="desk-spread-title">{title}</h2><div className="desk-spread-tools">{tool}<button className="icon-btn desk-close" aria-label={t('desk.collapse')} onClick={close}><IconClose size={20} /></button></div></header>
    {actions && <div className="desk-spread-actions">{actions}</div>}
    <div className="desk-spread-content" onClick={e => { if (e.target === e.currentTarget || (e.target as HTMLElement).classList.contains('desk-fan')) close(); }}>{children}</div>
  </DialogSurface></div>, document.body);
}

function Proposal({ todo, proposal, busy, onResolve }: { todo: DeskTodo; proposal: NonNullable<Todo['proposals']>[number]; busy: boolean; onResolve: (todo: DeskTodo, proposal: NonNullable<Todo['proposals']>[number], action: 'accept' | 'dismiss') => void }) {
  return <div className="desk-proposal">{proposal.evidence && <p>{proposal.evidence}</p>}<div className="desk-card-actions"><button className="primary" disabled={busy} onClick={() => onResolve(todo, proposal, 'accept')}>{t(proposal.kind === 'done' ? 'personal.acceptDone' : 'desk.track')}</button><button className="ghost" disabled={busy} onClick={() => onResolve(todo, proposal, 'dismiss')}>{t(proposal.kind === 'done' ? 'personal.dismiss' : 'desk.dismiss')}</button></div></div>;
}

/** The outside threads a matter follows. Folded by default: following is background, not news. */
function Subscriptions({ list, busy, onUnsubscribe }: { list: TodoSubscription[]; busy: boolean; onUnsubscribe: (item: TodoSubscription) => void }) {
  const [open, setOpen] = useState(false);
  return <div className="desk-subscriptions">
    <button className="desk-subscriptions-toggle" aria-expanded={open} onClick={() => setOpen(on => !on)}>{t('desk.subscriptions', list.length)}<IconChevron size={13} open={open} /></button>
    {open && <ul>{list.map(item => <li key={`${item.kind}:${item.ref}`}><span title={item.ref}>{t(`desk.subscription.${item.kind}`)}</span><button className="ghost" disabled={busy} onClick={() => onUnsubscribe(item)}>{t('desk.unsubscribe')}</button></li>)}</ul>}
  </div>;
}

function MatterCards({ api, pile, busy, onChange, onUnsubscribe, onResolve, onOpenThread }: { api: Api; pile: Pile; busy: boolean; onChange: (todo: Todo, done: boolean) => void; onUnsubscribe: (todo: Todo, item: TodoSubscription) => void; onResolve: (todo: DeskTodo, proposal: NonNullable<Todo['proposals']>[number], action: 'accept' | 'dismiss') => void; onOpenThread: (id: string) => void }) {
  const { todo } = pile;
  const [note, setNote] = useState<TodoNote | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true; setNote(null); setError('');
    void api.todoNote(todo.id).then(value => { if (live) setNote(value); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [api, todo.id, todo.noteUpdatedAt, retry]);
  const sections = note ? noteSections(note.text) : [];
  const source = sourceLabel(todo.source);
  const subscriptions = todo.subscriptions ?? [];
  return <div className="desk-fan">
    {(source || subscriptions.length > 0) && <article className="desk-card desk-source-card" style={rotation(todo.id + 'source')}><span className="desk-card-kind">{t('desk.source')}</span>{source && <SourceLine todo={todo} label={source} />}{subscriptions.length > 0 && <Subscriptions list={subscriptions} busy={busy} onUnsubscribe={item => onUnsubscribe(todo, item)} />}</article>}
    <article className="desk-card desk-note-card" style={rotation(todo.id + 'note')}><span className="desk-card-kind"><IconFile size={14} />{t('desk.note')}</span>
      {!note && !error && <p role="status">{t('common.loading')}</p>}
      {error && <p role="alert">{error}<button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>}
      {note && (sections.length ? sections.map((section, i) => <section key={i} className={`desk-note-section${section.title === NEXT_STEP ? ' next' : ''}`}>{section.title && <h3>{section.title}</h3>}<p>{section.body}</p></section>) : <p className="desk-empty">{t('personal.noNote')}</p>)}
      {note?.updatedAt && <small className="desk-card-foot">{t('personal.updated', formatDateTime(note.updatedAt, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }))}</small>}
    </article>
    {pile.children.length > 0 && <article className="desk-card" style={rotation(todo.id + 'steps')}><span className="desk-card-kind">{t('desk.checklist')}</span><ul className="desk-checklist">{pile.children.map(child => <li key={child.id}><button role="checkbox" aria-checked={child.done} disabled={busy} onClick={() => onChange(child, !child.done)}><span className="desk-checkbox">{child.done && <IconCheck size={13} />}</span><span>{child.text}</span></button></li>)}</ul></article>}
    {todo.threads.map(thread => <article className="desk-card desk-thread-card" key={thread.id} style={rotation(thread.id)}><span className="desk-card-kind"><IconSideChat size={14} />{t('desk.conversations')}</span><button className="desk-thread-link" onClick={() => onOpenThread(thread.id)}><h3>{thread.title}</h3><IconExternal size={17} /></button><p className="desk-thread-status">{t(`status.${thread.status}`)}</p></article>)}
    {todo.proposals?.map(proposal => <article className="desk-card" key={proposal.id} style={rotation(proposal.id)}><span className="desk-card-kind">{t(proposal.kind === 'done' ? 'desk.doneProposal' : 'desk.addProposal')}</span><Proposal todo={todo} proposal={proposal} busy={busy} onResolve={onResolve} /></article>)}
    <article className="desk-card desk-time-card" style={rotation(todo.id + 'time')}><span className="desk-card-kind">{t('desk.timing')}</span>{todo.followUpPaused && <p className="personal-warning">{t('personal.paused')}</p>}<dl><div><dt>{t('desk.created')}</dt><dd>{formatDate(todo.createdAt, { month: 'short', day: 'numeric' })}</dd></div>{todo.dueAt && <div><dt>{t('desk.due')}</dt><dd className={pile.overdue ? 'personal-warning' : ''}>{formatDateTime(todo.dueAt, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</dd></div>}{todo.followUpAt && <div><dt>{t('desk.followUp')}</dt><dd>{formatDateTime(todo.followUpAt, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</dd></div>}{todo.waitingOn && <div><dt>{t('desk.waitingOn')}</dt><dd>{todo.waitingOn}</dd></div>}</dl></article>
  </div>;
}
