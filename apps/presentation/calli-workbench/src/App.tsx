import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import {
  Api,
  ApiError,
  MAX_QUEUED,
  type AgentInfo,
  type GoalView,
  type ModelOption,
  type SearchHit,
  type Section,
  type SlashCommand,
  type Thread,
  type Todo,
  type WorkdirsConfig,
  type WorkMode,
} from './api';
import { historyPage, mergeHistory, watchHistory } from './history';
import type { HubAction } from './commands';
import { buildProjectOptions } from './projects';
import { useAppearance, useInTitlebar, useIsCompact, useListSwipe, useStoredFlag } from './hooks';
import { useHosts } from './hosts';
import type { PreparedImage } from './images';
import { attentionCount, pageTitle, threadNotices } from './notices';
import { folderName } from './paths';
import { ThreadPlace } from './components/ThreadPlace';
import { PERMISSION_LABELS, startingPermission } from './permissions';
import { buildTimeline, toMarkdown, turnPreviews, type StoredEvent } from './timeline';
import { AppearancePanel } from './components/AppearancePanel';
import { ActionSheet, type SheetTarget } from './components/ActionSheet';
import { Composer, prefillComposerDraft } from './components/Composer';
import { DirectoryPicker } from './components/DirectoryPicker';
import { HostDown, MachinePicker } from './components/Hosts';
import { Menu } from './components/Menu';
import { StatusPanel } from './components/StatusPanel';
import { DeskPage } from './components/DeskPage';
import { createThreadFeed } from './thread-feed';
import { clampSidebarWidth, defaultSidebarWidth, projectDir, savedSidebarWidth, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, type HostSource } from './sidebar';
import { AgentPicker, effortsForModel, imagesBlocked, imagesNote, modelName, speedsForModel, ModelPicker, PermissionPicker, UsageMeter, WorkspacePicker } from './components/Pickers';
import { Sidebar, SIDEBAR_SHORTCUT } from './components/Sidebar';
import { forkBlocked, useThreadActions } from './components/ThreadActions';
import { ThreadView } from './components/ThreadView';
import { currentPlan, PlanButton } from './components/PlanPanel';
import { TurnRuler } from './components/TurnRuler';
import { NewProjectDialog, ProjectPicker, RootsDialog } from './components/Projects';
import { Toast, type ToastState } from './components/Toast';
import { DemoApi, DemoError } from './demo/DemoApi';
import { IconArrowDown, IconBack, IconClose, IconEdit, IconList, IconMenu, IconMore, IconPalette, IconSidebar, AgentMark, IconLanguage } from './components/icons';
import { chooseLocale, LOCALES, localeChoice, t } from './i18n';

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(t);
      resolve();
    });
  });

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));


/** How close to the end of the thread still counts as being at the bottom. */
const NEAR_BOTTOM_PX = 160;
/** Momentum keeps a list scrolling for a while after the hand that flicked it has lifted. */
const GESTURE_GRACE_MS = 1500;

const isNearBottom = (el: HTMLElement) => el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;

/** The plan page's address. Threads have none: the main area showing a thread is the plain URL. */
const DESK_HASH = '#/desk';

type MainView = 'thread' | 'desk';

/** The page an address opens; null for none (a thread). */
function viewForHash(hash: string): MainView | null {
  return hash === DESK_HASH ? 'desk' : null;
}

/** Whether the thread list is put away on a wide screen. */
const SIDEBAR_HIDDEN_KEY = 'calli.sidebar.hidden';
/** The width a person last dragged the desktop thread list to. */
const SIDEBAR_WIDTH_KEY = 'calli.sidebar.width';
const SIDEBAR_KEYBOARD_STEP = 16;

const QUICK_SWITCH_SIDE = 10;
const QUICK_SWITCH_HOLD_MS = 360;

function ThreadQuickSwitch({ threads, selectedId, agents, onSelect }: {
  threads: Thread[];
  selectedId: string;
  agents: AgentInfo[];
  onSelect: (id: string) => void;
}) {
  const selectedIndex = threads.findIndex((thread) => thread.id === selectedId);
  const items = useMemo(() => selectedIndex < 0 ? [] : threads.slice(Math.max(0, selectedIndex - QUICK_SWITCH_SIDE), Math.min(threads.length, selectedIndex + QUICK_SWITCH_SIDE + 1)), [threads, selectedIndex]);
  const root = useRef<HTMLDivElement>(null);
  const rows = useRef(new Map<string, HTMLButtonElement>());
  const panel = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  const lastY = useRef(0);
  const tapOpenAtPointerDown = useRef(false);
  const [tapOpen, setTapOpen] = useState(false);
  const [press, setPress] = useState<{ pointerId: number; open: boolean; targetId: string } | null>(null);
  const agentById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  const open = tapOpen || (press?.open ?? false);
  const clearTimer = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };
  const targetAt = (y: number) => {
    const panelRect = panel.current?.getBoundingClientRect();
    // The floating trigger sits below the list. A hold-and-release without sliding should not
    // jump to the nearest row above the finger.
    if (!panelRect || y < panelRect.top || y > panelRect.bottom) return selectedId;
    const visible = items
      .map((thread) => ({ thread, el: rows.current.get(thread.id) }))
      .filter((row): row is { thread: Thread; el: HTMLButtonElement } => Boolean(row.el));
    if (!visible.length) return selectedId;
    let best = visible[0];
    let bestDistance = Infinity;
    for (const row of visible) {
      const rect = row.el.getBoundingClientRect();
      if (y >= rect.top && y <= rect.bottom) return row.thread.id;
      const distance = Math.abs(y - (rect.top + rect.bottom) / 2);
      if (distance < bestDistance) {
        best = row;
        bestDistance = distance;
      }
    }
    return best.thread.id;
  };
  const updateTarget = (y: number) => {
    if (!panel.current) return;
    setPress((current) => current && current.open ? { ...current, targetId: targetAt(y) } : current);
  };
  const reset = (el?: HTMLElement, pointerId?: number) => {
    clearTimer();
    if (el && pointerId !== undefined && el.hasPointerCapture(pointerId)) el.releasePointerCapture(pointerId);
    setPress(null);
  };
  useEffect(() => () => clearTimer(), []);
  useEffect(() => {
    if (!tapOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setTapOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setTapOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [tapOpen]);
  if (selectedIndex < 0 || items.length <= 1) return null;
  return (
    <div className={`quick-switch${open ? ' open' : ''}`} ref={root}>
      <button
        type="button"
        className="quick-switch-button"
        aria-label={t('quickSwitch.handle')}
        aria-expanded={open}
        onPointerDown={(e) => {
          if (e.pointerType === 'mouse' && e.button !== 0) return;
          e.preventDefault();
          e.currentTarget.setPointerCapture(e.pointerId);
          tapOpenAtPointerDown.current = tapOpen;
          lastY.current = e.clientY;
          setPress({ pointerId: e.pointerId, open: false, targetId: selectedId });
          clearTimer();
          timer.current = window.setTimeout(() => {
            timer.current = null;
            setPress((current) => current?.pointerId === e.pointerId ? { ...current, open: true } : current);
            requestAnimationFrame(() => updateTarget(lastY.current));
          }, QUICK_SWITCH_HOLD_MS);
        }}
        onPointerMove={(e) => {
          if (press?.pointerId !== e.pointerId) return;
          e.preventDefault();
          lastY.current = e.clientY;
          updateTarget(e.clientY);
        }}
        onPointerUp={(e) => {
          if (press?.pointerId !== e.pointerId) return;
          e.preventDefault();
          const targetId = press.open ? press.targetId : null;
          reset(e.currentTarget, e.pointerId);
          if (targetId) {
            setTapOpen(false);
            if (targetId !== selectedId) onSelect(targetId);
          } else {
            setTapOpen(!tapOpenAtPointerDown.current);
          }
        }}
        onPointerCancel={(e) => {
          if (press?.pointerId === e.pointerId) reset(e.currentTarget, e.pointerId);
        }}
      >
        <IconList size={20} />
      </button>
      {open && (
        <div className="quick-switch-panel" ref={panel} role="listbox" aria-label={t('quickSwitch.sessions')}>
          {items.map((thread) => {
            const agent = agentById.get(thread.agent);
            const target = press?.open ? press.targetId === thread.id : thread.id === selectedId;
            const current = thread.id === selectedId;
            const place = thread.scratch ? t('projects.none') : folderName(projectDir(thread).cwd);
            return (
              <button
                type="button"
                key={thread.id}
                ref={(el) => { if (el) rows.current.set(thread.id, el); else rows.current.delete(thread.id); }}
                className={`quick-switch-card${current ? ' current' : ''}${target ? ' target' : ''}`}
                role="option"
                aria-selected={target}
                onClick={() => {
                  setTapOpen(false);
                  if (thread.id !== selectedId) onSelect(thread.id);
                }}
              >
                <span className="quick-switch-title">{thread.title}</span>
                <span className="quick-switch-meta">
                  {agent?.icon && <AgentMark path={agent.icon} size={13} />}
                  <span>{agent?.label ?? thread.agent}</span>
                  <span>{place}</span>
                  {thread.status === 'running' && <span className="dot running" aria-label={t('status.running')} />}
                  {thread.waiting && <span className="dot waiting" aria-label={t('notices.unknown.label')} />}
                  {thread.unread && !current && <span className="dot unread" aria-label={t('sidebar.unread')} />}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface AppProps {
  /** The machine on screen: one of the other machines, or null for the local one. */
  hostId?: string | null;
  /** Switches machines. The app starts over on the new one, with what the switch was made from still open. */
  onHost?: (id: string | null, start?: AppStart) => void;
  start?: AppStart;
}

/** What is open when the app starts over on another machine: the phone's thread list, or a thread of that machine. */
export type AppStart = 'list' | { thread: string };

export function App({ hostId = null, onHost, start }: AppProps) {
  // Looking at another machine, every call is answered by that machine's service.
  const { hosts, loaded: hostsLoaded, reload: reloadHosts } = useHosts(false);
  const host = hostId ? hosts.find((h) => h.id === hostId) : undefined;
  // Until the machines are known, one is taken to be up: asking it is how the page finds out.
  const hostUp = !hostId || !hostsLoaded || host?.status === 'online';
  // DATA LAYER PENDING: the fixture-backed DemoApi stands in until the LoopX adapter replaces it.
  const api = useMemo<Api>(() => new DemoApi(), [hostId, hostUp]);
  const demo = api.demo;

  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [workdirs, setWorkdirs] = useState<string[]>([]);
  // Each project root and whether it was added from the page; and whether threads can be in no project.
  const [roots, setRoots] = useState<Array<{ path: string; removable: boolean }>>([]);
  const [scratchAvailable, setScratchAvailable] = useState(false);
  // A new thread in no project: the service gives it a scratch folder.
  const [draftScratch, setDraftScratch] = useState(false);
  const [projectDialog, setProjectDialog] = useState<'new' | 'roots' | null>(null);
  const applyWorkdirs = useCallback((c: WorkdirsConfig) => {
    setWorkdirs(c.workdirs);
    setRoots(c.roots ?? c.workdirs.map((path) => ({ path, removable: false })));
    setScratchAvailable(c.scratch === true);
  }, []);
  const [threads, setThreads] = useState<Thread[]>([]);
  /** Whether the list has been read yet: until then an empty one means "not read", not "none". */
  const [threadsKnown, setThreadsKnown] = useState(false);
  const [sections, setSections] = useState<Section[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [todosLoading, setTodosLoading] = useState(true);
  const [todosError, setTodosError] = useState('');
  /** What the main area shows: a thread (or the blank one a new thread starts from) or the desk. */
  const [threadLink, setThreadLink] = useState(() => window.location.hash.startsWith('#/thread/') ? window.location.hash : null);
  const [view, setView] = useState<MainView>(() => viewForHash(window.location.hash) ?? 'thread');
  const [observedAt, setObservedAt] = useState<string | null>(null);
  const threadFeed = useMemo(() => createThreadFeed(api, (list, at) => {
    setThreads(list);
    setObservedAt(at);
    setThreadsKnown(true);
  }, () => undefined), [api]);
  useEffect(() => {
    threadFeed.start();
    return () => threadFeed.dispose();
  }, [threadFeed]);
  // Every machine's threads in one list. A display setting, so it stays on as the app starts over
  // on another machine; with no other machine there is nothing to merge.
  const [allHosts, setAllHosts] = useStoredFlag('calli.sidebar.allHosts');
  const merging = allHosts && hosts.length > 0;
  const [elsewhere, setElsewhere] = useState<HostSource[]>([]);
  const hostsRef = useRef(hosts);
  hostsRef.current = hosts;
  // Which machines there are and whether they answer: the list is read again when that changes.
  const hostsKey = hosts.map((h) => `${h.id}:${h.status}`).join('|');
  useEffect(() => {
    if (!merging) { setElsewhere([]); return; }
    let alive = true;
    const targets = [{ id: null as string | null, label: t('hosts.local'), online: true }, ...hostsRef.current.map((h) => ({ id: h.id as string | null, label: h.label, online: h.status === 'online' }))]
      .filter((x) => x.id !== hostId);
    setElsewhere(targets.map((x): HostSource => ({ hostId: x.id, label: x.label, state: x.online ? 'loading' : 'offline', at: null, threads: [] })));
    const update = (id: string | null, change: (s: HostSource) => HostSource) => setElsewhere((prev) => prev.map((s) => (s.hostId === id ? change(s) : s)));
    const read = () => {
      for (const x of targets.filter((x) => x.online)) {
        new DemoApi().threads().then(
          (list) => { if (alive) update(x.id, (s) => ({ ...s, state: 'live', at: new Date().toISOString(), threads: list })); },
          () => { if (alive) update(x.id, (s) => ({ ...s, state: 'failed' })); },
        );
      }
    };
    read();
    const timer = setInterval(() => { if (!document.hidden) read(); }, 20_000);
    return () => { alive = false; clearInterval(timer); };
  }, [merging, hostId, hostsKey]);
  const [todosKey, setTodosKey] = useState(0);
  const [selectedId, setSelectedIdState] = useState<string | null>(typeof start === 'object' ? start.thread : null);
  const setSelectedId = useCallback((id: string | null) => setSelectedIdState(id), []);
  const [historyTurnIds, setHistoryTurnIds] = useState<string[]>([]);
  const [events, setEvents] = useState<StoredEvent[]>([]);
  /** The history revision the view holds; an edit names it so a stale page is refused. */
  const [historyRevision, setHistoryRevision] = useState(0);
  /** Bumped when an edit replaced the conversation, so the page is read again from its start. */
  const [historyEpoch, setHistoryEpoch] = useState(0);
  const [draftAgent, setDraftAgent] = useState('');
  const [draftCwd, setDraftCwd] = useState('');
  // A new thread may start in a git worktree of its directory; offered only when the directory is in a repository.
  const [draftWorktree, setDraftWorktree] = useState(false);
  const [worktreeOffer, setWorktreeOffer] = useState<{ cwd: string; branch: string | null; changes: number } | null>(null);
  const [draftSectionId, setDraftSectionId] = useState<string | null>(null);
  const [draftWorkMode, setDraftWorkMode] = useState<WorkMode>('execute');
  const [modeBusy, setModeBusy] = useState(false);
  const [draftPermission, setDraftPermission] = useState('auto_edit');
  const [draftModel, setDraftModel] = useState<string | null>(null);
  const [draftEffort, setDraftEffort] = useState<string | null>(null);
  const [draftSpeed, setDraftSpeed] = useState<string | null>(null);
  const [modelsByAgent, setModelsByAgent] = useState<Record<string, ModelOption[]>>({});
  const modelVersions = useRef<Record<string, number>>({});
  const [commandsByKey, setCommandsByKey] = useState<Record<string, SlashCommand[]>>({});
  const [drawer, setDrawer] = useState(start === 'list');
  const [chosenSidebarWidth, setSidebarWidth] = useState<number | null>(() => {
    try {
      return savedSidebarWidth(window.localStorage.getItem(SIDEBAR_WIDTH_KEY));
    } catch {
      return null;
    }
  });
  // Without a dragged width the list follows the window. The state changes only when the width it
  // gives does, so resizing the window does not render the app on every frame.
  const [fittedSidebarWidth, setFittedSidebarWidth] = useState(() => defaultSidebarWidth(window.innerWidth));
  useEffect(() => {
    if (chosenSidebarWidth !== null) return;
    const fit = () => setFittedSidebarWidth(defaultSidebarWidth(window.innerWidth));
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [chosenSidebarWidth]);
  const sidebarWidth = chosenSidebarWidth ?? fittedSidebarWidth;
  const [sidebarDrag, setSidebarDrag] = useState<{ pointerId: number; startX: number; startWidth: number } | null>(null);
  const rememberSidebarWidth = useCallback((width: number) => {
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width));
    } catch {
      // The resize still applies for this page when storage is unavailable.
    }
  }, []);
  const startSidebarResize = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setSidebarDrag({ pointerId: e.pointerId, startX: e.clientX, startWidth: sidebarWidth });
  }, [sidebarWidth]);
  const moveSidebarResize = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!sidebarDrag || e.pointerId !== sidebarDrag.pointerId) return;
    e.preventDefault();
    setSidebarWidth(clampSidebarWidth(sidebarDrag.startWidth + e.clientX - sidebarDrag.startX));
  }, [sidebarDrag]);
  const finishSidebarResize = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!sidebarDrag || e.pointerId !== sidebarDrag.pointerId) return;
    const width = clampSidebarWidth(sidebarDrag.startWidth + e.clientX - sidebarDrag.startX);
    setSidebarWidth(width);
    rememberSidebarWidth(width);
    setSidebarDrag(null);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  }, [rememberSidebarWidth, sidebarDrag]);
  const cancelSidebarResize = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!sidebarDrag || e.pointerId !== sidebarDrag.pointerId) return;
    rememberSidebarWidth(sidebarWidth);
    setSidebarDrag(null);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  }, [rememberSidebarWidth, sidebarDrag, sidebarWidth]);
  const resizeSidebarFromKey = useCallback((e: ReactKeyboardEvent<HTMLDivElement>) => {
    const next = e.key === 'ArrowLeft' ? sidebarWidth - SIDEBAR_KEYBOARD_STEP
      : e.key === 'ArrowRight' ? sidebarWidth + SIDEBAR_KEYBOARD_STEP
        : e.key === 'Home' ? SIDEBAR_MIN_WIDTH
          : e.key === 'End' ? SIDEBAR_MAX_WIDTH
            : null;
    if (next === null) return;
    e.preventDefault();
    const width = clampSidebarWidth(next);
    setSidebarWidth(width);
    rememberSidebarWidth(width);
  }, [rememberSidebarWidth, sidebarWidth]);
  // On a wide screen the list can be put away for room, like Codex; remembered on this device.
  const [sidebarHidden, setSidebarHidden] = useState(() => {
    try {
      return window.localStorage.getItem(SIDEBAR_HIDDEN_KEY) === '1';
    } catch {
      return false;
    }
  });
  const toggleSidebar = useCallback(() => {
    setSidebarHidden((hidden) => {
      try {
        window.localStorage.setItem(SIDEBAR_HIDDEN_KEY, hidden ? '0' : '1');
      } catch {
        // Not remembered, still toggled.
      }
      return !hidden;
    });
  }, []);
  // On a phone the thread list is a page of its own; `drawer` slides the conversation off to show it.
  const compact = useIsCompact();
  // The installed app draws into the window's title bar (see the stylesheet): that row is one band
  // of the list's colour, as in Codex, and the theme colour, which the window paints behind its own
  // buttons there, says the same.
  const inTitlebar = useInTitlebar() && !compact;
  // ⇧⌘S (Ctrl+Shift+S elsewhere), Codex's key for it. Only where there is a sidebar to put away.
  useEffect(() => {
    if (compact) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.code === 'KeyS' && e.shiftKey && (e.metaKey || e.ctrlKey) && !e.altKey) {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [compact, toggleSidebar]);
  const listOpen = drawer && compact;
  useListSwipe(compact, listOpen, setDrawer);
  /** Where the settings menu opened from, while it is open. */
  const [settingsAt, setSettingsAt] = useState<{ x: number; y: number } | null>(null);
  const [appearance, setAppearance, theme] = useAppearance();
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  // An error belongs to the thread (or new-thread page) it happened in, so other threads stay clean.
  // The closure keeps the thread that was open when the failing request started.
  const [errorState, setErrorState] = useState<{ text: string; thread: string | null } | null>(null);
  const setError = (text: string | null) => setErrorState(text ? { text, thread: selectedId } : null);
  const error = errorState && errorState.thread === selectedId ? errorState.text : null;
  const [toast, setToast] = useState<ToastState | null>(null);
  const [showDirPicker, setShowDirPicker] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  // Counters that open the composer's pickers from elsewhere (the slash menu).
  const [permissionSignal, setPermissionSignal] = useState(0);
  const [modelSignal, setModelSignal] = useState(0);
  const [atBottom, setAtBottom] = useState(true);
  // The thread has turns older than the ones loaded; they are fetched a page at a time.
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // The first page of the open thread is still on its way.
  const [eventsLoading, setEventsLoading] = useState(false);
  // Text handed back to the composer: messages that were waiting when the user pressed stop.
  const [restore, setRestore] = useState<{ text: string; nonce: number } | null>(null);
  const composerDraftKey = hostId ? `host:${hostId}:${selectedId ?? 'new'}` : selectedId ?? 'new';
  const [pageHidden, setPageHidden] = useState(() => document.hidden);
  const baseTitle = useRef(document.title);
  const seenThreads = useRef<Thread[] | null>(null);

  const timeline = useMemo(() => buildTimeline(events), [events]);
  const turnPreviewMap = useMemo(() => turnPreviews(timeline.items), [timeline.items]);
  // The newest turn's plan sits in the top bar, so the conversation does not show it twice.
  const topPlan = useMemo(() => currentPlan(timeline.items), [timeline.items]);
  const threadItems = useMemo(() => topPlan ? timeline.items.filter((item) => item !== topPlan) : timeline.items, [timeline.items, topPlan]);
  /** The thread's native goal. Live events say it when they can; otherwise what the service last said. */
  const [goalFetched, setGoalFetched] = useState<GoalView | null>(null);
  const goalView = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === 'goal.updated') return (events[i].data.goal as GoalView | null) ?? null;
    }
    return goalFetched;
  }, [events, goalFetched]);
  const rulerTurnIds = useMemo(() => [...new Set([...historyTurnIds, ...turnPreviewMap.keys()])], [historyTurnIds, turnPreviewMap]);
  // What the user last sent here, for a box that was reloaded mid-send to learn that its message arrived.
  const lastSent = useMemo(() => {
    for (let i = timeline.items.length - 1; i >= 0; i--) {
      const item = timeline.items[i];
      if (item.kind === 'user') return { turnId: item.turnId, text: item.text };
    }
    return null;
  }, [timeline.items]);
  const selected = threads.find((t) => t.id === selectedId) ?? null;
  const onDesk = view === 'desk' && !selectedId;
  /** The name of the page the main area shows instead of a thread, for the top bar; null on a thread. */
  const viewTitle = onDesk ? t('desk.title') : null;
  const onPage = viewTitle !== null;

  // The address follows what the main area shows. Replaced, not pushed: going between the pages and
  // threads is not a trail for the back button to walk.
  useEffect(() => {
    if (threadLink) return;
    const want = onDesk ? DESK_HASH : '';
    if (window.location.hash !== want) {
      window.history.replaceState(window.history.state, '', want || window.location.pathname + window.location.search);
    }
  }, [onDesk, threadLink]);

  // Whether the directory a new thread would start in is a git repository. Asked once per directory picked.
  useEffect(() => {
    if (!api || !draftCwd || selectedId) return;
    let stale = false;
    api
      .worktreeOptions(draftCwd)
      .then((o) => {
        if (!stale) setWorktreeOffer(o.available ? { cwd: draftCwd, branch: o.branch ?? null, changes: o.changes ?? 0 } : null);
      })
      .catch(() => !stale && setWorktreeOffer(null));
    return () => {
      stale = true;
    };
  }, [api, draftCwd, selectedId]);
  const offer = worktreeOffer?.cwd === draftCwd ? worktreeOffer : null;
  // Projects for a new thread: where threads went lately, newest first, then roots not used yet.
  const projectOptions = useMemo(() => buildProjectOptions(threads, workdirs, draftCwd), [threads, workdirs, draftCwd]);

  // An address typed or followed while the page is open.
  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash;
      if (hash.startsWith('#/thread/')) { setThreadLink(hash); return; }
      setThreadLink(null);
      const next = viewForHash(hash);
      if (!next) return;
      setSelectedId(null);
      setView(next);
      setDrawer(false);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // Both spellings of the thread's directory; kept stable so the thread view is not re-rendered for nothing.
  const threadRoots = useMemo(() => (selected ? [selected.cwd, selected.cwdReal] : []), [selected?.cwd, selected?.cwdReal]);
  const running = timeline.running || selected?.status === 'running';

  // A plan the user chose to leave be, by thread: the proposal's key, kept for this page only.
  const [planSkip, setPlanSkip] = useState<{ threadId: string; key: string } | null>(null);

  // What is waiting for the user right now: the earliest of a pending question, a pending approval,
  // or the last finished plan nothing has been said after. The bottom sheet asks about it in the
  // composer's place.
  const waiting = useMemo(() => {
    const candidates: Array<{ order: number; target: SheetTarget }> = [];
    timeline.items.forEach((it, order) => {
      if (it.kind === 'approval' && it.decision === null) candidates.push({ order, target: { kind: 'approval', item: it } });
      else if (it.kind === 'input' && it.answer === null) candidates.push({ order, target: { kind: it.inputKind === 'plan' ? 'plan-approval' : 'question', item: it } });
    });
    if (!timeline.running) {
      for (let i = timeline.items.length - 1; i >= 0; i--) {
        const it = timeline.items[i];
        if (it.kind === 'user') break;
        if (it.kind === 'proposal' && it.done) {
          if (!(planSkip && planSkip.threadId === selectedId && planSkip.key === it.key)) candidates.push({ order: i, target: { kind: 'hub-plan', item: it } });
          break;
        }
      }
    }
    candidates.sort((a, b) => a.order - b.order);
    return candidates;
  }, [timeline, planSkip, selectedId]);
  const waitingOn = waiting[0] ?? null;

  const listedThreads = threads;

  const scrollerRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const stickToBottom = useRef(true);
  // The thread on screen, for answers that arrive after the user has moved on.
  const shownThread = useRef<string | null>(null);
  shownThread.current = selectedId;
  // Whether the user has a hand on the scroller, and when they last did.
  const gesture = useRef({ held: false, at: 0 });

  const handleError = (e: unknown) => {
    // Doing something the read-only workbench cannot do is refused, not reported as a failure.
    if (e instanceof DemoError) {
      if (e.write) setToast({ message: t('app.connectToSend') });
    }
    // A machine that dropped is explained in the main area; the machines are asked at once how they are.
    else if (e instanceof ApiError && e.details.code === 'host_offline') void reloadHosts();
    else setError(message(e));
  };

  const refreshThreads = () => threadFeed.read().catch(() => undefined);
  const todosRequest = useRef(0);
  const reloadTodos = useCallback(async () => {
    const request = ++todosRequest.current;
    try {
      const value = await api.todos();
      if (request === todosRequest.current) { setTodos(value); setTodosError(''); }
    } catch (e) { if (request === todosRequest.current) setTodosError(String(e)); }
    finally { if (request === todosRequest.current) setTodosLoading(false); }
  }, [api]);
  useEffect(() => {
    setTodosLoading(true); setTodosError(''); setTodos([]); void reloadTodos();
    return () => { todosRequest.current++; };
  }, [reloadTodos]);
  const dismissToast = useCallback(() => setToast(null), []);

  // Initial data.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [a, c, , s] = await Promise.all([
          api.agents(),
          api.workdirs(),
          threadFeed.read(),
          api.sections(),
        ]);
        if (cancelled) return;
        setAgents(a);
        applyWorkdirs(c);
        setSections(s);
        setDraftCwd((cur) => cur || c.workdirs[0] || '');
        const first = a.find((x) => x.available && !x.hidden);
        setDraftAgent(first?.id ?? '');
        if (first) setDraftPermission(startingPermission(first.capabilities));
      } catch (e) {
        if (!cancelled) handleError(e);
      }
    })();
    return () => {
      cancelled = true;
    };
    // handleError only touches state setters, so it is stable enough to leave out.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);

  // Thread-list changes, pushed by the service. Refresh is debounced.
  useEffect(() => {
    threadFeed.stream(false);
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        void threadFeed.read().catch(() => undefined);
        // Plan chips show thread states; a bumped key reloads the open page.
        setTodosKey((k) => k + 1);
      }, 150);
    };
    (async () => {
      for (let attempt = 0; !ctrl.signal.aborted; attempt++) {
        // Whatever changed while the stream was down was never announced on it.
        if (attempt > 0) refresh();
        try {
          await api.stream<StoredEvent>(
            '/api/stream',
            (ev) => {
              if (ev.type === 'agent.modelsChanged') {
                const { agent, models } = ev.data as { agent: string; models: ModelOption[] };
                modelVersions.current[agent] = (modelVersions.current[agent] ?? 0) + 1;
                setModelsByAgent(previous => ({ ...previous, [agent]: models }));
              } else refresh();
            },
            ctrl.signal,
            undefined,
            () => threadFeed.stream(true),
          );
        } catch {
          // On another machine, a stream that will not open is how its dropping first shows.
          if (api.host && !ctrl.signal.aborted) void reloadHosts();
        }
        // The demo has no service; anywhere else, a stream that ended is being retried.
        if (!ctrl.signal.aborted) threadFeed.stream(false);
        await sleep(2000, ctrl.signal);
      }
    })();
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);

  const historyCursor = useRef<number | null>(null);
  const olderRequest = useRef<object | null>(null);
  // The card reads the goal from events once they arrive; this covers goals older than the page.
  useEffect(() => {
    setGoalFetched(null);
    if (!api || !selectedId) return;
    let active = true;
    api.threadGoal(selectedId).then(value => { if (active) setGoalFetched(value.goal); }).catch(() => undefined);
    return () => { active = false; };
  }, [api, selectedId, historyEpoch]);
  useEffect(() => {
    setEvents([]); setHistoryTurnIds([]); setHasOlder(false); setLoadingOlder(false);
    setEventsLoading(Boolean(api && selectedId)); historyCursor.current = null; olderRequest.current = null;
    if (!api || !selectedId) return;
    const ctrl = new AbortController();
    stickToBottom.current = true; setAtBottom(true);
    void watchHistory(api, selectedId, ctrl.signal, {
      page: page => {
        setEvents(page.events); setHistoryTurnIds(page.turnIds ?? []); setEventsLoading(false);
        setHistoryRevision(page.snapshotSeq);
        historyCursor.current = page.nextCursor; setHasOlder(page.hasOlder);
      },
      events: batch => {
        // An edit replaced the conversation behind this page: read the current one from the start.
        if (batch.some((ev) => ev.type === 'thread.historyChanged')) {
          stickToBottom.current = true;
          setHistoryEpoch((n) => n + 1);
          return;
        }
        // Everything the page has seen is part of the history it may edit against.
        setHistoryRevision((revision) => batch.reduce((latest, ev) => Math.max(latest, ev.seq), revision));
        setEvents(previous => mergeHistory(previous, batch));
      },
      error: error => { setEventsLoading(false); handleError(error); },
    });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, selectedId, historyEpoch]);

  /** Prepends the page of turns before the loaded ones, keeping the scroll position on the same content. */
  const loadOlder = async () => {
    const id = selectedId;
    const first = historyCursor.current ?? 0;
    if (!api || !id || loadingOlder || olderRequest.current || first <= 1) return;
    const request = {}; olderRequest.current = request;
    setLoadingOlder(true);
    try {
      const page = await historyPage(api, id, first);
      const older = page.events;
      // The answer is for the thread that asked; another one may be open by now.
      if (shownThread.current !== id || olderRequest.current !== request) return;
      setHasOlder(page.hasOlder); historyCursor.current = page.nextCursor;
      const el = scrollerRef.current;
      const fromBottom = el ? el.scrollHeight - el.scrollTop : 0;
      setEvents((prev) => mergeHistory(older, prev));
      // New content lands above the viewport; stay on the same row while it inserts.
      requestAnimationFrame(() => {
        if (el && !stickToBottom.current && !jump) el.scrollTop = el.scrollHeight - fromBottom;
      });
    } catch (e) {
      if (olderRequest.current === request) { setJump(null); handleError(e); }
    } finally {
      if (olderRequest.current === request) { olderRequest.current = null; setLoadingOlder(false); }
    }
  };

  // Keep the view pinned to the bottom while the user has not scrolled up.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [events]);

  // Screenshots and slow-rendering blocks grow the thread after the scroll above, which used to
  // leave a reopened thread parked at the top. Watching the thread's height keeps it pinned. The
  // scroller is watched too: a phone keyboard shrinks it and would hide the newest lines.
  useEffect(() => {
    const el = scrollerRef.current;
    const content = el?.querySelector('.merged-history, .thread');
    if (!el || !content) return;
    const ro = new ResizeObserver(() => {
      if (stickToBottom.current) el.scrollTop = el.scrollHeight;
      // Growth below an unpinned view moves nothing, so no scroll event reports that the view
      // has fallen behind; the jump button has to learn it here.
      else setAtBottom(isNearBottom(el));
    });
    ro.observe(content);
    ro.observe(el);
    return () => ro.disconnect();
  }, [selectedId]);

  // A phone keyboard does not resize the page: it covers the bottom of it, and the browser pans
  // what is left (the visual viewport) over the page to keep the focused field in sight. So the
  // app is drawn exactly where the visual viewport is: its height, and its offset down the page.
  // Following the height alone is not enough. Safari pans to the field before the app has shrunk,
  // and stays panned: the app, still at the top of the page, shows only its lower part at the top
  // of the screen, with bare page under it down to the keyboard.
  // Pinch zoom moves and shrinks the visual viewport too, and that is not the keyboard.
  //
  // This is the one keyboard mechanism: never add interactive-widget=resizes-content to the
  // viewport meta as well, or where it is honoured the keyboard is taken off twice.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const apply = () => {
      if (Math.abs(vv.scale - 1) >= 0.01) return;
      const root = document.documentElement.style;
      root.setProperty('--vvh', `${vv.height}px`);
      root.setProperty('--vvt', `${vv.pageTop}px`);
      // With the keyboard up the home indicator is under it, so the bottom inset is no space to
      // keep clear: kept, it shows as a band between the composer and the keyboard.
      if (document.documentElement.clientHeight - vv.height > 120) root.setProperty('--safe-bottom', '0px');
      else root.removeProperty('--safe-bottom');
    };
    apply();
    vv.addEventListener('resize', apply);
    vv.addEventListener('scroll', apply);
    // The closing keyboard's last report can be lost: the pan-back can land without a final
    // scroll event, and a keyboard closed while the page was hidden (app switch, lock, another
    // tab) reports nothing at all. The app then stays drawn shrunk and panned, and the edge
    // colour below fills most of the screen. Re-read the viewport when focus leaves a field
    // and when the page comes back, and keep re-reading briefly, past the close animation.
    const timers: number[] = [];
    const settle = () => {
      timers.forEach((t) => clearTimeout(t));
      timers.length = 0;
      for (const ms of [0, 250, 700]) timers.push(window.setTimeout(apply, ms));
    };
    const onVisible = () => { if (!document.hidden) settle(); };
    document.addEventListener('focusout', settle);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pageshow', settle);
    window.addEventListener('orientationchange', settle);
    return () => {
      timers.forEach((t) => clearTimeout(t));
      vv.removeEventListener('resize', apply);
      vv.removeEventListener('scroll', apply);
      document.removeEventListener('focusout', settle);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('pageshow', settle);
      window.removeEventListener('orientationchange', settle);
    };
  }, []);

  // What a phone paints behind its status bar follows the page: the theme colour on older
  // systems, the page background on newer ones. With the list open the top of the screen is the
  // list, so both say the list's colour; otherwise a band of the wrong shade sits above it.
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('list-open', listOpen);
    root.classList.toggle('in-titlebar', inTitlebar);
    // The keyboard belongs to the conversation; it goes down when the conversation moves aside.
    if (listOpen && document.activeElement instanceof HTMLElement) document.activeElement.blur();
    // Read again whenever the look changes: by then the new palette is on the page.
    const colour = getComputedStyle(root).getPropertyValue(listOpen || inTitlebar ? '--sidebar' : '--bg').trim();
    if (colour) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', colour);
  }, [listOpen, inTitlebar, theme, appearance.palette]);

  // A scroll hands the position over to the user only when their hand caused it. Scroll events
  // also come from the pin itself, from the keyboard resizing the page and from content changing
  // height, and none of those may unpin the view. A gesture that scrolls nothing here (a sideways
  // swipe over a code block) unpins nothing either. Scrolling back near the bottom pins again.
  const gestureProps = {
    onWheel: () => void (gesture.current.at = Date.now()),
    onTouchMove: () => void (gesture.current.at = Date.now()),
    onTouchEnd: () => void (gesture.current.at = Date.now()),
    onKeyDown: () => void (gesture.current.at = Date.now()),
    onPointerDown: () => void (gesture.current = { held: true, at: Date.now() }),
    onPointerUp: () => void (gesture.current = { held: false, at: Date.now() }),
    onPointerCancel: () => void (gesture.current = { held: false, at: Date.now() }),
  };

  const onScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    const near = isNearBottom(el);
    const byHand = gesture.current.held || Date.now() - gesture.current.at < GESTURE_GRACE_MS;
    if (near) stickToBottom.current = true;
    else if (byHand) stickToBottom.current = false;
    setAtBottom(near);
  };

  // The turn a search led to: shown once it is on the page, which for an old turn means fetching back to it.
  const [jump, setJump] = useState<{ threadId: string; turnId: string } | null>(null);
  useEffect(() => {
    if (!jump || jump.threadId !== selectedId || eventsLoading) return;
    const scope = scrollerRef.current?.querySelector(`[data-history-thread="${CSS.escape(jump.threadId)}"]`) ?? scrollerRef.current;
    const row = scope?.querySelector<HTMLElement>(`[data-turn="${CSS.escape(jump.turnId)}"]`);
    if (row) {
      stickToBottom.current = false;
      setAtBottom(false);
      row.scrollIntoView({ block: 'start' });
      row.classList.add('found');
      setTimeout(() => row.classList.remove('found'), 2200);
      setJump(null);
      return;
    }
    // Loaded but not drawn yet: the next render has it.
    if (events.some((e) => e.threadId === jump.threadId && e.turnId === jump.turnId)) return;
    // A thread opens at its end; an old turn is pages back, fetched until it is there.
    if (events.length === 0 || events[0].threadId !== jump.threadId) return;
    stickToBottom.current = false;
    if (hasOlder) { if (!loadingOlder) void loadOlder(); }
    else setJump(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump, selectedId, events, hasOlder, loadingOlder, eventsLoading]);

  const jumpToBottom = () => {
    const el = scrollerRef.current;
    if (!el) return;
    stickToBottom.current = true;
    setAtBottom(true);
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  };

  // The agent and directory the composer is talking to: the thread's, or the draft's for a new thread.
  const activeAgent = selected?.agent ?? draftAgent;
  const activeCwd = selected?.cwd ?? draftCwd;
  const commandsKey = `${activeAgent}\n${activeCwd}`;
  const lastFocusModelRefresh = useRef<{ agent: string; at: number } | null>(null);
  const refreshActiveModels = useCallback((force = false) => {
    if (!api || !activeAgent || document.hidden) return;
    const now = Date.now();
    if (!force && lastFocusModelRefresh.current?.agent === activeAgent && now - lastFocusModelRefresh.current.at < 1000) return;
    lastFocusModelRefresh.current = { agent: activeAgent, at: now };
    const version = modelVersions.current[activeAgent] ?? 0;
    void api
      .models(activeAgent)
      .then((list) => {
        if ((modelVersions.current[activeAgent] ?? 0) === version) setModelsByAgent(prev => ({ ...prev, [activeAgent]: list }));
      })
      .catch(() => undefined);
  }, [api, activeAgent]);

  // Models for that agent, loaded once per agent.
  useEffect(() => {
    if (!api || !activeAgent || modelsByAgent[activeAgent]) return;
    const version = modelVersions.current[activeAgent] ?? 0;
    api
      .models(activeAgent)
      .then((list) => {
        if ((modelVersions.current[activeAgent] ?? 0) === version) setModelsByAgent(prev => ({ ...prev, [activeAgent]: list }));
      })
      .catch(handleError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, activeAgent]);

  // Model load is a live hint; refresh when the user comes back to the page.
  useEffect(() => {
    const onFocus = () => refreshActiveModels();
    const onVisible = () => { if (!document.hidden) refreshActiveModels(); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refreshActiveModels]);

  // Slash commands depend on the directory (projects add their own). Claude only announces them
  // once a session has started, so the list is re-read whenever a turn finishes as well.
  // Load an initial list even during a turn, so a freshly opened page can offer queued commands.
  useEffect(() => {
    if (!api || !activeAgent || (running && commandsByKey[commandsKey])) return;
    api
      .commands(activeAgent, activeCwd || undefined)
      .then((list) => setCommandsByKey((prev) => ({ ...prev, [commandsKey]: list })))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, commandsKey, running]);

  const loadImage = useCallback(
    (attachmentId: string) => {
      if (!api || !selectedId) return Promise.reject(new Error('no thread'));
      return api.attachment(selectedId, attachmentId);
    },
    [api, selectedId],
  );

  const loadFile = useCallback(
    (path: string) => {
      if (!api || !selectedId) return Promise.reject(new Error('no thread'));
      return api.threadFile(selectedId, path);
    },
    [api, selectedId],
  );

  useEffect(() => {
    const onVisibility = () => setPageHidden(document.hidden);
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  // The thread on screen is being read. With the page in the background, nothing is.
  const viewingId = pageHidden ? null : selectedId;

  // Say so when something happens in a thread that is not on screen, and keep a count in the tab title.
  useEffect(() => {
    const before = seenThreads.current;
    seenThreads.current = threads;
    document.title = pageTitle(baseTitle.current, attentionCount(threads, viewingId));
    if (!before) return;
    const notice = threadNotices(before, threads, viewingId).at(-1);
    if (notice) {
      setToast({
        message: notice.message,
        action: {
          label: notice.action,
          run: () => {
            setSelectedId(notice.threadId);
            setDrawer(false);
          },
        },
      });
    }
  }, [threads, viewingId]);

  // Looking at a thread marks it read, on every device.
  const selectedUnread = Boolean(selected?.unread);
  useEffect(() => {
    if (!api || !selectedId || !selectedUnread || pageHidden) return;
    api
      .updateThread(selectedId, { unread: false })
      .then((updated) => setThreads((prev) => prev.map((t) => (t.id === updated.id ? updated : t))))
      .catch(() => undefined);
  }, [api, selectedId, selectedUnread, pageHidden]);

  /** Shows a thread that was just made here: forked, or brought back from the archive. */
  const openThread = (thread: Thread, draft?: string) => {
    if (draft !== undefined) prefillComposerDraft(hostId ? `host:${hostId}:${thread.id}` : thread.id, draft);
    setThreads((prev) => [thread, ...prev.filter((t) => t.id !== thread.id)]);
    setSelectedId(thread.id);
    setView('thread');
    setDrawer(false);
    void refreshThreads(); // the service decides the order
  };

  const threadActions = useThreadActions({
    api,
    agents,
    sections,
    todos,
    onTodosChange: reloadTodos,
    onThreadOpen: openThread,
    onMarkedUnread: (thread) => {
      if (thread.id === selectedId) setSelectedId(null);
    },
    onThreadChange: (updated) => {
      setThreads((prev) => {
        if (updated.archivedAt) return prev.filter((t) => t.id !== updated.id);
        return prev.some((t) => t.id === updated.id) ? prev.map((t) => (t.id === updated.id ? updated : t)) : [updated, ...prev];
      });
      if (updated.archivedAt && selectedId === updated.id) setSelectedId(null);
      void refreshThreads(); // the service decides the order
    },
    onThreadRemove: (id) => {
      setThreads((prev) => prev.filter((t) => t.id !== id));
      if (selectedId === id) setSelectedId(null);
    },
    onSectionsChange: () => {
      api?.sections().then(setSections).catch(handleError);
      void refreshThreads();
    },
    onError: handleError,
    toast: setToast,
    // The open thread is already in memory; copying it straight away keeps the browser's
    // "this came from a click" window, which the plain-http copy fallback depends on.
    // Only when all of it is: a long thread has older turns that were never loaded, and the copy
    // must not stop where the screen does.
    markdownOf: async (thread) => {
      if (thread.id === selectedId && !hasOlder) return toMarkdown(thread.title, timeline.items);
      if (!api) return '';
      return toMarkdown(thread.title, buildTimeline(await api.allEvents(thread.id)).items);
    },
  });

  // A new thread is created by its first message.
  const createDraftThread = async (isNewThread: boolean) => {
    const t = await api.createThread({
      agent: draftAgent,
      cwd: draftCwd,
      permissionMode: draftPermission,
      workMode: draftWorkMode,
      model: draftModel,
      effort: draftEffort,
      speed: draftSpeed,
      ...(draftSectionId && isNewThread ? { sectionId: draftSectionId } : {}),
      ...(draftScratch ? { scratch: true } : draftWorktree && offer ? { worktree: true } : {}),
    });
    const id = t.id;
    setSelectedId(id);
    setDraftSectionId(null);
    // Every worktree is a new branch; the next thread starts in place unless asked again.
    setDraftWorktree(false);
    return id;
  };

  const send = async (text: string, images: PreparedImage[]) => {
    setError(null);
    try {
      let id = selectedId;
      if (!id) id = await createDraftThread(true);
      stickToBottom.current = true;
      // "queue" only matters while a turn is running: then the message waits instead of being refused.
      await api.startTurn(
        id,
        text,
        images.map(({ data, mimeType }) => ({ data, mimeType })),
        { queue: true },
      );
      void refreshThreads();
    } catch (e) {
      // The composer keeps the draft.
      handleError(e);
      throw e;
    }
  };

  // Stopping means stopping: messages that were waiting are not sent. Their text goes back in the box.
  const stop = () => {
    if (!selectedId) return;
    api
      .cancel(selectedId)
      .then(({ dropped }) => {
        if (dropped.length) setRestore({ text: dropped.join('\n\n'), nonce: Date.now() });
      })
      .catch(handleError);
  };

  const removeQueued = (queuedId: string) => {
    if (selectedId) api.removeQueued(selectedId, queuedId).then(refreshThreads).catch(handleError);
  };

  const editQueued = async (queuedId: string) => {
    if (!selectedId) throw new Error(t('composer.noThread'));
    const taken = await api.removeQueued(selectedId, queuedId);
    refreshThreads();
    return taken;
  };

  const sendQueuedNow = (queuedId: string) => {
    if (selectedId) api.sendQueuedNow(selectedId, queuedId).then(refreshThreads).catch(handleError);
  };

  const decide = (approvalId: string, optionId: string | null) => {
    const body = optionId === null ? { cancel: true as const } : { optionId };
    api.decide(approvalId, body).catch(handleError);
  };

  const pick = (id: string) => {
    setSelectedId(id);
    setDrawer(false);
  };

  // The desk takes the place of a thread, so no thread is open while it is.
  const openDesk = () => {
    setSelectedId(null); setView('desk'); setDrawer(false);
  };

  const newThread = (opts: { cwd?: string; sectionId?: string | null; scratch?: boolean } = {}) => {
    setSelectedId(null);
    setView('thread');
    setDrawer(false);
    if (opts.cwd !== undefined) {
      setDraftCwd(opts.cwd);
      setDraftScratch(false);
    }
    if (opts.scratch) setDraftScratch(true);
    setDraftSectionId(opts.sectionId ?? null);
  };

  // A turn a search found: its thread is opened and the conversation is brought to that turn. An
  // archived thread comes back out of the archive, as it does when opened from anywhere else.
  const openHit = async (hit: SearchHit) => {
    if (!api) return;
    try {
      let thread = threads.find((t) => t.id === hit.threadId) ?? (await api.thread(hit.threadId));
      if (thread.archivedAt) {
        thread = await api.updateThread(hit.threadId, { archived: false });
        setToast({ message: t('app.unarchived') });
      }
      openThread(thread);
      setJump({ threadId: hit.threadId, turnId: hit.turnId });
    } catch (e) {
      handleError(e);
    }
  };

  useEffect(() => {
    if (!threadLink) return;
    if (hostId) { onHost?.(null); return; }
    let active = true;
    let id: string;
    try { id = decodeURIComponent(threadLink.slice('#/thread/'.length)); }
    catch { id = ''; }
    void api.thread(id).then(async thread => {
      if (!active) return;
      if (thread.archivedAt) thread = await api.updateThread(id, { archived: false });
      if (active) openThread(thread);
    }).catch(() => {
      if (active) setToast({ message: t('app.threadNotFound') });
    }).finally(() => {
      if (active) {
        setThreadLink(null);
        window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
      }
    });
    return () => { active = false; };
  }, [api, hostId, threadLink]);

  const agentLabel = (id: string) => agents.find((a) => a.id === id)?.label ?? id;
  const capsOf = (id: string) => agents.find((a) => a.id === id)?.capabilities;
  const activeCaps = capsOf(activeAgent);
  const models = modelsByAgent[activeAgent] ?? [];
  const permissionModes = activeCaps?.permissionModes ?? [];
  const effortLevels = activeCaps?.effortLevels ?? [];

  // Settings shown in the composer: the thread's own, or the draft's until the first message is sent.
  const workMode = selected?.workMode ?? (selected ? 'execute' : draftWorkMode);
  const changeWorkMode = async (mode: WorkMode, execute = false) => {
    if (!selected) { setDraftWorkMode(mode); return; }
    const id = selected.id;
    setModeBusy(true);
    try {
      if ((selected.workMode ?? 'execute') !== mode) {
        const updated = await api.updateThread(id, { workMode: mode });
        setThreads(prev => prev.map(t => t.id === id ? updated : t));
      }
      if (execute) await api.startTurn(id, t('app.executePlan'));
    } catch (error) { handleError(error); }
    finally { setModeBusy(false); }
  };

  const permission = selected ? selected.permissionMode : draftPermission;
  const model = selected ? selected.model : draftModel;
  const effort = selected ? selected.effort : draftEffort;
  const speed = selected ? selected.speed ?? null : draftSpeed;

  const updateSelected = (patch: { speed?: string | null; permissionMode?: string; model?: string | null; effort?: string | null }) => {
    if (!selected) return;
    // Shown at once: a control that waits for the round trip snaps back to the old value first.
    const id = selected.id;
    const before = { permissionMode: selected.permissionMode, model: selected.model, effort: selected.effort, speed: selected.speed };
    setThreads((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
    api
      .updateThread(id, patch)
      .then((updated) => setThreads((prev) => prev.map((t) => (t.id === updated.id ? updated : t))))
      .catch((e) => {
        setThreads((prev) => prev.map((t) => (t.id === id ? { ...t, ...before } : t)));
        handleError(e);
      });
  };

  const pickAgent = (id: string) => {
    setDraftAgent(id);
    setDraftWorkMode('execute');
    setDraftSpeed(null);
    setDraftModel(null);
    setDraftEffort(null);
    // Each agent starts at its own level: one that cannot ask starts where it can work.
    const caps = capsOf(id);
    if (caps) setDraftPermission(startingPermission(caps));
  };

  // What the slash menu offers besides the agent's own commands. The keywords include the names
  // these have in the agents' terminals (/clear, /resume, /export, /status), so habits still work.
  const hubActions: HubAction[] = [
    { id: 'new', label: t('sidebar.newThread'), hint: t('app.cmd.new'), keywords: ['new', 'clear'] },
    { id: 'status', label: t('statusPanel.status'), hint: t('app.cmd.status'), keywords: ['status', 'info'] },
    ...(models.length || effortLevels.length
      ? [{ id: 'model', label: t('pickers.modelAndEffort'), hint: modelName(models, model), keywords: ['model', 'effort', 'thinking'] }]
      : []),
    ...(permissionModes.length
      ? [{ id: 'permission', label: t('statusPanel.permission'), hint: PERMISSION_LABELS[permission]?.label ?? '', keywords: ['permission', 'permissions', 'approvals', 'mode'] }]
      : []),
    ...(selected
      ? [
          { id: 'rename', label: t('actions.rename'), hint: t('app.cmd.rename'), keywords: ['rename', 'name'] },
          { id: 'pin', label: selected.pinned ? t('actions.unpin') : t('actions.pin'), hint: t('app.cmd.pin'), keywords: ['pin'] },
          ...(activeCaps?.fork && !forkBlocked(selected)
            ? [{ id: 'fork', label: t('sidebar.fork'), hint: t('app.cmd.fork'), keywords: ['fork', 'branch', 'clone'] }]
            : []),
          { id: 'export', label: t('app.cmd.exportLabel'), hint: t('app.cmd.export'), keywords: ['export', 'copy'] },
          { id: 'archive', label: t('actions.archive'), hint: t('app.cmd.archive'), keywords: ['archive'] },
        ]
      : []),
    { id: 'appearance', label: t('appearance.title'), hint: t('app.cmd.appearance'), keywords: ['theme', 'appearance', 'dark', 'light', '主题'] },
    { id: 'help', label: t('app.cmd.helpLabel'), hint: t('app.cmd.help'), keywords: ['help', 'commands'] },
  ];

  const runHubAction = (id: string) => {
    if (id === 'new') return newThread();
    if (id === 'status') return setStatusOpen(true);
    if (id === 'export' && selected) return threadActions.copyConversation(selected);
    if (id === 'model') return setModelSignal((n) => n + 1);
    if (id === 'permission') return setPermissionSignal((n) => n + 1);
    if (id === 'appearance') return setAppearanceOpen(true);
    if (selected && (id === 'rename' || id === 'pin' || id === 'fork' || id === 'archive')) threadActions.run(id, selected);
  };

  // The hub has its own controls for these; going through the agent would leave them out of sync.
  // The hub's own goal entries (the card, or the menu commands on agents without it) replace the
  // agent's raw /goal everywhere, as the phone's compact menu already did.
  const hiddenCommands = [...(models.length ? ['model'] : []), ...(effortLevels.length ? ['effort'] : []), ...(activeCaps?.quota ? ['usage'] : []), ...(activeCaps?.goal ? ['goal'] : [])];

  const attachBlocked = !activeCaps?.images ? t('app.noImages', agentLabel(activeAgent)) : imagesBlocked(models, model);

  // The top bar names a thread's agent and directory, so the composer does not repeat them: its
  // strip is only for choosing them, before the thread exists.
  const header = selected ? null : (
    <>
      <AgentPicker api={api} agents={agents} value={draftAgent} onChange={pickAgent} />
      <ProjectPicker
        projects={projectOptions}
        machineLabel={host?.label ?? (hostId ? t('app.remoteMachine') : t('hosts.local'))}
        remote={Boolean(hostId)}
        cwd={draftCwd}
        scratch={draftScratch}
        scratchAvailable={scratchAvailable}
        onPick={(cwd) => {
          setDraftCwd(cwd);
          setDraftScratch(false);
        }}
        onScratch={() => setDraftScratch(true)}
        onBrowse={() => setShowDirPicker(true)}
        onNewProject={() => setProjectDialog('new')}
        onManageRoots={() => setProjectDialog('roots')}
      />
      {onHost && <MachinePicker
        hosts={hosts}
        current={hostId}
        onPick={(id) => { if (id !== hostId) onHost(id); }}
      />}
      {offer && !draftScratch && <WorkspacePicker branch={offer.branch} changes={offer.changes} worktree={draftWorktree} onChange={setDraftWorktree} />}
      {draftSectionId && sections.some((s) => s.id === draftSectionId) && (
        <button
          className="context-chip"
          title={t('app.sectionChip')}
          onClick={() => setDraftSectionId(null)}
        >
          <IconList size={13} />
          {sections.find((s) => s.id === draftSectionId)?.name}
          <IconClose size={12} />
        </button>
      )}
    </>
  );

  const left = (<>
    <PermissionPicker
      modes={permissionModes}
      value={permission}
      canAsk={activeCaps?.approvals ?? true}
      openSignal={permissionSignal}
      onChange={(m) => (selected ? updateSelected({ permissionMode: m }) : setDraftPermission(m))}
    />
  </>);

  // Older daemons advertise /compact before they expose the dedicated capability.
  const canCompact = activeCaps?.compact ?? (commandsByKey[commandsKey] ?? []).some((command) => command.name === 'compact' && command.kind !== 'skill');

  const right = (
    <>
      {selected && (timeline.usage || canCompact) && <UsageMeter
        usage={timeline.usage}
        onCompact={canCompact ? () => send('/compact', []) : undefined}
        running={running}
        compactQueued={selected.queued?.some((message) => message.text.trim() === '/compact')}
        compactDisabled={(selected.queued?.length ?? 0) >= MAX_QUEUED}
      />}
      <ModelPicker
      models={models}
      model={model}
      efforts={effortLevels}
      effort={effort}
      speed={speed}
      onSpeed={activeCaps?.speed ? v => selected ? updateSelected({ speed: v }) : setDraftSpeed(v) : undefined}
      openSignal={modelSignal}
      onOpen={() => refreshActiveModels(true)}
      onModel={(v) => {
        if (selected) return updateSelected({ model: v }); // the service drops an effort the new model lacks
        setDraftModel(v);
        if (draftSpeed && !speedsForModel(models, v).some(s => s.id === draftSpeed)) setDraftSpeed(null);
        if (draftEffort && !effortsForModel(models, v, effortLevels).includes(draftEffort)) setDraftEffort(null);
      }}
      onEffort={(v) => (selected ? updateSelected({ effort: v }) : setDraftEffort(v))}
      />
    </>
  );

  return (
    <div
      className={`app${listOpen ? ' drawer-open' : ''}${sidebarHidden && !compact ? ' sidebar-hidden' : ''}${sidebarDrag ? ' sidebar-resizing' : ''}`}
      style={{ '--sidebar-w': `${sidebarWidth}px` } as CSSProperties}
    >
      {showDirPicker && (
        <DirectoryPicker
          api={api}
          roots={workdirs}
          selected={draftCwd}
          machine={host?.label ?? (hostId ? t('app.remoteMachine') : t('hosts.local'))}
          onRootsChange={applyWorkdirs}
          onManageRoots={() => {
            setShowDirPicker(false);
            setProjectDialog('roots');
          }}
          onSelect={(cwd) => {
            setDraftCwd(cwd);
            setDraftScratch(false);
          }}
          onClose={() => setShowDirPicker(false)}
        />
      )}
      {projectDialog === 'new' && (
        <NewProjectDialog
          api={api}
          roots={workdirs}
          onCancel={() => setProjectDialog(null)}
          onCreated={(path) => {
            setProjectDialog(null);
            setDraftCwd(path);
            setDraftScratch(false);
            setToast({ message: t('app.projectCreated', folderName(path)) });
          }}
        />
      )}
      {projectDialog === 'roots' && <RootsDialog api={api} roots={roots} onChange={applyWorkdirs} onCancel={() => setProjectDialog(null)} />}
      <aside className="sidebar">
        <Sidebar
          threads={listedThreads}
          sections={sections}
          agents={agents}
          selectedId={selectedId}
          onSelect={pick}
          onNew={() => newThread()}
          onNewIn={newThread}
          compact={compact}
          onSearch={api ? (q) => api.search(q) : undefined}
          onOpenHit={(hit) => void openHit(hit)}
          onOpenSettings={(x, y) => setSettingsAt({ x, y })}
          hosts={hosts}
          hostId={hostId}
          onHost={onHost && ((id) => {
            setAllHosts(false);
            if (id !== hostId) onHost(id, 'list');
          })}
          hostDown={Boolean(host) && !hostUp}
          loading={!threadsKnown}
          merged={merging ? [
            { hostId, label: host?.label ?? t('hosts.local'), state: (Boolean(host) && !hostUp ? 'offline' : threadsKnown ? 'live' : 'loading') as HostSource['state'], at: observedAt, threads },
            ...elsewhere,
          ].sort((a, b) => (a.hostId === null ? -1 : b.hostId === null ? 1 : hosts.findIndex((h) => h.id === a.hostId) - hosts.findIndex((h) => h.id === b.hostId))) : null}
          onAll={hosts.length > 0 ? () => setAllHosts(true) : undefined}
          onOpenElsewhere={onHost && ((id, thread) => onHost(id, { thread }))}
          onHide={compact ? undefined : toggleSidebar}
          onThreadMenu={threadActions.openThreadMenu}
          onSectionMenu={threadActions.openSectionMenu}
          onNewSection={threadActions.newSection}
          onOpenDesk={openDesk}
          deskActive={onDesk}
        />
      </aside>
      {!compact && !sidebarHidden && (
        <div
          className="sidebar-resize-handle"
          role="separator"
          aria-label={t('sidebar.resize')}
          aria-orientation="vertical"
          aria-valuemin={SIDEBAR_MIN_WIDTH}
          aria-valuemax={SIDEBAR_MAX_WIDTH}
          aria-valuenow={sidebarWidth}
          tabIndex={0}
          title={t('sidebar.resize')}
          onPointerDown={startSidebarResize}
          onPointerMove={moveSidebarResize}
          onPointerUp={finishSidebarResize}
          onPointerCancel={cancelSidebarResize}
          onKeyDown={resizeSidebarFromKey}
        />
      )}

      {/* A conversation under way leans toward the middle of the screen, as in Codex; a new
          thread, and the pages, stay centred where they are. */}
      <main className={`main${selected && !onPage && hostUp && !compact ? ' lean' : ''}`} ref={mainRef}>
        {compact && selected && !listOpen && !onPage && hostUp && (
          <ThreadQuickSwitch threads={threads} selectedId={selected.id} agents={agents} onSelect={pick} />
        )}
        <header className="topbar">
          <button className="icon-btn menu-btn" aria-label={t('app.openList')} aria-expanded={listOpen} onClick={() => setDrawer((v) => !v)}>
            {compact ? <IconBack size={22} /> : <IconMenu />}
          </button>
          {/* With the list put away, the way back and a new thread move up here. */}
          {sidebarHidden && !compact && (
            <div className="topbar-lead">
              <button className="icon-btn" aria-label={t('app.showSidebar')} title={t('app.showSidebarKey', SIDEBAR_SHORTCUT)} onClick={toggleSidebar}>
                <IconSidebar />
              </button>
              <button className="icon-btn" aria-label={t('sidebar.newThread')} title={t('sidebar.newThread')} onClick={() => newThread()}>
                <IconEdit />
              </button>
            </div>
          )}
          <div className="topbar-heading">
            {/* Every page names itself here, the way a thread does: the bar is never left blank. */}
            {/* On a phone there is no room for the tag: the title itself opens where the thread runs. */}
            {selected && !viewTitle && !(host && !hostUp) && !compact && <ThreadPlace thread={selected} agent={agentLabel(selected.agent)} host={host?.label} />}
            {compact && selected && !viewTitle && !(host && !hostUp) ? (
              <ThreadPlace thread={selected} agent={agentLabel(selected.agent)} host={host?.label} title={selected.title} />
            ) : (
              <div className="topbar-title">{host && !hostUp ? host.label : viewTitle ?? (selected ? selected.title : t('sidebar.newThread'))}</div>
            )}
            {!selected && host && hostUp && <div className="topbar-sub">{host.label}</div>}
          </div>
          {selected && (
            <div className="topbar-actions">
              {topPlan && !onPage && <PlanButton item={topPlan} />}
              <button className="icon-btn topbar-new" aria-label={t('sidebar.newThread')} onClick={() => newThread()}>
                <IconEdit />
              </button>
              <button
                className="icon-btn topbar-more"
                aria-label={t('app.threadMore')}
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  threadActions.openThreadMenu(selected, rect.right - 200, rect.bottom + 6);
                }}
              >
                <IconMore />
              </button>
            </div>
          )}
        </header>

        {error && (
          <div className="banner" role="alert">
            <span>{error}</span>
            <button className="icon-btn" aria-label={t('app.dismiss')} onClick={() => setError(null)}>
              ×
            </button>
          </div>
        )}

        {host && !hostUp ? (
          // The machine's threads are over there; until it is back there is only this to say.
          <div key="host-down" className="scroller">
            <HostDown host={host} onRetry={() => api.reconnectHost(host.id).catch(() => undefined).then(reloadHosts)} />
          </div>
        ) : onDesk ? (
          <div key="desk" className="scroller">
            <DeskPage api={api} todos={todos} loading={todosLoading} error={todosError}
              refreshKey={todosKey} onReload={reloadTodos} onOpenThread={pick} />
          </div>
        ) : (
        <div key="thread" className="scroller" ref={scrollerRef} onScroll={onScroll} {...gestureProps}>
          {selected && <TurnRuler key={selected.id} threadId={selected.id} scrollerRef={scrollerRef} previews={turnPreviewMap} turnIds={rulerTurnIds} onJump={turnId => setJump({ threadId: selected.id, turnId })} />}
          {selected && eventsLoading && <div className="loading-note">{t('common.loading')}</div>}
          {selected ? (
            <ThreadView
              key={selected.id}
              historyApi={api ?? undefined}
              historyThreadId={selected.id}
              items={threadItems}
              revealTurnId={jump?.threadId === selected.id ? jump.turnId : undefined}
              roots={threadRoots}
              loadImage={loadImage}
              loadFile={loadFile}
              hasOlder={hasOlder}
              loadingOlder={loadingOlder}
              onLoadOlder={loadOlder}
              // Under each answer, like Codex: one click copies the thread up to there and opens the
              // copy. Only once there is a conversation to fork; a click while it cannot run says why.
              // The button appears the moment the log says a turn is over; the thread list hears of it
              // a beat later, so the log's word on whether a turn is running is the one to go by.
              onFork={
                selected.agentSessionId && activeCaps?.fork
                  ? (turnId) => threadActions.forkAt(timeline.running ? selected : { ...selected, status: 'idle' }, turnId)
                  : undefined
              }
              forkAnyTurn={Boolean(activeCaps?.forkAt)}
              revision={historyRevision}
              onEdit={
                selected.agentSessionId && activeCaps?.editMessage
                  ? async (turnId, edit) => {
                      stickToBottom.current = true;
                      await api.editTurn(selected.id, turnId, edit);
                    }
                  : undefined
              }
            />
          ) : (
            <div className="empty">
              <h2>{draftScratch ? t('app.emptyScratch') : t('app.emptyIn', folderName(draftCwd))}</h2>
            </div>
          )}
        </div>
        )}

        <div className="jump-anchor">
          {selected && !atBottom && (
            <button className="jump-btn" aria-label={t('app.toBottom')} onClick={jumpToBottom}>
              <IconArrowDown size={17} />
            </button>
          )}
        </div>

        {!onPage && !(host && !hostUp) && (
        <div className="composer-slot">
        <Composer
          draftKey={composerDraftKey}
          running={running}
          disabled={modeBusy || (!selected && !draftCwd)}
          placeholder={demo ? t('app.placeholder.demo') : workMode === 'plan' && !running ? t('composerMenu.planOn') : selected ? (running ? t('sideChat.queuePlaceholder') : t('app.placeholder.continue')) : draftScratch ? t('app.placeholder.scratch') : t('app.placeholder.startIn', folderName(draftCwd))}
          onSend={send}
          onStop={stop}
          header={header}
          planMode={activeCaps?.workModes?.includes('plan') ? { active: workMode === 'plan', disabled: running || modeBusy, onChange: active => void changeWorkMode(active ? 'plan' : 'execute') } : undefined}
          left={left}
          right={right}
          commands={commandsByKey[commandsKey] ?? []}
          goal={activeCaps?.goal}
          goalState={goalView}
          actions={hubActions}
          hiddenCommands={hiddenCommands}
          onAction={runHubAction}
          attachBlocked={attachBlocked}
          attachNote={imagesNote(models, model)}
          onError={setError}
          queued={selected?.queued ?? []}
          modelQueue={selected?.modelQueue ?? null}
          onRemoveQueued={removeQueued}
          onEditQueued={editQueued}
          onSendQueuedNow={sendQueuedNow}
          restore={restore}
          lastSent={selected ? lastSent : null}
        />
        {waitingOn && selected && (
          <ActionSheet
            key={waitingOn.target.item.key}
            target={waitingOn.target}
            index={1}
            total={waiting.length}
            roots={threadRoots}
            onAnswer={(id, answer) => api.answerInput(id, answer)}
            onDecide={decide}
            // The service has put the thread back in execute mode when the plan arrived; the call
            // below only switches it when that did not happen, then sends the go-ahead.
            onExecutePlan={running ? undefined : () => changeWorkMode('execute', true)}
            onRejectPlan={(text) => void api.startTurn(selected.id, text, [], { workMode: 'plan' })}
            onSkipPlan={() => {
              if (waitingOn.target.kind !== 'hub-plan' || !selectedId) return;
              setPlanSkip({ threadId: selectedId, key: waitingOn.target.item.key });
            }}
          />
        )}
        </div>
        )}
      </main>

      {statusOpen && (
        <StatusPanel
          thread={selected}
          agentLabel={agentLabel(activeAgent)}
          model={modelName(models, model)}
          permission={permissionModes.length ? permission : ''}
          directory={activeCwd}
          usage={selected ? timeline.usage : null}
          usageCommand={['usage', 'cost', 'session'].find((name) => (commandsByKey[commandsKey] ?? []).some((c) => c.name === name)) ?? null}
          onRunCommand={(text) => void send(text, []).catch(() => undefined)}
          onClose={() => setStatusOpen(false)}
        />
      )}
      {settingsAt && (
        <Menu
          label={t('sidebar.settings')}
          x={settingsAt.x}
          y={settingsAt.y}
          corner="bottom-right"
          onClose={() => setSettingsAt(null)}
          items={[
            {
              id: 'appearance',
              label: t('appearance.title'),
              icon: IconPalette,
              onSelect: () => {
                setAppearanceOpen(true);
                setDrawer(false);
              },
            },
            {
              id: 'language',
              label: t('locale.menu'),
              icon: IconLanguage,
              // Each language is named in itself, so it can be found from any of the others.
              submenu: [
                { id: 'locale:system', label: t('locale.system'), checked: localeChoice() === 'system', onSelect: () => chooseLocale('system') },
                'separator' as const,
                ...LOCALES.map((l) => ({ id: `locale:${l.id}`, label: l.name, checked: localeChoice() === l.id, onSelect: () => chooseLocale(l.id) })),
              ],
            },
          ]}
        />
      )}
      {appearanceOpen && (
        <AppearancePanel appearance={appearance} theme={theme} onChange={setAppearance} onClose={() => setAppearanceOpen(false)} />
      )}
      {threadActions.layer}
      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
}
