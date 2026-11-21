import { applyModelPreference, readModelPreferences, type DraftModelPreference, type DraftModelPreferences, type ModelPreferencePatch } from './model-preferences.js';
import { PreviewNotes } from './preview-notes.js';
import { ContextReferences } from './context-references.js';
import { GitManager, gitWorktreeRoot } from './git.js';
import { PersonalBriefSchedule } from './personal-brief-schedule.js';
import { changedFiles, mentionedFiles, checkMissingFiles, MAX_OUTPUT_ITEMS, type ThreadOutputs } from './outputs.js';
import { LarkChannel } from './channels/lark.js';
import { PersonalService } from './personal.js';
import { AutoHandover, type AutoHandoverView, type HandoverTarget, type SettledOutcome } from './auto-handover.js';
import { AutoCompact } from './auto-compact.js';
import type { HandoverLink } from './handover-mode.js';
import { QuotaResume, type ResumeView } from './quota-resume.js';
import type { VoiceTransport } from './voice-sessions.js';
import { VoiceConversation } from './voice-conversation.js';
import { turnOutcome } from './lifecycle.js';
import { UserInputs } from './user-input.js';
import type { LoginState, SystemPrompt, WorkMode } from './adapters/types.js';
import { diagnosticFailure, modelLoginFailure } from './adapters/diagnostics.js';
import { turnAnswer, type CoordinationManager, type CoordinationSetup } from './coordination.js';
import { Injections, type InjectionGate } from './injections.js';
import type { LoopxHooks } from './adapters/loopx/service.js';
import { parseProgress, progressInstructions, PROGRESS_STAGES, type ProgressStage } from './progress-report.js';
import { habitsPath, isEditableArticle, isWritingStage, newestArticle, noteFile, saveArticle, writingInstructions, type WritingState } from './writing.js';
import { randomUUID } from 'node:crypto';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, realpathSync, rmSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, resolve, sep } from 'node:path';
import {
  EFFORT_LEVELS,
  PERMISSION_MODES,
  type AgentAdapter,
  type AgentSession,
  type AgentSessionSummary,
  type ApprovalDecision,
  type ApprovalRequest,
  type EffortLevel,
  type ModelOption,
  type PermissionMode,
  type PlanQuota,
  type SlashCommand,
  type TurnHooks,
  type TurnInput,
} from './adapters/types.js';
import type { GoalSnapshot } from './adapters/native-goal.js';
import { findFiles, listDir, readText, type Listing, type TextFile } from './browse.js';
import { referencedFile } from './artifacts.js';
import { imagePathNote, isVideoType, MAX_IMAGES, sniffImageType, sniffSvg, type AttachmentStore, type StoredAttachment, type StoredVideo } from './attachments.js';
import { HttpError } from './errors.js';
import { handoverBrief, handoverPrompt, synthesizeBrief, turnMessage } from './handover.js';
import { McpManager } from './mcp.js';
import { SkillsManager } from './skills.js';
import { isWithinWorkdirs, Workdirs } from './workdirs.js';
import { coalesceToolEvents, compactDeltas, toolText, type EmittedEvent, type EventType, type StoredEvent, type TurnChannel } from './events.js';
import type { SearchHit, SectionRecord, Store, ThreadRecord, ThreadWorktree, TodoProposal, TodoRecord, TodoUrgency } from './store.js';
import { readBrief, type ThreadBrief } from './brief.js';
import { urgencyOf } from './store.js';
import { todoStages } from './todo-stage.js';
import { batchShape, mergeBatches, type TurnBatch } from './turn-batch.js';
import { buildTrace, type TurnTrace } from './trace.js';
import { aggregateThreadStats, statsOfTrace, type ThreadStats, type TurnStatsRow } from './turn-stats.js';
import { askedText } from './util.js';
import { MessageEdits } from './message-edits.js';
import { addWorktree, countChanges, findCheckout, initRepository, removeWorktree, samePath, worktreeLeftovers, type Checkout, type Worktree } from './worktrees.js';
import { isProbeSession, offeredSessionDir } from './local-sessions.js';

/** Why a copy cannot get a worktree of its own, said to whoever asked for one anyway. */
const WORKTREE_REFUSALS = {
  agent: 'this agent cannot continue a copy in another directory',
  not_git: 'the directory is not in a git repository with a commit to start a worktree from',
  outside_workdirs: 'the repository is outside the allowed workdirs, so a worktree in it would be too',
} as const;

/** General preferences are shared by every browser connected to this daemon. */
export type AutoArchiveDays = 1 | 3 | 7 | 15 | 30 | null;

const AUTO_ARCHIVE_DAY_OPTIONS = [1, 3, 7, 15, 30] as const;
const DAY_MS = 24 * 60 * 60 * 1000;

export interface GeneralSettings {
  /** The page to open when no thread or page was explicitly requested. */
  startupPage: 'new-thread' | 'recent-thread';
  /** Conversations older than this many days are hidden from the sidebar. Null leaves them alone. */
  autoArchiveDays: AutoArchiveDays;
  /** List every conversation the agents on this machine already have. Off: only LoopX Goal conversations. */
  autoImportLocal: boolean;
}

function storedAutoArchiveDays(value: unknown): AutoArchiveDays {
  return AUTO_ARCHIVE_DAY_OPTIONS.includes(value as never) ? value as AutoArchiveDays : null;
}

function parseAutoArchiveDays(value: unknown): AutoArchiveDays {
  if (value === null) return null;
  if (AUTO_ARCHIVE_DAY_OPTIONS.includes(value as never)) return value as AutoArchiveDays;
  throw new HttpError(400, 'invalid auto archive period');
}

const DRAFT_MODEL_PREFERENCES_KEY = 'draft.model.preferences';

/** What forking a thread can offer besides a copy in the same directory. */
export interface ForkOptions {
  worktree: {
    available: boolean;
    /** Why not, when it is not: the agent cannot move a copy, the directory is not in a git repository, or the repository lies outside the allowed directories. */
    reason?: 'agent' | 'not_git' | 'outside_workdirs';
    /** The branch the thread's directory is on. Null when HEAD is detached. */
    branch?: string | null;
    /** How many files there differ from the last commit. They come along. */
    changes?: number;
  };
}

export interface ModelQueueView {
  state: string;
  position: number | null;
  operation?: string | null;
  message?: string | null;
  updatedAt: string;
}

type ModelQueueState = ModelQueueView & { turnId: string };

/** A thread as the API returns it: the stored record plus values worked out from it. */
export type ThreadView = Omit<ThreadRecord, 'worktree' | 'handover' | 'writing'> & {
  /** Writing mode, with where the person's writing habits are kept. */
  writing: (WritingState & { habits: string }) | null;
  handover?: (NonNullable<ThreadRecord['handover']> & { automatic: boolean; /** The source started a turn after the handover. */ sourceMoved: boolean }) | null;
  autoHandover?: AutoHandoverView;
  /** `doing`, on a member: its latest task while it works, the opening of its answer once it has returned. */
  coordination?: { retired?: boolean; rootId: string; children: number; paused: boolean; doing?: string; team?: { name: string; status: import('./teams.js').TaskStatus; role: string; error?: string } };
  /** Set when this is a LoopX Goal conversation: which Goal it belongs to and the permission it runs under. */
  loopx?: import('./adapters/loopx/binding.js').LoopxBinding | null;
  /** For lists: which Goal this conversation is on and how that was found out (its binding, the core's records, or its own text). */
  loopxInfo?: import('./session-enrich.js').SessionLoopx | null;
  /** Which front end started the agent session (its app or its terminal client), when its file says. */
  origin?: 'app' | 'cli' | null;
  /** Working in the hub's scratch area: a thread that is in no project. */
  scratch: boolean;
  /** The project directory no longer exists, e.g. a temporary checkout removed after a run. */
  projectGone: boolean;
  /** `of` is how the directory was spelled when the worktree was made; `ofReal` has symlinks resolved, like `cwdReal`. */
  worktree: (ThreadWorktree & { ofReal: string }) | null;
  /** Shell command that continues this conversation in a terminal. Null until the agent session exists. */
  resumeCommand: string | null;
  /**
   * `cwd` with symlinks resolved. Agents report file paths in this form, so the UI needs it to
   * recognise which paths are inside the thread's directory.
   */
  cwdReal: string;
  /** The agent is waiting for the user to respond. */
  waiting: boolean;
  waitingReason: 'approval' | 'question' | 'plan' | null;
  /** Messages sent while a turn was running. They go out, in order, as turns finish. */
  queued: Array<{ id: string; text: string; images: number; videos: number; files?: number; targets?: string[]; error?: string; channel?: TurnChannel }>;
  /** The upstream model-provider queue reported by the native runtime, if this turn is waiting there. */
  modelQueue?: ModelQueueView | null;
  /** Turns the terminal added to this session since the hub last wrote to it. */
  terminalActivity: boolean;
  /** When the running turn started; null while nothing runs. */
  runningSince: string | null;
  /** Why the thread stopped, while its status is `error`: the latest turn's failure. */
  failure?: { code: string | null; message: string; at: string } | null;
};

interface QueuedTurn {
  assistantSay?: string;
  displayText?: string;
  channel?: TurnChannel;
  /** Later sends carrying a batch fold into this one instead of queueing behind it. */
  coalesce?: TurnBatch;
  voiceSummary?: string;
  collaboration?: CoordinationSetup;
  error?: string;
  workMode?: WorkMode;
  mentions?: unknown[];
  /** Typed by the person into this thread, rather than sent in by the hub. */
  fromUser?: boolean;
  id: string;
  text: string;
  images: unknown[];
  videos: string[];
}

/** A thread's native goal as the API serves it to the goal card. */
export interface GoalView {
  objective: string;
  /** Active, paused, blocked… in the agent's own vocabulary. */
  status: string;
  tokensUsed?: number;
  tokenBudget?: number;
  /** When this goal was set. ISO. */
  startedAt: string;
  /** When this view was computed. ISO; while active the clock runs on from here in the UI. */
  at: string;
  /** Active work so far, ms. While active: elapsed = activeMs + (now − at). */
  activeMs: number;
}

/** How many messages may wait behind a running turn. */
export const MAX_QUEUED = 5;
/** The largest image file the hub will serve from a thread's directory. */
const MAX_SERVED_IMAGE_BYTES = 20 * 1024 * 1024;
/** An SVG is text, and the page holds it whole to show it; a drawing is far smaller than this. */
const MAX_SERVED_SVG_BYTES = 5 * 1024 * 1024;
/** How much of a file is read to tell what it is. An SVG may open with a declaration and a comment. */
const SNIFF_BYTES = 4096;
/** How long a plan-usage answer is reused. The probes spawn a CLI; a minute is fresh enough. */
const QUOTA_TTL_MS = 60_000;
/** A reading the agent gave while answering stands in for a probe this long, even on a refresh. */
const OBSERVED_FRESH_MS = 3 * 60_000;

/**
 * The content type a downloaded file is served as. Types a browser can play or show inline are
 * named so a video streams and a PDF opens; everything else is octet-stream, which saves rather
 * than guesses. The browser never sniffs these (X-Content-Type-Options: nosniff).
 */
const DOWNLOAD_TYPES: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.m4v': 'video/mp4',
  '.mov': 'video/quicktime',
  '.webm': 'video/webm',
  '.mp3': 'audio/mpeg',
  '.m4a': 'audio/mp4',
  '.wav': 'audio/wav',
  '.ogg': 'audio/ogg',
  '.pdf': 'application/pdf',
};
function downloadType(path: string): string {
  const dot = path.lastIndexOf('.');
  return (dot < 0 ? '' : DOWNLOAD_TYPES[path.slice(dot).toLowerCase()]) ?? 'application/octet-stream';
}

/** Quotes a path for a POSIX shell, leaving plain paths readable. */
function shellQuote(value: string): string {
  return /^[A-Za-z0-9_./-]+$/.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`;
}

/** A turn's identity for matching hub turns against the agent's history. */
function turnKey(text: string): string {
  return text.trim().slice(0, 200);
}

/**
 * Pairs the agent's turns with the hub's, walking both lists from the start. A hub turn the agent
 * never recorded (cancelled before it got there) is skipped, up to `tolerance` in a row. Returns,
 * for each hub turn, which agent turn it is (-1 for none), and the first agent turn the hub does
 * not have. Null when the two histories disagree beyond the tolerance.
 */
export function matchTurns(agentKeys: string[], hubKeys: string[], tolerance: number): { hubToAgent: number[]; agentNext: number } | null {
  const hubToAgent = hubKeys.map(() => -1);
  let ai = 0;
  let hi = 0;
  while (ai < agentKeys.length && hi < hubKeys.length) {
    let j = hi;
    while (j < hubKeys.length && j - hi < tolerance && agentKeys[ai] !== hubKeys[j]) j++;
    if (j >= hubKeys.length || agentKeys[ai] !== hubKeys[j]) return null;
    hubToAgent[j] = ai;
    ai++;
    hi = j + 1;
  }
  return { hubToAgent, agentNext: ai };
}

export interface AgentInfo {
  id: string;
  label: string;
  hidden?: boolean;
  /** The agent's mark, when its adapter has one. Instances of one adapter share it. */
  icon?: string;
  /** Which adapter this instance is: instances of one adapter group together in the picker. */
  adapter?: string;
  /** The account this instance runs as, when it is a linked second login (an email). */
  account?: string | null;
  available: boolean;
  streamBridgeEnabled?: boolean;
  /** How it can be signed in from the hub, when its adapter can start the CLI's login. */
  login?: 'terminal' | 'wizard';
  /** In-progress login owned by the daemon, recoverable after page navigation. */
  activeLogin?: { id: string } & LoginState;
  capabilities: AgentAdapter['capabilities'];
}

export { HttpError };

interface PendingApproval {
  threadId: string;
  turnId: string;
  /** The option ids the agent offered. Anything else is refused instead of being passed on. */
  optionIds: string[];
  resolve(decision: ApprovalDecision): void;
}

type Listener = (event: StoredEvent) => void;

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

/**
 * Owns thread lifecycle: starts agent sessions, runs turns, persists every event,
 * fans events out to subscribers, and routes approval decisions back to agents.
 */
/** The most context, in tokens, a handover still carries over by copying the conversation. */
const RESUME_HANDOVER_MAX_CONTEXT = 50_000;

export class ThreadManager {
  coordination?: CoordinationManager;
  borrowing?: import('./borrowing.js').BorrowingService;

  /** Drop an idle process so runtime-only connection context takes effect on resume. */
  async refreshRuntime(id: string): Promise<void> {
    if (this.running.has(id)) throw new HttpError(409, '请等待当前轮结束后启用协作');
    await this.dropSession(id);
  }

  notifyCoordination(id: string): void {
    if (this.store.getThread(id)) this.emitThreadEvent(id, null, 'thread.updated', {});
  }
  private readonly personalScopes = new Map<string, boolean>();
  private readonly sessionAdapters = new Map<string, AgentAdapter>();
  private readonly sessionStarts = new Map<string, Promise<AgentSession>>();
  private readonly nativeIdle = new Map<string, Set<() => void>>();
  /** Each thread's native goal, as the goal card shows it. */
  private readonly goals = new Map<string, GoalView>();
  private readonly nativeTurns = new Map<string, Map<string, string>>();
  private readonly voiceThreads = new Set<string>();
  private readonly sessions = new Map<string, AgentSession>();
  /** Threads whose model/effort/speed was changed mid-turn: the setting is stored, the session that
   *  turn still runs in is stale. It is dropped when the turn ends, never interrupted mid-turn. */
  private readonly refreshAfterTurn = new Set<string>();
  readonly git = new GitManager(
    id => this.getThread(id).cwd,
    (id, path) => { this.resolveInThread(id, path, new HttpError(403, 'Git repository is outside the readable roots')); },
    root => [...this.running.keys(), ...this.startingTurns.keys()].some(id => gitWorktreeRoot(this.getThread(id).cwd) === root),
  );

  /** Turns in flight. `cancelRequested` covers cancels that arrive before the agent session exists. */
  private readonly running = new Map<string, { turnId: string; cancelRequested: boolean; since: string }>();
  readonly inputs = new UserInputs((thread, turn, type, data) => this.emitThreadEvent(thread, turn, type, data));
  private readonly dispatchingQueues = new Set<string>();
  private readonly startingTurns = new Map<string, Promise<unknown>>();
  private readonly changingModes = new Set<string>();
  /** Turns that asked the agent for a `[进度]` line, read back when they end. */
  private readonly reportTurns = new Set<string>();
  /** Archived threads asking an agent one question (askOnce): they may run, and get nothing added. */
  private readonly backgroundThreads = new Set<string>();
  /** Questions whose input must not enter hub history or search indexes. */
  private readonly unrecordedQuestions = new Set<string>();
  /** Which protocol blocks each thread's agent has read, so a block is not attached to every message. */
  private readonly injections: Injections;
  /** Whether an enabled extension holds `progress.report`. Wired by the daemon's entry point. */
  progressWanted: () => boolean = () => false;
  private readonly approvals = new Map<string, PendingApproval>();
  /** Approval hooks are retired before terminal events or cancellation can reenter listeners. */
  private readonly approvalTurns = new Map<string, string>();
  private readonly listeners = new Set<Listener>();
  /** Agent sessions being imported right now, so the same one is not imported twice in parallel. */
  private readonly importing = new Set<string>();
  /** Read-only checks are cached per instance and share in-flight probes. */
  private readonly diagnosticCache = new Map<string, { at: number; pending: boolean; value: Promise<unknown> }>();
  /** Plan-usage reads in flight or fresh, per agent: the probes spawn a CLI, so they are shared. */
  private readonly quotaCache = new Map<string, { at: number; value: Promise<PlanQuota> }>();
  /** The last probe each agent answered, kept for between-turn decisions when a fresh one fails. */
  private readonly lastGoodQuota = new Map<string, { at: number; quota: PlanQuota }>();
  /** When the agent last told its usage while answering: a refresh within OBSERVED_FRESH_MS uses it. */
  private readonly observedQuotaAt = new Map<string, number>();
  /** The last reading sent to pages, so an unchanged one is not sent again. */
  private readonly publishedQuota = new Map<string, string>();
  private quotaWatch?: NodeJS.Timeout;
  /** Threads whose session is being synced right now, for the same reason. */
  private readonly syncing = new Set<string>();
  /** Messages waiting for the running turn of their thread to finish. Kept in memory only. */
  private readonly queues = new Map<string, QueuedTurn[]>();
  /** Latest provider queue position per thread. Kept in memory; it is only meaningful while a turn runs. */
  private readonly modelQueues = new Map<string, ModelQueueState>();

  /** Where threads may work; directories can be added from the page while the hub runs. */
  readonly dirs: Workdirs;
  readonly references: ContextReferences;
  readonly previewNotes: PreviewNotes;
  readonly autoHandover: AutoHandover;
  readonly autoCompact: AutoCompact;
  readonly personal: PersonalService;
  readonly lark: LarkChannel;
  readonly briefs: PersonalBriefSchedule;
  private readonly quietTurns = new Set<string>();
  private readonly settlingHandovers = new Set<Promise<unknown>>();

  constructor(
    private readonly store: Store,
    private readonly adapters: Map<string, AgentAdapter>,
    workdirs: string[] | Workdirs,
    private readonly attachments: AttachmentStore,
    /** What each instance is beyond its adapter: its adapter kind and linked account. */
    private readonly agentMeta: () => Array<{ id: string; adapter?: string; account?: string | null; hidden?: boolean; activeLogin?: { id: string } & LoginState }> = () => [],
  ) {
    this.dirs = Array.isArray(workdirs) ? new Workdirs(workdirs) : workdirs;
    this.references = new ContextReferences(store, this);
    this.previewNotes = new PreviewNotes(store, this);
    this.injections = new Injections(store);
    this.autoHandover = new AutoHandover(store, this);
    this.quotaResume = new QuotaResume(store, this, this.autoHandover);
    this.autoHandover.onSaved = settings => this.quotaResume.onSettings(settings.resume);
    this.autoCompact = new AutoCompact(store, this);
    for (const [id, adapter] of adapters) adapter.onQuotaObserved = quota => this.observeQuota(id, quota);
    this.startQuotaWatch();
    this.quotaResume.start();
    this.personal = new PersonalService(store, this, adapters);
    this.lark = new LarkChannel(store, this.personal);
    this.briefs = new PersonalBriefSchedule(store, () => this.personal.review.status().quietHours, () => todoStages(this, this.listTodos()));
    this.dirs.personalDirectory = () => this.personal.directory();
    this.skills = new SkillsManager(this.adapters);
    this.mcp = new McpManager(this.adapters, store.mcpEntries);
  }

  /** Watches parked threads for their quota to reset, then continues them. Owned here so the HTTP layer can reach it. */
  readonly quotaResume: QuotaResume;

  /** Marks the timeline: the quota came back and the daemon resent the failed turn by itself. */
  recordQuotaResume(id: string, resetsAt: string | null): void {
    this.emitThreadEvent(id, null, 'handover.resume', { ...(resetsAt ? { resetsAt } : {}) });
  }

  /** The person's 「额度恢复后自动继续」 on an exhausted turn, or its cancel. */
  async setResume(id: string, armed: unknown): Promise<AutoHandoverView & { resume: ResumeView | null }> {
    this.getThread(id);
    if (typeof armed !== 'boolean') throw new HttpError(400, 'resume must be boolean');
    if (armed) await this.quotaResume.arm(id, 'user');
    else this.quotaResume.drop(id);
    return { ...this.autoHandover.view(id), resume: this.quotaResume.view(id) };
  }

  /** The LoopX Goal adapter, when one is attached: a thread it binds is a Goal conversation, every other is ordinary. */
  loopx: LoopxHooks | null = null;
  /** What the last local-session scan knows of a native session: where it came from, and its Goal. */
  /** Directories whose sessions are not offered for import (`LOOCHAT_HIDDEN_SESSION_DIRS`). */
  hiddenSessionDirs: string[] = [];
  sessionIndex: ((sessionId: string) => { origin?: 'app' | 'cli' | null; loopx?: import('./session-enrich.js').SessionLoopx } | undefined) | null = null;

  /** The skills every agent instance on this machine can reach. Read-mostly, by the skills page. */
  readonly skills: SkillsManager;

  /** The MCP servers of every agent instance on this machine, and the one-entry writes the page asks for. */
  readonly mcp: McpManager;

  /** Every directory a thread may work in right now, the scratch area included. */
  private get workdirs(): string[] {
    return this.dirs.all();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Directory browsing permissions include the personal directory, but not scratch threads. */
  get allowedWorkdirs(): string[] {
    return [...this.dirs.roots(), this.personal.directory()];
  }

  async listAgents(): Promise<AgentInfo[]> {
    const out: AgentInfo[] = [];
    const meta = new Map(this.agentMeta().map((m) => [m.id, m]));
    for (const [id, a] of this.adapters) {
      // The key is the instance id: a second account of the same CLI must not report the
      // adapter's own name, or the picker could not tell the two apart.
      const m = meta.get(id);
      out.push({
        id,
        label: a.label,
        icon: a.icon,
        ...(m?.adapter ? { adapter: m.adapter } : {}),
        ...(m?.account !== undefined ? { account: m.account } : {}),
        ...(m?.hidden !== undefined ? { hidden: m.hidden } : {}),
        available: await a.available(),
        ...(a.startLogin ? { login: 'wizard' as const } : a.loginCommand ? { login: 'terminal' as const } : {}),
        ...(m?.activeLogin ? { activeLogin: m.activeLogin } : {}),
        capabilities: a.capabilities,
        ...(a.capabilities.streamBridge ? { streamBridgeEnabled: a.streamBridgeEnabled ?? false } : {}),
      });
    }
    return out;
  }

  /** Invalidate only metadata; running turns keep their existing adapter until they finish. */
  agentSettingsChanged(agent: string): void {
    this.diagnosticCache.delete(agent);
  }

  /** Commands for an agent. `cwd` selects project-level commands and must be an allowed workdir. */
  async listCommands(agent: string, cwd?: string): Promise<SlashCommand[]> {
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(404, `unknown agent: ${agent}`);
    const dir = cwd ? resolve(cwd) : undefined;
    if (dir && !isWithinWorkdirs(dir, this.workdirs)) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    return adapter.commands(dir);
  }

  /**
   * Plan usage of an agent instance. The probe costs a CLI run (claude's takes seconds), so the
   * answer is reused for a minute and concurrent callers share one run.
   */
  agentQuota(agent: string, refresh = false, force = false): Promise<PlanQuota> {
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(404, `unknown agent: ${agent}`);
    if (!adapter.capabilities.quota || !adapter.fetchQuota) throw new HttpError(400, `${agent} has no plan usage to read`);
    const hit = this.quotaCache.get(agent);
    if (!refresh && hit && Date.now() - hit.at < QUOTA_TTL_MS) return hit.value;
    // Even a refresh does not probe right after the agent said it: the usage endpoint can turn a
    // probe away, and it would not know more.
    const observed = this.observedQuotaAt.get(agent);
    if (refresh && !force && hit && observed !== undefined && Date.now() - observed < OBSERVED_FRESH_MS) return hit.value;
    const value = adapter.fetchQuota().catch((err) => {
      // A failed probe is not worth caching; the next caller can try again right away.
      if (this.quotaCache.get(agent)?.value === value) this.quotaCache.delete(agent);
      throw err;
    });
    this.quotaCache.set(agent, { at: Date.now(), value });
    void value.then(q => { this.lastGoodQuota.set(agent, { at: Date.now(), quota: q }); this.publishQuota(agent, q); }).catch(() => undefined);
    return value;
  }

  /** Whether this instance has plan usage to read at all; a bridge to a third-party model has none. */
  readsQuota(agent: string): boolean {
    const adapter = this.adapters.get(agent);
    return !!adapter?.capabilities.quota && !!adapter.fetchQuota;
  }

  /** The last reading that came through, however old; null when none ever did. */
  lastQuota(agent: string): PlanQuota | null { return this.lastGoodQuota.get(agent)?.quota ?? null; }

  /** What the agent itself said while answering beats any probe: it feeds the same cache the
   *  probes fill, so decisions and the dashboard read one truth. */
  private observeQuota(agent: string, observed: PlanQuota): void {
    // The agent names its windows, not its plan; the plan a probe read still holds.
    const last = this.lastGoodQuota.get(agent)?.quota;
    const plan = observed.plan ?? last?.plan;
    // Nor does it name the balances of keys billed per use; those a probe read still hold too.
    const balances = observed.balances ?? last?.balances;
    const quota = { ...observed, ...(plan ? { plan } : {}), ...(balances ? { balances } : {}) };
    this.lastGoodQuota.set(agent, { at: Date.now(), quota });
    this.quotaCache.set(agent, { at: Date.now(), value: Promise.resolve(quota) });
    this.observedQuotaAt.set(agent, Date.now());
    this.publishQuota(agent, quota);
  }

  /** Sends a new reading to open pages, so quota shown anywhere moves as the agent answers. */
  private publishQuota(agent: string, quota: PlanQuota): void {
    const key = JSON.stringify([quota.windows, quota.balances ?? null, quota.plan ?? null, quota.note ?? null]);
    if (this.publishedQuota.get(agent) === key) return;
    this.publishedQuota.set(agent, key);
    const event: StoredEvent = { threadId: '', seq: 0, turnId: null, type: 'quota.updated', ts: new Date().toISOString(), data: { agent, quota } };
    for (const listener of this.listeners) listener(event);
  }

  /** The freshest reading a between-turn decision may trust: a recent one without a probe, a fresh
   *  probe when the reading is old, and — when even the probe fails — a reading up to `graceMs`
   *  old, for a near-limit check errs better on the side of acting than on waiting. */
  async decisionQuota(agent: string, maxAgeMs = 5 * 60_000, graceMs = 15 * 60_000): Promise<PlanQuota | null> {
    const last = this.lastGoodQuota.get(agent);
    if (last && Date.now() - last.at <= maxAgeMs) return last.quota;
    try {
      return await this.agentQuota(agent, true);
    } catch {
      const older = this.lastGoodQuota.get(agent);
      return older && Date.now() - older.at <= graceMs ? older.quota : null;
    }
  }

  /** Agents worth watching: those of running threads and threads active in the last quarter hour. */
  private quotaWatchAgents(): string[] {
    const agents = new Set<string>();
    for (const id of this.running.keys()) {
      const thread = this.store.getThread(id);
      if (thread) agents.add(thread.agent);
    }
    for (const row of this.store.activeThreadsSince(new Date(Date.now() - 15 * 60_000).toISOString())) {
      const thread = this.store.getThread(row.threadId);
      if (thread && !thread.archivedAt) agents.add(thread.agent);
    }
    return [...agents].filter(a => this.adapters.get(a)?.capabilities.quota).slice(0, 8);
  }

  /** Refreshes the watched agents' quota in the background, so between-turn decisions and the
   *  dashboard see usage move without a probe after every turn. */
  async refreshQuotas(): Promise<void> {
    for (const agent of this.quotaWatchAgents()) await this.agentQuota(agent, true).catch(() => undefined);
  }
  private startQuotaWatch(): void {
    const timer = setInterval(() => void this.refreshQuotas(), 3 * 60_000);
    timer.unref?.();
    this.quotaWatch = timer;
  }

  /**
   * Conversations the agent already has, in any directory that still exists, is not hidden and
   * could be a workdir (the sidebar's rule, {@link offeredSessionDir}): importing one from outside
   * the workdirs adds its directory (see {@link importSession}).
   * `threadId` is set for those that are already a thread here (including archived ones).
   */
  async listAgentSessions(agent: string): Promise<Array<AgentSessionSummary & { threadId: string | null }>> {
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(404, `unknown agent: ${agent}`);
    if (!adapter.capabilities.history || !adapter.listSessions) {
      throw new HttpError(400, `${agent} has no session history to import`);
    }
    const sessions = await adapter.listSessions();
    const offered = new Map<string, boolean>();
    const workdirs = this.workdirs;
    const hidden = this.hiddenSessionDirs;
    return sessions
      .filter((s) => !isProbeSession(s.title))
      // A session whose directory has since been removed, or that could never become a workdir,
      // cannot be continued, so it is not offered.
      .filter((s) => {
        if (!offered.has(s.cwd)) offered.set(s.cwd, offeredSessionDir(s.cwd, workdirs, hidden) && isDirectory(s.cwd));
        return offered.get(s.cwd);
      })
      .map((s) => ({ ...s, threadId: this.store.findThreadByAgentSession(agent, s.id)?.id ?? null }))
      // Newest first; sessions with no date go last.
      .sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''));
  }

  /**
   * Makes a thread out of a conversation the agent already has. Its past turns are written to the
   * event log once; the thread then continues the same agent session, so the terminal and the hub
   * keep sharing it.
   *
   * A session outside the workdirs is imported only with `addDirectory`, which the person's own
   * import click sets: its directory then becomes a workdir, as if added from the page (a home
   * directory or the root itself is refused as too wide). The directory and thread are committed
   * as one recoverable operation: either failure restores the previous workdirs and leaves no
   * importable thread.
   * Imports nobody clicked, such as the LoopX Goal sync, never widen where threads may work.
   */
  async importSession(input: { agent?: unknown; sessionId?: unknown; addDirectory?: boolean }): Promise<ThreadView> {
    const agent = String(input.agent ?? '');
    const sessionId = String(input.sessionId ?? '');
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(400, `unknown agent: ${agent}`);
    if (!adapter.capabilities.history) throw new HttpError(400, `${agent} has no session history to import`);
    const summary = adapter.getSession
      ? await adapter.getSession(sessionId)
      : (await this.listAgentSessions(agent)).find((s) => s.id === sessionId);
    const outside = !!summary?.cwd && !isWithinWorkdirs(summary.cwd, this.workdirs);
    if (!summary || summary.id !== sessionId || !summary.cwd || (outside && !input.addDirectory) || !isDirectory(summary.cwd)) {
      throw new HttpError(404, 'session not found, or it is outside the allowed workdirs');
    }
    const key = `${agent}:${sessionId}`;
    if (this.store.findThreadByAgentSession(agent, sessionId) || this.importing.has(key)) throw new HttpError(409, 'this session is already a thread');
    if (!adapter.loadHistory) throw new HttpError(400, `${agent} has no session history to import`);
    const refused = outside ? this.dirs.refusal(summary.cwd) : null;
    if (refused) throw new HttpError(400, refused);

    this.importing.add(key);
    try {
      const turns = await adapter.loadHistory(sessionId, summary.cwd);
      const title = (summary.title || turns[0]?.text || '').split('\n')[0].trim().slice(0, 60) || '导入的会话';
      const permissionMode = 'auto_edit'; // the same default a new thread gets
      const id = randomUUID();
      let undoWorkdir: (() => void) | null = null;
      let threadCommitted = false;

      try {
        // Publish the durable root before creating its thread, and keep the undo until the thread
        // transaction commits. A save failure therefore cannot report success or leave a retry 409.
        if (outside) undoWorkdir = this.dirs.add(summary.cwd);

        // Images are files, written before the database work so a failure there leaves nothing half-done.
        const images: StoredAttachment[][] = turns.map((turn) => {
          if (turn.images.length === 0) return [];
          try {
            return this.attachments.save(id, turn.images.slice(0, MAX_IMAGES)).stored;
          } catch {
            return []; // an image the hub would not accept is left out; the text still comes across
          }
        });

        const events: StoredEvent[] = [];
        this.store.transaction(() => {
          this.store.createThread({ id, agent, cwd: summary.cwd, title, permissionMode, model: null, effort: null });
          this.store.updateThread(id, { agentSessionId: sessionId, hubTurnAt: new Date().toISOString() });
          const add = (turnId: string | null, type: EventType, data: Record<string, unknown>) =>
            events.push(this.store.appendEvent(id, turnId, type, data));
          add(null, 'thread.created', { agent, cwd: summary.cwd, imported: true });
          turns.forEach((turn, i) => {
            const turnId = randomUUID();
            add(turnId, 'turn.started', { text: turn.text, imported: true, ...(images[i].length ? { images: images[i] } : {}) });
            for (const ev of turn.events) add(turnId, ev.type, ev.data);
            add(turnId, 'turn.done', { stopReason: 'end_turn', imported: true });
          });
        });
        threadCommitted = true;
        for (const ev of events) for (const l of this.listeners) l(ev);
        return this.threadView(id);
      } catch (err) {
        if (threadCommitted) {
          try {
            this.store.deleteThread(id);
          } catch (cleanupErr) {
            console.error(`import: could not remove thread ${id} after failed import: ${cleanupErr instanceof Error ? cleanupErr.message : cleanupErr}`);
          }
        }
        try {
          this.attachments.removeThread(id);
        } catch (cleanupErr) {
          console.error(`import: could not remove attachments after failed import: ${cleanupErr instanceof Error ? cleanupErr.message : cleanupErr}`);
        }
        if (undoWorkdir) {
          try {
            undoWorkdir();
          } catch (rollbackErr) {
            console.error(`import: could not restore workdirs after ${summary.cwd} import failed: ${rollbackErr instanceof Error ? rollbackErr.message : rollbackErr}`);
          }
        }
        throw err;
      }
    } finally {
      this.importing.delete(key);
    }
  }

  /**
   * Copies a thread into a new one that can go its own way. The agent copies its side of the
   * conversation; the hub copies the event log and the images. With `turnId` the copy ends where
   * that turn ended, on both sides; without it, or when that turn is the last, it is the whole thread.
   */
  async forkThread(id: string, opts: { turnId?: string; worktree?: boolean } = {}): Promise<ThreadView> {
    const thread = this.getThread(id);
    const adapter = this.adapters.get(thread.agent);
    if (!adapter?.capabilities.fork || !adapter.forkSession) throw new HttpError(400, `${thread.agent} cannot fork a conversation`);
    if (!thread.agentSessionId) throw new HttpError(400, 'there is nothing to fork yet; send a message first');
    const running = this.running.has(id);
    if (running && !opts.turnId) throw new HttpError(409, 'wait for the running turn to finish before forking');

    // Where the hub's copy stops, and which of its turns that is. Null: the whole thread.
    let cut: { upToSeq: number; hubIndex: number; hubKeys: string[] } | null = null;
    if (opts.turnId) {
      const starts = this.store.listEventsOfType(id, 'turn.started');
      const hubIndex = starts.findIndex((ev) => ev.turnId === opts.turnId);
      if (hubIndex < 0) throw new HttpError(404, 'no such turn in this thread');
      // A turn the user stopped is not a place to go on from: the agent may not have kept it at all.
      const end = this.store.listEventsOfType(id, 'turn.done').find((ev) => ev.turnId === opts.turnId);
      if (running && !end) throw new HttpError(409, 'wait for this turn to finish before forking from it');
      if (end?.data.stopReason === 'cancelled') throw new HttpError(409, 'this turn was stopped, so a copy cannot end there');
      if (hubIndex < starts.length - 1) {
        // Match only through the requested boundary. Later turns may be running or divergent, and
        // they are not part of the copy being made.
        cut = { upToSeq: starts[hubIndex + 1].seq - 1, hubIndex, hubKeys: starts.slice(0, hubIndex + 1).map((ev) => turnKey(String(ev.data.text ?? ''))) };
      }
    }

    const copyId = randomUUID();
    // Where the copy runs. In place, it shares whatever worktree its source stands in.
    let cwd = thread.cwd;
    let worktree = thread.worktree;
    let added: { main: string; tree: Worktree } | null = null;
    const undoWorktree = async () => {
      if (added) await removeWorktree(added.main, added.tree).catch(() => undefined);
    };

    let forkedSessionId: string;
    try {
      const forkRef = cut ? await this.agentForkRef(thread, adapter, cut.hubIndex, cut.hubKeys) : undefined;
      if (opts.worktree) {
        const plan = await this.worktreePlan(thread);
        if (!plan.checkout) throw new HttpError(400, WORKTREE_REFUSALS[plan.reason]);
        const tree = await addWorktree(plan.checkout, copyId.slice(0, 8)).catch((err) => {
          throw new HttpError(500, `could not add a git worktree: ${err instanceof Error ? err.message : err}`);
        });
        added = { main: plan.checkout.main, tree };
        cwd = plan.checkout.prefix ? join(tree.root, plan.checkout.prefix) : tree.root;
        // `.worktrees` could be a link to somewhere else; where it really is still has to be allowed.
        if (!isWithinWorkdirs(cwd, this.workdirs)) throw new HttpError(400, WORKTREE_REFUSALS.outside_workdirs);
        worktree = { root: tree.root, branch: tree.branch, of: thread.worktree?.of ?? thread.cwd };
      }
      forkedSessionId = await adapter.forkSession(thread.agentSessionId, thread.cwd, forkRef, added ? cwd : undefined);
    } catch (err) {
      await undoWorktree();
      if (err instanceof HttpError) throw err;
      throw new HttpError(502, `could not fork the ${thread.agent} session: ${err instanceof Error ? err.message : err}`);
    }

    this.attachments.copyThread(thread.id, copyId);
    try {
      this.store.transaction(() => {
        this.store.createThread({
          id: copyId,
          agent: thread.agent,
          cwd,
          title: `${thread.title}（分叉）`,
          permissionMode: thread.permissionMode,
          workMode: thread.workMode,
          speed: thread.speed,
          model: thread.model,
          effort: thread.effort,
          forkedFrom: thread.id,
          worktree,
        });
        this.store.updateThread(copyId, { agentSessionId: forkedSessionId, sectionId: thread.sectionId }, { touch: true });
        this.store.copyEvents(thread.id, copyId, cut?.upToSeq);
      });
    } catch (err) {
      this.attachments.removeThread(copyId);
      await undoWorktree();
      throw err;
    }
    // The last event of the copy: what comes before it was copied, what comes after is its own.
    this.emitThreadEvent(copyId, null, 'thread.updated', { forkedFrom: thread.id, ...(added ? { worktree: { branch: added.tree.branch } } : {}) });
    return this.threadView(copyId);
  }

  /** What forking this thread can offer. Asked when the user reaches for the fork button, not before. */
  async forkOptions(id: string): Promise<ForkOptions> {
    const plan = await this.worktreePlan(this.getThread(id));
    if (!plan.checkout) return { worktree: { available: false, reason: plan.reason } };
    return {
      worktree: {
        available: true,
        branch: plan.checkout.branch,
        changes: await countChanges(plan.checkout.top).catch(() => 0),
      },
    };
  }

  /** Whether a copy of this thread could get a worktree of its own, and where its directory stands in git. */
  private async worktreePlan(
    thread: ThreadRecord,
  ): Promise<{ checkout: Checkout; reason?: undefined } | { checkout: null; reason: NonNullable<ForkOptions['worktree']['reason']> }> {
    if (!this.adapters.get(thread.agent)?.capabilities.forkInto) return { checkout: null, reason: 'agent' };
    return this.checkoutFor(thread.cwd);
  }

  /** Where a directory stands in git, when a worktree can be added to its repository. */
  private async checkoutFor(cwd: string): Promise<{ checkout: Checkout; reason?: undefined } | { checkout: null; reason: 'not_git' | 'outside_workdirs' }> {
    const checkout = existsSync(cwd) ? await findCheckout(cwd) : null;
    if (!checkout) return { checkout: null, reason: 'not_git' };
    // Worktrees go under the repository's own directory, so that is what has to be allowed.
    if (!isWithinWorkdirs(checkout.main, this.workdirs)) return { checkout: null, reason: 'outside_workdirs' };
    return { checkout };
  }

  /**
   * What the agent needs to stop its copy where one of the hub's turns ended. The agent numbers
   * and names its turns its own way, so its history is read and lined up with the hub's by what
   * was asked in each turn. Undefined when the agent has nothing after that turn: then its whole
   * conversation is the copy.
   */
  private async agentForkRef(thread: ThreadRecord, adapter: AgentAdapter, hubIndex: number, hubKeys: string[]): Promise<string | undefined> {
    if (!adapter.capabilities.forkAt || !adapter.loadHistory || !thread.agentSessionId) {
      throw new HttpError(400, `${thread.agent} can only fork a whole conversation`);
    }
    const active = this.running.has(thread.id);
    const retryDelays = active ? [0, 50, 150] : [0];
    for (const delayMs of retryDelays) {
      // A second app-server can briefly see the active writer's history one turn behind. Only
      // retry that prefix case; a real text disagreement still fails immediately.
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
      const agentTurns = await adapter.loadHistory(thread.agentSessionId, thread.cwd);
      const matched = matchTurns(agentTurns.map((t) => turnKey(t.text)), hubKeys, ThreadManager.SYNC_SKIP_TOLERANCE);
      const agentIndex = matched?.hubToAgent[hubIndex] ?? -1;
      if (agentIndex < 0) {
        if (matched && delayMs !== retryDelays.at(-1)) continue;
        break;
      }
      // During a live turn the read snapshot may end at this older boundary even though the writer
      // has already started another turn. Pass the explicit ref instead of cloning its moving tip.
      if (!active && agentIndex === agentTurns.length - 1) return undefined;
      const ref = agentTurns[agentIndex].forkRef;
      if (!ref) throw new HttpError(409, `${thread.agent} cannot stop a copy at this turn`);
      return ref;
    }
    throw new HttpError(409, `this turn is not in ${thread.agent}'s own history, so a copy cannot stop there`);
  }

  /** Where a stored image is on disk, for the HTTP layer to stream. */
  getAttachment(threadId: string, attachmentId: string): { path: string; mimeType: string; size: number } {
    this.getThread(threadId);
    const found = this.attachments.find(threadId, attachmentId);
    if (!found) throw new HttpError(404, 'attachment not found');
    return found;
  }

  async saveVideo(threadId: string, source: AsyncIterable<Uint8Array>, name: string): Promise<StoredVideo> {
    const thread = this.getThread(threadId);
    if (thread.archivedAt) throw new HttpError(409, 'this thread is archived');
    return this.attachments.saveVideo(threadId, source, name);
  }

  /** Counts a waiting message's uploads by kind; `videos` held only videos before other files were allowed. */
  private queuedUploads(threadId: string, ids: string[]): { videos: number; files?: number } {
    const files = ids.filter(id => { const found = this.attachments.find(threadId, id); return found && !isVideoType(found.mimeType); }).length;
    return { videos: ids.length - files, ...(files ? { files } : {}) };
  }

  async saveFile(threadId: string, source: AsyncIterable<Uint8Array>, name: string): Promise<StoredVideo> {
    const thread = this.getThread(threadId);
    if (thread.archivedAt) throw new HttpError(409, 'this thread is archived');
    return this.attachments.saveFile(threadId, source, name);
  }

  usageReport(hours: number, agent: string) { return this.store.telemetry.read(hours, agent); }
  usageCosts(hours: number, agent: string) { return this.store.telemetry.costReport(hours, agent); }
  usageDaily(agent: string, timeZone?: string) { return this.store.telemetry.daily(agent, timeZone); }

  activityReport(days: number, agent: string) { return this.store.activity.read(days, agent); }

  /** Keep log summaries until they change; disk checks expire after 30 seconds and share in-flight reads. */
  private readonly outputsCache = new Map<string, { seq: number; checkedAt: number | null; value: Promise<ThreadOutputs> }>();

  /** What the thread has produced over all its turns: changed files, and the pictures and files its answers name. */
  async threadOutputs(threadId: string): Promise<ThreadOutputs> {
    const thread = this.getThread(threadId);
    const latest = this.store.history.latest(threadId);
    const cached = this.outputsCache.get(threadId);
    if (cached && cached.seq === latest && (cached.checkedAt === null || Date.now() - cached.checkedAt < 30_000)) return cached.value;
    const read = async () => {
      if (cached && cached.seq === latest) return checkMissingFiles(await cached.value);
      const { diffs, answers, seq } = await this.store.outputSources(threadId);
      let cwdReal = thread.cwd;
      try { cwdReal = realpathSync(thread.cwd); } catch { /* The directory may have been removed. */ }
      const changes = changedFiles(diffs, thread.cwd, cwdReal);
      return checkMissingFiles({
        ...mentionedFiles(answers, thread.cwd, changes.files, cwdReal), seq,
        changes: { ...changes, files: changes.files.slice(0, MAX_OUTPUT_ITEMS).map(({ seq: _seq, ...file }) => file) },
      });
    };
    const entry: NonNullable<typeof cached> = {
      seq: latest, checkedAt: null,
      value: read().then(outputs => {
        entry.seq = outputs.seq;
        entry.checkedAt = Date.now();
        return outputs;
      }).catch(error => {
        if (this.outputsCache.get(threadId) === entry) this.outputsCache.delete(threadId);
        throw error;
      }),
    };
    this.outputsCache.delete(threadId);
    this.outputsCache.set(threadId, entry);
    if (this.outputsCache.size > 50) this.outputsCache.delete(this.outputsCache.keys().next().value!);
    return entry.value;
  }

  /**
   * The thread's session statistics: stored per-turn timing, tokens summed from the native usage
   * samples, and the running turn's progress computed fresh. Turns without a stored row — old
   * threads, imports, a turn that ended while the daemon was down — are computed once here and
   * then read from storage; the running turn is never stored, it is still growing.
   */
  threadStats(threadId: string): ThreadStats {
    this.getThread(threadId);
    const turns = this.store.listTurns(threadId);
    const runningTurnId = this.store.openTurn(threadId);
    const rows = this.store.telemetry.turnStatsRows(threadId);
    for (const turn of turns) {
      if (rows.has(turn.turnId)) continue;
      const row = this.computeTurnStats(threadId, turn.turnId);
      if (!row) continue;
      rows.set(turn.turnId, row);
      // The running turn is computed fresh on every read and never stored; it is still growing.
      if (turn.turnId !== runningTurnId) this.store.telemetry.recordTurnStats(threadId, turn.turnId, row);
    }
    return aggregateThreadStats({
      threadId,
      checkedAt: new Date().toISOString(),
      running: runningTurnId !== null,
      runningTurnId,
      turns,
      rows,
      tokens: this.store.telemetry.tokenSumsByTurn(threadId),
      compactions: this.store.compactionSummary(threadId),
    });
  }

  private computeTurnStats(threadId: string, turnId: string): TurnStatsRow | null {
    const events = this.store.turnEvents(threadId, turnId);
    if (!events.length) return null;
    const trace = buildTrace(events);
    return statsOfTrace(trace, trace.endedAt ?? events[events.length - 1].ts);
  }

  /** One turn of a thread as a trace, the last turn when none is named. */
  turnTrace(threadId: string, turnId?: string): TurnTrace {
    const thread = this.getThread(threadId);
    const turns = this.store.listTurns(threadId);
    const turn = turnId ? turns.find((t) => t.turnId === turnId) : turns[turns.length - 1];
    if (!turn) throw new HttpError(404, '这个对话没有这一轮');
    const trace = buildTrace(this.store.turnEvents(threadId, turn.turnId));
    return {
      threadId,
      turnId: turn.turnId,
      title: thread.title,
      agent: thread.agent,
      cwd: thread.cwd,
      models: trace.models.length ? trace.models : thread.model ? [thread.model] : [],
      // A turn that never ended and is not the one running now was cut off.
      status: trace.status === 'running' && (thread.status !== 'running' || turn !== turns[turns.length - 1]) ? 'cancelled' : trace.status,
      startedAt: turn.ts,
      endedAt: trace.endedAt,
      steps: trace.steps,
      segments: trace.segments,
      totals: trace.totals,
      turns: turns.map((t) => ({ turnId: t.turnId, ts: t.ts })),
    };
  }

  /**
   * The whole prompt a thread's conversation runs with, from the agent's own session file. Reading
   * it starts nothing and writes nothing; an agent that does not persist its prompt says so.
   */
  async threadSystemPrompt(threadId: string): Promise<SystemPrompt> {
    const thread = this.getThread(threadId);
    if (!thread) throw new HttpError(404, 'no such thread');
    const adapter = this.adapters.get(thread.agent);
    if (!adapter?.capabilities.systemPrompt || !adapter.readSystemPrompt) {
      throw new HttpError(400, `${thread.agent} 的会话文件不保存系统提示词`);
    }
    if (!thread.agentSessionId) throw new HttpError(400, '这个对话还没有会话');
    const prompt = await adapter.readSystemPrompt(thread.agentSessionId, thread.cwd);
    if (!prompt) throw new HttpError(404, '会话文件里没有系统提示词');
    return prompt;
  }

  /**
   * One tool call's complete output, read from the agent's own session file. The hub stores a
   * capped snapshot of every tool output (docs/tool-output-storage-design.md); this is how the
   * page gets past the cap when someone actually wants the whole thing. Reading changes nothing.
   */
  async threadToolOutput(threadId: string, turnId: string, detailId: number): Promise<{ output: string }> {
    const thread = this.getThread(threadId);
    if (!thread) throw new HttpError(404, 'no such thread');
    const detail = await this.store.history.detail(threadId, turnId, detailId) as { toolId?: string | null };
    if (!detail.toolId) throw new HttpError(404, '这个详情不是一次工具调用');
    const adapter = this.adapters.get(thread.agent);
    if (!adapter?.capabilities.history || !adapter.loadHistory) {
      throw new HttpError(400, `${thread.agent} 的会话历史不能从这里读取`);
    }
    if (!thread.agentSessionId) throw new HttpError(400, '这个对话还没有会话');
    const turns = await adapter.loadHistory(thread.agentSessionId, thread.cwd);
    for (const turn of turns) {
      for (const ev of turn.events) {
        if ((ev.type !== 'tool.update' && ev.type !== 'tool.end') || String((ev.data as Record<string, unknown>).id) !== detail.toolId) continue;
        const data = ev.data as Record<string, unknown>;
        const text = toolText(data.output, data.content);
        if (text) return { output: text };
      }
    }
    throw new HttpError(404, 'agent 会话里找不到这个工具的输出');
  }

  /** Whether one thread's full tool outputs can be read back from its agent session. */
  canReadToolOutput(threadId: string): boolean {
    const thread = this.getThread(threadId);
    const adapter = thread && this.adapters.get(thread.agent);
    return !!(thread?.agentSessionId && adapter?.capabilities.history && adapter.loadHistory);
  }

  agentDiagnostic(agent: string, refresh = false): Promise<unknown> {
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(404, '未知 agent 实例');
    const hit = this.diagnosticCache.get(agent);
    if (hit && (hit.pending || (!refresh && Date.now() - hit.at < 60000))) return hit.value;
    const entry = { at: Date.now(), pending: true, value: Promise.resolve<unknown>(null) };
    entry.value = (async () => {
      const base = { agent, label: adapter.label, capabilities: adapter.capabilities };
      try {
        if (!await adapter.available()) return { ...base, checkedAt: new Date().toISOString(), state: 'missing', message: '没有找到 CLI，请检查该实例的命令和 PATH。', version: null, protocol: null, models: [] };
        const runtime = await adapter.diagnose?.().catch(() => null);
        if (adapter.capabilities.modelCatalog === false) return {
          ...base, ...runtime, checkedAt: new Date().toISOString(), state: 'cli_only', models: [],
          message: 'CLI 可启动；原生接口不提供模型目录，沿用 CLI 默认设置。登录有效性以实际请求结果为准。',
        };
        try {
          const models = await this.adapterModels(adapter, refresh);
          return { ...base, ...runtime, checkedAt: new Date().toISOString(), state: models.length ? 'ready' : 'empty',
            message: models.length ? '模型读取成功。登录有效性以实际请求结果为准。' : 'CLI 可启动，但未返回可选模型。请检查原生 CLI 的登录、模型配置和套餐。', models };
        } catch (error) { return { ...base, ...runtime, checkedAt: new Date().toISOString(), ...diagnosticFailure(error), models: [] }; }
      } catch (error) { return { ...base, checkedAt: new Date().toISOString(), ...diagnosticFailure(error), models: [] }; }
    })().finally(() => { entry.pending = false; });
    this.diagnosticCache.set(agent, entry);
    return entry.value;
  }

  private adapterModels(adapter: AgentAdapter, refresh = false): Promise<ModelOption[]> {
    adapter.onModelsChanged = models => {
      // Instance ids can differ from adapter ids; removed/replaced instances must not publish.
      const agent = [...this.adapters].find(([, current]) => current === adapter)?.[0];
      if (!agent) return;
      const event: StoredEvent = { threadId: '', seq: 0, turnId: null, type: 'agent.modelsChanged',
        ts: new Date().toISOString(), data: { agent, models } };
      for (const listener of this.listeners) listener(event);
    };
    return adapter.models(refresh).catch(error => { throw modelLoginFailure(error, adapter.label); });
  }

  async listModels(agent: string, refresh = false): Promise<ModelOption[]> {
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(404, `unknown agent: ${agent}`);
    return this.adapterModels(adapter, refresh);
  }

  /** Turns of any thread, archived ones too, in which the words were said. */
  search(query: unknown): SearchHit[] {
    return typeof query === 'string' ? this.store.searchText(query.slice(0, 200)) : [];
  }

  /**
   * One question to an agent, answered in a thread of its own that is archived from the start: it
   * never shows in the list, and the asking thread's agent and session are not touched. The answer
   * comes back as plain text. A turn that asks for an approval or a reply is stopped, since nobody
   * is there to give one; so is one that takes longer than `timeoutMs`. An AbortSignal stops this
   * exact question, including startup. `recordPrompt: false` omits its input from hub history;
   * native agent session retention is still owned by the adapter.
   */
  async askOnce(agent: string, text: string, opts: { model?: string | null; title?: string; timeoutMs?: number; signal?: AbortSignal; recordPrompt?: boolean } = {}): Promise<string> {
    opts.signal?.throwIfAborted();
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(400, `unknown agent: ${agent}`);
    const modes = adapter.capabilities.permissionModes;
    const thread = await this.createThread({
      agent, cwd: '', scratch: true, title: opts.title ?? 'LoopX', model: opts.model ?? null,
      permissionMode: modes.includes('ask') ? 'ask' : modes[0],
    });
    await this.updateThread(thread.id, { archived: true });
    this.backgroundThreads.add(thread.id);
    if (opts.recordPrompt === false) this.unrecordedQuestions.add(thread.id);
    const cancel = () => { void this.cancelTurn(thread.id).catch(() => undefined); };
    opts.signal?.addEventListener('abort', cancel, { once: true });
    let turnId: string | null = null;
    let stop: (() => void) | null = null;
    let timedOut = false;
    try {
      opts.signal?.throwIfAborted();
      const ended = new Promise<StoredEvent>((resolve, reject) => {
        // On timeout the turn is cancelled and its recorded end — `cancelled`, not a session error —
        // is awaited before the process is dropped in the finally below. The safety timer answers
        // if the agent never records anything at all.
        const timeoutMs = opts.timeoutMs ?? 120_000;
        const timer = setTimeout(() => {
          timedOut = true;
          void this.cancelTurn(thread.id).catch(() => undefined);
        }, timeoutMs);
        timer.unref?.();
        const safety = setTimeout(() => reject(new HttpError(504, 'the agent took too long to answer')), timeoutMs + 10_000);
        safety.unref?.();
        const unsubscribe = this.subscribe(ev => {
          if (ev.threadId !== thread.id) return;
          if (ev.type === 'approval.request' || ev.type === 'input.request') void this.cancelTurn(thread.id).catch(() => undefined);
          if (turnId && ev.turnId === turnId && turnOutcome(ev)) resolve(ev);
        });
        stop = () => { clearTimeout(timer); clearTimeout(safety); unsubscribe(); };
      });
      const started = await this.startTurn(thread.id, { text });
      if (!('turnId' in started) || !started.turnId) throw new HttpError(500, 'the question was not sent');
      turnId = started.turnId;
      if (opts.signal?.aborted) cancel();
      // The turn may have ended before its id was known here.
      const done = this.store.listTurnEvents(thread.id, turnId).find(e => turnOutcome(e));
      const end = done ?? await ended;
      const outcome = turnOutcome(end)!;
      opts.signal?.throwIfAborted();
      if (timedOut) throw new HttpError(504, 'the agent took too long to answer');
      if (outcome.outcome !== 'ok') throw new HttpError(502, outcome.message ?? `the agent's turn ended: ${outcome.outcome}`);
      return turnAnswer(this.store.listEvents(thread.id, 0, -1), turnId).trim();
    } finally {
      (stop as (() => void) | null)?.();
      opts.signal?.removeEventListener('abort', cancel);
      this.unrecordedQuestions.delete(thread.id);
      this.backgroundThreads.delete(thread.id);
      // The answer is read from the store: the agent process and its scratch folder can go now, so
      // one question does not leave a resident process behind.
      await this.dropSession(thread.id);
      if (this.dirs.scratchRoot && thread.cwd.startsWith(this.dirs.scratchRoot + sep)) rmSync(thread.cwd, { recursive: true, force: true });
    }
  }

  /** A title an extension gave the thread. Marked as such, so a later automatic pass may revise it. */
  autoRename(id: string, title: string): ThreadView {
    const clean = title.trim();
    if (!clean) throw new HttpError(400, 'empty title');
    this.getThread(id);
    this.store.updateThread(id, { title: clean });
    this.store.setTitleBy(id, 'auto');
    this.emitThreadEvent(id, null, 'thread.updated', {});
    return this.threadView(id);
  }


  /** Who set the thread's title; see Store.titleBy. */
  titleBy(id: string) { return this.store.titleBy(id); }

  listThreads(opts: { archived?: boolean } = {}): ThreadView[] {
    return this.store.listThreads(opts).map((t) => this.view(t));
  }

  generalSettings(): GeneralSettings {
    const value = this.store.hubSetting<Partial<GeneralSettings> | null>('general.settings', null);
    return {
      startupPage: value?.startupPage === 'recent-thread' ? 'recent-thread' : 'new-thread',
      autoArchiveDays: storedAutoArchiveDays(value?.autoArchiveDays),
      autoImportLocal: value?.autoImportLocal !== false,
    };
  }

  saveGeneralSettings(input: unknown): GeneralSettings {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, 'invalid general settings');
    const current = this.generalSettings();
    const raw = input as Partial<GeneralSettings>;
    const startupPage = raw.startupPage;
    if (startupPage !== 'new-thread' && startupPage !== 'recent-thread') throw new HttpError(400, 'invalid startup page');
    const autoArchiveDays = Object.hasOwn(raw, 'autoArchiveDays') ? parseAutoArchiveDays(raw.autoArchiveDays) : current.autoArchiveDays;
    if (Object.hasOwn(raw, 'autoImportLocal') && typeof raw.autoImportLocal !== 'boolean') throw new HttpError(400, 'invalid auto-import setting');
    const autoImportLocal = Object.hasOwn(raw, 'autoImportLocal') ? raw.autoImportLocal === true : current.autoImportLocal;
    const value: GeneralSettings = { startupPage, autoArchiveDays, autoImportLocal };
    this.store.saveHubSetting('general.settings', value);
    return value;
  }

  async draftModelPreferences(): Promise<DraftModelPreferences> {
    return readModelPreferences(this.store.hubSetting<unknown>(DRAFT_MODEL_PREFERENCES_KEY, {}));
  }

  /** Legacy clients send a map; merge its entries without deleting another device's choices. */
  async saveDraftModelPreferences(input: unknown): Promise<DraftModelPreferences> {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, 'invalid draft model preferences');
    for (const [agent, value] of Object.entries(input)) {
      if (!this.adapters.has(agent) || !value || typeof value !== 'object') continue;
      const raw = value as DraftModelPreference;
      const model = typeof raw.model === 'string' ? await this.validateModelOrNull(agent, raw.model) : null;
      const effort = typeof raw.effort === 'string' ? await this.validateEffortOrNull(agent, model, raw.effort) : null;
      const speed = typeof raw.speed === 'string' ? await this.validateSpeedOrNull(agent, model, raw.speed) : null;
      this.rememberModelPreference(agent, { model, effort, speed });
    }
    return this.draftModelPreferences();
  }

  async patchDraftModelPreference(input: unknown): Promise<DraftModelPreference> {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, 'invalid model preference');
    const { agent, ...raw } = input as { agent: string } & ModelPreferencePatch;
    if (!this.adapters.has(agent)) throw new HttpError(400, 'unknown agent');
    const patch: ModelPreferencePatch = {};
    for (const key of ['model', 'effort', 'speed'] as const) {
      if (raw[key] === undefined) continue;
      if (raw[key] !== null && (typeof raw[key] !== 'string' || !raw[key])) throw new HttpError(400, 'invalid model preference');
      patch[key] = raw[key];
    }
    if (!Object.keys(patch).length) throw new HttpError(400, 'empty model preference');
    const current = (await this.draftModelPreferences())[agent];
    const model = patch.model === undefined ? current?.model ?? null : patch.model;
    if (patch.model != null) await this.validateModel(agent, patch.model);
    if (patch.effort != null) await this.validateEffort(agent, model, patch.effort);
    if (patch.speed != null) await this.validateSpeed(agent, model, patch.speed);
    // Re-read after asynchronous validation; concurrent edits to other fields/instances survive.
    return this.rememberModelPreference(agent, patch);
  }

  private rememberModelPreference(agent: string, patch: ModelPreferencePatch): DraftModelPreference {
    const all = readModelPreferences(this.store.hubSetting<unknown>(DRAFT_MODEL_PREFERENCES_KEY, {}));
    const preference = applyModelPreference(all[agent], patch);
    this.store.saveHubSetting(DRAFT_MODEL_PREFERENCES_KEY, { ...all, [agent]: preference });
    return preference;
  }

  private async validateModelOrNull(agent: string, model: string): Promise<string | null> {
    try { return await this.validateModel(agent, model); }
    catch { return null; }
  }

  private async validateEffortOrNull(agent: string, model: string | null, effort: string): Promise<EffortLevel | null> {
    try { return await this.validateEffort(agent, model, effort); }
    catch { return null; }
  }

  private async validateSpeedOrNull(agent: string, model: string | null, speed: string): Promise<string | null> {
    try { return await this.validateSpeed(agent, model, speed); }
    catch { return null; }
  }

  /** Periodic housekeeping only archives conversations that do not currently need attention. */
  async autoArchiveOldThreads(now = new Date()): Promise<number> {
    const days = this.generalSettings().autoArchiveDays;
    if (days === null) return 0;
    const cutoff = now.getTime() - days * DAY_MS;
    let archived = 0;
    for (const thread of this.store.listThreads()) {
      const updatedAt = Date.parse(thread.updatedAt);
      if (!Number.isFinite(updatedAt) || updatedAt >= cutoff) continue;
      if (thread.pinned || thread.unread || thread.status === 'running') continue;
      if (this.view(thread).waiting) continue;
      try {
        await this.updateThread(thread.id, { archived: true });
        archived++;
      } catch (error) {
        if (error instanceof HttpError && error.status === 409) continue;
        throw error;
      }
    }
    return archived;
  }

  threadView(id: string): ThreadView {
    return this.view(this.getThread(id));
  }

  private view(thread: ThreadRecord): ThreadView {
    const adapter = this.adapters.get(thread.agent);
    const resumeCommand =
      adapter && thread.agentSessionId
        ? `cd ${shellQuote(thread.cwd)} && ${adapter.resumeCommand(thread.agentSessionId)}`
        : null;
    let cwdReal = thread.cwd;
    let cwdGone = false;
    try {
      cwdReal = realpathSync(thread.cwd);
    } catch {
      // The directory is gone; the stored spelling is the best there is.
      cwdGone = true;
    }
    let waitingReason: ThreadView['waitingReason'] = this.inputs.waitingReason(thread.id);
    for (const p of this.approvals.values()) if (p.threadId === thread.id) { waitingReason = 'approval'; break; }
    const waiting = waitingReason !== null;
    const queued = (this.queues.get(thread.id) ?? []).map((q) => {
      // Only a mention with words of its own is named in the message the person will see; one left
      // blank is the lead's to assign, and the queue's chip follows that message, not the request.
      const targets = (q.mentions ?? []).flatMap((m) => {
        const value = m as { label?: unknown; id?: unknown; task?: unknown };
        if (typeof value.task !== 'string' || !value.task.trim()) return [];
        return typeof value.label === 'string' ? value.label : String(value.id ?? '');
      });
      return { id: q.id, text: q.voiceSummary ? '[语音结束总结]' : q.displayText ?? q.text, images: q.images.length, ...this.queuedUploads(thread.id, q.videos),
        ...(q.channel ? { channel: q.channel } : {}),
        ...(targets.length ? { targets } : {}), ...(q.error ? { error: q.error } : {}) };
    });
    // The terminal wrote to this session after the hub last did: the UI asks before sending more.
    const terminalActivity = thread.externalAt !== null && (thread.hubTurnAt === null || thread.externalAt > thread.hubTurnAt);
    let worktree: ThreadView['worktree'] = null;
    let projectGone = cwdGone;
    if (thread.worktree) {
      let ofReal = thread.worktree.of;
      projectGone = false;
      try {
        ofReal = realpathSync(thread.worktree.of);
      } catch {
        // As above.
        projectGone = true;
      }
      worktree = { ...thread.worktree, ofReal };
    }
    const scratch = this.dirs.scratchRoot !== null && isWithinWorkdirs(thread.cwd, [this.dirs.scratchRoot]);
    const runningSince = this.running.get(thread.id)?.since ?? null;
    const modelQueueState = this.modelQueues.get(thread.id);
    const modelQueue = modelQueueState ? {
      state: modelQueueState.state,
      position: modelQueueState.position,
      ...(modelQueueState.operation !== undefined ? { operation: modelQueueState.operation } : {}),
      ...(modelQueueState.message !== undefined ? { message: modelQueueState.message } : {}),
      updatedAt: modelQueueState.updatedAt,
    } : null;
    const failed = thread.status === 'error' ? this.store.lastTurnFailure(thread.id) : null;
    const failure = failed ? { code: failed.code, message: failed.message.slice(0, 500), at: failed.at } : null;
    return { ...thread, failure, writing: thread.writing ? { ...thread.writing, habits: habitsPath() } : null, handover: thread.handover ? { ...thread.handover, mode: this.store.handoverMode(thread)!, automatic: thread.handover.mode === 'continue', sourceMoved: this.store.handoverSourceMoved(thread)! } : null, autoHandover: { ...this.autoHandover.view(thread.id), resume: this.quotaResume.view(thread.id) }, worktree, resumeCommand, cwdReal, waiting, waitingReason, queued, modelQueue, terminalActivity, scratch, projectGone, runningSince, coordination: this.coordination?.metadata(thread.id), loopx: this.loopx?.binding(thread.id) ?? null, ...this.sessionInfo(thread) };
  }

  /** A thread was just bound to a Goal: tell the pages. */
  /** The thread an agent session already is, if any (archived ones count). */
  threadOfSession(agent: string, sessionId: string): ThreadRecord | null {
    return this.store.findThreadByAgentSession(agent, sessionId);
  }

  private sessionInfo(thread: ThreadRecord): Pick<ThreadView, 'loopxInfo' | 'origin'> {
    const scanned = thread.agentSessionId ? this.sessionIndex?.(thread.agentSessionId) : undefined;
    return { loopxInfo: this.loopx?.info(thread.id, scanned?.loopx) ?? scanned?.loopx ?? null, origin: scanned?.origin ?? null };
  }

  announceLoopx(id: string): void {
    if (this.store.getThread(id)) this.emitThreadEvent(id, null, 'thread.updated', {});
  }

  /**
   * The folders the file browser may stand on, most specific first: the thread's own directory,
   * the agent's folders for this session, this machine's project directories, and the home
   * directory. Read only, like every file the hub shows. Gone folders are left out.
   */
  private browseRoots(thread: ThreadRecord): Array<{ path: string; kind: 'thread' | 'session' | 'project' | 'home' }> {
    const adapter = this.adapters.get(thread.agent);
    const named: Array<[string, 'thread' | 'session' | 'project' | 'home']> = [
      [thread.cwd, 'thread'],
      ...(thread.agentSessionId
        ? (adapter?.artifactDirs?.(thread.agentSessionId) ?? []).map((path): [string, 'session'] => [path, 'session'])
        : []),
      ...this.dirs.roots().map((path): [string, 'project'] => [path, 'project']),
      [homedir(), 'home'],
    ];
    const roots: Array<{ path: string; kind: 'thread' | 'session' | 'project' | 'home' }> = [];
    const seen = new Set<string>();
    for (const [path, kind] of named) {
      let real: string;
      try {
        real = realpathSync(path);
      } catch {
        continue;
      }
      if (seen.has(real)) continue;
      seen.add(real);
      roots.push({ path: real, kind });
    }
    return roots;
  }

  /**
   * The folders of the task itself, without the file browser's wider choice: the thread's own
   * directory and the agent's folders for this session. A borrowed tool's verified artifacts must
   * come from here, not from anywhere the browser may read.
   */
  private taskRoots(thread: ThreadRecord): Array<{ path: string; kind: 'thread' | 'session' | 'project' | 'home' }> {
    const adapter = this.adapters.get(thread.agent);
    const named: Array<[string, 'thread' | 'session']> = [
      [thread.cwd, 'thread'],
      ...(thread.agentSessionId
        ? (adapter?.artifactDirs?.(thread.agentSessionId) ?? []).map((path): [string, 'session'] => [path, 'session'])
        : []),
    ];
    const roots: Array<{ path: string; kind: 'thread' | 'session' | 'project' | 'home' }> = [];
    const seen = new Set<string>();
    for (const [path, kind] of named) {
      let real: string;
      try {
        real = realpathSync(path);
      } catch {
        continue;
      }
      if (seen.has(real)) continue;
      seen.add(real);
      roots.push({ path: real, kind });
    }
    return roots;
  }

  /**
   * Where a path the page asked about really is, if the thread may show it: inside one of the
   * folders the file browser may stand on (see browseRoots), or with `task` scope, only the
   * thread's own directory and the agent's session folders. Symlinks are resolved first, so a
   * link inside cannot point the way out. Anything else is "not found", whether it exists or not.
   * `root` is the real path of the folder it was found in, which the file browser counts from.
   */
  resolveInThread(threadId: string, rawPath: unknown, notFound: HttpError, scope: 'browse' | 'task' = 'browse'): { real: string; root: string } {
    const thread = this.getThread(threadId);
    if (rawPath !== undefined && rawPath !== null && typeof rawPath !== 'string') throw new HttpError(400, 'path must be a string');
    if (typeof rawPath === 'string' && rawPath.length > 4096) throw new HttpError(400, 'path is too long');
    const given = !rawPath ? thread.cwd : expandHome(stripFileUrl(rawPath));

    let real: string;
    try {
      real = realpathSync(isAbsolute(given) ? given : resolve(thread.cwd, given));
    } catch {
      throw notFound;
    }
    for (const root of scope === 'browse' ? this.browseRoots(thread) : this.taskRoots(thread)) {
      if (real === root.path || real.startsWith(root.path.endsWith(sep) ? root.path : root.path + sep)) return { real, root: root.path };
    }
    throw notFound;
  }

  /** The folders the file browser can stand on, for the page's root switcher. */
  threadRoots(threadId: string): { roots: Array<{ path: string; kind: 'thread' | 'session' | 'project' | 'home' }> } {
    return { roots: this.browseRoots(this.getThread(threadId)) };
  }

  /**
   * An image file the agent made or mentioned, for showing it in the thread. Only files the thread
   * may show (see resolveReadableFile), and only real images: one of the four bitmap formats, or an SVG.
   */
  getThreadImage(threadId: string, rawPath: unknown): { path: string; mimeType: string; size: number } {
    const notFound = new HttpError(404, 'no such image in this thread');
    if (typeof rawPath !== 'string' || !rawPath) throw new HttpError(400, 'path is required');
    const { real } = this.resolveReadableFile(threadId, rawPath, notFound);

    const stat = statSync(real);
    if (!stat.isFile()) throw notFound;
    // There, but not something to show as a picture: the page says so instead of "not found".
    const notImage = new HttpError(415, 'not an image this hub shows');
    if (stat.size === 0 || stat.size > MAX_SERVED_IMAGE_BYTES) throw notImage;
    let head = Buffer.alloc(SNIFF_BYTES);
    const fd = openSync(real, 'r');
    try {
      head = head.subarray(0, readSync(fd, head, 0, head.length, 0));
    } finally {
      closeSync(fd);
    }
    const mimeType = sniffImageType(head) ?? (sniffSvg(head) && stat.size <= MAX_SERVED_SVG_BYTES ? 'image/svg+xml' : null);
    if (!mimeType) throw notImage;
    return { path: real, mimeType, size: stat.size };
  }

  /**
   * Any file the thread may read (see resolveReadableFile), for downloading or playing it: its
   * real path, size and the name to save it under. Unlike the image route this puts no ceiling on
   * size and does not care what kind of file it is — a download is a download, a video streams.
   */
  getThreadDownload(threadId: string, rawPath: unknown): { path: string; mimeType: string; size: number; name: string } {
    const notFound = new HttpError(404, 'no such file in this thread');
    if (typeof rawPath !== 'string' || !rawPath) throw new HttpError(400, 'path is required');
    const { real } = this.resolveReadableFile(threadId, rawPath, notFound);
    const stat = statSync(real);
    if (!stat.isFile()) throw notFound;
    return { path: real, mimeType: downloadType(real), size: stat.size, name: basename(real) };
  }

  /** One folder of the thread's directory, for the file browser. No path: the thread's directory itself. */
  async listThreadDir(threadId: string, rawPath: unknown): Promise<{ root: string; path: string } & Listing> {
    const { real, root } = this.resolveInThread(threadId, rawPath, new HttpError(404, 'no such folder in this thread'));
    if (!statSync(real).isDirectory()) throw new HttpError(404, 'no such folder in this thread');
    return { root, path: real, ...(await listDir(real)) };
  }

  /** Files under the thread's directory (or another browse root, when `rawPath` names one) whose path matches a search, relative to it. */
  async findThreadFiles(threadId: string, query: unknown, rawPath?: unknown): Promise<{ root: string; paths: string[]; truncated: boolean }> {
    if (typeof query !== 'string' || query.length > 200) throw new HttpError(400, 'q must be a short string');
    const { real } = this.resolveInThread(threadId, rawPath ?? null, new HttpError(404, 'the thread directory is gone'));
    return { root: real, ...(await findFiles(real, query)) };
  }

  /** A text file in the thread's directory, for the file viewer. A binary one comes back without text. */
  /** One file of the thread's directory (or an artifact dir), resolved and checked, for previews. */
  threadFile(threadId: string, rawPath: unknown): { real: string; rel: string } {
    const notFound = new HttpError(404, 'no such file in this thread');
    const { real, root } = this.resolveInThread(threadId, rawPath, notFound);
    if (!statSync(real).isFile()) throw notFound;
    return { real, rel: real === root ? '' : real.slice(root.length + 1) };
  }

  readThreadText(threadId: string, rawPath: unknown): { root: string; path: string; hash: string | null; editable: boolean } & TextFile {
    const notFound = new HttpError(404, 'no such file in this thread');
    if (typeof rawPath !== 'string' || !rawPath) throw new HttpError(400, 'path is required');
    // The person's notes open from any thread, editable; anything else as the file viewer allows.
    const note = isAbsolute(rawPath) || rawPath.startsWith('file://') ? noteFile(rawPath) : null;
    if (note) {
      this.getThread(threadId);
      const file = readText(note);
      const whole = file.text !== null && !file.truncated;
      return { root: this.threadView(threadId).cwdReal, path: note, ...file, editable: whole };
    }
    const { real, root } = this.resolveReadableFile(threadId, rawPath, notFound);
    if (!statSync(real).isFile()) throw notFound;
    const file = readText(real);
    // A whole text file gets a hash, so an edit can tell whether the file moved on under it.
    const whole = file.text !== null && !file.truncated;
    return { root, path: real, ...file, editable: whole && isEditableArticle(real, this.threadView(threadId).cwdReal) };
  }

  /** Starts writing mode, or moves it to another stage. Takes effect from the next message. */
  setWriting(threadId: string, stage: unknown): ThreadView {
    if (!isWritingStage(stage)) throw new HttpError(400, 'stage must be one of draft, second, third, figures, polish');
    const thread = this.getThread(threadId);
    const writing: WritingState = thread.writing
      ? { ...thread.writing, stage }
      : { stage, file: newestArticle(this.threadView(threadId).cwdReal) };
    this.store.setThreadWriting(threadId, writing);
    this.emitThreadEvent(threadId, null, 'thread.updated', {});
    return this.threadView(threadId);
  }

  /** Ends writing mode. The article stays where it is. */
  endWriting(threadId: string): ThreadView {
    this.getThread(threadId);
    this.store.setThreadWriting(threadId, null);
    this.emitThreadEvent(threadId, null, 'thread.updated', {});
    return this.threadView(threadId);
  }

  /**
   * The person's own edit of an article: only a `.md` under `writing/` in the thread's directory,
   * and only over the version they opened. The agent hears about it with its next message.
   */
  saveWritingText(threadId: string, rawPath: unknown, text: unknown, baseHash: unknown): { hash: string } {
    const notFound = new HttpError(404, 'no such file in this thread');
    if (typeof rawPath !== 'string' || !rawPath) throw new HttpError(400, 'path is required');
    const note = isAbsolute(rawPath) || rawPath.startsWith('file://') ? noteFile(rawPath) : null;
    if (note) {
      this.getThread(threadId);
      return saveArticle(note, text, baseHash);
    }
    const { real } = this.resolveInThread(threadId, rawPath, notFound);
    if (!statSync(real).isFile()) throw notFound;
    const cwdReal = this.threadView(threadId).cwdReal;
    if (!isEditableArticle(real, cwdReal)) throw new HttpError(403, 'only Markdown files under writing/ can be edited');
    const saved = saveArticle(real, text, baseHash);
    const writing = this.getThread(threadId).writing;
    if (writing) {
      this.store.setThreadWriting(threadId, { ...writing, file: real.slice(cwdReal.length + 1).split(sep).join('/'), edited: true });
      this.emitThreadEvent(threadId, null, 'thread.updated', {});
    }
    return saved;
  }

  /**
   * Beyond the thread's directory, any file this thread's completed answers name by absolute path
   * is readable on its own. A file that is not there is 404; one that is but was never named, 403,
   * so the page can tell a missing file from one it may not show.
   */
  private resolveReadableFile(threadId: string, rawPath: string, notFound: HttpError): { real: string; root: string } {
    const note = isAbsolute(rawPath) || rawPath.startsWith('file://') ? noteFile(rawPath) : null;
    if (note) return { real: note, root: this.threadView(threadId).cwdReal };
    try {
      return this.resolveInThread(threadId, rawPath, notFound);
    } catch (error) {
      if (error !== notFound) throw error;
    }
    let given = expandHome(stripFileUrl(rawPath));
    const path = resolve(this.getThread(threadId).cwd, given);
    const real = referencedFile(path);
    if (!real) throw notFound;
    if (!isAbsolute(given) || !this.store.referencesFile(threadId, path)) throw new HttpError(403, 'no answer in this thread names this file');
    // Keep the browser rooted in the project; the grant is only for this file, not its folder.
    return { real, root: this.threadView(threadId).cwdReal };
  }

  listSections(): SectionRecord[] {
    return this.store.listSections();
  }

  createSection(name: unknown): SectionRecord {
    return this.store.createSection(randomUUID(), this.sectionName(name));
  }

  renameSection(id: string, name: unknown): SectionRecord {
    if (!this.store.getSection(id)) throw new HttpError(404, 'section not found');
    this.store.renameSection(id, this.sectionName(name));
    return this.store.getSection(id)!;
  }

  /** Deletes a section. Its threads stay and are listed under their project again. */
  deleteSection(id: string): void {
    if (!this.store.getSection(id)) throw new HttpError(404, 'section not found');
    this.store.deleteSection(id);
  }

  private sectionName(name: unknown): string {
    const value = typeof name === 'string' ? name.trim() : '';
    if (!value) throw new HttpError(400, 'section name is empty');
    if (value.length > 40) throw new HttpError(400, 'section name is longer than 40 characters');
    return value;
  }

  quiet(id: string): { turnId: string } {
    const turnId = this.running.get(id)?.turnId;
    if (!turnId) throw new HttpError(409, 'quiet requires a running turn');
    this.quietTurns.add(turnId);
    return { turnId };
  }

  notice(id: string, text: unknown, confirmation = false, proactive = false): StoredEvent {
    this.getThread(id);
    if (typeof text !== 'string' || !text.trim() || [...text].length > 500) throw new HttpError(400, 'notice text must contain 1–500 characters');
    this.personal.review.recordNotice(id, this.running.get(id)?.turnId ?? null);
    this.store.updateThread(id, { unread: true });
    const event = this.store.appendEvent(id, this.running.get(id)?.turnId ?? null, 'agent.notice', { text, ...(confirmation ? { confirmation: true } : {}), ...(proactive ? { proactive: true } : {}) });
    for (const listener of this.listeners) listener(event);
    this.emitThreadEvent(id, null, 'thread.updated', {});
    return event;
  }

  // ---------- Plan page (todos) ----------

  /** Todos with their linked threads as full views, so chips can show agent, title and state. */
  listTodos(): Array<TodoRecord & { urgency: TodoUrgency; proposals: TodoProposal[]; threads: ThreadView[] }> {
    return this.store.listTodos().map((todo) => ({
      ...todo,
      urgency: urgencyOf(todo),
      proposals: this.store.todoProposals(todo.id).filter(p => p.state === 'open'),
      threads: this.store
        .todoThreadIds(todo.id)
        .map((id) => this.store.getThread(id))
        .filter((t): t is ThreadRecord => t !== null)
        .map((t) => this.view(t)),
    }));
  }

  createTodo(text: unknown, parentId?: unknown, fields: Record<string, unknown> = {}): TodoRecord {
    return this.personal.create({ ...fields, text, parentId });
  }

  updateTodo(id: string, patch: Record<string, unknown>): TodoRecord {
    return this.personal.update(id, patch);
  }

  deleteTodo(id: string, rev?: unknown): void {
    this.store.checkTodoRev(id, rev === undefined ? this.store.getTodo(id)?.rev : rev);
    this.store.deleteTodo(id);
    this.personal.mirrorTodoRemoval(id);
    this.broadcastTodo();
  }

  /**
   * Moves an item: reordering among its siblings, a plan becoming a sub-item of another (Tab), or a
   * sub-item coming back to the top level (Shift+Tab). Still one level: a plan with sub-items of its
   * own cannot go under another.
   */
  moveTodo(id: string, target: { parentId?: unknown; before?: unknown; rev?: unknown }): void {
    const todo = this.store.checkTodoRev(id, target.rev === undefined ? this.store.getTodo(id)?.rev : target.rev);
    const parent = target.parentId == null ? null : this.todoParent(target.parentId);
    if (parent?.id === id) throw new HttpError(400, '不能放到自己下面');
    if (parent && this.store.listTodos().some((t) => t.parentId === id)) throw new HttpError(400, '有子项的计划不能变成子项');
    let before: string | null = null;
    if (target.before != null) {
      if (typeof target.before !== 'string') throw new HttpError(400, 'before must be a string');
      const sibling = this.store.getTodo(target.before);
      if (!sibling || sibling.parentId !== (parent?.id ?? null)) throw new HttpError(400, 'before is not in that list');
      if (sibling.id !== id) before = sibling.id;
    }
    this.store.moveTodo(id, parent?.id ?? null, before);
    this.broadcastTodo();
  }

  linkTodoThread(id: string, threadId: unknown, rev?: unknown): void {
    this.store.checkTodoRev(id, rev === undefined ? this.store.getTodo(id)?.rev : rev);
    this.getThread(this.threadId(threadId));
    this.store.linkTodoThread(id, this.threadId(threadId));
    this.store.updateTodo(id, {});
    this.broadcastTodo();
  }

  unlinkTodoThread(id: string, threadId: unknown, rev?: unknown): void {
    this.store.checkTodoRev(id, rev === undefined ? this.store.getTodo(id)?.rev : rev);
    this.store.unlinkTodoThread(id, this.threadId(threadId));
    this.store.updateTodo(id, {});
    this.broadcastTodo();
  }

  /** A todo's parent must exist and be top level: plans have children, children do not. */
  private todoParent(parentId: unknown): TodoRecord {
    if (typeof parentId !== 'string') throw new HttpError(400, 'parentId must be a string');
    const parent = this.store.getTodo(parentId);
    if (!parent) throw new HttpError(404, 'todo not found');
    if (parent.parentId) throw new HttpError(400, '子项下不能再建子项');
    return parent;
  }

  private threadId(threadId: unknown): string {
    if (typeof threadId !== 'string' || !threadId) throw new HttpError(400, 'threadId is required');
    return threadId;
  }

  /** Plan-page changes are not thread events: they go to listeners only, without an event log. */
  broadcastTodo(): void {
    const ev: StoredEvent = { threadId: '', seq: 0, turnId: null, type: 'todo.updated', ts: new Date().toISOString(), data: {} };
    for (const l of this.listeners) l(ev);
  }

  getThread(id: string): ThreadRecord {
    const t = this.store.getThread(id);
    if (!t) throw new HttpError(404, 'thread not found');
    return t;
  }

  async updateThread(
    id: string,
    updates: {
      /** Only an explicit composer edit changes defaults for future threads. */
      rememberModelChoice?: boolean;
      title?: string;
      pinned?: boolean;
      workMode?: WorkMode;
      speed?: string | null;
      permissionMode?: string;
      model?: string | null;
      effort?: string | null;
      archived?: boolean;
      sectionId?: string | null;
      unread?: boolean;
      done?: boolean;
      /** The person places the thread on the board; it stands until a later turn reports again. */
      stage?: ProgressStage;
    },
  ): Promise<ThreadView> {
    const thread = this.getThread(id);
    const rememberChoice = updates.rememberModelChoice === true &&
      (updates.model !== undefined || updates.effort !== undefined || updates.speed !== undefined);
    if (rememberChoice && updates.model !== undefined) {
      const saved = applyModelPreference((await this.draftModelPreferences())[thread.agent], { model: updates.model });
      updates = {
        ...updates,
        effort: updates.effort !== undefined ? updates.effort : saved.effort === null ? null : await this.validateEffortOrNull(thread.agent, updates.model, saved.effort),
        speed: updates.speed !== undefined ? updates.speed : saved.speed === null ? null : await this.validateSpeedOrNull(thread.agent, updates.model, saved.speed),
      };
    }
    const patch: Record<string, unknown> = {};
    if (updates.done !== undefined) {
      if (typeof updates.done !== 'boolean') throw new HttpError(400, 'done must be true or false');
      // The person's word; whatever the agent said before no longer stands.
      this.store.setThreadProgress(id, { doneAt: updates.done ? (thread.doneAt ?? new Date().toISOString()) : null, report: null });
    }
    if (updates.stage !== undefined) {
      if (updates.done !== undefined) throw new HttpError(400, 'set done or stage, not both');
      if (!PROGRESS_STAGES.includes(updates.stage)) throw new HttpError(400, 'stage must be backlog, doing, blocked or review');
      this.store.setThreadProgress(id, { doneAt: null, report: { stage: updates.stage, note: '', at: new Date().toISOString(), by: 'user' } });
    }
    if (updates.speed !== undefined) {
      patch.speed = updates.speed === null ? (thread.speed ? 'default' : null) : await this.validateSpeed(thread.agent, updates.model === undefined ? thread.model : updates.model, updates.speed);
    } else if (updates.model !== undefined && thread.speed) {
      const adapter = this.adapters.get(thread.agent)!;
      const option = await this.modelOption(adapter, updates.model);
      if (!option?.speeds?.some(s => s.id === thread.speed)) patch.speed = 'default';
    }
    if (updates.title !== undefined) patch.title = String(updates.title).trim() || thread.title;
    if (updates.pinned !== undefined) patch.pinned = Boolean(updates.pinned);
    if (updates.sectionId !== undefined) {
      if (updates.sectionId !== null && !this.store.getSection(updates.sectionId)) {
        throw new HttpError(400, 'section not found');
      }
      patch.sectionId = updates.sectionId;
    }
    if (updates.archived !== undefined) {
      if (updates.archived && this.running.has(id)) {
        throw new HttpError(409, 'stop the running turn before archiving this thread');
      }
      // Archiving stops the agent process. The record and the agent's session stay, so it can come back.
      if (updates.archived) await this.dropSession(id);
      patch.archivedAt = updates.archived ? (thread.archivedAt ?? new Date().toISOString()) : null;
    }
    if (updates.unread !== undefined) {
      if (typeof updates.unread !== 'boolean') throw new HttpError(400, 'unread must be true or false');
      patch.unread = updates.unread;
    }
    if (updates.workMode !== undefined) {
      const mode = this.validateWorkMode(thread.agent, updates.workMode);
      if (this.running.has(id) || this.changingModes.has(id)) throw new HttpError(409, '请等待当前轮结束后切换模式');
      if (updates.permissionMode !== undefined || updates.model !== undefined || updates.effort !== undefined || updates.speed !== undefined) throw new HttpError(400, '请单独修改工作模式');
      this.changingModes.add(id);
      try {
        const session = this.sessions.get(id);
        if (session?.setWorkMode) await session.setWorkMode(mode);
        else if (session) await this.dropSession(id);
        this.store.updateThread(id, { workMode: mode });
      } finally { this.changingModes.delete(id); }
      patch.workMode = mode;
    }
    if (updates.permissionMode !== undefined) {
      if (this.loopx?.binding(id)) throw new HttpError(400, 'LoopX Goal 会话的权限在创建时确定；要换档位请新建会话');
      const mode = this.validatePermissionMode(thread.agent, updates.permissionMode);
      patch.permissionMode = mode;
      // A live session switches now; otherwise the next session starts with the new level.
      const session = this.sessions.get(id);
      if (session?.setPermissionMode) await session.setPermissionMode(mode);
    }
    if (updates.model !== undefined) {
      if (updates.model === null) {
        // Back to the agent's own default: only a restart can do that.
        patch.model = null;
        await this.restartForChange(thread, 'model');
      } else {
        const model = await this.validateModel(thread.agent, updates.model);
        patch.model = model;
        await this.applyLive(thread, 'model', (s) => s.setModel?.(model) ?? Promise.resolve(false));
      }
    }
    // Effort is checked against the model the thread will have after this update.
    const nextModel = patch.model !== undefined ? (patch.model as string | null) : thread.model;
    if (updates.effort !== undefined) {
      if (updates.effort === null) {
        patch.effort = null;
        await this.restartForChange(thread, 'effort');
      } else {
        const effort = await this.validateEffort(thread.agent, nextModel, updates.effort);
        patch.effort = effort;
        await this.applyLive(thread, 'effort', (s) => s.setEffort?.(effort) ?? Promise.resolve(false));
      }
    } else if (patch.model !== undefined && thread.effort) {
      // The model changed but the effort was left alone: drop it if the new model cannot take it.
      const supported = await this.effortsFor(thread.agent, nextModel);
      if (!supported.includes(thread.effort as EffortLevel)) {
        patch.effort = null;
        await this.restartForChange(thread, 'effort');
      }
    }
    if (patch.speed !== undefined) {
      await this.applyLive(thread, 'speed', s => s.setSpeed?.(patch.speed as string | null) ?? Promise.resolve(false));
    }
    this.store.updateThread(id, patch as any);
    if (rememberChoice) {
      const chosen = this.getThread(id);
      this.rememberModelPreference(thread.agent, { model: chosen.model, effort: chosen.effort, speed: chosen.speed ?? null });
    }
    // The person named it: nothing renames it for them again.
    if (patch.title !== undefined && patch.title !== thread.title) this.store.setTitleBy(id, 'user');
    this.emitThreadEvent(id, null, 'thread.updated', {});
    return this.threadView(id);
  }

  /**
   * Applies a model or effort change. If the live session cannot change it in place, the session is
   * closed and the next turn resumes the same agent session with the new setting. A turn in flight
   * is never interrupted: the change is remembered and the session is refreshed for the next turn,
   * the way Claude Code's /model takes effect for the next message.
   */
  private async applyLive(
    thread: ThreadRecord,
    what: 'model' | 'effort' | 'speed',
    apply: (s: AgentSession) => Promise<boolean>,
  ): Promise<void> {
    const session = this.sessions.get(thread.id);
    if (!session) return;
    const inPlace = await apply(session).catch(() => false);
    if (inPlace) return;
    if (this.running.has(thread.id)) {
      this.refreshAfterTurn.add(thread.id);
      return;
    }
    await this.dropSession(thread.id);
  }

  private async restartForChange(thread: ThreadRecord, what: 'model' | 'effort'): Promise<void> {
    if (!this.sessions.has(thread.id)) return;
    if (this.running.has(thread.id)) {
      this.refreshAfterTurn.add(thread.id);
      return;
    }
    await this.dropSession(thread.id);
  }

  /**
   * Deletes a thread, its events and its images for good. A running turn must be cancelled first.
   * With `agentSession`, the agent's own copy of the conversation is removed too, where the agent
   * supports that. Without it, the conversation can still be continued from a terminal.
   *
   * With `worktree`, the git worktree the thread stands in goes too, branch included. Work that
   * exists nowhere else (files not committed, commits no other branch has) stops this unless
   * `force` says the user has seen the count and means it.
   */
  async deleteThread(id: string, opts: { agentSession?: boolean; worktree?: boolean; force?: boolean } = {}): Promise<void> {
    const thread = this.getThread(id);
    if (this.running.has(id)) throw new HttpError(409, 'cancel the running turn before deleting this thread');
    if (this.coordination?.metadata(id)) throw new HttpError(409, '协作对话请先归档，保留成员关系与消息记录');
    let doomed: { main: string; tree: ThreadWorktree } | null = null;
    if (opts.worktree) {
      const tree = thread.worktree;
      if (!tree) throw new HttpError(400, 'this thread has no worktree');
      const others = [...this.store.listThreads(), ...this.store.listThreads({ archived: true })];
      if (others.some((t) => t.id !== id && t.worktree && samePath(t.worktree.root, tree.root))) {
        throw new HttpError(409, 'another thread still runs in this worktree', { code: 'worktree_shared' });
      }
      // `<repository>/.worktrees/<name>`: the repository is two levels up.
      const main = dirname(dirname(tree.root));
      const left = await worktreeLeftovers(main, tree);
      if ((left.changes > 0 || left.commits > 0) && !opts.force) {
        throw new HttpError(409, 'the worktree holds work that exists nowhere else', { code: 'worktree_dirty', ...left });
      }
      doomed = { main, tree };
    }
    await this.dropSession(id);
    this.quotaResume.drop(id);
    if (opts.agentSession && thread.agentSessionId) {
      const adapter = this.adapters.get(thread.agent);
      if (!adapter?.capabilities.deleteSession || !adapter.deleteSession) {
        throw new HttpError(400, `${thread.agent} sessions cannot be deleted from here`);
      }
      try {
        await adapter.deleteSession(thread.agentSessionId, thread.cwd);
      } catch (err) {
        // Nothing has been removed yet, so the user can retry or delete without this option.
        throw new HttpError(502, `could not delete the ${thread.agent} session: ${err instanceof Error ? err.message : err}`);
      }
    }
    if (doomed) {
      try {
        await removeWorktree(doomed.main, doomed.tree);
      } catch (err) {
        throw new HttpError(502, `could not remove the worktree: ${err instanceof Error ? err.message : err}`);
      }
    }
    this.emitThreadEvent(id, null, 'thread.updated', {});
    this.queues.delete(id);
    this.modelQueues.delete(id);
    this.goals.delete(id);
    this.store.deleteThread(id);
    this.attachments.removeThread(id);
  }

  /** Model ids come from the adapter's own list, so a stale or invented id is rejected early. */
  private async validateModel(agent: string, model: string): Promise<string> {
    const adapter = this.adapters.get(agent);
    if (!adapter) throw new HttpError(400, `unknown agent: ${agent}`);
    const options: ModelOption[] = await this.adapterModels(adapter);
    if (!options.some((o) => o.id === model)) throw new HttpError(400, `unknown model for ${agent}: ${model}`);
    return model;
  }

  /**
   * Effort levels a model accepts. `model` null is the agent default. When the agent does not
   * report per-model levels, the agent-wide list applies.
   */
  private async effortsFor(agent: string, model: string | null): Promise<readonly EffortLevel[]> {
    const adapter = this.adapters.get(agent);
    if (!adapter) return [];
    const option = await this.modelOption(adapter, model);
    return option?.efforts ?? adapter.capabilities.effortLevels;
  }

  /**
   * What the agent reports about a model. For `null` (no override) this is the agent's default:
   * an entry with the id "default" if the agent has one, else the entry it marks as its default.
   */
  private async modelOption(adapter: AgentAdapter, model: string | null): Promise<ModelOption | undefined> {
    const options = await this.adapterModels(adapter);
    if (model !== null) return options.find((o) => o.id === model);
    return options.find((o) => o.id === 'default') ?? options.find((o) => o.isDefault);
  }

  private async validateSpeed(agent: string, model: string | null, speed: unknown): Promise<string> {
    const adapter = this.adapters.get(agent);
    const option = adapter?.capabilities.speed ? await this.modelOption(adapter, model) : undefined;
    if (adapter?.capabilities.speed && speed === 'default') return speed;
    if (typeof speed !== 'string' || !option?.speeds?.some(s => s.id === speed)) throw new HttpError(400, '当前模型不支持此速度');
    return speed;
  }

  private async validateEffort(agent: string, model: string | null, effort: string): Promise<EffortLevel> {
    const supported = await this.effortsFor(agent, model);
    if (supported.length === 0) {
      throw new HttpError(400, `${model ?? agent} has no thinking effort setting`);
    }
    if (!(EFFORT_LEVELS as readonly string[]).includes(effort) || !supported.includes(effort as EffortLevel)) {
      throw new HttpError(400, `unsupported effort for ${model ?? agent}: ${effort}`);
    }
    return effort as EffortLevel;
  }

  /** A Goal conversation runs under the Goal's own permission, not one of the three levels; codex enforces it with its sandbox. */
  private sessionPermission(thread: ThreadRecord, adapter: AgentAdapter): PermissionMode {
    const goal = this.loopx?.binding(thread.id);
    if (goal && adapter.id === 'codex') return `loopx_${goal.permission}` as PermissionMode;
    return thread.permissionMode as PermissionMode;
  }

  /** Checks a permission level against what the thread's agent supports. */
  private validatePermissionMode(agent: string, mode: string): PermissionMode {
    const adapter = this.adapters.get(agent);
    const supported = adapter?.capabilities.permissionModes ?? [];
    if (supported.length === 0) throw new HttpError(400, `${agent} has no permission modes`);
    if (!(PERMISSION_MODES as readonly string[]).includes(mode) || !supported.includes(mode as PermissionMode)) {
      throw new HttpError(400, `unsupported permission mode for ${agent}: ${mode}`);
    }
    return mode as PermissionMode;
  }

  listEvents(threadId: string, afterSeq: number): StoredEvent[] {
    this.getThread(threadId);
    return this.store.listEvents(threadId, afterSeq);
  }

  history(threadId: string) {
    this.getThread(threadId);
    return this.store.history;
  }

  /**
   * Everything after a sequence number, however much that is: what a stream replays before it
   * goes live. Stopping at a page size here would leave a hole between the replay and the live
   * events that nothing fills in later.
   */
  listEventsSince(threadId: string, afterSeq: number): StoredEvent[] {
    this.getThread(threadId);
    return compactDeltas(coalesceToolEvents(this.store.iterateEvents(threadId, afterSeq)));
  }

  /** How far back, in stored events, one page of history reaches before stopping at a turn's start. */
  private static readonly PAGE_BUDGET = 2_000;

  /**
   * One page of a thread's history for the view: the newest turns, or the ones just before
   * `beforeSeq`. Pages begin where a turn begins; see Store.listEventsPage.
   */
  listEventsPage(threadId: string, beforeSeq?: number): StoredEvent[] {
    this.getThread(threadId);
    return compactDeltas(coalesceToolEvents(this.store.iterateEventsPage(threadId, ThreadManager.PAGE_BUDGET, beforeSeq)));
  }

  /**
   * Run once when the daemon starts. A thread still marked running was cut off when the last
   * daemon stopped without ending its turn; nothing is running now. The open turn gets an ending,
   * so the thread reads as interrupted instead of working forever.
   */
  recoverInterrupted(): void {
    for (const thread of [...this.store.listThreads(), ...this.store.listThreads({ archived: true })]) {
      if (thread.status !== 'running') continue;
      const turnId = this.store.openTurn(thread.id);
      if (turnId) {
        this.emitThreadEvent(thread.id, turnId, 'turn.error', {
          message: 'The hub restarted while this turn was running.',
          code: 'interrupted',
        });
      }
      this.store.updateThread(thread.id, { status: turnId ? 'error' : 'idle' });
    }
  }

  /** How many hub-only turns may be skipped while matching the agent's history. See syncThread. */
  private static readonly SYNC_SKIP_TOLERANCE = 3;

  /**
   * Brings a thread up to date with what the terminal did to its session: the agent's history is
   * read (without touching the conversation), matched against the turns the hub knows, and the
   * turns that are new here are appended, marked as coming from the terminal.
   *
   * Matching walks both lists from the start. A hub turn the agent never recorded (cancelled before
   * it got there) is skipped, up to a small tolerance; anything the agent has beyond that is new.
   * When the lists disagree beyond the tolerance nothing is written: better to miss an update than
   * to append a wrong one.
   */
  async syncThread(id: string): Promise<{ added: number; terminalActivity: boolean }> {
    const thread = this.getThread(id);
    const adapter = this.adapters.get(thread.agent);
    if (!thread.agentSessionId || !adapter?.capabilities.history || !adapter.loadHistory) {
      throw new HttpError(400, `this thread's session cannot be read back`);
    }
    // A running turn is writing to the same session; syncing now would race it.
    if (this.running.has(id)) return { added: 0, terminalActivity: this.view(thread).terminalActivity };
    // The UI polls and anything else may sync too; two readers would both append the same turns.
    if (this.syncing.has(id)) return { added: 0, terminalActivity: this.view(thread).terminalActivity };
    this.syncing.add(id);
    try {
      return await this.doSync(thread);
    } finally {
      this.syncing.delete(id);
    }
  }

  private async doSync(thread: ThreadRecord): Promise<{ added: number; terminalActivity: boolean }> {
    const { id } = thread;
    const adapter = this.adapters.get(thread.agent);
    if (!thread.agentSessionId || !adapter?.capabilities.history || !adapter.loadHistory) {
      throw new HttpError(400, `this thread's session cannot be read back`);
    }

    const agentTurns = await adapter.loadHistory(thread.agentSessionId, thread.cwd);
    // Reading the history takes a while. A turn the hub started meanwhile is already in the agent's
    // session, so the hub's turns are read after it, and a turn still running is left for later.
    if (this.running.has(id)) return { added: 0, terminalActivity: this.view(thread).terminalActivity };
    // Every turn the hub has, not one page of the log: a turn missing from this list would look
    // new on the agent's side and be appended again on every sync.
    const hubTexts = this.store.listEventsOfType(id, 'turn.started').map((ev) => turnKey(String(ev.data.text ?? '')));

    const matched = matchTurns(agentTurns.map((t) => turnKey(t.text)), hubTexts, ThreadManager.SYNC_SKIP_TOLERANCE);
    if (!matched) return { added: 0, terminalActivity: this.view(thread).terminalActivity }; // histories disagree
    const fresh = agentTurns.slice(matched.agentNext);
    if (fresh.length === 0) return { added: 0, terminalActivity: this.view(thread).terminalActivity };

    const events: StoredEvent[] = [];
    const images = fresh.map((turn) => {
      if (turn.images.length === 0) return [];
      try {
        return this.attachments.save(id, turn.images.slice(0, MAX_IMAGES)).stored;
      } catch {
        return [];
      }
    });
    this.store.transaction(() => {
      fresh.forEach((turn, i) => {
        const turnId = randomUUID();
        const add = (type: EventType, data: Record<string, unknown>) => events.push(this.store.appendEvent(id, turnId, type, data));
        add('turn.started', { text: turn.text, fromTerminal: true, ...(images[i].length ? { images: images[i] } : {}) });
        for (const ev of turn.events) add(ev.type, ev.data);
        add('turn.done', { stopReason: 'end_turn', fromTerminal: true });
      });
      this.store.updateThread(id, { externalAt: new Date().toISOString() }, { touch: true });
    });
    for (const ev of events) for (const l of this.listeners) l(ev);
    this.emitThreadEvent(id, null, 'thread.updated', {});
    return { added: fresh.length, terminalActivity: true };
  }

  private validateWorkMode(agent: string, value: unknown): WorkMode {
    if (value !== 'execute' && value !== 'plan') throw new HttpError(400, '工作模式无效');
    if (value === 'plan' && !this.adapters.get(agent)?.capabilities.workModes?.includes('plan')) throw new HttpError(400, '此智能体不支持计划模式');
    return value;
  }

  async createThread(input: {
    workMode?: WorkMode;
    speed?: string | null;
    agent: string;
    cwd: string;
    title?: string;
    permissionMode?: string;
    model?: string | null;
    effort?: string | null;
    sectionId?: string | null;
    /** Start in a new git worktree of the directory's repository, under `.worktrees/`. */
    worktree?: boolean;
    /** In no project: the thread gets a fresh folder of its own in the hub's scratch area; `cwd` is ignored. */
    scratch?: boolean;
    /** The scheduled task that starts this thread, if any: shown as its badge. */
    schedule?: { id: string; name: string } | null;
  }, inheritedWorktree?: ThreadWorktree | null, handover?: ThreadRecord['handover']): Promise<ThreadView> {
    const adapter = this.adapters.get(input.agent);
    if (!adapter) throw new HttpError(400, `unknown agent: ${input.agent}`);
    const id = randomUUID();
    let cwd: string;
    if (input.scratch === true) {
      if (!this.dirs.scratchRoot) throw new HttpError(400, 'this hub has no scratch area for threads outside a project');
      cwd = join(this.dirs.scratchRoot, `${new Date().toISOString().slice(0, 10)}-${id.slice(0, 8)}`);
      mkdirSync(cwd, { recursive: true });
    } else {
      cwd = resolve(input.cwd);
    }
    // An allowed directory or anything inside one. The directory picker offers subdirectories.
    if (!isWithinWorkdirs(cwd, this.workdirs)) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    if (!existsSync(cwd) || !statSync(cwd).isDirectory()) throw new HttpError(400, 'cwd does not exist');
    if (input.sectionId !== undefined && input.sectionId !== null && !this.store.getSection(input.sectionId)) {
      throw new HttpError(400, 'section not found');
    }
    // Default for new threads: the agent's own, else auto-approve edits and still ask before other actions.
    const permissionMode =
      adapter.capabilities.permissionModes.length === 0
        ? 'auto_edit'
        : this.validatePermissionMode(input.agent, input.permissionMode ?? adapter.capabilities.defaultPermissionMode ?? 'auto_edit');
    const workMode = this.validateWorkMode(input.agent, input.workMode ?? 'execute');
    const model = input.model ? await this.validateModel(input.agent, input.model) : null;
    const effort = input.effort ? await this.validateEffort(input.agent, model, input.effort) : null;
    const speed = input.speed == null ? null : await this.validateSpeed(input.agent, model, input.speed);
    const title = (input.title ?? '').trim() || 'New thread';
    // A new thread has no conversation to move, so any agent can start in a worktree.
    let added: { main: string; tree: Worktree; cwd: string } | null = null;
    if (input.worktree === true) {
      const plan = await this.checkoutFor(cwd);
      if (!plan.checkout) throw new HttpError(400, WORKTREE_REFUSALS[plan.reason]);
      const tree = await addWorktree(plan.checkout, id.slice(0, 8)).catch((err) => {
        throw new HttpError(500, `could not add a git worktree: ${err instanceof Error ? err.message : err}`);
      });
      added = { main: plan.checkout.main, tree, cwd: plan.checkout.prefix ? join(tree.root, plan.checkout.prefix) : tree.root };
      // `.worktrees` could be a link to somewhere else; where it really is still has to be allowed.
      if (!isWithinWorkdirs(added.cwd, this.workdirs)) {
        await removeWorktree(added.main, tree).catch(() => undefined);
        throw new HttpError(400, WORKTREE_REFUSALS.outside_workdirs);
      }
    }
    let thread: ThreadRecord;
    try {
      thread = this.store.createThread({
        id,
        agent: input.agent,
        cwd: added?.cwd ?? cwd,
        title,
        permissionMode,
        workMode,
        speed,
        model,
        effort,
        handover,
        schedule: input.schedule ?? null,
        ...(input.sectionId ? { sectionId: input.sectionId } : {}),
        ...(added ? { worktree: { root: added.tree.root, branch: added.tree.branch, of: cwd } } : inheritedWorktree ? { worktree: inheritedWorktree } : {}),
      });
    } catch (err) {
      if (added) await removeWorktree(added.main, added.tree).catch(() => undefined);
      throw err;
    }
    this.emitThreadEvent(thread.id, null, 'thread.created', { agent: thread.agent, cwd: thread.cwd });
    return this.view(thread);
  }

  /**
   * Restore one segment as an independent conversation. Its immediate automatic successors split
   * off together: leaving any one attached would still make the source read-only. When called on a
   * leaf, its incoming continuation is split instead. Downstream successor chains stay untouched.
   */
  detachAutomaticHandover(threadId: string): { detached: string[]; thread: ThreadView } {
    const selected = this.getThread(threadId);
    const all = [...this.store.listThreads(), ...this.store.listThreads({ archived: true })];
    let source = selected;
    let detached = all.filter(thread => thread.handover?.sourceId === selected.id
      && thread.handover.mode === 'continue' && this.store.handoverMode(thread) === 'continue');
    if (!detached.length && selected.handover?.mode === 'continue' && this.store.handoverMode(selected) === 'continue') {
      source = this.getThread(selected.handover.sourceId);
      detached = all.filter(thread => thread.handover?.sourceId === source.id
        && thread.handover.mode === 'continue' && this.store.handoverMode(thread) === 'continue');
    }
    if (!detached.length) throw new HttpError(409, 'this handover is already independent');
    this.autoHandover.setOverride(source.id, false);
    for (const thread of detached) this.store.detachHandover(thread.id);
    const currentMember = detached.find(thread => this.coordination?.metadata(thread.id) && !this.coordination.metadata(thread.id)?.retired);
    if (this.coordination?.metadata(source.id)?.retired && currentMember) {
      this.coordination.detachHandoverMember(source.id, currentMember.id);
    }
    for (const thread of [source, ...detached]) this.emitThreadEvent(thread.id, null, 'thread.updated', {});
    return { detached: detached.map(thread => thread.id), thread: this.threadView(threadId) };
  }

  /** A fresh conversation in the source's exact directory, sharing its existing worktree. No agent is started. */
  async createHandoverThread(threadId: string, agent: string, model?: string, mode?: unknown, method?: HandoverLink['method']): Promise<ThreadView> {
    const source = this.getThread(threadId);
    if (mode !== undefined && mode !== 'continue' && mode !== 'branch') throw new HttpError(400, 'invalid handover mode');
    // Whoever takes over from here, the work leaves this thread; stop waiting for its quota.
    this.quotaResume.drop(threadId);
    const selected = mode ?? 'branch';
    // A relay between accounts of the same agent keeps the thread's model and effort: without this
    // the successor falls back to the target account's default (opus, say) and the swap quietly
    // multiplies the burn and the cache rewrite. Across agent kinds the names mean different
    // things, so the target keeps its own default.
    // Accounts of one agent can still serve different catalogs (a gateway's GLM means nothing to a
    // subscription), so only what the target lists is carried; the rest falls back to its default.
    const kind = (id: string) => this.adapters.get(id)?.id;
    const sameKind = kind(agent) !== undefined && kind(agent) === kind(source.agent);
    const offers = async (pick: () => Promise<boolean>) => { try { return await pick(); } catch { return false; } };
    const target = this.adapters.get(agent);
    const chosen = model ?? (sameKind && source.model && target
      && await offers(async () => (await this.adapterModels(target)).some(o => o.id === source.model)) ? source.model : undefined);
    const effort = sameKind && source.effort
      && await offers(async () => (await this.effortsFor(agent, chosen ?? null) as readonly string[]).includes(source.effort!)) ? source.effort : undefined;
    const successor = await this.createThread({
      agent,
      cwd: source.cwd,
      title: `${source.title}（接手）`,
      model: chosen,
      effort,
      sectionId: source.sectionId,
    }, source.worktree, { sourceId: source.id, title: source.title, agent: source.agent, mode: selected,
      sourceSeq: this.store.history.latest(source.id), ...(method ? { method } : {}) });
    this.personal.inherit(source.id, successor.id);
    return successor;
  }

  /** The project roots, each with whether it was added from the page and so can be removed there. */
  workdirsConfig(): { workdirs: string[]; roots: Array<{ path: string; removable: boolean }>; scratch: boolean } {
    return { workdirs: this.dirs.roots(), roots: this.dirs.describe(), scratch: this.dirs.scratchRoot !== null };
  }

  addWorkdir(path: unknown): ReturnType<ThreadManager['workdirsConfig']> {
    try {
      this.dirs.add(typeof path === 'string' ? path.trim() : '');
    } catch (err) {
      throw new HttpError(400, err instanceof Error ? err.message : String(err));
    }
    return this.workdirsConfig();
  }

  removeWorkdir(path: unknown): ReturnType<ThreadManager['workdirsConfig']> {
    try {
      this.dirs.remove(typeof path === 'string' ? path : '');
    } catch (err) {
      throw new HttpError(400, err instanceof Error ? err.message : String(err));
    }
    return this.workdirsConfig();
  }

  /**
   * A new project: a folder named `name` in `parent` (inside a project root), made a git repository
   * with one empty commit when `git` is set, so a thread can start in a worktree of it at once.
   */
  async createProject(input: { parent?: unknown; name?: unknown; git?: unknown }): Promise<{ path: string; git: boolean }> {
    const name = typeof input.name === 'string' ? input.name.trim() : '';
    if (!name || name.length > 100 || /[\\/\0]/.test(name) || name.startsWith('.')) {
      throw new HttpError(400, 'a project name is a plain folder name: no slashes, not starting with a dot');
    }
    const parent = typeof input.parent === 'string' && input.parent ? resolve(input.parent) : '';
    if (!parent || !isWithinWorkdirs(parent, this.allowedWorkdirs)) throw new HttpError(400, 'parent is not in the allowed workdirs');
    if (!isDirectory(parent)) throw new HttpError(400, 'parent does not exist');
    const path = join(parent, name);
    if (existsSync(path)) throw new HttpError(409, 'a file or folder with this name is already there', { code: 'exists' });
    mkdirSync(path);
    const git = input.git === true;
    if (git) {
      await initRepository(path).catch((err) => {
        throw new HttpError(500, `the folder was made, but git init failed: ${err instanceof Error ? err.message : err}`);
      });
    }
    return { path, git };
  }

  /**
   * Whether a new thread in `cwd` could start in a worktree of its own, asked when a directory is
   * picked for one. The same answer as for forking, minus the agent: a new thread has nothing to move.
   */
  async worktreeOptions(cwdInput: unknown): Promise<ForkOptions['worktree']> {
    if (typeof cwdInput !== 'string' || !cwdInput) throw new HttpError(400, 'cwd is required');
    const cwd = resolve(cwdInput);
    if (!isWithinWorkdirs(cwd, this.workdirs)) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    const plan = await this.checkoutFor(cwd);
    if (!plan.checkout) return { available: false, reason: plan.reason };
    return { available: true, branch: plan.checkout.branch, changes: await countChanges(plan.checkout.top).catch(() => 0) };
  }

  /**
   * Edits an earlier user message and sends it again from that point, in this same thread.
   *
   * The agent's conversation up to just before that turn is copied into a new native session and
   * this thread continues there. The turns from the edited one on are kept out of the current
   * conversation and preserved in the hidden edit records, so the thread still reads as one
   * conversation and nothing is lost for recovery. Files are not rewound.
   */
  async editTurn(
    threadId: string,
    turnId: string,
    input: { text?: unknown; images?: unknown; videos?: unknown; revision?: unknown; requestId?: unknown },
  ): Promise<{ turnId: string; revision: number }> {
    const thread = this.getThread(threadId);
    const adapter = this.adapters.get(thread.agent);
    if (!adapter?.capabilities.editMessage || !adapter.capabilities.forkAt || !adapter.loadHistory) {
      throw new HttpError(400, `${thread.agent} cannot edit a message that was already sent`);
    }
    if (!adapter.forkSession && !adapter.forkBeforeSession) throw new HttpError(400, `${thread.agent} cannot copy a conversation`);
    if (!thread.agentSessionId) throw new HttpError(400, 'there is nothing to edit yet; send a message first');
    if (thread.archivedAt) throw new HttpError(409, 'this thread is archived; restore it first');
    if (this.running.has(threadId) || this.startingTurns.has(threadId) || this.autoHandover.held(threadId)) {
      throw new HttpError(409, 'wait for the current work in this thread to finish before editing a message');
    }
    if ((this.queues.get(threadId) ?? []).some(q => !q.error)) throw new HttpError(409, 'send or take back the waiting messages first');
    const requestId = typeof input.requestId === 'string' ? input.requestId : '';
    if (!/^[A-Za-z0-9._-]{8,80}$/.test(requestId)) throw new HttpError(400, 'invalid edit request id');
    const revision = Number(input.revision);
    if (!Number.isSafeInteger(revision) || revision < 1) throw new HttpError(400, 'invalid history revision');
    const text = typeof input.text === 'string' ? input.text.trim() : '';
    const imageIds = Array.isArray(input.images) ? input.images : [];
    const videoIds = Array.isArray(input.videos) ? input.videos : [];
    if (!text && imageIds.length === 0 && videoIds.length === 0) throw new HttpError(400, 'text is empty');
    const fingerprint = MessageEdits.fingerprint({ turnId, text, images: imageIds, videos: videoIds, revision });

    // A retry of an edit that already went through answers with the turn it sent: the turn it
    // replaced is gone from the current history by now, so this is checked before looking for it.
    const previous = this.store.messageEdits.get(threadId, requestId);
    if (previous && previous.fingerprint !== fingerprint) throw new HttpError(409, 'this edit request id was used for different content');
    if (previous?.state === 'committed' && previous.newTurnId) {
      return { turnId: previous.newTurnId, revision: this.store.history.latest(threadId) };
    }

    const starts = this.store.listEventsOfType(threadId, 'turn.started');
    const hubIndex = starts.findIndex((ev) => ev.turnId === turnId);
    if (hubIndex < 0) throw new HttpError(404, 'no such turn in this thread');
    const target = starts[hubIndex];
    const targetData = target.data as Record<string, unknown>;
    if (targetData.fromTerminal || targetData.imported || targetData.handover || targetData.coordination
      || targetData.control || targetData.scheduled) {
      throw new HttpError(409, 'this message was not sent from the hub, so it cannot be edited here');
    }

    if (!previous) {
      this.store.messageEdits.reserve({ threadId, requestId, turnId, revision, fingerprint, sourceSession: thread.agentSessionId });
    }

    // A revision that predates an edit means the page is looking at a conversation that has since
    // been replaced. Turns finishing after it are fine: they are part of the history the page saw
    // the start of, and the turn being edited is still the one it named.
    const replacedAfter = this.store.listEventsOfType(threadId, 'thread.historyChanged').some((ev) => ev.seq > revision);
    if (replacedAfter && previous?.state !== 'ready') throw new HttpError(409, 'this conversation changed; reload it before editing', { code: 'history_changed' });

    let branchSession = previous?.state === 'ready' ? previous.branchSession : null;
    if (!branchSession) {
      const [agentTurns, hubTexts] = await Promise.all([
        adapter.loadHistory(thread.agentSessionId, thread.cwd),
        Promise.resolve(starts.map((ev) => turnKey(String(ev.data.text ?? '')))),
      ]);
      const matched = matchTurns(agentTurns.map((t) => turnKey(t.text)), hubTexts, ThreadManager.SYNC_SKIP_TOLERANCE);
      const agentIndex = matched?.hubToAgent[hubIndex] ?? -1;
      if (agentIndex < 0) throw new HttpError(409, `this turn is not in ${thread.agent}'s own history, so it cannot be edited`);
      const at = agentTurns[agentIndex];
      try {
        if (at.forkBeforeRef && adapter.forkBeforeSession) {
          branchSession = await adapter.forkBeforeSession(thread.agentSessionId, thread.cwd, at.forkBeforeRef);
        } else if (agentIndex > 0 && agentTurns[agentIndex - 1].forkRef && adapter.forkSession) {
          branchSession = await adapter.forkSession(thread.agentSessionId, thread.cwd, agentTurns[agentIndex - 1].forkRef);
        } else {
          throw new HttpError(409, `${thread.agent} cannot copy a conversation up to just before this message`);
        }
      } catch (err) {
        this.store.messageEdits.failed(threadId, requestId);
        if (err instanceof HttpError) throw err;
        throw new HttpError(502, `could not copy the ${thread.agent} conversation: ${err instanceof Error ? err.message : err}`);
      }
      this.store.messageEdits.ready(threadId, requestId, branchSession);
    }

    const newTurnId = randomUUID();
    await this.dropSession(threadId);
    this.store.transaction(() => {
      this.store.messageEdits.archive(threadId, requestId, starts[hubIndex].seq);
      this.store.updateThread(threadId, { agentSessionId: branchSession, status: 'idle' }, { touch: true });
      this.store.messageEdits.commit(threadId, requestId, newTurnId);
    });
    this.emitThreadEvent(threadId, null, 'thread.historyChanged', { requestId, turnId: newTurnId });

    const images = imageIds.map((id) => {
      const found = typeof id === 'string' ? this.attachments.image(threadId, id) : null;
      if (!found) throw new HttpError(404, 'an image of this message is no longer available');
      return found;
    });
    const videos = this.attachments.videos(threadId, videoIds);
    try {
      await this.startTurn(
        threadId,
        {
          text,
          images: images.map((it) => ({ data: it.image.data, mimeType: it.image.mimeType })),
          videos: videos.map((v) => v.id),
          workMode: typeof targetData.workMode === 'string' ? targetData.workMode : undefined,
          fromUser: true,
        },
        undefined,
        false,
        newTurnId,
      );
    } catch (err) {
      this.emitThreadEvent(threadId, newTurnId, 'turn.error', {
        message: `the edited message was kept, but sending it failed: ${err instanceof Error ? err.message : err}`,
        code: 'edit_send_failed',
      });
      throw err;
    }
    return { turnId: newTurnId, revision: this.store.history.latest(threadId) };
  }

  /** Check before creating a team task or reopening one; invalid pictures must not change its state. */
  async checkImages(agent: string, model: string | null, images: unknown[]): Promise<void> {
    if (!images.length) return;
    const adapter = this.adapters.get(agent);
    if (!adapter?.capabilities.images) throw new HttpError(400, `${agent} does not accept images`);
    let option = await this.modelOption(adapter, model);
    if (adapter.modelForImages) option = await adapter.modelForImages(model);
    if (option?.images === false && !option.imagesVia) throw new HttpError(400, `${option.label} cannot read images; pick another model`);
    this.attachments.check(images);
  }

  /**
   * Starts a turn in the background and returns its id right away.
   * A turn needs text, images, or both. Images are checked and stored before anything else changes.
   *
   * With `queue`, a message sent while a turn is running waits its turn instead of being refused:
   * it goes out when the running turn (and anything queued before it) has finished.
   */
  startTurn(
    threadId: string,
    input: Parameters<ThreadManager['startTurnReady']>[1],
    delivery?: Parameters<ThreadManager['startTurnReady']>[2],
    fromQueue = false,
    /** Sends this exact turn id, reserved by an edit; the edit's steps all use it. */
    reservedTurnId?: string,
    beforeStart?: (turnId: string) => void,
    personalControl?: { assistantSay: string; authorize?: () => void },
  ): Promise<{ turnId: string } | { queued: true; id: string }> {
    // Setup may await discovery or an idle runtime refresh. Keep competing sends in order.
    const previous = this.startingTurns.get(threadId) ?? Promise.resolve();
    const next = previous.catch(() => undefined).then(() => this.startTurnReady(threadId, input, delivery, fromQueue, reservedTurnId, beforeStart, personalControl));
    this.startingTurns.set(threadId, next);
    const clean = () => {
      if (this.startingTurns.get(threadId) === next) this.startingTurns.delete(threadId);
      this.startNextQueued(threadId);
    };
    void next.then(clean, clean);
    return next;
  }

  private async startTurnReady(
    threadId: string,
    input: { voiceSummary?: string; workMode?: unknown; text?: unknown; displayText?: string; channel?: TurnChannel; coalesce?: TurnBatch; images?: unknown; videos?: unknown; queue?: unknown; mentions?: unknown; collaboration?: CoordinationSetup; handoverFrom?: string; autoContinuation?: boolean; handover?: unknown; scheduled?: unknown; review?: boolean; proactive?: unknown; fromUser?: boolean },
    delivery?: { turnId: string; source: Record<string, unknown>; displayText: string; claim: () => void },
    fromQueue = false,
    reservedTurnId?: string,
    beforeStart?: (turnId: string) => void,
    personalControl?: { assistantSay: string; authorize?: () => void },
  ): Promise<{ turnId: string } | { queued: true; id: string }> {
    const thread = this.getThread(threadId);
    if (this.changingModes.has(threadId)) throw new HttpError(409, '模式正在切换，请稍后发送');
    if (input.workMode !== undefined) thread.workMode = this.validateWorkMode(thread.agent, input.workMode);
    const originalText = typeof input.text === 'string' ? input.text.trim() : '';
    const reference = this.references.prepare(threadId, originalText, input.fromUser === true, input.handoverFrom);
    if (thread.workMode === 'plan' && /^\/goal(?:\s|$)/.test(originalText) && !['/goal', '/goal pause', '/goal clear'].includes(originalText)) throw new HttpError(409, '请切到执行模式后启动目标');
    const mentions = Array.isArray(input.mentions) && input.mentions.length ? input.mentions : undefined;
    if ((mentions || input.collaboration) && !this.coordination) throw new HttpError(400, '协作不可用');
    let rawText = await this.coordination?.previewMentions(threadId, originalText, mentions, input.collaboration) ?? originalText;
    const rawImages = Array.isArray(input.images) ? input.images : [];
    const storedVideos = this.attachments.videos(threadId, input.videos);
    const videoIds = storedVideos.map(v => v.id);
    if (storedVideos.length && /^\/\S+/.test(rawText)) throw new HttpError(400, '视频和文件请随普通消息发送，不能附在斜杠命令后');
    if (!rawText && rawImages.length === 0 && !storedVideos.length) throw new HttpError(400, 'text is empty');
    personalControl?.authorize?.();
    if (thread.archivedAt && !this.backgroundThreads.has(threadId)) throw new HttpError(409, 'this thread is archived; restore it first');
    if (this.running.has(threadId) || this.autoHandover.held(threadId) && !input.handover && !input.autoContinuation || !fromQueue && this.queues.get(threadId)?.some(q => !q.error)) {
      const goalAction = originalText === '/goal' ? 'show' : originalText === '/goal pause' ? 'pause' : originalText === '/goal clear' ? 'clear' : null;
      const adapter = this.adapters.get(thread.agent);
      const session = this.sessions.get(threadId);
      if (this.running.has(threadId) && !delivery && goalAction && !rawImages.length && !mentions && adapter?.capabilities.goal?.controls.includes(goalAction)) {
        if (!session?.controlGoal) throw new HttpError(409, '智能体正在启动，请稍后重试目标操作。');
        // A control must not wait behind the work it is supposed to stop. It has its own
        // transcript entry, but does not claim or release the running turn's lock/hooks.
        const turnId = randomUUID();
        // Marked, so whoever reads turn endings can tell an answer to a control from finished work.
        this.emitThreadEvent(threadId, turnId, 'turn.started', { text: originalText, control: true });
        if (goalAction === 'pause') {
          for (const [id, approval] of this.approvals) {
            if (approval.threadId === threadId) this.resolveApproval(id, { cancelled: true });
          }
        }
        void session.controlGoal(goalAction).then((text) => {
          if (!this.store.getThread(threadId)) return;
          this.emitThreadEvent(threadId, turnId, 'message.delta', { text });
          this.emitThreadEvent(threadId, turnId, 'turn.done', { stopReason: 'end_turn' });
        }).catch((error) => {
          if (this.store.getThread(threadId)) this.emitThreadEvent(threadId, turnId, 'turn.error', { message: String(error) });
        });
        return { turnId };
      }
      if (input.queue !== true) throw new HttpError(409, 'a turn is already running in this thread');
      const queue = this.queues.get(threadId) ?? [];
      const tail = queue.at(-1);
      const merged = input.coalesce && tail?.coalesce && !tail.error ? mergeBatches(tail.coalesce, input.coalesce) : null;
      if (tail && merged) {
        this.setQueue(threadId, [...queue.slice(0, -1), { ...tail, text: merged.body + merged.context, coalesce: merged, fromUser: merged.parts.some(part => part.kind === 'person'), ...batchShape(merged.parts) }]);
        return { queued: true, id: tail.id };
      }
      if (queue.length >= MAX_QUEUED) throw new HttpError(409, `at most ${MAX_QUEUED} messages can wait in a thread`);
      this.attachments.check(rawImages); // a bad image is refused now, not when its turn comes
      const queued: QueuedTurn = { ...(personalControl ? { assistantSay: personalControl.assistantSay } : {}), voiceSummary: input.voiceSummary, workMode: thread.workMode ?? 'execute', id: randomUUID(), text: originalText, displayText: input.displayText, channel: input.channel, ...(input.coalesce ? { coalesce: input.coalesce } : {}), images: rawImages, videos: videoIds, ...(mentions ? { mentions } : {}), ...(input.collaboration ? { collaboration: input.collaboration } : {}), ...(input.fromUser ? { fromUser: true } : {}) };
      this.references.grant(threadId, reference.references);
      this.queues.set(threadId, [...queue, queued]);
      this.emitThreadEvent(threadId, null, 'thread.updated', {});
      return { queued: true, id: queued.id };
    }

    if ((mentions || input.collaboration) && !this.coordination?.metadata(threadId)) {
      if (!this.coordination) throw new HttpError(400, '协作不可用');
      await this.coordination.enable(threadId, input.collaboration);
    }
    rawText = this.coordination?.mentionText(threadId, reference.text, mentions) ?? reference.text;
    // Slash commands have their own argument grammar; hidden instructions become unwanted arguments.
    const slashCommand = /^\/\S+/.test(rawText);
    // A collaboration member spoken to by the person directly answers the person, not the lead.
    const fromUser = Boolean(input.fromUser && !delivery && this.coordination?.isMember(threadId));
    // A resent prompt (a retry, a handover) carries its blocks already: they are not attached twice.
    const gate: InjectionGate = (kind, block) => !rawText.includes(`<loochat-${kind}>`) && this.injections.needed(threadId, kind, block);
    const coordinated = slashCommand ? rawText : rawText ? (this.coordination?.instructions(input.handoverFrom ?? threadId, rawText, fromUser, gate) ?? rawText) : '';
    // A background question goes as asked: no hints, tools or progress request around it.
    const bare = this.backgroundThreads.has(threadId);
    const discoverable = slashCommand || !rawText || bare ? coordinated : this.personal.instructions(threadId, this.borrowing?.instructions(threadId, coordinated, gate) ?? coordinated, gate);
    // With the board's consent, the agent is asked to say where the work stands; read back at the end.
    // Writing mode's way of working for its stage; an edit the person made is mentioned once.
    const writing = !slashCommand && rawText ? thread.writing : null;
    const written = writing ? writingInstructions(discoverable, writing) : discoverable;
    const askProgress = !bare && !slashCommand && Boolean(rawText) && this.progressWanted();
    const text = askProgress ? progressInstructions(written, gate) : written;

    if (rawImages.length > 0) {
      await this.checkImages(thread.agent, thread.model, rawImages);
      // The model lookup above can take a moment, so check again before claiming the thread.
      if (this.running.has(threadId)) throw new HttpError(409, 'a turn is already running in this thread');
      if (this.changingModes.has(threadId) || input.workMode === undefined && this.getThread(threadId).workMode !== thread.workMode) throw new HttpError(409, '工作模式已变化，请重新发送');
    }
    this.git.assertTurnAllowed(thread.cwd);
    const { stored, images } = this.attachments.save(threadId, rawImages);
    const personalHuman = this.personal.router.status().threadId === threadId && input.fromUser === true
      && !delivery && !input.scheduled && !input.review && !input.proactive && !input.autoContinuation;
    const context = personalHuman && !slashCommand ? await this.personal.router.context(threadId) : null;
    const goalContext = !slashCommand && rawText && !bare ? await this.loopx?.context(threadId) ?? '' : '';
    const promptText = (context?.text ?? '') + goalContext + text + this.attachments.videoNote(threadId, storedVideos);
    // Internal callers may attach protocol context. Only the person's words (or a channel
    // summary) belong in the conversation, its queue, and later replay/continuation views.
    const displayText = delivery?.displayText ?? input.displayText ?? originalText.split('\n\n[指定协作对象]')[0];

    const turnId = delivery?.turnId ?? reservedTurnId ?? randomUUID();
    if (askProgress) this.reportTurns.add(turnId);
    try {
      this.store.transaction(() => {
        delivery?.claim();
        this.references.grant(threadId, reference.references);
        personalControl?.authorize?.();
        beforeStart?.(turnId);
        context?.commit();
        // New work reopens a thread the person had marked done.
        if (thread.doneAt && !slashCommand) this.store.setThreadProgress(threadId, { doneAt: null });
        if (writing?.edited) {
          const now = this.store.getThread(threadId)?.writing;
          if (now) this.store.setThreadWriting(threadId, { stage: now.stage, file: now.file });
        }
        this.running.set(threadId, { turnId, cancelRequested: false, since: new Date().toISOString() });
        this.modelQueues.delete(threadId);
        this.store.updateThread(threadId, { status: 'running', workMode: thread.workMode ?? 'execute', hubTurnAt: new Date().toISOString() }, { touch: true });
        if (thread.title === 'New thread' && !input.voiceSummary) {
          const title = askedText(displayText).split('\n')[0].slice(0, 60) || (storedVideos.length ? storedVideos.every(v => isVideoType(v.mimeType)) ? '视频' : storedVideos[0].name : '图片');
          this.store.updateThread(threadId, { title });
          this.store.setTitleBy(threadId, 'default');
        }
        this.emitThreadEvent(threadId, turnId, 'turn.started', {
          text: promptText,
          ...(input.channel ? { channel: input.channel } : {}),
          ...(input.displayText !== undefined ? { hiddenPrompt: true } : {}),
          ...(bare ? { background: true } : {}),
          ...(input.voiceSummary ? { voiceSummary: input.voiceSummary } : {}),
          speed: thread.speed,
          workMode: thread.workMode ?? 'execute',
          ...(delivery ? { coordination: delivery.source } : {}),
          ...(fromUser ? { fromUser: true } : {}),
          ...(personalHuman ? { personalOrigin: 'person' } : {}),
          ...(personalControl ? { assistantSay: personalControl.assistantSay } : {}),
          ...(promptText !== displayText ? { displayText } : {}),
          ...(stored.length ? { images: stored } : {}),
          ...(storedVideos.length ? { videos: storedVideos } : {}),
          ...(typeof input.handover === 'string' && input.handover ? { handover: input.handover, handoverLabel: this.adapters.get(input.handover)?.label ?? input.handover } : {}),
          ...(input.scheduled === true ? { scheduled: true } : {}),
          ...(input.review === true ? { review: true } : {}),
          ...(input.proactive === true ? { proactive: true } : {}),
        });
      });
    } catch (err) { this.running.delete(threadId); this.reportTurns.delete(turnId); throw err; }
    void this.runTurn(thread, turnId, { text: promptText, images });
    return { turnId };
  }

  /**
   * Asks this thread's agent to write a handover summary for another agent, in its own session:
   * it knows its own context best. The turn runs like any other and streams into the thread.
   */
  async startHandover(threadId: string, target: string, allowSame = false): Promise<{ turnId: string }> {
    const thread = this.getThread(threadId);
    const successor = this.adapters.get(target);
    if (!successor) throw new HttpError(404, `no such agent: ${target}`);
    if (!allowSame && target === thread.agent) throw new HttpError(400, 'the successor must be another agent');
    const started = await this.startTurn(threadId, { text: handoverPrompt(successor.label) + this.personal.context(threadId), handover: target }, undefined, true);
    if (!('turnId' in started)) throw new HttpError(409, 'a turn is already running in this thread');
    return started;
  }

  /** Session coordinates without reading history or starting an agent. */
  async handoverLocation(threadId: string) {
    const thread = this.getThread(threadId);
    const adapter = this.adapters.get(thread.agent);
    const location = await adapter?.sessionLocation?.(thread.agentSessionId, thread.cwd);
    return { threadId, agent: thread.agent, agentLabel: adapter?.label ?? thread.agent,
      adapter: adapter?.id ?? null, sessionId: thread.agentSessionId, cwd: thread.cwd,
      sessionPath: location?.sessionPath ?? null, title: thread.title, status: thread.status };
  }

  /**
   * The handover summary: what the agent wrote in its handover turn, or — when the turn failed or
   * none was run — one the hub synthesizes from the event log, which holds the whole process.
   * `brief` is for a successor the hub starts itself: it can read the source with `loochat`, so it gets
   * where to look and a few lines of progress. Without it the text stands alone, to copy anywhere.
   */
  async handoverSummary(
    threadId: string,
    turnId: string | null,
    opts: { carryFailed?: boolean; compress?: boolean } = {},
  ): Promise<{ pending: true } | { summary: string; source: 'agent' | 'log' }> {
    const thread = this.threadView(threadId);
    const events = this.listEventsSince(threadId, 0);
    const adapter = this.adapters.get(thread.agent);
    const checkout = await findCheckout(thread.cwd);
    const worktree = checkout && !samePath(checkout.top, checkout.main)
      ? { root: checkout.top, branch: checkout.branch, main: checkout.main }
      : thread.worktree ? { root: thread.worktree.root, branch: thread.worktree.branch, main: thread.worktree.of } : null;
    // The brief of the auto handover: the successor reads the source itself with `loochat`, so it gets
    // where to look and how, plus a few lines of where things stood — not a retelling.
    const context = handoverBrief({ threadId, from: adapter?.label ?? thread.agent, cwd: thread.cwd, worktree, lead: this.coordination?.leads(threadId) })
      + this.personal.context(threadId);
    if (turnId) {
      const turnEvents = events.filter((e) => e.turnId === turnId);
      if (turnEvents.length === 0) throw new HttpError(404, 'no such turn');
      if (!turnEvents.some((e) => e.type === 'turn.done' || e.type === 'turn.error')) return { pending: true };
      const written = turnMessage(turnEvents).trim();
      if (written && !turnEvents.some(e => e.type === 'turn.error' || e.type === 'turn.done' && e.data.stopReason === 'cancelled')) return { summary: `${context}\n\n${written}`, source: 'agent' };
    }
    // The brief's 最近的要求 steps over a failed last ask (the auto path carries it word for word
    // after the text); without this section a handover written on a failed thread would lose it.
    const lastTurn = this.store.listTurns(threadId).at(-1)?.turnId ?? null;
    const failedTurn = lastTurn && this.store.listTurnEvents(threadId, lastTurn).some(e => e.type === 'turn.error') ? lastTurn : null;
    const retryStart = failedTurn ? this.store.listTurnEvents(threadId, failedTurn).find(e => e.type === 'turn.started') : undefined;
    const retry = opts.carryFailed && retryStart ? String(retryStart.data.displayText ?? retryStart.data.text ?? '') : '';
    // The source could not write its own summary: another agent reads the record, when one is set up for that.
    const written = opts.compress ? await this.autoCompact.summarizeForHandover(threadId) : null;
    const brief = [context, written, synthesizeBrief(events)].filter(Boolean).join('\n\n');
    return { summary: retry ? `${brief}\n\n## 继续未完成的这一轮\n\n${retry}` : brief, source: 'log' };
  }

  hasAgent(id: string): boolean { return this.adapters.has(id); }
  canSwitchHandoverModel(id: string): boolean { return this.adapters.get(id)?.capabilities.modelSwitch === true; }
  async handoverModel(agent: string, model?: string | null): Promise<string | undefined> {
    try {
      const adapter = this.adapters.get(agent);
      const models = adapter ? await this.adapterModels(adapter) : [];
      return model ? models.find(m => m.id === model)?.id : models.find(m => m.isDefault)?.id;
    } catch { return model || undefined; }
  }
  notifyHandover(id?: string): void {
    if (id) { if (this.store.getThread(id)) this.emitThreadEvent(id, null, 'thread.updated', {}); }
    else for (const listener of this.listeners) listener({ threadId: '', seq: 0, turnId: null, type: 'handover.updated', ts: new Date().toISOString(), data: {} });
  }
  recordHandoverModel(id: string, model: string, previous: string | undefined, reason: string): void {
    this.emitThreadEvent(id, null, 'handover.model', { model, previous, reason });
  }
  async cancelHandoverSummary(id: string): Promise<void> {
    const run = this.running.get(id);
    if (run && this.store.listTurnEvents(id, run.turnId).some(e => e.type === 'turn.started' && e.data.handover)) await this.stopTurn(id).catch(() => undefined);
  }
  async autoHandoverSummary(id: string, target: string, exhausted: boolean): Promise<string> {
    let summaryTurn: string | null = null;
    if (!exhausted) {
      try {
        const started = await this.startHandover(id, target, true);
        summaryTurn = started.turnId;
        await new Promise<void>((resolve) => {
          const finish = () => { clearTimeout(timer); off(); resolve(); };
          const off = this.subscribe(e => { if (e.threadId === id && e.type === 'thread.updated' && !this.running.has(id)) finish(); });
          const timer = setTimeout(() => { void this.stopTurn(id); finish(); }, 60_000);
          if (!this.running.has(id)) finish();
        });
        if (this.running.has(id)) throw new HttpError(409, 'handover summary did not stop');
      } catch (error) {
        if (this.running.has(id)) throw error;
        summaryTurn = null;
      }
    }
    const result = await this.handoverSummary(id, summaryTurn, { compress: true });
    if ('summary' in result) return result.summary;
    const fallback = await this.handoverSummary(id, null, { compress: true });
    if ('summary' in fallback) return fallback.summary;
    throw new HttpError(409, 'handover summary unavailable');
  }
  async retryAutoHandoverTurn(id: string, failedTurn: string): Promise<void> {
    const failed = this.store.listTurnEvents(id, failedTurn).find(e => e.type === 'turn.started');
    if (!failed) throw new HttpError(409, 'failed turn not found');
    const images = ((failed.data.images ?? []) as Array<{ id: string }>).map(img => {
      const file = this.attachments.find(id, img.id);
      if (!file) throw new HttpError(409, 'handover attachment unavailable');
      return { data: readFileSync(file.path).toString('base64') };
    });
    const text = String(failed.data.displayText ?? failed.data.text ?? '');
    const turnId = randomUUID();
    await this.startTurn(id, { text: failed.data.hiddenPrompt ? failed.data.text : text, ...(failed.data.hiddenPrompt ? { displayText: text } : {}), channel: failed.data.channel as TurnChannel | undefined, images, videos: ((failed.data.videos ?? []) as Array<{ id: string }>).map(v => v.id), autoContinuation: true }, {
      turnId, source: { retry: failedTurn }, displayText: text,
      claim: () => this.coordination?.retryHandoverTurn(id, failedTurn, turnId),
    }, true);
  }

  /** The facts behind a thread that wants the person, for the companion to put in words. Read-only, no model. */
  threadBrief(id: string): ThreadBrief {
    const thread = this.getThread(id);
    const pendingApprovals = [...this.approvals].filter(([, approval]) => approval.threadId === id).map(([approvalId]) => approvalId);
    return readBrief(this.store, thread, { pendingApprovals, resume: this.quotaResume.view(id) !== null });
  }

  /**
   * The person's 「接着做」 on a thread that stopped on an error: the failed ask goes out again, as
   * the quota watch would send it, and any watch on the thread is dropped since it is moot now.
   */
  async retryFailedTurn(id: string): Promise<ThreadView> {
    const thread = this.getThread(id);
    if (thread.archivedAt) throw new HttpError(409, 'the thread is archived');
    if (this.running.has(id) || this.threadView(id).waiting) throw new HttpError(409, 'the thread is busy');
    const failed = thread.status === 'error' ? this.store.lastTurnFailure(id) : null;
    if (!failed) throw new HttpError(409, 'the thread did not stop on an error');
    this.quotaResume.drop(id);
    await this.retryAutoHandoverTurn(id, failed.turnId);
    return this.threadView(id);
  }

  /**
   * The person hands the thread to `agent` now, because its own agent cannot go on (its plan ran
   * out). Same path as an automatic handover, without asking the source for a summary first: the
   * successor takes over the conversation, its queue and, in a collaboration, its place.
   */
  async takeOver(id: string, agent: unknown, model: unknown, note: unknown): Promise<ThreadView> {
    const thread = this.getThread(id);
    if (typeof agent !== 'string' || !this.adapters.has(agent) || agent === thread.agent && !model) throw new HttpError(400, 'invalid handover target');
    if (model !== undefined && typeof model !== 'string') throw new HttpError(400, 'invalid model');
    if (note !== undefined && (typeof note !== 'string' || note.length > 20_000)) throw new HttpError(400, 'invalid note');
    if (this.running.has(id) || this.threadView(id).waiting) throw new HttpError(409, 'the thread is busy');
    this.autoHandover.cancel(id);
    this.quotaResume.drop(id);
    // The turn that failed is carried over, so the successor picks up the ask that was cut off.
    const last = this.store.listTurns(id).at(-1)?.turnId ?? null;
    const failed = last && this.store.listTurnEvents(id, last).some(e => e.type === 'turn.error') ? last : null;
    const target = { agent, ...(model ? { model } : {}) };
    if (this.autoHandover.settings().method === 'resume' && await this.canResumeHandover(id, agent, failed)) {
      const sessionId = await this.importHandoverSession(id, agent);
      if (sessionId) return this.executeResumeHandover(id, target, sessionId, failed, note?.trim());
    }
    const result = await this.handoverSummary(id, null, { compress: true });
    if (!('summary' in result)) throw new HttpError(409, 'handover summary unavailable');
    return this.executeAutoHandover(id, target, result.summary, failed, note?.trim());
  }

  /**
   * Whether `target` can go on in a copy of this thread's own conversation: another instance (another
   * account) of the same agent, one that can take in a session the source wrote.
   */
  async canResumeHandover(id: string, target: string, failedTurn: string | null): Promise<boolean> {
    const thread = this.getThread(id);
    if (target === thread.agent || !thread.agentSessionId || !this.adapters.get(thread.agent)?.sessionLocation || !this.adapters.get(target)?.importSession) return false;
    // The copy starts the successor with the whole context on a cold cache, and every step after
    // re-reads it; past this size a brief and `loochat history` cost far less than carrying it over.
    if ((this.store.lastContextUsed(id) ?? 0) > RESUME_HANDOVER_MAX_CONTEXT) return false;
    // A teammate's place moves with the successor's first turn; without a failed turn to resend there is none.
    if (!failedTurn && this.coordination?.metadata(id)) return false;
    const agents = await this.listAgents();
    const kind = (agent: string) => agents.find(a => a.id === agent)?.adapter;
    return kind(thread.agent) !== undefined && kind(thread.agent) === kind(target);
  }

  /** Copies the thread's conversation into `target`'s account; null when it cannot be read or written. */
  async importHandoverSession(id: string, target: string): Promise<string | null> {
    const thread = this.getThread(id);
    try {
      const { sessionPath } = await this.adapters.get(thread.agent)!.sessionLocation!(thread.agentSessionId!, thread.cwd);
      return sessionPath ? await this.adapters.get(target)!.importSession!(sessionPath) : null;
    } catch (error) {
      console.error(`[handover] could not copy the conversation of ${id} to ${target}: ${error instanceof Error ? error.message : error}`);
      return null;
    }
  }

  /**
   * Hands the thread to another account of the same agent, which goes on in a copy of the
   * conversation: no brief, the failed turn resent as it was asked. The successor joins the source as
   * an automatic continuation, so the page shows one conversation.
   */
  async executeResumeHandover(id: string, target: HandoverTarget, sessionId: string, failedTurn: string | null, note?: string): Promise<ThreadView> {
    const source = this.getThread(id);
    this.quotaResume.drop(id);
    const successor = await this.createHandoverThread(id, target.agent, target.model, 'continue', 'resume');
    this.store.updateThread(successor.id, { agentSessionId: sessionId });
    this.autoHandover.inherit(id, successor.id);
    const adapter = this.adapters.get(target.agent)!;
    if (adapter.capabilities.permissionModes.includes(source.permissionMode as PermissionMode)) await this.updateThread(successor.id, { permissionMode: source.permissionMode });
    if (adapter.capabilities.workModes?.includes(source.workMode ?? 'execute')) await this.updateThread(successor.id, { workMode: source.workMode ?? 'execute' });
    this.attachments.copyThread(id, successor.id);
    const failed = failedTurn ? this.store.listTurnEvents(id, failedTurn).find(e => e.type === 'turn.started') : undefined;
    if (failed || note) {
      const images = ((failed?.data.images ?? []) as Array<{ id: string }>).map(img => {
        const file = this.attachments.find(id, img.id);
        if (!file) throw new HttpError(409, 'handover attachment unavailable');
        return { mimeType: file.mimeType, data: readFileSync(file.path).toString('base64') };
      });
      const added = note ? `${failed ? '\n\n' : ''}${note}` : '';
      const text = String(failed?.data.displayText ?? failed?.data.text ?? '') + added;
      const turnId = randomUUID();
      await this.startTurn(successor.id, { text: failed?.data.hiddenPrompt ? String(failed.data.text) + added : text, ...(failed?.data.hiddenPrompt ? { displayText: text } : {}), channel: failed?.data.channel as TurnChannel | undefined, images, videos: ((failed?.data.videos ?? []) as Array<{ id: string }>).map(v => v.id), handoverFrom: id }, {
        turnId, source: { handover: id, ...(failedTurn ? { retry: failedTurn } : {}) }, displayText: text,
        claim: () => this.coordination?.replaceHandoverMember(id, successor.id, turnId),
      });
    }
    const queue = this.queues.get(id) ?? [];
    this.setQueue(id, []);
    if (queue.length) this.setQueue(successor.id, [...(this.queues.get(successor.id) ?? []), ...queue]);
    this.startNextQueued(successor.id);
    return this.threadView(successor.id);
  }

  async executeAutoHandover(id: string, target: HandoverTarget, summary: string, failedTurn: string | null, note?: string): Promise<ThreadView> {
    const source = this.getThread(id);
    // The work moves; waiting for this agent's quota to come back is moot.
    this.quotaResume.drop(id);
    const successor = await this.createHandoverThread(id, target.agent, target.model, 'continue', 'summary');
    this.autoHandover.inherit(id, successor.id);
    const adapter = this.adapters.get(target.agent)!;
    // Preserve the user's execution and permission choices when the successor supports them.
    if (adapter.capabilities.permissionModes.includes(source.permissionMode as PermissionMode)) await this.updateThread(successor.id, { permissionMode: source.permissionMode });
    if (adapter.capabilities.workModes?.includes(source.workMode ?? 'execute')) await this.updateThread(successor.id, { workMode: source.workMode ?? 'execute' });
    this.attachments.copyThread(id, successor.id);
    const failed = failedTurn ? this.store.listTurnEvents(id, failedTurn).find(e => e.type === 'turn.started') : undefined;
    const images = ((failed?.data.images ?? []) as Array<{ id: string }>).map(img => {
      const file = this.attachments.find(id, img.id);
      if (!file) throw new HttpError(409, 'handover attachment unavailable');
      return { mimeType: file.mimeType, data: readFileSync(file.path).toString('base64') };
    });
    const videos = ((failed?.data.videos ?? []) as Array<{ id: string }>).map(v => v.id);
    const retry = failed ? String(failed.data.displayText ?? failed.data.text ?? '') : '';
    const turnId = randomUUID();
    const text = summary + (retry ? '\n\n## 继续未完成的这一轮\n\n' + retry : '') + (note ? '\n\n## 用户补充\n\n' + note : '');
    const promptText = failed?.data.hiddenPrompt
      ? summary + '\n\n## 继续未完成的这一轮\n\n' + String(failed.data.text) + (note ? '\n\n## 用户补充\n\n' + note : '')
      : text;
    // Reserve the mailbox identity in the same synchronous claim as the first successor turn.
    await this.startTurn(successor.id, { text: promptText, ...(failed?.data.hiddenPrompt ? { displayText: text } : {}), channel: failed?.data.channel as TurnChannel | undefined, images, videos, handoverFrom: id }, {
      turnId, source: { handover: id }, displayText: text,
      claim: () => this.coordination?.replaceHandoverMember(id, successor.id, turnId),
    });
    const queue = this.queues.get(id) ?? [];
    this.attachments.copyThread(id, successor.id);
    this.setQueue(id, []);
    if (queue.length) this.setQueue(successor.id, [...(this.queues.get(successor.id) ?? []), ...queue]);
    this.startNextQueued(successor.id);
    return this.threadView(successor.id);
  }

  /** Takes a waiting message out of the queue and hands it back, so the page can edit and resend it. */
  removeQueued(threadId: string, queuedId: string): { text: string; images: unknown[]; videos?: StoredVideo[]; mentions?: unknown[] } {
    this.getThread(threadId);
    const queue = this.queues.get(threadId) ?? [];
    const taken = queue.find((q) => q.id === queuedId);
    if (!taken) throw new HttpError(404, 'no such waiting message');
    const videos = this.attachments.videos(threadId, taken.videos);
    this.setQueue(threadId, queue.filter((q) => q.id !== queuedId));
    this.startNextQueued(threadId);
    return { text: taken.displayText ?? taken.text, images: taken.images, ...(videos.length ? { videos } : {}), ...(taken.mentions ? { mentions: taken.mentions } : {}) };
  }

  /** Interrupts the running turn so that this waiting message goes out next, ahead of the others. */
  async sendQueuedNow(threadId: string, queuedId: string): Promise<void> {
    this.getThread(threadId);
    const queue = this.queues.get(threadId) ?? [];
    const chosen = queue.find((q) => q.id === queuedId);
    if (!chosen) throw new HttpError(404, 'no such waiting message');
    delete chosen.error;
    this.setQueue(threadId, [chosen, ...queue.filter((q) => q !== chosen)]);
    if (this.running.has(threadId)) await this.cancelTurn(threadId, { keepQueue: true });
    else this.startNextQueued(threadId);
  }

  private setQueue(threadId: string, queue: QueuedTurn[]): void {
    if (queue.length) this.queues.set(threadId, queue);
    else this.queues.delete(threadId);
    this.emitThreadEvent(threadId, null, 'thread.updated', {});
  }

  /** Sends the next waiting message, if there is one. Called whenever a turn has finished. */
  private startNextQueued(threadId: string): void {
    if (this.autoHandover.queuePaused(threadId) || this.running.has(threadId) || this.startingTurns.has(threadId) || this.dispatchingQueues.has(threadId)) return;
    const queue = this.queues.get(threadId);
    const next = queue?.find(q => !q.error);
    if (!queue || !next) return;
    const rest = queue.filter(q => q !== next);
    this.dispatchingQueues.add(threadId);
    if (rest.length) this.queues.set(threadId, rest);
    else this.queues.delete(threadId);
    this.startTurn(threadId, { workMode: next.workMode, text: next.text, displayText: next.displayText, channel: next.channel, voiceSummary: next.voiceSummary, images: next.images, videos: next.videos, mentions: next.mentions, collaboration: next.collaboration, fromUser: next.fromUser }, undefined, true, undefined, undefined, next.assistantSay ? { assistantSay: next.assistantSay } : undefined).catch((err) => {
      // The thread was archived or deleted in the meantime, or the message is no longer acceptable.
      console.error(`could not send a queued message: ${err instanceof Error ? err.message : err}`);
      if (this.store.getThread(threadId)) this.setQueue(threadId, [
        { ...next, error: err instanceof Error ? err.message : String(err) }, ...(this.queues.get(threadId) ?? []),
      ]);
    }).finally(() => {
      this.dispatchingQueues.delete(threadId);
      this.startNextQueued(threadId);
    });
  }

  /** A cancelled round cannot authorize new work while its process is still stopping. */
  activeTurnId(threadId: string): string | null {
    const run = this.running.get(threadId);
    return run && !run.cancelRequested ? run.turnId : null;
  }

  /**
   * Stops the running turn. Stopping means stopping: messages waiting behind it are not sent, they
   * are handed back (their text) so the caller can put them in front of the user again.
   */
  async cancelTurn(threadId: string, opts: { keepQueue?: boolean; authorize?: () => void } = {}): Promise<{ dropped: string[]; mentions?: unknown[] }> {
    this.getThread(threadId);
    if (!opts.keepQueue) await this.coordination?.beforeStop(threadId);
    opts.authorize?.();
    const mentions = opts.keepQueue ? [] : (this.queues.get(threadId) ?? []).flatMap(q => q.mentions ?? []);
    const dropped = opts.keepQueue ? [] : (this.queues.get(threadId) ?? []).map((q) => q.displayText ?? q.text).filter(Boolean);
    if (!opts.keepQueue && this.queues.has(threadId)) this.setQueue(threadId, []);
    await this.stopTurn(threadId);
    return { dropped, ...(mentions.length ? { mentions } : {}) };
  }

  private async stopTurn(threadId: string): Promise<void> {
    const run = this.running.get(threadId);
    if (run) run.cancelRequested = true;
    this.inputs.cancel(threadId, undefined, true);
    this.cancelApprovals(threadId);
    const session = this.sessions.get(threadId);
    if (session) await session.cancel();
  }

  /**
   * Answers a pending approval. Returns false if there is none with this id.
   * An option the agent did not offer is an error: agents treat unknown ids as a refusal,
   * which would silently turn a mistyped "allow" into "deny".
   */
  resolveApproval(approvalId: string, decision: ApprovalDecision): boolean {
    const p = this.approvals.get(approvalId);
    if (!p) return false;
    if (!('cancelled' in decision) && !p.optionIds.includes(decision.optionId)) {
      throw new HttpError(400, `not one of the offered options: ${decision.optionId}`);
    }
    this.approvals.delete(approvalId);
    p.resolve(decision);
    return true;
  }

  private cancelApprovals(threadId: string, turnId?: string): void {
    for (const [id, thread] of this.approvalTurns) {
      if (thread === threadId && (turnId === undefined || id === turnId)) this.approvalTurns.delete(id);
    }
    for (const [id, approval] of this.approvals) {
      if (approval.threadId === threadId && (turnId === undefined || approval.turnId === turnId)) {
        this.resolveApproval(id, { cancelled: true });
      }
    }
  }

  /** Closes live agent processes. Threads and events stay in the store. */
  async shutdown(): Promise<void> {
    await this.personal.explore.dispose();
    await this.lark.dispose();
    this.autoHandover.close();
    if (this.quotaWatch) clearInterval(this.quotaWatch);
    this.quotaResume.dispose();
    this.coordination?.suspendAll();
    // The file mirror must finish its queued writes before callers remove the data directory.
    await this.personal.flushMirror().catch(() => undefined);
    for (const threadId of [...this.running.keys()]) await this.cancelTurn(threadId).catch(() => undefined);
    const deadline = Date.now() + 3000;
    while (this.running.size > 0 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 25));
    await Promise.allSettled([...this.settlingHandovers]);
    for (const [, s] of this.sessions) await s.close().catch(() => undefined);
    this.sessions.clear();
    this.sessionAdapters.clear();
  }

  private async runTurn(thread: ThreadRecord, turnId: string, input: TurnInput): Promise<void> {
    const start = this.store.listTurnEvents(thread.id, turnId).find(e => e.type === 'turn.started');
    if (!start?.data.handover && !start?.data.control) this.autoHandover.reserve(thread.id, turnId);
    let finalStatus: ThreadRecord['status'] = 'idle';
    // A finished turn is something new to look at. A turn the user stopped is not.
    let unread = true;
    try {
      const hooks = this.hooksFor(thread.id, turnId);
      hooks.emit({ type: 'turn.phase', data: { phase: 'session_start', reused: this.sessions.has(thread.id) } });
      const session = await this.ensureSession(thread);
      hooks.emit({ type: 'turn.phase', data: { phase: 'session_ready' } });
      if (this.nativeTurns.get(thread.id)?.size) await new Promise<void>(resolve => {
        const waiters = this.nativeIdle.get(thread.id) ?? new Set<() => void>();
        waiters.add(resolve);
        this.nativeIdle.set(thread.id, waiters);
      });
      if (this.running.get(thread.id)?.cancelRequested) {
        // Cancelled while the agent was starting: never send the prompt.
        unread = false;
        this.emitThreadEvent(thread.id, turnId, 'turn.done', { stopReason: 'cancelled' });
        return;
      }
      // Durable before the agent is asked: a restart after this point must not ask it again as if it never was.
      if (!this.adapters.get(thread.agent)?.reportsPromptSent) hooks.emit({ type: 'turn.phase', data: { phase: 'prompt_sent' } });
      const result = await session.prompt(input, hooks);
      if (!start?.data.handover && !start?.data.control) this.autoHandover.reserve(thread.id, turnId);
      if (result.stopReason === 'cancelled' || this.quietTurns.has(turnId) && result.stopReason !== 'error') unread = false;
      if (result.stopReason === 'error') {
        finalStatus = 'error';
        const message = result.error ?? 'agent error';
        const code = result.errorCode ?? (this.adapters.get(thread.agent)?.planExhausted?.(message) ? 'plan_exhausted' : undefined);
        this.emitThreadEvent(thread.id, turnId, 'turn.error', {
          message,
          ...(code ? { code, ...(code === 'permission_refused' ? { refused: result.refused ?? [] } : {}) } : {}),
        });
      } else {
        this.emitThreadEvent(thread.id, turnId, 'turn.done', { stopReason: result.stopReason });
        if (result.stopReason !== 'cancelled' && this.reportTurns.has(turnId)) this.recordProgress(thread.id, turnId);
      }
    } catch (err) {
      finalStatus = 'error';
      const message = err instanceof Error ? err.message : String(err);
      const exhausted = this.adapters.get(thread.agent)?.planExhausted?.(message);
      this.emitThreadEvent(thread.id, turnId, 'turn.error', { message, ...(exhausted ? { code: 'plan_exhausted' } : {}) });
      await this.dropSession(thread.id);
    } finally {
      this.cancelApprovals(thread.id, turnId);
      this.quietTurns.delete(turnId);
      this.reportTurns.delete(turnId);
      if (!this.nativeTurns.get(thread.id)?.size) this.inputs.cancel(thread.id, undefined, true);
      if (this.running.get(thread.id)?.turnId === turnId) this.running.delete(thread.id);
      this.clearModelQueue(thread.id, turnId);
      const nativeTurn = this.nativeTurns.get(thread.id)?.values().next().value;
      if (nativeTurn && !this.running.has(thread.id)) {
        this.running.set(thread.id, { turnId: nativeTurn, cancelRequested: false, since: new Date().toISOString() });
        finalStatus = 'running';
      }
      // A model/effort/speed change arrived while this turn ran: now that nothing runs in the
      // session (a native takeover keeps it busy, and its own finish hook retries), drop it so
      // the next turn resumes the agent session with the new setting.
      if (!this.running.has(thread.id) && this.refreshAfterTurn.delete(thread.id)) {
        await this.dropSession(thread.id).catch(() => undefined);
      }
      // The thread may have been deleted while its last turn was winding down.
      if (this.store.getThread(thread.id)) {
        const writing = this.store.getThread(thread.id)!.writing;
        if (writing) {
          // The agent may have named or started the article this turn.
          const file = newestArticle(this.threadView(thread.id).cwdReal);
          if (file !== writing.file) this.store.setThreadWriting(thread.id, { ...writing, file });
        }
        this.store.updateThread(thread.id, { status: finalStatus, ...(unread ? { unread: true } : {}) }, { touch: true });
        this.emitThreadEvent(thread.id, null, 'thread.updated', {});
        // A goal may have ended with the turn without a notification the hub understood.
        void this.refreshGoal(thread.id);
        const settling = this.autoHandover.settled(thread.id, turnId);
        this.settlingHandovers.add(settling);
        let settled: SettledOutcome = 'none';
        try { settled = await settling; } finally { this.settlingHandovers.delete(settling); }
        // The turn died of plan exhaustion and the handover did not move the work away: a spot
        // where the resume watch belongs (armed here when the global setting is on, by the
        // exhausted turn's card otherwise).
        const failed = this.store.listTurnEvents(thread.id, turnId).find(e => e.type === 'turn.error');
        if (failed?.data.code === 'plan_exhausted' && settled !== 'moved'
          && !this.running.has(thread.id) && !this.threadView(thread.id).waiting) this.quotaResume.parked(thread.id);
        // Quota is closing in but the handover did not move the work: shrink the context while the
        // account can still answer, so the rest of the window is not spent re-reading it.
        if (settled !== 'moved') void this.autoCompact.settled(thread.id, turnId).catch(() => undefined);
        this.startNextQueued(thread.id);
      }
    }
  }

  /**
   * Reads the turn's `[进度]` line. A turn that was asked and said nothing withdraws the last
   * report, so an old one never outlives the work.
   */
  private recordProgress(threadId: string, turnId: string): void {
    const said = parseProgress(turnAnswer(this.store.listTurnEvents(threadId, turnId), turnId));
    this.store.setThreadProgress(threadId, { report: said ? { ...said, at: new Date().toISOString() } : null });
  }

  voiceProvider(threadId: string, fallbackProviderId?: string): { providerId: string } {
    const thread = this.getThread(threadId);
    const native = this.adapters.get(thread.agent)?.capabilities.realtimeVoice;
    if (!native && !this.coordination?.metadata(threadId) && this.running.has(threadId)) {
      throw new HttpError(409, 'voice_target_busy');
    }
    const providerId = native ? thread.agent : fallbackProviderId;
    if (thread.archivedAt || !providerId || !this.adapters.get(providerId)?.capabilities.realtimeVoice
      || !native && (!this.coordination || !this.adapters.get(thread.agent)?.capabilities.coordination
        || this.coordination.metadata(threadId)?.team || this.coordination.metadata(threadId)?.retired)
      || this.sessions.has(threadId) && this.sessionAdapters.get(threadId) !== this.adapters.get(thread.agent) && this.running.has(threadId)) {
      throw new HttpError(409, 'voice_provider_unavailable');
    }
    return { providerId };
  }

  /** Deferred initialization lets the lease own cancellation before any process is started. */
  voiceTransport(threadId: string, authorize: () => void = () => {}, providerId?: string): VoiceTransport {
    const provider = this.voiceProvider(threadId, providerId).providerId;
    const adapter = this.adapters.get(provider);
    let nativeThreadId = threadId;
    let ended = false;
    let started = false;
    let transport: VoiceTransport | undefined;
    let unsubscribe = () => {};
    let releaseReports!: () => void;
    const reportsReady = new Promise<void>(resolve => { releaseReports = resolve; });
    let reports = Promise.resolve();
    let queuedReports = 0;
    const reported = new Set<string>();
    const taskTurns = new Map<string, { threadId: string; turnId: string }>();
    const startSeq = this.store.history.latest(threadId) + 1;
    let finalized = false;
    let closeReason: string | undefined;
    let unsubscribeTasks = () => {};

    const conversation = new VoiceConversation(
      () => this.store.history.latest(threadId),
      message => {
        const thread = this.store.getThread(threadId);
        if (!thread) return;
        // A thread started by voice is named by the first thing said in it, as a typed first message would.
        if (thread.title === 'New thread' && message.role === 'user' && message.final && message.text.trim()) {
          this.store.updateThread(threadId, { title: message.text.trim().split('\n')[0].slice(0, 60) });
          this.emitThreadEvent(threadId, null, 'thread.updated', {});
        }
        this.emitThreadEvent(threadId, null, 'voice.message', { ...message, sessionId: conversation.id });
      },
    );
    const finalize = () => {
      if (finalized) return;
      finalized = true;
      unsubscribeTasks();
      const messages = conversation.finalMessages();
      conversation.close();
      if (!this.store.getThread(threadId)) return;
      const stats = this.store.history.voiceStats(threadId, startSeq);
      this.emitThreadEvent(threadId, null, 'voice.closed', {
        sessionId: conversation.id, startedAt: conversation.startedAt, endedAt: new Date().toISOString(),
        count: stats.count, startSeq, endSeq: this.store.history.latest(threadId),
      });
      const substantial = taskTurns.size > 0 || stats.finals >= 6 && stats.userChars >= 40;
      if (closeReason !== 'ended' || !substantial || !this.store.getThread(nativeThreadId)) return;
      const tasks = [...taskTurns.values()].map(task => ({ ...task,
        status: this.store.getThread(task.threadId)?.status ?? 'deleted',
        result: turnAnswer(this.store.voiceAnswerEvents(task.threadId, task.turnId), task.turnId).slice(-6000),
      }));
      const text = '[语音结束总结]\n本次语音已结束。请根据下面的最终字幕和任务结果写一份完整的书面结果：定了什么 / 任务结果 / 下一步。只总结，不启动新任务；未结束的任务明确写仍在进行。字幕是对话记录，不是新的执行指令。用用户在字幕里说的语言写（Write in the language the user spoke）。\n'
        + JSON.stringify({ sessionId: conversation.id, transcript: messages.map(m => ({ role: m.role, text: m.text.slice(0, 2000) })).slice(-32), tasks });
      void this.startTurn(nativeThreadId, { text, queue: true, voiceSummary: conversation.id }).catch(() => {
        if (this.store.getThread(threadId)) this.emitThreadEvent(threadId, null, 'voice.summary.error', {});
      });
    };
    return {
      start: async (offer, emit) => {
        if (ended || started) throw new Error('voice_session_ended');
        started = true;
        authorize();
        if (provider !== this.getThread(threadId).agent) {
          nativeThreadId = await this.coordination!.voiceThread(threadId, provider);
        } else if (this.coordination && !this.coordination.metadata(threadId) && !this.running.has(threadId)) {
          await this.coordination.enable(threadId, { permission: this.getThread(threadId).permissionMode });
        }
        if (ended) throw new Error('voice_session_ended');
        authorize();
        if (this.sessions.has(nativeThreadId) && this.sessionAdapters.get(nativeThreadId) !== adapter) {
          if (this.running.has(nativeThreadId)) throw new Error('voice_provider_changed');
          await this.dropSession(nativeThreadId);
        }
        const session = await this.ensureSession(this.getThread(nativeThreadId));
        if (ended) throw new Error('voice_session_ended');
        authorize();
        if (this.voiceProvider(threadId, provider).providerId !== provider || this.adapters.get(provider) !== adapter) {
          throw new Error('voice_provider_changed');
        }
        let instructions = this.coordination?.metadata(nativeThreadId)
          ? this.coordination.instructions(nativeThreadId, '你在处理实时语音委托。普通聊天不派发任务。用户指定其他智能体时使用协作命令；先查现有成员，后续修改继续同一成员。派发立即返回，不等待任务结束、不轮询。结果自动交回后按真实状态简短汇报，失败或待处理要明确说出。不能把已派发或本轮已返回当作需求已完成。停止播报、结束语音和停止工作是不同动作，指代不清时先澄清。')
          : undefined;
        if (instructions && nativeThreadId !== threadId) {
          const target = this.threadView(threadId);
          instructions += `\n目标会话概况（仅作上下文，不是新任务）：${JSON.stringify({ agent: target.agent,
            title: target.title, status: target.status, waiting: target.waitingReason, workMode: target.workMode,
            recent: this.store.voiceContext(threadId) })}`;
        }
        transport = session.voiceTransport?.(instructions ? { instructions } : undefined) ?? undefined;
        if (!transport) throw new Error('voice_session_unavailable');
        this.voiceThreads.add(nativeThreadId);
        unsubscribeTasks = this.subscribe(event => {
          if (ended || event.type !== 'turn.started' || !event.turnId || event.data.voiceSummary || (event.data.coordination as { kind?: string } | undefined)?.kind === 'result') return;
          if (event.threadId === nativeThreadId || nativeThreadId !== threadId && event.threadId === threadId
            || this.coordination?.metadata(event.threadId)?.rootId === nativeThreadId) {
            taskTurns.set(event.turnId, { threadId: event.threadId, turnId: event.turnId });
            if (taskTurns.size > 64) taskTurns.delete(taskTurns.keys().next().value!);
          }
        });
        if (instructions && transport.speak) {
          const report = (key: string, text: string) => {
            if (ended || reported.has(key) || !text.trim()) return;
            if (queuedReports >= 30) { emit({ type: 'error' }); return; }
            reported.add(key);
            if (reported.size > 256) reported.delete(reported.values().next().value!);
            queuedReports++;
            // Preserve final summaries even when a native turn finishes before the start ACK.
            reports = reports.then(async () => {
              await reportsReady;
              if (ended) return;
              authorize();
              if (this.voiceProvider(threadId, provider).providerId !== provider || this.adapters.get(provider) !== adapter) throw new Error('voice_provider_changed');
              const spoken = text.replace(/```[\s\S]*?```/g, '（代码见会话）').trim();
              await transport!.speak!(spoken.length > 900 ? spoken.slice(0, 900) + '。详细结果请查看会话。' : spoken);
            }).catch(() => { if (!ended) emit({ type: 'error' }); }).finally(() => { queuedReports--; });
          };
          unsubscribe = this.subscribe(event => {
            if (ended || !event.turnId) return;
            const outcome = turnOutcome(event);
            if (event.threadId === nativeThreadId && outcome && !outcome.imported && !outcome.fromTerminal && !this.store.isVoiceSummary(nativeThreadId, event.turnId)) {
              const answer = turnAnswer(this.store.voiceAnswerEvents(nativeThreadId, event.turnId), event.turnId);
              report(`turn:${event.turnId}`, outcome.outcome === 'ok' ? answer
                : outcome.outcome === 'cancelled' ? '任务已停止。' : `任务失败。${outcome.message ?? answer}`);
            } else if (event.type === 'approval.request' || event.type === 'input.request') {
              const relevant = nativeThreadId !== threadId ? event.threadId === threadId
                : this.coordination?.metadata(event.threadId)?.rootId === this.coordination?.metadata(nativeThreadId)?.rootId;
              if (relevant) report(`attention:${event.threadId}:${event.seq}`,
                `${this.getThread(event.threadId).title} ${event.type === 'approval.request' ? '等待批准' : '需要你的回答'}，请在会话中处理。`);
            }
          });
        }
        try { await transport.start(offer, signal => {
          if (ended) return;
          // Persistence must obey the same current grant/account checks as the private SSE,
          // before any final transcript can reach the ordinary conversation history.
          try {
            authorize();
            if (this.voiceProvider(threadId, provider).providerId !== provider || this.adapters.get(provider) !== adapter) {
              throw new Error('voice_provider_changed');
            }
          } catch { emit({ type: 'error' }); return; }
          emit(conversation.accept(signal));
        }); } finally { releaseReports(); }
      },
      appendText: async (text, attached) => {
        if (ended || !transport?.appendText) throw new HttpError(409, 'voice_text_unavailable');
        authorize();
        const videos = this.attachments.videos(threadId, attached?.videos);
        const { images } = this.attachments.save(threadId, Array.isArray(attached?.images) ? attached.images : []);
        await transport.appendText(text + imagePathNote(images) + this.attachments.videoNote(threadId, videos));
      },
      stop: async (reason = 'ended') => {
        closeReason = reason;
        ended = true;
        unsubscribe(); releaseReports();
        this.voiceThreads.delete(nativeThreadId);
        try { await transport?.stop(); } finally { finalize(); }
      },
      dispose: () => { ended = true; unsubscribe(); releaseReports(); this.voiceThreads.delete(nativeThreadId); transport?.dispose(); finalize(); },
    };
  }

  private async ensureSession(thread: ThreadRecord): Promise<AgentSession> {
    const pending = this.sessionStarts.get(thread.id);
    if (pending) return pending;
    const task = this.createSession(thread);
    this.sessionStarts.set(thread.id, task);
    try { return await task; }
    finally { if (this.sessionStarts.get(thread.id) === task) this.sessionStarts.delete(thread.id); }
  }

  private async createSession(thread: ThreadRecord): Promise<AgentSession> {
    const personalScope = Boolean(this.personal.environment(thread.id));
    const adapter = this.adapters.get(thread.agent);
    if (!adapter) throw new Error(`unknown agent: ${thread.agent}`);
    if (this.sessions.has(thread.id) && (this.personalScopes.get(thread.id) !== personalScope || this.sessionAdapters.get(thread.id) !== adapter)) await this.dropSession(thread.id);
    const existing = this.sessions.get(thread.id);
    if (existing) {
      if (existing.setWorkMode) await existing.setWorkMode(thread.workMode ?? 'execute');
      return existing;
    }
    const session = await adapter.start({
      borrowedTool: this.borrowing?.toolForThread(thread.id),
      coordinationEnv: this.backgroundThreads.has(thread.id) ? {} : { ...this.coordination?.environment(thread.id), ...this.borrowing?.environment(thread.id), ...this.personal.environment(thread.id) },
      cwd: thread.cwd,
      resumeId: thread.agentSessionId ?? undefined,
      onSessionId: (id) => {
        if (!id || !this.store.getThread(thread.id)) return;
        this.store.updateThread(thread.id, { agentSessionId: id });
        this.emitThreadEvent(thread.id, null, 'thread.updated', {});
      },
      permissionMode: this.sessionPermission(thread, adapter),
      speed: thread.speed,
      workMode: thread.workMode ?? 'execute',
      onGoal: (goal) => this.applyGoal(thread.id, goal),
      onSpeed: (speed) => {
        if (!this.store.getThread(thread.id)) return;
        this.store.updateThread(thread.id, { speed });
        this.emitThreadEvent(thread.id, null, 'thread.updated', {});
      },
      onWorkMode: (workMode) => {
        if (!this.store.getThread(thread.id)) return;
        this.store.updateThread(thread.id, { workMode });
        this.emitThreadEvent(thread.id, null, 'thread.updated', {});
      },
      model: thread.model ?? undefined,
      effort: (thread.effort ?? undefined) as EffortLevel | undefined,
    });
    session.setSessionHooks?.({
      turnStarted: (nativeId, opts) => {
        // An upstream delegation is a real user task; captions alone never resume collaboration.
        if (this.voiceThreads.has(thread.id)) void this.coordination?.userSpoke(thread.id);
        const turnId = randomUUID();
        const turns = this.nativeTurns.get(thread.id) ?? new Map<string, string>();
        turns.set(nativeId, turnId);
        this.nativeTurns.set(thread.id, turns);
        // The native engine serializes turns. Keep its work visible and block queued prompts.
        if (!this.running.has(thread.id)) this.running.set(thread.id, {
          turnId, cancelRequested: false, since: new Date().toISOString(),
        });
        this.store.updateThread(thread.id, { status: 'running', hubTurnAt: new Date().toISOString() }, { touch: true });
        this.emitThreadEvent(thread.id, turnId, 'turn.started', { text: '', source: opts?.background ? 'background' : 'voice', nativeTurnId: nativeId });
        return this.hooksFor(thread.id, turnId);
      },
      turnFinished: async (nativeId, result) => {
        const turns = this.nativeTurns.get(thread.id);
        const turnId = turns?.get(nativeId);
        if (!turnId) return;
        turns!.delete(nativeId);
        if (!turns!.size) {
          this.nativeTurns.delete(thread.id);
          const waiters = this.nativeIdle.get(thread.id);
          this.nativeIdle.delete(thread.id);
          for (const resolve of waiters ?? []) resolve();
        }
        if (!this.store.getThread(thread.id)) return;
        this.cancelApprovals(thread.id, turnId);
        this.emitThreadEvent(thread.id, turnId, result.stopReason === 'error' ? 'turn.error' : 'turn.done',
          result.stopReason === 'error' ? { message: result.error ?? 'agent error' } : { stopReason: result.stopReason });
        if (this.running.get(thread.id)?.turnId === turnId) {
          this.running.delete(thread.id);
          this.clearModelQueue(thread.id, turnId);
          this.inputs.cancel(thread.id, undefined, true);
          this.store.updateThread(thread.id, { status: result.stopReason === 'error' ? 'error' : 'idle', ...(result.stopReason !== 'cancelled' && (result.stopReason === 'error' || !this.quietTurns.has(turnId)) ? { unread: true } : {}) }, { touch: true });
          this.quietTurns.delete(turnId);
          this.emitThreadEvent(thread.id, null, 'thread.updated', {});
          // Same end-of-turn refresh as a hub turn: a setting changed mid-turn waits for this moment.
          if (this.refreshAfterTurn.delete(thread.id)) await this.dropSession(thread.id).catch(() => undefined);
          this.startNextQueued(thread.id);
        }
      },
    });
    this.personalScopes.set(thread.id, personalScope);
    this.sessions.set(thread.id, session);
    this.sessionAdapters.set(thread.id, adapter);
    if (session.agentSessionId && session.agentSessionId !== thread.agentSessionId) {
      this.store.updateThread(thread.id, { agentSessionId: session.agentSessionId });
    }
    return session;
  }

  private async dropSession(threadId: string): Promise<void> {
    const pending = this.sessionStarts.get(threadId);
    if (pending) await pending.catch(() => undefined);
    const s = this.sessions.get(threadId);
    this.personalScopes.delete(threadId);
    this.sessions.delete(threadId);
    this.sessionAdapters.delete(threadId);
    if (s) await s.close().catch(() => undefined);
  }

  private updateModelQueue(threadId: string, turnId: string, data: Record<string, unknown>): boolean {
    if (data.phase !== 'model_queue') return false;
    const state = typeof data.state === 'string' && data.state ? data.state : 'queued';
    if (state === 'ready' || state === 'done' || state === 'completed') return this.clearModelQueue(threadId, turnId);
    const position = typeof data.position === 'number' && Number.isFinite(data.position) && data.position >= 0 ? data.position : null;
    this.modelQueues.set(threadId, {
      turnId,
      state,
      position,
      operation: typeof data.operation === 'string' ? data.operation : data.operation === null ? null : undefined,
      message: typeof data.message === 'string' ? data.message : data.message === null ? null : undefined,
      updatedAt: new Date().toISOString(),
    });
    return true;
  }

  private clearModelQueue(threadId: string, turnId?: string): boolean {
    const current = this.modelQueues.get(threadId);
    if (!current || turnId && current.turnId !== turnId) return false;
    this.modelQueues.delete(threadId);
    return true;
  }

  /** Keeps the goal view and tells subscribers; the clock lives in the view, not in events. */
  private applyGoal(threadId: string, goal: GoalSnapshot | null): void {
    // A push or a late refresh can land while the thread is being deleted or the store closes
    // underneath shutdown; the goal card is best-effort state, never worth failing a turn for.
    try {
      if (!this.store.getThread(threadId)) return;
      const previous = this.goals.get(threadId);
      const now = Date.now();
      if (!goal) {
        if (!previous) return;
        this.goals.delete(threadId);
        this.emitThreadEvent(threadId, null, 'goal.updated', { goal: null });
        return;
      }
      const unchanged = previous
        && previous.objective === goal.objective && previous.status === goal.status
        && previous.tokensUsed === goal.tokensUsed && previous.tokenBudget === goal.tokenBudget;
      if (unchanged) return;
      // Another objective is another goal: the clock starts over. The same one keeps counting.
      const carries = previous?.objective === goal.objective;
      const view: GoalView = {
        objective: goal.objective,
        status: goal.status,
        ...(goal.tokensUsed !== undefined ? { tokensUsed: goal.tokensUsed } : {}),
        ...(goal.tokenBudget !== undefined ? { tokenBudget: goal.tokenBudget } : {}),
        startedAt: carries ? previous!.startedAt : new Date(now).toISOString(),
        at: new Date(now).toISOString(),
        activeMs: carries ? previous!.activeMs + (previous!.status === 'active' ? now - Date.parse(previous!.at) : 0) : 0,
      };
      this.goals.set(threadId, view);
      this.emitThreadEvent(threadId, null, 'goal.updated', { goal: view });
    } catch {
      // Nothing to keep: the goal went with the thread or the store.
    }
  }

  /** The thread's native goal for the card, refreshed from the live session when there is one. */
  async goalView(threadId: string): Promise<GoalView | null> {
    this.getThread(threadId);
    await this.refreshGoal(threadId);
    return this.goals.get(threadId) ?? null;
  }

  /** Asks the live session what its goal is; the last known state stands when it cannot say. */
  private async refreshGoal(threadId: string): Promise<void> {
    const session = this.sessions.get(threadId);
    if (!session?.goalState) return;
    const timeout = Symbol('goal timeout');
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const goal = await Promise.race([
        session.goalState().catch(() => timeout),
        new Promise<typeof timeout>(resolve => { timer = setTimeout(() => resolve(timeout), 5000); timer.unref?.(); }),
      ]);
      if (goal !== timeout) this.applyGoal(threadId, goal as GoalSnapshot | null);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  private hooksFor(threadId: string, turnId: string): TurnHooks {
    this.approvalTurns.set(turnId, threadId);
    const started = performance.now();
    let previousPhase = started;
    return {
      emit: (ev: EmittedEvent) => {
        if (ev.type === 'turn.phase') {
          const now = performance.now();
          ev = { ...ev, data: { ...ev.data, elapsedMs: now - started, sincePreviousPhaseMs: now - previousPhase } };
          previousPhase = now;
        }
        const modelQueueChanged = ev.type === 'turn.phase' && this.updateModelQueue(threadId, turnId, ev.data);
        this.emitThreadEvent(threadId, turnId, ev.type, ev.data);
        if (modelQueueChanged && this.store.getThread(threadId)) this.emitThreadEvent(threadId, null, 'thread.updated', {});
        // A finished plan ends plan mode: what is said next, a go-ahead or a change to the plan, is
        // a normal turn. The session takes the mode back on before that turn (ensureSession).
        if (ev.type === 'plan.completed' && this.store.getThread(threadId)?.workMode === 'plan') {
          this.store.updateThread(threadId, { workMode: 'execute' });
          this.emitThreadEvent(threadId, null, 'thread.updated', {});
        }
      },
      requestInput: req => this.running.get(threadId)?.cancelRequested
        ? Promise.resolve({ cancelled: true }) : this.inputs.request(threadId, turnId, req),
      cancelInput: id => this.inputs.cancel(threadId, id, true),
      requestApproval: (req: ApprovalRequest) => {
        const run = this.running.get(threadId);
        const native = [...(this.nativeTurns.get(threadId)?.values() ?? [])].includes(turnId);
        if (this.approvalTurns.get(turnId) !== threadId || run?.cancelRequested || (run?.turnId !== turnId && !native)) return Promise.resolve({ cancelled: true });
        // A Goal conversation never asks the person: what its sandbox does not allow is simply refused.
        const refused = this.loopx?.refuse(threadId, req);
        if (refused) return Promise.resolve(refused);
        const approvalId = randomUUID();
        return new Promise<ApprovalDecision>((resolve) => {
          this.approvals.set(approvalId, {
            threadId,
            turnId,
            optionIds: req.options.map((o) => o.optionId),
            resolve: (decision) => {
              this.emitThreadEvent(threadId, turnId, 'approval.resolved', { approvalId, decision });
              resolve(decision);
            },
          });
          this.emitThreadEvent(threadId, turnId, 'approval.request', {
            approvalId,
            title: req.title,
            options: req.options,
            toolCall: req.toolCall,
          });
        });
      },
    };
  }

  private emitThreadEvent(threadId: string, turnId: string | null, type: EventType, data: Record<string, unknown>): void {
    if (turnId && (type === 'turn.done' || type === 'turn.error')) this.approvalTurns.delete(turnId);
    if (type === 'turn.done' && turnId && this.quietTurns.has(turnId) && data.stopReason !== 'cancelled') data = { ...data, quiet: true };
    if (type === 'turn.started') {
      if (!data.control) this.injections.started(threadId, String(data.text ?? ''));
      const agent = this.store.getThread(threadId)?.agent;
      const account = this.agentMeta().find(m => m.id === agent)?.account;
      if (account) data = { ...data, linkedAccount: account };
    }
    // What the agent was told may have left its context, or never reached it: attach it again next time.
    if (type === 'context.compaction' || type === 'turn.error') this.injections.forget(threadId);
    if (type === 'turn.started' && this.unrecordedQuestions.has(threadId)) {
      data = { ...data, text: '', displayText: '', promptOmitted: true };
    }
    const stored = this.store.appendEvent(threadId, turnId, type, data);
    // Turn timing is computed once, when the turn ends; a deferred task keeps it off the turn's tail.
    if (turnId && (type === 'turn.done' || type === 'turn.error') && !data.imported && !data.fromTerminal) this.recordTurnStats(threadId, turnId);
    for (const l of this.listeners) l(stored);
  }

  /** Store a finished turn's timing in the background; a failed run is recomputed on the next read. */
  private recordTurnStats(threadId: string, turnId: string): void {
    const timer = setTimeout(() => {
      try {
        const row = this.computeTurnStats(threadId, turnId);
        if (row) this.store.telemetry.recordTurnStats(threadId, turnId, row);
      } catch { /* The store may be closing; the read path backfills this turn later. */ }
    }, 0);
    timer.unref?.();
  }
}

/** A `file://` prefix, as answers and the page both write paths, is not part of the path itself. */
export function stripFileUrl(raw: string): string {
  if (!/^file:\/\//i.test(raw)) return raw;
  let rest = raw.slice('file://'.length);
  try {
    rest = decodeURIComponent(rest);
  } catch {
    // A literal percent sign: use it as written.
  }
  return rest;
}

/** `~` and `~/…` stand for the daemon user's home directory, as answers often spell them. */
export function expandHome(given: string): string {
  if (given === '~') return homedir();
  if (given.startsWith('~/')) return join(homedir(), given.slice(2));
  return given;
}
