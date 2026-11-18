import { TodoDelegate, TodoThreadPicker, type DelegateRequest } from './TodoDelegate';
import { useIsNarrow } from '../hooks';
import { AssistantSettings } from './AssistantSettingsPage';
import { TodoDetails, TodoMetadata, Wishes } from './PersonalTodo';
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { ApiError, type AgentInfo, type PersonalSettings, type Api, type Thread, type Todo, type TodoSummaryStatus } from '../api';
import { buildTodoBoard, indentPlace, linkableThreads, moveTodo, openRows, outdentPlace, type TodoNode, type TodoPlace } from '../todos';
import { Dialog } from './Dialog';
import { Menu, type MenuEntry } from './Menu';
import { Popover } from './Popover';
import { IconFile, IconSettings, IconSparkle, IconAgent, IconArrowDown, IconArrowUp, IconCheck, IconChevron, IconClose, IconGrip, IconList, IconMore, IconPlus } from './icons';
import { t } from '../i18n';

interface Props {
  agents: AgentInfo[];
  personal: PersonalSettings | null;
  personalError: string;
  onPersonalChange: (value: PersonalSettings) => void;
  onPersonalRetry: () => void;
  api: Api;
  /** The whole list, owned by App: the thread menu needs it too. */
  todos: Todo[];
  /** All live threads, for the link submenu. */
  threads: Thread[];
  /** Changes when the daemon broadcasts a plan or thread change, so the list reloads. */
  refreshKey: number;
  onReload: () => void;
  /** Shows a change at once, before the server has it: a move, a deletion. */
  onChange: (todos: Todo[]) => void;
  /** Opens a linked thread, which takes the page's place. */
  onOpenThread: (id: string) => void;
  onError: (e: unknown) => void;
}

function below(e: MouseEvent<HTMLElement>): [number, number] {
  const rect = e.currentTarget.getBoundingClientRect();
  return [rect.left, rect.bottom + 4];
}



/** Whether the done group is open, kept on this device. */
const SHOW_DONE_KEY = 'calli.todos.showDone';
const SUMMARY_DAYS_KEY = 'calli.todos.summaryDays';
const SUMMARY_RANGES = [1, 3, 7, 14];

function readSummaryDays(): number {
  try {
    const days = Number(localStorage.getItem(SUMMARY_DAYS_KEY));
    return SUMMARY_RANGES.includes(days) ? days : 7;
  } catch {
    return 7;
  }
}

function readShowDone(): boolean {
  try {
    return localStorage.getItem(SHOW_DONE_KEY) === '1';
  } catch {
    return false;
  }
}

/** Where a dragged row would land, and where to draw the line that says so. */
interface DropTarget {
  before: string | null;
  top: number;
  left: number;
  width: number;
}

/**
 * Enter that means "done typing". While an input method is composing (pinyin picking its
 * characters), Enter belongs to it and must not submit half a word.
 */
const isCommit = (e: ReactKeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => e.key === 'Enter' && !e.nativeEvent.isComposing;

/**
 * A plan's text while it is being edited: the same line, in the same place, with a caret in it.
 * It grows with what is typed, so a long plan is edited as it reads, not through a one-line slot.
 */
function TodoEditor({
  value,
  onChange,
  onSave,
  onCancel,
  onTab,
  onDeleteEmpty,
}: {
  value: string;
  onChange: (text: string) => void;
  onSave: () => void;
  onCancel: () => void;
  /** Tab: one level in; Shift+Tab: one level out. */
  onTab: (out: boolean) => void;
  /** Backspace on a line with nothing left on it. */
  onDeleteEmpty: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  // The caret starts at the end of the line, where the next word goes.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  return (
    <textarea
      ref={ref}
      className="todo-edit"
      rows={1}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onSave}
      onKeyDown={(e) => {
        // A plan is one paragraph: Enter finishes it instead of breaking the line.
        if (isCommit(e)) {
          e.preventDefault();
          onSave();
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          onCancel();
        }
        // Like a checklist in a document: Tab nests the line, Shift+Tab takes it back out, and
        // Backspace on an empty line removes it.
        if (e.key === 'Tab' && !e.nativeEvent.isComposing) {
          e.preventDefault();
          onTab(e.shiftKey);
        }
        if (e.key === 'Backspace' && value === '' && !e.nativeEvent.isComposing) {
          e.preventDefault();
          onDeleteEmpty();
        }
      }}
      aria-label={t('todos.edit')}
    />
  );
}

/**
 * The plan page: the user's own long-lived list, laid out as a document in the main area. Plans
 * can be struck through and linked to threads, so the work happening in threads and the plan it
 * serves stay side by side.
 */
export function TodosPage({ agents, personal, personalError, onPersonalChange, onPersonalRetry, api, todos, threads, refreshKey, onReload, onChange, onOpenThread, onError }: Props) {
  const [adding, setAdding] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pendingDelegate, setPendingDelegate] = useState<DelegateRequest | null>(null);
  const narrow = useIsNarrow();
  const [linkRequest, setLinkRequest] = useState<DelegateRequest | null>(null);
  const [delegating, setDelegating] = useState(false);
  const [wishCount, setWishCount] = useState(0);
  const [wishError, setWishError] = useState('');
  const loadWishes = () => { if (api.demo) return; setWishError(''); void api.wishCount().then(v => setWishCount(v.count)).catch(e => setWishError(String(e))); };
  useEffect(loadWishes, [api, refreshKey]);
  // "Summarize recent days": a personal thread groups recent threads into the plan. Its result is the
  // plan itself, so the page stays here; while it runs, the button opens that thread instead.
  const [summaryDays, setSummaryDays] = useState(readSummaryDays);
  const [summary, setSummary] = useState<TodoSummaryStatus | null>(null);
  const [summarizing, setSummarizing] = useState(false);
  const [pendingSummary, setPendingSummary] = useState(false);
  useEffect(() => { if (!api.demo) void api.todoSummary().then(setSummary).catch(() => setSummary(null)); }, [api, refreshKey]);
  const chooseSummaryDays = (days: number) => {
    setSummaryDays(days);
    try { localStorage.setItem(SUMMARY_DAYS_KEY, String(days)); } catch { /* the choice lasts until the page reloads */ }
  };
  const summarize = async (configured = false) => {
    if (summarizing) return;
    if (summary?.running && summary.threadId) { onOpenThread(summary.threadId); return; }
    if (!configured && personal && !personal.agent) { setPendingSummary(true); setSettingsOpen(true); return; }
    setSummarizing(true);
    try {
      const now = new Date();
      const thread = await api.summarizeTodos(summaryDays, t('todos.summaryTitle', summaryDays), t('todos.summaryHeading', now.getMonth() + 1, now.getDate()));
      setPendingSummary(false);
      setSummary({ threadId: thread.id, running: true, days: summaryDays, startedAt: now.toISOString() });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === 'personal agent is not configured') { setPendingSummary(true); setSettingsOpen(true); }
      else onError(e);
    } finally { setSummarizing(false); }
  };
  const toggleDetails = (id: string) => setExpanded(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else { if (narrow) next.clear(); next.add(id); } return next; });
  const delegate = async (request: DelegateRequest, configured = false) => {
    if (delegating) return;
    if (!configured && personal && !personal.agent) { setPendingDelegate(request); setSettingsOpen(true); return; }
    setDelegating(true);
    let id = request.id;
    try {
      if (!id) { const created = await api.createTodo(request.text!); id = created.id; setAdding(''); onReload(); }
      const thread = await api.delegateTodo(id, request.cwd); setPendingDelegate(null); onReload(); onOpenThread(thread.id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === 'personal agent is not configured') { setPendingDelegate({ ...request, id }); setSettingsOpen(true); }
      else onError(e);
    } finally { setDelegating(false); }
  };
  // The plan a sub-item is being typed under, with what has been typed so far.
  const [child, setChild] = useState<{ parentId: string; text: string } | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [menu, setMenu] = useState<{ todo: Todo; x: number; y: number } | null>(null);
  const [deleting, setDeleting] = useState<Todo | null>(null);
  const [showDone, setShowDone] = useState(readShowDone);
  const [dragging, setDragging] = useState<string | null>(null);
  const [drop, setDrop] = useState<DropTarget | null>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  // While a line moves or goes away under the caret, the editor it leaves must not save on blur:
  // the edit carries on in the line's new place.
  const holdBlur = useRef(false);
  const hold = () => {
    holdBlur.current = true;
    setTimeout(() => {
      holdBlur.current = false;
    });
  };
  // On touch there is no handle to drag by; the row's menu moves it up and down instead.
  const touch = typeof window !== 'undefined' && Boolean(window.matchMedia?.('(hover: none)').matches);

  // Reload when anything changes on the server: plan edits from another device, or thread
  // states that the chips show.
  useEffect(() => {
    if (refreshKey > 0) onReload();
    // onReload only refetches; it is not a reason to run again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const act = async (run: () => Promise<unknown>) => {
    try {
      await run();
      onReload();
    } catch (e) {
      onError(e);
    }
  };

  const board = buildTodoBoard(todos.filter(todo => !todo.proposed));
  const empty = board.open.length === 0 && board.done.length === 0;

  const add = async (parentId: string | null, text: string) => {
    const value = text.trim();
    if (!value) return;
    await act(() => api.createTodo(value, parentId));
  };

  const toggle = (todo: Todo) => void act(() => api.updateTodo(todo.id, { done: !todo.done }));

  const toggleDone = () =>
    setShowDone((v) => {
      try {
        localStorage.setItem(SHOW_DONE_KEY, v ? '0' : '1');
      } catch {
        // Storage unavailable: the choice lasts until the page reloads.
      }
      return !v;
    });

  /** Moves an item, shown at once; `text` is an edit in progress that goes along with it. */
  const relocate = async (id: string, to: TodoPlace, text?: string) => {
    const current = todos.find((t) => t.id === id);
    const value = text?.trim();
    const edited = Boolean(value && current && value !== current.text);
    let next = moveTodo(todos, id, to);
    if (edited) next = next.map((t) => (t.id === id ? { ...t, text: value! } : t));
    onChange(next);
    try {
      if (edited) await api.updateTodo(id, { text: value });
      await api.moveTodo(id, to);
    } catch (e) {
      onError(e);
    }
    onReload();
  };

  const tab = (todo: TodoNode, out: boolean) => {
    const to = out ? outdentPlace(todos, todo.id) : indentPlace(board, todo.id);
    if (!to) return;
    hold();
    void relocate(todo.id, to, editing?.id === todo.id ? editing.text : undefined);
  };

  /** Backspace on an emptied line: it goes, and the caret moves up to the end of the line above. */
  const deleteEmpty = (todo: TodoNode) => {
    // A plan that still holds sub-items or threads is not thrown away by a stray key.
    if (todo.children.length > 0 || todo.threads.length > 0) return;
    const rows = openRows(board);
    const prev = rows[rows.findIndex((r) => r.id === todo.id) - 1];
    hold();
    setEditing(prev ? { id: prev.id, text: prev.text } : null);
    onChange(todos.filter((t) => t.id !== todo.id));
    void act(() => api.deleteTodo(todo.id));
  };

  /** The rows an item sits among, in order; null for finished plans, which sort by when they were done. */
  const siblingsOf = (todo: Todo): TodoNode[] | null => {
    if (todo.parentId) return [...board.open, ...board.done].find((p) => p.id === todo.parentId)?.children ?? null;
    return todo.done ? null : board.open;
  };

  /**
   * Dragging by the handle: the row stays where it is, faded, and a line shows where it will land
   * among its siblings. Levels change with Tab and Shift+Tab, not by dragging.
   */
  const startDrag = (e: ReactPointerEvent<HTMLButtonElement>, todo: TodoNode) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const row = e.currentTarget.closest<HTMLElement>('.todo');
    const list = row?.parentElement;
    const page = pageRef.current;
    if (!row || !list || !page) return;
    const scroller = page.parentElement;
    const rowsOf = () => [...list.children].filter((el): el is HTMLElement => el instanceof HTMLElement && el.classList.contains('todo'));

    const targetAt = (y: number): DropTarget => {
      const base = page.getBoundingClientRect();
      const box = list.getBoundingClientRect();
      const rows = rowsOf();
      const left = box.left - base.left;
      for (const r of rows) {
        const line = r.querySelector('.todo-line')?.getBoundingClientRect();
        if (line && y < line.top + line.height / 2) {
          return { before: r.dataset.id ?? null, top: r.getBoundingClientRect().top - base.top, left, width: box.width };
        }
      }
      const last = rows[rows.length - 1];
      return { before: null, top: (last ?? row).getBoundingClientRect().bottom - base.top, left, width: box.width };
    };

    let target: DropTarget | null = null;
    const move = (ev: PointerEvent) => {
      // Near the top or bottom edge, the page scrolls along.
      if (scroller) {
        const edge = scroller.getBoundingClientRect();
        if (ev.clientY < edge.top + 40) scroller.scrollBy(0, -12);
        else if (ev.clientY > edge.bottom - 40) scroller.scrollBy(0, 12);
      }
      target = targetAt(ev.clientY);
      setDrop(target);
    };
    const end = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      setDragging(null);
      setDrop(null);
      if (ev.type !== 'pointerup' || !target) return;
      const rows = rowsOf();
      const next = rows[rows.indexOf(row) + 1]?.dataset.id ?? null;
      // Dropped just above itself or just below: it stays where it was.
      if (target.before === todo.id || target.before === next) return;
      void relocate(todo.id, { parentId: todo.parentId, before: target.before });
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    setDragging(todo.id);
  };

  const saveEdit = () => {
    if (holdBlur.current) return;
    const next = editing;
    setEditing(null);
    if (!next) return;
    const value = next.text.trim();
    const current = todos.find((t) => t.id === next.id);
    if (!value || !current || current.text === value) return;
    void act(() => api.updateTodo(next.id, { text: value }));
  };

  const menuEntries = (todo: Todo): MenuEntry[] => {
    const isTop = todo.parentId === null;
    const choices = linkableThreads(todos, todo.id, threads);
    const siblings = siblingsOf(todo);
    const at = siblings ? siblings.findIndex((t) => t.id === todo.id) : -1;
    const moves: MenuEntry[] =
      touch && siblings
        ? [
            {
              id: 'up',
              label: t('todos.moveUp'),
              icon: IconArrowUp,
              disabled: at <= 0,
              onSelect: () => void relocate(todo.id, { parentId: todo.parentId, before: siblings[at - 1]?.id ?? null }),
            },
            {
              id: 'down',
              label: t('todos.moveDown'),
              icon: IconArrowDown,
              disabled: at === -1 || at >= siblings.length - 1,
              onSelect: () => void relocate(todo.id, { parentId: todo.parentId, before: siblings[at + 2]?.id ?? null }),
            },
            'separator',
          ]
        : [];
    return [
      ...moves,
      ...(isTop
        ? [{ id: 'add-child', label: t('todos.addChild'), icon: IconPlus, onSelect: () => setChild({ parentId: todo.id, text: '' }) }]
        : []),
      {
        id: 'link',
        label: t('todos.link'),
        icon: IconList,
        disabled: choices.length === 0,
        onSelect: () => setLinkRequest({ id: todo.id }),
      },
      'separator',
      { id: 'delete', label: t('common.delete'), icon: IconClose, danger: true, onSelect: () => setDeleting(todo) },
    ];
  };

  const chip = (todo: Todo, thread: Thread) => (
    <span key={thread.id} className={`todo-chip${thread.archivedAt ? ' archived' : ''}`}>
      <span className={`todo-chip-dot ${thread.status}${thread.waiting ? ' waiting' : ''}`} />
      <button type="button" className="todo-chip-name" title={t('todos.openThread', thread.title)} onClick={() => onOpenThread(thread.id)}>
        {thread.title || thread.cwd}
      </button>
      <button
        type="button"
        className="todo-chip-unlink"
        aria-label={t('todos.unlink')}
        onClick={() => void act(() => api.unlinkTodo(todo.id, thread.id))}
      >
        <IconClose size={11} />
      </button>
    </span>
  );

  const row = (todo: TodoNode, isChild: boolean) => {
    const editingThis = editing?.id === todo.id;
    return (
      <div key={todo.id} data-id={todo.id} className={`todo${isChild ? ' child' : ''}${dragging === todo.id ? ' dragging' : ''}${expanded.has(todo.id) ? ' expanded' : ''}`} >
        <div className="todo-line" onClick={e => { if (!api.demo && !(e.target as HTMLElement).closest('button, input, textarea, a')) toggleDetails(todo.id); }}>
          {/* Finished plans keep the order they were finished in; everything else can be dragged. */}
          {!touch && siblingsOf(todo) && (
            <button
              type="button"
              className="todo-grip"
              aria-label={t('todos.drag', todo.text)}
              tabIndex={-1}
              onPointerDown={(e) => startDrag(e, todo)}
            >
              <IconGrip size={14} />
            </button>
          )}
          <button
            type="button"
            className={`todo-check${todo.done ? ' done' : ''}`}
            aria-label={todo.done ? t('todos.markOpen') : t('todos.markDone')}
            onClick={() => toggle(todo)}
          >
            {todo.done && <IconCheck size={12} />}
          </button>
          <div className="todo-title">
            {editingThis ? (
              <TodoEditor
                value={editing.text}
                onChange={(text) => setEditing({ id: todo.id, text })}
                onSave={saveEdit}
                onCancel={() => setEditing(null)}
                onTab={(out) => tab(todo, out)}
                onDeleteEmpty={() => deleteEmpty(todo)}
              />
            ) : (
              <button
                type="button"
                className={`todo-text${todo.done ? ' done' : ''}`}
                title={t('todos.clickToEdit')}
                onClick={() => setEditing({ id: todo.id, text: todo.text })}
              >
                {todo.text}
              </button>
            )}
            {!api.demo && todo.owner === 'agent' && <span className="todo-owner" title={t('personal.agentOwned')}><IconAgent size={14} /></span>}
          </div>
          {!api.demo && <>
            <TodoMetadata todo={todo} />
            {todo.hasNote && <button className="todo-details-toggle" aria-label={t('personal.details')} aria-expanded={expanded.has(todo.id)} onClick={() => toggleDetails(todo.id)}><IconFile size={14} /></button>}
          </>}
          <div className="todo-actions">
            {!api.demo && !todo.done && !todo.threads.some(thread => thread.status === 'running') && <TodoDelegate api={api} personal={personal} threads={threads} request={{ id: todo.id }} busy={delegating} onDelegate={request => void delegate(request)} onLink={setLinkRequest} />}
            <button
              type="button"
              className="row-more"
              aria-label={t('todos.more', todo.text)}
              onClick={(e) => {
                const [x, y] = below(e);
                setMenu({ todo, x, y });
              }}
            >
              <IconMore size={15} />
            </button>
          </div>
        </div>
        {todo.threads.length > 0 && <div className="todo-chips">{todo.threads.map((t) => chip(todo, t))}</div>}
        {expanded.has(todo.id) && <TodoDetails api={api} todo={todo} onReload={onReload} onOpenThread={onOpenThread} />}
        {(todo.children.length > 0 || child?.parentId === todo.id) && (
          <div className="todo-children">
            {todo.children.map((c) => row(c, true))}
            {child?.parentId === todo.id && (
              <div className="todo-line todo-new">
                <span className="todo-check blank" aria-hidden="true" />
                <input
                  className="todo-add"
                  autoFocus
                  placeholder={t('todos.childPlaceholder')}
                  value={child.text}
                  onChange={(e) => setChild({ parentId: todo.id, text: e.target.value })}
                  onKeyDown={(e) => {
                    // Stays open for the next one: sub-items tend to come several at a time. Enter
                    // on an empty line, or Escape, is how it is put away. Not losing focus: the menu
                    // that opened it takes focus back as it closes, which would shut it at once.
                    if (isCommit(e)) {
                      if (child.text.trim()) void add(todo.id, child.text);
                      setChild(child.text.trim() ? { parentId: todo.id, text: '' } : null);
                    }
                    if (e.key === 'Escape') {
                      e.preventDefault();
                      setChild(null);
                    }
                  }}
                  aria-label={t('todos.newChild')}
                />
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  if (settingsOpen) return <AssistantSettings api={api} agents={agents} onClose={() => { setSettingsOpen(false); setPendingDelegate(null); setPendingSummary(false); }} onSave={value => { onPersonalChange(value); setSettingsOpen(false); if (pendingDelegate) void delegate(pendingDelegate, true); if (pendingSummary) void summarize(true); }} />;
  return (
    <div className={`todos-page${api.demo ? '' : ' personal-plan'}`} ref={pageRef}>
      <header className="todos-head">
        <h1 className="todos-title">{t('todos.title')}</h1>
        <p className="todos-hint">{t('todos.hint')}</p>
        {!api.demo && <button className="personal-summary" onClick={() => setSettingsOpen(true)} disabled={!personal}>
          {personal?.directory && personal.agent ? <><span>{t('personal.private')}</span><span className="personal-summary-directory">{personal.directoryIsDefault ? t('personal.defaultDirectory', personal.directoryDisplay ?? personal.directory) : personal.directoryDisplay ?? personal.directory} · </span><span>{agents.find(agent => agent.id === personal.agent)?.label ?? personal.agent}{personal.model && ` · ${personal.model}`}</span></> : t(personal ? 'personal.setup' : 'common.loading')}<IconSettings size={14} />
        </button>}
        {!api.demo && <div className="todo-summary-bar">
          <button type="button" className="todo-summary-run" disabled={!personal || summarizing} title={summary?.running ? undefined : t('todos.summaryHint')} onClick={() => void summarize()}>
            <IconSparkle size={14} />{summary?.running ? t('todos.summarizing') : t('todos.summarize')}
          </button>
          {!summary?.running && <Popover label={t('todos.summaryRangeLabel')} triggerLabel={t('todos.summaryRangeLabel')} triggerClassName="todo-summary-range" placement="below"
            trigger={<>{t('todos.summaryRange', summaryDays)}<IconChevron size={12} open /></>}>
            {close => <div role="menu">{SUMMARY_RANGES.map(days => <button key={days} type="button" className="pop-item compact" role="menuitemradio" aria-checked={days === summaryDays} onClick={() => { chooseSummaryDays(days); close(); }}>
              <span className="pop-item-label">{t('todos.summaryRange', days)}</span>
              {days === summaryDays && <span className="pop-item-check"><IconCheck size={16} /></span>}
            </button>)}</div>}
          </Popover>}
        </div>}
        {personalError && <p className="panel-error" role="alert">{personalError} <button className="ghost" onClick={onPersonalRetry}>{t('common.retry')}</button></p>}
      </header>
      {todos.some(todo => todo.proposals?.length) && <section className="todo-proposals" aria-label={t('personal.confirm')}><p>{t('personal.confirm')}</p>{todos.flatMap(todo => (todo.proposals ?? []).map(proposal => <div key={proposal.id} className="todo-proposal">
        <small>{t(proposal.kind === 'done' ? 'personal.proposeDone' : 'personal.proposeAdd')}</small><div className="todo-proposal-body"><span>{todo.text}</span><small>{proposal.evidence || t('personal.source', todo.threads.find(thread => thread.id === proposal.threadId)?.title || t('sidebar.threads'))}</small></div>
        <div className="todo-proposal-actions"><button className="primary" onClick={() => void act(() => api.resolveTodoProposal(todo.id, proposal.id, 'accept', todo.rev))}>{t(proposal.kind === 'done' ? 'personal.acceptDone' : 'personal.acceptAdd')}</button><button className="ghost" onClick={() => void act(() => api.resolveTodoProposal(todo.id, proposal.id, 'dismiss', todo.rev))}>{t('personal.dismiss')}</button></div>
      </div>))}</section>}

      <div className="todos-list">
        {board.open.map((n) => row(n, false))}
        {/* The next blank line of the document: what is typed here becomes the last plan. */}
        <div className="todo-line todo-new">
          <span className="todo-check blank" aria-hidden="true" />
          <input
            className="todo-add"
            placeholder={empty ? t('todos.firstPlaceholder') : t('todos.addPlaceholder')}
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            onKeyDown={(e) => {
              if (isCommit(e) && adding.trim()) {
                void add(null, adding);
                setAdding('');
              }
            }}
            aria-label={t('todos.new')}
          />
          {!api.demo && adding.trim() && <TodoDelegate api={api} personal={personal} threads={threads} request={{ text: adding.trim() }} draft busy={delegating} onDelegate={request => void delegate(request)} onLink={setLinkRequest} />}
        </div>
      </div>

      {!api.demo && <Wishes api={api} count={wishCount} threads={threads} onOpenThread={onOpenThread} />}
      {wishError && <p className="panel-error" role="alert">{wishError} <button className="ghost" onClick={loadWishes}>{t('common.retry')}</button></p>}
      {linkRequest && <TodoThreadPicker todos={todos} threads={threads} todoId={linkRequest.id} busy={delegating} onClose={() => setLinkRequest(null)} onPick={threadId => {
        if (delegating) return;
        setDelegating(true);
        void (async () => {
          let id = linkRequest.id;
          if (!id) { id = (await api.createTodo(linkRequest.text!)).id; setAdding(''); setLinkRequest({ id }); onReload(); }
          await api.linkTodo(id, threadId); setLinkRequest(null); onReload();
        })().catch(onError).finally(() => setDelegating(false));
      }} />}

      {board.done.length > 0 && (
        <div className="todos-done-group">
          <button type="button" className="todos-done-head" aria-expanded={showDone} onClick={toggleDone}>
            <IconChevron size={13} open={showDone} />
            {t('todos.done', board.done.length)}
          </button>
          {showDone && board.done.map((n) => row(n, false))}
        </div>
      )}

      {drop && <div className="todo-drop" style={{ top: drop.top, left: drop.left, width: drop.width }} aria-hidden="true" />}

      {menu && (
        <Menu
          label={t('todos.actions')}
          x={menu.x}
          y={menu.y}
          items={menuEntries(menu.todo)}
          onClose={() => setMenu(null)}
        />
      )}
      {deleting && (
        <Dialog
          title={t('todos.deleteTitle')}
          message={deleting.parentId ? t('todos.deleteChild') : t('todos.deletePlan')}
          confirmLabel={t('common.delete')}
          danger
          onConfirm={() => {
            setDeleting(null);
            void act(() => api.deleteTodo(deleting.id));
          }}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
