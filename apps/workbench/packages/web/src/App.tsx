import { AgentLoginRecovery, needsAgentLogin } from './components/AgentLoginRecovery';
import { createPortal } from 'react-dom';
import { GitPanel } from './components/GitPanel';
import { SettingsPage, type SettingsGroup } from './components/SettingsPage';
import { AssistantSettingsPage, assistantTitle } from './components/AssistantSettingsPage';
import { PageNavigation, SettingsSection, usePageNavigation } from './components/PageNavigation';
import { PushSettingsDialog } from './components/PushSettingsDialog';
import { LoopxNotifySettings } from './components/LoopxNotifySettings';
import { CommandPalette } from './components/CommandPalette';
import { isPaletteChord, type PaletteItem } from './command-palette';
import { browserAlertsWanted, browserPermission, newlyWaiting } from './loopx/notify';
import { FeishuSettingsDialog } from './components/FeishuSettingsDialog';
import { GeneralSettingsDialog } from './components/GeneralSettingsDialog';
import { AutoHandoverNotice, HandoverSettingsDialog } from './components/AutoHandover';
import { useWorkSources, WorkSummary } from './extensions/work';
import { HostIcon } from './extensions/ui';
import { historyPage, mergeHistory, mergeHistoryPage, peekCachedPage, prependHistory, refreshHistoryPage, watchHistory } from './history';
import { cachedRouterThreadId, rememberRouterThreadId } from './history-cache';
import { useReadingPosition } from './use-reading-position';
import type { WorkMode, WritingStage } from './api';
import { forkBlocked, useThreadActions } from './components/ThreadActions';import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ComponentProps, type ReactNode } from 'react';
import type { Theme } from './appearance';
import {
  Api,
  ApiError,
  AuthError,
  MAX_QUEUED,
  type AgentInfo,
  type ExternalSession,
  type PersonalSettings,
  type GeneralSettings,
  type GoalView,
  type HandoverTarget,
  type ModelOption,
  type Section,
  type SlashCommand,
  type Thread,
  type Todo,
  type Schedule,
  type WorkdirsConfig,
} from './api';
import type { HubAction } from './commands';
import { buildProjectOptions } from './projects';
import { rootRows, withWorkdirs } from './workdirs';
import { isBounce, useAppHistory } from './appHistory';
import { useAppearance, useInTitlebar, useIsCompact, useListSwipe, useMinWidth, useStoredFlag } from './hooks';
import { useModelPreferences } from './model-preferences';
import { useHosts } from './hosts';
import type { PreparedImage } from './images';
import { restoreVideos, type PreparedVideo } from './videos';
import { attentionCount, pageTitle } from './notices';
import { folderName } from './paths';
import { ThreadPlace } from './components/ThreadPlace';
import { PERMISSION_LABELS, PERMISSION_ORDER, permissionHint, startingPermission } from './permissions';
import { buildTimeline, toMarkdown, turnPreviews, type StoredEvent, type Timeline } from './timeline';
import { newerBuildAvailable } from './version';
import { AppearancePanel } from './components/AppearancePanel';
import { ActionSheet, type SheetTarget } from './components/ActionSheet';
import { ArchivePanel } from './components/ArchivePanel';
import { Composer, prefillComposerDraft, type DraftNote } from './components/Composer';
import { HandToAssistant, type MessageSnapshot } from './components/MessageActions';
import type { ContextReference } from './context-references';
import { CollaborationMenu } from './components/CollaborationMenu';
import { DirectoryPicker } from './components/DirectoryPicker';
import { Dialog } from './components/Dialog';
import { HostDown, HostsPanel, MachinePicker } from './components/Hosts';
import { Menu } from './components/Menu';
import { StatusPanel } from './components/StatusPanel';
import { TerminalPanel } from './components/TerminalPanel';
import { DesktopPanel } from './components/DesktopPanel';
import { FilesPanel, type FilesTarget } from './components/FilesPanel';
import { PreviewPanel } from './components/PreviewPanel';
import { DockPanel } from './components/DockPanel';
import { PictureDock } from './components/PictureDock';
import { handoverGroups } from './sidebar';
import { fileRef } from './files';
import { AddAgentDialog } from './components/AddAgentDialog';
import { OnboardingDialog } from './components/OnboardingDialog';
import { ObservabilityPage } from './components/ObservabilityPage';
import { MachineUsagePage } from './components/MachineUsagePage';
import { QuotaBoard } from './components/QuotaBoard';
import { QuotaNavigation, quotaPushed } from './quota-reading';
import { noteSource, SelectionMenu } from './components/SelectionMenu';
import { AgentPanel } from './components/AgentPanel';
import { memberProgress, ThreadOverview, type OverviewSection } from './components/ThreadOverview';
import { goalDestination, goalOverviewHash, goalOverviewRequest, goalsBoardTarget, type GoalSection } from './loopx/goal-navigation';
import { extensionStorage } from './extensions/host';
import { prepareOverview } from './overview-reading';
import { ChangeDiffDialog } from './components/ChangeDiffDialog';
import { AgentsPage } from './components/AgentsPage';
import type { AgentMention, ChangedFile, Coordination, MemberDefault, SearchHit } from './api';
import { Popover } from './components/Popover';
import { SideChat } from './components/SideChat';
import { McpPage } from './components/McpPage';
import { TeamsPage } from './components/TeamsPage';
import { teamRecipient } from './teams';
import type { PlanQuota, TeamSummary } from './api';
import { ExploreDialog } from './components/ExploreDialog';
import { needsExplore, exploreSummary } from './personal-explore';
import { PersonalSettingsDialog } from './components/PersonalSettingsDialog';
import { SkillsPage } from './components/SkillsPage';
import { SchedulesPage } from './components/SchedulesPage';
import { ConversationSkeleton } from './components/ConversationSkeleton';
import { AccessSettings } from './components/AccessSettings';
import { PwaInstallPromotion, PwaInstallSettings } from './components/PwaInstall';
import { LinkLoginNotice, useAuthKind } from './components/LinkLoginNotice';
import { StewardPage } from './components/StewardPage';
import { StewardOverview } from './components/StewardOverview';
import { useLoopxStatus } from './loopx/use-status';
import { needsYouGoalIds, sortGoalTree } from './loopx/status';
import { ActivityPage } from './components/ActivityPage';
import { LoopxGoalGroup } from './components/LoopxGoalGroup';
import { drivenByApp } from './loopx/session';
import { GoalConfirm, GoalPermissionPicker, LoopxToggle } from './components/LoopxGoal';
import { SendCancelled } from './drafts';
import { defaultGoalPermission, fetchGoalCapabilities, goalOf, type GoalAgentCapability, type GoalPermission } from './loopx/launch';
import { ExtensionsPage } from './components/ExtensionsPage';
import { VoiceOrb, VoiceControls } from './components/VoiceOrb';
import { useVoiceSession } from './voice-session';
import type { ExtensionNavEntry } from './components/Sidebar';
import type { ExtensionState } from '@calli/extension-api';
import type { ConnectionState, MachineState } from '@calli/extension-api/web';
import { ThreadPeek } from './components/ThreadPeek';
import { RemoteThreadMenu } from './extensions/RemoteThreadMenu';
import { ExtensionView } from './extensions/ExtensionView';
import { machineSummaries, threadMachine, parseThreadLink } from './extensions/project';
import { createThreadFeed } from './extensions/thread-feed';
import { cachedAgentMarks, cachedThreads, rememberAgentMarks, rememberThreads } from './thread-cache';
import { cachedAgents, cachedDraft, cachedModels, rememberAgents, rememberDraft, rememberModels } from './host-cache';
import { clampSidebarWidth, defaultSidebarWidth, projectDir, savedSidebarWidth, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, type HostSource } from './sidebar';
import { enabledViews, extensionHash, looksLikeExtensionRoute, resolveExtensionRoute } from './extensions/routes';
import { AgentPicker, type DiscoveredAccount, type DiscoveredAgent, effortsForModel, imagesBlocked, imagesNote, modelName, resolveModel, speedsForModel, ModelPicker, PermissionPicker, UsageMeter, WorkspacePicker } from './components/Pickers';
import { Sidebar, SIDEBAR_SHORTCUT } from './components/Sidebar';
import { MergedHistory } from './components/MergedHistory';
import { currentPlan, PlanButton } from './components/PlanPanel';
import { HandoverComposer } from './components/HandoverComposer';
import { WritingBar } from './components/WritingBar';
import { handoverHistoryKey, laterSegment, latestHandoverThread, overviewHistoryBoundary } from './handover';
import { TurnRuler } from './components/TurnRuler';
import { NewProjectDialog, ProjectPicker, RootsDialog } from './components/Projects';
import { NoteMarks, type NoteMark } from './components/NoteMarks';
import { ConnectDialog, DemoBar } from './components/ConnectDialog';
import { DemoApi, DemoError } from './demo/DemoApi';
import { EMPTY_MODEL_PREFERENCE, type DraftModelPreference } from '../../daemon/src/model-preferences';
import { QUICK_SWITCH_HIDDEN_KEY, clampQuickSwitchBottom, savedQuickSwitchBottom, saveQuickSwitchBottom } from './quick-switch';
import { IconDownload, IconCheck, IconBell, AgentMark, IconActivity, IconAgents, IconArchive, IconArrowLeft, IconArrowDown, IconBack, IconClose, IconEdit, IconFolder, IconGauge, IconGlobe, IconList, IconMenu, IconSettings, IconMore, IconOverview, IconPalette, IconPlus, IconPuzzle, IconServer, IconShield, IconSidebar, IconSignIn, IconSignOut, IconSparkle, IconTerminal, IconTrace, IconLanguage, IconWaveform, IconSideChat } from './components/icons';
import { StatReadings } from './components/SessionStats';
import { ThreadTracePanel } from './components/TracePanel';
import { chooseLocale, locale, LOCALES, localeChoice, t } from './i18n';

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

/** How often, at most, the page asks whether a newer build is being served. */
const UPDATE_CHECK_MS = 30_000;

/**
 * How often the plan-exhausted card retries the quota-aware takeover target while no target is
 * available: quota windows move fast enough that the next sample may have one again.
 */
const TAKEOVER_RETRY_MS = 30_000;

const timelineCache = new WeakMap<StoredEvent[], Timeline>();
function buildCachedTimeline(events: StoredEvent[], speech: Parameters<typeof buildTimeline>[1]): Timeline {
  if (speech?.length) return buildTimeline(events, speech);
  const cached = timelineCache.get(events);
  if (cached) return cached;
  const timeline = buildTimeline(events);
  timelineCache.set(events, timeline);
  return timeline;
}

const isNearBottom = (el: HTMLElement) => el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;

/** The plan page's address. Threads have none: the main area showing a thread is the plain URL. */
const TODOS_HASH = '#/todos';
const DESK_HASH = '#/desk';
const STEWARD_HASH = '#/steward';
const SKILLS_HASH = '#/skills';
const TEAMS_HASH = '#/teams';
const AGENTS_HASH = '#/agents';
const MCP_HASH = '#/mcp';
const SCHEDULES_HASH = '#/schedules';
const ACTIVITY_HASH = '#/activity';
const OBSERVABILITY_HASH = '#/observability';
const MACHINES_HASH = '#/machines';
const EXTENSIONS_HASH = '#/extensions';
/** The hub's own pages. Any other `#/…` may be an extension's view; see extensions/routes.ts. */
const CORE_ROUTES = ['#/settings', AGENTS_HASH, TEAMS_HASH, STEWARD_HASH, '#/goals', SKILLS_HASH, MCP_HASH, SCHEDULES_HASH, ACTIVITY_HASH, OBSERVABILITY_HASH, MACHINES_HASH, EXTENSIONS_HASH];

type MainView = 'settings' | 'agents' | 'teams' | 'thread' | 'steward' | 'schedules' | 'activity' | 'observability' | 'machines' | 'skills' | 'mcp' | 'extensions' | 'ext';

function quotaAccount(path: string): string | undefined {
  if (!path.startsWith('quota/')) return undefined;
  try { return decodeURIComponent(path.slice('quota/'.length)); } catch { return path.slice('quota/'.length); }
}

/** The page an address opens; null for none (a thread). Skills and MCP are the hub's own machinery, which the demo has none of. */
function viewForHash(hash: string, connected: boolean): MainView | null {
  if (hash.startsWith('#/schedules/')) return 'schedules';
  if (hash.startsWith('#/machines/')) return 'machines';
  if (hash === '#/settings' || hash.startsWith('#/settings/')) return 'settings';
  if (hash.startsWith('#/thread/')) return null;
  if (goalOverviewRequest(hash)) return 'steward';
  // The assistant desk and its matters list are gone; their addresses open the steward, their successor.
  if (hash === DESK_HASH || hash.startsWith(`${DESK_HASH}/`) || hash === TODOS_HASH) return 'steward';
  switch (hash) {
    case STEWARD_HASH:
      return 'steward';
    case SCHEDULES_HASH:
      return 'schedules';
    case ACTIVITY_HASH:
      return 'activity';
    case OBSERVABILITY_HASH:
      return connected ? 'observability' : null;
    case MACHINES_HASH:
      return 'machines';
    case AGENTS_HASH:
      return connected ? 'agents' : null;
    case TEAMS_HASH:
      return connected ? 'teams' : null;
    case SKILLS_HASH:
      return connected ? 'skills' : null;
    case MCP_HASH:
      return connected ? 'mcp' : null;
    case EXTENSIONS_HASH:
      return 'extensions';
  }
  return looksLikeExtensionRoute(hash, CORE_ROUTES) ? 'ext' : null;
}

/** Whether the thread list is put away on a wide screen. */
const SIDEBAR_HIDDEN_KEY = 'calli.sidebar.hidden';
/** The width a person last dragged the desktop thread list to. */
const SIDEBAR_WIDTH_KEY = 'calli.sidebar.width';
const SIDEBAR_KEYBOARD_STEP = 16;

/**
 * How wide a window must be for the overview to stand beside the conversation instead of opening
 * over it. The panel needs 364px to the right of the column; making that room costs the column
 * 348px, and below this width what is left reads like a phone column (under 550px at the line),
 * so a narrower window gets the old popover, closed by default, instead.
 */
export const OVERVIEW_DOCK_MIN_WIDTH = 1240;
/** The overview's standing panel, remembered on this device: open until the person closes it. */
const OVERVIEW_OPEN_KEY = 'calli.overview.open';

/** What members of each instance start with, remembered on this device for the next team. */
const MEMBER_DEFAULTS_KEY = 'calli.coord.defaults';
/** A plan the user chose to leave be, by thread: the proposal's key, so a reload does not ask again. */
const PLAN_SKIP_KEY = 'calli:plan-skipped';

const QUICK_SWITCH_SIDE = 10;
function ThreadQuickSwitch({ threads, selectedId, agents, onSelect }: {
  threads: Thread[];
  selectedId: string;
  agents: AgentInfo[];
  onSelect: (id: string) => void;
}) {
  const selectedIndex = threads.findIndex((thread) => thread.id === selectedId);
  const items = useMemo(() => selectedIndex < 0 ? [] : threads.slice(Math.max(0, selectedIndex - QUICK_SWITCH_SIDE), Math.min(threads.length, selectedIndex + QUICK_SWITCH_SIDE + 1)), [threads, selectedIndex]);
  const root = useRef<HTMLDivElement>(null);
  const current = useRef<HTMLButtonElement | null>(null);
  const start = useRef<{ pointerId: number; x: number; y: number; bottom: number; moved: boolean } | null>(null);
  const [open, setOpen] = useState(false);
  const [hidden, setHidden] = useStoredFlag(QUICK_SWITCH_HIDDEN_KEY);
  const [bottom, setBottom] = useState(savedQuickSwitchBottom);
  const agentById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  // The panel is short: it opens on the current session, with its neighbours above and below.
  useLayoutEffect(() => { if (open) current.current?.scrollIntoView({ block: 'center' }); }, [open]);
  useEffect(() => {
    if (!open) return;
    // Dismissing is all a tap outside does: what lies under it is not pressed as well.
    const onPointerDown = (e: PointerEvent) => {
      if (!root.current || root.current.contains(e.target as Node)) return;
      setOpen(false);
      const swallow = (click: MouseEvent) => { click.stopPropagation(); click.preventDefault(); };
      document.addEventListener('click', swallow, { capture: true, once: true });
      window.setTimeout(() => document.removeEventListener('click', swallow, { capture: true }), 500);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);
  if (hidden || selectedIndex < 0 || items.length <= 1) return null;
  const release = (el: HTMLElement, pointerId: number) => {
    if (el.hasPointerCapture(pointerId)) el.releasePointerCapture(pointerId);
    start.current = null;
  };
  return (
    <div className="quick-switch" ref={root}>
      {/* A small visible handle near the right edge, inset from iOS's own edge gestures. Tap it or
          pull it left to open the panel; drag it up or down to move it out of the way of text. It
          covers only its own few pixels, so selecting text elsewhere is never intercepted. */}
      <button
        type="button"
        className={`quick-switch-handle${open ? ' open' : ''}`}
        style={{ bottom: `calc(${bottom}px + var(--safe-bottom))` }}
        aria-label={t('quickSwitch.sessions')}
        aria-expanded={open}
        onPointerDown={(e) => {
          if (e.pointerType === 'mouse' && e.button !== 0) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          start.current = { pointerId: e.pointerId, x: e.clientX, y: e.clientY, bottom, moved: false };
        }}
        onPointerMove={(e) => {
          const from = start.current;
          if (from?.pointerId !== e.pointerId) return;
          const dx = from.x - e.clientX;
          const dy = e.clientY - from.y;
          if (!from.moved && dx > 24 && dx > Math.abs(dy)) { release(e.currentTarget, e.pointerId); setOpen(true); return; }
          if (!from.moved && Math.abs(dy) > 8) from.moved = true;
          if (from.moved) setBottom(clampQuickSwitchBottom(from.bottom - dy));
        }}
        onPointerUp={(e) => {
          const from = start.current;
          if (from?.pointerId !== e.pointerId) return;
          release(e.currentTarget, e.pointerId);
          if (from.moved) saveQuickSwitchBottom(bottom);
          else setOpen((was) => !was);
        }}
        onPointerCancel={(e) => {
          if (start.current?.pointerId === e.pointerId) release(e.currentTarget, e.pointerId);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            const next = clampQuickSwitchBottom(bottom + (e.key === 'ArrowUp' ? 24 : -24));
            setBottom(next);
            saveQuickSwitchBottom(next);
          }
        }}
        onClick={(e) => { if (e.detail === 0) setOpen((was) => !was); }}
      ><IconArrowLeft size={12} /></button>
      {open && (
        <div className="quick-switch-panel" role="listbox" aria-label={t('quickSwitch.sessions')}>
          {items.map((thread) => {
            const agent = agentById.get(thread.agent);
            const isCurrent = thread.id === selectedId;
            const place = thread.scratch ? t('projects.none') : folderName(projectDir(thread).cwd);
            return (
              <button
                type="button"
                key={thread.id}
                ref={isCurrent ? current : undefined}
                className={`quick-switch-card${isCurrent ? ' current' : ''}`}
                role="option"
                aria-selected={isCurrent}
                onClick={() => {
                  setOpen(false);
                  if (!isCurrent) onSelect(thread.id);
                }}
              >
                <span className="quick-switch-title">{thread.title}</span>
                <span className="quick-switch-meta">
                  {agent?.icon && <AgentMark path={agent.icon} size={13} />}
                  <span>{agent?.label ?? thread.agent}</span>
                  <span>{place}</span>
                  {thread.status === 'running' && <span className="dot running" aria-label={t('status.running')} />}
                  {thread.waiting && <span className="dot waiting" aria-label={t('notices.unknown.label')} />}
                  {thread.unread && !isCurrent && <span className="dot unread" aria-label={t('sidebar.unread')} />}
                </span>
              </button>
            );
          })}
          <button type="button" className="quick-switch-hide" onClick={() => { setOpen(false); setHidden(true); }}>{t('quickSwitch.hide')}</button>
        </div>
      )}
    </div>
  );
}

function savedMemberDefaults(key: string): Record<string, MemberDefault> {
  try {
    const saved = JSON.parse(localStorage.getItem(key) ?? '{}');
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
  } catch {
    return {};
  }
}

function fitDraftModelPreference(value: DraftModelPreference, models: ModelOption[], effortLevels: string[], canSpeed: boolean): DraftModelPreference {
  const resolved = value.model && models.length > 0 ? resolveModel(models, value.model) : undefined;
  const impliedEffort = value.model && resolved && resolved.id !== value.model ? value.model.slice(resolved.id.length + 1) : null;
  const model = value.model && models.length > 0 ? (resolved?.id ?? null) : value.model;
  const levels = effortsForModel(models, model, effortLevels);
  const rawEffort = value.effort ?? impliedEffort;
  const effort = rawEffort && (levels.includes(rawEffort) || (models.length === 0 && effortLevels.length === 0)) ? rawEffort : null;
  const speeds = speedsForModel(models, model);
  const speed = canSpeed && value.speed && (models.length === 0 || speeds.some((option) => option.id === value.speed)) ? value.speed : null;
  return { model, effort, speed };
}

/** The model and thinking effort for one instance's members: a quiet mark until something is chosen. */
function MemberModel({ agent, models, value, permission, part, onNeed, onChange, onPermission }: {
  agent: AgentInfo; models: ModelOption[]; value: MemberDefault; onNeed: (agent: string, refresh?: boolean) => void; onChange: (value: MemberDefault) => void;
  /** The level in force, and how to change it; left out where the agent has no levels. */
  permission?: string; onPermission?: (mode: string) => void;
  /** One half only, drawn as the composer's own control rather than a quiet line: a member's box sits like the lead's. */
  part?: 'permission' | 'model';
}) {
  useEffect(() => { if (part !== 'permission') onNeed(agent.id); }, [agent.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const efforts = agent.capabilities.effortLevels ?? [];
  const quiet = part ? undefined : agent.label;
  return (<>
    {part !== 'model' && permission && onPermission && <PermissionPicker quietFor={quiet} modes={agent.capabilities.permissionModes} value={permission} canAsk={agent.capabilities.approvals ?? true} onChange={onPermission} />}
    {part !== 'permission' && <ModelPicker
      quietFor={quiet}
      catalog={agent.capabilities.modelCatalog}
      models={models}
      model={value.model}
      efforts={efforts}
      effort={value.effort}
      onOpen={() => onNeed(agent.id, true)}
      // An effort the newly picked model does not take goes back to the default with it.
      onModel={(model) => onChange({ ...value, model, effort: value.effort && effortsForModel(models, model, efforts).includes(value.effort) ? value.effort : null })}
      onEffort={(effort) => onChange({ ...value, effort })}
    />}
  </>);
}

/** Presentation overrides for the standalone, local-only product walkthrough. */
export interface Showcase {
  api: Api;
  initialThreadId: string | null;
  theme: Theme;
  notice: (threadId: string | null, view?: string) => ReactNode;
  intro: (threadId: string) => ReactNode;
  composer: (threadId: string | null) => ReactNode;
  onInstall: () => void;
}

interface AppProps {
  /** The hub's access token; empty shows the demo workspace, which talks to no daemon. */
  token: string;
  /** Connects (a token the hub accepted) or disconnects (empty, with why when it was not the user's doing). */
  onToken: (token: string, reason?: string) => void;
  /** Opens the connect dialog at once, saying why. */
  connect?: { reason?: string };
  /** The machine on screen: one of the hub's other machines, or null for the hub's own. */
  hostId?: string | null;
  /** Switches machines. The app starts over on the new one, with what the switch was made from still open. */
  onHost?: (id: string | null, start?: AppStart) => void;
  start?: AppStart;
  showcase?: Showcase;
}

/** What is open when the app starts over on another machine: the phone's thread list, the machines panel, or a thread of that machine. */
export type AppStart = 'list' | 'machines' | { thread: string } | { draft: { project?: string } };

export function App(props: AppProps) { return <PageNavigation><Hub {...props} /></PageNavigation>; }

function Hub({ token, onToken, connect, hostId = null, onHost, start, showcase }: AppProps) {
  const navigate = usePageNavigation();
  // Asking for the token, and why: what was being done, or why the old one stopped working.
  const [connectAsk, setConnectAsk] = useState<{ reason?: string } | null>(connect ?? null);
  // Looking at another machine, every call is answered by its daemon, through the hub.
  const { hosts, loaded: hostsLoaded, reload: reloadHosts } = useHosts(token);
  const host = hostId ? hosts.find((h) => h.id === hostId) : undefined;
  // Until the hub has said how the machine is, it is taken to be up: asking it is how the page finds out.
  const hostUp = !hostId || !hostsLoaded || host?.status === 'online';
  // A machine that comes back gets a new client, so everything is read from it again.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const api = useMemo<Api>(() => showcase?.api ?? (token ? new Api(token, '', hostId) : new DemoApi()), [token, hostId, hostUp, showcase?.api]);
  const demo = api.demo;
  // A machine that was removed (here or on another device) has nothing to show: back to the hub's own.
  useEffect(() => {
    if (hostId && hostsLoaded && !host) onHost?.(null, window.location.hash.startsWith('#/settings/machines') ? 'machines' : undefined);
  }, [hostId, hostsLoaded, host, onHost]);

  // What this device last read from a machine stands in until the machine answers, so nothing jumps
  // when the app starts over on it. Demo and showcase lists are not the hub's and are not kept.
  const keepThreads = Boolean(token) && !showcase;

  const [teamDefinitions, setTeamDefinitions] = useState<TeamSummary[]>([]);
  const reloadTeams = useCallback(() => { if (!api.demo) void api.teams().then(setTeamDefinitions).catch(() => setTeamDefinitions([])); }, [api]);
  useEffect(reloadTeams, [reloadTeams]);
  const [draftTeam, setDraftTeam] = useState<string | null>(null);
  useEffect(() => setDraftTeam(null), [api]);
  const [agents, setAgents] = useState<AgentInfo[]>(() => (keepThreads ? cachedAgents(hostId) ?? [] : []));
  /** Whether this machine's agents have been read: until then the sidebar marks rows from what this device last saw. */
  const [agentsKnown, setAgentsKnown] = useState(false);
  /** CLIs on PATH that look like extra agents, offered in the picker. */
  const [discovered, setDiscovered] = useState<DiscoveredAgent[]>([]);
  /** Accounts of multi-account agents already signed in here, offered in the picker. */
  const [accounts, setAccounts] = useState<DiscoveredAccount[]>([]);
  const [workdirs, setWorkdirs] = useState<string[]>([]);
  // Each project root and whether it was added from the page; and whether threads can be in no project.
  const [roots, setRoots] = useState<Array<{ path: string; removable: boolean }>>([]);
  const [scratchAvailable, setScratchAvailable] = useState(false);
  // A new thread in no project: the hub gives it a scratch folder. What this machine was last left
  // in opens there again, rather than snapping to its first choice.
  const [draftScratch, setDraftScratch] = useState(() => !(typeof start === 'object' && 'draft' in start && start.draft.project) && keepThreads && cachedDraft(hostId)?.scratch === true);
  const [projectDialog, setProjectDialog] = useState<'new' | 'roots' | null>(null);
  const applyWorkdirs = useCallback((c: WorkdirsConfig) => {
    setWorkdirs(c.workdirs);
    setRoots(rootRows(c));
    setScratchAvailable(c.scratch === true);
  }, []);
  // The list last read from this machine stands in until the live one arrives: switching machines
  // must not flash an empty sidebar.
  const [threads, setThreads] = useState<Thread[]>(() => (keepThreads ? cachedThreads(hostId)?.threads ?? [] : []));
  /** Whether there is any list to show yet, cached or live: until then an empty one means "not read", not "none". */
  const [threadsKnown, setThreadsKnown] = useState(() => !keepThreads || cachedThreads(hostId) !== null);
  const [sections, setSections] = useState<Section[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [todosLoading, setTodosLoading] = useState(true);
  const [todosError, setTodosError] = useState('');
  // The LoopX core status: the Goals group, the work pages and the steward brief all read this.
  const loopxRead = useLoopxStatus(token);
  const loopxStatus = loopxRead.status;
  const loopxNeedsYou = useMemo(() => needsYouGoalIds(loopxStatus?.attention ?? []), [loopxStatus]);
  // What this device logged in with: the hub's own token, or a login link that runs out. A link
  // that has died ends the session with its own words, so the login page can say what happened.
  const authKindRead = useAuthKind(token);
  const authKind = authKindRead.auth;
  useEffect(() => {
    const reason = authKindRead.error?.reason;
    if (token && (reason === 'link_expired' || reason === 'link_revoked')) {
      onToken('', reason === 'link_expired' ? t('auth.linkExpired') : t('auth.linkRevoked'));
    }
  }, [token, authKindRead.error, onToken]);
  // A browser alert for each Goal that newly waits on the person. The first read is only the baseline.
  const alertSeen = useRef<Set<string> | null>(null);
  const openGoalRef = useRef<(goalId: string | null, section?: GoalSection | null) => void>(() => undefined);
  useEffect(() => {
    if (!loopxStatus) return;
    const { now, fresh } = newlyWaiting(alertSeen.current, loopxStatus.attention);
    alertSeen.current = now;
    if (!fresh.length || !browserAlertsWanted() || browserPermission() !== 'granted') return;
    for (const id of fresh) {
      const title = loopxStatus.goals.find(goal => goal.goalId === id)?.title ?? id;
      try {
        const alert = new Notification(t('app.name'), { body: t('loopx.notify.alert', title), tag: `loopx-goal-${id}` });
        alert.onclick = () => { window.focus(); openGoalRef.current(id); alert.close(); };
      } catch { /* a browser that refuses the constructor (some phones) just does not alert */ }
    }
  }, [loopxStatus]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isPaletteChord(event) || event.isComposing) return;
      event.preventDefault();
      setPaletteOpen(open => !open);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  /** What the main area shows: a thread (or the blank one a new thread starts from), one of the hub's pages, or an extension's view. */
  // Those pages have addresses of their own, so a reload or a bookmark lands back on them.
  const [threadLink, setThreadLink] = useState(() => window.location.hash.startsWith('#/thread/') ? window.location.hash : null);
  const [schedulePath, setSchedulePath] = useState(() => window.location.hash.startsWith('#/schedules/') ? window.location.hash.slice('#/schedules/'.length) : '');
  const [boardEntry, setBoardEntry] = useState(0);
  const [goalRequest, setGoalRequest] = useState(() => goalOverviewRequest(window.location.hash));
  const [goalDetailFocus, setGoalDetailFocus] = useState<{ api: Api; threadId: string | null; goalId: string | null; section?: GoalSection | null } | null>(null);
  const [machinePath, setMachinePath] = useState(() => window.location.hash.startsWith('#/machines/') ? window.location.hash.slice('#/machines/'.length) : '');
  const [settingsPath, setSettingsPath] = useState(() => start === 'machines' ? 'machines' : window.location.hash.replace(/^#\/settings\/?/, ''));
  // `list`: the phone's thread list was open, so closing settings goes back to it.
  const settingsOrigin = useRef<{ view: MainView; thread: string | null; hash: string; scroll: number; pinned: boolean; entry: string; list: boolean } | null>(null);
  // The hub's "back" (what the top-left button does), run when a back swipe reaches the history
  // floor, and the address the app entry goes back to afterwards. See appHistory.ts.
  const appBack = useRef<() => boolean>(() => false);
  const appHref = useRef(window.location.pathname + window.location.search + window.location.hash);
  useAppHistory(appBack, appHref);
  const [view, setView] = useState<MainView>(() => start === 'machines' ? 'settings' : (typeof start === 'object' ? 'thread' : viewForHash(window.location.hash, Boolean(token)) ?? 'thread'));
  /** The address of the extension view asked for. Which view that is waits for the list of extensions. */
  const [extTarget, setExtTarget] = useState<string | null>(() => viewForHash(window.location.hash, Boolean(token)) === 'ext' ? window.location.hash : null);
  /** The extension catalog and what is installed on the machine on screen; null until read. */
  const [extensions, setExtensions] = useState<ExtensionState[] | null>(null);
  /** How the list stream is doing, and when the thread list was last read: how extension views tell whether what they show may be stale. */
  const [connection, setConnection] = useState<ConnectionState>('reconnecting');
  const [observedAt, setObservedAt] = useState<string | null>(null);
  const threadFeed = useMemo(() => createThreadFeed(api, (list, at) => {
    setThreads(list);
    setObservedAt(at);
    setThreadsKnown(true);
    if (keepThreads) rememberThreads(hostId, list, at ?? new Date().toISOString());
  }, setConnection), [api, keepThreads, hostId]);
  useEffect(() => {
    threadFeed.start();
    return () => threadFeed.dispose();
  }, [threadFeed]);
  // The marks this device last saw on this machine stand in until its agents are read, so rows do
  // not flash from text to icons on every switch.
  const [keptMarks] = useState(() => (keepThreads ? cachedAgentMarks(hostId) : null));
  const sidebarAgents = agentsKnown ? agents : keptMarks;
  useEffect(() => { if (keepThreads && agentsKnown) rememberAgentMarks(hostId, agents); }, [keepThreads, agentsKnown, hostId, agents]);
  // Every machine's threads in one list. A device setting, so it stays on as the app starts over
  // on another machine; with no other machine there is nothing to merge.
  const [allHosts, setAllHosts] = useStoredFlag('calli.sidebar.allHosts');
  const merging = allHosts && keepThreads && hosts.length > 0;
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
    // What this device last read stands in at once; the machines are asked again behind it.
    setElsewhere(targets.map((x): HostSource => {
      const kept = cachedThreads(x.id);
      return { hostId: x.id, label: x.label, state: !x.online ? 'offline' : kept ? 'cached' : 'loading', at: kept?.at ?? null, threads: kept?.threads ?? [], agents: cachedAgentMarks(x.id) };
    }));
    const update = (id: string | null, change: (s: HostSource) => HostSource) => setElsewhere((prev) => prev.map((s) => (s.hostId === id ? change(s) : s)));
    // Agents change rarely: read once per machine, not every round.
    for (const x of targets.filter((x) => x.online)) {
      new Api(token, '', x.id).agents().then((list) => {
        if (!alive) return;
        rememberAgentMarks(x.id, list);
        update(x.id, (s) => ({ ...s, agents: list }));
      }, () => undefined);
    }
    const read = () => {
      for (const x of targets.filter((x) => x.online)) {
        new Api(token, '', x.id).threads().then(
          (list) => {
            if (!alive) return;
            const at = new Date().toISOString();
            rememberThreads(x.id, list, at);
            update(x.id, (s) => ({ ...s, state: 'live', at, threads: list }));
          },
          () => { if (alive) update(x.id, (s) => ({ ...s, state: 'failed' })); },
        );
      }
    };
    read();
    const timer = setInterval(() => { if (!document.hidden) read(); }, 20_000);
    return () => { alive = false; clearInterval(timer); };
  }, [merging, token, hostId, hostsKey]);
  // This machine's agent sessions that are not threads yet, mixed into the list by their time.
  // A page at a time (a machine can hold hundreds), refreshed on the daemon's own scan cadence
  // while someone is looking; importing one makes it the thread it opens.
  const [externalSessions, setExternalSessions] = useState<ExternalSession[]>([]);
  const [externalCursor, setExternalCursor] = useState<string | null>(null);
  const [externalLoaded, setExternalLoaded] = useState(0);
  const [externalState, setExternalState] = useState<'off' | 'loading' | 'ready' | 'failed'>('off');
  const [externalError, setExternalError] = useState<{ message: string; retry?: () => void } | null>(null);
  const [importingExternal, setImportingExternal] = useState<string | null>(null);
  // Bumped when the setting that limits the list changes, so the list is read again at once.
  const [externalNonce, setExternalNonce] = useState(0);
  const externalScope = api.demo ? 'demo' : hostId ?? '';
  useEffect(() => {
    // The merged every-machine view lists threads only; so does a machine that cannot be asked.
    if (api.demo || merging) { setExternalSessions([]); setExternalCursor(null); setExternalLoaded(0); setExternalState('off'); return; }
    let alive = true;
    const read = async () => {
      if (document.hidden) return;
      setExternalState((old) => (old === 'off' ? 'loading' : old));
      try {
        const page = await api.localSessions(null, Math.max(externalLoaded, 60));
        if (!alive) return;
        setExternalSessions(page.sessions);
        setExternalCursor(page.nextCursor);
        setExternalLoaded(page.sessions.length);
        setExternalState('ready');
        setExternalError(null);
      } catch (e) {
        if (!alive || e instanceof AuthError) return;
        setExternalState('failed');
      }
    };
    void read();
    const timer = setInterval(read, 60_000);
    return () => { alive = false; clearInterval(timer); };
    // `externalLoaded` grows only through "show more", which re-reads deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, externalScope, merging, externalNonce]);
  const loadMoreExternal = () => {
    if (!externalCursor) return;
    setExternalState('loading');
    api.localSessions(externalCursor)
      .then((page) => {
        setExternalSessions((prev) => {
          const seen = new Set(prev.map((s) => `${s.agent}:${s.sessionId}`));
          return [...prev, ...page.sessions.filter((s) => !seen.has(`${s.agent}:${s.sessionId}`))];
        });
        setExternalCursor(page.nextCursor);
        setExternalLoaded((n) => n + page.sessions.length);
        setExternalState('ready');
      })
      .catch(() => setExternalState('failed'));
  };
  const importExternal = (session: ExternalSession) => {
    const key = `${session.agent}:${session.sessionId}`;
    setExternalError(null);
    setImportingExternal(key);
    api.importSession(session.agent, session.sessionId)
      .then((thread) => {
        setThreads((prev) => [thread, ...prev.filter((t) => t.id !== thread.id)]);
        setExternalSessions((prev) => prev.filter((s) => `${s.agent}:${s.sessionId}` !== key));
        setSelectedId(thread.id);
        setImportingExternal(null);
        // Importing a session from outside the project roots adds its directory there: show it.
        api.workdirs().then(applyWorkdirs).catch(() => undefined);
      })
      .catch((e) => {
        setImportingExternal(null);
        // A bad token ends the session; anything else is said beside the rows, where the click was.
        if (e instanceof AuthError) handleError(e);
        else setExternalError({ message: t('sidebar.externalFailed', session.title || session.cwd), retry: () => importExternal(session) });
      });
  };
  // Folders last chosen for members on each machine, offered first next time.
  const recentRemoteDirs = (host: string): string[] => { try { return JSON.parse(localStorage.getItem(`calli.collab.dirs.${host}`) ?? '[]'); } catch { return []; } };
  const rememberRemoteDir = (host: string, cwd: string) => localStorage.setItem(`calli.collab.dirs.${host}`, JSON.stringify([cwd, ...recentRemoteDirs(host).filter((d) => d !== cwd)].slice(0, 5)));
  /** While set, the roots dialog stands in for the picker: its rows, with each root's own remove flag. */
  const [remotePick, setRemotePick] = useState<{ host: string; api: Api; machine: string; roots: string[]; selected: string; manage: Array<{ path: string; removable: boolean }> | null; choose: (cwd: string) => void } | null>(null);
  // Instances on the other machines, for a collaboration led from this hub: each with that machine's folders.
  const [remoteAgents, setRemoteAgents] = useState<Array<AgentInfo & { host: string; directories: string[]; name: string; machine: string }>>([]);
  useEffect(() => {
    if (hostId || !token || api.demo) { setRemoteAgents([]); return; }
    let alive = true;
    void Promise.all(hostsRef.current.filter((h) => h.status === 'online').map(async (h) => {
      const remote = new Api(token, '', h.id);
      const [list, config] = await Promise.all([remote.agents(), remote.workdirs()]).catch((): [AgentInfo[], { workdirs: string[] }] => [[], { workdirs: [] }]);
      return list.filter((a) => a.available).map((a) => ({ ...a, id: `${a.id}@${h.id}`, name: a.label, machine: h.label, label: `${a.label} · ${h.label}`, host: h.id, directories: config.workdirs }));
    })).then((lists) => { if (alive) setRemoteAgents(lists.flat()); });
    return () => { alive = false; };
  }, [token, hostId, hostsKey, api]);
  /** A machine's folder list changed from the page: the open picker and every cached agent of that
      machine carry it on, so the next picker starts from the fresh list instead of a stale copy. */
  const applyRemoteWorkdirs = useCallback((host: string, config: WorkdirsConfig) => {
    setRemotePick((p) => (p && p.host === host ? { ...p, roots: config.workdirs } : p));
    setRemoteAgents((list) => withWorkdirs(list, host, config));
  }, []);
  /** The machine's own rows for the roots dialog, which then stands in for the picker until closed. */
  const openRemoteRoots = (host: string, config: WorkdirsConfig) => {
    applyRemoteWorkdirs(host, config);
    setRemotePick((p) => (p && p.host === host ? { ...p, manage: rootRows(config) } : p));
  };
  /** Bumped by daemon broadcasts, so the open plan page reloads. */
  const [personal, setPersonal] = useState<PersonalSettings | null>(null);
  const [personalError, setPersonalError] = useState('');
  const reloadPersonal = useCallback(() => {
    if (api.demo) return; setPersonalError('');
    void api.personalSettings().then(setPersonal).catch(e => setPersonalError(String(e)));
  }, [api]);
  useEffect(() => { setPersonal(null); reloadPersonal(); }, [reloadPersonal]);
  const [todosKey, setTodosKey] = useState(0);
  /** The same, for the open scheduled-task page. */
  const [schedulesKey, setSchedulesKey] = useState(0);
  // Explicit navigation wins even when the startup preference or thread list arrives late.
  const startupPending = useRef(!demo && !showcase && start === undefined && !window.location.hash);
  const [startupSettings, setStartupSettings] = useState<GeneralSettings | null>(null);
  const [selectedId, setSelectedIdState] = useState<string | null>(typeof start === 'object' && 'thread' in start ? start.thread : showcase?.initialThreadId ?? null);
  const setSelectedId = useCallback((id: string | null) => {
    startupPending.current = false;
    setSelectedIdState(id);
  }, []);
  useEffect(() => {
    if (api.demo || !startupPending.current) return;
    let live = true;
    void api.generalSettings().then(value => { if (live) setStartupSettings(value); })
      .catch(() => { if (live) setStartupSettings({ startupPage: 'new-thread', autoArchiveDays: null, autoImportLocal: true }); });
    return () => { live = false; };
  }, [api]);
  const [singleSegmentId, setSingleSegmentId] = useState<string | null>(null);
  const [overviewSegment, setOverviewSegment] = useState<{ api: Api; threadId: string; before: number } | null>(null);
  const [coordination, setCoordination] = useState<Coordination | null>(null);
  const memberDefaultsKey = hostId ? `${MEMBER_DEFAULTS_KEY}:${hostId}` : MEMBER_DEFAULTS_KEY;
  const [memberDefaults, setMemberDefaults] = useState(() => savedMemberDefaults(memberDefaultsKey));
  // Members work unattended, so they start with full access unless this device was told otherwise.
  const [memberPermission, setMemberPermission] = useState(() => localStorage.getItem(`${memberDefaultsKey}.permission`) ?? 'full_access');
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [draftCollaboration, setDraftCollaboration] = useState(false);
  const [collaborationDirectories, setCollaborationDirectories] = useState<string[]>([]);
  const [historyTurnIds, setHistoryTurnIds] = useState<string[]>([]);
  const [events, setEvents] = useState<StoredEvent[]>([]);
  /** The history revision the view holds; an edit names it so a stale page is refused. */
  const [historyRevision, setHistoryRevision] = useState(0);
  /** Bumped when an edit replaced the conversation, so the page is read again from its start. */
  const [historyEpoch, setHistoryEpoch] = useState(0);
  // A new thread opens where this machine's last one was left: the choice this device kept stands
  // until the machine's own answer says otherwise.
  const keptDraft = keepThreads ? cachedDraft(hostId) : null;
  const [draftAgent, setDraftAgent] = useState(() => keptDraft?.agent ?? '');
  const [draftCwd, setDraftCwd] = useState(() => typeof start === 'object' && 'draft' in start ? start.draft.project ?? keptDraft?.cwd ?? '' : keptDraft?.cwd ?? '');
  // A new thread may start in a git worktree of its directory; offered only when the directory is in a repository.
  const [draftWorktree, setDraftWorktree] = useState(false);
  // A new conversation can be handed to LoopX as a Goal: it then runs under the Goal's own two permission levels.
  const [draftLoopx, setDraftLoopx] = useState(false);
  const [goalCaps, setGoalCaps] = useState<GoalAgentCapability[]>([]);
  const [draftGoalPermission, setDraftGoalPermission] = useState<GoalPermission | null>(null);
  const [pendingGoal, setPendingGoal] = useState<{ text: string; onCreated?: (threadId: string) => void; resolve: () => void; reject: (e: unknown) => void } | null>(null);
  const [worktreeOffer, setWorktreeOffer] = useState<{ cwd: string; branch: string | null; changes: number } | null>(null);
  const [draftSectionId, setDraftSectionId] = useState<string | null>(null);
  const [draftWorkMode, setDraftWorkMode] = useState<WorkMode>('execute');
  const [modeBusy, setModeBusy] = useState(false);
  /** Writing mode for a thread not started yet: switched on once the first message creates it. */
  const [draftWriting, setDraftWriting] = useState<WritingStage | null>(null);
  const [writingBusy, setWritingBusy] = useState(false);
  const [draftPermission, setDraftPermission] = useState('auto_edit');
  const modelPreferences = useModelPreferences(api, error => setError(message(error)));
  const [modelLogin, setModelLogin] = useState<{ agent: string; label: string; login?: AgentInfo['login']; empty?: boolean; key: string; client: Api; scope: Api } | null>(null);
  const [modelsByAgent, setModelsByAgent] = useState<Record<string, ModelOption[]>>(() => (keepThreads ? cachedModels(hostId) ?? {} : {}));
  const draftCaps = agents.find(agent => agent.id === draftAgent)?.capabilities;
  const goalCap = goalCaps.find(cap => cap.agent === draftAgent);
  const draftGoalLevel: GoalPermission = draftGoalPermission && goalCap?.permissions.find(p => p.id === draftGoalPermission)?.available ? draftGoalPermission : defaultGoalPermission(goalCap);
  useEffect(() => {
    if (api.demo || !token) return;
    void fetchGoalCapabilities(token).then(setGoalCaps, () => setGoalCaps([]));
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps
  // An agent that cannot hold a Goal takes the mode back off.
  useEffect(() => { if (draftLoopx && goalCap && !goalCap.available) setDraftLoopx(false); }, [draftAgent, goalCap?.available]); // eslint-disable-line react-hooks/exhaustive-deps
  const draftChoice = fitDraftModelPreference(modelPreferences.preferences[draftAgent] ?? EMPTY_MODEL_PREFERENCE,
    modelsByAgent[draftAgent] ?? [], draftCaps?.effortLevels ?? [], Boolean(draftCaps?.speed));
  const { model: draftModel, effort: draftEffort, speed: draftSpeed } = draftChoice;
  const modelVersions = useRef<Record<string, number>>({});
  const [commandsByKey, setCommandsByKey] = useState<Record<string, SlashCommand[]>>({});
  const [drawer, setDrawer] = useState(start === 'list');
  const [chosenSidebarWidth, setSidebarWidth] = useState<number | null>(() => {
    if (showcase) return null;
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
    if (showcase) return;
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width));
    } catch {
      // The resize still applies for this page when storage is unavailable.
    }
  }, [showcase]);
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
    if (showcase) return false;
    try {
      return window.localStorage.getItem(SIDEBAR_HIDDEN_KEY) === '1';
    } catch {
      return false;
    }
  });
  const toggleSidebar = useCallback(() => {
    setSidebarHidden((hidden) => {
      try {
        if (!showcase) window.localStorage.setItem(SIDEBAR_HIDDEN_KEY, hidden ? '0' : '1');
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
        if (!document.querySelector('.settings-page')) toggleSidebar();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [compact, toggleSidebar]);
  const listOpen = drawer && compact;
  useListSwipe(compact, listOpen, setDrawer);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const [appearance, setAppearance, theme] = useAppearance(showcase ? 'calli.showcase.appearance' : undefined, showcase ? { mode: showcase.theme, palette: 'paper' } : undefined);
  // An error belongs to the thread (or new-thread page) it happened in, so other threads stay clean.
  // The closure keeps the thread that was open when the failing request started.
  const [errorState, setErrorState] = useState<{ text: string; thread: string | null } | null>(null);
  const setError = (text: string | null) => setErrorState(text ? { text, thread: selectedId } : null);
  const error = errorState && errorState.thread === selectedId ? errorState.text : null;
  // What a thread is busy with that has no trace of its own yet (naming it, copying it): a quiet
  // line by its title until the result lands. Confirmations that the screen already shows are not said.
  const [busyLine, setBusyLine] = useState<{ threadId: string; message: string } | null>(null);
  // A thread just archived: its row is gone, so the undo is offered where the row was noticed.
  const [archivedUndo, setArchivedUndo] = useState<{ thread: Thread; nonce: number } | null>(null);
  // The daemon serves a newer build than the page runs: a row under the header offers the reload.
  const [newVersion, setNewVersion] = useState(false);
  const [showDirPicker, setShowDirPicker] = useState(false);
  // A Goal join that waits on a directory choice: the picker resolves it, or null on cancel.
  const [goalDirAsk, setGoalDirAsk] = useState<{ resolve: (cwd: string | null) => void } | null>(null);
  const pickGoalDir = () => new Promise<string | null>(resolve => setGoalDirAsk({ resolve }));
  const [archiveKey, setArchiveKey] = useState(0);
  const [statusOpen, setStatusOpen] = useState(false);
  const [termOpen, setTermOpen] = useState(false);
  // Minimized keeps the connection: coming back shows the same desktop at once, without reconnecting.
  const [desktop, setDesktop] = useState<'closed' | 'open' | 'minimized'>('closed');
  // Whether the machine shows a desktop (a VNC server runs on it): its entries appear only then.
  const [hasDesktop, setHasDesktop] = useState(false);
  useEffect(() => {
    setHasDesktop(false);
    // Another machine: a desktop kept from the one before is not this one's.
    setDesktop('closed');
    if (demo) return;
    let live = true;
    // A daemon from before desktops answers 404: no desktop, like a machine without a VNC server.
    api.desktop().then((d) => live && setHasDesktop(d.available)).catch(() => undefined);
    return () => { live = false; };
  }, [api, demo]);
  /** The terminal a login started; null attaches to the newest live one. */
  const [terminalTarget, setTerminalTarget] = useState<string | null>(null);
  const [terminalClient, setTerminalClient] = useState<Api | null>(null);
  const [observabilityTab, setObservabilityTab] = useState<'usage' | 'diagnostics'>('usage');
  const [observabilityFocus, setObservabilityFocus] = useState<string | undefined>(undefined);
  // Bumped on every visit so the page opens on the requested tab, even when it is already showing.
  const [observabilityKey, setObservabilityKey] = useState(0);
  // Bumped on every visit so the page opens on its list, even when it is already showing.
  const [machineKey, setMachineKey] = useState(0);
  /** The thread the machines page was opened from; the browser's back returns to it. */
  const machinesOrigin = useRef<string | null>(null);
  /** Other agent CLIs found on this host, added from the settings menu — not the picker. */
  const [addAgentOpen, setAddAgentOpen] = useState(false);
  const [onboardOpen, setOnboardOpen] = useState(false);
  const [onboardStep, setOnboardStep] = useState<'ask' | 'results'>('ask');
  // Counters that open the composer's pickers from elsewhere (the slash menu).
  const [permissionSignal, setPermissionSignal] = useState(0);
  const [modelSignal, setModelSignal] = useState(0);
  const [atBottom, setAtBottom] = useState(true);
  // The thread has turns older than the ones loaded; they are fetched a page at a time.
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // The first page of the open thread is still on its way from the daemon.
  const [eventsLoading, setEventsLoading] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);
  // Text handed back to the composer: messages that were waiting when the user pressed stop.
  const [restore, setRestore] = useState<{ text: string; nonce: number; mentions?: AgentMention[] } | null>(null);
  // Text quoted out of a message by selecting it.
  const composerDraftKey = hostId ? `host:${hostId}:${selectedId ?? (draftTeam ? `new:team:${draftTeam}` : 'new')}` : selectedId ?? (draftTeam ? `new:team:${draftTeam}` : 'new');
  const [noteSignal, setNoteSignal] = useState<{ draftKey: string; note: DraftNote & { nonce: number } } | null>(null);
  const pendingNoteId = useRef<string | null>(null);
  pendingNoteId.current = noteSignal?.note.id ?? null;
  const [messageReference, setMessageReference] = useState<{ draftKey: string; value: ContextReference; nonce: number } | null>(null);
  const [assistantMessage, setAssistantMessage] = useState<MessageSnapshot | null>(null);
  const messageAssistantApi = useMemo(() => hostId === null ? api : new Api(token), [api, hostId, token]);
  const messageSnapshot = async (text: string, turnId: string, threadId?: string): Promise<MessageSnapshot> => {
    const source = await api.thread(threadId ?? selectedId!);
    return { text, turnId, sourceId: source.id, title: host ? `${host.label} · ${source.title}` : source.title, path: source.cwd };
  };
  const consumeNote = useCallback((id: string) => {
    setNoteSignal((current) => current?.note.id === id ? null : current);
  }, []);
  // Where each noted passage stands in the conversation, for as long as its note waits in the box.
  const noteRanges = useRef(new Map<string, Range>());
  const notesByTarget = useRef(new Map<string, DraftNote[]>());
  const [noteMarks, setNoteMarks] = useState<NoteMark[]>([]);
  // Notes waiting in the box, and a tick each time one is picked out of the file panel — the phone's
  // full-screen page cannot show the box, so it counts them at its foot instead.
  const [noteCount, setNoteCount] = useState(0);
  const [filesNotePulse, setFilesNotePulse] = useState(0);
  const onNotesChangeFor = useCallback((target: string, notes: DraftNote[]) => {
    if (notes.length) notesByTarget.current.set(target, notes);
    else notesByTarget.current.delete(target);
    const allIds = new Set<string>();
    const marks: NoteMark[] = [];
    let total = 0;
    for (const list of notesByTarget.current.values()) {
      total += list.length;
      list.forEach((n, i) => {
        allIds.add(n.id);
        const range = noteRanges.current.get(n.id);
        if (range) marks.push({ id: n.id, n: i + 1, range });
      });
    }
    for (const id of [...noteRanges.current.keys()]) if (!allIds.has(id) && id !== pendingNoteId.current) noteRanges.current.delete(id);
    setNoteCount(total);
    setNoteMarks(marks);
  }, []);
  const onNotesChange = useCallback((notes: DraftNote[]) => onNotesChangeFor(composerDraftKey, notes), [composerDraftKey, onNotesChangeFor]);
  useEffect(() => () => onNotesChangeFor(composerDraftKey, []), [composerDraftKey, onNotesChangeFor]);
  // The passage a side chat was opened for, if one is open.
  const [sideAsk, setSideAsk] = useState<{ text: string; nonce: number; source?: Thread } | null>(null);
  // A message waiting for the user to confirm sending into a session the terminal also used.
  const [pendingSend, setPendingSend] = useState<{ text: string; images: PreparedImage[]; videos?: PreparedVideo[]; mentions?: AgentMention[]; resolve: () => void; reject: (e: unknown) => void } | null>(null);
  const [pageHidden, setPageHidden] = useState(() => document.hidden);
  const baseTitle = useRef(document.title);

  const voice = useVoiceSession(api, selectedId, hostId);
  const timeline = useMemo(() => buildCachedTimeline(events,
    voice.state.messagesThreadId === selectedId ? voice.state.messages : []),
  [events, selectedId, voice.state.messages, voice.state.messagesThreadId]);
  const turnPreviewMap = useMemo(() => turnPreviews(timeline.items), [timeline.items]);
  // The newest turn's plan sits in the top bar, so the conversation does not show it twice.
  const topPlan = useMemo(() => currentPlan(timeline.items), [timeline.items]);
  const threadItems = useMemo(() => topPlan ? timeline.items.filter((item) => item !== topPlan) : timeline.items, [timeline.items, topPlan]);
  /** The thread's native goal. Live events say it when they can; otherwise what the hub last said. */
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
      if (item.kind === 'user' && !item.coordination && !item.key.startsWith('voice:')) return { turnId: item.turnId, text: item.text };
    }
    return null;
  }, [timeline.items]);
  const selected = threads.find((t) => t.id === selectedId) ?? null;
  const threadPending = !selected && (Boolean(threadLink) || (Boolean(selectedId) && !threadsKnown));
  // Her own conversation is told apart by the hub's answer, never by its agent or title.
  const [routerThreadId, setRouterThreadId] = useState<string | null>(() => (api.demo ? null : cachedRouterThreadId(api.host)));
  useEffect(() => {
    if (api.demo) { setRouterThreadId(null); return; }
    let live = true;
    const cached = cachedRouterThreadId(api.host);
    if (cached) setRouterThreadId(current => current ?? cached);
    void api.petRouter().then(router => {
      if (!live) return;
      rememberRouterThreadId(api.host, router.threadId);
      setRouterThreadId(router.threadId);
    }).catch(() => undefined);
    return () => { live = false; };
  }, [api, threads.length]);
  const onRouterThread = Boolean(selected && routerThreadId === selected.id);
  const routerTitle = t('pet.name');
  const latestSegment = selected ? latestHandoverThread(selected, threads) : null;
  const viewingEarlier = Boolean(selected && latestSegment && latestSegment.id !== selected.id);
  const mergedHandover = !api.demo && singleSegmentId !== selectedId && Boolean(selected?.handover?.mode === 'continue' || viewingEarlier);
  const historyCutoff = overviewSegment?.api === api && overviewSegment.threadId === selectedId && singleSegmentId !== selectedId ? overviewSegment.before : undefined;
  useLayoutEffect(() => { setOverviewSegment(value => value?.api === api && value.threadId === selectedId ? value : null); }, [api, selectedId]);
  const selectDraftAgent = (id: string, list = agents) => {
    const caps = list.find(item => item.id === id)?.capabilities;
    setDraftAgent(id);
    setDraftWorkMode('execute');
    if (caps) setDraftPermission(startingPermission(caps));
  };
  // Returning to the new-thread page also picks up choices made on another device.
  useEffect(() => { if (!selectedId) void modelPreferences.refresh(); }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let cancelled = false;
    if (!api || !selected?.coordination) { setCoordination(null); return; }
    void api.coordination(selected.id).then(value => { if (!cancelled) setCoordination(value); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [api, selectedId, threads]);
  useEffect(() => { setAgentsOpen(false); setDraftCollaboration(false); setCollaborationDirectories([]); }, [selectedId, hostId]);
  const onSettings = view === 'settings';
  const onSteward = view === 'steward' && !selectedId;
  const onSchedules = view === 'schedules' && !selectedId;
  const onActivity = view === 'activity' && !selectedId;
  const onAgents = view === 'agents' && !selectedId;
  const onObservability = view === 'observability' && !selectedId;
  const onMachines = view === 'machines';
  const onTeams = view === 'teams' && !selectedId;
  const onSkills = view === 'skills' && !selectedId;
  const onMcp = view === 'mcp' && !selectedId;
  const onExtensions = view === 'extensions' && !selectedId;
  const onExt = view === 'ext' && !selectedId;
  /** The extension view on screen; null while the list of extensions is still being read. */
  const extOpen = onExt && extensions && extTarget ? resolveExtensionRoute(extTarget, extensions) : null;
  const extName = extOpen ? extensions?.find((e) => e.manifest.id === extOpen.extensionId)?.manifest.name[locale()] ?? extOpen.extensionId : '';
  /** The name of the page the main area shows instead of a thread, for the top bar; null on a thread. */
  const viewTitle = onSettings ? t('sidebar.settings') : onSteward ? t('loopx.steward') : onObservability ? t('obs.title') : onMachines ? t('hosts.machines') : onAgents ? t('agents.title') : onTeams ? t('teams.title') : onSchedules
      ? t('sidebar.schedules')
      : onActivity
        ? t('activity.title')
        : onSkills
          ? t('composerMenu.skills')
          : onMcp
            ? 'MCP'
            : onExtensions
              ? t('extensions.title')
              : onExt
                ? (extOpen?.view.title[locale()] ?? '')
                : null;
  const onPage = viewTitle !== null;
  // What an extension view is handed: the summaries of the threads the page holds, and how fresh they are.
  const connectionShown: ConnectionState = api.demo || (host && !hostUp) ? 'offline' : connection;
  const workSources = useWorkSources(api, extensions, Boolean(selectedId) || onExt, onExt ? extOpen?.extensionId : undefined, selectedId ?? undefined);
  const machineSources = useMemo<HostSource[]>(() => [{
    hostId, label: host?.label ?? t('hosts.local'), threads, agents: sidebarAgents,
    state: connectionShown === 'offline' ? 'offline' : connectionShown === 'live' ? 'live' : threadsKnown ? 'cached' : 'loading',
    at: observedAt ?? (keepThreads ? cachedThreads(hostId)?.at ?? null : null),
  }, ...(merging ? elsewhere : [])], [hostId, host?.label, threads, sidebarAgents, connectionShown, threadsKnown, observedAt, keepThreads, merging, elsewhere]);
  const extInputs = useMemo(
    () => (onExt ? { workSources, threads: machineSummaries(machineSources.map(source => ({ ...source, goals: source.hostId === hostId ? loopxStatus?.goals : undefined })), selectedId, merging),
      machines: machineSources.map((source): MachineState => ({ id: source.hostId, label: source.label, state: source.state, observedAt: source.at })),
      connection: connectionShown, observedAt: api.demo ? null : observedAt, agents, extension: extensions?.find(e => e.manifest.id === extOpen?.extensionId) ?? null } : null),
    [workSources, onExt, machineSources, hostId, loopxStatus, merging, selectedId, connectionShown, observedAt, agents, api.demo, extensions, extOpen?.extensionId],
  );

  // An extension view that is not there (never installed, removed, switched off, or an address
  // nothing claims) is the thread view instead, once the list says so.
  useEffect(() => {
    if (view === 'ext' && !selectedId && extensions && !(extTarget && resolveExtensionRoute(extTarget, extensions))) setView('thread');
  }, [view, selectedId, extensions, extTarget]);

  const voiceExt = extensions?.find((e) => e.manifest.id === 'voice');
  const voiceExtInstalled = Boolean(voiceExt?.source === 'builtin' && voiceExt.installed && voiceExt.enabled
    && voiceExt.granted.includes('voice.control') && voiceExt.manifest.contributes.composerActions?.some(action => action.id === 'voice')
    && (!voiceExt.requirements || voiceExt.requirements.state === 'satisfied'));
  useEffect(() => {
    if (!voiceExtInstalled && voice.state.active) {
      void voice.stop();
    }
  }, [voiceExtInstalled, voice.state.active, voice]);
  const voiceAgent = (id: string | undefined) => {
    const caps = agents.find(agent => agent.id === id)?.capabilities;
    return Boolean(caps?.realtimeVoice || caps?.coordination && agents.some(agent => agent.available && agent.capabilities.realtimeVoice));
  };
  const canVoice = Boolean(voiceExtInstalled && selected && !selected.archivedAt && voiceAgent(selected.agent)
    && (agents.find(agent => agent.id === selected.agent)?.capabilities.realtimeVoice || !selected.coordination?.team && !selected.coordination?.retired));
  // A draft has no thread yet: starting voice creates it first, as its first message would.
  const canVoiceDraft = Boolean(voiceExtInstalled && !selected && !draftTeam && draftAgent && (draftCwd || draftScratch) && voiceAgent(draftAgent));
  const [voiceOnSelect, setVoiceOnSelect] = useState<string | null>(null);
  useEffect(() => {
    if (!voiceOnSelect || selectedId !== voiceOnSelect) return;
    setVoiceOnSelect(null);
    void voice.start();
  }, [voiceOnSelect, selectedId, voice]);
  const startDraftVoice = async () => {
    try { setVoiceOnSelect(await createDraftThread(true)); void refreshThreads(); }
    catch (e) { handleError(e); }
  };

  // The address follows what the main area shows. Replaced, not pushed: going between the pages and
  // threads is not a trail for the back button to walk.
  const stewardWant = goalRequest ? goalOverviewHash(goalRequest.goalId, goalRequest.section) : goalDetailFocus?.api === api && goalDetailFocus.threadId === null ? goalOverviewHash(goalDetailFocus.goalId, goalDetailFocus.section) : STEWARD_HASH;
  const extWant = onExt ? (extOpen ? extensionHash(extOpen) : extTarget ?? '') : '';
  useEffect(() => {
    if (threadLink) return;
    const want = onSettings ? `#/settings${settingsPath ? `/${settingsPath}` : ''}` : onSteward ? stewardWant : onObservability ? OBSERVABILITY_HASH : onMachines ? `${MACHINES_HASH}${machinePath ? `/${machinePath}` : ''}` : onAgents ? AGENTS_HASH : onTeams ? TEAMS_HASH : onSchedules ? `${SCHEDULES_HASH}${schedulePath ? `/${schedulePath}` : ''}` : onActivity ? ACTIVITY_HASH : onSkills ? SKILLS_HASH : onMcp ? MCP_HASH : onExtensions ? EXTENSIONS_HASH : extWant;
    if (window.location.hash !== want) {
      window.history.replaceState(window.history.state, '', want || window.location.pathname + window.location.search);
    }
    appHref.current = window.location.pathname + window.location.search + window.location.hash;
  }, [stewardWant, goalRequest, goalDetailFocus, api, boardEntry, schedulePath, machinePath, onSettings, settingsPath, onSteward, onObservability, onMachines, onAgents, onTeams, onActivity, onSkills, onSchedules, onMcp, onExtensions, extWant, threadLink]);

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

  // An address typed or followed while the page is open. Signing in or out changes what may be
  // opened, so the listener is renewed with the token.
  useEffect(() => {
    const onHash = (event: HashChangeEvent) => {
      // A landing on the history floor is the back gesture, not an address followed (appHistory.ts).
      if (isBounce()) return;
      const hash = window.location.hash;
      const apply = () => {
        startupPending.current = false;
        window.history.replaceState(window.history.state, '', hash || window.location.pathname + window.location.search);
        if (hash.startsWith('#/thread/')) { setThreadLink(hash); return; }
        setThreadLink(null);
        const next = viewForHash(hash, Boolean(token));
        if (!next) {
          // Back to a plain thread address. The settings origin may already have restored the
          // view while a close was still landing (the origin is consumed on first render away
          // from settings), so keep whatever is already a thread view and only replace a page.
          const origin = settingsOrigin.current;
          setView(v => v === 'thread' ? v : origin?.view ?? 'thread');
          if (origin?.thread) setSelectedId(origin.thread);
          setDrawer(false);
          return;
        }
        if (next !== 'settings') setSelectedId(null);
        setView(next);
        if (next === 'schedules') setSchedulePath(hash.startsWith('#/schedules/') ? hash.slice('#/schedules/'.length) : '');
      if (next === 'machines') setMachinePath(hash.startsWith('#/machines/') ? hash.slice('#/machines/'.length) : '');
        if (next === 'settings') setSettingsPath(hash.replace(/^#\/settings\/?/, ''));
        if (next !== 'steward') setGoalRequest(null);
        if (next === 'steward') {
          const request = goalOverviewRequest(hash);
          setGoalRequest(request);
          if (!request) setGoalDetailFocus(null);
        }
        if (next === 'ext') setExtTarget(hash);
        setDrawer(false);
      };
      if (!navigate(apply)) window.history.replaceState(window.history.state, '', event.oldURL);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [token, navigate]);

  // Both spellings of the thread's directory; kept stable so the thread view is not re-rendered for nothing.
  const threadRoots = useMemo(() => (selected ? [selected.cwd, selected.cwdReal] : []), [selected?.cwd, selected?.cwdReal]);
  const running = timeline.running || selected?.status === 'running';

  // A plan the user chose to leave be: remembered on this device, so a reload does not ask again.
  const [planSkip, setPlanSkip] = useState<string | null>(null);
  useEffect(() => {
    try {
      setPlanSkip(selectedId ? localStorage.getItem(`${PLAN_SKIP_KEY}:${selectedId}`) : null);
    } catch {
      setPlanSkip(null);
    }
  }, [selectedId]);

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
        if (it.kind === 'user' && !it.coordination) break;
        if (it.kind === 'proposal' && it.done) {
          if (planSkip !== it.key) candidates.push({ order: i, target: { kind: 'hub-plan', item: it } });
          break;
        }
      }
    }
    candidates.sort((a, b) => a.order - b.order);
    return candidates;
  }, [timeline, planSkip]);
  const waitingOn = waiting[0] ?? null;

  const topLevelThreads = useMemo(() => threads.filter((t) => !t.coordination || t.coordination.rootId === t.id || !threads.some((r) => r.id === t.coordination!.rootId)), [threads]);
  // Her conversation is reached through her, from the sidebar's own row and the desk, not listed twice.
  const listedThreads = useMemo(() => routerThreadId ? topLevelThreads.filter((t) => t.id !== routerThreadId) : topLevelThreads, [topLevelThreads, routerThreadId]);

  useEffect(() => {
    if (!startupPending.current || !startupSettings) return;
    if (startupSettings.startupPage === 'recent-thread' && !observedAt) return;
    startupPending.current = false;
    if (startupSettings.startupPage === 'recent-thread') {
      const recent = topLevelThreads.filter(thread => !thread.archivedAt)
        .reduce<Thread | null>((latest, thread) => !latest || thread.updatedAt > latest.updatedAt ? thread : latest, null);
      if (recent) { setSelectedIdState(recent.id); setDrawer(false); }
    }
  }, [startupSettings, observedAt, topLevelThreads]);

  // Members are listed under their lead in the sidebar, not as threads of their own.
  const teamMembers = useMemo(() => {
    const byLead = new Map<string, Thread[]>();
    for (const t of threads) {
      if (t.coordination && !t.coordination.retired && t.coordination.rootId !== t.id) byLead.set(t.coordination.rootId, [...(byLead.get(t.coordination.rootId) ?? []), t]);
    }
    return byLead;
  }, [threads]);

  // A takeover under way, from the button on a turn that failed because the plan ran out.
  const [takingOver, setTakingOver] = useState(false);
  const [takeoverTarget, setTakeoverTarget] = useState<{ threadId: string; target: HandoverTarget } | null>(null);
  // Arming or cancelling a "wait for the quota and continue" watch on that same turn.
  const [resumeBusy, setResumeBusy] = useState(false);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const topbar = el.querySelector<HTMLElement>(':scope > .topbar');
    const composer = el.querySelector<HTMLElement>(':scope > .composer-slot');
    const box = composer?.querySelector<HTMLElement>(':scope > .composer');
    const read = () => {
      // What the column's lean toward the middle of the screen is worked out from (see styles.css).
      // clientWidth includes the padding the lean adds, so the two do not chase each other.
      el.style.setProperty('--main-w', `${el.clientWidth}px`);
      // Let the conversation pass behind glass controls. Padding keeps both ends readable;
      // measure the slot because queues, attachments and the phone keyboard change its height.
      // Notices keep their place in the flow, and an installed window keeps its native title band.
      const top = composer && topbar?.nextElementSibling?.classList.contains('scroller') && !el.closest('.in-titlebar') ? topbar.offsetHeight : 0;
      el.style.setProperty('--chat-top-overlap', `${top}px`);
      el.style.setProperty('--chat-bottom-overlap', `${composer?.offsetHeight ?? 0}px`);
      // The edge wash under the composer is as tall as the box with its queue, not the notes stacked above it.
      el.style.setProperty('--chat-composer-h', `${box?.offsetHeight ?? 0}px`);
    };
    read();
    const watch = new ResizeObserver(read);
    watch.observe(el);
    if (topbar) watch.observe(topbar);
    if (composer) watch.observe(composer);
    if (box) watch.observe(box);
    return () => watch.disconnect();
  });
  const [agentsStart, setAgentsStart] = useState<string | null>(null);
  const stickToBottom = useRef(true);
  const [jump, setJump] = useState<{ threadId: string; turnId: string } | null>(null);
  const readingPosition = useReadingPosition(scrollerRef, stickToBottom, selectedId, events, `${mergedHandover}:${historyEpoch}:${onSettings}`, Boolean(jump));
  // The thread on screen, for answers that arrive after the user has moved on.
  const shownThread = useRef<string | null>(null);
  shownThread.current = selectedId;
  const shownApi = useRef(api);
  shownApi.current = api;
  // Whether the user has a hand on the scroller, and when they last did.
  const gesture = useRef({ held: false, at: 0 });

  const askToConnect = () => showcase ? showcase.onInstall() : setConnectAsk((cur) => cur ?? {});

  // The app starts over on the demo; a token that stopped working says so in the connect dialog.
  const signOut = (msg?: string) => onToken('', msg);

  const handleError = (e: unknown, thread = selectedId) => {
    // In the demo, doing something asks to connect; a part it has no data for just stays empty.
    if (e instanceof DemoError) {
      if (e.write) askToConnect();
    } else if (e instanceof AuthError) signOut(e.code === 'link_expired' ? t('auth.linkExpired') : e.code === 'link_revoked' ? t('auth.linkRevoked') : t('app.tokenExpired'));
    // A machine that dropped is explained in the main area; the hub is asked at once how it is.
    else if (e instanceof ApiError && e.details.code === 'host_offline') void reloadHosts();
    else setErrorState({ text: message(e), thread });
  };

  const refreshThreads = () => threadFeed.read().catch(() => undefined);
  // An older daemon has no extensions. One that drops for a moment keeps the list it gave: an open
  // view stays and says it may be stale, instead of closing under the reader.
  const extensionRequest = useRef(0);
  const reloadExtensions = useCallback(async () => {
    const request = ++extensionRequest.current;
    try {
      const next = await api.extensions();
      if (request === extensionRequest.current) setExtensions(next);
    } catch {
      if (request === extensionRequest.current) setExtensions(cur => cur ?? []);
    }
  }, [api]);
  const changeExtension = (next: ExtensionState) => {
    extensionRequest.current++;
    setExtensions(list => next.installed || next.source === 'builtin' ? [...(list ?? []).filter(e => e.manifest.id !== next.manifest.id), next] : (list ?? []).filter(e => e.manifest.id !== next.manifest.id));
  };
  useEffect(() => {
    setExtensions(null);
    void reloadExtensions();
    return () => { extensionRequest.current++; };
  }, [reloadExtensions]);
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
  const reloadSchedules = useCallback(
    () => api?.schedules().then(setSchedules).catch(() => setSchedules([])),
    [api],
  );

  // Initial data once we have a token.
  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    (async () => {
      try {
        const [a, c, , s, sc] = await Promise.all([
          api.agents(),
          api.workdirs(),
          threadFeed.read(),
          api.sections(),
          api.schedules().catch(() => []),
        ]);
        if (cancelled) return;
        setAgents(a);
        setAgentsKnown(true);
        applyWorkdirs(c);
        setSections(s);
        setSchedules(sc);
        // What was kept only stands while the machine still offers it; a gone scratch folder or
        // instance falls back rather than keeping a choice that cannot start.
        if (draftScratch && c.scratch !== true) setDraftScratch(false);
        setDraftCwd((cur) => cur || c.workdirs[0] || '');
        const first = a.find((x) => x.available && !x.hidden);
        const kept = draftAgent && a.some((x) => x.id === draftAgent && !x.hidden) ? draftAgent : first?.id ?? '';
        selectDraftAgent(kept, a);
        if (!demo) {
          const dismissed = localStorage.getItem('calli_onboard_dismissed') === '1';
          const needsOnboard = a.length === 0 || a.filter((x) => x.available).length === 0;
          if (!dismissed && needsOnboard) {
            setOnboardStep('ask');
            setOnboardOpen(true);
          }
        }
        // Cheap enough to do once: PATH scan for other copies of known CLIs.
        api
          .discoverAgents()
          .then(setDiscovered)
          .catch(() => undefined);
        api
          .discoverAccounts()
          .then(setAccounts)
          .catch(() => undefined);
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

  // What a machine said is what its next cold start opens with. Written as it changes, not per
  // event: unchanged answers must not touch storage.
  useEffect(() => { if (keepThreads && agentsKnown) rememberAgents(hostId, agents); }, [keepThreads, agentsKnown, hostId, agents]);
  useEffect(() => { if (keepThreads) rememberModels(hostId, modelsByAgent); }, [keepThreads, hostId, modelsByAgent]);
  useEffect(() => {
    if (!keepThreads || selected || !draftAgent) return;
    rememberDraft(hostId, { agent: draftAgent, cwd: draftScratch ? '' : draftCwd, scratch: draftScratch && scratchAvailable });
  }, [keepThreads, hostId, selected, draftAgent, draftCwd, draftScratch, scratchAvailable]);

  // Thread-list changes, pushed by the daemon. Refresh is debounced.
  useEffect(() => {
    if (!api) return;
    threadFeed.stream(false);
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        void threadFeed.read().catch(() => undefined);
        // Plan chips show thread states; a bumped key reloads the open page.
        setTodosKey((k) => k + 1);
        // Scheduled runs finish and problems appear the same way.
        api.schedules().then(setSchedules).catch(() => undefined);
        setSchedulesKey((k) => k + 1);
      }, 150);
    };
    (async () => {
      for (let attempt = 0; !ctrl.signal.aborted; attempt++) {
        // Whatever changed while the stream was down was never announced on it. A dropped stream
        // is also how a restarted daemon looks from here, so it is a moment to look for a new build.
        if (attempt > 0) {
          refresh();
          checkForUpdate();
          void reloadExtensions();
        }
        try {
          await api.stream<StoredEvent>(
            '/api/stream',
            (ev) => {
              if (ev.type === 'extensions.changed') void reloadExtensions();
              else if (ev.type === 'quota.updated') {
                const { agent, quota } = ev.data as { agent: string; quota: PlanQuota };
                quotaPushed(api, agent, quota);
              } else if (ev.type === 'agent.modelsChanged') {
                const { agent, models } = ev.data as { agent: string; models: ModelOption[] };
                modelVersions.current[agent] = (modelVersions.current[agent] ?? 0) + 1;
                setModelsByAgent(previous => ({ ...previous, [agent]: models }));
                setModelLogin(current => models.length && current?.scope === api && current.client === api && current.key === agent ? null : current);
              } else refresh();
            },
            ctrl.signal,
            undefined,
            () => threadFeed.stream(true),
          );
        } catch (e) {
          if (e instanceof AuthError) return signOut(e.code === 'link_expired' ? t('auth.linkExpired') : e.code === 'link_revoked' ? t('auth.linkRevoked') : t('app.tokenExpired'));
          // On another machine, a stream that will not open is how its dropping first shows.
          if (api.host && !ctrl.signal.aborted) void reloadHosts();
        }
        // The demo has no hub; anywhere else, a stream that ended is being retried.
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

  // A page left open keeps running the build it loaded. When the daemon serves a newer one, say
  // so and offer to reload; asked when the list stream reconnects and when the page comes back
  // to the foreground, at most once in a while.
  const updateAskedAt = useRef(0);
  const checkForUpdate = () => {
    if (showcase) return;
    if (Date.now() - updateAskedAt.current < UPDATE_CHECK_MS) return;
    updateAskedAt.current = Date.now();
    void newerBuildAvailable().then((newer) => { if (newer) setNewVersion(true); });
  };

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
    const cached = api && selectedId && historyCutoff === undefined ? peekCachedPage(api, selectedId, undefined, undefined, mergedHandover)?.page ?? null : null;
    if (cached) {
      setEvents(cached.events); setHistoryTurnIds(cached.turnIds ?? []); setHasOlder(cached.hasOlder);
      setLoadingOlder(false); setHistoryReady(true); setEventsLoading(false);
      setHistoryRevision(cached.snapshotSeq); historyCursor.current = cached.nextCursor; olderRequest.current = null;
    } else {
      setEvents([]); setHistoryTurnIds([]); setHasOlder(false); setLoadingOlder(false); setHistoryReady(false);
      setEventsLoading(Boolean(api && selectedId)); historyCursor.current = null; olderRequest.current = null;
    }
    if (!api || !selectedId) return;
    const ctrl = new AbortController();
    stickToBottom.current = true; setAtBottom(true);
    const callbacks: Parameters<typeof watchHistory>[3] = {
      chain: mergedHandover, maximumSeq: historyCutoff, pinned: selectedId === routerThreadId,
      page: page => {
        setEvents(page.events); setHistoryTurnIds(page.turnIds ?? []); setEventsLoading(false); setHistoryReady(true);
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
    };
    void watchHistory(api, selectedId, ctrl.signal, callbacks);
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, selectedId, mergedHandover, historyEpoch, historyCutoff]);

  /** Prepends the page of turns before the loaded ones, keeping the scroll position on the same content. */
  const loadOlder = async () => {
    const id = selectedId;
    const first = historyCursor.current ?? 0;
    if (!api || !id || loadingOlder || olderRequest.current || first <= 1) return;
    const request = {}; olderRequest.current = request;
    // Loading older turns is an explicit upward reading intent; unpin immediately.
    stickToBottom.current = false;
    setLoadingOlder(true);
    try {
      const page = await historyPage(api, id, first, undefined, mergedHandover);
      const older = page.events;
      // The answer is for the thread that asked; another one may be open by now.
      if (shownThread.current !== id || olderRequest.current !== request) return;
      setHasOlder(page.hasOlder); historyCursor.current = page.nextCursor;
      const firstTurn = older.find(event => event.type === 'turn.started') ?? older[0];
      readingPosition.preparePrepend(firstTurn?.seq);
      setEvents((prev) => prependHistory(older, prev));
    } catch (e) {
      if (olderRequest.current === request) { setJump(null); handleError(e); }
    } finally {
      if (olderRequest.current === request) { olderRequest.current = null; setLoadingOlder(false); }
    }
  };

  // Settings keep the active conversation in memory; coming back restores its reading position.
  useLayoutEffect(() => {
    if (onSettings) return;
    const origin = settingsOrigin.current;
    settingsOrigin.current = null;
    const el = scrollerRef.current;
    if (!origin || origin.thread !== selectedId || !el) return;
    stickToBottom.current = origin.pinned;
    el.scrollTop = origin.pinned ? el.scrollHeight : origin.scroll;
    setAtBottom(isNearBottom(el));
  }, [onSettings, selectedId]);

  // Keep the view pinned to the bottom while the user has not scrolled up.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [events, selected?.autoHandover?.state, selected?.autoHandover?.step]);

  // Screenshots and slow-rendering blocks grow the thread after the scroll above, which used to
  // leave a reopened thread parked at the top. Watching the thread's height keeps it pinned. The
  // scroller is watched too: a phone keyboard shrinks it and would hide the newest lines.
  useEffect(() => {
    const el = scrollerRef.current;
    const content = el?.querySelector('.merged-history, .thread');
    if (!el || !content) return;
    const ro = new ResizeObserver(() => {
      if (stickToBottom.current) { readingPosition.capture(); el.scrollTop = el.scrollHeight; }
      // Growth below an unpinned view moves nothing, so no scroll event reports that the view
      // has fallen behind; the jump button has to learn it here.
      else {
        readingPosition.restore();
        setAtBottom(isNearBottom(el));
      }
    });
    ro.observe(content);
    ro.observe(el);
    return () => ro.disconnect();
  }, [selected?.id, eventsLoading, mergedHandover, onSettings]);

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
    if (readingPosition.didRestore()) { setAtBottom(isNearBottom(el)); return; }
    const near = isNearBottom(el);
    const byHand = gesture.current.held || Date.now() - gesture.current.at < GESTURE_GRACE_MS;
    if (near) stickToBottom.current = true;
    else if (byHand) stickToBottom.current = false;
    readingPosition.capture();
    setAtBottom(near);
  };

  // The turn a search led to: shown once it is on the page, which for an old turn means fetching back to it.
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
  const draftTeamDefinition = draftTeam ? teamDefinitions.find(team => team.id === draftTeam) : undefined;
  const activeAgent = selected?.agent ?? draftTeamDefinition?.entryAgent ?? draftAgent;
  const activeCwd = selected?.cwd ?? draftCwd;
  const commandsKey = `${activeAgent}\n${activeCwd}`;
  const activeCatalog = useRef({ api, agent: activeAgent });
  activeCatalog.current = { api, agent: activeAgent };
  useEffect(() => { setModelLogin(null); }, [api, activeAgent]);
  const acceptModelCatalog = (agent: string, list: ModelOption[], client = api, id = agent) => {
    setModelsByAgent(previous => ({ ...previous, [agent]: list }));
    const info = agents.find(item => item.id === agent) ?? remoteAgents.find(item => item.id === agent);
    if (!list.length && info?.available && info.capabilities.modelCatalog !== false) {
      setModelLogin({ agent: id, key: agent, client, scope: api, label: info.label, login: info.login, empty: true });
      return;
    }
    setModelLogin(current => current?.scope === api && current.key === agent ? null : current);
  };
  const handleModelError = (error: unknown, agent: string, client = api, key = agent, report = true) => {
    if (!needsAgentLogin(error)) { if (report) handleError(error); return; }
    modelVersions.current[key] = (modelVersions.current[key] ?? 0) + 1;
    setModelsByAgent(previous => { const next = { ...previous }; delete next[key]; return next; });
    const info = agents.find(item => item.id === key) ?? remoteAgents.find(item => item.id === key);
    setModelLogin({ agent, key, client, scope: api, label: info?.label ?? agent, login: info?.login });
  };
  const lastFocusModelRefresh = useRef<{ agent: string; at: number } | null>(null);
  const refreshActiveModels = useCallback((force = false) => {
    if (!api || !activeAgent || document.hidden) return;
    const now = Date.now();
    if (!force && lastFocusModelRefresh.current?.agent === activeAgent && now - lastFocusModelRefresh.current.at < 1000) return;
    lastFocusModelRefresh.current = { agent: activeAgent, at: now };
    const version = modelVersions.current[activeAgent] ?? 0;
    // Opening the picker refreshes optional metadata instead of reusing the cached catalog.
    void api
      .models(activeAgent, force)
      .then((list) => {
        if (activeCatalog.current.api === api && activeCatalog.current.agent === activeAgent && (modelVersions.current[activeAgent] ?? 0) === version) acceptModelCatalog(activeAgent, list);
      })
      .catch(error => { if (activeCatalog.current.api === api && activeCatalog.current.agent === activeAgent && (modelVersions.current[activeAgent] ?? 0) === version) handleModelError(error, activeAgent, api, activeAgent, force); });
  }, [api, activeAgent]);

  // Models of an agent other than the thread's own, for choosing what its members start with.
  const modelsAsked = useRef(new Set<string>());
  const needModels = (agent: string, refresh = false) => {
    if (!api || modelsAsked.current.has(agent) || (!refresh && modelsByAgent[agent])) return;
    modelsAsked.current.add(agent);
    const version = modelVersions.current[agent] ?? 0;
    // An instance of another machine is asked there, under the id it has on that machine.
    const remote = remoteAgents.find((a) => a.id === agent);
    const client = remote && token ? new Api(token, '', remote.host) : api;
    const id = remote && token ? agent.slice(0, -(remote.host.length + 1)) : agent;
    client.models(id, refresh).then((list) => {
      if ((modelVersions.current[agent] ?? 0) === version) acceptModelCatalog(agent, list, client, id);
    }).catch(error => { if ((modelVersions.current[agent] ?? 0) === version) handleModelError(error, id, client, agent, refresh); }).finally(() => { modelsAsked.current.delete(agent); });
  };

  // Models for that agent, loaded once per agent.
  useEffect(() => {
    if (!api || !activeAgent || modelsByAgent[activeAgent]?.length) return;
    let live = true;
    const version = modelVersions.current[activeAgent] ?? 0;
    api
      .models(activeAgent)
      .then((list) => {
        if (live && (modelVersions.current[activeAgent] ?? 0) === version) acceptModelCatalog(activeAgent, list);
      })
      .catch(error => { if (live && (modelVersions.current[activeAgent] ?? 0) === version) handleModelError(error, activeAgent); });
    return () => { live = false; };
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

  // Import follow-up: while a thread with a session is open and the page is visible, check now and
  // then whether the terminal added turns to it. New turns arrive through the thread's event stream.
  useEffect(() => {
    if (!api || !selectedId || !selected?.agentSessionId || running || pageHidden) return;
    let stopped = false;
    const check = () => api.syncThread(selectedId).catch(() => undefined);
    void check();
    const timer = setInterval(() => {
      if (!stopped && !document.hidden) void check();
    }, 30_000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
    // `selected` is read for agentSessionId only; the poll itself only needs the id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, selectedId, selected?.agentSessionId, running, pageHidden]);

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

  // The file panel may browse any thread; switching the conversation closes it.
  const [filesThreadId, setFilesThreadId] = useState<string | null>(null);
  const [files, setFiles] = useState<FilesTarget | null>(null);
  const filesThread = threads.find(thread => thread.id === filesThreadId) ?? selected;
  /** The thread's trace, beside the conversation on a desktop, a page of its own on a phone. */
  const [traceOpen, setTraceOpen] = useState(false);
  const [gitOpen, setGitOpen] = useState(false);
  const [referenceId, setReferenceId] = useState<string | null>(null);
  const referenceOrigin = useRef<{ focus: HTMLElement | null; top: number; offset: number } | null>(null);
  const referenceOverlay = !useMinWidth(901);
  const openReference = (id: string) => {
    const focus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    referenceOrigin.current ??= { focus, top: scrollerRef.current?.scrollTop ?? 0, offset: focus?.getBoundingClientRect().top ?? 0 };
    stickToBottom.current = false;
    setFiles(null); setTraceOpen(false); setGitOpen(false); setAgentsOpen(false); setOverviewDetail(null);
    setReferenceId(id);
  };
  useLayoutEffect(() => {
    const origin = referenceOrigin.current;
    if (!origin || !scrollerRef.current) return;
    if (referenceId) {
      if (origin.focus?.isConnected) scrollerRef.current.scrollTop += origin.focus.getBoundingClientRect().top - origin.offset;
    } else {
      stickToBottom.current = false;
      scrollerRef.current.scrollTop = origin.top;
      origin.focus?.focus({ preventScroll: true });
      referenceOrigin.current = null;
    }
  }, [referenceId]);
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!referenceId || !referenceOverlay || !main) return;
    main.inert = true;
    return () => { main.inert = false; };
  }, [referenceId, referenceOverlay]);
  useLayoutEffect(() => { setReferenceId(null); referenceOrigin.current = null; }, [api, selectedId, onPage]);
  useEffect(() => { if (files || traceOpen || agentsOpen || gitOpen) setReferenceId(null); }, [files, traceOpen, agentsOpen, gitOpen]);
  useLayoutEffect(() => { setGitOpen(false); }, [selectedId, api]);
  useEffect(() => { if (files || traceOpen || agentsOpen) setGitOpen(false); }, [files, traceOpen, agentsOpen]);
  const filesSeq = useRef(0);
  useEffect(() => { setFiles(null); setFilesThreadId(null); setTraceOpen(false); }, [selectedId]);

  // The overview: a popover on narrow screens and the pages, a standing panel beside the
  // conversation on a wide one. Its switch is remembered here, and starts open.
  const overviewDock = useMinWidth(OVERVIEW_DOCK_MIN_WIDTH) && (!onPage || onSteward);
  const [overviewOpen, setOverviewOpen] = useStoredFlag(OVERVIEW_OPEN_KEY, true);
  const [overviewDetail, setOverviewDetail] = useState<{ api: Api; threadId: string; section: OverviewSection; returnToPreview: boolean } | null>(null);
  const [overviewSignal, setOverviewSignal] = useState(0);
  const [goalDetailsExpanded, setGoalDetailsExpanded] = useState<{ threadId: string; expanded: boolean } | null>(null);
  const onGoalDetailsChange = useCallback((threadId: string, expanded: boolean) => {
    setGoalDetailsExpanded(current => current?.threadId === threadId && current.expanded === expanded ? current : { threadId, expanded });
  }, []);
  useEffect(() => {
    if (goalDetailFocus?.api === api && (goalDetailFocus.threadId === null ? onSteward : goalDetailFocus.threadId === selectedId && !onPage)) setOverviewSignal(signal => signal + 1);
  }, [goalDetailFocus, api, selectedId, onPage, onSteward]);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const activeOverviewDetail = overviewDetail?.api === api && overviewDetail?.threadId === selectedId && !onPage ? overviewDetail : null;
  useLayoutEffect(() => { setOverviewDetail(d => d && d.api === api && d.threadId === selectedId && !onPage ? d : null); setOverviewSignal(0); }, [api, selectedId, onPage]);
  const closeOverviewDetail = () => {
    setOverviewDetail(null);
    if (overviewDock && !activeOverviewDetail?.returnToPreview) setOverviewOpen(false);
    else if (!overviewDock && activeOverviewDetail?.returnToPreview) setOverviewSignal(signal => signal + 1);
  };
  /** A side panel takes the same edge as the overview, so the panel steps aside while one is open —
   *  but the person's own switch is left alone: it comes back when the side panel closes. */
  const [preview, setPreview] = useState<{ threadId: string; path: string } | null>(null);
  const [diffFile, setDiffFile] = useState<{ threadId: string; file: ChangedFile } | null>(null);
  const [picture, setPicture] = useState<{ threadId: string; path: string; name: string } | null>(null);
  useLayoutEffect(() => {
    setPreview(value => !onPage && value?.threadId === selectedId ? value : null);
    setDiffFile(value => !onPage && value?.threadId === selectedId ? value : null);
    setPicture(value => !onPage && value?.threadId === selectedId ? value : null);
  }, [selectedId, onPage]);
  useLayoutEffect(() => { setPreview(null); setDiffFile(null); setPicture(null); }, [api]);
  const dockOpen = !compact && Boolean(activeOverviewDetail || preview || diffFile || picture);
  const sideKeys = Boolean(referenceId) || Boolean(agentsOpen && coordination) || Boolean(files) || traceOpen || gitOpen;
  const wasSide = useRef(false);
  const wasDock = useRef(false);
  useEffect(() => {
    if (sideKeys && !wasSide.current) { setPreview(null); setDiffFile(null); setPicture(null); setOverviewDetail(null); }
    if (dockOpen && !wasDock.current) { setFiles(null); setTraceOpen(false); setAgentsOpen(false); setGitOpen(false); setReferenceId(null); }
    wasSide.current = sideKeys; wasDock.current = dockOpen;
  }, [sideKeys, dockOpen]);
  const sideOpen = sideKeys || dockOpen;
  const overviewRoom = overviewDock && overviewOpen && !sideOpen && !activeOverviewDetail && (onSteward || (Boolean(selected || threadPending) && hostUp));
  const overviewShown = overviewRoom && Boolean(selected || onSteward);
  /** Only the person turns the standing panel off; the switch above remembers it. */
  const closeOverview = useCallback(() => { if (overviewDock) setOverviewOpen(false); }, [overviewDock, setOverviewOpen]);
  // A page the agent wrote, rendered in a sandboxed iframe.
  const openFileLink = useCallback(
    async (raw: string) => {
      if (!api || !selectedId) throw new Error('no thread');
      const ref = fileRef(raw);
      // Asked first, so a file the thread cannot show falls back to copying its path.
      let file;
      try {
        file = await api.threadText(selectedId, ref.path);
      } catch (error) {
        // The link may name a folder: stand the file browser's tree in it instead.
        if (error instanceof ApiError && error.status === 404) {
          try {
            const listing = await api.threadTree(selectedId, ref.path);
            setFilesThreadId(selectedId);
            setAgentsOpen(false);
            setFiles({ path: null, folder: listing.path, seq: ++filesSeq.current });
            return;
          } catch {
            // Not a folder either: the original error stands.
          }
        }
        throw error;
      }
      setFilesThreadId(selectedId);
      setAgentsOpen(false);
      setFiles({ path: file.path, line: ref.line, file, seq: ++filesSeq.current });
    },
    [api, selectedId],
  );

  useEffect(() => {
    const onVisibility = () => {
      setPageHidden(document.hidden);
      if (!document.hidden) checkForUpdate();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
    // checkForUpdate only reads a ref and calls a state setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The thread on screen is being read. With the page in the background, nothing is.
  const viewingId = pageHidden ? null : selectedId;
  // Keep a count of threads that want attention in the tab title; the sidebar rows carry it inside
  // the app. What happens off-screen is not announced over the page.
  useEffect(() => {
    document.title = pageTitle(baseTitle.current, attentionCount(threads, viewingId));
  }, [threads, viewingId]);

  // The undo for an archive waits a while, then the offer goes; the archive page still has the thread.
  useEffect(() => {
    if (!archivedUndo) return;
    const timer = window.setTimeout(() => setArchivedUndo(null), 8000);
    return () => window.clearTimeout(timer);
  }, [archivedUndo]);

  // Looking at a thread marks it read, on every device.
  const selectedUnread = Boolean(selected?.unread);
  useEffect(() => {
    if (!api || !selectedId || !selectedUnread || pageHidden) return;
    api
      .updateThread(selectedId, { unread: false })
      .then((updated) => setThreads((prev) => prev.map((t) => (t.id === updated.id ? updated : t))))
      .catch(() => undefined);
  }, [api, selectedId, selectedUnread, pageHidden]);

  /** Shows a thread that was just made here: imported, forked, or brought back from the archive. */
  const openThread = (thread: Thread, draft?: string) => {
    if (draft !== undefined) prefillComposerDraft(hostId ? `host:${hostId}:${thread.id}` : thread.id, draft);
    setThreads((prev) => [thread, ...prev.filter((t) => t.id !== thread.id)]);
    setSingleSegmentId(null);
    setSelectedId(thread.id);
    setView('thread');
    setDrawer(false);
    void refreshThreads(); // the server decides the order
  };

  /**
   * Opens the thread a message names by id, whole or as the short prefix the assistant cites.
   * A thread outside the list (archived, on another screen) is still fetched and opened; an id
   * nothing answers is left to the error banner rather than quietly doing nothing.
   */
  const openThreadById = (id: string) => {
    const known = threads.find(t => t.id === id);
    if (known) { openThread(known); return; }
    const ref = id.toLowerCase();
    const prefixed = threads.filter(t => t.id.toLowerCase().startsWith(ref));
    if (prefixed.length === 1) { openThread(prefixed[0]!); return; }
    if (api) void api.thread(id).then(openThread).catch(handleError);
  };

  const autotitleExt = extensions?.find((e) => e.manifest.id === 'autotitle');
  const boardExt = extensions?.find((e) => e.manifest.id === 'board');
  const threadActions = useThreadActions({
    api,
    autotitle: Boolean(autotitleExt?.source === 'builtin' && autotitleExt.installed && autotitleExt.enabled),
    board: Boolean(boardExt?.source === 'builtin' && boardExt.installed && boardExt.enabled),
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
      setArchiveKey((k) => k + 1);
      void refreshThreads(); // the server decides the order
    },
    onThreadRemove: (id) => {
      setThreads((prev) => prev.filter((t) => t.id !== id));
      if (selectedId === id) setSelectedId(null);
      setArchiveKey((k) => k + 1);
    },
    onSectionsChange: () => {
      api?.sections().then(setSections).catch(handleError);
      void refreshThreads();
    },
    onOpenTerminal: () => (demo ? askToConnect() : openTerminal()),
    onOpenGit: () => { setFiles(null); setTraceOpen(false); setAgentsOpen(false); setGitOpen(true); },
    onOpenFiles: (thread) => {
      setAgentsOpen(false);
      setTraceOpen(false);
      setFilesThreadId(thread.id);
      setFiles({ path: null, seq: ++filesSeq.current });
    },
    // The bar hands its trace mark to the menu on a wide screen; a phone reaches it from the
    // overview panel's foot.
    onOpenTrace: compact ? undefined : () => { setAgentsOpen(false); setFiles(null); setTraceOpen(true); },
    onError: handleError,
    onBusy: setBusyLine,
    onArchived: (thread) => setArchivedUndo({ thread, nonce: Date.now() }),
    // The handover chain record lives in the thread menu, not as chrome above the conversation.
    chain: api.demo ? undefined : {
      threads,
      isMerged: (thread) => (thread.id === selectedId ? mergedHandover : singleSegmentId !== thread.id),
      onToggle: (thread) => {
        setSingleSegmentId((thread.id === selectedId ? mergedHandover : singleSegmentId !== thread.id) ? thread.id : null);
        setSelectedId(thread.id);
        setView('thread');
        setDrawer(false);
      },
      onSelect: (id) => pick(id),
      onRefresh: () => { setSingleSegmentId(null); void refreshThreads(); },
    },
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

  // A new thread is created by its first message, or by starting voice on the draft.
  const createDraftThread = async (isNewThread: boolean, onCreated?: (threadId: string) => void) => {
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
    onCreated?.(id);
    if (draftWriting) {
      const updated = await api.setWriting(id, draftWriting);
      setThreads(prev => [updated, ...prev.filter(v => v.id !== updated.id)]);
      setDraftWriting(null);
    }
    setSelectedId(id);
    setDraftSectionId(null);
    // Every worktree is a new branch; the next thread starts in place unless asked again.
    setDraftWorktree(false);
    return id;
  };

  const performSend = async (text: string, images: PreparedImage[], isNewThread: boolean, mentions?: AgentMention[], videos: PreparedVideo[] = [], onCreated?: (draftKey: string) => void) => {
    setError(null);
    let id = selectedId;
    const created = (threadId: string) => onCreated?.(hostId ? `host:${hostId}:${threadId}` : threadId);
    try {
      // A Goal is reviewed by the LoopX core before it exists; the draft stays in the box until it is created or backed out of.
      if (!id && draftLoopx && !draftTeam) {
        if (images.length || videos.length) throw new Error(t('loopx.goal.noMedia'));
        return await new Promise<void>((resolve, reject) => setPendingGoal({ text, onCreated: created, resolve, reject }));
      }
      if (!id && draftTeam) {
        if (videos.length) throw new Error(t('teams.attachments'));
        const task = await api.dispatchTeam(draftTeam, text, draftCwd, images.map(({ data, mimeType }) => ({ data, mimeType })));
        created(task.threadId);
        setSelectedId(task.threadId); setDraftTeam(null); setDraftSectionId(null);
        setView('thread'); void refreshThreads(); return;
      }
      if (!id) id = await createDraftThread(isNewThread, created);
      // Bringing an agent in with @ is itself the request to collaborate; there is no switch to find first.
      const collaboration = (draftCollaboration || mentions?.length) && !selected?.coordination
        ? { directories: collaborationDirectories.length ? collaborationDirectories : undefined, defaults: startingDefaults, permission: memberPermission } : undefined;
      stickToBottom.current = true;
      // "queue" only matters while a turn is running: then the message waits instead of being refused.
      await api.startTurn(
        id,
        text,
        images.map(({ data, mimeType }) => ({ data, mimeType })),
        { queue: true, mentions, collaboration, videos: await Promise.all(videos.map(async video => (await api.uploadVideo(id!, video.file)).id)) },
      );
      void refreshThreads();
    } catch (e) {
      // The composer keeps the draft; the dialog says it will still be there.
      if (e instanceof DemoError) {
        if (showcase) showcase.onInstall();
        else setConnectAsk({ reason: t('app.connectToSend') });
      }
      else if (!(e instanceof SendCancelled)) handleError(e, id);
      throw e;
    }
  };

  const send = async (text: string, images: PreparedImage[], mentions?: AgentMention[], videos: PreparedVideo[] = [], onCreated?: (draftKey: string) => void) => {
    // The terminal wrote to this session after the hub last did. Both sides writing the same session
    // interleaves turns, so ask before joining in again.
    // The composer keeps the draft and its pictures until this settles, so backing out loses nothing.
    if (selectedId && selected?.terminalActivity) {
      return new Promise<void>((resolve, reject) => setPendingSend({ text, images, videos, mentions, resolve, reject }));
    }
    await performSend(text, images, true, mentions, videos, onCreated);
  };

  // Stopping means stopping: messages that were waiting are not sent. Their text goes back in the box.
  const stop = () => {
    if (!selectedId) return;
    api
      .cancel(selectedId)
      .then(({ dropped, mentions }) => {
        if (dropped.length || mentions?.length) setRestore({ text: dropped.join('\n\n'), mentions, nonce: Date.now() });
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
    // The text goes back in the box now; videos and files follow once they are downloaded again.
    return { ...taken, videos: restoreVideos(api, selectedId, taken.videos) };
  };

  const sendQueuedNow = (queuedId: string) => {
    if (selectedId) api.sendQueuedNow(selectedId, queuedId).then(refreshThreads).catch(handleError);
  };

  const decide = async (approvalId: string, optionId: string | null) => {
    const id = selectedId;
    const body = optionId === null ? { cancel: true as const } : { optionId };
    const reconcile = async () => {
      if (!id || api.demo) return false;
      const page = await refreshHistoryPage(api, id, mergedHandover);
      if (shownThread.current === id && shownApi.current === api) {
        setEvents(previous => shownThread.current === id && shownApi.current === api ? mergeHistoryPage(previous, page) : previous);
        setHistoryRevision(revision => Math.max(revision, page.snapshotSeq));
      }
      return page.events.some(ev => ev.type === 'approval.resolved' && ev.data.approvalId === approvalId);
    };
    try {
      await api.decide(approvalId, body);
    } catch (e) {
      // A timeout or another tab's decision is ambiguous: trust the server's summary, not a guess.
      if (!(e instanceof DemoError || e instanceof AuthError || e instanceof ApiError && e.details.code === 'host_offline')) {
        if (await reconcile().catch(() => false)) return;
      }
      // Keep ordinary submission errors beside the decision, not in a second global banner.
      if (e instanceof DemoError || e instanceof AuthError || e instanceof ApiError && e.details.code === 'host_offline') handleError(e);
      throw e;
    }
    if (!api.demo && !await reconcile().catch(() => false)) throw new Error(t('sheet.approval.syncError'));
  };

  const pick = (id: string) => navigate(() => {
    setSingleSegmentId(null); setView('thread'); setSelectedId(id); setDrawer(false);
  });

  const openSettings = (path = '') => {
    if (onSettings && settingsPath === path) return;
    navigate(() => {
      if (!onSettings) {
        settingsOrigin.current = { view, thread: selectedId, hash: window.location.hash, scroll: scrollerRef.current?.scrollTop ?? 0, pinned: stickToBottom.current, entry: path, list: listOpen };
      }
      setThreadLink(null); setView('settings'); setSettingsPath(path); setDrawer(false);
    });
  };
  // The address follows the page (see the address effect); no history entry is pushed for a
  // level, so closing has nothing to walk back down.
  const closeSettings = () => navigate(() => {
    const origin = settingsOrigin.current;
    setSettingsPath(''); setView(origin?.view ?? 'thread'); setSelectedId(origin?.thread ?? null); setThreadLink(null);
    setDrawer(Boolean(origin?.list));
  });

  // The hub's "back", for a back swipe or the browser's back button (see appHistory.ts): the same
  // steps as the top-left button, one at a time. Nothing left to undo hands the swipe to the browser.
  appBack.current = () => {
    if (referenceId) { setReferenceId(null); return true; }
    if (listOpen) { setDrawer(false); return true; }
    if (!compact && diffFile) { setDiffFile(null); return true; }
    if (!compact && preview) { setPreview(null); return true; }
    if (!compact && picture) { setPicture(null); return true; }
    if (activeOverviewDetail) { closeOverviewDetail(); return true; }
    if (onSettings) {
      if (settingsPath.includes('/')) openSettings(settingsPath.slice(0, settingsPath.lastIndexOf('/')));
      else if (settingsPath && !(settingsOrigin.current?.entry === 'quota' && settingsPath === 'quota')) openSettings('');
      else closeSettings();
      return true;
    }
    if (onSchedules && schedulePath) { openSchedules(''); return true; }
    if (onMachines) {
      if (machinePath) { openMachines(''); return true; }
      setView('thread'); setSelectedId(machinesOrigin.current);
      return true;
    }
    if (compact) { setDrawer(true); return true; }
    return false;
  };

  const [peek, setPeek] = useState<{ machine: string | null; api: Api; thread: Thread; returnFocus: HTMLElement | null } | null>(null);
  useEffect(() => { if (!onExt) setPeek(null); }, [onExt]);
  const [remoteMenu, setRemoteMenu] = useState<{ machine: string | null; api: Api; thread: Thread; x: number; y: number } | null>(null);
  const openMachineMenu = (id: string, at: { x: number; y: number }) => {
    const machine = threadMachine(machineSources, id);
    const thread = machineSources.flatMap(source => source.threads).find(thread => thread.id === id)!;
    if (machine === hostId) threadActions.openThreadMenu(thread, at.x, at.y);
    else setRemoteMenu({ machine, api: new Api(token, '', machine), thread, ...at });
  };
  const threadApi = (id: string) => {
    const machine = threadMachine(machineSources, id);
    return machine === hostId ? api : new Api(token, '', machine);
  };
  const updateMachineThread = (machine: string | null, updated: Thread) => {
    const apply = (list: Thread[]) => updated.archivedAt ? list.filter(t => t.id !== updated.id)
      : list.some(t => t.id === updated.id) ? list.map(t => t.id === updated.id ? updated : t) : [updated, ...list];
    if (machine === hostId) setThreads(apply);
    else setElsewhere(sources => sources.map(source => source.hostId === machine ? { ...source, threads: apply(source.threads) } : source));
  };
  const patchBoardThread = async (id: string, patch: { done?: boolean; stage?: 'backlog' | 'doing' | 'blocked' | 'review' }) => {
    const machine = threadMachine(machineSources, id);
    updateMachineThread(machine, await threadApi(id).updateThread(id, patch));
  };
  const openBoardThread = (id: string) => {
    const machine = threadMachine(machineSources, id);
    if (machine === hostId) pick(id);
    else onHost?.(machine, { thread: id });
  };

  // The steward takes the place of a thread; Goal work stays in the shared overview.
  const openSteward = () => navigate(() => {
    setGoalRequest(null); setGoalDetailFocus(null); setThreadLink(null);
    setSelectedId(null); setView('steward'); setDrawer(false);
  });
  const openGoalsBoard = () => navigate(() => {
    setExtTarget(goalsBoardTarget(extensionStorage('board')));
    setBoardEntry(value => value + 1);
    setThreadLink(null); setSelectedId(null); setView('ext'); setDrawer(false);
  });
  const openGoal = (goalId: string | null, section: GoalSection | null = null) => {
    const target = goalDestination(goalId, threads);
    navigate(() => {
      setThreadLink(null);
      if (target.threadId) openThread(threads.find(thread => thread.id === target.threadId)!);
      else { setSelectedId(null); setView('steward'); setDrawer(false); }
      setGoalDetailFocus({ api, threadId: target.threadId, goalId, section });
      setOverviewOpen(true);
    });
  };
  openGoalRef.current = openGoal;
  // Keep the requested Goal identity while the host is reading its conversation bindings.
  // Bookmarks only select a reader; they never create a conversation or start Goal work.
  useEffect(() => {
    if (!goalRequest || !onSteward || !observedAt && !api.demo) return;
    setGoalRequest(null);
    openGoalRef.current(goalRequest.goalId, goalRequest.section);
  }, [goalRequest, onSteward, observedAt, api.demo, threads]);
  const [exploreOpen, setExploreOpen] = useState(false);
  const [setupNeeded, setSetupNeeded] = useState<boolean | null>(null);
  const [setupNotice, setSetupNotice] = useState<{ text: string; at: number } | null>(null);
  useEffect(() => {
    let live = true;
    setSetupNeeded(needsExplore(personal, null, null));
    if (api.demo || !personal?.agent) return;
    void Promise.all([api.personalProfile(), api.larkChannel()]).then(([profile, lark]) => {
      if (live) setSetupNeeded(needsExplore(personal, profile.isTemplate, lark));
    }).catch(() => undefined);
    return () => { live = false; };
  }, [api, personal]);
  const openExplore = () => navigate(() => setExploreOpen(true));
  const openSchedules = (path = '') => navigate(() => {
    setSchedulePath(path);
    setSelectedId(null);
    setView('schedules');
    setDrawer(false);
  });

  const openActivity = () => navigate(() => {
    setSelectedId(null);
    setView('activity');
    setDrawer(false);
  });

  const openObservability = (tab: 'usage' | 'diagnostics', focusAgent?: string) => navigate(() => {
    setSelectedId(null);
    setObservabilityTab(tab);
    setObservabilityFocus(focusAgent);
    setObservabilityKey((n) => n + 1);
    setView('observability');
    setDrawer(false);
  });

  const openMachines = (path = '') => navigate(() => {
    // Where the browser's back lands when the page itself is left: the thread it was opened from.
    if (view !== 'machines') machinesOrigin.current = selectedId;
    setSelectedId(null);
    setMachinePath(path);
    setMachineKey((n) => n + 1);
    setView('machines');
    setDrawer(false);
  });

  const reloadAgents = async () => {
    if (!api) return;
    const list = await api.agents();
    setAgents(list);
    if (!list.some((a) => a.id === draftAgent && !a.hidden)) {
      selectDraftAgent(list.find((a) => a.available && !a.hidden)?.id ?? '', list);
    }
  };

  const openAgents = () => { openSettings('agents'); void reloadAgents().catch(handleError); };

  const openTerminal = () => { setTerminalTarget(null); setTerminalClient(null); setTermOpen(true); };

  /** Starts an instance's CLI login in a fresh terminal and shows that terminal. */
  const openAgentLogin = async (agent: Pick<AgentInfo, 'id'>, client = api) => {
    const { terminalId } = await client.loginAgentTerminal(agent.id);
    setTerminalTarget(terminalId);
    setTerminalClient(client);
    setTermOpen(true);
  };

  const openTeams = () => { openSettings('teams'); reloadTeams(); };

  const openSkills = () => openSettings('skills');

  const openMcp = () => openSettings('mcp');

  const openExtensions = () => openSettings('extensions');

  /** Opens an extension's view by its address, the way the sidebar entry and a bookmark do. */
  const openExtensionView = (hash: string) => navigate(() => {
    setSelectedId(null);
    setExtTarget(hash);
    setView('ext');
    setDrawer(false);
  });
  // Views enabled extensions pin, after the hub's own entries.
  const extensionNav: ExtensionNavEntry[] = enabledViews(extensions ?? [])
    .filter((v) => v.view.sidebar?.pinned)
    .map((v) => ({
      key: `${v.extensionId}/${v.view.id}`,
      label: v.view.title[locale()],
      icon: v.view.icon,
      active: Boolean(extOpen && extOpen.extensionId === v.extensionId && extOpen.view.id === v.view.id),
      onOpen: () => openExtensionView(extensionHash(v)),
    }));

  const newThread = (opts: { cwd?: string; sectionId?: string | null; scratch?: boolean } = {}) => navigate(() => {
    void modelPreferences.refresh();
    if (showcase) { showcase.onInstall(); return; }
    setSelectedId(null);
    setView('thread');
    setDrawer(false);
    if (opts.cwd !== undefined) {
      setDraftCwd(opts.cwd);
      setDraftScratch(false);
    }
    if (opts.scratch) setDraftScratch(true);
    setDraftSectionId(opts.sectionId ?? null);
  });

  // A turn a search found: its thread is opened and the conversation is brought to that turn. An
  // archived thread comes back out of the archive, as it does when opened from anywhere else.
  const openHit = async (hit: SearchHit) => {
    if (!api) return;
    try {
      let thread = threads.find((t) => t.id === hit.threadId) ?? (await api.thread(hit.threadId));
      if (thread.archivedAt) {
        thread = await api.updateThread(hit.threadId, { archived: false });
        setArchiveKey((k) => k + 1);
      }
      openThread(thread);
      setJump({ threadId: hit.threadId, turnId: hit.turnId });
    } catch (e) {
      handleError(e);
    }
  };

  useEffect(() => {
    if (!threadLink || !token) return;
    const link = parseThreadLink(threadLink);
    if (!link) { setThreadLink(null); return; }
    if (link.machine !== null) {
      if (!hostsLoaded) return;
      if (!hosts.some(host => host.id === link.machine)) {
        setThreadLink(null); setError(t('app.machineNotFound'));
        window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
        return;
      }
    }
    if (hostId !== link.machine) { onHost?.(link.machine); return; }
    let active = true;
    const { id } = link;
    void api.thread(id).then(async thread => {
      if (!active) return;
      if (thread.archivedAt) {
        thread = await api.updateThread(id, { archived: false });
        if (active) setArchiveKey(key => key + 1);
      }
      if (active) {
        openThread(thread);
        const previewPath = new URLSearchParams(threadLink.split('?')[1]).get('preview');
        if (previewPath) setPreview({ threadId: id, path: previewPath });
      }
    }).catch(() => {
      if (active) setError(t('app.threadNotFound'));
    }).finally(() => {
      if (active) {
        setThreadLink(null);
        window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
      }
    });
    return () => { active = false; };
  }, [api, token, hostId, threadLink, hostsLoaded, hosts]);

  const agentLabel = (id: string) => agents.find((a) => a.id === id)?.label ?? id;
  const capsOf = (id: string) => agents.find((a) => a.id === id)?.capabilities;
  const activeCaps = capsOf(activeAgent);
  const sideAgent = sideAsk?.source?.agent ?? activeAgent;
  const sideAgentInfo = agents.find(agent => agent.id === sideAgent);
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

  // Writing mode: the thread's own, or the draft's until the first message creates the thread.
  const writing = selected ? selected.writing ?? null : draftWriting ? { stage: draftWriting, file: null } : null;
  const habitsPath = writing?.habits ?? null;
  // The habits note is looked for when the writing menu opens: it may not exist yet, and may have
  // been written by the last turn.
  const [habitsReady, setHabitsReady] = useState<boolean | null>(null);
  useEffect(() => { setHabitsReady(null); }, [habitsPath]);
  const checkWritingHabits = useCallback(async () => {
    if (!api || !selectedId || !habitsPath) return;
    try {
      await api.threadText(selectedId, habitsPath);
      setHabitsReady(true);
    } catch {
      setHabitsReady(false);
    }
  }, [api, selectedId, habitsPath]);
  const changeWriting = async (stage: WritingStage | null) => {
    if (!selected) { setDraftWriting(stage); return; }
    const id = selected.id;
    setWritingBusy(true);
    try {
      const updated = stage ? await api.setWriting(id, stage) : await api.endWriting(id);
      setThreads(prev => prev.map(v => v.id === id ? updated : v));
    } catch (error) { handleError(error); }
    finally { setWritingBusy(false); }
  };

  const permission = selected ? selected.permissionMode : draftPermission;
  const model = selected ? selected.model : draftTeam ? draftTeamDefinition?.entryModel ?? null : draftModel;
  const effort = selected ? selected.effort : draftEffort;
  const speed = selected ? selected.speed ?? null : draftSpeed;

  const updateSelected = (patch: { speed?: string | null; permissionMode?: string; model?: string | null; effort?: string | null }) => {
    if (!selected) return;
    // Shown at once: a control that waits for the round trip snaps back to the old value first.
    const id = selected.id;
    const before = { permissionMode: selected.permissionMode, model: selected.model, effort: selected.effort, speed: selected.speed };
    setThreads((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
    api
      .updateThread(id, { ...patch, rememberModelChoice: true })
      .then((updated) => {
        setThreads((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
        if (patch.model !== undefined || patch.effort !== undefined || patch.speed !== undefined) {
          void modelPreferences.refresh();
        }
      })
      .catch((e) => {
        setThreads((prev) => prev.map((t) => (t.id === id ? { ...t, ...before } : t)));
        handleError(e);
      });
  };

  const pickAgent = (id: string) => {
    selectDraftAgent(id);
  };

  // Adds a discovered CLI as its own instance and selects it for the new thread.
  const addDiscoveredAgent = async (found: DiscoveredAgent) => {
    if (!api) return;
    await api.addAgent({ id: found.id, adapter: found.adapter, ...(found.command ? { command: [found.command] } : {}) });
    const [list, more] = await Promise.all([api.agents(), api.discoverAgents()]);
    setAgents(list);
    setDiscovered(more);
  };

  // Links a signed-in account as its own instance and selects it for the new thread.
  const addAccount = async (account: DiscoveredAccount) => {
    if (!api) return;
    await api.addAgent({ id: account.id, adapter: account.adapter, label: account.label, env: account.env, account: account.email });
    const [list, rest] = await Promise.all([api.agents(), api.discoverAccounts()]);
    setAgents(list);
    setAccounts(rest);
  };

  const handleOnboardConnected = (freshList: AgentInfo[]) => {
    setAgents(freshList);
    const first = freshList.find((x) => x.available && !x.hidden);
    if (!draftAgent || !freshList.some((x) => x.id === draftAgent && !x.hidden)) {
      selectDraftAgent(first?.id ?? '', freshList);
    }
    if (api) {
      api.discoverAgents().then(setDiscovered).catch(() => undefined);
      api.discoverAccounts().then(setAccounts).catch(() => undefined);
    }
    localStorage.setItem('calli_onboard_dismissed', '1');
    setOnboardOpen(false);
  };

  const handleOnboardLater = () => {
    localStorage.setItem('calli_onboard_dismissed', '1');
    setOnboardOpen(false);
  };

  // What the slash menu offers besides the agent's own commands. The keywords include the names
  // these have in the agents' terminals (/clear, /resume, /export, /status), so habits still work.
  const hubActions: HubAction[] = [
    { id: 'new', label: t('sidebar.newThread'), hint: t('app.cmd.new'), keywords: ['new', 'clear'] },
    { id: 'status', label: t('statusPanel.status'), hint: t('app.cmd.status'), keywords: ['status', 'info'] },
    { id: 'term', label: t('term.title'), hint: t('app.cmd.term'), keywords: ['term', 'terminal', 'shell', '命令行'] },
    ...(hasDesktop ? [{ id: 'desktop', label: t('desktop.title'), hint: t('app.cmd.desktop'), keywords: ['desktop', 'vnc', 'screen', 'gui', '桌面', '屏幕'] }] : []),
    ...(agents.some((a) => !a.hidden && a.capabilities.quota)
      ? [{ id: 'usage', label: t('app.quota'), hint: t('app.cmd.usage'), keywords: ['usage', 'quota', '额度', 'cost'] }]
      : []),
    { id: 'activity', label: t('activity.title'), hint: t('app.cmd.activity'), keywords: ['activity', 'overview', 'trace', '概览', '轨迹'] },
    { id: 'skills', label: t('composerMenu.skills'), hint: t('app.cmd.skills'), keywords: ['skills', 'skill', '技能'] },
    { id: 'mcp', label: 'MCP', hint: t('app.cmd.mcp'), keywords: ['mcp', 'server', '业务线'] },
    { id: 'extensions', label: t('extensions.title'), hint: t('app.cmd.extensions'), keywords: ['extensions', 'extension', 'plugin', 'board', '扩展', '插件', '看板'] },
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
    // These are the hub's own machinery, which the demo has none of.
    if (demo && (id === 'term' || id === 'usage' || id === 'skills' || id === 'mcp')) return askToConnect();
    if (id === 'new') return newThread();
    if (id === 'status') return setStatusOpen(true);
    if (id === 'term') return openTerminal();
    if (id === 'desktop') return setDesktop('open');
    if (id === 'usage') return openSettings('quota');
    if (id === 'activity') return openActivity();
    if (id === 'skills') return openSkills();
    if (id === 'mcp') return openMcp();
    if (id === 'extensions') return openExtensions();
    if (id === 'export' && selected) return threadActions.copyConversation(selected);
    if (id === 'model') return setModelSignal((n) => n + 1);
    if (id === 'permission') return setPermissionSignal((n) => n + 1);
    if (id === 'appearance') return openSettings('appearance');
    if (selected && (id === 'rename' || id === 'pin' || id === 'fork' || id === 'archive')) threadActions.run(id, selected);
  };

  // The hub has its own controls for these; going through the agent would leave them out of sync.
  // The hub's own goal entries (the card, or the menu commands on agents without it) replace the
  // agent's raw /goal everywhere, as the phone's compact menu already did.
  const hiddenCommands = [...(models.length ? ['model'] : []), ...(effortLevels.length ? ['effort'] : []), ...(activeCaps?.quota ? ['usage'] : []), ...(activeCaps?.goal ? ['goal'] : [])];

  const attachBlocked = !selected && draftTeam && !draftTeamDefinition?.entryAgent ? t('teams.entryUnavailable') : !activeCaps?.images ? t('app.noImages', agentLabel(activeAgent)) : imagesBlocked(models, model);

  // The top bar names a thread's agent and directory, so the composer does not repeat them: its
  // strip is only for choosing them, before the thread exists.
  const header = selected || threadPending ? null : (
    <>
      <AgentPicker api={api} agents={agents} teams={teamDefinitions} value={draftTeam ? `team:${draftTeam}` : draftAgent} onChange={value => { const id = teamRecipient(value); setDraftTeam(id); if (id) { setDraftScratch(false); setDraftWorktree(false); setDraftCollaboration(false); } else pickAgent(value); }} accounts={accounts} onAddAccount={addAccount} />
      <ProjectPicker
        projects={projectOptions}
        machineLabel={host?.label ?? (hostId ? t('app.remoteMachine') : t('hosts.local'))}
        remote={Boolean(hostId)}
        cwd={draftCwd}
        scratch={draftScratch}
        scratchAvailable={scratchAvailable && !draftTeam}
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
        onManage={() => openSettings('machines')}
      />}
      {offer && !draftScratch && !draftTeam && <WorkspacePicker branch={offer.branch} changes={offer.changes} worktree={draftWorktree} onChange={setDraftWorktree} />}
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

  // The team's own choices once there is a team; until then, what this device remembers. A hidden
  // instance is not offered; one already in a running coordination stays, and instances that joined
  // the hub after it started are offered alongside them.
  const leading = Boolean(coordination && selected && coordination.rootId === selected.id);
  const teamAgents = agents.filter((a) => a.available && a.capabilities.coordination && (leading ? coordination!.agents.includes(a.id) || !a.hidden : !a.hidden));
  const teamDefaults: Record<string, MemberDefault> = leading ? coordination!.defaults ?? {} : memberDefaults;
  const startingDefaults = Object.fromEntries(Object.entries(memberDefaults).filter(([id]) => teamAgents.some((a) => a.id === id) || remoteAgents.some((a) => a.id === id)));
  const setMemberDefault = (agent: string, value: MemberDefault) => {
    const remembered = { ...memberDefaults, [agent]: value };
    setMemberDefaults(remembered);
    localStorage.setItem(memberDefaultsKey, JSON.stringify(remembered));
    if (leading && api) void api.configureCoordination(coordination!.rootId, { action: 'defaults', defaults: { ...teamDefaults, [agent]: value } }).then((team) => { setCoordination(team); void refreshThreads(); }).catch(handleError);
  };
  const teamPermission = leading ? coordination!.permission ?? 'full_access' : memberPermission;
  const chooseMemberPermission = (mode: string) => {
    setMemberPermission(mode);
    localStorage.setItem(`${memberDefaultsKey}.permission`, mode);
    // One level for everyone: what single instances were given goes with it.
    const cleared = Object.fromEntries(Object.entries(memberDefaults).map(([id, { permission: _, ...rest }]) => [id, rest]));
    setMemberDefaults(cleared);
    localStorage.setItem(memberDefaultsKey, JSON.stringify(cleared));
    if (leading && api) void api.configureCoordination(coordination!.rootId, { action: 'permission', permission: mode }).then((value) => { setCoordination(value); void refreshThreads(); }).catch(handleError);
  };
  const memberModel = (agent: AgentInfo) => {
    const value = teamDefaults[agent.id] ?? { model: null, effort: null };
    const level = value.permission ?? teamPermission;
    return (
      <MemberModel agent={agent} models={modelsByAgent[agent.id] ?? []} value={value} onNeed={needModels} onChange={(next) => setMemberDefault(agent.id, next)}
        permission={agent.capabilities.permissionModes.includes(level) ? level : undefined} onPermission={(permission) => setMemberDefault(agent.id, { ...value, permission })} />
    );
  };

  const collaborationOptions: AgentMention[] = (!draftTeam && !selected?.coordination?.team && (selected?.coordination ? selected.coordination.rootId === selected.id : activeCaps?.coordination)) ? [
            ...(coordination?.members.map(m => ({ id: m.id, label: m.name, kind: 'member' as const })) ?? []),
            // An instance hidden on purpose stays out; the roster follows the hub's live list, so
            // instances that joined after the collaboration started are offered here too.
            ...agents.filter(a => a.available && a.capabilities.coordination && (coordination ? coordination.agents.includes(a.id) || !a.hidden : !a.hidden)).map(a => ({ id: a.id, label: a.label, kind: 'agent' as const })),
            ...remoteAgents.map(a => ({ id: a.id, label: a.label, name: a.name, machine: a.machine, kind: 'agent' as const, host: a.host, directories: [...new Set([...recentRemoteDirs(a.host), ...a.directories])] })),
          ] : [];
  const collaborationIcon = (m: AgentMention) => [...agents, ...remoteAgents].find((a) => a.id === (m.kind === 'agent' ? m.id : coordination?.members.find((v) => v.id === m.id)?.thread?.agent))?.icon;

  const collaborationControl: NonNullable<ComponentProps<typeof Composer>['collaboration']> = (select, chosen, anchorRef, openSignal) => (<Popover label={t('app.collab')} panelClassName="coord-picker-panel" anchorRef={anchorRef} openSignal={openSignal}>
      {close => <CollaborationMenu options={collaborationOptions} selected={chosen} icon={collaborationIcon}
        onSelect={mention => { close(); select(mention); }}
        unavailable={selected?.coordination && !leading ? t('app.collabBack') : t('app.collabNone')}
        settings={<div className="coord-setup">
        <p>{t('app.collabHint')}</p>
        {!selected?.coordination && <>
          <div className="coord-caption">{t('app.collabDirs')}</div>
          {[...new Set([activeCwd, ...workdirs].filter(Boolean))].map(dir => <button className="pop-item compact" role="menuitemcheckbox" aria-checked={(collaborationDirectories.length ? collaborationDirectories : [activeCwd]).includes(dir)} key={dir} onClick={() => setCollaborationDirectories(prev => { const current = prev.length ? prev : [activeCwd]; return current.includes(dir) ? current.filter(v => v !== dir) : [...current, dir]; })}>{(collaborationDirectories.length ? collaborationDirectories : [activeCwd]).includes(dir) ? `${t('app.chosen')} · ` : ''}{dir}</button>)}
          {!running && <button className="pop-item compact" onClick={() => {
            if (selected) void api.configureCoordination(selected.id, { directories: collaborationDirectories.length ? collaborationDirectories : [selected.cwd], defaults: startingDefaults, permission: memberPermission }).then(value => { setCoordination(value); void refreshThreads(); close(); }).catch(handleError);
            else { setDraftCollaboration(v => !v); close(); }
          }}>{draftCollaboration ? t('app.collabOff') : t('app.collabEnable')}</button>}
        </>}
        {(!selected?.coordination || leading) && teamAgents.length > 0 && <>
          <div className="coord-caption">{t('app.memberPermission')}</div>
          {PERMISSION_ORDER.map((mode) => <button type="button" className="pop-item compact" role="menuitemradio" aria-checked={mode === teamPermission} key={mode} title={permissionHint(mode, true)} onClick={() => chooseMemberPermission(mode)}>
            <span className="pop-item-text"><span className="pop-item-label">{PERMISSION_LABELS[mode].label}</span></span>
            {mode === teamPermission && <span className="pop-item-check"><IconCheck size={15} /></span>}
          </button>)}
          <div className="coord-caption">{t('app.memberModel')}</div>
          {teamAgents.map((a) => <div className="coord-default" key={a.id}>
            <span className="pop-item-icon">{a.icon ? <AgentMark path={a.icon} size={15} /> : null}</span>
            <span className="coord-default-name">{a.label}</span>
            {memberModel(a)}
          </div>)}
        </>}
        {selected?.coordination && <button className="pop-item compact" onClick={() => { setAgentsOpen(true); setFiles(null); close(); }}>{t('team.view')}</button>}
      </div>} />}
    </Popover>);

  const left = (<>
    {goalOf(selected) ? null : !selected && draftLoopx ? (
      <GoalPermissionPicker capability={goalCap} value={draftGoalLevel} onChange={setDraftGoalPermission} />
    ) : (
      <PermissionPicker
        modes={permissionModes}
        value={permission}
        canAsk={activeCaps?.approvals ?? true}
        openSignal={permissionSignal}
        onChange={(m) => (selected ? updateSelected({ permissionMode: m }) : setDraftPermission(m))}
      />
    )}
  </>);

  // When the thread's agent has run out of plan, the one-click takeover must use the
  // daemon's quota-aware order, not just the next visible instance in the list.
  const lastItem = timeline.items[timeline.items.length - 1];
  const outOfPlan = Boolean(selected && !running && lastItem?.kind === 'error' && lastItem.planExhausted
    && (!selected.coordination || selected.coordination.rootId === selected.id));
  useEffect(() => {
    if (!selected || !outOfPlan || api.demo) { setTakeoverTarget(null); return; }
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    setTakeoverTarget(null);
    // Quota windows move fast (a running successor can cross the threshold and come back within
    // minutes), so an empty answer is retried while the card is up instead of hiding the button
    // until the thread is reopened.
    const probe = () => {
      if (!alive) return;
      void api.autoHandover(selected.id).then(value => {
        if (!alive) return;
        if (value.targets[0]) setTakeoverTarget({ threadId: selected.id, target: value.targets[0] });
        else timer = setTimeout(probe, TAKEOVER_RETRY_MS);
      }).catch(() => { if (alive) timer = setTimeout(probe, TAKEOVER_RETRY_MS); });
    };
    probe();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [api, api.demo, outOfPlan, selected?.id]);
  const takeoverChoice = selected && takeoverTarget?.threadId === selected.id ? takeoverTarget.target : null;
  const takeoverLabel = takeoverChoice ? [agents.find(a => a.id === takeoverChoice.agent)?.label ?? takeoverChoice.agent, takeoverChoice.model].filter(Boolean).join(' · ') : undefined;

  const teamWaiting = Boolean(coordination?.members.some((m) => m.thread?.waiting));
  const teamShown = Boolean(selected?.coordination && (selected.coordination.rootId !== selected.id || selected.coordination.children > 0) && !onPage && hostUp);
  // The overview is a panel from one top-bar button now; the button carries only what asks for the
  // person. The team it opens is the lead's own (a member's thread has none to show).
  const overviewTeam = teamShown && coordination?.rootId === selected?.id ? coordination : null;
  const teamNeeds = overviewTeam?.members.filter((member) => memberProgress(member) === 'need').length ?? 0;
  const waitingOnUser = loopxStatus?.attention.filter(item => loopxNeedsYou.has(item.goalId)) ?? [];
  const goalNeeds = selected?.loopxInfo && !selected.loopxInfo.missing && loopxNeedsYou.has(selected.loopxInfo.goalId) ? 1 : 0;
  const overviewNeeds = onSteward ? waitingOnUser.length : teamNeeds + goalNeeds;
  const overviewBusy = onSteward ? loopxRead.loading : overviewLoading;
  const goalsChanged = () => { loopxRead.reload(); void refreshThreads(); };
  // The conversation's turns, for naming the section each change belongs to in the diff dialog.
  const turnOrder = useMemo(() => {
    const seen = new Set<string>();
    const order: string[] = [];
    for (const item of timeline.items) {
      if (!item.turnId || seen.has(item.turnId)) continue;
      seen.add(item.turnId);
      order.push(item.turnId);
    }
    return order;
  }, [timeline.items]);
  // One file's whole change history, opened from the changes group; read only while it is open.
  // A member's own thread: it was sent from the lead's, which is where its way back goes.
  const atMember = Boolean(teamShown && selected!.coordination!.rootId !== selected!.id && !selected!.coordination!.retired);
  // Where the thread runs, beside its title; a page or a team task names itself instead.
  const placeShown = Boolean(selected && !viewTitle && !(host && !hostUp) && !selected.coordination?.team && !atMember);
  const leadTitle = atMember ? threads.find((v) => v.id === selected!.coordination!.rootId)?.title ?? '' : '';

  // Older daemons advertise /compact before they expose the dedicated capability.
  const canCompact = activeCaps?.compact ?? (commandsByKey[commandsKey] ?? []).some((command) => command.name === 'compact' && command.kind !== 'skill');
  // The agent's own context breakdown command, when its list has one (Claude Code's /context).
  const hasContextCommand = (commandsByKey[commandsKey] ?? []).some((command) => command.name === 'context' && command.kind !== 'skill');

  const right = (
    <>
      {selected && (timeline.usage || canCompact) && <UsageMeter
        key={selected.id}
        usage={timeline.usage}
        sample={timeline.lastSample}
        api={api}
        threadId={selected.id}
        onCompact={canCompact ? () => send('/compact', []) : undefined}
        onContext={hasContextCommand ? () => send('/context', []) : undefined}
        running={running}
        compactQueued={selected.queued?.some((message) => message.text.trim() === '/compact')}
        compactDisabled={(selected.queued?.length ?? 0) >= MAX_QUEUED}
      />}
      <ModelPicker
        preferenceStatus={!selected ? modelPreferences.status : undefined}
        onRetryPreference={() => void modelPreferences.refresh()}
        catalog={activeCaps?.modelCatalog}
        models={models}
        model={model}
        efforts={effortLevels}
        effort={effort}
        speed={speed}
        onSpeed={activeCaps?.speed ? v => selected ? updateSelected({ speed: v }) : modelPreferences.change(draftAgent, { model: draftModel, speed: v }) : undefined}
        onDiagnose={() => openObservability('diagnostics', activeAgent)}
        openSignal={modelSignal}
        onOpen={() => refreshActiveModels(true)}
        onModel={(v) => {
          if (selected) return updateSelected({ model: v });
          modelPreferences.change(draftAgent, { model: v });
        }}
        onEffort={(v) => selected ? updateSelected({ effort: v }) : modelPreferences.change(draftAgent, { model: draftModel, effort: v })}
      />

    </>
  );

  const settingsGroups: SettingsGroup[] = [
    { label: t('settings.preferences'), entries: [
      ...(!demo ? [{ id: 'general', label: t('general.title'), icon: IconSettings, render: () => <GeneralSettingsDialog api={api} onSave={value => { setStartupSettings(value); setExternalNonce(n => n + 1); }} onClose={closeSettings} /> }] : []),
      { id: 'appearance', label: t('appearance.title'), icon: IconPalette, render: () => <AppearancePanel appearance={appearance} theme={theme} onChange={setAppearance} onClose={closeSettings} /> },
      ...(!showcase ? [{ id: 'install', label: t('pwa.title'), icon: IconDownload, render: () => <PwaInstallSettings /> }] : []),
      { id: 'language', label: t('locale.menu'), icon: IconLanguage, render: () => <SettingsSection title={t('locale.menu')} hint={t('settings.automatic')}><div className="settings-options">
        {[{ id: 'system' as const, name: t('locale.system') }, ...LOCALES].map(l => <button key={l.id} className="settings-link-row" role="radio" aria-checked={localeChoice() === l.id} onClick={() => navigate(() => chooseLocale(l.id))}><span>{l.name}</span>{localeChoice() === l.id && <IconCheck size={16} />}</button>)}
      </div></SettingsSection> },
      ...(!demo ? [
        { id: 'push', label: t('push.title'), icon: IconBell, render: () => <PushSettingsDialog api={api} onClose={closeSettings} /> },
        { id: 'loopx-notify', label: t('loopx.notify.title'), icon: IconBell, render: () => <LoopxNotifySettings token={token} goals={loopxStatus?.goals ?? []} notifications={loopxStatus?.notifications ?? []} failed={loopxRead.failure !== null} onChanged={loopxRead.reload} /> },
        { id: 'feishu', label: t('feishu.title'), icon: IconSideChat, render: () => <FeishuSettingsDialog api={api} agents={agents} onClose={closeSettings} /> },
        { id: 'handover', label: t('auto.title'), icon: IconAgents, render: () => <HandoverSettingsDialog api={api} agents={agents} onClose={closeSettings} /> },
      ] : []),
    ] },
    { label: t('settings.resources'), entries: [
      ...(!demo ? [
        { id: 'agents', label: t('agents.title'), icon: IconAgents, render: () => <AgentsPage api={api} agents={agents} onAdd={() => { setOnboardStep('results'); setOnboardOpen(true); }} onReload={reloadAgents} onLogin={openAgentLogin} /> },
        { id: 'teams', label: t('teams.title'), icon: IconAgents, render: () => <TeamsPage api={api} agents={agents} onReload={reloadTeams} /> },
        { id: 'skills', label: t('composerMenu.skills'), icon: IconSparkle, render: () => <SkillsPage api={api} token={token} hostId={hostId} hosts={hosts} agents={agents} onError={handleError} /> },
        { id: 'mcp', label: 'MCP', icon: IconGlobe, render: () => <McpPage api={api} token={token} hostId={hostId} hosts={hosts} agents={agents} onError={handleError} /> },
        { id: 'machines', label: t('hosts.machines'), icon: IconServer, render: () => <HostsPanel api={api} token={token} hosts={hosts} onOpenUsage={machine => openMachines(machine ?? 'local')} onChanged={reloadHosts} onClose={closeSettings} onError={handleError} /> },
      ] : []),
      { id: 'extensions', label: t('extensions.title'), icon: IconPuzzle, render: () => <ExtensionsPage api={api} demo={demo} extensions={extensions} threads={threads} onReload={() => void reloadExtensions()} onChange={changeExtension} onOpenView={openExtensionView} onError={handleError} /> },
    ] },
    { label: t('settings.history'), entries: [
      { id: 'archive', label: t('archive.title'), icon: IconArchive, render: () => <ArchivePanel api={api} agents={agents} refreshKey={archiveKey} onRestore={thread => threadActions.run('restore', thread)} onDelete={thread => threadActions.run('delete', thread)} onError={handleError} /> },
      ...(agents.some(a => !a.hidden && a.capabilities.quota) ? [{ id: 'quota', label: t('app.quota'), icon: IconGauge, render: () => <QuotaBoard api={api} agents={agents} current={activeAgent} account={quotaAccount(settingsPath)} onAccount={id => openSettings(`quota/${encodeURIComponent(id)}`)} /> }] : []),
      { id: 'activity', label: t('activity.title'), icon: IconActivity, render: () => <ActivityPage api={api} agents={agents} onOpenThread={pick} onError={handleError} /> },
      ...(!demo ? [{ id: 'observability', label: t('obs.title'), icon: IconGauge, render: () => <ObservabilityPage api={api} agents={agents} initial="usage" /> }] : []),
      ...enabledViews(extensions ?? []).filter(v => v.view.sidebar?.pinned === false).map(v => ({ id: `ext:${v.extensionId}/${v.view.id}`, label: v.view.title[locale()], icon: (props: { size?: number }) => <HostIcon name={v.view.icon} {...props} />, onOpen: () => openExtensionView(extensionHash(v)) })),
    ] },
    { label: t('settings.account'), entries: [
      ...(demo ? [{ id: 'connect', label: t('connect.connectHub'), icon: IconSignIn, onOpen: () => navigate(askToConnect) }] : [
        { id: 'access', label: t('access.title'), icon: IconShield, render: () => <AccessSettings token={token} auth={authKind} onReplaced={next => onToken(next)} /> },
        { id: 'sign-out', label: t('app.signOut'), icon: IconSignOut, onOpen: () => setConfirmSignOut(true) },
      ]),
    ] },
  ];
  // The same body in both shapes: the standing panel beside the conversation, and the popover a
  // narrow window still gets. Only the standing one keeps itself open behind its own rows.
  const overviewChain = selected && !demo ? (() => {
    const group = handoverGroups(threads).find(group => group.members.some(item => item.id === selected.id));
    if (!group || group.members.length < 2) return undefined;
    return { rows: group.members, latestId: group.latest.id, viewingId: selected.id, onOpen: (id: string) => {
      const before = overviewHistoryBoundary(id, group.members);
      setOverviewSegment(before === undefined ? null : { api, threadId: id, before });
      setSingleSegmentId(null);
      setOverviewDetail(d => d && d.section === 'chain' && !compact ? { ...d, threadId: id } : d);
      pick(id);
    } };
  })() : undefined;
  const overviewBody = (close: () => void, persistent: boolean, detail?: OverviewSection) => onSteward ? (
    <StewardOverview focusedGoal={goalDetailFocus?.api === api && goalDetailFocus.threadId === null ? goalDetailFocus : undefined} token={token} goals={loopxStatus?.goals ?? []} attention={loopxStatus?.attention ?? []}
      failure={loopxRead.failure} loading={loopxRead.loading} onChanged={goalsChanged} onRetry={loopxRead.reload}
      threads={threads} defaultAgent={goalCaps.find(cap => cap.agent === draftAgent && cap.available)?.agent ?? goalCaps.find(cap => cap.available)?.agent ?? ''} pickDirectory={pickGoalDir}
      onOpenThread={id => { if (!persistent) close(); openThreadById(id); }}
      onBrowseFiles={threadId => { if (!persistent) close(); setFilesThreadId(threadId); setFiles({ path: null, seq: ++filesSeq.current }); }}
      onOpenAll={showcase ? undefined : () => { if (!persistent) close(); openGoalsBoard(); }} />
  ) : selected && api && (
    <ThreadOverview key={`${hostId ?? ''}:${selected.id}:${detail ?? 'preview'}`}
      goalExpanded={Boolean(selected.loopxInfo && !selected.loopxInfo.missing && (goalDetailsExpanded?.threadId === selected.id ? goalDetailsExpanded.expanded : goalDetailFocus?.api === api && goalDetailFocus.threadId === selected.id))}
      chain={overviewChain} detail={detail} onLoadingChange={setOverviewLoading}
      onViewAll={(section, returnToPreview = true) => setOverviewDetail({ api, threadId: selected.id, section, returnToPreview })}
      api={api} threadId={selected.id} updatedAt={selected.updatedAt} active={selected.status === 'running'}
      team={overviewTeam} agents={agents} plan={topPlan} roots={threadRoots}
      goal={selected.loopxInfo && !api.demo ? <LoopxGoalGroup token={token} thread={selected} goal={loopxStatus?.goals.find(goal => goal.goalId === selected.loopxInfo?.goalId)} attention={loopxStatus?.attention.find(item => item.goalId === selected.loopxInfo?.goalId)} onChanged={goalsChanged} onExpandedChange={onGoalDetailsChange} threads={threads} onBrowseFiles={threadId => { if (!persistent) close(); setFilesThreadId(threadId); setFiles({ path: null, seq: ++filesSeq.current }); }} initiallyExpanded={goalDetailFocus?.api === api && goalDetailFocus.threadId === selected.id && goalDetailFocus.goalId === selected.loopxInfo.goalId} section={goalDetailFocus?.section ?? null} /> : undefined}
      loadFile={loadFile} persistent={persistent}
      onOpenMember={(member) => { setAgentsStart(member ?? null); setAgentsOpen(true); setFiles(null); }}
      onOpenDiff={(file) => setDiffFile({ threadId: selected.id, file })}
      onOpenPicture={compact ? undefined : (path, name) => setPicture({ threadId: selected.id, path, name })}
      onOpenFile={async (path) => {
        if (/\.html?$/i.test(path)) setPreview({ threadId: selected.id, path });
        else await openFileLink(path);
      }}
      onPreview={(path) => setPreview({ threadId: selected.id, path })}
      onOpenTrace={() => { setAgentsOpen(false); setFiles(null); setTraceOpen(true); }}
      onClose={close}
    />
  );

  return (
    <QuotaNavigation.Provider value={() => openSettings('quota')}><div
      className={`app${listOpen ? ' drawer-open' : ''}${sidebarHidden && !compact ? ' sidebar-hidden' : ''}${sidebarDrag ? ' sidebar-resizing' : ''}`}
      style={{ '--sidebar-w': `${sidebarWidth}px` } as CSSProperties}
      onPointerDownCapture={() => { startupPending.current = false; }}
      onKeyDownCapture={() => { startupPending.current = false; }}
    >
      {remotePick && (remotePick.manage ? (
        <RootsDialog
          api={remotePick.api}
          roots={remotePick.manage}
          machine={remotePick.machine}
          onChange={(config) => openRemoteRoots(remotePick.host, config)}
          onCancel={() => setRemotePick((p) => (p ? { ...p, manage: null } : p))}
        />
      ) : (
        <DirectoryPicker
          api={remotePick.api}
          roots={remotePick.roots}
          selected={remotePick.selected}
          machine={remotePick.machine}
          onRootsChange={(config) => applyRemoteWorkdirs(remotePick.host, config)}
          onManageRoots={() => {
            // The dialog needs each root's own remove flag, so the machine is asked before it opens;
            // a machine that cannot answer leaves the picker standing with the error said.
            void remotePick.api.workdirs().then((config) => openRemoteRoots(remotePick.host, config)).catch(handleError);
          }}
          onSelect={(cwd) => { rememberRemoteDir(remotePick.host, cwd); remotePick.choose(cwd); }}
          onClose={() => setRemotePick(null)}
        />
      ))}
      {/* The roots dialog stands in for the picker it was opened from, the way it does remotely;
          from the project menu it still stands alone, with no picker behind it. */}
      {showDirPicker && projectDialog !== 'roots' && (
        <DirectoryPicker
          api={api}
          roots={workdirs}
          selected={draftCwd}
          machine={host?.label ?? (hostId ? t('app.remoteMachine') : t('hosts.local'))}
          onRootsChange={applyWorkdirs}
          onManageRoots={() => setProjectDialog('roots')}
          onSelect={(cwd) => {
            setDraftCwd(cwd);
            setDraftScratch(false);
          }}
          onClose={() => setShowDirPicker(false)}
        />
      )}
      {/* The same picker, answering a Goal join instead of setting the draft's directory. */}
      {goalDirAsk && createPortal(
        <div className="goal-directory-picker">
        <DirectoryPicker
          api={api}
          roots={workdirs}
          selected={draftCwd}
          machine={host?.label ?? (hostId ? t('app.remoteMachine') : t('hosts.local'))}
          onRootsChange={applyWorkdirs}
          onManageRoots={() => setProjectDialog('roots')}
          onSelect={cwd => { const ask = goalDirAsk; setGoalDirAsk(null); ask.resolve(cwd); }}
          onClose={() => { const ask = goalDirAsk; setGoalDirAsk(null); ask.resolve(null); }}
        />
        </div>, document.body
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
          }}
        />
      )}
      {projectDialog === 'roots' && <RootsDialog api={api} roots={roots} onChange={applyWorkdirs} onCancel={() => setProjectDialog(null)} />}
      {/* The sidebar, the conversation and the panels beside it are one sheet of paper on a
          desktop; the window's band shows around it. Overlays stay outside, over the window. */}
      <div className="shell">
      <aside className="sidebar">
        {/* footerNotice: while the login came through a link, its running-out is standing
            business — pinned at the sidebar's foot above the buttons, a tap from the fix. */}
        <Sidebar
          personalDirectory={personal?.directory}
          storagePrefix={showcase ? 'calli.showcase.sidebar' : undefined}
          showEmptySections={!showcase}
          threads={listedThreads}
          members={teamMembers}
          sections={sections}
          agents={sidebarAgents}
          selectedId={selectedId}
          onSelect={pick}
          onNew={() => newThread()}
          onNewIn={newThread}
          compact={compact}
          onSearch={api ? (q) => api.search(q) : undefined}
          onOpenHit={(hit) => void openHit(hit)}
          onOpenSettings={() => showcase ? showcase.onInstall() : openSettings()}
          hosts={hosts}
          hostId={hostId}
          onHost={onHost && ((id) => navigate(() => {
            setAllHosts(false);
            if (id !== hostId) onHost(id, 'list');
          }))}
          hostDown={Boolean(host) && !hostUp}
          loading={!threadsKnown}
          merged={merging ? [
            { hostId, label: host?.label ?? t('hosts.local'), state: (Boolean(host) && !hostUp ? 'offline' : threadsKnown ? 'live' : 'loading') as HostSource['state'], at: observedAt, threads: routerThreadId ? threads.filter((t) => t.id !== routerThreadId) : threads, agents: sidebarAgents },
            ...elsewhere,
          ].sort((a, b) => (a.hostId === null ? -1 : b.hostId === null ? 1 : hosts.findIndex((h) => h.id === a.hostId) - hosts.findIndex((h) => h.id === b.hostId))) : null}
          onAll={keepThreads && hosts.length > 0 ? () => setAllHosts(true) : undefined}
          onOpenElsewhere={onHost && ((id, thread) => onHost(id, { thread }))}
          external={externalSessions}
          externalState={externalState}
          onImportExternal={importExternal}
          importingExternal={importingExternal}
          externalError={externalError}
          moreExternal={externalCursor !== null}
          onLoadMoreExternal={loadMoreExternal}
          onHide={compact ? undefined : toggleSidebar}
          onThreadMenu={(thread, x, y) => openMachineMenu(thread.id, { x, y })}
          onSectionMenu={threadActions.openSectionMenu}
          onNewSection={threadActions.newSection}
          onOpenSteward={showcase ? undefined : openSteward}
          stewardActive={onSteward}
          needsYouGoals={loopxNeedsYou}
          onOpenSchedules={showcase ? undefined : openSchedules}
          onOpenDesktop={hasDesktop ? () => { setDrawer(false); setDesktop('open'); } : undefined}
          desktopRunning={desktop === 'minimized'}
          schedulesActive={onSchedules}
          extensionNav={extensionNav}
          notice={demo && compact ? showcase ? showcase.notice(selectedId, extWant) : <DemoBar compact onConnect={askToConnect} /> : undefined}
          footerNotice={!showcase ? <>
            <PwaInstallPromotion onHelp={() => openSettings('install')} />
            {authKind?.kind === 'link' && <LinkLoginNotice auth={authKind} onOpenAccess={() => openSettings('access')} />}
          </> : undefined}
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
      <main className={`main${(selected || threadPending) && !onPage && hostUp && !compact ? ' lean' : ''}${overviewRoom || (activeOverviewDetail && overviewDock && overviewOpen && !dockOpen) ? ' overview-room' : ''}`} ref={mainRef}>
        {compact && selected && !listOpen && !onPage && hostUp && (
          <ThreadQuickSwitch threads={topLevelThreads} selectedId={selected.id} agents={agents} onSelect={pick} />
        )}
        {!onSettings && <header className="topbar">
          {/* In a member's thread the way back is to the thread that sent it, not to the list. */}
          {compact && atMember ? (
            <button className="icon-btn menu-btn" aria-label={t('app.backToLead')} onClick={() => pick(selected!.coordination!.rootId)}>
              <IconBack size={22} />
            </button>
          ) : (
            <button className="icon-btn menu-btn" aria-label={t('app.openList')} aria-expanded={listOpen} onClick={() => setDrawer((v) => !v)}>
              {compact ? <IconBack size={22} /> : <IconMenu />}
            </button>
          )}
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
            {placeShown && !compact && <ThreadPlace thread={selected!} agent={agentLabel(selected!.agent)} host={host?.label} />}
            {threadPending && !viewTitle && !compact && <div className="pop" aria-hidden="true"><span className="icon-btn place-icon"><IconFolder size={16} /></span></div>}
            {compact && placeShown ? (
              <ThreadPlace thread={selected!} agent={agentLabel(selected!.agent)} host={host?.label} title={onRouterThread ? routerTitle : selected!.title} />
            ) : (
              <div className="topbar-title">{host && !hostUp ? host.label : viewTitle ?? (selected ? (onRouterThread ? routerTitle : selected.title) : threadPending ? '' : t('sidebar.newThread'))}</div>
            )}
            {selected?.coordination?.team && <div className={`team-task-status${['awaiting_confirm', 'needs_attention'].includes(selected.coordination.team.status) ? ' attention' : ''}`}><span className="team-task-name">{selected.coordination.team.name}</span><span> · {t(`teams.${selected.coordination.team.status}`)}</span></div>}
            {!selected && !threadPending && host && hostUp && <div className="topbar-sub">{host.label}</div>}
            {busyLine && busyLine.threadId === selected?.id && <div className="topbar-sub topbar-busy">{busyLine.message}</div>}
            {atMember && (
              <button className="topbar-sub topbar-lead-link" title={t('app.backToLead')} onClick={() => pick(selected!.coordination!.rootId)}>
                {agentLabel(selected!.agent)} · {t('app.leadThread', leadTitle)}
              </button>
            )}
          </div>
          {(selected || onSteward) && (
            <div className="topbar-actions">
              {/* The thread at a glance: one panel for the team, the plan, the changes, the pictures
                  and the files. The badge is only ever what needs the person. */}
              {(onSteward || api && hostUp) && (overviewDock ? (
                <button
                  type="button"
                  className={`icon-btn topbar-overview${overviewOpen ? ' on' : ''}${overviewNeeds ? ' attention' : ''}`}
                  aria-label={t('overview.label')}
                  aria-pressed={overviewOpen}
                  aria-busy={overviewBusy}
                  title={overviewNeeds ? t('overview.needsYou', overviewNeeds) : t('overview.label')}
                  onClick={() => setOverviewOpen(!overviewOpen)}
                >
                  <IconOverview />
                  {overviewNeeds > 0 && <span className="topbar-badge">{overviewNeeds}</span>}
                </button>
              ) : (
                <Popover
                  key={onSteward ? 'steward' : `${hostId ?? ''}:${selected!.id}`}
                  label={t('overview.label')}
                  prepare={onSteward ? undefined : () => prepareOverview(api!, selected!.id, selected!.updatedAt)}
                  openSignal={overviewSignal}
                  triggerBusy={overviewBusy}
                  triggerClassName={`icon-btn topbar-overview${overviewNeeds ? ' attention' : ''}`}
                  triggerLabel={t('overview.label')}
                  triggerTitle={overviewNeeds ? t('overview.needsYou', overviewNeeds) : t('overview.label')}
                  align="end"
                  placement="below"
                  panelClassName="overview-panel"
                  suspendDismissal={onSteward && Boolean(goalDirAsk)}
                  trigger={<><IconOverview />{overviewNeeds > 0 && <span className="topbar-badge">{overviewNeeds}</span>}</>}
                >
                  {(close) => overviewBody(close, false)}
                </Popover>
              ))}
              {/* A phone keeps its plan button: it has no margin card, and the lead's list of what
                  leaves the bar was the desktop's. The overview has the plan too, one tap away. */}
              {compact && topPlan && !onPage && <PlanButton item={topPlan} />}
              {compact && selected && (
                <button className="icon-btn topbar-new" aria-label={t('sidebar.newThread')} onClick={() => newThread()}>
                  <IconEdit />
                </button>
              )}
              {selected && <button
                className="icon-btn topbar-more"
                aria-label={t('app.threadMore')}
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  threadActions.openPageMenu(selected, rect.right, rect.bottom + 6);
                }}
              >
                <IconMore />
              </button>}
            </div>
          )}
        </header>}

        {/* Rows that wait for a decision, in the flow of the page rather than over it: a newer
            build to load, a just-archived thread to bring back. */}
        {newVersion && (
          <div className="notice-row" role="status">
            <span>{t('app.newVersion')}</span>
            <button className="notice-act" onClick={() => window.location.reload()}>{t('preview.refresh')}</button>
            <button className="notice-x" aria-label={t('app.dismiss')} onClick={() => setNewVersion(false)}>×</button>
          </div>
        )}
        {archivedUndo && (
          <div className="notice-row" role="status" key={archivedUndo.nonce}>
            <span>{t('actions.archivedNotice', archivedUndo.thread.title)}</span>
            <button
              className="notice-act"
              onClick={() => {
                threadActions.run('restore', archivedUndo.thread);
                setArchivedUndo(null);
              }}
            >
              {t('actions.undo')}
            </button>
            <button className="notice-x" aria-label={t('app.dismiss')} onClick={() => setArchivedUndo(null)}>×</button>
          </div>
        )}

        {demo && (showcase ? showcase.notice(selectedId, extWant) : <DemoBar compact={compact} onConnect={askToConnect} />)}

        {modelLogin?.scope === api && <AgentLoginRecovery key={`${modelLogin.key}:${hostId ?? 'local'}`}
          api={modelLogin.client} agent={{ id: modelLogin.agent, label: modelLogin.label, login: modelLogin.login }} empty={modelLogin.empty}
          onTerminalLogin={() => openAgentLogin({ id: modelLogin.agent }, modelLogin.client)} onRecovered={list => {
            modelVersions.current[modelLogin.key] = (modelVersions.current[modelLogin.key] ?? 0) + 1;
            setModelsByAgent(previous => ({ ...previous, [modelLogin.key]: list }));
            setModelLogin(current => current === modelLogin ? null : current);
            void reloadAgents().catch(handleError);
          }} />}

        {error && (
          <div className="banner" role="alert">
            <span>{error}</span>
            <button className="icon-btn" aria-label={t('app.dismiss')} onClick={() => setError(null)}>
              ×
            </button>
          </div>
        )}

        {onSettings ? (
          <SettingsPage groups={settingsGroups} path={settingsPath} onNavigate={openSettings} onClose={closeSettings} closeOnRoot={settingsOrigin.current?.entry === 'quota' && settingsPath === 'quota'}
            detailTitle={settingsPath.startsWith('assistant') ? assistantTitle(settingsPath) : undefined} />
        ) : host && !hostUp ? (
          // The machine's threads are over there; until it is back there is only this to say.
          <div key="host-down" className="scroller">
            <HostDown host={host} onRetry={() => api.reconnectHost(host.id).catch(() => undefined).then(reloadHosts)} />
          </div>
        ) : onExtensions ? (
          // A document-style page like the others; installing here changes the machine on screen.
          <div key="extensions" className="scroller">
            <ExtensionsPage
              api={api}
              demo={demo}
              extensions={extensions}
              threads={threads}
              onReload={() => void reloadExtensions()}
              onChange={changeExtension}
              onOpenView={openExtensionView}
              onError={handleError}
            />
          </div>
        ) : onExt ? (
          // An extension's view, behind a boundary of its own. Until the list is read, it is on its way.
          <div key={`ext:${extOpen ? `${extOpen.extensionId}/${extOpen.view.id}` : ''}:${boardEntry}`} className="extension-stage"><div className="scroller">
            {extOpen && extInputs ? (
              <ExtensionView api={api} extensionId={extOpen.extensionId} viewId={extOpen.view.id} name={extName} inputs={extInputs} onOpenThread={openBoardThread}
                onSetThreadDone={(id, done) => patchBoardThread(id, { done }).catch(error => { handleError(error); throw error; })}
                onSetThreadStage={(id, stage) => patchBoardThread(id, { stage })}
                navigate={{ openThread: openBoardThread, peekThread: id => {
                  if (window.matchMedia('(max-width: 640px)').matches) { openBoardThread(id); return; }
                  const machine = threadMachine(machineSources, id);
                  const thread = machineSources.flatMap(source => source.threads).find(thread => thread.id === id)!;
                  setPeek({ machine, api: threadApi(id), thread, returnFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null });
                }, threadMenu: (id, at) => openMachineMenu(id, at),
                  newThread: options => {
                    const machine = options?.machine === undefined ? hostId : options.machine;
                    if (machine !== hostId) onHost?.(machine, { draft: { project: options?.project } });
                    else newThread({ cwd: options?.project });
                  } }}
                onAskAssistant={async text => {
                  const assistantApi = hostId === null ? api : new Api(token);
                  const result = await assistantApi.petSay(text);
                  if (hostId !== null) onHost?.(null, { thread: result.threadId });
                  else { setRouterThreadId(result.threadId); openThread(await api.thread(result.threadId)); }
                }} onExtensionChange={changeExtension} />
            ) : (
              <div className="loading-note">{t('common.loading')}</div>
            )}
            </div>
            {peek && <ThreadPeek key={`${peek.machine ?? ''}:${peek.thread.id}`} api={peek.api} initial={peek.thread} returnFocus={peek.returnFocus}
              draftKey={peek.machine ? `host:${peek.machine}:${peek.thread.id}` : peek.thread.id}
              onClose={() => setPeek(null)} onOpen={() => openBoardThread(peek.thread.id)}
              onChange={updated => updateMachineThread(peek.machine, updated)} />}
          </div>
        ) : onMachines ? (
          <div key="machines" className="scroller">
            <MachineUsagePage
              key={`${hostId ?? 'local'}:${machineKey}`}
              api={api}
              token={token}
              hosts={hosts}
              open={machinePath}
              onOpen={openMachines}
            />
          </div>
        ) : onObservability ? (
          <div key="observability" className="scroller">
            <ObservabilityPage
              key={`${hostId ?? 'local'}:${observabilityKey}`}
              api={api}
              agents={agents}
              initial={observabilityTab}
              focusAgent={observabilityFocus}
              onResult={(result) => { if (result.state === 'ready' || result.state === 'empty') setModelsByAgent(previous => ({ ...previous, [result.agent]: result.models })); }}
            />
          </div>
        ) : onAgents ? (
          <div key="agents" className="scroller">
            <AgentsPage
              api={api}
              agents={agents}
              onAdd={() => { setOnboardStep('results'); setOnboardOpen(true); }}
              onReload={reloadAgents}
              onLogin={openAgentLogin}
            />
          </div>
        ) : onTeams ? (
          <div key="teams" className="scroller"><TeamsPage api={api} agents={agents} onReload={reloadTeams} /></div>
        ) : onSkills ? (
          // The skills page spans the machines from the hub, so it carries its own scroller too.
          <div key="skills" className="scroller">
            <SkillsPage api={api} token={token} hostId={hostId} hosts={hosts} agents={agents} onError={handleError} />
          </div>
        ) : onMcp ? (
          // The same: one view across every machine, with a scroller of its own.
          <div key="mcp" className="scroller">
            <McpPage api={api} token={token} hostId={hostId} hosts={hosts} agents={agents} onError={handleError} />
          </div>
        ) : onSchedules ? (
          // The same: a document-style page with a scroller of its own.
          <div key="schedules" className="scroller schedules-scroller">
            <SchedulesPage
              path={schedulePath}
              onNavigate={openSchedules}
              api={api}
              schedules={schedules}
              agents={agents}
              workdirs={workdirs}
              refreshKey={schedulesKey}
              onReload={reloadSchedules}
              onOpenThread={(id) => pick(id)}
              onError={handleError}
            />
          </div>
        ) : onSteward ? (
          <div key="steward" className="scroller">
            <StewardPage token={token} failure={loopxRead.failure} deferSessionOpen={Boolean(goalRequest || goalDetailFocus?.api === api && goalDetailFocus.threadId === null)} />
          </div>
        ) : onActivity ? (
          // The same document shape: days from the top, each conversation's asks under its name.
          <div key="activity" className="scroller">
            <ActivityPage api={api} agents={agents} onOpenThread={pick} onError={handleError} />
          </div>
        ) : (
        <div key="thread" className="scroller" ref={scrollerRef} data-note-target={composerDraftKey} data-note-thread={selectedId ?? undefined} onScroll={onScroll} {...gestureProps}>
          {selected && <TurnRuler key={selected.id} threadId={selected.id} scrollerRef={scrollerRef} previews={turnPreviewMap} turnIds={rulerTurnIds} onJump={turnId => setJump({ threadId: selected.id, turnId })} />}
          {selected && eventsLoading && <ConversationSkeleton />}
          {showcase && selected && !eventsLoading && !timeline.items.length && showcase.intro(selected.id)}
          {selected ? (
            <MergedHistory
              key={`${handoverHistoryKey(selected, threads)}:${mergedHandover}`}
              api={api} thread={selected} merged={mergedHandover} ready={historyReady} agentLabel={agentLabel}
              beforeItems={historyCutoff !== undefined ? <div className="row chain-followups"><button className="text-btn" onClick={() => setSingleSegmentId(selected.id)}>{t('overview.followups')}</button></div> : undefined}
              laterOf={latestSegment ? thread => laterSegment(thread, latestSegment, threads) : undefined}
              onBrowseOlder={() => { stickToBottom.current = false; setAtBottom(false); }}
              canFork={thread => ({ enabled: Boolean(thread.agentSessionId && capsOf(thread.agent)?.fork), any: Boolean(capsOf(thread.agent)?.forkAt) })}
              onSegmentFork={(thread, turn) => threadActions.forkAt(thread, turn)}
              onSegmentFile={async (thread, raw) => {
                const ref = fileRef(raw);
                let file;
                try {
                  file = await api.threadText(thread.id, ref.path);
                } catch (error) {
                  if (error instanceof ApiError && error.status === 404) {
                    try {
                      const listing = await api.threadTree(thread.id, ref.path);
                      setAgentsOpen(false); setFilesThreadId(thread.id);
                      setFiles({ path: null, folder: listing.path, seq: ++filesSeq.current });
                      return;
                    } catch { /* Not a folder either. */ }
                  }
                  throw error;
                }
                setAgentsOpen(false); setFilesThreadId(thread.id);
                setFiles({ path: file.path, line: ref.line, file, seq: ++filesSeq.current });
              }}
              onSegmentPreview={(thread, path) => setPreview({ threadId: thread.id, path })}
              historyApi={api ?? undefined}
              onQuote={(quote, turnId, threadId) => {
                const id = `note-${Date.now()}`;
                setNoteSignal({ draftKey: composerDraftKey, note: { id, quote, comment: '', nonce: Date.now(), ...(threadId && threadId !== selectedId ? { source: { threadId, turnId } } : {}) } });
              }}
              onMessageContext={!demo && !voice.state.active && !writing ? async (text, turnId, threadId) => {
                const draftKey = composerDraftKey;
                const source = await messageSnapshot(text, turnId, threadId);
                const value = await api.createReference({ kind: 'message', ...source });
                setMessageReference({ draftKey, value, nonce: Date.now() });
              } : undefined}
              onHandToAssistant={!demo ? async (text, turnId, threadId) => setAssistantMessage(await messageSnapshot(text, turnId, threadId)) : undefined}
              historyThreadId={selected.id}
              items={threadItems}
              revealTurnId={jump?.threadId === selected.id ? jump.turnId : undefined}
              roots={threadRoots}
              loadImage={loadImage}
              loadFile={loadFile}
              onOpenFile={openFileLink}
              onPreview={selected ? (path) => setPreview({ threadId: selected.id, path }) : undefined}
              // A delivery names its sender; the team roster says which thread is theirs, and the
              // body's footer can offer to go there — the person's own navigation, nothing sideways.
              memberThreadId={coordination ? (name) => coordination.members.find(m => m.name === name)?.id ?? null : undefined}
              onOpenMemberThread={(id) => {
                // A member on another machine has its thread there; the member panel reads it from that machine.
                if (coordination?.members.find(m => m.id === id)?.host) { setAgentsStart(id); setAgentsOpen(true); setFiles(null); return; }
                const known = threads.find(t => t.id === id);
                if (known) { openThread(known); return; }
                if (api) void api.thread(id).then(openThread).catch(handleError);
              }}
              // Read a referenced conversation beside this one, keeping the draft and reading position.
              threads={threads}
              onOpenThread={openReference}
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
                selected.agentSessionId && activeCaps?.editMessage && !mergedHandover
                  ? async (turnId, edit) => {
                      stickToBottom.current = true;
                      await api.editTurn(selected.id, turnId, edit);
                    }
                  : undefined
              }
              takeover={outOfPlan ? {
                label: takeoverLabel,
                busy: takingOver,
                go: takeoverChoice ? () => {
                  setTakingOver(true);
                  void threadActions.takeOver(selected, takeoverChoice).catch(() => undefined).finally(() => setTakingOver(false));
                } : undefined,
                choose: () => threadActions.handover(selected),
              } : null}
              resume={!api.demo && selected.autoHandover?.resume !== undefined ? {
                armed: Boolean(selected.autoHandover.resume),
                resetsAt: selected.autoHandover.resume?.resetsAt ?? null,
                error: selected.autoHandover.resume?.lastError,
                busy: resumeBusy,
                onArm: () => {
                  setResumeBusy(true);
                  void api.setResume(selected.id, true).catch(handleError).finally(() => setResumeBusy(false));
                },
                onCancel: () => {
                  setResumeBusy(true);
                  void api.setResume(selected.id, false).catch(handleError).finally(() => setResumeBusy(false));
                },
              } : null}
            />
          ) : (
            <div className="empty">
              <h2>{draftScratch ? t('app.emptyScratch') : t('app.emptyIn', folderName(draftCwd))}</h2>
            </div>
          )}
          {selected && <AutoHandoverNotice api={api} thread={selected} agents={agents} />}
        </div>
        )}

        <div className="jump-anchor">
          {selected && !onPage && !atBottom && (
            <button className="jump-btn" aria-label={t('app.toBottom')} onClick={jumpToBottom}>
              <IconArrowDown size={17} />
            </button>
          )}
        </div>

        {!onPage && !(host && !hostUp) && (
        <div className={`composer-slot${voice.state.active || voice.state.error ? ' voice-mode' : ''}`}>
        {selectedId && <WorkSummary items={workSources} />}
        {(voice.state.active || voice.state.error) && <VoiceOrb session={voice.state} provider={agentLabel(voice.state.providerId ?? selected?.agent ?? '')}
          levels={voice.levels} empty={timeline.items.length === 0} awaitingApproval={Boolean(waitingOn) || teamWaiting}
          tasks={[...(running && selected ? [{ id: selected.id, title: selected.title, since: selected.runningSince }] : []), ...(coordination?.members.filter(m => m.thread?.status === 'running').map(m => ({ id: m.id, title: m.name, since: m.thread?.runningSince })) ?? [])]}
          onTask={id => {
            const member = id ?? (!waitingOn ? coordination?.members.find(m => m.thread?.waiting)?.id : undefined);
            if (member && member !== selectedId) { setAgentsStart(member); setAgentsOpen(true); }
            else document.querySelector('.action-sheet')?.scrollIntoView({ block: 'nearest' });
          }} />}
        {showcase ? showcase.composer(selectedId) : <>
        {selected?.coordination?.team && selected.coordination.rootId === selected.id && ['awaiting_confirm', 'needs_attention'].includes(selected.coordination.team.status) && <div className="team-task-notice">{selected.coordination.team.status === 'needs_attention' && <span>{selected.coordination.team.error}</span>}{selected.coordination.team.status === 'awaiting_confirm' && <button className="ghost" onClick={() => void api.configureCoordination(selected.id, { action: 'confirm' }).then(value => { setCoordination(value); void refreshThreads(); }).catch(handleError)}>{t('teams.confirm')}</button>}</div>}
        {voice.state.error ? <div className="voice-reconnect-row"><button className="voice-reconnect" onClick={() => void voice.start()}>{t('voice.reconnect')}</button><VoiceControls session={voice.state} onMute={voice.toggleMute} onStop={voice.dismissError} /></div> : viewingEarlier && latestSegment ? <HandoverComposer
          key={latestSegment.id} api={api} thread={latestSegment} sourceId={selected!.id} label={agentLabel(latestSegment.agent)} hostId={hostId}
          attachBlocked={capsOf(latestSegment.agent)?.images ? null : t('app.noImages', agentLabel(latestSegment.agent))}
          onLatest={() => pick(latestSegment.id)} onRefresh={() => { setSingleSegmentId(null); void refreshThreads(); }} onError={handleError}
          note={noteSignal?.draftKey === composerDraftKey ? noteSignal.note : null} onNotesChange={onNotesChange}
        /> : <Composer
          draftKey={composerDraftKey}
          voiceControls={voice.state.active ? <VoiceControls session={voice.state} onMute={voice.toggleMute} onStop={() => void voice.stop()} /> : undefined}
          running={voice.state.active ? false : running}
          disabled={modeBusy || (!selected && (!draftCwd || modelPreferences.status !== 'ready'))}
          placeholder={voice.state.active ? t('app.placeholder.continue') : onRouterThread && !running ? t('pet.threadPlaceholder') : demo ? t('app.placeholder.demo') : workMode === 'plan' && !running ? t('composerMenu.planOn') : writing && !running ? t('writing.placeholder') : selected ? (running ? t('sideChat.queuePlaceholder') : t('app.placeholder.continue')) : draftScratch ? t('app.placeholder.scratch') : t('app.placeholder.startIn', folderName(draftCwd))}
          onSend={voice.state.active ? async (text, images, _mentions, videos = []) => {
            try { await voice.appendText(text, images.map(img => ({ data: img.data, mimeType: img.mimeType })), await Promise.all(videos.map(async video => (await api.uploadVideo(selectedId!, video.file)).id))); }
            catch (error) { handleError(error); throw error; }
          } : send}
          referenceSource={!demo && !voice.state.active ? { api, cwd: activeCwd, threadId: selected?.id } : undefined}
          reference={messageReference?.draftKey === composerDraftKey ? messageReference : null}
          onReferenceConsumed={id => setMessageReference(current => current?.value.id === id ? null : current)}
          mentionOptions={collaborationOptions}
          mentionIcon={collaborationIcon}
          collaboration={!draftTeam && !selected?.coordination?.team && (activeCaps?.coordination || selected?.coordination) ? collaborationControl : undefined}
          collaborationActive={Boolean(selected?.coordination || draftCollaboration)}
          mentionExtra={(m) => { const a = agents.find((v) => v.id === m.id) ?? remoteAgents.find((v) => v.id === m.id); return a ? memberModel(a) : null; }}
          onBrowseMentionDir={(m, choose) => {
            const a = remoteAgents.find((v) => v.id === m.id);
            // With no folder chosen yet, the picker opens where that machine was last left, the way
            // the new-thread picker opens in the draft's folder — mid-browse, not at the roots list.
            if (a && token) setRemotePick({ host: a.host, api: new Api(token, '', a.host), machine: hosts.find((h) => h.id === a.host)?.label ?? a.host, roots: a.directories, selected: m.cwd ?? recentRemoteDirs(a.host)[0] ?? '', manage: null, choose });
          }}
          mentionDirectories={coordination?.directories ?? (collaborationDirectories.length ? collaborationDirectories : [activeCwd])}
          onStop={stop}
          header={header}
          writingMode={draftTeam ? undefined : { active: !!writing, disabled: writingBusy, onChange: active => void changeWriting(active ? 'draft' : null) }}
          takeover={outOfPlan && !voice.state.active ? { from: agentLabel(selected!.agent), current: selected!.agent, go: (agent, note) => threadActions.takeOver(selected!, agent, note) } : undefined}
          banner={<>{selected && !api.demo && drivenByApp(selected.loopxInfo, selected.origin) && <p className="loopx-driven" role="status">{t('loopx.driven')}</p>}{writing && !draftTeam && <WritingBar writing={writing} busy={writingBusy} onStage={stage => void changeWriting(stage)} onEnd={() => void changeWriting(null)}
            onOpen={file => void openFileLink(file).catch(handleError)}
            habits={habitsPath ? { path: habitsPath, ready: habitsReady } : null}
            onCheckHabits={() => void checkWritingHabits()}
            onOpenHabits={path => void openFileLink(path).catch(handleError)} />}</>}
          planMode={!draftTeam && activeCaps?.workModes?.includes('plan') ? { active: workMode === 'plan', disabled: running || modeBusy, onChange: active => void changeWorkMode(active ? 'plan' : 'execute') } : undefined}
          left={draftTeam ? undefined : left}
          right={draftTeam ? undefined : right}
          composerActions={<>
            {!selected && !draftTeam && (api.demo || goalCap) && (
              // The demo has no hub to hand a Goal to: the mark stays, so the entry can be found, and says what it needs.
              <LoopxToggle active={draftLoopx} disabledReason={api.demo ? t('demo.needsHub') : goalCap?.available ? undefined : goalCap?.reason} onToggle={() => setDraftLoopx(on => !on)} />
            )}
            {(canVoice || canVoiceDraft) && !voice.state.active && (
            <button
              type="button"
              className={`tool-btn composer-voice${voice.state.active ? ' active' : ''}`}
              disabled={modeBusy}
              onClick={voice.state.active ? () => void voice.stop() : canVoiceDraft ? () => void startDraftVoice() : () => void voice.start()}
              title={voice.state.active ? t('voice.end') : t('voice.start')}
              aria-label={voice.state.active ? t('voice.end') : t('voice.start')}
            >
              <IconWaveform size={16} />
            </button>
          )}
          </>}
          commands={commandsByKey[commandsKey] ?? []}
          goal={activeCaps?.goal}
          goalState={goalView}
          actions={hubActions}
          hiddenCommands={hiddenCommands}
          onAction={runHubAction}
          uploadsBlocked={!selected && draftTeam ? t('teams.attachments') : undefined}
          attachBlocked={attachBlocked}
          attachNote={imagesNote(models, model)}
          onError={setError}
          queued={selected?.queued ?? []}
          modelQueue={selected?.modelQueue ?? null}
          onRemoveQueued={removeQueued}
          onEditQueued={editQueued}
          onSendQueuedNow={sendQueuedNow}
          restore={restore}
          note={noteSignal?.draftKey === composerDraftKey ? noteSignal.note : null}
          onNoteConsumed={consumeNote}
          onNotesChange={onNotesChange}
          quoteSource={(threadId, turnId, sourceHost) => sourceHost === (hostId ?? null) && threadId !== selectedId && threads.some(thread => thread.id === threadId)
            ? { threadId, turnId, elsewhere: true } : undefined}
          lastSent={selected ? lastSent : null}
        />}
        </>}
        {/* Guided demos replace the composer, but use the same approval and question sheets. */}
        {!viewingEarlier && waitingOn && selected && (!showcase || waitingOn.target.kind !== 'hub-plan') && (
          <ActionSheet
            key={waitingOn.target.item.key}
            target={waitingOn.target}
            index={1}
            total={waiting.length}
            roots={threadRoots}
            onAnswer={(id, answer) => api.answerInput(id, answer)}
            onDecide={decide}
            // The daemon has put the thread back in execute mode when the plan arrived; the call
            // below only switches it when that did not happen, then sends the go-ahead.
            onExecutePlan={running ? undefined : () => changeWorkMode('execute', true)}
            onRejectPlan={(text) => void api.startTurn(selected.id, text, [], { workMode: 'plan' })}
            onSkipPlan={() => {
              if (waitingOn.target.kind !== 'hub-plan' || !selectedId) return;
              try {
                localStorage.setItem(`${PLAN_SKIP_KEY}:${selectedId}`, waitingOn.target.item.key);
              } catch {
                // Not remembered: the sheet stays away until the page reloads all the same.
              }
              setPlanSkip(waitingOn.target.item.key);
            }}
          />
        )}
        </div>
        )}
        {activeOverviewDetail && compact && overviewBody(closeOverviewDetail, true, activeOverviewDetail.section)}
      </main>

      {/* The overview standing beside the conversation: a layer of its own, put away only by the
          button directly above it. It sits below the top bar on the window's right edge. */}
      {overviewShown && (
        <div className="pop-panel overview-panel overview-dock" role="menu" aria-label={t('overview.label')}>
          {overviewBody(closeOverview, true)}
        </div>
      )}

      {referenceId && <ThreadPeek key={`${hostId ?? 'local'}:${referenceId}`} side api={api} initial={referenceId}
        draftKey={hostId ? `host:${hostId}:${referenceId}` : referenceId} returnFocus={null}
        noteSignal={noteSignal} onNoteConsumed={consumeNote} onNotesChange={onNotesChangeFor}
        onClose={() => setReferenceId(null)} onOpen={() => { setReferenceId(null); openThreadById(referenceId); }}
        onChange={() => { void refreshThreads(); }} />}
      {agentsOpen && coordination && <AgentPanel key={`${coordination.rootId}:${agentsStart ?? ''}`} start={agentsStart} api={api} memberApi={(id) => new Api(token ?? '', '', id)} team={coordination} agents={agents} memberModel={(thread, part) => {
        const a = agents.find((v) => v.id === thread.agent);
        return a ? <MemberModel part={part} agent={a} models={modelsByAgent[a.id] ?? []} value={{ model: thread.model ?? null, effort: thread.effort ?? null }} onNeed={needModels} onChange={({ model, effort }) => { void api.updateThread(thread.id, { model, effort }).then(() => refreshThreads()).catch(handleError); }}
          permission={a.capabilities.permissionModes.length ? thread.permissionMode : undefined} onPermission={(permissionMode) => { void api.updateThread(thread.id, { permissionMode }).then(() => refreshThreads()).catch(handleError); }} /> : null;
      }} leadAgent={threads.find((t) => t.id === coordination.rootId)?.agent} phone={compact} noteSignal={noteSignal} onNoteConsumed={consumeNote} onNotesChange={onNotesChangeFor} onClose={() => setAgentsOpen(false)} onOpen={thread => { openThread(thread); setAgentsOpen(false); }} onAdjust={(name, draft) => { setAgentsOpen(false); setSelectedId(coordination.rootId); setRestore({ text: t('app.adjustTask', name) + draft, nonce: Date.now() }); }} onRefresh={() => { void refreshThreads(); }} onError={handleError} />}
      {activeOverviewDetail && !compact && <DockPanel hidden={Boolean(preview || picture || termOpen || (diffFile && diffFile.threadId === selected?.id))} label={t(activeOverviewDetail.section === 'all' ? 'overview.label' : `overview.${activeOverviewDetail.section}`)} onDismiss={closeOverviewDetail}>{overviewBody(closeOverviewDetail, true, activeOverviewDetail.section)}</DockPanel>}
      {picture && api && !compact && picture.threadId === selected?.id && <PictureDock key={`${hostId ?? ''}:${picture.threadId}:${picture.path}`} path={picture.path} name={picture.name} load={loadFile}
        onClose={() => { setPicture(null); setOverviewDetail(null); }} onBack={activeOverviewDetail ? () => setPicture(null) : undefined} />}
      {preview && api && <PreviewPanel key={`${hostId ?? ''}:${preview.threadId}:${preview.path}`} api={api} threadId={preview.threadId} path={preview.path}
        onClose={() => { setPreview(null); if (!compact) setOverviewDetail(null); }} onBack={!compact && activeOverviewDetail ? () => setPreview(null) : undefined} docked={!compact} />}
      {api && diffFile && diffFile.threadId === selected?.id && (
        <ChangeDiffDialog
          api={api}
          threadId={diffFile.threadId}
          file={diffFile.file}
          turnOrder={turnOrder}
          docked={!compact}
          onBack={!compact && activeOverviewDetail ? () => setDiffFile(null) : undefined}
          onClose={() => { setDiffFile(null); if (!compact) setOverviewDetail(null); }}
          onJumpTurn={(turnId) => { setDiffFile(null); setJump({ threadId: diffFile.threadId, turnId }); }}
        />
      )}
      {files && filesThread && api && <FilesPanel api={api} threadId={filesThread.id} target={files} phone={compact} revision={filesThread.updatedAt} writingStage={filesThread.writing?.stage ?? null} writingHabits={filesThread.writing?.habits ?? null} noted={{ count: noteCount, nonce: filesNotePulse }} onClose={() => setFiles(null)} />}
      {gitOpen && selected && api && <GitPanel key={`${hostId ?? ''}:${selected.id}`} api={api} threadId={selected.id} phone={compact} running={selected.status === 'running'} onClose={() => setGitOpen(false)} />}
      {traceOpen && selected && (
        <ThreadTracePanel
          api={api}
          agents={agents}
          threadId={selected.id}
          phone={compact}
          readings={compact ? <StatReadings api={api} threadId={selected.id} active={selected.status === 'running'} /> : undefined}
          onClose={() => setTraceOpen(false)}
        />
      )}
      {termOpen && (
        <TerminalPanel
          api={terminalClient ?? api}
          cwd={selected?.cwd || draftCwd || workdirs[0] || ''}
          terminalId={terminalTarget}
          docked={!compact}
          onClose={() => { setTermOpen(false); setTerminalTarget(null); setTerminalClient(null); }}
          onError={handleError}
        />
      )}
      </div>
      {desktop !== 'closed' && (
        <DesktopPanel api={api} hidden={desktop === 'minimized'} onMinimize={() => setDesktop('minimized')} onClose={() => setDesktop('closed')} />
      )}
      {statusOpen && (
        <StatusPanel
          thread={selected}
          agentLabel={agentLabel(activeAgent)}
          model={modelName(models, model)}
          permission={permissionModes.length ? permission : ''}
          directory={activeCwd}
          usage={selected ? timeline.usage : null}
          usageCommand={['usage', 'cost', 'session'].find((name) => (commandsByKey[commandsKey] ?? []).some((c) => c.name === name)) ?? null}
          onOpenQuota={agents.some((a) => !a.hidden && a.capabilities.quota) ? () => openSettings('quota') : undefined}
          onRunCommand={(text) => void send(text, []).catch(() => undefined)}
          onClose={() => setStatusOpen(false)}
        />
      )}
      {onboardOpen && (
        <OnboardingDialog
          api={api}
          agents={agents}
          initialStep={onboardStep}
          onConnected={handleOnboardConnected}
          onClose={() => setOnboardOpen(false)}
          onLater={handleOnboardLater}
        />
      )}
      {addAgentOpen && (
        <AddAgentDialog
          api={api}
          agents={agents}
          onConnected={handleOnboardConnected}
          found={discovered}
          onAdd={addDiscoveredAgent}
          onClose={() => {
            setAddAgentOpen(false);
          }}
        />
      )}
      {pendingGoal && (
        <GoalConfirm token={token} agent={draftAgent} agentLabel={agentLabel} cwd={draftCwd} permission={draftGoalLevel} text={pendingGoal.text}
          model={draftModel} effort={draftEffort}
          onCancel={() => { pendingGoal.reject(new SendCancelled()); setPendingGoal(null); }}
          onCreated={(threadId) => { pendingGoal.onCreated?.(threadId); pendingGoal.resolve(); setPendingGoal(null); setDraftLoopx(false); setSelectedId(threadId); setView('thread'); void refreshThreads(); }} />
      )}
      {pendingSend && (
        <Dialog
          title={t('app.terminalBusy')}
          message={t('app.terminalBusyMessage')}
          confirmLabel={t('app.sendAnyway')}
          onCancel={() => {
            pendingSend.reject(new Error('not sent'));
            setPendingSend(null);
          }}
          onConfirm={() => {
            const p = pendingSend;
            setPendingSend(null);
            performSend(p.text, p.images, false, p.mentions, p.videos).then(p.resolve, p.reject);
          }}
        />
      )}
      {paletteOpen && (() => {
        const entries = settingsGroups.flatMap(group => group.entries);
        const items: PaletteItem[] = [
          { id: 'cmd:new', group: 'commands', label: t('palette.newSession'), keywords: 'new thread' },
          ...(showcase ? [] : [{ id: 'cmd:steward', group: 'commands' as const, label: t('palette.steward'), keywords: 'steward loopx' }]),
          ...topLevelThreads.map(thread => ({ id: `thread:${thread.id}`, group: 'sessions' as const, label: thread.title, hint: thread.cwd, keywords: thread.agent })),
          ...sortGoalTree(loopxStatus?.goals ?? []).map(goal => ({ id: `goal:${goal.goalId}`, group: 'goals' as const, label: goal.title, hint: goal.status, keywords: goal.objective ?? '', needsYou: loopxNeedsYou.has(goal.goalId) })),
          ...entries.map(entry => ({ id: `settings:${entry.id}`, group: 'settings' as const, label: entry.label })),
        ];
        const pickItem = (item: PaletteItem) => {
          setPaletteOpen(false);
          const [kind, ...rest] = item.id.split(':'); const key = rest.join(':');
          if (kind === 'thread') pick(key);
          else if (kind === 'goal') openGoal(key);
          else if (kind === 'settings') { const entry = entries.find(e => e.id === key); if (entry?.onOpen) entry.onOpen(); else openSettings(key); }
          else if (key === 'steward') openSteward();
          else newThread();
        };
        return <CommandPalette items={items} onPick={pickItem} onClose={() => setPaletteOpen(false)} />;
      })()}
      {connectAsk && !showcase && <ConnectDialog reason={connectAsk.reason} onCancel={() => setConnectAsk(null)} onConnected={(t) => onToken(t)} />}
      {confirmSignOut && (
        <Dialog
          title={t('app.signOutTitle')}
          message={t('app.signOutMessage')}
          confirmLabel={t('app.signOut')}
          onCancel={() => setConfirmSignOut(false)}
          onConfirm={() => {
            setConfirmSignOut(false);
            setDrawer(false);
            signOut();
          }}
        />
      )}
      {sideAsk && (
        <SideChat
          api={api}
          agent={sideAgent}
          agentInfo={sideAgentInfo}
          models={modelsByAgent[sideAgent] ?? []}
          savedPreference={modelPreferences.preferences[sideAgent]}
          onModelsLoaded={(list) => acceptModelCatalog(sideAgent, list)}
          onPreferenceChanged={() => void modelPreferences.refresh()}
          agentLabel={agentLabel(sideAgent)}
          cwd={sideAsk.source?.cwd ?? activeCwd}
          permissionMode={sideAsk.source?.permissionMode ?? permission}
          model={sideAsk.source ? sideAsk.source.model : model}
          effort={sideAsk.source ? sideAsk.source.effort : effort}
          quoteText={sideAsk.text}
          sourceTitle={(sideAsk.source ?? selected)?.title ?? null}
          forkSourceId={sideAsk.source ? (capsOf(sideAsk.source.agent)?.fork && !forkBlocked(sideAsk.source) ? sideAsk.source.id : null) : selected && activeCaps?.fork && !forkBlocked(selected) ? selected.id : null}
          attachBlocked={sideAgentInfo?.capabilities.images === false ? t('app.noImages', agentLabel(sideAgent)) : null}
          onClose={() => setSideAsk(null)}
          onError={error => handleModelError(error, sideAgent)}
          onThreadCreated={(t) => setThreads((prev) => [t, ...prev.filter((x) => x.id !== t.id)])}
          threads={threads}
          onOpenThread={(id) => { setSideAsk(null); openThreadById(id); }}
        />
      )}

      {threadActions.layer}
      {remoteMenu && <RemoteThreadMenu key={remoteMenu.machine ?? 'local'} api={remoteMenu.api} target={remoteMenu} board={Boolean(boardExt?.enabled)}
        onChange={updated => updateMachineThread(remoteMenu.machine, updated)}
        onOpen={thread => onHost?.(remoteMenu.machine, { thread: thread.id })}
        onRemove={id => setElsewhere(sources => sources.map(source => source.hostId === remoteMenu.machine ? { ...source, threads: source.threads.filter(t => t.id !== id) } : source))}
        onError={handleError} />}
      <NoteMarks marks={noteMarks} scrollerRef={scrollerRef} />
      {assistantMessage && <HandToAssistant key={`${hostId}:${assistantMessage.sourceId}:${assistantMessage.turnId}`} api={messageAssistantApi} source={assistantMessage}
        draftKey={`${hostId}:${assistantMessage.sourceId}:${assistantMessage.turnId}`} onClose={() => setAssistantMessage(null)} onSent={threadId => {
          setAssistantMessage(null);
          if (hostId !== null) onHost?.(null, { thread: threadId });
          else { setRouterThreadId(threadId); void api.thread(threadId).then(openThread).catch(handleError); }
        }} />}
      <SelectionMenu
        onNote={(quote, comment, range, file) => {
          const id = `note-${Date.now()}`;
          if (range) noteRanges.current.set(id, range);
          const container = range?.commonAncestorContainer instanceof Element ? range.commonAncestorContainer : range?.commonAncestorContainer?.parentElement;
          const targetEl = container?.closest<HTMLElement>('[data-note-target]');
          const targetDraftKey = targetEl?.dataset.noteTarget || composerDraftKey;
          const targetThreadId = targetEl?.dataset.noteThread || (viewingEarlier ? latestSegment?.id : selectedId);
          // Messages go to the panel the passage was selected in; a passage from another thread says where it is.
          const source = noteSource(range, targetThreadId);
          setNoteSignal({ draftKey: targetDraftKey, note: { id, quote, comment, nonce: Date.now(), ...(file ? { file } : {}), ...(source ? { source } : {}) } });
          if (file) setFilesNotePulse((v) => v + 1);
        }}
        onSideAsk={(text, threadId) => {
          if (threadId && threadId !== selectedId) void api.thread(threadId).then(source => setSideAsk({ text, nonce: Date.now(), source })).catch(handleError);
          else setSideAsk({ text, nonce: Date.now() });
        }}
      />
    </div></QuotaNavigation.Provider>
  );
}
