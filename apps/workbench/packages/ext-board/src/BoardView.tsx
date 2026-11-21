import { useEffect, useId, useMemo, useRef, useState, type DragEvent, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react';
import type { WorkProjection } from '@calli/extension-api';
import type { ExtensionViewProps, WebHost } from '@calli/extension-api/web';
import { BACKLOG_OPEN_KEY, parseBacklogOpen, buildColumns, COLUMN_IDS, FILTERS_VERSION, GONE_PROJECT, machineOptions, markAllDone, emptyFilters, firstColumn, newThreadOptions, parseFilters, projectKey, projectLabels, projectOptions, showStaleWarning, staleMachines, cardFor, type BoardCard, type BoardFilters, type ColumnId } from './columns';
import { buildSummary } from './assist';
import { applyDrop, createLongPress, dropAction, withGuesses, type DropAction, type Guess } from './drag';
import { machineName, reasonOf } from './reason';
import { parsePins, PINS_KEY, pillFilter, pillSelected, pinCount, togglePin } from './pins';
import { t } from './i18n';
import { collectTags, parseTitleTags } from './tags';

const FILTERS_KEY = 'filters.v1';

function readFilters(host: WebHost): BoardFilters {
  try {
    return parseFilters(host.storage.get(FILTERS_KEY));
  } catch {
    return emptyFilters();
  }
}

function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

interface ProjectChoice {
  /** The project's name as the board shows it. */
  label: (key: string) => string;
  /** The project the board is narrowed to, if exactly one. */
  only: string | null;
  /** Narrows the board to this project, or widens it again when it already is. */
  pick: (key: string) => void;
}

/** A name's width in `ch`: wide (CJK) characters count twice. */
function textWidth(text: string): number {
  return [...text].reduce((sum, char) => sum + ((char.codePointAt(0) ?? 0) > 0x2e7f ? 2 : 1), 0);
}

interface CardDrag {
  dragging: boolean;
  onDragStart: (event: DragEvent) => void;
  onDragEnd: (event: DragEvent) => void;
}

function ThreadCard({ card, host, now, work, tagLabels, projects, drag }: { card: BoardCard; host: WebHost; now: number; work: WorkProjection[]; tagLabels: Map<string, string>; projects: ProjectChoice; drag: CardDrag | null }) {
  const locale = host.locale();
  const { AgentMark, Icon, RelativeTime } = host.ui;
  const { thread } = card;
  const reason = reasonOf(card, locale, now);
  const [saving, setSaving] = useState(false);
  const done = thread.doneAt !== null;
  const parsed = parseTitleTags(thread.title);
  const title = parsed.title || t(locale, 'untitled');
  const tags = parsed.tags.map(tag => tagLabels.get(tag.toLowerCase()) ?? tag);
  const note = card.reason.kind === 'report' ? thread.report?.note.trim() : '';
  const project = projectKey(thread);
  const projectLabel = projects.label(project);
  const narrowed = projects.only === project;
  const badges = [
    ...(thread.scheduled ? [{ key: 'scheduled', text: t(locale, 'scheduled'), icon: 'clock' as const }] : []),
    ...(thread.worktree ? [{ key: 'worktree', text: t(locale, 'worktree'), icon: 'worktree' as const }] : []),
    ...(thread.handover ? [{ key: 'handover', text: t(locale, 'handover'), icon: 'fork' as const }] : []),
    ...(thread.teamSize > 0 ? [{ key: 'team', text: t(locale, 'team', thread.teamSize), icon: 'team' as const }] : []),
  ];
  const openMenu = (x: number, y: number) => host.navigate.threadMenu(thread.id, { x, y });
  // A long press opens the menu; the click that follows the lift must not also open the thread.
  const press = useRef<ReturnType<typeof createLongPress>>();
  press.current ??= createLongPress((x, y) => openMenu(x, y));
  useEffect(() => () => press.current?.cancel(), []);
  const touch = (event: PointerEvent) => event.pointerType === 'touch' || event.pointerType === 'pen';
  const setDone = () => {
    if (saving) return;
    setSaving(true);
    host.threads.setDone(thread.id, !done).catch(() => undefined).finally(() => setSaving(false));
  };

  return (
    <article
      className={`ext-board-card-wrap${done ? ' is-done' : ''}${work.length ? ' has-work' : ''}${drag?.dragging ? ' is-dragging' : ''}`}
      draggable={drag !== null}
      onDragStart={drag?.onDragStart}
      onDragEnd={drag?.onDragEnd}
      onContextMenu={(event: MouseEvent) => {
        event.preventDefault();
        // A touch long press raises this event too: it is the same menu, opened once.
        if (press.current?.pending) press.current.trigger();
        else if (!press.current?.firing) openMenu(event.clientX, event.clientY);
      }}
      onPointerDown={(event) => { if (touch(event)) press.current?.start(event.clientX, event.clientY); }}
      onPointerMove={(event) => { if (touch(event)) press.current?.move(event.clientX, event.clientY); }}
      onPointerUp={() => press.current?.release()}
      onPointerCancel={() => press.current?.release()}
      onClickCapture={(event) => { if (press.current?.consumeClick()) { event.preventDefault(); event.stopPropagation(); } }}
    >
      {/* The open button stretches over the whole card; the project name sits above it as a button of its own. */}
      <div className="ext-board-card">
        <button type="button" className="ext-board-open" onClick={() => host.narrow ? host.navigate.openThread(thread.id) : host.navigate.peekThread(thread.id)}>
          <span className="ext-board-card-title">{title}</span>
        </button>
        {tags.length > 0 && <span className="ext-board-tags">
          {tags.slice(0, 2).map(tag => <span className="ext-board-tag" key={tag} title={tag}>{tag}</span>)}
          {tags.length > 2 && <span className="ext-board-tag" title={tags.slice(2).join(', ')}>{t(locale, 'moreTags', tags.length - 2)}</span>}
        </span>}
        {note && <span className="ext-board-note">{note}</span>}
        <span className={`ext-board-reason ext-board-tone-${reason.tone}`}>
          {reason.tone !== 'idle' && <span className="ext-board-dot" aria-hidden="true" />}
          <span className="ext-board-reason-text">{reason.text}</span>
          {(badges.length > 0 || thread.pinned) && <span className="ext-board-badges">
            {thread.pinned && <span className="ext-board-badge" title={t(locale, 'pinned')} aria-label={t(locale, 'pinned')}><Icon name="pin" size={12} /></span>}
            {badges.map((badge) => <span className="ext-board-badge" key={badge.key} title={badge.text} aria-label={badge.text}><Icon name={badge.icon} size={12} /></span>)}
          </span>}
        </span>
        <span className="ext-board-card-footer">
          <span className="ext-board-context">
            {thread.machine && <span className="ext-board-machine" title={machineName(thread.machine, locale)}>{machineName(thread.machine, locale)}</span>}
            <button
              type="button"
              className={`ext-board-project${narrowed ? ' is-narrowed' : ''}`}
              title={`${thread.project || projectLabel} · ${narrowed ? t(locale, 'allProjects') : t(locale, 'onlyProject', projectLabel)}`}
              aria-label={narrowed ? t(locale, 'allProjects') : t(locale, 'onlyProject', projectLabel)}
              aria-pressed={narrowed}
              style={{ minWidth: `min(${Math.min(textWidth(projectLabel), 10)}ch, 100%)` }}
              onClick={() => projects.pick(project)}
            >{projectLabel}</button>
            <span aria-hidden="true">·</span>
            <span className="ext-board-agent"><AgentMark agentId={thread.agent} size={12} label /></span>
          </span>
          {reason.at && <span className="ext-board-time"><RelativeTime iso={reason.at} /></span>}
        </span>
      </div>
      <button
        type="button"
        className="ext-board-check"
        role="checkbox"
        aria-checked={done}
        aria-label={`${t(locale, done ? 'markUndone' : 'markDone')}: ${title}`}
        title={t(locale, done ? 'markUndone' : 'markDone')}
        disabled={saving}
        onClick={setDone}
      >{done && <Icon name="check" size={12} />}</button>
      {work.length > 0 && <host.ui.WorkSummary items={work} />}
    </article>
  );
}

/** Conversations placed by what they are doing and what their agents report; the person marks them done. */
export function BoardView({ host }: ExtensionViewProps) {
  const locale = host.locale();
  const { Popover, PopoverOption, Icon, RelativeTime, EmptyState, Confirm } = host.ui;
  const [work, setWork] = useState(() => host.workSources.snapshot());
  const [hostThreads, setThreads] = useState(() => host.threads.snapshot());
  // Drops show at once; each stays until the host's own state for that thread moves, or the drop fails.
  const [guesses, setGuesses] = useState<ReadonlyMap<string, Guess>>(new Map());
  const threads = useMemo(() => withGuesses(hostThreads, guesses), [hostThreads, guesses]);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overColumn, setOverColumn] = useState<ColumnId | null>(null);
  const lastOver = useRef<ColumnId | null>(null);
  const [notice, setNotice] = useState<{ text: string; key: number } | null>(null);
  const [asking, setAsking] = useState(false);
  const [connection, setConnection] = useState(() => ({ state: host.connection.state(), observedAt: host.connection.observedAt() }));
  const [now, setNow] = useState(Date.now);
  const [filters, setFilters] = useState(() => readFilters(host));
  const [pins, setPins] = useState(() => { try { return parsePins(host.storage.get(PINS_KEY)); } catch { return []; } });
  const [backlogOpen, setBacklogOpen] = useState(() => { try { return parseBacklogOpen(host.storage.get(BACKLOG_OPEN_KEY)); } catch { return false; } });
  const [expanded, setExpanded] = useState<ReadonlySet<ColumnId>>(new Set());
  const [selectedColumn, setSelectedColumn] = useState<ColumnId | null>(null);
  // Old threads waiting to be checked, cleared in one go: asked first, then counted up in place.
  const [confirming, setConfirming] = useState<string[] | null>(null);
  const [marking, setMarking] = useState<{ done: number; total: number } | null>(null);
  const [failed, setFailed] = useState(0);
  const markOlderDone = (ids: string[]) => {
    setConfirming(null);
    setFailed(0);
    setMarking({ done: 0, total: ids.length });
    void markAllDone(ids, (id) => host.threads.setDone(id, true), (done) => setMarking({ done, total: ids.length }))
      .then((count) => { setFailed(count); setMarking(null); });
  };
  const viewId = useId();

  // A small pill in a fixed place, so what it says never moves the board.
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 4500);
    return () => clearTimeout(timer);
  }, [notice]);
  const say = (text: string) => setNotice({ text, key: Date.now() });

  useEffect(() => {
    const refreshConnection = () => setConnection({ state: host.connection.state(), observedAt: host.connection.observedAt() });
    const offThreads = host.threads.subscribe((next) => {
      setThreads(next);
      refreshConnection();
    });
    const offWork = host.workSources.subscribe(setWork);
    const offConnection = host.connection.subscribe(refreshConnection);
    // Subscribe first, then read the synchronous current snapshots to close the mount gap.
    setThreads(host.threads.snapshot());
    setWork(host.workSources.snapshot());
    refreshConnection();
    return () => { offThreads(); offWork(); offConnection(); };
  }, [host.threads, host.workSources, host.connection]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  const updatePins = (next: string[]) => {
    setPins(next);
    try {
      host.storage.set(PINS_KEY, JSON.stringify(next));
    } catch {
      // Pins stay for this visit when device storage is unavailable.
    }
  };
  const updateBacklogOpen = (open: boolean) => {
    setBacklogOpen(open);
    try {
      host.storage.set(BACKLOG_OPEN_KEY, JSON.stringify(open));
    } catch {
      // It stays this way for this visit when device storage is unavailable.
    }
  };
  const updateFilters = (next: BoardFilters) => {
    setFilters(next);
    try {
      host.storage.set(FILTERS_KEY, JSON.stringify({ ...next, v: FILTERS_VERSION }));
    } catch {
      // The view remains usable when device storage is unavailable.
    }
  };

  const machines = host.machines();
  const columns = buildColumns(threads, filters, now, expanded);
  const cardOf = (id: string | null) => {
    const thread = id ? threads.find((item) => item.id === id) : undefined;
    return thread ? cardFor(thread) : null;
  };
  const drop = (id: string, target: ColumnId) => {
    const thread = hostThreads.find((item) => item.id === id);
    const card = cardOf(id);
    if (!thread || !card) return;
    const action: DropAction = dropAction(card, target);
    if (action.kind === 'reject') return say(t(locale, action.reason === 'running' ? 'dropRunning' : 'dropNeedsYou'));
    if (action.kind === 'none') return;
    setGuesses((value) => new Map(value).set(id, { base: thread, thread: applyDrop(thread, action, Date.now()) }));
    const request = action.kind === 'setDone' ? host.threads.setDone(id, true) : host.threads.setStage(id, action.stage);
    request.catch(() => {
      setGuesses((value) => { const next = new Map(value); next.delete(id); return next; });
      say(t(locale, 'dropFailed', parseTitleTags(thread.title).title || t(locale, 'untitled')));
    });
  };
  const rejection = (target: ColumnId) => {
    const card = cardOf(dragId);
    const action = card ? dropAction(card, target) : null;
    return action?.kind === 'reject' ? action : null;
  };
  const askAssistant = () => {
    const assistant = host.assistant;
    if (!assistant || asking) return;
    const everything = buildColumns(threads, filters, now, new Set(COLUMN_IDS));
    const text = buildSummary(everything, {
      locale,
      now,
      threadLink: assistant.threadLink,
      projectLabel: projects.label,
      projectOf: (card) => projectKey(card.thread),
    });
    setAsking(true);
    assistant.ask(text)
      .catch((error: unknown) => say(t(locale, 'askFailed', error instanceof Error ? error.message : String(error))))
      .finally(() => setAsking(false));
  };
  const current = selectedColumn ?? firstColumn(columns);
  const empty = columns.every((column) => column.total === 0);
  const showMachines = machines.length > 1 || threads.some((thread) => thread.machine);
  const hasFilters = filters.tags.length + filters.project.length + filters.agent.length + filters.scheduled.length + (showMachines ? filters.machine.length : 0) > 0;
  const tags = collectTags(threads);
  const tagLabels = new Map(tags.map(tag => [tag.key, tag.label]));
  const tagCounts = new Map(tags.map(tag => [tag.key, tag.count]));
  const projectChoices = projectOptions(threads, filters.project);
  // Pinned projects with no thread stay listed, so they can be unpinned.
  const pinChoices = projectOptions(threads, [...filters.project, ...pins]);
  const projectCounts = new Map(pinChoices.map((option) => [option.key, option.count]));
  const projectNames = projectLabels(pinChoices.map((option) => option.key));
  const projects: ProjectChoice = {
    label: (key) => key === GONE_PROJECT ? t(locale, 'projectGone') : key ? projectNames.get(key) ?? key : t(locale, 'noProject'),
    only: filters.project.length === 1 ? filters.project[0] : null,
    pick: (key) => updateFilters({ ...filters, project: filters.project.length === 1 && filters.project[0] === key ? [] : [key] }),
  };
  const machineChoices = machineOptions(threads, machines, filters.machine);
  const machineInfo = new Map(machineChoices.map((option) => [option.key, option]));
  const stale = staleMachines(machines);
  const perMachine = machines.length > 1;
  const agentLabels = new Map(host.agents().map((agent) => [agent.id, agent.label]));
  const agents = [...new Set([...threads.map((thread) => thread.agent), ...filters.agent])].sort();

  const filter = <T extends string | boolean,>(
    key: keyof BoardFilters,
    label: string,
    selected: T[],
    options: T[],
    name: (value: T) => string,
    select: (values: T[]) => void,
    count?: (value: T) => string | number,
  ) => <Popover
    key={key}
    placement="below"
    align="end"
    label={label}
    triggerClassName={`ext-board-filter${selected.length ? ' ext-board-filter-active' : ''}`}
    triggerTitle={selected.length ? `${label} · ${t(locale, 'selected', selected.length)}` : label}
    trigger={<><span>{label}</span>{selected.length > 0 && <span className="ext-board-count">{selected.length}</span>}<Icon name="chevron" size={12} /></>}
  >
    {() => <>
      <PopoverOption multiple selected={selected.length === 0} onSelect={() => select([])}>{t(locale, 'all')}</PopoverOption>
      {options.map((option) => <PopoverOption multiple key={String(option)} selected={selected.includes(option)} onSelect={() => select(toggle(selected, option))}>
        <span className="ext-board-option-row"><span className="ext-board-option" title={name(option)}>{name(option)}</span>{count && <span className="ext-board-option-count">{count(option)}</span>}</span>
      </PopoverOption>)}
    </>}
  </Popover>;

  const moveTab = (event: KeyboardEvent<HTMLButtonElement>, id: ColumnId) => {
    const index = COLUMN_IDS.indexOf(id);
    const next = event.key === 'ArrowRight' ? (index + 1) % COLUMN_IDS.length
      : event.key === 'ArrowLeft' ? (index + COLUMN_IDS.length - 1) % COLUMN_IDS.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? COLUMN_IDS.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    setSelectedColumn(COLUMN_IDS[next]);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };

  return (
    <div className={`ext-board-page${host.narrow ? ' ext-board-narrow' : ''}`}>
      <header className="ext-board-head">
        <div className="ext-board-heading">
          <h1 className="ext-board-title">{t(locale, 'title')}</h1>
          {perMachine ? stale.slice(0, 2).map((machine) => <span className="ext-board-stale" role="status" key={machine.id ?? ':local'}
            title={machine.observedAt ? `${t(locale, 'lastSynced')} · ${machine.observedAt}` : t(locale, 'neverSynced')}
          >
            {t(locale, 'machineStale', machine.label)} · {machine.observedAt ? <RelativeTime iso={machine.observedAt} /> : t(locale, 'neverSynced')}
          </span>) : showStaleWarning(connection.state, connection.observedAt) && <span className="ext-board-stale" role="status"
            title={connection.observedAt ? `${t(locale, 'lastSynced')} · ${connection.observedAt}` : t(locale, 'neverSynced')}
          >
            {t(locale, 'stale')} · {connection.observedAt
              ? <RelativeTime iso={connection.observedAt} />
              : t(locale, 'neverSynced')}
          </span>}
          {perMachine && stale.length > 2 && <span className="ext-board-stale" title={stale.slice(2).map((machine) => machine.label).join(', ')}>{t(locale, 'moreMachines', stale.length - 2)}</span>}
        </div>
        <div className="ext-board-filters">
          <button type="button" className="ext-board-action" title={t(locale, 'newThread')} aria-label={t(locale, 'newThread')} onClick={() => host.navigate.newThread(newThreadOptions(filters))}>
            <Icon name="plus" size={14} /><span>{t(locale, 'newThread')}</span>
          </button>
          {host.assistant && <button type="button" className={`ext-board-action${asking ? ' is-busy' : ''}`} title={t(locale, asking ? 'askingAssistant' : 'askAssistant')} aria-label={t(locale, asking ? 'askingAssistant' : 'askAssistant')} disabled={asking || empty} aria-busy={asking} onClick={askAssistant}>
            <Icon name="sparkle" size={14} />
            {/* Both labels share the cell, so the button keeps its width while it waits. */}
            <span className="ext-board-swap">
              <span aria-hidden={asking} style={{ visibility: asking ? 'hidden' : undefined }}>{t(locale, 'askAssistant')}</span>
              {asking && <span role="status">{t(locale, 'askingAssistant')}</span>}
            </span>
          </button>}
          {threads.length > 0 && <>
          {tags.length > 0 && filter('tags', t(locale, 'tags'), filters.tags, [...new Set([...tags.map(tag => tag.key), ...filters.tags])], tag => tagLabels.get(tag) ?? tag, tags => updateFilters({ ...filters, tags }), tag => tagCounts.get(tag) ?? 0)}
          {filter('project', t(locale, 'project'), filters.project, projectChoices.map((option) => option.key), projects.label, (project) => updateFilters({ ...filters, project }), (key) => projectCounts.get(key) ?? 0)}
          {showMachines && filter('machine', t(locale, 'machine'), filters.machine, machineChoices.map((option) => option.key), (key) => machineInfo.get(key)?.label || t(locale, 'localMachine'), (machine) => updateFilters({ ...filters, machine }), (key) => machineInfo.get(key)?.count ?? t(locale, 'countUnknown'))}
          {filter('agent', t(locale, 'agent'), filters.agent, agents, (agent) => agentLabels.get(agent) ?? agent, (agent) => updateFilters({ ...filters, agent }))}
          {filter('scheduled', t(locale, 'scheduledFilter'), filters.scheduled, [true, false], (scheduled) => t(locale, scheduled ? 'scheduled' : 'notScheduled'), (scheduled) => updateFilters({ ...filters, scheduled }))}
          {hasFilters && <button type="button" className="ext-board-clear" onClick={() => updateFilters(emptyFilters())}>{t(locale, 'clearFilters')}</button>}
          </>}
        </div>
      </header>
      <div className="ext-board-pins" role="toolbar" aria-label={t(locale, 'pinsBar')}>
        <button type="button" className="ext-board-pin" aria-pressed={pillSelected(filters.project, null)} onClick={() => updateFilters({ ...filters, project: pillFilter(filters.project, null) })}>{t(locale, 'all')}</button>
        {pins.map((key) => {
          const count = pinCount(projectCounts.get(key), machines);
          return <button type="button" className="ext-board-pin" key={key} title={key === '' || key === GONE_PROJECT ? undefined : key}
            aria-pressed={pillSelected(filters.project, key)} onClick={() => updateFilters({ ...filters, project: pillFilter(filters.project, key) })}>
            <span className="ext-board-pin-name">{projects.label(key)}</span>
            <span className="ext-board-count">{count ?? '…'}</span>
          </button>;
        })}
        <Popover
          placement="below"
          align="start"
          label={t(locale, 'pinProject')}
          triggerClassName="ext-board-pin ext-board-pin-add"
          trigger={<><Icon name="pin" size={13} /><span>{t(locale, 'pinProject')}</span></>}
        >
          {() => pinChoices.map((option) => <PopoverOption multiple key={option.key} selected={pins.includes(option.key)} onSelect={() => updatePins(togglePin(pins, option.key))}>
            <span className="ext-board-option-row"><span className="ext-board-option" title={projects.label(option.key)}>{projects.label(option.key)}</span><span className="ext-board-option-count">{pinCount(option.count, machines) ?? '…'}</span></span>
          </PopoverOption>)}
        </Popover>
      </div>
      {host.narrow && !empty && <div className="ext-board-tabs" role="tablist" aria-label={t(locale, 'columns')}>
        {columns.map((column) => <button
          type="button"
          role="tab"
          className="ext-board-tab"
          key={column.id}
          id={`${viewId}-tab-${column.id}`}
          aria-controls={`${viewId}-panel-${column.id}`}
          aria-selected={current === column.id}
          tabIndex={current === column.id ? 0 : -1}
          onClick={() => setSelectedColumn(column.id)}
          onKeyDown={(event) => moveTab(event, column.id)}
        >{t(locale, column.id)} <span className="ext-board-count">{column.total}</span></button>)}
      </div>}
      {empty && <div className="ext-board-empty"><EmptyState text={t(locale, hasFilters ? 'filteredEmpty' : 'empty')} /></div>}
      {!empty && <div className={`ext-board-columns${!host.narrow && !backlogOpen ? ' has-rail' : ''}`}>
        {columns.map((column) => <section
          key={column.id}
          id={`${viewId}-panel-${column.id}`}
          className={`ext-board-column${!host.narrow && column.id === 'backlog' && !backlogOpen ? ' is-rail' : ''}${overColumn === column.id ? ' is-drop-target' : ''}${dragId && rejection(column.id) ? ' is-drop-refused' : ''}`}
          onDragOver={(event) => {
            if (!dragId) return;
            lastOver.current = column.id;
            if (rejection(column.id)) { event.dataTransfer.dropEffect = 'none'; event.preventDefault(); setOverColumn(null); return; }
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            if (overColumn !== column.id) setOverColumn(column.id);
          }}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOverColumn((value) => value === column.id ? null : value); }}
          onDrop={(event) => {
            event.preventDefault();
            const id = dragId;
            setOverColumn(null);
            setDragId(null);
            if (id) drop(id, column.id);
          }}
          hidden={host.narrow && current !== column.id}
          role={host.narrow ? 'tabpanel' : undefined}
          aria-labelledby={host.narrow ? `${viewId}-tab-${column.id}` : `${viewId}-heading-${column.id}`}
          tabIndex={host.narrow ? 0 : undefined}
        >
          {!host.narrow && column.id === 'backlog' && !backlogOpen && <button
            type="button"
            className="ext-board-rail"
            aria-expanded={false}
            aria-label={t(locale, 'expandBacklog', column.total)}
            onClick={() => updateBacklogOpen(true)}
          ><span className="ext-board-rail-name" id={`${viewId}-heading-${column.id}`}>{t(locale, column.id)}</span><span className="ext-board-count">{column.total}</span></button>}
          {!host.narrow && !(column.id === 'backlog' && !backlogOpen) && <h2 className="ext-board-column-title" id={`${viewId}-heading-${column.id}`}>
            <span className={`ext-board-dot ext-board-tone-${column.id}`} aria-hidden="true" />
            {t(locale, column.id)}<span className="ext-board-count">{column.total}</span>
            {column.id === 'backlog' && <button
              type="button"
              className="ext-board-collapse"
              aria-expanded={true}
              aria-label={t(locale, 'collapseBacklog')}
              title={t(locale, 'collapseBacklog')}
              onClick={() => updateBacklogOpen(false)}
            ><Icon name="chevron" size={14} /></button>}
          </h2>}
          <div className="ext-board-cards">
            {column.cards.map((card) => <ThreadCard
              key={card.thread.id}
              card={card}
              host={host}
              now={now}
              tagLabels={tagLabels}
              projects={projects}
              drag={host.narrow ? null : {
                dragging: dragId === card.thread.id,
                onDragStart: (event) => {
                  event.dataTransfer.setData('text/plain', card.thread.id);
                  event.dataTransfer.effectAllowed = 'move';
                  lastOver.current = null;
                  setDragId(card.thread.id);
                },
                onDragEnd: (event) => {
                  // Dropped where the card cannot go: the drop never fires, so say why here.
                  const refused = lastOver.current ? rejection(lastOver.current) : null;
                  if (refused && event.dataTransfer.dropEffect === 'none') say(t(locale, refused.reason === 'running' ? 'dropRunning' : 'dropNeedsYou'));
                  setDragId(null);
                  setOverColumn(null);
                },
              }}
              work={work.filter((item) => item.binding.threadIds.includes(card.thread.id))}
            />)}
            {column.total === 0 && <p className="ext-board-column-empty">{t(locale, 'emptyColumn')}</p>}
            {column.olderCount > 0 && <div className="ext-board-older-row">
              <button
                type="button"
                className="ext-board-older"
                aria-expanded={expanded.has(column.id)}
                onClick={() => setExpanded((value) => { const next = new Set(value); if (!next.delete(column.id)) next.add(column.id); return next; })}
              >{expanded.has(column.id) ? t(locale, 'hideOlder') : t(locale, column.id === 'done' ? 'olderDone' : 'olderReview', column.olderCount)}</button>
              {column.id === 'review' && <button
                type="button"
                className="ext-board-older ext-board-older-action"
                disabled={marking !== null}
                aria-live="polite"
                onClick={() => setConfirming(column.older.map((card) => card.thread.id))}
              >{marking ? t(locale, 'markingDone', marking.done, marking.total) : t(locale, 'markOlderDone')}</button>}
            </div>}
            {column.id === 'review' && failed > 0 && !marking && <p className="ext-board-bulk-error" role="alert">{t(locale, 'markFailed', failed)}</p>}
          </div>
        </section>)}
      </div>}
      <div className="ext-board-notice" role="status" aria-live="polite">{notice && <span key={notice.key} className="ext-board-notice-pill">{notice.text}</span>}</div>
      {confirming && <Confirm
        title={t(locale, 'markOlderDoneTitle', confirming.length)}
        message={t(locale, 'markOlderDoneMessage')}
        confirmLabel={t(locale, 'markOlderDoneConfirm')}
        onConfirm={() => markOlderDone(confirming)}
        onCancel={() => setConfirming(null)}
      />}
    </div>
  );
}
