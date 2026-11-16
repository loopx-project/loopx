import brandIconLight from '../assets/loo-icon-paper.svg';
import brandIconDark from '../assets/loo-icon-dark.svg';
import { Fragment, useEffect, useState, type MouseEvent, type ReactNode } from 'react';
import type { LoopxGoal } from '../loopx/status';
import { SidebarGoals } from './SidebarGoals';

const EMPTY_SET: Set<string> = new Set();
import type { ExternalSession, HostInfo, SearchHit, Section, Thread } from '../api';
import type { AgentMarkInfo } from '../thread-cache';
import { clockLabel } from '../timeline';
import { useInTitlebar, useLongPress, useStoredFlag, useStoredSet } from '../hooks';
import { attentionCount, threadMark, waitingLabel, type ThreadMark } from '../notices';
import { folderName } from '../paths';
import { buildActivity, buildMerged, buildSidebar, externalThreadRow, handoverGroups, markTerms, navOverflow, projectDir, SEARCH_MIN, taggedAgents, withExternalSessions, type HostSource } from '../sidebar';
import { timeAgo } from '../sessions';
import { deliveredPreview } from '../timeline';
import { HostSwitcher } from './Hosts';
import { Menu } from './Menu';
import type { HostIconName } from '@calli/extension-api';
import { HostIcon } from '../extensions/ui';
import { AgentMark, IconBell, IconChevron, IconClock, IconClose, IconDesktop, IconEdit, IconFolder, IconList, IconMore, IconPin, IconPlus, IconSearch, IconSettings, IconSidebar, IconSparkle, IconServer } from './icons';
import { t } from '../i18n';

export { folderName } from '../paths';

/** A sidebar row an extension view pinned: its name, its icon, and whether it is on screen. */
export interface ExtensionNavEntry {
  key: string;
  label: string;
  icon: HostIconName;
  active: boolean;
  onOpen: () => void;
}

/** A page the nav lists behind its "more" button: what it is called, and a state worth a glance, like a desktop still connected while minimized. */
interface NavPage {
  key: string;
  label: string;
  icon: (p: { size?: number }) => JSX.Element;
  active: boolean;
  /** A state to keep visible at a glance; such a page stays a row instead of hiding behind the button. */
  live?: boolean;
  /** What the live state says, shown on the dot and on hover. */
  liveLabel?: string;
  onOpen: () => void;
}

interface Props {
  personalDirectory?: string | null;
  /** Keeps a standalone walkthrough's preferences separate from the workspace. */
  storagePrefix?: string;
  showEmptySections?: boolean;
  /** Puts the list away on a wide screen; the top bar then has the button to bring it back. */
  onHide?: () => void;
  threads: Thread[];
  /** Each lead thread's team members, by the lead's id: rows under the lead instead of threads of their own. */
  members?: Map<string, Thread[]>;
  sections: Section[];
  /** For the mark each row shows in place of its agent's name; null while not known, so no name stands in. */
  agents: AgentMarkInfo[] | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  /** Starts a new thread with a directory or section pre-filled, from a group's "+". */
  onNewIn: (opts: { cwd?: string; sectionId?: string; scratch?: boolean }) => void;
  onThreadMenu: (thread: Thread, x: number, y: number) => void;
  onSectionMenu: (section: Section, x: number, y: number) => void;
  onNewSection: () => void;
  onOpenSteward?: () => void;
  stewardActive?: boolean;
  /** The LoopX core's goals, shown as a group under the nav; read-only here. */
  goals?: LoopxGoal[];
  needsYouGoals?: Set<string>;
  activeGoalId?: string | null;
  onOpenGoal?: (goalId: string) => void;
  /** Opens the machines page, listed behind the nav's "more" button unless it is on screen. */
  onOpenMachines?: () => void;
  machinesActive?: boolean;
  onOpenSchedules?: () => void;
  /** Opens the machine's desktop; absent when the machine shows none. */
  onOpenDesktop?: () => void;
  /** The desktop is minimized, still connected: its row says so. */
  desktopRunning?: boolean;
  /** The scheduled-task page is what the main area shows. */
  schedulesActive?: boolean;
  /** Entries enabled extensions pin to the sidebar: rows of their own after the plans link. */
  extensionNav?: ExtensionNavEntry[];
  /** The phone layout: the list is a page of its own under the conversation, laid out for thumbs. */
  compact?: boolean;
  /** Opens the settings menu, where the look, the archive, importing and signing out are. It grows up and to the left from the point given. */
  onOpenSettings: (x: number, y: number) => void;
  /** Other machines the hub shows, and the one this list is of (null: the hub's own). */
  hosts?: HostInfo[];
  hostId?: string | null;
  onHost?: (id: string | null) => void;
  /** The machine this list is of cannot be reached: its threads are over there, so there is no list. */
  hostDown?: boolean;
  /** No list has been read from this machine yet, live or remembered: empty means "not read", not "none". */
  loading?: boolean;
  /** Every machine's list, the one on screen among them: the sidebar shows them merged. Absent: just this machine's. */
  merged?: HostSource[] | null;
  /** Shows every machine's threads in one list; offered as a tab before the machines. */
  onAll?: () => void;
  /** Opens a thread of another machine, which means switching to that machine. */
  onOpenElsewhere?: (hostId: string | null, threadId: string) => void;
  /** Looks through what was said in every thread; without it the search is of titles and folders only. */
  onSearch?: (query: string) => Promise<SearchHit[]>;
  /** Opens the thread a found turn is in, at that turn. */
  onOpenHit?: (hit: SearchHit) => void;
  /** This machine's agent sessions that are not threads yet; they mix into the list by time. */
  external?: ExternalSession[];
  /** `off`: this machine does not list them (an older daemon, or the demo). */
  externalState?: 'off' | 'loading' | 'ready' | 'failed';
  /** Opens one of them: import it and continue it as a thread. */
  onImportExternal?: (session: ExternalSession) => void;
  /** `agent:sessionId` of the session being imported; its row says so. */
  importingExternal?: string | null;
  /** Something went wrong reading the list or importing one: shown once, with a way to try again. */
  externalError?: { message: string; retry?: () => void } | null;
  /** More external sessions are known but not shown; the call brings the next page. */
  moreExternal?: boolean;
  onLoadMoreExternal?: () => void;
  /** A line over the list, such as the demo's way to connect. */
  notice?: ReactNode;
}

/** In the merged list, the machine a row's thread is on when that is not the hub's own, and how to open it if it is not the one on screen. */
/** `agents`: that machine's, by id, when the row is another machine's; null while not known. */
interface Elsewhere { label?: string; offline?: string; open?: () => void; agents?: Map<string, AgentMarkInfo> | null }

/** Where a menu opened from a "more" button should appear: just under the button. */
function below(e: MouseEvent<HTMLElement>): [number, number] {
  const rect = e.currentTarget.getBoundingClientRect();
  return [rect.left, rect.bottom + 4];
}

/** A small "+" on a group header that starts a thread right in that group. */
function GroupAdd({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="group-add" aria-label={label} title={label} onClick={(e) => {
      e.stopPropagation();
      onClick();
    }}>
      <IconPlus size={14} />
    </button>
  );
}

/** The key that shows and hides the list, as Codex has it. */
export const SIDEBAR_SHORTCUT = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⇧⌘S' : 'Ctrl+Shift+S';

export function Sidebar({
  personalDirectory,
  storagePrefix = 'calli.sidebar',
  showEmptySections = true,
  threads,
  members,
  sections,
  agents,
  selectedId,
  onSelect,
  onNew,
  onNewIn,
  onThreadMenu,
  onSectionMenu,
  onNewSection,
  onOpenSteward,
  stewardActive = false,
  goals = [],
  needsYouGoals = EMPTY_SET,
  activeGoalId = null,
  onOpenGoal,
  onOpenMachines,
  machinesActive = false,
  onOpenSchedules,
  onOpenDesktop,
  desktopRunning = false,
  schedulesActive = false,
  extensionNav = [],
  compact = false,
  onOpenSettings,
  notice,
  onHide,
  hosts = [],
  hostId = null,
  onHost,
  hostDown = false,
  loading = false,
  merged = null,
  onAll,
  onOpenElsewhere,
  onSearch,
  onOpenHit,
  external = [],
  externalState = 'off',
  onImportExternal,
  importingExternal = null,
  externalError = null,
  moreExternal = false,
  onLoadMoreExternal,
}: Props) {
  const [search, setSearch] = useState('');
  // What was said, looked up a moment after the typing stops. `for` is the search the hits answer.
  const [found, setFound] = useState<{ for: string; hits: SearchHit[] } | null>(null);
  const query = search.trim();
  useEffect(() => {
    if (!onSearch || query.length < SEARCH_MIN) { setFound(null); return; }
    let stale = false;
    const timer = setTimeout(() => {
      onSearch(query).then((hits) => { if (!stale) setFound({ for: query, hits }); }).catch(() => { if (!stale) setFound({ for: query, hits: [] }); });
    }, 220);
    return () => { stale = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);
  const [expandedHandovers, setExpandedHandovers] = useState<Set<string>>(new Set());
  // A team is open on its own while a member works or waits, or is the one on screen; a click overrides that.
  const [teamOpen, setTeamOpen] = useState<Map<string, boolean>>(new Map());
  // The search field is put away behind a button until it is asked for.
  const [searching, setSearching] = useState(false);
  // Where the nav's "more" pages menu opened from; null when closed.
  const [pagesMenu, setPagesMenu] = useState<[number, number] | null>(null);
  const closeSearch = () => {
    setSearch('');
    setSearching(false);
  };
  // A long press on a row is the phone's right click.
  const longPress = useLongPress<Thread>(onThreadMenu);
  const inTitlebar = useInTitlebar() && !compact;
  const [collapsed, toggle] = useStoredSet(`${storagePrefix}.collapsed`);
  // The other way to read the list: by what is going on, not by where a thread is filed. Like Codex's activity view.
  const [byActivity, setByActivity] = useStoredFlag(`${storagePrefix}.activity`);
  // This machine's sessions that are not threads yet mix into the list by their time; every
  // machine's own list (the merged view) shows threads only.
  const withExternal = merged ? threads : withExternalSessions(threads, external);
  const model = buildSidebar(withExternal, sections, search);
  const mergedModel = merged ? buildMerged(merged, search, new Date(), selectedId) : null;
  const activity = byActivity && !mergedModel ? buildActivity(withExternal, search, new Date(), selectedId) : null;
  // Out of their project groups, rows say where they are and what was asked last.
  const flat = byActivity || Boolean(mergedModel);
  // The machine on screen being down hides its list, unless every machine is listed.
  const down = hostDown && !mergedModel;
  const wanted = attentionCount(threads, selectedId);
  // The bell's red dot is for what waits on the person: a session asking, a Goal needing a decision. Unread results only count in the title.
  const waitingOnYou = threads.filter((thread) => thread.waiting).length + needsYouGoals.size;
  // While searching, everything is expanded so matches are never hidden inside a closed group.
  const isOpen = (key: string) => Boolean(search.trim()) || !collapsed.has(key);

  const MARK_LABELS = { waiting: t('notices.unknown.label'), running: t('status.running'), error: t('status.error'), unread: t('sidebar.unread') } as const;
  const agentById = new Map((agents ?? []).map((a) => [a.id, a]));
  const tagged = taggedAgents(agents ?? []);

  /**
   * `chain`: the handover chain this row stands for, newest first, as `handoverGroups` gives it.
   * `on`: in the merged list, the other machine the thread is on (named on the row), and how to open
   * it there. The host routes menu actions to that same machine.
   */
  const row = (thread: Thread, title = thread.title, preview = thread.preview, chain?: Thread[], on?: Elsewhere) => {
    const open = on?.open;
    const mark = threadMark(thread, thread.id === selectedId);
    const offline = on?.offline ?? (!on && hostDown ? t('sidebar.hostDown') : undefined);
    // A row of another machine is marked by that machine's agents, not by the ones on screen.
    const theirs = on && on.agents !== undefined;
    const known = theirs ? on.agents !== null : agents !== null;
    const byId = theirs ? on.agents ?? new Map<string, AgentMarkInfo>() : agentById;
    const tags = theirs ? taggedAgents([...byId.values()]) : tagged;
    const agent = byId.get(thread.agent);
    // The agents the task passed through, first to last, and the one it came from.
    const passed = chain ? [...chain].reverse().map((m) => byId.get(m.agent)?.label ?? m.agent).join(' → ') : thread.handover ? `${byId.get(thread.handover.agent)?.label ?? thread.handover.agent} → ${agent?.label ?? thread.agent}` : null;
    const hint = [agent && tags.has(agent.id) ? agent.label : null, thread.worktree ? t('sidebar.inWorktree', thread.worktree.branch) : null, thread.forkedFrom ? t('sidebar.fork') : null, passed ? t('sidebar.handover', passed) : null, thread.schedule ? t('sidebar.scheduleRun', thread.schedule.name) : null].filter(Boolean).join(' · ');
    const from = chain ? byId.get(chain.find((m) => m.id === thread.handover?.sourceId)?.agent ?? thread.handover?.agent ?? '') : undefined;
    // Filed by project, the list is for scanning: a title per row, as in Codex. The latest ask
    // shows where it helps finding something, by activity and in a search. A thread that needs a
    // hand says so in every view.
    const detail = flat || query ? preview : null;
    const note = mark === 'waiting' ? waitingLabel(thread) : mark === 'error' ? t('sidebar.failed') : null;
    return (
    <div
      key={thread.id}
      className={`thread-row${thread.id === selectedId ? ' active' : ''}${mark === 'unread' || mark === 'waiting' ? ' attention' : ''}`}
      onContextMenu={(e) => {
        e.preventDefault();
        onThreadMenu(thread, e.clientX, e.clientY);
      }}
      {...longPress(thread)}
    >
      {/* Two accounts of one agent share a mark; which one runs the thread shows on hover and in the
          open thread's top bar, not as text on every row. */}
      <button className="thread-item" onClick={() => (open ? open() : onSelect(thread.id))} title={[on?.label && open ? t('sidebar.merged.openOn', on.label) : null, hint].filter(Boolean).join(' · ') || undefined}>
        <span className="thread-text">
          <span className="thread-head">
            <span className="thread-title">{title}</span>
            {/* Beside the title, so the row stays one line like the rest. */}
            {note && <span className={`thread-note ${mark}`}>{note}</span>}
            {/* Out of its project group, a thread says where it is. */}
            {flat && on?.label && <span className="thread-host" title={on.label}>{on.label}</span>}
            {flat && <span className="thread-dir">{folderName(projectDir(thread).cwd)}</span>}
          </span>
          {detail && <span className="thread-preview">{detail}</span>}
        </span>
        <span className="thread-meta">
          {/* A handover chain: the agent now on it in front, the one before it tucked behind. */}
          {agent?.icon && from?.icon ? (
            <span className="agent-stack" role="img" aria-label={agent.label} title={passed ?? undefined}>
              <AgentMark path={from.icon} size={compact ? 12 : 11} />
              <AgentMark path={agent.icon} size={compact ? 14 : 12} />
            </span>
          ) : agent?.icon ? (
            <span className="agent-mark" role="img" aria-label={agent.label} title={agent.label}>
              <AgentMark path={agent.icon} size={compact ? 13 : 12} />
            </span>
          ) : known ? (
            // An agent without a mark, or one this machine no longer has: its name, as before.
            <span className="agent-tag">{thread.agent}</span>
          ) : null}
          {offline ? <span className="dot offline" role="img" aria-label={offline} title={offline} /> : mark && <span className={`dot ${mark}`} role="img" aria-label={mark === 'waiting' ? waitingLabel(thread) : MARK_LABELS[mark]} title={mark === 'waiting' ? waitingLabel(thread) : MARK_LABELS[mark]} />}
        </span>
      </button>
      {(
        <button className="row-more" aria-label={t('sidebar.more', thread.title)} onClick={(e) => onThreadMenu(thread, ...below(e))}>
          <IconMore size={16} />
        </button>
      )}
    </div>
    );
  };

  const now = Date.now();
  /** Who in a team comes first: whoever is at work or waiting, then whoever was last heard from. */
  const busy = (thread: Thread) => (thread.waiting ? 2 : thread.status === 'running' ? 1 : 0);
  const teamOrder = (a: Thread, b: Thread) => busy(b) - busy(a) || b.updatedAt.localeCompare(a.updatedAt);

  // One run of overlapping agent marks, the frontmost carrying the most urgency; the words stay in
  // the button's label. Like Orca's crew stack. Null when any agent has no mark, so text takes over.
  const stackLine = (list: Thread[], dimQuiet = true) => {
    const chips = list.map((m) => ({ id: m.id, mark: agentById.get(m.agent)?.icon, quiet: busy(m) === 0 && !threadMark(m, m.id === selectedId) }));
    if (chips.some((c) => !c.mark)) return null;
    const shown = chips.slice(0, 3);
    return <span className="stack-line">
      {shown.map((c, i) => <span key={c.id} className={`stack-chip${dimQuiet && c.quiet ? ' dim' : ''}`} style={{ zIndex: shown.length - i }}><AgentMark path={c.mark as string} size={compact ? 14 : 13} /></span>)}
      {chips.length > shown.length && <span className="stack-more" aria-hidden="true">+{chips.length - shown.length}</span>}
    </span>;
  };
  // One dot stands for the whole run: waiting, then error, unread, running; none when all is quiet.
  const runDot = (list: Thread[]) => {
    const marks = [...new Set(list.map((m) => threadMark(m, m.id === selectedId)).filter(Boolean))] as ThreadMark[];
    const mark = (['waiting', 'error', 'unread', 'running'] as const).find((m) => marks.includes(m));
    return mark ? <span className={`dot ${mark}`} role="img" aria-label={MARK_LABELS[mark]} title={MARK_LABELS[mark]} /> : null;
  };

  /**
   * A conversation an agent on this machine already has, sitting among the threads by its time.
   * Quieter than a thread (nothing is running): reading it is free, opening it imports it, which
   * takes a moment and can fail — both said on the row, in place.
   */
  const externalRow = (thread: Thread) => {
    const session = thread.external!;
    const key = `${session.agent}:${session.sessionId}`;
    const busy = importingExternal === key;
    const agent = agentById.get(session.agent);
    return (
      <div key={thread.id} className="thread-row external">
        <button
          className="thread-item"
          disabled={importingExternal !== null}
          onClick={() => onImportExternal?.(session)}
          title={t('sidebar.externalHint')}
        >
          <span className="thread-text">
            <span className="thread-head">
              <span className="thread-title">{thread.title}</span>
              {busy && <span className="thread-note external">{t('sidebar.externalImporting')}</span>}
              {flat && <span className="thread-dir">{folderName(projectDir(thread).cwd)}</span>}
            </span>
          </span>
          <span className="thread-meta">
            {agent?.icon ? (
              <span className="agent-mark" role="img" aria-label={agent.label} title={agent.label}>
                <AgentMark path={agent.icon} size={compact ? 13 : 12} />
              </span>
            ) : agents !== null ? (
              <span className="agent-tag">{session.agent}</span>
            ) : null}
            {/* The one quiet mark that this is a conversation on disk, not a thread yet; the row's
                tooltip says it in words. */}
            <i className="external-dot" aria-hidden="true" />
          </span>
        </button>
      </div>
    );
  };

  // Like Orca's agents under a task: the lead's row, then one line per member saying what it is doing.
  const entry = (thread: Thread, on?: Elsewhere) => {
    if (thread.external) return externalRow(thread);
    const team = members?.get(thread.id);
    if (!team?.length || on?.open) return row(thread, thread.title, thread.preview, undefined, on);
    const sorted = [...team].sort(teamOrder);
    const auto = sorted.some((m) => busy(m) > 0 || m.id === selectedId);
    const open = Boolean(search.trim()) || (teamOpen.get(thread.id) ?? auto);
    const working = sorted.filter((m) => m.status === 'running').length;
    const waiting = sorted.filter((m) => m.waiting).length;
    const stack = stackLine(sorted);
    const current = thread.id === selectedId || sorted.some((m) => m.id === selectedId);
    return <div key={thread.id} className={`handover-group team-group${open ? ' open' : ''}${current ? ' selected' : ''}`}>
      {row(thread, thread.title, thread.preview, undefined, on)}
      <button className="handover-toggle" aria-expanded={open} onClick={() => setTeamOpen((old) => new Map(old).set(thread.id, !open))}
        aria-label={[t('sidebar.agents', sorted.length), waiting ? t('sidebar.waitingCount', waiting) : working ? t('team.running', working) : null, open ? t('sidebar.collapseTeam', thread.title) : t('sidebar.expandTeam', thread.title)].filter(Boolean).join(' · ')}>
        {stack ? <>
          {runDot(sorted)}
          {stack}
        </> : <>
          <span>{t('sidebar.agents', sorted.length)}</span>
          {(waiting || working) > 0 && <span className="team-note">{waiting ? t('sidebar.waitingCount', waiting) : t('team.running', working)}</span>}
        </>}
        <IconChevron size={13} open={open} />
      </button>
      {open && <div className="handover-members">{sorted.map((m) => {
        const agent = agentById.get(m.agent);
        const mark = threadMark(m, m.id === selectedId);
        return <button
          key={m.id}
          className={`handover-member${m.id === selectedId ? ' active' : ''}`}
          aria-current={m.id === selectedId ? 'true' : undefined}
          title={m.title}
          onClick={() => onSelect(m.id)}
          onContextMenu={(e) => { e.preventDefault(); onThreadMenu(m, e.clientX, e.clientY); }}
          {...longPress(m)}
        >
          <span className={`dot ${mark ?? 'idle'}`} role="img" aria-label={mark === 'waiting' ? waitingLabel(m) : mark ? MARK_LABELS[mark] : t('status.idle')} />
          {agent?.icon ? <span className="agent-mark" role="img" aria-label={agent.label}><AgentMark path={agent.icon} size={compact ? 14 : 13} /></span> : null}
          <span className="handover-member-name team-member-text">{deliveredPreview(m.coordination?.doing ?? '') || m.title}</span>
          <time className="handover-member-time" dateTime={m.updatedAt}>{timeAgo(m.updatedAt, now)}</time>
        </button>;
      })}</div>}
    </div>;
  };

  const groupedRows = (list: Thread[], keepOrder = false) => handoverGroups(list, keepOrder).map(({ key, latest, members }) => {
    if (members.length === 1) return entry(latest);
    const storageKey = `${hostId ?? 'local'}:${key}`;
    const expanded = Boolean(search.trim()) || expandedHandovers.has(storageKey);
    const marks = [...new Set(members.filter((thread) => thread.id !== latest.id).map((thread) => threadMark(thread, thread.id === selectedId)).filter(Boolean))] as ThreadMark[];
    const chainStack = stackLine(members, false);
    const chainDot = (['waiting', 'error', 'unread', 'running'] as const).find((m) => marks.includes(m));
    const current = members.some((thread) => thread.id === selectedId);
    // Like Orca's agents under a task: one card, the latest conversation on top, the chain folded below it.
    return <div key={key} className={`handover-group${expanded ? ' open' : ''}${current ? ' selected' : ''}`}>
      {row(latest, latest.title.replace(/(?:（接手）)+$/, ''), latest.preview, members)}
      <button className="handover-toggle" aria-expanded={expanded} onClick={() => setExpandedHandovers((old) => {
        const next = new Set(old);
        if (next.has(storageKey)) next.delete(storageKey); else next.add(storageKey);
        return next;
      })}
        aria-label={[t('sidebar.sessions', members.length), expanded ? t('sidebar.collapseHandover', latest.title) : t('sidebar.expandHandover', latest.title)].join(' · ')}>
        {chainStack ? <>
          {chainDot && <span className={`dot ${chainDot}`} role="img" aria-label={t('sidebar.otherHandover', MARK_LABELS[chainDot])} title={t('sidebar.otherHandover', MARK_LABELS[chainDot])} />}
          {chainStack}
        </> : <span>{t('sidebar.sessions', members.length)}</span>}
        <IconChevron size={13} open={expanded} />
      </button>
      {expanded && <div className="handover-members handover-chain">{members.map((thread) => {
        const agent = agentById.get(thread.agent);
        const mark = threadMark(thread, thread.id === selectedId);
        return <button
          key={thread.id}
          className={`handover-member${thread.id === selectedId ? ' active' : ''}`}
          aria-current={thread.id === selectedId ? 'true' : undefined}
          onClick={() => onSelect(thread.id)}
          onContextMenu={(e) => { e.preventDefault(); onThreadMenu(thread, e.clientX, e.clientY); }}
          {...longPress(thread)}
        >
          <span className={`dot ${mark ?? 'idle'}`} role="img" aria-label={mark === 'waiting' ? waitingLabel(thread) : mark ? MARK_LABELS[mark] : t('status.idle')} />
          {agent?.icon ? <span className="agent-mark"><AgentMark path={agent.icon} size={compact ? 14 : 13} /></span> : null}
          <span className="handover-member-name">{agent?.label ?? thread.agent}</span>
          <span className="handover-member-role">{thread.handover ? t('sidebar.successor') : t('sidebar.original')}</span>
          <time className="handover-member-time" dateTime={thread.updatedAt}>{timeAgo(thread.updatedAt, now)}</time>
        </button>;
      })}</div>}
    </div>;
  });

  const group = (key: string, head: React.ReactNode, list: Thread[], extra?: React.ReactNode, emptyHint?: string) => (
    <section key={key} className="thread-group">
      <div className="group-head-row">
        <button className="group-head" aria-expanded={isOpen(key)} onClick={() => toggle(key)}>
          <span className="group-chevron">
            <IconChevron size={13} open={isOpen(key)} />
          </span>
          {head}
        </button>
        {extra}
      </div>
      {isOpen(key) && (list.length ? groupedRows(list) : emptyHint ? <p className="group-empty">{emptyHint}</p> : null)}
    </section>
  );

  const empty = (
    <p className="group-empty top" role={loading ? 'status' : undefined}>
      {loading ? t('sidebar.loading') : search.trim() ? t('sidebar.noMatch') : compact ? t('sidebar.emptyPhone') : t('sidebar.empty')}
    </p>
  );

  const clock = (at: string | null) => {
    if (!at) return null;
    const d = new Date(at);
    const sameDay = d.toDateString() === new Date().toDateString();
    const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  };
  const labelOf = new Map((merged ?? []).map((s) => [s.hostId, s.label]));
  const offlineOf = new Map((merged ?? []).filter(s => s.state === 'offline').map(s => [s.hostId, t('sidebar.merged.offline', s.label, clock(s.at))]));
  const agentsOf = new Map((merged ?? []).map((s) => [s.hostId, s.agents ? new Map(s.agents.map((a) => [a.id, a])) : null]));
  const groups = mergedModel ? (
    <nav className="thread-groups activity merged" aria-label={t('sidebar.merged.label')}>
      {mergedModel.notes.filter(n => n.state !== 'offline').map((n) => (
        <p key={n.hostId ?? ''} className={`merged-note ${n.state}`} role={n.state === 'loading' ? 'status' : undefined}>
          {n.state === 'loading' ? t('sidebar.merged.loading', n.label) : t('sidebar.merged.failed', n.label, clock(n.at))}
        </p>
      ))}
      {mergedModel.count === 0 && !mergedModel.notes.some((n) => n.state === 'loading') && (offlineOf.size ? <p className="group-empty">{t('sidebar.hostDown')}</p> : empty)}
      {mergedModel.groups.map((g) => (
        <section key={g.key} className="thread-group" aria-label={g.label}>
          <div className="group-label">{g.label}</div>
          {g.items.map(({ thread, hostId: on }) => (
            <Fragment key={`${on ?? ''}:${thread.id}`}>
              {entry(thread, {
                // The hub's own machine is the usual one; only threads elsewhere are named.
                label: on === null ? undefined : labelOf.get(on),
                offline: offlineOf.get(on),
                open: on === hostId ? undefined : () => onOpenElsewhere?.(on, thread.id),
                agents: on === hostId ? undefined : agentsOf.get(on) ?? null,
              })}
            </Fragment>
          ))}
        </section>
      ))}
    </nav>
  ) : activity ? (
    <nav className="thread-groups activity" aria-label={t('sidebar.threadsByActivity')}>
      {activity.count === 0 && empty}
      {activity.groups.map((g) => (
        <section key={g.key} className="thread-group" aria-label={g.label}>
          <div className="group-label">{g.label}</div>
          {groupedRows(g.threads, true)}
        </section>
      ))}
    </nav>
  ) : (
    <nav className="thread-groups" aria-label={t('sidebar.threads')}>
      {model.count === 0 && empty}

      {model.pinned.length > 0 &&
        group(
          'pinned',
          <>
            <IconPin size={13} />
            <span className="group-name">{t('sidebar.pinned')}</span>
          </>,
          model.pinned,
        )}

      {(model.sections.length > 0 || (showEmptySections && !search.trim())) && (
        <div className="group-label-row">
          <span className="group-label">{t('sidebar.sections')}</span>
          <button className="group-add" aria-label={t('sidebar.newSection')} title={t('sidebar.newSection')} onClick={onNewSection}>
            <IconPlus size={14} />
          </button>
        </div>
      )}
      {model.sections.map(({ section, threads: list }) =>
        group(
          `section:${section.id}`,
          <span className="group-name">{section.name}</span>,
          list,
          <span className="group-head-actions">
            <GroupAdd label={t('sidebar.newInSection', section.name)} onClick={() => onNewIn({ sectionId: section.id })} />
            <button
              className="row-more"
              aria-label={t('sidebar.sectionMore', section.name)}
              onClick={(e) => onSectionMenu(section, ...below(e))}
            >
              <IconMore size={16} />
            </button>
          </span>,
          compact ? t('sidebar.sectionHintPhone') : t('sidebar.sectionHint'),
        ),
      )}
      {showEmptySections && model.sections.length === 0 && !search.trim() && <p className="group-empty">{t('sidebar.noSections')}</p>}

      {model.projects.length > 0 && <div className="group-label">{t('sidebar.projects')}</div>}
      {model.projects.map((p) =>
        group(
          `project:${p.key}`,
          <>
            <IconFolder size={13} />
            <span className="group-name" title={p.cwd}>
              {p.key === personalDirectory ? t('personal.private') : p.name}
            </span>
          </>,
          p.threads,
          <GroupAdd label={t('sidebar.newInProject', p.name)} onClick={() => onNewIn({ cwd: p.cwd })} />,
        ),
      )}
      {model.scratch.length > 0 &&
        group(
          'scratch',
          <span className="group-name">{t('projects.none')}</span>,
          model.scratch,
          <GroupAdd label={t('sidebar.newScratch')} onClick={() => onNewIn({ scratch: true })} />,
        )}
    </nav>
  );

  // The pages after "Plans" and the pinned views: behind a "more" button, except the one on screen or one with a state to keep an eye on.
  const pages: NavPage[] = [
    ...(onOpenMachines ? [{ key: 'machines', label: t('hosts.machines'), icon: IconServer, active: machinesActive, onOpen: onOpenMachines }] : []),
    ...(onOpenSchedules ? [{ key: 'schedules', label: t('sidebar.schedules'), icon: IconClock, active: schedulesActive, onOpen: onOpenSchedules }] : []),
    ...(onOpenDesktop ? [{ key: 'desktop', label: t('desktop.title'), icon: IconDesktop, active: false, live: desktopRunning, liveLabel: t('desktop.running'), onOpen: onOpenDesktop }] : []),
  ];
  const { shown: pagesShown, hidden: pagesHidden } = navOverflow(pages);

  const searchInput = (
    <input
      type="search"
      className="sidebar-search"
      placeholder={onSearch ? t('sidebar.searchAll') : t('sidebar.search')}
      value={search}
      onChange={(e) => setSearch(e.target.value)}
      aria-label={t('sidebar.search')}
      autoFocus
      onKeyDown={(e) => {
        if (e.key === 'Escape') closeSearch();
      }}
    />
  );

  const hideButton = onHide && (
    <button className={`${inTitlebar ? 'icon-btn' : 'round-btn'} sidebar-hide`} aria-label={t('sidebar.hide')} title={t('sidebar.hideKey', SIDEBAR_SHORTCUT)} onClick={onHide}>
      <IconSidebar size={16} />
    </button>
  );

  return (
    <div className={`sidebar-inner${compact ? ' compact' : ''}`}>
      {/* In the window's title bar, as in Codex: beside the system's buttons only the list's own
          toggle; the rest of the strip drags the window, and the name and its buttons sit below. */}
      {inTitlebar && <div className="sidebar-titlebar">{hideButton}</div>}
      <div className="sidebar-top">
        {searching ? (
          <label className="sidebar-search-box">
            <IconSearch size={compact ? 18 : 15} />
            {searchInput}
            <button type="button" className="search-close" aria-label={t('sidebar.closeSearch')} onClick={closeSearch}>
              <IconClose size={compact ? 16 : 14} />
            </button>
          </label>
        ) : (
          <>
            <span className="brand"><img className="brand-icon loo-light" src={brandIconLight} alt="" width="32" height="32" />
              <img className="brand-icon loo-dark" src={brandIconDark} alt="" width="32" height="32" /><span>{t('app.name')}</span></span>
            <span className="sidebar-top-actions">
              {!inTitlebar && hideButton}
              <button
                className={`round-btn activity-btn${byActivity ? ' on' : ''}`}
                aria-label={t('sidebar.byActivity')}
                aria-pressed={byActivity}
                title={byActivity ? t('sidebar.byProject') : waitingOnYou > 0 ? t('sidebar.byActivityUrgent', waitingOnYou) : wanted > 0 ? t('sidebar.byActivityCount', wanted) : t('sidebar.byActivity')}
                onClick={() => setByActivity(!byActivity)}
              >
                <IconBell size={compact ? 20 : 16} />
                {waitingOnYou > 0 && <span className="bell-dot" aria-hidden="true" />}
              </button>
              <button className="round-btn" aria-label={t('sidebar.search')} title={t('sidebar.search')} onClick={() => setSearching(true)}>
                <IconSearch size={compact ? 20 : 16} />
              </button>
            </span>
          </>
        )}
      </div>

      {notice}
      <div className="sidebar-scroll">
        {onHost && !search.trim() && <HostSwitcher hosts={hosts} current={hostId} onPick={onHost} all={Boolean(mergedModel)} onAll={onAll} />}
        {down && <p className="group-empty top">{t('sidebar.hostDown')}</p>}
        {!down && !search.trim() && (
          <div className="side-nav">
            {(() => {
              const links = [
                // On a wide screen a new thread is the first row, like Codex; a phone keeps the floating button by the thumb.
                !compact && (
                  <button key="new" className="side-link" onClick={onNew}>
                    <IconEdit size={17} /> {t('sidebar.newThread')}
                  </button>
                ),
                onOpenSteward && <button key="steward" className={`side-link${stewardActive ? ' active' : ''}`} aria-current={stewardActive ? 'page' : undefined} onClick={onOpenSteward}>
                  <IconSparkle size={compact ? 21 : 17} /> {t('sidebar.steward')}
                </button>,
                // Views an extension pins (the board) keep a row of their own; the machines wait behind "more".
                ...extensionNav.map((entry) => (
                  <button key={entry.key} className={`side-link${entry.active ? ' active' : ''}`} aria-current={entry.active ? 'page' : undefined} onClick={entry.onOpen}>
                    <HostIcon name={entry.icon} size={compact ? 21 : 17} /> {entry.label}
                  </button>
                )),
                ...pagesShown.map((page) => (
                  <button key={page.key} className={`side-link${page.active ? ' active' : ''}`} aria-current={page.active ? 'page' : undefined} title={page.live ? page.liveLabel : undefined} onClick={page.onOpen}>
                    <page.icon size={compact ? 21 : 17} /> {page.label}
                    {page.live && <span className="side-desktop-live" role="img" aria-label={page.liveLabel} />}
                  </button>
                )),
              ].filter(Boolean);
              if (!pagesHidden.length) return links;
              const more = (
                <button
                  key="more"
                  className="side-link side-more"
                  aria-label={t('sidebar.morePages')}
                  title={t('sidebar.morePages')}
                  aria-haspopup="menu"
                  aria-expanded={pagesMenu !== null}
                  onClick={(e) => setPagesMenu(below(e))}
                >
                  <IconMore size={compact ? 20 : 17} />
                </button>
              );
              // The "more" button shares the last row instead of taking one of its own.
              return [...links.slice(0, -1), <div key="last" className="side-nav-row">{links.at(-1)}{more}</div>];
            })()}
            {pagesMenu && (
              <Menu
                label={t('sidebar.morePages')}
                x={pagesMenu[0]}
                y={pagesMenu[1]}
                items={pagesHidden.map((page) => ({ id: page.key, label: page.label, icon: page.icon, onSelect: page.onOpen }))}
                onClose={() => setPagesMenu(null)}
              />
            )}
          </div>
        )}
        {!down && !search.trim() && goals.length > 0 && onOpenGoal && (
          <SidebarGoals goals={goals} needsYou={needsYouGoals} activeGoalId={activeGoalId} onOpenGoal={onOpenGoal} />
        )}
        {!down && groups}
        {/* Reading the machine's sessions, or importing one, went wrong: said here, where the rows
            are, with a way to try again. Cleared by the next attempt. */}
        {!down && !mergedModel && externalError && (
          <p className="group-empty external-error" role="alert">
            {externalError.message}
            {externalError.retry && (
              <>
                {' '}
                <button type="button" className="text-btn" onClick={externalError.retry}>
                  {t('common.retry')}
                </button>
              </>
            )}
          </p>
        )}
        {/* The machine may hold far more sessions than this: shown a page at a time, more on ask. */}
        {!down && !mergedModel && moreExternal && onLoadMoreExternal && !search.trim() && (
          <button type="button" className="sidebar-more-sessions" onClick={onLoadMoreExternal}>
            {t('sidebar.moreExternal')}
          </button>
        )}
        {!down && onSearch && query.length >= SEARCH_MIN && (
          <section className="search-hits" aria-label={t('sidebar.inConversations')}>
            <div className="search-hits-head">{t('sidebar.inConversations')}</div>
            {found?.for !== query ? (
              <p className="group-empty">{t('sidebar.searching')}</p>
            ) : found.hits.length === 0 ? (
              <p className="group-empty">{t('sidebar.notFound')}</p>
            ) : (
              found.hits.map((hit) => (
                <button type="button" className={`search-hit${hit.threadId === selectedId ? ' active' : ''}`} key={`${hit.threadId}:${hit.turnId}:${hit.role}`} onClick={() => onOpenHit?.(hit)}>
                  <span className="search-hit-top">
                    <span className="thread-title">{hit.title}</span>
                    {hit.archived && <span className="search-hit-tag">{t('activity.archived')}</span>}
                    <time dateTime={hit.ts}>{clockLabel(hit.ts)}</time>
                  </span>
                  <span className="search-hit-text">
                    <span className="search-hit-who">{hit.role === 'user' ? t('sidebar.me') : t('sidebar.reply')}</span>
                    {markTerms(hit.snippet, query).map((part, i) => (part.hit ? <mark key={i}>{part.text}</mark> : part.text))}
                  </span>
                </button>
              ))
            )}
          </section>
        )}
      </div>

      <div className="sidebar-float">
        {compact && !down && (
          <button className="new-thread" onClick={onNew}>
            <IconEdit size={19} /> {t('sidebar.newThread')}
          </button>
        )}
        {/* The look, the archive, importing and signing out are not everyday things: they live behind this. */}
        <button
          className="round-btn"
          aria-label={t('sidebar.settings')}
          title={t('sidebar.settings')}
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            onOpenSettings(rect.right, rect.top - 8);
          }}
        >
          <IconSettings size={compact ? 20 : 17} />
        </button>
      </div>
    </div>
  );
}
